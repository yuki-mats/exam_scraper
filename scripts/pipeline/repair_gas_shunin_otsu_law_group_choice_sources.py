#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag
from urllib.request import Request, urlopen


ROOT_DIR = Path("output/gas-shunin-otsu/questions_json")

TARGET_QUESTION_IDS = {
    "85637e8473839657": {
        "year": "2017",
        "label": "問4",
        "refs": "法86条、法91条、規78条",
    },
    "57549d6c206db6c3": {
        "year": "2018",
        "label": "問1",
        "refs": "法3条、法35条、法13条1項、法57条の3",
    },
    "4541d30bb8030bd6": {
        "year": "2019",
        "label": "問1",
        "refs": "法14条1項、規17条1項ただし書き、規22条1項一号ただし書き、規64条二号ヘ",
    },
    "963e0ea3649101da": {
        "year": "2020",
        "label": "問5",
        "refs": "法101条、法102条、規144条",
    },
    "24cc2bac28af7e28": {
        "year": "2020",
        "label": "問16",
        "refs": "法159条2項、法162条、特監法2条2項、特監法3条、特監法6条",
    },
    "11e45cb9ebc00dc2": {
        "year": "2021",
        "label": "問5",
        "refs": "法25条1項、規則151条1項、法31条、法67条",
    },
    "aacce362b22eb526": {
        "year": "2021",
        "label": "問11",
        "refs": "技告示7条1項一号、技省令52条の2、技省令53条",
    },
    "250fbc3bceff6113": {
        "year": "2022",
        "label": "問16",
        "refs": "法159条2項、規則207条二号、規則207条四号",
    },
    "606300689177531c": {
        "year": "2023",
        "label": "問7",
        "refs": "技省令6条2項、技省令12条、技省令5条、技告示3条三号",
    },
}

STAGE_SUBDIRS = ("00_source", "20_merged_1_law_only")

SECTION_PATTERN_TEMPLATE = r"<h2>{label}</h2>(.*?)(?:<hr>|\Z)"
CHOICE_OPTION_PATTERN = re.compile(
    r"<strong[^>]*>\((\d)\)</strong>\s*(.*?)</(?:div|li)>",
    re.DOTALL,
)
CORRECT_NUMBER_PATTERN = re.compile(r"🎯 正解:\s*\((\d)\)")
DETAIL_BLOCK_PATTERN = re.compile(
    r'<div class="statement-judge-(correct|wrong)">(.*?)</div>\s*(?=<div class="statement-judge-|</details>)',
    re.DOTALL,
)
TAG_PATTERN = re.compile(r"<[^>]+>")
WHITESPACE_PATTERN = re.compile(r"\s+")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def fetch_html(url: str) -> str:
    page_url, _ = urldefrag(url)
    request = Request(
        page_url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; Codex source repair)",
        },
    )
    with urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace")


def clean_text(value: str) -> str:
    value = TAG_PATTERN.sub("", value)
    value = html.unescape(value)
    value = value.replace("\u3000", " ")
    value = value.replace("\xa0", " ")
    value = WHITESPACE_PATTERN.sub(" ", value)
    return value.strip()


def extract_question_section(page_html: str, label: str) -> str:
    pattern = re.compile(
        SECTION_PATTERN_TEMPLATE.format(label=re.escape(label)),
        re.DOTALL,
    )
    match = pattern.search(page_html)
    if not match:
        raise ValueError(f"{label} の section を抽出できませんでした")
    return match.group(1)


def extract_choice_box(section_html: str) -> str:
    start = section_html.find('<div class="num-choice-box"')
    if start < 0:
        raise ValueError("num-choice-box を抽出できませんでした")
    details_start = section_html.find("<details>", start)
    if details_start < 0:
        raise ValueError("num-choice-box の終端を特定できませんでした")
    return section_html[start:details_start]


def extract_choices(choice_box_html: str) -> list[str]:
    choices_by_index: dict[int, str] = {}
    for raw_index, raw_text in CHOICE_OPTION_PATTERN.findall(choice_box_html):
        index = int(raw_index)
        cleaned = clean_text(raw_text)
        if not cleaned:
            continue
        choices_by_index[index] = cleaned
    if sorted(choices_by_index.keys()) != [1, 2, 3, 4, 5]:
        raise ValueError(f"選択肢が5件揃いません: {sorted(choices_by_index.keys())}")
    return [choices_by_index[idx] for idx in range(1, 6)]


def extract_correct_choice_number(section_html: str) -> int:
    match = CORRECT_NUMBER_PATTERN.search(section_html)
    if not match:
        raise ValueError("正解番号を抽出できませんでした")
    return int(match.group(1))


def build_correct_choice_text(question_intent: str, correct_choice_number: int, choice_count: int) -> list[str]:
    values: list[str] = []
    for idx in range(1, choice_count + 1):
        if question_intent == "select_incorrect":
            values.append("間違い" if idx == correct_choice_number else "正しい")
        else:
            values.append("正しい" if idx == correct_choice_number else "間違い")
    return values


