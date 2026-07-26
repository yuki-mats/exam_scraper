import unittest

from tools.question_review_console.question_list_read_model import (
    question_list_summary,
    validate_question_list_read_model,
)


class QuestionListReadModelTests(unittest.TestCase):
    def test_summary_keeps_only_fields_needed_by_simple_list(self):
        summary = question_list_summary(
            {
                "id": "question-1",
                "reviewKey": "sample:2026:q1",
                "sourceQuestionKey": "sample:2026:q1",
                "questionLabel": "問1",
                "qualification": "sample",
                "listGroupId": "2026",
                "body": "本文",
                "contentUpdatedAt": "2026-07-26T10:00:00+09:00",
                "choiceCount": 2,
                "choicesExtractedFromQuestionBody": True,
                "isLawRelated": False,
                "issues": [],
                "issueCodes": [],
                "workflow": {
                    "merge": "match",
                    "convert": "match",
                    "upload": "match",
                },
                "stateHash": "state-1",
                "projected": {
                    "isCalculationQuestion": True,
                    "questionType": "正誤式",
                    "correctChoiceText": ["正しい", "間違い"],
                    "explanationText": ["説明1", "説明2"],
                },
                "uploadReadyDocs": [
                    {
                        "correctChoiceText": "正しい",
                        "explanationText": "説明1",
                        "questionType": "正誤式",
                    },
                    {
                        "correctChoiceText": "間違い",
                        "explanationText": "説明2",
                        "questionType": "正誤式",
                    },
                ],
                "largeDetailOnlyField": {"unused": True},
            },
            snapshot_version="2026-07-26T10:10:00+09:00",
        )

        self.assertTrue(summary["isCalculationQuestion"])
        self.assertEqual(
            summary["publicationSummary"]["verdicts"],
            ["正しい", "間違い"],
        )
        self.assertEqual(summary["publicationSummary"]["explanationCount"], 2)
        self.assertNotIn("projected", summary)
        self.assertNotIn("uploadReadyDocs", summary)
        self.assertNotIn("largeDetailOnlyField", summary)
        self.assertEqual(len(summary["detailVersion"]), 16)

    def test_validator_rejects_incomplete_snapshot(self):
        with self.assertRaisesRegex(ValueError, "questions"):
            validate_question_list_read_model(
                "sample",
                {
                    "qualification": "sample",
                    "listGroupIds": [],
                    "groups": [],
                },
            )


if __name__ == "__main__":
    unittest.main()
