"""Private, source-bound institutional evidence used by normal maintenance.

The model selects applicability; hashes prove delivery and acquisition only.
No value from this contract is projected to a Firestore document.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

from scripts.common.question_identity import SourceIdentityBinding, load_source_record_inventory
from tools.question_review_console.official_guidance_evidence import OfficialGuidanceEvidenceResolver, PROVIDERS

FIELD = 'officialGuidanceReferences'
PROOF_DIRECTORY = 'output/user_feedback_response_system/execution/official-guidance-proofs'

def _hash(data):
    return hashlib.sha256(data).hexdigest()

def _object(properties, required=None):
    return {'type': 'object', 'additionalProperties': False, 'properties': properties,
        'required': list(properties) if required is None else required}

_text = {'type': 'string', 'minLength': 1}
_pages = {'type': 'array', 'minItems': 1, 'maxItems': 32,
    'items': {'type': 'integer', 'minimum': 1}}
REQUEST_RULE = _object({
    'schemaVersion': {'type': 'string', 'enum': ['official-guidance-request/v1']},
    'provider': {'type': 'string', 'enum': list(PROVIDERS)},
    'currentAsOf': _text, 'examAsOf': {'type': ['string', 'null']},
    'currentPages': _pages,
    'historicalDocuments': {'type': 'array', 'maxItems': 6, 'items': _object({
        'sourceUrl': _text, 'revisionLabel': _text, 'pages': _pages})},
    'choiceBindings': {'type': 'array', 'minItems': 1, 'items': _object({
        'choiceIndex': {'type': 'integer', 'minimum': 0}, 'currentPages': _pages,
        'historicalSelections': {'type': 'array', 'items': _object({
            'documentIndex': {'type': 'integer', 'minimum': 0}, 'pages': _pages})}})},
})
FIELD_RULE = _object({'request': REQUEST_RULE,
    'evidenceRef': _object({'ref': _text, 'sha256': _text}),
    'applicabilityByChoice': {'type': 'array', 'minItems': 1, 'items': _object({
        'choiceIndex': {'type': 'integer', 'minimum': 0},
        'currentBasis': _text, 'examTimeBasis': _text})}})
FIELD_RULE['description'] = ('行政の公式制度資料を採用する場合だけ指定する内部field。'
    'serverが渡したrequestとevidenceRefを保持し、各肢の規約条項・例外・適用時点を独立に説明する。'
    '取得証明は内容適合や時点適用の承認ではない。e-Gov法令IDを捏造しない。')

def source_scope(root, qualification, target):
    root = Path(root).resolve()
    group = str(target.get('listGroupId') or '')
    binding = SourceIdentityBinding.from_mapping(target)
    entries = load_source_record_inventory(root / 'output' / qualification / 'questions_json' / group / '00_source',
        qualification=qualification, list_group_id=group)
    matches = [entry for entry in entries if entry.identity.binding == binding]
    if len(matches) != 1 or not binding.is_complete():
        raise ValueError('official guidance requires one preserved source binding')
    return {'qualification': qualification, 'listGroupId': group,
        'questionId': str(target.get('id') or target.get('uiQuestionId') or ''),
        'sourceBinding': binding.as_mapping(), 'sourceSha256': _hash(matches[0].path.read_bytes())}

def _immutable_bytes(path, raw):
    # Publish a complete fsynced temporary file exclusively. A crash must not
    # create a partially written final immutable proof.
    from tools.question_bank.feedback_verified_save import _exclusive
    try:
        _exclusive(path, raw)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != raw:
            raise ValueError('immutable official guidance proof conflict')

def store_evidence(root, receipt):
    root = Path(root).resolve()
    raw = (json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    path = root / PROOF_DIRECTORY / (receipt['evidenceHash'] + '.json')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.resolve() != path.parent:
        raise ValueError('private guidance cache cannot traverse a symlink')
    _immutable_bytes(path, raw)
    return {'ref': str(path), 'sha256': _hash(raw)}

def prepare_evidence(root, request, *, record, scope, resolver=None):
    resolver = resolver or OfficialGuidanceEvidenceResolver(root)
    receipt = resolver.resolve(request, record=record, scope=scope)
    return {'request': copy.deepcopy(request), 'evidenceRef': store_evidence(root, receipt), 'receipt': receipt}

def verify_delivery(inputs, prompt, delivery, *, input_fingerprint, projected_input_hash):
    """Replay the actual normal primary prompt, not a copied evidence label."""
    from tools.question_review_console.law_audit_verification import question_inputs_from_prompt
    from tools.question_review_console.projection import sha256_json
    actual = {qid: value['officialGuidanceEvidence'] for qid, value in question_inputs_from_prompt(prompt).items()
        if value.get('officialGuidanceEvidence') is not None}
    expected = {'inputsHash': sha256_json(inputs), 'promptHash': _hash(prompt.encode()),
        'inputFingerprint': input_fingerprint, 'projectedInputHash': projected_input_hash}
    if (not inputs or actual != inputs or not isinstance(delivery, dict)
            or set(delivery) != set(expected) | {'threadId', 'turnId'}
            or any(delivery.get(key) != value for key, value in expected.items())
            or any(not isinstance(delivery.get(key), str) or not delivery[key] for key in ('threadId', 'turnId'))):
        raise ValueError('actual official guidance prompt/input/native delivery differs')
    return copy.deepcopy(delivery)

def validate_reference(root, value, *, record=None, scope=None, refresh=False, resolver=None):
    """Read actual private proof and validate raw cached sources, never hash alone."""
    root = Path(root).resolve()
    if not isinstance(value, dict) or set(value) != {'request', 'evidenceRef', 'applicabilityByChoice'}:
        raise ValueError('dedicated official guidance reference required')
    ref = value['evidenceRef']
    if not isinstance(ref, dict) or set(ref) != {'ref', 'sha256'}:
        raise ValueError('official guidance proof reference required')
    path = root / str(ref['ref'])
    if (not path.is_file() or path.is_symlink() or path.resolve() != path
            or path.parent != root / PROOF_DIRECTORY):
        raise ValueError('official guidance proof outside private immutable cache')
    raw = path.read_bytes()
    if _hash(raw) != ref['sha256']:
        raise ValueError('official guidance proof bytes changed')
    receipt = json.loads(raw)
    if path.name != str(receipt.get('evidenceHash')) + '.json':
        raise ValueError('official guidance proof name/hash differs')
    actual_scope = receipt['scope']
    target = {**actual_scope['sourceBinding'], 'id': actual_scope['questionId'],
        'listGroupId': actual_scope['listGroupId']}
    expected_scope = source_scope(root, actual_scope['qualification'], target)
    if actual_scope != expected_scope or (scope is not None and actual_scope != scope):
        raise ValueError('official guidance preserved source scope changed')
    if record is not None:
        if record.get('isLawRelated') is False or record.get('lawGroundedExplanationNotNeeded') is True:
            raise ValueError('non-institutional classification must explicitly remove stale guidance references')
        record_binding = SourceIdentityBinding.from_mapping(record)
        if record_binding.is_complete() and record_binding.as_mapping() != actual_scope['sourceBinding']:
            raise ValueError('official guidance reference belongs to another source record')
    if record is None:
        # Mechanical sidecar checks recover the exact source; source-bound
        # prepared delivery separately checks the projected full statement.
        entries = load_source_record_inventory(root / 'output' / actual_scope['qualification'] / 'questions_json'
            / actual_scope['listGroupId'] / '00_source', qualification=actual_scope['qualification'],
            list_group_id=actual_scope['listGroupId'])
        record = next(entry.record for entry in entries
            if entry.identity.binding.as_mapping() == actual_scope['sourceBinding'])
    applicability = value['applicabilityByChoice']
    if (not isinstance(applicability, list) or len(applicability) != len(record.get('choiceTextList') or [])
            or any(not isinstance(item, dict) or set(item) != {'choiceIndex', 'currentBasis', 'examTimeBasis'}
                or item['choiceIndex'] != index or any(not isinstance(item[key], str) or not item[key].strip()
                    for key in ('currentBasis', 'examTimeBasis')) for index, item in enumerate(applicability))):
        raise ValueError('every full choice requires independent current/exam applicability')
    resolver = resolver or OfficialGuidanceEvidenceResolver(root)
    resolver.validate(receipt, request=value['request'], record=record, scope=actual_scope, refresh=refresh)
    if refresh:
        save_refresh(root, resolver.last_refresh_receipt)
    return receipt

def save_refresh(root, wrapper):
    if not isinstance(wrapper, dict) or wrapper.get('schemaVersion') != 'official-guidance-refresh/v1':
        raise ValueError('actual official guidance refresh receipt required')
    raw = (json.dumps(wrapper, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    directory = Path(root).resolve() / PROOF_DIRECTORY / 'refreshes'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.resolve() != directory:
        raise ValueError('private guidance refresh cache cannot traverse a symlink')
    path = directory / (wrapper['validationHash'] + '.json')
    _immutable_bytes(path, raw)
    return {'ref': str(path), 'sha256': _hash(raw)}

def refresh_reference(root, value, *, record=None, scope=None):
    resolver = OfficialGuidanceEvidenceResolver(root)
    receipt = validate_reference(root, value, record=record, scope=scope)
    # validate_reference already authenticated preserved record/scope; refresh
    # revalidates the pinned bytes and performs real GETs before it returns.
    if record is None:
        raise ValueError('fresh publication requires explicit full record')
    wrapper = resolver.refresh(receipt, request=value['request'], record=record,
        scope=scope or receipt['scope'])
    return {'receipt': wrapper, 'receiptRef': save_refresh(root, wrapper)}

def has_guidance_basis(record, *, root=None, allow_public_variant=False):
    if FIELD not in record:
        return False
    root = root or Path(__file__).resolve().parents[2]
    try:
        protected = record if record.get('choiceTextList') else None
        if allow_public_variant and record.get('originalQuestionBodyText'):
            # Only the source-TF display body changes. Authenticate the retained
            # complete original body and unchanged full choice list explicitly.
            # Normal saves and refreshes never use this display-only adapter.
            protected = {**record, 'questionBodyText': record['originalQuestionBodyText']}
        validate_reference(root, record[FIELD], record=protected)
    except (ValueError, OSError, KeyError, TypeError, StopIteration):
        return False
    return True
