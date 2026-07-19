from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "question-maintenance-candidates/v2"


class QuestionCandidateError(ValueError):
    pass


_ROLE_BY_PATH_PART = {
    "05_originalized": "originalized",
    "10_questionType_fixed": "question_type",
    "15_correctChoiceText_fixed": "question_intent",
    "18_law_context_prepared": "law_context",
    "21_explanationText_added": "explanation",
    "22_questionSetId_linked": "question_set",
    "23_correctChoiceText_fixed": "correct_choice",
}

_FIELDS_BY_ROLE: dict[str, frozenset[str]] = {
    "originalized": frozenset(
        {
            "questionBodyText",
            "choiceTextList",
            "correctChoiceText",
            "questionIntent",
            "answer_result_text",
        }
    ),
    "question_type": frozenset({"questionType"}),
    "question_intent": frozenset(
        {"questionIntent", "correctChoiceText", "answer_result_text"}
    ),
    "correct_choice": frozenset({"correctChoiceText", "answer_result_text"}),
    "law_context": frozenset(
        {
            "isLawRelated",
            "lawGroundedExplanationNotNeeded",
            "lawReferences",
            "lawContextForExplanation",
        }
    ),
    "explanation": frozenset(
        {
            "explanationText",
            "suggestedQuestions",
            "suggestedQuestionDetails",
            "isLawRelated",
            "lawGroundedExplanationNotNeeded",
            "lawReferences",
            "lawContextForExplanation",
            "lawRevisionFacts",
        }
    ),
    "question_set": frozenset(
        {"questionSetId", "questionSetIdList", "choiceQuestionSetIds"}
    ),
    "law_audit": frozenset(
        {
            "auditStatus",
            "reviewState",
            "sourceSummary",
            "verificationSummary",
            "reconciliationStatus",
            "primaryAuditRunId",
            "secondaryAuditRunId",
            "tertiaryAuditRunId",
            "auditInputHash",
            "evidenceBindingHash",
            "lawRevisionFacts",
            "lawReferences",
            "isLawRelated",
            "lawGroundedExplanationNotNeeded",
            "correctChoiceText",
            "explanationText",
            "suggestedQuestions",
            "suggestedQuestionDetails",
            "holdReason",
            "reviewNotes",
            "evidenceSummary",
        }
    ),
}

_STAGE_ROLES: dict[str, frozenset[str]] = {
    "originalize": frozenset({"originalized"}),
    "question_type": frozenset({"question_type"}),
    "question_intent": frozenset({"question_intent"}),
    "correct_choice": frozenset({"correct_choice"}),
    "law_context": frozenset({"law_context"}),
    "explanation": frozenset({"explanation"}),
    "law_audit": frozenset(
        {"law_context", "explanation", "correct_choice", "law_audit"}
    ),
    "question_set": frozenset({"question_set"}),
}

_REQUIRED_STAGE_ROLES: dict[str, frozenset[str]] = {
    **_STAGE_ROLES,
    # 法令監査は解説と監査sidecarを正本とする。利用可能な場合だけ
    # law contextと正答patchも同じ候補で更新できる。
    "law_audit": frozenset({"explanation", "law_audit"}),
}

_FIELD_RULES_BY_ROLE: dict[str, dict[str, Any]] = {
    "law_audit": {
        "auditStatus": {
            "type": "string",
            "allowedValues": [
                "same_as_current",
                "updated_to_current_law",
                "hold",
                "not_law_related",
            ],
        },
        "reviewState": {
            "type": "string",
            "allowedValues": [
                "primary_checked",
                "secondary_verified",
                "tertiary_verified",
                "needs_secondary_review",
                "needs_tertiary_review",
            ],
        },
    }
}


@dataclass(frozen=True)
class CandidateTarget:
    target_id: str
    role: str
    path: str
    allowed_fields: tuple[str, ...]

    def prompt_value(self) -> dict[str, Any]:
        value = {
            "targetId": self.target_id,
            "role": self.role,
            "allowedFields": list(self.allowed_fields),
        }
        field_rules = _FIELD_RULES_BY_ROLE.get(self.role)
        if field_rules:
            value["fieldRules"] = field_rules
        return value


