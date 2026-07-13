from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.check.check_00_source_immutability import (
    differences,
    load_manifest,
    main,
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

    def test_difference_names_are_simple(self) -> None:
        self.assertEqual(
            differences({"a": "1", "b": "2"}, {"a": "9", "c": "3"}),
            {"改変": ["a"], "消失": ["b"], "未登録": ["c"]},
        )


if __name__ == "__main__":
    unittest.main()
