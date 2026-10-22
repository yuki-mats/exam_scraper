"""Explicit normal-source TF public variants; no T041/verified25 schema reuse.

Normal stages own their patches, versions and checkpoints. This adapter records a
separately approved public display variant and transports only its exact v3 delta.
Missing normal evidence is a hold, never an asserted approval or successful gate.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, closing
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys

from scripts.common.question_identity import SourceIdentityBinding, load_source_record_inventory
from scripts.common.question_publication_unit import publication_unit
from scripts.common.question_answer_contract import parse_official_answer_numbers
from scripts.common.scoped_canonical_context import reader_dependencies
from scripts.convert.convert_merged_to_firestore import convert_question_to_firestore, get_original_question_body_text
from scripts.upload import upload_questions_to_firestore as uploader
from tools.question_bank import feedback_prepared_stage as normal
from tools.question_bank import feedback_verified_stage_execution as native
from tools.question_bank.feedback_legacy_review_context import (
    PRIVATE, MANIFEST, _bytes, _hash, _inside, _json, _object, _physical, _read, _ref, _text, _unchanged,
)
from tools.question_bank.feedback_verified_save import _snapshots, _exclusive, LEDGER
from tools.question_review_console.independent_approval import verify_independent_approval, scope_marker
from tools.question_review_console.projection import sha256_json

MODULE = 'tools.question_bank.feedback_source_tf_delivery'
REQUEST = 'source-bound-public-tf-request/v1'
SCHEMA = 'source-bound-public-tf-unit/v1'
PLAN = 'source-bound-public-tf-plan/v1'
SAVED = 'source-bound-public-tf-save-receipt/v1'
CHECKS = 'source-bound-public-tf-checks/v1'
RESULT = 'source-bound-public-tf-publication-result/v1'
FILES = ('source.json', 'public.json', 'manifest.json')
READ_FIELDS = sorted(set(uploader.DOC_COMPARE_KEYS) | {'createdAt', 'createdById', 'updatedAt', 'updatedById'})
STRUCTURAL = {'questionBodyText', 'questionText', 'correctChoiceText'}
CONTENT = {'explanationText', 'suggestedQuestions', 'suggestedQuestionDetails'}


class GateRequired(ValueError):
    pass


def _seal(value, field='planHash'):
    return {**value, field: sha256_json(value)}


def _verify(value, schema, field='planHash'):
    if value.get('schemaVersion') != schema or value.get(field) != sha256_json({k: v for k, v in value.items() if k != field}):
        raise ValueError('schema/hash differs')
    return value


def _rows(raw):
    value = _json(raw) if isinstance(raw, bytes) else raw
    if isinstance(value, list): return value
    if isinstance(value, dict):
        for key in ('question_bodies', 'patched_questions', 'questions', 'entries'):
            if isinstance(value.get(key), list): return value[key]
    raise ValueError('record array required')


def _patch_rows(path, raw):
    if Path(path).suffix != '.jsonl': return _rows(raw)
    if not raw or not raw.endswith(b'\n'): raise ValueError('complete normal JSONL artifact required')
    rows = [_json(line) for line in raw.splitlines()]
    if not rows or any(not isinstance(row, dict) for row in rows): raise ValueError('normal JSONL records required')
    return rows


def _bound_row(rows, binding):
    matches = [r for r in rows if isinstance(r, dict) and SourceIdentityBinding.from_mapping(r) == binding]
    if len(matches) != 1: raise ValueError('exact stage row binding must resolve once')
    return matches[0]


def _load(root, path, files, *, private=False):
    return _json(_read(root, path, files, private=private))


def _reference(path, raw):
    return {'ref': str(path), 'sha256': _hash(raw)}


def _dependencies(root, files):
    initial = [root / 'tools/question_bank/feedback_source_tf_delivery.py',
        root / 'tools/question_review_console/scoped_artifacts.py', root / 'tools/question_review_console/evaluation.py']
    for path in reader_dependencies(root, initial): _read(root, path, files)
    for relative in ('config/question_maintenance_workflow.toml', 'config/qualification_rules.json',
                     'config/requirements/required_fields.toml', 'tools/question_review_console/evaluation_result.schema.json'):
        _read(root, root / relative, files)


def _source(root, request, files):
    spec = _object(request['source'], {'ref', 'sha256', 'binding'})
    _object(spec['binding'], {'sourceQuestionKey', 'reviewQuestionId', 'sourceRecordRef'})
    binding = SourceIdentityBinding.from_mapping(spec['binding'])
    if not binding.is_complete() or binding.as_mapping() != spec['binding']: raise ValueError('complete exact source identity required')
    group = root / 'output' / request['qualification'] / 'questions_json' / request['listGroupId']
    path = _inside(root, spec['ref'])
    if path.parent != group / '00_source': raise ValueError('source must belong to requested normal group')
    raw = _ref(root, {k: spec[k] for k in ('ref', 'sha256')}, files, decode=False)
    manifest = {}
    for line in _read(root, root / MANIFEST, files).splitlines():
        if not line: continue
        entry = _object(_json(line), {'path', 'sha256'})
        if entry['path'] in manifest: raise ValueError('duplicate preservation manifest entry')
        manifest[entry['path']] = entry['sha256']
    payloads = {}
    for source_path in sorted((group / '00_source').glob('*.json')):
        data = _read(root, source_path, files)
        if manifest.get(str(source_path.relative_to(root))) != _hash(data): raise ValueError('source preservation manifest differs')
        payloads[source_path] = _rows(data)
    expected_paths = {name for name in manifest if Path(name).parent == Path('output') / request['qualification'] / 'questions_json' / request['listGroupId'] / '00_source'
        and Path(name).suffix == '.json'}
    if expected_paths != {str(p.relative_to(root)) for p in payloads}:
        raise ValueError('source preservation manifest membership differs')
    inventory = load_source_record_inventory(group / '00_source', qualification=request['qualification'], list_group_id=request['listGroupId'])
    matches = [e for e in inventory if e.identity.binding == binding and e.path == path]
    if len(matches) != 1: raise ValueError('source must resolve exactly once')
    for entry in inventory:
        if payloads[entry.path][entry.record_index] != entry.record: raise ValueError('source read/decode drift')
    if _hash(raw) != spec['sha256']: raise ValueError('source changed')
    return binding, matches[0].record


def _normal_question(coordinator, request):
    group = coordinator.workflow.inventory.group(request['qualification'], request['listGroupId'])
    matches = [q for q in group.get('questions', []) if q.get('id') == request['questionId']
        and SourceIdentityBinding.from_mapping(q).as_mapping() == request['source']['binding']]
    if len(matches) != 1: raise ValueError('normal inventory identity must resolve once')
    question = deepcopy(matches[0]); question['qualification'] = request['qualification']
    return question


def _policy_fields(policy):
    return {f for target in policy.get('updateTargets', []) for f in target.get('fields', [])}


def _pin_native_dependencies(root, request):
    guards = {}
    for proof in request['stageProofs']:
        receipt = _ref(root, proof['receipt'], {}, private=True)
        approval = _ref(root, receipt['approval'], {}, private=True)
        guards.update(native.capture_native_guard([approval]))
    native.assert_native_guard(guards)
    return guards


def _native_graph(root, path, plan):
    guards = _pin_native_dependencies(root, plan['request'])
    receipt_path = _formal(root, plan) / 'receipt.json'
    if receipt_path.exists():
        receipt = _load(root, receipt_path, {})
        approval = _ref(root, receipt['approval'], {}, private=True)
        guards.update(native.capture_native_guard([approval]))
    native.assert_native_guard(guards, terminal=True)
    return guards


def _history_patches(root, request, coordinator, *, stages=None):
    """Stage-order after snapshots; all are authenticated by proof replay."""
    plans = [_ref(root, proof['plan'], {}, private=True) for proof in request['stageProofs']]
    rank = {p['id']: n for n, p in enumerate(coordinator.workflow.catalog(request['qualification'])['stages'])}
    if any(plan['stageId'] not in rank for plan in plans): raise ValueError('unknown historical normal stage')
    latest = {}
    for plan in sorted(plans, key=lambda p: rank[p['stageId']]):
        if stages is not None and plan['stageId'] not in stages: continue
        for patch in plan['commitView']['patches']: latest[patch['path']] = patch
    return latest


def _stage_predecessor_record(root, request, coordinator, stage, applicable, *, include_stage=False):
    """Use the normal pure projector with only applicable predecessor layers."""
    from tools.question_review_console.projection import STAGE_SPECS, project_record, PatchEntry
    from scripts.common.question_identity import IdentityCandidateIndex
    group = root / 'output' / request['qualification'] / 'questions_json' / request['listGroupId']
    sources = load_source_record_inventory(group / '00_source', qualification=request['qualification'], list_group_id=request['listGroupId'])
    binding = SourceIdentityBinding.from_mapping(request['source']['binding'])
    target = next(e for e in sources if e.identity.binding == binding)
    identities = tuple(e.identity for e in sources)
    inventory = coordinator.workflow.inventory
    before = []
    for policy in coordinator.workflow.catalog(request['qualification'])['stages']:
        if policy['id'] == stage:
            if include_stage: before.append(policy)
            break
        if policy['id'] in applicable: before.append(policy)
    snapshots = _history_patches(root, request, coordinator, stages={p['id'] for p in before})
    selected = {}
    for key, directory, _ in STAGE_SPECS:
        entries = [PatchEntry(path=root / relative,
            entry=_bound_row(_patch_rows(relative, patch['afterUtf8'].encode()), binding),
            source_stem=target.identity.source_stem) for relative, patch in snapshots.items() if directory in Path(relative).parts]
        if entries:
            # Reuse normal projection and identity binding without materializing
            # historical files over today's shared 23/21 artifacts.
            selected[key] = IdentityCandidateIndex({binding: tuple(entries)}, {})
    issue = inventory._projection_issue_index(request['qualification'], request['listGroupId'], group, identities)
    result = project_record(target.record, set(target.identity.aliases), selected, issue,
        source_binding=binding, validate_aggregate_question_type=stage != 'question_type')
    if result.errors: raise GateRequired('normal_predecessor_projection_errors:' + stage)
    fields = {'questionBodyText', 'choiceTextList'}
    for policy in before: fields.update(_policy_fields(policy))
    return result.record, fields


def _normal_law_verification(plan, child):
    """Replay the real writer's candidate-bound 03b reviews, no new verdicts."""
    from tools.question_review_console.law_audit_verification import required_review_count, parse_review
    metadata = plan['preparedCandidate']['content']['executionMetadata']
    receipt = metadata.get('lawAuditVerification', {}).get(plan['request']['questionId'])
    if not isinstance(receipt, dict): raise GateRequired('normal_03b_candidate_bound_reviews_missing')
    bundle = receipt['bundle']
    if receipt['evidenceHash'] != bundle['evidenceHash'] or bundle['evidenceHash'] != sha256_json(
            {k: v for k, v in bundle.items() if k != 'evidenceHash'}):
        raise ValueError('normal 03b evidence hash differs')
    if bundle['question']['questionId'] != plan['request']['questionId']:
        raise ValueError('foreign normal 03b question')
    reviews = receipt['reviews']
    count = required_review_count(bundle['proposedFields'])
    if len(reviews) != count or child.get('lawAuditReviewExecutions') != reviews:
        raise ValueError('actual normal 03b executions differ')
    if not child.get('threadId'): raise ValueError('normal 03b primary execution identity missing')
    seen = {str(child['threadId'])}
    for number, execution in enumerate(reviews):
        if (execution['phase'] != ('secondary' if number == 0 else 'tertiary')
                or not execution['threadId'] or execution['threadId'] in seen or not execution['turnId']
                or execution['executionId'] != execution['threadId'] + ':' + execution['turnId']):
            raise ValueError('normal 03b independent execution identity differs')
        seen.add(execution['threadId'])
        if parse_review(json.dumps(execution['review']), bundle)['decision'] != 'approve':
            raise GateRequired('normal_03b_review_not_approved')
    return {'evidenceHash': bundle['evidenceHash'], 'reviewCount': count, 'executionHash': sha256_json(reviews)}


