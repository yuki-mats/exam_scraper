import unittest

from tools.question_review_console.adaptive_scheduler import (
    DEFAULT_MAX_PARALLEL_TURNS,
    DEFAULT_MAX_QUESTIONS_PER_TURN,
    AdaptiveLimits,
    scheduler_status,
)


class AdaptiveSchedulerTest(unittest.TestCase):
    def test_one_question_is_assigned_to_each_model_turn(self):
        limits = AdaptiveLimits.initial(pending_batches=80)
        status = scheduler_status(
            limits,
            batch_count=80,
            in_flight_questions=80,
        )

        self.assertEqual(DEFAULT_MAX_QUESTIONS_PER_TURN, 1)
        self.assertEqual(status["questionsPerTurn"], 1)
        self.assertEqual(status["turnCount"], 80)

    def test_initial_parallelism_uses_all_available_batches_up_to_safety_cap(self):
        limits = AdaptiveLimits.initial(pending_batches=17, max_parallel_turns=32)

        self.assertEqual(limits.parallel_turns, 17)

    def test_default_parallel_turn_limit_matches_global_capacity(self):
        limits = AdaptiveLimits.initial(pending_batches=80)

        self.assertEqual(DEFAULT_MAX_PARALLEL_TURNS, 64)
        self.assertEqual(limits.parallel_turns, 64)

    def test_provider_failure_halves_parallelism(self):
        limits = AdaptiveLimits(parallel_turns=12)

        limits.observe(provider_failure=True)

        self.assertEqual(limits.parallel_turns, 6)

    def test_schema_failure_retries_only_that_question_without_reducing_parallelism(self):
        limits = AdaptiveLimits(parallel_turns=8)

        limits.observe(schema_failure=True)

        self.assertEqual(limits.parallel_turns, 8)
        self.assertEqual(limits.success_streak, 0)

    def test_isolated_failure_is_capacity_neutral(self):
        limits = AdaptiveLimits(parallel_turns=32, success_streak=12)

        limits.observe(isolated_failure=True, max_parallel_turns=64)

        self.assertEqual(limits.parallel_turns, 32)
        self.assertEqual(limits.success_streak, 0)

    def test_successful_wave_end_does_not_reduce_parallel_capacity(self):
        limits = AdaptiveLimits(parallel_turns=64)

        for _ in range(64):
            limits.observe(max_parallel_turns=64)

        self.assertEqual(limits.parallel_turns, 64)
        self.assertEqual(limits.success_streak, 0)

    def test_successes_recover_provider_reduction_one_slot_at_a_time(self):
        limits = AdaptiveLimits(parallel_turns=64)
        limits.observe(provider_failure=True)

        for _ in range(32):
            limits.observe(max_parallel_turns=64)

        self.assertEqual(limits.parallel_turns, 33)
        self.assertEqual(limits.success_streak, 0)


if __name__ == "__main__":
    unittest.main()
