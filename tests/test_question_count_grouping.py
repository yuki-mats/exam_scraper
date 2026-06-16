from __future__ import annotations

import json
import importlib.util
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


COUNT_SUMMARY_MODULE = load_module(
    REPO_ROOT / "scripts" / "count_questions" / "1_update_question_count.py",
    "count_summary_module",
)
COMMON_QUESTION_COUNTING_MODULE = load_module(
    REPO_ROOT / "scripts" / "common" / "question_counting.py",
    "question_counting_module",
)
CATEGORY_COUNT_MODULE = load_module(
    REPO_ROOT / "scripts" / "count_questions" / "2_update_category_counts.py",
    "category_count_module",
)
UPLOAD_CATEGORY_MODULE = load_module(
    REPO_ROOT / "scripts" / "upload" / "upload_category_to_firestore.py",
    "upload_category_module",
)


class QuestionCountGroupingTests(unittest.TestCase):
    def test_common_module_applies_true_false_special_rule(self) -> None:
        payload = {
            "questions": [
                {"questionId": "q1_1", "originalQuestionId": "q1", "questionSetId": "setA", "questionType": "true_false"},
                {"questionId": "q1_2", "originalQuestionId": "q1", "questionSetId": "setA", "questionType": "true_false"},
                {"questionId": "q2_1", "originalQuestionId": "q2", "questionSetId": "setB", "questionType": "single_choice"},
                {"questionId": "q2_2", "originalQuestionId": "q2", "questionSetId": "setB", "questionType": "single_choice"},
            ]
        }

        total_questions, counter = COMMON_QUESTION_COUNTING_MODULE.analyze_question_payload(payload)

        self.assertEqual(total_questions, 3)
        self.assertEqual(counter["setA"], 2)
        self.assertEqual(counter["setB"], 1)

    def test_summary_count_treats_same_original_question_id_as_one(self) -> None:
        payload = {
            "questions": [
                {"questionId": "q1_1", "originalQuestionId": "q1", "questionSetId": "setA"},
                {"questionId": "q1_2", "originalQuestionId": "q1", "questionSetId": "setA"},
                {"questionId": "q2_1", "originalQuestionId": "q2", "questionSetId": "setA"},
            ]
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "sample.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            total_questions, counter, _ = COUNT_SUMMARY_MODULE.analyze_file(path)

        self.assertEqual(total_questions, 2)
        self.assertEqual(counter["setA"], 2)

    def test_category_count_treats_same_original_question_id_as_one(self) -> None:
        payload = {
            "questions": [
                {"questionId": "q1_1", "originalQuestionId": "q1", "questionSetId": "setA"},
                {"questionId": "q1_2", "originalQuestionId": "q1", "questionSetId": "setA"},
                {"questionId": "q2_1", "originalQuestionId": "q2", "questionSetId": "setB"},
            ]
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "sample.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            counter = CATEGORY_COUNT_MODULE.analyze_file(path)

        self.assertEqual(counter["setA"], 1)
        self.assertEqual(counter["setB"], 1)

    def test_true_false_summary_count_uses_split_question_ids(self) -> None:
        payload = {
            "questions": [
                {"questionId": "q1_1", "originalQuestionId": "q1", "questionSetId": "setA", "questionType": "true_false"},
                {"questionId": "q1_2", "originalQuestionId": "q1", "questionSetId": "setA", "questionType": "true_false"},
            ]
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "sample.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            total_questions, counter, _ = COUNT_SUMMARY_MODULE.analyze_file(path)

        self.assertEqual(total_questions, 2)
        self.assertEqual(counter["setA"], 2)

    def test_true_false_category_count_uses_split_question_ids(self) -> None:
        payload = {
            "questions": [
                {"questionId": "q1_1", "originalQuestionId": "q1", "questionSetId": "setA", "questionType": "true_false"},
                {"questionId": "q1_2", "originalQuestionId": "q1", "questionSetId": "setA", "questionType": "true_false"},
            ]
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "sample.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            counter = CATEGORY_COUNT_MODULE.analyze_file(path)

        self.assertEqual(counter["setA"], 2)

    def test_upload_category_counts_match_count_scripts(self) -> None:
        payload = {
            "questions": [
                {"questionId": "q1_1", "originalQuestionId": "q1", "questionSetId": "setA", "questionType": "true_false"},
                {"questionId": "q1_2", "originalQuestionId": "q1", "questionSetId": "setA", "questionType": "true_false"},
                {"questionId": "q2_1", "originalQuestionId": "q2", "questionSetId": "setB", "questionType": "single_choice"},
                {"questionId": "q2_2", "originalQuestionId": "q2", "questionSetId": "setB", "questionType": "single_choice"},
            ]
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "sample.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            counter = UPLOAD_CATEGORY_MODULE.aggregate_question_set_counts([path])

        self.assertEqual(counter["setA"], 2)
        self.assertEqual(counter["setB"], 1)


if __name__ == "__main__":
    unittest.main()
