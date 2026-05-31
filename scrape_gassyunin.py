from __future__ import annotations

import os
import re
import sys
from collections import Counter
from typing import Iterable

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

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


QUALIFICATION_CODE = "gas-shunin-otsu"
QUALIFICATION_NAME = "ガス主任技術者乙種"
LIST_FIRST_PAGE_URL = "https://gassyunin.com/exam/otsu/otsu_2025/"
JSON_SUBDIR_NAME = "00_source"
MAX_QUESTIONS: int | None = None
OUTPUT_DIR = "/Users/yuki/development/exam_scraper/output"
IMAGE_OUTPUT_DIR: str | None = None

SUBJECT_DEFINITIONS = [
    ("law", "tab-content-law", "法令"),
    ("kiso", "tab-content-kiso", "基礎理論"),
    ("seizo", "tab-content-seizo", "製造"),
    ("kyokyu", "tab-content-kyokyu", "供給"),
    ("shohi", "tab-content-shohi", "消費機器"),
]

CHOICE_LINE_PATTERN = re.compile(
    r"^(?:[（(](?P<paren_marker>[^）)]+)[）)]|(?P<plain_marker>[イロハニホ]))\s*(?P<rest>.*)$"
)


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


def build_year_url(year_token: str) -> str:
    match = re.search(r"/exam/(?P<track>[^/]+)/(?P=track)_\d{4}/?$", LIST_FIRST_PAGE_URL)
    track = match.group("track") if match else "otsu"
    return f"https://gassyunin.com/exam/{track}/{track}_{year_token}/"


def extract_output_list_group_id(target_url: str) -> str | None:
    match = re.search(r"/exam/(?P<track>[^/]+)/(?P=track)_(\d{4})/?$", target_url)
    if match:
        return match.group(2)
    return None


def extract_exam_year_from_text(text: str) -> int | None:
    match = re.search(r"((?:19|20)\d{2})年", text)
    if match:
        return int(match.group(1))
    return None


def trim_exam_title(h1_text: str) -> str:
    return re.sub(r"\s*過去問題\s*$", "", h1_text).strip()


def determine_question_intent(question_text: str) -> str | None:
    normalized = normalize_inline_text(question_text)
    if not normalized:
        return None
    if any(token in normalized for token in ["正しいもの", "正しい記述", "正しいものの組合せ"]):
        return "select_correct"
    if any(
        token in normalized
        for token in ["誤っているもの", "不適当なもの", "不適切なもの", "いずれも誤っているもの"]
    ):
        return "select_incorrect"
    return None


def parse_correct_choice_numbers(text: str) -> list[int]:
    numbers = [
        int(token)
        for token in re.findall(r"\((\d+)\)", text or "")
        if token.isdigit()
    ]
    return numbers


def build_answer_result_text(correct_numbers: list[int]) -> str:
    if not correct_numbers:
        return ""
    joined = ", ".join(str(number) for number in correct_numbers)
    return f"正解は {joined} です。"


def collect_nodes_until_next_h2(question_heading: Tag) -> list[Tag]:
    nodes: list[Tag] = []
    node = question_heading.find_next_sibling()
    while node is not None:
        if getattr(node, "name", None) == "h2":
            break
        if isinstance(node, Tag):
            nodes.append(node)
        node = node.find_next_sibling()
    return nodes


def parse_choice_line(line: str) -> tuple[str, str] | None:
    match = CHOICE_LINE_PATTERN.match(line.strip())
    if match is None:
        return None
    marker = normalize_choice_marker(match.group("paren_marker") or match.group("plain_marker") or "")
    remainder = normalize_question_body_text(match.group("rest") or "")
    return marker, remainder


def extract_question_body_and_markers_from_nodes(nodes: Iterable[Tag]) -> tuple[str, list[str]]:
    body_lines: list[str] = []
    discovered_markers: list[str] = []
    choices_started = False

    for node in nodes:
        if node.name == "details":
            break
        if node.name != "p":
            continue
        text = normalize_question_body_text(node.get_text("\n", strip=True))
        if not text or text == "選択肢":
            continue
        for line in text.splitlines():
            stripped_line = line.strip()
            if not stripped_line:
                continue
            parsed_choice_line = parse_choice_line(stripped_line)
            if parsed_choice_line is not None:
                choices_started = True
                marker, _ = parsed_choice_line
                if marker:
                    discovered_markers.append(marker)
                continue
            if not choices_started:
                body_lines.append(stripped_line)

    return "\n".join(body_lines).strip(), discovered_markers


