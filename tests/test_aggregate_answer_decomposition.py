from __future__ import annotations

import unittest

from scripts.common.aggregate_answer_decomposition import (
    REVIEW_SCHEMA_VERSION,
    derived_source_unique_keys,
    extract_source_statements,
    materialize_decomposition,
    reconcile_reviews,
    source_text_hash,
)


SOURCE_TEXT = (
    "次の記述のうち、正しいものの組合せはどれか。\n"
    "A  最初の記述である。\n"
    "B  二番目の記述である。"
)
FIRST = "A  最初の記述である。"
SECOND = "B  二番目の記述である。"


def approved_review() -> dict[str, object]:
    first_start = SOURCE_TEXT.index(FIRST)
    second_start = SOURCE_TEXT.index(SECOND)
    return {
        "schemaVersion": REVIEW_SCHEMA_VERSION,
        "sourceHash": source_text_hash(SOURCE_TEXT),
        "classification": "target",
        "spans": [
            {"start": first_start, "end": first_start + len(FIRST)},
            {"start": second_start, "end": second_start + len(SECOND)},
        ],
        "decision": "approve",
        "issueCodes": [],
    }


class AggregateAnswerDecompositionTests(unittest.TestCase):
    def test_exact_reviews_extract_only_source_slices(self) -> None:
        review = approved_review()

        decomposition = reconcile_reviews(SOURCE_TEXT, [review, dict(review)])

        self.assertEqual(
            extract_source_statements(SOURCE_TEXT, decomposition),
            [FIRST, SECOND],
        )

    def test_agent_authored_text_field_is_rejected(self) -> None:
        review = approved_review()
        review["extractedText"] = "エージェントが生成した文章"

        with self.assertRaisesRegex(ValueError, "only"):
            reconcile_reviews(SOURCE_TEXT, [review, review])

    def test_review_disagreement_becomes_hold_without_third_adjudication(self) -> None:
        first = approved_review()
        second = approved_review()
        second["spans"] = list(reversed(second["spans"]))

        with self.assertRaisesRegex(ValueError, "ordered"):
            reconcile_reviews(SOURCE_TEXT, [first, second])

        second = approved_review()
        second["classification"] = "non_target"
        second["spans"] = []
        held = reconcile_reviews(SOURCE_TEXT, [first, second])
        self.assertEqual(held["decision"], "hold")
        self.assertEqual(held["issueCodes"], ["review_disagreement"])

    def test_source_hash_mismatch_becomes_hold(self) -> None:
        review = approved_review()
        review["sourceHash"] = "sha256:" + "0" * 64

        held = reconcile_reviews(SOURCE_TEXT, [review, review])

        self.assertEqual(held["classification"], "hold")
        self.assertEqual(held["issueCodes"], ["source_hash_mismatch"])

    def test_materializer_generates_new_stable_statement_keys(self) -> None:
        source = {
            "canonical_question_key": "sample:2026:q001",
            "questionBodyText": SOURCE_TEXT,
            "choiceTextList": ["A、B", "A、C"],
        }
        review = approved_review()

        materialized = materialize_decomposition(source, [review, dict(review)])

        self.assertEqual(materialized["questionType"], "true_false")
        self.assertEqual(materialized["choiceTextList"], [FIRST, SECOND])
        self.assertEqual(
            materialized["sourceUniqueKeys"],
            derived_source_unique_keys(
                source,
                materialized["aggregateAnswerDecomposition"],
            ),
        )
        self.assertTrue(
            all("aggregate-statement" in key for key in materialized["sourceUniqueKeys"])
        )


if __name__ == "__main__":
    unittest.main()
