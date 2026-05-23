from __future__ import annotations

import argparse
import json
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


DEFAULT_START_URL = "https://study.mecnet.jp/exercises/exercise_list/1"


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


def login_with_password(session: requests.Session, base_url: str, userid: str, password: str) -> None:
    login_url = urljoin(base_url, "/users/login")
    slow_down(0.4, 0.9)
    resp = session.post(login_url, data={"userid": userid, "password": password}, timeout=20, allow_redirects=True)
    resp.raise_for_status()
    if "name=\"userid\"" in resp.text and "name=\"password\"" in resp.text:
        raise RuntimeError("ログイン失敗の可能性があります（ログインフォームが返ってきました）。")


def get_html(
    session: requests.Session,
    url: str,
    *,
    min_delay_sec: float,
    max_delay_sec: float,
) -> str:
    last_error: Exception | None = None
    for attempt in range(3):
        slow_down(min_delay_sec, max_delay_sec)
        try:
            resp = session.get(url, timeout=30, allow_redirects=True)
            if resp.status_code in {429, 503}:
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    time.sleep(int(retry_after))
                else:
                    time.sleep(10 + attempt * 10)
                continue
            resp.raise_for_status()
            return resp.text
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(5 + attempt * 10)
    raise RuntimeError(f"failed to fetch: {url} ({last_error})")


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
    # よくある: select.page_links option[value="/exercises/exercise-list?page=2&limit=20"]
    links: dict[int, str] = {}
    for opt in soup.select("select.page_links option[value]"):
        href = (opt.get("value") or "").strip()
        if not href:
            continue
        abs_url = urljoin(base_url, href)
        num = _page_num_from_url(abs_url)
        if num is not None:
            links[num] = abs_url

    # フォールバック: a[href*='page=']
    if not links:
        for a in soup.select("a[href]"):
            href = (a.get("href") or "").strip()
            if not href or "page=" not in href:
                continue
            abs_url = urljoin(base_url, href)
            num = _page_num_from_url(abs_url)
            if num is not None and num not in links:
                links[num] = abs_url

    return [PageLink(page_num=k, url=v) for k, v in sorted(links.items(), key=lambda x: x[0])]


EXAM_LABEL_RE = re.compile(r"\b\d{1,3}[A-Z]-\d{1,3}\b")
EXAM_LABEL_PARTS_RE = re.compile(r"^(?P<occurrence>\d{1,3})(?P<paper>[A-Z])-(?P<question_num>\d{1,3})$")


def infer_exam_year_from_occurrence(occurrence: int, *, year_offset: int | None) -> int | None:
    """
    医師国家試験は「第120回=2026年」「第119回=2025年」など回数と西暦がほぼ 1906 差で対応する。
    ただし、本スクリプトはサイト側の表示から確定できない場合があるため、推定として扱う。
    """
    if year_offset is None:
        return None
    if occurrence <= 0:
        return None
    return occurrence + year_offset


def parse_exam_label(label: str, *, year_offset: int | None) -> dict[str, Any] | None:
    m = EXAM_LABEL_PARTS_RE.match(label.strip())
    if not m:
        return None
    occurrence = int(m.group("occurrence"))
    paper = m.group("paper")
    question_num = int(m.group("question_num"))
    return {
        "examOccurrence": occurrence,  # 出題回（第n回）
        "examPaper": paper,  # A/B/C/D...（表示上の区分）
        "examQuestionNumber": question_num,  # -以降の番号
        "examYear": infer_exam_year_from_occurrence(occurrence, year_offset=year_offset),  # 推定
        "examYearIsInferred": year_offset is not None,
    }


