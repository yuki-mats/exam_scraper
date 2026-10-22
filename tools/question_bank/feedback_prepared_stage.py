"""One source-bound stage: prepare privately, approve natively, use normal writer.

No Firestore writes. A preview proves prospective patch bytes only. A saved
receipt requires the normal writer, work version, checkpoint and exact readback.
Interrupted/partial applications hold; they are never automatically reapplied.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from copy import deepcopy
import json
import re
from pathlib import Path
import secrets
import shlex
import sqlite3
import sys
import threading

from scripts.common.question_identity import SourceIdentityBinding, load_source_record_inventory
from scripts.upload.upload_questions_to_firestore import DOC_COMPARE_KEYS
from scripts.common.scoped_canonical_context import reader_dependencies
from tools.question_bank.feedback_legacy_review_context import (
    PRIVATE, MANIFEST, _bytes, _hash, _inside, _json, _object, _physical, _read, _ref, _text, _unchanged,
)
from tools.question_bank.feedback_task_requirements import task_requirement_binding
from tools.question_bank.feedback_verified_save import _snapshots, _exclusive, LEDGER
from tools.question_review_console.independent_approval import verify_independent_approval, scope_marker
from tools.question_review_console.projection import sha256_json
from tools.question_review_console.qualification_runs import (
    QualificationRunCoordinator, QualificationRunError, _maintenance_session_phases,
    _validated_question_work_queue, stage_input_readiness_issues, candidate_targets,
    QuestionValidationResult,
)
from tools.question_review_console.question_patch_proposal import assert_target_resolvable

REQUEST = 'feedback-prepared-stage-request/v1'
PLAN = 'feedback-prepared-stage-plan/v1'
PREVIEW = 'feedback-prepared-stage-preview-receipt/v1'
SAVED = 'feedback-prepared-stage-save-receipt/v1'
FAILED = 'feedback-prepared-stage-validation-failure/v1'
STAGES = frozenset({'question_type', 'question_intent', 'correct_choice', 'law_context', 'explanation', 'law_audit', 'question_set'})
LIVE_FIELDS = frozenset(DOC_COMPARE_KEYS) | {'choiceTextList', 'isCalculationQuestion', 'questionIntent'}
REQUIRED_LIVE = frozenset({'originalQuestionId', 'qualificationId', 'isDeleted', 'isChoiceOnly'})


def _requirements(connection, authority):
    _object(authority, {'kind', 'taskId'}, {'caseId'})
    _text(authority['taskId'])
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        if authority['kind'] == 'improvement' and set(authority) == {'kind', 'taskId'}:
            return task_requirement_binding(cursor, authority['taskId'])
        if authority['kind'] != 'report' or not authority.get('caseId'):
            raise ValueError('explicit improvement or report/case authority required')
        row = cursor.execute('SELECT task_id,report_id,question_id,categories_json,case_ids_json,case_statuses_json,'
            'intake_status,decision,decision_reason,evidence_ref,proposal_hash FROM report_tasks WHERE task_id=?',
            (authority['taskId'],)).fetchone()
        decision = cursor.execute('SELECT task_id,case_id,decision,reason,evidence_ref,decided_at '
            'FROM report_case_decisions WHERE task_id=? AND case_id=?',
            (authority['taskId'], authority['caseId'])).fetchone()
        if (row is None or row['intake_status'] != 'complete' or authority['caseId'] not in json.loads(row['case_ids_json'])
                or decision is None or decision['decision'] != 'fix_required'):
            raise ValueError('complete report and fix_required individual case binding required')
        requirement = {'schemaVersion': 'feedback-report-case-requirement/v1', 'report': dict(row), 'case': dict(decision)}
        return {'requirement': requirement, 'requirementHash': sha256_json(requirement)}
    finally:
        cursor.close()


def _request(root, path, files):
    value = _json(_read(root, path, files, private=True))
    _object(value, {'schemaVersion', 'qualification', 'listGroupId', 'questionId', 'stageId', 'source',
                    'authority', 'live', 'primaryEvidence'}, {'retryFromFailure'})
    if value['schemaVersion'] != REQUEST or value['stageId'] not in STAGES:
        raise ValueError('dedicated single-stage request required')
    for field in ('qualification', 'listGroupId', 'questionId'):
        _text(value[field])
        if '/' in value[field] or value[field] in ('.', '..'):
            raise ValueError('single source directory/question identity required')
    _object(value['source'], {'ref', 'sha256', 'binding'})
    _object(value['source']['binding'], {'sourceQuestionKey', 'reviewQuestionId', 'sourceRecordRef'})
    binding = SourceIdentityBinding.from_mapping(value['source']['binding'])
    if not binding.is_complete() or binding.as_mapping() != value['source']['binding']:
        raise ValueError('complete exact SourceIdentityBinding required')
    source = _inside(root, value['source']['ref'])
    expected = root / 'output' / value['qualification'] / 'questions_json' / value['listGroupId'] / '00_source'
    if source.parent != expected:
        raise ValueError('source file outside requested group')
    source_data = _ref(root, {k: value['source'][k] for k in ('ref', 'sha256')}, files, decode=False)
    manifest = {}
    for line in _read(root, root / MANIFEST, files).splitlines():
        if line:
            entry = _object(_json(line), {'path', 'sha256'})
            if entry['path'] in manifest:
                raise ValueError('duplicate source preservation manifest entry')
            manifest[entry['path']] = entry['sha256']
    payloads = {}
    for source_file in sorted(expected.glob('*.json')):
        data = _read(root, source_file, files)
        if manifest.get(str(source_file.relative_to(root))) != _hash(data):
            raise ValueError('source not preserved by manifest')
        payloads[source_file] = _json(data)
    inventory = load_source_record_inventory(expected, qualification=value['qualification'], list_group_id=value['listGroupId'])
    matches = [entry for entry in inventory if entry.identity.binding == binding]
    if len(matches) != 1 or matches[0].path != source:
        raise ValueError('source binding must resolve to requested file exactly once')
    for entry in inventory:
        payload = payloads[entry.path]
        if entry.record != (payload.get('question_bodies') if isinstance(payload, dict) else payload)[entry.record_index]:
            raise ValueError('source reader bytes drift')
    if _hash(source_data) != value['source']['sha256']:
        raise ValueError('source bytes changed')
    live = _object(value['live'], {'originalQuestionId', 'qualificationId', 'questionIds', 'readFields'})
    for field in ('originalQuestionId', 'qualificationId'):
        _text(live[field])
    for field in ('questionIds', 'readFields'):
        if not isinstance(live[field], list) or not live[field] or len(set(live[field])) != len(live[field]):
            raise ValueError('explicit unique nonempty live IDs/mask required')
        for item in live[field]: _text(item)
    if not REQUIRED_LIVE.issubset(live['readFields']) or not set(live['readFields']).issubset(LIVE_FIELDS):
        raise ValueError('safe complete live identity mask required')
    if not isinstance(value['primaryEvidence'], list) or not value['primaryEvidence']:
        raise ValueError('fixed primary evidence required')
    for evidence in value['primaryEvidence']:
        _ref(root, evidence, files, private=True, decode=False)
    if 'retryFromFailure' in value:
        _ref(root, value['retryFromFailure'], files, private=True, decode=False)
    return value


def _request_identity(request):
    return {key: value for key, value in request.items() if key != 'retryFromFailure'}


def _preparation_input_hash(plan):
    """Bind complete prospective inputs, including retry ancestry, before preview."""
    return sha256_json({key: value for key, value in plan.items()
                        if key not in ('preparationBinding', 'commitView', 'planHash')})


def _failed_attempt(coordinator, fixed, output, previous):
    """Derive normal retry feedback only from the writer's persisted validation."""
    child = coordinator.store.get(fixed['qualification'], fixed['childRunId'])
    results = output.get('questionResults')
    if (output.get('preview') or not isinstance(results, list) or len(results) != 1
            or child.get('parentRunId') != fixed['parentRunId']
            or child.get('externalApprovalRequired') is not True
            or child.get('preparedStagePreparation') != fixed.get('preparationBinding')
            or not fixed.get('preparationBinding')
            or fixed['preparationBinding'].get('preparationInputHash') != _preparation_input_hash(fixed)
            or child.get('batchQuestionResults') != results
            or child.get('candidateTransactionOpen') is not False
            or child.get('canonicalWriteStarted') is not False
            or child.get('deltaUnknown') is True
            or child.get('workVersionReceipt', {}).get('recordedCount') != 0):
        raise ValueError('safe actual normal preview failure required')
    result = results[0]
    if (result.get('questionId') != fixed['request']['questionId'] or result.get('status') != 'failed'
            or result.get('changedFiles') or result.get('policyChanged')
            or not any(command.get('status') == 'fail' for command in result.get('commands') or [])):
        raise ValueError('actual same-question mechanical validation failure required')
    attempt_number = len(previous) + 1
    normalized = QuestionValidationResult(question_id=result['questionId'], status='failed',
        summary=str(result.get('summary') or '機械検査に失敗しました。'),
        commands=tuple(result.get('commands') or []), changed_files=())
    feedback = coordinator._question_feedback(child, normalized, attempt=attempt_number,
        stage_id=fixed['stageId'], validation_issues=result.get('validationIssues') or ())
    owners = {issue.get('ownerStageId') for issue in feedback.get('issues') or []
              if issue.get('schemaVersion') and issue.get('ownerStageId')}
    blocked = feedback.get('status') == 'blocked' or bool(owners and owners != {fixed['stageId']}) or attempt_number >= 3
    attempt = {'attempt': attempt_number, 'childRunId': fixed['childRunId'],
        'status': 'blocked' if blocked else 'failed', 'feedback': feedback,
        'requestedModel': child.get('requestedModel'), 'requestedReasoningEffort': child.get('requestedReasoningEffort'),
        'modelAttempt': deepcopy(child.get('modelAttempt')), 'retryable': not blocked}
    return attempt


