from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.common.image_storage_urls import build_public_storage_url
from scripts.fix import rewrite_image_storage_urls as module


class RewriteImageStorageUrlsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.output_root = self.root / "output"
        self.qualification = "sample-qualification"
        self.questions_json_dir = self.output_root / self.qualification / "questions_json"
        self.source_dir = self.questions_json_dir / "85010" / "00_source"
        self.source_dir.mkdir(parents=True, exist_ok=True)
        self.legacy_url = (
            "https://firebasestorage.googleapis.com/v0/b/repaso-rbaqy4.appspot.com/o/"
            "question_images%2Fofficial%2Fsample-qualification%2F85010%2Fqsample_q_img01.png?alt=media"
        )
        self.current_file = self.source_dir / "question_85010_1.json"
        self.current_file.write_text(
            json.dumps({"question_bodies": [{"questionImageStorageUrls": [self.legacy_url]}]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        self.old_dir = self.source_dir / "old"
        self.old_dir.mkdir(parents=True, exist_ok=True)
        self.old_file = self.old_dir / "question_85010_old.json"
        self.old_file.write_text(
            json.dumps({"question_bodies": [{"questionImageStorageUrls": [self.legacy_url]}]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_dry_run_reports_changes_without_rewriting_files(self) -> None:
        before = self.current_file.read_text(encoding="utf-8")

        exit_code = module.main(
            [
                "--output-root",
                str(self.output_root),
                "--qualification",
                self.qualification,
                "--dry-run",
            ]
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(self.current_file.read_text(encoding="utf-8"), before)
        self.assertFalse(any(path.name.startswith("2026") for path in self.old_dir.iterdir()))

    def test_write_mode_rewrites_current_files_and_archives_previous_versions(self) -> None:
        fake_now = mock.Mock()
        fake_now.strftime.return_value = "20260409_120000"

        with mock.patch.object(module, "datetime") as mocked_datetime:
            mocked_datetime.now.return_value = fake_now
            exit_code = module.main(
                [
                    "--output-root",
                    str(self.output_root),
                    "--qualification",
                    self.qualification,
                ]
            )

        self.assertEqual(exit_code, 0)
        rewritten = json.loads(self.current_file.read_text(encoding="utf-8"))
        self.assertEqual(
            rewritten["question_bodies"][0]["questionImageStorageUrls"],
            [build_public_storage_url(self.qualification, "qsample_q_img01.png")],
        )
        archived_file = self.old_dir / "20260409_120000" / self.current_file.name
        self.assertTrue(archived_file.exists())
        archived = json.loads(archived_file.read_text(encoding="utf-8"))
        self.assertEqual(
            archived["question_bodies"][0]["questionImageStorageUrls"],
            [self.legacy_url],
        )
        old_payload = json.loads(self.old_file.read_text(encoding="utf-8"))
        self.assertEqual(
            old_payload["question_bodies"][0]["questionImageStorageUrls"],
            [self.legacy_url],
        )


if __name__ == "__main__":
    unittest.main()
