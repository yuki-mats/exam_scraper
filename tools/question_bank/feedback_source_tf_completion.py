"""Source-TF-only completion proofs and immutable ledger lifecycle reconstruction.

No publication writes. A JSON artifact is only a reference: every use replays the
real source/native/publication/readback graph before returning a process proof.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path
import json
import sqlite3

from tools.question_bank.feedback_legacy_review_context import _bytes, _hash, _inside, _physical, _read, _ref
from tools.question_review_console.projection import sha256_json

SCHEMA = 'source-bound-public-tf-completion/v1'
CONTRACT = 'source-bound-public-tf-completion-contract/v1'
REASON = '通常sourceTFの独立監査・正式保存・限定公開・全read setの反映一致を確認。'
HISTORY = 'source_tf_report_publication_history'
_REPLAY = ContextVar('source_tf_matching_completion_replay', default=None)


def _rows(connection, sql, args=()):
    cursor = connection.cursor(); cursor.row_factory = sqlite3.Row
    try: return [dict(row) for row in cursor.execute(sql, args)]
    finally: cursor.close()


def _table(connection, name):
    return bool(connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def report_publications(connection, task_id):
    return _rows(connection, 'SELECT * FROM ' + HISTORY + ' WHERE task_id=? ORDER BY id', (task_id,)) if _table(connection, HISTORY) else []


def _actual_lifecycle(connection, request):
    authority = request['authority']; task_id = authority['taskId']
    if authority['kind'] == 'improvement':
        rows = _rows(connection, 'SELECT status,updated_at FROM improvement_tasks WHERE task_id=?', (task_id,))
        if len(rows) != 1: raise ValueError('completion improvement task missing')
        latest = _rows(connection, 'SELECT * FROM improvement_result_history WHERE task_id=? ORDER BY id DESC LIMIT 1', (task_id,))
        return {'kind': 'improvement', 'expectedStatus': rows[0]['status'], 'updatedAt': rows[0]['updated_at'],
            'latestResult': latest[0] if latest else None}
    if authority['kind'] != 'report' or not authority.get('caseId'):
        raise ValueError('source TF completion requires one improvement or report/case')
    rows = _rows(connection, 'SELECT published_at,case_ids_json,intake_status FROM report_tasks WHERE task_id=?', (task_id,))
    if len(rows) != 1 or rows[0]['intake_status'] != 'complete': raise ValueError('complete report intake required')
    case_ids = json.loads(rows[0]['case_ids_json'])
    if not isinstance(case_ids, list) or len(set(case_ids)) != len(case_ids) or authority['caseId'] not in case_ids:
        raise ValueError('completion report case membership differs')
    membership = _rows(connection, 'SELECT case_id FROM report_task_cases WHERE task_id=? ORDER BY case_id', (task_id,))
    if [row['case_id'] for row in membership] != sorted(case_ids):
        raise ValueError('completion report case membership table differs')
    decisions = _rows(connection, 'SELECT task_id,case_id,decision,reason,evidence_ref,decided_at FROM report_case_decisions WHERE task_id=? ORDER BY case_id', (task_id,))
    if any(row['case_id'] not in case_ids for row in decisions): raise ValueError('foreign report decision row')
    return {'kind': 'report', 'publishedAt': rows[0]['published_at'], 'caseIds': sorted(case_ids),
        'caseDecisions': decisions, 'publications': report_publications(connection, task_id)}


def task_lifecycle(connection, request):
    actual = _actual_lifecycle(connection, request)
    replay = _REPLAY.get()
    if replay is not None:
        return replay.lifecycle(connection, request, actual)
    if actual['kind'] == 'improvement' and actual['expectedStatus'] not in {'open', 'in_progress', 'review_pending'}:
        raise ValueError('unfinished_unblocked_task_lifecycle_required')
    if actual['kind'] == 'report' and actual['publishedAt'] is not None:
        raise ValueError('unfinished report lifecycle required')
    return actual


def report_fact_binding(connection, authority):
    """Own case evidence stays valid when only another case's aggregate status advances.

    A task-wide proposal change cannot be attributed to a particular case and is
    intentionally a hold. The ordinary stage requirement is never rewritten.
    """
    from tools.question_bank.feedback_prepared_stage import _requirements
    requirement = _requirements(connection, authority)['requirement']
    report = requirement['report']
    return {'report': {key: report[key] for key in ('task_id','report_id','question_id',
        'categories_json','case_ids_json','intake_status','proposal_hash')}, 'case': requirement['case']}


def _fact_matches(connection, fact, task_id, case_ids):
    authority = {'kind': 'report', 'taskId': task_id, 'caseId': fact['case_id']}
    if fact['case_id'] not in case_ids: return False
    path = Path(fact['artifact_ref'])
    if path.is_symlink() or not path.is_file() or _hash(path.read_bytes()) != fact['artifact_sha256']: return False
    artifact = _read_fact_artifact(path)
    from tools.question_bank import feedback_source_tf_delivery as api
    reference = artifact.get('plan') or {}
    plan_path = Path(reference.get('ref', ''))
    if plan_path.is_symlink() or not plan_path.is_file(): return False
    raw_plan = plan_path.read_bytes()
    if _hash(raw_plan) != reference.get('sha256'): return False
    try: plan = api._verify(json.loads(raw_plan), api.PLAN)
    except (ValueError, KeyError, TypeError): return False
    if (plan.get('planHash') != fact['plan_hash']
            or plan['source']['taskRequirement']['requirementHash'] != fact['requirement_hash']
            or artifact != artifact_for(None, plan_path, plan)
            or json.loads(fact['read_document_ids_json']) != artifact['readDocumentIds']
            or json.loads(fact['write_document_ids_json']) != artifact['writeDocumentIds']): return False
    contract = artifact.get('completionContract') or {}
    if (artifact.get('schemaVersion') != SCHEMA or artifact.get('authority') != authority
            or contract.get('authority') != authority or contract.get('reason') != fact['reason']
            or contract.get('schemaVersion') != CONTRACT
            or sha256_json(contract) != fact['completion_contract_hash']
            or contract.get('reportFactBinding') != report_fact_binding(connection, authority)
            or contract.get('before', {}).get('caseIds') != case_ids
            or fact['parent_published_at'] != (fact['recorded_at'] if contract.get('parentMayComplete') else None)): return False
    return True


def _read_fact_artifact(path):
    # Only references written by the verified append-only ledger writer reach
    # this path. Never treat a JSON success flag as an independent proof.
    try: return json.loads(path.read_bytes())
    except (ValueError, OSError): return {}


def parent_can_complete(connection, request, lifecycle, *, include_current_case=True):
    """Only decided no-change cases and matching immutable publication facts close a report."""
    if lifecycle['kind'] != 'report': return False
    decisions = {row['case_id']: row for row in lifecycle['caseDecisions']}
    for case_id in lifecycle['caseIds']:
        decision = decisions.get(case_id, {}).get('decision')
        if decision == 'no_change': continue
        if decision != 'fix_required': return False
        if include_current_case and case_id == request['authority']['caseId']: continue
        matches = [row for row in lifecycle['publications'] if row['case_id'] == case_id]
        if not matches or not _fact_matches(connection, matches[-1], request['authority']['taskId'], lifecycle['caseIds']):
            return False
    return True


def completion_contract(connection, request, lifecycle):
    if request['authority']['kind'] == 'report':
        from tools.question_bank.feedback_source_tf_report_review import REPORT_REQUEST
        if request['schemaVersion'] != REPORT_REQUEST or not request.get('reportReview'):
            # Historical v1 report units keep their missing-review hold.
            return None
        from tools.question_bank.feedback_prepared_stage import _requirements
        requirement_hash = _requirements(connection, request['authority'])['requirementHash']
        if any(row['case_id'] == request['authority']['caseId'] and row['requirement_hash'] == requirement_hash
               for row in lifecycle['publications']):
            raise ValueError('report case already has a matching publication fact; only exact completion replay is allowed')
    return {'schemaVersion': CONTRACT, 'authority': deepcopy(request['authority']),
        'reason': REASON, 'before': deepcopy(lifecycle),
        'parentMayComplete': parent_can_complete(connection, request, lifecycle),
        'reportFactBinding': report_fact_binding(connection, request['authority']) if lifecycle['kind'] == 'report' else None}


class _MatchingReplay:
    """Normalize only this artifact's proven own transition, never a CLI override."""
    def __init__(self, plan, artifact_path, artifact_hash):
        self.plan = plan; self.path = Path(artifact_path); self.digest = artifact_hash

    def lifecycle(self, connection, request, actual):
        if request != self.plan['request']:
            raise ValueError('completion replay cannot normalize a foreign request')
        expected = self.plan['source']['taskLifecycle']
        if actual == expected: return deepcopy(expected)
        if not self.path.is_file() or _hash(self.path.read_bytes()) != self.digest:
            raise ValueError('matching completed artifact bytes required')
        contract = self.plan['source']['completionContract']; authority = request['authority']
        if actual['kind'] == 'improvement':
            latest = actual['latestResult']
            if (actual['expectedStatus'] != 'completed' or not latest or latest['status'] != 'completed'
                    or latest['reason'] != contract['reason'] or latest['artifact_ref'] != str(self.path)
                    or latest['artifact_sha256'] != self.digest or actual['updatedAt'] != latest['recorded_at']):
                raise ValueError('improvement lifecycle is not this exact completed transition')
            prior = _rows(connection, 'SELECT * FROM improvement_result_history WHERE task_id=? AND id<? ORDER BY id DESC LIMIT 1',
                (authority['taskId'], latest['id']))
            if (prior[0] if prior else None) != expected['latestResult']:
                raise ValueError('improvement completion history ancestry differs')
        else:
            publications = actual['publications']
            own = [row for row in publications if row['artifact_ref'] == str(self.path) and row['artifact_sha256'] == self.digest]
            if len(own) != 1: raise ValueError('one matching report publication fact required')
            row = own[0]
            if (row['case_id'] != authority['caseId'] or row['reason'] != contract['reason']
                    or row['plan_hash'] != self.plan['planHash']
                    or row['completion_contract_hash'] != sha256_json(contract)
                    or row['requirement_hash'] != self.plan['source']['taskRequirement']['requirementHash']):
                raise ValueError('report publication fact belongs to another completion')
            if row['parent_published_at'] is not None and actual['publishedAt'] != row['parent_published_at']:
                raise ValueError('matching report history requires its exact parent publication after-state')
            prior = expected['publications']
            if publications[:len(prior)] != prior:
                raise ValueError('report publication history ancestry differs')
            for fact in publications[len(prior):]:
                case = fact['case_id']
                if not _fact_matches(connection, fact, authority['taskId'], expected['caseIds']):
                    raise ValueError('later report case publication/binding differs')
                if case == authority['caseId'] and fact['id'] != row['id']:
                    raise ValueError('own report case was superseded by another completion')
            rebuilt = deepcopy(actual); rebuilt['publications'] = prior
            if actual['publishedAt'] != expected['publishedAt']:
                if (actual['publishedAt'] not in {fact['parent_published_at'] for fact in publications}
                        or not parent_can_complete(connection, request, actual, include_current_case=False)):
                    raise ValueError('report parent has no matching all-case completion fact')
                rebuilt['publishedAt'] = expected['publishedAt']
            if rebuilt != expected: raise ValueError('report lifecycle/case set changed outside own completion')
        return deepcopy(expected)

    @contextmanager
    def active(self):
        if _REPLAY.get() is not None: raise ValueError('nested source TF completion replay refused')
        token = _REPLAY.set(self)
        try: yield
        finally: _REPLAY.reset(token)


