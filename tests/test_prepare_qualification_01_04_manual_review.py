from __future__ import annotations

from pathlib import Path
import unittest

from scripts.check import prepare_qualification_01_04_manual_review as module
from scripts.merge.patch_views import PatchArtifactEntry, apply_question_type
from scripts.merge.record_projection import project_merge_record
from scripts.common.aggregate_answer_decomposition import (
    REVIEW_SCHEMA_VERSION,
    generate_statement_candidates,
    materialize_decomposition,
    source_text_hash,
)


class PrepareQualification0104ManualReviewTest(unittest.TestCase):
    @staticmethod
    def _patch(path: str, entry: dict) -> PatchArtifactEntry:
        return PatchArtifactEntry(path=Path(path), entry=entry)

    def test_aggregate_answer_projection_discards_old_ids_and_choice_data(self) -> None:
        body = "組合せを選べ。\nA 原文一。\nB 原文二。"
        source = {
            "canonical_question_key": "sample:2026:q001",
            "original_question_id": "q1",
            "questionBodyText": body,
            "choiceTextList": ["A、B", "A、C"],
            "questionType": "flash_card",
            "correctChoiceText": ["正しい", "間違い"],
            "explanationText": ["旧解説"],
            "firestoreQuestionIds": ["old-1", "old-2"],
            "firestoreSourceQuestions": [{"questionId": "old-1"}],
        }
        candidate = generate_statement_candidates(body)["candidates"][0]
        review = {
            "schemaVersion": REVIEW_SCHEMA_VERSION,
            "sourceHash": source_text_hash(body),
            "classification": "target",
            "candidateId": candidate["candidateId"],
            "decision": "approve",
            "issueCodes": [],
        }
        patch = {
            "questionType": "true_false",
            "isCalculationQuestion": False,
            **materialize_decomposition(source, [review, dict(review)]),
        }
        payload = {"question_bodies": [source]}

        updated = apply_question_type(payload, {"firestore:old-1,old-2": patch})

        projected = payload["question_bodies"][0]
        self.assertEqual(updated, 1)
        self.assertEqual(projected["choiceTextList"], ["A 原文一。", "B 原文二。"])
        self.assertNotIn("firestoreQuestionIds", projected)
        self.assertNotIn("firestoreSourceQuestions", projected)
        self.assertNotIn("correctChoiceText", projected)
        self.assertNotIn("explanationText", projected)

    def test_normal_question_type_patch_only_updates_owned_fields(self) -> None:
        source = {
            "original_question_id": "q1",
            "questionBodyText": "現在の問題文",
            "choiceTextList": ["現在の肢1", "現在の肢2"],
            "sourceUniqueKeys": ["current:1", "current:2"],
            "questionType": "group_choice",
            "isCalculationQuestion": False,
        }
        patch = {
            "original_question_id": "別ID",
            "questionBodyText": "古い問題文",
            "choiceTextList": ["古い肢1", "古い肢2"],
            "sourceUniqueKeys": ["old:1", "old:2"],
            "questionType": "true_false",
            "isCalculationQuestion": True,
        }
        payload = {"question_bodies": [source]}

        updated = apply_question_type(payload, {"q1": patch})

        self.assertEqual(updated, 1)
        self.assertEqual(
            payload["question_bodies"][0],
            {
                **source,
                "questionType": "true_false",
                "isCalculationQuestion": True,
            },
        )

    def test_matching_aggregate_downstream_patch_remains_applicable(self) -> None:
        body = "組合せを選べ。\nA 原文一。\nB 原文二。"
        source = {
            "canonical_question_key": "sample:2026:q001",
            "original_question_id": "q1",
            "questionBodyText": body,
            "choiceTextList": ["A、B", "Aのみ"],
            "sourceUniqueKeys": ["source:1", "source:2"],
            "questionType": "group_choice",
        }
        candidate = generate_statement_candidates(body)["candidates"][0]
        review = {
            "schemaVersion": REVIEW_SCHEMA_VERSION,
            "sourceHash": source_text_hash(body),
            "classification": "target",
            "candidateId": candidate["candidateId"],
            "decision": "approve",
            "issueCodes": [],
        }
        target = {
            "questionType": "true_false",
            "isCalculationQuestion": False,
            **materialize_decomposition(source, [review, dict(review)]),
        }
        downstream = {
            **target,
            "questionIntent": "select_correct",
            "correctChoiceText": ["正しい", "間違い"],
        }

        projected = project_merge_record(
            source,
            question_type=(self._patch("question_type.json", target),),
            intent_fallback=(self._patch("intent.json", downstream),),
            strict_correct=(self._patch("correct.json", downstream),),
        )

        self.assertEqual(projected.merged2["correctChoiceText"], ["正しい", "間違い"])
        self.assertEqual(projected.update_counts["stale_aggregate_question_intent"], 0)
        self.assertEqual(projected.update_counts["stale_aggregate_correct_choice"], 0)
        self.assertIn(Path("correct.json"), projected.applied_paths)

    def test_reclassified_non_target_ignores_all_stale_aggregate_layers(self) -> None:
        source = {
            "canonical_question_key": "sample:2026:q001",
            "original_question_id": "q1",
            "questionBodyText": "個数を選べ。\nA 前提一。\nB 前提二。",
            "choiceTextList": ["一つ", "二つ"],
            "sourceUniqueKeys": ["source:1", "source:2"],
            "questionType": "group_choice",
        }
        restored = {
            "questionType": "group_choice",
            "choiceTextList": list(source["choiceTextList"]),
            "sourceUniqueKeys": list(source["sourceUniqueKeys"]),
        }
        stale = {
            "aggregateAnswerDecomposition": {
                "schemaVersion": "aggregate-answer-decomposition/v1",
                "sourceHash": source_text_hash(source["questionBodyText"]),
                "classification": "target",
                "spans": [{"start": 7, "end": 13}, {"start": 14, "end": 20}],
                "decision": "approve",
                "issueCodes": [],
            },
            "choiceTextList": ["A 前提一。", "B 前提二。"],
            "sourceUniqueKeys": ["derived:1", "derived:2"],
            "questionIntent": "select_correct",
            "correctChoiceText": ["正しい", "間違い"],
            "lawReferences": [[{"lawId": "old"}], [{"lawId": "old"}]],
            "explanationText": ["古い解説一", "古い解説二"],
            "questionSetId": "old-set",
        }

        projected = project_merge_record(
            source,
            question_type=(self._patch("question_type.json", restored),),
            intent_fallback=(self._patch("intent.json", stale),),
            strict_correct=(self._patch("correct.json", stale),),
            law_context=(self._patch("law.json", stale),),
            explanation=(self._patch("explanation.json", stale),),
            question_set=(self._patch("question_set.json", stale),),
        )

        self.assertEqual(projected.merged2["choiceTextList"], ["一つ", "二つ"])
        self.assertEqual(projected.merged2["sourceUniqueKeys"], ["source:1", "source:2"])
        self.assertNotIn("aggregateAnswerDecomposition", projected.merged2)
        self.assertNotIn("lawReferences", projected.merged2)
        self.assertNotIn("explanationText", projected.merged2)
        self.assertNotIn("questionSetId", projected.merged2)
        self.assertNotIn(Path("correct.json"), projected.applied_paths)
        self.assertEqual(projected.update_counts["stale_aggregate_question_intent"], 1)
        self.assertEqual(projected.update_counts["stale_aggregate_correct_choice"], 1)
        self.assertEqual(projected.update_counts["stale_aggregate_law_context"], 1)
        self.assertEqual(projected.update_counts["stale_aggregate_explanation"], 1)
        self.assertEqual(projected.update_counts["stale_aggregate_question_set"], 1)

    def test_ordinary_downstream_patch_remains_applicable(self) -> None:
        source = {
            "original_question_id": "q1",
            "questionBodyText": "正しいものを選べ。",
            "choiceTextList": ["選択肢一", "選択肢二"],
            "questionType": "true_false",
        }
        ordinary = {
            "questionIntent": "select_correct",
            "correctChoiceText": ["正しい", "間違い"],
        }

        projected = project_merge_record(
            source,
            intent_fallback=(self._patch("intent.json", ordinary),),
            strict_correct=(self._patch("correct.json", ordinary),),
        )

        self.assertEqual(projected.merged2["correctChoiceText"], ["正しい", "間違い"])
        self.assertEqual(projected.update_counts["stale_aggregate_question_intent"], 0)
        self.assertEqual(projected.update_counts["stale_aggregate_correct_choice"], 0)

    def test_flash_card_review_uses_one_question_level_explanation(self) -> None:
        row = module.build_review_row(
            qualification="sample",
            qualification_name="サンプル",
            source_path=Path(
                "output/sample/questions_json/2026/00_source/question_2026_1.json"
            ),
            source_file_index=1,
            question_index_in_file=1,
            global_index=1,
            question={
                "original_question_id": "q1",
                "questionBodyText": "計算する。",
                "choiceTextList": ["1", "2"],
                "questionType": "flash_card",
                "isCalculationQuestion": True,
                "explanationText": ["式を示して2と求める。"],
            },
            category_path=Path("output/sample/category/category.json"),
        )

        self.assertIs(row["isCalculationQuestion"], True)
        self.assertTrue(row["autoAudit"]["isCalculationQuestionPresent"])
        self.assertTrue(row["autoAudit"]["explanationLengthMatchesQuestionType"])

    def test_question_intent_and_strict_answer_use_separate_patch_layers(self) -> None:
        source = Path("output/sample/questions_json/2026/00_source/question_2026_1.json")

        intent = module.patch_path_for(source, "questionIntent")
        correct = module.patch_path_for(source, "correctChoice")

        self.assertEqual(intent.parent.name, "15_correctChoiceText_fixed")
        self.assertEqual(correct.parent.name, "23_correctChoiceText_fixed")

    def test_question_id_uses_firestore_ids_before_original_question_id(self) -> None:
        question = {
            "original_question_id": "duplicated-original",
            "firestoreQuestionIds": ["doc-1", "doc-2"],
        }

        self.assertEqual(module.question_id(question), "firestore:doc-1,doc-2")

    def test_stage_entry_keeps_source_original_id_separate_from_review_id(self) -> None:
        question = {
            "original_question_id": "duplicated-original",
            "public_question_id": "public-id",
            "firestoreQuestionIds": ["doc-1"],
        }

        entry = module.stage_entry_base(Path("output/sample.json"), question)

        self.assertEqual(entry["original_question_id"], "firestore:doc-1")
        self.assertEqual(entry["source_original_question_id"], "duplicated-original")
        self.assertEqual(entry["public_question_id"], "public-id")

    def test_patch_views_apply_by_firestore_review_id(self) -> None:
        payload = {
            "question_bodies": [
                {
                    "original_question_id": "duplicated-original",
                    "public_question_id": "public-id",
                    "firestoreQuestionIds": ["doc-1"],
                    "questionType": "true_false",
                }
            ]
        }

        updated = apply_question_type(
            payload,
            {
                "firestore:doc-1": {
                    "questionType": "fill_in_blank",
                },
            },
        )

        self.assertEqual(updated, 1)
        self.assertEqual(payload["question_bodies"][0]["questionType"], "fill_in_blank")

    def test_pending_rows_allow_public_question_id_and_empty_question_intent(self) -> None:
        row = {
            "schemaVersion": module.SCHEMA_VERSION,
            "reviewId": "2025:question_2025_gassyunin_site_1:public-id",
            "qualification": "gas-shunin-kou",
            "sourceFile": "output/gas-shunin-kou/questions_json/2025/00_source/question_2025_gassyunin_site_1.json",
            "originalQuestionId": "",
            "publicQuestionId": "public-id",
            "questionUrl": "https://gassyunin.com/exam/kou/kou_2025/#law-q1",
            "questionBodyText": "既存本文",
            "questionType": "true_false",
            "questionIntent": "",
            "review01QuestionType": "pending",
            "review02QuestionIntent": "pending",
            "review02CorrectChoiceText": "pending",
            "review03ExplanationText": "pending",
            "review04QuestionSetId": "pending",
            "reviewDecision": "pending",
            "questionSetId": "",
        }

        summary, errors = module.validate_rows(
            [row],
            expected_total=1,
            allow_pending=True,
            require_stage_files=False,
            category_ids=set(),
        )

        self.assertEqual(errors, [])
        self.assertEqual(summary["rowCount"], 1)


if __name__ == "__main__":
    unittest.main()
