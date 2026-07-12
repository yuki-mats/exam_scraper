from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from tools.question_review_console.review_store import atomic_write


SCHEMA_VERSION = "local-firestore-readback/v1"


def _safe_segment(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise ValueError(f"invalid path segment: {value}")
    return value


def _hash(value: Any) -> str:
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def expected_documents(question: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    upload_ready = question.get("uploadReadyDocs")
    if isinstance(upload_ready, list) and upload_ready:
        return [item for item in upload_ready if isinstance(item, Mapping)]
    converted = question.get("convertedDocs")
    if isinstance(converted, list) and converted:
        return [item for item in converted if isinstance(item, Mapping)]
    return []


class LiveReadbackStore:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()
        self.root = (
            self.repo_root
            / "output"
            / "question_review_console"
            / "firestore_readback"
        )

    def load(self, question: Mapping[str, Any]) -> dict[str, Any] | None:
        path = self._path(question)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if payload.get("schemaVersion") != SCHEMA_VERSION:
            return None
        result = payload.get("result")
        if not isinstance(result, Mapping):
            return None
        return self.with_current_metadata(question, result, payload)

    def save(
        self, question: Mapping[str, Any], result: Mapping[str, Any]
    ) -> dict[str, Any]:
        payload = self._payload(question, result)
        atomic_write(
            self._path(question),
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        return self.with_current_metadata(question, payload["result"], payload)

    def load_manifest(self, qualification: str) -> dict[str, Any] | None:
        path = self._manifest_path(qualification)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if payload.get("schemaVersion") != SCHEMA_VERSION:
            return None
        return copy.deepcopy(payload)

    def save_manifest(
        self, qualification: str, readback: Mapping[str, Any]
    ) -> dict[str, Any]:
        stored_at = str(readback.get("readAt") or _now())
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "qualification": qualification,
            "projectId": str(readback.get("projectId") or ""),
            "storedAt": stored_at,
            "groupCount": int(readback.get("groupCount") or 0),
            "questionCount": int(readback.get("questionCount") or 0),
            "documentCount": int(readback.get("documentCount") or 0),
            "unavailableQuestionCount": int(
                readback.get("unavailableQuestionCount") or 0
            ),
            "statusCounts": copy.deepcopy(dict(readback.get("statusCounts") or {})),
            "groups": copy.deepcopy(list(readback.get("groups") or [])),
        }
        atomic_write(
            self._manifest_path(qualification),
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        return payload

    def with_current_metadata(
        self,
        question: Mapping[str, Any],
        result: Mapping[str, Any],
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        current_hash = self.expected_hash(question)
        embedded = result.get("readbackMeta")
        base = payload or (embedded if isinstance(embedded, Mapping) else {}) or {}
        metadata = {
            "qualification": str(
                base.get("qualification") or question.get("qualification") or ""
            ),
            "listGroupId": str(
                base.get("listGroupId") or question.get("listGroupId") or ""
            ),
            "questionId": str(base.get("questionId") or question.get("id") or ""),
            "reviewKey": str(
                base.get("reviewKey") or question.get("reviewKey") or ""
            ),
            "projectId": str(base.get("projectId") or result.get("projectId") or ""),
            "expectedHash": str(base.get("expectedHash") or ""),
            "currentExpectedHash": current_hash,
            "stateHash": str(base.get("stateHash") or ""),
            "currentStateHash": str(question.get("stateHash") or ""),
            "expectedSource": str(
                base.get("expectedSource") or result.get("expectedSource") or ""
            ),
            "storedAt": str(base.get("storedAt") or ""),
        }
        metadata["stale"] = bool(
            metadata["expectedHash"] and metadata["expectedHash"] != current_hash
        )
        decorated = copy.deepcopy(dict(result))
        decorated["readbackMeta"] = metadata
        return decorated

    def expected_hash(self, question: Mapping[str, Any]) -> str:
        return _hash(expected_documents(question))

    def _payload(
        self, question: Mapping[str, Any], result: Mapping[str, Any]
    ) -> dict[str, Any]:
        stored_at = str(result.get("readAt") or _now())
        expected_hash = self.expected_hash(question)
        result_payload = copy.deepcopy(dict(result))
        metadata = {
            "qualification": str(question["qualification"]),
            "listGroupId": str(question["listGroupId"]),
            "questionId": str(question["id"]),
            "reviewKey": str(question.get("reviewKey") or ""),
            "projectId": str(result.get("projectId") or ""),
            "expectedHash": expected_hash,
            "currentExpectedHash": expected_hash,
            "stateHash": str(question.get("stateHash") or ""),
            "currentStateHash": str(question.get("stateHash") or ""),
            "expectedSource": str(result.get("expectedSource") or ""),
            "storedAt": stored_at,
            "stale": False,
        }
        result_payload["readbackMeta"] = metadata
        return {
            "schemaVersion": SCHEMA_VERSION,
            "qualification": str(question["qualification"]),
            "listGroupId": str(question["listGroupId"]),
            "questionId": str(question["id"]),
            "reviewKey": str(question.get("reviewKey") or ""),
            "projectId": str(result.get("projectId") or ""),
            "expectedHash": expected_hash,
            "stateHash": str(question.get("stateHash") or ""),
            "expectedSource": str(result.get("expectedSource") or ""),
            "storedAt": stored_at,
            "result": result_payload,
        }

    def _path(self, question: Mapping[str, Any]) -> Path:
        return (
            self.root
            / _safe_segment(str(question["qualification"]))
            / _safe_segment(str(question["listGroupId"]))
            / f"{_safe_segment(str(question['id']))}.json"
        )

    def _manifest_path(self, qualification: str) -> Path:
        return self.root / _safe_segment(qualification) / "manifest.json"
