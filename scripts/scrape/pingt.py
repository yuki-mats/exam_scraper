from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urljoin, urlparse

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
)


PINGT_COOKIES_JSON_ENV = "PINGT_COOKIES_JSON"
PINGT_COOKIE_HEADER_ENV = "PINGT_COOKIE_HEADER"
PINGT_BROWSER_EXPORT_PATH_ENV = "PINGT_BROWSER_EXPORT_PATH"
PINGT_MIN_DELAY_SEC_ENV = "PINGT_MIN_DELAY_SEC"
PINGT_MAX_DELAY_SEC_ENV = "PINGT_MAX_DELAY_SEC"
PINGT_BASE_URL = "https://mondai.ping-t.com"
LOGIN_MARKERS = (
    "アカウント登録もしくはログインしてください",
    'name="user[email]"',
    'action="/login"',
)


@dataclass(frozen=True)
class PingTIndexQuestion:
    question_id: str
    url: str
    category: str
    question_text: str


@dataclass(frozen=True)
class PingTIndexPage:
    questions: tuple[PingTIndexQuestion, ...]
    expected_count: int | None
    page_count: int


@dataclass(frozen=True)
class PingTParsedQuestion:
    question_id: str
    category: str
    question_text: str
    choices: tuple[str, ...]
    correct_choice_numbers: tuple[int, ...]
    selection_type: str
    explanation_text: str
    question_image_urls: tuple[str, ...]
    choice_image_urls: tuple[tuple[str, ...], ...]
    explanation_image_urls: tuple[str, ...]
    reference_urls: tuple[dict[str, str], ...]


def subject_id_from_url(url: str) -> str:
    match = re.search(r"/question_subjects/(?P<subject_id>[0-9]+)(?:/|$)", url)
    if not match:
        raise ValueError(f"Ping-t subject IDをURLから取得できません: {url}")
    return match.group("subject_id")


def question_url(subject_id: str, question_id: str) -> str:
    return f"{PINGT_BASE_URL}/question_subjects/{subject_id}/questions/{question_id}"


def _assert_authenticated_html(html: str, *, url: str) -> None:
    if any(marker in html for marker in LOGIN_MARKERS):
        raise RuntimeError(
            "Ping-tの認証が必要です。secure.envのPINGT_COOKIES_JSON又は"
            f"PINGT_COOKIE_HEADERを確認してください: {url}"
        )


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


def _image_urls(element: Tag | None, base_url: str) -> tuple[str, ...]:
    if element is None:
        return ()
    urls: list[str] = []
    for image in element.select("img"):
        raw = str(image.get("data-src") or image.get("src") or "").strip()
        if raw:
            urls.append(urljoin(base_url, raw))
    return _dedupe(urls)


def _find_strong(soup: BeautifulSoup | Tag, label: str) -> Tag | None:
    for strong in soup.select("strong"):
        if normalize_inline_text(strong.get_text(" ", strip=True)) == label:
            return strong
    return None


def parse_index_page(html: str, *, page_url: str, subject_id: str) -> PingTIndexPage:
    _assert_authenticated_html(html, url=page_url)
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("main") or soup
    question_pattern = re.compile(
        rf"^/question_subjects/{re.escape(subject_id)}/questions/(?P<question_id>[0-9]+)$"
    )
    questions: list[PingTIndexQuestion] = []
    seen_ids: set[str] = set()
    for anchor in main.select("a[href]"):
        raw_href = str(anchor.get("href") or "").strip()
        parsed_href = urlparse(urljoin(page_url, raw_href))
        match = question_pattern.match(parsed_href.path)
        if not match:
            continue
        question_id = match.group("question_id")
        if question_id in seen_ids:
            continue
        paragraphs = anchor.select("p")
        header_text = normalize_inline_text(paragraphs[0].get_text(" ", strip=True)) if paragraphs else ""
        category = re.sub(rf"^{re.escape(question_id)}\s*", "", header_text).strip()
        question_text = (
            normalize_question_body_text(paragraphs[1].get_text("\n", strip=True))
            if len(paragraphs) >= 2
            else ""
        )
        seen_ids.add(question_id)
        questions.append(
            PingTIndexQuestion(
                question_id=question_id,
                url=question_url(subject_id, question_id),
                category=category,
                question_text=question_text,
            )
        )

    expected_count: int | None = None
    count_match = re.search(r"([0-9][0-9,]*)\s*件の問題が該当します", main.get_text(" ", strip=True))
    if count_match:
        expected_count = int(count_match.group(1).replace(",", ""))

    page_numbers = {1}
    for anchor in main.select("a[href]"):
        parsed = urlparse(urljoin(page_url, str(anchor.get("href") or "")))
        raw_page = parse_qs(parsed.query).get("page", [""])[0]
        if raw_page.isdigit():
            page_numbers.add(int(raw_page))
    return PingTIndexPage(
        questions=tuple(questions),
        expected_count=expected_count,
        page_count=max(page_numbers),
    )


