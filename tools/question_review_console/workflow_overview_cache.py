from __future__ import annotations

import copy
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from tools.question_review_console.review_store import atomic_write


WORKFLOW_OVERVIEW_CACHE_SCHEMA = "question-workflow-overview-cache/v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _safe_segment(value: str) -> str:
    if value in {"", ".", ".."} or any(
        not (character.isalnum() or character in "-._") for character in value
    ):
        raise ValueError(f"invalid workflow overview cache segment: {value}")
    return value


class WorkflowOverviewCache:
    """Serve the last complete dashboard read model while it is revalidated.

    A qualification overview is intentionally expensive: it reconciles every
    workflow group, historical failed delta, and work-policy version.  The HTTP
    request must not repeat that work or make the dashboard wait for it.  This
    cache persists the last complete overview and allows only one refresh per
    qualification at a time.
    """

    def __init__(
        self,
        repo_root: Path,
        loader: Callable[[str], Mapping[str, Any]],
        *,
        refresh_interval_seconds: float | None = None,
        retry_interval_seconds: float = 5.0,
        invalidation_delay_seconds: float = 2.0,
        refresh_allowed: Callable[[str], bool] | None = None,
    ):
        self.repo_root = repo_root.resolve()
        self.loader = loader
        self.refresh_interval_seconds = (
            None
            if refresh_interval_seconds is None
            else max(float(refresh_interval_seconds), 0.0)
        )
        self.retry_interval_seconds = max(float(retry_interval_seconds), 0.0)
        self.invalidation_delay_seconds = max(
            float(invalidation_delay_seconds),
            0.0,
        )
        self.refresh_allowed = refresh_allowed or (lambda _qualification: True)
        self.root = (
            self.repo_root
            / "output"
            / "question_review_console"
            / "cache"
            / "workflow_overviews"
        )
        self._condition = threading.Condition(threading.RLock())
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._loaded_from_disk: set[str] = set()
        self._refreshing: set[str] = set()
        self._refresh_after: dict[str, float] = {}
        self._last_errors: dict[str, str] = {}
        self._generations: dict[str, int] = {}
        self._snapshot_generations: dict[str, int] = {}

    def get(self, qualification: str, *, fresh: bool = False) -> dict[str, Any]:
        qualification = _safe_segment(qualification)
        snapshot = self._snapshot(qualification)
        if fresh or snapshot is None:
            return self._refresh_and_wait(qualification)

        with self._condition:
            refresh_due = time.monotonic() >= self._refresh_after.get(
                qualification, 0.0
            )
            stale = (
                self._snapshot_generations.get(qualification, -1)
                != self._generations.get(qualification, 0)
                or refresh_due
            )
            should_refresh = (
                qualification not in self._refreshing
                and refresh_due
                and self.refresh_allowed(qualification)
            )
            if should_refresh:
                self._refreshing.add(qualification)
                generation = self._generations.get(qualification, 0)
                thread = threading.Thread(
                    target=self._refresh_in_background,
                    args=(qualification, generation),
                    name=f"workflow-overview-{qualification}",
                    daemon=True,
                )
                thread.start()
            return self._public_snapshot(
                snapshot,
                refreshing=qualification in self._refreshing,
                error=self._last_errors.get(qualification, ""),
                stale=stale,
            )

    def put(
        self,
        qualification: str,
        overview: Mapping[str, Any],
    ) -> dict[str, Any]:
        qualification = _safe_segment(qualification)
        snapshot = self._validated_snapshot(qualification, overview)
        with self._condition:
            generation = self._generations.get(qualification, 0)
        self._store(qualification, snapshot, generation)
        return self._public_snapshot(
            snapshot,
            refreshing=False,
            error="",
            stale=self._is_stale(qualification),
        )

    def invalidate(
        self,
        qualification: str,
        *,
        delay_seconds: float | None = None,
    ) -> None:
        qualification = _safe_segment(qualification)
        with self._condition:
            self._generations[qualification] = (
                self._generations.get(qualification, 0) + 1
            )
            self._refresh_after[qualification] = (
                time.monotonic()
                + (
                    self.invalidation_delay_seconds
                    if delay_seconds is None
                    else max(float(delay_seconds), 0.0)
                )
            )

    def _snapshot(self, qualification: str) -> dict[str, Any] | None:
        with self._condition:
            cached = self._snapshots.get(qualification)
            if cached is not None:
                return copy.deepcopy(cached)
            if qualification in self._loaded_from_disk:
                return None
            self._loaded_from_disk.add(qualification)

        path = self._path(qualification)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if (
            not isinstance(value, Mapping)
            or value.get("schemaVersion") != WORKFLOW_OVERVIEW_CACHE_SCHEMA
            or value.get("qualification") != qualification
            or not isinstance(value.get("overview"), Mapping)
        ):
            return None
        try:
            snapshot = self._validated_snapshot(
                qualification,
                value["overview"],
            )
        except ValueError:
            return None
        with self._condition:
            self._snapshots[qualification] = snapshot
            self._snapshot_generations[qualification] = -1
            self._refresh_after[qualification] = 0.0
        return copy.deepcopy(snapshot)

    def _refresh_and_wait(self, qualification: str) -> dict[str, Any]:
        with self._condition:
            while qualification in self._refreshing:
                self._condition.wait()
                snapshot = self._snapshots.get(qualification)
                if snapshot is not None:
                    return self._public_snapshot(
                        snapshot,
                        refreshing=False,
                        error=self._last_errors.get(qualification, ""),
                        stale=self._is_stale_locked(qualification),
                    )
            self._refreshing.add(qualification)
            generation = self._generations.get(qualification, 0)

        try:
            snapshot = self._load(qualification)
        except Exception as exc:
            self._complete_refresh(qualification, error=exc)
            raise
        self._store(qualification, snapshot, generation)
        return self._public_snapshot(
            snapshot,
            refreshing=False,
            error="",
            stale=self._is_stale(qualification),
        )

    def _refresh_in_background(
        self,
        qualification: str,
        generation: int,
    ) -> None:
        try:
            snapshot = self._load(qualification)
        except Exception as exc:  # noqa: BLE001
            self._complete_refresh(qualification, error=exc)
            return
        self._store(qualification, snapshot, generation)

    def _load(self, qualification: str) -> dict[str, Any]:
        return self._validated_snapshot(
            qualification,
            self.loader(qualification),
        )

    @staticmethod
    def _validated_snapshot(
        qualification: str,
        overview: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(overview, Mapping):
            raise ValueError("workflow overview must be an object")
        snapshot = copy.deepcopy(dict(overview))
        if snapshot.get("qualification") != qualification:
            raise ValueError("workflow overview qualification mismatch")
        if not isinstance(snapshot.get("groups"), list):
            raise ValueError("workflow overview groups must be an array")
        if not isinstance(snapshot.get("stages"), list):
            raise ValueError("workflow overview stages must be an array")
        snapshot.pop("cache", None)
        return snapshot

    def _store(
        self,
        qualification: str,
        snapshot: dict[str, Any],
        generation: int,
    ) -> None:
        refreshed_at = _now_iso()
        try:
            atomic_write(
                self._path(qualification),
                json.dumps(
                    {
                        "schemaVersion": WORKFLOW_OVERVIEW_CACHE_SCHEMA,
                        "qualification": qualification,
                        "refreshedAt": refreshed_at,
                        "overview": snapshot,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n",
            )
        except OSError:
            # The snapshot is derived data. Keep the in-memory result usable
            # when the output directory is temporarily read-only.
            pass
        with self._condition:
            self._snapshots[qualification] = copy.deepcopy(snapshot)
            self._snapshot_generations[qualification] = generation
            changed_during_refresh = (
                self._generations.get(qualification, 0) != generation
            )
            if changed_during_refresh:
                self._refresh_after[qualification] = (
                    time.monotonic() + self.invalidation_delay_seconds
                )
            elif self.refresh_interval_seconds is None:
                self._refresh_after[qualification] = float("inf")
            else:
                self._refresh_after[qualification] = (
                    time.monotonic() + self.refresh_interval_seconds
                )
            self._last_errors.pop(qualification, None)
            self._refreshing.discard(qualification)
            self._condition.notify_all()

    def _is_stale(self, qualification: str) -> bool:
        with self._condition:
            return self._is_stale_locked(qualification)

    def _is_stale_locked(self, qualification: str) -> bool:
        return bool(
            self._snapshot_generations.get(qualification, -1)
            != self._generations.get(qualification, 0)
            or time.monotonic()
            >= self._refresh_after.get(qualification, 0.0)
        )

    def _complete_refresh(
        self,
        qualification: str,
        *,
        error: BaseException,
    ) -> None:
        with self._condition:
            self._last_errors[qualification] = str(error)[:500]
            self._refresh_after[qualification] = (
                time.monotonic() + self.retry_interval_seconds
            )
            self._refreshing.discard(qualification)
            self._condition.notify_all()

    def _path(self, qualification: str) -> Path:
        return self.root / f"{qualification}.json"

    @staticmethod
    def _public_snapshot(
        snapshot: Mapping[str, Any],
        *,
        refreshing: bool,
        error: str,
        stale: bool,
    ) -> dict[str, Any]:
        value = copy.deepcopy(dict(snapshot))
        value["cache"] = {
            "refreshing": refreshing,
            "stale": stale,
            "refreshError": error or None,
        }
        return value
