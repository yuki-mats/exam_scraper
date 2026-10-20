from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from tools.question_bank import feedback_verified_save as saver
from tools.question_review_console.scoped_artifacts import write_json
from scripts.pipeline import rebuild_gas_shunin_all_year_artifacts as gas


class VerifiedSaveTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.connection=sqlite3.connect(':memory:'); self.connection.row_factory=sqlite3.Row
        self.addCleanup(self.connection.close)
        self.connection.executescript('''CREATE TABLE improvement_tasks(task_id,title,need,target,status);
            CREATE TABLE ai_question_candidates(candidate_id,question_id,text_hash,review_status,improvement_task_id);
            INSERT INTO improvement_tasks VALUES ('task','定義','違いを説明','display','review_pending');
            INSERT INTO ai_question_candidates VALUES ('candidate','display','hash','promoted','task');''')
        self.doc=dict(questionId='display',originalQuestionId='original',originalQuestionBodyText='本文',
            originalQuestionChoiceText='肢',qualificationId='gas-shunin-otsu',examYear=2021,
            questionType='true_false',isChoiceOnly=False,correctChoiceText='正しい',
            explanationText='正しい。旧説明。',suggestedQuestions=[],suggestedQuestionDetails=[])
        self.sibling={**self.doc,'questionId':'sibling','originalQuestionChoiceText':'別の肢'}
        def ref(path,value):
            path=self.root/path; write_json(path,value)
            return {'ref':str(path),'sha256':saver.file_hash(path)}
        self.ref=ref
        qpdf=self.root/'question.pdf'; qpdf.write_bytes(b'question')
        apdf=self.root/'answer.pdf'; apdf.write_bytes(b'answer')
        self.index=self.root/'index.json'; self.verify=self.root/'verify.json'
        ref(Path('index.json'),dict(summary=dict(holdCount=0,contentRepairRequiredCount=0),
            records=[dict(questionId=q,officialQuestionPdf=dict(path='question.pdf',sha256=saver.file_hash(qpdf)),
                officialAnswerPdf=dict(path='answer.pdf',sha256=saver.file_hash(apdf))) for q in ('display','sibling')]))
        ref(Path('verify.json'),dict(result='pass',questionIndex=dict(holdCount=0,currentPdfHashMismatchCount=0),answerIndex=dict(currentPdfHashMismatchCount=0)))
        evidence=gas.verify_official_pdf_evidence(document_index=self.index,pdf_verification=self.verify,root=self.root)
        self.formal=self.root/'output/gas-shunin-otsu/questions_json/2021/25_verified_publication/2021_verified_publication.json'
        ref(self.formal.relative_to(self.root),dict(schemaVersion='gas-shunin-verified-publication/v1',
            qualificationId='gas-shunin-otsu',list_group_id='2021',total_count=2,questions=[self.doc,self.sibling],evidence=evidence))
        self.before=self.formal.read_bytes()
        self.snapshots={d['questionId']:dict(questionId=d['questionId'],exists=True,
            updateTimeExact='2026-10-13T00:00:00.123456789Z',fields=deepcopy(d)) for d in (self.doc,self.sibling)}
        baseline=ref(Path('baseline.json'),self.snapshots['display'])
        baseline['updateTimeExact']=self.snapshots['display']['updateTimeExact']
        diffs=[dict(field='explanationText',beforePresent=True,before=self.doc['explanationText'],after='正しい。違いを具体化した説明。')]
        private=ref(Path('patch.json'),dict(schemaVersion='feedback-improvement-private-patch/v1',taskId='task',questionId='display',
            fieldDiffs=diffs,precondition=dict(updateTimeExact=baseline['updateTimeExact'],baselineSha256=baseline['sha256']),
            approvalReady=False,formalPatchSaved=False,FirestoreWritten=False,executablePatch=False,fullDocumentUploadAllowed=False))
        self.result=self.root/'result.json'
        ref(Path('result.json'),dict(schemaVersion='feedback-improvement-result/v1',taskId='task',status='review_pending',
            approvalReady=False,formalPatchSaved=False,FirestoreWritten=False,
            proposedUpdates=[dict(questionId='display',baseline=baseline,preparedPatch=private,fieldDiffs=diffs)],
            primaryEvidence=[{'ref':str(qpdf),'sha256':saver.file_hash(qpdf)}]))
        self.destination=self.root/saver.PRIVATE/'run'
        self.mock_live=patch.object(saver,'_snapshots',side_effect=self.selected_snapshots); self.mock_live.start(); self.addCleanup(self.mock_live.stop)
        self.plan=saver.prepare(self.root,self.result,self.formal,self.destination,
            document_index=self.index,pdf_verification=self.verify,connection=self.connection)
        self.path=self.destination/'formal-save-plan.json'
        self.approval={'native':'test fixture only'}
        self.binding={'nativeVerified':'test fixture only'}
        self.mock_approval=patch.object(saver,'verify_independent_approval',return_value=self.binding); self.mock_approval.start(); self.addCleanup(self.mock_approval.stop)

    def save(self,hook=None):
        return saver.save(self.root,self.path,self.approval,connection=self.connection,failure_hook=hook)

    def test_scope_matches_terminal_lf_in_actual_native_exec_format(self):
        from tests.test_native_independent_approval import native_audit
        from tools.question_review_console.independent_approval import verify_independent_approval
        scope=saver.approval_scope(self.root,self.path,self.plan)
        record=native_audit(self.root,scope)
        parent=Path(record['creation']['sessionPath'])
        lines=parent.read_bytes().splitlines(keepends=True)
        call=json.loads(lines[2]); call['payload']['input']=call['payload']['input'].rstrip('\n')+'\n'
        lines[2]=(json.dumps(call,ensure_ascii=False)+'\n').encode(); parent.write_bytes(b''.join(lines))
        self.assertEqual(verify_independent_approval(record,scope,production=False)['reviewerSessionId'],'reviewer')

    @unittest.skipUnless(shutil.which('node'), 'Node is required to verify native creation JS')
    def test_native_creation_keeps_marker_after_yield_and_rejects_failed_command(self):
        code=saver.approval_scope(self.root,self.path,self.plan)['nativeCreationCall']
        script='const chunks=JSON.parse(process.argv[1]);const tools={exec_command:async()=>chunks.shift(),write_stdin:async()=>chunks.shift()};const text=s=>process.stdout.write(s);(async()=>{'+code+'})().catch(e=>{process.stderr.write(e.message);process.exitCode=1;});'
        success=subprocess.run(['node','-e',script,json.dumps([
            {'output':'','session_id':7},{'output':'','session_id':7},
            {'output':'NATIVE_CANDIDATE_SCOPE=fixed\n','exit_code':0}])],capture_output=True,text=True)
        self.assertEqual(success.returncode,0,success.stderr)
        self.assertEqual(success.stdout,'NATIVE_CANDIDATE_SCOPE=fixed\n')
        failed=subprocess.run(['node','-e',script,json.dumps([
            {'output':'','session_id':7},{'output':'invalid candidate','exit_code':1}])],capture_output=True,text=True)
        self.assertNotEqual(failed.returncode,0)
        self.assertEqual(failed.stdout,'')

    def selected_snapshots(self,ids,fields,**kwargs):
        return {i:{**deepcopy(self.snapshots[i]),'fields':{k:deepcopy(v) for k,v in self.snapshots[i]['fields'].items() if k in fields}} for i in ids}

    def rewrite_plan(self,change):
        p=json.loads(self.path.read_bytes()); change(p)
        p['planHash']=saver.sha256_json({k:v for k,v in p.items() if k!='planHash'})
        write_json(self.path,p)

    def test_success_idempotency_immutable_receipt_and_no_network_write(self):
        receipt=self.save(); self.assertFalse(receipt['FirestoreWritten'])
        self.assertEqual(receipt['schemaVersion'],saver.RECEIPT)
        after=json.loads(self.formal.read_bytes()); before=json.loads(self.before)
        self.assertEqual(after['questions'][1],before['questions'][1])
        self.assertEqual(after['questions'][0]['correctChoiceText'],before['questions'][0]['correctChoiceText'])
        stamp=(self.formal.stat().st_mtime_ns,(self.destination/'save-receipt.json').stat().st_mtime_ns)
        self.snapshots['display']['updateTimeExact']='changed after publication'
        self.assertEqual(self.save(),receipt)
        self.assertEqual(stamp,(self.formal.stat().st_mtime_ns,(self.destination/'save-receipt.json').stat().st_mtime_ns))

    def test_real_verifier_rejects_asserted_approval_without_touching_canonical(self):
        self.mock_approval.stop()
        with self.assertRaisesRegex(ValueError,'approval evidence'):
            self.save()
        self.assertEqual(self.formal.read_bytes(),self.before)

    def test_native_binding_is_always_production(self):
        self.save()
        self.assertTrue(all(c.kwargs['production'] is True for c in self.mock_approval.target.verify_independent_approval.call_args_list))

    def test_task_and_sibling_live_race_rejected(self):
        self.connection.execute("UPDATE improvement_tasks SET need='different'"); self.connection.commit()
        with self.assertRaisesRegex(ValueError,'task requirement'):
            self.save()
        self.assertEqual(self.formal.read_bytes(),self.before)
        self.connection.execute("UPDATE improvement_tasks SET need='違いを説明'"); self.connection.commit()
        self.snapshots['sibling']['updateTimeExact']='new'
        with self.assertRaisesRegex(ValueError,'live precondition'):
            self.save()
        self.assertEqual(self.formal.read_bytes(),self.before)

    def test_inputs_and_candidate_tampering_rejected(self):
        (self.root/'question.pdf').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'input changed'):
            self.save()
        self.assertEqual(self.formal.read_bytes(),self.before)

    def test_narrowing_sibling_mask_or_id_scope_rejected_even_rehashed(self):
        self.rewrite_plan(lambda p:p['snapshotFields'].remove('explanationText'))
        with self.assertRaisesRegex(ValueError,'snapshot mask'):
            self.save()
        self.assertEqual(self.formal.read_bytes(),self.before)

    def test_before_conflict_and_competing_writer_never_overwritten(self):
        self.formal.write_bytes(b'competitor')
        with self.assertRaisesRegex(ValueError,'before conflict'):
            self.save()
        self.assertEqual(self.formal.read_bytes(),b'competitor')

    def test_displaced_conflicting_value_preserved(self):
        original=saver._rename_exclusive
        def race(source,target):
            if Path(source)==self.formal:
                self.formal.write_bytes(b'competitor between check and move')
            original(source,target)
        with patch.object(saver,'_rename_exclusive',side_effect=race):
            with self.assertRaisesRegex(ValueError,'moved canonical conflict'):
                self.save()
        self.assertEqual(self.formal.read_bytes(),b'competitor between check and move')

    def test_missing_canonical_during_interruption_resumes_exclusively(self):
        def fail(stage):
            if stage=='original_moved': raise RuntimeError('interrupted')
        with self.assertRaisesRegex(RuntimeError,'interrupted'): self.save(fail)
        self.assertFalse(self.formal.exists())
        self.save(); self.assertTrue(self.formal.exists())

    def test_after_save_interruption_recovers_receipt_without_second_write(self):
        def fail(stage):
            if stage=='candidate_saved': raise RuntimeError('interrupted')
        with self.assertRaises(RuntimeError): self.save(fail)
        stamp=self.formal.stat().st_mtime_ns
        self.save(); self.assertEqual(stamp,self.formal.stat().st_mtime_ns)

    def test_competitor_after_original_move_is_held(self):
        def race(stage):
            if stage=='original_moved': self.formal.write_bytes(b'new third value')
        with self.assertRaisesRegex(ValueError,'third value'): self.save(race)
        self.assertEqual(self.formal.read_bytes(),b'new third value')
        original=self.formal.parent/('.feedback-save-'+self.plan['planHash'])/'original.json'
        self.assertEqual(original.read_bytes(),self.before)

    def test_backup_corruption_and_different_approval_resend_refused(self):
        receipt=self.save()
        with self.assertRaisesRegex(ValueError,'different approval'):
            saver.save(self.root,self.path,{'different':True},connection=self.connection)
        Path(receipt['originalRef']).write_bytes(b'bad backup')
        with self.assertRaisesRegex(ValueError,'receipt differs'): self.save()

    def test_restore_and_restore_third_value_rejection(self):
        self.save()
        self.formal.write_bytes(b'third value')
        with self.assertRaisesRegex(ValueError,'third value'):
            saver.restore_original(self.root,self.path,self.approval,connection=self.connection)
        self.assertEqual(self.formal.read_bytes(),b'third value')
        self.formal.write_bytes(Path(self.plan['candidateRef']).read_bytes())
        saver.restore_original(self.root,self.path,self.approval,connection=self.connection)
        self.assertEqual(self.formal.read_bytes(),self.before)
        with self.assertRaises(ValueError): self.save()

    def test_bootstrap_never_overwrites_existing_or_recovering_canonical(self):
        with patch.object(gas,'ROOT_DIR',self.root):
            with self.assertRaisesRegex(ValueError,'上書き'): gas.save_initial_canonical(self.formal,{})
            self.formal.unlink(); (self.formal.parent/'.feedback-save-something').mkdir()
            with self.assertRaisesRegex(ValueError,'上書き'): gas.save_initial_canonical(self.formal,{})

    def test_prepare_detects_outside_scope_update_during_live_read(self):
        def race(ids,fields,**kwargs):
            value=json.loads(self.formal.read_bytes()); value['sourcePolicy']='competing change'
            write_json(self.formal,value)
            return self.selected_snapshots(ids,fields)
        with patch.object(saver,'_snapshots',side_effect=race):
            with self.assertRaisesRegex(ValueError,'changed during preparation'):
                saver.prepare(self.root,self.result,self.formal,self.root/saver.PRIVATE/'competing-run',
                    document_index=self.index,pdf_verification=self.verify,connection=self.connection)
        self.assertEqual(json.loads(self.formal.read_bytes())['sourcePolicy'],'competing change')

    def test_removing_required_evidence_input_even_with_new_plan_hash_fails(self):
        self.rewrite_plan(lambda p:p['inputs'].pop(str(self.root/'question.pdf')))
        with self.assertRaisesRegex(ValueError,'required evidence'):
            saver.read_plan(self.root,self.path)

    def test_incorrect_before_copy_hash_is_not_a_valid_plan(self):
        self.rewrite_plan(lambda p:p.update(beforeSha256='f'*64))
        with self.assertRaisesRegex(ValueError,'material changed'):
            saver.read_plan(self.root,self.path)

    def test_restore_interruption_resumes_and_resend_is_read_only(self):
        self.save(); exclusive=saver._exclusive
        def interrupt(path,data):
            if path.name=='restored.json': raise OSError('interrupted before restore receipt')
            return exclusive(path,data)
        with patch.object(saver,'_exclusive',side_effect=interrupt):
            with self.assertRaises(OSError):
                saver.restore_original(self.root,self.path,self.approval,connection=self.connection)
        self.assertEqual(self.formal.read_bytes(),self.before)
        saver.restore_original(self.root,self.path,self.approval,connection=self.connection)
        stamp=self.formal.stat().st_mtime_ns
        saver.restore_original(self.root,self.path,self.approval,connection=self.connection)
        self.assertEqual(self.formal.stat().st_mtime_ns,stamp)

    def test_partial_intent_or_receipt_fails_closed_preserving_bytes(self):
        directory=self.formal.parent/('.feedback-save-'+self.plan['planHash']); directory.mkdir()
        (directory/'intent.json').write_bytes(b'{')
        with self.assertRaises(ValueError): self.save()
        self.assertEqual(self.formal.read_bytes(),self.before)

    def test_legal_scope_requires_three_distinct_native_reviewers_before_final(self):
        p=deepcopy(self.plan); p['lawReviewRequired']=True
        with self.assertRaisesRegex(ValueError,'three-stage'):
            saver.approval_scope(self.root,self.path,p)
        for stage in saver.LAW_STAGES:
            write_json(self.destination/f'law-{stage}-approval.json',{'stage':stage})
        with patch.object(saver,'verify_independent_approval',side_effect=lambda r,s,production:{'reviewerSessionId':r['stage']}):
            scope=saver.approval_scope(self.root,self.path,p)
            self.assertEqual([r['stage'] for r in scope['priorLawReviews']],list(saver.LAW_STAGES))
            primary=saver.approval_scope(self.root,self.path,p,'primary')
            self.assertEqual(primary['priorLawReviews'],[])
            secondary=saver.approval_scope(self.root,self.path,p,'secondary')
            self.assertEqual(len(secondary['priorLawReviews']),1)
        with patch.object(saver,'verify_independent_approval',return_value={'reviewerSessionId':'same reviewer'}):
            with self.assertRaisesRegex(ValueError,'independent law stage'):
                saver.approval_scope(self.root,self.path,p)

    def test_changed_audited_scope_is_rejected_before_first_move(self):
        scope=saver.approval_scope(self.root,self.path,self.plan)
        with patch.object(saver,'approval_scope',side_effect=[scope,{**scope,'priorLawReviews':['new evidence']}]):
            with self.assertRaisesRegex(ValueError,'scope changed'):
                self.save()
        self.assertEqual(self.formal.read_bytes(),self.before)

    def test_exclusive_receipt_failure_has_no_partial_visible_json(self):
        rename=saver._rename_exclusive
        def fail(source,target):
            if Path(target).name=='save-receipt.json': raise OSError('interrupted before receipt rename')
            return rename(source,target)
        with patch.object(saver,'_rename_exclusive',side_effect=fail):
            with self.assertRaises(OSError): self.save()
        self.assertFalse((self.destination/'save-receipt.json').exists())
        self.assertEqual(saver.file_hash(self.formal),self.plan['candidateSha256'])
        self.save()

    def test_task_status_changed_is_not_a_save_approval(self):
        self.connection.execute("UPDATE improvement_tasks SET status='blocked'"); self.connection.commit()
        with self.assertRaisesRegex(ValueError,'review-pending task'):
            self.save()
        self.assertEqual(self.formal.read_bytes(),self.before)

    def test_evidence_change_during_network_read_is_rejected_before_candidate_save(self):
        calls=0
        def race(ids,fields,**kwargs):
            nonlocal calls
            calls+=1
            if calls==2: (self.root/'question.pdf').write_bytes(b'changed during second live read')
            return self.selected_snapshots(ids,fields)
        with patch.object(saver,'_snapshots',side_effect=race):
            with self.assertRaisesRegex(ValueError,'input changed'):
                self.save()
        self.assertFalse(self.formal.exists())
        self.assertEqual((self.formal.parent/('.feedback-save-'+self.plan['planHash'])/'original.json').read_bytes(),self.before)
        self.assertFalse((self.destination/'save-receipt.json').exists())

    def test_law_scope_change_during_network_read_is_rejected_before_candidate_save(self):
        calls=0; changed=False
        original=saver.approval_scope
        def race(ids,fields,**kwargs):
            nonlocal calls,changed
            calls+=1
            if calls==2: changed=True
            return self.selected_snapshots(ids,fields)
        def scope(root,path,plan,stage='final'):
            value=original(root,path,plan,stage)
            return {**value,'priorLawReviews':['changed native law review binding']} if changed else value
        with patch.object(saver,'_snapshots',side_effect=race), patch.object(saver,'approval_scope',side_effect=scope):
            with self.assertRaisesRegex(ValueError,'scope changed'):
                self.save()
        self.assertFalse(self.formal.exists())
        self.assertEqual((self.formal.parent/('.feedback-save-'+self.plan['planHash'])/'original.json').read_bytes(),self.before)

    def test_qualified_publication_unit_is_fixed_in_plan_scope_and_saved_receipt(self):
        expected={'qualificationId':'gas-shunin-otsu','examYear':2021,'originalQuestionId':'original'}
        self.assertEqual(self.plan['publicationUnit'],expected)
        self.assertEqual(self.plan['membershipMode'],saver.QUALIFIED_MEMBERSHIP)
        self.assertEqual(saver.approval_scope(self.root,self.path,self.plan)['publicationUnit'],expected)
        receipt=self.save()
        self.assertEqual(receipt['publicationUnit'],expected)
        self.assertEqual(receipt['membershipMode'],saver.QUALIFIED_MEMBERSHIP)
        receipt['publicationUnit']['qualificationId']='gas-shunin-kou'
        write_json(self.destination/'save-receipt.json',receipt)
        with self.assertRaisesRegex(ValueError,'qualified publication unit'):
            saver.load_saved(self.root,self.path,self.destination/'save-receipt.json',connection=self.connection)

    def test_changed_or_incomplete_qualified_publication_unit_is_refused(self):
        for key,value in [('examYear',True),('qualificationId','gas-shunin-kou'),('originalQuestionId','another')]:
            self.rewrite_plan(lambda p:p['publicationUnit'].update({key:value}))
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'qualified publication unit'):
                saver.read_plan(self.root,self.path,verify_law=False,connection=self.connection)
            write_json(self.path,self.plan)
        for keys in [('membershipMode',),('publicationUnit',),('membershipMode','publicationUnit')]:
            self.rewrite_plan(lambda p:[p.pop(key) for key in keys])
            with self.subTest(removed=keys),self.assertRaisesRegex(ValueError,'qualified publication unit'):
                saver.read_plan(self.root,self.path,verify_law=False,connection=self.connection)
            write_json(self.path,self.plan)

    def test_production_snapshot_identity_is_metadata_and_membership_is_checked(self):
        snapshot=lambda i:SimpleNamespace(id=i,exists=True,
            update_time=SimpleNamespace(rfc3339=lambda:'2026-10-13T00:00:00.123456789Z'),
            to_dict=lambda:{'originalQuestionId':'original','isDeleted':False,'isChoiceOnly':False})
        class Database:
            def __init__(db): db.ids=['display','sibling']; db.selected=None
            def collection(db,name): return db
            def document(db,i): return i
            def get_all(db,refs,field_paths):
                self.assertNotIn('questionId',field_paths)
                return [snapshot(i) for i in refs]
            def where(db,**kwargs): return db
            def select(db,fields): db.selected=fields; return db
            def stream(db): return [snapshot(i) for i in db.ids]
        db=Database()
        self.mock_live.stop()
        with patch.object(saver,'FirestoreReadback',return_value=SimpleNamespace(_database=lambda:db)):
            result=saver._snapshots(['display','sibling'],['originalQuestionId','isDeleted','isChoiceOnly'])
            self.assertEqual(result['display']['questionId'],'display')
            self.assertNotIn('questionId',result['display']['fields'])
            self.assertEqual(db.selected,['isDeleted','isChoiceOnly'])
            db.ids.append('new-sibling')
            with self.assertRaisesRegex(ValueError,'membership changed'):
                saver._snapshots(['display','sibling'],['originalQuestionId','isDeleted','isChoiceOnly'])


if __name__=='__main__': unittest.main()
