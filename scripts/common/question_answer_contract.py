from __future__ import annotations

from collections.abc import Sequence
from typing import Any


QUESTION_LEVEL_TYPES = frozenset({"flash_card", "group_choice"})
CORRECT_LABELS = frozenset({"正解", "正しい"})


def correct_choice_count(correct_choices: Any) -> int | None:
    if not isinstance(correct_choices, Sequence) or isinstance(
        correct_choices,
        (str, bytes),
    ):
        return None
    return sum(
        1
        for value in correct_choices
        if isinstance(value, str) and value.strip() in CORRECT_LABELS
    )


def question_level_answer_cardinality_issue(
    question_type: Any,
    correct_choices: Any,
) -> str | None:
    """Validate the final cross-field contract without guessing which field is wrong."""

    if question_type not in QUESTION_LEVEL_TYPES:
        return None
    count = correct_choice_count(correct_choices)
    if count == 1:
        return None
    if count is None:
        detail = "correctChoiceTextを配列として確認できません"
    else:
        detail = f"正答が{count}件あります"
    return (
        f"{question_type}は公開時に正答を1件だけ必要としますが、{detail}。"
        "questionTypeとcorrectChoiceTextのどちらが正しいかを再確認してください。"
    )
