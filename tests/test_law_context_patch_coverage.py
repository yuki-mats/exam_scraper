import json
import tempfile
import unittest
from pathlib import Path

from scripts.check.check_law_context_patch_coverage import check_pair


class LawContextPatchCoverageTests(unittest.TestCase):
    def test_checker_uses_effective_question_type_projection(self):
        source = {
            "original_question_id": "q1",
            "question_url": "https://example.com/q1",
            "questionBodyText": "問題本文",
            "choiceTextList": ["1", "2", "3", "4"],
            "questionType": "group_choice",
        }
        question_type = {
            "original_question_id": "q1",
            "question_url": "https://example.com/q1",
            "questionType": "true_false",
            "choiceTextList": ["A", "B", "C", "D", "E"],
            "isCalculationQuestion": False,
        }
        law_context = {
            "original_question_id": "q1",
            "question_url": "https://example.com/q1",
            "isLawRelated": False,
            "lawGroundedExplanationNotNeeded": True,
            "lawReferences": [[], [], [], [], []],
        }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "question.json"
            question_type_path = root / "question_questionType_fixed.json"
            law_context_path = root / "question_lawContext_prepared.json"
            source_path.write_text(
                json.dumps({"question_bodies": [source]}, ensure_ascii=False),
                encoding="utf-8",
            )
            question_type_path.write_text(
                json.dumps([question_type], ensure_ascii=False),
                encoding="utf-8",
            )
            law_context_path.write_text(
                json.dumps([law_context], ensure_ascii=False),
                encoding="utf-8",
            )

            result = check_pair(
                source_path,
                law_context_path,
                question_type_path,
            )

        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
