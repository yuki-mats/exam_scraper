"""Native-log fixtures only; no accounts or live writes."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
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



def yielded_native_audit(root, scope, *, mutate=None, completion_before_wait=False):
    """Three real outer exec waits; shell polling stays inside canonical JS."""
    record = native_audit(root, scope)
    parent = Path(record['creation']['sessionPath'])
    base = [json.loads(line) for line in parent.read_bytes().splitlines()]
    metadata = {'turn_id': 'author-turn'}
    def response(kind, **fields):
        return {'type': 'response_item', 'timestamp': '2026-10-12T00:00:00Z',
            'payload': {'type': kind, 'id': 'runtime', **fields,
                'internal_chat_message_metadata_passthrough': metadata}}
    def running(kind, call):
        event = response(kind, call_id=call,
            output='Script running with cell ID 215\nWall time 61.0 seconds\nOutput:\n')
        event['metadata'] = {'client_authored': False, 'fallback_token_limit_override': 12000}
        return event
    launch = deepcopy(base[2]); launch['metadata'] = {'client_authored': False, 'user_input_order': 0}
    events = [*base[:2], {'type': 'turn_context', 'payload': {'turn_id': 'author-turn'}}, launch,
        running('custom_tool_call_output', 'create')]
    for n in range(1, 4):
        event = response('function_call', name='wait', call_id='wait-' + str(n),
            arguments=json.dumps({'cell_id': '215', 'yield_time_ms': 1000, 'max_tokens': 1500}))
        event['metadata'] = {'client_authored': False, 'user_input_order': n}
        events.append(event)
        if n < 3: events.append(running('function_call_output', 'wait-' + str(n)))
    terminal = running('function_call_output', 'wait-3')
    terminal['payload']['output'] = [
        {'type': 'input_text', 'text': 'Script completed\nWall time 37.9 seconds\nOutput:\n'},
        {'type': 'input_text', 'text': scope_marker(scope, creation=True) + '\n'}]
    prefix = 'let r=await tools.exec_command('
    spec, _ = json.JSONDecoder().raw_decode(scope['nativeCreationCall'].split(prefix, 1)[1])
    completed = {'type': 'event_msg', 'timestamp': '2026-10-12T00:00:00Z', 'payload': {
        'type': 'item_completed', 'thread_id': 'author', 'turn_id': 'author-turn',
        'started_at_ms': 1, 'completed_at_ms': 2, 'item': {
            'type': 'CommandExecution', 'id': 'command-complete', 'status': 'completed', 'exit_code': 0,
            'source': 'unified_exec_startup', 'process_id': '12345', 'duration': {'secs': 1, 'nanos': 0},
            'command': ['/bin/zsh', '-lc', spec['cmd']], 'cwd': Path(spec['workdir']).as_uri(),
            'parsed_cmd': [{'type': 'unknown', 'cmd': spec['cmd']}],
            'aggregated_output': terminal['payload']['output'][1]['text']}}}
    if completion_before_wait:
        events = [*events[:5], completed,
            response('message', role='assistant', phase='commentary', content=[{'type': 'output_text', 'text': '結果を確認します。'}]),
            response('reasoning', summary=[]), events[-1], terminal, *base[4:]]
    else: events.extend([completed, terminal, *base[4:]])
    references = {'authorization': events[1], 'creation': terminal, 'delegation': base[4],
        'delegationActivity': base[5], 'delegationResult': base[6]}
    if mutate: mutate(events)
    lines = [(json.dumps(e, ensure_ascii=False) + '\n').encode() for e in events]
    parent.write_bytes(b''.join(lines))
    for name, event in references.items():
        n = next(n for n, value in enumerate(events) if value is event)
        record[name] = {'sessionPath': str(parent), 'eventLine': n + 1,
            'eventSha256IncludingFinalLf': hashlib.sha256(lines[n]).hexdigest()}
    return record


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

    def test_scope_matches_actual_native_exec_terminal_lf(self):
        parent = Path(self.record['creation']['sessionPath'])
        lines = parent.read_bytes().splitlines(keepends=True)
        call = json.loads(lines[2])
        call['payload']['input'] = call['payload']['input'].rstrip('\n') + '\n'
        lines[2] = (json.dumps(call, ensure_ascii=False) + '\n').encode()
        parent.write_bytes(b''.join(lines))
        self.assertEqual(self.verify()['reviewerSessionId'], 'reviewer')
        # Exact provenance still rejects a call with different bytes.
        call['payload']['input'] = call['payload']['input'].rstrip('\n')
        lines[2] = (json.dumps(call, ensure_ascii=False) + '\n').encode()
        parent.write_bytes(b''.join(lines))
        with self.assertRaises(ValueError):
            self.verify()

    @unittest.skipUnless(shutil.which('node'), 'Node is required to verify native creation JS')
    def test_scope_creation_collects_yielded_output_and_rejects_failure(self):
        script = ('const chunks=JSON.parse(process.argv[1]);'
            'const tools={exec_command:async()=>chunks.shift(),write_stdin:async()=>chunks.shift()};'
            'const text=s=>process.stdout.write(s);(async()=>{'
            + self.scope['nativeCreationCall']
            + '})().catch(e=>{process.stderr.write(e.message);process.exitCode=1;});')
        success = subprocess.run(['node', '-e', script, json.dumps([
            {'output': 'prefix\n', 'session_id': 7}, {'output': '', 'session_id': 7},
            {'output': 'NATIVE_CANDIDATE_SCOPE=fixed\n', 'exit_code': 0}])],
            capture_output=True, text=True)
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(success.stdout, 'prefix\nNATIVE_CANDIDATE_SCOPE=fixed\n')
        for chunks in ([{'output': 'invalid candidate', 'exit_code': 1}],
                       [{'output': '', 'session_id': 7}, {'output': 'invalid candidate', 'exit_code': 1}]):
            failed = subprocess.run(['node', '-e', script, json.dumps(chunks)],
                capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(failed.stdout, '')

    def test_yielded_creation_replays_exact_chain_and_same_record_resends(self):
        self.record = yielded_native_audit(self.root, self.scope)
        binding = self.verify(); proof = binding['creationExecution']
        self.assertEqual(proof['schemaVersion'], 'native-yielded-scope-creation/v1')
        self.assertEqual(proof['waitCallIds'], ['wait-1', 'wait-2', 'wait-3'])
        self.assertEqual(proof['cellId'], '215')
        self.assertEqual(binding, self.verify())
        _, law = approvals(self.root, self.plan); receipt = self.root / 'yielded-receipt.json'
        def crash(phase, unit):
            if phase == 'after_rename': raise RuntimeError('interrupted')
        with self.assertRaises(RuntimeError):
            save_formal_units(self.root, self.path, subagent_approval=self.record,
                law_recheck=law, receipt_path=receipt, failure_hook=crash)
        result = save_formal_units(self.root, self.path, subagent_approval=self.record,
            law_recheck=law, receipt_path=receipt)
        self.assertEqual(result['subagentApprovalBinding'], binding)
        self.assertEqual(len(load_saved_units(self.root, self.path, receipt)), 3)
        self.assertEqual(save_formal_units(self.root, self.path, subagent_approval=self.record,
            law_recheck=law, receipt_path=receipt), result)
        # Same terminal reference, changed intermediate native bytes: the
        # persisted proof must differ and cannot be reinterpreted as success.
        parent = Path(self.record['creation']['sessionPath']); lines = parent.read_bytes().splitlines(keepends=True)
        event = json.loads(lines[4]); event['payload']['output'] = event['payload']['output'].replace('61.0', '62.0')
        lines[4] = (json.dumps(event, ensure_ascii=False) + '\n').encode(); parent.write_bytes(b''.join(lines))
        self.assertNotEqual(self.verify()['creationExecution'], proof)
        with self.assertRaises(ValueError): load_saved_units(self.root, self.path, receipt)

    def test_yielded_creation_accepts_completion_before_its_single_final_wait(self):
        def progress(events):
            wait = deepcopy(events[8]); wait['payload']['call_id'] = 'progress-before-completion'
            wait['metadata']['user_input_order'] = 1
            output = deepcopy(events[4]); output['payload'].update(type='function_call_output', call_id='progress-before-completion')
            events[5:5] = [wait, output]
        for prior_progress in (None, progress):
            with self.subTest(prior_progress=prior_progress is not None):
                self.record = yielded_native_audit(self.root, self.scope,
                    completion_before_wait=True, mutate=prior_progress)
                result = self.verify(); proof = result['creationExecution']
                self.assertEqual(proof['cellId'], '215')
                self.assertEqual(proof['waitCallIds'], (['progress-before-completion'] if prior_progress else []) + ['wait-3'])
                self.assertLess(proof['commandCompletionEventLine'], proof['terminalEventLine'] - 1)
                self.assertEqual(result, self.verify())

    def test_completion_before_wait_refuses_nearby_invalid_chains(self):
        def extra_wait(events):
            wait = deepcopy(events[8]); wait['payload']['call_id'] = 'extra-final-wait'
            wait['metadata']['user_input_order'] = 4
            events.insert(9, wait)
        def yield_again(events):
            output = deepcopy(events[4]); output['payload'].update(type='function_call_output', call_id='wait-3')
            events.insert(9, output)
        def completion_before_yield(events):
            events.insert(4, events.pop(5))
        mutations = [
            completion_before_yield,
            lambda e: e.insert(6, deepcopy(e[5])),
            lambda e: e[5]['payload']['item'].update(exit_code=1),
            lambda e: e[5]['payload']['item'].update(status='failed'),
            lambda e: e[5]['payload']['item'].update(command=['/bin/zsh', '-lc', 'foreign command']),
            lambda e: e[5]['payload']['item'].update(aggregated_output='different stdout'),
            lambda e: e[5]['payload'].update(thread_id='foreign-session'),
            lambda e: e[5]['payload'].update(turn_id='foreign-turn'),
            lambda e: e[8]['payload'].update(name='other_tool'),
            lambda e: e[8]['payload'].update(arguments=json.dumps({'cell_id': 'foreign-cell'})),
            lambda e: e[8]['payload'].update(status='failed'),
            lambda e: e[8]['payload']['internal_chat_message_metadata_passthrough'].update(turn_id='foreign-turn'),
            lambda e: e[8]['payload'].update(call_id='create'),
            extra_wait, yield_again,
            lambda e: e.insert(8, deepcopy(e[3])),
            lambda e: e.insert(8, {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                'content': [{'type': 'input_text', 'text': 'new instruction'}]}}),
            lambda e: e[9]['payload']['output'][0].update(text='Script failed\nOutput:\n'),
            lambda e: e[9]['payload']['output'][1].update(text='!' + scope_marker(self.scope, creation=True)[1:] + '\n'),
            lambda e: e[9]['payload']['output'][1].update(text=scope_marker(self.scope, creation=True) + '\nchanged-marker\n'),
        ]
        # Same marker plus an extra stdout line must not match the command's
        # exact stdout, even though marker extraction itself would succeed.
        for number, mutation in enumerate(mutations):
            with self.subTest(number=number):
                self.record = yielded_native_audit(self.root, self.scope, completion_before_wait=True, mutate=mutation)
                with self.assertRaises(ValueError): self.verify()

    def test_pending_wait_completion_cannot_enter_before_wait_race_branch(self):
        def mutation(events):
            wait = deepcopy(events[9]); wait['payload']['call_id'] = 'new-after-pending-completion'
            wait['metadata']['user_input_order'] = 4
            events.insert(-4, wait)
        self.record = yielded_native_audit(self.root, self.scope, mutate=mutation)
        with self.assertRaises(ValueError): self.verify()

    def test_yielded_creation_rejects_foreign_calls_turns_metadata_and_incomplete_results(self):
        def excessive_waits(events):
            more = []
            for n in range(4, 66):
                wait = deepcopy(events[5]); wait['payload']['call_id'] = 'wait-' + str(n)
                wait['metadata']['user_input_order'] = n
                result = deepcopy(events[6]); result['payload']['call_id'] = 'wait-' + str(n)
                more.extend([wait, result])
            events[9]['metadata']['user_input_order'] = 66
            events[9:9] = more
        mutations = [
            lambda e: e[5]['payload'].update(name='other_tool'),
            lambda e: e[5]['payload'].update(status='failed'),
            lambda e: e[5]['payload'].update(status='interrupted'),
            lambda e: e[5]['payload'].update(arguments=json.dumps({'cell_id': 'different'})),
            lambda e: e[5]['payload'].update(arguments=json.dumps({'cell_id': '215', 'terminate': True})),
            lambda e: e[5]['payload'].update(call_id='create'),
            lambda e: e[7]['payload'].update(call_id='wait-1'),
            lambda e: e[6]['payload'].update(call_id='foreign-output'),
            lambda e: e[5]['payload']['internal_chat_message_metadata_passthrough'].update(turn_id='other-turn'),
            lambda e: e[5]['metadata'].update(unknown='unsupported'),
            lambda e: e[4]['metadata'].update(client_authored=True),
            lambda e: e[3]['metadata'].update(unknown='unsupported'),
            lambda e: e[3]['payload'].update(input='text("later scope");'),
            lambda e: e.insert(5, deepcopy(e[3])),
            lambda e: e.insert(3, {'type': 'response_item', 'payload': {'type': 'custom_tool_call',
                'name': 'exec', 'call_id': 'create', 'input': 'different earlier operation'}}),
            lambda e: e.insert(3, {'type': 'response_item', 'payload': {'type': 'function_call',
                'name': 'other_tool', 'call_id': 'wait-1', 'arguments': '{}'}}),
            lambda e: e.insert(5, {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                'content': [{'type': 'input_text', 'text': 'new instruction'}]}}),
            lambda e: e.insert(5, {'type': 'turn_context', 'payload': {'turn_id': 'other-turn'}}),
            lambda e: e[-4]['payload'].update(output='Script running with cell ID 215\nWall time 1.0 seconds\nOutput:\n'),
            lambda e: e[-4]['payload']['output'][0].update(text='Script failed\nOutput:\n'),
            lambda e: e[-4]['payload']['output'][0].update(text='Script interrupted\nOutput:\n'),
            lambda e: e[-4]['payload']['output'][1].update(text=scope_marker(self.scope, creation=True) + '\n' + scope_marker(self.scope, creation=True) + '\n'),
            lambda e: e[-4]['payload']['output'][1].update(unknown='unsupported'),
            lambda e: e[-4]['payload'].update(call_id='wait-2'),
            lambda e: e[-4]['metadata'].update(fallback_token_limit_override=True),
            lambda e: e[-4]['payload']['internal_chat_message_metadata_passthrough'].update(turn_id='other-turn'),
            lambda e: e[-4]['payload']['output'][1].update(text=scope_marker(self.scope, creation=True)),
            lambda e: e[-5]['payload']['item'].update(command=['/bin/zsh', '-lc', 'different command']),
            lambda e: e[-5]['payload']['item'].update(cwd='file:///different-repo'),
            lambda e: e[-5]['payload']['item'].update(exit_code=1),
            lambda e: e[-5]['payload']['item'].update(aggregated_output='different stdout'),
            lambda e: e[-5]['payload']['item'].update(source='other_tool'),
            lambda e: e[-5]['payload']['item']['duration'].update(secs=True),
            lambda e: e[-5]['payload']['item'].update(unknown='unsupported'),
            lambda e: e[-5]['payload'].update(turn_id='other-turn'),
            lambda e: e.pop(-5),
            lambda e: e.insert(-4, deepcopy(e[-5])),
            lambda e: e.insert(-4, deepcopy(e[5])),
            lambda e: e.insert(-3, deepcopy(e[5])),
            lambda e: e.insert(-3, {'type': 'turn_context', 'payload': {'turn_id': 'other-turn'}}),
            lambda e: e.insert(-3, {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                'content': [{'type': 'input_text', 'text': 'new instruction'}]}}),
            excessive_waits,
        ]
        for n, mutation in enumerate(mutations):
            with self.subTest(n=n):
                self.record = yielded_native_audit(self.root, self.scope, mutate=mutation)
                with self.assertRaises(ValueError): self.verify()

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

    def test_present_completion_without_final_memory_footer_is_not_an_exact_match(self):
        footer = '\n<oai-mem-citation>fixture footer</oai-mem-citation>'
        change(self.record, 'reviewerFinal',
            lambda e: e['payload']['content'][0].update(text=e['payload']['content'][0]['text'] + footer))
        # The JSON verdict is unchanged; task_complete still omits the footer.
        with self.assertRaisesRegex(ValueError, 'reviewer completed turn binding differs'):
            self.verify()

    def test_real_save_rejects_fixture_sessions_even_for_another_repository_root(self):
        _, law = approvals(self.root,self.plan)
        with patch('tools.question_review_console.independent_approval.NATIVE_SESSION_ROOT', Path.home()/'.codex/sessions'):
            with self.assertRaisesRegex(ValueError,'native Codex'):
                save_formal_units(self.root,self.path,subagent_approval=self.record,law_recheck=law,receipt_path=self.root/'receipt.json')
        self.assertFalse((self.root/'output').exists())
