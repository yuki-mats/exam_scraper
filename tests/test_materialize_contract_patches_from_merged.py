from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.pipeline.materialize_contract_patches_from_merged import (
    materialize_contract_patches,
)


def dump_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


class MaterializeContractPatchesFromMergedTests(unittest.TestCase):
    def test_materializes_contract_patch_files_from_latest_merged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "85001"
            source_question = {
                "questionBodyText": "問題文",
                "choiceTextList": ["肢1", "肢2"],
                "questionType": "true_false",
                "questionIntent": "select_correct",
                "correctChoiceText": ["正しい", "間違い"],
                "public_question_id": "q1",
                "question_url": "https://example.com/q1",
                "explanation_choice_snippets": [["根拠1"], ["根拠2"]],
            }
            merged_question = {
                **source_question,
                "original_question_id": "q1",
                "questionSetId": "set-1",
                "questionIntent": "select_incorrect",
                "explanationText": ["解説1", "解説2"],
                "isLawRelated": True,
                "lawGroundedExplanationNotNeeded": False,
                "lawReferences": [
                    [{"role": "current_basis", "scope": "choice", "choiceIndex": 99}],
                    [],
                ],
            }
            dump_json(root / "00_source" / "question_85001_1.json", {"question_bodies": [source_question]})
            dump_json(
                root / "30_merged_2" / "question_85001_1_merged_20260705_0100.json",
                {"question_bodies": [merged_question]},
            )
            dump_json(
                root / "23_correctChoiceText_fixed" / "current_correct.json",
                [{"original_question_id": "q1", "correctChoiceText": ["間違い", "間違い"]}],
            )

            outputs = materialize_contract_patches(root, "20260705_0200")

            self.assertEqual(len(outputs), 4)
            intent_patch = json.loads(
                (root / "15_correctChoiceText_fixed" / "question_85001_1_merged_correctChoiceText_fixed_20260705_0200.json").read_text(encoding="utf-8")
            )
            explanation_patch = json.loads(
                (root / "21_explanationText_added" / "question_85001_1_merged_explanationText_added_20260705_0200.json").read_text(encoding="utf-8")
            )
            self.assertTrue(intent_patch[0]["questionIntent_changed"])
            self.assertEqual(intent_patch[0]["correctChoiceText"], ["間違い", "間違い"])
            self.assertEqual(explanation_patch[0]["suggestedQuestions"][0], "この問題はどの条文から確認しますか？")
            self.assertEqual(explanation_patch[0]["isLawRelated"], True)
            self.assertEqual(explanation_patch[0]["lawGroundedExplanationNotNeeded"], False)
            self.assertEqual(explanation_patch[0]["lawReferences"][0][0]["choiceIndex"], 0)


if __name__ == "__main__":
    unittest.main()
