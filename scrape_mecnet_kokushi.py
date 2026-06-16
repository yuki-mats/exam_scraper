from __future__ import annotations

import json
import os
import random
import re
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from bs4.element import NavigableString
from bs4.element import Tag

from scripts.scrape.common import (
    create_http_session,
    download_and_save_images,
    extract_image_urls_from_element,
    load_local_secure_env,
    make_public_question_id,
    make_storage_url,
    normalize_inline_text,
    normalize_question_body_text,
    prepare_output_dirs,
    save_question_body_chunks,
)


QUALIFICATION_CODE = "mecnet-kokushi"
QUALIFICATION_NAME = "医師国家試験（MEC Net.）"
LIST_FIRST_PAGE_URL = "https://study.mecnet.jp/exercises/exercise_list/1"
OUTPUT_LIST_GROUP_ID = "120A"
JSON_SUBDIR_NAME = "00_source"
OUTPUT_DIR = str(Path(__file__).resolve().parent / "output")
MAX_QUESTIONS: int | None = None
IMAGE_OUTPUT_DIR: str | None = None

MECNET_COOKIES_JSON_ENV = "MECNET_COOKIES_JSON"
MECNET_USERID_ENV = "MECNET_USERID"
MECNET_PASSWORD_ENV = "MECNET_PASSWORD"
MECNET_SCRAPE_ALL_GROUPS_ENV = "MECNET_SCRAPE_ALL_GROUPS"
MECNET_MAX_PAGES_ENV = "MECNET_MAX_PAGES"
MECNET_MIN_DELAY_SEC_ENV = "MECNET_MIN_DELAY_SEC"
MECNET_MAX_DELAY_SEC_ENV = "MECNET_MAX_DELAY_SEC"

MECNET_BASE_URL = "https://study.mecnet.jp"
DEFAULT_ANCHOR_OCCURRENCE = 120
DEFAULT_ANCHOR_YEAR = 2026


def _delay_range() -> tuple[float, float]:
    min_delay = float(os.environ.get(MECNET_MIN_DELAY_SEC_ENV, "8.0"))
    max_delay = float(os.environ.get(MECNET_MAX_DELAY_SEC_ENV, "15.0"))
    if max_delay < min_delay:
        max_delay = min_delay
    return min_delay, max_delay


def apply_runtime_overrides_from_env() -> None:
    global QUALIFICATION_CODE
    global QUALIFICATION_NAME
    global LIST_FIRST_PAGE_URL
    global OUTPUT_LIST_GROUP_ID
    global JSON_SUBDIR_NAME
    global OUTPUT_DIR
    global MAX_QUESTIONS

    qualification_code = os.environ.get("SCRAPER_QUALIFICATION_CODE")
    qualification_name = os.environ.get("SCRAPER_QUALIFICATION_NAME")
    list_first_page_url = os.environ.get("SCRAPER_LIST_FIRST_PAGE_URL")
    output_list_group_id = os.environ.get("SCRAPER_OUTPUT_LIST_GROUP_ID")
    json_subdir_name = os.environ.get("SCRAPER_JSON_SUBDIR_NAME")
    output_dir = os.environ.get("SCRAPER_OUTPUT_DIR")
    max_questions = os.environ.get("SCRAPER_MAX_QUESTIONS")

    if qualification_code:
        QUALIFICATION_CODE = qualification_code
    if qualification_name:
        QUALIFICATION_NAME = qualification_name
    if list_first_page_url:
        LIST_FIRST_PAGE_URL = list_first_page_url
    if output_list_group_id:
        OUTPUT_LIST_GROUP_ID = output_list_group_id
    if json_subdir_name:
        JSON_SUBDIR_NAME = json_subdir_name
    if output_dir:
        OUTPUT_DIR = output_dir
    if max_questions is not None:
        MAX_QUESTIONS = int(max_questions) if max_questions else None


def slow_down(min_sec: float = 8.0, max_sec: float = 15.0) -> None:
    time.sleep(random.uniform(min_sec, max_sec))


def load_cookies_from_json(http_session: requests.Session, cookies_json_path: Path) -> None:
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
        http_session.cookies.set(name, value, domain=domain, path=path)


