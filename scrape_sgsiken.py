from __future__ import annotations

import os
import re
from typing import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from bs4.element import Tag

from scripts.scrape.common import (
    create_http_session,
    download_and_save_images,
    extract_image_urls_from_element,
    fetch_html_text,
    load_local_secure_env,
    make_public_question_id,
    make_storage_url,
    normalize_inline_text,
    normalize_question_body_text,
    prepare_output_dirs,
    save_question_body_chunks,
)


QUALIFICATION_CODE = "sg"
QUALIFICATION_NAME = "情報セキュリティマネジメント"
LIST_FIRST_PAGE_URL = "https://www.sg-siken.com/kakomon/01_aki/"
JSON_SUBDIR_NAME = "00_source"
MAX_QUESTIONS: int | None = None
OUTPUT_DIR = "/Users/yuki/development/exam_scraper/output"
IMAGE_OUTPUT_DIR: str | None = None

Q_PAGE_HREF_RE = re.compile(r"^q(?P<num>[0-9]+)\.html$")
PM_PAGE_HREF_RE = re.compile(r"^pm(?P<num>[0-9]+)\.html$")
AM1_PAGE_HREF_RE = re.compile(r"^am1_(?P<num>[0-9]+)\.html$")
AM2_PAGE_HREF_RE = re.compile(r"^am2_(?P<num>[0-9]+)\.html$")
# CBT公開問題（科目A/科目B）
A_PAGE_HREF_RE = re.compile(r"^a(?P<num>[0-9]+)\.html$")
B_PAGE_HREF_RE = re.compile(r"^b(?P<num>[0-9]+)\.html$")

ERA_START_YEAR = {
    "令和": 2019,
    "平成": 1989,
    "昭和": 1926,
    "大正": 1912,
    "明治": 1868,
}
FULLWIDTH_DIGITS_TRANS = str.maketrans("０１２３４５６７８９", "0123456789")


def apply_runtime_overrides_from_env() -> None:
    global QUALIFICATION_CODE
    global QUALIFICATION_NAME
    global LIST_FIRST_PAGE_URL
    global JSON_SUBDIR_NAME
    global MAX_QUESTIONS
    global OUTPUT_DIR

    qualification_code = os.environ.get("SCRAPER_QUALIFICATION_CODE")
    qualification_name = os.environ.get("SCRAPER_QUALIFICATION_NAME")
    list_first_page_url = os.environ.get("SCRAPER_LIST_FIRST_PAGE_URL")
    json_subdir_name = os.environ.get("SCRAPER_JSON_SUBDIR_NAME")
    max_questions = os.environ.get("SCRAPER_MAX_QUESTIONS")
    output_dir = os.environ.get("SCRAPER_OUTPUT_DIR")

    if qualification_code:
        QUALIFICATION_CODE = qualification_code
    if qualification_name:
        QUALIFICATION_NAME = qualification_name
    if list_first_page_url:
        LIST_FIRST_PAGE_URL = list_first_page_url
    if json_subdir_name:
        JSON_SUBDIR_NAME = json_subdir_name
    if max_questions is not None:
        MAX_QUESTIONS = int(max_questions) if max_questions else None
    if output_dir:
        OUTPUT_DIR = output_dir


def normalize_digits(text: str) -> str:
    return (text or "").translate(FULLWIDTH_DIGITS_TRANS)


def parse_japanese_era_year(text: str) -> int | None:
    if not text:
        return None
    normalized = normalize_digits(text)
    match = re.search(r"(令和|平成|昭和|大正|明治)\s*(元|[0-9]+)\s*年(?:度)?", normalized)
    if not match:
        return None
    era = match.group(1)
    token = match.group(2)
    base = ERA_START_YEAR.get(era)
    if base is None:
        return None
    if token == "元":
        era_year = 1
    elif token.isdigit():
        era_year = int(token)
    else:
        return None
    if era_year <= 0:
        return None
    return base + era_year - 1


def determine_question_intent(question_text: str) -> str:
    """
    問題文から「正しいものを選ぶ」か「誤っているものを選ぶ」かを判定する。
    既存の code.py 相当ロジック。
    """
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


