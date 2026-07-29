import copy
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from scripts.upload.upload_questions_to_firestore import build_doc_data_base
from tools.question_review_console.evaluation import (
    EvaluationError,
    QuestionEvaluationService,
)
from tools.question_review_console.codex_app_server import AppServerTurnResult
from tools.question_review_console.publisher import PublicationError, QuestionPublisher


def question_payload(*, question_id="api-q1", body="問題1", state_hash="state-1"):
    documents = [
        upload_document("doc-1", "original-1", "選択肢A", "正しい"),
        upload_document("doc-2", "original-1", "選択肢B", "間違い"),
    ]
    return {
        "id": question_id,
        "reviewKey": f"sample:2026:question_1:{question_id}",
        "sourceQuestionKey": f"sample:{question_id}",
        "qualification": "sample",
        "publicationQualificationId": "sample",
        "listGroupId": "2026",
        "originalQuestionId": "original-1",
        "questionLabel": body,
        "body": body,
        "choiceCount": 2,
        "stateHash": state_hash,
        "issueCodes": [],
        "workflow": {"merge": "match", "convert": "match", "upload": "match"},
        "projected": {
            "questionBodyText": body,
            "questionType": "true_false",
            "questionIntent": "select_correct",
            "choiceTextList": ["選択肢A", "選択肢B"],
            "correctChoiceText": ["正しい", "間違い"],
            "answer_result_text": "正解は1",
            "explanationText": ["Aの解説", "Bの解説"],
        },
        "uploadReadyDocs": documents,
        "paths": {
            "source": "output/sample/questions_json/2026/00_source/question_1.json",
            "uploadReady": (
                "output/sample/questions_json/upload_to_firestore/"
                "2026_firestore_20260714_120000.json"
            ),
        },
    }


def evaluation_result(*, first_verdict="true", status="passed"):
    return {
        "status": status,
        "explanationScore": 94,
        "criticalIssues": [],
        "summary": "全選択肢の正誤と解説を確認した。",
        "choiceEvaluations": [
            {
                "choiceIndex": 0,
                "verdict": first_verdict,
                "reason": "一次資料と一致する。",
                "evidence": [
                    {
                        "source": "公式資料",
                        "locator": "第1章 1頁",
                        "summary": "選択肢Aを裏付ける。",
                    }
                ],
            },
            {
                "choiceIndex": 1,
                "verdict": "false",
                "reason": "定義と一致しない。",
                "evidence": [
                    {
                        "source": "公式資料",
                        "locator": "第1章 2頁",
                        "summary": "選択肢Bの誤りを示す。",
                    }
                ],
            },
        ],
        "reworkItems": [],
    }


def upload_document(question_id, original_id, choice, verdict):
    return {
        "questionId": question_id,
        "originalQuestionId": original_id,
        "originalQuestionBodyText": "問題1",
        "originalQuestionChoiceText": choice,
        "questionBodyText": "問題1",
        "questionSetId": "set-1",
        "questionText": f"問題1 {choice}",
        "questionType": "true_false",
        "qualificationId": "sample",
        "listGroupId": "2026",
        "correctChoiceText": verdict,
        "explanationText": f"{choice}の解説",
        "examYear": 2026,
        "examSource": "サンプル資格 2026年",
        "questionTags": [],
        "isOfficial": True,
        "isDeleted": False,
        "isChoiceOnly": False,
        "isGroupable": True,
    }


