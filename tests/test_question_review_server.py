import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from tools.question_review_console.server import (
    ApiError,
    QuestionReviewApplication,
    QuestionReviewRequestHandler,
    build_tailscale_access,
)


class QuestionReviewServerTests(unittest.TestCase):
    def test_serves_qualification_workflow_and_stage_prompt(self):
        class Workflow:
            def overview(self, qualification):
                return {"qualification": qualification, "nextStageId": "question_type"}

            def prompt(self, qualification, stage_id, mode="remaining"):
                return {
                    "qualification": qualification,
                    "stageId": stage_id,
                    "mode": mode,
                    "prompt": "依頼",
                }

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.qualification_workflow = Workflow()
            get_status, overview = app.get(
                "/api/qualification-workflow", {"qualification": ["sample"]}
            )
            post_status, prompt = app.post(
                "/api/qualification-workflow/prompt",
                {"qualification": "sample", "stageId": "question_type"},
            )

        self.assertEqual(get_status, 200)
        self.assertEqual(overview["nextStageId"], "question_type")
        self.assertEqual(post_status, 200)
        self.assertEqual(prompt["prompt"], "依頼")

    def test_previews_starts_and_resumes_qualification_run(self):
        class Runs:
            def preview(self, qualification, stage_id, mode, *, resumed_from=None):
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
                resumed_from=None,
            ):
                return {
                    "run": {"runId": "run-1", "qualification": qualification},
                    "prompt": "依頼",
                    "job": None,
                }

            def resume_prompt(self, qualification, run_id):
                return {"run": {"runId": run_id}, "prompt": "依頼"}

            def recent(self, qualification):
                return {"qualification": qualification, "runs": []}

        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            app.qualification_runs = Runs()
            _, preview = app.post(
                "/api/qualification-runs/preview",
                {"qualification": "sample", "stageId": "law_audit", "mode": "attention"},
            )
            start_status, started = app.post(
                "/api/qualification-runs/start",
                {
                    "qualification": "sample",
                    "stageId": "law_audit",
                    "mode": "attention",
                    "previewToken": "token",
                },
            )
            _, resumed = app.post(
                "/api/qualification-runs/resume-prompt",
                {"qualification": "sample", "runId": "run-1"},
            )
            _, recent = app.get(
                "/api/qualification-runs", {"qualification": ["sample"]}
            )

        self.assertEqual(preview["mode"], "attention")
        self.assertEqual(start_status, 201)
        self.assertEqual(started["run"]["runId"], "run-1")
        self.assertEqual(resumed["prompt"], "依頼")
        self.assertEqual(recent["qualification"], "sample")

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
        self.assertEqual(len(review["targetFiles"]), 1)

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
                            "sourceStem": f"question_{list_group_id}_1",
                            "issueCodes": ["law_audit_metadata_incomplete"],
                            "paths": {"patches": [path]},
                        },
                        {
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

        self.assertEqual(len(paths), 2)
        self.assertTrue(all("21_explanationText_added" in path for path in paths))
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

    def test_production_publish_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            with self.assertRaises(ApiError) as caught:
                app.post("/api/groups/sample/2026/publish", {})

        self.assertEqual(caught.exception.status, 422)
        self.assertIn("本番反映の確認", str(caught.exception))

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
                self.assertEqual(
                    json.loads(response.read())["sessionToken"], app.session_token
                )
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


if __name__ == "__main__":
    unittest.main()