def verify_normal_stage_proofs(root, request, coordinator, connection, files):
    """Replay historical saves by owned rows/fields, not stale whole-plan inputs.

    Shared work-version files and later-stage selections legitimately grow. Each
    receipt's historical immutable scope/candidate remains exact, while its stage
    version/checkpoint and current predecessor semantics are checked independently.
    """
    all_guards = _pin_native_dependencies(root, request)
    question = _normal_question(coordinator, request)
    projection = coordinator.workflow.inventory.projected_input(request['qualification'], request['listGroupId'],
        request['source']['binding']['sourceRecordRef'])
    if projection.errors: raise GateRequired('normal_projection_errors')
    current = deepcopy(dict(projection.record))
    policies = coordinator.workflow.versioned_policies(request['qualification'])
    status = coordinator.work_versions.status_for(question, policies.values())
    applicable = {s['id'] for s in status.get('stages', []) if s['id'] != 'evaluation'}
    supplied = {}; receipts = []
    binding = SourceIdentityBinding.from_mapping(request['source']['binding'])
    latest = _history_patches(root, request, coordinator)
    if 'law_audit' in applicable and not coordinator._projection_stage_applicable({}, 'law_audit', current):
        recorded = (coordinator.work_versions.record_for(question) or {}).get('stages', {}).get('law_audit', {})
        policy = policies['law_audit']
        if (recorded.get('policyFingerprint') != policy['policyFingerprint'] or recorded.get('version') != policy['policyVersion']
                or not recorded.get('runId')): raise GateRequired('genuine_normal_law_not_applicable_work_version_required')
        parent = coordinator.store.question_detail(request['qualification'], recorded['runId'], request['questionId'],
            include_attempt_artifacts=False)['execution']
        checkpoint = next((s for s in parent['stages'] if s['stageId'] == 'law_audit'), None)
        if not checkpoint or checkpoint.get('status') != 'not_applicable':
            raise GateRequired('genuine_normal_law_not_applicable_checkpoint_required')
        _read(root, coordinator.work_versions.question_path_for(question), files)
        _read(root, coordinator.store.root / request['qualification'] / recorded['runId'] / 'manifest.json', files)
        receipts.append({'stageId': 'law_audit', 'status': 'not_applicable', 'workVersion': recorded, 'checkpoint': checkpoint})
        applicable.remove('law_audit')
    for proof in request['stageProofs']:
        _object(proof, {'plan', 'receipt'})
        plan = _ref(root, proof['plan'], files, private=True)
        _verify(plan, normal.PLAN)
        stage = plan['stageId']
        if stage in supplied: raise ValueError('duplicate stage proof')
        supplied[stage] = plan
        if (plan['qualification'] != request['qualification'] or plan['request']['questionId'] != request['questionId']
                or plan['request']['source'] != request['source'] or plan['request']['authority'] != request['authority']):
            raise ValueError('foreign stage source/task')
        if not coordinator._phase_plan_policy_is_current(request['qualification'], plan['stagePlan'], stage):
            raise GateRequired('normal_stage_policy_changed:' + stage)
        receipt = _ref(root, proof['receipt'], files, private=True)
        if (receipt.get('schemaVersion') != normal.SAVED or receipt.get('planHash') != plan['planHash']
                or any(receipt.get(k) is not True for k in ('formalPatchSaved', 'workVersionRecorded', 'checkpointRecorded'))
                or receipt.get('FirestoreWritten') is not False): raise ValueError('genuine normal save receipt required')
        plan_path = _inside(root, proof['plan']['ref'], private=True)
        if Path(proof['receipt']['ref']) != plan_path.parent / 'save-receipt.json': raise ValueError('stage receipt directory differs')
        preview = _load(root, plan_path.parent / 'preview-receipt.json', files, private=True)
        if preview != normal._preview_receipt(plan): raise ValueError('normal preview/candidate differs')
        intent = _load(root, plan_path.parent / 'apply-intent.json', files, private=True)
        scope = normal._scope_data(root, plan_path, plan, connection)
        approval = _ref(root, receipt['approval'], files, private=True)
        guard = native.capture_native_guard([approval])
        approval_binding = verify_independent_approval(approval, scope, production=True)
        expected_intent = {'schemaVersion': 'feedback-prepared-stage-apply-intent/v1', 'planHash': plan['planHash'],
            'approval': receipt['approval'], 'nativeBinding': approval_binding, 'scope': scope}
        if intent != expected_intent or receipt.get('nativeBinding') != approval_binding: raise ValueError('native normal save binding differs')
        if normal._requirements(connection, request['authority']) != plan['taskRequirement']: raise ValueError('normal task authority drift')
        candidate = coordinator.store.load_prepared_candidate(plan['qualification'], plan['childRunId'],
            input_fingerprint_value=plan['inputFingerprint'], projected_input_hash=plan['projectedInputHash'])
        if candidate != plan['preparedCandidate']: raise ValueError('normal saved candidate changed')
        evidence = normal._stage_evidence(coordinator, plan)
        old = receipt['stageEvidence']
        if any(evidence[k] != old[k] for k in ('checkpoint', 'questionResults', 'workVersionReceipt')):
            raise ValueError('actual child/checkpoint evidence differs')
        if evidence['workVersion']['stages'][stage] != old['workVersion']['stages'][stage]:
            raise ValueError('same stage workVersion changed')
        original_projection_path = root / plan['stagePlan']['progressTargets'][0]['_projectedInputPath']
        projected_raw = _read(root, original_projection_path, files)
        if _hash(projected_raw) != plan['projectedInputHash']: raise ValueError('normal historical projected input changed')
        original_projection = _rows(projected_raw)[0]
        targets = normal.candidate_targets(request['questionId'], stage, plan['stagePlan'], current_record=original_projection)
        stage_output, _ = _stage_predecessor_record(root, request, coordinator, stage, applicable, include_stage=True)
        owned = set().union(*(set(t.allowed_fields) for t in targets))
        target_paths = {t.path for t in targets}
        if not target_paths: raise GateRequired('normal_stage_targets_missing:' + stage)
        for patch in plan['commitView']['patches']:
            if patch['path'] not in target_paths: raise ValueError('undeclared normal stage patch')
            raw = patch['afterUtf8'].encode()
            if patch['afterSha256'] != _hash(raw) or receipt['actualFiles'].get(patch['path']) != _hash(raw):
                raise ValueError('historical normal patch bytes differ')
            saved_row = _bound_row(_patch_rows(patch['path'], raw), binding)
            actual_row = _bound_row(_patch_rows(patch['path'], _read(root, root / patch['path'], files)), binding)
            last_row = _bound_row(_patch_rows(patch['path'], latest[patch['path']]['afterUtf8'].encode()), binding)
            if actual_row != last_row: raise GateRequired('normal_stage_owned_patch_changed:' + stage)
            target = next(t for t in targets if t.path == patch['path'])
            for field in target.allowed_fields:
                if target.role != 'law_audit' and field in saved_row and saved_row[field] != stage_output.get(field):
                    raise GateRequired('normal_stage_projection_changed:' + stage + ':' + field)
        predecessor, predecessor_fields = _stage_predecessor_record(root, request, coordinator, stage, applicable)
        predecessor_fields.update(plan['stagePlan'].get('readFieldsByStage', {}).get(stage, []))
        predecessor_fields -= owned
        for field in predecessor_fields:
            if (field in original_projection) != (field in predecessor) or original_projection.get(field) != predecessor.get(field):
                raise GateRequired('normal_stage_predecessor_changed:' + stage + ':' + field)
        native.assert_native_guard(guard, terminal=True)
        summary = {'stageId': stage, 'planHash': plan['planHash'], 'receipt': proof['receipt'],
            'nativeBinding': approval_binding, 'ownedFields': sorted(owned)}
        if stage == 'law_audit':
            summary['lawVerification'] = _normal_law_verification(plan, coordinator.store.get(plan['qualification'], plan['childRunId']))
        receipts.append(summary)
    if not applicable or set(supplied) != applicable or status.get('allCurrent') is not True:
        raise GateRequired('all_applicable_normal_stages_and_genuine_proofs_required:' + ','.join(sorted(applicable - set(supplied))))
    ordered = [p['id'] for p in coordinator.workflow.catalog(request['qualification'])['stages'] if p['id'] in applicable]
    final_projection, _ = _stage_predecessor_record(root, request, coordinator, ordered[-1], applicable, include_stage=True)
    if final_projection != current:
        raise GateRequired('current_normal_projection_differs_from_authenticated_stage_history')
    for path in getattr(projection, 'applied_files', ()):
        _read(root, _inside(root, root / path), files)
    native.assert_native_guard(all_guards, terminal=True)
    return question, current, receipts, status


