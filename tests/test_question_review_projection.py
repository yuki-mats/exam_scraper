import json
import tempfile
import unittest
from pathlib import Path

from scripts.common.question_identity import SourceIdentityBinding
from scripts.merge.patch_views import build_layered_patch_candidate_index
from scripts.merge.record_projection import project_merge_record
from scripts.merge.question_issue_corrections import (
    QuestionIssueCorrectionEntry,
    question_record_hash,
)
from tools.question_review_console.projection import (
    PROJECTED_COMPARE_FIELDS,
    PatchEntry,
    SourceRecordIdentity,
    build_identity_candidate_index,
    build_question_issue_index,
    explanation_prefix_matches,
    project_record,
)


def source_identity(
    source_ref: str,
    source_stem: str,
    *aliases: str,
) -> SourceRecordIdentity:
    binding = SourceIdentityBinding.from_values(
        "sample:2026:shared",
        "shared",
        source_ref,
    )
    return SourceRecordIdentity(
        binding=binding,
        aliases=frozenset({*binding.as_tuple(), *aliases}),
        source_stem=source_stem,
    )


def candidate_index(
    candidates: list[PatchEntry],
    sources: list[SourceRecordIdentity],
):
    return build_identity_candidate_index(
        candidates,
        sources=sources,
        record_of=lambda candidate: candidate.entry,
        source_stem_of=lambda candidate: candidate.source_stem,
        label="test patch",
    )


def layered_candidate_index(
    candidates: list[PatchEntry],
    sources: list[SourceRecordIdentity],
):
    return build_layered_patch_candidate_index(
        candidates,
        sources=sources,
        record_of=lambda candidate: candidate.entry,
        source_stem_of=lambda candidate: candidate.source_stem,
        path_of=lambda candidate: candidate.path,
        label="test patch",
    )


