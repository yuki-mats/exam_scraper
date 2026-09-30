import unittest

from scripts.upload.upload_questions_to_firestore import validate_gx_authoring_privacy


class GxAuthoringPrivacyTest(unittest.TestCase):
    def setUp(self):
        self.question = {
            "questionId": "gx-001",
            "qualificationId": "gx-kentei",
            "examSource": "独自問題",
            "questionText": "GXが目指す変革は何か。",
            "explanationText": "経済社会の変革を含む。",
        }

    def test_accepts_only_original_content(self):
        validate_gx_authoring_privacy([self.question])

    def test_blocks_references_and_urls(self):
        for change in (
            {"lawReferences": [{"sourceUrl": "https://example.com"}]},
            {"explanationReferences": []},
            {"explanationText": "出典 https://example.com"},
            {"explanationText": "出典：外部資料"},
            {"examSource": "GX検定公式問題"},
        ):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    validate_gx_authoring_privacy([{**self.question, **change}])


if __name__ == "__main__":
    unittest.main()