def infer_correct_choice_texts(
    *,
    choice_count: int,
    answer_numbers: list[int],
    question_intent: str,
) -> list[str]:
    answer_set = set(answer_numbers)
    if question_intent == "select_incorrect":
        # answer_numbers の位置が「間違い」
        return ["間違い" if (i + 1) in answer_set else "正しい" for i in range(choice_count)]
    # select_correct: answer_numbers の位置が「正しい」
    return ["正しい" if (i + 1) in answer_set else "間違い" for i in range(choice_count)]


def normalize_question_page_url(url: str) -> str:
    """
    モバイル一覧 `/s/kakomon/.../` から拾った相対リンクを、
    既存パーサが読める PC 版の詳細URLへ正規化する。
    """
    return re.sub(r"^(https?://[^/]+)/s/kakomon/", r"\1/kakomon/", url)


def collect_question_page_urls(list_page_html: str, list_page_url: str) -> tuple[list[str], list[str]]:
    soup = BeautifulSoup(list_page_html, "html.parser")
    # 「午前/午後」旧形式(qNN/pmNN) + 「公開問題」新形式(aNN/bNN)
    # + nw-siken 系の am1/am2 を同一の単問URLとして扱う。
    question_urls_in_order: list[str] = []
    seen_question_urls: set[str] = set()
    pm_urls: dict[int, str] = {}

    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        if (
            Q_PAGE_HREF_RE.fullmatch(href)
            or A_PAGE_HREF_RE.fullmatch(href)
            or B_PAGE_HREF_RE.fullmatch(href)
            or AM1_PAGE_HREF_RE.fullmatch(href)
            or AM2_PAGE_HREF_RE.fullmatch(href)
        ):
            url = normalize_question_page_url(urljoin(list_page_url, href))
            if url not in seen_question_urls:
                question_urls_in_order.append(url)
                seen_question_urls.add(url)
            continue
        pm_match = PM_PAGE_HREF_RE.fullmatch(href)
        if pm_match:
            num = int(pm_match.group("num"))
            pm_urls[num] = normalize_question_page_url(urljoin(list_page_url, href))

    return question_urls_in_order, [pm_urls[k] for k in sorted(pm_urls)]


def extract_exam_meta_from_h2(soup: BeautifulSoup) -> tuple[str, int | None, str]:
    h2 = soup.find("h2")
    h2_text = normalize_inline_text(h2.get_text(" ", strip=True) if h2 else "")
    exam_year = parse_japanese_era_year(h2_text)
    return h2_text, exam_year, h2_text


def extract_classification_text(soup: BeautifulSoup) -> str | None:
    """
    午前問題ページには「分類 :」があるので取得する（任意）。
    例: "テクノロジ系 » セキュリティ » 情報セキュリティ対策"
    """
    for h3 in soup.find_all("h3"):
        if "分類" not in h3.get_text(strip=True):
            continue
        value_div = h3.find_next_sibling("div")
        if value_div is None:
            continue
        text = normalize_inline_text(value_div.get_text(" ", strip=True))
        return text or None
    return None


def marker_list_from_q_page(choice_items: list[Tag]) -> list[str]:
    """
    選択肢のマーカー（ア/イ/ウ...）を抽出する。
    - 典型: liごとに button.selectBtn が1つ
    - 変形: liが1つで複数 button.selectBtn（解答群画像+ボタンだけ等）
    """
    if not choice_items:
        return []

    # 変形: liが1つでボタンが複数
    if len(choice_items) == 1:
        buttons = choice_items[0].find_all("button", class_="selectBtn")
        markers = [
            normalize_inline_text(b.get_text(" ", strip=True))
            for b in buttons
            if normalize_inline_text(b.get_text(" ", strip=True))
        ]
        deduped: list[str] = []
        for m in markers:
            if m not in deduped:
                deduped.append(m)
        return deduped

    markers: list[str] = []
    for li in choice_items:
        button = li.find("button", class_="selectBtn")
        marker = normalize_inline_text(button.get_text(" ", strip=True) if button else "")
        if marker:
            markers.append(marker)
    return markers


