"""Snapshot-bound partial updates, independent of the full-document uploader.

This module never chooses a production database. Gateways are explicitly injected.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Mapping
import json
from pathlib import Path

from tools.question_review_console.projection import sha256_json

CONTENT_FIELDS = frozenset({"questionText", "questionBodyText", "correctChoiceText",
    "questionIntent", "explanationText", "lawReferences", "lawRevisionFacts"})


def load_correction_manifest(root, path):
    from scripts.common.scoped_canonical_context import file_hash
    manifest = json.loads(Path(path).read_text())
    if manifest.get('schemaVersion') != 'snapshot-corrections/v1':
        raise ValueError('wrong correction manifest schema')
    if manifest.get('manifestHash') != sha256_json({k: v for k, v in manifest.items() if k != 'manifestHash'}):
        raise ValueError('correction manifest changed')
    for name, digest in manifest['inputs'].items():
        if file_hash(Path(name)) != digest:
            raise ValueError('correction input changed')
    for entry in manifest['cases'].values():
        if file_hash(Path(entry['candidatePath'])) != entry['candidateFileHash']:
            raise ValueError('correction artifact changed')
    return manifest


def prepare_snapshot_corrections(root, review_input, destination, *, readback):
    """Prepare concrete private previews. Never save formal patches or write live."""
    from datetime import datetime, timezone
    from scripts.common.scoped_canonical_context import SnapshotCorrectionContext, file_hash, reader_dependencies
    from tools.question_review_console.validated_evidence_import import replay_recovery_native_chain
    from tools.question_review_console.scoped_artifacts import write_json
    from scripts.common.law_audit_sidecar_contract import law_audit_sidecar_metadata_errors
    root, review_input, destination = Path(root).resolve(), Path(review_input).resolve(), Path(destination).resolve()
    expected = root / 'output/user_feedback_response_system/staging/recovery-three-cases/execution-contract/runs/T040-658bcd595031'
    if destination != expected or (destination.exists() and any(p.name not in {'diagnostics', 'command-history.json'} for p in destination.iterdir())):
        raise ValueError('fresh exact private destination required')
    request = json.loads(review_input.read_text())
    chain = replay_recovery_native_chain(root)
    old_manifest_path = review_input.parent / 'input-manifest.json'
    old_manifest = json.loads(old_manifest_path.read_text())
    if sha256_json(old_manifest['files']) != old_manifest['inputHash']:
        raise ValueError('fixed input files map differs')
    accepted = chain[-1]['receipt']['evidence'][0]
    if (file_hash(review_input) != accepted['reviewInputFileSha256']
            or sha256_json(request) != accepted['reviewInputCanonicalObjectHash']
            or old_manifest['inputHash'] != accepted['filesMapInputHash']
            or request['nativeEvidenceHash'] != accepted['nativeEvidenceCanonicalObjectHash']):
        raise ValueError('T039 accepted fixed input differs')
    inputs, drift = {}, {}
    for name, digest in old_manifest['files'].items():
        current = file_hash(Path(name))
        if current != digest:
            relative = Path(name).relative_to(root) if Path(name).is_relative_to(root) else None
            allowed_code = relative is not None and (str(relative).startswith('tools/question_review_console/') or str(relative).startswith('scripts/'))
            if not allowed_code:
                raise ValueError('fixed primary input changed')
            drift[name] = {'historicalHash': digest, 'currentHash': current}
        inputs[name] = current
    inputs[str(review_input)] = file_hash(review_input)
    inputs[str(old_manifest_path)] = file_hash(old_manifest_path)
    for path in reader_dependencies(root, [root / 'tools/question_review_console/scoped_corrections.py']):
        inputs[str(path)] = file_hash(path)
    chain_binding = [{k: v for k, v in event.items() if k != 'receipt'} for event in chain]
    chain_hash = sha256_json(chain_binding)
    run_ids = {event['taskId']: 'native:' + event['sessionId'] + ':' + event['eventHash'] for event in chain}
    audit = {'auditStatus': 'same_as_current', 'reviewState': 'tertiary_verified',
        'auditedAt': chain[-1]['eventTimestamp'], 'nextAuditDueAt': '2026-10-05',
        'auditMethodVersion': 'native-recovery-chain-T028-T029-T033-T037-T039/v1',
        'auditInputHash': 'sha256:' + sha256_json(inputs), 'auditRunId': run_ids['T039'],
        'lawCorpusSnapshotId': 'fixed-egov-xml-2026-10-03:' + request['nativeEvidenceHash'],
        'primaryAuditRunId': run_ids['T028'], 'secondaryAuditRunId': run_ids['T029'],
        'tertiaryAuditRunId': run_ids['T033'] + '+' + run_ids['T039'],
        'reconciliationStatus': 'native_full_challenge_then_narrow_correction_accepted',
        'sourceEvidenceVersionId': 'sha256:' + chain_hash}
    cases = {}
    now = datetime.now(timezone.utc).isoformat()
    for year, entry in request['cases'].items():
        candidate_path = Path(entry['candidatePath'])
        candidate = json.loads(candidate_path.read_text())
        if sha256_json(candidate) != entry['candidateHash']:
            raise ValueError('fixed candidate changed')
        snapshots = json.loads((review_input.parent / year / 'snapshot.json').read_text())
        ids = [snapshot['questionId'] for snapshot in snapshots]
        if ids != [delta['questionId'] for delta in candidate['delta']]:
            raise ValueError('source/public selected mapping differs')
        fields = sorted(set().union(*(set(snapshot['fieldSelection']) for snapshot in snapshots)))
        live = readback.read_selected_snapshots(ids, fields=fields)
        for snapshot in snapshots:
            selected = live[snapshot['questionId']]
            if not selected['exists'] or selected['updateTime'] != snapshot['updateTime']:
                raise ValueError('fixed selected snapshot version changed')
            for field in fields:
                present = field in snapshot['fields']
                if selected['fields'][field] != {'present': present, 'value': snapshot['fields'].get(field)}:
                    raise ValueError('fixed selected snapshot content/presence changed')
        promoted = deepcopy(candidate)
        if year != '2020':
            group = '85004' if year == '2018' else '85010'
            source_root = root / ('output/user_feedback_response_system/staging/recovery-source-' + year)
            context = SnapshotCorrectionContext(root, source_root, '2nd-class-kenchikushi', group,
                candidate['sourceProposal']['sourceBinding'], snapshots, references=[candidate_path, review_input])
            source = deepcopy(context.source.record)
            for index, fact in enumerate(promoted['sourceProposal']['lawRevisionFacts']):
                refs = promoted['sourceProposal']['lawReferences'][index]
                for ref in refs:
                    ref['verificationStatus'] = 'verified'
                for scope in ('examTime', 'current'):
                    fact[scope]['verificationStatus'] = 'verified'
                fact.update(audit)
                fact['notes'] = ['真正T028/T029の法的判断とT033/T039の限定challengeを新世代へ結合。正式保存・機械checkpoint・model評価は未実行。']
                fact['answerImpactFacts'] = [candidate['sourceProposal']['lawRevisionFacts'][index]['answerImpactFacts'][0],
                    '真偽は両時点で同じ。表示・条項・旧解説の修復不要を意味しない。']
                fact['evidenceSummary']['verdict'] = 'same_as_current'
                fact['evidenceSummary']['differenceSummary'] = '両時点のTF真偽は同じ。原問と公開表示を区別し、条項・表示・解説を修復する。'
                fact['evidenceBindingHash'] = 'sha256:' + sha256_json({'chain': chain_hash, 'binding': candidate['lawSidecar']['bindings'][index], 'references': refs})
                promoted['projection'][index]['lawReferences'] = deepcopy(refs)
                promoted['projection'][index]['lawRevisionFacts'] = deepcopy(fact)
                promoted['delta'][index]['fields']['lawReferences']['after'] = deepcopy(refs)
                promoted['delta'][index]['fields']['lawRevisionFacts']['after'] = deepcopy(fact)
            sidecar = {**audit, **candidate['sourceProposal']['sourceBinding'],
                'schemaVersion': 'law-revision-audit/v2', 'qualification': '2nd-class-kenchikushi',
                'listGroupId': group, 'examYear': int(year),
                'examTimeDecision': deepcopy(candidate['sourceProposal']['correctChoiceText']),
                'currentLawDecision': deepcopy(candidate['sourceProposal']['correctChoiceText']),
                'isLawRelated': True, 'userVisibleNoticeRequired': True,
                'noticeReason': '原問選択答と公開TF表示を分離し旧条項・解説を修復する。',
                'sourceSummary': '公式原問とordered choicesは保持。公開TF別variantへの限定修復。',
                'lawReferences': [ref for refs in promoted['sourceProposal']['lawReferences'] for ref in refs],
                'remainingRisk': '正式承認、保存前の現行版再照合、真正checkpoint/evaluation、production承認は未済。'}
            errors = law_audit_sidecar_metadata_errors(sidecar, expected_choice_count=5)
            if errors:
                raise ValueError('standard sidecar metadata invalid: ' + '; '.join(errors))
            promoted['lawSidecar'] = sidecar
            source_patch = {**candidate['sourceProposal']['sourceBinding'],
                'expectedBeforeHash': sha256_json(source), 'originalQuestionBodyText': source.get('questionBodyText'),
                'originalChoiceTextList': deepcopy(source.get('choiceTextList')),
                'originalAnswerText': candidate['sourceProposal']['answer_result_text'],
                'originalSelectionAnswer': candidate['sourceProposal']['answer_result_inferred_correct_choice_numbers'],
                'publicVariant': deepcopy(candidate['sourceProposal']['publicBodyProposal']),
                'fields': {k: deepcopy(promoted['sourceProposal'][k]) for k in ('correctChoiceText', 'questionIntent', 'explanationText', 'lawReferences', 'lawRevisionFacts')},
                'formalSaveApproved': False, 'sourceBodyOverwriteAllowed': False,
                'remaining': ['原問sourceへ公開TF vectorを直接保存しない。variantの正式移送承認待ち。']}
            context.assert_unchanged()
            inputs.update(context.files)
            write_json(destination / year / 'source-patch-preview.json', source_patch)
        else:
            delta = promoted['delta'][0]
            if set(delta['fields']) != {'questionText'}:
                raise ValueError('2020 correction exceeds one field')
            before, after = (delta['fields']['questionText'][key] for key in ('before', 'after'))
            if len(before) != len(after) + 1 or before.replace('明らかなたとき', '明らかなとき', 1) != after:
                raise ValueError('2020 correction differs from approved one-character deletion')
        promoted['reviewPending'] = False
        promoted['approvalReady'] = False
        promoted['publicationReady'] = False
        promoted['formalDataUpdated'] = False
        write_json(destination / year / 'live-selected-snapshot.json', live)
        write_json(destination / year / 'candidate.json', promoted)
        cases[year] = {'candidatePath': str(destination / year / 'candidate.json'),
            'candidateFileHash': file_hash(destination / year / 'candidate.json'),
            'candidateHash': sha256_json(promoted), 'liveSnapshotHash': sha256_json(live),
            'selectedCount': len(ids), 'formalSaveApproved': False, 'productionApproved': False}
    manifest = {'schemaVersion': 'snapshot-corrections/v1', 'createdAt': now, 'inputs': inputs,
        'historicalDependencyDrift': drift, 'nativeChain': chain_binding, 'nativeChainHash': chain_hash,
        'historicalT033ManifestLabel': 'manifestObjectHash denotes files-map inputHash; historical label unchanged',
        'historicalFilesMapHash': old_manifest['inputHash'], 'historicalManifestObjectHash': sha256_json(old_manifest),
        'historicalManifestFileHash': file_hash(old_manifest_path), 'cases': cases,
        'formalSaveApproved': False, 'productionApproved': False, 'evaluationPerformed': False,
        'publicationReady': False, 'formalDataUpdated': False,
        'remaining': ['正式保存承認とproduction承認は別々に未済', '真正current checkpoint/evaluation未済',
            '2025追加4field承認pending維持', '法令基準2026-10-03、正式保存前の現行版再照合が必要',
            'updatedAt/updatedByIdは既存監査責務。内容allowlistへ無断追加しない']}
    manifest['manifestHash'] = sha256_json(manifest)
    write_json(destination / 'manifest.json', manifest)
    return manifest


def verify_snapshot_corrections(root, manifest_path):
    """Run real existing gates, stopping on the first new strict failure."""
    import contextlib
    import io
    from datetime import datetime, timezone
    from scripts.check.check_law_revision_fact_coverage import run as strict_check
    from scripts.check.check_choice_text_alignment import check_file
    from scripts.check.check_correct_choice_patch_coverage import compare_entries
    from scripts.common.requirements import load_requirements, get_stage_rules, validate_records
    from tools.question_review_console.scoped_artifacts import write_json
    root, manifest_path = Path(root), Path(manifest_path)
    manifest = load_correction_manifest(root, manifest_path)
    old_run = root / 'output/user_feedback_response_system/staging/recovery-three-cases/runs/T037-b3d34b0c439c'
    results = []
    for year in ('2018', '2024'):
        candidate = json.loads(Path(manifest['cases'][year]['candidatePath']).read_text())
        merged = json.loads((old_run / year / 'converter-diagnostic/projection.json').read_text())
        proposal = candidate['sourceProposal']
        merged.update({key: deepcopy(proposal[key]) for key in ('correctChoiceText', 'questionIntent', 'explanationText', 'lawReferences', 'lawRevisionFacts')})
        merged['questionBodyText'] = deepcopy(proposal['publicBodyProposal'])
        documents = [{**projection, 'questionId': delta['questionId']} for projection, delta in zip(candidate['projection'], candidate['delta'], strict=True)]
        from scripts.common.scoped_canonical_context import SnapshotCorrectionContext
        snapshots = json.loads((old_run / year / 'snapshot.json').read_text())
        context = SnapshotCorrectionContext(root, root / ('output/user_feedback_response_system/staging/recovery-source-' + year),
            '2nd-class-kenchikushi', '85004' if year == '2018' else '85010', proposal['sourceBinding'], snapshots)
        coverage_errors, coverage_warnings = compare_entries([context.source.record],
            [{**merged, **proposal}], require_full=True, require_snippets=False, require_change_meta=False)
        if coverage_errors:
            write_json(manifest_path.parent / year / 'coverage-results.json', {'errors': coverage_errors, 'warnings': coverage_warnings})
            raise ValueError('correct-choice coverage failed')
        write_json(manifest_path.parent / year / 'coverage-results.json', {'errors': coverage_errors, 'warnings': coverage_warnings})
        # Projection is exclusively before + declared field updates: no defaults.
        for snapshot, projection, delta in zip(snapshots, candidate['projection'], candidate['delta'], strict=True):
            expected = deepcopy(snapshot['fields'])
            expected.update({k: deepcopy(v['after']) for k, v in delta['fields'].items()})
            if expected != projection:
                raise ValueError('projection contains undeclared field changes')
            for protected in ('originalQuestionBodyText', 'originalQuestionChoiceText', 'originalQuestionId', 'choiceTextList'):
                if (protected in projection) != (protected in snapshot['fields']) or projection.get(protected) != snapshot['fields'].get(protected):
                    raise ValueError('original source/public snapshot field changed')
        for stage, records, array in (('merged', [merged], 'question_bodies'), ('firestore', documents, 'questions')):
            directory = manifest_path.parent / year / 'verification' / stage
            file = directory / ('30_merged_2/question_candidate_merged.json' if stage == 'merged' else '40_convert/candidate_firestore_scoped.json')
            write_json(file, {array: records})
            rules = get_stage_rules(load_requirements(), stage=stage, record_array=array, qualification='2nd-class-kenchikushi')
            required = validate_records(records=records, rules=rules, source_path=file)
            alignment = check_file(file) if stage == 'merged' else []
            started = datetime.now(timezone.utc).isoformat()
            with contextlib.redirect_stdout(io.StringIO()) as output:
                rc = strict_check(list_group_dir=directory, stage=stage, require_all_law_related=True,
                    fail_on_hold=True, require_evidence_summary=True, require_law_references=True,
                    require_current_correct_choice=True, require_verified_law_references=True,
                    require_public_law_evidence=True, original_question_ids=[], report=directory / 'strict-law.json')
            result = {'year': year, 'stage': stage, 'inputHash': sha256_json(records), 'startedAt': started,
                'finishedAt': datetime.now(timezone.utc).isoformat(), 'strictExitCode': rc,
                'strictCommand': 'scripts.check.check_law_revision_fact_coverage.run(require_all_law_related=True,fail_on_hold=True,require_evidence_summary=True,require_law_references=True,require_current_correct_choice=True,require_verified_law_references=True,require_public_law_evidence=True)',
                'stdout': output.getvalue(), 'requiredErrors': required, 'alignmentErrors': alignment}
            results.append(result)
            write_json(manifest_path.parent / 'verification-results.json', {'results': results, 'complete': False})
            if rc:
                raise ValueError('new private strict failed; hard stop')
            if required or alignment:
                raise ValueError('required/alignment gate failed')
    load_correction_manifest(root, manifest_path)
    write_json(manifest_path.parent / 'verification-results.json', {'results': results, 'complete': True})
    return results


@dataclass(frozen=True)
class SnapshotCorrection:
    question_id: str
    update_time: str
    fields: Mapping

    @classmethod
    def from_mapping(cls, value):
        fields = deepcopy(value["fields"])
        if (not value.get("questionId") or not value.get("updateTime")
                or not fields or not set(fields) <= CONTENT_FIELDS
                or set(value.get("allowlist", [])) != set(fields)
                or value.get("fullDocumentUploadAllowed") is not False):
            raise ValueError("invalid partial correction contract")
        for change in fields.values():
            if set(change) != {"beforePresent", "before", "after"} or type(change["beforePresent"]) is not bool:
                raise ValueError("explicit presence and before/after required")
            if not change["beforePresent"] and change["before"] is not None:
                raise ValueError("missing before must have null sentinel")
        return cls(value["questionId"], value["updateTime"], fields)

    def compare(self, snapshot, *, after=False):
        if not snapshot.get("exists") or snapshot.get("questionId") != self.question_id:
            raise ValueError("selected document missing or differs")
        if not after and snapshot.get("updateTime") != self.update_time:
            raise ValueError("updateTime conflict")
        for field, change in self.fields.items():
            actual = snapshot["fields"][field]
            present = True if after else change["beforePresent"]
            expected = change["after"] if after else change["before"]
            if actual["present"] is not present or (present and actual["value"] != expected):
                raise ValueError("field presence/value conflict")


class CorrectionContract:
    def __init__(self, deltas, binding):
        self._deltas = deepcopy(deltas)
        self.corrections = tuple(SnapshotCorrection.from_mapping(d) for d in deltas)
        ids = [c.question_id for c in self.corrections]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("duplicate or empty selected IDs")
        required = {"sourceHash", "candidateHash", "policyHash", "workVersionHash", "manifestHash", "liveSnapshotHash"}
        if set(binding) != required or any(not binding[k] for k in required):
            raise ValueError("complete preview binding required")
        self.binding = deepcopy(binding)
        self.token = sha256_json({"binding": binding, "deltas": deltas})

    def apply(self, gateway, *, token, formal_approval, production_approval,
              machine_ready, operation_receipt=None):
        if self.token != sha256_json({'binding': self.binding, 'deltas': self._deltas}) or any(
                c.fields != raw['fields'] or c.question_id != raw['questionId'] or c.update_time != raw['updateTime']
                for c, raw in zip(self.corrections, self._deltas, strict=True)):
            raise ValueError('contract binding or delta drift')
        if token != self.token or formal_approval != token or production_approval != token or machine_ready is not True:
            raise ValueError("independent approvals and genuine machine gate required")
        ids = [c.question_id for c in self.corrections]
        fields = sorted(set().union(*(c.fields for c in self.corrections)))
        before = gateway.read_selected_snapshots(ids, fields=fields)
        if set(before) != set(ids):
            raise ValueError("selected read returned another ID set")
        if operation_receipt is not None:
            if operation_receipt.get("token") != token or operation_receipt.get("afterVersions") != {k: v["updateTime"] for k, v in before.items()}:
                raise ValueError("retry receipt/version differs")
            for c in self.corrections:
                c.compare(before[c.question_id], after=True)
            return deepcopy(operation_receipt)
        for c in self.corrections:
            c.compare(before[c.question_id])
        # Gateway must atomically apply every update with its snapshot precondition.
        gateway.update_fields_atomically([(c.question_id, {k: deepcopy(v["after"]) for k, v in c.fields.items()}, c.update_time) for c in self.corrections])
        after = gateway.read_selected_snapshots(ids, fields=fields)
        for c in self.corrections:
            c.compare(after[c.question_id], after=True)
        return {"token": token, "afterVersions": {k: v["updateTime"] for k, v in after.items()}}

    def compensation(self, receipt, *, approval):
        if approval != sha256_json({"rollback": receipt, "token": self.token}) or receipt.get("token") != self.token:
            raise ValueError("separate compensation approval required")
        result = []
        for c in self.corrections:
            if any(not f["beforePresent"] for f in c.fields.values()):
                raise ValueError("rollback requires deletion contract for originally missing field")
            result.append({"questionId": c.question_id, "updateTime": receipt["afterVersions"][c.question_id],
                "allowlist": list(c.fields), "fullDocumentUploadAllowed": False,
                "fields": {k: {"beforePresent": True, "before": deepcopy(v["after"]), "after": deepcopy(v["before"])} for k, v in c.fields.items()}})
        return CorrectionContract(result, self.binding)
