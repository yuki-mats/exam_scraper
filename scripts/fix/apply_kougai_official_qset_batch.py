from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CATEGORY_JSON = ROOT_DIR / "output" / "kougai" / "category" / "category.json"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolve_repo_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path.resolve()


def category_question_set_ids(category_json: Path) -> set[str]:
    category = load_json(category_json)
    return {
        str(item["questionSetId"])
        for item in category.get("questionSets", [])
        if isinstance(item, dict) and item.get("questionSetId")
    }


def require_list_payload(payload: Any, target_file: Path) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError(f"{target_file}: expected a list payload")
    records = [item for item in payload if isinstance(item, dict)]
    if len(records) != len(payload):
        raise ValueError(f"{target_file}: all list items must be JSON objects")
    return records


def apply_batch(*, batch_path: Path, category_json: Path, write: bool) -> dict[str, Any]:
    batch = load_json(batch_path)
    if not isinstance(batch, dict):
        raise ValueError(f"{batch_path}: expected a JSON object")

    target_file_value = batch.get("targetFile")
    if not isinstance(target_file_value, str) or not target_file_value:
        raise ValueError(f"{batch_path}: targetFile is required")
    target_file = resolve_repo_path(target_file_value)

    assignments = batch.get("assignments")
    if not isinstance(assignments, list) or not assignments:
        raise ValueError(f"{batch_path}: assignments[] is required")

    valid_qsets = category_question_set_ids(category_json)
    payload = load_json(target_file)
    records = require_list_payload(payload, target_file)
    by_original_id: dict[str, dict[str, Any]] = {}
    for record in records:
        original_id = record.get("original_question_id")
        if not original_id:
            raise ValueError(f"{target_file}: record without original_question_id")
        original_id = str(original_id)
        if original_id in by_original_id:
            raise ValueError(f"{target_file}: duplicate original_question_id: {original_id}")
        by_original_id[original_id] = record

    changed = 0
    unchanged = 0
    for assignment in assignments:
        if not isinstance(assignment, dict):
            raise ValueError(f"{batch_path}: assignment must be an object")
        original_id = str(assignment.get("original_question_id") or "")
        to_qset = str(assignment.get("toQuestionSetId") or "")
        from_qset = str(assignment.get("fromQuestionSetId") or "")
        question_url = str(assignment.get("question_url") or "")
        if not original_id or not to_qset:
            raise ValueError(f"{batch_path}: assignment requires original_question_id and toQuestionSetId")
        if to_qset not in valid_qsets:
            raise ValueError(f"{batch_path}: unknown toQuestionSetId for {original_id}: {to_qset}")
        record = by_original_id.get(original_id)
        if record is None:
            raise ValueError(f"{target_file}: assignment missing target record: {original_id}")
        if question_url and str(record.get("question_url") or "") != question_url:
            raise ValueError(f"{target_file}: question_url mismatch for {original_id}")
        current_qset = str(record.get("questionSetId") or "")
        if from_qset and current_qset != from_qset and current_qset != to_qset:
            raise ValueError(
                f"{target_file}: fromQuestionSetId mismatch for {original_id}: "
                f"expected {from_qset}, got {current_qset}"
            )
        if current_qset == to_qset:
            unchanged += 1
            continue
        record["questionSetId"] = to_qset
        changed += 1

    if write and changed:
        write_json(target_file, payload)

    return {
        "batch": str(batch_path.relative_to(ROOT_DIR) if batch_path.is_relative_to(ROOT_DIR) else batch_path),
        "targetFile": str(target_file.relative_to(ROOT_DIR) if target_file.is_relative_to(ROOT_DIR) else target_file),
        "assignments": len(assignments),
        "changed": changed,
        "unchanged": unchanged,
        "write": write,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply reviewed kougai official questionSetId batch assignments.")
    parser.add_argument("batch", type=Path)
    parser.add_argument("--category-json", type=Path, default=DEFAULT_CATEGORY_JSON)
    parser.add_argument("--write", action="store_true", help="Persist changes to targetFile.")
    args = parser.parse_args()

    summary = apply_batch(
        batch_path=args.batch.expanduser().resolve(),
        category_json=args.category_json.expanduser().resolve(),
        write=args.write,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
