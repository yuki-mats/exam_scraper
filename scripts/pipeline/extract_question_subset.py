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
    parser = argparse.ArgumentParser(description="question_bodies から条件一致分だけを切り出す")
    parser.add_argument("--source", required=True, help="20_merged_1 / 00_source の question_*.json")
    parser.add_argument("--output", required=True, help="subset 保存先")
    parser.add_argument("--category", action="append", default=[], help="一致させる category。複数指定可")
    parser.add_argument(
        "--question-label",
        action="append",
        default=[],
        help="一致させる questionLabel。複数指定可",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_path = Path(args.source).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    payload = load_json(source_path)
    if not isinstance(payload, dict):
        raise SystemExit("source JSON must be an object")
    question_bodies = payload.get("question_bodies")
    if not isinstance(question_bodies, list):
        raise SystemExit("source JSON missing question_bodies")

    category_filter = set(args.category)
    question_label_filter = set(args.question_label)

    subset = []
    for question in question_bodies:
        if not isinstance(question, dict):
            continue
        if category_filter and question.get("category") not in category_filter:
            continue
        if question_label_filter and question.get("questionLabel") not in question_label_filter:
            continue
        subset.append(question)

    output_payload = dict(payload)
    output_payload["question_bodies"] = subset
    save_json(output_path, output_payload)
    print(f"[OK] extracted {len(subset)} questions")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
