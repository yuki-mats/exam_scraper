from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.common.question_identity import (
    SourceIdentityBinding,
    load_source_record_inventory,
    source_question_key,
    source_record_ref,
    source_identity_aliases,
    workflow_identity_aliases,
)
from tools.question_review_console.projection import record_identity_aliases


class QuestionIdentityTests(unittest.TestCase):
    def test_source_inventory_builds_exact_binding_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = Path(temp_dir) / "00_source"
            source_dir.mkdir()
            (source_dir / "question_2026.json").write_text(
                json.dumps(
                    {
                        "question_bodies": [
                            {"original_question_id": "q1"},
                            {"original_question_id": "q2"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            inventory = load_source_record_inventory(
                source_dir,
                qualification="sample",
                list_group_id="2026",
            )

        self.assertEqual(
            [item.identity.binding.as_tuple() for item in inventory],
            [
                ("sample:2026:q1", "q1", "question_2026.json#0"),
                ("sample:2026:q2", "q2", "question_2026.json#1"),
            ],
        )

    def test_source_identity_binding_normalizes_supported_field_names(self) -> None:
        binding = SourceIdentityBinding.from_mapping(
            {
                "source_question_key": " source-key ",
                "originalQuestionId": " review-id ",
                "sourceRecordRef": " question.json#0 ",
            }
        )

        self.assertTrue(binding.is_complete())
        self.assertEqual(
            binding.as_tuple(),
            ("source-key", "review-id", "question.json#0"),
        )

    def test_source_and_workflow_aliases_have_separate_responsibilities(self) -> None:
        record = {
            "original_question_id": "source-q1",
            "sourceQuestionKey": "sample:2026:q1",
            "firestoreQuestionIds": ["doc-a", "doc-b"],
            "reviewQuestionId": "ui-session-id",
        }

        source = source_identity_aliases(record)
        workflow = workflow_identity_aliases(record)

        self.assertEqual(workflow, {"ui-session-id"})
        self.assertIn("source-q1", source)
        self.assertIn("sample:2026:q1", source)
        self.assertIn("firestore:doc-a,doc-b", source)
        self.assertNotIn("ui-session-id", source)
        self.assertEqual(record_identity_aliases(record), source | workflow)

    def test_workflow_id_alone_is_not_source_evidence(self) -> None:
        record = {"reviewQuestionId": "ui-only"}

        self.assertEqual(source_identity_aliases(record), set())
        self.assertEqual(workflow_identity_aliases(record), {"ui-only"})

    def test_explicit_source_question_key_is_never_rewritten(self) -> None:
        record = {
            "sourceQuestionKey": "stored:key",
            "original_question_id": "source-q1",
        }

        self.assertEqual(
            source_question_key("sample", "2026", record),
            "stored:key",
        )

    def test_generic_fallback_uses_stable_review_id_not_repeated_label(self) -> None:
        first = {
            "questionLabel": "問1",
            "original_question_id": "source-a",
        }
        second = {
            "questionLabel": "問1",
            "original_question_id": "source-b",
        }

        self.assertEqual(
            source_question_key("sample", "2026", first),
            "sample:2026:source-a",
        )
        self.assertEqual(
            source_question_key("sample", "2026", second),
            "sample:2026:source-b",
        )

    def test_gas_source_key_is_derived_from_source_identity(self) -> None:
        record = {
            "questionLabel": "問2",
            "original_question_id": (
                "firestore:gasushunin-koushu-kiso-2019-2-1,"
                "gasushunin-koushu-kiso-2019-2-2"
            ),
        }

        self.assertEqual(
            source_question_key("gas-shunin-kou", "2019", record),
            "gas-shunin:kou:2019:kiso:q02",
        )

    def test_source_record_ref_is_00_source_relative_and_indexed(self) -> None:
        self.assertEqual(
            source_record_ref(
                "nested/question_2026_1.json",
                2,
            ),
            "nested/question_2026_1.json#2",
        )
        self.assertEqual(
            source_record_ref("../escape.json", 0),
            "",
        )


if __name__ == "__main__":
    unittest.main()