def _failure_marker(path, receipt):
    return {'ref': str(path), 'sha256': _hash(_bytes(receipt)), 'failureHash': receipt['failureHash']}


def _save_failure(root, destination, fixed, output, *, coordinator):
    previous = (fixed.get('retryContext') or {}).get('validationAttempts', [])
    attempt = _failed_attempt(coordinator, fixed, output, previous)
    _unchanged(root, fixed['inputFiles'])
    _canonical_before(root, {'transactionBefore': fixed['canonicalInputsBefore']})
    receipt = {'schemaVersion': FAILED, 'failedPlan': fixed, 'validationResult': output['questionResults'][0],
        'validationAttempts': [*previous, attempt], 'retryable': attempt['retryable'],
        'formalPatchSaved': False, 'FirestoreWritten': False}
    receipt['failureHash'] = sha256_json(receipt)
    # Close the wrapper parent before receipt I/O. The normal writer's terminal
    # child is immutable; its exact failed question result remains evidence.
    coordinator.store.update(fixed['qualification'], fixed['parentRunId'], status='failed', receiptValidated=False)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _inside(root, destination, private=True); destination.mkdir(mode=0o700)
    path = destination / 'failure-receipt.json'
    _exclusive(path, _bytes(receipt))
    marker = _failure_marker(path, receipt)
    coordinator.store.update_question_stage(fixed['qualification'], fixed['parentRunId'], fixed['request']['questionId'],
        fixed['stageId'], status='queued' if receipt['retryable'] else 'blocked',
        validationAttempts=receipt['validationAttempts'], retryDeferred=receipt['retryable'],
        error=attempt['feedback'].get('reason'), preparedStageFailure=marker)
    coordinator.store.update(fixed['qualification'], fixed['parentRunId'], preparedStageFailure=marker,
        status='failed', receiptValidated=False, error=attempt['feedback'].get('reason'))
    raise ValueError('normal preview validation failed; fixed failure receipt: ' + str(path))