def normalize_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_problem_numbers(html: str, page_url: str, *, year_offset: int | None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")

    items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for a in soup.select("a[data-qid]"):
        qid = str(a.get("data-qid") or "").strip()
        text = normalize_text(a.get_text(" ", strip=True))
        if not qid or not text:
            continue
        m = EXAM_LABEL_RE.search(text)
        if not m:
            continue
        label = m.group(0)
        parsed = parse_exam_label(label, year_offset=year_offset) or {}
        key = (label, qid)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "label": label,
                "qid": qid,
                "text": text,
                **parsed,
            }
        )

    return {"page_url": page_url, "items": items}


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "exercise_list のページネーションを辿り、各ページから問題番号（例: 69B-27）を全量収集して保存します。\n"
            "注意: MEC側の許諾条件に従い、低頻度・単一接続で実行してください。"
        )
    )
    parser.add_argument("--start-url", default=DEFAULT_START_URL, help="開始URL（exercise_list/1 など）")
    parser.add_argument("--out-dir", default=None, help="出力先（省略時: tmp/mecnet_exercise_list_numbers/<timestamp>）")
    parser.add_argument("--cookies-json", default=None, help="Cookie JSON（推奨）")
    parser.add_argument("--userid", default=os.environ.get("MECNET_USERID"), help="ログインID（環境変数MECNET_USERID可）")
    parser.add_argument("--password", default=os.environ.get("MECNET_PASSWORD"), help="ログインPW（環境変数MECNET_PASSWORD可）")
    parser.add_argument("--min-delay-sec", type=float, default=8.0, help="最小待機秒（デフォルト8秒）")
    parser.add_argument("--max-delay-sec", type=float, default=15.0, help="最大待機秒（デフォルト15秒）")
    parser.add_argument(
        "--exam-year-offset",
        type=int,
        default=1906,
        help="出題年推定のオフセット（examYear = examOccurrence + offset）。推定不要なら -1 を指定。",
    )
    parser.add_argument("--max-pages", type=int, default=None, help="検証用: 先頭からページ数制限")
    parser.add_argument("--force", action="store_true", help="既存の保存結果があっても上書きする")
    parser.add_argument("--resume", action="store_true", help="途中まで取得済みの場合、既存の page_*.json を読み、続きから取得する")
    parser.add_argument("--stop-file", default=None, help="このファイルが存在したら直ちに停止（緊急停止用）")
    parser.add_argument("--user-agent", default=None, help="User-Agent上書き")
    args = parser.parse_args()

    if args.out_dir:
        out_dir = Path(args.out_dir).expanduser().resolve()
    else:
        out_dir = (Path("tmp") / "mecnet_exercise_list_numbers" / now_ts()).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    base_url = f"{urlparse(args.start_url).scheme}://{urlparse(args.start_url).netloc}"

    session = create_http_session(args.user_agent)
    if args.cookies_json:
        load_cookies_from_json(session, Path(args.cookies_json).expanduser().resolve())

    start_html = get_html(session, args.start_url, min_delay_sec=args.min_delay_sec, max_delay_sec=args.max_delay_sec)
    if is_login_page(start_html):
        if args.userid and args.password:
            login_with_password(session, base_url, args.userid, args.password)
            start_html = get_html(
                session,
                args.start_url,
                min_delay_sec=args.min_delay_sec,
                max_delay_sec=args.max_delay_sec,
            )
        else:
            raise RuntimeError("ログインが必要です。--cookies-json または --userid/--password を指定してください。")

    write_text(out_dir / "page_001.html", start_html)
    start_soup = BeautifulSoup(start_html, "html.parser")
    page_links = extract_pagination_links(start_soup, args.start_url)

    if not page_links:
        rec = extract_problem_numbers(start_html, args.start_url, year_offset=year_offset)
        write_json(out_dir / "page_001.json", rec)
        write_json(
            out_dir / "meta.json",
            {"extracted_at": now_iso(), "start_url": args.start_url, "pages": 1, "note": "pagination not detected"},
        )
        print(f"[OK] out_dir: {out_dir}")
        return 0

    page_urls: list[PageLink]
    if any(pl.page_num == 1 for pl in page_links):
        page_urls = page_links
    else:
        page_urls = [PageLink(page_num=1, url=args.start_url)] + page_links

    if args.max_pages is not None:
        page_urls = page_urls[: args.max_pages]

    combined: list[dict[str, Any]] = []
    start_index = 1
    if args.resume:
        existing = sorted(out_dir.glob("page_*.json"))
        for p in existing:
            m = re.search(r"page_(\d{3})\.json$", p.name)
            if not m:
                continue
            try:
                combined.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                continue
            start_index = max(start_index, int(m.group(1)) + 1)

    for idx, pl in enumerate(page_urls, start=1):
        if idx < start_index:
            continue
        if args.stop_file and Path(args.stop_file).expanduser().exists():
            print(f"[STOP] stop-file detected: {args.stop_file}")
            break

        html_path = out_dir / f"page_{idx:03d}.html"
        json_path = out_dir / f"page_{idx:03d}.json"
        if not args.force and html_path.exists() and json_path.exists():
            combined.append(json.loads(json_path.read_text(encoding="utf-8")))
            print(f"[SKIP] page={pl.page_num} ({idx}/{len(page_urls)})")
            continue

        html = get_html(session, pl.url, min_delay_sec=args.min_delay_sec, max_delay_sec=args.max_delay_sec)
        write_text(html_path, html)
        rec = extract_problem_numbers(html, pl.url, year_offset=year_offset)
        write_json(json_path, rec)
        combined.append(rec)
        print(f"[OK] saved page={pl.page_num} ({idx}/{len(page_urls)}) items={len(rec.get('items') or [])}")

    # 集約（重複は (label,qid) で除去）
    all_items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for page in combined:
        for it in page.get("items") or []:
            label = str(it.get("label") or "").strip()
            qid = str(it.get("qid") or "").strip()
            if not label or not qid:
                continue
            key = (label, qid)
            if key in seen:
                continue
            seen.add(key)
            parsed = parse_exam_label(label, year_offset=year_offset) or {}
            all_items.append({"label": label, "qid": qid, **parsed})

    # 出題回→紙面→番号でソート（落ちたものは label ソートへフォールバック）
    paper_order = {p: i for i, p in enumerate(list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"), start=1)}

    def sort_key(it: dict[str, Any]) -> tuple:
        occ = it.get("examOccurrence")
        paper = it.get("examPaper")
        qn = it.get("examQuestionNumber")
        if isinstance(occ, int) and isinstance(paper, str) and isinstance(qn, int):
            return (0, occ, paper_order.get(paper, 999), qn, str(it.get("qid") or ""))
        return (1, str(it.get("label") or ""), str(it.get("qid") or ""))

    all_items.sort(key=sort_key)
    write_json(out_dir / "pages_combined.json", combined)
    write_json(out_dir / "all_problem_numbers.json", {"items": all_items})
    write_text(
        out_dir / "all_problem_numbers.txt",
        "\n".join(
            f"{it.get('label')}\t{it.get('qid')}\t{it.get('examYear')}\t{it.get('examOccurrence')}"
            for it in all_items
        )
        + "\n",
    )
    write_json(
        out_dir / "meta.json",
        {
            "extracted_at": now_iso(),
            "start_url": args.start_url,
            "pages_planned": len(page_urls),
            "pages_saved": len(combined),
            "unique_items": len(all_items),
            "min_delay_sec": args.min_delay_sec,
            "max_delay_sec": args.max_delay_sec,
            "exam_year_offset": year_offset,
        },
    )

    print(f"[OK] out_dir: {out_dir}")
    print(f"[OK] unique items: {len(all_items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
    year_offset: int | None = None if args.exam_year_offset < 0 else args.exam_year_offset