def _request(root, request_path, files):
    value = _load(root, request_path, files, private=True)
    _object(value, {'schemaVersion', 'qualification', 'listGroupId', 'questionId', 'source', 'authority',
        'publicationUnit', 'primaryEvidence', 'officialAnswer', 'stageProofs', 'mapping', 'public'})
    if value['schemaVersion'] != REQUEST: raise ValueError('source TF request schema required')
    for key in ('qualification', 'listGroupId', 'questionId'):
        _text(value[key])
        if '/' in value[key] or value[key] in ('.', '..'): raise ValueError('single identity segment required')
    unit = publication_unit(value['publicationUnit'])
    if unit != value['publicationUnit']: raise ValueError('explicit exact publication unit required')
    if not isinstance(value['stageProofs'], list) or not value['stageProofs']: raise GateRequired('normal stage proofs missing')
    if not isinstance(value['primaryEvidence'], list) or len(value['primaryEvidence']) != 2: raise ValueError('question and answer official PDFs required')
    for evidence in value['primaryEvidence']:
        _object(evidence, {'kind', 'ref', 'sha256', 'page', 'locator'})
        if evidence['kind'] not in ('question_pdf', 'answer_pdf') or type(evidence['page']) is not int or evidence['page'] < 1:
            raise ValueError('explicit official PDF locator/page required')
        _text(evidence['locator'])
        raw = _ref(root, {k: evidence[k] for k in ('ref', 'sha256')}, files, decode=False)
        if not raw.startswith(b'%PDF'): raise ValueError('official PDF bytes required')
    if {e['kind'] for e in value['primaryEvidence']} != {'question_pdf', 'answer_pdf'}: raise ValueError('both PDF roles required')
    _object(value['officialAnswer'], {'sourceValue', 'selectedChoiceText'})
    _text(value['officialAnswer']['sourceValue']); _text(value['officialAnswer']['selectedChoiceText'])
    return value


def _snapshot(request):
    ids = sorted(m['questionId'] for m in request['mapping'])
    result = _snapshots(ids, READ_FIELDS, expected_unit=request['publicationUnit'])
    if set(result) != set(ids): raise ValueError('complete active live read-set required')
    for qid, snap in result.items():
        if (snap.get('questionId') != qid or snap.get('exists') is not True or not snap.get('updateTimeExact')
                or publication_unit(snap.get('fields')) != request['publicationUnit']
                or snap['fields'].get('isDeleted') is not False or snap['fields'].get('isChoiceOnly') is not False
                or snap['fields'].get('questionType') != 'true_false' or set(snap['fields']) - set(READ_FIELDS)):
            raise ValueError('active TF unit/snapshot mask differs')
    uploader.resolve_expected_update_times({'expectedUpdateTimes': {i: result[i]['updateTimeExact'] for i in ids}},
        [{'questionId': i} for i in ids], ('explanationText',))
    return result


