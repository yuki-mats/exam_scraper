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
        normal_patch = next(p for p in plan['commitView']['patches'] if '10_questionType_fixed' in p['path'])
        row = json.loads(normal_patch['afterUtf8'])[0]
        self.assertEqual(set(row), {'questionBodyText', 'choiceTextList', 'questionType', 'isCalculationQuestion',
            'original_question_id', 'question_url', 'sourceQuestionKey', 'reviewQuestionId', 'sourceRecordRef'})
        self.assertEqual(row['questionBodyText'], json.loads(original)['question_bodies'][0]['questionBodyText'])
        self.assertEqual(row['choiceTextList'], json.loads(original)['question_bodies'][0]['choiceTextList'])
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
        expected = self.plan()['commitView']['patches']
        binding = {'authorSessionId': 'author', 'reviewerSessionId': 'independent'}
        with patch.object(stage, 'verify_independent_approval', return_value=binding) as verifier:
            receipt = self.apply()
            self.assertIs(receipt['formalPatchSaved'], True)
            self.assertIs(receipt['workVersionRecorded'], True)
            self.assertIs(receipt['checkpointRecorded'], True)
            self.assertIs(receipt['FirestoreWritten'], False)
            for planned_patch in expected:
                self.assertEqual((self.root / planned_patch['path']).read_bytes(), planned_patch['afterUtf8'].encode('utf-8'))
            saved_files = {path: (self.root / path).read_bytes() for path in receipt['actualFiles'] if (self.root / path).is_file()}
            self.assertEqual(receipt, self.apply())
            self.assertEqual(saved_files, {path: (self.root / path).read_bytes() for path in saved_files})
            self.assertGreaterEqual(verifier.call_count, 4)
        self.assertEqual(len(self.model.calls), 1)

    def legacy_question_type(self):
        source = Path(self.request['source']['ref'])
        path = source.parent.parent / '10_questionType_fixed' / (source.stem + '_questionType_fixed.json')
        binding = self.request['source']['binding']
        legacy = {**binding, 'original_question_id': binding['reviewQuestionId'], 'questionType': 'group_choice',
            'isCalculationQuestion': True, 'questionBodyText': '残す旧本文', 'choiceTextList': ['残す旧肢'],
            'correctChoiceText': ['間違い'], 'explanationText': ['残す旧説明'], 'firestoreQuestionIds': ['existing-live-id'],
            'legacyPrivateMetadata': {'presence': None}}
        other = {'original_question_id': 'other', 'sourceQuestionKey': 'new-exam:2026:other',
            'reviewQuestionId': 'other', 'sourceRecordRef': source.name + '#1', 'questionType': 'flash_card',
            'explanationText': ['別問題の説明'], 'legacyPrivateMetadata': [None, False]}
        self.write(path, [legacy, other])
        return path, legacy, other

    def test_existing_legacy_row_and_other_record_are_preserved_without_migration(self):
        path, legacy, other = self.legacy_question_type()
        source = Path(self.request['source']['ref']); source_before = source.read_bytes()
        unrelated = path.with_name('another_questionType_fixed.json'); self.write(unrelated, [other])
        untouched = unrelated.read_bytes()
        self.prepare()
        preview = next(p for p in self.plan()['commitView']['patches'] if p['path'] == str(path.relative_to(self.root)))
        rows = json.loads(preview['afterUtf8'])
        self.assertEqual(rows[1], other)
        for field, value in legacy.items():
            if field not in {'questionType', 'isCalculationQuestion'}: self.assertEqual(rows[0][field], value)
        with patch.object(stage, 'verify_independent_approval', return_value={'authorSessionId': 'author', 'reviewerSessionId': 'independent'}):
            self.apply()
        self.assertEqual(path.read_bytes(), preview['afterUtf8'].encode('utf-8'))
        self.assertEqual(unrelated.read_bytes(), untouched)
        self.assertEqual(source.read_bytes(), source_before)

    def test_new_question_type_scope_refuses_non_owned_field_injection_before_save(self):
        from tools.question_review_console.question_patch_proposal import IsolatedQuestionPatchWorkspace
        original = IsolatedQuestionPatchWorkspace.apply_record_update
        def inject(workspace, relative, **kwargs):
            result = original(workspace, relative, **kwargs)
            if '10_questionType_fixed' in Path(relative).parts:
                path = workspace.root / relative; rows = json.loads(path.read_bytes())
                rows[0]['correctChoiceText'] = ['正しい']
                self.write(path, rows)
            return result
        with patch.object(IsolatedQuestionPatchWorkspace, 'apply_record_update', inject):
            with self.assertRaises(ValueError): self.prepare()
        source = Path(self.request['source']['ref'])
        self.assertFalse((source.parent.parent / '10_questionType_fixed' / (source.stem + '_questionType_fixed.json')).exists())

    def test_existing_question_type_scope_refuses_non_owned_change_or_deletion(self):
        from tools.question_review_console.question_patch_proposal import IsolatedQuestionPatchWorkspace
        path, legacy, other = self.legacy_question_type(); before = path.read_bytes()
        original = IsolatedQuestionPatchWorkspace.apply_record_update
        def inject(workspace, relative, **kwargs):
            result = original(workspace, relative, **kwargs)
            if '10_questionType_fixed' in Path(relative).parts:
                file = workspace.root / relative; rows = json.loads(file.read_bytes())
                rows[0]['correctChoiceText'] = ['正しい']; rows[0].pop('legacyPrivateMetadata')
                self.write(file, rows)
            return result
        with patch.object(IsolatedQuestionPatchWorkspace, 'apply_record_update', inject):
            with self.assertRaises(ValueError): self.prepare()
        self.assertEqual(path.read_bytes(), before)

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

    def failed_preparation(self):
        with patch.object(self.coordinator, '_validate_record_scope', side_effect=ValueError('fixture scope validation failed')):
            with self.assertRaisesRegex(ValueError, 'fixed failure receipt'):
                self.prepare()
        path = self.output / 'failure-receipt.json'
        receipt = stage._json(path.read_bytes())
        self.assertEqual(receipt['schemaVersion'], stage.FAILED)
        self.assertFalse(receipt['formalPatchSaved'])
        self.assertFalse(receipt['FirestoreWritten'])
        return path, receipt

    def retry_request(self, path):
        self.request = {**self.request, 'retryFromFailure': {'ref': str(path), 'sha256': stage._hash(path.read_bytes())}}
        self.request_path = self.private / ('retry-request-' + str(len(self.model.calls)) + '.json')
        self.write(self.request_path, self.request)
        self.output = self.private / ('retry-plan-' + str(len(self.model.calls)))

    def test_real_preview_failure_is_recorded_and_normal_retry_receives_machine_feedback(self):
        path, receipt = self.failed_preparation()
        self.assertTrue(receipt['retryable'])
        self.assertEqual(receipt['validationAttempts'][0]['status'], 'failed')
        fixed = receipt['failedPlan']
        self.assertFalse((path.parent / 'plan.json').exists())
        parent = self.coordinator.store.get('new-exam', fixed['parentRunId'])
        self.assertEqual(parent['status'], 'failed')
        queue = self.coordinator._queue_stage(parent, self.request['questionId'], self.request['stageId'])
        self.assertEqual(queue['validationAttempts'], receipt['validationAttempts'])
        self.retry_request(path)
        self.prepare(); plan = self.plan()
        self.assertEqual(plan['retryContext']['failureHash'], receipt['failureHash'])
        child = self.coordinator.store.get('new-exam', plan['childRunId'])
        self.assertTrue(child['retryModelFallback'])
        self.assertEqual(child['modelAttempt']['attemptMode'], 'retry')
        self.assertIn('fixture scope validation failed', self.model.calls[-1][0])
        self.assertIn('previousValidationFeedback', self.model.calls[-1][0])
        self.assertEqual(len(self.model.calls), 2)
        # Saving continues through the unchanged native approval boundary.
        with self.assertRaises(ValueError): self.apply()
        self.assertFalse((self.output / 'save-receipt.json').exists())
        with patch.object(stage, 'verify_independent_approval', return_value={'reviewerSessionId': 'independent'}):
            saved = self.apply()
            self.assertTrue(saved['formalPatchSaved'])
            self.assertEqual(saved, self.apply())
        self.assertEqual(len(self.model.calls), 2)

    def test_failure_reference_hash_foreign_identity_and_actual_evidence_tampering_are_rejected(self):
        path, receipt = self.failed_preparation()
        self.retry_request(path)
        original = deepcopy(self.request)
        for mutation in ('hash', 'stage', 'authority', 'candidate', 'failure-status', 'marker'):
            self.request = deepcopy(original)
            if mutation == 'hash': self.request['retryFromFailure']['sha256'] = '0' * 64
            elif mutation == 'stage': self.request['stageId'] = 'question_intent'
            elif mutation == 'authority':
                self.connection.execute("UPDATE improvement_tasks SET need='changed'")
            elif mutation == 'failure-status':
                changed = deepcopy(receipt); changed['validationResult']['status'] = 'succeeded'
                changed['failureHash'] = stage.sha256_json({k: v for k, v in changed.items() if k != 'failureHash'})
                self.write(path, changed)
                self.request['retryFromFailure']['sha256'] = stage._hash(path.read_bytes())
            else:
                self.coordinator.store.update('new-exam', receipt['failedPlan']['parentRunId'], preparedStageFailure=None)
            self.write(self.request_path, self.request)
            with self.subTest(mutation=mutation), self.assertRaises(Exception):
                if mutation == 'candidate':
                    with patch.object(self.coordinator.store, 'load_prepared_candidate', return_value={'forged': True}):
                        self.prepare()
                else:
                    self.prepare()
            self.assertEqual(len(self.model.calls), 1)
            self.connection.execute("UPDATE improvement_tasks SET need='設問の性質'")
            self.write(path, receipt)
            marker = stage._failure_marker(path, receipt)
            self.coordinator.store.update('new-exam', receipt['failedPlan']['parentRunId'], preparedStageFailure=marker)

    def test_retry_claim_prevents_duplicate_and_interrupted_replays(self):
        path, _ = self.failed_preparation()
        self.retry_request(path)
        with patch.object(self.coordinator, '_prepare_question_candidate_attempt', side_effect=RuntimeError('interrupted before model')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'): self.prepare()
        self.assertTrue((path.parent / 'retry-claim.json').is_file())
        with self.assertRaisesRegex(ValueError, 'consumed or interrupted'): self.prepare()
        self.assertEqual(len(self.model.calls), 1)

    def test_failure_receipt_interruption_cannot_be_used_without_completed_runtime_markers(self):
        original = stage._exclusive
        def write_then_interrupt(path, value):
            original(path, value)
            if path.name == 'failure-receipt.json': raise OSError('failure receipt interrupted')
        with patch.object(stage, '_exclusive', side_effect=write_then_interrupt), \
                patch.object(self.coordinator, '_validate_record_scope', side_effect=ValueError('validation failed')):
            with self.assertRaisesRegex(OSError, 'failure receipt interrupted'): self.prepare()
        path = self.output / 'failure-receipt.json'
        self.retry_request(path)
        with self.assertRaisesRegex(ValueError, 'parent/child/queue'): self.prepare()
        self.assertEqual(len(self.model.calls), 1)

    def test_failed_input_policy_live_and_success_scope_cannot_be_substituted(self):
        path, receipt = self.failed_preparation()
        self.retry_request(path)
        for mutation in ('policy', 'live', 'old-request', 'canonical'):
            target = None; original = None
            if mutation == 'policy': target = self.root / 'config/question_maintenance_workflow.toml'
            elif mutation == 'old-request': target = self.root / receipt['failedPlan']['requestPath']
            elif mutation == 'canonical':
                entry = receipt['failedPlan']['canonicalInputsBefore'][0]; target = self.root / entry['path']
            else: self.snapshots['live-a']['updateTimeExact'] = '2026-10-20T01:00:00Z'
            if target:
                original = target.read_bytes() if target.is_file() else None
                self.write(target, original + b'\n' if original else b'{}')
            try:
                with self.subTest(mutation=mutation), self.assertRaises(ValueError): self.prepare()
                self.assertEqual(len(self.model.calls), 1)
            finally:
                if target:
                    if original is None: target.unlink()
                    else: target.write_bytes(original)
                self.snapshots['live-a']['updateTimeExact'] = '2026-10-20T00:00:00.123456789Z'
        self.request['retryFromFailure'] = {'ref': str(path.parent / 'preview-receipt.json'), 'sha256': '0' * 64}
        self.write(self.request_path, self.request)
        with self.assertRaises((ValueError, FileNotFoundError)): self.prepare()
        self.assertEqual(len(self.model.calls), 1)

    def test_three_actual_quality_failures_are_bounded_by_normal_retry_policy(self):
        for number in range(1, 4):
            path, receipt = self.failed_preparation()
            self.assertEqual(len(receipt['validationAttempts']), number)
            self.assertEqual(receipt['retryable'], number < 3)
            self.retry_request(path)
        with self.assertRaisesRegex(ValueError, 'retryable normal failure'): self.prepare()
        self.assertEqual(len(self.model.calls), 3)

    def test_self_reported_history_cannot_reset_immutable_native_attempt_count(self):
        first_path, _ = self.failed_preparation()
        self.retry_request(first_path)
        path, actual = self.failed_preparation()
        self.assertEqual(len(actual['validationAttempts']), 2)
        self.retry_request(path)
        request_before = deepcopy(self.request)
        fixed = actual['failedPlan']
        child_before = self.coordinator.store.get('new-exam', fixed['childRunId'])
        for mutation in ('counter-only', 'counter-and-context', 'context-and-binding', 'chain-swap'):
            forged = deepcopy(actual)
            # This is the reported exploit: rewrite only the receipt and mutable
            # wrapper records, while leaving the real terminal native child intact.
            forged['validationAttempts'] = [stage._failed_attempt(self.coordinator, fixed,
                {'questionResults': [actual['validationResult']]}, [])]
            if mutation != 'counter-only':
                forged['failedPlan']['retryContext']['validationAttempts'] = []
            if mutation == 'chain-swap':
                forged['failedPlan']['retryContext']['failure'] = {'ref': str(path), 'sha256': '0' * 64}
            if mutation in ('context-and-binding', 'chain-swap'):
                forged['failedPlan']['preparationBinding']['preparationInputHash'] = stage._preparation_input_hash(forged['failedPlan'])
            forged['failureHash'] = stage.sha256_json({k: v for k, v in forged.items() if k != 'failureHash'})
            self.write(path, forged)
            marker = stage._failure_marker(path, forged)
            self.coordinator.store.update('new-exam', fixed['parentRunId'], preparedStageFailure=marker)
            self.coordinator.store.update_question_stage('new-exam', fixed['parentRunId'], self.request['questionId'],
                self.request['stageId'], preparedStageFailure=marker, validationAttempts=forged['validationAttempts'])
            self.request = deepcopy(request_before)
            self.request['retryFromFailure']['sha256'] = stage._hash(path.read_bytes())
            self.write(self.request_path, self.request)
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, 'ancestry|parent/child/queue'):
                self.prepare()
            self.assertEqual(len(self.model.calls), 2)
            self.assertEqual(child_before, self.coordinator.store.get('new-exam', fixed['childRunId']))
        # Restore the genuine chain; it permits exactly the third candidate.
        self.write(path, actual)
        marker = stage._failure_marker(path, actual)
        self.coordinator.store.update('new-exam', fixed['parentRunId'], preparedStageFailure=marker)
        self.coordinator.store.update_question_stage('new-exam', fixed['parentRunId'], self.request['questionId'],
            self.request['stageId'], preparedStageFailure=marker, validationAttempts=actual['validationAttempts'])
        self.request = request_before; self.write(self.request_path, self.request)
        _, final_failure = self.failed_preparation()
        self.assertEqual(len(final_failure['validationAttempts']), 3)
        self.assertFalse(final_failure['retryable'])
        self.assertEqual(len(self.model.calls), 3)

    def test_ancestor_claim_and_receipt_are_verified_through_the_actual_fixed_chain(self):
        first_path, first = self.failed_preparation()
        self.retry_request(first_path)
        path, _ = self.failed_preparation()
        self.retry_request(path)
        claim_path = first_path.parent / 'retry-claim.json'
        claim = stage._json(claim_path.read_bytes())
        for mutation in ('claim-swap', 'claim-missing', 'receipt-drift'):
            if mutation == 'claim-swap':
                self.write(claim_path, {**claim, 'parentRunId': 'another-real-or-fake-parent'})
            elif mutation == 'claim-missing': claim_path.unlink()
            else: self.write(first_path, {**first, 'retryable': False})
            with self.subTest(mutation=mutation), self.assertRaises((ValueError, FileNotFoundError)):
                self.prepare()
            self.assertEqual(len(self.model.calls), 2)
            self.write(claim_path, claim); self.write(first_path, first)