def parse_question_page(
    html: str,
    *,
    page_url: str,
    subject_id: str,
    expected_question_id: str | None = None,
    expected_question_text: str | None = None,
) -> PingTParsedQuestion:
    _assert_authenticated_html(html, url=page_url)
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("main")
    if main is None:
        raise ValueError(f"main要素がありません: {page_url}")

    id_element = main.select_one(".text-roman-number")
    question_id = normalize_inline_text(id_element.get_text(" ", strip=True)) if id_element else ""
    if not question_id.isdigit():
        raise ValueError(f"問題IDを取得できません: {page_url}")
    if expected_question_id and question_id != str(expected_question_id):
        raise ValueError(
            f"問題IDがURL一覧と一致しません: expected={expected_question_id} actual={question_id}"
        )

    category = ""
    if id_element and id_element.parent:
        sibling_spans = id_element.parent.find_all("span", recursive=False)
        for span in sibling_spans:
            text = normalize_inline_text(span.get_text(" ", strip=True))
            if text and text != question_id:
                category = text
                break

    question_candidates = list(main.select("div.mb-6"))
    question_element: Tag | None = None
    normalized_expected_text = normalize_question_body_text(expected_question_text or "")
    normalized_expected_prefix = re.sub(r"(?:\.\.\.|…)+$", "", normalized_expected_text)
    expected_text_is_truncated = normalized_expected_prefix != normalized_expected_text
    if normalized_expected_text:
        question_element = next(
            (
                element
                for element in question_candidates
                if (
                    normalize_question_body_text(element.get_text("\n", strip=True))
                    == normalized_expected_text
                    or (
                        expected_text_is_truncated
                        and normalize_question_body_text(element.get_text("\n", strip=True)).startswith(
                            normalized_expected_prefix
                        )
                    )
                    or re.fullmatch(
                        rf"{re.escape(normalized_expected_text)}\([0-9]+つ選択\)",
                        normalize_question_body_text(element.get_text("\n", strip=True)),
                    )
                    is not None
                )
            ),
            None,
        )
    if question_element is None:
        question_element = next(
            (
                element
                for element in question_candidates
                if element.select_one("label.form-check-label") is None
                and element.select_one("strong") is None
                and "履歴" not in normalize_inline_text(element.get_text(" ", strip=True))
            ),
            None,
        )
    if question_element is None:
        raise ValueError(f"問題文を取得できません: {page_url}")
    question_text = normalize_question_body_text(question_element.get_text("\n", strip=True))
    if not question_text:
        raise ValueError(f"問題文が空です: {page_url}")

    choice_labels = list(main.select("label.form-check-label"))
    if len(choice_labels) < 2:
        raise ValueError(f"選択肢が2件未満です: {page_url}")
    choices = tuple(
        normalize_question_body_text(label.get_text("\n", strip=True))
        for label in choice_labels
    )
    choice_image_urls = tuple(_image_urls(label, page_url) for label in choice_labels)
    for index, (choice_text, image_urls) in enumerate(zip(choices, choice_image_urls), 1):
        if not choice_text and not image_urls:
            raise ValueError(f"選択肢{index}が空です: {page_url}")

    input_element = main.select_one("input.form-check-input")
    selection_type = str(input_element.get("type") or "").strip().lower() if input_element else ""
    if selection_type not in {"radio", "checkbox"}:
        selection_type = "checkbox" if "選択" in question_text else "radio"

    correct_choice_numbers = tuple(
        index
        for index, label in enumerate(choice_labels, 1)
        if "text-info" in (label.get("class") or [])
        or "correct-image-border" in (label.get("class") or [])
        or label.select_one(".correct-image-border") is not None
    )
    if not correct_choice_numbers:
        answer_heading = _find_strong(main, "正解")
        answer_card = answer_heading.find_parent(class_="card-body") if answer_heading else None
        answer_texts = (
            [
                normalize_question_body_text(item.get_text("\n", strip=True))
                for item in answer_card.select("p.h3.text-info strong")
            ]
            if answer_card
            else []
        )
        mapped: list[int] = []
        for answer_text in answer_texts:
            exact_matches = [index for index, text in enumerate(choices, 1) if text == answer_text]
            if len(exact_matches) == 1:
                mapped.append(exact_matches[0])
        correct_choice_numbers = tuple(sorted(set(mapped)))
    if not correct_choice_numbers:
        raise ValueError(f"正答を取得できません: {page_url}")

    explanation_heading = _find_strong(main, "解説")
    explanation_label = explanation_heading.find_parent("p") if explanation_heading else None
    explanation_element = explanation_label.find_next_sibling("div") if explanation_label else None
    if explanation_element is None:
        raise ValueError(f"解説を取得できません: {page_url}")
    explanation_text = normalize_question_body_text(
        explanation_element.get_text("\n", strip=True)
    )
    if not explanation_text:
        raise ValueError(f"解説が空です: {page_url}")

    reference_urls_by_url: dict[str, dict[str, str]] = {}
    reference_heading = _find_strong(main, "参考URL")
    reference_card = reference_heading.find_parent(class_="card-body") if reference_heading else None
    if reference_card:
        for anchor in reference_card.select("a[href]"):
            href = urljoin(page_url, str(anchor.get("href") or "").strip())
            title = normalize_inline_text(anchor.get_text(" ", strip=True))
            if not href.startswith(("http://", "https://")):
                continue
            existing = reference_urls_by_url.get(href)
            if existing is None or (not existing["title"] and title):
                reference_urls_by_url[href] = {"title": title, "url": href}

    return PingTParsedQuestion(
        question_id=question_id,
        category=category,
        question_text=question_text,
        choices=choices,
        correct_choice_numbers=correct_choice_numbers,
        selection_type=selection_type,
        explanation_text=explanation_text,
        question_image_urls=_image_urls(question_element, page_url),
        choice_image_urls=choice_image_urls,
        explanation_image_urls=_image_urls(explanation_element, page_url),
        reference_urls=tuple(reference_urls_by_url.values()),
    )


