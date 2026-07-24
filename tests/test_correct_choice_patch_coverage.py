import json
import tempfile
import unittest
from pathlib import Path

from scripts.check.check_correct_choice_patch_coverage import check_pair, compare_entries
from scripts.common.aggregate_answer_decomposition import (
    derived_source_unique_keys,
    source_text_hash,
)


class CorrectChoicePatchCoverageTests(unittest.TestCase):
    def setUp(self):
        self.source = [
            {
                "original_question_id": "q1",
                "question_url": "https://example.com/q1",
                "choiceTextList": ["選択肢"],
                "correctChoiceText": ["正しい"],
            }
        ]

    def compare(self, patch):
        return compare_entries(
            self.source,
            [patch],
            require_full=True,
            require_snippets=False,
            require_change_meta=False,
        )

    def test_question_url_can_be_omitted(self):
        errors, warnings = self.compare(
            {
                "original_question_id": "q1",
                "correctChoiceText": ["正しい"],
            }
        )

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_question_url_must_match_source_when_present(self):
        errors, _ = self.compare(
            {
                "original_question_id": "q1",
                "question_url": "https://example.com/other",
                "correctChoiceText": ["正しい"],
            }
        )

        self.assertTrue(any("question_url mismatch" in error for error in errors))

    def test_correct_choice_labels_must_be_canonical(self):
        errors, _ = self.compare(
            {
                "original_question_id": "q1",
                "correctChoiceText": ["誤り"],
            }
        )

        self.assertTrue(
            any("must contain only 正しい/間違い" in error for error in errors)
        )

    def test_checker_uses_question_type_projection_for_aggregate_choices(self):
        source_text = "前提。Aは正しい。Bは誤り。"
        spans = [
            {
                "start": source_text.index("Aは正しい。"),
                "end": source_text.index("Aは正しい。") + len("Aは正しい。"),
            },
            {
                "start": source_text.index("Bは誤り。"),
                "end": source_text.index("Bは誤り。") + len("Bは誤り。"),
            },
        ]
        decomposition = {
            "schemaVersion": "aggregate-answer-decomposition/v1",
            "sourceHash": source_text_hash(source_text),
            "classification": "target",
            "spans": spans,
            "decision": "approve",
            "issueCodes": [],
        }
        source_question = {
            "original_question_id": "q1",
            "questionBodyText": source_text,
            "choiceTextList": ["1個", "2個", "3個"],
        }
        question_type_entry = {
            "original_question_id": "q1",
            "questionType": "true_false",
            "isCalculationQuestion": False,
            "aggregateAnswerDecomposition": decomposition,
            "choiceTextList": ["Aは正しい。", "Bは誤り。"],
            "sourceUniqueKeys": derived_source_unique_keys(
                source_question,
                decomposition,
            ),
        }
        correct_entry = {
            "original_question_id": "q1",
            "correctChoiceText": ["正しい", "間違い"],
        }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.json"
            question_type_path = root / "question_type.json"
            correct_path = root / "correct.json"
            source_path.write_text(
                json.dumps(
                    {"question_bodies": [source_question]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            question_type_path.write_text(
                json.dumps([question_type_entry], ensure_ascii=False),
                encoding="utf-8",
            )
            correct_path.write_text(
                json.dumps([correct_entry], ensure_ascii=False),
                encoding="utf-8",
            )

            exit_code = check_pair(
                source_path,
                correct_path,
                require_full=True,
                require_snippets=False,
                require_change_meta=False,
                question_type_patch_path=question_type_path,
            )

        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
