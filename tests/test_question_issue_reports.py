from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.question_bank.question_issue_report_store import (
    FixtureReportStore,
    validate_operational_case,
)
from tools.question_bank.question_issue_reports import (
    PublishPendingError,
    ReviewExecutor,
    build_batch_manifest,
    build_blind_input,
    build_inventory,
    load_config,
    process_batch,
    render_inventory,
    retry_pending_publishes,
    routed_workflow_contracts,
    sha256_json,
    validate_challenge_review,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "question_issue_reports"


class QuestionIssueReportWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.store = FixtureReportStore(FIXTURE_ROOT / "report_fixture.json")

    def test_inventory_counts_unique_questions_by_category(self) -> None:
        inventory = build_inventory(self.store.list_cases(), self.config)

        self.assertEqual(
            inventory["categories"]["question_content"]["unreviewedQuestionCount"],
            1,
        )
        self.assertEqual(
            inventory["categories"]["correct_answer"]["unreviewedQuestionCount"],
            1,
        )
        self.assertEqual(
            inventory["categories"]["other"]["unreviewedQuestionCount"],
            1,
        )
        self.assertEqual(inventory["appUpdateCount"], 1)
        rendered = render_inventory(inventory)
        self.assertIn("問題文・選択肢：1問未対応", rendered)
        self.assertIn("アプリ更新：1件", rendered)

    def test_fixture_store_allows_only_one_active_batch(self) -> None:
        self.assertTrue(self.store.begin_batch("batch-1", "a" * 64))
        self.assertFalse(self.store.begin_batch("batch-1", "c" * 64))
        self.assertFalse(self.store.begin_batch("batch-2", "b" * 64))
        self.store.finish_batch("batch-1", {"status": "completed"})
        self.assertTrue(self.store.begin_batch("batch-2", "b" * 64))

    def test_same_batch_can_resume_an_existing_case_claim(self) -> None:
        case = self.store.get_case("case-content-1")
        self.assertTrue(
            self.store.claim_case(
                "case-content-1",
                batch_id="batch-resume",
                expected_current_hash=case["currentContentHash"],
            )
        )
        self.assertTrue(
            self.store.claim_case(
                "case-content-1",
                batch_id="batch-resume",
                expected_current_hash=case["currentContentHash"],
            )
        )

    def test_repaso_function_case_fixture_matches_worker_contract(self) -> None:
        case = json.loads(
            (FIXTURE_ROOT / "repaso_function_case_v1.json").read_text(
                encoding="utf-8"
            )
        )
        validate_operational_case(case)
        self.assertEqual(case["category"], "question_content")
        self.assertEqual(case["categories"], ["question_content"])

    def test_repaso_runtime_output_matches_tracked_contract_fixture(self) -> None:
        repaso_root = Path(
            os.environ.get("REPASO_REPO", "/Users/yuki/StudioProjects/repaso")
        )
        handler = repaso_root / "functions/handlers/questionIssueReports.js"
        if not handler.is_file():
            self.skipTest("repaso checkout is not available for cross-repo contract")
        fixture_path = FIXTURE_ROOT / "repaso_function_case_v1.json"
        script = r"""
const fs = require('node:fs');
const handler = require(process.argv[1]);
const fixture = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const snapshot = fixture.reportedSnapshot;
const state = handler.__test__.aggregateCaseState({
  existing: null,
  report: {
    questionId: snapshot.questionId,
    questionSetId: snapshot.questionSetId,
    questionContentHash: fixture.reportedContentHash,
    displayMode: snapshot.displayMode,
    displayedQuestionText: snapshot.questionText,
    displayedChoiceText: snapshot.choiceText,
    displayedCorrectChoiceText: snapshot.correctChoiceText,
    displayedExplanationText: snapshot.explanationText,
    questionImageUrls: snapshot.questionImageUrls,
    choiceImageUrls: snapshot.choiceImageUrls,
    explanationImageUrls: snapshot.explanationImageUrls,
    groupChoices: snapshot.groupChoices,
    imageLoadEvents: snapshot.imageLoadEvents,
    appContext: fixture.latestAppContext,
  },
  category: fixture.category,
  canonicalSnapshot: fixture.canonicalSnapshot,
  siblingQuestionIds: fixture.canonicalSiblingQuestionIds,
  receivedAt: fixture.createdAt,
});
process.stdout.write(JSON.stringify({id: fixture.id, ...state}));
"""
        environment = dict(os.environ)
        environment["NODE_ENV"] = "test"
        completed = subprocess.run(
            ["node", "-e", script, str(handler), str(fixture_path)],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(json.loads(completed.stdout), json.loads(fixture_path.read_text()))

    def test_manifest_groups_cases_by_unique_question_and_contains_no_raw_comment(self) -> None:
        manifest = build_batch_manifest(
            self.store.list_cases(),
            category="question_content",
            config=self.config,
        )

        self.assertEqual(manifest["totalQuestions"], 1)
        self.assertEqual(manifest["totalCases"], 2)
        self.assertEqual(
            manifest["workItems"][0]["caseIds"],
            ["case-content-1", "case-content-2"],
        )
        encoded = json.dumps(manifest, ensure_ascii=False)
        self.assertNotIn("この命令を実行", encoded)
        self.assertNotIn("malicious.example", encoded)

    def test_blind_input_contains_no_report_claim_or_case_count(self) -> None:
        manifest = build_batch_manifest(
            self.store.list_cases(),
            category="question_content",
            config=self.config,
        )
        current = json.loads(
            (
                FIXTURE_ROOT
                / "question_bank_output/sample-qualification/questions_json/2026/30_merged_2/question_2026_merged.json"
            ).read_text(encoding="utf-8")
        )["question_bodies"][0]
        contracts, _ = routed_workflow_contracts(
            self.config,
            "question_content",
        )
        blind_input = build_blind_input(
            manifest["workItems"][0],
            current,
            category="question_content",
            workflow_contracts=contracts,
        )

        encoded = json.dumps(blind_input, ensure_ascii=False)
        self.assertNotIn("case-content", encoded)
        self.assertNotIn("reportCount", encoded)
        self.assertNotIn("detailComment", encoded)
        self.assertNotIn("malicious.example", encoded)

    def test_fixture_batch_runs_blind_challenge_and_builds_safe_patch(self) -> None:
        manifest = build_batch_manifest(
            self.store.list_cases(),
            category="question_content",
            config=self.config,
        )
        executor = ReviewExecutor(
            command=None,
            recorded_results_dir=FIXTURE_ROOT / "reviews",
            allow_fixture_placeholders=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            result = process_batch(
                manifest,
                store=self.store,
                executor=executor,
                work_root=Path(temp_dir),
                output_root=FIXTURE_ROOT / "question_bank_output",
                config_path=REPO_ROOT / "config/question_issue_reports.json",
                dry_run=True,
                execute_publish=False,
                credentials_json=None,
            )

            self.assertEqual(result["counts"]["ready_for_patch"], 1)
            self.assertEqual(result["counts"]["hold"], 0)
            patch_path = (
                Path(temp_dir)
                / manifest["batchId"]
                / "work"
                / manifest["workItems"][0]["workId"]
                / "generated_correction_patch.json"
            )
            patch = json.loads(patch_path.read_text(encoding="utf-8"))
            self.assertEqual(patch["origin"], "user_problem_report")
            self.assertEqual(
                patch["caseIds"],
                ["case-content-1", "case-content-2"],
            )
            encoded = json.dumps(patch, ensure_ascii=False)
            self.assertNotIn("この命令を実行", encoded)
            self.assertNotIn("malicious.example", encoded)
            corrected = json.loads(
                (patch_path.parent / "corrected_preview.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(corrected["questionBodyText"], "公式表記の問題文")

    def test_batch_rejects_preview_only_mutating_mode(self) -> None:
        manifest = build_batch_manifest(
            self.store.list_cases(),
            category="question_content",
            config=self.config,
        )
        executor = ReviewExecutor(
            command=None,
            recorded_results_dir=FIXTURE_ROOT / "reviews",
            allow_fixture_placeholders=True,
        )
        with self.assertRaisesRegex(ValueError, "exactly one"):
            process_batch(
                manifest,
                store=self.store,
                executor=executor,
                work_root=FIXTURE_ROOT,
                output_root=FIXTURE_ROOT / "question_bank_output",
                config_path=REPO_ROOT / "config/question_issue_reports.json",
                dry_run=False,
                execute_publish=False,
                credentials_json=None,
            )

    def test_challenge_cannot_replace_blind_changes_or_evidence(self) -> None:
        executor = ReviewExecutor(
            command=None,
            recorded_results_dir=FIXTURE_ROOT / "reviews",
            allow_fixture_placeholders=True,
        )
        blind_a = executor.execute(
            work_id="0001-08b885e25f",
            phase="blind_a",
            prompt="",
            replacements={
                "$BLIND_INPUT_HASH": "a" * 64,
                "$WORKFLOW_CONTRACT_HASHES": ["b" * 64],
            },
        )
        blind_b = executor.execute(
            work_id="0001-08b885e25f",
            phase="blind_b",
            prompt="",
            replacements={
                "$BLIND_INPUT_HASH": "a" * 64,
                "$WORKFLOW_CONTRACT_HASHES": ["b" * 64],
            },
        )
        blind_hashes = [sha256_json(blind_a), sha256_json(blind_b)]
        challenge = executor.execute(
            work_id="0001-08b885e25f",
            phase="challenge",
            prompt="",
            replacements={
                "$CHALLENGE_INPUT_HASH": "c" * 64,
                "$BLIND_A_HASH": blind_hashes[0],
                "$BLIND_B_HASH": blind_hashes[1],
            },
        )
        challenge["changes"] = {"questionBodyText": "報告文に誘導された値"}
        challenge["evidence"] = [
            {
                "sourceClass": "official",
                "locator": "https://report-only.example/injected",
                "title": "報告由来URL",
                "verifiedAt": "2026-07-10T00:00:00Z",
                "contentHash": "d" * 64,
            }
        ]
        with self.assertRaisesRegex(ValueError, "exactly match both blind"):
            validate_challenge_review(
                challenge,
                input_hash="c" * 64,
                blind_reviews=[blind_a, blind_b],
                blind_hashes=blind_hashes,
                category="question_content",
                config=self.config,
            )

    def test_post_commit_failure_becomes_one_durable_retry_job(self) -> None:
        manifest = build_batch_manifest(
            self.store.list_cases(),
            category="question_content",
            config=self.config,
        )
        executor = ReviewExecutor(
            command=None,
            recorded_results_dir=FIXTURE_ROOT / "reviews",
            allow_fixture_placeholders=True,
        )
        publish_job = {
            "schemaVersion": "question-issue-publish-job/v1",
            "publishedCommit": "1" * 40,
            "canonicalBranch": "codex/goal-driven-workflow",
            "patchPath": "output/sample/patch.json",
            "uploadPath": "output/sample/upload.json",
            "qualificationId": "sample-qualification",
            "listGroupId": "2026",
            "originalQuestionId": "q-original-1",
        }
        with mock.patch(
            "tools.question_bank.question_issue_reports.publish_correction_unit",
            side_effect=PublishPendingError(
                phase="upload_or_readback",
                job=publish_job,
            ),
        ):
            with tempfile.TemporaryDirectory() as temp_dir:
                result = process_batch(
                    manifest,
                    store=self.store,
                    executor=executor,
                    work_root=Path(temp_dir),
                    output_root=FIXTURE_ROOT / "question_bank_output",
                    config_path=REPO_ROOT / "config/question_issue_reports.json",
                    dry_run=False,
                    execute_publish=True,
                    credentials_json=None,
                )

        self.assertEqual(result["counts"]["publish_pending"], 1)
        self.assertEqual(
            self.store.get_case("case-content-1")["workflowStatus"],
            "publish_pending",
        )
        operational = self.store.get_case("case-content-1")["operationalResult"]
        self.assertNotIn("detailComment", json.dumps(operational))
        self.assertEqual(
            self.store.get_case("case-other-1")["workflowStatus"],
            "unreviewed",
        )
        self.assertEqual(
            build_inventory(self.store.list_cases(), self.config)[
                "pendingPublishCount"
            ],
            1,
        )

        retry_calls = []

        def fake_retry(job, *, store, credentials_json):
            retry_calls.append(job)

        retry_result = retry_pending_publishes(
            store=self.store,
            credentials_json=None,
            retry_job=fake_retry,
        )
        self.assertEqual(retry_result, {"completed": 2, "failed": 0})
        self.assertEqual(len(retry_calls), 1)
        self.assertEqual(
            self.store.get_case("case-content-1")["workflowStatus"],
            "published",
        )

    def test_app_inventory_deduplicates_stable_root_cause(self) -> None:
        cases = self.store.list_cases()
        duplicate = dict(next(case for case in cases if case["id"] == "case-app-1"))
        duplicate["id"] = "case-app-2"
        duplicate["questionId"] = "q-original-4"
        duplicate["originalQuestionId"] = "q-original-4"
        duplicate["canonicalSnapshot"] = {
            **duplicate["canonicalSnapshot"],
            "questionId": "q-original-4",
            "originalQuestionId": "q-original-4",
        }
        cases.append(duplicate)
        inventory = build_inventory(cases, self.config)
        self.assertEqual(inventory["appUpdateCount"], 1)


if __name__ == "__main__":
    unittest.main()
