from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import copy
import subprocess
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

from tools.question_bank.feedback_daily import (
    _private_db,
    daily_summary,
    list_improvement_tasks,
    list_report_tasks,
    promote_ai_candidate,
    reconcile,
    record_decision,
    record_proposal,
    register_amendment,
    list_amendments,
    amendment_history,
    refresh_amendments,
    AMENDMENT_FIELDS,
)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class FeedbackDailyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "private" / "ledger.sqlite3"
        self.db = _private_db(self.db_path)
        self.addCleanup(self.db.close)
        self.path1 = "users/user-1/questionIssueReportSubmissions/report-1"
        self.path2 = "users/user-2/questionIssueReportSubmissions/report-2"
        self.path3 = "users/user-3/questionIssueReportSubmissions/report-3"
        self.snapshot = {
            "cases": [
                {"id": "case-a", "workflowStatus": "unreviewed"},
                {"id": "case-b", "workflowStatus": "reviewed_no_change"},
            ],
            "caseReports": {
                "case-a": [
                    {"sourceSubmissionPath": self.path1},
                    {"sourceSubmissionPath": self.path2},
                ],
                "case-b": [{"sourceSubmissionPath": self.path1}],
            },
            "receipts": [
                {"sourceSubmissionPath": self.path1, "status": "processed"},
                {"sourceSubmissionPath": self.path2, "status": "duplicate"},
            ],
            "submissions": [
                {"sourceSubmissionPath": self.path1, "reportId": "report-1",
                 "questionId": "q1", "categories": ["question_content", "other"],
                 "receivedAt": "2026-10-01T00:00:00Z"},
                {"sourceSubmissionPath": self.path2, "reportId": "report-2",
                 "questionId": "q1", "categories": ["question_content"],
                 "receivedAt": "2026-10-01T00:01:00Z"},
                {"sourceSubmissionPath": self.path3, "reportId": "report-3",
                 "questionId": "q2", "categories": ["correct_answer"],
                 "receivedAt": "2026-10-01T00:02:00Z"},
            ],
            "aiQuestions": [
                {"sourcePath": "memos/memo-1", "questionId": "q1",
                 "createdAt": "2026-10-01T00:00:00Z", "textHash": sha("why?"),
                 "visibility": "public"},
                {"sourcePath": "memos/memo-2", "questionId": "q2",
                 "createdAt": "2026-10-01T00:00:00Z", "textHash": sha("private"),
                 "visibility": "private"},
            ],
        }

    def test_each_submission_has_one_task_and_reconciliation_flags_gap(self) -> None:
        result = reconcile(self.db, self.snapshot, source="fixture")
        self.assertEqual(result["reportTasks"], 3)
        self.assertEqual(result["intakeGaps"], 1)
        self.assertEqual(result["intakeGapTaskIds"], [sha(self.path3)])
        self.assertEqual(result["reportsAwaitingDecision"], 2)
        rows = self.db.execute(
            "SELECT source_path, case_ids_json FROM report_tasks ORDER BY source_path"
        ).fetchall()
        self.assertEqual(len(rows), 3)
        self.assertEqual(json.loads(rows[0]["case_ids_json"]), ["case-a", "case-b"])
        self.assertEqual(json.loads(rows[1]["case_ids_json"]), ["case-a"])
        self.assertEqual(result["aiQuestionsAwaitingReview"], 1)
        self.assertEqual(self.db_path.stat().st_mode & 0o777, 0o600)

    def test_decisions_and_proposal_survive_rescan_and_are_individual(self) -> None:
        reconcile(self.db, self.snapshot, source="fixture")
        task1 = sha(self.path1)
        task2 = sha(self.path2)
        record_decision(
            self.db, task_id=task1, decision="fix_required",
            reason="official source differs", evidence_ref="official-pdf:page-2",
            case_id="case-a",
        )
        self.assertEqual(daily_summary(self.db)["reportsAwaitingDecision"], 2)
        record_decision(
            self.db, task_id=task1, decision="fix_required",
            reason="official source differs", evidence_ref="official-pdf:page-2",
            case_id="case-b",
        )
        self.assertEqual(daily_summary(self.db)["fixesAwaitingProposal"], 1)
        self.assertIn(task1, [row["taskId"] for row in list_report_tasks(self.db, limit=3)])
        record_proposal(
            self.db, task_id=task1, proposal_hash=sha("proposal"),
            proposal_ref="private:proposal-1",
        )
        self.snapshot["cases"][0]["workflowStatus"] = "ready_for_approval"
        result = reconcile(self.db, self.snapshot, source="fixture")
        self.assertEqual(result["patchApprovalsWaiting"], 1)
        self.assertEqual(result["fixesAwaitingProposal"], 0)
        self.assertEqual(result["reportsAwaitingDecision"], 1)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM decision_history WHERE task_id=?", (task1,)
        ).fetchone()[0], 2)
        self.assertIsNone(self.db.execute(
            "SELECT decision FROM report_tasks WHERE task_id=?", (task2,)
        ).fetchone()[0])

    def test_missing_source_and_changed_ai_text_are_not_silently_accepted(self) -> None:
        reconcile(self.db, self.snapshot, source="fixture")
        candidate_id = sha("memos/memo-1")
        need_id = promote_ai_candidate(
            self.db, candidate_id=candidate_id, title="Improve explanation",
            need="Explain why choice 2 is wrong", target="question:q1",
        )
        self.assertTrue(need_id)
        self.snapshot["submissions"] = self.snapshot["submissions"][:2]
        self.snapshot["aiQuestions"][0]["textHash"] = sha("changed")
        result = reconcile(self.db, self.snapshot, source="fixture")
        self.assertEqual(result["intakeGaps"], 1)
        self.assertEqual(result["aiQuestionsAwaitingReview"], 1)
        self.assertIsNone(self.db.execute(
            "SELECT improvement_task_id FROM ai_question_candidates WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()[0])
        self.snapshot["aiQuestions"] = []
        reconcile(self.db, self.snapshot, source="fixture")
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM ai_question_candidates"
        ).fetchone()[0], 0)
        self.assertEqual(daily_summary(self.db)["improvementTasksOpen"], 1)
        self.assertEqual(list_improvement_tasks(self.db, limit=1)[0]["task_id"], need_id)

    def test_new_counts_use_identities_when_candidate_is_replaced(self) -> None:
        first = reconcile(self.db, self.snapshot, source="fixture")
        self.assertEqual(first["newReportTasks"], 3)
        self.assertEqual(first["newAiQuestionCandidates"], 1)
        self.snapshot["aiQuestions"][0]["sourcePath"] = "memos/memo-3"
        second = reconcile(self.db, self.snapshot, source="fixture")
        self.assertEqual(second["newReportTasks"], 0)
        self.assertEqual(second["newAiQuestionCandidates"], 1)

    def test_incomplete_intake_cannot_receive_decision(self) -> None:
        reconcile(self.db, self.snapshot, source="fixture")
        with self.assertRaises(ValueError):
            record_decision(
                self.db, task_id=sha(self.path3), decision="no_change",
                reason="checked", evidence_ref="source:1",
            )
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM decision_history"
        ).fetchone()[0], 0)

    def test_multi_category_decisions_and_reopened_case_require_recheck(self) -> None:
        reconcile(self.db, self.snapshot, source="fixture")
        task_id = sha(self.path1)
        with self.assertRaises(ValueError):
            record_decision(
                self.db, task_id=task_id, decision="no_change",
                reason="checked", evidence_ref="source:1",
            )
        record_decision(
            self.db, task_id=task_id, case_id="case-a",
            decision="fix_required", reason="source differs", evidence_ref="source:1",
        )
        record_decision(
            self.db, task_id=task_id, case_id="case-b",
            decision="no_change", reason="source agrees", evidence_ref="source:2",
        )
        self.assertEqual(self.db.execute(
            "SELECT decision FROM report_tasks WHERE task_id=?", (task_id,)
        ).fetchone()[0], "mixed")
        self.snapshot["cases"][1]["workflowStatus"] = "unreviewed"
        reconcile(self.db, self.snapshot, source="fixture")
        self.assertIsNone(self.db.execute(
            "SELECT decision FROM report_tasks WHERE task_id=?", (task_id,)
        ).fetchone()[0])
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM decision_history WHERE task_id=?", (task_id,)
        ).fetchone()[0], 2)


    def amendment_fixture(self):
        # Complete the unrelated rejected intake too: CLI summary intentionally
        # returns nonzero for any actual intake gap.
        self.snapshot['receipts'].append({'sourceSubmissionPath':self.path3,'status':'rejected'})
        reconcile(self.db, self.snapshot, source='fixture')
        task = sha(self.path1)
        for case in ['case-a', 'case-b']:
            record_decision(self.db, task_id=task, case_id=case, decision='fix_required', reason='fixture decision', evidence_ref='fixture evidence')
        record_proposal(self.db, task_id=task, proposal_hash='a'*64, proposal_ref='existing-base-proposal')
        directory = Path(self.temp.name).resolve() / 'artifacts'; directory.mkdir()
        def artifact(name, value):
            path=directory/name;path.write_text(json.dumps(value,sort_keys=True));path.chmod(0o600)
            return {'ref':str(path),'hash':hashlib.sha256(path.read_bytes()).hexdigest(),'hashKind':'file_bytes_sha256'}
        base=artifact('base.json',{'approvedBase':True})
        with self.db:
            self.db.execute('UPDATE report_tasks SET patch_ref=?,commit_sha=?,published_at=? WHERE task_id=?',(base['ref'],'b'*40,'already-published',task))
        source={'sourceQuestionKey':'fixture:source','reviewQuestionId':'fixture-source','sourceRecordRef':'source.json#0'}
        proposal=artifact('proposal.json',{'sourceIdentity':source,'changes':[{'field':f,'before':None,'after':[]} for f in sorted(AMENDMENT_FIELDS)]})
        policy=artifact('policy.json',{'version':'fixture-current'})
        code=artifact('reader.py',{'fixtureCode':True})
        evidence=artifact('evidence.json',{'verifiedPrimaryFixture':True})
        binding={'schemaVersion':'approval-amendment-binding/v1','taskId':task,'caseId':'case-a',
            'base':{'patchRef':base['ref'],'commitSha':'b'*40,'patchHash':base['hash']},
            'proposal':proposal,'fieldScope':sorted(AMENDMENT_FIELDS),'inputs':[code], 'policy':policy,'evidence':[evidence],
            'contextMissing':False,'formalPlan':{'recordType':'source-bound-field-amendment/v1','sourceBinding':source,'publicationIds':['fixture-public'],
                'fieldStages':{f:{'patchRef':f'output/fixture/{f}.json','stage':'18' if f=='lawReferences' else '21'} for f in AMENDMENT_FIELDS}}}
        proof=artifact('binding.json',binding)
        args={'task_id':task,'case_id':'case-a','request_id':'fixture-request','expected_revision':0,
            'binding_ref':proof['ref'],'binding_hash':proof['hash'],'state':'pending'}
        return artifact,binding,args

    def test_amendment_pending_is_independent_and_idempotent(self):
        artifact,binding,args=self.amendment_fixture()
        before=dict(self.db.execute('SELECT * FROM report_tasks WHERE task_id=?',(args['task_id'],)).fetchone())
        identifier=register_amendment(self.db,**args)
        self.assertEqual(register_amendment(self.db,**args),identifier)
        self.assertEqual(dict(self.db.execute('SELECT * FROM report_tasks WHERE task_id=?',(args['task_id'],)).fetchone()),before)
        summary=daily_summary(self.db)
        self.assertEqual(summary['patchApprovalsWaiting'],0)
        self.assertEqual(summary['amendmentApprovalsWaiting'],1)
        self.assertEqual(summary['amendmentsByState'],{'pending':1})
        self.assertEqual(len(list_amendments(self.db)),1)
        self.assertIn(args['task_id'],[r['taskId'] for r in list_report_tasks(self.db,limit=100)])
        self.assertEqual(len(amendment_history(self.db,identifier)),1)
        changed={**args,'state':'draft'}
        with self.assertRaises(ValueError): register_amendment(self.db,**changed)
        with self.assertRaises(ValueError): register_amendment(self.db,**{**args,'request_id':'new-stale-request','expected_revision':9})

    def test_amendment_supersession_preserves_history(self):
        artifact,binding,args=self.amendment_fixture();old=register_amendment(self.db,**args)
        new=register_amendment(self.db,**{**args,'request_id':'new-request','expected_revision':1})
        self.assertNotEqual(old,new)
        self.assertEqual(list_amendments(self.db)[0]['revision'],2)
        self.assertEqual(len(list_amendments(self.db)),1)
        self.assertEqual(amendment_history(self.db,old)[-1]['to_state'],'superseded')
        self.assertEqual(register_amendment(self.db,**args),old)

    def test_amendment_binding_ref_hash_case_base_and_fields_refused(self):
        artifact,binding,args=self.amendment_fixture()
        for mutation in ['hash','case','base','scope','proposal_hash']:
            altered=copy.deepcopy(binding)
            changed=dict(args)
            if mutation=='hash': changed['binding_hash']='0'*64
            if mutation=='case': altered['caseId']='different-case'
            if mutation=='base': altered['base']['commitSha']='c'*40
            if mutation=='scope': altered['fieldScope'].append('questionText')
            if mutation=='proposal_hash': altered['proposal']['hash']='0'*64
            if mutation!='hash':
                proof=artifact(mutation+'.json',altered);changed.update(binding_ref=proof['ref'],binding_hash=proof['hash'])
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):register_amendment(self.db,**changed)
        self.assertEqual(list_amendments(self.db),[])

    def test_amendment_unready_cases_never_become_pending(self):
        artifact,binding,args=self.amendment_fixture()
        for year in ['2018','2024','2020']:
            draft=copy.deepcopy(binding);draft['contextMissing']=True;draft['formalPlan']={};draft['ready']=True
            proof=artifact(year+'.json',draft)
            with self.subTest(year=year),self.assertRaises(ValueError):
                register_amendment(self.db,**{**args,'binding_ref':proof['ref'],'binding_hash':proof['hash']})
        self.assertEqual(daily_summary(self.db)['amendmentApprovalsWaiting'],0)
        draft=copy.deepcopy(binding);draft['policy']=None;draft['inputs']=[]
        proof=artifact('asked-blocked.json',draft)
        identifier=register_amendment(self.db,**{**args,'binding_ref':proof['ref'],'binding_hash':proof['hash'],
            'state':'system_blocked','answer_status':'already_asked','asked_at':'2026-10-03T12:00:00Z'})
        summary=daily_summary(self.db)
        self.assertEqual(summary['amendmentApprovalsWaiting'],0)
        self.assertEqual(summary['amendmentResponsesWaiting'],1)
        self.assertEqual(list_amendments(self.db)[0]['state'],'system_blocked')
        self.assertIn('policy_code_revalidation_missing',list_amendments(self.db)[0]['blockers'])

    def test_amendment_rescan_reopen_and_changed_policy_stay_visible(self):
        artifact,binding,args=self.amendment_fixture();identifier=register_amendment(self.db,**args)
        reconcile(self.db,self.snapshot,source='fixture')
        self.assertEqual(list_amendments(self.db)[0]['state'],'pending')
        self.snapshot['cases'][0]['workflowStatus']='published';reconcile(self.db,self.snapshot,source='fixture')
        self.snapshot['cases'][0]['workflowStatus']='unreviewed';reconcile(self.db,self.snapshot,source='fixture')
        self.assertEqual(list_amendments(self.db)[0]['state'],'stale')
        parent=self.db.execute('SELECT patch_ref,commit_sha,decision FROM report_tasks WHERE task_id=?',(args['task_id'],)).fetchone()
        self.assertEqual(tuple(parent),(binding['base']['patchRef'],'b'*40,'fix_required'))
        self.assertEqual(amendment_history(self.db,identifier)[-1]['reason'],'case_reopened')
        self.assertEqual(daily_summary(self.db)['amendmentApprovalsWaiting'],0)
        self.assertIn(args['task_id'],[r['taskId'] for r in list_report_tasks(self.db,limit=100)])
        # Re-establishing case decision/rescanning never revives the old amendment.
        record_decision(self.db,task_id=args['task_id'],case_id='case-a',decision='fix_required',reason='new review',evidence_ref='new proof')
        reconcile(self.db,self.snapshot,source='fixture');self.assertEqual(list_amendments(self.db)[0]['state'],'stale')

    def test_amendment_input_drift_and_migration_preserve_base_and_proposal(self):
        artifact,binding,args=self.amendment_fixture();identifier=register_amendment(self.db,**args)
        before=dict(self.db.execute('SELECT * FROM report_tasks WHERE task_id=?',(args['task_id'],)).fetchone())
        # Simulate a legacy database without the additive migration tables.
        with self.db:
            self.db.execute('DROP TABLE amendment_history');self.db.execute('DROP TABLE proposal_amendments');self.db.execute('DROP TABLE report_task_cases')
        reopened=_private_db(self.db_path);reopened.close();reopened=_private_db(self.db_path)
        self.assertEqual(dict(reopened.execute('SELECT * FROM report_tasks WHERE task_id=?',(args['task_id'],)).fetchone()),before)
        identifier=register_amendment(reopened,**args)
        Path(binding['policy']['ref']).write_text('{"changed":true}')
        refresh_amendments(reopened)
        self.assertEqual(list_amendments(reopened)[0]['state'],'stale')
        self.assertEqual(amendment_history(reopened,identifier)[-1]['reason'],'fixed_input_or_policy_changed')
        self.assertEqual(dict(reopened.execute('SELECT * FROM report_tasks WHERE task_id=?',(args['task_id'],)).fetchone()),before)
        reopened.close()

    def test_amendment_report_input_and_base_reference_changes_are_stale(self):
        artifact,binding,args=self.amendment_fixture();identifier=register_amendment(self.db,**args)
        self.snapshot['submissions'][0]['questionId']='changed-fixture-question'
        reconcile(self.db,self.snapshot,source='fixture')
        self.assertEqual(list_amendments(self.db)[0]['state'],'stale')
        self.assertEqual(amendment_history(self.db,identifier)[-1]['reason'],'report_input_or_case_binding_changed')
        # A new explicit revision is required. Changing the parent commit cannot
        # silently rebase the old binding or make it ready again.
        with self.db:self.db.execute('UPDATE report_tasks SET commit_sha=? WHERE task_id=?',('c'*40,args['task_id']))
        with self.assertRaises(ValueError):register_amendment(self.db,**args)
        self.assertEqual(len(list_amendments(self.db)),1)

    def test_amendment_canonical_hash_contract_and_content_conflict(self):
        artifact,binding,args=self.amendment_fixture()
        canonical=hashlib.sha256(json.dumps(binding,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        amended={**args,'binding_hash':canonical,'binding_hash_kind':'canonical_json_sha256'}
        identifier=register_amendment(self.db,**amended)
        self.assertEqual(register_amendment(self.db,**amended),identifier)
        altered=copy.deepcopy(binding);altered['policy']=None
        proof=artifact('changed-content.json',altered)
        with self.assertRaises(ValueError):
            register_amendment(self.db,**{**args,'binding_ref':proof['ref'],'binding_hash':proof['hash'],'state':'system_blocked'})

    def test_amendment_real_cli_register_summary_list_and_refusal(self):
        artifact,binding,args=self.amendment_fixture()
        cli=[sys.executable,'-m','tools.question_bank.feedback_daily','--db',str(self.db_path)]
        def run(*arguments,success=True):
            started=datetime.now(timezone.utc).isoformat()
            result=subprocess.run([*cli,*arguments],capture_output=True,text=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
            print(json.dumps({'fixtureCli':{'command':arguments[0],'startedAt':started,
                'finishedAt':datetime.now(timezone.utc).isoformat(),'exitCode':result.returncode,
                'bindingHash':arguments[arguments.index('--binding-hash')+1] if '--binding-hash' in arguments else args['binding_hash'],
                'stdoutHash':sha(result.stdout),'stderrHash':sha(result.stderr)}},sort_keys=True))
            self.assertEqual(result.returncode,0 if success else 1,result.stderr)
            if success:
                self.assertNotIn('source_path',result.stdout);self.assertNotIn('reporter',result.stdout)
                return json.loads(result.stdout)
        command=['amend-register',args['task_id'],'--case-id','case-a','--request-id','fixture-request','--expected-revision','0',
            '--binding-ref',args['binding_ref'],'--binding-hash',args['binding_hash'],'--state','pending']
        first=run(*command);self.assertEqual(run(*command)['amendmentId'],first['amendmentId'])
        self.assertEqual(run('summary')['amendmentApprovalsWaiting'],1)
        self.assertEqual(len(run('amend-list')['amendments']),1)
        self.assertEqual(len(run('amend-history',first['amendmentId'])['history']),1)
        run(*[x if x!='case-a' else 'wrong-case' for x in command],success=False)
        self.assertEqual(run('amend-check')['amendmentApprovalsWaiting'],1)
        blocked=copy.deepcopy(binding);blocked['policy']=None;blocked['inputs']=[]
        proof=artifact('cli-blocked-binding.json',blocked)
        result=run('amend-register',args['task_id'],'--case-id','case-a','--request-id','blocked-explicit-revision',
            '--expected-revision','1','--binding-ref',proof['ref'],'--binding-hash',proof['hash'],
            '--state','system_blocked','--answer-status','already_asked','--asked-at','2026-10-03T12:00:00Z')
        summary=run('summary');self.assertEqual(summary['amendmentApprovalsWaiting'],0);self.assertEqual(summary['amendmentResponsesWaiting'],1)
        latest=run('amend-list')['amendments'];self.assertEqual(len(latest),1)
        self.assertEqual(latest[0]['state'],'system_blocked');self.assertEqual(latest[0]['answerStatus'],'already_asked')


if __name__ == "__main__":
    unittest.main()
