"""Read-only replay of saved native evidence; never manufactures checkpoints."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from uuid import uuid4
from scripts.check.check_explanation_patch_coverage import compare_entries
from scripts.common.law_audit_sidecar_contract import law_audit_sidecar_metadata_errors
from tools.question_review_console.projection import sha256_json
from tools.question_review_console.scoped_artifacts import load_scoped_artifacts, write_json


def replay_recovery_native_chain(root):
    """Replay the five saved events; retain their different serialization scopes."""
    notes = Path(root) / 'docs/goals/feedback-correction-recovery/notes'
    result = []
    for stem in ('T028-blind-a', 'T029-blind-b', 'T033-challenge', 'T037-correction', 'T039-accept'):
        provenance_path = notes / (stem + '-provenance.json')
        receipt_path = notes / (stem + '-receipt.json')
        provenance = json.loads(provenance_path.read_text())
        line = Path(provenance['sessionPath']).read_bytes().splitlines(keepends=True)[provenance['eventLine'] - 1]
        include_lf = 'eventLineSha256' not in provenance
        event_hash = hashlib.sha256(line if include_lf else line.rstrip(b'\r\n')).hexdigest()
        declared = provenance.get('eventLineSha256') or provenance.get('eventSha256') or provenance['eventSha256IncludingFinalLf']
        if event_hash != declared:
            raise ValueError('native event binding differs')
        event = json.loads(line)
        payload = event.get('payload', {})
        if event.get('type') != 'response_item' or payload.get('role') != 'assistant':
            raise ValueError('native evidence is not assistant output')
        output = ''.join(v.get('text', '') for v in payload.get('content', []) if v.get('type') == 'output_text')
        output_hash = hashlib.sha256(output.encode()).hexdigest()
        if output_hash != (provenance.get('assistantOutputSha256') or provenance['outputSha256']):
            raise ValueError('native output binding differs')
        native = json.loads(output)
        stored = json.loads(receipt_path.read_text())
        inner = native['goalbuddy_receipt_v1']
        if stored != native and stored != inner:
            raise ValueError('stored receipt differs from native output')
        compact_hash = hashlib.sha256(json.dumps(inner, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        expected_compact = provenance.get('receiptSha256') or provenance.get('nativeInnerReceiptCompactObjectHash')
        if expected_compact and compact_hash != expected_compact:
            raise ValueError('native compact inner receipt differs')
        receipt_file_hash = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        if provenance.get('receiptFileSha256') and receipt_file_hash != provenance['receiptFileSha256']:
            raise ValueError('native receipt file differs')
        result.append({'taskId': stem[:4], 'sessionId': provenance.get('nativeSessionID') or provenance['sessionId'],
            'eventHash': event_hash, 'eventIncludesFinalLf': include_lf,
            'outputHash': output_hash, 'receiptFileHash': receipt_file_hash,
            'innerCompactObjectHash': compact_hash, 'eventTimestamp': event['timestamp'],
            'receipt': inner})
    return result


def validate_fixed_package(root, package_manifest, *, expected_hash=None):
    package_manifest = Path(package_manifest)
    if expected_hash is not None and hashlib.sha256(package_manifest.read_bytes()).hexdigest() != expected_hash:
        raise ValueError('fixed package manifest changed')
    package = json.loads(package_manifest.read_text())
    for entry in package['files']:
        path = root / entry['path']
        if not path.is_relative_to(root) or path.is_symlink() or path.absolute() != path.resolve() or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('fixed native package changed')
    return package


def import_native_evidence(repo_root, artifact_manifest, package_manifest, law_directory, destination):
    root = Path(repo_root).resolve()
    manifest, context, record, _ = load_scoped_artifacts(root, artifact_manifest)
    destination = Path(destination)
    if not destination.resolve().is_relative_to(root / 'output/user_feedback_response_system/staging/recovery-scoped-2025'):
        raise ValueError('evidence import requires private recovery destination')
    expected_package_hash = context.files.get(str(Path(package_manifest)))
    if not expected_package_hash:
        raise ValueError('fixed package manifest is not bound to the input context')
    validate_fixed_package(root, package_manifest, expected_hash=expected_package_hash)
    law = Path(law_directory)
    union = json.loads((law / 'union-audit-input.json').read_text())
    if union['sourceIdentity'] != context.binding.as_mapping():
        raise ValueError('native evidence binding differs')
    native = []
    for prefix in ('primary-union', 'secondary-union', 'tertiary-union', 'tertiary-union-schema-amendment'):
        review = json.loads((law / (prefix + '-review.json')).read_text())
        provenance = json.loads((law / (prefix + '-provenance.json')).read_text())
        response = (law / (prefix + '-response.json')).read_text()
        receipt = json.loads(response)
        actual = next(e['review'] for e in receipt['goalbuddy_receipt_v1']['evidence'] if 'review' in e)
        if actual != review or sha256_json(review) != provenance['reviewObjectHash'] or sha256_json(receipt) != provenance['receiptObjectHash']:
            raise ValueError('saved native output differs')
        if hashlib.sha256(response.encode()).hexdigest() != provenance['assistantOutputSha256']:
            raise ValueError('native response hash differs')
        sessions = list((Path('/Users/yuki/.codex/sessions') / provenance['eventTimestamp'][:10].replace('-', '/')).glob('*-' + provenance['nativeSessionId'] + '.jsonl'))
        if len(sessions) != 1:
            raise ValueError('native session evidence missing')
        matching = [line for line in sessions[0].read_text().splitlines() if hashlib.sha256(line.encode()).hexdigest() == provenance['eventLineSha256']]
        if len(matching) != 1:
            raise ValueError('native output event not uniquely verified')
        event = json.loads(matching[0])
        if event.get('type') != 'response_item' or event.get('payload', {}).get('type') != 'message':
            raise ValueError('native event is not an assistant message')
        content = event['payload'].get('content', [])
        actual_text = ''.join(item.get('text', '') for item in content if item.get('type') == 'output_text')
        if event['payload'].get('role') != 'assistant' or actual_text != response:
            raise ValueError('native assistant output does not match saved response')
        if review['inputHash'] != sha256_json(union):
            raise ValueError('native review input hash differs')
        native.append({'reviewHash': sha256_json(review), 'provenance': provenance})
    candidate = json.loads((law / 'final-candidate-changes.json').read_text())
    sidecar = json.loads((law / 'final-law-audit-sidecar.json').read_text())
    expected = dict(context.projection.record); expected.update(candidate)
    if record != expected:
        raise ValueError('private projection differs from saved final candidate')
    patch = {**record}
    errors, warnings = compare_entries([context.projection.record], [patch], require_law_grounded_flag=True,
        require_is_law_related=True, require_law_revision_facts=True, require_law_evidence_utilization=True)
    errors += law_audit_sidecar_metadata_errors(sidecar, expected_choice_count=5,
        expected_qualification=context.qualification, expected_list_group_id=context.list_group_id)
    if errors:
        raise ValueError('; '.join(errors))
    context.assert_unchanged()
    result = {'schemaVersion': 'validated-native-evidence-import/v1', 'importRunId': 'evidence-import:' + str(uuid4()),
        'artifactManifestHash': manifest['manifestHash'], 'inputHash': context.manifest['inputHash'],
        'nativeReviews': native, 'candidateHash': sha256_json(candidate), 'sidecarHash': sha256_json(sidecar),
        'validatorsUnmodified': True, 'warnings': warnings, 'status': 'validated_private',
        'modelRunPerformed': False, 'checkpointStages': [], 'formalDataUpdated': False,
        'checkpointStopReason': '追加4fieldの正式承認・保存前。法令reviewは01/02/02a/02b/03/04の全面実行を証明しません。'}
    result['validatorVersions'] = {str(path.relative_to(root)): digest for value, digest in context.files.items()
        for path in [Path(value)] if path.is_relative_to(root) and path.suffix == '.py'}
    write_json(destination / 'evidence-import.json', result)
    return result
