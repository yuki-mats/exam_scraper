from __future__ import annotations

import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

from scripts.merge.patch_views import infer_question_intent_from_text
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


QUALIFICATION_CODE = "judoseifukushi"
QUALIFICATION_NAME = "柔道整復師"
LIST_FIRST_PAGE_URL = "https://kurohon.jp/gakusei/exams_js/js_34/"
JSON_SUBDIR_NAME = "00_source"
MAX_QUESTIONS: int | None = None
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
IMAGE_OUTPUT_DIR: str | None = None

FULLWIDTH_DIGIT_TRANS = str.maketrans("０１２３４５６７８９", "0123456789")
QUESTION_TITLE_RE = re.compile(r"問題\s*([0-9０-９]+)\s*[．.、。]?\s*(.*)")
CHOICE_TEXT_RE = re.compile(r"^\s*([0-9０-９]+)\s*[．.。):）]?\s*(.*)$")


@dataclass(frozen=True)
class ChoiceRecord:
    choice_number: int
    choice_text: str
    is_marked_correct: bool
    image_urls: list[str]


@dataclass(frozen=True)
class ParsedQuestion:
    ordinal: int
    displayed_question_number: int | None
    question_body_text: str
    session_name: str | None
    choices: list[ChoiceRecord]
    class_correct_choice_numbers: list[int]
    answer_table_choice_numbers: list[int]
    source_answer_status: str


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


def normalize_digit_text(value: str) -> str:
    return (value or "").translate(FULLWIDTH_DIGIT_TRANS)


def parse_int_token(value: str) -> int | None:
    normalized = normalize_digit_text(value).strip()
    return int(normalized) if normalized.isdigit() else None


def extract_round_number_from_url(page_url: str) -> int | None:
    match = re.search(r"/(?:am|js|hq)_([0-9]+)/?$", page_url)
    if not match:
        return None
    return int(match.group(1))


def extract_round_number_from_title(soup: BeautifulSoup) -> int | None:
    h1_tag = soup.select_one("main#single-exams h1, h1")
    if h1_tag is None:
        return None
    match = re.search(r"第\s*([0-9０-９]+)\s*回", h1_tag.get_text(" ", strip=True))
    if not match:
        return None
    return parse_int_token(match.group(1))


def infer_exam_year(
    *,
    output_list_group_id: str | None,
    round_number: int | None,
) -> int | None:
    if output_list_group_id and re.fullmatch(r"(?:19|20)\d{2}", output_list_group_id):
        return int(output_list_group_id)
    if round_number is not None:
        return 1992 + round_number
    return None


def build_exam_occurrence_id(exam_year: int | None, round_number: int | None) -> str:
    parts: list[str] = []
    if exam_year is not None:
        parts.append(str(exam_year))
    if round_number is not None:
        parts.append(f"r{round_number}")
    return "-".join(parts)


def extract_answer_table_by_ordinal(soup: BeautifulSoup) -> dict[int, list[int]]:
    answer_numbers_by_ordinal: dict[int, list[int]] = {}
    for ordinal, th in enumerate(soup.select("th.past-question__all-head"), start=1):
        td = th.find_next_sibling("td")
        if td is None:
            answer_numbers_by_ordinal[ordinal] = []
            continue
        answer_text = normalize_digit_text(normalize_inline_text(td.get_text(" ", strip=True)))
        numbers: list[int] = []
        for token in re.findall(r"\d+", answer_text):
            number = int(token)
            if number not in numbers:
                numbers.append(number)
        answer_numbers_by_ordinal[ordinal] = numbers
    return answer_numbers_by_ordinal


def extract_question_body_from_title(title_text: str) -> tuple[int | None, str, bool]:
    title_text = normalize_inline_text(title_text)
    no_answer = "※解なし" in title_text or "解なし" in title_text
    cleaned_title = title_text.replace("※解なし", "").strip()
    match = QUESTION_TITLE_RE.search(cleaned_title)
    if match is None:
        return None, normalize_question_body_text(cleaned_title), no_answer
    displayed_number = parse_int_token(match.group(1))
    question_body_text = normalize_question_body_text(match.group(2))
    return displayed_number, question_body_text, no_answer


