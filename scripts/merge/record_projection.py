from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from scripts.common.question_identity import (
    IdentityCandidateIndex,
    review_question_id,
)
from scripts.merge.patch_views import (
    PatchArtifactEntry,
    apply_answer_result_overrides,
    apply_correct_choice,
    apply_explanation_fields,
    apply_law_context_fields,
    apply_originalized_fields,
    apply_question_intent,
    apply_question_set,
    apply_question_type,
    ensure_originalized_explanation_is_distinct,
    ensure_identity_candidate_index_valid,
    normalize_true_false_intent_and_correct_choice,
)
from scripts.merge.question_issue_corrections import (
    QuestionIssueCorrectionEntry,
    apply_question_issue_correction_entry,
    question_issue_correction_target,
)


JAPANESE_ERA_START_YEAR = {
    "令和": 2019,
    "平成": 1989,
    "昭和": 1926,
    "大正": 1912,
    "明治": 1868,
}
FULLWIDTH_DIGIT_TRANS = str.maketrans("０１２３４５６７８９", "0123456789")
ANSWER_RESULT_RE = re.compile(
    r"正解は\s*([1-9０-９]+(?:\s*,\s*[1-9０-９]+)*)\s*です。"
)


@dataclass(frozen=True)
class RecordMergeProjection:
    merged1: dict[str, Any]
    merged2: dict[str, Any]
    applied_paths: tuple[Path, ...]
    update_counts: Mapping[str, int]
    applied_question_issue_targets: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


def ensure_projection_indexes_valid(
    indexes: Sequence[tuple[str, IdentityCandidateIndex]],
) -> None:
    """Apply physical Merge's fail-closed artifact checks to a projection."""

    for label, index in indexes:
        ensure_identity_candidate_index_valid(index, label=label)


def _normalize_digit_text(value: str) -> str:
    return (value or "").translate(FULLWIDTH_DIGIT_TRANS)


def _parse_japanese_era_year(era_name: str, year_token: str) -> int | None:
    base_year = JAPANESE_ERA_START_YEAR.get(era_name)
    if base_year is None:
        return None
    normalized_year = _normalize_digit_text(year_token).strip()
    if normalized_year == "元":
        era_year = 1
    elif normalized_year.isdigit():
        era_year = int(normalized_year)
    else:
        return None
    return base_year + era_year - 1 if era_year > 0 else None


def infer_exam_year_from_label(exam_label: str) -> int | None:
    if not exam_label:
        return None
    normalized_label = _normalize_digit_text(exam_label)
    western_year = re.search(
        r"[（(]\s*((?:19|20)\d{2})\s*年\s*[)）]", normalized_label
    ) or re.search(r"((?:19|20)\d{2})\s*年(?:度)?", normalized_label)
    if western_year:
        return int(western_year.group(1))
    japanese_year = re.search(
        r"(令和|平成|昭和|大正|明治)\s*(元|[0-9０-９]+)\s*年(?:度)?",
        normalized_label,
    )
    if japanese_year:
        return _parse_japanese_era_year(
            japanese_year.group(1), japanese_year.group(2)
        )
    return None


def backfill_exam_year(data: dict[str, Any]) -> int:
    updated = 0
    for body in data.get("question_bodies") or []:
        if not isinstance(body, dict) or body.get("examYear") not in (None, ""):
            continue
        label = body.get("examLabel")
        inferred = infer_exam_year_from_label(label) if isinstance(label, str) else None
        if inferred is not None:
            body["examYear"] = inferred
            updated += 1
    return updated


def _parse_answer_numbers(answer_result_text: str) -> list[int]:
    match = ANSWER_RESULT_RE.search(_normalize_digit_text(answer_result_text))
    if not match:
        return []
    return list(
        dict.fromkeys(
            int(part.strip())
            for part in match.group(1).split(",")
            if part.strip().isdigit()
        )
    )


def backfill_correct_choice_text_from_answer_result(data: dict[str, Any]) -> int:
    updated = 0
    for body in data.get("question_bodies") or []:
        if not isinstance(body, dict):
            continue
        current = body.get("correctChoiceText")
        choices = body.get("choiceTextList")
        if not (
            isinstance(current, list)
            and any(value is None for value in current)
            and isinstance(choices, list)
            and choices
        ):
            continue
        body_text = str(
            body.get("questionBodyText")
            or body.get("originalQuestionBodyText")
            or ""
        )
        if "いくつ" in body_text:
            continue
        answer_numbers = _parse_answer_numbers(
            str(body.get("answer_result_text") or "")
        )
        if not answer_numbers:
            inferred = body.get("answer_result_inferred_correct_choice_numbers")
            if isinstance(inferred, list):
                answer_numbers = list(
                    dict.fromkeys(
                        int(value)
                        for value in inferred
                        if isinstance(value, int) or str(value).isdigit()
                    )
                )
        if not answer_numbers or any(
            number < 1 or number > len(choices) for number in answer_numbers
        ):
            continue
        intent = str(body.get("questionIntent") or "").strip()
        if intent == "select_incorrect":
            labels = ["正しい"] * len(choices)
            replacement = "間違い"
        elif intent == "select_correct":
            labels = ["間違い"] * len(choices)
            replacement = "正しい"
        else:
            continue
        for number in answer_numbers:
            labels[number - 1] = replacement
        body["correctChoiceText"] = labels
        updated += 1
    return updated


