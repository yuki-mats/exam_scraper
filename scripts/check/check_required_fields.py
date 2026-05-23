#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.common.requirements import (
    DEFAULT_REQUIREMENTS_PATH,
    RequirementsError,
    get_stage_rules,
    load_requirements,
    validate_records,
)


def infer_qualification_from_base_dir(base_dir: Path) -> str | None:
    try:
        # 例: output/2nd-class-kenchikushi/questions_json
        if base_dir.name == "questions_json":
            return base_dir.parent.name
    except Exception:  # noqa: BLE001
        return None
    return None


def iter_source_files(base_dir: Path) -> list[Path]:
    return sorted(base_dir.rglob("00_source/*.json"))


def iter_merged_files(base_dir: Path) -> list[Path]:
    files: list[Path] = []
    for subdir in ("20_merged_1", "30_merged_2"):
        files.extend(sorted(base_dir.rglob(f"{subdir}/*.json")))
    return files


def iter_firestore_files(base_dir: Path, list_group_id: str | None) -> list[Path]:
    if list_group_id:
        group_dir = base_dir / list_group_id / "40_convert"
        if not group_dir.exists():
            return []
        return sorted(group_dir.glob("*_firestore_*.json"))
    # 資格全体: 全 list_group_id の 40_convert を見る
    return sorted(base_dir.rglob("40_convert/*_firestore_*.json"))


def load_records(path: Path, array_key: str) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get(array_key)
    if not isinstance(records, list):
        return []
    return [r for r in records if isinstance(r, dict)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="requirements(必須項目)チェックを実行する")
    parser.add_argument(
        "--base-dir",
        type=Path,
        required=True,
        help="questions_json ルート（例: output/2nd-class-kenchikushi/questions_json）",
    )
    parser.add_argument(
        "--requirements",
        type=Path,
        default=DEFAULT_REQUIREMENTS_PATH,
        help="requirements TOML のパス",
    )
    parser.add_argument(
        "--stage",
        choices=("source", "merged", "firestore"),
        required=True,
        help="チェック対象ステージ",
    )
    parser.add_argument(
        "--list-group-id",
        default=None,
        help="firestoreステージで特定のlist_group_idに限定（任意）",
    )
    args = parser.parse_args(argv)

    base_dir = args.base_dir.resolve()
    qualification = infer_qualification_from_base_dir(base_dir)

    try:
        requirements = load_requirements(args.requirements)
    except RequirementsError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2

    stage = args.stage
    all_errors: list[str] = []

    if stage == "source":
        rules = get_stage_rules(requirements, stage="source", record_array="question_bodies", qualification=qualification)
        for path in iter_source_files(base_dir):
            records = load_records(path, "question_bodies")
            all_errors.extend(validate_records(records=records, rules=rules, source_path=path))
    elif stage == "merged":
        rules = get_stage_rules(requirements, stage="merged", record_array="question_bodies", qualification=qualification)
        for path in iter_merged_files(base_dir):
            records = load_records(path, "question_bodies")
            all_errors.extend(validate_records(records=records, rules=rules, source_path=path))
    else:
        rules = get_stage_rules(requirements, stage="firestore", record_array="questions", qualification=qualification)
        for path in iter_firestore_files(base_dir, args.list_group_id):
            records = load_records(path, "questions")
            all_errors.extend(validate_records(records=records, rules=rules, source_path=path, id_keys=("questionId",)))

    if all_errors:
        try:
            print(f"[NG] errors={len(all_errors)}")
            for line in all_errors[:200]:
                print(line)
            if len(all_errors) > 200:
                print(f"... truncated ({len(all_errors) - 200} more)")
        except BrokenPipeError:
            # `... | head` 等で途中切断された場合は静かに終了する
            return 1
        return 1

    print("[OK] requirements check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