def extract_choice_text_from_li(li: Tag, marker: str) -> str:
    """
    li から選択肢本文を抽出する。
    - spanが無い/空のケースがあるため、li全体テキストから marker を除去して本文にする。
    """
    span = li.find("span")
    span_text = normalize_question_body_text(span.get_text("\n", strip=True) if span else "")
    if span_text:
        return span_text

    li_text = normalize_question_body_text(li.get_text("\n", strip=True))
    if not li_text:
        return ""

    lines = [line.strip() for line in li_text.split("\n") if line.strip()]
    if lines and marker and lines[0] == marker:
        return "\n".join(lines[1:]).strip()

    # インラインで先頭に付く場合: "ア 本文..." のようなケース
    if marker:
        candidate = re.sub(rf"^{re.escape(marker)}\s*", "", li_text).strip()
        if candidate and candidate != marker:
            return candidate

    return li_text.strip()


def parse_answer_markers_from_q_page(soup: BeautifulSoup) -> list[str]:
    answer_box = soup.find("div", class_="answerBox")
    if answer_box is None:
        return []
    # #answerChar が基本だが、複数出る可能性に備え全 span を拾う
    markers = [
        normalize_inline_text(span.get_text(" ", strip=True))
        for span in answer_box.find_all("span")
        if normalize_inline_text(span.get_text(" ", strip=True))
    ]
    deduped: list[str] = []
    for marker in markers:
        if marker not in deduped:
            deduped.append(marker)
    return deduped


def map_answer_markers_to_numbers(choice_markers: list[str], answer_markers: list[str]) -> list[int]:
    answer_numbers: list[int] = []
    if not choice_markers or not answer_markers:
        return answer_numbers
    for marker in answer_markers:
        for idx, choice_marker in enumerate(choice_markers, start=1):
            if marker == choice_marker and idx not in answer_numbers:
                answer_numbers.append(idx)
                break
    return sorted(answer_numbers)


def parse_q_explanation_fields(
    soup: BeautifulSoup,
    *,
    choice_count: int,
) -> tuple[list[str], int | None, list[str], list[list[str]], list[None]]:
    kaisetsu = soup.find(id="kaisetsu")
    if kaisetsu is None:
        return [], None, [], [[] for _ in range(choice_count)], [None for _ in range(choice_count)]

    # 選択肢ごとの解説は li + class="lia|lii|liu|..." 系で出る。
    # li1/li2/li3 等の一般リストは除外する。
    choice_li_class_re = re.compile(r"^li[a-z]+$")
    explanation_items: list[Tag] = []
    for li in kaisetsu.find_all("li"):
        if not isinstance(li, Tag):
            continue
        class_list = li.get("class") or []
        if any(choice_li_class_re.fullmatch(c) for c in class_list):
            explanation_items.append(li)

    choice_texts = [
        normalize_question_body_text(li.get_text("\n", strip=True))
        for li in explanation_items
        if normalize_question_body_text(li.get_text("\n", strip=True))
    ]
    prefix_parts: list[str] = []
    for child in kaisetsu.children:
        if isinstance(child, Tag) and child.name == "ul":
            break
        if isinstance(child, Tag):
            text = normalize_question_body_text(child.get_text("\n", strip=True))
            if text:
                prefix_parts.append(text)

    if len(choice_texts) == choice_count:
        snippets = [[text] if text else [] for text in choice_texts]
        return prefix_parts, None, [], snippets, [None for _ in range(choice_count)]

    fallback = normalize_question_body_text(kaisetsu.get_text("\n", strip=True))
    snippets = [[fallback] if fallback else [] for _ in range(choice_count)]
    return prefix_parts, None, [], snippets, [None for _ in range(choice_count)]


