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
from urllib.parse import urljoin

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
LIST_FIRST_PAGE_URL = "https://study.mecnet.jp/exercises/exercise-list/1?firstpage=1"
OUTPUT_LIST_GROUP_ID = "120A"
JSON_SUBDIR_NAME = "00_source"
OUTPUT_DIR = "/Users/yuki/development/exam_scraper/output"
MAX_QUESTIONS: int | None = None
IMAGE_OUTPUT_DIR: str | None = None

MECNET_COOKIES_JSON_ENV = "MECNET_COOKIES_JSON"
MECNET_USERID_ENV = "MECNET_USERID"
MECNET_PASSWORD_ENV = "MECNET_PASSWORD"

MECNET_BASE_URL = "https://study.mecnet.jp"


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


def slow_down(min_sec: float = 2.0, max_sec: float = 5.0) -> None:
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
    slow_down(2.0, 5.0)
    resp = http_session.get(target_url, timeout=20)
    resp.raise_for_status()
    return resp.text


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


def filter_120a_1_to_20(items: list[MecnetListItem]) -> list[MecnetListItem]:
    picked: list[tuple[int, MecnetListItem]] = []
    for item in items:
        m = LABEL_NUM_RE.match(item.label.strip())
        if not m:
            continue
        if m.group("prefix") != "120A":
            continue
        num = int(m.group("num"))
        if 1 <= num <= 20:
            picked.append((num, item))
    return [it for _, it in sorted(picked, key=lambda x: x[0])]


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

    exam_label = f"医師国家試験 第120回 A問題"
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

    output_list_group_id = OUTPUT_LIST_GROUP_ID

    json_output_dir, image_output_dir = prepare_output_dirs(
        OUTPUT_DIR,
        QUALIFICATION_CODE,
        output_list_group_id,
        JSON_SUBDIR_NAME,
    )
    global IMAGE_OUTPUT_DIR
    IMAGE_OUTPUT_DIR = image_output_dir

    http_session = create_http_session()

    cookies_json_path = os.environ.get(MECNET_COOKIES_JSON_ENV)
    if cookies_json_path:
        load_cookies_from_json(http_session, Path(cookies_json_path).expanduser().resolve())
    else:
        # Cookieが無い場合はパスワードログインを試す（環境変数から）
        try_login_with_password(http_session)

    # list page
    list_html = fetch_html_text_rate_limited(http_session, LIST_FIRST_PAGE_URL)
    items = parse_list_items(list_html)
    targets = filter_120a_1_to_20(items)
    if not targets:
        print("[NG] 対象(120A-1..20)が一覧から見つかりませんでした")
        return 2

    assumed_exam_year = 2026

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
        print("[NG] 1件も保存できませんでした")
        return 3

    saved = save_question_body_chunks(
        json_output_dir=json_output_dir,
        list_group_id=output_list_group_id,
        question_bodies=question_bodies,
        chunk_size=25,
    )
    for p in saved:
        print(f"[OK] wrote: {p}")

    print(f"[OK] images_dir: {image_output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