def _variant(request, source, current, baseline):
    choices = source.get('choiceTextList')
    if (not isinstance(choices, list) or not choices or any(not isinstance(c, str) or not c for c in choices)
            or len(set(choices)) != len(choices) or current.get('choiceTextList') != choices):
        raise GateRequired('exact_unique_normal_source_statements_required; aggregate mapping needs its dedicated review')
    if current.get('questionType') != 'true_false': raise GateRequired('normal_true_false_stage_required')
    if source.get('answer_result_text') != request['officialAnswer']['sourceValue']:
        raise ValueError('official source selected answer changed')
    selected = parse_official_answer_numbers(request['officialAnswer']['sourceValue'])
    if (len(selected) != 1 or not 1 <= selected[0] <= len(choices)
            or choices[selected[0] - 1] != request['officialAnswer']['selectedChoiceText']):
        raise GateRequired('official_selected_choice_text_requires_unambiguous_source_answer')
    mapping = request['mapping']; public = request['public']
    if not isinstance(mapping, list) or len(mapping) != len(choices): raise ValueError('full explicit source/live mapping required')
    by_text, by_id = {}, {}
    for row in mapping:
        _object(row, {'questionId', 'sourceChoiceText', 'sourceChoiceSha256', 'liveChoiceText'})
        qid = _text(row['questionId']); text = _text(row['sourceChoiceText'])
        if qid in by_id or text in by_text or _hash(text.encode()) != row['sourceChoiceSha256']:
            raise ValueError('duplicate/mixed statement mapping')
        if qid not in baseline or baseline[qid]['fields'].get('originalQuestionChoiceText') != row['liveChoiceText'] or row['liveChoiceText'] != text:
            raise ValueError('full source/live statement text must agree uniquely')
        by_text[text] = row; by_id[qid] = row
    if set(by_text) != set(choices) or set(by_id) != set(baseline): raise ValueError('all statements/siblings must be mapped')
    if not isinstance(public, list) or len(public) != len(choices): raise ValueError('explicit full public TF candidate required')
    candidates = {}
    for row in public:
        _object(row, {'questionId', 'questionBodyText', 'questionText', 'correctChoiceText'}, CONTENT)
        qid = row['questionId']
        if qid in candidates or qid not in by_id: raise ValueError('duplicate/foreign public ID')
        _text(row['questionBodyText']); _text(row['questionText'])
        if row['correctChoiceText'] not in ('正しい', '間違い'): raise ValueError('individual TF truth required')
        candidates[qid] = row
    bodies = {r['questionBodyText'] for r in public}
    if len(bodies) != 1: raise ValueError('one common public display body required')
    record = {**deepcopy(current), 'questionBodyText': next(iter(bodies)),
        'originalQuestionBodyText': get_original_question_body_text(source),
        'firestoreQuestionIds': [by_text[text]['questionId'] for text in choices],
        'sourceConflictReviewDecision': '', 'sourceContentConflictPolicy': ''}
    # Every ID above comes from an explicit full-text bijection, never a suffix.
    record.pop('original_question_body_text', None); record.pop('firestoreSourceQuestions', None)
    generated = convert_question_to_firestore(record)
    converted = {row['questionId']: row for row in generated}
    if set(converted) != set(candidates): raise ValueError('normal converter ID coverage differs')
    result = []; masks = {}
    for qid in sorted(candidates):
        before = baseline[qid]['fields']; row = candidates[qid]; normal_doc = uploader.build_doc_data_base(converted[qid])
        expected = {**deepcopy(before), **{f: row[f] for f in row if f != 'questionId'}}
        for field in CONTENT:
            if ((field in before) != (field in normal_doc) or before.get(field) != normal_doc.get(field)) and field not in row:
                raise GateRequired('normal_explanation_or_suggestion_delta_required:' + field)
        for field in row:
            if field != 'questionId' and normal_doc.get(field) != row[field]:
                raise ValueError('public candidate differs from normal saved projection/converter:' + field)
        original = get_original_question_body_text(source)
        if before.get('originalQuestionBodyText') != original or before.get('originalQuestionChoiceText') != by_id[qid]['sourceChoiceText']:
            raise ValueError('official original body/statement must remain unchanged')
        changed = {f for f in set(before) | set(expected) if (f in before) != (f in expected) or before.get(f) != expected.get(f)}
        if changed:
            optional = changed - STRUCTURAL
            matches = [list(mask) for mask in uploader.READ_SET_TF_TRUTH_WRITE_FIELD_SETS if set(mask) == STRUCTURAL | optional]
            if len(matches) != 1 or not changed <= STRUCTURAL | CONTENT: raise ValueError('exact v3 owned mask required')
            mask = matches[0]
            if any(f not in before or f not in expected for f in mask): raise ValueError('v3 mask presence must be preserved')
            masks[qid] = mask
        result.append({'questionId': qid, **expected})
    if not masks: raise ValueError('all-no-op public unit refused')
    uploader.validate_tf_truth_questions(result)
    return record, result, masks


def _task_lifecycle(connection, request):
    if request['authority']['kind'] != 'improvement':
        # Report lifecycle is fixed by its normal individual-case requirement;
        # publication remains blocked until its blind/challenge reader connects.
        return {'kind': 'report', 'publicationGate': 'genuine_report_blind_a_b_challenge_reader_not_connected'}
    cursor = connection.cursor(); cursor.row_factory = sqlite3.Row
    try:
        row = cursor.execute('SELECT status,updated_at FROM improvement_tasks WHERE task_id=?',
            (request['authority']['taskId'],)).fetchone()
        if row is None or row['status'] not in {'open', 'in_progress', 'review_pending'}:
            raise GateRequired('unfinished_unblocked_task_lifecycle_required')
        latest = cursor.execute('SELECT * FROM improvement_result_history WHERE task_id=? ORDER BY id DESC LIMIT 1',
            (request['authority']['taskId'],)).fetchone()
        return {'kind': 'improvement', 'expectedStatus': row['status'], 'updatedAt': row['updated_at'],
            'latestResult': dict(latest) if latest is not None else None}
    finally: cursor.close()


def _derive(root, path, coordinator, connection, files, *, baseline_override=None):
    request = _request(root, path, files)
    guards = _pin_native_dependencies(root, request)
    binding, source = _source(root, request, files)
    requirement = normal._requirements(connection, request['authority'])
    lifecycle = _task_lifecycle(connection, request)
    question, current, proofs, versions = verify_normal_stage_proofs(root, request, coordinator, connection, files)
    normal._authority_matches({'live': {'questionIds': [m['questionId'] for m in request['mapping']],
        'originalQuestionId': request['publicationUnit']['originalQuestionId']}, 'questionId': request['questionId'],
        'source': request['source']}, requirement)
    baseline = _snapshot(request) if baseline_override is None else deepcopy(baseline_override)
    record, documents, masks = _variant(request, source, current, baseline)
    _dependencies(root, files)
    for policy in coordinator.workflow.versioned_policies(request['qualification']).values():
        for relative in [*policy.get('canonicalDocs', []), *policy.get('inputs', [])]:
            p = _inside(root, root / relative)
            if p.is_dir():
                for child in sorted(p.rglob('*')):
                    if child.is_file(): _read(root, child, files)
            else: _read(root, p, files)
    source_payload = {'schemaVersion': SCHEMA, 'sourceBinding': binding.as_mapping(), 'source': source,
        'normalProjection': current, 'stageProofs': proofs, 'taskRequirement': requirement, 'taskLifecycle': lifecycle,
        'primaryEvidence': request['primaryEvidence'], 'officialAnswer': request['officialAnswer'], 'mapping': request['mapping']}
    source_payload['requiredReviewGates'] = []
    if request['authority']['kind'] == 'report':
        source_payload['requiredReviewGates'].append('genuine_report_blind_a_b_challenge_reader_not_connected')
    if current.get('isLawRelated') is True and not any(p.get('lawVerification') for p in proofs):
        source_payload['requiredReviewGates'].append('normal_03b_candidate_bound_reviews_missing')
    public_payload = {'schemaVersion': SCHEMA, 'publicationUnit': request['publicationUnit'], 'record': record,
        'questions': documents, 'baseline': baseline, 'writeFieldsByDocument': masks}
    if _task_lifecycle(connection, request) != lifecycle: raise ValueError('task lifecycle changed during replay')
    native.assert_native_guard(guards, terminal=True)
    return request, question, versions, source_payload, public_payload


@native.operation
def prepare(root, request_path, output, *, coordinator, connection):
    root = _physical(root); output = _inside(root, output, private=True)
    if output.exists(): raise ValueError('exclusive new private directory required')
    files = {}; request, question, versions, source, public = _derive(root, request_path, coordinator, connection, files)
    unit_hash = sha256_json({'source': source, 'public': public, 'inputs': files})
    formal = root / 'output' / request['qualification'] / 'publication_corrections' / unit_hash
    manifest = {'schemaVersion': SCHEMA, 'unitHash': unit_hash, 'sourceHash': sha256_json(source), 'publicHash': sha256_json(public),
        'sourceBinding': request['source']['binding'], 'publicationUnit': request['publicationUnit'],
        'publicationIds': sorted(public['baseline']), 'inputFiles': files, 'formalPath': str(formal.relative_to(root))}
    plan = _seal({'schemaVersion': PLAN, 'requestPath': str(_inside(root, request_path, private=True)), 'request': request,
        'unitHash': unit_hash, 'formalPath': manifest['formalPath'], 'ledgerPath': normal._ledger_path(connection), 'normalQuestionIdentity': {k: question.get(k) for k in
            ('id', 'reviewKey', 'qualification', 'listGroupId', 'sourceQuestionKey', 'sourceRecordRef', 'originalQuestionId')},
        'workVersions': versions, 'source': source, 'public': public, 'manifest': manifest, 'inputFiles': files,
        'formalUnitSaved': False, 'FirestoreWritten': False, 'publicationReady': False})
    _unchanged(root, files)
    native.assert_native_guard(_pin_native_dependencies(root, request), terminal=True)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700); output.mkdir(mode=0o700)
    _exclusive(output / 'plan.json', _bytes(plan))
    _exclusive(output / 'preview-receipt.json', _bytes({'schemaVersion': PLAN, 'planHash': plan['planHash'],
        'formalUnitSaved': False, 'FirestoreWritten': False, 'publicationReady': False}))
    return inspect(root, output / 'plan.json', coordinator=coordinator, connection=connection)


