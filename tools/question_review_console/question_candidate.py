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
from scripts.common.explanation_contract import (
    explanation_shape_errors,
)
from scripts.common.aggregate_answer_decomposition import REVIEW_SCHEMA_VERSION
from scripts.common.explanation_references import explanation_reference_errors
from scripts.common.question_answer_contract import (
    explicit_statement_question_intent,
    official_answer_alignment_issue,
    question_level_answer_cardinality_issue,
)
from scripts.common.repaso_firestore_schema import (
    LAW_REVISION_EVIDENCE_REF_KEYS,
    LAW_REVISION_EVIDENCE_SUMMARY_KEYS,
    LAW_REVISION_FACT_KEYS,
    LAW_REVISION_SNAPSHOT_KEYS,
    _is_law_revision_evidence_summary,
    is_law_revision_facts_shape,
    law_revision_facts_shape_errors,
)
from scripts.merge.patch_views import validate_originalized_entry
from tools.question_review_console.explanation_quality import (
    explanation_style_issues,
)
from tools.question_review_console.law_audit_quality import (
    law_revision_current_verdict_issues,
)


CANDIDATE_PAYLOAD_SCHEMA_VERSION = "question-maintenance-candidates/v3"
OFFICIAL_QUESTION_TYPES = ("true_false", "flash_card", "group_choice")
AGGREGATE_REVIEW_ISSUE_CODES = (
    "ambiguous_target",
    "ambiguous_boundary",
    "missing_statement",
    "not_self_contained",
    "source_hash_mismatch",
)


class QuestionCandidateError(ValueError):
    pass


def aggregate_answer_review_schema(
    expected_question_ids: Iterable[str],
    candidate_ids_by_question: Mapping[str, Iterable[str]] | None = None,
    source_hashes_by_question: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Schema for a prose-free, read-only aggregate-answer review turn."""

    question_ids = tuple(dict.fromkeys(str(value) for value in expected_question_ids))
    source_hashes = sorted(
        {
            str(source_hashes_by_question[question_id])
            for question_id in question_ids
            if source_hashes_by_question
            and question_id in source_hashes_by_question
        }
    )
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schemaVersion", "questionReviews"],
        "properties": {
            "schemaVersion": {
                "type": "string",
                "const": "aggregate-answer-review-batch/v2",
            },
            "questionReviews": {
                "type": "array",
                "minItems": len(question_ids),
                "maxItems": len(question_ids),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "questionId",
                        "schemaVersion",
                        "sourceHash",
                        "classification",
                        "candidateId",
                        "decision",
                        "issueCodes",
                    ],
                    "properties": {
                        "questionId": {"type": "string", "enum": list(question_ids)},
                        "schemaVersion": {
                            "type": "string",
                            "const": REVIEW_SCHEMA_VERSION,
                        },
                        "sourceHash": {
                            "type": "string",
                            **(
                                {"enum": source_hashes}
                                if source_hashes
                                else {"pattern": "^sha256:[0-9a-f]{64}$"}
                            ),
                        },
                        "classification": {
                            "type": "string",
                            "enum": ["target", "non_target", "hold"],
                        },
                        "candidateId": {
                            "type": ["string", "null"],
                            "enum": [
                                None,
                                *sorted(
                                    {
                                        str(candidate_id)
                                        for values in (
                                            candidate_ids_by_question or {}
                                        ).values()
                                        for candidate_id in values
                                    }
                                ),
                            ],
                        },
                        "decision": {
                            "type": "string",
                            "enum": ["approve", "hold"],
                        },
                        "issueCodes": {
                            "type": "array",
                            "uniqueItems": True,
                            "items": {
                                "type": "string",
                                "enum": list(AGGREGATE_REVIEW_ISSUE_CODES),
                            },
                        },
                    },
                },
            },
        },
    }


def parse_aggregate_answer_reviews(
    value: str | Mapping[str, Any],
    expected_question_ids: Iterable[str],
    candidate_ids_by_question: Mapping[str, Iterable[str]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Parse only structural review data; prose and extracted text are rejected."""

    try:
        payload = json.loads(value) if isinstance(value, str) else dict(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise QuestionCandidateError("集約回答レビューをJSONとして読み取れません。") from exc
    if set(payload) != {"schemaVersion", "questionReviews"} or payload.get(
        "schemaVersion"
    ) != "aggregate-answer-review-batch/v2":
        raise QuestionCandidateError("集約回答レビューbatch schemaが一致しません。")
    rows = payload.get("questionReviews")
    if not isinstance(rows, list):
        raise QuestionCandidateError("集約回答レビューにquestionReviewsがありません。")
    expected = tuple(dict.fromkeys(str(value) for value in expected_question_ids))
    result: dict[str, dict[str, Any]] = {}
    allowed = {
        "questionId",
        "schemaVersion",
        "sourceHash",
        "classification",
        "candidateId",
        "decision",
        "issueCodes",
    }
    for raw in rows:
        if not isinstance(raw, Mapping) or set(raw) != allowed:
            raise QuestionCandidateError("集約回答レビューに文章又は未許可fieldがあります。")
        question_id = str(raw.get("questionId") or "")
        if question_id not in expected or question_id in result:
            raise QuestionCandidateError("集約回答レビューのquestionIdが対象外又は重複です。")
        candidate_id = raw.get("candidateId")
        allowed_candidate_ids = {
            str(value)
            for value in (candidate_ids_by_question or {}).get(question_id, ())
        }
        if candidate_id is not None and (
            not isinstance(candidate_id, str)
            or candidate_id not in allowed_candidate_ids
        ):
            raise QuestionCandidateError("集約回答レビューのcandidateIdが対象外です。")
        classification = raw.get("classification")
        decision = raw.get("decision")
        if classification == "target" and decision == "approve":
            if candidate_id is None:
                raise QuestionCandidateError("target承認にはcandidateIdが必要です。")
        elif candidate_id is not None:
            raise QuestionCandidateError("target承認以外はcandidateIdを選択できません。")
        issue_codes = raw.get("issueCodes")
        if (
            not isinstance(issue_codes, list)
            or not all(code in AGGREGATE_REVIEW_ISSUE_CODES for code in issue_codes)
            or len(issue_codes) != len(set(issue_codes))
        ):
            raise QuestionCandidateError("集約回答レビューのissueCodesが不正又は重複です。")
        result[question_id] = {key: raw[key] for key in allowed if key != "questionId"}
    if set(result) != set(expected):
        raise QuestionCandidateError("集約回答レビューが全対象問題を含んでいません。")
    return result


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
    "correct_choice": frozenset({"correctChoiceText"}),
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
            "explanationReferences",
            "suggestedQuestionDetailsByChoice",
            "isLawRelated",
            "lawGroundedExplanationNotNeeded",
            "lawReferences",
            "lawContextForExplanation",
            "lawRevisionFacts",
        }
    ),
    "question_set": frozenset({"questionSetId"}),
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
            "true_falseの各解説は対応するcorrectChoiceTextに合わせて、"
            "「正しい。」又は「間違い。」で始める。正しい選択肢は追加の"
            "学習情報がなければ「正しい。」だけとし、選択肢を再掲しない。"
            "間違いの選択肢は正しい内容と判断を分ける差を必ず説明する。"
        ),
    },
    "suggestedQuestionDetailsByChoice": (
        _SUGGESTED_QUESTION_DETAILS_BY_CHOICE_RULE
    ),
    "explanationReferences": {
        "type": "array",
        "description": (
            "解説の根拠として実際に確認した公式一次資料だけを保存する。"
            "各要素はtitle、sourceUrl、referenceDateだけを必須とし、"
            "特定の選択肢だけに対応する場合のみ0始まりのchoiceIndexを加える。"
            "sourceUrlはHTTPS URL、referenceDateはYYYY-MM-DD形式とする。"
            "候補、未確認、非公式の参照先は正式patchへ保存しない。"
        ),
        "items": {
            "type": "object",
            "required": ["title", "sourceUrl", "referenceDate"],
            "additionalProperties": False,
            "properties": {
                "title": {"type": "string", "minLength": 1},
                "sourceUrl": {"type": "string", "minLength": 1},
                "referenceDate": {"type": "string", "minLength": 1},
                "choiceIndex": {"type": "integer", "minimum": 0},
            },
        },
    },
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
        "03bでは各根拠をobjectで返し、verificationStatus=verified、正式法令名、"
        "lawId、条番号、基準日、一次情報sourceを省略しない。変更不要な選択肢の"
        "検証済み根拠は保持する。法令肢と技術肢が混在する問題では、法令肢だけに"
        "verified根拠を入れ、技術肢は空配列にする。"
    ),
    "items": {
        "type": "array",
        "items": {
            "type": "object",
            "required": [
                "role",
                "scope",
                "lawId",
                "lawTitle",
                "referenceDate",
                "article",
                "verificationStatus",
                "source",
            ],
            "properties": {
                "role": {
                    "type": "string",
                    "allowedValues": ["current_basis", "exam_time_basis"],
                },
                "scope": {
                    "type": "string",
                    "allowedValues": ["question", "choice"],
                },
                "choiceIndex": {"type": "integer", "minimum": 0},
                "lawId": {"type": "string", "minLength": 1},
                "lawTitle": {"type": "string", "minLength": 1},
                "referenceDate": {"type": "string", "minLength": 1},
                "article": {"type": "string", "minLength": 1},
                "verificationStatus": {
                    "type": "string",
                    "allowedValues": ["verified"],
                },
                "source": {"type": "string", "minLength": 1},
            },
        },
    },
}

