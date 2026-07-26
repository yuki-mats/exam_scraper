from __future__ import annotations

import copy
import json
import os
import queue
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "monitor-event/v1"
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
CORRELATION_LIST_FIELDS = ("questionIds", "workItemKeys", "listGroupIds")
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
    r"-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_FILE_URL = re.compile(r"(?i)\bfile:/+(?:[^\s\"'<>\[\]{}()]+)")
_NAMED_SECRET = re.compile(
    r"(?i)[\"']?("
    r"[A-Za-z0-9_.-]*(?:"
    r"password|passphrase|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"id[_-]?token|auth(?:orization)?|cookie|session[_-]?(?:id|token)|"
    r"client[_-]?secret|private[_-]?key|secret|token"
    r")[A-Za-z0-9_.-]*"
    r")[\"']?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_BEARER_OR_TOKEN = re.compile(
    r"(?i)\b(?:Bearer\s+\S+|"
    r"sk-[A-Za-z0-9_-]{8,}|"
    r"gh[pousr]_[A-Za-z0-9_]{8,}|"
    r"xox[baprs]-[A-Za-z0-9-]{8,}|"
    r"glpat-[A-Za-z0-9_-]{8,}|"
    r"AIza[A-Za-z0-9_-]{20,}|"
    r"AKIA[A-Z0-9]{12,})\b"
)
_URL_CREDENTIALS = re.compile(
    r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@]*:[^/\s@]+@"
)
_JWT = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"(?![A-Za-z0-9_-])"
)
_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?i)(?<![\w])(?:[A-Z]:\\|\\\\)[^\s\"'<>\[\]{}()]+"
)
_POSIX_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_./])/(?!/)[^\s\"'<>\[\]{}()]+"
)


