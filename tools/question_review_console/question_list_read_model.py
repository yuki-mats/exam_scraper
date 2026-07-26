from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from scripts.common.explanation_contract import expected_explanation_count
from tools.question_review_console.inventory import QuestionInventory


QUESTION_LIST_READ_MODEL_SCHEMA = "question-list-read-model-cache/v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().replace(
        microsecond=0
    ).isoformat()


def validate_question_list_read_model(
    qualification: str,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("question list read model must be an object")
    snapshot = copy.deepcopy(dict(value))
    if snapshot.get("qualification") != qualification:
        raise ValueError("question list read model qualification mismatch")
    if not isinstance(snapshot.get("listGroupIds"), list):
        raise ValueError("question list read model listGroupIds must be an array")
    if not isinstance(snapshot.get("groups"), list):
        raise ValueError("question list read model groups must be an array")
    if not isinstance(snapshot.get("questions"), list):
        raise ValueError("question list read model questions must be an array")
    return snapshot


def question_list_summary(
    question: Mapping[str, Any],
    *,
    snapshot_version: str,
) -> dict[str, Any]:
    """Project only fields rendered or filtered on the simple list screen."""

    summary = {
        key: copy.deepcopy(question.get(key))
        for key in (
            "id",
            "reviewKey",
            "sourceQuestionKey",
            "sourceRecordRef",
            "sourceStem",
            "questionLabel",
            "examLabel",
            "qualification",
            "listGroupId",
            "body",
            "contentUpdatedAt",
            "choiceCount",
            "choicesExtractedFromQuestionBody",
            "isLawRelated",
            "issues",
            "issueCodes",
            "workflow",
            "stateHash",
            "sourceCorrectChoiceComparison",
        )
    }
    summary["issues"] = list(summary.get("issues") or [])
    summary["issueCodes"] = list(summary.get("issueCodes") or [])
    projected = question.get("projected")
    projected = projected if isinstance(projected, Mapping) else {}
    summary["isCalculationQuestion"] = (
        projected.get("isCalculationQuestion") is True
    )

    workflow = question.get("workflow")
    workflow = workflow if isinstance(workflow, Mapping) else {}
    local_ready = all(
        workflow.get(stage) == "match"
        for stage in ("merge", "convert", "upload")
    )
    upload_documents = question.get("uploadReadyDocs")
    upload_documents = (
        [
            document
            for document in upload_documents
            if isinstance(document, Mapping)
        ]
        if isinstance(upload_documents, list)
        else []
    )
    if local_ready and upload_documents:
        verdicts = [
            document.get("correctChoiceText") for document in upload_documents
        ]
        explanations = [
            document.get("explanationText") for document in upload_documents
        ]
        content_source = "upload_ready"
    else:
        raw_verdicts = projected.get("correctChoiceText")
        raw_explanations = projected.get("explanationText")
        verdicts = (
            list(raw_verdicts) if isinstance(raw_verdicts, list) else []
        )
        explanations = (
            list(raw_explanations)
            if isinstance(raw_explanations, list)
            else []
        )
        content_source = "projected"
    choice_count = int(question.get("choiceCount") or len(verdicts))
    question_type = projected.get("questionType")
    if not question_type and upload_documents:
        question_type = upload_documents[0].get("questionType")
    summary["publicationSummary"] = {
        "contentSource": content_source,
        "verdicts": [str(value or "") for value in verdicts],
        "explanationCount": sum(
            bool(str(value or "").strip()) for value in explanations
        ),
        "explanationExpectedCount": expected_explanation_count(
            question_type,
            choice_count,
        ),
        "choiceCount": choice_count,
    }
    detail_version_source = {
        "stateHash": question.get("stateHash"),
        "contentUpdatedAt": question.get("contentUpdatedAt"),
        "snapshotVersion": snapshot_version,
    }
    summary["detailVersion"] = hashlib.sha256(
        json.dumps(
            detail_version_source,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
    body = str(summary.get("body") or "")
    summary["body"] = body if len(body) <= 280 else body[:279] + "…"
    return summary


def build_question_list_read_model(
    repo_root: Path,
    qualification: str,
) -> dict[str, Any]:
    """Build the complete lightweight list projection outside HTTP requests."""

    inventory = QuestionInventory(repo_root.resolve())
    qualification_info = next(
        (
            item
            for item in inventory.inventory()["qualifications"]
            if item["id"] == qualification
        ),
        None,
    )
    if qualification_info is None:
        raise FileNotFoundError(f"qualification not found: {qualification}")

    generated_at = _now_iso()
    group_ids = [
        str(value) for value in qualification_info.get("listGroupIds") or []
    ]
    groups: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    for list_group_id in group_ids:
        group = inventory.group(qualification, list_group_id)
        raw_questions = [
            value
            for value in group.get("questions") or []
            if isinstance(value, Mapping)
        ]
        groups.append(
            {
                "listGroupId": list_group_id,
                "questionCount": len(raw_questions),
                "fingerprint": str(group.get("fingerprint") or ""),
            }
        )
        questions.extend(
            question_list_summary(
                question,
                snapshot_version=generated_at,
            )
            for question in raw_questions
        )
    return {
        "qualification": qualification,
        "generatedAt": generated_at,
        "listGroupIds": group_ids,
        "groups": groups,
        "questionCount": len(questions),
        "questions": questions,
    }
