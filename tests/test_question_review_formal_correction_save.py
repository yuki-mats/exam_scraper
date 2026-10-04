"""Physical fixture repositories only; never connect to a provider or database."""
from copy import deepcopy
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import xml.etree.ElementTree as ET
from pathlib import Path
import tempfile
import unittest

from tools.question_review_console.formal_correction_save import (
    PLAN_SCHEMA, UNIT_SCHEMA, PublicationUnit, approval_scope, approval_question, digest, json_bytes,
    load_saved_units, physical, read_plan, replay, save_formal_units,
)
from tools.question_review_console.projection import sha256_json
from tools.question_review_console.scoped_artifacts import write_json, load_formal_correction_unit


def fixture_plan(root):
    """Three independent typed units, with an absent optional public group."""
    directory = root / 'private-run'
    xml = root / 'law-cache/fixture-law/2026-10-03.json'
    value = {'schemaVersion': 'primary-law-file-cache/v1', 'lawId': 'fixture-law', 'asOf': '2026-10-03', 'revisionId': 'fixture-revision', 'xmlText': '<Law><LawBody><MainProvision><Article Num="1"/></MainProvision></LawBody></Law>', 'sourceUrl': 'fixture'}
    value['xmlHash'] = digest(value['xmlText'].encode()); write_json(xml, value)
    units = []
    for year in (2018, 2024, 2020):
        qualification = 'kanrigyoumu' if year == 2020 else '2nd-class-kenchikushi'
        binding = {'sourceQuestionKey': 'fixture-source', 'reviewQuestionId': 'fixture-original', 'sourceRecordRef': 'fixture.json#0'}
        count = 1 if year == 2020 else 5
        snapshots, deltas, projection = {}, [], []
        for index in range(count):
            identity = f'fixture-{year}-{index}'
            before = '明らかなたとき' if year == 2020 else '公開before'
            after = '明らかなとき' if year == 2020 else '公開after'
            fields = {'questionText': before, 'unknownField': {'keep': index}, 'originalQuestionBodyText': '原問は保持', 'originalQuestionChoiceText': '肢は保持', 'qualificationId': 'condominiummanager' if year == 2020 else '2nd-class-kenchikushi'}
            snapshots[identity] = {'questionId': identity, 'exists': True, 'updateTime': 'fixture-version', 'fields': {**{k: {'present': True, 'value': v} for k, v in fields.items()}, 'listGroupId': {'present': False, 'value': None}}}
            deltas.append({'questionId': identity, 'updateTime': 'fixture-version', 'allowlist': ['questionText'], 'fields': {'questionText': {'beforePresent': True, 'before': before, 'after': after}}, 'fullDocumentUploadAllowed': False})
            projection.append({**fields, 'questionText': after})
        candidate = {'delta': deltas, 'projection': projection}
        if year != 2020:
            candidate.update({'sourceProposal': {'sourceBinding': binding, 'answer_result_inferred_correct_choice_numbers': [4 if year == 2018 else 2]}, 'lawSidecar': {'fixture': 'separate immutable metadata'}})
        unit_hash = sha256_json({'schemaVersion': UNIT_SCHEMA, 'caseYear': year, 'candidateHash': sha256_json(candidate), 'sourceManifestHash': 'fixed-fixture'})
        transport = {'qualification': qualification, 'evaluationScopeId': 'snapshot-unbound-fixture' if year == 2020 else str(year), 'contextMissing': year == 2020, 'sourceBinding': None if year == 2020 else binding, 'sourceListGroupId': None if year == 2020 else str(year)}
        if year == 2020:
            source = {'role': 'production_snapshot', 'sourceGroup': None, 'sourceRef': None, 'publicationQualificationId': 'condominiummanager', 'snapshot': snapshots}
            public = {'role': 'public_snapshot_delta', 'candidate': candidate}
            names = ('production-snapshot.json', 'public-delta.json')
        else:
            source = {'role': 'official_source_noop', 'sourceBinding': binding, 'snapshot': snapshots, 'question_bodies': [{'questionBodyText': '否定形原問を保存', 'choiceTextList': ['原肢'] * 5}], 'preservedOriginal': {'originalQuestionBodyText': '否定形原問を保存', 'originalChoiceTextList': ['原肢'] * 5, 'originalSelectionAnswer': [4 if year == 2018 else 2]}}
            public = {'role': 'public_tf_variant', 'candidate': candidate, 'sourceProposal': candidate['sourceProposal'], 'lawSidecar': candidate['lawSidecar']}
            names = ('source-envelope.json', 'public-variant.json')
        manifest = {'schemaVersion': UNIT_SCHEMA, 'unitHash': unit_hash, 'caseYear': year, 'kind': 'production_snapshot_delta' if year == 2020 else 'official_source_public_variant', 'qualification': qualification, 'nativeChainHash': 'fixture-native', 'snapshotHash': sha256_json(snapshots), 'candidateHash': sha256_json(candidate), 'sourceManifestHash': 'fixed-fixture', 'limitedDelta': deltas, 'publicationIds': [d['questionId'] for d in deltas], 'transportContext': transport, 'payloadHashes': {n: digest(json_bytes(v)) for n, v in zip(names, (source, public), strict=True)}}
        files = []
        for name, payload in zip(('manifest.json', *names), (manifest, source, public), strict=True):
            relative = f'output/{qualification}/publication_corrections/{unit_hash}/{name}'
            private = 'planned-files/' + relative
            write_json(directory / private, payload)
            files.append({'path': relative, 'privatePath': private, 'sha256': digest(json_bytes(payload)), 'targetExists': False, 'targetHash': None})
        units.append({'caseYear': year, 'kind': manifest['kind'], 'qualification': qualification, 'unitHash': unit_hash, 'files': files})
    inputs = {str(xml): digest(xml.read_bytes())}
    plan = {'schemaVersion': PLAN_SCHEMA, 'createdAt': datetime.now(timezone.utc).isoformat(), 'inputs': inputs, 'inputHash': sha256_json(inputs), 'sourceManifestHash': 'fixed-fixture', 'units': units, 'plannedPaths': [f['path'] for u in units for f in u['files']]}
    (directory / 'approval-proposal.md').write_text('fixture field before/after approval')
    write_json(directory / 'approval-details.json', {'fixture': 'exact refs/facts appendix'})
    plan['approvalMaterials'] = {name: digest((directory / name).read_bytes()) for name in ('approval-proposal.md', 'approval-details.json')}
    plan['approvalMaterialPaths'] = {name: str(directory / name) for name in plan['approvalMaterials']}
    plan['planHash'] = sha256_json(plan)
    path = directory / 'formal-save-plan.json'; write_json(path, plan)
    return path, plan


