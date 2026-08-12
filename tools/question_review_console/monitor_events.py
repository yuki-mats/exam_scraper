from __future__ import annotations

import copy
import fcntl
import json
import math
import os
import queue
import re
import stat
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "monitor-event/v1"
MAX_RUN_EVENT_LOG_BYTES = 4 * 1024 * 1024
RUN_EVENT_LOG_BACKUPS = 1
MAX_OBSERVATION_RUNS = 64
MAX_RUN_SNAPSHOT_BYTES = 16 * 1024
MAX_PUBLIC_STREAM_TEXT = 4096
MAX_PUBLIC_TEXT = 4096
MAX_PUBLIC_SOURCE_TEXT = 16 * 1024
MAX_PUBLIC_COLLECTION_ITEMS = 64
MAX_MONITOR_BINDINGS = 512
MAX_PENDING_GAP_SEGMENTS = 64
MAX_RUN_ROUTES_PER_BINDING = 3
MAX_RUN_OBSERVATION_METRICS = (
    MAX_MONITOR_BINDINGS * MAX_RUN_ROUTES_PER_BINDING
)
DEFAULT_EVENT_LIFECYCLE_TIMEOUT_SECONDS = 5.0
MAX_DISK_BATCH_SIZE = 256
DISK_FAILURE_CATEGORIES = (
    "queue_full",
    "batch_setup",
    "lock_timeout",
    "open",
    "append",
    "snapshot",
    "rotation",
)


class DiskPersistenceError(OSError):
    def __init__(
        self,
        category: str,
        message: str,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(getattr(cause, "errno", None), message)
        self.category = category
EVENT_LIFECYCLE_WAIT_SLICE_SECONDS = 0.05
MAX_RUN_OBSERVATION_BYTES = (
    MAX_RUN_EVENT_LOG_BYTES * (1 + RUN_EVENT_LOG_BACKUPS)
    + (2 * MAX_RUN_SNAPSHOT_BYTES)
)
ALLOWED_METHODS = frozenset(
    {
        "error",
        "item/agentMessage/delta",
        "item/plan/delta",
        "item/reasoning/summaryPartAdded",
        "item/reasoning/summaryTextDelta",
        "item/started",
        "item/completed",
        "thread/archived",
        "thread/closed",
        "thread/deleted",
        "thread/started",
        "thread/status/changed",
        "thread/tokenUsage/updated",
        "thread/unarchived",
        "turn/plan/updated",
        "turn/started",
        "turn/completed",
    }
)
CORRELATION_SCALAR_FIELDS = (
    "qualification",
    "runId",
    "parentRunId",
    "childRunId",
    "questionId",
    "workItemKey",
    "stageId",
    "workType",
    "phase",
    "listGroupId",
    "sessionId",
)
CORRELATION_LIST_FIELDS = (
    "questionIds",
    "workItemKeys",
    "listGroupIds",
    "affectedRunIds",
)
TOKEN_FIELDS = frozenset(
    {
        "inputTokens",
        "cachedInputTokens",
        "cacheWriteInputTokens",
        "outputTokens",
        "reasoningOutputTokens",
        "totalTokens",
    }
)
PUBLIC_TOOL_TYPES = frozenset(
    {
        "commandExecution",
        "fileChange",
        "mcpToolCall",
        "dynamicToolCall",
        "collabAgentToolCall",
        "subAgentActivity",
        "webSearch",
        "imageView",
        "imageGeneration",
        "sleep",
        "enteredReviewMode",
        "exitedReviewMode",
        "contextCompaction",
    }
)
PUBLIC_TOOL_STATES = frozenset(
    {"started", "inProgress", "completed", "failed", "declined", "interrupted"}
)
PUBLIC_TURN_STATES = frozenset(
    {"started", "inProgress", "completed", "failed", "interrupted"}
)
PUBLIC_THREAD_STATES = frozenset(
    {
        "started",
        "notLoaded",
        "idle",
        "active",
        "systemError",
        "archived",
        "unarchived",
        "closed",
        "deleted",
    }
)
PUBLIC_ACTIVE_FLAGS = frozenset({"waitingOnApproval", "waitingOnUserInput"})
PUBLIC_PLAN_STATES = frozenset({"pending", "inProgress", "completed"})
PUBLIC_MESSAGE_PHASES = frozenset({"commentary", "final_answer"})
_SAFE_DISK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,299}$")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?"
    r"(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|$)",
    re.IGNORECASE | re.DOTALL,
)
_FILE_URL = re.compile(r"(?i)\bfile:/+(?:[^\s\"'<>\[\]{}()]+)")
_NAMED_SECRET = re.compile(
    r"(?i)(?<![A-Za-z0-9_.-])[\"']?("
    r"[A-Za-z0-9_.-]{0,64}(?:"
    r"password|passphrase|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"id[_-]?token|auth(?:orization)?|cookie|session[_-]?(?:id|token)|"
    r"client[_-]?secret|private[_-]?key|secret|token"
    r")[A-Za-z0-9_.-]{0,64}"
    r")[\"']?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_AUTHORIZATION_HEADER = re.compile(
    r"(?im)\b([\"']?(?:Authorization|Proxy-Authorization)[\"']?"
    r"\s*[:=]\s*)[^\r\n]*"
)
_COOKIE_HEADER = re.compile(
    r"(?im)\b([\"']?(?:Cookie|Set-Cookie)[\"']?\s*[:=]\s*)[^\r\n]*"
)
_BEARER_OR_TOKEN = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:(?:Bearer|Basic)\s+"
    r"[A-Za-z0-9._~+/=-]+|"
    r"sk-[A-Za-z0-9_-]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|"
    r"gh[pousr]_[A-Za-z0-9_]{8,}|"
    r"xox[baprs]-[A-Za-z0-9-]{8,}|"
    r"glpat-[A-Za-z0-9_-]{8,}|"
    r"AIza[A-Za-z0-9_-]{20,}|"
    r"AKIA[A-Z0-9]{12,})(?![A-Za-z0-9_])"
)
_URL_CREDENTIALS = re.compile(
    r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@]*:[^/\s@]+@"
)
_JWT = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"(?![A-Za-z0-9_-])"
)
_TRUNCATED_JWT_TAIL = re.compile(
    r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_.-]{8,}$"
)
_TRUNCATED_URL_CREDENTIALS = re.compile(
    r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@]*:[^/\s@]*$"
)
_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?i)(?<![\w])(?:[A-Z]:\\|\\\\)[^\s\"'<>\[\]{}()]+"
)
_POSIX_ABSOLUTE_PATH = re.compile(
    r"(?<![\w./+])/(?:"
    r"Users|home|root|workspace|workspaces|tmp|private|var|etc|opt|usr|"
    r"bin|sbin|lib|Library|Applications|Volumes|mnt|srv|dev|proc|sys|run|"
    r"app|data|nix"
    r")(?:/[^\s\"'<>\[\]{}()]*)?"
)


def _string(value: Any, limit: int = MAX_PUBLIC_TEXT) -> str:
    """Return public text after strict secret and absolute-path redaction."""
    source = str(value or "")
    source_truncated = len(source) > MAX_PUBLIC_SOURCE_TEXT
    text = source[:MAX_PUBLIC_SOURCE_TEXT]
    text = _PRIVATE_KEY.sub("<redacted-private-key>", text)
    text = _AUTHORIZATION_HEADER.sub(r"\1<redacted>", text)
    text = _COOKIE_HEADER.sub(r"\1<redacted>", text)
    text = _BEARER_OR_TOKEN.sub("<redacted>", text)
    text = _NAMED_SECRET.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    text = _URL_CREDENTIALS.sub(r"\1<redacted>:<redacted>@", text)
    text = _JWT.sub("<redacted-jwt>", text)
    if source_truncated:
        text = _TRUNCATED_JWT_TAIL.sub("<redacted-jwt>", text)
        text = _TRUNCATED_URL_CREDENTIALS.sub(
            r"\1<redacted>:<redacted>@",
            text,
        )
    text = _FILE_URL.sub("<absolute-path>", text)
    text = _WINDOWS_ABSOLUTE_PATH.sub("<absolute-path>", text)
    text = _POSIX_ABSOLUTE_PATH.sub("<absolute-path>", text)
    return text[:limit]


def _public_id(value: Any, limit: int = 300) -> str:
    return _string(value, limit).strip()


def _public_id_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for candidate in value[:MAX_PUBLIC_COLLECTION_ITEMS]:
        public = _public_id(candidate)
        if public:
            result.append(public)
    return result


def _safe_state(value: Any, allowed: frozenset[str], fallback: str) -> str:
    state = str(value or "")
    return state if state in allowed else fallback


