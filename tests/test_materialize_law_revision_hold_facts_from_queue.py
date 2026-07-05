from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.pipeline.materialize_law_revision_hold_facts_from_queue import (
    LawRevisionHoldMaterializeError,
    materialize_hold_facts,
)


def dump_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def write_queue(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            fh.write("\n")


def queue_record(choice_index: int) -> dict[str, object]:
    return {
        "schemaVersion": "law-revision-audit-queue/v1",
        "auditReason": "missing_lawRevisionFacts",
        "questionId": f"q1_{choice_index + 1}",
        "originalQuestionId": "q1",
        "question": {
            "correctChoiceText": "正しい" if choice_index == 0 else "間違い",
        },
        "lawReferences": [
            {
                "choiceIndex": choice_index,
                "role": "current_basis",
                "lawId": "325AC0000000201",
                "lawTitle": "建築基準法",
                "article": "6条",
                "paragraph": "1項",
                "item": f"{choice_index + 1}号",
                "referenceDate": "2026-07-04",
                "verificationStatus": "verified",
            }
        ],
        "currentEvidence": {
            "refs": [
                {
                    "lawReference": {
                        "choiceIndex": choice_index,
                        "role": "current_basis",
                        "lawId": "325AC0000000201",
                        "lawTitle": "建築基準法",
                        "article": "6条",
                        "paragraph": "1項",
                        "item": f"{choice_index + 1}号",
                        "referenceDate": "2026-07-04",
                        "verificationStatus": "verified",
                    },
                    "snapshot": {
                        "apiUrl": "https://laws.e-gov.go.jp/api/1/articles;lawId=325AC0000000201;article=6",
                        "articleTextHash": f"hash-{choice_index}",
                        "rawXmlHash": f"raw-{choice_index}",
                        "status": "fetched",
                    },
                }
            ]
        },
    }


def queue_record_for(original_id: str, choice_index: int) -> dict[str, object]:
    record = queue_record(choice_index)
    record["originalQuestionId"] = original_id
    record["questionId"] = f"{original_id}_{choice_index + 1}"
    return record


def wrong_suffix_queue_record(question_id: str, basis_choice_index: int) -> dict[str, object]:
    record = queue_record(basis_choice_index)
    record["originalQuestionId"] = "q1"
    record["questionId"] = question_id
    for ref in record["lawReferences"]:  # type: ignore[index]
        ref["choiceIndex"] = basis_choice_index
    for ref in record["currentEvidence"]["refs"]:  # type: ignore[index]
        ref["lawReference"]["choiceIndex"] = basis_choice_index
    return record


class MaterializeLawRevisionHoldFactsFromQueueTests(unittest.TestCase):
    def test_materializes_hold_facts_for_complete_choice_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            patch_path = root / "patch.json"
            output_path = root / "patch_hold.json"
            queue_path = root / "queue.jsonl"
            dump_json(
                patch_path,
                [
                    {
                        "original_question_id": "q1",
                        "question_url": "https://example.com/q1",
                        "explanationText": ["説明1", "説明2"],
                        "lawReferences": [[], []],
                        "isLawRelated": [True, True],
                        "lawGroundedExplanationNotNeeded": [False, False],
                        "suggestedQuestions": ["現行法では？"],
                        "suggestedQuestionDetails": [
                            {"question": "現行法では？", "answer": "確認中です。"}
                        ],
                    }
                ],
            )
            write_queue(queue_path, [queue_record(0), queue_record(1)])

            updated_questions, updated_choices = materialize_hold_facts(
                queue_jsonl_path=queue_path,
                explanation_patch_path=patch_path,
                output_path=output_path,
            )

            self.assertEqual(updated_questions, 1)
            self.assertEqual(updated_choices, 2)
            result = json.loads(output_path.read_text(encoding="utf-8"))
            facts = result[0]["lawRevisionFacts"]
            self.assertEqual(len(facts), 2)
            self.assertTrue(all(fact["auditStatus"] == "hold" for fact in facts))
            self.assertTrue(
                all(fact["reviewState"] == "needs_secondary_review" for fact in facts)
            )
            self.assertEqual(
                facts[0]["current"]["sourceUrl"],
                "https://laws.e-gov.go.jp/api/1/articles;lawId=325AC0000000201;article=6",
            )
            self.assertEqual(facts[0]["current"]["articleTextHash"], "hash-0")
            self.assertEqual(facts[0]["evidenceSummary"]["refs"][0]["lawId"], "325AC0000000201")
            self.assertIn("推測して断定しない", facts[0]["evidenceSummary"]["promptContext"])
            self.assertRegex(facts[0]["evidenceBindingHash"], r"^[0-9a-f]{64}$")

    def test_rejects_partial_choice_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            patch_path = root / "patch.json"
            output_path = root / "patch_hold.json"
            queue_path = root / "queue.jsonl"
            dump_json(
                patch_path,
                [
                    {
                        "original_question_id": "q1",
                        "explanationText": ["説明1", "説明2"],
                    }
                ],
            )
            write_queue(queue_path, [queue_record(0)])

            with self.assertRaisesRegex(
                LawRevisionHoldMaterializeError,
                "queue must contain all choices",
            ):
                materialize_hold_facts(
                    queue_jsonl_path=queue_path,
                    explanation_patch_path=patch_path,
                    output_path=output_path,
                )

    def test_rejects_existing_facts_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            patch_path = root / "patch.json"
            output_path = root / "patch_hold.json"
            queue_path = root / "queue.jsonl"
            dump_json(
                patch_path,
                [
                    {
                        "original_question_id": "q1",
                        "explanationText": ["説明1"],
                        "lawRevisionFacts": [{"auditStatus": "same_as_current"}],
                    }
                ],
            )
            write_queue(queue_path, [queue_record(0)])

            with self.assertRaisesRegex(LawRevisionHoldMaterializeError, "already exists"):
                materialize_hold_facts(
                    queue_jsonl_path=queue_path,
                    explanation_patch_path=patch_path,
                    output_path=output_path,
                )

    def test_can_skip_queue_records_for_other_patch_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            patch_path = root / "patch.json"
            output_path = root / "patch_hold.json"
            queue_path = root / "queue.jsonl"
            dump_json(
                patch_path,
                [
                    {
                        "original_question_id": "q1",
                        "explanationText": ["説明1"],
                    }
                ],
            )
            write_queue(queue_path, [queue_record_for("q1", 0), queue_record_for("q2", 0)])

            updated_questions, updated_choices = materialize_hold_facts(
                queue_jsonl_path=queue_path,
                explanation_patch_path=patch_path,
                output_path=output_path,
                skip_missing_patch_ids=True,
            )

            self.assertEqual(updated_questions, 1)
            self.assertEqual(updated_choices, 1)
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(result[0]["lawRevisionFacts"][0]["auditStatus"], "hold")

    def test_maps_wrong_suffix_records_back_to_choice_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            patch_path = root / "patch.json"
            output_path = root / "patch_hold.json"
            queue_path = root / "queue.jsonl"
            dump_json(
                patch_path,
                [
                    {
                        "original_question_id": "q1",
                        "explanationText": ["説明1", "説明2", "説明3"],
                    }
                ],
            )
            write_queue(
                queue_path,
                [
                    wrong_suffix_queue_record("q1_w1", 1),
                    wrong_suffix_queue_record("q1", 1),
                    wrong_suffix_queue_record("q1_w2", 1),
                ],
            )

            updated_questions, updated_choices = materialize_hold_facts(
                queue_jsonl_path=queue_path,
                explanation_patch_path=patch_path,
                output_path=output_path,
            )

            self.assertEqual(updated_questions, 1)
            self.assertEqual(updated_choices, 3)
            result = json.loads(output_path.read_text(encoding="utf-8"))
            refs = [
                fact["evidenceSummary"]["refs"][0]["refId"]
                for fact in result[0]["lawRevisionFacts"]
            ]
            self.assertEqual(
                refs,
                [
                    "choice_1_hold_current_basis_1",
                    "choice_2_hold_current_basis_1",
                    "choice_3_hold_current_basis_1",
                ],
            )

    def test_prefers_direct_choice_indexes_when_wrong_suffix_records_are_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            patch_path = root / "patch.json"
            output_path = root / "patch_hold.json"
            queue_path = root / "queue.jsonl"
            dump_json(
                patch_path,
                [
                    {
                        "original_question_id": "q1",
                        "explanationText": ["説明1", "説明2", "説明3"],
                    }
                ],
            )
            write_queue(
                queue_path,
                [
                    wrong_suffix_queue_record("q1_w1", 0),
                    wrong_suffix_queue_record("q1", 1),
                    wrong_suffix_queue_record("q1_w2", 2),
                ],
            )

            updated_questions, updated_choices = materialize_hold_facts(
                queue_jsonl_path=queue_path,
                explanation_patch_path=patch_path,
                output_path=output_path,
            )

            self.assertEqual(updated_questions, 1)
            self.assertEqual(updated_choices, 3)
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(
                [
                    fact["current"]["item"]
                    for fact in result[0]["lawRevisionFacts"]
                ],
                ["1号", "2号", "3号"],
            )


if __name__ == "__main__":
    unittest.main()
