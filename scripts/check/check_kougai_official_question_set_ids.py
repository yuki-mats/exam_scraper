from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


DEFAULT_QUESTIONS_ROOT = ROOT_DIR / "output" / "kougai" / "questions_json"
DEFAULT_CATEGORY_JSON = ROOT_DIR / "output" / "kougai" / "category" / "category.json"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def category_question_set_ids(category: dict[str, Any]) -> set[str]:
    return {
        str(item["questionSetId"])
        for item in category.get("questionSets", [])
        if isinstance(item, dict) and item.get("questionSetId")
    }


def question_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("question_bodies", "questions"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def question_key(record: dict[str, Any]) -> str:
    for key in ("original_question_id", "originalQuestionId", "public_question_id", "questionId", "question_url"):
        value = record.get(key)
        if value:
            return str(value)
    return ""


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def scan_question_set_ids(*, questions_root: Path, category_json: Path, stage: str) -> dict[str, Any]:
    valid_ids = category_question_set_ids(load_json(category_json))
    files = sorted(questions_root.glob(f"*/{stage}/*.json"))
    missing_counter: Counter[str] = Counter()
    empty_count = 0
    record_count = 0
    samples: list[dict[str, str]] = []

    for path in files:
        payload = load_json(path)
        for record in question_records(payload):
            record_count += 1
            qset_id = record.get("questionSetId")
            if not qset_id:
                empty_count += 1
                if len(samples) < 20:
                    samples.append(
                        {
                            "file": display_path(path),
                            "questionKey": question_key(record),
                            "questionSetId": "",
                        }
                    )
                continue
            qset_id = str(qset_id)
            if qset_id not in valid_ids:
                missing_counter[qset_id] += 1
                if len(samples) < 20:
                    samples.append(
                        {
                            "file": display_path(path),
                            "questionKey": question_key(record),
                            "questionSetId": qset_id,
                        }
                    )

    return {
        "stage": stage,
        "filesScanned": len(files),
        "recordsScanned": record_count,
        "validQuestionSetIdCount": len(valid_ids),
        "emptyQuestionSetIdCount": empty_count,
        "invalidQuestionSetIdCounts": dict(sorted(missing_counter.items())),
        "invalidRecordCount": sum(missing_counter.values()),
        "samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check kougai 04 questionSetId outputs against the official canonical category.json."
    )
    parser.add_argument("--questions-root", type=Path, default=DEFAULT_QUESTIONS_ROOT)
    parser.add_argument("--category-json", type=Path, default=DEFAULT_CATEGORY_JSON)
    parser.add_argument("--stage", default="22_questionSetId_linked")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON summary.")
    args = parser.parse_args()

    summary = scan_question_set_ids(
        questions_root=args.questions_root.expanduser().resolve(),
        category_json=args.category_json.expanduser().resolve(),
        stage=args.stage,
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"stage: {summary['stage']}")
        print(f"filesScanned: {summary['filesScanned']}")
        print(f"recordsScanned: {summary['recordsScanned']}")
        print(f"validQuestionSetIdCount: {summary['validQuestionSetIdCount']}")
        print(f"emptyQuestionSetIdCount: {summary['emptyQuestionSetIdCount']}")
        print(f"invalidRecordCount: {summary['invalidRecordCount']}")
        for qset_id, count in summary["invalidQuestionSetIdCounts"].items():
            print(f"  {qset_id}: {count}")
        if summary["samples"]:
            print("samples:")
            for sample in summary["samples"]:
                print(f"  {sample['file']} {sample['questionKey']} {sample['questionSetId']}")

    return 1 if summary["emptyQuestionSetIdCount"] or summary["invalidRecordCount"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
