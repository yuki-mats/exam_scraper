"""Isolated source-TF contract tests. No live SDK or production authority."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch
from dataclasses import replace

import pytest

from tools.question_bank import feedback_source_tf_delivery as api
from tools.question_review_console.firestore_readback import FirestoreReadback
from tools.question_review_console import independent_approval as approval_api
from tests.test_native_independent_approval import native_audit


@pytest.fixture(autouse=True)
def no_live_sdk(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('live SDK initialization forbidden in source TF tests')
    monkeypatch.setattr(FirestoreReadback, '_database', forbidden)
    monkeypatch.setattr(api.uploader, 'init_firestore', forbidden)
    monkeypatch.setattr(api.uploader.firestore, 'client', forbidden)
    monkeypatch.setattr(api.uploader.firebase_admin, 'initialize_app', forbidden)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else api._bytes(value))
    return {'ref': str(path), 'sha256': api._hash(path.read_bytes())}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    for name in ('scripts', 'tools', 'config', 'prompt', 'document'):
        shutil.copytree(repo / name, tmp_path / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    private = tmp_path / api.PRIVATE / 'fixture'
    private.mkdir(parents=True)
    from tools.question_bank import feedback_daily as daily
    db = private / 'ledger.sqlite3'; connection = daily._private_db(db)
    connection.execute("INSERT INTO improvement_tasks VALUES('task','TF表示','命題と表示の整合','live-a','in_progress','fixture-time','fixture-time')")
    connection.execute("INSERT INTO ai_question_candidates(candidate_id,source_path,question_id,created_at,text_hash,visibility,review_status,improvement_task_id,first_seen_at,last_seen_at) VALUES('candidate','private-fixture','live-a','fixture','hash','public','promoted','task','fixture','fixture')")
    connection.commit()
    group = tmp_path / 'output/sample/questions_json/2026'
    binding = {'sourceQuestionKey': 'sample:2026:q1', 'reviewQuestionId': 'q1', 'sourceRecordRef': 'question_2026.json#0'}
    source = {'original_question_id': 'q1', **binding, 'questionBodyText': '誤っているものはどれか。',
        'choiceTextList': ['命題A。', '命題B。', '命題C。'], 'correctChoiceText': ['正しい'] * 3,
        'answer_result_text': '正解は 1 です。', 'question_url': 'https://example.test/q1',
        'examYear': 2026, 'examLabel': '2026年度', 'questionLabel': '問1'}
    other_source = {**source, 'original_question_id': 'q2', 'reviewQuestionId': 'q2',
        'sourceQuestionKey': 'sample:2026:q2', 'sourceRecordRef': 'question_2026.json#1',
        'question_url': 'https://example.test/q2', 'questionBodyText': '対象外の別原問。'}
    source_ref = write(group / '00_source/question_2026.json', {'question_bodies': [source, other_source]})
    write(tmp_path / api.MANIFEST, (json.dumps({'path': str(Path(source_ref['ref']).relative_to(tmp_path)),
        'sha256': source_ref['sha256']}) + '\n').encode())
    current = {**source, 'questionType': 'true_false', 'questionIntent': 'select_incorrect', 'isCalculationQuestion': False,
        'correctChoiceText': ['間違い', '正しい', '正しい'], 'explanationText': ['間違い。正しくは別の命題である。', '正しい。', '正しい。'],
        'questionSetId': 'set', 'questionLearningPatternId': 'differences_relationships', 'isLawRelated': False, 'lawGroundedExplanationNotNeeded': True}
    live_record = {**current, 'correctChoiceText': ['正しい'] * 3, 'explanationText': ['正しい。'] * 3,
        'firestoreQuestionIds': ['live-a', 'live-b', 'live-c']}
    baseline = {}
    for row in api.convert_question_to_firestore(live_record):
        qid = row['questionId']; fields = api.uploader.build_doc_data_base(row)
        fields.update(qualificationId='sample', examYear=2026)
        if qid == 'live-c':
            fields['questionBodyText'] = '次の命題は正しいか。'
            fields['questionText'] = f"次の命題は正しいか。[quote]{fields['originalQuestionChoiceText']}[/quote]"
        baseline[qid] = {'questionId': qid, 'exists': True, 'updateTimeExact': '2026-10-22T00:00:00.123456789Z', 'fields': fields}
    question = {'id': 'ui-q1', 'reviewKey': 'normal-source-q1', 'qualification': 'sample', 'listGroupId': '2026',
        **binding, 'originalQuestionId': 'q1', 'projected': current, 'isLawRelated': False,
        'workflow': {}, 'issueCodes': [], 'choiceCount': 3}
    coordinator = SimpleNamespace()
    statuses = {'allCurrent': True, 'stages': [{'id': 'question_type', 'status': 'current'}, {'id': 'correct_choice', 'status': 'current'}]}
    coordinator.workflow = SimpleNamespace(inventory=SimpleNamespace(group=lambda *a: {'questions': [deepcopy(question)]}),
        versioned_policies=lambda *a: {})
    coordinator.work_versions = SimpleNamespace(status_for=lambda *a: deepcopy(statuses))
    def proofs(root, request, owner, conn, files):
        for proof in request['stageProofs']:
            plan = api._ref(root, proof['plan'], files)
            api._ref(root, proof['receipt'], files)
            for p in plan['commitView']['patches']: api._read(root, root / p['path'], files)
        return deepcopy(question), deepcopy(current), [{'stageId': 'fixture-injected-proof'}], deepcopy(statuses)
    monkeypatch.setattr(api, 'verify_normal_stage_proofs', proofs)
    monkeypatch.setattr(api, '_pin_native_dependencies', lambda *args: {})
    monkeypatch.setattr(api, '_dependencies', lambda root, files: api._read(root, root / 'tools/question_bank/feedback_source_tf_delivery.py', files))
    snapshots = deepcopy(baseline)
    monkeypatch.setattr(api, '_snapshots', lambda *a, **k: deepcopy(snapshots))
    monkeypatch.setattr(approval_api, 'NATIVE_SESSION_ROOT', private)
    stage_proofs = []
    for stage, folder, fields in [('question_type', '10_questionType_fixed', {'questionType': 'true_false', 'isCalculationQuestion': False}),
                                  ('correct_choice', '23_correctChoiceText_fixed', {'correctChoiceText': current['correctChoiceText']})]:
        patch_path = group / folder / f'question_2026_{stage}.json'
        write(patch_path, [{'original_question_id': 'q1', **binding, **fields}])
        p = {'stageId': stage, 'qualification': 'sample', 'childRunId': 'fixture-child-' + stage,
            'commitView': {'patches': [{'path': str(patch_path.relative_to(tmp_path))}]}}
        stage_proofs.append({'plan': write(private / stage / 'plan.json', p), 'receipt': write(private / stage / 'save-receipt.json', {'fixture': True})})
    primary = []
    for kind in ('question_pdf', 'answer_pdf'):
        primary.append({'kind': kind, **write(private / (kind + '.pdf'), b'%PDF fixture official evidence'), 'page': 1, 'locator': '問1'})
    request = {'schemaVersion': api.REQUEST, 'qualification': 'sample', 'listGroupId': '2026', 'questionId': 'ui-q1',
        'source': {**source_ref, 'binding': binding}, 'authority': {'kind': 'improvement', 'taskId': 'task'},
        'publicationUnit': {'qualificationId': 'sample', 'examYear': 2026, 'originalQuestionId': 'q1'},
        'primaryEvidence': primary, 'officialAnswer': {'sourceValue': source['answer_result_text'], 'selectedChoiceText': source['choiceTextList'][0]},
        'stageProofs': stage_proofs, 'mapping': [], 'public': []}
    for qid, text, truth in zip(('live-a', 'live-b', 'live-c'), source['choiceTextList'], current['correctChoiceText']):
        request['mapping'].append({'questionId': qid, 'sourceChoiceText': text, 'sourceChoiceSha256': api._hash(text.encode()), 'liveChoiceText': text})
        row = {'questionId': qid, 'questionBodyText': '次の命題は正しいか。',
            'questionText': f'次の命題は正しいか。[quote]{text}[/quote]', 'correctChoiceText': truth}
        if qid == 'live-a': row['explanationText'] = current['explanationText'][0]
        request['public'].append(row)
    request_path = private / 'request.json'; write(request_path, request)
    output = private / 'unit'; plan_path = output / 'plan.json'
    options = {'coordinator': coordinator, 'connection': connection}
    state = SimpleNamespace(root=tmp_path, private=private, source=source, current=current, baseline=baseline,
        snapshots=snapshots, request=request, request_path=request_path, output=output, path=plan_path,
        coordinator=coordinator, connection=connection, options=options, original_upload=api.uploader.upload_read_set_maintenance_questions)
    yield state
    connection.close()


def prepared(rig):
    api.prepare(rig.root, rig.request_path, rig.output, **rig.options)
    return api._json(rig.path.read_bytes())


def approved(rig, action='save', evaluation=None):
    scope = api.scope(rig.root, rig.path, action=action, evaluation=evaluation, **rig.options)
    folder = rig.private / (action + '-audit'); folder.mkdir(exist_ok=True)
    return Path(write(folder / 'approval.json', native_audit(folder, scope))['ref'])


def saved(rig):
    plan = prepared(rig); approval = approved(rig)
    receipt = api.save(rig.root, rig.path, approval, **rig.options)
    return plan, approval, receipt


def test_default_sdk_read_and_write_initialization_is_denied():
    for initialize in (FirestoreReadback()._database, api.uploader.init_firestore,
                       api.uploader.firestore.client, api.uploader.firebase_admin.initialize_app):
        with pytest.raises(AssertionError): initialize()


def test_real_normal_stage_proofs_allow_later_truth_fix_and_reject_child_checkpoint_patch_native_drift(monkeypatch):
    """Real prepare/writer/workVersions/checkpoints, not the boundary rig's stub."""
    from tests.test_feedback_prepared_stage import PreparedStageTests
    from tools.question_review_console.inventory import QuestionInventory
    from tests.qualification_run_test_support import SourceOnlyInventory
    f = PreparedStageTests(); f.setUp()
    try:
        source_path = Path(f.request['source']['ref'])
        row = json.loads(source_path.read_bytes())['question_bodies'][0]
        row.update(questionBodyText='誤っている記述はどれか。', choiceTextList=['命題A。', '命題B。', '命題C。'],
            correctChoiceText=['正しい'] * 3, questionIntent='select_incorrect')
        f.write(source_path, {'question_bodies': [row]})
        f.request['source']['sha256'] = api._hash(source_path.read_bytes())
        f.write(f.root / api.MANIFEST, (json.dumps({'path': str(source_path.relative_to(f.root)),
            'sha256': f.request['source']['sha256']}) + '\n').encode())
        reader = QuestionInventory(f.root)
        class NormalProjectionInventory(SourceOnlyInventory):
            def group(self, qualification, group):
                result = super().group(qualification, group)
                question = result['questions'][0]
                question['source'] = deepcopy(row)
                projected = reader.projected_input_for_stage(qualification, group, question['sourceRecordRef'], 'question_type')
                question['projected'] = dict(projected.record)
                question['paths']['patches'] = list(projected.applied_files)
                return result
            def projected_input(self, *args): return reader.projected_input(*args)
            def projected_input_for_stage(self, *args): return reader.projected_input_for_stage(*args)
            def source_input(self, *args): return reader.source_input(*args)
            def _projection_stage_maps(self, *args): return reader._projection_stage_maps(*args)
            def _projection_issue_index(self, *args): return reader._projection_issue_index(*args)
        f.coordinator.workflow.inventory = NormalProjectionInventory()
        category_path = f.root / 'output/new-exam/category/category.json'
        f.write(category_path, {'folders': [{'folderId': 'folder', 'name': '分類'}],
            'questionSets': [{'questionSetId': 'test-question-set', 'folderId': 'folder',
                'name': '基本分類', 'description': '問題全体の判断基準', 'matchingHints': ['一般的な技術知識']}]})
        original_turn = f.model.run_turn
        def run_turn(prompt, **kwargs):
            result = original_turn(prompt, **kwargs)
            values = {'maintenance_question_intent_candidate': ('questionIntent', 'select_incorrect'),
                'maintenance_correct_choice_candidate': ('correctChoiceText', ['間違い', '正しい', '正しい'])}
            if kwargs['work_type'] in values:
                key, value = values[kwargs['work_type']]
                payload = json.loads(result.final_message)
                payload['update'] = {'setFields': [{'field': key, 'value': value}], 'unsetFields': []}
                result = replace(result, final_message=json.dumps(payload, ensure_ascii=False))
            return result
        monkeypatch.setattr(f.model, 'run_turn', run_turn)
        monkeypatch.setattr(approval_api, 'NATIVE_SESSION_ROOT', f.private)
        policies = f.coordinator.workflow.versioned_policies
        selected = {'question_type', 'question_intent', 'correct_choice', 'question_set'}
        monkeypatch.setattr(f.coordinator.workflow, 'versioned_policies',
            lambda *a: {k: v for k, v in policies(*a).items() if k in selected})
        proofs = []
        for stage_id in ('question_type', 'question_intent', 'correct_choice', 'question_set'):
            f.request['stageId'] = stage_id
            f.output = f.private / stage_id
            f.request_path = f.private / (stage_id + '-request.json')
            f.write(f.request_path, f.request)
            f.prepare()
            plan = f.plan()
            scope = api.normal.approval_scope(f.root, f.output / 'plan.json', coordinator=f.coordinator, connection=f.connection)
            audit_dir = f.private / (stage_id + '-audit'); audit_dir.mkdir()
            f.approval_path = audit_dir / 'approval.json'
            f.write(f.approval_path, native_audit(audit_dir, scope))
            f.apply()
            proofs.append({'plan': write(f.output / 'plan.json', (f.output / 'plan.json').read_bytes()),
                'receipt': write(f.output / 'save-receipt.json', (f.output / 'save-receipt.json').read_bytes())})
        request = {k: deepcopy(f.request[k]) for k in ('qualification', 'listGroupId', 'questionId', 'source', 'authority')}
        request['stageProofs'] = proofs
        def verify(files=None): return api.verify_normal_stage_proofs(
            f.root, request, f.coordinator, f.connection, files if files is not None else {})
        _, projected, receipts, status = verify()
        assert projected['correctChoiceText'] == ['間違い', '正しい', '正しい']
        assert {r['stageId'] for r in receipts} == selected and status['allCurrent']
        files = {}; verify(files)
        category_relative = str(category_path.relative_to(f.root))
        category_before = category_path.read_bytes()
        assert files[category_relative] == api._hash(category_before)
        for mutation in ('changed', 'missing'):
            if mutation == 'changed': category_path.write_bytes(category_before + b'\n')
            else: category_path.unlink()
            with pytest.raises((ValueError, FileNotFoundError)):
                verify()
            category_path.write_bytes(category_before)
            assert verify()[3]['allCurrent']
        # An old or foreign classification binding cannot gain authority by
        # reconstructing its other historical fields from current state.
        original_ref = api._ref
        for mutation in ('missing', 'foreign'):
            def legacy_category_ref(root, spec, files, *, private=False):
                value = original_ref(root, spec, files, private=private)
                if spec == proofs[-1]['plan']:
                    value = deepcopy(value)
                    expected = value['inputFiles'].pop(category_relative)
                    if mutation == 'foreign':
                        value['inputFiles']['output/other-exam/category/category.json'] = expected
                    value.pop('planHash'); value = api._seal(value)
                return value
            with patch.object(api, '_ref', side_effect=legacy_category_ref):
                with pytest.raises(ValueError, match='fresh prepare required'):
                    verify()
        final = api._json(Path(proofs[2]['plan']['ref']).read_bytes())
        # Pure history projector only: a later 03b may own the same 23 path.
        # This synthetic input is never accepted as a saved normal proof.
        last_patch = deepcopy(final['commitView']['patches'][0])
        law_rows = api._rows(last_patch['afterUtf8'].encode())
        law_rows[0]['correctChoiceText'] = ['正しい', '間違い', '正しい']
        last_patch['afterUtf8'] = api._bytes(law_rows).decode()
        later = write(f.private / 'pure-law-history.json', {'stageId': 'law_audit', 'commitView': {'patches': [last_patch]}})
        with_law = deepcopy(request); with_law['stageProofs'].append({'plan': later})
        earlier, _ = api._stage_predecessor_record(f.root, with_law, f.coordinator, 'correct_choice',
            selected | {'law_audit'}, include_stage=True)
        after_law, _ = api._stage_predecessor_record(f.root, with_law, f.coordinator, 'law_audit',
            selected | {'law_audit'}, include_stage=True)
        assert earlier['correctChoiceText'] == ['間違い', '正しい', '正しい']
        assert after_law['correctChoiceText'] == ['正しい', '間違い', '正しい']
        patch_path = f.root / final['commitView']['patches'][0]['path']; before = patch_path.read_bytes()
        changed = api._json(before); changed[0]['correctChoiceText'] = ['正しい'] * 3
        write(patch_path, changed)
        with pytest.raises(ValueError): verify()
        patch_path.write_bytes(before)
        get_child = f.coordinator.store.get
        def invalid_child(qualification, run_id):
            child = get_child(qualification, run_id)
            if run_id == final['childRunId']: child['receiptValidated'] = False
            return child
        with patch.object(f.coordinator.store, 'get', side_effect=invalid_child):
            with pytest.raises(ValueError, match='actual normal writer'): verify()
        detail = f.coordinator.store.question_detail
        def invalid_checkpoint(qualification, run_id, question_id, **kwargs):
            value = detail(qualification, run_id, question_id, **kwargs)
            if run_id == final['parentRunId']:
                for checkpoint in value['execution']['stages']:
                    if checkpoint['stageId'] == final['stageId']: checkpoint['status'] = 'blocked'
            return value
        with patch.object(f.coordinator.store, 'question_detail', side_effect=invalid_checkpoint):
            with pytest.raises(ValueError, match='actual validated parent'): verify()
        with patch.object(f.coordinator, '_phase_plan_policy_is_current', return_value=False):
            with pytest.raises(ValueError, match='policy_changed'): verify()
        journal = Path(api._json(f.approval_path.read_bytes())['reviewerFinal']['sessionPath'])
        journal.write_bytes(journal.read_bytes() + api._bytes({'type': 'event_msg', 'payload': {'type': 'user_message', 'message': '承認撤回'}}))
        with pytest.raises(ValueError): verify()
    finally:
        f.doCleanups()



