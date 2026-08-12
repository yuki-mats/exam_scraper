from __future__ import annotations

import tempfile
import unittest
import subprocess
import json
import hashlib
from pathlib import Path

from scripts.check.check_00_source_immutability import (
    differences,
    load_manifest,
    main,
    record_scrape_refresh,
    source_hashes,
    staged_source_change_violations,
)


class SourceImmutabilityTest(unittest.TestCase):
    def test_staged_parent_move_with_same_content_and_filename_is_allowed(self) -> None:
        changes = [
            (
                "R100",
                "output/old/questions_json/84001/00_source/question_1.json",
                "output/readable/questions_json/202501/00_source/question_1.json",
            )
        ]

        self.assertEqual(staged_source_change_violations(changes), [])

    def test_staged_source_filename_change_is_rejected(self) -> None:
        changes = [
            (
                "R100",
                "output/old/questions_json/84001/00_source/question_1.json",
                "output/readable/questions_json/202501/00_source/renamed.json",
            )
        ]

        self.assertEqual(
            staged_source_change_violations(changes),
            [
                "R100\toutput/old/questions_json/84001/00_source/question_1.json"
                "\toutput/readable/questions_json/202501/00_source/renamed.json"
            ],
        )

    def test_staged_source_content_change_is_rejected(self) -> None:
        changes = [("M", "output/sample/00_source/question_1.json")]

        self.assertEqual(
            staged_source_change_violations(changes),
            ["M\toutput/sample/00_source/question_1.json"],
        )

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.manifest = self.root / "manifest.jsonl"
        self.source = self.root / "output/sample/00_source/question_1.json"
        self.source.parent.mkdir(parents=True)
        self.source.write_text('{"value":"original"}\n', encoding="utf-8")
        self.assertEqual(main(["--root", str(self.root), "--manifest", str(self.manifest), "--initialize"]), 0)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def initialize_git_with_partial_residency(self) -> None:
        missing_path = "output/not-resident/00_source/question_2.json"
        with self.manifest.open("a", encoding="utf-8") as manifest:
            manifest.write(
                json.dumps(
                    {
                        "path": missing_path,
                        "sha256": hashlib.sha256(b"not resident\n").hexdigest(),
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True
        )
        subprocess.run(["git", "add", "output"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=self.root, check=True)

    def check_staged(self) -> int:
        return main(
            [
                "--root",
                str(self.root),
                "--manifest",
                str(self.manifest),
                "--check-staged",
            ]
        )

    def test_check_staged_ignores_nonresident_manifest_paths(self) -> None:
        self.initialize_git_with_partial_residency()

        self.assertEqual(self.check_staged(), 0)

    def test_check_staged_rejects_partial_residency_source_modification(self) -> None:
        self.initialize_git_with_partial_residency()
        self.source.write_text('{"value":"changed"}\n', encoding="utf-8")
        subprocess.run(["git", "add", str(self.source)], cwd=self.root, check=True)

        self.assertEqual(self.check_staged(), 1)

    def test_check_staged_rejects_partial_residency_source_deletion(self) -> None:
        self.initialize_git_with_partial_residency()
        self.source.unlink()
        subprocess.run(["git", "add", "-u", "output"], cwd=self.root, check=True)

        self.assertEqual(self.check_staged(), 1)

    def test_check_staged_rejects_partial_residency_filename_change(self) -> None:
        self.initialize_git_with_partial_residency()
        renamed = self.source.with_name("renamed.json")
        self.source.rename(renamed)
        subprocess.run(["git", "add", "-A", "output"], cwd=self.root, check=True)

        self.assertEqual(self.check_staged(), 1)

    def test_check_staged_allows_partial_residency_parent_move(self) -> None:
        self.initialize_git_with_partial_residency()
        moved = self.root / "output/moved/00_source/question_1.json"
        moved.parent.mkdir(parents=True)
        self.source.rename(moved)
        subprocess.run(["git", "add", "-A", "output"], cwd=self.root, check=True)

        self.assertEqual(self.check_staged(), 0)

    def test_unchanged_passes(self) -> None:
        self.assertEqual(main(["--root", str(self.root), "--manifest", str(self.manifest)]), 0)

    def test_change_is_rejected(self) -> None:
        self.source.write_text('{"value":"changed"}\n', encoding="utf-8")
        self.assertEqual(main(["--root", str(self.root), "--manifest", str(self.manifest)]), 1)

    def test_delete_is_rejected(self) -> None:
        self.source.unlink()
        self.assertEqual(main(["--root", str(self.root), "--manifest", str(self.manifest)]), 1)

    def test_rename_is_rejected(self) -> None:
        self.source.rename(self.source.with_name("renamed.json"))
        self.assertEqual(main(["--root", str(self.root), "--manifest", str(self.manifest)]), 1)

    def test_parent_directory_move_can_be_recorded_without_changing_content(self) -> None:
        moved = self.root / "output/readable/questions_json/202501/00_source/question_1.json"
        moved.parent.mkdir(parents=True)
        self.source.rename(moved)

        self.assertEqual(
            main(
                [
                    "--root",
                    str(self.root),
                    "--manifest",
                    str(self.manifest),
                    "--record-moves",
                ]
            ),
            0,
        )
        self.assertEqual(
            load_manifest(self.manifest),
            {
                str(moved.relative_to(self.root)):
                    "62182d25250ad0c481e9ea8ab30b4a6347e3e443eaa9f89b7574704b30713400"
            },
        )

    def test_record_moves_rejects_source_filename_change(self) -> None:
        moved = self.root / "output/sample/other/00_source/renamed.json"
        moved.parent.mkdir(parents=True)
        self.source.rename(moved)

        self.assertEqual(
            main(
                [
                    "--root",
                    str(self.root),
                    "--manifest",
                    str(self.manifest),
                    "--record-moves",
                ]
            ),
            1,
        )

    def test_new_source_requires_record_new(self) -> None:
        self.source.with_name("question_2.json").write_text('{"value":"new"}\n', encoding="utf-8")
        self.assertEqual(main(["--root", str(self.root), "--manifest", str(self.manifest)]), 1)
        self.assertEqual(main(["--root", str(self.root), "--manifest", str(self.manifest), "--record-new"]), 0)
        self.assertEqual(len(load_manifest(self.manifest)), 2)

    def test_record_new_refuses_existing_change(self) -> None:
        self.source.write_text('{"value":"changed"}\n', encoding="utf-8")
        self.source.with_name("question_2.json").write_text('{"value":"new"}\n', encoding="utf-8")
        self.assertEqual(main(["--root", str(self.root), "--manifest", str(self.manifest), "--record-new"]), 1)
        self.assertEqual(len(load_manifest(self.manifest)), 1)

    def test_scrape_refresh_records_existing_change_and_new_file_in_scope(self) -> None:
        self.source.write_text('{"value":"changed"}\n', encoding="utf-8")
        added = self.source.with_name("question_2.json")
        added.write_text('{"value":"new"}\n', encoding="utf-8")

        self.assertEqual(
            main(
                [
                    "--root",
                    str(self.root),
                    "--manifest",
                    str(self.manifest),
                    "--record-scrape-refresh",
                    "--scope",
                    "output/sample/00_source",
                ]
            ),
            0,
        )
        self.assertEqual(load_manifest(self.manifest), source_hashes(self.root))

    def test_scrape_refresh_rejects_difference_outside_scope(self) -> None:
        other = self.root / "output/other/00_source/question_1.json"
        other.parent.mkdir(parents=True)
        other.write_text('{"value":"other"}\n', encoding="utf-8")

        self.assertEqual(
            main(
                [
                    "--root",
                    str(self.root),
                    "--manifest",
                    str(self.manifest),
                    "--record-scrape-refresh",
                    "--scope",
                    "output/sample/00_source",
                ]
            ),
            1,
        )
        self.assertEqual(len(load_manifest(self.manifest)), 1)

    def test_scrape_refresh_rejects_deleted_source(self) -> None:
        self.source.unlink()

        with self.assertRaisesRegex(ValueError, "消失"):
            record_scrape_refresh(
                {"output/sample/00_source/question_1.json": "before"},
                {},
                {"改変": [], "消失": ["output/sample/00_source/question_1.json"], "未登録": []},
                scope="output/sample/00_source",
            )

    def test_difference_names_are_simple(self) -> None:
        self.assertEqual(
            differences({"a": "1", "b": "2"}, {"a": "9", "c": "3"}),
            {"改変": ["a"], "消失": ["b"], "未登録": ["c"]},
        )


if __name__ == "__main__":
    unittest.main()
