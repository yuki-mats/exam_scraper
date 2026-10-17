"""Validate completed, scoped publisher runs before closing an improvement task.

This reads existing receipts and production data. It never publishes anything.
Unsupported publication paths stay open rather than accepting asserted flags.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.question_review_console.formal_correction_save import load_saved_units, physical
from tools.question_review_console.firestore_readback import FirestoreReadback, PRODUCTION_PROJECT_ID
from tools.question_review_console.projection import sha256_json
from scripts.upload.upload_questions_to_firestore import resolve_write_fields


def _bound_json(root, ref):
    if not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}:
        raise ValueError('bound publication file required')
    path = physical(root, ref['path'])
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != ref['sha256']:
        raise ValueError('publication evidence bytes differ')
    return path, json.loads(content)


def verify_improvement_publication(root, artifact, *, task_id, source_question_ids, task_requirement, live_reader=None):
    # Dedicated verified25 revisions have their own genuine native stage,
    # evaluation and atomic-commit contract. Do not cast them as old v2 units.
    if isinstance(artifact, dict) and artifact.get('schemaVersion') == 'feedback-verified-improvement-publication/v1':
        from tools.question_bank.feedback_verified_delivery import verify_completion
        return verify_completion(root, artifact, task_id=task_id,
            source_question_ids=source_question_ids, task_requirement=task_requirement,
            live_reader=live_reader)
    root = Path(root).resolve()
    if (not isinstance(artifact, dict) or set(artifact) != {'schemaVersion', 'taskId', 'formalPlan', 'formalReceipt', 'publicationFiles'}
            or artifact['schemaVersion'] != 'feedback-improvement-publication/v1' or artifact['taskId'] != task_id):
        raise ValueError('dedicated task publication evidence required')
    plan_path, plan = _bound_json(root, artifact['formalPlan'])
    receipt_path, save = _bound_json(root, artifact['formalReceipt'])
    if save.get('schemaVersion') != 'formal-correction-save-receipt/v2':
        raise ValueError('native independent formal save receipt required')
    units = load_saved_units(root, plan_path, receipt_path)
    fulfillment = plan.get('feedbackTasks', {}).get(task_id)
    if (not isinstance(fulfillment, dict) or set(fulfillment) != {'requirement', 'requirementHash', 'satisfiedByUnitHashes'}
            or {k:v for k,v in fulfillment.items() if k != 'satisfiedByUnitHashes'} != task_requirement
            or fulfillment['requirementHash'] != sha256_json(fulfillment['requirement'])
            or len(fulfillment['satisfiedByUnitHashes']) != 1):
        raise ValueError('task requirement fulfillment must be bound to independent approval')
    files = artifact['publicationFiles']
    if not isinstance(files, dict) or set(files) != {'manifest.json', 'preflight.json', 'artifact.json', 'readback.json', 'result.json'}:
        raise ValueError('complete publisher run files required')
    values, paths = {}, {}
    for name, ref in files.items():
        paths[name], values[name] = _bound_json(root, ref)
    directory = paths['result.json'].parent
    if (not directory.is_relative_to(root / 'output/question_review_console/publish_runs')
            or any(path != directory / name for name, path in paths.items())):
        raise ValueError('existing publisher run directory required')
    manifest, preflight, result, readback = (values[n] for n in ('manifest.json', 'preflight.json', 'result.json', 'readback.json'))
    if (manifest.get('schemaVersion') != 'question-publish-run/v1' or manifest.get('status') != 'succeeded'
            or result.get('schemaVersion') != 'question-publish-result/v1' or result.get('status') != 'succeeded'
            or not manifest.get('runId') or result.get('runId') != manifest['runId']
            or manifest.get('projectId') != PRODUCTION_PROJECT_ID or readback.get('projectId') != PRODUCTION_PROJECT_ID
            or preflight.get('projectId') != PRODUCTION_PROJECT_ID or preflight.get('canPublish') is not True
            or preflight.get('publishReady') is not True or preflight.get('evaluationStatus') != 'passed'
            or not preflight.get('evaluationHash') or preflight['evaluationHash'] != manifest.get('evaluationHash')
            or preflight.get('missingCount') != 0 or preflight.get('failedDeltaPaths') != []
            or result.get('publishedCount', 0) <= 0 or result.get('readback') != readback
            or readback.get('sourceUnchanged') is not True or readback.get('status') != 'match'
            or readback.get('missingDocumentIds') != [] or readback.get('differenceCount') != 0):
        raise ValueError('successful gated production publication and readback required')
    approved, absent, masks, allowed_sets = {}, {}, {}, []
    for unit in units:
        if unit.manifest['unitHash'] not in fulfillment['satisfiedByUnitHashes']:
            continue
        ids = unit.manifest['publicationIds']
        projected = unit.validate()
        # Complete sibling unit must belong to an actual task source question.
        if not any(i in source_question_ids or d.get('originalQuestionId') in source_question_ids for i, d in zip(ids, projected, strict=True)):
            continue
        allowed_sets.append(set(ids))
        for identity, doc, delta in zip(ids, projected, unit.manifest['limitedDelta'], strict=True):
            approved[identity] = doc
            masks[identity] = delta['allowlist']
            absent[identity] = {k for k,v in unit.source['snapshot'][identity]['fields'].items() if v['present'] is False and k not in delta['fields']}
    documents = values['artifact.json'].get('questions', [])
    # A per-document mask alone is ignored by the actual uploader. Completion
    # requires its real root-level contract, equal to every approved mask.
    upload_mask = resolve_write_fields(values['artifact.json'])
    if not upload_mask:
        raise ValueError('actual uploader narrow writeFields contract required')
    ids = [d.get('questionId') for d in documents]
    if (not ids or len(ids) != len(set(ids)) or not any(set(ids) == s for s in allowed_sets)
            or manifest.get('documentIds') != ids or result.get('documentCount') != len(ids)
            or readback.get('documentCount') != len(ids)):
        raise ValueError('publication IDs do not match complete task sibling unit')
    covered_sources = set(ids) | {approved[i].get('originalQuestionId') for i in ids}
    if not source_question_ids <= covered_sources:
        raise ValueError('all task source questions must be published before completion')
    if (preflight.get('candidateHash') != sha256_json(documents) or manifest.get('candidateHash') != preflight['candidateHash']
            or not manifest.get('sourceHash') or preflight.get('sourceHash') != manifest['sourceHash']
            or readback.get('sourceHash') != manifest['sourceHash']
            or preflight.get('documentCount') != len(ids) or result.get('publishedCount') != preflight.get('changedCount')):
        raise ValueError('publisher fixed artifact/source/count binding differs')
    for document in documents:
        identity = document['questionId']
        if (document.get('writeFields') != masks[identity] or not masks[identity]
                or tuple(masks[identity]) != upload_mask
                or any(k not in document or document[k] != approved[identity].get(k) for k in masks[identity])):
            raise ValueError('published field mask or values differ from independent approval')
    recorded = readback.get('documents', [])
    if [d.get('questionId') for d in recorded] != ids or any(d.get('status') != 'match' or d.get('differences') != [] for d in recorded):
        raise ValueError('recorded readback document coverage differs')
    for d in recorded:
        identity = d['questionId']; live_fields = d.get('live')
        if (not isinstance(live_fields, dict) or any(k not in live_fields or live_fields[k] != v for k,v in approved[identity].items())
                or any(k in live_fields for k in absent[identity])):
            raise ValueError('recorded readback values differ from saved candidate')
    reader = live_reader or FirestoreReadback().read_documents
    fields = sorted(set().union(*(set(approved[i]) | absent[i] for i in ids)))
    live = reader(ids, fields=fields)
    for identity in ids:
        if (identity not in live or any(k not in live[identity] or live[identity][k] != v for k,v in approved[identity].items())
                or any(k in live[identity] for k in absent[identity])):
            raise ValueError('fresh live readback differs or preserved fields changed')
    # Reject drift during network readback, then replay immutable saved approval.
    for name, ref in files.items():
        _bound_json(root, ref)
    _bound_json(root, artifact['formalPlan']); _bound_json(root, artifact['formalReceipt'])
    load_saved_units(root, plan_path, receipt_path)
    return {'runId': manifest['runId'], 'documentIds': ids, 'liveReadbackMatched': True}