if __name__ == '__main__': unittest.main()


class PreparedIntentStageTests(unittest.TestCase):
    """Run the actual bounded 02 writer with the same isolated native fixtures."""

    write = staticmethod(PreparedStageTests.write)
    prepare = PreparedStageTests.prepare
    plan = PreparedStageTests.plan
    apply = PreparedStageTests.apply
    test_approved_same_candidate_normal_save_and_exact_retry = PreparedStageTests.test_approved_same_candidate_normal_save_and_exact_retry
    test_prepared_candidate_drift_or_canonical_third_value_rejected = PreparedStageTests.test_prepared_candidate_drift_or_canonical_third_value_rejected
    test_normal_work_version_failure_rolls_back_exact_patch = PreparedStageTests.test_normal_work_version_failure_rolls_back_exact_patch
    test_receipt_write_interruption_holds_without_second_application = PreparedStageTests.test_receipt_write_interruption_holds_without_second_application

    def setUp(self):
        PreparedStageTests.setUp(self)
        self.request['stageId'] = 'question_intent'
        self.write(self.request_path, self.request)

    def test_intent_new_row_is_judgment_and_server_identity_only(self):
        source = Path(self.request['source']['ref']); before = source.read_bytes()
        self.prepare(); plan = self.plan()
        planned = next(value for value in plan['commitView']['patches'] if '15_correctChoiceText_fixed' in value['path'])
        row = json.loads(planned['afterUtf8'])[0]
        self.assertEqual(set(row), {'original_question_id', 'questionIntent', 'sourceQuestionKey', 'reviewQuestionId', 'sourceRecordRef'})
        self.assertEqual(row['questionIntent'], 'select_correct')
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse((self.root / planned['path']).exists())

    def test_intent_legacy_answers_metadata_and_other_records_are_preserved(self):
        source = Path(self.request['source']['ref'])
        path = source.parent.parent / '15_correctChoiceText_fixed' / (source.stem + '_merged_correctChoiceText_fixed.json')
        binding = self.request['source']['binding']
        legacy = {**binding, 'original_question_id': binding['reviewQuestionId'], 'questionIntent': 'select_incorrect',
            'correctChoiceText': ['正しい'], 'answer_result_text': '正解は 1 です。',
            'answer_result_inferred_correct_choice_numbers': [1], 'explanationText': ['旧説明を保持'],
            'questionIntent_changed': True, 'questionIntent_change_reason': '旧根拠', 'legacyPrivateMetadata': {'old': True}}
        other = {'original_question_id': 'other', 'sourceQuestionKey': 'new-exam:2026:other',
                 'reviewQuestionId': 'other', 'sourceRecordRef': 'other.json#0', 'questionIntent': 'select_incorrect'}
        self.write(path, [legacy, other]); source_before = source.read_bytes()
        unrelated = path.parent / 'unrelated.json'; self.write(unrelated, [other]); unrelated_before = unrelated.read_bytes()
        self.prepare(); plan = self.plan()
        planned = next(value for value in plan['commitView']['patches'] if '15_correctChoiceText_fixed' in value['path'])
        rows = json.loads(planned['afterUtf8'])
        self.assertEqual(rows, [{**legacy, 'questionIntent': 'select_correct'}, other])
        with patch.object(stage, 'verify_independent_approval', return_value={'reviewerSessionId': 'independent'}):
            receipt = self.apply()
            self.assertEqual(self.apply(), receipt)
        self.assertEqual(path.read_bytes(), planned['afterUtf8'].encode())
        self.assertEqual(unrelated.read_bytes(), unrelated_before)
        self.assertEqual(source.read_bytes(), source_before)

    def test_intent_scope_rejects_new_answer_injection(self):
        from tools.question_review_console.question_patch_proposal import IsolatedQuestionPatchWorkspace
        original = IsolatedQuestionPatchWorkspace.apply_record_update
        def inject(workspace, relative, **kwargs):
            result = original(workspace, relative, **kwargs)
            if '15_correctChoiceText_fixed' in Path(relative).parts:
                path = workspace.root / relative; rows = json.loads(path.read_bytes())
                rows[0]['correctChoiceText'] = ['正しい']; self.write(path, rows)
            return result
        with patch.object(IsolatedQuestionPatchWorkspace, 'apply_record_update', inject):
            with self.assertRaises(ValueError): self.prepare()
        self.assertFalse((self.output / 'plan.json').exists())

    def test_intent_scope_rejects_deleting_existing_legacy_answer(self):
        from tools.question_review_console.question_patch_proposal import IsolatedQuestionPatchWorkspace
        source = Path(self.request['source']['ref'])
        path = source.parent.parent / '15_correctChoiceText_fixed' / (source.stem + '_merged_correctChoiceText_fixed.json')
        binding = self.request['source']['binding']
        legacy = {**binding, 'original_question_id': binding['reviewQuestionId'], 'questionIntent': 'select_incorrect',
            'correctChoiceText': ['正しい'], 'answer_result_text': '正解は 1 です。'}
        self.write(path, [legacy]); before = path.read_bytes()
        original = IsolatedQuestionPatchWorkspace.apply_record_update
        def inject(workspace, relative, **kwargs):
            result = original(workspace, relative, **kwargs)
            if '15_correctChoiceText_fixed' in Path(relative).parts:
                file = workspace.root / relative; rows = json.loads(file.read_bytes())
                rows[0].pop('correctChoiceText'); self.write(file, rows)
            return result
        with patch.object(IsolatedQuestionPatchWorkspace, 'apply_record_update', inject):
            with self.assertRaises(ValueError): self.prepare()
        self.assertEqual(path.read_bytes(), before)


