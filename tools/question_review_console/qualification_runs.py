from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from scripts.merge.merge_utils import (
    select_latest_patch_files,
    source_stem_from_patch_filename,
)
from tools.question_review_console.projection import (
    extract_records,
    record_identity_aliases,
)
from tools.question_review_console.jobs import (
    REPOSITORY_OPERATION_KEY,
    JobConflictError,
    JobManager,
)
from tools.question_review_console.failed_delta import (
    resolvable_failed_delta_paths,
    unresolved_failed_delta_paths,
)
from tools.question_review_console.explanation_quality import (
    explanation_style_issues,
)
from tools.question_review_console.codex_app_server import (
    MAINTENANCE_RESEARCH_WORKERS,
)
from tools.question_review_console.qualification_workflow import QualificationWorkflow
from tools.question_review_console.work_versions import QuestionWorkVersionStore
from tools.question_review_console.workflow_catalog import normalize_policy_version
from tools.question_review_console.workflow_runner import (
    ArtifactSynchronizer,
    sync_after_patch_update,
)


LIVE_RUN_STATUSES = {
    "queued",
    "running",
    "validating",
}
PROGRESS_EVENT_TYPES = {"question_started", "stage_completed", "question_completed"}
PROGRESS_RESULT_FIELDS = {
    "summary",
    "correctChoiceText",
    "explanationText",
    "questionType",
    "questionIntent",
    "lawContext",
    "lawAudit",
    "questionSetId",
}
MAX_PROGRESS_BYTES = 8 * 1024 * 1024
MAX_PROGRESS_EVENTS = 10_000
MAX_PROGRESS_LINE_BYTES = 32 * 1024
ALLOWED_MAINTENANCE_DIR_NAMES = {
    "10_questionType_fixed",
    "15_correctChoiceText_fixed",
    "18_law_context_prepared",
    "21_explanationText_added",
    "22_questionSetId_linked",
    "23_correctChoiceText_fixed",
    "24_questionIssueCorrections",
    "99_model_review_flags",
}
STAGE_PATCH_DIR_NAMES = {
    "question_type": {"10_questionType_fixed", "99_model_review_flags"},
    "question_intent": {"15_correctChoiceText_fixed", "99_model_review_flags"},
    "correct_choice": {"23_correctChoiceText_fixed", "99_model_review_flags"},
    "law_context": {"18_law_context_prepared", "99_model_review_flags"},
    "explanation": {"21_explanationText_added", "99_model_review_flags"},
    "law_audit": {
        "18_law_context_prepared",
        "21_explanationText_added",
        "23_correctChoiceText_fixed",
        "99_model_review_flags",
    },
    "question_set": {"22_questionSetId_linked", "99_model_review_flags"},
}
PATCH_SUFFIX_BY_DIR = {
    "10_questionType_fixed": "questionType_fixed",
    "15_correctChoiceText_fixed": "correctChoiceText_fixed",
    "18_law_context_prepared": "lawContext_prepared",
    "21_explanationText_added": "explanationText_added",
    "22_questionSetId_linked": "questionSetId_linked",
    "23_correctChoiceText_fixed": "correctChoiceText_fixed",
}
REVIEW_FLAG_SUFFIX_BY_PATCH_DIR = {
    "10_questionType_fixed": "questionType",
    "15_correctChoiceText_fixed": "questionIntent",
    "18_law_context_prepared": "lawContext",
    "21_explanationText_added": "explanationText",
    "22_questionSetId_linked": "questionSetId",
    "23_correctChoiceText_fixed": "correctChoiceText",
}
STAGE_REVIEW_FLAG_SUFFIXES = {
    "question_type": {"questionType"},
    "question_intent": {"questionIntent"},
    "correct_choice": {"correctChoiceText"},
    "law_context": {"lawContext"},
    "explanation": {"explanationText"},
    "law_audit": {"lawRevision"},
    "question_set": {"questionSetId"},
}
FIELD_PATCH_DIR_NAMES = {
    "questionType": {"10_questionType_fixed", "99_model_review_flags"},
    "questionIntent": {"15_correctChoiceText_fixed", "99_model_review_flags"},
    "answer_result_text": {"15_correctChoiceText_fixed", "99_model_review_flags"},
    "correctChoiceText": {"23_correctChoiceText_fixed", "99_model_review_flags"},
    "explanationText": {"21_explanationText_added", "99_model_review_flags"},
    "suggestedQuestions": {"21_explanationText_added", "99_model_review_flags"},
    "suggestedQuestionDetails": {"21_explanationText_added", "99_model_review_flags"},
    "questionSetId": {"22_questionSetId_linked", "99_model_review_flags"},
}
NON_AUTOMATED_CORRECTION_FIELDS = {"questionBodyText", "choiceTextList"}
LAW_PATCH_DIR_NAMES = set(STAGE_PATCH_DIR_NAMES["law_audit"])
LAW_AUDIT_ISSUES = {
    "law_audit_metadata_incomplete",
    "law_audit_verdict_mismatch",
    "law_basis_missing",
    "law_hold",
}


def _review_requests_law_audit(review: Mapping[str, Any]) -> bool:
    issue_types = {
        str(value) for value in review.get("issueTypes") or [] if value
    }
    selection = review.get("selection")
    selection_fields = (
        selection.get("fields")
        if isinstance(selection, Mapping)
        else []
    )
    fields = {
        str(value).split(".", 1)[0].split("[", 1)[0]
        for value in [
            *(review.get("fields") or []),
            *(selection_fields or []),
        ]
        if value
    }
    evaluation_snapshot = review.get("evaluationSnapshot")
    rework_items = (
        evaluation_snapshot.get("reworkItems")
        if isinstance(evaluation_snapshot, Mapping)
        else []
    )
    return bool(
        issue_types & LAW_AUDIT_ISSUES
        or review.get("requestKind") == "qualification_law_audit"
        or any(field.startswith(("law", "isLawRelated")) for field in fields)
        or any(
            isinstance(item, Mapping)
            and str(item.get("stage") or "") == "03b"
            for item in rework_items or []
        )
    )


