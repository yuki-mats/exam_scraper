from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.scrape.knoun_images import digest
from scripts.scrape.knoun_ocr import make_draft


class KnounOcrTests(unittest.TestCase):
    def record(self, path):
        return {
            "sourceQuestionId": "https://knoun.jp/example/mondai1_17.html",
            "questionImage": {
                "filename": path.name, "relativePath": path.name,
                "sha256": digest(path.read_bytes()), "sourceUrl": "https://knoun.jp/example/mondai1_17.png",
            },
        }

    def test_draft_is_bound_to_one_image_and_stays_unreviewed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "mondai1_17.png"
            image.write_bytes(b"fixture image")
            target = root / "ocr.json"
            record = self.record(image)
            self.assertTrue(make_draft(record, "questionImage", root / "manifest.json", target, lambda _: "原画像の文字"))
            value = json.loads(target.read_text())
            self.assertEqual(value["binding"]["sourceQuestionId"], record["sourceQuestionId"])
            self.assertEqual(value["binding"]["imageSha256"], digest(image.read_bytes()))
            self.assertEqual(value["reviewState"], "needs_visual_review")
            self.assertNotIn("correctChoiceText", value)

    def test_changed_image_is_rejected_before_ocr(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "image.png"
            image.write_bytes(b"first")
            record = self.record(image)
            image.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                make_draft(record, "questionImage", root / "manifest.json", root / "ocr.json", lambda _: "text")
            self.assertFalse((root / "ocr.json").exists())

    def test_draft_from_other_question_cannot_be_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "image.png"
            image.write_bytes(b"image")
            record = self.record(image)
            target = root / "ocr.json"
            make_draft(record, "questionImage", root / "manifest.json", target, lambda _: "text")
            record["sourceQuestionId"] = "https://knoun.jp/example/mondai1_18.html"
            with self.assertRaises(ValueError):
                make_draft(record, "questionImage", root / "manifest.json", target, lambda _: "different")

    def test_same_image_keeps_existing_transcription(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "image.png"
            image.write_bytes(b"image")
            record = self.record(image)
            target = root / "ocr.json"
            make_draft(record, "questionImage", root / "manifest.json", target, lambda _: "first")
            self.assertFalse(make_draft(record, "questionImage", root / "manifest.json", target, lambda _: "second"))
            self.assertEqual(json.loads(target.read_text())["text"], "first")


if __name__ == "__main__":
    unittest.main()
