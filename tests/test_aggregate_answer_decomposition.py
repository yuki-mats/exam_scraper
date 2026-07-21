from __future__ import annotations

import unittest

from scripts.common.aggregate_answer_decomposition import (
    REVIEW_SCHEMA_VERSION,
    candidate_set_hash,
    derived_source_unique_keys,
    extract_source_statements,
    generate_statement_candidates,
    materialize_decomposition,
    reconcile_reviews,
    stable_parent_identity,
    source_text_hash,
    statement_boundary_id,
    statement_candidate_id,
)


SOURCE_TEXT = (
    "次の記述のうち、正しいものの組合せはどれか。\n"
    "A  最初の記述である。\n"
    "B  二番目の記述である。"
)
FIRST = "A  最初の記述である。"
SECOND = "B  二番目の記述である。"


def approved_review() -> dict[str, object]:
    candidate_set = generate_statement_candidates(SOURCE_TEXT)
    candidate = candidate_set["candidates"][0]
    return {
        "schemaVersion": REVIEW_SCHEMA_VERSION,
        "sourceHash": source_text_hash(SOURCE_TEXT),
        "classification": "target",
        "candidateId": candidate["candidateId"],
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
        second["classification"] = "non_target"
        second["candidateId"] = None
        held = reconcile_reviews(SOURCE_TEXT, [first, second])
        self.assertEqual(held["decision"], "hold")
        self.assertEqual(held["issueCodes"], ["review_disagreement"])

    def test_source_hash_mismatch_becomes_hold(self) -> None:
        review = approved_review()
        review["sourceHash"] = "sha256:" + "0" * 64

        held = reconcile_reviews(SOURCE_TEXT, [review, review])

        self.assertEqual(held["classification"], "hold")
        self.assertEqual(held["issueCodes"], ["source_hash_mismatch"])

    def test_candidate_and_boundary_ids_are_source_offset_owned(self) -> None:
        candidate_set = generate_statement_candidates(SOURCE_TEXT)
        candidate = candidate_set["candidates"][0]
        spans = candidate["spans"]
        source_hash = source_text_hash(SOURCE_TEXT)

        self.assertEqual(
            [span["boundaryId"] for span in spans],
            [
                statement_boundary_id(source_hash, span["start"], span["end"])
                for span in spans
            ],
        )
        self.assertEqual(
            candidate["candidateId"],
            statement_candidate_id(source_hash, spans),
        )
        self.assertEqual(candidate_set_hash(candidate_set), candidate_set_hash(
            generate_statement_candidates(SOURCE_TEXT)
        ))

    def test_raw_offsets_and_unknown_candidate_ids_are_rejected(self) -> None:
        review = approved_review()
        review["spans"] = [{"start": 0, "end": 1}]
        with self.assertRaisesRegex(ValueError, "only"):
            reconcile_reviews(SOURCE_TEXT, [review, review])

        review = approved_review()
        review["candidateId"] = "candidate:" + "0" * 24
        with self.assertRaisesRegex(ValueError, "not present"):
            reconcile_reviews(SOURCE_TEXT, [review, review])

    def test_nested_marker_family_does_not_split_outer_candidate(self) -> None:
        source_text = (
            "A  第一の記述。\n"
            "① 補足一。\n"
            "② 補足二。\n"
            "B  第二の記述。"
        )
        candidate_set = generate_statement_candidates(source_text)
        outer = next(
            candidate
            for candidate in candidate_set["candidates"]
            if len(candidate["spans"]) == 2
            and source_text[candidate["spans"][0]["start"]] == "A"
        )

        first = outer["spans"][0]
        self.assertIn("① 補足一。", source_text[first["start"] : first["end"]])
        self.assertEqual(
            source_text[
                outer["spans"][1]["start"] : outer["spans"][1]["end"]
            ],
            "B  第二の記述。",
        )

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

    def test_stable_parent_identity_uses_source_priority(self) -> None:
        cases = (
            ("canonical_question_key", "canonical-snake"),
            ("canonicalQuestionKey", "canonical-camel"),
            ("source_question_id", "source-id"),
            ("sourceQuestionKey", "source-question-key"),
            ("public_question_id", "public-id"),
            ("original_question_id", "original-snake"),
            ("originalQuestionId", "original-camel"),
        )

        for index, (field, value) in enumerate(cases):
            with self.subTest(field=field):
                record = dict(cases[index:])
                self.assertEqual(
                    stable_parent_identity(record),
                    {"field": field, "value": value},
                )


if __name__ == "__main__":
    unittest.main()