def extract_session_name(question_box: Tag) -> str | None:
    heading = question_box.find_previous("h2")
    if heading is None:
        return None
    text = normalize_inline_text(heading.get_text(" ", strip=True))
    if not text or "解答" in text:
        return None
    return text


def iter_answer_wraps_for_question(question_box: Tag) -> list[Tag]:
    answer_wraps: list[Tag] = []
    node = question_box.next_sibling
    while node is not None:
        if isinstance(node, Tag) and node.name == "div" and "past-question__list" in (node.get("class") or []):
            break
        if isinstance(node, Tag) and node.name == "div" and "past-question__answer-wrap" in (node.get("class") or []):
            answer_wraps.append(node)
        node = node.next_sibling
    return answer_wraps


def parse_choice_record(choice_tag: Tag, page_url: str, fallback_number: int) -> ChoiceRecord:
    class_list = choice_tag.get("class") or []
    text = normalize_inline_text(choice_tag.get_text(" ", strip=True))
    match = CHOICE_TEXT_RE.match(text)
    if match is None:
        choice_number = fallback_number
        choice_text = text
    else:
        choice_number = parse_int_token(match.group(1)) or fallback_number
        choice_text = normalize_question_body_text(match.group(2))
    image_urls = extract_image_urls_from_element(choice_tag, page_url)
    return ChoiceRecord(
        choice_number=choice_number,
        choice_text=choice_text,
        is_marked_correct="past-question__answer--true" in class_list,
        image_urls=image_urls,
    )


def parse_questions_from_html(
    html_text: str,
    page_url: str,
) -> tuple[list[ParsedQuestion], int | None]:
    soup = BeautifulSoup(html_text, "html.parser")
    round_number = extract_round_number_from_title(soup) or extract_round_number_from_url(page_url)
    answer_table_by_ordinal = extract_answer_table_by_ordinal(soup)

    questions: list[ParsedQuestion] = []
    for ordinal, question_box in enumerate(soup.select("div.past-question__list"), start=1):
        title_tag = question_box.select_one("p.past-question__title")
        if title_tag is None:
            continue
        displayed_number, question_body_text, no_answer_in_title = extract_question_body_from_title(
            title_tag.get_text(" ", strip=True)
        )

        choices: list[ChoiceRecord] = []
        for answer_wrap in iter_answer_wraps_for_question(question_box):
            for choice_index, choice_tag in enumerate(
                answer_wrap.select("p.past-question__answer, p.past-question__answer--true"),
                start=1,
            ):
                choices.append(parse_choice_record(choice_tag, page_url, choice_index))

        class_correct_numbers = [
            choice.choice_number
            for choice in choices
            if choice.is_marked_correct and 1 <= choice.choice_number <= len(choices)
        ]
        answer_table_numbers = [
            number
            for number in answer_table_by_ordinal.get(ordinal, [])
            if 1 <= number <= len(choices)
        ]

        if no_answer_in_title and not answer_table_numbers:
            source_answer_status = "no_answer"
        elif answer_table_numbers:
            source_answer_status = "answer_table"
        elif class_correct_numbers:
            source_answer_status = "choice_class"
        else:
            source_answer_status = "missing_answer"

        questions.append(
            ParsedQuestion(
                ordinal=ordinal,
                displayed_question_number=displayed_number,
                question_body_text=question_body_text,
                session_name=extract_session_name(question_box),
                choices=choices,
                class_correct_choice_numbers=class_correct_numbers,
                answer_table_choice_numbers=answer_table_numbers,
                source_answer_status=source_answer_status,
            )
        )

    return questions, round_number


def build_answer_result_text(answer_numbers: list[int], *, no_answer: bool = False) -> str:
    if no_answer:
        return "解なしです。"
    joined = ", ".join(str(number) for number in answer_numbers)
    return f"正解は {joined} です。" if joined else ""