def extract_question_image_urls(nodes: Iterable[Tag], base_url: str) -> list[str]:
    image_urls: list[str] = []
    for node in nodes:
        if node.name == "details":
            break
        for image_url in extract_image_urls_from_element(node, base_url):
            if image_url not in image_urls:
                image_urls.append(image_url)
    return image_urls


def normalize_choice_marker(marker_text: str) -> str:
    return (marker_text or "").strip().strip("()（）").strip()


def build_explanation_common_prefix(details: Tag) -> list[str]:
    prefixes: list[str] = []

    point_box = details.find("div", class_="point-box")
    if point_box is not None:
        for item in point_box.find_all("li"):
            text = normalize_question_body_text(item.get_text("\n", strip=True))
            if text:
                prefixes.append(text)

    focus_heading = details.find("h3", string=lambda s: s and "焦点ポイント" in s)
    if focus_heading is not None:
        focus_blockquote = focus_heading.find_next_sibling("blockquote")
        if focus_blockquote is not None:
            focus_text = normalize_question_body_text(focus_blockquote.get_text("\n", strip=True))
            if focus_text:
                prefixes.append(focus_text)

    return prefixes


def render_choice_text_with_wrong_markers(node: Tag | NavigableString | None) -> str:
    if node is None:
        return ""
    if isinstance(node, NavigableString):
        return str(node)
    if node.name == "br":
        return "\n"

    rendered = "".join(render_choice_text_with_wrong_markers(child) for child in node.children)
    if "kw-wrong-inline" in (node.get("class") or []):
        return f"[wrong]{rendered}[/wrong]"
    return rendered


def collect_row_markers(row_records: list[dict]) -> list[str]:
    return [row_record["marker"] for row_record in row_records if row_record["marker"]]


def select_question_markers_for_audit(
    discovered_markers: list[str],
    judge_markers: list[str],
) -> list[str]:
    if not discovered_markers:
        return []
    if not judge_markers:
        return discovered_markers
    if len(discovered_markers) == len(judge_markers) and Counter(discovered_markers) == Counter(judge_markers):
        return discovered_markers

    matched_markers = [marker for marker in discovered_markers if marker in judge_markers]
    if len(matched_markers) == len(judge_markers) and Counter(matched_markers) == Counter(judge_markers):
        return matched_markers

    discovered_numeric_markers = [marker for marker in discovered_markers if marker.isdigit()]
    if all(marker.isdigit() for marker in judge_markers) and len(discovered_numeric_markers) >= len(judge_markers):
        return discovered_numeric_markers[: len(judge_markers)]

    return discovered_markers


def normalize_verdict_text(verdict_text: str) -> str:
    normalized = normalize_inline_text(verdict_text)
    normalized = re.sub(r"^[^0-9A-Za-zぁ-んァ-ン一-龥]+", "", normalized)
    return normalized.strip()


