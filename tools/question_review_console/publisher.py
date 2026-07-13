from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

from scripts.upload.upload_questions_to_firestore import (
    DOC_COMPARE_KEYS,
    build_doc_data_base,
    validate_required_question_fields,
)
from tools.question_review_console.firestore_readback import (
    PRODUCTION_PROJECT_ID,
    FirestoreReadback,
)
from tools.question_review_console.inventory import QuestionInventory
from tools.question_review_console.workflow_runner import (
    LOCAL_STALE_ISSUES,
    aggregate_group_workflow,
)


class PublicationError(RuntimeError):
    pass


class GroupPublisher:
    def __init__(
        self,
        repo_root: Path,
        inventory: QuestionInventory,
        firestore: FirestoreReadback,
        secret: str,
        *,
        command_runner: Callable[..., int] | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.inventory = inventory
        self.firestore = firestore
        self.secret = secret.encode("utf-8")
        self.command_runner = command_runner or self._run_command

    def preview(self, qualification: str, list_group_id: str) -> dict[str, Any]:
        group = self.inventory.group(qualification, list_group_id)
        local = aggregate_group_workflow(group)
        issue_counts = self._blocking_issue_counts(group)
        if not local["localReady"] or issue_counts:
            return {
                "qualification": qualification,
                "listGroupId": list_group_id,
                "projectId": PRODUCTION_PROJECT_ID,
                "localReady": local["localReady"],
                "stages": local["stages"],
                "blockingIssues": issue_counts,
                "canPublish": False,
                "reason": (
                    "Merge・Convert・upload-readyを先に同期してください。"
                    if not local["localReady"]
                    else "公開を停止する要確認項目が残っています。"
                ),
            }

        path, documents, artifact_hash = self._load_artifact(
            group, qualification, list_group_id
        )
        try:
            live = self.firestore.read_documents(
                [str(document["questionId"]) for document in documents],
                fields=DOC_COMPARE_KEYS,
            )
        except Exception as exc:  # noqa: BLE001
            raise PublicationError("Firestoreの差分取得に失敗しました。") from exc
        changed: list[dict[str, Any]] = []
        missing: list[str] = []
        for document in documents:
            question_id = str(document["questionId"])
            existing = live.get(question_id)
            base = build_doc_data_base(document)
            if existing is None:
                missing.append(question_id)
                changed.append({"questionId": question_id, "fields": ["document"]})
                continue
            fields = [
                field
                for field in DOC_COMPARE_KEYS
                if field in base and existing.get(field) != base.get(field)
            ]
            if fields:
                changed.append({"questionId": question_id, "fields": fields})

        token_payload = {
            "qualification": qualification,
            "listGroupId": list_group_id,
            "projectId": PRODUCTION_PROJECT_ID,
            "artifactHash": artifact_hash,
            "changed": changed,
            "missing": missing,
        }
        return {
            "qualification": qualification,
            "listGroupId": list_group_id,
            "projectId": PRODUCTION_PROJECT_ID,
            "localReady": True,
            "blockingIssues": {},
            "artifactPath": str(path.relative_to(self.repo_root)),
            "artifactHash": artifact_hash,
            "documentCount": len(documents),
            "changedCount": len(changed),
            "missingCount": len(missing),
            "unchangedCount": len(documents) - len(changed),
            "changes": changed[:100],
            "canPublish": bool(changed),
            "status": "mismatch" if changed else "match",
            "preflightToken": self._token(token_payload),
            "alsoUpdatesOfficialExamYearsManifest": True,
        }

    def run(
        self,
        qualification: str,
        list_group_id: str,
        preflight: Mapping[str, Any],
        emit: Callable[[str], None],
    ) -> dict[str, Any]:
        current = self.preview(qualification, list_group_id)
        if not self.token_matches(current, str(preflight.get("preflightToken") or "")):
            raise PublicationError("実行直前に成果物又はFirestoreが更新されました。")
        if not current.get("canPublish"):
            raise PublicationError("実行直前の確認で本番反映対象がなくなりました。")
        preflight = current
        path = (self.repo_root / str(preflight["artifactPath"])).resolve()
        if not path.is_relative_to(self.repo_root) or not path.is_file():
            raise PublicationError("upload-ready成果物がありません。")
        if self._file_hash(path) != preflight.get("artifactHash"):
            raise PublicationError("確認後にupload-readyが更新されました。")

        emit(f"本番反映: {qualification} / {list_group_id}")
        emit(f"対象document: {preflight.get('changedCount', 0)} / {preflight.get('documentCount', 0)}")
        command = [
            sys.executable,
            str(self.repo_root / "scripts" / "upload" / "upload_questions_to_firestore.py"),
            str(path),
        ]
        return_code = self.command_runner(
            command,
            cwd=self.repo_root,
            env=self._environment(),
            emit=emit,
        )
        if return_code != 0:
            raise PublicationError(f"Firestore反映に失敗しました（exit={return_code}）。")

        verification = self.preview(qualification, list_group_id)
        if verification.get("changedCount") != 0 or verification.get("missingCount") != 0:
            raise PublicationError("upload後のreadbackで差分が残っています。")
        emit("本番Firestoreとupload-readyの一致を確認しました。")
        return {
            **verification,
            "publishedCount": int(preflight.get("changedCount") or 0),
            "message": "本番Firestoreへ反映し、readbackまで完了しました。",
        }

    def token_matches(self, preview: Mapping[str, Any], token: str) -> bool:
        expected = str(preview.get("preflightToken") or "")
        return bool(expected and hmac.compare_digest(expected, token))

    def _load_artifact(
        self,
        group: Mapping[str, Any],
        qualification: str,
        list_group_id: str,
    ) -> tuple[Path, list[dict[str, Any]], str]:
        paths = {
            str(question.get("paths", {}).get("uploadReady") or "")
            for question in group.get("questions") or []
            if question.get("paths", {}).get("uploadReady")
        }
        if len(paths) != 1:
            raise PublicationError("upload-ready成果物を一意に特定できません。")
        path = (self.repo_root / next(iter(paths))).resolve()
        expected_root = (
            self.repo_root / "output" / qualification / "questions_json" / "upload_to_firestore"
        ).resolve()
        if not path.is_relative_to(expected_root) or not path.is_file():
            raise PublicationError("upload-ready成果物のパスが不正です。")
        flags = int(getattr(path.stat(), "st_flags", 0))
        if flags & int(getattr(stat, "SF_DATALESS", 0)):
            raise PublicationError("upload-readyがGoogle Drive上で未ダウンロードです。")
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_documents = payload.get("questions") if isinstance(payload, dict) else None
        if not isinstance(raw_documents, list) or not raw_documents:
            raise PublicationError("upload-readyにquestionsがありません。")
        documents = [copy.deepcopy(value) for value in raw_documents if isinstance(value, dict)]
        validate_required_question_fields(documents, str(path))
        publication_qualification_id = str(
            group.get("publicationQualificationId") or qualification
        )
        ids: set[str] = set()
        for document in documents:
            question_id = str(document.get("questionId") or "")
            if question_id in ids:
                raise PublicationError(f"questionIdが重複しています: {question_id}")
            ids.add(question_id)
            if document.get("qualificationId") != publication_qualification_id:
                raise PublicationError("upload-readyに別資格のdocumentが含まれています。")
            if str(document.get("listGroupId") or "") != list_group_id:
                raise PublicationError("upload-readyに別フォルダのdocumentが含まれています。")
        return path, documents, self._file_hash(path)

    @staticmethod
    def _blocking_issue_counts(group: Mapping[str, Any]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for question in group.get("questions") or []:
            for code in question.get("issueCodes") or []:
                if code in LOCAL_STALE_ISSUES:
                    continue
                counts[code] = counts.get(code, 0) + 1
        return dict(sorted(counts.items()))

    @staticmethod
    def _file_hash(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _token(self, payload: Mapping[str, Any]) -> str:
        value = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hmac.new(self.secret, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env["FIREBASE_PROJECT_ID"] = PRODUCTION_PROJECT_ID
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONPATH"] = os.pathsep.join(
            value
            for value in (str(self.repo_root), env.get("PYTHONPATH"))
            if value
        )
        return env

    @staticmethod
    def _run_command(
        command: list[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        emit: Callable[[str], None],
    ) -> int:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=dict(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            emit(line)
        return process.wait()
