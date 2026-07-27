from __future__ import annotations

import threading
from collections import Counter
from contextlib import contextmanager
from typing import Any, Callable, Iterator


GLOBAL_TURN_CAPACITY = 100
UNGROUPED_TURN_GROUP = "__ungrouped__"


class GlobalTurnBudget:
    """全資格で共有するmodel turn数のhard capと公平配分を管理する。"""

    def __init__(self, capacity: int = GLOBAL_TURN_CAPACITY) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("turn capacityは1以上の整数で指定してください。")
        self.capacity = capacity
        self._registered: Counter[str] = Counter()
        self._active: Counter[str] = Counter()
        self._waiting: Counter[str] = Counter()
        self._priority_waiting: Counter[str] = Counter()
        self._peak_in_flight = 0
        self._condition = threading.Condition(threading.RLock())

    @staticmethod
    def _normalize_group(group: str | None) -> str:
        value = str(group or UNGROUPED_TURN_GROUP).strip()
        return value or UNGROUPED_TURN_GROUP

    def _groups_locked(self) -> list[str]:
        return sorted(
            {
                group
                for counter in (self._registered, self._active, self._waiting)
                for group, count in counter.items()
                if count > 0
            }
        )

    def _allocations_locked(self) -> dict[str, int]:
        groups = self._groups_locked()
        if not groups:
            return {}
        base, remainder = divmod(self.capacity, len(groups))
        return {
            group: base + (1 if index < remainder else 0)
            for index, group in enumerate(groups)
        }

    @contextmanager
    def register(self, group: str) -> Iterator[None]:
        normalized = self._normalize_group(group)
        with self._condition:
            self._registered[normalized] += 1
            self._condition.notify_all()
        try:
            yield
        finally:
            with self._condition:
                self._registered[normalized] -= 1
                if self._registered[normalized] <= 0:
                    self._registered.pop(normalized, None)
                self._condition.notify_all()

    def acquire(
        self,
        group: str | None,
        *,
        heartbeat: Callable[[], None] | None = None,
        priority: bool = False,
    ) -> str:
        normalized = self._normalize_group(group)
        with self._condition:
            self._waiting[normalized] += 1
            if priority:
                self._priority_waiting[normalized] += 1
            self._condition.notify_all()
        try:
            while True:
                with self._condition:
                    allocations = self._allocations_locked()
                    total_active = sum(self._active.values())
                    priority_is_waiting = bool(
                        sum(self._priority_waiting.values())
                    )
                    if (
                        total_active < self.capacity
                        and self._active[normalized]
                        < allocations.get(normalized, 0)
                        and (priority or not priority_is_waiting)
                    ):
                        self._remove_waiter_locked(
                            normalized,
                            priority=priority,
                        )
                        self._active[normalized] += 1
                        self._peak_in_flight = max(
                            self._peak_in_flight,
                            total_active + 1,
                        )
                        self._condition.notify_all()
                        return normalized
                    self._condition.wait(timeout=0.25)
                # A heartbeat can perform filesystem or parent-manifest I/O.
                # Never run it while holding the allocation condition.
                if callable(heartbeat):
                    try:
                        heartbeat()
                    except Exception:
                        pass
        except BaseException:
            with self._condition:
                self._remove_waiter_locked(
                    normalized,
                    priority=priority,
                )
                self._condition.notify_all()
            raise

    def _remove_waiter_locked(
        self,
        group: str,
        *,
        priority: bool,
    ) -> None:
        self._waiting[group] -= 1
        if self._waiting[group] <= 0:
            self._waiting.pop(group, None)
        if priority:
            self._priority_waiting[group] -= 1
            if self._priority_waiting[group] <= 0:
                self._priority_waiting.pop(group, None)

    def release(self, group: str) -> None:
        normalized = self._normalize_group(group)
        with self._condition:
            if self._active[normalized] <= 0:
                raise RuntimeError("取得していないturn slotは解放できません。")
            self._active[normalized] -= 1
            if self._active[normalized] <= 0:
                self._active.pop(normalized, None)
            self._condition.notify_all()

    @contextmanager
    def slot(
        self,
        group: str | None,
        *,
        heartbeat: Callable[[], None] | None = None,
        priority: bool = False,
    ) -> Iterator[None]:
        acquired_group = self.acquire(
            group,
            heartbeat=heartbeat,
            priority=priority,
        )
        try:
            yield
        finally:
            self.release(acquired_group)

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            allocations = self._allocations_locked()
            groups = self._groups_locked()
            return {
                "capacity": self.capacity,
                "inFlight": sum(self._active.values()),
                "waiting": sum(self._waiting.values()),
                "priorityWaiting": sum(self._priority_waiting.values()),
                "peakInFlight": self._peak_in_flight,
                "groups": [
                    {
                        "group": group,
                        "registered": self._registered[group],
                        "allocation": allocations.get(group, 0),
                        "inFlight": self._active[group],
                        "waiting": self._waiting[group],
                        "priorityWaiting": self._priority_waiting[group],
                    }
                    for group in groups
                ],
            }
