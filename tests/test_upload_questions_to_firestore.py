from __future__ import annotations

import unittest
import contextlib
import io
import json
from pathlib import Path
import tempfile
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
from google.api_core.datetime_helpers import DatetimeWithNanoseconds

from scripts.upload import upload_questions_to_firestore as module
from scripts.common.image_storage_urls import build_public_storage_url
from scripts.common.independent_question_images import (
    INDEPENDENT_IMAGE_REQUIRED_FIELD,
)


class UploadQuestionsToFirestoreTests(unittest.TestCase):
    @staticmethod
    def independent_question(**overrides):
        question = {
            "questionId": "qsample",
            "questionSetId": "qs1",
            "questionText": "本文",
            "questionType": "single_choice",
            "qualificationId": "sample-qualification",
            "questionTags": [],
            "isOfficial": True,
            "isDeleted": False,
            "isChoiceOnly": False,
            "isGroupable": False,
            "originalQuestionId": "original-1",
            "originalQuestionBodyText": "元問題文",
            "correctChoiceText": "正しい",
            "examSource": "独自問題",
        }
        question.update(overrides)
        return question

    def test_top_level_merge_fields_replaces_nested_maps_as_one_field(self) -> None:
        doc_data = {
            "questionId": "q1",
            "lawRevisionFacts": {"current": {"article": "4"}},
        }

        self.assertEqual(
            module.top_level_merge_fields(doc_data),
            ["questionId", "lawRevisionFacts"],
        )

    def test_resolve_write_fields_accepts_explanation_only_contract(self) -> None:
        self.assertEqual(
            module.resolve_write_fields({"writeFields": ["explanationText"]}),
            ("explanationText",),
        )

    def test_resolve_write_fields_accepts_answer_correction_contract(self) -> None:
        self.assertEqual(
            module.resolve_write_fields(
                {"writeFields": ["correctChoiceText", "explanationText"]}
            ),
            ("correctChoiceText", "explanationText"),
        )

    def test_resolve_write_fields_accepts_suggestion_normalization_contract(self) -> None:
        self.assertEqual(
            module.resolve_write_fields(
                {"writeFields": ["suggestedQuestions", "suggestedQuestionDetails"]}
            ),
            ("suggestedQuestions", "suggestedQuestionDetails"),
        )

    def test_resolve_write_fields_accepts_routing_normalization_contract(self) -> None:
        self.assertEqual(
            module.resolve_write_fields(
                {"writeFields": ["qualificationId", "listGroupId"]}
            ),
            ("qualificationId", "listGroupId"),
        )

    def test_validate_routing_patch_requires_list_group_to_match_year(self) -> None:
        question = {
            "questionId": "routing-1",
            "questionText": "問題文",
            "examYear": 2022,
            "isDeleted": False,
            "isChoiceOnly": False,
            "qualificationId": "gas-shunin-kou",
            "listGroupId": "2022",
        }
        module.validate_question_patch_questions(
            [question], ("qualificationId", "listGroupId"), "repair.json"
        )
        question["listGroupId"] = "2021"
        with self.assertRaisesRegex(ValueError, "must match examYear"):
            module.validate_question_patch_questions(
                [question], ("qualificationId", "listGroupId"), "repair.json"
            )

    def test_validate_suggestion_patch_requires_matching_max_three_details(self) -> None:
        module.validate_question_patch_questions(
            [
                {
                    "questionId": "suggestion-1",
                    "questionText": "問題文",
                    "isDeleted": False,
                    "isChoiceOnly": False,
                    "suggestedQuestions": ["質問1", "質問2", "質問3"],
                    "suggestedQuestionDetails": [
                        {"question": "質問1", "answer": "回答1"},
                        {"question": "質問2", "answer": "回答2"},
                        {"question": "質問3", "answer": "回答3"},
                    ],
                }
            ],
            ("suggestedQuestions", "suggestedQuestionDetails"),
            "repair.json",
        )

    def test_validate_suggestion_patch_rejects_four_details(self) -> None:
        with self.assertRaisesRegex(ValueError, "1 to 3"):
            module.validate_question_patch_questions(
                [
                    {
                        "questionId": "suggestion-1",
                        "questionText": "問題文",
                        "isDeleted": False,
                        "isChoiceOnly": False,
                        "suggestedQuestions": ["質問"] * 4,
                        "suggestedQuestionDetails": [
                            {"question": "質問", "answer": "回答"}
                            for _ in range(4)
                        ],
                    }
                ],
                ("suggestedQuestions", "suggestedQuestionDetails"),
                "repair.json",
            )

    def test_validate_suggestion_rollback_explicitly_allows_legacy_count(self) -> None:
        module.validate_question_patch_questions(
            [
                {
                    "questionId": "suggestion-rollback-1",
                    "questionText": "問題文",
                    "isDeleted": False,
                    "isChoiceOnly": False,
                    "suggestedQuestions": [f"質問{index}" for index in range(4)],
                    "suggestedQuestionDetails": [
                        {"question": f"質問{index}", "answer": f"回答{index}"}
                        for index in range(4)
                    ],
                }
            ],
            ("suggestedQuestions", "suggestedQuestionDetails"),
            "rollback.json",
            allow_legacy_suggestion_rollback=True,
        )

    def test_resolve_write_fields_accepts_semantic_recovery_contract(self) -> None:
        self.assertEqual(
            module.resolve_write_fields(
                {
                    "writeFields": [
                        "originalQuestionBodyText",
                        "originalQuestionChoiceText",
                        "correctChoiceText",
                        "explanationText",
                    ]
                }
            ),
            (
                "originalQuestionBodyText",
                "originalQuestionChoiceText",
                "correctChoiceText",
                "explanationText",
            ),
        )

    def test_build_semantic_recovery_patch_writes_only_approved_fields(self) -> None:
        now = datetime(2026, 8, 29, 12, 0, 0)
        actual = module.build_patch_doc_data(
            {
                "originalQuestionBodyText": "対象本文",
                "originalQuestionChoiceText": "対象選択肢",
                "correctChoiceText": "間違い",
                "explanationText": "間違い。対象選択肢の理由。",
                "questionText": "変更しない問題文",
            },
            (
                "originalQuestionBodyText",
                "originalQuestionChoiceText",
                "correctChoiceText",
                "explanationText",
            ),
            now,
        )
        self.assertEqual(
            actual,
            {
                "originalQuestionBodyText": "対象本文",
                "originalQuestionChoiceText": "対象選択肢",
                "correctChoiceText": "間違い",
                "explanationText": "間違い。対象選択肢の理由。",
                "updatedAt": now,
                "updatedById": module.UPDATED_BY_ID,
            },
        )

    def test_resolve_write_fields_accepts_question_text_recovery_contract(self) -> None:
        fields = [
            "originalQuestionBodyText",
            "originalQuestionChoiceText",
            "questionText",
            "correctChoiceText",
            "explanationText",
        ]
        self.assertEqual(module.resolve_write_fields({"writeFields": fields}), tuple(fields))

    def test_resolve_write_fields_rejects_unapproved_patch_contract(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported writeFields"):
            module.resolve_write_fields(
                {"writeFields": ["questionText", "explanationText"]}
            )

    def test_build_patch_doc_data_writes_only_explanation_and_update_metadata(self) -> None:
        now = datetime(2026, 8, 29, 12, 0, 0)

        actual = module.build_patch_doc_data(
            {
                "explanationText": "正しい。理由。",
                "correctChoiceText": "正しい",
                "questionTags": [],
            },
            ("explanationText",),
            now,
        )

        self.assertEqual(
            actual,
            {
                "explanationText": "正しい。理由。",
                "updatedAt": now,
                "updatedById": module.UPDATED_BY_ID,
            },
        )

    def test_validate_explanation_patch_accepts_legacy_doc_without_optional_fields(self) -> None:
        module.validate_explanation_patch_questions(
            [
                {
                    "questionId": "legacy-1",
                    "questionText": "問題文[quote]記述[/quote]",
                    "explanationText": "正しい。理由。",
                    "isDeleted": False,
                    "isChoiceOnly": False,
                }
            ],
            "repair.json",
        )

    def test_validate_explanation_patch_rejects_choice_only_target(self) -> None:
        with self.assertRaisesRegex(ValueError, "isChoiceOnly=false"):
            module.validate_explanation_patch_questions(
                [
                    {
                        "questionId": "choice-only-1",
                        "questionText": "問題文",
                        "explanationText": "解説",
                        "isDeleted": False,
                        "isChoiceOnly": True,
                    }
                ],
                "repair.json",
            )

    def test_validate_answer_correction_patch_requires_matching_prefix(self) -> None:
        with self.assertRaisesRegex(ValueError, "prefix mismatch"):
            module.validate_question_patch_questions(
                [
                    {
                        "questionId": "answer-fix-1",
                        "questionText": "問題文",
                        "correctChoiceText": "正しい",
                        "explanationText": "間違い。理由。",
                        "isDeleted": False,
                        "isChoiceOnly": False,
                    }
                ],
                ("correctChoiceText", "explanationText"),
                "repair.json",
            )

    def test_build_answer_correction_patch_writes_only_approved_fields(self) -> None:
        now = datetime(2026, 8, 29, 12, 0, 0)

        actual = module.build_patch_doc_data(
            {
                "correctChoiceText": "間違い",
                "explanationText": "間違い。理由。",
                "questionText": "変更しない本文",
            },
            ("correctChoiceText", "explanationText"),
            now,
        )

        self.assertEqual(
            actual,
            {
                "correctChoiceText": "間違い",
                "explanationText": "間違い。理由。",
                "updatedAt": now,
                "updatedById": module.UPDATED_BY_ID,
            },
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

    def test_independent_question_requires_image_gate_marker(self) -> None:
        with self.assertRaisesRegex(ValueError, INDEPENDENT_IMAGE_REQUIRED_FIELD):
            module.validate_required_question_fields(
                [self.independent_question()], "sample.json"
            )

    def test_image_required_independent_question_is_blocked_without_generated_image(self) -> None:
        with self.assertRaisesRegex(ValueError, "独自生成画像がありません"):
            module.validate_required_question_fields(
                [
                    self.independent_question(
                        **{INDEPENDENT_IMAGE_REQUIRED_FIELD: True}
                    )
                ],
                "sample.json",
            )

    def test_independent_question_accepts_generated_image_and_internal_marker_is_not_uploaded(self) -> None:
        generated_url = build_public_storage_url(
            "sample-qualification",
            "originalized_original-1_question_01.png",
        )
        question = self.independent_question(
            **{
                INDEPENDENT_IMAGE_REQUIRED_FIELD: True,
                "questionImageUrls": [generated_url],
            }
        )

        module.validate_required_question_fields([question], "sample.json")

        self.assertNotIn(
            INDEPENDENT_IMAGE_REQUIRED_FIELD,
            module.build_doc_data_base(question),
        )

    def test_independent_question_rejects_public_image_without_originalized_filename(self) -> None:
        source_like_url = build_public_storage_url(
            "sample-qualification",
            "source_question_01.png",
        )
        question = self.independent_question(
            **{
                INDEPENDENT_IMAGE_REQUIRED_FIELD: True,
                "questionImageUrls": [source_like_url],
            }
        )

        with self.assertRaisesRegex(ValueError, "originalized_"):
            module.validate_required_question_fields([question], "sample.json")

    def test_official_past_exam_image_does_not_use_independent_image_gate(self) -> None:
        question = self.independent_question(
            examSource="サンプル資格, 2025年, 問1",
            examYear=2025,
            questionImageUrls=["https://example.test/source-image.png"],
        )

        module.validate_required_question_fields([question], "sample.json")

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

    def test_compare_keys_cover_every_field_written_from_upload_ready(self) -> None:
        question = {
            "questionId": "qsample",
            "questionSetId": "qs1",
            "listGroupId": "2026",
            "originalQuestionId": "original-1",
            "originalQuestionBodyText": "元問題文",
            "questionBodyText": "問題文",
            "originalQuestionChoiceText": "選択肢",
            "originalQuestionChoiceImageUrls": ["https://example.test/choice.png"],
            "questionText": "本文",
            "questionType": "true_false",
            "qualificationId": "sample-qualification",
            "correctChoiceText": "正しい",
            "explanationText": "解説",
            "knowledgeText": "補足知識",
            "suggestedQuestions": [],
            "suggestedQuestionDetails": [],
            "explanationReferences": [],
            "lawReferences": [],
            "lawRevisionFacts": {},
            "isLawRelated": False,
            "lawGroundedExplanationNotNeeded": False,
            "examYear": 2026,
            "examSource": "サンプル試験",
            "questionTags": [],
            "questionImageUrls": ["https://example.test/question.png"],
            "importKey": "sample-import-key",
            "isOfficial": True,
            "isDeleted": False,
            "isChoiceOnly": False,
            "isGroupable": False,
        }

        blank_question = {
            "questionType": "fill_in_blank",
            "fillInBlankContent": {
                "schemaVersion": 1,
                "segments": [{"type": "blank", "blankId": "b1", "placementId": "p1"}],
                "blanks": [{"blankId": "b1", "blankIndex": 1,
                            "correctAnswerText": "答え", "acceptedAnswerTexts": ["答え"]}],
                "answerInput": {"allowedMethods": ["text_input"], "defaultMethod": "text_input"},
            },
        }
        self.assertEqual(
            set(module.build_doc_data_base(question)) | set(module.build_doc_data_base(blank_question)),
            set(module.DOC_COMPARE_KEYS)
            - set(module.PRODUCTION_CLIENT_OMITTED_FIELDS),
        )
        self.assertNotIn(
            "explanationReferences",
            module.build_doc_data_base(question),
        )

    def test_choice_only_doc_omits_explanation_and_saved_suggestions(self) -> None:
        question = {
            "questionId": "q-choice-only",
            "questionSetId": "qs1",
            "listGroupId": "2026",
            "originalQuestionId": "original-1",
            "originalQuestionBodyText": "元問題文",
            "questionBodyText": "問題文",
            "originalQuestionChoiceText": "誤答肢",
            "questionText": "本文",
            "questionType": "flash_card",
            "qualificationId": "sample-qualification",
            "correctChoiceText": "間違い",
            "explanationText": "古い選択肢別解説",
            "suggestedQuestions": ["古い質問"],
            "suggestedQuestionDetails": [
                {"question": "古い質問", "answer": "古い回答"}
            ],
            "examSource": "サンプル試験",
            "questionTags": [],
            "isOfficial": True,
            "isDeleted": False,
            "isChoiceOnly": True,
            "isGroupable": False,
        }

        actual = module.build_doc_data_base(question)

        self.assertNotIn("explanationText", actual)
        self.assertNotIn("suggestedQuestions", actual)
        self.assertNotIn("suggestedQuestionDetails", actual)
        self.assertEqual(
            module.stale_public_fields_to_delete(
                actual,
                {
                    "explanationText": "古い選択肢別解説",
                    "explanationReferences": [
                        {
                            "title": "公式資料",
                            "sourceUrl": "https://example.test/reference",
                            "referenceDate": "2026-07-29",
                        }
                    ],
                    "suggestedQuestions": ["古い質問"],
                    "suggestedQuestionDetails": [
                        {"question": "古い質問", "answer": "古い回答"}
                    ],
                },
            ),
            (
                "explanationReferences",
                "explanationText",
                "suggestedQuestions",
                "suggestedQuestionDetails",
            ),
        )

    def test_regular_doc_removes_stale_suggestions_when_current_artifact_has_none(
        self,
    ) -> None:
        new_base = module.build_doc_data_base(
            {
                "questionId": "q-regular",
                "questionSetId": "qs1",
                "listGroupId": "2026",
                "originalQuestionId": "original-1",
                "originalQuestionBodyText": "元問題文",
                "questionBodyText": "問題文",
                "originalQuestionChoiceText": "選択肢",
                "questionText": "本文",
                "questionType": "true_false",
                "qualificationId": "sample-qualification",
                "correctChoiceText": "正しい",
                "explanationText": "基本解説",
                "examSource": "サンプル試験",
                "questionTags": [],
                "isOfficial": True,
                "isDeleted": False,
                "isChoiceOnly": False,
                "isGroupable": True,
            }
        )

        self.assertNotIn("suggestedQuestions", new_base)
        self.assertNotIn("suggestedQuestionDetails", new_base)
        self.assertEqual(
            module.stale_public_fields_to_delete(
                new_base,
                {
                    "suggestedQuestions": ["旧補足"],
                    "suggestedQuestionDetails": [
                        {"question": "旧補足", "answer": "旧回答"}
                    ],
                },
            ),
            ("suggestedQuestions", "suggestedQuestionDetails"),
        )

    def test_release_incompatible_field_is_removed_from_regular_doc(self) -> None:
        actual = module.build_doc_data_base(
            {
                "questionId": "q-regular",
                "questionSetId": "qs1",
                "originalQuestionBodyText": "元問題文",
                "questionText": "本文",
                "questionType": "true_false",
                "qualificationId": "sample-qualification",
                "questionTags": [],
                "isOfficial": True,
                "isDeleted": False,
                "isChoiceOnly": False,
                "isGroupable": False,
                "explanationReferences": [
                    {
                        "title": "公式資料",
                        "sourceUrl": "https://example.test/reference",
                        "referenceDate": "2026-07-29",
                    }
                ],
            }
        )

        self.assertNotIn("explanationReferences", actual)
        self.assertEqual(
            module.stale_public_fields_to_delete(
                actual,
                {"explanationReferences": []},
            ),
            ("explanationReferences",),
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

    def test_guarded_write_uses_last_update_precondition_for_existing_document(self) -> None:
        class FakeBatch:
            def __init__(self) -> None:
                self.calls = []

            def update(self, ref, data, *, option=None):
                self.calls.append((ref, data, option))

        class Snapshot:
            exists = True
            update_time = datetime(2026, 7, 15, 12, 0, 0)

        batch = FakeBatch()
        module.add_guarded_question_write(batch, "ref", {"field": "value"}, Snapshot())

        self.assertEqual(batch.calls[0][:2], ("ref", {"field": "value"}))
        self.assertIsInstance(batch.calls[0][2], module.firestore.LastUpdateOption)

    def test_guarded_write_uses_create_for_missing_document(self) -> None:
        class FakeBatch:
            def __init__(self) -> None:
                self.calls = []

            def create(self, ref, data):
                self.calls.append((ref, data))

        class Snapshot:
            exists = False

        batch = FakeBatch()
        module.add_guarded_question_write(batch, "ref", {"field": "value"}, Snapshot())

        self.assertEqual(batch.calls, [("ref", {"field": "value"})])

    def test_guarded_write_rejects_existing_document_without_update_time(self) -> None:
        class Snapshot:
            exists = True
            update_time = None

        with self.assertRaisesRegex(RuntimeError, "update_time"):
            module.add_guarded_question_write(object(), "ref", {}, Snapshot())

    def test_expected_live_fingerprint_detects_value_change_in_same_field(self) -> None:
        class Snapshot:
            id = "q1"
            exists = True

            def __init__(self, explanation: str) -> None:
                self.explanation = explanation

            def to_dict(self):
                return {"explanationText": self.explanation}

        expected = module.firestore_live_fingerprint(
            ["q1"], {"q1": {"explanationText": "確認時"}}
        )

        with self.assertRaisesRegex(RuntimeError, "Firestore documentが更新"):
            module.require_expected_live_fingerprint(
                ["q1"], [Snapshot("別更新")], expected
            )

    def test_expected_live_fingerprint_accepts_unchanged_missing_document(self) -> None:
        class Snapshot:
            id = "q1"
            exists = False

            def to_dict(self):
                return None

        expected = module.firestore_live_fingerprint(["q1"], {})

        module.require_expected_live_fingerprint(["q1"], [Snapshot()], expected)


class NarrowExplanationUploadTests(unittest.TestCase):
    stamp = "2026-10-14T00:00:00.123456789Z"
    mask = ["explanationText", "suggestedQuestionDetails"]

    def question(self, identity="target"):
        return {"questionId": identity, "qualificationId": "fixture", "questionText": "問題",
                "isDeleted": False, "isChoiceOnly": False, "explanationText": "正しい。新しい説明。",
                "suggestedQuestions": ["補足"],
                "suggestedQuestionDetails": [{"question": "補足", "answer": "新しい回答"}]}

    def payload(self, ids=("target",)):
        return {"writeFields": self.mask, "questions": [self.question(i) for i in ids],
                "expectedUpdateTimes": {i: self.stamp for i in ids}}

    def snapshots(self, ids=("target",), *, unchanged=False):
        result = []
        for identity in ids:
            fields = self.question(identity)
            if not unchanged:
                fields["explanationText"] = "正しい。旧説明。"
                fields["suggestedQuestionDetails"][0]["answer"] = "旧回答"
            result.append(SimpleNamespace(id=identity, exists=True,
                update_time=DatetimeWithNanoseconds.from_rfc3339(self.stamp),
                to_dict=lambda fields=fields: deepcopy(fields)))
        return result

    class Database:
        def __init__(self, snapshots, *, conflict_after_read=False):
            self.snapshots = snapshots; self.reads = []; self.collection_names = []
            self.pending = []; self.committed = []; self.batch_count = 0
            self.conflict_after_read = conflict_after_read

        def collection(self, name):
            self.collection_names.append(name)
            return self

        def document(self, identity): return identity

        def get_all(self, refs, field_paths):
            self.reads.append((refs, field_paths))
            return self.snapshots

        def batch(self):
            self.batch_count += 1
            db = self
            class Batch:
                def update(self, ref, data, *, option): db.pending.append((ref, data, option))
                def create(self, ref, data): raise AssertionError("narrow upload must not create")
                def commit(self):
                    if db.conflict_after_read: raise RuntimeError("LastUpdateOption conflict")
                    db.committed.extend(db.pending)
            return Batch()

    def upload(self, payload, db, *, dry_run=False, expected_hash=None, raw=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "upload.json"
            path.write_text(raw if raw is not None else json.dumps(payload), encoding="utf-8")
            with patch.object(module, "init_firestore", return_value=db) as init, \
                    patch.dict(module.os.environ, {"QUESTION_PUBLISH_EXPECTED_LIVE_HASH": expected_hash or ""}), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                module.upload_questions(str(path), dry_run=dry_run)
                return output.getvalue(), init.call_count

    def test_new_mask_validates_payload_mirror_types_flags_and_never_relaxes_rollback(self):
        self.assertEqual(module.resolve_write_fields(self.payload()), tuple(self.mask))
        question = self.question()
        module.validate_question_patch_questions([question], tuple(self.mask), "fixture")
        invalid = [dict(explanationText=""), dict(suggestedQuestions=["別の補足"]),
                   dict(isDeleted=True), dict(isChoiceOnly=True),
                   dict(suggestedQuestionDetails=[{"question": "補足", "answer": ""}]),
                   dict(suggestedQuestions=["12"], suggestedQuestionDetails=[{"question": 12, "answer": "答え"}]),
                   dict(suggestedQuestions=["補足"] * 4,
                        suggestedQuestionDetails=[{"question": "補足", "answer": "回答"}] * 4)]
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValueError):
                module.validate_question_patch_questions([{**question, **change}], tuple(self.mask),
                    "fixture", allow_legacy_suggestion_rollback=True)

    def test_update_time_structure_rejected_without_sdk_even_in_dry_run(self):
        original = self.payload()
        invalid = [None, {}, [], {"target": True}, {"target": "bad"},
                   {"target": "2026-02-30T00:00:00.123456789Z"},
                   {"target": "2026-10-14T00:00:00.1234567890Z"},
                   {"target": "2026-10-14T00:00:00"}, {"other": self.stamp},
                   {"target": self.stamp, "extra": self.stamp}]
        for value in invalid:
            with self.subTest(value=value), patch.object(module, "init_firestore") as init:
                with self.assertRaises(ValueError):
                    self.upload({**original, "expectedUpdateTimes": value}, object(), dry_run=True)
                init.assert_not_called()
        for mutation in (dict(writeFields=None), dict(questions=[]),
                         dict(questions=[self.question(), self.question()]),
                         dict(questions=[self.question("parent/child")])):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.upload({**original, **mutation}, object(), dry_run=True)

    def test_duplicate_timestamp_json_keys_rejected(self):
        raw = json.dumps(self.payload()).replace(
            '"expectedUpdateTimes": {"target": "' + self.stamp + '"}',
            '"expectedUpdateTimes": {"target": "' + self.stamp + '", "target": "' + self.stamp + '"}')
        with self.assertRaisesRegex(ValueError, "unique nonempty"):
            self.upload(None, object(), dry_run=True, raw=raw)
        raw = json.dumps(self.payload()).replace(
            '"expectedUpdateTimes": {', '"expectedUpdateTimes": {}, "expectedUpdateTimes": {')
        with self.assertRaisesRegex(ValueError, "unique nonempty"):
            self.upload(None, object(), dry_run=True, raw=raw)

    def test_valid_nano_contract_dry_run_has_no_sdk(self):
        _, initialized = self.upload(self.payload(), object(), dry_run=True)
        self.assertEqual(initialized, 0)

    def test_upload_only_changes_approved_fields_and_reuses_guarded_snapshot(self):
        snapshots = self.snapshots(); db = self.Database(snapshots)
        expected = module.firestore_live_fingerprint(["target"], {"target": snapshots[0].to_dict()})
        self.upload(self.payload(), db, expected_hash=expected)
        self.assertEqual(len(db.reads), 1)
        self.assertEqual(db.reads[0][0], ["target"])
        self.assertEqual(db.reads[0][1], module.EXISTING_DOC_FIELD_PATHS)
        self.assertEqual(len(db.committed), 1)
        identity, fields, option = db.committed[0]
        self.assertEqual(identity, "target")
        self.assertEqual(set(fields), {"explanationText", "suggestedQuestionDetails", "updatedAt", "updatedById"})
        self.assertNotIn("suggestedQuestions", fields)
        self.assertNotIn("createdAt", fields)
        self.assertEqual(option._last_update_time, snapshots[0].update_time)
        self.assertEqual(set(db.collection_names), {"questions"})

    def test_mismatch_missing_duplicate_or_extra_snapshot_prevents_all_batches(self):
        snapshots = self.snapshots(("target", "second"))
        newer = deepcopy(snapshots)
        newer[1].update_time = DatetimeWithNanoseconds.from_rfc3339("2026-10-14T00:00:00.123456790Z")
        missing = deepcopy(snapshots); missing[1].exists = False
        no_time = deepcopy(snapshots); no_time[1].update_time = datetime(2026, 10, 14)
        invalid = [newer, missing, no_time, snapshots[:1], snapshots + snapshots[:1],
                   snapshots + self.snapshots(("outside",))]
        for values in invalid:
            db = self.Database(values)
            with self.subTest(values=values), patch.object(module, "BATCH_SIZE", 1), self.assertRaises(RuntimeError):
                self.upload(self.payload(("target", "second")), db)
            self.assertEqual(db.batch_count, 0)
            self.assertEqual(db.pending, [])
            self.assertEqual(db.committed, [])

    def test_live_value_hash_conflict_prevents_write_with_matching_timestamps(self):
        db = self.Database(self.snapshots())
        with self.assertRaises(RuntimeError): self.upload(self.payload(), db, expected_hash="wrong")
        self.assertEqual(db.batch_count, 0)

    def test_time_contract_also_guards_existing_explanation_only_mask(self):
        payload = self.payload(); payload["writeFields"] = ["explanationText"]
        db = self.Database(self.snapshots())
        self.upload(payload, db)
        self.assertEqual(set(db.committed[0][1]), {"explanationText", "updatedAt", "updatedById"})

    def test_preserved_live_suggestion_mirror_and_flags_cannot_be_faked_in_payload(self):
        for change in (dict(suggestedQuestions=["different"]), dict(isDeleted=True), dict(isChoiceOnly=True)):
            values = self.snapshots(); fields = {**values[0].to_dict(), **change}
            values[0].to_dict = lambda fields=fields: fields
            db = self.Database(values)
            with self.subTest(change=change), self.assertRaises(RuntimeError): self.upload(self.payload(), db)
            self.assertEqual(db.batch_count, 0)

    def test_same_payload_resend_skips_without_metadata_or_manifest_write(self):
        db = self.Database(self.snapshots(unchanged=True))
        output, _ = self.upload(self.payload(), db)
        self.assertEqual(db.committed, [])
        self.assertIn("スキップ 1", output)
        self.assertEqual(set(db.collection_names), {"questions"})

    def test_read_after_guard_conflict_keeps_last_update_option_and_no_committed_write(self):
        db = self.Database(self.snapshots(), conflict_after_read=True)
        output, _ = self.upload(self.payload(), db)
        self.assertEqual(db.committed, [])
        self.assertIsInstance(db.pending[0][2], module.firestore.LastUpdateOption)
        self.assertIn("エラー 1", output)


class VerifiedMaintenanceUploadTests(unittest.TestCase):
    stamp = NarrowExplanationUploadTests.stamp
    mask = list(module.LAW_FACTS_DETAILS_WRITE_FIELDS)
    Database = NarrowExplanationUploadTests.Database
    upload = NarrowExplanationUploadTests.upload
    payload = NarrowExplanationUploadTests.payload

    def question(self, identity='target'):
        return {**NarrowExplanationUploadTests.question(self, identity),
                'lawRevisionFacts': {'auditStatus': 'same_as_current',
                                     'auditedAt': '2026-07-28',
                                     'differenceFacts': ['条文で定める測定場所を説明する。']}}

    def snapshots(self, ids=('target',), *, unchanged=False):
        snapshots = NarrowExplanationUploadTests.snapshots(self, ids, unchanged=unchanged)
        if not unchanged:
            for snapshot in snapshots:
                value = snapshot.to_dict()
                value['lawRevisionFacts']['differenceFacts'] = ['旧説明。']
                snapshot.to_dict = lambda value=value: deepcopy(value)
        return snapshots

    def test_all_maintenance_masks_dry_run_and_exact_version_requirement(self):
        for mask in [module.DETAILS_ONLY_WRITE_FIELDS, module.LAW_FACTS_DETAILS_WRITE_FIELDS]:
            payload = self.payload(); payload['writeFields'] = list(mask)
            with self.subTest(mask=mask):
                self.assertEqual(module.resolve_write_fields(payload), mask)
                _, initialized = self.upload(payload, object(), dry_run=True)
                self.assertEqual(initialized, 0)
                del payload['expectedUpdateTimes']
                with self.assertRaisesRegex(ValueError, 'exact expectedUpdateTimes'):
                    self.upload(payload, object(), dry_run=True)

    def test_details_only_writes_no_question_or_explanation_fields(self):
        payload = self.payload(); payload['writeFields'] = list(module.DETAILS_ONLY_WRITE_FIELDS)
        db = self.Database(self.snapshots())
        self.upload(payload, db)
        self.assertEqual(set(db.committed[0][1]), {'suggestedQuestionDetails', 'updatedAt', 'updatedById'})
        self.assertEqual(db.committed[0][2]._last_update_time, db.snapshots[0].update_time)
        self.assertEqual(len(db.reads), 1)

    def test_legal_map_only_replaces_difference_facts_and_preserves_audit_metadata(self):
        payload = self.payload(); db = self.Database(self.snapshots())
        self.upload(payload, db)
        values = db.committed[0][1]
        self.assertEqual(set(values), set(self.mask) | {'updatedAt', 'updatedById'})
        self.assertEqual(values['lawRevisionFacts'], payload['questions'][0]['lawRevisionFacts'])
        self.assertEqual(values['lawRevisionFacts']['auditedAt'], '2026-07-28')
        self.assertNotIn('suggestedQuestions', values)

    def test_legal_payload_shape_empty_facts_and_non_string_facts_are_refused(self):
        for facts in [None, {}, {'auditStatus': 'same_as_current'},
                      {'auditStatus': 'same_as_current', 'differenceFacts': []},
                      {'auditStatus': 'same_as_current', 'differenceFacts': [' ']},
                      {'auditStatus': 'same_as_current', 'differenceFacts': [12]},
                      {'auditStatus': 'same_as_current', 'differenceFacts': ['new'], 'unknown': 'value'}]:
            payload = self.payload(); payload['questions'][0]['lawRevisionFacts'] = facts
            with self.subTest(facts=facts), self.assertRaises(ValueError):
                self.upload(payload, object(), dry_run=True)

    def test_live_provenance_verdict_missing_facts_and_extra_metadata_cannot_change(self):
        for change in [None, {'auditStatus': 'hold'}, {'auditedAt': '2026-10-15'},
                       {'current': {'correctChoiceText': '間違い'}}, {'notes': ['extra']}]:
            snapshots = self.snapshots(); value = snapshots[0].to_dict()
            if change is None:
                del value['lawRevisionFacts']
            else:
                value['lawRevisionFacts'].update(change)
            snapshots[0].to_dict = lambda value=value: deepcopy(value)
            db = self.Database(snapshots)
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                self.upload(self.payload(), db)
            self.assertEqual(db.batch_count, 0)
            self.assertFalse(db.pending)

    def test_new_masks_refuse_live_question_mirror_or_flags_before_any_batch(self):
        for mask in [module.DETAILS_ONLY_WRITE_FIELDS, module.LAW_FACTS_DETAILS_WRITE_FIELDS]:
            for key, value in [('suggestedQuestions', ['other']), ('isDeleted', True), ('isChoiceOnly', True)]:
                snapshots = self.snapshots(); fields = {**snapshots[0].to_dict(), key: value}
                snapshots[0].to_dict = lambda fields=fields: deepcopy(fields)
                db = self.Database(snapshots); payload = self.payload(); payload['writeFields'] = list(mask)
                with self.subTest(mask=mask, key=key), self.assertRaises(RuntimeError):
                    self.upload(payload, db)
                self.assertEqual(db.batch_count, 0)

    def test_legal_payload_cannot_relabel_preserved_metadata_with_matching_time(self):
        payload = self.payload(); payload['questions'][0]['lawRevisionFacts']['auditedAt'] = '2026-10-15'
        db = self.Database(self.snapshots())
        with self.assertRaisesRegex(RuntimeError, 'preserved law metadata'):
            self.upload(payload, db)
        self.assertFalse(db.pending)

    def test_legal_resend_skips_and_read_after_guard_conflict_uses_CAS(self):
        db = self.Database(self.snapshots(unchanged=True))
        output, _ = self.upload(self.payload(), db)
        self.assertFalse(db.committed)
        self.assertIn('スキップ 1', output)
        db = self.Database(self.snapshots(), conflict_after_read=True)
        output, _ = self.upload(self.payload(), db)
        self.assertFalse(db.committed)
        self.assertEqual(db.pending[0][2]._last_update_time, db.snapshots[0].update_time)
        self.assertIn('エラー 1', output)

    def test_new_routes_never_accept_four_suggestions_or_mismatched_prompt_types(self):
        for mask in [module.DETAILS_ONLY_WRITE_FIELDS, module.LAW_FACTS_DETAILS_WRITE_FIELDS]:
            payload = self.payload(); payload['writeFields'] = list(mask)
            payload['questions'][0]['suggestedQuestions'] *= 4
            payload['questions'][0]['suggestedQuestionDetails'] *= 4
            payload['allowLegacySuggestionRollback'] = True
            with self.subTest(mask=mask), self.assertRaises(ValueError):
                self.upload(payload, object(), dry_run=True)


if __name__ == "__main__":
    unittest.main()