_LAW_REVISION_FACT_FIELDS = "、".join(sorted(LAW_REVISION_FACT_KEYS))
_LAW_REVISION_SNAPSHOT_FIELDS = "、".join(
    sorted(LAW_REVISION_SNAPSHOT_KEYS)
)
_LAW_REVISION_EVIDENCE_SUMMARY_FIELDS = "、".join(
    sorted(LAW_REVISION_EVIDENCE_SUMMARY_KEYS)
)
_LAW_REVISION_EVIDENCE_REF_FIELDS = "、".join(
    sorted(LAW_REVISION_EVIDENCE_REF_KEYS)
)

_LAW_REVISION_FACTS_RULE: dict[str, Any] = {
    "description": (
        "question field契約に従う。true_false等の複数選択肢patchでは"
        "choiceTextListと同じ件数のobject配列を使い、各objectにauditStatus、"
        "reviewState、current.correctChoiceTextのscalar、examTime.correctChoiceTextの"
        "scalar、非空objectのevidenceSummaryを入れる。選択肢との対応は配列順で表し、"
        "choiceIndex等の独自fieldをobjectへ追加しない。"
        f"各objectのtop-levelで使用できるfieldは{_LAW_REVISION_FACT_FIELDS}だけである。"
        "互換のquestion-level objectを"
        "使う場合はcurrent/examTime.correctChoiceTextを選択肢順の配列にする。"
        "evidenceSummaryで使用できるfieldは"
        f"{_LAW_REVISION_EVIDENCE_SUMMARY_FIELDS}だけとし、"
        "旧形式のsummaryは使わない。refsは文字列配列ではなくobject配列とし、"
        "構造化できない根拠はrefsをomitする。refsのobjectは"
        f"{_LAW_REVISION_EVIDENCE_REF_FIELDS}だけを使用する。"
        "currentとexamTimeで使用できるfieldは"
        f"{_LAW_REVISION_SNAPSHOT_FIELDS}だけである。"
        "複数の号をitems等の独自fieldへ"
        "まとめず、単一値にできないlocatorはsnapshotからomitし、必要なら"
        "evidenceSummary.refsのobjectごとに記録する。"
        "auditStatus=updated_to_current_lawはreviewState=tertiary_verifiedに限る。"
        "法令肢と技術肢が混在する問題では、技術肢を"
        "auditStatus=not_law_related、reviewState=secondary_verifiedとして"
        "選択肢別に確定する。"
    ),
    "oneOf": [{
        "type": "object",
        "additionalProperties": False,
        "required": [
            "auditStatus",
            "reviewState",
            "reconciliationStatus",
            "examTime",
            "current",
            "differenceFacts",
            "answerImpactFacts",
            "notes",
            "evidenceSummary",
        ],
        "properties": {
            "auditStatus": {
                "type": "string",
                "enum": [
                    "same_as_current",
                    "updated_to_current_law",
                    "hold",
                    "not_law_related",
                ],
            },
            "reviewState": {"type": "string", "minLength": 1},
            "reconciliationStatus": {"type": "string", "minLength": 1},
            "examTime": {
                "type": "object",
                "additionalProperties": False,
                "required": ["correctChoiceText"],
                "properties": {
                    "correctChoiceText": {"type": "string", "minLength": 1},
                },
            },
            "current": {
                "type": "object",
                "additionalProperties": False,
                "required": ["correctChoiceText"],
                "properties": {
                    "correctChoiceText": {"type": "string", "minLength": 1},
                },
            },
            "differenceFacts": {
                "type": "array",
                "items": {"type": "string"},
            },
            "answerImpactFacts": {
                "type": "array",
                "items": {"type": "string"},
            },
            "notes": {"type": "array", "items": {"type": "string"}},
            "evidenceSummary": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "verdict",
                    "explanationText",
                    "differenceSummary",
                    "promptContext",
                    "displayRefIds",
                    "refs",
                ],
                "properties": {
                    "verdict": {"type": "string", "minLength": 1},
                    "explanationText": {"type": "string", "minLength": 1},
                    "differenceSummary": {"type": "string", "minLength": 1},
                    "promptContext": {"type": "string", "minLength": 1},
                    "displayRefIds": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "refs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "refId",
                                "lawTimeScope",
                                "relation",
                                "primaryBasis",
                                "lawId",
                                "lawRevisionId",
                                "lawTitle",
                                "elm",
                                "encodedElm",
                                "rootArticleElm",
                                "article",
                                "paragraph",
                                "item",
                                "subitem",
                                "highlightElms",
                                "articleTextHash",
                                "textHash",
                            ],
                            "properties": {
                                **{
                                    field: {"type": "string", "minLength": 1}
                                    for field in sorted(
                                        LAW_REVISION_EVIDENCE_REF_KEYS
                                        - {"highlightElms", "primaryBasis"}
                                    )
                                },
                                "highlightElms": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "primaryBasis": {"type": "boolean"},
                            },
                        },
                    },
                },
            },
        },
    }, {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "auditStatus",
                "reviewState",
                "reconciliationStatus",
                "examTime",
                "current",
                "differenceFacts",
                "answerImpactFacts",
                "notes",
                "evidenceSummary",
            ],
            "properties": {},
        },
    }],
}
_LAW_REVISION_FACTS_RULE["oneOf"][1]["items"]["properties"] = (
    _LAW_REVISION_FACTS_RULE["oneOf"][0]["properties"]
)