class PreparedStageGenuineHoldTests(unittest.TestCase):
    """Real normal preview protocol with an actual denied XML fetch and model hold."""
    write = staticmethod(PreparedStageTests.write)
    prepare = PreparedStageTests.prepare

    def setUp(self):
        from dataclasses import replace
        from tests import test_law_context_primary_cycle as law
        from tools.question_review_console.primary_law_evidence import PrimaryLawEvidenceResolver
        law.LawContextPrimaryCycleTests.setUp(self)

        class HoldModel(law.LawModel):
            def run_turn(self, prompt, **kwargs):
                result = super().run_turn(prompt, **kwargs)
                if kwargs['work_type'] == 'maintenance_law_context_candidate' and len(self.calls) >= 2:
                    payload = {'decision': 'blocked',
                        'summary': '現行法本文は実取得HTTP 403のため確認できず、確定を保留する。',
                        'update': {'setFields': [], 'unsetFields': []}}
                    return replace(result, final_message=json.dumps(payload, ensure_ascii=False))
                return result
        self.model = HoldModel(); self.coordinator.app_server = self.model
        self.fetcher = Mock(side_effect=OSError('HTTP 403'))
        self.coordinator.primary_law_evidence = PrimaryLawEvidenceResolver(self.root, fetcher=self.fetcher)

    def held(self):
        return stage.load_hold(self.root, self.output / 'hold-receipt.json', self.request_path,
                               coordinator=self.coordinator, connection=self.connection)

    def test_actual_denied_fetch_hold_is_nonretryable_and_closes_parent_queue_without_quality_attempt(self):
        source = Path(self.request['source']['ref']); before = source.read_bytes()
        receipt = self.prepare(); self.assertEqual(receipt, self.held())
        self.assertEqual(receipt['schemaVersion'], stage.HELD)
        self.assertEqual(receipt['status'], 'held'); self.assertIs(receipt['retryable'], False)
        self.assertIn('HTTP 403', receipt['reason']); self.assertEqual(receipt['qualityValidationAttempts'], [])
        self.assertFalse(receipt['formalPatchSaved']); self.assertFalse(receipt['FirestoreWritten'])
        fixed = receipt['holdPlan']; child = self.coordinator.store.get('new-exam', fixed['childRunId'])
        parent = self.coordinator.store.get('new-exam', fixed['parentRunId'])
        queue = self.coordinator._queue_stage(parent, self.request['questionId'], 'law_context')
        self.assertEqual(child['status'], 'succeeded'); self.assertTrue(child['receiptValidated'])
        self.assertEqual(parent['status'], 'blocked'); self.assertEqual(queue['status'], 'blocked')
        self.assertEqual(queue['validationAttempts'], []); self.assertFalse(queue['retryDeferred'])
        self.assertTrue(self.fetcher.called); self.assertEqual(len(self.model.calls), 2)
        proof = fixed['preparedCandidate']['content']['executionMetadata']['lawContextPrimaryEvidence']
        self.assertIn('HTTP 403', json.dumps(proof, ensure_ascii=False))
        self.assertEqual(source.read_bytes(), before)
        for name in ('plan.json', 'failure-receipt.json', 'save-receipt.json'):
            self.assertFalse((self.output / name).exists())
        stage._canonical_before(self.root, {'transactionBefore': fixed['canonicalInputsBefore']})

    def test_same_destination_replay_returns_exact_hold_without_model_or_child_changes(self):
        receipt = self.prepare(); fixed = receipt['holdPlan']
        child = self.coordinator.store.get('new-exam', fixed['childRunId'])
        self.assertEqual(receipt, self.prepare()); self.assertEqual(len(self.model.calls), 2)
        self.assertEqual(child, self.coordinator.store.get('new-exam', fixed['childRunId']))

    def test_hold_cannot_enter_approval_apply_or_mechanical_retry(self):
        self.prepare(); path = self.output / 'hold-receipt.json'
        with patch.object(stage, 'verify_independent_approval') as approval:
            with self.assertRaisesRegex(ValueError, 'fixed prepared-stage plan'):
                stage.approval_scope(self.root, path, coordinator=self.coordinator, connection=self.connection)
            with self.assertRaisesRegex(ValueError, 'fixed prepared-stage plan'):
                stage.apply(self.root, path, self.approval_path, coordinator=self.coordinator, connection=self.connection)
            approval.assert_not_called()
        self.request['retryFromFailure'] = {'ref': str(path), 'sha256': stage._hash(path.read_bytes())}
        self.write(self.request_path, self.request); self.output = self.private / 'retry-held'
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual(len(self.model.calls), 2)

    def test_forged_child_or_write_protocol_never_counts_as_genuine_hold(self):
        receipt = self.prepare(); fixed = receipt['holdPlan']
        child = self.coordinator.store.get('new-exam', fixed['childRunId'])
        mutations = [
            {'status': 'failed'}, {'questionId': 'other'}, {'parentRunId': 'other'}, {'stageId': 'explanation'},
            {'receiptValidated': False}, {'canonicalWriteStarted': True}, {'canonicalWriteStarted': 0},
            {'candidateTransactionOpen': True}, {'deltaUnknown': True}, {'writeAttributionVerified': False},
            {'unsafeChangedFiles': ['other']}, {'unsafeNotifiedChangedFiles': ['other']},
            {'workVersionReceipt': {'recordedCount': 1}}, {'patchApplyStartedAt': 'now'},
            {'rollback': {**child['rollback'], 'remainingChangedFiles': ['other']}},
            {'rollback': {**child['rollback'], 'status': 'succeeded', 'restoredFiles': ['other']}},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation), patch.object(self.coordinator.store, 'get', return_value={**child, **mutation}):
                with self.assertRaisesRegex(ValueError, 'genuine same-input'):
                    stage._genuine_hold(self.coordinator, fixed, {'questionResults': [receipt['validationResult']]})
        altered = deepcopy(fixed); altered['preparationBinding'].pop('holdContract')
        with self.assertRaises(ValueError):
            stage._genuine_hold(self.coordinator, altered, {'questionResults': [receipt['validationResult']]})
        with patch.object(self.coordinator.store, 'load_prepared_candidate', return_value={}):
            with self.assertRaises(ValueError):
                stage._genuine_hold(self.coordinator, fixed, {'questionResults': [receipt['validationResult']]})

    def test_candidate_or_result_mutation_is_rejected(self):
        receipt = self.prepare(); fixed = receipt['holdPlan']
        child = self.coordinator.store.get('new-exam', fixed['childRunId'])
        for mutation in ({'summary': 'fabricated'}, {'commands': [{'command': 'mechanical', 'status': 'fail'}]},
                         {'questionId': 'other'}, {'changedFiles': ['patch.json']}):
            result = {**receipt['validationResult'], **mutation}
            with self.subTest(mutation=mutation), patch.object(self.coordinator.store, 'get',
                    return_value={**child, 'batchQuestionResults': [result]}):
                with self.assertRaises(ValueError): stage._genuine_hold(self.coordinator, fixed, {'questionResults': [result]})

    def test_live_input_selection_and_canonical_third_value_conflicts_reject_replay(self):
        receipt = self.prepare(); fixed = receipt['holdPlan']
        self.snapshots['live-a']['updateTimeExact'] = 'changed'
        with self.assertRaises(ValueError): self.prepare()
        self.snapshots['live-a']['updateTimeExact'] = fixed['liveSnapshots']['live-a']['updateTimeExact']
        added = Path(self.request['source']['ref']).parent.parent / 'unselected' / 'added.json'
        self.write(added, {})
        with self.assertRaisesRegex(ValueError, 'selection drift'): self.held()
        added.unlink()
        before = fixed['canonicalInputsBefore'][0]; path = self.root / before['path']
        self.write(path, b'third value')
        with self.assertRaisesRegex(ValueError, 'third value'): self.held()
        self.assertEqual(len(self.model.calls), 2)

    def test_interrupted_hold_receipt_keeps_blocked_pending_and_never_replays_model(self):
        original = stage._exclusive
        def interrupted(path, value):
            if path.name == 'hold-receipt.json': raise OSError('hold receipt interrupted')
            return original(path, value)
        with patch.object(stage, '_exclusive', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'hold receipt interrupted'): self.prepare()
        # The intent pins both real runs even when the receipt write never completed.
        intent = json.loads((self.output / 'hold-intent.json').read_bytes())
        parent = self.coordinator.store.get('new-exam', intent['parentRunId'])
        self.assertEqual(parent['status'], 'blocked'); self.assertTrue(parent['preparedStageHoldPending'])
        with self.assertRaisesRegex(ValueError, 'exclusive new private'): self.prepare()
        self.assertEqual(len(self.model.calls), 2)
        self.assertFalse((self.output / 'plan.json').exists())

    def test_cli_controlled_hold_is_explicit_and_success_status_is_prepared(self):
        import io
        from contextlib import redirect_stdout
        args = ['--repo-root', str(self.root), '--db', str(self.db), 'prepare',
                '--request', str(self.request_path), '--output', str(self.output)]
        with patch.object(stage, 'coordinator_for', return_value=self.coordinator), redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(stage.main(args), 0)
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'held')
        with patch.object(stage, 'coordinator_for', return_value=self.coordinator), \
             patch.object(stage, 'prepare', return_value={'schemaVersion': stage.PREVIEW}), redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(stage.main(args), 0)
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'prepared')

    def test_hold_parent_marker_and_receipt_tampering_are_rejected_without_reexecution(self):
        receipt = self.prepare(); fixed = receipt['holdPlan']
        parent = self.coordinator.store.get('new-exam', fixed['parentRunId'])
        original = self.coordinator.store.get
        for changed in ({'preparedStageHold': {}}, {'preparedStageHoldPending': {'pending': True}}, {'status': 'succeeded'}):
            def get(qualification, run_id, *args, **kwargs):
                return {**parent, **changed} if run_id == fixed['parentRunId'] else original(qualification, run_id, *args, **kwargs)
            with self.subTest(changed=changed), patch.object(self.coordinator.store, 'get', side_effect=get):
                with self.assertRaisesRegex(ValueError, 'disposition'): self.held()
        path = self.output / 'hold-receipt.json'
        altered = deepcopy(receipt); altered['reason'] = 'arbitrary hold'
        self.write(path, altered)
        with self.assertRaisesRegex(ValueError, 'exact nonretryable'): self.held()
        self.assertEqual(len(self.model.calls), 2)

    def test_next_run_actual_recovered_evidence_can_be_fixed_for_a_fresh_preparation(self):
        from tests import test_law_context_primary_cycle as law
        from tools.question_review_console.primary_law_evidence import PrimaryLawEvidenceResolver
        receipt = self.prepare()
        proof = receipt['holdPlan']['preparedCandidate']['content']['executionMetadata']['lawContextPrimaryEvidence'][self.request['questionId']]
        record = {'choiceTextList': ['記述'], 'lawReferences': proof['rounds'][0]['lawReferences']}
        resolver = PrimaryLawEvidenceResolver(self.root / 'recovered-probe', fetcher=self.fetch)
        recovered = resolver.resolve(record, current_as_of='2026-10-22')
        self.assertTrue(recovered['items'][0]['currentSnapshot']['textHash'])
        probe = self.private / 'actual-recovered-evidence.json'; self.write(probe, recovered)
        self.request['primaryEvidence'].append({'ref': str(probe), 'sha256': stage._hash(probe.read_bytes())})
        self.write(self.request_path, self.request)
        self.output = self.private / 'next-run-recovered'
        self.model = law.LawModel(); self.coordinator.app_server = self.model
        self.coordinator.primary_law_evidence = resolver
        resumed = self.prepare()
        self.assertEqual(resumed['schemaVersion'], stage.PREVIEW)
        self.assertEqual(len(self.model.calls), 2)
        self.assertTrue((self.output / 'plan.json').is_file())
        self.assertFalse(resumed['formalPatchSaved']); self.assertFalse(resumed['FirestoreWritten'])

    def test_receipt_written_before_interruption_is_not_a_completed_hold_or_a_second_execution(self):
        original = stage._exclusive
        def interrupted(path, value):
            original(path, value)
            if path.name == 'hold-receipt.json': raise OSError('after receipt write')
        with patch.object(stage, '_exclusive', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'after receipt write'): self.prepare()
        self.assertTrue((self.output / 'hold-receipt.json').exists())
        with self.assertRaisesRegex(ValueError, 'disposition incomplete'): self.prepare()
        self.assertEqual(len(self.model.calls), 2)
        self.assertFalse((self.output / 'plan.json').exists())

    def normal_work_target(self):
        plan = self.coordinator._plan('new-exam', 'law_context', 'refresh', None,
            stage_ids=['law_context'], list_group_ids=['2026'], question_ids=[self.request['questionId']])
        return {**plan['progressTargets'][0], 'qualification': 'new-exam'}

    def test_new_normal_workversion_alias_and_legacy_paths_reject_held_replay(self):
        receipt = self.prepare(); fixed = receipt['holdPlan']
        target = {**fixed['stagePlan']['progressTargets'][0], 'qualification': 'new-exam'}
        store = self.coordinator.work_versions
        canonical = store.question_path_for(target)
        alias = canonical.parent / (hashlib.sha256(target['id'].encode()).hexdigest()[:24] + '.json')
        legacy = store.legacy_group_path_for('new-exam', '2026')
        self.assertNotEqual(canonical, alias)
        self.assertEqual(fixed['workVersionInputsBefore'],
            [{'path': str(canonical.relative_to(self.root)), 'exists': False, 'bytesHex': None}])
        unrelated = canonical.parent / 'unrelated-question.json'; self.write(unrelated, b'untouched unrelated')
        self.assertEqual(receipt, self.held())
        for path in (canonical, alias, legacy):
            self.write(path, b'third workVersion value')
            try:
                with self.subTest(path=path.name), self.assertRaisesRegex(ValueError, 'workVersion'):
                    self.prepare()
            finally: path.unlink()
        self.assertEqual(receipt, self.held()); self.assertEqual(len(self.model.calls), 2)
        self.assertEqual(unrelated.read_bytes(), b'untouched unrelated')

    def test_existing_normal_workversion_and_alias_legacy_bytes_and_presence_are_fixed(self):
        target = self.normal_work_target(); store = self.coordinator.work_versions
        store.record_stage([target], {'id': 'question_type', 'policyVersion': '1.0', 'policyFingerprint': 'fixture'},
                           run_id='prior-real-fixture', source='validated_run')
        canonical = store.question_path_for(target)
        payload = json.loads(canonical.read_bytes())
        alias_key = hashlib.sha256(target['id'].encode()).hexdigest()[:24]
        alias = canonical.parent / (alias_key + '.json')
        record = deepcopy(next(iter(payload['questions'].values())))
        record['reviewKey'] = target['id']
        self.write(alias, {**payload, 'questions': {alias_key: record}})
        legacy = store.legacy_group_path_for('new-exam', '2026'); self.write(legacy, payload)
        receipt = self.prepare()
        self.assertEqual({row['path'] for row in receipt['holdPlan']['workVersionInputsBefore']},
                         {str(path.relative_to(self.root)) for path in (canonical, alias, legacy)})
        for path in (canonical, alias, legacy):
            before = path.read_bytes()
            for mode in ('changed', 'deleted'):
                if mode == 'changed': path.write_bytes(before + b'\n')
                else: path.unlink()
                try:
                    with self.subTest(path=path.name, mode=mode), self.assertRaisesRegex(ValueError, 'workVersion'):
                        self.held()
                finally: path.write_bytes(before)
        self.assertEqual(receipt, self.held()); self.assertEqual(len(self.model.calls), 2)

    def test_normal_workversion_created_during_generation_cannot_save_a_hold(self):
        target = self.normal_work_target(); canonical = self.coordinator.work_versions.question_path_for(target)
        def mutate(_):
            if self.model.writer_count == 2: self.write(canonical, b'concurrent workVersion')
        self.model.before_receipt = mutate
        with self.assertRaisesRegex(ValueError, 'workVersion'): self.prepare()
        self.assertFalse((self.output / 'hold-receipt.json').exists())
        self.assertFalse((self.output / 'plan.json').exists())
        self.assertEqual(canonical.read_bytes(), b'concurrent workVersion')


