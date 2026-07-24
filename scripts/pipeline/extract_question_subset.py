#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="question_bodies / Firestore questions から条件一致分だけを切り出す"
    )
    parser.add_argument(
        "--source",
        required=True,
        help="20_merged_1 / 00_source / upload-ready の JSON",
    )
    parser.add_argument("--output", required=True, help="subset 保存先")
    parser.add_argument("--category", action="append", default=[], help="一致させる category。複数指定可")
    parser.add_argument(
        "--question-label",
        action="append",
        default=[],
        help="一致させる questionLabel。複数指定可",
    )
    parser.add_argument(
        "--question-id",
        action="append",
        default=[],
        help="一致させる questionId。複数指定可",
    )
    parser.add_argument(
        "--original-question-id",
        action="append",
        default=[],
        help="一致させる originalQuestionId。複数指定可",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_path = Path(args.source).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    payload = load_json(source_path)
    if not isinstance(payload, dict):
        raise SystemExit("source JSON must be an object")
    record_key = "question_bodies"
    records = payload.get(record_key)
    if not isinstance(records, list):
        record_key = "questions"
        records = payload.get(record_key)
    if not isinstance(records, list):
        raise SystemExit("source JSON missing question_bodies / questions")

    category_filter = set(args.category)
    question_label_filter = set(args.question_label)
    question_id_filter = set(args.question_id)
    original_question_id_filter = set(args.original_question_id)

    subset = []
    for question in records:
        if not isinstance(question, dict):
            continue
        if category_filter and question.get("category") not in category_filter:
            continue
        if question_label_filter and question.get("questionLabel") not in question_label_filter:
            continue
        if question_id_filter and question.get("questionId") not in question_id_filter:
            continue
        if (
            original_question_id_filter
            and question.get("originalQuestionId") not in original_question_id_filter
        ):
            continue
        subset.append(question)

    output_payload = dict(payload)
    output_payload[record_key] = subset
    if record_key == "questions" and "total_count" in output_payload:
        output_payload["total_count"] = len(subset)
    save_json(output_path, output_payload)
    print(f"[OK] extracted {len(subset)} questions")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
