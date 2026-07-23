from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import urlparse


EXPLANATION_REFERENCE_FIELDS = {
    "title",
    "sourceUrl",
    "referenceDate",
    "choiceIndex",
}


def explanation_reference_errors(value: object) -> list[str]:
    """Validate the small, qualification-agnostic official-reference contract."""

    if value is None:
        return []
    if not isinstance(value, list):
        return ["explanationReferencesは配列で指定してください。"]

    errors: list[str] = []
    seen: set[tuple[str, int | None]] = set()
    for index, reference in enumerate(value):
        path = f"explanationReferences[{index}]"
        if not isinstance(reference, dict):
            errors.append(f"{path}はobjectで指定してください。")
            continue
        extra = sorted(set(reference) - EXPLANATION_REFERENCE_FIELDS)
        if extra:
            errors.append(f"{path}に未定義fieldがあります: {', '.join(extra)}")
        for field in ("title", "sourceUrl", "referenceDate"):
            if not isinstance(reference.get(field), str) or not reference[field].strip():
                errors.append(f"{path}.{field}は空でないstringが必要です。")

        source_url = str(reference.get("sourceUrl") or "").strip()
        if source_url:
            parsed = urlparse(source_url)
            if parsed.scheme != "https" or not parsed.netloc:
                errors.append(f"{path}.sourceUrlは有効なHTTPS URLが必要です。")

        reference_date = str(reference.get("referenceDate") or "").strip()
        if reference_date:
            try:
                date.fromisoformat(reference_date)
            except ValueError:
                errors.append(f"{path}.referenceDateはYYYY-MM-DD形式が必要です。")

        choice_index = reference.get("choiceIndex")
        if choice_index is not None and (
            not isinstance(choice_index, int)
            or isinstance(choice_index, bool)
            or choice_index < 0
        ):
            errors.append(f"{path}.choiceIndexは0以上の整数が必要です。")
            choice_index = None

        dedupe_key = (source_url, choice_index if isinstance(choice_index, int) else None)
        if source_url and dedupe_key in seen:
            errors.append(f"{path}.sourceUrlが同じ対象へ重複しています。")
        seen.add(dedupe_key)
    return errors


def normalize_explanation_references(
    value: object,
    *,
    choice_index: int | None = None,
) -> list[dict[str, Any]]:
    """Return verified reference metadata for one public question document."""

    if explanation_reference_errors(value):
        return []
    references = value if isinstance(value, list) else []
    normalized: list[dict[str, Any]] = []
    for reference in references:
        if not isinstance(reference, dict):
            continue
        reference_choice_index = reference.get("choiceIndex")
        if (
            choice_index is not None
            and isinstance(reference_choice_index, int)
            and reference_choice_index != choice_index
        ):
            continue
        item: dict[str, Any] = {
            "title": reference["title"].strip(),
            "sourceUrl": reference["sourceUrl"].strip(),
            "referenceDate": reference["referenceDate"].strip(),
        }
        if isinstance(reference_choice_index, int):
            item["choiceIndex"] = reference_choice_index
        normalized.append(item)
    return normalized
