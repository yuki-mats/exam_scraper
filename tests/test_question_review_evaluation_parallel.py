from __future__ import annotations
import json
import pathlib
import tempfile
import threading
import types
import unittest
import tools.question_review_console.evaluation as module
import tests.test_question_review_evaluation as existing

class ParallelAuditTest(unittest.TestCase):

    def test_disjoint_batches_really_overlap_and_keep_receipts_and_order(self):
        with tempfile.TemporaryDirectory() as temp:
            s = module.QuestionEvaluationService(pathlib.Path(temp), 'secret', result_runner=lambda _: {})
            s.app_server = types.SimpleNamespace(config=types.SimpleNamespace(limits=types.SimpleNamespace(question_parallelism=4, llm_call_concurrency=2, audit_batch_questions=2, audit_batch_input_bytes=120000)), configured=True)
            qs = [existing.question_payload(question_id=f'p-{n}', state_hash=f's-{n}', body=f'Q-{n}') for n in range(12)]
            for q in qs:
                q['projected']['questionImageStorageUrls'] = []
            lock = threading.Lock()
            barrier = threading.Barrier(2)
            counts = {'active': 0, 'peak': 0, 'calls': 0}

            def batch(values, emit, **kwargs):
                with lock:
                    counts['active'] += 1
                    counts['calls'] += 1
                    counts['peak'] = max(counts['peak'], counts['active'])
                barrier.wait(5)
                with lock:
                    counts['active'] -= 1
                return ({q['id']: existing.evaluation_result() for q in values}, {'model': 'gpt-5.6-sol', 'threadId': 'thread-' + values[0]['id'], 'sessionId': 'thread-' + values[0]['id'], 'turnId': 'turn-' + values[0]['id'], 'auditBatchQuestionIds': [q['id'] for q in values], 'auditBatchQuestionCount': len(values), 'auditBatchInputBytes': 1})
            s._run_batch_result = batch
            p = s.preview_many(qs)
            self.assertEqual(p['evaluationConcurrencyLimit'], 2)
            result = s.run_many(qs, p['previewToken'], lambda _: None)
            self.assertEqual(counts, {'active': 0, 'peak': 2, 'calls': 6})
            self.assertEqual([x['questionId'] for x in result['results']], [q['id'] for q in qs])
            self.assertEqual(result['passedCount'], 12)
            self.assertFalse(s._active)
            for path in s.run_store.root.glob('sample/*/manifest.json'):
                m = json.loads(path.read_text())
                self.assertEqual(m['status'], 'succeeded')
                self.assertEqual(m['workVersionReceipt']['recordedCount'], 1)

    def test_parallel_transport_failure_closes_only_owned_reservations_without_fanout(self):
        with tempfile.TemporaryDirectory() as temp:
            s = module.QuestionEvaluationService(pathlib.Path(temp), 'secret', result_runner=lambda _: {})
            s.app_server = types.SimpleNamespace(config=types.SimpleNamespace(limits=types.SimpleNamespace(question_parallelism=2, llm_call_concurrency=2, audit_batch_questions=1, audit_batch_input_bytes=120000)), configured=True)
            qs = [existing.question_payload(question_id=f'f-{n}', state_hash=f's-{n}') for n in range(2)]
            for q in qs:
                q['projected']['questionImageStorageUrls'] = []
            barrier = threading.Barrier(2)
            calls = []

            def batch(values, emit, **kwargs):
                calls.append(values[0]['id'])
                barrier.wait(5)
                raise RuntimeError('transport failed')
            s._run_batch_result = batch
            p = s.preview_many(qs)
            r = s.run_many(qs, p['previewToken'], lambda _: None)
            self.assertEqual(r['failedCount'], 2)
            self.assertEqual(len(calls), 2)
            self.assertFalse(s._active)
            for path in s.run_store.root.glob('sample/*/manifest.json'):
                self.assertEqual(json.loads(path.read_text())['status'], 'failed')
            for q in qs:
                self.assertIsNone(s.store.load_projection(q).get('currentValid'))

    def test_parallel_policy_change_rejects_completed_results_and_releases_all_owned_attempts(self):
        from unittest.mock import patch
        import copy
        with tempfile.TemporaryDirectory() as temp:
            s = module.QuestionEvaluationService(pathlib.Path(temp), 'secret', result_runner=lambda _: {})
            s.app_server = types.SimpleNamespace(config=types.SimpleNamespace(limits=types.SimpleNamespace(question_parallelism=2, llm_call_concurrency=2, audit_batch_questions=1, audit_batch_input_bytes=120000)), configured=True)
            qs = [existing.question_payload(question_id=f'changed-{n}', state_hash=f's-{n}') for n in range(2)]
            for q in qs:
                q['projected']['questionImageStorageUrls'] = []
            initial = s.current_policy()
            current = [initial]
            barrier = threading.Barrier(2)
            calls = []

            def batch(values, emit, **kwargs):
                calls.append(values[0]['id'])
                barrier.wait(5)
                current[0] = {**initial, 'policyFingerprint': 'changed-during-parallel-model'}
                return ({q['id']: existing.evaluation_result() for q in values}, {})
            s._run_batch_result = batch
            with patch.object(s, 'current_policy', side_effect=lambda **_: copy.deepcopy(current[0])):
                p = s.preview_many(qs)
                r = s.run_many(qs, p['previewToken'], lambda _: None)
            self.assertEqual(r['failedCount'], 2)
            self.assertEqual(r['completedCount'], 0)
            self.assertEqual(len(calls), 2)
            self.assertFalse(s._active)
            manifests = [json.loads(path.read_text()) for path in s.run_store.root.glob('sample/*/manifest.json')]
            self.assertEqual(len(manifests), 2)
            self.assertTrue(all((m['status'] == 'failed' and m['workVersionReceipt'] is None for m in manifests)))
            for q in qs:
                projection = s.store.load_projection(q)
                self.assertEqual(projection['latestAttempt']['status'], 'failed')
                self.assertIsNone(projection.get('currentValid'))

    def test_concurrency_uses_both_config_limits_session_count_and_500_cap(self):
        with tempfile.TemporaryDirectory() as temp:
            s = module.QuestionEvaluationService(pathlib.Path(temp), 'secret', result_runner=lambda _: {})
            self.assertEqual(s._evaluation_concurrency_limit(9), 1)
            s.app_server = types.SimpleNamespace(config=types.SimpleNamespace(limits=types.SimpleNamespace(question_parallelism=700, llm_call_concurrency=600)))
            self.assertEqual(s._evaluation_concurrency_limit(1000), 500)
            self.assertEqual(s._evaluation_concurrency_limit(7), 7)
            s.app_server.config.limits.llm_call_concurrency = 3
            self.assertEqual(s._evaluation_concurrency_limit(7), 3)
if __name__ == '__main__':
    unittest.main()
