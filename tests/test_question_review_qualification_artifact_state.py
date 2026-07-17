from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.question_review_console.qualification_runs import (
    QualificationRunError,
    QualificationRunStore,
)


def _plan(kind: str) -> dict[str, object]:
    return {
        "qualification": "sample",
        "stageId": "explanation",
        "stageIds": ["explanation"],
        "stageCode": "03",
        "stageLabel": "解説",
        "mode": "remaining",
        "modeLabel": "未整備のみ",
        "kind": kind,
        "workType": "maintenance_flow" if kind == "orchestration" else "maintenance",
        "targetCount": 0,
        "workItemCount": 0,
        "targetGroupIds": ["2026"],
        "policyTargets": {},
        "progressTargets": [],
        "sourceFiles": [],
        "canonicalDocs": [],
    }


def _success_result(summary: str) -> dict[str, object]:
    return {
        "status": "succeeded",
        "summary": summary,
        "commands": [{"command": "python check.py", "status": "pass"}],
        "changedFiles": [],
        "resolvedFailedDeltaPaths": [],
    }


class QualificationArtifactStateTests(unittest.TestCase):
    def test_incomplete_sync_preserves_validated_patch_state_and_groups(self) -> None:
        groups = [{"listGroupId": "2026", "status": "blocked"}]
        with tempfile.TemporaryDirectory() as directory:
            store = QualificationRunStore(Path(directory))
            run = store.create(
                _plan("human"),
                status="validating",
                prompt="work",
                append_receipt_contract=False,
            )
            store.update(
                "sample",
                run["runId"],
                receiptValidated=True,
                artifactSync={"status": "running", "groups": groups},
                result=_success_result("patch検証済み"),
                error="sync error",
            )

            completed = store.mark_validated_artifact_sync_incomplete(
                "sample",
                run["runId"],
                artifact_status="failed",
                message="手動再生成できます。",
            )

        self.assertEqual(completed["status"], "succeeded")
        self.assertTrue(completed["receiptValidated"])
        self.assertIsNone(completed["error"])
        self.assertEqual(completed["artifactSync"]["status"], "failed")
        self.assertEqual(completed["artifactSync"]["groups"], groups)

    def test_orchestration_persists_success_receipt_in_transition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            run = store.create(_plan("orchestration"), status="validating")
            store.update(
                "sample",
                run["runId"],
                receiptValidated=True,
                artifactSync={"status": "running", "groups": []},
            )

            completed = store.mark_validated_artifact_sync_incomplete(
                "sample",
                run["runId"],
                artifact_status="interrupted",
                message="同期を再実行できます。",
                result_if_missing=_success_result("子工程のpatchは検証済み"),
            )
            manifest_path = (
                root
                / "output/question_review_console/workflow_runs/sample"
                / run["runId"]
                / "manifest.json"
            )
            persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
            result_exists = (manifest_path.parent / "result.json").is_file()

        self.assertEqual(completed["result"]["status"], "succeeded")
        self.assertEqual(completed["artifactSync"]["status"], "interrupted")
        self.assertTrue(persisted["resultReceiptHash"])
        self.assertTrue(result_exists)

    def test_unvalidated_run_cannot_use_validated_sync_transition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = QualificationRunStore(Path(directory))
            run = store.create(
                _plan("human"),
                status="validating",
                prompt="work",
                append_receipt_contract=False,
            )

            with self.assertRaises(QualificationRunError):
                store.mark_validated_artifact_sync_incomplete(
                    "sample",
                    run["runId"],
                    artifact_status="failed",
                    message="同期失敗",
                )


if __name__ == "__main__":
    unittest.main()
