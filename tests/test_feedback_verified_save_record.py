"""Formal-save ledger proof using real load_saved and temporary file ledgers."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
import pytest

from tests import test_feedback_verified_save as fixtures
from tests import test_feedback_verified_maintenance as maintenance_fixtures
from tools.question_bank import feedback_verified_save as saver
from tools.question_bank import feedback_verified_save_record as records
from tools.question_review_console.scoped_artifacts import write_json
from tools.question_review_console.firestore_readback import FirestoreReadback
from scripts.upload import upload_questions_to_firestore as uploader
from tests.test_native_independent_approval import native_audit
from tests import test_feedback_verified_stage_execution as native_fixtures


@pytest.fixture(autouse=True)
def prohibit_live_firestore(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('live Firestore SDK is forbidden in save-record tests')
    monkeypatch.setattr(FirestoreReadback, '_database', forbidden)
    monkeypatch.setattr(uploader, 'init_firestore', forbidden)
    monkeypatch.setattr(uploader.firestore, 'client', forbidden)
    monkeypatch.setattr(uploader.firebase_admin, 'initialize_app', forbidden)


def genuine_save_approval(fixture):
    fixture.mock_approval.stop()
    p = patch.object(records.stages.native, 'NATIVE_SESSION_ROOT', fixture.root)
    p.start(); fixture.addCleanup(p.stop)
    folder = fixture.root / 'native-save'; folder.mkdir()
    scope = saver.approval_scope(fixture.root, fixture.path, fixture.plan)
    fixture.approval = native_audit(folder, scope, encrypted=False)
    fixture.binding = records.stages.native.verify_independent_approval(fixture.approval, scope, production=True)
    fixture.mock_approval = patch.object(saver, 'verify_independent_approval', wraps=records.stages.native.verify_independent_approval)
    fixture.mock_approval.start(); fixture.addCleanup(fixture.mock_approval.stop)


def file_ledger(fixture):
    path = fixture.root / 'ledger.sqlite3'
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    fixture.connection.backup(connection)
    fixture.addCleanup(connection.close)
    fixture.connection = connection
    return path


class VerifiedSaveRecordTest(unittest.TestCase):
    selected_snapshots = fixtures.VerifiedSaveTest.selected_snapshots
    save = fixtures.VerifiedSaveTest.save
    append_event = staticmethod(native_fixtures.VerifiedStageExecutionTests.append_event)
    owner_response = staticmethod(native_fixtures.VerifiedStageExecutionTests.owner_response)
    owner_progress = native_fixtures.VerifiedStageExecutionTests.owner_progress

    def owner_launch(self, argv, *, call_id='owner-launch'):
        parent = Path(self.approval['authorization']['sessionPath'])
        self.append_event(parent, {'type': 'turn_context', 'payload': {'turn_id': 'owner-turn'}})
        self.owner_order = max((e.get('metadata', {}).get('user_input_order', -1)
            for e in map(json.loads, parent.read_bytes().splitlines())), default=-1) + 1
        launch = self.owner_response('custom_tool_call', name='exec', call_id=call_id,
            input=records.stages.native_operation_call(argv, module_name=records.MODULE), status='completed')
        launch['metadata'] = {'client_authored': False, 'user_input_order': self.owner_order}
        self.append_event(parent, launch)
        return parent

    def setUp(self):
        # Existing fixture mocks native approval and live reads only. Plan,
        # projectors, saved journal, references and load_saved remain real.
        fixtures.VerifiedSaveTest.setUp(self)
        self.ledger = file_ledger(self)
        genuine_save_approval(self)
        self.receipt = self.save()

    def record(self, hook=None):
        return records.record(self.connection, root=self.root, plan_path=self.path, failure_hook=hook)

    def inspect(self):
        return records.inspect(self.connection, root=self.root, task_id='task')

    def table_count(self):
        if not records._exists(self.connection):
            return 0
        return self.connection.execute('SELECT COUNT(*) FROM verified_formal_save_records').fetchone()[0]

    def test_record_real_saved_refs_privacy_backup_idempotency_and_inspect(self):
        artifact_bytes = {p: p.read_bytes() for p in self.root.rglob('*.json')}
        with patch.object(saver, 'load_saved', wraps=saver.load_saved) as genuine:
            result = self.record()
        self.assertEqual(genuine.call_count, 1)
        self.assertTrue(all(call.kwargs['production'] is True
                            for call in saver.verify_independent_approval.call_args_list))
        self.assertEqual(result['schemaVersion'], records.SCHEMA)
        self.assertEqual((result['taskKind'], result['taskId'], result['improvementTaskId']),
                         ('improvement', 'task', 'task'))
        self.assertEqual(result['state'], 'formally_saved')
        self.assertFalse(result['FirestoreWritten'])
        self.assertFalse(result['publicationCompleted'])
        self.assertEqual(result['requirementHash'], self.plan['taskRequirement']['requirementHash'])
        self.assertEqual(result['candidateSha256'], self.plan['candidateSha256'])
        for name, path in [('plan', self.path), ('saveReceipt', self.path.parent / 'save-receipt.json'),
                           ('candidate', Path(self.plan['candidateRef'])),
                           ('before', Path(self.plan['beforeRef'])), ('formal', self.formal),
                           ('original', Path(self.receipt['originalRef'])),
                           ('saveIntent', Path(self.receipt['originalRef']).parent / 'intent.json')]:
            self.assertEqual(result['artifacts'][name]['ref'], str(path))
            self.assertEqual(result['artifacts'][name]['sha256'], saver.file_hash(path))
        self.assertTrue(set(self.plan['inputs']) <= set(result['inputFiles']))
        data = self.connection.execute('SELECT record_json FROM verified_formal_save_records').fetchone()[0]
        for forbidden in ('旧説明', '違いを具体化', '本文', 'candidate_id', 'definition', 'sources', 'source_path'):
            self.assertNotIn(forbidden, data)
        backup = self.ledger.with_suffix('.backup.sqlite3')
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        backup_db = sqlite3.connect(backup)
        try:
            self.assertIsNone(backup_db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='verified_formal_save_records'").fetchone())
        finally:
            backup_db.close()
        backup_stamp = backup.stat().st_mtime_ns
        changes = self.connection.total_changes
        self.assertEqual(self.record(), result)
        self.assertEqual(self.inspect(), result)
        self.assertEqual(self.connection.total_changes, changes)
        self.assertEqual(backup.stat().st_mtime_ns, backup_stamp)
        self.assertEqual(self.table_count(), 1)
        self.assertEqual(artifact_bytes, {p: p.read_bytes() for p in artifact_bytes})

    def test_missing_native_approval_never_creates_optional_table(self):
        path = self.path.parent / 'save-receipt.json'; value = json.loads(path.read_bytes())
        value['approvalEvidence'] = {}
        write_json(path, value)
        with self.assertRaisesRegex(ValueError, 'native session evidence'):
            self.record()
        self.assertEqual(self.table_count(), 0)
        self.assertFalse(records._exists(self.connection))

    def test_source_requirement_and_work_authority_revocation_rejected(self):
        for statement in (
            "UPDATE improvement_tasks SET need='different'",
            "UPDATE ai_question_candidates SET review_status='no_task'",
            "UPDATE improvement_tasks SET status='completed'",
        ):
            self.connection.execute('SAVEPOINT fixture')
            self.connection.execute(statement)
            self.connection.execute('RELEASE fixture')
            with self.assertRaises(ValueError):
                self.record()
            self.connection.execute("UPDATE improvement_tasks SET need='違いを説明',status='review_pending'")
            self.connection.execute("UPDATE ai_question_candidates SET review_status='promoted'")
            self.connection.commit()
        self.assertFalse(records._exists(self.connection))

    def test_candidate_third_value_and_canonical_rollback_rejected_and_preserved(self):
        candidate = self.formal.read_bytes()
        for value in (b'competing third value', self.before):
            self.formal.write_bytes(value)
            with self.assertRaises(ValueError):
                self.record()
            self.assertEqual(self.formal.read_bytes(), value)
            self.assertEqual(self.table_count(), 0)
        self.formal.write_bytes(candidate)
        self.record()
        self.formal.write_bytes(self.before)
        with self.assertRaises(ValueError):
            self.inspect()
        self.assertEqual(self.table_count(), 1)

    def test_all_fixed_artifact_tampering_refused(self):
        files = [self.path, self.path.parent / 'save-receipt.json', Path(self.plan['beforeRef']),
                 Path(self.plan['candidateRef']), Path(self.receipt['originalRef']),
                 Path(self.receipt['originalRef']).parent / 'intent.json', self.root / 'question.pdf']
        for path in files:
            with self.subTest(path=path):
                value = path.read_bytes()
                path.write_bytes(b'changed')
                with self.assertRaises((ValueError, KeyError)):
                    self.record()
                self.assertFalse(records._exists(self.connection))
                path.write_bytes(value)

    def test_heavy_load_saved_runs_outside_write_transaction_and_race_rejected(self):
        genuine = saver.load_saved
        def race(*args, **kwargs):
            self.assertFalse(self.connection.in_transaction)
            result = genuine(*args, **kwargs)
            (self.root / 'question.pdf').write_bytes(b'changed during verification')
            return result
        with patch.object(saver, 'load_saved', side_effect=race):
            with self.assertRaisesRegex(ValueError, 'changed during recording'):
                self.record()
        self.assertFalse(records._exists(self.connection))

    def test_external_source_race_after_verification_is_cas_rejected(self):
        def race(stage):
            if stage == 'after_verification':
                other = sqlite3.connect(self.ledger)
                try:
                    other.execute("UPDATE ai_question_candidates SET text_hash='changed'")
                    other.commit()
                finally:
                    other.close()
        with self.assertRaisesRegex(ValueError, 'authority changed'):
            self.record(race)
        self.assertFalse(records._exists(self.connection))

    def test_input_races_before_insert_and_commit_roll_back_record(self):
        for hook_stage in ('after_verification', 'before_insert', 'after_insert', 'before_commit'):
            with self.subTest(stage=hook_stage):
                value = self.path.read_bytes()
                def race(stage):
                    if stage == hook_stage:
                        self.path.write_bytes(value + b' ')
                with self.assertRaisesRegex(ValueError, 'evidence changed'):
                    self.record(race)
                self.assertFalse(records._exists(self.connection))
                self.assertFalse(self.connection.in_transaction)
                self.path.write_bytes(value)

    def test_interrupted_transaction_rolls_back_table_and_retry_succeeds(self):
        for hook_stage in ('before_insert', 'after_insert', 'before_commit'):
            def fail(stage):
                if stage == hook_stage:
                    raise RuntimeError('interrupted')
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                self.record(fail)
            self.assertFalse(records._exists(self.connection))
        self.assertEqual(self.record()['state'], 'formally_saved')
        self.assertEqual(self.table_count(), 1)

    def test_native_session_change_is_ephemeral_guard_not_persisted_body(self):
        session = Path(self.approval['authorization']['sessionPath'])
        original = session.read_bytes()
        def race(stage):
            if stage == 'before_insert':
                self.append_event(session, {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': []}})
        with self.assertRaisesRegex(ValueError, 'new decision input'):
            self.record(race)
        self.assertFalse(records._exists(self.connection))
        session.write_bytes(original)
        result = self.record()
        self.assertNotIn(str(session), json.dumps(result))
        # Unrelated later activity is re-verified by load_saved at inspect,
        # without becoming an immutable whole-session hash conflict.
        self.append_event(session, {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'phase': 'commentary', 'content': []}})
        self.assertEqual(self.inspect(), result)

    def test_append_only_triggers_and_future_transition_refused(self):
        result = self.record()
        for statement in ("UPDATE verified_formal_save_records SET state='completed'",
                          'DELETE FROM verified_formal_save_records'):
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'append-only'):
                self.connection.execute(statement)
            self.connection.rollback()
        with self.assertRaises(TypeError):
            records.record(self.connection, root=self.root, plan_path=self.path, state='published')
        self.assertEqual(self.inspect(), result)

    def test_record_tampering_or_consistently_rehashed_extra_claim_is_rejected(self):
        self.record()
        for rehash in (False, True):
            self.connection.execute('DROP TRIGGER verified_formal_save_records_no_update')
            row = self.connection.execute('SELECT record_json,recorded_at FROM verified_formal_save_records').fetchone()
            value = json.loads(row[0])
            value['extraClaim'] = 'publication approved'
            digest = records._hash(value) if rehash else '0' * 64
            self.connection.execute('UPDATE verified_formal_save_records SET record_json=?,record_hash=?,event_hash=?',
                (records._json(value), digest, records._hash({'recordSha256': digest, 'recordedAt': row[1]})))
            self.connection.execute(records.TRIGGERS['verified_formal_save_records_no_update'])
            self.connection.commit()
            with self.assertRaises(ValueError):
                self.record()
            with self.assertRaises(ValueError):
                self.inspect()
            self.assertEqual(self.table_count(), 1)

    def test_missing_trigger_and_stale_source_refuse_inspect(self):
        self.record()
        self.connection.execute('DROP TRIGGER verified_formal_save_records_no_delete')
        self.connection.commit()
        with self.assertRaisesRegex(ValueError, 'append-only trigger'):
            self.inspect()
        self.connection.execute(records.TRIGGERS['verified_formal_save_records_no_delete'])
        self.connection.execute("UPDATE ai_question_candidates SET review_status='unreviewed'")
        self.connection.commit()
        with self.assertRaisesRegex(ValueError, 'source requirement'):
            self.inspect()

    def test_backup_failure_busy_writer_and_nested_transaction_refused(self):
        with patch('tools.question_bank.feedback_daily._backup_db', side_effect=OSError('backup failed')):
            with self.assertRaisesRegex(OSError, 'backup failed'):
                self.record()
        self.assertFalse(records._exists(self.connection))
        other = sqlite3.connect(self.ledger)
        other.execute('BEGIN IMMEDIATE')
        self.connection.execute('PRAGMA busy_timeout=1')
        try:
            with self.assertRaises(sqlite3.OperationalError):
                self.record()
        finally:
            other.rollback()
            other.close()
        self.connection.execute('BEGIN')
        with self.assertRaisesRegex(ValueError, 'independent'):
            self.record()
        self.assertTrue(self.connection.in_transaction)
        self.connection.rollback()
        self.assertFalse(records._exists(self.connection))

    def test_inspect_does_not_bootstrap_optional_table(self):
        with self.assertRaisesRegex(ValueError, 'not found'):
            self.inspect()
        self.assertFalse(records._exists(self.connection))

    def test_inspect_rejects_requirement_changed_before_final_record_reread(self):
        self.record()
        genuine = records._stored
        count = 0
        def race(connection, kind, task_id):
            nonlocal count
            count += 1
            if count == 2:
                # BEGIN is deferred: this external commit occurs before the
                # final read snapshot is established, reproducing the audit.
                other = sqlite3.connect(self.ledger)
                try:
                    other.execute("UPDATE improvement_tasks SET need='changed before final record read'")
                    other.commit()
                finally:
                    other.close()
            return genuine(connection, kind, task_id)
        with patch.object(records, '_stored', side_effect=race):
            with self.assertRaisesRegex(ValueError, 'authority changed'):
                self.inspect()
        self.assertEqual(count, 2)
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(self.connection.execute('PRAGMA query_only').fetchone()[0], 0)
        self.assertEqual(self.table_count(), 1)

    def test_inspect_rechecks_files_after_final_record_reread(self):
        self.record()
        genuine = records._stored
        count = 0
        def race(connection, kind, task_id):
            nonlocal count
            count += 1
            result = genuine(connection, kind, task_id)
            if count == 2:
                self.path.write_bytes(self.path.read_bytes() + b' ')
            return result
        with patch.object(records, '_stored', side_effect=race):
            with self.assertRaisesRegex(ValueError, 'evidence changed'):
                self.inspect()
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(self.connection.execute('PRAGMA query_only').fetchone()[0], 0)

    def test_inspect_uses_short_readonly_snapshot_after_heavy_validation(self):
        expected = self.record()
        genuine_load, genuine_stored = saver.load_saved, records._stored
        count = 0
        def verify(*args, **kwargs):
            self.assertFalse(self.connection.in_transaction)
            return genuine_load(*args, **kwargs)
        def reread(connection, kind, task_id):
            nonlocal count
            count += 1
            if count == 2:
                self.assertTrue(connection.in_transaction)
                self.assertEqual(connection.execute('PRAGMA query_only').fetchone()[0], 1)
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("UPDATE improvement_tasks SET need='forbidden inspect write'")
            return genuine_stored(connection, kind, task_id)
        with patch.object(saver, 'load_saved', side_effect=verify) as load, \
             patch.object(records, '_stored', side_effect=reread):
            self.assertEqual(self.inspect(), expected)
        self.assertEqual(load.call_count, 1)
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(self.connection.execute('PRAGMA query_only').fetchone()[0], 0)
        self.connection.execute('PRAGMA query_only=ON')
        try:
            self.assertEqual(self.inspect(), expected)
            self.assertEqual(self.connection.execute('PRAGMA query_only').fetchone()[0], 1)
        finally:
            self.connection.execute('PRAGMA query_only=OFF')

    def test_cli_record_inspect_missing_db_and_future_state(self):
        base = ['--repo-root', str(self.root), '--db', str(self.ledger)]
        output = io.StringIO()
        self.owner_launch([*base, 'record', '--plan', str(self.path)])
        with redirect_stdout(output):
            self.assertEqual(records.main([*base, 'record', '--plan', str(self.path)]), 0)
        result = json.loads(output.getvalue())
        output = io.StringIO()
        self.owner_launch([*base, 'inspect', '--task-id', 'task'])
        with redirect_stdout(output):
            self.assertEqual(records.main([*base, 'inspect', '--task-id', 'task']), 0)
        self.assertEqual(json.loads(output.getvalue()), result)
        missing = self.root / 'missing.sqlite3'
        for command in (['record', '--plan', str(self.path)], ['inspect', '--task-id', 'task']):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(records.main(['--repo-root', str(self.root), '--db', str(missing), *command]), 1)
            self.assertFalse(missing.exists())
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            records.main([*base, 'record', '--plan', str(self.path), '--state', 'completed'])

    def test_canonical_cli_record_inspect_and_replay_allow_progress_and_same_cell_wait(self):
        genuine = saver.load_saved
        expected = None
        base = ['--repo-root', str(self.root), '--db', str(self.ledger)]
        for command in ('record', 'inspect', 'record'):
            argv = [*base, command, *(['--plan', str(self.path)] if command == 'record' else ['--task-id', 'task'])]
            parent = self.owner_launch(argv)
            def heavy(*args, **kwargs):
                self.assertFalse(kwargs['connection'].in_transaction)
                for event in self.owner_progress(): self.append_event(parent, event)
                return genuine(*args, **kwargs)
            output = io.StringIO()
            with patch.object(saver, 'load_saved', side_effect=heavy), redirect_stdout(output):
                self.assertEqual(records.main(argv), 0)
            result = json.loads(output.getvalue())
            expected = expected or result
            self.assertEqual(result, expected)
        self.assertEqual(self.table_count(), 1)
        self.assertEqual(result['schemaVersion'], records.SCHEMA)

    def test_library_call_does_not_invent_a_cli_owner_from_a_record_launch(self):
        argv = ['--repo-root', str(self.root), '--db', str(self.ledger), 'record', '--plan', str(self.path)]
        parent = self.owner_launch(argv)
        def progress(moment):
            if moment == 'before_insert':
                for event in self.owner_progress(): self.append_event(parent, event)
        with self.assertRaisesRegex(ValueError, 'new decision input'):
            self.record(progress)
        self.assertFalse(records._exists(self.connection))

    def test_owner_rejects_other_cell_tool_turn_input_unknown_suffix_and_revocation(self):
        argv = ['--repo-root', str(self.root), '--db', str(self.ledger), 'record', '--plan', str(self.path)]
        parent = self.owner_launch(argv); reviewer = Path(self.approval['reviewerFinal']['sessionPath'])
        initial = parent.read_bytes(); review_before = reviewer.read_bytes()
        for mutation in ('cell', 'tool', 'turn', 'user', 'agent', 'unknown', 'revocation'):
            parent.write_bytes(initial); reviewer.write_bytes(review_before)
            def race(moment):
                if moment != 'before_commit': return
                events = self.owner_progress()
                if mutation == 'cell': events[5]['payload']['arguments'] = json.dumps({'cell_id': 'other'})
                elif mutation == 'tool': events[5]['payload']['name'] = 'exec'
                elif mutation == 'turn': events[4]['payload']['internal_chat_message_metadata_passthrough']['turn_id'] = 'other'
                elif mutation == 'user': events.append(self.owner_response('message', role='user', content=[]))
                elif mutation == 'agent': events.append({'type': 'response_item', 'payload': {'type': 'agent_message', 'author': '/root/other', 'content': []}})
                elif mutation == 'unknown': events.append({'type': 'unknown_native_suffix', 'payload': {}})
                for event in events: self.append_event(parent, event)
                if mutation == 'revocation': self.append_event(reviewer, self.owner_response('message', role='assistant', phase='final_answer', content=[{'type': 'output_text', 'text': 'rejected'}]))
            with self.subTest(mutation=mutation), records.stages.native_operation(argv, module_name=records.MODULE):
                with self.assertRaisesRegex(ValueError, 'new decision input'):
                    self.record(race)
            self.assertFalse(records._exists(self.connection))
            self.assertFalse(self.connection.in_transaction)
        parent.write_bytes(initial); reviewer.write_bytes(review_before)

    def test_consumed_prefix_rewrite_truncate_replace_and_identity_change_roll_back(self):
        parent = Path(self.approval['authorization']['sessionPath']); original = parent.read_bytes()
        for mutation in ('rewrite', 'truncate', 'replace', 'identity'):
            parent.write_bytes(original)
            def race(moment):
                if moment != 'after_insert': return
                if mutation == 'rewrite': parent.write_bytes(original.replace(b'author', b'Author', 1))
                elif mutation == 'truncate': parent.write_bytes(original[:-1])
                elif mutation == 'identity': parent.write_bytes(original.replace(b'"id": "author"', b'"id": "otherx"', 1))
                else:
                    other = parent.with_suffix('.replacement'); other.write_bytes(original); other.replace(parent)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): self.record(race)
            self.assertFalse(records._exists(self.connection))
        parent.write_bytes(original)

    def test_full_reference_coverage_and_shared_helper_input_hashes_are_required(self):
        self.record()
        stored = records._stored(self.connection, 'improvement', 'task')
        self.assertIn(str(Path(records.stages.__file__).resolve()), stored['inputFiles'])
        self.assertIn(str(Path(records.stages.context_api.__file__).resolve()), stored['inputFiles'])
        genuine = records._file_hash
        helper = Path(records.stages.__file__).resolve()
        def changed(path): return '0' * 64 if Path(path) == helper else genuine(path)
        with patch.object(records, '_file_hash', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'conflicts'): self.inspect()
            with self.assertRaisesRegex(ValueError, 'conflicts'): self.record()
        self.assertEqual(records._stored(self.connection, 'improvement', 'task'), stored)

    def test_nested_native_reference_not_in_direct_approval_guard_is_rejected(self):
        folder = self.root / 'unconsumed'; folder.mkdir()
        extra = native_audit(folder, {'nativeCreationCall': 'unused'}, encrypted=False)
        path = self.path.parent / 'save-receipt.json'; receipt = json.loads(path.read_bytes())
        receipt['extraNestedReference'] = {'deep': extra['authorization']}
        write_json(path, receipt)
        with self.assertRaisesRegex(ValueError, 'guard coverage'):
            self.record()
        self.assertFalse(records._exists(self.connection))

    def legacy_record(self):
        payload = records._prepare(self.connection, self.root, self.path)['payload']
        payload['schemaVersion'] = records.LEGACY_SCHEMA
        payload['inputFiles'][str(Path(records.__file__).resolve())] = 'a' * 64  # Historical verifier, never adopted as current.
        raw = records._json(payload); digest = records._hash(payload); now = '2026-10-19T00:00:00Z'
        records._tables(self.connection)
        self.connection.execute('INSERT INTO verified_formal_save_records VALUES (?,?,?,?,?,?,?,?,?)',
            ('improvement', 'task', 'task', 'formally_saved', str(self.path), raw, digest, now,
             records._hash({'recordSha256': digest, 'recordedAt': now})))
        self.connection.commit()
        return self.connection.execute('SELECT * FROM verified_formal_save_records').fetchall()

    def test_legacy_v1_replay_and_inspect_preserve_fact_but_require_explicit_migration(self):
        before = self.legacy_record(); count = self.connection.total_changes
        for operation in (self.inspect, self.record):
            with self.assertRaises(records.LegacyRuntimeMigrationRequired) as caught: operation()
            value = caught.exception.observation
            self.assertTrue(value['recorded'])
            self.assertFalse(value['currentRuntimeVerified'])
            self.assertEqual(value['holdReason'], 'legacy_runtime_migration_required')
            self.assertFalse(value['FirestoreWritten']); self.assertFalse(value['publicationCompleted'])
        for command in (['inspect', '--task-id', 'task'], ['record', '--plan', str(self.path)]):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(records.main(['--repo-root', str(self.root), '--db', str(self.ledger), *command]), 1)
            self.assertTrue(json.loads(output.getvalue())['recorded'])
        self.assertEqual(self.connection.total_changes, count)
        self.assertEqual([tuple(r) for r in before], [tuple(r) for r in self.connection.execute('SELECT * FROM verified_formal_save_records')])

    def test_legacy_structure_hash_event_and_trigger_corruption_are_not_accepted_as_saved_fact(self):
        self.legacy_record()
        for column in ('record_json', 'record_hash', 'event_hash'):
            self.connection.execute('SAVEPOINT fixture')
            self.connection.execute('DROP TRIGGER verified_formal_save_records_no_update')
            self.connection.execute('UPDATE verified_formal_save_records SET ' + column + "='corrupted'")
            self.connection.execute(records.TRIGGERS['verified_formal_save_records_no_update'])
            with self.assertRaises((ValueError, json.JSONDecodeError)): self.inspect()
            self.connection.execute('ROLLBACK TO fixture'); self.connection.execute('RELEASE fixture')
        self.connection.execute('DROP TRIGGER verified_formal_save_records_no_delete'); self.connection.commit()
        with self.assertRaisesRegex(ValueError, 'trigger differs'): self.inspect()

    def test_every_successful_terminal_rechecks_native_prefix_and_idempotent_ledger_identity(self):
        genuine = records.stages.assert_native_guard
        for operation in (self.record, self.record, self.inspect):
            with patch.object(records.stages, 'assert_native_guard', wraps=genuine) as verify:
                operation()
            self.assertTrue(any(c.kwargs.get('terminal') is True for c in verify.call_args_list))
        def replace_at_terminal(guard, *, terminal=False):
            result = genuine(guard, terminal=terminal)
            if terminal:
                replacement = self.ledger.with_suffix('.replacement')
                other = sqlite3.connect(replacement); other.close(); replacement.replace(self.ledger)
            return result
        with patch.object(records.stages, 'assert_native_guard', side_effect=replace_at_terminal):
            with self.assertRaisesRegex(ValueError, 'ledger file replaced'):
                self.record()
        self.assertFalse(self.connection.in_transaction)

    def test_native_revocation_at_final_inspect_reread_preserves_existing_record(self):
        result = self.record(); before = self.connection.total_changes
        genuine = records._stored; calls = 0
        reviewer = Path(self.approval['reviewerFinal']['sessionPath'])
        def revoke(connection, kind, task_id):
            nonlocal calls
            value = genuine(connection, kind, task_id); calls += 1
            if calls == 2:
                self.append_event(reviewer, {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                    'phase': 'final_answer', 'content': [{'type': 'output_text', 'text': 'rejected'}]}})
            return value
        with patch.object(records, '_stored', side_effect=revoke):
            with self.assertRaisesRegex(ValueError, 'new decision input'): self.inspect()
        self.assertEqual(self.connection.total_changes, before)
        self.assertEqual(genuine(self.connection, 'improvement', 'task'), result)

    def test_guard_implementation_change_during_heavy_verification_is_a_cas_failure(self):
        changed = False; helper = Path(records.stages.__file__).resolve()
        genuine_load, genuine_hash = saver.load_saved, records._file_hash
        def drift(*args, **kwargs):
            nonlocal changed
            result = genuine_load(*args, **kwargs); changed = True
            return result
        def digest(path): return '0' * 64 if changed and Path(path) == helper else genuine_hash(path)
        with patch.object(saver, 'load_saved', side_effect=drift), patch.object(records, '_file_hash', side_effect=digest):
            with self.assertRaisesRegex(ValueError, 'evidence changed'): self.record()
        self.assertFalse(records._exists(self.connection))


class MaintenanceFormalSaveRecordTest(unittest.TestCase):
    def setUp(self):
        self.fixture = maintenance_fixtures.VerifiedMaintenanceTest(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.ledger = file_ledger(self.fixture)
        connection = self.fixture.connection
        connection.executescript("""CREATE TABLE maintenance_tasks(task_id,status,binding_hash);
            CREATE TABLE maintenance_task_history(id,task_id,status,binding_hash);
            INSERT INTO maintenance_tasks VALUES ('maintenance','open','fixed');
            INSERT INTO maintenance_task_history VALUES (1,'maintenance','open','fixed');""")
        # This is only a local ledger transaction test; existing native law gate
        # tests own the three independent approvals. load_saved remains real.
        self.laws = patch.object(saver, '_law_review_bindings', return_value=[])
        self.laws.start()
        self.addCleanup(self.laws.stop)
        for stage in saver.LAW_STAGES:
            folder = self.fixture.root / ('native-law-' + stage); folder.mkdir()
            write_json(self.fixture.path.parent / f'law-{stage}-approval.json', native_audit(folder, {'nativeCreationCall': 'law fixture'}, encrypted=False))
        f = self.fixture
        folder = f.root / 'native-maintenance'; folder.mkdir()
        audit = Path(f.authority['auditRef']['ref'])
        write_json(audit, native_audit(folder, {'nativeCreationCall': 'maintenance fixture'}, encrypted=False))
        f.authority['auditRef']['sha256'] = saver.file_hash(audit)
        f.plan['maintenanceBinding'] = deepcopy(f.authority)
        f.plan['inputs'][str(audit)] = saver.file_hash(audit)
        f.plan['planHash'] = saver.sha256_json({k: v for k, v in f.plan.items() if k != 'planHash'})
        write_json(f.path, f.plan)
        genuine_save_approval(f)
        self.fixture.save()

    def test_formal_task_id_and_immutable_authority_are_separate(self):
        f = self.fixture
        before = [tuple(r) for r in f.connection.execute('SELECT * FROM maintenance_tasks')]
        history = [tuple(r) for r in f.connection.execute('SELECT * FROM maintenance_task_history')]
        result = records.record(f.connection, root=f.root, plan_path=f.path)
        self.assertEqual((result['taskKind'], result['taskId'], result['improvementTaskId']),
                         ('maintenance', 'maintenance', 'task'))
        self.assertEqual(len(result['lawReviewRefs']), 3)
        self.assertEqual(result['maintenanceBindingSha256'], saver.sha256_json(f.plan['maintenanceBinding']))
        self.assertEqual(before, [tuple(r) for r in f.connection.execute('SELECT * FROM maintenance_tasks')])
        self.assertEqual(history, [tuple(r) for r in f.connection.execute('SELECT * FROM maintenance_task_history')])
        self.assertEqual(records.inspect(f.connection, root=f.root, task_id='maintenance'), result)
        self.assertEqual(records.record(f.connection, root=f.root, plan_path=f.path), result)

    def test_maintenance_authority_race_rolls_back_save_record(self):
        f = self.fixture
        def race(stage):
            if stage == 'after_insert':
                f.connection.execute("UPDATE maintenance_tasks SET status='completed'")
        with self.assertRaisesRegex(ValueError, 'authority changed'):
            records.record(f.connection, root=f.root, plan_path=f.path, failure_hook=race)
        self.assertFalse(records._exists(f.connection))
        self.assertEqual(f.connection.execute('SELECT status FROM maintenance_tasks').fetchone()[0], 'open')
        self.assertEqual(f.connection.execute('SELECT COUNT(*) FROM maintenance_task_history').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
