from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = ROOT_DIR / "scripts" / "pipeline" / "extract_question_subset.py"


class ExtractQuestionSubsetTest(unittest.TestCase):
    def run_script(self, payload: dict, *args: str) -> dict:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.json"
            output = Path(temp_dir) / "output.json"
            source.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--source",
                    str(source),
                    "--output",
                    str(output),
                    *args,
                ],
                cwd=ROOT_DIR,
                check=True,
                capture_output=True,
                text=True,
            )
            return json.loads(output.read_text(encoding="utf-8"))

    def test_filters_legacy_question_bodies_by_label(self) -> None:
        payload = {
            "question_bodies": [
                {"questionLabel": "問1"},
                {"questionLabel": "問2"},
            ]
        }

        result = self.run_script(payload, "--question-label", "問2")

        self.assertEqual(result["question_bodies"], [{"questionLabel": "問2"}])

    def test_filters_upload_ready_by_original_question_id(self) -> None:
        payload = {
            "questions": [
                {"questionId": "q1", "originalQuestionId": "original-1"},
                {"questionId": "q2", "originalQuestionId": "original-1"},
                {"questionId": "q3", "originalQuestionId": "original-2"},
            ],
            "total_count": 3,
        }

        result = self.run_script(
            payload,
            "--original-question-id",
            "original-1",
        )

        self.assertEqual(
            [question["questionId"] for question in result["questions"]],
            ["q1", "q2"],
        )
        self.assertEqual(result["total_count"], 2)


if __name__ == "__main__":
    unittest.main()
