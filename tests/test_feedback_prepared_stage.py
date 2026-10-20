"""Focused private stage tests with normal writer and injected model/live data."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock

from scripts.common.question_identity import load_source_record_inventory
from tools.question_bank import feedback_prepared_stage as stage
from tools.question_review_console.jobs import JobManager
from tools.question_review_console.qualification_runs import QualificationRunCoordinator
from tools.question_review_console.qualification_workflow import QualificationWorkflow
from tests.qualification_run_test_support import SourceOnlyInventory, FakeSynchronizer, FlowAppServer


class NativeProfileFixture(FlowAppServer):
    def snapshot_for(self, profile):
        return {'name': profile, 'fingerprint': 'test-profile', 'roles': {}}

    def assert_profile_access(self, profile, *, force=False):
        return {'allowed': True}


class PreparedStageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = Path(__file__).resolve().parents[1]
        # Keep real policy documents and code dependencies, with no production data.
        for relative in ('config', 'prompt', 'document', 'scripts', 'tools'):
            shutil.copytree(self.repo / relative, self.root / relative,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for relative in ('AGENTS.md', 'README.md'):
            shutil.copyfile(self.repo / relative, self.root / relative)
        self.model = NativeProfileFixture()
        self.inventory = SourceOnlyInventory()
        self.coordinator = QualificationRunCoordinator(self.root,
            QualificationWorkflow(self.root, self.inventory), FakeSynchronizer(), JobManager(), 'test-secret', app_server=self.model)
        question = self.inventory.group('new-exam', '2026')['questions'][0]
        source_record = {**deepcopy(question['projected']), **deepcopy(question['source']),
            'sourceQuestionKey': question['sourceQuestionKey'], 'reviewQuestionId': question['originalQuestionId'],
            'sourceRecordRef': question['sourceRecordRef']}
        source = self.root / question['paths']['source']
        self.write(source, {'question_bodies': [source_record]})
        manifest = self.root / stage.MANIFEST
        self.write(manifest, (json.dumps({'path': str(source.relative_to(self.root)), 'sha256': stage._hash(source.read_bytes())}) + '\n').encode())
        entry = load_source_record_inventory(source.parent, qualification='new-exam', list_group_id='2026')[0]
        self.private = self.root / stage.PRIVATE / 'test'
        pdf = self.private / 'official.pdf'; self.write(pdf, b'%PDF fixture primary evidence')
        self.request_path = self.private / 'request.json'
        self.output = self.private / 'plan'
        self.request = {'schemaVersion': stage.REQUEST, 'qualification': 'new-exam', 'listGroupId': '2026',
            'questionId': question['id'], 'stageId': 'question_type',
            'source': {'ref': str(source), 'sha256': stage._hash(source.read_bytes()), 'binding': entry.identity.binding.as_mapping()},
            'authority': {'kind': 'improvement', 'taskId': 'task'},
            'live': {'originalQuestionId': 'original-live', 'qualificationId': 'live-qualification',
                     'questionIds': ['live-a', 'live-b'], 'readFields': sorted(stage.LIVE_FIELDS)},
            'primaryEvidence': [{'ref': str(pdf), 'sha256': stage._hash(pdf.read_bytes())}]}
        self.write(self.request_path, self.request)
        self.db = self.private / 'ledger.sqlite3'
        self.connection = sqlite3.connect(self.db); self.connection.row_factory = sqlite3.Row
        self.addCleanup(self.connection.close)
        self.connection.executescript('''CREATE TABLE improvement_tasks(task_id,title,need,target);
            CREATE TABLE ai_question_candidates(candidate_id,question_id,text_hash,review_status,improvement_task_id);
            INSERT INTO improvement_tasks VALUES ('task','分類','設問の性質','live-a');
            INSERT INTO ai_question_candidates VALUES ('candidate','live-a','hash','promoted','task');''')
        self.connection.commit()
        self.snapshots = {qid: {'questionId': qid, 'exists': True, 'updateTimeExact': '2026-10-20T00:00:00.123456789Z',
            'fields': {'originalQuestionId': 'original-live', 'qualificationId': 'live-qualification',
                       'isDeleted': False, 'isChoiceOnly': False, 'correctChoiceText': '正しい'}} for qid in ('live-a', 'live-b')}
        live = patch.object(stage, '_snapshots', side_effect=lambda *_: deepcopy(self.snapshots))
        live.start(); self.addCleanup(live.stop)
        # Cache AST dependency selection only; actual bytes are still pinned.
        dependency = list(stage.reader_dependencies(self.root, [self.root / 'tools/question_bank/feedback_prepared_stage.py']))
        dependency_patch = patch.object(stage, 'reader_dependencies', return_value=dependency)
        dependency_patch.start(); self.addCleanup(dependency_patch.stop)
        self.approval_path = self.private / 'native-approval.json'
        self.write(self.approval_path, {'schemaVersion': 'native-independent-approval/v1', 'fixture': True})

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else stage._bytes(value))

    def prepare(self):
        return stage.prepare(self.root, self.request_path, self.output,
                             coordinator=self.coordinator, connection=self.connection)

    def plan(self, *, saved=False):
        return stage.load_plan(self.root, self.output / 'plan.json',
                               coordinator=self.coordinator, connection=self.connection, saved=saved)

    def apply(self):
        return stage.apply(self.root, self.output / 'plan.json', self.approval_path,
                           coordinator=self.coordinator, connection=self.connection)

    def test_prepare_uses_normal_candidate_and_writer_preview_without_formal_save(self):
        source = Path(self.request['source']['ref']); original = source.read_bytes()
        receipt = self.prepare(); plan = self.plan()
        self.assertIs(receipt['formalPatchSaved'], False)
        self.assertIs(receipt['workVersionRecorded'], False)
        self.assertIs(receipt['checkpointRecorded'], False)
        self.assertIs(receipt['FirestoreWritten'], False)
        self.assertFalse(plan['sourceToLiveMappingApproved'])
        self.assertEqual(plan['liveMembershipPredicate'], {'originalQuestionId': 'original-live',
                                                         'isDeleted': False, 'isChoiceOnly': False})
        self.assertTrue(plan['commitView']['patches'])
        self.assertIn(plan['stagePlan']['progressTargets'][0]['_projectedInputPath'], plan['inputFiles'])
        for before in plan['commitView']['transactionBefore']:
            path = self.root / before['path']
            self.assertEqual(path.is_file(), before['exists'])
            self.assertEqual(path.read_bytes().hex() if path.is_file() else None, before['bytesHex'])
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(len(self.model.calls), 1)
        self.assertEqual(len(self.model.aggregate_review_calls), 2)
        self.assertTrue(all(call[1]['sandbox'] == 'read-only' for call in self.model.calls))
        child = self.coordinator.store.get('new-exam', plan['childRunId'])
        self.assertTrue(child['externalApprovalRequired'])
        self.assertEqual(child['status'], 'prepared')
        self.assertFalse(child['canonicalWriteStarted'])

    def test_unapproved_native_record_never_reaches_writer(self):
        self.prepare()
        with self.assertRaises(ValueError): self.apply()
        self.assertFalse((self.output / 'apply-intent.json').exists())
        self.assertFalse((self.output / 'save-receipt.json').exists())
        self.plan()

    def test_approved_same_candidate_normal_save_and_exact_retry(self):
        self.prepare()
        binding = {'authorSessionId': 'author', 'reviewerSessionId': 'independent'}
        with patch.object(stage, 'verify_independent_approval', return_value=binding) as verifier:
            receipt = self.apply()
            self.assertIs(receipt['formalPatchSaved'], True)
            self.assertIs(receipt['workVersionRecorded'], True)
            self.assertIs(receipt['checkpointRecorded'], True)
            self.assertIs(receipt['FirestoreWritten'], False)
            saved_files = {path: (self.root / path).read_bytes() for path in receipt['actualFiles'] if (self.root / path).is_file()}
            self.assertEqual(receipt, self.apply())
            self.assertEqual(saved_files, {path: (self.root / path).read_bytes() for path in saved_files})
            self.assertGreaterEqual(verifier.call_count, 4)
        self.assertEqual(len(self.model.calls), 1)

    def test_marker_requires_guard_even_when_internal_apply_is_called(self):
        self.prepare(); plan = self.plan()
        with self.assertRaisesRegex(Exception, 'guard'):
            stage._run(self.coordinator, plan)
        self.plan()

    def test_locked_native_rejection_closes_unwritten_baseline(self):
        self.prepare()
        binding = {'reviewerSessionId': 'independent'}
        calls = []
        def native(*args, **kwargs):
            calls.append(True)
            if len(calls) >= 3: raise ValueError('native approval revoked while waiting for lock')
            return binding
        with patch.object(stage, 'verify_independent_approval', side_effect=native):
            with self.assertRaises(ValueError): self.apply()
        self.assertFalse((self.output / 'save-receipt.json').exists())
        plan = self.plan()
        child = self.coordinator.store.get('new-exam', plan['childRunId'])
        self.assertFalse(child.get('canonicalWriteStarted'))
        self.assertFalse(child.get('candidateTransactionOpen'))
        with patch.object(stage, 'verify_independent_approval', return_value=binding):
            with self.assertRaisesRegex(ValueError, 'incomplete'): self.apply()

    def test_input_mutation_during_locked_native_verification_is_rechecked(self):
        self.prepare(); plan = self.plan()
        calls = []
        def native(*args, **kwargs):
            calls.append(True)
            if len(calls) == 3:
                self.request_path.write_bytes(self.request_path.read_bytes() + b' ')
            return {'reviewerSessionId': 'reviewer'}
        with patch.object(stage, 'verify_independent_approval', side_effect=native):
            with self.assertRaises(ValueError): self.apply()
        self.assertEqual(len(calls), 3)
        self.assertFalse((self.output / 'save-receipt.json').exists())
        for entry in plan['commitView']['transactionBefore']:
            path = self.root / entry['path']
            self.assertEqual(path.read_bytes().hex() if path.is_file() else None, entry['bytesHex'])
        child = self.coordinator.store.get('new-exam', plan['childRunId'])
        self.assertFalse(child['canonicalWriteStarted'])
        self.assertFalse(child['candidateTransactionOpen'])

    def test_symlink_and_escape_are_rejected_before_generation(self):
        linked = self.private / 'linked-request.json'; linked.symlink_to(self.request_path)
        with self.assertRaises(ValueError):
            stage.prepare(self.root, linked, self.output, coordinator=self.coordinator, connection=self.connection)
        with self.assertRaises(ValueError):
            stage.prepare(self.root, self.request_path, self.root / 'outside-private',
                          coordinator=self.coordinator, connection=self.connection)
        self.assertEqual(self.model.calls, [])

    def test_task_live_input_and_dependency_drift_rejected(self):
        self.prepare()
        path = self.root / 'tools/question_bank/feedback_prepared_stage.py'; original = path.read_bytes()
        path.write_bytes(original + b'\n# drift\n')
        with self.assertRaises(ValueError): self.plan()
        path.write_bytes(original)
        self.connection.execute("UPDATE improvement_tasks SET need='another need'")
        with self.assertRaisesRegex(ValueError, 'requirement'): self.plan()
        self.connection.execute("UPDATE improvement_tasks SET need='設問の性質'")
        self.snapshots['live-b']['updateTimeExact'] = '2026-10-20T00:00:01Z'
        with patch.object(stage, 'verify_independent_approval', return_value={'reviewerSessionId': 'independent'}):
            with self.assertRaisesRegex(ValueError, 'live'): self.apply()

    def test_unknown_request_field_and_foreign_binding_fail_before_model(self):
        self.request['bypassApproval'] = True; self.write(self.request_path, self.request)
        with self.assertRaises(ValueError): self.prepare()
        del self.request['bypassApproval']
        self.request['source']['binding']['reviewQuestionId'] = 'other'; self.write(self.request_path, self.request)
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual(self.model.calls, [])

    def test_prepared_candidate_drift_or_canonical_third_value_rejected(self):
        self.prepare(); plan = self.plan()
        first = plan['commitView']['transactionBefore'][0]
        path = self.root / first['path']; path.parent.mkdir(parents=True, exist_ok=True)
        original = path.read_bytes() if path.exists() else None
        path.write_bytes(b'{"third":true}')
        with self.assertRaisesRegex(ValueError, 'third value'): self.plan()
        if original is None: path.unlink()
        else: path.write_bytes(original)
        self.plan()

    def test_existing_reader_rejects_an_incomplete_active_live_sibling_set(self):
        from tools.question_bank import feedback_verified_save as reader
        from types import SimpleNamespace
        def snapshot(qid):
            values = deepcopy(self.snapshots['live-a']['fields'])
            return SimpleNamespace(id=qid, exists=True, to_dict=lambda: values,
                update_time=SimpleNamespace(rfc3339=lambda: '2026-10-20T00:00:00.123456789Z'))
        database = Mock()
        database.get_all.return_value = [snapshot('live-a'), snapshot('live-b')]
        database.collection.return_value.where.return_value.select.return_value.stream.return_value = [
            snapshot('live-a'), snapshot('live-b'), snapshot('omitted-active-sibling')]
        with patch.object(reader, 'FirestoreReadback') as firestore, patch.object(stage, '_snapshots', reader._snapshots):
            firestore.return_value._database.return_value = database
            with self.assertRaisesRegex(ValueError, 'live sibling membership changed'):
                stage._live(self.request)
        database.collection.return_value.where.assert_called_once()

    def test_projected_input_bytes_are_fixed_before_scope_or_apply(self):
        self.prepare(); plan = self.plan()
        path = self.root / plan['stagePlan']['progressTargets'][0]['_projectedInputPath']
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'drift'):
            stage.approval_scope(self.root, self.output / 'plan.json', coordinator=self.coordinator, connection=self.connection)
        with self.assertRaisesRegex(ValueError, 'drift'): self.apply()
        self.assertFalse((self.output / 'apply-intent.json').exists())

    def test_scope_fixes_source_stage_candidate_and_native_creation(self):
        self.prepare(); plan = self.plan()
        scope = stage.approval_scope(self.root, self.output / 'plan.json', coordinator=self.coordinator, connection=self.connection)
        self.assertEqual(scope['planHash'], plan['planHash'])
        self.assertEqual(scope['sourceBinding'], self.request['source']['binding'])
        self.assertEqual(scope['stageId'], 'question_type')
        self.assertIn('scope', scope['nativeCreationCall'])
        self.assertIn('feedback_prepared_stage', scope['nativeCreationCall'])
        self.assertFalse(scope['FirestoreWritten'])

    def test_real_native_validator_and_normal_writer_are_connected(self):
        from tests.test_native_independent_approval import native_audit
        import tools.question_review_console.independent_approval as native
        self.prepare()
        scope = stage.approval_scope(self.root, self.output / 'plan.json', coordinator=self.coordinator, connection=self.connection)
        record = native_audit(self.private, scope)
        self.write(self.approval_path, record)
        # Test native provenance is restricted to this fixture session directory.
        with patch.object(native, 'NATIVE_SESSION_ROOT', self.private):
            receipt = self.apply()
            self.assertTrue(receipt['formalPatchSaved'])
            self.assertEqual(receipt['nativeBinding']['reviewerSessionId'], 'reviewer')
            self.assertEqual(receipt, self.apply())

    def test_missing_preview_receipt_is_not_an_approvable_plan(self):
        self.prepare()
        (self.output / 'preview-receipt.json').unlink()
        with self.assertRaises(FileNotFoundError): self.plan()
        with self.assertRaises(FileNotFoundError):
            stage.approval_scope(self.root, self.output / 'plan.json', coordinator=self.coordinator, connection=self.connection)

    def test_receipt_write_interruption_holds_without_second_application(self):
        self.prepare()
        original = stage._exclusive
        def interrupted(path, data):
            if path.name == 'save-receipt.json': raise OSError('receipt interruption')
            return original(path, data)
        with patch.object(stage, 'verify_independent_approval', return_value={'reviewerSessionId': 'reviewer'}):
            with patch.object(stage, '_exclusive', side_effect=interrupted):
                with self.assertRaisesRegex(OSError, 'receipt interruption'): self.apply()
            self.assertFalse((self.output / 'save-receipt.json').exists())
            with self.assertRaisesRegex(ValueError, 'third value|incomplete'): self.apply()
        self.assertEqual(len(self.model.calls), 1)

    def test_normal_work_version_failure_rolls_back_exact_patch(self):
        self.prepare(); plan = self.plan()
        original = {entry['path']: entry['bytesHex'] for entry in plan['commitView']['transactionBefore']}
        with patch.object(stage, 'verify_independent_approval', return_value={'reviewerSessionId': 'reviewer'}), \
                patch.object(self.coordinator, '_record_work_versions', side_effect=RuntimeError('version-write failed')):
            with self.assertRaises(ValueError): self.apply()
        self.assertFalse((self.output / 'save-receipt.json').exists())
        for path, before_hex in original.items():
            target = self.root / path
            self.assertEqual(target.read_bytes().hex() if target.is_file() else None, before_hex)
        child = self.coordinator.store.get('new-exam', plan['childRunId'])
        self.assertEqual(child['rollback']['status'], 'succeeded')
        self.assertFalse(child['rollback']['remainingChangedFiles'])

    def test_report_case_authority_is_individual_and_readonly(self):
        self.connection.executescript("""CREATE TABLE report_tasks(task_id,report_id,question_id,categories_json,
            case_ids_json,case_statuses_json,intake_status,decision,decision_reason,evidence_ref,proposal_hash);
            CREATE TABLE report_case_decisions(task_id,case_id,decision,reason,evidence_ref,decided_at);
            INSERT INTO report_tasks VALUES ('report-task','report-id','live-a','["explanation"]',
                '["case-a","case-b"]','{}','complete','mixed','case-specific','private-evidence',NULL);
            INSERT INTO report_case_decisions VALUES ('report-task','case-a','fix_required','reason','private-evidence','2026-10-20');
            INSERT INTO report_case_decisions VALUES ('report-task','case-b','no_change','reason','private-evidence','2026-10-20');""")
        self.request['authority'] = {'kind': 'report', 'taskId': 'report-task', 'caseId': 'case-a'}
        self.write(self.request_path, self.request)
        before = list(self.connection.iterdump())
        self.prepare()
        plan = self.plan()
        self.assertEqual(plan['taskRequirement']['requirement']['case']['case_id'], 'case-a')
        self.assertEqual(before, list(self.connection.iterdump()))
        self.connection.execute("UPDATE report_case_decisions SET decision='no_change' WHERE case_id='case-a'")
        with self.assertRaises(ValueError): self.plan()

    def test_saved_receipt_flags_and_actual_file_drift_rejected(self):
        self.prepare()
        with patch.object(stage, 'verify_independent_approval', return_value={'reviewerSessionId': 'reviewer'}):
            receipt = self.apply()
            receipt_path = self.output / 'save-receipt.json'
            changed = deepcopy(receipt); changed['FirestoreWritten'] = True
            self.write(receipt_path, changed)
            with self.assertRaisesRegex(ValueError, 'evidence drift'): self.apply()
            self.write(receipt_path, receipt)
            patch_path = self.root / next(iter(receipt['actualFiles']))
            patch_path.write_bytes(patch_path.read_bytes() + b' ')
            with self.assertRaises(ValueError): self.apply()



if __name__ == '__main__': unittest.main()
