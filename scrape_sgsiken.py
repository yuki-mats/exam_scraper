from __future__ import annotations

import os
import re
import json
import tempfile
import gzip
from pathlib import Path
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag

from scripts.scrape.common import (
    create_http_session,
    download_and_save_images as _download_and_save_images,
    extract_image_urls_from_element,
    fetch_html_text,
    load_local_secure_env,
    is_placeholder_image_url,
    make_public_question_id,
    make_canonical_question_key,
    make_url_source_question_id,
    source_site_from_url,
    extract_text_with_subsup,
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
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
IMAGE_OUTPUT_DIR: str | None = None

Q_PAGE_HREF_RE = re.compile(r"^q(?P<num>[0-9]+)\.html$")
PM_PAGE_HREF_RE = re.compile(r"^pm(?P<num>[0-9]+)\.html$")
AM1_PAGE_HREF_RE = re.compile(r"^am1_(?P<num>[0-9]+)\.html$")
AM2_PAGE_HREF_RE = re.compile(r"^am2_(?P<num>[0-9]+)\.html$")
# CBT公開問題（科目A/科目B）
A_PAGE_HREF_RE = re.compile(r"^a(?P<num>[0-9]+)\.html$")
B_PAGE_HREF_RE = re.compile(r"^b(?P<num>[0-9]+)\.html$")
NUMBERED_PAGE_HREF_RE = re.compile(r"^(?P<num>[0-9]{2})\.html$")

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


def download_and_save_images(http_session, image_url_list, filename_prefix, *, base_dir):
    saved = _download_and_save_images(http_session, image_url_list, filename_prefix, base_dir=base_dir)
    expected = sum(bool(url) and not is_placeholder_image_url(url) for url in image_url_list)
    if len(saved) != expected:
        raise ValueError(f"画像の取得が不完全です: {filename_prefix}")
    # HTTP 200のエラー画面等を画像として保存したままsourceを確定しない。
    from PIL import Image
    from xml.etree import ElementTree
    for filename in saved:
        path = Path(base_dir) / filename
        if path.suffix.lower() == ".svg":
            if not ElementTree.parse(path).getroot().tag.endswith("svg"):
                raise ValueError(f"SVGではない画像応答: {path}")
        else:
            with Image.open(path) as image:
                image.verify()
    return saved


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
    # 予備判定は設問の選択指示だけを見る。説明中の「誤り訂正」等は根拠にしない。
    # 「該当しない」「含まれない」等の断片肢は、本文の述語を補う命題が成立する
    # 側を選ぶため、否定語だけで反転しない。最終判定は02工程が独立に行う。
    negative_predicate = (
        r"(?:最も)?(?:不適切|不適当|誤っている|誤り(?:である)?|"
        r"間違っている|正しくない|適切でない|適当でない)"
    )
    selector = (
        r"(?:な|である)?(?:もの|記述|説明|組合せ|選択肢|対応|方法|"
        r"処置|行動|内容|行為|の)?(?:は|を)?(?:どれか|いずれか|選べ|選びなさい)"
    )
    if re.search(negative_predicate + selector, normalized):
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
            or NUMBERED_PAGE_HREF_RE.fullmatch(href)
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


def split_classification_hierarchy(classification: str | None) -> tuple[list[str], str | None, str | None, str | None]:
    """
    既存の category 文字列を 3 階層へ分解する。
    例: "テクノロジ系 » セキュリティ » 情報セキュリティ対策"
    """
    if not classification:
        return [], None, None, None

    parts = [
        normalize_inline_text(part)
        for part in re.split(r"\s*»\s*", classification)
        if normalize_inline_text(part)
    ]
    if not parts:
        return [], None, None, None

    major = parts[0] if len(parts) >= 1 else None
    middle = parts[1] if len(parts) >= 2 else None
    small = parts[2] if len(parts) >= 3 else None
    return parts, major, middle, small


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


def extract_q_text(element: Tag | None) -> str:
    if element is None:
        return ""
    # CSS生成の手順番号とHTMLの番号付きリストも問題本文の一部である。
    copied = BeautifulSoup(str(element), "html.parser").find(element.name)
    for item in copied.find_all("li"):
        number_class = next((c for c in (item.get("class") or []) if re.fullmatch(r"(?:li|maru)[0-9]+", c)), None)
        if number_class:
            # CSS counterはli1/maru1でリセットされ、クラス数字そのものではない。
            family = "maru" if number_class.startswith("maru") else "li"
            siblings = item.parent.find_all("li", recursive=False)
            counter = 0
            for sibling in siblings:
                classes = sibling.get("class") or []
                if f"{family}1" in classes:
                    counter = 0
                if any(re.fullmatch(rf"{family}[0-9]+", c) for c in classes):
                    counter += 1
                if sibling is item:
                    break
            marker = chr(0x2460 + counter - 1) if family == "maru" and 1 <= counter <= 20 else f"({counter})"
            item.insert(0, marker + " ")
        elif item.parent.name == "ol":
            counter = int(item.parent.get("start", "1"))
            for sibling in item.parent.find_all("li", recursive=False):
                counter = int(sibling.get("value", counter))
                if sibling is item:
                    break
                counter += 1
            marker = str(counter)
            list_type = item.parent.get("type", "1")
            if list_type in ("i", "I"):
                roman = ""
                remaining = counter
                for value, symbol in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
                    count, remaining = divmod(remaining, value)
                    roman += symbol * count
                marker = roman.lower() if list_type == "i" else roman
            elif list_type in ("a", "A"):
                marker = ""
                remaining = counter
                while remaining > 0:
                    remaining, digit = divmod(remaining - 1, 26)
                    marker = chr(ord("a") + digit) + marker
                if list_type == "A":
                    marker = marker.upper()
            item.insert(0, f"{marker}. ")
    # nw/sg/sc-siken の .ol は補集合や論理否定を表す上線である。
    return normalize_question_body_text(
        extract_text_with_subsup(
            copied, overline_classes=("ol", "dol"), fraction_classes=("frac",),
            radical_classes=("root",),
        )
    )


def extract_choice_text_from_li(li: Tag, marker: str) -> str:
    """
    li から選択肢本文を抽出する。
    - 選択ボタンだけを除き、数式の一部を囲むspanも含めて本文全体を読む。
    """
    copied = BeautifulSoup(str(li), "html.parser").find("li")
    buttons = copied.find_all("button", class_="selectBtn")
    for button in buttons:
        button.decompose()
    li_text = extract_q_text(copied)
    if not li_text:
        return ""
    if buttons:
        return li_text

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
    choice_markers = {"lia": 0, "lii": 1, "liu": 2, "lie": 3}
    explanation_items: list[Tag] = []
    for li in kaisetsu.find_all("li"):
        if not isinstance(li, Tag):
            continue
        class_list = li.get("class") or []
        if any(c in choice_markers for c in class_list):
            explanation_items.append(li)

    texts_by_marker = {}
    for li in explanation_items:
        marker = next(choice_markers[c] for c in li.get("class", []) if c in choice_markers)
        if marker in texts_by_marker:
            raise ValueError("同じ問題内で選択肢解説の記号が重複しています")
        texts_by_marker[marker] = extract_q_text(li)
    if set(texts_by_marker) == set(range(choice_count)) and all(texts_by_marker.values()):
        contents = kaisetsu.decode_contents()
        first_start = contents.find(str(explanation_items[0]))
        last_end = contents.rfind(str(explanation_items[-1])) + len(str(explanation_items[-1]))
        prefix = BeautifulSoup("<div>" + contents[:first_start] + "</div>", "html.parser").div
        summary = BeautifulSoup("<div>" + contents[last_end:] + "</div>", "html.parser").div
        prefix_text, summary_text = extract_q_text(prefix), extract_q_text(summary)
        snippets = [[texts_by_marker[index]] for index in range(choice_count)]
        return [prefix_text] if prefix_text else [], None, [summary_text] if summary_text else [], snippets, [None for _ in range(choice_count)]

    fallback = extract_q_text(kaisetsu)
    snippets = [[fallback] if fallback else [] for _ in range(choice_count)]
    return [], None, [], snippets, [None for _ in range(choice_count)]


def parse_q_question_page(
    html_text: str,
    page_url: str,
    *,
    http_session,
    download_images: bool,
    output_list_group_id: str,
    existing_identity: dict | None = None,
) -> dict | None:
    soup = BeautifulSoup(html_text, "html.parser")
    if soup.select_one(".mondai") is not None and soup.select_one("ol.selectList") is not None:
        return parse_numbered_question_page(
            html_text, page_url, http_session=http_session,
            download_images=download_images, output_list_group_id=output_list_group_id,
            existing_identity=existing_identity,
        )
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
    # 見出し内のメモ・電卓ボタンは問番号の一部ではない。
    question_number = re.search(r"問\s*([0-9]+)", normalize_digits(question_label))
    if question_number:
        question_label = f"問{question_number.group(1)}"

    mondai = soup.find(id="mondai")
    question_body_text = extract_q_text(mondai)
    if not question_body_text:
        return None

    source_question_id = f"{output_list_group_id}:am:{question_label}:{page_url}"
    if existing_identity and existing_identity.get("source_question_id") and existing_identity["source_question_id"] != source_question_id:
        raise ValueError(f"取得元IDが既存記録と一致しません: {page_url}")
    public_question_id = (
        existing_identity["public_question_id"]
        if existing_identity and existing_identity.get("public_question_id") else make_public_question_id(source_question_id)
    )

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
    explanation_source_images = extract_image_urls_from_element(soup.find(id="kaisetsu"), page_url)
    explanation_image_filenames = (
        download_and_save_images(
            http_session, explanation_source_images, f"q{public_question_id}_exp",
            base_dir=IMAGE_OUTPUT_DIR or ".",
        ) if download_images and explanation_source_images else []
    )
    classification = extract_classification_text(soup)
    category_hierarchy, category_major, category_middle, category_small = split_classification_hierarchy(
        classification
    )

    record = {
        "questionBodyText": question_body_text,
        "examLabel": exam_label,
        "questionLabel": question_label,
        "questionType": "true_false",
        "choiceTextList": choice_text_list,
        "originalQuestionChoiceImageUrls": choice_image_storage_urls_by_choice,
        "category": classification,
        "categoryHierarchy": category_hierarchy,
        "categoryMajor": category_major,
        "categoryMiddle": category_middle,
        "categorySmall": category_small,
        "examYear": exam_year,
        "list_group_id": output_list_group_id,
        "question_url": page_url,
        "public_question_id": public_question_id,
        "original_question_id": existing_identity.get("original_question_id", public_question_id) if existing_identity else public_question_id,
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
    if explanation_source_images:
        record["explanationImageSourceUrls"] = explanation_source_images
        record["explanationImageStorageUrls"] = [make_storage_url(name, QUALIFICATION_CODE) for name in explanation_image_filenames]
    return record


def _own_list_item(item: Tag) -> Tag:
    """省略されたli閉じタグによる兄弟の混入を除き、当該項目だけを読む。"""
    copied = BeautifulSoup(str(item), "html.parser").find("li")
    for nested in list(copied.find_all("li", recursive=False)):
        nested.decompose()
    return copied


def parse_numbered_question_page(
    html_text: str, page_url: str, *, http_session, download_images: bool,
    output_list_group_id: str, existing_identity: dict | None = None,
) -> dict:
    """番号式の過去問道場DOM。正答表示と同じ問のdata-answerを独立に照合する。"""
    soup = BeautifulSoup(html_text, "html.parser")
    path = re.fullmatch(r"/kakomon/(\d{4})/(\d{2})\.html", urlparse(page_url).path)
    if path is None:
        raise ValueError(f"番号式の問題URLではありません: {page_url}")
    year, number = map(int, path.groups())
    exam_label, heading_year, _ = extract_exam_meta_from_h2(soup)
    heading_number = re.search(r"問\s*(\d+)", exam_label)
    if heading_year != year or not heading_number or int(heading_number[1]) != number:
        raise ValueError(f"ページ題名とURLの年度・問番号が一致しません: {page_url}")
    body = soup.select_one(".mondai")
    choices = [_own_list_item(item) for item in soup.select("ol.selectList li")]
    answer = soup.select_one(".answerBox .answerChar")
    if body is None or answer is None or len(choices) != 4:
        raise ValueError(f"本文・正答・4選択肢がそろいません: {page_url}")
    answer_numbers = [int(value) for value in re.findall(r"\d+", answer.get_text(" ", strip=True))]
    marked = [index for index, item in enumerate(choices, 1) if item.get("data-answer") == "t"]
    if not answer_numbers or sorted(set(answer_numbers)) != marked:
        raise ValueError(f"正答表示とdata-answerが一致しません: {page_url}")
    source_id = make_url_source_question_id(QUALIFICATION_CODE, page_url)
    canonical_key = make_canonical_question_key(
        qualification_code=QUALIFICATION_CODE, exam_year=year, question_number=number,
    )
    public_id = make_public_question_id(canonical_key)
    if existing_identity:
        if existing_identity.get("source_question_id") != source_id:
            raise ValueError(f"既存の取得元IDが一致しません: {page_url}")
        public_id = existing_identity.get("public_question_id") or public_id

    def images(element: Tag, purpose: str) -> list[str]:
        urls = extract_image_urls_from_element(element, page_url)
        filenames = download_and_save_images(
            http_session, urls, f"dojo_q{public_id}_{purpose}", base_dir=IMAGE_OUTPUT_DIR or ".",
        ) if download_images and urls else []
        return [make_storage_url(filename, QUALIFICATION_CODE) for filename in filenames]

    question_images = images(body, "q")
    # kanaListのア～エ等はCSSの表示なので、本文へ明示して保持する。
    for ordered in body.select("ol.kanaList"):
        for index, item in enumerate(ordered.find_all("li", recursive=False)):
            item.insert(0, "アイウエオカキクケコ"[index] + "　")
    choice_texts = [normalize_question_body_text(extract_text_with_subsup(item)) for item in choices]
    if not all(choice_texts):
        raise ValueError(f"空の選択肢があります: {page_url}")
    explanation = soup.select_one("section.kaisetsu")
    if explanation is None:
        raise ValueError(f"解説がありません: {page_url}")
    explanation_images = images(explanation, "exp")
    heading = explanation.find("h3")
    if heading:
        heading.decompose()
    snippets = [[] for _ in choices]
    explanation_list = explanation.select_one("ol.kaisetsuList")
    # 個数・組合せ問題のア～エ解説を、回答候補1～4に結び付けない。
    if explanation_list is not None and "kanaList" not in explanation_list.get("class", []):
        items = [_own_list_item(item) for item in explanation_list.find_all("li")]
        if len(items) == len(choices):
            snippets = [[normalize_question_body_text(extract_text_with_subsup(item))] for item in items]
    for ordered in explanation.select("ol.kanaList"):
        for index, item in enumerate(ordered.find_all("li", recursive=False)):
            item.insert(0, "アイウエオカキクケコ"[index] + "　")
    classification = [normalize_inline_text(a.get_text(" ", strip=True)) for a in soup.select(".bunyalinks a")]
    selected = [choice_texts[index - 1] for index in answer_numbers]
    return {
        "questionBodyText": normalize_question_body_text(extract_text_with_subsup(body)),
        "examLabel": exam_label, "examYear": year, "questionLabel": f"問{number}",
        "list_group_id": output_list_group_id, "source_list_group_id": str(year),
        "choiceTextList": choice_texts,
        "correctChoiceText": selected[0] if len(selected) == 1 else selected,
        "answer_result_text": build_answer_result_text(answer_numbers),
        "answer_result_inferred_correct_choice_numbers": answer_numbers,
        "question_url": page_url, "source_question_id": source_id,
        "questionSourceSite": source_site_from_url(page_url),
        "canonical_question_key": canonical_key, "public_question_id": public_id,
        "original_question_id": existing_identity.get("original_question_id", public_id) if existing_identity else public_id,
        "source_public_question_id": make_public_question_id(source_id),
        "questionImageStorageUrls": question_images,
        "originalQuestionChoiceImageUrls": [images(item, f"c{index:02d}") for index, item in enumerate(choices, 1)],
        "explanationImageStorageUrls": explanation_images,
        "explanation_common_prefix": [normalize_question_body_text(extract_text_with_subsup(explanation))],
        "explanation_choice_snippets": snippets,
        "category": " » ".join(classification), "categoryHierarchy": classification,
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


def common_problem_blocks(soup: BeautifulSoup) -> list[Tag]:
    """問見出しの後から最初の設問までを、DOMの文書順で収集する。"""
    qno = soup.find("h3", class_="qno")
    if qno is None:
        return []
    blocks = []
    for node in qno.find_all_next():
        if node.name == "h3" and re.match(r"設問\s*[0-9０-９]+", node.get_text(strip=True)):
            break
        if node.name == "div" and "mondai" in (node.get("class") or []):
            if node.find(lambda tag: tag.name == "h3" and re.match(r"設問\s*[0-9０-９]+", tag.get_text(strip=True))) is not None:
                break
            if not any(parent in blocks for parent in node.parents):
                blocks.append(node)
    return blocks


def extract_common_problem_statement(soup: BeautifulSoup) -> str:
    return "\n\n".join(filter(None, (extract_q_text(block) for block in common_problem_blocks(soup))))


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
    h3 = node.find_previous(lambda tag: tag.name == "h3" and re.match(r"設問\s*[0-9０-９]+", tag.get_text(strip=True)))
    if h3 is None:
        return ""
    text = normalize_inline_text(h3.get_text(" ", strip=True))
    m = re.search(r"設問\s*([0-9０-９]+)", normalize_digits(text))
    return m.group(1) if m else ""


def pm_select_groups(input_box: Tag) -> list[list[Tag]]:
    """同一解答群から複数を選ぶ欄を一問に、名前付き空欄は別問にする。"""
    groups: dict[str, list[Tag]] = {}
    for select in input_box.find_all("select"):
        match = re.search(r"([a-z]+)$", select.get("name", ""))
        key = match[1] if match else "main"
        group = groups.setdefault(key, [])
        if group and parse_choices_from_select_options(group[0]) != parse_choices_from_select_options(select):
            raise ValueError("同一空欄の解答群が一致しません")
        group.append(select)
    return list(groups.values())


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
    existing_identities: dict[str, dict] | None = None,
) -> list[dict]:
    soup = BeautifulSoup(html_text, "html.parser")
    exam_label, exam_year, _ = extract_exam_meta_from_h2(soup)
    if exam_year is None:
        return []

    pm_question_no = find_pm_question_number(soup)
    common_statement = extract_common_problem_statement(soup)
    common_image_urls = list(dict.fromkeys(
        url for block in common_problem_blocks(soup)
        for url in extract_image_urls_from_element(block, page_url)
    ))
    common_image_filenames = (
        download_and_save_images(
            http_session, common_image_urls,
            f"pm{output_list_group_id}_q{pm_question_no or 'x'}_common",
            base_dir=IMAGE_OUTPUT_DIR or ".",
        ) if download_images and common_image_urls else []
    )
    common_image_storage_urls = [make_storage_url(name, QUALIFICATION_CODE) for name in common_image_filenames]

    question_bodies: list[dict] = []
    for input_box in soup.find_all("div", class_="inputAnswerBox"):
        select_tags = [group[0] for group in pm_select_groups(input_box)]
        if not select_tags:
            continue

        statement_div = input_box.find_previous("div", class_="mondai")
        statement_text = extract_q_text(statement_div)
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
        question_image_storage_urls = common_image_storage_urls + [make_storage_url(fname, QUALIFICATION_CODE) for fname in question_image_filenames]

        combined_body_lines = []
        if common_statement:
            combined_body_lines.append(common_statement)
        section_heading = input_box.find_previous(lambda tag: tag.name == "h3" and re.match(r"設問\s*[0-9０-９]+", tag.get_text(strip=True)))
        section_block = section_heading.find_parent("div", class_="mondai") if section_heading else None
        if section_block is not None and section_block is not statement_div:
            combined_body_lines.append(extract_q_text(section_block))
        elif section_heading is not None and section_block is None:
            combined_body_lines.append(extract_q_text(section_heading))
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
            identity = (existing_identities or {}).get(source_question_id, {})
            public_question_id = identity.get("public_question_id") or make_public_question_id(source_question_id)

            question_bodies.append(
                {
                    "questionBodyText": combined_body_text,
                    "examLabel": exam_label,
                    "questionLabel": question_label,
                    "questionType": "true_false",
                    "choiceTextList": choice_text_list,
                    "originalQuestionChoiceImageUrls": [[] for _ in choice_text_list],
                    "category": None,
                    "categoryHierarchy": [],
                    "categoryMajor": None,
                    "categoryMiddle": None,
                    "categorySmall": None,
                    "examYear": exam_year,
                    "list_group_id": output_list_group_id,
                    "question_url": page_url,
                    "public_question_id": public_question_id,
                    "original_question_id": identity.get("original_question_id", public_question_id),
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


def load_existing_identities(group_dir: Path) -> dict[str, dict]:
    """欠損sourceの復旧でも、過去のIDだけをexact source IDで継承する。"""
    identities: dict[str, dict] = {}
    for directory in ("00_source", "12_merged_questionType", "20_merged_1", "30_merged_2", "10_questionType_fixed", "15_correctChoiceText_fixed", "21_explanationText_added", "22_questionSetId_linked", "23_correctChoiceText_fixed"):
        for path in sorted((group_dir / directory).glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            records = payload if isinstance(payload, list) else payload.get("question_bodies", [])
            for record in records:
                keys = ("source_question_id", "question_url", "public_question_id", "original_question_id")
                if not record.get("question_url") or not record.get("original_question_id"):
                    continue
                identity = {key: record[key] for key in keys if record.get(key)}
                url = identity["question_url"]
                key = identity.get("source_question_id", url) if PM_PAGE_HREF_RE.fullmatch(urlparse(url).path.rsplit("/", 1)[-1]) else url
                previous = identities.get(key, {})
                if any(previous[key] != value for key, value in identity.items() if key in previous):
                    raise ValueError(f"既存記録のIDが競合しています: {url}")
                identities[key] = {**previous, **identity}
    # 旧sgsikenは同じ公開IDを両fieldへ保存していた。patchにだけ残る
    # original_question_idも、その取得契約に基づく公開IDのexact aliasである。
    for identity in identities.values():
        identity.setdefault("public_question_id", identity["original_question_id"])
    return identities


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_page_evidence(evidence_dir: Path, filename: str, html: str) -> None:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(evidence_dir / f"{filename}.gz", "wt", encoding="utf-8") as evidence:
        evidence.write(html)


def save_validated_source(json_dir: Path, group_id: str, records: list[dict], *, expected_count: int) -> dict:
    """全件検証後、既存のchunk内IDとfile名を保持して取得結果を保存する。"""
    source_ids = [record.get("source_question_id") for record in records]
    if len(records) != expected_count or not all(source_ids) or len(set(source_ids)) != len(source_ids):
        raise ValueError("取得件数又は取得元IDの検証に失敗しました")
    for record in records:
        choices = record.get("choiceTextList")
        answers = record.get("answer_result_inferred_correct_choice_numbers")
        if not record.get("questionBodyText") or not choices or not answers or any(
            not isinstance(answer, int) or not 1 <= answer <= len(choices) for answer in answers
        ):
            raise ValueError(f"取得内容が不完全です: {record['source_question_id']}")
    planned = {}
    new_ids, changed_ids, unchanged_ids = [], [], []
    for start in range(0, len(records), 25):
        path = json_dir / f"question_{group_id}_{start // 25 + 1}.json"
        chunk = records[start:start + 25]
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        old_records = old.get("question_bodies", []) if old else []
        if old and [r.get("source_question_id") for r in old_records] != [r["source_question_id"] for r in chunk]:
            raise ValueError(f"既存file内の取得元ID又は順序が変わっています: {path.name}")
        old_by_id = {r["source_question_id"]: r for r in old_records}
        for record in chunk:
            previous = old_by_id.get(record["source_question_id"])
            if previous and any(previous.get(key) != record.get(key) for key in ("public_question_id", "original_question_id")):
                raise ValueError(f"既存IDが変わっています: {record['source_question_id']}")
            target = new_ids if previous is None else unchanged_ids if previous == record else changed_ids
            target.append(record["source_question_id"])
        payload = {"list_group_id": group_id, "question_bodies": chunk}
        if payload != old:
            planned[path] = payload
    if set(json_dir.glob("question_*.json")) - set(
        json_dir / f"question_{group_id}_{i + 1}.json" for i in range((len(records) + 24) // 25)
    ):
        raise ValueError("取得一覧にない既存source fileがあります")
    for path, payload in planned.items():
        atomic_write_json(path, payload)
    return {"newSourceQuestionIds": new_ids, "changedSourceQuestionIds": changed_ids, "unchangedSourceQuestionIds": unchanged_ids}


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
    evidence_dir = Path(OUTPUT_DIR) / QUALIFICATION_CODE / "verification" / "dojo" / output_list_group_id
    save_page_evidence(evidence_dir, "index.html", list_html)
    q_urls, pm_urls = collect_question_page_urls(list_html, LIST_FIRST_PAGE_URL)
    if not q_urls and not pm_urls:
        raise ValueError("取得一覧に問題がありません")
    expected_count = int(os.environ.get("SCRAPER_EXPECTED_QUESTION_COUNT") or 0)
    if expected_count and MAX_QUESTIONS is not None:
        raise ValueError("全件更新では部分取得を使用できません")
    identities = load_existing_identities(Path(json_output_dir).parent)

    question_bodies: list[dict] = []
    discovered_count = len(q_urls)

    def can_add_more() -> bool:
        return MAX_QUESTIONS is None or len(question_bodies) < MAX_QUESTIONS

    for url in q_urls:
        if not can_add_more():
            break
        html = fetch_html_text(http_session, url)
        save_page_evidence(evidence_dir, urlparse(url).path.rsplit("/", 1)[-1], html)
        qb = parse_q_question_page(
            html,
            url,
            http_session=http_session,
            download_images=True,
            output_list_group_id=output_list_group_id,
            existing_identity=identities.get(url),
        )
        if qb is None:
            raise ValueError(f"問題の解析に失敗しました: {url}")
        question_bodies.append(qb)
        print(f"[FETCH] {output_list_group_id} {len(question_bodies)}/{len(q_urls)} {qb['questionLabel']}", flush=True)

    # 記述式午後を資料として別途取得するpresetでは、午前だけを問題JSONにする。
    include_afternoon = os.environ.get("SCRAPER_INCLUDE_AFTERNOON_QUESTIONS", "1") == "1"
    for url in pm_urls if include_afternoon else []:
        if not can_add_more():
            break
        html = fetch_html_text(http_session, url)
        save_page_evidence(evidence_dir, urlparse(url).path.rsplit("/", 1)[-1], html)
        page_expected_count = sum(len(pm_select_groups(box)) for box in BeautifulSoup(html, "html.parser").select("div.inputAnswerBox"))
        discovered_count += page_expected_count
        qbs = parse_pm_question_page(
            html,
            url,
            http_session=http_session,
            download_images=True,
            output_list_group_id=output_list_group_id,
            existing_identities=identities,
        )
        if not qbs or len(qbs) != page_expected_count:
            raise ValueError(f"午後問題の解析に失敗しました: {url}")
        for qb in qbs:
            if not can_add_more():
                break
            question_bodies.append(qb)

    if MAX_QUESTIONS is None:
        expected_count = expected_count or discovered_count
        if QUALIFICATION_CODE == "sg":
            from scripts.check.check_sgsiken_acquisition import audit_page
            by_url: dict[str, list[dict]] = {}
            for record in question_bodies:
                by_url.setdefault(record["question_url"], []).append(record)
            for url, records in by_url.items():
                with gzip.open(evidence_dir / (urlparse(url).path.rsplit("/", 1)[-1] + ".gz"), "rt", encoding="utf-8") as evidence:
                    errors = audit_page(evidence.read(), records, url)
                if errors:
                    raise ValueError(f"保存前HTML照合に失敗しました: {url}: {errors}")
        details = save_validated_source(Path(json_output_dir), output_list_group_id, question_bodies, expected_count=expected_count)
        report = {"status": "succeeded", "qualification": QUALIFICATION_CODE, "listGroupId": output_list_group_id,
                  "sourceListUrl": LIST_FIRST_PAGE_URL, "questionCount": len(question_bodies),
                  "singleQuestionPageCount": len(q_urls), "afternoonPageCount": len(pm_urls),
                  "includeAfternoonQuestions": include_afternoon,
                  "expectedQuestionCount": expected_count, "completedAt": datetime.now(timezone.utc).isoformat(), **details}
        atomic_write_json(Path(OUTPUT_DIR) / QUALIFICATION_CODE / "scrape_reports" / f"{output_list_group_id}.json", report)
    else:
        save_question_body_chunks(json_output_dir, output_list_group_id, question_bodies)
    print(f"[DONE] saved question bodies: {len(question_bodies)} -> {json_output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