class QuestionEvaluationServiceTests(unittest.TestCase):
    def test_status_uses_precomputed_failed_delta_paths_without_rescanning_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=lambda _prompt: evaluation_result(),
            )
            with patch(
                "tools.question_review_console.evaluation.unresolved_failed_delta_paths",
                side_effect=AssertionError("manifestを再走査しない"),
            ):
                status = service.status_for(
                    question_payload(),
                    failed_delta_paths=(),
                )

        self.assertEqual(status["failedDeltaPaths"], [])
        self.assertTrue(status["machineReady"])

    def test_precomputed_failed_delta_paths_still_block_evaluation(self):
        failed_path = "output/sample/questions_json/2026/21_explanationText_added/partial.json"
        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=lambda _prompt: evaluation_result(),
            )
            status = service.status_for(
                question_payload(),
                failed_delta_paths=(failed_path,),
            )

        self.assertEqual(status["failedDeltaPaths"], [failed_path])
        self.assertFalse(status["machineReady"])

    def test_failed_app_server_turn_keeps_session_trace(self):
        class FailingAppServer:
            configured = True
            provider = "Codex App Server"

            def run_turn(self, _prompt, **kwargs):
                kwargs["on_thread_started"]("thread-failed", "session-failed")
                kwargs["on_turn_started"]("thread-failed", "turn-failed")
                raise RuntimeError("turn failed")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = QuestionEvaluationService(
                root,
                "secret",
                app_server=FailingAppServer(),
            )
            question = question_payload()
            preview = service.preview(question)

            with self.assertRaisesRegex(RuntimeError, "turn failed"):
                service.run(question, preview["previewToken"], lambda _line: None)

            manifest_path = next(
                (
                    root
                    / "output"
                    / "question_review_console"
                    / "workflow_runs"
                    / "sample"
                ).glob("*/manifest.json")
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["sessionId"], "session-failed")
        self.assertEqual(manifest["threadId"], "thread-failed")
        self.assertEqual(manifest["turnId"], "turn-failed")

    def test_app_server_evaluation_saves_real_thread_receipt_in_isolated_cwd(self):
        class FakeAppServer:
            configured = True
            provider = "Codex App Server"

            def __init__(self):
                self.calls = []

            def run_turn(self, prompt, **kwargs):
                self.calls.append((prompt, kwargs))
                kwargs["on_thread_started"](
                    "thread-evaluation-1", "session-evaluation-1"
                )
                kwargs["on_turn_started"](
                    "thread-evaluation-1", "turn-evaluation-1"
                )
                return AppServerTurnResult(
                    thread_id="thread-evaluation-1",
                    session_id="session-evaluation-1",
                    turn_id="turn-evaluation-1",
                    final_message=json.dumps(evaluation_result(), ensure_ascii=False),
                    model="gpt-test",
                    service_tier=None,
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = FakeAppServer()
            service = QuestionEvaluationService(
                root,
                "secret",
                app_server=app_server,
            )
            question = question_payload()
            preview = service.preview(question)
            result = service.run(
                question, preview["previewToken"], lambda _line: None
            )["evaluation"]
            manifest = json.loads(
                (
                    root
                    / "output"
                    / "question_review_console"
                    / "workflow_runs"
                    / "sample"
                    / result["runId"]
                    / "manifest.json"
                ).read_text(encoding="utf-8")
            )
            receipt_path = root / manifest["resultReceiptPath"]
            receipt_exists = receipt_path.is_file()
            receipt_path.unlink()
            missing_receipt_status = service.status_for(question)

        self.assertEqual(result["threadId"], "thread-evaluation-1")
        self.assertEqual(result["turnId"], "turn-evaluation-1")
        self.assertEqual(result["sessionId"], "session-evaluation-1")
        self.assertEqual(manifest["workType"], "evaluation")
        self.assertEqual(manifest["sandbox"], "read-only")
        self.assertEqual(manifest["threadId"], "thread-evaluation-1")
        self.assertEqual(manifest["sessionId"], "session-evaluation-1")
        self.assertEqual(manifest["turnId"], "turn-evaluation-1")
        self.assertEqual(manifest["model"], "gpt-test")
        self.assertIsNone(manifest["serviceTier"])
        self.assertEqual(manifest["reasoningEffort"], "high")
        self.assertTrue(receipt_exists)
        self.assertEqual(missing_receipt_status["status"], "stale")
        self.assertFalse(missing_receipt_status["publishReady"])
        prompt, kwargs = app_server.calls[0]
        self.assertEqual(kwargs["sandbox"], "read-only")
        self.assertNotEqual(Path(kwargs["cwd"]), root)
        self.assertEqual(
            kwargs["output_schema"]["properties"]["choiceEvaluations"]["minItems"],
            2,
        )
        self.assertEqual(
            kwargs["output_schema"]["properties"]["choiceEvaluations"]["maxItems"],
            2,
        )
        self.assertNotIn("turn_timeout", kwargs)
        self.assertNotIn('"paths"', prompt)
        self.assertEqual(
            kwargs["monitor_context"],
            {
                "qualification": "sample",
                "runId": result["runId"],
                "questionId": "api-q1",
                "questionIds": ["api-q1"],
                "workItemKey": "api-q1",
                "workItemKeys": ["api-q1"],
                "listGroupIds": ["2026"],
                "stageId": "evaluation",
                "workType": "evaluation",
                "phase": "evaluation",
            },
        )

    def test_output_schema_uses_supported_structured_output_keywords(self):
        schema_path = (
            Path(__file__).resolve().parents[1]
            / "tools"
            / "question_review_console"
            / "evaluation_result.schema.json"
        )
        schema_text = json.dumps(json.loads(schema_path.read_text(encoding="utf-8")))

        self.assertNotIn('"uniqueItems"', schema_text)
        self.assertIn("Truth value of the choice statement itself", schema_text)

    def test_prompt_defines_verdict_as_the_choice_statement_truth_value(self):
        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=lambda _prompt: evaluation_result(),
            )
            prompt = service._build_prompt(question_payload())

        self.assertIn("選択肢の記述自体が事実として正しければtrue", prompt)
        self.assertIn('"questionType": "true_false"', prompt)
        self.assertIn("各選択肢は公開時に独立した○×問題", prompt)
        self.assertIn("複数のtrue又はfalseがあることだけをcriticalIssues", prompt)
        self.assertIn("公式問題のquestionTypeはtrue_false、flash_card、group_choiceの3分類だけ", prompt)
        self.assertIn("single_choiceとfill_in_blankはユーザー作成問題用", prompt)
        self.assertIn("計算結果を問題文の条件から一意に求める問題はflash_card", prompt)
        self.assertIn("複数選択形式と誤解して分類変更を求めない", prompt)
        self.assertIn("isCalculationQuestionは計算過程が主要な学習対象かを表し", prompt)
        self.assertIn("questionTypeとは独立に評価", prompt)
        self.assertIn("api/2/law_data/{lawId}?response_format=json", prompt)
        self.assertIn("tagがArticleかつattr.Numが対象条番号に一致", prompt)
        self.assertIn("--retry 3 --retry-all-errors", prompt)
        self.assertIn('.attr.Num? == \"45\"', prompt)
        self.assertIn("jq式全体を一組のsingle quote内に保つ", prompt)
        self.assertIn("`head`で法令JSONの先頭だけを読んで確認完了にしない", prompt)
        self.assertIn("elaws.e-gov.go.jp/document?lawid={lawId}", prompt)
        self.assertIn("一つの公式URLへの一時的な通信失敗だけでinsufficient_evidenceにせず", prompt)
        self.assertIn("隔離workspaceにはrepository fileがない", prompt)
        self.assertIn("`placeholder`、`N/A`", prompt)
        self.assertIn("現在の正答対応は意図的に渡されていない", prompt)
        self.assertIn("sourceAnswerEvidenceがある場合", prompt)
        self.assertIn("非法令問題のcurrentExplanationText", prompt)
        self.assertIn("減点又は要再整備理由にしない", prompt)
        self.assertIn("正しい定義・基準と条文位置", prompt)
        self.assertIn("その後に選択肢との差", prompt)
        self.assertIn("02: questionIntentだけ", prompt)
        self.assertIn("03: explanationTextだけ", prompt)
        self.assertIn("03b: lawReferences、lawRevisionFacts", prompt)
        self.assertIn("法令の根拠、改正、現行法判定の問題を02へ入れない", prompt)
        self.assertNotIn("currentCorrectChoiceText", prompt)
        self.assertNotIn("officialAnswer", prompt)

    def test_prompt_includes_exact_trusted_source_answer_as_separate_evidence(self):
        question = question_payload()
        question["sourceRecordRef"] = "question_2025_2.json#13"
        question["source"] = {
            "questionBodyText": question["projected"]["questionBodyText"],
            "choiceTextList": copy.deepcopy(
                question["projected"]["choiceTextList"]
            ),
            "correctChoiceText": ["正しい", "間違い"],
            "answer_result_text": "正解は 5 です。",
            "sourceProvider": "gassyunin.com",
            "sourceOrigin": "gassyunin_site",
            "choiceMarkerSource": "judge",
            "markerAlignmentMode": "judge_only",
            "markerMismatchDetected": False,
            "answerResultNumbersRemapped": False,
            "judgeChoiceMarkers": ["イ", "ロ"],
            "sourceStatementCount": 2,
        }
        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=lambda _prompt: evaluation_result(),
            )
            prompt = service._build_prompt(question)

        self.assertIn('"sourceAnswerEvidence"', prompt)
        self.assertIn(
            '"verdictSemantics": "final_correct_choice_text_for_source_text"',
            prompt,
        )
        self.assertIn(
            "同じ年度・資格・種別・科目・問番号の公式問題冊子と公式解答",
            prompt,
        )

    def test_saves_passed_result_and_marks_it_stale_after_question_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = QuestionEvaluationService(
                root,
                "secret",
                result_runner=lambda _prompt: evaluation_result(),
            )
            question = question_payload()
            preview = service.preview(question)
            result = service.run(
                question, preview["previewToken"], lambda _line: None
            )["evaluation"]

            current = service.status_for(question)
            version_record = service.work_versions.record_for(question)
            changed = copy.deepcopy(question)
            changed["stateHash"] = "state-2"
            stale = service.status_for(changed)

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["verifiedChoiceCount"], 2)
        self.assertTrue(current["publishReady"])
        self.assertEqual(version_record["stages"]["evaluation"]["version"], "2.1")
        self.assertEqual(stale["status"], "stale")
        self.assertFalse(stale["publishReady"])

    def test_evaluation_freshness_uses_version_not_document_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=lambda _prompt: evaluation_result(),
            )
            question = question_payload()
            preview = service.preview(question)
            service.run(question, preview["previewToken"], lambda _line: None)
            original_policy = service.current_policy()
            service.current_policy = lambda: {
                **original_policy,
                "policyFingerprint": "non-semantic-document-change",
            }
            same_version = service.status_for(question)
            service.current_policy = lambda: {
                **original_policy,
                "policyVersion": "2.1",
                "policyFingerprint": "new-evaluation-policy",
            }
            minor_version = service.status_for(question)
            service.current_policy = lambda: {
                **original_policy,
                "policyVersion": "3.0",
                "policyFingerprint": "breaking-evaluation-policy",
            }
            next_major = service.status_for(question)

        self.assertEqual(same_version["status"], "passed")
        self.assertEqual(minor_version["status"], "passed")
        self.assertEqual(next_major["status"], "stale")

    def test_current_work_policy_is_required_before_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=lambda _prompt: evaluation_result(),
            )
            question = question_payload()
            question["workVersions"] = {
                "allCurrent": False,
                "outdatedStageIds": ["question_type"],
                "unrecordedStageIds": [],
            }

            status = service.status_for(question)
            preview = service.preview(question)

        self.assertFalse(status["policyReady"])
        self.assertFalse(status["machineReady"])
        self.assertFalse(preview["canEvaluate"])

    def test_server_recomputes_failure_when_reported_pass_disagrees_with_current_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=lambda _prompt: evaluation_result(
                    first_verdict="false", status="passed"
                ),
            )
            question = question_payload()
            preview = service.preview(question)
            result = service.run(
                question, preview["previewToken"], lambda _line: None
            )["evaluation"]

        self.assertEqual(result["reportedStatus"], "passed")
        self.assertEqual(result["status"], "needs_rework")
        self.assertFalse(result["answerMappingMatched"])
        self.assertFalse(result["choiceEvaluations"][0]["matchesCurrent"])

    def test_tampered_evaluation_result_is_not_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = QuestionEvaluationService(
                root,
                "secret",
                result_runner=lambda _prompt: evaluation_result(),
            )
            question = question_payload()
            preview = service.preview(question)
            service.run(question, preview["previewToken"], lambda _line: None)
            path = service.store.evaluation_path(question)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["explanationScore"] = 0
            path.write_text(json.dumps(payload), encoding="utf-8")

            status = service.status_for(question)

        self.assertEqual(status["status"], "not_started")
        self.assertFalse(status["publishReady"])

    def test_batch_uses_a_separate_runner_call_per_question_and_continues_after_failure(self):
        calls = []

        def runner(prompt):
            calls.append(prompt)
            if "問題2" in prompt:
                raise RuntimeError("evaluation failed")
            return evaluation_result()

        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory), "secret", result_runner=runner
            )
            first = question_payload()
            second = question_payload(
                question_id="api-q2", body="問題2", state_hash="state-2"
            )
            second["reviewKey"] = "sample:2026:question_2:api-q2"
            preview = service.preview_many([first, second])
            result = service.run_many(
                [first, second], preview["previewToken"], lambda _line: None
            )

        self.assertEqual(preview["sessionCount"], 2)
        self.assertEqual(preview["evaluationConcurrencyLimit"], 100)
        self.assertEqual(preview["qualification"], "sample")
        self.assertEqual(preview["listGroupIds"], ["2026"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["completedCount"], 1)
        self.assertEqual(result["failedCount"], 1)
        self.assertEqual(result["passedCount"], 1)

    def test_batch_runs_sessions_in_parallel_and_preserves_result_order(self):
        active = 0
        max_active = 0
        lock = threading.Lock()
        barrier = threading.Barrier(4)

        def runner(_prompt):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            barrier.wait(timeout=5)
            time.sleep(0.01)
            with lock:
                active -= 1
            return evaluation_result()

        questions = []
        for index in range(4):
            question = question_payload(
                question_id=f"api-q{index + 1}",
                body=f"問題{index + 1}",
                state_hash=f"state-{index + 1}",
            )
            question["reviewKey"] = (
                f"sample:2026:question_{index + 1}:api-q{index + 1}"
            )
            questions.append(question)

        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=runner,
            )
            preview = service.preview_many(questions)
            result = service.run_many(
                questions, preview["previewToken"], lambda _line: None
            )

        self.assertEqual(max_active, 4)
        self.assertEqual(
            [item["questionId"] for item in result["results"]],
            [question["id"] for question in questions],
        )

    def test_continuous_queue_refills_workers_until_all_questions_finish(self):
        active = 0
        max_active = 0
        call_count = 0
        lock = threading.Lock()
        first_wave_started = threading.Event()
        release = threading.Event()

        def runner(_prompt):
            nonlocal active, max_active, call_count
            with lock:
                active += 1
                call_count += 1
                max_active = max(max_active, active)
                if call_count >= 3:
                    first_wave_started.set()
            release.wait(timeout=5)
            time.sleep(0.01)
            with lock:
                active -= 1
            return evaluation_result()

        questions = []
        for index in range(7):
            question = question_payload(
                question_id=f"api-q{index + 1}",
                body=f"問題{index + 1}",
                state_hash=f"state-{index + 1}",
            )
            question["reviewKey"] = (
                f"sample:2026:question_{index + 1}:api-q{index + 1}"
            )
            questions.append(question)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "tools.question_review_console.evaluation."
                "MAX_EVALUATION_CONCURRENCY",
                3,
            ),
            ThreadPoolExecutor(max_workers=1) as executor,
        ):
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=runner,
            )
            preview = service.preview_many(
                questions,
                continuous_queue=True,
            )
            future = executor.submit(
                service.run_many,
                questions,
                preview["previewToken"],
                lambda _line: None,
                continuous_queue=True,
            )
            self.assertTrue(first_wave_started.wait(timeout=5))
            self.assertEqual(max_active, 3)
            release.set()
            result = future.result(timeout=5)

        self.assertTrue(preview["continuousQueue"])
        self.assertEqual(preview["evaluationConcurrencyLimit"], 3)
        self.assertEqual(call_count, 7)
        self.assertEqual(max_active, 3)
        self.assertEqual(result["completedCount"], 7)
        self.assertEqual(result["passedCount"], 7)

    def test_continuous_queue_can_exceed_manual_selection_limit(self):
        questions = []
        for index in range(101):
            question = question_payload(
                question_id=f"api-q{index + 1}",
                body=f"問題{index + 1}",
                state_hash=f"state-{index + 1}",
            )
            question["reviewKey"] = (
                f"sample:2026:question_{index + 1}:api-q{index + 1}"
            )
            questions.append(question)

        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=lambda _prompt: evaluation_result(),
            )
            with self.assertRaisesRegex(EvaluationError, "100問まで"):
                service.preview_many(questions)
            preview = service.preview_many(
                questions,
                continuous_queue=True,
            )

        self.assertEqual(preview["selectedCount"], 101)
        self.assertEqual(preview["sessionCount"], 101)
        self.assertTrue(preview["continuousQueue"])

    def test_batch_retries_once_when_all_choice_evidence_is_incomplete(self):
        calls = 0
        prompts = []

        def runner(prompt):
            nonlocal calls
            calls += 1
            prompts.append(prompt)
            result = evaluation_result()
            if calls == 1:
                result["status"] = "needs_rework"
                result["explanationScore"] = 0
                result["criticalIssues"] = ["全選択肢の根拠確認が未完了。"]
                for choice in result["choiceEvaluations"]:
                    choice["verdict"] = "insufficient_evidence"
            return result

        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=runner,
            )
            question = question_payload()
            preview = service.preview_many([question])
            result = service.run_many(
                [question], preview["previewToken"], lambda _line: None
            )

        self.assertEqual(calls, 2)
        self.assertEqual(result["completedCount"], 1)
        self.assertEqual(result["passedCount"], 1)
        self.assertEqual(result["needsReworkCount"], 0)
        self.assertNotIn("前回の評価未完了feedback", prompts[0])
        self.assertIn("前回の評価未完了feedback", prompts[1])
        self.assertIn("全選択肢の根拠確認が未完了。", prompts[1])
        self.assertIn('"verifiedChoiceCount": 0', prompts[1])

    def test_batch_keeps_fully_unverified_retry_out_of_rework(self):
        calls = 0

        def runner(_prompt):
            nonlocal calls
            calls += 1
            result = evaluation_result(status="needs_rework")
            result["explanationScore"] = 0
            result["criticalIssues"] = ["評価処理を完了できませんでした。"]
            result["reworkItems"] = [
                {
                    "stage": "03",
                    "message": "評価をやり直してください。",
                    "choiceIndexes": [0, 1],
                }
            ]
            for choice in result["choiceEvaluations"]:
                choice["verdict"] = "insufficient_evidence"
            return result

        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=runner,
            )
            question = question_payload()
            preview = service.preview_many([question])
            result = service.run_many(
                [question], preview["previewToken"], lambda _line: None
            )
            status = service.status_for(question)

        self.assertEqual(calls, 2)
        self.assertEqual(result["completedCount"], 1)
        self.assertEqual(result["passedCount"], 0)
        self.assertEqual(result["needsReworkCount"], 0)
        self.assertEqual(result["inconclusiveCount"], 1)
        self.assertEqual(result["results"][0]["status"], "inconclusive")
        self.assertEqual(status["status"], "inconclusive")
        self.assertEqual(status["nextAction"], "evaluate")
        self.assertEqual(status["reworkItems"], [])

    def test_batch_rejects_questions_from_different_qualifications(self):
        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=lambda _prompt: evaluation_result(),
            )
            first = question_payload()
            second = question_payload(question_id="api-q2", body="問題2")
            second["qualification"] = "other"
            second["reviewKey"] = "other:2026:question_2:api-q2"

            with self.assertRaisesRegex(EvaluationError, "同じ資格"):
                service.preview_many([first, second])


