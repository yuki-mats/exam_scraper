from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from scripts.common.question_answer_contract import selected_choice_labels


FIELD_NAME = "suggestedQuestionDetailsByChoice"
MAX_ITEMS_PER_CHOICE = 3


def public_choice_indexes(
    question_type: object,
    correct_choices: object,
    choice_count: int,
    question_intent: object,
) -> set[int]:
    """Return choice indexes that become isChoiceOnly=false split documents."""

    if choice_count <= 0:
        return set()
    if question_type == "true_false":
        return set(range(choice_count))
    if question_type in {"flash_card", "group_choice"}:
        if not isinstance(correct_choices, list):
            return set()
        public_labels = selected_choice_labels(question_intent)
        if public_labels is None:
            return set()
        correct_indexes = {
            index
            for index, value in enumerate(correct_choices[:choice_count])
            if value in public_labels
        }
        return correct_indexes if len(correct_indexes) == 1 else set()
    return set(range(choice_count))


def validation_errors(
    value: object,
    *,
    choice_count: int | None = None,
    allowed_choice_indexes: Iterable[int] | None = None,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        return (f"{FIELD_NAME} must be a list",)

    allowed = set(allowed_choice_indexes) if allowed_choice_indexes is not None else None
    seen_indexes: set[int] = set()
    errors: list[str] = []
    for entry_index, entry in enumerate(value):
        path = f"{FIELD_NAME}[{entry_index}]"
        if not isinstance(entry, Mapping) or set(entry) != {"choiceIndex", "items"}:
            errors.append(f"{path} must contain only choiceIndex and items")
            continue
        choice_index = entry.get("choiceIndex")
        if isinstance(choice_index, bool) or not isinstance(choice_index, int):
            errors.append(f"{path}.choiceIndex must be an integer")
            continue
        if choice_index < 0 or (
            choice_count is not None and choice_index >= choice_count
        ):
            errors.append(f"{path}.choiceIndex is outside the choices")
        if choice_index in seen_indexes:
            errors.append(f"{path}.choiceIndex is duplicated")
        seen_indexes.add(choice_index)
        if allowed is not None and choice_index not in allowed:
            errors.append(f"{path}.choiceIndex targets an isChoiceOnly document")

        items = entry.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS_PER_CHOICE:
            errors.append(
                f"{path}.items must contain 1 to {MAX_ITEMS_PER_CHOICE} entries"
            )
            continue
        seen_questions: set[str] = set()
        for item_index, item in enumerate(items):
            item_path = f"{path}.items[{item_index}]"
            if not isinstance(item, Mapping) or set(item) != {"question", "answer"}:
                errors.append(f"{item_path} must contain only question and answer")
                continue
            question = item.get("question")
            answer = item.get("answer")
            if not isinstance(question, str) or not question.strip():
                errors.append(f"{item_path}.question must be non-empty")
            if not isinstance(answer, str) or not answer.strip():
                errors.append(f"{item_path}.answer must be non-empty")
            normalized_question = question.strip() if isinstance(question, str) else ""
            if normalized_question and normalized_question in seen_questions:
                errors.append(f"{item_path}.question is duplicated in the choice")
            seen_questions.add(normalized_question)
    return tuple(errors)


def normalize(
    value: object,
    *,
    choice_count: int | None = None,
    allowed_choice_indexes: Iterable[int] | None = None,
) -> list[dict[str, Any]]:
    errors = validation_errors(
        value,
        choice_count=choice_count,
        allowed_choice_indexes=allowed_choice_indexes,
    )
    if errors:
        raise ValueError("; ".join(errors))
    return [
        {
            "choiceIndex": entry["choiceIndex"],
            "items": [
                {
                    "question": item["question"].strip(),
                    "answer": item["answer"].strip(),
                }
                for item in entry["items"]
            ],
        }
        for entry in value
    ]


def details_for_choice(value: object, choice_index: int) -> list[dict[str, str]]:
    if value is None:
        return []
    for entry in normalize(value):
        if entry["choiceIndex"] == choice_index:
            return entry["items"]
    return []
