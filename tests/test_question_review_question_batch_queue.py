import unittest

from tools.question_review_console.question_batch_queue import (
    QuestionBatchReceiptError,
    batch_size,
    chunks,
    model_worker_limit,
    normalize_batch_question_results,
    validate_batch_question_results,
    validate_changed_file_attribution,
)


class QuestionBatchQueueTests(unittest.TestCase):
    def test_five_and_ten_mean_total_questions_not_model_turns(self):
        self.assertEqual((batch_size(5), model_worker_limit(5)), (5, 1))
        self.assertEqual((batch_size(10), model_worker_limit(10)), (5, 2))
        self.assertEqual(
            chunks(list(range(11)), 5),
            [[0, 1, 2, 3, 4], [5, 6, 7, 8, 9], [10]],
        )

    def test_receipt_keeps_each_question_result_independent(self):
        results = validate_batch_question_results(
            {
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "succeeded",
                        "summary": "合格",
                        "commands": [{"command": "check q1", "status": "pass"}],
                        "changedFiles": ["patch.json"],
                    },
                    {
                        "questionId": "q2",
                        "status": "failed",
                        "summary": "形式不備",
                        "commands": [{"command": "check q2", "status": "fail"}],
                        "changedFiles": ["patch.json"],
                    },
                ]
            },
            ["q1", "q2"],
        )

        self.assertEqual(
            [result.status for result in results],
            ["succeeded", "failed"],
        )
        validate_changed_file_attribution(results, ["patch.json"])

    def test_receipt_rejects_missing_or_unattributed_question(self):
        with self.assertRaisesRegex(QuestionBatchReceiptError, "未記録"):
            validate_batch_question_results(
                {
                    "questionResults": [
                        {
                            "questionId": "q1",
                            "status": "succeeded",
                            "summary": "合格",
                            "commands": [{"command": "check", "status": "pass"}],
                            "changedFiles": [],
                        }
                    ]
                },
                ["q1", "q2"],
            )

    def test_malformed_result_does_not_reject_valid_sibling(self):
        results = normalize_batch_question_results(
            {
                "questionResults": [
                    {
                        "questionId": "q1",
                        "status": "succeeded",
                        "summary": "合格",
                        "commands": [
                            {"command": "check q1", "status": "pass"}
                        ],
                        "changedFiles": ["a.json"],
                    },
                    {
                        "questionId": "q2",
                        "status": "succeeded",
                        "summary": "command欠落",
                        "commands": [],
                        "changedFiles": ["b.json"],
                    },
                ]
            },
            ["q1", "q2"],
        )

        self.assertEqual(
            [result.status for result in results],
            ["succeeded", "failed"],
        )
        self.assertEqual(results[1].changed_files, ("b.json",))
        validate_changed_file_attribution(results, ["a.json", "b.json"])


if __name__ == "__main__":
    unittest.main()
