#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


PRIMARY_SOURCE_SUBDIR = "20_merged_1"
FALLBACK_SOURCE_SUBDIR = "00_source"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def iter_question_files(list_group_dir: Path) -> list[Path]:
    primary_dir = list_group_dir / PRIMARY_SOURCE_SUBDIR
    if primary_dir.exists():
        return sorted(primary_dir.glob("question_*.json"))
    fallback_dir = list_group_dir / FALLBACK_SOURCE_SUBDIR
    if fallback_dir.exists():
        return sorted(fallback_dir.glob("question_*.json"))
    return []


def collect_targets(base_dir: Path) -> dict[str, Any]:
    years: list[dict[str, Any]] = []
    total_law_questions = 0

    for list_group_dir in sorted(path for path in base_dir.iterdir() if path.is_dir() and path.name.isdigit()):
        file_targets: list[dict[str, Any]] = []
        year_count = 0
        source_subdir = PRIMARY_SOURCE_SUBDIR if (list_group_dir / PRIMARY_SOURCE_SUBDIR).exists() else FALLBACK_SOURCE_SUBDIR

        for question_file in iter_question_files(list_group_dir):
            payload = load_json(question_file)
            question_bodies = payload.get("question_bodies")
            if not isinstance(question_bodies, list):
                continue

            law_questions = []
            for question in question_bodies:
                if not isinstance(question, dict):
                    continue
                if question.get("category") != "法令":
                    continue
                law_questions.append(
                    {
                        "original_question_id": question.get("original_question_id") or question.get("public_question_id"),
                        "public_question_id": question.get("public_question_id"),
                        "questionLabel": question.get("questionLabel"),
                        "question_url": question.get("question_url"),
                    }
                )

            if not law_questions:
                continue

            year_count += len(law_questions)
            total_law_questions += len(law_questions)
            file_targets.append(
                {
                    "source_file": str(question_file),
                    "law_question_count": len(law_questions),
                    "law_questions": law_questions,
                }
            )

        if file_targets:
            years.append(
                {
                    "list_group_id": list_group_dir.name,
                    "source_subdir": source_subdir,
                    "law_question_count": year_count,
                    "files": file_targets,
                }
            )

    return {
        "base_dir": str(base_dir),
        "total_law_question_count": total_law_questions,
        "years": years,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="法令問題の explanation 対象一覧を出力する")
    parser.add_argument("base_dir", help="output/<qualification>/questions_json")
    parser.add_argument("--output", help="manifest 保存先 JSON")
    args = parser.parse_args()

    base_dir = Path(args.base_dir).expanduser().resolve()
    if not base_dir.exists():
        raise SystemExit(f"base_dir not found: {base_dir}")

    manifest = collect_targets(base_dir)
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print(output_path)
    else:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