def parse_q_question_page(
    html_text: str,
    page_url: str,
    *,
    http_session,
    download_images: bool,
    output_list_group_id: str,
) -> dict | None:
    soup = BeautifulSoup(html_text, "html.parser")
    exam_label, exam_year, _ = extract_exam_meta_from_h2(soup)
    if exam_year is None:
        return None

    question_label = ""
    qno = soup.find("h3", class_="qno")
    if qno is not None:
        question_label = normalize_inline_text(qno.get_text(" ", strip=True))
    if not question_label:
        # フォールバック: パンくず等から問番号を拾う
        pan = soup.find("div", class_="pan")
        question_label = normalize_inline_text(pan.get_text(" ", strip=True) if pan else "")

    mondai = soup.find(id="mondai")
    question_body_text = normalize_question_body_text(mondai.get_text("\n", strip=True) if mondai else "")
    if not question_body_text:
        return None

    source_question_id = f"{output_list_group_id}:am:{question_label}:{page_url}"
    public_question_id = make_public_question_id(source_question_id)

    # 選択肢リストは button.selectBtn を含む ul を優先する
    choice_list_wrap = None
    first_choice_btn = soup.find("button", class_="selectBtn")
    if first_choice_btn is not None:
        choice_list_wrap = first_choice_btn.find_parent("ul")
    if choice_list_wrap is None:
        choice_list_wrap = soup.find("ul", class_=lambda c: c and "selectList" in c.split())
    choice_items = choice_list_wrap.find_all("li") if choice_list_wrap is not None else []
    choice_markers = marker_list_from_q_page(choice_items)
    choice_text_list: list[str] = []
    choice_image_storage_urls_by_choice: list[list[str]] = []

    # 変形: li が1つ + buttonが複数（解答群画像+ボタンだけ等）
    if len(choice_items) == 1 and len(choice_markers) > 1:
        li = choice_items[0]
        choice_text_list = [m for m in choice_markers]

        shared_choice_image_urls = extract_image_urls_from_element(li, page_url)
        shared_choice_image_filenames = (
            download_and_save_images(
                http_session,
                shared_choice_image_urls,
                f"q{public_question_id}_choices",
                base_dir=IMAGE_OUTPUT_DIR or ".",
            )
            if download_images and shared_choice_image_urls
            else []
        )
        shared_choice_storage_urls = [
            make_storage_url(fname, QUALIFICATION_CODE) for fname in shared_choice_image_filenames
        ]
        choice_image_storage_urls_by_choice = [
            shared_choice_storage_urls[:] for _ in range(len(choice_markers))
        ]
    else:
        for idx, li in enumerate(choice_items, start=1):
            button = li.find("button", class_="selectBtn")
            marker = normalize_inline_text(button.get_text(" ", strip=True) if button else "")
            choice_text = extract_choice_text_from_li(li, marker)
            if not choice_text:
                choice_text = marker
            choice_text_list.append(choice_text)

            choice_image_urls = extract_image_urls_from_element(li, page_url)
            choice_image_filenames = (
                download_and_save_images(
                    http_session,
                    choice_image_urls,
                    f"q{public_question_id}_c{idx:02d}",
                    base_dir=IMAGE_OUTPUT_DIR or ".",
                )
                if download_images and choice_image_urls
                else []
            )
            choice_image_storage_urls_by_choice.append(
                [make_storage_url(fname, QUALIFICATION_CODE) for fname in choice_image_filenames]
            )

    if not choice_text_list:
        return None

    answer_markers = parse_answer_markers_from_q_page(soup)
    answer_numbers = map_answer_markers_to_numbers(choice_markers, answer_markers)
    if not answer_numbers:
        return None

    question_intent = determine_question_intent(question_body_text)
    correct_choice_texts = infer_correct_choice_texts(
        choice_count=len(choice_text_list),
        answer_numbers=answer_numbers,
        question_intent=question_intent,
    )
    answer_result_text = build_answer_result_text(answer_numbers)

    question_image_urls = extract_image_urls_from_element(mondai, page_url)
    question_image_filenames = (
        download_and_save_images(
            http_session,
            question_image_urls,
            f"q{public_question_id}_q",
            base_dir=IMAGE_OUTPUT_DIR or ".",
        )
        if download_images and question_image_urls
        else []
    )
    question_image_storage_urls = [make_storage_url(fname, QUALIFICATION_CODE) for fname in question_image_filenames]

    (
        explanation_common_prefix,
        explanation_common_prefix_inferred_correct_choice,
        explanation_common_summary,
        explanation_choice_snippets,
        explanation_choice_correctness,
    ) = parse_q_explanation_fields(soup, choice_count=len(choice_text_list))
    classification = extract_classification_text(soup)

    return {
        "questionBodyText": question_body_text,
        "examLabel": exam_label,
        "questionLabel": question_label,
        "questionType": "true_false",
        "choiceTextList": choice_text_list,
        "originalQuestionChoiceImageUrls": choice_image_storage_urls_by_choice,
        "category": classification,
        "examYear": exam_year,
        "list_group_id": output_list_group_id,
        "question_url": page_url,
        "public_question_id": public_question_id,
        "original_question_id": public_question_id,
        "questionImageStorageUrls": question_image_storage_urls,
        "questionIntent": question_intent,
        "correctChoiceText": correct_choice_texts,
        "explanation_common_prefix": explanation_common_prefix,
        "explanation_common_prefix_inferred_correct_choice": explanation_common_prefix_inferred_correct_choice,
        "explanation_common_summary": explanation_common_summary,
        "explanation_choice_snippets": explanation_choice_snippets,
        "explanation_choice_correctness": explanation_choice_correctness,
        "answer_result_text": answer_result_text,
        "answer_result_inferred_correct_choice_numbers": answer_numbers,
        "source_question_id": source_question_id,
    }


