from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Callable, Mapping

from tools.question_bank.question_issue_reports import (
    DEFAULT_CONFIG_PATH,
    ReviewExecutor,
    _extract_json_text,
    build_correction_patch,
    correction_patch_filename,
    find_current_question_record,
    load_config,
    run_objective_review,
    sha256_json,
    utc_now_text,
    verify_patch_against_record,
    write_private_json,
)


ALLOWED_EVIDENCE_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
MAX_EVIDENCE_BYTES = 50 * 1024 * 1024
SAFE_ID = re.compile(r"[^A-Za-z0-9_-]+")


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
        del replacements
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
        payload = _extract_json_text(str(getattr(result, "final_message", "") or ""))
        self._normalize_review_payload(payload, phase=phase)
        self.emit(f"{phase}: 構造化結果を受領しました。")
        return payload

    def _normalize_review_payload(
        self,
        payload: dict[str, Any],
        *,
        phase: str,
    ) -> None:
        change_field = "changes" if phase == "challenge" else "proposedChanges"
        changes = payload.get(change_field)
        if isinstance(changes, Mapping):
            payload[change_field] = {
                key: value
                for key, value in changes.items()
                if self.current_record.get(key) != value
            }
        evidence = payload.get("evidence")
        if not isinstance(evidence, list):
            return
        for item in evidence:
            if (
                isinstance(item, dict)
                and item.get("sourceClass") == "official"
                and item.get("contentHash") == self.evidence_hash
            ):
                item["title"] = self.evidence_title
                item["locator"] = self.canonical_evidence_locator
                item["verifiedAt"] = self.evidence_verified_at


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
    ):
        self.repo_root = repo_root.resolve()
        self.app_server = app_server
        self.config_path = (config_path or DEFAULT_CONFIG_PATH).resolve()
        self.config = load_config(self.config_path)
        self.review_runner = review_runner
        self.record_finder = record_finder
        self.patch_verifier = patch_verifier

    def run(
        self,
        question: Mapping[str, Any],
        *,
        state_hash: str,
        evidence_path: str,
        evidence_title: str,
        evidence_locator: str,
        verified_transcription: str,
        emit: Callable[[str], None],
    ) -> dict[str, Any]:
        qualification = self._required_text(question, "qualification")
        list_group_id = self._required_text(question, "listGroupId")
        original_question_id = self._required_text(question, "originalQuestionId")
        current_state_hash = self._required_text(question, "stateHash")
        if state_hash != current_state_hash:
            raise OfficialSourceCorrectionError(
                "画面表示後に問題内容が更新されました。問題を開き直してください。"
            )

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
            "evidenceHash": evidence_hash,
            "locator": locator,
            "transcription": transcription,
        }
        input_hash = sha256_json(identity_seed)
        case_id = f"ui-official-{input_hash[:20]}"
        work_id = f"official-{self._safe_id(original_question_id)}"
        batch_id = f"ui-qir-{timestamp}-{input_hash[:10]}"
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
                    "localRenderedPagePath": evidence_relative_path,
                    "verifiedTranscription": transcription,
                }
            ],
        }
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
            "category": "question_content",
            "categoryLabel": self.config["categories"]["question_content"]["label"],
            "snapshotAt": created_at,
            "totalQuestions": 1,
            "totalCases": 1,
            "workItems": [work_item],
        }

        current_record, current_path, source_identity = self.record_finder(
            work_item,
            output_root=self.repo_root / "output",
        )
        work_dir = (
            self.repo_root
            / "output"
            / "question_issue_reports"
            / "ui_official_source"
            / batch_id
            / work_id
        )
        executor = AppServerReviewExecutor(
            self.app_server,
            repo_root=self.repo_root,
            qualification=qualification,
            current_record=current_record,
            evidence_hash=evidence_hash,
            evidence_title=title,
            evidence_locator=locator,
            evidence_relative_path=evidence_relative_path,
            evidence_verified_at=created_at,
            emit=emit,
        )
        blind_a, blind_b, challenge = self.review_runner(
            work_item,
            category="question_content",
            current_record=current_record,
            store=_EmptyClaimsStore(),
            executor=executor,
            work_dir=work_dir,
            config=self.config,
        )
        decision = str(challenge.get("decision") or "")
        if decision != "fix":
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
        patch = build_correction_patch(
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
        emit(
            "公式資料と一致した変更を24_questionIssueCorrectionsへ保存しました。"
        )
        return {
            "decision": "fix",
            "patchPath": str(target_path.relative_to(self.repo_root)),
            "currentRecordPath": str(
                current_path.resolve().relative_to(self.repo_root)
            ),
            "workDirectory": str(work_dir.relative_to(self.repo_root)),
            "changedFields": sorted((challenge.get("changes") or {}).keys()),
            "message": (
                "公式資料とのBlind A/B照合に一致し、問題文・選択肢の補正patchを保存しました。"
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