def test_real_explanation_mirrors_preserve_canonical_context_and_later_law_audit_history(monkeypatch):
    from tests.test_feedback_prepared_stage import PreparedLawAuditTimestampTests
    from tests.test_law_context_primary_cycle import LawModel, XML
    from tools.question_review_console.primary_law_evidence import PrimaryLawEvidenceResolver, LawFileSnapshot
    from tools.question_review_console.inventory import QuestionInventory
    f = PreparedLawAuditTimestampTests(); f.setUp()
    try:
        reader = QuestionInventory(f.root)
        for method in ('projected_input', 'projected_input_for_stage', 'source_input',
                       '_projection_stage_maps', '_projection_issue_index'):
            setattr(f.inventory, method, getattr(reader, method))
        law_model = LawModel()
        f.coordinator.primary_law_evidence = PrimaryLawEvidenceResolver(f.root, fetcher=lambda law_id, as_of:
            LawFileSnapshot(law_id, as_of, f'https://laws.e-gov.go.jp/api/2/law_file/xml/{law_id}?asof={as_of}', 'fixture', XML))
        category = f.root / 'output/new-exam/category/category.json'
        f.write(category, {'folders': [{'folderId': 'folder', 'name': '分類'}],
            'questionSets': [{'questionSetId': 'test-question-set', 'folderId': 'folder', 'name': '制度'}]})
        original = f.model.run_turn
        def run(prompt, **kwargs):
            kind = kwargs['work_type']
            if kind == 'maintenance_law_context_candidate':
                return law_model.run_turn(prompt, **kwargs)
            result = original(prompt, **kwargs)
            if kind not in {'maintenance_explanation_candidate', 'maintenance_law_audit_candidate'}: return result
            payload = json.loads(result.final_message)
            fields = {item['field']: item['value'] for item in payload['update']['setFields']}
            fields.update(explanationText=['正しい。設問に示された行為は制度の定義に該当する。'])
            if kind == 'maintenance_explanation_candidate':
                fields.update(questionLearningPatternId='terms_basics', explanationReferences=[])
                reference = deepcopy(fields['lawReferences'][0][0]); reference['article'] = '12'
                fields.update(lawReferences=[[reference]], lawContextForExplanation='以前の参照を残した歴史的な補助情報。')
            else:
                fields['lawContextForExplanation'] = '根拠条文の制度と設問の行為を照合した。'
            payload['update']['setFields'] = [{'field': key, 'value': value} for key, value in fields.items()]
            payload['update']['unsetFields'] = [key for key in payload['update']['unsetFields'] if key not in fields]
            return replace(result, final_message=json.dumps(payload, ensure_ascii=False))
        monkeypatch.setattr(f.model, 'run_turn', run)
        monkeypatch.setattr(approval_api, 'NATIVE_SESSION_ROOT', f.private)
        policies = f.coordinator.workflow.versioned_policies
        stages = ('question_type', 'question_intent', 'correct_choice', 'law_context', 'explanation', 'law_audit', 'question_set')
        monkeypatch.setattr(f.coordinator.workflow, 'versioned_policies',
            lambda *a: {k: v for k, v in policies(*a).items() if k in stages})
        proofs = []; plans = {}
        for stage_id in stages:
            f.request['stageId'] = stage_id
            f.output = f.private / stage_id; f.request_path = f.private / (stage_id + '-request.json')
            f.write(f.request_path, f.request); f.prepare(); plans[stage_id] = f.plan()
            scope = api.normal.approval_scope(f.root, f.output / 'plan.json', coordinator=f.coordinator, connection=f.connection)
            audit_dir = f.private / (stage_id + '-audit'); audit_dir.mkdir()
            f.approval_path = audit_dir / 'approval.json'; f.write(f.approval_path, native_audit(audit_dir, scope))
            f.apply()
            proofs.append({'plan': write(f.output / 'plan.json', (f.output / 'plan.json').read_bytes()),
                'receipt': write(f.output / 'save-receipt.json', (f.output / 'save-receipt.json').read_bytes())})
        request = {k: deepcopy(f.request[k]) for k in ('qualification', 'listGroupId', 'questionId', 'source', 'authority')}
        request['stageProofs'] = proofs
        def verify(): return api.verify_normal_stage_proofs(f.root, request, f.coordinator, f.connection, {})
        _, current, receipts, status = verify()
        assert status['allCurrent'] and len(receipts) == 7
        explanation = plans['explanation']
        patch21 = next(p for p in explanation['commitView']['patches'] if '21_explanationText_added' in p['path'])
        row21 = api._rows(patch21['afterUtf8'].encode())[0]
        assert row21['lawReferences'][0][0]['article'] == '12'
        assert current['lawReferences'][0][0]['article'] == '11'
        assert current['lawContextForExplanation'] != row21['lawContextForExplanation']
        receipt03 = next(r for r in receipts if r['stageId'] == 'explanation')
        assert 'lawReferences' in receipt03['ownedFields'] and 'lawReferences' not in receipt03['effectiveProjectionFields']
        assert 'explanationText' in receipt03['effectiveProjectionFields']
        assert next(r for r in receipts if r['stageId'] == 'law_audit')['lawVerification']['reviewCount'] >= 1
        real_predecessor = api._stage_predecessor_record
        for changed_stage, field, value in (
            ('law_context', 'lawContextForExplanation', '不一致の有効18投影。'),
            ('explanation', 'explanationText', ['正しい。不一致の有効21投影。'])):
            def changed_projection(*args, **kwargs):
                record, fields = real_predecessor(*args, **kwargs)
                if args[3] == changed_stage and kwargs.get('include_stage'):
                    record = deepcopy(record); record[field] = value
                return record, fields
            with patch.object(api, '_stage_predecessor_record', side_effect=changed_projection):
                with pytest.raises(api.GateRequired, match='normal_stage_projection_changed:' + changed_stage + ':' + field):
                    verify()
        # Both real owners and ignored mirror bytes remain immutable: a normal
        # projector ignoring a mirror must never authorize modifying its file.
        for stage_id, directory, field, value in (
            ('law_audit', '18_law_context_prepared', 'lawContextForExplanation', '第三者による変更。'),
            ('law_audit', '21_explanationText_added', 'explanationText', ['正しい。第三者による変更。']),
            ('law_audit', '21_explanationText_added', 'lawContextForExplanation', '未承認のmirror変更。')):
            candidate = next(p for p in plans[stage_id]['commitView']['patches'] if directory in p['path'])
            path = f.root / candidate['path']; before = path.read_bytes(); rows = api._rows(before)
            rows[0][field] = value; f.write(path, rows)
            with pytest.raises(ValueError): verify()
            path.write_bytes(before)
        # A genuine old 03 projected input cannot be replaced by a stale 18
        # value even though 21 had permission to store the same mirror field.
        projected_path = f.root / explanation['stagePlan']['progressTargets'][0]['_projectedInputPath']
        before = projected_path.read_bytes(); rows = api._rows(before)
        rows[0]['lawContextForExplanation'] = '未認証の古い前工程入力。'; f.write(projected_path, rows)
        with pytest.raises(ValueError, match='historical projected input changed'): verify()
        projected_path.write_bytes(before)
        # Exercise the predecessor semantic check independently of the byte
        # guard: simulated matching bytes still cannot hide stale canonical input.
        real_rows = api._rows
        def stale_projection(raw):
            rows = real_rows(raw)
            if raw == before:
                rows = deepcopy(rows); rows[0]['lawContextForExplanation'] = '未認証の古い前工程入力。'
            return rows
        with patch.object(api, '_rows', side_effect=stale_projection):
            with pytest.raises(api.GateRequired, match='normal_stage_predecessor_changed:explanation:lawContextForExplanation'):
                verify()
        real_candidate = f.coordinator.store.load_prepared_candidate
        def changed_candidate(*args, **kwargs):
            candidate = real_candidate(*args, **kwargs)
            if args[1] == explanation['childRunId']:
                candidate = deepcopy(candidate); candidate['contentHash'] = 'sha256:' + '0' * 64
            return candidate
        with patch.object(f.coordinator.store, 'load_prepared_candidate', side_effect=changed_candidate):
            with pytest.raises(ValueError, match='normal saved candidate changed'): verify()
        assert verify()[3]['allCurrent']
    finally:
        f.doCleanups()


