"""Mechanical publication contract matching Repaso's FillInBlankContent DTO.

Validate only; never infer answers, change source text, or repair identities.
"""

from typing import Any


def _object(value: Any, path: str, allowed: set[str]) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: must be an object")
    if any(not isinstance(key, str) or key not in allowed for key in value):
        raise ValueError(f"{path}: unexpected field")
    return value


def _list(value: Any, path: str, maximum: int) -> list:
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise ValueError(f"{path}: must be a list of 1..{maximum} items")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}: must be nonempty text")
    return value


def _positive(value: Any, path: str) -> int:
    if type(value) is not int or not 1 <= value <= 64:
        raise ValueError(f"{path}: must be an integer 1..64")
    return value


def _unique(values: list, path: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{path}: duplicate identity")


def validate_fill_in_blank_question(question: dict, *, doc_id: str) -> None:
    path = f"questions:{doc_id} fillInBlankContent"
    if question.get("questionType") != "fill_in_blank":
        if "fillInBlankContent" in question:
            raise ValueError(f"{path}: must be omitted for other question types")
        return
    for flag in ("isChoiceOnly", "isGroupable"):
        if flag in question and question[flag] is not False:
            raise ValueError(f"questions:{doc_id} {flag}: must be false for fill_in_blank")
    validate_fill_in_blank_content(question.get("fillInBlankContent"), path=path)


def validate_fill_in_blank_content(value: Any, *, path: str = "fillInBlankContent") -> None:
    data = _object(value, path, {"schemaVersion", "segments", "blanks", "answerInput", "wordBank"})
    if type(data.get("schemaVersion")) is not int or data["schemaVersion"] != 1:
        raise ValueError(f"{path}.schemaVersion: must be integer 1")

    placements = []
    for index, value in enumerate(_list(data.get("segments"), f"{path}.segments", 512)):
        item_path = f"{path}.segments[{index}]"
        segment = _object(value, item_path, {"type", "text", "blankId", "placementId"})
        if segment.get("type") == "text":
            if "blankId" in segment or "placementId" in segment:
                raise ValueError(f"{item_path}: text segment cannot reference a blank")
            if not isinstance(segment.get("text"), str) or not segment["text"]:
                raise ValueError(f"{item_path}.text: must be nonempty passage text")
        elif segment.get("type") == "blank" and "text" not in segment:
            _text(segment.get("blankId"), f"{item_path}.blankId")
            _text(segment.get("placementId"), f"{item_path}.placementId")
            placements.append(segment)
        else:
            raise ValueError(f"{item_path}: invalid segment type or fields")

    blanks = _list(data.get("blanks"), f"{path}.blanks", 64)
    accepted_by_blank = []
    for index, value in enumerate(blanks):
        item_path = f"{path}.blanks[{index}]"
        blank = _object(value, item_path, {"blankId", "blankIndex", "correctAnswerText", "acceptedAnswerTexts"})
        _text(blank.get("blankId"), f"{item_path}.blankId")
        _positive(blank.get("blankIndex"), f"{item_path}.blankIndex")
        correct = _text(blank.get("correctAnswerText"), f"{item_path}.correctAnswerText")
        accepted = {
            _text(answer, f"{item_path}.acceptedAnswerTexts[{answer_index}]").strip()
            for answer_index, answer in enumerate(
                _list(blank.get("acceptedAnswerTexts"), f"{item_path}.acceptedAnswerTexts", 32)
            )
        }
        if correct.strip() not in accepted:
            raise ValueError(f"{item_path}.correctAnswerText: must be included in acceptedAnswerTexts")
        accepted_by_blank.append(accepted)

    input_path = f"{path}.answerInput"
    answer_input = _object(data.get("answerInput"), input_path, {"allowedMethods", "defaultMethod"})
    methods = _list(answer_input.get("allowedMethods"), f"{input_path}.allowedMethods", 2)
    supported = {"word_bank", "text_input"}
    if any(not isinstance(method, str) or method not in supported for method in methods):
        raise ValueError(f"{input_path}.allowedMethods: unsupported input method")
    _unique(methods, f"{input_path}.allowedMethods")
    if answer_input.get("defaultMethod") not in methods:
        raise ValueError(f"{input_path}.defaultMethod: must be included in allowedMethods")

    choices = []
    if data.get("wordBank") is not None:
        bank_path = f"{path}.wordBank"
        bank = _object(data["wordBank"], bank_path, {"origin", "choices"})
        origin = _text(bank.get("origin"), f"{bank_path}.origin")
        if origin not in {"original", "learning_generated", "mixed"}:
            raise ValueError(f"{bank_path}.origin: unsupported origin")
        choices = _list(bank.get("choices"), f"{bank_path}.choices", 128)
        for index, value in enumerate(choices):
            item_path = f"{bank_path}.choices[{index}]"
            choice = _object(value, item_path, {"choiceId", "text", "maxUses"})
            _text(choice.get("choiceId"), f"{item_path}.choiceId")
            _text(choice.get("text"), f"{item_path}.text")
            _positive(choice.get("maxUses"), f"{item_path}.maxUses")

    _unique([blank["blankId"] for blank in blanks], f"{path}.blanks.blankId")
    _unique([blank["blankIndex"] for blank in blanks], f"{path}.blanks.blankIndex")
    _unique([placement["placementId"] for placement in placements], f"{path}.segments.placementId")
    blank_ids = {blank["blankId"] for blank in blanks}
    referenced_ids = {placement["blankId"] for placement in placements}
    if blank_ids != referenced_ids:
        raise ValueError(f"{path}.segments.blankId: references must match defined blanks")
    _unique([choice["choiceId"] for choice in choices], f"{path}.wordBank.choices.choiceId")

    if "word_bank" in methods and not _can_complete_word_bank(accepted_by_blank, choices):
        raise ValueError(f"{path}.wordBank: cannot answer all blanks within maxUses")


def _can_complete_word_bank(accepted_by_blank: list[set[str]], choices: list[dict]) -> bool:
    # Capacity-aware matching: shared alternative answers must still allow a
    # simultaneous assignment. Repeated placements do not consume extra uses.
    tokens = [choice["text"].strip() for choice in choices for _ in range(choice["maxUses"])]
    matched: dict[int, int] = {}

    def assign(blank_index: int, seen: set[int]) -> bool:
        for token_index, text in enumerate(tokens):
            if text not in accepted_by_blank[blank_index] or token_index in seen:
                continue
            seen.add(token_index)
            if token_index not in matched or assign(matched[token_index], seen):
                matched[token_index] = blank_index
                return True
        return False

    return all(assign(index, set()) for index in range(len(accepted_by_blank)))
