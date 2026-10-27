#!/usr/bin/env python3
"""Materialize adopted minimal 10/15 metadata through normal CLI only.

Default prepares and validates private outputs. --apply additionally installs
identical validated bytes and runs normal merge. No 00_source or remote write.
Adoption schema: hei-minimal-metadata-adoption/v1, typeCandidates[522] and
intentCandidates[27] contain sourceQuestionId/currentRecordHash; candidateInputs
binds type/intent path+sha256; bodyReviewCompletion binds a 306-record report.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from scripts.common.question_identity import load_source_record_inventory, review_question_id
from scripts.merge.merge_utils import strip_timestamp_suffix
from tools.question_bank.question_issue_reports import sha256_json

BASE = ROOT / 'output/gas-shunin-hei'
QUESTIONS = BASE / 'questions_json'
REVIEW = BASE / 'pdf_patch_review'
NOTES = Path(__file__).resolve().parent
TYPE_INPUT = NOTES / 'T008-current-type-candidates.json'
INTENT_INPUT = NOTES / 'T006-intent-candidate-native.json'
TYPE_FIELDS = {'questionType', 'isCalculationQuestion'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def file_ref(path):
    return {'path': str(path.relative_to(ROOT)), 'sha256': digest(path)}


def write_once(path, payload):
    path = Path(path)
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + '\n').encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f'private artifact drift: {path}')
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError(f'conflicting exclusive write: {path}')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def exact_index(rows, expected_count, label):
    if not isinstance(rows, list) or len(rows) != expected_count:
        raise ValueError(f'{label}: expected {expected_count} records')
    out = {}
    for row in rows:
        key = row['sourceQuestionId']
        if key in out:
            raise ValueError(f'{label}: duplicate identity {key}')
        out[key] = row
    return out


def fixed_ref(value, label):
    path = (ROOT / value['path']).resolve()
    if not path.is_relative_to(ROOT) or not path.is_file() or digest(path) != value['sha256']:
        raise ValueError(f'{label}: fixed reference bytes/hash mismatch')
    return path


def source_inventory():
    entries, files = {}, {}
    for year in range(2017, 2026):
        for entry in load_source_record_inventory(QUESTIONS / str(year) / '00_source', qualification='gas-shunin-hei', list_group_id=str(year)):
            key = entry.record['source_question_id']
            if key in entries:
                raise ValueError('duplicate canonical source identity')
            entries[key] = entry
            files[str(entry.path.relative_to(ROOT))] = digest(entry.path)
    baseline = {x['path']: x['sha256'] for x in read(NOTES / 'source-baseline.json')}
    if len(entries) != 522 or len(files) != 27 or files != baseline:
        raise ValueError('source inventory/baseline changed')
    return entries, files


def merged_inventory(entries):
    result, paths = {}, {}
    for year in range(2017, 2026):
        selected = {}
        for path in sorted((QUESTIONS / str(year) / '30_merged_2').glob('*.json')):
            stem = strip_timestamp_suffix(path.stem).removesuffix('_merged')
            if stem in selected:
                raise ValueError('ambiguous merged file versions; normal merge first')
            selected[stem] = path
        if len(selected) != 3:
            raise ValueError('normal merged input must have three files per year')
        for stem, path in selected.items():
            for record in read(path)['question_bodies']:
                key = record['source_question_id']
                entry = entries.get(key)
                if key in result or entry is None or entry.identity.source_stem != stem:
                    raise ValueError('merged/source stem or natural identity mismatch')
                if review_question_id(record) != entry.identity.binding.review_question_id:
                    raise ValueError('merged/source public identity mismatch')
                for field, value in entry.identity.binding.as_mapping().items():
                    if field in record and record[field] != value:
                        raise ValueError('merged complete source binding mismatch')
                result[key] = copy.deepcopy(record)
                paths[key] = path
    if set(result) != set(entries):
        raise ValueError('merged 522-ID coverage incomplete')
    return result, paths


def verify_body_completion(adoption):
    path = fixed_ref(adoption['bodyReviewCompletion'], 'body review completion')
    report = read(path)
    records = exact_index(report['records'], 306, 'completed body review')
    originals = {x['sourceQuestionId'] for p in (REVIEW / 'plans-v2').glob('*.json') for x in read(p)['items']}
    if set(records) != originals:
        raise ValueError('completed body review 306-ID scope mismatch')
    formal = sorted(QUESTIONS.glob('*/24_questionIssueCorrections/*.json'))
    if len(formal) != 289:
        raise ValueError('all 289 formal 24 corrections must exist first')
    expected_patches = {}
    decisions = {'fix': 0, 'no_change': 0}
    for key, row in records.items():
        decision = row['decision']
        if decision not in decisions:
            raise ValueError('body review/save not completed: unresolved decision')
        decisions[decision] += 1
        if decision == 'fix':
            ref = row.get('patch') or {'path': row['patchPath'], 'sha256': row['patchSha256']}
            target = fixed_ref(ref, 'body formal patch')
            if target.parent.name != '24_questionIssueCorrections':
                raise ValueError('body proof does not point to formal 24')
            expected_patches[str(target.relative_to(ROOT))] = digest(target)
    if decisions != {'fix': 289, 'no_change': 17} or set(expected_patches) != {str(p.relative_to(ROOT)) for p in formal}:
        raise ValueError('body review/save 289/17 proof mismatch')
    return expected_patches


def adoption_inputs(path, entries, current):
    adoption = read(path)
    if adoption.get('schemaVersion') != 'hei-minimal-metadata-adoption/v1':
        raise ValueError('unsupported metadata adoption schema')
    type_ref = fixed_ref(adoption['candidateInputs']['type'], 'type candidates')
    intent_ref = fixed_ref(adoption['candidateInputs']['intent'], 'intent candidates')
    if type_ref != TYPE_INPUT or intent_ref != INTENT_INPUT:
        raise ValueError('unexpected candidate origin')
    types = exact_index(read(type_ref)['candidates'], 522, 'type candidates')
    intent_rows = read(intent_ref)['payload']['goalbuddy_receipt_v1']['evidence'][0]['intentCandidates']
    intents = exact_index(intent_rows, 27, 'intent candidates')
    adopted_types = exact_index(adoption['typeCandidates'], 522, 'adopted types')
    adopted_intents = exact_index(adoption['intentCandidates'], 27, 'adopted intents')
    if set(types) != set(entries) or set(adopted_types) != set(types) or set(adopted_intents) != set(intents):
        raise ValueError('metadata adoption coverage mismatch')
    if set(intents) != set(read(type_ref)['trueFalseMissingIntent']):
        raise ValueError('intent subset differs from independently identified missing intents')
    for kind, candidates, adopted in [('type', types, adopted_types), ('intent', intents, adopted_intents)]:
        for key, candidate in candidates.items():
            if key not in current or sha256_json(current[key]) != adopted[key]['currentRecordHash']:
                raise ValueError(f'adopted current record drift: {key}')
            if candidate['original_question_id'] != entries[key].identity.binding.review_question_id or candidate.get('holdReason'):
                raise ValueError(f'unadoptable candidate identity/hold: {key}')
            if kind == 'type':
                if candidate['questionType'] not in {'true_false', 'group_choice', 'flash_card'} or not isinstance(candidate['isCalculationQuestion'], bool) or candidate.get('requiresAggregateDecomposition'):
                    raise ValueError(f'type candidate needs separate decomposition: {key}')
            elif candidate['questionIntent'] not in {'select_correct', 'select_incorrect'}:
                raise ValueError(f'invalid intent candidate: {key}')
    return adoption, types, intents


def run_cli(arguments, name, work, commands):
    log = work / 'logs' / (name + '.log')
    log.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, *map(str, arguments)]
    with log.open('w') as stream:
        code = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT).returncode
    commands.append({'command': command, 'exitCode': code, 'log': file_ref(log)})
    if code != 0:
        raise RuntimeError(f'normal CLI failed: {name}; see {log}')


def prepare(current, entries, types, intents, work, commands):
    planned = []
    for task, candidates, subdir, suffix, checker in [
        ('question_type', types, '10_questionType_fixed', '_questionType_fixed.json', 'check-question-type-patch'),
        ('question_intent', intents, '15_correctChoiceText_fixed', '_merged_correctChoiceText_fixed.json', 'check-question-intent-patch'),
    ]:
        grouped = {}
        for key in candidates:
            entry = entries[key]
            grouped.setdefault((key.split(':')[0], entry.identity.source_stem), []).append(key)
        for (year, stem), keys in sorted(grouped.items()):
            group = work / 'inputs' / task / 'gas-shunin-hei' / 'questions_json' / year
            group.mkdir(parents=True, exist_ok=True)
            link = group / '00_source'
            original = QUESTIONS / year / '00_source'
            if link.exists() or link.is_symlink():
                if not link.is_symlink() or link.resolve() != original.resolve():
                    raise ValueError('private source link changed')
            else:
                link.symlink_to(original, target_is_directory=True)
            # The subset is a current judgment input, never a replacement source.
            records = [{**copy.deepcopy(current[key]), **entries[key].identity.binding.as_mapping()} for key in sorted(keys)]
            input_path = group / '12_merged_questionType' / (stem + '_merged.json')
            raw_path = group / 'raw' / (stem + '.json')
            output_path = group / subdir / (stem + suffix)
            write_once(input_path, {'question_bodies': records})
            write_once(raw_path, [copy.deepcopy(candidates[key]) for key in sorted(keys)])
            run_cli(['tools/question_bank/question_bank.py', 'materialize-patch', '--task', task, '--source', input_path, '--raw', raw_path, '--output', output_path], task + '-' + stem, work, commands)
            run_cli(['tools/question_bank/question_bank.py', checker, '--source', input_path, '--patch', output_path], 'coverage-' + task + '-' + stem, work, commands)
            output = read(output_path)
            if len(output) != len(keys) or {x['reviewQuestionId'] for x in output} != {entries[key].identity.binding.review_question_id for key in keys}:
                raise ValueError('materialized identity/coverage mismatch')
            by_id = {x['reviewQuestionId']: x for x in output}
            for key in keys:
                row = by_id[entries[key].identity.binding.review_question_id]
                fields = TYPE_FIELDS if task == 'question_type' else {'questionIntent'}
                if any(row[field] != candidates[key][field] for field in fields):
                    raise ValueError('materializer changed adopted metadata meaning')
                if task == 'question_type' and (row['questionBodyText'] != current[key]['questionBodyText'] or row['choiceTextList'] != current[key]['choiceTextList']):
                    raise ValueError('materializer snapshot changed current content')
                if set(row) & {'correctChoiceText', 'answer_result_text', 'explanationText'}:
                    raise ValueError('metadata patch contains protected answer/explanation')
            planned.append({'task': task, 'sourceInput': file_ref(input_path), 'rawInput': file_ref(raw_path),
                            'stagedPatch': file_ref(output_path), 'targetPath': str((QUESTIONS / year / subdir / (stem + suffix)).relative_to(ROOT)),
                            'sourceQuestionIds': sorted(keys), 'bytesUtf8': output_path.read_text()})
    return planned


def unchanged_inputs(source_hashes, patch_hashes):
    _entries, now_source = source_inventory()
    now_patches = {str(p.relative_to(ROOT)): digest(p) for p in QUESTIONS.glob('*/24_questionIssueCorrections/*.json')}
    if now_source != source_hashes or now_patches != patch_hashes:
        raise ValueError('source/body correction changed during metadata operation')


def verify_after(before, after, types, intents):
    if set(before) != set(after):
        raise ValueError('merge changed question identity coverage')
    changes = []
    for key in before:
        allowed = TYPE_FIELDS | ({'questionIntent'} if key in intents else set())
        different = {field for field in set(before[key]) | set(after[key])
                     if (field in before[key]) != (field in after[key]) or before[key].get(field) != after[key].get(field)}
        if not different <= allowed:
            raise ValueError(f'normal merge changed protected field: {key} {sorted(different - allowed)}')
        if any(after[key].get(field) != types[key][field] for field in TYPE_FIELDS):
            raise ValueError('normal merge did not reflect adopted type/calculation metadata')
        if key in intents and after[key].get('questionIntent') != intents[key]['questionIntent']:
            raise ValueError('normal merge did not reflect adopted intent')
        changes.append({'sourceQuestionId': key, 'changedFields': sorted(different), 'beforeHash': sha256_json(before[key]), 'afterHash': sha256_json(after[key])})
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adoption', type=Path, default=REVIEW / 'metadata-adoption.json')
    parser.add_argument('--apply', action='store_true', help='Install locally validated formal 10/15 patches and run normal merge')
    args = parser.parse_args()
    entries, source_hashes = source_inventory()
    current, merged_paths = merged_inventory(entries)
    adoption, types, intents = adoption_inputs(args.adoption.resolve(), entries, current)
    patch_hashes = verify_body_completion(adoption)
    work = REVIEW / 'metadata' / sha256_json(adoption)[:16]
    work.mkdir(parents=True, exist_ok=True)
    fixed_inputs = [file_ref(args.adoption.resolve()), adoption['candidateInputs']['type'], adoption['candidateInputs']['intent'], adoption['bodyReviewCompletion']]
    implementation_inputs = [file_ref(ROOT / name) for name in [str(Path(__file__).relative_to(ROOT)), 'tools/question_bank/question_bank.py', 'scripts/fix/materialize_minimal_patch.py', 'scripts/check/check_questiontype_patch_coverage.py', 'tools/question_bank/checks/check_question_intent_patch_coverage.py', 'scripts/common/question_answer_contract.py']]
    result = {'schemaVersion': 'hei-minimal-metadata-result/v1', 'adoption': file_ref(args.adoption.resolve()),
              'implementationInputs': implementation_inputs,
              'sourceBaseline': source_hashes, 'bodyPatchInputs': patch_hashes, 'currentInputs': {str(p.relative_to(ROOT)): digest(p) for p in set(merged_paths.values())},
              'commands': [], 'status': 'preparing', 'formalApplied': False}
    try:
        planned = prepare(current, entries, types, intents, work, result['commands'])
        result['patches'] = planned
        unchanged_inputs(source_hashes, patch_hashes)
        for ref in fixed_inputs + implementation_inputs:
            fixed_ref(ref, 'adopted input or implementation')
        if args.apply:
            # Check every existing target before making any formal write.
            for row in planned:
                target = ROOT / row['targetPath']
                staged = fixed_ref(row['stagedPatch'], 'validated staged metadata')
                if target.exists() and read(target) != read(staged):
                    raise ValueError(f'existing formal metadata differs: {target}')
            for row in planned:
                unchanged_inputs(source_hashes, patch_hashes)
                for ref in fixed_inputs + implementation_inputs:
                    fixed_ref(ref, 'adopted input or implementation')
                for path, expected_hash in result['currentInputs'].items():
                    if not (ROOT / path).is_file() or digest(ROOT / path) != expected_hash:
                        raise ValueError('adopted merged input changed before formal save')
                target = ROOT / row['targetPath']
                if not target.exists():
                    write_once(target, read(fixed_ref(row['stagedPatch'], 'validated staged metadata')))
                row['formalPatch'] = file_ref(target)
            result['formalApplied'] = True
            for year in range(2017, 2026):
                run_cli(['scripts/merge/00_merge_all.py', str(year), '--base-dir', QUESTIONS], 'normal-merge-' + str(year), work, result['commands'])
            after, _paths = merged_inventory(entries)
            result['recordChanges'] = verify_after(current, after, types, intents)
            unchanged_inputs(source_hashes, patch_hashes)
            for ref in fixed_inputs + implementation_inputs:
                fixed_ref(ref, 'adopted input or implementation')
            result['status'] = 'applied_and_verified'
        else:
            result['status'] = 'private_prepared_and_coverage_verified'
    except Exception as exc:
        result['status'] = 'failed'
        result['error'] = str(exc)
        raise
    finally:
        result['finishedAt'] = datetime.now(timezone.utc).isoformat()
        (work / 'metadata-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(work / 'metadata-result.json')


if __name__ == '__main__':
    main()
