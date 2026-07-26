from __future__ import annotations

from dataclasses import dataclass


# 一問を一つのtop-level model turnへ割り当てる。これにより
# questionConcurrencyが「同時にmodelが整備している問題数」と一致する。
DEFAULT_MAX_QUESTIONS_PER_TURN = 1
DEFAULT_MAX_PARALLEL_TURNS = 64


@dataclass
class AdaptiveLimits:
    parallel_turns: int
    success_streak: int = 0

    @classmethod
    def initial(
        cls,
        *,
        pending_batches: int,
        max_parallel_turns: int = DEFAULT_MAX_PARALLEL_TURNS,
    ) -> "AdaptiveLimits":
        return cls(
            parallel_turns=max(
                1,
                min(int(pending_batches or 1), int(max_parallel_turns)),
            ),
        )

    def observe(
        self,
        *,
        provider_failure: bool = False,
        schema_failure: bool = False,
        isolated_failure: bool = False,
        elapsed_seconds: float | None = None,
        max_parallel_turns: int = DEFAULT_MAX_PARALLEL_TURNS,
    ) -> None:
        if provider_failure:
            self.parallel_turns = max(1, self.parallel_turns // 2)
            self.success_streak = 0
            return
        if schema_failure or isolated_failure:
            # 一問一turnでは入力をさらに束分割できない。失敗した一問だけを
            # queue末尾へ戻す。個別turnの期限切れも全体容量の成功又は
            # provider失敗とは数えず、正常な他問の並列数は下げない。
            self.success_streak = 0
            return
        self.success_streak += 1
        if self.success_streak < max(1, self.parallel_turns):
            return
        if elapsed_seconds is not None and elapsed_seconds > 1_800:
            self.success_streak = 0
            return
        # parallel_turnsはrunをまたいで維持する実行容量であり、観測時点の
        # 残りfuture数ではない。wave終端の成功を容量低下として扱わず、
        # provider失敗で縮小した場合だけ成功に応じて一枠ずつ回復する。
        self.parallel_turns = min(
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
        "questionsPerTurn": DEFAULT_MAX_QUESTIONS_PER_TURN,
        "batchCount": int(batch_count),
        "turnCount": int(batch_count),
        "inFlightQuestions": int(in_flight_questions),
    }
