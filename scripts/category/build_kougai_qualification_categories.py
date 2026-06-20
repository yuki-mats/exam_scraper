from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CANONICAL_CATEGORY_JSON = ROOT_DIR / "output" / "kougai" / "category" / "category.json"
DEFAULT_MAPPING_JSON = ROOT_DIR / "output" / "kougai" / "category" / "qualification_mappings.json"
DEFAULT_OUTPUT_ROOT = ROOT_DIR / "output"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def materialized_id(qualification_id: str, canonical_id: str) -> str:
    prefix = "kougai_"
    if not canonical_id.startswith(prefix):
        raise ValueError(f"canonical id must start with {prefix!r}: {canonical_id}")
    return f"{qualification_id}_{canonical_id[len(prefix):]}"


def require_unique(values: list[str], *, label: str) -> None:
    seen: set[str] = set()
    duplicated: list[str] = []
    for value in values:
        if value in seen:
            duplicated.append(value)
        seen.add(value)
    if duplicated:
        raise ValueError(f"{label} has duplicated ids: {sorted(set(duplicated))}")


def validate_inputs(canonical: dict[str, Any], mapping: dict[str, Any]) -> None:
    canonical_folder_ids = [str(folder.get("folderId")) for folder in canonical.get("folders", [])]
    canonical_qset_ids = [str(qset.get("questionSetId")) for qset in canonical.get("questionSets", [])]
    require_unique(canonical_folder_ids, label="canonical folders")
    require_unique(canonical_qset_ids, label="canonical questionSets")

    qualifications = mapping.get("qualifications", [])
    if not isinstance(qualifications, list):
        raise ValueError("mapping.qualifications must be a list")
    expected_count = mapping.get("metadata", {}).get("qualificationCountExpected")
    if expected_count is not None and expected_count != len(qualifications):
        raise ValueError(f"qualificationCountExpected={expected_count} actual={len(qualifications)}")

    qualification_ids = [str(item.get("qualificationId")) for item in qualifications]
    require_unique(qualification_ids, label="qualifications")

    folder_by_id = {folder_id: folder for folder_id, folder in zip(canonical_folder_ids, canonical["folders"])}
    for qualification in qualifications:
        qualification_id = qualification.get("qualificationId")
        folder_ids = qualification.get("canonicalFolderIds", [])
        if not isinstance(folder_ids, list) or not folder_ids:
            raise ValueError(f"{qualification_id}: canonicalFolderIds must be a non-empty list")
        missing = [folder_id for folder_id in folder_ids if folder_id not in folder_by_id]
        if missing:
            raise ValueError(f"{qualification_id}: unknown canonicalFolderIds: {missing}")
        expected_count = sum(int(folder_by_id[folder_id].get("examQuestionCount", 0)) for folder_id in folder_ids)
        if qualification.get("examQuestionCount") != expected_count:
            raise ValueError(
                f"{qualification_id}: examQuestionCount={qualification.get('examQuestionCount')} "
                f"expected={expected_count}"
            )


