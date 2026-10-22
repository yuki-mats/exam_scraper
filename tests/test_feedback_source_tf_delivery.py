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
    db = private / 'ledger.sqlite3'; connection = sqlite3.connect(db); connection.row_factory = sqlite3.Row
    connection.executescript("CREATE TABLE improvement_tasks(task_id,title,need,target,status,updated_at);CREATE TABLE improvement_result_history(id,task_id,status,reason,artifact_ref,artifact_sha256,recorded_at);"
        "CREATE TABLE ai_question_candidates(candidate_id,question_id,text_hash,review_status,improvement_task_id);"
        "INSERT INTO improvement_tasks VALUES('task','TF表示','命題と表示の整合','live-a','in_progress','fixture-time');"
        "INSERT INTO ai_question_candidates VALUES('candidate','live-a','hash','promoted','task');")
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
        coordinator=coordinator, connection=connection, options=options)
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
        selected = {'question_type', 'question_intent', 'correct_choice'}
        monkeypatch.setattr(f.coordinator.workflow, 'versioned_policies',
            lambda *a: {k: v for k, v in policies(*a).items() if k in selected})
        proofs = []
        for stage_id in ('question_type', 'question_intent', 'correct_choice'):
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
        def verify(): return api.verify_normal_stage_proofs(f.root, request, f.coordinator, f.connection, {})
        _, projected, receipts, status = verify()
        assert projected['correctChoiceText'] == ['間違い', '正しい', '正しい']
        assert {r['stageId'] for r in receipts} == selected and status['allCurrent']
        final = api._json(Path(proofs[-1]['plan']['ref']).read_bytes())
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
