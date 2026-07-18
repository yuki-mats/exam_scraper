import json
import tempfile
import unittest
from pathlib import Path

from scripts.common.question_identity import SourceIdentityBinding
from tools.question_review_console.question_patch_proposal import (
    IsolatedQuestionPatchWorkspace,
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


class IsolatedQuestionPatchWorkspaceTests(unittest.TestCase):
    @staticmethod
    def _record(question: str, explanation: str) -> dict[str, str]:
        return {
            "sourceQuestionKey": f"sample:2026:{question}",
            "reviewQuestionId": f"review-{question}",
            "sourceRecordRef": f"source.json#{question.removeprefix('q')}",
            "explanationText": explanation,
        }

    def test_rebases_one_record_without_losing_sibling_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            patch_relative = Path(
                "output/sample/questions_json/2026/21_explanationText_added/patch.json"
            )
            patch_path = root / patch_relative
            patch_path.parent.mkdir(parents=True)
            q1 = self._record("q1", "before-1")
            q2 = self._record("q2", "before-2")
            patch_path.write_text(
                json.dumps([q1, q2], ensure_ascii=False),
                encoding="utf-8",
            )
            workspace = IsolatedQuestionPatchWorkspace.create(
                root,
                root / "output/question_review_console/run/isolated_workspace",
                qualification="sample",
                mutable_paths=[patch_relative.as_posix()],
            )
            isolated_patch = workspace.root / patch_relative
            candidate = json.loads(isolated_patch.read_text(encoding="utf-8"))
            candidate[0]["explanationText"] = "candidate-1"
            isolated_patch.write_text(
                json.dumps(candidate, ensure_ascii=False),
                encoding="utf-8",
            )

            canonical = json.loads(patch_path.read_text(encoding="utf-8"))
            canonical[1]["explanationText"] = "committed-by-sibling"
            patch_path.write_text(
                json.dumps(canonical, ensure_ascii=False),
                encoding="utf-8",
            )
            binding = SourceIdentityBinding.from_mapping(q1)
            changed = workspace.rebase_into_canonical(
                workspace.changed_paths(),
                binding=binding,
                aliases_by_path={
                    patch_relative.as_posix(): [list(binding.as_tuple())]
                },
            )
            result = json.loads(patch_path.read_text(encoding="utf-8"))

        self.assertEqual(changed, [patch_relative.as_posix()])
        self.assertEqual(result[0]["explanationText"], "candidate-1")
        self.assertEqual(result[1]["explanationText"], "committed-by-sibling")

    def test_rejects_same_record_changed_after_workspace_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            patch_relative = Path(
                "output/sample/questions_json/2026/21_explanationText_added/patch.json"
            )
            patch_path = root / patch_relative
            patch_path.parent.mkdir(parents=True)
            q1 = self._record("q1", "before")
            patch_path.write_text(json.dumps([q1]), encoding="utf-8")
            workspace = IsolatedQuestionPatchWorkspace.create(
                root,
                root / "output/question_review_console/run/isolated_workspace",
                qualification="sample",
                mutable_paths=[patch_relative.as_posix()],
            )
            isolated_patch = workspace.root / patch_relative
            candidate = json.loads(isolated_patch.read_text(encoding="utf-8"))
            candidate[0]["explanationText"] = "candidate"
            isolated_patch.write_text(json.dumps(candidate), encoding="utf-8")
            canonical = [self._record("q1", "manual-update")]
            patch_path.write_text(json.dumps(canonical), encoding="utf-8")
            binding = SourceIdentityBinding.from_mapping(q1)

            with self.assertRaisesRegex(
                QuestionPatchProposalError,
                "準備後に対象recordが更新",
            ):
                workspace.rebase_into_canonical(
                    workspace.changed_paths(),
                    binding=binding,
                    aliases_by_path={
                        patch_relative.as_posix(): [list(binding.as_tuple())]
                    },
                )


if __name__ == "__main__":
    unittest.main()