def _retry_context(root, request, files, *, coordinator, connection, _consumed_by=None, _seen=()):
    reference = request.get('retryFromFailure')
    if reference is None:
        return None
    path = _inside(root, reference['ref'], private=True)
    if str(path) in _seen or len(_seen) >= 3:
        raise ValueError('cyclic or excessive normal failure history')
    seen = (*_seen, str(path))
    receipt = _ref(root, reference, files, private=True)
    _object(receipt, {'schemaVersion', 'failedPlan', 'validationResult', 'validationAttempts', 'retryable',
                      'formalPatchSaved', 'FirestoreWritten', 'failureHash'})
    if (receipt['schemaVersion'] != FAILED or receipt['formalPatchSaved'] is not False
            or receipt['FirestoreWritten'] is not False or receipt['retryable'] is not True
            or sha256_json({k: v for k, v in receipt.items() if k != 'failureHash'}) != receipt['failureHash']):
        raise ValueError('exact retryable normal failure receipt required')
    fixed = receipt['failedPlan']; path = _inside(root, reference['ref'], private=True)
    if path.name != 'failure-receipt.json' or (path.parent / 'plan.json').exists():
        raise ValueError('dedicated failed preparation required; successful scopes cannot be retried')
    if fixed.get('preparationBinding', {}).get('destination') != str(path.parent):
        raise ValueError('failure belongs to another preparation destination')
    marker = _failure_marker(path, receipt)
    if reference['sha256'] != marker['sha256']:
        raise ValueError('exact persisted failure receipt bytes required')
    parent = coordinator.store.get(fixed['qualification'], fixed['parentRunId'])
    child = coordinator.store.get(fixed['qualification'], fixed['childRunId'])
    queue = coordinator._queue_stage(parent, fixed['request']['questionId'], fixed['stageId']) or {}
    if (parent.get('status') != 'failed' or parent.get('preparedStageFailure') != marker
            or child.get('preparedStagePreparation') != fixed.get('preparationBinding')
            or not fixed.get('preparationBinding')
            or queue.get('preparedStageFailure') != marker or queue.get('validationAttempts') != receipt['validationAttempts']):
        raise ValueError('completed actual parent/child/queue failure evidence required')
    if fixed['preparationBinding'].get('preparationInputHash') != _preparation_input_hash(fixed):
        raise ValueError('immutable normal preparation inputs/ancestry changed')
    if _request_identity(fixed['request']) != _request_identity(request):
        raise ValueError('failure belongs to another stage/question/request input')
    _unchanged(root, fixed['inputFiles'])
    for relative, digest in fixed['inputFiles'].items():
        if relative in files and files[relative] != digest:
            raise ValueError('failure input binding conflicts')
        files[relative] = digest
    if (_requirements(connection, request['authority']) != fixed['taskRequirement']
            or _live(request) != fixed['liveSnapshots']
            or not coordinator._phase_plan_policy_is_current(fixed['qualification'], fixed['stagePlan'], fixed['stageId'])):
        raise ValueError('failed preparation task/live/current policy changed')
    _canonical_before(root, {'transactionBefore': fixed['canonicalInputsBefore']})
    _require_retry_claim(root, fixed, files=files)
    candidate = coordinator.store.load_prepared_candidate(fixed['qualification'], fixed['childRunId'],
        input_fingerprint_value=fixed['inputFingerprint'], projected_input_hash=fixed['projectedInputHash'])
    if candidate != fixed['preparedCandidate']:
        raise ValueError('failed native candidate changed')
    retry = fixed.get('retryContext')
    previous = retry.get('validationAttempts') if isinstance(retry, dict) else []
    if receipt['validationAttempts'][:-1] != previous:
        raise ValueError('failure history differs from immutable preparation ancestry')
    if retry is not None:
        ancestor = _retry_context(root, fixed['request'], files, coordinator=coordinator,
            connection=connection, _consumed_by=fixed, _seen=seen)
        if ancestor != retry:
            raise ValueError('referenced actual failure history/claim chain differs')
    elif 'retryFromFailure' in fixed['request']:
        raise ValueError('fixed retry ancestry required')
    attempt = _failed_attempt(coordinator, fixed, {'questionResults': [receipt['validationResult']]}, previous)
    if receipt['validationAttempts'] != [*previous, attempt] or receipt['retryable'] != attempt['retryable']:
        raise ValueError('normal failed result/feedback differs')
    claim = path.parent / 'retry-claim.json'
    if _consumed_by is not None:
        if _consumed_by.get('retryContext', {}).get('failure') != reference:
            raise ValueError('failure chain points to another consumed preparation')
        _require_retry_claim(root, _consumed_by, files=files)
    elif claim.exists():
        raise ValueError('failure already consumed or interrupted; inspect retry claim, do not replay')
    return {'failure': reference, 'failureHash': receipt['failureHash'],
            'validationAttempts': receipt['validationAttempts'], 'failedInputFingerprint': fixed['inputFingerprint'],
            'failedProjectedInputHash': fixed['projectedInputHash']}