ISSUE_PATCH_DIR_NAMES = {
    "answer_explanation_mismatch": {
        "21_explanationText_added",
        "23_correctChoiceText_fixed",
        "99_model_review_flags",
    },
    "explanation_missing": {"21_explanationText_added", "99_model_review_flags"},
    "law_audit_metadata_incomplete": LAW_PATCH_DIR_NAMES,
    "law_audit_verdict_mismatch": LAW_PATCH_DIR_NAMES,
    "law_basis_missing": LAW_PATCH_DIR_NAMES,
    "law_hold": LAW_PATCH_DIR_NAMES,
}
REWORK_STAGE_PATCH_DIR_NAMES = {
    "01": STAGE_PATCH_DIR_NAMES["question_type"],
    "02": STAGE_PATCH_DIR_NAMES["question_intent"],
    "02a": STAGE_PATCH_DIR_NAMES["correct_choice"],
    "02b": STAGE_PATCH_DIR_NAMES["law_context"],
    "03": STAGE_PATCH_DIR_NAMES["explanation"],
    "03b": STAGE_PATCH_DIR_NAMES["law_audit"],
}
REWORK_POLICY_STAGE_IDS = {
    "01": "question_type",
    "02": "question_intent",
    "02a": "correct_choice",
    "02b": "law_context",
    "03": "explanation",
    "03b": "law_audit",
    "04": "question_set",
}
POLICY_STAGE_BY_PATCH_DIR = {
    "10_questionType_fixed": "question_type",
    "15_correctChoiceText_fixed": "question_intent",
    "23_correctChoiceText_fixed": "correct_choice",
    "18_law_context_prepared": "law_context",
    "21_explanationText_added": "explanation",
    "22_questionSetId_linked": "question_set",
}
SNAPSHOT_IGNORED_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}
CODEX_PROTECTED_CONTENT_FIELDS = (
    "questionBodyText",
    "choiceTextList",
    "originalQuestionBodyText",
    "originalChoiceTextList",
    "sourceUniqueKeys",
    "firestoreSourceQuestions",
    "sourceConflictReviewDecision",
    "sourceContentConflictPolicy",
)
CODEX_PROTECTED_IDENTITY_FIELDS = (
    "original_question_id",
    "public_question_id",
    "originalQuestionId",
    "questionId",
    "reviewQuestionId",
    "review_question_id",
    "sourceQuestionKey",
    "source_question_key",
    "uploadOriginalQuestionId",
    "firestoreQuestionIds",
)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _safe_segment(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise ValueError(f"invalid path segment: {value}")
    return value


def _normalized_alias_groups(value: Any) -> list[list[str]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [
        list(group)
        for group in dict.fromkeys(
            tuple(sorted({str(alias) for alias in raw if alias}))
            for raw in value
            if isinstance(raw, (list, tuple, set)) and raw
        )
        if group
    ]


def _add_record_scope(
    scopes: dict[str, list[list[str]]],
    path: str,
    groups: list[list[str]],
) -> None:
    scopes[path] = _normalized_alias_groups(
        [*(scopes.get(path) or []), *groups]
    )


def _content_fingerprint(path: Path) -> str:
    if path.is_symlink():
        return f"symlink:{os.readlink(path)}"
    if not path.exists():
        return "missing"
    if not path.is_file():
        stat = path.lstat()
        return f"directory:{stat.st_mode}"
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _snapshot_roots(repo_root: Path, roots: list[Path] | tuple[Path, ...]) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    resolved_repo = repo_root.resolve()
    for raw_root in roots:
        root = raw_root.resolve()
        if not root.is_relative_to(resolved_repo):
            raise QualificationRunError("baseline対象がrepository外です。")
        relative_root = root.relative_to(resolved_repo)
        snapshot[relative_root.as_posix()] = _content_fingerprint(root)
        if not root.is_dir():
            continue
        for current_root, dir_names, file_names in os.walk(root, followlinks=False):
            current = Path(current_root)
            for name in sorted(dir_names):
                path = current / name
                if path.is_symlink():
                    relative = path.relative_to(resolved_repo)
                    snapshot[relative.as_posix()] = _content_fingerprint(path)
            for name in sorted(file_names):
                path = current / name
                relative = path.relative_to(resolved_repo)
                snapshot[relative.as_posix()] = _content_fingerprint(path)
    return snapshot


def _snapshot_records(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        records: list[dict[str, Any]] = []
        for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            line = raw_line.strip()
            if not line:
                continue
            value = json.loads(line)
            if not isinstance(value, Mapping):
                raise QualificationRunError(
                    f"JSONLの{line_number}行目がobjectではありません: {path}"
                )
            records.append(dict(value))
        return records
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = extract_records(payload)
    if records:
        return records
    if isinstance(payload, Mapping) and isinstance(payload.get("entries"), list):
        return [
            dict(value)
            for value in payload["entries"]
            if isinstance(value, Mapping)
        ]
    if isinstance(payload, Mapping):
        return [dict(payload)]
    return []


def _record_snapshot(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file():
        raise QualificationRunError(f"record snapshot対象が通常fileではありません: {path}")
    try:
        records = _snapshot_records(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualificationRunError(
            f"patch JSON/JSONLをrecord単位で確認できません: {path}"
        ) from exc
    snapshot: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        canonical = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        snapshot.append(
            {
                "index": index,
                "aliases": sorted(record_identity_aliases(record)),
                "hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                "protectedFields": {
                    field: copy.deepcopy(record[field])
                    for field in CODEX_PROTECTED_CONTENT_FIELDS
                    if field in record
                },
                "identityFields": {
                    field: copy.deepcopy(record[field])
                    for field in CODEX_PROTECTED_IDENTITY_FIELDS
                    if field in record
                },
            }
        )
    return snapshot


class QualificationRunError(RuntimeError):
    pass


def _maintenance_research_prompt(prompt: str) -> str:
    base_prompt = prompt.partition("\n## 画面用の問題別進捗\n")[0].rstrip()
    return "\n".join(
        [
            "# read-only並列調査",
            "",
            "下の整備promptをこのthreadで実行・保存せず、親threadが後続の別sessionで使う判断案だけを作成する。",
            f"対象問題を重複なく分け、最大{MAINTENANCE_RESEARCH_WORKERS}つのexplorer subagentで並列に確認する。",
            "patch、progress.jsonl、result.jsonを含むfileは一切変更しない。",
            "返却は問題IDと工程ごとの最終案に限定し、思考過程は含めない。",
            "",
            "# 参照する整備prompt",
            "",
            base_prompt,
        ]
    )


def _maintenance_writer_prompt(prompt: str, research_summary: str) -> str:
    if not research_summary.strip():
        return prompt
    return "\n".join(
        [
            "# read-only並列調査の統合結果",
            "",
            "以下は別sessionのread-only調査結果である。必ず現在の問題本文と正本で再確認し、ズレがあれば採用しない。",
            "",
            research_summary.strip(),
            "",
            "# 保存する整備prompt",
            "",
            prompt,
        ]
    )


def _maintenance_session_phases(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_stage_plans = plan.get("stagePlans")
    stage_plans = (
        [dict(value) for value in raw_stage_plans if isinstance(value, Mapping)]
        if isinstance(raw_stage_plans, list) and raw_stage_plans
        else [dict(plan)]
    )
    phases: list[dict[str, Any]] = []
    for stage_plan in stage_plans:
        group_id = str(stage_plan.get("sessionGroup") or "maintenance")
        group_label = str(
            stage_plan.get("sessionLabel")
            or stage_plan.get("stageLabel")
            or "問題を整備"
        )
        stage_ids = [
            str(value)
            for value in stage_plan.get("stageIds")
            or [stage_plan.get("stageId")]
            if value and str(value) != "multi"
        ]
        if not stage_ids:
            continue
        for stage_id in stage_ids:
            phases.append(
                {
                    "id": stage_id,
                    "label": str(stage_plan.get("stageLabel") or group_label),
                    "sessionGroup": group_id,
                    "sessionLabel": group_label,
                    "stageIds": [stage_id],
                    "stageCodes": [str(stage_plan.get("stageCode") or "")],
                    "allQuestionGate": bool(stage_plan.get("allQuestionGate")),
                }
            )
    return phases


class QualificationRunStore:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()
        self.root = self.repo_root / "output" / "question_review_console" / "workflow_runs"
        self._lock = threading.RLock()
        self._recover_interrupted_runs()

    def create(
        self,
        plan: Mapping[str, Any],
        *,
        status: str,
        prompt: str | None = None,
        resumed_from: str | None = None,
        append_receipt_contract: bool = True,
    ) -> dict[str, Any]:
        qualification = _safe_segment(str(plan["qualification"]))
        run_id = f"{datetime.now().strftime('%Y%m%dT%H%M%S%f')}-{secrets.token_hex(4)}"
        run_dir = self.root / qualification / run_id
        result_path = (
            run_dir / "agent_output" / "result.json"
            if str(plan["kind"]) == "human"
            else run_dir / "result.json"
        )
        progress_path = run_dir / "agent_output" / "progress.jsonl"
        now = _now()
        target_record_alias_groups = [
            sorted({str(value) for value in group if value})
            for group in plan.get("targetRecordAliasGroups") or []
            if isinstance(group, (list, tuple, set)) and group
        ]
        target_record_aliases = {
            str(value) for value in plan.get("targetRecordAliases") or []
        }
        target_record_aliases.update(
            value for group in target_record_alias_groups for value in group
        )
        progress_targets = []
        for raw_target in plan.get("progressTargets") or []:
            if not isinstance(raw_target, Mapping):
                continue
            question_id = str(
                raw_target.get("id") or raw_target.get("questionKey") or ""
            ).strip()
            if not question_id:
                continue
            aliases = sorted(
                {
                    question_id,
                    str(raw_target.get("questionKey") or "").strip(),
                    *(
                        str(value).strip()
                        for value in raw_target.get("aliases") or []
                    ),
                }
                - {""}
            )
            progress_targets.append(
                {
                    "id": question_id,
                    "questionKey": str(raw_target.get("questionKey") or question_id)[:300],
                    "listGroupId": str(raw_target.get("listGroupId") or "")[:100],
                    "questionLabel": str(raw_target.get("questionLabel") or "")[:200],
                    "bodyPreview": str(raw_target.get("bodyPreview") or "")[:240],
                    "aliases": aliases,
                }
            )
        progress_stages = [
            {
                "id": str(stage.get("stageId") or ""),
                "code": str(stage.get("stageCode") or ""),
                "label": str(stage.get("stageLabel") or ""),
            }
            for stage in plan.get("stagePlans") or [plan]
            if str(stage.get("stageId") or "")
        ]
        def normalized_record_scopes(value: Any) -> dict[str, list[list[str]]]:
            if not isinstance(value, Mapping):
                return {}
            return {
                str(path): [
                    sorted({str(alias) for alias in group if alias})
                    for group in groups
                    if isinstance(group, (list, tuple, set)) and group
                ]
                for path, groups in value.items()
                if isinstance(groups, (list, tuple))
            }
        manifest = {
            "runId": run_id,
            "qualification": qualification,
            "stageId": str(plan["stageId"]),
            "stageIds": list(plan.get("stageIds") or [str(plan["stageId"])]),
            "stageCode": str(plan["stageCode"]),
            "stageLabel": str(plan["stageLabel"]),
            "mode": str(plan["mode"]),
            "modeLabel": str(plan["modeLabel"]),
            "kind": str(plan["kind"]),
            "workType": str(
                plan.get("workType")
                or ("delivery" if str(plan["kind"]) == "machine" else "maintenance")
            ),
            "parentRunId": plan.get("parentRunId"),
            "flowPhaseId": plan.get("flowPhaseId"),
            "phaseIndex": plan.get("phaseIndex"),
            "maintenancePhases": copy.deepcopy(
                list(plan.get("maintenancePhases") or [])
            ),
            "phaseExecutions": copy.deepcopy(
                list(plan.get("phaseExecutions") or [])
            ),
            "currentPhaseId": plan.get("currentPhaseId"),
            "childRunIds": list(plan.get("childRunIds") or []),
            "status": status,
            "targetCount": int(plan["targetCount"]),
            "workItemCount": int(plan.get("workItemCount") or plan["targetCount"]),
            "targetGroupIds": list(plan.get("targetGroupIds") or []),
            "scopeListGroupId": plan.get("scopeListGroupId"),
            "scopeListGroupIds": list(plan.get("scopeListGroupIds") or []),
            "targetQuestionIds": list(plan.get("targetQuestionIds") or []),
            "targetQuestionKeys": list(plan.get("targetQuestionKeys") or []),
            "progressTargets": progress_targets,
            "progressStages": progress_stages,
            "canonicalDocs": list(plan.get("canonicalDocs") or []),
            "catalogHash": plan.get("catalogHash"),
            "policyVersions": {
                str(stage_id): normalize_policy_version(version)
                for stage_id, version in (plan.get("policyVersions") or {}).items()
            },
            "policyFingerprints": {
                str(stage_id): str(fingerprint)
                for stage_id, fingerprint in (
                    plan.get("policyFingerprints") or {}
                ).items()
            },
            "policyTargets": {
                str(stage_id): [str(value) for value in values or []]
                for stage_id, values in (plan.get("policyTargets") or {}).items()
            },
            "sourceFiles": sorted(
                {str(value) for value in plan.get("sourceFiles") or []}
            ),
            "targetRecordAliases": sorted(target_record_aliases),
            "targetRecordAliasGroups": target_record_alias_groups,
            "targetSourceRecordScopes": normalized_record_scopes(
                plan.get("targetSourceRecordScopes")
            ),
            "targetRecordScopes": normalized_record_scopes(
                plan.get("targetRecordScopes")
            ),
            "reviewId": plan.get("reviewId"),
            "stateHash": plan.get("stateHash"),
            "sandbox": plan.get("sandbox"),
            "provider": plan.get("provider"),
            "parallelStrategy": plan.get("parallelStrategy"),
            "parallelWorkerLimit": int(plan.get("parallelWorkerLimit") or 1),
            "writeWorkerLimit": int(plan.get("writeWorkerLimit") or 1),
            "executionPhase": "queued",
            "researchStatus": None,
            "researchThreadId": None,
            "researchSessionId": None,
            "researchTurnId": None,
            "researchModel": None,
            "researchServiceTier": None,
            "researchReasoningEffort": None,
            "researchSubagentCount": 0,
            "researchSubagentThreadIds": [],
            "researchError": None,
            "model": None,
            "serviceTier": None,
            "reasoningEffort": None,
            "threadId": None,
            "sessionId": None,
            "turnId": None,
            "completedGroupIds": [],
            "jobId": None,
            "resumedFrom": resumed_from,
            "createdAt": now,
            "startedAt": None,
            "updatedAt": now,
            "finishedAt": None,
            "error": None,
            "result": None,
            "promptPath": None,
            "resultReceiptPath": str(
                result_path.relative_to(self.repo_root)
            ),
            "progressReceiptPath": (
                str(progress_path.relative_to(self.repo_root))
                if str(plan["kind"]) == "human"
                else None
            ),
            "resultReceiptHash": None,
            "receiptError": None,
            "receiptValidated": False,
            "workVersionReceipt": None,
            "baselinePath": None,
            "baselineHash": None,
            "deltaUnknown": False,
            "allowedPatchDirs": sorted(
                {str(value) for value in plan.get("allowedPatchDirs") or []}
            ),
            "allowedWriteAreas": sorted(
                {str(value) for value in plan.get("allowedWriteAreas") or []}
            ),
            "allowedWriteFiles": sorted(
                {str(value) for value in plan.get("allowedWriteFiles") or []}
            ),
            "allowedPatchFiles": sorted(
                {str(value) for value in plan.get("allowedPatchFiles") or []}
            ),
            "resolvableFailedDeltaPaths": sorted(
                {
                    str(value)
                    for value in plan.get("resolvableFailedDeltaPaths") or []
                }
            ),
        }
        with self._lock:
            run_dir.mkdir(parents=True, exist_ok=False)
            if str(plan["kind"]) == "human":
                result_path.parent.mkdir()
                progress_path.touch()
            if prompt is not None:
                prompt_path = run_dir / "prompt.md"
                prompt_path.write_text(
                    (
                        self._with_receipt_contract(
                            prompt,
                            result_path,
                            progress_path,
                            run_dir / "manifest.json",
                            manifest["resolvableFailedDeltaPaths"],
                        )
                        if append_receipt_contract
                        else prompt.rstrip() + "\n"
                    ),
                    encoding="utf-8",
                )
                manifest["promptPath"] = str(prompt_path.relative_to(self.repo_root))
            self._write_manifest(run_dir / "manifest.json", manifest)
        return copy.deepcopy(manifest)

    def update(self, qualification: str, run_id: str, **changes: Any) -> dict[str, Any]:
        path = self._manifest_path(qualification, run_id)
        with self._lock:
            manifest = self._load_manifest(path)
            manifest.update(changes)
            manifest["updatedAt"] = _now()
            if manifest.get("status") in {"succeeded", "failed"}:
                manifest["finishedAt"] = manifest.get("finishedAt") or manifest["updatedAt"]
            self._write_manifest(path, manifest)
        return copy.deepcopy(manifest)

    def list(self, qualification: str, *, limit: int = 8) -> list[dict[str, Any]]:
        qualification = _safe_segment(qualification)
        directory = self.root / qualification
        if not directory.is_dir():
            return []
        manifests: list[dict[str, Any]] = []
        with self._lock:
            for path in sorted(directory.glob("*/manifest.json"), reverse=True):
                manifest = self._load_manifest(path)
                manifest = self._apply_result_receipt(path, manifest)
                manifests.append(self._public(manifest))
                if len(manifests) >= limit:
                    break
        return manifests

    def get(self, qualification: str, run_id: str) -> dict[str, Any]:
        with self._lock:
            return self._public(self._load_manifest(self._manifest_path(qualification, run_id)))

    def refresh(self, qualification: str, run_id: str) -> dict[str, Any]:
        path = self._manifest_path(qualification, run_id)
        with self._lock:
            manifest = self._apply_result_receipt(path, self._load_manifest(path))
            return self._public(manifest)

    def write_result(
        self, qualification: str, run_id: str, result: Mapping[str, Any]
    ) -> Path:
        with self._lock:
            manifest_path = self._manifest_path(qualification, run_id)
            manifest = self._load_manifest(manifest_path)
            path = self._result_path(manifest_path, manifest)
            self._write_json(path, result)
        return path

    def result_path(self, qualification: str, run_id: str) -> Path:
        manifest_path = self._manifest_path(qualification, run_id)
        with self._lock:
            return self._result_path(
                manifest_path,
                self._load_manifest(manifest_path),
            )

    def progress_path(self, qualification: str, run_id: str) -> Path:
        manifest_path = self._manifest_path(qualification, run_id)
        with self._lock:
            manifest = self._load_manifest(manifest_path)
            if manifest.get("kind") != "human":
                raise QualificationRunError("この作業には問題単位の進捗がありません。")
            return manifest_path.parent / "agent_output" / "progress.jsonl"

    def progress(self, qualification: str, run_id: str) -> dict[str, Any]:
        manifest_path = self._manifest_path(qualification, run_id)
        with self._lock:
            manifest = self._load_manifest(manifest_path)
            if manifest.get("kind") != "human":
                return self._empty_progress(manifest)
            progress_path = manifest_path.parent / "agent_output" / "progress.jsonl"
            raw = progress_path.read_bytes() if progress_path.is_file() else b""
        return self._parsed_progress(manifest, raw)

    def combined_progress(
        self, qualification: str, run_id: str
    ) -> dict[str, Any]:
        manifest_path = self._manifest_path(qualification, run_id)
        with self._lock:
            manifest = self._load_manifest(manifest_path)
            chunks: list[bytes] = []
            for child_run_id in manifest.get("childRunIds") or []:
                child_path = self._manifest_path(qualification, str(child_run_id))
                child = self._load_manifest(child_path)
                if str(child.get("parentRunId") or "") != str(run_id):
                    raise QualificationRunError(
                        "工程別runとトップ整備runの対応が一致しません。"
                    )
                progress_path = child_path.parent / "agent_output" / "progress.jsonl"
                if progress_path.is_file():
                    chunks.append(progress_path.read_bytes())
        payload = self._parsed_progress(manifest, b"\n".join(chunks))
        target_work = int(manifest.get("workItemCount") or 0)
        completed_work = int(payload.get("completedWorkItemCount") or 0)
        if target_work:
            payload["percent"] = min(
                100, round((completed_work / target_work) * 100)
            )
        payload["status"] = manifest.get("status")
        payload["verified"] = bool(
            manifest.get("status") == "succeeded"
            and manifest.get("receiptValidated") is True
        )
        return payload

    @staticmethod
    def _empty_progress(manifest: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "runId": manifest.get("runId"),
            "status": manifest.get("status"),
            "verified": bool(
                manifest.get("status") == "succeeded"
                and manifest.get("receiptValidated") is True
            ),
            "targetQuestionCount": int(manifest.get("targetCount") or 0),
            "completedQuestionCount": 0,
            "targetWorkItemCount": int(manifest.get("workItemCount") or 0),
            "completedWorkItemCount": 0,
            "percent": 0,
            "current": None,
            "events": [],
            "questions": [],
            "groups": [],
            "invalidEventCount": 0,
        }

    @classmethod
    def _parsed_progress(
        cls, manifest: Mapping[str, Any], raw: bytes
    ) -> dict[str, Any]:
        payload = cls._empty_progress(manifest)
        if not raw:
            return payload
        if len(raw) > MAX_PROGRESS_BYTES:
            payload["invalidEventCount"] = 1
            payload["warning"] = "進捗記録が上限を超えたため表示できません。"
            return payload

        targets = [
            dict(target)
            for target in manifest.get("progressTargets") or []
            if isinstance(target, Mapping) and target.get("id")
        ]
        target_by_alias: dict[str, dict[str, Any]] = {}
        for index, target in enumerate(targets, start=1):
            target["targetIndex"] = index
            aliases = {
                str(target.get("id") or ""),
                str(target.get("questionKey") or ""),
                *(str(value) for value in target.get("aliases") or []),
            } - {""}
            for alias in aliases:
                target_by_alias.setdefault(alias, target)
        stages = {
            str(stage.get("id")): dict(stage)
            for stage in manifest.get("progressStages") or []
            if isinstance(stage, Mapping) and stage.get("id")
        }
        raw_policy_targets = manifest.get("policyTargets")
        planned_work_items: set[tuple[str, str]] | None = None
        if isinstance(raw_policy_targets, Mapping) and raw_policy_targets:
            planned_work_items = set()
            for stage_id, raw_aliases in raw_policy_targets.items():
                stage_id = str(stage_id)
                if stage_id not in stages or not isinstance(raw_aliases, list):
                    continue
                stage_aliases = {str(value) for value in raw_aliases if value}
                for target in targets:
                    target_aliases = {
                        str(target.get("id") or ""),
                        str(target.get("questionKey") or ""),
                        *(str(value) for value in target.get("aliases") or []),
                    } - {""}
                    if target_aliases & stage_aliases:
                        planned_work_items.add((str(target["id"]), stage_id))
        invalid_count = 0
        events: list[dict[str, Any]] = []
        for raw_line in raw.splitlines()[:MAX_PROGRESS_EVENTS]:
            if not raw_line.strip():
                continue
            if len(raw_line) > MAX_PROGRESS_LINE_BYTES:
                invalid_count += 1
                continue
            try:
                value = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                invalid_count += 1
                continue
            if not isinstance(value, Mapping):
                invalid_count += 1
                continue
            event_type = str(value.get("event") or "")
            target = target_by_alias.get(str(value.get("questionId") or ""))
            stage_id = str(value.get("stageId") or "")
            stage = stages.get(stage_id) if stage_id else None
            if (
                event_type not in PROGRESS_EVENT_TYPES
                or target is None
                or (event_type == "stage_completed" and stage is None)
                or (stage_id and stage is None)
            ):
                invalid_count += 1
                continue
            if (
                event_type == "stage_completed"
                and planned_work_items is not None
                and (str(target["id"]), stage_id) not in planned_work_items
            ):
                continue
            raw_result = value.get("result")
            result: dict[str, Any] = {}
            if isinstance(raw_result, Mapping):
                for field in PROGRESS_RESULT_FIELDS:
                    if field not in raw_result:
                        continue
                    item = raw_result[field]
                    if isinstance(item, list):
                        result[field] = [str(entry)[:2000] for entry in item[:20]]
                    elif isinstance(item, Mapping):
                        result[field] = {
                            str(key)[:100]: str(entry)[:1000]
                            for key, entry in list(item.items())[:20]
                        }
                    elif item is not None:
                        result[field] = str(item)[:4000]
            events.append(
                {
                    "sequence": len(events) + 1,
                    "event": event_type,
                    "questionId": str(target["id"]),
                    "questionKey": str(target.get("questionKey") or ""),
                    "questionLabel": str(target.get("questionLabel") or "")
                    or f"問{target['targetIndex']}",
                    "targetIndex": int(target["targetIndex"]),
                    "listGroupId": str(target.get("listGroupId") or ""),
                    "bodyPreview": str(target.get("bodyPreview") or ""),
                    "stageId": stage_id or None,
                    "stageCode": str((stage or {}).get("code") or "") or None,
                    "stageLabel": str((stage or {}).get("label") or "") or None,
                    "result": result,
                    "at": str(value.get("at") or "")[:100] or None,
                }
            )
        if len(raw.splitlines()) > MAX_PROGRESS_EVENTS:
            invalid_count += len(raw.splitlines()) - MAX_PROGRESS_EVENTS

        completed_questions = {
            event["questionId"]
            for event in events
            if event["event"] == "question_completed"
        }
        completed_work_items = {
            (event["questionId"], event["stageId"])
            for event in events
            if event["event"] == "stage_completed" and event["stageId"]
        }
        events_by_question: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            events_by_question.setdefault(event["questionId"], []).append(event)
        questions: list[dict[str, Any]] = []
        for target in targets:
            question_id = str(target["id"])
            question_events = events_by_question.get(question_id, [])
            if not question_events:
                continue
            latest_stage_events: dict[str, dict[str, Any]] = {}
            for event in question_events:
                if event["event"] == "stage_completed" and event["stageId"]:
                    latest_stage_events[str(event["stageId"])] = event
            outputs = sorted(
                latest_stage_events.values(),
                key=lambda event: int(event["sequence"]),
            )
            display_event = outputs[-1] if outputs else question_events[-1]
            questions.append(
                {
                    **display_event,
                    "completed": question_id in completed_questions,
                    "outputs": outputs,
                }
            )
        groups: list[dict[str, Any]] = []
        for group_id in dict.fromkeys(
            str(target.get("listGroupId") or "") for target in targets
        ):
            group_targets = {
                str(target["id"])
                for target in targets
                if str(target.get("listGroupId") or "") == group_id
            }
            group_completed = group_targets & completed_questions
            groups.append(
                {
                    "listGroupId": group_id,
                    "targetQuestionCount": len(group_targets),
                    "completedQuestionCount": len(group_completed),
                    "percent": round(
                        (len(group_completed) / len(group_targets)) * 100
                    ) if group_targets else 0,
                }
            )
        target_count = len(targets) or int(manifest.get("targetCount") or 0)
        payload.update(
            {
                "targetQuestionCount": target_count,
                "completedQuestionCount": len(completed_questions),
                "completedWorkItemCount": len(completed_work_items),
                "percent": round(
                    (len(completed_questions) / target_count) * 100
                ) if target_count else 0,
                "current": events[-1] if events else None,
                "events": events[-40:],
                "questions": questions,
                "groups": groups,
                "invalidEventCount": invalid_count,
            }
        )
        return payload

    def write_baseline(
        self,
        qualification: str,
        run_id: str,
        roots: tuple[Path, ...],
    ) -> Path:
        manifest_path = self._manifest_path(qualification, run_id)
        with self._lock:
            manifest = self._load_manifest(manifest_path)
            agent_output = self._result_path(manifest_path, manifest).parent.resolve()
            tracked_roots = [
                path.resolve() for path in roots if path.resolve() != agent_output
            ]
            record_paths: list[Path] = []
            for value in [
                *(manifest.get("allowedPatchFiles") or []),
                *(manifest.get("allowedWriteFiles") or []),
            ]:
                relative = Path(str(value))
                absolute = (self.repo_root / relative).resolve()
                if (
                    relative.is_absolute()
                    or not absolute.is_relative_to(self.repo_root)
                ):
                    raise QualificationRunError("record baselineのpathが不正です。")
                if relative.suffix.lower() in {".json", ".jsonl"}:
                    record_paths.append(relative)
            source_record_paths: list[Path] = []
            for value in manifest.get("sourceFiles") or []:
                relative = Path(str(value))
                absolute = (self.repo_root / relative).resolve()
                if (
                    relative.is_absolute()
                    or not absolute.is_relative_to(self.repo_root)
                ):
                    raise QualificationRunError("source baselineのpathが不正です。")
                if relative.suffix.lower() == ".json":
                    source_record_paths.append(relative)
            payload = {
                "schemaVersion": "question-maintenance-baseline/v1",
                "roots": [
                    path.relative_to(self.repo_root).as_posix()
                    for path in tracked_roots
                ],
                "files": _snapshot_roots(self.repo_root, tracked_roots),
                "recordSnapshots": {
                    relative.as_posix(): _record_snapshot(self.repo_root / relative)
                    for relative in sorted(set(record_paths))
                },
                "sourceRecordSnapshots": {
                    relative.as_posix(): _record_snapshot(self.repo_root / relative)
                    for relative in sorted(set(source_record_paths))
                },
            }
            baseline_path = manifest_path.parent / "baseline.json"
            self._write_json(baseline_path, payload)
            baseline_hash = hashlib.sha256(baseline_path.read_bytes()).hexdigest()
            manifest["baselinePath"] = str(
                baseline_path.relative_to(self.repo_root)
            )
            manifest["baselineHash"] = baseline_hash
            manifest["deltaUnknown"] = False
            manifest["updatedAt"] = _now()
            self._write_manifest(manifest_path, manifest)
        return baseline_path

    def prompt(self, qualification: str, run_id: str) -> str:
        manifest = self.get(qualification, run_id)
        relative = str(manifest.get("promptPath") or "")
        if not relative:
            raise QualificationRunError("この作業には再コピーできるCodex依頼がありません。")
        path = (self.repo_root / relative).resolve()
        if not path.is_relative_to(self.root.resolve()) or not path.is_file():
            raise QualificationRunError("保存済みのCodex依頼が見つかりません。")
        return path.read_text(encoding="utf-8")

    def _manifest_path(self, qualification: str, run_id: str) -> Path:
        return self.root / _safe_segment(qualification) / _safe_segment(run_id) / "manifest.json"

    def _recover_interrupted_runs(self) -> None:
        if not self.root.is_dir():
            return
        with self._lock:
            for path in self.root.glob("*/*/manifest.json"):
                manifest = self._load_manifest(path)
                if manifest.get("status") not in {"queued", "running", "validating"}:
                    continue
                if (
                    manifest.get("status") == "validating"
                    and manifest.get("kind") == "human"
                    and manifest.get("receiptValidated") is True
                ):
                    artifact_sync = manifest.get("artifactSync")
                    artifact_sync = (
                        artifact_sync
                        if isinstance(artifact_sync, Mapping)
                        else {}
                    )
                    manifest["status"] = "succeeded"
                    manifest["artifactSync"] = {
                        "status": "interrupted",
                        "groups": list(artifact_sync.get("groups") or []),
                        "message": (
                            "公開用データの自動更新中にローカルUIが停止しました。"
                            "問題詳細又は管理機能から再生成できます。"
                        ),
                    }
                    manifest["error"] = None
                    manifest["updatedAt"] = _now()
                    manifest["finishedAt"] = manifest["updatedAt"]
                    self._write_manifest(path, manifest)
                    continue
                was_running = manifest.get("status") in {"running", "validating"}
                changed_files: list[str] | None = None
                if was_running and manifest.get("kind") == "human":
                    changed_files = self._recover_baseline_delta(path, manifest)
                if changed_files is None:
                    manifest["status"] = "interrupted"
                    manifest["deltaUnknown"] = bool(
                        was_running and manifest.get("kind") == "human"
                    )
                    manifest["error"] = (
                        "ローカルUIの再起動で処理が中断され、差分を安全に復元できません。"
                        if manifest["deltaUnknown"]
                        else "ローカルUIの再起動で処理が中断されました。再開できます。"
                    )
                else:
                    summary = (
                        "ローカルUIの再起動でCodex turnが中断されました。"
                        + (
                            " 未確定差分: " + ", ".join(changed_files[:20])
                            if changed_files
                            else " file差分はありません。"
                        )
                    )
                    receipt = {
                        "status": "failed",
                        "summary": summary,
                        "commands": [],
                        "changedFiles": changed_files,
                        "resolvedFailedDeltaPaths": [],
                    }
                    receipt_path = self._result_path(path, manifest)
                    self._write_json(receipt_path, receipt)
                    manifest["status"] = "failed"
                    manifest["result"] = receipt
                    manifest["resultReceiptHash"] = hashlib.sha256(
                        receipt_path.read_bytes()
                    ).hexdigest()
                    manifest["deltaUnknown"] = False
                    manifest["error"] = summary
                manifest["updatedAt"] = _now()
                manifest["finishedAt"] = manifest["updatedAt"]
                self._write_manifest(path, manifest)

    def _recover_baseline_delta(
        self,
        manifest_path: Path,
        manifest: Mapping[str, Any],
    ) -> list[str] | None:
        baseline_path = manifest_path.parent / "baseline.json"
        expected_hash = str(manifest.get("baselineHash") or "")
        if not baseline_path.is_file() or not expected_hash:
            return None
        raw = baseline_path.read_bytes()
        if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_hash):
            return None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if (
            not isinstance(payload, Mapping)
            or payload.get("schemaVersion") != "question-maintenance-baseline/v1"
            or not isinstance(payload.get("files"), Mapping)
            or not isinstance(payload.get("roots"), list)
        ):
            return None
        roots: list[Path] = []
        for value in payload["roots"]:
            relative = Path(str(value))
            absolute = (self.repo_root / relative).resolve()
            if relative.is_absolute() or not absolute.is_relative_to(self.repo_root):
                return None
            roots.append(absolute)
        before = {str(key): str(value) for key, value in payload["files"].items()}
        try:
            after = _snapshot_roots(self.repo_root, roots)
        except (OSError, QualificationRunError):
            return None
        return sorted(
            path
            for path in before.keys() | after.keys()
            if before.get(path) != after.get(path)
        )

    def _apply_result_receipt(
        self, manifest_path: Path, manifest: dict[str, Any]
    ) -> dict[str, Any]:
        if manifest.get("kind") != "human":
            return manifest
        receipt_path = self._result_path(manifest_path, manifest)
        if not receipt_path.is_file():
            return manifest
        if receipt_path.is_symlink():
            manifest["receiptError"] = "完了receiptにsymlinkは使用できません。"
            manifest["updatedAt"] = _now()
            self._write_manifest(manifest_path, manifest)
            return manifest
        raw = receipt_path.read_bytes()
        receipt_hash = hashlib.sha256(raw).hexdigest()
        if receipt_hash == manifest.get("resultReceiptHash"):
            return manifest
        manifest["resultReceiptHash"] = receipt_hash
        manifest["updatedAt"] = _now()
        try:
            value = json.loads(raw.decode("utf-8"))
            receipt = self._validated_result_receipt(value)
        except (UnicodeDecodeError, json.JSONDecodeError, QualificationRunError) as exc:
            manifest["receiptError"] = str(exc)
            self._write_manifest(manifest_path, manifest)
            return manifest

        manifest["receiptError"] = None
        requires_server_validation = bool(
            manifest.get("provider") == "Codex App Server"
            and manifest.get("sandbox") == "workspace-write"
        )
        manifest["status"] = (
            "validating"
            if receipt["status"] == "succeeded"
            and requires_server_validation
            and manifest.get("receiptValidated") is not True
            else receipt["status"]
        )
        manifest["result"] = receipt
        manifest["error"] = (
            receipt["summary"] if receipt["status"] == "failed" else None
        )
        manifest["finishedAt"] = (
            manifest["updatedAt"]
            if manifest["status"] in {"succeeded", "failed"}
            else None
        )
        self._write_manifest(manifest_path, manifest)
        return manifest

    @staticmethod
    def _result_path(manifest_path: Path, manifest: Mapping[str, Any]) -> Path:
        if manifest.get("kind") == "human":
            return manifest_path.parent / "agent_output" / "result.json"
        return manifest_path.parent / "result.json"

    @staticmethod
    def _validated_result_receipt(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise QualificationRunError("完了receiptはJSON objectで保存してください。")
        status = str(value.get("status") or "")
        if status not in {"succeeded", "failed"}:
            raise QualificationRunError("完了receiptのstatusはsucceeded又はfailedです。")
        summary = str(value.get("summary") or "").strip()
        if not summary:
            raise QualificationRunError("完了receiptにsummaryが必要です。")
        commands_value = value.get("commands") or []
        if not isinstance(commands_value, list):
            raise QualificationRunError("完了receiptのcommandsは配列で保存してください。")
        commands: list[dict[str, str]] = []
        for item in commands_value:
            if not isinstance(item, Mapping):
                raise QualificationRunError("commandsの各要素はobjectで保存してください。")
            command = str(item.get("command") or "").strip()
            command_status = str(item.get("status") or "").strip()
            command_status = {
                "passed": "pass",
                "failed": "fail",
            }.get(command_status, command_status)
            if not command or command_status not in {"pass", "fail"}:
                raise QualificationRunError("commandsにはcommandとpass/failのstatusが必要です。")
            commands.append({"command": command[:2000], "status": command_status})
        if status == "succeeded" and (
            not commands or any(item["status"] != "pass" for item in commands)
        ):
            raise QualificationRunError(
                "succeededの完了receiptには、1件以上のpass検証が必要です。"
            )
        changed_files_value = value.get("changedFiles") or []
        if not isinstance(changed_files_value, list) or not all(
            isinstance(item, str) for item in changed_files_value
        ):
            raise QualificationRunError("changedFilesは文字列配列で保存してください。")
        resolved_value = value.get("resolvedFailedDeltaPaths") or []
        if not isinstance(resolved_value, list) or not all(
            isinstance(item, str) for item in resolved_value
        ):
            raise QualificationRunError(
                "resolvedFailedDeltaPathsは文字列配列で保存してください。"
            )
        if status != "succeeded" and resolved_value:
            raise QualificationRunError(
                "失敗receiptでは未確定差分を解決済みにできません。"
            )
        return {
            "status": status,
            "summary": summary[:4000],
            "commands": commands,
            "changedFiles": [str(item)[:2000] for item in changed_files_value],
            "resolvedFailedDeltaPaths": [
                str(item)[:2000] for item in resolved_value
            ],
        }

    def _with_receipt_contract(
        self,
        prompt: str,
        receipt_path: Path,
        progress_path: Path,
        manifest_path: Path,
        resolvable_failed_paths: list[str],
    ) -> str:
        python_executable = (self.repo_root / ".venv" / "bin" / "python").resolve()
        example = {
            "status": "succeeded",
            "summary": "対象工程と検証が完了した。",
            "commands": [{"command": "<実行した検証>", "status": "pass"}],
            "changedFiles": [],
            "resolvedFailedDeltaPaths": [],
        }
        started_example = {
            "event": "question_started",
            "questionId": "<progressTargets[].id>",
            "at": "<ISO 8601>",
        }
        stage_example = {
            "event": "stage_completed",
            "questionId": "<progressTargets[].id>",
            "stageId": "<progressStages[].id>",
            "result": {
                "summary": "正答判断を完了",
                "correctChoiceText": ["正しい", "誤り"],
            },
            "at": "<ISO 8601>",
        }
        completed_example = {
            "event": "question_completed",
            "questionId": "<progressTargets[].id>",
            "at": "<ISO 8601>",
        }
        return "\n".join(
            [
                prompt.rstrip(),
                "",
                "## 画面用の問題別進捗",
                "",
                f"対象IDと工程IDは `{manifest_path}` のprogressTargetsとprogressStagesを使う。",
                "stage_completedはpolicyTargetsでその工程の対象になる問題だけに追記する。",
                f"作業中、次のJSONLへ1イベント1行で追記する: `{progress_path}`",
                "各行は追記直後に完全なJSONと改行を保存し、既存行は変更しない。",
                "問題を始める直前にquestion_started、各工程の判断完了直後にstage_completed、問題の全工程完了直後にquestion_completedを追記する。",
                "resultには思考過程ではなく、利用者が確認できる最終判断・正答・解説文などの出力だけを記録する。",
                f"開始例: `{json.dumps(started_example, ensure_ascii=False, separators=(',', ':'))}`",
                f"工程完了例: `{json.dumps(stage_example, ensure_ascii=False, separators=(',', ':'))}`",
                f"問題完了例: `{json.dumps(completed_example, ensure_ascii=False, separators=(',', ':'))}`",
                "正答工程ではcorrectChoiceText、解説工程ではexplanationTextのように、該当工程の確定出力だけをresultへ入れる。該当しないfieldは省略する。",
                "progress.jsonl自身はchangedFilesへ含めない。",
                "",
                "## 完了記録",
                "",
                f"このローカルUIのPython検証は、正本中のpython又はpython3を必ず `{python_executable}` に読み替えて実行する。system Pythonへ代替しない。",
                "commands各要素のstatusは、成功ならpass、失敗ならfailの文字列だけを保存する。passed又はfailed等の別表記は使わない。",
                "正本指定の検証が1件でもfailなら、独自の代替検証だけで成功扱いにせず、修正して正本指定の検証を再実行する。failが残る場合は完了receipt自体をfailedにする。",
                f"完了時に検証結果を次へJSONで保存する: `{receipt_path}`",
                f"`{json.dumps(example, ensure_ascii=False, separators=(',', ':'))}`",
                "changedFilesには実際の最終差分だけを記載し、result.json自身は含めない。",
                *(
                    [
                        "次の未確定差分は内容と検証結果を確認する:",
                        *(f"- `{path}`" for path in resolvable_failed_paths),
                        "変更不要でも正しいと確認できたpathだけをresolvedFailedDeltaPathsへ記載する。",
                    ]
                    if resolvable_failed_paths
                    else []
                ),
                "未完了時はstatusをfailedにし、summaryへ理由を記録する。",
                (
                    "全検証とprogress保存を終えてからresult.jsonを最後のfile操作として保存する。"
                    "result.json保存後はtool、command、web、file操作を追加せず、"
                    "直ちに最終応答を返してturnを終了する。"
                ),
                "",
            ]
        )

    @staticmethod
    def _load_manifest(path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise QualificationRunError("作業履歴が見つかりません。")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise QualificationRunError("作業履歴の形式が不正です。")
        return value

    @staticmethod
    def _write_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
        QualificationRunStore._write_json(path, manifest)

    @staticmethod
    def _write_json(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def _public(manifest: Mapping[str, Any]) -> dict[str, Any]:
        value = copy.deepcopy(dict(manifest))
        value.pop("resultReceiptHash", None)
        return value


class QualificationRunCoordinator:
    def __init__(
        self,
        repo_root: Path,
        workflow: QualificationWorkflow,
        synchronizer: ArtifactSynchronizer,
        jobs: JobManager,
        secret: str,
        *,
        store: QualificationRunStore | None = None,
        app_server: Any | None = None,
        work_versions: QuestionWorkVersionStore | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.workflow = workflow
        self.synchronizer = synchronizer
        self.jobs = jobs
        self.secret = secret.encode("utf-8")
        self.store = store or QualificationRunStore(self.repo_root)
        self.app_server = app_server
        self.work_versions = (
            work_versions
            or getattr(workflow, "work_versions", None)
            or QuestionWorkVersionStore(self.repo_root)
        )

    def preview(
        self,
        qualification: str,
        stage_id: str,
        mode: str,
        *,
        stage_ids: list[str] | None = None,
        list_group_id: str | None = None,
        list_group_ids: list[str] | None = None,
        resumed_from: str | None = None,
    ) -> dict[str, Any]:
        plan = self._plan(
            qualification,
            stage_id,
            mode,
            resumed_from,
            stage_ids=stage_ids,
            list_group_id=list_group_id,
            list_group_ids=list_group_ids,
        )
        group_previews: list[dict[str, Any]] = []
        blocking_warnings: list[dict[str, Any]] = []
        if plan["kind"] == "machine":
            for group_id in plan["targetGroupIds"]:
                preview = self.synchronizer.preview(
                    qualification, group_id, force=bool(plan.get("force"))
                )
                group_previews.append(
                    {
                        "listGroupId": group_id,
                        "previewToken": preview["previewToken"],
                        "questionCount": preview["questionCount"],
                        "localReady": preview["localReady"],
                    }
                )
                blocking_warnings.extend(preview.get("requiredFieldWarnings") or [])
                blocking_warnings.extend(
                    {
                        "detail": "失敗turnの未確定patchを成功runで確定してください。",
                        "path": path,
                        "fields": [],
                    }
                    for path in preview.get("failedDeltaPaths") or []
                )
        token_payload = {"plan": plan, "groupPreviews": group_previews}
        return {
            "qualification": qualification,
            "stageId": plan["stageId"],
            "stageIds": list(plan.get("stageIds") or [plan["stageId"]]),
            "stageCode": plan["stageCode"],
            "stageLabel": plan["stageLabel"],
            "purpose": plan["purpose"],
            "kind": plan["kind"],
            "mode": mode,
            "modeLabel": plan["modeLabel"],
            "resumedFrom": resumed_from,
            "targetCount": plan["targetCount"],
            "workItemCount": int(plan.get("workItemCount") or plan["targetCount"]),
            "stageCount": int(
                plan.get("stageCount") or len(plan.get("stageIds") or [plan["stageId"]])
            ),
            "targetGroupIds": plan["targetGroupIds"],
            "scopeListGroupId": plan.get("scopeListGroupId"),
            "scopeListGroupIds": list(plan.get("scopeListGroupIds") or []),
            "canonicalDocs": list(plan.get("canonicalDocs") or []),
            "sourceFileCount": len(plan.get("sourceFiles") or []),
            "outputFileCount": len(plan.get("outputFiles") or []),
            "canStart": bool(plan["targetCount"])
            and not blocking_warnings
            and (
                plan["kind"] == "machine"
                or self.app_server is None
                or bool(self.app_server.configured)
            ),
            "blockingWarnings": blocking_warnings[:20],
            "isProductionWrite": False,
            "previewToken": self._token(token_payload),
        }

    def start(
        self,
        qualification: str,
        stage_id: str,
        mode: str,
        preview_token: str,
        *,
        stage_ids: list[str] | None = None,
        list_group_id: str | None = None,
        list_group_ids: list[str] | None = None,
        resumed_from: str | None = None,
    ) -> dict[str, Any]:
        preview = self.preview(
            qualification,
            stage_id,
            mode,
            stage_ids=stage_ids,
            list_group_id=list_group_id,
            list_group_ids=list_group_ids,
            resumed_from=resumed_from,
        )
        if not hmac.compare_digest(str(preview["previewToken"]), preview_token):
            raise QualificationRunError("対象が更新されました。もう一度確認してください。")
        if not preview["canStart"]:
            if preview["blockingWarnings"]:
                if any(
                    warning.get("path")
                    for warning in preview["blockingWarnings"]
                    if isinstance(warning, Mapping)
                ):
                    raise QualificationRunError(
                        "失敗又は中断turnの未確定差分があるため開始できません。"
                    )
                raise QualificationRunError("必須field不足があるため開始できません。")
            raise QualificationRunError("選択した範囲に対象はありません。")

        plan = self._plan(
            qualification,
            stage_id,
            mode,
            resumed_from,
            stage_ids=stage_ids,
            list_group_id=list_group_id,
            list_group_ids=list_group_ids,
        )
        if plan["kind"] == "human":
            selected_stage_ids = list(plan.get("stageIds") or [stage_id])
            prompt_scope = {}
            if list_group_ids is not None:
                prompt_scope["list_group_ids"] = list_group_ids
            elif list_group_id is not None:
                prompt_scope["list_group_id"] = list_group_id
            if len(selected_stage_ids) > 1:
                prompt = self.workflow.prompt_many(
                    qualification,
                    selected_stage_ids,
                    mode,
                    **prompt_scope,
                )["prompt"]
            elif prompt_scope:
                prompt = self.workflow.prompt(
                    qualification,
                    selected_stage_ids[0],
                    mode,
                    **prompt_scope,
                )["prompt"]
            else:
                prompt = self.workflow.prompt(
                    qualification, selected_stage_ids[0], mode
                )["prompt"]
            if self.app_server is None:
                run = self.store.create(
                    plan,
                    status="awaiting_changes",
                    prompt=prompt,
                    resumed_from=resumed_from,
                )
                saved_prompt = self.store.prompt(qualification, run["runId"])
                return {"run": run, "prompt": saved_prompt, "job": None}
            try:
                self.app_server.assert_subscription_access(force=False)
            except Exception as exc:  # noqa: BLE001
                raise QualificationRunError(str(exc)) from exc
            plan = {
                **plan,
                "workType": "maintenance",
                "sandbox": "workspace-write",
                "provider": self.app_server.provider,
                "parallelStrategy": "read_only_research",
                "parallelWorkerLimit": (
                    MAINTENANCE_RESEARCH_WORKERS
                    if int(plan.get("targetCount") or 0) > 1
                    else 1
                ),
                "writeWorkerLimit": 1,
            }
            maintenance_phases = _maintenance_session_phases(plan)
            if len(maintenance_phases) > 1:
                phase_executions = [
                    {
                        **phase,
                        "index": index,
                        "status": "pending",
                        "childRunId": None,
                        "targetCount": None,
                        "threadId": None,
                        "sessionId": None,
                        "turnId": None,
                        "researchThreadId": None,
                        "researchSessionId": None,
                        "model": None,
                        "reasoningEffort": None,
                        "error": None,
                    }
                    for index, phase in enumerate(maintenance_phases, start=1)
                ]
                flow_plan = {
                    **plan,
                    "kind": "orchestration",
                    "workType": "maintenance_flow",
                    "maintenancePhases": maintenance_phases,
                    "phaseExecutions": phase_executions,
                    "currentPhaseId": None,
                    "childRunIds": [],
                }
                run = self.store.create(
                    flow_plan,
                    status="queued",
                    prompt=prompt,
                    resumed_from=resumed_from,
                    append_receipt_contract=False,
                )
                try:
                    job = self.jobs.start(
                        kind="codex-maintenance-flow",
                        key=REPOSITORY_OPERATION_KEY,
                        worker=lambda emit: self._run_maintenance_flow(
                            qualification,
                            run["runId"],
                            emit,
                        ),
                    )
                except JobConflictError:
                    self.store.update(
                        qualification,
                        run["runId"],
                        status="failed",
                        error="この資格で別の整備処理が実行中です。",
                    )
                    raise
                run = self.store.update(
                    qualification, run["runId"], jobId=job["jobId"]
                )
                return {"run": run, "prompt": None, "job": job}
            run = self.store.create(
                plan,
                status="queued",
                prompt=prompt,
                resumed_from=resumed_from,
            )
            saved_prompt = self.store.prompt(qualification, run["runId"])
            try:
                job = self.jobs.start(
                    kind="codex-maintenance",
                    key=REPOSITORY_OPERATION_KEY,
                    worker=lambda emit: self._run_human(
                        qualification,
                        run["runId"],
                        saved_prompt,
                        "maintenance",
                        emit,
                    ),
                )
            except JobConflictError:
                self.store.update(
                    qualification,
                    run["runId"],
                    status="failed",
                    error="この資格で別の整備処理が実行中です。",
                )
                raise
            run = self.store.update(
                qualification, run["runId"], jobId=job["jobId"]
            )
            return {"run": run, "prompt": None, "job": job}

        run = self.store.create(
            plan, status="queued", resumed_from=resumed_from
        )
        try:
            job = self.jobs.start(
                kind="qualification-sync",
                key=REPOSITORY_OPERATION_KEY,
                worker=lambda emit: self._run_delivery(plan, run["runId"], emit),
            )
        except JobConflictError:
            self.store.update(
                qualification,
                run["runId"],
                status="failed",
                error="この資格で別の出力処理が実行中です。",
            )
            raise
        run = self.store.update(
            qualification, run["runId"], jobId=job["jobId"]
        )
        return {"run": run, "prompt": None, "job": job}

    def recent(self, qualification: str) -> dict[str, Any]:
        runs = [
            run
            for run in self.store.list(qualification, limit=100)
            if run.get("workType") not in {"evaluation", "reevaluation"}
            and not run.get("parentRunId")
        ][:8]
        return {
            "qualification": qualification,
            "runs": runs,
            "activeRun": next(
                (run for run in runs if run.get("status") in LIVE_RUN_STATUSES),
                None,
            ),
        }

    def progress(self, qualification: str, run_id: str) -> dict[str, Any]:
        run = self.store.get(qualification, run_id)
        if str(run.get("qualification") or "") != qualification:
            raise QualificationRunError("対象資格と作業履歴が一致しません。")
        if run.get("workType") == "maintenance_flow":
            return self.store.combined_progress(qualification, run_id)
        return self.store.progress(qualification, run_id)

    def resume_prompt(self, qualification: str, run_id: str) -> dict[str, Any]:
        run = self.store.get(qualification, run_id)
        return {"run": run, "prompt": self.store.prompt(qualification, run_id)}

    def start_review(
        self,
        question: Mapping[str, Any],
        review: Mapping[str, Any],
        *,
        work_type: str,
    ) -> dict[str, Any]:
        if self.app_server is None:
            raise QualificationRunError("Codex App Serverが設定されていません。")
        if work_type not in {"maintenance", "rework"}:
            raise ValueError(f"unsupported work type: {work_type}")
        prompt = str(review.get("prompt") or "").strip()
        if not prompt:
            raise QualificationRunError("Codex App Serverへ渡すpromptがありません。")
        try:
            self.app_server.assert_subscription_access(force=False)
        except Exception as exc:  # noqa: BLE001
            raise QualificationRunError(str(exc)) from exc
        qualification = str(question["qualification"])
        list_group_id = str(question["listGroupId"])
        question_id = str(question["id"])
        target_group_ids = self._review_target_group_ids(question, review)
        investigation_scope = str(
            review.get("investigationScope") or "current_question"
        )
        stage_code = "再整備" if work_type == "rework" else "整備"
        (
            allowed_patch_dirs,
            allowed_write_areas,
            allowed_patch_files,
            allowed_write_files,
        ) = self._review_write_contract(question, review)
        selected_stages: set[str] = set()
        if work_type == "rework":
            snapshot = review.get("evaluationSnapshot")
            rework_items = (
                snapshot.get("reworkItems")
                if isinstance(snapshot, Mapping)
                else None
            )
            selected_stages = {
                str(item.get("stage") or "")
                for item in rework_items or []
                if isinstance(item, Mapping)
            }
            selected_dirs = set().union(
                *(
                    REWORK_STAGE_PATCH_DIR_NAMES.get(stage, set())
                    for stage in selected_stages
                )
            )
            if selected_dirs:
                allowed_patch_dirs = selected_dirs
                allowed_write_areas = (
                    {"review"}
                    if selected_stages & {"03b"}
                    else set()
                )
                allowed_patch_files = self._review_patch_files(
                    question,
                    review,
                    selected_dirs,
                    {
                        suffix
                        for patch_dir in selected_dirs
                        for suffix in [
                            REVIEW_FLAG_SUFFIX_BY_PATCH_DIR.get(patch_dir)
                        ]
                        if suffix
                    }
                    | ({"lawRevision"} if "03b" in selected_stages else set()),
                )
                allowed_write_files = (
                    {self._law_review_sidecar_file(question)}
                    if selected_stages & {"03b"}
                    and investigation_scope == "current_question"
                    else set()
                )
        if "review" in allowed_write_areas:
            allowed_write_files = {
                self._law_review_sidecar_path(qualification, group_id)
                for group_id in target_group_ids
            }
        if (
            investigation_scope == "current_question"
            or review.get("requestKind") != "qualification_law_audit"
        ):
            target_record_alias_groups = [
                sorted(self._question_record_aliases(question))
            ]
        else:
            target_record_alias_groups = [
                sorted({str(value) for value in group if value})
                for group in review.get("targetRecordAliasGroups") or []
                if isinstance(group, (list, tuple, set)) and group
            ]
            if (
                review.get("requestKind") == "qualification_law_audit"
                and not target_record_alias_groups
            ):
                raise QualificationRunError(
                    "法令監査の対象record identityを安全に特定できません。"
                )
        target_record_aliases = sorted(
            {
                value
                for group in target_record_alias_groups
                for value in group
            }
        )
        source_files = (
            sorted(
                {
                    str(value)
                    for value in review.get("targetSourceFiles") or []
                    if value
                }
            )
            if review.get("requestKind") == "qualification_law_audit"
            else [str(question.get("paths", {}).get("source") or "")]
        )
        if review.get("requestKind") == "qualification_law_audit":
            raw_source_scopes = review.get("targetSourceRecordScopes")
            if not isinstance(raw_source_scopes, Mapping):
                raise QualificationRunError(
                    "法令監査のsource別record scopeを確認できません。"
                )
            target_source_record_scopes = {
                self._maintenance_relative_path(path).as_posix(): (
                    _normalized_alias_groups(groups)
                )
                for path, groups in raw_source_scopes.items()
            }
            if (
                set(target_source_record_scopes) != set(source_files)
                or any(not groups for groups in target_source_record_scopes.values())
            ):
                raise QualificationRunError(
                    "法令監査のsource別record scopeが対象sourceと一致しません。"
                )
        else:
            target_source_record_scopes = {
                source_files[0]: target_record_alias_groups
            }
        scoped_groups = _normalized_alias_groups(
            [
                group
                for groups in target_source_record_scopes.values()
                for group in groups
            ]
        )
        if {
            tuple(group) for group in scoped_groups
        } != {tuple(group) for group in target_record_alias_groups}:
            raise QualificationRunError(
                "対象record scopeとsource別scopeが一致しません。"
            )

        review_flag_suffixes: set[str] | None = None
        if review.get("requestKind") == "qualification_law_audit":
            review_flag_suffixes = {"lawRevision"}
        elif selected_stages:
            review_flag_suffixes = {
                suffix
                for patch_dir in allowed_patch_dirs
                for suffix in [
                    REVIEW_FLAG_SUFFIX_BY_PATCH_DIR.get(patch_dir)
                ]
                if suffix
            } | ({"lawRevision"} if "03b" in selected_stages else set())
        target_record_scopes: dict[str, list[list[str]]] = {}
        scoped_review = {
            **review,
            "investigationScope": "current_question",
        }
        for source_path, groups in target_source_record_scopes.items():
            scoped_files = self._review_patch_files(
                {"paths": {"source": source_path, "patches": []}},
                scoped_review,
                set(allowed_patch_dirs),
                review_flag_suffixes,
            )
            for path in scoped_files & set(allowed_patch_files):
                _add_record_scope(target_record_scopes, path, groups)
            source_parts = Path(source_path).parts
            if len(source_parts) >= 4:
                sidecar = self._law_review_sidecar_path(
                    qualification, source_parts[3]
                )
                if sidecar in allowed_write_files:
                    _add_record_scope(target_record_scopes, sidecar, groups)
        scoped_record_files = {
            path
            for path in [*allowed_patch_files, *allowed_write_files]
            if Path(path).suffix.lower() in {".json", ".jsonl"}
            and (
                set(Path(path).parts) & allowed_patch_dirs
                or "/review/law_revision_audit/" in f"/{path}"
            )
        }
        if scoped_record_files - set(target_record_scopes):
            raise QualificationRunError(
                "対象file別のrecord scopeを安全に作成できません。"
            )
        catalog_loader = getattr(self.workflow, "catalog", None)
        catalog = (
            catalog_loader(qualification)
            if callable(catalog_loader)
            else QualificationWorkflow(self.repo_root, None).catalog(qualification)
        )
        policy_by_id = {
            str(stage["id"]): stage
            for stage in catalog["stages"]
            if stage.get("policyVersion") is not None
        }
        issue_types = {
            str(value) for value in review.get("issueTypes") or [] if value
        }
        if review.get("requestKind") == "qualification_law_audit":
            requested_policy_ids = {"law_audit"}
        elif work_type == "rework" and selected_stages:
            requested_policy_ids = {
                REWORK_POLICY_STAGE_IDS[stage]
                for stage in selected_stages
                if stage in REWORK_POLICY_STAGE_IDS
            }
        elif _review_requests_law_audit(review):
            requested_policy_ids = {"law_audit"}
        else:
            requested_policy_ids = {
                POLICY_STAGE_BY_PATCH_DIR[patch_dir]
                for patch_dir in allowed_patch_dirs
                if patch_dir in POLICY_STAGE_BY_PATCH_DIR
            }
        policy_stage_ids = [
            str(stage["id"])
            for stage in catalog["stages"]
            if str(stage["id"]) in requested_policy_ids
        ]
        if not policy_stage_ids:
            raise QualificationRunError("整備対象の工程バージョンを特定できません。")
        canonical_docs = list(
            dict.fromkeys(
                path
                for stage_id in policy_stage_ids
                for path in policy_by_id[stage_id].get("canonicalDocs") or []
            )
        )
        plan = {
            "qualification": qualification,
            "stageId": work_type,
            "stageIds": [work_type],
            "stageCode": stage_code,
            "stageLabel": str(question.get("questionLabel") or question_id),
            "mode": "question",
            "modeLabel": {
                "current_group": "対象フォルダ",
                "qualification": "対象資格全体",
            }.get(investigation_scope, "対象問題のみ"),
            "kind": "human",
            "workType": work_type,
            "targetCount": max(1, len(target_record_alias_groups)),
            "workItemCount": max(1, len(target_record_alias_groups)),
            "targetGroupIds": target_group_ids,
            "scopeListGroupId": (
                target_group_ids[0] if len(target_group_ids) == 1 else None
            ),
            "scopeListGroupIds": target_group_ids,
            "targetQuestionIds": [question_id],
            "targetQuestionKeys": target_record_aliases,
            "sourceFiles": source_files,
            "targetRecordAliases": target_record_aliases,
            "targetRecordAliasGroups": target_record_alias_groups,
            "targetSourceRecordScopes": target_source_record_scopes,
            "targetRecordScopes": target_record_scopes,
            "reviewId": review.get("reviewId"),
            "stateHash": question.get("stateHash"),
            "sandbox": "workspace-write",
            "provider": self.app_server.provider,
            "parallelStrategy": "read_only_research",
            "parallelWorkerLimit": (
                MAINTENANCE_RESEARCH_WORKERS
                if len(target_record_alias_groups) > 1
                else 1
            ),
            "writeWorkerLimit": 1,
            "canonicalDocs": canonical_docs,
            "catalogHash": catalog["catalogHash"],
            "policyVersions": {
                stage_id: normalize_policy_version(
                    policy_by_id[stage_id]["policyVersion"]
                )
                for stage_id in policy_stage_ids
            },
            "policyFingerprints": {
                stage_id: str(policy_by_id[stage_id]["policyFingerprint"])
                for stage_id in policy_stage_ids
            },
            "policyTargets": {
                stage_id: list(target_record_aliases)
                for stage_id in policy_stage_ids
            },
            "allowedPatchDirs": sorted(allowed_patch_dirs),
            "allowedWriteAreas": sorted(allowed_write_areas),
            "allowedPatchFiles": sorted(allowed_patch_files),
            "allowedWriteFiles": sorted(allowed_write_files),
        }
        plan["resolvableFailedDeltaPaths"] = self._resolvable_for_plan(
            qualification,
            target_group_ids,
            plan,
        )
        run = self.store.create(plan, status="queued", prompt=prompt)
        saved_prompt = self.store.prompt(qualification, run["runId"])
        try:
            job = self.jobs.start(
                kind=f"codex-{work_type}",
                key=REPOSITORY_OPERATION_KEY,
                worker=lambda emit: self._run_human(
                    qualification,
                    run["runId"],
                    saved_prompt,
                    work_type,
                    emit,
                ),
            )
        except JobConflictError:
            self.store.update(
                qualification,
                run["runId"],
                status="failed",
                error="この指摘のCodex処理は既に実行中です。",
            )
            raise
        run = self.store.update(qualification, run["runId"], jobId=job["jobId"])
        return {"run": run, "prompt": None, "job": job}

    def _review_write_contract(
        self,
        question: Mapping[str, Any],
        review: Mapping[str, Any],
    ) -> tuple[set[str], set[str], set[str], set[str]]:
        selection = review.get("selection")
        selection_fields = (
            selection.get("fields")
            if isinstance(selection, Mapping)
            else []
        )
        fields = {
            str(value).split(".", 1)[0].split("[", 1)[0]
            for value in [
                *(review.get("fields") or []),
                *(selection_fields or []),
            ]
            if value
        }
        blocked_fields = fields & NON_AUTOMATED_CORRECTION_FIELDS
        if blocked_fields:
            raise QualificationRunError(
                "問題文・選択肢は専用の24_questionIssueCorrections契約で"
                "blind reviewするため、Codex App Serverの自動整備対象外です: "
                + ", ".join(sorted(blocked_fields))
            )
        issue_types = {
            str(value) for value in review.get("issueTypes") or [] if value
        }
        patch_dirs = set().union(
            *(FIELD_PATCH_DIR_NAMES.get(field, set()) for field in fields)
        )
        patch_dirs.update(
            set().union(
                *(ISSUE_PATCH_DIR_NAMES.get(issue, set()) for issue in issue_types)
            )
        )
        evaluation_snapshot = review.get("evaluationSnapshot")
        rework_items = (
            evaluation_snapshot.get("reworkItems")
            if isinstance(evaluation_snapshot, Mapping)
            else []
        )
        patch_dirs.update(
            set().union(
                *(
                    REWORK_STAGE_PATCH_DIR_NAMES.get(
                        str(item.get("stage") or ""), set()
                    )
                    for item in rework_items or []
                    if isinstance(item, Mapping)
                )
            )
        )
        law_related = _review_requests_law_audit(review)
        if law_related:
            patch_dirs.update(LAW_PATCH_DIR_NAMES)
        for value in review.get("targetFiles") or []:
            path = self._maintenance_relative_path(value)
            if "24_questionIssueCorrections" in path.parts:
                raise QualificationRunError(
                    "24_questionIssueCorrectionsは専用workflow以外から変更できません。"
                )
        if not patch_dirs:
            raise QualificationRunError(
                "整備責務を限定できません。修正するfieldを1つ以上選択してください。"
            )
        scope = str(review.get("investigationScope") or "current_question")
        law_audit_requested = _review_requests_law_audit(review)
        write_areas: set[str] = set()
        write_files: set[str] = set()
        if law_audit_requested:
            write_areas.add("review")
            write_files.add(self._law_review_sidecar_file(question))
        review_flag_suffixes = (
            {"lawRevision"}
            if review.get("requestKind") == "qualification_law_audit"
            else None
        )
        patch_files = self._review_patch_files(
            question,
            review,
            patch_dirs,
            review_flag_suffixes,
        )
        return patch_dirs, write_areas, patch_files, write_files

    @staticmethod
    def _law_review_sidecar_file(question: Mapping[str, Any]) -> str:
        return QualificationRunCoordinator._law_review_sidecar_path(
            str(question["qualification"]), str(question["listGroupId"])
        )

    @staticmethod
    def _law_review_sidecar_path(
        qualification: str, list_group_id: str
    ) -> str:
        qualification = _safe_segment(qualification)
        list_group_id = _safe_segment(list_group_id)
        return str(
            Path("output")
            / qualification
            / "review"
            / "law_revision_audit"
            / f"{list_group_id}_law_revision_audit.jsonl"
        )

    @staticmethod
    def _question_record_aliases(question: Mapping[str, Any]) -> set[str]:
        aliases: set[str] = set()
        for key in ("source", "projected"):
            value = question.get(key)
            if isinstance(value, Mapping):
                aliases.update(record_identity_aliases(value))
        for value in (
            question.get("id"),
            question.get("originalQuestionId"),
            question.get("sourceQuestionKey"),
        ):
            text = str(value or "").strip()
            if text and not text.startswith(("http://", "https://")):
                aliases.add(text)
        if not aliases:
            raise QualificationRunError(
                "対象問題に一意IDがなく、record identityを安全に特定できません。"
            )
        return aliases

    def _review_patch_files(
        self,
        question: Mapping[str, Any],
        review: Mapping[str, Any],
        patch_dirs: set[str],
        review_flag_suffixes: set[str] | None = None,
    ) -> set[str]:
        if review_flag_suffixes is None:
            review_flag_suffixes = {
                suffix
                for patch_dir in patch_dirs
                for suffix in [REVIEW_FLAG_SUFFIX_BY_PATCH_DIR.get(patch_dir)]
                if suffix
            }
            if _review_requests_law_audit(review):
                review_flag_suffixes.add("lawRevision")
        scope = str(review.get("investigationScope") or "current_question")
        if (
            scope != "current_question"
            and review.get("requestKind") == "qualification_law_audit"
        ):
            allowed: set[str] = set()
            for source_value in review.get("targetSourceFiles") or []:
                allowed.update(
                    self._review_patch_files(
                        {
                            "paths": {
                                "source": source_value,
                                "patches": [],
                            }
                        },
                        {"investigationScope": "current_question"},
                        patch_dirs,
                        set(review_flag_suffixes),
                    )
                )
            if not allowed:
                raise QualificationRunError(
                    "法令監査の対象patch fileを安全に特定できません。"
                )
            return allowed
        allowed: set[Path] = set()
        paths = question.get("paths")
        paths = paths if isinstance(paths, Mapping) else {}
        source_value = paths.get("source")
        if source_value:
            source = self._maintenance_relative_path(source_value)
            if len(source.parts) >= 2:
                group_dir = source.parent.parent
                for patch_dir in patch_dirs:
                    suffix = PATCH_SUFFIX_BY_DIR.get(patch_dir)
                    if suffix:
                        patch_root = self.repo_root / group_dir / patch_dir
                        selected = select_latest_patch_files(
                            sorted(patch_root.glob("*.json")), suffix
                        )
                        source_stems = {source.stem, f"{source.stem}_merged"}
                        preferred = [
                            path
                            for path in selected
                            if source_stem_from_patch_filename(path.name, suffix)
                            in source_stems
                        ]
                        if preferred:
                            allowed.add(
                                sorted(preferred)[-1].relative_to(self.repo_root)
                            )
                        else:
                            merged = (
                                "_merged"
                                if patch_dir
                                in {
                                    "18_law_context_prepared",
                                    "21_explanationText_added",
                                }
                                else ""
                            )
                            allowed.add(
                                group_dir
                                / patch_dir
                                / f"{source.stem}{merged}_{suffix}.json"
                            )
                if "99_model_review_flags" in patch_dirs:
                    for suffix in sorted(review_flag_suffixes):
                        allowed.add(
                            group_dir
                            / "99_model_review_flags"
                            / f"{source.stem}_{suffix}_needs_5_5_high_review.jsonl"
                        )
        if not allowed:
            raise QualificationRunError(
                "対象問題のpatch fileを安全に特定できません。"
            )
        return {path.as_posix() for path in allowed}

    def _review_target_group_ids(
        self,
        question: Mapping[str, Any],
        review: Mapping[str, Any],
    ) -> list[str]:
        qualification = _safe_segment(str(question["qualification"]))
        current_group = _safe_segment(str(question["listGroupId"]))
        if review.get("requestKind") == "qualification_law_audit":
            groups: set[str] = set()
            for value in review.get("targetSourceFiles") or []:
                relative = self._maintenance_relative_path(value)
                parts = relative.parts
                if (
                    len(parts) < 5
                    or parts[:3] != ("output", qualification, "questions_json")
                ):
                    raise QualificationRunError(
                        "法令監査の対象source pathが資格配下ではありません。"
                    )
                groups.add(_safe_segment(parts[3]))
            if not groups:
                raise QualificationRunError(
                    "法令監査の対象年度を安全に特定できません。"
                )
            return sorted(groups)
        scope = str(review.get("investigationScope") or "current_question")
        if scope == "all_qualifications":
            raise QualificationRunError(
                "Codex App Serverの書込調査は1資格ずつ実行してください。"
            )
        if review.get("requestKind") != "qualification_law_audit":
            return [current_group]
        groups = {current_group}
        if scope == "qualification":
            inventory = getattr(self.workflow, "inventory", None)
            inventory_method = getattr(inventory, "inventory", None)
            if callable(inventory_method):
                value = inventory_method()
                qualifications = (
                    value.get("qualifications")
                    if isinstance(value, Mapping)
                    else None
                )
                for item in qualifications or []:
                    if (
                        isinstance(item, Mapping)
                        and str(item.get("id") or "") == qualification
                    ):
                        groups.update(
                            _safe_segment(str(group_id))
                            for group_id in item.get("listGroupIds") or []
                        )
                        break
        return sorted(groups)

    def _flow_phase_plan_prompt(
        self,
        parent: Mapping[str, Any],
        phase: Mapping[str, Any],
    ) -> tuple[dict[str, Any], str]:
        qualification = str(parent["qualification"])
        stage_ids = [str(value) for value in phase.get("stageIds") or []]
        if not stage_ids:
            raise QualificationRunError("トップ整備の工程が空です。")
        mode = str(parent["mode"])
        scope: dict[str, Any] = {}
        scope_group_ids = list(parent.get("scopeListGroupIds") or [])
        if scope_group_ids and stage_ids != ["category_setup"]:
            scope["list_group_ids"] = scope_group_ids
        phase_mode = mode
        if (
            stage_ids == ["question_set"]
            and "category_setup" in set(parent.get("stageIds") or [])
        ):
            phase_mode = "group_refresh" if scope_group_ids else "refresh"
        plan = self._plan(
            qualification,
            stage_ids[0],
            phase_mode,
            None,
            stage_ids=stage_ids,
            **scope,
        )

        def specialize(candidate: dict[str, Any]) -> dict[str, Any]:
            candidate.update(
                {
                    "parentRunId": str(parent["runId"]),
                    "flowPhaseId": str(phase["id"]),
                    "phaseIndex": int(phase["index"]),
                    "workType": f"maintenance_{phase['id']}",
                    "sandbox": "workspace-write",
                    "provider": self.app_server.provider,
                    "parallelStrategy": "read_only_research",
                    "parallelWorkerLimit": (
                        MAINTENANCE_RESEARCH_WORKERS
                        if int(candidate.get("targetCount") or 0) > 1
                        else 1
                    ),
                    "writeWorkerLimit": 1,
                }
            )
            candidate["resolvableFailedDeltaPaths"] = self._resolvable_for_plan(
                qualification,
                list(candidate.get("targetGroupIds") or []),
                candidate,
            )
            return candidate

        plan = specialize(plan)
        if scope.get("list_group_ids") and phase_mode != "group_refresh":
            refresh_plan = specialize(
                self._plan(
                    qualification,
                    stage_ids[0],
                    "group_refresh",
                    None,
                    stage_ids=stage_ids,
                    **scope,
                )
            )
            current_resolvable = set(
                plan.get("resolvableFailedDeltaPaths") or []
            )
            refresh_resolvable = set(
                refresh_plan.get("resolvableFailedDeltaPaths") or []
            )
            if refresh_resolvable - current_resolvable:
                plan = refresh_plan
                phase_mode = "group_refresh"
        if not int(plan.get("targetCount") or 0):
            return plan, ""
        if len(stage_ids) > 1:
            prompt = self.workflow.prompt_many(
                qualification,
                stage_ids,
                phase_mode,
                **scope,
            )["prompt"]
        else:
            prompt = self.workflow.prompt(
                qualification,
                stage_ids[0],
                phase_mode,
                **scope,
            )["prompt"]
        return plan, prompt

    def _update_flow_phase(
        self,
        qualification: str,
        run_id: str,
        phase_id: str,
        **changes: Any,
    ) -> dict[str, Any]:
        parent = self.store.get(qualification, run_id)
        executions = [
            dict(value)
            for value in parent.get("phaseExecutions") or []
            if isinstance(value, Mapping)
        ]
        matched = False
        for execution in executions:
            if str(execution.get("id") or "") == phase_id:
                execution.update(changes)
                matched = True
                break
        if not matched:
            raise QualificationRunError(
                f"トップ整備の工程記録が見つかりません: {phase_id}"
            )
        return self.store.update(
            qualification,
            run_id,
            phaseExecutions=executions,
        )

    def _run_maintenance_flow(
        self,
        qualification: str,
        run_id: str,
        emit: Callable[[str], None],
    ) -> dict[str, Any]:
        parent = self.store.update(
            qualification,
            run_id,
            status="running",
            executionPhase="preparing",
            startedAt=_now(),
            error=None,
        )
        child_run_ids: list[str] = []
        phase_receipts: list[dict[str, Any]] = []
        current_phase_id = ""
        try:
            phases = [
                dict(value)
                for value in parent.get("phaseExecutions") or []
                if isinstance(value, Mapping)
            ]
            for phase_index, phase in enumerate(phases):
                current_phase_id = str(phase["id"])
                parent = self.store.get(qualification, run_id)
                phase_plan, phase_prompt = self._flow_phase_plan_prompt(parent, phase)
                target_count = int(phase_plan.get("targetCount") or 0)
                if not target_count:
                    self._update_flow_phase(
                        qualification,
                        run_id,
                        current_phase_id,
                        status="skipped",
                        targetCount=0,
                        finishedAt=_now(),
                    )
                    emit(f"{phase['label']}: 対象がないため省略します。")
                    continue
                emit(
                    f"{phase['label']}: {target_count}問を新しいsessionで開始します。"
                )
                child = self.store.create(
                    phase_plan,
                    status="queued",
                    prompt=phase_prompt,
                )
                child_run_ids.append(str(child["runId"]))
                parent = self.store.update(
                    qualification,
                    run_id,
                    currentPhaseId=current_phase_id,
                    executionPhase=current_phase_id,
                    childRunIds=list(child_run_ids),
                )
                self._update_flow_phase(
                    qualification,
                    run_id,
                    current_phase_id,
                    status="running",
                    targetCount=target_count,
                    childRunId=child["runId"],
                    startedAt=_now(),
                    error=None,
                )
                saved_prompt = self.store.prompt(qualification, child["runId"])
                self._run_human(
                    qualification,
                    child["runId"],
                    saved_prompt,
                    str(phase_plan["workType"]),
                    emit,
                    sync_artifacts=False,
                )
                child = self.store.refresh(qualification, child["runId"])
                if child.get("status") != "succeeded" or not child.get(
                    "receiptValidated"
                ):
                    raise QualificationRunError(
                        f"{phase['label']}の完了結果を検証できませんでした。"
                    )
                if child.get("allowedPatchDirs") and phase_index < len(phases) - 1:
                    emit(
                        f"{phase['label']}: 次の独立sessionが最新の入力を読めるようにmergeします。"
                    )
                    merge_groups = [
                        self.synchronizer.refresh_merged_views(
                            qualification,
                            str(list_group_id),
                            emit,
                        )
                        for list_group_id in child.get("targetGroupIds") or []
                    ]
                    merge_statuses = {
                        str(group.get("status") or "failed")
                        for group in merge_groups
                    }
                    if merge_statuses - {"succeeded", "current"}:
                        self.store.update(
                            qualification,
                            child["runId"],
                            artifactSync={
                                "status": "failed",
                                "groups": merge_groups,
                                "message": "次工程用のmergeを完了できませんでした。",
                            },
                        )
                        raise QualificationRunError(
                            f"{phase['label']}後の工程間mergeを完了できませんでした。"
                        )
                    child = self.store.update(
                        qualification,
                        child["runId"],
                        artifactSync={
                            "status": "succeeded",
                            "groups": merge_groups,
                            "message": "次工程用のmerged viewを更新しました。",
                        },
                    )
                receipt = child.get("workVersionReceipt")
                if isinstance(receipt, Mapping):
                    phase_receipts.append(dict(receipt))
                cumulative_receipt = {
                    "recordedCount": sum(
                        int(value.get("recordedCount") or 0)
                        for value in phase_receipts
                    ),
                    "phases": list(phase_receipts),
                }
                self._update_flow_phase(
                    qualification,
                    run_id,
                    current_phase_id,
                    status="succeeded",
                    childRunId=child["runId"],
                    threadId=child.get("threadId"),
                    sessionId=child.get("sessionId"),
                    turnId=child.get("turnId"),
                    researchThreadId=child.get("researchThreadId"),
                    researchSessionId=child.get("researchSessionId"),
                    model=child.get("model"),
                    reasoningEffort=child.get("reasoningEffort"),
                    receiptValidated=True,
                    workVersionReceipt=receipt,
                    artifactSync=child.get("artifactSync"),
                    finishedAt=_now(),
                    error=None,
                )
                parent = self.store.update(
                    qualification,
                    run_id,
                    threadId=child.get("threadId"),
                    sessionId=child.get("sessionId"),
                    turnId=child.get("turnId"),
                    model=child.get("model"),
                    serviceTier=child.get("serviceTier"),
                    reasoningEffort=child.get("reasoningEffort"),
                    researchThreadId=child.get("researchThreadId"),
                    researchSessionId=child.get("researchSessionId"),
                    researchTurnId=child.get("researchTurnId"),
                    researchStatus=child.get("researchStatus"),
                    researchSubagentCount=child.get("researchSubagentCount"),
                    researchSubagentThreadIds=child.get(
                        "researchSubagentThreadIds"
                    ),
                    workVersionReceipt=cumulative_receipt,
                )

            current_phase_id = ""
            work_version_receipt = {
                "recordedCount": sum(
                    int(receipt.get("recordedCount") or 0)
                    for receipt in phase_receipts
                ),
                "phases": phase_receipts,
            }
            parent = self.store.update(
                qualification,
                run_id,
                status="validating",
                executionPhase="final_validation",
                currentPhaseId=None,
                receiptValidated=False,
                workVersionReceipt=work_version_receipt,
            )
            emit("すべての独立sessionが完了しました。公開用データを最終検証します。")
            sync_groups = [
                sync_after_patch_update(
                    self.synchronizer,
                    qualification,
                    str(list_group_id),
                    emit,
                )
                for list_group_id in parent.get("targetGroupIds") or []
            ]
            sync_statuses = {
                str(group.get("status") or "failed") for group in sync_groups
            }
            if sync_statuses <= {"succeeded", "current"}:
                sync_status = "succeeded"
                sync_message = "公開用データまで最新patchへ同期しました。"
            else:
                sync_status = "failed" if "failed" in sync_statuses else "blocked"
                sync_message = "公開用データの最終検証を完了できませんでした。"
            artifact_sync = {
                "status": sync_status,
                "groups": sync_groups,
                "message": sync_message,
            }
            if sync_status != "succeeded":
                self.store.update(
                    qualification,
                    run_id,
                    artifactSync=artifact_sync,
                )
                raise QualificationRunError(sync_message)
            result = {
                "status": "succeeded",
                "summary": "トップ整備と最終検証を完了しました。",
                "commands": [],
                "changedFiles": [],
            }
            self.store.write_result(qualification, run_id, result)
            self.store.update(
                qualification,
                run_id,
                status="succeeded",
                executionPhase="done",
                currentPhaseId=None,
                receiptValidated=True,
                workVersionReceipt=work_version_receipt,
                artifactSync=artifact_sync,
                result=result,
                error=None,
            )
            return {
                "qualification": qualification,
                "runId": run_id,
                "childRunIds": child_run_ids,
                "artifactSync": artifact_sync,
                "message": result["summary"],
            }
        except Exception as exc:  # noqa: BLE001
            child = None
            if child_run_ids:
                try:
                    child = self.store.refresh(qualification, child_run_ids[-1])
                except Exception:  # noqa: BLE001
                    child = None
            if current_phase_id:
                self._update_flow_phase(
                    qualification,
                    run_id,
                    current_phase_id,
                    status="failed",
                    threadId=(child or {}).get("threadId"),
                    sessionId=(child or {}).get("sessionId"),
                    turnId=(child or {}).get("turnId"),
                    researchThreadId=(child or {}).get("researchThreadId"),
                    researchSessionId=(child or {}).get("researchSessionId"),
                    model=(child or {}).get("model"),
                    reasoningEffort=(child or {}).get("reasoningEffort"),
                    finishedAt=_now(),
                    error=str(exc),
                )
            result = {
                "status": "failed",
                "summary": str(exc),
                "commands": [],
                "changedFiles": [],
            }
            self.store.write_result(qualification, run_id, result)
            self.store.update(
                qualification,
                run_id,
                status="failed",
                currentPhaseId=current_phase_id or None,
                receiptValidated=False,
                result=result,
                error=str(exc),
            )
            raise

    def _run_delivery(
        self,
        plan: Mapping[str, Any],
        run_id: str,
        emit: Callable[[str], None],
    ) -> dict[str, Any]:
        qualification = str(plan["qualification"])
        completed: list[str] = []
        self.store.update(qualification, run_id, status="running")
        try:
            for group_id in plan["targetGroupIds"]:
                emit(f"{group_id}: 出力を確認します。")
                preview = self.synchronizer.preview(
                    qualification, group_id, force=bool(plan.get("force"))
                )
                result = self.synchronizer.run(
                    qualification,
                    group_id,
                    str(preview["previewToken"]),
                    emit,
                    force=bool(plan.get("force")),
                )
                completed.append(group_id)
                self.store.update(
                    qualification,
                    run_id,
                    completedGroupIds=list(completed),
                    result={"lastGroup": group_id, "message": result.get("message")},
                )
        except Exception as exc:  # noqa: BLE001
            self.store.update(
                qualification,
                run_id,
                status="failed",
                completedGroupIds=list(completed),
                error=str(exc),
            )
            raise
        message = f"{len(completed)}フォルダのMerge・Convert・upload-readyを確認しました。"
        self.store.update(
            qualification,
            run_id,
            status="succeeded",
            completedGroupIds=list(completed),
            result={"message": message},
        )
        return {
            "qualification": qualification,
            "runId": run_id,
            "completedGroupIds": completed,
            "message": message,
        }

    def _run_human(
        self,
        qualification: str,
        run_id: str,
        prompt: str,
        work_type: str,
        emit: Callable[[str], None],
        *,
        sync_artifacts: bool = True,
    ) -> dict[str, Any]:
        if self.app_server is None:
            raise QualificationRunError("Codex App Serverが設定されていません。")
        created_writable_dirs: list[Path] = []
        filesystem_changed_files: tuple[str, ...] = ()
        self.store.update(
            qualification,
            run_id,
            status="running",
            startedAt=_now(),
        )
        try:
            current_run = self.store.get(qualification, run_id)
            target_count = int(current_run.get("targetCount") or 0)
            if target_count > 1:
                emit(
                    f"問題の読み取りと根拠確認は最大{MAINTENANCE_RESEARCH_WORKERS}並列、"
                    "patch・進捗・receiptの保存は1担当で実行します。"
                )
            self._check_source_immutability(emit)
            writable_roots, created_writable_dirs = self._maintenance_writable_roots(
                qualification, run_id
            )
            baseline_path = self.store.write_baseline(
                qualification, run_id, writable_roots
            )
            emit(f"再起動回収用baselineを保存: {baseline_path.relative_to(self.repo_root)}")
            before_files = self._repository_file_fingerprints(
                qualification, run_id
            )

            research_summary = ""
            if target_count > 1:
                self.store.update(
                    qualification,
                    run_id,
                    executionPhase="parallel_research",
                    researchStatus="running",
                )

                def on_research_thread_started(
                    thread_id: str, session_id: str
                ) -> None:
                    self.store.update(
                        qualification,
                        run_id,
                        researchThreadId=thread_id,
                        researchSessionId=session_id,
                    )

                def on_research_turn_started(thread_id: str, turn_id: str) -> None:
                    self.store.update(
                        qualification,
                        run_id,
                        researchThreadId=thread_id,
                        researchTurnId=turn_id,
                    )

                try:
                    emit("read-only並列調査を開始します。")
                    with tempfile.TemporaryDirectory(
                        prefix="question-maintenance-research-"
                    ) as research_directory:
                        research_result = self.app_server.run_turn(
                            _maintenance_research_prompt(prompt),
                            work_type="maintenance_research",
                            sandbox="read-only",
                            emit=emit,
                            on_thread_started=on_research_thread_started,
                            on_turn_started=on_research_turn_started,
                            cwd=Path(research_directory).resolve(),
                        )
                    if research_result.changed_files:
                        raise QualificationRunError(
                            "read-only並列調査でfile変更通知を検出しました。"
                        )
                    research_summary = research_result.final_message
                    research_subagent_count = len(
                        research_result.subagent_thread_ids
                    )
                    self.store.update(
                        qualification,
                        run_id,
                        researchStatus=(
                            "succeeded"
                            if research_subagent_count > 1
                            else "completed_without_parallel"
                        ),
                        researchModel=research_result.model,
                        researchServiceTier=research_result.service_tier,
                        researchReasoningEffort=research_result.reasoning_effort,
                        researchSubagentCount=research_subagent_count,
                        researchSubagentThreadIds=list(
                            research_result.subagent_thread_ids
                        ),
                    )
                    emit(
                        "read-only並列調査を完了し、"
                        f"実績{research_subagent_count}件の調査担当から"
                        "保存担当へ引き継ぎました。"
                    )
                except QualificationRunError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    self.store.update(
                        qualification,
                        run_id,
                        researchStatus="failed",
                        researchError=str(exc),
                    )
                    emit(
                        "read-only並列調査を完了できなかったため、"
                        f"1担当の整備へ切り替えます: {exc}"
                    )

            self.store.update(
                qualification,
                run_id,
                executionPhase="writing",
            )

            def on_thread_started(thread_id: str, session_id: str) -> None:
                self.store.update(
                    qualification,
                    run_id,
                    threadId=thread_id,
                    sessionId=session_id,
                )

            def on_turn_started(thread_id: str, turn_id: str) -> None:
                self.store.update(
                    qualification,
                    run_id,
                    threadId=thread_id,
                    turnId=turn_id,
                )

            result = None
            app_server_changed_files: tuple[str, ...] = ()
            turn_error: Exception | None = None
            receipt_completion_snapshot: dict[str, Any] | None = None

            def completion_probe() -> bool:
                nonlocal receipt_completion_snapshot
                if receipt_completion_snapshot is not None:
                    return True
                snapshot = self._success_receipt_completion_snapshot(
                    qualification,
                    run_id,
                )
                if snapshot is None:
                    return False
                receipt_completion_snapshot = snapshot
                return True

            try:
                with tempfile.TemporaryDirectory(
                    prefix="question-maintenance-session-"
                ) as directory:
                    turn_workspace = Path(directory).resolve()
                    result = self.app_server.run_turn(
                        _maintenance_writer_prompt(prompt, research_summary),
                        work_type=work_type,
                        sandbox="workspace-write",
                        emit=emit,
                        on_thread_started=on_thread_started,
                        on_turn_started=on_turn_started,
                        cwd=turn_workspace,
                        writable_roots=writable_roots,
                        completion_probe=completion_probe,
                    )
                    app_server_changed_files = self._repository_change_notifications(
                        result.changed_files,
                        transient_root=turn_workspace,
                    )
                    if receipt_completion_snapshot is not None:
                        self._assert_receipt_completion_unchanged(
                            qualification,
                            run_id,
                            receipt_completion_snapshot,
                        )
            except Exception as exc:  # noqa: BLE001
                turn_error = exc
            after_files = self._repository_file_fingerprints(
                qualification, run_id
            )
            filesystem_changed_files = tuple(
                str(path)
                for path in sorted(before_files.keys() | after_files.keys())
                if before_files.get(path) != after_files.get(path)
            )
            self._check_source_immutability(emit)
            if turn_error is not None:
                changed = self._failed_run_changed_files(
                    qualification,
                    run_id,
                    filesystem_changed_files,
                )
                suffix = (
                    " 失敗前のfile変更: " + ", ".join(changed)
                    if changed
                    else ""
                )
                raise QualificationRunError(
                    f"Codex App Serverのturnに失敗しました: {turn_error}{suffix}"
                ) from turn_error
            if result is None:
                raise QualificationRunError(
                    "Codex App Serverの実行結果がありません。"
                )
            self.store.update(
                qualification,
                run_id,
                model=result.model,
                serviceTier=result.service_tier,
                reasoningEffort=result.reasoning_effort,
                turnCompletionMode=result.completion_mode,
            )
            refreshed = self.store.refresh(qualification, run_id)
            if refreshed.get("receiptError"):
                raise QualificationRunError(str(refreshed["receiptError"]))
            refreshed_result = refreshed.get("result")
            if (
                not isinstance(refreshed_result, Mapping)
                or refreshed_result.get("status") != "succeeded"
            ):
                raise QualificationRunError(
                    "Codex App Serverは完了しましたが、有効な成功receiptがありません。"
                )
            self._validate_changed_files(
                qualification,
                run_id,
                refreshed,
                app_server_changed_files,
                filesystem_changed_files,
            )
            refreshed = self.store.update(
                qualification,
                run_id,
                status="validating",
                receiptValidated=False,
                error=None,
            )
            inventory = getattr(self.workflow, "inventory", None)
            invalidate = getattr(inventory, "invalidate", None)
            if callable(invalidate):
                for list_group_id in refreshed.get("targetGroupIds") or []:
                    invalidate(qualification, str(list_group_id))
            work_version_receipt = self._record_work_versions(refreshed)
            refreshed = self.store.update(
                qualification,
                run_id,
                receiptValidated=True,
                workVersionReceipt=work_version_receipt,
                artifactSync={
                    "status": "running",
                    "groups": [],
                },
                error=None,
            )
            emit("完了receipt・00_source不変・工程バージョンを確認しました。")
            if refreshed.get("allowedPatchDirs") and sync_artifacts:
                sync_groups = [
                    sync_after_patch_update(
                        self.synchronizer,
                        qualification,
                        str(list_group_id),
                        emit,
                    )
                    for list_group_id in refreshed.get("targetGroupIds") or []
                ]
                sync_statuses = {
                    str(group.get("status") or "failed") for group in sync_groups
                }
                if sync_statuses <= {"succeeded", "current"}:
                    sync_status = "succeeded"
                    sync_message = "公開用データも最新patchへ同期しました。"
                    warning = False
                else:
                    sync_status = (
                        "failed" if "failed" in sync_statuses else "blocked"
                    )
                    sync_message = (
                        "公開用データの自動更新は完了できませんでした。"
                        "問題詳細又は管理機能から再生成できます。"
                    )
                    warning = True
            elif refreshed.get("allowedPatchDirs"):
                sync_groups = []
                sync_status = "deferred"
                sync_message = "公開用データはトップ整備の最終検証で更新します。"
                warning = False
            else:
                sync_groups = []
                sync_status = "not_required"
                sync_message = ""
                warning = False
            artifact_sync = {
                "status": sync_status,
                "groups": sync_groups,
                "message": sync_message,
            }
            refreshed = self.store.update(
                qualification,
                run_id,
                status="succeeded",
                artifactSync=artifact_sync,
                error=None,
            )
            summary = str(
                refreshed.get("result", {}).get("summary")
                or "整備を完了しました。"
            )
            return {
                "qualification": qualification,
                "runId": run_id,
                "threadId": result.thread_id,
                "turnId": result.turn_id,
                "artifactSync": artifact_sync,
                "warning": warning,
                "message": " ".join(value for value in (summary, sync_message) if value),
            }
        except Exception as exc:  # noqa: BLE001
            original_exc = exc
            error_to_raise: Exception = exc
            current = self.store.refresh(qualification, run_id)
            current_result = current.get("result")
            current_result = current_result if isinstance(current_result, Mapping) else {}
            try:
                changed_files = self._failed_run_changed_files(
                    qualification,
                    run_id,
                    filesystem_changed_files,
                )
            except QualificationRunError as change_error:
                receipt_relative = Path(
                    "output",
                    "question_review_console",
                    "workflow_runs",
                    qualification,
                    run_id,
                    "agent_output",
                    "result.json",
                )
                progress_relative = receipt_relative.with_name("progress.jsonl")
                changed_files = [
                    str(path)
                    for value in filesystem_changed_files
                    for path in [self._maintenance_relative_path(value)]
                    if path not in {receipt_relative, progress_relative}
                ]
                error_to_raise = QualificationRunError(
                    f"{original_exc}; {change_error}"
                )
            self.store.write_result(
                qualification,
                run_id,
                {
                    "status": "failed",
                    "summary": str(error_to_raise),
                    "commands": list(current_result.get("commands") or []),
                    "changedFiles": changed_files,
                },
            )
            self.store.refresh(qualification, run_id)
            self.store.update(
                qualification,
                run_id,
                status="failed",
                error=str(error_to_raise),
            )
            if error_to_raise is not original_exc:
                raise error_to_raise from original_exc
            raise
        finally:
            for path in sorted(
                created_writable_dirs,
                key=lambda item: len(item.parts),
                reverse=True,
            ):
                try:
                    path.rmdir()
                except OSError:
                    pass

    def _record_work_versions(self, run: Mapping[str, Any]) -> dict[str, Any]:
        qualification = str(run["qualification"])
        stage_ids = {
            str(value)
            for value in run.get("stageIds") or [run.get("stageId")]
            if value
        }
        if "category_setup" in stage_ids and not self.workflow.category_ready(
            qualification
        ):
            raise QualificationRunError(
                "03c カテゴリ設計のcategory.jsonを検証できません。"
            )
        versions = run.get("policyVersions") or {}
        if not versions:
            return {"recordedCount": 0, "stages": []}
        inventory = getattr(self.workflow, "inventory", None)
        if inventory is None:
            raise QualificationRunError("工程バージョン記録用inventoryがありません。")
        questions: list[Mapping[str, Any]] = []
        for list_group_id in run.get("targetGroupIds") or []:
            group = inventory.group(qualification, str(list_group_id))
            questions.extend(group.get("questions") or [])
        policy_loader = getattr(self.workflow, "versioned_policies", None)
        policies = (
            policy_loader(qualification)
            if callable(policy_loader)
            else QualificationWorkflow(
                self.repo_root, inventory, work_versions=self.work_versions
            ).versioned_policies(qualification)
        )
        fingerprints = run.get("policyFingerprints") or {}
        targets = run.get("policyTargets") or {}
        planned: list[tuple[list[Mapping[str, Any]], dict[str, Any]]] = []
        for stage_id, raw_version in versions.items():
            stage_id = str(stage_id)
            if stage_id not in policies:
                raise QualificationRunError(
                    f"実行時の工程バージョン定義を確認できません: {stage_id}"
                )
            run_fingerprint = str(fingerprints.get(stage_id) or "")
            current_version = normalize_policy_version(
                policies[stage_id]["policyVersion"]
            )
            current_fingerprint = str(
                policies[stage_id].get("policyFingerprint") or ""
            )
            if (
                normalize_policy_version(raw_version) != current_version
                or not run_fingerprint
                or run_fingerprint != current_fingerprint
            ):
                raise QualificationRunError(
                    f"実行中に{stage_id}の作業版又は正本文書が変更されました。"
                    "新しいrunでやり直してください。"
                )
            target_values = {
                str(value) for value in targets.get(stage_id) or [] if value
            }
            if not target_values:
                continue
            selected = [
                question
                for question in questions
                if target_values & self._work_version_aliases(question)
            ]
            if not selected:
                raise QualificationRunError(
                    f"工程バージョンの対象問題を解決できません: {stage_id}"
                )
            if stage_id == "explanation":
                self._validate_explanation_quality(selected)
            if stage_id == "law_audit":
                self._validate_law_audit_quality(selected)
            policy = {
                **policies[stage_id],
                "policyVersion": normalize_policy_version(raw_version),
                "policyFingerprint": run_fingerprint,
            }
            planned.append((selected, policy))
        for list_group_id in run.get("targetGroupIds") or []:
            self.work_versions.load_group(qualification, str(list_group_id))
        receipts = [
            self.work_versions.record_stage(
                selected,
                policy,
                run_id=str(run["runId"]),
                source="validated_run",
            )
            for selected, policy in planned
        ]
        return {
            "recordedCount": sum(
                int(receipt.get("recordedCount") or 0) for receipt in receipts
            ),
            "stages": receipts,
        }

    @staticmethod
    def _validate_explanation_quality(
        questions: list[Mapping[str, Any]],
    ) -> None:
        errors: list[str] = []
        for question in questions:
            projected = question.get("projected")
            explanations = (
                projected.get("explanationText")
                if isinstance(projected, Mapping)
                else None
            )
            label = str(
                question.get("questionLabel")
                or question.get("originalQuestionId")
                or question.get("id")
                or "対象問題"
            )
            if not isinstance(explanations, list) or not explanations:
                errors.append(f"{label}: explanationTextを確認できません。")
                continue
            errors.extend(
                f"{label} {issue}"
                for issue in explanation_style_issues(explanations)
            )
        if errors:
            raise QualificationRunError(
                "03 解説の日本語品質検証に失敗しました。"
                + " ".join(errors[:5])
                + (f" ほか{len(errors) - 5}件。" if len(errors) > 5 else "")
            )

    @staticmethod
    def _validate_law_audit_quality(
        questions: list[Mapping[str, Any]],
    ) -> None:
        errors: list[str] = []
        for question in questions:
            label = str(
                question.get("questionLabel")
                or question.get("originalQuestionId")
                or question.get("id")
                or "対象問題"
            )
            issue_codes = set(question.get("issueCodes") or [])
            blocking = sorted(issue_codes & LAW_AUDIT_ISSUES)
            projected = question.get("projected")
            facts = (
                projected.get("lawRevisionFacts")
                if isinstance(projected, Mapping)
                else None
            )
            if blocking:
                errors.append(f"{label}: {', '.join(blocking)}")
            elif question.get("isLawRelated") is not False and not facts:
                errors.append(f"{label}: lawRevisionFactsを確認できません。")
        if errors:
            raise QualificationRunError(
                "03b 現行法監査の必須メタデータ検証に失敗しました。"
                + " ".join(errors[:5])
                + (f" ほか{len(errors) - 5}件。" if len(errors) > 5 else "")
            )

    @staticmethod
    def _work_version_aliases(question: Mapping[str, Any]) -> set[str]:
        aliases: set[str] = set()
        for key in ("source", "projected"):
            value = question.get(key)
            if isinstance(value, Mapping):
                aliases.update(record_identity_aliases(value))
        aliases.update(
            str(value)
            for value in (
                question.get("id"),
                question.get("reviewKey"),
                question.get("sourceQuestionKey"),
                question.get("originalQuestionId"),
            )
            if value
        )
        return aliases

    def _failed_run_changed_files(
        self,
        qualification: str,
        run_id: str,
        filesystem_changed_files: tuple[str, ...],
    ) -> list[str]:
        paths = {
            self._maintenance_relative_path(value)
            for value in filesystem_changed_files
        }
        paths.discard(
            Path(
                "output",
                "question_review_console",
                "workflow_runs",
                qualification,
                run_id,
                "agent_output",
                "result.json",
            )
        )
        paths.discard(
            Path(
                "output",
                "question_review_console",
                "workflow_runs",
                qualification,
                run_id,
                "agent_output",
                "progress.jsonl",
            )
        )
        run = self.store.get(qualification, run_id)
        allowed_roots = self._maintenance_root_candidates(
            qualification,
            run_id,
            run,
        )
        unsafe = {
            path
            for path in paths
            if not self._maintenance_path_allowed_for_run(
                path, allowed_roots, run
            )
        }
        if unsafe:
            raise QualificationRunError(
                "失敗turnで整備責務外のfile変更を検出しました: "
                + ", ".join(str(path) for path in sorted(unsafe))
            )
        return [str(path) for path in sorted(paths)]

    def _check_source_immutability(self, emit: Callable[[str], None]) -> None:
        checker = self.repo_root / "scripts" / "check" / "check_00_source_immutability.py"
        if not checker.is_file():
            return
        completed = subprocess.run(
            [sys.executable, str(checker)],
            cwd=self.repo_root,
            text=True,
            capture_output=True,
            timeout=300,
            check=False,
        )
        if completed.returncode != 0:
            detail = " ".join((completed.stderr or completed.stdout).splitlines()[-10:])
            raise QualificationRunError(
                f"00_source不変検証に失敗しました{': ' + detail[-1200:] if detail else ''}"
            )
        emit("00_source不変を確認しました。")

    def _maintenance_writable_roots(
        self,
        qualification: str,
        run_id: str,
    ) -> tuple[tuple[Path, ...], list[Path]]:
        run = self.store.get(qualification, run_id)
        roots = self._maintenance_root_candidates(qualification, run_id, run)
        created: list[Path] = []
        resolved_roots: list[Path] = []
        for root in sorted(roots):
            resolved = root.resolve()
            if not resolved.is_relative_to(self.repo_root):
                raise QualificationRunError("整備用writable rootがrepository外です。")
            if not resolved.exists():
                missing: list[Path] = []
                cursor = resolved
                while not cursor.exists() and cursor.is_relative_to(self.repo_root):
                    missing.append(cursor)
                    cursor = cursor.parent
                resolved.mkdir(parents=True, exist_ok=True)
                created.extend(reversed(missing))
            if not resolved.is_dir():
                raise QualificationRunError("整備用writable rootがdirectoryではありません。")
            symlink = next(
                (path for path in resolved.rglob("*") if path.is_symlink()),
                None,
            )
            if symlink is not None:
                raise QualificationRunError(
                    f"整備用writable root内にsymlinkがあります: {symlink}"
                )
            resolved_roots.append(resolved)
        return tuple(resolved_roots), created

    def _maintenance_root_candidates(
        self,
        qualification: str,
        run_id: str,
        run: Mapping[str, Any],
    ) -> set[Path]:
        questions_root = self.repo_root / "output" / qualification / "questions_json"
        roots = {
            self.store.root / qualification / run_id / "agent_output"
        }
        stage_ids = {
            str(value)
            for value in run.get("stageIds") or [run.get("stageId")]
            if value
        }
        patch_dirs = {
            str(value) for value in run.get("allowedPatchDirs") or []
        }
        write_areas = {
            str(value) for value in run.get("allowedWriteAreas") or []
        }
        if not patch_dirs and not write_areas:
            unknown = stage_ids - set(STAGE_PATCH_DIR_NAMES) - {
                "setup",
                "category_setup",
            }
            if unknown:
                raise QualificationRunError(
                    "書込範囲を安全に判定できない工程です: "
                    + ", ".join(sorted(unknown))
                )
            patch_dirs = set().union(
                *(STAGE_PATCH_DIR_NAMES.get(stage, set()) for stage in stage_ids)
            )
            if "setup" in stage_ids:
                write_areas.add("qualification_docs")
            if "category_setup" in stage_ids:
                write_areas.update({"category", "qualification_docs"})
            if "law_context" in stage_ids:
                write_areas.add("law_evidence")
            if "explanation" in stage_ids:
                write_areas.update({"qualification_docs", "review"})
            if "law_audit" in stage_ids:
                write_areas.update({"law_evidence", "review", "reports"})
        if not patch_dirs.issubset(ALLOWED_MAINTENANCE_DIR_NAMES):
            raise QualificationRunError("未定義のpatch層は書き込めません。")
        allowed_areas = {
            "category",
            "law_evidence",
            "reports",
            "review",
            "qualification_docs",
        }
        if not write_areas.issubset(allowed_areas):
            raise QualificationRunError("未定義の整備領域は書き込めません。")
        for area in write_areas:
            roots.add(
                self.repo_root / "prompt" / "qualification_docs" / qualification
                if area == "qualification_docs"
                else self.repo_root / "output" / qualification / area
            )
        for list_group_id in run.get("targetGroupIds") or []:
            try:
                safe_group_id = _safe_segment(str(list_group_id))
            except ValueError as exc:
                raise QualificationRunError(
                    f"整備対象のグループIDが不正です: {list_group_id}"
                ) from exc
            group_root = questions_root / safe_group_id
            roots.update(group_root / name for name in patch_dirs)
        for path in roots:
            if path.is_symlink():
                raise QualificationRunError(
                    f"整備用writable rootにsymlinkは使用できません: {path}"
                )
        return {path.resolve() for path in roots}

    def _validate_changed_files(
        self,
        qualification: str,
        run_id: str,
        run: Mapping[str, Any],
        app_server_changed_files: tuple[str, ...],
        filesystem_changed_files: tuple[str, ...] = (),
    ) -> None:
        result = run.get("result")
        result = result if isinstance(result, Mapping) else {}
        declared = {
            self._maintenance_relative_path(path)
            for path in result.get("changedFiles") or []
        }
        resolved_failed = {
            self._maintenance_relative_path(path)
            for path in result.get("resolvedFailedDeltaPaths") or []
        }
        resolvable = {
            self._maintenance_relative_path(path)
            for path in run.get("resolvableFailedDeltaPaths") or []
        }
        unexpected_resolutions = resolved_failed - resolvable
        if unexpected_resolutions:
            raise QualificationRunError(
                "このrunの開始時に未確定でなかったpathは解決済みにできません: "
                + ", ".join(str(path) for path in sorted(unexpected_resolutions))
            )
        notified = {
            self._maintenance_relative_path(path)
            for path in app_server_changed_files
        }
        actual = {
            self._maintenance_relative_path(path)
            for path in filesystem_changed_files
        }
        receipt_path = Path(
            "output",
            "question_review_console",
            "workflow_runs",
            qualification,
            run_id,
            "agent_output",
            "result.json",
        )
        progress_path = receipt_path.with_name("progress.jsonl")
        notified.discard(receipt_path)
        actual.discard(receipt_path)
        notified.discard(progress_path)
        actual.discard(progress_path)
        declared.discard(progress_path)
        agent_output_root = receipt_path.parent
        extra_agent_output = {
            path
            for path in declared | notified | actual
            if path == agent_output_root or path.is_relative_to(agent_output_root)
        }
        if extra_agent_output:
            raise QualificationRunError(
                "agent_outputにはresult.json以外（画面用progress.jsonlを除く）を保存できません: "
                + ", ".join(str(path) for path in sorted(extra_agent_output))
            )
        symlinks = {
            path for path in actual if (self.repo_root / path).is_symlink()
        }
        if symlinks:
            raise QualificationRunError(
                "整備差分にsymlinkは使用できません: "
                + ", ".join(str(path) for path in sorted(symlinks))
            )
        self._validate_record_scope(
            qualification,
            run_id,
            run,
            actual,
        )
        allowed_roots = self._maintenance_root_candidates(
            qualification,
            run_id,
            run,
        )
        for path in resolved_failed:
            if self._is_failed_delta_manifest_sentinel(path, qualification):
                continue
            if not self._maintenance_path_allowed_for_run(
                path, allowed_roots, run
            ):
                raise QualificationRunError(
                    f"整備責務外の未確定差分は解決済みにできません: {path}"
                )
        for path in declared | notified | actual:
            if not self._maintenance_path_allowed_for_run(
                path, allowed_roots, run
            ):
                raise QualificationRunError(
                    f"整備責務外のfile変更を検出しました: {path}"
                )
        undeclared = (notified | actual) - declared
        if undeclared:
            raise QualificationRunError(
                "完了receiptに未記載のfile変更があります: "
                + ", ".join(str(path) for path in sorted(undeclared))
            )
        missing = declared - actual
        if missing:
            raise QualificationRunError(
                "完了receiptに記載されたが実際の最終差分にないfileがあります: "
                + ", ".join(str(path) for path in sorted(missing))
            )

    def _validate_record_scope(
        self,
        qualification: str,
        run_id: str,
        run: Mapping[str, Any],
        actual: set[Path],
    ) -> None:
        target_aliases = {
            str(value) for value in run.get("targetRecordAliases") or []
        }
        target_alias_groups = [
            {str(value) for value in group if value}
            for group in run.get("targetRecordAliasGroups") or []
            if isinstance(group, list) and group
        ]
        if not target_alias_groups and target_aliases:
            target_alias_groups = [set(target_aliases)]
        target_aliases.update(
            value for group in target_alias_groups for value in group
        )
        raw_record_scopes = run.get("targetRecordScopes")
        record_scopes = (
            {
                self._maintenance_relative_path(path): (
                    _normalized_alias_groups(groups)
                )
                for path, groups in raw_record_scopes.items()
            }
            if isinstance(raw_record_scopes, Mapping)
            else {}
        )
        allowed_record_files = {
            self._maintenance_relative_path(value)
            for value in [
                *(run.get("allowedPatchFiles") or []),
                *(run.get("allowedWriteFiles") or []),
            ]
        }
        changed_record_files = {
            path
            for path in actual & allowed_record_files
            if path.suffix.lower() in {".json", ".jsonl"}
        }
        stage_ids = {
            str(value)
            for value in run.get("stageIds") or [run.get("stageId")]
            if value
        }
        if "category_setup" in stage_ids:
            changed_record_files.discard(
                Path("output", qualification, "category", "category.json")
            )
        if not changed_record_files:
            return
        if target_aliases and not record_scopes:
            raise QualificationRunError(
                "file別の対象record scopeを確認できません。"
            )
        baseline_path = (
            self.store.root / qualification / run_id / "baseline.json"
        )
        try:
            raw = baseline_path.read_bytes()
            if not hmac.compare_digest(
                hashlib.sha256(raw).hexdigest(),
                str(run.get("baselineHash") or ""),
            ):
                raise QualificationRunError("record baselineのhashが一致しません。")
            payload = json.loads(raw.decode("utf-8"))
            snapshots = payload.get("recordSnapshots")
            source_snapshots = payload.get("sourceRecordSnapshots")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QualificationRunError(
                "record baselineを確認できません。"
            ) from exc
        if not isinstance(snapshots, Mapping):
            raise QualificationRunError("record baselineがありません。")
        if not isinstance(source_snapshots, Mapping):
            raise QualificationRunError("source record baselineがありません。")
        source_entries = [
            entry
            for entries in source_snapshots.values()
            if isinstance(entries, list)
            for entry in entries
            if isinstance(entry, Mapping)
        ]

        def aliases(entry: Mapping[str, Any]) -> set[str]:
            return {str(value) for value in entry.get("aliases") or []}

        def protected(entry: Mapping[str, Any]) -> dict[str, Any]:
            value = entry.get("protectedFields")
            if not isinstance(value, Mapping):
                raise QualificationRunError("record baselineの保護field形式が不正です。")
            return dict(value)

        def identity(entry: Mapping[str, Any]) -> dict[str, Any]:
            value = entry.get("identityFields")
            if not isinstance(value, Mapping):
                raise QualificationRunError("record baselineのID field形式が不正です。")
            return dict(value)

        def matching(
            entries: list[Any], entry_aliases: set[str]
        ) -> list[Mapping[str, Any]]:
            if not entry_aliases:
                return []
            return [
                entry
                for entry in entries
                if isinstance(entry, Mapping)
                and aliases(entry) & entry_aliases
            ]

        def unambiguous_protected(
            entries: list[Mapping[str, Any]], relative: Path
        ) -> dict[str, Any] | None:
            if not entries:
                return None
            values = [protected(entry) for entry in entries]
            canonical = {
                json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                for value in values
            }
            if len(canonical) != 1:
                raise QualificationRunError(
                    f"保護fieldの参照recordが一意ではありません: {relative}"
                )
            return values[0]

        def unambiguous_identity(
            entries: list[Mapping[str, Any]], relative: Path
        ) -> dict[str, Any] | None:
            if not entries:
                return None
            values = [identity(entry) for entry in entries]
            canonical = {
                json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                for value in values
            }
            if len(canonical) != 1:
                raise QualificationRunError(
                    f"ID fieldの参照recordが一意ではありません: {relative}"
                )
            return values[0]

        for relative in sorted(changed_record_files):
            file_target_alias_groups = [
                set(group) for group in record_scopes.get(relative, [])
            ]
            if target_aliases and not file_target_alias_groups:
                raise QualificationRunError(
                    f"file別の対象record scopeがありません: {relative}"
                )
            file_target_aliases = {
                alias
                for group in file_target_alias_groups
                for alias in group
            }
            before = snapshots.get(relative.as_posix())
            if not isinstance(before, list):
                raise QualificationRunError(
                    f"変更前recordを確認できません: {relative}"
                )
            after = _record_snapshot(self.repo_root / relative)

            for after_entry in after:
                if not isinstance(after_entry, Mapping):
                    raise QualificationRunError("record baselineの形式が不正です。")
                entry_aliases = aliases(after_entry)
                before_matches = matching(before, entry_aliases)
                source_matches = matching(source_entries, entry_aliases)
                before_fields = unambiguous_protected(
                    before_matches, relative
                )
                source_fields = unambiguous_protected(
                    source_matches, relative
                )
                after_fields = protected(after_entry)
                before_identity = unambiguous_identity(
                    before_matches, relative
                )
                after_identity = identity(after_entry)
                if before_identity is not None:
                    if after_identity != before_identity:
                        raise QualificationRunError(
                            f"既存ID fieldの変更を検出しました: {relative}"
                        )
                else:
                    for field, value in after_identity.items():
                        if field == "firestoreQuestionIds":
                            valid = bool(
                                isinstance(value, list)
                                and value
                                and all(
                                    isinstance(item, str) and bool(item.strip())
                                    for item in value
                                )
                                and len({item.strip() for item in value})
                                == len(value)
                            )
                        else:
                            valid = bool(
                                isinstance(value, str) and value.strip()
                            )
                        if not valid:
                            raise QualificationRunError(
                                f"新規recordのID fieldが空又は不正です: "
                                f"{relative} / {field}"
                            )
                    source_aliases = {
                        alias
                        for entry in source_matches
                        for alias in aliases(entry)
                    }
                    matching_target_groups = [
                        group
                        for group in file_target_alias_groups
                        if entry_aliases & group
                    ]
                    if len(matching_target_groups) > 1:
                        raise QualificationRunError(
                            f"新規recordが複数の対象問題IDに一致します: {relative}"
                        )
                    if (
                        len(matching_target_groups) != 1
                        or not source_matches
                        or not entry_aliases.issubset(source_aliases)
                    ):
                        raise QualificationRunError(
                            f"sourceと異なるID fieldを検出しました: {relative}"
                        )
                if before_fields is None and source_fields is None:
                    if after_fields:
                        raise QualificationRunError(
                            f"問題文・選択肢の参照元を確認できません: {relative}"
                        )
                    continue
                for field in CODEX_PROTECTED_CONTENT_FIELDS:
                    if before_fields is not None and field in before_fields:
                        if (
                            field not in after_fields
                            or after_fields[field] != before_fields[field]
                        ):
                            raise QualificationRunError(
                                f"Codex自動整備対象外fieldの変更を検出しました: "
                                f"{relative} / {field}"
                            )
                    elif field in after_fields:
                        if (
                            source_fields is None
                            or field not in source_fields
                            or after_fields[field] != source_fields[field]
                        ):
                            raise QualificationRunError(
                                f"Codex自動整備対象外fieldの追加を検出しました: "
                                f"{relative} / {field}"
                            )

            def target_count(entries: list[Any], group: set[str]) -> int:
                return sum(
                    1
                    for entry in entries
                    if isinstance(entry, Mapping)
                    and aliases(entry) & group
                )

            for group in file_target_alias_groups:
                before_count = target_count(before, group)
                after_count = target_count(after, group)
                if before_count > 1 or after_count > 1:
                    raise QualificationRunError(
                        f"対象問題の一意IDがfile内で重複しています: {relative}"
                    )
                if before_count == 1 and after_count == 0:
                    raise QualificationRunError(
                        f"対象問題のrecord削除を検出しました: {relative}"
                    )

            if not file_target_aliases:
                continue

            def non_target(entries: list[Any]) -> list[tuple[tuple[str, ...], str]]:
                values: list[tuple[tuple[str, ...], str]] = []
                for entry in entries:
                    if not isinstance(entry, Mapping):
                        raise QualificationRunError("record baselineの形式が不正です。")
                    entry_aliases = tuple(
                        sorted(str(value) for value in entry.get("aliases") or [])
                    )
                    if set(entry_aliases) & file_target_aliases:
                        continue
                    values.append((entry_aliases, str(entry.get("hash") or "")))
                return sorted(values)

            if non_target(before) != non_target(after):
                raise QualificationRunError(
                    f"対象問題以外のrecord変更を検出しました: {relative}"
                )

    def _repository_file_fingerprints(
        self,
        qualification: str,
        run_id: str,
    ) -> dict[Path, str]:
        fingerprints: dict[Path, str] = {}
        for root_value, dir_names, file_names in os.walk(self.repo_root):
            root = Path(root_value)
            relative_root = root.relative_to(self.repo_root)
            kept_dirs = []
            for name in dir_names:
                child = root / name
                relative = relative_root / name
                if name in SNAPSHOT_IGNORED_DIR_NAMES or name == "00_source":
                    continue
                if relative == Path("output", "question_review_console"):
                    # UI自身が管理するreview・job・receiptは、整備threadの
                    # repository差分と分離する。実体patchは別途、厳密照合する。
                    continue
                if child.is_symlink():
                    fingerprints[relative] = self._path_fingerprint(child)
                    continue
                kept_dirs.append(name)
            dir_names[:] = kept_dirs
            for name in file_names:
                path = root / name
                relative = relative_root / name
                fingerprints[relative] = self._path_fingerprint(path)

        # UI管理treeは通常除外するが、agent専用receipt inboxだけは全fileを監視する。
        agent_output = self.store.result_path(qualification, run_id).parent
        if agent_output.is_dir():
            for path in agent_output.rglob("*"):
                relative = path.relative_to(self.repo_root)
                fingerprints[relative] = self._path_fingerprint(path)

        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo_root,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if head.returncode != 0:
            raise QualificationRunError("Git HEADを確認できません。")
        fingerprints[Path(".git", "HEAD")] = "commit:" + head.stdout.strip()

        changed_paths: set[Path] = set()
        for command in (
            ["git", "diff", "--name-only", "-z", "HEAD", "--"],
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        ):
            completed = subprocess.run(
                command,
                cwd=self.repo_root,
                capture_output=True,
                timeout=60,
                check=False,
            )
            if completed.returncode != 0:
                raise QualificationRunError("repository差分を確認できません。")
            changed_paths.update(
                Path(os.fsdecode(value))
                for value in completed.stdout.split(b"\0")
                if value
            )
        for relative in changed_paths:
            if relative.parts[:2] == ("output", "question_review_console"):
                continue
            path = (self.repo_root / relative).resolve()
            if not path.is_relative_to(self.repo_root):
                raise QualificationRunError("repository差分のpathが不正です。")
            fingerprints[relative] = self._path_content_fingerprint(path)
        fingerprints[Path(".git", "config")] = self._path_content_fingerprint(
            self.repo_root / ".git" / "config"
        )
        staged = subprocess.run(
            ["git", "diff", "--cached", "--binary", "HEAD", "--"],
            cwd=self.repo_root,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if staged.returncode != 0:
            raise QualificationRunError("staging差分を確認できません。")
        fingerprints[Path(".git", "index")] = (
            "staged:" + hashlib.sha256(staged.stdout).hexdigest()
        )
        hooks_root = self.repo_root / ".git" / "hooks"
        if hooks_root.is_dir():
            for hook in hooks_root.iterdir():
                if hook.is_file() or hook.is_symlink():
                    relative = hook.relative_to(self.repo_root)
                    fingerprints[relative] = self._path_content_fingerprint(hook)
        return fingerprints

    def _success_receipt_completion_snapshot(
        self,
        qualification: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        run = self.store.refresh(qualification, run_id)
        result = run.get("result")
        if (
            run.get("status") != "validating"
            or run.get("receiptValidated") is True
            or run.get("receiptError")
            or not isinstance(result, Mapping)
            or result.get("status") != "succeeded"
        ):
            return None
        watched_paths = {
            self._maintenance_relative_path(value)
            for value in result.get("changedFiles") or []
        }
        receipt_path = self.store.result_path(qualification, run_id)
        watched_paths.add(receipt_path.relative_to(self.repo_root))
        watched_paths.add(
            receipt_path.with_name("progress.jsonl").relative_to(self.repo_root)
        )
        return {
            "fileFingerprints": {
                path.as_posix(): self._path_content_fingerprint(
                    self.repo_root / path
                )
                for path in sorted(watched_paths)
            },
        }

    def _assert_receipt_completion_unchanged(
        self,
        qualification: str,
        run_id: str,
        snapshot: Mapping[str, Any],
    ) -> None:
        run = self.store.refresh(qualification, run_id)
        result = run.get("result")
        if (
            run.get("receiptError")
            or not isinstance(result, Mapping)
            or result.get("status") != "succeeded"
        ):
            raise QualificationRunError(
                "成功receiptの検出後にresult.jsonが変更されました。"
            )
        raw_fingerprints = snapshot.get("fileFingerprints")
        if not isinstance(raw_fingerprints, Mapping):
            raise QualificationRunError("成功receipt時点のfile hashがありません。")
        changed_after_receipt: list[str] = []
        for value, expected in raw_fingerprints.items():
            relative = self._maintenance_relative_path(value)
            actual = self._path_content_fingerprint(self.repo_root / relative)
            if not hmac.compare_digest(actual, str(expected)):
                changed_after_receipt.append(relative.as_posix())
        if changed_after_receipt:
            raise QualificationRunError(
                "成功receiptの保存後にfile変更を検出しました: "
                + ", ".join(sorted(changed_after_receipt))
            )

    @staticmethod
    def _path_fingerprint(path: Path) -> str:
        try:
            stat = path.lstat()
        except FileNotFoundError:
            return "missing"
        suffix = f":{os.readlink(path)}" if path.is_symlink() else ""
        # Google Drive File Providerは、placeholderの実体化だけでもctimeを更新する。
        # 内容を表さないctimeは除外し、mode・size・mtimeとsymlink先を監視する。
        return f"stat:{stat.st_mode}:{stat.st_size}:{stat.st_mtime_ns}{suffix}"

    @staticmethod
    def _path_content_fingerprint(path: Path) -> str:
        if path.is_symlink():
            return f"symlink:{os.readlink(path)}"
        if not path.exists():
            return "missing"
        if not path.is_file():
            return QualificationRunCoordinator._path_fingerprint(path)
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"

    def _maintenance_relative_path(self, value: Any) -> Path:
        path = Path(str(value))
        absolute = Path(
            os.path.abspath(path if path.is_absolute() else self.repo_root / path)
        )
        if not absolute.is_relative_to(self.repo_root):
            raise QualificationRunError(f"repository外のfile変更は許可しません: {value}")
        return absolute.relative_to(self.repo_root)

    def _repository_change_notifications(
        self,
        values: tuple[str, ...],
        *,
        transient_root: Path,
    ) -> tuple[str, ...]:
        """Separate disposable turn files from persistent repository changes."""
        transient = transient_root.resolve()
        repository_paths: set[Path] = set()
        for value in values:
            raw = Path(str(value))
            candidate = (
                raw if raw.is_absolute() else transient / raw
            ).resolve(strict=False)
            if candidate == transient or candidate.is_relative_to(transient):
                continue
            if not candidate.is_relative_to(self.repo_root):
                raise QualificationRunError(
                    f"repository外のfile変更は許可しません: {value}"
                )
            repository_paths.add(candidate.relative_to(self.repo_root))
        return tuple(path.as_posix() for path in sorted(repository_paths))

    def _maintenance_path_allowed_for_roots(
        self,
        path: Path,
        roots: set[Path],
    ) -> bool:
        candidate = (self.repo_root / path).absolute()
        return any(
            candidate == root or candidate.is_relative_to(root)
            for root in roots
        )

    def _maintenance_path_allowed_for_run(
        self,
        path: Path,
        roots: set[Path],
        run: Mapping[str, Any],
    ) -> bool:
        if not self._maintenance_path_allowed_for_roots(path, roots):
            return False
        allowed_patch_files = {
            self._maintenance_relative_path(value)
            for value in run.get("allowedPatchFiles") or []
        }
        if set(path.parts) & ALLOWED_MAINTENANCE_DIR_NAMES:
            return not allowed_patch_files or path in allowed_patch_files

        qualification = str(run.get("qualification") or "")
        write_roots = {
            (
                Path("prompt", "qualification_docs", qualification)
                if str(area) == "qualification_docs"
                else Path("output", qualification, str(area))
            )
            for area in run.get("allowedWriteAreas") or []
        }
        if any(path == root or path.is_relative_to(root) for root in write_roots):
            allowed_write_files = {
                self._maintenance_relative_path(value)
                for value in run.get("allowedWriteFiles") or []
            }
            return not allowed_write_files or path in allowed_write_files
        return True

    @staticmethod
    def _is_failed_delta_manifest_sentinel(
        path: Path, qualification: str
    ) -> bool:
        parts = path.parts
        return (
            len(parts) == 6
            and parts[:4]
            == (
                "output",
                "question_review_console",
                "workflow_runs",
                qualification,
            )
            and bool(re.fullmatch(r"[A-Za-z0-9._-]+", parts[4]))
            and parts[5] == "manifest.json"
        )

    def _plan(
        self,
        qualification: str,
        stage_id: str,
        mode: str,
        resumed_from: str | None,
        *,
        stage_ids: list[str] | None = None,
        list_group_id: str | None = None,
        list_group_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        selected_stage_ids = list(dict.fromkeys(stage_ids or [stage_id]))
        scope: dict[str, Any] = {}
        if list_group_ids is not None:
            scope["list_group_ids"] = list_group_ids
        elif list_group_id is not None:
            scope["list_group_id"] = list_group_id
        if len(selected_stage_ids) > 1:
            plan = dict(
                self.workflow.plan_many(
                    qualification,
                    selected_stage_ids,
                    mode,
                    **scope,
                )
            )
        elif scope:
            plan = dict(
                self.workflow.plan(
                    qualification,
                    selected_stage_ids[0],
                    mode,
                    **scope,
                )
            )
        else:
            plan = dict(
                self.workflow.plan(qualification, selected_stage_ids[0], mode)
            )
        plan.setdefault("stageIds", selected_stage_ids)
        if plan["kind"] == "human":
            plan.setdefault("workType", "maintenance")
            self._apply_plan_write_contract(plan)
        if not resumed_from or plan["kind"] != "machine":
            if plan["kind"] == "human":
                plan["resolvableFailedDeltaPaths"] = self._resolvable_for_plan(
                    qualification,
                    list(plan.get("targetGroupIds") or []),
                    plan,
                )
            return plan
        previous = self.store.get(qualification, resumed_from)
        previous_scope = (
            list(previous.get("scopeListGroupIds") or [])
            if "scopeListGroupIds" in previous
            else [str(previous["scopeListGroupId"])]
            if previous.get("scopeListGroupId")
            else None
        )
        if (
            previous.get("stageId") != stage_id
            or previous.get("mode") != mode
            or previous_scope is not None
            and previous_scope != list(plan.get("scopeListGroupIds") or [])
        ):
            raise QualificationRunError("再開元と工程又は対象範囲が一致しません。")
        completed = set(previous.get("completedGroupIds") or [])
        remaining = [
            group_id
            for group_id in plan.get("targetGroupIds") or []
            if group_id not in completed
        ]
        plan["targetGroupIds"] = remaining
        plan["targetCount"] = len(remaining)
        plan["sourceFiles"] = [
            str(Path("output") / qualification / "questions_json" / group_id)
            for group_id in remaining
        ]
        return plan

    def _apply_plan_write_contract(self, plan: dict[str, Any]) -> None:
        raw_stage_plans = plan.get("stagePlans")
        stage_plans = (
            [value for value in raw_stage_plans if isinstance(value, Mapping)]
            if isinstance(raw_stage_plans, list) and raw_stage_plans
            else [plan]
        )
        patch_dirs: set[str] = set()
        write_areas: set[str] = set()
        patch_files: set[str] = set()
        write_files: set[str] = set()
        record_scopes: dict[str, list[list[str]]] = {}
        for stage_plan in stage_plans:
            current_stage_ids = {
                str(value)
                for value in stage_plan.get("stageIds")
                or [stage_plan.get("stageId")]
                if value and str(value) != "multi"
            }
            current_patch_dirs = set().union(
                *(
                    STAGE_PATCH_DIR_NAMES.get(stage, set())
                    for stage in current_stage_ids
                )
            )
            patch_dirs.update(current_patch_dirs)

            if "setup" in current_stage_ids:
                write_areas.add("qualification_docs")
            if "category_setup" in current_stage_ids:
                write_areas.update({"category", "qualification_docs"})
            if "law_audit" in current_stage_ids:
                write_areas.add("review")
                for group_id in stage_plan.get("targetGroupIds") or []:
                    write_files.add(
                        self._law_review_sidecar_path(
                            str(plan["qualification"]), str(group_id)
                        )
                    )

            raw_source_scopes = stage_plan.get("targetSourceRecordScopes")
            source_scopes = (
                {
                    self._maintenance_relative_path(path).as_posix(): (
                        _normalized_alias_groups(groups)
                    )
                    for path, groups in raw_source_scopes.items()
                }
                if isinstance(raw_source_scopes, Mapping)
                else {}
            )

            for value in stage_plan.get("outputFiles") or []:
                relative = self._maintenance_relative_path(value)
                if set(relative.parts) & current_patch_dirs:
                    patch_files.add(relative.as_posix())
                else:
                    write_files.add(relative.as_posix())

            if not current_patch_dirs:
                continue
            review_flag_suffixes = set().union(
                *(
                    STAGE_REVIEW_FLAG_SUFFIXES.get(stage, set())
                    for stage in current_stage_ids
                )
            )
            for source_value in stage_plan.get("sourceFiles") or []:
                if Path(str(source_value)).suffix.lower() != ".json":
                    continue
                scoped_files = self._review_patch_files(
                    {"paths": {"source": source_value, "patches": []}},
                    {"investigationScope": "current_question"},
                    current_patch_dirs,
                    review_flag_suffixes,
                )
                patch_files.update(scoped_files)
                groups = source_scopes.get(str(source_value), [])
                for path in scoped_files:
                    if groups:
                        _add_record_scope(record_scopes, path, groups)
                source_parts = Path(str(source_value)).parts
                if "law_audit" in current_stage_ids and len(source_parts) >= 4:
                    sidecar = self._law_review_sidecar_path(
                        str(plan["qualification"]), source_parts[3]
                    )
                    if sidecar in write_files and groups:
                        _add_record_scope(record_scopes, sidecar, groups)

        plan["allowedPatchDirs"] = sorted(patch_dirs)
        plan["allowedWriteAreas"] = sorted(write_areas)
        plan["allowedPatchFiles"] = sorted(patch_files)
        plan["allowedWriteFiles"] = sorted(write_files)
        scoped_record_files = {
            path
            for path in [*patch_files, *write_files]
            if Path(path).suffix.lower() in {".json", ".jsonl"}
            and (
                set(Path(path).parts) & patch_dirs
                or "/review/law_revision_audit/" in f"/{path}"
            )
        }
        if plan.get("targetRecordAliasGroups") and (
            scoped_record_files - set(record_scopes)
        ):
            raise QualificationRunError(
                "工程の対象file別record scopeを安全に作成できません。"
            )
        plan["targetRecordScopes"] = record_scopes

    def _unresolved_for_groups(
        self, qualification: str, group_ids: list[str]
    ) -> list[str]:
        if not group_ids:
            return list(unresolved_failed_delta_paths(self.repo_root, qualification))
        return sorted(
            {
                path
                for group_id in group_ids
                for path in unresolved_failed_delta_paths(
                    self.repo_root, qualification, str(group_id)
                )
            }
        )

    def _resolvable_for_plan(
        self,
        qualification: str,
        group_ids: list[str],
        plan: Mapping[str, Any],
    ) -> list[str]:
        """Limit failed-delta resolution to the current run's write contract."""

        if not group_ids:
            return list(
                resolvable_failed_delta_paths(
                    self.repo_root,
                    qualification,
                    plan,
                )
            )
        return sorted(
            {
                path
                for group_id in group_ids
                for path in resolvable_failed_delta_paths(
                    self.repo_root,
                    qualification,
                    plan,
                    str(group_id),
                )
            }
        )

    def _token(self, payload: Mapping[str, Any]) -> str:
        value = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hmac.new(self.secret, value.encode("utf-8"), hashlib.sha256).hexdigest()