def _apply_candidates(
    data: dict[str, Any],
    candidates: Sequence[PatchArtifactEntry],
    apply_patch: Callable[[dict[str, Any], Mapping[str, Any]], int],
    *,
    value_key: str | None = None,
    apply_empty_map_when_missing: bool = False,
) -> int:
    questions = data.get("question_bodies")
    if not isinstance(questions, list) or len(questions) != 1:
        raise ValueError("record projection requires exactly one question")
    if not candidates and apply_empty_map_when_missing:
        return apply_patch(data, {})
    updated = 0
    for candidate in candidates:
        question_id = review_question_id(questions[0])
        if not question_id:
            raise RuntimeError("patch適用中にreviewQuestionIdを取得できません。")
        value = candidate.entry if value_key is None else candidate.entry.get(value_key)
        updated += apply_patch(data, {question_id: value})
    return updated


def project_merge_record(
    source_record: Mapping[str, Any],
    *,
    originalized: Sequence[PatchArtifactEntry] = (),
    question_type: Sequence[PatchArtifactEntry] = (),
    intent_fallback: Sequence[PatchArtifactEntry] = (),
    strict_correct: Sequence[PatchArtifactEntry] = (),
    law_context: Sequence[PatchArtifactEntry] = (),
    explanation: Sequence[PatchArtifactEntry] = (),
    question_set: Sequence[PatchArtifactEntry] = (),
    question_issues: Sequence[QuestionIssueCorrectionEntry] = (),
) -> RecordMergeProjection:
    if originalized and explanation:
        ensure_originalized_explanation_is_distinct(
            source_record,
            explanation,
        )
    merged1 = {"question_bodies": [copy.deepcopy(dict(source_record))]}
    counts: dict[str, int] = {}
    counts["originalized"] = _apply_candidates(
        merged1,
        originalized,
        apply_originalized_fields,
    )
    counts["question_type"] = _apply_candidates(
        merged1,
        question_type,
        apply_question_type,
        apply_empty_map_when_missing=True,
    )
    counts["answer_result_override"] = _apply_candidates(
        merged1, intent_fallback, apply_answer_result_overrides
    )
    counts["strict_answer_result_override"] = _apply_candidates(
        merged1, strict_correct, apply_answer_result_overrides
    )
    counts["question_intent"] = _apply_candidates(
        merged1,
        intent_fallback,
        apply_question_intent,
        value_key="questionIntent",
    )
    counts["law_context"] = _apply_candidates(
        merged1, law_context, apply_law_context_fields
    )
    (
        counts["true_false_intent"],
        counts["true_false_correct_choice"],
    ) = normalize_true_false_intent_and_correct_choice(merged1)
    counts["exam_year"] = backfill_exam_year(merged1)
    counts["correct_choice_backfill"] = (
        backfill_correct_choice_text_from_answer_result(merged1)
    )
    counts["strict_correct_choice"] = _apply_candidates(
        merged1,
        strict_correct,
        apply_correct_choice,
        value_key="correctChoiceText",
    )

    merged2 = copy.deepcopy(merged1)
    counts["explanation"] = _apply_candidates(
        merged2, explanation, apply_explanation_fields
    )
    counts["question_set"] = _apply_candidates(
        merged2, question_set, apply_question_set
    )
    preferred = strict_correct or intent_fallback
    counts["answer_result"] = _apply_candidates(
        merged2, preferred, apply_answer_result_overrides
    )
    counts["question_intent_merged2"] = _apply_candidates(
        merged2,
        intent_fallback,
        apply_question_intent,
        value_key="questionIntent",
    )
    (
        counts["true_false_intent_merged2"],
        counts["true_false_correct_choice_merged2"],
    ) = normalize_true_false_intent_and_correct_choice(merged2)
    counts["exam_year_merged2"] = backfill_exam_year(merged2)
    counts["correct_choice_backfill_merged2"] = (
        backfill_correct_choice_text_from_answer_result(merged2)
    )
    counts["correct_choice"] = _apply_candidates(
        merged2,
        preferred,
        apply_correct_choice,
        value_key="correctChoiceText",
    )

    issue_base = copy.deepcopy(merged2)
    applied_issue_targets: list[str] = []
    errors: list[str] = []
    issue_updates = 0
    for patch in question_issues:
        try:
            changed = apply_question_issue_correction_entry(
                merged2["question_bodies"][0], patch.entry, patch.path
            )
        except (RuntimeError, ValueError) as exc:
            errors.append(str(exc))
            merged2 = issue_base
            applied_issue_targets = []
            issue_updates = 0
            break
        applied_issue_targets.append(
            question_issue_correction_target(patch.path, patch.entry)
        )
        issue_updates += int(changed)
    counts["question_issue"] = issue_updates

    used_candidates = (
        *originalized,
        *question_type,
        *intent_fallback,
        *strict_correct,
        *law_context,
        *explanation,
        *question_set,
        *(question_issues if not errors else ()),
    )
    return RecordMergeProjection(
        merged1=merged1["question_bodies"][0],
        merged2=merged2["question_bodies"][0],
        applied_paths=tuple(dict.fromkeys(candidate.path for candidate in used_candidates)),
        update_counts=counts,
        applied_question_issue_targets=tuple(applied_issue_targets),
        errors=tuple(errors),
    )