def try_login_with_password(http_session: requests.Session) -> None:
    userid = os.environ.get(MECNET_USERID_ENV)
    password = os.environ.get(MECNET_PASSWORD_ENV)
    if not userid or not password:
        return
    login_url = urljoin(MECNET_BASE_URL, "/users/login")
    slow_down(0.6, 1.2)
    resp = http_session.post(login_url, data={"userid": userid, "password": password}, timeout=20)
    resp.raise_for_status()


def determine_question_intent(question_text: str) -> str:
    normalized = re.sub(r"\s+", "", question_text or "")
    incorrect_patterns = [
        r"最も不適切(?:なもの|な記述|な説明|な組合せ|な選択肢)?",
        r"最も不適当(?:なもの|な記述|な説明|な組合せ|な選択肢)?",
        r"誤っている(?:もの|記述|説明|組合せ|選択肢)?",
        r"誤り(?:である)?(?:もの|記述|説明|組合せ|選択肢)?",
        r"間違っている(?:もの|記述|説明|組合せ|選択肢)?",
        r"正しくない(?:もの|記述|説明|組合せ|選択肢)?",
        r"不適切(?:な|である)?(?:もの|記述|説明|組合せ|選択肢|対応|方法|処置|行動|内容)?",
        r"不適当(?:な|である)?(?:もの|記述|説明|組合せ|選択肢|対応|方法|処置|行動|内容)?",
        r"適切でない(?:もの|記述|説明|組合せ|選択肢)?",
        r"適当でない(?:もの|記述|説明|組合せ|選択肢)?",
        r"含まれないもの",
        r"該当しないもの",
        r"規定されていないもの",
        r"定められていないもの",
        r"対象とならないもの",
    ]
    if any(re.search(pattern, normalized) for pattern in incorrect_patterns):
        return "select_incorrect"
    return "select_correct"


def build_answer_result_text(answer_numbers: list[int]) -> str:
    if not answer_numbers:
        return ""
    joined = ", ".join(str(n) for n in answer_numbers)
    return f"正解は {joined} です。"


