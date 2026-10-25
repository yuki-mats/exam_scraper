"""Safe task delivery, actual normal turns, and independent fulfillment checks."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.question_review_console import feedback_task_input as task
from tools.question_review_console import qualification_runs as runs
from tools.question_review_console import law_audit_verification as audit
from tools.question_review_console.question_candidate import CandidateTarget, CandidateUpdate, QuestionCandidate, validate_candidate_content
from tools.question_bank import feedback_prepared_stage as stage
from tools.question_bank import feedback_source_tf_delivery as publication
from tests import test_feedback_prepared_stage as normal_fixture
from tests import test_official_guidance_pipeline as guidance_fixture


class TaskInputTests(unittest.TestCase):
    def setUp(self):
        self.binding = {'requirement': {'schemaVersion': 'feedback-task-requirement/v1', 'taskId': 'task',
            'definition': {'title': '説明改善', 'need': '原則と例外の理由を説明する。', 'target': 'opaque'},
            'sources': [{'candidate_id': 'private', 'question_id': 'public-b', 'text_hash': 'private-hash', 'review_status': 'promoted'}]}}
        self.binding['requirementHash'] = task._hash(self.binding['requirement'])
        self.record = {'questionBodyText': '正しいものはどれか。', 'choiceTextList': ['第一の完全命題。', '第二の完全命題。']}
        self.target = {'id': 'normal', 'listGroupId': 'group', 'sourceQuestionKey': 'exam:group:normal',
            'reviewQuestionId': 'normal', 'sourceRecordRef': 'question.json#0'}
        self.scope = task.scope_for('exam', 'explanation', self.target, input_fingerprint='input', projected_input_hash='projection')
        self.snapshots = {'public-b': {'fields': {'originalQuestionChoiceText': self.record['choiceTextList'][0]}},
            'public-a': {'fields': {'originalQuestionChoiceText': self.record['choiceTextList'][1]}}}
        self.statements = task.target_statements(self.binding, self.record, self.snapshots,
            original_question_id='original', normal_question_id='normal')
        self.value = task.build(self.binding, self.scope, self.statements)

    def prompt(self, stage_id='explanation'):
        value = deepcopy(self.value); value['scope']['stageId'] = stage_id
        value['inputHash'] = task._hash({k:v for k,v in value.items() if k != 'inputHash'})
        target = CandidateTarget('explanation', 'explanation', '21_explanationText_added/q.json', ('explanationText',))
        prompt = runs._structured_candidate_prompt('通常policy', [self.target], stage_id=stage_id,
            records_by_question={'normal': self.record}, candidate_targets_by_question={'normal': (target,)},
            feedback_by_question={}, feedback_task_requirements_by_question={'normal': value})
        delivery = {'inputHash': value['inputHash'], 'requirementHash': value['requirementHash'],
            'promptHash': hashlib.sha256(prompt.encode()).hexdigest(), 'threadId': 'native-thread', 'turnId': 'native-turn',
            'inputFingerprint': 'input', 'projectedInputHash': 'projection'}
        return prompt, value, delivery

    def test_actual_safe_prompt_maps_target_by_full_text_and_never_copies_source_rows(self):
        self.assertEqual(self.statements[0]['questionId'], 'public-b')
        self.assertEqual(self.statements[0]['choiceIndex'], 0)
        for owner in ('explanation', 'law_audit'):
            prompt, value, delivery = self.prompt(owner)
            task.verify_delivery(value, prompt, delivery, binding=self.binding, scope=value['scope'])
            question = audit.question_inputs_from_prompt(prompt)['normal']
            self.assertEqual(question['feedbackTaskRequirement'], value)
            self.assertNotIn('sources', value)
            self.assertNotIn('private-hash', prompt)
            self.assertNotIn('candidate_id', prompt)

    def test_canonical_scope_shape_actual_prompt_and_native_turn_refuse_foreign_or_missing_inputs(self):
        prompt, value, delivery = self.prompt()
        for mutation in ('definition', 'task', 'question', 'stage', 'projection', 'source', 'raw', 'source_row', 'hash'):
            changed = deepcopy(value)
            if mutation == 'definition': changed['definition']['need'] = '改変要件'
            if mutation == 'task': changed['taskId'] = 'foreign'
            if mutation == 'question': changed['scope']['questionId'] = 'foreign'
            if mutation == 'stage': changed['scope']['stageId'] = 'law_audit'
            if mutation == 'projection': changed['scope']['projectedInputHash'] = 'foreign'
            if mutation == 'source': changed['scope']['sourceBinding']['sourceRecordRef'] = 'foreign.json#0'
            if mutation == 'raw': changed['rawUserText'] = 'not allowed'
            if mutation == 'source_row': changed['definition']['sources'] = []
            if mutation == 'hash': changed['requirementHash'] = 'b' * 64
            changed['inputHash'] = task._hash({k:v for k,v in changed.items() if k != 'inputHash'})
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                task.validate(changed, binding=self.binding, scope=self.scope)
        stale = deepcopy(self.binding); stale['requirement']['definition']['need'] = '新しい要件'
        stale['requirementHash'] = task._hash(stale['requirement'])
        with self.assertRaises(ValueError): task.validate(value, binding=stale)
        for key in delivery:
            bad = deepcopy(delivery); bad[key] = ''
            with self.subTest(key=key), self.assertRaises(ValueError):
                task.verify_delivery(value, prompt, bad, binding=self.binding, scope=self.scope)
        for bad_prompt in (prompt + '\n改変', prompt.replace('feedbackTaskRequirement"', 'missingRequirement"')):
            with self.assertRaises(ValueError): task.verify_delivery(value, bad_prompt, delivery, binding=self.binding, scope=self.scope)

    def test_duplicate_missing_foreign_target_statement_and_raw_binding_refused(self):
        for kind in ('duplicate', 'missing', 'foreign'):
            snapshots = deepcopy(self.snapshots)
            if kind == 'duplicate': snapshots['public-a'] = deepcopy(snapshots['public-b'])
            if kind == 'missing': snapshots.pop('public-a')
            if kind == 'foreign': snapshots['public-a']['fields']['originalQuestionChoiceText'] = '別問'
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                task.target_statements(self.binding, self.record, snapshots, original_question_id='original', normal_question_id='normal')
        binding = deepcopy(self.binding); binding['requirement']['rawMessage'] = 'forbidden'
        binding['requirementHash'] = task._hash(binding['requirement'])
        with self.assertRaises(ValueError): task.build(binding, self.scope, self.statements)

    def test_independent_03b_replays_same_requirement_and_refuses_unfulfilled_approval(self):
        prompt, value, _ = self.prompt('law_audit')
        question = audit.question_inputs_from_prompt(prompt)['normal']
        fields = {'isLawRelated': True, 'auditStatus': 'same_as_current', 'correctChoiceText': ['正しい', '間違い'],
            'examTimeDecision': ['正しい', '間違い'], 'currentLawDecision': ['正しい', '間違い'],
            'lawRevisionFacts': [{'auditStatus':'same_as_current','examTime':{'correctChoiceText':v},'current':{'correctChoiceText':v}}
                for v in ('正しい','間違い')]}
        candidate = QuestionCandidate('normal','candidate','独立に審査する。',(CandidateUpdate('audit', fields, ()),))
        bundle = audit.verification_bundle(question,candidate)
        reply = {'schemaVersion':audit.SCHEMA_VERSION,'questionId':'normal','evidenceHash':bundle['evidenceHash'],
            'decision':'approve','examTimeDecision':fields['examTimeDecision'],'currentLawDecision':fields['currentLawDecision'],
            'issues':[],'verifiedSourceUrls':['https://example.com/official'],
            'feedbackTaskRequirementInputHash':value['inputHash'],'taskRequirementSatisfied':True}
        self.assertEqual(audit.parse_review(json.dumps(reply),bundle)['decision'],'approve')
        restored=json.loads(json.dumps(bundle,sort_keys=True))
        self.assertEqual(audit.review_prompt(bundle,'二次'),audit.review_prompt(restored,'二次'))
        execution = {'phase':'secondary','threadId':'review','turnId':'turn','executionId':'review:turn', 'review':reply,
            'feedbackTaskRequirementInputHash':value['inputHash'],
            'promptHash':hashlib.sha256(audit.review_prompt(bundle,'二次').encode()).hexdigest()}
        plan = {'request':{'questionId':'normal'},'stagePlan':{'feedbackTaskRequirements':{'normal':value}},
            'preparedCandidate':{'content':{'executionMetadata':{'lawAuditVerification':{'normal':{
                'evidenceHash':bundle['evidenceHash'],'bundle':bundle,'reviews':[execution]}}}}}}
        child = {'threadId':'primary','lawAuditReviewExecutions':[execution]}
        self.assertEqual(publication._normal_law_verification(plan,child)['reviewCount'],1)
        for key in ('taskRequirementSatisfied','feedbackTaskRequirementInputHash'):
            bad=deepcopy(reply);bad[key]=False if key=='taskRequirementSatisfied' else 'b'*64
            with self.assertRaises(ValueError):audit.parse_review(json.dumps(bad),bundle)
        for key in ('feedbackTaskRequirementInputHash','promptHash','threadId','turnId'):
            bad_plan,bad_child=deepcopy(plan),deepcopy(child)
            bad_plan['preparedCandidate']['content']['executionMetadata']['lawAuditVerification']['normal']['reviews'][0][key]=''
            bad_child['lawAuditReviewExecutions'][0][key]=''
            with self.subTest(key=key),self.assertRaises((ValueError,publication.GateRequired)):
                publication._normal_law_verification(bad_plan,bad_child)
        bad_plan=deepcopy(plan);bad_plan['stagePlan']['feedbackTaskRequirements']['normal']['definition']['need']='別要件'
        with self.assertRaises(ValueError):publication._normal_law_verification(bad_plan,child)


class TaskNormalSaveTests(unittest.TestCase):
    def test_actual_explanation_turn_save_replay_and_stale_task_rejection(self):
        f = normal_fixture.PreparedStageTests();f.setUp();self.addCleanup(f.doCleanups)
        from tools.question_review_console.inventory import QuestionInventory
        reader=QuestionInventory(f.root)
        for method in ('projected_input','projected_input_for_stage','source_input','_projection_stage_maps','_projection_issue_index'):
            setattr(f.inventory,method,getattr(reader,method))
        original_group=f.inventory.group
        def group(*args):
            value=original_group(*args);question=value['questions'][0]
            projected=reader.projected_input(args[0],args[1],question['sourceRecordRef'])
            question['projected']=dict(projected.record);question['paths']['patches']=list(projected.applied_files)
            return value
        f.inventory.group=group
        source = json.loads(__import__('pathlib').Path(f.request['source']['ref']).read_text())['question_bodies'][0]
        choices = source['choiceTextList']
        self.assertEqual(len(choices),1)
        f.request['live']['questionIds']=['live-a'];f.snapshots={'live-a':f.snapshots['live-a']}
        f.snapshots['live-a']['fields']['originalQuestionChoiceText']=choices[0]
        f.request['stageId']='explanation';f.write(f.request_path,f.request)
        original=f.model.run_turn
        captured=[]
        def model(prompt,**kwargs):
            result=original(prompt,**kwargs)
            if kwargs['work_type']!='maintenance_explanation_candidate':return result
            q=audit.question_inputs_from_prompt(prompt)[f.request['questionId']];captured.append(q['feedbackTaskRequirement'])
            payload=json.loads(result.final_message)
            for key,val in {'questionLearningPatternId':'terms_basics','explanationReferences':[],
                    'explanationText':['正しい。対象の定義を説明する。']}.items():
                payload['update']['setFields']=[x for x in payload['update']['setFields'] if x['field']!=key]+[{'field':key,'value':val}]
                payload['update']['unsetFields']=[x for x in payload['update']['unsetFields'] if x!=key]
            return replace(result,final_message=json.dumps(payload,ensure_ascii=False))
        f.model.run_turn=model
        with patch.object(stage,'verify_independent_approval',return_value={'reviewerSessionId':'independent'}):
            f.prepare();plan=f.plan()
            self.assertEqual(len(captured),1)
            proof=stage.verify_task_input(f.coordinator,plan)
            self.assertEqual(proof['inputHash'],captured[0]['inputHash'])
            receipt=f.apply()
            self.assertEqual(receipt['stageEvidence']['feedbackTaskRequirementDelivery'],proof)
            self.assertEqual(publication._normal_task_requirement_delivery(plan,receipt,f.coordinator),proof)
            for mutation in ('missing','prompt','turn','receipt'):
                changed=deepcopy(plan);changed_receipt=deepcopy(receipt)
                if mutation=='missing':changed['stagePlan']['feedbackTaskRequirements']={}
                if mutation=='prompt':changed['preparedCandidate']['content']['executionMetadata']['feedbackTaskRequirementDelivery']['promptHash']='b'*64
                if mutation=='turn':changed['preparedCandidate']['content']['executionMetadata']['feedbackTaskRequirementDelivery']['turnId']=''
                if mutation=='receipt':changed_receipt['stageEvidence'].pop('feedbackTaskRequirementDelivery')
                with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                    publication._normal_task_requirement_delivery(changed,changed_receipt,f.coordinator)
            self.assertEqual(receipt,f.apply())
            f.connection.execute("UPDATE improvement_tasks SET need='新しい要件'");f.connection.commit()
            with self.assertRaisesRegex(ValueError,'requirement changed'):f.plan(saved=True)


class InstitutionalExplanationContentTests(unittest.TestCase):
    def setUp(self):
        f=guidance_fixture.OfficialGuidancePipelineTests();f.setUp();self.addCleanup(f.doCleanups)
        self.root,self.record,self.references,self.scope=f.root,deepcopy(f.record),deepcopy(f.references),f.scope
        self.record.update(questionType='true_false',questionIntent='select_correct',isCalculationQuestion=False,
            correctChoiceText=['正しい','間違い'],explanationText=['正しい。','間違い。第二の完全命題には例外がある。'])
        self.facts=[{'auditStatus':'same_as_current','reviewState':'secondary_verified',
            'current':{'correctChoiceText':v},'examTime':{'correctChoiceText':v},
            'evidenceSummary':{'verdict':'same_as_current','explanationText':'公式制度資料の適用を確認した。'}}
            for v in ('正しい','間違い')]
        self.path='output/kanrigyoumu/questions_json/94009/21_explanationText_added/q.json'
        self.target=CandidateTarget('explanation','explanation',self.path,('lawRevisionFacts','lawReferences','explanationText'))

    def errors(self,record=None,qid='ui-id'):
        candidate=QuestionCandidate(qid,'candidate','根拠と解説を照合する。',
            (CandidateUpdate('explanation',{'lawRevisionFacts':self.facts},()),))
        return validate_candidate_content(candidate,(self.target,),record or self.record)

    def test_real_private_provider_proof_allows_institutional_empty_statutory_buckets(self):
        self.assertEqual(self.errors(),())
        missing=deepcopy(self.record);missing.pop('officialGuidanceReferences')
        self.assertTrue(any('verified根拠がありません' in x for x in self.errors(missing)))

    def test_forged_foreign_missing_choice_and_mixed_unverified_statutes_still_fail(self):
        for mutation in ('proof','question','choice','applicability','classification','mixed'):
            record=deepcopy(self.record);qid='ui-id'
            if mutation=='proof':record['officialGuidanceReferences']['evidenceRef']['sha256']='b'*64
            if mutation=='question':qid='foreign'
            if mutation=='choice':record['choiceTextList'][0]+='別の本文'
            if mutation=='applicability':record['officialGuidanceReferences']['applicabilityByChoice'].pop()
            if mutation=='classification':record['isLawRelated']=False
            if mutation=='mixed':record['lawReferences'][0]=[{'lawId':'337AC0000000069','article':'18','scope':'choice','choiceIndex':0,'verificationStatus':'unverified'}]
            with self.subTest(mutation=mutation):self.assertTrue(self.errors(record,qid),mutation)
