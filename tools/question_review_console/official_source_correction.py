from __future__ import annotations

import copy
import hashlib
import json
import mimetypes
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Mapping

from scripts.common.image_storage_urls import (
    FIREBASE_STORAGE_BUCKET,
    build_public_storage_url,
    build_storage_object_path,
)
from scripts.common.question_identity import (
    SourceIdentityBinding,
    SourceRecordIdentity,
    load_source_record_inventory,
)
from scripts.upload.upload_question_images_to_storage import make_storage_bucket
from tools.question_bank.question_issue_reports import (
    DEFAULT_CONFIG_PATH,
    ReviewExecutor,
    _extract_json_text,
    build_correction_patch,
    build_current_answer_certification_patch,
    correction_patch_filename,
    find_current_question_record,
    load_config,
    run_objective_review,
    sha256_json,
    utc_now_text,
    verify_patch_against_record,
    write_append_only_private_json,
    write_private_json,
)
from tools.question_review_console.projection import PROJECTED_COMPARE_FIELDS


ALLOWED_EVIDENCE_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
MAX_EVIDENCE_BYTES = 50 * 1024 * 1024
SAFE_ID = re.compile(r"[^A-Za-z0-9_-]+")
PDF_PAGE_LOCATOR = re.compile(r"PDF\s*(\d+)\s*ページ", re.IGNORECASE)


class OfficialSourceCorrectionError(ValueError):
    pass


class _EmptyClaimsStore:
    @staticmethod
    def claims_for_case(_case_id: str) -> list[dict[str, Any]]:
        return []


class AppServerReviewExecutor(ReviewExecutor):
    """Run the existing blind/challenge protocol in read-only App Server turns."""

    def __init__(
        self,
        app_server: Any,
        *,
        repo_root: Path,
        qualification: str,
        current_record: Mapping[str, Any],
        evidence_hash: str,
        evidence_title: str,
        evidence_locator: str,
        evidence_relative_path: str,
        evidence_verified_at: str,
        emit: Callable[[str], None],
        work_dir: Path | None = None,
    ):
        super().__init__(
            command=None,
            recorded_results_dir=None,
            allow_fixture_placeholders=False,
        )
        self.app_server = app_server
        self.repo_root = repo_root
        self.qualification = qualification
        self.current_record = dict(current_record)
        self.evidence_hash = evidence_hash
        self.evidence_title = evidence_title
        self.evidence_verified_at = evidence_verified_at
        self.work_dir = (work_dir or repo_root / ".official_source_attempts").resolve()
        self._pending_attempts: dict[str, dict[str, Any]] = {}
        self.canonical_evidence_locator = (
            f"{evidence_relative_path} / {evidence_locator}"
        )
        self.emit = emit

    def execute(
        self,
        *,
        work_id: str,
        phase: str,
        prompt: str,
        replacements: Mapping[str, Any],
    ) -> dict[str, Any]:
        attempt_binding = dict(replacements.get("$ATTEMPT_BINDING") or {})
        self.emit(f"{phase}: 公式資料との独立照合を開始します。")
        result = self.app_server.run_turn(
            prompt,
            work_type="official_source_review",
            sandbox="read-only",
            emit=lambda message: self.emit(f"{phase}: {message}"),
            cwd=self.repo_root,
            turn_group=self.qualification,
            monitor_context={
                "qualification": self.qualification,
                "stageCode": "24",
                "workItemId": work_id,
                "phase": phase,
            },
        )
        changed_files = tuple(getattr(result, "changed_files", ()) or ())
        if changed_files:
            raise OfficialSourceCorrectionError(
                f"{phase}のread-only reviewがfile変更を報告しました。"
            )
        raw = str(getattr(result, "final_message", "") or "")
        parsed: dict[str, Any] | None = None
        normalized: dict[str, Any] | None = None
        removed_noop_fields: list[str] = []
        identity = {
            key: getattr(result, key, None)
            for key in ("session_id", "thread_id", "turn_id")
        }
        try:
            parsed = _extract_json_text(raw)
            normalized = copy.deepcopy(parsed)
            removed_noop_fields = self._normalize_review_payload(
                normalized, phase=phase
            )
        except Exception as exc:
            received = {
                "binding": attempt_binding,
                "raw": raw,
                "parsed": parsed,
                "normalized": normalized,
                "removedNoopFields": removed_noop_fields,
                **identity,
            }
            received["receiptHash"] = sha256_json(received)
            received_path = write_append_only_private_json(
                self.work_dir / "attempts", f"{phase}_received", received
            )
            write_append_only_private_json(
                self.work_dir / "attempts",
                f"{phase}_validation",
                {
                    "receivedReceipt": str(received_path),
                    "receivedReceiptHash": received["receiptHash"],
                    "validation": "failed",
                    "error": str(exc),
                },
            )
            raise
        self._pending_attempts[phase] = {
            "binding": attempt_binding,
            "raw": raw,
            "parsed": parsed,
            "normalized": normalized,
            "removedNoopFields": removed_noop_fields,
            **identity,
        }
        received = self._pending_attempts[phase]
        received["receiptHash"] = sha256_json(received)
        received_path = write_append_only_private_json(
            self.work_dir / "attempts", f"{phase}_received", received
        )
        self._pending_attempts[phase] = {
            "receivedReceipt": str(received_path),
            "receivedReceiptHash": received["receiptHash"],
        }
        self.emit(f"{phase}: 構造化結果を受領しました。")
        return normalized

    def finish_attempt(self, *, phase: str, validation_error: str | None) -> None:
        attempt = self._pending_attempts.pop(phase, None)
        if attempt is None:
            return
        write_append_only_private_json(
            self.work_dir / "attempts",
            f"{phase}_validation",
            {
                **attempt,
                "validation": "failed" if validation_error else "validated",
                "error": validation_error,
            },
        )

    def _normalize_review_payload(
        self,
        payload: dict[str, Any],
        *,
        phase: str,
    ) -> list[str]:
        change_field = "changes" if phase == "challenge" else "proposedChanges"
        changes = payload.get(change_field)
        removed: list[str] = []
        if isinstance(changes, Mapping):
            removed = [
                key for key, value in changes.items() if self.current_record.get(key) == value
            ]
            payload[change_field] = {
                key: value
                for key, value in changes.items()
                if self.current_record.get(key) != value
            }
        # Evidence provenance is fixed by the server before the model turn. The
        # reviewer owns only the conclusion and proposed changes; it must not be
        # able to add, omit, or mistype evidence metadata and its hashes.
        payload["evidence"] = [
            {
                "sourceClass": "official",
                "title": self.evidence_title,
                "locator": self.canonical_evidence_locator,
                "verifiedAt": self.evidence_verified_at,
                "contentHash": self.evidence_hash,
            }
        ]
        return sorted(removed)


