from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.fix.materialize_minimal_patch import (
    bind_source_questions,
    get_source_questions,
    materialize_entries,
    materialize_question_type,
)
from scripts.common.aggregate_answer_decomposition import (
    REVIEW_SCHEMA_VERSION,
    source_text_hash,
)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def explanation_entry(identity: dict[str, str] | None = None) -> dict[str, object]:
    return {
        "original_question_id": "shared",
        **(identity or {}),
        "explanationText": ["正しい。根拠を確認した。"],
        "suggestedQuestions": ["判断条件は何ですか？"],
        "suggestedQuestionDetails": [
            {
                "question": "判断条件は何ですか？",
                "answer": "対象と条件を分けて確認する。",
            }
        ],
    }


class MaterializeMinimalPatchIdentityTests(unittest.TestCase):
    def test_question_type_materializes_consensus_spans_from_source(self) -> None:
        body = "組合せを選べ。\nA 原文一。\nB 原文二。"
        first_start = body.index("A 原文一。")
        second_start = body.index("B 原文二。")
        review = {
            "schemaVersion": REVIEW_SCHEMA_VERSION,
            "sourceHash": source_text_hash(body),
            "classification": "target",
            "spans": [
                {"start": first_start, "end": first_start + len("A 原文一。")},
                {"start": second_start, "end": second_start + len("B 原文二。")},
            ],
            "decision": "approve",
            "issueCodes": [],
        }
        source = {
            "canonical_question_key": "sample:2026:q001",
            "questionBodyText": body,
            "choiceTextList": ["A、B"],
            "original_question_id": "q1",
            "question_url": "https://example.test/q1",
        }

        actual = materialize_question_type(
            source,
            {
                "questionType": "flash_card",
                "isCalculationQuestion": False,
                "aggregateAnswerReviews": [review, dict(review)],
            },
        )

        self.assertEqual(actual["questionType"], "true_false")
        self.assertEqual(actual["choiceTextList"], ["A 原文一。", "B 原文二。"])
        self.assertNotIn("aggregateAnswerReviews", actual)

    def test_question_type_materialization_keeps_calculation_flag(self) -> None:
        source = {
            "questionBodyText": "計算する。",
            "choiceTextList": ["1", "2"],
            "original_question_id": "q1",
            "question_url": "https://example.test/q1",
        }

        actual = materialize_question_type(
            source,
            {"questionType": "flash_card", "isCalculationQuestion": True},
        )

        self.assertIs(actual["isCalculationQuestion"], True)

    def test_question_type_materialization_requires_boolean_calculation_flag(self) -> None:
        source = {"original_question_id": "q1"}

        with self.assertRaisesRegex(ValueError, "isCalculationQuestion"):
            materialize_question_type(source, {"questionType": "flash_card"})

    def _source_file(self, root: Path, records: list[dict]) -> Path:
        source_path = (
            root
            / "output/sample/questions_json/2026/00_source/question_2026.json"
        )
        write_json(source_path, {"question_bodies": records})
        return source_path

    def test_exact_bindings_materialize_duplicate_legacy_ids_safely(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = self._source_file(
                Path(temp_dir),
                [
                    {"original_question_id": "shared", "choiceTextList": ["A"]},
                    {"original_question_id": "shared", "choiceTextList": ["B"]},
                ],
            )
            inputs = bind_source_questions(
                source_path,
                get_source_questions(json.loads(source_path.read_text())),
            )
            raw = [
                explanation_entry(source.identity.binding.as_mapping())
                for source in inputs
            ]
            raw[0]["explanationText"] = ["正しい。1件目の根拠を確認した。"]
            raw[1]["explanationText"] = ["正しい。2件目の根拠を確認した。"]
            raw.reverse()

            materialized = materialize_entries("explanation", inputs, raw)

        self.assertEqual(
            [entry["sourceRecordRef"] for entry in materialized],
            ["question_2026.json#0", "question_2026.json#1"],
        )
        self.assertTrue(
            all(entry["reviewQuestionId"] == "shared" for entry in materialized)
        )
        self.assertEqual(
            [entry["explanationText"][0] for entry in materialized],
            ["正しい。1件目の根拠を確認した。", "正しい。2件目の根拠を確認した。"],
        )

    def test_legacy_duplicate_id_is_not_assigned_by_position(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = self._source_file(
                Path(temp_dir),
                [
                    {"original_question_id": "shared", "choiceTextList": ["A"]},
                    {"original_question_id": "shared", "choiceTextList": ["B"]},
                ],
            )
            inputs = bind_source_questions(
                source_path,
                get_source_questions(json.loads(source_path.read_text())),
            )

            with self.assertRaisesRegex(ValueError, "一意|競合"):
                materialize_entries(
                    "explanation",
                    inputs,
                    [explanation_entry(), explanation_entry()],
                )

    def test_unmatched_and_duplicate_exact_raw_entries_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = self._source_file(
                Path(temp_dir),
                [{"original_question_id": "shared", "choiceTextList": ["A"]}],
            )
            inputs = bind_source_questions(
                source_path,
                get_source_questions(json.loads(source_path.read_text())),
            )
            unmatched = explanation_entry()
            unmatched["original_question_id"] = "missing"
            with self.assertRaisesRegex(ValueError, "対応しない"):
                materialize_entries("explanation", inputs, [unmatched])

            exact = explanation_entry(inputs[0].identity.binding.as_mapping())
            with self.assertRaisesRegex(ValueError, "一意"):
                materialize_entries("explanation", inputs, [exact, dict(exact)])

    def test_merged_view_uses_filename_scope_for_shared_legacy_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            group = root / "output/sample/questions_json/2026"
            for suffix in ("1", "2"):
                write_json(
                    group / f"00_source/question_2026_{suffix}.json",
                    {"question_bodies": [{"original_question_id": "shared"}]},
                )
            merged_path = group / "20_merged_1/question_2026_2_merged.json"
            write_json(
                merged_path,
                {"question_bodies": [{"original_question_id": "shared"}]},
            )

            inputs = bind_source_questions(
                merged_path,
                get_source_questions(json.loads(merged_path.read_text())),
            )

        self.assertEqual(
            inputs[0].identity.binding.source_record_ref,
            "question_2026_2.json#0",
        )


if __name__ == "__main__":
    unittest.main()