def _plan(root, path, coordinator, connection, *, live=True):
    root = _physical(root); path = _inside(root, path, private=True)
    files = {}; plan = _verify(_load(root, path, files, private=True), PLAN)
    guards = _native_graph(root, path, plan)
    _unchanged(root, plan['inputFiles'])
    if normal._ledger_path(connection) != plan['ledgerPath']: raise ValueError('authority database path differs')
    current_files = {}; request, question, versions, source, public = _derive(root, plan['requestPath'], coordinator, connection, current_files,
        baseline_override=None if live else plan['public']['baseline'])
    if request != plan['request'] or source != plan['source'] or public != plan['public'] or current_files != plan['inputFiles']:
        raise ValueError('fixed source/stage/public/live scope drift')
    preview = _load(root, path.parent / 'preview-receipt.json', files, private=True)
    if preview != {'schemaVersion': PLAN, 'planHash': plan['planHash'], 'formalUnitSaved': False,
                   'FirestoreWritten': False, 'publicationReady': False}: raise ValueError('partial prepared output')
    if plan.get('formalUnitSaved') is not False or plan.get('FirestoreWritten') is not False or plan.get('publicationReady') is not False:
        raise ValueError('preview cannot claim save or publication')
    unit_hash = sha256_json({'source': source, 'public': public, 'inputs': current_files})
    formal_path = str(Path('output') / request['qualification'] / 'publication_corrections' / unit_hash)
    expected_manifest = {'schemaVersion': SCHEMA, 'unitHash': unit_hash, 'sourceHash': sha256_json(source),
        'publicHash': sha256_json(public), 'sourceBinding': request['source']['binding'],
        'publicationUnit': request['publicationUnit'], 'publicationIds': sorted(public['baseline']),
        'inputFiles': current_files, 'formalPath': formal_path}
    if plan['unitHash'] != unit_hash or plan['formalPath'] != formal_path or plan['manifest'] != expected_manifest:
        raise ValueError('formal source TF unit identity/path/manifest drift')
    native.assert_native_guard(guards, terminal=True)
    return root, path, plan


def _payload(plan):
    public = plan['public']; unit = public['publicationUnit']; ids = sorted(public['baseline'])
    masks = public['writeFieldsByDocument']; documents = [d for d in public['questions'] if d['questionId'] in masks]
    payload = {'schemaVersion': uploader.READ_SET_TF_TRUTH_SCHEMA, **unit, 'readDocumentIds': ids,
        'expectedReadUpdateTimes': {i: public['baseline'][i]['updateTimeExact'] for i in ids},
        'expectedLiveFingerprint': uploader.firestore_live_fingerprint(ids, {i: public['baseline'][i]['fields'] for i in ids}),
        'writeFieldsByDocument': masks, 'questions': documents}
    uploader.resolve_read_set_maintenance_contract(payload, 'normal source TF public variant')
    return payload


def _formal(root, plan):
    return _inside(root, root / plan['formalPath'])


def _verify_saved(root, path, plan):
    directory = _formal(root, plan); refs = {}
    expected = {'source.json': plan['source'], 'public.json': plan['public'], 'manifest.json': plan['manifest']}
    for name, payload in expected.items():
        raw = _read(root, directory / name, {})
        if raw != _bytes(payload): raise ValueError('formal unit third value or partial output')
        refs[name] = _reference(directory / name, raw)
    receipt = _load(root, directory / 'receipt.json', {})
    _object(receipt, {'schemaVersion', 'planHash', 'unitHash', 'plan', 'files', 'approval', 'nativeBinding',
        'formalUnitSaved', 'normalStagesVerified', 'evaluationPerformed', 'FirestoreWritten', 'publicationReady'})
    if (receipt['schemaVersion'] != SAVED or receipt['planHash'] != plan['planHash'] or receipt['unitHash'] != plan['unitHash']
            or receipt['files'] != refs or receipt['formalUnitSaved'] is not True or receipt['normalStagesVerified'] is not True
            or any(receipt[k] is not False for k in ('evaluationPerformed', 'FirestoreWritten', 'publicationReady'))
            or receipt['plan'] != _reference(path, _read(root, path, {}, private=True))):
        raise ValueError('dedicated formal unit receipt differs')
    approval = _ref(root, receipt['approval'], {}, private=True)
    guard = native.capture_native_guard([approval])
    if verify_independent_approval(approval, _scope(root, path, plan, 'save'), production=True) != receipt['nativeBinding']:
        raise ValueError('formal approval changed')
    native.assert_native_guard(guard, terminal=True)
    return receipt


@native.operation
def inspect(root, path, *, coordinator, connection):
    root, path, plan = _plan(root, path, coordinator, connection)
    directory = _formal(root, plan)
    saved = _verify_saved(root, path, plan) if directory.exists() else None
    _native_graph(root, path, plan)
    return {'schemaVersion': PLAN, 'planHash': plan['planHash'], 'unitHash': plan['unitHash'],
        'formalUnitSaved': saved is not None, 'FirestoreWritten': False, 'publicationReady': False,
        'remainingGates': ['normal_checks', 'independent_normal_evaluation', 'native_publication_approval',
                           'limited_v3_commit', 'complete_readset_readback', 'task_completion_dispatch']}


def _scope(root, path, plan, action, *, gates=None):
    argv = ['--repo-root', str(root), '--db', plan['ledgerPath'], 'scope', '--plan', str(path), '--action', action]
    return {'schemaVersion': 'source-bound-public-tf-independent-scope/v1', 'action': action,
        'planHash': plan['planHash'], 'unitHash': plan['unitHash'], 'sourceBinding': plan['request']['source']['binding'],
        'taskRequirement': plan['source']['taskRequirement'], 'taskLifecycle': plan['source']['taskLifecycle'], 'publicHash': sha256_json(plan['public']),
        'stageProofsHash': sha256_json(plan['source']['stageProofs']), 'inputFilesHash': sha256_json(plan['inputFiles']),
        'transportSchema': uploader.READ_SET_TF_TRUTH_SCHEMA,
        'requiredReviewGates': plan['source'].get('requiredReviewGates', []), 'gates': gates,
        'nativeCreationCall': native.native_operation_call(argv, module_name=MODULE)}


@native.operation
def scope(root, path, *, coordinator, connection, action='save', evaluation=None):
    if action not in ('save', 'publish'): raise ValueError('explicit save/publish action required')
    root, path, plan = _plan(root, path, coordinator, connection)
    gates = _publication_gates(root, path, plan, coordinator, connection, evaluation) if action == 'publish' else None
    _native_graph(root, path, plan)
    return _scope(root, path, plan, action, gates=gates)


@contextmanager
def _lock(root, plan):
    directory = _formal(root, plan).parent
    directory.mkdir(parents=True, exist_ok=True)
    path = _inside(root, directory / ('.' + plan['unitHash'] + '.lock'))
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if not os.path.samestat(os.fstat(fd), path.stat()): raise ValueError('unit lock replaced')
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)


@native.operation
def save(root, path, approval_path, *, coordinator, connection, failure_hook=None):
    root = _physical(root); path = _inside(root, path, private=True)
    raw = _read(root, approval_path, {}, private=True); approval = _json(raw)
    guard = native.capture_native_guard([approval])
    root, path, plan = _plan(root, path, coordinator, connection)
    scope_value = _scope(root, path, plan, 'save')
    binding = verify_independent_approval(approval, scope_value, production=True)
    with _lock(root, plan):
        _plan(root, path, coordinator, connection); native.assert_native_guard(guard)
        directory = _formal(root, plan)
        if directory.exists():
            receipt = _verify_saved(root, path, plan)
            if receipt['approval'] != _reference(_inside(root, approval_path, private=True), raw): raise ValueError('save retry approval differs')
            _native_graph(root, path, plan)
            native.assert_native_guard(guard, terminal=True); return receipt
        intent_path = path.parent / 'save-intent.json'
        if intent_path.exists(): raise GateRequired('interrupted_unit_save_requires_inspection; do_not_reapply')
        intent = {'schemaVersion': SAVED, 'planHash': plan['planHash'], 'approval': _reference(_inside(root, approval_path, private=True), raw),
            'nativeBinding': binding, 'scope': scope_value}
        _exclusive(intent_path, _bytes(intent))
        directory.mkdir(mode=0o700)
        refs = {}
        for name, payload in zip(FILES, (plan['source'], plan['public'], plan['manifest']), strict=True):
            _plan(root, path, coordinator, connection); native.assert_native_guard(guard)
            data = _bytes(payload); _exclusive(directory / name, data); refs[name] = _reference(directory / name, data)
            if failure_hook: failure_hook(name)
        receipt = {'schemaVersion': SAVED, 'planHash': plan['planHash'], 'unitHash': plan['unitHash'],
            'plan': _reference(path, _read(root, path, {}, private=True)), 'files': refs, 'approval': intent['approval'],
            'nativeBinding': binding, 'formalUnitSaved': True, 'normalStagesVerified': True, 'evaluationPerformed': False,
            'FirestoreWritten': False, 'publicationReady': False}
        _plan(root, path, coordinator, connection)
        if verify_independent_approval(approval, scope_value, production=True) != binding: raise ValueError('save approval drift')
        native.assert_native_guard(guard, terminal=True)
        _native_graph(root, path, plan)
        _exclusive(directory / 'receipt.json', _bytes(receipt))
        result = _verify_saved(root, path, plan)
        _native_graph(root, path, plan)
        return result