def make_public_question_id_fallback(original_question_id: str) -> str:
    try:
        return make_public_question_id(original_question_id)
    except Exception:
        return sha256(original_question_id.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class MecnetListItem:
    label: str
    qid: str


def fetch_html_text_rate_limited(http_session: requests.Session, target_url: str) -> str:
    min_delay, max_delay = _delay_range()
    last_error: Exception | None = None
    for attempt in range(3):
        slow_down(min_delay, max_delay)
        try:
            resp = http_session.get(target_url, timeout=30)
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
    raise RuntimeError(f"failed to fetch: {target_url} ({last_error})")


def parse_list_items(list_page_html: str) -> list[MecnetListItem]:
    soup = BeautifulSoup(list_page_html, "html.parser")
    items: list[MecnetListItem] = []
    for a in soup.select("a.show_exercise[data-qid]"):
        qid = str(a.get("data-qid") or "").strip()
        label = a.get_text(" ", strip=True)
        if not qid or not label:
            continue
        items.append(MecnetListItem(label=label, qid=qid))
    return items


LABEL_NUM_RE = re.compile(r"^(?P<prefix>[0-9]+[A-Z])-(?P<num>[0-9]+)$")


def list_group_id_from_label(label: str) -> str | None:
    m = LABEL_NUM_RE.match(label.strip())
    if not m:
        return None
    return m.group("prefix")


def question_number_from_label(label: str) -> int | None:
    m = LABEL_NUM_RE.match(label.strip())
    if not m:
        return None
    return int(m.group("num"))


def filter_items_by_list_group_id(items: list[MecnetListItem], list_group_id: str) -> list[MecnetListItem]:
    picked: list[tuple[int, MecnetListItem]] = []
    for item in items:
        prefix = list_group_id_from_label(item.label)
        question_num = question_number_from_label(item.label)
        if prefix is None or question_num is None:
            continue
        if prefix != list_group_id:
            continue
        picked.append((question_num, item))
    return [it for _, it in sorted(picked, key=lambda x: x[0])]


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


def extract_pagination_links(list_page_html: str, base_url: str) -> list[PageLink]:
    soup = BeautifulSoup(list_page_html, "html.parser")
    links: dict[int, str] = {}
    for opt in soup.select("select.page_links option[value]"):
        href = (opt.get("value") or "").strip()
        if not href:
            continue
        abs_url = urljoin(base_url, href)
        num = _page_num_from_url(abs_url)
        if num is not None:
            links[num] = abs_url

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


def fetch_all_list_items(http_session: requests.Session, first_page_url: str) -> list[MecnetListItem]:
    first_html = fetch_html_text_rate_limited(http_session, first_page_url)
    page_links = extract_pagination_links(first_html, first_page_url)
    if any(pl.page_num == 1 for pl in page_links):
        page_urls = page_links
    else:
        page_urls = [PageLink(page_num=1, url=first_page_url)] + page_links

    max_pages_raw = os.environ.get(MECNET_MAX_PAGES_ENV)
    if max_pages_raw:
        page_urls = page_urls[: int(max_pages_raw)]

    all_items: list[MecnetListItem] = []
    seen: set[tuple[str, str]] = set()
    for idx, page_link in enumerate(page_urls, start=1):
        html = first_html if idx == 1 else fetch_html_text_rate_limited(http_session, page_link.url)
        for item in parse_list_items(html):
            key = (item.label, item.qid)
            if key in seen:
                continue
            seen.add(key)
            all_items.append(item)
        print(f"[OK] listed page={page_link.page_num} ({idx}/{len(page_urls)})")
    return all_items


def group_items_by_list_group(items: list[MecnetListItem]) -> dict[str, list[MecnetListItem]]:
    grouped: dict[str, list[MecnetListItem]] = {}
    for item in items:
        list_group_id = list_group_id_from_label(item.label)
        question_num = question_number_from_label(item.label)
        if list_group_id is None or question_num is None:
            continue
        grouped.setdefault(list_group_id, []).append(item)

    for list_group_id, group_items in grouped.items():
        grouped[list_group_id] = filter_items_by_list_group_id(group_items, list_group_id)
    return dict(sorted(grouped.items(), key=lambda kv: _list_group_sort_key(kv[0])))


def _list_group_sort_key(list_group_id: str) -> tuple[int, int, str]:
    m = re.match(r"^(?P<occurrence>\d+)(?P<paper>[A-Z])$", list_group_id)
    if not m:
        return (1, 0, list_group_id)
    occurrence = int(m.group("occurrence"))
    return (0, occurrence, m.group("paper"))


def infer_exam_year_from_list_group_id(list_group_id: str) -> int | None:
    m = re.match(r"^(?P<occurrence>\d+)[A-Z]$", list_group_id)
    if not m:
        return None
    return int(m.group("occurrence")) + (DEFAULT_ANCHOR_YEAR - DEFAULT_ANCHOR_OCCURRENCE)


def _extract_text(el: Tag | None) -> str:
    if el is None:
        return ""
    return normalize_question_body_text(el.get_text("\n", strip=True))


CHOICE_ANALYSIS_RE = re.compile(r"^(?P<marker>[ａ-ｚa-z])\s*[　 ]?(?P<body>.+)$")
CHOICE_MARKER_TO_INDEX = {
    "ａ": 1,
    "ｂ": 2,
    "ｃ": 3,
    "ｄ": 4,
    "ｅ": 5,
    "ｆ": 6,
    "ｇ": 7,
    "ｈ": 8,
    "ｉ": 9,
    "ｊ": 10,
    "a": 1,
    "b": 2,
    "c": 3,
    "d": 4,
    "e": 5,
    "f": 6,
    "g": 7,
    "h": 8,
    "i": 9,
    "j": 10,
}


def _tag_has_underline(tag: Tag) -> bool:
    if tag.name.lower() == "u":
        return True
    style = (tag.get("style") or "").lower()
    if "text-decoration" in style and "underline" in style:
        return True
    cls = " ".join(tag.get("class") or []).lower()
    if "underline" in cls:
        return True
    return False


def _render_html_preserve_underline(node: Tag | NavigableString | None, *, in_underline: bool = False) -> str:
    if node is None:
        return ""
    if isinstance(node, NavigableString):
        return str(node)
    if not isinstance(node, Tag):
        return ""

    tag_name = node.name.lower() if node.name else ""
    if tag_name == "br":
        return "\n"

    next_in_underline = in_underline or _tag_has_underline(node)

    parts: list[str] = []
    for child in node.children:
        parts.append(_render_html_preserve_underline(child, in_underline=next_in_underline))
    text = "".join(parts)

    # ブロック要素は改行を付ける（後段でstrip/整形する）
    if tag_name in {"p", "div", "li"}:
        text = text + "\n"

    # underline がこの要素で開始した場合のみラップ
    if next_in_underline and not in_underline:
        if text.strip():
            return f"[UNDERLINE]{text}[/UNDERLINE]"
    return text


def parse_question_images(
    soup: BeautifulSoup,
    page_url: str,
    http_session: requests.Session,
    public_question_id: str,
    download_images: bool,
) -> tuple[list[str], list[str]]:
    # 問題本文側だけを対象にする（正誤アイコンなどは入れない）
    q_wrap = soup.select_one(".bl_qa_text")
    image_urls = extract_image_urls_from_element(q_wrap, page_url)
    if not download_images or not image_urls:
        return [], []

    filenames = download_and_save_images(
        http_session,
        image_urls,
        f"q{public_question_id}_q",
        base_dir=IMAGE_OUTPUT_DIR or ".",
    )
    storage_urls = [make_storage_url(fname, QUALIFICATION_CODE) for fname in filenames]
    return filenames, storage_urls


def parse_single_explain_page(
    http_session: requests.Session,
    qid: str,
    label: str,
    *,
    output_list_group_id: str,
    download_images: bool,
    assumed_exam_year: int | None,
) -> dict[str, Any] | None:
    page_url = urljoin(MECNET_BASE_URL, f"/exercises/exercise_single_explain/{qid}")
    html = fetch_html_text_rate_limited(http_session, page_url)
    soup = BeautifulSoup(html, "html.parser")

    qtext_el = soup.select_one("p.bgcolor_large_txt")
    question_body_text = _extract_text(qtext_el)
    if not question_body_text:
        return None

    choice_labels = [normalize_inline_text(l.get_text(" ", strip=True)) for l in soup.select("ul.bl_choice_list label")]
    choice_labels = [c for c in choice_labels if c]
    if not choice_labels:
        return None

    correct_numbers: list[int] = []
    for li in soup.select("ul.bl_choice_list li"):
        if not li.select_one(".collect"):
            continue
        inp = li.select_one("input")
        if inp is None:
            continue
        raw = str(inp.get("value") or "").strip()
        if raw.isdigit():
            correct_numbers.append(int(raw))
    correct_numbers = sorted(set(correct_numbers))

    original_question_id = f"mecnet:{qid}"
    public_question_id = make_public_question_id_fallback(original_question_id)

    # 画像（問題本文側のみ）
    _, question_image_storage_urls = parse_question_images(
        soup,
        page_url,
        http_session,
        public_question_id,
        download_images=download_images,
    )

    # テーマ（あれば common_prefix へ）
    theme_el = soup.select_one("[id^=annotation_target_theme_]")
    theme = normalize_inline_text(theme_el.get_text(" ", strip=True)) if theme_el is not None else ""
    explanation_common_prefix = [theme] if theme else []

    # 選択肢考察
    answer_text_el = soup.select_one(".answer_text")
    explanation_choice_snippets = parse_choice_analysis(answer_text_el, choice_count=len(choice_labels))

    question_intent = determine_question_intent(question_body_text)
    correct_choice_texts = [
        ("正しい" if (idx + 1) in correct_numbers else "間違い") for idx in range(len(choice_labels))
    ]
    answer_result_text = build_answer_result_text(correct_numbers)

    m = re.match(r"^(?P<occurrence>\d+)(?P<paper>[A-Z])$", output_list_group_id)
    if m:
        exam_label = f"医師国家試験 第{int(m.group('occurrence'))}回 {m.group('paper')}問題"
    else:
        exam_label = f"医師国家試験 {output_list_group_id}"
    question_label = label

    return {
        "questionBodyText": question_body_text,
        "examLabel": exam_label,
        "questionLabel": question_label,
        "questionType": "true_false",
        "choiceTextList": choice_labels,
        "originalQuestionChoiceImageUrls": [[] for _ in range(len(choice_labels))],
        "category": None,
        "examYear": assumed_exam_year,
        "list_group_id": output_list_group_id,
        "question_url": page_url,
        "public_question_id": public_question_id,
        "original_question_id": original_question_id,
        "questionImageStorageUrls": question_image_storage_urls,
        "questionIntent": question_intent,
        "correctChoiceText": correct_choice_texts,
        "explanation_common_prefix": explanation_common_prefix,
        "explanation_common_prefix_inferred_correct_choice": None,
        "explanation_common_summary": [],
        "explanation_choice_snippets": explanation_choice_snippets,
        "explanation_choice_correctness": correct_choice_texts,
        "answer_result_text": answer_result_text,
        "answer_result_inferred_correct_choice_numbers": correct_numbers,
        "source_question_id": f"{output_list_group_id}:{label}:{qid}",
    }


def parse_choice_analysis(answer_text_el: Tag | None, choice_count: int) -> list[list[str]]:
    # 期待: <p>...ａ ...<br>ｂ ...<br> ...</p>
    # ここでは下線（<u> / style=text-decoration:underline 等）があれば [UNDERLINE]...[/UNDERLINE] で保持する。
    result: list[list[str]] = [[] for _ in range(choice_count)]
    if answer_text_el is None:
        return result

    consideration = answer_text_el.select_one("[id^=annotation_target_choice_consideration_]")
    raw = _render_html_preserve_underline(consideration or answer_text_el)
    if not raw:
        return result

    # 改行整理
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    lines = [line.strip() for line in raw.split("\n") if line.strip()]
    lines = [line for line in lines if "選択肢考察" not in line]

    for line in lines:
        m = CHOICE_ANALYSIS_RE.match(line)
        if not m:
            continue
        marker = m.group("marker")
        body = normalize_inline_text(m.group("body"))
        idx = CHOICE_MARKER_TO_INDEX.get(marker)
        if not idx or idx < 1 or idx > choice_count:
            continue
        result[idx - 1].append(f"選択肢{idx}. {body}")
    return result


def main() -> int:
    load_local_secure_env()
    apply_runtime_overrides_from_env()

    http_session = create_http_session()

    cookies_json_path = os.environ.get(MECNET_COOKIES_JSON_ENV)
    if cookies_json_path:
        load_cookies_from_json(http_session, Path(cookies_json_path).expanduser().resolve())
    else:
        # Cookieが無い場合はパスワードログインを試す（環境変数から）
        try_login_with_password(http_session)

    items = fetch_all_list_items(http_session, LIST_FIRST_PAGE_URL)
    if not items:
        print("[NG] 一覧から問題番号を取得できませんでした")
        return 2

    scrape_all_groups = os.environ.get(MECNET_SCRAPE_ALL_GROUPS_ENV) == "1" or OUTPUT_LIST_GROUP_ID.lower() == "all"
    if scrape_all_groups:
        grouped_items = group_items_by_list_group(items)
    else:
        targets = filter_items_by_list_group_id(items, OUTPUT_LIST_GROUP_ID)
        grouped_items = {OUTPUT_LIST_GROUP_ID: targets} if targets else {}

    if not grouped_items:
        print(f"[NG] 対象list_group_idが見つかりませんでした: {OUTPUT_LIST_GROUP_ID}")
        return 2

    total_saved = 0
    for output_list_group_id, targets in grouped_items.items():
        json_output_dir, image_output_dir = prepare_output_dirs(
            OUTPUT_DIR,
            QUALIFICATION_CODE,
            output_list_group_id,
            JSON_SUBDIR_NAME,
        )
        global IMAGE_OUTPUT_DIR
        IMAGE_OUTPUT_DIR = image_output_dir

        assumed_exam_year = infer_exam_year_from_list_group_id(output_list_group_id)

        question_bodies: list[dict[str, Any]] = []
        for idx, item in enumerate(targets, start=1):
            if MAX_QUESTIONS is not None and idx > MAX_QUESTIONS:
                break
            qb = parse_single_explain_page(
                http_session,
                qid=item.qid,
                label=item.label,
                output_list_group_id=output_list_group_id,
                download_images=True,
                assumed_exam_year=assumed_exam_year,
            )
            if qb is None:
                print(f"[WARN] parse failed: {item.label} (qid={item.qid})")
                continue
            question_bodies.append(qb)
            print(f"[OK] scraped: {item.label} (qid={item.qid})")

        if not question_bodies:
            print(f"[WARN] {output_list_group_id}: 1件も保存できませんでした")
            continue

        saved = save_question_body_chunks(
            json_output_dir=json_output_dir,
            list_group_id=output_list_group_id,
            question_bodies=question_bodies,
            chunk_size=25,
        )
        total_saved += len(question_bodies)
        for p in saved:
            print(f"[OK] wrote: {p}")
        print(f"[OK] images_dir: {image_output_dir}")

    if total_saved == 0:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
