from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.common.requirements import (
    get_stage_rules,
    load_requirements,
    validate_records,
)
from scripts.convert.convert_merged_to_firestore import convert_merged_to_firestore
from scripts.merge.patch_views import apply_originalized_fields
from scripts.merge.patch_views import PatchArtifactEntry
from scripts.merge.record_projection import project_merge_record
from scripts.upload.upload_questions_to_firestore import build_doc_data_base


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "original_question_pipeline"


def load_merge_module():
    path = ROOT / "scripts" / "merge" / "00_merge_all.py"
    spec = importlib.util.spec_from_file_location("merge_all_for_original_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Merge moduleをloadできません: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OriginalQuestionPipelineTests(unittest.TestCase):
    def test_fixture_runs_from_source_through_upload_projection(self) -> None:
        merge_module = load_merge_module()
        with tempfile.TemporaryDirectory() as directory:
            base_dir = (
                Path(directory)
                / "output"
                / "synthetic-qualification"
                / "questions_json"
            )
            group_dir = base_dir / "synthetic-source"
            shutil.copytree(FIXTURE_ROOT, group_dir)

            merge_module.merge_all("synthetic-source", base_dir)
            merged_paths = sorted((group_dir / "30_merged_2").glob("*.json"))
            self.assertEqual(len(merged_paths), 1)
            merged = json.loads(merged_paths[0].read_text(encoding="utf-8"))
            merged_question = merged["question_bodies"][0]
            self.assertEqual(merged_question["examSource"], "独自問題")
            self.assertNotIn("examYear", merged_question)
            self.assertNotIn("questionImageStorageUrls", merged_question)
            self.assertNotIn("SOURCE_EXPLANATION_SHOULD_NOT_PUBLISH", json.dumps(merged, ensure_ascii=False))
            self.assertEqual(
                merged_question["sourceUniqueKeys"],
                [
                    "a1b2c3d4e5f60718:choice:1",
                    "a1b2c3d4e5f60718:choice:2",
                    "a1b2c3d4e5f60718:choice:3",
                    "a1b2c3d4e5f60718:choice:4",
                ],
            )

            converted_path = group_dir / "40_convert" / "converted.json"
            converted_path.parent.mkdir()
            converted = convert_merged_to_firestore(
                merged_paths[0],
                converted_path,
            )

        self.assertEqual(converted["total_count"], 4)
        source = json.loads(
            (FIXTURE_ROOT / "00_source" / "question_source_001.json").read_text(
                encoding="utf-8"
            )
        )["question_bodies"][0]
        serialized = json.dumps(converted, ensure_ascii=False, sort_keys=True)
        self.assertNotIn(source["questionBodyText"], serialized)
        self.assertNotIn(source["question_url"], serialized)
        self.assertNotIn(source["source_question_id"], serialized)
        self.assertNotIn("SOURCE_EXPLANATION_SHOULD_NOT_PUBLISH", serialized)
        self.assertNotIn("source.example.invalid", serialized)

        forbidden_fields = {
            "question_url",
            "source_question_id",
            "questionSourceSite",
            "referenceUrls",
            "contentOriginType",
        }
        for question in converted["questions"]:
            self.assertTrue(forbidden_fields.isdisjoint(question))
            self.assertEqual(question["examSource"], "独自問題")
            self.assertTrue(question["isOfficial"])
            self.assertNotIn("examYear", question)
            self.assertEqual(question["questionSetId"], "aws-cost-management")
            self.assertNotIn("ping-t", question["questionId"])
            upload_doc = build_doc_data_base(question)
            self.assertNotIn("examYear", upload_doc)
            self.assertEqual(upload_doc["examSource"], "独自問題")

        firestore_rules = get_stage_rules(
            load_requirements(),
            stage="firestore",
            record_array="questions",
        )
        self.assertEqual(
            validate_records(
                records=converted["questions"],
                rules=firestore_rules,
                source_path=Path("converted.json"),
                id_keys=("questionId",),
            ),
            [],
        )

    def test_complete_source_body_match_is_rejected(self) -> None:
        source = {
            "public_question_id": "public-1",
            "original_question_id": "public-1",
            "questionBodyText": "AWSの費用を通知するサービスはどれか。",
            "choiceTextList": ["A", "B"],
        }
        patch = {
            "questionBodyText": " AWSの費用を通知するサービスはどれか。 ",
            "choiceTextList": ["Aの機能", "Bの機能"],
            "correctChoiceText": ["正しい", "間違い"],
            "questionIntent": "select_correct",
            "answer_result_text": "正解は1です。",
        }

        with self.assertRaisesRegex(ValueError, "問題文全体.*完全一致"):
            apply_originalized_fields(
                {"question_bodies": [source]},
                {"public-1": patch},
            )

    def test_complete_source_choice_set_match_is_rejected_even_if_reordered(self) -> None:
        source = {
            "public_question_id": "public-1",
            "original_question_id": "public-1",
            "questionBodyText": "元の問題文",
            "choiceTextList": ["A", "B"],
        }
        patch = {
            "questionBodyText": "独自の場面と条件に組み直した問題文",
            "choiceTextList": ["B", "A"],
            "correctChoiceText": ["間違い", "正しい"],
            "questionIntent": "select_correct",
            "answer_result_text": "正解は2です。",
        }

        with self.assertRaisesRegex(ValueError, "選択肢一式.*完全一致"):
            apply_originalized_fields(
                {"question_bodies": [source]},
                {"public-1": patch},
            )

    def test_exam_year_is_required_only_outside_independent_questions(self) -> None:
        rules = get_stage_rules(
            load_requirements(),
            stage="firestore",
            record_array="questions",
        )
        base = {
            "questionId": "q1",
            "questionSetId": "set-1",
            "questionText": "問題",
            "questionType": "true_false",
            "qualificationId": "sample",
            "questionTags": [],
            "isOfficial": True,
            "isDeleted": False,
            "isChoiceOnly": False,
            "isGroupable": False,
            "originalQuestionBodyText": "問題",
            "correctChoiceText": "正しい",
        }
        independent_errors = validate_records(
            records=[{**base, "examSource": "独自問題"}],
            rules=rules,
            source_path=Path("independent.json"),
            id_keys=("questionId",),
        )
        official_errors = validate_records(
            records=[{**base, "examSource": "サンプル試験"}],
            rules=rules,
            source_path=Path("official.json"),
            id_keys=("questionId",),
        )

        self.assertFalse(any("examYear" in error for error in independent_errors))
        self.assertTrue(any("examYear" in error for error in official_errors))

    def test_source_explanation_complete_match_is_rejected_only_for_originalized_question(self) -> None:
        source = {
            "public_question_id": "public-1",
            "original_question_id": "public-1",
            "questionBodyText": "取得元の問題文",
            "choiceTextList": ["A", "B"],
            "correctChoiceText": ["正しい", "間違い"],
            "questionIntent": "select_correct",
            "answer_result_text": "正解は1です。",
            "explanation_choice_snippets": [
                "取得元の解説をそのまま公開しない。",
                "別の取得元解説。",
            ],
        }
        originalized = PatchArtifactEntry(
            path=Path("question_originalized.json"),
            entry={
                "original_question_id": "public-1",
                "questionBodyText": "条件と情報順序を組み直した問題文",
                "choiceTextList": ["Aの機能", "Bの機能"],
                "correctChoiceText": ["正しい", "間違い"],
                "questionIntent": "select_correct",
                "answer_result_text": "正解は1です。",
            },
        )
        copied_explanation = PatchArtifactEntry(
            path=Path("question_explanationText_added.json"),
            entry={
                "original_question_id": "public-1",
                "explanationText": [
                    " 取得元の解説をそのまま公開しない。 ",
                    "独自に作成した別の解説。",
                ],
            },
        )

        with self.assertRaisesRegex(ValueError, "解説原文.*完全一致"):
            project_merge_record(
                source,
                originalized=(originalized,),
                explanation=(copied_explanation,),
            )

        official_projection = project_merge_record(
            source,
            explanation=(copied_explanation,),
        )
        self.assertEqual(
            official_projection.merged2["explanationText"][0].strip(),
            source["explanation_choice_snippets"][0],
        )


if __name__ == "__main__":
    unittest.main()
