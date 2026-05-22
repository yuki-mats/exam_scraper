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
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://study.mecnet.jp"
DEFAULT_LIST_URL = "https://study.mecnet.jp/exercises/exercise-list/1?firstpage=1"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def slow_down(min_delay_sec: float, max_delay_sec: float) -> None:
    delay = random.uniform(min_delay_sec, max_delay_sec)
    time.sleep(delay)


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


def http_get_text(session: requests.Session, url: str, timeout_sec: float = 20) -> str:
    slow_down(0.4, 0.9)
    resp = session.get(url, timeout=timeout_sec, allow_redirects=True)
    resp.raise_for_status()
    return resp.text


def http_get_bytes(session: requests.Session, url: str, timeout_sec: float = 20) -> bytes:
    slow_down(0.4, 0.9)
    resp = session.get(url, timeout=timeout_sec, allow_redirects=True)
    resp.raise_for_status()
    return resp.content


def ensure_logged_in(session: requests.Session, list_url: str) -> None:
    # list_url をGETしてログイン画面に飛ぶならログインが必要
    slow_down(0.2, 0.5)
    resp = session.get(list_url, timeout=20, allow_redirects=False)
    if resp.status_code in {301, 302, 303, 307, 308}:
        location = resp.headers.get("Location") or ""
        if "/users/login" in location:
            raise RuntimeError("ログインが必要です（list URL が /users/login にリダイレクトされました）。")
    # 200でもHTML中にログインフォームがある可能性があるので軽くチェック
    if resp.status_code == 200 and "name=\"userid\"" in resp.text and "/users/login" in resp.text:
        raise RuntimeError("ログインが必要です（HTMLがログインフォームっぽいです）。")


def login_with_password(session: requests.Session, userid: str, password: str) -> None:
    login_url = urljoin(BASE_URL, "/users/login")
    payload = {"userid": userid, "password": password}
    slow_down(0.4, 0.8)
    resp = session.post(login_url, data=payload, timeout=20, allow_redirects=True)
    resp.raise_for_status()
    # 成功判定は確実に取れないので、ログインフォームが残っていないかで推測
    if "name=\"userid\"" in resp.text and "name=\"password\"" in resp.text:
        raise RuntimeError("ログイン失敗の可能性があります（ログインフォームが返ってきました）。")


def load_cookies_from_json(session: requests.Session, cookies_json_path: Path) -> None:
    # [{"name": "...", "value": "...", "domain": "...", "path": "..."}] 形式を想定
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
        session.cookies.set(name, value, domain=domain or urlparse(BASE_URL).hostname, path=path)


@dataclass(frozen=True)
class QidItem:
    qid: str
    label: str


def extract_qids_from_list_html(list_html: str) -> list[QidItem]:
    soup = BeautifulSoup(list_html, "html.parser")
    items: list[QidItem] = []
    for a in soup.select("a.show_exercise[data-qid]"):
        qid = str(a.get("data-qid") or "").strip()
        if not qid:
            continue
        label = a.get_text(" ", strip=True)
        items.append(QidItem(qid=qid, label=label))
    return items


_JS_URL_RE = re.compile(r"['\"](?P<url>/exercises/[^'\"\\s]+)['\"]")


def guess_detail_endpoints_from_html(list_html: str) -> list[str]:
    soup = BeautifulSoup(list_html, "html.parser")
    candidates: set[str] = set()

    # inline script
    for script in soup.select("script"):
        text = script.get_text("\n", strip=True)
        for m in _JS_URL_RE.finditer(text):
            url = m.group("url")
            if "exercise" in url and "list" not in url:
                candidates.add(url)

    # ありがちなエンドポイントも候補に入れておく（最小限）
    candidates.update(
        {
            "/exercises/show_exercise",
            "/exercises/exercise-detail",
            "/exercises/exercise_detail",
            "/exercises/exercise",
            "/exercises/detail",
        }
    )
    return sorted(candidates)


