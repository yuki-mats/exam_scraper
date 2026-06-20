from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.category.build_kougai_qualification_categories import (
    DEFAULT_CANONICAL_CATEGORY_JSON,
    DEFAULT_MAPPING_JSON,
    materialized_id,
)


DEFAULT_OUTPUT_ROOT = ROOT_DIR / "output"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def category_context(canonical: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    qset_to_folder = {
        str(qset["questionSetId"]): str(qset["folderId"])
        for qset in canonical.get("questionSets", [])
        if isinstance(qset, dict) and qset.get("questionSetId") and qset.get("folderId")
    }
    folder_ids = {
        str(folder["folderId"])
        for folder in canonical.get("folders", [])
        if isinstance(folder, dict) and folder.get("folderId")
    }
    qualifications = {
        str(item["qualificationId"]): item
        for item in mapping.get("qualifications", [])
        if isinstance(item, dict) and item.get("qualificationId")
    }
    folder_to_qualifications: dict[str, list[str]] = {folder_id: [] for folder_id in folder_ids}
    for qualification_id, qualification in qualifications.items():
        for folder_id in qualification.get("canonicalFolderIds", []):
            if folder_id not in folder_to_qualifications:
                raise ValueError(f"{qualification_id}: unknown canonical folder id: {folder_id}")
            folder_to_qualifications[folder_id].append(qualification_id)
    return {
        "qsetToFolder": qset_to_folder,
        "folderToQualifications": folder_to_qualifications,
        "qualifications": qualifications,
    }


def question_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("questions"), list):
        return [item for item in payload["questions"] if isinstance(item, dict)]
    raise ValueError("upload payload must be a list or an object with questions[]")


def materialized_question_id(qualification_id: str, canonical_question_id: str) -> str:
    if not canonical_question_id:
        raise ValueError("questionId is required for materialization")
    return f"{qualification_id}__{canonical_question_id}"


def materialize_question(record: dict[str, Any], *, qualification_id: str, canonical_folder_id: str) -> dict[str, Any]:
    canonical_question_set_id = str(record.get("questionSetId") or "")
    canonical_question_id = str(record.get("questionId") or "")
    materialized = copy.deepcopy(record)
    materialized["questionId"] = materialized_question_id(qualification_id, canonical_question_id)
    materialized["qualificationId"] = qualification_id
    materialized["folderId"] = materialized_id(qualification_id, canonical_folder_id)
    materialized["questionSetId"] = materialized_id(qualification_id, canonical_question_set_id)
    materialized["canonicalFolderId"] = canonical_folder_id
    materialized["canonicalQuestionSetId"] = canonical_question_set_id
    materialized["sourceSharedQuestionSetId"] = canonical_question_set_id
    materialized["sourceSharedQuestionId"] = canonical_question_id
    return materialized


def materialize_records(records: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    qset_to_folder: dict[str, str] = context["qsetToFolder"]
    folder_to_qualifications: dict[str, list[str]] = context["folderToQualifications"]
    materialized_by_qualification: dict[str, list[dict[str, Any]]] = {
        qualification_id: []
        for qualification_id in context["qualifications"]
    }
    invalid_qsets: dict[str, int] = {}

    for record in records:
        qset_id = str(record.get("questionSetId") or "")
        canonical_folder_id = qset_to_folder.get(qset_id)
        if not canonical_folder_id:
            invalid_qsets[qset_id] = invalid_qsets.get(qset_id, 0) + 1
            continue
        for qualification_id in folder_to_qualifications[canonical_folder_id]:
            materialized_by_qualification[qualification_id].append(
                materialize_question(
                    record,
                    qualification_id=qualification_id,
                    canonical_folder_id=canonical_folder_id,
                )
            )

    if invalid_qsets:
        raise ValueError(f"upload payload has questionSetId not found in canonical category: {invalid_qsets}")

    return {
        qualification_id: records
        for qualification_id, records in materialized_by_qualification.items()
        if records
    }


def list_input_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(input_path.glob("*/upload_to_firestore/*.json"))


def infer_list_group_id(input_file: Path, payload: Any | None = None) -> str:
    if isinstance(payload, dict):
        list_group_id = payload.get("list_group_id")
        if isinstance(list_group_id, str) and list_group_id.strip():
            return list_group_id.strip()
        questions = payload.get("questions")
        if isinstance(questions, list):
            for question in questions:
                if not isinstance(question, dict):
                    continue
                list_group_id = question.get("listGroupId")
                if isinstance(list_group_id, str) and list_group_id.strip():
                    return list_group_id.strip()
    try:
        if input_file.parent.name == "upload_to_firestore":
            candidate = input_file.parent.parent.name
            if candidate != "questions_json":
                return candidate
    except IndexError:
        pass
    return "unknown"


def materialize_file(
    *,
    input_file: Path,
    output_root: Path,
    context: dict[str, Any],
    dry_run: bool,
) -> dict[str, int]:
    payload = load_json(input_file)
    records = question_records(payload)
    materialized = materialize_records(records, context)
    list_group_id = infer_list_group_id(input_file, payload)
    counts: dict[str, int] = {}
    for qualification_id, qualification_records in sorted(materialized.items()):
        counts[qualification_id] = len(qualification_records)
        questions_json_dir = output_root / qualification_id / "questions_json"
        list_group_dir = questions_json_dir / list_group_id
        output_path = (
            questions_json_dir
            / "upload_to_firestore"
            / input_file.name
        )
        if not dry_run:
            list_group_dir.mkdir(parents=True, exist_ok=True)
            write_json(
                output_path,
                {
                    "list_group_id": list_group_id,
                    "questions": qualification_records,
                    "total_count": len(qualification_records),
                },
            )
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Deprecated: materialize canonical kougai Firestore upload JSON into "
            "qualification-specific upload JSON. Use shared folder scopes instead."
        )
    )
    parser.add_argument("input", type=Path, help="Canonical upload JSON file or questions_json root.")
    parser.add_argument("--canonical-category-json", type=Path, default=DEFAULT_CANONICAL_CATEGORY_JSON)
    parser.add_argument("--mapping-json", type=Path, default=DEFAULT_MAPPING_JSON)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--allow-legacy-materialization",
        action="store_true",
        help="Allow deprecated per-qualification question materialization.",
    )
    args = parser.parse_args()
    if not args.allow_legacy_materialization:
        print(
            "deprecated: per-qualification kougai question materialization is disabled. "
            "Use shared questions/questionSets and folder qualificationIds/licenseNames, "
            "or rerun with --allow-legacy-materialization for an explicit legacy audit.",
            file=sys.stderr,
        )
        return 2

    context = category_context(
        load_json(args.canonical_category_json.expanduser().resolve()),
        load_json(args.mapping_json.expanduser().resolve()),
    )
    input_files = list_input_files(args.input.expanduser().resolve())
    if not input_files:
        print(f"no input upload JSON files found: {args.input}", file=sys.stderr)
        return 1

    total_counts: dict[str, int] = {}
    for input_file in input_files:
        counts = materialize_file(
            input_file=input_file,
            output_root=args.output_root.expanduser().resolve(),
            context=context,
            dry_run=args.dry_run,
        )
        print(f"{input_file}: {counts}")
        for qualification_id, count in counts.items():
            total_counts[qualification_id] = total_counts.get(qualification_id, 0) + count

    print(f"total: {dict(sorted(total_counts.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