def _iter_tags_between(start: Tag, stop_condition) -> Iterable[Tag]:
    node = start
    while node is not None:
        node = node.find_next_sibling()
        if node is None:
            break
        if isinstance(node, Tag) and stop_condition(node):
            break
        if isinstance(node, Tag):
            yield node


def extract_common_problem_statement(soup: BeautifulSoup) -> str:
    """
    午後問題ページの共通本文（冒頭の大きな mondai ブロック）を抽出する。
    最初の「設問」開始までの mondai を採用する。
    """
    first_setumon = soup.find("h3", class_="inline", string=lambda s: s and "設問" in s)
    if first_setumon is None:
        return ""
    first_mondai = first_setumon.find_parent("div", class_="mondai")
    if first_mondai is None:
        return ""

    # 先頭の本文は、first_mondai より前にある .mondai（h3.qno の後）を対象にする
    qno = soup.find("h3", class_="qno")
    if qno is None:
        return ""
    qno_wrap = qno.find_parent("div")
    if qno_wrap is None:
        return ""

    lines: list[str] = []
    for node in _iter_tags_between(qno_wrap, lambda n: n == first_mondai):
        if node.name != "div":
            continue
        if "mondai" not in (node.get("class") or []):
            continue
        text = normalize_question_body_text(node.get_text("\n", strip=True))
        if text:
            lines.append(text)
    return "\n\n".join(lines).strip()


def parse_answer_map_from_answer_chars(answer_chars: Tag) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for span in answer_chars.find_all("span"):
        marker = normalize_inline_text(span.get_text(" ", strip=True))
        if not marker:
            continue
        span_id = span.get("id") or ""
        key_match = re.search(r"([a-z]+)$", span_id)
        key = key_match.group(1) if key_match else "main"
        mapping.setdefault(key, [])
        if marker not in mapping[key]:
            mapping[key].append(marker)
    return mapping


def parse_choices_from_select_options(select_tag: Tag) -> tuple[list[str], list[str]]:
    markers: list[str] = []
    choice_texts: list[str] = []
    for opt in select_tag.find_all("option"):
        text = normalize_question_body_text(opt.get_text("\n", strip=True))
        if not text or text == "-":
            continue
        # 例: "ア　...." / "ア"
        parts = re.split(r"\s+", text, maxsplit=1)
        marker = parts[0].strip()
        remainder = parts[1].strip() if len(parts) > 1 else ""
        markers.append(marker)
        choice_texts.append(remainder or marker)
    return markers, choice_texts