def build_correct_choice_texts(
    *,
    choice_count: int,
    answer_numbers: list[int],
    question_intent: str,
    no_answer: bool,
) -> list[str | None]:
    if no_answer:
        return [None for _ in range(choice_count)]
    answer_indexes = {number - 1 for number in answer_numbers}
    if question_intent == "select_incorrect":
        labels = ["正しい" for _ in range(choice_count)]
        for index in answer_indexes:
            labels[index] = "間違い"
        return labels
    labels = ["間違い" for _ in range(choice_count)]
    for index in answer_indexes:
        labels[index] = "正しい"
    return labels


def save_choice_images_by_choice(
    http_session: requests.Session,
    parsed_question: ParsedQuestion,
    public_question_id: str,
    *,
    download_images: bool,
) -> list[list[str]]:
    saved_urls_by_choice: list[list[str]] = []
    for choice in parsed_question.choices:
        if not download_images or not choice.image_urls:
            saved_urls_by_choice.append([])
            continue
        filenames = download_and_save_images(
            http_session,
            choice.image_urls,
            f"q{public_question_id}_ch{choice.choice_number:02d}",
            base_dir=IMAGE_OUTPUT_DIR or ".",
        )
        saved_urls_by_choice.append([make_storage_url(filename, QUALIFICATION_CODE) for filename in filenames])
    return saved_urls_by_choice


def save_question_images(
    http_session: requests.Session,
    parsed_question: ParsedQuestion,
    public_question_id: str,
    *,
    download_images: bool,
) -> list[str]:
    if not download_images:
        return []
    # 国試黒本の現行ページでは問題本文画像は見当たらないが、将来の差分に備えて title 内だけ拾う。
    image_urls: list[str] = []
    if not image_urls:
        return []
    filenames = download_and_save_images(
        http_session,
        image_urls,
        f"q{public_question_id}_q",
        base_dir=IMAGE_OUTPUT_DIR or ".",
    )
    return [make_storage_url(filename, QUALIFICATION_CODE) for filename in filenames]


def parsed_question_to_dict(
    parsed_question: ParsedQuestion,
    *,
    page_url: str,
    output_list_group_id: str,
    round_number: int | None,
    exam_year: int | None,
    exam_occurrence_id: str,
    http_session: requests.Session,
    download_images: bool,
) -> dict[str, Any]:
    source_question_id = (
        f"{QUALIFICATION_CODE}:{output_list_group_id}:"
        f"r{round_number or 'unknown'}:q{parsed_question.ordinal:03d}"
    )
    public_question_id = make_public_question_id(source_question_id)
    answer_numbers = (
        parsed_question.answer_table_choice_numbers
        or parsed_question.class_correct_choice_numbers
    )
    no_answer = parsed_question.source_answer_status == "no_answer"
    question_intent = infer_question_intent_from_text(parsed_question.question_body_text) or "select_correct"
    choice_texts = [choice.choice_text for choice in parsed_question.choices]
    exam_label_parts = []
    if round_number is not None and exam_year is not None:
        exam_label_parts.append(f"第{round_number}回（{exam_year}年）")
    elif round_number is not None:
        exam_label_parts.append(f"第{round_number}回")
    if parsed_question.session_name:
        exam_label_parts.append(parsed_question.session_name)
    exam_label = " ".join(exam_label_parts).strip()

    return {
        "questionBodyText": parsed_question.question_body_text,
        "qualificationName": QUALIFICATION_NAME,
        "examLabel": exam_label,
        "questionLabel": f"問{parsed_question.ordinal}",
        "questionType": "true_false",
        "choiceTextList": choice_texts,
        "originalQuestionChoiceImageUrls": save_choice_images_by_choice(
            http_session,
            parsed_question,
            public_question_id,
            download_images=download_images,
        ),
        "category": parsed_question.session_name,
        "examYear": exam_year,
        "examOccurrenceId": exam_occurrence_id,
        "list_group_id": output_list_group_id,
        "question_url": urljoin(page_url, f"#q{parsed_question.ordinal}"),
        "public_question_id": public_question_id,
        "original_question_id": public_question_id,
        "questionImageStorageUrls": save_question_images(
            http_session,
            parsed_question,
            public_question_id,
            download_images=download_images,
        ),
        "questionIntent": question_intent,
        "correctChoiceText": build_correct_choice_texts(
            choice_count=len(choice_texts),
            answer_numbers=answer_numbers,
            question_intent=question_intent,
            no_answer=no_answer,
        ),
        "explanation_common_prefix": [],
        "explanation_common_prefix_inferred_correct_choice": answer_numbers[0] if answer_numbers else None,
        "explanation_common_summary": [],
        "explanation_choice_snippets": [[] for _ in choice_texts],
        "explanation_choice_correctness": [None for _ in choice_texts],
        "answer_result_text": build_answer_result_text(answer_numbers, no_answer=no_answer),
        "answer_result_inferred_correct_choice_numbers": answer_numbers,
        "source_question_id": source_question_id,
        "sourceDisplayedQuestionNumber": parsed_question.displayed_question_number,
        "sourceQuestionOrdinal": parsed_question.ordinal,
        "sourceAnswerStatus": parsed_question.source_answer_status,
        "answerTableCorrectChoiceNumbers": parsed_question.answer_table_choice_numbers,
        "choiceClassCorrectChoiceNumbers": parsed_question.class_correct_choice_numbers,
    }