@dataclass(frozen=True)
class CandidateUpdate:
    target_id: str
    set_fields: dict[str, Any]
    unset_fields: tuple[str, ...]


@dataclass(frozen=True)
class QuestionCandidate:
    question_id: str
    status: str
    summary: str
    updates: tuple[CandidateUpdate, ...]


def _path_role(path: str) -> str | None:
    value = Path(path)
    if "law_revision_audit" in value.parts:
        return "law_audit"
    return next(
        (role for part, role in _ROLE_BY_PATH_PART.items() if part in value.parts),
        None,
    )


def candidate_targets(
    question_id: str,
    stage_id: str,
    plan: Mapping[str, Any],
) -> tuple[CandidateTarget, ...]:
    stage_roles = _STAGE_ROLES.get(str(stage_id), frozenset())
    if not stage_roles:
        raise QuestionCandidateError(f"候補生成に未対応の工程です: {stage_id}")
    targets: list[CandidateTarget] = []
    seen_roles: set[str] = set()
    for raw_path in [
        *(plan.get("allowedPatchFiles") or []),
        *(plan.get("allowedWriteFiles") or []),
    ]:
        path = str(raw_path)
        role = _path_role(path)
        if role not in stage_roles or role in seen_roles:
            continue
        seen_roles.add(role)
        targets.append(
            CandidateTarget(
                target_id=f"{question_id}:{role}",
                role=role,
                path=path,
                allowed_fields=tuple(sorted(_FIELDS_BY_ROLE[role])),
            )
        )
    missing = _REQUIRED_STAGE_ROLES[stage_id] - seen_roles
    if missing:
        raise QuestionCandidateError(
            "候補反映先を解決できません: " + ", ".join(sorted(missing))
        )
    return tuple(targets)