class _SourceTfCompletionVerification(dict):
    def __init__(self, value, terminal, contract):
        super().__init__(value); self.__terminal = terminal; self.__contract = deepcopy(contract)

    def assert_completion_arguments(self, expected_status, reason):
        if (self.__contract['authority']['kind'] != 'improvement'
                or self.__contract['before']['expectedStatus'] != expected_status or self.__contract['reason'] != reason):
            raise ValueError('source TF completion CAS/reason differs from approved publication scope')

    def assert_current(self, connection):
        self.__terminal(connection)

    @property
    def contract(self): return deepcopy(self.__contract)


def artifact_for(root, plan_path, plan):
    result_path = Path(plan_path).parent / 'publication/result.json'
    return {'schemaVersion': SCHEMA, 'authority': deepcopy(plan['request']['authority']),
        'plan': {'ref': str(plan_path), 'sha256': _hash(Path(plan_path).read_bytes())},
        'publicationResult': {'ref': str(result_path), 'sha256': _hash(result_path.read_bytes())},
        'completionContract': deepcopy(plan['source'].get('completionContract')),
        'readDocumentIds': sorted(plan['public']['baseline']),
        'writeDocumentIds': sorted(plan['public']['writeFieldsByDocument'])}


def verify_completion(root, artifact, *, connection, artifact_ref, coordinator=None, evaluation=None, live_reader=None):
    """Recheck genuine publication and the complete current read set, including inside CAS."""
    from tools.question_bank import feedback_source_tf_delivery as api
    root = _physical(root); path = _inside(root, artifact['plan']['ref'], private=True)
    artifact_path = _inside(root, artifact_ref, private=True); artifact_bytes = _bytes(artifact)
    if artifact.get('schemaVersion') != SCHEMA: raise ValueError('dedicated source TF completion schema required')
    files = {}; plan = api._verify(_ref(root, artifact['plan'], files, private=True), api.PLAN)
    if path != Path(artifact['plan']['ref']) or artifact_path != path.parent / 'completion.json':
        raise ValueError('canonical source TF completion/plan path required')
    if not plan['source'].get('completionContract') or artifact != artifact_for(root, path, plan):
        raise ValueError('source TF completion authority/result/read-write/contract differs')
    contract = plan['source']['completionContract']
    if (contract.get('schemaVersion') != CONTRACT or contract.get('authority') != plan['request']['authority']
            or contract.get('before') != plan['source']['taskLifecycle'] or contract.get('reason') != REASON):
        raise ValueError('publication did not approve this exact completion contract')
    if artifact['authority']['kind'] == 'improvement':
        rows = _rows(connection, 'SELECT question_id FROM ai_question_candidates WHERE improvement_task_id=?',
            (artifact['authority']['taskId'],))
        sources = {row['question_id'] for row in rows}
        covered = set(artifact['readDocumentIds']) | {plan['request']['publicationUnit']['originalQuestionId']}
        if not sources or not sources <= covered: raise ValueError('completion improvement source coverage differs')
    coordinator = coordinator or api.normal.coordinator_for(root)
    replay = _MatchingReplay(plan, artifact_path, _hash(artifact_bytes))
    # Persist the earliest native prefixes in the process proof itself as well
    # as the operation cache: the ledger calls the terminal after this function
    # returns and may be invoked through the generic daily completion entry.
    pinned_native = api._native_graph(root, path, plan)
    pinned_intent = api._load(root, path.parent / 'publication/intent.json', files)
    pinned_approval = api._ref(root, pinned_intent['approval'], files, private=True)
    pinned_native.update(api.native.capture_native_guard([pinned_approval]))

    def current(transaction_connection):
        with replay.active():
            api.native.assert_native_guard(pinned_native, terminal=True)
            if artifact_path.exists() and artifact_path.read_bytes() != artifact_bytes:
                raise ValueError('completion artifact changed')
            local_files = dict(files)
            _, _, actual_plan = api._plan(root, path, coordinator, transaction_connection, live=False)
            if actual_plan != plan: raise ValueError('completion plan changed')
            directory = path.parent / 'publication'
            intent = api._load(root, directory / 'intent.json', local_files)
            attempt = api._load(root, directory / 'attempt.json', local_files)
            commit_raw = api._read(root, directory / 'commit.json', local_files)
            commit = api._json(commit_raw)
            result_raw = api._read(root, directory / 'result.json', local_files)
            result = api._json(result_raw)
            if artifact['publicationResult'] != {'ref': str(directory / 'result.json'), 'sha256': _hash(result_raw)}:
                raise ValueError('publication result reference changed')
            approval = api._ref(root, intent['approval'], local_files, private=True)
            guards = api.native.capture_native_guard([approval])
            gates = api._publication_gates(root, path, plan, coordinator, transaction_connection, evaluation)
            payload = api._payload(plan)
            if (intent.get('schemaVersion') != api.RESULT or intent.get('planHash') != plan['planHash']
                    or intent.get('payload') != payload
                    or intent.get('scope') != api._scope(root, path, plan, 'publish', gates=gates)
                    or api.verify_independent_approval(approval, intent['scope'], production=True) != intent.get('nativeBinding')
                    or api._git(root, plan) != intent.get('gitHead')
                    or attempt != {'planHash': plan['planHash'], 'intentHash': sha256_json(intent)}):
                raise ValueError('genuine source TF publication intent/attempt/approval/gates differ')
            recorded = result.get('liveReadback')
            versions = api._readback(plan, payload, commit, recorded)
            expected = {'schemaVersion': api.RESULT, 'planHash': plan['planHash'], 'unitHash': plan['unitHash'],
                'status': 'published_readback_matched', 'transportSchema': api.uploader.READ_SET_TF_TRUTH_SCHEMA,
                'commit': {'ref': str(directory / 'commit.json'), 'sha256': _hash(commit_raw)},
                'writeVersions': versions, 'liveReadbackHash': sha256_json(recorded), 'liveReadback': recorded,
                'FirestoreWritten': True, 'completionRecorded': False, 'remainingGates': ['task_completion_dispatch']}
            if result != expected: raise ValueError('exact source TF publication/readback result required')
            snapshot = api._snapshot(plan['request']) if live_reader is None else live_reader(plan['request'])
            api._readback(plan, payload, commit, snapshot)
            api._terminal_publication_guard(root, path, plan, transaction_connection, gates, local_files, guards)
            if artifact_path.exists() and artifact_path.read_bytes() != artifact_bytes:
                raise ValueError('completion artifact changed at terminal boundary')
            if task_lifecycle(transaction_connection, plan['request']) != plan['source']['taskLifecycle']:
                raise ValueError('completion lifecycle changed at terminal boundary')
            api.native.assert_native_guard(pinned_native, terminal=True)
    current(connection)
    return _SourceTfCompletionVerification({'authority': artifact['authority'], 'planHash': plan['planHash'],
        'readDocumentIds': artifact['readDocumentIds'], 'writeDocumentIds': artifact['writeDocumentIds'],
        'requirementHash': plan['source']['taskRequirement']['requirementHash'], 'liveReadbackMatched': True}, current, contract)
