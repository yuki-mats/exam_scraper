#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from bs4.element import Tag


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.scrape.common import normalize_inline_text  # noqa: E402


QUALIFICATIONS = ("gas-shunin-kou", "gas-shunin-otsu")
QUESTION_URL_RE = re.compile(r"#(?P<subject>[a-z]+)-q(?P<question_no>\d+)$")
CHOICE_MARKER_RE = re.compile(r"^[（(]?(?P<number>\d+)[）)]?$")
ANSWER_NUMBER_RE = re.compile(r"(?:正解|正答)[^0-9０-９]*[（(]?(?P<number>[0-9０-９]+)[）)]?")
FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR))
    except ValueError:
        return str(path.resolve())


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def fetch_html(url: str) -> str:
    page_url, _ = urldefrag(url)
    request = Request(
        page_url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; Codex gas-shunin source repair)"},
    )
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def iter_source_paths(qualifications: list[str]) -> list[Path]:
    paths: list[Path] = []
    for qualification in qualifications:
        root = ROOT_DIR / "output" / qualification / "questions_json"
        paths.extend(
            sorted(
                path
                for path in root.glob("*/00_source/question_*.json")
                if "99_archived" not in path.parts
            )
        )
    return paths


def parse_question_url(url: str) -> tuple[str, int] | None:
    match = QUESTION_URL_RE.search(url or "")
    if not match:
        return None
    return match.group("subject"), int(match.group("question_no"))


def find_question_heading(page_html: str, question_url: str) -> Tag:
    parsed = parse_question_url(question_url)
    if parsed is None:
        raise ValueError(f"unsupported question_url fragment: {question_url}")
    subject_code, question_no = parsed
    soup = BeautifulSoup(page_html, "html.parser")
    subject_div = soup.find("div", class_=lambda value: value and f"tab-content-{subject_code}" in value.split())
    search_root = subject_div or soup
    label = f"問{question_no}"
    heading = search_root.find("h2", string=lambda value: normalize_inline_text(value or "") == label)
    if not isinstance(heading, Tag):
        raise ValueError(f"question heading not found: {question_url}")
    return heading


def collect_question_section(question_heading: Tag) -> BeautifulSoup:
    parts: list[str] = []
    node = question_heading.find_next_sibling()
    while node is not None:
        if getattr(node, "name", None) == "h2":
            break
        if isinstance(node, Tag):
            parts.append(str(node))
        node = node.find_next_sibling()
    return BeautifulSoup("".join(parts), "html.parser")


def marker_number(marker_text: str) -> int | None:
    normalized = normalize_inline_text(marker_text).translate(FULLWIDTH_DIGITS)
    match = CHOICE_MARKER_RE.match(normalized)
    if not match:
        return None
    return int(match.group("number"))


def text_without_marker(candidate: Tag, marker: Tag) -> str:
    cloned = BeautifulSoup(str(candidate), "html.parser")
    cloned_marker = cloned.find("strong")
    if cloned_marker is not None:
        cloned_marker.decompose()
    return normalize_inline_text(cloned.get_text(" ", strip=True))


def extract_choice_container(section: BeautifulSoup) -> Tag:
    choice_container = section.select_one(".num-choice-box")
    if not isinstance(choice_container, Tag):
        choice_container = section.select_one("ol.choice-list")
    if not isinstance(choice_container, Tag):
        raise ValueError("choice container not found")
    return choice_container


def extract_choices(choice_box: Tag) -> list[str]:
    choices_by_number: dict[int, str] = {}
    for candidate in choice_box.find_all(["li", "div"]):
        if not isinstance(candidate, Tag):
            continue
        marker = candidate.find("strong", recursive=False)
        if not isinstance(marker, Tag):
            continue
        number = marker_number(marker.get_text(" ", strip=True))
        if number is None:
            continue
        text = text_without_marker(candidate, marker)
        if text:
            choices_by_number[number] = text

    if not choices_by_number:
        raise ValueError("choice options not found")
    expected_numbers = list(range(1, max(choices_by_number) + 1))
    if sorted(choices_by_number) != expected_numbers:
        raise ValueError(f"choice numbers are not contiguous: {sorted(choices_by_number)}")
    return [choices_by_number[number] for number in expected_numbers]


def parse_answer_numbers_from_text(text: Any) -> list[int]:
    if not isinstance(text, str):
        return []
    translated = text.translate(FULLWIDTH_DIGITS)
    return [int(match.group("number")) for match in ANSWER_NUMBER_RE.finditer(translated)]


def extract_correct_number(section: BeautifulSoup, question: dict[str, Any]) -> int:
    answer_numbers = parse_answer_numbers_from_text(question.get("answer_result_text"))
    if not answer_numbers:
        existing = question.get("answer_result_inferred_correct_choice_numbers")
        if isinstance(existing, list):
            answer_numbers = [int(value) for value in existing if str(value).translate(FULLWIDTH_DIGITS).isdigit()]

    details = section.find("details")
    if isinstance(details, Tag):
        answer_heading = details.find(["h2", "h3"], string=lambda value: value and "正解" in value)
        if not answer_numbers and isinstance(answer_heading, Tag):
            answer_numbers = parse_answer_numbers_from_text(answer_heading.get_text(" ", strip=True))
        if not answer_numbers:
            detail_text = normalize_inline_text(details.get_text(" ", strip=True))
            answer_numbers = parse_answer_numbers_from_text(detail_text)
    unique_numbers = sorted(set(answer_numbers))
    if len(unique_numbers) != 1:
        raise ValueError(f"expected exactly one correct choice number, got {answer_numbers}")
    return unique_numbers[0]


