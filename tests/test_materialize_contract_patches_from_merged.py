from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.fix import generate_anma_01_03_patches as legacy_anma

from scripts.pipeline.materialize_contract_patches_from_merged import (
    materialize_contract_patches,
)


def dump_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


class MaterializeContractPatchesFromMergedTests(unittest.TestCase):
    def test_legacy_anma_overwrite_does_not_replace_stage_01_from_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            questions_root = root / "output/anma/questions_json"
            year = questions_root / "2026"
            source = year / "00_source/question_2026_1.json"
            dump_json(source, {"question_bodies": [{
                "original_question_id": "q1", "questionType": "group_choice",
                "questionBodyText": "記述を選べ。", "choiceTextList": ["記述A"],
            }]})
            qtype = year / "10_questionType_fixed/question_2026_1_questionType_fixed.json"
            dump_json(qtype, [{"original_question_id": "q1", "questionType": "true_false"}])
            before_source, before_qtype = source.read_bytes(), qtype.read_bytes()
            with patch.object(legacy_anma, "ROOT_DIR", root), patch.object(legacy_anma, "QUESTIONS_ROOT", questions_root):
                legacy_anma.process_year("2026", overwrite=True)
            self.assertEqual(qtype.read_bytes(), before_qtype)
            self.assertEqual(source.read_bytes(), before_source)
            self.assertEqual(list(qtype.parent.glob("*.json")), [qtype])

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
                "questionType": "group_choice",
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
            qtype_path = root / "10_questionType_fixed" / "question_85001_1_questionType_fixed.json"
            dump_json(qtype_path, [{"original_question_id": "q1", "questionType": "true_false"}])
            before_qtype = qtype_path.read_bytes()
            dump_json(
                root / "30_merged_2" / "question_85001_1_merged_20260705_0100.json",
                {"question_bodies": [merged_question]},
            )
            dump_json(
                root / "23_correctChoiceText_fixed" / "current_correct.json",
                [{"original_question_id": "q1", "correctChoiceText": ["間違い", "間違い"]}],
            )

            outputs = materialize_contract_patches(root, "20260705_0200")

            self.assertEqual(len(outputs), 3)
            self.assertEqual(qtype_path.read_bytes(), before_qtype)
            self.assertEqual(list(qtype_path.parent.glob("*.json")), [qtype_path])
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
