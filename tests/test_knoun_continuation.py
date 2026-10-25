import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.pipeline.continue_knoun_maintenance import eligible_sources


class KnounContinuationTest(unittest.TestCase):
    def test_registered_transcription_conflict_is_held_without_blocking_other_ids(self):
        urls = [f"https://knoun.jp/book/mondai1_{n}.html" for n in (7,2,9)]
        manifest = {"records": dict.fromkeys(urls, {})}
        values = {u: {"source_question_id": u, "questionBodyText": "現在の転記"} for u in urls}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = root / "questions_json/knoun-2026-mock-1/00_source"
            sources.mkdir(parents=True)
            conflict = sources / "question_mondai1_2.json"
            old = json.dumps({"question_bodies": [values[urls[1]] | {"questionBodyText": "登録時の転記"}]}).encode()
            conflict.write_bytes(old)
            unchanged = sources / "question_mondai1_9.json"
            unchanged.write_text(json.dumps({"question_bodies": [values[urls[2]]]}))
            with patch("scripts.pipeline.continue_knoun_maintenance.read_manifest", return_value=manifest), patch(
                "scripts.pipeline.continue_knoun_maintenance.source_record",
                side_effect=lambda root, group, source_id, **kwargs: values[source_id],
            ):
                eligible, holds = eligible_sources(root, 1)
            self.assertEqual(eligible, [urls[0], urls[2]])
            self.assertEqual(len(holds), 1)
            self.assertEqual(holds[0]["sourceQuestionId"], urls[1])
            self.assertIn("登録済み00_source", holds[0]["reason"])
            self.assertEqual(conflict.read_bytes(), old)
            self.assertFalse((sources / "question_mondai1_7.json").exists())


if __name__ == "__main__":
    unittest.main()