def find_nearest_setumon_number(node: Tag) -> str:
    h3 = node.find_previous("h3", class_="inline", string=lambda s: s and "設問" in s)
    if h3 is None:
        return ""
    text = normalize_inline_text(h3.get_text(" ", strip=True))
    m = re.search(r"設問\s*([0-9０-９]+)", normalize_digits(text))
    return m.group(1) if m else ""


def find_pm_question_number(soup: BeautifulSoup) -> str:
    qno = soup.find("h3", class_="qno")
    text = normalize_inline_text(qno.get_text(" ", strip=True) if qno else "")
    m = re.search(r"問\s*([0-9]+)", normalize_digits(text))
    return m.group(1) if m else ""


def parse_pm_question_page(
    html_text: str,
    page_url: str,
    *,
    http_session,
    download_images: bool,
    output_list_group_id: str,
) -> list[dict]:
    soup = BeautifulSoup(html_text, "html.parser")
    exam_label, exam_year, _ = extract_exam_meta_from_h2(soup)
    if exam_year is None:
        return []

    pm_question_no = find_pm_question_number(soup)
    common_statement = extract_common_problem_statement(soup)

    question_bodies: list[dict] = []
    for input_box in soup.find_all("div", class_="inputAnswerBox"):
        select_tags = input_box.find_all("select")
        if not select_tags:
            continue

        statement_div = input_box.find_previous("div", class_="mondai")
        statement_text = normalize_question_body_text(statement_div.get_text("\n", strip=True) if statement_div else "")
        if not statement_text:
            continue

        setumon_no = find_nearest_setumon_number(input_box)
        sub_no_match = re.match(r"^\((\d+)\)", normalize_digits(statement_text).strip())
        sub_no = sub_no_match.group(1) if sub_no_match else ""

        answer_chars = input_box.find_next("div", class_="answerChars")
        if answer_chars is None:
            continue
        answer_map = parse_answer_map_from_answer_chars(answer_chars)

        select_block = input_box.find_previous("div", class_=lambda c: c and "select" in c.split())
        question_image_urls: list[str] = []
        if select_block is not None:
            question_image_urls.extend(extract_image_urls_from_element(select_block, page_url))
        if statement_div is not None:
            question_image_urls.extend(extract_image_urls_from_element(statement_div, page_url))

        question_image_urls_deduped: list[str] = []
        for u in question_image_urls:
            if u and u not in question_image_urls_deduped:
                question_image_urls_deduped.append(u)

        question_image_filenames = (
            download_and_save_images(
                http_session,
                question_image_urls_deduped,
                (
                    f"pm{output_list_group_id}"
                    f"_q{pm_question_no or 'x'}"
                    f"_s{setumon_no or 'x'}"
                    f"_{sub_no or 'x'}"
                ),
                base_dir=IMAGE_OUTPUT_DIR or ".",
            )
            if download_images and question_image_urls_deduped
            else []
        )
        question_image_storage_urls = [make_storage_url(fname, QUALIFICATION_CODE) for fname in question_image_filenames]

        combined_body_lines = []
        if common_statement:
            combined_body_lines.append(common_statement)
        combined_body_lines.append(statement_text)
        combined_body_text = "\n\n".join(line for line in combined_body_lines if line).strip()

        for select_tag in select_tags:
            markers, choice_text_list = parse_choices_from_select_options(select_tag)
            if not choice_text_list:
                continue

            select_name = (select_tag.get("name") or "").strip()
            key_match = re.search(r"([a-z]+)$", select_name)
            blank_key = key_match.group(1) if key_match else "main"

            answer_markers = answer_map.get(blank_key) or answer_map.get("main") or []
            answer_numbers = map_answer_markers_to_numbers(markers, answer_markers)
            if not answer_numbers:
                continue

            question_intent = determine_question_intent(combined_body_text)
            correct_choice_texts = infer_correct_choice_texts(
                choice_count=len(choice_text_list),
                answer_numbers=answer_numbers,
                question_intent=question_intent,
            )
            answer_result_text = build_answer_result_text(answer_numbers)

            explanation_div = answer_chars.find_next_sibling("div", class_="kaisetsu")
            explanation_text = ""
            if explanation_div is not None:
                explanation_text = normalize_question_body_text(explanation_div.get_text("\n", strip=True))
                if "この設問の解説はまだありません" in explanation_text:
                    explanation_text = ""
            explanation_choice_snippets = [
                [explanation_text] if explanation_text else []
                for _ in range(len(choice_text_list))
            ]

            label_parts = []
            if pm_question_no:
                label_parts.append(f"午後問{pm_question_no}")
            if setumon_no:
                label_parts.append(f"設問{setumon_no}")
            if sub_no:
                label_parts.append(f"({sub_no})")
            if blank_key and blank_key != "main":
                label_parts.append(blank_key)
            question_label = " ".join(label_parts) if label_parts else normalize_inline_text(statement_text)[:50]

            source_question_id = f"{output_list_group_id}:pm{pm_question_no}:setumon{setumon_no}:{sub_no}:{blank_key}:{page_url}"
            public_question_id = make_public_question_id(source_question_id)

            question_bodies.append(
                {
                    "questionBodyText": combined_body_text,
                    "examLabel": exam_label,
                    "questionLabel": question_label,
                    "questionType": "true_false",
                    "choiceTextList": choice_text_list,
                    "originalQuestionChoiceImageUrls": [[] for _ in choice_text_list],
                    "examYear": exam_year,
                    "list_group_id": output_list_group_id,
                    "question_url": page_url,
                    "public_question_id": public_question_id,
                    "original_question_id": public_question_id,
                    "questionImageStorageUrls": question_image_storage_urls,
                    "questionIntent": question_intent,
                    "correctChoiceText": correct_choice_texts,
                    "explanation_common_prefix": [],
                    "explanation_common_prefix_inferred_correct_choice": None,
                    "explanation_common_summary": [],
                    "explanation_choice_snippets": explanation_choice_snippets,
                    "explanation_choice_correctness": [None for _ in choice_text_list],
                    "answer_result_text": answer_result_text,
                    "answer_result_inferred_correct_choice_numbers": answer_numbers,
                    "source_question_id": source_question_id,
                }
            )

    return question_bodies


