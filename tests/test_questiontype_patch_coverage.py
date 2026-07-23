import unittest

from scripts.check.check_questiontype_patch_coverage import compare_entries
from scripts.common.aggregate_answer_decomposition import (
    REVIEW_SCHEMA_VERSION,
    generate_statement_candidates,
    materialize_decomposition,
    source_text_hash,
)


class QuestionTypePatchCoverageTest(unittest.TestCase):
    def test_allows_tool_sliced_aggregate_answer_choices(self):
        source = self.source()
        source["canonical_question_key"] = "sample:2026:q001"
        source["questionBodyText"] = "組合せを選べ。\nA 原文一。\nB 原文二。"
        body = source["questionBodyText"]
        candidate_id = generate_statement_candidates(body)["candidates"][0][
            "candidateId"
        ]
        review = {
            "schemaVersion": REVIEW_SCHEMA_VERSION,
            "sourceHash": source_text_hash(body),
            "classification": "target",
            "candidateId": candidate_id,
            "decision": "approve",
            "issueCodes": [],
        }
        patch = self.patch("true_false")
        patch["questionBodyText"] = body
        patch.update(materialize_decomposition(source, [review, dict(review)]))

        issues, _warnings = compare_entries([source], [patch])

        self.assertEqual(issues, [])

    def test_rejects_agent_supplied_choice_not_matching_spans(self):
        source = self.source()
        source["canonical_question_key"] = "sample:2026:q001"
        source["questionBodyText"] = "組合せを選べ。\nA 原文一。\nB 原文二。"
        body = source["questionBodyText"]
        candidate_id = generate_statement_candidates(body)["candidates"][0][
            "candidateId"
        ]
        review = {
            "schemaVersion": REVIEW_SCHEMA_VERSION,
            "sourceHash": source_text_hash(body),
            "classification": "target",
            "candidateId": candidate_id,
            "decision": "approve",
            "issueCodes": [],
        }
        patch = self.patch("true_false")
        patch["questionBodyText"] = body
        patch.update(materialize_decomposition(source, [review, dict(review)]))
        patch["choiceTextList"] = ["生成文一", "生成文二"]

        issues, _warnings = compare_entries([source], [patch])

        self.assertTrue(any("exact source span" in issue for issue in issues))

    def source(self, question_type="group_choice"):
        return {
            "original_question_id": "q1",
            "questionBodyText": "計算結果として最も近い値はどれか。",
            "choiceTextList": ["1", "2"],
            "questionType": question_type,
            "question_url": "https://example.com/q1",
            "sourceQuestionKey": "sample:2026:q1",
            "reviewQuestionId": "q1",
        }

    def patch(self, question_type):
        return {
            "original_question_id": "q1",
            "questionBodyText": "計算結果として最も近い値はどれか。",
            "choiceTextList": ["1", "2"],
            "questionType": question_type,
            "question_url": "https://example.com/q1",
            "isCalculationQuestion": True,
        }

    def test_rejects_new_single_choice_classification(self):
        issues, _warnings = compare_entries(
            [self.source()],
            [self.patch("single_choice")],
        )

        self.assertTrue(any("questionType must be one of" in issue for issue in issues))

    def test_allows_legacy_single_choice_to_remain_unchanged(self):
        issues, warnings = compare_entries(
            [self.source("single_choice")],
            [self.patch("single_choice")],
        )

        self.assertEqual(issues, [])
        self.assertTrue(any("legacy questionType preserved" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