def question_text_matches_index(index_text: str, detail_text: str) -> bool:
    normalized_index = normalize_question_body_text(index_text or "")
    normalized_detail = normalize_question_body_text(detail_text or "")
    if normalized_index == normalized_detail:
        return True
    prefix = re.sub(r"(?:\.\.\.|…)+$", "", normalized_index)
    if prefix != normalized_index and normalized_detail.startswith(prefix):
        return True
    return re.fullmatch(
        rf"{re.escape(normalized_index)}\([0-9]+つ選択\)", normalized_detail
    ) is not None


INCORRECT_PATTERNS = (
    r"最も不適切",
    r"最も不適当",
    r"誤っている",
    r"誤り(?:である)?(?:もの|記述|説明|組合せ|組み合わせ|選択肢)?",
    r"間違っている",
    r"正しくない",
    r"不適切(?:な|である)?(?:もの|記述|説明|組合せ|組み合わせ|選択肢)?",
    r"不適当(?:な|である)?(?:もの|記述|説明|組合せ|組み合わせ|選択肢)?",
    r"適切でない",
    r"適当でない",
    r"含まれないもの",
    r"該当しないもの",
    r"対象とならないもの",
)


def determine_question_intent(question_text: str) -> str:
    normalized = re.sub(r"\s+", "", question_text or "")
    if any(re.search(pattern, normalized) for pattern in INCORRECT_PATTERNS):
        return "select_incorrect"
    return "select_correct"


