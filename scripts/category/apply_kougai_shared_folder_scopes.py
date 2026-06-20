from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.category.apply_shared_folder_scopes import (
    apply_folder_scopes as _apply_folder_scopes,
    build_folder_scopes,
    load_json,
    write_json,
)


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CATEGORY_JSON = ROOT_DIR / "output" / "kougai" / "category" / "category.json"
DEFAULT_MAPPING_JSON = ROOT_DIR / "output" / "kougai" / "category" / "qualification_mappings.json"
DEFAULT_FOLDER_SCOPE_SOURCE = "output/kougai/category/qualification_mappings.json"


def apply_folder_scopes(category: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    return _apply_folder_scopes(
        category,
        mapping,
        folder_scope_source=DEFAULT_FOLDER_SCOPE_SOURCE,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compatibility wrapper for kougai. The standard implementation is "
            "scripts/category/apply_shared_folder_scopes.py."
        )
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
