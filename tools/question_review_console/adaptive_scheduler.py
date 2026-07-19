from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar


T = TypeVar("T")

DEFAULT_BATCH_TOKEN_BUDGET = 60_000
DEFAULT_MAX_QUESTIONS_PER_TURN = 50
DEFAULT_MAX_PARALLEL_TURNS = 32
MIN_BATCH_TOKEN_BUDGET = 8_000


def estimated_tokens(value: Any) -> int:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return max(1, math.ceil(len(raw) / 3))


def pack_by_token_budget(
    values: Sequence[T],
    *,
    payload: Callable[[T], Any],
    token_budget: int = DEFAULT_BATCH_TOKEN_BUDGET,
    max_questions: int = DEFAULT_MAX_QUESTIONS_PER_TURN,
) -> list[list[T]]:
    budget = max(MIN_BATCH_TOKEN_BUDGET, int(token_budget))
    limit = max(1, int(max_questions))
    batches: list[list[T]] = []
    current: list[T] = []
    current_tokens = 0
    for value in values:
        weight = estimated_tokens(payload(value))
        if current and (len(current) >= limit or current_tokens + weight > budget):
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(value)
        current_tokens += weight
    if current:
        batches.append(current)
    return batches


@dataclass
class AdaptiveLimits:
    parallel_turns: int
    batch_token_budget: int = DEFAULT_BATCH_TOKEN_BUDGET
    success_streak: int = 0

    @classmethod
    def initial(
        cls,
        *,
        pending_batches: int,
        max_parallel_turns: int = DEFAULT_MAX_PARALLEL_TURNS,
        batch_token_budget: int = DEFAULT_BATCH_TOKEN_BUDGET,
    ) -> "AdaptiveLimits":
        return cls(
            parallel_turns=max(
                1,
                min(int(pending_batches or 1), int(max_parallel_turns)),
            ),
            batch_token_budget=max(MIN_BATCH_TOKEN_BUDGET, int(batch_token_budget)),
        )

    def observe(
        self,
        *,
        provider_failure: bool = False,
        schema_failure: bool = False,
        elapsed_seconds: float | None = None,
        pending_batches: int = 1,
        max_parallel_turns: int = DEFAULT_MAX_PARALLEL_TURNS,
    ) -> None:
        if provider_failure:
            self.parallel_turns = max(1, self.parallel_turns // 2)
            self.success_streak = 0
            return
        if schema_failure:
            self.batch_token_budget = max(
                MIN_BATCH_TOKEN_BUDGET,
                self.batch_token_budget // 2,
            )
            self.success_streak = 0
            return
        self.success_streak += 1
        if self.success_streak < max(1, self.parallel_turns):
            return
        if elapsed_seconds is not None and elapsed_seconds > 1_800:
            self.success_streak = 0
            return
        self.parallel_turns = min(
            max(1, int(pending_batches or 1)),
            int(max_parallel_turns),
            self.parallel_turns + 1,
        )
        self.success_streak = 0


def scheduler_status(
    limits: AdaptiveLimits,
    *,
    batch_count: int,
    in_flight_questions: int,
) -> dict[str, int | str]:
    return {
        "mode": "auto_max",
        "parallelTurns": limits.parallel_turns,
        "batchTokenBudget": limits.batch_token_budget,
        "batchCount": int(batch_count),
        "inFlightQuestions": int(in_flight_questions),
    }