@pytest.mark.parametrize('law_value', ['no-layer', 'absent', None, []])
def test_explanation_effective_fields_use_normal_consumption_and_18_non_none_precedence(tmp_path, law_value):
    from tools.question_review_console.question_candidate import CandidateTarget
    from scripts.merge.record_projection import project_merge_record
    from scripts.merge.patch_views import PatchArtifactEntry
    request = {'qualification': 'sample', 'source': {'binding':
        {'sourceQuestionKey': 'key', 'reviewQuestionId': 'question', 'sourceRecordRef': 'record'}}}
    coordinator = SimpleNamespace(workflow=SimpleNamespace(catalog=lambda _: {'stages': [
        {'id': 'law_context'}, {'id': 'explanation'}]}))
    target = CandidateTarget('target', 'explanation', '21',
        ('explanationText', 'lawContextForExplanation', 'lawReferences'))
    row = {**request['source']['binding']}
    fallback = law_value is None or law_value == 'absent' or law_value == 'no-layer'
    if not (law_value == 'absent' or law_value == 'no-layer'): row['lawReferences'] = law_value
    mirror = {'lawReferences': [['historical-mirror']], 'lawContextForExplanation': '消費されないmirror。',
        'explanationText': ['正しい。説明本文。']}
    snapshots = {'output/sample/questions_json/2026/18_law_context_prepared/patch.json': {'afterUtf8': api._bytes([row]).decode()},
        '21': {'afterUtf8': api._bytes([{**request['source']['binding'], **mirror}]).decode()}}
    if law_value == 'no-layer': snapshots.pop('output/sample/questions_json/2026/18_law_context_prepared/patch.json')
    with patch.object(api, '_history_patches', return_value=snapshots):
        fields = api._effective_projection_fields(tmp_path, request, coordinator, 'explanation',
            {'law_context', 'explanation'}, target, {})
    assert 'explanationText' in fields and 'lawContextForExplanation' not in fields
    assert ('lawReferences' in fields) is fallback
    # The expected ownership is observed through the real normal projector,
    # including the legacy no-18 / None fallback and empty-list ownership.
    source = {'original_question_id': 'question', 'questionBodyText': '設問。',
        'choiceTextList': ['記述。'], 'correctChoiceText': ['正しい']}
    projected = project_merge_record(source,
        law_context=() if law_value == 'no-layer' else (PatchArtifactEntry(tmp_path / '18.json', row),),
        explanation=(PatchArtifactEntry(tmp_path / '21.json', mirror),)).merged2
    assert projected['lawReferences'] == (mirror['lawReferences'] if 'lawReferences' in fields else law_value)
    assert 'lawContextForExplanation' not in projected


def test_prepare_fixes_source_truth_mapping_complete_reads_and_actual_write_subset(rig):
    source_before = Path(rig.request['source']['ref']).read_bytes()
    plan = prepared(rig)
    assert plan['source']['source'] == rig.source
    assert plan['source']['normalProjection']['correctChoiceText'] == ['間違い', '正しい', '正しい']
    assert set(plan['public']['baseline']) == {'live-a', 'live-b', 'live-c'}
    assert set(plan['public']['writeFieldsByDocument']) == {'live-a', 'live-b'}
    payload = api._payload(plan)
    assert payload['schemaVersion'] == api.uploader.READ_SET_TF_TRUTH_SCHEMA
    assert Path(rig.request['source']['ref']).read_bytes() == source_before
    assert not (rig.root / plan['formalPath']).exists()
    with pytest.raises(ValueError): api.prepare(rig.root, rig.request_path, rig.output, **rig.options)


@pytest.mark.parametrize('mutation', ['duplicate_id', 'wrong_hash', 'mixed_text', 'missing', 'foreign', 'unknown'])
def test_source_live_mapping_and_unknown_fields_refuse_before_plan(rig, mutation):
    if mutation == 'duplicate_id': rig.request['mapping'][1]['questionId'] = 'live-a'
    if mutation == 'wrong_hash': rig.request['mapping'][0]['sourceChoiceSha256'] = '0' * 64
    if mutation == 'mixed_text': rig.request['mapping'][0]['liveChoiceText'] = '命題B。'
    if mutation == 'missing': rig.request['mapping'].pop()
    if mutation == 'foreign': rig.request['mapping'][0]['questionId'] = 'foreign'
    if mutation == 'unknown': rig.request['allowMissingStages'] = True
    write(rig.request_path, rig.request)
    with pytest.raises((ValueError, KeyError)): prepared(rig)
    assert not rig.path.exists()


def test_missing_explanation_delta_and_changed_official_answer_refuse(rig):
    rig.request['public'][0].pop('explanationText'); write(rig.request_path, rig.request)
    with pytest.raises(api.GateRequired, match='explanation'): prepared(rig)
    rig.request['public'][0]['explanationText'] = rig.current['explanationText'][0]
    rig.request['officialAnswer']['selectedChoiceText'] = '命題B。'; write(rig.request_path, rig.request)
    with pytest.raises(api.GateRequired, match='official'): prepared(rig)


def test_native_saved_unit_is_exclusive_idempotent_and_preserves_canonical_bytes(rig):
    plan = prepared(rig); before = Path(rig.request['source']['ref']).read_bytes(); approval = approved(rig)
    receipt = api.save(rig.root, rig.path, approval, **rig.options)
    assert receipt['schemaVersion'] == api.SAVED and receipt['formalUnitSaved'] is True
    assert receipt['FirestoreWritten'] is False and receipt['publicationReady'] is False
    assert api.save(rig.root, rig.path, approval, **rig.options) == receipt
    assert api.inspect(rig.root, rig.path, **rig.options)['formalUnitSaved'] is True
    assert Path(rig.request['source']['ref']).read_bytes() == before
    assert sorted(p.name for p in (rig.root / plan['formalPath']).iterdir()) == sorted([*api.FILES, 'receipt.json'])


def privacy_inputs(rig):
    """Synthetic private markers only; no real user data or SDK access."""
    actor = 'fixture-private-audit-actor-value'
    comment = 'fixture-private-user-report-body-must-never-enter-git'
    source_path = Path(rig.request['source']['ref'])
    original = json.loads(source_path.read_bytes())
    original['question_bodies'][0]['unknownMetadata'] = {
        'nested': [{'updatedById': actor, 'detailComment': comment}]}
    source_ref = write(source_path, original)
    rig.request['source']['sha256'] = source_ref['sha256']
    write(rig.root / api.MANIFEST, (json.dumps({'path': str(source_path.relative_to(rig.root)),
        'sha256': source_ref['sha256']}) + '\n').encode())
    rig.current['unknownMetadata'] = {'nested': {'created_by_id': actor, 'text': comment}}
    for snapshots in (rig.snapshots, rig.baseline):
        for snapshot in snapshots.values():
            snapshot['fields'].update(createdById=actor, updatedById=actor)
    rig.connection.execute('UPDATE improvement_tasks SET title=?,need=? WHERE task_id=?', (comment, comment, 'task'))
    rig.connection.commit()
    write(rig.request_path, rig.request)
    return actor, comment


def test_git_projection_excludes_private_context_and_recursive_metadata_without_changing_private_values(rig):
    actor, comment = privacy_inputs(rig)
    plan = prepared(rig); private_before = rig.path.read_bytes()
    source_before = Path(rig.request['source']['ref']).read_bytes()
    approval = approved(rig)
    receipt = api.save(rig.root, rig.path, approval, **rig.options)
    formal = rig.root / plan['formalPath']
    artifacts = {name: api._json((formal / name).read_bytes()) for name in (*api.FILES, 'receipt.json')}
    git_bytes = b'\n'.join((formal / name).read_bytes() for name in artifacts)
    assert actor.encode() not in git_bytes and comment.encode() not in git_bytes
    assert artifacts['source.json']['schemaVersion'] == api.GIT_ARTIFACT
    assert artifacts['public.json']['privateValueHash'] == api.sha256_json(plan['public'])
    assert artifacts['manifest.json']['gitPrivacy']['publicArtifactHash'] == api.sha256_json(artifacts['public.json'])
    public = artifacts['public.json']['value']
    assert public['baseline']['live-a']['fields']['privateMetadata']['privateValuePresent']
    assert public['questions'][0]['businessFields']['questionBodyText'] == '次の命題は正しいか。'
    assert plan['source']['source']['unknownMetadata']['nested'][0]['detailComment'] == comment
    assert plan['source']['normalProjection']['unknownMetadata']['nested']['created_by_id'] == actor
    assert plan['public']['baseline']['live-a']['fields']['updatedById'] == actor
    assert all(row['updatedById'] == actor for row in api._payload(plan)['questions'])
    assert rig.path.read_bytes() == private_before
    assert Path(rig.request['source']['ref']).read_bytes() == source_before
    assert rig.path.stat().st_mode & 0o777 == 0o600
    assert api.save(rig.root, rig.path, approval, **rig.options) == receipt
    assert api.load_saved_unit(rig.root, rig.path, **rig.options) == (plan, receipt)


def test_git_business_projection_commits_unknown_and_nested_values_by_default():
    actor = 'fixture-private-nested-actor'
    comment = 'fixture-private-nested-report'
    record = {'questionBodyText': '公開命題。', 'updatedById': actor,
        'suggestedQuestionDetails': [{'question': '公開補足', 'metadata': {'reporterUid': actor, 'detailComment': comment}}],
        'unrecognizedMetadata': {'freeform': comment}}
    projected = api._git_business_record(record)
    assert actor not in json.dumps(projected) and comment not in json.dumps(projected)
    assert projected['businessFields']['questionBodyText'] == '公開命題。'
    assert projected['businessFields']['suggestedQuestionDetails'][0] == api._private_commitment(record['suggestedQuestionDetails'][0])
    changed = deepcopy(record); changed['unrecognizedMetadata']['freeform'] = comment + '-changed'
    assert api._git_business_record(changed) != projected


