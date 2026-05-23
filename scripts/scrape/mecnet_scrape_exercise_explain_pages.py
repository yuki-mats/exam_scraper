from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://study.mecnet.jp"
DEFAULT_START_URL = "https://study.mecnet.jp/exercises/exercise_explain"


def now_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def slow_down(min_delay_sec: float, max_delay_sec: float) -> None:
    time.sleep(random.uniform(min_delay_sec, max_delay_sec))


def create_http_session(user_agent: str | None) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": user_agent
            or (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome Safari"
            ),
            "Accept-Language": "ja,en;q=0.8",
        }
    )
    return session


def load_cookies_from_json(session: requests.Session, cookies_json_path: Path) -> None:
    raw = json.loads(cookies_json_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("cookies json must be a list")
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        domain = str(item.get("domain") or "").strip()
        path = str(item.get("path") or "/").strip() or "/"
        if not name or not value:
            continue
        session.cookies.set(name, value, domain=domain, path=path)


def login_with_password(session: requests.Session, userid: str, password: str) -> None:
    login_url = urljoin(BASE_URL, "/users/login")
    slow_down(0.4, 0.9)
    resp = session.post(login_url, data={"userid": userid, "password": password}, timeout=20, allow_redirects=True)
    resp.raise_for_status()
    if "name=\"userid\"" in resp.text and "name=\"password\"" in resp.text:
        raise RuntimeError("ログイン失敗の可能性があります（ログインフォームが返ってきました）。")


def get_html(session: requests.Session, url: str, *, min_delay_sec: float, max_delay_sec: float) -> str:
    slow_down(min_delay_sec, max_delay_sec)
    resp = session.get(url, timeout=30, allow_redirects=True)
    resp.raise_for_status()
    return resp.text


def is_login_page(html: str) -> bool:
    return ("name=\"userid\"" in html and "name=\"password\"" in html) or ("/users/login" in html and "do_login" in html)


@dataclass(frozen=True)
class PageLink:
    page_num: int
    url: str


def _page_num_from_url(url: str) -> int | None:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "page" in qs and qs["page"] and qs["page"][0].isdigit():
        return int(qs["page"][0])
    return None


def extract_pagination_links(soup: BeautifulSoup, base_url: str) -> list[PageLink]:
    # 1) select.page_links option[value="/exercises/exercise_explain?page=2&limit=20"] 的なパターン
    links: dict[int, str] = {}
    for opt in soup.select("select.page_links option[value]"):
        href = (opt.get("value") or "").strip()
        if not href:
            continue
        abs_url = urljoin(base_url, href)
        num = _page_num_from_url(abs_url)
        if num is not None:
            links[num] = abs_url

    # 2) a[href*='page='] のフォールバック（重複は上書きしない）
    if not links:
        for a in soup.select("a[href]"):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            if "page=" not in href:
                continue
            abs_url = urljoin(base_url, href)
            num = _page_num_from_url(abs_url)
            if num is not None and num not in links:
                links[num] = abs_url

    return [PageLink(page_num=k, url=v) for k, v in sorted(links.items(), key=lambda x: x[0])]


EXAM_LABEL_RE = re.compile(r"\b\d{1,3}[A-Z]-\d{1,3}\b")


def normalize_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_page_records(html: str, page_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")

    # なるべく情報を落とさない「汎用抽出」
    anchors: list[dict[str, Any]] = []
    for a in soup.select("a[data-qid]"):
        anchors.append(
            {
                "qid": str(a.get("data-qid") or "").strip(),
                "text": normalize_text(a.get_text(" ", strip=True)),
                "classes": list(a.get("class") or []),
            }
        )

    rows: list[dict[str, Any]] = []
    for tr in soup.select("table tr"):
        cells = [normalize_text(td.get_text(" ", strip=True)) for td in tr.select("th,td")]
        if not any(cells):
            continue
        qids = {
            str(a.get("data-qid")).strip()
            for a in tr.select("a[data-qid]")
            if a.get("data-qid") is not None
        }
        label_match = EXAM_LABEL_RE.search(" ".join(cells))
        rows.append(
            {
                "cells": cells,
                "qids": sorted(qids),
                "label": label_match.group(0) if label_match else None,
            }
        )

    return {
        "page_url": page_url,
        "anchors": anchors,
        "rows": rows,
    }


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "exercise_explain のページネーションを辿り、1ページ目から最終ページまで順番にHTMLと抽出データを保存します。\n"
            "注意: MEC側から明示的許諾を得た範囲で、低頻度・単一接続で実行してください。"
        )
    )
    parser.add_argument("--start-url", default=DEFAULT_START_URL, help="開始URL（通常は exercise_explain）")
    parser.add_argument("--out-dir", default=None, help="出力先（省略時: tmp/mecnet_explain_pages/<timestamp>）")
    parser.add_argument("--cookies-json", default=None, help="Cookie JSON（推奨）")
    parser.add_argument("--userid", default=os.environ.get("MECNET_USERID"), help="ログインID（環境変数MECNET_USERID可）")
    parser.add_argument("--password", default=os.environ.get("MECNET_PASSWORD"), help="ログインPW（環境変数MECNET_PASSWORD可）")
    parser.add_argument("--min-delay-sec", type=float, default=3.0, help="リクエスト間の最小待機秒")
    parser.add_argument("--max-delay-sec", type=float, default=6.0, help="リクエスト間の最大待機秒")
    parser.add_argument("--max-pages", type=int, default=None, help="検証用: 先頭からページ数制限")
    parser.add_argument("--force", action="store_true", help="既存の保存結果があっても上書きする")
    parser.add_argument("--user-agent", default=None, help="User-Agent上書き")
    args = parser.parse_args()

    if args.out_dir:
        out_dir = Path(args.out_dir).expanduser().resolve()
    else:
        out_dir = (Path("tmp") / "mecnet_explain_pages" / now_ts()).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    session = create_http_session(args.user_agent)
    if args.cookies_json:
        load_cookies_from_json(session, Path(args.cookies_json).expanduser().resolve())

    # start page (ログイン判定 & 必要ならログイン)
    start_html = get_html(session, args.start_url, min_delay_sec=args.min_delay_sec, max_delay_sec=args.max_delay_sec)
    if is_login_page(start_html):
        if args.userid and args.password:
            login_with_password(session, args.userid, args.password)
            start_html = get_html(session, args.start_url, min_delay_sec=args.min_delay_sec, max_delay_sec=args.max_delay_sec)
        else:
            raise RuntimeError("ログインが必要です。--cookies-json または --userid/--password を指定してください。")

    write_text(out_dir / "page_001.html", start_html)
    start_soup = BeautifulSoup(start_html, "html.parser")
    page_links = extract_pagination_links(start_soup, args.start_url)

    # ページリンクが取れない場合でも、1ページ目だけは保存して終える
    if not page_links:
        rec = extract_page_records(start_html, args.start_url)
        write_json(out_dir / "page_001.json", rec)
        write_json(
            out_dir / "meta.json",
            {
                "extracted_at": now_iso(),
                "start_url": args.start_url,
                "pages": 1,
                "note": "pagination not detected",
            },
        )
        print(f"[OK] out_dir: {out_dir}")
        return 0

    # page=1 を含まないケースがあるので、先頭に start_url を1ページ目として固定
    # ただし page_links に page=1 があるならそちらを採用
    page_urls: list[PageLink] = []
    if any(pl.page_num == 1 for pl in page_links):
        page_urls = page_links
    else:
        page_urls = [PageLink(page_num=1, url=args.start_url)] + page_links

    if args.max_pages is not None:
        page_urls = page_urls[: args.max_pages]

    combined: list[dict[str, Any]] = []
    for idx, pl in enumerate(page_urls, start=1):
        html_path = out_dir / f"page_{idx:03d}.html"
        json_path = out_dir / f"page_{idx:03d}.json"

        if not args.force and html_path.exists() and json_path.exists():
            combined.append(json.loads(json_path.read_text(encoding="utf-8")))
            print(f"[SKIP] page={pl.page_num} ({idx}/{len(page_urls)})")
            continue

        html = get_html(session, pl.url, min_delay_sec=args.min_delay_sec, max_delay_sec=args.max_delay_sec)
        write_text(html_path, html)
        rec = extract_page_records(html, pl.url)
        write_json(json_path, rec)
        combined.append(rec)
        print(f"[OK] saved page={pl.page_num} ({idx}/{len(page_urls)})")

    write_json(
        out_dir / "meta.json",
        {
            "extracted_at": now_iso(),
            "start_url": args.start_url,
            "pages": len(page_urls),
            "min_delay_sec": args.min_delay_sec,
            "max_delay_sec": args.max_delay_sec,
        },
    )
    write_json(out_dir / "pages_combined.json", combined)
    print(f"[OK] out_dir: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