def _string(value: Any, limit: int = 100_000) -> str:
    """Return public text after strict secret and absolute-path redaction."""
    text = str(value or "")
    text = _PRIVATE_KEY.sub("<redacted-private-key>", text)
    text = _NAMED_SECRET.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    text = _BEARER_OR_TOKEN.sub("<redacted>", text)
    text = _URL_CREDENTIALS.sub(r"\1<redacted>:<redacted>@", text)
    text = _JWT.sub("<redacted-jwt>", text)
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
    for candidate in value[:200]:
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
        self._queue: queue.Queue[tuple[int, dict[str, Any]]] = queue.Queue(
            maxsize=self._queue_capacity
        )
        self._events: deque[dict[str, Any]] = deque(maxlen=self._replay_capacity)
        self._gap_events: deque[dict[str, Any]] = deque(
            maxlen=self._replay_capacity
        )
        self._run_events: dict[tuple[str, str], deque[dict[str, Any]]] = {}
        self._bindings: dict[str, dict[str, Any]] = {}
        self._pending_unbound: dict[str, deque[dict[str, Any]]] = {}
        self._pending_unbound_count = 0
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._sequence = 0
        self._dropped = 0
        self._drop_lock = threading.Lock()
        self._next_notification_ordinal = 0
        self._last_accepted_notification_ordinal = 0
        self._last_finished_notification_ordinal = 0
        self._gap_segments: deque[list[int]] = deque()
        self._disk_failures = 0
        self._closed = threading.Event()
        self._worker: threading.Thread | None = None
        if start_worker:
            self._worker = threading.Thread(
                target=self._run,
                daemon=True,
                name="question-review-monitor-events",
            )
            self._worker.start()

    def put_nowait(self, message: Mapping[str, Any]) -> None:
        """Non-blocking observer boundary; never raises into the producer."""
        try:
            notification = dict(message)
        except Exception:
            self._record_drop()
            return
        with self._drop_lock:
            self._next_notification_ordinal += 1
            ordinal = self._next_notification_ordinal
            try:
                self._queue.put_nowait((ordinal, notification))
                self._last_accepted_notification_ordinal = ordinal
            except Exception:
                self._record_drop_locked(
                    count=1,
                    boundary=self._last_accepted_notification_ordinal,
                )

    def observe(self, message: Mapping[str, Any]) -> None:
        """Compatibility alias; it retains the same non-blocking contract."""
        self.put_nowait(message)

    def record_observation_gap(self, count: int = 1) -> None:
        """Record a drop in an upstream bounded adapter without blocking it."""

        self._record_drop(count=max(1, int(count)))

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

    def drain(self) -> None:
        self._queue.join()

    def close(self) -> None:
        self._closed.set()
        with self._condition:
            self._materialize_pending_gap_locked()
            self._condition.notify_all()
        if self._worker is not None:
            self._worker.join(timeout=1)

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
            dropped = self._dropped
            disk_failures = self._disk_failures
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
                        "degraded" if dropped or disk_failures else "healthy"
                    ),
                    "droppedNotifications": dropped,
                    "diskFailures": disk_failures,
                    "eventCount": event_count,
                },
                "monitorModelRequests": self.monitor_model_requests,
            }

    def process_pending_for_test(self) -> None:
        while True:
            try:
                ordinal, message = self._queue.get_nowait()
            except queue.Empty:
                with self._condition:
                    self._materialize_pending_gap_locked()
                return
            try:
                self._process(message, notification_ordinal=ordinal)
            except Exception:
                self._record_drop(boundary=ordinal)
            finally:
                self._queue.task_done()
                self._notification_finished_for_gap(ordinal)

    def _run(self) -> None:
        while not self._closed.is_set() or not self._queue.empty():
            with self._condition:
                self._materialize_pending_gap_locked()
            try:
                ordinal, message = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                self._process(message, notification_ordinal=ordinal)
            except Exception:
                self._record_drop(boundary=ordinal)
            finally:
                self._queue.task_done()
                self._notification_finished_for_gap(ordinal)

    def _process(
        self,
        message: Mapping[str, Any],
        *,
        notification_ordinal: int,
    ) -> None:
        method = str(message.get("method") or "")
        if method not in ALLOWED_METHODS:
            return
        params = message.get("params")
        if not isinstance(params, Mapping):
            return
        thread_id = self._thread_id(params)
        if not thread_id:
            return
        event: dict[str, Any] | None = None
        with self._condition:
            binding = self._bindings.get(thread_id)
            if binding is None:
                self._defer_unbound_locked(
                    thread_id,
                    message,
                    notification_ordinal=notification_ordinal,
                )
                return
            event = self._project_event_locked(method, params, binding)
            if event is not None:
                self._append_event_locked(event)
                # Sequence assignment and append-only disk order share this lock.
                self._append_disk(event)

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
                current = self._bindings[thread_id]
            pending = list(self._pending_unbound.pop(thread_id, ()))
            self._pending_unbound_count -= len(pending)
            for message in pending:
                method = str(message.get("method") or "")
                params = message.get("params")
                if method not in ALLOWED_METHODS or not isinstance(params, Mapping):
                    continue
                event = self._project_event_locked(method, params, current)
                if event is not None:
                    self._append_event_locked(event)
                    self._append_disk(event)

    def _defer_unbound_locked(
        self,
        thread_id: str,
        message: Mapping[str, Any],
        *,
        notification_ordinal: int,
    ) -> None:
        if self._pending_unbound_count >= self._queue_capacity:
            self._record_drop(boundary=notification_ordinal)
            return
        pending = self._pending_unbound.setdefault(thread_id, deque())
        pending.append(dict(message))
        self._pending_unbound_count += 1

    def _project_event_locked(
        self,
        method: str,
        params: Mapping[str, Any],
        binding: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        public = self._public_payload(method, params)
        if public is None:
            return None
        thread_id = self._thread_id(params)
        turn = params.get("turn")
        turn_id = _public_id(
            params.get("turnId")
            or (turn.get("id") if isinstance(turn, Mapping) else "")
        )
        item = params.get("item")
        item_id = _public_id(
            params.get("itemId")
            or (item.get("id") if isinstance(item, Mapping) else "")
        )
        self._sequence += 1
        return self._envelope(
            str(public.pop("type")),
            public,
            sequence=self._sequence,
            correlation={
                **copy.deepcopy(dict(binding)),
                **({"threadId": thread_id} if thread_id else {}),
                **({"turnId": turn_id} if turn_id else {}),
                **({"itemId": item_id} if item_id else {}),
            },
        )

    def _append_event_locked(self, event: dict[str, Any]) -> None:
        evicted = self._events[0] if len(self._events) == self._replay_capacity else None
        self._events.append(event)
        if evicted is not None:
            self._remove_from_run_indexes_locked(evicted)
        if event.get("type") == "observationGap":
            self._gap_events.append(event)
        for key in self._route_keys(event):
            self._run_events.setdefault(key, deque()).append(event)
        self._condition.notify_all()

    def _remove_from_run_indexes_locked(self, event: Mapping[str, Any]) -> None:
        event_id = event.get("eventId")
        if event.get("type") == "observationGap":
            if self._gap_events and self._gap_events[0].get("eventId") == event_id:
                self._gap_events.popleft()
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
    ) -> None:
        with self._drop_lock:
            self._record_drop_locked(count=count, boundary=boundary)

    def _record_drop_locked(
        self,
        *,
        count: int,
        boundary: int | None,
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
        for index, segment in enumerate(self._gap_segments):
            if segment[0] == resolved_boundary:
                segment[1] += count
                return
            if segment[0] > resolved_boundary:
                self._gap_segments.insert(index, [resolved_boundary, count])
                return
        self._gap_segments.append([resolved_boundary, count])

    def _materialize_pending_gap_locked(self) -> None:
        ready: list[tuple[int, int, int]] = []
        with self._drop_lock:
            cumulative = self._dropped - sum(
                segment[1] for segment in self._gap_segments
            )
            while (
                self._gap_segments
                and self._gap_segments[0][0]
                <= self._last_finished_notification_ordinal
            ):
                boundary, count = self._gap_segments.popleft()
                cumulative += count
                ready.append((boundary, count, cumulative))
        for _boundary, dropped, total_dropped in ready:
            self._sequence += 1
            sequence = self._sequence
            self._append_event_locked(
                self._envelope(
                    "observationGap",
                    {
                        "fromSequence": sequence,
                        "toSequence": sequence,
                        "droppedNotifications": dropped,
                        "totalDroppedNotifications": total_dropped,
                    },
                    sequence=sequence,
                )
            )

    def _notification_finished_for_gap(self, ordinal: int) -> None:
        with self._condition:
            with self._drop_lock:
                self._last_finished_notification_ordinal = max(
                    self._last_finished_notification_ordinal,
                    ordinal,
                )
            self._materialize_pending_gap_locked()

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
            )
            if isinstance(run_id, str) and run_id
        }

    def _public_payload(
        self, method: str, params: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        item = params.get("item")
        item = item if isinstance(item, Mapping) else {}
        if method == "item/agentMessage/delta":
            return {"type": "agentMessage", "delta": _string(params.get("delta"))}
        if method == "item/reasoning/summaryTextDelta":
            payload: dict[str, Any] = {
                "type": "reasoningSummary",
                "delta": _string(params.get("delta")),
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
            return {"type": "plan", "delta": _string(params.get("delta"))}
        if method == "turn/plan/updated":
            plan: list[dict[str, str]] = []
            raw_plan = params.get("plan")
            if isinstance(raw_plan, list):
                for value in raw_plan[:200]:
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
                    summaries = [_string(value) for value in summary[:200]]
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
                self._condition.notify_all()

    def _envelope(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        sequence: int,
        correlation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "eventId": f"{self.server_instance_id}:{sequence}",
            "serverInstanceId": self.server_instance_id,
            "sequence": sequence,
            "observedAt": time.time(),
            "type": event_type,
            "correlation": copy.deepcopy(dict(correlation or {})),
            "payload": copy.deepcopy(dict(payload)),
        }

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
        super().__init__(
            queue_capacity=queue_capacity,
            replay_capacity=replay_capacity,
        )

    def snapshot(
        self,
        qualification: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        if qualification is None or run_id is None:
            return super().snapshot()
        with self._lock:
            self._materialize_pending_gap_locked()
            events = sorted(
                [
                    *self._run_events.get((qualification, run_id), ()),
                    *self._gap_events,
                ],
                key=lambda event: int(event["sequence"]),
            )
            result = self._replay_locked(events, None, 5000)
            result["serverInstanceId"] = self.server_instance_id
            result["bindings"] = copy.deepcopy(self._bindings)
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
            events = sorted(
                [*self._run_events.get(key, ()), *self._gap_events],
                key=lambda event: int(event["sequence"]),
            )
            return self._replay_locked(events, after or None, limit)

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
        return bool(
            (indexed and int(indexed[-1]["sequence"]) > after)
            or (
                self._gap_events
                and int(self._gap_events[-1]["sequence"]) > after
            )
        )

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

    def _append_disk(self, event: Mapping[str, Any]) -> None:
        correlation = event.get("correlation")
        if not isinstance(correlation, Mapping):
            return
        qualification = self._safe_disk_id(correlation.get("qualification"))
        run_id = self._safe_disk_id(
            correlation.get("parentRunId") or correlation.get("runId")
        )
        if qualification is None or run_id is None:
            return
        try:
            observation_root = (
                self.repo_root
                / "output/question_review_console/runtime_observations"
            ).resolve()
            if not observation_root.is_relative_to(self.repo_root):
                raise OSError("monitor observation root escaped repository")
            directory = (observation_root / qualification / run_id).resolve()
            if not directory.is_relative_to(observation_root):
                raise OSError("monitor observation directory escaped root")
            directory.mkdir(parents=True, exist_ok=True)
            line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
            flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(directory / "events.jsonl", flags, 0o600)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
            snapshot = {
                "schemaVersion": SCHEMA_VERSION,
                "serverInstanceId": self.server_instance_id,
                "cursor": self._cursor(int(event["sequence"])),
                "monitorModelRequests": 0,
                "observation": {
                    "droppedNotifications": self._dropped,
                    "diskFailures": self._disk_failures,
                },
            }
            temporary = directory / f".snapshot.{self.server_instance_id}.tmp"
            temporary.write_text(
                json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary, directory / "snapshot.json")
        except Exception:
            with self._condition:
                self._disk_failures += 1
                self._condition.notify_all()
