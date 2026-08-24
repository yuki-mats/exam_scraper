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
import time
import weakref
from collections import OrderedDict, deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from scripts.merge.merge_utils import (
    select_latest_patch_files,
    source_stem_from_patch_filename,
)
from scripts.common.question_identity import (
    SourceIdentityBinding,
    question_id_from_source_unique_key,
    source_question_key,
    source_record_ref,
)
from scripts.common.question_answer_contract import (
    all_correct_choice_sentinel_number,
    asks_for_combination_choice,
    asks_for_selected_choice_count,
    uses_official_firestore_statement_answers,
    uses_trusted_gassyunin_judge_answers,
)
from scripts.common.repaso_firestore_schema import is_law_revision_facts_shape
from scripts.common.law_audit_sidecar_contract import (
    law_audit_sidecar_metadata_errors,
)
from scripts.common.suggested_question_contract import (
    public_choice_indexes,
    validation_errors as suggested_question_validation_errors,
)
from tools.question_review_console.projection import (
    PROJECTED_COMPARE_FIELDS,
    extract_records,
    record_identity_aliases,
    sha256_json,
    source_identity_aliases,
    workflow_identity_aliases,
)
from tools.question_review_console.process_lease import (
    ProcessLeaseError,
    qualification_run_lease,
)
from scripts.common.aggregate_answer_decomposition import (
    candidate_set_hash,
    derived_source_unique_keys_for_parent,
    extract_source_statements,
    generate_statement_candidates,
    is_approved_target,
    materialize_decomposition,
    reconcile_reviews,
    stable_parent_identity,
    source_text_hash,
)
from tools.question_review_console.jobs import (
    REPOSITORY_OPERATION_KEY,
    JobConflictError,
    JobManager,
    normalize_log_event,
    qualification_operation_key,
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
from tools.question_review_console.law_audit_sidecar_normalizer import (
    normalize_law_audit_sidecars,
)
from tools.question_review_console.primary_law_evidence import (
    PrimaryLawEvidenceResolver,
)
from tools.question_review_console.codex_app_server import (
    MAINTENANCE_RESEARCH_WORKERS,
    QUESTION_MAINTENANCE_MODEL,
    QUESTION_MAINTENANCE_RETRY_MODEL,
    STANDARD_SPEED_MODE,
    TURN_REASONING_EFFORT,
    CodexAppServerError,
    CodexControlRequestTimeoutError,
    CodexTerminalTurnFailedError,
    CodexTurnTimeoutError,
    SubscriptionGateError,
    normalize_speed_mode,
)
from tools.question_review_console.qualification_workflow import (
    LAW_WORKFLOW_STAGE_IDS,
    LAW_WORKFLOW_UPDATE_TARGET_IDS,
    QualificationWorkflow,
)
from tools.question_review_console.qualification_progress import (
    derive_progress_completion,
)
from tools.question_review_console.question_patch_proposal import (
    CanonicalPatchCommitError,
    IsolatedQuestionPatchWorkspace,
    TargetResolutionCache,
    assert_target_resolvable,
)
from tools.question_review_console.question_candidate import (
    CandidateUpdate,
    CandidateTarget,
    QuestionCandidate,
    QuestionCandidateError,
    candidate_targets,
    output_schema as candidate_output_schema,
    parse_model_candidate_v3,
    parse_prepared_candidate_payload,
    validate_candidate_content,
    aggregate_answer_review_schema,
    parse_aggregate_answer_reviews,
)
from tools.question_review_console.adaptive_scheduler import (
    DEFAULT_MAX_PARALLEL_TURNS,
    DEFAULT_MAX_QUESTIONS_PER_TURN,
    AdaptiveLimits,
    scheduler_status,
)
from tools.question_review_console.review_store import atomic_write
from tools.question_review_console.question_work_queue import (
    WORK_ITEM_STATES,
    QuestionPlanIndex,
    QuestionWorkQueueError,
    build_question_plan_index,
    build_question_executions,
    input_fingerprint,
    queue_summary,
    refresh_question_status,
    resume_plan,
    specialize_question_plan,
    subset_question_plan,
    work_item_key,
)
from tools.question_review_console.question_run_state import (
    PLAN_OWNED_FIELDS,
    RUN_SCHEMA_VERSION as QUESTION_RUN_SCHEMA_VERSION,
    QuestionRunStateError,
    QuestionRunStateStore,
    question_state_filename,
    update_active_attempt_from_execution,
    validated_receipt_key,
)
from tools.question_review_console.run_target_identity import (
    RunTargetIdentityError,
    RunTargetIdentityResolver,
    resolve_policy_target_ids,
    target_identity_aliases,
)
from tools.question_review_console.validation_feedback import (
    build_child_feedback,
    build_improvement_report,
    feedback_prompt,
    reclassify_feedback,
    write_improvement_report,
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
    write_snapshot_fingerprints,
)


@dataclass(frozen=True)
class QuestionValidationResult:
    question_id: str
    status: str
    summary: str
    commands: tuple[dict[str, str], ...]
    changed_files: tuple[str, ...]


class _PipelineRuntimeTelemetry:
    """Run-local monotonic telemetry; never writes from App Server callbacks."""

    def __init__(
        self,
        *,
        model_capacity: int,
        patch_tool_capacity: int,
    ) -> None:
        self.model_capacity = max(1, int(model_capacity))
        self.patch_tool_capacity = max(1, int(patch_tool_capacity))
        self._lock = threading.Lock()
        self._model_started: set[tuple[str, str]] = set()
        self._model_finished: set[tuple[str, str]] = set()
        self._model_active = 0
        self._model_peak = 0
        self._model_queue_waits: list[float] = []
        self._model_durations: list[float] = []
        self._question_window_release_times: deque[tuple[str, float]] = deque()
        self._question_window_admitted_count = 0
        self._question_window_released_count = 0
        self._question_window_refill_latencies: list[float] = []
        self._patch_tool_active: dict[str, tuple[str, ...]] = {}
        self._patch_tool_peak = 0
        self._patch_tool_started_count = 0
        self._patch_tool_finished_count = 0
        self._patch_lock_active: dict[str, tuple[str, ...]] = {}
        self._patch_lock_peak = 0
        self._patch_lock_path_active: dict[str, int] = {}
        self._patch_lock_path_peak: dict[str, int] = {}
        self._patch_tool_queue_waits: list[float] = []
        self._patch_lock_waits: list[float] = []

    @staticmethod
    def _duration_summary(values: Iterable[float]) -> dict[str, float | int]:
        normalized = [max(0.0, float(value)) for value in values]
        total = sum(normalized)
        return {
            "count": len(normalized),
            "total": round(total, 6),
            "average": (
                round(total / len(normalized), 6)
                if normalized
                else 0.0
            ),
            "maximum": round(max(normalized), 6) if normalized else 0.0,
        }

    def observe_model_turn(self, event: Mapping[str, Any]) -> float | None:
        key = (
            str(event.get("threadId") or ""),
            str(event.get("turnId") or ""),
        )
        observed = event.get("observedMonotonic")
        if not all(key) or not isinstance(observed, (int, float)):
            return None
        event_name = str(event.get("event") or "")
        with self._lock:
            if event_name == "started":
                if key in self._model_started:
                    return None
                self._model_started.add(key)
                self._model_active += 1
                self._model_peak = max(
                    self._model_peak,
                    self._model_active,
                )
                queue_wait = event.get("queueWaitSeconds")
                if isinstance(queue_wait, (int, float)):
                    self._model_queue_waits.append(max(0.0, float(queue_wait)))
                return None
            if (
                event_name != "finished"
                or key not in self._model_started
                or key in self._model_finished
            ):
                return None
            self._model_finished.add(key)
            self._model_active = max(0, self._model_active - 1)
            duration = event.get("durationSeconds")
            if isinstance(duration, (int, float)):
                self._model_durations.append(max(0.0, float(duration)))
            return None

    def question_window_released(
        self,
        question_id: str,
        *,
        observed_monotonic: float,
    ) -> None:
        with self._lock:
            self._question_window_released_count += 1
            self._question_window_release_times.append(
                (str(question_id), float(observed_monotonic))
            )

    def question_window_segment_started(self) -> None:
        with self._lock:
            self._question_window_release_times.clear()

    def question_window_admitted(
        self,
        question_id: str,
        *,
        source: str,
        observed_monotonic: float,
    ) -> float | None:
        with self._lock:
            self._question_window_admitted_count += 1
            if not self._question_window_release_times:
                return None
            released_question_id, released_at = (
                self._question_window_release_times.popleft()
            )
            if source != "waiting" or released_question_id == str(question_id):
                return None
            latency = max(0.0, float(observed_monotonic) - released_at)
            self._question_window_refill_latencies.append(latency)
            return latency

    def patch_tool_started(
        self,
        child_id: str,
        *,
        queue_wait_seconds: float,
    ) -> None:
        with self._lock:
            if child_id in self._patch_tool_active:
                return
            self._patch_tool_active[child_id] = ()
            self._patch_tool_started_count += 1
            self._patch_tool_peak = max(
                self._patch_tool_peak,
                len(self._patch_tool_active),
            )
            self._patch_tool_queue_waits.append(
                max(0.0, queue_wait_seconds)
            )

    def patch_lock_acquired(
        self,
        child_id: str,
        paths: Iterable[Any],
        seconds: float,
    ) -> None:
        normalized_paths = tuple(
            sorted({str(value) for value in paths if str(value)})
        )
        with self._lock:
            self._patch_lock_waits.append(max(0.0, float(seconds)))
            if child_id in self._patch_lock_active:
                return
            self._patch_lock_active[child_id] = normalized_paths
            self._patch_lock_peak = max(
                self._patch_lock_peak,
                len(self._patch_lock_active),
            )
            for path in normalized_paths:
                active = self._patch_lock_path_active.get(path, 0) + 1
                self._patch_lock_path_active[path] = active
                self._patch_lock_path_peak[path] = max(
                    self._patch_lock_path_peak.get(path, 0),
                    active,
                )

    def patch_lock_released(self, child_id: str) -> None:
        with self._lock:
            paths = self._patch_lock_active.pop(child_id, None)
            if paths is None:
                return
            for path in paths:
                self._patch_lock_path_active[path] = max(
                    0,
                    self._patch_lock_path_active.get(path, 0) - 1,
                )

    def patch_tool_finished(self, child_id: str) -> None:
        with self._lock:
            if self._patch_tool_active.pop(child_id, None) is None:
                return
            self._patch_tool_finished_count += 1

    def model_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "measurement": "app_server_protocol_notifications",
                "capacity": self.model_capacity,
                "inFlight": self._model_active,
                "peakInFlight": self._model_peak,
                "startedCount": len(self._model_started),
                "finishedCount": len(self._model_finished),
                "queueWaitSeconds": self._duration_summary(
                    self._model_queue_waits
                ),
                "durationSeconds": self._duration_summary(
                    self._model_durations
                ),
            }

    def question_window_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "measurement": "scheduler_question_window",
                "admittedCount": self._question_window_admitted_count,
                "releasedCount": self._question_window_released_count,
                "refillLatencySeconds": self._duration_summary(
                    self._question_window_refill_latencies
                ),
            }

    def patch_tool_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "measurement": "deterministic_patch_tool_and_path_locks",
                "capacity": self.patch_tool_capacity,
                "inFlight": len(self._patch_tool_active),
                "peakInFlight": self._patch_tool_peak,
                "startedCount": self._patch_tool_started_count,
                "finishedCount": self._patch_tool_finished_count,
                "lockHeldInFlight": len(self._patch_lock_active),
                "lockHeldPeakInFlight": self._patch_lock_peak,
                "lockHeldInFlightByPath": {
                    key: value
                    for key, value in sorted(
                        self._patch_lock_path_active.items()
                    )
                    if value
                },
                "lockHeldPeakInFlightByPath": dict(
                    sorted(self._patch_lock_path_peak.items())
                ),
                "queueWaitSeconds": self._duration_summary(
                    self._patch_tool_queue_waits
                ),
                "lockWaitSeconds": self._duration_summary(
                    self._patch_lock_waits
                ),
            }


class _ParentRunHeartbeatTicker:
    """One coordinator-owned heartbeat writer for a maintenance parent run."""

    def __init__(
        self,
        store: "QualificationRunStore",
        qualification: str,
        run_id: str,
        *,
        interval_seconds: float = 15.0,
    ):
        self.store = store
        self.qualification = qualification
        self.run_id = run_id
        self.interval_seconds = max(1.0, float(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_ParentRunHeartbeatTicker":
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="question-run-parent-heartbeat",
        )
        self._thread.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=min(self.interval_seconds, 2.0))

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.store.update(
                    self.qualification,
                    self.run_id,
                    heartbeatAt=_now(),
                    heartbeatWriter="coordinator",
                )
            except Exception:
                # The owning job reports the real failure. A failed diagnostic
                # heartbeat must not create a second failure path.
                pass


QUESTION_CONCURRENCY_OPTIONS = (1, 5, 10, 32, 64, DEFAULT_MAX_PARALLEL_TURNS)
DEFAULT_QUESTION_CONCURRENCY = DEFAULT_MAX_PARALLEL_TURNS
PREPARATION_MAX_PARALLEL_QUESTIONS = 100
PREPARATION_HEARTBEAT_SECONDS = 15.0
OUTCOME_COALESCE_SECONDS = 0.25
PREPARED_CANDIDATE_SCHEMA_VERSION = "question-maintenance-prepared-candidate/v1"
LIVE_RUN_STATUSES = {
    "queued",
    "running",
    "validating",
}
RECOVERY_SIDECAR_SCHEMA = "qualification-run-recovery-candidate/v1"
MANIFEST_LIST_SUMMARY_SCHEMA = "qualification-run-list-summary/v1"
AGGREGATE_REVIEW_CHECKPOINT_SCHEMA = "aggregate-review-checkpoint/v1"
_AGGREGATE_CHECKPOINT_MISSING = object()
DASHBOARD_RUN_INDEX_SCHEMA = "qualification-dashboard-run-index/v1"
DASHBOARD_RUN_INDEX_LIMIT = 32
DASHBOARD_RUN_EXCLUDED_WORK_TYPES = frozenset({"evaluation", "reevaluation"})
DASHBOARD_RUN_EXCLUDED_SCHEMA_VERSIONS = frozenset(
    {"failed-delta-reconciliation/v1"}
)
MANIFEST_HEADER_BYTES = 64 * 1024
MANIFEST_CACHE_LIMIT = 256
RUN_LIST_HEAVY_FIELDS = frozenset(
    {
        "allowedPatchFiles",
        "allowedWriteFiles",
        "evaluationFeedbackByQuestion",
        "policyTargets",
        "progressTargets",
        "questionExecutions",
        "resumeWorkItemKeys",
        "sourceFiles",
        "targetQuestionKeys",
        "targetRecordAliasGroups",
        "targetRecordAliases",
        "targetRecordBindings",
        "targetRecordScopes",
        "targetSourceRecordScopes",
        "targetStageIdsByQuestion",
    }
)
ARTIFACT_SYNC_COMPLETE_STATUSES = {"succeeded", "current", "not_required"}
PROGRESS_EVENT_TYPES = {"question_started", "stage_completed", "question_completed"}
PROGRESS_RESULT_FIELDS = {
    "summary",
    "correctChoiceText",
    "explanationText",
    "questionType",
    "isCalculationQuestion",
    "questionIntent",
    "lawContext",
    "lawAudit",
    "questionSetId",
}
MAX_PROGRESS_BYTES = 8 * 1024 * 1024
MAX_PROGRESS_EVENTS = 10_000
MAX_PROGRESS_LINE_BYTES = 32 * 1024
MAX_WRITER_VALIDATION_ATTEMPTS = 3
AGGREGATE_REVIEW_PROMPT_CONTRACT_VERSION = "aggregate-answer-review-prompt/v5"
AGGREGATE_ADJUDICATION_PROMPT_CONTRACT_VERSION = (
    "aggregate-answer-adjudication-prompt/v1"
)
MAX_POLICY_REFRESH_ATTEMPTS = 2
MAX_PROVIDER_ATTEMPTS = 2
ALLOWED_MAINTENANCE_DIR_NAMES = {
    "05_originalized",
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
    "originalize": {"05_originalized"},
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
    "05_originalized": "originalized",
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
    "question_type": {"questionType", "isCalculationQuestion"},
    "question_intent": {"questionIntent"},
    "correct_choice": {"correctChoiceText"},
    "law_context": {"lawContext"},
    "explanation": {"explanationText"},
    "law_audit": {"lawRevision"},
    "question_set": {"questionSetId"},
}
FIELD_PATCH_DIR_NAMES = {
    "questionImageStorageUrls": {"05_originalized"},
    "originalQuestionChoiceImageUrls": {"05_originalized"},
    "questionType": {"10_questionType_fixed", "99_model_review_flags"},
    "isCalculationQuestion": {"10_questionType_fixed", "99_model_review_flags"},
    "questionIntent": {"15_correctChoiceText_fixed", "99_model_review_flags"},
    "answer_result_text": {"23_correctChoiceText_fixed", "99_model_review_flags"},
    "correctChoiceText": {"23_correctChoiceText_fixed", "99_model_review_flags"},
    "questionLearningPatternId": {
        "21_explanationText_added",
        "99_model_review_flags",
    },
    "explanationText": {"21_explanationText_added", "99_model_review_flags"},
    "suggestedQuestionDetailsByChoice": {
        "21_explanationText_added",
        "99_model_review_flags",
    },
    "questionSetId": {"22_questionSetId_linked", "99_model_review_flags"},
}
NON_AUTOMATED_CORRECTION_FIELDS = {"questionBodyText", "choiceTextList"}
LAW_PATCH_DIR_NAMES = set(STAGE_PATCH_DIR_NAMES["law_audit"])
LEGACY_SUGGESTED_QUESTION_FIELDS = {
    "suggestedQuestions",
    "suggestedQuestionDetails",
}


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
    "05": STAGE_PATCH_DIR_NAMES["originalize"],
    "01": STAGE_PATCH_DIR_NAMES["question_type"],
    "02": STAGE_PATCH_DIR_NAMES["question_intent"],
    "02a": STAGE_PATCH_DIR_NAMES["correct_choice"],
    "02b": STAGE_PATCH_DIR_NAMES["law_context"],
    "03": STAGE_PATCH_DIR_NAMES["explanation"],
    "03b": STAGE_PATCH_DIR_NAMES["law_audit"],
    "04": STAGE_PATCH_DIR_NAMES["question_set"],
}
REWORK_POLICY_STAGE_IDS = {
    "05": "originalize",
    "01": "question_type",
    "02": "question_intent",
    "02a": "correct_choice",
    "02b": "law_context",
    "03": "explanation",
    "03b": "law_audit",
    "04": "question_set",
}


def evaluation_rework_stage_codes(snapshot: Mapping[str, Any]) -> list[str]:
    """Return every workflow stage required by an evaluation result."""
    requested = list(
        dict.fromkeys(
            str(item.get("stage") or "")
            for item in snapshot.get("reworkItems") or []
            if isinstance(item, Mapping) and item.get("stage")
        )
    )
    if snapshot.get("answerMappingMatched") is False and "02a" not in requested:
        requested.append("02a")
    # A law audit can only verify revisions after the law-context stage has
    # independently classified the question and discovered its references.
    # Do not let a stale isLawRelated=false value skip that prerequisite.
    if "03b" in requested and "02b" not in requested:
        requested.append("02b")
    # questionType determines whether explanationText is stored per choice or
    # once per question. Rechecking 01 without 03 can therefore leave a
    # validated question with an invalid explanation shape. Correct-answer
    # changes likewise require the corresponding explanation to be rewritten.
    if ({"01", "02a"} & set(requested)) and "03" not in requested:
        requested.append("03")
    return [
        *(
            stage_code
            for stage_code in REWORK_POLICY_STAGE_IDS
            if stage_code in requested
        ),
        *(
            stage_code
            for stage_code in requested
            if stage_code not in REWORK_POLICY_STAGE_IDS
        ),
    ]


POLICY_STAGE_BY_PATCH_DIR = {
    "05_originalized": "originalize",
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


def _server_law_audit_fields(
    *,
    qualification: str,
    list_group_id: str,
    run_id: str,
    policy_version: str,
    projected: Mapping[str, Any],
    candidate_fields: Mapping[str, Any],
    audited_at: datetime | None = None,
) -> dict[str, Any]:
    """Add reproducible server-owned metadata to one 03b sidecar row."""

    observed_at = audited_at or datetime.now(timezone.utc).astimezone()
    if observed_at.tzinfo is None:
        raise ValueError("audited_at must include timezone")
    references = candidate_fields.get(
        "lawReferences", projected.get("lawReferences")
    )
    facts = candidate_fields.get(
        "lawRevisionFacts", projected.get("lawRevisionFacts")
    )
    current_correct = candidate_fields.get(
        "correctChoiceText", projected.get("correctChoiceText")
    )
    audit_input = {
        "questionBodyText": projected.get("questionBodyText"),
        "choiceTextList": projected.get("choiceTextList"),
        "correctChoiceText": current_correct,
        "examTimeDecision": candidate_fields.get("examTimeDecision"),
        "currentLawDecision": candidate_fields.get("currentLawDecision"),
        "lawReferences": references,
        "lawRevisionFacts": facts,
    }
    reference_hash = sha256_json(references)
    audit_status = str(candidate_fields.get("auditStatus") or "").strip()
    values = dict(candidate_fields)
    values.update(
        {
            "qualification": qualification,
            "listGroupId": list_group_id,
            "auditedAt": observed_at.isoformat(timespec="seconds"),
            "nextAuditDueAt": (
                observed_at + timedelta(days=365)
            ).date().isoformat(),
            "auditMethodVersion": f"law-audit/{policy_version or 'unknown'}",
            "auditInputHash": "sha256:" + sha256_json(audit_input),
            "evidenceBindingHash": "sha256:" + reference_hash,
            "auditRunId": run_id,
            "lawCorpusSnapshotId": (
                "codex-web-primary:"
                f"{observed_at.date().isoformat()}:{reference_hash[:16]}"
            ),
            "primaryAuditRunId": f"{run_id}:primary",
            "secondaryAuditRunId": f"{run_id}:secondary",
            "userVisibleNoticeRequired": (
                audit_status == "updated_to_current_law"
            ),
            "noticeReason": (
                "現行法に基づき学習用の正誤を更新した。"
                if audit_status == "updated_to_current_law"
                else ""
            ),
            "remainingRisk": str(
                candidate_fields.get("holdReason")
                or candidate_fields.get("reviewNotes")
                or ""
            ).strip(),
        }
    )
    values["tertiaryAuditRunId"] = (
        f"{run_id}:tertiary"
        if audit_status == "updated_to_current_law"
        else None
    )
    return values


def _safe_segment(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise ValueError(f"invalid path segment: {value}")
    return value


def _source_binding_accepts_identity(
    binding: Mapping[str, Any], identity: Mapping[str, Any]
) -> bool:
    expected = SourceIdentityBinding.from_mapping(binding)
    actual = SourceIdentityBinding.from_mapping(identity)
    aliases = {
        str(value)
        for value in [*(binding.get("aliases") or []), *expected.as_tuple()]
        if value
    }
    return bool(
        expected.is_complete()
        and actual.is_complete()
        and actual.source_question_key == expected.source_question_key
        and actual.source_record_ref == expected.source_record_ref
        and actual.review_question_id in aliases
    )


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
        contract_fields = {
            field: copy.deepcopy(record[field])
            for field in (
                "aggregateAnswerDecomposition",
                "schemaVersion",
                "qualification",
                "listGroupId",
            )
            if field in record
        }
        try:
            contract_fields["aggregateStableParentIdentity"] = (
                stable_parent_identity(record)
            )
        except ValueError:
            pass
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
                "contractFields": contract_fields,
            }
        )
    return snapshot


class QualificationRunError(RuntimeError):
    pass


def _question_plan_list_group_id(
    question_plan: Mapping[str, Any],
) -> str:
    """Resolve one question's server-owned group identity from its run plan."""

    progress_group_ids = {
        str(target.get("listGroupId") or "").strip()
        for target in question_plan.get("progressTargets") or []
        if isinstance(target, Mapping)
        and str(target.get("listGroupId") or "").strip()
    }
    scope_group_ids = {
        str(value).strip()
        for value in question_plan.get("targetGroupIds") or []
        if str(value).strip()
    }
    if len(progress_group_ids) > 1 or len(scope_group_ids) > 1:
        raise QualificationRunError(
            "一問のlistGroupIdを実行計画から一意に確定できません。"
        )
    progress_group_id = next(iter(progress_group_ids), "")
    scope_group_id = next(iter(scope_group_ids), "")
    if (
        progress_group_id
        and scope_group_id
        and progress_group_id != scope_group_id
    ):
        raise QualificationRunError(
            "一問のlistGroupIdが実行計画内で一致しません。"
        )
    list_group_id = progress_group_id or scope_group_id
    if not list_group_id:
        raise QualificationRunError(
            "一問のlistGroupIdを実行計画から確認できません。"
        )
    return list_group_id


def normalize_question_concurrency(value: Any) -> int:
    option_labels = "、".join(str(option) for option in QUESTION_CONCURRENCY_OPTIONS)
    if isinstance(value, bool):
        raise QualificationRunError(
            f"同時処理上限は{option_labels}から選択してください。"
        )
    try:
        concurrency = int(value)
    except (TypeError, ValueError) as exc:
        raise QualificationRunError(
            f"同時処理上限は{option_labels}から選択してください。"
        ) from exc
    if (
        isinstance(value, float) and value != concurrency
    ) or concurrency not in QUESTION_CONCURRENCY_OPTIONS:
        raise QualificationRunError(
            f"同時処理上限は{option_labels}から選択してください。"
        )
    return concurrency


def _canonical_json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _sorted_newline_sha256(values: list[str]) -> str:
    return hashlib.sha256(
        "".join(f"{value}\n" for value in sorted(values)).encode("utf-8")
    ).hexdigest()


def _question_work_selection_count(plan: Mapping[str, Any]) -> int:
    stage_plans = [
        stage_plan
        for stage_plan in plan.get("stagePlans") or []
        if isinstance(stage_plan, Mapping)
    ]
    if not stage_plans:
        return int(plan.get("targetCount") or 0)
    scoped_stage_plans = [
        stage_plan for stage_plan in stage_plans if "progressTargets" in stage_plan
    ]
    if not scoped_stage_plans:
        return int(plan.get("targetCount") or 0)
    return sum(
        len(stage_plan.get("progressTargets") or [])
        for stage_plan in scoped_stage_plans
    )


def _question_work_target_identity(plan: Mapping[str, Any]) -> dict[str, Any]:
    try:
        executions = build_question_executions(plan)
    except QuestionWorkQueueError as exc:
        raise QualificationRunError(str(exc)) from exc
    question_ids = [str(item.get("questionId") or "") for item in executions]
    stages = [
        stage
        for item in executions
        for stage in item.get("stages") or []
        if isinstance(stage, Mapping)
    ]
    work_item_keys = [str(stage.get("workItemKey") or "") for stage in stages]
    actual_stage_ids = [str(stage.get("stageId") or "") for stage in stages]
    declared_stage_ids = list(
        dict.fromkeys(
            str(value)
            for value in plan.get("stageIds") or [plan.get("stageId")]
            if value
        )
    )
    if any(not value for value in question_ids + work_item_keys + actual_stage_ids):
        raise QualificationRunError("preview対象identityに空値があります。")
    if len(question_ids) != len(set(question_ids)):
        raise QualificationRunError("preview対象のquestionIdが重複しています。")
    if len(work_item_keys) != len(set(work_item_keys)):
        raise QualificationRunError("preview対象のworkItemKeyが重複しています。")
    unknown = sorted(set(actual_stage_ids) - set(declared_stage_ids))
    if unknown:
        raise QualificationRunError(
            "preview対象に不明なstageがあります: " + ", ".join(unknown)
        )
    if len(question_ids) != int(plan.get("targetCount") or 0):
        raise QualificationRunError("preview対象のquestion totalが一致しません。")
    declared_work_item_count = int(plan.get("workItemCount") or plan["targetCount"])
    declared_selection_count = int(
        plan.get("selectionWorkItemCount") or declared_work_item_count
    )
    if declared_selection_count != _question_work_selection_count(plan):
        raise QualificationRunError("preview対象のworkItemCountが一致しません。")
    declared_stage_count = int(
        plan.get("stageCount") or len(declared_stage_ids)
    )
    if declared_stage_count != len(declared_stage_ids):
        raise QualificationRunError("preview対象のstageCountが一致しません。")
    stage_summary = [
        {
            "stageId": stage_id,
            "workItemCount": actual_stage_ids.count(stage_id),
        }
        for stage_id in declared_stage_ids
        if stage_id in actual_stage_ids
    ]
    if sum(item["workItemCount"] for item in stage_summary) != len(stages):
        raise QualificationRunError("preview対象のstage totalが一致しません。")
    if "selectionWorkItemCount" in plan and declared_work_item_count != len(stages):
        raise QualificationRunError("preview対象のqueue workItemCountが一致しません。")
    return {
        "schemaVersion": "question-work-target-identity/v1",
        "hashFormat": "utf-8-sorted-newline-sha256/v1",
        "questionIds": sorted(question_ids),
        "questionIdsHash": _sorted_newline_sha256(question_ids),
        "workItemKeys": sorted(work_item_keys),
        "workItemKeysHash": _sorted_newline_sha256(work_item_keys),
        "workItemCount": len(work_item_keys),
        "stageCount": len(stage_summary),
        "stageSummary": stage_summary,
    }


def _validated_question_work_queue(
    plan: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Rebuild and verify the question queue bound into a preview."""

    has_question_work = bool(
        plan.get("progressTargets") or plan.get("stagePlans")
    )
    expected_identity = plan.get("targetIdentity")
    if not has_question_work:
        if expected_identity is not None:
            raise QualificationRunError(
                "preview対象identityに対応するquestion queueがありません。"
            )
        return [], {"questionCount": 0, "workItemCount": 0}

    try:
        question_executions = build_question_executions(plan)
    except QuestionWorkQueueError as exc:
        raise QualificationRunError(str(exc)) from exc
    actual_target_identity = _question_work_target_identity(plan)
    if expected_identity != actual_target_identity:
        raise QualificationRunError(
            "preview対象identityと開始時queueが一致しません。"
        )
    actual_queue_summary = queue_summary(question_executions)
    if (
        int(actual_target_identity.get("workItemCount") or 0)
        != int(actual_queue_summary.get("workItemCount") or 0)
        or int(actual_target_identity.get("stageCount") or 0)
        != len(actual_target_identity.get("stageSummary") or [])
        or sum(
            int(item.get("workItemCount") or 0)
            for item in actual_target_identity.get("stageSummary") or []
            if isinstance(item, Mapping)
        )
        != int(actual_queue_summary.get("workItemCount") or 0)
    ):
        raise QualificationRunError(
            "preview対象identityのqueue集計が一致しません。"
        )
    return question_executions, actual_queue_summary


def _validated_projected_input_path(
    repo_root: Path,
    parent_run_directory: Path,
    target: Mapping[str, Any],
    expected_hash: str,
) -> Path:
    relative_path = str(target.get("_projectedInputPath") or "").strip()
    normalized_expected_hash = str(expected_hash).strip()
    if not relative_path or not normalized_expected_hash:
        raise QualificationRunError(
            "保存済み問題別候補のprojected input identityがありません。"
        )
    projected_root = (parent_run_directory / "projected_inputs").resolve()
    projected_path = (repo_root / relative_path).resolve()
    if projected_path.parent != projected_root:
        raise QualificationRunError(
            "保存済み問題別候補のprojected input pathがrun外を参照しています。"
        )
    try:
        actual_hash = hashlib.sha256(projected_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise QualificationRunError(
            "保存済み問題別候補のprojected inputを読み取れません。"
        ) from exc
    if not hmac.compare_digest(normalized_expected_hash, actual_hash):
        raise QualificationRunError(
            "保存済み問題別候補のprojected input hashが一致しません。"
        )
    return projected_path


def _question_candidates_payload(
    candidates: Iterable[QuestionCandidate],
) -> dict[str, Any]:
    return {
        "schemaVersion": "question-maintenance-candidates/v3",
        "questionResults": [
            {
                "questionId": candidate.question_id,
                "status": candidate.status,
                "summary": candidate.summary,
                "updates": [
                    {
                        "targetId": update.target_id,
                        "setFields": [
                            {
                                "field": field,
                                "value": copy.deepcopy(value),
                            }
                            for field, value in sorted(update.set_fields.items())
                        ],
                        "unsetFields": list(update.unset_fields),
                    }
                    for update in candidate.updates
                ],
            }
            for candidate in candidates
        ],
    }


def _prepared_candidate_envelope(
    *,
    question_id: str,
    stage_id: str,
    input_fingerprint_value: str,
    projected_input_hash: str,
    content: Mapping[str, Any],
) -> dict[str, Any]:
    normalized_question_id = str(question_id).strip()
    normalized_stage_id = str(stage_id).strip()
    normalized_input = str(input_fingerprint_value).strip()
    normalized_projection = str(projected_input_hash).strip()
    if not all(
        (
            normalized_question_id,
            normalized_stage_id,
            normalized_input,
            normalized_projection,
        )
    ):
        raise QualificationRunError(
            "問題別候補のidentity又は入力fingerprintがありません。"
        )
    payload = {
        "schemaVersion": PREPARED_CANDIDATE_SCHEMA_VERSION,
        "questionId": normalized_question_id,
        "stageId": normalized_stage_id,
        "inputFingerprint": normalized_input,
        "projectedInputHash": normalized_projection,
        "content": copy.deepcopy(dict(content)),
    }
    payload["contentHash"] = "sha256:" + _canonical_json_hash(payload)
    return payload


def _validated_prepared_candidate(
    value: Mapping[str, Any],
    *,
    question_id: str | None = None,
    stage_id: str | None = None,
    input_fingerprint_value: str | None = None,
    projected_input_hash: str | None = None,
) -> dict[str, Any]:
    candidate = copy.deepcopy(dict(value))
    expected_hash = str(candidate.pop("contentHash", "") or "")
    actual_hash = "sha256:" + _canonical_json_hash(candidate)
    if (
        candidate.get("schemaVersion") != PREPARED_CANDIDATE_SCHEMA_VERSION
        or not expected_hash
        or not hmac.compare_digest(expected_hash, actual_hash)
        or not isinstance(candidate.get("content"), Mapping)
    ):
        raise QualificationRunError(
            "保存済み問題別候補のschema又はcontent hashが一致しません。"
        )
    expected = {
        "questionId": question_id,
        "stageId": stage_id,
        "inputFingerprint": input_fingerprint_value,
        "projectedInputHash": projected_input_hash,
    }
    mismatched = [
        key
        for key, expected_value in expected.items()
        if expected_value is not None
        and str(candidate.get(key) or "") != str(expected_value)
    ]
    if mismatched:
        raise QualificationRunError(
            "保存済み問題別候補の入力が現在の問題と一致しません: "
            + ", ".join(mismatched)
        )
    candidate["contentHash"] = expected_hash
    return candidate


def prepare_question_items_concurrently(
    question_ids: list[str],
    prepare: Callable[[str], Any],
    *,
    max_workers: int,
    on_completed: Callable[[str, Any], None] | None = None,
) -> list[tuple[str, Any]]:
    """Prepare independent questions concurrently and return stable input order."""

    ordered_ids = [str(value) for value in question_ids]
    if not ordered_ids:
        return []
    worker_limit = max(1, min(int(max_workers), len(ordered_ids)))
    results: list[Any] = [None] * len(ordered_ids)
    with ThreadPoolExecutor(
        max_workers=worker_limit,
        thread_name_prefix="question-tool",
    ) as executor:
        futures = {
            executor.submit(prepare, question_id): (index, question_id)
            for index, question_id in enumerate(ordered_ids)
        }
        try:
            for future in as_completed(futures):
                index, question_id = futures[future]
                result = future.result()
                results[index] = result
                if on_completed is not None:
                    on_completed(question_id, result)
        except BaseException:
            for future in futures:
                future.cancel()
            raise
    return list(zip(ordered_ids, results, strict=True))


class QuestionItemError(QualificationRunError):
    """一問だけを保留できる対象解決エラー。"""


class QuestionPolicyChanged(QualificationRunError):
    """共通方針の更新後に同じ一問を準備し直すための内部通知。"""


class QuestionQueuePaused(QualificationRunError):
    """再開可能な外部停止又は安全条件の停止。"""

    def __init__(self, message: str, *, pause_kind: str) -> None:
        super().__init__(message)
        self.pause_kind = pause_kind


def _maintenance_research_prompt(prompt: str) -> str:
    writer_section_markers = (
        "\n## 画面用の問題別進捗\n",
        "\n## 完了記録\n",
    )
    section_starts = [
        index
        for marker in writer_section_markers
        if (index := prompt.find(marker)) >= 0
    ]
    base_prompt = (
        prompt[: min(section_starts)].rstrip()
        if section_starts
        else prompt.rstrip()
    )
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


def _exception_chain(exc: BaseException) -> Iterable[BaseException]:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = (
            current.__cause__
            if current.__cause__ is not None
            else None
            if current.__suppress_context__
            else current.__context__
        )


def _external_provider_failure(exc: BaseException) -> CodexAppServerError | None:
    chain = tuple(_exception_chain(exc))
    if any(
        isinstance(
            current,
            (CodexTurnTimeoutError, CodexControlRequestTimeoutError),
        )
        for current in chain
    ):
        return None
    return next(
        (
            current
            for current in chain
            if isinstance(current, (SubscriptionGateError, CodexAppServerError))
        ),
        None,
    )


def _isolated_turn_timeout(exc: BaseException) -> CodexTurnTimeoutError | None:
    return next(
        (
            current
            for current in _exception_chain(exc)
            if isinstance(current, CodexTurnTimeoutError)
        ),
        None,
    )


def _isolated_turn_failure(exc: BaseException) -> CodexAppServerError | None:
    return next(
        (
            current
            for current in _exception_chain(exc)
            if isinstance(
                current,
                (CodexTurnTimeoutError, CodexControlRequestTimeoutError),
            )
        ),
        None,
    )


def _isolated_failure_state(child: Mapping[str, Any]) -> bool:
    rollback = child.get("rollback")
    result = child.get("result")
    changed_files = (
        list(result.get("changedFiles") or [])
        if isinstance(result, Mapping)
        else []
    )
    unsafe_notified = bool(
        child.get("unsafeChangedFiles")
        or child.get("unsafeNotifiedChangedFiles")
    )
    attribution_verified = bool(
        child.get("writeAttributionVerified") is True
        and not unsafe_notified
    )
    rollback_safe = bool(
        isinstance(rollback, Mapping)
        and rollback.get("deltaUnknown") is not True
        and not rollback.get("remainingChangedFiles")
        and (
            rollback.get("status") == "succeeded"
            or (
                rollback.get("status") == "not_required"
                and child.get("canonicalWriteStarted") is False
            )
        )
    )
    return bool(
        rollback_safe
        and child.get("deltaUnknown") is not True
        and not unsafe_notified
        and (not changed_files or attribution_verified)
    )


def _child_retry_safe(child: Mapping[str, Any]) -> bool:
    if child.get("status") == "succeeded" and child.get("receiptValidated") is True:
        return True
    if not child.get("startedAt") and child.get("deltaUnknown") is not True:
        result = child.get("result")
        return not isinstance(result, Mapping) or not result.get("changedFiles")
    return _isolated_failure_state(child)


def _candidate_unset_fields(
    target: CandidateTarget,
    set_fields: Mapping[str, Any],
    requested_unset_fields: tuple[str, ...],
) -> tuple[str, ...]:
    """Keep the per-choice field as the sole explanation-patch authority."""

    unset_fields = set(requested_unset_fields)
    if target.role == "question_intent":
        unset_fields.update(
            {
                "correctChoiceText",
                "answer_result_text",
                "answer_result_inferred_correct_choice_numbers",
            }
        )
    elif target.role == "correct_choice":
        unset_fields.update(
            {
                "answer_result_text",
                "answer_result_inferred_correct_choice_numbers",
            }
        )
    if (
        target.role == "explanation"
        and "suggestedQuestionDetailsByChoice" in set_fields
    ):
        unset_fields.update(LEGACY_SUGGESTED_QUESTION_FIELDS)
    # Server-owned normalization may authoritatively materialize a field that
    # the model also requested to unset. The final operation must be
    # internally consistent; an authoritative set always wins.
    unset_fields.difference_update(set_fields)
    return tuple(sorted(unset_fields))


def _aggregate_calculation_flag(
    candidate_fields: Mapping[str, Any],
    current_record: Mapping[str, Any],
    selected_fields: set[str] | None,
) -> bool:
    """Resolve the independently selected calculation field for span projection."""

    candidate_value = candidate_fields.get("isCalculationQuestion")
    calculation_selected = (
        selected_fields is None or "isCalculationQuestion" in selected_fields
    )
    if calculation_selected:
        if isinstance(candidate_value, bool):
            return candidate_value
        raise QualificationRunError(
            "選択されたisCalculationQuestionのboolean候補がありません。"
        )
    current_value = current_record.get("isCalculationQuestion")
    if isinstance(current_value, bool):
        return current_value
    raise QualificationRunError(
        "更新対象外のisCalculationQuestionを現在のboolean値から維持できません。"
    )


def _batch_question_result(
    child: Mapping[str, Any], question_id: str
) -> Mapping[str, Any] | None:
    matches = [
        value
        for value in child.get("batchQuestionResults") or []
        if isinstance(value, Mapping)
        and str(value.get("questionId") or "") == question_id
    ]
    return matches[0] if len(matches) == 1 else None


def _terminal_receipt_validated(run: Mapping[str, Any]) -> bool:
    return bool(
        run.get("receiptValidated") is True
        and (
            run.get("status") == "succeeded"
            or (
                run.get("status") == "failed"
                and run.get("queueStatus") == "partial"
            )
        )
    )


def _law_reference_discovery_plan(
    record: Mapping[str, Any],
    *,
    stage_id: str,
) -> dict[str, Any]:
    """Choose the narrowest legal lookup path without trusting saved metadata."""

    choices = record.get("choiceTextList")
    choice_count = len(choices) if isinstance(choices, list) else 0
    linked_choice_indexes: set[int] = set()
    linked_locator_count = 0

    def has_locator(reference: Any) -> bool:
        return (
            isinstance(reference, Mapping)
            and bool(str(reference.get("lawId") or "").strip())
            and bool(
                str(reference.get("article") or "").strip()
                or str(reference.get("apiUrl") or "").strip()
                or str(reference.get("sourceUrl") or "").strip()
            )
        )

    def record_reference(reference: Mapping[str, Any], fallback_index: int) -> None:
        nonlocal linked_locator_count
        linked_locator_count += 1
        if str(reference.get("scope") or "") == "question":
            linked_choice_indexes.update(range(choice_count))
            return
        choice_index = reference.get("choiceIndex")
        if not isinstance(choice_index, int):
            choice_index = fallback_index
        if 0 <= choice_index < choice_count:
            linked_choice_indexes.add(choice_index)

    references = record.get("lawReferences")
    if isinstance(references, list):
        for fallback_index, bucket in enumerate(references):
            if isinstance(bucket, list):
                for reference in bucket:
                    if has_locator(reference):
                        record_reference(reference, fallback_index)
            elif has_locator(bucket):
                record_reference(bucket, fallback_index)

    missing_choice_indexes = sorted(
        set(range(choice_count)) - linked_choice_indexes
    )
    if stage_id == "law_audit" and record.get("isLawRelated") is False:
        strategy = "not_applicable"
    elif linked_choice_indexes and not missing_choice_indexes:
        strategy = "verify_linked_first"
    elif linked_choice_indexes:
        strategy = "verify_linked_then_target_gaps"
    else:
        strategy = "discover_after_classification"
    return {
        "strategy": strategy,
        "linkedChoiceIndexes": sorted(linked_choice_indexes),
        "missingChoiceIndexes": missing_choice_indexes,
        "linkedLocatorCount": linked_locator_count,
    }


def _structured_candidate_prompt(
    stage_prompt: str,
    targets: list[Mapping[str, Any]],
    *,
    canonical_guidance: str = "",
    stage_id: str | None = None,
    records_by_question: Mapping[str, Mapping[str, Any]],
    candidate_targets_by_question: Mapping[str, tuple[CandidateTarget, ...]],
    feedback_by_question: Mapping[str, list[Mapping[str, Any]]],
    stage_context: Mapping[str, Any] | None = None,
    original_aggregate_evidence_by_question: Mapping[
        str, Mapping[str, Any]
    ] | None = None,
    originalization_source_by_question: Mapping[
        str, Mapping[str, Any]
    ] | None = None,
    question_issue_evidence_by_question: Mapping[
        str, tuple[Mapping[str, Any], ...]
    ] | None = None,
    source_answer_evidence_by_question: Mapping[
        str, Mapping[str, Any]
    ] | None = None,
    primary_law_evidence_by_question: Mapping[
        str, Mapping[str, Any]
    ] | None = None,
) -> str:
    questions: list[dict[str, Any]] = []
    evidence_by_question = original_aggregate_evidence_by_question or {}
    originalization_sources = originalization_source_by_question or {}
    issue_evidence_by_question = question_issue_evidence_by_question or {}
    answer_evidence_by_question = source_answer_evidence_by_question or {}
    law_evidence_by_question = primary_law_evidence_by_question or {}
    for target in targets:
        question_id = str(target.get("id") or target.get("uiQuestionId") or "")
        binding = SourceIdentityBinding.from_mapping(target)
        candidate_targets = tuple(candidate_targets_by_question[question_id])
        allowed_fields = {
            field
            for candidate_target in candidate_targets
            for field in candidate_target.allowed_fields
        }
        previous_feedback = []
        for raw_feedback in feedback_by_question.get(question_id) or []:
            feedback = reclassify_feedback(
                raw_feedback,
                discard_resolved_sidecar_identity=True,
            )
            if feedback is None:
                continue
            feedback_text = json.dumps(
                feedback,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            obsolete_scope_fields = set(
                re.findall(
                    r"自動整備対象外field(?:の追加|の変更)?を検出しました:"
                    r"[^\"\n]* / ([A-Za-z_][A-Za-z0-9_]*)",
                    feedback_text,
                )
            )
            if obsolete_scope_fields and obsolete_scope_fields <= allowed_fields:
                continue
            previous_feedback.append(feedback)
        question = {
            "questionId": question_id,
            "displayLabel": str(target.get("displayLabel") or question_id),
            "sourceIdentity": binding.as_mapping(),
            "currentRecord": records_by_question[question_id],
            "candidateTargets": [
                value.prompt_value()
                for value in candidate_targets
            ],
            "previousValidationFeedback": previous_feedback,
        }
        if stage_id in {"law_context", "law_audit"}:
            question["lawReferenceDiscoveryPlan"] = (
                _law_reference_discovery_plan(
                    records_by_question[question_id],
                    stage_id=stage_id,
                )
            )
        primary_law_evidence = law_evidence_by_question.get(question_id)
        if primary_law_evidence is not None:
            question["primaryLawEvidence"] = copy.deepcopy(
                dict(primary_law_evidence)
            )
        source_answer_evidence = answer_evidence_by_question.get(question_id)
        if source_answer_evidence is not None:
            question["sourceAnswerEvidence"] = copy.deepcopy(
                dict(source_answer_evidence)
            )
        evidence = evidence_by_question.get(question_id)
        if evidence is not None:
            question["originalAggregateAnswerEvidence"] = copy.deepcopy(
                dict(evidence)
            )
        originalization_source = originalization_sources.get(question_id)
        if originalization_source is not None:
            question["originalizationSource"] = {
                field: copy.deepcopy(originalization_source.get(field))
                for field in (
                    "questionBodyText",
                    "choiceTextList",
                    "correctChoiceText",
                    "questionIntent",
                    "answer_result_text",
                    "explanation_common_prefix",
                    "explanation_common_summary",
                    "explanation_choice_snippets",
                    "explanationText",
                    "referenceUrls",
                )
                if field in originalization_source
            }
        question_issue_evidence = issue_evidence_by_question.get(question_id)
        if question_issue_evidence:
            question["questionIssueCorrectionEvidence"] = copy.deepcopy(
                list(question_issue_evidence)
            )
        questions.append(question)
    context_lines = (
        [
            "# 工程固有コンテキスト",
            "",
            json.dumps(stage_context, ensure_ascii=False, separators=(",", ":")),
            "",
        ]
        if stage_context
        else []
    )
    repeated_context_lines = (
        [
            "# previousValidationFeedback後の現行工程コンテキスト",
            "",
            "previousValidationFeedbackは再検証の材料であり、以下の現行rulesと"
            "allowedQuestionSetsを上書きしない。現在の問題全体と分類正本から再判定する。",
            json.dumps(stage_context, ensure_ascii=False, separators=(",", ":")),
            "",
        ]
        if stage_id == "question_set" and stage_context
        else []
    )
    law_reference_contract_lines = (
        [
            "lawReferenceDiscoveryPlanがある場合は、そのstrategyに従って探索範囲を限定する。",
            "これは実行順と計測用の入力情報であり、setFieldsへ転載しない。",
            "primaryLawEvidenceはserverがe-Gov法令API v2から取得した公式一次根拠である。",
            "status=completeのlocatorはモデルのweb探索より優先して使い、"
            "comparison=unchangedなら試験時点と現在時点の条文が同一である。",
            "comparison=changedなら二つの本文を比較して判定し、current_onlyなら"
            "現在時点の根拠として使う。status=partialの不足箇所だけ追加確認する。",
            "status=completeかつcomparison=unchangedの場合、lawReferencesのroleが"
            "current_basisであることだけを理由にholdへ送らない。",
            "examAsOfSourceは試験日の根拠であり、公式試験日catalog又はrecordを示す。",
            "primaryLawEvidence自体はsetFieldsへ転載しない。",
        ]
        if stage_id in {"law_context", "explanation", "law_audit"}
        else []
    )
    return "\n".join(
        [
            "# 工程の品質規則",
            "",
            canonical_guidance.rstrip(),
            "",
            "# 実行対象",
            "",
            stage_prompt.rstrip(),
            "",
            *context_lines,
            "# 構造化候補V3（この契約を最優先する）",
            "",
            "各問題を独立に判断し、指定されたallowedFieldsだけの更新候補を返す。",
            "previousValidationFeedbackが現行allowedFieldsと矛盾する場合は、"
            "現行allowedFieldsを優先する。",
            "file、shell、progress、receipt、git、外部状態は変更しない。",
            "対象を特定できない場合や根拠が足りない場合は、その問題だけblockedにする。",
            "一問だけを判断し、decision、summary、updateだけを返す。questionIdと反映先はserverが確定する。",
            "setFieldsはfieldとnative JSONのvalueの配列とする。",
            "candidateにする場合は、candidateTargetsのallowedFieldsをすべて明示的に確定する。確定できないfieldが一つでもあれば、その問題をblockedにする。",
            "各semantic fieldは一度だけsetFields又はunsetFieldsへ入れ、反映先はserverに任せる。",
            "fieldRulesがあるfieldは、そこに示す型とallowedValuesを厳守する。",
            *law_reference_contract_lines,
            "originalizationSourceがある場合、それは00_sourceの更新不能な比較証拠である。"
            "originalizationSourceを基準に、currentRecordは既存の草案として比較する。"
            "currentRecordが工程の品質規則を満たす場合は必要な最小修正にとどめる。"
            "元問題より構成、条件又は表現を大きく変えている場合は、"
            "元問題の情報と流れを保つ局所的な微修正へ整え直す。"
            "問題文と選択肢の両方をoriginalizationSourceと完全一致させず、"
            "工程の品質規則に沿う自然な差を残す。"
            "originalizationSource内の解説候補はprompt内だけの参照資料である。"
            "元解説を材料に03の解説promptへ沿ったより分かりやすい独自解説を作る。"
            "正答理由と各誤答の理由を変えず、文面をsetFieldsへ転載しない。",
            "correct_choice工程でoriginalizationSourceがある場合も、sourceの問題文、"
            "全選択肢、正答候補、元解説各field、referenceUrlsは更新不能な参照証拠である。"
            "currentRecordとは分離して独立に照合し、元解説又は元正答をsetFieldsへ転載せず、"
            "correctChoiceTextをsource値から自動割当しない。確認済み根拠との衝突はblockedにする。",
            "originalAggregateAnswerEvidenceがある場合、それは00_sourceの元集約選択肢と元正答を示す更新不能な参照証拠である。setFieldsへ入れず、現在の抽出記述ごとの判定と矛盾しないか照合する。",
            "元のcorrectChoiceTextは集約選択肢単位であり、抽出記述へ同じ配列を転記しない。元正答が示す組合せ又は個数を解釈して各記述を判定し、他の根拠とも一致する場合だけ確定する。",
            "sourceAnswerEvidenceがある場合、それは00_sourceから分離した更新不能な正答証拠である。"
            "evidenceType=trusted_gassyunin_judge_statement_verdictsは、取得元のjudge欄が"
            "sourceの問題文と各選択肢を組み合わせた最終命題へcorrectChoiceTextを直接対応付け、"
            "件数・順序・出所の機械検証を通過したことを示す。"
            "evidenceType=official_firestore_snapshot_statement_verdictsは、各肢の"
            "公式Firestore原本がofficial文書として保存され、本文・肢順・正答対応の"
            "機械検証を通過したことを示す。"
            "verdictSemantics=final_correct_choice_text_for_source_textかつ"
            "appliesToCurrentText=trueなら、applicationBasisを確認する。"
            "exact_source_textは現在と完全一致する本文・選択肢、"
            "official_question_content_correctionは同一問題・同一肢順の取得誤りを"
            "公式資料のBlind A/B・Challenge済みpatchで直した本文・選択肢に対する"
            "最終正誤である。どちらも否定語やquestionIntentを使って再反転しない。"
            "モデル内部の記憶や出典を示さない一般知識だけを、この証拠との明白な"
            "衝突とは扱わない。公式解答又は確認済みの公式・一次資料と衝突する場合だけ、"
            "確認した資料をsummaryへ要約してblockedにする。"
            "appliesToCurrentText=falseなら、source配列を現在値へ転記せず、現在の本文と"
            "選択肢から完全な命題を作り直す。各肢の正誤判定では本文と選択肢を"
            "組み合わせる。完結記述肢では本文の正誤指示を選択方向だけに使う。"
            "名詞句等の断片肢では本文の述語を一度だけ補った完全命題を作り、"
            "その命題が成立する真偽側をquestionIntentとする。構造が曖昧なら"
            "questionIntentを自動決定せずblockedにする。"
            "公式解答番号の意味はanswerResultSemanticsに従い、元の組合せ肢を指す場合は"
            "組合せ対応表がないことだけを理由にblockedにしない。"
            "証拠自体はsetFieldsへ転載しない。",
            "questionIssueCorrectionEvidenceがある場合、currentRecordと00_sourceの差は、専用のblind reviewと公式・一次資料で承認された問題訂正である。"
            "差があることだけを理由にblocked又は00_sourceへ差し戻さず、currentRecordの訂正文を設問として正答を独立判定する。"
            "この証拠はprompt内だけで参照し、setFieldsへ転載しない。",
            "別問題の内容や判断を流用しない。思考過程は返さない。",
            "出力は指定されたJSON Schemaに一致するobjectだけとする。",
            "",
            json.dumps(questions, ensure_ascii=False, separators=(",", ":")),
            "",
            *repeated_context_lines,
        ]
    )


def _canonical_document_guidance(
    repo_root: Path,
    canonical_docs: Iterable[str],
) -> str:
    """Embed trusted canonical documents for no-tool question model turns."""

    root = repo_root.resolve()
    sections = ["# 正本文書の内容", ""]
    seen: set[Path] = set()
    for raw_path in canonical_docs:
        value = str(raw_path or "").strip()
        if not value:
            continue
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
        try:
            relative = resolved.relative_to(root)
        except ValueError as exc:
            raise QualificationRunError(
                f"正本文書がrepository外を指しています: {value}"
            ) from exc
        if resolved in seen:
            continue
        seen.add(resolved)
        # WorkflowCatalog may be used against a minimal temporary repository in
        # tests or migrations. The catalog fingerprint already records missing
        # documents; embed every document that is actually present here.
        if not resolved.is_file():
            continue
        try:
            content = resolved.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise QualificationRunError(
                f"正本文書を読み込めません: {relative.as_posix()}"
            ) from exc
        sections.extend(
            [
                f"## {relative.as_posix()}",
                "",
                content,
                "",
            ]
        )
    if len(sections) == 2:
        return ""
    return "\n".join(sections).rstrip()


def _filter_structured_candidate_prompt(
    prompt: str,
    question_ids: set[str],
) -> str:
    """Keep only selected questions in an already-built structured prompt."""

    lines = prompt.splitlines()
    for index in range(len(lines) - 1, -1, -1):
        try:
            value = json.loads(lines[index])
        except json.JSONDecodeError:
            continue
        if not isinstance(value, list) or not all(
            isinstance(item, Mapping) and "questionId" in item for item in value
        ):
            continue
        filtered = [
            item for item in value if str(item.get("questionId") or "") in question_ids
        ]
        if len(filtered) != len(question_ids):
            raise QualificationRunError(
                "構造化候補promptから対象問題を一意に抽出できません。"
            )
        lines[index] = json.dumps(
            filtered,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return "\n".join(lines)
    raise QualificationRunError("構造化候補promptの問題一覧を確認できません。")


def _aggregate_answer_review_questions(
    targets: list[Mapping[str, Any]],
    records_by_question: Mapping[str, Mapping[str, Any]],
    candidate_sets_by_question: Mapping[str, Mapping[str, Any]],
    *,
    independent_reviews_by_question: (
        Mapping[str, list[Mapping[str, Any]]] | None
    ) = None,
) -> list[dict[str, Any]]:
    questions = []
    for target in targets:
        question_id = str(target.get("id") or target.get("uiQuestionId") or "")
        record = records_by_question[question_id]
        source_text = str(record.get("questionBodyText") or "")
        candidate_set = candidate_sets_by_question[question_id]
        candidate_views = []
        for candidate in candidate_set.get("candidates") or []:
            if not isinstance(candidate, Mapping):
                continue
            boundaries = []
            for span in candidate.get("spans") or []:
                if not isinstance(span, Mapping):
                    continue
                start = span.get("start")
                end = span.get("end")
                if not isinstance(start, int) or not isinstance(end, int):
                    continue
                boundaries.append(
                    {
                        "boundaryId": span.get("boundaryId"),
                        "sourceSlice": source_text[start:end],
                    }
                )
            candidate_views.append(
                {
                    "candidateId": candidate.get("candidateId"),
                    "boundaries": boundaries,
                }
            )
        question = {
            "questionId": question_id,
            "sourceHash": source_text_hash(source_text),
            "questionBodyText": source_text,
            "choiceTextList": copy.deepcopy(record.get("choiceTextList") or []),
            "candidateSets": candidate_views,
        }
        if independent_reviews_by_question is not None:
            question["independentReviews"] = copy.deepcopy(
                independent_reviews_by_question[question_id]
            )
        questions.append(question)
    return questions


def _aggregate_answer_review_prompt(
    targets: list[Mapping[str, Any]],
    records_by_question: Mapping[str, Mapping[str, Any]],
    candidate_sets_by_question: Mapping[str, Mapping[str, Any]],
) -> str:
    questions = _aggregate_answer_review_questions(
        targets,
        records_by_question,
        candidate_sets_by_question,
    )
    return "\n".join(
        [
            "# 集約回答問題の独立レビュー",
            "各問題を意味でtarget、non_target、holdに分類する。表記形式に限定しない。",
            "candidateSetsはserverが原文から機械生成した候補であり、文章や文字位置を作成・修正しない。",
            "最初にquestionBodyTextとchoiceTextListの役割を確認する。個別に正誤判定する記述そのものがchoiceTextListに既に並ぶ問題は、本文に「組合せ」又は「いくつ」とあってもnon_targetである。計算結果の数値候補、穴埋めの語句候補又は並べ替え候補も、本文に集約前の完全な命題が複数なければnon_targetである。",
            "candidateSetsが空又は不完全であることだけを理由にholdにしない。questionBodyTextに個別判定すべき完全な命題が複数あることと、choiceTextListがそれらの個数・組合せ等だけを表すことの両方を確認してからtarget候補を検討する。",
            "targetは、元の回答が複数記述の正誤を個数、組合せその他の一つの回答へ集約し、candidateSetsの各境界が受験者に個別の正誤判定を求める命題そのものである場合に限る。",
            "問題が事実として与える設例条件や共通前提、並べ替える項目、空欄へ入れる語句又は数値、計算の入力は、列挙されていても個別の正誤判定対象ではないためtargetにしない。",
            "choiceTextListに受験者が選ぶ個別の命題が既に並ぶ通常問題もtargetにしない。choiceTextListが個数又は組合せ等の集約回答で、個別に判定する全命題がquestionBodyText内にあるかを確認する。",
            "targetとして承認できる場合は、個別に判定する全命題を過不足なく含み、前提や入力を含まないcandidateIdを一つだけ選ぶ。",
            "正誤を解かず、正しい項目だけを選ばない。",
            "命題と前提を区別できない場合又は適切なcandidateIdがない場合はambiguous_target、ambiguous_boundary又はmissing_statementでholdにする。",
            "記述本文、理由、summary、説明、start/endその他の文字位置は出力しない。",
            "file、shell、外部状態を変更しない。指定JSON Schemaのobjectだけを返す。",
            json.dumps(questions, ensure_ascii=False, separators=(",", ":")),
        ]
    )


def _aggregate_answer_adjudication_prompt(
    targets: list[Mapping[str, Any]],
    records_by_question: Mapping[str, Mapping[str, Any]],
    candidate_sets_by_question: Mapping[str, Mapping[str, Any]],
    independent_reviews_by_question: Mapping[
        str, list[Mapping[str, Any]]
    ],
) -> str:
    questions = _aggregate_answer_review_questions(
        targets,
        records_by_question,
        candidate_sets_by_question,
        independent_reviews_by_question=independent_reviews_by_question,
    )
    return "\n".join(
        [
            "# 集約回答問題の不一致裁定",
            "2件の独立レビューが一致しなかった問題だけを、原文を正本として再判定する。",
            "independentReviewsは判断材料であり、多数決又はいずれか一方への自動追随はしない。",
            "questionBodyTextとchoiceTextListの役割を自分で確認し、target、non_target、holdを一つ確定する。",
            "candidateSetsはserverが原文から機械生成した候補であり、文章や文字位置を作成・修正しない。",
            "targetは、元の回答が本文内の複数命題の正誤を個数、組合せその他の一つの回答へ集約し、選択したcandidateIdが個別判定すべき全命題を過不足なく含む場合に限る。",
            "choiceTextListに受験者が選ぶ個別命題が既に並ぶ通常問題、計算結果、穴埋め、並べ替え、設例条件や共通前提はtargetにしない。",
            "原文と候補だけでは一意に確定できない場合に限り、ambiguous_target、ambiguous_boundary又はmissing_statementでholdにする。",
            "正誤を解かず、正しい項目だけを選ばない。",
            "記述本文、理由、summary、説明、start/endその他の文字位置は出力しない。",
            "file、shell、外部状態を変更しない。指定JSON Schemaのobjectだけを返す。",
            json.dumps(questions, ensure_ascii=False, separators=(",", ":")),
        ]
    )


def _structured_candidate_stage_context(
    repo_root: Path,
    qualification: str,
    stage_id: str,
) -> dict[str, Any]:
    if stage_id in {"law_context", "law_audit"}:
        return {
            "rules": [
                "既存lawReferencesは正答根拠として信用せず、lawId・article又は保存済みURLから一次情報本文を直接開く入口として先に使う。",
                "既存の紐付け先だけで全選択肢を十分に説明できると確認した場合は、広域検索とlawReferencesの再構築を行わず、有効な紐付けを保持する。",
                "不足又は不一致がある場合だけ、その選択肢と不足箇所に限定して一次情報を探索する。",
                "保存先が404、法令名不一致又は本文不足の場合は推測で補正せず、対象問題だけholdにする。",
                "別問題のlawReferencesを類似性だけで流用しない。",
            ],
        }
    if stage_id != "question_set":
        return {}
    category_path = (
        repo_root / "output" / qualification / "category" / "category.json"
    )
    try:
        payload = json.loads(category_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualificationRunError(
            f"04 問題集のcategory.jsonを読み込めません: {exc}"
        ) from exc
    question_sets = payload.get("questionSets") if isinstance(payload, Mapping) else None
    if not isinstance(question_sets, list):
        raise QualificationRunError(
            "04 問題集のcategory.jsonにquestionSetsがありません。"
        )
    options = [
        {
            "questionSetId": str(item.get("questionSetId") or ""),
            "name": str(item.get("name") or ""),
            "folderId": str(item.get("folderId") or ""),
            "description": item.get("description"),
            "matchingHints": item.get("matchingHints"),
        }
        for item in question_sets
        if isinstance(item, Mapping)
        and str(item.get("questionSetId") or "")
        and item.get("isDeleted") is not True
    ]
    return {
        "rules": [
            "questionSetIdだけを判定対象とし、questionSetIdListとchoiceQuestionSetIdsの新規生成は要求しない。",
            "現在値と同じ結論でも、問題全体と分類正本から独立に確定したquestionSetIdを明示して返す。",
            "allowedQuestionSetsにあるquestionSetIdだけを設定する。",
            "問題に登場するサービスの数ではなく、問題全体で正答を決める要件・制約・主な判断軸を各分類のdescriptionとmatchingHintsに照らし、最も強い決定要因を持つ一つを選ぶ。",
            "問題の明示的な決定要因をdescriptionとmatchingHintsで全て覆う候補は、サービス名だけが一致し決定要因を覆わない候補より優先する。両者を同等候補として扱わない。",
        ],
        "allowedQuestionSets": options,
    }


def _structured_candidate_inputs(
    repo_root: Path,
    stage_id: str,
    batch_plan: Mapping[str, Any],
) -> tuple[
    dict[str, Mapping[str, Any]],
    dict[str, tuple[CandidateTarget, ...]],
]:
    records: dict[str, Mapping[str, Any]] = {}
    targets: dict[str, tuple[CandidateTarget, ...]] = {}
    for raw_target in batch_plan.get("progressTargets") or []:
        if not isinstance(raw_target, Mapping):
            continue
        question_id = str(
            raw_target.get("id") or raw_target.get("uiQuestionId") or ""
        )
        projected_path = repo_root / str(raw_target.get("_projectedInputPath") or "")
        try:
            payload = json.loads(projected_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QualificationRunError(
                f"現在入力を読み取れません: {question_id}"
            ) from exc
        projected = payload.get("question_bodies") if isinstance(payload, Mapping) else None
        if (
            not isinstance(projected, list)
            or len(projected) != 1
            or not isinstance(projected[0], Mapping)
        ):
            raise QualificationRunError(
                f"現在入力は一問だけでなければなりません: {question_id}"
            )
        question_plan = subset_question_plan(batch_plan, [question_id])
        records[question_id] = copy.deepcopy(dict(projected[0]))
        targets[question_id] = candidate_targets(
            question_id,
            stage_id,
            question_plan,
        )
        binding = SourceIdentityBinding.from_mapping(raw_target)
        scopes = question_plan.get("targetRecordScopes") or {}
        for candidate_target in targets[question_id]:
            aliases = {
                str(alias)
                for group in scopes.get(candidate_target.path, [])
                for alias in group
                if alias
            }
            assert_target_resolvable(
                repo_root,
                candidate_target.path,
                binding=binding,
                aliases=aliases,
            )
    if not records or set(records) != set(targets):
        raise QualificationRunError("構造化候補の対象を準備できません。")
    return records, targets


def _projected_question_issue_evidence(
    repo_root: Path,
    target: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Read server-produced official correction evidence for one projection."""

    relative_path = str(target.get("_projectedInputPath") or "")
    if not relative_path:
        return ()
    try:
        payload = json.loads(
            (repo_root / relative_path).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ()
    if not isinstance(payload, Mapping):
        return ()
    return tuple(
        copy.deepcopy(dict(item))
        for item in payload.get("questionIssueCorrectionEvidence") or []
        if isinstance(item, Mapping)
    )


def _aggregate_review_source_records(
    repo_root: Path,
    qualification: str,
    batch_plan: Mapping[str, Any],
    raw_targets: list[Mapping[str, Any]],
    current_records: Mapping[str, Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    """Read review text and choices from the immutable source snapshot."""

    resolved_root = repo_root.resolve()
    allowed_source_paths = {
        (repo_root / str(value)).resolve()
        for value in batch_plan.get("sourceFiles") or []
        if str(value)
    }
    cached_records: dict[Path, list[dict[str, Any]]] = {}
    source_records: dict[str, Mapping[str, Any]] = {}
    for raw_target in raw_targets:
        question_id = str(
            raw_target.get("id") or raw_target.get("uiQuestionId") or ""
        )
        binding = SourceIdentityBinding.from_mapping(raw_target)
        try:
            source_name, raw_index = binding.source_record_ref.rsplit("#", 1)
            source_index = int(raw_index)
        except (ValueError, AttributeError) as exc:
            raise QualificationRunError(
                f"集約回答レビューのsourceRecordRefが不正です: {question_id}"
            ) from exc
        relative_source = Path(source_name)
        if (
            relative_source.is_absolute()
            or ".." in relative_source.parts
            or source_index < 0
        ):
            raise QualificationRunError(
                f"集約回答レビューのsourceRecordRefが不正です: {question_id}"
            )
        source_path = (
            repo_root
            / "output"
            / qualification
            / "questions_json"
            / str(raw_target.get("listGroupId") or "")
            / "00_source"
            / relative_source
        ).resolve()
        if (
            not source_path.is_relative_to(resolved_root)
            or not any(
                source_path == allowed
                or (allowed.is_dir() and source_path.is_relative_to(allowed))
                for allowed in allowed_source_paths
            )
        ):
            raise QualificationRunError(
                f"集約回答レビューのsource fileが実行scope外です: {question_id}"
            )
        if source_path not in cached_records:
            try:
                cached_records[source_path] = _snapshot_records(source_path)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise QualificationRunError(
                    f"集約回答レビューのsourceを読み取れません: {question_id}"
                ) from exc
        records = cached_records[source_path]
        if source_index >= len(records):
            raise QualificationRunError(
                f"集約回答レビューのsource recordが見つかりません: {question_id}"
            )
        current_record = current_records.get(question_id)
        if not isinstance(current_record, Mapping):
            raise QualificationRunError(
                f"集約回答レビューの現在入力が見つかりません: {question_id}"
            )
        source_record = copy.deepcopy(records[source_index])
        review_record = copy.deepcopy(dict(current_record))
        if isinstance(source_record.get("questionBodyText"), str):
            review_record["questionBodyText"] = source_record["questionBodyText"]
        if isinstance(source_record.get("choiceTextList"), list):
            review_record["choiceTextList"] = copy.deepcopy(
                source_record["choiceTextList"]
            )
            review_record["_aggregateSourceChoiceTextList"] = copy.deepcopy(
                source_record["choiceTextList"]
            )
        else:
            review_record["_aggregateSourceChoiceTextList"] = None
        if isinstance(source_record.get("sourceUniqueKeys"), list):
            review_record["_aggregateSourceUniqueKeys"] = copy.deepcopy(
                source_record["sourceUniqueKeys"]
            )
        else:
            review_record["_aggregateSourceUniqueKeys"] = None
        review_record["_aggregateSourceCorrectChoiceText"] = copy.deepcopy(
            source_record.get("correctChoiceText")
        )
        review_record["_aggregateSourceAnswerResultText"] = copy.deepcopy(
            source_record.get("answer_result_text")
        )
        source_records[question_id] = review_record
    if set(source_records) != set(current_records):
        raise QualificationRunError(
            "集約回答レビューのsource recordを全問分確認できません。"
        )
    return source_records


def _aggregate_downstream_source_evidence(
    repo_root: Path,
    qualification: str,
    batch_plan: Mapping[str, Any],
    raw_targets: list[Mapping[str, Any]],
    current_records: Mapping[str, Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    """Expose immutable aggregate answers as prompt-only downstream evidence."""

    aggregate_ids = {
        question_id
        for question_id, record in current_records.items()
        if is_approved_target(
            record.get("aggregateAnswerDecomposition"),
            str(record.get("questionBodyText") or ""),
        )
    }
    if not aggregate_ids:
        return {}
    source_records = _aggregate_review_source_records(
        repo_root,
        qualification,
        batch_plan,
        raw_targets,
        current_records,
    )
    evidence: dict[str, Mapping[str, Any]] = {}
    for question_id in aggregate_ids:
        source = source_records[question_id]
        evidence[question_id] = {
            "sourceRecordRef": SourceIdentityBinding.from_mapping(
                next(
                    target
                    for target in raw_targets
                    if str(target.get("id") or target.get("uiQuestionId") or "")
                    == question_id
                )
            ).source_record_ref,
            "choiceTextList": copy.deepcopy(
                source.get("_aggregateSourceChoiceTextList")
            ),
            "correctChoiceText": copy.deepcopy(
                source.get("_aggregateSourceCorrectChoiceText")
            ),
            "answerResultText": copy.deepcopy(
                source.get("_aggregateSourceAnswerResultText")
            ),
        }
    return evidence


def _trusted_source_answer_evidence(
    source_record: Mapping[str, Any],
    target: Mapping[str, Any],
    current_record: Mapping[str, Any],
    question_issue_correction_evidence: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any] | None:
    """Build prompt-only final verdict evidence from a trusted judge table."""

    source = dict(source_record)
    if uses_trusted_gassyunin_judge_answers(source):
        evidence_type = "trusted_gassyunin_judge_statement_verdicts"
    elif uses_official_firestore_statement_answers(source):
        evidence_type = "official_firestore_snapshot_statement_verdicts"
    else:
        return None
    source_body = source.get("questionBodyText")
    source_choices = source.get("choiceTextList")
    current_body = current_record.get("questionBodyText")
    current_choices = current_record.get("choiceTextList")
    exact_source_text = bool(
        source_body == current_body
        and source_choices == current_choices
    )
    corrected_fields: set[str] = set()
    if source_body != current_body:
        corrected_fields.add("questionBodyText")
    if source_choices != current_choices:
        corrected_fields.add("choiceTextList")
    source_question_key = str(target.get("sourceQuestionKey") or "")
    source_record_ref = SourceIdentityBinding.from_mapping(
        target
    ).source_record_ref
    official_correction_applies = bool(
        not exact_source_text
        and corrected_fields
        and isinstance(source_choices, list)
        and isinstance(current_choices, list)
        and len(source_choices) == len(current_choices)
        and isinstance(source.get("correctChoiceText"), list)
        and len(source["correctChoiceText"]) == len(current_choices)
        and any(
            corrected_fields.issubset(
                {
                    str(field)
                    for field in correction.get("changedFields") or []
                    if field
                }
            )
            and (
                not source_question_key
                or str(correction.get("sourceQuestionKey") or "")
                == source_question_key
            )
            and (
                not source_record_ref
                or str(correction.get("sourceRecordRef") or "")
                == source_record_ref
            )
            and any(
                isinstance(item, Mapping)
                and str(item.get("sourceClass") or "") == "official"
                and str(item.get("locator") or "").strip()
                and str(item.get("contentHash") or "").strip()
                for item in correction.get("evidence") or []
            )
            for correction in question_issue_correction_evidence
            if isinstance(correction, Mapping)
        )
    )
    if asks_for_selected_choice_count(source_body):
        answer_result_semantics = (
            "count_choice_index_with_all_correct_sentinel"
            if all_correct_choice_sentinel_number(source_body) is not None
            else "selected_statement_count"
        )
    elif asks_for_combination_choice(source_body):
        answer_result_semantics = "source_combination_choice_index"
    else:
        answer_result_semantics = "source_choice_index"
    return {
        "evidenceType": evidence_type,
        "verdictSemantics": "final_correct_choice_text_for_source_text",
        "answerResultSemantics": answer_result_semantics,
        "appliesToCurrentText": (
            exact_source_text or official_correction_applies
        ),
        "applicationBasis": (
            "exact_source_text"
            if exact_source_text
            else "official_question_content_correction"
            if official_correction_applies
            else "source_text_changed_without_verified_mapping"
        ),
        "sourceRecordRef": SourceIdentityBinding.from_mapping(
            target
        ).source_record_ref,
        "questionBodyText": copy.deepcopy(source_body),
        "choiceTextList": copy.deepcopy(source_choices),
        "correctChoiceText": copy.deepcopy(source.get("correctChoiceText")),
        "answerResultText": copy.deepcopy(source.get("answer_result_text")),
        "judgeChoiceMarkers": copy.deepcopy(source.get("judgeChoiceMarkers")),
        "sourceStatementCount": source.get("sourceStatementCount"),
    }


def _maintenance_session_phases(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_stage_plans = plan.get("stagePlans")
    stage_plans = (
        [dict(value) for value in raw_stage_plans if isinstance(value, Mapping)]
        if isinstance(raw_stage_plans, list) and raw_stage_plans
        else [dict(plan)]
    )
    completed_scope_stage_ids = {
        str(value)
        for value in plan.get("resumeCompletedScopeStageIds") or []
        if value
    }
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
            if stage_id in completed_scope_stage_ids:
                continue
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


def _question_phase_completion(
    executions: Iterable[Mapping[str, Any]],
    stage_id: str,
) -> dict[str, Any]:
    states = [
        stage
        for question in executions
        if isinstance(question, Mapping)
        for stage in question.get("stages") or []
        if isinstance(stage, Mapping)
        and str(stage.get("stageId") or "") == stage_id
    ]
    validated = sum(stage.get("status") == "validated" for stage in states)
    blocked = sum(stage.get("status") == "blocked" for stage in states)
    not_applicable = sum(
        stage.get("status") == "not_applicable" for stage in states
    )
    pending = len(states) - validated - blocked - not_applicable
    status = (
        "pending"
        if pending
        else "partial"
        if blocked
        else "skipped"
        if not states or not_applicable == len(states)
        else "succeeded"
    )
    return {
        "status": status,
        "targetCount": len(states),
        "validatedCount": validated,
        "notApplicableCount": not_applicable,
        "blockedCount": blocked,
        "pendingCount": pending,
        "receiptValidated": validated > 0,
        "artifactSync": {
            "status": "deferred" if validated else "not_required",
            "groups": [],
        },
        "error": f"{blocked}問を理由付きで保留しました。" if blocked else None,
    }


class QualificationRunStore:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()
        self.root = self.repo_root / "output" / "question_review_console" / "workflow_runs"
        self.question_states = QuestionRunStateStore(self.repo_root)
        # `_lock` protects only lock registries and startup recovery. Runtime
        # manifest I/O uses a lock per path so independent question turns do
        # not serialize behind unrelated run files.
        self._lock = threading.RLock()
        self._cache_lock = threading.RLock()
        self._aggregate_checkpoint_cache_lock = threading.RLock()
        self._path_locks: weakref.WeakValueDictionary[Path, Any] = (
            weakref.WeakValueDictionary()
        )
        self._manifest_cache: dict[
            Path,
            tuple[tuple[int, int, int], dict[str, Any]],
        ] = {}
        self._manifest_header_cache: dict[
            Path,
            tuple[tuple[int, int, int], bool],
        ] = {}
        self._manifest_list_summary_cache: dict[
            Path,
            tuple[
                tuple[
                    tuple[int, int, int],
                    tuple[int, int, int, bool] | None,
                ],
                dict[str, Any],
            ],
        ] = {}
        self._dashboard_run_index_cache: dict[str, list[dict[str, Any]]] = {}
        self._aggregate_checkpoint_cache: dict[
            Path,
            dict[str, dict[str, Any]],
        ] = {}
        self._technical_log_sequences: dict[Path, int] = {}
        self._technical_log_last_signatures: dict[Path, str] = {}

    def recover_interrupted_runs(self) -> None:
        """Recover runs only after the UI process owns the server lease."""

        self._recover_interrupted_runs()
        # Recovery reads only active sidecars. Drop those manifests from the
        # runtime cache so later requests observe the recovered state.
        self._manifest_cache.clear()

    def recover_interrupted_question_run_for_resume(
        self,
        qualification: str,
        run_id: str,
    ) -> dict[str, Any]:
        """Normalize a current question run whose terminal manifest stayed live."""

        manifest_path = self.root / qualification / run_id / "manifest.json"
        manifest = self._load_manifest(manifest_path)
        if not self.question_states.is_current(manifest):
            raise QualificationRunError(
                "現行の一問stateを持たないrunは再開できません。"
                "新規runとして開始してください。"
            )
        if (
            str(manifest.get("status") or "") == "interrupted"
            and str(manifest.get("queueStatus") or "") == "running"
            and manifest.get("retrySafe") is not False
        ):
            self._recover_question_run(manifest_path, manifest)
        return self.get(qualification, run_id)

    def _path_lock(self, path: Path) -> Any:
        """Return one re-entrant lock for an exact persisted path."""

        normalized = path.resolve()
        with self._lock:
            lock = self._path_locks.get(normalized)
            if lock is None:
                lock = threading.RLock()
                self._path_locks[normalized] = lock
            return lock

    @staticmethod
    def _parse_question_attempt_id(
        run_id: str,
    ) -> tuple[str, str, str] | None:
        if not str(run_id).startswith("qa-"):
            return None
        try:
            parent_prefix, question_hash, token = str(run_id).rsplit("-", 2)
        except ValueError:
            return None
        parent_run_id = parent_prefix.removeprefix("qa-")
        if (
            not parent_run_id
            or not re.fullmatch(r"[A-Za-z0-9_-]+", parent_run_id)
            or not re.fullmatch(r"[0-9a-f]{64}", question_hash)
            or not re.fullmatch(r"[0-9a-f]{16}", token)
        ):
            return None
        return parent_run_id, question_hash, token

    def is_question_attempt(self, run_id: str) -> bool:
        return self._parse_question_attempt_id(run_id) is not None

    def _question_attempt_context(
        self,
        qualification: str,
        attempt_id: str,
    ) -> tuple[Path, dict[str, Any], str, dict[str, Any]]:
        parsed = self._parse_question_attempt_id(attempt_id)
        if parsed is None:
            raise QualificationRunError("一問attempt IDが不正です。")
        parent_run_id, question_hash, _token = parsed
        parent_path = self._manifest_path(qualification, parent_run_id)
        with self._path_lock(parent_path):
            parent = self._load_manifest(parent_path)
        if not self.question_states.is_current(parent):
            raise QualificationRunError(
                "一問attemptの親runが現行形式ではありません。"
            )
        try:
            state = self.question_states.load_question_by_hash(
                parent_path.parent,
                parent,
                question_hash,
            )
        except QuestionRunStateError as exc:
            raise QualificationRunError(str(exc)) from exc
        question_id = str(state["questionId"])
        attempts = state.get("attemptArtifacts")
        attempt = (
            attempts.get(attempt_id)
            if isinstance(attempts, Mapping)
            else None
        )
        if not isinstance(attempt, Mapping):
            raise QualificationRunError(
                "一問attemptの永続記録がありません。"
            )
        return parent_path, parent, question_id, copy.deepcopy(dict(attempt))

    def create_question_attempt(
        self,
        qualification: str,
        parent_run_id: str,
        question_id: str,
        stage_id: str,
        plan: Mapping[str, Any],
        prompt: str,
    ) -> dict[str, Any]:
        parent_path = self._manifest_path(qualification, parent_run_id)
        with self._path_lock(parent_path):
            parent = self._load_manifest(parent_path)
        if not self.question_states.is_current(parent):
            raise QualificationRunError(
                "一問attemptは現行のmaintenance runだけで使用できます。"
            )
        question_hash = question_state_filename(question_id).removesuffix(
            ".json"
        )
        token = secrets.token_hex(8)
        attempt_id = f"qa-{parent_run_id}-{question_hash}-{token}"
        attempt_dir = parent_path.parent / "attempts" / token
        attempt_dir.mkdir(parents=True, exist_ok=False)
        now = _now()
        attempt_plan = copy.deepcopy(dict(plan))
        attempt_plan["progressStages"] = [
            {
                "id": str(stage.get("stageId") or ""),
                "code": str(stage.get("stageCode") or ""),
                "label": str(stage.get("stageLabel") or ""),
            }
            for stage in attempt_plan.get("stagePlans")
            or [attempt_plan]
            if str(stage.get("stageId") or "")
        ]
        attempt = {
            "attemptId": attempt_id,
            "questionId": question_id,
            "stageId": stage_id,
            "parentRunId": parent_run_id,
            "status": "queued",
            "createdAt": now,
            "updatedAt": now,
            "startedAt": None,
            "finishedAt": None,
            "prompt": str(prompt),
            "plan": attempt_plan,
            "resultReceiptPath": str(
                (attempt_dir / "result.json").relative_to(self.repo_root)
            ),
            "progressReceiptPath": str(
                (attempt_dir / "progress.jsonl").relative_to(self.repo_root)
            ),
            "artifactDirectory": str(
                attempt_dir.relative_to(self.repo_root)
            ),
            "result": None,
        }
        question_path = self.question_states.question_path(
            parent_path.parent,
            parent,
            question_id,
        )
        with self._path_lock(question_path):

            def add_attempt(state: dict[str, Any]) -> None:
                attempts = state.setdefault("attemptArtifacts", {})
                if not isinstance(attempts, dict):
                    raise QuestionRunStateError(
                        "一問stateのattemptArtifactsが不正です。"
                    )
                if attempt_id in attempts:
                    raise QuestionRunStateError(
                        "一問attempt IDが重複しています。"
                    )
                attempts[attempt_id] = copy.deepcopy(attempt)
                state["activeAttemptId"] = attempt_id

            try:
                self.question_states.update_question(
                    parent_path.parent,
                    parent,
                    question_id,
                    add_attempt,
                )
            except QuestionRunStateError as exc:
                shutil.rmtree(attempt_dir, ignore_errors=True)
                raise QualificationRunError(str(exc)) from exc
        return self._question_attempt_facade(attempt)

    @staticmethod
    def _question_attempt_facade(
        attempt: Mapping[str, Any],
    ) -> dict[str, Any]:
        plan = attempt.get("plan")
        if not isinstance(plan, Mapping):
            raise QualificationRunError("一問attemptのplanが不正です。")
        runtime = {
            str(key): copy.deepcopy(value)
            for key, value in attempt.items()
            if key not in {"plan", "prompt"}
        }
        return {
            **copy.deepcopy(dict(plan)),
            **runtime,
            "runId": str(attempt.get("attemptId") or ""),
            "parentRunId": str(attempt.get("parentRunId") or ""),
            "promptPath": None,
            "technicalLogPath": None,
        }

    def _update_question_attempt(
        self,
        qualification: str,
        attempt_id: str,
        changes: Mapping[str, Any],
    ) -> dict[str, Any]:
        parent_path, parent, question_id, _attempt = (
            self._question_attempt_context(
                qualification,
                attempt_id,
            )
        )
        question_path = self.question_states.question_path(
            parent_path.parent,
            parent,
            question_id,
        )
        with self._path_lock(question_path):

            def apply(state: dict[str, Any]) -> None:
                attempts = state.get("attemptArtifacts")
                if not isinstance(attempts, dict):
                    raise QuestionRunStateError(
                        "一問stateのattemptArtifactsが不正です。"
                    )
                current = attempts.get(attempt_id)
                if not isinstance(current, dict):
                    raise QuestionRunStateError(
                        "一問attemptの永続記録がありません。"
                    )
                if str(current.get("status") or "") in {
                    "succeeded",
                    "failed",
                    "interrupted",
                }:
                    non_idempotent = {
                        str(key): value
                        for key, value in changes.items()
                        if current.get(key) != value
                    }
                    if non_idempotent:
                        raise QuestionRunStateError(
                            "終端済みの一問attemptは変更できません。"
                        )
                    return
                immutable = {
                    key: copy.deepcopy(current.get(key))
                    for key in (
                        "attemptId",
                        "questionId",
                        "stageId",
                        "parentRunId",
                        "createdAt",
                        "plan",
                        "prompt",
                        "artifactDirectory",
                        "resultReceiptPath",
                        "progressReceiptPath",
                    )
                }
                for write_once_field in (
                    "preparedCandidate",
                    "patchApplyStartedAt",
                ):
                    if (
                        write_once_field in changes
                        and write_once_field in current
                        and current[write_once_field] != changes[write_once_field]
                    ):
                        raise QuestionRunStateError(
                            f"一問attemptの{write_once_field}は変更できません。"
                        )
                current.update(copy.deepcopy(dict(changes)))
                if any(current.get(key) != value for key, value in immutable.items()):
                    raise QuestionRunStateError(
                        "一問attemptのimmutable fieldは変更できません。"
                    )
                current["updatedAt"] = _now()
                if current.get("status") in {"succeeded", "failed"}:
                    current["finishedAt"] = (
                        current.get("finishedAt") or current["updatedAt"]
                    )

            try:
                state = self.question_states.update_question(
                    parent_path.parent,
                    parent,
                    question_id,
                    apply,
                )
            except QuestionRunStateError as exc:
                raise QualificationRunError(str(exc)) from exc
        attempts = state.get("attemptArtifacts") or {}
        return self._question_attempt_facade(attempts[attempt_id])

    def run_directory(self, qualification: str, run_id: str) -> Path:
        if not self.is_question_attempt(run_id):
            return self.root / _safe_segment(qualification) / _safe_segment(
                run_id
            )
        parent_path, _parent, _question_id, attempt = (
            self._question_attempt_context(qualification, run_id)
        )
        return self._question_attempt_directory(parent_path, attempt)

    def _question_attempt_directory(
        self,
        parent_path: Path,
        attempt: Mapping[str, Any],
    ) -> Path:
        path = (
            self.repo_root / str(attempt.get("artifactDirectory") or "")
        ).resolve()
        if path.parent != parent_path.parent / "attempts":
            raise QualificationRunError(
                "一問attemptのartifact directoryが不正です。"
            )
        return path

    def update_attempt_stage_status(
        self,
        qualification: str,
        attempt_id: str,
        status: str,
    ) -> dict[str, Any]:
        parent_path, parent, question_id, attempt = (
            self._question_attempt_context(
                qualification,
                attempt_id,
            )
        )
        stage_id = str(attempt.get("stageId") or "")
        if status not in {"preparing", "prepared", "committing"}:
            raise QualificationRunError(
                "一問attemptの工程状態が不正です。"
            )
        return self._update_current_question_stages(
            parent_path,
            parent,
            [
                {
                    "questionId": question_id,
                    "stageId": stage_id,
                    "changes": {
                        "status": status,
                        "error": None,
                    },
                }
            ],
            [(question_id, stage_id)],
            refresh_derived=False,
            hydrate_result=False,
        )

    def persist_prepared_candidate(
        self,
        qualification: str,
        attempt_id: str,
        candidate: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not self.is_question_attempt(attempt_id):
            raise QualificationRunError(
                "問題別候補は現行の一問attemptにだけ保存できます。"
            )
        _parent_path, _parent, question_id, attempt = (
            self._question_attempt_context(
                qualification,
                attempt_id,
            )
        )
        validated = _validated_prepared_candidate(
            candidate,
            question_id=question_id,
            stage_id=str(attempt.get("stageId") or ""),
        )
        updated = self._update_question_attempt(
            qualification,
            attempt_id,
            {"preparedCandidate": validated},
        )
        persisted = updated.get("preparedCandidate")
        if not isinstance(persisted, Mapping):
            raise QualificationRunError(
                "問題別候補をattemptへ保存できませんでした。"
            )
        readback = _validated_prepared_candidate(
            persisted,
            question_id=question_id,
            stage_id=str(attempt.get("stageId") or ""),
        )
        if readback != validated:
            raise QualificationRunError(
                "問題別候補の保存後readbackが一致しません。"
            )
        return readback

    def load_prepared_candidate(
        self,
        qualification: str,
        attempt_id: str,
        *,
        input_fingerprint_value: str,
        projected_input_hash: str,
    ) -> dict[str, Any]:
        if not self.is_question_attempt(attempt_id):
            raise QualificationRunError(
                "保存済み候補は現行の一問attemptからだけ読み取れます。"
            )
        _parent_path, _parent, question_id, attempt = (
            self._question_attempt_context(
                qualification,
                attempt_id,
            )
        )
        candidate = attempt.get("preparedCandidate")
        if not isinstance(candidate, Mapping):
            raise QualificationRunError(
                "一問attemptに保存済み問題別候補がありません。"
            )
        return _validated_prepared_candidate(
            candidate,
            question_id=question_id,
            stage_id=str(attempt.get("stageId") or ""),
            input_fingerprint_value=input_fingerprint_value,
            projected_input_hash=projected_input_hash,
        )

    def mark_patch_apply_started(
        self,
        qualification: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        current = self.get(qualification, attempt_id)
        if not isinstance(current.get("preparedCandidate"), Mapping):
            raise QualificationRunError(
                "保存済み問題別候補なしでpatch反映を開始できません。"
            )
        if current.get("patchApplyStartedAt"):
            raise QualificationRunError(
                "同じ問題別候補のpatch反映は再実行できません。"
            )
        if not self.is_question_attempt(attempt_id):
            raise QualificationRunError(
                "patch反映は現行の一問attemptでだけ開始できます。"
            )
        return self._update_question_attempt(
            qualification,
            attempt_id,
            {"patchApplyStartedAt": _now()},
        )

    def reusable_prepared_candidate(
        self,
        qualification: str,
        parent_run_id: str,
        question_id: str,
        stage_id: str,
        *,
        input_fingerprint_value: str,
        projected_input_hash: str,
    ) -> tuple[str, dict[str, Any]] | None:
        parent = self.get_compact(qualification, parent_run_id)
        if str(parent.get("status") or "") != "interrupted":
            return None
        detail = self.question_detail(
            qualification,
            parent_run_id,
            question_id,
        )
        attempts = [
            (str(attempt_id), dict(attempt))
            for attempt_id, attempt in (
                detail.get("attemptArtifacts") or {}
            ).items()
            if isinstance(attempt, Mapping)
            and str(attempt.get("stageId") or "") == stage_id
            and str(attempt.get("status") or "") == "interrupted"
            and not attempt.get("patchApplyStartedAt")
            and isinstance(attempt.get("preparedCandidate"), Mapping)
        ]
        for attempt_id, attempt in sorted(
            attempts,
            key=lambda item: (
                str(item[1].get("createdAt") or ""),
                item[0],
            ),
            reverse=True,
        ):
            try:
                candidate = _validated_prepared_candidate(
                    attempt["preparedCandidate"],
                    question_id=question_id,
                    stage_id=stage_id,
                    input_fingerprint_value=input_fingerprint_value,
                    projected_input_hash=projected_input_hash,
                )
            except QualificationRunError:
                continue
            return attempt_id, candidate
        return None

    def reusable_prewrite_candidate(
        self,
        qualification: str,
        parent_run_id: str,
        attempt_id: str,
        question_id: str,
        stage_id: str,
        *,
        input_fingerprint_value: str,
        projected_input_hash: str,
    ) -> dict[str, Any] | None:
        """Reuse a candidate only after a safely closed prewrite contention."""

        if not self.is_question_attempt(attempt_id):
            return None
        try:
            _parent_path, parent, bound_question_id, attempt = (
                self._question_attempt_context(
                    qualification,
                    attempt_id,
                )
            )
        except QualificationRunError:
            return None
        rollback = attempt.get("rollback")
        result = attempt.get("result")
        batch_results = attempt.get("batchQuestionResults")
        if (
            str(parent.get("runId") or "") != parent_run_id
            or str(attempt.get("parentRunId") or "") != parent_run_id
            or bound_question_id != question_id
            or str(attempt.get("questionId") or "") != question_id
            or str(attempt.get("stageId") or "") != stage_id
            or attempt.get("canonicalWriteStarted") is not False
            or attempt.get("candidateTransactionOpen") is True
            or attempt.get("deltaUnknown") is True
            or attempt.get("writeAttributionVerified") is not True
            or not isinstance(rollback, Mapping)
            or rollback.get("status") != "not_required"
            or rollback.get("deltaUnknown") is True
            or rollback.get("remainingChangedFiles")
            or not isinstance(result, Mapping)
            or result.get("status") != "succeeded"
            or not isinstance(batch_results, list)
            or len(batch_results) != 1
            or not isinstance(batch_results[0], Mapping)
            or batch_results[0].get("status") != "failed"
            or not any(
                isinstance(command, Mapping)
                and command.get("command") == "canonical prewrite validation"
                and command.get("status") == "fail"
                for command in batch_results[0].get("commands") or []
            )
            or not isinstance(attempt.get("preparedCandidate"), Mapping)
        ):
            return None
        try:
            return _validated_prepared_candidate(
                attempt["preparedCandidate"],
                question_id=question_id,
                stage_id=stage_id,
                input_fingerprint_value=input_fingerprint_value,
                projected_input_hash=projected_input_hash,
            )
        except QualificationRunError:
            return None

    def create(
        self,
        plan: Mapping[str, Any],
        *,
        status: str,
        prompt: str | None = None,
        resumed_from: str | None = None,
        append_receipt_contract: bool = True,
        hydrate_result: bool = True,
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
                    "reviewKey": str(raw_target.get("reviewKey") or "")[:1000],
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
            official_target_ids = {
                target_resolver.official_id(target)
                for target in target_resolver.targets
            }
            policy_targets: dict[str, list[str]] = {}
            for stage_id, raw_values in (plan.get("policyTargets") or {}).items():
                if not isinstance(raw_values, list):
                    raise RunTargetIdentityError(
                        f"{stage_id}のpolicyTargetsがlistではありません。"
                    )
                normalized: list[str] = []
                for raw_value in raw_values:
                    target_id = (
                        str(raw_value).strip()
                        if isinstance(raw_value, str)
                        else ""
                    )
                    if target_id not in official_target_ids:
                        raise RunTargetIdentityError(
                            f"{stage_id}のpolicy targetが現在の問題IDではありません。"
                        )
                    normalized.append(target_id)
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
            "lawWorkflowEnabled": bool(plan.get("lawWorkflowEnabled", True)),
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
            "phaseExecutions": copy.deepcopy(
                list(plan.get("phaseExecutions") or [])
            ),
            "currentPhaseId": plan.get("currentPhaseId"),
            "childRunIds": list(plan.get("childRunIds") or []),
            "questionExecutions": question_executions,
            "questionExecutionSummary": question_execution_summary,
            "queueStatus": plan.get("queueStatus"),
            "queueOrder": plan.get("queueOrder"),
            "retrySafe": bool(plan.get("retrySafe", True)),
            "retryUnsafeReason": plan.get("retryUnsafeReason"),
            "unsafeChildRunId": plan.get("unsafeChildRunId"),
            "status": status,
            "targetCount": int(plan["targetCount"]),
            "workItemCount": int(plan.get("workItemCount") or plan["targetCount"]),
            "targetIdentity": copy.deepcopy(plan.get("targetIdentity")),
            "previewPlanHash": plan.get("previewPlanHash"),
            "targetGroupIds": list(plan.get("targetGroupIds") or []),
            "scopeListGroupId": plan.get("scopeListGroupId"),
            "scopeListGroupIds": list(plan.get("scopeListGroupIds") or []),
            "questionIds": list(plan.get("questionIds") or []),
            "updateTargets": copy.deepcopy(list(plan.get("updateTargets") or [])),
            "selectedUpdateTargets": copy.deepcopy(
                list(plan.get("selectedUpdateTargets") or [])
            ),
            "selectedUpdateTargetIds": list(
                plan.get("selectedUpdateTargetIds") or []
            ),
            "selectedFieldsByStage": {
                str(stage_id): list(fields)
                for stage_id, fields in (
                    plan.get("selectedFieldsByStage") or {}
                ).items()
            },
            "readFieldsByStage": {
                str(stage_id): list(fields)
                for stage_id, fields in (
                    plan.get("readFieldsByStage") or {}
                ).items()
            },
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
            "throughputMode": plan.get("throughputMode"),
            "adaptiveScheduler": copy.deepcopy(plan.get("adaptiveScheduler")),
            "modelBatchSize": (
                int(plan["modelBatchSize"])
                if plan.get("modelBatchSize") is not None
                else None
            ),
            "modelWorkerLimit": (
                int(plan["modelWorkerLimit"])
                if plan.get("modelWorkerLimit") is not None
                else None
            ),
            "questionConcurrency": (
                int(plan["questionConcurrency"])
                if plan.get("questionConcurrency") is not None
                else None
            ),
            "speedMode": normalize_speed_mode(
                plan.get("speedMode") or STANDARD_SPEED_MODE
            ),
            "requestedServiceTier": plan.get("requestedServiceTier"),
            "parallelWorkerLimit": int(plan.get("parallelWorkerLimit") or 1),
            "writeWorkerLimit": int(plan.get("writeWorkerLimit") or 1),
            "executionPhase": "queued",
            "preparationProgress": None,
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
            "parentSourceChecked": bool(plan.get("parentSourceChecked")),
            "failedDeltaReconciliation": bool(
                plan.get("failedDeltaReconciliation")
            ),
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
        manifest_path = run_dir / "manifest.json"
        with self._path_lock(manifest_path):
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
                            include_progress=bool(manifest["progressTargets"]),
                        )
                        if append_receipt_contract
                        else prompt.rstrip() + "\n"
                    ),
                    encoding="utf-8",
                )
                manifest["promptPath"] = str(prompt_path.relative_to(self.repo_root))
            if (
                manifest.get("workType") == "maintenance_flow"
                and question_executions
            ):
                try:
                    manifest = self.question_states.initialize(
                        run_dir,
                        plan,
                        manifest,
                    )
                except QuestionRunStateError as exc:
                    raise QualificationRunError(str(exc)) from exc
            self._write_manifest(manifest_path, manifest)
        return (
            self._hydrate_question_run(manifest_path, manifest)
            if hydrate_result
            else self._public(manifest)
        )

    def append_technical_log(
        self,
        qualification: str,
        run_id: str,
        value: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """run配下の技術ログへ、許可fieldだけを一行追記する。"""

        manifest_path = self._manifest_path(qualification, run_id)
        with self._path_lock(manifest_path):
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

    def update(
        self,
        qualification: str,
        run_id: str,
        *,
        hydrate_result: bool = True,
        **changes: Any,
    ) -> dict[str, Any]:
        if self.is_question_attempt(run_id):
            return self._update_question_attempt(
                qualification,
                run_id,
                changes,
            )
        if "aggregateReviewCheckpoints" in changes:
            raise QualificationRunError(
                "aggregate review checkpointは問題別sidecar APIで更新してください。"
            )
        path = self._manifest_path(qualification, run_id)
        with self._path_lock(path):
            manifest = self._load_manifest(path)
            if self.question_states.is_current(manifest):
                work_version_receipt = changes.get("workVersionReceipt")
                changes = {
                    key: value
                    for key, value in changes.items()
                    if key not in PLAN_OWNED_FIELDS
                }
                if isinstance(work_version_receipt, Mapping):
                    changes["workVersionRecordedCount"] = int(
                        work_version_receipt.get("recordedCount") or 0
                    )
            manifest.update(changes)
            manifest["updatedAt"] = _now()
            if manifest.get("status") in {"succeeded", "failed"}:
                manifest["finishedAt"] = manifest.get("finishedAt") or manifest["updatedAt"]
            self._write_manifest(path, manifest)
        return (
            self._hydrate_question_run(path, manifest)
            if hydrate_result
            else self._public(manifest)
        )

    @staticmethod
    def _validate_aggregate_review_execution(
        execution: Mapping[str, Any],
        *,
        slot: int,
        signature: Mapping[str, Any],
    ) -> None:
        if (
            execution.get("reviewNumber") != slot
            or str(execution.get("model") or "")
            != str(signature.get("model") or "")
            or str(execution.get("reasoningEffort") or "")
            != str(signature.get("reasoningEffort") or "")
            or any(
                not isinstance(execution.get(field), str)
                or not str(execution.get(field) or "").strip()
                for field in ("threadId", "sessionId", "turnId")
            )
        ):
            raise QualificationRunError(
                "aggregate review execution evidenceが予約契約と一致しません。"
            )

    @staticmethod
    def _aggregate_checkpoint_slots(
        checkpoint: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        raw_slots = checkpoint.get("slots")
        if not isinstance(raw_slots, Mapping):
            raise QualificationRunError(
                "aggregate review checkpoint slotsの形式が不正です。"
            )
        if any(str(key) not in {"1", "2"} for key in raw_slots):
            raise QualificationRunError(
                "aggregate review checkpointに未知のslotがあります。"
            )
        slots: dict[str, dict[str, Any]] = {}
        for raw_key, raw_value in raw_slots.items():
            key = str(raw_key)
            if not isinstance(raw_value, Mapping):
                raise QualificationRunError(
                    "aggregate review slotの形式が不正です。"
                )
            value = copy.deepcopy(dict(raw_value))
            if value.get("slot") != int(key) or value.get("status") not in {
                "started",
                "resolved",
            }:
                raise QualificationRunError(
                    "aggregate review slotの番号又は状態が不正です。"
                )
            if value["status"] == "resolved" and (
                not isinstance(value.get("review"), Mapping)
                or not isinstance(value.get("execution"), Mapping)
            ):
                raise QualificationRunError(
                    "確定済みaggregate review slotの証拠が不正です。"
                )
            if value["status"] == "resolved":
                QualificationRunStore._validate_aggregate_review_execution(
                    value["execution"],
                    slot=int(key),
                    signature=checkpoint,
                )
            slots[key] = value
        return slots

    @staticmethod
    def _aggregate_checkpoint_signature(
        checkpoint: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            field: copy.deepcopy(checkpoint.get(field))
            for field in (
                "sourceHash",
                "candidateSetHash",
                "stableParentIdentity",
                "model",
                "reasoningEffort",
                "promptContractVersion",
            )
            if field in checkpoint
        }

    @staticmethod
    def _aggregate_checkpoint_path(
        parent_manifest_path: Path,
        question_id: str,
    ) -> Path:
        digest = hashlib.sha256(question_id.encode("utf-8")).hexdigest()
        return (
            parent_manifest_path.parent
            / "aggregate_review_checkpoints"
            / f"{digest}.json"
        )

    def _read_aggregate_checkpoint_sidecar(
        self,
        path: Path,
        question_id: str,
    ) -> object | dict[str, Any]:
        if not path.is_file():
            return _AGGREGATE_CHECKPOINT_MISSING
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QualificationRunError(
                "aggregate review checkpoint sidecarを読めません。"
            ) from exc
        if (
            not isinstance(value, Mapping)
            or value.get("schemaVersion") != AGGREGATE_REVIEW_CHECKPOINT_SCHEMA
            or value.get("questionId") != question_id
        ):
            raise QualificationRunError(
                "aggregate review checkpoint sidecarの形式が不正です。"
            )
        checkpoint = value.get("checkpoint")
        if not isinstance(checkpoint, Mapping):
            raise QualificationRunError(
                "aggregate review checkpoint sidecarの記録が不正です。"
            )
        return copy.deepcopy(dict(checkpoint))

    def _effective_aggregate_checkpoint(
        self,
        path: Path,
        question_id: str,
    ) -> dict[str, Any] | None:
        sidecar = self._read_aggregate_checkpoint_sidecar(path, question_id)
        if sidecar is _AGGREGATE_CHECKPOINT_MISSING:
            return None
        return (
            copy.deepcopy(dict(sidecar))
            if isinstance(sidecar, Mapping)
            else None
        )

    def _cache_aggregate_checkpoint(
        self,
        parent_manifest_path: Path,
        question_id: str,
        checkpoint: Mapping[str, Any] | None,
    ) -> None:
        with self._aggregate_checkpoint_cache_lock:
            cached = self._aggregate_checkpoint_cache.get(parent_manifest_path)
            if cached is None:
                return
            if checkpoint is None:
                cached.pop(question_id, None)
            else:
                cached[question_id] = copy.deepcopy(dict(checkpoint))

    def _write_aggregate_checkpoint_sidecar(
        self,
        parent_manifest_path: Path,
        path: Path,
        question_id: str,
        checkpoint: Mapping[str, Any] | None,
    ) -> None:
        if checkpoint is None:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                raise QualificationRunError(
                    "aggregate review checkpoint sidecarを削除できません。"
                ) from exc
            if path.exists() or path.is_symlink():
                raise QualificationRunError(
                    "aggregate review checkpoint sidecarの削除を再読検証できません。"
                )
            self._cache_aggregate_checkpoint(
                parent_manifest_path,
                question_id,
                None,
            )
            return
        self._write_json(
            path,
            {
                "schemaVersion": AGGREGATE_REVIEW_CHECKPOINT_SCHEMA,
                "questionId": question_id,
                "checkpoint": copy.deepcopy(dict(checkpoint)),
            },
        )
        persisted = self._read_aggregate_checkpoint_sidecar(path, question_id)
        expected = copy.deepcopy(dict(checkpoint))
        if persisted is _AGGREGATE_CHECKPOINT_MISSING or persisted != expected:
            raise QualificationRunError(
                "aggregate review checkpoint sidecarを再読検証できません。"
            )
        self._cache_aggregate_checkpoint(
            parent_manifest_path,
            question_id,
            expected,
        )

    def _aggregate_checkpoint_snapshot(
        self,
        parent_manifest_path: Path,
    ) -> dict[str, dict[str, Any]]:
        directory = parent_manifest_path.parent / "aggregate_review_checkpoints"
        if not directory.is_dir():
            return {}
        with self._aggregate_checkpoint_cache_lock:
            cached = self._aggregate_checkpoint_cache.get(parent_manifest_path)
            if cached is not None:
                return copy.deepcopy(cached)
            effective: dict[str, dict[str, Any]] = {}
            for sidecar_path in directory.glob("*.json"):
                try:
                    value = json.loads(sidecar_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise QualificationRunError(
                        "aggregate review checkpoint sidecarを読めません。"
                    ) from exc
                if (
                    not isinstance(value, Mapping)
                    or value.get("schemaVersion")
                    != AGGREGATE_REVIEW_CHECKPOINT_SCHEMA
                    or not isinstance(value.get("questionId"), str)
                ):
                    raise QualificationRunError(
                        "aggregate review checkpoint sidecarの形式が不正です。"
                    )
                question_id = str(value["questionId"])
                checkpoint = value.get("checkpoint")
                if isinstance(checkpoint, Mapping):
                    effective[question_id] = copy.deepcopy(dict(checkpoint))
                else:
                    raise QualificationRunError(
                        "aggregate review checkpoint sidecarの記録が不正です。"
                    )
            self._aggregate_checkpoint_cache[parent_manifest_path] = effective
            return copy.deepcopy(effective)

    def _public_with_aggregate_checkpoints(
        self,
        path: Path,
        manifest: Mapping[str, Any],
    ) -> dict[str, Any]:
        value = self._hydrate_question_run(path, manifest)
        checkpoints = self._aggregate_checkpoint_snapshot(path)
        value.pop("aggregateReviewCheckpoints", None)
        if checkpoints:
            value["aggregateReviewCheckpoints"] = checkpoints
        return self._public(value)

    def _hydrate_question_run(
        self,
        path: Path,
        manifest: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not self.question_states.is_current(manifest):
            return copy.deepcopy(dict(manifest))
        try:
            return self.question_states.hydrate(path.parent, manifest)
        except QuestionRunStateError as exc:
            raise QualificationRunError(str(exc)) from exc

    def aggregate_review_checkpoint(
        self,
        qualification: str,
        parent_run_id: str,
        question_id: str,
    ) -> dict[str, Any] | None:
        return self.aggregate_review_checkpoints(
            qualification,
            parent_run_id,
            [question_id],
        )[question_id]

    def aggregate_review_checkpoints(
        self,
        qualification: str,
        parent_run_id: str,
        question_ids: list[str],
    ) -> dict[str, dict[str, Any] | None]:
        """Read independently persisted checkpoint shards."""

        if len(question_ids) != len(set(question_ids)):
            raise QualificationRunError(
                "同じ問題のaggregate review checkpointを重複取得できません。"
            )
        parent_path = self._manifest_path(qualification, parent_run_id)
        results: dict[str, dict[str, Any] | None] = {}
        for question_id in question_ids:
            sidecar_path = self._aggregate_checkpoint_path(
                parent_path,
                question_id,
            )
            with self._path_lock(sidecar_path):
                results[question_id] = self._effective_aggregate_checkpoint(
                    sidecar_path,
                    question_id,
                )
        return results

    def reserve_aggregate_review_slot(
        self,
        qualification: str,
        parent_run_id: str,
        question_id: str,
        signature: Mapping[str, Any],
        slot: int,
    ) -> dict[str, Any]:
        return self.reserve_aggregate_review_slots(
            qualification,
            parent_run_id,
            [(question_id, signature, slot)],
        )[question_id]

    def reserve_aggregate_review_slots(
        self,
        qualification: str,
        parent_run_id: str,
        requests: list[tuple[str, Mapping[str, Any], int]],
    ) -> dict[str, dict[str, Any]]:
        """Reserve one slot per question using independent checkpoint shards."""

        question_ids = [question_id for question_id, _signature, _slot in requests]
        if len(question_ids) != len(set(question_ids)):
            raise QualificationRunError(
                "同じ問題のaggregate review slotをbatch内で重複予約できません。"
            )
        if any(slot not in {1, 2} for _question_id, _signature, slot in requests):
            raise QualificationRunError("aggregate review slotは1又は2です。")
        parent_path = self._manifest_path(qualification, parent_run_id)
        sidecar_paths = {
            question_id: self._aggregate_checkpoint_path(
                parent_path,
                question_id,
            )
            for question_id in question_ids
        }
        with ExitStack() as locks:
            for path in sorted(set(sidecar_paths.values())):
                locks.enter_context(self._path_lock(path))
            results: dict[str, dict[str, Any]] = {}
            changed: dict[str, tuple[str, dict[str, Any]]] = {}
            for question_id, signature, slot in requests:
                sidecar_path = sidecar_paths[question_id]
                current = self._effective_aggregate_checkpoint(
                    sidecar_path,
                    question_id,
                )
                if current is None:
                    current = {
                        **copy.deepcopy(dict(signature)),
                        "slots": {},
                        "consensus": None,
                    }
                elif not isinstance(current, Mapping):
                    results[question_id] = {
                        "status": "mismatch",
                        "checkpoint": None,
                    }
                    continue
                else:
                    current = copy.deepcopy(dict(current))
                if self._aggregate_checkpoint_signature(current) != dict(signature):
                    results[question_id] = {
                        "status": "mismatch",
                        "checkpoint": current,
                    }
                    continue
                try:
                    slots = self._aggregate_checkpoint_slots(current)
                except QualificationRunError:
                    results[question_id] = {
                        "status": "mismatch",
                        "checkpoint": current,
                    }
                    continue
                key = str(slot)
                existing = slots.get(key)
                if existing is not None:
                    status = str(existing.get("status") or "")
                    results[question_id] = {
                        "status": (
                            "resolved" if status == "resolved" else "unresolved"
                        ),
                        "checkpoint": current,
                        "slot": copy.deepcopy(existing),
                    }
                    continue
                if len(slots) >= 2:
                    results[question_id] = {
                        "status": "limit",
                        "checkpoint": current,
                    }
                    continue
                slots[key] = {
                    "slot": slot,
                    "status": "started",
                    "reservedAt": _now(),
                }
                current["slots"] = slots
                changed[question_id] = (key, current)
                results[question_id] = {
                    "status": "reserved",
                    "checkpoint": current,
                    "slot": copy.deepcopy(slots[key]),
                }
            if not changed:
                return results
            for question_id, (_key, current) in changed.items():
                self._write_aggregate_checkpoint_sidecar(
                    parent_path,
                    sidecar_paths[question_id],
                    question_id,
                    current,
                )
            for question_id, (key, _current) in changed.items():
                persisted = self._effective_aggregate_checkpoint(
                    sidecar_paths[question_id],
                    question_id,
                )
                if not isinstance(persisted, Mapping):
                    raise QualificationRunError(
                        "aggregate review slot予約を再読検証できません。"
                    )
                persisted_slot = self._aggregate_checkpoint_slots(persisted).get(key)
                if not isinstance(persisted_slot, Mapping) or (
                    persisted_slot.get("status") != "started"
                ):
                    raise QualificationRunError(
                        "aggregate review slot予約を再読検証できません。"
                    )
                results[question_id] = {
                    "status": "reserved",
                    "checkpoint": copy.deepcopy(dict(persisted)),
                    "slot": copy.deepcopy(dict(persisted_slot)),
                }
            return results

    def cancel_unstarted_aggregate_review_slots(
        self,
        qualification: str,
        parent_run_id: str,
        cancellations: list[
            tuple[str, Mapping[str, Any], int, Mapping[str, Any]]
        ],
    ) -> None:
        """Cancel only slots reserved before a thread was started."""

        self._cancel_started_aggregate_review_slots(
            qualification,
            parent_run_id,
            cancellations,
        )

    def cancel_terminal_failed_aggregate_review_slots(
        self,
        qualification: str,
        parent_run_id: str,
        cancellations: list[
            tuple[str, Mapping[str, Any], int, Mapping[str, Any]]
        ],
    ) -> None:
        """Cancel unresolved slots after a protocol-confirmed terminal failure."""

        self._cancel_started_aggregate_review_slots(
            qualification,
            parent_run_id,
            cancellations,
        )

    def _cancel_started_aggregate_review_slots(
        self,
        qualification: str,
        parent_run_id: str,
        cancellations: list[
            tuple[str, Mapping[str, Any], int, Mapping[str, Any]]
        ],
    ) -> None:
        """Atomically cancel exact started slots without touching siblings."""

        question_ids = [value[0] for value in cancellations]
        if len(question_ids) != len(set(question_ids)):
            raise QualificationRunError(
                "同じ問題のaggregate review予約を重複取消できません。"
            )
        parent_path = self._manifest_path(qualification, parent_run_id)
        sidecar_paths = {
            question_id: self._aggregate_checkpoint_path(
                parent_path,
                question_id,
            )
            for question_id in question_ids
        }
        with ExitStack() as locks:
            for sidecar_path in sorted(set(sidecar_paths.values())):
                locks.enter_context(self._path_lock(sidecar_path))
            prepared: list[
                tuple[
                    str,
                    dict[str, Any],
                    dict[str, dict[str, Any]],
                    str,
                ]
            ] = []
            for question_id, signature, slot, expected_slot in cancellations:
                if slot not in {1, 2}:
                    raise QualificationRunError(
                        "aggregate review取消slotは1又は2です。"
                    )
                current = self._effective_aggregate_checkpoint(
                    sidecar_paths[question_id],
                    question_id,
                )
                if not isinstance(current, Mapping) or (
                    self._aggregate_checkpoint_signature(current) != dict(signature)
                ):
                    raise QualificationRunError(
                        "aggregate review予約取消signatureが一致しません。"
                    )
                current_copy = copy.deepcopy(dict(current))
                slots = self._aggregate_checkpoint_slots(current_copy)
                key = str(slot)
                actual_slot = slots.get(key)
                if (
                    not isinstance(actual_slot, Mapping)
                    or actual_slot.get("status") != "started"
                    or dict(actual_slot) != dict(expected_slot)
                ):
                    raise QualificationRunError(
                        "aggregate review予約取消対象が現在の予約と一致しません。"
                    )
                prepared.append((question_id, current_copy, slots, key))

            expected: dict[str, dict[str, Any] | None] = {}
            for question_id, current, slots, key in prepared:
                del slots[key]
                if not slots and current.get("consensus") is None:
                    expected[question_id] = None
                    continue
                current["slots"] = slots
                expected[question_id] = current
            for question_id, checkpoint in expected.items():
                self._write_aggregate_checkpoint_sidecar(
                    parent_path,
                    sidecar_paths[question_id],
                    question_id,
                    checkpoint,
                )
            for question_id, checkpoint in expected.items():
                persisted = self._effective_aggregate_checkpoint(
                    sidecar_paths[question_id],
                    question_id,
                )
                if persisted != checkpoint:
                    raise QualificationRunError(
                        "aggregate review予約取消を再読検証できません。"
                    )

    def resolve_aggregate_review_slot(
        self,
        qualification: str,
        parent_run_id: str,
        question_id: str,
        signature: Mapping[str, Any],
        slot: int,
        *,
        review: Mapping[str, Any],
        execution: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self.resolve_aggregate_review_slots(
            qualification,
            parent_run_id,
            [
                (
                    question_id,
                    signature,
                    slot,
                    review,
                    execution,
                )
            ],
        )[question_id]

    def resolve_aggregate_review_slots(
        self,
        qualification: str,
        parent_run_id: str,
        resolutions: list[
            tuple[
                str,
                Mapping[str, Any],
                int,
                Mapping[str, Any],
                Mapping[str, Any],
            ]
        ],
    ) -> dict[str, dict[str, Any]]:
        """Resolve review results in independent checkpoint shards."""

        question_ids = [value[0] for value in resolutions]
        if len(question_ids) != len(set(question_ids)):
            raise QualificationRunError(
                "同じ問題のaggregate review結果をbatch内で重複確定できません。"
            )
        parent_path = self._manifest_path(qualification, parent_run_id)
        sidecar_paths = {
            question_id: self._aggregate_checkpoint_path(
                parent_path,
                question_id,
            )
            for question_id in question_ids
        }
        with ExitStack() as locks:
            for sidecar_path in sorted(set(sidecar_paths.values())):
                locks.enter_context(self._path_lock(sidecar_path))
            prepared: list[
                tuple[
                    str,
                    dict[str, Any],
                    dict[str, dict[str, Any]],
                    str,
                    Mapping[str, Any],
                    Mapping[str, Any],
                ]
            ] = []
            for (
                question_id,
                signature,
                slot,
                review,
                execution,
            ) in resolutions:
                current = self._effective_aggregate_checkpoint(
                    sidecar_paths[question_id],
                    question_id,
                )
                if not isinstance(current, Mapping) or (
                    self._aggregate_checkpoint_signature(current) != dict(signature)
                ):
                    raise QualificationRunError(
                        "aggregate review checkpoint signatureが一致しません。"
                    )
                current_copy = copy.deepcopy(dict(current))
                slots = self._aggregate_checkpoint_slots(current_copy)
                key = str(slot)
                reserved = slots.get(key)
                if not isinstance(reserved, Mapping) or (
                    reserved.get("status") != "started"
                ):
                    raise QualificationRunError(
                        "開始済みaggregate review slotを確認できません。"
                    )
                self._validate_aggregate_review_execution(
                    execution,
                    slot=slot,
                    signature=signature,
                )
                prepared.append(
                    (
                        question_id,
                        current_copy,
                        slots,
                        key,
                        review,
                        execution,
                    )
                )
            for question_id, current, slots, key, review, execution in prepared:
                reserved = slots[key]
                slots[key] = {
                    **copy.deepcopy(dict(reserved)),
                    "status": "resolved",
                    "review": copy.deepcopy(dict(review)),
                    "execution": copy.deepcopy(dict(execution)),
                    "resolvedAt": _now(),
                }
                current["slots"] = slots
            if not prepared:
                return {}
            for question_id, current, _slots, _key, _review, _execution in prepared:
                self._write_aggregate_checkpoint_sidecar(
                    parent_path,
                    sidecar_paths[question_id],
                    question_id,
                    current,
                )
            results: dict[str, dict[str, Any]] = {}
            for question_id, _current, _slots, key, review, execution in prepared:
                persisted = self._effective_aggregate_checkpoint(
                    sidecar_paths[question_id],
                    question_id,
                )
                if not isinstance(persisted, Mapping):
                    raise QualificationRunError(
                        "aggregate review slot確定を再読検証できません。"
                    )
                persisted_slot = self._aggregate_checkpoint_slots(persisted).get(key)
                if (
                    not isinstance(persisted_slot, Mapping)
                    or persisted_slot.get("status") != "resolved"
                    or persisted_slot.get("review") != dict(review)
                    or persisted_slot.get("execution") != dict(execution)
                ):
                    raise QualificationRunError(
                        "aggregate review slot確定を再読検証できません。"
                    )
                results[question_id] = copy.deepcopy(dict(persisted))
            return results

    def store_aggregate_review_consensus(
        self,
        qualification: str,
        parent_run_id: str,
        question_id: str,
        signature: Mapping[str, Any],
        consensus: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self.store_aggregate_review_consensuses(
            qualification,
            parent_run_id,
            [(question_id, signature, consensus)],
        )[question_id]

    def store_aggregate_review_consensuses(
        self,
        qualification: str,
        parent_run_id: str,
        values: list[
            tuple[str, Mapping[str, Any], Mapping[str, Any]]
        ],
    ) -> dict[str, dict[str, Any]]:
        """Persist consensus values in independent checkpoint shards."""

        question_ids = [question_id for question_id, _signature, _value in values]
        if len(question_ids) != len(set(question_ids)):
            raise QualificationRunError(
                "同じ問題のaggregate review consensusを重複保存できません。"
            )
        parent_path = self._manifest_path(qualification, parent_run_id)
        sidecar_paths = {
            question_id: self._aggregate_checkpoint_path(
                parent_path,
                question_id,
            )
            for question_id in question_ids
        }
        with ExitStack() as locks:
            for sidecar_path in sorted(set(sidecar_paths.values())):
                locks.enter_context(self._path_lock(sidecar_path))
            prepared: list[tuple[str, dict[str, Any], Mapping[str, Any]]] = []
            for question_id, signature, consensus in values:
                current = self._effective_aggregate_checkpoint(
                    sidecar_paths[question_id],
                    question_id,
                )
                if not isinstance(current, Mapping) or (
                    self._aggregate_checkpoint_signature(current) != dict(signature)
                ):
                    raise QualificationRunError(
                        "aggregate review consensus signatureが一致しません。"
                    )
                current_copy = copy.deepcopy(dict(current))
                slots = self._aggregate_checkpoint_slots(current_copy)
                if set(slots) != {"1", "2"} or any(
                    value.get("status") != "resolved" for value in slots.values()
                ):
                    raise QualificationRunError(
                        "二つのaggregate review slot確定前にconsensusを保存できません。"
                    )
                prepared.append((question_id, current_copy, consensus))
            for question_id, current, consensus in prepared:
                current["consensus"] = copy.deepcopy(dict(consensus))
            if not prepared:
                return {}
            for question_id, current, _consensus in prepared:
                self._write_aggregate_checkpoint_sidecar(
                    parent_path,
                    sidecar_paths[question_id],
                    question_id,
                    current,
                )
            results: dict[str, dict[str, Any]] = {}
            for question_id, _current, consensus in prepared:
                persisted = self._effective_aggregate_checkpoint(
                    sidecar_paths[question_id],
                    question_id,
                )
                if (
                    not isinstance(persisted, Mapping)
                    or persisted.get("consensus") != dict(consensus)
                ):
                    raise QualificationRunError(
                        "aggregate review consensusを再読検証できません。"
                    )
                results[question_id] = copy.deepcopy(dict(persisted))
            return results

    def update_question_stage(
        self,
        qualification: str,
        run_id: str,
        question_id: str,
        stage_id: str,
        *,
        block_dependents: bool = False,
        validated_receipt: Mapping[str, Any] | None = None,
        refresh_derived: bool = True,
        hydrate_result: bool = True,
        **changes: Any,
    ) -> dict[str, Any]:
        return self.update_question_stages(
            qualification,
            run_id,
            [
                {
                    "questionId": question_id,
                    "stageId": stage_id,
                    "blockDependents": block_dependents,
                    "validatedReceipt": validated_receipt,
                    "changes": changes,
                }
            ],
            refresh_derived=refresh_derived,
            hydrate_result=hydrate_result,
        )

    def update_question_stages(
        self,
        qualification: str,
        run_id: str,
        updates: list[Mapping[str, Any]],
        *,
        refresh_derived: bool = True,
        hydrate_result: bool = True,
    ) -> dict[str, Any]:
        """Persist independent queue-stage changes with one manifest write."""

        if not updates:
            return (
                self.get(qualification, run_id)
                if hydrate_result
                else self.get_compact(qualification, run_id)
            )
        update_keys = [
            (
                str(value.get("questionId") or ""),
                str(value.get("stageId") or ""),
            )
            for value in updates
        ]
        if any(
            not question_id or not stage_id
            for question_id, stage_id in update_keys
        ):
            raise QualificationRunError("一問queueの一括更新対象が不正です。")
        if len(update_keys) != len(set(update_keys)):
            raise QualificationRunError("一問queueの同じ工程を重複更新できません。")
        path = self._manifest_path(qualification, run_id)
        with self._path_lock(path):
            manifest = self._load_manifest(path)
        if self.question_states.is_current(manifest):
            return self._update_current_question_stages(
                path,
                manifest,
                updates,
                update_keys,
                refresh_derived=refresh_derived,
                hydrate_result=hydrate_result,
            )
        with self._path_lock(path):
            manifest = self._load_manifest(path)
            executions = manifest.get("questionExecutions")
            if not isinstance(executions, list):
                raise QualificationRunError("一問queueの実行記録がありません。")
            questions = {
                str(value.get("questionId") or ""): value
                for value in executions
                if isinstance(value, dict) and value.get("questionId")
            }
            appended_child_run_ids: list[str] = []
            for raw_update, (question_id, stage_id) in zip(updates, update_keys):
                question = questions.get(question_id)
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
                        f"一問queueの対象工程がありません: "
                        f"{question_id} / {stage_id}"
                    )
                raw_changes = raw_update.get("changes") or {}
                if not isinstance(raw_changes, Mapping):
                    raise QualificationRunError("一問queueの一括更新内容が不正です。")
                changes = dict(raw_changes)
                appended_child_run_ids.extend(
                    str(value)
                    for value in changes.get("childRunIds") or []
                    if value
                )
                next_status = str(
                    changes.get("status")
                    or stages[stage_index].get("status")
                    or ""
                )
                if next_status not in WORK_ITEM_STATES:
                    raise QualificationRunError(
                        f"一問queueの工程状態が不正です: {next_status}"
                    )
                stages[stage_index].update(copy.deepcopy(changes))
                if next_status in {"validated", "not_applicable"}:
                    if question.get("listGroupId"):
                        list_group_id = str(question["listGroupId"])
                        manifest["confirmedGroupIds"] = sorted(
                            {
                                *(
                                    str(value)
                                    for value in manifest.get("confirmedGroupIds") or []
                                    if value
                                ),
                                list_group_id,
                            }
                        )
                    validated_receipt = raw_update.get("validatedReceipt")
                    if isinstance(validated_receipt, Mapping):
                        existing_receipt = manifest.get("workVersionReceipt")
                        receipt_items = [
                            dict(value)
                            for value in (
                                existing_receipt.get("items") or []
                                if isinstance(existing_receipt, Mapping)
                                else []
                            )
                            if isinstance(value, Mapping)
                        ]
                        candidate_receipt = dict(validated_receipt)
                        candidate_key = json.dumps(
                            candidate_receipt,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        if all(
                            json.dumps(
                                value,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            )
                            != candidate_key
                            for value in receipt_items
                        ):
                            receipt_items.append(candidate_receipt)
                        manifest["workVersionReceipt"] = {
                            "recordedCount": sum(
                                int(value.get("recordedCount") or 0)
                                for value in receipt_items
                            ),
                            "items": receipt_items,
                        }
                if raw_update.get("blockDependents"):
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
            if appended_child_run_ids:
                manifest["childRunIds"] = list(
                    dict.fromkeys(
                        [
                            *(
                                str(value)
                                for value in manifest.get("childRunIds") or []
                                if value
                            ),
                            *appended_child_run_ids,
                        ]
                    )
                )
                appended_stage_ids = {
                    stage_id
                    for _question_id, stage_id in update_keys
                }
                if len(appended_stage_ids) == 1:
                    active_stage_id = next(iter(appended_stage_ids))
                    manifest["currentPhaseId"] = active_stage_id
                    manifest["executionPhase"] = f"candidate:{active_stage_id}"
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

    def _update_current_question_stages(
        self,
        manifest_path: Path,
        manifest: Mapping[str, Any],
        updates: list[Mapping[str, Any]],
        update_keys: list[tuple[str, str]],
        *,
        refresh_derived: bool = True,
        hydrate_result: bool = True,
    ) -> dict[str, Any]:
        appended_child_run_ids: list[str] = []
        confirmed_group_ids: set[str] = set()
        for raw_update, (question_id, stage_id) in zip(updates, update_keys):
            question_path = self.question_states.question_path(
                manifest_path.parent,
                manifest,
                question_id,
            )
            with self._path_lock(question_path):

                def apply(state: dict[str, Any]) -> None:
                    execution = state.get("execution")
                    if not isinstance(execution, dict):
                        raise QuestionRunStateError(
                            "一問queueの実行記録がありません。"
                        )
                    stages = execution.get("stages")
                    if not isinstance(stages, list):
                        raise QuestionRunStateError(
                            "一問queueの工程記録がありません。"
                        )
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
                        raise QuestionRunStateError(
                            "一問queueの対象工程がありません: "
                            f"{question_id} / {stage_id}"
                        )
                    raw_changes = raw_update.get("changes") or {}
                    if not isinstance(raw_changes, Mapping):
                        raise QuestionRunStateError(
                            "一問queueの一括更新内容が不正です。"
                        )
                    changes = copy.deepcopy(dict(raw_changes))
                    appended_child_run_ids.extend(
                        str(value)
                        for value in changes.get("childRunIds") or []
                        if value
                    )
                    next_status = str(
                        changes.get("status")
                        or stages[stage_index].get("status")
                        or ""
                    )
                    if next_status not in WORK_ITEM_STATES:
                        raise QuestionRunStateError(
                            f"一問queueの工程状態が不正です: {next_status}"
                        )
                    stages[stage_index].update(changes)
                    if next_status in {"validated", "not_applicable"}:
                        if execution.get("listGroupId"):
                            confirmed_group_ids.add(
                                str(execution["listGroupId"])
                            )
                        validated_receipt = raw_update.get(
                            "validatedReceipt"
                        )
                        if isinstance(validated_receipt, Mapping):
                            receipts = state.setdefault(
                                "validatedReceipts",
                                {},
                            )
                            if not isinstance(receipts, dict):
                                raise QuestionRunStateError(
                                    "一問stateのvalidatedReceiptsが不正です。"
                                )
                            receipts[
                                validated_receipt_key(
                                    question_id,
                                    stage_id,
                                )
                            ] = copy.deepcopy(dict(validated_receipt))
                    if raw_update.get("blockDependents"):
                        reason = str(
                            changes.get("error")
                            or "前工程で停止しました。"
                        )
                        for dependent in stages[stage_index + 1 :]:
                            if not isinstance(dependent, dict):
                                continue
                            if str(dependent.get("status") or "") in {
                                "validated",
                                "not_applicable",
                            }:
                                continue
                            dependent.update(
                                status="blocked",
                                error=(
                                    f"前工程 {stage_id} の停止により保留: "
                                    f"{reason}"
                                ),
                                finishedAt=(
                                    changes.get("finishedAt") or _now()
                                ),
                            )
                    refresh_question_status(execution)
                    update_active_attempt_from_execution(state)

                try:
                    self.question_states.update_question(
                        manifest_path.parent,
                        manifest,
                        question_id,
                        apply,
                    )
                except QuestionRunStateError as exc:
                    raise QualificationRunError(str(exc)) from exc

        if not refresh_derived:
            with self._path_lock(manifest_path):
                current = self._load_manifest(manifest_path)
            return (
                self._hydrate_question_run(manifest_path, current)
                if hydrate_result
                else self._public(current)
            )

        return self._refresh_current_question_summary(
            manifest_path,
            manifest,
            confirmed_group_ids=confirmed_group_ids,
            appended_child_run_ids=appended_child_run_ids,
            appended_stage_ids={
                stage_id for _question_id, stage_id in update_keys
            },
            hydrate_result=hydrate_result,
        )

    def refresh_question_summary(
        self,
        qualification: str,
        run_id: str,
        *,
        hydrate_result: bool = True,
    ) -> dict[str, Any]:
        """Refresh the derived question queue summary at a coordinator boundary."""

        manifest_path = self._manifest_path(qualification, run_id)
        with self._path_lock(manifest_path):
            manifest = self._load_manifest(manifest_path)
        if not self.question_states.is_current(manifest):
            return (
                self.get(qualification, run_id)
                if hydrate_result
                else self.get_compact(qualification, run_id)
            )
        return self._refresh_current_question_summary(
            manifest_path,
            manifest,
            hydrate_result=hydrate_result,
        )

    def _refresh_current_question_summary(
        self,
        manifest_path: Path,
        manifest: Mapping[str, Any],
        *,
        confirmed_group_ids: Iterable[str] = (),
        appended_child_run_ids: Iterable[str] = (),
        appended_stage_ids: Iterable[str] = (),
        hydrate_result: bool = True,
    ) -> dict[str, Any]:
        summary_path = self.repo_root / str(
            manifest.get("questionSummaryPath") or ""
        )
        with self._path_lock(summary_path):
            try:
                summary_payload = self.question_states.rebuild_summary(
                    manifest_path.parent,
                    manifest,
                )
            except QuestionRunStateError as exc:
                raise QualificationRunError(str(exc)) from exc
        summary = dict(summary_payload["queueSummary"])
        confirmed_group_id_set = {
            str(value) for value in confirmed_group_ids if value
        }
        appended_child_run_id_list = [
            str(value) for value in appended_child_run_ids if value
        ]
        appended_stage_id_set = {
            str(value) for value in appended_stage_ids if value
        }
        with self._path_lock(manifest_path):
            current = self._load_manifest(manifest_path)
            current["confirmedGroupIds"] = sorted(
                {
                    *(
                        str(value)
                        for value in current.get("confirmedGroupIds") or []
                        if value
                    ),
                    *confirmed_group_id_set,
                }
            )
            if appended_child_run_id_list:
                if len(appended_stage_id_set) == 1:
                    active_stage_id = next(iter(appended_stage_id_set))
                    current["currentPhaseId"] = active_stage_id
                    current["executionPhase"] = (
                        f"candidate:{active_stage_id}"
                    )
            current.update(
                questionExecutionSummary=summary,
                blockedQuestionCount=summary["blockedQuestionCount"],
                blockedWorkItemCount=summary["blockedWorkItemCount"],
                validatedQuestionCount=summary["validatedQuestionCount"],
                validatedWorkItemCount=summary["validatedWorkItemCount"],
                updatedAt=_now(),
            )
            self._write_manifest(manifest_path, current)
        return (
            self._hydrate_question_run(manifest_path, current)
            if hydrate_result
            else self._public(current)
        )

    def list(
        self,
        qualification: str,
        *,
        limit: int = 8,
        top_level_only: bool = False,
        newest_updated_first: bool = False,
        summary_only: bool = False,
        excluded_work_types: Iterable[str] = (),
        excluded_schema_versions: Iterable[str] = (),
    ) -> list[dict[str, Any]]:
        qualification = _safe_segment(qualification)
        directory = self.root / qualification
        if not directory.is_dir():
            return []
        excluded_work_type_set = set(excluded_work_types)
        excluded_schema_version_set = set(excluded_schema_versions)
        paths = list(directory.glob("*/manifest.json"))
        paths.sort(
            key=(
                (lambda path: (path.stat().st_mtime_ns, str(path)))
                if newest_updated_first
                else (lambda path: str(path))
            ),
            reverse=True,
        )
        manifests: list[dict[str, Any]] = []
        for path in paths:
            with self._path_lock(path):
                if top_level_only and self._manifest_has_parent(path):
                    continue
                manifest = (
                    self._load_manifest_list_summary(path)
                    if summary_only
                    else self._load_manifest(path)
                )
                if manifest.get("workType") in excluded_work_type_set:
                    continue
                if manifest.get("schemaVersion") in excluded_schema_version_set:
                    continue
                if summary_only:
                    manifests.append(manifest)
                else:
                    manifest = self._apply_result_receipt(path, manifest)
                    manifests.append(self._public(manifest))
                if len(manifests) >= limit:
                    break
        return manifests

    def dashboard_runs(
        self,
        qualification: str,
        *,
        limit: int = 8,
        excluded_work_types: Iterable[str] = (),
        excluded_schema_versions: Iterable[str] = (),
    ) -> list[dict[str, Any]]:
        """Return the compact top-level run index used by the dashboard."""

        qualification = _safe_segment(qualification)
        index_path = self.root / qualification / "dashboard_runs.json"
        with self._path_lock(index_path):
            indexed = self._read_dashboard_run_index(qualification)
        if indexed is None:
            indexed = self.list(
                qualification,
                limit=min(max(limit, 1), DASHBOARD_RUN_INDEX_LIMIT),
                top_level_only=True,
                newest_updated_first=True,
                summary_only=True,
                excluded_work_types=DASHBOARD_RUN_EXCLUDED_WORK_TYPES,
                excluded_schema_versions=DASHBOARD_RUN_EXCLUDED_SCHEMA_VERSIONS,
            )
            with self._path_lock(index_path):
                self._write_dashboard_run_index(qualification, indexed)

        excluded_work_type_set = set(excluded_work_types)
        excluded_schema_version_set = set(excluded_schema_versions)
        return [
            copy.deepcopy(run)
            for run in indexed
            if run.get("workType") not in excluded_work_type_set
            and run.get("schemaVersion") not in excluded_schema_version_set
        ][:limit]

    def get(self, qualification: str, run_id: str) -> dict[str, Any]:
        if self.is_question_attempt(run_id):
            parent_path, _parent, _question_id, attempt = (
                self._question_attempt_context(
                    qualification,
                    run_id,
                )
            )
            return self._question_attempt_facade(attempt)
        path = self._manifest_path(qualification, run_id)
        with self._path_lock(path):
            manifest = self._load_manifest(path)
        return self._public_with_aggregate_checkpoints(path, manifest)

    def get_compact(
        self,
        qualification: str,
        run_id: str,
    ) -> dict[str, Any]:
        """Read only the mutable parent manifest without hydrating question state."""

        if self.is_question_attempt(run_id):
            return self.get(qualification, run_id)
        path = self._manifest_path(qualification, run_id)
        with self._path_lock(path):
            manifest = self._load_manifest(path)
        return self._public(manifest)

    def question_detail(
        self,
        qualification: str,
        run_id: str,
        question_id: str,
    ) -> dict[str, Any]:
        manifest_path = self._manifest_path(qualification, run_id)
        with self._path_lock(manifest_path):
            manifest = self._load_manifest(manifest_path)
        if not self.question_states.is_current(manifest):
            run = self._public_with_aggregate_checkpoints(
                manifest_path,
                manifest,
            )
            execution = next(
                (
                    copy.deepcopy(dict(value))
                    for value in run.get("questionExecutions") or []
                    if isinstance(value, Mapping)
                    and str(value.get("questionId") or "") == question_id
                ),
                None,
            )
            if execution is None:
                raise QualificationRunError(
                    "指定した問題の実行記録がありません。"
                )
            return {
                "schemaVersion": "question-maintenance-question/v1-view",
                "runId": run_id,
                "questionId": question_id,
                "execution": execution,
            }
        try:
            state = self.question_states.load_question(
                manifest_path.parent,
                manifest,
                question_id,
            )
        except QuestionRunStateError as exc:
            raise QualificationRunError(str(exc)) from exc
        return {
            **copy.deepcopy(state),
            "runId": run_id,
        }

    def refresh(self, qualification: str, run_id: str) -> dict[str, Any]:
        if self.is_question_attempt(run_id):
            return self.get(qualification, run_id)
        path = self._manifest_path(qualification, run_id)
        with self._path_lock(path):
            manifest = self._apply_result_receipt(path, self._load_manifest(path))
        return self._public_with_aggregate_checkpoints(path, manifest)

    def write_result(
        self, qualification: str, run_id: str, result: Mapping[str, Any]
    ) -> Path:
        if self.is_question_attempt(run_id):
            path = self.result_path(qualification, run_id)
            self._write_json(path, result)
            self._update_question_attempt(
                qualification,
                run_id,
                {"result": copy.deepcopy(dict(result))},
            )
            return path
        manifest_path = self._manifest_path(qualification, run_id)
        with self._path_lock(manifest_path):
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
        with self._path_lock(path):
            manifest = self._finalize_validated_artifact_sync_incomplete(
                path,
                self._load_manifest(path),
                artifact_status=artifact_status,
                message=message,
                result_if_missing=result_if_missing,
            )
            return self._public(manifest)

    def result_path(self, qualification: str, run_id: str) -> Path:
        if self.is_question_attempt(run_id):
            parent_path, _parent, _question_id, attempt = (
                self._question_attempt_context(
                    qualification,
                    run_id,
                )
            )
            path = (
                self.repo_root
                / str(attempt.get("resultReceiptPath") or "")
            ).resolve()
            if not path.is_relative_to(
                self._question_attempt_directory(parent_path, attempt)
            ):
                raise QualificationRunError(
                    "一問attemptのresult pathが不正です。"
                )
            return path
        manifest_path = self._manifest_path(qualification, run_id)
        with self._path_lock(manifest_path):
            return self._result_path(
                manifest_path,
                self._load_manifest(manifest_path),
            )

    def progress_path(self, qualification: str, run_id: str) -> Path:
        if self.is_question_attempt(run_id):
            parent_path, _parent, _question_id, attempt = (
                self._question_attempt_context(
                    qualification,
                    run_id,
                )
            )
            path = (
                self.repo_root
                / str(attempt.get("progressReceiptPath") or "")
            ).resolve()
            if not path.is_relative_to(
                self._question_attempt_directory(parent_path, attempt)
            ):
                raise QualificationRunError(
                    "一問attemptのprogress pathが不正です。"
                )
            return path
        manifest_path = self._manifest_path(qualification, run_id)
        with self._path_lock(manifest_path):
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
        with self._path_lock(manifest_path):
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
        if self.is_question_attempt(run_id):
            manifest = self.get(qualification, run_id)
            progress_path = self.progress_path(qualification, run_id)
            raw = (
                progress_path.read_bytes()
                if progress_path.is_file()
                else b""
            )
            return self._parsed_progress(manifest, raw)
        manifest_path = self._manifest_path(qualification, run_id)
        with self._path_lock(manifest_path):
            manifest = self._load_manifest(manifest_path)
            if manifest.get("kind") != "human":
                return self._empty_progress(manifest)
            progress_path = manifest_path.parent / "agent_output" / "progress.jsonl"
            raw = progress_path.read_bytes() if progress_path.is_file() else b""
        return self._parsed_progress(manifest, raw)

    @staticmethod
    def _order_parent_questions(
        questions: list[dict[str, Any]],
        executions: Iterable[Mapping[str, Any]],
    ) -> None:
        positions = {
            str(execution.get("questionId") or ""): index
            for index, execution in enumerate(executions)
            if execution.get("questionId")
        }
        questions.sort(
            key=lambda value: (
                positions.get(str(value.get("questionId") or ""), len(positions)),
                str(value.get("questionId") or ""),
            )
        )
        for target_index, question in enumerate(questions, start=1):
            question["targetIndex"] = target_index

    def combined_progress(
        self,
        qualification: str,
        run_id: str,
        *,
        include_questions: bool = True,
    ) -> dict[str, Any]:
        manifest_path = self._manifest_path(qualification, run_id)
        with self._path_lock(manifest_path):
            manifest = self._load_manifest(manifest_path)
            child_run_ids = list(manifest.get("childRunIds") or [])
        if self.question_states.is_current(manifest):
            return self._current_question_run_progress(
                manifest_path,
                manifest,
                include_questions=include_questions,
            )
        children: list[tuple[dict[str, Any], bytes]] = []
        for child_run_id in child_run_ids:
            child_path = self._manifest_path(qualification, str(child_run_id))
            with self._path_lock(child_path):
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
        payload["verified"] = _terminal_receipt_validated(manifest)
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
        self._order_parent_questions(
            questions,
            manifest.get("questionExecutions") or [],
        )
        if payload["current"] is not None:
            current_question = question_by_id.get(
                str(payload["current"].get("questionId") or "")
            )
            if current_question is not None:
                payload["current"]["targetIndex"] = current_question[
                    "targetIndex"
                ]
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
        target_work_items = int(payload.get("targetWorkItemCount") or 0)
        completed_work_items_count = max(
            int(payload.get("completedWorkItemCount") or 0),
            completed_work_items,
        )
        processed_work_items_count = max(
            int(payload.get("processedWorkItemCount") or 0),
            processed_work_items_count,
        )
        if target_work_items:
            completed_work_items_count = min(
                target_work_items, completed_work_items_count
            )
            processed_work_items_count = min(
                target_work_items, processed_work_items_count
            )
        payload["completedWorkItemCount"] = completed_work_items_count
        payload["processedWorkItemCount"] = processed_work_items_count
        completed_question_count = max(
            int(payload.get("completedQuestionCount") or 0),
            int(execution_summary.get("validatedQuestionCount") or 0),
        )
        processed_question_count = max(
            int(payload.get("processedQuestionCount") or 0),
            int(execution_summary.get("validatedQuestionCount") or 0)
            + int(execution_summary.get("blockedQuestionCount") or 0),
        )
        target_question_count = int(payload.get("targetQuestionCount") or 0)
        if target_question_count:
            completed_question_count = min(
                target_question_count, completed_question_count
            )
            processed_question_count = min(
                target_question_count, processed_question_count
            )
        payload["completedQuestionCount"] = completed_question_count
        payload["validatedQuestionCount"] = completed_question_count
        payload["processedQuestionCount"] = processed_question_count
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

    def _current_question_run_progress(
        self,
        manifest_path: Path,
        manifest: Mapping[str, Any],
        *,
        include_questions: bool,
    ) -> dict[str, Any]:
        try:
            summary_payload = self.question_states.load_summary(
                manifest_path.parent,
                manifest,
            )
        except QuestionRunStateError:
            try:
                summary_payload = self.question_states.rebuild_summary(
                    manifest_path.parent,
                    manifest,
                )
            except QuestionRunStateError as exc:
                raise QualificationRunError(str(exc)) from exc
        execution_summary = dict(summary_payload["queueSummary"])
        payload = self._empty_progress(
            {
                **dict(manifest),
                "questionExecutionSummary": execution_summary,
            }
        )
        question_count = int(summary_payload.get("questionCount") or 0)
        validated_questions = int(
            execution_summary.get("validatedQuestionCount") or 0
        )
        blocked_questions = int(
            execution_summary.get("blockedQuestionCount") or 0
        )
        validated_work = int(
            execution_summary.get("validatedWorkItemCount") or 0
        )
        blocked_work = int(
            execution_summary.get("blockedWorkItemCount") or 0
        )
        not_applicable_work = int(
            execution_summary.get("notApplicableWorkItemCount") or 0
        )
        processed_questions = validated_questions + blocked_questions
        processed_work = validated_work + blocked_work + not_applicable_work
        verified = bool(
            manifest.get("status") == "succeeded"
            and manifest.get("receiptValidated") is True
        )
        questions: list[dict[str, Any]] = []
        if include_questions:
            compact_questions = summary_payload.get("questions")
            if not isinstance(compact_questions, list):
                raise QualificationRunError(
                    "question summaryに問題一覧がありません。"
                )
            for target_index, raw_execution in enumerate(
                compact_questions,
                start=1,
            ):
                if not isinstance(raw_execution, Mapping):
                    raise QualificationRunError(
                        "question summaryの問題状態が不正です。"
                    )
                execution = copy.deepcopy(dict(raw_execution))
                question_id = str(execution.get("questionId") or "")
                if not question_id:
                    raise QualificationRunError(
                        "question summaryにquestionIdがありません。"
                    )
                target = {
                    str(key): copy.deepcopy(value)
                    for key, value in execution.items()
                    if key not in {"status", "stages"}
                }
                stages = [
                    dict(value)
                    for value in execution.get("stages") or []
                    if isinstance(value, Mapping)
                ]
                active_stage = next(
                    (
                        stage
                        for status in (
                            "committing",
                            "prepared",
                            "preparing",
                            "queued",
                        )
                        for stage in stages
                        if str(stage.get("status") or "") == status
                    ),
                    stages[-1] if stages else {},
                )
                question_status = str(
                    execution.get("status") or "queued"
                )
                queue_status = (
                    "blocked"
                    if question_status == "blocked"
                    else "validated"
                    if question_status == "validated"
                    else str(active_stage.get("status") or "queued")
                )
                outputs: list[dict[str, Any]] = []
                for stage in stages:
                    stage_status = str(stage.get("status") or "")
                    if stage_status not in {
                        "validated",
                        "blocked",
                    }:
                        continue
                    result = (
                        {"summary": str(stage["error"])}
                        if stage.get("error")
                        else {}
                    )
                    outputs.append(
                        {
                            "event": "stage_completed",
                            "questionId": question_id,
                            "stageId": str(stage.get("stageId") or ""),
                            "stageCode": str(stage.get("stageCode") or ""),
                            "stageLabel": str(stage.get("stageLabel") or ""),
                            "result": result,
                        }
                    )
                blocked_reason = next(
                    (
                        str(stage.get("error") or "")
                        for stage in stages
                        if str(stage.get("status") or "") == "blocked"
                        and stage.get("error")
                    ),
                    "",
                )
                question = {
                    **copy.deepcopy(target),
                    "questionId": question_id,
                    "targetIndex": target_index,
                    "queueStatus": queue_status,
                    "approvalState": (
                        "blocked"
                        if queue_status == "blocked"
                        else "validated"
                        if queue_status == "validated"
                        else "working"
                    ),
                    "event": (
                        "question_completed"
                        if queue_status in {"validated", "blocked"}
                        else "question_started"
                        if queue_status
                        in {"preparing", "prepared", "committing"}
                        else None
                    ),
                    "stageId": str(active_stage.get("stageId") or ""),
                    "stageCode": str(active_stage.get("stageCode") or ""),
                    "stageLabel": str(active_stage.get("stageLabel") or ""),
                    "blockedReason": blocked_reason or None,
                    "processed": queue_status in {"validated", "blocked"},
                    "completed": queue_status == "validated",
                    "outputs": outputs,
                }
                questions.append(question)
        current = next(
            (
                question
                for status in ("committing", "preparing", "prepared")
                for question in questions
                if question.get("queueStatus") == status
            ),
            None,
        )
        groups: list[dict[str, Any]] = []
        for list_group_id in sorted(
            {
                str(question.get("listGroupId") or "")
                for question in questions
                if question.get("listGroupId")
            }
        ):
            group_questions = [
                question
                for question in questions
                if str(question.get("listGroupId") or "") == list_group_id
            ]
            completed = sum(
                question.get("queueStatus") == "validated"
                for question in group_questions
            )
            groups.append(
                {
                    "listGroupId": list_group_id,
                    "targetQuestionCount": len(group_questions),
                    "completedQuestionCount": completed,
                    "processedQuestionCount": sum(
                        question.get("queueStatus")
                        in {"validated", "blocked"}
                        for question in group_questions
                    ),
                    "percent": (
                        round(completed / len(group_questions) * 100)
                        if group_questions
                        else 0
                    ),
                }
            )
        payload.update(
            {
                "verified": verified,
                "targetQuestionCount": question_count,
                "completedQuestionCount": validated_questions,
                "touchedQuestionCount": processed_questions,
                "processedQuestionCount": processed_questions,
                "validatedQuestionCount": validated_questions,
                "blockedQuestionCount": blocked_questions,
                "targetWorkItemCount": int(
                    execution_summary.get("workItemCount") or 0
                ),
                "completedWorkItemCount": validated_work,
                "processedWorkItemCount": processed_work,
                "validatedWorkItemCount": validated_work,
                "blockedWorkItemCount": blocked_work,
                "percent": (
                    round(validated_questions / question_count * 100)
                    if question_count
                    else 0
                ),
                "processedPercent": (
                    round(processed_questions / question_count * 100)
                    if question_count
                    else 0
                ),
                "current": current,
                "events": [],
                "questions": questions,
                "groups": groups,
                "invalidEventCount": 0,
            }
        )
        return payload

    @staticmethod
    def _empty_progress(manifest: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "runId": manifest.get("runId"),
            "status": manifest.get("status"),
            "verified": _terminal_receipt_validated(manifest),
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
        verified_run = _terminal_receipt_validated(manifest)
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
        if self.is_question_attempt(run_id):
            return self._write_question_attempt_baseline(
                qualification,
                run_id,
                roots,
            )
        manifest_path = self._manifest_path(qualification, run_id)
        with self._path_lock(manifest_path):
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
                captured_files = write_snapshot_fingerprints(transaction)
                record_snapshots = {
                    relative.as_posix(): _record_snapshot(
                        self.repo_root / relative
                    )
                    for relative in sorted(set(record_paths))
                }
                source_record_snapshots = {
                    relative.as_posix(): _record_snapshot(
                        self.repo_root / relative
                    )
                    for relative in sorted(set(source_record_paths))
                }
                current_files = _snapshot_roots(
                    self.repo_root,
                    tracked_roots,
                )
                changed_during_capture = sorted(
                    path
                    for path in captured_files.keys() | current_files.keys()
                    if captured_files.get(path) != current_files.get(path)
                )
                if changed_during_capture:
                    raise WriteTransactionError(
                        "baseline取得中に対象fileが更新されました: "
                        + ", ".join(changed_during_capture)
                    )
            except (
                OSError,
                QualificationRunError,
                WriteTransactionError,
            ) as exc:
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
                "files": captured_files,
                "writeTransaction": transaction,
                "recordSnapshots": record_snapshots,
                "sourceRecordSnapshots": source_record_snapshots,
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

    def _write_question_attempt_baseline(
        self,
        qualification: str,
        run_id: str,
        roots: tuple[Path, ...],
    ) -> Path:
        manifest = self.get(qualification, run_id)
        run_dir = self.run_directory(qualification, run_id)
        agent_output = self.result_path(
            qualification,
            run_id,
        ).parent.resolve()
        tracked_roots = [
            path.resolve()
            for path in roots
            if path.resolve() != agent_output
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
                raise QualificationRunError(
                    "record baselineのpathが不正です。"
                )
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
                raise QualificationRunError(
                    "source baselineのpathが不正です。"
                )
            if relative.suffix.lower() == ".json":
                source_record_paths.append(relative)
        backup_root = run_dir / "baseline_files"
        try:
            transaction = capture_write_snapshot(
                self.repo_root,
                tracked_roots,
                backup_root,
            )
            captured_files = write_snapshot_fingerprints(transaction)
            record_snapshots = {
                relative.as_posix(): _record_snapshot(
                    self.repo_root / relative
                )
                for relative in sorted(set(record_paths))
            }
            source_record_snapshots = {
                relative.as_posix(): _record_snapshot(
                    self.repo_root / relative
                )
                for relative in sorted(set(source_record_paths))
            }
            current_files = _snapshot_roots(
                self.repo_root,
                tracked_roots,
            )
            changed_during_capture = sorted(
                path
                for path in captured_files.keys() | current_files.keys()
                if captured_files.get(path) != current_files.get(path)
            )
            if changed_during_capture:
                raise WriteTransactionError(
                    "baseline取得中に対象fileが更新されました: "
                    + ", ".join(changed_during_capture)
                )
        except (
            OSError,
            QualificationRunError,
            WriteTransactionError,
        ) as exc:
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
            "files": captured_files,
            "writeTransaction": transaction,
            "recordSnapshots": record_snapshots,
            "sourceRecordSnapshots": source_record_snapshots,
        }
        baseline_path = run_dir / "baseline.json"
        self._write_json(baseline_path, payload)
        baseline_hash = hashlib.sha256(
            baseline_path.read_bytes()
        ).hexdigest()
        self._update_question_attempt(
            qualification,
            run_id,
            {
                "baselinePath": str(
                    baseline_path.relative_to(self.repo_root)
                ),
                "baselineHash": baseline_hash,
                "deltaUnknown": False,
                "rollback": {
                    "status": "available",
                    "restoredFiles": [],
                    "remainingChangedFiles": [],
                    "deltaUnknown": False,
                    "message": (
                        "検証前の失敗時に開始前の状態へ戻せます。"
                    ),
                },
            },
        )
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

        partial = str(manifest.get("queueStatus") or "") == "partial"
        terminal_status = "succeeded"
        result = manifest.get("result")
        if (
            not isinstance(result, Mapping)
            or result.get("status") != terminal_status
        ) and result_if_missing is not None:
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
                "status": terminal_status,
                "receiptValidated": True,
                "artifactSync": {
                    "status": artifact_status,
                    "groups": copy.deepcopy(list(current_sync.get("groups") or [])),
                    "message": sync_message,
                },
                "error": (
                    str(result.get("summary") or "")
                    if partial and isinstance(result, Mapping)
                    else None
                ),
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

    def _recover_parent_shared_prerequisites(
        self,
        manifest: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
        phases = [
            dict(value)
            for value in manifest.get("phaseExecutions") or []
            if isinstance(value, Mapping)
        ]
        recovered_receipts: list[dict[str, Any]] = []
        confirmed_group_ids = {
            str(value) for value in manifest.get("confirmedGroupIds") or [] if value
        }
        qualification = str(manifest.get("qualification") or "")
        parent_run_id = str(manifest.get("runId") or "")
        child_ids = [
            str(value) for value in manifest.get("childRunIds") or [] if value
        ]
        children: dict[str, tuple[Path, dict[str, Any]]] = {}
        missing_child_ids: list[str] = []
        for child_id in child_ids:
            try:
                child_path = self._manifest_path(qualification, child_id)
                children[child_id] = (child_path, self._load_manifest(child_path))
            except (OSError, QualificationRunError, ValueError):
                missing_child_ids.append(child_id)

        unsafe_child_id: str | None = None
        for phase in phases:
            phase_id = str(phase.get("id") or "")
            if (
                phase_id not in {"setup", "category_setup"}
                or str(phase.get("status") or "") != "running"
            ):
                continue
            matches = [
                (child_id, child_path, child)
                for child_id, (child_path, child) in children.items()
                if str(child.get("parentRunId") or "") == parent_run_id
                and str(child.get("flowPhaseId") or "") == phase_id
            ]
            if not matches and not missing_child_ids:
                phase.update(
                    status="pending",
                    childRunIds=[],
                    receiptValidated=False,
                    artifactSync={"status": "not_required", "groups": []},
                    finishedAt=None,
                    error="共有前提処理の開始前に停止したため再実行できます。",
                )
                continue
            if len(matches) != 1 or missing_child_ids:
                unsafe_child_id = (
                    missing_child_ids[0]
                    if missing_child_ids
                    else matches[0][0]
                    if matches
                    else "unknown"
                )
                reason = (
                    "再起動前の共有前提childを親runと一意に照合できないため、"
                    "安全側で後続patch toolを停止しました。"
                )
                phase.update(status="failed", error=reason, finishedAt=_now())
                manifest.update(
                    retrySafe=False,
                    retryUnsafeReason=reason,
                    unsafeChildRunId=unsafe_child_id,
                )
                break

            child_id, child_path, child = matches[0]
            if (
                child.get("status") == "validating"
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
                child.get("status") == "succeeded"
                and child.get("receiptValidated") is True
                and isinstance(result, Mapping)
                and result.get("status") == "succeeded"
                and child.get("deltaUnknown") is not True
                and isinstance(receipt, Mapping)
            )
            if child_succeeded:
                phase.update(
                    status="succeeded",
                    childRunIds=[child_id],
                    threadId=child.get("threadId"),
                    sessionId=child.get("sessionId"),
                    turnId=child.get("turnId"),
                    model=child.get("model"),
                    serviceTier=child.get("serviceTier"),
                    reasoningEffort=child.get("reasoningEffort"),
                    receiptValidated=True,
                    workVersionReceipt=receipt,
                    artifactSync={"status": "deferred", "groups": []},
                    finishedAt=_now(),
                    error=None,
                )
                recovered_receipts.append(dict(receipt))
                if int(receipt.get("recordedCount") or 0):
                    confirmed_group_ids.update(
                        str(value)
                        for value in child.get("targetGroupIds") or []
                        if value
                    )
                continue

            if (
                (not child.get("startedAt") and _child_retry_safe(child))
                or _isolated_failure_state(child)
            ):
                phase.update(
                    status="pending",
                    childRunIds=[child_id],
                    receiptValidated=False,
                    artifactSync={"status": "not_required", "groups": []},
                    finishedAt=None,
                    error=(
                        str(child.get("error") or "")
                        or "共有前提を再実行できます。"
                    ),
                )
                continue

            unsafe_child_id = child_id
            reason = (
                "再起動前の共有前提childでrollback又は確定receiptを"
                "確認できないため、安全側で後続patch toolを停止しました。"
            )
            phase.update(status="failed", error=reason, finishedAt=_now())
            manifest.update(
                retrySafe=False,
                retryUnsafeReason=reason,
                unsafeChildRunId=unsafe_child_id,
            )
            break

        if unsafe_child_id:
            reason = str(manifest.get("retryUnsafeReason") or "再開できません。")
            executions = copy.deepcopy(list(manifest.get("questionExecutions") or []))
            for question in executions:
                if not isinstance(question, dict):
                    continue
                for stage_index, stage in enumerate(question.get("stages") or []):
                    if str(stage.get("status") or "") in {
                        "validated",
                        "not_applicable",
                        "blocked",
                    }:
                        continue
                    self._block_execution_from(question, stage_index, reason)
                    break
            manifest["questionExecutions"] = executions
        manifest["confirmedGroupIds"] = sorted(confirmed_group_ids)
        return phases, recovered_receipts, unsafe_child_id

    def _recover_question_run(
        self,
        manifest_path: Path,
        manifest: dict[str, Any],
    ) -> None:
        """Recover each current question independently, then rebuild parent state."""

        qualification = str(manifest.get("qualification") or "")
        run_id = str(manifest.get("runId") or "")
        try:
            question_ids = self.question_states.question_ids(
                manifest_path.parent,
                manifest,
            )
        except QuestionRunStateError as exc:
            manifest.update(
                status="interrupted",
                queueStatus="partial",
                retrySafe=False,
                retryUnsafeReason=str(exc),
                error=str(exc),
                updatedAt=_now(),
                finishedAt=_now(),
            )
            self._write_manifest(manifest_path, manifest)
            return

        shared_unsafe_reason = ""
        running_shared_phase = any(
            isinstance(value, Mapping)
            and str(value.get("id") or "") in {"setup", "category_setup"}
            and str(value.get("status") or "") == "running"
            for value in manifest.get("phaseExecutions") or []
        )
        if running_shared_phase:
            try:
                hydrated = self.question_states.hydrate(
                    manifest_path.parent,
                    manifest,
                )
                (
                    recovered_phases,
                    recovered_shared_receipts,
                    unsafe_child_id,
                ) = self._recover_parent_shared_prerequisites(hydrated)
            except (QualificationRunError, QuestionRunStateError) as exc:
                shared_unsafe_reason = str(exc)
            else:
                manifest["phaseExecutions"] = recovered_phases
                manifest["confirmedGroupIds"] = list(
                    hydrated.get("confirmedGroupIds") or []
                )
                shared_receipts = [
                    dict(value)
                    for value in manifest.get(
                        "sharedWorkVersionReceipts"
                    )
                    or []
                    if isinstance(value, Mapping)
                ]
                seen_shared = {
                    json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    for value in shared_receipts
                }
                for value in recovered_shared_receipts:
                    encoded = json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    if encoded in seen_shared:
                        continue
                    seen_shared.add(encoded)
                    shared_receipts.append(dict(value))
                manifest["sharedWorkVersionReceipts"] = shared_receipts
                if unsafe_child_id:
                    manifest["retrySafe"] = False
                    manifest["unsafeChildRunId"] = unsafe_child_id
                    shared_unsafe_reason = str(
                        hydrated.get("retryUnsafeReason")
                        or "共有前提を安全に回収できません。"
                    )

        unsafe_reason = ""
        unsafe_attempt_id = ""
        for question_id in question_ids:
            try:
                state = self.question_states.load_question(
                    manifest_path.parent,
                    manifest,
                    question_id,
                )
            except QuestionRunStateError as exc:
                unsafe_reason = str(exc)
                break
            attempts = state.get("attemptArtifacts")
            attempts = attempts if isinstance(attempts, Mapping) else {}
            for attempt_id, attempt in list(attempts.items()):
                if (
                    not isinstance(attempt, Mapping)
                    or attempt.get("candidateTransactionOpen") is not True
                ):
                    continue
                if attempt.get("canonicalWriteStarted") is False:
                    observed_delta = self.baseline_delta(
                        qualification,
                        str(attempt_id),
                    )
                    rollback = self.close_unwritten_baseline(
                        qualification,
                        str(attempt_id),
                        observed_changed_files=observed_delta or [],
                    )
                else:
                    rollback = self.rollback_baseline(
                        qualification,
                        str(attempt_id),
                    )
                rollback_safe = bool(
                    isinstance(rollback, Mapping)
                    and rollback.get("deltaUnknown") is not True
                    and not rollback.get("remainingChangedFiles")
                    and (
                        rollback.get("status") == "succeeded"
                        or (
                            rollback.get("status") == "not_required"
                            and attempt.get("canonicalWriteStarted") is False
                        )
                    )
                )
                if not rollback_safe:
                    unsafe_attempt_id = str(attempt_id)
                    unsafe_reason = (
                        f"{question_id}の未完了transactionを"
                        "開始前の内容へ戻せません。"
                    )
                    break
                self._update_question_attempt(
                    qualification,
                    str(attempt_id),
                    {
                        "status": "interrupted",
                        "candidateTransactionOpen": False,
                        "receiptValidated": False,
                        "error": (
                            "ローカルUIの再起動で一問turnが中断されました。"
                        ),
                        "finishedAt": _now(),
                    },
                )
            if unsafe_reason:
                break
        if not unsafe_reason and shared_unsafe_reason:
            unsafe_reason = shared_unsafe_reason

        if unsafe_reason:
            for question_id in question_ids:
                question_path = self.question_states.question_path(
                    manifest_path.parent,
                    manifest,
                    question_id,
                )
                with self._path_lock(question_path):

                    def block(state: dict[str, Any]) -> None:
                        execution = state.get("execution")
                        if not isinstance(execution, dict):
                            raise QuestionRunStateError(
                                "一問stateのexecutionが不正です。"
                            )
                        stages = execution.get("stages") or []
                        first_index = next(
                            (
                                index
                                for index, stage in enumerate(stages)
                                if isinstance(stage, dict)
                                and str(stage.get("status") or "")
                                not in {
                                    "validated",
                                    "not_applicable",
                                    "blocked",
                                }
                            ),
                            None,
                        )
                        if first_index is not None:
                            self._block_execution_from(
                                execution,
                                first_index,
                                unsafe_reason,
                            )
                        state["activeAttemptId"] = None

                    try:
                        self.question_states.update_question(
                            manifest_path.parent,
                            manifest,
                            question_id,
                            block,
                        )
                    except QuestionRunStateError:
                        pass
            manifest.update(
                status="interrupted",
                queueStatus="partial",
                retrySafe=False,
                retryUnsafeReason=unsafe_reason,
                unsafeChildRunId=unsafe_attempt_id or manifest.get(
                    "unsafeChildRunId"
                ),
                error=unsafe_reason,
                updatedAt=_now(),
                finishedAt=_now(),
            )
        else:
            for question_id in question_ids:
                question_path = self.question_states.question_path(
                    manifest_path.parent,
                    manifest,
                    question_id,
                )
                with self._path_lock(question_path):

                    def recover(state: dict[str, Any]) -> None:
                        execution = state.get("execution")
                        if not isinstance(execution, dict):
                            raise QuestionRunStateError(
                                "一問stateのexecutionが不正です。"
                            )
                        attempts = state.get("attemptArtifacts")
                        attempts = (
                            attempts if isinstance(attempts, dict) else {}
                        )
                        receipts = state.setdefault(
                            "validatedReceipts",
                            {},
                        )
                        if not isinstance(receipts, dict):
                            raise QuestionRunStateError(
                                "一問stateのvalidatedReceiptsが不正です。"
                            )
                        for stage in execution.get("stages") or []:
                            if not isinstance(stage, dict):
                                continue
                            validation_attempts = [
                                value
                                for value in (
                                    stage.get("validationAttempts") or []
                                )
                                if isinstance(value, dict)
                            ]
                            latest = (
                                validation_attempts[-1]
                                if validation_attempts
                                else None
                            )
                            attempt_id = str(
                                (latest or {}).get("childRunId") or ""
                            )
                            attempt = (
                                attempts.get(attempt_id)
                                if attempt_id
                                else None
                            )
                            batch_results = (
                                attempt.get("batchQuestionResults")
                                if isinstance(attempt, Mapping)
                                else []
                            )
                            result = next(
                                (
                                    value
                                    for value in batch_results or []
                                    if isinstance(value, Mapping)
                                    and str(
                                        value.get("questionId") or ""
                                    )
                                    == question_id
                                ),
                                None,
                            )
                            completed = bool(
                                isinstance(attempt, Mapping)
                                and attempt.get("status") == "succeeded"
                                and attempt.get("receiptValidated") is True
                                and isinstance(result, Mapping)
                                and result.get("status") == "succeeded"
                            )
                            if completed:
                                stage.update(
                                    status="validated",
                                    error=None,
                                    retryDeferred=False,
                                    finishedAt=(
                                        stage.get("finishedAt") or _now()
                                    ),
                                )
                                if latest is not None:
                                    latest.update(
                                        status="validated",
                                        finishedAt=(
                                            latest.get("finishedAt")
                                            or _now()
                                        ),
                                    )
                                receipt = result.get(
                                    "workVersionReceipt"
                                )
                                if isinstance(receipt, Mapping):
                                    receipts[
                                        validated_receipt_key(
                                            question_id,
                                            str(
                                                stage.get("stageId") or ""
                                            ),
                                        )
                                    ] = copy.deepcopy(dict(receipt))
                                continue
                            if str(stage.get("status") or "") in {
                                "preparing",
                                "prepared",
                                "committing",
                            }:
                                stage.update(
                                    status="queued",
                                    error=None,
                                    retryDeferred=True,
                                    finishedAt=None,
                                )
                            if latest is not None and str(
                                latest.get("status") or ""
                            ) in {"queued", "running", "preparing"}:
                                latest.update(
                                    status="interrupted",
                                    pauseReason=(
                                        "ローカルUIの再起動で"
                                        "一問turnが中断されました。"
                                    ),
                                    finishedAt=_now(),
                                )
                            if isinstance(attempt, dict) and str(
                                attempt.get("status") or ""
                            ) in {"queued", "running", "validating"}:
                                attempt.update(
                                    status="interrupted",
                                    candidateTransactionOpen=False,
                                    receiptValidated=False,
                                    error=(
                                        "ローカルUIの再起動で"
                                        "一問turnが中断されました。"
                                    ),
                                    finishedAt=_now(),
                                    updatedAt=_now(),
                                )
                        refresh_question_status(execution)
                        state["activeAttemptId"] = None

                    try:
                        self.question_states.update_question(
                            manifest_path.parent,
                            manifest,
                            question_id,
                            recover,
                        )
                    except QuestionRunStateError as exc:
                        unsafe_reason = str(exc)
                        break
            if unsafe_reason:
                manifest.update(
                    retrySafe=False,
                    retryUnsafeReason=unsafe_reason,
                )

        try:
            summary_payload = self.question_states.rebuild_summary(
                manifest_path.parent,
                manifest,
            )
        except QuestionRunStateError as exc:
            manifest.update(
                retrySafe=False,
                retryUnsafeReason=str(exc),
            )
            summary = {
                "blockedQuestionCount": 0,
                "blockedWorkItemCount": 0,
                "validatedQuestionCount": 0,
                "validatedWorkItemCount": 0,
                "pendingWorkItemCount": 1,
            }
        else:
            summary = dict(summary_payload["queueSummary"])
        phases = [
            copy.deepcopy(dict(value))
            for value in manifest.get("phaseExecutions") or []
            if isinstance(value, Mapping)
        ]
        try:
            executions = self.question_states.load_executions(
                manifest_path.parent,
                manifest,
            )
        except QuestionRunStateError:
            executions = []
        for phase in phases:
            stage_id = str(phase.get("id") or "")
            if stage_id in {"", "setup", "category_setup"}:
                if phase.get("status") == "running":
                    phase["status"] = "pending"
                continue
            completion = _question_phase_completion(
                executions,
                stage_id,
            )
            phase.update(
                **completion,
                finishedAt=(
                    None
                    if completion["status"] == "pending"
                    else phase.get("finishedAt") or _now()
                ),
            )
        terminal = not int(summary.get("pendingWorkItemCount") or 0)
        queue_status = (
            "partial"
            if int(summary.get("blockedQuestionCount") or 0)
            else "succeeded"
            if terminal
            else "interrupted"
        )
        now = _now()
        manifest.update(
            status=(
                "succeeded"
                if terminal
                and manifest.get("retrySafe") is not False
                else "interrupted"
            ),
            queueStatus=queue_status,
            retrySafe=manifest.get("retrySafe") is not False,
            phaseExecutions=phases,
            currentPhaseId=None,
            executionPhase="done" if terminal else "interrupted",
            questionExecutionSummary=summary,
            blockedQuestionCount=int(
                summary.get("blockedQuestionCount") or 0
            ),
            blockedWorkItemCount=int(
                summary.get("blockedWorkItemCount") or 0
            ),
            validatedQuestionCount=int(
                summary.get("validatedQuestionCount") or 0
            ),
            validatedWorkItemCount=int(
                summary.get("validatedWorkItemCount") or 0
            ),
            receiptValidated=bool(
                terminal and manifest.get("retrySafe") is not False
            ),
            artifactSync=(
                {
                    "status": "interrupted",
                    "groups": [],
                    "message": (
                        "patchは確定済みです。公開用データは"
                        "再開後に再生成してください。"
                    ),
                }
                if terminal
                else manifest.get("artifactSync")
            ),
            error=(
                unsafe_reason
                or (
                    "ローカルUIの再起動で処理が中断されました。"
                    "未確定の問題だけ再開できます。"
                    if not terminal
                    else None
                )
            ),
            updatedAt=now,
            finishedAt=now,
        )
        if terminal and manifest.get("retrySafe") is not False:
            result = {
                "status": "succeeded",
                "summary": (
                    "一問stateの確定receiptを再起動時に照合しました。"
                    "公開用データの同期だけ再実行が必要です。"
                ),
                "commands": [
                    {
                        "command": (
                            "workflow: recover validated question states"
                        ),
                        "status": "pass",
                    }
                ],
                "changedFiles": [],
                "resolvedFailedDeltaPaths": [],
            }
            self._write_json(self._result_path(manifest_path, manifest), result)
            manifest["result"] = result
        self._write_manifest(manifest_path, manifest)

    def _recover_interrupted_runs(self) -> None:
        if not self.root.is_dir():
            return
        with self._lock:
            candidates: list[tuple[bool, Path]] = []
            for sidecar_path in self.root.glob("*/*/recovery.json"):
                try:
                    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    sidecar = {}
                candidates.append(
                    (
                        sidecar.get("kind") == "orchestration",
                        sidecar_path.with_name("manifest.json"),
                    )
                )
            paths = [
                path
                for _orchestration, path in sorted(
                    candidates,
                    key=lambda value: (value[0], str(value[1])),
                )
                if path.is_file()
            ]
            for path in paths:
                manifest = self._load_manifest(path)
                if not self._requires_startup_recovery(manifest):
                    path.with_name("recovery.json").unlink(missing_ok=True)
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
                if self.question_states.is_current(manifest):
                    self._recover_question_run(path, manifest)
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

        if self.is_question_attempt(run_id):
            manifest = self.get(qualification, run_id)
            if manifest.get("receiptValidated") is True:
                return None
            manifest_path = (
                self.run_directory(qualification, run_id)
                / "manifest.json"
            )
            rollback = self._rollback_baseline_delta(
                manifest_path,
                manifest,
            )
            if rollback is None:
                return None
            self._update_question_attempt(
                qualification,
                run_id,
                {
                    "rollback": rollback,
                    "deltaUnknown": bool(
                        rollback.get("deltaUnknown")
                        or rollback.get("remainingChangedFiles")
                    ),
                },
            )
            return copy.deepcopy(rollback)
        manifest_path = self._manifest_path(qualification, run_id)
        with self._path_lock(manifest_path):
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

    def baseline_delta(
        self,
        qualification: str,
        run_id: str,
    ) -> list[str] | None:
        """Return the current delta from the captured one-question boundary."""

        if self.is_question_attempt(run_id):
            manifest = self.get(qualification, run_id)
            manifest_path = (
                self.run_directory(qualification, run_id)
                / "manifest.json"
            )
            return self._recover_baseline_delta(
                manifest_path,
                manifest,
            )
        manifest_path = self._manifest_path(qualification, run_id)
        with self._path_lock(manifest_path):
            manifest = self._load_manifest(manifest_path)
            return self._recover_baseline_delta(
                manifest_path,
                manifest,
            )

    def close_unwritten_baseline(
        self,
        qualification: str,
        run_id: str,
        *,
        observed_changed_files: list[str] | tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Close a baseline without restoring when no canonical write began."""

        if not self.is_question_attempt(run_id):
            raise QualificationRunError(
                "未書込みbaselineの終了は一問attemptに限定します。"
            )
        current = self.get(qualification, run_id)
        if current.get("canonicalWriteStarted") is not False:
            raise QualificationRunError(
                "正本書込み開始後のbaselineは復元せずに終了できません。"
            )
        rollback = {
            "status": "not_required",
            "restoredFiles": [],
            "remainingChangedFiles": [],
            "externalChangedFiles": sorted(
                {str(value) for value in observed_changed_files if value}
            ),
            "deltaUnknown": False,
            "message": (
                "正本書込み前に停止したためrollbackは不要です。"
            ),
        }
        self._update_question_attempt(
            qualification,
            run_id,
            {
                "rollback": rollback,
                "deltaUnknown": False,
                "candidateTransactionOpen": False,
            },
        )
        self.discard_baseline_backups(qualification, run_id)
        return copy.deepcopy(rollback)

    def discard_baseline_backups(
        self,
        qualification: str,
        run_id: str,
    ) -> None:
        if self.is_question_attempt(run_id):
            path = (
                self.run_directory(qualification, run_id)
                / "baseline_files"
            )
            shutil.rmtree(path, ignore_errors=True)
            return
        manifest_path = self._manifest_path(qualification, run_id)
        with self._path_lock(manifest_path):
            path = manifest_path.parent / "baseline_files"
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
        *,
        include_progress: bool = True,
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
        progress_section = (
            [
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
            ]
            if include_progress
            else []
        )
        return "\n".join(
            [
                prompt.rstrip(),
                *progress_section,
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
    def _manifest_file_signature(path: Path) -> tuple[int, int, int]:
        stat = path.stat()
        return stat.st_ino, stat.st_mtime_ns, stat.st_size

    def _remember_manifest(
        self,
        path: Path,
        signature: tuple[int, int, int],
        manifest: dict[str, Any],
    ) -> None:
        with self._cache_lock:
            self._manifest_cache.pop(path, None)
            self._manifest_cache[path] = (signature, manifest)
            while len(self._manifest_cache) > MANIFEST_CACHE_LIMIT:
                oldest = next(iter(self._manifest_cache))
                self._manifest_cache.pop(oldest, None)

    def _manifest_value(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise QualificationRunError("作業履歴が見つかりません。")
        signature = self._manifest_file_signature(path)
        with self._cache_lock:
            cached = self._manifest_cache.get(path)
            if cached is not None and cached[0] == signature:
                self._manifest_cache.pop(path, None)
                self._manifest_cache[path] = cached
                return cached[1]
        for _attempt in range(2):
            before = self._manifest_file_signature(path)
            value = json.loads(path.read_text(encoding="utf-8"))
            after = self._manifest_file_signature(path)
            signature = after
            if before == after:
                break
        if not isinstance(value, dict):
            raise QualificationRunError("作業履歴の形式が不正です。")
        self._remember_manifest(path, signature, value)
        return value

    def _load_manifest(self, path: Path) -> dict[str, Any]:
        return copy.deepcopy(self._manifest_value(path))

    def _manifest_has_parent(self, path: Path) -> bool:
        signature = self._manifest_file_signature(path)
        with self._cache_lock:
            cached = self._manifest_header_cache.get(path)
        if cached is not None and cached[0] == signature:
            return cached[1]
        try:
            with path.open("rb") as source:
                header = source.read(MANIFEST_HEADER_BYTES)
        except OSError as exc:
            raise QualificationRunError(
                f"作業履歴の先頭を確認できません: {path}"
            ) from exc
        match = re.search(
            rb'"parentRunId"\s*:\s*(null|"[^"]*")',
            header,
        )
        has_parent = (
            match.group(1) != b"null"
            if match is not None
            else bool(self._manifest_value(path).get("parentRunId"))
        )
        with self._cache_lock:
            self._manifest_header_cache[path] = (signature, has_parent)
        return has_parent

    def _load_manifest_list_summary(self, path: Path) -> dict[str, Any]:
        manifest_signature = self._manifest_file_signature(path)
        with self._cache_lock:
            cached = self._manifest_list_summary_cache.get(path)
        if cached is not None:
            receipt_signature = self._list_summary_receipt_signature(
                path,
                cached[1],
            )
            cache_signature = (manifest_signature, receipt_signature)
            if cached[0] == cache_signature:
                return copy.deepcopy(cached[1])

        sidecar = self._read_manifest_list_summary_sidecar(
            path,
            manifest_signature,
        )
        if sidecar is not None:
            summary, receipt_signature = sidecar
            cache_signature = (manifest_signature, receipt_signature)
            with self._cache_lock:
                self._manifest_list_summary_cache[path] = (
                    cache_signature,
                    summary,
                )
            return copy.deepcopy(summary)

        manifest = self._manifest_value(path)
        receipt_path = (
            self._result_path(path, manifest)
            if manifest.get("kind") == "human"
            else None
        )
        receipt_signature = (
            self._result_receipt_file_signature(receipt_path)
            if receipt_path is not None
            else None
        )
        cache_signature = (manifest_signature, receipt_signature)
        if receipt_path is not None:
            receipt_changed = receipt_path.is_symlink()
            if receipt_path.is_file() and not receipt_changed:
                receipt_changed = (
                    hashlib.sha256(receipt_path.read_bytes()).hexdigest()
                    != manifest.get("resultReceiptHash")
                )
            if receipt_changed:
                manifest = self._apply_result_receipt(
                    path,
                    copy.deepcopy(manifest),
                )
                manifest_signature = self._manifest_file_signature(path)
                receipt_signature = self._result_receipt_file_signature(
                    receipt_path
                )
                cache_signature = (manifest_signature, receipt_signature)
        summary = self._public_list_summary(manifest)
        with self._cache_lock:
            self._manifest_list_summary_cache[path] = (
                cache_signature,
                summary,
            )
        self._write_manifest_list_summary_sidecar(
            path,
            manifest_signature,
            receipt_signature,
            summary,
        )
        return copy.deepcopy(summary)

    def _read_manifest_list_summary_sidecar(
        self,
        manifest_path: Path,
        manifest_signature: tuple[int, int, int],
    ) -> tuple[
        dict[str, Any],
        tuple[int, int, int, bool] | None,
    ] | None:
        path = manifest_path.with_name("list_summary.json")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if (
            not isinstance(value, Mapping)
            or value.get("schemaVersion") != MANIFEST_LIST_SUMMARY_SCHEMA
            or value.get("manifestSignature") != list(manifest_signature)
            or not isinstance(value.get("summary"), Mapping)
        ):
            return None
        summary = copy.deepcopy(dict(value["summary"]))
        receipt_signature = self._list_summary_receipt_signature(
            manifest_path,
            summary,
        )
        stored_receipt_signature = value.get("receiptSignature")
        if stored_receipt_signature != (
            list(receipt_signature) if receipt_signature is not None else None
        ):
            return None
        return summary, receipt_signature

    def _write_manifest_list_summary_sidecar(
        self,
        manifest_path: Path,
        manifest_signature: tuple[int, int, int],
        receipt_signature: tuple[int, int, int, bool] | None,
        summary: Mapping[str, Any],
    ) -> None:
        try:
            self._write_json(
                manifest_path.with_name("list_summary.json"),
                {
                    "schemaVersion": MANIFEST_LIST_SUMMARY_SCHEMA,
                    "manifestSignature": list(manifest_signature),
                    "receiptSignature": (
                        list(receipt_signature)
                        if receipt_signature is not None
                        else None
                    ),
                    "summary": copy.deepcopy(dict(summary)),
                },
            )
        except OSError:
            # This sidecar is a derived read cache. Manifest writes and run
            # execution must remain authoritative if the cache cannot be saved.
            pass

    def _list_summary_receipt_signature(
        self,
        manifest_path: Path,
        summary: Mapping[str, Any],
    ) -> tuple[int, int, int, bool] | None:
        if summary.get("kind") != "human":
            return None
        return self._result_receipt_file_signature(
            self._result_path(manifest_path, summary)
        )

    @staticmethod
    def _result_receipt_file_signature(
        path: Path,
    ) -> tuple[int, int, int, bool] | None:
        try:
            stat = path.lstat()
        except OSError:
            return None
        return (
            stat.st_ino,
            stat.st_mtime_ns,
            stat.st_size,
            path.is_symlink(),
        )

    def _write_manifest(self, path: Path, manifest: Mapping[str, Any]) -> None:
        if self.question_states.is_current(manifest):
            manifest = {
                key: value
                for key, value in manifest.items()
                if key not in PLAN_OWNED_FIELDS
            }
        requires_recovery = self._requires_startup_recovery(manifest)
        recovery_path = path.with_name("recovery.json")
        if requires_recovery:
            self._write_json(
                recovery_path,
                {
                    "schemaVersion": RECOVERY_SIDECAR_SCHEMA,
                    "qualification": manifest.get("qualification"),
                    "runId": manifest.get("runId"),
                    "kind": manifest.get("kind"),
                    "status": manifest.get("status"),
                    "updatedAt": manifest.get("updatedAt"),
                },
            )
        QualificationRunStore._write_json(path, manifest)
        if not requires_recovery:
            recovery_path.unlink(missing_ok=True)
        cached = copy.deepcopy(dict(manifest))
        self._remember_manifest(
            path,
            self._manifest_file_signature(path),
            cached,
        )
        signature = self._manifest_file_signature(path)
        with self._cache_lock:
            self._manifest_header_cache[path] = (
                signature,
                bool(manifest.get("parentRunId")),
            )
        receipt_signature = (
            self._result_receipt_file_signature(
                self._result_path(path, manifest)
            )
            if manifest.get("kind") == "human"
            else None
        )
        summary = self._public_list_summary(manifest)
        with self._cache_lock:
            self._manifest_list_summary_cache[path] = (
                (
                    signature,
                    receipt_signature,
                ),
                summary,
            )
        self._write_manifest_list_summary_sidecar(
            path,
            signature,
            receipt_signature,
            summary,
        )
        self._update_dashboard_run_index(manifest, summary)

    def _read_dashboard_run_index(
        self,
        qualification: str,
    ) -> list[dict[str, Any]] | None:
        with self._cache_lock:
            cached = self._dashboard_run_index_cache.get(qualification)
        if cached is not None:
            return copy.deepcopy(cached)
        path = self.root / qualification / "dashboard_runs.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if (
            not isinstance(value, Mapping)
            or value.get("schemaVersion") != DASHBOARD_RUN_INDEX_SCHEMA
            or value.get("qualification") != qualification
            or value.get("complete") is not True
            or not isinstance(value.get("runs"), list)
            or any(not isinstance(run, Mapping) for run in value["runs"])
        ):
            return None
        runs = [copy.deepcopy(dict(run)) for run in value["runs"]]
        with self._cache_lock:
            self._dashboard_run_index_cache[qualification] = runs
        return copy.deepcopy(runs)

    def _write_dashboard_run_index(
        self,
        qualification: str,
        runs: Iterable[Mapping[str, Any]],
    ) -> None:
        normalized = [
            copy.deepcopy(dict(run))
            for run in runs
            if isinstance(run, Mapping)
            and not run.get("parentRunId")
            and str(run.get("qualification") or "") == qualification
            and run.get("runId")
            and run.get("workType") not in DASHBOARD_RUN_EXCLUDED_WORK_TYPES
            and run.get("schemaVersion")
            not in DASHBOARD_RUN_EXCLUDED_SCHEMA_VERSIONS
        ]
        normalized.sort(
            key=lambda run: (
                str(run.get("updatedAt") or run.get("createdAt") or ""),
                str(run.get("runId") or ""),
            ),
            reverse=True,
        )
        normalized = normalized[:DASHBOARD_RUN_INDEX_LIMIT]
        with self._cache_lock:
            self._dashboard_run_index_cache[qualification] = normalized
        try:
            self._write_json(
                self.root / qualification / "dashboard_runs.json",
                {
                    "schemaVersion": DASHBOARD_RUN_INDEX_SCHEMA,
                    "qualification": qualification,
                    "complete": True,
                    "runs": normalized,
                },
            )
        except OSError:
            # The index is derived data. Manifest persistence remains the
            # authority if the compact dashboard cache cannot be written.
            pass

    def _update_dashboard_run_index(
        self,
        manifest: Mapping[str, Any],
        summary: Mapping[str, Any],
    ) -> None:
        if manifest.get("parentRunId"):
            return
        qualification = str(manifest.get("qualification") or "")
        run_id = str(manifest.get("runId") or "")
        if not qualification or not run_id:
            return
        index_path = self.root / qualification / "dashboard_runs.json"
        with self._path_lock(index_path):
            indexed = self._read_dashboard_run_index(qualification)
            if indexed is None:
                return
            indexed = [
                run
                for run in indexed
                if str(run.get("runId") or "") != run_id
            ]
            indexed.append(copy.deepcopy(dict(summary)))
            self._write_dashboard_run_index(qualification, indexed)

    @staticmethod
    def _requires_startup_recovery(manifest: Mapping[str, Any]) -> bool:
        status = str(manifest.get("status") or "")
        if status in LIVE_RUN_STATUSES:
            return True
        return bool(
            status == "interrupted"
            and manifest.get("kind") == "orchestration"
            and manifest.get("schemaVersion") == QUESTION_RUN_SCHEMA_VERSION
            and str(manifest.get("queueStatus") or "") == "running"
        )

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

    @staticmethod
    def _public_list_summary(manifest: Mapping[str, Any]) -> dict[str, Any]:
        return {
            field: copy.deepcopy(value)
            for field, value in manifest.items()
            if field not in RUN_LIST_HEAVY_FIELDS
            and field != "resultReceiptHash"
        }


def _resume_selection_matches(
    previous_values: list[str],
    current_values: list[str],
    *,
    law_workflow_enabled: Any,
    allowed_law_removals: set[str],
) -> bool:
    """Allow only the policy-defined law workflow shrink on resume."""

    if previous_values == current_values:
        return True
    if law_workflow_enabled is not False:
        return False
    return [
        value for value in previous_values if value not in allowed_law_removals
    ] == current_values


def _resume_orchestration_selections_match(
    previous: Mapping[str, Any],
    plan: Mapping[str, Any],
    selected_stage_ids: list[str],
    *,
    compare_update_targets: bool,
) -> bool:
    previous_update_target_ids = [
        str(value) for value in previous.get("selectedUpdateTargetIds") or []
    ]
    allowed_law_update_target_removals = {
        value
        for value in previous_update_target_ids
        if value in LAW_WORKFLOW_UPDATE_TARGET_IDS
        or value.partition(".")[0] in LAW_WORKFLOW_STAGE_IDS
    }
    law_workflow_enabled = plan.get("lawWorkflowEnabled")
    return _resume_selection_matches(
        [str(value) for value in previous.get("stageIds") or []],
        selected_stage_ids,
        law_workflow_enabled=law_workflow_enabled,
        allowed_law_removals=LAW_WORKFLOW_STAGE_IDS,
    ) and (
        not compare_update_targets
        or _resume_selection_matches(
            previous_update_target_ids,
            [
                str(value)
                for value in plan.get("selectedUpdateTargetIds") or []
            ],
            law_workflow_enabled=law_workflow_enabled,
            allowed_law_removals=allowed_law_update_target_removals,
        )
    )


def _restore_resume_target_aliases(
    plan: dict[str, Any],
    previous: Mapping[str, Any],
) -> None:
    """Restore only aliases previously bound to the same complete source identity."""

    trusted: dict[SourceIdentityBinding, set[str]] = {}
    for raw in previous.get("targetRecordBindings") or []:
        if not isinstance(raw, Mapping):
            continue
        identity = SourceIdentityBinding.from_mapping(raw)
        if not identity.is_complete():
            continue
        aliases = {
            str(value).strip()
            for value in raw.get("aliases") or []
            if str(value).strip()
        }
        aliases.update(identity.as_tuple())
        trusted.setdefault(identity, set()).update(aliases)
    if not trusted:
        return

    def aliases_for(value: Mapping[str, Any]) -> set[str]:
        identity = SourceIdentityBinding.from_mapping(value)
        if not identity.is_complete():
            return {
                str(alias).strip()
                for alias in value.get("aliases") or []
                if str(alias).strip()
            }
        return {
            str(alias).strip()
            for alias in value.get("aliases") or []
            if str(alias).strip()
        } | trusted.get(identity, set()) | set(identity.as_tuple())

    def expand_alias_group(raw_group: Any) -> list[str]:
        aliases = {
            str(value).strip()
            for value in raw_group or []
            if str(value).strip()
        }
        matching = [
            trusted_aliases
            for identity, trusted_aliases in trusted.items()
            if set(identity.as_tuple()).issubset(aliases)
        ]
        if len(matching) == 1:
            aliases.update(matching[0])
        return sorted(aliases)

    def restore(container: dict[str, Any]) -> None:
        bindings = [
            dict(raw)
            for raw in container.get("targetRecordBindings") or []
            if isinstance(raw, Mapping)
        ]
        for binding in bindings:
            binding["aliases"] = sorted(aliases_for(binding))
        if bindings:
            container["targetRecordBindings"] = bindings
            container["targetRecordAliasGroups"] = [
                aliases
                for aliases in dict.fromkeys(
                    tuple(binding["aliases"])
                    for binding in bindings
                    if binding.get("aliases")
                )
            ]

        progress_targets = [
            dict(raw)
            for raw in container.get("progressTargets") or []
            if isinstance(raw, Mapping)
        ]
        for target in progress_targets:
            target["aliases"] = sorted(aliases_for(target))
        if progress_targets:
            container["progressTargets"] = progress_targets

        for field in ("targetSourceRecordScopes", "targetRecordScopes"):
            scopes = container.get(field)
            if not isinstance(scopes, Mapping):
                continue
            container[field] = {
                str(path): [
                    expand_alias_group(group)
                    for group in groups or []
                    if isinstance(group, (list, tuple, set))
                ]
                for path, groups in scopes.items()
            }

    restore(plan)
    stage_plans = plan.get("stagePlans")
    if isinstance(stage_plans, list):
        restored_stage_plans: list[dict[str, Any]] = []
        for raw in stage_plans:
            if not isinstance(raw, Mapping):
                continue
            stage_plan = dict(raw)
            restore(stage_plan)
            restored_stage_plans.append(stage_plan)
        plan["stagePlans"] = restored_stage_plans


def _question_work_preview_group_summary(
    plan: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Summarize an existing question-work plan without changing its scope."""

    if str(plan.get("kind") or "") not in {"human", "orchestration"}:
        return []
    progress_targets = plan.get("progressTargets")
    if progress_targets is None:
        # setup/category_setup are qualification-scope human work. They may
        # reference every source and group, but they do not enqueue one work
        # item per question and therefore have no question-level group summary.
        return []
    if not isinstance(progress_targets, list):
        raise QualificationRunError("previewの問題target形式が不正です。")
    if not progress_targets:
        return []
    group_ids = [
        str(value)
        for value in (
            plan.get("scopeListGroupIds") or plan.get("targetGroupIds") or []
        )
        if value
    ]
    if len(group_ids) != len(set(group_ids)):
        raise QualificationRunError("previewのgroup指定が重複しています。")
    if len(group_ids) < 2:
        return []
    group_counts = {
        group_id: {"questionCount": 0, "workItemCount": 0}
        for group_id in group_ids
    }
    targets_by_id: dict[str, str] = {}
    for raw_target in progress_targets:
        if not isinstance(raw_target, Mapping):
            raise QualificationRunError("previewの問題targetが不正です。")
        question_id = str(
            raw_target.get("id") or raw_target.get("questionKey") or ""
        ).strip()
        group_id = str(raw_target.get("listGroupId") or "").strip()
        if not question_id or question_id in targets_by_id:
            raise QualificationRunError("previewの問題targetが重複又は欠損しています。")
        if group_id not in group_counts:
            raise QualificationRunError(
                f"previewの問題targetに未知のgroupがあります: {group_id or '-'}"
            )
        targets_by_id[question_id] = group_id
        group_counts[group_id]["questionCount"] += 1
    target_count = int(plan.get("targetCount") or 0)
    if len(targets_by_id) != target_count:
        raise QualificationRunError(
            "previewの問題target集計が実行planと一致しません。"
        )

    try:
        executions = build_question_executions(plan)
    except QuestionWorkQueueError as exc:
        raise QualificationRunError(str(exc)) from exc
    queue_totals = queue_summary(executions)
    seen_execution_ids: set[str] = set()
    for execution in executions:
        question_id = str(execution.get("questionId") or "").strip()
        group_id = str(execution.get("listGroupId") or "").strip()
        if not question_id or question_id in seen_execution_ids:
            raise QualificationRunError("previewのqueue問題が重複又は欠損しています。")
        if group_id not in group_counts:
            raise QualificationRunError(
                f"previewのqueueに未知のgroupがあります: {group_id or '-'}"
            )
        if targets_by_id.get(question_id) != group_id:
            raise QualificationRunError(
                "previewのqueueと問題groupが一致しません。"
            )
        seen_execution_ids.add(question_id)
        group_counts[group_id]["workItemCount"] += len(
            [stage for stage in execution.get("stages") or [] if isinstance(stage, Mapping)]
        )
    work_item_count = sum(
        int(summary["workItemCount"]) for summary in group_counts.values()
    )
    if (
        int(queue_totals.get("questionCount") or 0) != target_count
        or seen_execution_ids != set(targets_by_id)
        or work_item_count != int(queue_totals.get("workItemCount") or 0)
    ):
        raise QualificationRunError(
            "previewのgroup集計が実queueと一致しません。"
        )
    return [
        {"listGroupId": group_id, **group_counts[group_id]}
        for group_id in group_ids
    ]


PREPARED_PREVIEW_CACHE_MAX_ENTRIES = 2
PREPARED_PREVIEW_CACHE_TTL_SECONDS = 30 * 60


@dataclass
class _PreparedPreviewEntry:
    request_key: str
    preview_token: str
    plan: dict[str, Any]
    preview: dict[str, Any]
    source_stamp: str
    stored_at: float


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
        reviews: Any | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.workflow = workflow
        self.synchronizer = synchronizer
        self.jobs = jobs
        self.secret = secret.encode("utf-8")
        self.store = store or QualificationRunStore(self.repo_root)
        self.app_server = app_server
        self.reviews = reviews
        self.work_versions = (
            work_versions
            or getattr(workflow, "work_versions", None)
            or QuestionWorkVersionStore(self.repo_root)
        )
        self.primary_law_evidence = PrimaryLawEvidenceResolver(self.repo_root)
        self._parent_heartbeat_lock = threading.Lock()
        self._parent_heartbeat_monotonic: dict[tuple[str, str], float] = {}
        self._prepared_preview_lock = threading.Condition(threading.Lock())
        self._prepared_previews: OrderedDict[str, _PreparedPreviewEntry] = (
            OrderedDict()
        )
        self._prepared_preview_tokens: dict[str, str] = {}
        self._preparing_preview_keys: set[str] = set()

    def _prepared_preview_source_stamp(
        self,
        qualification: str,
        resumed_from: str | None,
    ) -> str:
        canonical_roots = [
            self.repo_root / "output" / qualification,
            self.repo_root / "prompt",
            self.repo_root / "config" / "question_maintenance_workflow.toml",
            self.store.root / qualification,
        ]
        evidence: dict[str, Any] = {
            "qualification": qualification,
            "resumedFrom": resumed_from,
            "roots": [],
        }

        def stat_value(path: Path) -> list[Any]:
            try:
                value = path.stat()
            except OSError:
                return [str(path.relative_to(self.repo_root)), None]
            return [
                str(path.relative_to(self.repo_root)),
                int(value.st_mtime_ns),
                int(value.st_size),
                int(value.st_mode),
            ]

        evidence["roots"] = [stat_value(path) for path in canonical_roots]
        if resumed_from:
            evidence["resumeManifest"] = stat_value(
                self.store._manifest_path(qualification, resumed_from)
            )

        # canonical scopeがGit管理下なら、clean/dirty path集合とdirty fileの
        # stat、index identityをbindする。既にdirtyなfileの再変更もmtime/size
        # で検出し、cache利用時は現行plan再計算へfail closedする。
        if not (self.repo_root / ".git").exists():
            evidence["gitRepository"] = False
            return _canonical_json_hash(evidence)
        try:
            inside = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self.repo_root,
                check=True,
                capture_output=True,
                timeout=30,
            )
            if inside.stdout.strip() == b"true":
                head = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=self.repo_root,
                    check=True,
                    capture_output=True,
                    timeout=30,
                ).stdout.strip()
                evidence["gitHead"] = head.decode(
                    "ascii", errors="surrogateescape"
                )
                pathspecs = [
                    str(path.relative_to(self.repo_root))
                    for path in canonical_roots
                ]
                status = subprocess.run(
                    [
                        "git",
                        "status",
                        "--porcelain=v1",
                        "-z",
                        "--untracked-files=all",
                        "--",
                        *pathspecs,
                    ],
                    cwd=self.repo_root,
                    check=True,
                    capture_output=True,
                    timeout=30,
                ).stdout
                dirty_paths: list[str] = []
                for record in status.split(b"\0"):
                    if len(record) < 4:
                        continue
                    decoded = record.decode("utf-8", errors="surrogateescape")
                    path_value = decoded[3:]
                    if path_value:
                        dirty_paths.append(path_value)
                evidence["gitStatusHash"] = hashlib.sha256(status).hexdigest()
                evidence["dirtyPaths"] = [
                    stat_value(self.repo_root / path)
                    for path in sorted(set(dirty_paths))
                ]
                staged = subprocess.run(
                    [
                        "git",
                        "diff",
                        "--cached",
                        "--raw",
                        "-z",
                        "--",
                        *pathspecs,
                    ],
                    cwd=self.repo_root,
                    check=True,
                    capture_output=True,
                    timeout=30,
                ).stdout
                evidence["gitCachedHash"] = hashlib.sha256(staged).hexdigest()
        except (OSError, subprocess.SubprocessError):
            # unit-test用の非Git一時repoではroot statだけを使う。本番Git repoで
            # 一時的にGit照合不能ならstampが変わりcache missとなる。
            evidence["gitUnavailableAt"] = time.monotonic_ns()
        return _canonical_json_hash(evidence)

    @staticmethod
    def _prepared_preview_request_key(
        qualification: str,
        stage_id: str,
        mode: str,
        *,
        stage_ids: list[str] | None,
        list_group_ids: list[str] | None,
        update_target_ids: list[str] | None,
        question_ids: list[str] | None,
        resumed_from: str | None,
        evaluation_rework_snapshots: Mapping[str, Mapping[str, Any]] | None,
        blocked_rework_from: str | None,
    ) -> str:
        # 同時処理数とspeedは対象scopeを変えずpreview tokenにも含まれない。
        # 一方、順序を含む選択scopeと再開元はexactにbindする。
        selected_stage_ids = list(dict.fromkeys(stage_ids or [stage_id]))
        return _canonical_json_hash(
            {
                "qualification": qualification,
                "stageIds": selected_stage_ids,
                "mode": mode,
                "listGroupIds": list_group_ids,
                "updateTargetIds": update_target_ids,
                "questionIds": question_ids,
                "resumedFrom": resumed_from,
                "evaluationReworkSnapshots": evaluation_rework_snapshots,
                "blockedReworkFrom": blocked_rework_from,
            }
        )

    def _purge_prepared_previews_locked(self, now: float) -> None:
        expired = [
            request_key
            for request_key, entry in self._prepared_previews.items()
            if now - entry.stored_at > PREPARED_PREVIEW_CACHE_TTL_SECONDS
        ]
        for request_key in expired:
            entry = self._prepared_previews.pop(request_key)
            self._prepared_preview_tokens.pop(entry.preview_token, None)

    @staticmethod
    def _preview_with_execution_settings(
        preview: Mapping[str, Any],
        *,
        question_concurrency: int,
        speed_mode: str,
    ) -> dict[str, Any]:
        result = copy.deepcopy(dict(preview))
        result["questionConcurrency"] = question_concurrency
        result["speedMode"] = speed_mode
        return result

    def _begin_prepared_preview(
        self,
        request_key: str,
        *,
        source_stamp: str,
        question_concurrency: int,
        speed_mode: str,
    ) -> dict[str, Any] | None:
        # 同じ巨大projectionの同時再計算をsingle-flightにする。clientが先に
        # timeoutしてもserver側の最初の計算が完了すれば次回は同じ結果を返す。
        with self._prepared_preview_lock:
            while True:
                self._purge_prepared_previews_locked(time.monotonic())
                cached = self._prepared_previews.get(request_key)
                if cached is not None:
                    if not hmac.compare_digest(cached.source_stamp, source_stamp):
                        self._prepared_previews.pop(request_key, None)
                        self._prepared_preview_tokens.pop(
                            cached.preview_token, None
                        )
                        continue
                    self._prepared_previews.move_to_end(request_key)
                    return self._preview_with_execution_settings(
                        cached.preview,
                        question_concurrency=question_concurrency,
                        speed_mode=speed_mode,
                    )
                if request_key not in self._preparing_preview_keys:
                    self._preparing_preview_keys.add(request_key)
                    return None
                self._prepared_preview_lock.wait()

    def _finish_prepared_preview(
        self,
        request_key: str,
        plan: dict[str, Any],
        preview: dict[str, Any],
        source_stamp: str,
    ) -> None:
        entry = _PreparedPreviewEntry(
            request_key=request_key,
            preview_token=str(preview["previewToken"]),
            plan=plan,
            preview=copy.deepcopy(preview),
            source_stamp=source_stamp,
            stored_at=time.monotonic(),
        )
        with self._prepared_preview_lock:
            previous = self._prepared_previews.pop(request_key, None)
            if previous is not None:
                self._prepared_preview_tokens.pop(previous.preview_token, None)
            self._prepared_previews[request_key] = entry
            self._prepared_preview_tokens[entry.preview_token] = request_key
            while len(self._prepared_previews) > PREPARED_PREVIEW_CACHE_MAX_ENTRIES:
                _, evicted = self._prepared_previews.popitem(last=False)
                self._prepared_preview_tokens.pop(evicted.preview_token, None)
            self._preparing_preview_keys.discard(request_key)
            self._prepared_preview_lock.notify_all()

    def _abort_prepared_preview(self, request_key: str) -> None:
        with self._prepared_preview_lock:
            self._preparing_preview_keys.discard(request_key)
            self._prepared_preview_lock.notify_all()

    def _take_prepared_preview(
        self,
        request_key: str,
        preview_token: str,
        *,
        source_stamp: str,
        question_concurrency: int,
        speed_mode: str,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        with self._prepared_preview_lock:
            self._purge_prepared_previews_locked(time.monotonic())
            bound_request_key = self._prepared_preview_tokens.get(preview_token)
            if bound_request_key != request_key:
                return None
            entry = self._prepared_previews.pop(request_key, None)
            if entry is None or not hmac.compare_digest(
                entry.preview_token, preview_token
            ) or not hmac.compare_digest(entry.source_stamp, source_stamp):
                self._prepared_preview_tokens.pop(preview_token, None)
                if entry is not None and entry.preview_token != preview_token:
                    self._prepared_preview_tokens.pop(entry.preview_token, None)
                return None
            self._prepared_preview_tokens.pop(entry.preview_token, None)
        return (
            entry.plan,
            self._preview_with_execution_settings(
                entry.preview,
                question_concurrency=question_concurrency,
                speed_mode=speed_mode,
            ),
        )

    @staticmethod
    def _monitor_context(
        qualification: str,
        run_id: str,
        *,
        parent_run_id: str = "",
        question_ids: Iterable[str] = (),
        work_item_keys: Iterable[str] = (),
        list_group_ids: Iterable[str] = (),
        stage_id: str = "",
        work_type: str = "",
        phase: str = "",
    ) -> dict[str, Any]:
        """Build the stable, prompt-free identity projection for monitor events."""

        normalized_questions = list(
            dict.fromkeys(str(value) for value in question_ids if value)
        )
        normalized_work_items = list(
            dict.fromkeys(str(value) for value in work_item_keys if value)
        )
        normalized_groups = list(
            dict.fromkeys(str(value) for value in list_group_ids if value)
        )
        parent_run_id = str(parent_run_id or "")
        context: dict[str, Any] = {
            "qualification": str(qualification),
            "runId": parent_run_id or str(run_id),
            "questionIds": normalized_questions,
            "workItemKeys": normalized_work_items,
            "listGroupIds": normalized_groups,
            "stageId": str(stage_id or ""),
            "workType": str(work_type or ""),
            "phase": str(phase or ""),
        }
        if parent_run_id:
            context["parentRunId"] = parent_run_id
            context["childRunId"] = str(run_id)
        if len(normalized_questions) == 1:
            context["questionId"] = normalized_questions[0]
        if len(normalized_work_items) == 1:
            context["workItemKey"] = normalized_work_items[0]
        return context

    def _touch_parent_heartbeat(
        self,
        qualification: str,
        parent_run_id: str,
        heartbeat_at: str,
    ) -> None:
        """Coalesce concurrent child heartbeats into one parent write."""

        key = (qualification, parent_run_id)
        observed = time.monotonic()
        with self._parent_heartbeat_lock:
            previous = self._parent_heartbeat_monotonic.get(key, 0.0)
            if observed - previous < PREPARATION_HEARTBEAT_SECONDS:
                return
            self._parent_heartbeat_monotonic[key] = observed
        try:
            self.store.update(
                qualification,
                parent_run_id,
                heartbeatAt=heartbeat_at,
            )
        except Exception:
            with self._parent_heartbeat_lock:
                if self._parent_heartbeat_monotonic.get(key) == observed:
                    self._parent_heartbeat_monotonic.pop(key, None)
            raise

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

    def _run_in_turn_group(
        self,
        qualification: str,
        worker: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            with qualification_run_lease(
                self.repo_root,
                qualification,
            ):
                if (
                    self.app_server is None
                    or not hasattr(self.app_server, "turn_group")
                ):
                    return worker()
                with self.app_server.turn_group(qualification):
                    return worker()
        except ProcessLeaseError as exc:
            raise QualificationRunError(str(exc)) from exc

    def _apply_evaluation_rework_plan(
        self,
        plan: dict[str, Any],
        snapshots: Mapping[str, Mapping[str, Any]] | None,
    ) -> None:
        if snapshots is None:
            return
        if not snapshots:
            raise QualificationRunError(
                "再整備する評価結果がありません。"
            )
        selection_work_item_count = _question_work_selection_count(plan)
        selected_stage_ids = {
            str(value)
            for value in plan.get("stageIds") or [plan.get("stageId")]
            if value and str(value) != "multi"
        }
        targets_by_question = {
            str(target.get("id") or target.get("uiQuestionId") or ""): target
            for stage_plan in (
                plan.get("stagePlans")
                if isinstance(plan.get("stagePlans"), list)
                else [plan]
            )
            if isinstance(stage_plan, Mapping)
            for target in stage_plan.get("progressTargets") or []
            if isinstance(target, Mapping)
            and (target.get("id") or target.get("uiQuestionId"))
        }
        target_stage_ids: dict[str, list[str]] = {}
        feedback_by_question: dict[str, list[dict[str, Any]]] = {}
        for question_id, raw_snapshot in snapshots.items():
            normalized_question_id = str(question_id)
            snapshot = dict(raw_snapshot)
            if str(snapshot.get("status") or "") != "needs_rework":
                raise QualificationRunError(
                    f"要再整備ではない評価結果が含まれています: {normalized_question_id}"
                )
            target = targets_by_question.get(normalized_question_id)
            if target is None:
                raise QualificationRunError(
                    "評価結果の問題を実行planから解決できません: "
                    f"{normalized_question_id}"
                )
            snapshot_state_hash = str(snapshot.get("stateHash") or "")
            target_state_hash = str(target.get("stateHash") or "")
            if (
                not snapshot_state_hash
                or not target_state_hash
                or snapshot_state_hash != target_state_hash
            ):
                raise QualificationRunError(
                    "評価後に問題内容が更新されました。再評価してから再整備してください: "
                    f"{normalized_question_id}"
                )
            rework_items = [
                dict(item)
                for item in snapshot.get("reworkItems") or []
                if isinstance(item, Mapping)
            ]
            workflow_stage_ids = list(
                dict.fromkeys(
                    REWORK_POLICY_STAGE_IDS[stage_code]
                    for stage_code in evaluation_rework_stage_codes(snapshot)
                    if stage_code in REWORK_POLICY_STAGE_IDS
                )
            )
            if not workflow_stage_ids:
                raise QualificationRunError(
                    "評価結果から再整備工程を特定できません: "
                    f"{normalized_question_id}"
                )
            missing_stage_ids = set(workflow_stage_ids) - selected_stage_ids
            if missing_stage_ids:
                raise QualificationRunError(
                    "評価結果に必要な再整備工程が選択されていません: "
                    + ", ".join(sorted(missing_stage_ids))
                )
            target_stage_ids[normalized_question_id] = workflow_stage_ids
            feedback_by_question[normalized_question_id] = [
                {
                    "source": "independent_evaluation",
                    "status": "needs_rework",
                    "stateHash": snapshot_state_hash,
                    "resultHash": str(snapshot.get("resultHash") or ""),
                    "summary": str(snapshot.get("summary") or ""),
                    "criticalIssues": [
                        str(value)
                        for value in snapshot.get("criticalIssues") or []
                    ],
                    "answerMappingMatched": snapshot.get(
                        "answerMappingMatched"
                    ),
                    "choiceEvaluations": copy.deepcopy(
                        list(snapshot.get("choiceEvaluations") or [])
                    ),
                    "reworkItems": rework_items,
                }
            ]
        requested_question_ids = {
            str(value) for value in plan.get("questionIds") or [] if value
        }
        if requested_question_ids and requested_question_ids != set(
            target_stage_ids
        ):
            raise QualificationRunError(
                "選択した問題と評価結果が一致しません。"
            )
        plan.update(
            evaluationRework=True,
            targetStageIdsByQuestion=target_stage_ids,
            evaluationFeedbackByQuestion=feedback_by_question,
            targetCount=len(target_stage_ids),
            selectionWorkItemCount=selection_work_item_count,
            workItemCount=sum(
                len(stage_ids) for stage_ids in target_stage_ids.values()
            ),
        )

    def _apply_blocked_rework_plan(
        self,
        plan: dict[str, Any],
        blocked_rework_from: str | None,
    ) -> None:
        if not blocked_rework_from:
            return
        qualification = str(plan.get("qualification") or "")
        previous = self.store.get(qualification, blocked_rework_from)
        if (
            previous.get("status") != "succeeded"
            or previous.get("queueStatus") != "partial"
        ):
            raise QualificationRunError(
                "保留再整備の元runは、保留付きで終了したrunを指定してください。"
            )
        feedback_by_question: dict[str, list[dict[str, Any]]] = {}
        for execution in previous.get("questionExecutions") or []:
            if not isinstance(execution, Mapping):
                continue
            question_id = str(execution.get("questionId") or "")
            if not question_id or execution.get("status") != "blocked":
                continue
            blocked_stages = [
                dict(stage)
                for stage in execution.get("stages") or []
                if isinstance(stage, Mapping)
                and stage.get("status") == "blocked"
                and str(stage.get("error") or "").strip()
            ]
            reasons = list(
                dict.fromkeys(
                    str(stage.get("error") or "").strip()
                    for stage in blocked_stages
                )
            )
            if not reasons:
                continue
            feedback_by_question[question_id] = [
                {
                    "source": "blocked_maintenance",
                    "status": "needs_rework",
                    "runId": blocked_rework_from,
                    "summary": reasons[0],
                    "criticalIssues": reasons,
                    "blockedStageIds": list(
                        dict.fromkeys(
                            str(stage.get("stageId") or "")
                            for stage in blocked_stages
                            if stage.get("stageId")
                        )
                    ),
                }
            ]
        requested_question_ids = {
            str(value) for value in plan.get("questionIds") or [] if value
        }
        if not requested_question_ids:
            raise QualificationRunError(
                "保留再整備ではquestionIdsを指定してください。"
            )
        missing = requested_question_ids - set(feedback_by_question)
        if missing:
            raise QualificationRunError(
                "保留理由を確認できない問題が含まれています: "
                + ", ".join(sorted(missing))
            )
        targets_by_question = {
            str(target.get("id") or target.get("uiQuestionId") or ""): target
            for stage_plan in (
                plan.get("stagePlans")
                if isinstance(plan.get("stagePlans"), list)
                else [plan]
            )
            if isinstance(stage_plan, Mapping)
            for target in stage_plan.get("progressTargets") or []
            if isinstance(target, Mapping)
            and (target.get("id") or target.get("uiQuestionId"))
        }
        reviews = getattr(self, "reviews", None)
        if reviews is not None:
            for question_id in sorted(requested_question_ids):
                target = targets_by_question.get(question_id)
                state_hash = str(target.get("stateHash") or "") if target else ""
                review = reviews.latest_current_question_needs_review(
                    qualification,
                    question_id,
                    state_hash,
                    str(target.get("listGroupId") or "") if target else "",
                )
                if review is None:
                    continue
                feedback_by_question[question_id].append(
                    {
                        "source": "human_review",
                        "status": "needs_review",
                        "reviewId": str(review.get("reviewId") or ""),
                        "stateHash": state_hash,
                        "note": str(review.get("note") or ""),
                        "expectedOutcome": str(review.get("expectedOutcome") or ""),
                        "selection": copy.deepcopy(review.get("selection")),
                    }
                )
        plan.update(
            blockedReworkFrom=blocked_rework_from,
            evaluationFeedbackByQuestion={
                question_id: feedback_by_question[question_id]
                for question_id in sorted(requested_question_ids)
            },
        )

    def preview(
        self,
        qualification: str,
        stage_id: str,
        mode: str,
        *,
        stage_ids: list[str] | None = None,
        list_group_ids: list[str] | None = None,
        update_target_ids: list[str] | None = None,
        question_ids: list[str] | None = None,
        resumed_from: str | None = None,
        question_concurrency: int = DEFAULT_QUESTION_CONCURRENCY,
        speed_mode: str = STANDARD_SPEED_MODE,
        evaluation_rework_snapshots: Mapping[str, Mapping[str, Any]] | None = None,
        blocked_rework_from: str | None = None,
        _prepared_plan: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if _prepared_plan is not None:
            return self._preview_uncached(
                qualification,
                stage_id,
                mode,
                stage_ids=stage_ids,
                list_group_ids=list_group_ids,
                update_target_ids=update_target_ids,
                question_ids=question_ids,
                resumed_from=resumed_from,
                question_concurrency=question_concurrency,
                speed_mode=speed_mode,
                evaluation_rework_snapshots=evaluation_rework_snapshots,
                blocked_rework_from=blocked_rework_from,
                _prepared_plan=_prepared_plan,
            )

        normalized_concurrency = normalize_question_concurrency(
            question_concurrency
        )
        normalized_speed = normalize_speed_mode(speed_mode)
        request_key = self._prepared_preview_request_key(
            qualification,
            stage_id,
            mode,
            stage_ids=stage_ids,
            list_group_ids=list_group_ids,
            update_target_ids=update_target_ids,
            question_ids=question_ids,
            resumed_from=resumed_from,
            evaluation_rework_snapshots=evaluation_rework_snapshots,
            blocked_rework_from=blocked_rework_from,
        )
        source_stamp = self._prepared_preview_source_stamp(
            qualification,
            resumed_from,
        )
        cached = self._begin_prepared_preview(
            request_key,
            source_stamp=source_stamp,
            question_concurrency=normalized_concurrency,
            speed_mode=normalized_speed,
        )
        if cached is not None:
            return cached
        try:
            plan = self._plan(
                qualification,
                stage_id,
                mode,
                resumed_from,
                stage_ids=stage_ids,
                list_group_ids=list_group_ids,
                update_target_ids=update_target_ids,
                question_ids=question_ids,
            )
            self._apply_evaluation_rework_plan(
                plan,
                evaluation_rework_snapshots,
            )
            self._apply_blocked_rework_plan(plan, blocked_rework_from)
            preview = self._preview_uncached(
                qualification,
                stage_id,
                mode,
                stage_ids=stage_ids,
                list_group_ids=list_group_ids,
                update_target_ids=update_target_ids,
                question_ids=question_ids,
                resumed_from=resumed_from,
                question_concurrency=normalized_concurrency,
                speed_mode=normalized_speed,
                evaluation_rework_snapshots=evaluation_rework_snapshots,
                blocked_rework_from=blocked_rework_from,
                _prepared_plan=plan,
            )
            # 計算中にcanonical scope又はresume manifestが更新された場合は、
            # 完成planをcacheへ入れず次回の現行再計算へ送る。
            completed_source_stamp = self._prepared_preview_source_stamp(
                qualification,
                resumed_from,
            )
            if not hmac.compare_digest(source_stamp, completed_source_stamp):
                self._abort_prepared_preview(request_key)
                return preview
            self._finish_prepared_preview(
                request_key,
                plan,
                preview,
                completed_source_stamp,
            )
            return preview
        except BaseException:
            self._abort_prepared_preview(request_key)
            raise

    def _preview_uncached(
        self,
        qualification: str,
        stage_id: str,
        mode: str,
        *,
        stage_ids: list[str] | None = None,
        list_group_ids: list[str] | None = None,
        update_target_ids: list[str] | None = None,
        question_ids: list[str] | None = None,
        resumed_from: str | None = None,
        question_concurrency: int = DEFAULT_QUESTION_CONCURRENCY,
        speed_mode: str = STANDARD_SPEED_MODE,
        evaluation_rework_snapshots: Mapping[str, Mapping[str, Any]] | None = None,
        blocked_rework_from: str | None = None,
        _prepared_plan: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        question_concurrency = normalize_question_concurrency(question_concurrency)
        speed_mode = normalize_speed_mode(speed_mode)
        if _prepared_plan is None:
            plan = self._plan(
                qualification,
                stage_id,
                mode,
                resumed_from,
                stage_ids=stage_ids,
                list_group_ids=list_group_ids,
                update_target_ids=update_target_ids,
                question_ids=question_ids,
            )
            self._apply_evaluation_rework_plan(
                plan,
                evaluation_rework_snapshots,
            )
            self._apply_blocked_rework_plan(plan, blocked_rework_from)
        else:
            # 呼出し元がこのpreview専用に所有するplan。previewは読み取り専用で
            # 扱い、100MB級projectionの不要なdeepcopyを避ける。
            plan = dict(_prepared_plan)
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
        # 並列数は対象範囲を変えない実行設定であり、許可値はstart時にも
        # serverが検証する。切替のたびに高コストな対象計算をやり直さない。
        target_identity = (
            _question_work_target_identity(plan)
            if plan["kind"] != "machine"
            and bool(plan.get("progressTargets") or plan.get("stagePlans"))
            else None
        )
        token_payload = {
            "plan": plan,
            "groupPreviews": group_previews,
            "targetIdentity": target_identity,
        }
        preview_plan_hash = _canonical_json_hash(token_payload)
        group_summary = _question_work_preview_group_summary(plan)
        actual_work_item_count = (
            len(target_identity["workItemKeys"])
            if target_identity is not None
            else int(plan.get("workItemCount") or plan["targetCount"])
        )
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
            "questionConcurrency": question_concurrency,
            "speedMode": speed_mode,
            "requestedServiceTier": None,
            "targetCount": plan["targetCount"],
            "workItemCount": int(plan.get("workItemCount") or plan["targetCount"]),
            "stageCount": int(
                plan.get("stageCount") or len(plan.get("stageIds") or [plan["stageId"]])
            ),
            "targetGroupIds": plan["targetGroupIds"],
            "groupSummary": group_summary,
            "questionWorkItemCount": sum(
                int(group.get("workItemCount") or 0) for group in group_summary
            ) or actual_work_item_count,
            "scopeListGroupId": plan.get("scopeListGroupId"),
            "scopeListGroupIds": list(plan.get("scopeListGroupIds") or []),
            "requestedQuestionIds": list(plan.get("questionIds") or []),
            "questionIds": list(plan.get("questionIds") or []),
            "updateTargets": list(plan.get("updateTargets") or []),
            "selectedUpdateTargets": list(
                plan.get("selectedUpdateTargets") or []
            ),
            "selectedUpdateTargetIds": list(
                plan.get("selectedUpdateTargetIds") or []
            ),
            "selectedFieldsByStage": dict(
                plan.get("selectedFieldsByStage") or {}
            ),
            "readFieldsByStage": dict(plan.get("readFieldsByStage") or {}),
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
            "evaluationRework": bool(plan.get("evaluationRework")),
            "targetIdentity": target_identity,
            "previewPlanHash": preview_plan_hash,
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
        list_group_ids: list[str] | None = None,
        update_target_ids: list[str] | None = None,
        question_ids: list[str] | None = None,
        resumed_from: str | None = None,
        question_concurrency: int = DEFAULT_QUESTION_CONCURRENCY,
        speed_mode: str = STANDARD_SPEED_MODE,
        evaluation_rework_snapshots: Mapping[str, Mapping[str, Any]] | None = None,
        blocked_rework_from: str | None = None,
        hydrate_result: bool = True,
    ) -> dict[str, Any]:
        question_concurrency = normalize_question_concurrency(question_concurrency)
        speed_mode = normalize_speed_mode(speed_mode)
        request_key = self._prepared_preview_request_key(
            qualification,
            stage_id,
            mode,
            stage_ids=stage_ids,
            list_group_ids=list_group_ids,
            update_target_ids=update_target_ids,
            question_ids=question_ids,
            resumed_from=resumed_from,
            evaluation_rework_snapshots=evaluation_rework_snapshots,
            blocked_rework_from=blocked_rework_from,
        )
        prepared = self._take_prepared_preview(
            request_key,
            preview_token,
            source_stamp=self._prepared_preview_source_stamp(
                qualification,
                resumed_from,
            ),
            question_concurrency=question_concurrency,
            speed_mode=speed_mode,
        )
        if prepared is not None:
            plan, preview = prepared
        else:
            plan = self._plan(
                qualification,
                stage_id,
                mode,
                resumed_from,
                stage_ids=stage_ids,
                list_group_ids=list_group_ids,
                update_target_ids=update_target_ids,
                question_ids=question_ids,
            )
            self._apply_evaluation_rework_plan(
                plan,
                evaluation_rework_snapshots,
            )
            self._apply_blocked_rework_plan(plan, blocked_rework_from)
            preview = self.preview(
                qualification,
                stage_id,
                mode,
                stage_ids=stage_ids,
                list_group_ids=list_group_ids,
                update_target_ids=update_target_ids,
                question_ids=question_ids,
                resumed_from=resumed_from,
                question_concurrency=question_concurrency,
                speed_mode=speed_mode,
                evaluation_rework_snapshots=evaluation_rework_snapshots,
                blocked_rework_from=blocked_rework_from,
                _prepared_plan=plan,
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

        plan = {
            **plan,
            "targetIdentity": copy.deepcopy(preview["targetIdentity"]),
            "previewPlanHash": str(preview["previewPlanHash"]),
        }
        if plan["kind"] == "human":
            selected_stage_ids = list(plan.get("stageIds") or [stage_id])
            prompt_scope = {}
            if list_group_ids is not None:
                prompt_scope["list_group_ids"] = list_group_ids
            if update_target_ids is not None:
                prompt_scope["update_target_ids"] = update_target_ids
            if question_ids is not None:
                prompt_scope["question_ids"] = question_ids
            prompt_from_plan = getattr(
                self.workflow,
                "prompt_from_plan",
                None,
            )
            if callable(prompt_from_plan):
                prompt = prompt_from_plan(plan)["prompt"]
            elif len(selected_stage_ids) > 1:
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
                "speedMode": speed_mode,
                "requestedServiceTier": None,
            }
            maintenance_phases = _maintenance_session_phases(plan)
            question_executions, actual_queue_summary = (
                _validated_question_work_queue(plan)
            )
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
                    "workItemCount": actual_queue_summary["workItemCount"],
                    "kind": "orchestration",
                    "workType": "maintenance_flow",
                    "questionConcurrency": question_concurrency,
                    "parallelStrategy": "rolling_question_window",
                    "throughputMode": "auto_max",
                    "modelBatchSize": DEFAULT_MAX_QUESTIONS_PER_TURN,
                    "modelWorkerLimit": question_concurrency,
                    "parallelWorkerLimit": min(
                        question_concurrency,
                        int(plan.get("targetCount") or 1),
                    ),
                    "phaseExecutions": phase_executions,
                    "currentPhaseId": None,
                    "childRunIds": [],
                    "questionExecutions": question_executions,
                    "queueStatus": "queued",
                    "queueOrder": "question_turn",
                }
                run = self.store.create(
                    flow_plan,
                    status="queued",
                    prompt=prompt,
                    resumed_from=resumed_from,
                    append_receipt_contract=False,
                    hydrate_result=hydrate_result,
                )
                try:
                    job = self.jobs.start(
                        kind="codex-maintenance-flow",
                        key=qualification_operation_key(qualification),
                        worker=lambda emit: self._run_in_turn_group(
                            qualification,
                            lambda: self._run_with_technical_log(
                                qualification,
                                run["runId"],
                                emit,
                                lambda logged_emit: self._run_maintenance_flow(
                                    qualification,
                                    run["runId"],
                                    logged_emit,
                                ),
                            ),
                        ),
                    )
                except JobConflictError:
                    self.store.update(
                        qualification,
                        run["runId"],
                        hydrate_result=hydrate_result,
                        status="failed",
                        error="この資格で別の整備処理が実行中です。",
                    )
                    raise
                run = self.store.update(
                    qualification,
                    run["runId"],
                    hydrate_result=hydrate_result,
                    jobId=job["jobId"],
                )
                return {"run": run, "prompt": None, "job": job}
            run = self.store.create(
                plan,
                status="queued",
                prompt=prompt,
                resumed_from=resumed_from,
                hydrate_result=hydrate_result,
            )
            saved_prompt = self.store.prompt(qualification, run["runId"])
            try:
                job = self.jobs.start(
                    kind="codex-maintenance",
                    key=qualification_operation_key(qualification),
                    worker=lambda emit: self._run_in_turn_group(
                        qualification,
                        lambda: self._run_with_technical_log(
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
                    ),
                )
            except JobConflictError:
                self.store.update(
                    qualification,
                    run["runId"],
                    hydrate_result=hydrate_result,
                    status="failed",
                    error="この資格で別の整備処理が実行中です。",
                )
                raise
            run = self.store.update(
                qualification,
                run["runId"],
                hydrate_result=hydrate_result,
                jobId=job["jobId"],
            )
            return {"run": run, "prompt": None, "job": job}

        run = self.store.create(
            plan,
            status="queued",
            resumed_from=resumed_from,
            hydrate_result=hydrate_result,
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
                hydrate_result=hydrate_result,
                status="failed",
                error="この資格で別の出力処理が実行中です。",
            )
            raise
        run = self.store.update(
            qualification,
            run["runId"],
            hydrate_result=hydrate_result,
            jobId=job["jobId"],
        )
        return {"run": run, "prompt": None, "job": job}

    def recent(self, qualification: str) -> dict[str, Any]:
        dashboard_runs = getattr(self.store, "dashboard_runs", None)
        if callable(dashboard_runs):
            recent_runs = dashboard_runs(
                qualification,
                limit=8,
                excluded_work_types={"evaluation", "reevaluation"},
                excluded_schema_versions={"failed-delta-reconciliation/v1"},
            )
        else:
            recent_runs = self.store.list(
                qualification,
                limit=8,
                top_level_only=True,
                newest_updated_first=True,
                summary_only=True,
                excluded_work_types={"evaluation", "reevaluation"},
                excluded_schema_versions={"failed-delta-reconciliation/v1"},
            )
        runs = [dict(run) for run in recent_runs]
        return {
            "qualification": qualification,
            "runs": runs,
            "activeRun": next(
                (run for run in runs if run.get("status") in LIVE_RUN_STATUSES),
                None,
            ),
        }

    def progress(
        self,
        qualification: str,
        run_id: str,
        *,
        include_questions: bool = True,
    ) -> dict[str, Any]:
        compact_run = self.store.get_compact(qualification, run_id)
        if str(compact_run.get("qualification") or "") != qualification:
            raise QualificationRunError("対象資格と作業履歴が一致しません。")
        if compact_run.get("workType") == "maintenance_flow":
            return self.store.combined_progress(
                qualification,
                run_id,
                include_questions=include_questions,
            )
        run = (
            self.store.get(qualification, run_id)
            if include_questions
            else compact_run
        )
        return self.store.progress(qualification, run_id)

    def technical_log(self, qualification: str, run_id: str) -> dict[str, Any]:
        run = self.store.get(qualification, run_id)
        if str(run.get("qualification") or "") != qualification:
            raise QualificationRunError("対象資格と作業履歴が一致しません。")
        return self.store.technical_log(qualification, run_id)

    def question_run_detail(
        self,
        qualification: str,
        run_id: str,
        question_id: str,
    ) -> dict[str, Any]:
        run = self.store.get_compact(qualification, run_id)
        if str(run.get("qualification") or "") != qualification:
            raise QualificationRunError("対象資格と作業履歴が一致しません。")
        return self.store.question_detail(
            qualification,
            run_id,
            question_id,
        )

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
            selected_stages = set(
                evaluation_rework_stage_codes(snapshot)
                if isinstance(snapshot, Mapping)
                else []
            )
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
            "speedMode": STANDARD_SPEED_MODE,
            "requestedServiceTier": None,
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
                key=qualification_operation_key(qualification),
                worker=lambda emit: self._run_in_turn_group(
                    qualification,
                    lambda: self._run_with_technical_log(
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
        evaluation_rework_stages = (
            evaluation_rework_stage_codes(evaluation_snapshot)
            if isinstance(evaluation_snapshot, Mapping)
            else []
        )
        patch_dirs.update(
            set().union(
                *(
                    REWORK_STAGE_PATCH_DIR_NAMES.get(stage_code, set())
                    for stage_code in evaluation_rework_stages
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
        phase_update_target_ids = [
            str(value)
            for value in parent.get("selectedUpdateTargetIds") or []
            if any(str(value).startswith(f"{stage_id}.") for stage_id in stage_ids)
        ]
        if phase_update_target_ids:
            scope["update_target_ids"] = phase_update_target_ids
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
                    "speedMode": normalize_speed_mode(
                        parent.get("speedMode") or STANDARD_SPEED_MODE
                    ),
                    "requestedServiceTier": parent.get(
                        "requestedServiceTier"
                    ),
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
        prompt_from_plan = getattr(
            self.workflow,
            "prompt_from_plan",
            None,
        )
        if callable(prompt_from_plan):
            prompt = prompt_from_plan(plan)["prompt"]
        elif len(stage_ids) > 1:
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
        parent = self.store.get_compact(qualification, run_id)
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
        parent: Mapping[str, Any],
        question_id: str,
        stage_id: str,
        *,
        question_index: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        question = (
            question_index.get(question_id)
            if question_index is not None
            else next(
                (
                    value
                    for value in parent.get("questionExecutions") or []
                    if isinstance(value, Mapping)
                    and str(value.get("questionId") or "") == question_id
                ),
                None,
            )
        )
        if not isinstance(question, Mapping):
            return None
        return next(
            (
                dict(stage)
                for stage in question.get("stages") or []
                if isinstance(stage, Mapping)
                and str(stage.get("stageId") or "") == stage_id
            ),
            None,
        )

    @staticmethod
    def _question_execution_index(
        parent: Mapping[str, Any],
    ) -> dict[str, Mapping[str, Any]]:
        return {
            str(question.get("questionId") or ""): question
            for question in parent.get("questionExecutions") or []
            if isinstance(question, Mapping)
            and str(question.get("questionId") or "")
        }

    def _refresh_queued_stage_inputs(
        self,
        qualification: str,
        run_id: str,
        phase_plan: Mapping[str, Any],
        targets: list[dict[str, Any]],
        stage_id: str,
        *,
        parent: Mapping[str, Any] | None = None,
        parent_question_index: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        policy_fingerprint = str(
            (phase_plan.get("policyFingerprints") or {}).get(stage_id) or ""
        )
        parent_snapshot = parent or self.store.get(qualification, run_id)

        def persist_stage(
            question_id: str,
            **changes: Any,
        ) -> dict[str, Any] | None:
            self.store.update_question_stage(
                qualification,
                run_id,
                question_id,
                stage_id,
                refresh_derived=False,
                hydrate_result=False,
                **changes,
            )
            detail = self.store.question_detail(
                qualification,
                run_id,
                question_id,
            )
            return self._queue_stage(
                {"questionExecutions": [detail["execution"]]},
                question_id,
                stage_id,
            )

        refreshed_stage: dict[str, Any] | None = None
        for target in targets:
            question_id = str(
                target.get("id") or target.get("uiQuestionId") or ""
            )
            current = self._queue_stage(
                parent_snapshot,
                question_id,
                stage_id,
                question_index=parent_question_index,
            )
            expected_key = work_item_key(target, stage_id)
            if current is None or str(current.get("workItemKey") or "") != expected_key:
                raise QuestionItemError(
                    f"工程開始時の一問queue識別子が一致しません: "
                    f"{question_id} / {stage_id}"
                )
            expected_input = input_fingerprint(
                target,
                stage_id,
                policy_fingerprint,
                phase_plan.get("selectedUpdateTargetIds") or [],
                current.get("priorValidationFeedback") or [],
            )
            if str(current.get("inputFingerprint") or "") == expected_input:
                if current.get("policyFingerprint") != policy_fingerprint:
                    refreshed_stage = persist_stage(
                        question_id,
                        policyFingerprint=policy_fingerprint,
                    )
                else:
                    refreshed_stage = current
                continue
            if str(current.get("status") or "") != "queued":
                reason = (
                    "工程開始時に入力又は方針が変更されたため、"
                    "この問題だけを再実行してください。"
                )
                refreshed_stage = persist_stage(
                    question_id,
                    status="blocked",
                    error=reason,
                    finishedAt=_now(),
                    block_dependents=True,
                )
                continue
            refreshed_stage = persist_stage(
                question_id,
                inputFingerprint=expected_input,
                policyFingerprint=policy_fingerprint,
                preparationPath=None,
                preparationHash=None,
                error=None,
            )
        return refreshed_stage

    def _phase_plan_policy_is_current(
        self,
        qualification: str,
        phase_plan: Mapping[str, Any],
        stage_id: str,
    ) -> bool:
        planned_versions = phase_plan.get("policyVersions") or {}
        if stage_id not in planned_versions:
            return True
        current = self.workflow.versioned_policies(qualification).get(stage_id)
        if not isinstance(current, Mapping):
            return False
        return bool(
            normalize_policy_version(planned_versions[stage_id])
            == normalize_policy_version(current.get("policyVersion"))
            and str(
                (phase_plan.get("policyFingerprints") or {}).get(stage_id) or ""
            )
            == str(current.get("policyFingerprint") or "")
        )

    def _requeue_policy_changed_question(
        self,
        qualification: str,
        run_id: str,
        question_id: str,
        stage_id: str,
        emit: Callable[[str], None],
        *,
        superseded_child_run_id: str | None = None,
        validation_attempts: list[dict[str, Any]] | None = None,
    ) -> bool:
        detail = self.store.question_detail(
            qualification,
            run_id,
            question_id,
        )
        current = self._queue_stage(
            {"questionExecutions": [detail["execution"]]},
            question_id,
            stage_id,
        )
        if current is None:
            return False
        refresh_count = int(current.get("policyRefreshCount") or 0)
        if refresh_count >= MAX_POLICY_REFRESH_ATTEMPTS:
            reason = (
                "共通方針が実行中に連続更新されたため、この問題だけを保留しました。"
                "更新が落ち着いてから再開してください。"
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
            emit(f"{question_id}: {reason} 他の問題は続行します。")
            return False
        refreshed_at = _now()
        refresh_history = [
            dict(value)
            for value in current.get("policyRefreshes") or []
            if isinstance(value, Mapping)
        ]
        refresh_history.append(
            {
                "at": refreshed_at,
                "reason": "canonical_policy_changed",
                "supersededChildRunId": superseded_child_run_id,
            }
        )
        self.store.update_question_stage(
            qualification,
            run_id,
            question_id,
            stage_id,
            status="queued",
            policyRefreshCount=refresh_count + 1,
            policyRefreshes=refresh_history,
            preparationPath=None,
            preparationHash=None,
            projectedInputPath=None,
            projectedInputHash=None,
            validationAttempts=copy.deepcopy(
                validation_attempts
                if validation_attempts is not None
                else current.get("validationAttempts") or []
            ),
            error=None,
            pauseReason=None,
            finishedAt=None,
        )
        emit(
            f"{question_id}: 共通方針の更新を検知したため、"
            "古い準備を破棄してこの問題だけを自動再準備します。"
        )
        return True

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

    def _write_projected_question_input(
        self,
        qualification: str,
        run_id: str,
        target: Mapping[str, Any],
        work_key: str,
        stage_id: str,
    ) -> dict[str, Any]:
        inventory = getattr(self.workflow, "inventory", None)
        project = getattr(inventory, "projected_input_for_stage", None)
        project_args = (
            qualification,
            str(target.get("listGroupId") or ""),
            SourceIdentityBinding.from_mapping(target).source_record_ref,
        )
        if callable(project):
            result = project(*project_args, stage_id)
        else:
            project = getattr(inventory, "projected_input", None)
            if not callable(project):
                raise QuestionItemError(
                    "一問工程に必要なlogicalProjection機能がありません。"
                )
            result = project(*project_args)
        identity = SourceIdentityBinding.from_mapping(target)
        errors = tuple(str(value) for value in getattr(result, "errors", ()) if value)
        if errors:
            raise QuestionItemError(
                "現在入力の論理projectionを作成できません: "
                + " / ".join(errors)
            )
        record = getattr(result, "record", None)
        if not isinstance(record, Mapping):
            raise QuestionItemError("現在入力の論理projection形式が不正です。")
        path = (
            self.store.root
            / qualification
            / run_id
            / "projected_inputs"
            / f"{work_key}.json"
        )
        payload = {
            "schemaVersion": "question-maintenance-projection/v1",
            "qualification": qualification,
            "listGroupId": str(target.get("listGroupId") or ""),
            **identity.as_mapping(),
            "question_bodies": [copy.deepcopy(dict(record))],
            "appliedPatchFiles": list(getattr(result, "applied_files", ())),
            "questionIssueCorrectionEvidence": copy.deepcopy(
                list(getattr(result, "question_issue_evidence", ()))
            ),
        }
        self.store._write_json(path, payload)
        source_record = None
        applied_files = tuple(getattr(result, "applied_files", ()))
        needs_source_record = stage_id in {"originalize", "correct_choice"} or (
            stage_id == "explanation"
            and any(
                "05_originalized" in Path(str(value)).parts
                for value in applied_files
            )
        )
        if needs_source_record:
            source_input = getattr(inventory, "source_input", None)
            if not callable(source_input):
                raise QuestionItemError(
                    "工程の比較に必要な00_source参照入力がありません。"
                )
            source_record = source_input(*project_args)
        return {
            "path": path.relative_to(self.repo_root).as_posix(),
            "hash": hashlib.sha256(path.read_bytes()).hexdigest(),
            "record": copy.deepcopy(dict(record)),
            "sourceRecord": source_record,
            "questionIssueCorrectionEvidence": tuple(
                copy.deepcopy(
                    list(getattr(result, "question_issue_evidence", ()))
                )
            ),
        }

    def _project_question_now(
        self,
        qualification: str,
        target: Mapping[str, Any],
        stage_id: str | None = None,
    ) -> Any:
        inventory = getattr(self.workflow, "inventory", None)
        identity = SourceIdentityBinding.from_mapping(target)
        project_args = (
            qualification,
            str(target.get("listGroupId") or ""),
            identity.source_record_ref,
        )
        project = (
            getattr(inventory, "projected_input_for_stage", None)
            if stage_id
            else None
        )
        if callable(project):
            result = project(*project_args, stage_id)
        else:
            project = getattr(inventory, "projected_input", None)
            if not callable(project):
                raise QuestionItemError(
                    "一問工程に必要なlogicalProjection機能がありません。"
                )
            result = project(*project_args)
        errors = tuple(str(value) for value in getattr(result, "errors", ()) if value)
        if errors:
            raise QuestionItemError(
                "現在入力の論理projectionを作成できません: "
                + " / ".join(errors)
            )
        if not isinstance(getattr(result, "record", None), Mapping):
            raise QuestionItemError("現在入力の論理projection形式が不正です。")
        return result

    @staticmethod
    def _canonical_question_target(
        parent: Mapping[str, Any],
        question_id: str,
    ) -> dict[str, Any]:
        target = next(
            (
                dict(value)
                for value in parent.get("progressTargets") or []
                if isinstance(value, Mapping)
                and str(value.get("id") or value.get("uiQuestionId") or "")
                == question_id
            ),
            None,
        )
        if target is None:
            raise QuestionItemError(
                f"一問queueの基準targetが見つかりません: {question_id}"
            )
        return target

    def _dynamic_question_phase_plan(
        self,
        qualification: str,
        parent: Mapping[str, Any],
        phase: Mapping[str, Any],
        initial_plan: Mapping[str, Any],
        question_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Re-evaluate one changed question without rebuilding its 57 siblings."""

        stage_id = str(phase["id"])
        target = self._canonical_question_target(parent, question_id)
        projection = self._project_question_now(
            qualification,
            target,
            stage_id,
        )
        projected = dict(projection.record)
        applicable = self._projection_stage_applicable(
            initial_plan,
            stage_id,
            projected,
        )

        identity = SourceIdentityBinding.from_mapping(target)
        aliases = sorted(
            {
                str(value)
                for value in [*(target.get("aliases") or []), *identity.as_tuple()]
                if value
            }
        )
        source_name = identity.source_record_ref.rsplit("#", 1)[0]
        source_path = (
            Path("output")
            / qualification
            / "questions_json"
            / str(target.get("listGroupId") or "")
            / "00_source"
            / source_name
        ).as_posix()
        target.update(
            aliases=aliases,
            stateHash=sha256_json(
                {
                    field: projected.get(field)
                    for field in PROJECTED_COMPARE_FIELDS
                }
            ),
        )
        patch_files = self._review_patch_files(
            {"paths": {"source": source_path, "patches": []}},
            {"investigationScope": "current_question"},
            set(STAGE_PATCH_DIR_NAMES.get(stage_id) or []),
            set(STAGE_REVIEW_FLAG_SUFFIXES.get(stage_id) or []),
        )
        plan = copy.deepcopy(dict(initial_plan))
        plan.update(
            targetCount=1,
            workItemCount=1,
            targetQuestionKeys=[question_id],
            progressTargets=[target],
            targetRecordBindings=[
                {
                    "uiQuestionId": question_id,
                    **identity.as_mapping(),
                    "aliases": aliases,
                }
            ],
            targetRecordAliasGroups=[aliases],
            targetSourceRecordScopes={source_path: [aliases]},
            targetGroupIds=[str(target.get("listGroupId") or "")],
            sourceFiles=[source_path],
            outputFiles=sorted(patch_files),
            policyTargets={stage_id: [question_id]},
        )
        self._apply_plan_write_contract(plan)
        plan["resolvableFailedDeltaPaths"] = self._resolvable_for_plan(
            qualification,
            list(plan.get("targetGroupIds") or []),
            plan,
        )
        # A non-applicable stage still needs a one-question identity scope so
        # its work-version receipt can be recorded mechanically.  Returning
        # the scoped plan with no writer target prevents the same stage from
        # being reopened or reported as blocked on every later run.
        return plan, target if applicable else None

    @staticmethod
    def _projection_stage_applicable(
        phase_plan: Mapping[str, Any],
        stage_id: str,
        projected: Mapping[str, Any],
    ) -> bool:
        del phase_plan
        # Other question stages apply to every question once an actual upstream
        # patch changed.  Law audit alone has a record-level applicability gate.
        return not (
            stage_id == "law_audit"
            and projected.get("isLawRelated") is False
        )

    def _validated_queue_stage_changed(
        self,
        qualification: str,
        stage: Mapping[str, Any],
        child_run_cache: dict[tuple[str, str], Mapping[str, Any]] | None = None,
    ) -> bool:
        child_ids = [str(value) for value in stage.get("childRunIds") or [] if value]
        if not child_ids:
            return False
        for child_id in child_ids:
            cache_key = (qualification, child_id)
            child = (
                child_run_cache.get(cache_key)
                if child_run_cache is not None
                else None
            )
            if child is None:
                try:
                    child = self.store.get(qualification, child_id)
                except (FileNotFoundError, ValueError):
                    continue
                if child_run_cache is not None:
                    child_run_cache[cache_key] = child
            result = child.get("result")
            changed_files = (
                result.get("changedFiles")
                if isinstance(result, Mapping)
                else None
            )
            if (
                child.get("status") == "succeeded"
                and child.get("receiptValidated") is True
                and child.get("deltaUnknown") is not True
                and isinstance(child.get("workVersionReceipt"), Mapping)
                and isinstance(result, Mapping)
                and result.get("status") == "succeeded"
                and isinstance(changed_files, list)
                and any(
                    isinstance(value, str) and value.strip()
                    for value in changed_files
                )
            ):
                return True
        return False

    def _question_stage_spec(
        self,
        qualification: str,
        run_id: str,
        phase: Mapping[str, Any],
        question_id: str,
        initial_plan: Mapping[str, Any],
        initial_prompt: str,
        child_run_cache: dict[tuple[str, str], Mapping[str, Any]] | None = None,
        *,
        parent: Mapping[str, Any] | None = None,
        phase_plan_index: QuestionPlanIndex | None = None,
        parent_question_index: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        parent = parent or self.store.get(qualification, run_id)
        stage_id = str(phase["id"])
        current = self._queue_stage(
            parent,
            question_id,
            stage_id,
            question_index=parent_question_index,
        )
        if current is None:
            return {"status": "not_present", "stageId": stage_id}
        current_status = str(current.get("status") or "queued")
        if current_status in {"validated", "not_applicable", "blocked"}:
            return {"status": current_status, "stageId": stage_id}

        phase_plan = dict(initial_plan)
        phase_prompt = initial_prompt
        scoped_plan_index = phase_plan_index

        def matching_target(plan: Mapping[str, Any]) -> dict[str, Any] | None:
            if scoped_plan_index is not None:
                indexed = scoped_plan_index.targets_by_id.get(question_id)
                return dict(indexed) if indexed is not None else None
            return next(
                (
                    dict(value)
                    for value in plan.get("progressTargets") or []
                    if isinstance(value, Mapping)
                    and str(value.get("id") or value.get("uiQuestionId") or "")
                    == question_id
                ),
                None,
            )

        target = matching_target(phase_plan)
        question = (
            parent_question_index.get(question_id)
            if parent_question_index is not None
            else next(
                (
                    value
                    for value in parent.get("questionExecutions") or []
                    if isinstance(value, Mapping)
                    and str(value.get("questionId") or "") == question_id
                ),
                None,
            )
        )
        prior_validated = False
        prior_changed = False
        for prior in (question or {}).get("stages") or []:
            if not isinstance(prior, Mapping):
                continue
            if str(prior.get("stageId") or "") == stage_id:
                break
            if str(prior.get("status") or "") == "validated":
                prior_validated = True
                prior_changed = prior_changed or self._validated_queue_stage_changed(
                    qualification,
                    prior,
                    child_run_cache,
                )
        if stage_id == "law_audit" or (
            prior_validated and (target is not None or prior_changed)
        ):
            phase_plan, target = self._dynamic_question_phase_plan(
                qualification,
                parent,
                phase,
                phase_plan,
                question_id,
            )
            scoped_plan_index = None
            if not phase_prompt:
                phase_prompt = self.store.prompt(qualification, run_id)
        if target is None:
            if current_status != "queued":
                raise QuestionItemError(
                    "一問工程の対象判定中に未確定状態が残っています: "
                    f"{question_id} / {stage_id} / {current_status}"
                )
            work_version_receipt: dict[str, Any] | None = None
            if stage_id == "law_audit":
                try:
                    no_op_plan = specialize_question_plan(
                        phase_plan,
                        question_id,
                    )
                    no_op_plan.update(
                        runId=run_id,
                        stageId=stage_id,
                        stageIds=[stage_id],
                        parallelStrategy="question_turn",
                    )
                    work_version_receipt = self._record_work_versions(
                        no_op_plan
                    )
                except (
                    QualificationRunError,
                    QuestionWorkQueueError,
                    ValueError,
                ) as exc:
                    target = matching_target(phase_plan)
                    if target is None:
                        raise QuestionItemError(
                            "対象外判定の根拠又は作業版を記録できず、"
                            "通常writerの対象も解決できません: "
                            f"{exc}"
                        ) from exc
            if target is None:
                self.store.update_question_stage(
                    qualification,
                    run_id,
                    question_id,
                    stage_id,
                    validated_receipt=work_version_receipt,
                    refresh_derived=False,
                    hydrate_result=False,
                    status="not_applicable",
                    error=None,
                    finishedAt=_now(),
                )
                result = {
                    "status": "not_applicable",
                    "stageId": stage_id,
                    "listGroupId": str(
                        (question or {}).get("listGroupId") or ""
                    ),
                }
                if work_version_receipt is not None:
                    result["workVersionReceipt"] = work_version_receipt
                return result

        current = self._refresh_queued_stage_inputs(
            qualification,
            run_id,
            phase_plan,
            [target],
            stage_id,
            parent=parent,
            parent_question_index=parent_question_index,
        )
        if current is None:
            raise QuestionItemError(
                f"一問queueが見つかりません: {question_id} / {stage_id}"
            )
        current_status = str(current.get("status") or "queued")
        if current_status == "blocked":
            return {"status": "blocked", "stageId": stage_id}
        if current_status not in {"queued", "prepared"}:
            raise QuestionItemError(
                "一問工程を準備できない状態です: "
                f"{question_id} / {stage_id} / {current_status}"
            )
        try:
            scoped_plan = specialize_question_plan(
                phase_plan,
                question_id,
                index=scoped_plan_index,
            )
        except QuestionWorkQueueError as exc:
            raise QuestionItemError(str(exc)) from exc
        return {
            "status": current_status,
            "stageId": stage_id,
            "phase": dict(phase),
            "phasePlan": phase_plan,
            "phasePrompt": phase_prompt,
            "target": target,
            "scopedPlan": scoped_plan,
            "queueStage": current,
        }

    def _question_plan_for_spec(
        self,
        spec: Mapping[str, Any],
        *,
        parent_run_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        target = copy.deepcopy(dict(spec["target"]))
        question_plan = copy.deepcopy(dict(spec["scopedPlan"]))
        question_plan["progressTargets"] = [target]
        list_fields = {
            "targetGroupIds",
            "targetQuestionKeys",
            "progressTargets",
            "targetRecordBindings",
            "targetRecordAliasGroups",
            "targetRecordAliases",
            "sourceFiles",
            "outputFiles",
            "allowedPatchFiles",
            "allowedWriteFiles",
            "resolvableFailedDeltaPaths",
        }
        for field in list_fields:
            values = copy.deepcopy(list(question_plan.get(field) or []))
            if field in {"progressTargets", "targetRecordBindings", "targetRecordAliasGroups"}:
                question_plan[field] = values
            else:
                question_plan[field] = list(dict.fromkeys(values))

        for field in ("targetSourceRecordScopes", "targetRecordScopes"):
            merged: dict[str, list[list[str]]] = {}
            for path, groups in (question_plan.get(field) or {}).items():
                bucket = merged.setdefault(str(path), [])
                for group in groups or []:
                    normalized = sorted({str(value) for value in group if value})
                    if normalized and normalized not in bucket:
                        bucket.append(normalized)
            question_plan[field] = merged

        stage_id = str(spec["stageId"])
        question_id = str(target.get("id") or target.get("uiQuestionId") or "")
        if not question_id:
            raise QualificationRunError("一問planの問題IDが空です。")
        question_plan.update(
            targetCount=1,
            workItemCount=1,
            stageId=stage_id,
            stageIds=[stage_id],
            policyTargets={stage_id: [question_id]},
            parentRunId=parent_run_id,
            flowPhaseId=stage_id,
            workType=f"maintenance_{stage_id}_candidate",
            sandbox="read-only",
            provider=self.app_server.provider,
            parallelStrategy="question_turn",
            parallelWorkerLimit=1,
            writeWorkerLimit=1,
            parentSourceChecked=True,
            modelBatchSize=1,
        )
        question_plan.pop("stagePlans", None)
        self._apply_plan_write_contract(question_plan)
        return question_plan, target

    @staticmethod
    def _question_feedback(
        child: Mapping[str, Any],
        result: QuestionValidationResult,
        *,
        attempt: int,
        stage_id: str,
    ) -> dict[str, Any]:
        pseudo_child = {
            **dict(child),
            "status": "failed",
            "error": result.summary,
            "result": {
                "status": "failed",
                "summary": result.summary,
                "commands": list(result.commands),
                "changedFiles": [],
            },
            "rollback": {
                "status": "succeeded",
                "remainingChangedFiles": [],
                "deltaUnknown": False,
            },
            "deltaUnknown": False,
            "writeAttributionVerified": True,
            "unsafeChangedFiles": [],
            "unsafeNotifiedChangedFiles": [],
        }
        return build_child_feedback(
            pseudo_child,
            attempt=attempt,
            question_id=result.question_id,
            stage_id=stage_id,
        )

    def _run_shared_prerequisite(
        self,
        qualification: str,
        run_id: str,
        phase: Mapping[str, Any],
        emit: Callable[[str], None],
        *,
        child_run_ids: list[str],
        work_version_receipts: list[dict[str, Any]],
        confirmed_group_ids: set[str],
    ) -> bool:
        phase_id = str(phase["id"])
        parent = self.store.get(qualification, run_id)
        phase_plan, phase_prompt = self._flow_phase_plan_prompt(parent, phase)
        target_count = int(phase_plan.get("targetCount") or 0)
        if not target_count:
            self._update_flow_phase(
                qualification,
                run_id,
                phase_id,
                status="skipped",
                targetCount=0,
                notApplicableCount=0,
                artifactSync={"status": "not_required", "groups": []},
                finishedAt=_now(),
                error=None,
            )
            return True
        self.store.update(
            qualification,
            run_id,
            currentPhaseId=phase_id,
            executionPhase=f"committing:{phase_id}",
        )
        self._update_flow_phase(
            qualification,
            run_id,
            phase_id,
            status="running",
            targetCount=target_count,
            childRunIds=[],
            startedAt=_now(),
            error=None,
        )
        child = self.store.create(phase_plan, status="queued", prompt=phase_prompt)
        child_id = str(child["runId"])
        child_run_ids.append(child_id)
        self.store.update(
            qualification,
            run_id,
            childRunIds=list(child_run_ids),
        )
        try:
            self._run_human(
                qualification,
                child_id,
                self.store.prompt(qualification, child_id),
                str(phase_plan["workType"]),
                emit,
                sync_artifacts=False,
            )
            child = self.store.refresh(qualification, child_id)
            if child.get("status") != "succeeded" or not child.get(
                "receiptValidated"
            ):
                raise QualificationRunError(
                    f"{phase['label']}の完了結果を検証できませんでした。"
                )
        except Exception as exc:  # noqa: BLE001
            try:
                child = self.store.refresh(qualification, child_id)
            except Exception:  # noqa: BLE001
                child = self.store.get(qualification, child_id)
            reason = f"{phase['label']}で停止: {exc}"
            self._update_flow_phase(
                qualification,
                run_id,
                phase_id,
                status="failed",
                childRunIds=[child_id],
                finishedAt=_now(),
                error=reason,
            )
            if not self._isolated_child_failure(child):
                unsafe_reason = (
                    f"{phase['label']}: 失敗後のrollback完了を検証できないため、"
                    "後続処理と成果物同期を停止しました。"
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
            provider_failure = _external_provider_failure(exc)
            feedback = build_child_feedback(
                child,
                attempt=1,
                question_id=f"scope:{phase_id}",
                stage_id=phase_id,
            )
            if provider_failure is not None or feedback.get("status") == "blocked":
                pause_kind = (
                    "external_provider"
                    if provider_failure is not None
                    else "safety_violation"
                )
                pause_reason = (
                    "Codex App Serverの利用可否を回復後に再開してください: "
                    f"{provider_failure}"
                    if provider_failure is not None
                    else f"{phase['label']}の安全性違反を解消後に再開してください。"
                )
                self._block_remaining_queue(
                    qualification,
                    run_id,
                    pause_reason,
                )
                pause = QuestionQueuePaused(
                    pause_reason,
                    pause_kind=pause_kind,
                )
                self._persist_queue_pause(qualification, run_id, pause)
                raise pause from exc
            self._block_remaining_queue(qualification, run_id, reason)
            emit(f"{reason} 依存する後続だけを保留します。")
            return False
        receipt = child.get("workVersionReceipt")
        if isinstance(receipt, Mapping):
            receipt_copy = dict(receipt)
            work_version_receipts.append(receipt_copy)
            shared_receipts = [
                dict(value)
                for value in parent.get("sharedWorkVersionReceipts") or []
                if isinstance(value, Mapping)
            ]
            encoded_shared = {
                json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                for value in shared_receipts
            }
            encoded_receipt = json.dumps(
                receipt_copy,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if encoded_receipt not in encoded_shared:
                shared_receipts.append(receipt_copy)
            existing_receipt = parent.get("workVersionReceipt")
            aggregate_items = [
                dict(value)
                for value in (
                    existing_receipt.get("items") or []
                    if isinstance(existing_receipt, Mapping)
                    else []
                )
                if isinstance(value, Mapping)
            ]
            aggregate_items.append(receipt_copy)
            unique_items: list[dict[str, Any]] = []
            seen_items: set[str] = set()
            for value in aggregate_items:
                encoded = json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if encoded in seen_items:
                    continue
                seen_items.add(encoded)
                unique_items.append(value)
            self.store.update(
                qualification,
                run_id,
                sharedWorkVersionReceipts=shared_receipts,
                workVersionReceipt={
                    "recordedCount": sum(
                        int(value.get("recordedCount") or 0)
                        for value in unique_items
                    ),
                    "items": unique_items,
                },
            )
            if int(receipt.get("recordedCount") or 0):
                confirmed_group_ids.update(
                    str(value) for value in child.get("targetGroupIds") or [] if value
                )
        self._update_flow_phase(
            qualification,
            run_id,
            phase_id,
            status="succeeded",
            childRunIds=[child_id],
            threadId=child.get("threadId"),
            sessionId=child.get("sessionId"),
            turnId=child.get("turnId"),
            model=child.get("model"),
            serviceTier=child.get("serviceTier"),
            reasoningEffort=child.get("reasoningEffort"),
            receiptValidated=True,
            workVersionReceipt=receipt,
            artifactSync={"status": "deferred", "groups": []},
            finishedAt=_now(),
            error=None,
        )
        return True

    def _finalize_question_phases(
        self,
        qualification: str,
        run_id: str,
        phases: list[dict[str, Any]],
        phase_child_ids: Mapping[str, list[str]],
        phase_runtime: Mapping[str, Mapping[str, Any]],
    ) -> None:
        parent = self.store.get(qualification, run_id)
        for phase in phases:
            stage_id = str(phase["id"])
            if stage_id in {"setup", "category_setup"}:
                continue
            completion = _question_phase_completion(
                parent.get("questionExecutions") or [],
                stage_id,
            )
            self._update_flow_phase(
                qualification,
                run_id,
                stage_id,
                **completion,
                childRunIds=list(phase_child_ids.get(stage_id, [])),
                **dict(phase_runtime.get(stage_id, {})),
                finishedAt=_now(),
            )


    def _run_question_queue(
        self,
        qualification: str,
        run_id: str,
        phases: list[dict[str, Any]],
        emit: Callable[[str], None],
    ) -> dict[str, Any]:
        """Process normal work first and retry rejected questions at the end."""

        child_run_ids: list[str] = []
        work_version_receipts: list[dict[str, Any]] = []
        parent = self.store.get(qualification, run_id)
        confirmed_group_ids: set[str] = {
            str(value) for value in parent.get("confirmedGroupIds") or [] if value
        }
        phase_child_ids: dict[str, list[str]] = {}
        phase_runtime: dict[str, dict[str, Any]] = {}
        pipeline_stop = threading.Event()
        aggregation_lock = threading.Lock()
        validated_child_run_cache: dict[
            tuple[str, str],
            Mapping[str, Any],
        ] = {}
        target_resolution_cache = TargetResolutionCache()
        last_preparation_heartbeat = 0.0
        question_concurrency = (
            normalize_question_concurrency(parent["questionConcurrency"])
            if parent.get("questionConcurrency") is not None
            else DEFAULT_QUESTION_CONCURRENCY
        )
        scheduler_limits = AdaptiveLimits.initial(
            pending_batches=question_concurrency,
            max_parallel_turns=question_concurrency,
        )
        pipeline_telemetry = _PipelineRuntimeTelemetry(
            model_capacity=question_concurrency,
            patch_tool_capacity=question_concurrency,
        )
        provider_attempt_limit = MAX_PROVIDER_ATTEMPTS
        configured_provider_attempts = getattr(
            self.app_server,
            "provider_retry_attempts",
            None,
        )
        if isinstance(configured_provider_attempts, int) and not isinstance(
            configured_provider_attempts,
            bool,
        ):
            provider_attempt_limit = max(
                MAX_PROVIDER_ATTEMPTS,
                min(configured_provider_attempts, 10),
            )
        emit(
            "一問を一つのmodel turnへ分離し、"
            f"最大{question_concurrency}問を同時に整備します。"
            "入力生成、機械検証、patch反映も一問単位です。"
        )

        def preparation_heartbeat(
            stage_id: str,
            *,
            round_number: int,
            prepared_count: int,
            target_count: int,
            prepared_spec_count: int,
            batch_count: int,
            model_started: bool,
            status: str,
            force: bool = False,
        ) -> None:
            nonlocal last_preparation_heartbeat
            current_monotonic = time.monotonic()
            if (
                not force
                and current_monotonic - last_preparation_heartbeat
                < PREPARATION_HEARTBEAT_SECONDS
            ):
                return
            heartbeat_at = _now()
            changes: dict[str, Any] = {
                "heartbeatAt": heartbeat_at,
                "preparationProgress": {
                    "stageId": stage_id,
                    "round": round_number,
                    "status": status,
                    "preparedCount": prepared_count,
                    "preparedSpecCount": prepared_spec_count,
                    "targetCount": target_count,
                    "batchCount": batch_count,
                    "modelStarted": model_started,
                    "workerLimit": min(
                        question_concurrency,
                        PREPARATION_MAX_PARALLEL_QUESTIONS,
                        max(target_count, 1),
                    ),
                    "updatedAt": heartbeat_at,
                },
            }
            if not model_started:
                changes["executionPhase"] = f"preparing:{stage_id}"
            self.store.update(
                qualification,
                run_id,
                hydrate_result=False,
                **changes,
            )
            heartbeat = getattr(emit, "heartbeat", None)
            if callable(heartbeat):
                heartbeat()
            last_preparation_heartbeat = current_monotonic

        def prepare_spec(
            phase: Mapping[str, Any],
            phase_plan: Mapping[str, Any],
            phase_plan_index: QuestionPlanIndex,
            phase_prompt: str,
            question_id: str,
            parent: Mapping[str, Any],
            parent_question_index: Mapping[str, Mapping[str, Any]],
        ) -> dict[str, Any] | None:
            stage_id = str(phase["id"])
            try:
                spec = self._question_stage_spec(
                    qualification,
                    run_id,
                    phase,
                    question_id,
                    phase_plan,
                    phase_prompt,
                    validated_child_run_cache,
                    parent=parent,
                    phase_plan_index=phase_plan_index,
                    parent_question_index=parent_question_index,
                )
            except QuestionItemError as exc:
                self.store.update_question_stage(
                    qualification,
                    run_id,
                    question_id,
                    str(phase["id"]),
                    refresh_derived=False,
                    hydrate_result=False,
                    status="blocked",
                    error=str(exc),
                    finishedAt=_now(),
                    block_dependents=True,
                )
                emit(f"{question_id}: この問題だけを保留しました: {exc}")
                return None
            if str(spec.get("status") or "") not in {"queued", "prepared"}:
                work_version_receipt = spec.get("workVersionReceipt")
                if isinstance(work_version_receipt, Mapping):
                    with aggregation_lock:
                        work_version_receipts.append(
                            dict(work_version_receipt)
                        )
                        list_group_id = str(
                            spec.get("listGroupId") or ""
                        )
                        if list_group_id:
                            confirmed_group_ids.add(list_group_id)
                return None
            target = dict(spec["target"])
            stage_id = str(spec["stageId"])
            work_key = str((spec.get("queueStage") or {}).get("workItemKey") or "")
            try:
                projection = self._write_projected_question_input(
                    qualification,
                    run_id,
                    target,
                    work_key,
                    stage_id,
                )
            except Exception as exc:  # noqa: BLE001
                self.store.update_question_stage(
                    qualification,
                    run_id,
                    question_id,
                    stage_id,
                    refresh_derived=False,
                    hydrate_result=False,
                    status="blocked",
                    error=str(exc),
                    finishedAt=_now(),
                    block_dependents=True,
                )
                return None
            if projection is not None:
                target["_projectedInputPath"] = projection["path"]
            try:
                scoped_plan = dict(spec["scopedPlan"])
                prepared_targets = candidate_targets(
                    question_id,
                    stage_id,
                    scoped_plan,
                )
                binding = SourceIdentityBinding.from_mapping(target)
                scopes = scoped_plan.get("targetRecordScopes") or {}
                for candidate_target in prepared_targets:
                    aliases = {
                        str(alias)
                        for group in scopes.get(candidate_target.path, [])
                        for alias in group
                        if alias
                    }
                    assert_target_resolvable(
                        self.repo_root,
                        candidate_target.path,
                        binding=binding,
                        aliases=aliases,
                        cache=target_resolution_cache,
                    )
            except Exception as exc:  # noqa: BLE001
                self.store.update_question_stage(
                    qualification,
                    run_id,
                    question_id,
                    stage_id,
                    refresh_derived=False,
                    hydrate_result=False,
                    status="blocked",
                    error=str(exc),
                    finishedAt=_now(),
                    block_dependents=True,
                )
                emit(f"{question_id}: 対象を一意に確定できないため保留しました: {exc}")
                return None
            return {
                **spec,
                "target": target,
                "scopedPlan": scoped_plan,
                "candidateRecord": projection["record"],
                "sourceRecord": projection.get("sourceRecord"),
                "questionIssueCorrectionEvidence": projection.get(
                    "questionIssueCorrectionEvidence"
                ),
                "candidateTargets": prepared_targets,
                "projectionUpdate": {
                    "questionId": question_id,
                    "stageId": stage_id,
                    "changes": {
                        "projectedInputPath": projection["path"],
                        "projectedInputHash": projection["hash"],
                    },
                },
            }

        def spec_requires_retry_model(
            spec: Mapping[str, Any],
            stage_id: str,
        ) -> bool:
            stage = spec.get("queueStage") or {}
            return bool(stage.get("retryModelRequired")) or any(
                isinstance(attempt, Mapping)
                and str(attempt.get("status") or "")
                in {"failed", "blocked", "interrupted"}
                for attempt in stage.get("validationAttempts") or []
            )

        def prepare_batch(
            specs: list[Mapping[str, Any]],
            phase: Mapping[str, Any],
            phase_prompt: str,
            parent_snapshot: Mapping[str, Any],
        ) -> dict[str, Any]:
            if len(specs) != 1:
                raise QualificationRunError(
                    "候補生成は一問一つのmodel turnで実行してください。"
                )
            single_spec = specs[0]
            stage_id = str(phase["id"])
            retry_flags = {
                spec_requires_retry_model(spec, stage_id) for spec in specs
            }
            if len(retry_flags) != 1:
                raise QualificationRunError(
                    "初回問題と再試行問題を同じmodel turnへ混在できません。"
                )
            retrying = retry_flags.pop()
            catalog_loader = getattr(self.workflow, "catalog", None)
            catalog = catalog_loader(qualification) if callable(catalog_loader) else {}
            definition = next(
                (
                    value for value in catalog.get("stages") or []
                    if isinstance(value, Mapping) and str(value.get("id") or "") == stage_id
                ),
                {},
            )
            agent_policy = dict(definition.get("agentPolicy") or {})
            candidate_role = "candidate_retry" if retrying else "candidate_initial"
            candidate_policy = dict(agent_policy.get(candidate_role) or {})
            requested_model = str(
                candidate_policy.get("model")
                or (QUESTION_MAINTENANCE_RETRY_MODEL if retrying else QUESTION_MAINTENANCE_MODEL)
            )
            requested_effort = str(
                candidate_policy.get("reasoningEffort") or TURN_REASONING_EFFORT
            )
            batch_plan, target = self._question_plan_for_spec(
                single_spec,
                parent_run_id=run_id,
            )
            targets = [target]
            batch_plan.update(
                requestedModel=requested_model,
                requestedReasoningEffort=requested_effort,
                retryModelFallback=retrying,
                agentPolicy=agent_policy,
                speedMode=normalize_speed_mode(
                    parent.get("speedMode") or STANDARD_SPEED_MODE
                ),
                requestedServiceTier=parent.get("requestedServiceTier"),
            )
            batch_question_ids = [
                str(target.get("id") or target.get("uiQuestionId") or "")
                for target in targets
            ]
            prompt_from_plan = getattr(self.workflow, "prompt_from_plan", None)
            if callable(prompt_from_plan):
                batch_stage_prompt = prompt_from_plan(batch_plan)["prompt"]
            else:
                batch_stage_prompt = self.workflow.prompt(
                    qualification,
                    stage_id,
                    str(
                        batch_plan.get("mode")
                        or parent.get("mode")
                        or "remaining"
                    ),
                    list_group_ids=list(
                        batch_plan.get("scopeListGroupIds") or []
                    ),
                    update_target_ids=list(
                        batch_plan.get("selectedUpdateTargetIds") or []
                    ),
                    question_ids=batch_question_ids,
                )["prompt"]
            feedback_by_question: dict[str, list[Mapping[str, Any]]] = {}
            for target in targets:
                question_id = str(target.get("id") or target.get("uiQuestionId") or "")
                current = self._queue_stage(
                    parent_snapshot,
                    question_id,
                    stage_id,
                ) or {}
                prior_feedback = [
                    dict(value)
                    for value in current.get("priorValidationFeedback") or []
                    if isinstance(value, Mapping)
                ]
                current_feedback = [
                    dict(value["feedback"])
                    for value in current.get("validationAttempts") or []
                    if isinstance(value, Mapping)
                    and isinstance(value.get("feedback"), Mapping)
                ]
                feedback_by_question[question_id] = [
                    *prior_feedback,
                    *current_feedback,
                ]
            records_by_question = {
                str(spec["target"].get("id") or spec["target"].get("uiQuestionId") or ""):
                copy.deepcopy(dict(spec["candidateRecord"]))
                for spec in specs
            }
            originalization_source_by_question = {
                str(spec["target"].get("id") or spec["target"].get("uiQuestionId") or ""):
                copy.deepcopy(dict(spec["sourceRecord"]))
                for spec in specs
                if isinstance(spec.get("sourceRecord"), Mapping)
                and stage_id in {"originalize", "correct_choice", "explanation"}
            }
            source_answer_evidence_by_question = {
                question_id: evidence
                for spec in specs
                if isinstance(spec.get("sourceRecord"), Mapping)
                for question_id in [
                    str(
                        spec["target"].get("id")
                        or spec["target"].get("uiQuestionId")
                        or ""
                    )
                ]
                for evidence in [
                    _trusted_source_answer_evidence(
                        spec["sourceRecord"],
                        spec["target"],
                        spec["candidateRecord"],
                        spec.get("questionIssueCorrectionEvidence") or (),
                    )
                ]
                if stage_id == "correct_choice" and evidence is not None
            }
            candidate_targets_by_question = {
                str(spec["target"].get("id") or spec["target"].get("uiQuestionId") or ""):
                tuple(spec["candidateTargets"])
                for spec in specs
            }
            list_group_id_by_question = {
                str(spec["target"].get("id") or spec["target"].get("uiQuestionId") or ""):
                str(spec["target"].get("listGroupId") or "")
                for spec in specs
            }
            question_issue_evidence_by_question = {
                str(spec["target"].get("id") or spec["target"].get("uiQuestionId") or ""):
                tuple(
                    copy.deepcopy(
                        list(spec.get("questionIssueCorrectionEvidence") or ())
                    )
                )
                for spec in specs
                if spec.get("questionIssueCorrectionEvidence")
            }
            primary_law_evidence_by_question = (
                {
                    question_id: self.primary_law_evidence.resolve(
                        record,
                        current_as_of=str(
                            parent_snapshot.get("startedAt") or _now()
                        )[:10],
                        qualification=qualification,
                        list_group_id=list_group_id_by_question.get(
                            question_id,
                            "",
                        ),
                    )
                    for question_id, record in records_by_question.items()
                }
                if stage_id in {"law_context", "explanation", "law_audit"}
                else {}
            )
            original_aggregate_evidence = (
                _aggregate_downstream_source_evidence(
                    self.repo_root,
                    qualification,
                    batch_plan,
                    targets,
                    records_by_question,
                )
                if stage_id in {"correct_choice", "law_context", "explanation"}
                else {}
            )
            canonical_guidance = _canonical_document_guidance(
                self.repo_root,
                batch_plan.get("canonicalDocs") or [],
            )
            batch_prompt = _structured_candidate_prompt(
                batch_stage_prompt,
                targets,
                canonical_guidance=canonical_guidance,
                stage_id=stage_id,
                records_by_question=records_by_question,
                candidate_targets_by_question=candidate_targets_by_question,
                feedback_by_question=feedback_by_question,
                stage_context=_structured_candidate_stage_context(
                    self.repo_root,
                    qualification,
                    stage_id,
                ),
                original_aggregate_evidence_by_question=(
                    original_aggregate_evidence
                ),
                originalization_source_by_question=(
                    originalization_source_by_question
                ),
                question_issue_evidence_by_question=(
                    question_issue_evidence_by_question
                ),
                source_answer_evidence_by_question=(
                    source_answer_evidence_by_question
                ),
                primary_law_evidence_by_question=(
                    primary_law_evidence_by_question
                ),
            )
            child = self.store.create_question_attempt(
                qualification,
                run_id,
                batch_question_ids[0],
                stage_id,
                batch_plan,
                batch_prompt,
            )
            child_id = str(child["runId"])
            queue_stage = single_spec.get("queueStage") or {}
            input_fingerprint_value = str(
                queue_stage.get("inputFingerprint") or ""
            )
            projected_input_hash = str(
                (
                    single_spec.get("projectionUpdate") or {}
                ).get("changes", {}).get("projectedInputHash")
                or ""
            )
            reused_from_attempt_id = ""
            reused_reason = ""
            validation_attempts = [
                dict(value)
                for value in queue_stage.get("validationAttempts") or []
                if isinstance(value, Mapping)
            ]
            latest_attempt = (
                validation_attempts[-1] if validation_attempts else {}
            )
            latest_feedback = latest_attempt.get("feedback")
            latest_issue_codes = {
                str(issue.get("code") or "")
                for issue in (
                    latest_feedback.get("issues") or []
                    if isinstance(latest_feedback, Mapping)
                    else []
                )
                if isinstance(issue, Mapping)
            }
            prior_attempt_id = str(
                latest_attempt.get("childRunId") or ""
            )
            reusable_candidate = (
                self.store.reusable_prewrite_candidate(
                    qualification,
                    run_id,
                    prior_attempt_id,
                    batch_question_ids[0],
                    stage_id,
                    input_fingerprint_value=input_fingerprint_value,
                    projected_input_hash=projected_input_hash,
                )
                if (
                    prior_attempt_id
                    and "canonical_contention" in latest_issue_codes
                )
                else None
            )
            if reusable_candidate is not None:
                reused_from_attempt_id = prior_attempt_id
                reused_reason = "canonical_prewrite_contention"
            elif parent.get("resumedFrom"):
                reusable = self.store.reusable_prepared_candidate(
                    qualification,
                    str(parent["resumedFrom"]),
                    batch_question_ids[0],
                    stage_id,
                    input_fingerprint_value=input_fingerprint_value,
                    projected_input_hash=projected_input_hash,
                )
                if reusable is not None:
                    reused_from_attempt_id, reusable_candidate = reusable
                    reused_reason = "interrupted_before_patch_apply"
            if reused_from_attempt_id:
                self.store.persist_prepared_candidate(
                    qualification,
                    child_id,
                    reusable_candidate,
                )
                self.store.update(
                    qualification,
                    child_id,
                    preparedCandidateReusedFromAttemptId=(
                        reused_from_attempt_id
                    ),
                    preparedCandidateReusedReason=reused_reason,
                )
            if reused_from_attempt_id:
                emit(
                    f"{stage_id}: 入力が一致する検証済み候補を再利用し、"
                    "modelを呼ばずpatch toolだけを再試行します。"
                )
            elif retrying:
                emit(
                    f"{stage_id}: 失敗済み{len(targets)}問だけを"
                    f"{QUESTION_MAINTENANCE_RETRY_MODEL} / 推論 "
                    f"{TURN_REASONING_EFFORT}で再試行します。"
                )
            return {
                "childId": child_id,
                "child": child,
                "questionId": batch_question_ids[0],
                "batchPlan": batch_plan,
                "stageId": stage_id,
                "targets": targets,
                "prompt": batch_prompt,
                "recordsByQuestion": records_by_question,
                "sourceRecordsByQuestion": originalization_source_by_question,
                "candidateTargetsByQuestion": candidate_targets_by_question,
                "requestedModel": requested_model,
                "requestedReasoningEffort": requested_effort,
                "inputFingerprint": input_fingerprint_value,
                "projectedInputHash": projected_input_hash,
                "reusedPreparedCandidate": bool(reused_from_attempt_id),
            }

        def failed_question_outcome(
            prepared: Mapping[str, Any],
            exc: BaseException,
        ) -> dict[str, Any]:
            child_id = str(prepared["childId"])
            child = self.store.refresh(qualification, child_id)
            targets = [
                dict(value)
                for value in (
                    prepared.get("targets")
                    or child.get("progressTargets")
                    or []
                )
                if isinstance(value, Mapping)
            ]
            provider_failure = _external_provider_failure(exc)
            isolated_failure = _isolated_turn_failure(exc)
            schema_failure = isinstance(exc, QuestionCandidateError) or (
                "構造化候補" in str(exc)
                or "JSON Schema" in str(exc)
            )
            return {
                "childId": child_id,
                "child": child,
                "stageId": str(prepared.get("stageId") or child.get("stageId") or ""),
                "questionResults": [
                    {
                        "questionId": str(
                            target.get("id") or target.get("uiQuestionId") or ""
                        ),
                        "status": "failed",
                        "summary": str(child.get("error") or exc),
                        "commands": [],
                        "changedFiles": [],
                    }
                    for target in targets
                ],
                "providerFailure": provider_failure is not None,
                "schemaFailure": schema_failure,
                "isolatedFailure": isolated_failure is not None,
                "providerError": str(provider_failure or ""),
            }

        def run_model(prepared: Mapping[str, Any]) -> dict[str, Any]:
            model_queue_origin = float(
                prepared.get("_modelQueueEnteredMonotonic")
                or time.monotonic()
            )
            executor_wait = max(
                0.0,
                time.monotonic() - model_queue_origin,
            )

            def observe_model_turn(event: Mapping[str, Any]) -> float | None:
                return pipeline_telemetry.observe_model_turn(event)

            try:
                self.store.update(
                    qualification,
                    str(prepared["childId"]),
                    modelExecutorQueueWaitSeconds=round(
                        executor_wait,
                        6,
                    ),
                )
                self._run_structured_question(
                    qualification,
                    str(prepared["childId"]),
                    str(prepared["prompt"]),
                    emit,
                    batch_plan=dict(prepared["batchPlan"]),
                    stage_id=str(prepared["stageId"]),
                    pipeline_stop=pipeline_stop,
                    prepared_records=dict(prepared["recordsByQuestion"]),
                    prepared_source_records=dict(
                        prepared["sourceRecordsByQuestion"]
                    ),
                    prepared_targets=dict(
                        prepared["candidateTargetsByQuestion"]
                    ),
                    model=str(prepared["requestedModel"]),
                    reasoning_effort=str(
                        prepared["requestedReasoningEffort"]
                    ),
                    input_fingerprint_value=str(
                        prepared["inputFingerprint"]
                    ),
                    projected_input_hash=str(
                        prepared["projectedInputHash"]
                    ),
                    model_queue_origin_monotonic=model_queue_origin,
                    on_model_turn_event=observe_model_turn,
                    prepare_only=True,
                )
                return {
                    "prepared": {
                        "childId": str(prepared["childId"]),
                        "stageId": str(prepared["stageId"]),
                        "questionId": str(prepared["questionId"]),
                    },
                    "outcome": None,
                }
            except Exception as exc:  # noqa: BLE001
                return {
                    "prepared": {
                        "childId": str(prepared["childId"]),
                        "stageId": str(prepared["stageId"]),
                        "questionId": str(prepared["questionId"]),
                    },
                    "outcome": failed_question_outcome(prepared, exc),
                }

        def apply_prepared_candidate(
            prepared: Mapping[str, Any],
        ) -> dict[str, Any]:
            child_id = str(prepared["childId"])
            patch_tool_queue_wait = max(
                0.0,
                time.monotonic()
                - float(
                    prepared.get("_patchToolQueueEnteredMonotonic")
                    or time.monotonic()
                ),
            )
            pipeline_telemetry.patch_tool_started(
                child_id,
                queue_wait_seconds=patch_tool_queue_wait,
            )
            try:
                child = self.store.get(qualification, child_id)
                self.store.update(
                    qualification,
                    child_id,
                    patchToolQueueWaitSeconds=round(
                        patch_tool_queue_wait,
                        6,
                    ),
                )
                envelope = child.get("preparedCandidate")
                if not isinstance(envelope, Mapping):
                    raise QualificationRunError(
                        "patch反映対象に保存済み候補がありません。"
                    )
                outcome = self._run_structured_question(
                    qualification,
                    child_id,
                    "",
                    emit,
                    batch_plan=dict(child),
                    stage_id=str(child.get("stageId") or ""),
                    pipeline_stop=pipeline_stop,
                    model=str(
                        child.get("requestedModel")
                        or QUESTION_MAINTENANCE_MODEL
                    ),
                    reasoning_effort=str(
                        child.get("requestedReasoningEffort")
                        or TURN_REASONING_EFFORT
                    ),
                    input_fingerprint_value=str(
                        envelope.get("inputFingerprint") or ""
                    ),
                    projected_input_hash=str(
                        envelope.get("projectedInputHash") or ""
                    ),
                    on_patch_lock_acquired=(
                        lambda seconds, paths: (
                            pipeline_telemetry.patch_lock_acquired(
                                child_id,
                                paths,
                                seconds,
                            )
                        )
                    ),
                    on_patch_lock_released=lambda: (
                        pipeline_telemetry.patch_lock_released(child_id)
                    ),
                    apply_prepared=True,
                )
                return {
                    "childId": child_id,
                    "child": outcome["child"],
                    "stageId": str(child.get("stageId") or ""),
                    "questionResults": outcome["questionResults"],
                    "providerFailure": False,
                    "schemaFailure": False,
                    "isolatedFailure": False,
                }
            except Exception as exc:  # noqa: BLE001
                return failed_question_outcome(prepared, exc)
            finally:
                pipeline_telemetry.patch_tool_finished(child_id)

        def register_prepared_batches(
            prepared_batches: list[Mapping[str, Any]],
            stage_id: str | None = None,
        ) -> None:
            if not prepared_batches:
                return
            committing_updates: list[dict[str, Any]] = []
            for prepared in prepared_batches:
                child_id = str(prepared["childId"])
                prepared_stage_id = str(
                    prepared.get("stageId") or stage_id or ""
                )
                requested_model = str(prepared["requestedModel"])
                requested_effort = str(
                    prepared["requestedReasoningEffort"]
                )
                for target in prepared.get("targets") or []:
                    question_id = str(
                        target.get("id")
                        or target.get("uiQuestionId")
                        or ""
                    )
                    detail = self.store.question_detail(
                        qualification,
                        run_id,
                        question_id,
                    )
                    current = self._queue_stage(
                        {"questionExecutions": [detail["execution"]]},
                        question_id,
                        prepared_stage_id,
                    ) or {}
                    attempts = [
                        dict(value)
                        for value in current.get("validationAttempts") or []
                        if isinstance(value, Mapping)
                    ]
                    attempts.append(
                        {
                            "attempt": len(attempts) + 1,
                            "childRunId": child_id,
                            "status": "running",
                            "feedback": None,
                            "requestedModel": requested_model,
                            "requestedReasoningEffort": requested_effort,
                            "startedAt": _now(),
                            "finishedAt": None,
                        }
                    )
                    committing_updates.append(
                        {
                            "questionId": question_id,
                            "stageId": prepared_stage_id,
                            "changes": {
                                "status": "preparing",
                                "childRunIds": [
                                    *(
                                        str(value)
                                        for value in current.get(
                                            "childRunIds"
                                        )
                                        or []
                                        if value
                                    ),
                                    child_id,
                                ],
                                "validationAttempts": attempts,
                                "error": None,
                            },
                        }
                    )
            self.store.update_question_stages(
                qualification,
                run_id,
                committing_updates,
                hydrate_result=False,
            )

        def apply_batch_outcome(
            outcome: Mapping[str, Any],
            stage_id: str,
            *,
            next_ids: list[str],
            provider_waiting: set[str],
            parent_snapshot: Mapping[str, Any],
            stage_updates: list[dict[str, Any]],
        ) -> None:
            child = dict(outcome["child"])
            child_id = str(outcome["childId"])
            provider_failure = bool(outcome.get("providerFailure"))
            with aggregation_lock:
                phase_runtime[stage_id] = {
                    "threadId": child.get("threadId"),
                    "sessionId": child.get("sessionId"),
                    "turnId": child.get("turnId"),
                    "model": child.get("model"),
                    "serviceTier": child.get("serviceTier"),
                    "reasoningEffort": child.get("reasoningEffort"),
                }
            for raw_result in outcome.get("questionResults") or []:
                question_id = str(raw_result.get("questionId") or "")
                current = self._queue_stage(
                    parent_snapshot,
                    question_id,
                    stage_id,
                ) or {}
                attempts = [
                    dict(value)
                    for value in current.get("validationAttempts") or []
                    if isinstance(value, Mapping)
                ]
                attempt_index = next(
                    (
                        index
                        for index in range(len(attempts) - 1, -1, -1)
                        if str(attempts[index].get("childRunId") or "") == child_id
                    ),
                    None,
                )
                if attempt_index is None:
                    raise QualificationRunError(
                        f"batch attemptを親queueで確認できません: {question_id}"
                    )
                attempts[attempt_index].update(
                    model=child.get("model"),
                    reasoningEffort=child.get("reasoningEffort"),
                )
                if str(raw_result.get("status") or "") == "succeeded":
                    accepted = {
                        "status": "accepted",
                        "reason": str(raw_result.get("summary") or "検証済み"),
                        "questionId": question_id,
                        "stageId": stage_id,
                        "childRunId": child_id,
                        "attempt": attempt_index + 1,
                        "failedChecks": [],
                    }
                    attempts[attempt_index].update(
                        status="validated",
                        feedback=accepted,
                        finishedAt=_now(),
                    )
                    work_version_receipt = raw_result.get("workVersionReceipt")
                    if isinstance(work_version_receipt, Mapping):
                        with aggregation_lock:
                            work_version_receipts.append(
                                dict(work_version_receipt)
                            )
                    target = next(
                        (
                            value
                            for value in child.get("progressTargets") or []
                            if isinstance(value, Mapping)
                            and str(value.get("id") or "") == question_id
                        ),
                        {},
                    )
                    list_group_id = str(target.get("listGroupId") or "")
                    if list_group_id:
                        with aggregation_lock:
                            confirmed_group_ids.add(list_group_id)
                    stage_updates.append(
                        {
                            "questionId": question_id,
                            "stageId": stage_id,
                            "validatedReceipt": (
                                dict(work_version_receipt)
                                if isinstance(work_version_receipt, Mapping)
                                else None
                            ),
                            "changes": {
                                "status": "validated",
                                "validationAttempts": attempts,
                                "outputFingerprint": hashlib.sha256(
                                    json.dumps(
                                        raw_result,
                                        ensure_ascii=False,
                                        sort_keys=True,
                                    ).encode("utf-8")
                                ).hexdigest(),
                                "retryDeferred": False,
                                "error": None,
                                "finishedAt": _now(),
                            },
                        }
                    )
                    continue

                if provider_failure:
                    attempts[attempt_index].update(
                        status="interrupted",
                        feedback=None,
                        pauseReason=str(
                            outcome.get("providerError")
                            or raw_result.get("summary")
                            or "Codex App Serverを利用できません。"
                        ),
                        finishedAt=_now(),
                    )
                    provider_attempts = sum(
                        str(value.get("status") or "") == "interrupted"
                        for value in attempts
                    )
                    stage_updates.append(
                        {
                            "questionId": question_id,
                            "stageId": stage_id,
                            "changes": {
                                "status": "queued",
                                "validationAttempts": attempts,
                                "retryDeferred": True,
                                "error": str(raw_result.get("summary") or ""),
                                "finishedAt": None,
                            },
                        }
                    )
                    if provider_attempts < provider_attempt_limit:
                        next_ids.append(question_id)
                    else:
                        provider_waiting.add(question_id)
                    continue

                if raw_result.get("policyChanged") is True:
                    attempts[attempt_index].update(
                        status="superseded",
                        feedback=None,
                        finishedAt=_now(),
                    )
                    if self._requeue_policy_changed_question(
                        qualification,
                        run_id,
                        question_id,
                        stage_id,
                        emit,
                        superseded_child_run_id=child_id,
                        validation_attempts=attempts,
                    ):
                        next_ids.append(question_id)
                    continue

                normalized = QuestionValidationResult(
                    question_id=question_id,
                    status="failed",
                    summary=str(raw_result.get("summary") or "機械検査に失敗しました。"),
                    commands=tuple(
                        dict(value)
                        for value in raw_result.get("commands") or []
                        if isinstance(value, Mapping)
                    ),
                    changed_files=(),
                )
                quality_attempt = 1 + sum(
                    str(value.get("status") or "") in {"failed", "blocked"}
                    for value in attempts[:attempt_index]
                )
                feedback = self._question_feedback(
                    child,
                    normalized,
                    attempt=quality_attempt,
                    stage_id=stage_id,
                )
                blocked = feedback.get("status") == "blocked" or quality_attempt >= 3
                attempts[attempt_index].update(
                    status="blocked" if blocked else "failed",
                    feedback=feedback,
                    finishedAt=_now(),
                )
                stage_updates.append(
                    {
                        "questionId": question_id,
                        "stageId": stage_id,
                        "blockDependents": blocked,
                        "changes": {
                            "status": "blocked" if blocked else "queued",
                            "validationAttempts": attempts,
                            "retryDeferred": not blocked,
                            "error": normalized.summary,
                            "finishedAt": _now() if blocked else None,
                        },
                    }
                )
                if not blocked:
                    next_ids.append(question_id)
        immutable_parent = {
            key: copy.deepcopy(value)
            for key, value in parent.items()
            if key != "questionExecutions"
        }
        initial_question_ids = [
            str(value.get("questionId") or "")
            for value in parent.get("questionExecutions") or []
            if isinstance(value, Mapping) and value.get("questionId")
        ]
        phase_contexts: dict[
            str,
            tuple[dict[str, Any], str, QuestionPlanIndex],
        ] = {}
        started_phase_ids: set[str] = set()

        def compact_parent_snapshot() -> dict[str, Any]:
            return {
                **copy.deepcopy(immutable_parent),
                **self.store.get_compact(qualification, run_id),
            }

        def question_parent_snapshot(
            question_id: str,
        ) -> tuple[dict[str, Any], dict[str, Mapping[str, Any]]]:
            detail = self.store.question_detail(
                qualification,
                run_id,
                question_id,
            )
            execution = copy.deepcopy(dict(detail["execution"]))
            snapshot = compact_parent_snapshot()
            snapshot["questionExecutions"] = [execution]
            return snapshot, {question_id: execution}

        def phase_context(
            phase: Mapping[str, Any],
        ) -> tuple[dict[str, Any], str, QuestionPlanIndex]:
            stage_id = str(phase["id"])
            current = phase_contexts.get(stage_id)
            if current is not None and self._phase_plan_policy_is_current(
                qualification,
                current[0],
                stage_id,
            ):
                return current
            phase_parent = compact_parent_snapshot()
            inventory = getattr(self.workflow, "inventory", None)
            projection_snapshot = getattr(
                inventory,
                "projection_snapshot",
                None,
            )
            def build_context() -> tuple[
                dict[str, Any],
                str,
                QuestionPlanIndex,
            ]:
                snapshot_context = (
                    projection_snapshot(
                        qualification,
                        phase_parent.get("targetGroupIds") or [],
                    )
                    if callable(projection_snapshot)
                    else nullcontext()
                )
                with snapshot_context:
                    phase_plan, phase_prompt = self._flow_phase_plan_prompt(
                        phase_parent,
                        phase,
                    )
                return (
                    phase_plan,
                    phase_prompt,
                    build_question_plan_index(phase_plan),
                )
            current = build_context()
            if not self._phase_plan_policy_is_current(
                qualification,
                current[0],
                stage_id,
            ):
                phase_parent = compact_parent_snapshot()
                current = build_context()
            phase_contexts[stage_id] = current
            return current

        def pending_stage_for_question(
            question_id: str,
            segment_stage_ids: set[str],
        ) -> str | None:
            detail = self.store.question_detail(
                qualification,
                run_id,
                question_id,
            )
            for stage in detail["execution"].get("stages") or []:
                if (
                    not isinstance(stage, Mapping)
                    or str(stage.get("stageId") or "")
                    not in segment_stage_ids
                ):
                    continue
                status = str(stage.get("status") or "queued")
                if status in {"validated", "not_applicable"}:
                    continue
                if status == "blocked":
                    return None
                return str(stage["stageId"])
            return None

        def run_question_segment(
            segment_phases: list[dict[str, Any]],
        ) -> None:
            if not segment_phases:
                return
            pipeline_telemetry.question_window_segment_started()
            phase_by_id = {
                str(value["id"]): value for value in segment_phases
            }
            segment_stage_ids = set(phase_by_id)
            waiting_questions: deque[str] = deque(initial_question_ids)
            continuation_queue: deque[tuple[str, str]] = deque()
            retry_queue: deque[tuple[str, str]] = deque()
            ready_queue: deque[tuple[str, str]] = deque()
            window_questions: set[str] = set()
            applying_questions: set[str] = set()
            provider_waiting: set[str] = set()
            pending_spec_futures: dict[
                Any,
                tuple[str, str, Mapping[str, Any], str],
            ] = {}
            pending_batch_futures: dict[Any, tuple[str, str]] = {}
            pending_model_futures: dict[Any, tuple[str, str]] = {}
            pending_patch_tool_futures: dict[Any, tuple[str, str]] = {}
            prepared_model_backlog: deque[dict[str, Any]] = deque()
            patch_tool_backlog: deque[dict[str, Any]] = deque()
            input_tool_limit = min(
                question_concurrency,
                PREPARATION_MAX_PARALLEL_QUESTIONS,
                max(len(initial_question_ids), 1),
            )
            patch_tool_limit = question_concurrency
            tool_worker_limit = input_tool_limit + patch_tool_limit
            batch_count = 0
            prepared_count = 0
            prepared_spec_count = 0
            maximum_batch_size = 0
            peak_model_futures = 0
            peak_patch_tool_futures = 0
            peak_patch_tool_backlog = 0
            peak_pipeline_futures = 0
            peak_preparation_futures = 0
            peak_tool_futures = 0
            peak_question_window = 0
            model_started = False
            last_runtime_snapshot = 0.0
            provider_recovery_attempt = 0
            provider_recovery_needed = False

            def queue_continuation(question_id: str) -> None:
                next_stage_id = pending_stage_for_question(
                    question_id,
                    segment_stage_ids,
                )
                if next_stage_id is not None:
                    continuation_queue.append((question_id, next_stage_id))

            def release_window(question_id: str) -> None:
                if question_id not in window_questions:
                    return
                window_questions.remove(question_id)
                pipeline_telemetry.question_window_released(
                    question_id,
                    observed_monotonic=time.monotonic(),
                )

            def fill_question_window() -> None:
                nonlocal peak_question_window
                while len(window_questions) < question_concurrency:
                    work: tuple[str, str] | None = None
                    admission_source = ""
                    if continuation_queue:
                        work = continuation_queue.popleft()
                        admission_source = "continuation"
                    elif waiting_questions:
                        question_id = waiting_questions.popleft()
                        stage_id = pending_stage_for_question(
                            question_id,
                            segment_stage_ids,
                        )
                        if stage_id is not None:
                            work = (question_id, stage_id)
                            admission_source = "waiting"
                    elif retry_queue:
                        work = retry_queue.popleft()
                        admission_source = "retry"
                    else:
                        break
                    if work is None:
                        continue
                    question_id, stage_id = work
                    if (
                        question_id in window_questions
                        or question_id in applying_questions
                    ):
                        raise QualificationRunError(
                            "同じ問題を同時に二工程へ投入しようとしました: "
                            f"{question_id}"
                        )
                    window_questions.add(question_id)
                    pipeline_telemetry.question_window_admitted(
                        question_id,
                        source=admission_source,
                        observed_monotonic=time.monotonic(),
                    )
                    ready_queue.append((question_id, stage_id))
                peak_question_window = max(
                    peak_question_window,
                    len(window_questions),
                )

            def mark_phase_started(
                stage_id: str,
                phase_plan: Mapping[str, Any],
            ) -> None:
                if stage_id in started_phase_ids:
                    return
                started_phase_ids.add(stage_id)
                phase_child_ids.setdefault(stage_id, [])
                self._update_flow_phase(
                    qualification,
                    run_id,
                    stage_id,
                    status="running",
                    targetCount=int(phase_plan.get("targetCount") or 0),
                    childRunIds=[],
                    startedAt=_now(),
                    error=None,
                )

            def submit_ready_specs(
                tool_executor: ThreadPoolExecutor,
            ) -> None:
                nonlocal peak_preparation_futures
                nonlocal peak_pipeline_futures
                nonlocal peak_tool_futures
                while (
                    ready_queue
                    and len(pending_spec_futures) < input_tool_limit
                ):
                    question_id, stage_id = ready_queue.popleft()
                    phase = phase_by_id[stage_id]
                    phase_plan, phase_prompt, phase_plan_index = (
                        phase_context(phase)
                    )
                    mark_phase_started(stage_id, phase_plan)
                    question_parent, question_index = (
                        question_parent_snapshot(question_id)
                    )
                    future = tool_executor.submit(
                        prepare_spec,
                        phase,
                        phase_plan,
                        phase_plan_index,
                        phase_prompt,
                        question_id,
                        question_parent,
                        question_index,
                    )
                    pending_spec_futures[future] = (
                        question_id,
                        stage_id,
                        phase,
                        phase_prompt,
                    )
                peak_preparation_futures = max(
                    peak_preparation_futures,
                    len(pending_spec_futures)
                    + len(pending_batch_futures),
                )
                peak_tool_futures = max(
                    peak_tool_futures,
                    len(pending_spec_futures)
                    + len(pending_batch_futures)
                    + len(pending_patch_tool_futures),
                )
                peak_pipeline_futures = max(
                    peak_pipeline_futures,
                    len(pending_spec_futures)
                    + len(pending_batch_futures)
                    + len(pending_model_futures)
                    + len(pending_patch_tool_futures)
                    + len(prepared_model_backlog)
                    + len(patch_tool_backlog),
                )

            def submit_model_backlog(
                model_executor: ThreadPoolExecutor,
            ) -> None:
                nonlocal model_started
                nonlocal peak_model_futures
                nonlocal peak_pipeline_futures
                model_limit = min(
                    scheduler_limits.parallel_turns,
                    question_concurrency,
                )
                while (
                    prepared_model_backlog
                    and len(pending_model_futures) < model_limit
                ):
                    prepared = prepared_model_backlog.popleft()
                    question_id = str(prepared["questionId"])
                    stage_id = str(prepared["stageId"])
                    future = model_executor.submit(run_model, prepared)
                    pending_model_futures[future] = (
                        question_id,
                        stage_id,
                    )
                    model_started = True
                peak_model_futures = max(
                    peak_model_futures,
                    len(pending_model_futures),
                )
                peak_pipeline_futures = max(
                    peak_pipeline_futures,
                    len(pending_spec_futures)
                    + len(pending_batch_futures)
                    + len(pending_model_futures)
                    + len(pending_patch_tool_futures)
                    + len(prepared_model_backlog)
                    + len(patch_tool_backlog),
                )

            def submit_patch_tool_backlog(
                tool_executor: ThreadPoolExecutor,
            ) -> None:
                nonlocal peak_patch_tool_futures
                nonlocal peak_pipeline_futures
                nonlocal peak_tool_futures
                while (
                    patch_tool_backlog
                    and len(pending_patch_tool_futures) < patch_tool_limit
                ):
                    prepared = patch_tool_backlog.popleft()
                    question_id = str(prepared["questionId"])
                    stage_id = str(prepared["stageId"])
                    future = tool_executor.submit(
                        apply_prepared_candidate,
                        prepared,
                    )
                    pending_patch_tool_futures[future] = (
                        question_id,
                        stage_id,
                    )
                peak_patch_tool_futures = max(
                    peak_patch_tool_futures,
                    len(pending_patch_tool_futures),
                )
                peak_tool_futures = max(
                    peak_tool_futures,
                    len(pending_spec_futures)
                    + len(pending_batch_futures)
                    + len(pending_patch_tool_futures),
                )
                peak_pipeline_futures = max(
                    peak_pipeline_futures,
                    len(pending_spec_futures)
                    + len(pending_batch_futures)
                    + len(pending_model_futures)
                    + len(pending_patch_tool_futures)
                    + len(prepared_model_backlog)
                    + len(patch_tool_backlog),
                )

            def apply_completed_outcomes(
                outcomes: Iterable[Mapping[str, Any]],
            ) -> None:
                nonlocal provider_recovery_needed
                normalized_outcomes = list(outcomes)
                if not normalized_outcomes:
                    return
                stage_updates: list[dict[str, Any]] = []
                affected: list[tuple[str, str]] = []
                retry_ids: set[tuple[str, str]] = set()
                for outcome in normalized_outcomes:
                    stage_id = str(outcome.get("stageId") or "")
                    provider_failure = bool(outcome.get("providerFailure"))
                    provider_recovery_needed = (
                        provider_recovery_needed or provider_failure
                    )
                    scheduler_limits.observe(
                        provider_failure=provider_failure,
                        schema_failure=bool(outcome.get("schemaFailure")),
                        isolated_failure=bool(outcome.get("isolatedFailure")),
                        max_parallel_turns=question_concurrency,
                    )
                    results = [
                        value
                        for value in outcome.get("questionResults") or []
                        if isinstance(value, Mapping)
                    ]
                    if len(results) != 1:
                        raise QualificationRunError(
                            "一問turnの結果件数が1件ではありません。"
                        )
                    question_id = str(results[0].get("questionId") or "")
                    outcome_parent, _ = question_parent_snapshot(question_id)
                    next_ids: list[str] = []
                    apply_batch_outcome(
                        outcome,
                        stage_id,
                        next_ids=next_ids,
                        provider_waiting=provider_waiting,
                        parent_snapshot=outcome_parent,
                        stage_updates=stage_updates,
                    )
                    affected.append((question_id, stage_id))
                    retry_ids.update((value, stage_id) for value in next_ids)
                if stage_updates:
                    self.store.update_question_stages(
                        qualification,
                        run_id,
                        stage_updates,
                        hydrate_result=False,
                    )
                for question_id, stage_id in affected:
                    release_window(question_id)
                    applying_questions.discard(question_id)
                    if question_id in provider_waiting:
                        continue
                    detail = self.store.question_detail(
                        qualification,
                        run_id,
                        question_id,
                    )
                    current = self._queue_stage(
                        {"questionExecutions": [detail["execution"]]},
                        question_id,
                        stage_id,
                    )
                    status = str((current or {}).get("status") or "")
                    if (question_id, stage_id) in retry_ids or status == "queued":
                        retry_queue.append((question_id, stage_id))
                    elif status in {"validated", "not_applicable"}:
                        queue_continuation(question_id)

            def update_runtime_snapshot(*, force: bool = False) -> None:
                nonlocal last_runtime_snapshot
                current_monotonic = time.monotonic()
                if (
                    not force
                    and current_monotonic - last_runtime_snapshot < 5.0
                ):
                    return
                self.store.update(
                    qualification,
                    run_id,
                    hydrate_result=False,
                    adaptiveScheduler=scheduler_status(
                        scheduler_limits,
                        batch_count=batch_count,
                        in_flight_questions=len(window_questions),
                    ),
                    modelBatchSize=maximum_batch_size,
                    modelWorkerLimit=peak_model_futures,
                    inputToolLimit=input_tool_limit,
                    toolWorkerLimit=tool_worker_limit,
                    pipelineWorkerLimit=(
                        question_concurrency + tool_worker_limit
                    ),
                    patchToolBacklogLimit=patch_tool_limit,
                    questionWindowLimit=min(
                        question_concurrency,
                        max(len(initial_question_ids), 1),
                    ),
                    questionWindowPendingCount=len(window_questions),
                    questionWindowPeakPendingCount=peak_question_window,
                    inputToolPendingFutureCount=(
                        len(pending_spec_futures)
                        + len(pending_batch_futures)
                    ),
                    inputToolPeakPendingFutureCount=(
                        peak_preparation_futures
                    ),
                    modelPendingFutureCount=len(pending_model_futures),
                    patchToolPendingFutureCount=len(
                        pending_patch_tool_futures
                    ),
                    patchToolBacklogCount=len(patch_tool_backlog),
                    toolPendingFutureCount=(
                        len(pending_spec_futures)
                        + len(pending_batch_futures)
                        + len(pending_patch_tool_futures)
                    ),
                    pipelinePendingFutureCount=(
                        len(pending_spec_futures)
                        + len(pending_batch_futures)
                        + len(pending_model_futures)
                        + len(pending_patch_tool_futures)
                        + len(prepared_model_backlog)
                        + len(patch_tool_backlog)
                    ),
                    pipelinePeakPendingFutureCount=peak_pipeline_futures,
                    modelPeakPendingFutureCount=peak_model_futures,
                    patchToolPeakPendingFutureCount=(
                        peak_patch_tool_futures
                    ),
                    patchToolPeakBacklogCount=peak_patch_tool_backlog,
                    toolPeakPendingFutureCount=peak_tool_futures,
                    modelTurns=pipeline_telemetry.model_snapshot(),
                    questionWindow=pipeline_telemetry.question_window_snapshot(),
                    patchTools=pipeline_telemetry.patch_tool_snapshot(),
                )
                last_runtime_snapshot = current_monotonic

            fill_question_window()
            preparation_heartbeat(
                "question_pipeline",
                round_number=1,
                prepared_count=0,
                target_count=len(initial_question_ids),
                prepared_spec_count=0,
                batch_count=0,
                model_started=False,
                status="preparing",
                force=True,
            )
            with (
                ThreadPoolExecutor(
                    max_workers=max(1, question_concurrency),
                    thread_name_prefix="question-model",
                ) as model_executor,
                ThreadPoolExecutor(
                    max_workers=max(1, tool_worker_limit),
                    thread_name_prefix="question-tool",
                ) as tool_executor,
            ):
                try:
                    while True:
                        fill_question_window()
                        submit_ready_specs(tool_executor)
                        submit_model_backlog(model_executor)
                        submit_patch_tool_backlog(tool_executor)

                        completed_specs = [
                            future
                            for future in pending_spec_futures
                            if future.done()
                        ]
                        projection_updates: list[dict[str, Any]] = []
                        prepared_specs: list[
                            tuple[
                                Mapping[str, Any],
                                str,
                                str,
                                Mapping[str, Any],
                                str,
                            ]
                        ] = []
                        spec_without_candidate = False
                        for future in completed_specs:
                            (
                                question_id,
                                stage_id,
                                phase,
                                phase_prompt,
                            ) = pending_spec_futures.pop(future)
                            prepared_count += 1
                            spec = future.result()
                            if not isinstance(spec, Mapping):
                                release_window(question_id)
                                queue_continuation(question_id)
                                spec_without_candidate = True
                                continue
                            prepared_spec_count += 1
                            if isinstance(
                                spec.get("projectionUpdate"),
                                Mapping,
                            ):
                                projection_updates.append(
                                    dict(spec["projectionUpdate"])
                                )
                            prepared_specs.append(
                                (
                                    spec,
                                    question_id,
                                    stage_id,
                                    phase,
                                    phase_prompt,
                                )
                            )
                        if projection_updates:
                            self.store.update_question_stages(
                                qualification,
                                run_id,
                                projection_updates,
                                hydrate_result=False,
                            )
                        elif spec_without_candidate:
                            self.store.refresh_question_summary(
                                qualification,
                                run_id,
                                hydrate_result=False,
                            )
                        for (
                            spec,
                            question_id,
                            stage_id,
                            phase,
                            phase_prompt,
                        ) in prepared_specs:
                            batch_count += 1
                            maximum_batch_size = max(maximum_batch_size, 1)
                            question_parent, _ = question_parent_snapshot(
                                question_id
                            )
                            future = tool_executor.submit(
                                prepare_batch,
                                [spec],
                                phase,
                                phase_prompt,
                                question_parent,
                            )
                            pending_batch_futures[future] = (
                                question_id,
                                stage_id,
                            )
                        peak_preparation_futures = max(
                            peak_preparation_futures,
                            len(pending_spec_futures)
                            + len(pending_batch_futures),
                        )
                        peak_tool_futures = max(
                            peak_tool_futures,
                            len(pending_spec_futures)
                            + len(pending_batch_futures)
                            + len(pending_patch_tool_futures),
                        )
                        peak_pipeline_futures = max(
                            peak_pipeline_futures,
                            len(pending_spec_futures)
                            + len(pending_batch_futures)
                            + len(pending_model_futures)
                            + len(pending_patch_tool_futures)
                            + len(prepared_model_backlog)
                            + len(patch_tool_backlog),
                        )

                        completed_batches = [
                            future
                            for future in pending_batch_futures
                            if future.done()
                        ]
                        prepared_batches: list[Mapping[str, Any]] = []
                        for future in completed_batches:
                            pending_batch_futures.pop(future)
                            prepared_batches.append(future.result())
                        if prepared_batches:
                            register_prepared_batches(prepared_batches)
                        for prepared in prepared_batches:
                            if prepared.get("reusedPreparedCandidate"):
                                if self.store.is_question_attempt(
                                    str(prepared["childId"])
                                ):
                                    self.store.update_attempt_stage_status(
                                        qualification,
                                        str(prepared["childId"]),
                                        "prepared",
                                )
                                question_id = str(prepared["questionId"])
                                release_window(question_id)
                                applying_questions.add(question_id)
                                patch_tool_backlog.append(
                                    {
                                        "childId": str(prepared["childId"]),
                                        "stageId": str(prepared["stageId"]),
                                        "questionId": question_id,
                                        "_patchToolQueueEnteredMonotonic": (
                                            time.monotonic()
                                        ),
                                    }
                                )
                            else:
                                prepared_value = dict(prepared)
                                prepared_value["_modelQueueEnteredMonotonic"] = (
                                    time.monotonic()
                                )
                                prepared_model_backlog.append(
                                    prepared_value
                                )

                        completed_models = [
                            future
                            for future in pending_model_futures
                            if future.done()
                        ]
                        immediate_outcomes: list[Mapping[str, Any]] = []
                        for future in completed_models:
                            question_id, stage_id = (
                                pending_model_futures.pop(future)
                            )
                            generated = future.result()
                            outcome = generated.get("outcome")
                            if isinstance(outcome, Mapping):
                                immediate_outcomes.append(outcome)
                            else:
                                release_window(question_id)
                                applying_questions.add(question_id)
                                prepared = dict(generated["prepared"])
                                prepared.setdefault(
                                    "questionId",
                                    question_id,
                                )
                                prepared.setdefault("stageId", stage_id)
                                prepared["_patchToolQueueEnteredMonotonic"] = (
                                    time.monotonic()
                                )
                                patch_tool_backlog.append(prepared)
                        apply_completed_outcomes(immediate_outcomes)

                        completed_patch_tools = [
                            future
                            for future in pending_patch_tool_futures
                            if future.done()
                        ]
                        patch_tool_outcomes: list[Mapping[str, Any]] = []
                        for future in completed_patch_tools:
                            pending_patch_tool_futures.pop(future)
                            patch_tool_outcomes.append(future.result())
                        apply_completed_outcomes(patch_tool_outcomes)

                        peak_preparation_futures = max(
                            peak_preparation_futures,
                            len(pending_spec_futures)
                            + len(pending_batch_futures),
                        )
                        peak_patch_tool_backlog = max(
                            peak_patch_tool_backlog,
                            len(patch_tool_backlog),
                        )
                        peak_tool_futures = max(
                            peak_tool_futures,
                            len(pending_spec_futures)
                            + len(pending_batch_futures)
                            + len(pending_patch_tool_futures),
                        )
                        peak_question_window = max(
                            peak_question_window,
                            len(window_questions),
                        )
                        peak_pipeline_futures = max(
                            peak_pipeline_futures,
                            len(pending_spec_futures)
                            + len(pending_batch_futures)
                            + len(pending_model_futures)
                            + len(pending_patch_tool_futures)
                            + len(prepared_model_backlog)
                            + len(patch_tool_backlog),
                        )
                        fill_question_window()
                        submit_ready_specs(tool_executor)
                        submit_model_backlog(model_executor)
                        submit_patch_tool_backlog(tool_executor)
                        update_runtime_snapshot()
                        preparation_heartbeat(
                            "question_pipeline",
                            round_number=1,
                            prepared_count=prepared_count,
                            target_count=len(initial_question_ids),
                            prepared_spec_count=prepared_spec_count,
                            batch_count=batch_count,
                            model_started=model_started,
                            status=(
                                "streaming"
                                if model_started
                                else "preparing"
                            ),
                        )

                        outstanding = (
                            set(pending_spec_futures)
                            | set(pending_batch_futures)
                            | set(pending_model_futures)
                            | set(pending_patch_tool_futures)
                        )
                        queued = bool(
                            waiting_questions
                            or continuation_queue
                            or retry_queue
                            or ready_queue
                            or prepared_model_backlog
                            or patch_tool_backlog
                        )
                        if not outstanding and not queued:
                            break
                        if not outstanding:
                            continue
                        if not any(future.done() for future in outstanding):
                            completed, remaining = wait(
                                outstanding,
                                return_when=FIRST_COMPLETED,
                            )
                            if completed and remaining:
                                wait(
                                    remaining,
                                    timeout=OUTCOME_COALESCE_SECONDS,
                                )

                        if (
                            provider_recovery_needed
                            and not pending_model_futures
                            and not prepared_model_backlog
                        ):
                            provider_recovery_attempt += 1
                            recover = getattr(
                                self.app_server,
                                "recover_after_provider_failure",
                                None,
                            )
                            if callable(recover):
                                recover(
                                    attempt=provider_recovery_attempt,
                                    emit=emit,
                                )
                            provider_recovery_needed = False
                except BaseException:
                    pipeline_stop.set()
                    for future in (
                        set(pending_spec_futures)
                        | set(pending_batch_futures)
                        | set(pending_model_futures)
                        | set(pending_patch_tool_futures)
                    ):
                        future.cancel()
                    raise

            update_runtime_snapshot(force=True)
            preparation_heartbeat(
                "question_pipeline",
                round_number=1,
                prepared_count=prepared_count,
                target_count=len(initial_question_ids),
                prepared_spec_count=prepared_spec_count,
                batch_count=batch_count,
                model_started=model_started,
                status="prepared",
                force=True,
            )
            self.store.update(
                qualification,
                run_id,
                hydrate_result=False,
                questionWindowPendingCount=0,
                inputToolPendingFutureCount=0,
                modelPendingFutureCount=0,
                patchToolPendingFutureCount=0,
                patchToolBacklogCount=0,
                toolPendingFutureCount=0,
                pipelinePendingFutureCount=0,
            )
            if provider_waiting:
                reason = (
                    "通常問題の処理後もCodex App Serverを利用できない問題が"
                    f"{len(provider_waiting)}問残りました。回復後に再開してください。"
                )
                pause = QuestionQueuePaused(
                    reason,
                    pause_kind="external_provider",
                )
                self._persist_queue_pause(qualification, run_id, pause)
                raise pause

        question_segment: list[dict[str, Any]] = []
        for phase in phases:
            stage_id = str(phase["id"])
            if stage_id not in {"setup", "category_setup"}:
                question_segment.append(phase)
                continue
            run_question_segment(question_segment)
            question_segment = []
            self._run_shared_prerequisite(
                qualification,
                run_id,
                phase,
                emit,
                child_run_ids=child_run_ids,
                work_version_receipts=work_version_receipts,
                confirmed_group_ids=confirmed_group_ids,
            )
        run_question_segment(question_segment)

        self._finalize_question_phases(
            qualification,
            run_id,
            phases,
            phase_child_ids,
            phase_runtime,
        )
        return {
            "childRunIds": child_run_ids,
            "workVersionReceipts": work_version_receipts,
            "confirmedGroupIds": sorted(confirmed_group_ids),
        }

    def _record_improvement_report(
        self,
        qualification: str,
        run_id: str,
        emit: Callable[[str], None],
    ) -> str | None:
        current = self.store.get(qualification, run_id)
        try:
            report_path = write_improvement_report(
                self.store.root / qualification / run_id,
                build_improvement_report(current.get("questionExecutions") or []),
            )
            self.store.update(
                qualification,
                run_id,
                improvementReportPath=str(report_path.relative_to(self.repo_root)),
                improvementReportWarning=None,
            )
            return None
        except Exception as exc:  # noqa: BLE001
            warning = (
                "改善候補reportを保存できませんでした。"
                f"元の処理結果は維持します: {exc}"
            )
            self.store.update(
                qualification,
                run_id,
                improvementReportPath=None,
                improvementReportWarning=warning,
            )
            emit(warning)
            return warning

    def _persist_queue_pause(
        self,
        qualification: str,
        run_id: str,
        pause: QuestionQueuePaused,
    ) -> None:
        self.store.update(
            qualification,
            run_id,
            status="interrupted",
            queueStatus="partial",
            pauseKind=pause.pause_kind,
            retrySafe=True,
            retryUnsafeReason=None,
            error=str(pause),
        )

    def _normalize_run_law_audit_sidecars(
        self,
        qualification: str,
        run_id: str,
        parent: Mapping[str, Any],
        phases: Iterable[Mapping[str, Any]],
        emit: Callable[[str], None],
    ) -> dict[str, Any] | None:
        if not any(
            str(phase.get("id") or "") == "law_audit" for phase in phases
        ):
            return None
        groups: dict[str, list[Mapping[str, Any]]] = {}
        for list_group_id in parent.get("targetGroupIds") or []:
            group_id = str(list_group_id or "").strip()
            if not group_id:
                continue
            group = self.workflow.inventory.group(qualification, group_id)
            groups[group_id] = [
                value
                for value in group.get("questions") or []
                if isinstance(value, Mapping)
            ]
        receipt = normalize_law_audit_sidecars(
            self.repo_root,
            qualification,
            groups,
        )
        invalidate = getattr(self.workflow.inventory, "invalidate", None)
        if callable(invalidate):
            for list_group_id in groups:
                invalidate(qualification, list_group_id)
        self.store.update(
            qualification,
            run_id,
            lawAuditSidecarNormalization=receipt,
        )
        emit(
            "法令監査sidecarを現行source identityへ正規化しました: "
            f"{receipt['changedRowCount']}行 / {receipt['changedFileCount']}file。"
            "未完成metadataは再整備へ引き継ぎました: "
            f"{receipt['deferredMetadataRowCount']}行"
        )
        return receipt

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
            pauseKind=None,
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
        try:
            self._check_source_immutability(
                emit,
                source_files=[str(value) for value in parent.get("sourceFiles") or []],
            )
            phases = [
                dict(value)
                for value in parent.get("phaseExecutions") or []
                if isinstance(value, Mapping)
            ]
            self._normalize_run_law_audit_sidecars(
                qualification,
                run_id,
                parent,
                phases,
                emit,
            )
            parent = self.store.get(qualification, run_id)
            queue_order = str(parent.get("queueOrder") or "")
            if queue_order != "question_turn":
                raise QualificationRunError(
                    "一問queue契約が不正です。対象範囲から新規開始してください。"
                )
            with _ParentRunHeartbeatTicker(
                self.store,
                qualification,
                run_id,
            ):
                queue_result = self._run_question_queue(
                    qualification,
                    run_id,
                    phases,
                    emit,
                )
            child_run_ids.extend(queue_result["childRunIds"])
            work_version_receipts.extend(
                queue_result["workVersionReceipts"]
            )
            confirmed_group_ids.update(
                queue_result["confirmedGroupIds"]
            )
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
            improvement_report_warning = self._record_improvement_report(
                qualification,
                run_id,
                emit,
            )
            parent = self.store.get(qualification, run_id)
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
            ]
            if has_confirmed_work and not confirmed_group_ids:
                sync_group_ids = [
                    str(value)
                    for value in parent.get("targetGroupIds") or []
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
            warning = bool(improvement_report_warning) or partial or artifact_sync[
                "status"
            ] not in {"succeeded", "current", "not_required"}
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
                error=(result_summary if partial else None),
            )
            return {
                "qualification": qualification,
                "runId": run_id,
                "childRunIds": child_run_ids,
                "queueStatus": queue_status,
                "questionExecutionSummary": execution_summary,
                "artifactSync": artifact_sync,
                "warning": warning,
                "message": " ".join(
                    value
                    for value in (
                        result_summary,
                        improvement_report_warning,
                        str(artifact_sync["message"]),
                    )
                    if value
                ),
            }
        except QuestionQueuePaused as exc:
            result = {
                "status": "failed",
                "summary": str(exc),
                "commands": [],
                "changedFiles": [],
            }
            self.store.write_result(qualification, run_id, result)
            current = self.store.get(qualification, run_id)
            execution_summary = queue_summary(
                current.get("questionExecutions") or []
            )
            self._record_improvement_report(qualification, run_id, emit)
            self._persist_queue_pause(qualification, run_id, exc)
            self.store.update(
                qualification,
                run_id,
                currentPhaseId=None,
                receiptValidated=False,
                questionExecutionSummary=execution_summary,
                blockedQuestionCount=execution_summary["blockedQuestionCount"],
                blockedWorkItemCount=execution_summary["blockedWorkItemCount"],
                validatedQuestionCount=execution_summary["validatedQuestionCount"],
                validatedWorkItemCount=execution_summary["validatedWorkItemCount"],
                result=result,
                error=str(exc),
            )
            raise
        except Exception as exc:  # noqa: BLE001
            result = {
                "status": "failed",
                "summary": str(exc),
                "commands": [],
                "changedFiles": [],
            }
            self.store.write_result(qualification, run_id, result)
            current = self.store.get(qualification, run_id)
            execution_summary = queue_summary(current.get("questionExecutions") or [])
            self._record_improvement_report(qualification, run_id, emit)
            self.store.update(
                qualification,
                run_id,
                status="failed",
                queueStatus=(
                    "partial" if execution_summary["validatedWorkItemCount"] else "failed"
                ),
                currentPhaseId=None,
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

    def _run_structured_question(
        self,
        qualification: str,
        run_id: str,
        prompt: str,
        emit: Callable[[str], None],
        *,
        batch_plan: Mapping[str, Any],
        stage_id: str,
        pipeline_stop: threading.Event,
        prepared_records: Mapping[str, Mapping[str, Any]] | None = None,
        prepared_source_records: Mapping[str, Mapping[str, Any]] | None = None,
        prepared_targets: Mapping[str, tuple[CandidateTarget, ...]] | None = None,
        model: str = QUESTION_MAINTENANCE_MODEL,
        reasoning_effort: str = TURN_REASONING_EFFORT,
        input_fingerprint_value: str,
        projected_input_hash: str,
        model_queue_origin_monotonic: float | None = None,
        on_model_turn_event: (
            Callable[[Mapping[str, Any]], float | None] | None
        ) = None,
        on_patch_lock_acquired: (
            Callable[[float, tuple[str, ...]], None] | None
        ) = None,
        on_patch_lock_released: Callable[[], None] | None = None,
        prepare_only: bool = False,
        apply_prepared: bool = False,
    ) -> dict[str, Any]:
        """Generate a read-only candidate, then validate and apply it by tool."""

        if prepare_only and apply_prepared:
            raise QualificationRunError(
                "候補生成と保存済み候補のpatch反映を同時指定できません。"
            )
        if self.app_server is None:
            raise QualificationRunError("Codex App Serverが設定されていません。")
        if not self.store.is_question_attempt(run_id):
            raise QualificationRunError(
                "構造化候補pipelineは現行の一問attemptだけを処理します。"
            )
        existing_child = self.store.get(qualification, run_id)
        child = self.store.update(
            qualification,
            run_id,
            status="running",
            executionPhase=(
                "structured_candidate_patch_apply"
                if apply_prepared
                else "structured_candidate_generation"
            ),
            startedAt=existing_child.get("startedAt") or _now(),
            heartbeatAt=_now(),
            error=None,
        )
        speed_mode = normalize_speed_mode(
            child.get("speedMode")
            or batch_plan.get("speedMode")
            or STANDARD_SPEED_MODE
        )
        candidate_turn_runtime: dict[str, Any] = {}
        candidate_turn_runtime_lock = threading.Lock()

        def model_turn_callback(
            work_type: str,
            *,
            capture_candidate: bool = False,
        ) -> Callable[[Mapping[str, Any]], None] | None:
            if not callable(on_model_turn_event):
                return None

            def observe(event: Mapping[str, Any]) -> None:
                payload = {
                    **dict(event),
                    "workType": work_type,
                }
                on_model_turn_event(payload)
                if (
                    capture_candidate
                    and str(payload.get("event") or "") == "started"
                ):
                    with candidate_turn_runtime_lock:
                        observed = payload.get("observedMonotonic")
                        candidate_turn_runtime["queueWaitSeconds"] = (
                            round(
                                max(
                                    0.0,
                                    float(observed)
                                    - model_queue_origin_monotonic,
                                ),
                                6,
                            )
                            if isinstance(observed, (int, float))
                            and isinstance(
                                model_queue_origin_monotonic,
                                (int, float),
                            )
                            else None
                        )

            return observe

        raw_targets = [
            dict(value)
            for value in batch_plan.get("progressTargets") or []
            if isinstance(value, Mapping)
        ]
        question_ids = [
            str(value.get("id") or value.get("uiQuestionId") or "")
            for value in raw_targets
        ]
        if (
            not question_ids
            or len(question_ids) > DEFAULT_MAX_QUESTIONS_PER_TURN
            or len(set(question_ids)) != len(question_ids)
        ):
            raise QualificationRunError("構造化候補batchの問題数が不正です。")
        parent_run_id = str(
            child.get("parentRunId") or batch_plan.get("parentRunId") or ""
        )
        if apply_prepared:
            if len(raw_targets) != 1 or not parent_run_id:
                raise QualificationRunError(
                    "保存済み問題別候補の親run又は対象問題が不正です。"
                )
            _validated_projected_input_path(
                self.repo_root,
                self.store.run_directory(qualification, parent_run_id),
                raw_targets[0],
                projected_input_hash,
            )
        batch_work_item_keys = [
            str(value.get("workItemKey") or value.get("id") or "")
            for value in raw_targets
        ]
        batch_list_group_ids = [
            str(value)
            for value in (
                child.get("targetGroupIds")
                or batch_plan.get("targetGroupIds")
                or batch_plan.get("scopeListGroupIds")
                or []
            )
            if value
        ]
        if prepared_records is None or prepared_targets is None:
            records_by_question, targets_by_question = _structured_candidate_inputs(
                self.repo_root,
                stage_id,
                batch_plan,
            )
        else:
            records_by_question = dict(prepared_records)
            targets_by_question = dict(prepared_targets)
        raw_target_by_question = {
            str(value.get("id") or value.get("uiQuestionId") or ""): value
            for value in raw_targets
        }
        source_answer_evidence_by_question = {
            question_id: evidence
            for question_id, source_record in (
                prepared_source_records or {}
            ).items()
            if question_id in raw_target_by_question
            and question_id in records_by_question
            for evidence in [
                _trusted_source_answer_evidence(
                    source_record,
                    raw_target_by_question[question_id],
                    records_by_question[question_id],
                    _projected_question_issue_evidence(
                        self.repo_root,
                        raw_target_by_question[question_id],
                    ),
                )
            ]
            if stage_id == "correct_choice" and evidence is not None
        }

        def heartbeat() -> None:
            heartbeat_at = _now()
            self.store.update(qualification, run_id, heartbeatAt=heartbeat_at)
            callback = getattr(emit, "heartbeat", None)
            if callable(callback):
                callback()

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
        aggregate_consensus: dict[str, dict[str, Any]] = {}
        aggregate_review_pairs: dict[str, list[dict[str, Any]]] = {}
        aggregate_review_adjudications: dict[str, dict[str, Any]] = {}
        aggregate_source_records: dict[str, Mapping[str, Any]] = {}
        prepared_execution_metadata: dict[str, Any] = {}
        prepared_content: dict[str, Any] | None = None
        committed_files: set[str] = set()
        try:
            if apply_prepared:
                envelope = self.store.load_prepared_candidate(
                    qualification,
                    run_id,
                    input_fingerprint_value=input_fingerprint_value,
                    projected_input_hash=projected_input_hash,
                )
                prepared_content = copy.deepcopy(dict(envelope["content"]))
            aggregate_review_enabled = stage_id == "question_type"
            checkpoint_mismatches: set[str] = set()
            if aggregate_review_enabled and prepared_content is None:
                aggregate_source_records = _aggregate_review_source_records(
                    self.repo_root,
                    qualification,
                    batch_plan,
                    raw_targets,
                    records_by_question,
                )
                review_policy = dict(
                    (batch_plan.get("agentPolicy") or {}).get("independent_review") or {}
                )
                review_model = str(review_policy.get("model") or model)
                review_effort = str(
                    review_policy.get("reasoningEffort") or reasoning_effort
                )
                parent_run_id = str(batch_plan.get("parentRunId") or "")
                reused_question_ids: list[str] = []
                signatures: dict[str, dict[str, Any]] = {}
                candidate_sets_by_question: dict[str, dict[str, Any]] = {}
                reviews_by_question: dict[str, list[dict[str, Any]]] = {}
                executions_by_question: dict[str, list[dict[str, Any]]] = {}
                stored_checkpoints = (
                    self.store.aggregate_review_checkpoints(
                        qualification,
                        parent_run_id,
                        question_ids,
                    )
                    if parent_run_id
                    else {question_id: None for question_id in question_ids}
                )
                for question_id in question_ids:
                    record = aggregate_source_records[question_id]
                    source_text = str(record.get("questionBodyText") or "")
                    candidate_set = generate_statement_candidates(source_text)
                    candidate_sets_by_question[question_id] = candidate_set
                    signature = {
                        "sourceHash": source_text_hash(source_text),
                        "candidateSetHash": candidate_set_hash(candidate_set),
                        "stableParentIdentity": stable_parent_identity(record),
                        "model": review_model,
                        "reasoningEffort": review_effort,
                        "promptContractVersion": (
                            AGGREGATE_REVIEW_PROMPT_CONTRACT_VERSION
                        ),
                    }
                    signatures[question_id] = signature
                    checkpoint = stored_checkpoints[question_id]
                    if checkpoint is None:
                        reviews_by_question[question_id] = []
                        executions_by_question[question_id] = []
                        continue
                    checkpoint_signature = {
                        field: checkpoint.get(field)
                        for field in signature
                    }
                    try:
                        stored_slots = self.store._aggregate_checkpoint_slots(
                            checkpoint
                        )
                    except QualificationRunError:
                        checkpoint_mismatches.add(question_id)
                        reviews_by_question[question_id] = []
                        executions_by_question[question_id] = []
                        continue
                    if any(
                        value.get("status") != "resolved"
                        for value in stored_slots.values()
                    ):
                        checkpoint_mismatches.add(question_id)
                        reviews_by_question[question_id] = []
                        executions_by_question[question_id] = []
                        continue
                    ordered_slots = [
                        stored_slots[key]
                        for key in ("1", "2")
                        if key in stored_slots
                    ]
                    stored_reviews = [
                        copy.deepcopy(value.get("review"))
                        for value in ordered_slots
                    ]
                    stored_executions = [
                        copy.deepcopy(value.get("execution"))
                        for value in ordered_slots
                    ]
                    stored_consensus_matches = True
                    if isinstance(stored_reviews, list) and len(stored_reviews) == 2:
                        try:
                            stored_consensus_matches = (
                                checkpoint.get("consensus")
                                == reconcile_reviews(source_text, stored_reviews)
                            )
                        except ValueError:
                            stored_consensus_matches = False
                    if (
                        checkpoint_signature != signature
                        or not isinstance(stored_reviews, list)
                        or not isinstance(stored_executions, list)
                        or len(stored_reviews) != len(stored_executions)
                        or len(stored_reviews) > 2
                        or not stored_consensus_matches
                        or any(
                            not isinstance(value, Mapping)
                            for value in stored_executions
                        )
                        or any(
                            str(value.get("model") or "") != review_model
                            or str(value.get("reasoningEffort") or "")
                            != review_effort
                            for value in stored_executions
                            if isinstance(value, Mapping)
                        )
                    ):
                        checkpoint_mismatches.add(question_id)
                        reviews_by_question[question_id] = []
                        executions_by_question[question_id] = []
                        continue
                    reviews_by_question[question_id] = copy.deepcopy(stored_reviews)
                    executions_by_question[question_id] = copy.deepcopy(
                        stored_executions
                    )
                    if len(stored_reviews) == 2:
                        reused_question_ids.append(question_id)

                for review_number in (1, 2):
                    reservation_ids = [
                        question_id
                        for question_id in question_ids
                        if question_id not in checkpoint_mismatches
                        and len(reviews_by_question[question_id]) < review_number
                    ]
                    pending_ids: list[str] = []
                    reservations = (
                        self.store.reserve_aggregate_review_slots(
                            qualification,
                            parent_run_id,
                            [
                                (
                                    question_id,
                                    signatures[question_id],
                                    review_number,
                                )
                                for question_id in reservation_ids
                            ],
                        )
                        if parent_run_id
                        else {}
                    )
                    for question_id in reservation_ids:
                        if not parent_run_id:
                            checkpoint_mismatches.add(question_id)
                            continue
                        reservation = reservations[question_id]
                        reservation_status = str(reservation.get("status") or "")
                        if reservation_status == "reserved":
                            pending_ids.append(question_id)
                            continue
                        if reservation_status == "resolved":
                            checkpoint = reservation.get("checkpoint") or {}
                            slots = self.store._aggregate_checkpoint_slots(checkpoint)
                            ordered = [
                                slots[key]
                                for key in ("1", "2")
                                if key in slots
                                and slots[key].get("status") == "resolved"
                            ]
                            reviews_by_question[question_id] = [
                                copy.deepcopy(value["review"])
                                for value in ordered
                            ]
                            executions_by_question[question_id] = [
                                copy.deepcopy(value["execution"])
                                for value in ordered
                            ]
                            continue
                        checkpoint_mismatches.add(question_id)
                    if not pending_ids:
                        continue
                    pending_targets = [
                        target
                        for target in raw_targets
                        if str(target.get("id") or target.get("uiQuestionId") or "")
                        in pending_ids
                    ]
                    review_prompt = _aggregate_answer_review_prompt(
                        pending_targets,
                        aggregate_source_records,
                        candidate_sets_by_question,
                    )
                    started_threads: list[str] = []

                    def on_review_thread_started(
                        thread_id: str,
                        session_id: str,
                        *,
                        number: int = review_number,
                    ) -> None:
                        started_threads.append(thread_id)
                        self.store.update(
                            qualification,
                            run_id,
                            **{
                                f"aggregateReviewThreadId{number}": thread_id,
                                f"aggregateReviewSessionId{number}": session_id,
                            },
                        )

                    def on_review_turn_started(
                        thread_id: str,
                        turn_id: str,
                        *,
                        number: int = review_number,
                    ) -> None:
                        self.store.update(
                            qualification,
                            run_id,
                            **{
                                f"aggregateReviewThreadId{number}": thread_id,
                                f"aggregateReviewTurnId{number}": turn_id,
                            },
                        )

                    try:
                        review_result = self.app_server.run_turn(
                            review_prompt,
                            work_type=(
                                f"maintenance_{stage_id}_aggregate_review_"
                                f"{review_number}_candidate"
                            ),
                            sandbox="read-only",
                            emit=emit,
                            output_schema=aggregate_answer_review_schema(
                                pending_ids,
                                {
                                    question_id: [
                                        str(candidate.get("candidateId") or "")
                                        for candidate in candidate_sets_by_question[
                                            question_id
                                        ].get("candidates")
                                        or []
                                        if isinstance(candidate, Mapping)
                                        and candidate.get("candidateId")
                                    ]
                                    for question_id in pending_ids
                                },
                                {
                                    question_id: str(
                                        signatures[question_id]["sourceHash"]
                                    )
                                    for question_id in pending_ids
                                },
                            ),
                            on_thread_started=on_review_thread_started,
                            on_turn_started=on_review_turn_started,
                            on_model_turn_event=model_turn_callback(
                                (
                                    f"maintenance_{stage_id}_aggregate_review_"
                                    f"{review_number}_candidate"
                                )
                            ),
                            heartbeat=heartbeat,
                            cwd=self.repo_root,
                            model=review_model,
                            reasoning_effort=review_effort,
                            speed_mode=speed_mode,
                            turn_group=qualification,
                            monitor_context=self._monitor_context(
                                qualification,
                                run_id,
                                parent_run_id=parent_run_id,
                                question_ids=pending_ids,
                                work_item_keys=batch_work_item_keys,
                                list_group_ids=batch_list_group_ids,
                                stage_id=stage_id,
                                work_type=(
                                    f"maintenance_{stage_id}_aggregate_review_"
                                    f"{review_number}_candidate"
                                ),
                                phase="independent_review",
                            ),
                        )
                    except CodexTerminalTurnFailedError:
                        try:
                            self.store.cancel_terminal_failed_aggregate_review_slots(
                                qualification,
                                parent_run_id,
                                [
                                    (
                                        question_id,
                                        signatures[question_id],
                                        review_number,
                                        reservations[question_id]["slot"],
                                    )
                                    for question_id in pending_ids
                                ],
                            )
                        except QualificationRunError as cancellation_error:
                            deterministic_error = QualificationRunError(
                                "終端failedのaggregate review予約を"
                                "原子的に取消できません。"
                            )
                            deterministic_error.add_note(str(cancellation_error))
                            raise deterministic_error from None
                        raise
                    except Exception as exc:  # noqa: BLE001
                        if not started_threads and _external_provider_failure(exc):
                            try:
                                self.store.cancel_unstarted_aggregate_review_slots(
                                    qualification,
                                    parent_run_id,
                                    [
                                        (
                                            question_id,
                                            signatures[question_id],
                                            review_number,
                                            reservations[question_id]["slot"],
                                        )
                                        for question_id in pending_ids
                                    ],
                                )
                            except QualificationRunError as cancellation_error:
                                deterministic_error = QualificationRunError(
                                    "aggregate review予約を原子的に取消できません。"
                                )
                                deterministic_error.add_note(str(cancellation_error))
                                raise deterministic_error from None
                        raise
                    if review_result.changed_files:
                        raise QualificationRunError(
                            "read-only集約回答レビューでfile変更通知を検出しました。"
                        )
                    parsed_batch = parse_aggregate_answer_reviews(
                        review_result.final_message,
                        pending_ids,
                        {
                            question_id: [
                                str(candidate.get("candidateId") or "")
                                for candidate in candidate_sets_by_question[
                                    question_id
                                ].get("candidates")
                                or []
                                if isinstance(candidate, Mapping)
                                and candidate.get("candidateId")
                            ]
                            for question_id in pending_ids
                        },
                    )
                    if len(started_threads) != 1:
                        raise QualificationRunError(
                            "集約回答レビューthreadを一意に確認できませんでした。"
                        )
                    execution = {
                            "reviewNumber": review_number,
                            "threadId": review_result.thread_id,
                            "sessionId": review_result.session_id,
                            "turnId": review_result.turn_id,
                            "model": review_result.model,
                            "reasoningEffort": review_result.reasoning_effort,
                    }
                    resolved_checkpoints = self.store.resolve_aggregate_review_slots(
                        qualification,
                        parent_run_id,
                        [
                            (
                                question_id,
                                signatures[question_id],
                                review_number,
                                parsed_batch[question_id],
                                execution,
                            )
                            for question_id in pending_ids
                        ],
                    )
                    for question_id in pending_ids:
                        checkpoint = resolved_checkpoints[question_id]
                        slots = self.store._aggregate_checkpoint_slots(checkpoint)
                        ordered = [
                            slots[key]
                            for key in ("1", "2")
                            if key in slots
                            and slots[key].get("status") == "resolved"
                        ]
                        reviews_by_question[question_id] = [
                            copy.deepcopy(value["review"])
                            for value in ordered
                        ]
                        executions_by_question[question_id] = [
                            copy.deepcopy(value["execution"])
                            for value in ordered
                        ]
                    self.store.update(
                        qualification,
                        run_id,
                        aggregateReviewExecutions=copy.deepcopy(
                            [
                                value
                                for question_id in question_ids
                                for value in executions_by_question[question_id]
                            ]
                        ),
                    )
                consensus_values: list[
                    tuple[str, Mapping[str, Any], Mapping[str, Any]]
                ] = []
                for question_id in question_ids:
                    source_text = str(
                        aggregate_source_records[question_id].get("questionBodyText")
                        or ""
                    )
                    aggregate_review_pairs[question_id] = copy.deepcopy(
                        reviews_by_question[question_id]
                    )
                    thread_ids = [
                        str(value.get("threadId") or "")
                        for value in executions_by_question[question_id]
                    ]
                    if (
                        question_id in checkpoint_mismatches
                        or len(aggregate_review_pairs[question_id]) != 2
                        or len(thread_ids) != 2
                        or len(set(thread_ids)) != 2
                    ):
                        aggregate_consensus[question_id] = {
                            "schemaVersion": "aggregate-answer-decomposition/v1",
                            "sourceHash": source_text_hash(source_text),
                            "classification": "hold",
                            "spans": [],
                            "decision": "hold",
                            "issueCodes": ["invalid_review"],
                        }
                        continue
                    try:
                        aggregate_consensus[question_id] = reconcile_reviews(
                            source_text,
                            aggregate_review_pairs[question_id],
                            candidate_sets_by_question[question_id],
                        )
                    except ValueError:
                        aggregate_consensus[question_id] = {
                            "schemaVersion": "aggregate-answer-decomposition/v1",
                            "sourceHash": source_text_hash(source_text),
                            "classification": "hold",
                            "spans": [],
                            "decision": "hold",
                            "issueCodes": ["invalid_review"],
                        }
                    consensus_values.append(
                        (
                            question_id,
                            signatures[question_id],
                            aggregate_consensus[question_id],
                        )
                    )
                if consensus_values:
                    self.store.store_aggregate_review_consensuses(
                        qualification,
                        parent_run_id,
                        consensus_values,
                    )
                adjudication_ids = [
                    question_id
                    for question_id in question_ids
                    if aggregate_consensus.get(question_id, {}).get("issueCodes")
                    == ["review_disagreement"]
                ]
                if adjudication_ids:
                    adjudication_targets = [
                        target
                        for target in raw_targets
                        if str(
                            target.get("id") or target.get("uiQuestionId") or ""
                        )
                        in adjudication_ids
                    ]
                    adjudication_prompt = _aggregate_answer_adjudication_prompt(
                        adjudication_targets,
                        aggregate_source_records,
                        candidate_sets_by_question,
                        {
                            question_id: aggregate_review_pairs[question_id]
                            for question_id in adjudication_ids
                        },
                    )
                    adjudication_threads: list[str] = []

                    def on_adjudication_thread_started(
                        thread_id: str,
                        session_id: str,
                    ) -> None:
                        adjudication_threads.append(thread_id)
                        self.store.update(
                            qualification,
                            run_id,
                            aggregateReviewAdjudicationThreadId=thread_id,
                            aggregateReviewAdjudicationSessionId=session_id,
                        )

                    def on_adjudication_turn_started(
                        thread_id: str,
                        turn_id: str,
                    ) -> None:
                        self.store.update(
                            qualification,
                            run_id,
                            aggregateReviewAdjudicationThreadId=thread_id,
                            aggregateReviewAdjudicationTurnId=turn_id,
                        )

                    adjudication_result = self.app_server.run_turn(
                        adjudication_prompt,
                        work_type=(
                            f"maintenance_{stage_id}_aggregate_review_"
                            "3_adjudication"
                        ),
                        sandbox="read-only",
                        emit=emit,
                        output_schema=aggregate_answer_review_schema(
                            adjudication_ids,
                            {
                                question_id: [
                                    str(candidate.get("candidateId") or "")
                                    for candidate in candidate_sets_by_question[
                                        question_id
                                    ].get("candidates")
                                    or []
                                    if isinstance(candidate, Mapping)
                                    and candidate.get("candidateId")
                                ]
                                for question_id in adjudication_ids
                            },
                            {
                                question_id: str(
                                    signatures[question_id]["sourceHash"]
                                )
                                for question_id in adjudication_ids
                            },
                        ),
                        on_thread_started=on_adjudication_thread_started,
                        on_turn_started=on_adjudication_turn_started,
                        on_model_turn_event=model_turn_callback(
                            (
                                f"maintenance_{stage_id}_aggregate_review_"
                                "3_adjudication"
                            )
                        ),
                        heartbeat=heartbeat,
                        cwd=self.repo_root,
                        model=review_model,
                        reasoning_effort=review_effort,
                        speed_mode=speed_mode,
                        turn_group=qualification,
                        monitor_context=self._monitor_context(
                            qualification,
                            run_id,
                            parent_run_id=parent_run_id,
                            question_ids=adjudication_ids,
                            work_item_keys=batch_work_item_keys,
                            list_group_ids=batch_list_group_ids,
                            stage_id=stage_id,
                            work_type=(
                                f"maintenance_{stage_id}_aggregate_review_"
                                "3_adjudication"
                            ),
                            phase="review_adjudication",
                        ),
                    )
                    if adjudication_result.changed_files:
                        raise QualificationRunError(
                            "read-only集約回答裁定でfile変更通知を検出しました。"
                        )
                    if len(adjudication_threads) != 1:
                        raise QualificationRunError(
                            "集約回答裁定threadを一意に確認できませんでした。"
                        )
                    parsed_adjudications = parse_aggregate_answer_reviews(
                        adjudication_result.final_message,
                        adjudication_ids,
                        {
                            question_id: [
                                str(candidate.get("candidateId") or "")
                                for candidate in candidate_sets_by_question[
                                    question_id
                                ].get("candidates")
                                or []
                                if isinstance(candidate, Mapping)
                                and candidate.get("candidateId")
                            ]
                            for question_id in adjudication_ids
                        },
                    )
                    for question_id in adjudication_ids:
                        adjudication = parsed_adjudications[question_id]
                        execution = {
                            "reviewNumber": 3,
                            "role": "adjudication",
                            "threadId": adjudication_result.thread_id,
                            "sessionId": adjudication_result.session_id,
                            "turnId": adjudication_result.turn_id,
                            "model": adjudication_result.model,
                            "reasoningEffort": (
                                adjudication_result.reasoning_effort
                            ),
                            "promptContractVersion": (
                                AGGREGATE_ADJUDICATION_PROMPT_CONTRACT_VERSION
                            ),
                        }
                        prior_thread_ids = {
                            str(value.get("threadId") or "")
                            for value in executions_by_question[question_id]
                        }
                        if (
                            not execution["threadId"]
                            or execution["threadId"] in prior_thread_ids
                        ):
                            source_text = str(
                                aggregate_source_records[question_id].get(
                                    "questionBodyText"
                                )
                                or ""
                            )
                            aggregate_consensus[question_id] = {
                                "schemaVersion": (
                                    "aggregate-answer-decomposition/v1"
                                ),
                                "sourceHash": source_text_hash(source_text),
                                "classification": "hold",
                                "spans": [],
                                "decision": "hold",
                                "issueCodes": ["invalid_review"],
                            }
                        else:
                            aggregate_review_pairs[question_id] = [
                                copy.deepcopy(adjudication),
                                copy.deepcopy(adjudication),
                            ]
                            aggregate_consensus[question_id] = reconcile_reviews(
                                str(
                                    aggregate_source_records[question_id].get(
                                        "questionBodyText"
                                    )
                                    or ""
                                ),
                                aggregate_review_pairs[question_id],
                                candidate_sets_by_question[question_id],
                            )
                        aggregate_review_adjudications[question_id] = {
                            "review": copy.deepcopy(adjudication),
                            "execution": copy.deepcopy(execution),
                        }
                review_receipts = [
                    execution
                    for question_id in question_ids
                    for execution in executions_by_question[question_id]
                ]
                review_receipts.extend(
                    value["execution"]
                    for value in aggregate_review_adjudications.values()
                )
                all_review_receipts = list(
                    {
                        str(value.get("threadId") or ""): value
                        for value in review_receipts
                        if value.get("threadId")
                    }.values()
                )
                all_thread_ids = list(
                    dict.fromkeys(
                        str(value.get("threadId") or "")
                        for value in all_review_receipts
                        if value.get("threadId")
                    )
                )
                self.store.update(
                    qualification,
                    run_id,
                    executionPhase="server_aggregate_review_reconciliation",
                    aggregateReviewThreadIds=all_thread_ids,
                    aggregateReviewExecutions=all_review_receipts,
                    aggregateReviewReusedQuestionIds=reused_question_ids,
                    aggregateReviewReusedCount=len(reused_question_ids),
                    aggregateReviewReused=(
                        len(reused_question_ids) == len(question_ids)
                        and not aggregate_review_adjudications
                    ),
                    aggregateReviewAdjudications=copy.deepcopy(
                        aggregate_review_adjudications
                    ),
                    aggregateReviewAdjudicatedQuestionIds=sorted(
                        aggregate_review_adjudications
                    ),
                    aggregateReviewAdjudicatedCount=len(
                        aggregate_review_adjudications
                    ),
                    aggregateReviewPromptContractVersion=(
                        AGGREGATE_REVIEW_PROMPT_CONTRACT_VERSION
                    ),
                    aggregateReviewAdjudicationPromptContractVersion=(
                        AGGREGATE_ADJUDICATION_PROMPT_CONTRACT_VERSION
                    ),
                )
            if prepared_content is not None:
                raw_consensus = prepared_content.get("aggregateConsensus")
                raw_pairs = prepared_content.get("aggregateReviewPairs")
                raw_adjudications = prepared_content.get(
                    "aggregateReviewAdjudications",
                    {},
                )
                raw_source_records = prepared_content.get(
                    "aggregateSourceRecords"
                )
                raw_prepared_source_records = prepared_content.get(
                    "preparedSourceRecords"
                )
                raw_invalid_ids = prepared_content.get("invalidQuestionIds")
                raw_execution = prepared_content.get("executionMetadata")
                candidate_payload = prepared_content.get("candidatePayload")
                if (
                    not isinstance(raw_consensus, Mapping)
                    or not isinstance(raw_pairs, Mapping)
                    or not isinstance(raw_adjudications, Mapping)
                    or not isinstance(raw_source_records, Mapping)
                    or not isinstance(raw_prepared_source_records, Mapping)
                    or not isinstance(raw_invalid_ids, list)
                    or not isinstance(raw_execution, Mapping)
                    or not isinstance(candidate_payload, Mapping)
                ):
                    raise QualificationRunError(
                        "保存済み問題別候補のcontent形式が不正です。"
                    )
                aggregate_consensus = {
                    str(key): copy.deepcopy(dict(value))
                    for key, value in raw_consensus.items()
                    if isinstance(value, Mapping)
                }
                aggregate_review_pairs = {
                    str(key): [
                        copy.deepcopy(dict(review))
                        for review in value
                        if isinstance(review, Mapping)
                    ]
                    for key, value in raw_pairs.items()
                    if isinstance(value, list)
                }
                aggregate_review_adjudications = {
                    str(key): copy.deepcopy(dict(value))
                    for key, value in raw_adjudications.items()
                    if isinstance(value, Mapping)
                }
                aggregate_source_records = {
                    str(key): copy.deepcopy(dict(value))
                    for key, value in raw_source_records.items()
                    if isinstance(value, Mapping)
                }
                prepared_source_records = {
                    str(key): copy.deepcopy(dict(value))
                    for key, value in raw_prepared_source_records.items()
                    if isinstance(value, Mapping)
                }
                invalid_question_ids = {
                    str(value) for value in raw_invalid_ids
                }
                if not invalid_question_ids.issubset(set(question_ids)):
                    raise QualificationRunError(
                        "保存済み問題別候補に対象外問題があります。"
                    )
                candidate_question_ids = [
                    question_id
                    for question_id in question_ids
                    if question_id not in invalid_question_ids
                ]
                candidates = parse_prepared_candidate_payload(
                    candidate_payload,
                    candidate_question_ids,
                    targets_by_question,
                )
                prepared_execution_metadata = copy.deepcopy(
                    dict(raw_execution)
                )
            else:
                invalid_question_ids = set(checkpoint_mismatches)
                candidate_question_ids = [
                    question_id
                    for question_id in question_ids
                    if question_id not in invalid_question_ids
                ]
                if candidate_question_ids:
                    candidate_prompt = _filter_structured_candidate_prompt(
                        prompt,
                        set(candidate_question_ids),
                    )
                    result = self.app_server.run_turn(
                        candidate_prompt,
                        work_type=f"maintenance_{stage_id}_candidate",
                        sandbox="read-only",
                        emit=emit,
                        output_schema=candidate_output_schema(
                            candidate_question_ids,
                            targets_by_question,
                        ),
                        on_thread_started=on_thread_started,
                        on_turn_started=on_turn_started,
                        on_model_turn_event=model_turn_callback(
                            f"maintenance_{stage_id}_candidate",
                            capture_candidate=True,
                        ),
                        heartbeat=heartbeat,
                        cwd=self.repo_root,
                        model=model,
                        reasoning_effort=reasoning_effort,
                        speed_mode=speed_mode,
                        turn_group=qualification,
                        monitor_context=self._monitor_context(
                            qualification,
                            run_id,
                            parent_run_id=parent_run_id,
                            question_ids=candidate_question_ids,
                            work_item_keys=batch_work_item_keys,
                            list_group_ids=batch_list_group_ids,
                            stage_id=stage_id,
                            work_type=f"maintenance_{stage_id}_candidate",
                            phase="structured_candidate_generation",
                        ),
                    )
                    if result.changed_files:
                        raise QualificationRunError(
                            "read-only候補生成でfile変更通知を検出しました。"
                        )
                    candidates = parse_model_candidate_v3(
                        result.final_message,
                        candidate_question_ids,
                        targets_by_question,
                    )
                    prepared_execution_metadata = {
                        "model": result.model,
                        "serviceTier": result.service_tier,
                        "reasoningEffort": result.reasoning_effort,
                        "turnCompletionMode": result.completion_mode,
                        "modelTurnStartedAt": getattr(
                            result, "model_turn_started_at", None
                        ),
                        "modelTurnFinishedAt": getattr(
                            result, "model_turn_finished_at", None
                        ),
                        "modelTurnDurationSeconds": getattr(
                            result, "model_turn_duration_seconds", None
                        ),
                        "appServerQueueWaitSeconds": getattr(
                            result, "model_turn_queue_wait_seconds", None
                        ),
                    }
                    child_runtime = self.store.get(qualification, run_id)
                    executor_wait = float(
                        child_runtime.get("modelExecutorQueueWaitSeconds") or 0.0
                    )
                    queue_wait = candidate_turn_runtime.get(
                        "queueWaitSeconds"
                    )
                    self.store.update(
                        qualification,
                        run_id,
                        modelTurnTelemetry={
                            **prepared_execution_metadata,
                            "executorQueueWaitSeconds": round(
                                executor_wait,
                                6,
                            ),
                            "queueWaitSeconds": round(
                                float(queue_wait),
                                6,
                            )
                            if isinstance(queue_wait, (int, float))
                            else None,
                        },
                    )
                else:
                    candidates = []
                envelope = _prepared_candidate_envelope(
                    question_id=question_ids[0],
                    stage_id=stage_id,
                    input_fingerprint_value=input_fingerprint_value,
                    projected_input_hash=projected_input_hash,
                    content={
                        "candidatePayload": _question_candidates_payload(
                            candidates
                        ),
                        "aggregateConsensus": copy.deepcopy(
                            aggregate_consensus
                        ),
                        "aggregateReviewPairs": copy.deepcopy(
                            aggregate_review_pairs
                        ),
                        "aggregateReviewAdjudications": copy.deepcopy(
                            aggregate_review_adjudications
                        ),
                        "aggregateSourceRecords": copy.deepcopy(
                            aggregate_source_records
                        ),
                        "preparedSourceRecords": copy.deepcopy(
                            dict(prepared_source_records or {})
                        ),
                        "invalidQuestionIds": sorted(
                            invalid_question_ids
                        ),
                        "executionMetadata": copy.deepcopy(
                            prepared_execution_metadata
                        ),
                    },
                )
                self.store.persist_prepared_candidate(
                    qualification,
                    run_id,
                    envelope,
                )
            if self.store.is_question_attempt(run_id):
                self.store.update_attempt_stage_status(
                    qualification,
                    run_id,
                    "prepared",
                )
            if prepare_only:
                return {
                    "qualification": qualification,
                    "runId": run_id,
                    "preparedCandidateHash": str(
                        self.store.get(qualification, run_id)
                        .get("preparedCandidate", {})
                        .get("contentHash")
                        or ""
                    ),
                    "child": self.store.get(qualification, run_id),
                }
            self.store.mark_patch_apply_started(
                qualification,
                run_id,
            )
            patch_lock_wait_seconds: float | None = None
            patch_lock_paths: tuple[str, ...] = ()

            def record_patch_lock_acquired(
                seconds: float,
                paths: tuple[str, ...],
            ) -> None:
                nonlocal patch_lock_wait_seconds
                nonlocal patch_lock_paths
                patch_lock_wait_seconds = max(0.0, float(seconds))
                patch_lock_paths = tuple(paths)
                if callable(on_patch_lock_acquired):
                    on_patch_lock_acquired(
                        patch_lock_wait_seconds,
                        patch_lock_paths,
                    )

            bindings = {
                str(value.get("id") or value.get("uiQuestionId") or ""):
                SourceIdentityBinding.from_mapping(value)
                for value in raw_targets
            }
            committed_results: list[dict[str, Any]] = []
            work_version_receipts: list[dict[str, Any]] = []
            run_dir = self.store.run_directory(qualification, run_id)

            def checkpoint_question(
                question_result: dict[str, Any],
                work_version_receipt: dict[str, Any] | None = None,
            ) -> None:
                next_results = [*committed_results, question_result]
                next_receipts = [*work_version_receipts]
                if work_version_receipt is not None:
                    next_receipts.append(work_version_receipt)
                self.store.update(
                    qualification,
                    run_id,
                    executionPhase="server_candidate_checkpoint",
                    activeCandidateQuestionId=None,
                    candidateTransactionOpen=False,
                    batchQuestionResults=copy.deepcopy(next_results),
                    workVersionReceipt={
                        "recordedCount": sum(
                            int(value.get("recordedCount") or 0)
                            for value in next_receipts
                        ),
                        "items": copy.deepcopy(next_receipts),
                    },
                    deltaUnknown=False,
                )
                committed_results[:] = next_results
                work_version_receipts[:] = next_receipts

            self.store.update(
                qualification,
                run_id,
                executionPhase="server_candidate_validation",
                activeCandidateQuestionId=None,
                candidateTransactionOpen=False,
                batchQuestionResults=[],
                workVersionReceipt={"recordedCount": 0, "items": []},
                deltaUnknown=False,
            )

            for question_id in question_ids:
                if question_id not in invalid_question_ids:
                    continue
                consensus = aggregate_consensus[question_id]
                checkpoint_question(
                    {
                        "questionId": question_id,
                        "status": "failed",
                        "summary": "集約回答レビューを保留しました。",
                        "holdEvidence": {
                            "classification": consensus["classification"],
                            "issueCodes": list(consensus["issueCodes"]),
                            "sourceHash": consensus["sourceHash"],
                        },
                        "aggregateAnswerReview": {
                            "classification": consensus["classification"],
                            "decision": consensus["decision"],
                            "sourceHash": consensus["sourceHash"],
                            "issueCodes": list(consensus["issueCodes"]),
                            "decomposition": copy.deepcopy(consensus),
                            "adjudicated": (
                                question_id
                                in aggregate_review_adjudications
                            ),
                        },
                        "commands": [
                            {
                                "command": (
                                    "aggregate review adjudication"
                                    if question_id
                                    in aggregate_review_adjudications
                                    else "dual aggregate review"
                                ),
                                "status": "fail",
                            }
                        ],
                        "changedFiles": [],
                    }
                )

            for candidate in candidates:
                patch_lock_wait_seconds = None
                patch_lock_paths = ()
                question_id = candidate.question_id
                self.store.update(
                    qualification,
                    run_id,
                    executionPhase="server_candidate_patch_apply",
                    activeCandidateQuestionId=question_id,
                    candidateTransactionOpen=False,
                )
                commands = [
                    {"command": "structured candidate schema", "status": "pass"}
                ]
                consensus = aggregate_consensus.get(question_id)
                aggregate_review_evidence = (
                    {
                        "classification": consensus["classification"],
                        "decision": consensus["decision"],
                        "sourceHash": consensus["sourceHash"],
                        "issueCodes": list(consensus["issueCodes"]),
                        "decomposition": copy.deepcopy(consensus),
                        "adjudicated": (
                            question_id in aggregate_review_adjudications
                        ),
                    }
                    if consensus is not None
                    else None
                )
                current_record = records_by_question[question_id]
                current_source_text = current_record.get("questionBodyText")
                current_is_aggregate_target = bool(
                    isinstance(current_source_text, str)
                    and is_approved_target(
                        current_record.get("aggregateAnswerDecomposition"),
                        current_source_text,
                    )
                )
                hold_cleanup_required = bool(
                    consensus is not None
                    and consensus["decision"] == "hold"
                    and current_is_aggregate_target
                )
                if consensus is not None:
                    commands.append(
                        {
                            "command": (
                                "aggregate review adjudication"
                                if question_id
                                in aggregate_review_adjudications
                                else "dual aggregate review"
                            ),
                            "status": "pass",
                        }
                    )
                    if (
                        consensus["decision"] == "hold"
                        and not hold_cleanup_required
                    ):
                        issue_codes = list(consensus["issueCodes"])
                        checkpoint_question(
                            {
                                "questionId": question_id,
                                "status": "failed",
                                "summary": "集約回答レビューを保留しました。",
                                "holdEvidence": {
                                    "classification": consensus["classification"],
                                    "issueCodes": issue_codes,
                                    "sourceHash": consensus["sourceHash"],
                                },
                                "aggregateAnswerReview": aggregate_review_evidence,
                                "commands": commands,
                                "changedFiles": [],
                            }
                        )
                        continue
                if not hold_cleanup_required and candidate.status == "blocked":
                    checkpoint_question(
                        {
                            "questionId": question_id,
                            "status": "failed",
                            "summary": candidate.summary,
                            "aggregateAnswerReview": aggregate_review_evidence,
                            "commands": commands,
                            "changedFiles": [],
                        }
                    )
                    continue
                content_errors = (
                    []
                    if hold_cleanup_required
                    else validate_candidate_content(
                        candidate,
                        targets_by_question[question_id],
                        records_by_question[question_id],
                        (
                            prepared_source_records.get(question_id)
                            if prepared_source_records is not None
                            else None
                        ),
                        source_answer_evidence_by_question.get(question_id),
                    )
                )
                if content_errors:
                    commands.append(
                        {"command": "question content", "status": "fail"}
                    )
                    checkpoint_question(
                        {
                            "questionId": question_id,
                            "status": "failed",
                            "summary": " / ".join(content_errors),
                            "aggregateAnswerReview": aggregate_review_evidence,
                            "commands": commands,
                            "changedFiles": [],
                        }
                    )
                    continue
                commands.append({"command": "question content", "status": "pass"})
                if hold_cleanup_required:
                    commands.append(
                        {
                            "command": "deactivate stale aggregate target",
                            "status": "pass",
                        }
                    )
                if pipeline_stop.is_set():
                    raise QualificationRunError(
                        "正本の安全性を確認できないため候補反映を停止しました。"
                    )
                question_plan = subset_question_plan(batch_plan, [question_id])
                question_plan.update(
                    runId=run_id,
                    stageId=stage_id,
                    stageIds=[stage_id],
                    parallelStrategy="question_turn",
                )
                if not self._phase_plan_policy_is_current(
                    qualification,
                    question_plan,
                    stage_id,
                ):
                    checkpoint_question(
                        {
                            "questionId": question_id,
                            "status": "failed",
                                "summary": "実行中に共通方針が更新されました。",
                                "aggregateAnswerReview": aggregate_review_evidence,
                            "commands": commands,
                            "changedFiles": [],
                            "policyChanged": True,
                        }
                    )
                    continue
                binding = bindings[question_id]
                target_by_id = {
                    value.target_id: value
                    for value in targets_by_question[question_id]
                }
                mutable_paths = [value.path for value in target_by_id.values()]
                workspace = IsolatedQuestionPatchWorkspace.create(
                    self.repo_root,
                    run_dir
                    / "candidate_workspaces"
                    / hashlib.sha256(question_id.encode("utf-8")).hexdigest()[:16],
                    qualification=qualification,
                    mutable_paths=mutable_paths,
                )
                committed_for_question: set[str] = set()
                rollback: Mapping[str, Any] | None = None
                baseline_captured = False
                canonical_write_started = False
                try:
                    scopes = question_plan.get("targetRecordScopes") or {}
                    effective_updates = (
                        [] if hold_cleanup_required else list(candidate.updates)
                    )
                    if (
                        consensus is not None
                        and (
                            (
                                consensus["decision"] == "approve"
                                and (
                                    consensus["classification"] == "target"
                                    or current_is_aggregate_target
                                )
                            )
                            or hold_cleanup_required
                        )
                        and not any(
                            target_by_id[update.target_id].role == "question_type"
                            for update in effective_updates
                        )
                    ):
                        question_type_target = next(
                            value
                            for value in targets_by_question[question_id]
                            if value.role == "question_type"
                        )
                        effective_updates.append(
                            CandidateUpdate(
                                target_id=question_type_target.target_id,
                                set_fields={},
                                unset_fields=(),
                            )
                        )
                    for update in effective_updates:
                        target = target_by_id[update.target_id]
                        aliases = {
                            str(alias)
                            for group in scopes.get(target.path, [])
                            for alias in group
                            if alias
                        }
                        base_record: Mapping[str, Any]
                        if target.role == "law_audit":
                            projected = records_by_question[question_id]
                            base_record = {
                                **binding.as_mapping(),
                                "schemaVersion": "law-revision-audit/v2",
                                "examYear": projected.get("examYear"),
                            }
                        else:
                            base_record = records_by_question[question_id]
                        server_set_fields = dict(update.set_fields)
                        server_unset_fields = set(update.unset_fields)
                        if (
                            consensus is not None
                            and consensus["classification"] == "target"
                            and target.role == "question_type"
                        ):
                            raw_selected_fields = (
                                question_plan.get("selectedFieldsByStage") or {}
                            )
                            selected_stage_fields = (
                                {
                                    str(value)
                                    for value in raw_selected_fields.get(stage_id) or []
                                    if value
                                }
                                if isinstance(raw_selected_fields, Mapping)
                                and stage_id in raw_selected_fields
                                else None
                            )
                            server_set_fields["isCalculationQuestion"] = (
                                _aggregate_calculation_flag(
                                    server_set_fields,
                                    records_by_question[question_id],
                                    selected_stage_fields,
                                )
                            )
                            server_set_fields.update(
                                materialize_decomposition(
                                    aggregate_source_records[question_id],
                                    aggregate_review_pairs[question_id],
                                )
                            )
                        elif (
                            consensus is not None
                            and (
                                (
                                    consensus["classification"] == "non_target"
                                    and consensus["decision"] == "approve"
                                )
                                or consensus["decision"] == "hold"
                            )
                            and current_is_aggregate_target
                            and target.role == "question_type"
                        ):
                            source_record = aggregate_source_records[question_id]
                            source_choices = source_record.get(
                                "_aggregateSourceChoiceTextList"
                            )
                            if not isinstance(source_choices, list):
                                raise QualificationRunError(
                                    "集約回答の解除に必要なsource choiceTextListを"
                                    "配列として確認できません。"
                                )
                            server_set_fields["choiceTextList"] = copy.deepcopy(
                                source_choices
                            )
                            source_keys = source_record.get(
                                "_aggregateSourceUniqueKeys"
                            )
                            if isinstance(source_keys, list):
                                server_set_fields["sourceUniqueKeys"] = copy.deepcopy(
                                    source_keys
                                )
                            else:
                                server_unset_fields.add("sourceUniqueKeys")
                            server_unset_fields.add(
                                "aggregateAnswerDecomposition"
                            )
                        if target.role == "law_audit":
                            policy_version = str(
                                (question_plan.get("policyVersions") or {}).get(
                                    stage_id
                                )
                                or "unknown"
                            )
                            server_set_fields = _server_law_audit_fields(
                                qualification=qualification,
                                list_group_id=_question_plan_list_group_id(
                                    question_plan
                                ),
                                run_id=run_id,
                                policy_version=policy_version,
                                projected=projected,
                                candidate_fields=server_set_fields,
                            )
                            server_set_fields["schemaVersion"] = (
                                "law-revision-audit/v2"
                            )
                        workspace.apply_record_update(
                            target.path,
                            binding=binding,
                            aliases=aliases,
                            set_fields=server_set_fields,
                            unset_fields=_candidate_unset_fields(
                                target,
                                server_set_fields,
                                tuple(server_unset_fields),
                            ),
                            base_record=base_record,
                        )
                    candidate_paths = set(workspace.changed_paths())
                    target_group_ids = tuple(
                        sorted(
                            {
                                str(value)
                                for value in (
                                    question_plan.get("targetGroupIds") or []
                                )
                                if str(value)
                            }
                        )
                    )
                    work_version_paths = tuple(
                        path.relative_to(self.repo_root)
                        for path in (
                            self.work_versions.transaction_paths_for_questions(
                                {
                                    **target,
                                    "qualification": qualification,
                                }
                                for target in (
                                    question_plan.get("progressTargets") or []
                                )
                                if isinstance(target, Mapping)
                            )
                        )
                    )
                    record_work_version = not hold_cleanup_required
                    self._check_source_immutability(
                        emit,
                        source_files=[
                            str(value)
                            for value in question_plan.get("sourceFiles") or []
                        ],
                    )
                    commands.append(
                        {"command": "00_source immutability", "status": "pass"}
                    )
                    if self.store.is_question_attempt(run_id):
                        self.store.update_attempt_stage_status(
                            qualification,
                            run_id,
                            "committing",
                        )
                    if not candidate_paths and not record_work_version:
                        commands.append(
                            {"command": "record scope", "status": "pass"}
                        )
                        checkpoint_question(
                            {
                                "questionId": question_id,
                                "status": "failed",
                                "summary": (
                                    "集約回答レビューを保留し、旧targetを解除しました。"
                                ),
                                "aggregateAnswerReview": aggregate_review_evidence,
                                "commands": commands,
                                "changedFiles": [],
                            }
                        )
                        continue

                    def rollback_before_canonical_unlock(
                        _exc: BaseException,
                    ) -> None:
                        nonlocal rollback
                        if not baseline_captured:
                            return
                        if canonical_write_started:
                            rollback = self.store.rollback_baseline(
                                qualification,
                                run_id,
                            )
                            return
                        observed_delta = self.store.baseline_delta(
                            qualification,
                            run_id,
                        )
                        rollback = self.store.close_unwritten_baseline(
                            qualification,
                            run_id,
                            observed_changed_files=observed_delta or [],
                        )

                    try:
                        with workspace.canonical_transaction(
                            sorted(candidate_paths),
                            lock_paths=(
                                work_version_paths
                                if record_work_version
                                else ()
                            ),
                            on_acquired=record_patch_lock_acquired,
                            before_release_on_error=(
                                rollback_before_canonical_unlock
                            ),
                            on_released=on_patch_lock_released,
                        ) as canonical_transaction:
                            transaction_roots = tuple(
                                dict.fromkeys(
                                    [
                                        *(
                                            self.repo_root / value
                                            for value in candidate_paths
                                        ),
                                        *(
                                            self.repo_root / value
                                            for value in work_version_paths
                                            if record_work_version
                                        ),
                                    ]
                                )
                            )
                            baseline_path = self.store.write_baseline(
                                qualification,
                                run_id,
                                transaction_roots,
                            )
                            baseline_captured = True
                            self.store.update(
                                qualification,
                                run_id,
                                candidateTransactionOpen=True,
                                canonicalWriteStarted=False,
                            )
                            baseline_payload = json.loads(
                                baseline_path.read_text(encoding="utf-8")
                            )
                            prepared_patch = None
                            if candidate_paths:
                                prepared_patch = (
                                    canonical_transaction.prepare(
                                        binding=binding,
                                        aliases_by_path=scopes,
                                    )
                                )
                                self._validate_record_scope(
                                    qualification,
                                    run_id,
                                    question_plan,
                                    {
                                        Path(value)
                                        for value in candidate_paths
                                    },
                                    validation_root=workspace.root,
                                    baseline_payload=baseline_payload,
                                    projected_records=records_by_question,
                                )
                            commands.append(
                                {"command": "record scope", "status": "pass"}
                            )
                            prewrite_delta = self.store.baseline_delta(
                                qualification,
                                run_id,
                            )
                            if prewrite_delta is None:
                                raise QualificationRunError(
                                    "正本書込み前にbaselineを再検証できません。"
                                )
                            if prewrite_delta:
                                raise QualificationRunError(
                                    "正本書込み前に対象fileが更新されました: "
                                    + ", ".join(prewrite_delta)
                                )
                            patch_write_required = bool(
                                prepared_patch is not None
                                and prepared_patch.changed_files
                            )
                            if patch_write_required or record_work_version:
                                canonical_write_started = True
                                self.store.update(
                                    qualification,
                                    run_id,
                                    canonicalWriteStarted=True,
                                )
                            if prepared_patch is not None:
                                committed_for_question.update(
                                    prepared_patch.commit()
                                )
                            committed_files.update(committed_for_question)
                            if candidate_paths:
                                commands.append(
                                    {
                                        "command": "atomic patch apply",
                                        "status": "pass",
                                    }
                                )
                            work_version_receipt = (
                                None
                                if not record_work_version
                                else self._record_work_versions(question_plan)
                            )
                            checkpoint_question(
                                {
                                    "questionId": question_id,
                                    "status": (
                                        "failed"
                                        if hold_cleanup_required
                                        else "succeeded"
                                    ),
                                    "summary": (
                                        "集約回答レビューを保留し、旧targetを解除しました。"
                                        if hold_cleanup_required
                                        else candidate.summary
                                    ),
                                    "aggregateAnswerReview": (
                                        aggregate_review_evidence
                                    ),
                                    "commands": commands,
                                    "changedFiles": sorted(committed_for_question),
                                    **(
                                        {"workVersionReceipt": work_version_receipt}
                                        if work_version_receipt is not None
                                        else {}
                                    ),
                                },
                                work_version_receipt,
                            )
                            self.store.discard_baseline_backups(
                                qualification,
                                run_id,
                            )
                    except CanonicalPatchCommitError as exc:
                        committed_for_question.update(exc.committed_files)
                        committed_files.update(exc.committed_files)
                        raise

                    inventory = getattr(self.workflow, "inventory", None)
                    invalidate = getattr(inventory, "invalidate", None)
                    if callable(invalidate) and candidate_paths:
                        for list_group_id in target_group_ids:
                            try:
                                invalidate(
                                    qualification,
                                    list_group_id,
                                )
                            except Exception:
                                # Inventory is a derived cache. Canonical patch
                                # and work-version success remain authoritative.
                                pass
                except CanonicalPatchCommitError as exc:
                    rollback_safe = bool(
                        isinstance(rollback, Mapping)
                        and rollback.get("status") == "succeeded"
                        and rollback.get("deltaUnknown") is not True
                        and not rollback.get("remainingChangedFiles")
                    )
                    if rollback_safe:
                        committed_files.difference_update(
                            committed_for_question
                        )
                        committed_for_question.clear()
                    else:
                        pipeline_stop.set()
                    raise QualificationRunError(
                        "検証済みpatchのatomic反映を完了できません: "
                        + ", ".join(exc.pending_files)
                        + (
                            "。開始前の内容へrollbackしました。"
                            if rollback_safe
                            else "。開始前の内容へ安全にrollbackできません。"
                        )
                    ) from exc
                except Exception as exc:  # noqa: BLE001
                    rollback_safe = (
                        not baseline_captured
                        or bool(
                            isinstance(rollback, Mapping)
                            and rollback.get("deltaUnknown") is not True
                            and not rollback.get("remainingChangedFiles")
                            and (
                                rollback.get("status") == "succeeded"
                                or (
                                    rollback.get("status") == "not_required"
                                    and not canonical_write_started
                                )
                            )
                        )
                    )
                    if rollback_safe:
                        committed_files.difference_update(
                            committed_for_question
                        )
                        committed_for_question.clear()
                    if canonical_write_started and not rollback_safe:
                        pipeline_stop.set()
                        raise QualificationRunError(
                            "一問transactionに失敗し、開始前の内容へ"
                            "安全にrollbackできません。全patch toolを停止しました。"
                        ) from exc
                    checkpoint_question(
                        {
                            "questionId": question_id,
                            "status": "failed",
                            "summary": str(exc),
                            "aggregateAnswerReview": aggregate_review_evidence,
                            "commands": [
                                *commands,
                                {
                                    # Stable machine code retained for existing
                                    # post-write validation failures.  A
                                    # pre-write contention remains retryable.
                                    "command": (
                                        "server commit"
                                        if canonical_write_started
                                        else "canonical prewrite validation"
                                    ),
                                    "status": "fail",
                                },
                            ],
                            "changedFiles": (
                                []
                                if rollback_safe
                                else sorted(committed_for_question)
                            ),
                        }
                    )
                finally:
                    workspace.cleanup()
                    if patch_lock_wait_seconds is not None:
                        try:
                            self.store.update(
                                qualification,
                                run_id,
                                patchToolLockWaitSeconds=round(
                                    patch_lock_wait_seconds,
                                    6,
                                ),
                                patchToolLockPaths=list(patch_lock_paths),
                            )
                        except Exception:
                            # Telemetry must not replace a transaction result
                            # or hide its original failure.
                            pass

            shutil.rmtree(run_dir / "candidate_workspaces", ignore_errors=True)
            progress_lines: list[str] = []
            for value in committed_results:
                event_result = {"summary": str(value["summary"])}
                progress_lines.extend(
                    json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    for event in (
                        {"event": "question_started", "questionId": value["questionId"]},
                        {
                            "event": "stage_completed",
                            "questionId": value["questionId"],
                            "stageId": stage_id,
                            "result": event_result,
                        },
                        {"event": "question_completed", "questionId": value["questionId"]},
                    )
                )
            progress_relative = self._maintenance_relative_path(
                child["progressReceiptPath"]
            )
            atomic_write(
                self.repo_root / progress_relative,
                "\n".join(progress_lines) + "\n",
            )
            work_version_receipt = {
                "recordedCount": sum(
                    int(value.get("recordedCount") or 0)
                    for value in work_version_receipts
                ),
                "items": work_version_receipts,
            }
            aggregate_receipt = {
                "status": "succeeded",
                "summary": (
                    f"{len(question_ids)}問を検査し、"
                    f"{sum(value['status'] == 'succeeded' for value in committed_results)}問を確定しました。"
                ),
                "commands": [
                    {"command": "server-owned candidate validation", "status": "pass"}
                ],
                "changedFiles": sorted(committed_files),
                "resolvedFailedDeltaPaths": [],
                **(
                    {
                        "aggregateReviewPromptContractVersion": (
                            AGGREGATE_REVIEW_PROMPT_CONTRACT_VERSION
                        )
                    }
                    if aggregate_review_enabled
                    else {}
                ),
                **(
                    {
                        "aggregateReviewAdjudicationPromptContractVersion": (
                            AGGREGATE_ADJUDICATION_PROMPT_CONTRACT_VERSION
                        ),
                        "aggregateReviewAdjudicatedQuestionIds": sorted(
                            aggregate_review_adjudications
                        ),
                    }
                    if aggregate_review_adjudications
                    else {}
                ),
            }
            execution_metadata = copy.deepcopy(
                prepared_execution_metadata
            )
            persisted_attempt = self.store.get(qualification, run_id)
            persisted_rollback = persisted_attempt.get("rollback")
            final_rollback = (
                copy.deepcopy(dict(persisted_rollback))
                if isinstance(persisted_rollback, Mapping)
                and str(persisted_rollback.get("status") or "")
                in {"succeeded", "failed", "not_required"}
                else {
                    "status": "not_required",
                    "restoredFiles": [],
                    "remainingChangedFiles": [],
                    "deltaUnknown": False,
                    "message": "問題別checkpointを保存しました。",
                }
            )
            self.store.update(
                qualification,
                run_id,
                status="validating",
                receiptValidated=False,
                executionPhase="structured_candidate_finalize",
                activeCandidateQuestionId=None,
                candidateTransactionOpen=False,
                batchQuestionResults=copy.deepcopy(committed_results),
                workVersionReceipt=copy.deepcopy(work_version_receipt),
                **execution_metadata,
                writeAttributionVerified=True,
                unsafeNotifiedChangedFiles=[],
                unsafeChangedFiles=[],
                rollback=final_rollback,
                deltaUnknown=False,
                error=None,
            )
            self.store.write_result(qualification, run_id, aggregate_receipt)
            self.store.refresh(qualification, run_id)
            self._validate_progress_receipt(
                qualification,
                run_id,
                self.store.get(qualification, run_id),
            )
            refreshed = self.store.update(
                qualification,
                run_id,
                status="succeeded",
                receiptValidated=True,
                batchQuestionResults=committed_results,
                workVersionReceipt=work_version_receipt,
                **execution_metadata,
                writeAttributionVerified=True,
                unsafeNotifiedChangedFiles=[],
                unsafeChangedFiles=[],
                rollback=final_rollback,
                deltaUnknown=False,
                result=aggregate_receipt,
                artifactSync={
                    "status": "deferred",
                    "groups": [],
                    "message": "公開用データはqueue終了時に更新します。",
                },
                error=None,
                finishedAt=_now(),
            )
            emit(aggregate_receipt["summary"])
            return {
                "qualification": qualification,
                "runId": run_id,
                "questionResults": committed_results,
                "workVersionReceipt": work_version_receipt,
                "child": refreshed,
            }
        except Exception as exc:  # noqa: BLE001
            self.store.write_result(
                qualification,
                run_id,
                {
                    "status": "failed",
                    "summary": str(exc),
                    "commands": [],
                    "changedFiles": sorted(committed_files),
                },
            )
            self.store.refresh(qualification, run_id)
            failed_child = self.store.get(qualification, run_id)
            canonical_write_started = (
                failed_child.get("canonicalWriteStarted") is True
            )
            recorded_rollback = failed_child.get("rollback")
            if not isinstance(recorded_rollback, Mapping) or str(
                recorded_rollback.get("status") or ""
            ) not in {"succeeded", "failed", "not_required"}:
                recorded_rollback = {
                    "status": (
                        "failed"
                        if canonical_write_started
                        else "not_required"
                    ),
                    "restoredFiles": [],
                    "remainingChangedFiles": [],
                    "deltaUnknown": canonical_write_started,
                    "message": (
                        "正本書込み後の回復結果を確認できません。"
                        if canonical_write_started
                        else "正本書込み前に停止したためrollbackは不要です。"
                    ),
                }
            rollback_safe = bool(
                recorded_rollback.get("deltaUnknown") is not True
                and not recorded_rollback.get("remainingChangedFiles")
                and (
                    recorded_rollback.get("status") == "succeeded"
                    or (
                        recorded_rollback.get("status") == "not_required"
                        and not canonical_write_started
                    )
                )
            )
            self.store.update(
                qualification,
                run_id,
                status="failed",
                receiptValidated=False,
                candidateTransactionOpen=False,
                rollback=dict(recorded_rollback),
                deltaUnknown=not rollback_safe,
                writeAttributionVerified=rollback_safe,
                unsafeNotifiedChangedFiles=[],
                unsafeChangedFiles=list(
                    recorded_rollback.get("remainingChangedFiles") or []
                ),
                error=str(exc),
                finishedAt=_now(),
            )
            raise

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
        app_server_changed_files: tuple[str, ...] = ()
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
        human_question_ids = [
            str(value)
            for value in (
                run_at_start.get("targetQuestionIds")
                or [
                    target.get("id") or target.get("uiQuestionId")
                    for target in run_at_start.get("progressTargets") or []
                    if isinstance(target, Mapping)
                ]
            )
            if value
        ]
        human_work_item_keys = [
            str(target.get("workItemKey") or target.get("id") or "")
            for target in run_at_start.get("progressTargets") or []
            if isinstance(target, Mapping)
            and (target.get("workItemKey") or target.get("id"))
        ]
        human_list_group_ids = [
            str(value)
            for value in (
                run_at_start.get("targetGroupIds")
                or run_at_start.get("scopeListGroupIds")
                or []
            )
            if value
        ]
        human_stage_id = str(
            run_at_start.get("stageId")
            or run_at_start.get("currentPhaseId")
            or ""
        )
        speed_mode = normalize_speed_mode(
            run_at_start.get("speedMode") or STANDARD_SPEED_MODE
        )

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
                self._check_source_immutability(
                    emit,
                    source_files=[
                        str(value) for value in current_run.get("sourceFiles") or []
                    ],
                )
            writable_roots, created_writable_dirs = self._maintenance_writable_roots(
                qualification, run_id
            )
            scoped_transaction_roots = self._maintenance_transaction_roots(
                current_run,
                writable_roots,
            )
            work_version_questions = [
                {
                    **target,
                    "qualification": qualification,
                }
                for target in (current_run.get("progressTargets") or [])
                if isinstance(target, Mapping)
            ]
            if not work_version_questions:
                inventory = getattr(self.workflow, "inventory", None)
                if inventory is not None:
                    for list_group_id in (
                        current_run.get("targetGroupIds") or []
                    ):
                        group = inventory.group(
                            qualification,
                            str(list_group_id),
                        )
                        work_version_questions.extend(
                            {
                                **question,
                                "qualification": qualification,
                            }
                            for question in (group.get("questions") or [])
                            if isinstance(question, Mapping)
                        )
            transaction_roots = tuple(
                dict.fromkeys(
                    [
                        *scoped_transaction_roots,
                        *self.work_versions.transaction_paths_for_questions(
                            work_version_questions
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
                            speed_mode=speed_mode,
                            turn_group=qualification,
                            monitor_context=self._monitor_context(
                                qualification,
                                run_id,
                                parent_run_id=parent_run_id,
                                question_ids=human_question_ids,
                                work_item_keys=human_work_item_keys,
                                list_group_ids=human_list_group_ids,
                                stage_id=human_stage_id,
                                work_type="maintenance_research",
                                phase="parallel_research",
                            ),
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
                        speed_mode=speed_mode,
                        turn_group=qualification,
                        monitor_context=self._monitor_context(
                            qualification,
                            run_id,
                            parent_run_id=parent_run_id,
                            question_ids=human_question_ids,
                            work_item_keys=human_work_item_keys,
                            list_group_ids=human_list_group_ids,
                            stage_id=human_stage_id,
                            work_type=work_type,
                            phase="writing",
                        ),
                    )
                    app_server_changed_files = self._repository_change_notifications(
                        result.changed_files,
                        transient_root=turn_workspace,
                    )
                    self.store.update(
                        qualification,
                        run_id,
                        appServerChangedFiles=list(app_server_changed_files),
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
            self._check_source_immutability(
                emit,
                source_files=[
                    str(value) for value in current_run.get("sourceFiles") or []
                ],
            )
            if turn_error is not None:
                failed_attribution = self._attribute_repository_changes(
                    qualification,
                    run_id,
                    current_run,
                    notified_files=app_server_changed_files,
                    actual_files=filesystem_changed_files,
                )
                changed = sorted(
                    str(path)
                    for path in (
                        failed_attribution["scopedActual"]
                        | failed_attribution["unsafeNotified"]
                        | failed_attribution["unsafeActual"]
                        | failed_attribution["extraAgentOutput"]
                    )
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
            change_attribution = self._validate_changed_files(
                qualification,
                run_id,
                refreshed,
                app_server_changed_files,
                filesystem_changed_files,
            )
            self.store.update(
                qualification,
                run_id,
                writeAttributionVerified=True,
                externalConcurrentChangedFiles=change_attribution[
                    "externalConcurrentChangedFiles"
                ],
                ignoredReceiptChangedFiles=change_attribution[
                    "ignoredReceiptChangedFiles"
                ],
                unsafeNotifiedChangedFiles=[],
                unsafeChangedFiles=[],
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
                "changedFiles": change_attribution["changedFiles"],
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
            current_result = current.get("result")
            current_result = (
                current_result if isinstance(current_result, Mapping) else {}
            )
            attribution = self._attribute_repository_changes(
                qualification,
                run_id,
                current,
                declared_files=[
                    str(value) for value in current_result.get("changedFiles") or []
                ],
                notified_files=app_server_changed_files,
                actual_files=pre_rollback_files,
            )
            unsafe_notified = (
                attribution["unsafeNotified"]
                | attribution["extraAgentOutput"]
            )
            unsafe_changes = (
                unsafe_notified
                | attribution["unsafeActual"]
                | attribution["unsafeDeclared"]
            )
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
                filesystem_changed_files = tuple(
                    sorted(
                        {str(value) for value in unsafe_changes}
                        | {
                            str(value)
                            for value in rollback.get(
                                "remainingChangedFiles"
                            )
                            or []
                        }
                    )
                )
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
                appServerChangedFiles=list(app_server_changed_files),
                writeAttributionVerified=True,
                unsafeNotifiedChangedFiles=sorted(
                    str(path) for path in unsafe_notified
                ),
                unsafeChangedFiles=sorted(
                    str(path) for path in unsafe_changes
                ),
                externalConcurrentChangedFiles=sorted(
                    str(path) for path in attribution["externalActual"]
                ),
                ignoredReceiptChangedFiles=sorted(
                    str(path) for path in attribution["externalDeclared"]
                ),
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
        if str(run.get("parallelStrategy") or "") == "question_turn":
            questions = self._projected_policy_questions(run)
        else:
            questions = []
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
        selected_fields_by_stage = run.get("selectedFieldsByStage") or {}
        selected_update_target_ids = {
            str(value)
            for value in run.get("selectedUpdateTargetIds") or []
            if value
        }
        planned: list[
            tuple[list[Mapping[str, Any]], dict[str, Any], list[str] | None]
        ] = []
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
            selected_fields = {
                str(value)
                for value in selected_fields_by_stage.get(stage_id) or []
                if value
            }
            if stage_id in {"explanation", "law_audit"} and (
                not selected_fields or "explanationText" in selected_fields
            ):
                self._validate_explanation_quality(selected)
            if "suggestedQuestionDetailsByChoice" in selected_fields:
                self._validate_supplementary_questions(selected)
            if (
                stage_id == "law_audit"
                or (
                    stage_id == "explanation"
                    and "lawRevisionFacts" in selected_fields
                )
            ):
                self._validate_law_audit_quality(selected)
            if stage_id == "law_audit":
                self._validate_law_audit_sidecar_consistency(
                    qualification,
                    selected,
                )
            policy = {
                **policies[stage_id],
                "policyVersion": normalize_policy_version(raw_version),
                "policyFingerprint": run_fingerprint,
            }
            available_target_ids = {
                str(value.get("selectionId") or "")
                for value in policy.get("updateTargets") or []
                if isinstance(value, Mapping) and value.get("selectionId")
            }
            stage_target_ids = sorted(
                value
                for value in selected_update_target_ids
                if value in available_target_ids
            )
            partial_target_ids = (
                stage_target_ids
                if stage_target_ids and set(stage_target_ids) != available_target_ids
                else None
            )
            planned.append((selected, policy, partial_target_ids))
        receipts = [
            self.work_versions.record_stage(
                selected,
                policy,
                run_id=str(run["runId"]),
                source="validated_run",
                target_ids=target_ids,
            )
            for selected, policy, target_ids in planned
        ]
        return {
            "recordedCount": sum(
                int(receipt.get("recordedCount") or 0) for receipt in receipts
            ),
            "stages": receipts,
        }

    def _projected_policy_questions(
        self,
        run: Mapping[str, Any],
    ) -> list[Mapping[str, Any]]:
        qualification = str(run["qualification"])
        targets = [
            dict(value)
            for value in run.get("progressTargets") or []
            if isinstance(value, Mapping)
        ]
        if len(targets) != 1:
            raise QualificationRunError(
                "一問patch反映の工程バージョン対象が1問ではありません。"
            )
        target = targets[0]
        try:
            projection = self._project_question_now(qualification, target)
        except QuestionItemError as exc:
            raise QualificationRunError(str(exc)) from exc
        projected = copy.deepcopy(dict(projection.record))
        identity = SourceIdentityBinding.from_mapping(target)
        question_id = str(target.get("id") or target.get("uiQuestionId") or "")
        review_key = str(target.get("reviewKey") or question_id)
        return [
            {
                "id": question_id,
                "reviewKey": review_key,
                "qualification": qualification,
                "publicationQualificationId": str(
                    target.get("publicationQualificationId") or qualification
                ),
                "listGroupId": str(target.get("listGroupId") or ""),
                "originalQuestionId": identity.review_question_id,
                **identity.as_mapping(),
                "questionLabel": str(target.get("questionLabel") or ""),
                "isLawRelated": projected.get("isLawRelated") is True,
                "source": {},
                "projected": projected,
                "paths": {
                    "patches": list(getattr(projection, "applied_files", ())),
                },
                "stateHash": sha256_json(
                    {
                        field: projected.get(field)
                        for field in PROJECTED_COMPARE_FIELDS
                    }
                ),
            }
        ]

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
            errors.extend(
                f"{label} {issue}"
                for issue in explanation_style_issues(
                    explanations,
                    projected.get("correctChoiceText"),
                    choice_texts=choices,
                    question_type=projected.get("questionType"),
                    is_calculation_question=projected.get(
                        "isCalculationQuestion"
                    )
                    is True,
                )
            )
        if errors:
            raise QualificationRunError(
                "03 解説の日本語品質検証に失敗しました。"
                + " ".join(errors[:5])
                + (f" ほか{len(errors) - 5}件。" if len(errors) > 5 else "")
            )

    @staticmethod
    def _validate_supplementary_questions(
        questions: list[Mapping[str, Any]],
    ) -> None:
        errors: list[str] = []
        for question in questions:
            projected = question.get("projected")
            if not isinstance(projected, Mapping):
                errors.append("対象問題: projected recordを確認できません。")
                continue
            choices = projected.get("choiceTextList")
            choices = choices if isinstance(choices, list) else []
            issues = suggested_question_validation_errors(
                projected.get("suggestedQuestionDetailsByChoice"),
                choice_count=len(choices),
                allowed_choice_indexes=public_choice_indexes(
                    projected.get("questionType"),
                    projected.get("correctChoiceText"),
                    len(choices),
                    projected.get("questionIntent"),
                ),
            )
            if issues:
                label = str(
                    question.get("questionLabel")
                    or question.get("originalQuestionId")
                    or question.get("id")
                    or "対象問題"
                )
                errors.append(f"{label}: " + " / ".join(issues))
        if errors:
            raise QualificationRunError(
                "03 補足質問の品質検証に失敗しました。"
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
                if not is_law_revision_facts_shape(
                    dict(fact),
                    allow_choice_verdict_lists=True,
                ):
                    errors.append(
                        f"{label}: {fact_label}が"
                        "Firestore公開契約に一致しません。"
                    )
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
            expected_binding_payload = {
                **expected_binding.as_mapping(),
                "aliases": sorted(aliases),
            }
            matches = [
                (line_number, row)
                for line_number, row, row_aliases in rows_by_group.get(
                    list_group_id,
                    [],
                )
                if (
                    (
                        row.get("schemaVersion") == "law-revision-audit/v2"
                        and _source_binding_accepts_identity(
                            expected_binding_payload, row
                        )
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
                not in aliases | {expected_review_id}
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
            allowed_choice_states = allowed_final_states | {
                ("not_law_related", "secondary_verified"),
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
            ) or any(state not in allowed_choice_states for state in projected_states):
                errors.append(
                    f"{label}: projected lawRevisionFactsが公開確定状態ではありません。"
                )
            elif all(
                state == ("not_law_related", "secondary_verified")
                for state in projected_states
            ):
                errors.append(
                    f"{label}: isLawRelated=trueですが、全選択肢が"
                    "not_law_relatedになっています。"
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

    def _attribute_repository_changes(
        self,
        qualification: str,
        run_id: str,
        run: Mapping[str, Any],
        *,
        declared_files: tuple[str, ...] | list[str] = (),
        notified_files: tuple[str, ...] | list[str] = (),
        actual_files: tuple[str, ...] | list[str] = (),
    ) -> dict[str, set[Path]]:
        """sandbox内のwriter通知と、repoの外部変更を分離する。"""

        def relative_paths(values: tuple[str, ...] | list[str]) -> set[Path]:
            return {
                self._maintenance_relative_path(value)
                for value in values
                if str(value).strip()
            }

        declared = relative_paths(declared_files)
        notified = relative_paths(notified_files)
        actual = relative_paths(actual_files)
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
        for values in (declared, notified, actual):
            values.discard(receipt_path)
            values.discard(progress_path)

        agent_output_root = receipt_path.parent
        extra_agent_output = {
            path
            for path in declared | notified | actual
            if path == agent_output_root or path.is_relative_to(agent_output_root)
        }
        allowed_roots = self._maintenance_root_candidates(
            qualification,
            run_id,
            run,
        )

        def is_scoped(path: Path) -> bool:
            return self._maintenance_path_allowed_for_run(
                path,
                allowed_roots,
                run,
            )

        scoped_declared = {path for path in declared if is_scoped(path)}
        scoped_notified = {path for path in notified if is_scoped(path)}
        scoped_actual = {path for path in actual if is_scoped(path)}
        unsafe_notified = notified - scoped_notified - extra_agent_output
        outside_actual = actual - scoped_actual - extra_agent_output
        outside_declared = declared - scoped_declared - extra_agent_output
        concurrent_commit = bool(
            Path(".git", "HEAD") in outside_actual
            or Path(".git", "HEAD") in outside_declared
        )
        sandbox_isolated = str(run.get("sandbox") or "") == "workspace-write"
        # workspace-write threadはserver確定のwritable_roots内だけを書ける。
        # したがって、その外側でApp Server通知のない差分は別作業の変更であり、
        # receiptに混入してもwriterへ帰属させず、rollbackもしない。
        if concurrent_commit or sandbox_isolated:
            external_actual = outside_actual - unsafe_notified
            unsafe_actual = outside_actual & unsafe_notified
            external_declared = outside_declared - unsafe_notified
            unsafe_declared = outside_declared & unsafe_notified
        else:
            external_actual = set()
            unsafe_actual = outside_actual
            external_declared = set()
            unsafe_declared = outside_declared
        return {
            "scopedDeclared": scoped_declared,
            "scopedNotified": scoped_notified,
            "scopedActual": scoped_actual,
            "unsafeNotified": unsafe_notified,
            "unsafeActual": unsafe_actual,
            "unsafeDeclared": unsafe_declared,
            "externalDeclared": external_declared,
            "externalActual": external_actual,
            "extraAgentOutput": extra_agent_output,
        }

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

    def _check_source_immutability(
        self,
        emit: Callable[[str], None],
        *,
        source_files: list[str] | tuple[str, ...] = (),
    ) -> None:
        scoped_files = list(dict.fromkeys(str(value) for value in source_files if value))
        manifest_path = (
            self.repo_root / "docs/contracts/00_source_sha256_manifest.jsonl"
        )
        if scoped_files and manifest_path.is_file():
            try:
                manifest: dict[str, str] = {}
                for line_number, raw_line in enumerate(
                    manifest_path.read_text(encoding="utf-8").splitlines(), 1
                ):
                    if not raw_line.strip():
                        continue
                    row = json.loads(raw_line)
                    path = row.get("path") if isinstance(row, Mapping) else None
                    digest = row.get("sha256") if isinstance(row, Mapping) else None
                    if (
                        not isinstance(path, str)
                        or not isinstance(digest, str)
                        or len(digest) != 64
                        or path in manifest
                    ):
                        raise QualificationRunError(
                            f"00_source manifestの{line_number}行目が不正です。"
                        )
                    manifest[path] = digest
                checked: list[str] = []
                for value in scoped_files:
                    relative = self._maintenance_relative_path(value)
                    if "00_source" not in relative.parts:
                        raise QualificationRunError(
                            f"sourceFilesが00_source配下ではありません: {relative}"
                        )
                    path = self.repo_root / relative
                    candidates = (
                        [
                            candidate
                            for candidate in manifest
                            if Path(candidate).is_relative_to(relative)
                        ]
                        if path.is_dir()
                        else [relative.as_posix()]
                    )
                    if not candidates:
                        raise QualificationRunError(
                            f"00_sourceの登録済み正本を確認できません: {relative}"
                        )
                    for candidate in candidates:
                        expected = manifest.get(candidate)
                        candidate_path = self.repo_root / candidate
                        if (
                            not expected
                            or not candidate_path.is_file()
                            or candidate_path.is_symlink()
                        ):
                            raise QualificationRunError(
                                "00_sourceの登録済み正本を確認できません: "
                                f"{candidate}"
                            )
                        actual = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
                        if not hmac.compare_digest(actual, expected):
                            raise QualificationRunError(
                                f"00_sourceの改変を検出しました: {candidate}"
                            )
                        checked.append(candidate)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise QualificationRunError(
                    "00_source manifestを確認できません。"
                ) from exc
            emit(f"対象{len(checked)}fileの00_source不変を確認しました。")
            return
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
    ) -> dict[str, list[str]]:
        result = run.get("result")
        result = result if isinstance(result, Mapping) else {}
        resolved_failed = {
            self._maintenance_relative_path(path)
            for path in result.get("resolvedFailedDeltaPaths") or []
        }
        if resolved_failed:
            raise QualificationRunError(
                "未確定差分の解決記録はserverが確定するため、完了receiptへ指定できません。"
            )
        attribution = self._attribute_repository_changes(
            qualification,
            run_id,
            run,
            declared_files=[str(value) for value in result.get("changedFiles") or []],
            notified_files=app_server_changed_files,
            actual_files=filesystem_changed_files,
        )
        extra_agent_output = attribution["extraAgentOutput"]
        if extra_agent_output:
            raise QualificationRunError(
                "agent_outputにはresult.json以外（画面用progress.jsonlを除く）を保存できません: "
                + ", ".join(str(path) for path in sorted(extra_agent_output))
            )
        unsafe_notified = attribution["unsafeNotified"]
        if unsafe_notified:
            raise QualificationRunError(
                "Codex App Serverが整備責務外のfile変更を通知しました: "
                + ", ".join(str(path) for path in sorted(unsafe_notified))
            )
        unsafe_unattributed = (
            attribution["unsafeActual"] | attribution["unsafeDeclared"]
        )
        if unsafe_unattributed:
            raise QualificationRunError(
                "整備責務外のfile変更を検出しました: "
                + ", ".join(str(path) for path in sorted(unsafe_unattributed))
            )
        declared = attribution["scopedDeclared"]
        notified = attribution["scopedNotified"]
        actual = attribution["scopedActual"]
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
        return {
            "changedFiles": [str(path) for path in sorted(actual)],
            "externalConcurrentChangedFiles": [
                str(path) for path in sorted(attribution["externalActual"])
            ],
            "ignoredReceiptChangedFiles": [
                str(path) for path in sorted(attribution["externalDeclared"])
            ],
            "unsafeNotifiedChangedFiles": [],
        }

    def _validate_record_scope(
        self,
        qualification: str,
        run_id: str,
        run: Mapping[str, Any],
        actual: set[Path],
        *,
        validation_root: Path | None = None,
        baseline_payload: Mapping[str, Any] | None = None,
        projected_records: Mapping[str, Mapping[str, Any]] | None = None,
        current_source_records: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        record_root = (validation_root or self.repo_root).resolve()
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
        raw_selected_fields_by_stage = run.get("selectedFieldsByStage")
        selected_originalize_content_fields = (
            {
                str(value)
                for value in raw_selected_fields_by_stage.get("originalize") or []
                if value
            }
            & {"questionBodyText", "choiceTextList"}
            if isinstance(raw_selected_fields_by_stage, Mapping)
            else set()
        )
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
        if baseline_payload is None:
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
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise QualificationRunError(
                    "record baselineを確認できません。"
                ) from exc
        else:
            payload = baseline_payload
        snapshots = payload.get("recordSnapshots")
        source_snapshots = payload.get("sourceRecordSnapshots")
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

        def unbound_legacy_matches(
            entries: list[Any], entry_aliases: set[str]
        ) -> list[Mapping[str, Any]]:
            candidates = [
                entry
                for entry in matching(entries, entry_aliases)
                if not SourceIdentityBinding.from_mapping(
                    identity(entry)
                ).source_record_ref
            ]
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
            is_originalized_patch = bool(
                "originalize" in stage_ids
                and "05_originalized" in relative.parts
                and relative.parts[:3]
                == ("output", qualification, "questions_json")
            )
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
            after = _record_snapshot(record_root / relative)
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
                        if _source_binding_accepts_identity(
                            binding, after_identity
                        )
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
                    not matching_bindings
                    and not matching_target_groups
                    and (
                        is_law_audit_sidecar
                        or entry_source_binding.is_complete()
                    )
                ):
                    # Non-target sidecar rows can remain on the legacy v1
                    # schema.  Target-specific binding and schema checks apply
                    # only to this work item's row; the whole-file comparison
                    # below still rejects any non-target change.
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
                projected_fields: dict[str, Any] = {}
                projected_source_aliases: set[str] = set()
                projected_workflow_aliases: set[str] = set()
                current_source_fields: dict[str, Any] = {}
                if matched_binding is not None and isinstance(
                    projected_records, Mapping
                ):
                    projected_record = projected_records.get(
                        str(matched_binding.get("uiQuestionId") or "")
                    )
                    if isinstance(projected_record, Mapping):
                        projected_fields = {
                            field: copy.deepcopy(projected_record[field])
                            for field in CODEX_PROTECTED_CONTENT_FIELDS
                            if field in projected_record
                        }
                        projected_source_aliases = source_identity_aliases(
                            projected_record
                        )
                        projected_workflow_aliases = workflow_identity_aliases(
                            projected_record
                        )
                if matched_binding is not None and isinstance(
                    current_source_records, Mapping
                ):
                    current_source_record = current_source_records.get(
                        str(matched_binding.get("uiQuestionId") or "")
                    )
                    if isinstance(current_source_record, Mapping):
                        current_source_fields = {
                            field: copy.deepcopy(current_source_record[field])
                            for field in CODEX_PROTECTED_CONTENT_FIELDS
                            if field in current_source_record
                        }
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
                if not before_matches and matched_source_binding is not None:
                    legacy_before_matches = unbound_legacy_matches(
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
                    if (
                        not source_matches
                        and run.get("failedDeltaReconciliation") is True
                    ):
                        source_matches = unbound_legacy_matches(
                            source_entries,
                            binding_aliases or entry_aliases,
                        )
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
                    and run.get("failedDeltaReconciliation") is not True
                ):
                    raise QualificationRunError(
                        f"更新patch rowにsourceRecordRefがありません: {relative}"
                    )
                before_identity = unambiguous_identity(
                    before_matches, relative
                )
                source_identity = unambiguous_identity(
                    source_matches, relative
                )
                before_schema_versions = {
                    str(contract(entry).get("schemaVersion") or "")
                    for entry in before_matches
                }
                allowed_derived_fields: dict[str, Any] = {}
                allowed_server_removed_fields: set[str] = set()
                decomposition = contract(after_entry).get(
                    "aggregateAnswerDecomposition"
                )
                source_text = (
                    source_fields.get("questionBodyText")
                    if source_fields is not None
                    else None
                )
                after_source_text = after_fields.get("questionBodyText")
                declared_source_unique_key_aliases: set[str] = set()
                exact_scoped_source_unique_key_aliases: set[str] = set()
                raw_source_unique_keys = after_fields.get("sourceUniqueKeys")
                if isinstance(raw_source_unique_keys, list):
                    for value in raw_source_unique_keys:
                        if not value:
                            continue
                        source_key = str(value)
                        candidate_aliases = {source_key}
                        document_id = question_id_from_source_unique_key(
                            source_key
                        )
                        if document_id:
                            candidate_aliases.add(document_id)
                        declared_source_unique_key_aliases.update(
                            candidate_aliases
                        )
                        if candidate_aliases.issubset(matched_target_group):
                            exact_scoped_source_unique_key_aliases.update(
                                candidate_aliases
                            )
                if (
                    isinstance(raw_source_unique_keys, list)
                    and raw_source_unique_keys
                    and exact_scoped_source_unique_key_aliases
                    == declared_source_unique_key_aliases
                ):
                    allowed_derived_fields["sourceUniqueKeys"] = copy.deepcopy(
                        raw_source_unique_keys
                    )
                if (
                    isinstance(source_text, str)
                    and (
                        after_source_text is None
                        or after_source_text == source_text
                    )
                    and is_approved_target(decomposition, source_text)
                ):
                    derived_keys = after_fields.get("sourceUniqueKeys")
                    source_parent_identities = {
                        json.dumps(
                            contract(entry).get(
                                "aggregateStableParentIdentity"
                            ),
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        for entry in source_matches
                        if contract(entry).get(
                            "aggregateStableParentIdentity"
                        )
                    }
                    if len(source_parent_identities) != 1:
                        raise QualificationRunError(
                            f"sourceのstable parent identityを一意に確認できません: "
                            f"{relative}"
                        )
                    source_parent_identity = json.loads(
                        next(iter(source_parent_identities))
                    )
                    expected_derived_keys = derived_source_unique_keys_for_parent(
                        str(source_parent_identity["value"]),
                        source_text,
                        decomposition,
                    )
                    if derived_keys != expected_derived_keys:
                        raise QualificationRunError(
                            f"派生sourceUniqueKeysを再現できません: {relative}"
                        )
                    allowed_derived_fields = {
                        "choiceTextList": extract_source_statements(
                            source_text,
                            decomposition,
                        ),
                        "sourceUniqueKeys": expected_derived_keys,
                    }
                elif (
                    isinstance(source_text, str)
                    and decomposition is None
                    and "question_type"
                    in {
                        str(run.get("stageId") or ""),
                        *(
                            str(value)
                            for value in run.get("stageIds") or []
                        ),
                    }
                    and any(
                        is_approved_target(
                            contract(entry).get("aggregateAnswerDecomposition"),
                            source_text,
                        )
                        for entry in before_matches
                    )
                    and source_fields is not None
                ):
                    for field in ("choiceTextList", "sourceUniqueKeys"):
                        if field in source_fields:
                            allowed_derived_fields[field] = copy.deepcopy(
                                source_fields[field]
                            )
                        else:
                            allowed_server_removed_fields.add(field)
                if before_identity is not None:
                    if after_identity != before_identity:
                        allowed_empty_identity_cleanup = bool(
                            after_identity
                            == {
                                field: value
                                for field, value in before_identity.items()
                                if value is not None and value != ""
                            }
                        )
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
                            and entry_source_binding.review_question_id
                            == matched_source_binding.review_question_id
                            and all(
                                after_identity.get(field) == value
                                for field, value in before_identity.items()
                            )
                            and set(after_identity) - set(before_identity)
                            <= {
                                "sourceQuestionKey",
                                "reviewQuestionId",
                                "sourceRecordRef",
                            }
                        )
                        reconciliation_identity_values = dict(
                            source_identity or {}
                        )
                        if matched_source_binding is not None:
                            reconciliation_identity_values.update(
                                {
                                    "sourceQuestionKey": (
                                        matched_source_binding.source_question_key
                                    ),
                                    "reviewQuestionId": (
                                        matched_source_binding.review_question_id
                                    ),
                                    "sourceRecordRef": (
                                        matched_source_binding.source_record_ref
                                    ),
                                }
                            )
                        reconciliation_new_identity_fields = (
                            set(after_identity) - set(before_identity)
                        )
                        allowed_reconciliation_identity_enrichment = bool(
                            run.get("failedDeltaReconciliation") is True
                            and not is_law_audit_sidecar
                            and matched_source_binding is not None
                            and matched_source_binding.is_complete()
                            and all(
                                after_identity.get(field) == value
                                for field, value in before_identity.items()
                            )
                            and reconciliation_new_identity_fields
                            <= set(reconciliation_identity_values)
                            and all(
                                after_identity.get(field)
                                == reconciliation_identity_values.get(field)
                                for field in reconciliation_new_identity_fields
                            )
                        )
                        canonical_sidecar_identity = (
                            {
                                "reviewQuestionId": (
                                    matched_source_binding.review_question_id
                                ),
                                "sourceQuestionKey": (
                                    matched_source_binding.source_question_key
                                ),
                                "sourceRecordRef": (
                                    matched_source_binding.source_record_ref
                                ),
                            }
                            if (
                                is_law_audit_sidecar
                                and matched_source_binding is not None
                                and matched_source_binding.is_complete()
                            )
                            else {}
                        )
                        allowed_sidecar_identity_repair = bool(
                            is_law_audit_sidecar
                            and matched_source_binding is not None
                            and before_schema_versions
                            <= {
                                "law-revision-audit/v1",
                                "law-revision-audit/v2",
                            }
                            and contract(after_entry).get("schemaVersion")
                            == "law-revision-audit/v2"
                            and matched_source_binding.is_complete()
                            and after_identity == canonical_sidecar_identity
                            and set(before_identity)
                            <= {
                                "sourceQuestionKey",
                                "reviewQuestionId",
                                "sourceRecordRef",
                            }
                            and str(
                                before_identity.get("sourceQuestionKey") or ""
                            )
                            in {
                                "",
                                matched_source_binding.source_question_key,
                            }
                            and str(
                                before_identity.get("sourceRecordRef") or ""
                            )
                            in {
                                "",
                                matched_source_binding.source_record_ref,
                            }
                            and str(
                                before_identity.get("reviewQuestionId") or ""
                            )
                            in {
                                "",
                                *binding_aliases,
                            }
                        )
                        if not (
                            allowed_empty_identity_cleanup
                            or allowed_patch_identity_enrichment
                            or allowed_reconciliation_identity_enrichment
                            or allowed_sidecar_identity_repair
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
                    derived_aliases = set(
                        allowed_derived_fields.get("sourceUniqueKeys") or []
                    )
                    derived_aliases.update(
                        document_id
                        for source_key in tuple(derived_aliases)
                        if (document_id := question_id_from_source_unique_key(source_key))
                    )
                    source_bound_aliases = {
                        alias
                        for entry in source_matches
                        for alias in source_aliases(entry)
                    }
                    if (
                        matched_source_binding is not None
                        and run.get("failedDeltaReconciliation") is True
                    ):
                        # baseline側にURL等のsource aliasが残っていても、
                        # 現在inventoryが同じsourceRecordRefへ一意に束ねた
                        # 対象groupだけを許可し、無関係なID注入は拒否する。
                        source_bound_aliases.update(matched_target_group)
                    allowed_scoped_aliases = (
                        matched_target_group
                        | source_bound_aliases
                        | projected_source_aliases
                        | projected_workflow_aliases
                    )
                    if (
                        len(matching_target_groups) != 1
                        or not source_matches
                        or not (entry_aliases - derived_aliases).issubset(
                            allowed_scoped_aliases
                        )
                        or (
                            not is_law_audit_sidecar
                            and (
                                not (
                                    source_aliases(after_entry)
                                    - derived_aliases
                                    - exact_scoped_source_unique_key_aliases
                                ).issubset(
                                    source_bound_aliases
                                    | projected_source_aliases
                                )
                                or not workflow_aliases(after_entry).issubset(
                                    source_bound_aliases
                                    | projected_workflow_aliases
                                )
                            )
                        )
                    ):
                        target_extras = sorted(
                            (entry_aliases - derived_aliases)
                            - allowed_scoped_aliases
                        )
                        source_extras = sorted(
                            (source_aliases(after_entry) - derived_aliases)
                            - (
                                source_bound_aliases
                                | projected_source_aliases
                            )
                        )
                        workflow_extras = sorted(
                            workflow_aliases(after_entry)
                            - (
                                source_bound_aliases
                                | projected_workflow_aliases
                            )
                        )
                        raise QualificationRunError(
                            f"sourceと異なるID fieldを検出しました: {relative} / "
                            f"targetGroups={len(matching_target_groups)}, "
                            f"sourceMatches={len(source_matches)}, "
                            f"targetExtras={target_extras}, "
                            f"sourceExtras={source_extras}, "
                            f"workflowExtras={workflow_extras}"
                        )
                if is_law_audit_sidecar:
                    if (
                        matched_binding is None
                        or not _source_binding_accepts_identity(
                            matched_binding, after_identity
                        )
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
                    selected_originalize_change = bool(
                        is_originalized_patch
                        and field in selected_originalize_content_fields
                        and field in after_fields
                    )
                    if selected_originalize_change:
                        continue
                    exact_projected_value = bool(
                        field in projected_fields
                        and field in after_fields
                        and after_fields[field] == projected_fields[field]
                    )
                    exact_current_source_value = bool(
                        field in current_source_fields
                        and field in after_fields
                        and after_fields[field] == current_source_fields[field]
                    )
                    if before_fields is not None and field in before_fields:
                        exact_server_derived_change = bool(
                            field in allowed_derived_fields
                            and after_fields.get(field)
                            == allowed_derived_fields[field]
                        )
                        exact_server_derived_removal = bool(
                            field in allowed_server_removed_fields
                            and field not in after_fields
                        )
                        removed_redundant_source_copy = bool(
                            field not in after_fields
                            and source_fields is not None
                            and source_fields.get(field) == before_fields[field]
                        )
                        removed_legacy_content_copy = bool(
                            run.get("failedDeltaReconciliation") is True
                            and field not in after_fields
                            and (
                                field in current_source_fields
                                or field in projected_fields
                            )
                        )
                        if (
                            not exact_server_derived_change
                            and not exact_server_derived_removal
                            and not removed_redundant_source_copy
                            and not removed_legacy_content_copy
                            and not exact_projected_value
                            and not exact_current_source_value
                            and (
                                field not in after_fields
                                or after_fields[field] != before_fields[field]
                            )
                        ):
                            raise QualificationRunError(
                                f"Codex自動整備対象外fieldの変更を検出しました: "
                                f"{relative} / {field}"
                            )
                    elif field in after_fields:
                        if exact_projected_value or exact_current_source_value:
                            continue
                        if (
                            field in allowed_derived_fields
                            and after_fields[field] == allowed_derived_fields[field]
                        ):
                            continue
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
                            _source_binding_accepts_identity(
                                binding, identity(entry)
                            )
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
        list_group_ids: list[str] | None = None,
        update_target_ids: list[str] | None = None,
        question_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        selected_stage_ids = list(dict.fromkeys(stage_ids or [stage_id]))
        scope: dict[str, Any] = {}
        if list_group_ids is not None:
            scope["list_group_ids"] = list_group_ids
        if update_target_ids is not None:
            scope["update_target_ids"] = update_target_ids
        if question_ids is not None:
            scope["question_ids"] = question_ids
        if len(selected_stage_ids) > 1:
            plan = dict(
                self.workflow.plan_many(
                    qualification,
                    selected_stage_ids,
                    mode,
                    _dashboard_only=bool(resumed_from),
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
            if not resumed_from:
                plan["resolvableFailedDeltaPaths"] = self._resolvable_for_plan(
                    qualification,
                    list(plan.get("targetGroupIds") or []),
                    plan,
                )
                return plan
            previous = self.store.recover_interrupted_question_run_for_resume(
                qualification,
                resumed_from,
            )
            previous_scope = list(previous.get("scopeListGroupIds") or [])
            if (
                previous.get("kind") != "orchestration"
                or not _resume_orchestration_selections_match(
                    previous,
                    plan,
                    selected_stage_ids,
                    compare_update_targets=(
                        "selectedUpdateTargetIds" in previous
                        or update_target_ids is not None
                    ),
                )
                or str(previous.get("mode") or "") != mode
                or previous_scope != list(plan.get("scopeListGroupIds") or [])
                or (
                    ("questionIds" in previous or question_ids is not None)
                    and list(previous.get("questionIds") or [])
                    != list(plan.get("questionIds") or [])
                )
            ):
                raise QualificationRunError(
                    "再開元と工程、実行方式又は対象範囲が一致しません。"
                )
            resume_state = (
                str(previous.get("status") or ""),
                str(previous.get("queueStatus") or ""),
            )
            if resume_state not in {
                ("failed", "failed"),
                ("failed", "partial"),
                ("interrupted", "partial"),
                ("interrupted", "interrupted"),
                ("succeeded", "partial"),
                ("succeeded", "succeeded"),
            }:
                raise QualificationRunError(
                    "再開元のrun状態とqueue状態の組合せが不正です。"
                )
            self._assert_resume_safe(previous)
            previous_executions = previous.get("questionExecutions")
            if not isinstance(previous_executions, list):
                raise QualificationRunError("再開元に一問queueの記録がありません。")
            try:
                plan = resume_plan(
                    plan,
                    previous_executions,
                    unfinished_only=resume_state == ("succeeded", "partial"),
                )
            except QuestionWorkQueueError as exc:
                raise QualificationRunError(str(exc)) from exc
            _restore_resume_target_aliases(plan, previous)
            completed_scope_stage_ids = {
                str(phase.get("id") or "")
                for phase in previous.get("phaseExecutions") or []
                if isinstance(phase, Mapping)
                and str(phase.get("id") or "")
                in {"setup", "category_setup"}
                and str(phase.get("status") or "") == "succeeded"
            } - {""}
            plan["resumeCompletedScopeStageIds"] = sorted(
                completed_scope_stage_ids
            )
            if isinstance(plan.get("stagePlans"), list):
                plan["stagePlans"] = [
                    stage_plan
                    for stage_plan in plan["stagePlans"]
                    if isinstance(stage_plan, Mapping)
                    and str(stage_plan.get("stageId") or "")
                    not in completed_scope_stage_ids
                ]
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
            if not plan.get("stagePlans"):
                plan.update(
                    targetCount=0,
                    workItemCount=0,
                    targetQuestionKeys=[],
                    progressTargets=[],
                    targetRecordBindings=[],
                    targetRecordAliasGroups=[],
                    targetSourceRecordScopes={},
                    policyTargets={},
                    allowedPatchDirs=[],
                    allowedWriteAreas=[],
                    allowedPatchFiles=[],
                    allowedWriteFiles=[],
                    targetRecordScopes={},
                    resolvableFailedDeltaPaths=[],
                )
                return plan
            self._apply_plan_write_contract(plan)
            allowed_resume_paths = {
                str(value)
                for value in [
                    *(plan.get("allowedPatchFiles") or []),
                    *(plan.get("allowedWriteFiles") or []),
                ]
                if value
            }
            plan["resolvableFailedDeltaPaths"] = sorted(
                {
                    str(value)
                    for value in previous.get("resolvableFailedDeltaPaths") or []
                    if str(value) in allowed_resume_paths
                }
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
            or (
                ("selectedUpdateTargetIds" in previous or update_target_ids is not None)
                and list(previous.get("selectedUpdateTargetIds") or [])
                != list(plan.get("selectedUpdateTargetIds") or [])
            )
            or (
                ("questionIds" in previous or question_ids is not None)
                and list(previous.get("questionIds") or [])
                != list(plan.get("questionIds") or [])
            )
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
        previous: Mapping[str, Any],
    ) -> None:
        if previous.get("retrySafe") is False:
            raise QualificationRunError(
                str(previous.get("retryUnsafeReason") or "").strip()
                or "未確定差分の安全を確認できないため、この作業は再開できません。"
            )
        if previous.get("childRunIds"):
            raise QualificationRunError(
                "現行の一問state runにtop-level子run参照があるため再開できません。"
                "対象範囲から新規runとして開始してください。"
            )

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

        # The plan's write contract and targetRecordScopes already limit which
        # failed deltas it can resolve. Scanning once at qualification scope is
        # therefore equivalent to scanning the same historical manifests once
        # per selected group, while avoiding repeated reads of large manifests.
        return list(
            resolvable_failed_delta_paths(
                self.repo_root,
                qualification,
                plan,
            )
        )

    def _token(self, payload: Mapping[str, Any]) -> str:
        value = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hmac.new(self.secret, value.encode("utf-8"), hashlib.sha256).hexdigest()
