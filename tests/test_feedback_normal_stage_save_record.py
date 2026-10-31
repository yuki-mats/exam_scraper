"""Normal writer fixtures; only native approval verification is injected."""
import json
import sqlite3
import unittest
from unittest.mock import patch

from tests import test_feedback_prepared_stage as fixture
from tools.question_bank import feedback_prepared_stage as stage
from tools.question_bank import feedback_normal_stage_save_record as ledger


class NormalStageSaveRecordTests(unittest.TestCase):
    def setUp(self):
        self.f = f = fixture.PreparedStageTests('test_prepare_uses_normal_candidate_and_writer_preview_without_formal_save')
        f.setUp(); self.addCleanup(f.doCleanups)
        f.connection.execute("ALTER TABLE improvement_tasks ADD COLUMN status TEXT DEFAULT 'in_progress'")
        f.connection.commit()
        verifier = patch.object(stage, 'verify_independent_approval', return_value={'reviewerSessionId': 'independent-fixture'})
        verifier.start(); self.addCleanup(verifier.stop)
        f.prepare()

    def record(self):
        f = self.f
        return ledger.record(f.root, f.output/'plan.json', f.approval_path,
            coordinator=f.coordinator, connection=f.connection)

    def test_unsaved_plan_cannot_trigger_save_or_ledger_creation(self):
        with patch.object(stage, 'apply') as apply:
            with self.assertRaisesRegex(ValueError, 'existing normal-stage save receipt'): self.record()
            apply.assert_not_called()
        self.assertFalse((self.f.output/'apply-intent.json').exists())

    def test_genuine_normal_save_record_readback_retry_and_append_only(self):
        f = self.f; f.apply()
        with patch.object(stage, 'apply', side_effect=AssertionError('registration called mutation API')):
            result = self.record()
        self.assertTrue(result['formalPatchSaved']); self.assertFalse(result['publicationCompleted'])
        self.assertFalse(result['FirestoreWritten'])
        self.assertEqual(result, self.record())
        f.connection.execute("UPDATE improvement_tasks SET status='blocked'"); f.connection.commit()
        self.assertEqual(result, self.record())
        self.assertEqual(f.connection.execute(f'SELECT COUNT(*) FROM {ledger.TABLE}').fetchone()[0], 1)
        for sql in (f'DELETE FROM {ledger.TABLE}', f"UPDATE {ledger.TABLE} SET stage_id='other'"):
            with self.assertRaises(sqlite3.IntegrityError): f.connection.execute(sql)
            f.connection.rollback()

    def test_live_conflict_at_transaction_boundary_rolls_back_without_record(self):
        f = self.f; f.apply(); proof = ledger._proof; calls = []
        def change(*args, **kwargs):
            value = proof(*args, **kwargs); calls.append(1)
            if len(calls) == 1: f.snapshots['live-a']['updateTimeExact'] = '2026-10-31T00:00:00Z'
            return value
        with patch.object(ledger, '_proof', side_effect=change):
            with self.assertRaises(ValueError): self.record()
        self.assertFalse(f.connection.in_transaction)
        self.assertIsNone(f.connection.execute("SELECT name FROM sqlite_master WHERE name=?", (ledger.TABLE,)).fetchone())
        self.assertTrue((f.output/'save-receipt.json').is_file())

    def test_forged_receipt_or_native_rejection_never_records(self):
        f = self.f; f.apply(); path=f.output/'save-receipt.json'; original=path.read_bytes()
        receipt=json.loads(original); receipt['FirestoreWritten']=True; f.write(path,receipt)
        with self.assertRaises(ValueError): self.record()
        path.write_bytes(original)
        with patch.object(stage, 'verify_independent_approval', side_effect=ValueError('native rejected')):
            with self.assertRaisesRegex(ValueError, 'native rejected'): self.record()
        self.assertIsNone(f.connection.execute("SELECT name FROM sqlite_master WHERE name=?", (ledger.TABLE,)).fetchone())

    def test_same_columns_without_primary_key_are_rejected(self):
        f = self.f; f.apply()
        f.connection.execute(ledger.DDL.replace(' PRIMARY KEY', '')); f.connection.commit()
        with self.assertRaisesRegex(ValueError, 'table contract differs'): self.record()
        self.assertEqual(f.connection.execute(f'SELECT COUNT(*) FROM {ledger.TABLE}').fetchone()[0], 0)

    def test_forged_receipt_disappearing_never_starts_patch_or_intent(self):
        f = self.f; receipt=f.output/'save-receipt.json'
        f.write(receipt, {'formalPatchSaved': True, 'workVersionRecorded': True,
            'checkpointRecorded': True, 'FirestoreWritten': False, 'publicationReady': False})
        load=stage.load_plan
        def disappear(*args, **kwargs):
            receipt.unlink()
            return load(*args, **kwargs)
        with patch.object(stage, 'load_plan', side_effect=disappear), \
                patch.object(stage, 'apply', side_effect=AssertionError('mutation API forbidden')):
            with self.assertRaises((ValueError, OSError)): self.record()
        self.assertFalse((f.output/'apply-intent.json').exists())
        self.assertFalse(receipt.exists())
        self.assertEqual(list((f.root/'output/new-exam').rglob('*questionType_fixed.json')), [])

    def test_requirement_change_between_verification_and_lock_is_rejected(self):
        f = self.f; f.apply(); proof=ledger._proof; calls=[]
        def change(*args, **kwargs):
            result=proof(*args, **kwargs); calls.append(1)
            if len(calls)==1:
                f.connection.execute("UPDATE improvement_tasks SET need='変更された要件'"); f.connection.commit()
            return result
        with patch.object(ledger, '_proof', side_effect=change):
            with self.assertRaises(ValueError): self.record()
        self.assertFalse(f.connection.in_transaction)
        self.assertIsNone(f.connection.execute("SELECT name FROM sqlite_master WHERE name=?", (ledger.TABLE,)).fetchone())


if __name__ == '__main__':
    unittest.main()
