"""Explicit SDK field updates with durable intent and read-only recovery.

This adapter never obtains credentials, selects a project, or validates human
permission. A future entry point must validate the referenced native approvals,
formal save, checkpoints and evaluation before calling it. No existing entry
point imports this module.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re

from google.cloud.firestore_v1 import LastUpdateOption
from google.protobuf.timestamp_pb2 import Timestamp
from tools.question_review_console.scoped_corrections import SnapshotCorrection


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def object_hash(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def timestamp(value):
    """Preserve seconds/nanoseconds; never compare a float or truncate precision."""
    if hasattr(value, 'timestamp_pb'):
        result = value.timestamp_pb()
    elif isinstance(value, str):
        result = Timestamp(); result.FromJsonString(value)
    elif hasattr(value, 'isoformat') and value.tzinfo is not None:
        result = Timestamp(); result.FromJsonString(value.isoformat())
    else:
        raise ValueError('exact timezone-aware timestamp required')
    return result


def version(value):
    t = timestamp(value)
    return {'seconds': t.seconds, 'nanos': t.nanos}


@dataclass(frozen=True)
class DeltaRequest:
    operation_id: str
    corrections: tuple
    evidence_references: dict

    def mapping(self):
        if not isinstance(self.operation_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', self.operation_id):
            raise ValueError('safe operation ID required')
        deltas = deepcopy(list(self.corrections))
        if len(deltas) != 11:
            raise ValueError('complete eleven-document scope required')
        ids = []
        for delta in deltas:
            typed = SnapshotCorrection.from_mapping(delta)
            timestamp(typed.update_time)
            ids.append(typed.question_id)
        if len(set(ids)) != 11:
            raise ValueError('unique complete document IDs required')
        evidence = deepcopy(self.evidence_references)
        required = {'formalSaveReceipt', 'checkpoints', 'evaluation', 'productionApproval'}
        if not isinstance(evidence, dict) or set(evidence) != required:
            raise ValueError('external approval/gate evidence references required')
        for entry in evidence.values():
            if (not isinstance(entry, dict) or set(entry) != {'reference', 'sha256'} or not isinstance(entry['reference'], str)
                    or not entry['reference'] or not isinstance(entry['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', entry['sha256'])):
                raise ValueError('evidence references are not permission booleans/tokens')
        result = {'schemaVersion': 'publication-delta-request/v1', 'operationId': self.operation_id,
            'corrections': deltas, 'evidenceReferences': evidence}
        canonical(result)
        return result


class IntentJournal:
    """Exclusive owner-only hash-chained events, flushed before SDK commit."""
    transitions = {'intent': {'committing', 'unknown'}, 'committing': {'response_recorded', 'unknown'},
        'response_recorded': {'completed', 'unknown'}, 'unknown': set(), 'completed': set()}

    def __init__(self, root):
        self.root = Path(root)
        if self.root.absolute() != self.root.resolve():
            raise ValueError('physical journal directory required')
        if self.root.exists():
            self._directory(self.root)
        else:
            self.root.mkdir(mode=0o700)
            self._sync(self.root.parent)

    @staticmethod
    def _directory(path):
        if path.is_symlink() or not path.is_dir() or path.stat().st_mode & 0o777 != 0o700 or path.stat().st_uid != os.getuid():
            raise ValueError('owner-only physical journal directory required')

    @staticmethod
    def _sync(directory):
        fd = os.open(directory, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)

    def path(self, operation_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', operation_id):
            raise ValueError('invalid journal operation ID')
        path = self.root / operation_id
        if path.absolute() != path.resolve():
            raise ValueError('journal symlink refused')
        return path

    def read(self, operation_id):
        directory = self.path(operation_id)
        if not directory.exists(): return None
        self._directory(directory)
        paths = sorted(directory.iterdir())
        if not paths: raise ValueError('incomplete journal refused')
        events, previous = [], None
        for number, path in enumerate(paths, 1):
            if (path.name != f'{number:04}.json' or path.is_symlink() or not path.is_file()
                    or path.stat().st_mode & 0o777 != 0o600 or path.stat().st_nlink != 1 or path.stat().st_uid != os.getuid()):
                raise ValueError('journal gap/partial/permissions refused')
            entry = json.loads(path.read_bytes())
            claimed = entry.pop('eventHash')
            if object_hash(entry) != claimed or entry['previousHash'] != previous or entry['sequence'] != number:
                raise ValueError('journal event integrity differs')
            if number == 1:
                if (entry['schemaVersion'] != 'publication-delta-journal/v1' or entry['state'] != 'intent'
                        or entry['data']['request']['operationId'] != operation_id
                        or object_hash(entry['data']['request']) != entry['requestHash']):
                    raise ValueError('journal intent binding differs')
            elif entry['state'] not in self.transitions[events[-1]['state']] or entry['requestHash'] != events[0]['requestHash']:
                raise ValueError('nonmonotonic or conflicting journal transition')
            entry['eventHash'] = claimed; events.append(entry); previous = claimed
        return events

    def _append(self, directory, request_hash, state, data, previous):
        number = len(previous) + 1
        entry = {'schemaVersion': 'publication-delta-journal/v1', 'sequence': number,
            'requestHash': request_hash, 'state': state, 'data': deepcopy(data),
            'previousHash': previous[-1]['eventHash'] if previous else None}
        entry['eventHash'] = object_hash(entry)
        path = directory / f'{number:04}.json'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(canonical(entry) + b'\n'); stream.flush(); os.fsync(stream.fileno())
            self._sync(directory)
        except BaseException:
            # A partial event is held for operator inspection, never auto-erased.
            raise
        return entry

    def begin(self, request):
        directory = self.path(request['operationId'])
        directory.mkdir(mode=0o700)  # exclusive: another process cannot begin it
        self._sync(self.root)
        return self._append(directory, object_hash(request), 'intent', {'request': request}, [])

    def append(self, operation_id, state, data):
        events = self.read(operation_id)
        if not events or state not in self.transitions[events[-1]['state']]:
            raise ValueError('journal transition refused')
        return self._append(self.path(operation_id), events[0]['requestHash'], state, data, events)


class PublicationDeltaGateway:
    def __init__(self, *, client, collection_path, journal):
        if client is None or not isinstance(journal, IntentJournal):
            raise ValueError('explicit client and durable journal required')
        if (not isinstance(collection_path, str) or not collection_path or any(not p for p in collection_path.split('/'))
                or len(collection_path.split('/')) % 2 != 1):
            raise ValueError('explicit collection path required')
        self.client, self.collection_path, self.journal = client, collection_path, journal

    def _mapping(self, request):
        mapping = request.mapping()
        mapping['target'] = {'database': self.client._database_string, 'collection': self.collection_path}
        return mapping

    def _read(self, request):
        deltas = request['corrections']
        refs = [self.client.document(self.collection_path + '/' + d['questionId']) for d in deltas]
        mask = sorted({field for d in deltas for field in d['fields']})
        snapshots = list(self.client.get_all(refs, field_paths=mask, retry=None))
        expected = {ref.path: ref.id for ref in refs}
        selected = {}
        for snapshot in snapshots:
            if snapshot.reference.path not in expected or snapshot.id != expected[snapshot.reference.path] or snapshot.id in selected:
                raise ValueError('masked read returned duplicate/different document')
            fields = snapshot.to_dict() if snapshot.exists else {}
            if snapshot.exists and not isinstance(fields, dict): raise ValueError('snapshot fields unavailable')
            selected[snapshot.id] = {'exists': snapshot.exists, 'version': version(snapshot.update_time) if snapshot.exists else None,
                'fields': {f: {'present': f in fields, 'value': deepcopy(fields.get(f))} for f in mask}}
        if set(selected) != {d['questionId'] for d in deltas}:
            raise ValueError('masked selected coverage differs')
        canonical(selected)
        return refs, selected

    @staticmethod
    def _matches(delta, snapshot, *, after):
        if snapshot['exists'] is not True: return False
        if not after and snapshot['version'] != version(delta['updateTime']): return False
        return all(snapshot['fields'][field] == {'present': True if after else change['beforePresent'],
            'value': change['after'] if after else change['before']} for field, change in delta['fields'].items())

    def reconcile(self, request):
        mapping = self._mapping(request)
        events = self.journal.read(request.operation_id)
        if not events or events[0]['requestHash'] != object_hash(mapping):
            raise ValueError('journal/request conflict')
        try:
            _, selected = self._read(mapping)
        except Exception:
            return {'status': 'unavailable', 'attributionConfirmed': False, 'retryAllowed': False}
        all_before = all(self._matches(d, selected[d['questionId']], after=False) for d in mapping['corrections'])
        all_after = all(self._matches(d, selected[d['questionId']], after=True) for d in mapping['corrections'])
        response = next((e['data'] for e in events if e['state'] == 'response_recorded'), None)
        versions_match = response is not None and all(selected[k]['version'] == v for k, v in response['writeVersions'].items())
        completed = events[-1]['state'] == 'completed'
        if all_after and completed and versions_match:
            return {'status': 'completed_readonly_retry', 'attributionConfirmed': True, 'retryAllowed': False,
                'receipt': events[-1]['data'], 'selectedHash': object_hash(selected)}
        state = 'all_after' if all_after else 'all_before' if all_before else 'mixed_or_conflict'
        return {'status': state, 'attributionConfirmed': bool(all_after and response and versions_match),
            'completionRecorded': completed, 'retryAllowed': False, 'selectedHash': object_hash(selected)}

    def execute(self, request, *, interruption_hook=None):
        mapping = self._mapping(request)
        prior = self.journal.read(request.operation_id)
        if prior is not None:
            if prior[0]['requestHash'] != object_hash(mapping): raise ValueError('operation ID/content conflict')
            return self.reconcile(request)  # every existing operation is read-only
        refs, before = self._read(mapping)
        if not all(self._matches(d, before[d['questionId']], after=False) for d in mapping['corrections']):
            raise ValueError('preflight before/presence/updateTime conflict')
        batch = self.client.batch()
        for ref, delta in zip(refs, mapping['corrections'], strict=True):
            batch.update(ref, {k: deepcopy(v['after']) for k, v in delta['fields'].items()}, option=LastUpdateOption(timestamp(delta['updateTime'])))
        self.journal.begin(mapping)  # persistence failures propagate with zero commit
        hook = interruption_hook or (lambda phase: None)
        try:
            self.journal.append(request.operation_id, 'committing', {'preflightHash': object_hash(before)})
            hook('before_commit')
            results = batch.commit(retry=None)
            if len(results) != len(refs): raise ValueError('SDK write result coverage differs')
            versions = {d['questionId']: version(result.update_time) for d, result in zip(mapping['corrections'], results, strict=True)}
            response = {'writeVersions': versions, 'commitTime': version(batch.commit_time)}
            self.journal.append(request.operation_id, 'response_recorded', response)
            hook('after_response')
            _, after = self._read(mapping)
            if not all(self._matches(d, after[d['questionId']], after=True) and after[d['questionId']]['version'] == versions[d['questionId']] for d in mapping['corrections']):
                raise ValueError('response-bound masked readback conflict')
            receipt = {'requestHash': object_hash(mapping), 'writeVersions': versions, 'commitTime': response['commitTime'],
                'selectedReadbackHash': object_hash(after), 'evidenceReferences': mapping['evidenceReferences'],
                'permissionValidationPerformed': False, 'humanApprovalCreated': False, 'checkpointCreated': False, 'evaluationPerformed': False}
            self.journal.append(request.operation_id, 'completed', receipt)
            return {'status': 'completed', 'attributionConfirmed': True, 'retryAllowed': False, 'receipt': receipt}
        except Exception as error:
            # Never retry a lost response or undo a commit automatically.
            try: self.journal.append(request.operation_id, 'unknown', {'reasonType': type(error).__name__})
            except Exception: pass  # durable committing/response event remains conservative
            return {'status': 'unknown', 'attributionConfirmed': False, 'retryAllowed': False, 'reasonType': type(error).__name__}
