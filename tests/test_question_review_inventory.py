import json
import tempfile
import unittest
from pathlib import Path

from tools.question_review_console.inventory import QuestionInventory, detect_issues
from tools.question_review_console.patch_validation import (
    law_audit_quality_warnings,
    patch_entry_required_warnings,
    upload_document_required_warnings,
)


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class QuestionReviewInventoryTests(unittest.TestCase):
    def test_inventory_exposes_japanese_qualification_name_and_publication_id(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_json(
                root / "config" / "scrape_presets.json",
                {
                    "readable-code": {
                        "qualification_name": "表示用資格名",
                        "publication_qualification_id": "stable-public-id",
                    }
                },
            )
            source_dir = (
                root
                / "output"
                / "readable-code"
                / "questions_json"
                / "202501"
                / "00_source"
            )
            write_json(source_dir / "question_source_1.json", {"question_bodies": []})

            overview = QuestionInventory(root).inventory()

        self.assertEqual(
            overview["qualifications"],
            [
                {
                    "id": "readable-code",
                    "displayName": "表示用資格名",
                    "publicationId": "stable-public-id",
                    "listGroupIds": ["202501"],
                    "listGroupCount": 1,
                }
            ],
        )

    def test_reports_missing_top_level_upload_ready_verdict(self):
        warnings = upload_document_required_warnings(
            {
                "questionId": "doc1",
                "originalQuestionBodyText": "問題文",
                "questionSetId": "set1",
                "questionText": "問題文 [quote]選択肢[/quote]",
                "questionType": "true_false",
                "qualificationId": "sample",
                "explanationText": "間違い。根拠。",
                "isOfficial": True,
                "isDeleted": False,
                "isChoiceOnly": False,
                "isGroupable": True,
                "questionTags": [],
                "originalQuestionChoiceText": "選択肢",
                "isLawRelated": True,
                "lawReferences": [{"lawId": "law1"}],
                "lawRevisionFacts": {
                    "auditStatus": "same_as_current",
                    "current": {"lawId": "law1"},
                    "evidenceSummary": {"verdict": "incorrect"},
                },
            }
        )

        self.assertEqual(
            [warning["field"] for warning in warnings],
            ["correctChoiceText"],
        )
        self.assertTrue(all(warning["documentId"] == "doc1" for warning in warnings))

    def test_allows_missing_law_snapshot_verdict_when_top_level_verdict_exists(self):
        document = {
                "questionId": "doc1",
                "originalQuestionBodyText": "問題文",
                "questionSetId": "set1",
                "questionText": "問題文 [quote]選択肢[/quote]",
                "questionType": "true_false",
                "qualificationId": "sample",
                "correctChoiceText": "間違い",
                "explanationText": "間違い。根拠。",
                "isOfficial": True,
                "isDeleted": False,
                "isChoiceOnly": False,
                "isGroupable": True,
                "questionTags": [],
                "originalQuestionChoiceText": "選択肢",
                "isLawRelated": True,
                "lawReferences": [{"lawId": "law1"}],
                "lawRevisionFacts": {
                    "auditStatus": "same_as_current",
                    "current": {"lawId": "law1"},
                    "evidenceSummary": {"verdict": "incorrect"},
                },
            }

        warnings = upload_document_required_warnings(document)

        self.assertEqual(warnings, [])

        quality_warnings = law_audit_quality_warnings(document)
        self.assertEqual(len(quality_warnings), 1)
        self.assertEqual(
            quality_warnings[0]["code"], "law_audit_metadata_incomplete"
        )
        self.assertEqual(
            quality_warnings[0]["field"],
            "lawRevisionFacts.current.correctChoiceText",
        )
        self.assertFalse(quality_warnings[0]["blocksSync"])
        self.assertTrue(quality_warnings[0]["blocksPublish"])

    def test_reports_law_snapshot_verdict_mismatch_separately(self):
        warnings = law_audit_quality_warnings(
            {
                "questionId": "doc1",
                "correctChoiceText": "正しい",
                "isLawRelated": True,
                "lawReferences": [{"lawId": "law1"}],
                "lawRevisionFacts": {
                    "auditStatus": "same_as_current",
                    "current": {"correctChoiceText": "間違い"},
                    "evidenceSummary": {"verdict": "correct"},
                },
            }
        )

        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["code"], "law_audit_verdict_mismatch")

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

    def test_malformed_choice_arrays_do_not_stop_converted_comparison(self):
        projected = {
            "questionBodyText": "正しいものはどれか。",
            "choiceTextList": ["A", "B"],
            "correctChoiceText": ["正しい"],
            "explanationText": ["正しい。根拠", "間違い。根拠"],
            "questionType": "multiple_choice",
            "isLawRelated": False,
        }
        converted = [
            {
                "questionId": f"doc{index}",
                "originalQuestionChoiceText": choice,
                "correctChoiceText": verdict,
                "explanationText": explanation,
            }
            for index, (choice, verdict, explanation) in enumerate(
                [
                    ("A", "正しい", "正しい。根拠"),
                    ("B", "間違い", "間違い。根拠"),
                ]
            )
        ]

        issues = detect_issues(projected, projected, converted, converted, [])

        required = next(
            issue for issue in issues if issue["code"] == "required_field_missing"
        )
        self.assertIn("correctChoiceText", required["fields"])
        self.assertNotIn("convert_stale", {issue["code"] for issue in issues})

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
