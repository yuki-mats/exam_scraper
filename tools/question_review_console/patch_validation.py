from __future__ import annotations

from typing import Any, Mapping

from tools.question_review_console.projection import normalize_verdict


PATCH_REQUIRED_FIELDS = {
    "explanation": {
        "explanationText",
        "suggestedQuestions",
        "suggestedQuestionDetails",
        "original_question_id",
        "question_url",
    },
    "correctChoice": {
        "correctChoiceText",
        "original_question_id",
        "question_url",
    },
}


def patch_entry_required_warnings(
    entry: Mapping[str, Any], stage: str
) -> list[dict[str, str]]:
    required = PATCH_REQUIRED_FIELDS.get(stage, set())
    warnings = []
    for field in sorted(required):
        if field not in entry or entry.get(field) is None:
            warnings.append(
                {"field": field, "detail": f"{stage} patchに{field}がありません。"}
            )
    for field in ("original_question_id", "question_url"):
        if field in required and not str(entry.get(field) or "").strip():
            if not any(warning["field"] == field for warning in warnings):
                warnings.append(
                    {"field": field, "detail": f"{stage} patchの{field}が空です。"}
                )
    return warnings


def projected_required_warnings(record: Mapping[str, Any]) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []

    def add(field: str, detail: str) -> None:
        warnings.append({"field": field, "detail": detail})

    if not str(record.get("questionBodyText") or "").strip():
        add("questionBodyText", "問題文がありません。")
    if not str(record.get("questionType") or "").strip():
        add("questionType", "questionTypeがありません。")

    choices = record.get("choiceTextList")
    if not isinstance(choices, list) or not choices:
        add("choiceTextList", "選択肢がありません。")
        choice_count = 0
    else:
        choice_count = len(choices)
        if any(not str(value or "").strip() for value in choices):
            add("choiceTextList", "空の選択肢があります。")

    correctness = record.get("correctChoiceText")
    if not isinstance(correctness, list) or len(correctness) != choice_count:
        add("correctChoiceText", "正誤数が選択肢数と一致しません。")
    elif any(
        normalize_verdict(value) not in {"正しい", "間違い"}
        for value in correctness
    ):
        add("correctChoiceText", "未確定又は不正な正誤があります。")

    explanations = record.get("explanationText")
    if not isinstance(explanations, list) or len(explanations) != choice_count:
        add("explanationText", "解説数が選択肢数と一致しません。")
    elif any(not str(value or "").strip() for value in explanations):
        add("explanationText", "空の解説があります。")

    return warnings