def output_schema(
    expected_question_ids: Iterable[str],
    targets_by_question: Mapping[str, Iterable[CandidateTarget]],
) -> dict[str, Any]:
    question_ids = tuple(dict.fromkeys(str(value) for value in expected_question_ids))
    target_ids = sorted(
        {
            target.target_id
            for question_id in question_ids
            for target in targets_by_question.get(question_id, ())
        }
    )
    field_names = sorted(
        {
            field
            for question_id in question_ids
            for target in targets_by_question.get(question_id, ())
            for field in target.allowed_fields
        }
    )
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schemaVersion", "questionResults"],
        "properties": {
            "schemaVersion": {"type": "string", "const": SCHEMA_VERSION},
            "questionResults": {
                "type": "array",
                "minItems": len(question_ids),
                "maxItems": len(question_ids),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["questionId", "status", "summary", "updates"],
                    "properties": {
                        "questionId": {"type": "string", "enum": list(question_ids)},
                        "status": {
                            "type": "string",
                            "enum": ["candidate", "blocked"],
                        },
                        "summary": {"type": "string", "minLength": 1},
                        "updates": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "targetId",
                                    "setFields",
                                    "unsetFields",
                                ],
                                "properties": {
                                    "targetId": {
                                        "type": "string",
                                        "enum": target_ids,
                                    },
                                    "setFields": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "required": ["field", "valueJson"],
                                            "properties": {
                                                "field": {
                                                    "type": "string",
                                                    "enum": field_names,
                                                },
                                                "valueJson": {"type": "string"},
                                            },
                                        },
                                    },
                                    "unsetFields": {
                                        "type": "array",
                                        "items": {
                                            "type": "string",
                                            "enum": field_names,
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    }


def parse_candidates(
    value: str | Mapping[str, Any],
    expected_question_ids: Iterable[str],
    targets_by_question: Mapping[str, Iterable[CandidateTarget]],
) -> tuple[QuestionCandidate, ...]:
    try:
        payload = json.loads(value) if isinstance(value, str) else dict(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise QuestionCandidateError("構造化候補をJSONとして読み取れません。") from exc
    if payload.get("schemaVersion") != SCHEMA_VERSION:
        raise QuestionCandidateError("構造化候補のschemaVersionが一致しません。")
    raw_results = payload.get("questionResults")
    if not isinstance(raw_results, list):
        raise QuestionCandidateError("構造化候補にquestionResultsがありません。")

    expected = tuple(dict.fromkeys(str(value) for value in expected_question_ids))
    grouped: dict[str, list[Mapping[str, Any]]] = {value: [] for value in expected}
    for raw in raw_results:
        if not isinstance(raw, Mapping):
            raise QuestionCandidateError("questionResultsの要素がobjectではありません。")
        question_id = str(raw.get("questionId") or "")
        if question_id in grouped:
            grouped[question_id].append(raw)

    normalized: list[QuestionCandidate] = []
    for question_id in expected:
        rows = grouped[question_id]
        if len(rows) != 1:
            normalized.append(
                QuestionCandidate(
                    question_id=question_id,
                    status="blocked",
                    summary=(
                        f"問題別候補が"
                        f"{'ありません' if not rows else '重複しています'}: "
                        f"{question_id}"
                    ),
                    updates=(),
                )
            )
            continue
        raw = rows[0]
        try:
            status = str(raw.get("status") or "")
            if status not in {"candidate", "blocked"}:
                raise QuestionCandidateError(f"候補状態が不正です: {question_id}")
            summary = str(raw.get("summary") or "").strip()
            if not summary:
                raise QuestionCandidateError(
                    f"候補のsummaryがありません: {question_id}"
                )
            allowed_targets = {
                target.target_id: target
                for target in targets_by_question.get(question_id, ())
            }
            updates: list[CandidateUpdate] = []
            seen_targets: set[str] = set()
            raw_updates = raw.get("updates")
            if not isinstance(raw_updates, list):
                raise QuestionCandidateError(
                    f"updatesが配列ではありません: {question_id}"
                )
            for raw_update in raw_updates:
                if not isinstance(raw_update, Mapping):
                    raise QuestionCandidateError(
                        f"updateがobjectではありません: {question_id}"
                    )
                target_id = str(raw_update.get("targetId") or "")
                target = allowed_targets.get(target_id)
                if target is None or target_id in seen_targets:
                    raise QuestionCandidateError(
                        f"候補のtargetIdが対象外又は重複です: "
                        f"{question_id} / {target_id}"
                    )
                seen_targets.add(target_id)
                set_fields = raw_update.get("setFields")
                unset_fields = raw_update.get("unsetFields")
                if not isinstance(set_fields, list) or not isinstance(
                    unset_fields, list
                ):
                    raise QuestionCandidateError(
                        f"setFields又はunsetFieldsの形式が不正です: "
                        f"{question_id}"
                    )
                parsed_fields: dict[str, Any] = {}
                for item in set_fields:
                    if not isinstance(item, Mapping):
                        raise QuestionCandidateError(
                            f"setFieldsの要素がobjectではありません: "
                            f"{question_id}"
                        )
                    field = str(item.get("field") or "")
                    if not field or field in parsed_fields:
                        raise QuestionCandidateError(
                            f"setFieldsのfieldが空又は重複しています: "
                            f"{question_id}"
                        )
                    try:
                        parsed_fields[field] = json.loads(
                            str(item.get("valueJson") or "")
                        )
                    except json.JSONDecodeError as exc:
                        raise QuestionCandidateError(
                            f"setFields.valueJsonがJSONではありません: "
                            f"{question_id} / {field}"
                        ) from exc
                unset = tuple(dict.fromkeys(str(field) for field in unset_fields))
                requested = set(parsed_fields) | set(unset)
                disallowed = requested - set(target.allowed_fields)
                overlap = set(parsed_fields) & set(unset)
                if disallowed or overlap:
                    reason = disallowed or overlap
                    raise QuestionCandidateError(
                        "候補に許可されていないfieldがあります: "
                        f"{question_id} / {target_id} / "
                        + ", ".join(sorted(reason))
                        + "。このtargetのallowedFields: "
                        + ", ".join(target.allowed_fields)
                    )
                updates.append(
                    CandidateUpdate(
                        target_id=target_id,
                        set_fields=parsed_fields,
                        unset_fields=unset,
                    )
                )
            if status == "blocked" and updates:
                raise QuestionCandidateError(
                    f"blocked候補はpatch更新を返せません: {question_id}"
                )
            normalized.append(
                QuestionCandidate(
                    question_id=question_id,
                    status=status,
                    summary=summary[:4000],
                    updates=tuple(updates),
                )
            )
        except QuestionCandidateError as exc:
            normalized.append(
                QuestionCandidate(
                    question_id=question_id,
                    status="blocked",
                    summary=str(exc)[:4000],
                    updates=(),
                )
            )
    return tuple(normalized)


def validate_candidate_content(
    candidate: QuestionCandidate,
    targets: Iterable[CandidateTarget],
    projected_record: Mapping[str, Any],
) -> tuple[str, ...]:
    """Run cheap deterministic checks against this question only."""

    if candidate.status == "blocked":
        return ()
    target_by_id = {target.target_id: target for target in targets}
    logical = json.loads(json.dumps(dict(projected_record), ensure_ascii=False))
    audit_payloads: list[Mapping[str, Any]] = []
    changed_fields: set[str] = set()
    for update in candidate.updates:
        target = target_by_id[update.target_id]
        if target.role == "law_audit":
            audit_payloads.append(update.set_fields)
            continue
        changed_fields.update(str(field) for field in update.set_fields)
        changed_fields.update(update.unset_fields)
        logical.update(update.set_fields)
        for field in update.unset_fields:
            logical.pop(field, None)

    errors: list[str] = []
    choices = logical.get("choiceTextList") or []
    correct = logical.get("correctChoiceText")
    if "correctChoiceText" in changed_fields and correct is not None and (
        not isinstance(correct, list)
        or len(correct) != len(choices)
        or any(value not in {"正しい", "間違い", "誤り"} for value in correct)
    ):
        errors.append("correctChoiceTextが選択肢と同じ件数の正誤配列ではありません。")
    explanations = logical.get("explanationText")
    if "explanationText" in changed_fields and explanations is not None and (
        not isinstance(explanations, list)
        or len(explanations) != len(choices)
        or any(not isinstance(value, str) or not value.strip() for value in explanations)
    ):
        errors.append("explanationTextが選択肢と同じ件数の非空文字列ではありません。")
    if "isLawRelated" in changed_fields and not isinstance(
        logical.get("isLawRelated"), bool
    ):
        errors.append("isLawRelatedがbooleanではありません。")
    law_references = logical.get("lawReferences")
    if "lawReferences" in changed_fields and law_references is not None and (
        not isinstance(law_references, list)
        or len(law_references) != len(choices)
        or any(not isinstance(value, list) for value in law_references)
    ):
        errors.append("lawReferencesが選択肢と同じ件数の配列ではありません。")
    suggested = logical.get("suggestedQuestions")
    details = logical.get("suggestedQuestionDetails")
    if "suggestedQuestions" in changed_fields and suggested is not None and (
        not isinstance(suggested, list)
        or not 3 <= len(suggested) <= 5
        or any(not isinstance(value, str) or not value.strip() for value in suggested)
    ):
        errors.append("suggestedQuestionsが3〜5件の非空文字列ではありません。")
    if changed_fields & {"suggestedQuestions", "suggestedQuestionDetails"} and details is not None and (
        not isinstance(details, list)
        or not isinstance(suggested, list)
        or len(details) != len(suggested)
    ):
        errors.append("suggestedQuestionDetailsがsuggestedQuestionsと対応していません。")
    facts = logical.get("lawRevisionFacts")
    if "lawRevisionFacts" in changed_fields and facts is not None and not isinstance(facts, Mapping):
        errors.append("lawRevisionFactsがobjectではありません。")
    if changed_fields & {"lawRevisionFacts", "correctChoiceText"} and isinstance(facts, Mapping) and isinstance(correct, list):
        current = facts.get("current")
        current_correct = (
            current.get("correctChoiceText")
            if isinstance(current, Mapping)
            else None
        )
        if current_correct is not None and current_correct != correct:
            errors.append("lawRevisionFacts.currentとトップレベル正答が一致しません。")
    for audit in audit_payloads:
        if audit.get("auditStatus") not in {
            "same_as_current",
            "updated_to_current_law",
            "not_law_related",
            "hold",
            None,
        }:
            errors.append("監査sidecarのauditStatusが不正です。")
    return tuple(dict.fromkeys(errors))
