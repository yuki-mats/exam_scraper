"""Freeze question-level source/choice-only observations, without save authority.

This is a read-only prerequisite for a future normal-stage/delivery adapter. It
does not turn combination choices into independent statements or approve the
current question type, answer, candidate, checkpoint or publication.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sqlite3

from scripts.common.question_identity import SourceIdentityBinding, load_source_record_inventory
from scripts.common.scoped_canonical_context import reader_dependencies
from tools.question_bank.feedback_task_requirements import task_requirement_binding
from tools.question_bank.feedback_legacy_review_context import (
    PRIVATE, MANIFEST, _bytes, _hash, _inside, _json, _object, _physical, _read, _ref, _unchanged,
)
from tools.question_review_console.feedback_task_input import safe_requirement

SCHEMA = 'feedback-question-level-read-set/v1'
RECEIPT = 'feedback-question-level-read-set-receipt/v1'
ALLOWED_FIELDS = frozenset({'qualificationId', 'examYear', 'originalQuestionId', 'isDeleted',
    'isChoiceOnly', 'questionType', 'questionBodyText', 'questionText', 'originalQuestionBodyText',
    'originalQuestionChoiceText', 'correctChoiceText', 'choiceTextList', 'explanationText',
    'suggestedQuestions', 'suggestedQuestionDetails', 'questionSetId', 'examSource', 'imageUrlList',
    'listGroupId'})
REQUIRED_FIELDS = frozenset({'qualificationId', 'examYear', 'originalQuestionId', 'isDeleted',
    'isChoiceOnly', 'questionType', 'originalQuestionChoiceText', 'originalQuestionBodyText'})
FLAGS = {k: False for k in ('machineReady', 'normalStageExecuted', 'questionTypeApproved',
    'answerApproved', 'candidateApproved', 'formalPatchSaved', 'publicationReady', 'FirestoreWritten')}


def _unit(value):
    _object(value, {'qualificationId', 'examYear', 'originalQuestionId'})
    if (type(value['examYear']) is not int or not 1900 <= value['examYear'] <= 2200
            or any(not isinstance(value[k], str) or not value[k].strip()
                or value[k] != value[k].strip() or '/' in value[k] or value[k] in {'.', '..'}
                for k in ('qualificationId', 'originalQuestionId'))):
        raise ValueError('exact qualification/year/original unit required')
    return value


def _production_reader(unit, fields):
    from google.cloud.firestore_v1.base_query import FieldFilter
    from tools.question_review_console.firestore_readback import FirestoreReadback
    from tools.question_bank.question_issue_report_store import _json_value
    database = FirestoreReadback()._database()
    query = database.collection('questions').where(
        filter=FieldFilter('originalQuestionId', '==', unit['originalQuestionId'])).select(fields)
    result = {}
    for snapshot in query.stream():
        values = _json_value(snapshot.to_dict() or {})
        if values.get('isDeleted') is True:
            continue
        if (not isinstance(values.get('qualificationId'), str)
                or type(values.get('examYear')) is not int):
            raise ValueError('ambiguous active unit membership')
        if any(values.get(k) != v for k, v in unit.items()):
            continue
        if snapshot.id in result:
            raise ValueError('duplicate document')
        result[snapshot.id] = {'questionId': snapshot.id, 'exists': snapshot.exists,
            'fields': values, 'updateTimeExact': snapshot.update_time.rfc3339()}
    return result


def _inputs(root, candidate_ref, connection):
    files = {}
    candidate = _ref(root, candidate_ref, files, private=True)
    if (candidate.get('schemaVersion') != 'feedback-improvement-content-candidate/v1'
            or any(candidate.get(k) is not False for k in
                ('approvalReady', 'formalPatchSaved', 'FirestoreWritten', 'publicationReady'))):
        raise ValueError('unapproved private content candidate required')
    unit = _unit(candidate['publicationUnit'])
    source = _object(candidate['source'], {'sourceFile', 'sourceSha256', 'binding'})
    path = _inside(root, source['sourceFile'])
    if (path.parent.name != '00_source' or path.parent.parent.parent.parent != root / 'output' / unit['qualificationId']
            or path.parent.parent.parent.name != 'questions_json'):
        raise ValueError('source outside qualification/group/00_source')
    source_data = _ref(root, {'ref': str(path), 'sha256': source['sourceSha256']}, files, decode=False)
    manifest = {}
    for line in _read(root, root / MANIFEST, files).splitlines():
        row = _object(_json(line), {'path', 'sha256'})
        if row['path'] in manifest:
            raise ValueError('duplicate preservation entry')
        manifest[row['path']] = row['sha256']
    if manifest.get(str(path.relative_to(root))) != _hash(source_data):
        raise ValueError('source differs from preservation manifest')
    # Pin every file the canonical inventory can consult, not only the selected file.
    payloads = {}
    for sibling_file in sorted(path.parent.glob('*.json')):
        data = _read(root, sibling_file, files)
        if manifest.get(str(sibling_file.relative_to(root))) != _hash(data):
            raise ValueError('source inventory file not preserved')
        payloads[sibling_file] = _json(data)
    binding = SourceIdentityBinding.from_mapping(source['binding'])
    if not binding.is_complete() or binding.as_mapping() != source['binding']:
        raise ValueError('exact source binding required')
    entries = load_source_record_inventory(path.parent, qualification=unit['qualificationId'],
        list_group_id=path.parent.parent.name)
    for entry in entries:
        payload = payloads.get(entry.path)
        records = payload.get('question_bodies') if isinstance(payload, dict) else payload
        if (not isinstance(records, list) or type(entry.record_index) is not int
                or not 0 <= entry.record_index < len(records)
                or entry.record != records[entry.record_index]):
            raise ValueError('canonical inventory differs from captured source bytes')
    matches = [entry for entry in entries if entry.identity.binding == binding and entry.path == path]
    if len(matches) != 1:
        raise ValueError('source binding must resolve exactly once')
    record = deepcopy(dict(matches[0].record))
    choices = record.get('choiceTextList')
    if (record.get('public_question_id') != unit['originalQuestionId'] or record.get('examYear') != unit['examYear']
            or record.get('questionType') not in {'flash_card', 'group_choice'}
            or not isinstance(record.get('questionBodyText'), str) or not record['questionBodyText']
            or not isinstance(choices, list) or not choices
            or any(not isinstance(x, str) or not x for x in choices) or len(set(choices)) != len(choices)):
        raise ValueError('one preserved question-level source with unique choices required')
    baseline = _ref(root, candidate['baseline'], files, private=True)
    if not isinstance(baseline, dict) or not baseline:
        raise ValueError('nonempty candidate baseline required')
    fields = sorted({k for snapshot in baseline.values() for k in snapshot['fields']})
    if not REQUIRED_FIELDS.issubset(fields) or not set(fields).issubset(ALLOWED_FIELDS):
        raise ValueError('safe complete read mask required')
    requirement = task_requirement_binding(connection, candidate['taskId'])
    task_ids = {x['question_id'] for x in requirement['requirement']['sources']}
    if not task_ids or not task_ids.issubset(set(baseline) | {unit['originalQuestionId']}):
        raise ValueError('task includes sources outside this unit')
    if not isinstance(candidate.get('primaryEvidence'), list) or not candidate['primaryEvidence']:
        raise ValueError('fixed primary evidence required')
    for evidence in candidate['primaryEvidence']:
        _ref(root, {k: evidence[k] for k in ('ref', 'sha256')}, files, private=True, decode=False)
    for dependency in reader_dependencies(root, [root / 'tools/question_bank/feedback_question_level_read_set.py']):
        _read(root, dependency, files)
    _unchanged(root, files)
    return candidate, record, baseline, fields, requirement, files


def _build(candidate, record, baseline, fields, requirement, snapshots):
    unit = candidate['publicationUnit']
    ids = sorted(snapshots)
    if ids != sorted(baseline) or ids != candidate['allObservedSiblingIds']:
        raise ValueError('complete source/candidate/live read set required')
    mapped = []
    for qid in ids:
        snapshot = snapshots[qid]
        _object(snapshot, {'questionId', 'exists', 'fields', 'updateTimeExact'})
        values = snapshot['fields']
        stamp = snapshot['updateTimeExact']
        if (snapshot['questionId'] != qid or snapshot['exists'] is not True
                or not isinstance(values, dict) or set(values) - set(fields)
                or values.get('isDeleted') is not False or type(values.get('isChoiceOnly')) is not bool
                or any(values.get(k) != v for k, v in unit.items())
                or values.get('questionType') != record['questionType']
                or values.get('originalQuestionBodyText') != record['questionBodyText']
                or not isinstance(stamp, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?Z', stamp)):
            raise ValueError('exact masked existing question-level snapshot required')
        datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if snapshot != baseline[qid]:
            raise ValueError('candidate baseline differs from fresh read set')
        choice = values.get('originalQuestionChoiceText')
        if choice not in record['choiceTextList']:
            raise ValueError('exact source/live choice correspondence required')
        mapped.append({'questionId': qid, 'sourceChoiceText': choice,
            'sourceChoiceIndex': record['choiceTextList'].index(choice), 'isChoiceOnly': values['isChoiceOnly']})
    if {m['sourceChoiceText'] for m in mapped} != set(record['choiceTextList']) or len(mapped) != len(record['choiceTextList']):
        raise ValueError('all unique source choices must be observed exactly once')
    if not any(m['isChoiceOnly'] is False for m in mapped):
        raise ValueError('active study document required')
    updates = candidate['proposedUpdates']
    if not isinstance(updates, list) or not updates or len({u['questionId'] for u in updates}) != len(updates):
        raise ValueError('unique nonempty proposed write set required')
    for update in updates:
        qid = update['questionId']
        if qid not in snapshots or update['updateTimeExact'] != snapshots[qid]['updateTimeExact']:
            raise ValueError('candidate precondition differs')
        diffs = update['fieldDiffs']
        if not isinstance(diffs, list) or not diffs or len({d['field'] for d in diffs}) != len(diffs):
            raise ValueError('unique nonempty field diffs required')
        for diff in diffs:
            _object(diff, {'field', 'beforePresent', 'before', 'after'})
            field = diff['field']; values = snapshots[qid]['fields']
            if (field not in {'explanationText', 'suggestedQuestions', 'suggestedQuestionDetails'}
                    or type(diff['beforePresent']) is not bool or diff['beforePresent'] != (field in values)
                    or diff['before'] != values.get(field) or diff['before'] == diff['after']):
                raise ValueError('local content-only before/after differs')
    return {'schemaVersion': SCHEMA, 'task': safe_requirement(requirement),
        'sourceBinding': candidate['source'], 'publicationUnit': unit, 'readFields': fields,
        'sourceChoiceMappings': mapped, 'readSet': snapshots,
        'proposedWriteDocumentIds': sorted(u['questionId'] for u in updates),
        'remainingGates': ['通常01の独立集約分解・形式判断', '正式保存・通常全工程・独立評価',
            '限定公開adapter・公開承認・CAS・対象内外live readback'], **FLAGS}


def inspect(root, destination, *, connection):
    root = _physical(root); destination = _inside(root, destination, private=True)
    context = _json(_read(root, destination / 'context.json', {}, private=True))
    receipt = _json(_read(root, destination / 'receipt.json', {}, private=True))
    _object(receipt, {'schemaVersion', 'contextSha256', 'candidate', 'inputs', 'observedAt', 'reader', *FLAGS})
    if (receipt['schemaVersion'] != RECEIPT or any(receipt[k] is not False for k in FLAGS)
            or receipt['reader'] not in {'production-fresh-read', 'injected-test-reader'}
            or not isinstance(receipt['observedAt'], str)):
        raise ValueError('read-only receipt required')
    datetime.fromisoformat(receipt['observedAt'])
    candidate, record, baseline, fields, requirement, files = _inputs(root, receipt['candidate'], connection)
    expected = _build(candidate, record, baseline, fields, requirement, context['readSet'])
    if context != expected or receipt['contextSha256'] != _hash(_bytes(context)) or receipt['inputs'] != files:
        raise ValueError('frozen read-set/input drift')
    _unchanged(root, files)
    return receipt


def prepare(root, candidate_ref, destination, *, connection, _reader=None):
    root = _physical(root); destination = _inside(root, destination, private=True)
    if destination == root / PRIVATE or destination in Path(candidate_ref['ref']).parents:
        raise ValueError('dedicated private output directory required')
    candidate, record, baseline, fields, requirement, files = _inputs(root, candidate_ref, connection)
    snapshots = deepcopy((_production_reader if _reader is None else _reader)(candidate['publicationUnit'], fields))
    context = _build(candidate, record, baseline, fields, requirement, snapshots)
    _unchanged(root, files)
    if destination.exists():
        receipt = inspect(root, destination, connection=connection)
        if receipt['candidate'] != candidate_ref or _json(_read(root, destination / 'context.json', {}, private=True)) != context:
            raise ValueError('same fixed candidate/read-set retransmission only')
        if receipt['reader'] != ('production-fresh-read' if _reader is None else 'injected-test-reader'):
            raise ValueError('reader provenance changed')
        return receipt
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _inside(root, destination, private=True)
    destination.mkdir(mode=0o700)
    receipt = {'schemaVersion': RECEIPT, 'contextSha256': _hash(_bytes(context)),
        'candidate': candidate_ref, 'inputs': files, 'observedAt': datetime.now(timezone.utc).isoformat(),
        'reader': 'production-fresh-read' if _reader is None else 'injected-test-reader', **FLAGS}
    for name, content in [('context.json', context), ('receipt.json', receipt)]:
        fd = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(_bytes(content)); stream.flush(); os.fsync(stream.fileno())
    return inspect(root, destination, connection=connection)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'inspect'])
    parser.add_argument('--repo-root', type=Path, required=True)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--candidate-sha256')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    with closing(sqlite3.connect(args.db.expanduser().resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        if args.action == 'prepare':
            if args.candidate is None or args.candidate_sha256 is None:
                parser.error('prepare requires fixed candidate path and SHA256')
            result = prepare(args.repo_root, {'ref': str(args.candidate.absolute()),
                'sha256': args.candidate_sha256}, args.output, connection=connection)
        else:
            if args.candidate is not None or args.candidate_sha256 is not None:
                parser.error('inspect uses the recorded candidate')
            result = inspect(args.repo_root, args.output, connection=connection)
    print(_bytes({'schemaVersion': result['schemaVersion'], 'contextSha256': result['contextSha256'], **FLAGS}).decode(), end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