def build_correct_choice_text(choice_count: int, correct_number: int) -> list[str]:
    if correct_number < 1 or correct_number > choice_count:
        raise ValueError(f"correct choice number out of range: {correct_number}/{choice_count}")
    return ["正解" if index == correct_number else "不正解" for index in range(1, choice_count + 1)]


def repair_question(question: dict[str, Any], page_html: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if question.get("choiceTextList"):
        return question, None
    question_url = str(question.get("question_url") or "")
    if "gassyunin.com" not in question_url:
        return question, None

    heading = find_question_heading(page_html, question_url)
    section = collect_question_section(heading)
    choice_container = extract_choice_container(section)
    choices = extract_choices(choice_container)
    correct_number = extract_correct_number(section, question)
    correct_choice_text = build_correct_choice_text(len(choices), correct_number)

    repaired = copy.deepcopy(question)
    repaired["questionType"] = "group_choice"
    repaired["choiceTextList"] = choices
    repaired["choiceTextMarkedList"] = choices.copy()
    repaired["correctChoiceText"] = correct_choice_text
    repaired["originalQuestionChoiceText"] = choices.copy()
    repaired["originalQuestionChoiceImageUrls"] = [[] for _ in choices]
    repaired["answer_result_inferred_correct_choice_numbers"] = [correct_number]
    repaired["answer_result_text"] = repaired.get("answer_result_text") or f"正解は {correct_number} です。"
    repaired["numChoiceBoxRepair"] = {
        "sourceUrl": question_url,
        "repairedAt": utc_now(),
        "method": "gassyunin_html_num_choice_box",
        "choiceCount": len(choices),
        "correctChoiceNumber": correct_number,
    }

    if not repaired.get("explanation_choice_correctness"):
        repaired["explanation_choice_correctness"] = correct_choice_text.copy()
    if not repaired.get("explanation_choice_snippets"):
        repaired["explanation_choice_snippets"] = [[] for _ in choices]

    return repaired, {
        "question_url": question_url,
        "choiceCount": len(choices),
        "correctChoiceNumber": correct_number,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    html_cache: dict[str, str] = {}
    changed_files: list[str] = []
    repaired_questions: list[dict[str, Any]] = []
    issue_records: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()

    for path in iter_source_paths(args.qualifications):
        payload = load_json(path)
        bodies = payload.get("question_bodies") if isinstance(payload, dict) else None
        if not isinstance(bodies, list):
            continue

        new_bodies: list[Any] = []
        file_changed = False
        for index, question in enumerate(bodies):
            if not isinstance(question, dict):
                new_bodies.append(question)
                continue
            if question.get("choiceTextList"):
                new_bodies.append(question)
                continue
            question_url = str(question.get("question_url") or "")
            if "gassyunin.com" not in question_url:
                new_bodies.append(question)
                continue
            page_url, _ = urldefrag(question_url)
            try:
                page_html = html_cache.get(page_url)
                if page_html is None:
                    page_html = fetch_html(page_url)
                    html_cache[page_url] = page_html
                repaired, repair_record = repair_question(question, page_html)
            except Exception as exc:  # noqa: BLE001
                issue_records.append(
                    {
                        "sourceFile": rel(path),
                        "questionIndex": index,
                        "question_url": question_url,
                        "error": str(exc),
                    }
                )
                new_bodies.append(question)
                continue

            new_bodies.append(repaired)
            if repair_record is not None:
                file_changed = True
                counts["repairedQuestions"] += 1
                repaired_questions.append(
                    {
                        "sourceFile": rel(path),
                        "questionIndex": index,
                        "questionLabel": question.get("questionLabel"),
                        "category": question.get("category"),
                        **repair_record,
                    }
                )

        if file_changed:
            changed_files.append(rel(path))
            if args.write:
                payload["question_bodies"] = new_bodies
                save_json(path, payload)

    return {
        "schemaVersion": "gas-shunin-num-choice-repair/v1",
        "generatedAt": utc_now(),
        "writeApplied": bool(args.write),
        "qualifications": args.qualifications,
        "changedFileCount": len(changed_files),
        "changedFiles": changed_files,
        "counts": dict(counts),
        "errorCount": len(issue_records),
        "errors": issue_records,
        "repairedQuestions": repaired_questions,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair empty gas-shunin num-choice source rows from gassyunin HTML")
    parser.add_argument(
        "--qualifications",
        nargs="+",
        default=list(QUALIFICATIONS),
        choices=QUALIFICATIONS,
    )
    parser.add_argument("--write", action="store_true", help="write repaired 00_source JSON files")
    parser.add_argument("--report", type=Path, help="write a JSON repair report")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run(args)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + "\n", encoding="utf-8")
    return 1 if report["errorCount"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
