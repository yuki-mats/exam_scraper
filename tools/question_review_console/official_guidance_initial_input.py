"""Source-bound acquisition input for the first normal institutional 02b turn.

This local artifact selects evidence. It never adopts applicability, modifies a
question patch, records a completed work version, or authorizes publication.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re

from tools.question_review_console.official_guidance_contract import (
    load_prepared_evidence, prepare_evidence, source_scope,
)
from tools.question_review_console.review_store import atomic_write

SCHEMA = 'official-guidance-initial-input/v1'


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _segment(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('safe qualification/group/question identity required')
    return value


def input_path(root, qualification, target):
    root = Path(root).resolve()
    path = (root / 'output/question_review_console' / _segment(qualification)
        / _segment(target.get('listGroupId')) / 'official_guidance_inputs'
        / (_segment(target.get('id') or target.get('uiQuestionId')) + '.json'))
    if path.resolve() != path:
        raise ValueError('physical private initial-input path required')
    return path


def _read(path):
    raw = path.read_bytes()
    value = json.loads(raw)
    if (not isinstance(value, dict) or set(value) != {
            'schemaVersion', 'scope', 'request', 'evidenceRef', 'preparedAt'}
            or value['schemaVersion'] != SCHEMA):
        raise ValueError('dedicated initial institutional input required')
    return raw, value


def register_initial_input(root, qualification, target, record, request, *,
                           replace_if_sha=None, resolver=None):
    """Acquire through the normal provider and publish a local input atomically."""
    root = Path(root).resolve()
    path = input_path(root, qualification, target)
    scope = source_scope(root, qualification, target)
    if replace_if_sha is not None and not re.fullmatch(r'[a-f0-9]{64}', replace_if_sha):
        raise ValueError('expected prior initial-input SHA-256 required')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    lock_path = path.parent / '.prepare.lock'
    if lock_path.is_symlink():
        raise ValueError('physical initial-input lock required')
    with lock_path.open('a+b') as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.is_symlink():
            raise ValueError('physical initial-input file required')
        before = path.read_bytes() if path.exists() else None
        if (before is None and replace_if_sha is not None) or (
                before is not None and _hash(before) != replace_if_sha):
            raise ValueError('initial input exists or prior hash changed; inspect before replacement')
        if before is not None:
            _, old = _read(path)
            if any(old['scope'].get(key) != scope[key] for key in (
                    'qualification', 'listGroupId', 'questionId', 'sourceBinding')):
                raise ValueError('prior initial input belongs to another source')
        prepared = prepare_evidence(root, request, record=record, scope=scope, resolver=resolver)
        load_prepared_evidence(root, request, prepared['evidenceRef'],
            record=record, scope=scope, resolver=resolver)
        if source_scope(root, qualification, target) != scope:
            raise ValueError('preserved source changed during acquisition')
        if path.is_symlink() or (path.read_bytes() if path.exists() else None) != before:
            raise ValueError('initial input changed during acquisition')
        if before is not None:
            from tools.question_review_console.official_guidance_contract import _immutable_bytes
            history = path.parent / 'history'
            if history.resolve() != history:
                raise ValueError('physical initial-input history required')
            history.mkdir(exist_ok=True, mode=0o700)
            _immutable_bytes(history / (_hash(before) + '.json'), before)
        value = {'schemaVersion': SCHEMA, 'scope': scope, 'request': prepared['request'],
            'evidenceRef': prepared['evidenceRef'],
            'preparedAt': prepared['receipt']['retrievalCompletedAt']}
        atomic_write(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
        os.chmod(path, 0o600)
        return {'path': str(path), 'sha256': _hash(path.read_bytes()),
            'evidenceRef': prepared['evidenceRef'], 'applicabilityStatus': 'unreviewed'}


def load_initial_input(root, qualification, target, record, *, current_as_of, resolver=None):
    path = input_path(root, qualification, target)
    if not path.exists():
        return None
    if path.is_symlink():
        raise ValueError('physical initial-input file required')
    _, value = _read(path)
    scope = source_scope(root, qualification, target)
    if value['scope'] != scope or value['request'].get('currentAsOf') != current_as_of:
        raise ValueError('initial institutional input source or reference date changed')
    return load_prepared_evidence(root, value['request'], value['evidenceRef'],
        record=record, scope=scope, resolver=resolver)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--qualification', required=True)
    parser.add_argument('--list-group-id', required=True)
    parser.add_argument('--source-record-ref', required=True)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--replace-if-sha')
    args = parser.parse_args(argv)
    from tools.question_review_console.inventory import QuestionInventory
    inventory = QuestionInventory(args.repo_root)
    matches = [question for question in inventory.group(args.qualification, args.list_group_id)['questions']
        if question['sourceRecordRef'] == args.source_record_ref]
    if len(matches) != 1:
        raise ValueError('exactly one preserved source question required')
    target = matches[0]
    result = register_initial_input(args.repo_root, args.qualification, target, target['projected'],
        json.loads(args.request.read_text()), replace_if_sha=args.replace_if_sha)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
