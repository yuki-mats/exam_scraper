from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SUBMISSION_PATH_RE = re.compile(
    r"^users/[^/]+/questionIssueReportSubmissions/[^/]+$"
)
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


def validate_operational_case(case: Mapping[str, Any]) -> None:
    required_strings = (
        "id",
        "workflowStatus",
        "questionId",
        "originalQuestionId",
        "qualificationId",
        "listGroupId",
        "category",
        "currentContentHash",
    )
    missing = [
        field
        for field in required_strings
        if not isinstance(case.get(field), str) or not str(case[field]).strip()
    ]
    if missing:
        raise ValueError(f"question issue case missing required strings: {missing}")
    category = str(case["category"])
    if case.get("categories") != [category]:
        raise ValueError("question issue case must contain exactly its scoped category")
    if not SHA256_RE.fullmatch(str(case["currentContentHash"])):
        raise ValueError("question issue case currentContentHash must be sha256")
    if case.get("schemaVersion") != 1:
        raise ValueError("question issue case schemaVersion must be 1")
    if not isinstance(case.get("canonicalSnapshot"), dict):
        raise ValueError("question issue case canonicalSnapshot must be an object")


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(child) for child in value]
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            pass
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class FixtureReportStore:
    def __init__(self, fixture_path: Path):
        self.fixture_path = fixture_path
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("report fixture must be an object")
        cases = payload.get("cases")
        if not isinstance(cases, list):
            raise ValueError("report fixture cases must be a list")
        self._cases = {
            str(case.get("id")): dict(case)
            for case in cases
            if isinstance(case, dict) and str(case.get("id") or "").strip()
        }
        for case in self._cases.values():
            validate_operational_case(case)
        self._case_reports = payload.get("caseReports") or {}
        self._submissions = payload.get("submissions") or {}
        self._questions = payload.get("questions") or {}
        self._active_batch_id: str | None = None
        self._active_manifest_hash: str | None = None

    def list_cases(self) -> list[dict[str, Any]]:
        return [dict(case) for case in self._cases.values()]

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        case = self._cases.get(case_id)
        return dict(case) if case is not None else None

    def claims_for_case(self, case_id: str) -> list[dict[str, Any]]:
        reports = self._case_reports.get(case_id) or []
        claims: list[dict[str, Any]] = []
        for report in reports:
            if not isinstance(report, dict):
                continue
            source_path = str(report.get("sourceSubmissionPath") or "")
            if not SUBMISSION_PATH_RE.fullmatch(source_path):
                continue
            submission = self._submissions.get(source_path)
            if not isinstance(submission, dict):
                continue
            claims.append(
                {
                    "reportId": str(submission.get("reportId") or ""),
                    "categories": list(submission.get("categories") or []),
                    "detailComment": str(submission.get("detailComment") or ""),
                    "questionContentHash": str(
                        submission.get("questionContentHash") or ""
                    ),
                    "imageLoadEvents": list(
                        submission.get("imageLoadEvents") or []
                    ),
                }
            )
        return claims

    def claim_case(
        self,
        case_id: str,
        *,
        batch_id: str,
        expected_current_hash: str,
    ) -> bool:
        case = self._cases.get(case_id)
        if case is None:
            return False
        if str(case.get("currentContentHash") or "") != expected_current_hash:
            return False
        if (
            case.get("workflowStatus") == "in_batch"
            and case.get("activeBatchId") == batch_id
        ):
            return True
        if case.get("workflowStatus") != "unreviewed":
            return False
        case["workflowStatus"] = "in_batch"
        case["activeBatchId"] = batch_id
        return True

    def begin_batch(self, batch_id: str, manifest_hash: str) -> bool:
        if self._active_batch_id not in (None, batch_id):
            return False
        if (
            self._active_batch_id == batch_id
            and self._active_manifest_hash != manifest_hash
        ):
            return False
        self._active_batch_id = batch_id
        self._active_manifest_hash = manifest_hash
        return True

    def finish_batch(self, batch_id: str, result: Mapping[str, Any]) -> None:
        if self._active_batch_id == batch_id:
            self._active_batch_id = None
            self._active_manifest_hash = None

    def complete_case(
        self,
        case_id: str,
        *,
        workflow_status: str,
        operational_result: Mapping[str, Any],
    ) -> None:
        case = self._cases[case_id]
        case["workflowStatus"] = workflow_status
        case["operationalResult"] = _json_value(dict(operational_result))
        case.pop("activeBatchId", None)

    def release_case(
        self,
        case_id: str,
        *,
        batch_id: str,
        machine_reason: str,
    ) -> None:
        case = self._cases[case_id]
        if case.get("activeBatchId") == batch_id:
            case["workflowStatus"] = "unreviewed"
            case["lastProcessingError"] = {"machineReason": machine_reason}
            case.pop("activeBatchId", None)

    def question_documents(self, question_ids: list[str]) -> dict[str, dict[str, Any]]:
        return {
            question_id: dict(self._questions[question_id])
            for question_id in question_ids
            if isinstance(self._questions.get(question_id), dict)
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "cases": self.list_cases(),
            "caseReports": self._case_reports,
            "submissions": self._submissions,
            "questions": self._questions,
        }