def verdict_text_to_correct_choice_text(
    verdict_text: str,
    question_body_text: str,
    fallback_class_list: list[str],
) -> str:
    normalized_verdict = normalize_verdict_text(verdict_text)
    if normalized_verdict == "正しい":
        return "正しい"
    if normalized_verdict == "誤っている":
        return "間違い"

    question_text = normalize_inline_text(question_body_text)
    family_rules = [
        {
            "verdict_true": "該当する",
            "verdict_false": "該当しない",
            "question_true_patterns": [r"該当する"],
            "question_false_patterns": [r"該当しない"],
        },
        {
            "verdict_true": "規定あり",
            "verdict_false": "規定なし",
            "question_true_patterns": [r"規定されている", r"規定している", r"規定がある"],
            "question_false_patterns": [r"規定されていない", r"規定していない", r"規定がない"],
        },
        {
            "verdict_true": "除外される",
            "verdict_false": "除外されない",
            "question_true_patterns": [r"除外される"],
            "question_false_patterns": [r"除外されない"],
        },
        {
            "verdict_true": "適合",
            "verdict_false": "不適合",
            "question_true_patterns": [r"適合"],
            "question_false_patterns": [r"不適合"],
        },
        {
            "verdict_true": "必要",
            "verdict_false": "不要",
            "question_true_patterns": [r"必要"],
            "question_false_patterns": [r"不要"],
        },
    ]

    for rule in family_rules:
        if normalized_verdict not in {rule["verdict_true"], rule["verdict_false"]}:
            continue
        asked_false = any(re.search(pattern, question_text) for pattern in rule["question_false_patterns"])
        asked_true = any(re.search(pattern, question_text) for pattern in rule["question_true_patterns"])

        if asked_false:
            return "正しい" if normalized_verdict == rule["verdict_false"] else "間違い"
        if asked_true:
            return "正しい" if normalized_verdict == rule["verdict_true"] else "間違い"

    if "statement-judge-correct" in fallback_class_list:
        return "正しい"
    if "statement-judge-wrong" in fallback_class_list:
        return "間違い"
    return "間違い"


def extract_choice_rows(details: Tag) -> list[dict]:
    row_records: list[dict] = []
    choices_heading = details.find("h3", string=lambda s: s and "各選択肢の判定" in s)
    if choices_heading is None:
        return row_records

    node = choices_heading.find_next_sibling()
    while node is not None:
        if getattr(node, "name", None) == "h3":
            break
        if not (
            isinstance(node, Tag)
            and node.name == "div"
            and any(re.fullmatch(r"statement-judge-(?:correct|wrong)", class_name) for class_name in (node.get("class") or []))
        ):
            node = node.find_next_sibling()
            continue

        block = node
        header = block.find("p", class_="judge-header")
        strong = header.find("strong") if header is not None else None
        marker = normalize_choice_marker(strong.get_text(" ", strip=True) if strong is not None else "")
        verdict_span = header.find("span", class_=re.compile(r"verdict-badge")) if header is not None else None
        verdict_text = verdict_span.get_text(" ", strip=True) if verdict_span is not None else ""
        blockquote = block.find("blockquote")
        blockquote_text = normalize_question_body_text(
            blockquote.get_text("\n", strip=True) if blockquote is not None else ""
        )
        marked_text = normalize_question_body_text(render_choice_text_with_wrong_markers(blockquote))

        snippet_lines: list[str] = []
        correct_text_line = block.find("p", class_="correct-text-line")
        if correct_text_line is not None:
            corrected = normalize_inline_text(correct_text_line.get_text(" ", strip=True))
            if corrected:
                snippet_lines.append(corrected)

        judge_meta = block.find("p", class_="judge-meta")
        if judge_meta is not None:
            meta_text = normalize_inline_text(judge_meta.get_text(" ", strip=True))
            if meta_text:
                snippet_lines.append(meta_text)

        row_records.append(
            {
                "marker": marker,
                "verdict_text": verdict_text,
                "plain_text": blockquote_text,
                "marked_text": marked_text,
                "class_list": block.get("class") or [],
                "snippet": ["\n".join(snippet_lines).strip()] if snippet_lines else [],
            }
        )
        node = node.find_next_sibling()

    return row_records


