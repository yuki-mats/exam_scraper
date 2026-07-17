from __future__ import annotations

import copy
import re
import secrets
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Mapping


JobWorker = Callable[[Callable[[str], None]], dict[str, Any]]
ExclusiveWorker = Callable[[], dict[str, Any]]
REPOSITORY_OPERATION_KEY = "question-review-repository-operation"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _log_level(line: str) -> str:
    lowered = line.casefold()
    if lowered.startswith(("command failed", "error:", "job failed:")):
        return "error"
    if lowered.startswith(("warning:", "warn:")) or "警告" in line:
        return "warning"
    return "info"


_SENSITIVE_LOG_PATTERN = re.compile(
    r"(?i)(?:\b(?:authorization|api[_-]?key|token|secret|password|cookie)\b"
    r"\s*[:=]|--(?:api[_-]?key|token|secret|password|cookie)\s+\S+|"
    r"\bBearer\s+\S+|\bsk-[A-Za-z0-9_-]{8,}|"
    r"\bgh[pousr]_[A-Za-z0-9_]{8,}|\bAKIA[A-Z0-9]{12,})"
)
_LOG_LEVELS = {"info", "warning", "error"}


def _safe_log_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split())[:limit]
    if _SENSITIVE_LOG_PATTERN.search(text):
        return "<redacted sensitive content>"
    return text


def normalize_log_event(
    value: Mapping[str, Any],
    *,
    sequence: int,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """表示・永続化に共通する、安全な技術ログ形式へ限定する。"""

    observed_at = observed_at or _now()
    message = _safe_log_text(value.get("message"), limit=2000)
    level = str(value.get("level") or _log_level(message))
    if level not in _LOG_LEVELS:
        level = _log_level(message)
    event: dict[str, Any] = {
        "sequence": sequence,
        "observedAt": observed_at,
        # 既存のjob APIとの互換表示。永続ログの正本fieldはobservedAt。
        "at": observed_at,
        "level": level,
        "message": message,
    }
    command_status = _safe_log_text(value.get("commandStatus"), limit=40)
    if command_status:
        event["commandStatus"] = command_status
    exit_code = value.get("exitCode")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        event["exitCode"] = exit_code
    output_tail = _safe_log_text(value.get("outputTail"), limit=600)
    if output_tail:
        event["outputTail"] = output_tail
    raw_paths = value.get("changedPaths")
    if isinstance(raw_paths, (list, tuple, set)):
        changed_paths = [
            path
            for item in list(raw_paths)[:50]
            for path in [_safe_log_text(item, limit=300)]
            if path
        ]
        if changed_paths:
            event["changedPaths"] = changed_paths
    return event


class JobConflictError(RuntimeError):
    pass


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._active_keys: dict[str, str] = {}
        self._lock = threading.RLock()

    def start(self, *, kind: str, key: str, worker: JobWorker) -> dict[str, Any]:
        with self._lock:
            active_id = self._active_keys.get(key)
            if active_id:
                raise JobConflictError("問題整備システムで別の処理が実行中です。")
            job_id = secrets.token_urlsafe(12)
            job = {
                "jobId": job_id,
                "kind": kind,
                "key": key,
                "status": "queued",
                "logs": [],
                "logEntries": [],
                "logSequence": 0,
                "createdAt": _now(),
                "startedAt": None,
                "finishedAt": None,
                "lastActivityAt": None,
                "result": None,
                "error": None,
            }
            job["lastActivityAt"] = job["createdAt"]
            self._jobs[job_id] = job
            self._active_keys[key] = job_id

        thread = threading.Thread(
            target=self._execute,
            args=(job_id, key, worker),
            daemon=True,
            name=f"question-review-{kind}-{job_id}",
        )
        thread.start()
        return self.get(job_id)

    def run_exclusive(self, *, key: str, worker: ExclusiveWorker) -> dict[str, Any]:
        lease_id = f"sync:{secrets.token_urlsafe(12)}"
        with self._lock:
            if self._active_keys.get(key):
                raise JobConflictError("問題整備システムで別の処理が実行中です。")
            self._active_keys[key] = lease_id
        try:
            return worker()
        finally:
            with self._lock:
                if self._active_keys.get(key) == lease_id:
                    self._active_keys.pop(key, None)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(f"job not found: {job_id}")
            return copy.deepcopy(self._jobs[job_id])

    def touch(self, job_id: str) -> None:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(f"job not found: {job_id}")
            self._jobs[job_id]["lastActivityAt"] = _now()

    @staticmethod
    def _append_log(
        job: dict[str, Any], value: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        now = _now()
        job["lastActivityAt"] = now
        line = _safe_log_text(value.get("message"), limit=2000)
        if not line:
            return None
        logs = job["logs"]
        if logs and logs[-1] == line:
            return None
        sequence = int(job.get("logSequence") or 0) + 1
        job["logSequence"] = sequence
        logs.append(line)
        entries = job["logEntries"]
        entry = normalize_log_event(
            {**dict(value), "message": line},
            sequence=sequence,
            observed_at=now,
        )
        entries.append(entry)
        if len(logs) > 400:
            del logs[: len(logs) - 400]
        if len(entries) > 400:
            del entries[: len(entries) - 400]
        return entry

    def _execute(self, job_id: str, key: str, worker: JobWorker) -> None:
        with self._lock:
            self._jobs[job_id]["status"] = "running"
            self._jobs[job_id]["startedAt"] = _now()
            self._jobs[job_id]["lastActivityAt"] = self._jobs[job_id]["startedAt"]

        def emit(line: str) -> None:
            clean = str(line).strip()
            if not clean:
                return
            with self._lock:
                self._append_log(
                    self._jobs[job_id],
                    {"message": clean[:2000]},
                )

        def emit_event(value: Mapping[str, Any]) -> None:
            if not isinstance(value, Mapping):
                return
            with self._lock:
                self._append_log(self._jobs[job_id], value)

        setattr(emit, "heartbeat", lambda: self.touch(job_id))
        setattr(emit, "event", emit_event)

        try:
            result = worker(emit)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._jobs[job_id]["status"] = "failed"
                self._jobs[job_id]["error"] = str(exc)
                self._jobs[job_id]["finishedAt"] = _now()
                self._jobs[job_id]["lastActivityAt"] = self._jobs[job_id][
                    "finishedAt"
                ]
        else:
            with self._lock:
                self._jobs[job_id]["status"] = "succeeded"
                self._jobs[job_id]["result"] = result
                self._jobs[job_id]["finishedAt"] = _now()
                self._jobs[job_id]["lastActivityAt"] = self._jobs[job_id][
                    "finishedAt"
                ]
        finally:
            with self._lock:
                if self._active_keys.get(key) == job_id:
                    self._active_keys.pop(key, None)
