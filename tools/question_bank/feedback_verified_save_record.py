"""Append-only local proof of a verified formal save; never publication.

Heavy native/PDF verification runs before the ledger write lock. The transaction
then compares every fixed file, native session and source/authority row again.
Native session guards are temporary: later unrelated chat activity must not
change the immutable record of the already verified save.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

SCHEMA = 'feedback-verified-formal-save-record/v1'
TABLE = 'verified_formal_save_records'
PRIVATE = Path('output/user_feedback_response_system/execution')
DEFAULT_DB = Path.home() / 'Library/Application Support/Repaso/feedback-ops/ledger.sqlite3'
KINDS = ('improvement', 'maintenance')
WORK_STATES = frozenset({'open', 'in_progress', 'review_pending', 'blocked'})
TRIGGERS = {
    'verified_formal_save_records_no_update':
        "CREATE TRIGGER verified_formal_save_records_no_update BEFORE UPDATE ON verified_formal_save_records BEGIN SELECT RAISE(ABORT, 'formal save records are append-only'); END",
    'verified_formal_save_records_no_delete':
        "CREATE TRIGGER verified_formal_save_records_no_delete BEFORE DELETE ON verified_formal_save_records BEGIN SELECT RAISE(ABORT, 'formal save records are append-only'); END",
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _physical(path):
    path = Path(path).absolute()
    if path != path.resolve() or path.is_symlink():
        raise ValueError('physical ledger/root/reference required')
    return path


def _file_hash(path):
    path = _physical(path)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _requirement(connection, task_id):
    from tools.question_bank.feedback_task_requirements import task_requirement_binding
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        return task_requirement_binding(cursor, task_id)
    finally:
        cursor.close()


def _task_state(connection, task_id):
    row = connection.execute('SELECT status FROM improvement_tasks WHERE task_id=?', (task_id,)).fetchone()
    if row is None or row[0] not in WORK_STATES:
        raise ValueError('current improvement work authority required')
    return row[0]


def _authority(connection, kind, task_id):
    if kind != 'maintenance':
        return None
    row = connection.execute('SELECT * FROM maintenance_tasks WHERE task_id=?', (task_id,)).fetchall()
    history = connection.execute(
        'SELECT * FROM maintenance_task_history WHERE task_id=? ORDER BY id', (task_id,)).fetchall()
    if len(row) != 1 or len(history) != 1:
        raise ValueError('one immutable maintenance authority/history required')
    return {'row': [tuple(r) for r in row], 'history': [tuple(r) for r in history]}


def _ledger(connection):
    if connection.in_transaction:
        raise ValueError('independent formal-save record transaction required')
    rows = connection.execute('PRAGMA database_list').fetchall()
    main = next((r[2] for r in rows if r[1] == 'main'), '')
    if not main:
        raise ValueError('existing file ledger required')
    path = _physical(main)
    info = path.stat()
    return path, (info.st_dev, info.st_ino)


def _session_paths(value):
    if isinstance(value, dict):
        if 'sessionPath' in value:
            path = value['sessionPath']
            if not isinstance(path, str) or not Path(path).is_absolute():
                raise ValueError('absolute native session reference required')
            yield _physical(path)
        for item in value.values():
            yield from _session_paths(item)
    elif isinstance(value, list):
        for item in value:
            yield from _session_paths(item)


def _prepare(connection, root, plan_path):
    """Return a body-free payload plus ephemeral guards after real load_saved."""
    from tools.question_bank import feedback_verified_save as saver
    root, path = _physical(root), _physical(plan_path)
    if not path.is_relative_to(root / PRIVATE):
        raise ValueError('saved plan outside private execution root')
    files = {}

    def capture(file, expected=None):
        file = _physical(file)
        digest = _file_hash(file)
        if expected is not None and digest != expected:
            raise ValueError('formal-save evidence bytes differ')
        if str(file) in files and files[str(file)] != digest:
            raise ValueError('formal-save evidence changed during verification')
        files[str(file)] = digest
        return {'ref': str(file), 'sha256': digest}

    def read(file):
        ref = capture(file)
        value = json.loads(Path(ref['ref']).read_bytes())
        if not isinstance(value, dict) or _file_hash(ref['ref']) != ref['sha256']:
            raise ValueError('fixed evidence JSON object required')
        return ref, value

    plan_ref, plan = read(path)
    receipt_ref, receipt = read(path.parent / 'save-receipt.json')
    if (plan.get('schemaVersion') not in {saver.SCHEMA, saver.MAINTENANCE_SCHEMA}
            or not isinstance(plan.get('inputs'), dict) or not plan['inputs']):
        raise ValueError('supported saved plan with fixed inputs required')
    improvement = plan['taskId']
    kind = 'maintenance' if plan['schemaVersion'] == saver.MAINTENANCE_SCHEMA else 'improvement'
    binding = plan.get('maintenanceBinding')
    task_id = binding['taskId'] if kind == 'maintenance' and isinstance(binding, dict) else improvement
    if any(not isinstance(i, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]*', i)
           for i in (task_id, improvement)):
        raise ValueError('unambiguous formal task identity required')
    requirement = _requirement(connection, improvement)
    if (requirement != plan.get('taskRequirement')
            or any(s['review_status'] != 'promoted' for s in requirement['requirement']['sources'])):
        raise ValueError('current promoted source requirement differs')
    task_state = _task_state(connection, improvement)
    authority = _authority(connection, kind, task_id)
    for file, digest in plan['inputs'].items():
        if (not isinstance(file, str) or not Path(file).is_absolute()
                or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)):
            raise ValueError('exact saved-plan input hash required')
        capture(file, digest)
    artifacts = {
        'plan': {**plan_ref, 'planHash': plan['planHash']},
        'saveReceipt': receipt_ref,
        'before': capture(plan['beforeRef'], plan['beforeSha256']),
        'candidate': capture(plan['candidateRef'], plan['candidateSha256']),
        'formal': capture(root / plan['formalPath'], plan['candidateSha256']),
        'original': capture(receipt['originalRef'], plan['beforeSha256']),
        'saveIntent': capture(Path(receipt['originalRef']).parent / 'intent.json'),
    }
    native_records = [receipt]
    laws = []
    if plan.get('lawReviewRequired') is True:
        for stage in saver.LAW_STAGES:
            ref, evidence = read(path.parent / f'law-{stage}-approval.json')
            laws.append({'stage': stage, **ref})
            native_records.append(evidence)
    if kind == 'maintenance':
        _, evidence = read(binding['auditRef']['ref'])
        native_records.append(evidence)
    # Full session hashes are ephemeral CAS guards. Never persist raw sessions
    # or their evolving whole-file hashes into the immutable save record.
    sessions = {str(p): _file_hash(p) for record in native_records for p in _session_paths(record)}
    for implementation in (Path(__file__), Path(__file__).with_name('feedback_daily.py')):
        capture(implementation)
    verified = saver.load_saved(root, path, path.parent / 'save-receipt.json', connection=connection)
    if verified != receipt:
        raise ValueError('verified saved receipt changed')
    payload = {
        'schemaVersion': SCHEMA, 'taskKind': kind, 'taskId': task_id,
        'improvementTaskId': improvement, 'requirementHash': requirement['requirementHash'],
        'candidateSha256': plan['candidateSha256'], 'state': 'formally_saved',
        'FirestoreWritten': False, 'publicationCompleted': False,
        'artifacts': artifacts, 'lawReviewRefs': laws,
        'maintenanceBindingSha256': _hash(binding) if kind == 'maintenance' else None,
        'approvalEvidenceSha256': _hash(receipt['approvalEvidence']),
        'approvalBindingSha256': _hash(receipt['approvalBinding']),
        'inputFiles': dict(sorted(files.items())),
    }
    proof = {'payload': payload, 'sessions': sessions, 'requirement': requirement,
             'taskState': task_state, 'authority': authority}
    _cas(connection, proof)
    return proof


def _cas(connection, proof):
    payload = proof['payload']
    if (_requirement(connection, payload['improvementTaskId']) != proof['requirement']
            or _task_state(connection, payload['improvementTaskId']) != proof['taskState']
            or _authority(connection, payload['taskKind'], payload['taskId']) != proof['authority']):
        raise ValueError('task/source/maintenance authority changed during recording')
    for file, digest in {**payload['inputFiles'], **proof['sessions']}.items():
        if _file_hash(file) != digest:
            raise ValueError('formal-save/native evidence changed during recording')


def _exists(connection):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone() is not None


def _tables(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS verified_formal_save_records (
        task_kind TEXT NOT NULL CHECK(task_kind IN ('improvement','maintenance')),
        task_id TEXT NOT NULL, improvement_task_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state='formally_saved'), plan_ref TEXT NOT NULL UNIQUE,
        record_json TEXT NOT NULL, record_hash TEXT NOT NULL,
        recorded_at TEXT NOT NULL, event_hash TEXT NOT NULL,
        PRIMARY KEY(task_kind,task_id))""")
    for sql in TRIGGERS.values():
        connection.execute(sql.replace('CREATE TRIGGER ', 'CREATE TRIGGER IF NOT EXISTS ', 1))