def build_answer_result_text(correct_choice_numbers: Iterable[int]) -> str:
    numbers = [int(number) for number in correct_choice_numbers]
    return f"正解は {', '.join(str(number) for number in numbers)} です。"


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


def load_cookie_configuration(http_session: requests.Session) -> None:
    cookie_header = str(os.environ.get(PINGT_COOKIE_HEADER_ENV) or "").strip()
    if cookie_header:
        http_session.headers["Cookie"] = cookie_header

    cookies_json = str(os.environ.get(PINGT_COOKIES_JSON_ENV) or "").strip()
    if not cookies_json:
        return
    cookie_path = Path(cookies_json).expanduser()
    raw: Any
    if cookie_path.is_file():
        raw = json.loads(cookie_path.read_text(encoding="utf-8"))
    else:
        raw = json.loads(cookies_json)
    if not isinstance(raw, list):
        raise ValueError(f"{PINGT_COOKIES_JSON_ENV}はcookie objectの配列にしてください")
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        domain = str(item.get("domain") or ".ping-t.com").strip()
        path = str(item.get("path") or "/").strip() or "/"
        if name and value:
            http_session.cookies.set(name, value, domain=domain, path=path)


def _delay_range() -> tuple[float, float]:
    minimum = float(os.environ.get(PINGT_MIN_DELAY_SEC_ENV, "1.0"))
    maximum = float(os.environ.get(PINGT_MAX_DELAY_SEC_ENV, "1.5"))
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
            _assert_authenticated_html(response.text, url=url)
            return response.text
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < 2:
                time.sleep(2 + attempt * 3)
    raise RuntimeError(f"Ping-t取得に失敗しました: {url} ({last_error})")