class PreparedPrimaryEvidenceTests(unittest.TestCase):
    """Explicit source spans reach the private normal model input."""
    setUp = PreparedStageTests.setUp
    write = staticmethod(PreparedStageTests.write)
    prepare = PreparedStageTests.prepare
    plan = PreparedStageTests.plan
    apply = PreparedStageTests.apply
    def selection(self):
        path = self.private / 'primary.txt'
        self.write(path, '不要な前文\n正式なlawId=123AC1000000001\n不要な後文\n'.encode())
        ref = {'ref': str(path), 'sha256': stage._hash(path.read_bytes())}
        self.request['primaryEvidence'].append(ref)
        self.request['primaryEvidenceSelections'] = [{'evidence': ref, 'format': 'utf8_lines', 'startLine': 2, 'endLine': 2}]
        self.write(self.request_path, self.request)
        return path

    def test_span_reaches_actual_prompt_and_receipt_replays_without_model(self):
        from tools.question_review_console.law_audit_verification import question_inputs_from_prompt
        path = self.selection(); receipt = self.prepare(); plan = self.plan()
        question = question_inputs_from_prompt(self.model.calls[0][0])[self.request['questionId']]
        bundle = question['preparedPrimaryEvidence']; entry = bundle['entries'][0]
        self.assertEqual(entry['text'], '正式なlawId=123AC1000000001\n')
        self.assertNotIn('不要な前文', self.model.calls[0][0])
        self.assertEqual(entry['selection']['evidence']['sha256'], stage._hash(path.read_bytes()))
        self.assertNotEqual(entry['selection']['evidence']['sha256'], entry['extractedTextSha256'])
        self.assertEqual(bundle['scope']['sourceBinding'], self.request['source']['binding'])
        self.assertEqual(bundle['scope']['authority'], self.request['authority'])
        proof = plan['preparedCandidate']['content']['executionMetadata']['preparedPrimaryEvidenceDelivery']
        self.assertEqual(proof['bundleHash'], bundle['bundleHash'])
        self.assertEqual(proof['promptHash'], stage._hash(self.model.calls[0][0].encode()))
        self.assertEqual(receipt['primaryEvidenceBundleHash'], bundle['bundleHash'])
        self.assertEqual(plan['inputFingerprint'], bundle['scope']['inputFingerprint'])
        before_calls = len(self.model.calls)
        with patch.object(stage, 'verify_independent_approval', return_value={'reviewerSessionId': 'independent'}):
            first = self.apply(); self.assertTrue(first['formalPatchSaved']); self.assertEqual(self.apply(), first)
        self.assertEqual(len(self.model.calls), before_calls)

    def test_old_request_does_not_claim_evidence_read(self):
        from tools.question_review_console.law_audit_verification import question_inputs_from_prompt
        receipt = self.prepare(); plan = self.plan()
        self.assertNotIn('primaryEvidenceBundleHash', receipt)
        self.assertNotIn('preparedPrimaryEvidence', question_inputs_from_prompt(self.model.calls[0][0])[self.request['questionId']])
        self.assertNotIn('preparedPrimaryEvidenceDelivery', plan['preparedCandidate']['content']['executionMetadata'])

    def test_missing_modified_unlisted_unsupported_empty_or_bad_range_never_starts_model(self):
        path = self.selection(); original = path.read_bytes()
        for mutation in ('missing', 'modified', 'unlisted', 'pdf', 'empty', 'range'):
            self.write(path, original); request = deepcopy(self.request)
            if mutation == 'missing': path.unlink()
            elif mutation == 'modified': path.write_bytes(original + b'changed')
            elif mutation == 'unlisted': request['primaryEvidenceSelections'][0]['evidence']['sha256'] = 'a' * 64
            elif mutation == 'pdf': request['primaryEvidenceSelections'][0] = {'evidence': request['primaryEvidence'][0], 'format': 'pdf_pages', 'pages': [1]}
            elif mutation == 'empty': request['primaryEvidenceSelections'] = []
            elif mutation == 'range': request['primaryEvidenceSelections'][0]['endLine'] = 500
            self.write(self.request_path, request)
            with self.subTest(mutation=mutation), self.assertRaises((ValueError, RuntimeError, OSError)): self.prepare()
            self.assertFalse(self.model.calls); self.assertFalse(self.output.exists())

    def test_tamper_after_native_generation_cannot_create_plan(self):
        path = self.selection()
        self.model.before_receipt = lambda _: path.write_bytes(b'replaced after model read')
        with self.assertRaises((ValueError, RuntimeError, OSError)): self.prepare()
        self.assertEqual(len(self.model.calls), 1); self.assertFalse((self.output / 'plan.json').exists())

    def test_plan_rejects_changed_source_selection_or_task(self):
        path = self.selection(); self.prepare(); initial = path.read_bytes()
        for mutation in ('source', 'request', 'task'):
            if mutation == 'source': path.write_bytes(initial + b'changed')
            elif mutation == 'request':
                request = deepcopy(self.request); request['primaryEvidenceSelections'][0]['startLine'] = 1
                self.write(self.request_path, request)
            else: self.connection.execute("UPDATE improvement_tasks SET need='different'"); self.connection.commit()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): self.plan()
            path.write_bytes(initial); self.write(self.request_path, self.request)
        self.assertFalse((self.output / 'save-receipt.json').exists())

    def test_rebound_question_stage_source_input_policy_is_rejected(self):
        from tools.question_review_console.qualification_runs import _validate_prepared_primary_evidence
        self.selection(); self.prepare(); plan = self.plan()
        bundle = plan['stagePlan']['preparedPrimaryEvidence']; target = plan['stagePlan']['progressTargets'][0]
        original_record = stage._stage_inputs(self.coordinator, self.request, plan['stagePlan'],
            self.coordinator.store.get('new-exam', plan['parentRunId']), '')['candidateRecord']
        for field in ('qualification', 'stageId', 'questionId', 'listGroupId', 'inputFingerprint',
                      'projectedInputHash', 'policyFingerprint', 'inputRecordHash', 'sourceBinding'):
            mutated = deepcopy(bundle); mutated['scope'][field] = {'wrong': True} if field == 'sourceBinding' else 'other'
            mutated['bundleHash'] = stage.sha256_json({k: v for k, v in mutated.items() if k != 'bundleHash'})
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                _validate_prepared_primary_evidence(self.root, mutated, qualification='new-exam', stage_id=self.request['stageId'],
                    target=target, input_fingerprint=plan['inputFingerprint'], projected_input_hash=plan['projectedInputHash'],
                    policy_fingerprint=plan['stagePlan']['policyFingerprints'][self.request['stageId']], record=original_record)

    def test_native_interruption_and_replaced_prompt_never_silently_reuse_evidence(self):
        self.selection()
        self.model.before_receipt = lambda _: (_ for _ in ()).throw(InterruptedError('interrupted native read'))
        with self.assertRaises(InterruptedError): self.prepare()
        self.assertFalse((self.output / 'plan.json').exists())
        self.model.before_receipt = None
        self.output = self.private / 'fresh-after-interruption'
        self.prepare(); plan = self.plan(); count = len(self.model.calls)
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual(count, len(self.model.calls))
        self.coordinator.store.update('new-exam', plan['childRunId'], preparedPrimaryEvidencePromptHash='a' * 64)
        with self.assertRaises(ValueError): self.plan()

    def test_xml_law_body_title_and_main_must_be_globally_unique_and_directly_associated(self):
        from tools.question_review_console.qualification_runs import _prepared_primary_evidence_entries
        path = self.private / 'ambiguous-law.xml'
        title = '<LawTitle>First law</LawTitle>'
        main = '<MainProvision><Article Num="2">First text</Article></MainProvision>'
        body = '<LawBody>' + title + main + '</LawBody>'
        law = '<Law>' + body + '</Law>'
        variants = {
            'two-laws': '<Laws>' + law + law.replace('First', 'Second') + '</Laws>',
            'one-wrapped-law': '<Laws>' + law + '</Laws>',
            'nested-law': '<Law>' + body + law + '</Law>',
            'two-bodies': '<Law>' + body + body.replace('First', 'Second') + '</Law>',
            'nested-body': '<Law><LawBody>' + title + main + body + '</LawBody></Law>',
            'two-mains': '<Law><LawBody>' + title + main + main + '</LawBody></Law>',
            'two-titles': '<Law><LawBody>' + title + title.replace('First', 'Second') + main + '</LawBody></Law>',
            'title-outside-body': '<Law>' + title + '<LawBody>' + main + '</LawBody></Law>',
            'main-outside-body': '<Law><LawBody>' + title + '</LawBody>' + main + '</Law>',
            'title-nested': '<Law><LawBody><Other>' + title + '</Other>' + main + '</LawBody></Law>',
            'main-nested': '<Law><LawBody>' + title + '<Other>' + main + '</Other></LawBody></Law>',
            'empty-title': law.replace('First law', ' '),
            'duplicate-article': law.replace('</MainProvision>', '<Article Num="2">Second text</Article></MainProvision>'),
        }
        for name, value in variants.items():
            self.write(path, value.encode())
            selection = {'evidence': {'ref': str(path), 'sha256': stage._hash(path.read_bytes())},
                'format': 'xml_articles', 'articles': ['2']}
            with self.subTest(mutation=name), self.assertRaises(RuntimeError):
                _prepared_primary_evidence_entries(self.root, [selection])
        self.write(path, law.encode())
        selection['evidence']['sha256'] = stage._hash(path.read_bytes())
        entry = _prepared_primary_evidence_entries(self.root, [selection])[0]
        self.assertIn('First law', entry['text']); self.assertIn('First text', entry['text'])

    def test_bounded_xml_json_and_binary_contracts(self):
        from tools.question_review_console.qualification_runs import _prepared_primary_evidence_entries
        path = self.private / 'law.xml'
        xml = b'<Law><LawBody><LawTitle>Test</LawTitle><MainProvision><Article Num="2">Needed</Article><Article Num="42">Duty</Article></MainProvision></LawBody></Law>'
        self.write(path, xml); ref = {'ref': str(path), 'sha256': stage._hash(xml)}
        entries = _prepared_primary_evidence_entries(self.root, [{'evidence': ref, 'format': 'xml_articles', 'articles': ['2', '42']}])
        self.assertIn('Needed', entries[0]['text']); self.assertIn('Duty', entries[0]['text'])
        receipt = self.private / 'receipt.json'; self.write(receipt, {'lawId': '123AC100', 'unused': 'omit', 'a/b': [1, 2]})
        reference = {'ref': str(receipt), 'sha256': stage._hash(receipt.read_bytes())}
        entries = _prepared_primary_evidence_entries(self.root, [{'evidence': reference, 'format': 'json_pointers', 'pointers': ['/lawId', '/a~1b/1']}])
        self.assertNotIn('unused', entries[0]['text']); self.assertEqual(json.loads(entries[0]['text'])['/a~1b/1'], 2)
        for value, kind in ((b'%PDF fake', 'utf8_lines'), (b'<Law><Article Num="2">a</Article><Article Num="2">b</Article></Law>', 'xml_articles'), (b'x' * 16_001, 'utf8_lines')):
            self.write(path, value); ref = {'ref': str(path), 'sha256': stage._hash(value)}
            selection = {'evidence': ref, 'format': kind, **({'articles': ['2']} if kind == 'xml_articles' else {'startLine': 1, 'endLine': 1})}
            with self.assertRaises(RuntimeError): _prepared_primary_evidence_entries(self.root, [selection])



