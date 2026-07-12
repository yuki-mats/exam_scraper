import importlib
import json
import tempfile
import unittest
from pathlib import Path


merge_all = importlib.import_module("scripts.merge.00_merge_all").merge_all


class MergeStrictCorrectChoiceBeforeExplanationTests(unittest.TestCase):
    def test_strict_correct_choice_is_written_to_merged1(self):
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            group_dir = base_dir / "2026"
            source_dir = group_dir / "00_source"
            patch_dir = group_dir / "23_correctChoiceText_fixed"
            source_dir.mkdir(parents=True)
            patch_dir.mkdir(parents=True)

            source = {
                "question_bodies": [
                    {
                        "original_question_id": "question-1",
                        "questionBodyText": "確認対象",
                        "choiceTextList": ["選択肢"],
                        "questionType": "flash_card",
                        "questionIntent": "select_correct",
                        "answer_result_text": "正解は1です。",
                        "correctChoiceText": "下書き",
                        "examYear": 2026,
                    }
                ]
            }
            patch = [
                {
                    "original_question_id": "question-1",
                    "correctChoiceText": "厳密レビュー済み",
                }
            ]
            (source_dir / "question_2026_1.json").write_text(
                json.dumps(source, ensure_ascii=False), encoding="utf-8"
            )
            (patch_dir / "question_2026_1_merged_correctChoiceText_fixed.json").write_text(
                json.dumps(patch, ensure_ascii=False), encoding="utf-8"
            )

            merge_all("2026", base_dir, require_answer_result_text=False)

            merged = json.loads(
                (group_dir / "20_merged_1" / "question_2026_1_merged.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(
            merged["question_bodies"][0]["correctChoiceText"], "厳密レビュー済み"
        )


if __name__ == "__main__":
    unittest.main()