_SHARED_LAW_FIELD_RULES: dict[str, Any] = {
    "isLawRelated": {"type": "boolean"},
    "lawGroundedExplanationNotNeeded": {"type": "boolean"},
    "lawReferences": _LAW_REFERENCES_RULE,
    "lawContextForExplanation": {
        "type": "string",
        "minLength": 1,
        "description": "解説工程へ渡す短い根拠メモ。法令本文や長文引用は入れない。",
    },
}

_FIELD_RULES_BY_ROLE: dict[str, dict[str, Any]] = {
    "originalized": {
        "questionBodyText": {"type": "string"},
        "choiceTextList": {"type": "array", "items": {"type": "string"}},
        "correctChoiceText": _CORRECT_CHOICE_TEXT_RULE,
        "questionIntent": {
            "type": "string",
            "allowedValues": ["select_correct", "select_incorrect"],
        },
        "answer_result_text": {"type": "string"},
        "questionImageStorageUrls": {
            "type": "array",
            "items": {"type": "string"},
        },
        "originalQuestionChoiceImageUrls": {
            "type": "array",
            "items": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
    },
    "question_type": {
        "questionType": {
            "type": "string",
            "allowedValues": list(OFFICIAL_QUESTION_TYPES),
            "description": (
                "公式過去問とexamYearのない暗記プラス独自問題は、いずれも"
                "true_false、flash_card、group_choiceの3分類で回答体験を表す。"
                "single_choiceとfill_in_blankはユーザー作成問題だけに使う。"
                "問題文の条件、知識、図又は計算から具体的な答えを一意に導き、"
                "choiceTextListをその答えとの照合にだけ使う問題はflash_cardとする。"
                "単一の計算結果に最も近い数値候補を選ぶ問題もflash_cardであり、"
                "数値候補を順番に照合することだけを理由にtrue_falseへ変えない。"
                "choiceTextListの各肢が互いに異なる条件、物質、反応式などの"
                "計算対象を持ち、肢ごとに独立して正誤を判定する問題はtrue_false"
                "とする。最大・最小などを問う問題で、"
                "choiceTextListが組合せ番号ではなく比較対象そのものを持つ場合も"
                "true_falseとする。最終回答となる組合せ候補そのものが"
                "choiceTextListに並び、そこから正答を1つ選ぶ場合だけgroup_choice"
                "とする。現行correctChoiceText、"
                "answer_result_text又は組合せmappingの欠落・不整合は、"
                "後続の正答精査で扱うため、このfieldをblockedにする理由にしない。"
            ),
        },
        "isCalculationQuestion": {"type": "boolean"},
    },
    "question_intent": {
        "questionIntent": {
            "type": "string",
            "allowedValues": ["select_correct", "select_incorrect"],
            "description": (
                "設問が正しい、適切又は条件に合う側を選ばせるなら"
                "select_correct、誤り、不適切又は該当しない側を選ばせるなら"
                "select_incorrectとする。choiceTextListが設備名、名詞句、数値、"
                "対象名などの断片でも、問題文が明示する選択方向を反転しない。"
                "各肢のcorrectChoiceText判定とquestionIntent判定を混ぜない。"
                "現在値、正答番号又はcorrectChoiceTextから逆算しない。"
            ),
        },
    },
    "correct_choice": {"correctChoiceText": _CORRECT_CHOICE_TEXT_RULE},
    "question_set": {
        "questionSetId": {"type": "string", "minLength": 1},
    },
    "law_context": _SHARED_LAW_FIELD_RULES,
    "explanation": {
        **_EXPLANATION_FIELD_RULES,
        **_SHARED_LAW_FIELD_RULES,
        "lawRevisionFacts": _LAW_REVISION_FACTS_RULE,
    },
    "law_audit": {
        **_EXPLANATION_FIELD_RULES,
        **_SHARED_LAW_FIELD_RULES,
        "explanationText": _LAW_AUDIT_EXPLANATION_TEXT_RULE,
        "suggestedQuestionDetailsByChoice": _LAW_AUDIT_SUGGESTED_QUESTION_RULE,
        "correctChoiceText": _CORRECT_CHOICE_TEXT_RULE,
        "examTimeDecision": _CHOICE_DECISION_RULE,
        "currentLawDecision": _CHOICE_DECISION_RULE,
        "lawRevisionFacts": _LAW_REVISION_FACTS_RULE,
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
        "sourceSummary": {"type": "string"},
        "verificationSummary": {"type": "string"},
        "reconciliationStatus": {"type": "string"},
        "holdReason": {"type": "string"},
        "reviewNotes": {"type": "string"},
        "evidenceSummary": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "differenceSummary",
                "displayRefIds",
                "explanationText",
                "promptContext",
                "refs",
                "verdict",
            ],
            "properties": {
                "differenceSummary": {"type": "string", "minLength": 1},
                "displayRefIds": {"type": "array", "items": {"type": "string"}},
                "explanationText": {"type": "string", "minLength": 1},
                "promptContext": {"type": "string", "minLength": 1},
                "refs": {
                    "type": "array",
                    "items": _LAW_REVISION_FACTS_RULE["oneOf"][0]["properties"][
                        "evidenceSummary"
                    ]["properties"]["refs"]["items"],
                },
                "verdict": {"type": "string", "minLength": 1},
            },
        },
    }
}

