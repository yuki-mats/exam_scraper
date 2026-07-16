import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.question_review_console import work_version_backfill
from tools.question_review_console.work_versions import QuestionWorkVersionStore


class FakeInventory:
    def __init__(self, questions):
        self.questions = questions

    def inventory(self):
        return {
            "qualifications": [
                {"id": "sample", "publicationId": "sample-public"}
            ]
        }

    def group(self, qualification, list_group_id):
        if qualification != "sample" or list_group_id != "2026":
            raise FileNotFoundError
        return {"questions": self.questions}


def local_question():
    return {
        "id": "question-1",
        "reviewKey": "sample:2026:question_1:original-1",
        "qualification": "sample",
        "publicationQualificationId": "sample-public",
        "listGroupId": "2026",
        "originalQuestionId": "original-1",
        "isLawRelated": True,
        "paths": {
            "source": "output/sample/questions_json/2026/00_source/question_1.json"
        },
        "uploadReadyDocs": [
            {"questionId": "document-1"},
            {"questionId": "document-2"},
        ],
        "convertedDocs": [],
    }


class WorkVersionBackfillTests(unittest.TestCase):
    def test_invalidate_run_writes_receipt_and_returns_stage_to_rework(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = local_question()
            store = QuestionWorkVersionStore(root)
            explanation_policy = {
                "id": "explanation",
                "code": "03",
                "label": "解説",
                "policyVersion": "2.0",
                "policyFingerprint": "fingerprint-2",
            }
            store.record_stage(
                [item],
                explanation_policy,
                run_id="bad-run",
                source="validated_run",
            )
            run_path = (
                root
                / "output/question_review_console/workflow_runs/sample/bad-run"
                / "manifest.json"
            )
            run_path.parent.mkdir(parents=True)
            run_path.write_text(
                json.dumps(
                    {
                        "runId": "bad-run",
                        "qualification": "sample",
                        "status": "succeeded",
                        "targetGroupIds": ["2026"],
                        "policyTargets": {"explanation": ["question-1"]},
                    }
                ),
                encoding="utf-8",
            )

            result = work_version_backfill.invalidate_work_version_run(
                root,
                qualification="sample",
                run_id="bad-run",
                stage_id="explanation",
                reason="解説品質が基準未達のため",
                execute=True,
            )
            status = QuestionWorkVersionStore(root).status_for(
                item, [explanation_policy]
            )
            receipt_path = root / result["receiptPath"]
            receipt_exists = receipt_path.is_file()
            invalidated_run = json.loads(run_path.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["invalidatedCount"], 1)
        self.assertEqual(status["status"], "outdated")
        self.assertTrue(receipt_exists)
        self.assertEqual(invalidated_run["status"], "invalidated")
        self.assertFalse(invalidated_run["receiptValidated"])
        self.assertEqual(
            invalidated_run["workVersionInvalidation"]["stageId"],
            "explanation",
        )

    def test_execute_assigns_one_legacy_record_per_published_original_question(self):
        documents = [
            {
                "documentId": "document-1",
                "qualificationId": "sample-public",
                "listGroupId": "2026",
                "originalQuestionId": "original-1",
                "isLawRelated": True,
            },
            {
                "documentId": "document-2",
                "qualificationId": "sample-public",
                "listGroupId": "2026",
                "originalQuestionId": "original-1",
                "isLawRelated": True,
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = FakeInventory([local_question()])
            with (
                patch.object(
                    work_version_backfill,
                    "QuestionInventory",
                    return_value=inventory,
                ),
                patch.object(
                    work_version_backfill,
                    "_published_qualification_ids",
                    return_value=["sample-public"],
                ),
                patch.object(
                    work_version_backfill,
                    "_stream_published_questions",
                    return_value=documents,
                ),
            ):
                result = work_version_backfill.backfill_published_work_versions(
                    root,
                    execute=True,
                    db=object(),
                )
            record = QuestionWorkVersionStore(root).record_for(local_question())

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["activeDocumentCount"], 2)
        self.assertEqual(result["publishedQuestionCount"], 1)
        self.assertEqual(result["unmatchedDocumentCount"], 0)
        self.assertEqual(result["recordedStageCount"], 8)
        self.assertEqual(set(record["stages"]), {
            "question_type",
            "question_intent",
            "correct_choice",
            "law_context",
            "explanation",
            "law_audit",
            "question_set",
            "evaluation",
        })
        self.assertTrue(
            all(stage["version"] == "0.0" for stage in record["stages"].values())
        )

    def test_unmatched_live_document_blocks_all_local_writes(self):
        documents = [
            {
                "documentId": "missing-document",
                "qualificationId": "sample-public",
                "listGroupId": "2026",
                "originalQuestionId": "missing-original",
                "isLawRelated": False,
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = FakeInventory([local_question()])
            with (
                patch.object(
                    work_version_backfill,
                    "QuestionInventory",
                    return_value=inventory,
                ),
                patch.object(
                    work_version_backfill,
                    "_published_qualification_ids",
                    return_value=["sample-public"],
                ),
                patch.object(
                    work_version_backfill,
                    "_stream_published_questions",
                    return_value=documents,
                ),
            ):
                result = work_version_backfill.backfill_published_work_versions(
                    root,
                    execute=True,
                    db=object(),
                )
            version_files = list(
                (root / "output" / "question_review_console").glob(
                    "*/*/work_versions.json"
                )
            )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["unmatchedQuestionCount"], 1)
        self.assertEqual(result["unmatchedDocumentCount"], 1)
        self.assertEqual(version_files, [])


if __name__ == "__main__":
    unittest.main()
