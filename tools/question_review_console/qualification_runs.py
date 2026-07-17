from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from scripts.merge.merge_utils import (
    select_latest_patch_files,
    source_stem_from_patch_filename,
)
from scripts.common.question_identity import (
    SourceIdentityBinding,
    source_question_key,
    source_record_ref,
)
from scripts.common.law_audit_sidecar_contract import (
    law_audit_sidecar_metadata_errors,
)
from tools.question_review_console.projection import (
    extract_records,
    record_identity_aliases,
    source_identity_aliases,
    workflow_identity_aliases,
)
from tools.question_review_console.jobs import (
    REPOSITORY_OPERATION_KEY,
    JobConflictError,
    JobManager,
    normalize_log_event,
)
from tools.question_review_console.failed_delta import (
    resolvable_failed_delta_paths,
    unresolved_failed_delta_paths,
)
from tools.question_review_console.explanation_quality import (
    explanation_style_issues,
    law_evidence_utilization_issues,
)
from tools.question_review_console.law_audit_quality import (
    law_revision_current_verdict_issues,
)
from tools.question_review_console.law_audit_contract import is_law_audit_review
from tools.question_review_console.codex_app_server import (
    MAINTENANCE_RESEARCH_WORKERS,
    CodexAppServerError,
    SubscriptionGateError,
)
from tools.question_review_console.qualification_workflow import QualificationWorkflow
from tools.question_review_console.qualification_progress import (
    derive_progress_completion,
)
from tools.question_review_console.question_patch_proposal import (
    QuestionPatchProposalError,
    QuestionPatchProposalStore,
)
from tools.question_review_console.question_work_queue import (
    WORK_ITEM_STATES,
    QuestionWorkQueueError,
    block_group_after_stage,
    build_question_executions,
    input_fingerprint,
    queue_summary,
    recover_interrupted_executions,
    refresh_question_status,
    resume_plan,
    specialize_question_plan,
    subset_question_plan,
    work_item_key,
)
from tools.question_review_console.run_target_identity import (
    RunTargetIdentityError,
    RunTargetIdentityResolver,
    resolve_policy_target_ids,
    target_identity_aliases,
)
from tools.question_review_console.work_versions import QuestionWorkVersionStore
from tools.question_review_console.workflow_catalog import normalize_policy_version
from tools.question_review_console.workflow_runner import (
    ArtifactSynchronizer,
    sync_after_patch_update,
)
from tools.question_review_console.write_transaction import (
    WriteTransactionError,
    capture_write_snapshot,
    restore_write_snapshot,
)


LIVE_RUN_STATUSES = {
    "queued",
    "running",
    "validating",
}
ARTIFACT_SYNC_COMPLETE_STATUSES = {"succeeded", "current", "not_required"}
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
    "sourceRecordRef",
    "source_record_ref",
    "uploadOriginalQuestionId",
    "firestoreQuestionIds",
)


