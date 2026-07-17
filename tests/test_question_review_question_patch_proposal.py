import tempfile
import unittest
from pathlib import Path

from tools.question_review_console.question_patch_proposal import (
    QuestionPatchProposalError,
    QuestionPatchProposalStore,
)


class QuestionPatchProposalStoreTests(unittest.TestCase):
    def test_round_trips_bound_preparation_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_root = root / "output/question_review_console/workflow_runs"
            store = QuestionPatchProposalStore(root, workflow_root)
            written = store.write(
                "sample",
                "run-1",
                work_item_key="abc123",
                question_id="q1",
                stage_id="explanation",
                input_fingerprint="input-1",
                summary="修正案です。",
                thread_id="thread-1",
                session_id="session-1",
                turn_id="turn-1",
            )

            payload = store.read(
                "sample",
                "run-1",
                work_item_key="abc123",
                expected_hash=written["hash"],
                question_id="q1",
                stage_id="explanation",
                input_fingerprint="input-1",
            )

        self.assertEqual(payload["summary"], "修正案です。")
        self.assertIn("question_preparations/abc123.json", written["path"])

    def test_rejects_receipt_reused_for_another_question(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QuestionPatchProposalStore(
                root,
                root / "output/question_review_console/workflow_runs",
            )
            written = store.write(
                "sample",
                "run-1",
                work_item_key="abc123",
                question_id="q1",
                stage_id="explanation",
                input_fingerprint="input-1",
                summary="修正案です。",
                thread_id="thread-1",
                session_id="session-1",
                turn_id="turn-1",
            )

            with self.assertRaisesRegex(QuestionPatchProposalError, "一致"):
                store.read(
                    "sample",
                    "run-1",
                    work_item_key="abc123",
                    expected_hash=written["hash"],
                    question_id="q2",
                    stage_id="explanation",
                    input_fingerprint="input-1",
                )

    def test_rejects_path_segments_outside_workflow_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QuestionPatchProposalStore(
                root,
                root / "output/question_review_console/workflow_runs",
            )
            for qualification, run_id, work_item_key in (
                ("..", "run-1", "abc123"),
                ("sample", "..", "abc123"),
                ("sample", "run-1", ".."),
            ):
                with self.subTest(
                    qualification=qualification,
                    run_id=run_id,
                    work_item_key=work_item_key,
                ), self.assertRaises(QuestionPatchProposalError):
                    store.write(
                        qualification,
                        run_id,
                        work_item_key=work_item_key,
                        question_id="q1",
                        stage_id="explanation",
                        input_fingerprint="input-1",
                        summary="修正案です。",
                        thread_id="thread-1",
                        session_id="session-1",
                        turn_id="turn-1",
                    )


if __name__ == "__main__":
    unittest.main()