def parse_subject_section(
    subject_div: Tag,
    *,
    subject_code: str,
    subject_name: str,
    page_url: str,
    exam_title: str,
    exam_year: int | None,
    exam_occurrence_id: str,
    http_session,
    download_images: bool,
) -> list[dict]:
    questions: list[dict] = []
    question_headings = [
        heading
        for heading in subject_div.find_all("h2")
        if re.fullmatch(r"問\d+", heading.get_text(strip=True))
    ]

    for question_heading in question_headings:
        question_label = normalize_inline_text(question_heading.get_text(" ", strip=True))
        question_number_match = re.search(r"問(\d+)", question_label)
        question_number = question_number_match.group(1) if question_number_match else question_label
        question_url = f"{page_url}#{subject_code}-q{question_number}"
        source_question_id = f"{exam_occurrence_id}:{subject_code}:{question_label}"
        public_question_id = make_public_question_id(source_question_id)
        block_nodes = collect_nodes_until_next_h2(question_heading)
        details = next((node for node in block_nodes if node.name == "details"), None)

        question_body_text, discovered_question_markers = extract_question_body_and_markers_from_nodes(block_nodes)
        question_image_urls = extract_question_image_urls(block_nodes, page_url)
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
        question_image_storage_urls = [
            make_storage_url(filename, QUALIFICATION_CODE)
            for filename in question_image_filenames
        ]

        answer_result_text = ""
        answer_result_numbers: list[int] = []
        choice_text_list: list[str] = []
        choice_text_marked_list: list[str] = []
        correct_choice_texts: list[str] = []
        explanation_choice_snippets: list[list[str]] = []
        explanation_common_prefix: list[str] = []
        question_choice_markers: list[str] = []
        judge_choice_markers: list[str] = []
        choice_marker_source = "unknown"
        marker_alignment_mode = "no_marker_data"
        marker_mismatch_detected = False
        answer_result_numbers_remapped = False

        if details is not None:
            answer_heading = details.find("h3", string=lambda s: s and "正解" in s)
            answer_heading_text = normalize_inline_text(answer_heading.get_text(" ", strip=True)) if answer_heading else ""
            answer_result_numbers = parse_correct_choice_numbers(answer_heading_text)
            explanation_common_prefix = build_explanation_common_prefix(details)
            row_records = extract_choice_rows(details)
            judge_choice_markers = collect_row_markers(row_records)
            question_choice_markers = select_question_markers_for_audit(
                discovered_question_markers,
                judge_choice_markers,
            )

            if row_records:
                choice_marker_source = "judge"
                if question_choice_markers and judge_choice_markers and question_choice_markers != judge_choice_markers:
                    marker_mismatch_detected = True
                    marker_alignment_mode = "judge_priority_mismatch"
                    print(
                        "[WARN] marker sequence mismatch detected;"
                        f" preferring judge markers for {source_question_id}:"
                        f" question={question_choice_markers} judge={judge_choice_markers}"
                    )
                elif question_choice_markers and judge_choice_markers:
                    marker_alignment_mode = "judge_matches_question_markers"
                else:
                    marker_alignment_mode = "judge_only"
            elif question_choice_markers:
                choice_marker_source = "question"
                marker_alignment_mode = "question_only"

            answer_result_text = build_answer_result_text(answer_result_numbers)
            choice_text_list = [row_record["plain_text"] for row_record in row_records]
            choice_text_marked_list = [row_record["marked_text"] for row_record in row_records]
            correct_choice_texts = [
                verdict_text_to_correct_choice_text(
                    row_record["verdict_text"],
                    question_body_text,
                    row_record["class_list"],
                )
                for row_record in row_records
            ]
            explanation_choice_snippets = [row_record["snippet"] for row_record in row_records]

        exam_label = f"{exam_title} {subject_name}".strip()
        question_dict = {
            "questionBodyText": question_body_text,
            "examLabel": exam_label,
            "questionLabel": question_label,
            "questionType": "true_false" if choice_text_list else "flash_card",
            "choiceTextList": choice_text_list,
            "choiceTextMarkedList": choice_text_marked_list,
            "questionChoiceMarkers": question_choice_markers,
            "judgeChoiceMarkers": judge_choice_markers,
            "choiceMarkerSource": choice_marker_source,
            "markerAlignmentMode": marker_alignment_mode,
            "markerMismatchDetected": marker_mismatch_detected,
            "answerResultNumbersRemapped": answer_result_numbers_remapped,
            "originalQuestionChoiceImageUrls": [[] for _ in choice_text_list],
            "category": subject_name,
            "examYear": exam_year,
            "examOccurrenceId": exam_occurrence_id,
            "list_group_id": exam_occurrence_id,
            "question_url": question_url,
            "public_question_id": public_question_id,
            "questionImageStorageUrls": question_image_storage_urls,
            "questionIntent": determine_question_intent(question_body_text),
            "correctChoiceText": correct_choice_texts,
            "explanation_common_prefix": explanation_common_prefix,
            "explanation_common_prefix_inferred_correct_choice": (
                answer_result_numbers[0] if answer_result_numbers else None
            ),
            "explanation_common_summary": [],
            "explanation_choice_snippets": explanation_choice_snippets,
            "explanation_choice_correctness": [None for _ in choice_text_list],
            "answer_result_text": answer_result_text,
            "answer_result_inferred_correct_choice_numbers": answer_result_numbers,
            "source_question_id": source_question_id,
        }
        questions.append(question_dict)

    return questions


