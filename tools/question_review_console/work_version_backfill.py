from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from tools.question_review_console.firestore_readback import PRODUCTION_PROJECT_ID
from tools.question_review_console.inventory import QuestionInventory
from tools.question_review_console.qualification_workflow import QualificationWorkflow
from tools.question_review_console.review_store import atomic_write
from tools.question_review_console.work_versions import (
    LEGACY_VERSION,
    QuestionWorkVersionStore,
    evaluation_policy,
)


CONFIG_DOC_ID = "08zYvCuKUcvGTNYqehrm"
OFFICIAL_EXAM_YEARS_FIELD = "official_exam_years_by_qualification"
LEGACY_FINGERPRINT = "legacy-unknown"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%dT%H%M%S%f")


def _database(credentials_json: Path | None = None) -> Any:
    import firebase_admin
    from firebase_admin import firestore
    from scripts.upload.firebase_credentials import initialize_firebase_app

    initialize_firebase_app(
        project_id=PRODUCTION_PROJECT_ID,
        credentials_json=credentials_json,
    )
    return firestore.client(app=firebase_admin.get_app())


def migrate_work_versions(repo_root: Path, *, execute: bool) -> dict[str, Any]:
    """Normalize existing local records to the MAJOR.MINOR schema."""

    repo_root = repo_root.resolve()
    result = QuestionWorkVersionStore(repo_root).migrate_all(execute=execute)
    result.update(
        {
            "status": "succeeded" if execute else "ready",
            "generatedAt": _now(),
        }
    )
    if execute:
        receipt_path = (
            repo_root
            / "output"
            / "question_review_console"
            / "work_version_migrations"
            / _timestamp()
            / "manifest.json"
        )
        result["receiptPath"] = str(receipt_path.relative_to(repo_root))
        atomic_write(
            receipt_path,
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
    return result


def invalidate_work_version_run(
    repo_root: Path,
    *,
    qualification: str,
    run_id: str,
    stage_id: str,
    reason: str,
    execute: bool,
) -> dict[str, Any]:
    """Return one run/stage to rework while preserving the invalidated record."""

    repo_root = repo_root.resolve()
    run_path = (
        repo_root
        / "output"
        / "question_review_console"
        / "workflow_runs"
        / qualification
        / run_id
        / "manifest.json"
    )
    if not run_path.is_file():
        raise ValueError(f"対象runのmanifestがありません: {run_path}")
    manifest = json.loads(run_path.read_text(encoding="utf-8"))
    if manifest.get("qualification") != qualification or manifest.get("runId") != run_id:
        raise ValueError("対象runのqualification又はrunIdが一致しません。")
    if manifest.get("status") != "succeeded":
        raise ValueError("成功済みrunだけを無効化できます。")
    policy_targets = manifest.get("policyTargets")
    target_ids = (
        policy_targets.get(stage_id)
        if isinstance(policy_targets, Mapping)
        else None
    )
    if not isinstance(target_ids, list) or not target_ids:
        raise ValueError(f"対象runに{stage_id}の完了記録がありません。")
    group_ids = [str(value) for value in manifest.get("targetGroupIds") or [] if str(value)]
    if not group_ids:
        raise ValueError("対象runにlistGroupIdがありません。")

    receipt_id = f"invalidate-{_timestamp()}"
    store = QuestionWorkVersionStore(repo_root)
    preflight_groups = [
        store.invalidate_stage_run(
            qualification,
            group_id,
            stage_id=stage_id,
            run_id=run_id,
            question_ids=target_ids,
            reason=reason,
            receipt_id=receipt_id,
            execute=False,
        )
        for group_id in group_ids
    ]
    matched_ids = {
        question_id
        for group in preflight_groups
        for question_id in group["invalidatedQuestionIds"]
    }
    target_id_set = {str(value) for value in target_ids}
    if matched_ids != target_id_set:
        missing = sorted(target_id_set - matched_ids)
        raise ValueError(
            "対象runと現在の作業バージョンが一致しません: "
            + ", ".join(missing[:10])
        )
    groups = (
        [
            store.invalidate_stage_run(
                qualification,
                group_id,
                stage_id=stage_id,
                run_id=run_id,
                question_ids=target_ids,
                reason=reason,
                receipt_id=receipt_id,
                execute=True,
            )
            for group_id in group_ids
        ]
        if execute
        else preflight_groups
    )

    invalidated_at = _now()
    result = {
        "schemaVersion": "question-work-version-invalidation/v1",
        "status": "succeeded" if execute else "ready",
        "generatedAt": invalidated_at,
        "receiptId": receipt_id,
        "qualification": qualification,
        "stageId": stage_id,
        "invalidatedRunId": run_id,
        "reason": reason,
        "targetCount": len(target_id_set),
        "invalidatedCount": len(matched_ids),
        "groups": groups,
    }
    if execute:
        receipt_path = (
            repo_root
            / "output"
            / "question_review_console"
            / "work_version_invalidations"
            / receipt_id
            / "manifest.json"
        )
        result["receiptPath"] = str(receipt_path.relative_to(repo_root))
        atomic_write(
            receipt_path,
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        manifest.update(
            {
                "status": "invalidated",
                "receiptValidated": False,
                "updatedAt": invalidated_at,
                "error": reason,
                "workVersionInvalidation": {
                    "receiptId": receipt_id,
                    "receiptPath": result["receiptPath"],
                    "stageId": stage_id,
                    "reason": reason,
                    "invalidatedAt": invalidated_at,
                },
            }
        )
        atomic_write(
            run_path,
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
    return result


def _published_qualification_ids(db: Any) -> list[str]:
    snapshot = db.collection("config").document(CONFIG_DOC_ID).get()
    if not snapshot.exists:
        raise RuntimeError("公開資格を定義するFirestore configがありません。")
    value = (snapshot.to_dict() or {}).get(OFFICIAL_EXAM_YEARS_FIELD)
    if not isinstance(value, Mapping) or not value:
        raise RuntimeError("公開資格の設定が空です。")
    return sorted(str(key) for key in value if str(key))


def _stream_published_questions(db: Any, qualification_id: str) -> list[dict[str, Any]]:
    try:
        from google.cloud.firestore_v1.base_query import FieldFilter

        query = db.collection("questions").where(
            filter=FieldFilter("qualificationId", "==", qualification_id)
        )
    except (ImportError, TypeError):
        query = db.collection("questions").where(
            "qualificationId", "==", qualification_id
        )
    query = query.select(
        [
            "qualificationId",
            "listGroupId",
            "originalQuestionId",
            "isDeleted",
            "isLawRelated",
        ]
    )
    result: list[dict[str, Any]] = []
    for snapshot in query.stream():
        value = snapshot.to_dict() or {}
        if value.get("isDeleted") is True:
            continue
        result.append({"documentId": snapshot.id, **value})
    return result


def _question_document_ids(question: Mapping[str, Any]) -> set[str]:
    return {
        str(document.get("questionId") or "")
        for document in [
            *(question.get("uploadReadyDocs") or []),
            *(question.get("convertedDocs") or []),
        ]
        if isinstance(document, Mapping) and document.get("questionId")
    }


def _candidate_rank(question: Mapping[str, Any]) -> tuple[int, int, str]:
    source_path = str(question.get("paths", {}).get("source") or "")
    return (
        int("_empty_" in Path(source_path).stem),
        -len(_question_document_ids(question)),
        str(question.get("reviewKey") or question.get("id") or ""),
    )


def backfill_published_work_versions(
    repo_root: Path,
    *,
    execute: bool,
    credentials_json: Path | None = None,
    db: Any | None = None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    database = db or _database(credentials_json)
    inventory = QuestionInventory(repo_root)
    work_versions = QuestionWorkVersionStore(repo_root)
    workflow = QualificationWorkflow(
        repo_root,
        inventory,
        work_versions=work_versions,
    )
    inventory_payload = inventory.inventory()
    local_by_publication: dict[str, list[str]] = {}
    for item in inventory_payload.get("qualifications") or []:
        local_by_publication.setdefault(
            str(item.get("publicationId") or item["id"]), []
        ).append(str(item["id"]))

    qualification_ids = _published_qualification_ids(database)
    live_by_qualification: dict[str, list[dict[str, Any]]] = {}
    active_document_count = 0
    qualification_summary: dict[str, dict[str, Any]] = {}
    for publication_id in qualification_ids:
        documents = _stream_published_questions(database, publication_id)
        live_by_qualification[publication_id] = documents
        active_document_count += len(documents)
        qualification_summary[publication_id] = {
            "activeDocumentCount": len(documents),
            "publishedQuestionCount": 0,
            "matchedQuestionCount": 0,
            "unmatchedQuestionCount": 0,
        }

    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    duplicate_resolutions: list[dict[str, Any]] = []
    for publication_id in qualification_ids:
        local_ids = local_by_publication.get(publication_id) or []
        live_documents = live_by_qualification[publication_id]
        groups = sorted(
            {
                str(document.get("listGroupId") or "")
                for document in live_documents
                if document.get("listGroupId")
            }
        )
        questions_by_group: dict[str, list[dict[str, Any]]] = {}
        for list_group_id in groups:
            for local_id in local_ids:
                try:
                    group = inventory.group(local_id, list_group_id)
                except FileNotFoundError:
                    continue
                questions_by_group.setdefault(list_group_id, []).extend(
                    group.get("questions") or []
                )
        resolved: dict[str, dict[str, Any]] = {}
        for document in live_documents:
            document_id = str(document["documentId"])
            list_group_id = str(document.get("listGroupId") or "")
            original_question_id = str(document.get("originalQuestionId") or "")
            group_questions = questions_by_group.get(list_group_id) or []
            candidates = [
                question
                for question in group_questions
                if document_id in _question_document_ids(question)
            ]
            if not candidates and original_question_id:
                candidates = [
                    question
                    for question in group_questions
                    if str(question.get("originalQuestionId") or "")
                    == original_question_id
                ]
            if not candidates:
                unmatched.append(
                    {
                        "publicationQualificationId": publication_id,
                        "listGroupId": list_group_id,
                        "originalQuestionId": original_question_id,
                        "documentId": document_id,
                        "reason": "local question not found",
                    }
                )
                continue
            candidates = sorted(candidates, key=_candidate_rank)
            chosen = candidates[0]
            if len(candidates) > 1:
                duplicate_resolutions.append(
                    {
                        "documentId": document_id,
                        "chosenReviewKey": str(chosen.get("reviewKey") or ""),
                        "candidateReviewKeys": [
                            str(question.get("reviewKey") or "")
                            for question in candidates
                        ],
                    }
                )
            review_key = str(chosen.get("reviewKey") or chosen.get("id") or "")
            entry = resolved.setdefault(review_key, dict(chosen))
            entry["isLawRelated"] = bool(
                entry.get("isLawRelated") is True
                or document.get("isLawRelated") is True
            )
        matched.extend(resolved.values())
        qualification_summary[publication_id]["publishedQuestionCount"] = len(resolved)
        qualification_summary[publication_id]["matchedQuestionCount"] = len(resolved)
        qualification_summary[publication_id]["unmatchedQuestionCount"] = len(
            {
                (
                    item["listGroupId"],
                    item["originalQuestionId"],
                )
                for item in unmatched
                if item["publicationQualificationId"] == publication_id
            }
        )

    published_keys = sorted(
        f"{publication_id}|{document['documentId']}"
        for publication_id, documents in live_by_qualification.items()
        for document in documents
    )
    unmatched_question_count = len(
        {
            (
                item["publicationQualificationId"],
                item["listGroupId"],
                item["originalQuestionId"],
            )
            for item in unmatched
        }
    )
    result: dict[str, Any] = {
        "schemaVersion": "question-work-version-backfill/v1",
        "status": "ready" if not unmatched else "blocked",
        "execute": execute,
        "projectId": PRODUCTION_PROJECT_ID,
        "createdAt": _now(),
        "qualificationIds": qualification_ids,
        "activeDocumentCount": active_document_count,
        "publishedQuestionCount": len(matched),
        "matchedQuestionCount": len(matched),
        "unmatchedQuestionCount": unmatched_question_count,
        "unmatchedDocumentCount": len(unmatched),
        "publishedScopeHash": hashlib.sha256(
            "\n".join(published_keys).encode("utf-8")
        ).hexdigest(),
        "qualifications": qualification_summary,
        "unmatched": unmatched,
        "duplicateResolutionCount": len(duplicate_resolutions),
        "duplicateResolutions": duplicate_resolutions,
        "stageReceipts": [],
    }
    if unmatched:
        return result
    if not execute:
        return result

    policies_by_qualification = {
        qualification: workflow.versioned_policies(qualification)
        for qualification in sorted(
            {str(question["qualification"]) for question in matched}
        )
    }
    stage_receipts: list[dict[str, Any]] = []
    for qualification, policies in policies_by_qualification.items():
        for stage_id, policy in policies.items():
            questions = [
                question
                for question in matched
                if question["qualification"] == qualification
            ]
            receipt = work_versions.record_stage(
                questions,
                policy,
                run_id=None,
                source="firestore_published_backfill",
                only_missing=True,
                version=LEGACY_VERSION,
                policy_fingerprint_override=LEGACY_FINGERPRINT,
            )
            stage_receipts.append({"qualification": qualification, **receipt})
    evaluation = evaluation_policy(repo_root)
    for qualification in sorted(policies_by_qualification):
        questions = [
            question
            for question in matched
            if question["qualification"] == qualification
        ]
        receipt = work_versions.record_stage(
            questions,
            evaluation,
            run_id=None,
            source="firestore_published_backfill",
            only_missing=True,
            version=LEGACY_VERSION,
            policy_fingerprint_override=LEGACY_FINGERPRINT,
        )
        stage_receipts.append({"qualification": qualification, **receipt})
    result["stageReceipts"] = stage_receipts
    result["recordedStageCount"] = sum(
        int(receipt["recordedCount"]) for receipt in stage_receipts
    )
    result["skippedStageCount"] = sum(
        int(receipt["skippedCount"]) for receipt in stage_receipts
    )
    result["status"] = "succeeded"
    receipt_path = (
        repo_root
        / "output"
        / "question_review_console"
        / "work_version_backfills"
        / _timestamp()
        / "manifest.json"
    )
    result["receiptPath"] = str(receipt_path.relative_to(repo_root))
    atomic_write(
        receipt_path,
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return result