def _require_retry_claim(root, plan, *, files=None):
    if 'retryContext' not in plan:
        return
    retry = plan['retryContext']
    claim = _json(_read(root, _inside(root, retry['failure']['ref'], private=True).parent / 'retry-claim.json',
                        files if files is not None else {}, private=True))
    expected = {'schemaVersion': 'feedback-prepared-stage-retry-claim/v1', 'failureHash': retry['failureHash'],
        'parentRunId': plan['parentRunId'], 'requestPath': plan['requestPath'],
        'requestHash': plan['inputFiles'][plan['requestPath']]}
    if plan['request'].get('retryFromFailure') != retry['failure'] or claim != expected:
        raise ValueError('same fixed retry failure/claim required')


def _live(request):
    spec = request['live']
    snapshots = _snapshots(sorted(spec['questionIds']), sorted(spec['readFields']))
    if set(snapshots) != set(spec['questionIds']):
        raise ValueError('all live siblings required')
    for qid, snapshot in snapshots.items():
        if (snapshot.get('questionId') != qid or snapshot.get('exists') is not True
                or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?Z', str(snapshot.get('updateTimeExact', '')))
                or not isinstance(snapshot.get('fields'), dict)
                or snapshot['fields'].get('originalQuestionId') != spec['originalQuestionId']
                or snapshot['fields'].get('qualificationId') != spec['qualificationId']
                or snapshot['fields'].get('isDeleted') is not False or snapshot['fields'].get('isChoiceOnly') is not False
                or set(snapshot['fields']) - set(spec['readFields'])):
            raise ValueError('fresh exact masked sibling identity differs')
    return snapshots


def _authority_matches(request, requirement):
    req = requirement['requirement']
    question_ids = ({row['question_id'] for row in req['sources']} if 'sources' in req else {req['report']['question_id']})
    accepted = {*request['live']['questionIds'], request['live']['originalQuestionId'], request['questionId'],
                *request['source']['binding'].values()}
    if not question_ids or not question_ids.issubset(accepted):
        raise ValueError('task/report requirements refer to another question')


def _inputs(root, request, stage_plan, files):
    group = root / 'output' / request['qualification'] / 'questions_json' / request['listGroupId']
    mutable = set(stage_plan.get('allowedPatchFiles') or []) | set(stage_plan.get('allowedWriteFiles') or [])
    selected = []
    for path in sorted(group.rglob('*.json')):
        if str(path.relative_to(root)) not in mutable:
            _read(root, path, files)
            selected.append(str(path.relative_to(root)))
    for path in reader_dependencies(root, [root / 'tools/question_bank/feedback_prepared_stage.py',
                                         root / 'tools/question_review_console/qualification_runs.py']):
        _read(root, path, files)
    for relative in ('config/question_maintenance_workflow.toml', 'config/qualification_rules.json',
                     'config/question_maintenance_llm.toml', *stage_plan.get('canonicalDocs', [])):
        path = _inside(root, root / relative)
        if path.is_dir():
            for child in sorted(path.rglob('*')):
                if child.is_file(): _read(root, child, files)
        else:
            _read(root, path, files)
    return selected


def _stage_inputs(coordinator, request, plan, parent, prompt):
    qualification, stage = request['qualification'], request['stageId']
    phase = {'id': stage}
    spec = coordinator._question_stage_spec(qualification, parent['runId'], phase, request['questionId'], plan, prompt, parent=parent)
    if spec.get('status') not in ('queued', 'prepared'):
        raise ValueError('one applicable queued source-bound stage required')
    target = dict(spec['target'])
    if SourceIdentityBinding.from_mapping(target).as_mapping() != request['source']['binding']:
        raise ValueError('normal runner selected another source identity')
    work_key = str((spec.get('queueStage') or {}).get('workItemKey') or '')
    projection = coordinator._write_projected_question_input(qualification, parent['runId'], target, work_key, stage)
    if projection is None or stage_input_readiness_issues(projection['record'], stage):
        raise ValueError('normal stage input is not ready')
    target['_projectedInputPath'] = projection['path']
    targets = candidate_targets(request['questionId'], stage, spec['scopedPlan'], current_record=projection['record'])
    binding = SourceIdentityBinding.from_mapping(target)
    for candidate in targets:
        aliases = {str(alias) for group in (spec['scopedPlan'].get('targetRecordScopes') or {}).get(candidate.path, [])
                   for alias in group if alias}
        assert_target_resolvable(coordinator.repo_root, candidate.path, binding=binding, aliases=aliases)
    return {**spec, 'target': target, 'candidateRecord': projection['record'], 'sourceRecord': projection.get('sourceRecord'),
        'questionIssueCorrectionEvidence': projection.get('questionIssueCorrectionEvidence'), 'candidateTargets': targets,
        'projectionUpdate': {'changes': {'projectedInputHash': projection['hash']}}}


def _run(coordinator, plan, *, preview=False, guard=None):
    child = coordinator.store.get(plan['qualification'], plan['childRunId'])
    envelope = coordinator.store.load_prepared_candidate(plan['qualification'], plan['childRunId'],
        input_fingerprint_value=plan['inputFingerprint'], projected_input_hash=plan['projectedInputHash'])
    return coordinator._run_structured_question(plan['qualification'], plan['childRunId'], '', lambda _: None,
        batch_plan=child, stage_id=plan['stageId'], pipeline_stop=threading.Event(),
        model=str(child['requestedModel']), reasoning_effort=str(child['requestedReasoningEffort']),
        input_fingerprint_value=envelope['inputFingerprint'], projected_input_hash=envelope['projectedInputHash'],
        apply_prepared=True, preview_prepared=preview, before_canonical_commit=guard)


