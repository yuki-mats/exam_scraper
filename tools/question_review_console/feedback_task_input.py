"""Deliver only paraphrased improvement requirements to their real model turn.

The canonical binding still includes private source hashes. Its safe definition
is an unverified task requirement, never primary evidence or a policy override.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from scripts.common.question_identity import SourceIdentityBinding

SCHEMA = 'feedback-task-requirement-input/v1'
STAGES = frozenset({'explanation', 'law_audit'})

def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def scope_for(qualification, stage_id, target, *, input_fingerprint, projected_input_hash):
    return {'qualification': qualification, 'stageId': stage_id,
        'questionId': str(target.get('id') or target.get('uiQuestionId') or ''),
        'listGroupId': str(target.get('listGroupId') or ''),
        'sourceBinding': SourceIdentityBinding.from_mapping(target).as_mapping(),
        'inputFingerprint': input_fingerprint, 'projectedInputHash': projected_input_hash}

def _shape(value):
    if not isinstance(value, dict) or set(value) != {'schemaVersion', 'taskId', 'definition', 'requirementHash', 'scope', 'targetStatements', 'inputHash'}:
        raise ValueError('closed feedback task requirement input required')
    definition = value['definition']
    scope = value['scope']
    if (value['schemaVersion'] != SCHEMA or not isinstance(value['taskId'], str) or not value['taskId']
            or not isinstance(definition, dict) or set(definition) != {'title', 'need', 'target'}
            or any(not isinstance(item, str) or not item.strip() for item in definition.values())
            or not re.fullmatch(r'[a-f0-9]{64}', str(value['requirementHash']))
            or not isinstance(scope, dict) or set(scope) != {'qualification', 'stageId', 'questionId', 'listGroupId',
                'sourceBinding', 'inputFingerprint', 'projectedInputHash'}
            or not isinstance(scope['sourceBinding'], dict) or set(scope['sourceBinding']) != {'sourceQuestionKey', 'reviewQuestionId', 'sourceRecordRef'}
            or scope['stageId'] not in STAGES or not SourceIdentityBinding.from_mapping(scope['sourceBinding']).is_complete()
            or any(not isinstance(scope[key], str) or not scope[key] for key in
                ('qualification', 'questionId', 'listGroupId', 'inputFingerprint', 'projectedInputHash'))
            or value['inputHash'] != _hash({k:v for k,v in value.items() if k != 'inputHash'})):
        raise ValueError('feedback task requirement definition/scope/hash differs')
    statements = value['targetStatements']
    if (not isinstance(statements, list) or not statements or any(not isinstance(x, dict) or set(x) != {'questionId', 'choiceIndex', 'text', 'textHash'} or not isinstance(x['questionId'], str) or not x['questionId'] or type(x['choiceIndex']) is not int or x['choiceIndex'] < 0 or not isinstance(x['text'], str) or not x['text'] or x['textHash'] != hashlib.sha256(x['text'].encode()).hexdigest() for x in statements) or len({x['questionId'] for x in statements}) != len(statements) or len({x['choiceIndex'] for x in statements}) != len(statements)):
        raise ValueError('exact unique target statements required')
    return deepcopy(value)

def target_statements(binding, record, snapshots, *, original_question_id, normal_question_id):
    choices = record.get('choiceTextList')
    if not isinstance(choices, list) or not choices or len(set(choices)) != len(choices):
        raise ValueError('unique preserved source choices required')
    by_id = {}
    for qid, snapshot in snapshots.items():
        text = snapshot.get('fields', {}).get('originalQuestionChoiceText')
        if text not in choices or text in [x['text'] for x in by_id.values()]:
            raise ValueError('full source/live statement bijection required')
        by_id[qid] = {'questionId': qid, 'choiceIndex': choices.index(text), 'text': text,
            'textHash': hashlib.sha256(text.encode()).hexdigest()}
    if {x['text'] for x in by_id.values()} != set(choices):
        raise ValueError('all source/live siblings required')
    ids = {x['question_id'] for x in binding['requirement']['sources']}
    if ids & {original_question_id, normal_question_id}:
        ids = set(by_id)
    if not ids or not ids.issubset(by_id):
        raise ValueError('task has no exact source/live target')
    return [by_id[qid] for qid in sorted(ids)]

def safe_requirement(binding):
    if not isinstance(binding, dict) or set(binding) != {'requirement', 'requirementHash'}:
        raise ValueError('canonical improvement requirement binding required')
    requirement = binding['requirement']
    if (not isinstance(requirement, dict) or set(requirement) != {'schemaVersion', 'taskId', 'definition', 'sources'}
            or requirement['schemaVersion'] != 'feedback-task-requirement/v1'
            or binding['requirementHash'] != _hash(requirement)
            or not isinstance(requirement['definition'], dict)
            or set(requirement['definition']) != {'title', 'need', 'target'}
            or any(not isinstance(x, str) or not x.strip() for x in requirement['definition'].values())):
        raise ValueError('canonical improvement requirement hash/definition differs')
    return {'taskId': requirement['taskId'], 'definition': deepcopy(requirement['definition']),
        'requirementHash': binding['requirementHash']}


def build(binding, scope, target_statements):
    # Intentionally do not copy sources, report rows, submission paths, UIDs,
    # raw messages or receipt contents to any model-facing input.
    value = {'schemaVersion': SCHEMA, **safe_requirement(binding),
        'scope': deepcopy(scope), 'targetStatements': deepcopy(target_statements)}
    value['inputHash'] = _hash(value)
    return _shape(value)


def validate(value, *, binding=None, scope=None, question_id=None, target_statements=None):
    result = _shape(value)
    if scope is not None and value['scope'] != scope:
        raise ValueError('feedback task requirement belongs to another normal stage/input')
    if question_id is not None and value['scope']['questionId'] != question_id:
        raise ValueError('feedback task requirement belongs to another question')
    if target_statements is not None and value['targetStatements'] != target_statements:
        raise ValueError('feedback task source/live targets changed')
    if binding is not None and value != build(binding, scope or value['scope'], target_statements or value['targetStatements']):
        raise ValueError('feedback task requirement differs from current canonical definition/hash')
    return result

def verify_delivery(value, prompt, delivery, *, binding, scope):
    from tools.question_review_console.law_audit_verification import question_inputs_from_prompt
    validate(value, binding=binding, scope=scope)
    question = question_inputs_from_prompt(prompt)[scope['questionId']]
    expected = {'inputHash': value['inputHash'], 'requirementHash': value['requirementHash'],
        'promptHash': hashlib.sha256(prompt.encode()).hexdigest(),
        'inputFingerprint': scope['inputFingerprint'], 'projectedInputHash': scope['projectedInputHash']}
    if (question.get('feedbackTaskRequirement') != value or not isinstance(delivery, dict)
            or set(delivery) != set(expected) | {'threadId', 'turnId'}
            or any(delivery.get(key) != item for key,item in expected.items())
            or any(not isinstance(delivery.get(key), str) or not delivery[key] for key in ('threadId', 'turnId'))):
        raise ValueError('feedback task requirement was not delivered to the actual fixed native model turn')
    return deepcopy(delivery)
