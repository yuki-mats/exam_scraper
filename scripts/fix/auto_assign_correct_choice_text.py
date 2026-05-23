#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ANSWER_RESULT_RE = re.compile(r"正解は\s*([0-9０-９]+(?:\s*,\s*[0-9０-９]+)*)\s*です。")
FULLWIDTH_DIGIT_TRANSLATION = str.maketrans("０１２３４５６７８９", "0123456789")
TARGET_SUBDIRS = ("20_merged_1", "30_merged_2")


@dataclass(frozen=True)
class UnresolvedRecord:
    file_path: Path
    question_index: int
    original_question_id: str | None
    question_type: str | None
    question_intent: str | None
    answer_result_text: str | None
    reason: str


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def parse_answer_numbers(answer_result_text: Any) -> list[int]:
    text = str(answer_result_text or "").translate(FULLWIDTH_DIGIT_TRANSLATION)
    match = ANSWER_RESULT_RE.search(text)
    if not match:
        return []

    numbers: list[int] = []
    for part in match.group(1).split(","):
        part = part.strip()
        if not part.isdigit():
            continue
        number = int(part)
        if number not in numbers:
            numbers.append(number)
    return numbers


def get_inferred_answer_numbers(question_body: dict) -> list[int]:
    """
    answer_result_inferred_correct_choice_numbers を優先して取得する。
    無い場合は answer_result_text を正規表現でパースする。
    """
    candidates = question_body.get("answer_result_inferred_correct_choice_numbers")
    if isinstance(candidates, list) and candidates:
        numbers: list[int] = []
        for value in candidates:
            if isinstance(value, int):
                numbers.append(value)
            elif str(value).isdigit():
                numbers.append(int(str(value)))
        # 重複除外 + 昇順
        normalized: list[int] = []
        for num in numbers:
            if num not in normalized:
                normalized.append(num)
        return sorted(normalized)
    return parse_answer_numbers(question_body.get("answer_result_text"))


def detect_choice_count(question_body: dict) -> int:
    choice_text_list = question_body.get("choiceTextList")
    if isinstance(choice_text_list, list) and choice_text_list:
        return len(choice_text_list)

    correct_choice_text = question_body.get("correctChoiceText")
    if isinstance(correct_choice_text, list) and correct_choice_text:
        return len(correct_choice_text)

    choice_image_urls = question_body.get("originalQuestionChoiceImageUrls")
    if isinstance(choice_image_urls, list) and choice_image_urls:
        return len(choice_image_urls)

    return 0


def build_expected_correct_choice_text(question_body: dict) -> tuple[list[str] | None, str | None]:
    answer_numbers = get_inferred_answer_numbers(question_body)
    if not answer_numbers:
        return None, "answer_result_text_unparseable"

    choice_count = detect_choice_count(question_body)
    if choice_count <= 0:
        return None, "choice_count_unresolved"

    if any((num < 1 or num > choice_count) for num in answer_numbers):
        return None, "answer_number_out_of_range"

    answer_indexes = sorted({num - 1 for num in answer_numbers})
    question_intent = str(question_body.get("questionIntent") or "").strip() or None
    if question_intent not in ("select_correct", "select_incorrect"):
        return None, "question_intent_required"

    # ルール: answer_numbers の件数が絶対。
    # - select_correct   → answer_numbers の位置が「正しい」
    # - select_incorrect → answer_numbers の位置が「間違い」
    if question_intent == "select_incorrect":
        labels = ["正しい"] * choice_count
        for idx in answer_indexes:
            labels[idx] = "間違い"
        return labels, None

    labels = ["間違い"] * choice_count
    for idx in answer_indexes:
        labels[idx] = "正しい"
    return labels, None


def process_file(path: Path, *, apply: bool) -> tuple[int, list[UnresolvedRecord]]:
    data = load_json(path)
    if not isinstance(data, dict):
        return 0, []

    question_bodies = data.get("question_bodies")
    if not isinstance(question_bodies, list):
        return 0, []

    updated = 0
    unresolved: list[UnresolvedRecord] = []

    for idx, question_body in enumerate(question_bodies):
        if not isinstance(question_body, dict):
            continue

        expected, reason = build_expected_correct_choice_text(question_body)
        if expected is None:
            unresolved.append(
                UnresolvedRecord(
                    file_path=path,
                    question_index=idx,
                    original_question_id=question_body.get("original_question_id") or question_body.get("public_question_id"),
                    question_type=question_body.get("questionType"),
                    question_intent=question_body.get("questionIntent"),
                    answer_result_text=question_body.get("answer_result_text"),
                    reason=str(reason),
                )
            )
            continue

        if question_body.get("correctChoiceText") == expected:
            continue

        question_body["correctChoiceText"] = expected
        updated += 1

    if apply and updated:
        save_json(path, data)

    return updated, unresolved


def collect_target_files(group_dir: Path) -> list[Path]:
    files: list[Path] = []
    for subdir_name in TARGET_SUBDIRS:
        subdir = group_dir / subdir_name
        if not subdir.exists():
            continue
        # *_invalid.json は手動対応用の外出しなので自動更新対象から除外する
        files.extend(sorted(p for p in subdir.glob("*.json") if not p.name.endswith("_invalid.json")))
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="answer_result_text と questionIntent から merged JSON の correctChoiceText を自動割当する",
    )
    parser.add_argument("list_group_id", type=str, help="list_group_id")
    parser.add_argument(
        "--base-dir",
        type=Path,
        required=True,
        help="questions_json のベースディレクトリ",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="ファイルを上書きする（省略時は dry-run）",
    )
    parser.add_argument(
        "--fail-on-unresolved",
        action="store_true",
        help="questionIntent 未確定などで自動割当できないレコードがあれば異常終了する",
    )
    args = parser.parse_args(argv)

    group_dir = (args.base_dir / args.list_group_id).resolve()
    if not group_dir.exists():
        print(f"[ERROR] list_group_id directory not found: {group_dir}", file=sys.stderr)
        return 1

    target_files = collect_target_files(group_dir)
    if not target_files:
        print(f"[INFO] target files not found: {group_dir}")
        return 0

    total_updated = 0
    unresolved_all: list[UnresolvedRecord] = []

    for path in target_files:
        updated, unresolved = process_file(path, apply=args.apply)
        total_updated += updated
        unresolved_all.extend(unresolved)
        if updated:
            label = "UPDATED" if args.apply else "DRY-RUN"
            print(f"[{label}] {path}: correctChoiceText updated={updated}")

    print(
        f"[SUMMARY] files={len(target_files)} updated={total_updated} unresolved={len(unresolved_all)}"
    )

    if unresolved_all:
        for item in unresolved_all[:20]:
            print(
                "[UNRESOLVED] "
                f"file={item.file_path} "
                f"idx={item.question_index} "
                f"id={item.original_question_id} "
                f"type={item.question_type} "
                f"intent={item.question_intent} "
                f"reason={item.reason} "
                f"answer_result_text={item.answer_result_text}"
            )
        if args.fail_on_unresolved:
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
