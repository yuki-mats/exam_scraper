from __future__ import annotations

import unittest
from datetime import datetime

from scripts.upload import upload_questions_to_firestore as module


class UploadQuestionsToFirestoreTests(unittest.TestCase):
    def test_validate_required_question_fields_rejects_blank_original_question_body_text(self) -> None:
        questions = [
            {
                "questionId": "qsample",
                "questionSetId": "qs1",
                "questionText": "本文",
                "questionType": "true_false",
                "qualificationId": "sample-qualification",
                "questionTags": [],
                "originalQuestionBodyText": "  ",
            }
        ]

        with self.assertRaisesRegex(ValueError, "originalQuestionBodyText is required: qsample"):
            module.validate_required_question_fields(questions, "sample.json")

    def test_validate_required_question_fields_rejects_grouped_candidate_without_choice_content(self) -> None:
        questions = [
            {
                "questionId": "qsample_1",
                "questionSetId": "qs1",
                "questionType": "true_false",
                "questionText": "本文",
                "qualificationId": "sample-qualification",
                "questionTags": [],
                "isOfficial": True,
                "isDeleted": False,
                "isChoiceOnly": False,
                "originalQuestionId": "qsample",
                "originalQuestionBodyText": "元問題文",
                "originalQuestionChoiceText": "",
                "correctChoiceText": "正しい",
            }
        ]

        with self.assertRaisesRegex(
            ValueError,
            "originalQuestionChoiceText or originalQuestionChoiceImageUrls is required: qsample_1",
        ):
            module.validate_required_question_fields(questions, "sample.json")

    def test_validate_required_question_fields_accepts_grouped_candidate_with_choice_images_only(self) -> None:
        questions = [
            {
                "questionId": "qsample_1",
                "questionSetId": "qs1",
                "questionType": "true_false",
                "questionText": "本文",
                "qualificationId": "sample-qualification",
                "questionTags": [],
                "isOfficial": True,
                "isDeleted": False,
                "isChoiceOnly": False,
                "originalQuestionId": "qsample",
                "originalQuestionBodyText": "元問題文",
                "originalQuestionChoiceText": "",
                "originalQuestionChoiceImageUrls": ["https://example.test/choice-a.png"],
                "correctChoiceText": "正しい",
            },
            {
                "questionId": "qsample_2",
                "questionSetId": "qs1",
                "questionType": "true_false",
                "questionText": "本文",
                "qualificationId": "sample-qualification",
                "questionTags": [],
                "isOfficial": True,
                "isDeleted": False,
                "isChoiceOnly": False,
                "originalQuestionId": "qsample",
                "originalQuestionBodyText": "元問題文",
                "originalQuestionChoiceText": "",
                "originalQuestionChoiceImageUrls": ["https://example.test/choice-b.png"],
                "correctChoiceText": "間違い",
            },
        ]

        module.validate_required_question_fields(questions, "sample.json")

    def test_validate_required_question_fields_recalculates_groupable_flags(self) -> None:
        questions = [
            {
                "questionId": "qsample_1",
                "questionSetId": "qs1",
                "questionType": "true_false",
                "questionText": "本文",
                "qualificationId": "sample-qualification",
                "questionTags": [],
                "isOfficial": True,
                "isDeleted": False,
                "isChoiceOnly": False,
                "isGroupable": False,
                "originalQuestionId": "qsample",
                "originalQuestionBodyText": "元問題文",
                "originalQuestionChoiceText": "選択肢A",
                "correctChoiceText": "正解",
            },
            {
                "questionId": "qsample_2",
                "questionSetId": "qs1",
                "questionType": "true_false",
                "questionText": "本文",
                "qualificationId": "sample-qualification",
                "questionTags": [],
                "isOfficial": True,
                "isDeleted": False,
                "isChoiceOnly": False,
                "isGroupable": False,
                "originalQuestionId": "qsample",
                "originalQuestionBodyText": "元問題文",
                "originalQuestionChoiceText": "選択肢B",
                "correctChoiceText": "不正解",
            },
        ]

        module.validate_required_question_fields(questions, "sample.json")

        self.assertTrue(all(q["isGroupable"] for q in questions))
        self.assertEqual([q["correctChoiceText"] for q in questions], ["正しい", "間違い"])

    def test_build_doc_data_keeps_required_original_question_body_text(self) -> None:
        doc_data = module.build_doc_data(
            {
                "questionId": "qsample",
                "questionSetId": "qs1",
                "questionText": "本文",
                "questionType": "true_false",
                "qualificationId": "sample-qualification",
                "questionTags": [],
                "originalQuestionBodyText": "元問題文",
            },
            datetime(2026, 4, 13, 12, 0, 0),
        )

        self.assertEqual(doc_data["originalQuestionBodyText"], "元問題文")

    def test_build_doc_data_keeps_original_question_choice_image_urls(self) -> None:
        doc_data = module.build_doc_data(
            {
                "questionId": "qsample",
                "questionSetId": "qs1",
                "questionText": "本文",
                "questionType": "true_false",
                "qualificationId": "sample-qualification",
                "questionTags": [],
                "originalQuestionBodyText": "元問題文",
                "originalQuestionChoiceImageUrls": ["https://example.test/choice.png"],
            },
            datetime(2026, 4, 13, 12, 0, 0),
        )

        self.assertEqual(
            doc_data["originalQuestionChoiceImageUrls"],
            ["https://example.test/choice.png"],
        )

    def test_build_doc_data_sets_meta_fields(self) -> None:
        now = datetime(2026, 4, 13, 12, 0, 0)
        doc_data = module.build_doc_data(
            {
                "questionId": "qsample",
                "questionSetId": "qs1",
                "questionText": "本文",
                "questionType": "true_false",
                "qualificationId": "sample-qualification",
                "questionTags": [],
                "originalQuestionBodyText": "元問題文",
            },
            now,
        )
        self.assertEqual(doc_data["createdAt"], now)
        self.assertEqual(doc_data["updatedAt"], now)
        self.assertTrue(doc_data["createdById"])
        self.assertTrue(doc_data["updatedById"])


if __name__ == "__main__":
    unittest.main()
