"""Explicit immutable publication units. Private plans are never normal patches.

Object hashes use compact sorted UTF-8 JSON without LF. File hashes include the
pretty JSON final LF. Native event hashes explicitly include their stored LF.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from scripts.common.scoped_canonical_context import file_hash, reader_dependencies, SnapshotCorrectionContext
from scripts.merge.patch_views import extract_patch_entries
from tools.question_review_console.projection import sha256_json
from tools.question_review_console.scoped_artifacts import write_json
from tools.question_review_console.scoped_corrections import SnapshotCorrection

UNIT_SCHEMA = 'question-publication-correction/v1'
PLAN_SCHEMA = 'formal-correction-save-plan/v1'
MERGED_DIAGNOSTIC_NAME = 'question_formal_preview_merged.json'
FIRESTORE_DIAGNOSTIC_NAME = 'formal_preview_firestore_scoped.json'
PRIVATE = Path('output/user_feedback_response_system/staging/recovery-three-cases/formal-save-plans/runs')
FIXED = Path('output/user_feedback_response_system/staging/recovery-three-cases/execution-contract/runs/T041-repair1-e1ed4d7c4c27/manifest.json')


def _utcnow():
    return datetime.now(timezone.utc)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def physical(root, relative):
    root = Path(root).resolve()
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts or not rel.parts:
        raise ValueError('relative physical path required')
    path = root / rel
    if path.absolute() != path.resolve() or not path.is_relative_to(root):
        raise ValueError('symlink or escaping path refused')
    return path


def replay(candidate, snapshots):
    """Compare every selected field, including absence; no full converter involved."""
    if not candidate['delta'] or len(candidate['delta']) != len(candidate['projection']):
        raise ValueError('delta/projection coverage differs')
    ids = [d['questionId'] for d in candidate['delta']]
    if len(set(ids)) != len(ids) or set(ids) != set(snapshots):
        raise ValueError('selected ID coverage differs')
    result = []
    for raw, expected in zip(candidate['delta'], candidate['projection'], strict=True):
        delta = SnapshotCorrection.from_mapping(raw)
        selected = snapshots[raw['questionId']]
        if selected['questionId'] != delta.question_id or selected['exists'] is not True or selected['updateTime'] != delta.update_time:
            raise ValueError('snapshot identity/version differs')
        fields = selected['fields']
        projected = {k: deepcopy(v['value']) for k, v in fields.items() if v['present'] is True}
        for key, change in raw['fields'].items():
            if key not in fields or fields[key] != {'present': change['beforePresent'], 'value': change['before']}:
                raise ValueError('delta before/presence differs')
            projected[key] = deepcopy(change['after'])
        if projected != expected:
            raise ValueError('all-selected-field projection differs')
        result.append(projected)
    return result


@dataclass(frozen=True)
class PublicationUnit:
    manifest: dict
    source: dict
    public: dict

    def validate(self):
        m = self.manifest
        if m.get('schemaVersion') != UNIT_SCHEMA or m.get('kind') not in {'official_source_public_variant', 'production_snapshot_delta'}:
            raise ValueError('unit kind/schema differs')
        if not m.get('nativeChainHash') or not m.get('snapshotHash') or not m.get('candidateHash'):
            raise ValueError('unit provenance missing')
        if sha256_json(self.source['snapshot']) != m['snapshotHash']:
            raise ValueError('snapshot binding differs')
        if sha256_json(self.public['candidate']) != m['candidateHash']:
            raise ValueError('candidate binding differs')
        candidate = self.public['candidate']
        docs = replay(candidate, self.source['snapshot'])
        if [d['questionId'] for d in candidate['delta']] != m['publicationIds'] or candidate['delta'] != m['limitedDelta']:
            raise ValueError('unit ID/delta alignment differs')
        if m['kind'] == 'official_source_public_variant':
            records = extract_patch_entries(self.source)
            if len(records) != 1 or self.source.get('role') != 'official_source_noop' or self.public.get('role') != 'public_tf_variant':
                raise ValueError('source/public roles differ')
            source, preserved = records[0], self.source['preservedOriginal']
            if source['questionBodyText'] != preserved['originalQuestionBodyText'] or source['choiceTextList'] != preserved['originalChoiceTextList']:
                raise ValueError('original source body/choices changed')
            answer = 4 if m['caseYear'] == 2018 else 2
            if preserved['originalSelectionAnswer'] != [answer] or candidate['sourceProposal']['answer_result_inferred_correct_choice_numbers'] != [answer]:
                raise ValueError('source selection answer differs')
            if self.source['sourceBinding'] != candidate['sourceProposal']['sourceBinding'] or not self.source['sourceBinding']:
                raise ValueError('source identity differs')
            if self.public['sourceProposal'] != candidate['sourceProposal'] or self.public['lawSidecar'] != candidate['lawSidecar']:
                raise ValueError('public proposal/law sidecar differs')
        else:
            if (m['caseYear'] != 2020 or m['qualification'] != 'kanrigyoumu'
                    or self.source.get('role') != 'production_snapshot' or self.public.get('role') != 'public_snapshot_delta'
                    or self.source.get('sourceGroup') is not None or self.source.get('sourceRef') is not None
                    or self.source.get('publicationQualificationId') != 'condominiummanager'):
                raise ValueError('snapshot falsely bound to official source')
            if len(candidate['delta']) != 1 or set(candidate['delta'][0]['fields']) != {'questionText'}:
                raise ValueError('2020 field scope differs')
            change = candidate['delta'][0]['fields']['questionText']
            if change['before'].count('明らかなたとき') != 1 or change['after'] != change['before'].replace('明らかなたとき', '明らかなとき', 1):
                raise ValueError('2020 one-character delta differs')
        return docs


def read_plan(root, path, *, check_inputs=True):
    root, path = Path(root).resolve(), Path(path)
    if path.absolute() != path.resolve():
        raise ValueError('physical plan required')
    plan = json.loads(path.read_bytes())
    if plan.get('schemaVersion') != PLAN_SCHEMA or plan.get('planHash') != sha256_json({k: v for k, v in plan.items() if k != 'planHash'}) or plan.get('inputHash') != sha256_json(plan['inputs']):
        raise ValueError('plan object hash differs')
    if check_inputs:
        for name, expected in plan['inputs'].items():
            if file_hash(Path(name)) != expected:
                raise ValueError('current plan input drift')
    materials = plan.get('approvalMaterials')
    if not isinstance(materials, dict) or set(materials) != {'approval-proposal.md', 'approval-details.json'}:
        raise ValueError('exact approval materials required')
    for name, expected in materials.items():
        if plan.get('approvalMaterialPaths', {}).get(name) != str(physical(path.parent, name)):
            raise ValueError('approval material link differs')
        if file_hash(physical(path.parent, name)) != expected:
            raise ValueError('approval material bytes changed')
    all_paths = []
    for unit in plan['units']:
        paths = [f['path'] for f in unit['files']]
        if len(paths) != 3 or paths[0].split('/')[:-1] != paths[1].split('/')[:-1] or paths[0].split('/')[:-1] != paths[2].split('/')[:-1]:
            raise ValueError('partial or mixed sibling unit')
        qual = unit['qualification']
        expected_dir = f'output/{qual}/publication_corrections/{unit["unitHash"]}'
        if any(str(Path(p).parent) != expected_dir for p in paths):
            raise ValueError('formal path differs')
        payloads = []
        for entry in unit['files']:
            physical(root, entry['path'])
            private = physical(path.parent, entry['privatePath'])
            if file_hash(private) != entry['sha256']:
                raise ValueError('planned bytes changed')
            payloads.append(json.loads(private.read_bytes()))
        manifest, source, public = payloads
        if manifest['payloadHashes'] != {Path(f['path']).name: f['sha256'] for f in unit['files'][1:]}:
            raise ValueError('payload file hash differs')
        identity_hash = sha256_json({'schemaVersion': UNIT_SCHEMA, 'caseYear': manifest['caseYear'], 'candidateHash': manifest['candidateHash'], 'sourceManifestHash': manifest['sourceManifestHash']})
        if (manifest['unitHash'] != unit['unitHash'] or manifest['qualification'] != qual
                or unit['unitHash'] != identity_hash or manifest['caseYear'] != unit['caseYear']
                or manifest['kind'] != unit['kind'] or manifest['sourceManifestHash'] != plan['sourceManifestHash']):
            raise ValueError('unit scope differs')
        PublicationUnit(manifest, source, public).validate()
        all_paths.extend(paths)
    if len(all_paths) != 9 or len(set(all_paths)) != 9 or all_paths != plan['plannedPaths']:
        raise ValueError('exact nine-file path coverage differs')
    return plan


def prepare_formal_save_plan(root, manifest_path, destination):
    from tools.question_review_console.validated_evidence_import import replay_recovery_native_chain
    root = Path(root).resolve()
    path = Path(manifest_path)
    if not path.is_absolute():
        path = root / path
    if path != root / FIXED:
        raise ValueError('fixed T041 manifest required')
    destination = Path(destination)
    if not destination.is_absolute():
        destination = root / destination
    if destination.absolute() != destination.resolve() or not destination.is_relative_to(root / PRIVATE):
        raise ValueError('private physical destination required')
    if (destination / 'formal-save-plan.json').exists():
        raise ValueError('fresh generation required')
    fixed = json.loads(path.read_bytes())
    if fixed['manifestHash'] != sha256_json({k: v for k, v in fixed.items() if k != 'manifestHash'}):
        raise ValueError('fixed manifest differs')
    notes = root / 'docs/goals/feedback-correction-recovery/notes'
    judge_path = notes / 'T045-formal-contract-receipt.json'
    judge = json.loads(judge_path.read_bytes())
    provenance = json.loads((notes / 'T045-formal-contract-provenance.json').read_bytes())
    line = Path(provenance['sessionPath']).read_bytes().splitlines(keepends=True)[provenance['eventLine'] - 1]
    event = json.loads(line)
    output = ''.join(c.get('text', '') for c in event['payload'].get('content', []) if c.get('type') == 'output_text')
    if (digest(line) != provenance['eventSha256IncludingFinalLf'] or event['payload'].get('role') != 'assistant'
            or digest(output.encode()) != provenance['outputSha256'] or file_hash(judge_path) != provenance['receiptFileSha256']
            or json.loads(output)['goalbuddy_receipt_v1'] != judge):
        raise ValueError('genuine T045 native binding differs')
    authority = next(e for e in judge['evidence'] if e['kind'] == 'fixed_candidate_authority')
    contract = next(e for e in judge['evidence'] if e['kind'] == 'planned_formal_files')
    if fixed['manifestHash'] != authority['manifestHash']:
        raise ValueError('Judge fixed manifest authority differs')
    chain = [{k: v for k, v in e.items() if k != 'receipt'} for e in replay_recovery_native_chain(root)]
    if sha256_json(chain) != fixed['nativeChainHash'] or chain != fixed['nativeChain']:
        raise ValueError('native chain differs')
    inputs, drift = {}, {}
    for name, old in fixed['inputs'].items():
        current = file_hash(Path(name))
        relative = Path(name).relative_to(root) if Path(name).is_relative_to(root) else None
        code = relative is not None and (str(relative).startswith('scripts/') or str(relative).startswith('tools/question_review_console/'))
        current_case_review = (relative is not None and relative.parts[:3] == ('output', 'user_feedback_response_system', 'cases') and relative.name == 'case_review.json' and len(relative.parts) == 5)
        if current != old and not code and not current_case_review:
            raise ValueError('fixed primary evidence changed')
        if current != old:
            drift[name] = {'historicalHash': old, 'currentHash': current, 'kind': 'T044_current_case_review' if current_case_review else 'current_reader_dependency'}
        inputs[name] = current
    for file in (path, judge_path, notes / 'T045-formal-contract-provenance.json'):
        inputs[str(file)] = file_hash(file)
    for dep in reader_dependencies(root, [root / 'tools/question_review_console/formal_correction_save.py', root / 'tools/question_review_console/inventory.py', root / 'tools/question_review_console/workflow_runner.py', root / 'scripts/pipeline/prepare_scoped_question_artifacts.py']):
        inputs[str(dep)] = file_hash(dep)
    inputs[str(root / 'config/question_maintenance_workflow.toml')] = file_hash(root / 'config/question_maintenance_workflow.toml')
    units = []
    for selected in contract['units']:
        year = str(selected['year'])
        entry = fixed['cases'][year]
        candidate_path = path.parent / year / 'candidate.json'
        snapshot_path = path.parent / year / 'live-selected-snapshot.json'
        candidate, snapshots = json.loads(candidate_path.read_bytes()), json.loads(snapshot_path.read_bytes())
        if file_hash(candidate_path) != entry['candidateFileHash'] or sha256_json(candidate) != authority['candidateHashes'][year] or sha256_json(snapshots) != entry['liveSnapshotHash']:
            raise ValueError('fixed candidate/snapshot changed')
        replay(candidate, snapshots)
        inputs[str(candidate_path)] = file_hash(candidate_path)
        inputs[str(snapshot_path)] = file_hash(snapshot_path)
        qualification = 'kanrigyoumu' if year == '2020' else '2nd-class-kenchikushi'
        unit_hash = sha256_json({'schemaVersion': UNIT_SCHEMA, 'caseYear': int(year), 'candidateHash': entry['candidateHash'], 'sourceManifestHash': fixed['manifestHash']})
        names = ('production-snapshot.json', 'public-delta.json') if year == '2020' else ('source-envelope.json', 'public-variant.json')
        paths = [f'output/{qualification}/publication_corrections/{unit_hash}/{name}' for name in ('manifest.json', *names)]
        if paths != selected['plannedPaths']:
            raise ValueError('Judge planned paths differ')
        if year == '2020':
            source = {'role': 'production_snapshot', 'sourceGroup': None, 'sourceRef': None, 'publicationQualificationId': 'condominiummanager', 'snapshot': snapshots}
            public = {'role': 'public_snapshot_delta', 'candidate': candidate}
        else:
            preview_path = path.parent / year / 'source-patch-preview.json'
            preview = json.loads(preview_path.read_bytes())
            inputs[str(preview_path)] = file_hash(preview_path)
            group = '85004' if year == '2018' else '85010'
            context = SnapshotCorrectionContext(root, root / ('output/user_feedback_response_system/staging/recovery-source-' + year), qualification, group, candidate['sourceProposal']['sourceBinding'], list(snapshots.values()))
            inputs.update(context.files)
            source = {'role': 'official_source_noop', 'sourceBinding': context.binding.as_mapping(), 'question_bodies': [deepcopy(context.source.record)],
                'preservedOriginal': {k: preview[k] for k in ('originalQuestionBodyText', 'originalChoiceTextList', 'originalAnswerText', 'originalSelectionAnswer')}, 'snapshot': snapshots}
            public = {'role': 'public_tf_variant', 'candidate': candidate, 'sourceProposal': candidate['sourceProposal'], 'lawSidecar': candidate['lawSidecar']}
        manifest = {'schemaVersion': UNIT_SCHEMA, 'unitHash': unit_hash, 'caseYear': int(year), 'kind': selected['kind'], 'qualification': qualification,
            'sourceManifestHash': fixed['manifestHash'], 'candidateHash': entry['candidateHash'], 'snapshotHash': entry['liveSnapshotHash'], 'nativeChainHash': fixed['nativeChainHash'], 'nativeChain': chain,
            'publicationIds': [d['questionId'] for d in candidate['delta']], 'limitedDelta': candidate['delta'], 'transportContext': entry['transportContext'],
            'payloadHashes': {names[0]: digest(json_bytes(source)), names[1]: digest(json_bytes(public))}}
        PublicationUnit(manifest, source, public).validate()
        files = []
        for relative, payload in zip(paths, (manifest, source, public), strict=True):
            private = 'planned-files/' + relative
            write_json(physical(destination, private), payload)
            target = physical(root, relative)
            files.append({'path': relative, 'privatePath': private, 'sha256': digest(json_bytes(payload)), 'targetExists': target.exists(), 'targetHash': file_hash(target) if target.exists() else None})
        units.append({'caseYear': int(year), 'kind': selected['kind'], 'qualification': qualification, 'unitHash': unit_hash, 'files': files})
    plan = {'schemaVersion': PLAN_SCHEMA, 'createdAt': _utcnow().isoformat(), 'sourceManifestHash': fixed['manifestHash'], 'sourceManifestFileHash': file_hash(path), 'nativeChainHash': fixed['nativeChainHash'],
        'inputs': inputs, 'inputHash': sha256_json(inputs), 'historicalDependencyDrift': drift, 'units': units,
        'plannedPaths': [f['path'] for u in units for f in u['files']], 'formalSaveApproved': False, 'formalDataUpdated': False, 'publicationReady': False,
        'evaluationPerformed': False, 'remaining': ['human_exact_plan_approval_missing', 'current_law_revision_recheck_missing', 'current_checkpoints_and_evaluation_missing', 'production_permission_missing']}
    proposal = ['# 正式保存承認案（未承認）', '', '資料hashは外側のplan/固定質問に結合（循環参照なし）。',
        'input hash: ' + plan['inputHash'], '',
        '2018/2024 sourceは原問本文・肢順・答4/2を保持するno-op。公開TF差分はpublic-variantのみ。2020はproduction snapshot、source group/ref=nullでquestionText一文字だけ。', '',
        '以下の全9fileのexact bytesをplanned-filesで確認してください。保存stageはqualification/publication_correctionsの型付きunitで、既存18/21/24/05へ移送しません。', '']
    appendix = {}
    for unit in units:
        proposal.append('## ' + str(unit['caseYear']))
        proposal.extend('- ' + f['path'] + ' SHA256 ' + f['sha256'] for f in unit['files'])
        payload = json.loads(physical(destination, unit['files'][2]['privatePath']).read_bytes())
        if unit['caseYear'] != 2020:
            source = json.loads(physical(destination, unit['files'][1]['privatePath']).read_bytes())
            proposal.extend(['', '原問source（before=after、no-op）:', '```json', json.dumps(source['preservedOriginal'], ensure_ascii=False, indent=2), '```'])
        candidate = payload['candidate']
        appendix[str(unit['caseYear'])] = {'sourceProposal': candidate.get('sourceProposal'), 'lawSidecar': candidate.get('lawSidecar'), 'delta': candidate['delta']}
        for index, delta in enumerate(candidate['delta'], 1):
            proposal.extend(['', '### 肢 ' + str(index) + '（既存ID/肢順保持）'])
            for field, change in delta['fields'].items():
                if field in {'lawReferences', 'lawRevisionFacts'}:
                    proposal.append('- ' + field + ': before/after全文は approval-details.json の該当年度/肢/field。既受理T041値と完全一致。')
                else:
                    proposal.extend(['#### ' + field, '', 'before（present=' + str(change['beforePresent']) + '）:', '```json', json.dumps(change['before'], ensure_ascii=False, indent=2), '```', 'after:', '```json', json.dumps(change['after'], ensure_ascii=False, indent=2), '```'])
            refs = candidate.get('sourceProposal', {}).get('lawReferences', [])
            if refs:
                proposal.append('一次根拠（出題時/2026-10-03審査、保存前に現在版再照合）:')
                proposal.extend('- ' + ref['lawTitle'] + ' ' + str(ref.get('article', '')) + ' ' + str(ref.get('paragraph', '')) + ' / ' + ref['sourceUrl'] for ref in refs[index - 1])
            proposal.append('影響: 宣言したdelta fieldsだけ。原問・original fields・未知selected fields・欠落fieldのpresenceは保存。正誤scalarは原選択答と別責務。')
    proposal.extend(['## 根拠と未完条件', '', 'native chain hash: ' + plan['nativeChainHash'],
        '固定T041 candidate/manifestと真正T028/T029/T033/T037/T039、T045 native provenanceを新planへ結合。新法令審査やcheckpoint成功ではありません。',
        '承認は固定質問と全plan/file/pathへ結合した真正native日本語回答が必要です。質問はhuman-approval-request.jsonに未送信のまま保存しました。',
        '現在法の取得日時・URL・revision・XML/bodyhash再照合は未済。2026-10-03審査を今日現在の確認とは扱いません。真正checkpoint・model評価・本番承認・公開は未済です。',
        '2020は公式rawと改題snapshotの同一source結合を主張しません。local評価namespaceは正式groupではありません。',
        '2025の既存base/amendment承認をこの3件へ拡張しません。現case reviewはT044更新後hashに固定し、以後の変化は保存拒否します。', ''])
    (destination / 'approval-proposal.md').write_text('\n'.join(proposal), encoding='utf-8')
    (destination / 'approval-proposal.md').chmod(0o600)
    write_json(destination / 'approval-details.json', appendix)
    plan['approvalMaterials'] = {name: file_hash(destination / name) for name in ('approval-proposal.md', 'approval-details.json')}
    plan['approvalMaterialPaths'] = {name: str(destination / name) for name in plan['approvalMaterials']}
    plan['planHash'] = sha256_json(plan)
    write_json(destination / 'formal-save-plan.json', plan)
    read_plan(root, destination / 'formal-save-plan.json')
    write_json(destination / 'human-approval-request.json', {'questions': [{'title': approval_question(plan), 'options': ['この計画の正式保存を承認します', '保存しない']}], 'sent': False})
    return plan


def _native_event(record, prefix='event'):
    session = Path(record['sessionPath'])
    if session.absolute() != session.resolve() or not session.is_file():
        raise ValueError('physical native session required')
    number = record[prefix + 'Line']
    if type(number) is not int or number < 1:
        raise ValueError('native line number invalid')
    line = session.read_bytes().splitlines(keepends=True)[number - 1]
    if digest(line) != record[prefix + 'Sha256IncludingFinalLf']:
        raise ValueError('native event bytes differ')
    return json.loads(line), line


def approval_question(plan):
    """Self-contained human decision; full file binding stays inside plan/scope."""
    materials = plan['approvalMaterialPaths']
    return ('次の3件について、原問と既存IDを保持する限定修正unitの正式保存を承認しますか。\n\n'
        '- 2018：肢2を「正しい→間違い」、肢4を「間違い→正しい」へ修正。原問の選択答4・本文・肢順は保持。公開TF命題と日本語解説・法令refs/factsは別payload。\n'
        '- 2024：「必要がない」という否定形の原問・選択答2を保持。肯定形の公開TF variantを別に保存し、解説・一次法令refs/factsを結合。原問を肯定形で上書きしません。\n'
        '- 2020：公開questionTextの「明らかなたとき→明らかなとき」一文字削除だけ。original fieldsは保持、source group/ref=nullのproduction snapshotとして扱います。\n\n'
        '[年度・肢・field別のbefore/afterと一次根拠](' + materials['approval-proposal.md'] + ')\n'
        '[refs/facts・全deltaの正確な付録](' + materials['approval-details.json'] + ')\n\n'
        '対象は資料に示す全9fileのexact bytes/pathです。正式保存の承認と本番公開の承認は別です。'
        '未検証gateの成功や本番更新をこの回答だけで許可しません。\n'
        '計画 SHA256: ' + plan['planHash'] + '\n'
        '差分資料 SHA256: ' + plan['approvalMaterials']['approval-proposal.md'] + '\n'
        '付録 SHA256: ' + plan['approvalMaterials']['approval-details.json'] + '\n\n'
        '正式保存を承認する場合は「この計画の正式保存を承認します」と回答してください。')


def _native_approval(record, plan):
    """Replay a fixed native question and its explicit Japanese human response."""
    required = {'sessionPath', 'requestLine', 'requestSha256IncludingFinalLf', 'eventLine', 'eventSha256IncludingFinalLf'}
    if not isinstance(record, dict) or set(record) != required or record['eventLine'] <= record['requestLine']:
        raise ValueError('native question and human reply required')
    request, request_bytes = _native_event(record, 'request')
    message, message_bytes = _native_event(record)
    payload = request.get('payload', {})
    tools = {'functions.request_user_input_async', 'functions.request_user_input', 'request_user_input_async', 'request_user_input'}
    if request.get('type') != 'response_item' or payload.get('type') != 'function_call' or payload.get('name') not in tools:
        raise ValueError('native user-input request required')
    args = json.loads(payload['arguments'])
    questions = args.get('questions')
    if not isinstance(questions, list) or len(questions) != 1 or not payload.get('call_id'):
        raise ValueError('one unambiguous fixed approval question required')
    question = questions[0]
    title = question.get('title', question.get('question'))
    if title != approval_question(plan):
        raise ValueError('fixed question plan/path/hash binding differs')
    reply = message.get('payload', {})
    if message.get('type') != 'response_item' or reply.get('type') != 'message' or reply.get('role') != 'user':
        raise ValueError('direct human reply required')
    raw = ''.join(c.get('text', '') for c in reply.get('content', []) if c.get('type') == 'input_text')
    tag = '<send_user_message_question_reply>'
    end_tag = '</send_user_message_question_reply>'
    allowed = {'この計画の正式保存を承認します', 'この差分を承認する'}
    if raw.startswith(tag) and raw.endswith(end_tag):
        answers = json.loads(raw[len(tag):-len(end_tag)])
        if not isinstance(answers, list) or len(answers) != 1:
            raise ValueError('ambiguous approval UI answers')
        item = answers[0]
        question_id = json.loads(item['questionItemId'])
        tool_name = payload['name'].removeprefix('functions.')
        if question_id != [tool_name, payload['call_id'], 0] or item['question'] != title:
            raise ValueError('UI reply call/index/question differs')
        text = item['answer']
        options = question.get('options', [])
        labels = {o if isinstance(o, str) else o.get('label') for o in options}
        if text not in allowed or (options and text not in labels):
            raise ValueError('explicit formal-save approval missing')
    else:
        # Free text has no questionItemId: reject intervening questions.
        lines = Path(record['sessionPath']).read_bytes().splitlines()
        for line in lines[record['requestLine']:record['eventLine'] - 1]:
            e = json.loads(line)
            if e.get('payload', {}).get('name') in tools:
                raise ValueError('free-text answer has ambiguous question association')
        text = raw
        if text != 'この計画の正式保存を承認します':
            raise ValueError('explicit human formal-save reply missing')
    return {'requestHash': digest(request_bytes), 'eventHash': digest(message_bytes), 'eventTimestamp': message['timestamp'],
        'messageHash': digest(text.encode()), 'includesFinalLf': True}


def _law_corpus(plan):
    """Current fixed XML is stored in bound primary-law cache JSON, not .xml."""
    import xml.etree.ElementTree as ET
    result = {}
    for name, file_digest in plan['inputs'].items():
        if '/law-cache/' not in name or not name.endswith('/2026-10-03.json'):
            continue
        value = json.loads(Path(name).read_bytes())
        if value.get('schemaVersion') != 'primary-law-file-cache/v1' or value['asOf'] != '2026-10-03' or digest(value['xmlText'].encode()) != value['xmlHash']:
            raise ValueError('fixed XML provenance differs')
        ET.fromstring(value['xmlText'])
        key = value['lawId']
        if key in result and (result[key]['xmlHash'], result[key]['revisionId']) != (value['xmlHash'], value['revisionId']):
            raise ValueError('fixed law corpus duplicate conflict')
        result[key] = {**value, 'fixedFilePath': name, 'fixedFileHash': file_digest}
    if not result:
        raise ValueError('fixed current law corpus missing')
    return result


def _law_recheck(record, plan, *, require_fresh=True):
    """Reproduce dated HTTP XML/revision comparisons. A human assertion fails."""
    import xml.etree.ElementTree as ET
    from urllib.parse import urlsplit, parse_qs
    if not isinstance(record, dict) or set(record) != {'evidencePath', 'sha256'}:
        raise ValueError('retrieval evidence file required')
    path = Path(record['evidencePath'])
    if file_hash(path) != record['sha256']:
        raise ValueError('law retrieval evidence bytes differ')
    evidence = json.loads(path.read_bytes())
    if (evidence.get('schemaVersion') != 'formal-current-law-retrieval/v1' or evidence.get('planHash') != plan['planHash']
            or evidence.get('inputHash') != plan['inputHash']):
        raise ValueError('law retrieval plan binding differs')
    corpus = _law_corpus(plan)
    entries = evidence.get('entries')
    if not isinstance(entries, list) or len(entries) != len(corpus) or {e['lawId'] for e in entries} != set(corpus):
        raise ValueError('law recheck coverage differs')
    comparisons = []
    for entry in entries:
        fixed = corpus[entry['lawId']]
        fetched = datetime.fromisoformat(entry['retrievedAt'].replace('Z', '+00:00'))
        created = datetime.fromisoformat(plan['createdAt'].replace('Z', '+00:00'))
        now = _utcnow()
        if fetched.tzinfo is None or fetched < created or fetched > now or (require_fresh and fetched.astimezone(timezone.utc).date() != now.date()):
            raise ValueError('fresh dated law retrieval required')
        parts = urlsplit(entry['url'])
        if (parts.scheme != 'https' or parts.netloc != 'laws.e-gov.go.jp' or parts.fragment
                or parts.path != '/api/2/law_file/xml/' + entry['lawId']
                or parse_qs(parts.query) != {'asof': [entry['referenceDate']]} or entry['referenceDate'] != fetched.date().isoformat()):
            raise ValueError('current primary XML retrieval URL/date differs')
        metadata_parts = urlsplit(entry['metadataUrl'])
        if (metadata_parts.scheme != 'https' or metadata_parts.netloc != 'laws.e-gov.go.jp' or metadata_parts.fragment
                or metadata_parts.path != '/api/2/law_data/' + entry['lawId']
                or parse_qs(metadata_parts.query) != {'asof': [entry['referenceDate']]}):
            raise ValueError('dated law_data metadata URL differs')
        metadata_path = Path(entry['metadataResponsePath'])
        if file_hash(metadata_path) != entry['metadataResponseSha256'] or entry['metadataHttpStatus'] != 200:
            raise ValueError('law_data metadata response hash/status differs')
        metadata = json.loads(metadata_path.read_bytes())
        semantic_revision = metadata['revision_info']['law_revision_id']
        if metadata['law_info']['law_id'] != entry['lawId'] or semantic_revision != entry['revisionId']:
            raise ValueError('metadata semantic law/revision differs')
        response_path = Path(entry['responsePath'])
        if file_hash(response_path) != entry['responseSha256'] or entry.get('httpStatus') != 200:
            raise ValueError('retrieved HTTP XML bytes/status differ')
        xml = response_path.read_text(encoding='utf-8')
        ET.fromstring(xml)
        main = ET.fromstring(xml).findall('.//MainProvision')
        if len(main) != 1:
            raise ValueError('current XML MainProvision ambiguous')
        body_hash = digest(ET.tostring(main[0], encoding='utf-8'))
        fixed_main = ET.fromstring(fixed['xmlText']).findall('.//MainProvision')
        if len(fixed_main) != 1 or entry['bodyHash'] != body_hash:
            raise ValueError('law body hash differs')
        # Full XML plus revision equality is stricter than a body-only declaration.
        if (entry['fixedXmlHash'] != fixed['xmlHash'] or entry['fixedRevisionId'] != fixed['revisionId']
                or entry['xmlHash'] != digest(xml.encode()) or entry['xmlHash'] != fixed['xmlHash']
                or entry['revisionId'] != fixed['revisionId'] or body_hash != digest(ET.tostring(fixed_main[0], encoding='utf-8'))):
            raise ValueError('current law revision/body changed; stop for review')
        comparisons.append({'lawId': entry['lawId'], 'retrievedAt': entry['retrievedAt'], 'url': entry['url'],
            'revisionId': entry['revisionId'], 'xmlHash': entry['xmlHash'], 'bodyHash': body_hash,
            'fixedFileHash': fixed['fixedFileHash'], 'responseSha256': entry['responseSha256'],
            'metadataUrl': entry['metadataUrl'], 'metadataResponseSha256': entry['metadataResponseSha256'], 'semanticRevisionId': semantic_revision})
    return {'evidenceFileHash': record['sha256'], 'comparisons': comparisons}


def _rename_exclusive(source, target):
    """Atomic no-replace directory finalize; fail closed on unsupported systems."""
    import ctypes
    import sys
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == 'darwin':
        function = libc.renamex_np
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = function(os.fsencode(source), os.fsencode(target), 0x00000004)  # RENAME_EXCL
    elif sys.platform.startswith('linux') and hasattr(libc, 'renameat2'):
        function = libc.renameat2
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = function(-100, os.fsencode(source), -100, os.fsencode(target), 1)  # RENAME_NOREPLACE
    else:
        raise ValueError('atomic exclusive finalize unsupported')
    if result:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))


def approval_scope(plan):
    return {'schemaVersion': 'formal-correction-human-approval/v1', 'action': 'save_immutable_publication_units', 'planHash': plan['planHash'],
        'inputHash': plan['inputHash'], 'files': {f['path']: f['sha256'] for u in plan['units'] for f in u['files']}, 'unitHashes': [u['unitHash'] for u in plan['units']], 'approvalMaterials': plan['approvalMaterials'], 'approvalMaterialPaths': plan['approvalMaterialPaths']}


def save_formal_units(root, plan_path, *, human_approval, law_recheck, receipt_path, failure_hook=None):
    """Explicit API only. Caller supplies human native approval and dated law HTTP retrieval evidence.

    No production write, checkpoint or evaluation is performed by this API.
    A directory lacking the final save receipt is deliberately not loadable.
    """
    root, plan_path = Path(root).resolve(), Path(plan_path)
    plan = read_plan(root, plan_path)
    if root == Path(__file__).resolve().parents[2]:
        if not isinstance(human_approval, dict) or not Path(human_approval.get('sessionPath', '')).is_relative_to(Path.home() / '.codex/sessions'):
            raise ValueError('production repository requires native Codex session provenance')
    approved = _native_approval(human_approval, plan)
    receipt_path = Path(receipt_path)
    if not receipt_path.is_absolute():
        receipt_path = root / receipt_path
    if receipt_path.absolute() != receipt_path.resolve() or not receipt_path.is_relative_to(root) or str(receipt_path) in plan['inputs']:
        raise ValueError('physical receipt destination required')
    if receipt_path in [root / f['path'] for u in plan['units'] for f in u['files']]:
        raise ValueError('receipt cannot replace approved payload')
    if receipt_path.exists():
        existing_receipt = json.loads(receipt_path.read_bytes())
        if existing_receipt.get('planHash') != plan['planHash']:
            raise ValueError('existing receipt belongs to a different plan')
        load_saved_units(root, plan_path, receipt_path)
        if existing_receipt['humanApprovalBinding'] != approved or existing_receipt.get('lawRecheckEvidence') != law_recheck:
            raise ValueError('immutable save receipt evidence conflict')
        return existing_receipt
    law_binding = _law_recheck(law_recheck, plan)
    hook = failure_hook or (lambda phase, unit: None)
    # Preflight every sibling before any writes.
    for unit in plan['units']:
        target = physical(root, str(Path(unit['files'][0]['path']).parent))
        if target.exists():
            if not target.is_dir() or set(p.name for p in target.iterdir()) != {Path(f['path']).name for f in unit['files']}:
                raise ValueError('existing partial/conflicting unit refused')
            for f in unit['files']:
                if file_hash(physical(root, f['path'])) != f['sha256']:
                    raise ValueError('immutable existing unit conflict')
        elif any(f['targetExists'] for f in unit['files']):
            raise ValueError('planned target disappeared')
    for unit in plan['units']:
        target = physical(root, str(Path(unit['files'][0]['path']).parent))
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        staging = Path(tempfile.mkdtemp(prefix='.formal-staging-', dir=target.parent))
        try:
            for f in unit['files']:
                source = physical(plan_path.parent, f['privatePath'])
                child = staging / Path(f['path']).name
                approved_bytes = source.read_bytes()
                if digest(approved_bytes) != f['sha256']:
                    raise ValueError('approved private bytes drift during staging')
                child.write_bytes(approved_bytes)
                child.chmod(0o600)
            hook('before_rename', unit['unitHash'])
            # Recheck staged bytes and current inputs after the last hook.
            read_plan(root, plan_path)
            for f in unit['files']:
                if file_hash(staging / Path(f['path']).name) != f['sha256']:
                    raise ValueError('staged approved bytes changed before finalize')
            # Competing publication must never be replaced, including empty dirs.
            _rename_exclusive(staging, target)
            hook('after_rename', unit['unitHash'])
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    receipt = {'schemaVersion': 'formal-correction-save-receipt/v1', 'planHash': plan['planHash'], 'inputHash': plan['inputHash'],
        'files': approval_scope(plan)['files'], 'humanApprovalBinding': approved, 'lawRecheckBinding': law_binding,
        'humanApprovalEvidence': human_approval, 'lawRecheckEvidence': law_recheck,
        'result': 'saved', 'checkpointRecorded': False, 'evaluationPerformed': False, 'publicationReady': False}
    for f in [f for unit in plan['units'] for f in unit['files']]:
        if file_hash(physical(root, f['path'])) != f['sha256']:
            raise ValueError('finalized immutable file differs')
    hook('before_receipt', plan['planHash'])
    read_plan(root, plan_path)
    for f in [f for unit in plan['units'] for f in unit['files']]:
        if file_hash(physical(root, f['path'])) != f['sha256']:
            raise ValueError('saved bytes changed before receipt finalize')
    # Exclusive file link finalizes the receipt without replacing a race winner.
    receipt_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_receipt = tempfile.mkstemp(prefix='.formal-receipt-', dir=receipt_path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(json_bytes(receipt))
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp_receipt, receipt_path)
    finally:
        os.unlink(temp_receipt)
    hook('after_receipt', plan['planHash'])
    load_saved_units(root, plan_path, receipt_path)
    return receipt


def load_saved_units(root, plan_path, receipt_path):
    plan = read_plan(root, plan_path)
    receipt = json.loads(Path(receipt_path).read_bytes())
    if (receipt.get('schemaVersion') != 'formal-correction-save-receipt/v1' or receipt.get('result') != 'saved'
            or receipt.get('planHash') != plan['planHash'] or receipt.get('inputHash') != plan['inputHash']
            or receipt.get('files') != approval_scope(plan)['files'] or not receipt.get('humanApprovalBinding') or not receipt.get('lawRecheckBinding')):
        raise ValueError('approved immutable save receipt required')
    approved = _native_approval(receipt.get('humanApprovalEvidence'), plan)
    law = _law_recheck(receipt.get('lawRecheckEvidence'), plan, require_fresh=False)
    if approved != receipt['humanApprovalBinding'] or law != receipt['lawRecheckBinding']:
        raise ValueError('saved native binding differs')
    result = []
    for unit in plan['units']:
        payloads = []
        for f in unit['files']:
            path = physical(root, f['path'])
            if file_hash(path) != f['sha256']:
                raise ValueError('saved immutable bytes differ')
            payloads.append(json.loads(path.read_bytes()))
        typed = PublicationUnit(*payloads)
        typed.validate()
        result.append(typed)
    return result


def write_verification_input(directory, stage, records):
    """Generate and discover the actual strict input through its existing reader."""
    from scripts.check.check_law_revision_fact_coverage import latest_merged_files, latest_firestore_file
    directory = Path(directory)
    if stage == 'merged':
        output = directory / '30_merged_2' / MERGED_DIAGNOSTIC_NAME
        write_json(output, {'question_bodies': records})
        if latest_merged_files(directory) != [output]:
            raise ValueError('generated merged input is not uniquely discoverable')
    elif stage == 'firestore':
        output = directory / '40_convert' / FIRESTORE_DIAGNOSTIC_NAME
        write_json(output, {'questions': records})
        if latest_firestore_file(directory) != output:
            raise ValueError('generated firestore input is not discoverable')
    else:
        raise ValueError('unknown strict stage')
    return output


def verify_formal_plan(root, plan_path):
    """Required/alignment/coverage on replayed inputs; strict only on dependency drift."""
    import contextlib
    import io
    from scripts.check.check_choice_text_alignment import check_file
    from scripts.check.check_correct_choice_patch_coverage import compare_entries
    from scripts.check.check_law_revision_fact_coverage import run as strict_check
    from scripts.common.requirements import load_requirements, get_stage_rules, validate_records
    root, plan_path = Path(root).resolve(), Path(plan_path)
    plan = read_plan(root, plan_path)
    fixed = json.loads((root / FIXED).read_bytes())
    strict_dependencies = reader_dependencies(root, [root / 'scripts/check/check_law_revision_fact_coverage.py'])
    drift = {str(p): {'historicalHash': fixed['inputs'].get(str(p)), 'currentHash': file_hash(p)} for p in strict_dependencies if fixed['inputs'].get(str(p)) != file_hash(p)}
    results = []
    for entry in plan['units']:
        payloads = [json.loads(physical(plan_path.parent, f['privatePath']).read_bytes()) for f in entry['files']]
        unit = PublicationUnit(*payloads)
        documents = [{**p, 'questionId': d['questionId']} for p, d in zip(unit.validate(), unit.manifest['limitedDelta'], strict=True)]
        year = str(entry['caseYear'])
        if year == '2020':
            results.append({'year': year, 'selectedCount': 1, 'allFieldsReplay': True, 'oneCharacterDelta': True, 'sourceGroup': None, 'sourceRef': None})
            continue
        # This diagnostic is an unchanged, already-reviewed shape, not a save payload.
        old_file = root / FIXED.parent / year / 'verification/merged/30_merged_2/question_candidate_merged.json'
        merged_data = json.loads(old_file.read_bytes())
        merged = extract_patch_entries(merged_data)[0]
        proposal = unit.public['sourceProposal']
        for field in ('correctChoiceText', 'questionIntent', 'explanationText', 'lawReferences', 'lawRevisionFacts'):
            if merged[field] != proposal[field]:
                raise ValueError('historical strict input shape differs from typed public payload')
        if merged['questionBodyText'] != proposal['publicBodyProposal']:
            raise ValueError('historical public body differs')
        errors, warnings = compare_entries(extract_patch_entries(unit.source), [{**merged, **proposal}], require_full=True, require_snippets=False, require_change_meta=False)
        if errors:
            raise ValueError('source/public correct-choice coverage failed')
        for stage, records, array in (('merged', [merged], 'question_bodies'), ('firestore', documents, 'questions')):
            started = _utcnow().isoformat()
            directory = plan_path.parent / 'verification' / year / stage
            output = write_verification_input(directory, stage, records)
            rules = get_stage_rules(load_requirements(), stage=stage, record_array=array, qualification='2nd-class-kenchikushi')
            required = validate_records(records=records, rules=rules, source_path=output)
            alignment = check_file(output) if stage == 'merged' else []
            historical_file = root / FIXED.parent / year / 'verification' / stage / ('30_merged_2/question_candidate_merged.json' if stage == 'merged' else '40_convert/candidate_firestore_scoped.json')
            if json.loads(historical_file.read_bytes()) != {array: records}:
                raise ValueError('strict shape drift requires separately reviewed input')
            code = None
            stdout = ''
            if True:  # Fresh formal reader requires all four actual strict gates.
                report_path = directory / 'strict-law.json'
                try:
                    with contextlib.redirect_stdout(io.StringIO()) as capture:
                        code = strict_check(list_group_dir=directory, stage=stage, require_all_law_related=True, fail_on_hold=True,
                            require_evidence_summary=True, require_law_references=True, require_current_correct_choice=True,
                            require_verified_law_references=True, require_public_law_evidence=True, original_question_ids=[], report=report_path)
                finally:
                    if report_path.is_file():
                        report_path.chmod(0o600)
                stdout = capture.getvalue()
            result = {'year': year, 'stage': stage, 'startedAt': started, 'finishedAt': _utcnow().isoformat(),
                'inputHash': sha256_json(records), 'historicalFileHash': file_hash(historical_file), 'strictDependencyDrift': drift,
                'strictPerformed': True, 'strictExitCode': code, 'strictHistoricalOnly': False, 'stdout': stdout,
                'requiredErrors': required, 'alignmentErrors': alignment, 'coverageErrors': errors, 'coverageWarnings': warnings,
                'allFieldsReplay': True, 'selectedCount': len(documents), 'sourceSelectionAnswer': unit.source['preservedOriginal']['originalSelectionAnswer']}
            results.append(result)
            write_json(plan_path.parent / 'verification-results.json', {'complete': False, 'results': results})
            if code not in (None, 0):
                raise ValueError('new private strict failed; hard stop')
            if required or alignment:
                raise ValueError('required/alignment gate failed')
    read_plan(root, plan_path)
    write_json(plan_path.parent / 'verification-results.json', {'complete': True, 'results': results})
    return results



def retrieve_current_law_evidence(root, plan_path, destination, *, opener=None):
    """Read-only official HTTP retrieval; save original responses before comparing."""
    import urllib.request
    import xml.etree.ElementTree as ET
    root, destination = Path(root).resolve(), Path(destination)
    if destination.absolute() != destination.resolve() or not destination.is_relative_to(Path(plan_path).parent):
        raise ValueError('law retrieval requires physical private plan destination')
    if destination.exists():
        raise ValueError('fresh law retrieval destination required')
    plan = read_plan(root, plan_path)
    corpus = _law_corpus(plan)
    entries = []
    evidence_path = destination / 'law-retrieval.json'
    http_open = opener or urllib.request.urlopen
    for law_id, fixed in sorted(corpus.items()):
        now = _utcnow()
        date = now.date().isoformat()
        urls = {'metadata': 'https://laws.e-gov.go.jp/api/2/law_data/' + law_id + '?asof=' + date,
            'xml': 'https://laws.e-gov.go.jp/api/2/law_file/xml/' + law_id + '?asof=' + date}
        captures = {}
        for kind, url in urls.items():
            request = urllib.request.Request(url, headers={'User-Agent': 'exam-scraper-question-maintenance/1'})
            with http_open(request, timeout=45) as response:
                raw = response.read()
                status = response.status
                acquired = _utcnow().isoformat()
                headers = dict(response.headers.items())
            target = destination / law_id / ('law-data-response.json' if kind == 'metadata' else 'law-response.xml')
            write_json(target.with_suffix(target.suffix + '.http.json'), {'url': url, 'retrievedAt': acquired, 'status': status, 'headers': headers, 'responseSha256': digest(raw)})
            target.write_bytes(raw); target.chmod(0o600)
            captures[kind] = {'path': target, 'hash': digest(raw), 'status': status, 'retrievedAt': acquired}
        metadata = json.loads(captures['metadata']['path'].read_bytes())
        xml = captures['xml']['path'].read_text(encoding='utf-8')
        main = ET.fromstring(xml).findall('.//MainProvision')
        if len(main) != 1:
            raise ValueError('retrieved MainProvision ambiguous')
        entries.append({'lawId': law_id, 'retrievedAt': captures['xml']['retrievedAt'], 'referenceDate': date,
            'url': urls['xml'], 'httpStatus': captures['xml']['status'], 'responsePath': str(captures['xml']['path']), 'responseSha256': captures['xml']['hash'],
            'metadataUrl': urls['metadata'], 'metadataHttpStatus': captures['metadata']['status'], 'metadataResponsePath': str(captures['metadata']['path']), 'metadataResponseSha256': captures['metadata']['hash'],
            'revisionId': metadata['revision_info']['law_revision_id'], 'xmlHash': digest(xml.encode()), 'bodyHash': digest(ET.tostring(main[0], encoding='utf-8')),
            'fixedRevisionId': fixed['revisionId'], 'fixedXmlHash': fixed['xmlHash']})
        write_json(evidence_path, {'schemaVersion': 'formal-current-law-retrieval/v1', 'planHash': plan['planHash'], 'inputHash': plan['inputHash'], 'entries': entries, 'complete': len(entries) == len(corpus)})
    record = {'evidencePath': str(evidence_path), 'sha256': file_hash(evidence_path)}
    try:
        binding = _law_recheck(record, plan)
    except Exception as error:
        write_json(destination / 'comparison-result.json', {'result': 'blocked', 'reason': str(error), 'evidence': record})
        raise
    write_json(destination / 'comparison-result.json', {'result': 'unchanged', 'binding': binding, 'evidence': record, 'notHumanApproval': True})
    return record
