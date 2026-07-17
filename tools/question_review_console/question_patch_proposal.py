from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tools.question_review_console.review_store import atomic_write


SCHEMA_VERSION = "question-maintenance-preparation/v1"
MAX_SUMMARY_LENGTH = 200_000


class QuestionPatchProposalError(ValueError):
    pass


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


class QuestionPatchProposalStore:
    """Persist read-only per-question preparation before the single writer runs."""

    def __init__(self, repo_root: Path, workflow_root: Path):
        self.repo_root = repo_root.resolve()
        self.workflow_root = workflow_root.resolve()
        if not self.workflow_root.is_relative_to(self.repo_root):
            raise QuestionPatchProposalError("workflow runの保存先がrepository外です。")

    def _path(self, qualification: str, run_id: str, work_item_key: str) -> Path:
        segments = (qualification, run_id, work_item_key)
        if any(
            not value
            or value in {".", ".."}
            or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in value)
            for value in segments
        ):
            raise QuestionPatchProposalError("準備記録のIDが不正です。")
        path = (
            self.workflow_root
            / qualification
            / run_id
            / "question_preparations"
            / f"{work_item_key}.json"
        ).resolve()
        expected_root = (self.workflow_root / qualification / run_id).resolve()
        if (
            not expected_root.is_relative_to(self.workflow_root)
            or not path.is_relative_to(expected_root)
        ):
            raise QuestionPatchProposalError("準備記録の保存先がrun外です。")
        return path

    def write(
        self,
        qualification: str,
        run_id: str,
        *,
        work_item_key: str,
        question_id: str,
        stage_id: str,
        input_fingerprint: str,
        summary: str,
        thread_id: str,
        session_id: str,
        turn_id: str,
    ) -> dict[str, Any]:
        normalized_summary = str(summary or "").strip()
        if not normalized_summary:
            raise QuestionPatchProposalError("一問の準備結果が空です。")
        if len(normalized_summary) > MAX_SUMMARY_LENGTH:
            raise QuestionPatchProposalError("一問の準備結果が上限を超えています。")
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "workItemKey": str(work_item_key),
            "questionId": str(question_id),
            "stageId": str(stage_id),
            "inputFingerprint": str(input_fingerprint),
            "summary": normalized_summary,
            "threadId": str(thread_id),
            "sessionId": str(session_id),
            "turnId": str(turn_id),
        }
        raw = _canonical_bytes(payload)
        path = self._path(qualification, run_id, work_item_key)
        atomic_write(path, raw.decode("utf-8"))
        return {
            "path": path.relative_to(self.repo_root).as_posix(),
            "hash": hashlib.sha256(raw).hexdigest(),
            "payload": payload,
        }

    def read(
        self,
        qualification: str,
        run_id: str,
        *,
        work_item_key: str,
        expected_hash: str,
        question_id: str,
        stage_id: str,
        input_fingerprint: str,
    ) -> dict[str, Any]:
        path = self._path(qualification, run_id, work_item_key)
        try:
            raw = path.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QuestionPatchProposalError("一問の準備記録を読み取れません。") from exc
        if not isinstance(payload, Mapping):
            raise QuestionPatchProposalError("一問の準備記録がobjectではありません。")
        if hashlib.sha256(raw).hexdigest() != str(expected_hash):
            raise QuestionPatchProposalError("一問の準備記録hashが一致しません。")
        expected = {
            "schemaVersion": SCHEMA_VERSION,
            "workItemKey": str(work_item_key),
            "questionId": str(question_id),
            "stageId": str(stage_id),
            "inputFingerprint": str(input_fingerprint),
        }
        if any(str(payload.get(key) or "") != value for key, value in expected.items()):
            raise QuestionPatchProposalError("一問の準備記録とqueue itemが一致しません。")
        if not str(payload.get("summary") or "").strip():
            raise QuestionPatchProposalError("一問の準備記録に修正案がありません。")
        return dict(payload)
