"""Temporary verified25/native fixtures; no real ledger, service or patch writes."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tools.question_bank import feedback_verified_stage_execution as module
from tests import test_feedback_verified_review_context as context_fixtures
from tests.test_native_independent_approval import native_audit, change


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
        with self.assertRaisesRegex(ValueError, 'replaced or revoked'):
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


if __name__ == '__main__': unittest.main()