_CANONICAL_ROLES_BY_FIELD: dict[str, tuple[str, ...]] = {
    # 03bの一つの候補を、既存patch責務と監査sidecarへ同時に配送する。
    "correctChoiceText": ("correct_choice", "law_audit"),
    "explanationText": ("explanation", "law_audit"),
    "explanationReferences": ("explanation",),
    "suggestedQuestionDetailsByChoice": ("explanation", "law_audit"),
    "lawRevisionFacts": ("explanation", "law_audit"),
    "isLawRelated": ("law_context", "explanation", "law_audit"),
    "lawGroundedExplanationNotNeeded": (
        "law_context",
        "explanation",
        "law_audit",
    ),
    "lawReferences": ("law_context", "explanation", "law_audit"),
    "lawContextForExplanation": (
        "law_context",
        "explanation",
        "law_audit",
    ),
}

SERVER_OWNED_LAW_AUDIT_FIELDS = frozenset(
    {
        "qualification",
        "listGroupId",
        "auditedAt",
        "nextAuditDueAt",
        "auditMethodVersion",
        "auditInputHash",
        "evidenceBindingHash",
        "auditRunId",
        "lawCorpusSnapshotId",
        "primaryAuditRunId",
        "secondaryAuditRunId",
        "tertiaryAuditRunId",
        "userVisibleNoticeRequired",
        "noticeReason",
        "remainingRisk",
    }
)


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
    allowed_targets: Mapping[str, CandidateTarget],
    requested_target: CandidateTarget | None = None,
) -> tuple[CandidateTarget, ...]:
    candidates = tuple(
        target
        for target in allowed_targets.values()
        if field in target.allowed_fields
    )
    preferred_roles = _CANONICAL_ROLES_BY_FIELD.get(field, ())
    by_role = {target.role: target for target in candidates}
    preferred = tuple(
        by_role[role] for role in preferred_roles if role in by_role
    )
    if preferred:
        return preferred
    if requested_target is not None and field in requested_target.allowed_fields:
        return (requested_target,)
    if len(candidates) == 1:
        return candidates
    raise QuestionCandidateError(
        "候補に許可されていないfield又は反映先が曖昧です: "
        f"{question_id} / {field}"
    )


@dataclass(frozen=True)
class CandidateTarget:
    target_id: str
    role: str
    path: str
    allowed_fields: tuple[str, ...]

    def prompt_value(self) -> dict[str, Any]:
        value = {
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
    if stage_id == "law_audit":
        selected_fields -= SERVER_OWNED_LAW_AUDIT_FIELDS
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


def _json_schema_rule(rule: Mapping[str, Any]) -> dict[str, Any]:
    def normalize(item: Any) -> Any:
        if isinstance(item, Mapping):
            return _json_schema_rule(item)
        if isinstance(item, list):
            return [normalize(value) for value in item]
        return item

    value = {key: normalize(item) for key, item in rule.items() if key != "allowedValues"}
    if "allowedValues" in rule:
        value["enum"] = list(rule["allowedValues"])
    if value.get("type") == "object" and isinstance(value.get("properties"), Mapping):
        value["additionalProperties"] = False
    return value


def _without_schema_descriptions(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _without_schema_descriptions(item)
            for key, item in value.items()
            if key != "description"
        }
    if isinstance(value, list):
        return [_without_schema_descriptions(item) for item in value]
    return value


def _semantic_field_rules(
    question_id: str,
    targets: Iterable[CandidateTarget],
) -> dict[str, dict[str, Any]]:
    target_values = tuple(targets)
    allowed_targets = {target.target_id: target for target in target_values}
    result: dict[str, dict[str, Any]] = {}
    for target in target_values:
        role_rules = _FIELD_RULES_BY_ROLE.get(target.role, {})
        for field in target.allowed_fields:
            _field_destinations(question_id, field, allowed_targets)
            rule = role_rules.get(field)
            if rule is None:
                raise QuestionCandidateError(
                    f"native JSON schemaが未定義です: {question_id} / {field}"
                )
            normalized = _json_schema_rule(rule)
            previous = result.get(field)
            if (
                previous is not None
                and _without_schema_descriptions(previous)
                != _without_schema_descriptions(normalized)
            ):
                raise QuestionCandidateError(
                    f"native JSON schemaが競合しています: {question_id} / {field}"
                )
            result[field] = normalized
    return result


def _output_schema_rule(rule: Mapping[str, Any]) -> dict[str, Any]:
    """Project semantic rules into the App Server's strict output schema."""
    if isinstance(rule.get("oneOf"), list):
        return {
            key: _output_schema_rule(item)
            if isinstance(item, Mapping)
            else item
            for key, item in rule.items()
            if key != "oneOf"
        } | {
            "anyOf": [
                _output_schema_rule(variant)
                for variant in rule["oneOf"]
                if isinstance(variant, Mapping)
            ]
        }

    projected = {
        key: (
            _output_schema_rule(item)
            if isinstance(item, Mapping)
            else [
                _output_schema_rule(value)
                if isinstance(value, Mapping)
                else value
                for value in item
            ]
            if isinstance(item, list)
            else item
        )
        for key, item in rule.items()
    }
    properties = projected.get("properties")
    if projected.get("type") != "object" or not isinstance(properties, Mapping):
        return projected

    required = set(projected.get("required") or [])
    optional = sorted(set(properties) - required)
    if not optional:
        projected["required"] = list(properties)
        return projected

    variants: list[dict[str, Any]] = []
    for mask in range(1 << len(optional)):
        included = required | {
            field
            for index, field in enumerate(optional)
            if mask & (1 << index)
        }
        variant = {
            key: value
            for key, value in projected.items()
            if key not in {"description", "properties", "required"}
        }
        variant["properties"] = {
            field: value
            for field, value in properties.items()
            if field in included
        }
        variant["required"] = list(variant["properties"])
        variants.append(variant)
    result = {"anyOf": variants}
    if "description" in projected:
        result["description"] = projected["description"]
    return result


def output_schema(
    expected_question_ids: Iterable[str],
    targets_by_question: Mapping[str, Iterable[CandidateTarget]],
) -> dict[str, Any]:
    question_ids = tuple(dict.fromkeys(str(value) for value in expected_question_ids))
    if len(question_ids) != 1:
        raise QuestionCandidateError("model候補は厳密に一問でなければなりません。")
    question_id = question_ids[0]
    field_rules = _semantic_field_rules(
        question_id, targets_by_question.get(question_id, ())
    )
    field_names = sorted(field_rules)
    set_variants = [
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["field", "value"],
            "properties": {
                "field": {"type": "string", "const": field},
                "value": _output_schema_rule(rule),
            },
        }
        for field, rule in sorted(field_rules.items())
    ]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["decision", "summary", "update"],
        "properties": {
            "decision": {"type": "string", "enum": ["candidate", "blocked"]},
            "summary": {"type": "string", "minLength": 1},
            "update": {
                "type": "object",
                "additionalProperties": False,
                "required": ["setFields", "unsetFields"],
                "properties": {
                    "setFields": {
                        "type": "array",
                        "items": {"anyOf": set_variants},
                    },
                    "unsetFields": {
                        "type": "array",
                        "items": {"type": "string", "enum": field_names},
                    },
                },
            },
        },
    }


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise QuestionCandidateError(f"JSON object keyが重複しています: {key}")
        value[key] = item
    return value


