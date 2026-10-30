"""The new owner-specific stage contract keeps the legacy contract unchanged."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tools.question_bank import feedback_prepared_stage as stage
from tools.question_bank import feedback_question_level_read_set as level
from tests import test_feedback_prepared_stage as fixture


class QuestionLevelPreparedTypeTests(unittest.TestCase):
    def setUp(self):
        self.f = f = fixture.PreparedStageTests('test_prepare_uses_normal_candidate_and_writer_preview_without_formal_save')
        f.setUp(); self.addCleanup(f.doCleanups)
        group = f.inventory.group('new-exam', '2026'); question = group['questions'][0]
        self.qid = question['id']; source = Path(f.request['source']['ref'])
        self.ui_id = 'opaque-normal-ui-id'
        question['id'] = self.ui_id
        f.request['questionId'] = self.ui_id
        raw = json.loads(source.read_bytes())['question_bodies'][0]
        raw.update(public_question_id=self.qid, examYear=2026, questionType='flash_card',
            questionBodyText='甲と乙の正しい組合せを選べ。', choiceTextList=['甲正・乙誤', '甲誤・乙正'],
            correctChoiceText=['正しい', '間違い'])
        for key in ('source', 'projected'): question[key].update(deepcopy(raw))
        f.inventory.group = lambda qualification, group_id: deepcopy(group)
        f.write(source, {'question_bodies': [raw]})
        f.write(f.root / stage.MANIFEST, (json.dumps({'path': str(source.relative_to(f.root)),
            'sha256': stage._hash(source.read_bytes())}) + '\n').encode())
        f.snapshots = {}
        for qid, choice, flag in [(self.qid, '甲正・乙誤', False), ('opaque-other-id', '甲誤・乙正', True)]:
            f.snapshots[qid] = {'questionId': qid, 'exists': True, 'updateTimeExact': '2026-10-30T00:00:00.123456789Z',
                'fields': {'qualificationId': 'new-exam', 'examYear': 2026, 'originalQuestionId': self.qid,
                    'isDeleted': False, 'isChoiceOnly': flag, 'questionType': 'flash_card',
                    'originalQuestionBodyText': raw['questionBodyText'], 'originalQuestionChoiceText': choice,
                    'explanationText': '元の説明'}}
        f.connection.execute('UPDATE ai_question_candidates SET question_id=?', (self.qid,)); f.connection.commit()
        def ref(path, value):
            f.write(path, value); return {'ref': str(path), 'sha256': stage._hash(path.read_bytes())}
        self.candidate_path = f.private / 'content-candidate.json'
        candidate = {'schemaVersion': 'feedback-improvement-content-candidate/v1', 'taskId': 'task',
            'source': {'sourceFile': str(source), 'sourceSha256': stage._hash(source.read_bytes()), 'binding': f.request['source']['binding']},
            'baseline': ref(f.private / 'baseline.json', f.snapshots),
            'publicationUnit': {'qualificationId': 'new-exam', 'examYear': 2026, 'originalQuestionId': self.qid},
            'primaryEvidence': deepcopy(f.request['primaryEvidence']), 'allObservedSiblingIds': sorted(f.snapshots),
            'proposedUpdates': [{'questionId': self.qid, 'updateTimeExact': f.snapshots[self.qid]['updateTimeExact'],
                'fieldDiffs': [{'field': 'explanationText', 'beforePresent': True, 'before': '元の説明', 'after': '比較した説明'}]}],
            'approvalReady': False, 'formalPatchSaved': False, 'FirestoreWritten': False, 'publicationReady': False}
        self.candidate_ref = ref(self.candidate_path, candidate)
        self.context_dir = f.private / 'read-set'
        # Cache dependency selection only, as the legacy stage fixture does.
        # Every actual dependency byte is still re-read and hash-checked.
        dependencies = level.reader_dependencies(f.root, [f.root / 'tools/question_bank/feedback_question_level_read_set.py'])
        dependency = patch.object(level, 'reader_dependencies', return_value=dependencies)
        dependency.start(); self.addCleanup(dependency.stop)
        reader = patch.object(level, '_production_reader', side_effect=lambda *_: deepcopy(f.snapshots))
        reader.start(); self.addCleanup(reader.stop)
        self.reader_receipt = level.prepare(f.root, self.candidate_ref, self.context_dir, connection=f.connection)
        f.request.update(schemaVersion=stage.QUESTION_LEVEL_REQUEST,
            source={'ref': str(source), 'sha256': stage._hash(source.read_bytes()), 'binding': f.request['source']['binding']},
            questionLevelReadSet=ref(self.context_dir / 'receipt.json', self.reader_receipt),
            live={'qualificationId': 'new-exam', 'examYear': 2026, 'originalQuestionId': self.qid,
                'questionIds': sorted(f.snapshots), 'readFields': sorted({k for s in f.snapshots.values() for k in s['fields']})})
        f.write(f.request_path, f.request)

    def test_full_choice_only_read_set_reaches_normal_type_preview_with_dedicated_schema(self):
        f = self.f; original = Path(f.request['source']['ref']).read_bytes()
        receipt = f.prepare(); plan = f.plan()
        self.assertEqual(receipt['schemaVersion'], stage.QUESTION_LEVEL_PREVIEW)
        self.assertEqual(plan['schemaVersion'], stage.QUESTION_LEVEL_PLAN)
        self.assertEqual(plan['request']['questionId'], self.ui_id)
        self.assertNotEqual(self.ui_id, self.qid)
        self.assertTrue(plan['liveSnapshots']['opaque-other-id']['fields']['isChoiceOnly'])
        self.assertTrue(plan['liveMembershipPredicate']['includeChoiceOnly'])
        self.assertEqual(plan['liveMembershipPredicate']['examYear'], 2026)
        scope = stage.approval_scope(f.root, f.output / 'plan.json', coordinator=f.coordinator, connection=f.connection)
        self.assertEqual(scope['schemaVersion'], 'feedback-prepared-question-level-type-independent-scope/v1')
        self.assertEqual(scope['questionLevelReadSet'], f.request['questionLevelReadSet'])
        self.assertFalse(plan['sourceToLiveMappingApproved']); self.assertFalse(receipt['formalPatchSaved'])
        self.assertEqual(Path(f.request['source']['ref']).read_bytes(), original)
        self.assertEqual(len(f.model.aggregate_review_calls), 2)
        with self.assertRaises(ValueError): f.apply()

    def test_new_contract_rejects_other_stages_missing_receipt_and_downgrade(self):
        f = self.f; original = deepcopy(f.request)
        for change in ('stage', 'missing', 'downgrade', 'year', 'source', 'ui'):
            f.request = deepcopy(original)
            if change == 'stage': f.request['stageId'] = 'explanation'
            if change == 'missing': f.request.pop('questionLevelReadSet')
            if change == 'downgrade': f.request['schemaVersion'] = stage.REQUEST
            if change == 'year': f.request['live']['examYear'] = 2025
            if change == 'source': f.request['source']['binding']['reviewQuestionId'] = 'foreign'
            if change == 'ui': f.request['questionId'] = 'foreign-ui-id'
            f.write(f.request_path, f.request)
            with self.subTest(change=change), self.assertRaises(ValueError): f.prepare()
        self.assertEqual(f.model.calls, [])

    def test_fresh_membership_time_presence_and_requirement_conflicts_block(self):
        f = self.f
        for change in ('missing', 'extra', 'time', 'presence'):
            snapshots = deepcopy(f.snapshots)
            if change == 'missing': snapshots.pop('opaque-other-id')
            if change == 'extra': snapshots['new-sibling'] = deepcopy(snapshots['opaque-other-id'])
            if change == 'time': snapshots['opaque-other-id']['updateTimeExact'] = '2026-10-30T00:01:00Z'
            if change == 'presence': snapshots[self.qid]['fields'].pop('explanationText')
            with patch.object(level, '_production_reader', return_value=snapshots), self.subTest(change=change):
                with self.assertRaisesRegex(ValueError, 'read-set'): f.prepare()
        f.connection.execute("UPDATE improvement_tasks SET need='新しい要件'")
        with self.assertRaises(ValueError): f.prepare()
        self.assertEqual(f.model.calls, [])

    def test_normal_save_revalidates_choice_only_and_retry_cannot_hide_partial_intent(self):
        f = self.f; f.prepare()
        with patch.object(stage, 'verify_independent_approval', return_value={'reviewerSessionId': 'independent-fixture'}):
            saved = f.apply(); self.assertEqual(saved['schemaVersion'], stage.QUESTION_LEVEL_SAVED)
            self.assertFalse(saved['publicationReady']); self.assertFalse(saved['sourceToLiveMappingApproved'])
            self.assertEqual(saved, f.apply())
            f.snapshots['opaque-other-id']['updateTimeExact'] = '2026-10-30T00:01:00Z'
            with self.assertRaisesRegex(ValueError, 'read-set'): f.apply()

    def test_test_reader_receipt_or_changed_receipt_never_reaches_model(self):
        f = self.f
        injected = f.private / 'injected-read-set'
        level.prepare(f.root, self.candidate_ref, injected, connection=f.connection,
            _reader=lambda *_: deepcopy(f.snapshots))
        f.request['questionLevelReadSet'] = {'ref': str(injected / 'receipt.json'),
            'sha256': stage._hash((injected / 'receipt.json').read_bytes())}
        f.write(f.request_path, f.request)
        with self.assertRaisesRegex(ValueError, 'verified question-level'): f.prepare()
        f.request['questionLevelReadSet'] = {'ref': str(self.context_dir / 'receipt.json'), 'sha256': '0' * 64}
        f.write(f.request_path, f.request)
        with self.assertRaises(ValueError): f.prepare()
        self.assertEqual(f.model.calls, [])

    def test_interrupted_new_apply_preserves_intent_and_never_reapplies(self):
        f = self.f; f.prepare()
        with patch.object(stage, 'verify_independent_approval', return_value={'reviewerSessionId': 'independent-fixture'}):
            with patch.object(stage, '_run', side_effect=RuntimeError('interrupted')) as writer:
                with self.assertRaises(RuntimeError): f.apply()
                self.assertTrue((f.output / 'apply-intent.json').is_file())
                with self.assertRaisesRegex(ValueError, 'incomplete'): f.apply()
                self.assertEqual(writer.call_count, 1)
        self.assertFalse((f.output / 'save-receipt.json').exists())

    def test_choice_only_conflict_during_generation_never_issues_preview(self):
        f = self.f; run = stage._run
        def changed(*args, **kwargs):
            output = run(*args, **kwargs)
            f.snapshots['opaque-other-id']['updateTimeExact'] = '2026-10-30T00:02:00Z'
            return output
        with patch.object(stage, '_run', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'read-set'): f.prepare()
        self.assertFalse((f.output / 'plan.json').exists())

    def test_context_aba_after_inspect_cannot_replace_the_fixed_read_set(self):
        f = self.f; path = self.context_dir / 'context.json'; original = path.read_bytes()
        context = json.loads(original)
        context['readSet']['opaque-other-id']['updateTimeExact'] = '2026-10-30T00:02:00Z'
        forged = level._bytes(context); inspect = level.inspect
        def swap(root, destination, **kwargs):
            path.write_bytes(original)
            result = inspect(root, destination, **kwargs)
            path.write_bytes(forged)
            return result
        with patch.object(level, 'inspect', side_effect=swap):
            with self.assertRaisesRegex(ValueError, 'captured context hash'): f.prepare()
        self.assertEqual(f.model.calls, [])

    def test_default_cli_tuple_connection_is_supported_without_changing_its_factory(self):
        f = self.f
        f.connection.row_factory = None
        receipt = f.prepare()
        self.assertEqual(receipt['schemaVersion'], stage.QUESTION_LEVEL_PREVIEW)
        self.assertEqual(f.plan()['schemaVersion'], stage.QUESTION_LEVEL_PLAN)
        self.assertIsNone(f.connection.row_factory)


if __name__ == '__main__':
    unittest.main()