@native.operation
def load_saved_unit(root, path, *, coordinator=None, connection=None):
    """Explicit selector; normal 24/group discovery never imports this unit."""
    if coordinator is None or connection is None:
        raise GateRequired('normal_coordinator_and_readonly_authority_connection_required')
    root, path, plan = _plan(root, path, coordinator, connection, live=False)
    receipt = _verify_saved(root, path, plan)
    return plan, receipt


def _question(root, path, plan, coordinator, connection, *, checked=False):
    question = _normal_question(coordinator, plan['request'])
    question.update(projected=deepcopy(plan['public']['record']), merged=deepcopy(plan['public']['record']),
        convertedDocs=deepcopy(plan['public']['questions']), uploadReadyDocs=deepcopy(plan['public']['questions']),
        stateHash=plan['unitHash'], choiceCount=len(plan['public']['questions']),
        sourceTfUnit={'plan': str(path), 'planHash': plan['planHash']},
        sourceTfOriginalBody=get_original_question_body_text(plan['source']['source']),
        sourceTfOfficialAnswer=deepcopy(plan['source']['officialAnswer']))
    # Preserve the normal work-version identity, not a new fictitious checkpoint.
    question['workVersions'] = coordinator.work_versions.status_for(question,
        coordinator.workflow.versioned_policies(plan['request']['qualification']).values())
    if checked:
        question['workflow'] = {**question.get('workflow', {}), 'merge': 'match', 'convert': 'match', 'upload': 'match'}
    return question


