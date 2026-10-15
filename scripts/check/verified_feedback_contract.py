"""Read-only checks for a genuinely saved gas verified-publication source.

No old source/patch projection is synthesized. Only an explicit save plan can
select this route; its native receipt and registry authority remain mandatory.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from scripts.common.fill_in_blank_contract import validate_fill_in_blank_question
from scripts.common.question_answer_contract import choice_text_content_alignment_issue
from scripts.common.repaso_firestore_schema import validate_question_doc
from scripts.common.requirements import (
    RequirementsError, get_stage_rules, load_requirements, validate_records,
)
from scripts.pipeline import rebuild_gas_shunin_all_year_artifacts as gas

SCHEMA = 'verified-feedback-check/v2'
KINDS = frozenset({'alignment', 'required', 'coverage'})
# These are executable checker inputs, even where a save plan predates this
# route. Bind their actual source files, not files copied under --repo-root.
IMPLEMENTATION_ROOT = Path(__file__).resolve().parents[2]
IMPLEMENTATION_FILES = (
    'scripts/check/verified_feedback_contract.py',
    'scripts/check/check_choice_text_alignment.py',
    'scripts/check/check_required_fields.py',
    'scripts/check/check_correct_choice_patch_coverage.py',
    'scripts/common/fill_in_blank_contract.py',
    'scripts/common/question_answer_contract.py',
    'scripts/common/repaso_firestore_schema.py',
    'scripts/common/requirements.py',
    'scripts/common/question_learning_patterns.py',
    'config/question_learning_patterns.json',
    'scripts/common/aggregate_answer_decomposition.py',
    'scripts/common/question_answer_scope.py',
    'scripts/pipeline/rebuild_gas_shunin_all_year_artifacts.py',
)
PROTECTED_FIELDS = frozenset({
    'questionId', 'originalQuestionId', 'originalQuestionBodyText',
    'originalQuestionChoiceText', 'questionBodyText', 'questionText',
    'qualificationId', 'listGroupId', 'examYear', 'examSource',
    'questionType', 'isChoiceOnly', 'correctChoiceText',
})


def _hash(content):
    return hashlib.sha256(content).hexdigest()


def _saver():
    # Only the explicit saved-plan route requires Firebase/native-save readers.
    # In particular, old checker invocation and --help keep their dependencies.
    from tools.question_bank import feedback_verified_save
    return feedback_verified_save


def _object_hash(value):
    return _hash(json.dumps(value, ensure_ascii=False, sort_keys=True,
                            separators=(',', ':'), allow_nan=False).encode())


def _physical(path):
    path = Path(path).absolute()
    if path != path.resolve() or path.is_symlink():
        raise ValueError('physical checker path required')
    return path


@contextmanager
def readonly_connection():
    from tools.question_bank.feedback_daily import DEFAULT_DB
    # A missing ledger fails; this path never creates tables or a database.
    db = sqlite3.connect(Path(DEFAULT_DB).as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()


def add_arguments(parser):
    parser.add_argument('--verified-save-plan', type=Path,
                        help='Explicit saved gas25 plan; excludes legacy arguments')
    parser.add_argument('--repo-root', type=Path,
                        help='Physical repository owning the saved plan and gas25 source')


def selected_mode(parser, args, argv, *, legacy_options):
    """Reject mixed routes, including explicitly supplied legacy defaults."""
    if args.verified_save_plan is None:
        if args.repo_root is not None:
            parser.error('--repo-root requires --verified-save-plan')
        return False
    if args.repo_root is None:
        parser.error('--verified-save-plan requires --repo-root')
    supplied = {arg.split('=', 1)[0] for arg in argv if arg.startswith('--')}
    # argparse accepts unique option abbreviations in the legacy route. Resolve
    # those too so an abbreviation cannot bypass route exclusivity.
    if any(option.startswith(arg) for arg in supplied for option in legacy_options):
        parser.error('verified-save mode excludes legacy arguments')
    return True


def _bundle(value):
    if (not isinstance(value, dict) or value.get('schemaVersion') != gas.SCHEMA_VERSION
            or value.get('qualificationId') not in gas.QUALIFICATIONS
            or not re.fullmatch(r'\d{4}', str(value.get('list_group_id', '')))):
        raise ValueError('unsupported verified gas source schema/identity')
    rows = value.get('questions')
    if (not isinstance(rows, list) or not rows
            or type(value.get('total_count')) is not int or value['total_count'] != len(rows)
            or any(not isinstance(row, dict) for row in rows)):
        raise ValueError('verified questions must be nonempty objects with exact total_count')
    ids = [row.get('questionId') for row in rows]
    if (any(not isinstance(i, str) or not i or i != i.strip() or '/' in i for i in ids)
            or len(ids) != len(set(ids))):
        raise ValueError('verified question IDs must be nonempty and unique')
    for row in rows:
        if (row.get('qualificationId') != value['qualificationId']
                or row.get('listGroupId') != str(value['list_group_id'])
                or type(row.get('examYear')) is not int
                or row['examYear'] != int(value['list_group_id'])
                or not gas.active_display(row)
                or any(field in row for field in gas.METADATA_FIELDS)):
            raise ValueError('verified row identity/display/server-field contract differs')
    return rows, ids


def _dto_issues(rows, source):
    issues = []
    for row in rows:
        identity = row['questionId']
        try:
            doc = deepcopy(row)
            doc.pop('questionId')
            # Same final schema used by upload dry-run. These four validator-only
            # audit values are not source data, checkpoints, output or writes.
            doc.update(createdAt=datetime(1970, 1, 1, tzinfo=timezone.utc),
                       updatedAt=datetime(1970, 1, 1, tzinfo=timezone.utc),
                       createdById='validator-only', updatedById='validator-only')
            validate_question_doc(doc, doc_id=identity)
            validate_fill_in_blank_question(row, doc_id=identity)
        except (ValueError, TypeError, KeyError) as error:
            issues.append(f'{source.name}:{identity}: {error}')
    return issues


def _alignment(rows):
    issues = []
    for row in rows:
        identity = row['questionId']
        issue = choice_text_content_alignment_issue(row)
        if issue:
            issues.append(f'{identity}: {issue}')
        # Final true_false DTOs have implicit choices; no choiceTextList is made.
        if row.get('questionType') == 'true_false':
            answer = row.get('correctChoiceText')
            if answer not in {'正しい', '間違い'}:
                issues.append(f'{identity}: noncanonical true_false verdict')
            elif row.get('isChoiceOnly') is False and not str(row.get('explanationText') or '').startswith(answer + '。'):
                issues.append(f'{identity}: explanation/verdict mismatch')
    return issues


def _coverage(before, candidate, plan):
    old, ids = _bundle(before)
    new, current_ids = _bundle(candidate)
    if ids != current_ids:
        raise ValueError('saved before/candidate IDs or order differ')
    if {k: v for k, v in before.items() if k != 'questions'} != {
            k: v for k, v in candidate.items() if k != 'questions'}:
        raise ValueError('root metadata changed')
    updates = plan.get('updates')
    if not isinstance(updates, list) or not updates:
        raise ValueError('approved update scope must be nonempty')
    approved = {}
    for update in updates:
        identity = update['questionId']
        if identity in approved or identity not in ids:
            raise ValueError('approved update ID absent or duplicate')
        diffs = update.get('fieldDiffs')
        if (not isinstance(diffs, list) or not diffs
                or any(not isinstance(d, dict) or d.get('field') in PROTECTED_FIELDS for d in diffs)):
            raise ValueError('protected identity/body/verdict delta is forbidden')
        approved[identity] = diffs
    for previous, current in zip(old, new, strict=True):
        if any((key in previous) != (key in current) or previous.get(key) != current.get(key)
               for key in PROTECTED_FIELDS):
            raise ValueError('protected identity/body/verdict changed')
        replay = deepcopy(previous)
        for delta in approved.get(previous['questionId'], []):
            field = delta['field']
            if (type(delta.get('beforePresent')) is not bool
                    or delta.get('afterPresent', True) is not True
                    or (field in previous) != delta['beforePresent']
                    or 'before' not in delta or previous.get(field) != delta['before']
                    or 'after' not in delta or previous.get(field) == delta['after']):
                raise ValueError('approved delta before/presence/no-op differs')
            replay[field] = deepcopy(delta['after'])
        if replay != current:
            raise ValueError('candidate differs from complete approved delta replay')
    # Reuse the real save projector, including dedicated registered maintenance
    # scope and differenceFacts-only metadata constraints; never fake old patches.
    if _saver()._project(before, updates, plan.get('maintenanceBinding')) != candidate:
        raise ValueError('exact save projection differs')


def check(kind, repo_root, plan_path, *, connection=None):
    if kind not in KINDS:
        raise ValueError('unsupported checker kind')
    if connection is None:
        try:
            with readonly_connection() as db:
                return check(kind, repo_root, plan_path, connection=db)
        except (OSError, ValueError, ImportError, sqlite3.Error) as error:
            return _failed(kind, error)
    result = {'schemaVersion': SCHEMA, 'checkKind': kind, 'checkedCount': 0,
              'checkedIdsSha256': None, 'orderedIdsSha256': None, 'artifacts': {},
              'planHash': None, 'saveReceiptSha256': None, 'inputFiles': {},
              'inputFilesVerified': False, 'issues': []}
    files = {}
    try:
        saver = _saver()
        root, path = _physical(repo_root), _physical(plan_path)
        def capture(file, expected_hash=None):
            file = _physical(file)
            content = file.read_bytes()
            digest = _hash(content)
            if expected_hash is not None and digest != expected_hash:
                raise ValueError('declared checker input hash differs')
            if str(file) in files and files[str(file)] != digest:
                raise ValueError('checker input changed during read')
            files[str(file)] = digest
            return content
        def read(file):
            file = _physical(file)
            if not file.is_relative_to(root):
                raise ValueError('checker input outside repository')
            content = capture(file)
            value = json.loads(content)
            if not isinstance(value, dict):
                raise ValueError('checker artifact must be a JSON object')
            return value
        for implementation in IMPLEMENTATION_FILES:
            capture(IMPLEMENTATION_ROOT / implementation)
        plan = read(path)
        if (plan.get('schemaVersion') not in {saver.SCHEMA, saver.MAINTENANCE_SCHEMA}
                or _object_hash({k: v for k, v in plan.items() if k != 'planHash'}) != plan.get('planHash')):
            raise ValueError('verified save plan schema/hash differs')
        receipt_path = path.parent / 'save-receipt.json'
        receipt = read(receipt_path)
        verified = saver.load_saved(root, path, receipt_path, connection=connection)
        if not isinstance(plan.get('inputs'), dict) or not plan['inputs']:
            raise ValueError('nonempty saved-plan input hashes required')
        # load_saved authenticates these refs, including official evidence,
        # baselines, patches and native-save implementation. Re-hash the actual
        # bytes and carry them into this checker's complete input binding.
        for input_path, expected_hash in plan['inputs'].items():
            if (not isinstance(input_path, str) or not Path(input_path).is_absolute()
                    or not isinstance(expected_hash, str)
                    or not re.fullmatch('[0-9a-f]{64}', expected_hash)):
                raise ValueError('invalid saved-plan input hash binding')
            capture(input_path, expected_hash)
        expected_schema = saver.MAINTENANCE_RECEIPT if plan['schemaVersion'] == saver.MAINTENANCE_SCHEMA else saver.RECEIPT
        if (receipt != verified or receipt.get('schemaVersion') != expected_schema
                or receipt.get('planHash') != plan['planHash']
                or receipt.get('candidateSha256') != plan['candidateSha256']
                or receipt.get('beforeSha256') != plan['beforeSha256']
                or receipt.get('FirestoreWritten') is not False):
            raise ValueError('genuine saved receipt differs')
        result.update(planHash=plan['planHash'], saveReceiptSha256=files[str(receipt_path)])
        formal = _physical(root / plan['formalPath'])
        canonical = read(formal)
        rows, ids = _bundle(canonical)
        year, qualification = str(canonical['list_group_id']), canonical['qualificationId']
        expected = root / 'output' / qualification / 'questions_json' / year / '25_verified_publication' / f'{year}_verified_publication.json'
        if formal != expected or files[str(formal)] != plan['candidateSha256']:
            raise ValueError('current formal25 path/hash differs from saved candidate')
        before = read(plan['beforeRef'])
        candidate = read(plan['candidateRef'])
        if (files[str(_physical(plan['beforeRef']))] != plan['beforeSha256']
                or files[str(_physical(plan['candidateRef']))] != plan['candidateSha256']
                or canonical != candidate):
            raise ValueError('fixed before/candidate bytes differ')
        _, before_ids = _bundle(before)
        if before_ids != ids:
            raise ValueError('full before/current IDs or order differ')
        merged_path = formal.parent.parent / '30_merged_2' / f'{year}_verified_publication_merged.json'
        converted_paths = sorted((formal.parent.parent / '40_convert').glob(f'{year}_firestore_*.json'))
        if len(converted_paths) != 1:
            raise ValueError('exactly one current converted artifact required')
        merged, converted = read(merged_path), read(converted_paths[0])
        expected_merged = {**deepcopy(canonical), 'schemaVersion': gas.MERGED_SCHEMA_VERSION}
        expected_convert = {'list_group_id': year, 'questions': deepcopy(rows), 'total_count': len(rows)}
        if merged != expected_merged or converted != expected_convert:
            raise ValueError('30/40 artifacts differ from full formal25 generation')
        result.update(checkedCount=len(rows), checkedIdsSha256=_object_hash(sorted(ids)),
                      orderedIdsSha256=_object_hash(ids), artifacts={
                          stage: {'path': str(file), 'sha256': files[str(file)]}
                          for stage, file in [('25', formal), ('30', merged_path), ('40', converted_paths[0])]})
        result['issues'].extend(_dto_issues(rows, formal))
        result['issues'].extend(_alignment(rows))
        if kind == 'required':
            requirements_path = root / 'config/requirements/required_fields.toml'
            capture(requirements_path)
            rules = get_stage_rules(load_requirements(requirements_path), stage='firestore',
                                    record_array='questions', qualification=qualification)
            if not rules.required_keys or not rules.required_non_empty_keys:
                raise ValueError('nonempty final questions requirements required')
            result['issues'].extend(validate_records(records=rows, rules=rules,
                                                     source_path=formal, id_keys=('questionId',)))
        elif kind == 'coverage':
            _coverage(before, canonical, plan)
        # Re-read native/authority evidence and every local input after inspection.
        if saver.load_saved(root, path, receipt_path, connection=connection) != verified:
            raise ValueError('save authority changed during checking')
        if any(_hash(_physical(file).read_bytes()) != digest for file, digest in files.items()):
            raise ValueError('checker input changed during inspection')
        if result['checkedCount'] <= 0:
            raise ValueError('nonempty checked source required for verified input receipt')
        result.update(inputFiles=dict(sorted(files.items())), inputFilesVerified=True)
    except (OSError, ValueError, TypeError, KeyError, IndexError, ImportError, RequirementsError, sqlite3.Error) as error:
        result['issues'].append(str(error))
    result['exitCode'] = 1 if result['issues'] else 0
    result['status'] = 'failed' if result['issues'] else 'passed'
    return result


def _failed(kind, error):
    return {'schemaVersion': SCHEMA, 'checkKind': kind, 'checkedCount': 0,
            'checkedIdsSha256': None, 'orderedIdsSha256': None, 'artifacts': {},
            'planHash': None, 'saveReceiptSha256': None, 'inputFiles': {},
            'inputFilesVerified': False, 'exitCode': 1,
            'status': 'failed', 'issues': [str(error)]}


def run_cli(kind, args):
    result = check(kind, args.repo_root, args.verified_save_plan)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result['exitCode']