class FakeInventory:
    def __init__(self, question):
        self.question = question

    def group(self, qualification, list_group_id):
        return {
            "qualification": qualification,
            "listGroupId": list_group_id,
            "questions": [self.question],
        }


class FakeEvaluationService:
    def status_for(self, _question, *, failed_delta_paths=None):
        return {
            "status": "passed",
            "publishReady": True,
            "resultHash": "evaluation-hash",
            "machineReady": True,
            "blockingIssues": [],
        }


class FakeFirestore:
    def __init__(self):
        self.documents = {}

    def read_documents(self, document_ids, *, fields=None):
        return {
            question_id: copy.deepcopy(self.documents[question_id])
            for question_id in document_ids
            if question_id in self.documents
        }


class QuestionPublisherTests(unittest.TestCase):
    def test_failed_delta_blocks_question_publish_before_firestore_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = question_payload()
            failed_path = Path(
                "output/sample/questions_json/2026/"
                "21_explanationText_added/partial.json"
            )
            absolute = root / failed_path
            absolute.parent.mkdir(parents=True)
            absolute.write_text("{}\n", encoding="utf-8")
            manifest = (
                root
                / "output/question_review_console/workflow_runs/sample/"
                "20260101-run/manifest.json"
            )
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "result": {"changedFiles": [failed_path.as_posix()]},
                    }
                ),
                encoding="utf-8",
            )
            publisher = QuestionPublisher(
                root,
                FakeInventory(question),
                FakeFirestore(),
                FakeEvaluationService(),
                "secret",
            )

            preview = publisher.preview(question)

        self.assertFalse(preview["canPublish"])
        self.assertEqual(preview["failedDeltaPaths"], [failed_path.as_posix()])
        self.assertIn("未確定差分", preview["reason"])

    def test_uploads_only_documents_for_the_selected_original_question(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = question_payload()
            question["uploadReadyDocs"][0].pop("isDeleted")
            artifact = root / question["paths"]["uploadReady"]
            artifact.parent.mkdir(parents=True)
            source = root / question["paths"]["source"]
            source.parent.mkdir(parents=True)
            source.write_text('{"question":"source"}\n', encoding="utf-8")
            other = upload_document("doc-other", "original-other", "他の選択肢", "正しい")
            artifact.write_text(
                json.dumps(
                    {"questions": [*question["uploadReadyDocs"], other]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            original_hash = artifact.read_bytes()
            firestore = FakeFirestore()
            commands = []

            def run(command, *, cwd, env, emit):
                commands.append(command)
                candidate = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
                self.assertEqual(
                    [item["questionId"] for item in candidate["questions"]],
                    ["doc-1", "doc-2"],
                )
                for document in candidate["questions"]:
                    firestore.documents[document["questionId"]] = build_doc_data_base(
                        document
                    )
                emit("uploaded")
                return 0

            publisher = QuestionPublisher(
                root,
                FakeInventory(question),
                firestore,
                FakeEvaluationService(),
                "secret",
                command_runner=run,
            )
            preview = publisher.preview(question)
            result = publisher.run(question, preview, lambda _line: None)

            self.assertEqual(preview["documentCount"], 2)
            self.assertEqual(preview["missingCount"], 2)
            self.assertEqual(result["status"], "succeeded")
            self.assertNotEqual(Path(commands[0][-1]).resolve(), artifact.resolve())
            self.assertEqual(artifact.read_bytes(), original_hash)
            self.assertNotIn("doc-other", firestore.documents)
            result_path = next(
                (root / "output" / "question_review_console" / "publish_runs").glob(
                    "sample/*/result.json"
                )
            )
            self.assertEqual(
                json.loads(result_path.read_text(encoding="utf-8"))["status"],
                "succeeded",
            )

    def test_rejects_documents_for_a_different_publication_qualification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = question_payload()
            question["publicationQualificationId"] = "published-sample"
            artifact = root / question["paths"]["uploadReady"]
            artifact.parent.mkdir(parents=True)
            source = root / question["paths"]["source"]
            source.parent.mkdir(parents=True)
            source.write_text('{"question":"source"}\n', encoding="utf-8")
            artifact.write_text(
                json.dumps({"questions": question["uploadReadyDocs"]}),
                encoding="utf-8",
            )
            publisher = QuestionPublisher(
                root,
                FakeInventory(question),
                FakeFirestore(),
                FakeEvaluationService(),
                "secret",
            )

            with self.assertRaisesRegex(PublicationError, "別資格"):
                publisher.preview(question)


if __name__ == "__main__":
    unittest.main()
