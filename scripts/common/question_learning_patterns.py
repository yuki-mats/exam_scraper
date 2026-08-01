from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG_PATH = REPO_ROOT / "config" / "question_learning_patterns.json"
QUESTION_LEARNING_PATTERN_FIELD = "questionLearningPatternId"


@dataclass(frozen=True)
class QuestionLearningPattern:
    id: str
    display_name: str
    description: str
    sort_order: int


@dataclass(frozen=True)
class QuestionLearningPatternCatalog:
    schema_version: int
    catalog_id: str
    field_name: str
    patterns: tuple[QuestionLearningPattern, ...]

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(pattern.id for pattern in self.patterns)


def load_question_learning_pattern_catalog(
    path: Path = DEFAULT_CATALOG_PATH,
) -> QuestionLearningPatternCatalog:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"問題学習パターンを読み込めません: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("問題学習パターンのrootはobjectである必要があります。")
    if payload.get("schemaVersion") != 1:
        raise ValueError("問題学習パターンのschemaVersionは1である必要があります。")
    catalog_id = str(payload.get("catalogId") or "").strip()
    field_name = str(payload.get("fieldName") or "").strip()
    if catalog_id != "question_learning_patterns":
        raise ValueError("問題学習パターンのcatalogIdが不正です。")
    if field_name != QUESTION_LEARNING_PATTERN_FIELD:
        raise ValueError("問題学習パターンのfieldNameが不正です。")
    raw_patterns = payload.get("patterns")
    if not isinstance(raw_patterns, list) or not raw_patterns:
        raise ValueError("問題学習パターンのpatternsは非空配列である必要があります。")
    patterns: list[QuestionLearningPattern] = []
    seen_ids: set[str] = set()
    seen_orders: set[int] = set()
    for index, raw in enumerate(raw_patterns):
        if not isinstance(raw, Mapping):
            raise ValueError(f"patterns[{index}]はobjectである必要があります。")
        pattern_id = str(raw.get("id") or "").strip()
        display_name = str(raw.get("displayName") or "").strip()
        description = str(raw.get("description") or "").strip()
        sort_order = raw.get("sortOrder")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", pattern_id):
            raise ValueError(f"patterns[{index}].idが不正です: {pattern_id}")
        if pattern_id in seen_ids:
            raise ValueError(f"問題学習パターンIDが重複しています: {pattern_id}")
        if not display_name or not description:
            raise ValueError(f"patterns[{index}]の表示名又は説明が空です。")
        if isinstance(sort_order, bool) or not isinstance(sort_order, int):
            raise ValueError(f"patterns[{index}].sortOrderは整数である必要があります。")
        if sort_order in seen_orders:
            raise ValueError(f"sortOrderが重複しています: {sort_order}")
        seen_ids.add(pattern_id)
        seen_orders.add(sort_order)
        patterns.append(
            QuestionLearningPattern(
                id=pattern_id,
                display_name=display_name,
                description=description,
                sort_order=sort_order,
            )
        )
    ordered = tuple(sorted(patterns, key=lambda pattern: pattern.sort_order))
    if tuple(patterns) != ordered:
        raise ValueError("patternsはsortOrderの昇順で並べる必要があります。")
    return QuestionLearningPatternCatalog(
        schema_version=1,
        catalog_id=catalog_id,
        field_name=field_name,
        patterns=ordered,
    )


CATALOG = load_question_learning_pattern_catalog()
QUESTION_LEARNING_PATTERN_IDS = CATALOG.ids


def question_learning_pattern_id_error(value: Any, *, required: bool) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return (
            f"{QUESTION_LEARNING_PATTERN_FIELD}が必要です。"
            if required
            else None
        )
    if not isinstance(value, str) or value not in QUESTION_LEARNING_PATTERN_IDS:
        return (
            f"{QUESTION_LEARNING_PATTERN_FIELD}は分類カタログのIDから1つ"
            "選んでください。"
        )
    return None
