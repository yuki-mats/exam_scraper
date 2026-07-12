import json
import tempfile
import unittest
from pathlib import Path

from tools.question_review_console.inventory import QuestionInventory, detect_issues
from tools.question_review_console.patch_validation import (
    patch_entry_required_warnings,
)


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class QuestionReviewInventoryTests(unittest.TestCase):
    def test_detects_missing_required_fields_in_applied_patch_entry(self):
        warnings = patch_entry_required_warnings(
            {
                "original_question_id": "q1",
                "explanationText": ["正しい。根拠"],
            },
            "explanation",
        )

        self.assertEqual(
            {warning["field"] for warning in warnings},
            {"question_url", "suggestedQuestions", "suggestedQuestionDetails"},
        )

    def test_reports_all_projected_required_field_warnings(self):
        projected = {
            "questionBodyText": "",
            "choiceTextList": ["A", ""],
            "correctChoiceText": ["未確定"],
            "explanationText": [""],
        }

        issues = detect_issues(projected, projected, [], [], [])
        required = next(
            issue for issue in issues if issue["code"] == "required_field_missing"
        )

        self.assertEqual(
            required["fields"],
            [
                "choiceTextList",
                "correctChoiceText",
                "explanationText",
                "questionBodyText",
                "questionType",
            ],
        )
        self.assertIn("問題文がありません。", required["detail"])
        self.assertIn("正誤数が選択肢数と一致しません。", required["detail"])

    def test_merge_comparison_treats_verdict_synonyms_as_equal(self):
        projected = {
            "questionBodyText": "正しいものはどれか。",
            "choiceTextList": ["A", "B"],
            "correctChoiceText": ["正解", "不正解"],
            "explanationText": ["正解。", "不正解。"],
            "questionType": "multiple_choice",
            "isLawRelated": False,
        }
        merged = {**projected, "correctChoiceText": ["正しい", "間違い"]}

        issues = detect_issues(projected, merged, [], [], [])

        self.assertNotIn("merge_stale", {issue["code"] for issue in issues})

    def test_discovers_qualification_and_projects_latest_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            group = root / "output" / "sample-exam" / "questions_json" / "2026"
            source = {
                "original_question_id": "q1",
                "questionLabel": "問1",
                "questionBodyText": "正しいものはどれか。",
                "choiceTextList": ["A", "B"],
                "correctChoiceText": ["正しい", "間違い"],
                "explanationText": ["正しい。旧", "間違い。旧"],
                "questionType": "multiple_choice",
                "isLawRelated": False,
            }
            write_json(group / "00_source" / "question_2026_1.json", {"question_bodies": [source]})
            write_json(group / "30_merged_2" / "question_2026_1_merged.json", {"question_bodies": [source]})
            write_json(
                group / "21_explanationText_added" / "question_2026_1_explanationText_added.json",
                [
                    {
                        "original_question_id": "q1",
                        "question_url": "https://example.test/q1",
                        "explanationText": ["正しい。新", "間違い。新"],
                        "suggestedQuestions": [],
                        "suggestedQuestionDetails": [],
                    }
                ],
            )
            firestore_docs = [
                {
                    "questionId": f"doc{index}",
                    "qualificationId": "sample-exam",
                    "originalQuestionId": "q1",
                    "originalQuestionBodyText": source["questionBodyText"],
                    "originalQuestionChoiceText": choice,
                    "correctChoiceText": source["correctChoiceText"][index],
                    "explanationText": ["正しい。新", "間違い。新"][index],
                }
                for index, choice in enumerate(source["choiceTextList"])
            ]
            write_json(
                group / "40_convert" / "question_2026_firestore_20260712_120000.json",
                {"questions": firestore_docs},
            )

            inventory = QuestionInventory(root)
            overview = inventory.inventory()
            payload = inventory.group("sample-exam", "2026")

        self.assertEqual(overview["qualifications"][0]["id"], "sample-exam")
        self.assertEqual(payload["questionCount"], 1)
        question = payload["questions"][0]
        self.assertEqual(question["originalQuestionId"], "q1")
        self.assertEqual(question["projected"]["explanationText"], ["正しい。新", "間違い。新"])
        self.assertIn("merge_stale", question["issueCodes"])
        self.assertNotIn("convert_stale", question["issueCodes"])


if __name__ == "__main__":
    unittest.main()
