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
        self.assertNotIn(
            "--fail-on-unresolved",
            by_name["auto assign correctChoiceText (85010)"],
        )
        self.assertIn(
            "--skip-intent-correct-choice-check",
            by_name["convert (85010)"],
        )


if __name__ == "__main__":
    unittest.main()
