#!/usr/bin/env python3
"""Inventory unresolved original IDs and stage fresh single-question reviews.

Only --execute starts native reviews. This helper never passes --save and never
writes a formal 24 patch. Existing conclusive main/recovery receipts are reused.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from tools.question_bank.question_issue_reports import sha256_json

BASE = ROOT / 'output/gas-shunin-hei/pdf_patch_review'
DRIVER = ROOT / 'docs/goals/gas-shunin-hei-pdf-patches/notes/T007-review-driver.py'
DRIVER_SHA256 = 'a0829c9b184d2e02aa28895a77315dce74b150205bc4bf052670c51123c81329'
RULE = 'proposedChangesには現在値から実際に異なるfieldのみを含める。同値のchoiceTextListの再掲はfindingsへ記録する。公式画像から必要と判断した選択肢の実質的な修正とその完全値の提案は妨げない。'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def freeze(path, value):
    """Plan/result identity is write-once, so changed evidence needs a fresh run."""
    path = Path(path)
    data = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text() != data:
            raise ValueError(f'existing recovery artifact differs: {path}')
    else:
        with path.open('x', encoding='utf-8') as stream:
            stream.write(data)


def plan_items(directory):
    result = {}
    for path in sorted(directory.glob('*.json')):
        plan = read(path)
        for item in plan['items']:
            key = item['sourceQuestionId']
            if key in result:
                raise ValueError(f'duplicate plan source ID: {key}')
            result[key] = {'plan': plan, 'item': item, 'path': path}
    if not result:
        raise ValueError(f'empty plan inventory: {directory}')
    return result


def conclusive_results(path, *, formal):
    """Read only results tied to a successful normal per-question replay."""
    data = read(path)
    plan = read(path.parent / 'frozen-plan.json')['plan']
    if data['planSha256'] != sha256_json(plan):
        raise ValueError(f'result/plan hash mismatch: {path}')
    by_id = {x['sourceQuestionId']: x for x in plan['items']}
    if len(by_id) != len(plan['items']):
        raise ValueError('duplicate frozen source IDs')
    seen, result = set(), {}
    for row in data['results']:
        key = row['sourceQuestionId']
        if key in seen or key not in by_id:
            raise ValueError('duplicate or unknown result ID')
        seen.add(key)
        if row['decision'] not in ('fix', 'no_change'):
            continue
        work_id = by_id[key]['workItem']['workId']
        replay = path.parent / 'individual' / work_id / 'standard-replay'
        receipt = read(replay / 'driver-replay-receipt.json')
        reviews = [read(replay / f'{phase}.json') for phase in ('blind_a', 'blind_b', 'challenge')]
        if receipt['blindInputHash'] != sha256_json(read(replay / 'blind_input.json')):
            raise ValueError('normal replay input hash mismatch')
        if receipt['reviews'] != [sha256_json(review) for review in reviews]:
            raise ValueError('normal replay result hashes mismatch')
        expected_conclusion = 'problem_found' if row['decision'] == 'fix' else 'no_problem'
        if any(review['conclusion'] != expected_conclusion for review in reviews[:2]):
            raise ValueError('normal blind conclusions do not authorize decision')
        if reviews[0]['proposedChanges'] != reviews[1]['proposedChanges'] or reviews[2].get('changes', {}) != reviews[0]['proposedChanges']:
            raise ValueError('normal complete review changes mismatch')
        if reviews[2]['decision'] != row['decision']:
            raise ValueError('normal challenge/result decision mismatch')
        if reviews[2]['blindReviewHashes'] != [sha256_json(x) for x in reviews[:2]]:
            raise ValueError('challenge does not bind exact blind reviews')
        if row['decision'] == 'fix':
            patch_path = ROOT / row['patchPath' if formal else 'stagedPath']
            if not patch_path.is_file() or digest(patch_path) != row['patchSha256']:
                raise ValueError('accepted/staged patch bytes missing or changed')
            patch = read(patch_path)
            if patch['blindReviewHashes'] != [sha256_json(x) for x in reviews[:2]] or patch['challengeReviewHash'] != sha256_json(reviews[2]):
                raise ValueError('patch does not bind successful replay')
            entries = patch['entries']
            if len(entries) != 1 or any(entries[0].get(field) != value for field, value in by_id[key]['sourceBinding'].items()):
                raise ValueError('patch/source identity mismatch')
            if entries[0]['changes'] != reviews[2]['changes']:
                raise ValueError('patch/review complete changes mismatch')
            if formal and patch_path.parent.name != '24_questionIssueCorrections':
                raise ValueError('accepted fix is not formally saved')
        elif reviews[2].get('changes'):
            raise ValueError('no_change contains changes')
        result[key] = {'sourceQuestionId': key, 'decision': row['decision'],
                       'resultPath': str(path.relative_to(ROOT)), 'result': row}
    if seen != set(by_id):
        raise ValueError('result does not cover all frozen IDs')
    return result


def inventory(base=BASE):
    original = plan_items(base / 'plans-v2')
    current = plan_items(base / 'plans-v4')
    if len(original) != 306 or not set(current).issubset(original):
        raise ValueError('original 306-ID inventory or v4 subset changed')
    accepted = {}
    for version in ('runs-v2', 'runs-v3', 'runs-v4'):
        for path in sorted((base / version).glob('[0-9]*-*/save-result.json')):
            for key, receipt in conclusive_results(path, formal=True).items():
                if key not in original:
                    raise ValueError('accepted result outside original scope')
                if key in accepted and accepted[key]['decision'] != receipt['decision']:
                    raise ValueError('conflicting accepted decisions')
                accepted[key] = receipt
    # v4 intentionally omits the four prior pilot IDs; omission itself is not success.
    missing_current = set(original) - set(current)
    if not missing_current.issubset(accepted):
        raise ValueError('original IDs missing from v4 are not proven accepted pilots')
    reused = {}
    for version in ('runs-v4', 'runs-v5'):
        for path in sorted((base / version).glob('recovery-*/stage-result.json')):
            for key, receipt in conclusive_results(path, formal=False).items():
                if key not in original:
                    raise ValueError('staged result outside original scope')
                if key not in accepted:
                    reused[key] = receipt
    terminal_plans = set()
    execution_path = base / 'batch-execution-results-v4.json'
    if execution_path.exists():
        for batch in read(execution_path)['batches']:
            if isinstance(batch.get('exitCode'), int) and batch.get('finishedAt'):
                terminal_plans.add(Path(batch['plan']).stem)
    # A completed pilot may be outside the orchestration's batch list.
    terminal_plans.update(path.parent.name for path in (base / 'runs-v4').glob('[0-9]*-*/save-result.json'))
    unresolved = set(original) - set(accepted) - set(reused)
    pending = {key: current[key] for key in sorted(unresolved) if current[key]['path'].stem in terminal_plans}
    deferred = {key: current[key] for key in sorted(unresolved - set(pending))}
    return {'original': original, 'current': current, 'accepted': accepted, 'reused': reused,
            'pending': pending, 'deferred': deferred}



def recovery_plan(key, source):
    """Never copy old reviewer decisions or expected phase values into a fresh plan."""
    old_plan, item = source['plan'], copy.deepcopy(source['item'])
    plan = {k: copy.deepcopy(v) for k, v in old_plan.items() if k != 'items'}
    slug = key.replace(':', '-').replace('問', 'q')
    plan['batchId'] = 'hei-pdf-recovery2-' + slug
    for snapshot in item['workItem']['caseSnapshots']:
        content = snapshot['canonicalSnapshot']
        rules = content.setdefault('displayFormatRules', [])
        for rule in (RULE,
                     '【科目共通注】は元PDFの引用文字列ではなく、適用する共通注を設問本文から区別する編集上の表示見出しである。',
                     '元表の左右欄を文章として接続し、欄の区切りに半角空白、注ごとの区切りに改行を用いる。これらの表示上の区切りを原PDFの逐語的な文字列と主張しない。',
                     '注の用語・数値・否定・適用範囲・例外、図の必要関係は公式画像に照らして独立に判定する。表示規則は候補の内容を正しいと認定するものではない。'):
            if rule not in rules:
                rules.append(rule)
        item['workItem']['caseInputHashes'][snapshot['id']] = sha256_json(content)
    plan['items'] = [item]
    return plan, slug


def stage_commands(plan_path, work):
    common = [sys.executable, str(DRIVER), '--plan', str(plan_path), '--work-dir', str(work)]
    commands = {phase: common + ['--stage', phase] for phase in ('blind_a', 'blind_b', 'challenge', 'save')}
    if any('--save' in command for command in commands.values()):
        raise ValueError('formal save is forbidden in recovery support')
    return commands


def run_staged(plan_path, work):
    commands = stage_commands(plan_path, work)
    phase_codes = {}
    with (work / 'blind_a-execution.log').open('a') as log_a, (work / 'blind_b-execution.log').open('a') as log_b:
        a = subprocess.Popen(commands['blind_a'], cwd=ROOT, stdout=log_a, stderr=subprocess.STDOUT)
        b = subprocess.Popen(commands['blind_b'], cwd=ROOT, stdout=log_b, stderr=subprocess.STDOUT)
        phase_codes['blind_a'] = a.wait()
        phase_codes['blind_b'] = b.wait()
    code = next((value for value in phase_codes.values() if value != 0), 0)
    with (work / 'execution.log').open('a') as log:
        for phase in ('challenge', 'save'):
            if code != 0:
                break
            if digest(DRIVER) != DRIVER_SHA256:
                raise ValueError('fixed recovery driver changed during native stages')
            phase_codes[phase] = subprocess.run(commands[phase], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode
            code = phase_codes[phase]
    return code, phase_codes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Run native single-question stages, never formal-save')
    parser.add_argument('--limit', type=int, default=None)
    args = parser.parse_args()
    if digest(DRIVER) != DRIVER_SHA256:
        raise ValueError('fixed recovery driver code hash changed')
    state = inventory()
    summary = {key: len(state[key]) for key in ('original', 'current', 'accepted', 'reused', 'pending', 'deferred')}
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if not args.execute:
        return
    outcomes = []
    for index, key in enumerate(state['pending']):
        if args.limit is not None and index >= args.limit:
            break
        # The main serial writer may have accepted this ID since the initial inventory.
        latest = inventory()
        if key not in latest['pending']:
            outcomes.append({'sourceQuestionId': key, 'state': 'reused_or_accepted_during_run'})
            continue
        plan, slug = recovery_plan(key, latest['pending'][key])
        plan_path = BASE / 'recovery-plans-v5' / (slug + '.json')
        freeze(plan_path, plan)
        work = BASE / 'runs-v5' / ('recovery-' + slug)
        work.mkdir(parents=True, exist_ok=True)
        # A previously held/incomplete recovery is evidence, not a retry directory.
        if any(work.glob('*-received.json')) and not (work / 'stage-result.json').exists():
            raise ValueError(f'incomplete native recovery must remain intact: {work}')
        print(key + ': fresh single-question stage', flush=True)
        code, phase_codes = run_staged(plan_path, work)
        outcome = {'sourceQuestionId': key, 'originPlan': str(latest['pending'][key]['path'].relative_to(ROOT)),
                   'plan': str(plan_path.relative_to(ROOT)), 'workDirectory': str(work.relative_to(ROOT)), 'exitCode': code, 'phaseExitCodes': phase_codes}
        if code == 0:
            outcome['results'] = read(work / 'stage-result.json')['results']
        outcomes.append(outcome)
        # Append-only execution receipt, never rewrite older held outcomes.
        freeze(BASE / 'recovery-stage-results-v5' / (slug + '.json'), outcome)
        print(key + ': staged exit=' + str(code), flush=True)


if __name__ == '__main__':
    main()
