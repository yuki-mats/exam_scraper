import tempfile
import unittest
from pathlib import Path

from tools.question_review_console.write_transaction import (
    WriteTransactionError,
    capture_write_snapshot,
    restore_write_snapshot,
)


class WriteTransactionTests(unittest.TestCase):
    def test_restore_recovers_modified_deleted_and_created_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scope = root / "output/sample/questions_json/2026"
            scope.mkdir(parents=True)
            modified = scope / "modified.json"
            deleted = scope / "deleted.json"
            modified.write_text("before modified\n", encoding="utf-8")
            deleted.write_text("before deleted\n", encoding="utf-8")
            backup = root / "run" / "baseline_files"
            backup.parent.mkdir(parents=True)
            snapshot = capture_write_snapshot(root, [scope], backup)

            modified.write_text("after modified\n", encoding="utf-8")
            deleted.unlink()
            created = scope / "created.json"
            created.write_text("created\n", encoding="utf-8")

            restore_write_snapshot(root, snapshot, backup)

            self.assertEqual(
                modified.read_text(encoding="utf-8"), "before modified\n"
            )
            self.assertEqual(
                deleted.read_text(encoding="utf-8"), "before deleted\n"
            )
            self.assertFalse(created.exists())

    def test_corrupt_backup_is_rejected_before_live_paths_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scope = root / "output/sample/questions_json/2026"
            scope.mkdir(parents=True)
            first = scope / "first.json"
            second = scope / "second.json"
            first.write_text("first before\n", encoding="utf-8")
            second.write_text("second before\n", encoding="utf-8")
            backup = root / "run" / "baseline_files"
            backup.parent.mkdir(parents=True)
            snapshot = capture_write_snapshot(root, [scope], backup)

            first.write_text("first after\n", encoding="utf-8")
            second.write_text("second after\n", encoding="utf-8")
            created = scope / "created.json"
            created.write_text("created after\n", encoding="utf-8")
            second_entry = snapshot["entries"][
                second.relative_to(root).as_posix()
            ]
            (backup / second_entry["backupFile"]).write_bytes(b"corrupt")

            with self.assertRaisesRegex(
                WriteTransactionError, "hashが一致しません"
            ):
                restore_write_snapshot(root, snapshot, backup)

            self.assertEqual(
                first.read_text(encoding="utf-8"), "first after\n"
            )
            self.assertEqual(
                second.read_text(encoding="utf-8"), "second after\n"
            )
            self.assertEqual(
                created.read_text(encoding="utf-8"), "created after\n"
            )

    def test_restore_does_not_clobber_a_preexisting_old_temp_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scope = root / "output/sample/questions_json/2026"
            scope.mkdir(parents=True)
            target = scope / "question.json"
            old_temp_name = scope / ".question.json.rollback.tmp"
            target.write_text("question before\n", encoding="utf-8")
            old_temp_name.write_text("keep this file\n", encoding="utf-8")
            backup = root / "run" / "baseline_files"
            backup.parent.mkdir(parents=True)
            snapshot = capture_write_snapshot(root, [scope], backup)

            target.write_text("question after\n", encoding="utf-8")
            old_temp_name.write_text("changed temp\n", encoding="utf-8")
            restore_write_snapshot(root, snapshot, backup)

            self.assertEqual(
                target.read_text(encoding="utf-8"), "question before\n"
            )
            self.assertEqual(
                old_temp_name.read_text(encoding="utf-8"), "keep this file\n"
            )

    def test_restore_writes_files_before_reapplying_read_only_directory_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scope = root / "output/sample/questions_json/2026"
            scope.mkdir(parents=True)
            target = scope / "question.json"
            target.write_text("before\n", encoding="utf-8")
            scope.chmod(0o555)
            backup = root / "run" / "baseline_files"
            backup.parent.mkdir(parents=True)
            snapshot = capture_write_snapshot(root, [scope], backup)

            scope.chmod(0o755)
            target.write_text("after\n", encoding="utf-8")
            restore_write_snapshot(root, snapshot, backup)

            self.assertEqual(target.read_text(encoding="utf-8"), "before\n")
            self.assertEqual(scope.stat().st_mode & 0o777, 0o555)


if __name__ == "__main__":
    unittest.main()
