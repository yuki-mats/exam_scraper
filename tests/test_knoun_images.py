from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from scripts.scrape.knoun_images import (
    BOOK_ID, atomic_json, frame_identity, image_url_from_html,
    main, parse_args, read_manifest, store_png,
)

BASE = f"https://knoun.jp/knounclient/contents/{BOOK_ID}/"


def png_bytes(color="white"):
    buffer = io.BytesIO()
    Image.new("RGB", (450, 800), color).save(buffer, format="PNG")
    return buffer.getvalue()


class KnounImageTests(unittest.TestCase):
    def test_question_and_comment_identity_are_checked_independently(self):
        self.assertEqual(frame_identity(BASE + "mondai1_17.html", role="mondai", group=1, book=BOOK_ID), "17")
        self.assertEqual(frame_identity(BASE + "comment1_17.html", role="comment", group=1, book=BOOK_ID), "17")
        for url in (BASE + "comment2_17.html", BASE + "mondai1_17.html", BASE.replace("knoun.jp", "elsewhere.test") + "comment1_17.html"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                frame_identity(url, role="comment", group=1, book=BOOK_ID)

    def test_image_is_bound_to_its_frame_instead_of_array_order(self):
        url = BASE + "mondai1_17.html"
        self.assertEqual(image_url_from_html('<img src="mondai1_17.png">', url), BASE + "mondai1_17.png")
        for html in ('<img src="mondai1_18.png">', '<img src="comment1_17.png">', '<input type="password"><img src="logo.png">', '<img src="mondai1_17.png"><img src="extra.png">'):
            with self.subTest(html=html), self.assertRaises(ValueError):
                image_url_from_html(html, url)

    def test_html_and_corrupt_images_are_not_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "image.png"
            for data, content_type in ((b"<html>login</html>", "text/html"), (b"\x89PNG\r\n\x1a\ninvalid", "image/png")):
                with self.assertRaises(Exception):
                    store_png(target, data, content_type)
                self.assertFalse(target.exists())

    def test_resume_keeps_existing_image_and_rejects_changed_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "image.png"
            data = png_bytes()
            first = store_png(target, data, "image/png")
            self.assertEqual(first, store_png(target, data, "image/png; charset=binary"))
            with self.assertRaises(ValueError):
                store_png(target, png_bytes("red"), "image/png")
            self.assertEqual(target.read_bytes(), data)

    def test_manifest_verifies_every_image_hash_on_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture_manifest.json"
            manifest = read_manifest(path, 1, BOOK_ID)
            question = store_png(path.parent / "raw/mondai1_17.png", png_bytes(), "image/png")
            explanation = store_png(path.parent / "raw/comment1_17.png", png_bytes(), "image/png")
            for image in (question, explanation):
                image["relativePath"] = f"raw/{image['filename']}"
            source_id = BASE + "mondai1_17.html"
            manifest["records"][source_id] = {
                "sourceQuestionId": source_id, "questionFrameUrl": source_id,
                "explanationFrameUrl": BASE + "comment1_17.html",
                "questionImage": question, "explanationImage": explanation,
            }
            atomic_json(path, manifest)
            self.assertEqual(read_manifest(path, 1, BOOK_ID), manifest)
            (path.parent / question["relativePath"]).write_bytes(png_bytes("red"))
            with self.assertRaises(ValueError):
                read_manifest(path, 1, BOOK_ID)

    def test_manifest_rejects_explanation_from_another_question(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture_manifest.json"
            value = read_manifest(path, 1, BOOK_ID)
            source_id = BASE + "mondai1_17.html"
            value["records"][source_id] = {
                "sourceQuestionId": source_id, "questionFrameUrl": source_id,
                "explanationFrameUrl": BASE + "comment1_18.html",
            }
            atomic_json(path, value)
            with self.assertRaises(ValueError):
                read_manifest(path, 1, BOOK_ID)

    def test_capture_requires_explicit_history_permission(self):
        with self.assertRaises(SystemExit):
            parse_args([])
        args = parse_args(["--allow-learning-history-updates", "--groups", "1", "--max-questions", "2"])
        self.assertEqual(args.groups, [1])
        self.assertEqual(args.max_questions, 2)
        self.assertTrue(args.headless)

    def test_browser_window_is_opened_only_by_explicit_option(self):
        args = parse_args(["--allow-learning-history-updates", "--headed"])
        self.assertFalse(args.headless)

    def test_verify_only_does_not_treat_partial_capture_as_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "eco-kentei/question_images/knoun-2026-mock-1/capture_manifest.json"
            atomic_json(path, read_manifest(path, 1, BOOK_ID))
            with self.assertRaisesRegex(RuntimeError, "未完了"):
                main(["--verify-only", "--groups", "1", "--output", directory])


if __name__ == "__main__":
    unittest.main()
