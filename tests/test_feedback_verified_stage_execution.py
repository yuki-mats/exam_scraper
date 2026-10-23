"""Temporary verified25/native fixtures; no real ledger, service or patch writes."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import pytest

from tools.question_bank import feedback_verified_stage_execution as module
from tests import test_feedback_verified_review_context as context_fixtures
from tests.test_native_independent_approval import native_audit, change
from tools.question_review_console.firestore_readback import FirestoreReadback
from scripts.upload import upload_questions_to_firestore as uploader


@pytest.fixture(autouse=True)
def prohibit_live_firestore(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('live Firestore SDK is forbidden in native-stage tests')
    monkeypatch.setattr(FirestoreReadback, '_database', forbidden)
    monkeypatch.setattr(uploader, 'init_firestore', forbidden)
    monkeypatch.setattr(uploader.firestore, 'client', forbidden)
    monkeypatch.setattr(uploader.firebase_admin, 'initialize_app', forbidden)


class VerifiedStageExecutionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = context_fixtures.VerifiedReviewContextTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.root, self.db, self.private = f.root, f.db, f.private
        for row in f.rows:
            row['questionImageStorageUrls'] = []
            row['originalQuestionChoiceImageUrls'] = []
            row['isCalculationQuestion'] = False
        f.plan['candidateSha256'] = f.write(f.canonical, f.bundle)['sha256']
        f.seal_plan()
        f.policies['maintenance']['question_type'].update(
            updateTargets=[{'fields': list(module.FIELDS)}],
            agentPolicy={'independent_review': {'model': 'gpt-5.6-sol', 'reasoningEffort': 'high'}})
        self.context = f.output
        f.prepare(official_order=f.mapping_path)
        self.output = self.private / 'execution'

    def prepare(self, phase='aggregate_a', output=None, candidate=None, priors=(), **kwargs):
        return module.prepare(self.root, self.context, output or self.output, connection=self.db,
                              phase=phase, candidate=candidate, priors=priors, **kwargs)

    def approval(self, output=None, *, reviewer='reviewer', model='gpt-5.6-sol', effort='high'):
        output = output or self.output
        scope = module.scope(self.root, output, connection=self.db)
        folder = self.fixture.sessions / output.name
        folder.mkdir(exist_ok=True)
        record = native_audit(folder, scope, encrypted=False)
        parent = Path(record['authorization']['sessionPath'])
        child = Path(record['reviewerFinal']['sessionPath'])
        # Rename the fixture reviewer in all semantic locations without changing
        # scope, then insert the real-runtime turn_context shape before final.
        for path in (parent, child):
            events = [json.loads(raw) for raw in path.read_bytes().splitlines()]
            for event in events:
                def replace(value):
                    if isinstance(value, dict): return {k: replace(v) for k, v in value.items()}
                    if isinstance(value, list): return [replace(v) for v in value]
                    if isinstance(value, str):
                        if value == 'reviewer': return reviewer
                        if value == '/root/reviewer': return '/root/' + reviewer
                        if value.startswith('{'):
                            try: return json.dumps(replace(json.loads(value)), ensure_ascii=False)
                            except ValueError: pass
                    return value
                event['payload'] = replace(event['payload'])
            if path == child:
                events.insert(3, {'type': 'turn_context', 'payload': {'turn_id': 'review-turn', 'model': model, 'effort': effort}})
            raw_lines = [(json.dumps(e, ensure_ascii=False) + '\n').encode() for e in events]
            path.write_bytes(b''.join(raw_lines))
            for key, ref in record.items():
                if isinstance(ref, dict) and ref.get('sessionPath') == str(path):
                    if path == child and ref['eventLine'] >= 4: ref['eventLine'] += 1
                    ref['eventSha256IncludingFinalLf'] = module.context_api._hash(raw_lines[ref['eventLine'] - 1])
        return record

    def record(self, approval, output=None, **kwargs):
        return module.record(self.root, output or self.output, approval, connection=self.db, **kwargs)

    def inspect(self, output=None):
        return module.inspect(self.root, output or self.output, connection=self.db)

    def classification(self):
        return {'decisionsByDocument': {i: {'questionType': 'true_false', 'isCalculationQuestion': False} for i in self.fixture.ids},
                'reasonsByDocument': {i: {'questionType': '各記述そのものの正誤を別々に学ぶ。',
                    'isCalculationQuestion': '計算で新たな数量を導かない。'} for i in self.fixture.ids}}

    def priors(self):
        result = []
        for number, phase in enumerate(module.PHASES[:2]):
            output = self.private / phase
            self.prepare(phase, output)
            self.record(self.approval(output, reviewer='reviewer' + str(number)), output)
            result.append(output)
        return result

    def test_full_current_01_executes_three_distinct_native_sessions_and_readback(self):
        priors = self.priors()
        profile = {'model': 'gpt-6.1-sol', 'reasoningEffort': 'high'}
        self.prepare('classification', candidate=self.classification(), priors=priors, execution_profile=profile)
        approval = self.approval(reviewer='classification-reviewer', model='gpt-6.1-sol')
        receipt = self.record(approval)
        self.assertEqual(receipt, self.inspect())
        self.assertEqual(receipt, self.record(approval))
        self.assertEqual(receipt, self.prepare('classification', candidate=self.classification(), priors=priors,
                                              execution_profile=profile))
        self.assertEqual(json.loads((self.output / 'stage-plan.json').read_bytes())['reviewerProfileSource'],
                         'execution_configuration')
        self.assertTrue(receipt['contentAuditValidated'])
        self.assertTrue(receipt['stageCompleted'])
        for key in ('publicationReady', 'checkpointRecorded', 'evaluationPerformed', 'formalPatchSaved', 'FirestoreWritten'):
            self.assertIs(receipt[key], False)
        self.assertEqual(set(receipt['unsupportedStageIds']),
                         {'question_intent', 'correct_choice', 'law_context', 'explanation', 'law_audit', 'question_set'})

    def test_aggregate_receipt_never_completes_stage_and_input_hides_current_values(self):
        prepared = self.prepare()
        self.assertFalse(prepared['stageCompleted'])
        stage_input = json.loads((self.output / 'stage-input.json').read_bytes())
        text = json.dumps(stage_input['statements'])
        for field in ('questionType', 'correctChoiceText', 'explanationText', 'isCalculationQuestion'):
            self.assertNotIn(field, text)
        receipt = self.record(self.approval())
        self.assertFalse(receipt['stageCompleted'])
        self.assertEqual(receipt['holdReasons'], ['classification_not_executed'])

    def test_no_native_approval_or_wrong_model_effort_cannot_record(self):
        self.prepare()
        for approval in ({}, self.approval(model='gpt-6.1-sol'), self.approval(effort='low')):
            with self.subTest(approval=approval.get('schemaVersion')):
                with self.assertRaises(ValueError): self.record(approval)
                self.assertFalse((self.output / 'execution-receipt.json').exists())

    def test_rejection_incomplete_native_completion_and_self_review_are_refused(self):
        self.prepare()
        for key, mutate in (
            ('reviewerComplete', lambda e: e['payload'].update(last_agent_message='different final')),
            ('reviewerFinal', lambda e: e['payload'].update(phase='commentary')),
            ('delegationActivity', lambda e: e['payload']['item'].update(agent_thread_id='author')),
        ):
            approval = self.approval()
            change(approval, key, mutate)
            with self.assertRaises(ValueError): self.record(approval)
        approval = self.approval()
        change(approval, 'reviewerFinal', lambda e: e['payload']['content'][0].update(
            text=e['payload']['content'][0]['text'].replace('"approved"', '"rejected"')))
        with self.assertRaises(ValueError): self.record(approval)

    def test_classification_requires_two_distinct_prior_sessions_and_new_final_session(self):
        priors = self.priors()
        for supplied in ((), priors[:1], list(reversed(priors)), [priors[0], priors[0]]):
            with self.assertRaises(ValueError): self.prepare('classification', candidate=self.classification(), priors=supplied)
        self.prepare('classification', candidate=self.classification(), priors=priors)
        with self.assertRaises(ValueError): self.record(self.approval(reviewer='reviewer0'))

    def test_classification_missing_field_or_id_or_extra_field_or_nonboolean_is_refused(self):
        stage_input = {'questionBodyText': '原問', 'statements': [{'questionId': i} for i in self.fixture.ids]}
        for mutate in (
            lambda c: c['decisionsByDocument'].pop(self.fixture.ids[0]),
            lambda c: c['decisionsByDocument'][self.fixture.ids[0]].pop('isCalculationQuestion'),
            lambda c: c['decisionsByDocument'][self.fixture.ids[0]].update(isCalculationQuestion=0),
            lambda c: c['decisionsByDocument'][self.fixture.ids[0]].update(correctChoiceText='正しい'),
            lambda c: c['reasonsByDocument'][self.fixture.ids[0]].update(questionType=''),
        ):
            candidate = self.classification(); mutate(candidate)
            with self.assertRaises(ValueError): module._validate_output('classification', candidate, stage_input)

    def test_field_differences_are_audited_but_not_saved_or_marked_completed(self):
        priors = self.priors()
        candidate = self.classification()
        for value in candidate['decisionsByDocument'].values(): value['isCalculationQuestion'] = True
        self.prepare('classification', candidate=candidate, priors=priors)
        before = self.fixture.canonical.read_bytes()
        receipt = self.record(self.approval(reviewer='third'))
        self.assertFalse(receipt['stageCompleted'])
        self.assertTrue(receipt['contentAuditValidated'])
        self.assertEqual(len(receipt['remainingFieldDiffs']), 5)
        self.assertEqual(receipt['holdReasons'], ['stage_output_requires_formal_save'])
        self.assertEqual(before, self.fixture.canonical.read_bytes())

    def test_current_context_and_policy_are_required_at_every_operation(self):
        self.prepare(); approval = self.approval()
        self.fixture.policy_file.write_text('policy changed')
        for action in (lambda: self.prepare(), lambda: module.scope(self.root, self.output, connection=self.db),
                       lambda: self.record(approval), lambda: self.inspect()):
            with self.assertRaises(ValueError): action()
        self.assertFalse((self.output / 'execution-receipt.json').exists())

    def test_output_conflicts_and_foreign_scope_are_preserved(self):
        self.output.mkdir(); (self.output / 'foreign.json').write_text('keep')
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual((self.output / 'foreign.json').read_text(), 'keep')

    def test_partial_prepare_and_partial_record_resume_only_same_inputs(self):
        def fail(name):
            if name == 'stage-input.json': raise RuntimeError('interrupted')
        with self.assertRaises(RuntimeError): self.prepare(failure_hook=fail)
        self.prepare()
        approval = self.approval()
        def fail_approval(name):
            if name == 'native-approval.json': raise RuntimeError('interrupted')
        with self.assertRaises(RuntimeError): self.record(approval, failure_hook=fail_approval)
        self.assertFalse((self.output / 'execution-receipt.json').exists())
        self.assertEqual(self.record(approval), self.inspect())

    def test_final_reads_recheck_inputs_and_output_set_for_prepare_record_inspect(self):
        original_output = self.output
        try:
            for operation in ('prepare', 'record', 'inspect'):
                for injection in ('foreign', 'input'):
                    with self.subTest(operation=operation, injection=injection):
                        self.output = self.private / ('race-' + operation + '-' + injection)
                        if operation != 'prepare': self.prepare()
                        approval = self.approval() if operation != 'prepare' else None
                        if operation == 'inspect': self.record(approval)
                        original_finish, original_read = module._finish, Path.read_bytes
                        active = False
                        injected = False
                        before = self.fixture.policy_file.read_bytes()
                        def finish(*args, **kwargs):
                            nonlocal active
                            active = True
                            return original_finish(*args, **kwargs)
                        def read(path):
                            nonlocal injected
                            raw = original_read(path)
                            if active and not injected and path == self.output / 'stage-output.json':
                                injected = True
                                if injection == 'foreign': (self.output / 'other-scope.json').write_text('keep')
                                else: self.fixture.policy_file.write_bytes(before + b' drift')
                            return raw
                        try:
                            with patch.object(module, '_finish', side_effect=finish), patch.object(Path, 'read_bytes', read):
                                with self.assertRaises(ValueError):
                                    if operation == 'prepare': self.prepare()
                                    elif operation == 'record': self.record(approval)
                                    else: self.inspect()
                            self.assertTrue(injected)
                        finally: self.fixture.policy_file.write_bytes(before)
        finally: self.output = original_output

    def revoke(self, directory):
        approval = json.loads((directory / 'native-approval.json').read_bytes())
        path = Path(approval['reviewerFinal']['sessionPath'])
        final = json.loads(path.read_bytes().splitlines()[approval['reviewerFinal']['eventLine'] - 1])
        verdict = json.loads(final['payload']['content'][0]['text'])
        verdict['decision'] = 'rejected'
        final['payload']['content'][0]['text'] = json.dumps(verdict, ensure_ascii=False)
        with path.open('ab') as stream:
            stream.write((json.dumps(final, ensure_ascii=False) + '\n').encode())

    def test_public_and_legacy_body_and_choice_images_all_require_image_adapter(self):
        f = self.fixture
        original = deepcopy(f.rows[0])
        for field in module.IMAGE_FIELDS:
            with self.subTest(field=field):
                f.rows[0].clear(); f.rows[0].update(deepcopy(original))
                f.rows[0][field] = ['https://example.invalid/required-image.png']
                f.plan['candidateSha256'] = f.write(f.canonical, f.bundle)['sha256']
                f.seal_plan()
                f.output = self.private / ('image-context-' + field)
                f.prepare(official_order=f.mapping_path)
                self.context = f.output
                self.output = self.private / ('image-stage-' + field)
                with self.assertRaisesRegex(ValueError, 'image-bearing stage execution'):
                    self.prepare()
                self.assertFalse(self.output.exists())

    def test_prior_revocation_during_record_is_rejected_and_never_returns_completion(self):
        priors = self.priors()
        self.prepare('classification', candidate=self.classification(), priors=priors)
        approval = self.approval(reviewer='classification')
        def revoke_prior(name):
            if name == 'native-approval.json': self.revoke(priors[0])
        with self.assertRaisesRegex(ValueError, 'replaced or revoked|new decision input'):
            self.record(approval, failure_hook=revoke_prior)
        with self.assertRaises(ValueError): self.inspect()

    def test_foreign_scope_added_to_prior_during_record_is_rejected(self):
        priors = self.priors()
        self.prepare('classification', candidate=self.classification(), priors=priors)
        approval = self.approval(reviewer='classification')
        def foreign_prior(name):
            if name == 'native-approval.json': (priors[0] / 'other-scope.json').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'stage output scope'):
            self.record(approval, failure_hook=foreign_prior)
        self.assertEqual((priors[0] / 'other-scope.json').read_text(), 'keep')

    def test_late_prior_revocation_is_rejected_by_all_public_operation_terminals(self):
        # Every operation reaches the same _finish contract. Inject only into
        # the classification's final output comparison, after prior derivation.
        for operation in ('prepare', 'scope', 'record', 'inspect'):
            with self.subTest(operation=operation):
                # A separate fixture keeps a rejected review from contaminating
                # the next operation's genuinely validated starting state.
                case = VerifiedStageExecutionTests()
                case.setUp()
                try:
                    priors = case.priors()
                    if operation != 'prepare':
                        case.prepare('classification', candidate=case.classification(), priors=priors)
                    approval = case.approval(reviewer='classification') if operation in ('record', 'inspect') else None
                    if operation == 'inspect': case.record(approval)
                    original_finish = module._finish
                    original_read = Path.read_bytes
                    active = False
                    injected = False
                    def finish(output, *args, **kwargs):
                        nonlocal active
                        if output == case.output: active = True
                        return original_finish(output, *args, **kwargs)
                    def read(path):
                        nonlocal injected
                        raw = original_read(path)
                        if active and not injected and path == case.output / 'stage-output.json':
                            injected = True
                            case.revoke(priors[0])
                        return raw
                    with patch.object(module, '_finish', side_effect=finish), patch.object(Path, 'read_bytes', read):
                        with self.assertRaisesRegex(ValueError, 'replaced or revoked|native session changed'):
                            if operation == 'prepare':
                                case.prepare('classification', candidate=case.classification(), priors=priors)
                            elif operation == 'scope': module.scope(case.root, case.output, connection=case.db)
                            elif operation == 'record': case.record(approval)
                            else: case.inspect()
                    self.assertTrue(injected)
                finally: case.doCleanups()

    def test_own_approval_revoked_during_record_or_native_verification_is_refused(self):
        self.prepare()
        approval = self.approval()
        native_path = Path(approval['reviewerFinal']['sessionPath'])
        original_native = native_path.read_bytes()
        def revoke_own(name):
            if name == 'execution-receipt.json': self.revoke(self.output)
        with self.assertRaises(ValueError): self.record(approval, failure_hook=revoke_own)
        # Restore the fixture native log, then change it during the verifier's
        # own reads: even a still-valid referenced old final is insufficient.
        native_path.write_bytes(original_native)
        original = module.native.verify_independent_approval
        def append_after_verify(*args, **kwargs):
            binding = original(*args, **kwargs)
            # Even unrelated append activity during a single replay is a race;
            # it must not be hidden by comparing only the referenced final.
            with native_path.open('ab') as stream:
                stream.write(b'{"type":"event_msg","payload":{"type":"fixture-heartbeat"}}\n')
            return binding
        with patch.object(module.native, 'verify_independent_approval', side_effect=append_after_verify):
            with self.assertRaisesRegex(ValueError, 'native session changed'):
                self.record(approval)

    def test_expensive_genuine_context_is_verified_once_per_operation_not_across_operations(self):
        priors = self.priors()
        f = self.fixture
        f.mock_saved.reset_mock()
        self.prepare('classification', candidate=self.classification(), priors=priors)
        self.assertEqual(f.mock_saved.call_count, 1)
        f.mock_saved.reset_mock()
        module.scope(self.root, self.output, connection=self.db)
        self.assertEqual(f.mock_saved.call_count, 1)
        self.assertIsNone(module._OPERATION_CONTEXT.get())

    def test_cached_context_does_not_allow_input_drift_between_prior_replays(self):
        priors = self.priors()
        original = module._load_context
        count = 0
        def change_after_first(*args, **kwargs):
            nonlocal count
            value = original(*args, **kwargs)
            count += 1
            if count == 1: self.fixture.policy_file.write_text('changed after genuine initial context')
            return value
        with patch.object(module, '_load_context', side_effect=change_after_first):
            with self.assertRaises(ValueError):
                self.prepare('classification', candidate=self.classification(), priors=priors)
        self.assertFalse(self.output.exists())
        self.assertIsNone(module._OPERATION_CONTEXT.get())

    @staticmethod
    def usage_event(session_id):
        counters = {key: 0 for key in ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
                                      'output_tokens', 'reasoning_output_tokens', 'total_tokens')}
        return {'timestamp': '2026-10-17T00:00:00Z', 'ordinal': 1, 'type': 'token_usage_record',
                'payload': {'thread_id': session_id, 'session_id': 'distinct-runtime-session',
                    'turn_id': 'runtime-turn', 'root_turn_id': 'runtime-root-turn', 'response_id': 'runtime-response',
                    'usage': deepcopy(counters), 'turn_token_usage': deepcopy(counters), 'thread_token_usage': deepcopy(counters)}}

    @staticmethod
    def append_event(path, event):
        with path.open('ab') as stream:
            stream.write((json.dumps(event, ensure_ascii=False) + '\n').encode())

    def test_runtime_usage_append_is_accepted_for_author_and_reviewer_during_real_native_replay(self):
        self.prepare()
        approval = self.approval()
        original = module.native.verify_independent_approval
        appended = 0
        def usage_after_verify(*args, **kwargs):
            nonlocal appended
            binding = original(*args, **kwargs)
            for ref, identity in (('authorization', 'author'), ('reviewerFinal', 'reviewer')):
                self.append_event(Path(approval[ref]['sessionPath']), self.usage_event(identity))
                appended += 1
            return binding
        with patch.object(module.native, 'verify_independent_approval', side_effect=usage_after_verify):
            receipt = self.record(approval)
        self.assertGreaterEqual(appended, 2)
        self.assertEqual(receipt, self.inspect())
        self.assertNotIn('journal', json.dumps(receipt))

    def test_runtime_usage_append_during_record_and_final_output_reads_is_accepted(self):
        self.prepare()
        approval = self.approval()
        parent = Path(approval['authorization']['sessionPath'])
        def runtime_activity(name):
            self.append_event(parent, self.usage_event('author'))
        original_finish, original_read = module._finish, Path.read_bytes
        active = False
        def finish(*args, **kwargs):
            nonlocal active
            active = True
            return original_finish(*args, **kwargs)
        def read(path):
            raw = original_read(path)
            if active and path == self.output / 'stage-output.json':
                self.append_event(parent, self.usage_event('author'))
            return raw
        with patch.object(module, '_finish', side_effect=finish), patch.object(Path, 'read_bytes', read):
            receipt = self.record(approval, failure_hook=runtime_activity)
        self.assertEqual(receipt, self.inspect())

    def test_consumed_prefix_rewrite_replacement_or_truncation_is_refused(self):
        self.prepare()
        approval = self.approval()
        parent = Path(approval['authorization']['sessionPath'])
        raw = parent.read_bytes()
        original = module.native.verify_independent_approval
        for mutation in ('rewrite', 'replace', 'truncate'):
            with self.subTest(mutation=mutation):
                parent.write_bytes(raw)
                def mutate_after_verify(*args, **kwargs):
                    binding = original(*args, **kwargs)
                    if mutation == 'rewrite':
                        events = raw.splitlines(keepends=True)
                        first = json.loads(events[0]); first['payload']['fixtureOnly'] = True
                        events[0] = (json.dumps(first) + '\n').encode()
                        parent.write_bytes(b''.join(events))
                    elif mutation == 'replace':
                        replacement = parent.with_suffix('.replacement')
                        replacement.write_bytes(raw); replacement.replace(parent)
                    else: parent.write_bytes(raw[:raw.find(b'\n') + 1])
                    return binding
                with patch.object(module.native, 'verify_independent_approval', side_effect=mutate_after_verify):
                    with self.assertRaisesRegex(ValueError, 'native session changed|journal replaced|journal truncated'):
                        self.record(approval)
        parent.write_bytes(raw)

    def test_new_author_instruction_or_scope_context_between_replays_requires_fresh_operation(self):
        self.prepare()
        approval = self.approval()
        parent = Path(approval['authorization']['sessionPath'])
        raw = parent.read_bytes()
        events = [
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                'content': [{'type': 'input_text', 'text': 'fixture: stop this operation'}]}},
            {'type': 'response_item', 'payload': {'type': 'agent_message', 'content': []}},
            {'type': 'turn_context', 'payload': {'turn_id': 'different', 'model': 'different'}},
            {'type': 'response_item', 'payload': {'type': 'custom_tool_call_output', 'output': 'changed scope'}},
        ]
        for event in events:
            with self.subTest(type=event['type'], payloadType=event['payload'].get('type')):
                parent.write_bytes(raw)
                def instruction_during_write(name):
                    if name == 'native-approval.json': self.append_event(parent, event)
                with self.assertRaisesRegex(ValueError, 'new decision input'):
                    self.record(approval, failure_hook=instruction_during_write)
                # A failed operation may retain immutable partial artifacts;
                # only the fixture removes them to isolate the next scenario.
                for filename in module.FINAL_FILES[len(module.BASE_FILES):]:
                    (self.output / filename).unlink(missing_ok=True)
        parent.write_bytes(raw)

    def test_usage_envelope_cannot_smuggle_text_unknown_keys_or_noninteger_counters(self):
        self.prepare()
        approval = self.approval()
        parent = Path(approval['authorization']['sessionPath'])
        raw = parent.read_bytes()
        original = module.native.verify_independent_approval
        mutations = [
            lambda e: e.update(text='hidden directive'),
            lambda e: e['payload'].update(text='hidden directive'),
            lambda e: e['payload']['usage'].update(text='hidden directive'),
            lambda e: e['payload']['usage'].update(output_tokens=True),
            lambda e: e['payload']['usage'].update(output_tokens=-1),
            lambda e: e['payload'].update(thread_id='another-session'),
            lambda e: e.update(type='unrecognized-observation-schema'),
        ]
        for number, mutate in enumerate(mutations):
            with self.subTest(number=number):
                parent.write_bytes(raw)
                event = self.usage_event('author'); mutate(event)
                def append_after_verify(*args, **kwargs):
                    binding = original(*args, **kwargs)
                    self.append_event(parent, event)
                    return binding
                with patch.object(module.native, 'verify_independent_approval', side_effect=append_after_verify):
                    with self.assertRaisesRegex(ValueError, 'new decision input'):
                        self.record(approval)
                self.assertFalse((self.output / 'execution-receipt.json').exists())
        parent.write_bytes(raw)

    def test_incomplete_or_malformed_journal_tail_is_never_accepted(self):
        self.prepare()
        approval = self.approval()
        parent = Path(approval['authorization']['sessionPath'])
        raw = parent.read_bytes()
        original = module.native.verify_independent_approval
        for suffix in (b'{"type":', b'not-json\n', b'{"type":\n'):
            for timing in ('before', 'during'):
                with self.subTest(suffix=suffix, timing=timing):
                    parent.write_bytes(raw)
                    def append_bad_tail(*args, **kwargs):
                        binding = original(*args, **kwargs)
                        with parent.open('ab') as stream: stream.write(suffix)
                        return binding
                    if timing == 'before':
                        with parent.open('ab') as stream: stream.write(suffix)
                    manager = patch.object(module.native, 'verify_independent_approval', side_effect=append_bad_tail)
                    with manager if timing == 'during' else patch.object(module.native, 'verify_independent_approval', wraps=original):
                        with self.assertRaises(ValueError): self.record(approval)
                    self.assertFalse((self.output / 'execution-receipt.json').exists())
        parent.write_bytes(raw)

    def test_native_binding_is_replayed_once_per_exact_proof_and_public_operation(self):
        priors = self.priors()
        self.prepare('classification', candidate=self.classification(), priors=priors)
        approval = self.approval(reviewer='classification')
        original = module.native.verify_independent_approval
        with patch.object(module.native, 'verify_independent_approval', wraps=original) as verify:
            receipt = self.record(approval)
            self.assertEqual(verify.call_count, 3)  # Each prior + own native proof.
            verify.reset_mock()
            self.assertEqual(receipt, self.inspect())
            self.assertEqual(verify.call_count, 3)  # No reuse across operations.
        self.assertIsNone(module._OPERATION_CONTEXT.get())

    def test_native_binding_cache_never_accepts_a_different_exact_scope(self):
        self.prepare()
        approval = self.approval()
        plan, _, _, refs, _ = module._read_plan(self.root, self.output, self.db)
        scope = module._scope(self.root, self.output, plan, refs)
        original = module.native.verify_independent_approval
        @module._operation
        def two_different_scopes():
            module._native_binding(approval, scope, plan)
            other = deepcopy(scope); other['action'] = 'different-action'
            module._native_binding(approval, other, plan)
        with patch.object(module.native, 'verify_independent_approval', wraps=original) as verify:
            with self.assertRaises(ValueError): two_different_scopes()
            self.assertEqual(verify.call_count, 2)
        self.assertIsNone(module._OPERATION_CONTEXT.get())

    def test_author_instruction_during_expensive_genuine_context_replay_is_refused(self):
        self.prepare()
        approval = self.approval()
        parent = Path(approval['authorization']['sessionPath'])
        original = module.context_api.inspect
        def new_instruction(*args, **kwargs):
            value = original(*args, **kwargs)
            self.append_event(parent, {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                'content': [{'type': 'input_text', 'text': 'fixture: stop before native validation'}]}})
            return value
        with patch.object(module.context_api, 'inspect', side_effect=new_instruction):
            with self.assertRaisesRegex(ValueError, 'new decision input'):
                self.record(approval)
        self.assertFalse((self.output / 'native-approval.json').exists())
        self.assertFalse((self.output / 'execution-receipt.json').exists())

    def test_bootstrap_never_absorbs_user_append_before_or_after_first_raw_read(self):
        self.prepare()
        approval = self.approval()
        parent = Path(approval['authorization']['sessionPath'])
        initial = parent.read_bytes()
        original = Path.read_bytes
        stop = {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
            'content': [{'type': 'input_text', 'text': 'fixture: stop in initial native read'}]}}
        for timing in ('before_raw', 'after_raw'):
            with self.subTest(timing=timing):
                parent.write_bytes(initial)
                injected = False
                def initial_read(path):
                    nonlocal injected
                    if path == parent and not injected:
                        injected = True
                        if timing == 'before_raw': self.append_event(parent, stop)
                        raw = original(path)
                        if timing == 'after_raw': self.append_event(parent, stop)
                        return raw
                    return original(path)
                with patch.object(Path, 'read_bytes', initial_read):
                    with self.assertRaisesRegex(ValueError, 'bootstrap changed'):
                        self.record(approval)
                self.assertTrue(injected)
                self.assertFalse((self.output / 'native-approval.json').exists())
                self.assertFalse((self.output / 'execution-receipt.json').exists())
                self.assertIsNone(module._OPERATION_CONTEXT.get())
        parent.write_bytes(initial)

    def test_bootstrap_usage_append_is_held_then_a_fresh_stable_operation_can_replay(self):
        self.prepare()
        approval = self.approval()
        parent = Path(approval['authorization']['sessionPath'])
        original = Path.read_bytes
        injected = False
        def initial_read(path):
            nonlocal injected
            raw = original(path)
            if path == parent and not injected:
                injected = True
                self.append_event(parent, self.usage_event('author'))
            return raw
        with patch.object(Path, 'read_bytes', initial_read):
            with self.assertRaisesRegex(ValueError, 'bootstrap changed'):
                self.record(approval)
        self.assertFalse((self.output / 'execution-receipt.json').exists())
        self.assertEqual(self.record(approval), self.inspect())

    def test_bootstrap_prefix_rewrite_truncate_and_replacement_are_refused_in_both_read_windows(self):
        self.prepare()
        approval = self.approval()
        parent = Path(approval['authorization']['sessionPath'])
        initial = parent.read_bytes()
        original = Path.read_bytes
        for timing in ('before_raw', 'after_raw'):
            for mutation in ('rewrite', 'truncate', 'replace'):
                with self.subTest(timing=timing, mutation=mutation):
                    parent.write_bytes(initial)
                    injected = False
                    def mutate():
                        if mutation == 'rewrite':
                            # Same byte length and ID; native provenance alone
                            # would not notice a non-authoritative metadata edit.
                            parent.write_bytes(initial.replace(b'vscode', b'otherx', 1))
                        elif mutation == 'truncate': parent.write_bytes(initial[:-1])
                        else:
                            replacement = parent.with_suffix('.bootstrap-replacement')
                            replacement.write_bytes(initial); replacement.replace(parent)
                    def initial_read(path):
                        nonlocal injected
                        if path == parent and not injected:
                            injected = True
                            if timing == 'before_raw': mutate()
                            raw = original(path)
                            if timing == 'after_raw': mutate()
                            return raw
                        return original(path)
                    with patch.object(Path, 'read_bytes', initial_read):
                        with self.assertRaisesRegex(ValueError, 'bootstrap changed|journal replaced|journal truncated'):
                            self.record(approval)
                    self.assertTrue(injected)
                    self.assertFalse((self.output / 'execution-receipt.json').exists())
        parent.write_bytes(initial)

    def owner_launch(self, approval, *, command='record', call_id='owner-launch', module_name=None, argv=None):
        if argv is None:
            argv = [command, '--repo-root', str(self.root), '--execution', str(self.output)]
            if command == 'record': argv += ['--approval', str(self.private / 'approval.json')]
        module_name = module_name or 'tools.question_bank.feedback_verified_stage_execution'
        parent = Path(approval['authorization']['sessionPath'])
        self.append_event(parent, {'type': 'turn_context', 'payload': {'turn_id': 'owner-turn'}})
        latest = max((event.get('metadata', {}).get('user_input_order', -1)
                      for event in (json.loads(line) for line in parent.read_bytes().splitlines())), default=-1)
        self.owner_order = latest + 1
        launch = self.owner_response('custom_tool_call', name='exec',
            call_id=call_id, input=module.native_operation_call(argv, module_name=module_name), status='completed')
        launch['metadata'] = {'client_authored': False, 'user_input_order': self.owner_order}
        self.append_event(parent, launch)
        return parent, argv

    @staticmethod
    def owner_response(kind, **fields):
        return {'type': 'response_item', 'payload': {'type': kind, 'id': 'runtime-id', **fields,
            'internal_chat_message_metadata_passthrough': {'turn_id': 'owner-turn'}}}

    def owner_progress(self, *, launch='owner-launch', cell='327'):
        def running(call, kind):
            event = self.owner_response(kind, call_id=call,
                output=f'Script running with cell ID {cell}\nWall time 1.0 seconds\nOutput:\n')
            event['metadata'] = {'client_authored': False, 'fallback_token_limit_override': 12000}
            return event
        events = [running(launch, 'custom_tool_call_output'),
            {'type': 'event_msg', 'payload': {'type': 'item_completed', 'thread_id': 'author',
                'turn_id': 'owner-turn', 'item': {'type': 'Reasoning', 'id': 'reasoning',
                'summary_text': [], 'raw_content': []}, 'started_at_ms': 1, 'completed_at_ms': 2}},
            self.owner_response('reasoning', summary=[], encrypted_content='runtime-output'),
            {'type': 'event_msg', 'payload': {'type': 'item_completed', 'thread_id': 'author',
                'turn_id': 'owner-turn', 'item': {'type': 'AgentMessage', 'id': 'message', 'phase': 'commentary',
                    'content': [{'type': 'Text', 'text': 'verification continues'}]},
                'started_at_ms': 3, 'completed_at_ms': 4}},
            self.owner_response('message', role='assistant', phase='commentary', status='completed',
                content=[{'type': 'output_text', 'text': 'verification continues', 'annotations': [], 'logprobs': []}]),
            self.owner_response('function_call', name='wait', call_id='own-wait',
                arguments=json.dumps({'cell_id': cell, 'yield_time_ms': 1000, 'max_tokens': 1500})),
            running('own-wait', 'function_call_output')]
        events[4]['metadata'] = {'client_authored': False, 'user_input_order': self.owner_order + 1,
            'retained_source': {'complete': True, 'id': {'role': 'assistant', 'turn_id': 'owner-turn',
                'message_id': events[4]['payload']['id']},
                'revision': 'retained_00000000-0000-0000-0000-000000000000'}}
        events[5]['metadata'] = {'client_authored': False, 'user_input_order': self.owner_order + 2}
        return events

    def test_owner_wait_and_own_output_protocol_survives_record_and_fresh_inspect(self):
        self.prepare(); approval = self.approval()
        parent, argv = self.owner_launch(approval)
        def runtime(name):
            if name == 'native-approval.json':
                for event in self.owner_progress(): self.append_event(parent, event)
        with module.native_operation(argv):
            receipt = self.record(approval, failure_hook=runtime)
        parent, argv = self.owner_launch(approval, command='inspect', call_id='inspect-launch')
        original = module.context_api.inspect
        def during_context(*args, **kwargs):
            value = original(*args, **kwargs)
            for event in self.owner_progress(launch='inspect-launch'): self.append_event(parent, event)
            return value
        with module.native_operation(argv), patch.object(module.context_api, 'inspect', side_effect=during_context):
            self.assertEqual(receipt, self.inspect())
        self.assertIsNone(module._CLI_OPERATION.get())
        self.assertIsNone(module._OPERATION_CONTEXT.get())

    def test_owner_wait_protocol_rejects_wrong_cell_terminate_tool_result_and_new_input(self):
        self.prepare(); approval = self.approval()
        parent, argv = self.owner_launch(approval)
        initial = parent.read_bytes()
        mutations = [
            lambda e: e[5]['payload'].update(arguments=json.dumps({'cell_id': 'different'})),
            lambda e: e[5]['payload'].update(arguments=json.dumps({'cell_id': '327', 'terminate': True})),
            lambda e: e[5]['payload'].update(name='exec'),
            lambda e: e[6]['payload'].update(call_id='unrelated-tool'),
            lambda e: e[6]['payload'].update(output='Script running with cell ID 327\nWall time 1.0 seconds\nOutput:\nnew information'),
            lambda e: e[6]['payload'].update(output='Script running with cell ID 328\nWall time 1.0 seconds\nOutput:\n'),
            lambda e: e[4]['payload'].update(role='user'),
            lambda e: e[4]['payload'].update(status='in_progress'),
            lambda e: e[4]['payload']['content'][0].update(annotations=[{'text': 'new source'}]),
            lambda e: e[4]['payload']['content'][0].update(logprobs=[{'text': 'new information'}]),
            lambda e: e[4]['payload']['content'][0].update(logprobs=None),
            lambda e: e[4]['payload']['internal_chat_message_metadata_passthrough'].update(turn_id='other-turn'),
            lambda e: e.append({'type': 'turn_context', 'payload': {'turn_id': 'new-turn'}}),
            lambda e: e.append({'type': 'response_item', 'payload': {'type': 'agent_message', 'content': []}}),
        ]
        for number, mutate in enumerate(mutations):
            with self.subTest(number=number):
                parent.write_bytes(initial)
                events = self.owner_progress(); mutate(events)
                with module.native_operation(argv):
                    guard = module.capture_native_guard([approval])
                    for event in events: self.append_event(parent, event)
                    with self.assertRaisesRegex(ValueError, 'new decision input'):
                        module.assert_native_guard(guard, terminal=True)
        parent.write_bytes(initial)

    def test_owner_continuation_is_not_allowed_in_reviewer_or_without_exact_launch(self):
        self.prepare(); approval = self.approval()
        parent, argv = self.owner_launch(approval)
        reviewer = Path(approval['reviewerFinal']['sessionPath'])
        with module.native_operation(argv):
            guard = module.capture_native_guard([approval])
            self.append_event(reviewer, self.owner_progress()[0])
            with self.assertRaisesRegex(ValueError, 'new decision input'):
                module.assert_native_guard(guard, terminal=True)
        with module.native_operation([*argv, '--different']):
            with self.assertRaisesRegex(ValueError, 'canonical native owner'):
                module.capture_native_guard([approval])
        # The exact operation must be observed already at bootstrap. A caller's
        # claimed argv cannot retroactively legitimize arbitrary assistant tools.
        with self.assertRaises(ValueError): module.native_operation_call(argv, module_name='untrusted.module')

    def test_owner_input_after_launch_before_bootstrap_is_not_absorbed(self):
        self.prepare(); approval = self.approval()
        parent, argv = self.owner_launch(approval)
        self.append_event(parent, self.owner_response('message', role='user',
            content=[{'type': 'input_text', 'text': 'stop'}]))
        with module.native_operation(argv):
            with self.assertRaisesRegex(ValueError, 'after owner launch'):
                module.capture_native_guard([approval])

    def test_owner_wait_requires_output_first_and_matching_pending_call(self):
        self.prepare(); approval = self.approval()
        parent, argv = self.owner_launch(approval)
        initial = parent.read_bytes()
        invalid = [self.owner_progress()[5],
                   self.owner_response('function_call_output', call_id='owner-launch',
                       output='Script running with cell ID 327\nWall time 1.0 seconds\nOutput:\n'),
                   self.owner_response('custom_tool_call_output', call_id='owner-launch',
                       output='Script running with cell ID 327\nWall time 1.0 seconds\nOutput:\n\n')]
        for event in invalid:
            parent.write_bytes(initial)
            with module.native_operation(argv):
                guard = module.capture_native_guard([approval]); self.append_event(parent, event)
                with self.assertRaises(ValueError): module.assert_native_guard(guard, terminal=True)
        parent.write_bytes(initial)

    def test_owner_guard_signature_cache_rehashes_on_change_and_always_at_terminal(self):
        self.prepare(); approval = self.approval()
        parent, argv = self.owner_launch(approval)
        original = Path.read_bytes
        reads = []
        def read(path):
            if path == parent: reads.append(path)
            return original(path)
        with module.native_operation(argv), patch.object(Path, 'read_bytes', read):
            guard = module.capture_native_guard([approval])
            count = len(reads)
            module.assert_native_guard(guard)
            self.assertEqual(len(reads), count)
            self.append_event(parent, self.usage_event('author'))
            module.assert_native_guard(guard)
            self.assertGreater(len(reads), count)
            count = len(reads)
            module.assert_native_guard(guard, terminal=True)
            self.assertGreater(len(reads), count)

    def test_terminal_guard_does_not_trust_even_a_matching_cached_stat(self):
        self.prepare(); approval = self.approval()
        parent, argv = self.owner_launch(approval)
        with module.native_operation(argv):
            guard = module.capture_native_guard([approval])
            original = module._signature
            pinned = guard[parent]['signature']
            raw = parent.read_bytes(); parent.write_bytes(raw.replace(b'owner-launch', b'other-launch'))
            with patch.object(module, '_signature', side_effect=lambda stat: pinned if stat.st_ino == parent.stat().st_ino else original(stat)):
                with self.assertRaisesRegex(ValueError, 'native session changed'):
                    module.assert_native_guard(guard, terminal=True)

    def test_owner_partial_record_resumes_only_with_fresh_real_launch(self):
        self.prepare(); approval = self.approval()
        parent, argv = self.owner_launch(approval)
        def interrupted(name):
            if name == 'native-approval.json':
                for event in self.owner_progress(): self.append_event(parent, event)
                raise RuntimeError('process interrupted after immutable approval')
        with module.native_operation(argv):
            with self.assertRaises(RuntimeError): self.record(approval, failure_hook=interrupted)
        self.assertFalse((self.output / 'execution-receipt.json').exists())
        parent, argv = self.owner_launch(approval, call_id='resume-launch')
        with module.native_operation(argv): receipt = self.record(approval)
        parent, argv = self.owner_launch(approval, command='inspect', call_id='fresh-inspect')
        with module.native_operation(argv): self.assertEqual(receipt, self.inspect())

    def test_delivery_module_uses_same_explicit_owner_guard_contract(self):
        self.prepare(); approval = self.approval()
        name = 'tools.question_bank.feedback_verified_delivery'
        parent, argv = self.owner_launch(approval, command='inspect', module_name=name)
        with module.native_operation(argv, module_name=name):
            guard = module.capture_native_guard([approval])
            for event in self.owner_progress(): self.append_event(parent, event)
            module.assert_native_guard(guard, terminal=True)

    def test_formal_save_record_module_allows_only_its_two_exact_cli_shapes(self):
        self.prepare(); approval = self.approval()
        name = 'tools.question_bank.feedback_verified_save_record'
        base = ['--repo-root', str(self.root), '--db', str(self.private / 'ledger.sqlite3')]
        for command in (['record', '--plan', str(self.private / 'formal-save-plan.json')],
                        ['inspect', '--task-id', 'task', '--task-kind', 'maintenance']):
            parent, argv = self.owner_launch(approval, module_name=name, argv=[*base, *command])
            with module.native_operation(argv, module_name=name):
                guard = module.capture_native_guard([approval])
                for event in self.owner_progress(): self.append_event(parent, event)
                module.assert_native_guard(guard, terminal=True)
        for argv in ([*base, 'scope'], [*base, 'save', '--plan', 'plan'], [*base, 'record'],
                     [*base, 'record', '--plan', 'plan', '--force', 'true'],
                     [*base, 'inspect', '--task-id', 'task', '--task-kind', 'unknown'],
                     [*base, 'inspect', '--task-id', 'task', '--task-id', 'other'],
                     [*base, 'inspect', '--task-id', 'task', '--execution', 'other'],
                     ['record', '--repo-root', str(self.root), '--plan', 'plan'],
                     [*base, '--repo-root', str(self.root), 'inspect', '--task-id', 'task']):
            with self.subTest(argv=argv), self.assertRaises(ValueError): module.native_operation_call(argv, module_name=name)
        with self.assertRaises(ValueError):
            module.native_operation_call([*base, 'record', '--plan', 'plan'], module_name='tools.question_bank.feedback_verified_save')

    def test_source_tf_module_allows_only_exact_bound_owner_commands(self):
        self.prepare(); approval = self.approval()
        name = 'tools.question_bank.feedback_source_tf_delivery'
        base = ['--repo-root', str(self.root), '--db', str(self.private / 'ledger.sqlite3')]
        commands = [
            ['scope', '--plan', 'plan', '--action', 'save'],
            ['scope', '--plan', 'plan', '--action', 'publish'],
            *[[command, '--plan', 'plan'] for command in ('inspect', 'check', 'evaluate', 'reconcile')],
            *[[command, '--plan', 'plan', '--approval', 'approval'] for command in ('save', 'publish')],
        ]
        for command in commands:
            with self.subTest(command=command):
                parent, argv = self.owner_launch(approval, module_name=name, argv=[*base, *command])
                with module.native_operation(argv, module_name=name):
                    guard = module.capture_native_guard([approval])
                    for event in self.owner_progress(): self.append_event(parent, event)
                    module.assert_native_guard(guard, terminal=True)
        invalid = [
            ['prepare', '--request', 'request', '--output', 'output'],
            ['save', '--plan', 'plan'], ['publish', '--plan', 'plan'],
            ['scope', '--plan', 'plan'], ['scope', '--plan', 'plan', '--action', 'unknown'],
            ['check', '--plan', 'plan', '--force', 'true'],
            ['inspect', '--plan', 'plan', '--plan', 'other'],
            ['inspect', '--plan', '--approval'], ['evaluate', '--execution', 'other'],
        ]
        for command in invalid:
            with self.subTest(command=command), self.assertRaises(ValueError):
                module.native_operation_call([*base, *command], module_name=name)
        for argv in (['inspect', '--repo-root', str(self.root), '--plan', 'plan'],
                     ['--db', 'db', 'inspect', '--plan', 'plan'],
                     [*base, '--repo-root', str(self.root), 'inspect', '--plan', 'plan']):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                module.native_operation_call(argv, module_name=name)

    def test_report_review_owner_is_exact_execute_and_same_cell_only(self):
        self.prepare(); approval = self.approval()
        name = 'tools.question_bank.feedback_source_tf_report_review'
        base = ['--repo-root', str(self.root), '--db', str(self.private / 'ledger.sqlite3')]
        command = ['execute', '--request', 'request', '--output', 'output']
        parent, argv = self.owner_launch(approval, module_name=name, argv=[*base, *command])
        initial = parent.read_bytes()
        with module.native_operation(argv, module_name=name):
            guard = module.capture_native_guard([approval])
            for event in self.owner_progress(): self.append_event(parent, event)
            module.assert_native_guard(guard, terminal=True)
        # Same code, different owner input or cell cannot inherit the window.
        bad_events = [self.owner_response('message', role='user', content=[{'type': 'input_text', 'text': 'stop'}]),
            self.owner_response('function_call', name='wait', namespace='functions', call_id='wrong-cell',
                arguments=json.dumps({'cell_id': 'different', 'max_tokens': 1000})),
            self.owner_response('custom_tool_call', name='exec', call_id='other-tool', input='text(1);')]
        for event in bad_events:
            parent.write_bytes(initial)
            with module.native_operation(argv, module_name=name):
                guard = module.capture_native_guard([approval]); self.append_event(parent, event)
                with self.assertRaises(ValueError): module.assert_native_guard(guard, terminal=True)
        # The final LF and exact executable source are part of origin proof.
        for mutate in (lambda text: text.rstrip(), lambda text: text.replace('max_output_tokens": 16000', 'max_output_tokens": 15999')):
            events = [json.loads(line) for line in initial.splitlines()]
            events[-1]['payload']['input'] = mutate(events[-1]['payload']['input'])
            parent.write_bytes(b''.join((json.dumps(event) + '\n').encode() for event in events))
            with module.native_operation(argv, module_name=name):
                with self.assertRaises(ValueError): module.capture_native_guard([approval])
        for invalid in (['scope', '--request', 'request', '--output', 'output'],
                        ['execute', '--request', 'request'],
                        [*command, '--approval', 'approval'],
                        ['execute', '--request', 'request', '--request', 'other'],
                        ['execute', '--request', '--output', '--output', 'output']):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                module.native_operation_call([*base, *invalid], module_name=name)
        for invalid in ([*base[:2], *command], [*base, '--db', 'other', *command]):
            with self.assertRaises(ValueError): module.native_operation_call(invalid, module_name=name)

    def token_count_event(self):
        counters = self.usage_event('author')['payload']['usage']
        return {'type': 'event_msg', 'payload': {'type': 'token_count',
            'info': {'total_token_usage': deepcopy(counters), 'last_token_usage': deepcopy(counters),
                     'model_context_window': 475000},
            'rate_limits': {'limit_id': 'codex', 'limit_name': None,
                'primary': {'used_percent': 31.0, 'window_minutes': 10080, 'resets_at': 1792649641},
                'secondary': None, 'credits': {'has_credits': True, 'unlimited': False, 'balance': '62449.1090800000'},
                'individual_limit': None, 'spend_control_reached': None, 'plan_type': 'pro',
                'rate_limit_reached_type': None}}}

    def test_owner_actual_runtime_metrics_and_commentary_metadata_are_accepted_but_not_text_smuggling(self):
        self.prepare(); approval = self.approval()
        parent, argv = self.owner_launch(approval)
        events = self.owner_progress()
        events[4]['payload']['internal_chat_message_metadata_passthrough']['content_item_kinds'] = ['unknown']
        events.insert(1, self.token_count_event())
        with module.native_operation(argv):
            guard = module.capture_native_guard([approval])
            for event in events: self.append_event(parent, event)
            module.assert_native_guard(guard, terminal=True)
        base = parent.read_bytes()
        for mutate in (lambda e: e['payload']['info'].update(text='new instruction'),
                       lambda e: e['payload']['info']['last_token_usage'].update(output_tokens=True),
                       lambda e: e['payload']['rate_limits'].update(plan_type='new instruction')):
            parent.write_bytes(base)
            with module.native_operation(argv):
                guard = module.capture_native_guard([approval])
                event = self.token_count_event(); mutate(event); self.append_event(parent, event)
                with self.assertRaises(ValueError): module.assert_native_guard(guard, terminal=True)

    def test_owner_cannot_reuse_wait_call_identity_or_adopt_unfinished_other_tool(self):
        self.prepare(); approval = self.approval()
        parent, argv = self.owner_launch(approval)
        with module.native_operation(argv):
            guard = module.capture_native_guard([approval])
            for event in self.owner_progress(): self.append_event(parent, event)
            self.append_event(parent, self.owner_progress()[5])  # same wait call ID twice
            with self.assertRaises(ValueError): module.assert_native_guard(guard, terminal=True)

    def test_owner_outer_metadata_rejects_wrong_authorship_source_identity_and_order(self):
        self.prepare(); approval = self.approval()
        parent, argv = self.owner_launch(approval)
        initial = parent.read_bytes()
        mutations = [
            lambda e: e[0]['metadata'].update(client_authored=True),
            lambda e: e[0]['metadata'].update(fallback_token_limit_override=True),
            lambda e: e[0]['metadata'].update(fallback_token_limit_override=0),
            lambda e: e[0]['metadata'].update(other='input'),
            lambda e: e[4]['metadata']['retained_source'].update(complete=False),
            lambda e: e[4]['metadata']['retained_source']['id'].update(role='user'),
            lambda e: e[4]['metadata']['retained_source']['id'].update(turn_id='other-turn'),
            lambda e: e[4]['metadata']['retained_source']['id'].update(message_id='different-message'),
            lambda e: e[4]['metadata']['retained_source'].update(revision='unrecognized-revision'),
            lambda e: e[4]['metadata'].update(user_input_order=True),
            lambda e: e[5]['metadata'].update(user_input_order=-1),
            lambda e: e[5]['metadata'].update(user_input_order=self.owner_order),
        ]
        for number, mutate in enumerate(mutations):
            with self.subTest(number=number):
                parent.write_bytes(initial)
                with module.native_operation(argv):
                    guard = module.capture_native_guard([approval])
                    events = self.owner_progress(); mutate(events)
                    for event in events: self.append_event(parent, event)
                    with self.assertRaises(ValueError): module.assert_native_guard(guard, terminal=True)
        parent.write_bytes(initial)

    def test_full_observed_owner_runtime_sequence_replays_all_running_event_shapes(self):
        # Structural fixture of the complete real running interval: initial
        # yield, six waits, six reasoning outputs, seven metric envelopes, and
        # one assistant commentary. No real native IDs or message text retained.
        self.prepare(); approval = self.approval()
        parent, argv = self.owner_launch(approval)
        base_order = self.owner_order
        progress = self.owner_progress()
        events = [progress[0], self.token_count_event()]
        for number in range(6):
            events.extend(deepcopy(progress[1:3]))
            if number == 1:
                commentary = deepcopy(progress[3:5])
                commentary[1]['metadata']['user_input_order'] = base_order + number * 2 + 1
                events.extend(commentary)
            wait = deepcopy(progress[5]); wait['payload']['call_id'] = f'wait-{number}'
            wait['metadata']['user_input_order'] = base_order + number * 2 + 2
            result = deepcopy(progress[6]); result['payload']['call_id'] = f'wait-{number}'
            events.extend([wait, self.usage_event('author'), result, self.token_count_event()])
        self.assertEqual(len(events), 40)
        with module.native_operation(argv):
            guard = module.capture_native_guard([approval])
            for event in events: self.append_event(parent, event)
            module.assert_native_guard(guard, terminal=True)
            # A completed shell result carries new decision data. It is outside
            # the running interval, and is deliberately not a progress event.
            self.append_event(parent, {'type': 'event_msg', 'payload': {'type': 'item_completed',
                'thread_id': 'author', 'turn_id': 'owner-turn', 'started_at_ms': 1, 'completed_at_ms': 2,
                'item': {'type': 'CommandExecution', 'id': 'completed-command', 'status': 'completed', 'exit_code': 0}}})
            with self.assertRaises(ValueError): module.assert_native_guard(guard, terminal=True)


if __name__ == '__main__': unittest.main()
