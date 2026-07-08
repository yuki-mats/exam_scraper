import unittest

from scripts.pipeline.collect_lawzilla_mcp_candidates import question_matches_item


class QuestionMatchesItemTest(unittest.TestCase):
    def test_matches_source_original_question_id(self):
        self.assertTrue(
            question_matches_item(
                {
                    "original_question_id": "firestore:doc-a,doc-b",
                    "source_original_question_id": "gasushunin-koushu-hourei-2022-16",
                },
                {
                    "originalQuestionId": "gasushunin-koushu-hourei-2022-16",
                    "reviewQuestionId": "firestore:doc-a,doc-b",
                },
            )
        )

    def test_matches_aggregate_firestore_original_question_id(self):
        self.assertTrue(
            question_matches_item(
                {
                    "original_question_id": "firestore:doc-a,doc-b",
                },
                {
                    "originalQuestionId": "not-the-source-id",
                    "reviewQuestionId": "firestore:doc-a,doc-b",
                },
            )
        )

    def test_does_not_match_partial_firestore_set(self):
        self.assertFalse(
            question_matches_item(
                {
                    "original_question_id": "firestore:doc-a",
                },
                {
                    "originalQuestionId": "not-the-source-id",
                    "reviewQuestionId": "firestore:doc-a,doc-b",
                },
            )
        )


if __name__ == "__main__":
    unittest.main()
