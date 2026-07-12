import json
import tempfile
import unittest
from pathlib import Path

from tools.question_review_console.patch_editor import DirectEditError, PatchEditor


class QuestionReviewPatchEditorTests(unittest.TestCase):
    def test_updates_only_target_patch_entries_and_keeps_source_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            group = root / "output" / "sample" / "questions_json" / "2026"
            source_path = group / "00_source" / "question_2026_1.json"
            explanation_path = group / "21_explanationText_added" / "question_2026_1_explanationText_added.json"
            source_path.parent.mkdir(parents=True)
            explanation_path.parent.mkdir(parents=True)
            source_payload = {"question_bodies": [{"original_question_id": "q1"}]}
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
                "source": {"original_question_id": "q1"},
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
        self.assertEqual(explanation[1]["explanationText"], ["正しい。他"])
        self.assertEqual(correct[0]["correctChoiceText"], ["間違い", "正しい"])
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


if __name__ == "__main__":
    unittest.main()