class MonitorEventStore:
    """Best-effort, read-only projection of public App Server notifications.

    ``put_nowait`` is the producer boundary. Normalisation, redaction, replay,
    disk I/O and pre-bind buffering happen away from the App Server reader.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        queue_capacity: int = 4096,
        replay_capacity: int = 20_000,
        server_instance_id: str | None = None,
        start_worker: bool = True,
    ) -> None:
        self.path = path
        self.server_instance_id = server_instance_id or uuid.uuid4().hex
        self.monitor_model_requests = 0
        self._queue_capacity = max(1, queue_capacity)
        self._replay_capacity = max(1, replay_capacity)
        self._queue: queue.Queue[
            tuple[int, dict[str, Any], float]
        ] = queue.Queue(
            maxsize=self._queue_capacity
        )
        self._events: deque[dict[str, Any]] = deque(maxlen=self._replay_capacity)
        self._run_events: dict[tuple[str, str], deque[dict[str, Any]]] = {}
        self._bindings: dict[str, dict[str, Any]] = {}
        self._binding_order: deque[str] = deque()
        self._ordered_pending: deque[
            tuple[int, dict[str, Any], float]
        ] = deque()
        self._active_thread_routes: dict[
            str,
            tuple[str, str, frozenset[tuple[str, str]]],
        ] = {}
        self._active_route_snapshot: tuple[
            tuple[str, str, tuple[tuple[str, str], ...]],
            ...,
        ] = ()
        self._public_stream_text: dict[tuple[str, ...], str] = {}
        self._closed_public_streams: set[tuple[str, ...]] = set()
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._sequence = 0
        self._dropped = 0
        self._drop_lock = threading.Lock()
        self._next_notification_ordinal = 0
        self._last_accepted_notification_ordinal = 0
        self._last_finished_notification_ordinal = 0
        self._gap_segments: dict[tuple[Any, ...], list[Any]] = {}
        self._pending_gap_total = 0
        self._materialized_gap_total = 0
        self._gap_overflow_count = 0
        self._gap_overflow_boundary = 0
        self._scope_truncated = False
        self._scope_truncated_drops = 0
        self._disk_failures = 0
        self._disk_failure_categories = {
            category: {"count": 0, "last": None}
            for category in DISK_FAILURE_CATEGORIES
        }
        self._run_dropped: dict[tuple[str, str], int] = {}
        self._run_disk_failures: dict[tuple[str, str], int] = {}
        self._run_metric_order: deque[tuple[str, str]] = deque()
        self._event_disk_failures: dict[str, int] = {}
        self._retained_event_ids: set[str] = set()
        self._closed = threading.Event()
        self._worker: threading.Thread | None = None
        self._worker_failure: BaseException | None = None
        if start_worker:
            self._worker = threading.Thread(
                target=self._run,
                daemon=True,
                name="question-review-monitor-events",
            )
            self._worker.start()

    def put_nowait(self, message: Mapping[str, Any]) -> None:
        """Non-blocking observer boundary; never raises into the producer."""
        self.put_observed_nowait(message, time.time())

    def put_observed_nowait(
        self,
        message: Mapping[str, Any],
        observed_at: float,
    ) -> None:
        """Enqueue with the exact upstream Python receive timestamp."""

        try:
            notification = dict(message)
            received_at = float(observed_at)
            if not math.isfinite(received_at) or received_at < 0:
                raise ValueError("invalid monitor observation timestamp")
        except Exception:
            self._record_drop()
            return
        with self._drop_lock:
            if self._closed.is_set():
                return
            self._next_notification_ordinal += 1
            ordinal = self._next_notification_ordinal
            try:
                self._queue.put_nowait(
                    (ordinal, notification, received_at)
                )
                self._last_accepted_notification_ordinal = ordinal
            except Exception:
                self._record_drop_locked(
                    count=1,
                    boundary=self._last_accepted_notification_ordinal,
                )

    def observe(self, message: Mapping[str, Any]) -> None:
        """Compatibility alias; it retains the same non-blocking contract."""
        self.put_nowait(message)

    def record_observation_gap(
        self,
        count: int = 1,
        *,
        affected_routes: Any = None,
        scope_truncated: bool = False,
    ) -> None:
        """Record a drop in an upstream bounded adapter without blocking it."""

        self._record_drop(
            count=max(1, int(count)),
            affected_routes=affected_routes,
            scope_truncated=scope_truncated,
        )
        with self._condition:
            self._materialize_pending_gap_locked()

    def observation_routes_snapshot(
        self,
    ) -> tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...]:
        """Return an immutable, lock-free route snapshot for queue metadata."""

        return self._active_route_snapshot

    def bind(
        self,
        *,
        thread_id: str,
        session_id: str,
        turn_id: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        binding = self._public_binding(
            context or {},
            thread_id=thread_id,
            session_id=session_id,
            turn_id=turn_id,
        )
        self._install_binding_and_flush(thread_id, binding)

    def bind_runtime(
        self,
        context: Mapping[str, Any],
        thread_id: str,
        turn_id: str | None = None,
    ) -> None:
        self.bind(
            thread_id=thread_id,
            session_id=_public_id(context.get("sessionId")),
            turn_id=turn_id,
            context=context,
        )

    @staticmethod
    def _lifecycle_deadline(timeout: float) -> float:
        seconds = float(timeout)
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("monitor lifecycle timeout must be finite and nonnegative")
        return time.monotonic() + seconds

    @staticmethod
    def _remaining_lifecycle_time(deadline: float) -> float:
        return max(0.0, deadline - time.monotonic())

    @classmethod
    def _wait_queue_until(
        cls,
        work_queue: queue.Queue[Any],
        deadline: float,
        *,
        worker: threading.Thread | None,
        label: str,
    ) -> None:
        with work_queue.all_tasks_done:
            while work_queue.unfinished_tasks:
                if worker is not None and not worker.is_alive():
                    raise RuntimeError(
                        f"{label} worker stopped with "
                        f"{work_queue.unfinished_tasks} accepted event(s) pending"
                    )
                remaining = cls._remaining_lifecycle_time(deadline)
                if remaining <= 0:
                    raise TimeoutError(
                        f"{label} did not finish "
                        f"{work_queue.unfinished_tasks} accepted event(s) "
                        "before the lifecycle deadline"
                    )
                work_queue.all_tasks_done.wait(
                    timeout=min(
                        remaining,
                        EVENT_LIFECYCLE_WAIT_SLICE_SECONDS,
                    )
                )

    @classmethod
    def _join_worker_until(
        cls,
        worker: threading.Thread | None,
        deadline: float,
        *,
        label: str,
    ) -> None:
        if worker is None:
            return
        worker.join(timeout=cls._remaining_lifecycle_time(deadline))
        if worker.is_alive():
            raise TimeoutError(
                f"{label} worker did not stop before the lifecycle deadline"
            )

    def _drain_projection_until(self, deadline: float) -> None:
        self._raise_if_projection_worker_failed()
        self._wait_queue_until(
            self._queue,
            deadline,
            worker=self._worker,
            label="monitor projection",
        )
        self._raise_if_projection_worker_failed()
        if (
            self._worker is not None
            and not self._worker.is_alive()
            and not self._closed.is_set()
        ):
            raise RuntimeError("monitor projection worker stopped unexpectedly")

    def drain(
        self,
        timeout: float = DEFAULT_EVENT_LIFECYCLE_TIMEOUT_SECONDS,
    ) -> None:
        """Wait boundedly for accepted projection work, or raise explicitly."""

        self._drain_projection_until(self._lifecycle_deadline(timeout))

    def _close_projection_until(self, deadline: float) -> None:
        # Admission and closure share the drop lock. An event is therefore
        # either accepted before this boundary and drained, or rejected after
        # it; it cannot be stranded behind an already exited worker.
        with self._drop_lock:
            self._closed.set()
        with self._condition:
            self._materialize_pending_gap_locked()
            self._condition.notify_all()
        self._join_worker_until(
            self._worker,
            deadline,
            label="monitor projection",
        )
        self._wait_queue_until(
            self._queue,
            deadline,
            worker=self._worker,
            label="monitor projection",
        )
        self._raise_if_projection_worker_failed()

    def close(
        self,
        timeout: float = DEFAULT_EVENT_LIFECYCLE_TIMEOUT_SECONDS,
    ) -> None:
        """Stop boundedly; a normal return means accepted work was projected."""

        self._close_projection_until(self._lifecycle_deadline(timeout))

    def replay(self, cursor: str | None = None, *, limit: int = 500) -> dict[str, Any]:
        with self._lock:
            self._materialize_pending_gap_locked()
            return self._replay_locked(list(self._events), cursor, limit)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._materialize_pending_gap_locked()
            result = self._replay_locked(list(self._events), None, 5000)
            result["serverInstanceId"] = self.server_instance_id
            result["bindings"] = copy.deepcopy(self._bindings)
            return result

    def health(
        self,
        qualification: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Return observation health without copying replay events or bindings."""

        with self._lock:
            self._materialize_pending_gap_locked()
            scoped = qualification is not None and run_id is not None
            key = (str(qualification), str(run_id))
            scoped_events = (
                list(self._run_events.get(key, ())) if scoped else []
            )
            scoped_observation = (
                self._run_observation_locked(key, scoped_events)
                if scoped
                else None
            )
            dropped = (
                scoped_observation["droppedNotifications"]
                if scoped_observation is not None
                else self._dropped
            )
            disk_failures = (
                scoped_observation["diskFailures"]
                if scoped_observation is not None
                else self._disk_failures
            )
            scope_truncated = self._scope_truncated
            event_count = (
                len(self._events)
                if qualification is None or run_id is None
                else len(self._run_events.get((qualification, run_id), ()))
            )
            return {
                "schemaVersion": "monitor-observation-health/v1",
                "serverInstanceId": self.server_instance_id,
                "cursor": self._cursor(self._sequence),
                "observationHealth": {
                    "status": (
                        "degraded"
                        if dropped or disk_failures or scope_truncated
                        else "healthy"
                    ),
                    "droppedNotifications": dropped,
                    "diskFailures": disk_failures,
                    "scopeTruncated": scope_truncated,
                    "scopeTruncatedDrops": self._scope_truncated_drops,
                    "eventCount": event_count,
                },
                "monitorModelRequests": self.monitor_model_requests,
            }

    def _run_observation_locked(
        self,
        key: tuple[str, str],
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        replay_dropped = sum(
            int(event.get("payload", {}).get("droppedNotifications") or 0)
            for event in events
            if event.get("type") == "observationGap"
            and isinstance(event.get("payload"), Mapping)
        )
        replay_disk_failures = sum(
            self._event_disk_failures.get(
                str(event.get("eventId") or ""),
                0,
            )
            for event in events
        )
        return {
            "droppedNotifications": max(
                self._run_dropped.get(key, 0),
                replay_dropped,
            ),
            "diskFailures": max(
                self._run_disk_failures.get(key, 0),
                replay_disk_failures,
            ),
            "scopeTruncated": self._scope_truncated,
            "scopeTruncatedDrops": self._scope_truncated_drops,
            "eventCount": len(events),
        }

    def process_pending_for_test(self) -> None:
        while True:
            try:
                ordinal, message, observed_at = self._queue.get_nowait()
            except queue.Empty:
                with self._condition:
                    self._materialize_pending_gap_locked()
                return
            try:
                self._process(
                    message,
                    notification_ordinal=ordinal,
                    observed_at=observed_at,
                )
            except Exception:
                self._handle_processing_failure(ordinal)
            finally:
                self._queue.task_done()

    def _raise_if_projection_worker_failed(self) -> None:
        failure = self._worker_failure
        if failure is not None:
            raise RuntimeError(
                "monitor projection worker stopped with accepted events pending"
            ) from failure

    def _record_projection_worker_failure(self, failure: BaseException) -> None:
        with self._condition:
            self._worker_failure = failure
            self._condition.notify_all()

    def _run(self) -> None:
        try:
            while not self._closed.is_set() or not self._queue.empty():
                with self._condition:
                    self._materialize_pending_gap_locked()
                try:
                    ordinal, message, observed_at = self._queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                try:
                    try:
                        self._process(
                            message,
                            notification_ordinal=ordinal,
                            observed_at=observed_at,
                        )
                    except Exception:
                        self._handle_processing_failure(ordinal)
                except BaseException as exc:
                    self._record_projection_worker_failure(exc)
                    return
                finally:
                    self._queue.task_done()
        except BaseException as exc:
            self._record_projection_worker_failure(exc)

    def _process(
        self,
        message: Mapping[str, Any],
        *,
        notification_ordinal: int,
        observed_at: float,
    ) -> None:
        with self._condition:
            if len(self._ordered_pending) >= self._queue_capacity:
                dropped_ordinal, _dropped_message, _dropped_time = (
                    self._ordered_pending.popleft()
                )
                self._record_drop(
                    boundary=max(0, dropped_ordinal - 1),
                )
                with self._drop_lock:
                    self._last_finished_notification_ordinal = max(
                        self._last_finished_notification_ordinal,
                        dropped_ordinal,
                    )
                self._materialize_pending_gap_locked()
            self._ordered_pending.append(
                (
                    notification_ordinal,
                    dict(message),
                    observed_at,
                )
            )
            self._drain_ordered_pending_locked()

    def _handle_processing_failure(self, ordinal: int) -> None:
        with self._condition:
            self._ordered_pending = deque(
                entry
                for entry in self._ordered_pending
                if entry[0] != ordinal
            )
            self._record_drop(
                boundary=max(0, ordinal - 1),
            )
            self._finish_notification_locked(ordinal)

    def _install_binding_and_flush(
        self, thread_id: str, binding: Mapping[str, Any]
    ) -> None:
        thread_id = _public_id(thread_id)
        if not thread_id:
            return
        with self._condition:
            current = self._bindings.get(thread_id)
            if current is not None:
                for key, value in binding.items():
                    previous = current.get(key)
                    if previous is not None and previous != value:
                        raise ValueError(f"monitor binding mismatch: {key}")
                current.update(copy.deepcopy(dict(binding)))
            else:
                self._bindings[thread_id] = copy.deepcopy(dict(binding))
                self._binding_order.append(thread_id)
                current = self._bindings[thread_id]
            route_group = self._binding_route_group(current)
            if route_group is not None:
                self._active_thread_routes[thread_id] = route_group
            self._prune_bindings_locked()
            self._refresh_active_route_snapshot_locked()
            self._drain_ordered_pending_locked()

    def _drain_ordered_pending_locked(self) -> None:
        while self._ordered_pending:
            ordinal, message, observed_at = self._ordered_pending[0]
            method = str(message.get("method") or "")
            params = message.get("params")
            if method not in ALLOWED_METHODS or not isinstance(params, Mapping):
                self._ordered_pending.popleft()
                self._finish_notification_locked(ordinal)
                continue
            thread_id = self._thread_id(params)
            if not thread_id:
                self._ordered_pending.popleft()
                self._finish_notification_locked(ordinal)
                continue
            binding = self._bindings.get(thread_id)
            if binding is None:
                break
            self._ordered_pending.popleft()
            if method in {"turn/started", "thread/started"}:
                route_group = self._binding_route_group(binding)
                if (
                    route_group is not None
                    and self._active_thread_routes.get(thread_id) != route_group
                ):
                    self._active_thread_routes[thread_id] = route_group
                    self._refresh_active_route_snapshot_locked()
            event = self._project_event_locked(
                method,
                params,
                binding,
                observed_at=observed_at,
            )
            if event is not None:
                self._append_event_locked(event)
                # Sequence assignment and append-only disk order share this lock.
                self._append_disk(event)
            self._finish_notification_locked(ordinal)
            if self._terminal_notification(method, params):
                removed_route = self._active_thread_routes.pop(
                    thread_id,
                    None,
                )
                if removed_route is not None:
                    self._refresh_active_route_snapshot_locked()
                self._prune_bindings_locked()

    @staticmethod
    def _binding_route_group(
        binding: Mapping[str, Any],
    ) -> tuple[str, str, frozenset[tuple[str, str]]] | None:
        qualification = str(binding.get("qualification") or "")
        if not qualification:
            return None
        routes = {
            (qualification, run_id)
            for value in (
                binding.get("runId"),
                binding.get("parentRunId"),
                binding.get("childRunId"),
            )
            for run_id in [str(value or "")]
            if run_id
        }
        storage_run_id = str(
            binding.get("parentRunId")
            or binding.get("runId")
            or binding.get("childRunId")
            or ""
        )
        if not storage_run_id or not routes:
            return None
        return qualification, storage_run_id, frozenset(routes)

    @staticmethod
    def _terminal_notification(
        method: str,
        params: Mapping[str, Any],
    ) -> bool:
        if method in {
            "turn/completed",
            "thread/closed",
            "thread/deleted",
        }:
            return True
        if method != "error" or params.get("willRetry") is True:
            return False
        return True

    def _project_event_locked(
        self,
        method: str,
        params: Mapping[str, Any],
        binding: Mapping[str, Any],
        *,
        observed_at: float,
    ) -> dict[str, Any] | None:
        public = self._public_payload(method, params)
        if public is None:
            return None
        thread_id = self._thread_id(params)
        turn = params.get("turn")
        item = params.get("item")
        turn_id = _public_id(
            params.get("turnId")
            or (turn.get("id") if isinstance(turn, Mapping) else "")
            or (item.get("turnId") if isinstance(item, Mapping) else "")
        )
        item_id = _public_id(
            params.get("itemId")
            or (item.get("id") if isinstance(item, Mapping) else "")
        )
        correlation = copy.deepcopy(dict(binding))
        # A binding describes the run/thread relationship. Its turn is not
        # evidence that every later thread-level notification belongs to that
        # turn, so publish a turnId only when the source notification has one.
        correlation.pop("turnId", None)
        correlation.update(
            {
                **({"threadId": thread_id} if thread_id else {}),
                **({"turnId": turn_id} if turn_id else {}),
                **({"itemId": item_id} if item_id else {}),
            }
        )
        self._sequence += 1
        return self._envelope(
            str(public.pop("type")),
            public,
            sequence=self._sequence,
            correlation=correlation,
            occurred_at=self._occurred_at(method, params),
            observed_at=observed_at,
        )

    @staticmethod
    def _occurred_at(
        method: str,
        params: Mapping[str, Any],
    ) -> int | float | str | None:
        lifecycle: str
        nested_key: str
        if method in {
            "item/started",
            "turn/started",
            "thread/started",
        }:
            lifecycle = "started"
            nested_key = method.split("/", 1)[0]
        elif method in {
            "item/completed",
            "turn/completed",
            "thread/closed",
            "thread/deleted",
        }:
            lifecycle = "completed"
            nested_key = method.split("/", 1)[0]
        else:
            return None
        fields = (
            f"{lifecycle}AtMs",
            f"{lifecycle}At",
        )
        nested = params.get(nested_key)
        sources: list[Mapping[str, Any]] = [params]
        if isinstance(nested, Mapping):
            sources.append(nested)
        for field in fields:
            for source in sources:
                value = source.get(field)
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                    and value >= 0
                ):
                    return value
                if isinstance(value, str):
                    public = _string(value, 100).strip()
                    if public:
                        return public
        return None

    def _append_event_locked(self, event: dict[str, Any]) -> None:
        evicted = self._events[0] if len(self._events) == self._replay_capacity else None
        self._events.append(event)
        event_id = str(event.get("eventId") or "")
        if event_id:
            self._retained_event_ids.add(event_id)
        if evicted is not None:
            self._remove_from_run_indexes_locked(evicted)
            evicted_id = str(evicted.get("eventId") or "")
            self._retained_event_ids.discard(evicted_id)
            self._event_disk_failures.pop(evicted_id, None)
        for key in self._route_keys(event):
            self._run_events.setdefault(key, deque()).append(event)
        self._condition.notify_all()

    def _track_event_disk_failure_locked(
        self,
        event: Mapping[str, Any],
    ) -> None:
        event_id = str(event.get("eventId") or "")
        if event_id and event_id in self._retained_event_ids:
            self._event_disk_failures[event_id] = (
                self._event_disk_failures.get(event_id, 0) + 1
            )

    def _remove_from_run_indexes_locked(self, event: Mapping[str, Any]) -> None:
        event_id = event.get("eventId")
        for key in self._route_keys(event):
            indexed = self._run_events.get(key)
            if indexed and indexed[0].get("eventId") == event_id:
                indexed.popleft()
            if not indexed:
                self._run_events.pop(key, None)

    def _record_drop(
        self,
        *,
        count: int = 1,
        boundary: int | None = None,
        affected_routes: Any = None,
        scope_truncated: bool = False,
    ) -> None:
        with self._drop_lock:
            self._record_drop_locked(
                count=count,
                boundary=boundary,
                affected_routes=affected_routes,
                scope_truncated=scope_truncated,
            )

    def _record_drop_locked(
        self,
        *,
        count: int,
        boundary: int | None,
        affected_routes: Any = None,
        scope_truncated: bool = False,
    ) -> None:
        count = max(1, int(count))
        resolved_boundary = max(
            0,
            int(
                self._last_accepted_notification_ordinal
                if boundary is None
                else boundary
            ),
        )
        self._dropped += count
        self._pending_gap_total += count
        if scope_truncated:
            self._record_gap_overflow_locked(count, resolved_boundary)
            return
        if affected_routes is None:
            routes = self._active_route_snapshot
            route_identity: tuple[Any, ...] = (
                "active",
                id(routes),
            )
        else:
            routes = self._normalize_route_groups(affected_routes)
            route_identity = ("routes", routes)
        key = (resolved_boundary, *route_identity)
        existing = self._gap_segments.get(key)
        if existing is not None:
            existing[1] += count
            return
        if len(self._gap_segments) >= MAX_PENDING_GAP_SEGMENTS:
            self._record_gap_overflow_locked(count, resolved_boundary)
            return
        self._gap_segments[key] = [resolved_boundary, count, routes]

    def _record_gap_overflow_locked(
        self,
        count: int,
        boundary: int,
    ) -> None:
        self._gap_overflow_count += count
        self._gap_overflow_boundary = max(
            self._gap_overflow_boundary,
            boundary,
        )
        self._scope_truncated = True
        self._scope_truncated_drops += count

    def _materialize_pending_gap_locked(self) -> None:
        ready: list[
            tuple[
                int,
                int,
                int,
                bool,
                tuple[
                    tuple[str, str, tuple[tuple[str, str], ...]],
                    ...,
                ],
            ]
        ] = []
        with self._drop_lock:
            materializable: list[
                tuple[
                    int,
                    int,
                    bool,
                    tuple[
                        tuple[str, str, tuple[tuple[str, str], ...]],
                        ...,
                    ],
                ]
            ] = []
            for key, segment in tuple(self._gap_segments.items()):
                boundary, count, routes = segment
                if boundary > self._last_finished_notification_ordinal:
                    continue
                self._gap_segments.pop(key, None)
                materializable.append((boundary, count, False, routes))
            if (
                self._gap_overflow_count
                and self._gap_overflow_boundary
                <= self._last_finished_notification_ordinal
            ):
                materializable.append(
                    (
                        self._gap_overflow_boundary,
                        self._gap_overflow_count,
                        True,
                        (),
                    )
                )
                self._gap_overflow_count = 0
                self._gap_overflow_boundary = 0
            materializable.sort(
                key=lambda item: (item[0], item[2])
            )
            for boundary, count, truncated, routes in materializable:
                self._pending_gap_total -= count
                self._materialized_gap_total += count
                ready.append(
                    (
                        boundary,
                        count,
                        self._materialized_gap_total,
                        truncated,
                        routes,
                    )
                )
        for (
            _boundary,
            dropped,
            total_dropped,
            scope_truncated,
            affected_routes,
        ) in ready:
            route_groups: list[
                tuple[str, str, tuple[tuple[str, str], ...]] | None
            ] = (
                [None]
                if scope_truncated
                else list(affected_routes) or [None]
            )
            for route_group in route_groups:
                self._sequence += 1
                sequence = self._sequence
                if route_group is None:
                    correlation: dict[str, Any] = {}
                    indexed_routes: tuple[tuple[str, str], ...] = ()
                else:
                    qualification, storage_run_id, indexed_routes = route_group
                    correlation = {
                        "qualification": qualification,
                        "runId": storage_run_id,
                        "affectedRunIds": [
                            run_id for _qualification, run_id in indexed_routes
                        ],
                    }
                payload = {
                    "fromSequence": sequence,
                    "toSequence": sequence,
                    "droppedNotifications": dropped,
                    "totalDroppedNotifications": total_dropped,
                }
                if scope_truncated:
                    payload["scopeTruncated"] = True
                gap = self._envelope(
                    "observationGap",
                    payload,
                    sequence=sequence,
                    correlation=correlation,
                )
                self._append_event_locked(gap)
                if route_group is not None:
                    for route in indexed_routes:
                        self._run_dropped[route] = (
                            self._run_dropped.get(route, 0) + dropped
                        )
                        self._touch_run_metric_locked(route)
                    self._append_disk(gap)

    @staticmethod
    def _normalize_route_groups(
        value: Any,
    ) -> tuple[
        tuple[str, str, tuple[tuple[str, str], ...]],
        ...,
    ]:
        grouped: dict[tuple[str, str], set[tuple[str, str]]] = {}
        if not isinstance(value, (list, tuple, set, frozenset)):
            return ()
        for candidate in value:
            if not isinstance(candidate, (list, tuple)):
                continue
            if len(candidate) == 2 and candidate[0] and candidate[1]:
                qualification = str(candidate[0])
                storage_run_id = str(candidate[1])
                routes = {(qualification, storage_run_id)}
            elif (
                len(candidate) == 3
                and candidate[0]
                and candidate[1]
                and isinstance(candidate[2], (list, tuple, set, frozenset))
            ):
                qualification = str(candidate[0])
                storage_run_id = str(candidate[1])
                routes = {
                    (str(route[0]), str(route[1]))
                    for route in candidate[2]
                    if (
                        isinstance(route, (list, tuple))
                        and len(route) == 2
                        and route[0]
                        and route[1]
                    )
                }
            else:
                continue
            routes.add((qualification, storage_run_id))
            grouped.setdefault((qualification, storage_run_id), set()).update(
                route
                for route in routes
                if route[0] == qualification
            )
        return tuple(
            (
                qualification,
                storage_run_id,
                tuple(sorted(routes)),
            )
            for (qualification, storage_run_id), routes in sorted(
                grouped.items()
            )
        )

    def _finish_notification_locked(self, ordinal: int) -> None:
        with self._drop_lock:
            self._last_finished_notification_ordinal = max(
                self._last_finished_notification_ordinal,
                ordinal,
            )
        self._materialize_pending_gap_locked()

    def _refresh_active_route_snapshot_locked(self) -> None:
        grouped: dict[
            tuple[str, str],
            set[tuple[str, str]],
        ] = {}
        for qualification, storage_run_id, routes in (
            self._active_thread_routes.values()
        ):
            grouped.setdefault(
                (qualification, storage_run_id),
                set(),
            ).update(
                route
                for route in routes
                if route[0] == qualification
            )
        snapshot = tuple(
            (
                qualification,
                storage_run_id,
                tuple(sorted(routes)),
            )
            for (qualification, storage_run_id), routes in sorted(
                grouped.items()
            )
        )
        if snapshot == self._active_route_snapshot:
            return
        self._active_route_snapshot = snapshot

    def _prune_bindings_locked(self) -> None:
        removed_binding = False
        while len(self._bindings) > MAX_MONITOR_BINDINGS:
            removed = False
            for _index in range(len(self._binding_order)):
                candidate = self._binding_order.popleft()
                if candidate not in self._bindings:
                    continue
                if candidate in self._active_thread_routes:
                    self._binding_order.append(candidate)
                    continue
                self._bindings.pop(candidate, None)
                removed_binding = True
                removed = True
                break
            if removed:
                continue
            candidate = self._binding_order.popleft()
            binding = self._bindings.pop(candidate, None)
            route_group = (
                self._binding_route_group(binding)
                if isinstance(binding, Mapping)
                else None
            )
            self._active_thread_routes.pop(candidate, None)
            removed_binding = True
            if route_group is not None:
                self._record_drop(
                    affected_routes=(route_group,),
                )
        if removed_binding and self._run_metric_order:
            self._prune_run_metrics_locked()

    def _touch_run_metric_locked(self, key: tuple[str, str]) -> None:
        try:
            self._run_metric_order.remove(key)
        except ValueError:
            pass
        self._run_metric_order.append(key)
        self._prune_run_metrics_locked()

    def _retained_binding_routes_locked(self) -> set[tuple[str, str]]:
        return {
            route
            for binding in self._bindings.values()
            for route_group in [self._binding_route_group(binding)]
            if route_group is not None
            for route in route_group[2]
        }

    def _prune_run_metrics_locked(self) -> None:
        retained_routes = self._retained_binding_routes_locked()
        for candidate in tuple(self._run_metric_order):
            if candidate in retained_routes:
                continue
            self._run_metric_order.remove(candidate)
            self._run_dropped.pop(candidate, None)
            self._run_disk_failures.pop(candidate, None)
        while len(self._run_metric_order) > MAX_RUN_OBSERVATION_METRICS:
            candidate = self._run_metric_order.popleft()
            self._run_dropped.pop(candidate, None)
            self._run_disk_failures.pop(candidate, None)

    @staticmethod
    def _route_keys(event: Mapping[str, Any]) -> set[tuple[str, str]]:
        correlation = event.get("correlation")
        if not isinstance(correlation, Mapping):
            return set()
        qualification = str(correlation.get("qualification") or "")
        if not qualification:
            return set()
        return {
            (qualification, str(run_id))
            for run_id in (
                correlation.get("runId"),
                correlation.get("parentRunId"),
                correlation.get("childRunId"),
                *(
                    correlation.get("affectedRunIds")
                    if isinstance(
                        correlation.get("affectedRunIds"),
                        (list, tuple),
                    )
                    else ()
                ),
            )
            if isinstance(run_id, str) and run_id
        }

    def _public_payload(
        self, method: str, params: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        item = params.get("item")
        item = item if isinstance(item, Mapping) else {}
        if method == "item/agentMessage/delta":
            text = self._public_stream_delta(method, params)
            if text is None:
                return None
            return {
                "type": "agentMessage",
                "text": text,
            }
        if method == "item/reasoning/summaryTextDelta":
            text = self._public_stream_delta(method, params)
            if text is None:
                return None
            payload: dict[str, Any] = {
                "type": "reasoningSummary",
                "text": text,
            }
            if self._nonnegative_int(params.get("summaryIndex")) is not None:
                payload["summaryIndex"] = int(params["summaryIndex"])
            return payload
        if method == "item/reasoning/summaryPartAdded":
            summary_index = self._nonnegative_int(params.get("summaryIndex"))
            return {
                "type": "reasoningSummaryPart",
                **({"summaryIndex": summary_index} if summary_index is not None else {}),
            }
        if method == "item/plan/delta":
            text = self._public_stream_delta(method, params)
            if text is None:
                return None
            return {
                "type": "plan",
                "text": text,
            }
        if method == "turn/plan/updated":
            plan: list[dict[str, str]] = []
            raw_plan = params.get("plan")
            if isinstance(raw_plan, list):
                for value in raw_plan[:MAX_PUBLIC_COLLECTION_ITEMS]:
                    if not isinstance(value, Mapping):
                        continue
                    step = _string(value.get("step"))
                    status = str(value.get("status") or "")
                    if step and status in PUBLIC_PLAN_STATES:
                        plan.append({"step": step, "status": status})
            return {
                "type": "plan",
                "plan": plan,
                **(
                    {"explanation": _string(params.get("explanation"))}
                    if params.get("explanation") is not None
                    else {}
                ),
            }
        if method in {"item/started", "item/completed"}:
            item_type = str(item.get("type") or "")
            lifecycle = "completed" if method.endswith("completed") else "started"
            if item_type == "agentMessage":
                phase = str(item.get("phase") or "")
                return {
                    "type": "agentMessage",
                    "text": _string(item.get("text")),
                    **({"phase": phase} if phase in PUBLIC_MESSAGE_PHASES else {}),
                    "state": lifecycle,
                }
            if item_type == "plan":
                return {
                    "type": "plan",
                    "text": _string(item.get("text")),
                    "state": lifecycle,
                }
            if item_type == "reasoning":
                summary = item.get("summary")
                if isinstance(summary, list):
                    summaries = [
                        _string(value)
                        for value in summary[:MAX_PUBLIC_COLLECTION_ITEMS]
                    ]
                else:
                    summaries = []
                return {
                    "type": "reasoningSummary",
                    "summaryParts": summaries,
                    "state": lifecycle,
                }
            if item_type in PUBLIC_TOOL_TYPES:
                return {
                    "type": "toolState",
                    "toolType": item_type,
                    "state": _safe_state(
                        item.get("status"), PUBLIC_TOOL_STATES, lifecycle
                    ),
                }
            return None
        if method in {"turn/started", "turn/completed"}:
            turn = params.get("turn")
            turn = turn if isinstance(turn, Mapping) else {}
            fallback = "completed" if method.endswith("completed") else "started"
            return {
                "type": "turnState",
                "state": _safe_state(
                    turn.get("status"), PUBLIC_TURN_STATES, fallback
                ),
            }
        if method == "thread/started":
            thread = params.get("thread")
            thread = thread if isinstance(thread, Mapping) else {}
            status = self._thread_status(thread.get("status"), "started")
            return {"type": "threadState", **status}
        if method == "thread/status/changed":
            return {
                "type": "threadState",
                **self._thread_status(params.get("status"), "active"),
            }
        if method in {
            "thread/archived",
            "thread/unarchived",
            "thread/closed",
            "thread/deleted",
        }:
            return {"type": "threadState", "state": method.split("/")[1]}
        if method == "thread/tokenUsage/updated":
            usage = params.get("tokenUsage")
            usage = usage if isinstance(usage, Mapping) else {}
            public_usage = {
                "last": self._usage_breakdown(usage.get("last")),
                "total": self._usage_breakdown(usage.get("total")),
            }
            context_window = self._nonnegative_int(usage.get("modelContextWindow"))
            if context_window is not None:
                public_usage["modelContextWindow"] = context_window
            return {"type": "tokenUsage", "usage": public_usage}
        if method == "error":
            error = params.get("error")
            error = error if isinstance(error, Mapping) else {}
            return {
                "type": "error",
                "message": _string(error.get("message")),
                "willRetry": bool(params.get("willRetry") is True),
            }
        return None

    def _public_stream_delta(
        self,
        method: str,
        params: Mapping[str, Any],
    ) -> str | None:
        """Publish a redacted replacement, never independently joinable deltas."""

        thread_id = self._thread_id(params)
        turn = params.get("turn")
        item = params.get("item")
        turn_id = _public_id(
            params.get("turnId")
            or (turn.get("id") if isinstance(turn, Mapping) else "")
            or (item.get("turnId") if isinstance(item, Mapping) else "")
        )
        item_id = _public_id(
            params.get("itemId")
            or (item.get("id") if isinstance(item, Mapping) else "")
        )
        summary_index = self._nonnegative_int(params.get("summaryIndex"))
        key = (
            method,
            thread_id,
            turn_id,
            item_id,
            "" if summary_index is None else str(summary_index),
        )
        current = self._public_stream_text.get(key, "")
        if key in self._closed_public_streams:
            return None
        delta = str(params.get("delta") or "")
        combined = current + delta
        truncated = len(combined) > MAX_PUBLIC_STREAM_TEXT
        if truncated:
            combined = combined[:MAX_PUBLIC_STREAM_TEXT]
            self._closed_public_streams.add(key)
        if key not in self._public_stream_text and (
            len(self._public_stream_text) >= self._replay_capacity
        ):
            oldest = next(iter(self._public_stream_text))
            self._public_stream_text.pop(oldest, None)
            self._closed_public_streams.discard(oldest)
        self._public_stream_text[key] = combined
        public = _string(combined)
        if truncated:
            public += "\n…（実況プレビューは4,096文字で終了）"
        return public

    @staticmethod
    def _nonnegative_int(value: Any) -> int | None:
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        return None

    def _usage_breakdown(self, value: Any) -> dict[str, int]:
        if not isinstance(value, Mapping):
            return {}
        return {
            key: numeric
            for key in TOKEN_FIELDS
            if (numeric := self._nonnegative_int(value.get(key))) is not None
        }

    @staticmethod
    def _thread_status(value: Any, fallback: str) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {"state": fallback}
        state = str(value.get("type") or "")
        result: dict[str, Any] = {
            "state": state if state in PUBLIC_THREAD_STATES else fallback
        }
        flags = value.get("activeFlags")
        if result["state"] == "active" and isinstance(flags, list):
            public_flags = [
                str(flag) for flag in flags if str(flag) in PUBLIC_ACTIVE_FLAGS
            ]
            if public_flags:
                result["activeFlags"] = public_flags
        return result

    @staticmethod
    def _thread_id(params: Mapping[str, Any]) -> str:
        thread = params.get("thread")
        return _public_id(
            params.get("threadId")
            or (thread.get("id") if isinstance(thread, Mapping) else "")
        )

    @staticmethod
    def _public_binding(
        context: Mapping[str, Any],
        *,
        thread_id: str,
        session_id: str,
        turn_id: str | None,
    ) -> dict[str, Any]:
        binding: dict[str, Any] = {}
        for key in CORRELATION_SCALAR_FIELDS:
            if context.get(key) is not None:
                public = _public_id(context.get(key))
                if public:
                    binding[key] = public
        for key in CORRELATION_LIST_FIELDS:
            public_values = _public_id_list(context.get(key))
            if public_values:
                binding[key] = public_values
        binding.update(
            {
                "threadId": _public_id(thread_id),
                "sessionId": _public_id(session_id),
            }
        )
        if turn_id:
            binding["turnId"] = _public_id(turn_id)
        return binding

    def _append_disk(self, event: Mapping[str, Any]) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
            flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(self.path, flags, 0o600)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
        except Exception:
            with self._condition:
                self._disk_failures += 1
                self._track_event_disk_failure_locked(event)
                for route in self._route_keys(event):
                    self._run_disk_failures[route] = (
                        self._run_disk_failures.get(route, 0) + 1
                    )
                    self._touch_run_metric_locked(route)
                self._condition.notify_all()

    def _envelope(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        sequence: int,
        correlation: Mapping[str, Any] | None = None,
        occurred_at: int | float | str | None = None,
        observed_at: float | None = None,
    ) -> dict[str, Any]:
        envelope = {
            "schemaVersion": SCHEMA_VERSION,
            "eventId": f"{self.server_instance_id}:{sequence}",
            "serverInstanceId": self.server_instance_id,
            "sequence": sequence,
            "observedAt": time.time() if observed_at is None else observed_at,
            "type": event_type,
            "correlation": copy.deepcopy(dict(correlation or {})),
            "payload": copy.deepcopy(dict(payload)),
        }
        if occurred_at is not None:
            envelope["occurredAt"] = occurred_at
        return envelope

    def _replay_locked(
        self,
        events: list[dict[str, Any]],
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        latest = self._sequence
        after = self._parse_cursor(cursor)
        if after is not None and after > latest:
            after = -1
        earliest = int(self._events[0]["sequence"]) if self._events else latest + 1
        result: list[dict[str, Any]] = []
        if after is not None and after < earliest - 1:
            result.append(
                self._envelope(
                    "observationGap",
                    {"fromSequence": max(0, after + 1), "toSequence": earliest - 1},
                    sequence=earliest - 1,
                )
            )
        result.extend(
            event for event in events if int(event["sequence"]) > (after or 0)
        )
        result = result[: max(1, min(limit, 5000))]
        if result:
            next_sequence = int(result[-1]["sequence"])
        elif after is None or after < 0:
            next_sequence = latest
        else:
            next_sequence = after
        return {
            "schemaVersion": SCHEMA_VERSION,
            "events": copy.deepcopy(result),
            "cursor": self._cursor(next_sequence),
            "observation": {
                "droppedNotifications": self._dropped,
                "diskFailures": self._disk_failures,
                "scopeTruncated": self._scope_truncated,
                "scopeTruncatedDrops": self._scope_truncated_drops,
                "eventCount": len(events),
            },
            "monitorModelRequests": self.monitor_model_requests,
        }

    def _cursor(self, sequence: int) -> str:
        return f"{self.server_instance_id}:{max(0, sequence)}"

    def _parse_cursor(self, cursor: str | None) -> int | None:
        if not cursor:
            return None
        prefix, separator, sequence = cursor.rpartition(":")
        if separator != ":" or prefix != self.server_instance_id:
            return -1
        try:
            return max(0, int(sequence))
        except ValueError:
            return -1


class MonitorEventHub(MonitorEventStore):
    """Repository-scoped public contract used by the monitor HTTP API."""

    def __init__(
        self,
        repo_root: Path,
        *,
        queue_capacity: int = 4096,
        replay_capacity: int = 20_000,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self._disk_queue: queue.Queue[dict[str, Any]] = queue.Queue(
            maxsize=max(1, queue_capacity)
        )
        self._disk_closed = threading.Event()
        self._disk_worker: threading.Thread | None = None
        self._disk_worker_failure: BaseException | None = None
        self._disk_queue_peak = 0
        self._last_disk_batch_size = 0
        self._last_disk_batch_duration_ms = 0.0
        self._last_disk_lock_hold_ms = 0.0
        super().__init__(
            queue_capacity=queue_capacity,
            replay_capacity=replay_capacity,
        )
        self._disk_worker = threading.Thread(
            target=self._run_disk_writer,
            daemon=True,
            name="question-review-monitor-disk",
        )
        self._disk_worker.start()

    def _raise_if_disk_worker_failed(self) -> None:
        failure = self._disk_worker_failure
        if failure is not None:
            raise RuntimeError(
                "monitor disk worker stopped before accepted events were durable"
            ) from failure
        if (
            self._disk_worker is not None
            and not self._disk_worker.is_alive()
            and not self._disk_closed.is_set()
        ):
            raise RuntimeError("monitor disk worker stopped unexpectedly")

    def _drain_disk_until(self, deadline: float) -> None:
        self._raise_if_disk_worker_failed()
        self._wait_queue_until(
            self._disk_queue,
            deadline,
            worker=self._disk_worker,
            label="monitor disk persistence",
        )
        self._raise_if_disk_worker_failed()

    def drain(
        self,
        timeout: float = DEFAULT_EVENT_LIFECYCLE_TIMEOUT_SECONDS,
    ) -> None:
        """Return only after accepted events are durable, within ``timeout``."""

        deadline = self._lifecycle_deadline(timeout)
        self._drain_projection_until(deadline)
        self._drain_disk_until(deadline)

    def close(
        self,
        timeout: float = DEFAULT_EVENT_LIFECYCLE_TIMEOUT_SECONDS,
    ) -> None:
        """Stop boundedly; timeout/failure never masquerades as durability."""

        deadline = self._lifecycle_deadline(timeout)
        self._close_projection_until(deadline)
        # Projection is now stopped, so no new disk item can arrive after this
        # shutdown boundary. A timed-out writer may still finish and a caller
        # can retry close without losing its accepted queue item.
        self._disk_closed.set()
        self._drain_disk_until(deadline)
        self._join_worker_until(
            self._disk_worker,
            deadline,
            label="monitor disk",
        )
        self._raise_if_disk_worker_failed()

    def snapshot(
        self,
        qualification: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        if qualification is None or run_id is None:
            return super().snapshot()
        with self._lock:
            self._materialize_pending_gap_locked()
            key = (qualification, run_id)
            events = list(self._run_events.get(key, ()))
            result = self._replay_locked(events, None, 5000)
            result["observation"] = self._run_observation_locked(key, events)
            result["observation"]["diskTelemetry"] = self._disk_telemetry_locked()
            result["serverInstanceId"] = self.server_instance_id
            result["bindings"] = copy.deepcopy(
                {
                    thread_id: binding
                    for thread_id, binding in self._bindings.items()
                    for route_group in [self._binding_route_group(binding)]
                    if route_group is not None
                    and key in route_group[2]
                }
            )
            return result

    def health(
        self,
        qualification: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        result = super().health(qualification, run_id)
        with self._condition:
            result["observationHealth"]["diskTelemetry"] = (
                self._disk_telemetry_locked()
            )
        return result

    def events(
        self,
        qualification: str,
        run_id: str,
        *,
        after: str = "",
        limit: int = 200,
        wait_ms: int = 0,
    ) -> dict[str, Any]:
        key = (qualification, run_id)
        bounded_wait = max(0, min(wait_ms, 30_000)) / 1000
        deadline = time.monotonic() + bounded_wait
        with self._condition:
            self._materialize_pending_gap_locked()
            while bounded_wait > 0 and not self._has_result_locked(key, after):
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._closed.is_set():
                    break
                self._condition.wait(timeout=remaining)
            events = list(self._run_events.get(key, ()))
            result = self._replay_locked(events, after or None, limit)
            result["observation"] = self._run_observation_locked(key, events)
            return result

    def _has_result_locked(
        self, key: tuple[str, str], cursor: str | None
    ) -> bool:
        after = self._parse_cursor(cursor)
        indexed = self._run_events.get(key)
        if after is None:
            return bool(indexed)
        if after < 0 or after > self._sequence:
            return True
        earliest = (
            int(self._events[0]["sequence"]) if self._events else self._sequence + 1
        )
        if after < earliest - 1:
            return True
        return bool(indexed and int(indexed[-1]["sequence"]) > after)

    @staticmethod
    def _matches(event: Mapping[str, Any], qualification: str, run_id: str) -> bool:
        return (qualification, run_id) in MonitorEventStore._route_keys(event)

    def bind_runtime(
        self,
        context: Mapping[str, Any],
        thread_id: str,
        turn_id: str | None = None,
    ) -> None:
        binding = self._public_binding(
            context,
            thread_id=thread_id,
            session_id=_public_id(context.get("sessionId")),
            turn_id=turn_id,
        )
        self._install_binding_and_flush(thread_id, binding)

    @staticmethod
    def _safe_disk_id(value: Any) -> str | None:
        candidate = _public_id(value)
        if (
            not candidate
            or candidate in {".", ".."}
            or not _SAFE_DISK_ID.fullmatch(candidate)
        ):
            return None
        return candidate

    @staticmethod
    def _directory_flags() -> int:
        return (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )

    @classmethod
    def _open_directory_at(
        cls,
        parent_fd: int,
        name: str,
        *,
        create: bool,
    ) -> int:
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=parent_fd)
            except FileExistsError:
                pass
        descriptor = os.open(
            name,
            cls._directory_flags(),
            dir_fd=parent_fd,
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode):
            os.close(descriptor)
            raise OSError("monitor observation component is not a directory")
        return descriptor

    @classmethod
    def _open_run_directory_at(
        cls,
        qualification_fd: int,
        run_id: str,
        *,
        expected_identity: tuple[int, int] | None,
    ) -> int:
        if expected_identity is None:
            try:
                os.mkdir(run_id, 0o700, dir_fd=qualification_fd)
            except FileExistsError as error:
                raise OSError(
                    "monitor observation run appeared after capacity check"
                ) from error
        descriptor = cls._open_directory_at(
            qualification_fd,
            run_id,
            create=False,
        )
        opened = os.fstat(descriptor)
        if (
            expected_identity is not None
            and (opened.st_dev, opened.st_ino) != expected_identity
        ):
            os.close(descriptor)
            raise OSError("monitor observation run changed before append")
        current = os.stat(
            run_id,
            dir_fd=qualification_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino)
            != (opened.st_dev, opened.st_ino)
        ):
            os.close(descriptor)
            raise OSError("monitor observation run path is unstable")
        return descriptor

    @staticmethod
    def _run_entry_limit(name: str) -> int | None:
        if name in {
            "events.jsonl",
            f"events.jsonl.{RUN_EVENT_LOG_BACKUPS}",
        }:
            return MAX_RUN_EVENT_LOG_BYTES
        if name == "snapshot.json" or (
            name.startswith(".snapshot.") and name.endswith(".tmp")
        ):
            return MAX_RUN_SNAPSHOT_BYTES
        return None

    @classmethod
    def _inspect_run_directory_fd(cls, run_fd: int) -> bool:
        oversized = False
        for name in os.listdir(run_fd):
            limit = cls._run_entry_limit(name)
            if limit is None:
                raise OSError("unsafe monitor observation run entry")
            entry = os.stat(
                name,
                dir_fd=run_fd,
                follow_symlinks=False,
            )
            if not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1:
                raise OSError(
                    "monitor observation entry must be single-link regular file"
                )
            oversized = oversized or entry.st_size > limit
        return oversized

    @classmethod
    def _observation_candidates_fd(
        cls,
        observation_fd: int,
    ) -> list[tuple[int, str, str, bool, int, int]]:
        candidates: list[tuple[int, str, str, bool, int, int]] = []
        for qualification in os.listdir(observation_fd):
            if qualification == ".observation.lock":
                continue
            if cls._safe_disk_id(qualification) is None:
                raise OSError("unsafe monitor observation qualification")
            qualification_fd = cls._open_directory_at(
                observation_fd,
                qualification,
                create=False,
            )
            try:
                for run_id in os.listdir(qualification_fd):
                    if cls._safe_disk_id(run_id) is None:
                        raise OSError("unsafe monitor observation run")
                    run_fd = cls._open_directory_at(
                        qualification_fd,
                        run_id,
                        create=False,
                    )
                    try:
                        oversized = cls._inspect_run_directory_fd(run_fd)
                        run_stat = os.fstat(run_fd)
                        modified = run_stat.st_mtime_ns
                    finally:
                        os.close(run_fd)
                    candidates.append(
                        (
                            modified,
                            qualification,
                            run_id,
                            oversized,
                            run_stat.st_dev,
                            run_stat.st_ino,
                        )
                    )
            finally:
                os.close(qualification_fd)
        return candidates

    @classmethod
    def _prune_observation_run_fd(
        cls,
        observation_fd: int,
        qualification: str,
        run_id: str,
        *,
        expected_identity: tuple[int, int],
    ) -> None:
        qualification_fd = cls._open_directory_at(
            observation_fd,
            qualification,
            create=False,
        )
        run_fd: int | None = None
        try:
            run_fd = cls._open_directory_at(
                qualification_fd,
                run_id,
                create=False,
            )
            run_stat = os.fstat(run_fd)
            if (run_stat.st_dev, run_stat.st_ino) != expected_identity:
                raise OSError("monitor observation run changed during prune")
            cls._inspect_run_directory_fd(run_fd)
            for name in os.listdir(run_fd):
                os.unlink(name, dir_fd=run_fd)
            os.close(run_fd)
            run_fd = None
            os.rmdir(run_id, dir_fd=qualification_fd)
        finally:
            if run_fd is not None:
                os.close(run_fd)
            os.close(qualification_fd)
        try:
            os.rmdir(qualification, dir_fd=observation_fd)
        except OSError:
            pass

    @classmethod
    def _ensure_observation_run_capacity_fd(
        cls,
        observation_fd: int,
        qualification: str,
        run_id: str,
    ) -> tuple[int, int] | None:
        candidates = cls._observation_candidates_fd(observation_fd)
        current = (qualification, run_id)
        oversized = sorted(
            (
                candidate
                for candidate in candidates
                if candidate[3]
                and (candidate[1], candidate[2]) != current
            ),
            key=lambda candidate: (
                candidate[0],
                candidate[1],
                candidate[2],
            ),
        )
        for (
            _mtime,
            candidate_qualification,
            candidate_run,
            _oversized,
            candidate_device,
            candidate_inode,
        ) in oversized:
            cls._prune_observation_run_fd(
                observation_fd,
                candidate_qualification,
                candidate_run,
                expected_identity=(candidate_device, candidate_inode),
            )
        if oversized:
            candidates = cls._observation_candidates_fd(observation_fd)

        current_exists = any(
            (candidate[1], candidate[2]) == current
            for candidate in candidates
        )
        prune_count = max(
            0,
            len(candidates)
            + (0 if current_exists else 1)
            - max(1, MAX_OBSERVATION_RUNS),
        )
        safe_candidates = sorted(
            (
                candidate
                for candidate in candidates
                if (candidate[1], candidate[2]) != current
            ),
            key=lambda candidate: (
                candidate[0],
                candidate[1],
                candidate[2],
            ),
        )
        if len(safe_candidates) < prune_count:
            raise OSError("no safe monitor observation run can be pruned")
        for (
            _mtime,
            candidate_qualification,
            candidate_run,
            _oversized,
            candidate_device,
            candidate_inode,
        ) in safe_candidates[:prune_count]:
            cls._prune_observation_run_fd(
                observation_fd,
                candidate_qualification,
                candidate_run,
                expected_identity=(candidate_device, candidate_inode),
            )
        current_candidate = next(
            (
                candidate
                for candidate in candidates
                if (candidate[1], candidate[2]) == current
            ),
            None,
        )
        if current_candidate is None:
            return None
        return current_candidate[4], current_candidate[5]

    @classmethod
    def _ensure_observation_batch_capacity_fd(
        cls,
        observation_fd: int,
        routes: set[tuple[str, str]],
    ) -> dict[tuple[str, str], tuple[int, int] | None]:
        """Reserve all batch routes from one capacity-directory scan."""

        candidates = cls._observation_candidates_fd(observation_fd)
        retained = list(candidates)
        for candidate in sorted(candidates, key=lambda item: item[:3]):
            route = (candidate[1], candidate[2])
            if not candidate[3] or route in routes:
                continue
            cls._prune_observation_run_fd(
                observation_fd,
                candidate[1],
                candidate[2],
                expected_identity=(candidate[4], candidate[5]),
            )
            retained.remove(candidate)
        existing_routes = {(item[1], item[2]) for item in retained}
        required = len(retained) + len(routes - existing_routes)
        prune_count = max(0, required - max(1, MAX_OBSERVATION_RUNS))
        safe_candidates = sorted(
            (item for item in retained if (item[1], item[2]) not in routes),
            key=lambda item: (item[0], item[1], item[2]),
        )
        if len(safe_candidates) < prune_count:
            raise OSError("no safe monitor observation run can be pruned")
        for candidate in safe_candidates[:prune_count]:
            cls._prune_observation_run_fd(
                observation_fd,
                candidate[1],
                candidate[2],
                expected_identity=(candidate[4], candidate[5]),
            )
            retained.remove(candidate)
        identities = {
            (item[1], item[2]): (item[4], item[5]) for item in retained
        }
        return {route: identities.get(route) for route in routes}

    @classmethod
    def _cleanup_snapshot_temporaries_fd(cls, run_fd: int) -> None:
        for name in os.listdir(run_fd):
            if not (
                name.startswith(".snapshot.")
                and name.endswith(".tmp")
            ):
                continue
            entry = os.stat(
                name,
                dir_fd=run_fd,
                follow_symlinks=False,
            )
            if not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1:
                raise OSError("unsafe monitor snapshot temporary")
            os.unlink(name, dir_fd=run_fd)

    @classmethod
    def _prepare_run_event_log_fd(
        cls,
        run_fd: int,
        line_bytes: int,
    ) -> None:
        if line_bytes > MAX_RUN_EVENT_LOG_BYTES:
            raise OSError("monitor event exceeds bounded disk segment")
        current = "events.jsonl"
        backup = f"events.jsonl.{RUN_EVENT_LOG_BACKUPS}"

        stats: dict[str, os.stat_result] = {}
        for name in (current, backup):
            try:
                value = os.stat(
                    name,
                    dir_fd=run_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
                raise OSError(
                    "monitor event log must be single-link regular file"
                )
            stats[name] = value
        backup_stat = stats.get(backup)
        if (
            backup_stat is not None
            and backup_stat.st_size > MAX_RUN_EVENT_LOG_BYTES
        ):
            os.unlink(backup, dir_fd=run_fd)
        current_stat = stats.get(current)
        current_size = current_stat.st_size if current_stat is not None else 0
        if current_size + line_bytes <= MAX_RUN_EVENT_LOG_BYTES:
            return
        if current_stat is not None and current_size <= MAX_RUN_EVENT_LOG_BYTES:
            os.replace(
                current,
                backup,
                src_dir_fd=run_fd,
                dst_dir_fd=run_fd,
            )
        elif current_stat is not None:
            os.unlink(current, dir_fd=run_fd)

    def _append_disk(self, event: Mapping[str, Any]) -> None:
        """Queue best-effort persistence without blocking event projection."""

        correlation = event.get("correlation")
        qualification = (
            self._safe_disk_id(correlation.get("qualification"))
            if isinstance(correlation, Mapping)
            else None
        )
        run_id = (
            self._safe_disk_id(
                correlation.get("parentRunId") or correlation.get("runId")
            )
            if isinstance(correlation, Mapping)
            else None
        )
        if qualification is None or run_id is None:
            self._mark_disk_failure(event)
            return
        try:
            self._disk_queue.put_nowait(copy.deepcopy(dict(event)))
            with self._condition:
                self._disk_queue_peak = max(
                    self._disk_queue_peak,
                    self._disk_queue.qsize(),
                )
        except queue.Full as exc:
            self._mark_disk_failure(event, "queue_full", exc)
        except Exception as exc:
            self._mark_disk_failure(event, "batch_setup", exc)

    def _run_disk_writer(self) -> None:
        try:
            while not self._disk_closed.is_set() or not self._disk_queue.empty():
                try:
                    event = self._disk_queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                batch = [event]
                while len(batch) < MAX_DISK_BATCH_SIZE:
                    try:
                        batch.append(self._disk_queue.get_nowait())
                    except queue.Empty:
                        break
                try:
                    batch_started = time.monotonic()
                    with self._condition:
                        self._last_disk_batch_size = len(batch)
                    try:
                        # Keep the single-event seam for fault-injection tests and
                        # callers which intentionally replace it on the instance.
                        if "_write_disk_event" in self.__dict__:
                            for item in batch:
                                try:
                                    self._write_disk_event(item)
                                except Exception:
                                    self._mark_disk_failure(item, "append")
                        else:
                            self._write_disk_batch(batch)
                    except Exception as exc:
                        category = getattr(exc, "category", "batch_setup")
                        for item in batch:
                            self._mark_disk_failure(item, category, exc)
                except BaseException as exc:
                    with self._condition:
                        self._disk_worker_failure = exc
                        self._condition.notify_all()
                    return
                finally:
                    with self._condition:
                        self._last_disk_batch_duration_ms = round(
                            (time.monotonic() - batch_started) * 1000,
                            3,
                        )
                    for _item in batch:
                        self._disk_queue.task_done()
        except BaseException as exc:
            with self._condition:
                self._disk_worker_failure = exc
                self._condition.notify_all()

    def _mark_disk_failure(
        self,
        event: Mapping[str, Any],
        category: str = "append",
        error: BaseException | None = None,
    ) -> None:
        with self._condition:
            self._disk_failures += 1
            if category not in self._disk_failure_categories:
                category = "append"
            metadata = self._disk_failure_categories[category]
            metadata["count"] += 1
            correlation = event.get("correlation")
            route = None
            if isinstance(correlation, Mapping):
                qualification = self._safe_disk_id(correlation.get("qualification"))
                run_id = self._safe_disk_id(
                    correlation.get("parentRunId") or correlation.get("runId")
                )
                if qualification is not None and run_id is not None:
                    route = f"{qualification}/{run_id}"
            metadata["last"] = {
                "errno": getattr(error, "errno", None),
                "time": time.time(),
                "sequence": int(event.get("sequence") or 0),
                "route": route,
            }
            self._track_event_disk_failure_locked(event)
            for route in self._route_keys(event):
                self._run_disk_failures[route] = (
                    self._run_disk_failures.get(route, 0) + 1
                )
                self._touch_run_metric_locked(route)
            self._condition.notify_all()

    def _disk_telemetry_locked(self) -> dict[str, Any]:
        return {
            "queueCapacity": self._disk_queue.maxsize,
            "queueDepth": self._disk_queue.qsize(),
            "queuePeak": self._disk_queue_peak,
            "batchMax": MAX_DISK_BATCH_SIZE,
            "lastBatchSize": self._last_disk_batch_size,
            "lastBatchDurationMs": self._last_disk_batch_duration_ms,
            "lastLockHoldMs": self._last_disk_lock_hold_ms,
            "failureCategories": copy.deepcopy(self._disk_failure_categories),
        }

    @classmethod
    def _open_observation_lock_fd(cls, observation_fd: int) -> int:
        existing_flags = (
            os.O_RDWR
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor: int | None = None
        for _attempt in range(4):
            try:
                descriptor = os.open(
                    ".observation.lock",
                    existing_flags,
                    dir_fd=observation_fd,
                )
                break
            except FileNotFoundError:
                try:
                    descriptor = os.open(
                        ".observation.lock",
                        os.O_CREAT
                        | os.O_EXCL
                        | os.O_RDWR
                        | getattr(os, "O_CLOEXEC", 0),
                        0o600,
                        dir_fd=observation_fd,
                    )
                    break
                except FileExistsError:
                    continue
        if descriptor is None:
            raise OSError("monitor observation lock could not be opened")
        lock_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(lock_stat.st_mode)
            or lock_stat.st_nlink != 1
            or lock_stat.st_size != 0
        ):
            os.close(descriptor)
            raise OSError("unsafe monitor observation lock")
        return descriptor

    @staticmethod
    def _acquire_observation_lock(lock_fd: int) -> None:
        deadline = time.monotonic() + 0.1
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise OSError("monitor observation lock is busy")
                time.sleep(0.005)

    def _write_disk_event(self, event: Mapping[str, Any]) -> None:
        correlation = event.get("correlation")
        if not isinstance(correlation, Mapping):
            raise OSError("monitor event has no disk correlation")
        qualification = self._safe_disk_id(correlation.get("qualification"))
        run_id = self._safe_disk_id(
            correlation.get("parentRunId") or correlation.get("runId")
        )
        if qualification is None or run_id is None:
            raise OSError("monitor event has unsafe disk correlation")
        opened: list[int] = []
        lock_fd: int | None = None
        run_fd: int | None = None
        temporary_name: str | None = None
        try:
            batch_context = getattr(self, "_disk_batch_context", None)
            if batch_context is None:
                repo_fd = os.open(self.repo_root, self._directory_flags())
                opened.append(repo_fd)
                parent_fd = repo_fd
                for part in (
                    "output",
                    "question_review_console",
                    "runtime_observations",
                ):
                    child_fd = self._open_directory_at(
                        parent_fd,
                        part,
                        create=True,
                    )
                    opened.append(child_fd)
                    parent_fd = child_fd
                observation_fd = parent_fd
                lock_fd = self._open_observation_lock_fd(observation_fd)
                self._acquire_observation_lock(lock_fd)
            else:
                observation_fd = batch_context["observation_fd"]

            if batch_context is None:
                expected_run_identity = self._ensure_observation_run_capacity_fd(
                    observation_fd,
                    qualification,
                    run_id,
                )
            else:
                expected_run_identity = batch_context["expected_identities"][
                    (qualification, run_id)
                ]
            qualification_fd = self._open_directory_at(
                observation_fd,
                qualification,
                create=True,
            )
            opened.append(qualification_fd)
            run_fd = self._open_run_directory_at(
                qualification_fd,
                run_id,
                expected_identity=expected_run_identity,
            )
            opened.append(run_fd)
            if batch_context is not None and expected_run_identity is None:
                run_stat = os.fstat(run_fd)
                batch_context["expected_identities"][(qualification, run_id)] = (
                    run_stat.st_dev,
                    run_stat.st_ino,
                )
            self._cleanup_snapshot_temporaries_fd(run_fd)
            line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
            encoded_line = line.encode("utf-8")
            self._prepare_run_event_log_fd(run_fd, len(encoded_line))
            flags = (
                os.O_APPEND
                | os.O_CREAT
                | os.O_WRONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            fd = os.open(
                "events.jsonl",
                flags,
                0o600,
                dir_fd=run_fd,
            )
            try:
                opened_stat = os.fstat(fd)
                if (
                    not stat.S_ISREG(opened_stat.st_mode)
                    or opened_stat.st_nlink != 1
                ):
                    raise OSError(
                        "monitor event log must be a single-link regular file"
                    )
                original_size = opened_stat.st_size
                try:
                    remaining = memoryview(encoded_line)
                    while remaining:
                        written = os.write(fd, remaining)
                        if written <= 0:
                            raise OSError(
                                "monitor event log write made no progress"
                            )
                        remaining = remaining[written:]
                    final_size = os.fstat(fd).st_size
                    if (
                        final_size != original_size + len(encoded_line)
                        or final_size > MAX_RUN_EVENT_LOG_BYTES
                    ):
                        raise OSError(
                            "monitor event log append was not atomic-sized"
                        )
                except Exception:
                    os.ftruncate(fd, original_size)
                    raise
            finally:
                os.close(fd)
            with self._condition:
                observation_snapshot = {
                    "droppedNotifications": self._run_dropped.get(
                        (qualification, run_id),
                        0,
                    ),
                    "diskFailures": self._run_disk_failures.get(
                        (qualification, run_id),
                        0,
                    ),
                    "scopeTruncated": self._scope_truncated,
                    "scopeTruncatedDrops": self._scope_truncated_drops,
                    "eventCount": len(
                        self._run_events.get((qualification, run_id), ())
                    ),
                }
            if batch_context is not None and int(event["sequence"]) != batch_context[
                "last_sequence_by_route"
            ][(qualification, run_id)]:
                return
            snapshot = {
                "schemaVersion": SCHEMA_VERSION,
                "serverInstanceId": self.server_instance_id,
                "cursor": self._cursor(int(event["sequence"])),
                "monitorModelRequests": 0,
                "observation": observation_snapshot,
            }
            temporary_name = (
                f".snapshot.{self.server_instance_id}.{uuid.uuid4().hex}.tmp"
            )
            snapshot_bytes = json.dumps(
                snapshot,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(snapshot_bytes) > MAX_RUN_SNAPSHOT_BYTES:
                raise OSError("monitor observation snapshot exceeds limit")
            snapshot_flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            if hasattr(os, "O_NOFOLLOW"):
                snapshot_flags |= os.O_NOFOLLOW
            snapshot_fd = os.open(
                temporary_name,
                snapshot_flags,
                0o600,
                dir_fd=run_fd,
            )
            try:
                temporary_stat = os.fstat(snapshot_fd)
                if temporary_stat.st_nlink != 1:
                    raise OSError(
                        "monitor snapshot temporary must be single-link"
                    )
                remaining_snapshot = memoryview(snapshot_bytes)
                while remaining_snapshot:
                    written = os.write(snapshot_fd, remaining_snapshot)
                    if written <= 0:
                        raise OSError(
                            "monitor snapshot write made no progress"
                        )
                    remaining_snapshot = remaining_snapshot[written:]
            finally:
                os.close(snapshot_fd)
            os.replace(
                temporary_name,
                "snapshot.json",
                src_dir_fd=run_fd,
                dst_dir_fd=run_fd,
            )
            temporary_name = None
        except Exception:
            raise
        finally:
            if temporary_name is not None and run_fd is not None:
                try:
                    os.unlink(temporary_name, dir_fd=run_fd)
                except OSError:
                    pass
            if lock_fd is not None:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                try:
                    os.close(lock_fd)
                except OSError:
                    pass
            for descriptor in reversed(opened):
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def _write_disk_batch(self, events: list[Mapping[str, Any]]) -> None:
        """Persist one bounded batch under one repository observation lock."""

        opened: list[int] = []
        lock_fd: int | None = None
        try:
            repo_fd = os.open(self.repo_root, self._directory_flags())
            opened.append(repo_fd)
            parent_fd = repo_fd
            for part in (
                "output",
                "question_review_console",
                "runtime_observations",
            ):
                child_fd = self._open_directory_at(parent_fd, part, create=True)
                opened.append(child_fd)
                parent_fd = child_fd
            observation_fd = parent_fd
            lock_fd = self._open_observation_lock_fd(observation_fd)
            try:
                self._acquire_observation_lock(lock_fd)
            except Exception as exc:
                raise DiskPersistenceError(
                    "lock_timeout", "monitor observation lock unavailable", exc
                ) from exc
            lock_acquired = time.monotonic()
            last_sequence_by_route: dict[tuple[str, str], int] = {}
            events_by_route: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
            for event in events:
                correlation = event.get("correlation")
                if not isinstance(correlation, Mapping):
                    raise OSError("monitor event has no disk correlation")
                qualification = self._safe_disk_id(correlation.get("qualification"))
                run_id = self._safe_disk_id(
                    correlation.get("parentRunId") or correlation.get("runId")
                )
                if qualification is None or run_id is None:
                    raise OSError("monitor event has unsafe disk correlation")
                route = (qualification, run_id)
                last_sequence_by_route[route] = int(event["sequence"])
                events_by_route.setdefault(route, []).append(event)
            self._disk_batch_context = {
                "observation_fd": observation_fd,
                "last_sequence_by_route": last_sequence_by_route,
                "expected_identities": self._ensure_observation_batch_capacity_fd(
                    observation_fd,
                    set(last_sequence_by_route),
                ),
            }
            for route, route_events in events_by_route.items():
                try:
                    self._write_disk_route_batch(route, route_events)
                except Exception as exc:
                    category = getattr(exc, "category", "append")
                    for event in route_events:
                        self._mark_disk_failure(event, category, exc)
            with self._condition:
                self._last_disk_lock_hold_ms = round(
                    (time.monotonic() - lock_acquired) * 1000,
                    3,
                )
        finally:
            self.__dict__.pop("_disk_batch_context", None)
            if lock_fd is not None:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                finally:
                    os.close(lock_fd)
            for descriptor in reversed(opened):
                os.close(descriptor)

    def _write_disk_route_batch(
        self,
        route: tuple[str, str],
        events: list[Mapping[str, Any]],
    ) -> None:
        """Encode and append one route together, then replace one snapshot."""

        qualification, run_id = route
        context = self._disk_batch_context
        observation_fd = context["observation_fd"]
        opened: list[int] = []
        temporary_name: str | None = None
        run_fd: int | None = None
        try:
            try:
                qualification_fd = self._open_directory_at(
                    observation_fd, qualification, create=True
                )
                opened.append(qualification_fd)
                expected = context["expected_identities"][route]
                run_fd = self._open_run_directory_at(
                    qualification_fd, run_id, expected_identity=expected
                )
                opened.append(run_fd)
                if expected is None:
                    run_stat = os.fstat(run_fd)
                    context["expected_identities"][route] = (
                        run_stat.st_dev,
                        run_stat.st_ino,
                    )
                self._cleanup_snapshot_temporaries_fd(run_fd)
            except Exception as exc:
                raise DiskPersistenceError("open", "route setup failed", exc) from exc

            encoded = [
                (
                    json.dumps(
                        event,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8")
                for event in events
            ]
            index = 0
            while index < len(encoded):
                try:
                    current_size = os.stat(
                        "events.jsonl",
                        dir_fd=run_fd,
                        follow_symlinks=False,
                    ).st_size
                except FileNotFoundError:
                    current_size = 0
                available = MAX_RUN_EVENT_LOG_BYTES - current_size
                if available < len(encoded[index]):
                    try:
                        self._prepare_run_event_log_fd(run_fd, len(encoded[index]))
                    except Exception as exc:
                        raise DiskPersistenceError("rotation", "rotation failed", exc) from exc
                    available = MAX_RUN_EVENT_LOG_BYTES
                end = index
                chunk_size = 0
                while end < len(encoded) and chunk_size + len(encoded[end]) <= available:
                    chunk_size += len(encoded[end])
                    end += 1
                chunk = b"".join(encoded[index:end])
                flags = (
                    os.O_APPEND
                    | os.O_CREAT
                    | os.O_WRONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                )
                try:
                    fd = os.open("events.jsonl", flags, 0o600, dir_fd=run_fd)
                except Exception as exc:
                    raise DiskPersistenceError("open", "event log open failed", exc) from exc
                try:
                    opened_stat = os.fstat(fd)
                    if not stat.S_ISREG(opened_stat.st_mode) or opened_stat.st_nlink != 1:
                        raise DiskPersistenceError("open", "unsafe event log")
                    original_size = opened_stat.st_size
                    try:
                        remaining = memoryview(chunk)
                        while remaining:
                            written = os.write(fd, remaining)
                            if written <= 0:
                                raise OSError("event append made no progress")
                            remaining = remaining[written:]
                        os.fsync(fd)
                        if os.fstat(fd).st_size != original_size + len(chunk):
                            raise OSError("event append size mismatch")
                    except Exception as exc:
                        os.ftruncate(fd, original_size)
                        os.fsync(fd)
                        raise DiskPersistenceError("append", "event append failed", exc) from exc
                finally:
                    os.close(fd)
                index = end

            last_event = events[-1]
            with self._condition:
                observation = self._run_observation_locked(
                    route, list(self._run_events.get(route, ()))
                )
                observation["diskTelemetry"] = self._disk_telemetry_locked()
            snapshot = {
                "schemaVersion": SCHEMA_VERSION,
                "serverInstanceId": self.server_instance_id,
                "cursor": self._cursor(int(last_event["sequence"])),
                "monitorModelRequests": 0,
                "observation": observation,
            }
            snapshot_bytes = json.dumps(
                snapshot, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            if len(snapshot_bytes) > MAX_RUN_SNAPSHOT_BYTES:
                raise DiskPersistenceError("snapshot", "snapshot exceeds limit")
            temporary_name = f".snapshot.{self.server_instance_id}.{uuid.uuid4().hex}.tmp"
            flags = (
                os.O_CREAT
                | os.O_EXCL
                | os.O_WRONLY
                | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                snapshot_fd = os.open(temporary_name, flags, 0o600, dir_fd=run_fd)
                try:
                    remaining = memoryview(snapshot_bytes)
                    while remaining:
                        written = os.write(snapshot_fd, remaining)
                        if written <= 0:
                            raise OSError("snapshot write made no progress")
                        remaining = remaining[written:]
                    os.fsync(snapshot_fd)
                finally:
                    os.close(snapshot_fd)
                os.replace(temporary_name, "snapshot.json", src_dir_fd=run_fd, dst_dir_fd=run_fd)
                temporary_name = None
                os.fsync(run_fd)
            except Exception as exc:
                raise DiskPersistenceError("snapshot", "snapshot replace failed", exc) from exc
        finally:
            if temporary_name is not None and run_fd is not None:
                try:
                    os.unlink(temporary_name, dir_fd=run_fd)
                except OSError:
                    pass
            for descriptor in reversed(opened):
                os.close(descriptor)