def native(root, name, question, role='user', answer='この計画の正式保存を承認します'):
    request = {'type': 'response_item', 'timestamp': datetime.now(timezone.utc).isoformat(), 'payload': {'type': 'function_call', 'name': 'functions.request_user_input_async', 'call_id': 'fixture-call', 'arguments': json.dumps({'questions': [{'title': question, 'options': ['この計画の正式保存を承認します', '保存しない']}]}, ensure_ascii=False)}}
    event = {'type': 'response_item', 'timestamp': datetime.now(timezone.utc).isoformat(), 'payload': {'type': 'message', 'role': role, 'content': [{'type': 'input_text', 'text': answer}]}}
    first = json.dumps(request, ensure_ascii=False).encode() + b'\n'
    second = json.dumps(event, ensure_ascii=False).encode() + b'\n'
    path = root / (name + '.jsonl'); path.write_bytes(first + second)
    return {'sessionPath': str(path), 'requestLine': 1, 'requestSha256IncludingFinalLf': digest(first), 'eventLine': 2, 'eventSha256IncludingFinalLf': digest(second)}


def approvals(root, plan):
    human = native(root, 'fixture-human', approval_question(plan))
    entries = []
    for file, file_hash in plan['inputs'].items():
        fixed = json.loads(Path(file).read_bytes())
        now = datetime.now(timezone.utc)
        response = root / 'fixture-http-response.xml'; response.write_text(fixed['xmlText'])
        metadata = root / 'fixture-law-data.json'; write_json(metadata, {'law_info': {'law_id': fixed['lawId']}, 'revision_info': {'law_revision_id': fixed['revisionId']}})
        entries.append({'lawId': fixed['lawId'], 'retrievedAt': now.isoformat(), 'referenceDate': now.date().isoformat(),
            'metadataUrl': 'https://laws.e-gov.go.jp/api/2/law_data/' + fixed['lawId'] + '?asof=' + now.date().isoformat(), 'metadataHttpStatus': 200, 'metadataResponsePath': str(metadata), 'metadataResponseSha256': digest(metadata.read_bytes()),
            'url': 'https://laws.e-gov.go.jp/api/2/law_file/xml/' + fixed['lawId'] + '?asof=' + now.date().isoformat(), 'httpStatus': 200,
            'responsePath': str(response), 'responseSha256': digest(response.read_bytes()), 'fixedXmlHash': fixed['xmlHash'], 'fixedRevisionId': fixed['revisionId'],
            'xmlHash': fixed['xmlHash'], 'revisionId': fixed['revisionId'], 'bodyHash': digest(ET.tostring(ET.fromstring(fixed['xmlText']).find('.//MainProvision'), encoding='utf-8'))})
    evidence = root / 'fixture-law-retrieval.json'
    write_json(evidence, {'schemaVersion': 'formal-current-law-retrieval/v1', 'planHash': plan['planHash'], 'inputHash': plan['inputHash'], 'entries': entries})
    return human, {'evidencePath': str(evidence), 'sha256': digest(evidence.read_bytes())}


class FormalCorrectionSaveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.path, self.plan = fixture_plan(self.root)
        self.human, self.law = approvals(self.root, self.plan)
        self.receipt = self.root / 'private-run/save-receipt.json'

    def save(self, **kwargs):
        return save_formal_units(self.root, self.path, human_approval=kwargs.pop('human_approval', self.human), law_recheck=kwargs.pop('law_recheck', self.law), receipt_path=self.receipt, **kwargs)

    def test_all_fields_absence_and_original_source_public_roles(self):
        read_plan(self.root, self.path)
        for year in (2018, 2024, 2020):
            plan, unit, docs = load_formal_correction_unit(self.root, self.path, year)
            self.assertEqual(len(docs), 1 if year == 2020 else 5)
            for doc in docs:
                self.assertNotIn('listGroupId', doc)
                self.assertIn('unknownField', doc)
                self.assertEqual(doc['originalQuestionBodyText'], '原問は保持')
            if year == 2020:
                self.assertIsNone(unit.source['sourceGroup'])
            else:
                self.assertEqual(unit.source['preservedOriginal']['originalSelectionAnswer'], [4 if year == 2018 else 2])
                self.assertEqual(unit.source['question_bodies'][0]['questionBodyText'], '否定形原問を保存')

    def test_direct_native_approval_and_law_are_required(self):
        for record in (None, True, {'token': self.plan['planHash']}):
            with self.subTest(record=record), self.assertRaises(ValueError): self.save(human_approval=record)
        with self.assertRaises(ValueError): self.save(law_recheck=None)
        assistant = native(self.root, 'assistant', approval_question(self.plan), role='assistant')
        with self.assertRaises(ValueError): self.save(human_approval=assistant)
        self.assertFalse((self.root / 'output').exists())

    def test_different_plan_path_partial_sibling_approval_refused(self):
        for mutation in ('planHash', 'files', 'unitHashes'):
            message = approval_scope(self.plan)
            if mutation == 'planHash': message[mutation] = 'different'
            elif mutation == 'files': message[mutation].pop(next(iter(message[mutation])))
            else: message[mutation].pop()
            altered = deepcopy(self.plan); altered['planHash'] = sha256_json(message)
            record = native(self.root, mutation, approval_question(altered))
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): self.save(human_approval=record)
        self.assertFalse((self.root / 'output').exists())

    def test_modified_bytes_input_drift_and_bad_native_hash_refused(self):
        first = self.path.parent / self.plan['units'][0]['files'][0]['privatePath']
        old = first.read_bytes(); first.write_bytes(old + b' ')
        with self.assertRaises(ValueError): self.save()
        first.write_bytes(old)
        xml = Path(next(iter(self.plan['inputs']))); old_xml = xml.read_bytes(); xml.write_text('<Changed/>')
        with self.assertRaises(ValueError): self.save()
        xml.write_bytes(old_xml)
        forged = {**self.human, 'eventSha256IncludingFinalLf': '0' * 64}
        with self.assertRaises(ValueError): self.save(human_approval=forged)

    def test_success_immutable_readback_and_same_resend_preserve_unrelated(self):
        unrelated = self.root / 'unrelated.txt'; unrelated.write_text('keep')
        result = self.save()
        self.assertFalse(result['checkpointRecorded'])
        self.assertFalse(result['publicationReady'])
        before = {f['path']: physical(self.root, f['path']).read_bytes() for u in self.plan['units'] for f in u['files']}
        self.assertEqual(len(load_saved_units(self.root, self.path, self.receipt)), 3)
        self.assertEqual(self.save(), result)
        self.assertEqual(before, {p: physical(self.root, p).read_bytes() for p in before})
        self.assertEqual(unrelated.read_text(), 'keep')
        for p in before: self.assertEqual(physical(self.root, p).stat().st_mode & 0o777, 0o600)

    def test_existing_conflict_and_partial_directory_refused_before_any_save(self):
        f = self.plan['units'][1]['files'][0]
        target = physical(self.root, f['path']); target.parent.mkdir(parents=True); target.write_text('{}')
        with self.assertRaises(ValueError): self.save()
        self.assertFalse(physical(self.root, self.plan['units'][0]['files'][0]['path']).exists())
        self.assertEqual(target.read_text(), '{}')

    def test_interruption_before_and_after_atomic_rename_no_load_without_receipt(self):
        for phase in ('before_rename', 'after_rename', 'before_receipt'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve(); path, plan = fixture_plan(root); human, law = approvals(root, plan); receipt = root / 'receipt.json'
                def fail(current, unit):
                    if current == phase: raise RuntimeError('fixture interruption')
                with self.assertRaises(RuntimeError): save_formal_units(root, path, human_approval=human, law_recheck=law, receipt_path=receipt, failure_hook=fail)
                self.assertFalse(receipt.exists())
                for unit in plan['units']:
                    directory = physical(root, str(Path(unit['files'][0]['path']).parent))
                    if directory.exists(): self.assertEqual({p.name for p in directory.iterdir()}, {Path(f['path']).name for f in unit['files']})
                with self.assertRaises(FileNotFoundError): load_saved_units(root, path, receipt)
                save_formal_units(root, path, human_approval=human, law_recheck=law, receipt_path=receipt)
                self.assertEqual(len(load_saved_units(root, path, receipt)), 3)
                self.assertEqual(list(root.rglob('.formal-staging-*')), [])

    def test_symlink_path_escape_and_forged_receipt_refused(self):
        with self.assertRaises(ValueError): physical(self.root, '../outside')
        (self.root / 'output').symlink_to(self.root / 'private-run', target_is_directory=True)
        with self.assertRaises(ValueError): self.save()
        (self.root / 'output').unlink()
        write_json(self.receipt, {'schemaVersion': 'formal-correction-save-receipt/v1', 'result': 'saved', 'planHash': self.plan['planHash'], 'inputHash': self.plan['inputHash'], 'files': approval_scope(self.plan)['files'], 'humanApprovalBinding': {'fake': True}, 'lawRecheckBinding': {'fake': True}})
        with self.assertRaises(ValueError): load_saved_units(self.root, self.path, self.receipt)

    def test_replay_rejects_unknown_delta_original_change_presence_version_and_coverage(self):
        _, unit, _ = load_formal_correction_unit(self.root, self.path, 2020)
        candidate, snapshot = unit.public['candidate'], unit.source['snapshot']
        for kind in ('unknown_delta', 'original_changed', 'missing_field', 'version', 'coverage'):
            c, s = deepcopy(candidate), deepcopy(snapshot)
            if kind == 'unknown_delta': c['delta'][0]['fields']['unknownField'] = {'beforePresent': True, 'before': 0, 'after': 1}
            elif kind == 'original_changed': c['projection'][0]['originalQuestionBodyText'] = 'changed'
            elif kind == 'missing_field': s[next(iter(s))]['fields']['questionText']['present'] = False
            elif kind == 'version': s[next(iter(s))]['updateTime'] = 'changed'
            else: c['projection'].clear()
            with self.subTest(kind=kind), self.assertRaises(ValueError): replay(c, s)

    def test_typed_roles_missing_provenance_and_fake_source_binding_refused(self):
        for year in (2018, 2020):
            _, unit, _ = load_formal_correction_unit(self.root, self.path, year)
            for kind in ('role', 'provenance', 'source'):
                m, s, p = deepcopy(unit.manifest), deepcopy(unit.source), deepcopy(unit.public)
                if kind == 'role': s['role'] = 'public_tf_variant'
                elif kind == 'provenance': m['nativeChainHash'] = None
                elif year == 2020: s['sourceGroup'] = 'guessed'
                else: s['question_bodies'][0]['questionBodyText'] = 'changed'
                with self.subTest(year=year, kind=kind), self.assertRaises(ValueError): PublicationUnit(m, s, p).validate()

    def test_unapproved_plan_not_normal_group_patch_and_explicit_preview_pending(self):
        from tools.question_review_console.inventory import QuestionInventory
        from tools.question_review_console.workflow_runner import ArtifactSynchronizer
        inventory = QuestionInventory(self.root)
        preview = ArtifactSynchronizer(self.root, inventory, 'fixture').preview_formal_corrections(self.path)
        self.assertEqual(sum(c['selectedCount'] for c in preview['cases'].values()), 11)
        self.assertFalse(preview['formalDataUpdated'])
        self.assertFalse(preview['publicationReady'])
        for c in preview['cases'].values():
            self.assertFalse(c['machineReady']); self.assertFalse(c['workVersions']['allCurrent'])
        self.assertTrue(preview['cases']['2020']['transportContext']['contextMissing'])
        q = inventory.formal_correction_question(self.path, '2020')
        self.assertEqual(q['qualification'], 'kanrigyoumu')
        self.assertNotIn('listGroupId', q['projected'])
        with self.assertRaises(FileNotFoundError): inventory.group('kanrigyoumu', 'snapshot-unbound-fixture')
        self.save()
        explicit = inventory.formal_correction_question(self.path, '2018', save_receipt=self.receipt)
        self.assertTrue(explicit['formalSaveReceiptPresent'])
        self.assertFalse(explicit['workVersions']['allCurrent'])

    def test_actual_race_empty_directory_is_not_overwritten(self):
        def race(phase, unit_hash):
            if phase == 'before_rename':
                target = physical(self.root, str(Path(self.plan['units'][0]['files'][0]['path']).parent))
                target.mkdir()
        with self.assertRaises(OSError): self.save(failure_hook=race)
        target = physical(self.root, str(Path(self.plan['units'][0]['files'][0]['path']).parent))
        self.assertEqual(list(target.iterdir()), [])
        self.assertFalse(self.receipt.exists())

    def test_law_http_revision_body_and_date_cannot_be_human_declaration(self):
        original = Path(self.law['evidencePath']).read_bytes()
        for kind in ('revision', 'body', 'date', 'url', 'declaration'):
            value = json.loads(original)
            if kind == 'revision': value['entries'][0]['revisionId'] = 'different'
            elif kind == 'body': value['entries'][0]['bodyHash'] = 'different'
            elif kind == 'date': value['entries'][0]['retrievedAt'] = '2026-10-03T00:00:00Z'
            elif kind == 'url': value['entries'][0]['url'] = 'https://untrusted.example/law'
            else: value = {'currentRevisionAndBodyUnchanged': True}
            evidence = Path(self.law['evidencePath']); write_json(evidence, value)
            record = {'evidencePath': str(evidence), 'sha256': digest(evidence.read_bytes())}
            with self.subTest(kind=kind), self.assertRaises(ValueError): self.save(law_recheck=record)
        Path(self.law['evidencePath']).write_bytes(original)
        self.assertFalse((self.root / 'output').exists())

    def test_native_ui_answer_replays_actual_tag_call_index_question(self):
        session = Path(self.human['sessionPath']); lines = session.read_bytes().splitlines(keepends=True)
        item = {'questionItemId': json.dumps(['request_user_input_async', 'fixture-call', 0]),
            'question': approval_question(self.plan), 'answer': 'この計画の正式保存を承認します'}
        def response(value):
            reply = {'type': 'response_item', 'timestamp': datetime.now(timezone.utc).isoformat(), 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': '<send_user_message_question_reply>' + json.dumps([value], ensure_ascii=False) + '</send_user_message_question_reply>'}]}}
            second = json.dumps(reply, ensure_ascii=False).encode() + b'\n'; session.write_bytes(lines[0] + second)
            return {**self.human, 'eventSha256IncludingFinalLf': digest(second)}
        record = response(item); self.save(human_approval=record)
        for key, value in [('questionItemId', json.dumps(['request_user_input_async', 'other-call', 0])), ('questionItemId', json.dumps(['request_user_input_async', 'fixture-call', 1])), ('question', 'other question'), ('answer', '承認しない')]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): self.save(human_approval=response({**item, key: value}))

    def test_free_text_approval_refuses_an_intervening_question(self):
        session = Path(self.human['sessionPath']); lines = session.read_bytes().splitlines(keepends=True)
        session.write_bytes(lines[0] + lines[0] + lines[1])
        with self.assertRaises(ValueError): self.save(human_approval={**self.human, 'eventLine': 3})


    def test_actual_generator_outputs_are_discovered_by_existing_strict_readers(self):
        from scripts.check.check_law_revision_fact_coverage import latest_merged_files, latest_firestore_file
        from tools.question_review_console.formal_correction_save import write_verification_input
        directory = self.root / 'verification'
        write_json(directory / '30_merged_2/question_formal_preview.json', {'question_bodies': []})
        records = [{'fixtureOriginal': 'unchanged'}]
        merged = write_verification_input(directory, 'merged', records)
        self.assertEqual(latest_merged_files(directory), [merged])
        self.assertEqual(json.loads(merged.read_bytes()), {'question_bodies': records})
        converted = write_verification_input(directory, 'firestore', records)
        self.assertEqual(latest_firestore_file(directory), converted)
        self.assertEqual(json.loads(converted.read_bytes()), {'questions': records})

    def test_metadata_semantic_revision_and_response_hash_cannot_be_declared(self):
        evidence = json.loads(Path(self.law['evidencePath']).read_bytes())
        metadata = Path(evidence['entries'][0]['metadataResponsePath'])
        metadata.write_text('{}')
        with self.assertRaises(ValueError): self.save()
        write_json(metadata, {'law_info': {'law_id': 'fixture-law'}, 'revision_info': {'law_revision_id': 'changed'}})
        evidence['entries'][0]['metadataResponseSha256'] = digest(metadata.read_bytes())
        write_json(Path(self.law['evidencePath']), evidence)
        altered = {'evidencePath': self.law['evidencePath'], 'sha256': digest(Path(self.law['evidencePath']).read_bytes())}
        with self.assertRaises(ValueError): self.save(law_recheck=altered)
        self.assertFalse((self.root / 'output').exists())

    def test_finalize_rechecks_private_staged_and_current_input_drift(self):
        for kind in ('private', 'staged', 'input'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve(); path, plan = fixture_plan(root); human, law = approvals(root, plan)
                def alter(phase, unit):
                    if phase != 'before_rename': return
                    if kind == 'private': target = path.parent / plan['units'][0]['files'][0]['privatePath']
                    elif kind == 'staged': target = next(root.rglob('.formal-staging-*')) / 'manifest.json'
                    else: target = Path(next(iter(plan['inputs'])))
                    target.write_bytes(target.read_bytes() + b' ')
                with self.assertRaises(ValueError): save_formal_units(root, path, human_approval=human, law_recheck=law, receipt_path=root / 'receipt.json', failure_hook=alter)
                self.assertFalse(physical(root, plan['units'][0]['files'][0]['path']).exists())
                self.assertFalse((root / 'receipt.json').exists())

    def test_readonly_retry_next_day_returns_original_receipt_without_new_law(self):
        result = self.save(); original = self.receipt.read_bytes()
        tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
        with patch('tools.question_review_console.formal_correction_save._utcnow', return_value=tomorrow):
            self.assertEqual(self.save(), result)
        self.assertEqual(self.receipt.read_bytes(), original)
        new_record = {**self.law, 'sha256': 'another-proof'}
        with self.assertRaises(ValueError): self.save(law_recheck=new_record)

    def test_interrupted_resume_requires_fresh_law_then_preserves_same_bytes(self):
        def interrupt(phase, unit):
            if phase == 'before_receipt': raise RuntimeError('fixture interruption')
        with self.assertRaises(RuntimeError): self.save(failure_hook=interrupt)
        self.assertFalse(self.receipt.exists())
        tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
        with patch('tools.question_review_console.formal_correction_save._utcnow', return_value=tomorrow):
            with self.assertRaises(ValueError): self.save()
            evidence = json.loads(Path(self.law['evidencePath']).read_bytes())
            for entry in evidence['entries']:
                entry['retrievedAt'] = tomorrow.isoformat(); entry['referenceDate'] = tomorrow.date().isoformat()
                entry['url'] = entry['url'].split('?')[0] + '?asof=' + entry['referenceDate']
                entry['metadataUrl'] = entry['metadataUrl'].split('?')[0] + '?asof=' + entry['referenceDate']
            new_path = self.root / 'fresh-fixture-law.json'; write_json(new_path, evidence)
            fresh = {'evidencePath': str(new_path), 'sha256': digest(new_path.read_bytes())}
            self.save(law_recheck=fresh)
        self.assertTrue(self.receipt.exists())

    def test_after_receipt_interrupt_has_valid_original_readonly_retry(self):
        def interrupt(phase, unit):
            if phase == 'after_receipt': raise RuntimeError('fixture return interrupted')
        with self.assertRaises(RuntimeError): self.save(failure_hook=interrupt)
        original = self.receipt.read_bytes()
        self.assertEqual(len(load_saved_units(self.root, self.path, self.receipt)), 3)
        self.save(); self.assertEqual(self.receipt.read_bytes(), original)

    def test_approval_material_hash_is_in_question_and_detects_changes(self):
        scope = approval_scope(self.plan)
        self.assertEqual(scope['approvalMaterials'], self.plan['approvalMaterials'])
        question = approval_question(self.plan)
        self.assertIn(self.plan['approvalMaterials']['approval-proposal.md'], question)
        self.assertIn('](' + self.plan['approvalMaterialPaths']['approval-proposal.md'] + ')', question)
        for scope in ('2018', '2024', '2020', '選択答4', '肯定形', '否定形', '明らかなたとき→明らかなとき', '本番公開'):
            self.assertIn(scope, question)
        self.assertNotIn('FORMAL_SAVE_BINDING', question)
        (self.path.parent / 'approval-proposal.md').write_text('changed material')
        with self.assertRaises(ValueError): self.save()
        self.assertFalse((self.root / 'output').exists())