def _matches_rule(value: Any, rule: Mapping[str, Any]) -> bool:
    variants = rule.get("oneOf")
    if isinstance(variants, list):
        return sum(
            _matches_rule(value, variant)
            for variant in variants
            if isinstance(variant, Mapping)
        ) == 1
    types = rule.get("type")
    allowed_types = [types] if isinstance(types, str) else list(types or [])
    matches = (
        ("null" in allowed_types and value is None)
        or ("boolean" in allowed_types and isinstance(value, bool))
        or (
            "integer" in allowed_types
            and isinstance(value, int)
            and not isinstance(value, bool)
        )
        or ("string" in allowed_types and isinstance(value, str))
        or ("array" in allowed_types and isinstance(value, list))
        or ("object" in allowed_types and isinstance(value, Mapping))
    )
    if not matches or ("enum" in rule and value not in rule["enum"]):
        return False
    if isinstance(value, str) and len(value) < int(rule.get("minLength", 0)):
        return False
    if isinstance(value, list):
        if len(value) < int(rule.get("minItems", 0)):
            return False
        if "maxItems" in rule and len(value) > int(rule["maxItems"]):
            return False
        item_rule = rule.get("items")
        if isinstance(item_rule, Mapping) and not all(
            _matches_rule(item, item_rule) for item in value
        ):
            return False
    if isinstance(value, Mapping):
        required = set(rule.get("required") or [])
        properties = rule.get("properties") or {}
        if not required <= set(value):
            return False
        if rule.get("additionalProperties") is False and not set(value) <= set(properties):
            return False
        if any(
            key in properties and not _matches_rule(item, properties[key])
            for key, item in value.items()
        ):
            return False
    return True


def parse_prepared_candidate_payload(
    payload: Mapping[str, Any],
    expected_question_ids: Iterable[str],
    targets_by_question: Mapping[str, Iterable[CandidateTarget]],
) -> tuple[QuestionCandidate, ...]:
    """Read one closed native-JSON payload persisted by the current pipeline."""
    if not isinstance(payload, Mapping):
        raise QuestionCandidateError("保存済み候補はobjectでなければなりません。")
    if payload.get("schemaVersion") != CANDIDATE_PAYLOAD_SCHEMA_VERSION:
        raise QuestionCandidateError("構造化候補のschemaVersionが一致しません。")
    _validate_v3_prepared_shape(
        payload, expected_question_ids, targets_by_question
    )
    return _parse_prepared_candidates(
        dict(payload), expected_question_ids, targets_by_question
    )


def parse_model_candidate_v3(
    raw_json: str,
    expected_question_ids: Iterable[str],
    targets_by_question: Mapping[str, Iterable[CandidateTarget]],
) -> tuple[QuestionCandidate, ...]:
    """Read one fresh semantic model response."""
    if not isinstance(raw_json, str):
        raise QuestionCandidateError("model候補はraw JSON stringでなければなりません。")
    try:
        payload = json.loads(
            raw_json, object_pairs_hook=_reject_duplicate_object_keys
        )
    except (TypeError, json.JSONDecodeError) as exc:
        raise QuestionCandidateError("model候補をJSONとして読み取れません。") from exc
    if not isinstance(payload, Mapping):
        raise QuestionCandidateError("model候補のrootがobjectではありません。")
    return _parse_semantic_candidate(
        payload, expected_question_ids, targets_by_question
    )


def _validate_v3_prepared_shape(
    payload: Mapping[str, Any],
    expected_question_ids: Iterable[str],
    targets_by_question: Mapping[str, Iterable[CandidateTarget]],
) -> None:
    if set(payload) != {"schemaVersion", "questionResults"}:
        raise QuestionCandidateError("v3保存候補のroot fieldが一致しません。")
    rows = payload.get("questionResults")
    if not isinstance(rows, list):
        raise QuestionCandidateError("v3保存候補のquestionResultsが不正です。")
    expected = {str(value) for value in expected_question_ids}
    seen_questions: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {
            "questionId", "status", "summary", "updates"
        }:
            raise QuestionCandidateError("v3保存候補のresult fieldが一致しません。")
        question_id = row.get("questionId")
        if (
            not isinstance(question_id, str)
            or question_id not in expected
            or question_id in seen_questions
        ):
            raise QuestionCandidateError("v3保存候補のquestionIdが対象外又は重複です。")
        seen_questions.add(question_id)
        updates = row.get("updates")
        status = row.get("status")
        summary = row.get("summary")
        if (
            status not in {"candidate", "blocked"}
            or not isinstance(summary, str)
            or not summary.strip()
            or not isinstance(updates, list)
        ):
            raise QuestionCandidateError("v3保存候補のupdatesが不正です。")
        allowed_targets = {
            target.target_id: target
            for target in targets_by_question.get(question_id, ())
        }
        seen_targets: set[str] = set()
        for update in updates:
            if not isinstance(update, Mapping) or set(update) != {
                "targetId", "setFields", "unsetFields"
            }:
                raise QuestionCandidateError("v3保存候補のupdate fieldが一致しません。")
            target_id = update.get("targetId")
            if (
                not isinstance(target_id, str)
                or target_id not in allowed_targets
                or target_id in seen_targets
            ):
                raise QuestionCandidateError("v3保存候補のtargetIdが対象外又は重複です。")
            seen_targets.add(target_id)
            sets = update.get("setFields")
            unsets = update.get("unsetFields")
            if not isinstance(sets, list) or not isinstance(unsets, list):
                raise QuestionCandidateError("v3保存候補のset/unsetが不正です。")
            names: list[str] = []
            for item in sets:
                if not isinstance(item, Mapping) or set(item) != {"field", "value"}:
                    raise QuestionCandidateError("v3保存候補のset fieldが不正です。")
                field = item.get("field")
                if (
                    not isinstance(field, str)
                    or field not in allowed_targets[target_id].allowed_fields
                ):
                    raise QuestionCandidateError(
                        "v3保存候補のset fieldが対象外です。"
                    )
                names.append(field)
                rule = _FIELD_RULES_BY_ROLE.get(
                    allowed_targets[target_id].role, {}
                ).get(field)
                if rule is None or not _matches_rule(
                    item.get("value"), _json_schema_rule(rule)
                ):
                    raise QuestionCandidateError(
                        f"v3保存候補のnative valueが不正です: {field}"
                    )
            if any(
                not isinstance(item, str)
                or item not in allowed_targets[target_id].allowed_fields
                for item in unsets
            ):
                raise QuestionCandidateError(
                    "v3保存候補のunset fieldが対象外又は文字列ではありません。"
                )
            unset_names = list(unsets)
            if (
                "" in names
                or "" in unset_names
                or len(names) != len(set(names))
                or len(unset_names) != len(set(unset_names))
                or set(names) & set(unset_names)
            ):
                raise QuestionCandidateError("v3保存候補のfieldが重複又は競合しています。")
        if status == "blocked" and updates:
            raise QuestionCandidateError("v3保存候補のblocked updateは空でなければなりません。")
        if status == "candidate":
            expected_targets = {
                target.target_id: set(target.allowed_fields)
                for target in targets_by_question.get(question_id, ())
            }
            actual_targets = {
                update["targetId"]: {
                    *(item["field"] for item in update["setFields"]),
                    *update["unsetFields"],
                }
                for update in updates
            }
            if actual_targets != expected_targets:
                raise QuestionCandidateError(
                    "v3保存候補が全target/allowed fieldを確定していません。"
                )
    if seen_questions != expected:
        raise QuestionCandidateError("v3保存候補に対象問題の不足があります。")


