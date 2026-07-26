from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from scripts.common.question_identity import SourceIdentityBinding


WORK_ITEM_STATES = {
    "queued",
    "preparing",
    "prepared",
    "committing",
    "validated",
    "not_applicable",
    "blocked",
}


class QuestionWorkQueueError(ValueError):
    pass


@dataclass(frozen=True)
class QuestionPlanIndex:
    """Immutable lookup tables for repeatedly scoping one qualification plan."""

    targets_by_id: Mapping[str, Mapping[str, Any]]
    alias_groups_by_id: Mapping[str, tuple[str, ...]]
    bindings_by_id: Mapping[str, tuple[Mapping[str, Any], ...]]
    source_scope_paths_by_group: Mapping[tuple[str, ...], tuple[str, ...]]
    source_scope_path_order: Mapping[str, int]
    record_scope_paths_by_group: Mapping[tuple[str, ...], tuple[str, ...]]
    record_scope_path_order: Mapping[str, int]
    source_files: frozenset[str]
    output_files: frozenset[str]
    allowed_patch_files: frozenset[str]
    allowed_write_files: frozenset[str]
    policy_targets: Mapping[str, frozenset[str]]


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _target_id(target: Mapping[str, Any]) -> str:
    return str(
        target.get("id")
        or target.get("uiQuestionId")
        or target.get("questionKey")
        or ""
    ).strip()


def _target_aliases(target: Mapping[str, Any]) -> set[str]:
    identity = SourceIdentityBinding.from_mapping(target)
    return {
        str(value).strip()
        for value in [
            _target_id(target),
            target.get("questionKey"),
            *(target.get("aliases") or []),
            *identity.as_tuple(),
        ]
        if str(value or "").strip()
    }


def _natural_key(value: str) -> tuple[tuple[int, int | str], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", value)
        if part
    )


def work_item_key(target: Mapping[str, Any], stage_id: str) -> str:
    identity = SourceIdentityBinding.from_mapping(target)
    if not identity.is_complete():
        raise QuestionWorkQueueError(
            "一問work itemのsourceQuestionKey/reviewQuestionId/"
            "sourceRecordRefを一意に特定できません。"
        )
    normalized_stage = str(stage_id).strip()
    if not normalized_stage:
        raise QuestionWorkQueueError("一問work itemの工程IDがありません。")
    return _canonical_hash(
        {
            "identity": identity.as_mapping(),
            "stageId": normalized_stage,
        }
    )[:24]


def input_fingerprint(
    target: Mapping[str, Any],
    stage_id: str,
    policy_fingerprint: str,
    update_target_ids: Iterable[str] = (),
) -> str:
    identity = SourceIdentityBinding.from_mapping(target)
    return _canonical_hash(
        {
            "identity": identity.as_mapping(),
            "stageId": str(stage_id),
            "stateHash": str(target.get("stateHash") or ""),
            "policyFingerprint": str(policy_fingerprint or ""),
            "updateTargetIds": sorted(
                str(value)
                for value in update_target_ids
                if str(value).startswith(f"{stage_id}.")
            ),
        }
    )


