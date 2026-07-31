import copy
import hashlib
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
from tools.question_review_console.server import QuestionReviewApplication


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
        "sourceCorrectChoiceComparison": {
            "comparable": True,
            "different": False,
            "source": ["正しい", "間違い"],
            "current": ["正しい", "間違い"],
            "changedChoiceIndexes": [],
        },
        "sourceAnswerDifferenceApproval": {
            "approved": False,
            "reason": "verified_correct_answer_patch_missing",
        },
        "issueCodes": [],
        "workflow": {"merge": "match", "convert": "match", "upload": "match"},
        "projected": {
            "questionBodyText": body,
            "questionType": "true_false",
            "questionIntent": "select_correct",
            "choiceTextList": ["選択肢A", "選択肢B"],
            "isCalculationQuestion": False,
            "questionImageStorageUrls": [
                "https://example.invalid/question-image.png"
            ],
            "originalQuestionChoiceImageUrls": [],
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


def inconclusive_result():
    result = evaluation_result(status="needs_rework")
    result["explanationScore"] = 0
    result["criticalIssues"] = ["評価を完了できなかった。"]
    for choice in result["choiceEvaluations"]:
        choice["verdict"] = "insufficient_evidence"
    return result


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
    def _committed_with_failed_promotion(self, directory):
        service = QuestionEvaluationService(
            Path(directory),
            "secret",
            result_runner=lambda _prompt: evaluation_result(),
        )
        question = question_payload()
        original_write = service.store._write_projection
        writes = 0

        def fail_promotion(target, projection):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise OSError("projection unavailable")
            return original_write(target, projection)

        service.store._write_projection = fail_promotion
        preview = service.preview(question)
        completed = service.run(
            question, preview["previewToken"], lambda _line: None
        )
        service.store._write_projection = original_write
        return service, question, completed

    def test_active_lifecycle_failure_matrix_releases_and_allows_retry(self):
        stages = (
            "recover",
            "load",
            "build_prompt",
            "run_create",
            "reserve",
            "prompt_save",
            "run_update",
            "model",
        )
        for stage in stages:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                model_calls = 0

                def runner(_prompt):
                    nonlocal model_calls
                    model_calls += 1
                    if stage == "model" and model_calls == 1:
                        raise RuntimeError("injected model failure")
                    return evaluation_result()

                service = QuestionEvaluationService(
                    Path(directory), "secret", result_runner=runner
                )
                question = question_payload()
                preview = service.preview(question)
                owner, attribute = {
                    "recover": (service, "_recover_projection"),
                    "load": (service.store, "load"),
                    "build_prompt": (service, "_build_prompt"),
                    "run_create": (service.run_store, "create"),
                    "reserve": (service.store, "reserve_attempt"),
                    "prompt_save": (service.store, "save_prompt"),
                    "run_update": (service.run_store, "update"),
                }.get(stage, (None, None))
                original = getattr(owner, attribute) if owner is not None else None
                if owner is not None:
                    failed = False

                    def fail_once(*args, **kwargs):
                        nonlocal failed
                        if not failed:
                            failed = True
                            raise RuntimeError(f"injected {stage} failure")
                        return original(*args, **kwargs)

                    setattr(owner, attribute, fail_once)
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    service.run(
                        question, preview["previewToken"], lambda _line: None
                    )
                if owner is not None:
                    setattr(owner, attribute, original)
                self.assertNotIn(question["reviewKey"], service._active)
                self.assertNotEqual(service.status_for(question)["status"], "running")
                manifests = list(
                    service.run_store.root.glob("sample/*/manifest.json")
                )
                expected_manifests = 0 if stage in {
                    "recover",
                    "load",
                    "build_prompt",
                    "run_create",
                } else 1
                self.assertEqual(len(manifests), expected_manifests)
                self.assertEqual(
                    model_calls,
                    1 if stage == "model" else 0,
                )
                retry = service.preview(question)
                completed = service.run(
                    question, retry["previewToken"], lambda _line: None
                )

            self.assertEqual(completed["evaluation"]["status"], "passed")

    def test_recovery_write_failure_releases_active_before_run_creation(self):
        calls = 0

        def runner(_prompt):
            nonlocal calls
            calls += 1
            return evaluation_result()

        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory), "secret", result_runner=runner
            )
            question = question_payload()
            original_write = service.store._write_projection
            writes = 0

            def fail_first_promotion(target, projection):
                nonlocal writes
                writes += 1
                if writes == 2:
                    raise OSError("projection unavailable")
                return original_write(target, projection)

            service.store._write_projection = fail_first_promotion
            preview = service.preview(question)
            service.run(question, preview["previewToken"], lambda _line: None)
            service.store._write_projection = (
                lambda _target, _projection: (_ for _ in ()).throw(
                    OSError("projection unavailable")
                )
            )
            retry_preview = service.preview(question)
            manifests_before = len(
                list(service.run_store.root.glob("sample/*/manifest.json"))
            )
            with self.assertRaisesRegex(OSError, "projection unavailable"):
                service.run(
                    question,
                    retry_preview["previewToken"],
                    lambda _line: None,
                )
            manifests_after = len(
                list(service.run_store.root.glob("sample/*/manifest.json"))
            )
            self.assertNotIn(question["reviewKey"], service._active)
            self.assertEqual(calls, 1)
            self.assertEqual(manifests_after, manifests_before)
            logical_status = service.status_for(question)
            self.assertEqual(logical_status["status"], "passed")
            self.assertEqual(
                logical_status["resultHash"],
                service.run_store.get(
                    "sample",
                    service.store.load_projection(question)["latestAttempt"]["runId"],
                )["result"]["resultHash"],
            )
            self.assertTrue(logical_status["publishReady"])
            service.store._write_projection = original_write
            retry_preview = service.preview(question)
            service.run(
                question,
                retry_preview["previewToken"],
                lambda _line: None,
            )

        self.assertEqual(calls, 2)

    def test_terminal_inconclusive_and_failed_status_reads_do_not_rewrite_projection(self):
        for terminal in ("inconclusive", "failed"):
            with self.subTest(terminal=terminal), tempfile.TemporaryDirectory() as directory:
                runner = (
                    (lambda _prompt: inconclusive_result())
                    if terminal == "inconclusive"
                    else (lambda _prompt: (_ for _ in ()).throw(RuntimeError("failed")))
                )
                service = QuestionEvaluationService(
                    Path(directory), "secret", result_runner=runner
                )
                question = question_payload()
                preview = service.preview(question)
                if terminal == "failed":
                    with self.assertRaisesRegex(RuntimeError, "failed"):
                        service.run(
                            question, preview["previewToken"], lambda _line: None
                        )
                else:
                    service.run(
                        question, preview["previewToken"], lambda _line: None
                    )
                path = service.store.evaluation_path(question)
                before = path.read_bytes()
                before_mtime = path.stat().st_mtime_ns
                writes = 0
                original_write = service.store._write_projection

                def count_write(target, projection):
                    nonlocal writes
                    writes += 1
                    return original_write(target, projection)

                service.store._write_projection = count_write
                first = service.status_for(question)
                second = service.status_for(question)
                after = path.read_bytes()
                after_mtime = path.stat().st_mtime_ns

            self.assertEqual(writes, 0)
            self.assertEqual(after, before)
            self.assertEqual(after_mtime, before_mtime)
            self.assertEqual(first["status"], terminal if terminal == "inconclusive" else "not_started")
            self.assertEqual(second["status"], first["status"])
            if terminal == "inconclusive":
                self.assertEqual(len(first["choiceEvaluations"]), 2)

    def test_reserved_failed_projection_repairs_once_and_is_immediately_not_started(self):
        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=lambda _prompt: (_ for _ in ()).throw(
                    RuntimeError("model failed")
                ),
            )
            question = question_payload()
            original_record = service.store.record_attempt
            failed = False

            def fail_first_record(*args, **kwargs):
                nonlocal failed
                if not failed:
                    failed = True
                    raise OSError("projection unavailable")
                return original_record(*args, **kwargs)

            service.store.record_attempt = fail_first_record
            preview = service.preview(question)
            with self.assertRaisesRegex(RuntimeError, "model failed"):
                service.run(question, preview["previewToken"], lambda _line: None)
            projection = service.store.load_projection(question)
            self.assertEqual(projection["latestAttempt"]["status"], "reserved")
            writes = 0

            def count_record(*args, **kwargs):
                nonlocal writes
                writes += 1
                return original_record(*args, **kwargs)

            service.store.record_attempt = count_record
            first = service.status_for(question)
            second = service.status_for(question)
            repaired = service.store.load_projection(question)

        self.assertEqual(first["status"], "not_started")
        self.assertEqual(second["status"], "not_started")
        self.assertEqual(repaired["latestAttempt"]["status"], "failed")
        self.assertEqual(writes, 1)

    def test_malformed_policy_evidence_is_stale_without_exception(self):
        cases = (
            "policy_versions_type",
            "policy_version_value",
            "policy_fingerprints_type",
            "policy_fingerprints_missing",
            "receipt_version",
            "work_version",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                service = QuestionEvaluationService(
                    Path(directory),
                    "secret",
                    result_runner=lambda _prompt: evaluation_result(),
                )
                question = question_payload()
                original_write = service.store._write_projection
                writes = 0

                def fail_promotion(target, projection):
                    nonlocal writes
                    writes += 1
                    if writes == 2:
                        raise OSError("projection unavailable")
                    return original_write(target, projection)

                service.store._write_projection = fail_promotion
                preview = service.preview(question)
                completed = service.run(
                    question, preview["previewToken"], lambda _line: None
                )
                service.store._write_projection = original_write
                manifest_path = (
                    service.run_store.root
                    / "sample"
                    / completed["runId"]
                    / "manifest.json"
                )
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if case == "policy_versions_type":
                    manifest["policyVersions"] = []
                elif case == "policy_version_value":
                    manifest["policyVersions"] = {"evaluation": "invalid-version"}
                elif case == "policy_fingerprints_type":
                    manifest["policyFingerprints"] = []
                elif case == "policy_fingerprints_missing":
                    manifest.pop("policyFingerprints", None)
                elif case == "receipt_version":
                    manifest["workVersionReceipt"]["version"] = []
                elif case == "work_version":
                    version_path = service.work_versions.question_path_for(question)
                    versions = json.loads(version_path.read_text(encoding="utf-8"))
                    record = next(iter(versions["questions"].values()))
                    record["stages"]["evaluation"]["version"] = []
                    version_path.write_text(json.dumps(versions), encoding="utf-8")
                    service.work_versions._cache.clear()
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                service.run_store._manifest_cache.clear()

                status = service.status_for(question)

            self.assertEqual(status["status"], "stale")

    def test_committed_result_rejects_identity_hash_policy_and_work_version_mismatch(self):
        cases = (
            "result_hash",
            "review_key",
            "question_id",
            "qualification",
            "list_group_id",
            "original_question_id",
            "state_hash",
            "policy",
            "manifest_summary",
            "work_version",
            "session_id",
            "thread_id",
            "turn_id",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                service = QuestionEvaluationService(
                    Path(directory),
                    "secret",
                    result_runner=lambda _prompt: evaluation_result(),
                )
                question = question_payload()
                preview = service.preview(question)
                completed = service.run(
                    question, preview["previewToken"], lambda _line: None
                )
                projection = service.store.load_projection(question)
                run_id = completed["runId"]
                result_path = service.run_store.root / "sample" / run_id / "result.json"
                manifest_path = result_path.with_name("manifest.json")
                result = json.loads(result_path.read_text(encoding="utf-8"))
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if case == "result_hash":
                    result["summary"] = "tampered"
                elif case in {
                    "review_key",
                    "question_id",
                    "qualification",
                    "list_group_id",
                    "original_question_id",
                }:
                    result[
                        {
                            "review_key": "reviewKey",
                            "question_id": "questionId",
                            "qualification": "qualification",
                            "list_group_id": "listGroupId",
                            "original_question_id": "originalQuestionId",
                        }[case]
                    ] = "foreign"
                elif case == "state_hash":
                    result["stateHash"] = "foreign"
                elif case == "policy":
                    result["policyFingerprint"] = "foreign"
                elif case == "manifest_summary":
                    manifest["result"]["summary"] = "foreign"
                elif case == "work_version":
                    version_path = service.work_versions.question_path_for(question)
                    versions = json.loads(version_path.read_text(encoding="utf-8"))
                    record = next(iter(versions["questions"].values()))
                    record["stages"]["evaluation"]["policyFingerprint"] = "foreign"
                    version_path.write_text(json.dumps(versions), encoding="utf-8")
                    service.work_versions._cache.clear()
                elif case in {"session_id", "thread_id", "turn_id"}:
                    service.app_server = object()
                    field = {
                        "session_id": "sessionId",
                        "thread_id": "threadId",
                        "turn_id": "turnId",
                    }[case]
                    for receipt_field in ("sessionId", "threadId", "turnId"):
                        result[receipt_field] = f"same-{receipt_field}"
                        manifest[receipt_field] = f"same-{receipt_field}"
                    result[field] = "foreign"
                if case in {
                    "review_key",
                    "question_id",
                    "qualification",
                    "list_group_id",
                    "original_question_id",
                    "state_hash",
                    "policy",
                    "session_id",
                    "thread_id",
                    "turn_id",
                }:
                    unsigned = {
                        key: value for key, value in result.items() if key != "resultHash"
                    }
                    result["resultHash"] = hashlib.sha256(
                        json.dumps(
                            unsigned,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                    manifest["result"]["resultHash"] = result["resultHash"]
                result_path.write_text(json.dumps(result), encoding="utf-8")
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                service.run_store._manifest_cache.clear()

                recovered = service._committed_attempt_result(question, projection)

            self.assertIsNone(recovered)

    def test_foreign_work_version_evidence_is_stale_without_projection_write(self):
        cases = (
            "reviewKey",
            "questionId",
            "originalQuestionId",
            "publicationQualificationId",
            "source",
            "empty_policy_fingerprint",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                service, question, completed = self._committed_with_failed_promotion(
                    directory
                )
                projection_path = service.store.evaluation_path(question)
                before_bytes = projection_path.read_bytes()
                before_mtime = projection_path.stat().st_mtime_ns
                version_path = service.work_versions.question_path_for(question)
                versions = json.loads(version_path.read_text(encoding="utf-8"))
                record = next(iter(versions["questions"].values()))
                stage = record["stages"]["evaluation"]
                if case in {
                    "reviewKey",
                    "questionId",
                    "originalQuestionId",
                    "publicationQualificationId",
                }:
                    record[case] = "foreign"
                elif case == "source":
                    stage["source"] = "foreign"
                else:
                    run_id = completed["runId"]
                    result_path = (
                        service.run_store.root / "sample" / run_id / "result.json"
                    )
                    manifest_path = result_path.with_name("manifest.json")
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    result["policyFingerprint"] = ""
                    unsigned = {
                        key: value for key, value in result.items() if key != "resultHash"
                    }
                    result["resultHash"] = hashlib.sha256(
                        json.dumps(
                            unsigned,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                    manifest["policyFingerprints"]["evaluation"] = ""
                    manifest["result"]["resultHash"] = result["resultHash"]
                    stage["policyFingerprint"] = ""
                    result_path.write_text(json.dumps(result), encoding="utf-8")
                    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                    service.run_store._manifest_cache.clear()
                version_path.write_text(json.dumps(versions), encoding="utf-8")
                service.work_versions._cache.clear()

                status = service.status_for(question)

                self.assertEqual(status["status"], "stale")
                self.assertEqual(projection_path.read_bytes(), before_bytes)
                self.assertEqual(projection_path.stat().st_mtime_ns, before_mtime)

    def test_exact_current_and_history_work_versions_recover(self):
        for location in ("current", "history"):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as directory:
                service, question, completed = self._committed_with_failed_promotion(
                    directory
                )
                if location == "history":
                    version_path = service.work_versions.question_path_for(question)
                    versions = json.loads(version_path.read_text(encoding="utf-8"))
                    record = next(iter(versions["questions"].values()))
                    stage = record["stages"]["evaluation"]
                    historical = {
                        key: copy.deepcopy(value)
                        for key, value in stage.items()
                        if key != "history"
                    }
                    stage["history"] = [historical]
                    stage["runId"] = "newer-run"
                    version_path.write_text(json.dumps(versions), encoding="utf-8")
                    service.work_versions._cache.clear()

                status = service.status_for(question)
                projection = service.store.load_projection(question)

                self.assertEqual(status["status"], "passed")
                self.assertEqual(status["resultHash"], completed["evaluation"]["resultHash"])
                self.assertEqual(projection["currentValid"]["status"], "passed")

    def test_foreign_latest_work_version_preserves_existing_current(self):
        results = iter([evaluation_result(), evaluation_result(status="needs_rework")])
        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=lambda _prompt: next(results),
            )
            question = question_payload()
            preview = service.preview(question)
            service.run(question, preview["previewToken"], lambda _line: None)
            original_write = service.store._write_projection
            writes = 0

            def fail_second_promotion(target, projection):
                nonlocal writes
                writes += 1
                if writes == 2:
                    raise OSError("projection unavailable")
                return original_write(target, projection)

            service.store._write_projection = fail_second_promotion
            preview = service.preview(question)
            service.run(question, preview["previewToken"], lambda _line: None)
            service.store._write_projection = original_write
            version_path = service.work_versions.question_path_for(question)
            versions = json.loads(version_path.read_text(encoding="utf-8"))
            record = next(iter(versions["questions"].values()))
            record["stages"]["evaluation"]["source"] = "foreign"
            version_path.write_text(json.dumps(versions), encoding="utf-8")
            service.work_versions._cache.clear()
            projection_path = service.store.evaluation_path(question)
            before_bytes = projection_path.read_bytes()
            before_mtime = projection_path.stat().st_mtime_ns

            status = service.status_for(question)

            self.assertEqual(status["status"], "passed")
            self.assertEqual(projection_path.read_bytes(), before_bytes)
            self.assertEqual(projection_path.stat().st_mtime_ns, before_mtime)

    def test_projection_preserves_passed_current_across_two_inconclusive_attempts(self):
        results = iter(
            [evaluation_result(), inconclusive_result(), inconclusive_result()]
        )
        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=lambda _prompt: next(results),
            )
            question = question_payload()
            for _ in range(3):
                preview = service.preview(question)
                service.run(question, preview["previewToken"], lambda _line: None)
            projection = service.store.load_projection(question)
            status = service.status_for(question)

        self.assertEqual(status["status"], "passed")
        self.assertEqual(projection["currentValid"]["status"], "passed")
        self.assertEqual(projection["latestAttempt"]["status"], "inconclusive")
        self.assertEqual(projection["latestAttemptSequence"], 3)
        self.assertEqual(projection["promotedAttemptSequence"], 1)

    def test_terminal_inconclusive_with_current_skips_latest_run_evidence(self):
        for current_status in ("passed", "needs_rework"):
            with self.subTest(current_status=current_status), tempfile.TemporaryDirectory() as directory:
                results = iter(
                    [
                        evaluation_result(status=current_status),
                        inconclusive_result(),
                    ]
                )
                service = QuestionEvaluationService(
                    Path(directory),
                    "secret",
                    result_runner=lambda _prompt: next(results),
                )
                question = question_payload()
                preview = service.preview(question)
                current_run = service.run(
                    question, preview["previewToken"], lambda _line: None
                )
                preview = service.preview(question)
                latest_run = service.run(
                    question, preview["previewToken"], lambda _line: None
                )
                projection_path = service.store.evaluation_path(question)
                latest_result_path = (
                    service.run_store.root
                    / "sample"
                    / latest_run["runId"]
                    / "result.json"
                )
                before_bytes = projection_path.read_bytes()
                before_mtime = projection_path.stat().st_mtime_ns
                committed_calls = 0
                manifest_calls = 0
                result_reads = 0
                writes = 0
                original_committed = service._committed_attempt_result
                original_get = service.run_store.get
                original_read_text = Path.read_text
                original_write = service.store._write_projection

                def count_committed(*args, **kwargs):
                    nonlocal committed_calls
                    committed_calls += 1
                    return original_committed(*args, **kwargs)

                def count_get(qualification, run_id):
                    nonlocal manifest_calls
                    if run_id == latest_run["runId"]:
                        manifest_calls += 1
                    return original_get(qualification, run_id)

                def count_read_text(target, *args, **kwargs):
                    nonlocal result_reads
                    if target == latest_result_path:
                        result_reads += 1
                    return original_read_text(target, *args, **kwargs)

                def count_write(target, projection):
                    nonlocal writes
                    writes += 1
                    return original_write(target, projection)

                service._committed_attempt_result = count_committed
                service.run_store.get = count_get
                service.store._write_projection = count_write
                with patch.object(Path, "read_text", count_read_text):
                    status = service.status_for(question, failed_delta_paths=())

                self.assertEqual(status["status"], current_status)
                self.assertEqual(
                    status["resultHash"],
                    current_run["evaluation"]["resultHash"],
                )
                self.assertEqual(committed_calls, 0)
                self.assertEqual(manifest_calls, 0)
                self.assertEqual(result_reads, 0)
                self.assertEqual(writes, 0)
                self.assertEqual(projection_path.read_bytes(), before_bytes)
                self.assertEqual(projection_path.stat().st_mtime_ns, before_mtime)

    def test_fresh_terminal_inconclusive_still_reads_exact_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=lambda _prompt: inconclusive_result(),
            )
            question = question_payload()
            preview = service.preview(question)
            completed = service.run(
                question, preview["previewToken"], lambda _line: None
            )
            result_path = (
                service.run_store.root
                / "sample"
                / completed["runId"]
                / "result.json"
            )
            committed_calls = 0
            result_reads = 0
            original_committed = service._committed_attempt_result
            original_read_text = Path.read_text

            def count_committed(*args, **kwargs):
                nonlocal committed_calls
                committed_calls += 1
                return original_committed(*args, **kwargs)

            def count_read_text(target, *args, **kwargs):
                nonlocal result_reads
                if target == result_path:
                    result_reads += 1
                return original_read_text(target, *args, **kwargs)

            service._committed_attempt_result = count_committed
            with patch.object(Path, "read_text", count_read_text):
                status = service.status_for(question, failed_delta_paths=())

            self.assertEqual(status["status"], "inconclusive")
            self.assertEqual(len(status["choiceEvaluations"]), 2)
            self.assertEqual(committed_calls, 1)
            self.assertEqual(result_reads, 1)

    def test_projection_preserves_needs_rework_current_after_failed_attempt(self):
        calls = 0

        def runner(_prompt):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("model failed")
            return evaluation_result(status="needs_rework")

        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory), "secret", result_runner=runner
            )
            question = question_payload()
            preview = service.preview(question)
            service.run(question, preview["previewToken"], lambda _line: None)
            preview = service.preview(question)
            with self.assertRaisesRegex(RuntimeError, "model failed"):
                service.run(question, preview["previewToken"], lambda _line: None)
            projection = service.store.load_projection(question)
            status = service.status_for(question)

        self.assertEqual(status["status"], "needs_rework")
        self.assertEqual(projection["currentValid"]["status"], "needs_rework")
        self.assertEqual(projection["latestAttempt"]["status"], "failed")

    def test_projection_failure_after_commit_recovers_before_next_attempt(self):
        results = iter([evaluation_result(), inconclusive_result()])
        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=lambda _prompt: next(results),
            )
            question = question_payload()
            original = service.store._write_projection
            calls = 0

            def fail_first_promotion(target, projection):
                nonlocal calls
                calls += 1
                if calls in {2, 3}:
                    raise OSError("projection unavailable")
                return original(target, projection)

            service.store._write_projection = fail_first_promotion
            preview = service.preview(question)
            first = service.run(
                question, preview["previewToken"], lambda _line: None
            )
            first_manifest = service.run_store.get("sample", first["runId"])
            logical_status = service.status_for(question)
            service.store._write_projection = original
            preview = service.preview(question)
            service.run(question, preview["previewToken"], lambda _line: None)
            projection = service.store.load_projection(question)

        self.assertEqual(first_manifest["status"], "succeeded")
        self.assertIsInstance(first_manifest["workVersionReceipt"], dict)
        self.assertEqual(logical_status["status"], "passed")
        self.assertEqual(logical_status["resultHash"], first["evaluation"]["resultHash"])
        self.assertEqual(projection["currentValid"]["status"], "passed")
        self.assertEqual(projection["promotedAttemptSequence"], 1)
        self.assertEqual(projection["latestAttemptSequence"], 2)

    def test_corrupt_projection_blocks_next_attempt_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=lambda _prompt: evaluation_result(),
            )
            question = question_payload()
            path = service.store.evaluation_path(question)
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "question-evaluation-projection/v2",
                        "reviewKey": question["reviewKey"],
                        "projectionHash": "invalid",
                    }
                ),
                encoding="utf-8",
            )
            preview = service.preview(question)
            with self.assertRaisesRegex(EvaluationError, "検証できません"):
                service.run(question, preview["previewToken"], lambda _line: None)
            unchanged = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(unchanged["projectionHash"], "invalid")

    def test_legacy_v1_is_promoted_naturally_on_first_new_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=lambda _prompt: inconclusive_result(),
            )
            question = question_payload()
            legacy = service.store.build_result(
                question,
                evaluation_result(),
                session_id="legacy-session",
                provider="test",
                started_at="2026-01-01T00:00:00+09:00",
                run_id="legacy-run",
                policy_version="2.1",
                policy_fingerprint="legacy",
            )
            path = service.store.evaluation_path(question)
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(legacy, ensure_ascii=False),
                encoding="utf-8",
            )
            preview = service.preview(question)
            service.run(question, preview["previewToken"], lambda _line: None)
            projection = service.store.load_projection(question)

        self.assertEqual(
            projection["schemaVersion"],
            "question-evaluation-projection/v2",
        )
        self.assertEqual(projection["currentValid"]["status"], "passed")
        self.assertEqual(projection["latestAttemptSequence"], 1)
        self.assertEqual(projection["promotedAttemptSequence"], 0)

    def test_manifest_commit_failure_keeps_previous_valid_projection(self):
        results = iter(
            [evaluation_result(), evaluation_result(status="needs_rework")]
        )
        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=lambda _prompt: next(results),
            )
            question = question_payload()
            preview = service.preview(question)
            first = service.run(
                question, preview["previewToken"], lambda _line: None
            )
            original_update = service.run_store.update

            def fail_commit(qualification, run_id, **changes):
                if (
                    changes.get("status") == "succeeded"
                    and "workVersionReceipt" in changes
                ):
                    raise OSError("manifest unavailable")
                return original_update(qualification, run_id, **changes)

            service.run_store.update = fail_commit
            preview = service.preview(question)
            with self.assertRaisesRegex(OSError, "manifest unavailable"):
                service.run(question, preview["previewToken"], lambda _line: None)
            projection = service.store.load_projection(question)
            failed_run_id = projection["latestAttempt"]["runId"]
            failed_result = json.loads(
                (
                    service.run_store.root
                    / "sample"
                    / failed_run_id
                    / "result.json"
                ).read_text(encoding="utf-8")
            )
            old_version_evidence = service._work_version_has_evaluation_run(
                question,
                first["runId"],
            )

        self.assertEqual(projection["currentValid"]["status"], "passed")
        self.assertEqual(projection["latestAttempt"]["status"], "failed")
        self.assertEqual(projection["promotedAttemptSequence"], 1)
        self.assertTrue(old_version_evidence)
        self.assertEqual(failed_result["status"], "needs_rework")
        self.assertEqual(len(failed_result["choiceEvaluations"]), 2)

    def test_record_stage_failure_keeps_previous_valid_projection(self):
        results = iter(
            [evaluation_result(), evaluation_result(status="needs_rework")]
        )
        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=lambda _prompt: next(results),
            )
            question = question_payload()
            preview = service.preview(question)
            service.run(question, preview["previewToken"], lambda _line: None)
            original_record = service.work_versions.record_stage
            calls = 0

            def fail_second_record(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OSError("work version unavailable")
                return original_record(*args, **kwargs)

            service.work_versions.record_stage = fail_second_record
            preview = service.preview(question)
            with self.assertRaisesRegex(OSError, "work version unavailable"):
                service.run(question, preview["previewToken"], lambda _line: None)
            projection = service.store.load_projection(question)

        self.assertEqual(projection["currentValid"]["status"], "passed")
        self.assertEqual(projection["latestAttempt"]["status"], "failed")
        self.assertEqual(projection["promotedAttemptSequence"], 1)

    def test_projection_rejects_non_monotonic_promotion(self):
        results = iter(
            [evaluation_result(), evaluation_result(status="needs_rework")]
        )
        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=lambda _prompt: next(results),
            )
            question = question_payload()
            runs = []
            for _ in range(2):
                preview = service.preview(question)
                runs.append(
                    service.run(
                        question, preview["previewToken"], lambda _line: None
                    )
                )
            with self.assertRaisesRegex(EvaluationError, "予約順序"):
                service.store.record_attempt(
                    question,
                    sequence=1,
                    run_id=runs[0]["runId"],
                    status="passed",
                    result=runs[0]["evaluation"],
                    promote=True,
                )
            projection = service.store.load_projection(question)

        self.assertEqual(projection["currentValid"]["status"], "needs_rework")
        self.assertEqual(projection["promotedAttemptSequence"], 2)

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

    def test_stable_status_polls_use_one_projection_snapshot(self):
        cases = (
            "not_started",
            "legacy",
            "passed",
            "needs_rework",
            "inconclusive",
            "failed",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                runner = (
                    (lambda _prompt: (_ for _ in ()).throw(RuntimeError("failed")))
                    if case == "failed"
                    else (
                        (lambda _prompt: inconclusive_result())
                        if case == "inconclusive"
                        else (
                            lambda _prompt: evaluation_result(
                                status=(
                                    "needs_rework"
                                    if case == "needs_rework"
                                    else "passed"
                                )
                            )
                        )
                    )
                )
                service = QuestionEvaluationService(
                    Path(directory), "secret", result_runner=runner
                )
                question = question_payload()
                path = service.store.evaluation_path(question)
                if case == "legacy":
                    policy = service.current_policy()
                    legacy = service.store.build_result(
                        question,
                        evaluation_result(),
                        session_id="legacy-session",
                        provider="test",
                        started_at="2026-01-01T00:00:00+09:00",
                        run_id="legacy-run",
                        policy_version=policy["policyVersion"],
                        policy_fingerprint=policy["policyFingerprint"],
                    )
                    path.parent.mkdir(parents=True)
                    path.write_text(json.dumps(legacy), encoding="utf-8")
                elif case != "not_started":
                    preview = service.preview(question)
                    if case == "failed":
                        with self.assertRaisesRegex(RuntimeError, "failed"):
                            service.run(
                                question,
                                preview["previewToken"],
                                lambda _line: None,
                            )
                    else:
                        service.run(
                            question,
                            preview["previewToken"],
                            lambda _line: None,
                        )
                original_load = service.store.load_projection
                load_calls = 0

                def count_load(target):
                    nonlocal load_calls
                    load_calls += 1
                    return original_load(target)

                original_stat = Path.stat
                path_stat_calls = 0

                def count_stat(target, *args, **kwargs):
                    nonlocal path_stat_calls
                    if target == path:
                        path_stat_calls += 1
                    return original_stat(target, *args, **kwargs)

                service.store.load_projection = count_load
                with patch.object(Path, "stat", count_stat):
                    service.status_for(question, failed_delta_paths=())

                self.assertLessEqual(load_calls, 1)
                self.assertLessEqual(path_stat_calls, 2)

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

    def test_app_server_exact_logical_results_read_run_evidence_once(self):
        class FakeAppServer:
            configured = True
            provider = "Codex App Server"

            def __init__(self, result):
                self.result = result

            def run_turn(self, _prompt, **kwargs):
                kwargs["on_thread_started"]("thread-1", "session-1")
                kwargs["on_turn_started"]("thread-1", "turn-1")
                return AppServerTurnResult(
                    thread_id="thread-1",
                    session_id="session-1",
                    turn_id="turn-1",
                    final_message=json.dumps(self.result, ensure_ascii=False),
                    model="gpt-test",
                    service_tier=None,
                )

        for case in ("fresh_inconclusive", "post_commit_passed"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                service = QuestionEvaluationService(
                    Path(directory),
                    "secret",
                    app_server=FakeAppServer(
                        inconclusive_result()
                        if case == "fresh_inconclusive"
                        else evaluation_result()
                    ),
                )
                question = question_payload()
                if case == "post_commit_passed":
                    original_write = service.store._write_projection
                    writes = 0

                    def fail_promotion(target, projection):
                        nonlocal writes
                        writes += 1
                        if writes == 2:
                            raise OSError("projection unavailable")
                        return original_write(target, projection)

                    service.store._write_projection = fail_promotion
                preview = service.preview(question)
                completed = service.run(
                    question, preview["previewToken"], lambda _line: None
                )
                if case == "post_commit_passed":
                    service.store._write_projection = original_write
                result_path = (
                    service.run_store.root
                    / "sample"
                    / completed["runId"]
                    / "result.json"
                )
                manifest_reads = 0
                result_reads = 0
                original_get = service.run_store.get
                original_read_text = Path.read_text

                def count_get(qualification, run_id):
                    nonlocal manifest_reads
                    if run_id == completed["runId"]:
                        manifest_reads += 1
                    return original_get(qualification, run_id)

                def count_read_text(target, *args, **kwargs):
                    nonlocal result_reads
                    if target == result_path:
                        result_reads += 1
                    return original_read_text(target, *args, **kwargs)

                service.run_store.get = count_get
                with patch.object(Path, "read_text", count_read_text):
                    status = service.status_for(question, failed_delta_paths=())

                self.assertEqual(manifest_reads, 1)
                self.assertEqual(result_reads, 1)
                self.assertEqual(len(status["choiceEvaluations"]), 2)
                if case == "fresh_inconclusive":
                    self.assertEqual(status["status"], "inconclusive")
                else:
                    self.assertEqual(status["status"], "passed")
                    self.assertTrue(status["publishReady"])

    def test_promoted_and_legacy_payloads_still_validate_app_server_receipt(self):
        class FakeAppServer:
            configured = True
            provider = "Codex App Server"

            def run_turn(self, _prompt, **kwargs):
                kwargs["on_thread_started"]("thread-1", "session-1")
                kwargs["on_turn_started"]("thread-1", "turn-1")
                return AppServerTurnResult(
                    thread_id="thread-1",
                    session_id="session-1",
                    turn_id="turn-1",
                    final_message=json.dumps(evaluation_result(), ensure_ascii=False),
                    model="gpt-test",
                    service_tier=None,
                )

        for case in ("promoted", "legacy"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                service = QuestionEvaluationService(
                    Path(directory),
                    "secret",
                    app_server=FakeAppServer(),
                )
                question = question_payload()
                if case == "promoted":
                    preview = service.preview(question)
                    service.run(
                        question, preview["previewToken"], lambda _line: None
                    )
                else:
                    policy = service.current_policy()
                    legacy = service.store.build_result(
                        question,
                        evaluation_result(),
                        session_id="session-1",
                        provider="Codex App Server",
                        started_at="2026-01-01T00:00:00+09:00",
                        thread_id="thread-1",
                        turn_id="foreign",
                        run_id="legacy-run",
                        policy_version=policy["policyVersion"],
                        policy_fingerprint=policy["policyFingerprint"],
                    )
                    path = service.store.evaluation_path(question)
                    path.parent.mkdir(parents=True)
                    path.write_text(json.dumps(legacy), encoding="utf-8")
                receipt_checks = 0

                def count_valid(*_args, **_kwargs):
                    nonlocal receipt_checks
                    receipt_checks += 1
                    return False

                service._session_receipt_valid = count_valid
                status = service.status_for(question, failed_delta_paths=())

                self.assertEqual(receipt_checks, 1)
                self.assertEqual(status["status"], "stale")

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
        self.assertIn("計算で一つの数値を求め", prompt)
        self.assertIn("その値に最も近い数値候補を選ぶ問題もflash_card", prompt)
        self.assertIn("数値候補を一つずつ計算結果と照合することだけを理由にtrue_false化せず", prompt)
        self.assertIn("複数選択形式と誤解して分類変更を求めない", prompt)
        self.assertIn("isCalculationQuestionは計算過程が主要な学習対象かを表し", prompt)
        self.assertIn("questionTypeとは独立に評価", prompt)
        self.assertIn('"isCalculationQuestion": false', prompt)
        self.assertIn('"questionImageStorageUrls"', prompt)
        self.assertIn("https://example.invalid/question-image.png", prompt)
        self.assertIn("図表画像は問題に添付されている", prompt)
        self.assertIn("技術式の裏取りに公式規程を使ったことだけを理由に", prompt)
        self.assertNotIn('"examLabel"', prompt)
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
        self.assertIn("解説が`正しい。`だけでも減点しない", prompt)
        self.assertIn("選択肢の全文再掲", prompt)
        self.assertIn(
            "true_falseの間違いの選択肢は、`間違い。`だけでは合格にしない",
            prompt,
        )
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

    def test_prompt_includes_exact_official_firestore_snapshot_answer_evidence(self):
        question = question_payload()
        choices = copy.deepcopy(question["projected"]["choiceTextList"])
        question["sourceRecordRef"] = "question_2025_firestore_1.json#3"
        question["source"] = {
            "questionBodyText": question["projected"]["questionBodyText"],
            "choiceTextList": choices,
            "correctChoiceText": ["正しい", "間違い"],
            "sourceOrigin": "firestore_snapshot",
            "sourceAcquisitionMethod": "firestore_snapshot",
            "firestoreSourceQuestions": [
                {
                    "questionId": "official-q1",
                    "isOfficial": True,
                    "originalQuestionChoiceText": choices[0],
                    "correctChoiceText": "正しい",
                },
                {
                    "questionId": "official-q2",
                    "isOfficial": True,
                    "originalQuestionChoiceText": choices[1],
                    "correctChoiceText": "間違い",
                },
            ],
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
            '"evidenceType": "official_firestore_snapshot_statement_verdicts"',
            prompt,
        )
        self.assertIn('"officialDocumentIds"', prompt)
        self.assertIn('"official-q1"', prompt)

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
        self.assertEqual(version_record["stages"]["evaluation"]["version"], "4.1")
        self.assertEqual(stale["status"], "stale")
        self.assertFalse(stale["publishReady"])

    def test_unapproved_source_answer_difference_blocks_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=lambda _prompt: evaluation_result(),
            )
            question = question_payload()
            preview = service.preview(question)
            service.run(
                question,
                preview["previewToken"],
                lambda _line: None,
            )
            unapproved = copy.deepcopy(question)
            unapproved["sourceCorrectChoiceComparison"] = {
                "comparable": True,
                "different": True,
                "source": ["正しい", "間違い"],
                "current": ["正しい", "正しい"],
                "changedChoiceIndexes": [1],
            }
            blocked = service.status_for(unapproved)
            approved = copy.deepcopy(unapproved)
            approved["sourceAnswerDifferenceApproval"] = {
                "approved": True,
                "reason": "verified_correct_answer_patch",
            }
            allowed = service.status_for(approved)

        self.assertFalse(blocked["machineReady"])
        self.assertFalse(blocked["publishReady"])
        self.assertEqual(blocked["status"], "needs_rework")
        self.assertEqual(
            [item["stage"] for item in blocked["reworkItems"]],
            ["02a", "03"],
        )
        self.assertEqual(
            blocked["reworkItems"][0]["choiceIndexes"],
            [1],
        )
        self.assertEqual(
            QuestionReviewApplication._quality_bucket(
                {
                    "evaluation": blocked,
                    "workflow": {"firestore": "mismatch"},
                }
            ),
            "needsRework",
        )
        self.assertIn(
            "source_answer_difference_unapproved",
            blocked["blockingIssues"],
        )
        self.assertTrue(allowed["machineReady"])
        self.assertTrue(allowed["publishReady"])

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
                "policyVersion": "4.1",
                "policyFingerprint": "new-evaluation-policy",
            }
            minor_version = service.status_for(question)
            service.current_policy = lambda: {
                **original_policy,
                "policyVersion": "5.0",
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

        self.assertEqual(status["status"], "stale")
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
        self.assertEqual(len(status["choiceEvaluations"]), 2)

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
    def test_source_answer_difference_has_specific_publication_block_reason(self):
        reason = QuestionPublisher._quality_block_reason(
            {
                "machineReady": False,
                "blockingIssues": [
                    "source_answer_difference_unapproved",
                ],
            }
        )

        self.assertIn("00_sourceと異なる正答", reason)
        self.assertIn("公式資料", reason)

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