def prepare(root, request_path, destination, *, coordinator, connection):
    root = _physical(root); destination = _inside(root, destination, private=True)
    if destination.exists():
        raise ValueError('exclusive new private plan directory required')
    files = {}; request = _request(root, request_path, files)
    requirement = _requirements(connection, request['authority']); _authority_matches(request, requirement)
    retry = _retry_context(root, request, files, coordinator=coordinator, connection=connection)
    live = _live(request)
    qualification, stage = request['qualification'], request['stageId']
    plan = coordinator._plan(qualification, stage, 'refresh', None, stage_ids=[stage],
        list_group_ids=[request['listGroupId']], question_ids=[request['questionId']])
    preview = coordinator.preview(qualification, stage, 'refresh', stage_ids=[stage],
        list_group_ids=[request['listGroupId']], question_ids=[request['questionId']], _prepared_plan=plan)
    if not preview.get('canStart') or len(plan.get('progressTargets') or []) != 1:
        raise ValueError('normal runner cannot start exactly one question/stage')
    plan['llmProfile'] = deepcopy(preview.get('llmProfile'))
    plan['targetIdentity'] = deepcopy(preview['targetIdentity'])
    plan['previewPlanHash'] = preview['previewPlanHash']
    if (not isinstance(plan['llmProfile'], dict) or plan['llmProfile'].get('name') != 'codex_only'):
        raise ValueError('current codex_only production profile required')
    coordinator.app_server.assert_profile_access('codex_only', force=False)
    prompt = coordinator.workflow.prompt_from_plan(plan)['prompt']
    executions, summary = _validated_question_work_queue(plan)
    if len(executions) != 1 or summary['workItemCount'] != 1:
        raise ValueError('single normal question-stage queue required')
    stage_plan = deepcopy(plan)
    plan.update(kind='orchestration', workType='maintenance_flow', questionExecutions=executions,
        phaseExecutions=[{**phase, 'status': 'pending'} for phase in _maintenance_session_phases(plan)],
        questionConcurrency=1, modelBatchSize=1, parallelWorkerLimit=1, writeWorkerLimit=1,
        sandbox='read-only', provider=coordinator.app_server.provider)
    selected_files = _inputs(root, request, plan, files)
    canonical_inputs_before = []
    for relative in sorted(set(plan.get('allowedPatchFiles') or []) | set(plan.get('allowedWriteFiles') or [])):
        target_path = _inside(root, root / relative)
        canonical_inputs_before.append({'path': relative, 'exists': target_path.is_file(),
            'bytesHex': target_path.read_bytes().hex() if target_path.is_file() else None})
    parent = coordinator._create_execution_run(plan, status='queued', prompt=prompt, append_receipt_contract=False)
    parent = coordinator.store.update(qualification, parent['runId'], externalApprovalRequired=True)
    try:
        spec = _stage_inputs(coordinator, request, stage_plan, parent, prompt)
        projected_bytes = _read(root, root / spec['target']['_projectedInputPath'], files)
        if _hash(projected_bytes) != spec['projectionUpdate']['changes']['projectedInputHash']:
            raise ValueError('normal projected input bytes changed before generation')
        if retry:
            if ((spec.get('queueStage') or {}).get('inputFingerprint') != retry['failedInputFingerprint']
                    or spec['projectionUpdate']['changes']['projectedInputHash'] != retry['failedProjectedInputHash']):
                raise ValueError('retry requires the same normal stage input and projected bytes')
            claim = {'schemaVersion': 'feedback-prepared-stage-retry-claim/v1', 'failureHash': retry['failureHash'],
                'parentRunId': parent['runId'], 'requestPath': str(Path(request_path).relative_to(root)),
                'requestHash': _hash(Path(request_path).read_bytes())}
            claim_path = _inside(root, retry['failure']['ref'], private=True).parent / 'retry-claim.json'
            _exclusive(claim_path, _bytes(claim))
            _read(root, claim_path, files, private=True)
            coordinator.store.update_question_stage(qualification, parent['runId'], request['questionId'], stage,
                validationAttempts=retry['validationAttempts'], priorValidationFeedback=[], retryModelRequired=True)
            parent = coordinator.store.get(qualification, parent['runId'])
            spec['queueStage'] = coordinator._queue_stage(parent, request['questionId'], stage)
        parent = coordinator.store.get(qualification, parent['runId'])
        _unchanged(root, files)
        prepared = coordinator._prepare_question_candidate_attempt(qualification, parent['runId'], [spec], {'id': stage}, prompt,
            parent, parent=parent, emit=lambda _: None)
        route = prepared.get('attemptMetadata') or {}
        if route.get('fallbackUsed') is not False or route.get('backendKind') != 'codex_app_server':
            raise ValueError('configured native primary attempt required; fallback prohibited')
        coordinator._run_structured_question(qualification, prepared['childId'], prepared['prompt'], lambda _: None,
            batch_plan=prepared['batchPlan'], stage_id=stage, pipeline_stop=threading.Event(),
            prepared_records=prepared['recordsByQuestion'], prepared_source_records=prepared['sourceRecordsByQuestion'],
            prepared_targets=prepared['candidateTargetsByQuestion'], model=prepared['requestedModel'],
            maintenance_attempt=prepared.get('attemptRoute'), reasoning_effort=prepared['requestedReasoningEffort'],
            input_fingerprint_value=prepared['inputFingerprint'], projected_input_hash=prepared['projectedInputHash'], prepare_only=True)
        fixed = {'schemaVersion': PLAN, 'qualification': qualification, 'stageId': stage,
            'parentRunId': parent['runId'], 'childRunId': prepared['childId'],
            'inputFingerprint': prepared['inputFingerprint'], 'projectedInputHash': prepared['projectedInputHash'],
            'request': request, 'requestPath': str(Path(request_path).relative_to(root)), 'taskRequirement': requirement,
            'liveSnapshots': live, 'liveMembershipReader': 'feedback_verified_save._snapshots',
            'liveMembershipPredicate': {'originalQuestionId': request['live']['originalQuestionId'],
                                        'isDeleted': False, 'isChoiceOnly': False},
            'sourceToLiveMappingApproved': False, 'stagePlan': prepared['batchPlan'], 'inputFiles': files,
            'groupInputSelection': selected_files, 'canonicalInputsBefore': canonical_inputs_before,
            'formalPatchSaved': False, 'FirestoreWritten': False}
        if retry:
            fixed['retryContext'] = retry
        candidate = coordinator.store.load_prepared_candidate(qualification, prepared['childId'],
            input_fingerprint_value=fixed['inputFingerprint'], projected_input_hash=fixed['projectedInputHash'])
        fixed['preparedCandidate'] = candidate
        fixed['preparationBinding'] = {'schemaVersion': 'feedback-prepared-stage-attempt-binding/v1',
            'destination': str(destination), 'requestHash': files[fixed['requestPath']],
            'inputFingerprint': fixed['inputFingerprint'], 'projectedInputHash': fixed['projectedInputHash'],
            'candidateHash': candidate['contentHash'], 'preparationInputHash': _preparation_input_hash(fixed)}
        coordinator.store.update(qualification, prepared['childId'], preparedStagePreparation=fixed['preparationBinding'])
        output = _run(coordinator, fixed, preview=True)
        if not output.get('preview') or output['preview'].get('recordWorkVersion') is not True:
            if _requirements(connection, request['authority']) != requirement or _live(request) != live:
                raise ValueError('task/live inputs changed during failed preparation')
            _save_failure(root, destination, fixed, output, coordinator=coordinator)
        fixed['commitView'] = output['preview']
        _unchanged(root, files)
        _canonical_before(root, {'transactionBefore': canonical_inputs_before})
        if _requirements(connection, request['authority']) != requirement or _live(request) != live:
            raise ValueError('task/live inputs changed during preparation')
        fixed['planHash'] = sha256_json(fixed)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _inside(root, destination, private=True); destination.mkdir(mode=0o700)
        _exclusive(destination / 'plan.json', _bytes(fixed))
        receipt = _preview_receipt(fixed)
        _exclusive(destination / 'preview-receipt.json', _bytes(receipt))
        load_plan(root, destination / 'plan.json', coordinator=coordinator, connection=connection)
        return receipt
    except BaseException as exc:
        coordinator.store.update(qualification, parent['runId'], status='failed', receiptValidated=False, error=str(exc))
        raise


