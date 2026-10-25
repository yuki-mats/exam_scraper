"""KnounのドリルをPlaywrightで巡回し、OCR前の原画像を保存する。"""
from __future__ import annotations

import argparse
import fcntl
import getpass
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
MENU_URL = "https://knoun.jp/knounclient/web/drill/init?contentsId=1266&assignedId=1091208"
BOOK_ID = "knoun-ISBN9784800594143"
QUALIFICATION = "eco-kentei"
GROUP_COUNT = 5
EXPECTED_COUNT = 85
FRAME_RE = re.compile(r"/knounclient/contents/(?P<book>knoun-ISBN\d+)/(?P<role>mondai|comment)(?P<group>\d+)_(?P<id>\d+)\.html$")


class CaptureTransportError(RuntimeError):
    """保存済みデータから再開可能な通信障害。"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def frame_identity(url: str, *, role: str, group: int, book: str) -> str:
    parsed = urlsplit(url)
    match = FRAME_RE.fullmatch(parsed.path)
    if (parsed.scheme != "https" or parsed.netloc != "knoun.jp" or parsed.query
            or parsed.fragment or not match or match["role"] != role
            or int(match["group"]) != group or match["book"] != book):
        raise ValueError(f"取得範囲外又は識別子不明のframe: {url}")
    return match["id"]


def image_url_from_html(html: str, frame_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    images = soup.find_all("img")
    if soup.select_one('input[type="password"]') or len(images) != 1:
        raise ValueError("原画像ページではありません（認証切れ又はDOM変更）")
    url = urljoin(frame_url, images[0].get("src", ""))
    frame = urlsplit(frame_url)
    image = urlsplit(url)
    expected_path = frame.path.removesuffix(".html") + ".png"
    if image.scheme != "https" or image.netloc != "knoun.jp" or image.path != expected_path or image.query or image.fragment:
        raise ValueError("frameと画像の識別子が一致しません")
    return url


def store_png(path: Path, data: bytes, content_type: str) -> dict:
    if content_type.split(";")[0].strip().lower() != "image/png" or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("PNG以外の応答を画像として保存できません")
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
        image.verify()
    if min(width, height) < 100:
        raise ValueError("原画像の大きさが不足しています")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"保存済み原画像との内容差を検出: {path.name}")
    else:
        # 中断で半端なPNGを確定pathへ残さない。hard linkで既存fileも上書きしない。
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            try:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    if path.read_bytes() != data:
                        raise ValueError(f"原画像の同時保存が競合: {path.name}")
            finally:
                temporary.unlink(missing_ok=True)
    return {"filename": path.name, "sha256": digest(data), "bytes": len(data), "width": width, "height": height}


def read_manifest(path: Path, group: int, book: str) -> dict:
    if not path.exists():
        return {"schemaVersion": "knoun-image-capture/v1", "bookId": book,
                "qualification": QUALIFICATION, "group": group, "expectedCount": EXPECTED_COUNT,
                "originalizationRequired": True, "records": {}, "status": "capturing"}
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schemaVersion") != "knoun-image-capture/v1" or value.get("bookId") != book or value.get("group") != group:
        raise ValueError("既存manifestの取得対象が異なります")
    for source_id, record in value["records"].items():
        if source_id != record["sourceQuestionId"]:
            raise ValueError("manifestの問題IDが一致しません")
        qid = frame_identity(record["questionFrameUrl"], role="mondai", group=group, book=book)
        cid = frame_identity(record["explanationFrameUrl"], role="comment", group=group, book=book)
        if qid != cid or source_id != record["questionFrameUrl"]:
            raise ValueError("manifestの問題と解説が対応しません")
        for role in ("questionImage", "explanationImage"):
            image = record[role]
            relative = Path(image["relativePath"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("不正な原画像path")
            target = path.parent / relative
            if not target.is_file() or digest(target.read_bytes()) != image["sha256"]:
                raise ValueError(f"原画像の欠落又はhash不一致: {target.name}")
    return value


def request_get(context, url: str):
    for attempt in range(3):
        try:
            response = context.request.get(url, timeout=45000)
        except Exception as exc:
            if attempt == 2:
                raise CaptureTransportError(f"通信に失敗: {Path(urlsplit(url).path).name} ({type(exc).__name__})") from None
            time.sleep(2 ** (attempt + 1))
            continue
        if response.status not in {429, 500, 502, 503, 504} or attempt == 2:
            return response
        retry_after = response.headers.get("retry-after", "")
        delay = float(retry_after) if retry_after.isdigit() else 2 ** (attempt + 1)
        if delay > 60:
            raise RuntimeError(f"取得元の待機指定により停止: HTTP {response.status}, Retry-After={int(delay)}秒")
        print(f"[RETRY] {Path(urlsplit(url).path).name}: HTTP {response.status}, {max(1, delay)}秒後", flush=True)
        time.sleep(max(1, delay))
    raise AssertionError("unreachable")


def fetch_image(context, frame_url: str, destination: Path) -> dict:
    # requestはこの専用browser contextの認証を使う。cookieをexportしない。
    response = request_get(context, frame_url)
    if not response.ok or response.url != frame_url:
        error_type = CaptureTransportError if response.status in {500, 502, 503, 504} else RuntimeError
        raise error_type(f"原画像ページの取得失敗: HTTP {response.status}, finalPath={urlsplit(response.url).path}")
    image_url = image_url_from_html(response.text(), frame_url)
    response = request_get(context, image_url)
    if not response.ok or response.url != image_url:
        error_type = CaptureTransportError if response.status in {500, 502, 503, 504} else RuntimeError
        raise error_type(f"原画像の取得失敗: HTTP {response.status}, finalPath={urlsplit(response.url).path}")
    metadata = store_png(destination, response.body(), response.headers.get("content-type", ""))
    metadata["sourceUrl"] = image_url
    return metadata


def find_card(page, group: int):
    title = f"eco 検定模擬問題{str(group).translate(str.maketrans('12345', '１２３４５'))}"
    node = page.get_by_text(title, exact=True)
    if node.count() != 1:
        raise RuntimeError(f"模擬問題{group}の見出しを一意に識別できません")
    for _ in range(8):
        node = node.locator("xpath=..")
        starts = node.get_by_role("button", name=re.compile(r"^はじめから\s*$"))
        if starts.count() == 1:
            return node
        if starts.count() > 1:
            break
    raise RuntimeError("問題集と開始ボタンの対応が不明です")


def advance_question(page, interval: float) -> None:
    # Knounのdontknow()はwindow loadで初期化される時刻に依存する。
    # frame URLや画像を読めても、回答操作が可能とは限らない。
    page.wait_for_load_state("load", timeout=60000)
    # 選択式・記述式では内部IDが異なるため、現在表示中の操作名で識別する。
    unknown = page.get_by_role("button", name=re.compile(r"^わから\s*ない$"))
    if unknown.count() > 1:
        raise RuntimeError("未習熟回答ボタンを一意に識別できません")
    if unknown.count() == 1:
        unknown.evaluate("button => button.click()")
    next_button = page.get_by_role("button", name="次へ", exact=True)
    next_button.wait_for(state="visible", timeout=45000)
    page.wait_for_timeout(interval * 1000)
    with page.expect_navigation(wait_until="commit", timeout=45000):
        next_button.evaluate("button => button.click()")


def login_if_needed(page, args) -> None:
    print("[STEP] ドリルメニューを開きます", flush=True)
    page.goto(args.url, wait_until="domcontentloaded", timeout=45000)
    if page.locator('input[type="password"]').count() == 0:
        return
    if args.login_stdin:
        # 端末のechoを切り、shell引数・環境変数・保存fileへ認証値を載せない。
        login_id = getpass.getpass("Knoun ID（非表示）: ")
        password = getpass.getpass("Knoun password（非表示）: ")
        print("[STEP] ログインフォームへ入力します（値は記録しません）", flush=True)
        page.locator("#userId").fill(login_id, force=True)
        page.locator("#password").fill(password, force=True)
        del login_id, password
        login_button = page.locator("button.login")
        with page.expect_navigation(wait_until="commit", timeout=45000):
            login_button.evaluate("button => button.click()")
            print("[STEP] ログイン送信済み", flush=True)
        print("[STEP] ログイン後のドリルメニューを開きます", flush=True)
        page.goto(args.url, wait_until="domcontentloaded", timeout=45000)
        if page.locator('input[type="password"]').count():
            raise RuntimeError("Knounのログインが完了しませんでした")
        return
    if args.headless:
        raise RuntimeError("未認証です。--login-stdinで非表示入力するか、本人操作の--headedでログインしてください")
    print("[LOGIN_REQUIRED] 開いた専用ブラウザでKnounにログインしてください。パスワードはツールへ入力しないでください。", flush=True)
    deadline = time.monotonic() + args.login_timeout
    while page.locator('input[type="password"]').count():
        if time.monotonic() >= deadline:
            raise RuntimeError("ログイン待ち時間を超過しました。再実行できます")
        page.wait_for_timeout(1000)
    # ログイン後の遷移を待ってから依頼されたドリルに戻る。
    page.wait_for_load_state("domcontentloaded")
    page.goto(args.url, wait_until="domcontentloaded", timeout=45000)


def capture_group(context, page, args, group: int) -> None:
    group_id = f"knoun-2026-mock-{group}"
    raw_dir = args.output / QUALIFICATION / "question_images" / group_id / "raw"
    manifest_path = raw_dir.parent / "capture_manifest.json"
    manifest = read_manifest(manifest_path, group, args.book_id)
    baseline_ids = set(manifest["records"])
    if not args.restart and manifest.get("status") == "images_complete" and len(baseline_ids) == EXPECTED_COUNT:
        print(f"[SKIP] {group_id}: 検証済み{EXPECTED_COUNT}組", flush=True)
        return
    print(f"[STEP] {group_id}: メニュー確認", flush=True)
    page.goto(args.url, wait_until="domcontentloaded", timeout=45000)
    card = find_card(page, group)
    if f"{EXPECTED_COUNT}問" not in re.sub(r"\s+", "", card.inner_text()):
        raise RuntimeError("画面の期待件数が設定と異なります")
    continuation = card.get_by_role("button", name="つづきから", exact=True)
    resuming = bool(baseline_ids) and not args.restart and continuation.count() == 1
    start_button = continuation if resuming else card.get_by_role("button", name=re.compile(r"^はじめから\s*$"))
    print(f"[STEP] {group_id}: {'中断位置から再開' if resuming else '先頭から取得・再照合'}", flush=True)
    with page.expect_navigation(wait_until="commit", timeout=45000):
        start_button.evaluate("button => button.click()")
    seen: set[str] = set()
    for _ in range(EXPECTED_COUNT):
        page.locator("#contentsFrm").wait_for(state="attached", timeout=45000)
        question_url = urljoin(page.url, page.locator("#contentsFrm").get_attribute("src") or "")
        explanation_url = urljoin(page.url, page.locator("#commentFrm").get_attribute("src") or "")
        question_id = frame_identity(question_url, role="mondai", group=group, book=args.book_id)
        comment_id = frame_identity(explanation_url, role="comment", group=group, book=args.book_id)
        if question_id != comment_id or question_url in seen:
            raise RuntimeError("問題と解説のID不一致又は同じ問題への循環")
        seen.add(question_url)
        question_image = fetch_image(context, question_url, raw_dir / f"mondai{group}_{question_id}.png")
        explanation_image = fetch_image(context, explanation_url, raw_dir / f"comment{group}_{comment_id}.png")
        for metadata in (question_image, explanation_image):
            metadata["relativePath"] = f"raw/{metadata['filename']}"
        record = {"sourceQuestionId": question_url, "questionFrameUrl": question_url,
                  "explanationFrameUrl": explanation_url, "questionImage": question_image,
                  "explanationImage": explanation_image}
        previous = manifest["records"].get(question_url)
        if previous and previous != record:
            raise RuntimeError("取得済みの問題と今回の対応又は画像が異なります")
        manifest["records"][question_url] = record
        manifest["updatedAt"] = now()
        manifest["status"] = "capturing"
        atomic_json(manifest_path, manifest)
        print(f"[SAVED] {group_id} {len(manifest['records'])}/{EXPECTED_COUNT}: {question_id}", flush=True)
        if (resuming and len(manifest["records"]) == EXPECTED_COUNT) or len(seen) == EXPECTED_COUNT:
            break
        if args.max_questions is not None and len(seen) >= args.max_questions:
            return
        advance_question(page, args.interval)
    # 件数だけでは欠落を見逃すため、取得元の全URL集合と保存済み集合を照合する。
    expected_ids = baseline_ids | seen if resuming else seen
    if expected_ids != set(manifest["records"]) or len(expected_ids) != EXPECTED_COUNT:
        raise RuntimeError("全問題のURL集合が不足又は不一致です。--restartで先頭から照合してください")
    manifest["status"] = "images_complete"
    manifest["updatedAt"] = now()
    atomic_json(manifest_path, manifest)
    page.screenshot(path=str(raw_dir.parent / "capture_completed.png"), full_page=True)
    print(f"[IMAGES_COMPLETE] {group_id}: {EXPECTED_COUNT}組。OCR・目視照合・05以降は未実行", flush=True)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=MENU_URL)
    parser.add_argument("--book-id", default=BOOK_ID)
    parser.add_argument("--groups", type=int, nargs="+", default=list(range(1, GROUP_COUNT + 1)))
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "output")
    parser.add_argument("--profile", type=Path, default=Path.home() / ".config/exam_scraper/knoun-browser")
    parser.add_argument("--channel", default="chrome", help="既存のChromeを専用profileで起動する")
    display = parser.add_mutually_exclusive_group()
    display.add_argument("--headless", dest="headless", action="store_true", default=True)
    display.add_argument("--headed", dest="headless", action="store_false", help="本人が画面上でログインする場合だけ明示指定する")
    parser.add_argument("--login-timeout", type=int, default=1800)
    parser.add_argument("--login-stdin", action="store_true", help="非表示の端末入力でID/passwordを一時的に受け付ける")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--max-questions", type=int)
    parser.add_argument("--restart", action="store_true", help="続きからではなく、先頭から全URLを再照合する")
    parser.add_argument("--allow-learning-history-updates", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    url = urlsplit(args.url)
    if url.scheme != "https" or url.netloc != "knoun.jp" or url.path != "/knounclient/web/drill/init":
        parser.error("KnounのドリルメニューURLを指定してください")
    if not args.groups or len(args.groups) != len(set(args.groups)) or not set(args.groups) <= set(range(1, 6)):
        parser.error("groupsは1〜5の重複しない値にしてください")
    if args.interval < 0.5 or args.login_timeout < 1 or (args.max_questions is not None and args.max_questions < 1):
        parser.error("intervalは0.5以上、待ち時間・問題数は正の値にしてください")
    if not args.verify_only and not args.allow_learning_history_updates:
        parser.error("画像巡回は学習履歴を更新します。許可済みの場合だけ--allow-learning-history-updatesを指定してください")
    args.output = args.output.expanduser().resolve()
    args.profile = args.profile.expanduser().resolve()
    return args


def capture_with_reconnect(context, page, args, group: int) -> None:
    for attempt in range(3):
        try:
            capture_group(context, page, args, group)
            return
        except CaptureTransportError:
            if attempt == 2:
                raise
            delay = 5 * (attempt + 1)
            print(f"[RECONNECT] group={group}: 通信回復を{delay}秒待ち、manifestから再開します", flush=True)
            time.sleep(delay)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.verify_only:
        for group in args.groups:
            path = args.output / QUALIFICATION / "question_images" / f"knoun-2026-mock-{group}" / "capture_manifest.json"
            if not path.exists():
                raise RuntimeError(f"未取得: 模擬問題{group}")
            value = read_manifest(path, group, args.book_id)
            print(f"[VERIFIED] group={group} images={len(value['records']) * 2} status={value['status']}")
            if value.get("status") != "images_complete" or len(value["records"]) != EXPECTED_COUNT:
                raise RuntimeError(f"画像取得は未完了: 模擬問題{group} {len(value['records'])}/{EXPECTED_COUNT}")
        return 0
    from playwright.sync_api import sync_playwright

    args.profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(args.profile, 0o700)
    # 同じ認証profileで取得処理を重ねない。終了・中断時はOSがlockを解放する。
    with (args.profile / "capture.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(args.profile), channel=args.channel, headless=args.headless,
                viewport={"width": 1280, "height": 900}, accept_downloads=False,
            )
            try:
                # データ取得に不要な外部fontの待ちで、ログイン・問題遷移を止めない。
                context.route("https://fonts.googleapis.com/**", lambda route: route.abort())
                context.route("https://fonts.gstatic.com/**", lambda route: route.abort())
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(45000)
                login_if_needed(page, args)
                for group in args.groups:
                    capture_with_reconnect(context, page, args, group)
            except Exception:
                print(f"[STATE] path={urlsplit(page.url).path} passwordForms={page.locator('input[type=password]').count()} questionFrames={page.locator('#contentsFrm').count()}", flush=True)
                error_image = args.output / QUALIFICATION / "reports/knoun_capture_error.png"
                error_image.parent.mkdir(parents=True, exist_ok=True)
                try:
                    page.screenshot(path=str(error_image), mask=[page.locator("input"), page.locator("header")], timeout=10000)
                except Exception:
                    pass
                raise
            finally:
                context.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, Exception) as exc:
        # playwrightの例外に認証ページ本文やform値を含めず、失敗位置だけ通知する。
        detail = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "取得が中断しました"
        print(f"[STOPPED] {type(exc).__name__}: {detail}。確定済みmanifestを保持しています。", file=sys.stderr)
        raise SystemExit(1)
