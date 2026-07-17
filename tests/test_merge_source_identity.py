from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MERGE_MODULE = importlib.import_module("scripts.merge.00_merge_all")
merge_all = MERGE_MODULE.merge_all


class MergeSourceIdentityTests(unittest.TestCase):
    def _source_record(self, key: str) -> dict:
        return {
            "sourceQuestionKey": key,
            "original_question_id": "shared-review-id",
            "questionBodyText": "確認対象",
            "choiceTextList": ["選択肢"],
            "questionType": "flash_card",
            "questionIntent": "select_correct",
            "answer_result_text": "正解は1です。",
            "correctChoiceText": "source",
            "examYear": 2026,
        }

    def _write_source(
        self,
        source_dir: Path,
        filename: str,
        key: str,
    ) -> None:
        (source_dir / filename).write_text(
            json.dumps(
                {"question_bodies": [self._source_record(key)]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _binding(self, filename: str, key: str) -> dict[str, str]:
        return {
            "sourceQuestionKey": key,
            "reviewQuestionId": "shared-review-id",
            "sourceRecordRef": f"{filename}#0",
        }

    def _write_patch(
        self,
        patch_dir: Path,
        filename: str,
        entries: list[dict],
    ) -> None:
        patch_dir.mkdir(parents=True, exist_ok=True)
        (patch_dir / filename).write_text(
            json.dumps(entries, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_exact_binding_updates_only_the_target_with_a_shared_review_id(self):
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            group_dir = base_dir / "2026"
            source_dir = group_dir / "00_source"
            source_dir.mkdir(parents=True)
            self._write_source(source_dir, "question_1.json", "key-1")
            self._write_source(source_dir, "question_2.json", "key-2")
            self._write_patch(
                group_dir / "23_correctChoiceText_fixed",
                "aggregate_correctChoiceText_fixed.json",
                [
                    {
                        **self._binding("question_2.json", "key-2"),
                        "original_question_id": "shared-review-id",
                        "correctChoiceText": "target-only",
                    }
                ],
            )

            merge_all("2026", base_dir, require_answer_result_text=False)

            first = json.loads(
                (group_dir / "20_merged_1" / "question_1_merged.json").read_text()
            )
            second = json.loads(
                (group_dir / "20_merged_1" / "question_2_merged.json").read_text()
            )

        self.assertEqual(first["question_bodies"][0]["correctChoiceText"], "source")
        self.assertEqual(
            second["question_bodies"][0]["correctChoiceText"],
            "target-only",
        )

    def test_ambiguous_legacy_patch_fails_before_existing_outputs_are_archived(self):
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            group_dir = base_dir / "2026"
            source_dir = group_dir / "00_source"
            source_dir.mkdir(parents=True)
            self._write_source(source_dir, "question_1.json", "key-1")
            self._write_source(source_dir, "question_2.json", "key-2")
            self._write_patch(
                group_dir / "23_correctChoiceText_fixed",
                "aggregate_correctChoiceText_fixed.json",
                [
                    {
                        "original_question_id": "shared-review-id",
                        "correctChoiceText": "ambiguous",
                    }
                ],
            )
            merged_dir = group_dir / "20_merged_1"
            merged_dir.mkdir()
            sentinel = merged_dir / "sentinel.json"
            sentinel.write_text("unchanged", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "一意に対応できません"):
                merge_all("2026", base_dir, require_answer_result_text=False)

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")
            self.assertFalse((merged_dir / "old" / "sentinel.json").exists())

    def test_aggregate_layer_overrides_per_source_layer_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            group_dir = base_dir / "2026"
            source_dir = group_dir / "00_source"
            source_dir.mkdir(parents=True)
            self._write_source(source_dir, "question_1.json", "key-1")
            binding = self._binding("question_1.json", "key-1")
            patch_dir = group_dir / "23_correctChoiceText_fixed"
            self._write_patch(
                patch_dir,
                "question_1_correctChoiceText_fixed.json",
                [
                    {
                        **binding,
                        "original_question_id": "shared-review-id",
                        "correctChoiceText": "per-source",
                    }
                ],
            )
            self._write_patch(
                patch_dir,
                "aggregate_correctChoiceText_fixed.json",
                [
                    {
                        **binding,
                        "original_question_id": "shared-review-id",
                        "correctChoiceText": "aggregate",
                    }
                ],
            )

            merge_all("2026", base_dir, require_answer_result_text=False)

            merged = json.loads(
                (group_dir / "20_merged_1" / "question_1_merged.json").read_text()
            )

        self.assertEqual(
            merged["question_bodies"][0]["correctChoiceText"],
            "aggregate",
        )

    def test_identical_duplicate_in_one_artifact_is_applied_once(self):
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            group_dir = base_dir / "2026"
            source_dir = group_dir / "00_source"
            source_dir.mkdir(parents=True)
            self._write_source(source_dir, "question_1.json", "key-1")
            entry = {
                **self._binding("question_1.json", "key-1"),
                "original_question_id": "shared-review-id",
                "correctChoiceText": "deduplicated",
            }
            self._write_patch(
                group_dir / "23_correctChoiceText_fixed",
                "question_1_correctChoiceText_fixed.json",
                [entry, dict(entry)],
            )

            merge_all("2026", base_dir, require_answer_result_text=False)

            merged = json.loads(
                (group_dir / "20_merged_1" / "question_1_merged.json").read_text()
            )

        self.assertEqual(
            merged["question_bodies"][0]["correctChoiceText"],
            "deduplicated",
        )

    def test_competing_duplicate_in_one_artifact_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            group_dir = base_dir / "2026"
            source_dir = group_dir / "00_source"
            source_dir.mkdir(parents=True)
            self._write_source(source_dir, "question_1.json", "key-1")
            binding = self._binding("question_1.json", "key-1")
            self._write_patch(
                group_dir / "23_correctChoiceText_fixed",
                "question_1_correctChoiceText_fixed.json",
                [
                    {
                        **binding,
                        "original_question_id": "shared-review-id",
                        "correctChoiceText": "first",
                    },
                    {
                        **binding,
                        "original_question_id": "shared-review-id",
                        "correctChoiceText": "second",
                    },
                ],
            )

            with self.assertRaisesRegex(RuntimeError, "同一artifact内"):
                merge_all("2026", base_dir, require_answer_result_text=False)

    def test_all_publication_patch_layers_keep_their_field_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            group_dir = base_dir / "2026"
            source_dir = group_dir / "00_source"
            source_dir.mkdir(parents=True)
            self._write_source(source_dir, "question_1.json", "key-1")
            binding = {
                **self._binding("question_1.json", "key-1"),
                "original_question_id": "shared-review-id",
            }
            for patch_dir, filename, fields in (
                (
                    "10_questionType_fixed",
                    "question_1_questionType_fixed.json",
                    {
                        "questionType": "fill_in_blank",
                        "questionBodyText": "patched body",
                        "choiceTextList": ["patched choice"],
                    },
                ),
                (
                    "15_correctChoiceText_fixed",
                    "question_1_correctChoiceText_fixed.json",
                    {
                        "questionIntent": "select_incorrect",
                        "answer_result_text": "fallback result",
                        "correctChoiceText": ["fallback"],
                    },
                ),
                (
                    "18_law_context_prepared",
                    "question_1_merged_lawContext_prepared.json",
                    {
                        "isLawRelated": True,
                        "lawContextForExplanation": "law context",
                    },
                ),
                (
                    "21_explanationText_added",
                    "question_1_merged_explanationText_added.json",
                    {
                        "explanationText": ["explanation"],
                        "suggestedQuestions": [],
                    },
                ),
                (
                    "22_questionSetId_linked",
                    "question_1_questionSetId_linked.json",
                    {
                        "questionSetId": "set-1",
                        "questionSetIds": ["set-1"],
                    },
                ),
                (
                    "23_correctChoiceText_fixed",
                    "question_1_correctChoiceText_fixed.json",
                    {
                        "answer_result_text": "strict result",
                        "correctChoiceText": ["strict"],
                    },
                ),
            ):
                self._write_patch(
                    group_dir / patch_dir,
                    filename,
                    [{**binding, **fields}],
                )

            merge_all("2026", base_dir, require_answer_result_text=False)

            merged1 = json.loads(
                (group_dir / "20_merged_1" / "question_1_merged.json").read_text()
            )["question_bodies"][0]
            merged2_path = next((group_dir / "30_merged_2").glob("*.json"))
            merged2 = json.loads(merged2_path.read_text())["question_bodies"][0]

        self.assertEqual(merged1["questionType"], "fill_in_blank")
        self.assertEqual(merged1["questionBodyText"], "patched body")
        self.assertEqual(merged1["choiceTextList"], ["patched choice"])
        self.assertEqual(merged1["questionIntent"], "select_incorrect")
        self.assertEqual(merged1["answer_result_text"], "strict result")
        self.assertEqual(merged1["correctChoiceText"], ["strict"])
        self.assertTrue(merged1["isLawRelated"])
        self.assertEqual(merged1["lawContextForExplanation"], "law context")
        self.assertEqual(merged2["explanationText"], ["explanation"])
        self.assertEqual(merged2["suggestedQuestions"], [])
        self.assertEqual(merged2["questionSetId"], "set-1")
        self.assertEqual(merged2["questionSetIds"], ["set-1"])
        self.assertEqual(merged2["answer_result_text"], "strict result")
        self.assertEqual(merged2["correctChoiceText"], ["strict"])

    def test_question_issue_hash_failure_leaves_all_existing_outputs_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            group_dir = base_dir / "2026"
            source_dir = group_dir / "00_source"
            source_dir.mkdir(parents=True)
            self._write_source(source_dir, "question_1.json", "key-1")
            self._write_patch(
                group_dir / "24_questionIssueCorrections",
                "correction.json",
                {
                    "schemaVersion": "question-issue-correction/v1",
                    "origin": "user_problem_report",
                    "entries": [
                        {
                            **self._binding("question_1.json", "key-1"),
                            "original_question_id": "shared-review-id",
                            "expectedBeforeHash": "0" * 64,
                            "changes": {"questionBodyText": "must not commit"},
                        }
                    ],
                },
            )
            sentinels: list[Path] = []
            for output_dir in (
                "12_merged_questionType",
                "20_merged_1",
                "30_merged_2",
            ):
                path = group_dir / output_dir / "sentinel.json"
                path.parent.mkdir()
                path.write_text(f'{{"value":"{output_dir}"}}', encoding="utf-8")
                sentinels.append(path)

            with self.assertRaisesRegex(RuntimeError, "input hash mismatch"):
                merge_all("2026", base_dir, require_answer_result_text=False)

            for path in sentinels:
                self.assertTrue(path.exists())
                self.assertIn(path.parent.name, path.read_text(encoding="utf-8"))
                self.assertFalse((path.parent / "old" / path.name).exists())

    def test_output_write_failure_rolls_back_all_three_artifact_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            group_dir = base_dir / "2026"
            source_dir = group_dir / "00_source"
            source_dir.mkdir(parents=True)
            self._write_source(source_dir, "question_1.json", "key-1")
            sentinels: list[Path] = []
            for output_dir in (
                "12_merged_questionType",
                "20_merged_1",
                "30_merged_2",
            ):
                path = group_dir / output_dir / "sentinel.json"
                path.parent.mkdir()
                path.write_text(f'{{"value":"{output_dir}"}}', encoding="utf-8")
                sentinels.append(path)

            real_save = MERGE_MODULE.save_json
            call_count = 0

            def fail_on_second_write(data: dict, path: Path) -> None:
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    raise OSError("simulated write failure")
                real_save(data, path)

            with mock.patch.object(
                MERGE_MODULE,
                "save_json",
                side_effect=fail_on_second_write,
            ):
                with self.assertRaisesRegex(OSError, "simulated write failure"):
                    merge_all("2026", base_dir, require_answer_result_text=False)

            for path in sentinels:
                self.assertTrue(path.exists())
                self.assertIn(path.parent.name, path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
