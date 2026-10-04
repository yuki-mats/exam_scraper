"""Actual SDK serialization, isolated transport and temporary private journals."""
from copy import deepcopy
from pathlib import Path
import json
import os
import socket
import tempfile
import unittest
from unittest.mock import patch
from google.auth.credentials import AnonymousCredentials
from google.cloud.firestore_v1 import Client, types, _helpers
from google.api_core.exceptions import Aborted
from tools.question_review_console.publication_delta_gateway import (
    DeltaRequest, IntentJournal, PublicationDeltaGateway, timestamp, version)


class Crash(BaseException): pass


class Transport:
    def __init__(self, client):
        self.client = client; self.docs = {}; self.commits = 0; self.reads = 0
        self.mode = None; self.fail_read = None; self.masks = []; self.journal = None

    def batch_get_documents(self, request, **kwargs):
        self.reads += 1
        if self.reads == self.fail_read: raise TimeoutError('fixture read unavailable')
        assert kwargs['retry'] is None
        mask = list(request['mask'].field_paths); self.masks.append(mask)
        for name in reversed(request['documents']):
            doc = self.docs.get(name)
            if doc is None:
                yield types.BatchGetDocumentsResponse(missing=name)
            else:
                fields = {k: v for k, v in doc['fields'].items() if k in mask}
                yield types.BatchGetDocumentsResponse(found=types.Document(name=name,
                    fields=_helpers.encode_dict(fields), update_time=timestamp(doc['time'])))

    def commit(self, request, **kwargs):
        self.commits += 1
        assert kwargs['retry'] is None
        assert self.journal.read('fixture')[ -1]['state'] == 'committing'
        writes = request['writes']; assert len(writes) == 11
        if self.mode == 'before': raise TimeoutError('response absent before application')
        if self.mode == 'conflict': self.docs[writes[4].update.name]['time'] = '2026-10-04T00:00:00.000000002Z'
        for write in writes:
            assert write._pb.WhichOneof('operation') == 'update'
            current = self.docs[write.update.name]
            if version(write.current_document.update_time) != version(current['time']):
                raise Aborted('fixture atomic precondition conflict')
        results = []
        for i, write in enumerate(writes):
            current = self.docs[write.update.name]
            fields = _helpers.decode_dict(write.update.fields, self.client)
            assert set(fields) == set(write.update_mask.field_paths)
            current['fields'].update(fields)
            current['time'] = f'2026-10-04T00:00:01.{i + 1:09}Z'
            results.append(types.WriteResult(update_time=timestamp(current['time'])))
        if self.mode == 'after': raise TimeoutError('applied response lost')
        return types.CommitResponse(write_results=results, commit_time=timestamp('2026-10-04T00:00:02Z'))


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.journal = IntentJournal(Path(self.temp.name).resolve() / 'journal')
        self.blockers = [patch.object(socket, 'socket', side_effect=AssertionError('network prohibited')),
            patch('google.auth.default', side_effect=AssertionError('credentials prohibited'))]
        for blocker in self.blockers: blocker.start(); self.addCleanup(blocker.stop)
        self.client = Client(project='fixture-only', database='fixture', credentials=AnonymousCredentials())
        self.rpc = Transport(self.client); self.rpc.journal = self.journal
        self.client._firestore_api_internal = self.rpc
        self.gateway = PublicationDeltaGateway(client=self.client, collection_path='questions', journal=self.journal)
        deltas = []
        for i in range(11):
            fields = {'questionText': {'beforePresent': True, 'before': f'before-{i}', 'after': f'after-{i}'}}
            source = {'questionText': f'before-{i}', 'originalQuestionBodyText': 'source preserved', 'unknown': {'value': i}}
            if i == 0: fields['questionIntent'] = {'beforePresent': False, 'before': None, 'after': 'intent'}
            if i == 1:
                source['explanationText'] = None
                fields['explanationText'] = {'beforePresent': True, 'before': None, 'after': 'explanation'}
            delta = {'questionId': f'fixture-{i:02}', 'updateTime': '2026-10-04T00:00:00.000000001Z',
                'allowlist': list(fields), 'fields': fields, 'fullDocumentUploadAllowed': False}
            deltas.append(delta)
            name = self.client.document('questions/' + delta['questionId'])._document_path
            self.rpc.docs[name] = {'fields': source, 'time': delta['updateTime']}
        refs = {key: {'reference': 'fixture-evidence-only', 'sha256': 'a' * 64}
            for key in ('formalSaveReceipt', 'checkpoints', 'evaluation', 'productionApproval')}
        self.request = DeltaRequest('fixture', tuple(deltas), refs)

    def test_success_real_sdk_and_readonly_retry(self):
        original = deepcopy(self.rpc.docs)
        result = self.gateway.execute(self.request)
        self.assertEqual(result['status'], 'completed'); self.assertEqual(self.rpc.commits, 1)
        self.assertFalse(result['receipt']['permissionValidationPerformed'])
        for name, doc in self.rpc.docs.items():
            self.assertEqual(doc['fields']['unknown'], original[name]['fields']['unknown'])
            self.assertEqual(doc['fields']['originalQuestionBodyText'], 'source preserved')
        before = {p: p.read_bytes() for p in self.journal.path('fixture').iterdir()}
        self.assertEqual(self.gateway.execute(self.request)['status'], 'completed_readonly_retry')
        self.assertEqual(before, {p: p.read_bytes() for p in before}); self.assertEqual(self.rpc.commits, 1)
        for path in self.journal.root.rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)
        self.assertNotIn('unknown', self.rpc.masks[0])

    def test_preflight_conflicts(self):
        for variant in ('missing', 'nanosecond', 'presence', 'null', 'other_id'):
            with self.subTest(variant=variant):
                saved = deepcopy(self.rpc.docs); name = next(iter(self.rpc.docs))
                if variant == 'missing': del self.rpc.docs[name]
                elif variant == 'nanosecond': self.rpc.docs[name]['time'] = '2026-10-04T00:00:00.000000002Z'
                elif variant == 'presence': self.rpc.docs[name]['fields']['questionIntent'] = None
                elif variant == 'null': self.rpc.docs[name]['fields']['questionText'] = None
                else:
                    original = self.rpc.batch_get_documents
                    def wrong(request, **kwargs):
                        for response in original(request, **kwargs):
                            response.found.name = response.found.name + '-other'; yield response
                    self.rpc.batch_get_documents = wrong
                with self.assertRaises((ValueError, KeyError)): self.gateway.execute(self.request)
                self.assertEqual(self.rpc.commits, 0); self.rpc.docs = saved

    def test_request_invalid_duplicate_forbidden_bool(self):
        for variant in ('duplicate', 'forbidden', 'boolean', 'count'):
            deltas = deepcopy(list(self.request.corrections)); evidence = deepcopy(self.request.evidence_references)
            if variant == 'duplicate': deltas[1]['questionId'] = deltas[0]['questionId']
            elif variant == 'forbidden': deltas[0]['allowlist'].append('unknown'); deltas[0]['fields']['unknown'] = {'beforePresent': False, 'before': None, 'after': 1}
            elif variant == 'boolean': evidence['productionApproval'] = True
            else: deltas.pop()
            with self.assertRaises(ValueError): self.gateway.execute(DeltaRequest('fixture', tuple(deltas), evidence))
        self.assertEqual(self.rpc.commits, 0)

    def test_atomic_one_conflict_changes_zero_fields(self):
        before = {k: deepcopy(v['fields']) for k, v in self.rpc.docs.items()}
        self.rpc.mode = 'conflict'
        self.assertEqual(self.gateway.execute(self.request)['status'], 'unknown')
        self.assertEqual(before, {k: v['fields'] for k, v in self.rpc.docs.items()})
        self.assertEqual(self.gateway.execute(self.request)['status'], 'mixed_or_conflict'); self.assertEqual(self.rpc.commits, 1)

    def test_intent_persistence_failure_commit_zero(self):
        with patch.object(self.journal, 'begin', side_effect=OSError('fixture durable failure')):
            with self.assertRaises(OSError): self.gateway.execute(self.request)
        self.assertEqual(self.rpc.commits, 0)

    def test_crash_before_commit_readonly_restart(self):
        def crash(phase): raise Crash()
        with self.assertRaises(Crash): self.gateway.execute(self.request, interruption_hook=crash)
        self.assertEqual(self.rpc.commits, 0)
        self.assertEqual(self.gateway.execute(self.request)['status'], 'all_before')
        self.assertEqual(self.rpc.commits, 0)

    def test_lost_response_before(self):
        self.rpc.mode = 'before'
        self.assertEqual(self.gateway.execute(self.request)['status'], 'unknown')
        self.assertEqual(self.gateway.execute(self.request)['status'], 'all_before'); self.assertEqual(self.rpc.commits, 1)

    def test_lost_response_after_not_attributed(self):
        self.rpc.mode = 'after'
        self.assertEqual(self.gateway.execute(self.request)['status'], 'unknown')
        result = self.gateway.execute(self.request)
        self.assertEqual(result['status'], 'all_after'); self.assertFalse(result['attributionConfirmed'])
        self.assertEqual(self.rpc.commits, 1)

    def test_response_journal_failure(self):
        original = self.journal.append
        def failure(operation, state, data):
            if state == 'response_recorded': raise OSError('fixture disk')
            return original(operation, state, data)
        with patch.object(self.journal, 'append', side_effect=failure):
            self.assertEqual(self.gateway.execute(self.request)['status'], 'unknown')
        self.assertFalse(self.gateway.execute(self.request)['attributionConfirmed']); self.assertEqual(self.rpc.commits, 1)

    def test_readback_failure_saved_response_and_completion_failure(self):
        self.rpc.fail_read = 2
        self.assertEqual(self.gateway.execute(self.request)['status'], 'unknown')
        result = self.gateway.reconcile(self.request)
        self.assertEqual(result['status'], 'all_after'); self.assertTrue(result['attributionConfirmed'])
        self.assertFalse(result['completionRecorded']); self.assertEqual(self.rpc.commits, 1)

    def test_completed_journal_failure_stays_unknown(self):
        original = self.journal.append
        def failure(operation, state, data):
            if state == 'completed': raise OSError('fixture completion persistence')
            return original(operation, state, data)
        with patch.object(self.journal, 'append', side_effect=failure):
            self.assertEqual(self.gateway.execute(self.request)['status'], 'unknown')
        result = self.gateway.reconcile(self.request)
        self.assertEqual(result['status'], 'all_after')
        self.assertTrue(result['attributionConfirmed']); self.assertFalse(result['completionRecorded'])
        self.assertEqual(self.rpc.commits, 1)

    def test_process_crash_after_saved_response(self):
        def crash(phase):
            if phase == 'after_response': raise Crash()
        with self.assertRaises(Crash): self.gateway.execute(self.request, interruption_hook=crash)
        restarted = PublicationDeltaGateway(client=self.client, collection_path='questions',
            journal=IntentJournal(self.journal.root))
        result = restarted.execute(self.request)
        self.assertEqual(result['status'], 'all_after'); self.assertTrue(result['attributionConfirmed'])
        self.assertFalse(result['completionRecorded']); self.assertEqual(self.rpc.commits, 1)

    def test_completed_changed_version_not_success(self):
        self.gateway.execute(self.request)
        next(iter(self.rpc.docs.values()))['time'] = '2026-10-04T00:00:03Z'
        result = self.gateway.execute(self.request)
        self.assertEqual(result['status'], 'all_after'); self.assertFalse(result['attributionConfirmed'])
        self.assertEqual(self.rpc.commits, 1)

    def test_unknown_mixed_unavailable(self):
        self.rpc.mode = 'before'; self.gateway.execute(self.request)
        doc = next(iter(self.rpc.docs.values())); doc['fields']['questionText'] = 'unrelated concurrent value'
        self.assertEqual(self.gateway.reconcile(self.request)['status'], 'mixed_or_conflict')
        self.rpc.fail_read = self.rpc.reads + 1
        self.assertEqual(self.gateway.reconcile(self.request)['status'], 'unavailable'); self.assertEqual(self.rpc.commits, 1)

    def test_operation_content_and_target_conflicts(self):
        self.gateway.execute(self.request)
        deltas = deepcopy(list(self.request.corrections)); deltas[0]['fields']['questionText']['after'] = 'different'
        with self.assertRaises(ValueError): self.gateway.execute(DeltaRequest('fixture', tuple(deltas), self.request.evidence_references))
        other = PublicationDeltaGateway(client=self.client, collection_path='other', journal=self.journal)
        with self.assertRaises(ValueError): other.execute(self.request)
        self.assertEqual(self.rpc.commits, 1)

    def test_journal_exclusive_and_tamper(self):
        self.journal.begin(self.gateway._mapping(self.request))
        with self.assertRaises(FileExistsError): self.journal.begin(self.gateway._mapping(self.request))
        path = self.journal.path('fixture') / '0001.json'
        data = json.loads(path.read_bytes()); data['data']['request']['corrections'][0]['questionId'] = 'tampered'
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError): self.gateway.execute(self.request)
        self.assertEqual(self.rpc.commits, 0)

    def test_partial_gap_permissions_and_empty(self):
        for variant in ('partial', 'gap', 'permissions', 'empty'):
            with self.subTest(variant=variant):
                root = Path(self.temp.name).resolve() / variant; journal = IntentJournal(root)
                journal.begin(self.gateway._mapping(self.request)); path = journal.path('fixture') / '0001.json'
                if variant == 'partial': path.write_text('{')
                elif variant == 'gap': path.rename(path.with_name('0002.json'))
                elif variant == 'permissions': path.chmod(0o644)
                else: path.unlink()
                with self.assertRaises((ValueError, KeyError)): journal.read('fixture')

    def test_append_exclusive_race_and_symlink(self):
        mapping = self.gateway._mapping(self.request); self.journal.begin(mapping)
        previous = self.journal.read('fixture')
        self.journal.append('fixture', 'committing', {})
        with self.assertRaises(FileExistsError): self.journal._append(self.journal.path('fixture'), previous[0]['requestHash'], 'committing', {}, previous)
        target = Path(self.temp.name).resolve() / 'link'; target.symlink_to(self.journal.root, target_is_directory=True)
        with self.assertRaises(ValueError): IntentJournal(target)

    def test_nanosecond_and_timezone_equivalence(self):
        self.assertEqual(version('2026-10-04T00:00:00.000000001Z'), version('2026-10-04T09:00:00.000000001+09:00'))
        self.assertNotEqual(version('2026-10-04T00:00:00.000000001Z'), version('2026-10-04T00:00:00.000000002Z'))

if __name__ == '__main__': unittest.main()