def _stored(connection, kind, task_id):
    cursor = connection.execute('SELECT * FROM verified_formal_save_records WHERE task_kind=? AND task_id=?', (kind, task_id))
    value = cursor.fetchone()
    if value is None:
        return None
    row = dict(zip((d[0] for d in cursor.description), value))
    payload = json.loads(row['record_json'])
    if (not isinstance(payload, dict) or payload.get('schemaVersion') != SCHEMA
            or payload.get('taskKind') != kind or payload.get('taskId') != task_id
            or payload.get('improvementTaskId') != row['improvement_task_id']
            or payload.get('state') != row['state'] or row['state'] != 'formally_saved'
            or payload.get('FirestoreWritten') is not False or payload.get('publicationCompleted') is not False
            or payload.get('artifacts', {}).get('plan', {}).get('ref') != row['plan_ref']
            or _hash(payload) != row['record_hash']
            or _hash({'recordSha256': row['record_hash'], 'recordedAt': row['recorded_at']}) != row['event_hash']):
        raise ValueError('immutable formal-save record differs')
    for name, expected in TRIGGERS.items():
        trigger = connection.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,)).fetchone()
        if trigger is None or trigger[0] != expected:
            raise ValueError('formal-save append-only trigger differs')
    return {**payload, 'recordSha256': row['record_hash'], 'recordedAt': row['recorded_at']}