class EarlyContentFailureRetryTests(unittest.TestCase):
    """Pre-baseline content rejection preserves the real new-attempt write state."""
    setUp = PreparedStageTests.setUp
    write = staticmethod(PreparedStageTests.write)
    prepare = PreparedStageTests.prepare
    plan = PreparedStageTests.plan
    apply = PreparedStageTests.apply
    retry_request = PreparedStageTests.retry_request

    def bad_explanation_model(self):
        from dataclasses import replace
        self.request['stageId'] = 'explanation'; self.write(self.request_path, self.request)
        original = self.model.run_turn
        def run(prompt, **kwargs):
            result = original(prompt, **kwargs)
            if kwargs['work_type'] == 'maintenance_explanation_candidate':
                payload = json.loads(result.final_message)
                # This focused model fixture supplies the current 03 learning
                # pattern contract as well as the prose under test.
                for key, value in {'questionLearningPatternId': 'terms_basics', 'explanationReferences': []}.items():
                    payload['update']['setFields'] = [item for item in payload['update']['setFields'] if item['field'] != key]
                    payload['update']['setFields'].append({'field': key, 'value': value})
                    payload['update']['unsetFields'] = [field for field in payload['update']['unsetFields'] if field != key]
                for field in payload['update']['setFields']:
                    if field['field'] == 'explanationText':
                        field['value'] = (['正しい。ガス事業法第2条第1項は、小売供給を定義している。']
                            if len(self.model.calls) == 1 else ['正しい。'])
                return replace(result, final_message=json.dumps(payload, ensure_ascii=False))
            return result
        self.model.run_turn = run

    def content_failure(self):
        self.bad_explanation_model()
        with patch.object(self.coordinator.store, 'write_baseline', wraps=self.coordinator.store.write_baseline) as baseline:
            with self.assertRaisesRegex(ValueError, 'fixed failure receipt'): self.prepare()
            baseline.assert_not_called()
        path = self.output / 'failure-receipt.json'; receipt = stage._json(path.read_bytes())
        return path, receipt

    def test_real_prebaseline_content_failure_records_no_write_and_normal_retry_uses_feedback(self):
        path, receipt = self.content_failure()
        self.assertTrue(receipt['retryable'])
        self.assertIn('機械的に文頭', receipt['validationResult']['summary'])
        self.assertIn({'command': 'question content', 'status': 'fail'}, receipt['validationResult']['commands'])
        fixed = receipt['failedPlan']; child = self.coordinator.store.get('new-exam', fixed['childRunId'])
        self.assertIs(child['canonicalWriteStarted'], False)
        self.assertIs(child['candidateTransactionOpen'], False)
        self.assertIs(child['deltaUnknown'], False)
        self.assertEqual(child['workVersionReceipt']['recordedCount'], 0)
        self.assertFalse(child.get('baselinePath')); self.assertFalse(child.get('patchApplyStartedAt'))
        self.retry_request(path)
        try: self.prepare()
        except ValueError:
            failure = stage._json((self.output / 'failure-receipt.json').read_bytes())
            self.fail(failure['validationResult']['summary'])
        plan = self.plan()
        retry_child = self.coordinator.store.get('new-exam', plan['childRunId'])
        self.assertEqual(retry_child['modelAttempt']['attemptMode'], 'retry')
        self.assertIn('機械的に文頭', self.model.calls[-1][0])
        self.assertIs(retry_child['canonicalWriteStarted'], False)
        self.assertEqual(child, self.coordinator.store.get('new-exam', fixed['childRunId']))
        self.assertFalse((self.output / 'save-receipt.json').exists())
        self.assertEqual(len(self.model.calls), 2)

    def test_fresh_marker_stays_false_in_preview_and_flips_only_at_normal_commit(self):
        self.prepare(); plan = self.plan()
        before = self.coordinator.store.get('new-exam', plan['childRunId'])
        self.assertIs(before['canonicalWriteStarted'], False)
        observed = []
        original_update = self.coordinator.store.update
        def observe(*args, **kwargs):
            if kwargs.get('canonicalWriteStarted') is True: observed.append(deepcopy(kwargs))
            return original_update(*args, **kwargs)
        with patch.object(self.coordinator.store, 'update', side_effect=observe):
            with patch.object(stage, 'verify_independent_approval', return_value={'reviewerSessionId': 'independent'}):
                self.assertTrue(self.apply()['formalPatchSaved'])
        self.assertEqual(len(observed), 1)
        self.assertIs(self.coordinator.store.get('new-exam', plan['childRunId'])['canonicalWriteStarted'], True)

    def test_prior_started_unknown_or_open_state_is_not_reset_to_allow_preview(self):
        for flags in ({'canonicalWriteStarted': True}, {'canonicalWriteStarted': None},
                      {'deltaUnknown': True}, {'candidateTransactionOpen': True}, {'patchApplyStartedAt': 'already-started'}):
            self.output = self.private / ('unsafe-preview-' + str(len(self.model.calls)))
            real_prepare = self.coordinator._prepare_question_candidate_attempt
            def unsafe(*args, **kwargs):
                prepared = real_prepare(*args, **kwargs)
                self.coordinator.store.update('new-exam', prepared['childId'], **flags)
                return prepared
            with self.subTest(flags=flags), patch.object(self.coordinator, '_prepare_question_candidate_attempt', side_effect=unsafe):
                with self.assertRaisesRegex(RuntimeError, 'previewできません'): self.prepare()
            self.assertFalse((self.output / 'failure-receipt.json').exists())
            self.assertFalse((self.output / 'plan.json').exists())

    def test_content_failure_with_changed_projected_input_cannot_become_retry_receipt(self):
        self.bad_explanation_model()
        real_prepare = self.coordinator._prepare_question_candidate_attempt
        def drift(*args, **kwargs):
            prepared = real_prepare(*args, **kwargs)
            target = prepared['targets'][0]; path = self.root / target['_projectedInputPath']
            self.model.before_receipt = lambda _: path.write_bytes(path.read_bytes() + b' ')
            return prepared
        with patch.object(self.coordinator, '_prepare_question_candidate_attempt', side_effect=drift):
            with self.assertRaisesRegex((ValueError, RuntimeError), 'drift|hash|変更|一致'): self.prepare()
        self.assertFalse((self.output / 'failure-receipt.json').exists())
        self.assertFalse((self.output / 'plan.json').exists())


