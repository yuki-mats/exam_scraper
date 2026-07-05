from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.merge.patch_views import apply_explanation_fields
from scripts.pipeline.materialize_law_revision_facts_from_audit import (
    materialize_law_revision_facts,
)


def dump_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


class MaterializeLawRevisionFactsFromAuditTests(unittest.TestCase):
    def test_materializes_choice_level_facts_from_audit_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "85001"
            dump_json(
                root / "20_merged_1" / "question_85001_1_merged.json",
                {
                    "question_bodies": [
                        {
                            "original_question_id": "q1",
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
                    {
                        "reviewQuestionId": "q1",
                        "auditStatus": "updated_to_current_law",
                        "examTimeDecision": "選択肢2が間違い",
                        "currentLawDecision": "選択肢2も正しい",
                        "noticeReason": "現行法では要件が変更されている。",
                        "sourceSummary": "e-Govで建築基準法第6条を確認。",
                        "remainingRisk": "出題当時条文は別途二次確認する。",
                        "reviewedAt": "2026-07-04T00:00:00+09:00",
                    },
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
