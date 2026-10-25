"""Independent evaluator receives safe needs and real institutional pages."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import pytest

from tests.test_feedback_source_tf_delivery import rig, saved
from tests import test_official_guidance_pipeline as fixture
from tools.question_bank import feedback_source_tf_delivery as api
from tools.question_review_console.evaluation import QuestionEvaluationService, EvaluationError
from tools.question_review_console.projection import sha256_json


def test_real_saved_source_unit_dispatch_pins_safe_requirement_and_refuses_context_replacement(rig, monkeypatch):
    plan, _, _ = saved(rig)
    question = api._question(rig.root, rig.path, plan, rig.coordinator, rig.connection, checked=True)
    question['sourceTfLedger'] = rig.connection.execute('PRAGMA database_list').fetchone()[2]
    monkeypatch.setattr(api.normal, 'coordinator_for', lambda root: rig.coordinator)
    monkeypatch.setattr(api, '_verify_checks', lambda *a, **k: None)
    api.validate_source_tf_question(rig.root, question)
    context = question['sourceTfFeedbackContext']
    assert context['taskRequirement']['definition'] == plan['source']['taskRequirement']['requirement']['definition']
    assert context['taskRequirement']['requirementHash'] == plan['source']['taskRequirement']['requirementHash']
    assert 'sources' not in context['taskRequirement']
    assert context['scope']['unitHash'] == question['stateHash']
    service = QuestionEvaluationService(rig.root, 'fixture', result_runner=lambda prompt: None)
    prompt = service._build_prompt(question)
    assert context['taskRequirement']['definition']['need'] in prompt
    assert context['contextHash'] in prompt
    assert 'feedbackEvaluationInstruction' in prompt
    for kind in ('missing', 'need', 'task', 'scope'):
        bad = deepcopy(question)
        if kind == 'missing': bad.pop('sourceTfFeedbackContext')
        if kind == 'need': bad['sourceTfFeedbackContext']['taskRequirement']['definition']['need'] = 'unapproved changed need'
        if kind == 'task': bad['sourceTfFeedbackContext']['taskRequirement']['taskId'] = 'foreign'
        if kind == 'scope': bad['sourceTfFeedbackContext']['scope']['planHash'] = 'b' * 64
        if 'sourceTfFeedbackContext' in bad:
            bad['sourceTfFeedbackContext']['contextHash'] = sha256_json({k:v for k,v in bad['sourceTfFeedbackContext'].items() if k != 'contextHash'})
        with pytest.raises(ValueError, match='sourceTfFeedbackContext'):
            api.validate_source_tf_question(rig.root, bad)


def test_evaluator_actual_prompt_has_same_positive_guidance_pages_and_never_uses_display_body_to_validate():
    f = fixture.OfficialGuidancePipelineTests(); f.setUp()
    try:
        req = {'schemaVersion':'feedback-task-requirement/v1','taskId':'task',
            'definition':{'title':'説明改善','need':'原則と例外の理由を説明する。','target':'public-first'},
            'sources':[{'candidate_id':'private-candidate','question_id':'public-first','text_hash':'private-hash','review_status':'promoted'}]}
        record = deepcopy(f.record)
        plan = {'planHash':'a'*64,'unitHash':'b'*64,
            'request':{'qualification':'kanrigyoumu','listGroupId':'94009','questionId':'ui-id',
                'publicationUnit':{'originalQuestionId':'original'},'authority':{'kind':'improvement','taskId':'task'},
                'source':{'binding':f.scope['sourceBinding']}},
            'source':{'source':record,'normalProjection':record,'stageProofs':[],
                'taskRequirement':{'requirement':req,'requirementHash':sha256_json(req)},
                'mapping':[{'questionId':qid,'sourceChoiceText':text} for qid,text in
                    zip(('public-first','public-second'),record['choiceTextList'])]}}
        context = api._feedback_evaluation_context(f.root,plan)
        assert context['officialGuidanceEvidence']['receipt'] == f.delivered['receipt']
        public = deepcopy(record);public['questionBodyText']='公開用肯定body'
        question = {'id':'ui-id','qualification':'kanrigyoumu','stateHash':plan['unitHash'],
            'projected':public,'sourceTfUnit':{'plan':'private-unit','planHash':plan['planHash']},
            'sourceTfOriginalBody':record['questionBodyText'],'sourceTfOfficialAnswer':{},'sourceTfFeedbackContext':context}
        service=QuestionEvaluationService(Path(__file__).resolve().parents[1],'fixture',result_runner=lambda prompt:None)
        prompt=service._build_prompt(question)
        assert f.delivered['receipt']['documents'][0]['pages'][0]['text'] in prompt
        assert f.delivered['receipt']['evidenceHash'] in prompt
        assert context['taskRequirement']['definition']['need'] in prompt
        assert 'private-hash' not in prompt
        invalid=deepcopy(question);invalid['sourceTfFeedbackContext']['scope']['unitHash']='wrong'
        with pytest.raises(EvaluationError):service._build_prompt(invalid)
        # Normal GUI questions without sourceTfUnit get no feedback context.
        question.pop('sourceTfUnit');question.pop('sourceTfFeedbackContext')
        assert 'feedbackEvaluationContext' not in service._build_prompt(question)
    finally:
        f.doCleanups()


def test_actual_evaluator_turn_saved_prompt_result_and_session_receipt_bind_context(rig, monkeypatch):
    plan, _, _ = saved(rig)
    api.check(rig.root, rig.path, **rig.options)
    monkeypatch.setattr(api.normal, 'coordinator_for', lambda root: rig.coordinator)
    prompts=[]
    def runner(prompt):
        prompts.append(prompt)
        return {'status':'passed','explanationScore':94,'criticalIssues':[], 'summary':'全肢と改善要件を独立照合したfixture。',
            'reworkItems':[], 'choiceEvaluations':[
                {'choiceIndex':index,'verdict':verdict,'reason':'独立したfixture根拠と一致。',
                    'evidence':[{'source':'公式fixture','locator':'問1','summary':'各命題を照合。'}]}
                for index,verdict in [(0,'false'),(1,'true'),(2,'true')]]}
    service=QuestionEvaluationService(rig.root,'fixture',result_runner=runner)
    def native_turn(prompt,*args,**kwargs):
        return runner(prompt), {'threadId':'independent-eval-thread','sessionId':'independent-eval-session','turnId':'eval-turn','model':'fixture'}
    monkeypatch.setattr(service,'_run_result',native_turn)
    result=api.evaluate(rig.root,rig.path,evaluation=service,**rig.options)
    assert result['evaluation']['status']=='passed' and len(prompts)==1
    question=api._question(rig.root,rig.path,plan,rig.coordinator,rig.connection,checked=True)
    question['sourceTfLedger']=api.normal._ledger_path(rig.connection)
    committed=service._committed_attempt_result(question,service.store.load_projection(question))
    context=question['sourceTfFeedbackContext']
    receipt=committed['feedbackInputReceipt']
    assert receipt['contextHash']==context['contextHash']
    assert receipt['promptHash']==api._hash(prompts[0].encode())
    assert receipt['threadId']=='independent-eval-thread' and receipt['turnId']=='eval-turn'
    manifest=service.run_store.get(question['qualification'],committed['runId'])
    stored=rig.root/manifest['promptPath']
    assert stored.read_text()==prompts[0]
    assert service._session_receipt_valid(question,committed)
    old=stored.read_bytes();stored.write_bytes(old+b'\nchanged')
    assert not service._session_receipt_valid(question,committed)
    stored.write_bytes(old)
    forged=deepcopy(committed);forged['feedbackInputReceipt']['contextHash']='b'*64
    assert not service._session_receipt_valid(question,forged)
    assert service._session_receipt_valid(question,committed)
