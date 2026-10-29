"""Question-level read-set boundaries only; never production/save success."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.common.question_identity import load_source_record_inventory
from scripts.common.scoped_canonical_context import reader_dependencies
from tools.question_bank import feedback_question_level_read_set as level


class QuestionLevelReadSetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Path(__file__).resolve().parents[1]
        cls.dependencies = reader_dependencies(cls.repo, [cls.repo / 'tools/question_bank/feedback_question_level_read_set.py'])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        files = []
        for source in self.dependencies:
            target = self.root / source.relative_to(self.repo)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            files.append(target)
        dependency_patch = patch.object(level, 'reader_dependencies', return_value=files)
        dependency_patch.start(); self.addCleanup(dependency_patch.stop)
        self.connection = sqlite3.connect(':memory:'); self.connection.row_factory = sqlite3.Row
        self.addCleanup(self.connection.close)
        self.connection.executescript("""
            CREATE TABLE improvement_tasks(task_id,title,need,target);
            CREATE TABLE ai_question_candidates(candidate_id,question_id,text_hash,review_status,improvement_task_id);
            INSERT INTO improvement_tasks VALUES ('task','比較する','違いを説明する','main');
            INSERT INTO ai_question_candidates VALUES ('candidate','main','hash','promoted','task');
        """)
        self.private = self.root / level.PRIVATE / 'fixture'
        self.source = self.root / 'output/qualification/questions_json/group/00_source/source.json'
        self.record = {'public_question_id': 'main', 'questionBodyText': '正しい組合せを選べ。',
            'examYear': 2020, 'questionType': 'flash_card', 'choiceTextList': ['ア正・イ誤', 'ア誤・イ正'],
            'correctChoiceText': ['正しい', '間違い']}
        source_ref = self.write(self.source, {'question_bodies': [self.record]})
        self.manifest = self.root / level.MANIFEST
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        self.manifest.write_text(json.dumps({'path': str(self.source.relative_to(self.root)),
            'sha256': source_ref['sha256']}) + '\n')
        binding = load_source_record_inventory(self.source.parent, qualification='qualification', list_group_id='group')[0].identity.binding.as_mapping()
        self.snapshots = {}
        for qid, choice, flag in [('main', 'ア正・イ誤', False), ('wrong', 'ア誤・イ正', True)]:
            self.snapshots[qid] = {'questionId': qid, 'exists': True,
                'updateTimeExact': '2026-10-29T00:00:00.123456789Z', 'fields': {
                    'qualificationId': 'qualification', 'examYear': 2020, 'originalQuestionId': 'main',
                    'isDeleted': False, 'isChoiceOnly': flag, 'questionType': 'flash_card',
                    'originalQuestionBodyText': self.record['questionBodyText'],
                    'originalQuestionChoiceText': choice, 'explanationText': '元の説明'}}
        self.candidate = {'schemaVersion': 'feedback-improvement-content-candidate/v1', 'taskId': 'task',
            'source': {'sourceFile': str(self.source), 'sourceSha256': source_ref['sha256'], 'binding': binding},
            'baseline': self.write(self.private / 'baseline.json', self.snapshots),
            'publicationUnit': {'qualificationId': 'qualification', 'examYear': 2020, 'originalQuestionId': 'main'},
            'primaryEvidence': [self.write(self.private / 'official.pdf', b'%PDF-fixture')],
            'allObservedSiblingIds': ['main', 'wrong'], 'proposedUpdates': [{'questionId': 'main',
                'updateTimeExact': self.snapshots['main']['updateTimeExact'], 'fieldDiffs': [{
                    'field': 'explanationText', 'beforePresent': True, 'before': '元の説明', 'after': '比較した説明'}]}],
            'approvalReady': False, 'formalPatchSaved': False, 'FirestoreWritten': False, 'publicationReady': False}
        self.candidate_path = self.private / 'candidate.json'; self.save_candidate()
        self.destination = self.private / 'context'

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else level._bytes(value))
        return {'ref': str(path), 'sha256': level._hash(path.read_bytes())}

    def save_candidate(self):
        self.candidate_ref = self.write(self.candidate_path, self.candidate)

    def prepare(self, reader=None):
        return level.prepare(self.root, self.candidate_ref, self.destination, connection=self.connection,
            _reader=reader or (lambda unit, fields: deepcopy(self.snapshots)))

    def test_full_read_set_includes_choice_only_and_retransmits_without_writes(self):
        source_bytes = self.source.read_bytes(); db_before = list(self.connection.iterdump())
        receipt = self.prepare(); self.assertEqual(receipt, self.prepare())
        self.assertEqual(receipt, level.inspect(self.root, self.destination, connection=self.connection))
        context = json.loads((self.destination / 'context.json').read_text())
        self.assertEqual(set(context['readSet']), {'main', 'wrong'})
        self.assertEqual(context['proposedWriteDocumentIds'], ['main'])
        self.assertTrue(context['readSet']['wrong']['fields']['isChoiceOnly'])
        self.assertNotIn('sources', context['task'])
        for key in level.FLAGS: self.assertIs(context[key], False); self.assertIs(receipt[key], False)
        self.assertEqual(self.source.read_bytes(), source_bytes)
        self.assertEqual(list(self.connection.iterdump()), db_before)
        self.assertEqual((self.destination / 'context.json').stat().st_mode & 0o777, 0o600)

    def test_rejects_missing_extra_or_duplicate_choice_correspondence(self):
        for change in ('missing', 'extra', 'duplicate'):
            with self.subTest(change=change):
                snapshots = deepcopy(self.snapshots)
                if change == 'missing': del snapshots['wrong']
                elif change == 'extra': snapshots['other'] = deepcopy(snapshots['wrong'])
                else: snapshots['wrong']['fields']['originalQuestionChoiceText'] = 'ア正・イ誤'
                with self.assertRaises(ValueError): self.prepare(lambda unit, fields: snapshots)
                self.assertFalse(self.destination.exists())

    def test_rejects_foreign_unit_stale_time_and_presence_drift(self):
        for key, value in [('qualificationId', 'other'), ('examYear', 2021), ('isChoiceOnly', None),
                ('originalQuestionBodyText', '別の問題の本文')]:
            with self.subTest(key=key):
                snapshots = deepcopy(self.snapshots); snapshots['wrong']['fields'][key] = value
                with self.assertRaises(ValueError): self.prepare(lambda unit, fields: snapshots)
        snapshots = deepcopy(self.snapshots); snapshots['wrong']['updateTimeExact'] = '2026-10-29T00:01:00Z'
        with self.assertRaisesRegex(ValueError, 'baseline'): self.prepare(lambda unit, fields: snapshots)
        snapshots = deepcopy(self.snapshots); del snapshots['main']['fields']['explanationText']
        with self.assertRaises(ValueError): self.prepare(lambda unit, fields: snapshots)

    def test_rejects_unowned_fields_noops_and_forged_success(self):
        original = deepcopy(self.candidate)
        for mutation in ('unowned', 'noop', 'approved'):
            self.candidate = deepcopy(original)
            if mutation == 'unowned': self.candidate['proposedUpdates'][0]['fieldDiffs'][0]['field'] = 'correctChoiceText'
            elif mutation == 'noop': self.candidate['proposedUpdates'][0]['fieldDiffs'][0]['after'] = '元の説明'
            else: self.candidate['formalPatchSaved'] = True
            self.save_candidate()
            with self.assertRaises(ValueError): self.prepare()

    def test_rejects_source_task_or_artifact_changes(self):
        self.prepare()
        self.connection.execute("update improvement_tasks set need='異なる目的'")
        with self.assertRaisesRegex(ValueError, 'drift'): level.inspect(self.root, self.destination, connection=self.connection)
        self.connection.execute("update improvement_tasks set need='違いを説明する'")
        self.source.write_bytes(self.source.read_bytes() + b' ')
        with self.assertRaises(ValueError): level.inspect(self.root, self.destination, connection=self.connection)

    def test_rejects_foreign_task_sources_unsafe_masks_and_path_unit(self):
        self.connection.execute("update ai_question_candidates set question_id='foreign'")
        with self.assertRaisesRegex(ValueError, 'outside'): self.prepare()
        self.connection.execute("update ai_question_candidates set question_id='main'")
        unsafe = deepcopy(self.snapshots); unsafe['main']['fields']['createdById'] = 'fictional-id'
        self.candidate['baseline'] = self.write(self.private / 'unsafe.json', unsafe); self.save_candidate()
        with self.assertRaisesRegex(ValueError, 'mask'): self.prepare()
        with self.assertRaises(ValueError): level._unit({'qualificationId': '../qualification', 'examYear': 2020, 'originalQuestionId': 'main'})

    def test_rejects_inflight_change_and_partial_or_competing_destination(self):
        def changed(unit, fields):
            self.candidate_path.write_bytes(self.candidate_path.read_bytes() + b' ')
            return deepcopy(self.snapshots)
        with self.assertRaisesRegex(ValueError, 'drift'): self.prepare(changed)
        self.assertFalse(self.destination.exists())
        self.save_candidate(); self.destination.mkdir()
        (self.destination / 'context.json').write_bytes(b'partial')
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual((self.destination / 'context.json').read_bytes(), b'partial')

    def test_rejects_symlink_and_implementation_drift(self):
        link = self.private / 'link'; link.symlink_to(self.private, target_is_directory=True)
        with self.assertRaises(ValueError): level.prepare(self.root, self.candidate_ref, link / 'out', connection=self.connection)
        self.prepare()
        implementation = self.root / 'tools/question_bank/feedback_question_level_read_set.py'
        implementation.write_bytes(implementation.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'drift'): level.inspect(self.root, self.destination, connection=self.connection)

    def test_rejects_inventory_aba_and_invalid_record_location(self):
        real = load_source_record_inventory(self.source.parent, qualification='qualification', list_group_id='group')[0]
        forged = deepcopy(self.record); forged['questionBodyText'] = '再読時だけ差し替わった本文'
        snapshots = deepcopy(self.snapshots)
        for snapshot in snapshots.values(): snapshot['fields']['originalQuestionBodyText'] = forged['questionBodyText']
        self.candidate['baseline'] = self.write(self.private / 'other-baseline.json', snapshots)
        self.save_candidate()
        for entry in (replace(real, record=forged), replace(real, record_index=99),
                replace(real, path=self.source.parent / 'unread.json')):
            with self.subTest(entry=entry.record_index), patch.object(level, 'load_source_record_inventory', return_value=(entry,)):
                with self.assertRaisesRegex(ValueError, 'captured source bytes'):
                    self.prepare(lambda unit, fields: snapshots)
        self.assertFalse(self.destination.exists())


if __name__ == '__main__':
    unittest.main()
