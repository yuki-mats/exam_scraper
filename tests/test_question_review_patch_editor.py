import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.common.question_identity import SourceIdentityBinding
from tools.question_review_console.patch_editor import (
    DirectEditError,
    PatchEditor,
    _find_entry,
)


class QuestionReviewPatchEditorTests(unittest.TestCase):
    def test_entry_lookup_prefers_exact_binding_over_shared_alias(self):
        target = SourceIdentityBinding.from_values(
            "sample:2026:q1", "q1", "question_2026_2.json#0"
        )
        other = SourceIdentityBinding.from_values(
            "sample:2026:q1", "q1", "question_2026_1.json#0"
        )
        records = [
            {"original_question_id": "q1", **other.as_mapping()},
            {"original_question_id": "q1", **target.as_mapping()},
        ]

        found = _find_entry(records, {"q1"}, target)

        self.assertIs(found, records[1])

    @staticmethod
    def _two_patch_fixture(root: Path):
        group = root / "output" / "sample" / "questions_json" / "2026"
        source_path = group / "00_source" / "question_2026_1.json"
        explanation_path = (
            group
            / "21_explanationText_added"
            / "question_2026_1_explanationText_added.json"
        )
        source_path.parent.mkdir(parents=True)
        explanation_path.parent.mkdir(parents=True)
        source_path.write_text(
            json.dumps(
                {
                    "question_bodies": [
                        {
                            "original_question_id": "q1",
                            "question_url": "https://example.test/q1",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        explanation_path.write_text(
            json.dumps(
                [
                    {
                        "original_question_id": "q1",
                        "explanationText": ["正しい。旧", "間違い。旧"],
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        question = {
            "stateHash": "hash-1",
            "source": {
                "original_question_id": "q1",
                "question_url": "https://example.test/q1",
            },
            "projected": {
                "original_question_id": "q1",
                "choiceTextList": ["A", "B"],
                "correctChoiceText": ["正しい", "間違い"],
                "explanationText": ["正しい。旧", "間違い。旧"],
                "isLawRelated": False,
            },
            "paths": {
                "source": str(source_path.relative_to(root)),
                "patches": [str(explanation_path.relative_to(root))],
            },
        }
        correct_path = (
            group
            / "23_correctChoiceText_fixed"
            / "question_2026_1_correctChoiceText_fixed.json"
        )
        return question, explanation_path, correct_path

    def test_updates_only_target_patch_entries_and_keeps_source_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            group = root / "output" / "sample" / "questions_json" / "2026"
            source_path = group / "00_source" / "question_2026_1.json"
            explanation_path = group / "21_explanationText_added" / "question_2026_1_explanationText_added.json"
            source_path.parent.mkdir(parents=True)
            explanation_path.parent.mkdir(parents=True)
            source_payload = {
                "question_bodies": [
                    {
                        "original_question_id": "q1",
                        "question_url": "https://example.test/q1",
                    }
                ]
            }
            source_path.write_text(json.dumps(source_payload), encoding="utf-8")
            source_before = source_path.read_bytes()
            explanation_path.write_text(
                json.dumps(
                    [
                        {"original_question_id": "q1", "explanationText": ["正しい。旧", "間違い。旧"]},
                        {"original_question_id": "q2", "explanationText": ["正しい。他"]},
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            question = {
                "stateHash": "hash-1",
                "sourceQuestionKey": "sample:2026:q1",
                "sourceRecordRef": "question_2026_1.json#0",
                "source": {
                    "original_question_id": "q1",
                    "question_url": "https://example.test/q1",
                },
                "projected": {
                    "original_question_id": "q1",
                    "choiceTextList": ["A", "B"],
                    "correctChoiceText": ["正しい", "間違い"],
                    "explanationText": ["正しい。旧", "間違い。旧"],
                    "isLawRelated": False,
                },
                "paths": {
                    "source": str(source_path.relative_to(root)),
                    "patches": [str(explanation_path.relative_to(root))],
                },
            }
            changes = {
                "correctChoiceText": ["×", "○"],
                "explanationText": ["間違い。新", "正しい。新"],
            }
            editor = PatchEditor(root)
            preview = editor.preview(question, changes, "根拠を確認した", "hash-1")
            result = editor.apply(
                question,
                changes,
                "根拠を確認した",
                "hash-1",
                preview["previewToken"],
            )

            explanation = json.loads(explanation_path.read_text(encoding="utf-8"))
            correct_path = group / "23_correctChoiceText_fixed" / "question_2026_1_correctChoiceText_fixed.json"
            correct = json.loads(correct_path.read_text(encoding="utf-8"))
            source_after = source_path.read_bytes()

        self.assertEqual(source_before, source_after)
        self.assertEqual(explanation[0]["explanationText"], ["間違い。新", "正しい。新"])
        self.assertEqual(explanation[0]["suggestedQuestionDetailsByChoice"], [])
        self.assertNotIn("suggestedQuestions", explanation[0])
        self.assertNotIn("suggestedQuestionDetails", explanation[0])
        self.assertEqual(explanation[0]["question_url"], "https://example.test/q1")
        self.assertEqual(explanation[0]["sourceQuestionKey"], "sample:2026:q1")
        self.assertEqual(
            explanation[0]["sourceRecordRef"], "question_2026_1.json#0"
        )
        self.assertEqual(explanation[1]["explanationText"], ["正しい。他"])
        self.assertEqual(correct[0]["correctChoiceText"], ["間違い", "正しい"])
        self.assertEqual(correct[0]["sourceQuestionKey"], "sample:2026:q1")
        self.assertEqual(correct[0]["sourceRecordRef"], "question_2026_1.json#0")
        self.assertEqual(len(result["changedPaths"]), 2)

    def test_rejects_law_correctness_change(self):
        editor = PatchEditor(Path.cwd())
        question = {
            "stateHash": "hash",
            "projected": {
                "choiceTextList": ["A"],
                "correctChoiceText": ["正しい"],
                "explanationText": ["間違い。根拠"],
                "isLawRelated": True,
            },
        }
        with self.assertRaises(DirectEditError) as context:
            editor.preview(
                question,
                {"correctChoiceText": ["間違い"]},
                "根拠",
                "hash",
            )
        self.assertTrue(context.exception.codex_required)

    def test_rejects_more_than_three_saved_answers_for_one_choice(self):
        editor = PatchEditor(Path.cwd())
        question = {
            "stateHash": "hash",
            "projected": {
                "questionType": "true_false",
                "choiceTextList": ["A"],
                "correctChoiceText": ["正しい"],
                "explanationText": ["正しい。旧"],
            },
        }
        items = [
            {"question": f"質問{index}", "answer": f"回答{index}"}
            for index in range(4)
        ]

        with self.assertRaisesRegex(DirectEditError, "最大3件"):
            editor.preview(
                question,
                {
                    "suggestedQuestionDetailsByChoice": [
                        {"choiceIndex": 0, "items": items}
                    ]
                },
                "",
                "hash",
            )

    def test_multi_file_save_rolls_back_every_patch_when_second_write_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question, explanation_path, correct_path = self._two_patch_fixture(
                root
            )
            explanation_before = explanation_path.read_bytes()
            changes = {
                "correctChoiceText": ["間違い", "正しい"],
                "explanationText": ["間違い。新", "正しい。新"],
            }
            editor = PatchEditor(root)
            preview = editor.preview(
                question, changes, "根拠を確認した", "hash-1"
            )
            original_write = editor._write_patch_payload
            call_count = 0

            def fail_second_write(path, payload):
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    raise OSError("simulated second write failure")
                original_write(path, payload)

            editor._write_patch_payload = fail_second_write

            with self.assertRaisesRegex(DirectEditError, "開始前状態"):
                editor.apply(
                    question,
                    changes,
                    "根拠を確認した",
                    "hash-1",
                    preview["previewToken"],
                )
            receipt = json.loads(
                next(editor.transactions_root.glob("*/manifest.json")).read_text(
                    encoding="utf-8"
                )
            )
            explanation_after = explanation_path.read_bytes()
            correct_exists = correct_path.exists()

        self.assertEqual(explanation_after, explanation_before)
        self.assertFalse(correct_exists)
        self.assertEqual(receipt["status"], "rolled_back")

    def test_rollback_failure_blocks_further_edits_in_same_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question, _, _ = self._two_patch_fixture(root)
            changes = {"explanationText": ["正しい。新", "間違い。新"]}
            editor = PatchEditor(root)
            preview = editor.preview(question, changes, "根拠", "hash-1")

            with (
                mock.patch.object(
                    editor,
                    "_write_patch_payload",
                    side_effect=OSError("write failed"),
                ),
                mock.patch(
                    "tools.question_review_console.patch_editor.restore_write_snapshot",
                    side_effect=OSError("rollback failed"),
                ),
                self.assertRaisesRegex(DirectEditError, "復元できませんでした"),
            ):
                editor.apply(
                    question,
                    changes,
                    "根拠",
                    "hash-1",
                    preview["previewToken"],
                )

            with self.assertRaisesRegex(DirectEditError, "前回の直接編集"):
                editor.apply(
                    question,
                    changes,
                    "根拠",
                    "hash-1",
                    preview["previewToken"],
                )

        self.assertTrue(editor._recovery_errors)

    def test_does_not_write_when_transaction_baseline_cannot_be_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question, explanation_path, correct_path = self._two_patch_fixture(root)
            before = explanation_path.read_bytes()
            changes = {
                "correctChoiceText": ["間違い", "正しい"],
                "explanationText": ["間違い。新", "正しい。新"],
            }
            editor = PatchEditor(root)
            preview = editor.preview(question, changes, "根拠", "hash-1")
            with mock.patch(
                "tools.question_review_console.patch_editor.capture_write_snapshot",
                side_effect=OSError("disk full"),
            ):
                with self.assertRaisesRegex(DirectEditError, "変更していません"):
                    editor.apply(
                        question,
                        changes,
                        "根拠",
                        "hash-1",
                        preview["previewToken"],
                    )

            transaction_receipts = list(
                editor.transactions_root.glob("*/manifest.json")
            )
            after = explanation_path.read_bytes()
            correct_exists = correct_path.exists()

        self.assertEqual(after, before)
        self.assertFalse(correct_exists)
        self.assertEqual(transaction_receipts, [])

    def test_startup_recovers_an_uncommitted_direct_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "output/sample/patch.json"
            target.parent.mkdir(parents=True)
            target.write_text("before\n", encoding="utf-8")
            editor = PatchEditor(root)
            transaction_dir, _ = editor._begin_transaction(
                [target], [target.relative_to(root).as_posix()]
            )
            target.write_text("partial\n", encoding="utf-8")

            PatchEditor(root)
            receipt = json.loads(
                (transaction_dir / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            recovered_text = target.read_text(encoding="utf-8")

        self.assertEqual(recovered_text, "before\n")
        self.assertEqual(receipt["status"], "rolled_back")

    def test_preview_reports_required_field_warnings(self):
        editor = PatchEditor(Path.cwd())
        question = {
            "stateHash": "hash",
            "projected": {
                "questionBodyText": "問題文",
                "choiceTextList": ["A"],
                "correctChoiceText": ["正しい"],
                "explanationText": ["正しい。旧"],
            },
        }

        preview = editor.preview(
            question,
            {"explanationText": ["正しい。新"]},
            "",
            "hash",
        )

        self.assertEqual(
            preview["validationWarnings"],
            [{"field": "questionType", "detail": "questionTypeがありません。"}],
        )

    def test_rejects_noncanonical_explanation_only_edit(self):
        editor = PatchEditor(Path.cwd())
        question = {
            "stateHash": "hash",
            "projected": {
                "choiceTextList": ["A"],
                "correctChoiceText": ["正しい"],
                "explanationText": ["正しい。旧"],
            },
        }

        for explanation, error in (
            ("定義に一致するため正しい。", "正しい。"),
            ("正しい。A", "判断理由"),
        ):
            with self.subTest(explanation=explanation), self.assertRaisesRegex(
                DirectEditError, error
            ):
                editor.preview(
                    question,
                    {"explanationText": [explanation]},
                    "",
                    "hash",
                )

    def test_rejects_explanation_only_edit_with_opposite_verdict(self):
        editor = PatchEditor(Path.cwd())
        question = {
            "stateHash": "hash",
            "projected": {
                "choiceTextList": ["A"],
                "correctChoiceText": ["正しい"],
                "explanationText": ["正しい。旧"],
            },
        }

        with self.assertRaisesRegex(DirectEditError, "correctChoiceText"):
            editor.preview(
                question,
                {"explanationText": ["間違い。新"]},
                "",
                "hash",
            )


if __name__ == "__main__":
    unittest.main()