@native.operation
def validate_source_tf_question(root, question):
    """Evaluation dispatch revalidates real normal receipts and the formal unit."""
    spec = _object(question.get('sourceTfUnit'), {'plan', 'planHash'})
    with closing(sqlite3.connect('file:' + str(question['sourceTfLedger']) + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        coordinator = normal.coordinator_for(root)
        plan, _ = load_saved_unit(root, spec['plan'], coordinator=coordinator, connection=connection)
        if plan['planHash'] != spec['planHash']: raise ValueError('evaluation unit token drift')
        _verify_checks(root, Path(spec['plan']), plan, rerun=False)
        expected = _question(root, Path(spec['plan']), plan, coordinator, connection, checked=True)
        for field in ('id', 'reviewKey', 'projected', 'uploadReadyDocs', 'stateHash', 'sourceTfOriginalBody', 'sourceTfOfficialAnswer'):
            if question.get(field) != expected.get(field): raise ValueError('normal evaluation unit scope differs:' + field)
        _native_graph(root, Path(spec['plan']), plan)
    # This is not the legacy scoped-artifact tuple. The dispatch validates then
    # returns None so ordinary normal-stage evaluation/version logic continues.
    return None


def _run_command(root, argv):
    completed = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=180, check=False)
    if completed.returncode != 0:
        raise GateRequired('normal_command_failed:' + ' '.join(argv) + '\n' + completed.stdout[-2000:] + completed.stderr[-2000:])
    return {'argv': argv, 'exitCode': completed.returncode, 'stdout': completed.stdout, 'stderr': completed.stderr}


def _check_commands(root, path, plan):
    directory = path.parent / 'checks'; group = directory / 'generated' / 'questions_json' / plan['request']['listGroupId']
    base = group.parent
    commands = [
        [sys.executable, str(root / 'scripts/check/check_choice_text_alignment.py'), '--base-dir', str(base), '--stage', 'merged'],
        [sys.executable, str(root / 'scripts/check/check_required_fields.py'), '--base-dir', str(base), '--stage', 'merged'],
    ]
    correct = [p for proof in plan['request']['stageProofs']
        for p in [_json(Path(proof['plan']['ref']).read_bytes())] if p['stageId'] == 'correct_choice']
    types = [p for proof in plan['request']['stageProofs']
        for p in [_json(Path(proof['plan']['ref']).read_bytes())] if p['stageId'] == 'question_type']
    patches = [p['path'] for p in correct[0]['commitView']['patches'] if '23_correctChoiceText_fixed' in Path(p['path']).parts]
    type_patches = [p['path'] for p in types[0]['commitView']['patches'] if '10_questionType_fixed' in Path(p['path']).parts]
    if len(patches) != 1 or len(type_patches) != 1: raise GateRequired('normal_23_and_10_owned_artifacts_required')
    commands += [[sys.executable, str(root / 'scripts/check/check_correct_choice_patch_coverage.py'),
        '--source', plan['request']['source']['ref'], '--patch', str(root / patches[0]),
        '--question-type-patch', str(root / type_patches[0])],
        [sys.executable, str(root / 'tools/question_bank/question_bank.py'), 'quality-gate',
         '--qualification', plan['request']['qualification'], '--list-group-id', plan['request']['listGroupId'],
         '--base-dir', str(base), '--mode', 'firestore'],
        [sys.executable, str(root / 'scripts/upload/upload_questions_to_firestore.py'), str(directory / 'transport.json'), '--dry-run']]
    return commands


def _check_coverage(root, path, plan):
    from scripts.check.check_choice_text_alignment import iter_target_files
    from scripts.check.check_required_fields import iter_merged_files, iter_firestore_files
    directory = path.parent / 'checks'; base = directory / 'generated/questions_json'
    group = plan['request']['listGroupId']
    merged = directory / 'generated/questions_json' / group / '30_merged_2/question_source_tf_merged_unit.json'
    converted = directory / 'generated/questions_json' / group / '40_convert/unit_firestore_public_tf.json'
    if (iter_target_files(base, 'merged', group) != [merged] or iter_merged_files(base) != [merged]
            or iter_firestore_files(base, group) != [converted]
            or len(_rows(merged.read_bytes())) != 1
            or len(_rows(converted.read_bytes())) != len(plan['public']['baseline'])):
        raise ValueError('actual normal checker file/record coverage must be nonzero and exact')
    # Parse by option name, never rely on arbitrary source record position.
    command = _check_commands(root, path, plan)[2]
    patch_path = Path(command[command.index('--patch') + 1])
    row = _bound_row(_rows(patch_path.read_bytes()), SourceIdentityBinding.from_mapping(plan['request']['source']['binding']))
    if row.get('correctChoiceText') != plan['source']['normalProjection'].get('correctChoiceText'):
        raise GateRequired('normal_23_checker_target_verdicts_differ')


def _generated(plan):
    group = Path('generated/questions_json') / plan['request']['listGroupId']
    return {str(group / '30_merged_2/question_source_tf_merged_unit.json'): {'list_group_id': plan['request']['listGroupId'],
                'question_bodies': [plan['public']['record']]},
        str(group / '40_convert/unit_firestore_public_tf.json'): {'questions': plan['public']['questions']},
        'transport.json': _payload(plan)}


@native.operation
def check(root, path, *, coordinator, connection):
    root, path, plan = _plan(root, path, coordinator, connection)
    _verify_saved(root, path, plan)
    directory = path.parent / 'checks'
    if directory.exists(): return _verify_checks(root, path, plan, rerun=True)
    directory.mkdir(mode=0o700)
    refs = {}
    for name, value in _generated(plan).items():
        output = directory / name; output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = _bytes(value); _exclusive(output, data); refs[name] = _reference(output, data)
    commands = _check_commands(root, path, plan)
    _check_coverage(root, path, plan)
    results = [_run_command(root, argv) for argv in commands]
    # Actual dry-run validation uses the same public uploader v3 contract.
    dry = uploader.upload_read_set_maintenance_questions(_payload(plan), 'source TF explicit unit', dry_run=True)
    if dry.get('schemaVersion') != uploader.READ_SET_TF_TRUTH_RESULT_SCHEMA or dry.get('status') != 'dry_run':
        raise ValueError('actual v3 dry-run result missing')
    _plan(root, path, coordinator, connection)
    receipt = _seal({'schemaVersion': CHECKS, 'planHash': plan['planHash'], 'files': refs,
        'commands': results, 'dryRun': dry, 'FirestoreWritten': False}, 'checksHash')
    _native_graph(root, path, plan)
    _exclusive(directory / 'receipt.json', _bytes(receipt))
    return receipt


def _verify_checks(root, path, plan, *, rerun):
    guards = _native_graph(root, path, plan)
    directory = path.parent / 'checks'; receipt = _verify(_load(root, directory / 'receipt.json', {}), CHECKS, 'checksHash')
    if receipt['planHash'] != plan['planHash'] or receipt.get('FirestoreWritten') is not False:
        raise ValueError('checks scope differs')
    generated = _generated(plan)
    if set(receipt['files']) != set(generated): raise ValueError('checks generated coverage differs')
    for name, value in generated.items():
        raw = _ref(root, receipt['files'][name], {}, decode=False)
        if receipt['files'][name]['ref'] != str(directory / name) or raw != _bytes(value): raise ValueError('actual check inputs changed')
    commands = _check_commands(root, path, plan)
    _check_coverage(root, path, plan)
    if [r.get('argv') for r in receipt['commands']] != commands or any(r.get('exitCode') != 0 for r in receipt['commands']):
        raise ValueError('complete normal CLI check results required')
    # Re-execute at every use: a private JSON exitCode is not command-origin evidence.
    for argv in commands: _run_command(root, argv)
    for name, value in generated.items():
        if _ref(root, receipt['files'][name], {}, decode=False) != _bytes(value):
            raise ValueError('actual check inputs changed during execution')
    native.assert_native_guard(guards, terminal=True)
    return receipt


def _evaluation_service(root, coordinator):
    from tools.question_review_console.evaluation import QuestionEvaluationService
    return QuestionEvaluationService(root, 'source-bound-tf-evaluation', app_server=coordinator.app_server,
        run_store=coordinator.store, work_versions=coordinator.work_versions,
        work_policy_provider=coordinator.workflow.versioned_policies)


@native.operation
def evaluate(root, path, *, coordinator, connection, evaluation=None):
    root, path, plan = _plan(root, path, coordinator, connection)
    _verify_saved(root, path, plan); _verify_checks(root, path, plan, rerun=True)
    evaluation = evaluation or _evaluation_service(root, coordinator)
    question = _question(root, path, plan, coordinator, connection, checked=True)
    question['sourceTfLedger'] = normal._ledger_path(connection)
    preview = evaluation.preview(question, model_profile='codex_only')
    if preview.get('canEvaluate') is not True: raise GateRequired('normal_independent_evaluation_not_ready')
    result = evaluation.run(question, preview['previewToken'], lambda _: None, model_profile='codex_only')
    _plan(root, path, coordinator, connection)
    status = evaluation.status_for(question)
    if status.get('status') != 'passed' or status.get('publishReady') is not True: raise GateRequired('normal_independent_evaluation_failed_or_incomplete')
    _native_graph(root, path, plan)
    return result


def _publication_gates(root, path, plan, coordinator, connection, evaluation):
    guards = _native_graph(root, path, plan)
    if plan['source'].get('requiredReviewGates'):
        raise GateRequired(';'.join(plan['source']['requiredReviewGates']))
    saved_receipt = _verify_saved(root, path, plan)
    checks = _verify_checks(root, path, plan, rerun=True)
    evaluation = evaluation or _evaluation_service(root, coordinator)
    question = _question(root, path, plan, coordinator, connection, checked=True)
    question['sourceTfLedger'] = normal._ledger_path(connection)
    status = evaluation.status_for(question)
    if (status.get('status') != 'passed' or status.get('publishReady') is not True
            or status.get('machineReady') is not True or not status.get('resultHash')):
        raise GateRequired('current_genuine_normal_evaluation_required')
    projection = evaluation.store.load_projection(question)
    if not isinstance(projection, dict): raise GateRequired('normal_evaluation_persisted_receipt_missing')
    committed = evaluation._committed_attempt_result(question, projection)
    current_policy = evaluation.current_policy(force=True)
    if (not committed or committed.get('status') != 'passed' or committed.get('resultHash') != status['resultHash']
            or committed.get('policyFingerprint') != current_policy['policyFingerprint']
            or committed.get('policyVersion') != current_policy['policyVersion']
            or not evaluation._session_receipt_valid(question, committed)):
        raise GateRequired('exact_current_policy_and_genuine_independent_evaluation_receipt_required')
    stage_threads = set()
    for proof in plan['request']['stageProofs']:
        stage_plan = _ref(root, proof['plan'], {}, private=True)
        child = coordinator.store.get(stage_plan['qualification'], stage_plan['childRunId'])
        stage_threads.update(str(child.get(k) or '') for k in ('sessionId', 'threadId'))
    if committed['threadId'] in stage_threads or committed['sessionId'] in stage_threads:
        raise GateRequired('evaluation_must_use_a_new_independent_normal_session')
    eval_path = evaluation.store.evaluation_path(question)
    result_path = evaluation.run_store.root / question['qualification'] / committed['runId'] / 'result.json'
    native.assert_native_guard(guards, terminal=True)
    return {'checksHash': checks['checksHash'], 'evaluationResultHash': committed['resultHash'],
        'formalReceipt': _reference(_formal(root, plan) / 'receipt.json', _bytes(saved_receipt)),
        'formalFiles': saved_receipt['files'],
        'evaluationProjection': _reference(eval_path, _read(root, eval_path, {})),
        'evaluationResult': _reference(result_path, _read(root, result_path, {})),
        'policyFingerprint': committed['policyFingerprint'], 'payloadHash': sha256_json(_payload(plan))}


def _git(root, plan):
    # Source snapshots remain in the existing manifest/backup contract. Their
    # actual bytes are still CAS inputs, without requiring newly force-added
    # copies of every ignored original in Git.
    _unchanged(root, plan['inputFiles'])
    def run(args):
        result = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    if run(['status', '--porcelain']): raise GateRequired('clean_committed_publication_inputs_required')
    head = run(['rev-parse', 'HEAD'])
    if run(['rev-parse', 'origin/main']) != head: raise GateRequired('pushed_main_publication_inputs_required')
    group_root = Path('output') / plan['request']['qualification'] / 'questions_json'
    source_root = group_root / plan['request']['listGroupId'] / '00_source'
    committed = {relative for relative in plan['inputFiles'] if Path(relative).parts[0] in
        {'scripts', 'tools', 'config', 'prompt', 'document', 'docs'}
        or Path(relative).is_relative_to(group_root) and not Path(relative).is_relative_to(source_root)}
    committed.update(str((_formal(root, plan) / name).relative_to(root)) for name in (*FILES, 'receipt.json'))
    for relative in sorted(committed):
        blob = subprocess.run(['git', 'show', head + ':' + relative], cwd=root, capture_output=True, check=False)
        if blob.returncode != 0 or blob.stdout != _read(root, root / relative, {}):
            raise GateRequired('audited_input_not_same_committed_blob:' + relative)
    return head


def _terminal_publication_guard(root, path, plan, connection, gates, files, guard):
    """Close the last Git/native/check I/O window before write or result save."""
    dependency_guards = _native_graph(root, path, plan)
    native.assert_native_guard(guard, terminal=True)
    _unchanged(root, plan['inputFiles'])
    for key in ('evaluationProjection', 'evaluationResult', 'formalReceipt'):
        if key in gates: _ref(root, gates[key], {}, decode=False)
    for reference in gates.get('formalFiles', {}).values(): _ref(root, reference, {}, decode=False)
    _unchanged(root, files)
    native.assert_native_guard(dependency_guards, terminal=True)
    native.assert_native_guard(guard, terminal=True)
    # Keep the authority-state read last: the preceding I/O may race a hold or
    # cancellation. This is a transport boundary, not a lifecycle transition.
    if normal._requirements(connection, plan['request']['authority']) != plan['source']['taskRequirement']:
        raise ValueError('task authority requirements changed at publication boundary')
    if _task_lifecycle(connection, plan['request']) != plan['source']['taskLifecycle']:
        raise ValueError('task lifecycle changed at publication boundary')


def _readback(plan, payload, commit, current):
    ids = payload['readDocumentIds']; write_ids = [q['questionId'] for q in payload['questions']]
    readonly = [i for i in ids if i not in write_ids]
    expected = {'schemaVersion': uploader.READ_SET_TF_TRUTH_RESULT_SCHEMA, 'status': 'committed',
        **plan['request']['publicationUnit'], 'readDocumentIds': ids, 'writeDocumentIds': write_ids,
        'readOnlyDocumentIds': readonly, 'writeFieldsByDocument': payload['writeFieldsByDocument'],
        'auditWriteFields': ['updatedAt', 'updatedById'], 'expectedReadUpdateTimes': payload['expectedReadUpdateTimes'],
        'expectedLiveFingerprint': payload['expectedLiveFingerprint'], 'publicationConfirmed': False}
    if set(commit) != set(expected) | {'writeResults'} or any(commit.get(k) != v for k, v in expected.items()):
        raise ValueError('exact v3 commit result required')
    entries = commit['writeResults']
    if not isinstance(entries, list) or any(not isinstance(e, dict) or set(e) != {'questionId', 'updateTime'} for e in entries):
        raise ValueError('exact write-result entries required')
    versions = {e['questionId']: e['updateTime'] for e in entries}
    if len(entries) != len(versions) or set(versions) != set(write_ids): raise ValueError('write-result coverage differs')
    uploader.resolve_expected_update_times({'expectedUpdateTimes': versions}, payload['questions'], ('explanationText',))
    if set(current) != set(ids): raise ValueError('full sibling readback required')
    docs = {q['questionId']: uploader.build_doc_data_base(q) for q in payload['questions']}
    for identity in ids:
        before_snapshot = plan['public']['baseline'][identity]; after_snapshot = current[identity]
        before, after = before_snapshot['fields'], after_snapshot['fields']
        if after_snapshot.get('exists') is not True or after_snapshot.get('questionId') != identity: raise ValueError('readback existing identity differs')
        if identity in readonly:
            if after_snapshot != before_snapshot: raise ValueError('read-only sibling changed')
            continue
        mask = payload['writeFieldsByDocument'][identity]
        if after_snapshot['updateTimeExact'] != versions[identity]: raise ValueError('exact commit readback time differs')
        if any(field not in after or after[field] != docs[identity][field] for field in mask): raise ValueError('public readback mismatch')
        if any((field in before) != (field in after) or before.get(field) != after.get(field)
               for field in set(before) | set(after) if field not in {*mask, 'updatedAt', 'updatedById'}):
            raise ValueError('outside-mask presence/value changed')
        if 'updatedAt' not in after or after.get('updatedById') != uploader.UPDATED_BY_ID: raise ValueError('write audit readback differs')
    uploader.validate_tf_truth_questions([{'questionId': i, **current[i]['fields']} for i in ids])
    return versions


@native.operation
def publish(root, path, approval_path, *, coordinator, connection, evaluation=None, failure_hook=None):
    root = _physical(root); path = _inside(root, path, private=True)
    directory = path.parent / 'publication'
    if (directory / 'intent.json').exists():
        return reconcile(root, path, coordinator=coordinator, connection=connection, evaluation=evaluation)
    approval_raw = _read(root, approval_path, {}, private=True); approval = _json(approval_raw)
    guard = native.capture_native_guard([approval])
    root, path, plan = _plan(root, path, coordinator, connection)
    gates = _publication_gates(root, path, plan, coordinator, connection, evaluation)
    scope_value = _scope(root, path, plan, 'publish', gates=gates)
    binding = verify_independent_approval(approval, scope_value, production=True)
    head = _git(root, plan); payload = _payload(plan)
    with _lock(root, plan):
        _plan(root, path, coordinator, connection)
        if gates != _publication_gates(root, path, plan, coordinator, connection, evaluation): raise ValueError('publication gates changed')
        if _git(root, plan) != head or verify_independent_approval(approval, scope_value, production=True) != binding: raise ValueError('publication authority changed')
        native.assert_native_guard(guard, terminal=True)
        directory.mkdir(mode=0o700)
        intent = {'schemaVersion': RESULT, 'planHash': plan['planHash'], 'gitHead': head, 'scope': scope_value,
            'approval': _reference(_inside(root, approval_path, private=True), approval_raw), 'nativeBinding': binding,
            'payload': payload}
        _exclusive(directory / 'intent.json', _bytes(intent))
        if failure_hook: failure_hook('intent')
        _exclusive(directory / 'attempt.json', _bytes({'planHash': plan['planHash'], 'intentHash': sha256_json(intent)}))
        if _git(root, plan) != head: raise ValueError('committed inputs changed before transport')
        _plan(root, path, coordinator, connection)
        if gates != _publication_gates(root, path, plan, coordinator, connection, evaluation):
            raise ValueError('publication gates changed before transport')
        publication_files = {}
        for name, expected in (('intent.json', intent), ('attempt.json', {'planHash': plan['planHash'], 'intentHash': sha256_json(intent)})):
            if _read(root, directory / name, publication_files) != _bytes(expected):
                raise ValueError('publication files changed before transport')
        if _read(root, approval_path, publication_files, private=True) != approval_raw:
            raise ValueError('publication approval bytes changed before transport')
        _plan(root, path, coordinator, connection)
        _terminal_publication_guard(root, path, plan, connection, gates, publication_files, guard)
        try:
            commit = uploader.upload_read_set_maintenance_questions(payload, 'native approved normal source TF')
        except BaseException:
            # No blind retry. An unconfirmed transaction cannot be reconstructed
            # solely from after values; its exact public SDK write results matter.
            raise
        _exclusive(directory / 'commit.json', _bytes(commit))
        if failure_hook: failure_hook('commit')
        native.assert_native_guard(guard, terminal=True)
    return reconcile(root, path, coordinator=coordinator, connection=connection, evaluation=evaluation)


@native.operation
def reconcile(root, path, *, coordinator, connection, evaluation=None):
    root = _physical(root); path = _inside(root, path, private=True)
    directory = path.parent / 'publication'
    publication_files = {}
    intent = _load(root, directory / 'intent.json', publication_files)
    approval = _ref(root, intent['approval'], publication_files, private=True)
    guard = native.capture_native_guard([approval])
    attempt = _load(root, directory / 'attempt.json', publication_files)
    if not (directory / 'commit.json').is_file(): raise GateRequired('commit_unconfirmed; exact SDK results required; no automatic replay')
    commit_raw = _read(root, directory / 'commit.json', publication_files)
    commit = _json(commit_raw)
    root, path, plan = _plan(root, path, coordinator, connection, live=False)
    if intent.get('planHash') != plan['planHash'] or intent.get('payload') != _payload(plan): raise ValueError('publication intent drift')
    if verify_independent_approval(approval, intent['scope'], production=True) != intent['nativeBinding']: raise ValueError('publication approval drift')
    gates = _publication_gates(root, path, plan, coordinator, connection, evaluation)
    if intent['scope'] != _scope(root, path, plan, 'publish', gates=gates) or _git(root, plan) != intent['gitHead']:
        raise ValueError('publication scope/gates/git drift')
    if attempt != {'planHash': plan['planHash'], 'intentHash': sha256_json(intent)}: raise ValueError('publication attempt differs')
    _unchanged(root, publication_files)
    current = _snapshot(plan['request'])
    versions = _readback(plan, intent['payload'], commit, current)
    _unchanged(root, publication_files)
    # The read itself can race source/task/native/evaluation changes. Recheck the
    # complete authority graph after readback and immediately before its result.
    _plan(root, path, coordinator, connection, live=False)
    if gates != _publication_gates(root, path, plan, coordinator, connection, evaluation) or _git(root, plan) != intent['gitHead']:
        raise ValueError('publication dependencies changed during readback')
    if verify_independent_approval(approval, intent['scope'], production=True) != intent['nativeBinding']:
        raise ValueError('publication approval changed during readback')
    _plan(root, path, coordinator, connection, live=False)
    result = {'schemaVersion': RESULT, 'planHash': plan['planHash'], 'unitHash': plan['unitHash'],
        'status': 'published_readback_matched', 'transportSchema': uploader.READ_SET_TF_TRUTH_SCHEMA,
        'commit': _reference(directory / 'commit.json', commit_raw),
        'writeVersions': versions, 'liveReadbackHash': sha256_json(current), 'liveReadback': current,
        'FirestoreWritten': True, 'completionRecorded': False, 'remainingGates': ['task_completion_dispatch']}
    _terminal_publication_guard(root, path, plan, connection, gates, publication_files, guard)
    output = directory / 'result.json'
    if output.exists():
        if _read(root, output, {}) != _bytes(result): raise ValueError('saved readback result drift')
    else: _exclusive(output, _bytes(result))
    _terminal_publication_guard(root, path, plan, connection, gates, publication_files, guard)
    return result


def complete(*args, **kwargs):
    raise GateRequired('dedicated_daily_publication_result_dispatch_not_connected; publication is not task completion')


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, required=True); parser.add_argument('--db', type=Path, default=LEDGER)
    subs = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'inspect', 'scope', 'save', 'check', 'evaluate', 'publish', 'reconcile'):
        sub = subs.add_parser(name)
        if name == 'prepare':
            sub.add_argument('--request', type=Path, required=True); sub.add_argument('--output', type=Path, required=True)
        else: sub.add_argument('--plan', type=Path, required=True)
        if name in ('save', 'publish'): sub.add_argument('--approval', type=Path, required=True)
        if name == 'scope': sub.add_argument('--action', choices=('save', 'publish'), required=True)
    args = parser.parse_args(argv); root = _physical(args.repo_root)
    with closing(sqlite3.connect('file:' + str(_physical(args.db)) + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row; coordinator = normal.coordinator_for(root)
        if args.command == 'prepare':
            result = prepare(root, args.request, args.output, coordinator=coordinator, connection=connection)
        else:
            with native.native_operation(argv, module_name=MODULE):
                options = {'coordinator': coordinator, 'connection': connection}
                if args.command in ('save', 'publish'): options['approval_path'] = args.approval
                if args.command == 'scope': options['action'] = args.action
                result = globals()[args.command](root, args.plan, **options)
        if args.command == 'scope': print(scope_marker(result, creation=True))
        else: print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