def build_synthetic_snippets(
    *,
    choices: list[str],
    correct_choice_number: int,
    question_intent: str,
    refs: str,
) -> list[list[str]]:
    correct_choice = choices[correct_choice_number - 1]
    correct_choice_text = build_correct_choice_text(question_intent, correct_choice_number, len(choices))
    snippets: list[list[str]] = []
    for idx, choice in enumerate(choices):
        if correct_choice_text[idx] == "正しい":
            snippets.append([f"📌 関連: {refs}"])
            continue
        snippets.append([f"正しくは: {correct_choice}\n📌 関連: {refs}"])
    return snippets


def extract_detailed_snippets(section_html: str, refs_fallback: str) -> tuple[list[str] | None, list[list[str]] | None]:
    blocks = DETAIL_BLOCK_PATTERN.findall(section_html)
    if len(blocks) != 5:
        return None, None

    marked_choices: dict[int, str] = {}
    snippets: dict[int, list[str]] = {}
    for verdict, block_html in blocks:
        index_match = re.search(r"<strong[^>]*>\((\d)\)</strong>", block_html)
        if not index_match:
            return None, None
        idx = int(index_match.group(1))
        blockquote_match = re.search(r"<blockquote>(.*?)</blockquote>", block_html, re.DOTALL)
        correction_match = re.search(r"→\s*正しくは:</strong>\s*<span[^>]*>(.*?)</span>", block_html, re.DOTALL)
        hint_match = re.search(r"💡\s*(.*?)</p>", block_html, re.DOTALL)

        marked_choice = clean_text(blockquote_match.group(1)) if blockquote_match else ""
        if verdict == "wrong" and correction_match:
            correction = clean_text(correction_match.group(1))
            hint = clean_text(hint_match.group(1)) if hint_match else refs_fallback
            snippets[idx] = [f"正しくは: {correction}\n📌 関連: {hint}"]
        else:
            hint = clean_text(hint_match.group(1)) if hint_match else refs_fallback
            snippets[idx] = [f"📌 関連: {hint}"]
        marked_choices[idx] = marked_choice

    return (
        [marked_choices[idx] for idx in range(1, 6)],
        [snippets[idx] for idx in range(1, 6)],
    )


def repair_question(question: dict[str, Any], page_html: str, refs: str) -> bool:
    if len(question.get("choiceTextList") or []) > 0:
        return False

    label = str(question.get("questionLabel") or "")
    if not label:
        raise ValueError("questionLabel がありません")

    section_html = extract_question_section(page_html, label)
    choice_box_html = extract_choice_box(section_html)
    choices = extract_choices(choice_box_html)
    correct_choice_number = extract_correct_choice_number(section_html)
    question_intent = str(question.get("questionIntent") or "select_correct")
    correct_choice_text = build_correct_choice_text(question_intent, correct_choice_number, len(choices))
    marked_choices, detailed_snippets = extract_detailed_snippets(section_html, refs)
    snippets = detailed_snippets or build_synthetic_snippets(
        choices=choices,
        correct_choice_number=correct_choice_number,
        question_intent=question_intent,
        refs=refs,
    )

    question["choiceTextList"] = choices
    question["choiceTextMarkedList"] = marked_choices or choices.copy()
    question["correctChoiceText"] = correct_choice_text
    question["explanation_choice_snippets"] = snippets
    question["explanation_choice_correctness"] = correct_choice_text.copy()
    if not question.get("answer_result_inferred_correct_choice_numbers"):
        question["answer_result_inferred_correct_choice_numbers"] = [correct_choice_number]
    if not question.get("answer_result_text"):
        question["answer_result_text"] = f"正解は {correct_choice_number} です。"
    return True


def repair_stage_file(path: Path, page_html_cache: dict[str, str]) -> int:
    data = load_json(path)
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError(f"{path} に question_bodies がありません")

    updated = 0
    changed = False
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = question.get("original_question_id") or question.get("public_question_id")
        if not isinstance(question_id, str):
            continue
        target = TARGET_QUESTION_IDS.get(question_id)
        if target is None:
            continue
        question_url = question.get("question_url")
        if not isinstance(question_url, str) or not question_url:
            raise ValueError(f"{path}:{question_id} に question_url がありません")
        page_html = page_html_cache.get(question_url)
        if page_html is None:
            page_html = fetch_html(question_url)
            page_html_cache[question_url] = page_html
        if repair_question(question, page_html, target["refs"]):
            updated += 1
            changed = True

    if changed:
        dump_json(path, data)
    return updated


def target_stage_files(root: Path) -> list[Path]:
    paths: list[Path] = []
    for question_id, target in TARGET_QUESTION_IDS.items():
        year = target["year"]
        for stage_subdir in STAGE_SUBDIRS:
            stage_dir = root / year / stage_subdir
            if not stage_dir.exists():
                continue
            for path in sorted(stage_dir.glob("*.json")):
                paths.append(path)
    # de-dup while preserving order
    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return deduped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT_DIR)
    args = parser.parse_args()

    page_html_cache: dict[str, str] = {}
    total_updated = 0
    for path in target_stage_files(args.root):
        updated = repair_stage_file(path, page_html_cache)
        if updated:
            total_updated += updated
            print(f"[UPDATED] {path} questions={updated}")

    print(f"total repaired questions: {total_updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
