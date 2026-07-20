"""Shared explanation-shape rules for maintenance and publication."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


QUESTION_LEVEL_EXPLANATION_TYPES = frozenset({"flash_card", "group_choice"})


def uses_question_level_explanation(question_type: object) -> bool:
    """Return whether the maintenance explanation is one question-level item."""

    return str(question_type or "").strip() in QUESTION_LEVEL_EXPLANATION_TYPES


def expected_explanation_count(question_type: object, choice_count: int) -> int:
    """Return the canonical number of explanationText entries in patch/merged data."""

    if uses_question_level_explanation(question_type):
        return 1
    return max(choice_count, 0)


def explanation_shape_errors(
    value: object,
    *,
    question_type: object,
    choice_count: int,
) -> list[str]:
    """Validate the patch/merged explanationText shape."""

    if not isinstance(value, list):
        return ["explanationText must be list[str]"]
    expected = expected_explanation_count(question_type, choice_count)
    errors: list[str] = []
    if len(value) != expected:
        if uses_question_level_explanation(question_type):
            errors.append(
                f"{str(question_type or '').strip()} explanationText must contain "
                "exactly one question-level item"
            )
        else:
            errors.append(
                "explanationText length must match choiceTextList "
                f"(expected={expected} actual={len(value)})"
            )
    if any(not isinstance(item, str) or not item.strip() for item in value):
        errors.append("explanationText items must be non-empty strings")
    return errors


def public_explanation_text(
    explanations: object,
    *,
    question_type: object,
    choice_index: int,
    is_choice_only: bool,
) -> str | None:
    """Resolve the public explanation while enforcing the document-role boundary."""

    if is_choice_only:
        return None
    if not isinstance(explanations, Sequence) or isinstance(explanations, (str, bytes)):
        return ""
    if uses_question_level_explanation(question_type):
        # Canonical flash_card/group_choice data has one problem-level item.
        # During the migration window, legacy choice-aligned data is still
        # readable and must keep using the correct choice's explanation rather
        # than item 0.
        index = 0 if len(explanations) == 1 else choice_index
    else:
        index = choice_index
    if index < 0 or index >= len(explanations):
        return ""
    value: Any = explanations[index]
    return value if isinstance(value, str) else ""
