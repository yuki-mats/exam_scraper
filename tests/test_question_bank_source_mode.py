from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.question_bank.question_bank import main


class QuestionBankSourceModeTests(unittest.TestCase):
    def test_source_mode_does_not_require_uncreated_merged_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            base_dir = Path(temporary_dir) / "questions_json"
            source_dir = base_dir / "trial-group" / "00_source"
            source_dir.mkdir(parents=True)
            (source_dir / "question_trial.json").write_text(
                json.dumps(
                    {
                        "question_bodies": [
                            {
                                "question_url": "https://example.test/question/1",
                                "answer_result_text": "正解は 1 です。",
                                "public_question_id": "public-1",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            exit_code = main(
                [
                    "quality-gate",
                    "--base-dir",
                    str(base_dir),
                    "--list-group-id",
                    "trial-group",
                    "--mode",
                    "source",
                ]
            )

        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
