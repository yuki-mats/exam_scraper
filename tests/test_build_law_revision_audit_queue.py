from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.pipeline.build_law_revision_audit_queue import build_queue


def dump_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def dump_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False))
            fh.write("\n")


class BuildLawRevisionAuditQueueTests(unittest.TestCase):
    def test_builds_queue_with_snapshot_evidence_for_missing_facts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "85001"
            firestore_path = root / "40_convert" / "85001_firestore_20260705_120000.json"
            dump_json(
                firestore_path,
                {
                    "questions": [
                        {
                            "questionId": "q1_1",
                            "originalQuestionId": "q1",
                            "listGroupId": "85001",
                            "qualificationId": "2nd-class-kenchikushi",
                            "examYear": 2015,
                            "questionBodyText": "法令問題",
                            "questionText": "選択肢1",
                            "correctChoiceText": "間違い",
                            "explanationText": "現行法の説明",
                            "isLawRelated": True,
                            "lawReferences": [
                                {
                                    "role": "current_basis",
                                    "lawId": "325AC0000000201",
                                    "lawTitle": "建築基準法",
                                    "article": "6条",
                                    "paragraph": "1項",
                                    "verificationStatus": "verified",
                                }
                            ],
                        },
                        {
                            "questionId": "q2_1",
                            "isLawRelated": False,
                        },
                    ]
                },
            )
            snapshots_path = Path(tmp) / "snapshots.jsonl"
            dump_jsonl(
                snapshots_path,
                [
                    {
                        "status": "fetched",
                        "lawId": "325AC0000000201",
                        "lawTitle": "建築基準法",
                        "article": "6条",
                        "paragraph": "1項",
                        "referenceDate": "2026-07-05",
                        "apiUrl": "https://laws.e-gov.go.jp/api/1/articles;lawId=325AC0000000201;article=6",
                        "articleText": "第六条 建築主は確認済証の交付を受けなければならない。",
                        "articleTextHash": "hash-a",
                        "rawXmlHash": "hash-x",
                        "rawXmlPath": "raw.xml",
                        "questionIds": ["q1_1"],
                    }
                ],
            )
            output_path = Path(tmp) / "queue.jsonl"
            summary_path = Path(tmp) / "summary.json"

            queued, counts = build_queue(
                list_group_dir=root,
                snapshots_path=snapshots_path,
                output_path=output_path,
                summary_path=summary_path,
                include_existing=False,
                include_hold=False,
                require_snapshots=True,
                snippet_chars=20,
            )

            self.assertEqual(queued, 1)
            self.assertEqual(counts["missing_lawRevisionFacts"], 1)
            record = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["schemaVersion"], "law-revision-audit-queue/v1")
            self.assertEqual(record["auditReason"], "missing_lawRevisionFacts")
            self.assertEqual(record["questionId"], "q1_1")
            ref = record["currentEvidence"]["refs"][0]
            self.assertEqual(ref["matchLevel"], "exact")
            self.assertEqual(ref["snapshot"]["articleTextHash"], "hash-a")
            self.assertEqual(ref["snapshot"]["rawXmlHash"], "hash-x")
            self.assertIn("第六条", ref["snapshot"]["articleTextSnippet"])
            self.assertTrue(summary_path.exists())

    def test_require_snapshots_fails_on_missing_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "85001"
            dump_json(
                root / "40_convert" / "85001_firestore_20260705_120000.json",
                {
                    "questions": [
                        {
                            "questionId": "q1_1",
                            "isLawRelated": True,
                            "lawReferences": [
                                {
                                    "lawId": "325AC0000000201",
                                    "article": "6条",
                                }
                            ],
                        }
                    ]
                },
            )
            snapshots_path = Path(tmp) / "snapshots.jsonl"
            dump_jsonl(snapshots_path, [])

            with self.assertRaisesRegex(RuntimeError, "missing or ambiguous snapshots"):
                build_queue(
                    list_group_dir=root,
                    snapshots_path=snapshots_path,
                    output_path=Path(tmp) / "queue.jsonl",
                    summary_path=None,
                    include_existing=False,
                    include_hold=False,
                    require_snapshots=True,
                    snippet_chars=20,
                )

    def test_uses_same_original_question_refs_as_explicit_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "85001"
            dump_json(
                root / "40_convert" / "85001_firestore_20260705_120000.json",
                {
                    "questions": [
                        {
                            "questionId": "q1",
                            "originalQuestionId": "oq1",
                            "isLawRelated": True,
                            "lawReferences": [
                                {
                                    "lawId": "325AC0000000201",
                                    "lawTitle": "建築基準法",
                                    "article": "52条",
                                    "paragraph": "2項",
                                }
                            ],
                            "lawRevisionFacts": {"auditStatus": "same_as_current"},
                        },
                        {
                            "questionId": "q1_w1",
                            "originalQuestionId": "oq1",
                            "isLawRelated": True,
                            "lawReferences": [],
                        },
                    ]
                },
            )
            snapshots_path = Path(tmp) / "snapshots.jsonl"
            dump_jsonl(
                snapshots_path,
                [
                    {
                        "status": "fetched",
                        "lawId": "325AC0000000201",
                        "lawTitle": "建築基準法",
                        "article": "52条",
                        "paragraph": "2項",
                        "articleText": "第五十二条 容積率",
                        "articleTextHash": "hash-52",
                    }
                ],
            )
            output_path = Path(tmp) / "queue.jsonl"

            queued, counts = build_queue(
                list_group_dir=root,
                snapshots_path=snapshots_path,
                output_path=output_path,
                summary_path=None,
                include_existing=False,
                include_hold=False,
                require_snapshots=True,
                snippet_chars=20,
            )

            self.assertEqual(queued, 1)
            self.assertEqual(counts["records_with_no_law_references"], 1)
            self.assertEqual(counts["records_using_group_law_reference_fallback"], 1)
            record = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["lawReferencesSource"], "same_original_question_fallback")
            self.assertEqual(record["currentEvidence"]["refs"][0]["matchLevel"], "exact")


if __name__ == "__main__":
    unittest.main()