def _parse_prepared_candidates(
    value: str | Mapping[str, Any],
    expected_question_ids: Iterable[str],
    targets_by_question: Mapping[str, Iterable[CandidateTarget]],
) -> tuple[QuestionCandidate, ...]:
    try:
        payload = (
            json.loads(value, object_pairs_hook=_reject_duplicate_object_keys)
            if isinstance(value, str)
            else dict(value)
        )
    except (TypeError, json.JSONDecodeError) as exc:
        raise QuestionCandidateError("構造化候補をJSONとして読み取れません。") from exc
    if payload.get("schemaVersion") != CANDIDATE_PAYLOAD_SCHEMA_VERSION:
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
                    parsed_fields[field] = _normalized_candidate_value(
                        field,
                        item.get("value"),
                    )
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
                    destinations = _field_destinations(
                        question_id, field, allowed_targets, target
                    )
                    for destination in destinations:
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
                    destinations = _field_destinations(
                        question_id, field, allowed_targets, target
                    )
                    for destination in destinations:
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


def _parse_semantic_candidate(
    payload: Mapping[str, Any],
    expected_question_ids: Iterable[str],
    targets_by_question: Mapping[str, Iterable[CandidateTarget]],
) -> tuple[QuestionCandidate, ...]:
    expected = tuple(dict.fromkeys(str(value) for value in expected_question_ids))
    if len(expected) != 1:
        raise QuestionCandidateError("model候補は厳密に一問でなければなりません。")
    if set(payload) != {"decision", "summary", "update"}:
        raise QuestionCandidateError("model候補のroot fieldが一致しません。")
    question_id = expected[0]
    decision = payload.get("decision")
    summary = payload.get("summary")
    update = payload.get("update")
    if (
        decision not in {"candidate", "blocked"}
        or not isinstance(summary, str)
        or not summary.strip()
        or not isinstance(update, Mapping)
        or set(update) != {"setFields", "unsetFields"}
    ):
        raise QuestionCandidateError("model候補の形式が不正です。")
    set_fields = update.get("setFields")
    unset_fields = update.get("unsetFields")
    if not isinstance(set_fields, list) or not isinstance(unset_fields, list):
        raise QuestionCandidateError("model候補のupdate形式が不正です。")
    if decision == "blocked" and (set_fields or unset_fields):
        raise QuestionCandidateError("blocked候補はupdateを空にしてください。")
    rules = _semantic_field_rules(
        question_id, targets_by_question.get(question_id, ())
    )
    values: dict[str, Any] = {}
    for item in set_fields:
        if not isinstance(item, Mapping) or set(item) != {"field", "value"}:
            raise QuestionCandidateError("setFieldsのfieldが不正です。")
        field = item.get("field")
        if not isinstance(field, str) or field not in rules or field in values:
            raise QuestionCandidateError("setFieldsのfieldが対象外又は重複です。")
        if not _matches_rule(item.get("value"), rules[field]):
            raise QuestionCandidateError(f"setFields.valueの型が不正です: {field}")
        values[field] = _normalized_candidate_value(field, item.get("value"))
    if (
        any(not isinstance(field, str) or field not in rules for field in unset_fields)
        or len(unset_fields) != len(set(unset_fields))
    ):
        raise QuestionCandidateError("unsetFieldsが対象外又は重複です。")
    if set(values) & set(unset_fields):
        raise QuestionCandidateError("同じfieldに設定と削除があります。")
    if decision == "candidate" and set(values) | set(unset_fields) != set(rules):
        raise QuestionCandidateError(
            "candidateは全allowed semantic fieldを一度だけ確定してください。"
        )
    allowed_targets = {
        target.target_id: target
        for target in targets_by_question.get(question_id, ())
    }
    routed = {
        target_id: {"set": {}, "unset": []}
        for target_id in allowed_targets
    }
    for field, item in values.items():
        for target in _field_destinations(question_id, field, allowed_targets):
            routed[target.target_id]["set"][field] = item
    for field in unset_fields:
        for target in _field_destinations(question_id, field, allowed_targets):
            routed[target.target_id]["unset"].append(field)
    updates = tuple(
        CandidateUpdate(
            target_id=target_id,
            set_fields=dict(value["set"]),
            unset_fields=tuple(value["unset"]),
        )
        for target_id, value in routed.items()
        if value["set"] or value["unset"]
    )
    return (
        QuestionCandidate(
            question_id=question_id,
            status=str(decision),
            summary=summary.strip()[:4000],
            updates=updates,
        ),
    )


