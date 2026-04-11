from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Set


def load_json(path: str | Path) -> Any:
    path_obj = Path(path)
    with path_obj.open("r", encoding="utf-8") as f:
        return json.load(f)


def collect_category_ids(
    category: Dict[str, Any],
    questionset_only: bool = False,
) -> Set[str]:
    ids: Set[str] = set()

    for question_set in category.get("questionSets", []) or []:
        if not isinstance(question_set, dict):
            continue
        question_set_id = question_set.get("questionSetId")
        if question_set_id:
            ids.add(str(question_set_id))

    if questionset_only:
        return ids

    for folder in category.get("folders", []) or []:
        if not isinstance(folder, dict):
            continue
        folder_id = folder.get("folderId")
        if folder_id:
            ids.add(str(folder_id))

    return ids
