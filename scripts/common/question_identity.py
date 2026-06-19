from __future__ import annotations

from typing import Any, Mapping


def review_question_id(question: Mapping[str, Any]) -> str:
    """Return the stable key used by 01-04 review patch files."""
    firestore_ids = question.get("firestoreQuestionIds")
    if isinstance(firestore_ids, list):
        values = [str(value) for value in firestore_ids if value]
        if values:
            return "firestore:" + ",".join(values)

    for field in ("original_question_id", "public_question_id", "question_url"):
        value = question.get(field)
        if value:
            return str(value)
    return ""
