"""Append-only ledger facts for genuine normal-stage saves, never publication."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from tools.question_bank import feedback_prepared_stage as stage
from tools.question_review_console.projection import sha256_json

SCHEMA = 'feedback-normal-stage-save-record/v1'
TABLE = 'normal_stage_save_records'
DDL = f'''CREATE TABLE IF NOT EXISTS {TABLE} (
    plan_hash TEXT PRIMARY KEY, task_id TEXT NOT NULL, stage_id TEXT NOT NULL,
    payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL, recorded_at TEXT NOT NULL)'''
TRIGGERS = {f'{TABLE}_no_{action.lower()}':
    f"CREATE TRIGGER {TABLE}_no_{action.lower()} BEFORE {action} ON {TABLE} BEGIN SELECT RAISE(ABORT, 'normal stage save records are append-only'); END"
    for action in ('UPDATE', 'DELETE')}


def _proof(root, path, approval_path, *, coordinator, connection):
    root = stage._physical(root)
    path = stage._inside(root, path, private=True)
    approval_path = stage._inside(root, approval_path, private=True)
    receipt_path = path.parent / 'save-receipt.json'
    # Registration has no call to the mutation API, including on a receipt race.
    if not receipt_path.is_file():
        raise ValueError('existing normal-stage save receipt required')
    files = {}
    receipt = stage._json(stage._read(root, receipt_path, files, private=True))
    plan = stage._json(stage._read(root, path, files, private=True))
    stage._read(root, approval_path, files, private=True)
    if (plan.get('request', {}).get('authority', {}).get('kind') != 'improvement'
            or receipt.get('formalPatchSaved') is not True
            or receipt.get('workVersionRecorded') is not True
            or receipt.get('checkpointRecorded') is not True
            or receipt.get('FirestoreWritten') is not False
            or receipt.get('publicationReady') is not False):
        raise ValueError('unpublished genuine improvement stage save required')
    task = plan['request']['authority']['taskId']
    row = connection.execute('SELECT status FROM improvement_tasks WHERE task_id=?', (task,)).fetchone()
    if row is None or row[0] not in {'open', 'in_progress', 'blocked', 'review_pending'}:
        raise ValueError('current unfinished task required')
    loaded = stage.load_plan(root, path, coordinator=coordinator, connection=connection, saved=True)
    if loaded != plan:
        raise ValueError('saved plan changed during verification')
    scope = stage._scope_from_saved(root, path, plan, connection)
    approval_raw = stage._read(root, approval_path, files, private=True)
    binding = stage.verify_independent_approval(stage._json(approval_raw), scope, production=True)
    approval_ref = {'ref': str(approval_path), 'sha256': stage._hash(approval_raw)}
    intent = {'schemaVersion': 'feedback-prepared-stage-apply-intent/v1',
        'planHash': plan['planHash'], 'approval': approval_ref, 'nativeBinding': binding, 'scope': scope}
    if stage._json(stage._read(root, path.parent/'apply-intent.json', files, private=True)) != intent:
        raise ValueError('saved application intent differs')
    verified = stage._save_receipt(plan, approval_ref, binding,
        stage._saved_files(root, plan), stage._stage_evidence(coordinator, plan))
    if verified != receipt:
        raise ValueError('saved receipt differs from normal writer/native verification')
    if stage._live(plan['request'], root=root, connection=connection) != plan['liveSnapshots']:
        raise ValueError('live read-set changed after normal stage save')
    stage._unchanged(root, files)
    return {'schemaVersion': SCHEMA, 'taskId': task, 'stageId': plan['stageId'],
        'planHash': plan['planHash'], 'candidateHash': plan['preparedCandidate']['contentHash'],
        'taskRequirement': plan['taskRequirement'], 'sourceBinding': plan['request']['source']['binding'],
        'artifacts': {'plan': str(path), 'approval': str(approval_path), 'saveReceipt': str(receipt_path)},
        'inputFiles': files, 'state': 'formally_saved_stage', 'taskStatusAtVerification': row[0],
        'formalPatchSaved': True, 'FirestoreWritten': False, 'publicationCompleted': False}


def _schema(connection):
    connection.execute(DDL)
    stored_ddl = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone()[0]
    if stored_ddl != DDL.replace('IF NOT EXISTS ', ''):
        raise ValueError('normal stage save ledger table contract differs')
    for name, sql in TRIGGERS.items():
        existing = connection.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,)).fetchone()
        if existing is None:
            connection.execute(sql)
        elif existing[0] != sql:
            raise ValueError('normal stage save ledger trigger differs')
    columns = [r[1] for r in connection.execute(f'PRAGMA table_info({TABLE})')]
    if columns != ['plan_hash', 'task_id', 'stage_id', 'payload_json', 'payload_sha256', 'recorded_at']:
        raise ValueError('normal stage save ledger schema differs')


def record(root, path, approval_path, *, coordinator, connection):
    if connection.in_transaction:
        raise ValueError('independent ledger transaction required')
    value = _proof(root, path, approval_path, coordinator=coordinator, connection=connection)
    # Fresh native/source/runtime/live and task validation also runs under the
    # ledger writer lock. A failure rolls back schema creation and insertion.
    connection.execute('BEGIN IMMEDIATE')
    try:
        current = _proof(root, path, approval_path, coordinator=coordinator, connection=connection)
        if current != value:
            raise ValueError('normal stage save proof changed at ledger boundary')
        _schema(connection)
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        existing = connection.execute(f'SELECT payload_json, payload_sha256 FROM {TABLE} WHERE plan_hash=?', (value['planHash'],)).fetchone()
        if existing is not None:
            # Task work status may legitimately advance after the immutable fact.
            stored = json.loads(existing[0])
            if existing[1] != sha256_json(stored):
                raise ValueError('stored normal stage save fact hash differs')
            comparable = dict(stored); comparable['taskStatusAtVerification'] = value['taskStatusAtVerification']
            if comparable != value:
                raise ValueError('conflicting normal stage save ledger fact')
            value = stored
        else:
            connection.execute(f'INSERT INTO {TABLE} VALUES (?,?,?,?,?,?)',
                (value['planHash'], value['taskId'], value['stageId'], encoded, sha256_json(value), datetime.now(timezone.utc).isoformat()))
        row = connection.execute(f'SELECT payload_json, payload_sha256 FROM {TABLE} WHERE plan_hash=?', (value['planHash'],)).fetchone()
        if json.loads(row[0]) != value or row[1] != sha256_json(value):
            raise ValueError('normal stage save ledger readback differs')
        connection.commit()
        return value
    except BaseException:
        connection.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, required=True)
    parser.add_argument('--db', type=Path, default=stage.LEDGER)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--approval', type=Path, required=True)
    args = parser.parse_args()
    db = stage._physical(args.db.expanduser())
    if not db.is_file():
        raise ValueError('existing canonical ledger required')
    with closing(sqlite3.connect(db)) as connection:
        result = record(args.repo_root, args.plan, args.approval,
            coordinator=stage.coordinator_for(args.repo_root), connection=connection)
        from tools.question_bank.feedback_daily import _backup_db
        _backup_db(connection, db)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
