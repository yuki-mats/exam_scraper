import unittest

from tools.question_review_console.adaptive_scheduler import (
    AdaptiveLimits,
    pack_by_token_budget,
)


class AdaptiveSchedulerTest(unittest.TestCase):
    def test_packs_by_payload_size_instead_of_fixed_question_count(self):
        values = ["a" * 300, "b" * 300, "c" * 30]

        batches = pack_by_token_budget(
            values,
            payload=lambda value: {"body": value},
            token_budget=8_000,
            max_questions=2,
        )

        self.assertEqual([len(batch) for batch in batches], [2, 1])

    def test_initial_parallelism_uses_all_available_batches_up_to_safety_cap(self):
        limits = AdaptiveLimits.initial(pending_batches=17, max_parallel_turns=32)

        self.assertEqual(limits.parallel_turns, 17)

    def test_provider_failure_halves_parallelism(self):
        limits = AdaptiveLimits(parallel_turns=12)

        limits.observe(provider_failure=True)

        self.assertEqual(limits.parallel_turns, 6)

    def test_schema_failure_reduces_batch_budget_without_reducing_quality_checks(self):
        limits = AdaptiveLimits(parallel_turns=8, batch_token_budget=60_000)

        limits.observe(schema_failure=True)

        self.assertEqual(limits.parallel_turns, 8)
        self.assertEqual(limits.batch_token_budget, 30_000)


if __name__ == "__main__":
    unittest.main()