class OfficialSourceCorrectionService:
    """Create one verified 24_questionIssueCorrections overlay from the UI."""

    def __init__(
        self,
        repo_root: Path,
        *,
        app_server: Any,
        config_path: Path | None = None,
        review_runner: Callable[..., Any] = run_objective_review,
        record_finder: Callable[..., Any] = find_current_question_record,
        patch_verifier: Callable[..., Any] = verify_patch_against_record,
        page_renderer: Callable[[Path, int, Path], None] | None = None,
        image_publisher: Callable[..., Mapping[str, str]] | None = None,
    ):
        self.repo_root = repo_root.resolve()
        self.app_server = app_server
        self.config_path = (config_path or DEFAULT_CONFIG_PATH).resolve()
        self.config = load_config(self.config_path)
        self.review_runner = review_runner
        self.record_finder = record_finder
        self._uses_server_owned_projection = record_finder is find_current_question_record
        self.patch_verifier = patch_verifier
        self.page_renderer = page_renderer or self._render_pdf_page
        self.image_publisher = image_publisher or self._publish_question_image

    def _server_owned_current_record(
        self,
        question: Mapping[str, Any],
        *,
        qualification: str,
        list_group_id: str,
        state_hash: str,
    ) -> tuple[dict[str, Any], Path, SourceRecordIdentity]:
        projected = question.get("projected")
        if not isinstance(projected, Mapping):
            raise OfficialSourceCorrectionError(
                "server生成のprojected問題がないため公式資料を照合できません。"
            )
        current_state_hash = self._required_text(question, "stateHash")
        if state_hash != current_state_hash:
            raise OfficialSourceCorrectionError(
                "画面表示後に問題内容が更新されました。問題を開き直してください。"
            )
        projected_hash = sha256_json(
            {field: projected.get(field) for field in PROJECTED_COMPARE_FIELDS}
        )
        if projected_hash != current_state_hash:
            raise OfficialSourceCorrectionError(
                "server生成のprojected問題hashがstateHashと一致しません。"
            )
        requested_binding = SourceIdentityBinding.from_mapping(question)
        if not requested_binding.is_complete():
            raise OfficialSourceCorrectionError(
                "要求されたsource identityが完全ではありません。"
            )
        projected_identity_values = {
            "sourceQuestionKey": str(projected.get("sourceQuestionKey") or ""),
            "reviewQuestionId": str(
                projected.get("reviewQuestionId")
                or projected.get("original_question_id")
                or ""
            ),
            "sourceRecordRef": str(projected.get("sourceRecordRef") or ""),
        }
        requested_identity_values = requested_binding.as_mapping()
        if any(
            value and value != requested_identity_values[field]
            for field, value in projected_identity_values.items()
        ):
            raise OfficialSourceCorrectionError(
                "server生成のprojected問題identityが要求identityと矛盾します。"
            )
        source_dir = (
            self.repo_root
            / "output"
            / qualification
            / "questions_json"
            / list_group_id
            / "00_source"
        )
        try:
            source_inventory = load_source_record_inventory(
                source_dir,
                qualification=qualification,
                list_group_id=list_group_id,
            )
        except ValueError as exc:
            raise OfficialSourceCorrectionError(
                "00_source inventoryを一意に検証できません。"
            ) from exc
        matches = [
            entry
            for entry in source_inventory
            if entry.identity.binding == requested_binding
        ]
        if len(matches) != 1:
            raise OfficialSourceCorrectionError(
                "要求identityが00_sourceのexact one recordへ解決できません。"
            )
        match = matches[0]
        return dict(projected), match.path, match.identity

    def _resume_work_directory(
        self,
        value: str,
        *,
        expected_work_id: str,
        qualification: str,
        list_group_id: str,
        original_question_id: str,
        state_hash: str,
        category: str,
        evidence_hash: str,
        evidence_title: str,
        evidence_locator: str,
        evidence_transcription: str,
        evidence_relative_path: str,
    ) -> tuple[Path, dict[str, str]]:
        raw = Path(str(value).strip())
        if not str(raw) or any(part in {"*", "?", "[", "]"} for part in raw.parts):
            raise OfficialSourceCorrectionError("resumeWorkDirectoryはexact pathが必要です。")
        path = (raw if raw.is_absolute() else self.repo_root / raw).absolute()
        root = (
            self.repo_root / "output/question_issue_reports/ui_official_source"
        ).resolve()
        if not path.is_relative_to(self.repo_root):
            raise OfficialSourceCorrectionError("resumeWorkDirectoryがrepo外です。")
        component = self.repo_root
        for part in path.relative_to(self.repo_root).parts:
            component = component / part
            if component.is_symlink():
                raise OfficialSourceCorrectionError("resumeWorkDirectoryにsymlinkは使用できません。")
        path = path.resolve()
        if not path.is_relative_to(root) or path.name != expected_work_id:
            raise OfficialSourceCorrectionError("resumeWorkDirectoryが対象question work dirではありません。")
        if not path.parent.name.startswith("ui-qir-") or not path.is_dir():
            raise OfficialSourceCorrectionError("resumeWorkDirectoryは既存batchのexact work dirに限ります。")
        blind_path = path / "blind_input.json"
        blind_paths = [
            candidate
            for candidate in (path / "blind_a.json", path / "blind_b.json")
            if candidate.is_file()
        ]
        if (
            not blind_path.is_file()
            or not blind_paths
            or (path / "challenge.json").exists()
        ):
            raise OfficialSourceCorrectionError(
                "resume対象は検証済みBlind結果がありChallenge未完了のwork dirに限ります。"
            )
        blind = json.loads(blind_path.read_text(encoding="utf-8"))
        current = blind.get("currentLocalRecord")
        if not isinstance(current, Mapping):
            raise OfficialSourceCorrectionError("resume current recordがありません。")
        if (
            blind.get("qualificationId") != qualification
            or blind.get("listGroupId") != list_group_id
            or blind.get("originalQuestionId") != original_question_id
            or blind.get("reviewScope") != category
            or sha256_json(
                {field: current.get(field) for field in PROJECTED_COMPARE_FIELDS}
            ) != state_hash
        ):
            raise OfficialSourceCorrectionError("resume work identity/state mismatchです。")
        snapshots = blind.get("currentFirestoreSnapshots") or []
        if len(snapshots) != 1 or not isinstance(snapshots[0], Mapping):
            raise OfficialSourceCorrectionError("resume snapshotを一意に解決できません。")
        candidates = snapshots[0].get("officialEvidenceCandidates") or []
        if len(candidates) != 1 or not isinstance(candidates[0], Mapping):
            raise OfficialSourceCorrectionError("resume evidenceを一意に解決できません。")
        saved = candidates[0]
        requested = {
            "contentHash": evidence_hash,
            "title": evidence_title,
            "locator": evidence_locator,
            "verifiedTranscription": evidence_transcription,
            "localSourcePath": evidence_relative_path,
        }
        if any(saved.get(key) != expected for key, expected in requested.items()):
            raise OfficialSourceCorrectionError("resume evidence metadata mismatchです。")
        canonical_locator = f"{evidence_relative_path} / {evidence_locator}"
        verified_at_values: set[str] = set()
        for result_path in blind_paths:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            evidence = result.get("evidence") or []
            if len(evidence) != 1 or not isinstance(evidence[0], Mapping):
                raise OfficialSourceCorrectionError(
                    f"resume {result_path.stem} evidenceを一意に解決できません。"
                )
            item = evidence[0]
            verified_at = item.get("verifiedAt")
            if (
                item.get("title") != evidence_title
                or item.get("locator") != canonical_locator
                or item.get("contentHash") != evidence_hash
                or not isinstance(verified_at, str)
                or not verified_at
            ):
                raise OfficialSourceCorrectionError(
                    f"resume {result_path.stem} canonical evidence mismatchです。"
                )
            verified_at_values.add(verified_at)
        if len(verified_at_values) != 1:
            raise OfficialSourceCorrectionError(
                "resume Blind evidence verifiedAt mismatchです。"
            )
        verified_at = next(iter(verified_at_values))
        identity_seed = {
            "qualification": qualification,
            "listGroupId": list_group_id,
            "originalQuestionId": original_question_id,
            "stateHash": state_hash,
            "category": category,
            "evidenceHash": evidence_hash,
            "locator": evidence_locator,
            "transcription": evidence_transcription,
        }
        rebuilt_hash = sha256_json(identity_seed)
        rebuilt_timestamp = re.sub(r"[^0-9]", "", verified_at)[:14]
        rebuilt_batch_id = f"ui-qir-{rebuilt_timestamp}-{rebuilt_hash[:10]}"
        if path.parent.name != rebuilt_batch_id:
            raise OfficialSourceCorrectionError("resume batchId provenance mismatchです。")
        return path, {
            "title": evidence_title,
            "canonicalLocator": canonical_locator,
            "contentHash": evidence_hash,
            "verifiedAt": verified_at,
        }

    def run(
        self,
        question: Mapping[str, Any],
        *,
        state_hash: str,
        category: str = "question_content",
        evidence_path: str,
        evidence_title: str,
        evidence_locator: str,
        verified_transcription: str,
        emit: Callable[[str], None],
        resume_work_directory: str = "",
    ) -> dict[str, Any]:
        qualification = self._required_text(question, "qualification")
        list_group_id = self._required_text(question, "listGroupId")
        original_question_id = self._required_text(question, "originalQuestionId")
        current_state_hash = self._required_text(question, "stateHash")
        category_id = str(category or "").strip()
        if category_id not in {"question_content", "correct_answer", "image"}:
            raise OfficialSourceCorrectionError(
                "公式資料との照合対象は問題文・選択肢、問題画像又は正答を"
                "指定してください。"
            )
        category_config = self.config["categories"][category_id]

        title = self._bounded_text(evidence_title, "資料名", 512)
        locator = self._bounded_text(evidence_locator, "該当箇所", 2048)
        transcription = self._bounded_text(
            verified_transcription,
            "公式資料の確認済み転記",
            20_000,
        )
        resolved_evidence = self._evidence_file(evidence_path)
        evidence_hash = self._file_sha256(resolved_evidence)
        evidence_relative_path = str(resolved_evidence.relative_to(self.repo_root))
        emit(
            "公式資料を固定しました: "
            f"{evidence_relative_path} sha256={evidence_hash[:12]}"
        )

        created_at = utc_now_text()
        timestamp = re.sub(r"[^0-9]", "", created_at)[:14]
        identity_seed = {
            "qualification": qualification,
            "listGroupId": list_group_id,
            "originalQuestionId": original_question_id,
            "stateHash": current_state_hash,
            "category": category_id,
            "evidenceHash": evidence_hash,
            "locator": locator,
            "transcription": transcription,
        }
        input_hash = sha256_json(identity_seed)
        case_id = f"ui-official-{input_hash[:20]}"
        work_id = f"official-{self._safe_id(original_question_id)}"
        batch_id = f"ui-qir-{timestamp}-{input_hash[:10]}"
        fresh_work_dir = (
            self.repo_root
            / "output"
            / "question_issue_reports"
            / "ui_official_source"
            / batch_id
            / work_id
        )
        resume_metadata: dict[str, str] | None = None
        if str(resume_work_directory or "").strip():
            work_dir, resume_metadata = self._resume_work_directory(
                resume_work_directory,
                expected_work_id=work_id,
                qualification=qualification,
                list_group_id=list_group_id,
                original_question_id=original_question_id,
                state_hash=state_hash,
                category=category_id,
                evidence_hash=evidence_hash,
                evidence_title=title,
                evidence_locator=locator,
                evidence_transcription=transcription,
                evidence_relative_path=evidence_relative_path,
            )
            created_at = resume_metadata["verifiedAt"]
            batch_id = work_dir.parent.name
        else:
            work_dir = fresh_work_dir
        rendered_evidence = self._prepare_rendered_evidence(
            resolved_evidence,
            locator=locator,
            work_dir=work_dir,
        )
        rendered_evidence_relative_path = str(
            rendered_evidence.relative_to(self.repo_root)
        )
        emit(
            "照合対象ページを固定しました: "
            f"{rendered_evidence_relative_path}"
        )
        canonical_snapshot = {
            "questionId": str(question.get("id") or ""),
            "originalQuestionId": original_question_id,
            "qualificationId": qualification,
            "listGroupId": list_group_id,
            "currentContentHash": current_state_hash,
            "officialEvidenceCandidates": [
                {
                    "sourceClass": "official",
                    "locator": locator,
                    "title": title,
                    "contentHash": evidence_hash,
                    "localSourcePath": evidence_relative_path,
                    "localRenderedPagePath": rendered_evidence_relative_path,
                    "localRenderedPageHash": self._file_sha256(
                        rendered_evidence
                    ),
                    "verifiedTranscription": transcription,
                }
            ],
        }
        if resume_metadata is not None:
            saved_blind_input = json.loads(
                (work_dir / "blind_input.json").read_text(encoding="utf-8")
            )
            saved_snapshots = saved_blind_input.get("currentFirestoreSnapshots")
            if not isinstance(saved_snapshots, list) or not saved_snapshots:
                raise OfficialSourceCorrectionError("resume blind input snapshotがありません。")
            canonical_snapshot = copy.deepcopy(saved_snapshots[0])
        work_item = {
            "workId": work_id,
            "qualificationId": qualification,
            "listGroupId": list_group_id,
            "questionId": str(question.get("id") or ""),
            "originalQuestionId": original_question_id,
            "sourceQuestionKey": str(question.get("sourceQuestionKey") or ""),
            "reviewQuestionId": original_question_id,
            "sourceRecordRef": str(question.get("sourceRecordRef") or ""),
            "caseIds": [case_id],
            "caseInputHashes": {case_id: input_hash},
            "caseSnapshots": [
                {
                    "id": case_id,
                    "canonicalSnapshot": canonical_snapshot,
                }
            ],
        }
        manifest = {
            "schemaVersion": "question-issue-batch/v1",
            "batchId": batch_id,
            "status": "awaiting_approval",
            "category": category_id,
            "categoryLabel": category_config["label"],
            "snapshotAt": created_at,
            "totalQuestions": 1,
            "totalCases": 1,
            "workItems": [work_item],
        }

        current_record, current_path, source_identity = (
            self._server_owned_current_record(
                question,
                qualification=qualification,
                list_group_id=list_group_id,
                state_hash=state_hash,
            )
            if self._uses_server_owned_projection
            else self.record_finder(
                work_item,
                output_root=self.repo_root / "output",
            )
        )
        image_publication: dict[str, Any] | None = None
        if category_id == "image":
            image_publication = self._image_publication_candidate(
                qualification=qualification,
                list_group_id=list_group_id,
                original_question_id=original_question_id,
                current_record=current_record,
                rendered_evidence=rendered_evidence,
                evidence_hash=evidence_hash,
            )
            canonical_snapshot["officialImagePublicationCandidate"] = {
                "localImagePath": image_publication["localImagePath"],
                "publicUrl": image_publication["publicUrl"],
                "contentHash": evidence_hash,
                "proposedChanges": image_publication["proposedChanges"],
            }
        executor = AppServerReviewExecutor(
            self.app_server,
            repo_root=self.repo_root,
            qualification=qualification,
            current_record=current_record,
            evidence_hash=evidence_hash,
            evidence_title=(resume_metadata or {}).get("title", title),
            evidence_locator=locator,
            evidence_relative_path=evidence_relative_path,
            evidence_verified_at=(resume_metadata or {}).get("verifiedAt", created_at),
            work_dir=work_dir,
            emit=emit,
        )
        blind_a, blind_b, challenge = self.review_runner(
            work_item,
            category=category_id,
            current_record=current_record,
            store=_EmptyClaimsStore(),
            executor=executor,
            work_dir=work_dir,
            config=self.config,
            resume_binding={
                "stateHash": state_hash,
                "evidenceHash": evidence_hash,
            },
            require_resume_consensus=True,
        )
        decision = str(challenge.get("decision") or "")
        certifies_current_answer = (
            decision == "no_change" and category_id == "correct_answer"
        )
        if decision != "fix" and not certifies_current_answer:
            return {
                "decision": decision,
                "patchPath": None,
                "workDirectory": str(work_dir.relative_to(self.repo_root)),
                "message": self._decision_message(decision),
            }

        self._verify_selected_evidence(
            challenge,
            content_hash=evidence_hash,
            title=title,
            locator=locator,
            local_path=evidence_relative_path,
        )
        if image_publication is not None:
            changes = challenge.get("changes")
            if changes != image_publication["proposedChanges"]:
                raise OfficialSourceCorrectionError(
                    "画像補正はserverが固定した新規画像URLだけを反映できます。"
                )
        patch_builder = (
            build_current_answer_certification_patch
            if certifies_current_answer
            else build_correction_patch
        )
        patch = patch_builder(
            manifest=manifest,
            work_item=work_item,
            current_record=current_record,
            source_binding=source_identity.binding,
            blind_reviews=[blind_a, blind_b],
            challenge=challenge,
            config=self.config,
        )
        filename = correction_patch_filename(manifest, work_item)
        staged_path = work_dir / filename
        write_private_json(staged_path, patch)
        self.patch_verifier(
            staged_path,
            current_record=current_record,
            source_identity=source_identity,
            config_path=self.config_path,
        )
        if image_publication is not None:
            published = self.image_publisher(
                qualification=qualification,
                source_path=rendered_evidence,
                local_path=self.repo_root / image_publication["localImagePath"],
                filename=image_publication["filename"],
                public_url=image_publication["publicUrl"],
                content_hash=evidence_hash,
                emit=emit,
            )
            if str(published.get("publicUrl") or "") != image_publication["publicUrl"]:
                raise OfficialSourceCorrectionError(
                    "画像公開後のURLが補正patchのURLと一致しません。"
                )

        target_dir = (
            self.repo_root
            / "output"
            / qualification
            / "questions_json"
            / list_group_id
            / "24_questionIssueCorrections"
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / filename
        if target_path.exists():
            raise OfficialSourceCorrectionError(
                f"同名の補正patchが既に存在します: {target_path.relative_to(self.repo_root)}"
            )
        os.replace(staged_path, target_path)
        target_path.chmod(0o644)
        if certifies_current_answer:
            emit(
                "公式資料と一致する現在の正答を"
                "24_questionIssueCorrectionsへ証明保存しました。"
            )
        else:
            emit(
                "公式資料と一致した変更を24_questionIssueCorrectionsへ保存しました。"
            )
        return {
            "decision": decision,
            "patchPath": str(target_path.relative_to(self.repo_root)),
            "currentRecordPath": str(
                current_path.resolve().relative_to(self.repo_root)
            ),
            "workDirectory": str(work_dir.relative_to(self.repo_root)),
            "changedFields": (
                []
                if certifies_current_answer
                else sorted((challenge.get("changes") or {}).keys())
            ),
            "certifiedFields": (
                ["correctChoiceText"] if certifies_current_answer else []
            ),
            "message": (
                "公式資料とのBlind A/B照合に一致し、現在の正答を証明する"
                "patchを保存しました。"
                if certifies_current_answer
                else (
                    "公式資料とのBlind A/B照合に一致し、"
                    f"{category_config['label']}の補正patchを保存しました。"
                )
            ),
        }

    @staticmethod
    def _required_text(value: Mapping[str, Any], field: str) -> str:
        text = str(value.get(field) or "").strip()
        if not text:
            raise OfficialSourceCorrectionError(f"{field}を確認できません。")
        return text

    @staticmethod
    def _bounded_text(value: str, label: str, limit: int) -> str:
        text = str(value or "").strip()
        if not text:
            raise OfficialSourceCorrectionError(f"{label}を入力してください。")
        if len(text) > limit:
            raise OfficialSourceCorrectionError(
                f"{label}は{limit}文字以内で入力してください。"
            )
        return text

    def _evidence_file(self, value: str) -> Path:
        raw = Path(str(value or "").strip()).expanduser()
        if not str(raw):
            raise OfficialSourceCorrectionError("公式資料のローカルpathを入力してください。")
        path = (raw if raw.is_absolute() else self.repo_root / raw).resolve()
        if not path.is_relative_to(self.repo_root):
            raise OfficialSourceCorrectionError(
                "公式資料はrepository内のファイルを指定してください。"
            )
        if not path.is_file():
            raise OfficialSourceCorrectionError(f"公式資料がありません: {path}")
        if path.suffix.casefold() not in ALLOWED_EVIDENCE_SUFFIXES:
            raise OfficialSourceCorrectionError(
                "公式資料はPDF又はPNG/JPEG/WEBPを指定してください。"
            )
        if path.stat().st_size > MAX_EVIDENCE_BYTES:
            raise OfficialSourceCorrectionError("公式資料は50MB以内にしてください。")
        return path

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _image_publication_candidate(
        self,
        *,
        qualification: str,
        list_group_id: str,
        original_question_id: str,
        current_record: Mapping[str, Any],
        rendered_evidence: Path,
        evidence_hash: str,
    ) -> dict[str, Any]:
        suffix = rendered_evidence.suffix.casefold()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise OfficialSourceCorrectionError(
                "問題画像の補正には、必要な図表だけを切り出した"
                "PNG/JPEG/WEBPを指定してください。"
            )
        existing = current_record.get("questionImageStorageUrls")
        if existing is None:
            current_urls: list[str] = []
        elif isinstance(existing, list) and all(
            isinstance(value, str) and value.strip() for value in existing
        ):
            current_urls = list(existing)
        else:
            raise OfficialSourceCorrectionError(
                "現在のquestionImageStorageUrlsが文字列配列ではありません。"
            )
        filename = (
            f"official-source-{self._safe_id(list_group_id)}-"
            f"{self._safe_id(original_question_id)}-{evidence_hash[:16]}{suffix}"
        )
        public_url = build_public_storage_url(qualification, filename)
        proposed_urls = list(current_urls)
        if public_url not in proposed_urls:
            proposed_urls.append(public_url)
        local_path = (
            Path("output")
            / qualification
            / "question_images"
            / list_group_id
            / filename
        )
        return {
            "filename": filename,
            "localImagePath": local_path.as_posix(),
            "publicUrl": public_url,
            "proposedChanges": {
                "questionImageStorageUrls": proposed_urls,
            },
        }

    def _publish_question_image(
        self,
        *,
        qualification: str,
        source_path: Path,
        local_path: Path,
        filename: str,
        public_url: str,
        content_hash: str,
        emit: Callable[[str], None],
    ) -> dict[str, str]:
        expected_url = build_public_storage_url(qualification, filename)
        if public_url != expected_url:
            raise OfficialSourceCorrectionError(
                "問題画像の公開URLがserverの決定値と一致しません。"
            )
        local_path.parent.mkdir(parents=True, exist_ok=True)
        created_local = False
        if local_path.exists():
            if self._file_sha256(local_path) != content_hash:
                raise OfficialSourceCorrectionError(
                    f"同名の問題画像と内容が一致しません: "
                    f"{local_path.relative_to(self.repo_root)}"
                )
        else:
            shutil.copyfile(source_path, local_path)
            local_path.chmod(0o644)
            created_local = True

        try:
            bucket = make_storage_bucket(FIREBASE_STORAGE_BUCKET)
            object_path = build_storage_object_path(qualification, filename)
            blob = bucket.blob(object_path)
            if blob.exists():
                remote_bytes = blob.download_as_bytes()
                if hashlib.sha256(remote_bytes).hexdigest() != content_hash:
                    raise OfficialSourceCorrectionError(
                        "同名のStorage画像と内容hashが一致しません。"
                    )
                emit(f"既存の同一Storage画像を確認しました: gs://{bucket.name}/{object_path}")
            else:
                content_type, _ = mimetypes.guess_type(filename)
                blob.metadata = {
                    "sha256": content_hash,
                    "origin": "official_source_correction",
                }
                blob.upload_from_filename(
                    str(local_path),
                    content_type=content_type,
                )
                emit(f"問題画像をStorageへ保存しました: gs://{bucket.name}/{object_path}")
            blob.reload()
            if int(blob.size or -1) != local_path.stat().st_size:
                raise OfficialSourceCorrectionError(
                    "Storage画像のreadbackサイズがローカル画像と一致しません。"
                )
        except Exception:
            if created_local and local_path.exists():
                local_path.unlink()
            raise
        emit(
            "問題画像のStorage readbackを確認しました: "
            f"{local_path.relative_to(self.repo_root)}"
        )
        return {
            "localPath": str(local_path.relative_to(self.repo_root)),
            "publicUrl": public_url,
        }

    def _prepare_rendered_evidence(
        self,
        evidence_path: Path,
        *,
        locator: str,
        work_dir: Path,
    ) -> Path:
        if evidence_path.suffix.casefold() != ".pdf":
            return evidence_path
        match = PDF_PAGE_LOCATOR.search(locator)
        if not match:
            raise OfficialSourceCorrectionError(
                "PDFを指定する場合、該当箇所に「PDF 26ページ」の形式で"
                "ページ番号を入力してください。"
            )
        page_number = int(match.group(1))
        if page_number < 1:
            raise OfficialSourceCorrectionError(
                "PDFのページ番号は1以上を指定してください。"
            )
        rendered_path = work_dir / f"official_page_{page_number:04d}.png"
        rendered_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.page_renderer(
                evidence_path,
                page_number - 1,
                rendered_path,
            )
        except OfficialSourceCorrectionError:
            raise
        except Exception as exc:
            raise OfficialSourceCorrectionError(
                f"公式PDFの{page_number}ページを画像化できませんでした: {exc}"
            ) from exc
        if not rendered_path.is_file() or rendered_path.stat().st_size == 0:
            raise OfficialSourceCorrectionError(
                f"公式PDFの{page_number}ページ画像を作成できませんでした。"
            )
        return rendered_path

    @staticmethod
    def _render_pdf_page(
        source_path: Path,
        page_index: int,
        target_path: Path,
    ) -> None:
        try:
            import pypdfium2 as pdfium
        except ImportError as exc:
            raise OfficialSourceCorrectionError(
                "公式PDFの画像化に必要なpypdfium2がありません。"
            ) from exc
        document = pdfium.PdfDocument(str(source_path))
        try:
            if page_index >= len(document):
                raise OfficialSourceCorrectionError(
                    f"公式PDFは{len(document)}ページのため、"
                    f"{page_index + 1}ページを参照できません。"
                )
            page = document[page_index]
            try:
                bitmap = page.render(scale=2.0)
                try:
                    image = bitmap.to_pil()
                    image.save(target_path, format="PNG", optimize=True)
                finally:
                    bitmap.close()
            finally:
                page.close()
        finally:
            document.close()

    @staticmethod
    def _verify_selected_evidence(
        challenge: Mapping[str, Any],
        *,
        content_hash: str,
        title: str,
        locator: str,
        local_path: str,
    ) -> None:
        allowed_locators = {
            locator,
            f"{local_path} / {locator}",
        }
        evidence = challenge.get("evidence")
        if not isinstance(evidence, list) or not any(
            isinstance(item, Mapping)
            and item.get("sourceClass") == "official"
            and item.get("contentHash") == content_hash
            and item.get("title") == title
            and item.get("locator") in allowed_locators
            for item in evidence
        ):
            raise OfficialSourceCorrectionError(
                "Challenge結果が、画面で固定した公式資料の"
                "title・path・locator・hashを保持していません。"
            )

    @staticmethod
    def _safe_id(value: str) -> str:
        return SAFE_ID.sub("_", value).strip("_")[:96] or "question"

    @staticmethod
    def _decision_message(decision: str) -> str:
        return {
            "no_change": "公式資料との独立照合では変更不要と判断されました。",
            "hold": "公式資料との独立照合で根拠不足又は不一致が残り、保留しました。",
            "app_update": "問題データではなくアプリ側の対応候補として分離しました。",
        }.get(decision, "公式資料との独立照合を完了しました。")
