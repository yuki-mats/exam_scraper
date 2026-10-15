"""Projection contract for independently detected verified-gas maintenance.

This adds no publication permission. The dedicated task fixes every delta;
the save engine still requires three native law reviews and a final approval.
"""
from copy import deepcopy

from tools.question_bank.feedback_improvement_plan import (
    checked_json, project_verified_bundle, EXPLANATION_FIELDS,
)

PATCH_SCHEMA = 'feedback-verified-maintenance-private-patch/v1'
FIELDS = EXPLANATION_FIELDS | {'lawRevisionFacts'}


def changes(updates):
    return [{'questionId':u['questionId'],'fieldDiffs':u['fieldDiffs']} for u in updates]


def project_maintenance_bundle(before, updates, binding):
    proposal = checked_json(binding['proposalRef'])
    if (proposal.get('schemaVersion') != 'feedback-gas-maintenance-content-scope/v1'
            or proposal.get('scopeHash') != binding['scopeHash']
            or proposal.get('taskId') != binding['relatedImprovementTaskId']
            or proposal.get('originalQuestionId') != binding['originalQuestionId']
            or changes(proposal['proposedUpdates']) != changes(updates)):
        raise ValueError('maintenance deltas differ from registered authority')
    ids = [u['questionId'] for u in updates]
    rows = before.get('questions', [])
    by_id = {r['questionId']:r for r in rows}
    siblings = [r['questionId'] for r in rows if r.get('originalQuestionId') == binding['originalQuestionId']]
    if (siblings != binding['allSiblingIds'] or len(ids) != len(set(ids))
            or not ids or not set(ids) <= set(siblings)):
        raise ValueError('one complete original sibling unit required')
    # The existing projector owns explanation/suggestion shape and preservation.
    explanation_updates=[]
    legal_updates=[]
    for update in updates:
        if update['questionId'] not in by_id:
            raise ValueError('maintenance document absent')
        diffs=update.get('fieldDiffs')
        if (not isinstance(diffs,list) or not diffs
                or len({d['field'] for d in diffs}) != len(diffs)
                or any(d['field'] not in FIELDS for d in diffs)):
            raise ValueError('unsupported maintenance fields')
        regular=[d for d in diffs if d['field'] in EXPLANATION_FIELDS]
        if regular: explanation_updates.append({**update,'fieldDiffs':regular})
        legal_updates += [(update,d) for d in diffs if d['field']=='lawRevisionFacts']
    if not explanation_updates:
        raise ValueError('maintenance must fulfill a researched explanation need')
    projected=project_verified_bundle(before,explanation_updates)
    projected_by_id={r['questionId']:r for r in projected['questions']}
    for update,delta in legal_updates:
        original=by_id[update['questionId']]
        snapshot=checked_json(update['baseline'])
        old=delta.get('before'); new=delta.get('after')
        if (snapshot.get('questionId') != update['questionId'] or snapshot.get('exists') is not True
                or snapshot.get('updateTimeExact') != update['baseline'].get('updateTimeExact')
                or delta.get('beforePresent') is not True or delta.get('afterPresent',True) is not True
                or not isinstance(old,dict) or not isinstance(new,dict)
                or original.get('lawRevisionFacts') != old or snapshot['fields'].get('lawRevisionFacts') != old
                or old==new):
            raise ValueError('law facts replacement before binding differs')
        # This route repairs explanatory difference facts, preserving legal
        # locators, verdicts, audit provenance and all other metadata.
        if ({k:v for k,v in old.items() if k!='differenceFacts'}
                != {k:v for k,v in new.items() if k!='differenceFacts'}
                or not isinstance(old.get('differenceFacts'),list)
                or not isinstance(new.get('differenceFacts'),list)
                or not new['differenceFacts']
                or any(not isinstance(v,str) or not v.strip() for v in new['differenceFacts'])):
            raise ValueError('law metadata outside explanatory difference facts changed')
        projected_by_id[update['questionId']]['lawRevisionFacts']=deepcopy(new)
    candidate=checked_json(proposal['candidate'])
    if projected != candidate or proposal['candidate']['sha256'] != binding['candidateSha256']:
        raise ValueError('registered maintenance candidate differs')
    expected=[{'questionId':i,'writeFields':sorted(d['field'] for u in updates if u['questionId']==i for d in u['fieldDiffs'])} for i in siblings]
    if expected != binding['publicationDocuments']:
        raise ValueError('registered maintenance write scope differs')
    return projected


def validate_private_patch(patch, update, task_id, binding):
    if (patch.get('schemaVersion') != PATCH_SCHEMA or patch.get('taskId') != task_id
            or patch.get('maintenanceTaskId') != binding['taskId']
            or patch.get('questionId') != update['questionId']
            or patch.get('fieldDiffs') != update['fieldDiffs']
            or patch.get('precondition') != {'updateTimeExact':update['baseline']['updateTimeExact'],
                'baselineSha256':update['baseline']['sha256']}
            or any(patch.get(k) is not False for k in ('approvalReady','formalPatchSaved','FirestoreWritten','executablePatch','fullDocumentUploadAllowed'))):
        raise ValueError('dedicated maintenance private patch differs')