def _preview_receipt(plan):
    return {'schemaVersion': PREVIEW, 'planHash': plan['planHash'], 'formalPatchSaved': False,
        'workVersionRecorded': False, 'checkpointRecorded': False, 'FirestoreWritten': False,
        'prospectivePatchHashes': {p['path']: p['afterSha256'] for p in plan['commitView']['patches']}}


def _canonical_before(root, view):
    for entry in view['transactionBefore']:
        path = _inside(root, root / entry['path'])
        data = path.read_bytes() if path.is_file() else None
        if path.exists() != entry['exists'] or (data.hex() if data is not None else None) != entry['bytesHex']:
            raise ValueError('canonical/workVersion before/existence third value')


def load_plan(root, path, *, coordinator, connection, saved=False):
    root = _physical(root); path = _inside(root, path, private=True)
    plan = _json(_read(root, path, {}, private=True))
    if plan.get('schemaVersion') != PLAN or sha256_json({k: v for k, v in plan.items() if k != 'planHash'}) != plan.get('planHash'):
        raise ValueError('fixed prepared-stage plan differs')
    if (plan.get('formalPatchSaved') is not False or plan.get('FirestoreWritten') is not False
            or plan.get('sourceToLiveMappingApproved') is not False
            or plan.get('liveMembershipReader') != 'feedback_verified_save._snapshots'
            or plan.get('liveMembershipPredicate') != {'originalQuestionId': plan['request']['live']['originalQuestionId'],
                'isDeleted': False, 'isChoiceOnly': False}):
        raise ValueError('preview must not claim a save/publication')
    if _json(_read(root, path.parent / 'preview-receipt.json', {}, private=True)) != _preview_receipt(plan):
        raise ValueError('complete exact preview receipt required')
    _unchanged(root, plan['inputFiles'])
    checked = _request(root, root / plan['requestPath'], {})
    if checked != plan['request'] or _requirements(connection, checked['authority']) != plan['taskRequirement']:
        raise ValueError('fixed request/task/report-case requirement changed')
    _authority_matches(checked, plan['taskRequirement'])
    group = root / 'output' / checked['qualification'] / 'questions_json' / checked['listGroupId']
    mutable = set(plan['stagePlan'].get('allowedPatchFiles') or []) | set(plan['stagePlan'].get('allowedWriteFiles') or [])
    selection = [str(p.relative_to(root)) for p in sorted(group.rglob('*.json')) if str(p.relative_to(root)) not in mutable]
    if selection != plan['groupInputSelection']:
        raise ValueError('stage input file selection drift')
    current = coordinator.store.load_prepared_candidate(plan['qualification'], plan['childRunId'],
        input_fingerprint_value=plan['inputFingerprint'], projected_input_hash=plan['projectedInputHash'])
    if current != plan['preparedCandidate']:
        raise ValueError('prepared candidate drift')
    _require_retry_claim(root, plan)
    child = coordinator.store.get(plan['qualification'], plan['childRunId'])
    if child.get('externalApprovalRequired') is not True or child.get('parentRunId') != plan['parentRunId']:
        raise ValueError('required independent approval boundary changed')
    if 'preparationBinding' in plan and child.get('preparedStagePreparation') != plan['preparationBinding']:
        raise ValueError('fixed normal preparation binding changed')
    if ('preparationInputHash' in plan.get('preparationBinding', {})
            and plan['preparationBinding']['preparationInputHash'] != _preparation_input_hash(plan)):
        raise ValueError('immutable normal preparation inputs/ancestry changed')
    if not coordinator._phase_plan_policy_is_current(plan['qualification'], plan['stagePlan'], plan['stageId']):
        raise ValueError('stage policy changed')
    changed_paths = {p['path'] for p in plan['commitView']['patches']} if saved else set()
    _canonical_before(root, {'transactionBefore': [entry for entry in plan['canonicalInputsBefore']
                                                  if entry['path'] not in changed_paths]})
    if not saved: _canonical_before(root, plan['commitView'])
    return plan


