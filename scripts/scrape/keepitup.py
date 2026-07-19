from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import tempfile
import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

from scripts.scrape.common import (
    create_http_session,
    guess_image_extension,
    load_local_secure_env,
    make_public_question_id,
    make_storage_url,
    make_url_source_question_id,
    normalize_inline_text,
    normalize_question_body_text,
    prepare_output_dirs,
    source_site_from_url,
)


KEEPITUP_MIN_DELAY_SEC_ENV = "KEEPITUP_MIN_DELAY_SEC"
KEEPITUP_MAX_DELAY_SEC_ENV = "KEEPITUP_MAX_DELAY_SEC"
QUESTION_SET_RE = re.compile(r"^[A-Z][A-Z0-9]*\d{3}[CS]$")
QUESTION_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*\d{3}[CS]\d{3}$")
QUESTION_URL_RE = re.compile(r"/(?P<question_id>[A-Z][A-Z0-9]*\d{3}[CS]\d{3})Q/$")
ANSWER_URL_RE = re.compile(r"/(?P<question_id>[A-Z][A-Z0-9]*\d{3}[CS]\d{3})A/$")
INCORRECT_PATTERNS = (
    r"誤っている",
    r"誤り",
    r"正しくない",
    r"不適切",
    r"不適当",
    r"適切でない",
    r"適当でない",
    r"含まれない",
    r"該当しない",
    r"対象とならない",
)


@dataclass(frozen=True)
class KeepItUpCourse:
    question_set_ids: tuple[str, ...]
    random_question_url: str


@dataclass(frozen=True)
class KeepItUpIndexQuestion:
    question_id: str
    question_set_id: str
    question_url: str
    answer_url: str
    category: str
    title: str


@dataclass(frozen=True)
class KeepItUpListPage:
    questions: tuple[KeepItUpIndexQuestion, ...]
    pagination_urls: tuple[str, ...]


@dataclass(frozen=True)
class KeepItUpParsedQuestion:
    question_id: str
    question_set_id: str
    title: str
    question_text: str
    choices: tuple[str, ...]
    correct_choice_numbers: tuple[int, ...]
    selection_type: str
    explanation_text: str
    question_image_urls: tuple[str, ...]
    choice_image_urls: tuple[tuple[str, ...], ...]
    explanation_image_urls: tuple[str, ...]
    reference_urls: tuple[dict[str, str], ...]


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)


def _form_values(form: Tag) -> dict[str, str]:
    values: dict[str, str] = {}
    for input_element in form.select("input[name]"):
        name = str(input_element.get("name") or "").strip()
        if name:
            values[name] = str(input_element.get("value") or "").strip()
    return values


def discover_course(html: str, *, page_url: str) -> KeepItUpCourse:
    soup = BeautifulSoup(html, "html.parser")
    course_code = Path(urlparse(page_url).path).name.upper()
    course_prefix_match = re.match(r"[A-Z]+", course_code)
    course_prefix = course_prefix_match.group(0) if course_prefix_match else ""
    question_set_ids: list[str] = []
    random_question_url = ""
    for form in soup.select("form[action]"):
        values = _form_values(form)
        question_set_id = values.get("QSET_ID", "")
        action = urljoin(page_url, str(form.get("action") or ""))
        if (
            QUESTION_SET_RE.fullmatch(question_set_id)
            and question_set_id.startswith(course_prefix)
            and values.get("ACTION_ID") == "Start"
            and action.rstrip("/").endswith(f"/{question_set_id}001Q")
        ):
            question_set_ids.append(question_set_id)
        if (
            re.fullmatch(r"[A-Z0-9]+99---", question_set_id)
            and question_set_id.startswith(course_prefix)
        ):
            random_question_url = action

    unique_sets = _dedupe(question_set_ids)
    if not unique_sets:
        raise ValueError(f"問題系列を発見できません: {page_url}")
    if not random_question_url:
        raise ValueError(f"ランダム出題ページを発見できません: {page_url}")
    return KeepItUpCourse(
        question_set_ids=unique_sets,
        random_question_url=random_question_url,
    )


