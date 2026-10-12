"""Native-log fixtures only; no accounts or live writes."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.question_review_console.independent_approval import (
    AUTHORIZATION_TEXT, RESULT_SCHEMA, SCHEMA, scope_marker, verify_independent_approval,
)
from tools.question_review_console.formal_correction_save import (
    independent_approval_scope, load_saved_units, save_formal_units,
)
from tests.test_question_review_formal_correction_save import fixture_plan, approvals


def native_audit(root, scope, *, encrypted=True, spawn=False):
    parent = root / 'parent.jsonl'
    child = root / 'reviewer.jsonl'
    identity = '/root/reviewer'
    metadata = {'turn_id': 'author-turn'}
    envelope = 'gAAAA-fixture-envelope' if encrypted else scope_marker(scope)
    verdict = {'schemaVersion': RESULT_SCHEMA, 'scope': scope, 'decision': 'approved',
               'unresolvedFindings': [], 'evidence': ['fixture primary source and field review']}
    final = json.dumps(verdict, ensure_ascii=False)
    def e(kind, payload, time='2026-10-12T00:00:00Z'):
        return {'type': kind, 'timestamp': time, 'payload': payload}
    parent_events = [e('session_meta', {'id': 'author', 'source': 'vscode'}),
        e('response_item', {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': AUTHORIZATION_TEXT}], 'internal_chat_message_metadata_passthrough': {'content_item_kinds': ['user.text']}}),
        e('response_item', {'type': 'custom_tool_call', 'name': 'exec', 'call_id': 'create', 'input': scope['nativeCreationCall'], 'internal_chat_message_metadata_passthrough': metadata}),
        e('response_item', {'type': 'custom_tool_call_output', 'call_id': 'create', 'output': [{'type': 'input_text', 'text': scope_marker(scope, creation=True)}], 'internal_chat_message_metadata_passthrough': metadata}),
        e('response_item', {'type': 'function_call', 'namespace': 'collaboration', 'name': 'spawn_agent' if spawn else 'followup_task', 'call_id': 'delegate', 'arguments': json.dumps({'task_name' if spawn else 'target': 'reviewer' if spawn else identity, 'message': envelope}), 'internal_chat_message_metadata_passthrough': metadata}),
        e('event_msg', {'type': 'item_completed', 'item': {'type': 'SubAgentActivity', 'id': 'delegate', 'kind': 'started' if spawn else 'interacted', 'agent_thread_id': 'reviewer', 'agent_path': identity}}),
        e('response_item', {'type': 'function_call_output', 'call_id': 'delegate', 'output': ''})]
    child_events = [e('session_meta', {'id': 'reviewer', 'parent_thread_id': 'author', 'source': {'subagent': {'thread_spawn': {'parent_thread_id': 'author', 'agent_path': identity}}}}),
        e('event_msg', {'type': 'task_started', 'turn_id': 'review-turn', 'root_turn_id': 'author-turn', 'turn_attribution': {'parent_turn_id': 'author-turn', 'root_turn_id': 'author-turn'}}),
        e('response_item', {'type': 'agent_message', 'author': '/root', 'recipient': identity, 'content': [{'type': 'encrypted_content', 'encrypted_content': envelope}] if encrypted else [{'type': 'input_text', 'text': envelope}], 'internal_chat_message_metadata_passthrough': {'turn_id': 'review-turn'}}),
        e('response_item', {'type': 'message', 'role': 'assistant', 'phase': 'final_answer', 'content': [{'type': 'output_text', 'text': final}], 'internal_chat_message_metadata_passthrough': {'turn_id': 'review-turn'}}, '2026-10-12T00:01:00Z'),
        e('event_msg', {'type': 'task_complete', 'turn_id': 'review-turn', 'root_turn_id': 'author-turn', 'last_agent_message': final}, '2026-10-12T00:01:01Z')]
    def write(path, events):
        lines = [(json.dumps(event, ensure_ascii=False) + '\n').encode() for event in events]
        path.write_bytes(b''.join(lines))
        return lambda n: {'sessionPath': str(path), 'eventLine': n, 'eventSha256IncludingFinalLf': hashlib.sha256(lines[n-1]).hexdigest()}
    pr, cr = write(parent, parent_events), write(child, child_events)
    return {'schemaVersion': SCHEMA, 'authorization': pr(2), 'creation': pr(4),
        'delegation': pr(5), 'delegationActivity': pr(6), 'delegationResult': pr(7),
        'reviewerStarted': cr(2), 'reviewerRequest': cr(3), 'reviewerFinal': cr(4), 'reviewerComplete': cr(5)}


def change(record, key, mutate):
    """Change a fixture event and its hash, to test semantic provenance gates."""
    ref = record[key]; path = Path(ref['sessionPath']); lines = path.read_bytes().splitlines(keepends=True)
    event = json.loads(lines[ref['eventLine'] - 1]); mutate(event)
    line = (json.dumps(event, ensure_ascii=False) + '\n').encode()
    lines[ref['eventLine'] - 1] = line; path.write_bytes(b''.join(lines))
    ref['eventSha256IncludingFinalLf'] = hashlib.sha256(line).hexdigest()


class NativeIndependentApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.path, self.plan = fixture_plan(self.root)
        self.scope = independent_approval_scope(self.plan, self.path, root=self.root)
        self.record = native_audit(self.root, self.scope)
        fixture_root = patch('tools.question_review_console.independent_approval.NATIVE_SESSION_ROOT', self.root)
        fixture_root.start(); self.addCleanup(fixture_root.stop)

    def verify(self):
        return verify_independent_approval(self.record, self.scope, production=False)

    def test_delivered_encrypted_native_audit_and_plain_request(self):
        self.assertNotEqual(self.verify()['authorSessionId'], self.verify()['reviewerSessionId'])
        self.record = native_audit(self.root, self.scope, encrypted=False)
        self.verify()
        self.record = native_audit(self.root, self.scope, spawn=True)
        self.verify()

    def test_reject_scope_drift_forged_hash_and_wrong_session_root(self):
        different = deepcopy(self.scope); different['inputHash'] = 'drift'
        with self.assertRaises(ValueError): verify_independent_approval(self.record, different, production=False)
        with patch('tools.question_review_console.independent_approval.NATIVE_SESSION_ROOT', self.root/'non-native'):
            with self.assertRaises(ValueError): verify_independent_approval(self.record, self.scope, production=True)
        self.record['reviewerFinal']['eventSha256IncludingFinalLf'] = '0' * 64
        with self.assertRaises(ValueError): self.verify()

    def test_reject_missing_or_wrong_native_provenance(self):
        mutations = [
            ('authorization', lambda e: e['payload'].update(role='assistant')),
            ('authorization', lambda e: e['payload']['content'][0].update(text='<heartbeat>' + AUTHORIZATION_TEXT + '</heartbeat>')),
            ('creation', lambda e: e['payload'].update(call_id='not-executed')),
            ('delegation', lambda e: e['payload'].update(namespace='untrusted')),
            ('delegationResult', lambda e: e['payload'].update(call_id='another')),
            ('delegationActivity', lambda e: e['payload']['item'].update(agent_thread_id='author')),
            ('reviewerRequest', lambda e: e['payload']['content'][0].update(encrypted_content='gAAAA-other')),
            ('reviewerStarted', lambda e: e['payload']['turn_attribution'].update(parent_turn_id='another')),
            ('reviewerStarted', lambda e: e['payload'].update(root_turn_id='another')),
            ('reviewerRequest', lambda e: e['payload']['internal_chat_message_metadata_passthrough'].update(turn_id='another')),
            ('reviewerFinal', lambda e: e['payload'].update(phase='analysis')),
            ('reviewerComplete', lambda e: e['payload'].update(turn_id='another')),
            ('reviewerComplete', lambda e: e['payload'].update(root_turn_id='another')),
            ('reviewerComplete', lambda e: e['payload'].update(last_agent_message='different')),
            ('reviewerFinal', lambda e: e.update(timestamp='2026-10-11T00:00:00Z')),
        ]
        for key, mutation in mutations:
            with self.subTest(key=key):
                self.record = native_audit(self.root, self.scope)
                change(self.record, key, mutation)
                with self.assertRaises(ValueError): self.verify()

    def test_reject_self_review_wrong_parent_and_duplicate_scope_marker(self):
        path = Path(self.record['reviewerFinal']['sessionPath'])
        original = path.read_bytes()
        for mutation in (lambda m: m.update(id='author'), lambda m: m.update(parent_thread_id='other')):
            lines = original.splitlines(keepends=True); event = json.loads(lines[0]); mutation(event['payload'])
            lines[0] = (json.dumps(event) + '\n').encode(); path.write_bytes(b''.join(lines))
            with self.assertRaises(ValueError): self.verify()
        path.write_bytes(original)
        change(self.record, 'creation', lambda e: e['payload']['output'][0].update(text=scope_marker(self.scope, creation=True) + '\n' + scope_marker(self.scope, creation=True)))
        with self.assertRaises(ValueError): self.verify()

    def test_reject_denial_findings_missing_evidence_even_with_matching_completion(self):
        for key, value in [('decision', 'rejected'), ('unresolvedFindings', ['unresolved']), ('evidence', [])]:
            self.record = native_audit(self.root, self.scope)
            def mutate(e):
                verdict = json.loads(e['payload']['content'][0]['text']); verdict[key] = value
                e['payload']['content'][0]['text'] = json.dumps(verdict)
            change(self.record, 'reviewerFinal', mutate)
            final = json.loads(Path(self.record['reviewerFinal']['sessionPath']).read_bytes().splitlines()[3])['payload']['content'][0]['text']
            change(self.record, 'reviewerComplete', lambda e: e['payload'].update(last_agent_message=final))
            with self.assertRaises(ValueError): self.verify()

    def test_formal_save_resume_resend_and_receipt_replay_use_separate_mode(self):
        human, law = approvals(self.root, self.plan); receipt = self.root / 'receipt.json'
        with self.assertRaises(ValueError): save_formal_units(self.root, self.path, human_approval=human, subagent_approval=self.record, law_recheck=law, receipt_path=receipt)
        def crash(phase, unit):
            if phase == 'after_rename': raise RuntimeError('interrupted')
        with self.assertRaises(RuntimeError): save_formal_units(self.root, self.path, subagent_approval=self.record, law_recheck=law, receipt_path=receipt, failure_hook=crash)
        self.assertFalse(receipt.exists())
        result = save_formal_units(self.root, self.path, subagent_approval=self.record, law_recheck=law, receipt_path=receipt)
        self.assertEqual(result['schemaVersion'], 'formal-correction-save-receipt/v2')
        self.assertNotIn('humanApprovalEvidence', result)
        self.assertEqual(len(load_saved_units(self.root, self.path, receipt)), 3)
        self.assertEqual(save_formal_units(self.root, self.path, subagent_approval=self.record, law_recheck=law, receipt_path=receipt), result)
        change(self.record, 'reviewerComplete', lambda e: e['payload'].update(last_agent_message='tampered'))
        with self.assertRaises(ValueError): load_saved_units(self.root, self.path, receipt)

    def test_native_change_before_formal_rename_does_not_save(self):
        _, law = approvals(self.root, self.plan)
        def tamper(phase, unit):
            if phase == 'before_rename': change(self.record, 'reviewerComplete', lambda e: e['payload'].update(turn_id='invalid'))
        with self.assertRaises(ValueError):
            save_formal_units(self.root, self.path, subagent_approval=self.record, law_recheck=law, receipt_path=self.root/'receipt.json', failure_hook=tamper)
        self.assertFalse((self.root / self.plan['units'][0]['files'][0]['path']).exists())

    def test_mixed_encrypted_final_is_not_machine_verifiable(self):
        change(self.record, 'reviewerFinal', lambda e: e['payload']['content'].append({'type':'encrypted_content','encrypted_content':'gAAAA-hidden'}))
        with self.assertRaises(ValueError): self.verify()

    def test_real_save_rejects_fixture_sessions_even_for_another_repository_root(self):
        _, law = approvals(self.root,self.plan)
        with patch('tools.question_review_console.independent_approval.NATIVE_SESSION_ROOT', Path.home()/'.codex/sessions'):
            with self.assertRaisesRegex(ValueError,'native Codex'):
                save_formal_units(self.root,self.path,subagent_approval=self.record,law_recheck=law,receipt_path=self.root/'receipt.json')
        self.assertFalse((self.root/'output').exists())