def approval_scope(root, path, *, coordinator, connection):
    plan = load_plan(root, path, coordinator=coordinator, connection=connection)
    return _scope_data(_physical(root), _inside(_physical(root), path, private=True), plan, connection)


def _scope_data(root, path, plan, connection):
    command = ' '.join(shlex.quote(s) for s in [sys.executable, '-m', 'tools.question_bank.feedback_prepared_stage',
        '--repo-root', str(root), '--db', _ledger_path(connection), 'scope', '--plan', str(path)])
    return {'schemaVersion': 'feedback-prepared-stage-independent-scope/v1', 'action': 'save_one_normal_source_bound_stage',
        'planPath': str(path), 'planHash': plan['planHash'], 'candidateHash': plan['preparedCandidate']['contentHash'],
        'taskRequirement': plan['taskRequirement'], 'sourceBinding': plan['request']['source']['binding'],
        'stageId': plan['stageId'], 'liveSnapshotsHash': sha256_json(plan['liveSnapshots']),
        'commitViewHash': sha256_json(plan['commitView']), 'FirestoreWritten': False,
        'sourceToLiveMappingApproved': False, 'liveMembershipPredicate': plan['liveMembershipPredicate'],
        'nativeCreationCall': 'let r=await tools.exec_command(' + json.dumps({'cmd': command, 'workdir': str(root),
            'max_output_tokens': 12000, 'yield_time_ms': 30000}, ensure_ascii=False)
            + ');let out=r.output;while(r.session_id){r=await tools.write_stdin({session_id:r.session_id,chars:"",yield_time_ms:1000,max_output_tokens:12000});out+=r.output;}'
            + 'if(r.exit_code!==0)throw new Error("scope creation failed");text(out);\n'}


def _ledger_path(connection):
    row = next((row for row in connection.execute('PRAGMA database_list') if row[1] == 'main'), None)
    if row is None or not row[2]: raise ValueError('existing file ledger required')
    return str(_physical(row[2]))


def _stage_evidence(coordinator, plan):
    child = coordinator.store.get(plan['qualification'], plan['childRunId'])
    if (child.get('status') != 'succeeded' or child.get('receiptValidated') is not True
            or child.get('candidateTransactionOpen') is not False or child.get('deltaUnknown') is True
            or child.get('workVersionReceipt', {}).get('recordedCount') != 1
            or len(child.get('batchQuestionResults') or []) != 1
            or child['batchQuestionResults'][0].get('status') != 'succeeded'):
        raise ValueError('actual normal writer/workVersion/checkpoint success required')
    target = {**plan['stagePlan']['progressTargets'][0], 'qualification': plan['qualification']}
    work = coordinator.work_versions.record_for(target)
    if not isinstance(work, dict) or not isinstance(work.get('stages', {}).get(plan['stageId']), dict):
        raise ValueError('actual workVersion record missing')
    recorded = work['stages'][plan['stageId']]
    if (recorded.get('runId') != plan['childRunId']
            or recorded.get('policyFingerprint') != plan['stagePlan']['policyFingerprints'][plan['stageId']]
            or recorded.get('version') != plan['stagePlan']['policyVersions'][plan['stageId']]):
        raise ValueError('actual same-run/current-policy workVersion required')
    checkpoint = coordinator.store.question_detail(plan['qualification'], plan['parentRunId'], plan['request']['questionId'],
        include_attempt_artifacts=False)['execution']
    stage = next((s for s in checkpoint['stages'] if s['stageId'] == plan['stageId']), None)
    if not stage or stage.get('status') != 'validated':
        raise ValueError('actual validated parent checkpoint missing')
    return {'workVersion': work, 'checkpoint': stage, 'questionResults': child['batchQuestionResults'],
            'workVersionReceipt': child['workVersionReceipt']}


def _saved_files(root, plan):
    result = {}
    for before in plan['commitView']['transactionBefore']:
        path = _inside(root, root / before['path'])
        result[before['path']] = _hash(path.read_bytes()) if path.is_file() else None
    for patch in plan['commitView']['patches']:
        path = _inside(root, root / patch['path'])
        if path.read_bytes() != patch['afterUtf8'].encode('utf-8'):
            raise ValueError('actual formal patch bytes differ')
    return result