def parse_declared_question_count(html: str, *, page_url: str) -> int:
    text = normalize_inline_text(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    match = re.search(r"ランダム出題\s*[（(]\s*([0-9][0-9,]*)\s*問", text)
    if not match:
        raise ValueError(f"ランダム出題の問題数を取得できません: {page_url}")
    return int(match.group(1).replace(",", ""))


def _category_from_list_page(contents: Tag, question_set_id: str) -> str:
    heading = contents.find("h3")
    if heading is None:
        return question_set_id
    parts = [normalize_inline_text(text) for text in heading.stripped_strings]
    parts = [part for part in parts if part]
    if len(parts) >= 2:
        return " ".join(parts[1:])
    return parts[0] if parts else question_set_id


def parse_list_page(
    html: str,
    *,
    page_url: str,
    question_set_id: str,
) -> KeepItUpListPage:
    soup = BeautifulSoup(html, "html.parser")
    contents = soup.select_one("#contents")
    if contents is None:
        raise ValueError(f"#contents要素がありません: {page_url}")
    category = _category_from_list_page(contents, question_set_id)
    questions: list[KeepItUpIndexQuestion] = []
    pagination_urls: list[str] = []

    for form in contents.select("form[action]"):
        values = _form_values(form)
        action = urljoin(page_url, str(form.get("action") or ""))
        action_id = values.get("ACTION_ID", "")
        form_question_set_id = values.get("QSET_ID", "")
        if form_question_set_id != question_set_id:
            continue
        if QUESTION_ID_RE.fullmatch(action_id):
            match = QUESTION_URL_RE.search(urlparse(action).path)
            if not match or match.group("question_id") != action_id:
                raise ValueError(
                    f"一覧の問題IDとURLが一致しません: id={action_id} url={action}"
                )
            row = form.find_parent("tr")
            title = ""
            if row is not None:
                cells = row.find_all("td", recursive=False)
                if cells:
                    title = normalize_inline_text(cells[0].get_text(" ", strip=True))
                    title = title.replace("（現在実行中の問題）", "").strip()
                    title = re.sub(r"^第\s*[0-9]+\s*問\s*", "", title).strip()
            question_id = action_id
            questions.append(
                KeepItUpIndexQuestion(
                    question_id=question_id,
                    question_set_id=question_set_id,
                    question_url=action,
                    answer_url=re.sub(r"Q/$", "A/", action),
                    category=category,
                    title=title,
                )
            )
        elif action_id == "List":
            pagination_urls.append(action)

    if not questions:
        raise ValueError(f"問題一覧が空です: {page_url}")
    return KeepItUpListPage(
        questions=tuple(questions),
        pagination_urls=_dedupe(pagination_urls),
    )


def _image_urls(element: Tag | None, base_url: str) -> tuple[str, ...]:
    if element is None:
        return ()
    urls: list[str] = []
    for image in element.select("img"):
        if image.find_parent("a", href=re.compile(r"valuecommerce\.com")):
            continue
        raw = str(image.get("data-src") or image.get("src") or "").strip()
        if not raw:
            continue
        url = urljoin(base_url, raw)
        if urlparse(url).netloc.lower() == "aws.keepitup.jp":
            urls.append(url)
    return _dedupe(urls)


def _clean_choice_text(choice: Tag) -> str:
    text = normalize_inline_text(choice.get_text(" ", strip=True))
    return re.sub(r"\s*\[正しい解答\]\s*$", "", text).strip()


def _explanation_container(contents: Tag, page_url: str) -> Tag:
    for enclosure in contents.select(".enclosure"):
        heading = enclosure.find("h3")
        if heading and normalize_inline_text(heading.get_text(" ", strip=True)) == "徹底解説":
            return enclosure
    raise ValueError(f"徹底解説を取得できません: {page_url}")


def _clean_explanation(container: Tag) -> tuple[str, BeautifulSoup]:
    clone = BeautifulSoup(str(container), "html.parser")
    root = clone.select_one(".enclosure") or clone
    for unwanted in root.select("h3, form, script, style, .btn_pos, .text_center"):
        unwanted.decompose()
    text = normalize_question_body_text(root.get_text("\n", strip=True))
    return text, clone


def _reference_urls(container: Tag, page_url: str) -> tuple[dict[str, str], ...]:
    references: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in container.select("a[href]"):
        raw = str(anchor.get("href") or "").strip()
        url = urljoin(page_url, raw)
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            continue
        if "valuecommerce.com" in parsed.netloc.lower() or url in seen:
            continue
        seen.add(url)
        references.append(
            {
                "title": normalize_inline_text(anchor.get_text(" ", strip=True)) or url,
                "url": url,
            }
        )
    return tuple(references)


def parse_answer_page(
    html: str,
    *,
    page_url: str,
    expected_question_id: str | None = None,
) -> KeepItUpParsedQuestion:
    match = ANSWER_URL_RE.search(urlparse(page_url).path)
    url_question_id = match.group("question_id") if match else ""
    if not url_question_id:
        raise ValueError(f"解答URLから問題IDを取得できません: {page_url}")
    if expected_question_id and url_question_id != expected_question_id:
        raise ValueError(
            f"期待した問題IDと解答URLが一致しません: expected={expected_question_id} actual={url_question_id}"
        )

    soup = BeautifulSoup(html, "html.parser")
    contents = soup.select_one("#contents")
    if contents is None:
        raise ValueError(f"#contents要素がありません: {page_url}")
    id_text = normalize_inline_text(contents.get_text(" ", strip=True))
    id_match = re.search(r"問題ID[：:]\s*([A-Z][A-Z0-9]*\d{3}[CS]\d{3})", id_text)
    if not id_match or id_match.group(1) != url_question_id:
        raise ValueError(f"本文の問題IDとURLが一致しません: {page_url}")

    heading = contents.find("h3")
    if heading is None:
        raise ValueError(f"問題タイトルを取得できません: {page_url}")
    title = normalize_inline_text(heading.get_text(" ", strip=True))
    title = re.sub(r"^第\s*[0-9]+\s*問\s*", "", title).strip()
    question_element = heading.find_next_sibling("p")
    if question_element is None:
        raise ValueError(f"問題文を取得できません: {page_url}")
    question_text = normalize_question_body_text(question_element.get_text("\n", strip=True))

    option_list = contents.select_one("ol.options_A") or contents.select_one("ol[class^='options_']")
    if option_list is None:
        raise ValueError(f"選択肢を取得できません: {page_url}")
    option_elements = option_list.find_all("li", recursive=False)
    choices = tuple(_clean_choice_text(option) for option in option_elements)
    correct_numbers = tuple(
        index
        for index, option in enumerate(option_elements, start=1)
        if option.select_one(".correct_answer") is not None
    )
    if not correct_numbers:
        raise ValueError(f"正答を取得できません: {page_url}")

    explanation_container = _explanation_container(contents, page_url)
    explanation_text, cleaned_explanation = _clean_explanation(explanation_container)
    cleaned_root = cleaned_explanation.select_one(".enclosure") or cleaned_explanation
    question_set_id = url_question_id[:-3]
    return KeepItUpParsedQuestion(
        question_id=url_question_id,
        question_set_id=question_set_id,
        title=title,
        question_text=question_text,
        choices=choices,
        correct_choice_numbers=correct_numbers,
        selection_type="checkbox" if question_set_id.endswith("S") else "radio",
        explanation_text=explanation_text,
        question_image_urls=_image_urls(question_element, page_url),
        choice_image_urls=tuple(_image_urls(option, page_url) for option in option_elements),
        explanation_image_urls=_image_urls(cleaned_root, page_url),
        reference_urls=_reference_urls(cleaned_root, page_url),
    )


def determine_question_intent(question_text: str) -> str:
    normalized = re.sub(r"\s+", "", question_text or "")
    if any(re.search(pattern, normalized) for pattern in INCORRECT_PATTERNS):
        return "select_incorrect"
    return "select_correct"


def choice_truth_labels(
    *,
    choice_count: int,
    correct_choice_numbers: Iterable[int],
    question_intent: str,
) -> list[str]:
    selected = {int(number) for number in correct_choice_numbers}
    selected_label = "間違い" if question_intent == "select_incorrect" else "正しい"
    unselected_label = "正しい" if question_intent == "select_incorrect" else "間違い"
    return [
        selected_label if index in selected else unselected_label
        for index in range(1, choice_count + 1)
    ]


def build_answer_result_text(correct_choice_numbers: Iterable[int]) -> str:
    numbers = [int(number) for number in correct_choice_numbers]
    return f"正解は {', '.join(str(number) for number in numbers)} です。"


def _delay_range() -> tuple[float, float]:
    minimum = float(os.environ.get(KEEPITUP_MIN_DELAY_SEC_ENV, "0.15"))
    maximum = float(os.environ.get(KEEPITUP_MAX_DELAY_SEC_ENV, "0.35"))
    return minimum, max(minimum, maximum)


def fetch_html(http_session: requests.Session, url: str) -> str:
    minimum, maximum = _delay_range()
    last_error: Exception | None = None
    for attempt in range(3):
        if maximum > 0:
            time.sleep(random.uniform(minimum, maximum))
        try:
            response = http_session.get(url, timeout=30)
            if response.status_code in {429, 503}:
                retry_after = response.headers.get("Retry-After")
                time.sleep(int(retry_after) if retry_after and retry_after.isdigit() else 5 + attempt * 5)
                continue
            response.raise_for_status()
            return response.content.decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < 2:
                time.sleep(2 + attempt * 3)
    raise RuntimeError(f"KeepItUp取得に失敗しました: {url} ({last_error})")


def enumerate_questions(
    *,
    http_session: requests.Session,
    course_url: str,
) -> tuple[list[KeepItUpIndexQuestion], int, tuple[str, ...]]:
    course = discover_course(fetch_html(http_session, course_url), page_url=course_url)
    declared_count = parse_declared_question_count(
        fetch_html(http_session, course.random_question_url),
        page_url=course.random_question_url,
    )
    by_id: dict[str, KeepItUpIndexQuestion] = {}
    for question_set_id in course.question_set_ids:
        start_url = urljoin(course_url, f"/{question_set_id}000L/")
        queue: deque[str] = deque([start_url])
        visited: set[str] = set()
        while queue:
            page_url = queue.popleft()
            if page_url in visited:
                continue
            visited.add(page_url)
            page = parse_list_page(
                fetch_html(http_session, page_url),
                page_url=page_url,
                question_set_id=question_set_id,
            )
            for item in page.questions:
                existing = by_id.get(item.question_id)
                if existing is not None and existing != item:
                    raise ValueError(f"一覧で同じ問題IDの内容が競合しています: {item.question_id}")
                by_id[item.question_id] = item
            for pagination_url in page.pagination_urls:
                if pagination_url not in visited:
                    queue.append(pagination_url)

    if len(by_id) != declared_count:
        raise ValueError(
            f"一覧件数がランダム出題の表示件数と一致しません: indexed={len(by_id)} declared={declared_count}"
        )
    return sorted(by_id.values(), key=lambda item: item.question_id), declared_count, course.question_set_ids


def _image_filename(question_id: str, image_url: str) -> str:
    digest = hashlib.sha1(image_url.encode("utf-8")).hexdigest()[:16]
    return f"keepitup_{question_id.lower()}_{digest}{guess_image_extension(image_url)}"


def ensure_image_saved(
    *,
    http_session: requests.Session,
    image_output_dir: Path,
    qualification_code: str,
    question_id: str,
    image_url: str,
) -> tuple[str, str]:
    filename = _image_filename(question_id, image_url)
    path = image_output_dir / filename
    if not path.is_file():
        response = http_session.get(image_url, timeout=30)
        response.raise_for_status()
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if "image" not in content_type or not response.content:
            raise ValueError(f"画像として取得できません: {image_url}")
        try:
            with path.open("xb") as output:
                output.write(response.content)
        except FileExistsError:
            pass
    if path.stat().st_size <= 0:
        raise ValueError(f"画像ファイルが空です: {path}")
    return filename, make_storage_url(filename, qualification_code)


def _storage_urls_for(
    source_urls: Iterable[str],
    *,
    http_session: requests.Session,
    image_output_dir: Path,
    qualification_code: str,
    question_id: str,
) -> list[str]:
    return [
        ensure_image_saved(
            http_session=http_session,
            image_output_dir=image_output_dir,
            qualification_code=qualification_code,
            question_id=question_id,
            image_url=url,
        )[1]
        for url in source_urls
    ]


def build_source_record(
    parsed: KeepItUpParsedQuestion,
    index_question: KeepItUpIndexQuestion,
    *,
    qualification_code: str,
    qualification_name: str,
    output_list_group_id: str,
    source_list_group_id: str,
    http_session: requests.Session,
    image_output_dir: Path,
) -> dict[str, Any]:
    if parsed.question_id != index_question.question_id:
        raise ValueError("一覧と解答ページの問題IDが一致しません")
    if index_question.title and parsed.title != index_question.title:
        raise ValueError(
            f"一覧と解答ページの問題タイトルが一致しません: list={index_question.title} detail={parsed.title}"
        )
    source_question_id = make_url_source_question_id(
        qualification_code,
        index_question.question_url,
    )
    public_question_id = make_public_question_id(source_question_id)
    question_intent = determine_question_intent(parsed.question_text)
    question_image_storage_urls = _storage_urls_for(
        parsed.question_image_urls,
        http_session=http_session,
        image_output_dir=image_output_dir,
        qualification_code=qualification_code,
        question_id=parsed.question_id,
    )
    choice_image_storage_urls = [
        _storage_urls_for(
            urls,
            http_session=http_session,
            image_output_dir=image_output_dir,
            qualification_code=qualification_code,
            question_id=parsed.question_id,
        )
        for urls in parsed.choice_image_urls
    ]
    explanation_image_storage_urls = _storage_urls_for(
        parsed.explanation_image_urls,
        http_session=http_session,
        image_output_dir=image_output_dir,
        qualification_code=qualification_code,
        question_id=parsed.question_id,
    )
    record: dict[str, Any] = {
        "questionBodyText": parsed.question_text,
        "examLabel": f"{qualification_name} / aws.keepitup.jp",
        "questionLabel": f"問題ID {parsed.question_id}",
        "questionType": "true_false",
        "choiceTextList": list(parsed.choices),
        "originalQuestionChoiceImageUrls": choice_image_storage_urls,
        "choiceImageSourceUrlsByChoice": [list(urls) for urls in parsed.choice_image_urls],
        "category": index_question.category,
        "list_group_id": output_list_group_id,
        "source_list_group_id": source_list_group_id,
        "question_url": index_question.question_url,
        "public_question_id": public_question_id,
        "original_question_id": public_question_id,
        "source_question_id": source_question_id,
        "source_public_question_id": public_question_id,
        "questionSourceSite": source_site_from_url(index_question.question_url),
        "question_id_policy_key": "source-question-id:hmac:v1",
        "question_id_policy_version": 1,
        "question_id_source_key_description": "{qualification_code}:aws-keepitup-jp:{question_id}Q",
        "sourceUniqueKeys": [
            f"{source_question_id}:s{index:02d}"
            for index in range(1, len(parsed.choices) + 1)
        ],
        "sourceQuestionInputType": parsed.selection_type,
        "questionImageSourceUrls": list(parsed.question_image_urls),
        "questionImageStorageUrls": question_image_storage_urls,
        "questionIntent": question_intent,
        "correctChoiceText": choice_truth_labels(
            choice_count=len(parsed.choices),
            correct_choice_numbers=parsed.correct_choice_numbers,
            question_intent=question_intent,
        ),
        "explanation_common_prefix": [parsed.explanation_text],
        "explanation_common_summary": [],
        "explanation_choice_snippets": [[] for _ in parsed.choices],
        "answer_result_text": build_answer_result_text(parsed.correct_choice_numbers),
        "answer_result_inferred_correct_choice_numbers": list(parsed.correct_choice_numbers),
        "explanationImageSourceUrls": list(parsed.explanation_image_urls),
        "explanationImageStorageUrls": explanation_image_storage_urls,
        "referenceUrls": list(parsed.reference_urls),
    }
    if len(parsed.correct_choice_numbers) == 1:
        record["explanation_common_prefix_inferred_correct_choice"] = parsed.correct_choice_numbers[0]
    return record


def source_filename(question_id: str) -> str:
    return f"question_keepitup-{question_id}.json"


def _question_id_from_record(record: dict[str, Any]) -> str:
    match = QUESTION_URL_RE.search(urlparse(str(record.get("question_url") or "")).path)
    return match.group("question_id") if match else ""


def load_existing_records(source_dir: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    records: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in sorted(source_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        bodies = data.get("question_bodies") if isinstance(data, dict) else None
        if not isinstance(bodies, list):
            continue
        for record in bodies:
            if not isinstance(record, dict):
                continue
            question_id = _question_id_from_record(record)
            if not question_id:
                continue
            if question_id in records:
                raise ValueError(f"既存00_sourceで問題IDが重複しています: {question_id}")
            records[question_id] = (path, record)
    return records


def save_source_record(
    *,
    source_dir: Path,
    output_list_group_id: str,
    source_list_group_id: str,
    record: dict[str, Any],
    replace_existing: bool = False,
) -> Path:
    question_id = _question_id_from_record(record)
    if not question_id:
        raise ValueError("保存対象recordから問題IDを取得できません")
    path = source_dir / source_filename(question_id)
    payload = {
        "list_group_id": output_list_group_id,
        "source_list_group_id": source_list_group_id,
        "question_bodies": [record],
    }
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if not replace_existing:
        with path.open("x", encoding="utf-8") as output:
            output.write(serialized)
        return path

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            output.write(serialized)
            output.flush()
            os.fsync(output.fileno())
            temporary_path = Path(output.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return path


def synchronize_staged_images(
    *,
    staged_image_dir: Path,
    image_output_dir: Path,
) -> tuple[int, int, int, list[str]]:
    """liveから再取得した画像を成功後だけ現在snapshotへ反映する。"""
    newly_saved = 0
    updated = 0
    verified = 0
    updated_question_ids: set[str] = set()
    for staged_path in sorted(staged_image_dir.glob("*")):
        if not staged_path.is_file():
            continue
        target_path = image_output_dir / staged_path.name
        staged_hash = hashlib.sha256(staged_path.read_bytes()).hexdigest()
        if not target_path.is_file():
            os.replace(staged_path, target_path)
            newly_saved += 1
            continue
        target_hash = hashlib.sha256(target_path.read_bytes()).hexdigest()
        if target_hash == staged_hash:
            verified += 1
            continue
        os.replace(staged_path, target_path)
        updated += 1
        filename_parts = staged_path.name.split("_", 2)
        if len(filename_parts) == 3:
            updated_question_ids.add(filename_parts[1].upper())
    return newly_saved, updated, verified, sorted(updated_question_ids)


def validate_record(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in (
        "questionBodyText",
        "choiceTextList",
        "answer_result_text",
        "answer_result_inferred_correct_choice_numbers",
        "explanation_common_prefix",
        "source_question_id",
        "question_url",
    ):
        if record.get(key) in (None, "", []):
            errors.append(f"{key} is empty")
    choices = record.get("choiceTextList")
    choice_images = record.get("originalQuestionChoiceImageUrls")
    if not isinstance(choices, list) or len(choices) < 2:
        errors.append("choiceTextList must contain at least 2 choices")
    elif isinstance(choice_images, list):
        for index, choice in enumerate(choices):
            images = choice_images[index] if index < len(choice_images) else []
            if not str(choice or "").strip() and not images:
                errors.append(f"choice {index + 1} has no text or image")
    correct_numbers = record.get("answer_result_inferred_correct_choice_numbers")
    if isinstance(correct_numbers, list) and isinstance(choices, list):
        for number in correct_numbers:
            if not isinstance(number, int) or not 1 <= number <= len(choices):
                errors.append(f"correct choice number out of range: {number}")
    if "examYear" in record:
        errors.append("independent source must not contain examYear")
    return errors


def _ids_digest(question_ids: Iterable[str]) -> str:
    text = "\n".join(sorted(str(value) for value in question_ids)) + "\n"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_result_report(
    *,
    report_path: Path,
    status: str,
    source_list_group_id: str,
    site_declared_count: int,
    question_set_ids: tuple[str, ...],
    expected_ids: set[str],
    persisted_records: dict[str, tuple[Path, dict[str, Any]]],
    newly_saved: int,
    updated_existing: int,
    updated_source_record_count: int,
    updated_question_ids: Iterable[str],
    updated_image_question_ids: Iterable[str],
    verified_existing: int,
    newly_saved_images: int,
    updated_images: int,
    verified_images: int,
    errors: list[dict[str, str]],
    image_output_dir: Path,
) -> dict[str, Any]:
    persisted_ids = set(persisted_records)
    categories = Counter(
        str(record.get("category") or "")
        for _, record in persisted_records.values()
    )
    question_sets = Counter(question_id[:-3] for question_id in persisted_ids)
    source_hashes = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted({path for path, _ in persisted_records.values()})
    }
    image_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(image_output_dir.glob("*"))
        if path.is_file()
    }
    report = {
        "status": status,
        "completedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sourceSite": "aws-keepitup-jp",
        "sourceListGroupId": source_list_group_id,
        "siteDeclaredCount": site_declared_count,
        "questionSetIds": list(question_set_ids),
        "expectedCount": len(expected_ids),
        "persistedCount": len(persisted_ids),
        "newlySavedCount": newly_saved,
        "updatedExistingCount": updated_existing,
        "updatedSourceRecordCount": updated_source_record_count,
        "updatedQuestionIds": sorted(str(value) for value in updated_question_ids),
        "updatedImageQuestionIds": sorted(
            str(value) for value in updated_image_question_ids
        ),
        "verifiedExistingCount": verified_existing,
        "newlySavedImageCount": newly_saved_images,
        "updatedImageCount": updated_images,
        "verifiedImageCount": verified_images,
        "missingIds": sorted(expected_ids - persisted_ids),
        "unexpectedIds": sorted(persisted_ids - expected_ids),
        "duplicateSourceQuestionIdCount": len(persisted_records) - len(
            {str(record.get("source_question_id") or "") for _, record in persisted_records.values()}
        ),
        "duplicateQuestionUrlCount": len(persisted_records) - len(
            {str(record.get("question_url") or "") for _, record in persisted_records.values()}
        ),
        "expectedIdsSha256": _ids_digest(expected_ids),
        "persistedIdsSha256": _ids_digest(persisted_ids),
        "questionSetCounts": dict(sorted(question_sets.items())),
        "categoryCounts": dict(sorted(categories.items())),
        "sourceFileSha256": source_hashes,
        "imageFileSha256": image_hashes,
        "errors": errors,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="aws.keepitup.jpの問題一覧と解答・解説を取得し、1問1ファイルの00_sourceへ保存する。"
    )
    parser.add_argument("--qualification-code", default=os.environ.get("SCRAPER_QUALIFICATION_CODE", ""))
    parser.add_argument("--qualification-name", default=os.environ.get("SCRAPER_QUALIFICATION_NAME", ""))
    parser.add_argument("--list-url", default=os.environ.get("SCRAPER_LIST_FIRST_PAGE_URL", ""))
    parser.add_argument("--output-list-group-id", default=os.environ.get("SCRAPER_OUTPUT_LIST_GROUP_ID", ""))
    parser.add_argument("--output-dir", default=os.environ.get("SCRAPER_OUTPUT_DIR", str(Path.cwd() / "output")))
    parser.add_argument(
        "--max-questions",
        type=int,
        default=int(os.environ["SCRAPER_MAX_QUESTIONS"]) if os.environ.get("SCRAPER_MAX_QUESTIONS") else None,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_local_secure_env()
    args = parse_args(argv)
    for name, value in (
        ("qualification-code", args.qualification_code),
        ("qualification-name", args.qualification_name),
        ("list-url", args.list_url),
        ("output-list-group-id", args.output_list_group_id),
    ):
        if not str(value or "").strip():
            raise ValueError(f"--{name}は必須です")

    source_list_group_id = Path(urlparse(args.list_url).path).name
    if not source_list_group_id:
        raise ValueError(f"course URLからsource_list_group_idを取得できません: {args.list_url}")
    http_session = create_http_session()
    index_questions, site_declared_count, question_set_ids = enumerate_questions(
        http_session=http_session,
        course_url=args.list_url,
    )
    selected_questions = index_questions[: args.max_questions] if args.max_questions else index_questions
    expected_ids = {item.question_id for item in selected_questions}
    print(
        f"[INDEX] course={source_list_group_id} site_count={site_declared_count} target_count={len(expected_ids)}"
    )

    json_output_dir, image_output_dir_raw = prepare_output_dirs(
        args.output_dir,
        args.qualification_code,
        args.output_list_group_id,
        "00_source",
    )
    source_dir = Path(json_output_dir)
    image_output_dir = Path(image_output_dir_raw)
    existing_records = load_existing_records(source_dir)
    errors: list[dict[str, str]] = []
    newly_saved = 0
    updated_existing = 0
    updated_question_ids: list[str] = []
    updated_source_record_ids: list[str] = []
    updated_image_question_ids: list[str] = []
    verified_existing = 0
    newly_saved_images = 0
    updated_images = 0
    verified_images = 0
    candidate_records: dict[str, tuple[Path, dict[str, Any]]] = {}
    candidate_actions: dict[str, str] = {}

    with tempfile.TemporaryDirectory(
        prefix=".keepitup-refresh-",
        dir=image_output_dir.parent,
    ) as temporary_image_dir:
        staged_image_dir = Path(temporary_image_dir)
        for position, item in enumerate(selected_questions, start=1):
            try:
                parsed = parse_answer_page(
                    fetch_html(http_session, item.answer_url),
                    page_url=item.answer_url,
                    expected_question_id=item.question_id,
                )
                record = build_source_record(
                    parsed,
                    item,
                    qualification_code=args.qualification_code,
                    qualification_name=args.qualification_name,
                    output_list_group_id=args.output_list_group_id,
                    source_list_group_id=source_list_group_id,
                    http_session=http_session,
                    image_output_dir=staged_image_dir,
                )
                validation_errors = validate_record(record)
                if validation_errors:
                    raise ValueError(" / ".join(validation_errors))
                existing = existing_records.get(item.question_id)
                expected_path = source_dir / source_filename(item.question_id)
                if existing is None:
                    action = "new"
                else:
                    if existing[0] != expected_path:
                        raise ValueError(
                            "安定問題IDに対応する00_sourceファイル名が一致しません"
                        )
                    action = "unchanged" if existing[1] == record else "updated"
                candidate_records[item.question_id] = (expected_path, record)
                candidate_actions[item.question_id] = action
                print(
                    f"[STAGE-{action.upper()}] "
                    f"({position}/{len(selected_questions)}) id={item.question_id}"
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    {
                        "questionId": item.question_id,
                        "url": item.answer_url,
                        "error": str(exc),
                    }
                )
                print(f"[ERROR] ({position}/{len(selected_questions)}) id={item.question_id}: {exc}")

        unexpected_existing_ids = set(existing_records) - expected_ids
        if args.max_questions is None and unexpected_existing_ids:
            for question_id in sorted(unexpected_existing_ids):
                errors.append(
                    {
                        "questionId": question_id,
                        "url": str(existing_records[question_id][1].get("question_url") or ""),
                        "error": "site一覧に存在しない既存00_sourceです",
                    }
                )

        if set(candidate_records) != expected_ids:
            for question_id in sorted(expected_ids - set(candidate_records)):
                if not any(error["questionId"] == question_id for error in errors):
                    errors.append(
                        {
                            "questionId": question_id,
                            "url": "",
                            "error": "live取得結果がありません",
                        }
                    )

        if not errors:
            try:
                for question_id in sorted(candidate_records):
                    _path, record = candidate_records[question_id]
                    action = candidate_actions[question_id]
                    if action == "unchanged":
                        verified_existing += 1
                        continue
                    save_source_record(
                        source_dir=source_dir,
                        output_list_group_id=args.output_list_group_id,
                        source_list_group_id=source_list_group_id,
                        record=record,
                        replace_existing=action == "updated",
                    )
                    if action == "updated":
                        updated_source_record_ids.append(question_id)
                    else:
                        newly_saved += 1
                (
                    newly_saved_images,
                    updated_images,
                    verified_images,
                    updated_image_question_ids,
                ) = synchronize_staged_images(
                    staged_image_dir=staged_image_dir,
                    image_output_dir=image_output_dir,
                )
                updated_question_ids = sorted(
                    set(updated_source_record_ids)
                    | (set(updated_image_question_ids) & set(existing_records))
                )
                updated_existing = len(updated_question_ids)
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    {
                        "questionId": "",
                        "url": str(source_dir),
                        "error": f"source snapshotの反映に失敗しました: {exc}",
                    }
                )

    after_records = load_existing_records(source_dir)

    relevant_records = {
        question_id: value
        for question_id, value in after_records.items()
        if question_id in expected_ids
    }
    for question_id, (_, record) in relevant_records.items():
        for message in validate_record(record):
            errors.append(
                {
                    "questionId": question_id,
                    "url": str(record.get("question_url") or ""),
                    "error": message,
                }
            )

    persisted_ids = set(relevant_records)
    complete = (
        not errors
        and persisted_ids == expected_ids
        and len({record[1].get("source_question_id") for record in relevant_records.values()}) == len(relevant_records)
        and len({record[1].get("question_url") for record in relevant_records.values()}) == len(relevant_records)
    )
    report_path = (
        Path(args.output_dir)
        / args.qualification_code
        / "reports"
        / f"keepitup_{source_list_group_id}_scrape_result.json"
    )
    report = write_result_report(
        report_path=report_path,
        status="complete" if complete else "incomplete",
        source_list_group_id=source_list_group_id,
        site_declared_count=site_declared_count,
        question_set_ids=question_set_ids,
        expected_ids=expected_ids,
        persisted_records=relevant_records,
        newly_saved=newly_saved,
        updated_existing=updated_existing,
        updated_source_record_count=len(updated_source_record_ids),
        updated_question_ids=updated_question_ids,
        updated_image_question_ids=updated_image_question_ids,
        verified_existing=verified_existing,
        newly_saved_images=newly_saved_images,
        updated_images=updated_images,
        verified_images=verified_images,
        errors=errors,
        image_output_dir=image_output_dir,
    )
    if not complete:
        print(
            f"[INCOMPLETE] persisted={report['persistedCount']} expected={report['expectedCount']} "
            f"errors={len(errors)} report={report_path}"
        )
        return 1
    print(
        f"[DONE] persisted={report['persistedCount']} new={newly_saved} "
        f"updated={updated_existing} verified={verified_existing} "
        f"images={len(report['imageFileSha256'])} report={report_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
