from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.question_bank.feedback_daily import (
    _private_db, reconcile, promote_ai_candidate, list_improvement_tasks, record_improvement_publication,
)
from tools.question_bank.feedback_publication_result import verify_improvement_publication
from tools.question_review_console.formal_correction_save import independent_approval_scope, save_formal_units
from tools.question_review_console.firestore_readback import PRODUCTION_PROJECT_ID
from tools.question_review_console.projection import sha256_json
from tools.question_bank.feedback_task_requirements import task_requirement_binding
from tests.test_native_independent_approval import native_audit
from tests.test_question_review_formal_correction_save import fixture_plan, approvals


class FeedbackPublicationResultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.plan_path, self.plan = fixture_plan(self.root)
        self.identity = 'fixture-2020-0'
        self.db = _private_db(self.root/'private/ledger.sqlite3'); self.addCleanup(self.db.close)
        reconcile(self.db, {'submissions': [], 'receipts': [], 'cases': [], 'caseReports': {}, 'aiQuestions': [dict(sourcePath='memos/fixture', questionId=self.identity, createdAt='2026-10-01T00:00:00Z', textHash=hashlib.sha256(b'fixture learning need').hexdigest(), visibility='public')]}, source='fixture')
        cid = self.db.execute('SELECT candidate_id FROM ai_question_candidates').fetchone()[0]
        self.task_id = promote_ai_candidate(self.db, candidate_id=cid, title='fix typo', need='fix typo', target=self.identity)
        self.requirement = task_requirement_binding(self.db,self.task_id)
        self.plan['feedbackTasks'] = {self.task_id:{**self.requirement,'satisfiedByUnitHashes':[self.plan['units'][2]['unitHash']]}}
        self.plan['planHash'] = sha256_json({k:v for k,v in self.plan.items() if k!='planHash'})
        self.plan_path.write_text(json.dumps(self.plan))
        mocked_root = patch('tools.question_review_console.independent_approval.NATIVE_SESSION_ROOT',self.root)
        mocked_root.start(); self.addCleanup(mocked_root.stop)
        _, law = approvals(self.root, self.plan)
        evidence = native_audit(self.root, independent_approval_scope(self.plan, self.plan_path, root=self.root))
        self.receipt = self.root / 'save.json'
        save_formal_units(self.root, self.plan_path, subagent_approval=evidence, law_recheck=law, receipt_path=self.receipt)
        unit = self.plan['units'][2]
        public = json.loads((self.root / unit['files'][2]['path']).read_bytes())
        self.identity = 'fixture-2020-0'
        self.doc = public['candidate']['projection'][0]
        self.upload = {**self.doc, 'questionId': self.identity, 'writeFields': ['questionText']}
        self.directory = self.root / 'output/question_review_console/publish_runs/fixture/run'
        self.directory.mkdir(parents=True)
        self.readback = {'projectId': PRODUCTION_PROJECT_ID, 'status': 'match', 'documentCount': 1,
            'missingDocumentIds': [], 'differenceCount': 0, 'sourceUnchanged': True, 'sourceHash': 'source',
            'documents': [{'questionId': self.identity, 'status': 'match', 'differences': [], 'live': self.doc}]}
        self.files = {
            'manifest.json': {'schemaVersion': 'question-publish-run/v1', 'runId': 'run', 'status': 'succeeded', 'projectId': PRODUCTION_PROJECT_ID,
                'documentIds': [self.identity], 'evaluationHash': 'evaluation', 'candidateHash': sha256_json([self.upload]), 'sourceHash': 'source'},
            'preflight.json': {'projectId': PRODUCTION_PROJECT_ID, 'canPublish': True, 'publishReady': True, 'evaluationStatus': 'passed',
                'evaluationHash': 'evaluation', 'missingCount': 0, 'failedDeltaPaths': [], 'documentCount': 1,
                'candidateHash': sha256_json([self.upload]), 'sourceHash': 'source', 'changedCount': 1},
            'artifact.json': {'questions': [self.upload]},
            'readback.json': self.readback,
            'result.json': {'schemaVersion': 'question-publish-result/v1', 'runId': 'run', 'status': 'succeeded', 'publishedCount': 1, 'documentCount': 1, 'readback': self.readback}}
        self.artifact_path = self.root/'completion.json'
        self.rebuild()

    def bind(self, path):
        return {'path': str(path.relative_to(self.root)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

    def rebuild(self):
        for name, value in self.files.items(): (self.directory/name).write_text(json.dumps(value))
        self.artifact = {'schemaVersion': 'feedback-improvement-publication/v1', 'taskId': self.task_id,
            'formalPlan': self.bind(self.plan_path), 'formalReceipt': self.bind(self.receipt),
            'publicationFiles': {name: self.bind(self.directory/name) for name in self.files}}
        self.artifact_path.write_text(json.dumps(self.artifact))
        self.artifact_hash = hashlib.sha256(self.artifact_path.read_bytes()).hexdigest()

    def reader(self, ids, *, fields): return {self.identity: deepcopy(self.doc)}

    def record(self, **kwargs):
        return record_improvement_publication(self.db, task_id=self.task_id, expected_status=kwargs.pop('expected_status','open'), reason='verified fixture',
            artifact_ref=self.artifact_path, artifact_sha256=self.artifact_hash, repo_root=self.root, live_reader=kwargs.pop('live_reader',self.reader), **kwargs)

    def test_complete_after_verified_write_readback_and_idempotent_resend(self):
        first = self.record(); self.assertEqual(first, self.record())
        self.assertEqual(self.db.execute('SELECT status FROM improvement_tasks').fetchone()[0], 'completed')
        self.assertEqual(list_improvement_tasks(self.db, limit=100), [])
        self.assertEqual(self.db.execute('SELECT count(*) FROM improvement_result_history').fetchone()[0], 1)

    def test_reject_gates_masks_values_counts_and_task_mismatches(self):
        original = deepcopy(self.files)
        mutations = [
            lambda f: f['preflight.json'].update(publishReady=False),
            lambda f: f['preflight.json'].update(missingCount=1),
            lambda f: f['preflight.json'].update(evaluationHash='wrong'),
            lambda f: f['manifest.json'].update(projectId='other'),
            lambda f: f['result.json'].update(status='failed'),
            lambda f: f['artifact.json']['questions'][0].update(writeFields=['questionText','unknownField']),
            lambda f: f['artifact.json']['questions'][0].update(questionText='unapproved'),
            lambda f: f['readback.json'].update(sourceUnchanged=False),
            lambda f: f['result.json'].update(documentCount=2),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.files = deepcopy(original); mutate(self.files); self.rebuild()
                with self.assertRaises(ValueError): self.record()
                self.assertEqual(self.db.execute('SELECT status FROM improvement_tasks').fetchone()[0], 'open')
        self.files=original; self.rebuild()
        with self.assertRaises(ValueError): verify_improvement_publication(self.root,self.artifact,task_id=self.task_id,source_question_ids={'other'},task_requirement=self.requirement,live_reader=self.reader)
        with self.assertRaises(ValueError): verify_improvement_publication(self.root,self.artifact,task_id=self.task_id,source_question_ids={self.identity,'other-independent-question'},task_requirement=self.requirement,live_reader=self.reader)

    def test_reject_fresh_mismatch_absence_conflict_read_failure_and_status_race(self):
        for live in ({}, {self.identity:{**self.doc,'questionText':'concurrent'}}, {self.identity:{**self.doc,'listGroupId':None}}, {self.identity:{**self.doc,'unknownField':{'changed':1}}}):
            with self.assertRaises(ValueError): self.record(live_reader=lambda ids,fields:live)
        def failed(ids,fields): raise RuntimeError('readback failed')
        with self.assertRaises(RuntimeError): self.record(live_reader=failed)
        self.db.execute("UPDATE improvement_tasks SET status='blocked'"); self.db.commit()
        with self.assertRaises(ValueError): self.record()
        self.assertEqual(self.db.execute('SELECT count(*) FROM improvement_result_history').fetchone()[0],0)

    def test_reject_evidence_tampering_and_drift_during_fresh_readback(self):
        (self.directory/'result.json').write_text('{}')
        with self.assertRaises(ValueError): self.record()
        self.rebuild()
        def changed(ids,fields):
            (self.directory/'readback.json').write_text('{}')
            return self.reader(ids,fields=fields)
        with self.assertRaises(ValueError): self.record(live_reader=changed)

    def test_reject_source_link_added_or_removed_by_another_connection(self):
        other = _private_db(self.root/'private/ledger.sqlite3'); self.addCleanup(other.close)
        for remove in (False, True):
            def concurrent(ids, fields):
                with other:
                    if remove: other.execute('UPDATE ai_question_candidates SET improvement_task_id=NULL WHERE source_path=?', ('memos/fixture',))
                    else:
                        row = dict(other.execute('SELECT * FROM ai_question_candidates WHERE source_path=?', ('memos/fixture',)).fetchone())
                        row.update(candidate_id='new-source',source_path='memos/new',question_id='other-independent-question')
                        columns=','.join(row); placeholders=','.join('?' for _ in row)
                        other.execute(f'INSERT INTO ai_question_candidates ({columns}) VALUES ({placeholders})',tuple(row.values()))
                return self.reader(ids,fields=fields)
            with self.assertRaisesRegex(ValueError,'sources changed'): self.record(live_reader=concurrent)
            self.assertEqual(self.db.execute('SELECT status FROM improvement_tasks').fetchone()[0],'open')
            self.assertEqual(self.db.execute('SELECT count(*) FROM improvement_result_history').fetchone()[0],0)
            with other:
                other.execute("DELETE FROM ai_question_candidates WHERE candidate_id='new-source'")
                other.execute('UPDATE ai_question_candidates SET improvement_task_id=? WHERE source_path=?', (self.task_id,'memos/fixture'))

    def test_task_purpose_and_definition_change_cannot_reuse_other_approval(self):
        with self.db: self.db.execute("UPDATE improvement_tasks SET need='improve explanation'")
        with self.assertRaisesRegex(ValueError,'fulfillment'): self.record()
        with self.db: self.db.execute("UPDATE improvement_tasks SET need='fix typo'")
        other = _private_db(self.root/'private/ledger.sqlite3'); self.addCleanup(other.close)
        def changed(ids,fields):
            with other: other.execute("UPDATE improvement_tasks SET target='changed requirement'")
            return self.reader(ids,fields=fields)
        with self.assertRaisesRegex(ValueError,'requirement changed'): self.record(live_reader=changed)

    def test_multiple_required_units_cannot_be_completed_by_one_run(self):
        # A newly audited plan requiring A+B must not accept only B's publication.
        self.plan['feedbackTasks'][self.task_id]['satisfiedByUnitHashes'].append(self.plan['units'][0]['unitHash'])
        self.plan['planHash']=sha256_json({k:v for k,v in self.plan.items() if k!='planHash'})
        self.plan_path.write_text(json.dumps(self.plan))
        # Bind a genuine separate fixture audit and save receipt for that plan.
        _,law=approvals(self.root,self.plan)
        audit=native_audit(self.root,independent_approval_scope(self.plan,self.plan_path,root=self.root))
        self.receipt=self.root/'new-save.json'
        save_formal_units(self.root,self.plan_path,subagent_approval=audit,law_recheck=law,receipt_path=self.receipt)
        self.rebuild()
        with self.assertRaisesRegex(ValueError,'fulfillment'): self.record()
