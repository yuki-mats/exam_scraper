"""Audited, exclusive replacement of one verified gas explanation canonical.

Formal save only. A missing canonical during a recoverable transaction is a
hard read failure, never an invitation to bootstrap it again. No Firestore writes.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shlex
import sqlite3
import sys

from scripts.common.scoped_canonical_context import file_hash
from scripts.pipeline import rebuild_gas_shunin_all_year_artifacts as gas
from tools.question_bank.feedback_improvement_plan import checked_json, project_verified_bundle, EXPLANATION_FIELDS
from tools.question_bank.feedback_task_requirements import task_requirement_binding
from tools.question_review_console.formal_correction_save import _rename_exclusive
from tools.question_review_console.independent_approval import scope_marker, verify_independent_approval
from tools.question_review_console.firestore_readback import FirestoreReadback, PRODUCTION_PROJECT_ID
from tools.question_review_console.projection import sha256_json
from tools.question_review_console.question_patch_proposal import _canonical_file_locks
from tools.question_review_console.scoped_artifacts import write_json

SCHEMA = 'feedback-verified-explanation-save/v1'
RECEIPT = 'feedback-verified-explanation-save-receipt/v1'
PRIVATE = Path('output/user_feedback_response_system/execution')
LEDGER = Path.home() / 'Library/Application Support/Repaso/feedback-ops/ledger.sqlite3'
DEPENDENCIES = [Path(__file__), Path(gas.__file__)]


def _physical(path):
    p = Path(path).absolute()
    if p != p.resolve() or p.is_symlink():
        raise ValueError('physical path required')
    return p


def _private(root, path):
    p = _physical(path)
    if not p.is_relative_to(root / PRIVATE):
        raise ValueError('private execution path required')
    return p


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def _sync(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _exclusive(path, data):
    import tempfile
    fd, temporary = tempfile.mkstemp(prefix='.pending-',dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _rename_exclusive(temporary,path)
        _sync(path.parent)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def _connection():
    connection = sqlite3.connect(f'file:{LEDGER}?mode=rw', uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _requirement(connection, task_id):
    return task_requirement_binding(connection, task_id)


def _snapshots(ids, fields):
    db = FirestoreReadback()._database()
    refs = [db.collection('questions').document(i) for i in ids]
    from tools.question_bank.question_issue_report_store import _json_value
    result = {}
    for snapshot in db.get_all(refs, field_paths=fields):
        if snapshot.id not in ids or snapshot.id in result or not snapshot.exists:
            raise ValueError('existing complete sibling snapshot required')
        if not hasattr(snapshot.update_time, 'rfc3339'):
            raise ValueError('exact production update time required')
        result[snapshot.id] = {'questionId': snapshot.id, 'exists': True,
            'updateTimeExact': snapshot.update_time.rfc3339(),
            'fields': _json_value(snapshot.to_dict() or {})}
    if set(result) != set(ids):
        raise ValueError('missing sibling snapshot')
    originals={s['fields'].get('originalQuestionId') for s in result.values()}
    if len(originals)!=1 or None in originals:
        raise ValueError('one original sibling identity required')
    from google.cloud.firestore_v1.base_query import FieldFilter
    query=db.collection('questions').where(filter=FieldFilter('originalQuestionId','==',next(iter(originals))))
    all_ids={s.id for s in query.select(['isDeleted','isChoiceOnly']).stream()
        if (s.to_dict() or {}).get('isDeleted') is False and (s.to_dict() or {}).get('isChoiceOnly') is False}
    if all_ids != set(ids):
        raise ValueError('live sibling membership changed')
    return result


def _dependencies():
    # Fix the implementation used by this save and its evidence readers.
    paths = set(DEPENDENCIES)
    for module in ('tools.question_bank.feedback_improvement_plan',
        'tools.question_bank.feedback_task_requirements',
        'tools.question_review_console.independent_approval',
        'tools.question_review_console.formal_correction_save',
        'tools.question_review_console.question_patch_proposal',
        'tools.question_review_console.firestore_readback',
        'tools.question_review_console.projection',
        'tools.question_review_console.scoped_artifacts',
        'tools.question_bank.question_issue_report_store',
        'scripts.common.scoped_canonical_context',
        'scripts.common.repaso_firestore_schema'):
        import importlib
        paths.add(Path(importlib.import_module(module).__file__))
    return {str(p.resolve()): file_hash(p) for p in paths}


def prepare(root, result_path, canonical_path, destination, *, document_index,
            pdf_verification, connection):
    root = _physical(root)
    destination = _private(root, destination)
    if destination.exists():
        raise ValueError('fresh plan directory required')
    result_path, canonical_path = _physical(result_path), _physical(canonical_path)
    result = json.loads(result_path.read_bytes())
    if (result.get('schemaVersion') != 'feedback-improvement-result/v1'
            or result.get('status') != 'review_pending'
            or any(result.get(k) is not False for k in ('approvalReady','formalPatchSaved','FirestoreWritten'))
            or len(result.get('proposedUpdates', [])) != 1 or not result.get('primaryEvidence')):
        raise ValueError('one unapproved researched task update required')
    updates = result['proposedUpdates']; update = updates[0]
    before_bytes = canonical_path.read_bytes()
    before_hash = hashlib.sha256(before_bytes).hexdigest()
    before = json.loads(before_bytes)
    year, qualification = str(before['list_group_id']), before['qualificationId']
    expected = root / 'output' / qualification / 'questions_json' / year / '25_verified_publication' / f'{year}_verified_publication.json'
    if canonical_path != expected:
        raise ValueError('verified canonical path differs')
    official = gas.verify_official_pdf_evidence(document_index=Path(document_index),
        pdf_verification=Path(pdf_verification), root=root)
    if any(before.get('evidence', {}).get(k) != official[k] for k in ('documentIndexSha256','pdfVerificationSha256')):
        raise ValueError('official provenance differs from canonical')
    projected = project_verified_bundle(before, updates)
    requirement = _requirement(connection, result['taskId'])
    selected = next(r for r in before['questions'] if r['questionId'] == update['questionId'])
    siblings = [r for r in before['questions'] if r['originalQuestionId'] == selected['originalQuestionId']]
    ids = [r['questionId'] for r in siblings]
    source_ids = {s['question_id'] for s in requirement['requirement']['sources']}
    if not source_ids <= set(ids) | {selected['originalQuestionId']}:
        raise ValueError('one sibling unit must cover all task sources')
    # questionId is the document identity, not a stored Firestore payload field.
    mask = sorted((set().union(*(set(r) for r in siblings)) | EXPLANATION_FIELDS) - gas.METADATA_FIELDS - {'questionId'})
    live = _snapshots(ids, mask)
    for row in siblings:
        if live[row['questionId']]['fields'] != {k:v for k,v in row.items() if k in mask}:
            raise ValueError('canonical and fresh sibling baseline differ')
    baseline = checked_json(update['baseline'])
    target = live[update['questionId']]
    if target['updateTimeExact'] != baseline.get('updateTimeExact'):
        raise ValueError('target baseline version changed')
    patch = checked_json(update['preparedPatch'])
    if (patch.get('schemaVersion') != 'feedback-improvement-private-patch/v1'
            or patch.get('taskId') != result['taskId'] or patch.get('questionId') != update['questionId']
            or patch.get('fieldDiffs') != update['fieldDiffs']
            or patch.get('precondition') != {'updateTimeExact': baseline['updateTimeExact'], 'baselineSha256': update['baseline']['sha256']}
            or any(patch.get(k) is not False for k in ('approvalReady','formalPatchSaved','FirestoreWritten','executablePatch','fullDocumentUploadAllowed'))):
        raise ValueError('private patch binding differs')
    inputs = _dependencies()
    refs = [update['baseline'], update['preparedPatch'], *result['primaryEvidence']]
    for ref in refs:
        checked_json(ref) if Path(ref['ref']).suffix == '.json' else None
        path = _physical(ref['ref'])
        if file_hash(path) != ref['sha256']:
            raise ValueError('primary or candidate evidence changed')
        inputs[str(path)] = ref['sha256']
    inputs.update({str(result_path):file_hash(result_path), str(_physical(document_index)):file_hash(document_index),
        str(_physical(pdf_verification)):file_hash(pdf_verification)})
    index = json.loads(Path(document_index).read_bytes())
    for record in index['records']:
        for key in ('officialQuestionPdf','officialAnswerPdf'):
            ref = record[key]; path = _physical(root / ref['path'])
            inputs[str(path)] = ref['sha256']
    if file_hash(canonical_path) != before_hash:
        raise ValueError('canonical changed during preparation')
    destination.mkdir(parents=True, mode=0o700)
    _exclusive(destination/'before.json', before_bytes)
    write_json(destination/'candidate.json', projected)
    changed = {update['questionId']: sorted(d['field'] for d in update['fieldDiffs'])}
    plan = {'schemaVersion': SCHEMA, 'taskId': result['taskId'], 'taskRequirement':requirement,
        'formalPath':str(canonical_path.relative_to(root)), 'beforeSha256':before_hash,
        'beforeRef':str(destination/'before.json'), 'beforeCopySha256':file_hash(destination/'before.json'),
        'candidateRef':str(destination/'candidate.json'), 'candidateSha256':file_hash(destination/'candidate.json'),
        'updates':updates, 'inputs':inputs, 'projectId':PRODUCTION_PROJECT_ID,
        'resultRef':{'ref':str(result_path),'sha256':inputs[str(result_path)]},
        'documentIndexRef':{'ref':str(_physical(document_index)),'sha256':file_hash(document_index)},
        'pdfVerificationRef':{'ref':str(_physical(pdf_verification)),'sha256':file_hash(pdf_verification)},
        'siblingIds':ids, 'snapshotFields':mask, 'baseline':live,
        'publicationDocuments':[{'questionId':i, 'writeFields':changed.get(i,[])} for i in ids],
        'requiredPublicationGates':['merge_convert','required_checkers','quality_gate',
            'independent_evaluation','scoped_upload_dry_run','fresh_live_preconditions','live_readback'],
        'lawReviewRequired':_law_required(before,updates),
        'FirestoreWritten':False}
    plan['planHash'] = sha256_json(plan)
    if file_hash(canonical_path) != before_hash:
        raise ValueError('canonical changed during preparation')
    write_json(destination/'formal-save-plan.json',plan)
    read_plan(root, destination/'formal-save-plan.json',verify_law=False)
    return plan


def _law_required(before,updates):
    ids={u['questionId'] for u in updates}
    return any('-law-' in r['questionId'] or r.get('lawReferences') or r.get('lawRevisionFacts')
        for r in before['questions'] if r['questionId'] in ids)


def read_plan(root, path, *, verify_law=True):
    root = _physical(root); path = _private(root,path)
    plan = json.loads(path.read_bytes())
    if plan.get('schemaVersion') != SCHEMA or sha256_json({k:v for k,v in plan.items() if k!='planHash'}) != plan.get('planHash'):
        raise ValueError('save plan hash or schema differs')
    if plan.get('projectId') != PRODUCTION_PROJECT_ID or plan.get('FirestoreWritten') is not False:
        raise ValueError('formal save is not publication')
    formal = _physical(root/plan['formalPath'])
    before_path = _private(root,plan['beforeRef']); candidate_path = _private(root,plan['candidateRef'])
    if before_path.parent != path.parent or candidate_path.parent != path.parent:
        raise ValueError('plan material directory differs')
    if (plan['beforeCopySha256'] != plan['beforeSha256']
            or file_hash(before_path) != plan['beforeSha256']
            or file_hash(candidate_path) != plan['candidateSha256']):
        raise ValueError('fixed save material changed')
    for p,h in plan['inputs'].items():
        if file_hash(_physical(p)) != h:
            raise ValueError('save input changed')
    if not set(_dependencies()) <= set(plan['inputs']) or any(plan['inputs'][p] != h for p,h in _dependencies().items()):
        raise ValueError('current implementation binding differs')
    before = json.loads(before_path.read_bytes())
    result = checked_json(plan['resultRef'])
    if (result.get('schemaVersion') != 'feedback-improvement-result/v1'
            or result.get('taskId') != plan['taskId'] or result.get('status') != 'review_pending'
            or result.get('proposedUpdates') != plan['updates'] or not result.get('primaryEvidence')
            or any(result.get(k) is not False for k in ('approvalReady','formalPatchSaved','FirestoreWritten'))):
        raise ValueError('current researched result binding differs')
    index = checked_json(plan['documentIndexRef'])
    checked_json(plan['pdfVerificationRef'])
    official = gas.verify_official_pdf_evidence(document_index=Path(plan['documentIndexRef']['ref']),
        pdf_verification=Path(plan['pdfVerificationRef']['ref']),root=root)
    if (any(before.get('evidence',{}).get(k) != official[k] for k in ('documentIndexSha256','pdfVerificationSha256'))
            or not {r['questionId'] for r in before['questions']} <= {r['questionId'] for r in index['records']}):
        raise ValueError('complete canonical official provenance differs')
    required_refs=[plan['resultRef'],plan['documentIndexRef'],plan['pdfVerificationRef'],
        *result['primaryEvidence']]
    for update in plan['updates']:
        required_refs.extend([update['baseline'],update['preparedPatch']])
        patch=checked_json(update['preparedPatch'])
        if (patch.get('schemaVersion') != 'feedback-improvement-private-patch/v1'
                or patch.get('taskId') != plan['taskId'] or patch.get('questionId') != update['questionId']
                or patch.get('fieldDiffs') != update['fieldDiffs']
                or patch.get('precondition') != {'updateTimeExact':update['baseline']['updateTimeExact'],
                    'baselineSha256':update['baseline']['sha256']}
                or any(patch.get(k) is not False for k in ('approvalReady','formalPatchSaved','FirestoreWritten','executablePatch','fullDocumentUploadAllowed'))):
            raise ValueError('fixed private patch differs')
    for record in index['records']:
        for key in ('officialQuestionPdf','officialAnswerPdf'):
            ref=record[key]
            required_refs.append({'ref':str(_physical(root/ref['path'])),'sha256':ref['sha256']})
    for ref in required_refs:
        p=_physical(ref['ref'])
        if plan['inputs'].get(str(p)) != ref['sha256'] or file_hash(p) != ref['sha256']:
            raise ValueError('required evidence input missing or changed')
    expected = root/'output'/before['qualificationId']/'questions_json'/str(before['list_group_id'])/'25_verified_publication'/f"{before['list_group_id']}_verified_publication.json"
    if formal != expected or len(plan['updates']) != 1:
        raise ValueError('one canonical update scope required')
    projected = project_verified_bundle(before,plan['updates'])
    if type(plan.get('lawReviewRequired')) is not bool or plan['lawReviewRequired'] != bool(_law_required(before,plan['updates'])):
        raise ValueError('required law review gate differs')
    if candidate_path.read_bytes() != _bytes(projected):
        raise ValueError('candidate projection differs')
    row = next(r for r in before['questions'] if r['questionId']==plan['updates'][0]['questionId'])
    ids = [r['questionId'] for r in before['questions'] if r['originalQuestionId']==row['originalQuestionId']]
    if ids != plan['siblingIds'] or set(plan['baseline']) != set(ids):
        raise ValueError('complete sibling scope differs')
    expected_documents = [{'questionId':i,'writeFields':sorted(d['field'] for d in plan['updates'][0]['fieldDiffs']) if i==row['questionId'] else []} for i in ids]
    if plan['publicationDocuments'] != expected_documents:
        raise ValueError('limited publication fields differ')
    siblings = [r for r in before['questions'] if r['questionId'] in ids]
    mask = sorted((set().union(*(set(r) for r in siblings)) | EXPLANATION_FIELDS) - gas.METADATA_FIELDS - {'questionId'})
    requirement = plan['taskRequirement']
    if (plan['snapshotFields'] != mask
            or requirement['requirementHash'] != sha256_json(requirement['requirement'])
            or requirement['requirement']['taskId'] != plan['taskId']
            or not {s['question_id'] for s in requirement['requirement']['sources']} <= set(ids) | {row['originalQuestionId']}):
        raise ValueError('task requirement or snapshot mask differs')
    for r in siblings:
        snapshot = plan['baseline'][r['questionId']]
        if (set(snapshot) != {'questionId','exists','updateTimeExact','fields'}
                or snapshot['questionId'] != r['questionId'] or snapshot['exists'] is not True
                or not isinstance(snapshot['updateTimeExact'],str) or not snapshot['updateTimeExact']
                or snapshot['fields'] != {k:v for k,v in r.items() if k in mask}):
            raise ValueError('complete baseline differs')
    if verify_law and plan['lawReviewRequired']:
        _law_review_bindings(root,path,plan,'final')
    return plan


LAW_STAGES=('primary','secondary','tertiary')


def _law_review_bindings(root,path,plan,stop):
    reviews=[]
    for stage in LAW_STAGES:
        if stage==stop: break
        p=_private(root,Path(path).parent/f'law-{stage}-approval.json')
        if not p.is_file(): raise ValueError('three-stage native law audit incomplete')
        raw=p.read_bytes(); record=json.loads(raw)
        scope=_scope(root,path,plan,stage,reviews)
        binding=verify_independent_approval(record,scope,production=True)
        if any(r['binding']['reviewerSessionId']==binding['reviewerSessionId'] for r in reviews):
            raise ValueError('independent law stage reviewers required')
        digest=hashlib.sha256(raw).hexdigest()
        if file_hash(p)!=digest: raise ValueError('law audit evidence changed during verification')
        reviews.append({'stage':stage,'ref':str(p),'sha256':digest,'binding':binding})
    return reviews


def _scope(root,path,plan,stage,reviews):
    command = ' '.join(shlex.quote(s) for s in [sys.executable,'-m','tools.question_bank.feedback_verified_save',
        'scope','--repo-root',str(root),'--plan',str(path),'--stage',stage])
    return {'schemaVersion':'feedback-verified-explanation-independent-scope/v1',
        'action':'save_single_verified_explanation_and_review_limited_publication' if stage=='final' else f'independent_{stage}_law_grounding_review',
        'planHash':plan['planHash'],'candidateSha256':plan['candidateSha256'],
        'taskRequirement':plan['taskRequirement'], 'publicationDocuments':plan['publicationDocuments'],
        'lawReviewRequired':plan['lawReviewRequired'],'priorLawReviews':reviews,
        'nativeCreationCall':'let r=await tools.exec_command(' + json.dumps({'cmd':command,'workdir':str(root),'max_output_tokens':12000,'yield_time_ms':30000},ensure_ascii=False)
            + ');let out=r.output;while(r.session_id){r=await tools.write_stdin({session_id:r.session_id,chars:"",yield_time_ms:1000,max_output_tokens:12000});out+=r.output;}'
            + 'if(r.exit_code!==0)throw new Error("scope creation failed");text(out);'}


def approval_scope(root,path,plan,stage='final'):
    if stage not in (*LAW_STAGES,'final'): raise ValueError('unknown audit stage')
    if stage!='final' and not plan['lawReviewRequired']: raise ValueError('law audit scope absent')
    reviews=_law_review_bindings(root,path,plan,stage) if plan['lawReviewRequired'] else []
    return _scope(root,path,plan,stage,reviews)


def _fresh(plan,connection):
    row=connection.execute('SELECT status FROM improvement_tasks WHERE task_id=?',(plan['taskId'],)).fetchone()
    if row is None or row['status']!='review_pending':
        raise ValueError('review-pending task required for new save')
    if _requirement(connection,plan['taskId']) != plan['taskRequirement']:
        raise ValueError('current task requirement changed')
    if _snapshots(plan['siblingIds'],plan['snapshotFields']) != plan['baseline']:
        raise ValueError('fresh sibling live precondition changed')


def save(root,path,approval,*,connection,failure_hook=None):
    root = _physical(root); path = _private(root,path)
    plan = read_plan(root,path); formal = _physical(root/plan['formalPath'])
    scope = approval_scope(root,path,plan)
    def validate():
        _fresh(plan,connection)
        current=read_plan(root,path)
        if current != plan or approval_scope(root,path,current) != scope:
            raise ValueError('audited save scope changed')
        binding = verify_independent_approval(approval,scope,production=True)
        if _requirement(connection,plan['taskId']) != plan['taskRequirement']:
            raise ValueError('current task requirement changed')
        return binding
    journal = formal.parent / ('.feedback-save-' + plan['planHash'])
    receipt_path = path.parent/'save-receipt.json'
    if connection.in_transaction:
        raise ValueError('independent ledger transaction required')
    with _canonical_file_locks(root,[formal.relative_to(root)]):
        connection.execute('BEGIN IMMEDIATE')
        try:
            if receipt_path.exists():
                receipt = load_saved(root,path,receipt_path,connection=connection)
                if receipt['approvalEvidence'] != approval:
                    raise ValueError('different approval resend refused')
                return receipt
            binding = validate()
            if not journal.exists():
                if not formal.exists() or file_hash(formal)!=plan['beforeSha256']:
                    raise ValueError('canonical before conflict')
                journal.mkdir(mode=0o700); _sync(journal.parent)
            _physical(journal)
            if (journal/'restoring.json').exists() or (journal/'restored.json').exists():
                raise ValueError('restored plan cannot be saved again; prepare a new plan')
            if not (journal/'intent.json').exists():
                if list(journal.iterdir()) or not formal.exists() or file_hash(formal)!=plan['beforeSha256']:
                    raise ValueError('incomplete conflicting save intent')
                _exclusive(journal/'intent.json',_bytes({'planHash':plan['planHash'],'approvalEvidence':approval,'approvalBinding':binding}))
            intent = json.loads((_physical(journal/'intent.json')).read_bytes())
            if intent != {'planHash':plan['planHash'],'approvalEvidence':approval,'approvalBinding':binding}:
                raise ValueError('competing save intent')
            old = journal/'original.json'; staged = journal/'replacement.json'
            if not old.exists():
                if not formal.exists() or file_hash(formal)!=plan['beforeSha256']:
                    raise ValueError('canonical before conflict')
                _rename_exclusive(formal,old); _sync(formal.parent); _sync(journal)
                if failure_hook: failure_hook('original_moved')
            if file_hash(_physical(old)) != plan['beforeSha256']:
                # Keep a competing writer's bytes; never replace a new third value.
                if not formal.exists():
                    _rename_exclusive(old,formal); _sync(formal.parent)
                raise ValueError('moved canonical conflict; original preserved')
            if not formal.exists():
                validate()
                if not staged.exists():
                    _exclusive(staged,Path(plan['candidateRef']).read_bytes())
                if file_hash(_physical(staged)) != plan['candidateSha256']:
                    raise ValueError('staged candidate changed')
                _rename_exclusive(staged,formal); _sync(formal.parent); _sync(journal)
                if failure_hook: failure_hook('candidate_saved')
            if file_hash(formal)!=plan['candidateSha256']:
                raise ValueError('canonical third value; recovery held')
            binding = validate()
            receipt = {'schemaVersion':RECEIPT,'planHash':plan['planHash'],
                'formalPath':plan['formalPath'],'beforeSha256':plan['beforeSha256'],
                'candidateSha256':plan['candidateSha256'],'originalRef':str(old),
                'approvalEvidence':approval,'approvalBinding':binding,'FirestoreWritten':False}
            _exclusive(receipt_path,_bytes(receipt))
            return load_saved(root,path,receipt_path,connection=connection)
        finally:
            connection.rollback()


def load_saved(root,path,receipt_path,*,connection):
    root = _physical(root); path = _private(root,path)
    plan = read_plan(root,path); receipt_path = _private(root,receipt_path)
    if receipt_path != path.parent/'save-receipt.json':
        raise ValueError('save receipt path differs')
    receipt = json.loads(receipt_path.read_bytes())
    original_ref=str(_physical(root/plan['formalPath']).parent/('.feedback-save-'+plan['planHash'])/'original.json')
    if (receipt.get('schemaVersion') != RECEIPT or receipt.get('planHash') != plan['planHash']
            or receipt.get('formalPath') != plan['formalPath'] or receipt.get('candidateSha256') != plan['candidateSha256']
            or receipt.get('beforeSha256') != plan['beforeSha256'] or receipt.get('FirestoreWritten') is not False
            or receipt.get('originalRef') != original_ref
            or file_hash(_physical(root/plan['formalPath'])) != plan['candidateSha256']
            or file_hash(_physical(receipt['originalRef'])) != plan['beforeSha256']):
        raise ValueError('saved canonical receipt differs')
    binding = verify_independent_approval(receipt['approvalEvidence'],approval_scope(root,path,plan),production=True)
    intent=json.loads((_physical(Path(original_ref).parent/'intent.json')).read_bytes())
    if intent != {'planHash':plan['planHash'],'approvalEvidence':receipt['approvalEvidence'],'approvalBinding':binding}:
        raise ValueError('saved native intent differs')
    if receipt['approvalBinding'] != binding or _requirement(connection,plan['taskId']) != plan['taskRequirement']:
        raise ValueError('saved approval or task requirement differs')
    return receipt


def restore_original(root,path,approval,*,connection):
    """Restore local before bytes exclusively. A third value is never replaced."""
    root=_physical(root); path=_private(root,path)
    plan=read_plan(root,path); formal=_physical(root/plan['formalPath'])
    binding=verify_independent_approval(approval,approval_scope(root,path,plan),production=True)
    journal=_physical(formal.parent/('.feedback-save-'+plan['planHash']))
    with _canonical_file_locks(root,[formal.relative_to(root)]):
        intent=json.loads((journal/'intent.json').read_bytes())
        if intent != {'planHash':plan['planHash'],'approvalEvidence':approval,'approvalBinding':binding}:
            raise ValueError('restore intent differs')
        if _requirement(connection,plan['taskId']) != plan['taskRequirement']:
            raise ValueError('restore task binding differs')
        old=_physical(journal/'original.json')
        restored={'planHash':plan['planHash'],'restoredBeforeSha256':plan['beforeSha256']}
        restoring={'planHash':plan['planHash'],'approvalBinding':binding}
        state=journal/'restoring.json'
        if state.exists():
            if json.loads(state.read_bytes()) != restoring: raise ValueError('restore state differs')
        else:
            _exclusive(state,_bytes(restoring))
        if formal.exists() and file_hash(formal)==plan['beforeSha256']:
            moved=journal/'rolled-back-candidate.json'
            if not moved.exists() or file_hash(_physical(moved))!=plan['candidateSha256']:
                raise ValueError('restored candidate evidence absent or changed')
            if (journal/'restored.json').exists():
                if json.loads((journal/'restored.json').read_bytes()) != restored: raise ValueError('restored state differs')
            else: _exclusive(journal/'restored.json',_bytes(restored))
            return {'formalRestored':True,'FirestoreWritten':False}
        if not old.exists() or file_hash(old)!=plan['beforeSha256']:
            raise ValueError('restore original absent or changed')
        if formal.exists():
            if file_hash(formal)!=plan['candidateSha256']:
                raise ValueError('restore third value conflict')
            _rename_exclusive(formal,journal/'rolled-back-candidate.json')
            if file_hash(journal/'rolled-back-candidate.json')!=plan['candidateSha256']:
                if not formal.exists():
                    _rename_exclusive(journal/'rolled-back-candidate.json',formal)
                raise ValueError('restore moved candidate conflict')
        _rename_exclusive(old,formal); _sync(formal.parent); _sync(journal)
        _exclusive(journal/'restored.json',_bytes(restored))
        # A receipt for the now-reverted candidate no longer validates.
        return {'formalRestored':True,'FirestoreWritten':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['scope','prepare','save','verify','restore'])
    parser.add_argument('--repo-root',type=Path,required=True)
    parser.add_argument('--plan',type=Path)
    parser.add_argument('--result',type=Path)
    parser.add_argument('--canonical',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--document-index',type=Path)
    parser.add_argument('--pdf-verification',type=Path)
    parser.add_argument('--approval',type=Path)
    parser.add_argument('--stage',choices=[*LAW_STAGES,'final'],default='final')
    args=parser.parse_args()
    root=_physical(args.repo_root)
    if args.command=='scope':
        plan=read_plan(root,args.plan,verify_law=False)
        print(scope_marker(approval_scope(root,args.plan,plan,args.stage),creation=True)); return
    with _connection() as connection:
        if args.command=='prepare':
            plan=prepare(root,args.result,args.canonical,args.output,document_index=args.document_index,
                pdf_verification=args.pdf_verification,connection=connection)
            print(json.dumps({'planHash':plan['planHash'],'formalPatchSaved':False,'FirestoreWritten':False}))
        elif args.command=='save':
            receipt=save(root,args.plan,json.loads(args.approval.read_bytes()),connection=connection)
            print(json.dumps({'schemaVersion':receipt['schemaVersion'],'formalPatchSaved':True,'FirestoreWritten':False}))
        elif args.command=='restore':
            print(json.dumps(restore_original(root,args.plan,json.loads(args.approval.read_bytes()),connection=connection)))
        else:
            load_saved(root,args.plan,args.plan.parent/'save-receipt.json',connection=connection)
            print(json.dumps({'formalPatchSaved':True,'FirestoreWritten':False}))


if __name__=='__main__':
    main()
