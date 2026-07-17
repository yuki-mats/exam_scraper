from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.merge.patch_views import apply_explanation_fields
from scripts.pipeline.materialize_law_revision_facts_from_audit import (
    LawRevisionFactsMaterializeError,
    materialize_law_revision_facts,
)
from tests.support.law_audit import valid_v2_audit_row


def dump_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


class MaterializeLawRevisionFactsFromAuditTests(unittest.TestCase):
    @staticmethod
    def _write_stable_identity_fixture(
        root: Path,
        *,
        include_source_key: bool = True,
    ) -> tuple[str, str, Path, Path]:
        review_id = "firestore:doc-a,doc-b"
        source_key = "sample:2026:q1"
        dump_json(
            root / "00_source" / "question_2026_1.json",
            {
                "question_bodies": [
                    {
                        "original_question_id": review_id,
                        **(
                            {"sourceQuestionKey": source_key}
                            if include_source_key
                            else {}
                        ),
                        "firestoreQuestionIds": ["doc-a", "doc-b"],
                        "choiceTextList": ["肢1"],
                        "correctChoiceText": ["正しい"],
                    }
                ]
            },
        )
        explanation_patch = root / "21_explanationText_added" / "law_patch.json"
        dump_json(
            explanation_patch,
            [{"original_question_id": review_id, "explanationText": ["正しい。"]}],
        )
        correct_patch = root / "23_correctChoiceText_fixed" / "correct_patch.json"
        dump_json(
            correct_patch,
            [{"original_question_id": review_id, "correctChoiceText": ["正しい"]}],
        )
        return review_id, source_key, explanation_patch, correct_patch

    def test_materializes_choice_level_facts_from_audit_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "85001"
            dump_json(
                root / "00_source" / "question_85001_1.json",
                {
                    "question_bodies": [
                        {
                            "original_question_id": "q1",
                            "sourceQuestionKey": "sample:85001:q1",
                            "choiceTextList": ["肢1", "肢2"],
                            "correctChoiceText": ["正しい", "間違い"],
                        }
                    ]
                },
            )
            explanation_patch = root / "21_explanationText_added" / "law_patch.json"
            dump_json(
                explanation_patch,
                [
                    {
                        "original_question_id": "q1",
                        "question_url": "https://example.com/q1",
                        "isLawRelated": True,
                        "explanationText": ["現行法でも正しい。", "現行法では正しい。"],
                        "lawReferences": [
                            [
                                {
                                    "role": "current_basis",
                                    "lawId": "325AC0000000201",
                                    "lawTitle": "建築基準法",
                                    "article": "6条",
                                    "referenceDate": "2026-07-04",
                                    "verificationStatus": "verified",
                                }
                            ],
                            [
                                {
                                    "role": "current_basis",
                                    "lawId": "325AC0000000201",
                                    "lawTitle": "建築基準法",
                                    "article": "6条",
                                    "referenceDate": "2026-07-04",
                                    "verificationStatus": "verified",
                                }
                            ],
                        ],
                    }
                ],
            )
            correct_patch = root / "23_correctChoiceText_fixed" / "correct_patch.json"
            dump_json(
                correct_patch,
                [
                    {
                        "original_question_id": "q1",
                        "correctChoiceText": ["正しい", "正しい"],
                    }
                ],
            )
            audit_path = Path(tmp) / "audit.jsonl"
            audit_path.write_text(
                json.dumps(
                    valid_v2_audit_row(
                        "q1",
                        "sample:85001:q1",
                        source_ref="question_85001_1.json#0",
                        choice_count=2,
                        qualification="sample",
                        listGroupId="85001",
                        isLawRelated=True,
                        auditStatus="updated_to_current_law",
                        examTimeDecision=["正しい", "間違い"],
                        currentLawDecision=["正しい", "正しい"],
                        noticeReason="現行法では要件が変更されている。",
                        sourceSummary="e-Govで建築基準法第6条を確認。",
                        remainingRisk="出題当時条文は別途二次確認する。",
                    ),
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            updated = materialize_law_revision_facts(
                list_group_dir=root,
                audit_jsonl_path=audit_path,
                explanation_patch_path=explanation_patch,
                correct_choice_patch_path=correct_patch,
            )

            self.assertEqual(updated, 1)
            result = json.loads(explanation_patch.read_text(encoding="utf-8"))
            facts = result[0]["lawRevisionFacts"]
            self.assertEqual(len(facts), 2)
            self.assertEqual(facts[1]["auditStatus"], "updated_to_current_law")
            self.assertEqual(facts[1]["reviewState"], "tertiary_verified")
            self.assertEqual(facts[1]["examTime"]["correctChoiceText"], "間違い")
            self.assertEqual(facts[1]["current"]["correctChoiceText"], "正しい")
            self.assertEqual(facts[1]["current"]["lawId"], "325AC0000000201")
            self.assertEqual(facts[1]["current"]["referenceDate"], "2026-07-04")
            self.assertEqual(facts[1]["current"]["verificationStatus"], "verified")
            self.assertEqual(
                facts[1]["evidenceSummary"]["refs"][0]["relation"],
                "current_basis",
            )
            self.assertIn("現行法では要件が変更", facts[1]["differenceFacts"][0])
            self.assertRegex(facts[1]["evidenceBindingHash"], r"^[0-9a-f]{64}$")

    def test_v2_joins_with_source_derived_review_id_and_source_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "2026"
            review_id, source_key, explanation_patch, correct_patch = (
                self._write_stable_identity_fixture(root)
            )
            audit_path = Path(tmp) / "audit.jsonl"
            audit_path.write_text(
                json.dumps(
                    valid_v2_audit_row(
                        review_id,
                        source_key,
                        auditStatus="same_as_current",
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            updated = materialize_law_revision_facts(
                list_group_dir=root,
                audit_jsonl_path=audit_path,
                explanation_patch_path=explanation_patch,
                correct_choice_patch_path=correct_patch,
            )

            self.assertEqual(updated, 1)
            result = json.loads(explanation_patch.read_text(encoding="utf-8"))
            self.assertIn("lawRevisionFacts", result[0])

    def test_v2_rejects_workflow_id_even_when_source_key_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "2026"
            _review_id, source_key, explanation_patch, correct_patch = (
                self._write_stable_identity_fixture(root)
            )
            before = explanation_patch.read_text(encoding="utf-8")
            audit_path = Path(tmp) / "audit.jsonl"
            audit_path.write_text(
                json.dumps(
                    valid_v2_audit_row(
                        "ui-session-id",
                        source_key,
                        auditStatus="same_as_current",
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                LawRevisionFactsMaterializeError,
                "source identity binding does not join",
            ):
                materialize_law_revision_facts(
                    list_group_dir=root,
                    audit_jsonl_path=audit_path,
                    explanation_patch_path=explanation_patch,
                    correct_choice_patch_path=correct_patch,
                )

            self.assertEqual(explanation_patch.read_text(encoding="utf-8"), before)

    def test_v2_requires_source_question_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "2026"
            review_id, _source_key, explanation_patch, correct_patch = (
                self._write_stable_identity_fixture(root)
            )
            audit_path = Path(tmp) / "audit.jsonl"
            row = valid_v2_audit_row(review_id, "unused")
            row.pop("sourceQuestionKey")
            audit_path.write_text(
                json.dumps(row)
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                LawRevisionFactsMaterializeError,
                "sourceQuestionKey is required",
            ):
                materialize_law_revision_facts(
                    list_group_dir=root,
                    audit_jsonl_path=audit_path,
                    explanation_patch_path=explanation_patch,
                    correct_choice_patch_path=correct_patch,
                )

    def test_v1_rejects_an_id_that_is_not_a_source_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "2026"
            _review_id, _source_key, explanation_patch, correct_patch = (
                self._write_stable_identity_fixture(root)
            )
            audit_path = Path(tmp) / "audit.jsonl"
            audit_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "law-revision-audit/v1",
                        "reviewQuestionId": "ui-session-id",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                LawRevisionFactsMaterializeError,
                "does not safely join",
            ):
                materialize_law_revision_facts(
                    list_group_dir=root,
                    audit_jsonl_path=audit_path,
                    explanation_patch_path=explanation_patch,
                    correct_choice_patch_path=correct_patch,
                )

    def test_duplicate_audit_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "2026"
            review_id, source_key, explanation_patch, correct_patch = (
                self._write_stable_identity_fixture(root)
            )
            row = json.dumps(valid_v2_audit_row(review_id, source_key))
            audit_path = Path(tmp) / "audit.jsonl"
            audit_path.write_text(f"{row}\n{row}\n", encoding="utf-8")

            with self.assertRaisesRegex(
                LawRevisionFactsMaterializeError,
                "duplicate source identity binding",
            ):
                materialize_law_revision_facts(
                    list_group_dir=root,
                    audit_jsonl_path=audit_path,
                    explanation_patch_path=explanation_patch,
                    correct_choice_patch_path=correct_patch,
                )

    def test_unmatched_selected_patch_record_stops_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "2026"
            review_id, source_key, explanation_patch, correct_patch = (
                self._write_stable_identity_fixture(root)
            )
            explanations = json.loads(
                explanation_patch.read_text(encoding="utf-8")
            )
            explanations.append(
                {
                    "original_question_id": "not-in-source",
                    "explanationText": ["正しい。未照合"],
                }
            )
            dump_json(explanation_patch, explanations)
            before = explanation_patch.read_bytes()
            audit_path = Path(tmp) / "audit.jsonl"
            audit_path.write_text(
                json.dumps(valid_v2_audit_row(review_id, source_key)) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                LawRevisionFactsMaterializeError,
                "patch record does not join",
            ):
                materialize_law_revision_facts(
                    list_group_dir=root,
                    audit_jsonl_path=audit_path,
                    explanation_patch_path=explanation_patch,
                    correct_choice_patch_path=correct_patch,
                )

            self.assertEqual(explanation_patch.read_bytes(), before)

    def test_v2_rejects_mismatched_source_record_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "2026"
            review_id, source_key, explanation_patch, correct_patch = (
                self._write_stable_identity_fixture(root)
            )
            before = explanation_patch.read_bytes()
            audit_path = Path(tmp) / "audit.jsonl"
            audit_path.write_text(
                json.dumps(
                    valid_v2_audit_row(
                        review_id,
                        source_key,
                        source_ref="another.json#0",
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                LawRevisionFactsMaterializeError,
                "source identity binding does not join",
            ):
                materialize_law_revision_facts(
                    list_group_dir=root,
                    audit_jsonl_path=audit_path,
                    explanation_patch_path=explanation_patch,
                    correct_choice_patch_path=correct_patch,
                )

            self.assertEqual(explanation_patch.read_bytes(), before)

    def test_v2_joins_when_source_key_is_derived_from_source_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "2026"
            review_id, _stored_key, explanation_patch, correct_patch = (
                self._write_stable_identity_fixture(
                    root,
                    include_source_key=False,
                )
            )
            derived_key = f"sample:2026:{review_id}"
            audit_path = Path(tmp) / "audit.jsonl"
            audit_path.write_text(
                json.dumps(
                    valid_v2_audit_row(review_id, derived_key),
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            updated = materialize_law_revision_facts(
                list_group_dir=root,
                audit_jsonl_path=audit_path,
                explanation_patch_path=explanation_patch,
                correct_choice_patch_path=correct_patch,
                qualification="sample",
                list_group_id="2026",
            )

            self.assertEqual(updated, 1)

    def test_pair_join_allows_shared_source_key_and_ignores_other_sidecar_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "2026"
            shared_key = "sample:2026:shared"
            dump_json(
                root / "00_source" / "questions.json",
                {
                    "question_bodies": [
                        {
                            "original_question_id": review_id,
                            "sourceQuestionKey": shared_key,
                            "choiceTextList": ["肢1"],
                            "correctChoiceText": ["正しい"],
                        }
                        for review_id in ("q1", "q2")
                    ]
                },
            )
            explanation_patch = root / "21_explanationText_added" / "q2.json"
            correct_patch = root / "23_correctChoiceText_fixed" / "q2.json"
            dump_json(
                explanation_patch,
                [
                    {
                        "original_question_id": "q2",
                        "sourceQuestionKey": shared_key,
                        "sourceRecordRef": "questions.json#1",
                        "explanationText": ["正しい。"],
                    }
                ],
            )
            dump_json(
                correct_patch,
                [
                    {
                        "original_question_id": "q2",
                        "sourceQuestionKey": shared_key,
                        "sourceRecordRef": "questions.json#1",
                        "correctChoiceText": ["正しい"],
                    }
                ],
            )
            audit_path = Path(tmp) / "audit.jsonl"
            audit_path.write_text(
                "\n".join(
                    json.dumps(
                        valid_v2_audit_row(
                            review_id,
                            shared_key,
                            source_ref=(
                                "questions.json#0"
                                if review_id == "q1"
                                else "questions.json#1"
                            ),
                        ),
                        ensure_ascii=False,
                    )
                    for review_id in ("q1", "q2")
                )
                + "\n",
                encoding="utf-8",
            )

            updated = materialize_law_revision_facts(
                list_group_dir=root,
                audit_jsonl_path=audit_path,
                explanation_patch_path=explanation_patch,
                correct_choice_patch_path=correct_patch,
            )

            result = json.loads(explanation_patch.read_text(encoding="utf-8"))
            self.assertEqual(updated, 1)
            self.assertIn("lawRevisionFacts", result[0])

    def test_materialized_fact_inherits_normalized_review_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "2026"
            review_id, source_key, explanation_patch, correct_patch = (
                self._write_stable_identity_fixture(root)
            )
            audit_path = Path(tmp) / "audit.jsonl"
            audit_path.write_text(
                json.dumps(
                    valid_v2_audit_row(
                        review_id,
                        source_key,
                        reviewState=" Primary-Verified ",
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            materialize_law_revision_facts(
                list_group_dir=root,
                audit_jsonl_path=audit_path,
                explanation_patch_path=explanation_patch,
                correct_choice_patch_path=correct_patch,
            )

            result = json.loads(explanation_patch.read_text(encoding="utf-8"))
            self.assertEqual(
                result[0]["lawRevisionFacts"][0]["reviewState"],
                "primary_checked",
            )

    def test_later_invalid_row_does_not_change_output_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "2026"
            records = [
                {
                    "original_question_id": review_id,
                    "sourceQuestionKey": f"sample:2026:{review_id}",
                    "choiceTextList": ["肢1"],
                    "correctChoiceText": ["正しい"],
                }
                for review_id in ("q1", "q2")
            ]
            dump_json(
                root / "00_source" / "questions.json",
                {"question_bodies": records},
            )
            explanation_patch = root / "21_explanationText_added" / "patch.json"
            correct_patch = root / "23_correctChoiceText_fixed" / "patch.json"
            dump_json(
                explanation_patch,
                [
                    {
                        "original_question_id": review_id,
                        "explanationText": ["正しい。"],
                    }
                    for review_id in ("q1", "q2")
                ],
            )
            dump_json(
                correct_patch,
                [
                    {
                        "original_question_id": review_id,
                        "correctChoiceText": ["正しい"],
                    }
                    for review_id in ("q1", "q2")
                ],
            )
            before = explanation_patch.read_bytes()
            second = valid_v2_audit_row(
                "q2",
                "sample:2026:q2",
                source_ref="questions.json#1",
            )
            second["auditInputHash"] = "invalid"
            audit_path = Path(tmp) / "audit.jsonl"
            audit_path.write_text(
                "\n".join(
                    json.dumps(row, ensure_ascii=False)
                    for row in (
                        valid_v2_audit_row(
                            "q1",
                            "sample:2026:q1",
                            source_ref="questions.json#0",
                        ),
                        second,
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                LawRevisionFactsMaterializeError,
                "auditInputHash",
            ):
                materialize_law_revision_facts(
                    list_group_dir=root,
                    audit_jsonl_path=audit_path,
                    explanation_patch_path=explanation_patch,
                    correct_choice_patch_path=correct_patch,
                )

            self.assertEqual(explanation_patch.read_bytes(), before)

    def test_explanation_merge_copies_law_revision_facts(self) -> None:
        data = {"question_bodies": [{"original_question_id": "q1"}]}
        facts = {"auditStatus": "same_as_current"}

        updated = apply_explanation_fields(
            data,
            {"q1": {"lawRevisionFacts": facts}},
        )

        self.assertEqual(updated, 1)
        self.assertEqual(data["question_bodies"][0]["lawRevisionFacts"], facts)


if __name__ == "__main__":
    unittest.main()