def parse_exam_page_html(
    html_text: str,
    page_url: str,
    *,
    output_list_group_id: str | None = None,
    http_session: requests.Session | None = None,
    download_images: bool = False,
) -> list[dict[str, Any]]:
    parsed_questions, round_number = parse_questions_from_html(html_text, page_url)
    resolved_output_list_group_id = (
        output_list_group_id
        or os.environ.get("SCRAPER_OUTPUT_LIST_GROUP_ID")
        or str(infer_exam_year(output_list_group_id=None, round_number=round_number) or "")
    )
    exam_year = infer_exam_year(
        output_list_group_id=resolved_output_list_group_id,
        round_number=round_number,
    )
    exam_occurrence_id = build_exam_occurrence_id(exam_year, round_number)
    session = http_session or create_http_session()

    return [
        parsed_question_to_dict(
            parsed_question,
            page_url=page_url,
            output_list_group_id=resolved_output_list_group_id,
            round_number=round_number,
            exam_year=exam_year,
            exam_occurrence_id=exam_occurrence_id,
            http_session=session,
            download_images=download_images,
        )
        for parsed_question in parsed_questions
    ]


def count_questions_by_status(question_dicts: list[dict[str, Any]]) -> Counter[str]:
    return Counter(str(question.get("sourceAnswerStatus") or "") for question in question_dicts)


def main() -> int:
    load_local_secure_env()
    apply_runtime_overrides_from_env()

    output_list_group_id = os.environ.get("SCRAPER_OUTPUT_LIST_GROUP_ID")
    if not output_list_group_id:
        round_number = extract_round_number_from_url(LIST_FIRST_PAGE_URL)
        inferred_year = infer_exam_year(output_list_group_id=None, round_number=round_number)
        output_list_group_id = str(inferred_year) if inferred_year is not None else None
    if not output_list_group_id:
        raise RuntimeError("SCRAPER_OUTPUT_LIST_GROUP_ID を指定できず、URL から年度も推定できません。")

    json_output_dir, image_output_dir = prepare_output_dirs(
        OUTPUT_DIR,
        QUALIFICATION_CODE,
        output_list_group_id,
        JSON_SUBDIR_NAME,
    )

    global IMAGE_OUTPUT_DIR
    IMAGE_OUTPUT_DIR = image_output_dir

    http_session = create_http_session()
    html_text = fetch_html_text(http_session, LIST_FIRST_PAGE_URL)
    question_dicts = parse_exam_page_html(
        html_text,
        LIST_FIRST_PAGE_URL,
        output_list_group_id=output_list_group_id,
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

    print(f"[INFO] list_group_id = {output_list_group_id}")
    print(f"[INFO] parsed questions = {len(question_dicts)}")
    for status, count in sorted(count_questions_by_status(question_dicts).items()):
        print(f"[INFO] sourceAnswerStatus {status}: {count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