def main() -> int:
    load_local_secure_env()
    apply_runtime_overrides_from_env()

    output_list_group_id = os.environ.get("SCRAPER_OUTPUT_LIST_GROUP_ID")
    if not output_list_group_id or not output_list_group_id.isdigit():
        raise RuntimeError("SCRAPER_OUTPUT_LIST_GROUP_ID（数字のYYYYSSなど）を指定してください。")

    json_output_dir, image_output_dir = prepare_output_dirs(
        OUTPUT_DIR,
        QUALIFICATION_CODE,
        output_list_group_id,
        JSON_SUBDIR_NAME,
    )

    global IMAGE_OUTPUT_DIR
    IMAGE_OUTPUT_DIR = image_output_dir

    http_session = create_http_session()
    list_html = fetch_html_text(http_session, LIST_FIRST_PAGE_URL)
    q_urls, pm_urls = collect_question_page_urls(list_html, LIST_FIRST_PAGE_URL)

    question_bodies: list[dict] = []

    def can_add_more() -> bool:
        return MAX_QUESTIONS is None or len(question_bodies) < MAX_QUESTIONS

    for url in q_urls:
        if not can_add_more():
            break
        html = fetch_html_text(http_session, url)
        qb = parse_q_question_page(
            html,
            url,
            http_session=http_session,
            download_images=True,
            output_list_group_id=output_list_group_id,
        )
        if qb is None:
            continue
        question_bodies.append(qb)

    for url in pm_urls:
        if not can_add_more():
            break
        html = fetch_html_text(http_session, url)
        qbs = parse_pm_question_page(
            html,
            url,
            http_session=http_session,
            download_images=True,
            output_list_group_id=output_list_group_id,
        )
        for qb in qbs:
            if not can_add_more():
                break
            question_bodies.append(qb)

    save_question_body_chunks(
        json_output_dir,
        output_list_group_id,
        question_bodies,
    )
    print(f"[DONE] saved question bodies: {len(question_bodies)} -> {json_output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
