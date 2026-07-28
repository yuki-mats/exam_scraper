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


def uses_trusted_gassyunin_judge_answers(record: dict[str, Any]) -> bool:
    """Return whether per-statement answers come from the trusted judge section."""

    provider = str(record.get("sourceProvider") or "").strip()
    origin = str(record.get("sourceOrigin") or "").strip()
    source_url = str(
        record.get("question_url") or record.get("sourceUrl") or ""
    ).strip()
    is_direct_source = (
        provider == "gassyunin.com" and origin == "gassyunin_site"
    )
    is_preserved_snapshot = (
        provider in {"gassyunin.com", "gassyunin.com+firestore_snapshot"}
        and origin == "firestore_snapshot"
        and source_url.startswith("https://gassyunin.com/")
    )
    if (
        not (is_direct_source or is_preserved_snapshot)
        or record.get("choiceMarkerSource") != "judge"
        or record.get("markerAlignmentMode") != "judge_only"
        or record.get("markerMismatchDetected") is not False
        or record.get("answerResultNumbersRemapped") is not False
    ):
        return False
    judge_markers = record.get("judgeChoiceMarkers")
    choices = record.get("choiceTextList")
    correct_choices = record.get("correctChoiceText")
    if not all(
        isinstance(values, list) and bool(values)
        for values in (judge_markers, choices, correct_choices)
    ):
        return False
    statement_count = record.get("sourceStatementCount")
    if not isinstance(statement_count, int) or statement_count <= 0:
        return False
    return (
        len(judge_markers)
        == len(choices)
        == len(correct_choices)
        == statement_count
        and len({str(value).strip() for value in judge_markers})
        == statement_count
        and all(str(value).strip() for value in judge_markers)
    )


def _exam_time_correct_choices_for_official_alignment(
    law_revision_facts: Any,
    *,
    choice_count: int,
) -> list[str] | None:
    """Return complete exam-time verdicts only for a verified current-law drift."""

    if isinstance(law_revision_facts, list):
        if (
            len(law_revision_facts) != choice_count
            or not all(isinstance(fact, dict) for fact in law_revision_facts)
            or not any(
                fact.get("auditStatus") == "updated_to_current_law"
                for fact in law_revision_facts
            )
        ):
            return None
        verdicts: list[str] = []
        for fact in law_revision_facts:
            exam_time = fact.get("examTime")
            verdict = (
                exam_time.get("correctChoiceText")
                if isinstance(exam_time, dict)
                else None
            )
            if verdict not in CORRECT_LABELS | INCORRECT_LABELS:
                return None
            verdicts.append(verdict)
        return verdicts

    if not isinstance(law_revision_facts, dict) or (
        law_revision_facts.get("auditStatus") != "updated_to_current_law"
    ):
        return None
    exam_time = law_revision_facts.get("examTime")
    verdicts = (
        exam_time.get("correctChoiceText")
        if isinstance(exam_time, dict)
        else None
    )
    if (
        not isinstance(verdicts, list)
        or len(verdicts) != choice_count
        or any(
            verdict not in CORRECT_LABELS | INCORRECT_LABELS
            for verdict in verdicts
        )
    ):
        return None
    return list(verdicts)


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
    exam_time_correct_choices = (
        _exam_time_correct_choices_for_official_alignment(
            record.get("lawRevisionFacts"),
            choice_count=len(correct_choices),
        )
    )
    answer_choices = exam_time_correct_choices or correct_choices
    official_numbers = parse_official_answer_numbers(record.get("answer_result_text"))
    if not official_numbers:
        return None
    selected_labels = selected_choice_labels(intent)
    if selected_labels is None:
        return None
    independently_selected = tuple(
        index
        for index, value in enumerate(answer_choices, start=1)
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
        if uses_trusted_gassyunin_judge_answers(record):
            # gassyuninの正解番号は元の組合せ肢を指す。judge sectionが各記述の
            # source truthなので、組合せmappingなしで記述indexとは比較しない。
            return None
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