class PreparedLawAuditTimestampTests(unittest.TestCase):
    """Normal prepared writer binds one server time after its independent audits."""
    write = staticmethod(PreparedStageTests.write)
    prepare = PreparedStageTests.prepare
    plan = PreparedStageTests.plan
    apply = PreparedStageTests.apply

    def setUp(self):
        from tests.test_law_audit_verification import NormalLawJudgmentDeliveryTests
        from datetime import datetime, timezone
        NormalLawJudgmentDeliveryTests.setUp(self)
        source=Path(self.request['source']['ref'])
        payload=json.loads(source.read_text());payload['question_bodies'][0]['examYear']=2026
        self.write(source,payload);self.request['source']['sha256']=stage._hash(source.read_bytes())
        self.write(self.request_path,self.request)
        self.write(self.root/stage.MANIFEST,(json.dumps({'path':str(source.relative_to(self.root)),
            'sha256':self.request['source']['sha256']})+'\n').encode())
        self.clock = {'now': datetime(2026,10,23,23,58,tzinfo=timezone.utc)}
        self.review_finished_at = datetime(2026,10,23,23,59,59,tzinfo=timezone.utc)
        self.review_calls = []
        from tools.question_review_console import qualification_runs as runs
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):
                current=self.clock['now']
                return current.astimezone(tz) if tz is not None else current.replace(tzinfo=None)
        clock = patch.object(runs,'datetime',Clock);clock.start();self.addCleanup(clock.stop)
        self.runs=runs
        # Checkpoints must observe the normal merged patch projection after the
        # commit, rather than the source-only fixture's immutable initial DTO.
        from tools.question_review_console.inventory import QuestionInventory
        reader=QuestionInventory(self.root)
        original_group=self.inventory.group
        def current_group(qualification,list_group_id):
            group=original_group(qualification,list_group_id)
            for question in group['questions']:
                projected=reader.projected_input(qualification,list_group_id,question['sourceRecordRef'])
                question['projected']=dict(projected.record)
                question['isLawRelated']=question['projected'].get('isLawRelated',False)
                question['paths']['patches']=list(projected.applied_files)
            return group
        self.inventory.group=current_group
        from dataclasses import replace
        from tools.question_review_console.codex_app_server import AppServerTurnResult
        original=self.model.run_turn
        def run(prompt,**kwargs):
            kind=kwargs['work_type']
            if kind in {'maintenance_law_audit_secondary_audit_candidate','maintenance_law_audit_tertiary_audit_candidate'}:
                bundle=json.loads(prompt.splitlines()[-1]);fields=bundle['proposedFields']
                self.review_calls.append(kind)
                self.clock['now']=self.review_finished_at
                return AppServerTurnResult(thread_id='independent-review-'+str(len(self.review_calls)),
                    session_id='independent-session',turn_id='independent-turn',model='fixture',service_tier=None,
                    final_message=json.dumps({'schemaVersion':'law-audit-verification/v1',
                        'questionId':bundle['question']['questionId'],'evidenceHash':bundle['evidenceHash'],
                        'decision':'approve','issues':[],'verifiedSourceUrls':['https://laws.e-gov.go.jp/fixture'],
                        'examTimeDecision':fields['examTimeDecision'],'currentLawDecision':fields['currentLawDecision']}))
            result=original(prompt,**kwargs)
            if kind=='maintenance_law_audit_candidate':
                payload=json.loads(result.final_message)
                fields={item['field']:item['value'] for item in payload['update']['setFields']}
                fields.update(isLawRelated=True,auditStatus='same_as_current',reviewState='needs_secondary_review',
                    reconciliationStatus='pending_secondary_review')
                for fact in fields['lawRevisionFacts']:
                    fact.update(auditStatus='same_as_current',reviewState='needs_secondary_review',reconciliationStatus='pending_secondary_review')
                payload['update']['setFields']=[{'field':key,'value':value} for key,value in fields.items()]
                result=replace(result,final_message=json.dumps(payload,ensure_ascii=False),
                    model_turn_finished_at='2026-10-23T23:58:00+00:00')
            return result
        self.model.run_turn=run
        self.coordinator.primary_law_evidence.resolve=Mock(return_value={'status':'complete','items':[]})

    def test_independent_completion_time_survives_preview_and_next_day_normal_apply(self):
        self.prepare();plan=self.plan()
        metadata=plan['preparedCandidate']['content']['executionMetadata']
        stamp=metadata['lawAuditCompletion']
        self.assertEqual(stamp['completedAt'],'2026-10-23T23:59:59+00:00')
        self.assertNotEqual(stamp['completedAt'],metadata['modelTurnFinishedAt'])
        self.assertEqual(self.review_calls,['maintenance_law_audit_secondary_audit_candidate'])
        self.assertEqual(stamp['runId'],plan['childRunId'])
        self.clock['now']=self.review_finished_at.replace(day=24,hour=0,minute=1)
        preview=stage._run(self.coordinator,plan,preview=True)
        self.assertEqual(preview['preview'],plan['commitView'])
        with patch.object(stage,'verify_independent_approval',return_value={'reviewerSessionId':'independent'}):
            receipt=self.apply();self.assertTrue(receipt['formalPatchSaved'])
            self.assertEqual(self.apply(),receipt)
        for planned in plan['commitView']['patches']:
            self.assertEqual((self.root/planned['path']).read_bytes(),planned['afterUtf8'].encode())
        audit=next(p for p in plan['commitView']['patches'] if 'law_revision_audit' in p['path'])
        sidecar=json.loads(audit['afterUtf8'].splitlines()[0])
        self.assertEqual(sidecar['auditedAt'],stamp['completedAt'])
        self.assertEqual(sidecar['nextAuditDueAt'],'2027-10-23')
        self.assertTrue(sidecar['lawCorpusSnapshotId'].startswith('codex-web-primary:2026-10-23:'))

    def test_timestamp_less_historical_envelope_is_not_filled_from_current_clock(self):
        persist=self.coordinator.store.persist_prepared_candidate
        def legacy_first_save(qualification,run_id,envelope):
            changed=deepcopy(envelope);changed['content']['executionMetadata'].pop('lawAuditCompletion')
            changed.pop('contentHash');changed['contentHash']='sha256:'+self.runs._canonical_json_hash(changed)
            return persist(qualification,run_id,changed)
        with patch.object(self.coordinator.store,'persist_prepared_candidate',side_effect=legacy_first_save), \
                patch.object(self.coordinator.store,'write_baseline',wraps=self.coordinator.store.write_baseline) as baseline:
            with self.assertRaisesRegex(self.runs.QualificationRunError,'新規prepare'):self.prepare()
            baseline.assert_not_called()
        self.assertFalse((self.output/'plan.json').exists())
        self.assertEqual(len(self.review_calls),1)

    def test_timestamp_from_another_run_is_rejected_even_with_valid_envelope_hash(self):
        persist=self.coordinator.store.persist_prepared_candidate
        def foreign_first_save(qualification,run_id,envelope):
            changed=deepcopy(envelope);content=changed['content'];metadata=content['executionMetadata']
            metadata['lawAuditCompletion']=self.runs._law_audit_completion_metadata(
                qualification=qualification,run_id='foreign-run',question_ids=[changed['questionId']],
                input_fingerprint_value=changed['inputFingerprint'],projected_input_hash=changed['projectedInputHash'],
                candidate_payload=content['candidatePayload'],execution_metadata=metadata)
            changed.pop('contentHash');changed['contentHash']='sha256:'+self.runs._canonical_json_hash(changed)
            return persist(qualification,run_id,changed)
        with patch.object(self.coordinator.store,'persist_prepared_candidate',side_effect=foreign_first_save), \
                patch.object(self.coordinator.store,'write_baseline',wraps=self.coordinator.store.write_baseline) as baseline:
            with self.assertRaisesRegex(self.runs.QualificationRunError,'新規prepare'):self.prepare()
            baseline.assert_not_called()

    def test_already_persisted_completion_cannot_be_replaced_for_another_approval_scope(self):
        self.prepare();plan=self.plan();envelope=deepcopy(plan['preparedCandidate'])
        metadata=envelope['content']['executionMetadata']
        metadata['lawAuditCompletion']=self.runs._law_audit_completion_metadata(
            qualification=plan['qualification'],run_id=plan['childRunId'],question_ids=[plan['request']['questionId']],
            input_fingerprint_value=plan['inputFingerprint'],projected_input_hash=plan['projectedInputHash'],
            candidate_payload=envelope['content']['candidatePayload'],execution_metadata=metadata,
            completed_at=self.review_finished_at.replace(day=24))
        envelope.pop('contentHash');envelope['contentHash']='sha256:'+self.runs._canonical_json_hash(envelope)
        with self.assertRaisesRegex(self.runs.QualificationRunError,'preparedCandidateは変更できません'):
            self.coordinator.store.persist_prepared_candidate(plan['qualification'],plan['childRunId'],envelope)
        self.assertEqual(self.plan()['preparedCandidate'],plan['preparedCandidate'])
        self.assertFalse((self.output/'apply-intent.json').exists())

    def test_non_law_03b_has_one_fixed_server_time_without_independent_legal_reviews(self):
        from dataclasses import replace
        source=Path(self.request['source']['ref']);payload=json.loads(source.read_text())
        record=payload['question_bodies'][0]
        record.update(isLawRelated=False,lawGroundedExplanationNotNeeded=True,
            explanationText=['正しい。水は高い所から低い所へ流れる。'])
        self.write(source,payload);self.request['source']['sha256']=stage._hash(source.read_bytes())
        self.write(self.request_path,self.request)
        self.write(self.root/stage.MANIFEST,(json.dumps({'path':str(source.relative_to(self.root)),
            'sha256':self.request['source']['sha256']})+'\n').encode())
        original=NativeProfileFixture.run_turn.__get__(self.model,NativeProfileFixture)
        def non_law(prompt,**kwargs):
            result=original(prompt,**kwargs)
            if kwargs['work_type']=='maintenance_law_audit_candidate':
                value=json.loads(result.final_message)
                fields={item['field']:item['value'] for item in value['update']['setFields']}
                fields.update(auditStatus='not_law_related',isLawRelated=False,
                    lawReferences=[[]],lawGroundedExplanationNotNeeded=True,lawContextForExplanation='法令に関係しない技術的事項である。')
                for fact in fields['lawRevisionFacts']:
                    fact.update(auditStatus='not_law_related',reviewState='secondary_verified')
                value['update']['setFields']=[{'field':key,'value':val} for key,val in fields.items()]
                result=replace(result,final_message=json.dumps(value,ensure_ascii=False))
            return result
        self.model.run_turn=non_law
        self.prepare();plan=self.plan()
        metadata=plan['preparedCandidate']['content']['executionMetadata']
        self.assertFalse(metadata['lawAuditVerification']);self.assertFalse(self.review_calls)
        self.clock['now']=self.review_finished_at.replace(day=24)
        self.assertEqual(stage._run(self.coordinator,plan,preview=True)['preview'],plan['commitView'])
        with patch.object(stage,'verify_independent_approval',return_value={'reviewerSessionId':'independent'}):
            self.assertTrue(self.apply()['formalPatchSaved'])


