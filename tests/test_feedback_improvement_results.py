from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.question_bank.feedback_daily import (
    _private_db, daily_summary, list_improvement_tasks, record_improvement_result,
)


class ImprovementResultTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'ledger.sqlite3'
        self.db = _private_db(self.path)
        self.addCleanup(self.db.close)
        with self.db:
            self.db.execute(
                '''INSERT INTO improvement_tasks
                   (task_id,title,need,target,created_at,updated_at)
                   VALUES ('task','Title','Need','question:q1','before','before')'''
            )
        self.artifact = Path(self.temp.name) / 'result.json'
        self.write_artifact()

    def write_artifact(self, **overrides):
        data = {'schemaVersion':'feedback-improvement-result/v1', 'taskId':'task',
                'status':'review_pending', 'approvalReady':False,
                'formalPatchSaved':False, 'FirestoreWritten':False,
                'proposedUpdates':{'q1':{'explanationText':'Public educational text'}}}
        data.update(overrides)
        self.artifact.write_text(json.dumps(data))
        self.digest = hashlib.sha256(self.artifact.read_bytes()).hexdigest()

    def record(self, **overrides):
        args = dict(task_id='task', status='review_pending', expected_status='open',
                    reason='Sources checked; independent review pending',
                    artifact_ref=self.artifact, artifact_sha256=self.digest)
        args.update(overrides)
        return record_improvement_result(self.db, **args)

    def test_pending_work_stays_visible_and_history_contains_only_references(self):
        result = self.record()
        row = list_improvement_tasks(self.db, limit=10)[0]
        self.assertEqual(row['status'], 'review_pending')
        self.assertEqual(row['artifact_sha256'], self.digest)
        self.assertEqual(daily_summary(self.db)['improvementTasksOpen'], 1)
        self.assertEqual(daily_summary(self.db)['improvementTasksByStatus'], {'review_pending':1})
        history = self.db.execute('SELECT * FROM improvement_result_history').fetchone()
        self.assertEqual(history['id'], result)
        self.assertNotIn('Public educational text', str(tuple(history)))

    def test_replayed_result_is_idempotent_but_cannot_rewind_later_work(self):
        first = self.record()
        self.assertEqual(first, self.record())
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM improvement_result_history').fetchone()[0], 1)
        self.write_artifact(status='blocked')
        self.record(status='blocked', expected_status='review_pending', reason='New source conflict')
        self.write_artifact()
        with self.assertRaisesRegex(ValueError, 'status changed'):
            self.record()
        self.assertEqual(list_improvement_tasks(self.db, limit=1)[0]['status'], 'blocked')

    def test_hash_task_binding_and_external_write_claims_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'hash differs'):
            self.record(artifact_sha256='0'*64)
        for overrides in [{'taskId':'other'}, {'approvalReady':True},
                          {'formalPatchSaved':True}, {'FirestoreWritten':True},
                          {'status':'blocked'}, {'schemaVersion':'other'}]:
            self.write_artifact(**overrides)
            with self.assertRaisesRegex(ValueError, 'binding differs'):
                self.record()
        self.assertEqual(self.db.execute('SELECT status FROM improvement_tasks').fetchone()[0], 'open')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM improvement_result_history').fetchone()[0], 0)

    def test_terminal_states_unknown_task_and_stale_expected_status_are_rejected(self):
        for state in ['completed','published','approved','cancelled']:
            with self.assertRaisesRegex(ValueError, 'unsupported'):
                self.record(status=state)
        self.write_artifact(taskId='absent')
        with self.assertRaisesRegex(ValueError, 'not found'):
            self.record(task_id='absent')
        self.write_artifact()
        with self.assertRaisesRegex(ValueError, 'status changed'):
            self.record(expected_status='in_progress')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM improvement_result_history').fetchone()[0], 0)

    def test_schema_upgrade_is_repeatable_and_preserves_pending_results(self):
        self.record()
        other = _private_db(self.path)
        try:
            self.assertEqual(list_improvement_tasks(other, limit=1)[0]['status'], 'review_pending')
            self.assertEqual(other.execute('SELECT COUNT(*) FROM improvement_result_history').fetchone()[0], 1)
        finally:
            other.close()

    def test_cli_records_result_and_refreshes_private_backup(self):
        command = [sys.executable, '-m', 'tools.question_bank.feedback_daily',
                   '--db', str(self.path), 'record-improvement-result', 'task',
                   '--status','review_pending','--expected-status','open',
                   '--reason','Sources checked; review pending',
                   '--artifact-ref',str(self.artifact),'--artifact-sha256',self.digest]
        result = subprocess.run(command,capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(result.stdout)['summary']['improvementTasksByStatus'], {'review_pending':1})
        backup = self.path.with_name('ledger.backup.sqlite3')
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(backup) as copied:
            self.assertEqual(copied.execute('SELECT status FROM improvement_tasks').fetchone()[0], 'review_pending')


if __name__ == '__main__':
    unittest.main()
