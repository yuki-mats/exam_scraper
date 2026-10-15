"""Maintenance scope and local transaction tests; no production services used."""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_feedback_verified_save as fixtures
from scripts.pipeline import rebuild_gas_shunin_all_year_artifacts as gas
from tools.question_bank import feedback_maintenance_tasks as registry
from tools.question_bank import feedback_verified_maintenance as maintenance
from tools.question_bank import feedback_verified_save as saver
from tools.question_review_console.scoped_artifacts import write_json


class VerifiedMaintenanceTest(unittest.TestCase):
    # Reuse only fixture construction and small helpers, not its test methods.
    selected_snapshots = fixtures.VerifiedSaveTest.selected_snapshots
    save = fixtures.VerifiedSaveTest.save
    rewrite_plan = fixtures.VerifiedSaveTest.rewrite_plan

    def setUp(self):
        fixtures.VerifiedSaveTest.setUp(self)
        self.sibling['lawRevisionFacts'] = {
            'differenceFacts': ['旧説明。'], 'auditStatus': 'same_as_current',
            'reviewState': 'secondary_verified',
            'current': {'lawId': 'law', 'article': '144', 'correctChoiceText': '正しい'},
        }
        self.untouched = {**self.doc, 'questionId': 'untouched',
                          'originalQuestionChoiceText': '変更しない肢'}
        rows = [self.doc, self.sibling, self.untouched]
        index = json.loads(self.index.read_bytes())
        index['records'].append({**deepcopy(index['records'][0]), 'questionId': 'untouched'})
        write_json(self.index, index)
        evidence = gas.verify_official_pdf_evidence(
            document_index=self.index, pdf_verification=self.verify, root=self.root)
        self.bundle = json.loads(self.before)
        self.bundle.update(questions=deepcopy(rows), total_count=3, evidence=evidence)
        write_json(self.formal, self.bundle)
        self.before = self.formal.read_bytes()
        self.snapshots = {
            row['questionId']: {'questionId': row['questionId'], 'exists': True,
                               'updateTimeExact': '2026-10-15T00:00:00.123456789Z',
                               'fields': deepcopy(row)} for row in rows}
        self.updates = []
        candidate = deepcopy(self.bundle)
        for row, field, after in [
            (self.doc, 'explanationText', '正しい。用語と供給条件を説明します。'),
            (self.sibling, 'lawRevisionFacts', {
                **deepcopy(self.sibling['lawRevisionFacts']),
                'differenceFacts': ['試験時点と現行の測定場所は同じです。']}),
        ]:
            identity = row['questionId']
            baseline = self.ref(Path(f'maintenance/{identity}-before.json'), self.snapshots[identity])
            baseline['updateTimeExact'] = self.snapshots[identity]['updateTimeExact']
            delta = {'field': field, 'beforePresent': True,
                     'before': deepcopy(row[field]), 'after': after}
            private = self.ref(Path(f'maintenance/{identity}-patch.json'), {
                'schemaVersion': maintenance.PATCH_SCHEMA,
                'taskId': 'task', 'maintenanceTaskId': 'maintenance', 'questionId': identity,
                'fieldDiffs': [delta], 'precondition': {
                    'updateTimeExact': baseline['updateTimeExact'],
                    'baselineSha256': baseline['sha256']},
                'approvalReady': False, 'formalPatchSaved': False, 'FirestoreWritten': False,
                'executablePatch': False, 'fullDocumentUploadAllowed': False})
            self.updates.append({'questionId': identity, 'baseline': baseline,
                                 'preparedPatch': private, 'fieldDiffs': [delta]})
            next(q for q in candidate['questions'] if q['questionId'] == identity)[field] = deepcopy(after)
        self.authority = self.register_scope(self.updates, candidate)
        self.mock_registry = patch.object(registry, 'load_binding', side_effect=self.load_binding)
        self.mock_registry.start()
        self.addCleanup(self.mock_registry.stop)
        result = json.loads(self.result.read_bytes())
        result['proposedUpdates'] = deepcopy(self.updates)
        write_json(self.result, result)
        self.destination = self.root / saver.PRIVATE / 'maintenance-run'
        self.plan = self.prepare(self.destination)
        self.path = self.destination / 'formal-save-plan.json'

    def load_binding(self, connection, *, root, task_id):
        self.assertIs(connection, self.connection)
        self.assertEqual(root, self.root)
        if task_id != 'maintenance':
            raise ValueError('unknown fixture maintenance task')
        return deepcopy(self.authority)

    def prepare(self, destination, task_id='maintenance'):
        return saver.prepare(self.root, self.result, self.formal, destination,
                             document_index=self.index, pdf_verification=self.verify,
                             connection=self.connection, maintenance_task_id=task_id)

    def register_scope(self, updates, candidate):
        candidate_ref = self.ref(saver.PRIVATE / 'authority/candidate.json', candidate)
        proposal = {'schemaVersion': 'feedback-gas-maintenance-content-scope/v1',
                    'taskId': 'task', 'originalQuestionId': 'original',
                    'proposedUpdates': deepcopy(updates), 'candidate': candidate_ref}
        proposal['scopeHash'] = saver.sha256_json(proposal)
        proposal_ref = self.ref(saver.PRIVATE / 'authority/proposal.json', proposal)
        audit_ref = self.ref(saver.PRIVATE / 'authority/audit.json',
                             {'fixture': 'registry native review tested separately'})
        return {'schemaVersion': registry.SCHEMA, 'taskId': 'maintenance',
                'relatedImprovementTaskId': 'task', 'status': 'review_pending',
                'originalQuestionId': 'original',
                'allSiblingIds': ['display', 'sibling', 'untouched'],
                'scopeHash': proposal['scopeHash'], 'proposalRef': proposal_ref,
                'auditRef': audit_ref, 'candidateSha256': candidate_ref['sha256'],
                'publicationDocuments': [
                    {'questionId': identity,
                     'writeFields': sorted(d['field'] for u in updates
                                           if u['questionId'] == identity for d in u['fieldDiffs'])}
                    for identity in ['display', 'sibling', 'untouched']]}

    def read(self, verify_law=False):
        return saver.read_plan(self.root, self.path, verify_law=verify_law,
                               connection=self.connection)

    @contextmanager
    def transaction_law_gate(self):
        # Only local file transaction tests bypass the separate native law reviews.
        with patch.object(saver, '_law_review_bindings', return_value=[]):
            yield

    def file_save(self, hook=None):
        with self.transaction_law_gate():
            return self.save(hook)

    def restore(self):
        with self.transaction_law_gate():
            return saver.restore_original(self.root, self.path, self.approval,
                                          connection=self.connection)

    def test_dedicated_scope_and_projection_preserve_protected_fields(self):
        plan = self.read()
        self.assertEqual(plan['schemaVersion'], saver.MAINTENANCE_SCHEMA)
        self.assertEqual(plan['maintenanceBinding'], self.authority)
        self.assertTrue(plan['lawReviewRequired'])
        scope = saver.approval_scope(self.root, self.path, plan, 'primary')
        self.assertEqual(scope['schemaVersion'], 'feedback-verified-maintenance-independent-scope/v1')
        self.assertEqual(scope['maintenanceBinding'], self.authority)
        self.assertEqual(scope['priorLawReviews'], [])
        self.assertEqual(scope['publicationDocuments'], self.authority['publicationDocuments'])
        self.assertEqual(plan['requiredPublicationGates'], [
            'merge_convert', 'required_checkers', 'quality_gate', 'independent_evaluation',
            'scoped_upload_dry_run', 'fresh_live_preconditions', 'live_readback'])
        candidate = json.loads(Path(plan['candidateRef']).read_bytes())
        for old, new in zip(self.bundle['questions'], candidate['questions'], strict=True):
            allowed = {'display': {'explanationText'}, 'sibling': {'lawRevisionFacts'}}.get(old['questionId'], set())
            self.assertEqual({k: v for k, v in old.items() if k not in allowed},
                             {k: v for k, v in new.items() if k not in allowed})
        self.assertEqual(self.formal.read_bytes(), self.before)

    def test_complete_exact_field_mask_and_live_preconditions(self):
        expected = sorted((set().union(*(set(q) for q in self.bundle['questions'])) |
                           saver.EXPLANATION_FIELDS) - gas.METADATA_FIELDS - {'questionId'})
        self.assertEqual(self.plan['snapshotFields'], expected)
        self.assertIn('lawRevisionFacts', expected)
        self.assertEqual(self.plan['siblingIds'], ['display', 'sibling', 'untouched'])
        self.snapshots['untouched']['updateTimeExact'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'live precondition'):
            self.file_save()
        self.assertEqual(self.formal.read_bytes(), self.before)

    def test_snapshot_mask_cannot_drop_legal_facts_even_when_rehashed(self):
        self.rewrite_plan(lambda p: p['snapshotFields'].remove('lawRevisionFacts'))
        with self.assertRaisesRegex(ValueError, 'snapshot mask'):
            self.read()

    def test_native_law_reviews_required_without_transaction_mock(self):
        self.mock_approval.stop()
        with self.assertRaisesRegex(ValueError, 'three-stage'):
            self.read(verify_law=True)
        with self.assertRaisesRegex(ValueError, 'three-stage'):
            self.save()
        self.assertEqual(self.formal.read_bytes(), self.before)

    def test_real_native_verifier_rejects_assertions_before_save(self):
        self.mock_approval.stop()
        for stage in saver.LAW_STAGES:
            write_json(self.destination / f'law-{stage}-approval.json', {'stage': stage})
        with self.assertRaisesRegex(ValueError, 'approval evidence'):
            self.save()
        self.assertEqual(self.formal.read_bytes(), self.before)

    def test_success_new_receipt_resend_and_production_native_calls(self):
        receipt = self.file_save()
        self.assertEqual(receipt['schemaVersion'], saver.MAINTENANCE_RECEIPT)
        self.assertNotEqual(receipt['schemaVersion'], saver.RECEIPT)
        self.assertIs(receipt['FirestoreWritten'], False)
        self.assertEqual(saver.file_hash(self.formal), self.plan['candidateSha256'])
        stamp = self.formal.stat().st_mtime_ns
        self.snapshots['display']['updateTimeExact'] = 'later publication'
        self.assertEqual(self.file_save(), receipt)
        self.assertEqual(self.formal.stat().st_mtime_ns, stamp)
        for call in self.mock_approval.target.verify_independent_approval.call_args_list:
            self.assertIs(call.kwargs['production'], True)
            self.assertEqual(call.args[1]['schemaVersion'], 'feedback-verified-maintenance-independent-scope/v1')
            self.assertEqual(call.args[1]['maintenanceBinding'], self.authority)
            self.assertEqual(call.args[1]['action'], 'save_verified_sibling_maintenance_and_review_limited_publication')

    def test_save_recovery_after_each_exclusive_move(self):
        for stage in ['original_moved', 'candidate_saved']:
            with self.subTest(stage=stage):
                def fail(actual):
                    if actual == stage:
                        raise RuntimeError('fixture interruption')
                with self.assertRaisesRegex(RuntimeError, 'fixture interruption'):
                    self.file_save(fail)
                self.assertFalse((self.destination / 'save-receipt.json').exists())
                stamp = self.formal.stat().st_mtime_ns if self.formal.exists() else None
                receipt = self.file_save()
                self.assertEqual(receipt['schemaVersion'], saver.MAINTENANCE_RECEIPT)
                if stamp is not None:
                    self.assertEqual(self.formal.stat().st_mtime_ns, stamp)
                self.restore()
                if stage == 'original_moved':
                    self.destination = self.root / saver.PRIVATE / 'recovery-second'
                    self.plan = self.prepare(self.destination)
                    self.path = self.destination / 'formal-save-plan.json'

    def test_save_third_value_is_held_with_original_backup(self):
        def conflict(stage):
            if stage == 'original_moved':
                self.formal.write_bytes(b'competing third value')
        with self.assertRaisesRegex(ValueError, 'third value'):
            self.file_save(conflict)
        self.assertEqual(self.formal.read_bytes(), b'competing third value')
        original = self.formal.parent / ('.feedback-save-' + self.plan['planHash']) / 'original.json'
        self.assertEqual(original.read_bytes(), self.before)

    def test_restore_resend_and_third_value_protection(self):
        self.file_save()
        self.formal.write_bytes(b'competing restore value')
        with self.assertRaisesRegex(ValueError, 'third value'):
            self.restore()
        self.assertEqual(self.formal.read_bytes(), b'competing restore value')
        self.formal.write_bytes(Path(self.plan['candidateRef']).read_bytes())
        self.assertIs(self.restore()['FirestoreWritten'], False)
        self.assertEqual(self.formal.read_bytes(), self.before)
        stamp = self.formal.stat().st_mtime_ns
        self.restore()
        self.assertEqual(self.formal.stat().st_mtime_ns, stamp)
        with self.assertRaises(ValueError):
            self.file_save()

    def test_restore_interruption_before_receipt_resumes_without_rewriting(self):
        self.file_save()
        exclusive = saver._exclusive
        def interrupt(path, data):
            if path.name == 'restored.json':
                raise OSError('restore receipt interrupted')
            return exclusive(path, data)
        with patch.object(saver, '_exclusive', side_effect=interrupt):
            with self.assertRaisesRegex(OSError, 'restore receipt interrupted'):
                self.restore()
        self.assertEqual(self.formal.read_bytes(), self.before)
        stamp = self.formal.stat().st_mtime_ns
        self.restore()
        self.assertEqual(self.formal.stat().st_mtime_ns, stamp)

    def test_authority_revocation_or_task_change_invalidates_plan(self):
        for field, value in [('status', 'blocked'), ('taskId', 'different'),
                             ('relatedImprovementTaskId', 'different')]:
            with self.subTest(field=field):
                original = deepcopy(self.authority)
                self.authority[field] = value
                with self.assertRaisesRegex(ValueError, 'authority changed'):
                    self.read()
                self.authority = original
        self.assertEqual(self.formal.read_bytes(), self.before)

    def test_revoked_authority_is_rechecked_on_saved_receipt_resend(self):
        self.file_save()
        saved = self.formal.read_bytes()
        with patch.object(registry, 'load_binding', side_effect=ValueError('native authority revoked')):
            with self.assertRaisesRegex(ValueError, 'authority revoked'):
                self.file_save()
            with self.assertRaisesRegex(ValueError, 'authority revoked'):
                self.restore()
        self.assertEqual(self.formal.read_bytes(), saved)

    def test_old_receipt_schema_cannot_validate_maintenance_save(self):
        self.file_save()
        receipt_path = self.destination / 'save-receipt.json'
        receipt = json.loads(receipt_path.read_bytes())
        receipt['schemaVersion'] = saver.RECEIPT
        write_json(receipt_path, receipt)
        with self.assertRaisesRegex(ValueError, 'receipt differs'):
            self.file_save()

    def test_prepare_rejects_another_improvement_or_missing_registry_authority(self):
        self.authority['relatedImprovementTaskId'] = 'other-task'
        with self.assertRaisesRegex(ValueError, 'another improvement'):
            self.prepare(self.root / saver.PRIVATE / 'wrong-task')
        with self.assertRaisesRegex(ValueError, 'unknown fixture'):
            self.prepare(self.root / saver.PRIVATE / 'unknown-task', 'unregistered')

    def test_legacy_schema_rejects_maintenance_binding_and_multiple_updates(self):
        self.rewrite_plan(lambda p: p.update(schemaVersion=saver.SCHEMA))
        with self.assertRaisesRegex(ValueError, 'dedicated schema'):
            self.read()
        self.rewrite_plan(lambda p: p.pop('maintenanceBinding'))
        with self.assertRaises(ValueError):
            self.read()
        with self.assertRaisesRegex(ValueError, 'one unapproved'):
            self.prepare(self.root / saver.PRIVATE / 'legacy-multi', None)

    def test_candidate_hash_and_required_registered_evidence_are_enforced(self):
        Path(self.plan['candidateRef']).write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'material changed'):
            self.read()
        write_json(Path(self.plan['candidateRef']), maintenance.project_maintenance_bundle(
            self.bundle, self.updates, self.authority))
        self.rewrite_plan(lambda p: p['inputs'].pop(self.authority['proposalRef']['ref']))
        with self.assertRaisesRegex(ValueError, 'required evidence'):
            self.read()

    def test_registered_evidence_bytes_and_publication_fields_cannot_drift(self):
        proposal = Path(self.authority['proposalRef']['ref'])
        original = proposal.read_bytes()
        proposal.write_bytes(original + b' ')
        with self.assertRaisesRegex(ValueError, 'input changed'):
            self.read()
        proposal.write_bytes(original)
        self.rewrite_plan(lambda p: p['publicationDocuments'][1]['writeFields'].clear())
        with self.assertRaisesRegex(ValueError, 'publication fields'):
            self.read()

    def test_registered_candidate_cannot_smuggle_other_fields(self):
        mutations = [
            lambda c: c['questions'][0].update(correctChoiceText='間違い'),
            lambda c: c['questions'][0].update(originalQuestionId='other'),
            lambda c: c['questions'][2].update(explanationText='正しい。無関係な変更。'),
            lambda c: c.update(sourcePolicy='unexpected metadata'),
            lambda c: c.update(extra='unexpected field'),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutations.index(mutate)):
                candidate = json.loads(Path(self.plan['candidateRef']).read_bytes())
                mutate(candidate)
                binding = self.register_scope(self.updates, candidate)
                with self.assertRaisesRegex(ValueError, 'candidate differs'):
                    maintenance.project_maintenance_bundle(self.bundle, self.updates, binding)

    def test_registered_delta_cannot_change_identity_verdict_or_audit_metadata(self):
        for field, value in [('correctChoiceText', '間違い'), ('originalQuestionId', 'other'),
                             ('questionId', 'renamed')]:
            with self.subTest(field=field):
                updates = deepcopy(self.updates)
                updates[0]['fieldDiffs'].append({'field': field, 'beforePresent': True,
                                                'before': self.doc[field], 'after': value})
                binding = self.register_scope(updates, self.bundle)
                with self.assertRaisesRegex(ValueError, 'unsupported maintenance fields'):
                    maintenance.project_maintenance_bundle(self.bundle, updates, binding)
        updates = deepcopy(self.updates)
        updates[1]['fieldDiffs'][0]['after']['reviewState'] = 'tertiary_verified'
        binding = self.register_scope(updates, self.bundle)
        with self.assertRaisesRegex(ValueError, 'metadata outside'):
            maintenance.project_maintenance_bundle(self.bundle, updates, binding)

    def test_unregistered_delta_or_incomplete_sibling_scope_is_rejected(self):
        updates = deepcopy(self.updates)
        updates[0]['fieldDiffs'][0]['after'] = '正しい。未登録の説明。'
        with self.assertRaisesRegex(ValueError, 'registered authority'):
            maintenance.project_maintenance_bundle(self.bundle, updates, self.authority)
        binding = deepcopy(self.authority)
        binding['allSiblingIds'].remove('untouched')
        with self.assertRaisesRegex(ValueError, 'complete original sibling'):
            maintenance.project_maintenance_bundle(self.bundle, self.updates, binding)

    def test_maintenance_private_patch_cannot_use_legacy_or_other_task(self):
        patch_value = json.loads(Path(self.updates[0]['preparedPatch']['ref']).read_bytes())
        for field, value in [('schemaVersion', 'feedback-improvement-private-patch/v1'),
                             ('maintenanceTaskId', 'other'), ('taskId', 'other'),
                             ('FirestoreWritten', True)]:
            with self.subTest(field=field):
                changed = {**deepcopy(patch_value), field: value}
                with self.assertRaisesRegex(ValueError, 'dedicated maintenance private patch'):
                    maintenance.validate_private_patch(changed, self.updates[0], 'task', self.authority)


if __name__ == '__main__':
    unittest.main()
