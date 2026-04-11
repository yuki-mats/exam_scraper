from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.upload.upload_question_images_to_storage import (
    ImageUploadItem,
    build_upload_plan,
    upload_images,
)


class FakeBlob:
    def __init__(self, exists: bool) -> None:
        self._exists = exists
        self.uploads: list[tuple[str, str | None]] = []

    def exists(self) -> bool:
        return self._exists

    def upload_from_filename(
        self,
        filename: str,
        content_type: str | None = None,
        **kwargs,
    ) -> None:
        self.uploads.append((filename, content_type))
        self._exists = True


class FakeBucket:
    name = "repaso-rbaqy4.appspot.com"

    def __init__(self, existing_paths: set[str] | None = None) -> None:
        self.existing_paths = existing_paths or set()
        self.blobs: dict[str, FakeBlob] = {}

    def blob(self, object_path: str) -> FakeBlob:
        if object_path not in self.blobs:
            self.blobs[object_path] = FakeBlob(object_path in self.existing_paths)
        return self.blobs[object_path]


class UploadQuestionImagesToStorageTests(unittest.TestCase):
    def test_build_upload_plan_deduplicates_same_filename_with_same_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir) / "output"
            image_root = output_root / "sample-qualification" / "question_images"
            first_dir = image_root / "85010"
            second_dir = image_root / "85011"
            first_dir.mkdir(parents=True)
            second_dir.mkdir(parents=True)
            (image_root / ".DS_Store").write_bytes(b"not-an-image")
            (first_dir / "notes.txt").write_text("skip", encoding="utf-8")
            (first_dir / "qsample_q_img01.png").write_bytes(b"same-image")
            (second_dir / "qsample_q_img01.png").write_bytes(b"same-image")

            plan = build_upload_plan("sample-qualification", output_root=output_root)

            self.assertEqual(len(plan), 1)
            self.assertEqual(plan[0].filename, "qsample_q_img01.png")
            self.assertEqual(
                plan[0].object_path,
                "question_images/official/sample-qualification/qsample_q_img01.png",
            )
            self.assertEqual(len(plan[0].duplicate_paths), 1)

    def test_build_upload_plan_raises_for_same_filename_with_different_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir) / "output"
            image_root = output_root / "sample-qualification" / "question_images"
            first_dir = image_root / "85010"
            second_dir = image_root / "85011"
            first_dir.mkdir(parents=True)
            second_dir.mkdir(parents=True)
            (first_dir / "qsample_q_img01.png").write_bytes(b"first")
            (second_dir / "qsample_q_img01.png").write_bytes(b"second")

            with self.assertRaises(ValueError):
                build_upload_plan("sample-qualification", output_root=output_root)

    def test_build_upload_plan_uses_questions_json_dirs_as_default_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir) / "output"
            qualification_root = output_root / "sample-qualification"
            (qualification_root / "questions_json" / "85010").mkdir(parents=True)
            image_root = qualification_root / "question_images"
            valid_dir = image_root / "85010"
            stray_dir = image_root / "99999"
            valid_dir.mkdir(parents=True)
            stray_dir.mkdir(parents=True)
            (valid_dir / "qvalid_q_img01.png").write_bytes(b"valid")
            (stray_dir / "qstray_q_img01.png").write_bytes(b"stray")

            plan = build_upload_plan("sample-qualification", output_root=output_root)

            self.assertEqual([item.filename for item in plan], ["qvalid_q_img01.png"])

    def test_upload_images_skips_existing_by_default(self) -> None:
        item = ImageUploadItem(
            filename="qsample_q_img01.png",
            local_path=Path("/tmp/qsample_q_img01.png"),
            duplicate_paths=(),
            object_path="question_images/official/sample-qualification/qsample_q_img01.png",
            public_url="https://example.test/image",
            sha256="dummy",
        )
        bucket = FakeBucket(existing_paths={item.object_path})

        summary = upload_images([item], bucket=bucket, dry_run=False, overwrite=False)

        self.assertEqual(summary.skipped_existing, 1)
        self.assertEqual(summary.uploaded, 0)
        self.assertEqual(bucket.blob(item.object_path).uploads, [])

    def test_upload_images_dry_run_counts_non_existing_without_upload(self) -> None:
        item = ImageUploadItem(
            filename="qsample_q_img01.png",
            local_path=Path("/tmp/qsample_q_img01.png"),
            duplicate_paths=(),
            object_path="question_images/official/sample-qualification/qsample_q_img01.png",
            public_url="https://example.test/image",
            sha256="dummy",
        )
        bucket = FakeBucket()

        summary = upload_images([item], bucket=bucket, dry_run=True, overwrite=False)

        self.assertEqual(summary.dry_run, 1)
        self.assertEqual(summary.uploaded, 0)
        self.assertEqual(bucket.blob(item.object_path).uploads, [])


if __name__ == "__main__":
    unittest.main()