def load_browser_export(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Ping-t browser exportはobjectである必要があります")
    has_html_pages = isinstance(data.get("index_pages"), dict) and isinstance(
        data.get("detail_pages"), dict
    )
    has_structured_records = isinstance(data.get("index_questions"), list) and isinstance(
        data.get("records"), dict
    )
    if not has_html_pages and not has_structured_records:
        raise ValueError(
            "Ping-t browser exportにindex_pages/detail_pages又は"
            "index_questions/recordsがありません"
        )
    return data


def parsed_question_from_export(raw: Any, *, expected_question_id: str) -> PingTParsedQuestion:
    if not isinstance(raw, dict):
        raise ValueError(f"browser exportの問題recordがobjectではありません: {expected_question_id}")
    question_id = str(raw.get("question_id") or "").strip()
    if question_id != expected_question_id:
        raise ValueError(
            f"browser exportの問題IDが一致しません: expected={expected_question_id} actual={question_id}"
        )
    choices = raw.get("choices")
    correct_numbers = raw.get("correct_choice_numbers")
    choice_images = raw.get("choice_image_urls")
    references = raw.get("reference_urls")
    if not isinstance(choices, list) or len(choices) < 2:
        raise ValueError(f"browser exportの選択肢が不正です: {question_id}")
    if not isinstance(correct_numbers, list) or not correct_numbers:
        raise ValueError(f"browser exportの正答がありません: {question_id}")
    if not isinstance(choice_images, list) or len(choice_images) != len(choices):
        raise ValueError(f"browser exportの選択肢画像対応が不正です: {question_id}")
    if references is None:
        references = []
    if not isinstance(references, list):
        raise ValueError(f"browser exportの参考URLが不正です: {question_id}")
    parsed = PingTParsedQuestion(
        question_id=question_id,
        category=str(raw.get("category") or "").strip(),
        question_text=normalize_question_body_text(str(raw.get("question_text") or "")),
        choices=tuple(normalize_question_body_text(str(value or "")) for value in choices),
        correct_choice_numbers=tuple(sorted({int(value) for value in correct_numbers})),
        selection_type=str(raw.get("selection_type") or "").strip().lower(),
        explanation_text=normalize_question_body_text(str(raw.get("explanation_text") or "")),
        question_image_urls=_dedupe(raw.get("question_image_urls") or []),
        choice_image_urls=tuple(_dedupe(urls or []) for urls in choice_images),
        explanation_image_urls=_dedupe(raw.get("explanation_image_urls") or []),
        reference_urls=tuple(
            {
                "title": normalize_inline_text(str(item.get("title") or "")),
                "url": str(item.get("url") or "").strip(),
            }
            for item in references
            if isinstance(item, dict) and str(item.get("url") or "").strip()
        ),
    )
    if not parsed.question_text or not parsed.explanation_text:
        raise ValueError(f"browser exportの問題文又は解説が空です: {question_id}")
    if parsed.selection_type not in {"radio", "checkbox"}:
        raise ValueError(f"browser exportのselection_typeが不正です: {question_id}")
    if any(number < 1 or number > len(parsed.choices) for number in parsed.correct_choice_numbers):
        raise ValueError(f"browser exportの正答番号が範囲外です: {question_id}")
    for index, (text, images) in enumerate(zip(parsed.choices, parsed.choice_image_urls), 1):
        if not text and not images:
            raise ValueError(f"browser exportの選択肢{index}が空です: {question_id}")
    return parsed


def enumerate_index(
    *,
    http_session: requests.Session,
    first_page_url: str,
    subject_id: str,
    browser_export: dict[str, Any] | None,
) -> tuple[list[PingTIndexQuestion], int]:
    if browser_export is not None and isinstance(browser_export.get("index_questions"), list):
        expected_count = int(browser_export.get("expected_count") or 0)
        if expected_count <= 0:
            raise ValueError("browser exportのexpected_countが不正です")
        by_id: dict[str, PingTIndexQuestion] = {}
        for raw in browser_export["index_questions"]:
            if not isinstance(raw, dict):
                raise ValueError("browser exportのindex questionがobjectではありません")
            question_id = str(raw.get("question_id") or "").strip()
            if not question_id.isdigit():
                raise ValueError(f"browser exportのindex question IDが不正です: {question_id}")
            item = PingTIndexQuestion(
                question_id=question_id,
                url=question_url(subject_id, question_id),
                category=str(raw.get("category") or "").strip(),
                question_text=normalize_question_body_text(str(raw.get("question_text") or "")),
            )
            existing = by_id.get(question_id)
            if existing is not None and existing != item:
                raise ValueError(f"browser exportの一覧IDが競合しています: {question_id}")
            by_id[question_id] = item
        if len(by_id) != expected_count:
            raise ValueError(
                f"browser exportの一覧件数が一致しません: indexed={len(by_id)} expected={expected_count}"
            )
        return sorted(by_id.values(), key=lambda item: int(item.question_id)), expected_count

    if browser_export is not None:
        raw_pages = browser_export["index_pages"]
        indexed_pages = sorted(
            ((int(page), str(html)) for page, html in raw_pages.items()),
            key=lambda item: item[0],
        )
        if not indexed_pages:
            raise ValueError("browser exportのindex_pagesが空です")
        pages = [
            parse_index_page(
                html,
                page_url=first_page_url if page == 1 else f"{first_page_url}?page={page}",
                subject_id=subject_id,
            )
            for page, html in indexed_pages
        ]
    else:
        first_html = fetch_html(http_session, first_page_url)
        first_page = parse_index_page(first_html, page_url=first_page_url, subject_id=subject_id)
        pages = [first_page]
        for page_number in range(2, first_page.page_count + 1):
            page_url = f"{first_page_url}?page={page_number}"
            page_html = fetch_html(http_session, page_url)
            pages.append(parse_index_page(page_html, page_url=page_url, subject_id=subject_id))

    expected_values = {page.expected_count for page in pages if page.expected_count is not None}
    if len(expected_values) != 1:
        raise ValueError(f"一覧の期待件数が一意ではありません: {sorted(expected_values)}")
    expected_count = expected_values.pop()
    by_id: dict[str, PingTIndexQuestion] = {}
    for page in pages:
        for question in page.questions:
            existing = by_id.get(question.question_id)
            if existing is not None and existing != question:
                raise ValueError(f"一覧で同じ問題IDの内容が競合しています: {question.question_id}")
            by_id[question.question_id] = question
    if len(by_id) != expected_count:
        raise ValueError(
            f"一覧件数が表示件数と一致しません: indexed={len(by_id)} expected={expected_count}"
        )
    return sorted(by_id.values(), key=lambda item: int(item.question_id)), expected_count


def _image_filename(subject_id: str, image_url: str) -> str:
    path = urlparse(image_url).path
    source_id_match = re.search(r"/question_images/(?P<source_id>[0-9]+)(?:\.[^/]+)?$", path)
    token = source_id_match.group("source_id") if source_id_match else hashlib.sha1(image_url.encode("utf-8")).hexdigest()[:16]
    return f"pingt_s{subject_id}_img_{token}{guess_image_extension(image_url)}"


def ensure_image_saved(
    *,
    http_session: requests.Session,
    image_output_dir: Path,
    qualification_code: str,
    subject_id: str,
    image_url: str,
) -> tuple[str, str]:
    filename = _image_filename(subject_id, image_url)
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
    subject_id: str,
) -> list[str]:
    return [
        ensure_image_saved(
            http_session=http_session,
            image_output_dir=image_output_dir,
            qualification_code=qualification_code,
            subject_id=subject_id,
            image_url=url,
        )[1]
        for url in source_urls
    ]