def _stage_plans(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = plan.get("stagePlans")
    if isinstance(raw, list) and raw:
        return [dict(value) for value in raw if isinstance(value, Mapping)]
    return [dict(plan)]


def _derive_question_status(stages: Iterable[Mapping[str, Any]]) -> str:
    states = [str(stage.get("status") or "queued") for stage in stages]
    if any(state == "blocked" for state in states):
        return "blocked"
    if states and all(
        state in {"validated", "not_applicable"} for state in states
    ):
        return "validated"
    for state in ("committing", "prepared", "preparing"):
        if state in states:
            return state
    return "queued"


def refresh_question_status(execution: dict[str, Any]) -> dict[str, Any]:
    execution["status"] = _derive_question_status(execution.get("stages") or [])
    return execution


def build_question_executions(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    stage_plans = [
        stage_plan
        for stage_plan in _stage_plans(plan)
        if str(stage_plan.get("stageId") or "").strip()
        not in {"", "multi", "category_setup", "setup"}
    ]
    canonical_targets: dict[str, dict[str, Any]] = {}
    canonical_priorities: dict[str, tuple[int, int]] = {}
    targets_by_stage: dict[str, dict[str, dict[str, Any]]] = {}
    for stage_index, stage_plan in enumerate(stage_plans):
        stage_id = str(stage_plan["stageId"])
        stage_targets = targets_by_stage.setdefault(stage_id, {})
        target_priority = (
            len(stage_plan.get("progressTargets") or []),
            stage_index,
        )
        for raw_target in stage_plan.get("progressTargets") or []:
            if not isinstance(raw_target, Mapping):
                continue
            target = dict(raw_target)
            question_id = _target_id(target)
            if not question_id:
                raise QuestionWorkQueueError("一問work itemの問題IDがありません。")
            existing = canonical_targets.get(question_id)
            if existing is not None and SourceIdentityBinding.from_mapping(
                existing
            ) != SourceIdentityBinding.from_mapping(target):
                raise QuestionWorkQueueError(
                    f"問題IDに複数のsource identityがあります: {question_id}"
                )
            if target_priority >= canonical_priorities.get(question_id, (-1, -1)):
                canonical_targets[question_id] = target
                canonical_priorities[question_id] = target_priority
            if question_id in stage_targets:
                raise QuestionWorkQueueError(
                    f"一問work itemが重複しています: {question_id} / {stage_id}"
                )
            stage_targets[question_id] = target

    executions: list[dict[str, Any]] = []
    seen_work_items: set[str] = set()
    resume_work_item_keys = {
        str(value)
        for value in plan.get("resumeWorkItemKeys") or []
        if value
    }
    retry_model_work_item_keys = {
        str(value)
        for value in plan.get("retryModelWorkItemKeys") or []
        if value
    }
    retry_feedback_by_work_item = {
        str(key): [
            dict(feedback)
            for feedback in value
            if isinstance(feedback, Mapping)
        ]
        for key, value in (plan.get("retryFeedbackByWorkItem") or {}).items()
        if isinstance(value, list)
    }
    group_order = {
        str(group_id): index
        for index, group_id in enumerate(plan.get("scopeListGroupIds") or [])
    }
    ordered_targets = sorted(
        canonical_targets.items(),
        key=lambda item: (
            group_order.get(str(item[1].get("listGroupId") or ""), len(group_order)),
            int(item[1].get("displayOrder") or 0),
            _natural_key(
                SourceIdentityBinding.from_mapping(item[1]).source_record_ref
            ),
            item[0],
        ),
    )
    for display_order, (question_id, canonical_target) in enumerate(
        ordered_targets,
        start=1,
    ):
        identity = SourceIdentityBinding.from_mapping(canonical_target)
        first_targeted_stage = next(
            index
            for index, stage_plan in enumerate(stage_plans)
            if question_id
            in targets_by_stage.get(str(stage_plan["stageId"]), {})
        )
        execution = {
            "questionId": question_id,
            "questionKey": str(
                canonical_target.get("questionKey") or question_id
            ),
            "sourceQuestionKey": identity.source_question_key,
            "reviewQuestionId": identity.review_question_id,
            "sourceRecordRef": identity.source_record_ref,
            "listGroupId": str(canonical_target.get("listGroupId") or ""),
            "displayLabel": str(
                canonical_target.get("displayLabel")
                or canonical_target.get("questionLabel")
                or question_id
            ),
            "displayOrder": display_order,
            "status": "queued",
            "stages": [],
        }
        for stage_plan in stage_plans[first_targeted_stage:]:
            stage_id = str(stage_plan["stageId"])
            target = targets_by_stage.get(stage_id, {}).get(
                question_id,
                canonical_target,
            )
            item_key = work_item_key(target, stage_id)
            if resume_work_item_keys and item_key not in resume_work_item_keys:
                continue
            if item_key in seen_work_items:
                raise QuestionWorkQueueError(
                    f"一問work itemが重複しています: {question_id} / {stage_id}"
                )
            seen_work_items.add(item_key)
            policy_fingerprint = str(
                (stage_plan.get("policyFingerprints") or {}).get(stage_id) or ""
            )
            execution["stages"].append(
                {
                    "workItemKey": item_key,
                    "stageId": stage_id,
                    "stageCode": str(stage_plan.get("stageCode") or ""),
                    "stageLabel": str(stage_plan.get("stageLabel") or stage_id),
                    "policyFingerprint": policy_fingerprint,
                    "status": "queued",
                    "retryModelRequired": item_key in retry_model_work_item_keys,
                    "priorValidationFeedback": copy.deepcopy(
                        retry_feedback_by_work_item.get(item_key, [])
                    ),
                    "attempts": 0,
                    "inputFingerprint": input_fingerprint(
                        target,
                        stage_id,
                        policy_fingerprint,
                        stage_plan.get("selectedUpdateTargetIds") or [],
                    ),
                    "outputFingerprint": None,
                    "preparationPath": None,
                    "preparationHash": None,
                    "preparationThreadId": None,
                    "preparationSessionId": None,
                    "preparationTurnId": None,
                    "projectedInputPath": None,
                    "projectedInputHash": None,
                    "childRunIds": [],
                    "error": None,
                    "startedAt": None,
                    "finishedAt": None,
                }
            )
        if execution["stages"]:
            executions.append(refresh_question_status(execution))
    return executions


def queue_summary(executions: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    questions = [value for value in executions if isinstance(value, Mapping)]
    stages = [
        stage
        for question in questions
        for stage in question.get("stages") or []
        if isinstance(stage, Mapping)
    ]
    validated_work_items = sum(
        str(stage.get("status") or "") == "validated" for stage in stages
    )
    not_applicable_work_items = sum(
        str(stage.get("status") or "") == "not_applicable" for stage in stages
    )
    blocked_work_items = sum(
        str(stage.get("status") or "") == "blocked" for stage in stages
    )
    return {
        "questionCount": len(questions),
        "validatedQuestionCount": sum(
            str(question.get("status") or "") == "validated"
            for question in questions
        ),
        "blockedQuestionCount": sum(
            str(question.get("status") or "") == "blocked"
            for question in questions
        ),
        "workItemCount": len(stages),
        "validatedWorkItemCount": validated_work_items,
        "notApplicableWorkItemCount": not_applicable_work_items,
        "completedWorkItemCount": (
            validated_work_items + not_applicable_work_items
        ),
        "blockedWorkItemCount": blocked_work_items,
        "pendingWorkItemCount": (
            len(stages)
            - validated_work_items
            - not_applicable_work_items
            - blocked_work_items
        ),
        "preparingWorkItemCount": sum(
            str(stage.get("status") or "") == "preparing" for stage in stages
        ),
        "preparedWorkItemCount": sum(
            str(stage.get("status") or "") == "prepared" for stage in stages
        ),
        "committingWorkItemCount": sum(
            str(stage.get("status") or "") == "committing" for stage in stages
        ),
    }


def recover_interrupted_executions(
    executions: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    recovered = copy.deepcopy(list(executions))
    for question in recovered:
        stages = question.get("stages") or []
        blocked_from: tuple[int, str, str] | None = None
        for index, stage in enumerate(stages):
            status = str(stage.get("status") or "queued")
            if status == "preparing":
                stage.update(
                    status="queued",
                    preparationPath=None,
                    preparationHash=None,
                    error=None,
                )
            elif status in {"prepared", "committing"}:
                reason = (
                    "ローカルUIの再起動で未確定作業が中断されました。"
                    "この問題だけを再実行できます。"
                )
                stage.update(
                    status="blocked",
                    error=reason,
                )
                blocked_from = (
                    index,
                    str(stage.get("stageId") or ""),
                    reason,
                )
            if stage.get("status") not in WORK_ITEM_STATES:
                raise QuestionWorkQueueError(
                    f"未定義の一問work item状態です: {stage.get('status')}"
                )
        if blocked_from is not None:
            index, stage_id, reason = blocked_from
            for dependent in stages[index + 1 :]:
                if str(dependent.get("status") or "") == "validated":
                    continue
                dependent.update(
                    status="blocked",
                    error=f"前工程 {stage_id} の停止により保留: {reason}",
                )
        refresh_question_status(question)
    return recovered




def build_question_plan_index(plan: Mapping[str, Any]) -> QuestionPlanIndex:
    """Build the immutable lookups used while preparing every question."""

    targets_by_id: dict[str, Mapping[str, Any]] = {}
    for raw_target in plan.get("progressTargets") or []:
        if not isinstance(raw_target, Mapping):
            continue
        question_id = _target_id(raw_target)
        if not question_id:
            continue
        if question_id in targets_by_id:
            raise QuestionWorkQueueError(
                f"一問queueの対象問題が重複しています: {question_id}"
            )
        targets_by_id[question_id] = raw_target

    normalized_groups = [
        tuple(sorted({str(value) for value in group if value}))
        for group in plan.get("targetRecordAliasGroups") or []
        if isinstance(group, (list, tuple, set))
    ]
    group_indexes_by_alias: dict[str, list[int]] = {}
    for group_index, group in enumerate(normalized_groups):
        for alias in group:
            group_indexes_by_alias.setdefault(alias, []).append(group_index)

    alias_groups_by_id: dict[str, tuple[str, ...]] = {}
    for question_id, target in targets_by_id.items():
        aliases = _target_aliases(target)
        source_ref = str(target.get("sourceRecordRef") or "")
        if source_ref:
            candidate_indexes = list(group_indexes_by_alias.get(source_ref, []))
        else:
            candidate_indexes = sorted(
                {
                    group_index
                    for alias in aliases
                    for group_index in group_indexes_by_alias.get(alias, [])
                }
            )
        candidates = [
            normalized_groups[group_index] for group_index in candidate_indexes
        ]
        if not candidates:
            candidates = [tuple(sorted(aliases))]
        scores = [
            (len(aliases & set(group)), group)
            for group in candidates
        ]
        best = max((score for score, _group in scores), default=0)
        strongest = [group for score, group in scores if score == best]
        if len(strongest) != 1 or not strongest[0]:
            raise QuestionWorkQueueError(
                "対象問題のrecord scopeを一意に特定できません: "
                f"{question_id}"
            )
        alias_groups_by_id[question_id] = strongest[0]

    bindings_by_id: dict[str, list[Mapping[str, Any]]] = {}
    for raw_binding in plan.get("targetRecordBindings") or []:
        if not isinstance(raw_binding, Mapping):
            continue
        question_id = str(raw_binding.get("uiQuestionId") or "")
        if question_id:
            bindings_by_id.setdefault(question_id, []).append(raw_binding)

    def scope_index(
        raw_scopes: Any,
    ) -> tuple[dict[tuple[str, ...], tuple[str, ...]], dict[str, int]]:
        paths_by_group: dict[tuple[str, ...], list[str]] = {}
        path_order: dict[str, int] = {}
        if not isinstance(raw_scopes, Mapping):
            return {}, {}
        for path_index, (raw_path, raw_groups) in enumerate(raw_scopes.items()):
            path = str(raw_path)
            path_order.setdefault(path, path_index)
            if not isinstance(raw_groups, (list, tuple)):
                continue
            for raw_group in raw_groups:
                if not isinstance(raw_group, (list, tuple, set)):
                    continue
                group = tuple(
                    sorted({str(alias) for alias in raw_group if alias})
                )
                if group:
                    paths_by_group.setdefault(group, []).append(path)
        return (
            {
                group: tuple(dict.fromkeys(paths))
                for group, paths in paths_by_group.items()
            },
            path_order,
        )

    source_paths_by_group, source_path_order = scope_index(
        plan.get("targetSourceRecordScopes")
    )
    record_paths_by_group, record_path_order = scope_index(
        plan.get("targetRecordScopes")
    )
    return QuestionPlanIndex(
        targets_by_id=targets_by_id,
        alias_groups_by_id=alias_groups_by_id,
        bindings_by_id={
            question_id: tuple(bindings)
            for question_id, bindings in bindings_by_id.items()
        },
        source_scope_paths_by_group=source_paths_by_group,
        source_scope_path_order=source_path_order,
        record_scope_paths_by_group=record_paths_by_group,
        record_scope_path_order=record_path_order,
        source_files=frozenset(
            str(path) for path in plan.get("sourceFiles") or []
        ),
        output_files=frozenset(
            str(path) for path in plan.get("outputFiles") or []
        ),
        allowed_patch_files=frozenset(
            str(path) for path in plan.get("allowedPatchFiles") or []
        ),
        allowed_write_files=frozenset(
            str(path) for path in plan.get("allowedWriteFiles") or []
        ),
        policy_targets={
            str(stage_id): frozenset(str(value) for value in raw_targets or [])
            for stage_id, raw_targets in (plan.get("policyTargets") or {}).items()
        },
    )


def _scopes_from_index(
    paths_by_group: Mapping[tuple[str, ...], tuple[str, ...]],
    path_order: Mapping[str, int],
    selected_groups: list[tuple[str, ...]],
) -> dict[str, list[list[str]]]:
    groups_by_path: dict[str, list[list[str]]] = {}
    for group in dict.fromkeys(selected_groups):
        for path in paths_by_group.get(group, ()):
            groups_by_path.setdefault(path, []).append(list(group))
    return {
        path: groups_by_path[path]
        for path in sorted(
            groups_by_path,
            key=lambda value: (path_order.get(value, len(path_order)), value),
        )
    }


def subset_question_plan(
    plan: Mapping[str, Any],
    question_ids: Iterable[str],
    *,
    index: QuestionPlanIndex | None = None,
) -> dict[str, Any]:
    selected_ids = {str(value) for value in question_ids if str(value)}
    if not selected_ids:
        raise QuestionWorkQueueError("一問queueの対象問題がありません。")
    plan_index = index or build_question_plan_index(plan)
    candidate = dict(plan)
    targets = [
        copy.deepcopy(dict(target))
        for question_id, target in plan_index.targets_by_id.items()
        if question_id in selected_ids
    ]
    resolved_ids = {_target_id(target) for target in targets}
    missing = selected_ids - resolved_ids
    if missing:
        raise QuestionWorkQueueError(
            "一問queueの対象を実行planから解決できません: "
            + ", ".join(sorted(missing))
        )
    groups = [
        plan_index.alias_groups_by_id[_target_id(target)]
        for target in targets
    ]
    aliases = {value for group in groups for value in group}
    bindings = [
        copy.deepcopy(dict(binding))
        for question_id in [_target_id(target) for target in targets]
        for binding in plan_index.bindings_by_id.get(question_id, ())
    ]
    if len(bindings) != len(targets):
        raise QuestionWorkQueueError("一問queueのsource identity bindingが不足しています。")
    source_scopes = _scopes_from_index(
        plan_index.source_scope_paths_by_group,
        plan_index.source_scope_path_order,
        groups,
    )
    record_scopes = _scopes_from_index(
        plan_index.record_scope_paths_by_group,
        plan_index.record_scope_path_order,
        groups,
    )
    scoped_paths = set(record_scopes)
    source_paths = set(source_scopes)
    target_group_ids = list(
        dict.fromkeys(
            str(target.get("listGroupId") or "")
            for target in targets
            if target.get("listGroupId")
        )
    )
    candidate.update(
        targetCount=len(targets),
        workItemCount=len(targets),
        targetGroupIds=target_group_ids,
        targetQuestionKeys=[_target_id(target) for target in targets],
        progressTargets=targets,
        targetRecordBindings=bindings,
        targetRecordAliasGroups=[list(group) for group in groups],
        targetRecordAliases=sorted(aliases),
        targetSourceRecordScopes=source_scopes,
        targetRecordScopes=record_scopes,
        sourceFiles=[
            path
            for path in sorted(
                source_paths,
                key=lambda value: (
                    plan_index.source_scope_path_order.get(
                        value,
                        len(plan_index.source_scope_path_order),
                    ),
                    value,
                ),
            )
            if path in plan_index.source_files
        ],
        outputFiles=[
            path
            for path in sorted(
                scoped_paths,
                key=lambda value: (
                    plan_index.record_scope_path_order.get(
                        value,
                        len(plan_index.record_scope_path_order),
                    ),
                    value,
                ),
            )
            if path in plan_index.output_files
        ],
        allowedPatchFiles=[
            path
            for path in sorted(
                scoped_paths,
                key=lambda value: (
                    plan_index.record_scope_path_order.get(
                        value,
                        len(plan_index.record_scope_path_order),
                    ),
                    value,
                ),
            )
            if path in plan_index.allowed_patch_files
        ],
        allowedWriteFiles=[
            path
            for path in sorted(
                scoped_paths,
                key=lambda value: (
                    plan_index.record_scope_path_order.get(
                        value,
                        len(plan_index.record_scope_path_order),
                    ),
                    value,
                ),
            )
            if path in plan_index.allowed_write_files
        ],
        policyTargets={
            str(stage_id): [
                _target_id(target)
                for target in targets
                if _target_id(target)
                in allowed_targets
            ]
            for stage_id, allowed_targets in plan_index.policy_targets.items()
        },
    )
    allowed_paths = {
        *candidate.get("allowedPatchFiles", []),
        *candidate.get("allowedWriteFiles", []),
    }
    candidate["resolvableFailedDeltaPaths"] = [
        str(path)
        for path in plan.get("resolvableFailedDeltaPaths") or []
        if str(path) in allowed_paths
    ]
    return candidate


def specialize_question_plan(
    plan: Mapping[str, Any],
    question_id: str,
    *,
    index: QuestionPlanIndex | None = None,
) -> dict[str, Any]:
    candidate = subset_question_plan(plan, [question_id], index=index)
    stage_id = str(candidate.get("stageId") or "")
    candidate["stageIds"] = [stage_id]
    candidate.pop("stagePlans", None)
    return candidate


def resume_plan(
    plan: Mapping[str, Any],
    previous_executions: Iterable[Mapping[str, Any]],
    *,
    unfinished_only: bool = False,
) -> dict[str, Any]:
    previous_execution_list = [
        dict(question)
        for question in previous_executions
        if isinstance(question, Mapping)
    ]
    previous_items = {
        str(stage.get("workItemKey") or ""): dict(stage)
        for question in previous_execution_list
        for stage in question.get("stages") or []
        if isinstance(stage, Mapping)
        and stage.get("workItemKey")
    }
    if not previous_items:
        raise QuestionWorkQueueError("再実行元の一問work itemがありません。")
    previous_question_ids = {
        str(question.get("questionId") or "").strip()
        for question in previous_execution_list
        if str(question.get("questionId") or "").strip()
    }
    if not previous_question_ids:
        raise QuestionWorkQueueError("再実行元の対象問題IDがありません。")

    raw_stage_plans = _stage_plans(plan)
    question_stage_plans = [
        stage_plan
        for stage_plan in raw_stage_plans
        if str(stage_plan.get("stageId") or "").strip()
        not in {"", "multi", "category_setup", "setup"}
    ]
    targets_by_stage: dict[str, dict[str, dict[str, Any]]] = {}
    canonical_targets: dict[str, dict[str, Any]] = {}
    for stage_plan in question_stage_plans:
        stage_id = str(stage_plan.get("stageId") or "")
        stage_targets = targets_by_stage.setdefault(stage_id, {})
        for raw_target in stage_plan.get("progressTargets") or []:
            if not isinstance(raw_target, Mapping):
                continue
            target = dict(raw_target)
            question_id = _target_id(target)
            if not question_id or question_id not in previous_question_ids:
                continue
            stage_targets[question_id] = target
            existing = canonical_targets.setdefault(question_id, target)
            if SourceIdentityBinding.from_mapping(
                existing
            ) != SourceIdentityBinding.from_mapping(target):
                raise QuestionWorkQueueError(
                    f"問題IDに複数のsource identityがあります: {question_id}"
                )

    resume_start_by_question: dict[str, int] = {}
    for question_id, canonical_target in canonical_targets.items():
        for stage_index, stage_plan in enumerate(question_stage_plans):
            stage_id = str(stage_plan.get("stageId") or "")
            target = targets_by_stage.get(stage_id, {}).get(
                question_id,
                canonical_target,
            )
            previous = previous_items.get(work_item_key(target, stage_id))
            current_target_exists = question_id in targets_by_stage.get(stage_id, {})
            if previous is None:
                # 再開は直前runに実在した未完了itemだけを対象にする。
                # 新たに検出された対象は通常runで扱い、再開範囲を広げない。
                needs_resume = False
            else:
                previous_status = str(previous.get("status") or "")
                if unfinished_only and previous_status in {
                    "validated",
                    "not_applicable",
                }:
                    needs_resume = False
                elif previous_status == "validated":
                    previous_policy = previous.get("policyFingerprint")
                    if previous_policy is None:
                        needs_resume = current_target_exists
                    else:
                        current_policy = str(
                            (stage_plan.get("policyFingerprints") or {}).get(
                                stage_id
                            )
                            or ""
                        )
                        needs_resume = (
                            current_target_exists
                            and str(previous_policy) != current_policy
                        )
                elif previous_status == "not_applicable":
                    needs_resume = current_target_exists
                else:
                    needs_resume = True
            if needs_resume:
                resume_start_by_question[question_id] = stage_index
                break

    explicit_question_keys: set[str] = set()
    targets_for_stage: dict[str, list[str]] = {}
    question_stage_indexes = {
        str(stage_plan.get("stageId") or ""): stage_index
        for stage_index, stage_plan in enumerate(question_stage_plans)
    }
    for question_id, first_stage_index in resume_start_by_question.items():
        canonical_target = canonical_targets[question_id]
        for stage_plan in question_stage_plans[first_stage_index:]:
            stage_id = str(stage_plan.get("stageId") or "")
            target = targets_by_stage.get(stage_id, {}).get(
                question_id,
                canonical_target,
            )
            explicit_question_keys.add(work_item_key(target, stage_id))
            if question_id in targets_by_stage.get(stage_id, {}):
                targets_for_stage.setdefault(stage_id, []).append(question_id)

    filtered_stage_plans: list[dict[str, Any]] = []
    scope_resume_keys: set[str] = set()
    scope_question_ids: list[str] = []
    scope_resume_needed = False
    for stage_plan in raw_stage_plans:
        stage_id = str(stage_plan.get("stageId") or "")
        if stage_id in {"", "multi"}:
            continue
        if stage_id in {"setup", "category_setup"}:
            scope_targets = [
                dict(target)
                for target in stage_plan.get("progressTargets") or []
                if isinstance(target, Mapping)
            ]
            if scope_targets:
                filtered_stage_plans.append(copy.deepcopy(dict(stage_plan)))
                scope_resume_keys.update(
                    work_item_key(target, stage_id) for target in scope_targets
                )
                scope_question_ids.extend(_target_id(target) for target in scope_targets)
                scope_resume_needed = True
            else:
                filtered_stage_plans.append(copy.deepcopy(dict(stage_plan)))
            continue
        selected_ids = list(dict.fromkeys(targets_for_stage.get(stage_id, [])))
        if selected_ids:
            filtered_stage_plans.append(
                subset_question_plan(stage_plan, selected_ids)
            )
        elif any(
            first_stage_index <= question_stage_indexes[stage_id]
            for first_stage_index in resume_start_by_question.values()
        ):
            empty_stage = copy.deepcopy(dict(stage_plan))
            empty_stage.update(
                targetCount=0,
                workItemCount=0,
                targetQuestionKeys=[],
                progressTargets=[],
                targetRecordBindings=[],
                targetRecordAliasGroups=[],
                targetRecordScopes={},
                targetSourceRecordScopes={},
                policyTargets={},
            )
            filtered_stage_plans.append(empty_stage)
    if not resume_start_by_question and not scope_resume_needed:
        raise QuestionWorkQueueError("再実行が必要な問題はありません。")
    candidate = copy.deepcopy(dict(plan))
    # Scope phases keep their own full stage plan and resume keys.  When a
    # question item also needs retrying, the parent question contract must not
    # be widened back to every question covered by that scope phase.  The
    # scope-only fallback is still needed when the scope phase itself is the
    # sole remaining work item.
    pending_ids = list(
        dict.fromkeys(
            resume_start_by_question
            if resume_start_by_question
            else scope_question_ids
        )
    )
    if not pending_ids:
        raise QuestionWorkQueueError("再実行が必要な問題を現在の入力から解決できません。")
    candidate = subset_question_plan(candidate, pending_ids)
    candidate["stagePlans"] = filtered_stage_plans
    candidate["workItemCount"] = len(explicit_question_keys)
    candidate["policyTargets"] = {
        str(stage_id): list(targets)
        for stage_plan in filtered_stage_plans
        for stage_id, targets in (stage_plan.get("policyTargets") or {}).items()
        if targets
    }
    candidate["resumeWorkItemKeys"] = sorted(
        explicit_question_keys | scope_resume_keys
    )
    retry_keys: list[str] = []
    retry_feedback: dict[str, list[dict[str, Any]]] = {}
    for work_item_key_value in sorted(explicit_question_keys):
        failed_attempts = [
            attempt
            for attempt in (
                previous_items.get(work_item_key_value, {}).get(
                    "validationAttempts"
                )
                or []
            )
            if isinstance(attempt, Mapping)
            and str(attempt.get("status") or "")
            in {"failed", "blocked", "interrupted"}
        ]
        if not failed_attempts:
            continue
        retry_keys.append(work_item_key_value)
        latest_feedback = failed_attempts[-1].get("feedback")
        if isinstance(latest_feedback, Mapping):
            retry_feedback[work_item_key_value] = [dict(latest_feedback)]
    candidate["retryModelWorkItemKeys"] = retry_keys
    candidate["retryFeedbackByWorkItem"] = retry_feedback
    return candidate
