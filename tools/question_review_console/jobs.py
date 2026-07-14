from __future__ import annotations

import copy
import secrets
import threading
from datetime import datetime, timezone
from typing import Any, Callable


JobWorker = Callable[[Callable[[str], None]], dict[str, Any]]
ExclusiveWorker = Callable[[], dict[str, Any]]
REPOSITORY_OPERATION_KEY = "question-review-repository-operation"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


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
                "createdAt": _now(),
                "startedAt": None,
                "finishedAt": None,
                "result": None,
                "error": None,
            }
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

    def _execute(self, job_id: str, key: str, worker: JobWorker) -> None:
        with self._lock:
            self._jobs[job_id]["status"] = "running"
            self._jobs[job_id]["startedAt"] = _now()

        def emit(line: str) -> None:
            clean = str(line).strip()
            if not clean:
                return
            with self._lock:
                logs = self._jobs[job_id]["logs"]
                logs.append(clean[:2000])
                if len(logs) > 400:
                    del logs[: len(logs) - 400]

        try:
            result = worker(emit)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._jobs[job_id]["status"] = "failed"
                self._jobs[job_id]["error"] = str(exc)
                self._jobs[job_id]["finishedAt"] = _now()
        else:
            with self._lock:
                self._jobs[job_id]["status"] = "succeeded"
                self._jobs[job_id]["result"] = result
                self._jobs[job_id]["finishedAt"] = _now()
        finally:
            with self._lock:
                if self._active_keys.get(key) == job_id:
                    self._active_keys.pop(key, None)