def build_source_record(
    parsed: PingTParsedQuestion,
    *,
    qualification_code: str,
    qualification_name: str,
    output_list_group_id: str,
    source_list_group_id: str,
    http_session: requests.Session,
    image_output_dir: Path,
) -> dict[str, Any]:
    stable_url = question_url(source_list_group_id, parsed.question_id)
    source_question_id = make_url_source_question_id(qualification_code, stable_url)
    public_question_id = make_public_question_id(source_question_id)
    question_intent = determine_question_intent(parsed.question_text)
    question_image_storage_urls = _storage_urls_for(
        parsed.question_image_urls,
        http_session=http_session,
        image_output_dir=image_output_dir,
        qualification_code=qualification_code,
        subject_id=source_list_group_id,
    )
    choice_image_storage_urls = [
        _storage_urls_for(
            urls,
            http_session=http_session,
            image_output_dir=image_output_dir,
            qualification_code=qualification_code,
            subject_id=source_list_group_id,
        )
        for urls in parsed.choice_image_urls
    ]
    explanation_image_storage_urls = _storage_urls_for(
        parsed.explanation_image_urls,
        http_session=http_session,
        image_output_dir=image_output_dir,
        qualification_code=qualification_code,
        subject_id=source_list_group_id,
    )
    record: dict[str, Any] = {
        "questionBodyText": parsed.question_text,
        "examLabel": f"{qualification_name} / Ping-t",
        "questionLabel": f"問題ID {parsed.question_id}",
        "questionType": "true_false",
        "choiceTextList": list(parsed.choices),
        "originalQuestionChoiceImageUrls": choice_image_storage_urls,
        "choiceImageSourceUrlsByChoice": [list(urls) for urls in parsed.choice_image_urls],
        "category": parsed.category,
        "list_group_id": output_list_group_id,
        "source_list_group_id": source_list_group_id,
        "question_url": stable_url,
        "public_question_id": public_question_id,
        "original_question_id": public_question_id,
        "source_question_id": source_question_id,
        "source_public_question_id": public_question_id,
        "questionSourceSite": "ping-t",
        "question_id_policy_key": "source-question-id:hmac:v1",
        "question_id_policy_version": 1,
        "question_id_source_key_description": "{qualification_code}:ping-t:question_subjects:{subject_id}:questions:{question_id}",
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


def source_filename(subject_id: str, question_id: str) -> str:
    return f"question_ping-t-{subject_id}_{question_id}.json"


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
            question_id_match = re.search(r"/questions/([0-9]+)$", str(record.get("question_url") or ""))
            if not question_id_match:
                continue
            question_id = question_id_match.group(1)
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
) -> Path:
    question_id = str(record["question_url"]).rstrip("/").rsplit("/", 1)[-1]
    path = source_dir / source_filename(source_list_group_id, question_id)
    payload = {
        "list_group_id": output_list_group_id,
        "source_list_group_id": source_list_group_id,
        "question_bodies": [record],
    }
    with path.open("x", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2)
        output.write("\n")
    return path


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
        value = record.get(key)
        if value in (None, "", []):
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
    return errors