def validate_candidate_content(
    candidate: QuestionCandidate,
    targets: Iterable[CandidateTarget],
    projected_record: Mapping[str, Any],
    original_source_record: Mapping[str, Any] | None = None,
    source_answer_evidence: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """Run cheap deterministic checks against this question only."""

    if candidate.status == "blocked":
        return ()
    target_values = tuple(targets)
    target_by_id = {target.target_id: target for target in target_values}
    logical = json.loads(json.dumps(dict(projected_record), ensure_ascii=False))
    audit_payloads: list[Mapping[str, Any]] = []
    has_law_audit_target = any(target.role == "law_audit" for target in target_values)
    changed_fields: set[str] = set()
    set_fields: set[str] = set()
    for update in candidate.updates:
        target = target_by_id[update.target_id]
        if target.role == "law_audit":
            audit_payloads.append(update.set_fields)
            continue
        set_fields.update(str(field) for field in update.set_fields)
        changed_fields.update(str(field) for field in update.set_fields)
        changed_fields.update(update.unset_fields)
        logical.update(update.set_fields)
        for field in update.unset_fields:
            logical.pop(field, None)

    errors: list[str] = []
    independently_required_fields = {
        field
        for target in target_values
        if target.role
        in {"question_type", "question_intent", "correct_choice", "question_set"}
        for field in target.allowed_fields
    }
    missing_fields = independently_required_fields - set_fields
    if missing_fields:
        errors.append(
            "選択された更新fieldの候補がありません: "
            + ", ".join(sorted(missing_fields))
            + "。各fieldを独立に確定できない場合は、この問題をblockedにしてください。"
        )
    question_body = logical.get("questionBodyText")
    if "questionBodyText" in changed_fields and (
        not isinstance(question_body, str) or not question_body.strip()
    ):
        errors.append("questionBodyTextが非空stringではありません。")
    choices = logical.get("choiceTextList") or []
    if "choiceTextList" in changed_fields and (
        not isinstance(choices, list)
        or not choices
        or any(not isinstance(value, str) or not value.strip() for value in choices)
    ):
        errors.append("choiceTextListが非空stringの配列ではありません。")
    correct = logical.get("correctChoiceText")
    if "questionType" in changed_fields:
        question_type = logical.get("questionType")
        if question_type not in OFFICIAL_QUESTION_TYPES:
            errors.append(
                "公式問題はexamYearの有無にかかわらず、回答体験に応じて"
                "true_false、flash_card、group_choiceのいずれかに分類してください。"
            )
    if "correctChoiceText" in changed_fields and (
        not isinstance(correct, list)
        or len(correct) != len(choices)
        or any(value not in {"正しい", "間違い"} for value in correct)
    ):
        errors.append("correctChoiceTextが選択肢と同じ件数の正誤配列ではありません。")
    if (
        any(target.role == "correct_choice" for target in target_values)
        and isinstance(source_answer_evidence, Mapping)
        and source_answer_evidence.get("evidenceType")
        == "trusted_gassyunin_judge_statement_verdicts"
        and source_answer_evidence.get("verdictSemantics")
        == "final_correct_choice_text_for_source_text"
        and source_answer_evidence.get("appliesToCurrentText") is True
    ):
        expected_correct = source_answer_evidence.get("correctChoiceText")
        if isinstance(expected_correct, list) and correct != expected_correct:
            errors.append(
                "現在の問題文・選択肢は00_sourceと完全一致するため、"
                "検証済みsourceAnswerEvidenceのcorrectChoiceTextを変更できません。"
                "公式問題・解答との衝突を確認した場合は推測で上書きせず、"
                "問題単位をblockedにしてください。"
            )
    correct_shape_valid = (
        isinstance(correct, list)
        and len(correct) == len(choices)
        and all(
            value in {"正しい", "間違い", "正解", "不正解", "誤り"}
            for value in correct
        )
    )
    intent_valid = logical.get("questionIntent") in {
        "select_correct",
        "select_incorrect",
    }
    # questionTypeとquestionIntentは内容から独立に確定する。正答を所有する
    # correct_choice工程でcorrectChoiceTextが更新された後だけ、3fieldの
    # 最終整合性を機械検証する。
    final_answer_reviewed = any(
        target.role == "correct_choice" for target in target_values
    )
    if (
        final_answer_reviewed
        and "correctChoiceText" in changed_fields
        and correct_shape_valid
    ):
        if not intent_valid:
            errors.append(
                "correctChoiceTextの照合に必要なquestionIntentが"
                "select_correct又はselect_incorrectではありません。"
            )
        if intent_valid:
            answer_contract_issue = question_level_answer_cardinality_issue(
                logical.get("questionType"),
                correct,
                logical.get("questionIntent"),
            )
            if answer_contract_issue:
                errors.append(answer_contract_issue)
            official_answer_issue = official_answer_alignment_issue(logical)
            if official_answer_issue:
                errors.append(official_answer_issue)
    if any(target.role == "originalized" for target in target_values):
        try:
            validate_originalized_entry(
                original_source_record or projected_record,
                logical,
            )
        except ValueError as exc:
            errors.append(str(exc))
    explanations = logical.get("explanationText")
    if "explanationText" in changed_fields and explanations is not None:
        explanation_shape = explanation_shape_errors(
            explanations,
            question_type=logical.get("questionType"),
            choice_count=len(choices),
        )
        errors.extend(explanation_shape)
        if not explanation_shape and isinstance(explanations, list):
            errors.extend(
                explanation_style_issues(
                    explanations,
                    correct,
                    choice_texts=choices,
                    question_type=logical.get("questionType"),
                )
            )
    if "explanationReferences" in changed_fields:
        errors.extend(
            explanation_reference_errors(logical.get("explanationReferences"))
        )
    if "isCalculationQuestion" in changed_fields and not isinstance(
        logical.get("isCalculationQuestion"), bool
    ):
        errors.append("isCalculationQuestionがbooleanではありません。")
    if "questionIntent" in changed_fields and logical.get(
        "questionIntent"
    ) not in {"select_correct", "select_incorrect"}:
        errors.append(
            "questionIntentがselect_correct又はselect_incorrectではありません。"
        )
    if "questionIntent" in changed_fields:
        explicit_intent = explicit_statement_question_intent(question_body)
        if (
            explicit_intent is not None
            and logical.get("questionIntent") != explicit_intent
        ):
            source_agrees = bool(
                isinstance(original_source_record, Mapping)
                and original_source_record.get("questionBodyText")
                == projected_record.get("questionBodyText")
                and original_source_record.get("choiceTextList")
                == projected_record.get("choiceTextList")
                and original_source_record.get("questionIntent")
                == explicit_intent
            )
            errors.append(
                "questionBodyTextが選択方向を明示していますが、"
                "questionIntent候補が一致しません。選択肢が短い又は"
                "断片であることを理由に、明示された選択方向を"
                "反転しないでください。"
                + (
                    "00_sourceの同一本文・選択肢に対するquestionIntentも"
                    "明示された方向と一致しています。"
                    if source_agrees
                    else ""
                )
            )
    if "questionSetId" in changed_fields and (
        not isinstance(logical.get("questionSetId"), str)
        or not logical["questionSetId"].strip()
    ):
        errors.append("questionSetIdが非空stringではありません。")
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
                logical.get("questionIntent"),
            ),
        )
        if suggestion_errors:
            errors.append(
                "suggestedQuestionDetailsByChoiceが選択肢別・最大3件の契約を満たしません: "
                + " / ".join(suggestion_errors)
            )
    facts = logical.get("lawRevisionFacts")
    law_revision_facts_targeted = any(
        "lawRevisionFacts" in target.allowed_fields for target in target_values
    )
    validate_law_revision_facts = bool(
        has_law_audit_target
        or (
            law_revision_facts_targeted
            and logical.get("isLawRelated") is True
        )
        or (
            "lawRevisionFacts" in changed_fields
            and facts is not None
        )
    )
    if has_law_audit_target:
        if not audit_payloads:
            errors.append("監査sidecarの更新候補がありません。")
        audit = dict(audit_payloads[-1]) if audit_payloads else {}
        for field in (
            "auditStatus",
            "reviewState",
            "sourceSummary",
            "verificationSummary",
            "reconciliationStatus",
            "examTimeDecision",
            "currentLawDecision",
        ):
            value = audit.get(field)
            if value in (None, "", []):
                errors.append(f"監査sidecarの{field}がありません。")
        if (
            audit.get("auditStatus") == "updated_to_current_law"
            and audit.get("reviewState") != "tertiary_verified"
        ):
            errors.append(
                "updated_to_current_lawにはtertiary_verifiedが必要です。"
            )
    if validate_law_revision_facts:
        fact_items = (
            list(facts)
            if isinstance(facts, list)
            else [facts]
            if isinstance(facts, Mapping)
            else []
        )
        if not fact_items:
            errors.append("lawRevisionFactsを確認できません。")
        if isinstance(facts, list) and len(facts) != len(choices):
            errors.append("lawRevisionFactsが選択肢と同じ件数ではありません。")
        for index, fact in enumerate(fact_items, start=1):
            if not isinstance(fact, Mapping):
                errors.append(f"lawRevisionFacts[{index}]がobjectではありません。")
                continue
            if not is_law_revision_facts_shape(
                dict(fact),
                allow_choice_verdict_lists=True,
            ):
                details = law_revision_facts_shape_errors(
                    dict(fact),
                    allow_choice_verdict_lists=True,
                )
                errors.append(
                    f"lawRevisionFacts[{index}]が"
                    "Firestore公開契約に一致しません。"
                )
                errors.append(
                    f"lawRevisionFacts[{index}]の公開契約エラー詳細: "
                    + " / ".join(details)
                )
            if fact.get("auditStatus") not in {
                "same_as_current",
                "updated_to_current_law",
                "hold",
                "not_law_related",
            }:
                errors.append(f"lawRevisionFacts[{index}].auditStatusが不正です。")
            if not str(fact.get("reviewState") or "").strip():
                errors.append(f"lawRevisionFacts[{index}].reviewStateがありません。")
            if not isinstance(fact.get("evidenceSummary"), Mapping) or not fact.get(
                "evidenceSummary"
            ):
                errors.append(
                    f"lawRevisionFacts[{index}].evidenceSummaryが非空objectではありません。"
                )
            elif not _is_law_revision_evidence_summary(
                dict(fact["evidenceSummary"])
            ):
                errors.append(
                    f"lawRevisionFacts[{index}].evidenceSummaryが"
                    "Firestore公開契約に一致しません。"
                )
        errors.extend(
            issue["detail"]
            for issue in law_revision_current_verdict_issues(
                correct_choice_text=correct,
                law_revision_facts=facts,
            )
        )
        if logical.get("isLawRelated") is True:
            if not isinstance(law_references, list) or len(law_references) != len(
                choices
            ):
                errors.append("lawReferencesが選択肢と同じ件数ではありません。")
            else:
                per_choice_facts = (
                    fact_items
                    if isinstance(facts, list) and len(fact_items) == len(choices)
                    else None
                )
                if per_choice_facts is not None and all(
                    isinstance(fact, Mapping)
                    and fact.get("auditStatus") == "not_law_related"
                    for fact in per_choice_facts
                ):
                    errors.append(
                        "isLawRelated=trueですが、全選択肢が"
                        "not_law_relatedになっています。"
                    )
                for choice_index, references in enumerate(law_references):
                    fact = (
                        per_choice_facts[choice_index]
                        if per_choice_facts is not None
                        else None
                    )
                    choice_is_not_law_related = (
                        isinstance(fact, Mapping)
                        and fact.get("auditStatus") == "not_law_related"
                    )
                    if choice_is_not_law_related:
                        if fact.get("reviewState") != "secondary_verified":
                            errors.append(
                                f"lawRevisionFacts[{choice_index + 1}]の"
                                "not_law_relatedにはsecondary_verifiedが必要です。"
                            )
                        if not isinstance(references, list) or references:
                            errors.append(
                                f"lawReferences[{choice_index}]は"
                                "not_law_relatedの選択肢では空配列にしてください。"
                            )
                        continue
                    if not isinstance(references, list) or not references:
                        errors.append(
                            f"lawReferences[{choice_index}]にverified根拠がありません。"
                        )
                        continue
                    for reference_index, reference in enumerate(references):
                        if not isinstance(reference, Mapping):
                            errors.append(
                                "lawReferences"
                                f"[{choice_index}][{reference_index}]がobjectではありません。"
                            )
                            continue
                        missing = [
                            field
                            for field in (
                                "role",
                                "scope",
                                "lawId",
                                "lawTitle",
                                "referenceDate",
                                "article",
                                "verificationStatus",
                                "source",
                            )
                            if not str(reference.get(field) or "").strip()
                        ]
                        if missing:
                            errors.append(
                                "lawReferences"
                                f"[{choice_index}][{reference_index}]の必須fieldがありません: "
                                + ", ".join(missing)
                            )
                        if reference.get("verificationStatus") != "verified":
                            errors.append(
                                "lawReferences"
                                f"[{choice_index}][{reference_index}]がverifiedではありません。"
                            )
                        if reference.get("scope") == "choice" and reference.get(
                            "choiceIndex"
                        ) != choice_index:
                            errors.append(
                                "lawReferences"
                                f"[{choice_index}][{reference_index}].choiceIndexが一致しません。"
                            )
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
