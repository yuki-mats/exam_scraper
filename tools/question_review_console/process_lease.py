from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any


class ProcessLeaseError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


class ExclusiveFileLease:
    """A non-blocking process lease whose lifetime is the open descriptor."""

    def __init__(self, path: Path, *, purpose: str):
        self.path = path.resolve()
        self.purpose = purpose
        self._handle: IO[str] | None = None

    def acquire(self) -> "ExclusiveFileLease":
        if self._handle is not None:
            raise ProcessLeaseError("process leaseは取得済みです。")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            owner = handle.read().strip()
            handle.close()
            suffix = f" owner={owner}" if owner else ""
            raise ProcessLeaseError(
                f"{self.purpose}は別processが所有しています。{suffix}"
            ) from exc
        payload: dict[str, Any] = {
            "purpose": self.purpose,
            "pid": os.getpid(),
            "acquiredAt": _now(),
        }
        handle.seek(0)
        handle.truncate()
        handle.write(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())
        self._handle = handle
        return self

    def close(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self) -> "ExclusiveFileLease":
        return self.acquire()

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


def review_console_process_lease(repo_root: Path) -> ExclusiveFileLease:
    return ExclusiveFileLease(
        repo_root.resolve()
        / "output"
        / "question_review_console"
        / ".review-ui.lock",
        purpose="問題整備システム",
    )


def qualification_run_lease(
    repo_root: Path,
    qualification: str,
) -> ExclusiveFileLease:
    safe = str(qualification).strip()
    if (
        not safe
        or safe in {".", ".."}
        or any(
            not (character.isalnum() or character in "-._")
            for character in safe
        )
    ):
        raise ProcessLeaseError("qualification run leaseの資格名が不正です。")
    return ExclusiveFileLease(
        repo_root.resolve()
        / "output"
        / "question_review_console"
        / "run_leases"
        / f"{safe}.lock",
        purpose=f"資格run:{safe}",
    )
