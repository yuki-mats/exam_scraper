from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from scripts.common.aggregate_answer_decomposition import is_approved_target


QUESTION_LEVEL_TYPES = frozenset({"flash_card", "group_choice"})
CORRECT_LABELS = frozenset({"正解", "正しい"})
INCORRECT_LABELS = frozenset({"不正解", "間違い", "誤り"})
ANSWER_RESULT_PATTERN = re.compile(
    r"正解は\s*([0-9０-９]+(?:\s*,\s*[0-9０-９]+)*)\s*です。"
)
FULLWIDTH_DIGIT_TRANSLATION = str.maketrans("０１２３４５６７８９", "0123456789")
SELECTED_CHOICE_COUNT_PATTERNS = (
    re.compile(
        r"(?:次|以下|上記)のうち"
        r"(?:、)?(?:いくつ|何(?:個|件|項目|肢|つ))"
    ),
    re.compile(
        r"(?:もの|記述|項目|選択肢|肢)"
        r"(?:の(?:個数|件数|項目数|肢数|数))?"
        r"(?:は|が|を)?"
        r"(?:いくつ|何(?:個|件|項目|肢|つ))"
    ),
    re.compile(
        r"(?:いくつ|何(?:個|件|項目|肢|つ))"
        r"(?:の(?:もの|記述|項目|選択肢|肢))?"
        r"(?:が|は)"
        r"(?:正しい|誤っている|誤り|適切|不適切|該当する|該当しない)"
    ),
    re.compile(
        r"(?:もの|記述|項目|選択肢|肢)"
        r"(?:の)?(?:個数|件数|項目数|肢数|数)"
        r"(?:を答え|を選べ|を求め|は(?:いくつ|何))"
    ),
    re.compile(
        r"(?:もの|記述|項目|選択肢|肢)"
        r"(?:の)?(?:個数|件数|項目数|肢数|数)"
        r"(?:は|が|を)?(?:どれ|何(?:個|件|項目|肢|つ))"
    ),
)
COMBINATION_CHOICE_PATTERN = re.compile(r"組(?:合せ|み合わせ)")


def selected_choice_labels(question_intent: Any) -> frozenset[str] | None:
    """Return the intrinsic verdicts selected by the question instruction."""

    if question_intent == "select_correct":
        return CORRECT_LABELS
    if question_intent == "select_incorrect":
        return INCORRECT_LABELS
    return None


def selected_choice_count(
    question_intent: Any,
    correct_choices: Any,
) -> int | None:
    if not isinstance(correct_choices, Sequence) or isinstance(
        correct_choices,
        (str, bytes),
    ):
        return None
    selected_labels = selected_choice_labels(question_intent)
    if selected_labels is None:
        return None
    return sum(
        1
        for value in correct_choices
        if isinstance(value, str) and value.strip() in selected_labels
    )


def question_level_answer_cardinality_issue(
    question_type: Any,
    correct_choices: Any,
    question_intent: Any,
) -> str | None:
    """Validate the final cross-field contract without guessing which field is wrong."""

    if question_type not in QUESTION_LEVEL_TYPES:
        return None
    if selected_choice_labels(question_intent) is None:
        return (
            f"{question_type}の公開正答を確定するquestionIntentがありません。"
            "questionType、questionIntent、correctChoiceTextを再確認してください。"
        )
    count = selected_choice_count(question_intent, correct_choices)
    if count == 1:
        return None
    if count is None:
        detail = "correctChoiceTextを配列として確認できません"
    else:
        detail = f"正答が{count}件あります"
    return (
        f"{question_type}は公開時に正答を1件だけ必要としますが、{detail}。"
        "questionType、questionIntent、correctChoiceTextを再確認してください。"
    )


def parse_official_answer_numbers(value: Any) -> tuple[int, ...]:
    match = ANSWER_RESULT_PATTERN.search(
        str(value or "").translate(FULLWIDTH_DIGIT_TRANSLATION)
    )
    if match is None:
        return ()
    numbers: list[int] = []
    for part in match.group(1).split(","):
        number = int(part.strip())
        if number not in numbers:
            numbers.append(number)
    return tuple(numbers)


def asks_for_selected_choice_count(value: Any) -> bool:
    """Return whether the prompt asks for the number of matching statements."""

    text = re.sub(r"\s+", "", str(value or ""))
    return any(pattern.search(text) for pattern in SELECTED_CHOICE_COUNT_PATTERNS)


def asks_for_combination_choice(value: Any) -> bool:
    """Return whether the official number selects a combination answer."""

    return COMBINATION_CHOICE_PATTERN.search(
        re.sub(r"\s+", "", str(value or ""))
    ) is not None


def official_answer_alignment_issue(record: Any) -> str | None:
    """Detect a final cross-field mismatch without choosing which field to change."""

    if not isinstance(record, dict):
        return None
    source_text = record.get("questionBodyText")
    decomposition = record.get("aggregateAnswerDecomposition")
    if (
        isinstance(source_text, str)
        and decomposition is not None
        and is_approved_target(decomposition, source_text)
    ):
        # 元の正解番号は集約前の候補を指し、投影後の各記述indexとは比較できない。
        return None
    intent = record.get("questionIntent")
    if intent not in {"select_correct", "select_incorrect"}:
        return None
    correct_choices = record.get("correctChoiceText")
    if not isinstance(correct_choices, list) or not correct_choices:
        return None
    if any(
        value not in CORRECT_LABELS | INCORRECT_LABELS
        for value in correct_choices
    ):
        return None
    official_numbers = parse_official_answer_numbers(record.get("answer_result_text"))
    if not official_numbers:
        return None
    selected_labels = selected_choice_labels(intent)
    if selected_labels is None:
        return None
    independently_selected = tuple(
        index
        for index, value in enumerate(correct_choices, start=1)
        if value in selected_labels
    )
    if asks_for_selected_choice_count(source_text):
        if len(official_numbers) != 1:
            return (
                "正答数を問う設問ですが、公式解答を単一の数として解釈できません"
                f"（公式={list(official_numbers)}）。"
                "questionIntent、correctChoiceText、answer_result_textを再確認してください。"
                "機械検証ではどのfieldを変更するか決めません。"
            )
        official_count = official_numbers[0]
        selected_count = len(independently_selected)
        if official_count == selected_count:
            return None
        return (
            "公式解答の正答数と独立判定した該当肢数が一致しません"
            f"（公式の正答数={official_count} / 判定した該当肢数={selected_count}）。"
            "questionIntent、correctChoiceText、answer_result_textを再確認してください。"
            "機械検証ではどのfieldを変更するか決めません。"
        )
    if set(official_numbers) == set(independently_selected):
        return None
    if asks_for_combination_choice(source_text):
        return (
            "組合せを選ぶ公式解答番号と、現在の選択肢別正誤を対応付ける"
            "検証済みmappingがありません。"
            "questionIntent、correctChoiceText、answer_result_text又は"
            "組合せmappingを再確認してください。"
            "機械検証ではどのfieldを変更するか決めません。"
        )
    return (
        "公式解答と独立判定した選択肢が一致しません"
        f"（公式={list(official_numbers)} / 判定={list(independently_selected)}）。"
        "questionIntent、correctChoiceText、answer_result_textを再確認してください。"
        "機械検証ではどのfieldを変更するか決めません。"
    )