def _same(stored, payload):
    if stored is None or {k: v for k, v in stored.items() if k not in {'recordSha256', 'recordedAt'}} != payload:
        raise ValueError('formal-save record conflicts with current saved evidence')
    return stored


def record(connection, *, root, plan_path, failure_hook=None):
    ledger, identity = _ledger(connection)
    proof = _prepare(connection, root, plan_path)
    payload = proof['payload']
    if failure_hook:
        failure_hook('after_verification')
    existing = _stored(connection, payload['taskKind'], payload['taskId']) if _exists(connection) else None
    if existing is None:
        from tools.question_bank.feedback_daily import _backup_db
        _backup_db(connection, ledger)
    try:
        connection.execute('BEGIN IMMEDIATE')
        _tables(connection)
        current = _stored(connection, payload['taskKind'], payload['taskId'])
        _cas(connection, proof)
        if (ledger.stat().st_dev, ledger.stat().st_ino) != identity:
            raise ValueError('ledger file replaced during recording')
        if current is not None:
            result = _same(current, payload)
            connection.rollback()
            return result
        if failure_hook:
            failure_hook('before_insert')
        _cas(connection, proof)
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        digest = _hash(payload)
        connection.execute('INSERT INTO verified_formal_save_records VALUES (?,?,?,?,?,?,?,?,?)',
            (payload['taskKind'], payload['taskId'], payload['improvementTaskId'], 'formally_saved',
             payload['artifacts']['plan']['ref'], _json(payload), digest, now,
             _hash({'recordSha256': digest, 'recordedAt': now})))
        if failure_hook:
            failure_hook('after_insert')
        result = _same(_stored(connection, payload['taskKind'], payload['taskId']), payload)
        if failure_hook:
            failure_hook('before_commit')
        _cas(connection, proof)
        if (ledger.stat().st_dev, ledger.stat().st_ino) != identity:
            raise ValueError('ledger file replaced during recording')
        connection.commit()
        return result
    except BaseException:
        connection.rollback()
        raise


def inspect(connection, *, root, task_id, task_kind=None):
    ledger, identity = _ledger(connection)
    if task_kind is not None and task_kind not in KINDS:
        raise ValueError('unsupported formal-save task kind')
    if not _exists(connection):
        raise ValueError('formal-save record not found')
    rows = connection.execute('SELECT task_kind FROM verified_formal_save_records WHERE task_id=? AND (? IS NULL OR task_kind=?)',
                              (task_id, task_kind, task_kind)).fetchall()
    if len(rows) != 1:
        raise ValueError('one unambiguous formal-save record required')
    stored = _stored(connection, rows[0][0], task_id)
    proof = _prepare(connection, root, stored['artifacts']['plan']['ref'])
    # Heavy validation above holds no read/write transaction. The final record
    # and authority reads share one short read-only snapshot; files and native
    # sessions are rechecked after the record reread, immediately before return.
    query_only = connection.execute('PRAGMA query_only').fetchone()[0]
    started = False
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        started = True
        current = _stored(connection, rows[0][0], task_id)
        if current != stored:
            raise ValueError('formal-save record changed during inspection')
        _cas(connection, proof)
        if (ledger.stat().st_dev, ledger.stat().st_ino) != identity:
            raise ValueError('ledger file replaced during inspection')
        return _same(current, proof['payload'])
    finally:
        if started:
            connection.rollback()
        connection.execute('PRAGMA query_only=' + ('ON' if query_only else 'OFF'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, required=True)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('record')
    create.add_argument('--plan', type=Path, required=True)
    show = commands.add_parser('inspect')
    show.add_argument('--task-id', required=True)
    show.add_argument('--task-kind', choices=KINDS)
    args = parser.parse_args(argv)
    connection = None
    try:
        root, db = _physical(args.repo_root.expanduser()), _physical(args.db.expanduser())
        connection = sqlite3.connect(db.as_uri() + ('?mode=rw' if args.command == 'record' else '?mode=ro'), uri=True)
        connection.row_factory = sqlite3.Row
        if args.command == 'record':
            result = record(connection, root=root, plan_path=_physical(args.plan.expanduser()))
        else:
            result = inspect(connection, root=root, task_id=args.task_id, task_kind=args.task_kind)
        print(_json(result))
        return 0
    except (OSError, ValueError, TypeError, KeyError, ImportError, sqlite3.Error) as error:
        print(_json({'schemaVersion': SCHEMA, 'error': str(error), 'recorded': False,
                     'FirestoreWritten': False, 'publicationCompleted': False}))
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