class FirestoreReportStore:
    def __init__(
        self,
        *,
        credentials_json: Path | None = None,
        project_id: str | None = None,
    ):
        from scripts.upload.firebase_credentials import (
            DEFAULT_PROJECT_ID,
            initialize_firebase_app,
        )

        initialize_firebase_app(
            project_id=project_id or DEFAULT_PROJECT_ID,
            credentials_json=credentials_json,
        )
        from firebase_admin import firestore

        self._firestore = firestore
        self._db = firestore.client()

    def list_cases(self) -> list[dict[str, Any]]:
        cases: list[dict[str, Any]] = []
        for snapshot in self._db.collection("questionIssueReportCases").stream():
            data = snapshot.to_dict() or {}
            case = {"id": snapshot.id, **_json_value(data)}
            validate_operational_case(case)
            cases.append(case)
        return cases

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        snapshot = self._db.collection("questionIssueReportCases").document(case_id).get()
        if not snapshot.exists:
            return None
        case = {"id": snapshot.id, **_json_value(snapshot.to_dict() or {})}
        validate_operational_case(case)
        return case

    def claims_for_case(self, case_id: str) -> list[dict[str, Any]]:
        case_ref = self._db.collection("questionIssueReportCases").document(case_id)
        claims: list[dict[str, Any]] = []
        for report in case_ref.collection("reports").limit(100).stream():
            report_data = report.to_dict() or {}
            source_path = str(report_data.get("sourceSubmissionPath") or "")
            if not SUBMISSION_PATH_RE.fullmatch(source_path):
                continue
            submission = self._db.document(source_path).get()
            if not submission.exists:
                continue
            data = submission.to_dict() or {}
            claims.append(
                {
                    "reportId": str(data.get("reportId") or ""),
                    "categories": list(data.get("categories") or []),
                    "detailComment": str(data.get("detailComment") or ""),
                    "questionContentHash": str(
                        data.get("questionContentHash") or ""
                    ),
                    "imageLoadEvents": _json_value(
                        list(data.get("imageLoadEvents") or [])
                    ),
                }
            )
        return claims

    def claim_case(
        self,
        case_id: str,
        *,
        batch_id: str,
        expected_current_hash: str,
    ) -> bool:
        case_ref = self._db.collection("questionIssueReportCases").document(case_id)
        transaction = self._db.transaction()
        firestore = self._firestore

        @firestore.transactional
        def claim(transaction):
            snapshot = case_ref.get(transaction=transaction)
            if not snapshot.exists:
                return False
            data = snapshot.to_dict() or {}
            if str(data.get("currentContentHash") or "") != expected_current_hash:
                return False
            if (
                data.get("workflowStatus") == "in_batch"
                and data.get("activeBatchId") == batch_id
            ):
                return True
            if data.get("workflowStatus") != "unreviewed":
                return False
            transaction.update(
                case_ref,
                {
                    "workflowStatus": "in_batch",
                    "activeBatchId": batch_id,
                    "batchClaimedAt": firestore.SERVER_TIMESTAMP,
                },
            )
            return True

        return bool(claim(transaction))

    def begin_batch(self, batch_id: str, manifest_hash: str) -> bool:
        lock_ref = self._db.collection("questionIssueReportControl").document(
            "activeBatch"
        )
        batch_ref = self._db.collection("questionIssueReportBatches").document(batch_id)
        transaction = self._db.transaction()
        firestore = self._firestore

        @firestore.transactional
        def begin(transaction):
            snapshot = lock_ref.get(transaction=transaction)
            if snapshot.exists:
                data = snapshot.to_dict() or {}
                if data.get("status") == "active":
                    if data.get("batchId") != batch_id:
                        return False
                    if data.get("manifestHash") != manifest_hash:
                        return False
            transaction.set(
                lock_ref,
                {
                    "status": "active",
                    "batchId": batch_id,
                    "manifestHash": manifest_hash,
                    "updatedAt": firestore.SERVER_TIMESTAMP,
                },
            )
            transaction.set(
                batch_ref,
                {
                    "status": "active",
                    "manifestHash": manifest_hash,
                    "startedAt": firestore.SERVER_TIMESTAMP,
                    "updatedAt": firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )
            return True

        return bool(begin(transaction))

    def finish_batch(self, batch_id: str, result: Mapping[str, Any]) -> None:
        lock_ref = self._db.collection("questionIssueReportControl").document(
            "activeBatch"
        )
        batch_ref = self._db.collection("questionIssueReportBatches").document(batch_id)
        transaction = self._db.transaction()
        firestore = self._firestore

        @firestore.transactional
        def finish(transaction):
            snapshot = lock_ref.get(transaction=transaction)
            if snapshot.exists:
                data = snapshot.to_dict() or {}
                if data.get("status") == "active" and data.get("batchId") != batch_id:
                    raise RuntimeError("active question issue batch changed before finish")
            transaction.set(
                lock_ref,
                {
                    "status": "idle",
                    "batchId": batch_id,
                    "updatedAt": firestore.SERVER_TIMESTAMP,
                },
            )
            transaction.set(
                batch_ref,
                {
                    "status": "completed",
                    "result": _json_value(dict(result)),
                    "completedAt": firestore.SERVER_TIMESTAMP,
                    "updatedAt": firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )

        finish(transaction)

    def complete_case(
        self,
        case_id: str,
        *,
        workflow_status: str,
        operational_result: Mapping[str, Any],
    ) -> None:
        case_ref = self._db.collection("questionIssueReportCases").document(case_id)
        case_ref.update(
            {
                "workflowStatus": workflow_status,
                "operationalResult": _json_value(dict(operational_result)),
                "activeBatchId": self._firestore.DELETE_FIELD,
                "reviewedAt": self._firestore.SERVER_TIMESTAMP,
                "updatedAt": self._firestore.SERVER_TIMESTAMP,
            }
        )

    def release_case(
        self,
        case_id: str,
        *,
        batch_id: str,
        machine_reason: str,
    ) -> None:
        case_ref = self._db.collection("questionIssueReportCases").document(case_id)
        transaction = self._db.transaction()
        firestore = self._firestore

        @firestore.transactional
        def release(transaction):
            snapshot = case_ref.get(transaction=transaction)
            if not snapshot.exists:
                return
            data = snapshot.to_dict() or {}
            if data.get("activeBatchId") != batch_id:
                return
            transaction.update(
                case_ref,
                {
                    "workflowStatus": "unreviewed",
                    "activeBatchId": firestore.DELETE_FIELD,
                    "lastProcessingError": {"machineReason": machine_reason},
                    "lastProcessingFailedAt": firestore.SERVER_TIMESTAMP,
                    "updatedAt": firestore.SERVER_TIMESTAMP,
                },
            )

        release(transaction)

    def question_documents(self, question_ids: list[str]) -> dict[str, dict[str, Any]]:
        refs = [self._db.collection("questions").document(question_id) for question_id in question_ids]
        result: dict[str, dict[str, Any]] = {}
        for snapshot in self._db.get_all(refs):
            if snapshot.exists:
                result[snapshot.id] = _json_value(snapshot.to_dict() or {})
        return result