def _ids_digest(question_ids: Iterable[str]) -> str:
    text = "\n".join(sorted((str(value) for value in question_ids), key=int)) + "\n"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_result_report(
    *,
    report_path: Path,
    status: str,
    subject_id: str,
    expected_ids: set[str],
    persisted_records: dict[str, tuple[Path, dict[str, Any]]],
    newly_saved: int,
    errors: list[dict[str, str]],
    image_output_dir: Path,
) -> dict[str, Any]:
    persisted_ids = set(persisted_records)
    categories = Counter(
        str(record.get("category") or "")
        for _, record in persisted_records.values()
    )
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
        "sourceSite": "ping-t",
        "subjectId": subject_id,
        "expectedCount": len(expected_ids),
        "persistedCount": len(persisted_ids),
        "newlySavedCount": newly_saved,
        "skippedExistingCount": len(expected_ids & persisted_ids) - newly_saved,
        "missingIds": sorted(expected_ids - persisted_ids, key=int),
        "unexpectedIds": sorted(persisted_ids - expected_ids, key=int),
        "duplicateSourceQuestionIdCount": len(persisted_records) - len(
            {str(record.get("source_question_id") or "") for _, record in persisted_records.values()}
        ),
        "duplicateQuestionUrlCount": len(persisted_records) - len(
            {str(record.get("question_url") or "") for _, record in persisted_records.values()}
        ),
        "expectedIdsSha256": _ids_digest(expected_ids),
        "persistedIdsSha256": _ids_digest(persisted_ids),
        "categoryCounts": dict(sorted(categories.items())),
        "sourceFileSha256": source_hashes,
        "imageFileSha256": image_hashes,
        "errors": errors,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ping-tのsubject一覧と問題詳細を取得し、1問1ファイルの00_sourceへ保存する。"
    )
    parser.add_argument("--qualification-code", default=os.environ.get("SCRAPER_QUALIFICATION_CODE", ""))
    parser.add_argument("--qualification-name", default=os.environ.get("SCRAPER_QUALIFICATION_NAME", ""))
    parser.add_argument("--list-url", default=os.environ.get("SCRAPER_LIST_FIRST_PAGE_URL", ""))
    parser.add_argument("--output-list-group-id", default=os.environ.get("SCRAPER_OUTPUT_LIST_GROUP_ID", ""))
    parser.add_argument("--output-dir", default=os.environ.get("SCRAPER_OUTPUT_DIR", str(Path.cwd() / "output")))
    parser.add_argument("--max-questions", type=int, default=int(os.environ["SCRAPER_MAX_QUESTIONS"]) if os.environ.get("SCRAPER_MAX_QUESTIONS") else None)
    parser.add_argument("--browser-export", default=os.environ.get(PINGT_BROWSER_EXPORT_PATH_ENV, ""))
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

    subject_id = subject_id_from_url(args.list_url)
    http_session = create_http_session()
    load_cookie_configuration(http_session)
    browser_export = load_browser_export(Path(args.browser_export).expanduser()) if args.browser_export else None
    if browser_export is None and not (
        os.environ.get(PINGT_COOKIE_HEADER_ENV) or os.environ.get(PINGT_COOKIES_JSON_ENV)
    ):
        raise RuntimeError(
            f"{PINGT_COOKIES_JSON_ENV}、{PINGT_COOKIE_HEADER_ENV}、又は"
            f"{PINGT_BROWSER_EXPORT_PATH_ENV}のいずれかが必要です"
        )

    index_questions, expected_count = enumerate_index(
        http_session=http_session,
        first_page_url=args.list_url,
        subject_id=subject_id,
        browser_export=browser_export,
    )
    selected_questions = index_questions[: args.max_questions] if args.max_questions else index_questions
    expected_ids = {item.question_id for item in selected_questions}
    print(
        f"[INDEX] subject={subject_id} site_count={expected_count} target_count={len(expected_ids)}"
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
    preexisting_hashes = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in {path for path, _ in existing_records.values()}
    }
    errors: list[dict[str, str]] = []
    newly_saved = 0
    detail_pages = browser_export.get("detail_pages", {}) if browser_export else {}
    structured_records = browser_export.get("records", {}) if browser_export else {}

    for position, item in enumerate(selected_questions, 1):
        if item.question_id in existing_records:
            print(f"[SKIP] ({position}/{len(selected_questions)}) id={item.question_id}")
            continue
        try:
            if browser_export is not None and item.question_id in structured_records:
                parsed = parsed_question_from_export(
                    structured_records[item.question_id],
                    expected_question_id=item.question_id,
                )
            else:
                html = (
                    str(detail_pages[item.question_id])
                    if browser_export is not None and item.question_id in detail_pages
                    else fetch_html(http_session, item.url)
                )
                parsed = parse_question_page(
                    html,
                    page_url=item.url,
                    subject_id=subject_id,
                    expected_question_id=item.question_id,
                    expected_question_text=item.question_text,
                )
            if item.category and parsed.category and item.category != parsed.category:
                raise ValueError(
                    f"一覧と詳細のcategoryが一致しません: list={item.category} detail={parsed.category}"
                )
            if item.question_text and not question_text_matches_index(
                item.question_text, parsed.question_text
            ):
                raise ValueError("一覧と詳細の問題文が一致しません")
            record = build_source_record(
                parsed,
                qualification_code=args.qualification_code,
                qualification_name=args.qualification_name,
                output_list_group_id=args.output_list_group_id,
                source_list_group_id=subject_id,
                http_session=http_session,
                image_output_dir=image_output_dir,
            )
            validation_errors = validate_record(record)
            if validation_errors:
                raise ValueError(" / ".join(validation_errors))
            path = save_source_record(
                source_dir=source_dir,
                output_list_group_id=args.output_list_group_id,
                source_list_group_id=subject_id,
                record=record,
            )
            existing_records[item.question_id] = (path, record)
            newly_saved += 1
            print(f"[SAVE] ({position}/{len(selected_questions)}) id={item.question_id}")
        except Exception as exc:  # noqa: BLE001
            errors.append({"questionId": item.question_id, "url": item.url, "error": str(exc)})
            print(f"[ERROR] ({position}/{len(selected_questions)}) id={item.question_id}: {exc}")

    after_records = load_existing_records(source_dir)
    for path, before_hash in preexisting_hashes.items():
        after_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if after_hash != before_hash:
            errors.append({"questionId": "", "url": str(path), "error": "既存00_sourceのhashが変化しました"})

    relevant_records = after_records
    for question_id in sorted(expected_ids & set(relevant_records), key=int):
        validation_errors = validate_record(relevant_records[question_id][1])
        for message in validation_errors:
            errors.append({"questionId": question_id, "url": relevant_records[question_id][1].get("question_url", ""), "error": message})

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
        / f"pingt_subject_{subject_id}_scrape_result.json"
    )
    report = write_result_report(
        report_path=report_path,
        status="complete" if complete else "incomplete",
        subject_id=subject_id,
        expected_ids=expected_ids,
        persisted_records=relevant_records,
        newly_saved=newly_saved,
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
        f"images={len(report['imageFileSha256'])} report={report_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