def _artifact_sync_result(
    groups: list[dict[str, Any]],
    *,
    success_message: str,
    incomplete_message: str,
) -> dict[str, Any]:
    """Summarize publication sync without changing validated work state."""

    statuses = {str(group.get("status") or "failed") for group in groups}
    if statuses <= ARTIFACT_SYNC_COMPLETE_STATUSES:
        status = "succeeded"
        message = success_message
    else:
        status = "failed" if "failed" in statuses else "blocked"
        message = incomplete_message
    return {"status": status, "groups": groups, "message": message}


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
        identity_record = dict(record)
        if "00_source" in path.parts:
            source_root_index = len(path.parts) - 1 - tuple(
                reversed(path.parts)
            ).index("00_source")
            relative_source = Path(*path.parts[source_root_index + 1 :])
            if relative_source.parts:
                identity_record["sourceRecordRef"] = source_record_ref(
                    relative_source.as_posix(), index
                )
            if (
                source_root_index >= 3
                and path.parts[source_root_index - 2] == "questions_json"
            ):
                derived_source_key = source_question_key(
                    path.parts[source_root_index - 3],
                    path.parts[source_root_index - 1],
                    record,
                )
                if derived_source_key:
                    identity_record["sourceQuestionKey"] = derived_source_key
        canonical = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        snapshot.append(
            {
                "index": index,
                "aliases": sorted(record_identity_aliases(identity_record)),
                "sourceAliases": sorted(
                    source_identity_aliases(identity_record)
                ),
                "workflowAliases": sorted(
                    workflow_identity_aliases(identity_record)
                ),
                "hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                "protectedFields": {
                    field: copy.deepcopy(record[field])
                    for field in CODEX_PROTECTED_CONTENT_FIELDS
                    if field in record
                },
                "identityFields": {
                    field: copy.deepcopy(identity_record[field])
                    for field in CODEX_PROTECTED_IDENTITY_FIELDS
                    if field in identity_record
                },
                "contractFields": {
                    field: copy.deepcopy(record[field])
                    for field in (
                        "schemaVersion",
                        "qualification",
                        "listGroupId",
                    )
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


def _preparation_retryable(exc: Exception) -> bool:
    if not isinstance(exc, CodexAppServerError) or isinstance(
        exc, SubscriptionGateError
    ):
        return False
    message = str(exc)
    return any(
        marker in message
        for marker in (
            "時間切れ",
            "停止しています",
            "送信に失敗",
            "stdio",
            "起動できません",
        )
    )


def _isolated_failure_state(child: Mapping[str, Any]) -> bool:
    rollback = child.get("rollback")
    result = child.get("result")
    changed_files = (
        list(result.get("changedFiles") or [])
        if isinstance(result, Mapping)
        else []
    )
    return bool(
        isinstance(rollback, Mapping)
        and rollback.get("status") == "succeeded"
        and rollback.get("deltaUnknown") is not True
        and not rollback.get("remainingChangedFiles")
        and child.get("deltaUnknown") is not True
        and not changed_files
    )


def _child_retry_safe(child: Mapping[str, Any]) -> bool:
    if child.get("status") == "succeeded" and child.get("receiptValidated") is True:
        return True
    if not child.get("startedAt") and child.get("deltaUnknown") is not True:
        result = child.get("result")
        return not isinstance(result, Mapping) or not result.get("changedFiles")
    return _isolated_failure_state(child)


def _child_output_fingerprint(child: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "result": child.get("result"),
                "workVersionReceipt": child.get("workVersionReceipt"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _question_scope_prompt(
    prompt: str,
    target: Mapping[str, Any],
    *,
    repo_root: Path,
    read_only: bool,
) -> str:
    question_id = str(target.get("id") or target.get("uiQuestionId") or "")
    identity = SourceIdentityBinding.from_mapping(target)
    action = (
        "この問題の判断・修正案と根拠を返す。fileは一切変更しない。"
        if read_only
        else (
            "この問題だけをpatchへ反映し、検証、progress、result receiptまで完了する。"
            "他の問題は変更も検証も行わない。"
        )
    )
    return "\n".join(
        [
            "# 一問work item（この指定を最優先する）",
            "",
            f"- repository: `{repo_root}`",
            f"- 問題ID: `{question_id}`",
            f"- 表示: `{target.get('displayLabel') or question_id}`",
            f"- sourceQuestionKey: `{identity.source_question_key}`",
            f"- reviewQuestionId: `{identity.review_question_id}`",
            f"- sourceRecordRef: `{identity.source_record_ref}`",
            "",
            action,
            "下記の対象件数とfile一覧は親範囲の説明であり、この一問へscopeを広げない。",
            "",
            "# 親範囲の整備prompt",
            "",
            prompt.rstrip(),
            "",
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


def _derive_resume_merge_dependencies(
    previous: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """再開対象より前で未完了だった工程間mergeを引き継ぐ。"""

    stage_order = [str(value) for value in plan.get("stageIds") or [] if value]
    stage_positions = {
        stage_id: index for index, stage_id in enumerate(stage_order)
    }
    resumed_stage_groups: set[tuple[str, str]] = set()
    raw_stage_plans = plan.get("stagePlans")
    stage_plans = (
        [
            dict(value)
            for value in raw_stage_plans
            if isinstance(value, Mapping)
        ]
        if isinstance(raw_stage_plans, list) and raw_stage_plans
        else [dict(plan)]
    )
    resumed_stage_ids: set[str] = set()
    for stage_plan in stage_plans:
        stage_id = str(stage_plan.get("stageId") or "")
        if not stage_id or stage_id == "multi":
            continue
        resumed_stage_ids.add(stage_id)
        groups = {
            str(target.get("listGroupId") or "")
            for target in stage_plan.get("progressTargets") or []
            if isinstance(target, Mapping) and target.get("listGroupId")
        }
        if not groups and not stage_plan.get("progressTargets"):
            groups.update(
                str(value)
                for value in stage_plan.get("targetGroupIds") or []
                if value
            )
        resumed_stage_groups.update(
            (stage_id, list_group_id) for list_group_id in groups
        )

    scoped_groups = {
        str(value) for value in plan.get("targetGroupIds") or [] if value
    }
    dependencies: dict[tuple[str, str], dict[str, Any]] = {}

    def add_dependency(
        list_group_id: str,
        after_stage_id: str,
        *,
        status: str,
    ) -> None:
        if not list_group_id or not after_stage_id:
            return
        if status not in {
            "running",
            "interrupted",
            "failed",
            "blocked",
            "pending",
        }:
            return
        if scoped_groups and list_group_id not in scoped_groups:
            return
        after_index = stage_positions.get(after_stage_id)
        if after_index is None or not any(
            stage_positions.get(stage_id, -1) > after_index
            for stage_id in resumed_stage_ids
        ):
            return
        if (after_stage_id, list_group_id) in resumed_stage_groups:
            # このgroupの前工程自体を再実行する場合は、通常の工程間mergeが
            # 後続を開放するため、開始前に重複してmergeしない。
            return
        dependencies[(after_stage_id, list_group_id)] = {
            "listGroupId": list_group_id,
            "afterStageId": after_stage_id,
            "status": "pending",
            "message": None,
        }

    for phase in previous.get("phaseExecutions") or []:
        if not isinstance(phase, Mapping):
            continue
        after_stage_id = str(phase.get("id") or "")
        artifact_sync = phase.get("artifactSync")
        if not isinstance(artifact_sync, Mapping):
            continue
        for group in artifact_sync.get("groups") or []:
            if not isinstance(group, Mapping):
                continue
            add_dependency(
                str(group.get("listGroupId") or ""),
                after_stage_id,
                status=str(group.get("status") or ""),
            )

    # 再merge自体が失敗したrunから再度再開しても、依存を失わない。
    for dependency in previous.get("resumeMergeDependencies") or []:
        if not isinstance(dependency, Mapping):
            continue
        add_dependency(
            str(dependency.get("listGroupId") or ""),
            str(dependency.get("afterStageId") or ""),
            status=(
                "pending"
                if dependency.get("remergeRequired") is True
                else str(dependency.get("status") or "pending")
            ),
        )

    return sorted(
        dependencies.values(),
        key=lambda value: (
            stage_positions.get(str(value["afterStageId"]), len(stage_order)),
            str(value["listGroupId"]),
        ),
    )


class QualificationRunStore:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()
        self.root = self.repo_root / "output" / "question_review_console" / "workflow_runs"
        self._lock = threading.RLock()
        self._technical_log_sequences: dict[Path, int] = {}
        self._technical_log_last_signatures: dict[Path, str] = {}
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
        technical_log_path = run_dir / "technical_log.jsonl"
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
                    "uiQuestionId": str(
                        raw_target.get("uiQuestionId") or question_id
                    )[:300],
                    "questionKey": str(raw_target.get("questionKey") or question_id)[:300],
                    "sourceQuestionKey": str(
                        raw_target.get("sourceQuestionKey") or ""
                    )[:500],
                    "sourceRecordRef": str(
                        raw_target.get("sourceRecordRef") or ""
                    )[:1000],
                    "reviewQuestionId": str(
                        raw_target.get("reviewQuestionId") or ""
                    )[:500],
                    "listGroupId": str(raw_target.get("listGroupId") or "")[:100],
                    "sectionLabel": str(
                        raw_target.get("sectionLabel") or ""
                    )[:200],
                    "questionLabel": str(raw_target.get("questionLabel") or "")[:200],
                    "displayLabel": str(
                        raw_target.get("displayLabel")
                        or raw_target.get("questionLabel")
                        or ""
                    )[:300],
                    "displayOrder": int(
                        raw_target.get("displayOrder") or len(progress_targets) + 1
                    ),
                    "bodyPreview": str(raw_target.get("bodyPreview") or "")[:240],
                    "stateHash": str(raw_target.get("stateHash") or "")[:128],
                    "aliases": aliases,
                }
            )
        target_record_bindings = [
            {
                "id": str(value.get("uiQuestionId") or ""),
                "uiQuestionId": str(value.get("uiQuestionId") or ""),
                "reviewQuestionId": str(
                    value.get("reviewQuestionId") or ""
                ),
                "sourceQuestionKey": str(
                    value.get("sourceQuestionKey") or ""
                ),
                "sourceRecordRef": str(
                    value.get("sourceRecordRef") or ""
                ),
                "aliases": sorted(
                    {
                        str(alias)
                        for alias in value.get("aliases") or []
                        if alias
                    }
                ),
            }
            for value in plan.get("targetRecordBindings") or []
            if isinstance(value, Mapping)
            and str(value.get("uiQuestionId") or "")
        ]
        try:
            target_resolver = RunTargetIdentityResolver.from_sources(
                ("progressTargets", progress_targets),
                ("targetRecordBindings", target_record_bindings),
            )
            policy_targets: dict[str, list[str]] = {}
            for stage_id, raw_values in (plan.get("policyTargets") or {}).items():
                if not isinstance(raw_values, list):
                    raise RunTargetIdentityError(
                        f"{stage_id}のpolicyTargetsがlistではありません。"
                    )
                normalized: list[str] = []
                for raw_value in raw_values:
                    target = target_resolver.resolve(raw_value)
                    normalized.append(target_resolver.official_id(target))
                policy_targets[str(stage_id)] = list(
                    dict.fromkeys(normalized)
                )
        except RunTargetIdentityError as exc:
            raise QualificationRunError(
                f"問題別の実行対象ID契約が不正です: {exc}"
            ) from exc
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
        question_executions = copy.deepcopy(
            list(plan.get("questionExecutions") or [])
        )
        question_execution_summary = (
            queue_summary(question_executions)
            if question_executions
            else {}
        )
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
            "questionExecutions": question_executions,
            "questionExecutionSummary": question_execution_summary,
            "queueStatus": plan.get("queueStatus"),
            "retrySafe": bool(plan.get("retrySafe", True)),
            "retryUnsafeReason": plan.get("retryUnsafeReason"),
            "unsafeChildRunId": plan.get("unsafeChildRunId"),
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
            "policyTargets": policy_targets,
            "sourceFiles": sorted(
                {str(value) for value in plan.get("sourceFiles") or []}
            ),
            "targetRecordAliases": sorted(target_record_aliases),
            "targetRecordAliasGroups": target_record_alias_groups,
            "targetRecordBindings": [
                {
                    "uiQuestionId": str(value.get("uiQuestionId") or ""),
                    "reviewQuestionId": str(
                        value.get("reviewQuestionId") or ""
                    ),
                    "sourceQuestionKey": str(
                        value.get("sourceQuestionKey") or ""
                    ),
                    "sourceRecordRef": str(
                        value.get("sourceRecordRef") or ""
                    ),
                    "aliases": sorted(
                        {
                            str(alias)
                            for alias in value.get("aliases") or []
                            if alias
                        }
                    ),
                }
                for value in target_record_bindings
            ],
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
            "confirmedGroupIds": sorted(
                {str(value) for value in plan.get("confirmedGroupIds") or [] if value}
            ),
            "jobId": None,
            "resumedFrom": resumed_from,
            "resumeWorkItemKeys": sorted(
                {str(value) for value in plan.get("resumeWorkItemKeys") or [] if value}
            ),
            "resumeMergeDependencies": [
                {
                    "listGroupId": str(value.get("listGroupId") or ""),
                    "afterStageId": str(value.get("afterStageId") or ""),
                    "status": str(value.get("status") or "pending"),
                    "message": (
                        str(value.get("message"))
                        if value.get("message") is not None
                        else None
                    ),
                    "remergeRequired": bool(value.get("remergeRequired")),
                    "startedAt": value.get("startedAt"),
                    "finishedAt": value.get("finishedAt"),
                    "updatedAt": value.get("updatedAt"),
                }
                for value in plan.get("resumeMergeDependencies") or []
                if isinstance(value, Mapping)
                and value.get("listGroupId")
                and value.get("afterStageId")
            ],
            "parentSourceChecked": bool(plan.get("parentSourceChecked")),
            "createdAt": now,
            "startedAt": None,
            "updatedAt": now,
            "heartbeatAt": now,
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
            "technicalLogPath": str(
                technical_log_path.relative_to(self.repo_root)
            ),
            "resultReceiptHash": None,
            "receiptError": None,
            "receiptValidated": False,
            "workVersionReceipt": copy.deepcopy(plan.get("workVersionReceipt")),
            "baselinePath": None,
            "baselineHash": None,
            "deltaUnknown": False,
            "rollback": None,
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

    def append_technical_log(
        self,
        qualification: str,
        run_id: str,
        value: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """run配下の技術ログへ、許可fieldだけを一行追記する。"""

        manifest_path = self._manifest_path(qualification, run_id)
        with self._lock:
            manifest = self._load_manifest(manifest_path)
            relative = str(manifest.get("technicalLogPath") or "")
            path = (
                (self.repo_root / relative).resolve()
                if relative
                else manifest_path.with_name("technical_log.jsonl")
            )
            run_dir = manifest_path.parent.resolve()
            if path.parent != run_dir or path.name != "technical_log.jsonl":
                raise QualificationRunError("技術ログの保存先がrun配下ではありません。")
            sequence = self._technical_log_sequences.get(path)
            if sequence is None:
                sequence = 0
                last_existing: Mapping[str, Any] | None = None
                if path.is_file():
                    for raw_line in path.read_bytes().splitlines():
                        try:
                            existing = json.loads(raw_line.decode("utf-8"))
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            continue
                        if isinstance(existing, Mapping):
                            last_existing = existing
                            raw_sequence = existing.get("sequence")
                            if isinstance(raw_sequence, int):
                                sequence = max(sequence, raw_sequence)
                if last_existing is not None:
                    self._technical_log_last_signatures[path] = (
                        self._technical_log_signature(last_existing)
                    )
            event = normalize_log_event(value, sequence=sequence + 1)
            if not event["message"]:
                return None
            # 表示API互換用のaliasは永続正本へ重複保存しない。
            event.pop("at", None)
            signature = self._technical_log_signature(event)
            if self._technical_log_last_signatures.get(path) == signature:
                return None
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            self._technical_log_sequences[path] = sequence + 1
            self._technical_log_last_signatures[path] = signature
            return copy.deepcopy(event)

    @staticmethod
    def _technical_log_signature(value: Mapping[str, Any]) -> str:
        return json.dumps(
            {
                key: item
                for key, item in value.items()
                if key not in {"sequence", "observedAt", "at"}
            },
            ensure_ascii=False,
            sort_keys=True,
        )

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

    def update_question_stage(
        self,
        qualification: str,
        run_id: str,
        question_id: str,
        stage_id: str,
        *,
        block_dependents: bool = False,
        **changes: Any,
    ) -> dict[str, Any]:
        path = self._manifest_path(qualification, run_id)
        with self._lock:
            manifest = self._load_manifest(path)
            executions = manifest.get("questionExecutions")
            if not isinstance(executions, list):
                raise QualificationRunError("一問queueの実行記録がありません。")
            question = next(
                (
                    value
                    for value in executions
                    if isinstance(value, dict)
                    and str(value.get("questionId") or "") == question_id
                ),
                None,
            )
            if question is None:
                raise QualificationRunError(
                    f"一問queueの対象問題がありません: {question_id}"
                )
            stages = question.get("stages")
            if not isinstance(stages, list):
                raise QualificationRunError("一問queueの工程記録がありません。")
            stage_index = next(
                (
                    index
                    for index, value in enumerate(stages)
                    if isinstance(value, dict)
                    and str(value.get("stageId") or "") == stage_id
                ),
                None,
            )
            if stage_index is None:
                raise QualificationRunError(
                    f"一問queueの対象工程がありません: {question_id} / {stage_id}"
                )
            next_status = str(changes.get("status") or stages[stage_index].get("status") or "")
            if next_status not in WORK_ITEM_STATES:
                raise QualificationRunError(
                    f"一問queueの工程状態が不正です: {next_status}"
                )
            stages[stage_index].update(copy.deepcopy(changes))
            if block_dependents:
                reason = str(changes.get("error") or "前工程で停止しました。")
                for dependent in stages[stage_index + 1 :]:
                    if str(dependent.get("status") or "") in {
                        "validated",
                        "not_applicable",
                    }:
                        continue
                    dependent.update(
                        status="blocked",
                        error=f"前工程 {stage_id} の停止により保留: {reason}",
                        finishedAt=changes.get("finishedAt") or _now(),
                    )
            refresh_question_status(question)
            summary = queue_summary(executions)
            manifest.update(
                questionExecutionSummary=summary,
                blockedQuestionCount=summary["blockedQuestionCount"],
                blockedWorkItemCount=summary["blockedWorkItemCount"],
                validatedQuestionCount=summary["validatedQuestionCount"],
                validatedWorkItemCount=summary["validatedWorkItemCount"],
                updatedAt=_now(),
            )
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

    def mark_validated_artifact_sync_incomplete(
        self,
        qualification: str,
        run_id: str,
        *,
        artifact_status: str,
        message: str,
        result_if_missing: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        path = self._manifest_path(qualification, run_id)
        with self._lock:
            manifest = self._finalize_validated_artifact_sync_incomplete(
                path,
                self._load_manifest(path),
                artifact_status=artifact_status,
                message=message,
                result_if_missing=result_if_missing,
            )
            return self._public(manifest)

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

    def technical_log(
        self,
        qualification: str,
        run_id: str,
        *,
        limit: int = 200,
    ) -> dict[str, Any]:
        manifest_path = self._manifest_path(qualification, run_id)
        with self._lock:
            manifest = self._load_manifest(manifest_path)
            relative = str(manifest.get("technicalLogPath") or "")
            path = (
                (self.repo_root / relative).resolve()
                if relative
                else manifest_path.with_name("technical_log.jsonl")
            )
            if (
                path.parent != manifest_path.parent.resolve()
                or path.name != "technical_log.jsonl"
            ):
                raise QualificationRunError("技術ログの保存先がrun配下ではありません。")
            raw_lines = path.read_bytes().splitlines() if path.is_file() else []
        entries: list[dict[str, Any]] = []
        for raw_line in raw_lines[-max(1, min(int(limit), 500)) :]:
            try:
                value = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(value, Mapping):
                raw_sequence = value.get("sequence")
                sequence = (
                    raw_sequence
                    if isinstance(raw_sequence, int)
                    and not isinstance(raw_sequence, bool)
                    else len(entries) + 1
                )
                event = normalize_log_event(
                    value,
                    sequence=sequence,
                    observed_at=str(value.get("observedAt") or "") or None,
                )
                event.pop("at", None)
                if event["message"]:
                    entries.append(event)
        return {
            "runId": run_id,
            "technicalLogPath": str(path.relative_to(self.repo_root)),
            "entries": entries,
        }

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
            children: list[tuple[dict[str, Any], bytes]] = []
            for child_run_id in manifest.get("childRunIds") or []:
                child_path = self._manifest_path(qualification, str(child_run_id))
                child = self._load_manifest(child_path)
                if str(child.get("parentRunId") or "") != str(run_id):
                    raise QualificationRunError(
                        "工程別runとトップ整備runの対応が一致しません。"
                    )
                progress_path = child_path.parent / "agent_output" / "progress.jsonl"
                children.append(
                    (
                        child,
                        progress_path.read_bytes()
                        if progress_path.is_file()
                        else b"",
                    )
                )
        payload = self._empty_progress(manifest)
        child_payloads = [
            (child, self._parsed_progress(child, raw))
            for child, raw in children
        ]
        events: list[dict[str, Any]] = []
        outputs_by_question: dict[str, dict[str, dict[str, Any]]] = {}
        display_by_question: dict[str, dict[str, Any]] = {}
        processed_work_items: set[tuple[str, str]] = set()
        finalized_work_items: set[tuple[str, str]] = set()
        validated_work_items: set[tuple[str, str]] = set()
        finalized_questions: set[str] = set()
        validated_child_questions: set[str] = set()
        failed_child_questions: set[str] = set()
        invalid_count = 0
        combined_sequence = 0
        for child_index, (child, child_payload) in enumerate(
            child_payloads, start=1
        ):
            invalid_count += int(child_payload.get("invalidEventCount") or 0)
            for event in child_payload.get("events") or []:
                combined_sequence += 1
                events.append({**event, "sequence": combined_sequence})
            child_verified = bool(
                child.get("status") == "succeeded"
                and child.get("receiptValidated") is True
            )
            for question in child_payload.get("questions") or []:
                question_id = str(question.get("questionId") or "")
                if not question_id:
                    continue
                display_by_question[question_id] = question
                if child.get("status") == "failed":
                    failed_child_questions.add(question_id)
                if question.get("processed"):
                    finalized_questions.add(question_id)
                if child_verified and question.get("completed"):
                    validated_child_questions.add(question_id)
                for output in question.get("outputs") or []:
                    stage_id = str(output.get("stageId") or "")
                    if not stage_id:
                        continue
                    work_item = (question_id, stage_id)
                    processed_work_items.add(work_item)
                    if question.get("processed"):
                        finalized_work_items.add(work_item)
                    if child_verified and question.get("completed"):
                        validated_work_items.add(work_item)
                    outputs_by_question.setdefault(question_id, {})[
                        stage_id
                    ] = {
                        **output,
                        "sequence": child_index * MAX_PROGRESS_EVENTS
                        + int(output.get("sequence") or 0),
                    }

        targets = [
            target
            for target in manifest.get("progressTargets") or []
            if isinstance(target, Mapping) and target.get("id")
        ]
        planned_by_question: dict[str, set[str]] = {}
        for stage_id, raw_aliases in (manifest.get("policyTargets") or {}).items():
            question_ids, contract_invalid = resolve_policy_target_ids(
                targets, raw_aliases
            )
            invalid_count += contract_invalid
            for question_id in question_ids:
                planned_by_question.setdefault(question_id, set()).add(
                    str(stage_id)
                )
        completion = derive_progress_completion(
            {str(target["id"]) for target in targets},
            planned_by_question,
            processed_work_items,
            finalized_work_items,
            finalized_questions,
            validated_work_items,
            validated_child_questions,
        )
        touched_questions = completion.touched_questions
        processed_questions = completion.processed_questions
        validated_questions = completion.validated_questions
        stage_order = {
            str(stage.get("id") or ""): index
            for index, stage in enumerate(manifest.get("progressStages") or [])
            if isinstance(stage, Mapping)
        }
        questions: list[dict[str, Any]] = []
        for target in targets:
            question_id = str(target["id"])
            raw_outputs = outputs_by_question.get(question_id, {})
            base = display_by_question.get(question_id)
            if base is None and not raw_outputs:
                continue
            outputs = sorted(
                raw_outputs.values(),
                key=lambda output: (
                    stage_order.get(str(output.get("stageId") or ""), 10_000),
                    int(output.get("sequence") or 0),
                ),
            )
            display = outputs[-1] if outputs else dict(base or {})
            approval_state = (
                "validated"
                if question_id in validated_questions
                else "failed_unapproved"
                if question_id in failed_child_questions
                else "processed_unverified"
                if question_id in processed_questions
                else "working"
            )
            questions.append(
                {
                    **display,
                    "questionId": question_id,
                    "processed": question_id in processed_questions,
                    "completed": question_id in validated_questions,
                    "approvalState": approval_state,
                    "outputs": outputs,
                }
            )
        payload["groups"] = [
            {
                "listGroupId": group_id,
                "targetQuestionCount": len(group_targets),
                "processedQuestionCount": len(
                    group_targets & processed_questions
                ),
                "completedQuestionCount": len(
                    group_targets & validated_questions
                ),
                "percent": round(
                    (
                        len(group_targets & validated_questions)
                        / len(group_targets)
                    )
                    * 100
                )
                if group_targets
                else 0,
            }
            for group_id in dict.fromkeys(
                str(target.get("listGroupId") or "") for target in targets
            )
            for group_targets in [
                {
                    str(target["id"])
                    for target in targets
                    if str(target.get("listGroupId") or "") == group_id
                }
            ]
        ]
        target_work = int(manifest.get("workItemCount") or 0)
        payload["touchedQuestionCount"] = len(touched_questions)
        payload["processedQuestionCount"] = len(processed_questions)
        payload["validatedQuestionCount"] = len(validated_questions)
        payload["completedQuestionCount"] = len(validated_questions)
        payload["processedWorkItemCount"] = len(processed_work_items)
        payload["validatedWorkItemCount"] = len(validated_work_items)
        payload["completedWorkItemCount"] = len(validated_work_items)
        if target_work:
            payload["percent"] = min(
                100,
                round((len(validated_work_items) / target_work) * 100),
            )
            payload["processedPercent"] = min(
                100,
                round((len(processed_work_items) / target_work) * 100),
            )
        payload["status"] = manifest.get("status")
        payload["verified"] = bool(
            manifest.get("status") == "succeeded"
            and manifest.get("receiptValidated") is True
        )
        payload["events"] = events[-40:]
        payload["questions"] = questions
        payload["current"] = copy.deepcopy(events[-1]) if events else None
        if payload["current"] is not None:
            current_question = next(
                (
                    question
                    for question in questions
                    if question.get("questionId")
                    == payload["current"].get("questionId")
                ),
                None,
            )
            if current_question is not None:
                payload["current"]["approvalState"] = current_question.get(
                    "approvalState"
                )
        payload["invalidEventCount"] = invalid_count
        execution_by_question = {
            str(value.get("questionId") or ""): value
            for value in manifest.get("questionExecutions") or []
            if isinstance(value, Mapping) and value.get("questionId")
        }
        question_by_id = {
            str(value.get("questionId") or ""): value
            for value in questions
            if value.get("questionId")
        }
        for question_id, execution in execution_by_question.items():
            blocked_stage = next(
                (
                    stage
                    for stage in execution.get("stages") or []
                    if isinstance(stage, Mapping)
                    and str(stage.get("status") or "") == "blocked"
                ),
                None,
            )
            display = question_by_id.get(question_id)
            if display is None:
                display = {
                    "questionId": question_id,
                    "listGroupId": str(execution.get("listGroupId") or ""),
                    "displayLabel": str(execution.get("displayLabel") or question_id),
                    "targetIndex": int(execution.get("displayOrder") or 0),
                    "processed": False,
                    "completed": False,
                    "outputs": [],
                }
                questions.append(display)
                question_by_id[question_id] = display
            display["queueStatus"] = str(execution.get("status") or "queued")
            if blocked_stage is not None:
                display["approvalState"] = "blocked"
                display["blockedStageId"] = str(
                    blocked_stage.get("stageId") or ""
                )
                display["blockedReason"] = str(blocked_stage.get("error") or "")
        questions.sort(
            key=lambda value: (
                int(
                    (execution_by_question.get(str(value.get("questionId") or "")) or {}).get(
                        "displayOrder"
                    )
                    or 0
                ),
                str(value.get("questionId") or ""),
            )
        )
        execution_summary = manifest.get("questionExecutionSummary")
        if not isinstance(execution_summary, Mapping):
            execution_summary = queue_summary(
                manifest.get("questionExecutions") or []
            )
        payload["questionExecutionSummary"] = copy.deepcopy(
            dict(execution_summary)
        )
        payload["blockedQuestionCount"] = int(
            execution_summary.get("blockedQuestionCount") or 0
        )
        payload["blockedWorkItemCount"] = int(
            execution_summary.get("blockedWorkItemCount") or 0
        )
        completed_work_items = int(
            execution_summary.get("completedWorkItemCount")
            or execution_summary.get("validatedWorkItemCount")
            or 0
        )
        processed_work_items_count = completed_work_items + int(
            execution_summary.get("blockedWorkItemCount") or 0
        )
        payload["completedWorkItemCount"] = max(
            int(payload.get("completedWorkItemCount") or 0),
            completed_work_items,
        )
        payload["processedWorkItemCount"] = max(
            int(payload.get("processedWorkItemCount") or 0),
            processed_work_items_count,
        )
        payload["completedQuestionCount"] = max(
            int(payload.get("completedQuestionCount") or 0),
            int(execution_summary.get("validatedQuestionCount") or 0),
        )
        payload["validatedQuestionCount"] = payload["completedQuestionCount"]
        payload["processedQuestionCount"] = max(
            int(payload.get("processedQuestionCount") or 0),
            payload["completedQuestionCount"]
            + int(execution_summary.get("blockedQuestionCount") or 0),
        )
        target_work_items = int(payload.get("targetWorkItemCount") or 0)
        if target_work_items:
            payload["percent"] = min(
                100,
                round(
                    (payload["completedWorkItemCount"] / target_work_items) * 100
                ),
            )
            payload["processedPercent"] = min(
                100,
                round(
                    (payload["processedWorkItemCount"] / target_work_items) * 100
                ),
            )
        payload["queueStatus"] = manifest.get("queueStatus")
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
            "touchedQuestionCount": 0,
            "processedQuestionCount": 0,
            "validatedQuestionCount": 0,
            "targetWorkItemCount": int(manifest.get("workItemCount") or 0),
            "completedWorkItemCount": 0,
            "processedWorkItemCount": 0,
            "validatedWorkItemCount": 0,
            "blockedQuestionCount": int(
                manifest.get("blockedQuestionCount") or 0
            ),
            "blockedWorkItemCount": int(
                manifest.get("blockedWorkItemCount") or 0
            ),
            "questionExecutionSummary": copy.deepcopy(
                manifest.get("questionExecutionSummary") or {}
            ),
            "queueStatus": manifest.get("queueStatus"),
            "percent": 0,
            "processedPercent": 0,
            "heartbeatAt": manifest.get("heartbeatAt") or manifest.get("updatedAt"),
            "executionPhase": manifest.get("executionPhase"),
            "currentPhaseId": manifest.get("currentPhaseId"),
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
        target_by_id: dict[str, dict[str, Any]] = {}
        duplicate_target_ids: set[str] = set()
        for index, target in enumerate(targets, start=1):
            target["targetIndex"] = index
            target_id = str(target.get("id") or "")
            if target_id in target_by_id:
                duplicate_target_ids.add(target_id)
            else:
                target_by_id[target_id] = target
        for target_id in duplicate_target_ids:
            target_by_id.pop(target_id, None)
        stages = {
            str(stage.get("id")): dict(stage)
            for stage in manifest.get("progressStages") or []
            if isinstance(stage, Mapping) and stage.get("id")
        }
        invalid_count = 0
        raw_policy_targets = manifest.get("policyTargets")
        planned_work_items: set[tuple[str, str]] | None = None
        if isinstance(raw_policy_targets, Mapping) and raw_policy_targets:
            planned_work_items = set()
            for stage_id, raw_aliases in raw_policy_targets.items():
                stage_id = str(stage_id)
                if stage_id not in stages or not isinstance(raw_aliases, list):
                    invalid_count += 1
                    continue
                question_ids, contract_invalid = resolve_policy_target_ids(
                    targets, raw_aliases
                )
                invalid_count += contract_invalid
                for question_id in question_ids:
                    planned_work_items.add((question_id, stage_id))
        planned_stage_order_by_question: dict[str, list[str]] = {}
        ordered_stage_ids = list(stages)
        for target in targets:
            question_id = str(target["id"])
            planned_stage_order_by_question[question_id] = [
                stage_id
                for stage_id in ordered_stage_ids
                if planned_work_items is None
                or (question_id, stage_id) in planned_work_items
            ]
        question_states = {
            str(target["id"]): {
                "started": False,
                "nextStageIndex": 0,
                "completed": False,
            }
            for target in targets
        }
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
            # progressTargets[].id is the receipt protocol.  Display aliases
            # must never decide ownership when two records share source IDs.
            target = target_by_id.get(str(value.get("questionId") or ""))
            stage_id = str(value.get("stageId") or "")
            stage = stages.get(stage_id) if stage_id else None
            if (
                event_type not in PROGRESS_EVENT_TYPES
                or target is None
                or (event_type == "stage_completed" and stage is None)
                or (stage_id and stage is None)
                or (
                    event_type in {"question_started", "question_completed"}
                    and bool(stage_id)
                )
            ):
                invalid_count += 1
                continue
            question_id = str(target["id"])
            if (
                event_type == "stage_completed"
                and planned_work_items is not None
                and (question_id, stage_id) not in planned_work_items
            ):
                invalid_count += 1
                continue
            state = question_states[question_id]
            planned_stage_order = planned_stage_order_by_question[question_id]
            if event_type == "question_started":
                valid_order = not state["started"] and not state["completed"]
                if valid_order:
                    state["started"] = True
            elif event_type == "stage_completed":
                next_stage_index = int(state["nextStageIndex"])
                valid_order = (
                    bool(state["started"])
                    and not state["completed"]
                    and next_stage_index < len(planned_stage_order)
                    and planned_stage_order[next_stage_index] == stage_id
                )
                if valid_order:
                    state["nextStageIndex"] = next_stage_index + 1
            else:
                valid_order = (
                    bool(state["started"])
                    and not state["completed"]
                    and int(state["nextStageIndex"])
                    == len(planned_stage_order)
                )
                if valid_order:
                    state["completed"] = True
            if not valid_order:
                invalid_count += 1
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
                    "questionId": question_id,
                    "questionKey": str(target.get("questionKey") or ""),
                    "questionLabel": str(target.get("questionLabel") or "")
                    or f"問{target['targetIndex']}",
                    "sectionLabel": str(target.get("sectionLabel") or ""),
                    "displayLabel": str(
                        target.get("displayLabel")
                        or target.get("questionLabel")
                        or f"問{target['targetIndex']}"
                    ),
                    "displayOrder": int(
                        target.get("displayOrder") or target["targetIndex"]
                    ),
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

        declared_completed_questions = {
            event["questionId"]
            for event in events
            if event["event"] == "question_completed"
        }
        processed_work_items = {
            (event["questionId"], event["stageId"])
            for event in events
            if event["event"] == "stage_completed" and event["stageId"]
        }
        planned_by_question: dict[str, set[str]] = {}
        for question_id, stage_id in planned_work_items or set():
            planned_by_question.setdefault(question_id, set()).add(stage_id)
        verified_run = bool(
            manifest.get("status") == "succeeded"
            and manifest.get("receiptValidated") is True
        )
        validated_work_items = processed_work_items if verified_run else set()
        completion = derive_progress_completion(
            {str(target["id"]) for target in targets},
            planned_by_question,
            processed_work_items,
            processed_work_items,
            declared_completed_questions,
            validated_work_items,
            declared_completed_questions if verified_run else set(),
        )
        touched_questions = completion.touched_questions
        processed_questions = completion.processed_questions
        validated_questions = completion.validated_questions
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
                    "processed": question_id in processed_questions,
                    "completed": question_id in validated_questions,
                    "approvalState": (
                        "validated"
                        if question_id in validated_questions
                        else "failed_unapproved"
                        if manifest.get("status") == "failed"
                        else "processed_unverified"
                        if question_id in processed_questions
                        else "working"
                    ),
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
            group_processed = group_targets & processed_questions
            group_completed = group_targets & validated_questions
            groups.append(
                {
                    "listGroupId": group_id,
                    "targetQuestionCount": len(group_targets),
                    "completedQuestionCount": len(group_completed),
                    "processedQuestionCount": len(group_processed),
                    "percent": round(
                        (len(group_completed) / len(group_targets)) * 100
                    ) if group_targets else 0,
                }
            )
        target_count = len(targets) or int(manifest.get("targetCount") or 0)
        current = copy.deepcopy(events[-1]) if events else None
        if current is not None:
            current_question = next(
                (
                    question
                    for question in questions
                    if question.get("questionId") == current.get("questionId")
                ),
                None,
            )
            if current_question is not None:
                current["approvalState"] = current_question.get(
                    "approvalState"
                )
        payload.update(
            {
                "targetQuestionCount": target_count,
                "completedQuestionCount": len(validated_questions),
                "touchedQuestionCount": len(touched_questions),
                "processedQuestionCount": len(processed_questions),
                "validatedQuestionCount": len(validated_questions),
                "completedWorkItemCount": len(validated_work_items),
                "processedWorkItemCount": len(processed_work_items),
                "validatedWorkItemCount": len(validated_work_items),
                "percent": round(
                    (len(validated_questions) / target_count) * 100
                ) if target_count else 0,
                "processedPercent": round(
                    (len(processed_questions) / target_count) * 100
                ) if target_count else 0,
                "current": current,
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
            backup_root = manifest_path.parent / "baseline_files"
            try:
                transaction = capture_write_snapshot(
                    self.repo_root,
                    tracked_roots,
                    backup_root,
                )
            except (OSError, WriteTransactionError) as exc:
                shutil.rmtree(backup_root, ignore_errors=True)
                raise QualificationRunError(
                    f"書込transactionのbaselineを保存できません: {exc}"
                ) from exc
            payload = {
                "schemaVersion": "question-maintenance-baseline/v2",
                "roots": [
                    path.relative_to(self.repo_root).as_posix()
                    for path in tracked_roots
                ],
                "files": _snapshot_roots(self.repo_root, tracked_roots),
                "writeTransaction": transaction,
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
            manifest["rollback"] = {
                "status": "available",
                "restoredFiles": [],
                "remainingChangedFiles": [],
                "deltaUnknown": False,
                "message": "検証前の失敗時に開始前の状態へ戻せます。",
            }
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

    def _finalize_validated_artifact_sync_incomplete(
        self,
        manifest_path: Path,
        manifest: dict[str, Any],
        *,
        artifact_status: str,
        message: str,
        result_if_missing: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if manifest.get("receiptValidated") is not True:
            raise QualificationRunError(
                "未検証のpatchをartifactSync失敗から成功へ変更できません。"
            )
        if manifest.get("kind") not in {"human", "orchestration"}:
            raise QualificationRunError(
                "artifactSync失敗を分離できるrun種別ではありません。"
            )
        if artifact_status not in {"failed", "interrupted"}:
            raise QualificationRunError(
                "artifactSync未完了のstatusが不正です。"
            )
        sync_message = str(message or "").strip()
        if not sync_message:
            raise QualificationRunError(
                "artifactSync未完了の説明がありません。"
            )

        result = manifest.get("result")
        if (
            (not isinstance(result, Mapping) or result.get("status") != "succeeded")
            and result_if_missing is not None
        ):
            result = self._validated_result_receipt(result_if_missing)
            receipt_path = self._result_path(manifest_path, manifest)
            self._write_json(receipt_path, result)
            manifest["result"] = result
            manifest["resultReceiptHash"] = hashlib.sha256(
                receipt_path.read_bytes()
            ).hexdigest()

        current_sync = manifest.get("artifactSync")
        current_sync = current_sync if isinstance(current_sync, Mapping) else {}
        now = _now()
        manifest.update(
            {
                "status": "succeeded",
                "receiptValidated": True,
                "artifactSync": {
                    "status": artifact_status,
                    "groups": copy.deepcopy(list(current_sync.get("groups") or [])),
                    "message": sync_message,
                },
                "error": None,
                "updatedAt": now,
                "finishedAt": now,
            }
        )
        self._write_manifest(manifest_path, manifest)
        return manifest

    @staticmethod
    def _block_execution_from(
        question: dict[str, Any],
        stage_index: int,
        reason: str,
    ) -> None:
        stages = question.get("stages") or []
        stage_id = str(stages[stage_index].get("stageId") or "")
        stages[stage_index].update(
            status="blocked",
            error=reason,
            finishedAt=_now(),
        )
        for dependent in stages[stage_index + 1 :]:
            if str(dependent.get("status") or "") in {
                "validated",
                "not_applicable",
            }:
                continue
            dependent.update(
                status="blocked",
                error=f"前工程 {stage_id} の停止により保留: {reason}",
                finishedAt=_now(),
            )
        refresh_question_status(question)

    @staticmethod
    def _child_identity_matches_question(
        child: Mapping[str, Any],
        question: Mapping[str, Any],
    ) -> bool:
        targets = [
            value
            for value in child.get("progressTargets") or []
            if isinstance(value, Mapping)
        ]
        if len(targets) != 1:
            return False
        expected = SourceIdentityBinding.from_mapping(question)
        actual = SourceIdentityBinding.from_mapping(targets[0])
        return expected.is_complete() and expected == actual

    def _recover_parent_committing_executions(
        self,
        manifest: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
        executions = copy.deepcopy(list(manifest.get("questionExecutions") or []))
        recovered_receipts: list[dict[str, Any]] = []
        confirmed_group_ids = {
            str(value) for value in manifest.get("confirmedGroupIds") or [] if value
        }
        unsafe_child_id: str | None = None
        qualification = str(manifest.get("qualification") or "")
        parent_run_id = str(manifest.get("runId") or "")
        for question in executions:
            if not isinstance(question, dict):
                continue
            for stage_index, stage in enumerate(question.get("stages") or []):
                if not isinstance(stage, dict) or stage.get("status") != "committing":
                    continue
                child_ids = [str(value) for value in stage.get("childRunIds") or [] if value]
                child_id = child_ids[-1] if child_ids else ""
                try:
                    child_path = self._manifest_path(qualification, child_id)
                    child = self._load_manifest(child_path)
                except (OSError, QualificationRunError, ValueError):
                    child = {}
                expected_stage_id = str(stage.get("stageId") or "")
                binding_matches = bool(
                    child
                    and str(child.get("parentRunId") or "") == parent_run_id
                    and str(child.get("flowPhaseId") or "") == expected_stage_id
                    and (
                        str(child.get("stageId") or "") == expected_stage_id
                        or list(child.get("stageIds") or []) == [expected_stage_id]
                    )
                    and self._child_identity_matches_question(child, question)
                )
                if (
                    binding_matches
                    and child.get("status") == "validating"
                    and child.get("receiptValidated") is True
                ):
                    child.update(
                        status="succeeded",
                        error=None,
                        finishedAt=child.get("finishedAt") or _now(),
                        updatedAt=_now(),
                    )
                    self._write_manifest(child_path, child)
                receipt = child.get("workVersionReceipt")
                result = child.get("result")
                child_succeeded = bool(
                    binding_matches
                    and child.get("status") == "succeeded"
                    and child.get("receiptValidated") is True
                    and isinstance(result, Mapping)
                    and result.get("status") == "succeeded"
                    and child.get("deltaUnknown") is not True
                    and isinstance(receipt, Mapping)
                )
                if child_succeeded:
                    stage.update(
                        status="validated",
                        outputFingerprint=_child_output_fingerprint(child),
                        error=None,
                        finishedAt=_now(),
                    )
                    recovered_receipts.append(dict(receipt))
                    if question.get("listGroupId"):
                        confirmed_group_ids.add(str(question["listGroupId"]))
                    refresh_question_status(question)
                    continue
                if binding_matches and _isolated_failure_state(child):
                    result_summary = (
                        str(result.get("summary") or "")
                        if isinstance(result, Mapping)
                        else ""
                    )
                    reason = str(
                        child.get("error")
                        or result_summary
                        or "再起動前の一問writerはrollback済みです。"
                    )
                    self._block_execution_from(question, stage_index, reason)
                    continue
                unsafe_child_id = child_id or "unknown"
                reason = (
                    "再起動前の一問writerと親queueのidentity又は確定receiptを"
                    "照合できないため、安全側で全writerを停止しました。"
                )
                self._block_execution_from(question, stage_index, reason)
                for candidate in executions:
                    if not isinstance(candidate, dict):
                        continue
                    for index, candidate_stage in enumerate(candidate.get("stages") or []):
                        if str(candidate_stage.get("status") or "") in {
                            "validated",
                            "not_applicable",
                            "blocked",
                        }:
                            continue
                        self._block_execution_from(candidate, index, reason)
                        break
                manifest.update(
                    retrySafe=False,
                    retryUnsafeReason=reason,
                    unsafeChildRunId=unsafe_child_id,
                )
                break
            if unsafe_child_id:
                break
        manifest["confirmedGroupIds"] = sorted(confirmed_group_ids)
        return executions, recovered_receipts, unsafe_child_id

    @staticmethod
    def _recover_running_merge_transactions(
        manifest: dict[str, Any],
        executions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        dependencies = [
            dict(value)
            for value in manifest.get("resumeMergeDependencies") or []
            if isinstance(value, Mapping)
        ]
        for dependency in dependencies:
            if str(dependency.get("status") or "") != "running":
                continue
            now = _now()
            reason = (
                "ローカルUIの再起動で工程間mergeの完了を確認できません。"
                "後続writerの前に自動で再mergeします。"
            )
            dependency.update(
                status="interrupted",
                remergeRequired=True,
                message=reason,
                interruptedAt=now,
                finishedAt=now,
                updatedAt=now,
            )
            executions = block_group_after_stage(
                executions,
                str(dependency.get("listGroupId") or ""),
                str(dependency.get("afterStageId") or ""),
                reason,
            )
            manifest["queueStatus"] = "partial"
        manifest["resumeMergeDependencies"] = dependencies
        return executions

    def _recover_interrupted_runs(self) -> None:
        if not self.root.is_dir():
            return
        with self._lock:
            paths = list(self.root.glob("*/*/manifest.json"))
            paths.sort(
                key=lambda candidate: (
                    self._load_manifest(candidate).get("kind") == "orchestration",
                    str(candidate),
                )
            )
            for path in paths:
                manifest = self._load_manifest(path)
                if manifest.get("status") not in {"queued", "running", "validating"}:
                    continue
                if (
                    manifest.get("status") == "validating"
                    and manifest.get("kind") in {"human", "orchestration"}
                    and manifest.get("receiptValidated") is True
                ):
                    fallback_result = (
                        {
                            "status": "succeeded",
                            "summary": (
                                "子工程のpatchは検証済みです。"
                                "公開用データの同期は再実行が必要です。"
                            ),
                            "commands": [
                                {
                                    "command": (
                                        "workflow: validate child maintenance receipts"
                                    ),
                                    "status": "pass",
                                }
                            ],
                            "changedFiles": [],
                            "resolvedFailedDeltaPaths": [],
                        }
                        if manifest.get("kind") == "orchestration"
                        else None
                    )
                    self._finalize_validated_artifact_sync_incomplete(
                        path,
                        manifest,
                        artifact_status="interrupted",
                        message=(
                            "公開用データの自動更新中にローカルUIが停止しました。"
                            "問題詳細又は管理機能から再生成できます。"
                        ),
                        result_if_missing=fallback_result,
                    )
                    continue
                if (
                    manifest.get("kind") == "orchestration"
                    and isinstance(manifest.get("questionExecutions"), list)
                ):
                    recovered_before_interrupt, recovered_receipts, _unsafe = (
                        self._recover_parent_committing_executions(manifest)
                    )
                    recovered_executions = recover_interrupted_executions(
                        recovered_before_interrupt
                    )
                    recovered_executions = self._recover_running_merge_transactions(
                        manifest,
                        recovered_executions,
                    )
                    execution_summary = queue_summary(recovered_executions)
                    existing_receipt = manifest.get("workVersionReceipt")
                    existing_items = (
                        list(existing_receipt.get("items") or [])
                        if isinstance(existing_receipt, Mapping)
                        else []
                    )
                    receipt_items: list[dict[str, Any]] = []
                    seen_receipts: set[str] = set()
                    for value in [*existing_items, *recovered_receipts]:
                        if not isinstance(value, Mapping):
                            continue
                        encoded = json.dumps(
                            value,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        if encoded in seen_receipts:
                            continue
                        seen_receipts.add(encoded)
                        receipt_items.append(dict(value))
                    work_version_receipt = {
                        "recordedCount": sum(
                            int(value.get("recordedCount") or 0)
                            for value in receipt_items
                        ),
                        "items": receipt_items,
                    }
                    manifest.update(
                        questionExecutions=recovered_executions,
                        questionExecutionSummary=execution_summary,
                        workVersionReceipt=work_version_receipt,
                        blockedQuestionCount=execution_summary[
                            "blockedQuestionCount"
                        ],
                        blockedWorkItemCount=execution_summary[
                            "blockedWorkItemCount"
                        ],
                        validatedQuestionCount=execution_summary[
                            "validatedQuestionCount"
                        ],
                        validatedWorkItemCount=execution_summary[
                            "validatedWorkItemCount"
                        ],
                        queueStatus=(
                            "partial"
                            if execution_summary["blockedQuestionCount"]
                            else "interrupted"
                        ),
                    )
                    if (
                        not execution_summary["pendingWorkItemCount"]
                        and execution_summary["validatedWorkItemCount"]
                        and manifest.get("retrySafe") is not False
                        and not any(
                            dependency.get("remergeRequired") is True
                            for dependency in manifest.get(
                                "resumeMergeDependencies"
                            )
                            or []
                            if isinstance(dependency, Mapping)
                        )
                    ):
                        queue_status = (
                            "partial"
                            if execution_summary["blockedQuestionCount"]
                            else "succeeded"
                        )
                        fallback_result = {
                            "status": "succeeded",
                            "summary": (
                                "一問writerの確定receiptを再起動時に照合しました。"
                                "公開用データの同期だけ再実行が必要です。"
                            ),
                            "commands": [
                                {
                                    "command": (
                                        "workflow: recover validated per-question receipts"
                                    ),
                                    "status": "pass",
                                }
                            ],
                            "changedFiles": [],
                            "resolvedFailedDeltaPaths": [],
                        }
                        manifest.update(
                            status="validating",
                            queueStatus=queue_status,
                            executionPhase="done",
                            currentPhaseId=None,
                            receiptValidated=True,
                            artifactSync={"status": "running", "groups": []},
                            error=None,
                        )
                        self._finalize_validated_artifact_sync_incomplete(
                            path,
                            manifest,
                            artifact_status="interrupted",
                            message=(
                                "patchは確定済みです。公開用データの自動更新中に"
                                "ローカルUIが停止したため、手動再生成できます。"
                            ),
                            result_if_missing=fallback_result,
                        )
                        continue
                was_running = manifest.get("status") in {"running", "validating"}
                changed_files: list[str] | None = None
                if was_running and manifest.get("kind") == "human":
                    rollback = self._rollback_baseline_delta(path, manifest)
                    if rollback is not None:
                        manifest["rollback"] = rollback
                        changed_files = (
                            None
                            if rollback.get("deltaUnknown") is True
                            else list(
                                rollback.get("remainingChangedFiles") or []
                            )
                        )
                    else:
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

            # 子runのrollback結果は親queueの再開可否へ集約する。親を先に
            # 読んだ場合でも、全run回収後の二巡目なら確定状態を判定できる。
            for path in self.root.glob("*/*/manifest.json"):
                manifest = self._load_manifest(path)
                if (
                    manifest.get("kind") != "orchestration"
                    or manifest.get("retrySafe") is False
                ):
                    continue
                unsafe_child_id = ""
                for child_run_id in manifest.get("childRunIds") or []:
                    try:
                        child = self._load_manifest(
                            self._manifest_path(
                                str(manifest["qualification"]),
                                str(child_run_id),
                            )
                        )
                    except (OSError, QualificationRunError, ValueError):
                        unsafe_child_id = str(child_run_id)
                        break
                    if not _child_retry_safe(child):
                        unsafe_child_id = str(child_run_id)
                        break
                if not unsafe_child_id:
                    continue
                reason = (
                    "再起動後に子作業のrollback又は残存差分を確認できないため、"
                    "手動で差分を解消するまで再開できません。"
                )
                manifest.update(
                    retrySafe=False,
                    retryUnsafeReason=reason,
                    unsafeChildRunId=unsafe_child_id,
                    updatedAt=_now(),
                )
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
            or payload.get("schemaVersion")
            not in {
                "question-maintenance-baseline/v1",
                "question-maintenance-baseline/v2",
            }
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

    def rollback_baseline(
        self,
        qualification: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        """Restore an unvalidated human run to its captured write boundary."""

        manifest_path = self._manifest_path(qualification, run_id)
        with self._lock:
            manifest = self._load_manifest(manifest_path)
            if manifest.get("receiptValidated") is True:
                return None
            rollback = self._rollback_baseline_delta(manifest_path, manifest)
            if rollback is None:
                return None
            manifest["rollback"] = rollback
            manifest["deltaUnknown"] = bool(
                rollback.get("deltaUnknown")
                or rollback.get("remainingChangedFiles")
            )
            manifest["updatedAt"] = _now()
            self._write_manifest(manifest_path, manifest)
            return copy.deepcopy(rollback)

    def discard_baseline_backups(
        self,
        qualification: str,
        run_id: str,
    ) -> None:
        with self._lock:
            path = self._manifest_path(qualification, run_id).parent / "baseline_files"
            shutil.rmtree(path, ignore_errors=True)

    def _rollback_baseline_delta(
        self,
        manifest_path: Path,
        manifest: Mapping[str, Any],
    ) -> dict[str, Any] | None:
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
            or payload.get("schemaVersion") != "question-maintenance-baseline/v2"
            or not isinstance(payload.get("writeTransaction"), Mapping)
        ):
            return None
        backup_root = manifest_path.parent / "baseline_files"
        try:
            restored = restore_write_snapshot(
                self.repo_root,
                payload["writeTransaction"],
                backup_root,
            )
            remaining = self._recover_baseline_delta(manifest_path, manifest)
            if remaining is None:
                raise WriteTransactionError(
                    "rollback後の差分を確認できません。"
                )
            status = "succeeded" if not remaining else "failed"
            message = (
                "検証前の変更を開始前の状態へ戻しました。"
                if status == "succeeded"
                else "rollback後も開始前と異なるfileが残っています。"
            )
        except (OSError, WriteTransactionError) as exc:
            restored = []
            remaining = self._recover_baseline_delta(manifest_path, manifest)
            status = "failed"
            message = f"検証前の変更をrollbackできませんでした: {exc}"
        delta_unknown = remaining is None
        rollback = {
            "status": status,
            "restoredFiles": restored,
            "remainingChangedFiles": list(remaining or []),
            "deltaUnknown": delta_unknown,
            "message": message,
        }
        if status == "succeeded":
            shutil.rmtree(backup_root, ignore_errors=True)
        return rollback

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
        if receipt["status"] == "succeeded" and not requires_server_validation:
            manifest["receiptValidated"] = True
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
                "新規作成又は更新するpatch rowには、manifestのtargetRecordBindingsで対応するsourceRecordRefを保存する。uiQuestionIdをsourceRecordRefの代わりに保存しない。",
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
                        "次の未確定差分は現在工程の検証対象に含まれる:",
                        *(f"- `{path}`" for path in resolvable_failed_paths),
                        "解決記録は成功検証後にserverが確定するため、receiptへ申告しない。",
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
        self.question_proposals = QuestionPatchProposalStore(
            self.repo_root,
            self.store.root,
        )

    def _technical_log_emitter(
        self,
        qualification: str,
        run_id: str,
        emit: Callable[[str], None],
    ) -> Callable[[str], None]:
        """job表示を保ったまま、指定runにも技術ログを追記する。"""

        log_failure_reported = False

        def append_technical_log(value: Mapping[str, Any]) -> None:
            nonlocal log_failure_reported
            try:
                self.store.append_technical_log(
                    qualification,
                    run_id,
                    value,
                )
            except Exception as exc:  # noqa: BLE001
                if log_failure_reported:
                    return
                log_failure_reported = True
                emit(
                    "技術ログを保存できませんでした"
                    f"（{type(exc).__name__}）。整備処理は継続します。"
                )

        def logged_emit(line: str) -> None:
            emit(line)
            append_technical_log({"message": line})

        def logged_event(value: Mapping[str, Any]) -> None:
            event_emit = getattr(emit, "event", None)
            if callable(event_emit):
                event_emit(value)
            else:
                emit(str(value.get("message") or ""))
            append_technical_log(value)

        heartbeat = getattr(emit, "heartbeat", None)
        if callable(heartbeat):
            setattr(logged_emit, "heartbeat", heartbeat)
        setattr(logged_emit, "event", logged_event)
        run_ids = {
            str(value)
            for value in getattr(emit, "technical_run_ids", set())
            if value
        }
        run_ids.add(run_id)
        setattr(logged_emit, "technical_run_ids", run_ids)
        return logged_emit

    def _run_with_technical_log(
        self,
        qualification: str,
        run_id: str,
        emit: Callable[[str], None],
        worker: Callable[[Callable[[str], None]], dict[str, Any]],
    ) -> dict[str, Any]:
        """job表示とrun永続ログへ、同じ安全な技術イベントを流す。"""

        logged_emit = self._technical_log_emitter(
            qualification,
            run_id,
            emit,
        )
        try:
            return worker(logged_emit)
        except Exception as exc:
            getattr(logged_emit, "event")(
                {
                    "level": "error",
                    "message": f"job failed: {exc}",
                }
            )
            raise

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
            try:
                question_executions = build_question_executions(plan)
            except QuestionWorkQueueError as exc:
                raise QualificationRunError(str(exc)) from exc
            if len(maintenance_phases) > 1 or question_executions:
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
                    "parallelStrategy": "per_question_preparation",
                    "maintenancePhases": maintenance_phases,
                    "phaseExecutions": phase_executions,
                    "currentPhaseId": None,
                    "childRunIds": [],
                    "questionExecutions": question_executions,
                    "queueStatus": "queued",
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
                        worker=lambda emit: self._run_with_technical_log(
                            qualification,
                            run["runId"],
                            emit,
                            lambda logged_emit: self._run_maintenance_flow(
                                qualification,
                                run["runId"],
                                logged_emit,
                            ),
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
                    worker=lambda emit: self._run_with_technical_log(
                        qualification,
                        run["runId"],
                        emit,
                        lambda logged_emit: self._run_human(
                            qualification,
                            run["runId"],
                            saved_prompt,
                            "maintenance",
                            logged_emit,
                        ),
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
                worker=lambda emit: self._run_with_technical_log(
                    qualification,
                    run["runId"],
                    emit,
                    lambda logged_emit: self._run_delivery(
                        plan,
                        run["runId"],
                        logged_emit,
                    ),
                ),
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

    def technical_log(self, qualification: str, run_id: str) -> dict[str, Any]:
        run = self.store.get(qualification, run_id)
        if str(run.get("qualification") or "") != qualification:
            raise QualificationRunError("対象資格と作業履歴が一致しません。")
        return self.store.technical_log(qualification, run_id)

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
        supplied_bindings = [
            dict(value)
            for value in review.get("targetRecordBindings") or []
            if isinstance(value, Mapping)
        ]
        binding_candidates: list[Mapping[str, Any]] = [question]
        if (
            review.get("requestKind") == "qualification_law_audit"
            and not supplied_bindings
        ):
            inventory = getattr(self.workflow, "inventory", None)
            if inventory is None:
                raise QualificationRunError(
                    "法令監査のID binding用inventoryがありません。"
                )
            binding_candidates = [
                candidate
                for group_id in target_group_ids
                for candidate in (
                    inventory.group(qualification, str(group_id)).get(
                        "questions"
                    )
                    or []
                )
                if isinstance(candidate, Mapping)
            ]
        target_record_bindings: list[dict[str, Any]] = []
        used_supplied_binding_indexes: set[int] = set()
        for alias_group in target_record_alias_groups:
            group_aliases = set(alias_group)
            available = [
                (index, binding)
                for index, binding in enumerate(supplied_bindings)
                if index not in used_supplied_binding_indexes
            ]
            exact_source_ref = [
                (index, binding)
                for index, binding in available
                if SourceIdentityBinding.from_mapping(
                    binding
                ).source_record_ref
                in group_aliases
            ]
            exact_ui = [
                (index, binding)
                for index, binding in available
                if str(binding.get("uiQuestionId") or "") in group_aliases
            ]
            legacy = [
                (index, binding)
                for index, binding in available
                if group_aliases & target_identity_aliases(binding)
            ]
            supplied = exact_source_ref or exact_ui or legacy
            if supplied:
                if len(supplied) != 1:
                    raise QualificationRunError(
                        "対象recordのID bindingが重複しています。"
                    )
                supplied_index, supplied_binding = supplied[0]
                used_supplied_binding_indexes.add(supplied_index)
                source_binding = SourceIdentityBinding.from_mapping(
                    supplied_binding
                )
                target_record_bindings.append(
                    {
                        "uiQuestionId": str(
                            supplied_binding.get("uiQuestionId") or ""
                        ),
                        **source_binding.as_mapping(),
                        "aliases": list(alias_group),
                    }
                )
                continue
            matches = [
                candidate
                for candidate in binding_candidates
                if set(alias_group) & self._question_record_aliases(candidate)
            ]
            if len(matches) != 1:
                raise QualificationRunError(
                    "対象recordのID bindingを一意に作成できません。"
                )
            candidate = matches[0]
            source_binding = SourceIdentityBinding.from_mapping(candidate)
            target_record_bindings.append(
                {
                    "uiQuestionId": str(candidate.get("id") or ""),
                    **source_binding.as_mapping(),
                    "aliases": list(alias_group),
                }
            )
        if review.get("requestKind") == "qualification_law_audit" and any(
            not SourceIdentityBinding.from_mapping(binding).is_complete()
            for binding in target_record_bindings
        ):
            raise QualificationRunError(
                "法令監査のsource identity 3要素を確認できません。"
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
        if review.get("requestKind") == "qualification_law_audit":
            self._reject_ambiguous_existing_patch_rows(
                allowed_patch_files,
                target_record_scopes,
                target_record_bindings,
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
        elif is_law_audit_review(review):
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
            "targetRecordBindings": target_record_bindings,
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
                stage_id: [
                    str(binding.get("uiQuestionId") or "")
                    for binding in target_record_bindings
                    if binding.get("uiQuestionId")
                ]
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
                worker=lambda emit: self._run_with_technical_log(
                    qualification,
                    run["runId"],
                    emit,
                    lambda logged_emit: self._run_human(
                        qualification,
                        run["runId"],
                        saved_prompt,
                        work_type,
                        logged_emit,
                    ),
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
        law_related = is_law_audit_review(review)
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
        law_audit_requested = is_law_audit_review(review)
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
            question.get("sourceRecordRef"),
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
            if is_law_audit_review(review):
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

    def _reject_ambiguous_existing_patch_rows(
        self,
        patch_files: set[str],
        record_scopes: Mapping[str, list[list[str]]],
        raw_bindings: list[Mapping[str, Any]],
    ) -> None:
        bindings = [
            {
                "identity": SourceIdentityBinding.from_mapping(value),
                "aliases": {
                    str(alias)
                    for alias in [
                        *(value.get("aliases") or []),
                        value.get("uiQuestionId"),
                        *SourceIdentityBinding.from_mapping(value).as_tuple(),
                    ]
                    if alias
                },
            }
            for value in raw_bindings
        ]
        ambiguous: set[str] = set()
        for relative in sorted(patch_files):
            path = self.repo_root / self._maintenance_relative_path(relative)
            if not path.is_file() or path.suffix.lower() != ".json":
                continue
            scope_aliases = {
                str(alias)
                for group in record_scopes.get(relative, [])
                for alias in group
            }
            scoped_bindings = [
                binding
                for binding in bindings
                if binding["identity"].source_record_ref in scope_aliases
            ]
            if len(scoped_bindings) < 2:
                continue
            for entry in _record_snapshot(path):
                entry_aliases = {str(value) for value in entry.get("aliases") or []}
                entry_identity = SourceIdentityBinding.from_mapping(
                    entry.get("identityFields") or {}
                )
                candidates = [
                    binding
                    for binding in scoped_bindings
                    if entry_aliases & binding["aliases"]
                ]
                if len(candidates) < 2:
                    continue
                if entry_identity.source_record_ref:
                    exact = [
                        binding
                        for binding in candidates
                        if binding["identity"].source_record_ref
                        == entry_identity.source_record_ref
                    ]
                    if len(exact) == 1:
                        continue
                scores = [
                    (len(entry_aliases & binding["aliases"]), binding)
                    for binding in candidates
                ]
                best = max(score for score, _binding in scores)
                if sum(score == best for score, _binding in scores) > 1:
                    ambiguous.add(relative)
                    break
        if ambiguous:
            raise QualificationRunError(
                "既存patch行をsource recordへ一意に対応できません。"
                "sourceRecordRefの手動確認が必要です: "
                + ", ".join(sorted(ambiguous))
            )

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
        resume_work_item_keys = {
            str(value) for value in parent.get("resumeWorkItemKeys") or [] if value
        }
        if resume_work_item_keys and plan.get("progressTargets"):
            resumable_targets = [
                target
                for target in plan.get("progressTargets") or []
                if isinstance(target, Mapping)
                and work_item_key(target, stage_ids[0]) in resume_work_item_keys
            ]
            if resumable_targets:
                plan = subset_question_plan(
                    plan,
                    [
                        str(target.get("id") or target.get("uiQuestionId") or "")
                        for target in resumable_targets
                    ],
                )
            else:
                plan.update(
                    targetCount=0,
                    workItemCount=0,
                    targetQuestionKeys=[],
                    progressTargets=[],
                    targetRecordBindings=[],
                    targetRecordAliasGroups=[],
                    targetRecordScopes={},
                    targetSourceRecordScopes={},
                    policyTargets={},
                )
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

    @staticmethod
    def _queue_stage(
        parent: Mapping[str, Any], question_id: str, stage_id: str
    ) -> dict[str, Any] | None:
        for question in parent.get("questionExecutions") or []:
            if (
                isinstance(question, Mapping)
                and str(question.get("questionId") or "") == question_id
            ):
                return next(
                    (
                        dict(stage)
                        for stage in question.get("stages") or []
                        if isinstance(stage, Mapping)
                        and str(stage.get("stageId") or "") == stage_id
                    ),
                    None,
                )
        return None

    def _refresh_queued_stage_inputs(
        self,
        qualification: str,
        run_id: str,
        phase_plan: Mapping[str, Any],
        targets: list[dict[str, Any]],
        stage_id: str,
    ) -> None:
        policy_fingerprint = str(
            (phase_plan.get("policyFingerprints") or {}).get(stage_id) or ""
        )
        for target in targets:
            question_id = str(
                target.get("id") or target.get("uiQuestionId") or ""
            )
            current = self._queue_stage(
                self.store.get(qualification, run_id),
                question_id,
                stage_id,
            )
            expected_key = work_item_key(target, stage_id)
            if current is None or str(current.get("workItemKey") or "") != expected_key:
                raise QualificationRunError(
                    f"工程開始時の一問queue識別子が一致しません: "
                    f"{question_id} / {stage_id}"
                )
            expected_input = input_fingerprint(
                target,
                stage_id,
                policy_fingerprint,
            )
            if str(current.get("inputFingerprint") or "") == expected_input:
                continue
            if str(current.get("status") or "") != "queued":
                reason = (
                    "工程開始時に入力又は方針が変更されたため、"
                    "この問題だけを再実行してください。"
                )
                self.store.update_question_stage(
                    qualification,
                    run_id,
                    question_id,
                    stage_id,
                    status="blocked",
                    error=reason,
                    finishedAt=_now(),
                    block_dependents=True,
                )
                continue
            self.store.update_question_stage(
                qualification,
                run_id,
                question_id,
                stage_id,
                inputFingerprint=expected_input,
                preparationPath=None,
                preparationHash=None,
                error=None,
            )

    def _reconcile_phase_targets(
        self,
        qualification: str,
        run_id: str,
        stage_id: str,
        targets: list[dict[str, Any]],
    ) -> int:
        """再計画で対象外になったqueue itemを明示的に完了させる。"""

        active_question_ids = {
            str(target.get("id") or target.get("uiQuestionId") or "")
            for target in targets
        }
        parent = self.store.get(qualification, run_id)
        reconciled = 0
        for question in parent.get("questionExecutions") or []:
            if not isinstance(question, Mapping):
                continue
            question_id = str(question.get("questionId") or "")
            if question_id in active_question_ids:
                continue
            stage = self._queue_stage(parent, question_id, stage_id)
            if stage is None:
                continue
            status = str(stage.get("status") or "")
            if status in {"validated", "not_applicable", "blocked"}:
                continue
            if status != "queued":
                raise QualificationRunError(
                    "工程再計画時に未確定の一問作業が残っています: "
                    f"{question_id} / {stage_id} / {status}"
                )
            self.store.update_question_stage(
                qualification,
                run_id,
                question_id,
                stage_id,
                status="not_applicable",
                error=None,
                finishedAt=_now(),
            )
            reconciled += 1
        return reconciled

    @staticmethod
    def _isolated_child_failure(child: Mapping[str, Any]) -> bool:
        return _isolated_failure_state(child)

    def _block_remaining_queue(
        self,
        qualification: str,
        run_id: str,
        reason: str,
    ) -> None:
        parent = self.store.get(qualification, run_id)
        for question in parent.get("questionExecutions") or []:
            if not isinstance(question, Mapping):
                continue
            first_pending = next(
                (
                    stage
                    for stage in question.get("stages") or []
                    if isinstance(stage, Mapping)
                    and str(stage.get("status") or "")
                    not in {"validated", "not_applicable"}
                ),
                None,
            )
            if first_pending is None:
                continue
            if str(first_pending.get("status") or "") == "blocked":
                # 先に失敗したwork itemの固有理由は保持する。依存工程は
                # 最初にblockedへ遷移した時点で既に保留済みである。
                continue
            self.store.update_question_stage(
                qualification,
                run_id,
                str(question.get("questionId") or ""),
                str(first_pending.get("stageId") or ""),
                status="blocked",
                error=reason,
                finishedAt=_now(),
                block_dependents=True,
            )

    def _block_group_queue(
        self,
        qualification: str,
        run_id: str,
        list_group_id: str,
        reason: str,
    ) -> None:
        parent = self.store.get(qualification, run_id)
        for question in parent.get("questionExecutions") or []:
            if (
                not isinstance(question, Mapping)
                or str(question.get("listGroupId") or "") != list_group_id
            ):
                continue
            first_pending = next(
                (
                    stage
                    for stage in question.get("stages") or []
                    if isinstance(stage, Mapping)
                    and str(stage.get("status") or "")
                    not in {"validated", "not_applicable"}
                ),
                None,
            )
            if first_pending is None or str(first_pending.get("status") or "") == "blocked":
                continue
            self.store.update_question_stage(
                qualification,
                run_id,
                str(question.get("questionId") or ""),
                str(first_pending.get("stageId") or ""),
                status="blocked",
                error=reason,
                finishedAt=_now(),
                block_dependents=True,
            )

    def _persist_merge_transaction(
        self,
        qualification: str,
        run_id: str,
        list_group_id: str,
        after_stage_id: str,
        *,
        status: str,
        message: str | None,
    ) -> list[dict[str, Any]]:
        parent = self.store.get(qualification, run_id)
        transactions = [
            dict(value)
            for value in parent.get("resumeMergeDependencies") or []
            if isinstance(value, Mapping)
        ]
        transaction = next(
            (
                value
                for value in transactions
                if str(value.get("listGroupId") or "") == list_group_id
                and str(value.get("afterStageId") or "") == after_stage_id
            ),
            None,
        )
        if transaction is None:
            transaction = {
                "listGroupId": list_group_id,
                "afterStageId": after_stage_id,
            }
            transactions.append(transaction)
        now = _now()
        transaction.update(
            status=status,
            message=message,
            remergeRequired=status not in {"succeeded", "current"},
            updatedAt=now,
        )
        if status == "running":
            transaction.update(startedAt=now, finishedAt=None)
        else:
            transaction["finishedAt"] = now
        self.store.update(
            qualification,
            run_id,
            resumeMergeDependencies=transactions,
        )
        return transactions

    def _refresh_merged_views_transaction(
        self,
        qualification: str,
        run_id: str,
        list_group_id: str,
        after_stage_id: str,
        emit: Callable[[str], None],
    ) -> dict[str, Any]:
        self._persist_merge_transaction(
            qualification,
            run_id,
            list_group_id,
            after_stage_id,
            status="running",
            message="工程間mergeを実行中です。",
        )
        try:
            raw_result = self.synchronizer.refresh_merged_views(
                qualification,
                list_group_id,
                emit,
            )
        except Exception as exc:  # noqa: BLE001
            message = str(exc) or "工程間mergeで例外が発生しました。"
            self._persist_merge_transaction(
                qualification,
                run_id,
                list_group_id,
                after_stage_id,
                status="failed",
                message=message,
            )
            # 派生成果物の例外だけならgroup隔離できる。source境界まで
            # 壊れている場合は例外を正規化せず、親run全体をfail-closeする。
            self._check_source_immutability(emit)
            return {
                "listGroupId": list_group_id,
                "status": "failed",
                "message": message,
            }
        if not isinstance(raw_result, Mapping):
            result = {
                "listGroupId": list_group_id,
                "status": "failed",
                "message": "工程間mergeの結果形式が不正です。",
            }
        else:
            result = dict(raw_result)
            result.setdefault("listGroupId", list_group_id)
        reported_status = str(result.get("status") or "failed")
        status = reported_status if reported_status in {"succeeded", "current"} else "failed"
        message = str(
            result.get("message")
            or (
                "工程間mergeを完了しました。"
                if status in {"succeeded", "current"}
                else "工程間mergeを完了できませんでした。"
            )
        )
        result.update(status=status, message=message)
        self._persist_merge_transaction(
            qualification,
            run_id,
            list_group_id,
            after_stage_id,
            status=status,
            message=message,
        )
        return result

    def _refresh_resume_merge_dependencies(
        self,
        qualification: str,
        run_id: str,
        phase_id: str,
        emit: Callable[[str], None],
        sync_blocked_group_ids: set[str],
    ) -> None:
        parent = self.store.get(qualification, run_id)
        dependencies = [
            dict(value)
            for value in parent.get("resumeMergeDependencies") or []
            if isinstance(value, Mapping)
        ]
        if not dependencies:
            return
        stage_order = [
            str(value) for value in parent.get("stageIds") or [] if value
        ]
        stage_positions = {
            stage_id: index for index, stage_id in enumerate(stage_order)
        }
        current_index = stage_positions.get(phase_id)
        if current_index is None:
            return
        due_by_group: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for dependency in dependencies:
            if str(dependency.get("status") or "pending") != "pending":
                continue
            after_index = stage_positions.get(
                str(dependency.get("afterStageId") or "")
            )
            if after_index is None or after_index >= current_index:
                continue
            list_group_id = str(dependency.get("listGroupId") or "")
            after_stage_id = str(dependency.get("afterStageId") or "")
            if list_group_id:
                due_by_group.setdefault(
                    (list_group_id, after_stage_id), []
                ).append(dependency)

        for (list_group_id, after_stage_id), group_dependencies in due_by_group.items():
            emit(
                f"{list_group_id}: 後続工程の開始前に未完了の工程間mergeを再実行します。"
            )
            merge_result = self._refresh_merged_views_transaction(
                qualification,
                run_id,
                list_group_id,
                after_stage_id,
                emit,
            )
            reported_status = str(merge_result.get("status") or "failed")
            succeeded = reported_status in {"succeeded", "current"}
            status = reported_status if succeeded else "failed"
            message = str(
                merge_result.get("message")
                or (
                    "工程間mergeを完了しました。"
                    if succeeded
                    else "工程間mergeを完了できませんでした。"
                )
            )
            if succeeded:
                emit(f"{list_group_id}: 工程間mergeを確認し、後続工程へ進みます。")
            else:
                sync_blocked_group_ids.add(list_group_id)
                reason = (
                    f"{list_group_id}: 後続工程に必要な工程間mergeを"
                    f"完了できなかったため、この範囲だけを保留しました: {message}"
                )
                self._block_group_queue(
                    qualification,
                    run_id,
                    list_group_id,
                    reason,
                )
                emit(reason)

    def _prepare_question_item(
        self,
        qualification: str,
        run_id: str,
        phase_prompt: str,
        target: Mapping[str, Any],
        stage_id: str,
        emit: Callable[[str], None],
    ) -> bool:
        question_id = str(target.get("id") or target.get("uiQuestionId") or "")
        initial = self._queue_stage(
            self.store.get(qualification, run_id), question_id, stage_id
        )
        if initial is None:
            raise QualificationRunError(
                f"一問queueの準備対象がありません: {question_id} / {stage_id}"
            )
        work_key = str(initial.get("workItemKey") or "")
        input_hash = str(initial.get("inputFingerprint") or "")
        last_error: Exception | None = None
        for retry_index in range(2):
            attempts = int(initial.get("attempts") or 0) + retry_index + 1
            self.store.update_question_stage(
                qualification,
                run_id,
                question_id,
                stage_id,
                status="preparing",
                attempts=attempts,
                startedAt=_now(),
                finishedAt=None,
                error=None,
            )

            def on_thread_started(thread_id: str, session_id: str) -> None:
                self.store.update_question_stage(
                    qualification,
                    run_id,
                    question_id,
                    stage_id,
                    preparationThreadId=thread_id,
                    preparationSessionId=session_id,
                )

            def on_turn_started(_thread_id: str, turn_id: str) -> None:
                self.store.update_question_stage(
                    qualification,
                    run_id,
                    question_id,
                    stage_id,
                    preparationTurnId=turn_id,
                )

            def heartbeat() -> None:
                heartbeat_at = _now()
                self.store.update(
                    qualification,
                    run_id,
                    heartbeatAt=heartbeat_at,
                )
                job_heartbeat = getattr(emit, "heartbeat", None)
                if callable(job_heartbeat):
                    job_heartbeat()

            try:
                with tempfile.TemporaryDirectory(
                    prefix=f"question-preparation-{work_key}-"
                ) as directory:
                    result = self.app_server.run_turn(
                        _question_scope_prompt(
                            phase_prompt,
                            target,
                            repo_root=self.repo_root,
                            read_only=True,
                        ),
                        work_type=f"maintenance_prepare_{stage_id}",
                        sandbox="read-only",
                        emit=emit,
                        on_thread_started=on_thread_started,
                        on_turn_started=on_turn_started,
                        heartbeat=heartbeat,
                        cwd=Path(directory).resolve(),
                    )
                if result.changed_files:
                    raise QualificationRunError(
                        "一問のread-only準備でfile変更通知を検出しました。"
                    )
                proposal = self.question_proposals.write(
                    qualification,
                    run_id,
                    work_item_key=work_key,
                    question_id=question_id,
                    stage_id=stage_id,
                    input_fingerprint=input_hash,
                    summary=result.final_message,
                    thread_id=result.thread_id,
                    session_id=result.session_id,
                    turn_id=result.turn_id,
                )
                self.store.update_question_stage(
                    qualification,
                    run_id,
                    question_id,
                    stage_id,
                    status="prepared",
                    preparationPath=proposal["path"],
                    preparationHash=proposal["hash"],
                    preparationThreadId=result.thread_id,
                    preparationSessionId=result.session_id,
                    preparationTurnId=result.turn_id,
                    error=None,
                )
                return True
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if retry_index == 0 and _preparation_retryable(exc):
                    emit(
                        f"{target.get('displayLabel') or question_id}: "
                        "一時的な通信失敗のため読取専用準備を一度だけ再試行します。"
                    )
                    continue
                break
        reason = str(last_error or "一問の準備を完了できませんでした。")
        self.store.update_question_stage(
            qualification,
            run_id,
            question_id,
            stage_id,
            status="blocked",
            error=reason,
            finishedAt=_now(),
            block_dependents=True,
        )
        emit(f"{target.get('displayLabel') or question_id}: 準備を保留しました: {reason}")
        return False

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
            queueStatus="running",
            executionPhase="preparing",
            startedAt=_now(),
            error=None,
        )
        child_run_ids: list[str] = []
        existing_work_version_receipt = parent.get("workVersionReceipt")
        work_version_receipts: list[dict[str, Any]] = [
            dict(value)
            for value in (
                existing_work_version_receipt.get("items") or []
                if isinstance(existing_work_version_receipt, Mapping)
                else []
            )
            if isinstance(value, Mapping)
        ]
        confirmed_group_ids: set[str] = {
            str(value) for value in parent.get("confirmedGroupIds") or [] if value
        }
        sync_blocked_group_ids: set[str] = set()
        current_phase_id = ""
        try:
            self._check_source_immutability(emit)
            phases = [
                dict(value)
                for value in parent.get("phaseExecutions") or []
                if isinstance(value, Mapping)
            ]
            for phase_index, phase in enumerate(phases):
                current_phase_id = str(phase["id"])
                self._refresh_resume_merge_dependencies(
                    qualification,
                    run_id,
                    current_phase_id,
                    emit,
                    sync_blocked_group_ids,
                )
                parent = self.store.get(qualification, run_id)
                phase_plan, phase_prompt = self._flow_phase_plan_prompt(parent, phase)
                target_count = int(phase_plan.get("targetCount") or 0)
                targets = [
                    dict(value)
                    for value in phase_plan.get("progressTargets") or []
                    if isinstance(value, Mapping)
                ]
                phase_stage_id = str(
                    phase_plan.get("stageId") or current_phase_id
                )
                scope_phase = phase_stage_id in {"setup", "category_setup"}
                not_applicable_count = (
                    0
                    if scope_phase
                    else self._reconcile_phase_targets(
                        qualification,
                        run_id,
                        phase_stage_id,
                        targets,
                    )
                )
                if not target_count:
                    self._update_flow_phase(
                        qualification,
                        run_id,
                        current_phase_id,
                        status="skipped",
                        targetCount=0,
                        notApplicableCount=not_applicable_count,
                        finishedAt=_now(),
                    )
                    emit(
                        f"{phase['label']}: 現在の入力では対象外のため"
                        f"{not_applicable_count}問を完了扱いにして省略します。"
                    )
                    continue
                self.store.update(
                    qualification,
                    run_id,
                    currentPhaseId=current_phase_id,
                    executionPhase=f"preparing:{current_phase_id}",
                )
                self._update_flow_phase(
                    qualification,
                    run_id,
                    current_phase_id,
                    status="running",
                    targetCount=target_count,
                    childRunIds=[],
                    startedAt=_now(),
                    error=None,
                )
                # qualification/category setup is a real scope dependency, not a
                # question item. It stays single-run and may block its dependants.
                if scope_phase or not targets:
                    child = self.store.create(
                        phase_plan,
                        status="queued",
                        prompt=phase_prompt,
                    )
                    child_run_ids.append(str(child["runId"]))
                    self.store.update(
                        qualification,
                        run_id,
                        childRunIds=list(child_run_ids),
                    )
                    try:
                        self._run_human(
                            qualification,
                            child["runId"],
                            self.store.prompt(qualification, child["runId"]),
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
                    except Exception as exc:  # noqa: BLE001
                        child = self.store.get(qualification, str(child["runId"]))
                        reason = f"{phase['label']}で停止: {exc}"
                        self._block_remaining_queue(qualification, run_id, reason)
                        self._update_flow_phase(
                            qualification,
                            run_id,
                            current_phase_id,
                            status="failed",
                            childRunIds=[child["runId"]],
                            threadId=child.get("threadId"),
                            sessionId=child.get("sessionId"),
                            turnId=child.get("turnId"),
                            model=child.get("model"),
                            serviceTier=child.get("serviceTier"),
                            reasoningEffort=child.get("reasoningEffort"),
                            finishedAt=_now(),
                            error=reason,
                        )
                        emit(reason)
                        raise QualificationRunError(reason) from exc
                    receipt = child.get("workVersionReceipt")
                    if isinstance(receipt, Mapping):
                        work_version_receipts.append(dict(receipt))
                        if int(receipt.get("recordedCount") or 0):
                            confirmed_group_ids.update(
                                str(value)
                                for value in child.get("targetGroupIds") or []
                                if value
                            )
                            self.store.update(
                                qualification,
                                run_id,
                                confirmedGroupIds=sorted(confirmed_group_ids),
                            )
                    self._update_flow_phase(
                        qualification,
                        run_id,
                        current_phase_id,
                        status="succeeded",
                        childRunIds=[child["runId"]],
                        threadId=child.get("threadId"),
                        sessionId=child.get("sessionId"),
                        turnId=child.get("turnId"),
                        model=child.get("model"),
                        serviceTier=child.get("serviceTier"),
                        reasoningEffort=child.get("reasoningEffort"),
                        receiptValidated=True,
                        workVersionReceipt=receipt,
                        finishedAt=_now(),
                        error=None,
                    )
                    continue

                stage_id = str(phase_plan["stageId"])
                self._refresh_queued_stage_inputs(
                    qualification,
                    run_id,
                    phase_plan,
                    targets,
                    stage_id,
                )
                eligible_targets = [
                    target
                    for target in targets
                    if (
                        self._queue_stage(
                            self.store.get(qualification, run_id),
                            str(target.get("id") or ""),
                            stage_id,
                        )
                        or {}
                    ).get("status")
                    == "queued"
                ]
                emit(
                    f"{phase['label']}: {len(eligible_targets)}問を一問queueで開始します。"
                )
                if len(eligible_targets) > 1:
                    worker_limit = max(
                        1,
                        min(
                            int(parent.get("parallelWorkerLimit") or 1),
                            len(eligible_targets),
                        ),
                    )
                    emit(
                        f"{phase['label']}: 判断・修正案を最大{worker_limit}問並列で準備します。"
                    )
                    with ThreadPoolExecutor(max_workers=worker_limit) as executor:
                        futures = {
                            executor.submit(
                                self._prepare_question_item,
                                qualification,
                                run_id,
                                phase_prompt,
                                target,
                                stage_id,
                                emit,
                            ): str(target.get("id") or "")
                            for target in eligible_targets
                        }
                        for future in as_completed(futures):
                            question_id = futures[future]
                            try:
                                future.result()
                            except Exception as exc:  # noqa: BLE001
                                self.store.update_question_stage(
                                    qualification,
                                    run_id,
                                    question_id,
                                    stage_id,
                                    status="blocked",
                                    error=str(exc),
                                    finishedAt=_now(),
                                    block_dependents=True,
                                )
                else:
                    for target in eligible_targets:
                        self._prepare_question_item(
                            qualification,
                            run_id,
                            phase_prompt,
                            target,
                            stage_id,
                            emit,
                        )

                self.store.update(
                    qualification,
                    run_id,
                    executionPhase=f"committing:{current_phase_id}",
                )
                phase_child_ids: list[str] = []
                for target in eligible_targets:
                    question_id = str(target.get("id") or "")
                    queue_stage = self._queue_stage(
                        self.store.get(qualification, run_id), question_id, stage_id
                    )
                    if queue_stage is None or queue_stage.get("status") != "prepared":
                        continue
                    research_summary = ""
                    if queue_stage.get("preparationPath"):
                        try:
                            research_summary = str(
                                self.question_proposals.read(
                                    qualification,
                                    run_id,
                                    work_item_key=str(queue_stage["workItemKey"]),
                                    expected_hash=str(queue_stage["preparationHash"]),
                                    question_id=question_id,
                                    stage_id=stage_id,
                                    input_fingerprint=str(
                                        queue_stage["inputFingerprint"]
                                    ),
                                )["summary"]
                            )
                        except QuestionPatchProposalError as exc:
                            self.store.update_question_stage(
                                qualification,
                                run_id,
                                question_id,
                                stage_id,
                                status="blocked",
                                error=str(exc),
                                finishedAt=_now(),
                                block_dependents=True,
                            )
                            continue
                    child_plan = specialize_question_plan(phase_plan, question_id)
                    child_plan.update(
                        parentRunId=run_id,
                        flowPhaseId=current_phase_id,
                        phaseIndex=int(phase["index"]),
                        workType=f"maintenance_{current_phase_id}",
                        sandbox="workspace-write",
                        provider=self.app_server.provider,
                        parallelStrategy="prepared_question",
                        parallelWorkerLimit=1,
                        writeWorkerLimit=1,
                        parentSourceChecked=True,
                    )
                    writer_prompt = _maintenance_writer_prompt(
                        _question_scope_prompt(
                            phase_prompt,
                            target,
                            repo_root=self.repo_root,
                            read_only=False,
                        ),
                        research_summary,
                    )
                    child = self.store.create(
                        child_plan,
                        status="queued",
                        prompt=writer_prompt,
                    )
                    child_id = str(child["runId"])
                    child_run_ids.append(child_id)
                    phase_child_ids.append(child_id)
                    self.store.update(
                        qualification,
                        run_id,
                        childRunIds=list(child_run_ids),
                    )
                    self.store.update_question_stage(
                        qualification,
                        run_id,
                        question_id,
                        stage_id,
                        status="committing",
                        childRunIds=[
                            *list(queue_stage.get("childRunIds") or []),
                            child_id,
                        ],
                        error=None,
                    )
                    try:
                        self._run_human(
                            qualification,
                            child_id,
                            self.store.prompt(qualification, child_id),
                            str(child_plan["workType"]),
                            emit,
                            sync_artifacts=False,
                        )
                        child = self.store.refresh(qualification, child_id)
                        if child.get("status") != "succeeded" or not child.get(
                            "receiptValidated"
                        ):
                            raise QualificationRunError(
                                "一問の完了結果を検証できませんでした。"
                            )
                    except Exception as exc:  # noqa: BLE001
                        try:
                            child = self.store.refresh(qualification, child_id)
                        except Exception:  # noqa: BLE001
                            pass
                        reason = str((child or {}).get("error") or exc)
                        self.store.update_question_stage(
                            qualification,
                            run_id,
                            question_id,
                            stage_id,
                            status="blocked",
                            error=reason,
                            finishedAt=_now(),
                            block_dependents=True,
                        )
                        if not self._isolated_child_failure(child or {}):
                            unsafe_reason = (
                                f"{target.get('displayLabel') or question_id}: "
                                "失敗後のrollback完了を検証できないため、"
                                "後続writerと成果物同期を停止しました。"
                            )
                            self.store.update(
                                qualification,
                                run_id,
                                retrySafe=False,
                                retryUnsafeReason=unsafe_reason,
                                unsafeChildRunId=child_id,
                            )
                            self._block_remaining_queue(
                                qualification,
                                run_id,
                                unsafe_reason,
                            )
                            raise QualificationRunError(unsafe_reason) from exc
                        emit(
                            f"{target.get('displayLabel') or question_id}: "
                            f"この問題だけを保留しました: {reason}"
                        )
                        continue
                    receipt = child.get("workVersionReceipt")
                    if isinstance(receipt, Mapping):
                        work_version_receipts.append(dict(receipt))
                    output_fingerprint = _child_output_fingerprint(child)
                    self.store.update_question_stage(
                        qualification,
                        run_id,
                        question_id,
                        stage_id,
                        status="validated",
                        outputFingerprint=output_fingerprint,
                        finishedAt=_now(),
                        error=None,
                    )
                    if target.get("listGroupId"):
                        confirmed_group_ids.add(str(target["listGroupId"]))
                    self.store.update(
                        qualification,
                        run_id,
                        confirmedGroupIds=sorted(confirmed_group_ids),
                        threadId=child.get("threadId"),
                        sessionId=child.get("sessionId"),
                        turnId=child.get("turnId"),
                        model=child.get("model"),
                        serviceTier=child.get("serviceTier"),
                        reasoningEffort=child.get("reasoningEffort"),
                    )

                parent = self.store.get(qualification, run_id)
                target_stage_states = [
                    (
                        target,
                        self._queue_stage(
                            parent,
                            str(target.get("id") or ""),
                            stage_id,
                        ),
                    )
                    for target in targets
                ]
                validated_count = sum(
                    bool(stage and stage.get("status") == "validated")
                    for _target, stage in target_stage_states
                )
                blocked_question_ids = {
                    str(target.get("id") or "")
                    for target, stage in target_stage_states
                    if stage and stage.get("status") == "blocked"
                }
                validated_group_ids = list(
                    dict.fromkeys(
                        str(target.get("listGroupId") or "")
                        for target, stage in target_stage_states
                        if stage
                        and stage.get("status") == "validated"
                        and target.get("listGroupId")
                    )
                )
                merge_groups: list[dict[str, Any]] = []
                merge_failed_groups: list[str] = []
                if (
                    validated_count
                    and phase_plan.get("allowedPatchDirs")
                    and phase_index < len(phases) - 1
                ):
                    emit(
                        f"{phase['label']}: 確定済み{validated_count}問をまとめてmergeします。"
                    )
                    for list_group_id in validated_group_ids:
                        merge_result = self._refresh_merged_views_transaction(
                            qualification,
                            run_id,
                            list_group_id,
                            current_phase_id,
                            emit,
                        )
                        merge_groups.append(merge_result)
                        if str(merge_result.get("status") or "failed") in {
                            "succeeded",
                            "current",
                        }:
                            continue
                        merge_failed_groups.append(list_group_id)
                        sync_blocked_group_ids.add(list_group_id)
                        reason = (
                            f"{list_group_id}: {phase['label']}後の工程間mergeを"
                            "完了できなかったため、この範囲の後続工程だけを保留しました。"
                        )
                        self._block_group_queue(
                            qualification,
                            run_id,
                            list_group_id,
                            reason,
                        )
                        emit(reason)
                merge_blocked_question_ids = {
                    str(target.get("id") or "")
                    for target in targets
                    if str(target.get("listGroupId") or "")
                    in merge_failed_groups
                }
                phase_blocked_count = len(
                    blocked_question_ids | merge_blocked_question_ids
                )
                phase_status = "partial" if phase_blocked_count else "succeeded"
                phase_runtime: dict[str, Any] = {}
                if phase_child_ids:
                    last_child = self.store.get(qualification, phase_child_ids[-1])
                    phase_runtime = {
                        "threadId": last_child.get("threadId"),
                        "sessionId": last_child.get("sessionId"),
                        "turnId": last_child.get("turnId"),
                        "model": last_child.get("model"),
                        "serviceTier": last_child.get("serviceTier"),
                        "reasoningEffort": last_child.get("reasoningEffort"),
                    }
                self._update_flow_phase(
                    qualification,
                    run_id,
                    current_phase_id,
                    status=phase_status,
                    childRunIds=phase_child_ids,
                    validatedCount=validated_count,
                    blockedCount=phase_blocked_count,
                    receiptValidated=validated_count > 0,
                    artifactSync={
                        "status": (
                            "failed"
                            if merge_failed_groups
                            else "succeeded"
                            if merge_groups
                            else "not_required"
                        ),
                        "groups": merge_groups,
                    },
                    **phase_runtime,
                    finishedAt=_now(),
                    error=(
                        f"{phase_blocked_count}問を理由付きで保留しました。"
                        if phase_blocked_count
                        else None
                    ),
                )

            current_phase_id = ""
            parent = self.store.get(qualification, run_id)
            execution_summary = queue_summary(parent.get("questionExecutions") or [])
            if execution_summary["pendingWorkItemCount"]:
                raise QualificationRunError(
                    "一問queueに未確定の工程が残っているため、"
                    "完了扱いにせず停止しました: "
                    f"{execution_summary['pendingWorkItemCount']}工程"
                )
            queue_status = (
                "partial" if execution_summary["blockedQuestionCount"] else "succeeded"
            )
            unique_work_version_receipts: list[dict[str, Any]] = []
            seen_work_version_receipts: set[str] = set()
            for receipt in work_version_receipts:
                encoded = json.dumps(
                    receipt,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if encoded in seen_work_version_receipts:
                    continue
                seen_work_version_receipts.add(encoded)
                unique_work_version_receipts.append(receipt)
            work_version_receipt = {
                "recordedCount": sum(
                    int(receipt.get("recordedCount") or 0)
                    for receipt in unique_work_version_receipts
                ),
                "items": unique_work_version_receipts,
            }
            has_confirmed_work = bool(
                execution_summary["validatedWorkItemCount"]
                or work_version_receipt["recordedCount"]
                or confirmed_group_ids
            )
            sync_group_ids = [
                str(value)
                for value in parent.get("targetGroupIds") or []
                if str(value) in confirmed_group_ids
                and str(value) not in sync_blocked_group_ids
            ]
            if has_confirmed_work and not confirmed_group_ids:
                sync_group_ids = [
                    str(value)
                    for value in parent.get("targetGroupIds") or []
                    if str(value) not in sync_blocked_group_ids
                ]
            parent = self.store.update(
                qualification,
                run_id,
                status="validating",
                queueStatus=queue_status,
                executionPhase="final_validation",
                currentPhaseId=None,
                receiptValidated=True,
                workVersionReceipt=work_version_receipt,
                confirmedGroupIds=sorted(confirmed_group_ids),
                questionExecutionSummary=execution_summary,
                blockedQuestionCount=execution_summary["blockedQuestionCount"],
                blockedWorkItemCount=execution_summary["blockedWorkItemCount"],
                validatedQuestionCount=execution_summary["validatedQuestionCount"],
                validatedWorkItemCount=execution_summary["validatedWorkItemCount"],
                artifactSync={"status": "running", "groups": []},
            )
            if has_confirmed_work:
                emit(
                    "一問queueの走査を完了しました。"
                    "確定済み変更をまとめて同期します。"
                )
                sync_groups = [
                    sync_after_patch_update(
                        self.synchronizer,
                        qualification,
                        str(list_group_id),
                        emit,
                    )
                    for list_group_id in sync_group_ids
                ]
                sync_groups.extend(
                    {
                        "listGroupId": list_group_id,
                        "status": "blocked",
                        "message": "工程間merge未完了のため自動同期を保留しました。",
                    }
                    for list_group_id in sorted(sync_blocked_group_ids)
                )
                artifact_sync = _artifact_sync_result(
                    sync_groups,
                    success_message="確定済みpatchを公開用データまで同期しました。",
                    incomplete_message=(
                        "公開用データの自動更新は完了できませんでした。"
                        "問題詳細又は管理機能から再生成できます。"
                    ),
                )
            else:
                emit(
                    "一問queueの走査を完了しました。"
                    "確定済み変更がないため成果物同期は省略します。"
                )
                artifact_sync = {
                    "status": "not_required",
                    "groups": [],
                    "message": "確定済みの変更がないため再生成は不要です。",
                }
            partial = queue_status == "partial"
            warning = partial or artifact_sync["status"] not in {
                "succeeded",
                "current",
                "not_required",
            }
            result_summary = (
                f"{execution_summary['validatedQuestionCount']}問を確定し、"
                f"{execution_summary['blockedQuestionCount']}問を理由付きで保留しました。"
                if partial
                else "一問queueの整備と最終検証を完了しました。"
            )
            result = {
                "status": "succeeded",
                "summary": result_summary,
                "commands": [
                    {
                        "command": "workflow: validate per-question child receipts",
                        "status": "pass",
                    }
                ],
                "changedFiles": [],
            }
            try:
                self.store.write_result(qualification, run_id, result)
            except Exception:  # noqa: BLE001
                completed = self.store.mark_validated_artifact_sync_incomplete(
                    qualification,
                    run_id,
                    artifact_status="failed",
                    message=(
                        "patchは検証済みですが、トップ整備の最終receiptを"
                        "保存できませんでした。公開用データは手動で"
                        "再生成できます。"
                    ),
                    result_if_missing=result,
                )
                self.store.update(
                    qualification,
                    run_id,
                    queueStatus=queue_status,
                    executionPhase="done",
                    currentPhaseId=None,
                    questionExecutionSummary=execution_summary,
                    workVersionReceipt=work_version_receipt,
                )
                failed_sync = completed["artifactSync"]
                return {
                    "qualification": qualification,
                    "runId": run_id,
                    "childRunIds": child_run_ids,
                    "queueStatus": queue_status,
                    "questionExecutionSummary": execution_summary,
                    "artifactSync": failed_sync,
                    "warning": True,
                    "message": " ".join(
                        (result_summary, str(failed_sync["message"]))
                    ),
                }
            self.store.update(
                qualification,
                run_id,
                status="succeeded",
                queueStatus=queue_status,
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
                "queueStatus": queue_status,
                "questionExecutionSummary": execution_summary,
                "artifactSync": artifact_sync,
                "warning": warning,
                "message": " ".join((result_summary, str(artifact_sync["message"]))),
            }
        except Exception as exc:  # noqa: BLE001
            if current_phase_id:
                self._update_flow_phase(
                    qualification,
                    run_id,
                    current_phase_id,
                    status="failed",
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
            current = self.store.get(qualification, run_id)
            execution_summary = queue_summary(current.get("questionExecutions") or [])
            self.store.update(
                qualification,
                run_id,
                status="failed",
                queueStatus=(
                    "partial" if execution_summary["validatedWorkItemCount"] else "failed"
                ),
                currentPhaseId=current_phase_id or None,
                receiptValidated=False,
                questionExecutionSummary=execution_summary,
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
        artifact_sync = {
            "status": "succeeded",
            "groups": [
                {"listGroupId": group_id, "status": "succeeded"}
                for group_id in completed
            ],
            "message": message,
        }
        self.store.update(
            qualification,
            run_id,
            status="succeeded",
            receiptValidated=True,
            completedGroupIds=list(completed),
            result={"message": message},
            artifactSync=artifact_sync,
        )
        return {
            "qualification": qualification,
            "runId": run_id,
            "completedGroupIds": completed,
            "artifactSync": artifact_sync,
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
        if run_id not in {
            str(value)
            for value in getattr(emit, "technical_run_ids", set())
            if value
        }:
            emit = self._technical_log_emitter(
                qualification,
                run_id,
                emit,
            )
        created_writable_dirs: list[Path] = []
        filesystem_changed_files: tuple[str, ...] = ()
        before_files: dict[Path, str] | None = None
        self.store.update(
            qualification,
            run_id,
            status="running",
            startedAt=_now(),
            heartbeatAt=_now(),
        )
        run_at_start = self.store.get(qualification, run_id)
        parent_run_id = str(run_at_start.get("parentRunId") or "")

        def heartbeat() -> None:
            heartbeat_at = _now()
            self.store.update(
                qualification,
                run_id,
                heartbeatAt=heartbeat_at,
            )
            if parent_run_id:
                self.store.update(
                    qualification,
                    parent_run_id,
                    heartbeatAt=heartbeat_at,
                )
            job_heartbeat = getattr(emit, "heartbeat", None)
            if callable(job_heartbeat):
                job_heartbeat()
        try:
            current_run = self.store.get(qualification, run_id)
            target_count = int(current_run.get("targetCount") or 0)
            if target_count > 1:
                emit(
                    f"問題の読み取りと根拠確認は最大{MAINTENANCE_RESEARCH_WORKERS}並列、"
                    "patch・進捗・receiptの保存は1担当で実行します。"
                )
            if not current_run.get("parentSourceChecked"):
                self._check_source_immutability(emit)
            writable_roots, created_writable_dirs = self._maintenance_writable_roots(
                qualification, run_id
            )
            scoped_transaction_roots = self._maintenance_transaction_roots(
                current_run,
                writable_roots,
            )
            transaction_roots = tuple(
                dict.fromkeys(
                    [
                        *scoped_transaction_roots,
                        *(
                            self.work_versions.path_for(
                                qualification, str(list_group_id)
                            )
                            for list_group_id in current_run.get(
                                "targetGroupIds"
                            )
                            or []
                        ),
                    ]
                )
            )
            baseline_path = self.store.write_baseline(
                qualification, run_id, transaction_roots
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
                            heartbeat=heartbeat,
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
                        heartbeat=heartbeat,
                        cwd=turn_workspace,
                        writable_roots=writable_roots,
                        completion_probe=completion_probe,
                    )
                    app_server_changed_files = self._repository_change_notifications(
                        result.changed_files,
                        transient_root=turn_workspace,
                    )
                    if receipt_completion_snapshot is not None:
                        validated_receipt = self._assert_receipt_completion_unchanged(
                            qualification,
                            run_id,
                            receipt_completion_snapshot,
                        )
                        # HTTPの進捗照会も同じmanifestへreceipt反映を行う。
                        # receipt本体のhashが検出時から不変なら、検出時に
                        # 正規化済みの内容をここで正本へ戻し、並行照会による
                        # manifest更新競合だけで成功を失わないようにする。
                        self.store.update(
                            qualification,
                            run_id,
                            status="validating",
                            receiptValidated=False,
                            receiptError=None,
                            result=validated_receipt,
                            resultReceiptHash=str(
                                receipt_completion_snapshot["resultReceiptHash"]
                            ),
                            error=None,
                            finishedAt=None,
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
            if isinstance(refreshed_result, Mapping) and (
                refreshed_result.get("status") == "failed"
            ):
                raise QualificationRunError(
                    self._failed_receipt_message(refreshed_result)
                )
            if (
                not isinstance(refreshed_result, Mapping)
                or refreshed_result.get("status") != "succeeded"
            ):
                raise QualificationRunError(
                    "Codex App Serverは完了しましたが、完了receiptが見つかりません。"
                )
            self._validate_changed_files(
                qualification,
                run_id,
                refreshed,
                app_server_changed_files,
                filesystem_changed_files,
            )
            self._validate_progress_receipt(qualification, run_id, refreshed)
            server_resolved_paths = sorted(
                {
                    str(value)
                    for value in refreshed.get("resolvableFailedDeltaPaths") or []
                }
            )
            normalized_result = {
                **dict(refreshed_result),
                "resolvedFailedDeltaPaths": server_resolved_paths,
            }
            self.store.write_result(
                qualification,
                run_id,
                normalized_result,
            )
            refreshed = self.store.refresh(qualification, run_id)
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
            self.store.discard_baseline_backups(qualification, run_id)
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
                artifact_sync = _artifact_sync_result(
                    sync_groups,
                    success_message="公開用データも最新patchへ同期しました。",
                    incomplete_message=(
                        "公開用データの自動更新は完了できませんでした。"
                        "問題詳細又は管理機能から再生成できます。"
                    ),
                )
                sync_status = str(artifact_sync["status"])
                sync_message = str(artifact_sync["message"])
                warning = sync_status != "succeeded"
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
            if not (refreshed.get("allowedPatchDirs") and sync_artifacts):
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
            if current.get("receiptValidated") is True:
                completed = self.store.mark_validated_artifact_sync_incomplete(
                    qualification,
                    run_id,
                    artifact_status="failed",
                    message=(
                        "patchは検証済みですが、公開用データの自動更新を"
                        "完了できませんでした。問題詳細又は管理機能から再生成できます。"
                    ),
                )
                artifact_sync = completed["artifactSync"]
                return {
                    "qualification": qualification,
                    "runId": run_id,
                    "artifactSync": artifact_sync,
                    "warning": True,
                    "message": artifact_sync["message"],
                }

            pre_rollback_files = filesystem_changed_files
            rollback = self.store.rollback_baseline(qualification, run_id)
            rollback_unknown = bool(
                rollback is not None
                and rollback.get("deltaUnknown") is True
            )
            if rollback is not None:
                emit(str(rollback.get("message") or ""))
                if rollback.get("status") == "failed":
                    error_to_raise = QualificationRunError(
                        f"{original_exc}; {rollback.get('message')}"
                    )
                allowed_roots = self._maintenance_root_candidates(
                    qualification,
                    run_id,
                    current,
                )
                outside_transaction = {
                    self._maintenance_relative_path(value).as_posix()
                    for value in pre_rollback_files
                    if not self._maintenance_path_allowed_for_run(
                        self._maintenance_relative_path(value),
                        allowed_roots,
                        current,
                    )
                }
                filesystem_changed_files = tuple(
                    sorted(
                        outside_transaction
                        | {
                            str(value)
                            for value in rollback.get(
                                "remainingChangedFiles"
                            )
                            or []
                        }
                    )
                )
            current_result = current.get("result")
            current_result = current_result if isinstance(current_result, Mapping) else {}
            preserve_failed_receipt = bool(
                current_result.get("status") == "failed"
                and not current.get("receiptError")
            )
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
                    "summary": (
                        str(current_result.get("summary") or "").strip()
                        if preserve_failed_receipt
                        else str(error_to_raise)
                    ),
                    "commands": list(current_result.get("commands") or []),
                    "changedFiles": changed_files,
                },
            )
            self.store.refresh(qualification, run_id)
            self.store.update(
                qualification,
                run_id,
                status="interrupted" if rollback_unknown else "failed",
                deltaUnknown=rollback_unknown,
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

    @staticmethod
    def _failed_receipt_message(receipt: Mapping[str, Any]) -> str:
        summary = str(receipt.get("summary") or "").strip()
        commands = receipt.get("commands")
        first_failed_command = (
            next(
                (
                    str(item.get("command") or "").strip()
                    for item in commands
                    if isinstance(item, Mapping) and item.get("status") == "fail"
                ),
                "",
            )
            if isinstance(commands, list)
            else ""
        )
        if first_failed_command:
            return f"{summary} 最初に失敗した検証: {first_failed_command}"
        return summary

    def _validate_progress_receipt(
        self,
        qualification: str,
        run_id: str,
        run: Mapping[str, Any],
    ) -> None:
        if not run.get("progressTargets"):
            return
        progress = self.store.progress(qualification, run_id)
        if int(progress.get("invalidEventCount") or 0):
            raise QualificationRunError(
                "問題別進捗に読み取れない記録があります。"
            )
        expected_work = int(run.get("workItemCount") or 0)
        processed_work = int(progress.get("processedWorkItemCount") or 0)
        expected_questions = int(run.get("targetCount") or 0)
        processed_questions = int(progress.get("processedQuestionCount") or 0)
        if (
            processed_work != expected_work
            or processed_questions != expected_questions
        ):
            raise QualificationRunError(
                "問題別進捗と実行契約が一致しません: "
                f"{processed_questions}/{expected_questions}問・"
                f"{processed_work}/{expected_work}工程"
            )

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
            selected = self._resolve_policy_questions(
                run,
                questions,
                stage_id,
                target_values,
            )
            if not selected:
                raise QualificationRunError(
                    f"工程バージョンの対象問題を解決できません: {stage_id}"
                )
            if stage_id in {"explanation", "law_audit"}:
                self._validate_explanation_quality(selected)
            if stage_id == "law_audit":
                self._validate_law_audit_quality(selected)
                self._validate_law_audit_sidecar_consistency(
                    qualification,
                    selected,
                )
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

    def _resolve_policy_questions(
        self,
        run: Mapping[str, Any],
        questions: list[Mapping[str, Any]],
        stage_id: str,
        target_values: set[str],
    ) -> list[Mapping[str, Any]]:
        progress_targets = run.get("progressTargets") or []
        target_bindings = run.get("targetRecordBindings") or []
        try:
            descriptor_resolver = RunTargetIdentityResolver.from_sources(
                ("progressTargets", progress_targets),
                ("targetRecordBindings", target_bindings),
            )
            question_resolver = RunTargetIdentityResolver.from_sources(
                ("inventory questions", questions)
            )
            selected: dict[str, Mapping[str, Any]] = {}
            for value in sorted(target_values):
                query: Any = value
                if descriptor_resolver.targets:
                    query = descriptor_resolver.resolve(value)
                question = question_resolver.resolve(query)
                selected[question_resolver.official_id(question)] = question
            return list(selected.values())
        except RunTargetIdentityError as exc:
            raise QualificationRunError(
                f"工程バージョンの対象問題を一意に解決できません: "
                f"{stage_id} / {exc}"
            ) from exc

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
            choices = projected.get("choiceTextList")
            require_verdict_prefix = not (
                isinstance(choices, list)
                and not choices
                and projected.get("questionType") in {"fill_in_blank", "free_text"}
            )
            errors.extend(
                f"{label} {issue}"
                for issue in explanation_style_issues(
                    explanations,
                    projected.get("correctChoiceText"),
                    choice_texts=choices,
                    require_verdict_prefix=require_verdict_prefix,
                )
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
            # Every law-audit issue code can come from the pre-sync
            # upload-ready snapshot.  Validate the projected patches here and
            # the sidecar immediately afterwards; otherwise a corrected patch
            # can never reach the artifact sync that clears the stale warning.
            projected = question.get("projected")
            facts = (
                projected.get("lawRevisionFacts")
                if isinstance(projected, Mapping)
                else None
            )
            if not isinstance(projected, Mapping):
                if question.get("isLawRelated") is not False:
                    errors.append(f"{label}: projectedを確認できません。")
                continue
            if projected.get("isLawRelated") is False:
                continue
            if not isinstance(facts, (Mapping, list)) or (
                isinstance(facts, list) and not facts
            ):
                errors.append(f"{label}: lawRevisionFactsを確認できません。")
                continue
            fact_items = list(facts) if isinstance(facts, list) else [facts]
            for fact_index, fact in enumerate(fact_items, start=1):
                fact_label = (
                    f"lawRevisionFacts[{fact_index}]"
                    if isinstance(facts, list)
                    else "lawRevisionFacts"
                )
                if not isinstance(fact, Mapping):
                    errors.append(f"{label}: {fact_label}を確認できません。")
                    continue
                if not str(fact.get("auditStatus") or "").strip():
                    errors.append(f"{label}: {fact_label}.auditStatusがありません。")
                summary = fact.get("evidenceSummary")
                if not isinstance(summary, Mapping) or not summary:
                    errors.append(
                        f"{label}: {fact_label}.evidenceSummaryがありません。"
                    )
            errors.extend(
                f"{label}: {issue['detail']}"
                for issue in law_revision_current_verdict_issues(
                    correct_choice_text=projected.get("correctChoiceText"),
                    law_revision_facts=facts,
                )
            )
            errors.extend(
                f"{label}: {issue}"
                for issue in law_evidence_utilization_issues(dict(projected))
            )
        if errors:
            raise QualificationRunError(
                "03b 現行法監査の必須メタデータ検証に失敗しました。"
                + " ".join(errors[:5])
                + (f" ほか{len(errors) - 5}件。" if len(errors) > 5 else "")
            )

    def _validate_law_audit_sidecar_consistency(
        self,
        qualification: str,
        questions: list[Mapping[str, Any]],
    ) -> None:
        errors: list[str] = []
        rows_by_group: dict[
            str, list[tuple[int, Mapping[str, Any], set[str]]]
        ] = {}

        def verified_law_bases(value: Any) -> set[tuple[str, str, str]]:
            bases: set[tuple[str, str, str]] = set()
            if isinstance(value, Mapping):
                if (
                    str(value.get("verificationStatus") or "").strip()
                    == "verified"
                    and str(value.get("lawTitle") or "").strip()
                    and str(value.get("lawId") or "").strip()
                    and str(value.get("article") or "").strip()
                ):
                    article = str(value["article"]).strip()
                    if article.startswith("第"):
                        article = article[1:]
                    if article.endswith("条"):
                        article = article[:-1]
                    bases.add(
                        (
                            str(value["lawTitle"]).strip(),
                            str(value["lawId"]).strip(),
                            article,
                        )
                    )
                for item in value.values():
                    bases.update(verified_law_bases(item))
            elif isinstance(value, list):
                for item in value:
                    bases.update(verified_law_bases(item))
            return bases

        def has_reference(value: Any) -> bool:
            if isinstance(value, Mapping):
                return bool(value)
            if isinstance(value, list):
                return any(has_reference(item) for item in value)
            return bool(value)

        for list_group_id in sorted(
            {
                str(question.get("listGroupId") or "").strip()
                for question in questions
            }
        ):
            if not list_group_id:
                errors.append("listGroupIdを確認できない対象問題があります。")
                continue
            relative = self._law_review_sidecar_path(
                qualification,
                list_group_id,
            )
            path = self.repo_root / relative
            if not path.is_file():
                errors.append(f"{relative}: 監査sidecarがありません。")
                continue
            rows: list[tuple[int, Mapping[str, Any], set[str]]] = []
            for line_number, raw_line in enumerate(
                path.read_text(encoding="utf-8").splitlines(),
                start=1,
            ):
                if not raw_line.strip():
                    continue
                try:
                    value = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    errors.append(
                        f"{relative}:{line_number}: JSONを読めません: {exc.msg}。"
                    )
                    continue
                if not isinstance(value, Mapping):
                    errors.append(
                        f"{relative}:{line_number}: 監査行がobjectではありません。"
                    )
                    continue
                rows.append(
                    (line_number, value, record_identity_aliases(value))
                )
            rows_by_group[list_group_id] = rows

        used_rows: dict[tuple[str, int], str] = {}
        for question in questions:
            list_group_id = str(question.get("listGroupId") or "").strip()
            label = str(
                question.get("questionLabel")
                or question.get("originalQuestionId")
                or question.get("id")
                or "対象問題"
            )
            aliases = self._work_version_aliases(question)
            expected_review_id = str(
                question.get("originalQuestionId") or ""
            ).strip()
            expected_source_key = str(
                question.get("sourceQuestionKey") or ""
            ).strip()
            expected_source_ref = str(
                question.get("sourceRecordRef") or ""
            ).strip()
            expected_binding = SourceIdentityBinding.from_values(
                expected_source_key,
                expected_review_id,
                expected_source_ref,
            )
            matches = [
                (line_number, row)
                for line_number, row, row_aliases in rows_by_group.get(
                    list_group_id,
                    [],
                )
                if (
                    (
                        row.get("schemaVersion") == "law-revision-audit/v2"
                        and SourceIdentityBinding.from_mapping(row)
                        == expected_binding
                    )
                    or (
                        row.get("schemaVersion") != "law-revision-audit/v2"
                        and bool(aliases & row_aliases)
                    )
                )
            ]
            if len(matches) != 1:
                errors.append(
                    f"{label}: 監査sidecarの対応行が{len(matches)}件です。"
                )
                continue
            line_number, row = matches[0]
            row_key = (list_group_id, line_number)
            if row_key in used_rows:
                errors.append(
                    f"{label}: 監査sidecar行が{used_rows[row_key]}と重複対応しています。"
                )
                continue
            used_rows[row_key] = label

            if row.get("schemaVersion") != "law-revision-audit/v2":
                errors.append(
                    f"{label}: 監査sidecar.schemaVersionがv2ではありません。"
                )
            projected = question.get("projected")
            source = question.get("source")
            projected_record = (
                projected if isinstance(projected, Mapping) else {}
            )
            source_record = source if isinstance(source, Mapping) else {}
            choice_lengths = [
                len(value)
                for value in (
                    source_record.get("choiceTextList"),
                    source_record.get("correctChoiceText"),
                    projected_record.get("choiceTextList"),
                    projected_record.get("correctChoiceText"),
                )
                if isinstance(value, list)
            ]
            errors.extend(
                f"{label}: 監査sidecar.{issue}"
                for issue in law_audit_sidecar_metadata_errors(
                    dict(row),
                    expected_choice_count=max(choice_lengths, default=0)
                    or None,
                    expected_qualification=qualification,
                    expected_list_group_id=list_group_id,
                )
            )
            if (
                not expected_review_id
                or str(row.get("reviewQuestionId") or "").strip()
                != expected_review_id
            ):
                errors.append(
                    f"{label}: 監査sidecar.reviewQuestionIdがsource由来IDと一致しません。"
                )
            if (
                not expected_source_key
                or str(row.get("sourceQuestionKey") or "").strip()
                != expected_source_key
            ):
                errors.append(
                    f"{label}: 監査sidecar.sourceQuestionKeyが一致しません。"
                )
            if (
                not expected_source_ref
                or str(row.get("sourceRecordRef") or "").strip()
                != expected_source_ref
            ):
                errors.append(
                    f"{label}: 監査sidecar.sourceRecordRefが一致しません。"
                )

            if row.get("qualification") != qualification:
                errors.append(
                    f"{label}: 監査sidecar.qualificationが一致しません。"
                )
            if str(row.get("listGroupId") or "") != list_group_id:
                errors.append(
                    f"{label}: 監査sidecar.listGroupIdが一致しません。"
                )

            projected_law = (
                projected.get("isLawRelated")
                if isinstance(projected, Mapping)
                else None
            )
            if not isinstance(projected_law, bool):
                errors.append(
                    f"{label}: projected.isLawRelatedをboolで確認できません。"
                )
                continue
            if question.get("isLawRelated") is not projected_law:
                errors.append(
                    f"{label}: inventoryとprojectedのisLawRelatedが一致しません。"
                )
            sidecar_law = row.get("isLawRelated")
            if not isinstance(sidecar_law, bool):
                errors.append(
                    f"{label}: 監査sidecar.isLawRelatedがboolではありません。"
                )
                continue
            if sidecar_law != projected_law:
                errors.append(
                    f"{label}: projectedと監査sidecarのisLawRelatedが一致しません。"
                )
                continue

            audit_status = str(row.get("auditStatus") or "").strip()
            review_state = str(row.get("reviewState") or "").strip()
            source_summary_value = row.get("sourceSummary")
            source_summary = (
                source_summary_value.strip()
                if isinstance(source_summary_value, str)
                else ""
            )
            facts = projected.get("lawRevisionFacts")
            if facts is None:
                fact_items: list[Any] = []
            elif isinstance(facts, Mapping):
                fact_items = [facts]
            elif isinstance(facts, list):
                fact_items = list(facts)
            else:
                fact_items = []
                errors.append(
                    f"{label}: projected lawRevisionFactsの型が不正です。"
                )
            if not source_summary:
                errors.append(
                    f"{label}: 監査sidecar.sourceSummaryがありません。"
                )

            if not projected_law:
                if projected.get("lawGroundedExplanationNotNeeded") is not True:
                    errors.append(
                        f"{label}: 非法令問題の"
                        "lawGroundedExplanationNotNeededがtrueではありません。"
                    )
                if has_reference(projected.get("lawReferences")):
                    errors.append(
                        f"{label}: 非法令問題のprojected lawReferencesが空ではありません。"
                    )
                if has_reference(row.get("lawReferences")):
                    errors.append(
                        f"{label}: 非法令問題の監査sidecar lawReferencesが空ではありません。"
                    )
                if (
                    audit_status != "not_law_related"
                    or review_state != "secondary_verified"
                ):
                    errors.append(
                        f"{label}: 非法令問題の監査sidecarは"
                        "not_law_related/secondary_verifiedではありません。"
                    )
                if any(
                    not isinstance(fact, Mapping)
                    or str(fact.get("auditStatus") or "").strip()
                    != "not_law_related"
                    or str(fact.get("reviewState") or "").strip()
                    != "secondary_verified"
                    for fact in fact_items
                ):
                    errors.append(
                        f"{label}: 非法令問題のprojected lawRevisionFactsが"
                        "not_law_related/secondary_verifiedではありません。"
                    )
                continue

            if projected.get("lawGroundedExplanationNotNeeded") is not False:
                errors.append(
                    f"{label}: 法令問題の"
                    "lawGroundedExplanationNotNeededがfalseではありません。"
                )
            allowed_final_states = {
                ("same_as_current", "secondary_verified"),
                ("same_as_current", "tertiary_verified"),
                ("updated_to_current_law", "tertiary_verified"),
            }
            if (audit_status, review_state) not in allowed_final_states:
                errors.append(
                    f"{label}: 法令問題の監査sidecarが公開確定状態ではありません。"
                )
            projected_states = {
                (
                    str(fact.get("auditStatus") or "").strip(),
                    str(fact.get("reviewState") or "").strip(),
                )
                for fact in fact_items
                if isinstance(fact, Mapping)
            }
            if not fact_items or any(
                not isinstance(fact, Mapping) for fact in fact_items
            ) or any(state not in allowed_final_states for state in projected_states):
                errors.append(
                    f"{label}: projected lawRevisionFactsが公開確定状態ではありません。"
                )
            expected_audit_status = (
                "updated_to_current_law"
                if any(
                    state[0] == "updated_to_current_law"
                    for state in projected_states
                )
                else "same_as_current"
            )
            if audit_status != expected_audit_status:
                errors.append(
                    f"{label}: projected lawRevisionFactsと監査sidecarの"
                    "auditStatusが一致しません。"
                )
            projected_bases = verified_law_bases(projected.get("lawReferences"))
            sidecar_bases = verified_law_bases(row.get("lawReferences"))
            if not projected_bases:
                errors.append(
                    f"{label}: projected lawReferencesにverifiedの"
                    "lawTitle・lawId・articleがありません。"
                )
            if not sidecar_bases:
                errors.append(
                    f"{label}: 監査sidecarにverifiedの"
                    "lawTitle・lawId・articleがありません。"
                )
            if projected_bases and sidecar_bases and not (
                projected_bases & sidecar_bases
            ):
                errors.append(
                    f"{label}: projectedと監査sidecarのverified法令根拠が"
                    "一致しません。"
                )

        if errors:
            raise QualificationRunError(
                "03b 現行法監査のsidecar整合検証に失敗しました。"
                + " ".join(errors[:5])
                + (f" ほか{len(errors) - 5}件。" if len(errors) > 5 else "")
            )

    @staticmethod
    def _work_version_aliases(question: Mapping[str, Any]) -> set[str]:
        return target_identity_aliases(question)

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

    def _maintenance_transaction_roots(
        self,
        run: Mapping[str, Any],
        writable_roots: tuple[Path, ...],
    ) -> tuple[Path, ...]:
        """Prefer exact allowlisted files over whole writable directories."""

        exact_paths = {
            (self.repo_root / self._maintenance_relative_path(value)).resolve()
            for value in [
                *(run.get("allowedPatchFiles") or []),
                *(run.get("allowedWriteFiles") or []),
            ]
        }
        selected: set[Path] = set()
        covered: set[Path] = set()
        for root in (path.resolve() for path in writable_roots):
            scoped = {
                path
                for path in exact_paths
                if path == root or path.is_relative_to(root)
            }
            if scoped:
                selected.update(scoped)
                covered.update(scoped)
            else:
                selected.add(root)
        uncovered = exact_paths - covered
        if uncovered:
            raise QualificationRunError(
                "書込transactionのexact fileがwritable root外です: "
                + ", ".join(
                    path.relative_to(self.repo_root).as_posix()
                    for path in sorted(uncovered)
                )
            )
        return tuple(sorted(selected))

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
        if resolved_failed:
            raise QualificationRunError(
                "未確定差分の解決記録はserverが確定するため、完了receiptへ指定できません。"
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
        target_bindings: list[dict[str, Any]] = []
        for value in run.get("targetRecordBindings") or []:
            if not isinstance(value, Mapping):
                continue
            source_binding = SourceIdentityBinding.from_mapping(value)
            target_bindings.append(
                {
                    "uiQuestionId": str(value.get("uiQuestionId") or ""),
                    **source_binding.as_mapping(),
                    "aliases": {
                        str(alias)
                        for alias in [
                            *(value.get("aliases") or []),
                            value.get("uiQuestionId"),
                            *source_binding.as_tuple(),
                        ]
                        if alias
                    },
                }
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

        def source_aliases(entry: Mapping[str, Any]) -> set[str]:
            value = entry.get("sourceAliases")
            return (
                {str(alias) for alias in value or []}
                if isinstance(value, list)
                else aliases(entry)
            )

        def workflow_aliases(entry: Mapping[str, Any]) -> set[str]:
            value = entry.get("workflowAliases")
            return (
                {str(alias) for alias in value or []}
                if isinstance(value, list)
                else set()
            )

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

        def contract(entry: Mapping[str, Any]) -> dict[str, Any]:
            value = entry.get("contractFields")
            return dict(value) if isinstance(value, Mapping) else {}

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

        def strongest_matches(
            entries: list[Any],
            entry_aliases: set[str],
            source_ref: str = "",
        ) -> list[Mapping[str, Any]]:
            candidates = matching(entries, entry_aliases)
            if source_ref:
                exact = [
                    entry
                    for entry in candidates
                    if SourceIdentityBinding.from_mapping(
                        identity(entry)
                    ).source_record_ref
                    == source_ref
                ]
                # A supplied sourceRecordRef is an exact scope boundary.  A
                # shared legacy alias must not fall back to another record.
                return exact
            scores = [
                (len(aliases(entry) & entry_aliases), entry)
                for entry in candidates
            ]
            best_score = max((score for score, _entry in scores), default=0)
            return [entry for score, entry in scores if score == best_score]

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
            is_law_audit_sidecar = (
                relative.parts[:4]
                == ("output", qualification, "review", "law_revision_audit")
                and relative.suffix.lower() == ".jsonl"
            )
            file_scoped_bindings = [
                binding
                for binding in target_bindings
                if (
                    not binding.get("sourceRecordRef")
                    or binding["sourceRecordRef"] in file_target_aliases
                )
            ]

            for after_entry in after:
                if not isinstance(after_entry, Mapping):
                    raise QualificationRunError("record baselineの形式が不正です。")
                entry_aliases = aliases(after_entry)
                after_identity = identity(after_entry)
                entry_source_binding = SourceIdentityBinding.from_mapping(
                    after_identity
                )
                if entry_source_binding.is_complete():
                    matching_bindings = [
                        binding
                        for binding in file_scoped_bindings
                        if SourceIdentityBinding.from_mapping(binding)
                        == entry_source_binding
                    ]
                    matching_target_groups = [
                        group
                        for group in file_target_alias_groups
                        if matching_bindings
                        if all(
                            value in group
                            for value in entry_source_binding.as_tuple()
                        )
                    ]
                else:
                    if entry_source_binding.source_record_ref:
                        matching_bindings = [
                            binding
                            for binding in file_scoped_bindings
                            if SourceIdentityBinding.from_mapping(
                                binding
                            ).source_record_ref
                            == entry_source_binding.source_record_ref
                        ]
                    else:
                        binding_scores = [
                            (len(entry_aliases & set(binding["aliases"])), binding)
                            for binding in file_scoped_bindings
                            if entry_aliases & set(binding["aliases"])
                        ]
                        best_score = max(
                            (score for score, _binding in binding_scores),
                            default=0,
                        )
                        matching_bindings = [
                            binding
                            for score, binding in binding_scores
                            if score == best_score
                        ]
                    if len(matching_bindings) == 1 and matching_bindings[0].get(
                        "sourceRecordRef"
                    ):
                        matching_target_groups = [
                            group
                            for group in file_target_alias_groups
                            if matching_bindings[0]["sourceRecordRef"] in group
                        ]
                    else:
                        group_scores = [
                            (len(entry_aliases & group), group)
                            for group in file_target_alias_groups
                            if entry_aliases & group
                        ]
                        best_score = max(
                            (score for score, _group in group_scores),
                            default=0,
                        )
                        matching_target_groups = [
                            group
                            for score, group in group_scores
                            if score == best_score
                        ]
                if (
                    entry_source_binding.is_complete()
                    and not matching_bindings
                    and not matching_target_groups
                ):
                    # A complete binding that points at another source record
                    # is non-target even when its two legacy IDs are shared.
                    continue
                if len(matching_target_groups) > 1:
                    raise QualificationRunError(
                        f"recordが複数の対象問題IDに一致します: {relative}"
                    )
                matched_target_group = (
                    matching_target_groups[0]
                    if len(matching_target_groups) == 1
                    else set()
                )
                if len(matching_bindings) > 1:
                    raise QualificationRunError(
                        f"recordが複数のID bindingに一致します: {relative}"
                    )
                matched_binding = (
                    matching_bindings[0]
                    if len(matching_bindings) == 1
                    else None
                )
                matched_source_binding = (
                    SourceIdentityBinding.from_mapping(matched_binding)
                    if matched_binding is not None
                    else None
                )
                binding_aliases = (
                    set(matched_binding["aliases"])
                    if matched_binding is not None
                    else matched_target_group
                )
                before_matches = strongest_matches(
                    before,
                    binding_aliases or entry_aliases,
                    (
                        matched_source_binding.source_record_ref
                        if matched_source_binding is not None
                        else ""
                    ),
                )
                if (
                    is_law_audit_sidecar
                    and not before_matches
                    and matched_source_binding is not None
                ):
                    legacy_before_matches = strongest_matches(
                        before,
                        binding_aliases or entry_aliases,
                    )
                    if len(legacy_before_matches) == 1:
                        before_matches = legacy_before_matches
                if matched_source_binding is not None:
                    source_matches = [
                        entry
                        for entry in source_entries
                        if str(
                            identity(entry).get("sourceRecordRef") or ""
                        )
                        == matched_source_binding.source_record_ref
                        and str(
                            identity(entry).get("sourceQuestionKey") or ""
                        )
                        == matched_source_binding.source_question_key
                        and matched_source_binding.review_question_id
                        in source_aliases(entry)
                    ]
                else:
                    source_matches = matching(
                        source_entries,
                        binding_aliases or entry_aliases,
                    )
                before_fields = unambiguous_protected(
                    before_matches, relative
                )
                source_fields = unambiguous_protected(
                    source_matches, relative
                )
                after_fields = protected(after_entry)
                record_changed = not any(
                    str(entry.get("hash") or "")
                    == str(after_entry.get("hash") or "")
                    for entry in before_matches
                )
                if (
                    not is_law_audit_sidecar
                    and record_changed
                    and matched_source_binding is not None
                    and entry_source_binding.source_record_ref
                    != matched_source_binding.source_record_ref
                ):
                    raise QualificationRunError(
                        f"更新patch rowにsourceRecordRefがありません: {relative}"
                    )
                before_identity = unambiguous_identity(
                    before_matches, relative
                )
                before_schema_versions = {
                    str(contract(entry).get("schemaVersion") or "")
                    for entry in before_matches
                }
                if before_identity is not None:
                    if after_identity != before_identity:
                        allowed_patch_identity_enrichment = bool(
                            not is_law_audit_sidecar
                            and matched_source_binding is not None
                            and entry_source_binding.source_record_ref
                            == matched_source_binding.source_record_ref
                            and (
                                not entry_source_binding.source_question_key
                                or entry_source_binding.source_question_key
                                == matched_source_binding.source_question_key
                            )
                            and all(
                                after_identity.get(field) == value
                                for field, value in before_identity.items()
                            )
                            and set(after_identity) - set(before_identity)
                            <= {"sourceQuestionKey", "sourceRecordRef"}
                        )
                        allowed_sidecar_migration = bool(
                            is_law_audit_sidecar
                            and matched_source_binding is not None
                            and before_schema_versions
                            == {"law-revision-audit/v1"}
                            and contract(after_entry).get("schemaVersion")
                            == "law-revision-audit/v2"
                            and after_identity
                            == matched_source_binding.as_mapping()
                            and matched_source_binding.is_complete()
                        )
                        if not (
                            allowed_patch_identity_enrichment
                            or allowed_sidecar_migration
                        ):
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
                    source_bound_aliases = {
                        alias
                        for entry in source_matches
                        for alias in source_aliases(entry)
                    }
                    if (
                        len(matching_target_groups) != 1
                        or not source_matches
                        or not entry_aliases.issubset(matched_target_group)
                        or (
                            not is_law_audit_sidecar
                            and (
                                not source_aliases(after_entry).issubset(
                                    source_bound_aliases
                                )
                                or not workflow_aliases(after_entry).issubset(
                                    source_bound_aliases
                                )
                            )
                        )
                    ):
                        raise QualificationRunError(
                            f"sourceと異なるID fieldを検出しました: {relative}"
                        )
                if is_law_audit_sidecar:
                    expected_binding = (
                        matched_source_binding
                        if matched_source_binding is not None
                        else SourceIdentityBinding.from_values("", "", "")
                    )
                    if (
                        not expected_binding.is_complete()
                        or after_identity != expected_binding.as_mapping()
                    ):
                        raise QualificationRunError(
                            f"監査sidecarのsource ID bindingが一致しません: {relative}"
                        )
                    if contract(after_entry).get("schemaVersion") != (
                        "law-revision-audit/v2"
                    ):
                        raise QualificationRunError(
                            f"監査sidecarのschemaVersionがv2ではありません: {relative}"
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
                source_refs = {
                    SourceIdentityBinding.from_mapping(binding).source_record_ref
                    for binding in target_bindings
                    if SourceIdentityBinding.from_mapping(
                        binding
                    ).source_record_ref
                    in group
                }
                return sum(
                    1
                    for entry in strongest_matches(entries, group)
                    if not source_refs
                    or not SourceIdentityBinding.from_mapping(
                        identity(entry)
                    ).source_record_ref
                    or SourceIdentityBinding.from_mapping(
                        identity(entry)
                    ).source_record_ref
                    in source_refs
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
                    entry_binding = SourceIdentityBinding.from_mapping(
                        identity(entry)
                    )
                    if entry_binding.is_complete():
                        is_target = any(
                            SourceIdentityBinding.from_mapping(binding)
                            == entry_binding
                            for binding in file_scoped_bindings
                        )
                    else:
                        is_target = bool(
                            set(entry_aliases) & file_target_aliases
                        )
                    if is_target:
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
            "result": copy.deepcopy(dict(result)),
            "resultReceiptHash": hashlib.sha256(
                receipt_path.read_bytes()
            ).hexdigest(),
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
    ) -> dict[str, Any]:
        result = snapshot.get("result")
        if not isinstance(result, Mapping) or result.get("status") != "succeeded":
            raise QualificationRunError("成功receipt時点の内容がありません。")
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
        receipt_path = self.store.result_path(qualification, run_id)
        raw = receipt_path.read_bytes()
        expected_hash = str(snapshot.get("resultReceiptHash") or "")
        if not expected_hash or not hmac.compare_digest(
            hashlib.sha256(raw).hexdigest(), expected_hash
        ):
            raise QualificationRunError(
                "成功receiptの保存後にresult.jsonの変更を検出しました。"
            )
        try:
            current = self.store._validated_result_receipt(
                json.loads(raw.decode("utf-8"))
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            QualificationRunError,
        ) as exc:
            raise QualificationRunError(
                "成功receiptの検出後にresult.jsonが変更されました。"
            ) from exc
        normalized = copy.deepcopy(dict(result))
        if current != normalized:
            raise QualificationRunError(
                "成功receiptの検出後にresult.jsonが変更されました。"
            )
        return normalized

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
        if plan["kind"] == "human":
            plan["resolvableFailedDeltaPaths"] = self._resolvable_for_plan(
                qualification,
                list(plan.get("targetGroupIds") or []),
                plan,
            )
            if not resumed_from:
                return plan
            previous = self.store.get(qualification, resumed_from)
            previous_scope = list(previous.get("scopeListGroupIds") or [])
            if (
                previous.get("kind") != "orchestration"
                or list(previous.get("stageIds") or []) != selected_stage_ids
                or str(previous.get("mode") or "") != mode
                or previous_scope != list(plan.get("scopeListGroupIds") or [])
            ):
                raise QualificationRunError(
                    "再開元と工程、実行方式又は対象範囲が一致しません。"
                )
            self._assert_resume_safe(qualification, previous)
            previous_executions = previous.get("questionExecutions")
            if not isinstance(previous_executions, list):
                raise QualificationRunError("再開元に一問queueの記録がありません。")
            try:
                plan = resume_plan(plan, previous_executions)
            except QuestionWorkQueueError as exc:
                raise QualificationRunError(str(exc)) from exc
            plan["resumeMergeDependencies"] = _derive_resume_merge_dependencies(
                previous,
                plan,
            )
            plan["confirmedGroupIds"] = sorted(
                {
                    str(value)
                    for value in previous.get("confirmedGroupIds") or []
                    if value
                }
            )
            if isinstance(previous.get("workVersionReceipt"), Mapping):
                plan["workVersionReceipt"] = copy.deepcopy(
                    previous["workVersionReceipt"]
                )
            plan["resumeWorkItemKeys"] = sorted(
                {
                    str(stage.get("workItemKey") or "")
                    for question in build_question_executions(plan)
                    for stage in question.get("stages") or []
                    if isinstance(stage, Mapping) and stage.get("workItemKey")
                }
            )
            self._apply_plan_write_contract(plan)
            plan["resolvableFailedDeltaPaths"] = self._resolvable_for_plan(
                qualification,
                list(plan.get("targetGroupIds") or []),
                plan,
            )
            return plan
        if not resumed_from:
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

    def _assert_resume_safe(
        self,
        qualification: str,
        previous: Mapping[str, Any],
    ) -> None:
        if previous.get("retrySafe") is False:
            raise QualificationRunError(
                str(previous.get("retryUnsafeReason") or "").strip()
                or "未確定差分の安全を確認できないため、この作業は再開できません。"
            )
        for child_run_id in previous.get("childRunIds") or []:
            try:
                child = self.store.get(qualification, str(child_run_id))
            except Exception as exc:  # noqa: BLE001
                reason = "子作業の安全状態を確認できないため、この作業は再開できません。"
                self.store.update(
                    qualification,
                    str(previous["runId"]),
                    retrySafe=False,
                    retryUnsafeReason=reason,
                    unsafeChildRunId=str(child_run_id),
                )
                raise QualificationRunError(reason) from exc
            if _child_retry_safe(child):
                continue
            reason = (
                "失敗した子作業のrollback又は残存差分を確認できないため、"
                "手動で差分を解消するまで再開できません。"
            )
            self.store.update(
                qualification,
                str(previous["runId"]),
                retrySafe=False,
                retryUnsafeReason=reason,
                unsafeChildRunId=str(child_run_id),
            )
            raise QualificationRunError(reason)

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