def build_qualification_category(
    *,
    canonical: dict[str, Any],
    mapping: dict[str, Any],
    qualification: dict[str, Any],
) -> dict[str, Any]:
    group_metadata = mapping.get("metadata", {})
    generated_at = str(group_metadata.get("updatedAt") or canonical.get("metadata", {}).get("updatedAt") or "")
    qualification_id = str(qualification["qualificationId"])
    canonical_folder_ids = list(qualification["canonicalFolderIds"])

    folder_by_id = {folder["folderId"]: folder for folder in canonical["folders"]}
    qsets_by_folder: dict[str, list[dict[str, Any]]] = {}
    for qset in canonical["questionSets"]:
        qsets_by_folder.setdefault(str(qset["folderId"]), []).append(qset)

    folder_id_map = {
        canonical_folder_id: materialized_id(qualification_id, canonical_folder_id)
        for canonical_folder_id in canonical_folder_ids
    }

    folders: list[dict[str, Any]] = []
    question_sets: list[dict[str, Any]] = []

    for display_order, canonical_folder_id in enumerate(canonical_folder_ids, start=1):
        source_folder = folder_by_id[canonical_folder_id]
        folder = copy.deepcopy(source_folder)
        folder["folderId"] = folder_id_map[canonical_folder_id]
        folder["qualificationId"] = qualification_id
        folder["qualificationGroupId"] = group_metadata.get("qualificationGroupId", "kougai")
        folder["canonicalFolderId"] = canonical_folder_id
        folder["sourceSharedFolderId"] = canonical_folder_id
        folder["canonicalOrder"] = source_folder.get("order")
        folder["order"] = display_order
        folder["questionCount"] = 0
        folder["isDeleted"] = False
        folder["updatedAt"] = generated_at
        folders.append(folder)

        for source_qset in qsets_by_folder.get(canonical_folder_id, []):
            qset = copy.deepcopy(source_qset)
            canonical_qset_id = str(source_qset["questionSetId"])
            qset["questionSetId"] = materialized_id(qualification_id, canonical_qset_id)
            qset["folderId"] = folder_id_map[canonical_folder_id]
            qset["qualificationId"] = qualification_id
            qset["qualificationGroupId"] = group_metadata.get("qualificationGroupId", "kougai")
            qset["canonicalFolderId"] = canonical_folder_id
            qset["canonicalQuestionSetId"] = canonical_qset_id
            qset["sourceSharedFolderId"] = canonical_folder_id
            qset["sourceSharedQuestionSetId"] = canonical_qset_id
            qset["questionCount"] = 0
            qset["isDeleted"] = False
            qset["updatedAt"] = generated_at
            question_sets.append(qset)

    return {
        "metadata": {
            "qualificationId": qualification_id,
            "qualificationGroupId": group_metadata.get("qualificationGroupId", "kougai"),
            "licenseName": qualification["licenseName"],
            "displayName": qualification["displayName"],
            "shortName": qualification["shortName"],
            "categoryKind": "materializedQualificationCategory",
            "canonicalQualificationId": canonical.get("metadata", {}).get("qualificationId", "kougai"),
            "canonicalCategoryJson": group_metadata.get("canonicalCategoryJson"),
            "mappingJson": "output/kougai/category/qualification_mappings.json",
            "mappingBasis": group_metadata.get("mappingBasis"),
            "sourceUrls": group_metadata.get("sourceUrls", {}),
            "examQuestionCount": qualification["examQuestionCount"],
            "folderCountExpected": len(folders),
            "folderCountActual": len(folders),
            "questionSetCountExpected": len(question_sets),
            "questionSetCountActual": len(question_sets),
            "generatedAt": generated_at,
            "notes": [
                "Generated from the kougai canonical taxonomy and qualification mapping.",
                "folders/questionSets are materialized per qualification for current Firestore/app compatibility.",
                "canonicalFolderId/canonicalQuestionSetId preserve shared subject identity.",
            ],
        },
        "updatedAt": generated_at,
        "folders": folders,
        "questionSets": question_sets,
    }


def build_all_categories(canonical: dict[str, Any], mapping: dict[str, Any]) -> dict[str, dict[str, Any]]:
    validate_inputs(canonical, mapping)
    return {
        str(qualification["qualificationId"]): build_qualification_category(
            canonical=canonical,
            mapping=mapping,
            qualification=qualification,
        )
        for qualification in mapping["qualifications"]
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build materialized kougai category.json files for each qualification division."
    )
    parser.add_argument("--canonical-category-json", type=Path, default=DEFAULT_CANONICAL_CATEGORY_JSON)
    parser.add_argument("--mapping-json", type=Path, default=DEFAULT_MAPPING_JSON)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--qualification-id",
        action="append",
        default=[],
        help="Build only the specified qualificationId. Repeatable. Defaults to all.",
    )
    args = parser.parse_args()

    canonical = load_json(args.canonical_category_json.expanduser().resolve())
    mapping = load_json(args.mapping_json.expanduser().resolve())
    categories = build_all_categories(canonical, mapping)

    selected_ids = set(args.qualification_id or categories.keys())
    unknown_ids = selected_ids - set(categories)
    if unknown_ids:
        print(f"unknown qualificationId(s): {sorted(unknown_ids)}", file=sys.stderr)
        return 2

    output_root = args.output_root.expanduser().resolve()
    for qualification_id in sorted(selected_ids):
        category = categories[qualification_id]
        output_path = output_root / qualification_id / "category" / "category.json"
        write_json(output_path, category)
        print(
            f"wrote {output_path} "
            f"folders={len(category['folders'])} questionSets={len(category['questionSets'])}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