def try_fetch_detail_html(
    session: requests.Session,
    endpoint_path: str,
    qid: str,
) -> str | None:
    # qid の渡し方が不明なので、よくあるパターンを順に試す（最大数は絞る）
    endpoint_url = urljoin(BASE_URL, endpoint_path)
    trial_params: list[dict[str, str]] = [
        {"qid": qid},
        {"id": qid},
        {"data-qid": qid},
        {"exercise_id": qid},
    ]
    for params in trial_params:
        slow_down(0.5, 1.2)
        resp = session.get(endpoint_url, params=params, timeout=20, allow_redirects=True)
        if resp.status_code != 200:
            continue
        text = resp.text.strip()
        # 明らかなNG（ログイン画面や空）
        if not text:
            continue
        if "/users/login" in text and "name=\"userid\"" in text:
            continue
        # HTML断片 or HTML全体
        if "<html" in text.lower() or "<div" in text.lower() or "<table" in text.lower():
            return text
    return None


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "study.mecnet.jp の演習一覧から qid を抽出し、1問詳細のHTML（HTML断片を含む）を1問ずつ取得して保存します。\n"
            "注意: 実行者がMECから明示的に許諾を得ている前提で使用してください。"
        )
    )
    parser.add_argument("--list-url", default=DEFAULT_LIST_URL, help="演習一覧URL（exercise-list）")
    parser.add_argument(
        "--out-dir",
        default=None,
        help="出力先（省略時: tmp/mecnet_fetch/<timestamp>/）",
    )
    parser.add_argument("--limit", type=int, default=3, help="取得する問題数（負荷低減のためデフォルト3）")
    parser.add_argument(
        "--min-delay-sec",
        type=float,
        default=2.0,
        help="各リクエスト間の最小待機秒（デフォルト2秒）",
    )
    parser.add_argument(
        "--max-delay-sec",
        type=float,
        default=4.0,
        help="各リクエスト間の最大待機秒（デフォルト4秒）",
    )
    parser.add_argument(
        "--cookies-json",
        default=None,
        help="ブラウザ等からエクスポートしたCookie JSON（推奨。ログイン回避ではなくセッション再利用）",
    )
    parser.add_argument("--userid", default=os.environ.get("MECNET_USERID"), help="ログインID（環境変数MECNET_USERID可）")
    parser.add_argument(
        "--password",
        default=os.environ.get("MECNET_PASSWORD"),
        help="ログインパスワード（環境変数MECNET_PASSWORD可）",
    )
    parser.add_argument("--user-agent", default=None, help="User-Agent上書き")
    args = parser.parse_args()

    # delay の上書き（common slow_down を使わず、ここだけ明示制御）
    global slow_down  # noqa: PLW0603

    def slow_down(min_delay_sec: float = args.min_delay_sec, max_delay_sec: float = args.max_delay_sec) -> None:  # type: ignore[misc]
        delay = random.uniform(min_delay_sec, max_delay_sec)
        time.sleep(delay)

    session = create_http_session(args.user_agent)

    if args.cookies_json:
        load_cookies_from_json(session, Path(args.cookies_json).expanduser().resolve())

    # list_url にアクセスできない（ログイン必須）場合のみパスワードログインを試す
    try:
        ensure_logged_in(session, args.list_url)
    except RuntimeError:
        if args.userid and args.password:
            login_with_password(session, args.userid, args.password)
        else:
            raise

    if args.out_dir:
        out_dir = Path(args.out_dir).expanduser().resolve()
    else:
        out_dir = Path("tmp") / "mecnet_fetch" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    meta: dict[str, Any] = {
        "fetched_at": now_iso(),
        "list_url": args.list_url,
        "limit": args.limit,
        "min_delay_sec": args.min_delay_sec,
        "max_delay_sec": args.max_delay_sec,
        "note": "取得頻度は許諾条件に合わせて調整してください。停止要請があれば直ちに中止してください。",
    }
    write_text(out_dir / "meta.json", json.dumps(meta, ensure_ascii=False, indent=2) + "\n")

    list_html = http_get_text(session, args.list_url)
    write_text(out_dir / "list.html", list_html)

    qids = extract_qids_from_list_html(list_html)
    write_text(
        out_dir / "qids.json",
        json.dumps([item.__dict__ for item in qids], ensure_ascii=False, indent=2) + "\n",
    )

    if not qids:
        print("[WARN] qidが抽出できませんでした。HTML構造が想定と違う可能性があります。", file=sys.stderr)
        return 2

    endpoints = guess_detail_endpoints_from_html(list_html)
    write_text(out_dir / "endpoint_candidates.json", json.dumps(endpoints, ensure_ascii=False, indent=2) + "\n")

    # 最初の問題だけでエンドポイントを推定
    first_qid = qids[0].qid
    chosen_endpoint: str | None = None
    for ep in endpoints:
        detail_html = try_fetch_detail_html(session, ep, first_qid)
        if detail_html is None:
            continue
        chosen_endpoint = ep
        write_text(out_dir / "detail_probe.html", detail_html)
        break

    if chosen_endpoint is None:
        print(
            "[WARN] 1問詳細の取得エンドポイントを自動推定できませんでした。"
            " out_dir/detail_probe.html は作成されません。",
            file=sys.stderr,
        )
        return 3

    write_text(out_dir / "chosen_endpoint.txt", chosen_endpoint + "\n")

    # 取得（安全のためデフォルトは少数）
    for idx, item in enumerate(qids[: max(0, args.limit)]):
        detail_html = try_fetch_detail_html(session, chosen_endpoint, item.qid)
        if detail_html is None:
            print(f"[WARN] detail fetch failed: qid={item.qid} label={item.label}", file=sys.stderr)
            continue
        safe_label = re.sub(r"[^0-9A-Za-z._-]+", "_", item.label).strip("_") or f"qid_{item.qid}"
        write_text(out_dir / "details" / f"{idx+1:03d}_{safe_label}_qid{item.qid}.html", detail_html)
        print(f"[OK] saved detail: {item.label} (qid={item.qid})")

    print(f"[OK] out_dir: {out_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

