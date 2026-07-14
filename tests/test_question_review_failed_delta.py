import json
import tempfile
import unittest
from pathlib import Path

from tools.question_review_console.failed_delta import (
    unresolved_failed_delta_paths,
)


class FailedDeltaTests(unittest.TestCase):
    UNKNOWN_CONTRACT = {
        "allowedPatchDirs": ["21_explanationText_added"],
        "allowedWriteAreas": [],
        "allowedPatchFiles": [
            "output/sample/questions_json/2026/"
            "21_explanationText_added/q1.json"
        ],
        "allowedWriteFiles": [],
        "targetRecordScopes": {
            "output/sample/questions_json/2026/"
            "21_explanationText_added/q1.json": [["q1"]]
        },
    }

    def test_failed_path_is_blocked_until_a_later_successful_run(self):
        relative = Path(
            "output/sample/questions_json/2026/"
            "21_explanationText_added/partial.json"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
            runs = root / "output/question_review_console/workflow_runs/sample"
            self._write_manifest(runs / "20260101-run" / "manifest.json", "failed", relative)

            blocked = unresolved_failed_delta_paths(root, "sample", "2026")

            self._write_manifest(
                runs / "20260102-run" / "manifest.json", "succeeded", relative
            )
            resolved = unresolved_failed_delta_paths(root, "sample", "2026")

        self.assertEqual(blocked, (relative.as_posix(),))
        self.assertEqual(resolved, ())

    def test_other_group_failure_does_not_block_selected_group(self):
        relative = Path(
            "output/sample/questions_json/2025/"
            "21_explanationText_added/partial.json"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
            manifest = (
                root
                / "output/question_review_console/workflow_runs/sample/20260101-run/manifest.json"
            )
            self._write_manifest(manifest, "failed", relative)

            blocked = unresolved_failed_delta_paths(root, "sample", "2026")

        self.assertEqual(blocked, ())

    def test_deleted_failed_path_remains_a_tombstone(self):
        relative = Path(
            "output/sample/questions_json/2026/"
            "21_explanationText_added/deleted.json"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = (
                root
                / "output/question_review_console/workflow_runs/sample/"
                "20260101-run/manifest.json"
            )
            self._write_manifest(manifest, "failed", relative)

            blocked = unresolved_failed_delta_paths(root, "sample", "2026")

        self.assertEqual(blocked, (relative.as_posix(),))

    def test_success_can_explicitly_resolve_a_verified_unchanged_path(self):
        relative = Path(
            "output/sample/questions_json/2026/"
            "21_explanationText_added/verified.json"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
            runs = root / "output/question_review_console/workflow_runs/sample"
            self._write_manifest(
                runs / "20260101-run/manifest.json", "failed", relative
            )
            success = runs / "20260102-run/manifest.json"
            success.parent.mkdir(parents=True)
            success.write_text(
                json.dumps(
                    {
                        "qualification": "sample",
                        "status": "succeeded",
                        "workType": "maintenance",
                        "stageIds": ["explanation"],
                        "targetGroupIds": ["2026"],
                        "targetQuestionIds": ["q1"],
                        **self._contract(relative),
                        "result": {
                            "changedFiles": [],
                            "resolvedFailedDeltaPaths": [relative.as_posix()],
                        },
                    }
                ),
                encoding="utf-8",
            )

            resolved = unresolved_failed_delta_paths(root, "sample", "2026")

        self.assertEqual(resolved, ())

    def test_successful_change_without_explicit_resolution_keeps_failure_blocked(self):
        relative = Path(
            "output/sample/questions_json/2026/"
            "21_explanationText_added/aggregate.json"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = root / "output/question_review_console/workflow_runs/sample"
            self._write_manifest(
                runs / "20260101-run/manifest.json", "failed", relative
            )
            success = runs / "20260102-run/manifest.json"
            success.parent.mkdir(parents=True)
            success.write_text(
                json.dumps(
                    {
                        "qualification": "sample",
                        "status": "succeeded",
                        "workType": "maintenance",
                        "stageIds": ["explanation"],
                        "targetGroupIds": ["2026"],
                        "targetQuestionIds": ["q1"],
                        **self._contract(relative),
                        "result": {"changedFiles": [relative.as_posix()]},
                    }
                ),
                encoding="utf-8",
            )

            blocked = unresolved_failed_delta_paths(root, "sample", "2026")

        self.assertEqual(blocked, (relative.as_posix(),))

    def test_same_file_success_for_another_record_does_not_clear_failure(self):
        relative = Path(
            "output/sample/questions_json/2026/"
            "21_explanationText_added/aggregate.json"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = root / "output/question_review_console/workflow_runs/sample"
            failed = runs / "20260101-run/manifest.json"
            failed.parent.mkdir(parents=True)
            failed.write_text(
                json.dumps(
                    {
                        "qualification": "sample",
                        "status": "failed",
                        "workType": "maintenance",
                        "stageIds": ["explanation"],
                        "targetGroupIds": ["2026"],
                        "targetQuestionIds": ["q1"],
                        **self._contract(relative),
                        "result": {"changedFiles": [relative.as_posix()]},
                    }
                ),
                encoding="utf-8",
            )
            other_contract = self._contract(relative)
            other_contract["targetRecordScopes"] = {
                relative.as_posix(): [["q2"]]
            }
            success = runs / "20260102-run/manifest.json"
            success.parent.mkdir(parents=True)
            success.write_text(
                json.dumps(
                    {
                        "qualification": "sample",
                        "status": "succeeded",
                        "workType": "maintenance",
                        "stageIds": ["explanation"],
                        "targetGroupIds": ["2026"],
                        "targetQuestionIds": ["q2"],
                        **other_contract,
                        "result": {
                            "changedFiles": [relative.as_posix()],
                            "resolvedFailedDeltaPaths": [relative.as_posix()],
                        },
                    }
                ),
                encoding="utf-8",
            )

            blocked = unresolved_failed_delta_paths(root, "sample", "2026")

        self.assertEqual(blocked, (relative.as_posix(),))

    def test_qualification_document_failure_blocks_every_group(self):
        relative = Path("prompt/qualification_docs/sample/01_policy.md")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_text("partial\n", encoding="utf-8")
            manifest = (
                root
                / "output/question_review_console/workflow_runs/sample/"
                "20260101-run/manifest.json"
            )
            self._write_manifest(manifest, "failed", relative)

            blocked = unresolved_failed_delta_paths(root, "sample", "2026")

        self.assertEqual(blocked, (relative.as_posix(),))

    def test_unknown_run_resolves_from_a_different_anchor_with_same_record_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = root / "output/question_review_console/workflow_runs/sample"
            interrupted_path = runs / "20260101-run/manifest.json"
            interrupted_path.parent.mkdir(parents=True)
            interrupted_path.write_text(
                json.dumps(
                    {
                        "qualification": "sample",
                        "status": "interrupted",
                        "deltaUnknown": True,
                        "workType": "maintenance",
                        "stageIds": ["explanation"],
                        "targetGroupIds": ["2026"],
                        "targetQuestionIds": ["anchor-a"],
                        **self.UNKNOWN_CONTRACT,
                    }
                ),
                encoding="utf-8",
            )
            blocked = unresolved_failed_delta_paths(root, "sample", "2026")

            success_path = runs / "20260102-run/manifest.json"
            success_path.parent.mkdir(parents=True)
            success_path.write_text(
                json.dumps(
                    {
                        "qualification": "sample",
                        "status": "succeeded",
                        "workType": "maintenance",
                        "stageIds": ["explanation"],
                        "targetGroupIds": ["2026"],
                        "targetQuestionIds": ["anchor-b"],
                        **self.UNKNOWN_CONTRACT,
                        "result": {
                            "changedFiles": [],
                            "resolvedFailedDeltaPaths": [
                                interrupted_path.relative_to(root).as_posix()
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )
            resolved = unresolved_failed_delta_paths(root, "sample", "2026")

        self.assertEqual(
            blocked,
            (
                "output/question_review_console/workflow_runs/sample/"
                "20260101-run/manifest.json",
            ),
        )
        self.assertEqual(resolved, ())

    def test_unknown_run_is_not_cleared_without_explicit_sentinel_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = root / "output/question_review_console/workflow_runs/sample"
            interrupted = runs / "20260101-run/manifest.json"
            interrupted.parent.mkdir(parents=True)
            interrupted.write_text(
                json.dumps(
                    {
                        "qualification": "sample",
                        "status": "interrupted",
                        "deltaUnknown": True,
                        "workType": "maintenance",
                        "stageIds": ["explanation"],
                        "targetGroupIds": ["2026"],
                        "targetQuestionIds": ["q1"],
                        **self.UNKNOWN_CONTRACT,
                    }
                ),
                encoding="utf-8",
            )
            success = runs / "20260102-run/manifest.json"
            success.parent.mkdir(parents=True)
            success.write_text(
                json.dumps(
                    {
                        "qualification": "sample",
                        "status": "succeeded",
                        "workType": "maintenance",
                        "stageIds": ["explanation"],
                        "targetGroupIds": ["2026"],
                        "targetQuestionIds": ["q1"],
                        **self.UNKNOWN_CONTRACT,
                        "result": {"changedFiles": []},
                    }
                ),
                encoding="utf-8",
            )

            blocked = unresolved_failed_delta_paths(root, "sample", "2026")

        self.assertEqual(
            blocked,
            (interrupted.relative_to(root).as_posix(),),
        )

    def test_other_year_law_review_sidecar_does_not_block_selected_year(self):
        relative = Path(
            "output/sample/review/law_revision_audit/"
            "2025_law_revision_audit.jsonl"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = (
                root
                / "output/question_review_console/workflow_runs/sample/"
                "20260101-run/manifest.json"
            )
            self._write_manifest(manifest, "failed", relative)

            blocked = unresolved_failed_delta_paths(root, "sample", "2026")

        self.assertEqual(blocked, ())

    def test_unknown_run_is_not_cleared_by_a_different_write_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = root / "output/question_review_console/workflow_runs/sample"
            interrupted = runs / "20260101-run/manifest.json"
            interrupted.parent.mkdir(parents=True)
            interrupted.write_text(
                json.dumps(
                    {
                        "qualification": "sample",
                        "status": "interrupted",
                        "deltaUnknown": True,
                        "workType": "maintenance",
                        "stageIds": ["maintenance"],
                        "targetGroupIds": ["2026"],
                        "targetQuestionIds": ["q1"],
                        **self.UNKNOWN_CONTRACT,
                    }
                ),
                encoding="utf-8",
            )
            success = runs / "20260102-run/manifest.json"
            success.parent.mkdir(parents=True)
            success.write_text(
                json.dumps(
                    {
                        "qualification": "sample",
                        "status": "succeeded",
                        "workType": "maintenance",
                        "stageIds": ["maintenance"],
                        "targetGroupIds": ["2026"],
                        "targetQuestionIds": ["q1"],
                        "allowedPatchDirs": ["10_questionType_fixed"],
                        "allowedWriteAreas": [],
                        "allowedPatchFiles": [
                            "output/sample/questions_json/2026/"
                            "10_questionType_fixed/q1.json"
                        ],
                        "allowedWriteFiles": [],
                        "targetRecordScopes": {
                            "output/sample/questions_json/2026/"
                            "10_questionType_fixed/q1.json": [["q1"]]
                        },
                        "result": {"changedFiles": []},
                    }
                ),
                encoding="utf-8",
            )

            blocked = unresolved_failed_delta_paths(root, "sample", "2026")

        self.assertEqual(
            blocked,
            (
                "output/question_review_console/workflow_runs/sample/"
                "20260101-run/manifest.json",
            ),
        )

    def test_legacy_unknown_run_without_contract_stays_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = root / "output/question_review_console/workflow_runs/sample"
            interrupted = runs / "20260101-run/manifest.json"
            interrupted.parent.mkdir(parents=True)
            interrupted.write_text(
                json.dumps(
                    {
                        "qualification": "sample",
                        "status": "interrupted",
                        "deltaUnknown": True,
                        "workType": "maintenance",
                        "stageIds": ["explanation"],
                        "targetGroupIds": ["2026"],
                        "targetQuestionIds": ["q1"],
                    }
                ),
                encoding="utf-8",
            )
            success = runs / "20260102-run/manifest.json"
            success.parent.mkdir(parents=True)
            success.write_text(
                json.dumps(
                    {
                        "qualification": "sample",
                        "status": "succeeded",
                        "workType": "maintenance",
                        "stageIds": ["explanation"],
                        "targetGroupIds": ["2026"],
                        "targetQuestionIds": ["q1"],
                        **self.UNKNOWN_CONTRACT,
                        "result": {"changedFiles": []},
                    }
                ),
                encoding="utf-8",
            )

            blocked = unresolved_failed_delta_paths(root, "sample", "2026")

        self.assertEqual(len(blocked), 1)

    def test_empty_record_scope_contract_cannot_clear_an_unknown_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = root / "output/question_review_console/workflow_runs/sample"
            interrupted = runs / "20260101-run/manifest.json"
            interrupted.parent.mkdir(parents=True)
            incomplete_contract = {
                **self.UNKNOWN_CONTRACT,
                "targetRecordScopes": {},
            }
            interrupted.write_text(
                json.dumps(
                    {
                        "qualification": "sample",
                        "status": "interrupted",
                        "deltaUnknown": True,
                        "workType": "maintenance",
                        "stageIds": ["explanation"],
                        "targetGroupIds": ["2026"],
                        "targetQuestionIds": ["q1"],
                        **incomplete_contract,
                    }
                ),
                encoding="utf-8",
            )
            success = runs / "20260102-run/manifest.json"
            success.parent.mkdir(parents=True)
            success.write_text(
                json.dumps(
                    {
                        "qualification": "sample",
                        "status": "succeeded",
                        "workType": "maintenance",
                        "stageIds": ["explanation"],
                        "targetGroupIds": ["2026"],
                        "targetQuestionIds": ["q1"],
                        **incomplete_contract,
                        "result": {
                            "changedFiles": [],
                            "resolvedFailedDeltaPaths": [
                                interrupted.relative_to(root).as_posix()
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )

            blocked = unresolved_failed_delta_paths(root, "sample", "2026")

        self.assertEqual(blocked, (interrupted.relative_to(root).as_posix(),))

    def test_empty_alias_groups_cannot_clear_a_failed_record_path(self):
        relative = Path(
            "output/sample/questions_json/2026/"
            "21_explanationText_added/aggregate.json"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = root / "output/question_review_console/workflow_runs/sample"
            failed_contract = {
                **self._contract(relative),
                "targetRecordScopes": {relative.as_posix(): []},
            }
            failed = runs / "20260101-run/manifest.json"
            failed.parent.mkdir(parents=True)
            failed.write_text(
                json.dumps(
                    {
                        "qualification": "sample",
                        "status": "failed",
                        "workType": "maintenance",
                        "stageIds": ["explanation"],
                        "targetGroupIds": ["2026"],
                        "targetQuestionIds": ["q1"],
                        **failed_contract,
                        "result": {"changedFiles": [relative.as_posix()]},
                    }
                ),
                encoding="utf-8",
            )
            success_contract = self._contract(relative)
            success = runs / "20260102-run/manifest.json"
            success.parent.mkdir(parents=True)
            success.write_text(
                json.dumps(
                    {
                        "qualification": "sample",
                        "status": "succeeded",
                        "workType": "maintenance",
                        "stageIds": ["explanation"],
                        "targetGroupIds": ["2026"],
                        "targetQuestionIds": ["q1"],
                        **success_contract,
                        "result": {
                            "changedFiles": [relative.as_posix()],
                            "resolvedFailedDeltaPaths": [relative.as_posix()],
                        },
                    }
                ),
                encoding="utf-8",
            )

            blocked = unresolved_failed_delta_paths(root, "sample", "2026")

        self.assertEqual(blocked, (relative.as_posix(),))

    @staticmethod
    def _write_manifest(path: Path, status: str, changed_path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "qualification": "sample",
                    "status": status,
                    "workType": "maintenance",
                    "stageIds": ["explanation"],
                    "targetGroupIds": [
                        changed_path.parts[3]
                        if len(changed_path.parts) >= 4
                        and changed_path.parts[2] == "questions_json"
                        else "2026"
                    ],
                    "targetQuestionIds": ["q1"],
                    **FailedDeltaTests._contract(changed_path),
                    "result": {
                        "changedFiles": [changed_path.as_posix()],
                        "resolvedFailedDeltaPaths": (
                            [changed_path.as_posix()]
                            if status == "succeeded"
                            else []
                        ),
                    },
                }
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _contract(path: Path) -> dict[str, object]:
        parts = path.parts
        patch_dir = parts[4] if len(parts) > 4 and parts[2] == "questions_json" else ""
        is_patch = bool(patch_dir)
        return {
            "allowedPatchDirs": [patch_dir] if is_patch else [],
            "allowedWriteAreas": [] if is_patch else ["review"],
            "allowedPatchFiles": [path.as_posix()] if is_patch else [],
            "allowedWriteFiles": [] if is_patch else [path.as_posix()],
            "targetRecordScopes": {path.as_posix(): [["q1"]]},
        }


if __name__ == "__main__":
    unittest.main()
