import unittest

from tools.question_review_console.question_detail_read_model import (
    parse_question_detail_cache_key,
    question_detail_cache_key,
    question_detail_content,
    validate_question_detail_read_model,
)


class QuestionDetailReadModelTests(unittest.TestCase):
    def test_content_keeps_read_only_fields_without_admin_payload(self):
        content = question_detail_content(
            {
                "id": "question-1",
                "sourceQuestionKey": "sample:2026:q1",
                "questionLabel": "問1",
                "qualification": "sample",
                "listGroupId": "2026",
                "body": "本文",
                "contentUpdatedAt": "2026-07-26T10:00:00+09:00",
                "choiceCount": 2,
                "isLawRelated": False,
                "issues": [],
                "issueCodes": [],
                "workflow": {
                    "patch": "match",
                    "merge": "match",
                    "convert": "match",
                    "upload": "match",
                },
                "stateHash": "state-1",
                "projected": {
                    "questionBodyText": "本文",
                    "choiceTextList": ["A", "B"],
                    "correctChoiceText": ["正しい", "間違い"],
                    "explanationText": ["説明1", "説明2"],
                    "questionType": "true_false",
                    "suggestedQuestionDetailsByChoice": [
                        {
                            "choiceIndex": 0,
                            "items": [{"question": "補足", "answer": "回答"}],
                        }
                    ],
                    "firestoreSourceQuestions": [{"large": "unused"}],
                },
                "source": {
                    "questionBodyText": "取得元本文",
                    "choiceTextList": ["取得元A", "取得元B"],
                    "firestoreSourceQuestions": [{"large": "unused"}],
                },
                "paths": {
                    "source": "output/sample/2026/00_source/questions.json",
                    "patches": [
                        "output/sample/2026/05_originalized/question.json"
                    ],
                },
                "uploadReadyDocs": [
                    {
                        "questionId": "public-1",
                        "questionBodyText": "公開本文",
                        "correctChoiceText": "正しい",
                        "explanationText": "公開解説",
                        "internalOnly": {"large": "unused"},
                    }
                ],
                "merged": {"large": "unused"},
                "convertedDocs": [{"large": "unused"}],
                "workVersions": {"large": "admin-only"},
                "evaluation": {"large": "admin-only"},
                "liveReadback": {"large": "admin-only"},
            }
        )

        self.assertEqual(
            content["projected"]["choiceTextList"],
            ["A", "B"],
        )
        self.assertEqual(
            content["source"]["questionBodyText"],
            "取得元本文",
        )
        self.assertEqual(
            content["paths"]["patches"],
            ["output/sample/2026/05_originalized/question.json"],
        )
        self.assertEqual(
            content["uploadReadyDocs"][0]["questionId"],
            "public-1",
        )
        self.assertNotIn("firestoreSourceQuestions", content["projected"])
        self.assertNotIn("firestoreSourceQuestions", content["source"])
        self.assertNotIn("internalOnly", content["uploadReadyDocs"][0])
        self.assertNotIn("merged", content)
        self.assertNotIn("convertedDocs", content)
        self.assertNotIn("workVersions", content)
        self.assertNotIn("evaluation", content)
        self.assertNotIn("liveReadback", content)
        self.assertEqual(len(content["detailVersion"]), 16)

    def test_cache_key_round_trip_and_snapshot_validation(self):
        cache_key = question_detail_cache_key("sample-exam", "2026")

        self.assertEqual(cache_key, "sample-exam--2026")
        self.assertEqual(
            parse_question_detail_cache_key(cache_key),
            ("sample-exam", "2026"),
        )
        snapshot = validate_question_detail_read_model(
            cache_key,
            {
                "cacheKey": cache_key,
                "qualification": "sample-exam",
                "listGroupId": "2026",
                "questionsById": {},
            },
        )
        self.assertEqual(snapshot["questionsById"], {})

    def test_cache_key_rejects_ambiguous_segments(self):
        with self.assertRaisesRegex(ValueError, "qualification"):
            question_detail_cache_key("sample--exam", "2026")
        with self.assertRaisesRegex(ValueError, "listGroupId"):
            question_detail_cache_key("sample", "../2026")


if __name__ == "__main__":
    unittest.main()
