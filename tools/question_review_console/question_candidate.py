from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.common.suggested_question_contract import (
    public_choice_indexes,
    validation_errors as suggested_question_validation_errors,
)
from scripts.common.explanation_contract import explanation_shape_errors


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
            "questionImageStorageUrls",
            "originalQuestionChoiceImageUrls",
        }
    ),
    "question_type": frozenset({"questionType", "isCalculationQuestion"}),
    "question_intent": frozenset({"questionIntent"}),
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
            "suggestedQuestionDetailsByChoice",
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
            "lawContextForExplanation",
            "correctChoiceText",
            "answer_result_text",
            "explanationText",
            "suggestedQuestionDetailsByChoice",
            "holdReason",
            "reviewNotes",
            "evidenceSummary",
            "examTimeDecision",
            "currentLawDecision",
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

_SUGGESTED_QUESTION_DETAILS_BY_CHOICE_RULE: dict[str, Any] = {
    "type": "array",
    "description": (
        "各要素はchoiceIndexとitemsだけを持つ。itemsの各要素はquestionとanswerだけを持つ。"
        "choiceIndexは0始まりで重複不可、itemsは1件以上3件以下。補足が不要なら空配列にする。"
        "最初に現在のexplanationTextと質問・回答の両方を照合し、基本解説に答えがある"
        "質問は一件も残さない。基本解説にない追加情報を具体的に回答できる場合だけ保存し、"
        "追加情報がなければ必ず空配列にする。全選択肢へ一律に作らない。"
        "計算方法、式、代入、途中計算又は答えを尋ねる補足は、詳細計算を基本解説へ置くため"
        "保存しない。"
        "flash_cardとgroup_choiceは公開対象の正答選択肢だけを対象にし、"
        "誤答選択肢ごとの補足を作らない。"
    ),
    "items": {
        "type": "object",
        "required": ["choiceIndex", "items"],
        "additionalProperties": False,
        "properties": {
            "choiceIndex": {"type": "integer", "minimum": 0},
            "items": {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "required": ["question", "answer"],
                    "additionalProperties": False,
                    "properties": {
                        "question": {"type": "string", "minLength": 1},
                        "answer": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
    },
}

_EXPLANATION_FIELD_RULES: dict[str, Any] = {
    "explanationText": {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
        "description": (
            "true_falseは選択肢数と同数。flash_cardとgroup_choiceは問題共通の1本だけ。"
        ),
    },
    "suggestedQuestionDetailsByChoice": (
        _SUGGESTED_QUESTION_DETAILS_BY_CHOICE_RULE
    ),
}

_CORRECT_CHOICE_TEXT_RULE: dict[str, Any] = {
    "type": "array",
    "description": (
        "questionTypeにかかわらずchoiceTextListと必ず同じ件数にし、"
        "選択肢順に正しい又は間違いを入れる。正解、不正解、誤り等の"
        "表記ゆれは使わない。flash_cardとgroup_choiceでも正答だけへ縮めず、"
        "全選択肢分を保持する。"
    ),
    "items": {
        "type": "string",
        "allowedValues": ["正しい", "間違い"],
    },
}

_LAW_AUDIT_EXPLANATION_TEXT_RULE: dict[str, Any] = {
    **_EXPLANATION_FIELD_RULES["explanationText"],
    "description": (
        _EXPLANATION_FIELD_RULES["explanationText"]["description"]
        + " isLawRelated=trueでは、検証済みlawReferencesと対応する具体的な法令名、"
        "条項又は別表等の根拠位置を公開文面にも明記する。"
    ),
}

_LAW_AUDIT_SUGGESTED_QUESTION_RULE: dict[str, Any] = {
    **_SUGGESTED_QUESTION_DETAILS_BY_CHOICE_RULE,
    "description": (
        _SUGGESTED_QUESTION_DETAILS_BY_CHOICE_RULE["description"]
        + " isLawRelated=trueでも件数を満たすために作らない。作る場合は、"
        "検証済みlawReferencesの事実だけを使い、基本解説で示した根拠と矛盾させない。"
    ),
}

_CHOICE_DECISION_RULE: dict[str, Any] = {
    "type": "array",
    "description": (
        "choiceTextListと必ず同じ件数にし、選択肢順の非空stringを入れる。"
    ),
    "items": {"type": "string", "minLength": 1},
}

_LAW_REFERENCES_RULE: dict[str, Any] = {
    "type": "array",
    "description": (
        "choiceTextListと必ず同じ件数にし、各要素をその選択肢の根拠配列にする。"
        "変更不要な選択肢の検証済み根拠は保持する。"
    ),
    "items": {"type": "array"},
}

_FIELD_RULES_BY_ROLE: dict[str, dict[str, Any]] = {
    "correct_choice": {"correctChoiceText": _CORRECT_CHOICE_TEXT_RULE},
    "explanation": _EXPLANATION_FIELD_RULES,
    "law_audit": {
        **_EXPLANATION_FIELD_RULES,
        "explanationText": _LAW_AUDIT_EXPLANATION_TEXT_RULE,
        "suggestedQuestionDetailsByChoice": _LAW_AUDIT_SUGGESTED_QUESTION_RULE,
        "correctChoiceText": _CORRECT_CHOICE_TEXT_RULE,
        "lawReferences": _LAW_REFERENCES_RULE,
        "examTimeDecision": _CHOICE_DECISION_RULE,
        "currentLawDecision": _CHOICE_DECISION_RULE,
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
        "tertiaryAuditRunId": {
            "type": ["string", "null"],
            "emptyStringAllowed": False,
        },
    }
}

_FALLBACK_ROLES_BY_FIELD: dict[str, tuple[str, ...]] = {
    # 03bでは正誤patchと監査sidecarの同じ値を同時に確定する。
    "correctChoiceText": ("correct_choice", "law_audit"),
}


def _normalized_candidate_value(field: str, value: Any) -> Any:
    if field != "tertiaryAuditRunId":
        return value
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, (list, dict)) and not value:
        return None
    raise QuestionCandidateError(
        "tertiaryAuditRunIdはnull又は非空stringで指定してください。"
    )


def _field_destinations(
    question_id: str,
    field: str,
    requested_target: CandidateTarget,
    allowed_targets: Mapping[str, CandidateTarget],
) -> tuple[CandidateTarget, ...]:
    if field in requested_target.allowed_fields:
        return (requested_target,)
    candidates = tuple(
        target
        for target in allowed_targets.values()
        if field in target.allowed_fields
    )
    preferred_roles = _FALLBACK_ROLES_BY_FIELD.get(field, ())
    preferred = tuple(
        target for target in candidates if target.role in preferred_roles
    )
    if preferred:
        return preferred
    if len(candidates) == 1:
        return candidates
    raise QuestionCandidateError(
        "候補に許可されていないfieldがあります: "
        f"{question_id} / {requested_target.target_id} / {field}。"
        "このtargetのallowedFields: "
        + ", ".join(requested_target.allowed_fields)
    )


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
            value["fieldRules"] = {
                field: rule
                for field, rule in field_rules.items()
                if field in self.allowed_fields
            }
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
    selected_fields_by_stage = plan.get("selectedFieldsByStage")
    if isinstance(selected_fields_by_stage, Mapping) and stage_id in selected_fields_by_stage:
        selected_fields = {
            str(value)
            for value in selected_fields_by_stage.get(stage_id) or []
            if value
        }
    else:
        selected_fields = set().union(
            *(_FIELDS_BY_ROLE[role] for role in stage_roles)
        )
    supported_fields = set().union(*(_FIELDS_BY_ROLE[role] for role in stage_roles))
    unsupported_fields = selected_fields - supported_fields
    if unsupported_fields:
        raise QuestionCandidateError(
            "更新項目に候補生成未対応のfieldがあります: "
            + ", ".join(sorted(unsupported_fields))
        )
    if not selected_fields:
        raise QuestionCandidateError(f"更新fieldが選択されていません: {stage_id}")
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
        allowed_fields = tuple(sorted(_FIELDS_BY_ROLE[role] & selected_fields))
        if not allowed_fields:
            continue
        seen_roles.add(role)
        targets.append(
            CandidateTarget(
                target_id=f"{question_id}:{role}",
                role=role,
                path=path,
                allowed_fields=allowed_fields,
            )
        )
    required_roles = {
        role
        for role in _REQUIRED_STAGE_ROLES[stage_id]
        if _FIELDS_BY_ROLE[role] & selected_fields
    }
    missing = required_roles - seen_roles
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
            routed_fields: dict[str, dict[str, Any]] = {
                target_id: {"set": {}, "unset": []}
                for target_id in allowed_targets
            }
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
                        parsed_fields[field] = _normalized_candidate_value(
                            field,
                            json.loads(str(item.get("valueJson") or "")),
                        )
                    except json.JSONDecodeError as exc:
                        raise QuestionCandidateError(
                            f"setFields.valueJsonがJSONではありません: "
                            f"{question_id} / {field}"
                        ) from exc
                unset = tuple(dict.fromkeys(str(field) for field in unset_fields))
                overlap = set(parsed_fields) & set(unset)
                if overlap:
                    raise QuestionCandidateError(
                        "候補に許可されていないfieldがあります: "
                        f"{question_id} / {target_id} / "
                        + ", ".join(sorted(overlap))
                        + "。このtargetのallowedFields: "
                        + ", ".join(target.allowed_fields)
                    )
                for field, field_value in parsed_fields.items():
                    for destination in _field_destinations(
                        question_id,
                        field,
                        target,
                        allowed_targets,
                    ):
                        routed = routed_fields[destination.target_id]
                        if field in routed["unset"]:
                            raise QuestionCandidateError(
                                f"同じfieldに設定と削除があります: {question_id} / {field}"
                            )
                        existing = routed["set"].get(field, field_value)
                        if existing != field_value:
                            raise QuestionCandidateError(
                                f"同じfieldに異なる候補値があります: {question_id} / {field}"
                            )
                        routed["set"][field] = field_value
                for field in unset:
                    for destination in _field_destinations(
                        question_id,
                        field,
                        target,
                        allowed_targets,
                    ):
                        routed = routed_fields[destination.target_id]
                        if field in routed["set"]:
                            raise QuestionCandidateError(
                                f"同じfieldに設定と削除があります: {question_id} / {field}"
                            )
                        if field not in routed["unset"]:
                            routed["unset"].append(field)
            updates = tuple(
                CandidateUpdate(
                    target_id=target_id,
                    set_fields=dict(routed["set"]),
                    unset_fields=tuple(routed["unset"]),
                )
                for target_id, routed in routed_fields.items()
                if routed["set"] or routed["unset"]
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
                    updates=updates,
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
    if "explanationText" in changed_fields and explanations is not None:
        errors.extend(
            explanation_shape_errors(
                explanations,
                question_type=logical.get("questionType"),
                choice_count=len(choices),
            )
        )
    if "isCalculationQuestion" in changed_fields and not isinstance(
        logical.get("isCalculationQuestion"), bool
    ):
        errors.append("isCalculationQuestionがbooleanではありません。")
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
    if "suggestedQuestionDetailsByChoice" in changed_fields:
        suggestion_errors = suggested_question_validation_errors(
            logical.get("suggestedQuestionDetailsByChoice"),
            choice_count=len(choices),
            allowed_choice_indexes=public_choice_indexes(
                logical.get("questionType"),
                correct,
                len(choices),
            ),
        )
        if suggestion_errors:
            errors.append(
                "suggestedQuestionDetailsByChoiceが選択肢別・最大3件の契約を満たしません: "
                + " / ".join(suggestion_errors)
            )
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
