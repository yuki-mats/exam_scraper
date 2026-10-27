"""Real normal prepare/writer/checkpoint path with an injected native model."""
from copy import deepcopy
from dataclasses import replace
import io
import json
import unittest
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import NameObject, DictionaryObject, DecodedStreamObject

from tests import test_feedback_prepared_stage as normal_fixture
from tests import test_official_guidance_evidence as provider_fixture
from tests.qualification_run_test_support import PerQuestionQueueAppServer
from tools.question_bank import feedback_prepared_stage as stage
from tools.question_review_console import official_guidance_contract as contract
from tools.question_review_console import official_guidance_initial_input as initial
from tools.question_review_console import official_guidance_evidence as provider
from tools.question_review_console import qualification_runs as runs


class OfficialGuidanceNormalSaveTests(unittest.TestCase):
    def test_real_save_binds_actual_refresh_to_plan_candidate_native_intent_and_replay(self):
        self._run_real_save(initial_input=False)

    def test_private_initial_input_reaches_real_normal_model_and_save(self):
        self._run_real_save(initial_input=True)

    def test_normal_model_explicitly_revokes_stale_downstream_adoption(self):
        self._run_real_save(initial_input=False, reclassify=True)

    def test_originalization_reworks_body_then_law_owner_adopts_new_bound_proof(self):
        self._run_real_save(initial_input=True, reoriginalize=True)

    def _run_real_save(self, *, initial_input, reclassify=False, reoriginalize=False):
        f = normal_fixture.PreparedStageTests(); f.setUp(); self.addCleanup(f.doCleanups)
        # The generic fixture intentionally uses a short source key distinct
        # from its review ID. This provider fixture uses its explicit complete
        # consistent source binding; no production source is changed.
        from tools.question_review_console.inventory import QuestionInventory
        reader = QuestionInventory(f.root)
        for method in ('projected_input', 'projected_input_for_stage', 'source_input',
                       '_projection_stage_maps', '_projection_issue_index'):
            setattr(f.inventory, method, getattr(reader, method))
        original_group = f.inventory.group
        def group(*args):
            value = original_group(*args)
            question = value['questions'][0]
            key = 'new-exam:2026:' + question['originalQuestionId']
            question['sourceQuestionKey'] = key
            question['source']['sourceQuestionKey'] = key
            question['projected']['sourceQuestionKey'] = key
            if f.request['stageId'] == 'question_set':
                projected = reader.projected_input_for_stage(args[0], args[1], question['sourceRecordRef'], 'question_set')
                question['projected'] = dict(projected.record)
                question['paths']['patches'] = list(projected.applied_files)
            return value
        f.inventory.group = group
        source_path = f.root / f.request['source']['ref']
        source = json.loads(source_path.read_bytes())
        binding = f.request['source']['binding']
        binding['sourceQuestionKey'] = 'new-exam:2026:' + binding['reviewQuestionId']
        source['question_bodies'][0]['sourceQuestionKey'] = binding['sourceQuestionKey']
        f.write(source_path, source)
        f.request['source']['sha256'] = stage._hash(source_path.read_bytes())
        f.write(f.root / stage.MANIFEST, (json.dumps({'path': str(source_path.relative_to(f.root)),
            'sha256': f.request['source']['sha256']}) + '\n').encode())
        writer = PdfWriter(); page = writer.add_blank_page(width=600, height=800)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({
            NameObject('/F1'): DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})})})
        stream = DecodedStreamObject(); stream.set_data(b'BT /F1 12 Tf 30 700 Td (Official institutional reference) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
        buffer = io.BytesIO(); writer.write(buffer); pdf = buffer.getvalue()
        request = {'schemaVersion': provider.REQUEST_SCHEMA, 'provider': provider.PROVIDER,
            'currentAsOf': '2026-10-25', 'examAsOf': None, 'currentPages': [1], 'historicalDocuments': [],
            'choiceBindings': [{'choiceIndex': 0, 'currentPages': [1], 'historicalSelections': []}]}
        resolver = provider.OfficialGuidanceEvidenceResolver(f.root, fetcher=lambda url, _: provider.OfficialGuidanceFetch(
            provider_fixture.INDEX if url == provider.INDEX_URL else pdf, url,
            'text/html' if url == provider.INDEX_URL else 'application/pdf'), now=lambda: '2026-10-25T09:00:00+00:00')
        class Model(normal_fixture.NativeProfileFixture):
            def run_turn(self, prompt, **kwargs):
                result = super().run_turn(prompt, **kwargs)
                if kwargs['work_type'] != 'maintenance_law_context_candidate': return result
                question = PerQuestionQueueAppServer._candidate_questions(prompt)[0]
                actual = question['officialGuidanceEvidence']
                refs = {key: actual[key] for key in ('request', 'evidenceRef')}
                refs['applicabilityByChoice'] = [{'choiceIndex': 0, 'currentBasis': '資料の本文を確認した。',
                    'examTimeBasis': '試験時点の確認は次の独立監査へ渡す。'}]
                fields = {'isLawRelated': True, 'lawGroundedExplanationNotNeeded': False,
                    'lawReferences': [[]], 'lawContextForExplanation': '制度資料を独立に確認する。', contract.FIELD: refs}
                return replace(result, final_message=json.dumps({'decision': 'candidate', 'summary': '根拠候補',
                    'update': {'setFields': [{'field': key, 'value': value} for key, value in fields.items()],
                        'unsetFields': []}}, ensure_ascii=False))
        f.coordinator.app_server = Model()
        f.request.update(stageId='law_context', officialGuidanceRequest=request); f.write(f.request_path, f.request)
        with patch.object(contract, 'OfficialGuidanceEvidenceResolver', return_value=resolver), \
                patch.object(provider, 'OfficialGuidanceEvidenceResolver', return_value=resolver), \
                patch.object(runs, '_now', return_value='2026-10-25T09:00:00+00:00'), \
                patch.object(stage, 'verify_independent_approval', return_value={'reviewerSessionId': 'independent'}):
            if initial_input:
                f.request.pop('officialGuidanceRequest')
                f.write(f.request_path, f.request)
                target = f.inventory.group('new-exam', '2026')['questions'][0]
                initial.register_initial_input(f.root, 'new-exam', target,
                    target['projected'], request, resolver=resolver)
            f.prepare(); plan = f.plan()
            receipt = f.apply()
            proof = receipt['stageEvidence']['officialGuidanceSaveRefresh']
            self.assertEqual(proof['saveOperation']['planHash'], plan['planHash'])
            self.assertEqual(proof['candidateHash'], stage.sha256_json(plan['preparedCandidate']['content']['candidatePayload']))
            self.assertTrue(proof['refresh']['receipt']['checkedAt'])
            self.assertEqual(receipt, f.apply())  # A completed replay never re-fetches/writes.
            child = f.coordinator.store.get(plan['qualification'], plan['childRunId'])
            changed = deepcopy(proof); changed['candidateHash'] = 'b' * 64
            changed['bindingHash'] = stage.sha256_json({k:v for k,v in changed.items() if k != 'bindingHash'})
            with self.assertRaises(runs.QualificationRunError):
                f.coordinator.store.update(plan['qualification'], plan['childRunId'], officialGuidanceSaveRefresh=changed)
            original_get = f.coordinator.store.get
            def corrupted(qualification, run_id):
                value = original_get(qualification, run_id)
                if run_id == plan['childRunId']: value['officialGuidanceSaveRefresh'] = changed
                return value
            with patch.object(f.coordinator.store, 'get', side_effect=corrupted):
                with self.assertRaises(ValueError): stage._stage_evidence(f.coordinator, plan)
            self.assertEqual(stage._stage_evidence(f.coordinator, plan), receipt['stageEvidence'])
            # 04 owns classification only: inherit and validate the source-bound
            # positive proof without delivering 68KB or declaring a fresh save.
            f.write(f.root / 'output/new-exam/category/category.json', {'folders': [{'folderId': 'folder', 'name': '分類'}],
                'questionSets': [{'questionSetId': 'test-question-set', 'folderId': 'folder', 'name': '制度'}]})
            f.request['stageId'] = 'question_set'; f.request.pop('officialGuidanceRequest', None)
            f.request_path = f.private / 'question-set-request.json'; f.output = f.private / 'question-set'
            f.write(f.request_path, f.request)
            f.prepare(); classification_plan = f.plan(); classification_receipt = f.apply()
            inherited = classification_receipt['stageEvidence']['officialGuidanceInheritedProof']
            self.assertEqual(inherited['evidenceHash'], proof['refresh']['receipt']['pinnedEvidenceHash'])
            self.assertNotIn('officialGuidanceSaveRefresh', classification_receipt['stageEvidence'])
            self.assertNotIn('officialGuidanceInputs', classification_plan['stagePlan'])
            self.assertEqual(classification_receipt, f.apply())
            if reoriginalize:
                class Reauthor(Model):
                    def run_turn(self, prompt, **kwargs):
                        result = super().run_turn(prompt, **kwargs)
                        if kwargs['work_type'] != 'maintenance_originalize_candidate': return result
                        payload = json.loads(result.final_message)
                        for item in payload['update']['setFields']:
                            if item['field'] == 'questionBodyText':
                                item['value'] += ' 使用形態の定義を明確にした。'
                        return replace(result, final_message=json.dumps(payload, ensure_ascii=False))
                f.coordinator.app_server = Reauthor()
                target = f.inventory.group('new-exam', '2026')['questions'][0]
                f.request['stageId'] = 'originalize'
                options = {'stage_ids': ['originalize'], 'list_group_ids': ['2026'],
                    'question_ids': [target['id']], 'question_concurrency': 1}
                preview = f.coordinator.preview('new-exam', 'originalize', 'refresh', **options)
                self.assertTrue(preview['canStart'], preview)
                started = f.coordinator.start('new-exam', 'originalize', 'refresh',
                    preview['previewToken'], **options)
                import time
                deadline = time.monotonic() + 30
                while True:
                    run = f.coordinator.store.get('new-exam', started['run']['runId'])
                    if run['status'] not in ('running', 'pending', 'queued'): break
                    if time.monotonic() >= deadline: self.fail('normal originalization did not terminate')
                    time.sleep(.05)
                self.assertEqual(run['status'], 'succeeded', run)
                progress = f.coordinator.progress('new-exam', started['run']['runId'])
                self.assertEqual(progress['blockedWorkItemCount'], 0, progress)
                self.assertEqual(progress['validatedWorkItemCount'], 1, progress)
                rewritten_record = reader.projected_input('new-exam', '2026', binding['sourceRecordRef']).record
                self.assertIn('使用形態の定義', rewritten_record['questionBodyText'])
                self.assertEqual(rewritten_record[contract.FIELD], proof['reference'])
                changed_reference = deepcopy(rewritten_record)
                changed_reference[contract.FIELD]['evidenceRef']['sha256'] = 'b' * 64
                with self.assertRaises(ValueError):
                    contract.reference_record_for_save(rewritten_record, changed_reference, stage_id='originalize')
                self.assertEqual(stage._hash(source_path.read_bytes()), f.request['source']['sha256'])
                with self.assertRaises(provider.OfficialGuidanceEvidenceError):
                    contract.validate_reference(f.root, rewritten_record[contract.FIELD],
                        record=rewritten_record, resolver=resolver)
                target = f.inventory.group('new-exam', '2026')['questions'][0]
                # The old initial input also remains stale; no automatic relabel.
                with self.assertRaises(provider.OfficialGuidanceEvidenceError):
                    initial.load_stage_input(f.root, 'new-exam', target, rewritten_record,
                        stage_id='law_context', current_as_of='2026-10-25', resolver=resolver)
                pointer = initial.input_path(f.root, 'new-exam', target)
                before = stage._hash(pointer.read_bytes())
                initial.register_initial_input(f.root, 'new-exam', target, rewritten_record,
                    request, replace_if_sha=before, resolver=resolver)
                supplied, from_initial = initial.load_stage_input(f.root, 'new-exam', target,
                    rewritten_record, stage_id='law_context', current_as_of='2026-10-25', resolver=resolver)
                self.assertTrue(from_initial)
                self.assertNotEqual(supplied['evidenceRef'], proof['reference']['evidenceRef'])
                for stage_id in ('explanation', 'law_audit'):
                    with self.assertRaises(provider.OfficialGuidanceEvidenceError):
                        initial.load_stage_input(f.root, 'new-exam', target, rewritten_record,
                            stage_id=stage_id, current_as_of='2026-10-25', resolver=resolver)
                f.coordinator.app_server = Model()
                f.request['stageId'] = 'law_context'
                f.request_path = f.private / 'rebind-request.json'; f.output = f.private / 'rebind'
                f.write(f.request_path, f.request)
                f.prepare(); rebound = f.apply()
                refreshed = rebound['stageEvidence']['officialGuidanceSaveRefresh']
                self.assertEqual(refreshed['reference']['evidenceRef'], supplied['evidenceRef'])
                current = reader.projected_input('new-exam', '2026', binding['sourceRecordRef']).record
                contract.validate_reference(f.root, current[contract.FIELD], record=current, resolver=resolver)
                self.assertEqual(rebound, f.apply())
            if reclassify:
                # Represent an independently saved upstream classification with
                # an older reference still present in the next stage's input.
                path = next((f.root / 'output/new-exam/questions_json/2026/18_law_context_prepared').glob('*.json'))
                payload = json.loads(path.read_bytes())
                payload[0].update(isLawRelated=False, lawGroundedExplanationNotNeeded=True)
                f.write(path, payload)
                class Revoke(Model):
                    def run_turn(self, prompt, **kwargs):
                        result = super().run_turn(prompt, **kwargs)
                        question = PerQuestionQueueAppServer._candidate_questions(prompt)[0]
                        self_test.assertEqual(question['officialGuidanceEvidence']['evidenceRef'], request_ref)
                        fields = {'isLawRelated': False, 'lawGroundedExplanationNotNeeded': True,
                            'lawReferences': [[]], 'lawContextForExplanation': '本問は一般知識を問う。'}
                        return replace(result, final_message=json.dumps({'decision': 'candidate', 'summary': '分類を再判定した。',
                            'update': {'setFields': [{'field': k, 'value': v} for k, v in fields.items()],
                                'unsetFields': [contract.FIELD]}}, ensure_ascii=False))
                self_test, request_ref = self, proof['reference']['evidenceRef']
                f.coordinator.app_server = Revoke()
                f.request['stageId'] = 'law_context'
                f.request_path = f.private / 'reclassify-request.json'; f.output = f.private / 'reclassify'
                f.write(f.request_path, f.request)
                f.prepare(); reclassified = f.apply()
                self.assertNotIn('officialGuidanceSaveRefresh', reclassified['stageEvidence'])
                current = reader.projected_input('new-exam', '2026', binding['sourceRecordRef']).record
                self.assertFalse(current['isLawRelated'])
                self.assertNotIn(contract.FIELD, current)
