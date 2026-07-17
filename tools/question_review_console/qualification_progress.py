from __future__ import annotations

from dataclasses import dataclass
from typing import AbstractSet, Mapping


WorkItem = tuple[str, str]


@dataclass(frozen=True)
class ProgressCompletion:
    touched_questions: frozenset[str]
    processed_questions: frozenset[str]
    validated_questions: frozenset[str]


def derive_progress_completion(
    target_ids: AbstractSet[str],
    planned_by_question: Mapping[str, AbstractSet[str]],
    processed_work_items: AbstractSet[WorkItem],
    finalized_work_items: AbstractSet[WorkItem],
    finalized_questions: AbstractSet[str],
    validated_work_items: AbstractSet[WorkItem],
    validated_finalized_questions: AbstractSet[str],
) -> ProgressCompletion:
    """Derive question states while keeping partial work separate."""

    targets = frozenset(target_ids)
    touched = frozenset(
        question_id
        for question_id, _stage_id in processed_work_items
        if question_id in targets
    )
    finalized_stages = _stages_by_question(finalized_work_items)
    validated_stages = _stages_by_question(validated_work_items)
    processed = frozenset(
        question_id
        for question_id in targets
        if _question_complete(
            set(planned_by_question.get(question_id, set())),
            finalized_stages.get(question_id, set()),
            question_id in finalized_questions,
        )
    )
    validated = frozenset(
        question_id
        for question_id in targets
        if _question_complete(
            set(planned_by_question.get(question_id, set())),
            validated_stages.get(question_id, set()),
            question_id in validated_finalized_questions,
        )
    )
    return ProgressCompletion(touched, processed, validated)


def _stages_by_question(
    work_items: AbstractSet[WorkItem],
) -> dict[str, set[str]]:
    stages: dict[str, set[str]] = {}
    for question_id, stage_id in work_items:
        stages.setdefault(question_id, set()).add(stage_id)
    return stages


def _question_complete(
    planned_stages: set[str],
    completed_stages: set[str],
    finalized: bool,
) -> bool:
    return planned_stages <= completed_stages and (
        bool(planned_stages) or finalized
    )
