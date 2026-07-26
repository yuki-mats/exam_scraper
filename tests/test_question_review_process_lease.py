from __future__ import annotations

import multiprocessing
import tempfile
import unittest
from pathlib import Path

from tools.question_review_console.process_lease import (
    ProcessLeaseError,
    review_console_process_lease,
)
from tools.question_review_console.qualification_runs import (
    QualificationRunStore,
)


def _try_lease(path: str, result: multiprocessing.Queue) -> None:
    lease = review_console_process_lease(Path(path))
    try:
        lease.acquire()
    except ProcessLeaseError:
        result.put("blocked")
    else:
        result.put("acquired")
        lease.close()


class QuestionReviewProcessLeaseTest(unittest.TestCase):
    def test_second_process_cannot_acquire_ui_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with review_console_process_lease(root):
                context = multiprocessing.get_context("spawn")
                result = context.Queue()
                process = context.Process(
                    target=_try_lease,
                    args=(str(root), result),
                )
                process.start()
                process.join(timeout=10)
                self.assertEqual(process.exitcode, 0)
                self.assertEqual(result.get(timeout=2), "blocked")

    def test_store_construction_does_not_recover_or_rewrite_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            plan = {
                "qualification": "sample",
                "stageId": "stage",
                "stageCode": "10",
                "stageLabel": "工程",
                "mode": "remaining",
                "modeLabel": "未整備",
                "kind": "human",
                "targetCount": 1,
                "progressTargets": [],
            }
            run = store.create(plan, status="running", prompt="test")
            path = (
                root
                / "output/question_review_console/workflow_runs/sample"
                / run["runId"]
                / "manifest.json"
            )
            before = path.read_bytes()

            QualificationRunStore(root)

            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(
                QualificationRunStore(root).get("sample", run["runId"])[
                    "status"
                ],
                "running",
            )


if __name__ == "__main__":
    unittest.main()