def test_native_scope_commits_all_freeform_private_context_without_redacting_the_emitted_marker(rig):
    actor, comment = privacy_inputs(rig)
    plan = prepared(rig)
    scope = api.scope(rig.root, rig.path, action='save', **rig.options)
    assert scope['schemaVersion'] == 'source-bound-public-tf-independent-scope/v3'
    for field in ('taskRequirement', 'taskLifecycle', 'completionContract'):
        assert scope[field] == api._private_commitment(plan['source'][field])
    marker = api.scope_marker(scope, creation=True)
    assert actor not in marker and comment not in marker
    assert marker.endswith(json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
    assert scope['publicHash'] == api.sha256_json(plan['public'])
    assert scope['gitPrivacyPolicyHash'] == api._git_policy_hash()


def test_git_projection_is_not_substituted_for_raw_report_candidate_review_context(rig):
    from tools.question_bank import feedback_source_tf_report_review as review
    actor = 'fixture-private-review-context-actor'
    for snapshot in rig.snapshots.values(): snapshot['fields']['updatedById'] = actor
    plan = prepared(rig)
    context = review.context(rig.request, plan['source'], plan['public'])
    assert context['public'] == plan['public']
    assert context['normalProjection'] == plan['source']['normalProjection']
    assert all(row['updatedById'] == actor for row in context['public']['questions'])
    subject = review.adoption.subject(context)
    assert actor not in json.dumps(subject)
    assert all(row['updatedById']['identityValueMasked'] for row in subject['candidate']['plannedPublic']['questions'])
    assert subject['candidate']['plannedPublic']['questions'][0]['questionBodyText'] == '次の命題は正しいか。'
    assert subject['observedBeforeHash'] == api.sha256_json(plan['public']['baseline'])
    assert api._git_public(plan['public'])['value'] != context['public']


@pytest.mark.parametrize('mutation', ['formal_public_hash', 'formal_content', 'manifest_readset', 'receipt_binding'])
def test_masked_formal_hashes_and_receipt_cannot_replace_private_commitments(rig, mutation):
    privacy_inputs(rig)
    plan, _, _ = saved(rig); directory = rig.root / plan['formalPath']
    if mutation in {'formal_public_hash', 'formal_content'}:
        path = directory / 'public.json'; value = api._json(path.read_bytes())
        if mutation == 'formal_public_hash': value['privateValueHash'] = '0' * 64
        else: value['value']['questions'][0]['businessFields']['questionBodyText'] = '未承認の変更。'
    elif mutation == 'manifest_readset':
        path = directory / 'manifest.json'; value = api._json(path.read_bytes())
        value['gitPrivacy']['privateReadSetHash'] = '0' * 64
    else:
        path = directory / 'receipt.json'; value = api._json(path.read_bytes())
        value['privacyBinding']['privateReceiptBindingHash'] = '0' * 64
    write(path, value)
    with pytest.raises(ValueError): api.inspect(rig.root, rig.path, **rig.options)


@pytest.mark.parametrize('mutation', ['hidden_source', 'hidden_normal', 'hidden_live_identity', 'private_requirement'])
def test_redacted_values_still_invalidate_current_plan_and_approval(rig, mutation):
    actor, comment = privacy_inputs(rig)
    plan, approval, _ = saved(rig)
    formal_before = {name: (rig.root / plan['formalPath'] / name).read_bytes() for name in (*api.FILES, 'receipt.json')}
    if mutation == 'hidden_source':
        path = Path(rig.request['source']['ref']); value = json.loads(path.read_bytes())
        value['question_bodies'][0]['unknownMetadata']['nested'][0]['updatedById'] = actor + '-changed'
        write(path, value)
    elif mutation == 'hidden_normal': rig.current['unknownMetadata']['nested']['created_by_id'] = actor + '-changed'
    elif mutation == 'hidden_live_identity': rig.snapshots['live-c']['fields']['updatedById'] = actor + '-changed'
    else:
        rig.connection.execute('UPDATE improvement_tasks SET need=? WHERE task_id=?', (comment + '-changed', 'task'))
        rig.connection.commit()
    with pytest.raises(ValueError): api.inspect(rig.root, rig.path, **rig.options)
    with pytest.raises(ValueError): api.save(rig.root, rig.path, approval, **rig.options)
    assert all((rig.root / plan['formalPath'] / name).read_bytes() == raw for name, raw in formal_before.items())


@pytest.mark.parametrize('schema', ['source-bound-public-tf-plan/v1', 'source-bound-public-tf-plan/v2'])
def test_legacy_privacy_plan_requires_fresh_preparation_without_overwriting_old_unit(rig, schema):
    plan, approval, _ = saved(rig)
    formal = rig.root / plan['formalPath']; before = {name: (formal / name).read_bytes() for name in (*api.FILES, 'receipt.json')}
    legacy = deepcopy(plan); legacy['schemaVersion'] = schema
    legacy = api._seal({key: value for key, value in legacy.items() if key != 'planHash'})
    write(rig.path, legacy)
    with pytest.raises(api.GateRequired, match='legacy_source_tf_privacy'):
        api.inspect(rig.root, rig.path, **rig.options)
    with pytest.raises(api.GateRequired, match='legacy_source_tf_privacy'):
        api.save(rig.root, rig.path, approval, **rig.options)
    assert all((formal / name).read_bytes() == raw for name, raw in before.items())


def test_legacy_saved_receipt_is_held_without_mutating_existing_bytes(rig):
    plan, approval, receipt = saved(rig)
    path = rig.root / plan['formalPath'] / 'receipt.json'
    legacy = {**receipt, 'schemaVersion': 'source-bound-public-tf-save-receipt/v1'}
    write(path, legacy); before = path.read_bytes()
    with pytest.raises(api.GateRequired, match='legacy_source_tf_saved_unit'):
        api.save(rig.root, rig.path, approval, **rig.options)
    assert path.read_bytes() == before


@pytest.mark.parametrize('phase', api.FILES)
def test_interrupted_partial_unit_never_becomes_saved_or_reapplied(rig, phase):
    plan = prepared(rig); approval = approved(rig)
    def interrupt(name):
        if name == phase: raise KeyboardInterrupt('simulated stop')
    with pytest.raises(KeyboardInterrupt): api.save(rig.root, rig.path, approval, failure_hook=interrupt, **rig.options)
    assert not (rig.root / plan['formalPath'] / 'receipt.json').exists()
    with pytest.raises((ValueError, FileNotFoundError)): api.inspect(rig.root, rig.path, **rig.options)
    with pytest.raises((ValueError, FileNotFoundError)): api.save(rig.root, rig.path, approval, **rig.options)


def test_drift_symlink_and_formal_path_escape_refuse(rig):
    plan = prepared(rig)
    rig.snapshots['live-c']['updateTimeExact'] = '2026-10-22T01:00:00.123456789Z'
    with pytest.raises(ValueError, match='drift'): api.inspect(rig.root, rig.path, **rig.options)
    rig.snapshots.update(deepcopy(rig.baseline))
    changed = {**plan, 'formalPath': 'output/sample/questions_json/2026/00_source/forbidden'}
    changed.pop('planHash'); changed = api._seal(changed); write(rig.path, changed)
    write(rig.output / 'preview-receipt.json', {'schemaVersion': api.PLAN, 'planHash': changed['planHash'],
        'formalUnitSaved': False, 'FirestoreWritten': False, 'publicationReady': False})
    with pytest.raises(ValueError, match='path'): api.inspect(rig.root, rig.path, **rig.options)
    write(rig.path, plan)
    write(rig.output / 'preview-receipt.json', {'schemaVersion': api.PLAN, 'planHash': plan['planHash'],
        'formalUnitSaved': False, 'FirestoreWritten': False, 'publicationReady': False})
    alias = rig.private / 'alias.json'; alias.symlink_to(rig.path)
    with pytest.raises(ValueError, match='physical'): api.inspect(rig.root, alias, **rig.options)


def test_actual_normal_checker_clis_cover_target_with_multi_record_source(rig):
    plan, _, _ = saved(rig)
    receipt = api.check(rig.root, rig.path, **rig.options)
    assert len(receipt['commands']) >= 5 and all(c['exitCode'] == 0 for c in receipt['commands'])
    assert '1 ファイル' in receipt['commands'][0]['stdout']
    assert 'coverage check passed' in receipt['commands'][2]['stdout']
    assert receipt['dryRun']['readDocumentIds'] == ['live-a', 'live-b', 'live-c']
    assert receipt['dryRun']['writeDocumentIds'] == ['live-a', 'live-b']


def commit_result(plan):
    payload = api._payload(plan); dry = api.uploader.upload_read_set_maintenance_questions(payload, 'fixture', dry_run=True)
    dry.update(status='committed', writeResults=[{'questionId': i, 'updateTime': '2026-10-22T02:00:00.123456789Z'} for i in dry['writeDocumentIds']])
    return dry


def readback(plan, commit):
    current = deepcopy(plan['public']['baseline']); rows = {r['questionId']: r for r in plan['public']['questions']}
    for entry in commit['writeResults']:
        qid = entry['questionId']; current[qid]['updateTimeExact'] = entry['updateTime']
        for field in plan['public']['writeFieldsByDocument'][qid]: current[qid]['fields'][field] = rows[qid][field]
        current[qid]['fields'].update(updatedAt='2026-10-22T02:00:00.123456789Z', updatedById=api.uploader.UPDATED_BY_ID)
    return current


def test_exact_v3_commit_full_readback_and_readonly_unchanged_are_required(rig):
    plan = prepared(rig); commit = commit_result(plan); current = readback(plan, commit)
    assert set(api._readback(plan, api._payload(plan), commit, current)) == {'live-a', 'live-b'}
    current['live-c']['fields']['explanationText'] = 'third value'
    with pytest.raises(ValueError, match='read-only'): api._readback(plan, api._payload(plan), commit, current)


def test_unknown_commit_partial_results_and_outside_field_changes_refuse(rig):
    plan = prepared(rig); commit = commit_result(plan); current = readback(plan, commit)
    partial = deepcopy(commit); partial['writeResults'].pop()
    with pytest.raises(ValueError, match='coverage'): api._readback(plan, api._payload(plan), partial, current)
    current['live-a']['fields']['questionSetId'] = 'forbidden-category'
    with pytest.raises(ValueError, match='outside-mask'): api._readback(plan, api._payload(plan), commit, current)


def test_unconnected_report_law_and_completion_gates_cannot_be_self_declared(rig, monkeypatch):
    plan, _, _ = saved(rig)
    for gate in ('genuine_report_blind_a_b_challenge_reader_not_connected', 'normal_03b_candidate_bound_reviews_missing'):
        value = deepcopy(plan); value['source']['requiredReviewGates'] = [gate]
        with pytest.raises(api.GateRequired, match=gate): api._publication_gates(rig.root, rig.path, value, rig.coordinator, rig.connection, None)
    with pytest.raises(api.GateRequired, match='completion'): api.complete()


def transport_rig(rig, monkeypatch):
    monkeypatch.setattr(api.uploader, 'upload_read_set_maintenance_questions', rig.original_upload)
    plan, _, receipt = saved(rig)
    gates = {'fixture': 'orchestration only; actual normal gates tested separately',
        'formalReceipt': write(rig.root / plan['formalPath'] / 'receipt.json', api._bytes(receipt)),
        'formalFiles': receipt['files'],
        'evaluationProjection': write(rig.private / 'validated-evaluation-projection.json', {'fixture': 'validated projection'}),
        'evaluationResult': write(rig.private / 'validated-evaluation-result.json', {'fixture': 'validated result'})}
    monkeypatch.setattr(api, '_publication_gates', lambda *a: deepcopy(gates))
    monkeypatch.setattr(api, '_git', lambda *a: 'fixture-committed-head')
    approval = approved(rig, 'publish')
    commit = commit_result(plan)
    calls = []
    def transport(*args, **kwargs):
        calls.append((args, kwargs)); rig.snapshots.update(readback(plan, commit)); return deepcopy(commit)
    monkeypatch.setattr(api.uploader, 'upload_read_set_maintenance_questions', transport)
    return plan, approval, commit, calls


def test_actual_v3_dispatch_readback_and_exact_retry_never_recommit(rig, monkeypatch):
    plan, approval, commit, calls = transport_rig(rig, monkeypatch)
    source_before = Path(rig.request['source']['ref']).read_bytes()
    result = api.publish(rig.root, rig.path, approval, **rig.options)
    assert result['status'] == 'published_readback_matched' and result['FirestoreWritten']
    assert result['completionRecorded'] is False
    assert calls[0][0][0]['schemaVersion'] == api.uploader.READ_SET_TF_TRUTH_SCHEMA
    assert api.publish(rig.root, rig.path, approval, **rig.options) == result
    assert len(calls) == 1
    assert rig.snapshots['live-c'] == rig.baseline['live-c']
    assert Path(rig.request['source']['ref']).read_bytes() == source_before


@pytest.mark.parametrize('phase', ['intent', 'commit'])
def test_publish_interruption_holds_or_reconciles_exact_commit_without_replay(rig, monkeypatch, phase):
    _, approval, _, calls = transport_rig(rig, monkeypatch)
    def stop(name):
        if name == phase: raise KeyboardInterrupt('isolated interruption')
    with pytest.raises(KeyboardInterrupt): api.publish(rig.root, rig.path, approval, failure_hook=stop, **rig.options)
    assert not (rig.path.parent / 'publication/result.json').exists()
    if phase == 'intent':
        with pytest.raises(FileNotFoundError): api.reconcile(rig.root, rig.path, **rig.options)
        assert not calls
    else:
        result = api.reconcile(rig.root, rig.path, **rig.options)
        assert result['FirestoreWritten'] is True and len(calls) == 1


def test_unconfirmed_sdk_commit_never_replays_or_claims_success(rig, monkeypatch):
    _, approval, _, calls = transport_rig(rig, monkeypatch)
    def unconfirmed(*args, **kwargs): calls.append('attempt'); raise RuntimeError('unconfirmed SDK result')
    monkeypatch.setattr(api.uploader, 'upload_read_set_maintenance_questions', unconfirmed)
    with pytest.raises(RuntimeError, match='unconfirmed'): api.publish(rig.root, rig.path, approval, **rig.options)
    with pytest.raises(api.GateRequired, match='commit_unconfirmed'): api.publish(rig.root, rig.path, approval, **rig.options)
    assert len(calls) == 1 and not (rig.path.parent / 'publication/result.json').exists()


def test_task_lifecycle_changes_block_prepare_and_post_readback_result(rig, monkeypatch):
    _, approval, _, calls = transport_rig(rig, monkeypatch)
    api.publish(rig.root, rig.path, approval, **rig.options)
    result = rig.path.parent / 'publication/result.json'; before = result.read_bytes()
    snapshot = api._snapshot
    def cancelled(request):
        current = snapshot(request)
        rig.connection.execute("UPDATE improvement_tasks SET status='blocked',updated_at='changed'")
        rig.connection.commit()
        return current
    monkeypatch.setattr(api, '_snapshot', cancelled)
    with pytest.raises(ValueError, match='lifecycle'): api.reconcile(rig.root, rig.path, **rig.options)
    assert len(calls) == 1 and result.read_bytes() == before


@pytest.mark.parametrize('when', ['last_git', 'last_native', 'evaluation_ref', 'authority_source'])
def test_final_prewrite_guard_refuses_lifecycle_race_before_transport(rig, monkeypatch, when):
    _, approval, _, calls = transport_rig(rig, monkeypatch)
    def block():
        rig.connection.execute("UPDATE improvement_tasks SET status='blocked',updated_at='prewrite-race'")
        rig.connection.commit()
    git_calls = []
    def git(*args):
        git_calls.append('git')
        if when == 'last_git' and len(git_calls) == 3: block()
        return 'fixture-committed-head'
    monkeypatch.setattr(api, '_git', git)
    if when in {'last_native', 'evaluation_ref', 'authority_source'}:
        graph = api._native_graph
        def native_graph(*args):
            import inspect
            value = graph(*args)
            if len(git_calls) >= 3 and inspect.currentframe().f_back.f_code.co_name == '_terminal_publication_guard':
                if when == 'last_native': block()
                elif when == 'evaluation_ref': write(rig.private / 'validated-evaluation-result.json', {'changed': True})
                else:
                    rig.connection.execute("UPDATE ai_question_candidates SET text_hash='changed-authority-source'")
                    rig.connection.commit()
            return value
        monkeypatch.setattr(api, '_native_graph', native_graph)
    with pytest.raises(ValueError): api.publish(rig.root, rig.path, approval, **rig.options)
    assert calls == [] and not (rig.path.parent / 'publication/commit.json').exists()
    assert not (rig.path.parent / 'publication/result.json').exists()


def test_terminal_file_io_cannot_hide_unit_native_approval_revocation(rig, monkeypatch):
    _, approval, _, calls = transport_rig(rig, monkeypatch)
    unit_approval = rig.private / 'save-audit/approval.json'
    journal = Path(api._json(unit_approval.read_bytes())['reviewerFinal']['sessionPath'])
    unchanged = api._unchanged
    def revoke_after_hash(root, files):
        import inspect
        unchanged(root, files)
        if inspect.currentframe().f_back.f_code.co_name == '_terminal_publication_guard':
            event = {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': 'ファイル照合中の承認撤回'}}
            journal.write_bytes(journal.read_bytes() + (json.dumps(event, ensure_ascii=False) + '\n').encode())
    monkeypatch.setattr(api, '_unchanged', revoke_after_hash)
    with pytest.raises(ValueError, match='native session changed'): api.publish(rig.root, rig.path, approval, **rig.options)
    assert calls == [] and not (rig.path.parent / 'publication/commit.json').exists()
    assert not (rig.path.parent / 'publication/result.json').exists()


@pytest.mark.parametrize('artifact', ['commit.json', 'intent.json', 'attempt.json', 'approval.json'])
def test_reconcile_pins_original_publication_bytes_across_readback(rig, monkeypatch, artifact):
    _, approval, _, calls = transport_rig(rig, monkeypatch)
    def stop(name):
        if name == 'commit': raise KeyboardInterrupt('commit saved, result not yet recorded')
    with pytest.raises(KeyboardInterrupt): api.publish(rig.root, rig.path, approval, failure_hook=stop, **rig.options)
    target = approval if artifact == 'approval.json' else rig.path.parent / 'publication' / artifact
    snapshot = api._snapshot
    def drift(request):
        current = snapshot(request)
        value = api._json(target.read_bytes())
        if artifact == 'commit.json': value['writeResults'][0]['updateTime'] = '2026-10-22T09:00:00.123456789Z'
        else: value['unexpectedDrift'] = True
        write(target, value)
        return current
    monkeypatch.setattr(api, '_snapshot', drift)
    with pytest.raises(ValueError): api.reconcile(rig.root, rig.path, **rig.options)
    assert len(calls) == 1 and not (rig.path.parent / 'publication/result.json').exists()


def test_manifest_missing_member_and_canonical_document_drift_refuse(rig):
    manifest = rig.root / api.MANIFEST
    with manifest.open('ab') as stream:
        stream.write((json.dumps({'path': 'output/sample/questions_json/2026/00_source/missing.json', 'sha256': '0' * 64}) + '\n').encode())
    with pytest.raises(ValueError, match='manifest membership'): prepared(rig)
    assert not rig.path.exists()


def test_actual_canonical_docs_are_pinned_not_removed_documents_alias(rig, monkeypatch):
    relative = 'prompt/01_prompt_fix_questionType.md'
    monkeypatch.setattr(rig.coordinator.workflow, 'versioned_policies',
        lambda *a: {'question_type': {'canonicalDocs': [relative]}})
    plan = prepared(rig)
    assert relative in plan['inputFiles']
    with (rig.root / relative).open('ab') as stream: stream.write(b'\nchanged input\n')
    with pytest.raises(ValueError): api.inspect(rig.root, rig.path, **rig.options)


def test_git_requires_actual_committed_formal_blobs_even_when_ignored_clean(rig):
    import subprocess
    plan, _, _ = saved(rig)
    for args in (['init', '--quiet'], ['config', 'user.email', 'fixture@example.test'], ['config', 'user.name', 'Fixture']):
        subprocess.run(['git', *args], cwd=rig.root, check=True)
    (rig.root / '.gitignore').write_text('output/\n')
    subprocess.run(['git', 'add', '.'], cwd=rig.root, check=True, capture_output=True)
    subprocess.run(['git', 'commit', '--quiet', '-m', 'isolated test inputs'], cwd=rig.root, check=True)
    subprocess.run(['git', 'update-ref', 'refs/remotes/origin/main', 'HEAD'], cwd=rig.root, check=True)
    with pytest.raises(api.GateRequired, match='committed_blob'): api._git(rig.root, plan)


def test_git_preserves_manifest_managed_ignored_sources_and_requires_formal_blobs(rig):
    import subprocess
    plan, _, _ = saved(rig)
    for args in (['init', '--quiet'], ['config', 'user.email', 'fixture@example.test'], ['config', 'user.name', 'Fixture']):
        subprocess.run(['git', *args], cwd=rig.root, check=True)
    (rig.root / '.gitignore').write_text('output/\n')
    subprocess.run(['git', 'add', '.'], cwd=rig.root, check=True, capture_output=True)
    patches = [p for p in plan['inputFiles'] if p.startswith('output/sample/questions_json/') and '/00_source/' not in p]
    formal = [str((rig.root / plan['formalPath'] / name).relative_to(rig.root)) for name in (*api.FILES, 'receipt.json')]
    subprocess.run(['git', 'add', '-f', *patches, *formal], cwd=rig.root, check=True)
    subprocess.run(['git', 'commit', '--quiet', '-m', 'isolated committed patch and unit'], cwd=rig.root, check=True)
    subprocess.run(['git', 'update-ref', 'refs/remotes/origin/main', 'HEAD'], cwd=rig.root, check=True)
    source = Path(rig.request['source']['ref'])
    tracked = subprocess.run(['git', 'ls-files', str(source.relative_to(rig.root))], cwd=rig.root, check=True, capture_output=True)
    assert tracked.stdout == b'' and api._git(rig.root, plan)
    original = source.read_bytes(); source.write_bytes(original + b' ')
    with pytest.raises(ValueError): api._git(rig.root, plan)
    source.write_bytes(original)
    receipt = rig.root / plan['formalPath'] / 'receipt.json'
    receipt.write_bytes(receipt.read_bytes() + b' ')
    with pytest.raises(ValueError): api._git(rig.root, plan)


@pytest.mark.parametrize('updated', [False, True])
def test_normal_03b_reuses_candidate_bound_actual_writer_reviews(updated):
    from tools.question_review_console.law_audit_verification import required_review_count
    status = 'updated_to_current_law' if updated else 'same_as_current'
    fields = {'auditStatus': status, 'correctChoiceText': ['間違い'], 'currentLawDecision': ['間違い'],
        'examTimeDecision': ['正しい'], 'lawRevisionFacts': [{'auditStatus': status,
            'examTime': {'correctChoiceText': '正しい'}, 'current': {'correctChoiceText': '間違い'}}]}
    bundle = {'question': {'questionId': 'q1'}, 'proposedFields': fields}
    bundle['evidenceHash'] = api.sha256_json(bundle)
    reviews = []
    for n in range(required_review_count(fields)):
        reviews.append({'phase': 'secondary' if n == 0 else 'tertiary', 'executionId': f'reviewer-{n}:turn-{n}',
            'threadId': f'reviewer-{n}', 'turnId': f'turn-{n}', 'model': 'normal-profile',
            'review': {'schemaVersion': 'law-audit-verification/v1', 'questionId': 'q1', 'evidenceHash': bundle['evidenceHash'],
                'decision': 'approve', 'examTimeDecision': ['正しい'], 'currentLawDecision': ['間違い'],
                'issues': [], 'verifiedSourceUrls': ['https://laws.e-gov.go.jp/law/fixture']}})
    plan = {'request': {'questionId': 'q1'}, 'preparedCandidate': {'content': {'executionMetadata': {
        'lawAuditVerification': {'q1': {'evidenceHash': bundle['evidenceHash'], 'bundle': bundle, 'reviews': reviews}}}}}}
    child = {'threadId': 'primary-thread', 'lawAuditReviewExecutions': deepcopy(reviews)}
    result = api._normal_law_verification(plan, child)
    assert result['reviewCount'] == (2 if updated else 1)
    child['lawAuditReviewExecutions'][0]['threadId'] = 'forged-other-execution'
    with pytest.raises(ValueError, match='executions differ'): api._normal_law_verification(plan, child)
    child['lawAuditReviewExecutions'] = deepcopy(reviews)
    bundle['proposedFields']['currentLawDecision'] = ['正しい']
    with pytest.raises(ValueError, match='evidence hash'): api._normal_law_verification(plan, child)


def test_checks_sealed_exit_zero_receipt_still_runs_real_commands(rig, monkeypatch):
    plan, _, _ = saved(rig)
    api.check(rig.root, rig.path, **rig.options)
    def refuse(*args): raise RuntimeError('actual checker refused')
    monkeypatch.setattr(api, '_run_command', refuse)
    with pytest.raises(RuntimeError, match='actual checker refused'): api._verify_checks(rig.root, rig.path, plan, rerun=False)


@pytest.mark.parametrize('mutation', ['same_major_old_fingerprint', 'invalid_session', 'author_thread'])
def test_evaluation_status_cannot_replace_exact_persisted_policy_and_origin(rig, monkeypatch, mutation):
    plan, _, _ = saved(rig)
    monkeypatch.setattr(api, '_verify_checks', lambda *a, **k: {'checksHash': 'checked'})
    monkeypatch.setattr(api, '_question', lambda *a, **k: {'qualification': 'sample'})
    committed = {'status': 'passed', 'resultHash': 'result-hash', 'policyVersion': '1.2.0',
        'policyFingerprint': 'current-policy', 'threadId': 'independent-evaluation-thread', 'sessionId': 'eval-session'}
    if mutation == 'same_major_old_fingerprint': committed['policyFingerprint'] = 'old-same-major-policy'
    if mutation == 'author_thread': committed['threadId'] = 'source-author-thread'
    evaluation = SimpleNamespace(status_for=lambda q: {'status': 'passed', 'publishReady': True, 'machineReady': True,
        'resultHash': 'result-hash', 'policyFingerprint': 'current-policy'},
        store=SimpleNamespace(load_projection=lambda q: {'effectiveResult': 'stored'}),
        _committed_attempt_result=lambda q, p: committed,
        current_policy=lambda **k: {'policyVersion': '1.2.0', 'policyFingerprint': 'current-policy'},
        _session_receipt_valid=lambda *a: mutation != 'invalid_session')
    rig.coordinator.store = SimpleNamespace(get=lambda *a: {'threadId': 'source-author-thread'})
    with pytest.raises(api.GateRequired, match='exact_current_policy|independent_normal_session'):
        api._publication_gates(rig.root, rig.path, plan, rig.coordinator, rig.connection, evaluation)


def test_publication_gate_pins_the_validated_formal_and_evaluation_receipts(rig, monkeypatch):
    plan, _, receipt = saved(rig)
    monkeypatch.setattr(api, '_verify_checks', lambda *a, **k: {'checksHash': 'checked'})
    committed = {'status': 'passed', 'resultHash': 'result-hash', 'runId': 'eval-run', 'policyVersion': '1.2.0',
        'policyFingerprint': 'current-policy', 'threadId': 'independent-evaluation-thread', 'sessionId': 'eval-session'}
    projection = rig.private / 'evaluation-projection.json'; write(projection, {'effective': 'validated'})
    runs = rig.private / 'evaluation-runs'; write(runs / 'sample/eval-run/result.json', committed)
    evaluation = SimpleNamespace(status_for=lambda q: {'status': 'passed', 'publishReady': True, 'machineReady': True,
        'resultHash': 'result-hash'}, store=SimpleNamespace(load_projection=lambda q: {'validated': True}, evaluation_path=lambda q: projection),
        run_store=SimpleNamespace(root=runs), _committed_attempt_result=lambda q, p: committed,
        current_policy=lambda **k: {'policyVersion': '1.2.0', 'policyFingerprint': 'current-policy'},
        _session_receipt_valid=lambda *a: True)
    rig.coordinator.store = SimpleNamespace(get=lambda *a: {'threadId': 'source-author-thread'})
    gates = api._publication_gates(rig.root, rig.path, plan, rig.coordinator, rig.connection, evaluation)
    assert gates['formalFiles'] == receipt['files']
    assert gates['formalReceipt']['sha256'] == api._hash((rig.root / plan['formalPath'] / 'receipt.json').read_bytes())
    assert gates['evaluationResult']['sha256'] == api._hash((runs / 'sample/eval-run/result.json').read_bytes())


def test_native_unit_revocation_during_check_io_refuses_receipt(rig, monkeypatch):
    _, approval, _ = saved(rig)
    journal = Path(api._json(approval.read_bytes())['reviewerFinal']['sessionPath'])
    original = api._run_command
    def revoke(root, argv):
        result = original(root, argv)
        journal.write_bytes(journal.read_bytes() + api._bytes({'type': 'event_msg', 'payload': {'type': 'user_message', 'message': '撤回'}}))
        return result
    monkeypatch.setattr(api, '_run_command', revoke)
    with pytest.raises(ValueError, match='native|approval'): api.check(rig.root, rig.path, **rig.options)
    assert not (rig.path.parent / 'checks/receipt.json').exists()


def test_normal_evaluation_prompt_separates_public_body_official_body_and_selected_answer(rig):
    from tools.question_review_console.evaluation import QuestionEvaluationService
    plan = prepared(rig)
    question = api._question(rig.root, rig.path, plan, rig.coordinator, rig.connection)
    service = QuestionEvaluationService(rig.root, 'fixture', result_runner=lambda prompt: None)
    prompt = service._build_prompt(question)
    payload = json.loads(prompt.split('```json\n')[-1].split('\n```')[0])
    assert payload['questionBodyText'] == '次の命題は正しいか。'
    assert payload['originalQuestionBodyText'] == rig.source['questionBodyText']
    assert payload['officialSelectedAnswer'] == rig.request['officialAnswer']
    assert 'correctChoiceText' not in payload
    assert 'sourceAnswerEvidence' not in payload
    ordinary = deepcopy(question)
    for key in ('sourceTfUnit', 'sourceTfOriginalBody', 'sourceTfOfficialAnswer'): ordinary.pop(key)
    normal_payload = json.loads(service._build_prompt(ordinary).split('```json\n')[-1].split('\n```')[0])
    assert 'officialSelectedAnswer' not in normal_payload and 'originalQuestionBodyText' not in normal_payload


def test_explicit_evaluation_dispatch_replays_saved_unit_and_rejects_projected_drift(rig, monkeypatch):
    from tools.question_review_console.scoped_artifacts import validate_scoped_question
    plan, _, _ = saved(rig)
    question = api._question(rig.root, rig.path, plan, rig.coordinator, rig.connection, checked=True)
    question['sourceTfLedger'] = api.normal._ledger_path(rig.connection)
    monkeypatch.setattr(api.normal, 'coordinator_for', lambda *a: rig.coordinator)
    monkeypatch.setattr(api, '_verify_checks', lambda *a, **k: {'fixture': 'dispatch only'})
    assert validate_scoped_question(rig.root, question) is None
    question['projected']['correctChoiceText'] = ['正しい'] * 3
    with pytest.raises(ValueError, match='evaluation unit scope differs:projected'):
        validate_scoped_question(rig.root, question)


def test_later_normal_law_owned_patch_history_has_explicit_jsonl_shape(tmp_path):
    # A normal 03b sidecar is JSONL, unlike source/10/15/23 record arrays.
    # Keep this fixture explicit; arbitrary source JSON must not become JSONL.
    row = {'schemaVersion': 'law-revision-audit/v2', 'reviewQuestionId': 'q1',
        'sourceQuestionKey': 'sample:2026:q1', 'sourceRecordRef': 'question.json#0'}
    sidecar = tmp_path / '2026_law_revision_audit.jsonl'
    rows = [row, {**row, 'reviewQuestionId': 'q2', 'sourceQuestionKey': 'sample:2026:q2'}]
    write(sidecar, ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows).encode())
    # The normal artifact parser is implemented with the history replay helper.
    assert api._patch_rows(sidecar, sidecar.read_bytes())[0] == row


# Completion tests use the same real intent/attempt/native publication flow as
# the transport tests. Only the expensive normal-stage/evaluation boundary is
# fixture-injected; no success JSON substitutes for the completion verifier.
def published_completion(rig, monkeypatch):
    plan, approval, commit, calls = transport_rig(rig, monkeypatch)
    api.publish(rig.root, rig.path, approval, **rig.options)
    artifact = api.completion(rig.root, rig.path, **rig.options)
    path = rig.output / 'completion.json'
    return plan, artifact, path, api._hash(path.read_bytes()), calls


@pytest.mark.parametrize('mutation', ['raw_readonly_identity', 'git_projection', 'git_receipt'])
def test_privacy_publication_and_completion_retain_exact_raw_readset_and_formal_bindings(rig, monkeypatch, mutation):
    actor, comment = privacy_inputs(rig)
    plan, artifact, path, digest, calls = published_completion(rig, monkeypatch)
    result = json.loads((rig.output / 'publication/result.json').read_bytes())
    assert result['liveReadback']['live-c']['fields']['updatedById'] == actor
    assert json.loads(rig.path.read_bytes())['public']['baseline']['live-a']['fields']['updatedById'] == actor
    assert result['planHash'] == plan['planHash']
    assert artifact['plan']['sha256'] == api._hash(rig.path.read_bytes())
    assert len(calls) == 1
    if mutation == 'raw_readonly_identity': rig.snapshots['live-c']['fields']['updatedById'] = actor + '-changed'
    elif mutation == 'git_projection':
        formal = rig.root / plan['formalPath'] / 'public.json'; value = json.loads(formal.read_bytes())
        value['privateValueHash'] = '0' * 64; write(formal, value)
    else:
        formal = rig.root / plan['formalPath'] / 'receipt.json'; value = json.loads(formal.read_bytes())
        value['privacyBinding']['privateReceiptBindingHash'] = '0' * 64; write(formal, value)
    with pytest.raises(ValueError): api.record_completion(rig.root, rig.path, path, digest, **rig.options)
    assert rig.connection.execute('SELECT status FROM improvement_tasks').fetchone()[0] == 'in_progress'
    assert rig.connection.execute('SELECT COUNT(*) FROM improvement_result_history').fetchone()[0] == 0


def test_improvement_completion_records_and_exact_replay_recovers_receipt_interruption(rig, monkeypatch):
    from tools.question_bank import feedback_daily as daily
    plan, artifact, path, digest, calls = published_completion(rig, monkeypatch)
    assert plan['schemaVersion'] == 'source-bound-public-tf-plan/v3'
    assert api._scope(rig.root, rig.path, plan, 'save')['completionContract'] == api._private_commitment(artifact['completionContract'])
    def stopped(stage):
        assert stage == 'database_committed'
        raise KeyboardInterrupt('receipt interrupted')
    with pytest.raises(KeyboardInterrupt):
        api.record_completion(rig.root, rig.path, path, digest, failure_hook=stopped, **rig.options)
    assert rig.connection.execute('SELECT status FROM improvement_tasks').fetchone()[0] == 'completed'
    assert not (rig.output / 'completion-ledger-readback.json').exists()
    result = api.record_completion(rig.root, rig.path, path, digest, **rig.options)
    assert api.record_completion(rig.root, rig.path, path, digest, **rig.options) == result
    assert api.completion(rig.root, rig.path, **rig.options) == artifact
    assert result['resultId'] == 1 and result['history']['artifact_sha256'] == digest
    assert rig.connection.execute('SELECT COUNT(*) FROM improvement_result_history').fetchone()[0] == 1
    assert len(calls) == 1 and daily.daily_summary(rig.connection)['improvementTasksOpen'] == 0


@pytest.mark.parametrize('mutation', ['readonly', 'outside', 'version', 'partial', 'commit', 'result', 'policy', 'stage', 'withdrawn', 'authority'])
def test_completion_rejects_publication_native_input_or_readback_drift(rig, monkeypatch, mutation):
    from tools.question_bank import feedback_source_tf_completion as complete
    _, artifact, path, _, _ = published_completion(rig, monkeypatch)
    if mutation == 'readonly': rig.snapshots['live-c']['fields']['questionBodyText'] = 'changed'
    elif mutation == 'outside': rig.snapshots['live-a']['fields']['questionSetId'] = 'changed'
    elif mutation == 'version': rig.snapshots['live-a']['updateTimeExact'] = '2026-10-23T00:00:00.000000001Z'
    elif mutation == 'partial': rig.snapshots.pop('live-c')
    elif mutation in {'commit', 'result'}:
        file = rig.output / 'publication' / (mutation + '.json')
        value = api._json(file.read_bytes()); value['foreign'] = True; write(file, value)
    elif mutation == 'policy':
        file = rig.root / 'tools/question_bank/feedback_source_tf_delivery.py'; file.write_bytes(file.read_bytes() + b'\n# changed policy input\n')
    elif mutation == 'stage':
        file = Path(rig.request['stageProofs'][0]['plan']['ref']); file.write_bytes(file.read_bytes() + b' ')
    elif mutation == 'withdrawn':
        # The public approval file is hash bound into the publication intent;
        # changing its decision is a real revocation, not a JSON success flag.
        file = rig.private / 'publish-audit/approval.json'; value = api._json(file.read_bytes()); value['decision'] = 'rejected'; write(file, value)
    else: artifact['authority']['taskId'] = 'foreign'
    with pytest.raises((ValueError, api.GateRequired, KeyError)):
        complete.verify_completion(rig.root, artifact, connection=rig.connection, artifact_ref=path, coordinator=rig.coordinator)
    assert rig.connection.execute('SELECT status FROM improvement_tasks').fetchone()[0] == 'in_progress'


@pytest.mark.parametrize('boundary', ['before_insert', 'after_insert'])
def test_completion_terminal_invalidations_rollback_improvement(rig, monkeypatch, boundary):
    from tools.question_bank import feedback_source_tf_completion as complete
    from tools.question_bank import feedback_daily as daily
    _, artifact, path, digest, _ = published_completion(rig, monkeypatch)
    real = complete.verify_completion
    calls = []
    def wrapped(*args, **kwargs):
        proof = real(*args, **kwargs)
        original = proof.assert_current
        def terminal(connection):
            inserted = connection.execute('SELECT COUNT(*) FROM improvement_result_history').fetchone()[0]
            if (boundary == 'before_insert' and not inserted) or (boundary == 'after_insert' and inserted):
                calls.append(boundary)
                connection.execute("UPDATE improvement_tasks SET need='changed' WHERE task_id='task'")
            original(connection)
        proof.assert_current = terminal
        return proof
    monkeypatch.setattr(complete, 'verify_completion', wrapped)
    with pytest.raises(ValueError):
        daily.record_improvement_publication(rig.connection, task_id='task', expected_status='in_progress', reason=artifact['completionContract']['reason'],
            artifact_ref=path, artifact_sha256=digest, repo_root=rig.root, source_tf_options={'coordinator':rig.coordinator})
    assert calls
    assert rig.connection.execute('SELECT need,status FROM improvement_tasks').fetchone()[:] == ('命題と表示の整合','in_progress')
    assert rig.connection.execute('SELECT COUNT(*) FROM improvement_result_history').fetchone()[0] == 0


def report_rig(rig, monkeypatch, *, cases=('case-a',), undecided=()):
    from tools.question_bank import feedback_source_tf_report_review as review
    db = rig.connection
    db.execute("INSERT INTO report_tasks(task_id,source_path,report_id,question_id,received_at,categories_json,case_ids_json,case_statuses_json,intake_status,decision,proposal_hash,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ('report-task','fixture-report','report','live-a','fixture',json.dumps(['correct_answer']),json.dumps(list(cases)),json.dumps({c:'open' for c in cases}),'complete',None,'proposal','fixture','fixture'))
    db.executemany('INSERT INTO report_task_cases VALUES(?,?)', [('report-task',c) for c in cases])
    db.executemany('INSERT INTO report_case_decisions VALUES(?,?,?,?,?,?)',
        [('report-task',c,'fix_required','checked','private-ref','fixture') for c in cases if c not in undecided]); db.commit()
    rig.request.update(schemaVersion=review.REPORT_REQUEST, authority={'kind':'report','taskId':'report-task','caseId':cases[0]}, reportReview={'fixture':'real reader verified independently'})
    write(rig.request_path,rig.request)
    def verify(root, reference, request, source, public, connection, files):
        return {'fixtureReader':'current independently verified gate','binding':deepcopy(request['authority'])}
    monkeypatch.setattr(review, 'verify', verify)


def test_report_completion_keeps_intake_decision_proposal_and_records_append_only_fact(rig, monkeypatch):
    from tools.question_bank import feedback_daily as daily
    report_rig(rig, monkeypatch)
    before = dict(rig.connection.execute('SELECT * FROM report_tasks').fetchone())
    from tools.question_bank.feedback_source_tf_report_review import REPORT_REQUEST
    plan, artifact, path, digest, calls = published_completion(rig, monkeypatch)
    assert plan['request']['schemaVersion'] == REPORT_REQUEST
    contract = plan['source']['completionContract']
    assert contract is not None and contract['authority'] == rig.request['authority']
    assert api._scope(rig.root, rig.path, plan, 'save')['completionContract'] == api._private_commitment(contract)
    intent = api._json((rig.output / 'publication/intent.json').read_bytes())
    assert intent['scope']['action'] == 'publish'
    assert intent['scope']['completionContract'] == api._private_commitment(contract)
    assert artifact['completionContract'] == contract
    result = api.record_completion(rig.root, rig.path, path, digest, **rig.options)
    assert api.record_completion(rig.root, rig.path, path, digest, **rig.options) == result
    assert api.completion(rig.root, rig.path, **rig.options) == artifact
    after = dict(rig.connection.execute('SELECT * FROM report_tasks').fetchone())
    assert after['published_at'] is not None
    assert {k:v for k,v in after.items() if k != 'published_at'} == {k:v for k,v in before.items() if k != 'published_at'}
    assert result['reportParentPublished'] is True and len(calls) == 1
    assert daily.daily_summary(rig.connection)['sourceTfReportCasesPublished'] == 1
    # Parent decision is intentionally untouched/undecided: intake and the
    # individual publication fact remain visible separately.
    listed = daily.list_report_tasks(rig.connection, limit=10)[0]
    assert listed['casePublications'][0]['case_id'] == 'case-a'
    assert listed['caseStatuses'] == {'case-a':'open'}
    for sql in ('DELETE FROM source_tf_report_publication_history','UPDATE source_tf_report_publication_history SET reason=reason'):
        with pytest.raises(sqlite3.IntegrityError, match='append-only'): rig.connection.execute(sql)
    rig.connection.rollback()


def test_report_partial_completion_remains_open_and_receipt_interruption_is_resumable(rig, monkeypatch):
    report_rig(rig, monkeypatch, cases=('case-a','case-b'), undecided=('case-b',))
    _, artifact, path, digest, _ = published_completion(rig, monkeypatch)
    assert artifact['completionContract']['parentMayComplete'] is False
    def stop(stage): raise KeyboardInterrupt('after DB commit')
    with pytest.raises(KeyboardInterrupt): api.record_completion(rig.root, rig.path, path, digest, failure_hook=stop, **rig.options)
    result = api.record_completion(rig.root, rig.path, path, digest, **rig.options)
    assert result['reportParentPublished'] is False
    assert rig.connection.execute('SELECT published_at FROM report_tasks').fetchone()[0] is None
    assert rig.connection.execute('SELECT COUNT(*) FROM source_tf_report_publication_history').fetchone()[0] == 1


@pytest.mark.parametrize('mutation', ['new_case','proposal','decision','fake_proof','foreign_case'])
def test_report_completion_rejects_changed_or_fabricated_authority(rig, monkeypatch, mutation):
    from tools.question_bank import feedback_daily as daily
    from tools.question_bank import feedback_source_tf_completion as complete
    report_rig(rig, monkeypatch)
    _, artifact, path, digest, _ = published_completion(rig, monkeypatch)
    case = 'case-a'
    if mutation == 'new_case': rig.connection.execute("UPDATE report_tasks SET case_ids_json='[\"case-a\",\"new\"]'")
    elif mutation == 'proposal': rig.connection.execute("UPDATE report_tasks SET proposal_hash='changed'")
    elif mutation == 'decision': rig.connection.execute("UPDATE report_case_decisions SET decided_at='changed'")
    elif mutation == 'fake_proof': monkeypatch.setattr(complete,'verify_completion',lambda *a,**k:{'liveReadbackMatched':True})
    elif mutation == 'foreign_case': case = 'foreign'
    rig.connection.commit()
    with pytest.raises(ValueError): daily.record_source_tf_report_publication(rig.connection, task_id='report-task',case_id=case,
        artifact_ref=path,artifact_sha256=digest,repo_root=rig.root,coordinator=rig.coordinator)
    assert rig.connection.execute('SELECT COUNT(*) FROM source_tf_report_publication_history').fetchone()[0] == 0


@pytest.mark.parametrize('boundary', ['before_insert','after_insert'])
def test_report_completion_rechecks_inside_transaction_and_rolls_back(rig, monkeypatch, boundary):
    from tools.question_bank import feedback_daily as daily
    from tools.question_bank import feedback_source_tf_completion as complete
    report_rig(rig, monkeypatch)
    _, _, path, digest, _ = published_completion(rig, monkeypatch)
    real = complete.verify_completion
    def wrapped(*args, **kwargs):
        proof = real(*args, **kwargs); original = proof.assert_current
        def terminal(connection):
            count = connection.execute('SELECT COUNT(*) FROM source_tf_report_publication_history').fetchone()[0]
            if (boundary == 'before_insert' and not count) or (boundary == 'after_insert' and count):
                connection.execute("UPDATE report_tasks SET proposal_hash='changed'")
            original(connection)
        proof.assert_current = terminal
        return proof
    monkeypatch.setattr(complete,'verify_completion',wrapped)
    with pytest.raises(ValueError): daily.record_source_tf_report_publication(rig.connection,task_id='report-task',case_id='case-a',
        artifact_ref=path,artifact_sha256=digest,repo_root=rig.root,coordinator=rig.coordinator)
    assert rig.connection.execute('SELECT proposal_hash,published_at FROM report_tasks').fetchone()[:] == ('proposal',None)
    assert rig.connection.execute('SELECT COUNT(*) FROM source_tf_report_publication_history').fetchone()[0] == 0


def test_report_sequential_other_case_decision_then_publication_closes_parent(rig, monkeypatch):
    from tools.question_bank import feedback_daily as daily
    from tools.question_bank import feedback_source_tf_completion as complete
    report_rig(rig, monkeypatch, cases=('case-a','case-b'), undecided=('case-b',))
    _, _, first_path, first_digest, _ = published_completion(rig, monkeypatch)
    first = api.record_completion(rig.root, rig.path, first_path, first_digest, **rig.options)
    assert not first['reportParentPublished']
    # A changed aggregate needs a fresh scope; the old stage is never rewritten.
    rig.connection.execute('INSERT INTO report_case_decisions VALUES(?,?,?,?,?,?)',
        ('report-task','case-b','fix_required','checked separately','private-ref-b','later'))
    rig.connection.execute("UPDATE report_tasks SET decision='fix_required',case_statuses_json=?",
        (json.dumps({'case-a':'under_review','case-b':'under_review'}),))
    rig.connection.commit()
    actual = complete._actual_lifecycle(rig.connection,rig.request)
    assert complete.parent_can_complete(rig.connection,{'authority':{'kind':'report','taskId':'report-task','caseId':'case-b'}},actual)
    with pytest.raises(ValueError):
        api.completion(rig.root,rig.path,**rig.options)
    # Fresh normal-stage boundary fixture with an actual distinct delta: the
    # second publication must not infer completion from an all-no-op write.
    rig.private = rig.private / 'second-case'; rig.private.mkdir()
    rig.output = rig.private / 'unit'; rig.path = rig.output / 'plan.json'
    rig.request_path = rig.private / 'request.json'
    rig.request['authority']['caseId'] = 'case-b'
    rig.current['explanationText'][1] = '正しい。命題Bの根拠を補足する。'
    rig.request['public'][1]['explanationText'] = rig.current['explanationText'][1]
    write(rig.request_path,rig.request)
    _, _, second_path, second_digest, _ = published_completion(rig,monkeypatch)
    second = api.record_completion(rig.root,rig.path,second_path,second_digest,**rig.options)
    assert second['reportParentPublished']
    assert rig.connection.execute('SELECT COUNT(*) FROM source_tf_report_publication_history').fetchone()[0] == 2
    assert daily.daily_summary(rig.connection)['sourceTfReportCasesPublished'] == 2
    assert api.record_completion(rig.root,rig.path,second_path,second_digest,**rig.options) == second


def test_completion_rejects_revoked_native_at_transaction_terminal(rig, monkeypatch):
    from tools.question_bank import feedback_source_tf_completion as complete
    from tools.question_bank import feedback_daily as daily
    _, artifact, path, digest, _ = published_completion(rig,monkeypatch)
    journal = Path(api._json((rig.private/'publish-audit/approval.json').read_bytes())['reviewerFinal']['sessionPath'])
    real = complete.verify_completion
    def wrapped(*args,**kwargs):
        proof=real(*args,**kwargs); original=proof.assert_current
        def terminal(connection):
            if connection.execute('SELECT COUNT(*) FROM improvement_result_history').fetchone()[0]:
                event={'type':'event_msg','payload':{'type':'user_message','message':'withdrawn'}}
                journal.write_bytes(journal.read_bytes()+(json.dumps(event)+'\n').encode())
            original(connection)
        proof.assert_current=terminal
        return proof
    monkeypatch.setattr(complete,'verify_completion',wrapped)
    with pytest.raises(ValueError,match='native session changed'):
        daily.record_improvement_publication(rig.connection,task_id='task',expected_status='in_progress',reason=artifact['completionContract']['reason'],
            artifact_ref=path,artifact_sha256=digest,repo_root=rig.root,source_tf_options={'coordinator':rig.coordinator})
    assert rig.connection.execute('SELECT status FROM improvement_tasks').fetchone()[0]=='in_progress'
    assert rig.connection.execute('SELECT COUNT(*) FROM improvement_result_history').fetchone()[0]==0


def test_completion_cannot_import_old_plan_or_success_flags(rig,monkeypatch):
    from tools.question_bank import feedback_source_tf_completion as complete
    _,artifact,path,_,_=published_completion(rig,monkeypatch)
    changed=deepcopy(artifact); changed['liveReadbackMatched']=True
    with pytest.raises(ValueError): complete.verify_completion(rig.root,changed,connection=rig.connection,artifact_ref=path,coordinator=rig.coordinator)
    plan=api._json(rig.path.read_bytes()); plan['schemaVersion']='source-bound-public-tf-plan/v1'
    write(rig.path,api._seal(plan, 'planHash'))
    with pytest.raises(ValueError): api.completion(rig.root,rig.path,**rig.options)


@pytest.mark.parametrize('changed', ['policy','reader'])
def test_report_completion_rolls_back_late_policy_or_reader_invalidation(rig,monkeypatch,changed):
    from tools.question_bank import feedback_daily as daily
    from tools.question_bank import feedback_source_tf_completion as complete
    from tools.question_bank import feedback_source_tf_report_review as review
    report_rig(rig,monkeypatch)
    _,_,path,digest,_=published_completion(rig,monkeypatch)
    real=complete.verify_completion
    def wrapped(*args,**kwargs):
        proof=real(*args,**kwargs); original=proof.assert_current
        def terminal(connection):
            if connection.execute('SELECT COUNT(*) FROM source_tf_report_publication_history').fetchone()[0]:
                if changed=='policy':
                    source=rig.root/'tools/question_bank/feedback_source_tf_delivery.py'
                    source.write_bytes(source.read_bytes()+b'\n# changed maintenance policy input\n')
                else:
                    def revoked(*a,**k): raise ValueError('report reader no longer current')
                    monkeypatch.setattr(review,'verify',revoked)
            original(connection)
        proof.assert_current=terminal
        return proof
    monkeypatch.setattr(complete,'verify_completion',wrapped)
    with pytest.raises(ValueError): daily.record_source_tf_report_publication(rig.connection,task_id='report-task',case_id='case-a',
        artifact_ref=path,artifact_sha256=digest,repo_root=rig.root,coordinator=rig.coordinator)
    assert rig.connection.execute('SELECT published_at FROM report_tasks').fetchone()[0] is None
    assert rig.connection.execute('SELECT COUNT(*) FROM source_tf_report_publication_history').fetchone()[0]==0


def test_completion_cli_read_path_never_opens_writer_and_owner_is_required_before_writer(rig,monkeypatch):
    from tools.question_bank import feedback_daily as daily
    import contextlib
    opened=[]
    @contextlib.contextmanager
    def owner(argv,**kwargs):
        opened.append('owner')
        raise ValueError('owner refused')
        yield
    def writer(*a,**k):
        opened.append('writer'); raise AssertionError('must verify owner before writer connection')
    monkeypatch.setattr(api.native,'native_operation',owner)
    monkeypatch.setattr(daily,'_private_db',writer)
    base=['--repo-root',str(rig.root),'--db',str(rig.private/'ledger.sqlite3')]
    with pytest.raises(ValueError,match='owner refused'):
        api.main([*base,'record-completion','--plan',str(rig.path),'--completion',str(rig.output/'completion.json'),'--completion-sha256','a'*64])
    assert opened==['owner']
    opened.clear()
    monkeypatch.setattr(api.native,'native_operation',lambda *a,**k:contextlib.nullcontext())
    def reader(database_uri,**kwargs):
        assert database_uri.endswith('?mode=ro') and kwargs=={'uri':True}
        opened.append('readonly'); raise ValueError('read-only reached')
    monkeypatch.setattr(api.sqlite3,'connect',reader)
    with pytest.raises(ValueError,match='read-only reached'): api.main([*base,'completion','--plan',str(rig.path)])
    assert opened==['readonly']


@pytest.mark.parametrize('command', ['prepare', 'inspect', 'save', 'check', 'evaluate', 'publish', 'reconcile', 'completion', 'record-completion'])
def test_cli_prints_only_safe_summary_not_private_readback_authority_or_freeform(rig, monkeypatch, capsys, command):
    import contextlib
    actor = 'fixture-private-cli-audit-identity'
    body = 'fixture-private-cli-user-body'
    private = {'schemaVersion': body, 'planHash': 'a' * 64, 'unitHash': 'b' * 64,
        'status': 'published_readback_matched', 'FirestoreWritten': True,
        'liveReadback': {'live-a': {'fields': {'updatedById': actor, 'detailComment': body}}},
        'authority': {'kind': 'report', 'taskId': 'task', 'privateMetadata': body},
        'completionContract': {'reason': body}, 'history': {'reason': body},
        'commands': [{'stdout': body, 'stderr': actor}], 'writeVersions': {'live-a': 'exact-version'}}
    monkeypatch.setattr(api.native, 'native_operation', lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(api.normal, 'coordinator_for', lambda *a: rig.coordinator)
    monkeypatch.setattr(api, command.replace('-', '_'), lambda *a, **k: deepcopy(private))
    argv = ['--repo-root', str(rig.root), '--db', str(rig.private / 'ledger.sqlite3'), command]
    if command == 'prepare': argv += ['--request', str(rig.request_path), '--output', str(rig.output)]
    else: argv += ['--plan', str(rig.path)]
    if command in {'save', 'publish'}: argv += ['--approval', str(rig.private / 'approval.json')]
    if command == 'record-completion': argv += ['--completion', str(rig.output / 'completion.json'), '--completion-sha256', 'c' * 64]
    assert api.main(argv) == 0
    output = capsys.readouterr().out
    assert actor not in output and body not in output
    summary = json.loads(output)
    assert summary == api._stdout_summary(command, private)
    assert summary['privateResultHash'] == api.sha256_json(private)
    assert summary['readDocumentCount'] == summary['writeDocumentCount'] == 1
    assert private['liveReadback']['live-a']['fields']['updatedById'] == actor


def test_cli_preserves_exact_native_scope_marker(rig, monkeypatch, capsys):
    import contextlib
    scope = {'schemaVersion': 'fixture-native-scope', 'planHash': 'a' * 64, 'unitHash': 'b' * 64}
    monkeypatch.setattr(api.native, 'native_operation', lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(api.normal, 'coordinator_for', lambda *a: rig.coordinator)
    monkeypatch.setattr(api, 'scope', lambda *a, **k: deepcopy(scope))
    assert api.main(['--repo-root', str(rig.root), '--db', str(rig.private / 'ledger.sqlite3'),
        'scope', '--plan', str(rig.path), '--action', 'save']) == 0
    assert capsys.readouterr().out == api.scope_marker(scope, creation=True) + '\n'


def test_cli_process_failure_does_not_echo_private_exception_text(monkeypatch, capsys):
    text = 'fixture-private-error-actor-and-user-report-body'
    def failed(): raise ValueError(text)
    monkeypatch.setattr(api, 'main', failed)
    assert api._cli_entrypoint() == 1
    captured = capsys.readouterr()
    assert captured.out == '' and text not in captured.err
    assert json.loads(captured.err) == {'schemaVersion': 'source-bound-public-tf-cli-summary/v1',
        'status': 'failed', 'errorType': 'ValueError',
        'privateErrorHash': api.sha256_json({'type': 'ValueError', 'message': text}),
        'privateDiagnosticSaved': False, 'privateDiagnostic': None}


@pytest.mark.parametrize('case', ['invalid_action', 'unknown_argument', 'missing_value', 'invalid_command', 'missing_command', 'newline_value'])
def test_cli_parser_refusals_never_echo_argv_or_dispatch_before_private_context_is_valid(tmp_path, monkeypatch, capsys, case):
    secret = 'fixture-private-parser-actor-and-report-body'
    base = ['--repo-root', str(tmp_path), '--db', str(tmp_path / (secret + '.sqlite3'))]
    valid = ['scope', '--plan', str(tmp_path / api.PRIVATE / 'unit/plan.json'), '--action', 'save']
    if case == 'invalid_action': args = [*base, *valid[:-1], secret]
    elif case == 'unknown_argument': args = [*base, *valid, '--' + secret, secret]
    elif case == 'missing_value': args = [*base, 'scope', '--plan']
    elif case == 'invalid_command': args = [*base, secret]
    elif case == 'missing_command': args = base
    else: args = [*base, *valid[:-1], secret + '\n非公開本文']
    def forbidden(*a, **k): raise AssertionError('parser must reject before native or database dispatch')
    monkeypatch.setattr(api.native, 'native_operation', forbidden)
    monkeypatch.setattr(api.sqlite3, 'connect', forbidden)
    monkeypatch.setattr(api.sys, 'argv', [secret, *args])
    assert api._cli_entrypoint() == 1
    captured = capsys.readouterr()
    assert captured.out == '' and secret not in captured.err and '非公開本文' not in captured.err
    summary = json.loads(captured.err)
    assert summary['status'] == 'failed' and summary['errorType'] == '_CliArgumentError'
    assert summary['privateDiagnosticSaved'] is False and summary['privateDiagnostic'] is None
    assert len(summary['privateErrorHash']) == 64
    assert not (tmp_path / api.PRIVATE / '.source-tf-cli-diagnostics').exists()


@pytest.mark.parametrize('subcommand', [False, True])
def test_cli_help_remains_static_and_successful_without_untrusted_program_name(tmp_path, monkeypatch, capsys, subcommand):
    secret = 'fixture-private-help-program-name'
    args = ['--repo-root', str(tmp_path), 'scope', '--help'] if subcommand else ['--help']
    monkeypatch.setattr(api.sys, 'argv', [secret, *args])
    with pytest.raises(SystemExit) as exited: api._cli_entrypoint()
    assert exited.value.code == 0
    captured = capsys.readouterr()
    assert captured.err == '' and secret not in captured.out and api.MODULE in captured.out


def test_argument_parser_nonzero_exit_cannot_print_private_message(capsys):
    parser = api._PrivateArgumentParser(prog=api.MODULE)
    with pytest.raises(api._CliArgumentError): parser.exit(2, 'fixture-private-direct-exit-message')
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('command', ['prepare', 'scope', 'publish'])
def test_failed_cli_retains_unique_0600_private_diagnostics_and_never_alters_existing_run(rig, monkeypatch, capsys, command):
    import contextlib
    text = 'fixture-private-diagnostic-actor-and-report-body'
    rig.output.mkdir(); original = rig.output / 'existing-failed-output.json'
    original.write_bytes(b'old immutable run'); original_before = original.read_bytes()
    def failed(*a, **k): raise ValueError(text)
    monkeypatch.setattr(api.native, 'native_operation', lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(api.normal, 'coordinator_for', lambda *a: rig.coordinator)
    monkeypatch.setattr(api, command, failed)
    argv = ['--repo-root', str(rig.root), '--db', str(rig.private / 'ledger.sqlite3'), command]
    if command == 'prepare': argv += ['--request', str(rig.request_path), '--output', str(rig.output)]
    else: argv += ['--plan', str(rig.path)]
    if command == 'scope': argv += ['--action', 'save']
    if command == 'publish': argv += ['--approval', str(rig.private / 'approval.json')]
    monkeypatch.setattr(api.sys, 'argv', ['source-tf', *argv])
    refs = []
    for _ in range(2):
        assert api._cli_entrypoint() == 1
        captured = capsys.readouterr(); assert captured.out == '' and text not in captured.err
        summary = json.loads(captured.err); assert summary['privateDiagnosticSaved']
        reference = summary['privateDiagnostic']; path = Path(reference['ref']); refs.append(path)
        assert path.is_relative_to(rig.root / api.PRIVATE / '.source-tf-cli-diagnostics')
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
        assert api._hash(path.read_bytes()) == reference['sha256']
        private = json.loads(path.read_bytes())
        assert private['argv'] == argv and private['exceptionText'] == text and text in private['traceback']
        assert private['command'] == command
    assert refs[0] != refs[1] and all(path.exists() for path in refs)
    assert original.read_bytes() == original_before and not rig.path.exists()
    assert sorted(path.name for path in rig.output.iterdir()) == [original.name]


@pytest.mark.parametrize('unsafe', ['outside', 'symlink', 'permissions'])
def test_cli_diagnostic_refuses_unsafe_context_or_storage_without_disclosure(rig, monkeypatch, capsys, unsafe):
    import contextlib
    text = 'fixture-private-unsafe-diagnostic-body'
    def failed(*a, **k): raise ValueError(text)
    path = rig.path
    if unsafe == 'outside': path = rig.root / 'outside-private-plan.json'
    elif unsafe == 'symlink':
        target = rig.private / 'real-private-plan.json'; target.write_bytes(b'unchanged')
        path = rig.private / 'symlink-plan.json'; path.symlink_to(target)
    else:
        directory = rig.root / api.PRIVATE / '.source-tf-cli-diagnostics'
        directory.mkdir(mode=0o755); directory.chmod(0o755)
    monkeypatch.setattr(api.native, 'native_operation', lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(api.normal, 'coordinator_for', lambda *a: rig.coordinator)
    monkeypatch.setattr(api, 'scope', failed)
    monkeypatch.setattr(api.sys, 'argv', ['source-tf', '--repo-root', str(rig.root),
        '--db', str(rig.private / 'ledger.sqlite3'), 'scope', '--plan', str(path), '--action', 'save'])
    assert api._cli_entrypoint() == 1
    captured = capsys.readouterr(); assert captured.out == '' and text not in captured.err
    summary = json.loads(captured.err)
    assert summary['privateDiagnosticSaved'] is False and summary['privateDiagnostic'] is None
    directory = rig.root / api.PRIVATE / '.source-tf-cli-diagnostics'
    assert not directory.exists() or not list(directory.iterdir())
    if unsafe == 'symlink': assert target.read_bytes() == b'unchanged'


def test_report_cannot_reconstruct_commit_from_history_without_bound_parent_after_state(rig,monkeypatch):
    from tools.question_bank import feedback_daily as daily
    from tools.question_bank import feedback_source_tf_completion as complete
    report_rig(rig,monkeypatch)
    plan,artifact,path,digest,_=published_completion(rig,monkeypatch)
    contract=artifact['completionContract']; stamp='pretend-recorded'
    # A real publication artifact plus a manually inserted history row cannot
    # stand in for the parent CAS having committed. This is a temp fixture only.
    rig.connection.execute('''INSERT INTO source_tf_report_publication_history
        (task_id,case_id,reason,artifact_ref,artifact_sha256,plan_hash,completion_contract_hash,
         requirement_hash,read_document_ids_json,write_document_ids_json,recorded_at,parent_published_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
        ('report-task','case-a',contract['reason'],str(path),digest,plan['planHash'],api.sha256_json(contract),
         plan['source']['taskRequirement']['requirementHash'],json.dumps(artifact['readDocumentIds']),json.dumps(artifact['writeDocumentIds']),stamp,stamp))
    rig.connection.commit()
    with pytest.raises(ValueError,match='exact parent publication after-state'):
        daily.record_source_tf_report_publication(rig.connection,task_id='report-task',case_id='case-a',
            artifact_ref=path,artifact_sha256=digest,repo_root=rig.root,coordinator=rig.coordinator)
    assert rig.connection.execute('SELECT published_at FROM report_tasks').fetchone()[0] is None



def test_legacy_report_request_never_receives_completion_contract(rig,monkeypatch):
    from tools.question_bank import feedback_source_tf_completion as complete
    report_rig(rig,monkeypatch)
    rig.request['schemaVersion']=api.REQUEST
    rig.request.pop('reportReview')
    write(rig.request_path,rig.request)
    plan=prepared(rig)
    assert plan['source']['completionContract'] is None
    assert 'genuine_report_blind_a_b_challenge_reader_not_connected' in plan['source']['requiredReviewGates']
    # No fabricated result reference grants this old report plan completion.
    artifact={'schemaVersion':complete.SCHEMA,'plan':{'ref':str(rig.path),'sha256':api._hash(rig.path.read_bytes())},
        'authority':rig.request['authority'],'completionContract':None,'publicationResult':{},
        'readDocumentIds':[],'writeDocumentIds':[]}
    with pytest.raises(ValueError,match='authority/result/read-write/contract differs'):
        complete.verify_completion(rig.root,artifact,connection=rig.connection,
            artifact_ref=rig.output/'completion.json',coordinator=rig.coordinator)
