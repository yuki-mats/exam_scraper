import http.client
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from tools.question_review_console.jobs import JobConflictError
from tools.question_review_console.server import (
    ApiError,
    QuestionReviewApplication,
    QuestionReviewRequestHandler,
    build_tailscale_access,
)


class QuestionReviewServerTests(unittest.TestCase):
    def test_publication_preview_uses_only_explicit_ids_in_normalized_order(self):
        class Inventory:
            def question(self, question_id):
                return {
                    "id": question_id,
                    "qualification": "sample",
                    "listGroupId": "2026",
                }

        class Publisher:
            def preview_many(self, questions):
                self.question_ids = [question["id"] for question in questions]
                return {
                    "questionIds": self.question_ids,
                    "selectedCount": len(self.question_ids),
                    "canStart": True,
                    "previewToken": "queue-token",
                    "items": [],
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            publisher = Publisher()
            app.inventory = Inventory()
            app.question_publisher = publisher

            status, preview = app.post(
                "/api/publications/preview",
                {"questionIds": ["q2", "q1", "q2"]},
            )

        self.assertEqual(status, 200)
        self.assertEqual(publisher.question_ids, ["q2", "q1"])
        self.assertEqual(preview["questionIds"], ["q2", "q1"])

    def test_publication_preview_rejects_more_than_one_hundred_questions(self):
        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            with self.assertRaises(ApiError) as caught:
                app.post(
                    "/api/publications/preview",
                    {"questionIds": [f"q{index}" for index in range(101)]},
                )

        self.assertEqual(caught.exception.status, 400)
        self.assertIn("最大100問", str(caught.exception))

    def test_publication_start_rechecks_token_and_runs_one_repository_job(self):
        class Inventory:
            def question(self, question_id):
                return {
                    "id": question_id,
                    "qualification": "sample",
                    "listGroupId": "2026",
                }

        class Publisher:
            def preview_many(self, questions):
                self.preview_ids = [question["id"] for question in questions]
                return {
                    "questionIds": self.preview_ids,
                    "selectedCount": len(self.preview_ids),
                    "canStart": True,
                    "previewToken": "queue-token",
                    "items": [
                        {
                            "questionId": question_id,
                            "canPublish": True,
                            "preflightToken": f"item-{question_id}",
                        }
                        for question_id in self.preview_ids
                    ],
                }

            @staticmethod
            def queue_token_matches(preview, token):
                return preview["previewToken"] == token

            def run_many(self, questions, preflight, emit, *, on_success):
                self.run_ids = [question["id"] for question in questions]
                self.run_preflight = preflight
                self.on_success = on_success
                emit("直列公開")
                return {
                    "status": "all_succeeded",
                    "succeededCount": len(questions),
                    "failedCount": 0,
                    "items": [],
                }

        class DeferredJobs:
            def start(self, *, kind, key, worker):
                self.kind = kind
                self.key = key
                self.worker = worker
                return {"jobId": "job-1", "status": "queued"}

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            publisher = Publisher()
            jobs = DeferredJobs()
            app.inventory = Inventory()
            app.question_publisher = publisher
            app.jobs = jobs

            with self.assertRaises(ApiError) as unconfirmed:
                app.post(
                    "/api/publications/start",
                    {
                        "questionIds": ["q2", "q1"],
                        "previewToken": "queue-token",
                    },
                )
            with self.assertRaises(ApiError) as changed:
                app.post(
                    "/api/publications/start",
                    {
                        "questionIds": ["q2", "q1"],
                        "previewToken": "changed-token",
                        "confirmedProduction": True,
                    },
                )
            status, job = app.post(
                "/api/publications/start",
                {
                    "questionIds": ["q2", "q1"],
                    "previewToken": "queue-token",
                    "confirmedProduction": True,
                },
            )
            result = jobs.worker(lambda _message: None)

        self.assertEqual(unconfirmed.exception.status, 422)
        self.assertIn("確認が必要", str(unconfirmed.exception))
        self.assertEqual(changed.exception.status, 422)
        self.assertIn("更新されました", str(changed.exception))
        self.assertEqual(status, 202)
        self.assertEqual(job["jobId"], "job-1")
        self.assertEqual(jobs.kind, "question-publish-queue")
        self.assertEqual(jobs.key, "question-review-repository-operation")
        self.assertEqual(publisher.preview_ids, ["q2", "q1"])
        self.assertEqual(publisher.run_ids, ["q2", "q1"])
        self.assertEqual(
            publisher.run_preflight["questionIds"],
            ["q2", "q1"],
        )
        self.assertTrue(callable(publisher.on_success))
        self.assertEqual(result["status"], "all_succeeded")

    def test_continuous_evaluation_queue_collects_every_page(self):
        app = object.__new__(QuestionReviewApplication)
        offsets = []
        pages = {
            0: {
                "questions": [{"id": f"q{index}"} for index in range(100)],
                "hasMore": True,
            },
            100: {
                "questions": [
                    {"id": f"q{index}"} for index in range(100, 135)
                ],
                "hasMore": False,
            },
        }

        def questions(query):
            offset = int(query["offset"][0])
            offsets.append(offset)
            self.assertEqual(query["evaluationStatus"], ["unreviewed"])
            self.assertEqual(query["sort"], ["updated_asc"])
            return pages[offset]

        app._questions = questions
        app._question = lambda question_id, _query: {"id": question_id}

        result = app._evaluation_queue_questions("sample", "__all__")

        self.assertEqual(offsets, [0, 100])
        self.assertEqual(len(result), 135)
        self.assertEqual(result[0]["id"], "q0")
        self.assertEqual(result[-1]["id"], "q134")

    def test_official_source_correction_starts_from_ui_request(self):
        class AppServer:
            provider = "fake"
            configured = True

            def assert_subscription_access(self, *, force):
                self.force = force

        class Inventory:
            def question(self, question_id):
                return {
                    "id": question_id,
                    "qualification": "sample",
                    "listGroupId": "2026",
                    "originalQuestionId": "q1",
                    "stateHash": "a" * 64,
                }

        class DeferredJobs:
            def start(self, *, kind, key, worker):
                self.kind = kind
                self.key = key
                self.worker = worker
                return {"jobId": "job-1", "status": "queued"}

        class CorrectionService:
            def run(self, question, **options):
                self.question = question
                self.options = options
                return {
                    "decision": "no_change",
                    "patchPath": None,
                    "message": "変更不要",
                }

        with tempfile.TemporaryDirectory() as directory:
            app_server = AppServer()
            app = QuestionReviewApplication(
                Path(directory),
                app_server=app_server,
            )
            app.inventory = Inventory()
            jobs = DeferredJobs()
            app.jobs = jobs
            service = CorrectionService()
            app.official_source_corrections = service

            status, job = app.post(
                "/api/official-source-corrections/start",
                {
                    "questionId": "ui-q1",
                    "stateHash": "a" * 64,
                    "category": "correct_answer",
                    "evidencePath": "tmp/official.png",
                    "evidenceTitle": "公式問題冊子",
                    "evidenceLocator": "問1",
                    "verifiedTranscription": "公式転記",
                    "resumeWorkDirectory": "output/question_issue_reports/ui_official_source/ui-qir-fixed/official-q1",
                },
            )
            result = jobs.worker(lambda _message: None)

        self.assertEqual(status, 202)
        self.assertEqual(job["jobId"], "job-1")
        self.assertEqual(jobs.kind, "official-source-correction")
        self.assertEqual(jobs.key, "question-review-qualification:sample")
        self.assertFalse(app_server.force)
        self.assertEqual(service.question["id"], "ui-q1")
        self.assertEqual(service.options["category"], "correct_answer")
        self.assertEqual(service.options["evidence_title"], "公式問題冊子")
        self.assertEqual(
            service.options["resume_work_directory"],
            "output/question_issue_reports/ui_official_source/ui-qir-fixed/official-q1",
        )
        self.assertEqual(result["decision"], "no_change")

    def test_question_lookup_reuses_loaded_inventory_snapshot(self):
        class Inventory:
            def question(self, question_id):
                return {
                    "id": question_id,
                    "qualification": "sample",
                    "listGroupId": "2026",
                }

            def group(self, _qualification, _list_group_id):
                raise AssertionError("loaded question must not rescan group")

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.inventory = Inventory()

            question = app._question("target-question", {})

        self.assertEqual(question["id"], "target-question")

    def test_question_lookup_skips_unrelated_invalid_group(self):
        class Inventory:
            def __init__(self):
                self.loaded = False

            def question(self, question_id):
                if self.loaded and question_id == "target-question":
                    return {
                        "id": question_id,
                        "qualification": "valid",
                        "listGroupId": "2026",
                    }
                raise KeyError(question_id)

            def inventory(self):
                return {
                    "qualifications": [
                        {"id": "broken", "listGroupIds": ["empty"]},
                        {"id": "valid", "listGroupIds": ["2026"]},
                    ]
                }

            def group(self, qualification, list_group_id):
                if qualification == "broken":
                    raise ValueError("source records not found")
                self.loaded = True
                return {"questions": []}

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.inventory = Inventory()

            question = app._question("target-question", {})

        self.assertEqual(question["qualification"], "valid")

    def test_manual_patch_sync_forces_regeneration_even_when_current(self):
        class Synchronizer:
            def __init__(self):
                self.preview_forces = []
                self.run_forces = []

            def preview(self, qualification, list_group_id, *, force=False):
                self.preview_forces.append(force)
                return {
                    "previewToken": "token",
                    "canSync": True,
                    "failedDeltaPaths": [],
                }

            def run(
                self, qualification, list_group_id, token, emit, *, force=False
            ):
                self.run_forces.append(force)
                return {"message": "再生成しました。"}

        class DeferredJobs:
            def start(self, *, kind, key, worker):
                self.worker = worker
                return {"jobId": "job-1", "status": "queued"}

        class Inventory:
            def group(self, qualification, list_group_id):
                return {"questions": []}

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            synchronizer = Synchronizer()
            jobs = DeferredJobs()
            app.synchronizer = synchronizer
            app.jobs = jobs
            app.inventory = Inventory()

            preview_status, preview = app.post(
                "/api/groups/sample/2026/sync-preview", {}
            )
            sync_status, _job = app.post(
                "/api/groups/sample/2026/sync",
                {"previewToken": preview["previewToken"]},
            )
            jobs.worker(lambda _message: None)

        self.assertEqual((preview_status, sync_status), (200, 202))
        self.assertEqual(synchronizer.preview_forces, [True, True])
        self.assertEqual(synchronizer.run_forces, [True])

    def test_manual_patch_sync_marks_matching_validated_run_current(self):
        class Synchronizer:
            def run(self, qualification, list_group_id, token, emit, *, force=False):
                return {"message": "ローカル成果物を最新patchに同期しました。"}

        class Inventory:
            def group(self, qualification, list_group_id):
                return {"questions": []}

        class RunStore:
            def __init__(self):
                self.updated = None

            def list(self, qualification, *, limit):
                return [
                    {
                        "runId": "unrelated-system-run",
                        "status": "succeeded",
                        "receiptValidated": True,
                        "workType": "maintenance",
                        "scopeListGroupIds": ["2026"],
                        "artifactSync": {"status": "blocked"},
                    },
                    {
                        "runId": "run-1",
                        "status": "succeeded",
                        "receiptValidated": True,
                        "workType": "maintenance_flow",
                        "scopeListGroupIds": ["2026"],
                        "artifactSync": {"status": "blocked"},
                    }
                ]

            def update(self, qualification, run_id, **changes):
                self.updated = (qualification, run_id, changes)

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.synchronizer = Synchronizer()
            app.inventory = Inventory()
            run_store = RunStore()
            app.run_store = run_store
            logs = []

            result = app._run_sync_job("sample", "2026", "token", logs.append)

        self.assertEqual(
            result["message"], "ローカル成果物を最新patchに同期しました。"
        )
        self.assertEqual(run_store.updated[:2], ("sample", "run-1"))
        artifact_sync = run_store.updated[2]["artifactSync"]
        self.assertEqual(artifact_sync["status"], "succeeded")
        self.assertEqual(artifact_sync["groups"][0]["listGroupId"], "2026")
        self.assertIn("直近の整備記録", logs[-1])

    def test_failed_delta_reconciliation_runs_from_group_ui_action(self):
        class DeferredJobs:
            def start(self, *, kind, key, worker):
                self.kind = kind
                self.key = key
                self.worker = worker
                return {"jobId": "job-1", "status": "queued"}

        class Inventory:
            def __init__(self):
                self.invalidated = []

            def invalidate(self, qualification, list_group_id):
                self.invalidated.append((qualification, list_group_id))

        def reconcile(_root, *, qualification, list_group_id, execute):
            result = {
                "qualification": qualification,
                "listGroupId": list_group_id,
                "status": "succeeded" if execute else "ready",
                "previewToken": "verified-token",
                "unresolvedPathCount": 3,
                "verifiedQuestionCount": 10,
                "failedRunIds": ["old-run"],
            }
            return result

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            jobs = DeferredJobs()
            inventory = Inventory()
            app.jobs = jobs
            app.inventory = inventory
            with patch(
                "tools.question_review_console.server.reconcile_failed_deltas",
                side_effect=reconcile,
            ):
                preview_status, preview = app.post(
                    "/api/groups/sample/2026/"
                    "failed-delta-reconciliation-preview",
                    {},
                )
                start_status, job = app.post(
                    "/api/groups/sample/2026/failed-delta-reconciliation",
                    {"previewToken": preview["previewToken"]},
                )
                result = jobs.worker(lambda _message: None)

        self.assertEqual((preview_status, start_status), (200, 202))
        self.assertEqual(job["jobId"], "job-1")
        self.assertEqual(jobs.kind, "failed-delta-reconciliation")
        self.assertEqual(jobs.key, "question-review-repository-operation")
        self.assertEqual(inventory.invalidated, [("sample", "2026")])
        self.assertIn("実質的な指摘は残しています", result["message"])

    def test_manual_sync_reports_strict_law_validation_reason(self):
        class Synchronizer:
            def preview(self, qualification, list_group_id, *, force=False):
                return {
                    "previewToken": "token",
                    "canSync": False,
                    "failedDeltaPaths": [],
                    "strictValidationWarnings": [
                        {
                            "field": "lawRevisionFacts",
                            "detail": "問1: 現行法監査スナップショットがありません。",
                        }
                    ],
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.synchronizer = Synchronizer()

            with self.assertRaises(ApiError) as caught:
                app.post(
                    "/api/groups/sample/2026/sync",
                    {"previewToken": "token"},
                )

        self.assertEqual(caught.exception.status, 422)
        self.assertIn("問1", str(caught.exception))
        self.assertIn("現行法監査スナップショット", str(caught.exception))

    def test_direct_patch_edit_automatically_regenerates_publication_artifacts(self):
        class Editor:
            def apply(self, *args):
                return {"changedPaths": ["patch.json"], "diffs": []}

        class Inventory:
            def group(self, qualification, list_group_id):
                return {"questions": [{"id": "question-1"}]}

            def invalidate(self, qualification, list_group_id):
                return None

        class Synchronizer:
            def __init__(self):
                self.calls = []

            def preview(self, qualification, list_group_id, *, force=False):
                return {
                    "needsSync": True,
                    "canSync": True,
                    "requiredFieldWarnings": [],
                    "failedDeltaPaths": [],
                    "previewToken": "token",
                }

            def run(
                self, qualification, list_group_id, token, emit, *, force=False
            ):
                self.calls.append((qualification, list_group_id, token))
                return {"message": "同期しました。"}

        class Reviews:
            def create(self, question, request, *, status):
                return {"reviewId": "review-1", "status": status}

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            question = {
                "id": "question-1",
                "qualification": "sample",
                "listGroupId": "2026",
                "stateHash": "state-1",
            }
            app._question = lambda _question_id, _query: dict(question)
            app._decorate = lambda value: dict(value)
            app.editor = Editor()
            app.inventory = Inventory()
            synchronizer = Synchronizer()
            app.synchronizer = synchronizer
            app.reviews = Reviews()

            status, result = app.post(
                "/api/direct-edits/apply",
                {
                    "questionId": "question-1",
                    "stateHash": "state-1",
                    "changes": {"explanationText": ["正しい。新"]},
                    "reason": "読みやすくした",
                    "previewToken": "preview",
                },
            )

        self.assertEqual(status, 200)
        self.assertEqual(result["artifactSync"]["status"], "succeeded")
        self.assertFalse(result["warning"])
        self.assertEqual(
            synchronizer.calls,
            [("sample", "2026", "token")],
        )

    def test_direct_patch_edit_returns_warning_after_post_commit_failure(self):
        class Editor:
            def __init__(self):
                self.applied = False

            def apply(self, *args):
                self.applied = True
                return {"changedPaths": ["patch.json"], "diffs": []}

        class Inventory:
            def invalidate(self, qualification, list_group_id):
                raise RuntimeError("cache unavailable")

        class Reviews:
            def create(self, *args, **kwargs):
                raise RuntimeError("review store unavailable")

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            question = {
                "id": "question-1",
                "qualification": "sample",
                "listGroupId": "2026",
                "stateHash": "state-1",
            }
            app._question = lambda _question_id, _query: dict(question)
            app._decorate = lambda value: dict(value)
            editor = Editor()
            app.editor = editor
            app.inventory = Inventory()
            app.reviews = Reviews()

            status, result = app.post(
                "/api/direct-edits/apply",
                {
                    "questionId": "question-1",
                    "stateHash": "state-1",
                    "changes": {"explanationText": ["正しい。新"]},
                    "reason": "読みやすくした",
                    "previewToken": "preview",
                },
            )

        self.assertEqual(status, 200)
        self.assertTrue(editor.applied)
        self.assertTrue(result["warning"])
        self.assertEqual(result["artifactSync"]["status"], "failed")
        self.assertIsNone(result["review"])
        self.assertTrue(
            any(
                "inventory更新" in error
                for error in result["postCommitErrors"]
            )
        )
        self.assertIn("patchは保存しました", result["message"])

    def test_question_fingerprint_includes_cross_browser_publication_state(self):
        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app._question = lambda _question_id, _query: {"id": "question-1"}
            app._decorate = lambda _question: {
                "id": "question-1",
                "stateHash": "state-1",
                "reviewStatus": "approved",
                "issueCodes": [],
                "workflow": {"firestore": "match"},
                "evaluation": {"status": "passed", "resultHash": "result-1"},
                "publishReady": False,
                "nextAction": "complete",
            }

            status, result = app.get(
                "/api/questions/question-1/fingerprint", {}
            )

        self.assertEqual(status, 200)
        self.assertEqual(result["workflowFirestore"], "match")
        self.assertEqual(result["evaluationStatus"], "passed")
        self.assertEqual(result["evaluationResultHash"], "result-1")
        self.assertFalse(result["publishReady"])
        self.assertEqual(result["nextAction"], "complete")

    def test_json_response_ignores_client_disconnect_without_retrying_headers(self):
        class DisconnectedWriter:
            def write(self, _content):
                raise BrokenPipeError("client closed")

        handler = object.__new__(QuestionReviewRequestHandler)
        statuses = []
        handler.wfile = DisconnectedWriter()
        handler.send_response = statuses.append
        handler.send_header = lambda _name, _value: None
        handler.end_headers = lambda: None
        handler._send_security_headers = lambda: None

        handler._send_json(200, {"ok": True})

        self.assertEqual(statuses, [200])

    def test_question_summary_uses_upload_ready_content_only_when_locally_current(self):
        question = {
            "id": "question-1",
            "body": "問題文",
            "choiceCount": 2,
            "workflow": {"merge": "match", "convert": "match", "upload": "match"},
            "projected": {
                "correctChoiceText": ["間違い", "間違い"],
                "explanationText": ["patch A", "patch B"],
            },
            "uploadReadyDocs": [
                {"correctChoiceText": "正しい", "explanationText": "公開 A"},
                {"correctChoiceText": "間違い", "explanationText": ""},
            ],
        }

        current = QuestionReviewApplication._summary(question)["publicationSummary"]
        question["workflow"]["upload"] = "stale"
        stale = QuestionReviewApplication._summary(question)["publicationSummary"]

        self.assertEqual(current["contentSource"], "upload_ready")
        self.assertEqual(current["verdicts"], ["正しい", "間違い"])
        self.assertEqual(current["explanationCount"], 1)
        self.assertEqual(current["explanationExpectedCount"], 2)
        self.assertEqual(stale["contentSource"], "projected")
        self.assertEqual(stale["verdicts"], ["間違い", "間違い"])
        self.assertEqual(stale["explanationCount"], 2)
        self.assertEqual(stale["explanationExpectedCount"], 2)

    def test_question_summary_expects_one_common_group_choice_explanation(self):
        question = {
            "id": "question-1",
            "body": "最も近い値はどれか。",
            "choiceCount": 5,
            "workflow": {"merge": "stale", "convert": "stale", "upload": "match"},
            "projected": {
                "questionType": "group_choice",
                "correctChoiceText": ["不正解", "不正解", "不正解", "正解", "不正解"],
                "explanationText": ["正解は80である。計算式から求める。"],
            },
        }

        publication = QuestionReviewApplication._summary(question)["publicationSummary"]

        self.assertEqual(publication["explanationCount"], 1)
        self.assertEqual(publication["explanationExpectedCount"], 1)

    def test_question_summary_exposes_failed_delta_count_without_repeating_paths(self):
        question = {
            "id": "question-1",
            "contentUpdatedAt": "2026-07-26T04:30:00Z",
            "evaluation": {
                "status": "stale",
                "failedDeltaPaths": ["first.json", "second.json"],
            },
        }

        summary = QuestionReviewApplication._summary(question)

        self.assertEqual(summary["evaluation"]["failedDeltaCount"], 2)
        self.assertEqual(summary["contentUpdatedAt"], "2026-07-26T04:30:00Z")
        self.assertNotIn("failedDeltaPaths", summary["evaluation"])

    def test_job_summary_returns_only_recent_truncated_logs(self):
        class Jobs:
            def get(self, job_id):
                return {
                    "jobId": job_id,
                    "kind": "codex-maintenance",
                    "status": "running",
                    "logs": [f"log-{index}-" + "x" * 800 for index in range(8)],
                    "logEntries": [
                        {
                            "sequence": index + 1,
                            "at": f"time-{index}",
                            "level": "info",
                            "message": f"entry-{index}-" + "x" * 800,
                        }
                        for index in range(8)
                    ],
                    "createdAt": "created",
                    "startedAt": "started",
                    "finishedAt": None,
                    "lastActivityAt": "active",
                    "result": None,
                    "error": None,
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.jobs = Jobs()
            status, payload = app.get("/api/jobs/job-1/summary", {})

        self.assertEqual(status, 200)
        self.assertEqual(payload["jobId"], "job-1")
        self.assertEqual(len(payload["logs"]), 5)
        self.assertTrue(all(len(line) == 500 for line in payload["logs"]))
        self.assertEqual(
            [entry["sequence"] for entry in payload["logEntries"]],
            [4, 5, 6, 7, 8],
        )
        self.assertTrue(
            all(len(entry["message"]) == 500 for entry in payload["logEntries"])
        )
        self.assertEqual(payload["lastActivityAt"], "active")
        self.assertNotIn("result", payload)

    def test_technical_log_has_explicit_endpoint_only(self):
        class Runs:
            def technical_log(self, qualification, run_id):
                self.called = (qualification, run_id)
                return {
                    "runId": run_id,
                    "technicalLogPath": "output/runs/run-1/technical_log.jsonl",
                    "entries": [
                        {
                            "sequence": 1,
                            "observedAt": "now",
                            "level": "error",
                            "message": "command failed",
                            "commandStatus": "failed",
                            "exitCode": 1,
                        }
                    ],
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            runs = Runs()
            app.qualification_runs = runs
            status, payload = app.get(
                "/api/qualification-runs/run-1/technical-log",
                {"qualification": ["sample"]},
            )

        self.assertEqual(status, 200)
        self.assertEqual(runs.called, ("sample", "run-1"))
        self.assertEqual(payload["entries"][0]["exitCode"], 1)

    def test_recent_qualification_runs_return_display_fields_only(self):
        run = {
            "runId": "run-1",
            "qualification": "sample",
            "status": "running",
            "workType": "maintenance_flow",
            "stageCode": "03",
            "stageLabel": "解説",
            "modeLabel": "未整備のみ",
            "kind": "human",
            "targetCount": 58,
            "workItemCount": 406,
            "stageIds": ["explanation"],
            "questionIds": ["q2", "q1"],
            "jobId": "job-1",
            "technicalLogPath": "internal/technical_log.jsonl",
            "queueStatus": "partial",
            "pauseKind": "external_provider",
            "blockedQuestionCount": 1,
            "blockedWorkItemCount": 1,
            "validatedQuestionCount": 57,
            "validatedWorkItemCount": 57,
            "questionExecutionSummary": {
                "questionCount": 58,
                "blockedQuestionCount": 1,
                "validatedQuestionCount": 57,
            },
            "questionExecutions": [
                {
                    "questionId": "secret-question",
                    "stages": [{"error": "large internal reason"}],
                }
            ],
            "phaseExecutions": [
                {
                    "id": "explanation",
                    "label": "解説",
                    "status": "running",
                    "stageCodes": ["03"],
                    "prompt": "large internal prompt",
                }
            ],
            "artifactSync": {
                "status": "pending",
                "message": "待機中",
                "commands": ["internal command"],
            },
            "progressTargets": [{"body": "x" * 10000}],
            "targetRecordAliases": ["alias"],
            "allowedFiles": ["internal-path"],
            "prompt": "x" * 10000,
        }

        class Runs:
            def recent(self, qualification):
                return {
                    "qualification": qualification,
                    "runs": [run],
                    "activeRun": run,
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.qualification_runs = Runs()
            status, payload = app.get(
                "/api/qualification-runs", {"qualification": ["sample"]}
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload["activeRun"]["runId"], "run-1")
        summary = payload["runs"][0]
        self.assertEqual(summary["workItemCount"], 406)
        self.assertEqual(summary["queueStatus"], "partial")
        self.assertEqual(summary["pauseKind"], "external_provider")
        self.assertEqual(summary["questionIds"], ["q2", "q1"])
        self.assertEqual(payload["activeRun"]["questionIds"], ["q2", "q1"])
        self.assertEqual(summary["blockedQuestionCount"], 1)
        self.assertEqual(
            summary["questionExecutionSummary"]["validatedQuestionCount"],
            57,
        )
        self.assertEqual(summary["phaseExecutions"][0]["stageCodes"], ["03"])
        self.assertEqual(
            summary["artifactSync"],
            {"status": "pending", "message": "待機中"},
        )
        for internal_field in (
            "progressTargets",
            "targetRecordAliases",
            "allowedFiles",
            "prompt",
            "technicalLogPath",
            "questionExecutions",
        ):
            self.assertNotIn(internal_field, summary)
        self.assertNotIn("prompt", summary["phaseExecutions"][0])
        self.assertNotIn("commands", summary["artifactSync"])

    def test_codex_start_conflict_returns_review_to_needs_review(self):
        class Gate:
            def assert_subscription_access(self, *, force=True):
                return {"allowed": True, "planType": "pro"}

        class Reviews:
            def create(self, question, request, *, status):
                return {
                    "reviewId": "review-conflict-1",
                    "qualification": question["qualification"],
                    "prompt": "maintenance prompt",
                    **request,
                }

            def update_status(self, review_id, status, *, current_state_hash=None):
                self.updated = (review_id, status, current_state_hash)
                return {"reviewId": review_id, "status": status}

        class Runs:
            def start_review(self, question, review, *, work_type):
                raise JobConflictError("別の処理が実行中です。")

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            reviews = Reviews()
            app.app_server = Gate()
            app.reviews = reviews
            app.qualification_runs = Runs()
            app._question = lambda question_id, query: {
                "id": question_id,
                "qualification": "sample",
                "listGroupId": "2026",
                "stateHash": "state-current",
            }
            app._decorate = lambda question: question
            with self.assertRaises(ApiError) as caught:
                app.post(
                    "/api/reviews",
                    {
                        "questionId": "question-1",
                        "status": "awaiting_codex",
                        "startCodex": True,
                        "review": {
                            "issueTypes": ["other"],
                            "note": "再確認する",
                        },
                    },
                )

        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(
            reviews.updated,
            ("review-conflict-1", "needs_review", "state-current"),
        )

    def test_protected_review_start_is_rejected_before_persistence(self):
        class Reviews:
            def create(self, *args, **kwargs):
                raise AssertionError("review must not be persisted")

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.reviews = Reviews()
            app._question = lambda question_id, query: {
                "id": question_id,
                "qualification": "sample",
                "listGroupId": "2026",
                "stateHash": "state-current",
            }
            app._decorate = lambda question: question
            with self.assertRaises(ApiError) as caught:
                app.post(
                    "/api/reviews",
                    {
                        "questionId": "question-1",
                        "status": "awaiting_codex",
                        "startCodex": True,
                        "submissionKey": "submission-1",
                        "review": {
                            "fields": ["choiceTextList"],
                            "note": "選択肢を確認",
                        },
                    },
                )

        self.assertEqual(caught.exception.status, 422)
        self.assertIn("記録専用review", str(caught.exception))

    def test_idempotent_review_replay_does_not_start_second_run(self):
        class Gate:
            def assert_subscription_access(self, *, force=True):
                return {"allowed": True}

        class Reviews:
            calls = 0

            def create(self, question, request, *, status):
                self.calls += 1
                return {
                    "reviewId": "review-1",
                    "qualification": question["qualification"],
                    "prompt": "prompt",
                    "idempotentReplay": self.calls > 1,
                }

        class Runs:
            calls = 0

            def start_review(self, question, review, *, work_type):
                self.calls += 1
                return {"job": {"jobId": "job-1"}}

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.app_server = Gate()
            app.reviews = Reviews()
            app.qualification_runs = Runs()
            app._question = lambda question_id, query: {
                "id": question_id,
                "qualification": "sample",
                "listGroupId": "2026",
                "stateHash": "state-current",
            }
            app._decorate = lambda question: question
            payload = {
                "questionId": "question-1",
                "status": "awaiting_codex",
                "startCodex": True,
                "submissionKey": "submission-1",
                "review": {"fields": ["explanationText"], "note": "確認"},
            }
            first_status, _ = app.post("/api/reviews", payload)
            second_status, _ = app.post("/api/reviews", payload)

        self.assertEqual(first_status, 202)
        self.assertEqual(second_status, 201)
        self.assertEqual(app.qualification_runs.calls, 1)

    def test_evaluation_rework_starts_fresh_codex_job_with_server_snapshot(self):
        class Gate:
            def assert_subscription_access(self, *, force=True):
                return {"allowed": True, "planType": "pro"}

        class Reviews:
            def create(self, question, request, *, status):
                self.request = request
                return {
                    "reviewId": "review-1",
                    "qualification": question["qualification"],
                    "prompt": "rework prompt",
                    **request,
                }

        class Runs:
            def start_review(self, question, review, *, work_type):
                self.work_type = work_type
                self.question = question
                self.review = review
                return {
                    "run": {"runId": "run-rework-1", "workType": work_type},
                    "prompt": None,
                    "job": {"jobId": "job-rework-1", "status": "queued"},
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.app_server = Gate()
            reviews = Reviews()
            runs = Runs()
            app.reviews = reviews
            app.qualification_runs = runs
            app._question = lambda question_id, query: {
                "id": question_id,
                "qualification": "sample",
                "listGroupId": "2026",
                "stateHash": "state-current",
            }
            app._decorate = lambda question: {
                **question,
                "evaluation": {
                    "status": "needs_rework",
                    "stateHash": "state-current",
                    "resultHash": "result-hash",
                    "summary": "正誤不一致",
                    "criticalIssues": ["正答が逆"],
                    "choiceEvaluations": [{"choiceIndex": 0}],
                    "reworkItems": [{"stage": "02a", "message": "正答修正"}],
                },
            }
            status, response = app.post(
                "/api/reviews",
                {
                    "questionId": "question-1",
                    "status": "awaiting_codex",
                    "startCodex": True,
                    "review": {
                        "requestKind": "evaluation_rework",
                        "issueTypes": ["other"],
                        "note": "評価結果に従って再確認する",
                    },
                },
            )

        self.assertEqual(status, 202)
        self.assertEqual(response["job"]["jobId"], "job-rework-1")
        self.assertEqual(runs.work_type, "rework")
        self.assertEqual(reviews.request["evaluationSnapshot"]["resultHash"], "result-hash")
        self.assertEqual(runs.review["prompt"], "rework prompt")

    def test_serves_qualification_workflow(self):
        class Workflow:
            def overview(self, qualification):
                return {
                    "qualification": qualification,
                    "nextStageId": "question_type",
                    "groups": [],
                    "stages": [],
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.qualification_workflow = Workflow()
            get_status, overview = app.get(
                "/api/qualification-workflow", {"qualification": ["sample"]}
            )

        self.assertEqual(get_status, 200)
        self.assertEqual(overview["nextStageId"], "question_type")

    def test_builds_production_workflow_overview_in_an_isolated_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = QuestionReviewApplication(root)
            config = root / "config" / "question_maintenance_workflow.toml"
            config.parent.mkdir(parents=True)
            config.write_text("[system]\n", encoding="utf-8")

            class Result:
                returncode = 0
                stdout = json.dumps(
                    {
                        "qualification": "sample",
                        "groups": [],
                        "stages": [],
                    }
                )
                stderr = ""

            with patch(
                "tools.question_review_console.server.subprocess.run",
                return_value=Result(),
            ) as runner:
                overview = app._load_workflow_overview("sample")

        self.assertEqual(overview["qualification"], "sample")
        command = runner.call_args.args[0]
        self.assertIn(
            "tools.question_review_console.workflow_overview_builder",
            command,
        )
        self.assertEqual(command[-1], "sample")

    def test_builds_question_list_read_model_in_an_isolated_process(self):
        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))

            class Result:
                returncode = 0
                stdout = json.dumps(
                    {
                        "qualification": "sample",
                        "listGroupIds": [],
                        "groups": [],
                        "questions": [],
                    }
                )
                stderr = ""

            with patch(
                "tools.question_review_console.server.subprocess.run",
                return_value=Result(),
            ) as runner:
                snapshot = app._load_question_list_read_model("sample")

        self.assertEqual(snapshot["qualification"], "sample")
        command = runner.call_args.args[0]
        self.assertIn(
            "tools.question_review_console.question_list_read_model_builder",
            command,
        )
        self.assertEqual(command[-1], "sample")

    def test_builds_question_detail_read_model_in_an_isolated_process(self):
        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))

            class Result:
                returncode = 0
                stdout = json.dumps(
                    {
                        "cacheKey": "sample--2026",
                        "qualification": "sample",
                        "listGroupId": "2026",
                        "questionsById": {},
                    }
                )
                stderr = ""

            with patch(
                "tools.question_review_console.server.subprocess.run",
                return_value=Result(),
            ) as runner:
                snapshot = app._load_question_detail_read_model(
                    "sample--2026"
                )

        self.assertEqual(snapshot["qualification"], "sample")
        self.assertEqual(snapshot["listGroupId"], "2026")
        command = runner.call_args.args[0]
        self.assertIn(
            "tools.question_review_console.question_detail_read_model_builder",
            command,
        )
        self.assertEqual(command[-4:], [
            "--qualification",
            "sample",
            "--list-group-id",
            "2026",
        ])

    def test_updates_qualification_law_workflow_setting(self):
        class Workflow:
            def set_law_workflow_enabled(self, qualification, enabled):
                self.updated = (qualification, enabled)
                return {
                    "qualification": qualification,
                    "lawWorkflowEnabled": enabled,
                    "groups": [],
                    "stages": [],
                }

        class Runs:
            def recent(self, qualification):
                return {"qualification": qualification, "activeRun": None}

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            workflow = Workflow()
            app.qualification_workflow = workflow
            app.qualification_runs = Runs()

            status, overview = app.post(
                "/api/qualification-workflow/law-setting",
                {"qualification": "sample", "enabled": False},
            )

        self.assertEqual(status, 200)
        self.assertEqual(workflow.updated, ("sample", False))
        self.assertFalse(overview["lawWorkflowEnabled"])

    def test_rejects_law_workflow_setting_change_during_active_run(self):
        class Runs:
            def recent(self, qualification):
                return {
                    "qualification": qualification,
                    "activeRun": {"runId": "run-1", "status": "running"},
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.qualification_runs = Runs()

            with self.assertRaises(ApiError) as caught:
                app.post(
                    "/api/qualification-workflow/law-setting",
                    {"qualification": "sample", "enabled": False},
                )

        self.assertEqual(caught.exception.status, 409)
        self.assertIn("作業中", str(caught.exception))

    def test_previews_and_starts_qualification_run(self):
        class Runs:
            def preview(
                self,
                qualification,
                stage_id,
                mode,
                *,
                stage_ids=None,
                list_group_ids=None,
                update_target_ids=None,
                question_ids=None,
                resumed_from=None,
                question_concurrency=None,
                speed_mode=None,
                model_profile=None,
            ):
                self.scope = list_group_ids
                self.update_target_ids = update_target_ids
                self.question_ids = question_ids
                self.question_concurrency = question_concurrency
                self.speed_mode = speed_mode
                self.model_profile = model_profile
                return {
                    "qualification": qualification,
                    "stageId": stage_id,
                    "mode": mode,
                    "previewToken": "token",
                }

            def start(
                self,
                qualification,
                stage_id,
                mode,
                preview_token,
                *,
                stage_ids=None,
                list_group_ids=None,
                update_target_ids=None,
                question_ids=None,
                resumed_from=None,
                question_concurrency=None,
                speed_mode=None,
                model_profile=None,
                hydrate_result=True,
            ):
                self.scope = list_group_ids
                self.update_target_ids = update_target_ids
                self.question_ids = question_ids
                self.question_concurrency = question_concurrency
                self.speed_mode = speed_mode
                self.model_profile = model_profile
                self.hydrate_result = hydrate_result
                return {
                    "run": {"runId": "run-1", "qualification": qualification},
                    "prompt": "依頼",
                    "job": None,
                }

            def recent(self, qualification):
                return {"qualification": qualification, "runs": []}

            def progress(
                self,
                qualification,
                run_id,
                *,
                include_questions=True,
            ):
                return {
                    "qualification": qualification,
                    "runId": run_id,
                    "completedQuestionCount": 3,
                    "questions": (
                        [{"questionId": "q1"}]
                        if include_questions
                        else []
                    ),
                }

            def question_run_detail(
                self,
                qualification,
                run_id,
                question_id,
            ):
                return {
                    "qualification": qualification,
                    "runId": run_id,
                    "questionId": question_id,
                    "revision": 7,
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            runs = Runs()
            app.qualification_runs = runs
            _, preview = app.post(
                "/api/qualification-runs/preview",
                {
                    "qualification": "sample",
                    "stageIds": ["law_audit"],
                    "listGroupIds": ["2024", "2026"],
                    "updateTargetIds": ["explanation.supplementary_questions"],
                    "questionIds": ["q2", "q10"],
                    "mode": "attention",
                    "questionConcurrency": 10,
                    "speedMode": "standard",
                },
            )
            start_status, started = app.post(
                "/api/qualification-runs/start",
                {
                    "qualification": "sample",
                    "stageIds": ["law_audit"],
                    "listGroupIds": ["2024", "2026"],
                    "updateTargetIds": ["explanation.supplementary_questions"],
                    "questionIds": ["q2", "q10"],
                    "mode": "attention",
                    "questionConcurrency": 10,
                    "speedMode": "standard",
                    "previewToken": "token",
                },
            )
            _, recent = app.get(
                "/api/qualification-runs", {"qualification": ["sample"]}
            )
            _, progress = app.get(
                "/api/qualification-runs/run-1/progress",
                {"qualification": ["sample"]},
            )
            _, detailed_progress = app.get(
                "/api/qualification-runs/run-1/progress",
                {
                    "qualification": ["sample"],
                    "includeQuestions": ["true"],
                },
            )
            _, question_detail = app.get(
                "/api/qualification-runs/run-1/questions/question-1",
                {"qualification": ["sample"]},
            )

        self.assertEqual(preview["mode"], "attention")
        self.assertEqual(runs.scope, ["2024", "2026"])
        self.assertEqual(
            runs.update_target_ids, ["explanation.supplementary_questions"]
        )
        self.assertEqual(runs.question_ids, ["q2", "q10"])
        self.assertEqual(runs.question_concurrency, 10)
        self.assertFalse(runs.hydrate_result)
        self.assertEqual(runs.speed_mode, "standard")
        self.assertEqual(start_status, 201)
        self.assertEqual(started["run"]["runId"], "run-1")
        self.assertEqual(recent["qualification"], "sample")
        self.assertEqual(progress["runId"], "run-1")
        self.assertEqual(progress["completedQuestionCount"], 3)
        self.assertFalse(progress["questionsIncluded"])
        self.assertEqual(progress["questions"], [])
        self.assertTrue(detailed_progress["questionsIncluded"])
        self.assertEqual(detailed_progress["questions"], [{"questionId": "q1"}])
        self.assertEqual(question_detail["questionId"], "question-1")
        self.assertEqual(question_detail["revision"], 7)

    def test_qualification_run_rejects_concurrency_above_configured_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "config"
            config_dir.mkdir()
            source_config = (
                Path(__file__).resolve().parents[1]
                / "config"
                / "question_maintenance_llm.toml"
            )
            (config_dir / "question_maintenance_llm.toml").write_text(
                source_config.read_text(encoding="utf-8"), encoding="utf-8"
            )
            app = QuestionReviewApplication(root)
            try:
                with self.assertRaises(ApiError) as caught:
                    app.post(
                        "/api/qualification-runs/preview",
                        {
                            "qualification": "sample",
                            "stageIds": ["law_audit"],
                            "modelProfile": "codex_only",
                            "questionConcurrency": 2,
                        },
                    )
            finally:
                app.close()

        self.assertEqual(caught.exception.status, 422)
        self.assertIn("設定上限1", str(caught.exception))

    def test_qualification_run_api_rejects_fast_before_coordinator(self):
        class Runs:
            def preview(self, *_args, **_kwargs):
                raise AssertionError("Fast指定をcoordinatorへ渡してはいけません。")

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.qualification_runs = Runs()

            with self.assertRaises(ApiError) as caught:
                app.post(
                    "/api/qualification-runs/preview",
                    {
                        "qualification": "sample",
                        "stageIds": ["question_type"],
                        "mode": "needed",
                        "speedMode": "fast",
                    },
                )

        self.assertEqual(caught.exception.status, 422)
        self.assertIn("Standard mode", str(caught.exception))

    def test_qualification_run_interprets_instruction_against_server_workflow(self):
        class Overviews:
            def get(self, qualification):
                self.qualification = qualification
                return {"qualification": qualification, "stages": []}

        class Interpreter:
            def interpret(self, **options):
                self.options = options
                return {
                    "status": "ready",
                    "canApply": True,
                    "selectedUpdateTargetIds": ["explanation.learning_pattern"],
                    "selectedStageIds": ["explanation"],
                    "mode": "group_refresh",
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            overviews = Overviews()
            interpreter = Interpreter()
            app.workflow_overviews = overviews
            app.maintenance_instruction_interpreter = interpreter

            status, result = app.post(
                "/api/qualification-runs/interpret-instruction",
                {
                    "qualification": "sample",
                    "instruction": "分類だけ再実行して",
                    "currentMode": "needed",
                },
            )

        self.assertEqual(status, 200)
        self.assertEqual(overviews.qualification, "sample")
        self.assertEqual(
            interpreter.options["workflow"],
            {"qualification": "sample", "stages": []},
        )
        self.assertEqual(interpreter.options["current_mode"], "needed")
        self.assertEqual(
            result["selectedUpdateTargetIds"],
            ["explanation.learning_pattern"],
        )

    def test_qualification_run_passes_normalized_question_ids(self):
        class Runs:
            def preview(self, qualification, stage_id, mode, **options):
                self.options = options
                return {"previewToken": "token"}

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            runs = Runs()
            app.qualification_runs = runs
            app.post(
                "/api/qualification-runs/preview",
                {
                    "qualification": "sample",
                    "stageIds": ["question_type"],
                    "listGroupIds": ["2026"],
                    "questionIds": ["q2", "q1", "q2"],
                },
            )

        self.assertEqual(runs.options["question_ids"], ["q2", "q1"])

    def test_qualification_run_evaluation_rework_uses_server_snapshots_and_exact_stages(self):
        class Runs:
            def preview(self, qualification, stage_id, mode, **options):
                self.qualification = qualification
                self.stage_id = stage_id
                self.mode = mode
                self.options = options
                return {
                    "previewToken": "token",
                    "evaluationRework": True,
                }

        class Workflow:
            def catalog(self, qualification):
                return {
                    "stages": [
                        {
                            "id": "originalize",
                            "updateTargets": [
                                {"selectionId": "originalize.content"}
                            ],
                        },
                        {
                            "id": "question_intent",
                            "updateTargets": [
                                {"selectionId": "question_intent.intent"}
                            ],
                        },
                        {
                            "id": "explanation",
                            "updateTargets": [
                                {"selectionId": "explanation.basic_explanation"}
                            ],
                        },
                        {
                            "id": "law_context",
                            "updateTargets": [
                                {"selectionId": "law_context.law_context"}
                            ],
                        },
                        {
                            "id": "law_audit",
                            "updateTargets": [
                                {"selectionId": "law_audit.law_audit"}
                            ],
                        },
                    ]
                }

        questions = {
            "q1": {
                "id": "q1",
                "qualification": "sample",
                "listGroupId": "2025",
                "evaluation": {
                    "status": "needs_rework",
                    "stateHash": "state-1",
                    "resultHash": "result-1",
                    "summary": "解説修正",
                    "criticalIssues": ["根拠不足"],
                    "choiceEvaluations": [{"choiceIndex": 0}],
                    "reworkItems": [{"stage": "03", "message": "根拠を補う"}],
                },
            },
            "q2": {
                "id": "q2",
                "qualification": "sample",
                "listGroupId": "2024",
                "evaluation": {
                    "status": "needs_rework",
                    "stateHash": "state-2",
                    "resultHash": "result-2",
                    "summary": "法令修正",
                    "criticalIssues": ["改正確認"],
                    "choiceEvaluations": [{"choiceIndex": 1}],
                    "reworkItems": [{"stage": "03b", "message": "新旧法を確認"}],
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            runs = Runs()
            app.qualification_runs = runs
            app.qualification_workflow = Workflow()
            app._question = lambda question_id, _query: questions[question_id]
            app._decorate = lambda question: question
            _, preview = app.post(
                "/api/qualification-runs/preview",
                {
                    "qualification": "sample",
                    "stageIds": ["question_intent"],
                    "questionIds": ["q1", "q2"],
                    "evaluationRework": True,
                    "questionConcurrency": 100,
                },
            )

        self.assertTrue(preview["evaluationRework"])
        self.assertEqual(runs.stage_id, "explanation")
        self.assertEqual(runs.mode, "group_refresh")
        self.assertEqual(
            runs.options["stage_ids"],
            ["explanation", "law_context", "law_audit"],
        )
        self.assertEqual(
            runs.options["list_group_ids"],
            ["2025", "2024"],
        )
        self.assertEqual(
            runs.options["update_target_ids"],
            [
                "explanation.basic_explanation",
                "law_context.law_context",
                "law_audit.law_audit",
            ],
        )
        self.assertEqual(
            runs.options["evaluation_rework_snapshots"]["q1"]["resultHash"],
            "result-1",
        )

    def test_evaluation_rework_adds_correct_choice_when_answer_mapping_mismatches(self):
        class Runs:
            def preview(self, qualification, stage_id, mode, **options):
                self.stage_id = stage_id
                self.options = options
                return {"previewToken": "token", "evaluationRework": True}

        class Workflow:
            def catalog(self, qualification):
                return {
                    "stages": [
                        {
                            "id": "originalize",
                            "updateTargets": [
                                {"selectionId": "originalize.content"}
                            ],
                        },
                        {
                            "id": "correct_choice",
                            "updateTargets": [
                                {"selectionId": "correct_choice.correct_answer"}
                            ],
                        },
                        {
                            "id": "explanation",
                            "updateTargets": [
                                {"selectionId": "explanation.basic_explanation"}
                            ],
                        },
                    ]
                }

        question = {
            "id": "q1",
            "qualification": "sample",
            "listGroupId": "2025",
            "evaluation": {
                "status": "needs_rework",
                "stateHash": "state-1",
                "resultHash": "result-1",
                "answerMappingMatched": False,
                "reworkItems": [{"stage": "03", "message": "解説を直す"}],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            runs = Runs()
            app.qualification_runs = runs
            app.qualification_workflow = Workflow()
            app._question = lambda question_id, _query: question
            app._decorate = lambda value: value
            app.post(
                "/api/qualification-runs/preview",
                {
                    "qualification": "sample",
                    "stageIds": ["explanation"],
                    "questionIds": ["q1"],
                    "evaluationRework": True,
                },
            )

        self.assertEqual(runs.stage_id, "originalize")
        self.assertEqual(
            runs.options["stage_ids"],
            ["originalize", "correct_choice", "explanation"],
        )
        self.assertEqual(
            runs.options["update_target_ids"],
            [
                "originalize.content",
                "correct_choice.correct_answer",
                "explanation.basic_explanation",
            ],
        )
        self.assertFalse(
            runs.options["evaluation_rework_snapshots"]["q1"][
                "answerMappingMatched"
            ]
        )

    def test_evaluation_rework_adds_explanation_when_question_type_is_rechecked(self):
        class Runs:
            def preview(self, qualification, stage_id, mode, **options):
                self.stage_id = stage_id
                self.options = options
                return {"previewToken": "token", "evaluationRework": True}

        class Workflow:
            def catalog(self, qualification):
                return {
                    "stages": [
                        {
                            "id": "question_type",
                            "updateTargets": [
                                {"selectionId": "question_type.question_type"},
                                {"selectionId": "question_type.calculation_flag"},
                            ],
                        },
                        {
                            "id": "explanation",
                            "updateTargets": [
                                {"selectionId": "explanation.basic_explanation"},
                                {
                                    "selectionId": (
                                        "explanation.supplementary_questions"
                                    )
                                },
                                {"selectionId": "explanation.law_support"},
                            ],
                        },
                    ]
                }

        question = {
            "id": "q1",
            "qualification": "sample",
            "listGroupId": "2025",
            "evaluation": {
                "status": "needs_rework",
                "stateHash": "state-1",
                "resultHash": "result-1",
                "reworkItems": [
                    {"stage": "01", "message": "問題形式を再確認する"}
                ],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            runs = Runs()
            app.qualification_runs = runs
            app.qualification_workflow = Workflow()
            app._question = lambda question_id, _query: question
            app._decorate = lambda value: value
            app.post(
                "/api/qualification-runs/preview",
                {
                    "qualification": "sample",
                    "stageIds": ["question_type"],
                    "questionIds": ["q1"],
                    "evaluationRework": True,
                },
            )

        self.assertEqual(runs.stage_id, "question_type")
        self.assertEqual(
            runs.options["stage_ids"],
            ["question_type", "explanation"],
        )
        self.assertEqual(
            runs.options["update_target_ids"],
            [
                "question_type.question_type",
                "question_type.calculation_flag",
                "explanation.basic_explanation",
                "explanation.supplementary_questions",
                "explanation.law_support",
            ],
        )

    def test_qualification_run_rejects_unsupported_question_concurrency(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(
            ApiError
        ) as caught:
            app = QuestionReviewApplication(Path(directory))
            app.post(
                "/api/qualification-runs/preview",
                {
                    "qualification": "sample",
                    "stageIds": ["question_type"],
                    "mode": "remaining",
                    "questionConcurrency": 50,
                },
            )

        self.assertEqual(caught.exception.status, 422)
        self.assertIn("1、5、10", str(caught.exception))

    def test_qualification_run_rejects_unknown_request_field(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(
            ApiError
        ) as caught:
            app = QuestionReviewApplication(Path(directory))
            app.post(
                "/api/qualification-runs/preview",
                {
                    "qualification": "sample",
                    "stageIds": ["explanation"],
                    "unknownScope": {"start": 5, "end": 2},
                },
            )

        self.assertEqual(caught.exception.status, 422)
        self.assertIn("未対応のrequest field", str(caught.exception))

    def test_qualification_run_rejects_unknown_singular_scope(self):
        cases = (
            {"stageId": "law_audit", "listGroupIds": ["2026"]},
            {"stageIds": ["law_audit"], "listGroupId": "2026"},
        )
        for legacy_scope in cases:
            with (
                self.subTest(legacy_scope=legacy_scope),
                tempfile.TemporaryDirectory() as directory,
                self.assertRaises(ApiError) as caught,
            ):
                app = QuestionReviewApplication(Path(directory))
                app.post(
                    "/api/qualification-runs/preview",
                    {"qualification": "sample", **legacy_scope},
                )

            self.assertEqual(caught.exception.status, 422)
            self.assertIn("未対応のrequest field", str(caught.exception))

    def test_bulk_law_audit_post_adds_all_qualification_target_files(self):
        class Inventory:
            def inventory(self):
                return {
                    "qualifications": [{"id": "sample", "listGroupIds": ["2025"]}]
                }

            def group(self, qualification, list_group_id):
                return {
                    "questions": [
                        {
                            "id": "sample-2025-q1",
                            "originalQuestionId": "sample-2025-q1",
                            "sourceStem": "question_2025_1",
                            "issueCodes": ["law_audit_metadata_incomplete"],
                            "paths": {
                                "patches": [
                                    "output/sample/questions_json/2025/21_explanationText_added/question_2025_1_explanationText_added.json"
                                ]
                            },
                        }
                    ]
                }

        class Reviews:
            def create(self, question, request, *, status):
                self.request = request
                return request

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.inventory = Inventory()
            app.reviews = Reviews()
            app._question = lambda question_id, query: {
                "id": question_id,
                "qualification": "sample",
            }
            app._decorate = lambda question: question
            status, review = app.post(
                "/api/reviews",
                {
                    "questionId": "question-1",
                    "review": {
                        "issueTypes": ["law_audit_metadata_incomplete"],
                        "note": "一括監査する",
                        "selection": {
                            "targetLabel": "法令監査メタデータの一括報告"
                        },
                        "investigationScope": "qualification",
                    },
                },
            )

        self.assertEqual(status, 201)
        self.assertEqual(review["requestKind"], "qualification_law_audit")
        self.assertEqual(review["investigationScope"], "qualification")
        self.assertEqual(len(review["targetFiles"]), 4)
        self.assertEqual(len(review["targetSourceFiles"]), 1)
        self.assertEqual(
            review["targetRecordAliasGroups"], [["sample-2025-q1"]]
        )

    def test_collects_qualification_law_audit_patch_files_for_selected_issue(self):
        class Inventory:
            def inventory(self):
                return {
                    "qualifications": [
                        {"id": "sample", "listGroupIds": ["2024", "2025"]}
                    ]
                }

            def group(self, qualification, list_group_id):
                path = (
                    f"output/{qualification}/questions_json/{list_group_id}/"
                    f"21_explanationText_added/question_{list_group_id}_1_explanationText_added.json"
                )
                return {
                    "questions": [
                        {
                            "id": f"sample-{list_group_id}-q1",
                            "originalQuestionId": f"sample-{list_group_id}-q1",
                            "sourceStem": f"question_{list_group_id}_1",
                            "issueCodes": ["law_audit_metadata_incomplete"],
                            "paths": {"patches": [path]},
                        },
                        {
                            "id": f"sample-{list_group_id}-q2",
                            "originalQuestionId": f"sample-{list_group_id}-q2",
                            "sourceStem": f"question_{list_group_id}_2",
                            "issueCodes": ["law_hold"],
                            "paths": {"patches": []},
                        },
                    ]
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.inventory = Inventory()
            paths = app._qualification_law_audit_target_files(
                "sample", ["law_audit_metadata_incomplete"]
            )

        self.assertEqual(len(paths), 8)
        self.assertTrue(
            all(
                any(
                    marker in path
                    for marker in (
                        "18_law_context_prepared",
                        "21_explanationText_added",
                        "23_correctChoiceText_fixed",
                        "99_model_review_flags",
                    )
                )
                for path in paths
            )
        )
        self.assertFalse(any("question_2024_2" in path for path in paths))

    def test_lists_all_groups_for_a_qualification(self):
        class Inventory:
            def inventory(self):
                return {
                    "qualifications": [
                        {"id": "sample", "listGroupIds": ["2024", "2025"]}
                    ]
                }

            def group(self, qualification, list_group_id):
                return {
                    "qualification": qualification,
                    "listGroupId": list_group_id,
                    "questionCount": 1,
                    "fingerprint": f"fingerprint-{list_group_id}",
                    "questions": [
                        {
                            "id": f"question-{list_group_id}",
                            "listGroupId": list_group_id,
                            "body": f"{list_group_id}年の問題",
                            "questionLabel": "問1",
                            "sourceQuestionKey": f"sample:{list_group_id}:q01",
                            "issues": [],
                            "issueCodes": [],
                            "reviewStatus": "unreviewed",
                            "isLawRelated": False,
                            "workflow": {"firestore": "unread"},
                        }
                    ],
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.inventory = Inventory()
            app._decorate = lambda question: question
            app._summary = lambda question: dict(question)
            result = app._questions(
                {
                    "qualification": ["sample"],
                    "listGroupId": ["__all__"],
                    "exceptionsOnly": ["false"],
                }
            )

        self.assertEqual(result["questionCount"], 2)
        self.assertEqual(result["filteredCount"], 2)
        self.assertEqual(
            [question["listGroupId"] for question in result["questions"]],
            ["2024", "2025"],
        )

    def test_lightweight_question_list_filters_materialized_snapshot(self):
        class QuestionLists:
            def get(self, qualification, *, wait_for_initial):
                self.request = (qualification, wait_for_initial)
                return {
                    "qualification": qualification,
                    "listGroupIds": ["2025", "2026"],
                    "groups": [
                        {
                            "listGroupId": "2025",
                            "questionCount": 1,
                            "fingerprint": "a",
                        },
                        {
                            "listGroupId": "2026",
                            "questionCount": 2,
                            "fingerprint": "b",
                        },
                    ],
                    "questions": [
                        {
                            "id": "old",
                            "listGroupId": "2025",
                            "body": "一般問題",
                            "contentUpdatedAt": "2026-07-20T00:00:00+09:00",
                            "isLawRelated": False,
                        },
                        {
                            "id": "new-law",
                            "listGroupId": "2026",
                            "body": "法令問題",
                            "contentUpdatedAt": "2026-07-26T00:00:00+09:00",
                            "isLawRelated": True,
                        },
                        {
                            "id": "middle-law",
                            "listGroupId": "2026",
                            "body": "別の法令問題",
                            "contentUpdatedAt": "2026-07-24T00:00:00+09:00",
                            "isLawRelated": True,
                        },
                    ],
                    "cache": {
                        "refreshing": False,
                        "stale": False,
                        "refreshError": None,
                    },
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            question_lists = QuestionLists()
            app.question_list_read_models = question_lists
            result = app._question_list(
                {
                    "qualification": ["sample"],
                    "listGroupId": ["__all__"],
                    "lawOnly": ["true"],
                    "offset": ["0"],
                    "limit": ["1"],
                }
            )

        self.assertEqual(question_lists.request, ("sample", False))
        self.assertEqual(result["questionCount"], 3)
        self.assertEqual(result["filteredCount"], 2)
        self.assertTrue(result["hasMore"])
        self.assertEqual(
            [question["id"] for question in result["questions"]],
            ["new-law"],
        )

    def test_cold_question_list_returns_loading_without_full_projection(self):
        class QuestionLists:
            def get(self, _qualification, *, wait_for_initial):
                self.wait_for_initial = wait_for_initial
                return None

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            question_lists = QuestionLists()
            app.question_list_read_models = question_lists
            app._questions = lambda _query: self.fail(
                "cold simple list must not run the full projection"
            )
            result = app._question_list(
                {
                    "qualification": ["sample"],
                    "listGroupId": ["2026"],
                }
            )

        self.assertFalse(question_lists.wait_for_initial)
        self.assertTrue(result["loading"])
        self.assertEqual(result["questions"], [])

    def test_question_content_reads_one_question_from_materialized_snapshot(self):
        class QuestionDetails:
            def get(self, cache_key, *, wait_for_initial):
                self.request = (cache_key, wait_for_initial)
                return {
                    "cacheKey": cache_key,
                    "qualification": "sample",
                    "listGroupId": "2026",
                    "questionsById": {
                        "question-1": {
                            "id": "question-1",
                            "qualification": "sample",
                            "listGroupId": "2026",
                            "questionLabel": "問1",
                            "projected": {
                                "questionBodyText": "本文",
                                "choiceTextList": ["A", "B"],
                            },
                            "detailVersion": "detail-version",
                        }
                    },
                    "cache": {
                        "refreshing": False,
                        "stale": False,
                        "refreshError": None,
                    },
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            question_details = QuestionDetails()
            app.question_detail_read_models = question_details
            app._question = lambda _question_id, _query: self.fail(
                "materialized detail must not load the full inventory group"
            )

            result = app._question_content(
                "question-1",
                {
                    "qualification": ["sample"],
                    "listGroupId": ["2026"],
                },
            )

        self.assertEqual(
            question_details.request,
            ("sample--2026", False),
        )
        self.assertFalse(result["loading"])
        self.assertEqual(result["detailVersion"], "detail-version")
        self.assertEqual(result["projected"]["choiceTextList"], ["A", "B"])

    def test_cold_question_content_returns_loading_without_sync_projection(self):
        class QuestionDetails:
            def get(self, _cache_key, *, wait_for_initial):
                self.wait_for_initial = wait_for_initial
                return None

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            question_details = QuestionDetails()
            app.question_detail_read_models = question_details
            app._question = lambda _question_id, _query: self.fail(
                "cold detail must not project synchronously"
            )

            result = app._question_content(
                "question-1",
                {
                    "qualification": ["sample"],
                    "listGroupId": ["2026"],
                },
            )

        self.assertFalse(question_details.wait_for_initial)
        self.assertTrue(result["loading"])
        self.assertTrue(result["cache"]["refreshing"])
        self.assertFalse(result["cache"]["waitingForRun"])

    def test_cold_question_content_uses_raw_snapshot_during_active_run(self):
        class QuestionDetails:
            def get(self, _cache_key, *, wait_for_initial):
                return None

        class Jobs:
            def has_conflict(self, _key):
                return True

        raw = {
            "id": "question-1",
            "qualification": "sample",
            "listGroupId": "2026",
            "questionLabel": "問1",
            "body": "本文",
            "issues": [],
            "issueCodes": [],
            "projected": {
                "questionBodyText": "本文",
                "choiceTextList": ["A", "B"],
                "correctChoiceText": ["正しい", "間違い"],
                "firestoreSourceQuestions": [{"unused": True}],
            },
            "workVersions": {"unused": True},
            "liveReadback": {"unused": True},
        }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.question_detail_read_models = QuestionDetails()
            app.jobs = Jobs()
            app._question = lambda _question_id, _query: raw
            app._decorate = lambda _question: self.fail(
                "active-run fallback must not decorate admin state"
            )

            result = app._question_content(
                "question-1",
                {
                    "qualification": ["sample"],
                    "listGroupId": ["2026"],
                },
            )

        self.assertFalse(result["loading"])
        self.assertTrue(result["cache"]["waitingForRun"])
        self.assertEqual(result["projected"]["choiceTextList"], ["A", "B"])
        self.assertNotIn("firestoreSourceQuestions", result["projected"])
        self.assertNotIn("workVersions", result)
        self.assertNotIn("liveReadback", result)

    def test_question_content_fingerprint_returns_lightweight_version(self):
        class QuestionDetails:
            def get(self, _cache_key, *, wait_for_initial):
                return {
                    "cacheKey": "sample--2026",
                    "qualification": "sample",
                    "listGroupId": "2026",
                    "questionsById": {
                        "question-1": {
                            "id": "question-1",
                            "qualification": "sample",
                            "listGroupId": "2026",
                            "detailVersion": "detail-version",
                            "stateHash": "state-hash",
                            "contentUpdatedAt": "2026-07-26T10:00:00+09:00",
                        }
                    },
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.question_detail_read_models = QuestionDetails()

            review_dir = (
                Path(directory)
                / "output/question_review_console/sample/2026/reviews"
            )
            review_dir.mkdir(parents=True)
            (review_dir / "review-1.json").write_text(
                json.dumps(
                    {
                        "reviewId": "review-1",
                        "reviewKey": "sample:2026:q1",
                        "questionId": "question-1",
                        "qualification": "sample",
                        "listGroupId": "2026",
                        "status": "needs_review",
                        "note": "display note",
                        "expectedOutcome": "display expected",
                        "selection": {"dataPath": "questionBodyText"},
                        "createdAt": "2026-08-11T10:00:00+09:00",
                        "internalSecret": "must not leak",
                    }
                ),
                encoding="utf-8",
            )

            status, result = app.get(
                "/api/question-content/question-1/fingerprint",
                {
                    "qualification": ["sample"],
                    "listGroupId": ["2026"],
                },
            )

        self.assertEqual(status, 200)
        self.assertEqual(result["id"], "question-1")
        self.assertEqual(result["detailVersion"], "detail-version")
        self.assertEqual(len(result["reviewHistoryVersion"]), 16)
        self.assertNotIn("reviewHistory", result)

    def test_question_list_is_paginated(self):
        class Inventory:
            def inventory(self):
                return {"qualifications": [{"id": "sample", "listGroupIds": ["2026"]}]}

            def group(self, qualification, list_group_id):
                questions = [
                    {
                        "id": f"question-{index}",
                        "listGroupId": list_group_id,
                        "body": f"問題{index}",
                        "questionLabel": f"問{index}",
                        "sourceQuestionKey": f"sample:2026:q{index}",
                        "issues": [],
                        "issueCodes": [],
                        "reviewStatus": "unreviewed",
                        "isLawRelated": False,
                        "workflow": {"firestore": "unread"},
                    }
                    for index in range(120)
                ]
                return {
                    "qualification": qualification,
                    "listGroupId": list_group_id,
                    "questionCount": len(questions),
                    "fingerprint": "fingerprint",
                    "questions": questions,
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.inventory = Inventory()
            app._decorate = lambda question: question
            app._summary = lambda question: dict(question)
            result = app._questions(
                {
                    "qualification": ["sample"],
                    "listGroupId": ["2026"],
                    "exceptionsOnly": ["false"],
                    "offset": ["50"],
                    "limit": ["50"],
                }
            )

        self.assertEqual(result["filteredCount"], 120)
        self.assertEqual(len(result["questions"]), 50)
        self.assertTrue(result["hasMore"])
        self.assertEqual(result["questions"][0]["id"], "question-50")

    def test_question_list_sorts_by_content_update_desc_by_default_and_can_reverse(self):
        class Inventory:
            def inventory(self):
                return {
                    "qualifications": [
                        {"id": "sample", "listGroupIds": ["2025", "2026"]}
                    ]
                }

            def group(self, qualification, list_group_id):
                timestamps = {
                    "2025": [
                        ("oldest", "2026-07-20T08:00:00+09:00"),
                        ("newest", "2026-07-26T08:00:00+09:00"),
                    ],
                    "2026": [
                        ("middle", "2026-07-23T08:00:00+09:00"),
                        ("missing", None),
                    ],
                }
                questions = [
                    {
                        "id": question_id,
                        "listGroupId": list_group_id,
                        "body": question_id,
                        "contentUpdatedAt": updated_at,
                        "issues": [],
                        "issueCodes": [],
                        "reviewStatus": "unreviewed",
                        "isLawRelated": False,
                        "workflow": {"firestore": "unread"},
                    }
                    for question_id, updated_at in timestamps[list_group_id]
                ]
                return {
                    "qualification": qualification,
                    "listGroupId": list_group_id,
                    "questionCount": len(questions),
                    "fingerprint": f"fingerprint-{list_group_id}",
                    "questions": questions,
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.inventory = Inventory()
            app._decorate = lambda question: question
            app._summary = lambda question: dict(question)
            base_query = {
                "qualification": ["sample"],
                "listGroupId": ["__all__"],
                "exceptionsOnly": ["false"],
            }

            descending = app._questions(base_query)
            ascending = app._questions({**base_query, "sort": ["updated_asc"]})
            second_page = app._questions({
                **base_query,
                "offset": ["1"],
                "limit": ["2"],
            })

        self.assertEqual(descending["sort"], "updated_desc")
        self.assertEqual(
            [question["id"] for question in descending["questions"]],
            ["newest", "middle", "oldest", "missing"],
        )
        self.assertEqual(
            [question["id"] for question in ascending["questions"]],
            ["oldest", "middle", "newest", "missing"],
        )
        self.assertEqual(
            [question["id"] for question in second_page["questions"]],
            ["middle", "oldest"],
        )

    def test_question_list_rejects_unknown_sort_order(self):
        class Inventory:
            def inventory(self):
                return {
                    "qualifications": [
                        {"id": "sample", "listGroupIds": ["2026"]}
                    ]
                }

            def group(self, qualification, list_group_id):
                return {
                    "qualification": qualification,
                    "listGroupId": list_group_id,
                    "questionCount": 0,
                    "fingerprint": "fingerprint",
                    "questions": [],
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.inventory = Inventory()
            with self.assertRaises(ApiError) as caught:
                app._questions(
                    {
                        "qualification": ["sample"],
                        "listGroupId": ["2026"],
                        "sort": ["question_number"],
                    }
                )

        self.assertEqual(caught.exception.status, 400)
        self.assertIn("updated_desc", str(caught.exception))

    def test_question_list_fast_page_stops_before_full_statistics(self):
        class Inventory:
            def inventory(self):
                return {
                    "qualifications": [
                        {"id": "sample", "listGroupIds": ["2026"]}
                    ]
                }

            def group(self, qualification, list_group_id):
                questions = [
                    {
                        "id": f"question-{index}",
                        "listGroupId": list_group_id,
                        "body": f"問題{index}",
                        "issues": [],
                        "issueCodes": [],
                        "reviewStatus": "unreviewed",
                        "isLawRelated": False,
                        "workflow": {"firestore": "unread"},
                    }
                    for index in range(10)
                ]
                return {
                    "qualification": qualification,
                    "listGroupId": list_group_id,
                    "questionCount": len(questions),
                    "fingerprint": "fingerprint",
                    "questions": questions,
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.inventory = Inventory()
            decorated_ids = []

            def decorate(question):
                decorated_ids.append(question["id"])
                return question

            app._decorate = decorate
            app._summary = lambda question: dict(question)
            result = app._questions(
                {
                    "qualification": ["sample"],
                    "listGroupId": ["2026"],
                    "exceptionsOnly": ["false"],
                    "includeStats": ["false"],
                    "limit": ["2"],
                }
            )

        self.assertEqual(decorated_ids, ["question-0", "question-1", "question-2"])
        self.assertEqual(
            [question["id"] for question in result["questions"]],
            ["question-0", "question-1"],
        )
        self.assertIsNone(result["filteredCount"])
        self.assertEqual(result["filteredCountLowerBound"], 3)
        self.assertFalse(result["statsIncluded"])
        self.assertTrue(result["hasMore"])
        self.assertIsNone(result["evaluationCounts"])

    def test_reflection_pending_filter_excludes_published_questions_with_warnings(self):
        class Inventory:
            def inventory(self):
                return {"qualifications": [{"id": "sample", "listGroupIds": ["2026"]}]}

            def group(self, qualification, list_group_id):
                return {
                    "qualification": qualification,
                    "listGroupId": list_group_id,
                    "questionCount": 2,
                    "fingerprint": "fingerprint",
                    "questions": [
                        {
                            "id": "published",
                            "listGroupId": list_group_id,
                            "body": "反映済み",
                            "issues": [{"code": "warning"}],
                            "issueCodes": ["warning"],
                            "reviewStatus": "approved",
                            "isLawRelated": False,
                            "workflow": {"firestore": "match"},
                            "evaluation": {"machineReady": True, "status": "passed"},
                        },
                        {
                            "id": "pending",
                            "listGroupId": list_group_id,
                            "body": "反映待ち",
                            "issues": [],
                            "issueCodes": [],
                            "reviewStatus": "approved",
                            "isLawRelated": False,
                            "workflow": {"firestore": "mismatch"},
                            "evaluation": {"machineReady": True, "status": "passed"},
                        },
                    ],
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.inventory = Inventory()
            app._decorate = lambda question: question
            app._summary = lambda question: dict(question)
            result = app._questions(
                {"qualification": ["sample"], "listGroupId": ["2026"]}
            )

        self.assertEqual([item["id"] for item in result["questions"]], ["pending"])

    def test_source_answer_difference_filter_includes_only_changed_questions(self):
        class Inventory:
            def inventory(self):
                return {"qualifications": [{"id": "sample", "listGroupIds": ["2026"]}]}

            def group(self, qualification, list_group_id):
                def question(question_id, different):
                    return {
                        "id": question_id,
                        "listGroupId": list_group_id,
                        "body": question_id,
                        "issues": [],
                        "issueCodes": [],
                        "reviewStatus": "approved",
                        "isLawRelated": False,
                        "workflow": {"firestore": "match"},
                        "evaluation": {"machineReady": True, "status": "passed"},
                        "sourceCorrectChoiceComparison": {"different": different},
                    }

                questions = [question("changed", True), question("same", False)]
                return {
                    "qualification": qualification,
                    "listGroupId": list_group_id,
                    "questionCount": len(questions),
                    "fingerprint": "fingerprint",
                    "questions": questions,
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.inventory = Inventory()
            app._decorate = lambda question: question
            app._summary = lambda question: dict(question)
            result = app._questions(
                {
                    "qualification": ["sample"],
                    "listGroupId": ["2026"],
                    "exceptionsOnly": ["false"],
                    "sourceAnswerDifference": ["true"],
                }
            )

        self.assertEqual(result["sourceAnswerDifferenceCount"], 1)
        self.assertEqual([item["id"] for item in result["questions"]], ["changed"])

    def test_question_list_filters_calculation_questions(self):
        class Inventory:
            def inventory(self):
                return {"qualifications": [{"id": "sample", "listGroupIds": ["2026"]}]}

            def group(self, qualification, list_group_id):
                def question(question_id, is_calculation):
                    return {
                        "id": question_id,
                        "listGroupId": list_group_id,
                        "body": question_id,
                        "issues": [],
                        "issueCodes": [],
                        "reviewStatus": "approved",
                        "isLawRelated": False,
                        "projected": {"isCalculationQuestion": is_calculation},
                        "workflow": {"firestore": "match"},
                        "evaluation": {"machineReady": True, "status": "passed"},
                    }

                questions = [question("calculation", True), question("knowledge", False)]
                return {
                    "qualification": qualification,
                    "listGroupId": list_group_id,
                    "questionCount": len(questions),
                    "fingerprint": "fingerprint",
                    "questions": questions,
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.inventory = Inventory()
            app._decorate = lambda question: question
            result = app._questions(
                {
                    "qualification": ["sample"],
                    "listGroupId": ["2026"],
                    "exceptionsOnly": ["false"],
                    "calculationOnly": ["true"],
                }
            )

        self.assertEqual(result["filteredCount"], 1)
        self.assertEqual([item["id"] for item in result["questions"]], ["calculation"])
        self.assertTrue(result["questions"][0]["isCalculationQuestion"])

    def test_question_list_filters_choices_extracted_from_question_body(self):
        class Inventory:
            def inventory(self):
                return {"qualifications": [{"id": "sample", "listGroupIds": ["2026"]}]}

            def group(self, qualification, list_group_id):
                def question(question_id, extracted):
                    return {
                        "id": question_id,
                        "listGroupId": list_group_id,
                        "body": question_id,
                        "issues": [],
                        "issueCodes": [],
                        "reviewStatus": "approved",
                        "isLawRelated": False,
                        "choicesExtractedFromQuestionBody": extracted,
                        "workflow": {"firestore": "match"},
                        "evaluation": {"machineReady": True, "status": "passed"},
                    }

                questions = [
                    question("question-body", True),
                    question("ordinary-choices", False),
                ]
                return {
                    "qualification": qualification,
                    "listGroupId": list_group_id,
                    "questionCount": len(questions),
                    "fingerprint": "fingerprint",
                    "questions": questions,
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.inventory = Inventory()
            app._decorate = lambda question: question
            result = app._questions(
                {
                    "qualification": ["sample"],
                    "listGroupId": ["2026"],
                    "exceptionsOnly": ["false"],
                    "questionBodyChoicesOnly": ["true"],
                }
            )

        self.assertEqual(result["questionBodyChoicesCount"], 1)
        self.assertEqual(result["filteredCount"], 1)
        self.assertEqual(
            [item["id"] for item in result["questions"]],
            ["question-body"],
        )
        self.assertTrue(result["questions"][0]["choicesExtractedFromQuestionBody"])

    def test_question_list_resolves_failed_deltas_once_per_group(self):
        class Inventory:
            def inventory(self):
                return {
                    "qualifications": [
                        {"id": "sample", "listGroupIds": ["2025", "2026"]}
                    ]
                }

            def group(self, qualification, list_group_id):
                questions = [
                    {
                        "id": f"question-{list_group_id}-{index}",
                        "listGroupId": list_group_id,
                        "body": f"問題{index}",
                        "questionLabel": f"問{index}",
                        "sourceQuestionKey": f"sample:{list_group_id}:q{index}",
                        "issues": [],
                        "issueCodes": [],
                        "reviewStatus": "unreviewed",
                        "isLawRelated": False,
                        "workflow": {"firestore": "unread"},
                    }
                    for index in range(60)
                ]
                return {
                    "qualification": qualification,
                    "listGroupId": list_group_id,
                    "questionCount": len(questions),
                    "fingerprint": f"fingerprint-{list_group_id}",
                    "questions": questions,
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.inventory = Inventory()
            app._decorate = lambda question: question
            app._summary = lambda question: dict(question)
            with patch(
                "tools.question_review_console.server.unresolved_failed_delta_paths",
                side_effect=lambda _root, _qualification, group: (f"{group}.json",),
            ) as resolver:
                result = app._questions(
                    {
                        "qualification": ["sample"],
                        "listGroupId": ["__all__"],
                        "exceptionsOnly": ["false"],
                        "limit": ["100"],
                    }
                )

        self.assertEqual(result["questionCount"], 120)
        self.assertEqual(len(result["questions"]), 100)
        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(
            [call.args[2] for call in resolver.call_args_list],
            ["2025", "2026"],
        )

    def test_question_list_filters_the_selected_stage_work_version(self):
        class Inventory:
            def inventory(self):
                return {
                    "qualifications": [
                        {"id": "sample", "listGroupIds": ["2026"]}
                    ]
                }

            def group(self, qualification, list_group_id):
                questions = []
                for status in ("current", "outdated", "unrecorded"):
                    questions.append(
                        {
                            "id": f"question-{status}",
                            "listGroupId": list_group_id,
                            "body": status,
                            "questionLabel": status,
                            "sourceQuestionKey": f"sample:2026:{status}",
                            "issues": [],
                            "issueCodes": [],
                            "reviewStatus": "unreviewed",
                            "isLawRelated": False,
                            "workflow": {"firestore": "unread"},
                            "workVersions": {
                                "stages": [
                                    {"id": "question_type", "status": status}
                                ]
                            },
                        }
                    )
                return {
                    "qualification": qualification,
                    "listGroupId": list_group_id,
                    "questionCount": len(questions),
                    "fingerprint": "fingerprint",
                    "questions": questions,
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.inventory = Inventory()
            app._decorate = lambda question: question
            app._summary = lambda question: dict(question)
            result = app._questions(
                {
                    "qualification": ["sample"],
                    "listGroupId": ["2026"],
                    "exceptionsOnly": ["false"],
                    "workStageId": ["question_type"],
                    "workVersionStatus": ["outdated"],
                }
            )

        self.assertEqual(result["filteredCount"], 1)
        self.assertEqual(result["questions"][0]["id"], "question-outdated")
        self.assertEqual(
            result["workVersionCounts"],
            {"current": 1, "outdated": 1, "unrecorded": 1},
        )

    def test_single_question_readback_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            with self.assertRaises(ApiError) as caught:
                app.post("/api/questions/question-1/live-readback", {})

        self.assertEqual(caught.exception.status, 422)
        self.assertIn("資格単位", str(caught.exception))

    def test_clears_live_results_only_for_changed_group(self):
        class Inventory:
            def group(self, qualification, list_group_id):
                self.request = (qualification, list_group_id)
                return {"questions": [{"id": "question-2024"}]}

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            inventory = Inventory()
            app.inventory = inventory
            app.live_results = {
                "question-2024": {"status": "match"},
                "question-2025": {"status": "match"},
            }

            app._clear_group_live_results("sample", "2024")

        self.assertEqual(inventory.request, ("sample", "2024"))
        self.assertNotIn("question-2024", app.live_results)
        self.assertIn("question-2025", app.live_results)

    def test_group_publish_is_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            for action in ("publish-preview", "publish"):
                with self.subTest(action=action), self.assertRaises(ApiError) as caught:
                    app.post(f"/api/groups/sample/2026/{action}", {})

                self.assertEqual(caught.exception.status, 422)
                self.assertIn("グループ単位の本番反映は無効", str(caught.exception))

    def test_tailscale_access_configuration_is_all_or_none_and_private(self):
        self.assertIsNone(build_tailscale_access(None))

        with self.assertRaisesRegex(ValueError, "すべて指定"):
            build_tailscale_access("https://mac.example.ts.net")
        with self.assertRaisesRegex(ValueError, "ts.net"):
            build_tailscale_access(
                "https://example.com",
                ["yuki@example.com"],
                ["100.101.102.103"],
            )
        with self.assertRaisesRegex(ValueError, "Tailscale端末IP"):
            build_tailscale_access(
                "https://mac.example.ts.net",
                ["yuki@example.com"],
                ["192.0.2.10"],
            )

        access = build_tailscale_access(
            "https://MAC.EXAMPLE.ts.net/",
            ["YUKI@example.com"],
            ["100.101.102.103", "fd7a:115c:a1e0::1234"],
        )
        self.assertIsNotNone(access)
        self.assertEqual(access.origin, "https://mac.example.ts.net")
        self.assertEqual(access.logins, {"yuki@example.com"})
        self.assertEqual(len(access.source_ips), 2)

    def test_remote_route_requires_tailscale_identity_device_and_origin(self):
        access = build_tailscale_access(
            "https://review-mac.example.ts.net",
            ["yuki@example.com"],
            ["100.101.102.103"],
        )
        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(
                Path(directory),
                tailscale_access=access,
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), QuestionReviewRequestHandler)
            server.app = app
            port = int(server.server_address[1])
            app.set_origin("127.0.0.1", port)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            remote_headers = {
                "Host": "review-mac.example.ts.net",
                "Tailscale-User-Login": "yuki@example.com",
                "Tailscale-Headers-Info": "https://tailscale.com/s/serve-headers",
                "X-Forwarded-For": "100.101.102.103",
                "X-Forwarded-Host": "review-mac.example.ts.net",
                "X-Forwarded-Proto": "https",
            }
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request(
                    "GET",
                    "/api/session",
                    headers={"Host": "review-mac.example.ts.net"},
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                connection.close()

                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request("GET", "/api/session", headers=remote_headers)
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    response.getheader("Strict-Transport-Security"),
                    "max-age=31536000",
                )
                session = json.loads(response.read())
                self.assertEqual(session["sessionToken"], app.session_token)
                self.assertEqual(
                    session["uiContractVersion"],
                    "question-review-ui/v4",
                )
                self.assertEqual(session["questionContentApiVersion"], 1)
                connection.close()

                wrong_device_headers = {
                    **remote_headers,
                    "X-Forwarded-For": "100.101.102.104",
                }
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request("GET", "/api/session", headers=wrong_device_headers)
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                connection.close()

                wrong_login_headers = {
                    **remote_headers,
                    "Tailscale-User-Login": "other@example.com",
                }
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request("GET", "/api/session", headers=wrong_login_headers)
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                connection.close()

                funnel_headers = {
                    **remote_headers,
                    "Tailscale-Funnel-Request": "?1",
                }
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request("GET", "/", headers=funnel_headers)
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                connection.close()

                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request(
                    "POST",
                    "/api/direct-edits/preview",
                    body="{}",
                    headers={
                        **remote_headers,
                        "Content-Type": "application/json",
                        "Origin": "https://example.invalid",
                        "X-Review-Session": app.session_token,
                    },
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                self.assertIn("Origin", json.loads(response.read())["error"])
                connection.close()

                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request(
                    "POST",
                    "/api/direct-edits/preview",
                    body="{}",
                    headers={
                        **remote_headers,
                        "Content-Type": "application/json",
                        "Origin": access.origin,
                        "X-Review-Session": app.session_token,
                    },
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                self.assertIn("questionId", json.loads(response.read())["error"])
                connection.close()

                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.putrequest("GET", "/api/session", skip_host=True)
                for header, value in remote_headers.items():
                    if header != "Tailscale-User-Login":
                        connection.putheader(header, value)
                connection.putheader("Tailscale-User-Login", "yuki@example.com")
                connection.putheader("Tailscale-User-Login", "other@example.com")
                connection.endheaders()
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_unknown_host_cannot_read_session(self):
        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            server = ThreadingHTTPServer(("127.0.0.1", 0), QuestionReviewRequestHandler)
            server.app = app
            port = int(server.server_address[1])
            app.set_origin("127.0.0.1", port)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request("GET", "/api/session")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIsNone(response.getheader("Strict-Transport-Security"))
                response.read()
                connection.close()

                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request(
                    "GET",
                    "/api/session",
                    headers={"Host": "example.invalid"},
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                self.assertIn("アクセス経路", json.loads(response.read())["error"])
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_mutation_api_requires_session_token_and_local_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            server = ThreadingHTTPServer(("127.0.0.1", 0), QuestionReviewRequestHandler)
            server.app = app
            port = int(server.server_address[1])
            app.set_origin("127.0.0.1", port)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request(
                    "POST",
                    "/api/direct-edits/preview",
                    body="{}",
                    headers={"Content-Type": "application/json", "Origin": app.origin},
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                self.assertIn("session token", json.loads(response.read())["error"])
                connection.close()

                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request(
                    "POST",
                    "/api/direct-edits/preview",
                    body="{}",
                    headers={
                        "Content-Type": "application/json",
                        "Origin": "https://example.invalid",
                        "X-Review-Session": app.session_token,
                    },
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                self.assertIn("Origin", json.loads(response.read())["error"])
                connection.close()

                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request(
                    "POST",
                    "/api/direct-edits/preview",
                    body="{}",
                    headers={
                        "Content-Type": "application/json",
                        "Origin": app.origin,
                        "X-Review-Session": app.session_token,
                    },
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                self.assertIn("questionId", json.loads(response.read())["error"])
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_monitor_http_routes_preserve_security_and_never_call_app_server(self):
        class NoRpcAppServer:
            provider = "test"
            configured = True

            def __init__(self):
                self.rpc_calls = 0
                self.observer = None

            def set_event_observer(self, observer):
                self.observer = observer

            def public_status(self, *args, **kwargs):
                self.rpc_calls += 1
                raise AssertionError("monitor GET must not call App Server")

            def run_turn(self, *args, **kwargs):
                self.rpc_calls += 1
                raise AssertionError("monitor GET must not start a turn")

            def close(self):
                return None

        class ReadOnlyHub:
            def __init__(self):
                self.health_calls = 0
                self.snapshot_calls = 0

            def health(self, qualification, run_id):
                self.health_calls += 1
                return {"status": "healthy"}

            def snapshot(self, qualification, run_id):
                self.snapshot_calls += 1
                raise AssertionError("monitor snapshot must use lightweight health")

            def events(self, qualification, run_id, *, after, limit, wait_ms):
                return {
                    "schemaVersion": "monitor-event/v1",
                    "events": [],
                    "cursor": after,
                }

            def close(self):
                return None

        access = build_tailscale_access(
            "https://review-mac.example.ts.net",
            ["yuki@example.com"],
            ["100.101.102.103"],
        )
        with tempfile.TemporaryDirectory() as directory:
            repo_root = Path(directory)
            app_server = NoRpcAppServer()
            monitor_hub = ReadOnlyHub()
            app = QuestionReviewApplication(
                repo_root,
                tailscale_access=access,
                app_server=app_server,
                monitor_event_hub=monitor_hub,
            )
            manifest_path = (
                app.run_store.root / "demo" / "run-1" / "manifest.json"
            )
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps(
                    {
                        "runId": "run-1",
                        "qualification": "demo",
                        "status": "running",
                        "receiptValidated": False,
                        "artifactSync": {"status": "pending"},
                        "childRunIds": [],
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), QuestionReviewRequestHandler
            )
            server.app = app
            port = int(server.server_address[1])
            app.set_origin("127.0.0.1", port)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            local_headers = {"Host": f"127.0.0.1:{port}"}
            remote_headers = {
                "Host": "review-mac.example.ts.net",
                "Tailscale-User-Login": "yuki@example.com",
                "Tailscale-Headers-Info": "https://tailscale.com/s/serve-headers",
                "X-Forwarded-For": "100.101.102.103",
                "X-Forwarded-Host": "review-mac.example.ts.net",
                "X-Forwarded-Proto": "https",
            }
            try:
                for resource in (
                    "/api/monitor/v1/runs?qualification=demo",
                    "/api/monitor/v1/runs/run-1/snapshot?qualification=demo",
                    "/api/monitor/v1/runs/run-1/events?qualification=demo",
                    "/api/monitor/v1/runs/run-1/artifacts?qualification=demo",
                ):
                    with self.subTest(resource=resource):
                        connection = http.client.HTTPConnection(
                            "127.0.0.1", port, timeout=5
                        )
                        connection.request("GET", resource, headers=local_headers)
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.getheader("Cache-Control"), "no-store")
                        self.assertIn(
                            "default-src 'self'",
                            response.getheader("Content-Security-Policy"),
                        )
                        payload = json.loads(response.read())
                        self.assertEqual(payload["monitorModelRequests"], 0)
                        if resource.endswith("artifacts?qualification=demo"):
                            self.assertNotIn("pagination", payload)
                        connection.close()

                connection = http.client.HTTPConnection(
                    "127.0.0.1", port, timeout=5
                )
                connection.request(
                    "GET",
                    (
                        "/api/monitor/v1/runs/run-1/artifacts"
                        "?qualification=demo&limit=64"
                    ),
                    headers=local_headers,
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                paged_payload = json.loads(response.read())
                self.assertIn("pagination", paged_payload)
                self.assertEqual(
                    paged_payload["pagination"]["limit"],
                    64,
                )
                connection.close()

                def read_snapshot_under_load(_index):
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", port, timeout=10
                    )
                    try:
                        connection.request(
                            "GET",
                            "/api/monitor/v1/runs/run-1/snapshot?qualification=demo",
                            headers=local_headers,
                        )
                        response = connection.getresponse()
                        response.read()
                        return response.status
                    finally:
                        connection.close()

                with ThreadPoolExecutor(max_workers=32) as executor:
                    statuses = list(executor.map(read_snapshot_under_load, range(64)))
                self.assertEqual(statuses, [200] * 64)
                self.assertEqual(app_server.rpc_calls, 0)
                self.assertEqual(monitor_hub.snapshot_calls, 0)
                self.assertGreaterEqual(monitor_hub.health_calls, 64)

                connection = http.client.HTTPConnection(
                    "127.0.0.1", port, timeout=5
                )
                connection.request(
                    "GET",
                    "/api/monitor/v1/runs/run-1/snapshot?qualification=demo",
                    headers=remote_headers,
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    response.getheader("Strict-Transport-Security"),
                    "max-age=31536000",
                )
                response.read()
                connection.close()

                connection = http.client.HTTPConnection(
                    "127.0.0.1", port, timeout=5
                )
                connection.request(
                    "GET",
                    "/api/monitor/v1/runs/run-1/snapshot?qualification=demo",
                    headers={"Host": "example.invalid"},
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                connection.close()

                connection = http.client.HTTPConnection(
                    "127.0.0.1", port, timeout=5
                )
                connection.request(
                    "GET",
                    "/api/monitor/v1/runs/missing/snapshot?qualification=demo",
                    headers=local_headers,
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 404)
                response.read()
                connection.close()

                for method in ("POST", "PUT", "PATCH", "DELETE"):
                    with self.subTest(method=method):
                        connection = http.client.HTTPConnection(
                            "127.0.0.1", port, timeout=5
                        )
                        connection.request(
                            method,
                            "/api/monitor/v1/runs",
                            body="{}",
                            headers=local_headers,
                        )
                        response = connection.getresponse()
                        self.assertEqual(response.status, 405)
                        self.assertEqual(
                            response.getheader("Cache-Control"), "no-store"
                        )
                        response.read()
                        connection.close()

                self.assertEqual(app_server.rpc_calls, 0)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                app.close()


if __name__ == "__main__":
    unittest.main()