def apply(root, path, approval_path, *, coordinator, connection):
    root = _physical(root); path = _inside(root, path, private=True)
    receipt_path = path.parent / 'save-receipt.json'
    saved = receipt_path.exists()
    plan = load_plan(root, path, coordinator=coordinator, connection=connection, saved=saved)
    # Scope is stable across pre/post-save. Its verifier needs the pre-save plan;
    # its authority is derived from the same immutable fields, never a fake preview.
    if saved:
        # Construct scope without requiring already-consumed preconditions.
        scope = _scope_from_saved(root, path, plan, connection)
    else:
        scope = approval_scope(root, path, coordinator=coordinator, connection=connection)
    approval_raw = _read(root, approval_path, {}, private=True)
    approval = _json(approval_raw)
    binding = verify_independent_approval(approval, scope, production=True)
    approval_ref = {'ref': str(_inside(root, approval_path, private=True)), 'sha256': _hash(approval_raw)}
    intent = {'schemaVersion': 'feedback-prepared-stage-apply-intent/v1', 'planHash': plan['planHash'],
              'approval': approval_ref, 'nativeBinding': binding, 'scope': scope}
    intent_path = path.parent / 'apply-intent.json'
    if saved:
        receipt = _json(_read(root, receipt_path, {}, private=True))
        expected = _save_receipt(plan, approval_ref, binding, _saved_files(root, plan), _stage_evidence(coordinator, plan))
        if _json(_read(root, intent_path, {}, private=True)) != intent or receipt != expected:
            raise ValueError('saved retransmission evidence drift')
        if _live(plan['request']) != plan['liveSnapshots']:
            raise ValueError('live read-set changed after formal save')
        return receipt
    if intent_path.exists():
        raise ValueError('previous application is incomplete: hold and inspect existing baseline/rollback; do not reapply')
    _exclusive(intent_path, _bytes(intent))
    def guard(view):
        load_plan(root, path, coordinator=coordinator, connection=connection)
        if view != plan['commitView'] or _live(plan['request']) != plan['liveSnapshots']:
            raise ValueError('locked prospective patch/live read-set changed')
        if _read(root, approval_path, {}, private=True) != approval_raw:
            raise ValueError('approval input bytes changed')
        if verify_independent_approval(approval, scope, production=True) != binding:
            raise ValueError('native approval changed at commit boundary')
        if _json(_read(root, intent_path, {}, private=True)) != intent:
            raise ValueError('application intent changed')
        load_plan(root, path, coordinator=coordinator, connection=connection)
    try:
        guard(plan['commitView'])
        outcome = _run(coordinator, plan, guard=guard)
        results = outcome.get('questionResults') or []
        if len(results) != 1 or results[0].get('status') != 'succeeded':
            raise ValueError('normal writer held/failed; inspect persisted baseline/rollback')
        coordinator.store.update_question_stage(plan['qualification'], plan['parentRunId'], plan['request']['questionId'],
            plan['stageId'], validated_receipt=results[0].get('workVersionReceipt'), status='validated', error=None)
        evidence = _stage_evidence(coordinator, plan)
        receipt = _save_receipt(plan, approval_ref, binding, _saved_files(root, plan), evidence)
        _exclusive(receipt_path, _bytes(receipt))
        return receipt
    except BaseException:
        # The normal writer owns rollback. Keep its exact state and the durable
        # intent; a new invocation must not conceal or automatically overwrite it.
        raise


def _save_receipt(plan, approval_ref, binding, actual_files, evidence):
    return {'schemaVersion': SAVED, 'planHash': plan['planHash'], 'approval': approval_ref,
        'nativeBinding': binding, 'actualFiles': actual_files, 'stageEvidence': evidence,
        'formalPatchSaved': True, 'workVersionRecorded': True, 'checkpointRecorded': True, 'FirestoreWritten': False,
        'publicationReady': False, 'evaluationPerformed': False, 'sourceToLiveMappingApproved': False}


def _scope_from_saved(root, path, plan, connection):
    # Use the pre-save scope persisted before any writer operation.
    intent = _json(_read(root, path.parent / 'apply-intent.json', {}, private=True))
    scope = intent.get('scope')
    if scope != _scope_data(root, path, plan, connection):
        raise ValueError('persisted exact native scope missing')
    return scope


def coordinator_for(root):
    from tools.question_review_console.codex_app_server import CodexAppServerClient
    from tools.question_review_console.inventory import QuestionInventory
    from tools.question_review_console.jobs import JobManager
    from tools.question_review_console.model_backend import ProfileModelRouter, load_model_backend_config
    from tools.question_review_console.qualification_workflow import QualificationWorkflow
    from tools.question_review_console.turn_budget import GlobalTurnBudget
    config = load_model_backend_config(root / 'config/question_maintenance_llm.toml')
    runtime = CodexAppServerClient(root, turn_budget=GlobalTurnBudget(config.limits.llm_call_concurrency))
    router = ProfileModelRouter(config, runtime)
    workflow = QualificationWorkflow(root, QuestionInventory(root))
    return QualificationRunCoordinator(root, workflow, None, JobManager(), secrets.token_urlsafe(32), app_server=router)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--db', type=Path, default=LEDGER)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('prepare'); create.add_argument('--request', type=Path, required=True); create.add_argument('--output', type=Path, required=True)
    for name in ('scope', 'inspect', 'apply'):
        command = commands.add_parser(name); command.add_argument('--plan', type=Path, required=True)
        if name == 'apply': command.add_argument('--approval', type=Path, required=True)
    args = parser.parse_args(argv); root = _physical(args.repo_root)
    coordinator = coordinator_for(root)
    with closing(sqlite3.connect(f'{_physical(args.db).as_uri()}?mode=ro', uri=True)) as connection:
        connection.execute('PRAGMA query_only=ON')
        if args.command == 'prepare': result = prepare(root, args.request, args.output, coordinator=coordinator, connection=connection)
        elif args.command == 'scope':
            result = approval_scope(root, args.plan, coordinator=coordinator, connection=connection)
            print(scope_marker(result, creation=True)); return 0
        elif args.command == 'apply': result = apply(root, args.plan, args.approval, coordinator=coordinator, connection=connection)
        else: result = load_plan(root, args.plan, coordinator=coordinator, connection=connection)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True)); return 0


if __name__ == '__main__':
    raise SystemExit(main())