class PreparedQuestionSetCategoryBindingTests(unittest.TestCase):
    setUp=PreparedStageTests.setUp
    write=staticmethod(PreparedStageTests.write)
    prepare=PreparedStageTests.prepare
    plan=PreparedStageTests.plan
    apply=PreparedStageTests.apply

    def category(self):
        path=self.root/'output/new-exam/category/category.json'
        self.write(path,{'folders':[{'folderId':'folder','name':'分類'}],
            'questionSets':[{'questionSetId':'test-question-set','folderId':'folder','name':'基本分類',
                'description':'問題全体の判断基準','matchingHints':['一般的な技術知識']} ]})
        return path

    def question_set(self):
        self.request['stageId']='question_set';self.write(self.request_path,self.request)
        return self.category()

    def test_canonical_category_is_actual_model_input_and_fixed_before_scope_apply(self):
        category=self.question_set();self.prepare();plan=self.plan()
        key=str(category.relative_to(self.root))
        self.assertEqual(plan['inputFiles'][key],stage._hash(category.read_bytes()))
        self.assertTrue(any('基本分類' in prompt for prompt,_ in self.model.calls))
        with patch.object(stage,'verify_independent_approval',return_value={'reviewerSessionId':'independent'}):
            self.assertTrue(self.apply()['formalPatchSaved'])
        self.assertEqual(category.read_bytes(),stage._bytes({'folders':[{'folderId':'folder','name':'分類'}],
            'questionSets':[{'questionSetId':'test-question-set','folderId':'folder','name':'基本分類',
                'description':'問題全体の判断基準','matchingHints':['一般的な技術知識']} ]}))

    def test_changed_or_missing_category_invalidates_load_scope_and_apply(self):
        category=self.question_set();self.prepare();before=category.read_bytes()
        for mutation in ('changed','missing'):
            if mutation=='changed':category.write_bytes(before+b'\n')
            else:category.unlink()
            for name,operation in (('load',self.plan),('scope',lambda:stage.approval_scope(self.root,self.output/'plan.json',
                    coordinator=self.coordinator,connection=self.connection)),('apply',self.apply)):
                with self.subTest(mutation=mutation,operation=name),self.assertRaises((ValueError,OSError)):operation()
            category.write_bytes(before)
        self.assertFalse((self.output/'apply-intent.json').exists())
        self.plan()

    def test_changed_during_actual_generation_never_creates_a_plan(self):
        category=self.question_set();original=self.model.run_turn
        def changed(prompt,**kwargs):
            result=original(prompt,**kwargs);category.write_bytes(category.read_bytes()+b'\n');return result
        self.model.run_turn=changed
        with self.assertRaises(ValueError):self.prepare()
        self.assertFalse((self.output/'plan.json').exists())

    def test_missing_own_category_and_foreign_symlink_never_start_model(self):
        self.request['stageId']='question_set';self.write(self.request_path,self.request)
        other=self.root/'output/other-exam/category/category.json'
        self.write(other,{'folders':[{'folderId':'f'}],'questionSets':[{'questionSetId':'foreign','folderId':'f'}]})
        with self.assertRaises((ValueError,OSError)):self.prepare()
        own=self.root/'output/new-exam/category/category.json';own.parent.mkdir(parents=True,exist_ok=True);own.symlink_to(other)
        with self.assertRaises((ValueError,OSError)):self.prepare()
        self.assertFalse(self.model.calls)

    def test_legacy_or_foreign_category_binding_is_not_silently_added_to_scope(self):
        category=self.question_set();self.prepare();plan=self.plan();key=str(category.relative_to(self.root))
        for kind in ('missing','foreign'):
            changed=deepcopy(plan);digest=changed['inputFiles'].pop(key)
            if kind=='foreign':changed['inputFiles']['output/other-exam/category/category.json']=digest
            with self.subTest(kind=kind),self.assertRaisesRegex(ValueError,'fresh prepare'):
                stage._scope_data(self.root,self.output/'plan.json',changed,self.connection)
        self.assertEqual(plan['inputFiles'][key],stage._hash(category.read_bytes()))

    def test_other_stage_does_not_add_category_or_change_its_existing_prompt(self):
        category=self.category();receipt=self.prepare();plan=self.plan()
        self.assertNotIn(str(category.relative_to(self.root)),plan['inputFiles'])
        self.assertFalse(any('基本分類' in prompt for prompt,_ in self.model.calls))
        category.write_bytes(category.read_bytes()+b'\n')
        self.assertEqual(self.plan(),plan)
