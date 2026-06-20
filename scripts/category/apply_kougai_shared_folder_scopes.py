from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CATEGORY_JSON = ROOT_DIR / "output" / "kougai" / "category" / "category.json"
DEFAULT_MAPPING_JSON = ROOT_DIR / "output" / "kougai" / "category" / "qualification_mappings.json"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_unique(values: list[str], value: str) -> None:
    value = value.strip()
    if value and value not in values:
        values.append(value)


def build_folder_scopes(mapping: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    scopes: dict[str, dict[str, list[str]]] = {}
    qualifications = mapping.get("qualifications", [])
    if not isinstance(qualifications, list):
        raise ValueError("mapping.qualifications must be a list")

    for qualification in qualifications:
        if not isinstance(qualification, dict):
            continue
        qualification_id = str(qualification.get("qualificationId") or "").strip()
        license_name = str(qualification.get("licenseName") or "").strip()
        folder_ids = qualification.get("canonicalFolderIds")
        if not qualification_id or not license_name:
            raise ValueError(f"qualificationId/licenseName is required: {qualification}")
        if not isinstance(folder_ids, list) or not folder_ids:
            raise ValueError(f"{qualification_id}: canonicalFolderIds must be a non-empty list")

        for folder_id_value in folder_ids:
            folder_id = str(folder_id_value or "").strip()
            if not folder_id:
                raise ValueError(f"{qualification_id}: canonicalFolderIds contains empty value")
            scope = scopes.setdefault(folder_id, {"qualificationIds": [], "licenseNames": []})
            _append_unique(scope["qualificationIds"], qualification_id)
            _append_unique(scope["licenseNames"], license_name)

    return scopes


def apply_folder_scopes(category: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    scopes = build_folder_scopes(mapping)
    folders = category.get("folders", [])
    if not isinstance(folders, list):
        raise ValueError("category.folders must be a list")

    folder_ids = {str(folder.get("folderId") or "") for folder in folders if isinstance(folder, dict)}
    missing = sorted(set(scopes) - folder_ids)
    if missing:
        raise ValueError(f"mapping references unknown folder ids: {missing}")

    for folder in folders:
        if not isinstance(folder, dict):
            continue
        folder_id = str(folder.get("folderId") or "").strip()
        scope = scopes.get(folder_id)
        if not scope:
            folder["qualificationIds"] = []
            folder["licenseNames"] = []
            continue
        folder["qualificationIds"] = scope["qualificationIds"]
        folder["licenseNames"] = scope["licenseNames"]

    metadata = category.setdefault("metadata", {})
    notes = metadata.setdefault("notes", [])
    note = "folders.qualificationIds/licenseNames define which qualification divisions share each subject; questionSets/questions remain canonical shared records."
    if isinstance(notes, list) and note not in notes:
        notes.append(note)
    metadata["folderScopeSource"] = "output/kougai/category/qualification_mappings.json"
    return category


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply kougai qualification/license arrays to canonical shared folders."
    )
    parser.add_argument("--category-json", type=Path, default=DEFAULT_CATEGORY_JSON)
    parser.add_argument("--mapping-json", type=Path, default=DEFAULT_MAPPING_JSON)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    category_path = args.category_json.expanduser().resolve()
    mapping_path = args.mapping_json.expanduser().resolve()
    category = apply_folder_scopes(load_json(category_path), load_json(mapping_path))
    folders = category.get("folders", [])
    scoped = [
        {
            "folderId": folder.get("folderId"),
            "qualificationCount": len(folder.get("qualificationIds", [])),
            "licenseCount": len(folder.get("licenseNames", [])),
        }
        for folder in folders
        if isinstance(folder, dict)
    ]
    print(json.dumps({"folders": scoped}, ensure_ascii=False, indent=2))
    if not args.dry_run:
        write_json(category_path, category)
        print(f"wrote {category_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