class QuestionReviewProjectionTests(unittest.TestCase):
    def test_projection_hash_covers_normalized_and_issue_image_fields(self):
        self.assertTrue(
            {
                "examYear",
                "manualQuestionIntentOverride",
                "questionImageStorageUrls",
                "originalQuestionChoiceImageUrls",
                "explanationImageUrls",
            }.issubset(PROJECTED_COMPARE_FIELDS)
        )

    def test_record_projection_matches_physical_merge_order_and_normalization(self):
        source = {
            "original_question_id": "q1",
            "questionType": "true_false",
            "questionBodyText": "正しいものを選べ。",
            "choiceTextList": ["A", "B"],
            "correctChoiceText": [None, None],
            "examLabel": "令和5年度",
        }
        intent = PatchEntry(
            Path("15.json"),
            {
                "original_question_id": "q1",
                "questionIntent": "select_correct",
                "correctChoiceText": ["間違い", "正しい"],
                "answer_result_text": "正解は 2 です。",
            },
        )
        strict = PatchEntry(
            Path("23.json"),
            {
                "original_question_id": "q1",
                "questionIntent": "select_incorrect",
                "answer_result_text": "正解は 1 です。",
            },
        )
        before_issue = project_merge_record(
            source,
            intent_fallback=(intent,),
            strict_correct=(strict,),
        ).merged2
        issue = QuestionIssueCorrectionEntry(
            Path("24.json"),
            {
                "original_question_id": "q1",
                "expectedBeforeHash": question_record_hash(before_issue),
                "changes": {"questionBodyText": "最終修正版"},
            },
        )

        projected = project_merge_record(
            source,
            intent_fallback=(intent,),
            strict_correct=(strict,),
            question_issues=(issue,),
        )

        self.assertEqual(
            {
                field: projected.merged2[field]
                for field in (
                    "questionIntent",
                    "examYear",
                    "correctChoiceText",
                    "answer_result_text",
                    "questionBodyText",
                )
            },
            {
                "questionIntent": "select_correct",
                "examYear": 2023,
                "correctChoiceText": ["正しい", "間違い"],
                "answer_result_text": "正解は 1 です。",
                "questionBodyText": "最終修正版",
            },
        )
        self.assertEqual(projected.errors, ())

    def test_record_projection_normalizes_without_patch_files(self):
        projected = project_merge_record(
            {
                "original_question_id": "q1",
                "questionType": None,
                "choiceTextList": ["", None],
                "examLabel": "平成25年度",
            }
        )

        self.assertEqual(projected.merged2["questionType"], "group_choice")
        self.assertEqual(projected.merged2["examYear"], 2013)

    def test_exact_binding_wins_even_when_source_aliases_are_shared(self):
        first = source_identity("question_1.json#0", "question_1", "shared")
        second = source_identity("question_2.json#0", "question_2", "shared")
        exact = PatchEntry(
            Path("aggregate.json"),
            {
                **second.binding.as_mapping(),
                "original_question_id": "shared",
                "questionType": "flash_card",
            },
            source_stem="aggregate",
        )

        index = candidate_index([exact], [first, second])

        self.assertNotIn(first.binding, index.by_binding)
        self.assertEqual(index.by_binding[second.binding], (exact,))
        self.assertEqual(index.errors_by_binding, {})

    def test_legacy_shared_alias_is_reported_as_ambiguous(self):
        first = source_identity("question_1.json#0", "question_1", "shared")
        second = source_identity("question_2.json#0", "question_2", "shared")
        legacy = PatchEntry(
            Path("aggregate.json"),
            {"original_question_id": "shared", "questionType": "flash_card"},
            source_stem="aggregate",
        )

        index = candidate_index([legacy], [first, second])

        self.assertEqual(index.by_binding, {})
        self.assertIn(first.binding, index.errors_by_binding)
        self.assertIn(second.binding, index.errors_by_binding)

    def test_legacy_alias_is_scoped_by_source_filename_before_group(self):
        first = source_identity("question_1.json#0", "question_1", "shared")
        second = source_identity("question_2.json#0", "question_2", "shared")
        first_patch = PatchEntry(
            Path("question_1_patch.json"),
            {"original_question_id": "shared", "value": 1},
            source_stem="question_1",
        )
        second_patch = PatchEntry(
            Path("question_2_patch.json"),
            {"original_question_id": "shared", "value": 2},
            source_stem="question_2",
        )

        index = candidate_index(
            [first_patch, second_patch],
            [first, second],
        )

        self.assertEqual(index.by_binding[first.binding], (first_patch,))
        self.assertEqual(index.by_binding[second.binding], (second_patch,))
        self.assertEqual(index.errors_by_binding, {})

    def test_exact_and_legacy_layers_keep_original_candidate_order(self):
        source = source_identity("question_1.json#0", "question_1", "shared")
        legacy = PatchEntry(
            Path("01_legacy.json"),
            {"original_question_id": "shared", "value": 1},
            source_stem="question_1",
        )
        exact = PatchEntry(
            Path("02_exact.json"),
            {**source.binding.as_mapping(), "value": 2},
            source_stem="aggregate",
        )

        index = candidate_index([legacy, exact], [source])

        self.assertEqual(index.by_binding[source.binding], (legacy, exact))

    def test_projection_applies_per_source_then_partial_aggregate_overlay(self):
        source = source_identity("question_1.json#0", "question_1", "shared")
        per_source = PatchEntry(
            Path("question_1_questionType_fixed.json"),
            {
                **source.binding.as_mapping(),
                "questionType": "flash_card",
                "questionBodyText": "per-source body",
            },
            source_stem="question_1",
        )
        aggregate = PatchEntry(
            Path("aggregate_questionType_fixed.json"),
            {
                **source.binding.as_mapping(),
                "questionType": "true_false",
            },
            source_stem="aggregate",
        )
        index = layered_candidate_index([aggregate, per_source], [source])

        result = project_record(
            {"original_question_id": "shared", "questionBodyText": "source"},
            {"shared"},
            {"questionType": index},
            [],
            source_binding=source.binding,
        )

        self.assertEqual(result.record["questionType"], "true_false")
        self.assertEqual(result.record["questionBodyText"], "per-source body")
        self.assertEqual(
            result.applied_files,
            (str(per_source.path), str(aggregate.path)),
        )
        self.assertEqual(result.errors, ())

    def test_projection_reports_competing_records_in_one_artifact(self):
        source = source_identity("question_1.json#0", "question_1", "shared")
        first = PatchEntry(
            Path("question_1_questionType_fixed.json"),
            {**source.binding.as_mapping(), "questionType": "flash_card"},
            source_stem="question_1",
        )
        second = PatchEntry(
            first.path,
            {**source.binding.as_mapping(), "questionType": "true_false"},
            source_stem="question_1",
        )
        index = layered_candidate_index([first, second], [source])

        result = project_record(
            {"original_question_id": "shared", "questionType": "source"},
            {"shared"},
            {"questionType": index},
            [],
            source_binding=source.binding,
        )

        self.assertEqual(result.record["questionType"], "source")
        self.assertIn("同一artifact内", " ".join(result.errors))
        self.assertEqual(result.applied_files, ())

    def test_question_issue_correction_uses_exact_source_binding(self):
        first = source_identity("question_1.json#0", "question_1", "shared")
        second = source_identity("question_2.json#0", "question_2", "shared")
        base = {
            "original_question_id": "shared",
            "questionBodyText": "修正前",
        }
        patch = {
            "schemaVersion": "question-issue-correction/v1",
            "origin": "user_problem_report",
            "entries": [
                {
                    **second.binding.as_mapping(),
                    "original_question_id": "shared",
                    "expectedBeforeHash": question_record_hash(base),
                    "changes": {"questionBodyText": "修正後"},
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "correction.json"
            path.write_text(json.dumps(patch), encoding="utf-8")
            index = build_question_issue_index([path], [first, second])
            first_result = project_record(
                base,
                {"shared"},
                {},
                index,
                source_binding=first.binding,
            )
            second_result = project_record(
                base,
                {"shared"},
                {},
                index,
                source_binding=second.binding,
            )

        self.assertEqual(first_result.record["questionBodyText"], "修正前")
        self.assertEqual(second_result.record["questionBodyText"], "修正後")
        self.assertEqual(first_result.errors, ())
        self.assertEqual(second_result.errors, ())

    def test_strict_correct_choice_patch_overrides_intent_before_explanation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent = PatchEntry(
                root / "15.json",
                {"original_question_id": "q1", "correctChoiceText": ["正しい", "間違い"]},
            )
            strict = PatchEntry(
                root / "23.json",
                {"original_question_id": "q1", "correctChoiceText": ["間違い", "正しい"]},
            )
            explanation = PatchEntry(
                root / "21.json",
                {
                    "original_question_id": "q1",
                    "explanationText": ["間違い。根拠1", "正しい。根拠2"],
                },
            )
            result = project_record(
                {
                    "original_question_id": "q1",
                    "questionBodyText": "問題",
                    "choiceTextList": ["A", "B"],
                },
                {"q1"},
                {
                    "questionIntent": {"q1": intent},
                    "explanation": {"q1": explanation},
                    "correctChoice": {"q1": strict},
                },
                [],
            )

        self.assertEqual(result.record["correctChoiceText"], ["間違い", "正しい"])
        self.assertEqual(result.record["explanationText"][1], "正しい。根拠2")
        self.assertEqual(result.applied_files, (str(intent.path), str(strict.path), str(explanation.path)))

    def test_question_issue_layer_rolls_back_record_when_later_hash_fails(self):
        base = {
            "original_question_id": "q1",
            "questionType": "multiple_choice",
            "questionBodyText": "修正前",
            "choiceTextList": ["A"],
        }
        first = QuestionIssueCorrectionEntry(
            Path("first.json"),
            {
                "original_question_id": "q1",
                "expectedBeforeHash": question_record_hash(base),
                "changes": {"questionBodyText": "途中の修正"},
            },
        )
        second = QuestionIssueCorrectionEntry(
            Path("second.json"),
            {
                "original_question_id": "q1",
                "expectedBeforeHash": "invalid-hash",
                "changes": {"choiceTextList": ["B"]},
            },
        )

        result = project_merge_record(base, question_issues=(first, second))

        self.assertEqual(result.merged2, base)
        self.assertEqual(result.update_counts["question_issue"], 0)
        self.assertTrue(result.errors)
        self.assertEqual(result.applied_paths, ())

    def test_explanation_prefix_matches_normalized_verdict(self):
        self.assertTrue(explanation_prefix_matches("○", "正しい。条文の通り。"))
        self.assertTrue(explanation_prefix_matches("誤り", "間違い。文言が異なる。"))
        self.assertFalse(
            explanation_prefix_matches("正しい", "選択肢1は「正しい」です。根拠を説明する。")
        )
        self.assertFalse(
            explanation_prefix_matches("間違い", "この記述は誤りです。根拠を説明する。")
        )
        self.assertFalse(explanation_prefix_matches("正しい", "間違い。文言が異なる。"))


if __name__ == "__main__":
    unittest.main()
