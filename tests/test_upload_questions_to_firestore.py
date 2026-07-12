from __future__ import annotations

import unittest
from datetime import datetime

from scripts.upload import upload_questions_to_firestore as module


class UploadQuestionsToFirestoreTests(unittest.TestCase):
    def test_top_level_merge_fields_replaces_nested_maps_as_one_field(self) -> None:
        doc_data = {
            "questionId": "q1",
            "lawRevisionFacts": {"current": {"article": "4"}},
        }

        self.assertEqual(
            module.top_level_merge_fields(doc_data),
            ["questionId", "lawRevisionFacts"],
        )

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

    def test_collect_exam_years_by_qualification_normalizes_and_sorts(self) -> None:
        years = module.collect_exam_years_by_qualification(
            [
                {"qualificationId": "qual-a", "examYear": "2025"},
                {"qualificationId": "qual-a", "examYear": 2026},
                {"qualificationId": "qual-a", "examYear": 2025},
                {"qualificationId": "qual-b", "examYear": 2024.0},
                {"qualificationId": "qual-b", "examYear": "invalid"},
                {"qualificationId": "", "examYear": 2023},
            ]
        )

        self.assertEqual(years, {"qual-a": [2026, 2025], "qual-b": [2024]})

    def test_merge_official_exam_years_map_preserves_existing_years(self) -> None:
        merged = module.merge_official_exam_years_map(
            {
                "qual-a": [2024, "2023", "invalid"],
                "qual-c": [2022],
            },
            {
                "qual-a": [2026, 2025],
                "qual-b": [2024],
            },
        )

        self.assertEqual(
            merged,
            {
                "qual-a": [2026, 2025, 2024, 2023],
                "qual-b": [2024],
                "qual-c": [2022],
            },
        )

    def test_fetch_existing_question_snapshots_uses_field_mask_with_get_all(self) -> None:
        class FakeDb:
            def __init__(self) -> None:
                self.field_paths = None

            def get_all(self, refs, field_paths=None):
                self.refs = refs
                self.field_paths = field_paths
                return ["snapshot"]

        db = FakeDb()
        refs = ["ref-1"]

        self.assertEqual(module.fetch_existing_question_snapshots(db, refs), ["snapshot"])
        self.assertEqual(db.refs, refs)
        self.assertEqual(db.field_paths, module.EXISTING_DOC_FIELD_PATHS)

    def test_fetch_existing_question_snapshots_uses_field_mask_with_ref_get_fallback(self) -> None:
        class FakeRef:
            def __init__(self) -> None:
                self.field_paths = None

            def get(self, field_paths=None):
                self.field_paths = field_paths
                return "snapshot"

        class FakeDb:
            pass

        ref = FakeRef()

        self.assertEqual(module.fetch_existing_question_snapshots(FakeDb(), [ref]), ["snapshot"])
        self.assertEqual(ref.field_paths, module.EXISTING_DOC_FIELD_PATHS)


if __name__ == "__main__":
    unittest.main()