def parse_exam_page_html(
    html_text: str,
    page_url: str,
    *,
    http_session=None,
    download_images: bool = False,
) -> list[dict]:
    soup = BeautifulSoup(html_text, "html.parser")
    h1_tag = soup.find("h1")
    h1_text = normalize_inline_text(h1_tag.get_text(" ", strip=True) if h1_tag else "")
    exam_title = trim_exam_title(h1_text)
    exam_year = extract_exam_year_from_text(exam_title)
    exam_occurrence_id = str(exam_year) if exam_year is not None else (extract_output_list_group_id(page_url) or "")

    question_dicts: list[dict] = []
    for subject_code, subject_class_name, subject_name in SUBJECT_DEFINITIONS:
        subject_div = soup.find("div", class_=lambda value: value and subject_class_name in value.split())
        if subject_div is None:
            continue
        question_dicts.extend(
            parse_subject_section(
                subject_div,
                subject_code=subject_code,
                subject_name=subject_name,
                page_url=page_url,
                exam_title=exam_title,
                exam_year=exam_year,
                exam_occurrence_id=exam_occurrence_id,
                http_session=http_session,
                download_images=download_images,
            )
        )

    return question_dicts


def count_questions_by_subject(html_text: str) -> Counter[str]:
    soup = BeautifulSoup(html_text, "html.parser")
    counts: Counter[str] = Counter()
    for _, subject_class_name, subject_name in SUBJECT_DEFINITIONS:
        subject_div = soup.find("div", class_=lambda value: value and subject_class_name in value.split())
        if subject_div is None:
            continue
        counts[subject_name] = len(
            [
                heading
                for heading in subject_div.find_all("h2")
                if re.fullmatch(r"問\d+", heading.get_text(strip=True))
            ]
        )
    return counts


def main() -> None:
    load_local_secure_env()
    apply_runtime_overrides_from_env()

    target_url = LIST_FIRST_PAGE_URL
    if len(sys.argv) > 1:
        target_url = build_year_url(sys.argv[1])
        print(f"[INFO] Overriding LIST_FIRST_PAGE_URL = {target_url}")

    output_list_group_id = os.environ.get("SCRAPER_OUTPUT_LIST_GROUP_ID") or extract_output_list_group_id(target_url)
    print(f"[INFO] list_group_id = {output_list_group_id}")

    global IMAGE_OUTPUT_DIR
    json_output_dir, IMAGE_OUTPUT_DIR = prepare_output_dirs(
        OUTPUT_DIR,
        QUALIFICATION_CODE,
        output_list_group_id,
        JSON_SUBDIR_NAME,
    )

    http_session = create_http_session()
    html_text = fetch_html_text(http_session, target_url)
    question_dicts = parse_exam_page_html(
        html_text,
        target_url,
        http_session=http_session,
        download_images=True,
    )

    if MAX_QUESTIONS is not None:
        question_dicts = question_dicts[:MAX_QUESTIONS]
        print(f"[INFO] limit questions to first {len(question_dicts)} entries (MAX_QUESTIONS={MAX_QUESTIONS})")

    saved_paths = save_question_body_chunks(
        json_output_dir,
        output_list_group_id,
        question_dicts,
        base_filename_suffix="question",
    )
    for saved_path in saved_paths:
        print(f"[INFO] saved {saved_path}")

    counts = Counter(question["category"] for question in question_dicts)
    print(f"[INFO] parsed questions = {len(question_dicts)}")
    for subject_name in [subject_name for _, _, subject_name in SUBJECT_DEFINITIONS]:
        if subject_name in counts:
            print(f"[INFO] {subject_name}: {counts[subject_name]}")


if __name__ == "__main__":
    main()
