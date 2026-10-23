"""Private report reviews under isolated SDK boundaries; no production writes."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_feedback_source_tf_delivery import rig, write, no_live_sdk
from tests.test_native_independent_approval import native_audit
from tools.question_bank import feedback_source_tf_delivery as delivery
from tools.question_bank import feedback_source_tf_report_review as api
from tools.question_bank import question_issue_reports as protocol
from tools.question_review_console.official_source_correction import OfficialSourceCorrectionService

_real_read_claims = api.read_claims


@pytest.fixture
def report(rig, monkeypatch):
    r = rig
    r.connection.executescript("CREATE TABLE report_tasks(task_id,report_id,question_id,categories_json,case_ids_json,case_statuses_json,intake_status,decision,decision_reason,evidence_ref,proposal_hash);"
        "CREATE TABLE report_case_decisions(task_id,case_id,decision,reason,evidence_ref,decided_at);"
        "INSERT INTO report_tasks VALUES('report-task','report','live-a','[\"correct_answer\"]','[\"case\"]','{}','complete','fix_required','reason','evidence','proposal');"
        "INSERT INTO report_case_decisions VALUES('report-task','case','fix_required','reason','evidence','time');")
    r.connection.execute('ALTER TABLE report_tasks ADD COLUMN source_path')
    source_path = 'users/private-fixture-user/questionIssueReportSubmissions/report'
    r.connection.execute('UPDATE report_tasks SET source_path=?', (source_path,))
    case_id = api._intake_hash({'questionId': 'live-a', 'questionContentHash': 'a' * 64, 'category': 'correct_answer'})
    r.connection.execute('UPDATE report_tasks SET case_ids_json=?', (json.dumps([case_id]),))
    r.connection.execute('UPDATE report_case_decisions SET case_id=?', (case_id,))
    r.connection.commit()
    r.request['authority'] = {'kind': 'report', 'taskId': 'report-task', 'caseId': case_id}
    write(r.request_path, r.request)
    snapshot = {'questionId': 'live-a', 'originalQuestionId': 'q1', 'qualificationId': 'sample', 'listGroupId': '2026', 'questionBodyText': '受付時の古い問題本文。'}
    state = {'case': {'id': case_id, 'schemaVersion': 1, 'workflowStatus': 'unreviewed',
        'questionId': 'live-a', 'originalQuestionId': 'q1', 'qualificationId': 'sample', 'listGroupId': '2026',
        'category': 'correct_answer', 'categories': ['correct_answer'], 'reportedContentHash': 'a' * 64, 'currentContentHash': api._intake_hash(snapshot),
        'canonicalSnapshot': snapshot}, 'updateTimeExact': '2026-10-23T00:00:00.123456789Z'}
    claims = [{'reportId': 'report', 'categories': ['correct_answer'], 'detailComment': '利用者の指摘本文',
        'questionContentHash': 'a' * 64, 'imageLoadEvents': [], 'intakeBinding': {'caseId': case_id,
        'questionId': 'live-a', 'category': 'correct_answer', 'sourcePathSha256': api._hash(source_path.encode()),
        'linkSha256': 'b' * 64, 'submissionSha256': 'c' * 64, 'receiptSha256': 'd' * 64,
        'linkUpdateTimeExact': 'version-link', 'submissionUpdateTimeExact': 'version-submission', 'receiptUpdateTimeExact': 'version-receipt'}}]
    monkeypatch.setattr(api, 'read_case', lambda _: deepcopy(state))
    monkeypatch.setattr(api, 'read_claims', lambda _: deepcopy(claims))
    monkeypatch.setattr(protocol, 'REPO_ROOT', r.root)
    monkeypatch.setattr(protocol, 'PROMPT_ROOT', r.root / 'prompt/question_issue_reports')
    monkeypatch.setattr(OfficialSourceCorrectionService, '_render_pdf_page',
        staticmethod(lambda source, page, target: target.write_bytes(b'PNG ' + source.name.encode() + str(page).encode())))
    turns = []
    class Runtime:
        def run_turn(self, prompt, **options):
            phase = options['monitor_context']['phase']; turns.append((phase, prompt, options))
            data = json.loads(prompt.split('<INPUT_JSON>\n')[-1].split('\n</INPUT_JSON>')[0])
            payload = {'schemaVersion': api.adoption.CHALLENGE_OUTPUT if phase == 'challenge' else api.adoption.BLIND_OUTPUT,
                'phase': 'challenge' if phase == 'challenge' else 'blind', 'inputHash': data['inputHash'], 'evidence': [],
                'reviewPurpose': api.adoption.PURPOSE, 'candidateHash': data['input']['candidateHash'],
                'observedBeforeHash': data['input']['observedBeforeHash'], 'modelSubjectHash': data['input']['modelSubjectHash'], 'decision': 'approve', 'findings': [],
                'rationale': '旧liveの不備を候補が解消し、公開表示の意味も一次資料と整合する。'}
            if phase == 'challenge':
                payload.update(blindReviewHashes=data['input']['blindReviewHashes'], caseScope=data['input']['caseScope'],
                    claimDecisions=[{'claimKey': c['intakeBinding']['sourcePathSha256'], 'reportId': c['reportId'],
                        'decision': 'resolved_by_candidate', 'rationale': '今回カテゴリの指摘は予定候補で解消する。'}
                        for c in data['input']['untrustedReportData']])
            else:
                payload.update(reviewerSlot=phase[-1].upper(), documents={identity: {'decision': 'approve', 'findings': [],
                    'rationale': '一次根拠に照らし、このdocの予定表示と正誤は正しい。',
                    'criteria': {key: {'satisfied': True, 'reason': 'このdocの原文、予定値、変更対象を照合した。'}
                        for key in api.adoption.CRITERIA}} for identity in data['input']['observedLiveBefore']})
            malformed_display = any('不正な表示' in d['questionBodyText'] for d in data['input']['candidate']['plannedPublic']['questions'])
            if malformed_display:
                payload['decision'] = 'reject'; payload['findings'] = ['公開表示が原文の意味を変えている。']
                if phase == 'challenge':
                    for item in payload['claimDecisions']: item['decision'] = 'unresolved'
                else:
                    for item in payload['documents'].values():
                        item['decision'] = 'reject'; item['findings'] = ['表示の意味が不正。']
                        item['criteria']['noNewDefects'] = {'satisfied': False, 'reason': '予定表示が命題の意味を変える。'}
            images = tuple({'sourcePath': str(p.relative_to(r.root)), 'attachmentIndex': n,
                'sha256': api._hash(p.read_bytes()), 'byteCount': len(p.read_bytes())}
                for n, p in enumerate(options['image_paths']))
            return SimpleNamespace(final_message=json.dumps(payload, ensure_ascii=False), changed_files=(),
                session_id='session-' + phase, thread_id='thread-' + phase, turn_id='turn-' + phase, image_inputs=images)
    r.coordinator.app_server = Runtime()
    r.case_state = state; r.claims = claims; r.turns = turns
    r.review_dir = r.private / 'fresh-review'; r.review_path = r.review_dir / 'review-plan.json'
    r.argv = ['--repo-root', str(r.root), '--db', str(r.private / 'ledger.sqlite3'), 'execute',
        '--request', str(r.request_path), '--output', str(r.review_dir)]
    return r


def executed(r):
    scope = api.execute(r.root, r.request_path, r.review_dir, coordinator=r.coordinator,
        connection=r.connection, execution_argv=r.argv)
    audit_dir = r.private / 'report-adoption'; audit_dir.mkdir()
    approval = write(audit_dir / 'approval.json', native_audit(audit_dir, scope))
    proof = {'plan': {'ref': str(r.review_path), 'sha256': api._hash(r.review_path.read_bytes())}, 'approval': approval}
    return scope, proof


def source_public(r):
    _, _, _, source, public = delivery._derive(r.root, r.request_path, r.coordinator, r.connection, {})
    return source, public


def test_fresh_review_keeps_claims_out_of_blind_and_records_two_images(report):
    r = report; scope, proof = executed(r)
    assert len(r.turns) == 3
    for phase, prompt, options in r.turns:
        assert len(options['image_paths']) == 2
        assert options['sandbox'] == 'read-only'
        assert ('利用者の指摘本文' in prompt) == (phase == 'challenge')
        assert ('"caseScope"' in prompt) == (phase == 'challenge')
        if phase != 'challenge': assert r.case_state['case']['id'] not in prompt
    source, public = source_public(r)
    result = api.verify(r.root, proof, r.request, source, public, r.connection, {})
    assert result['candidateAdopted'] is True
    assert result['caseCompletionPerformed'] is False
    assert scope['nativeCreationCall'] == api.execution_call(r.argv)
    assert not (r.review_dir / 'patch.json').exists()
    assert r.case_state['case']['workflowStatus'] == 'unreviewed'
    sent = json.loads((r.review_dir / 'blind_input.json').read_bytes())
    assert sent['observedLiveBefore']['live-a']['fields']['correctChoiceText'] == '正しい'
    assert sent['candidate']['normalLocal']['correctChoiceText'][0] == '間違い'
    after = next(d for d in sent['candidate']['plannedPublic']['questions'] if d['questionId'] == 'live-a')
    assert after['correctChoiceText'] == '間違い'
    assert sent['fieldDifferencesByDocument']['live-a']['correctChoiceText']['after'] == '間違い'
    assert 'currentFirestoreSnapshots' not in sent


@pytest.mark.parametrize('mutation', ['case_time', 'case_content', 'case_category', 'case_id', 'claims', 'decision', 'mapping', 'public', 'source', 'policy', 'evidence'])
def test_actual_binding_or_intake_change_invalidates_adoption(report, mutation):
    r = report; _, proof = executed(r); source, public = source_public(r)
    if mutation == 'case_time': r.case_state['updateTimeExact'] += 'changed'
    elif mutation == 'case_content': r.case_state['case']['currentContentHash'] = 'b' * 64
    elif mutation == 'case_category': r.case_state['case']['category'] = 'explanation'; r.case_state['case']['categories'] = ['explanation']
    elif mutation == 'case_id': r.case_state['case']['id'] = 'other-case'
    elif mutation == 'claims': r.claims[0]['detailComment'] = '別の指摘'
    elif mutation == 'decision':
        r.connection.execute("UPDATE report_case_decisions SET decision='no_fix'"); r.connection.commit()
    elif mutation == 'mapping': r.request['mapping'][0]['questionId'] = 'other'
    elif mutation == 'public': public['writeFieldsByDocument']['live-a'].append('questionSetId')
    elif mutation == 'source': source['normalProjection']['correctChoiceText'][0] = '正しい'
    elif mutation == 'evidence':
        path = Path(r.request['primaryEvidence'][0]['ref']); path.write_bytes(path.read_bytes() + b'changed')
    elif mutation == 'policy':
        path = r.root / 'prompt/02a_prompt_review_correctChoiceText.md'; path.write_text(path.read_text() + '\nchanged\n')
    with pytest.raises(ValueError): api.verify(r.root, proof, r.request, source, public, r.connection, {})


@pytest.mark.parametrize('mutation', ['missing_attempt', 'failed', 'normalized', 'same_thread', 'missing_image', 'wrong_image', 'review_hash', 'receipt', 'partial', 'approval'])
def test_attempt_chain_third_values_and_native_origin_are_rejected(report, mutation):
    r = report; _, proof = executed(r); source, public = source_public(r)
    directory = r.review_dir
    received_path = next((directory / 'attempts').glob('blind_b_received_*.json'))
    value = json.loads(received_path.read_bytes())
    if mutation == 'missing_attempt': received_path.unlink()
    elif mutation == 'failed':
        p = next((directory / 'attempts').glob('blind_b_validation_*.json')); v = json.loads(p.read_bytes()); v['validation'] = 'failed'; write(p, v)
    elif mutation in {'normalized', 'same_thread', 'missing_image', 'wrong_image'}:
        if mutation == 'normalized': value['normalized']['inputHash'] = '0' * 64
        if mutation == 'same_thread': value['thread_id'] = 'thread-blind_a'
        if mutation == 'missing_image': value['imageInputs'].pop()
        if mutation == 'wrong_image': value['imageInputs'][1]['sha256'] = '0' * 64
        value['receiptHash'] = api.sha256_json({k: v for k, v in value.items() if k != 'receiptHash'})
        write(received_path, value)
        p = next((directory / 'attempts').glob('blind_b_validation_*.json')); v = json.loads(p.read_bytes()); v['receivedReceiptHash'] = value['receiptHash']; write(p, v)
    elif mutation == 'review_hash':
        p = directory / 'blind_a.json'; v = json.loads(p.read_bytes()); v['inputHash'] = '0' * 64; write(p, v)
    elif mutation == 'receipt':
        p = directory / 'receipt.json'; v = json.loads(p.read_bytes()); v['candidateAdopted'] = False; write(p, v)
    elif mutation == 'partial': (directory / 'receipt.json').unlink()
    elif mutation == 'approval':
        v = json.loads(Path(proof['approval']['ref']).read_bytes()); v['schemaVersion'] = 'self-asserted-passed/v1'; proof['approval'] = write(Path(proof['approval']['ref']), v)
    with pytest.raises((ValueError, FileNotFoundError)):
        api.verify(r.root, proof, r.request, source, public, r.connection, {})


def test_interrupted_directory_cannot_be_retried_or_adopted(report):
    r = report
    r.coordinator.app_server.run_turn = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('interrupted'))
    with pytest.raises(RuntimeError): api.execute(r.root, r.request_path, r.review_dir, coordinator=r.coordinator, connection=r.connection, execution_argv=r.argv)
    assert not (r.review_dir / 'receipt.json').exists()
    with pytest.raises(ValueError, match='exclusive'):
        api.execute(r.root, r.request_path, r.review_dir, coordinator=r.coordinator, connection=r.connection, execution_argv=r.argv)


def test_versioned_report_reader_only_unblocks_proven_report_request(report):
    r = report; _, proof = executed(r)
    old = delivery._request(r.root, r.request_path, {})
    assert delivery._task_lifecycle(r.connection, old)['publicationGate']
    r.request.update(schemaVersion=api.REPORT_REQUEST, reportReview=proof)
    path = r.private / 'versioned-report-request.json'; write(path, r.request)
    result = delivery._derive(r.root, path, r.coordinator, r.connection, {})
    assert result[3]['requiredReviewGates'] == []
    assert result[3]['reportReview']['candidateAdopted'] is True
    r.request['schemaVersion'] = delivery.REQUEST; write(path, r.request)
    with pytest.raises(ValueError, match='legacy v1'): delivery._request(r.root, path, {})
    with pytest.raises(delivery.GateRequired): delivery.complete()


def test_final_transport_boundary_rechecks_case(report):
    r = report; _, proof = executed(r)
    source, public = source_public(r)
    r.request.update(schemaVersion=api.REPORT_REQUEST, reportReview=proof)
    source['reportReview'] = api.verify(r.root, proof, r.request, source, public, r.connection, {})
    plan = {'request': r.request, 'source': source, 'public': public}
    delivery._verify_report_boundary(r.root, plan, r.connection)
    r.case_state['case']['workflowStatus'] = 'reviewed_no_change'
    with pytest.raises(ValueError): delivery._verify_report_boundary(r.root, plan, r.connection)


def test_claim_limit_is_explicit_hold_and_never_silently_truncated(report, monkeypatch):
    class Reports:
        def limit(self, count):
            assert count == 101
            return self
        def stream(self): return iter(range(101))
    class DB:
        def collection(self, name): return self
        def document(self, name): return self
        def limit(self, count): return Reports().limit(count)
        def stream(self): return Reports().stream()
    monkeypatch.setattr(api.FirestoreReadback, '_database', lambda _: DB())
    with pytest.raises(ValueError, match='claim bound exceeded'):
        # Bypass the fixture's sanitized claim adapter to test the production reader.
        _real_read_claims(report.case_state['case']['id'])


def test_production_entrypoint_rejects_non_appserver_backend_before_execution(report, monkeypatch):
    r = report
    r.coordinator.app_server.backend_for = lambda *a: object()
    monkeypatch.setattr(api.normal, 'coordinator_for', lambda root: r.coordinator)
    with pytest.raises(ValueError, match='real configured Codex App Server'):
        api.main(r.argv)
    assert not r.review_dir.exists()


def test_native_approval_revocation_invalidates_same_proof(report):
    r = report; _, proof = executed(r); source, public = source_public(r)
    api.verify(r.root, proof, r.request, source, public, r.connection, {})
    record = json.loads(Path(proof['approval']['ref']).read_bytes())
    journal = Path(record['reviewerFinal']['sessionPath'])
    with journal.open('a') as stream:
        stream.write(json.dumps({'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
            'phase': 'final_answer', 'content': [{'type': 'output_text', 'text': '撤回します。'}]}}) + '\n')
    with pytest.raises(ValueError): api.verify(r.root, proof, r.request, source, public, r.connection, {})


def test_pending_publish_case_stays_pending_after_current_local_no_change(report):
    r = report; r.case_state['case']['workflowStatus'] = protocol.PUBLISH_PENDING_STATUS
    _, proof = executed(r); source, public = source_public(r)
    assert api.verify(r.root, proof, r.request, source, public, r.connection, {})['candidateAdopted']
    assert r.case_state['case']['workflowStatus'] == protocol.PUBLISH_PENDING_STATUS


@pytest.mark.parametrize('mutation', ['no_claims', 'foreign_report', 'foreign_snapshot', 'snapshot_body'])
@pytest.mark.parametrize('when', ['execute', 'verify'])
def test_audited_intake_and_case_inner_mutations_fail_closed(report, mutation, when):
    r = report
    if when == 'verify':
        _, proof = executed(r); source, public = source_public(r)
    if mutation == 'no_claims': r.claims.clear()
    elif mutation == 'foreign_report': r.claims[0]['reportId'] = 'foreign-report'
    elif mutation == 'foreign_snapshot':
        r.case_state['case']['canonicalSnapshot'] = {'questionId': 'foreign-q', 'originalQuestionId': 'foreign-original',
            'qualificationId': 'foreign-qualification', 'listGroupId': 'foreign-group', 'questionBodyText': '他問題'}
    elif mutation == 'snapshot_body': r.case_state['case']['canonicalSnapshot']['questionBodyText'] = '改変された受付原本'
    with pytest.raises(ValueError):
        if when == 'execute': api.execute(r.root, r.request_path, r.review_dir, coordinator=r.coordinator, connection=r.connection, execution_argv=r.argv)
        else: api.verify(r.root, proof, r.request, source, public, r.connection, {})
    if when == 'execute':
        assert not (r.review_dir / 'receipt.json').exists()
        assert not r.turns


def test_intake_hash_matches_handler_stable_stringify_golden_vectors():
    # Values computed by the read-only Repaso handler __test__.stableStringify /
    # sha256Hex on 2026-10-23, including UTF-16 key ordering rather than Python's.
    assert api._intake_hash({'questionId': 'live-a', 'originalQuestionId': 'q1', 'qualificationId': 'sample',
        'listGroupId': '2026', 'questionBodyText': '受付時の古い問題本文。'}) == 'ba9c9a95606ef328254e36d37fbaaee6a5fa3dae200b3ffcc518ad3cf4e94242'
    assert api._intake_hash({'😀': '絵', 'a': [None, True, 2026, '行\n末'], '\ue000': 'x'}) == 'a47501111ef6e4a7084883e1ba931c2ea18d6bb526b80e84cb2739e9a4437492'


def intake_database(r, mutation):
    path = 'users/private-fixture-user/questionIssueReportSubmissions/report'; digest = api._hash(path.encode())
    fingerprint = api._hash('question-issue-reporter:v1:private-fixture-user'.encode())
    link = {'schemaVersion': 1, 'reportId': 'report', 'category': 'correct_answer', 'sourceSubmissionPath': path,
        'reportedContentHash': 'a' * 64, 'reporterFingerprint': fingerprint}
    submission = {'schemaVersion': 1, 'reportId': 'report', 'questionId': 'live-a', 'clientReportedOfficial': True,
        'categories': ['correct_answer'], 'questionContentHash': 'a' * 64, 'detailComment': '利用者の指摘本文', 'imageLoadEvents': []}
    receipt = {'schemaVersion': 1, 'reportId': 'report', 'status': 'processed', 'sourceSubmissionPath': path,
        'caseIds': [r.case_state['case']['id']], 'reporterFingerprint': fingerprint}
    exists = {'link': True, 'submission': True, 'receipt': True}
    if mutation == 'bad_path': link['sourceSubmissionPath'] = 'invalid/private-path'
    elif mutation == 'missing_submission': exists['submission'] = False
    elif mutation == 'missing_receipt': exists['receipt'] = False
    elif mutation == 'wrong_question': submission['questionId'] = 'foreign-q'
    elif mutation == 'wrong_hash': submission['questionContentHash'] = 'b' * 64
    elif mutation == 'wrong_category': link['category'] = 'explanation'
    elif mutation == 'wrong_receipt': receipt['caseIds'] = ['foreign-case']
    elif mutation == 'rejected_receipt': receipt['status'] = 'rejected'
    def snap(kind, identity, data):
        return SimpleNamespace(id=identity, exists=exists[kind], to_dict=lambda: deepcopy(data),
            update_time=SimpleNamespace(rfc3339=lambda: '2026-10-23T00:00:00.123456789Z'))
    class Ref:
        def __init__(self, location=''): self.location = location
        def collection(self, name): return Ref(self.location + '/' + name)
        def document(self, name): return Ref(self.location + '/' + name)
        def limit(self, count):
            assert count == 101
            return self
        def stream(self):
            queries.append(self.location)
            return iter([snap('link', digest, link)])
        def get(self):
            if self.location == path: return snap('submission', 'report', submission)
            if self.location == '/questionIssueReportReceipts/' + digest: return snap('receipt', digest, receipt)
            raise AssertionError('unexpected intake read')
    queries = []
    db = Ref()
    db.document = lambda value: Ref(value)
    return db, queries


@pytest.mark.parametrize('mutation', ['none', 'bad_path', 'missing_submission', 'missing_receipt',
    'wrong_question', 'wrong_hash', 'wrong_category', 'wrong_receipt', 'rejected_receipt'])
def test_production_claim_reader_uses_one_complete_link_observation(report, monkeypatch, mutation):
    db, queries = intake_database(report, mutation)
    monkeypatch.setattr(api.FirestoreReadback, '_database', lambda _: db)
    if mutation == 'none':
        claims = _real_read_claims(report.case_state['case']['id'])
        assert len(claims) == 1 and claims[0]['reportId'] == 'report'
        assert 'private-fixture-user' not in json.dumps(claims, ensure_ascii=False)
        assert 'sourceSubmissionPath' not in json.dumps(claims, ensure_ascii=False)
        assert claims[0]['intakeBinding']['sourcePathSha256']
    else:
        with pytest.raises(ValueError): _real_read_claims(report.case_state['case']['id'])
    assert len(queries) == 1


def test_candidate_protocol_rejects_public_only_semantic_defect(report):
    r = report
    for row in r.request['public']:
        row['questionBodyText'] = '不正な表示として原命題を反転する。'
        text = next(m['sourceChoiceText'] for m in r.request['mapping'] if m['questionId'] == row['questionId'])
        row['questionText'] = row['questionBodyText'] + '[quote]' + text + '[/quote]'
    write(r.request_path, r.request)
    with pytest.raises(ValueError, match='candidate adoption rejected'):
        api.execute(r.root, r.request_path, r.review_dir, coordinator=r.coordinator, connection=r.connection, execution_argv=r.argv)
    assert not (r.review_dir / 'receipt.json').exists()
    blind = json.loads((r.review_dir / 'blind_input.json').read_bytes())
    assert blind['candidate']['normalLocal'] == r.current
    assert all('不正な表示' in d['questionBodyText'] for d in blind['candidate']['plannedPublic']['questions'])


def test_actual_sent_prompt_cannot_omit_planned_public(report, monkeypatch):
    r = report; original = api.adoption.prompt
    def omit(root, phase, value, contract_text):
        incomplete = deepcopy(value); incomplete['candidate'].pop('plannedPublic')
        return original(root, phase, incomplete, contract_text)
    monkeypatch.setattr(api.adoption, 'prompt', omit)
    with pytest.raises(ValueError, match='actual candidate adoption prompt omitted'):
        api.execute(r.root, r.request_path, r.review_dir, coordinator=r.coordinator, connection=r.connection, execution_argv=r.argv)
    assert not r.turns
    assert not (r.review_dir / 'receipt.json').exists()


def test_candidate_and_observed_before_have_distinct_hashes(report):
    r = report; source, public = source_public(r)
    context = api.context(r.request, source, public)
    before = api.adoption.subject(context)
    context['public']['baseline']['live-a']['updateTimeExact'] = '2026-10-24T00:00:00.123456789Z'
    after = api.adoption.subject(context)
    assert before['candidateHash'] == after['candidateHash']
    assert before['observedBeforeHash'] != after['observedBeforeHash']


def test_candidate_decisions_require_every_doc_and_scoped_claim(report):
    r = report; executed(r)
    for mutation in ('missing_doc', 'false_criterion', 'doc_finding', 'unknown_candidate', 'wrong_before', 'missing_claim', 'foreign_case', 'foreign_category'):
        name = 'challenge' if mutation in {'missing_claim', 'foreign_case', 'foreign_category'} else 'blind_a'
        value = json.loads((r.review_dir / ('challenge_input.json' if name == 'challenge' else 'blind_input.json')).read_bytes())
        review = json.loads((r.review_dir / (name + '.json')).read_bytes())
        if mutation == 'missing_doc': review['documents'].pop('live-b')
        elif mutation == 'false_criterion': review['documents']['live-a']['criteria']['primaryEvidenceConsistent']['satisfied'] = False
        elif mutation == 'doc_finding': review['documents']['live-a']['findings'] = ['未解決。']
        elif mutation == 'unknown_candidate': review['candidateHash'] = '0' * 64
        elif mutation == 'wrong_before': review['observedBeforeHash'] = '0' * 64
        elif mutation == 'missing_claim': review['claimDecisions'] = []
        elif mutation == 'foreign_case': review['caseScope']['caseId'] = 'other-case'
        elif mutation == 'foreign_category': review['caseScope']['category'] = 'explanation'
        with pytest.raises(ValueError): api.adoption.validate(review, name, value)


def test_old_reader_plan_schema_is_explicitly_refused(report):
    r = report; executed(r)
    value = json.loads(r.review_path.read_bytes()); value['schemaVersion'] = 'source-bound-public-tf-report-review/v1'
    write(r.review_path, value)
    with pytest.raises(ValueError, match='legacy_report_review_plan_requires_fresh_candidate_adoption'):
        api._load(r.root, r.review_path, {})


def test_model_subject_masks_identity_metadata_but_keeps_business_content(report):
    r = report; source, public = source_public(r)
    for identity, snapshot in public['baseline'].items():
        snapshot['fields']['createdById'] = 'sensitive-person-id'
        next(row for row in public['questions'] if row['questionId'] == identity)['createdById'] = 'sensitive-person-id'
    context = api.context(r.request, source, public)
    value = api.adoption.blind_input(context, [])
    actual_prompt = api.adoption.prompt(r.root, 'blind_a', value, '')
    assert 'sensitive-person-id' not in actual_prompt
    assert value['candidate']['plannedPublic']['questions'][0]['questionBodyText'] == public['questions'][0]['questionBodyText']
    assert value['maskedFieldContract']['protectedIdentityMetadataUnchanged'] is True
    assert value['modelSubjectHash']
    assert value['candidate']['plannedPublic']['questions'][0]['createdById']['identityValueMasked'] is True
    assert context['public']['questions'][0]['createdById'] == 'sensitive-person-id'
