from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.pipeline import prepare_firestore_upload as module


class PrepareFirestoreUploadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.qualification = "sample-qualification"
        self.base_dir = self.root / self.qualification / "questions_json"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.category_path = self.root / self.qualification / "category" / "category.json"
        self.category_path.parent.mkdir(parents=True, exist_ok=True)
        self.category_path.write_text(json.dumps({"folders": [], "questionSets": []}), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def make_group_dir(self, list_group_id: str) -> Path:
        group_dir = self.base_dir / list_group_id
        (group_dir / "40_convert").mkdir(parents=True, exist_ok=True)
        return group_dir

    def test_bulk_mode_processes_only_numeric_dirs_and_updates_category_once(self) -> None:
        self.make_group_dir("85010")
        self.make_group_dir("85011")
        (self.base_dir / "upload_to_firestore").mkdir(parents=True, exist_ok=True)
        (self.base_dir / "_staged_upload_json").mkdir(parents=True, exist_ok=True)
        (self.base_dir / "old").mkdir(parents=True, exist_ok=True)

        commands: list[tuple[str, list[str], bool]] = []

        def fake_run_step(name: str, command: list[str], dry_run: bool) -> None:
            commands.append((name, command, dry_run))

        stdout = io.StringIO()
        with (
            mock.patch.object(module, "run_step", side_effect=fake_run_step),
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = module.main(
                [
                    self.qualification,
                    "--base-dir",
                    str(self.base_dir),
                    "--category-json",
                    str(self.category_path),
                    "--questionset-only",
                    "--dry-run",
                ]
            )

        self.assertEqual(exit_code, 0)
        merge_targets = [command[2] for name, command, _ in commands if name.startswith("merge")]
        self.assertEqual(merge_targets, ["85010", "85011"])
        count_sources = [command[-1] for name, command, _ in commands if name.startswith("count summary")]
        self.assertEqual(len(count_sources), 2)
        self.assertTrue(all(source.endswith(".json") for source in count_sources))
        category_updates = [command for name, command, _ in commands if name.startswith("update category counts")]
        self.assertEqual(len(category_updates), 1)
        self.assertIn("--latest-upload-only", category_updates[0])
        self.assertIn(str((self.base_dir / "upload_to_firestore").resolve()), category_updates[0])
        self.assertIn("targets   : 85010, 85011", stdout.getvalue())

    def test_bulk_mode_continues_after_failure_and_skips_category_update(self) -> None:
        self.make_group_dir("85010")
        self.make_group_dir("85011")

        commands: list[tuple[str, list[str], bool]] = []

        def fake_run_step(name: str, command: list[str], dry_run: bool) -> None:
            commands.append((name, command, dry_run))
            if name == "merge (85010)":
                raise RuntimeError("boom")

        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(module, "run_step", side_effect=fake_run_step),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = module.main(
                [
                    self.qualification,
                    "--base-dir",
                    str(self.base_dir),
                    "--category-json",
                    str(self.category_path),
                    "--dry-run",
                ]
            )

        self.assertEqual(exit_code, 1)
        self.assertIn("list_group_id=85010", stderr.getvalue())
        self.assertIn("merge (85011)", [name for name, _, _ in commands])
        self.assertFalse(any(name.startswith("update category counts") for name, _, _ in commands))
        self.assertIn("list_group_id の失敗があるためスキップしました。", stdout.getvalue())
        self.assertIn("未処理:", stdout.getvalue())

    def test_single_mode_supports_upload_dry_run_without_prompt(self) -> None:
        self.make_group_dir("85010")
        upload_dir = self.base_dir / "upload_to_firestore"
        upload_dir.mkdir(parents=True, exist_ok=True)
        convert_output = self.base_dir / "85010" / "40_convert" / "85010_firestore_20260408_220000.json"
        upload_output = upload_dir / "85010_firestore_20260408_220000.json"
        convert_output.write_text(json.dumps({"questions": []}), encoding="utf-8")
        upload_output.write_text(json.dumps({"questions": []}), encoding="utf-8")

        commands: list[tuple[str, list[str], bool]] = []

        def fake_run_step(name: str, command: list[str], dry_run: bool) -> None:
            commands.append((name, command, dry_run))

        with mock.patch.object(module, "run_step", side_effect=fake_run_step):
            exit_code = module.main(
                [
                    "85010",
                    "--base-dir",
                    str(self.base_dir),
                    "--category-json",
                    str(self.category_path),
                    "--upload-dry-run",
                ]
            )

        self.assertEqual(exit_code, 0)
        upload_commands = [command for name, command, _ in commands if name.startswith("upload (upload_questions_to_firestore.py)")]
        self.assertEqual(len(upload_commands), 1)
        self.assertEqual(upload_commands[0][-1], "--dry-run")

    def test_single_mode_supports_readable_list_group_id(self) -> None:
        group_id = "keepitup-aws-clf-c02"
        group_dir = self.make_group_dir(group_id)
        (group_dir / "00_source").mkdir()
        commands: list[tuple[str, list[str], bool]] = []

        def fake_run_step(name: str, command: list[str], dry_run: bool) -> None:
            commands.append((name, command, dry_run))

        with mock.patch.object(module, "run_step", side_effect=fake_run_step):
            exit_code = module.main(
                [
                    group_id,
                    "--base-dir",
                    str(self.base_dir),
                    "--category-json",
                    str(self.category_path),
                    "--skip-requirements-check",
                    "--skip-update-category-counts",
                    "--dry-run",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn(f"merge ({group_id})", [name for name, _, _ in commands])

    def test_allow_missing_answer_result_is_forwarded_to_snapshot_pipeline(self) -> None:
        self.make_group_dir("85010")
        commands: list[tuple[str, list[str], bool]] = []

        def fake_run_step(name: str, command: list[str], dry_run: bool) -> None:
            commands.append((name, command, dry_run))

        with mock.patch.object(module, "run_step", side_effect=fake_run_step):
            exit_code = module.main(
                [
                    "85010",
                    "--base-dir",
                    str(self.base_dir),
                    "--category-json",
                    str(self.category_path),
                    "--allow-missing-answer-result",
                    "--skip-requirements-check",
                    "--skip-update-category-counts",
                    "--dry-run",
                ]
            )

        self.assertEqual(exit_code, 0)
        by_name = {name: command for name, command, _ in commands}
        self.assertIn("--allow-missing-answer-result", by_name["merge (85010)"])
        self.assertFalse(
            any(name.startswith("auto assign correctChoiceText") for name in by_name),
            "公開準備はcorrectChoiceTextを自動生成しない",
        )
        self.assertIn(
            "--skip-intent-correct-choice-check",
            by_name["convert (85010)"],
        )

    def test_allow_missing_answer_result_exempts_only_that_requirement(self) -> None:
        allowed, blocked = module.partition_requirement_errors(
            [
                "sample.json: id=q1 empty_required_key=answer_result_text",
                "sample.json: id=q2 missing_required_key=answer_result_text",
                "sample.json: id=q1 empty_required_key=questionSetId",
            ],
            allow_missing_answer_result=True,
        )

        self.assertEqual(len(allowed), 2)
        self.assertTrue(all("answer_result_text" in error for error in allowed))
        self.assertEqual(len(blocked), 1)
        self.assertIn("questionSetId", blocked[0])

    def test_allow_unuploadable_records_excludes_only_invalid_sidecars(self) -> None:
        group_dir = self.make_group_dir("85010")
        merged1 = group_dir / "20_merged_1"
        merged2 = group_dir / "30_merged_2"
        merged1.mkdir()
        merged2.mkdir()
        valid1 = merged1 / "question_a_merged.json"
        invalid1 = merged1 / "question_a_merged_invalid.json"
        valid2 = merged2 / "question_b_merged.json"
        invalid2 = merged2 / "question_b_merged_invalid.json"
        for path in (valid1, invalid1, valid2, invalid2):
            path.write_text("{}", encoding="utf-8")

        self.assertEqual(
            module.merged_requirement_files(
                group_dir,
                allow_unuploadable_records=True,
            ),
            [valid1, valid2],
        )
        self.assertEqual(
            module.merged_requirement_files(
                group_dir,
                allow_unuploadable_records=False,
            ),
            [valid1, invalid1, valid2, invalid2],
        )

    def test_allow_unuploadable_records_keeps_valid_intent_check(self) -> None:
        self.make_group_dir("85010")
        commands: list[tuple[str, list[str], bool]] = []

        def fake_run_step(name: str, command: list[str], dry_run: bool) -> None:
            commands.append((name, command, dry_run))

        with mock.patch.object(module, "run_step", side_effect=fake_run_step):
            exit_code = module.main(
                [
                    "85010",
                    "--base-dir",
                    str(self.base_dir),
                    "--category-json",
                    str(self.category_path),
                    "--allow-unuploadable-records",
                    "--skip-requirements-check",
                    "--skip-update-category-counts",
                    "--dry-run",
                ]
            )

        self.assertEqual(exit_code, 0)
        convert_command = next(
            command
            for name, command, _ in commands
            if name == "convert (85010)"
        )
        self.assertIn("--allow-excluded-invalid-records", convert_command)
        self.assertNotIn("--skip-intent-correct-choice-check", convert_command)

    def test_validated_question_summaries_use_intersection(self) -> None:
        first = self.root / "first_question_summary.json"
        second = self.root / "second_question_summary.json"
        first.write_text(
            json.dumps(
                {
                    "questions": [
                        {
                            "listGroupId": "85010",
                            "reviewQuestionId": "q1",
                            "status": "validated",
                        },
                        {
                            "listGroupId": "85010",
                            "reviewQuestionId": "q2",
                            "status": "validated",
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        second.write_text(
            json.dumps(
                {
                    "questions": [
                        {
                            "listGroupId": "85010",
                            "reviewQuestionId": "q1",
                            "status": "validated",
                        },
                        {
                            "listGroupId": "85010",
                            "reviewQuestionId": "q2",
                            "status": "blocked",
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )

        self.assertEqual(
            module.load_validated_question_keys([first, second]),
            {("85010", "q1")},
        )

    def test_partial_publication_filters_both_outputs_by_validated_question(self) -> None:
        group_dir = self.make_group_dir("85010")
        upload_dir = self.base_dir / "upload_to_firestore"
        upload_dir.mkdir()
        converted_path = group_dir / "40_convert" / "85010_firestore_20260813_120000.json"
        copied_path = upload_dir / "85010_firestore_20260813_120000.json"
        payload = {
            "questions": [
                {"questionId": "q1-choice-1", "originalQuestionId": "q1"},
                {"questionId": "q1-choice-2", "originalQuestionId": "q1"},
                {"questionId": "q2-choice-1", "originalQuestionId": "q2"},
            ]
        }
        for path in (converted_path, copied_path):
            path.write_text(json.dumps(payload), encoding="utf-8")
        summary_path = self.root / "question_summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "questions": [
                        {
                            "listGroupId": "85010",
                            "reviewQuestionId": "q1",
                            "status": "validated",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        excluded_questions, excluded_documents, details = (
            module.filter_outputs_to_validated_questions(
                list_group_id="85010",
                converted_path=converted_path,
                copied_path=copied_path,
                validated_question_keys={("85010", "q1")},
                summary_paths=[summary_path],
            )
        )

        self.assertEqual(excluded_questions, 1)
        self.assertEqual(excluded_documents, 1)
        self.assertTrue(any("1問" in detail for detail in details))
        for path in (converted_path, copied_path):
            questions = json.loads(path.read_text(encoding="utf-8"))["questions"]
            self.assertEqual(
                [question["questionId"] for question in questions],
                ["q1-choice-1", "q1-choice-2"],
            )
        reports = list((group_dir / "40_convert" / "publication_exclusions").glob("*.json"))
        self.assertEqual(len(reports), 1)
        report = json.loads(reports[0].read_text(encoding="utf-8"))
        self.assertEqual(report["excludedOriginalQuestionCount"], 1)
        self.assertEqual(report["excludedQuestions"][0]["originalQuestionId"], "q2")

    def test_partial_publication_fails_if_validated_question_was_not_converted(self) -> None:
        group_dir = self.make_group_dir("85010")
        upload_dir = self.base_dir / "upload_to_firestore"
        upload_dir.mkdir()
        converted_path = group_dir / "40_convert" / "85010_firestore_20260813_120000.json"
        copied_path = upload_dir / "85010_firestore_20260813_120000.json"
        payload = {
            "questions": [
                {"questionId": "q1-choice-1", "originalQuestionId": "q1"}
            ]
        }
        for path in (converted_path, copied_path):
            path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "公開成果物に見つかりません"):
            module.filter_outputs_to_validated_questions(
                list_group_id="85010",
                converted_path=converted_path,
                copied_path=copied_path,
                validated_question_keys={("85010", "missing")},
                summary_paths=[self.root / "question_summary.json"],
            )


if __name__ == "__main__":
    unittest.main()
