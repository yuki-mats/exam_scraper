"""Positive institutional evidence, actual prompt handoff and fail-closed gates."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor

from tests import test_official_guidance_evidence as provider_fixture
from scripts.check.check_law_revision_fact_coverage import audit_question_level_law_evidence
from scripts.convert.convert_merged_to_firestore import convert_true_false_to_firestore
from tools.question_review_console import official_guidance_contract as contract
from tools.question_review_console import qualification_runs as runs
from tools.question_review_console import law_audit_verification as audit
from tools.question_review_console.question_candidate import CandidateTarget, CandidateUpdate, QuestionCandidate


class OfficialGuidancePipelineTests(unittest.TestCase):
    def test_save_refresh_binds_original_approved_bytes_after_target_reordering(self):
        candidate = QuestionCandidate('question', 'candidate', '固定候補。', (
            CandidateUpdate('explanation', {'explanationText': ['正しい。']}, ()),
            CandidateUpdate('law', {'lawReferences': [[]], 'isLawRelated': True}, ()),
        ))
        approved = runs._question_candidates_payload([candidate])
        # The real prepared parser can reorder targets without changing fields.
        parsed = replace(candidate, updates=tuple(reversed(candidate.updates)))
        self.assertNotEqual(runs.sha256_json(approved), runs.sha256_json(runs._question_candidates_payload([parsed])))
        self.assertEqual(runs._official_guidance_save_candidate_hash(parsed, {'candidatePayload': approved}),
            runs.sha256_json(approved))
        self.assertEqual(runs._official_guidance_save_candidate_hash(parsed),
            runs.sha256_json(runs._question_candidates_payload([parsed])))
        for mutation in ('field', 'question', 'summary', 'duplicate', 'multiple'):
            with self.subTest(mutation=mutation):
                changed = deepcopy(approved)
                row = changed['questionResults'][0]
                if mutation == 'field': row['updates'][0]['setFields'][0]['value'] = ['間違い。']
                if mutation == 'question': row['questionId'] = 'another-question'
                if mutation == 'summary': row['summary'] = '別の候補。'
                if mutation == 'duplicate': row['updates'].append(deepcopy(row['updates'][0]))
                if mutation == 'multiple': changed['questionResults'].append(deepcopy(row))
                with self.assertRaises(runs.QualificationRunError):
                    runs._official_guidance_save_candidate_hash(parsed, {'candidatePayload': changed})

    def setUp(self):
        self.fixture = provider_fixture.OfficialGuidanceEvidenceTests()
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.root, self.record, self.scope, self.request = f.root, f.record, f.scope, f.request
        self.addCleanup(patch.stopall)
        patch.object(contract, 'source_scope', return_value=self.scope).start()
        patch.object(contract, 'OfficialGuidanceEvidenceResolver', return_value=f.resolver).start()
        self.delivered = contract.prepare_evidence(self.root, self.request, record=self.record,
            scope=self.scope, resolver=f.resolver)
        self.references = {key: self.delivered[key] for key in ('request', 'evidenceRef')}
        self.references['applicabilityByChoice'] = [
            {'choiceIndex': index, 'currentBasis': '条項と例外を現在時点で確認する。',
                'examTimeBasis': '歴史版の適用は独立監査で確認する。'} for index in range(2)]
        self.record.update(isLawRelated=True, lawGroundedExplanationNotNeeded=False,
            lawReferences=[[], []], officialGuidanceReferences=self.references)

    def validate(self, value=None, **kwargs):
        return contract.validate_reference(self.root, value or self.references,
            record=self.record, scope=self.scope, **kwargs)

    def test_private_pointer_reads_actual_receipt_and_raw_cache_not_a_hash_claim(self):
        receipt = self.validate()
        self.assertEqual(receipt, self.delivered['receipt'])
        self.assertNotIn('documents', self.references)
        for mutation in ('hash', 'path', 'request', 'choice', 'scope'):
            with self.subTest(mutation=mutation):
                value = deepcopy(self.references)
                if mutation == 'hash': value['evidenceRef']['sha256'] = 'b' * 64
                if mutation == 'path': value['evidenceRef']['ref'] = '../outside.json'
                if mutation == 'request': value['request']['currentPages'] = [9]
                if mutation == 'choice': value['applicabilityByChoice'].pop()
                if mutation == 'scope': self.record['choiceTextList'][0] += '改変'
                with self.assertRaises((ValueError, OSError)): self.validate(value)
                if mutation == 'scope': self.record['choiceTextList'][0] = '第1の完全命題。'

    def test_guidance_only_requires_positive_proof_and_cannot_replace_missing_statute(self):
        empty = {'schemaVersion': 'primary-law-evidence/v2', 'items': [], 'status': 'not_applicable'}
        result = runs._law_context_evidence_view(self.root, self.record, empty)
        self.assertEqual(result['officialGuidance']['evidenceHash'], self.delivered['receipt']['evidenceHash'])
        missing = deepcopy(self.record); missing.pop(contract.FIELD)
        with self.assertRaises(runs.QualificationRunError):
            runs._law_context_evidence_view(self.root, missing, empty)
        mixed = deepcopy(self.record)
        mixed['lawReferences'][0] = [{'lawId': '337AC0000000069', 'article': '18'}]
        with self.assertRaises(runs.QualificationRunError):
            runs._law_context_evidence_view(self.root, mixed, empty)

    def prompt(self, stage='law_context'):
        target = {'id': self.scope['questionId'], 'listGroupId': self.scope['listGroupId'], **self.scope['sourceBinding']}
        allowed = tuple(['isLawRelated', 'lawGroundedExplanationNotNeeded', 'lawReferences',
            'lawContextForExplanation', contract.FIELD])
        candidate_target = CandidateTarget('law', 'law_context', '18_law_context_prepared/q.json', allowed)
        prompt = runs._structured_candidate_prompt('独立に判断する。', [target], stage_id=stage,
            records_by_question={self.scope['questionId']: self.record},
            candidate_targets_by_question={self.scope['questionId']: (candidate_target,)},
            feedback_by_question={}, official_guidance_evidence_by_question={self.scope['questionId']: self.delivered})
        return prompt, target, candidate_target

    def test_03_prompt_receives_real_pages_and_source_kind_without_public_projection(self):
        prompt, _, _ = self.prompt('explanation')
        question = audit.question_inputs_from_prompt(prompt)[self.scope['questionId']]
        self.assertEqual(question['officialGuidanceEvidence'], self.delivered)
        self.assertIn('%PDF-current page 5', prompt)
        self.assertEqual(question['officialGuidanceEvidence']['receipt']['historicalCompleteness'], 'unreviewed')
        record = {**self.record, 'questionType': 'true_false', 'correctChoiceText': ['正しい', '間違い'],
            'explanationText': ['正しい。', '間違い。管理組合が負担する。'], 'questionSetId': 'set',
            'original_question_id': 'source-id', 'originalQuestionId': 'source-id', 'examYear': 2023}
        documents = convert_true_false_to_firestore(record)
        self.assertTrue(documents)
        self.assertTrue(all(contract.FIELD not in doc and 'officialGuidanceEvidence' not in doc
            and 'evidenceRef' not in json.dumps(doc, ensure_ascii=False) for doc in documents))

    def test_model_schema_pins_acquisition_metadata_but_keeps_applicability_judgment(self):
        from tools.question_review_console.question_candidate import output_schema, QuestionCandidateError
        _, _, target = self.prompt()
        qid = self.scope['questionId']; targets = {qid: (target,)}
        ordinary = output_schema([qid], targets)
        fixed = output_schema([qid], targets, official_guidance_inputs={qid: self.delivered})
        variants = fixed['properties']['update']['properties']['setFields']['items']['anyOf']
        value = next(v['properties']['value'] for v in variants
            if v['properties']['field']['const'] == contract.FIELD)
        for key in ('request', 'evidenceRef'):
            self.assertEqual(value['properties'][key]['enum'], [self.delivered[key]])
        self.assertNotIn('enum', value['properties']['applicabilityByChoice'])
        self.assertIn(contract.FIELD, fixed['properties']['update']['properties']['unsetFields']['items']['enum'])
        self.assertEqual(output_schema([qid], targets), ordinary)
        self.assertEqual(output_schema([qid], targets, official_guidance_inputs={}), ordinary)
        with self.assertRaises(QuestionCandidateError):
            output_schema([qid], targets, official_guidance_inputs={qid: self.delivered, 'foreign': self.delivered})
        with self.assertRaises(QuestionCandidateError):
            output_schema([qid], targets, official_guidance_inputs={'foreign': self.delivered})
        nonowner = CandidateTarget('type', 'question_type', 'q.json', ('questionType',))
        with self.assertRaises(QuestionCandidateError):
            output_schema([qid], {qid: (nonowner,)}, official_guidance_inputs={qid: self.delivered})
        changed = deepcopy(self.references)
        changed['request']['historicalDocuments'][0]['revisionLabel'] += '適用確認済み'
        with self.assertRaisesRegex(ValueError, 'binding/hash differs: requestHash'):
            self.validate(changed)

    def test_guidance_grounding_regenerates_actual_candidate_and_blocked_stays_blocked(self):
        prompt, target, candidate_target = self.prompt()
        fields = {key: self.record[key] for key in ('isLawRelated', 'lawGroundedExplanationNotNeeded', 'lawReferences', contract.FIELD)}
        fields['lawContextForExplanation'] = '制度資料を確認した。'
        candidate = QuestionCandidate(self.scope['questionId'], 'candidate', '資料適用を独立に判断した。',
            (CandidateUpdate('law', fields, ()),))
        owner = SimpleNamespace(repo_root=self.root, workflow=SimpleNamespace(prompt_from_plan=lambda _: {'prompt': ''}),
            primary_law_evidence=SimpleNamespace(resolve=lambda *a, **k: {'schemaVersion': 'primary-law-evidence/v2',
                'currentAsOf': '2026-10-25', 'items': [], 'status': 'not_applicable'}))
        output = SimpleNamespace(thread_id='real-injected-thread', session_id='session', turn_id='turn',
            model='fixture-model', final_message='candidate')
        calls = []
        def regenerate(actual):
            calls.append(actual)
            return output, (candidate,)
        kwargs = dict(batch_plan={}, records_by_question={candidate.question_id: self.record},
            targets_by_question={candidate.question_id: (candidate_target,)},
            raw_target_by_question={candidate.question_id: target}, regenerate=regenerate)
        with patch.object(runs, '_structured_candidate_stage_context', return_value={}), patch.object(runs, '_canonical_document_guidance', return_value=''):
            final, proof = runs.QualificationRunCoordinator._ground_law_context_candidate(owner, 'kanrigyoumu', candidate, prompt, **kwargs)
            self.assertEqual(final, candidate)
            self.assertEqual(len(calls), 1)
            self.assertIn(self.delivered['receipt']['evidenceHash'], calls[0])
            self.assertEqual(proof['currentEvidence']['officialGuidance'], self.delivered['receipt'])
            kwargs['regenerate'] = lambda _: (output, (replace(candidate, status='blocked', updates=()),))
            final, proof = runs.QualificationRunCoordinator._ground_law_context_candidate(owner, 'kanrigyoumu', candidate, prompt, **kwargs)
            self.assertEqual(final.status, 'blocked'); self.assertEqual(proof['currentEvidence'], [])

    def test_genuine_statutory_replacement_explicitly_unsets_guidance_and_finishes_one_round(self):
        from tests.test_law_context_primary_cycle import LAW_ID, XML
        from tools.question_review_console.primary_law_evidence import PrimaryLawEvidenceResolver, LawFileSnapshot
        prompt, target, candidate_target = self.prompt()
        references = [[{'role': 'current_basis', 'scope': 'choice', 'choiceIndex': index, 'lawId': LAW_ID,
            'lawTitle': '試験法', 'article': '11', 'referenceDate': '2026-10-25',
            'verificationStatus': 'verified', 'source': 'egov_xml'}] for index in range(2)]
        fields = {'isLawRelated': True, 'lawGroundedExplanationNotNeeded': False,
            'lawReferences': references, 'lawContextForExplanation': '実取得した法令本文を採用する。'}
        candidate = QuestionCandidate(self.scope['questionId'], 'candidate', '実法令を独立に採用した。',
            (CandidateUpdate('law', fields, (contract.FIELD,)),))
        resolver = PrimaryLawEvidenceResolver(self.root, fetcher=lambda law, date: LawFileSnapshot(law, date,
            f'https://laws.e-gov.go.jp/api/2/law_file/xml/{law}?asof={date}', 'fixture-revision', XML))
        owner = SimpleNamespace(repo_root=self.root, workflow=SimpleNamespace(prompt_from_plan=lambda _: {'prompt': ''}),
            primary_law_evidence=resolver)
        calls = []
        def regenerate(actual):
            calls.append(actual)
            return SimpleNamespace(thread_id='thread', session_id='session', turn_id='turn', model='fixture', final_message='candidate'), (candidate,)
        with patch.object(runs, '_structured_candidate_stage_context', return_value={}), \
                patch.object(runs, '_canonical_document_guidance', return_value=''), patch.object(runs, '_now', return_value='2026-10-25'):
            final, proof = runs.QualificationRunCoordinator._ground_law_context_candidate(owner, 'kanrigyoumu', candidate, prompt,
                batch_plan={}, records_by_question={candidate.question_id: self.record},
                targets_by_question={candidate.question_id: (candidate_target,)}, raw_target_by_question={candidate.question_id: target}, regenerate=regenerate)
        self.assertEqual(final, candidate); self.assertEqual(len(calls), 1)
        self.assertIsInstance(proof['currentEvidence'], list)
        self.assertEqual(len(proof['currentEvidence']), 2)

    def test_source_change_rejects_cached_proof_and_fresh_refresh_is_separately_saved(self):
        receipt = self.validate(refresh=True)
        self.assertEqual(receipt, self.delivered['receipt'])
        saved = list((self.root / contract.PROOF_DIRECTORY / 'refreshes').glob('*.json'))
        self.assertEqual(len(saved), 1)
        wrapper = json.loads(saved[0].read_bytes())
        self.assertEqual(wrapper['pinnedEvidenceHash'], receipt['evidenceHash'])
        self.fixture.payloads[provider_fixture.CURRENT] = replace(self.fixture.payloads[provider_fixture.CURRENT], data=b'%PDF-changed')
        with self.assertRaises(ValueError): self.validate(refresh=True)
        self.assertEqual(len(list((self.root / contract.PROOF_DIRECTORY / 'refreshes').glob('*.json'))), 1)

    def test_independent_review_receives_same_actual_pages_and_requires_all_source_urls(self):
        fields = {contract.FIELD: self.references, 'isLawRelated': True,
            'correctChoiceText': ['正しい', '間違い'], 'examTimeDecision': ['正しい', '間違い'],
            'currentLawDecision': ['正しい', '間違い']}
        candidate = QuestionCandidate('ui-id', 'candidate', '独立監査対象', (CandidateUpdate('audit', fields, ()),))
        bundle = audit.verification_bundle({'questionId': 'ui-id', 'currentRecord': self.record}, candidate,
            proposed_official_guidance_evidence=self.delivered['receipt'])
        with patch.object(audit, 'validate_guidance', side_effect=lambda _root, refs, **kwargs: self.validate(refs)):
            secondary = audit.review_prompt(bundle, '二次')
            tertiary = audit.review_prompt(bundle, '三次')
            for prompt in (secondary, tertiary):
                self.assertIn(self.delivered['receipt']['evidenceHash'], prompt)
                self.assertIn('%PDF-current page 5', prompt)
            reply = {'schemaVersion': audit.SCHEMA_VERSION, 'questionId': 'ui-id', 'evidenceHash': bundle['evidenceHash'],
                'decision': 'approve', 'examTimeDecision': fields['examTimeDecision'],
                'currentLawDecision': fields['currentLawDecision'], 'issues': [], 'verifiedSourceUrls': [provider_fixture.CURRENT]}
            with self.assertRaises(ValueError): audit.parse_review(json.dumps(reply), bundle)
            reply['verifiedSourceUrls'].append(provider_fixture.HISTORICAL)
            self.assertEqual(audit.parse_review(json.dumps(reply), bundle), reply)
            changed = deepcopy(bundle); changed['proposedOfficialGuidanceEvidence']['documents'][0]['pages'][0]['text'] = '改変'
            with self.assertRaises(ValueError): audit.review_prompt(changed, '二次')

    def test_split_public_checker_requires_full_source_bijection_and_keeps_statutory_gate(self):
        public = [{'originalQuestionId': 'source-id', 'originalQuestionBodyText': self.record['questionBodyText'],
            'originalQuestionChoiceText': choice, 'isLawRelated': True} for choice in self.record['choiceTextList']]
        def check(rows):
            return audit_question_level_law_evidence(rows, require_law_references=True,
                require_verified_law_references=True, institutional_evidence_records=[self.record])
        with patch('scripts.check.check_law_revision_fact_coverage.has_guidance_basis',
                side_effect=lambda record, **kwargs: contract.has_guidance_basis(record, root=self.root, **kwargs)):
            self.assertEqual(check(public), [])
            self.assertTrue(check(public[:1]))
            bad = deepcopy(public); bad[0]['originalQuestionChoiceText'] += '別の肢'
            self.assertTrue(check(bad))
            mixed = deepcopy(public); mixed[0]['lawReferences'] = [{'lawId': 'real-id', 'verificationStatus': 'unverified'}]
            self.assertTrue(check(mixed))

    def test_atomic_proof_interruption_replay_and_conflict(self):
        receipt = deepcopy(self.delivered['receipt'])
        path = self.root / contract.PROOF_DIRECTORY / (receipt['evidenceHash'] + '.json')
        path.unlink()
        with patch('tools.question_bank.feedback_verified_save._rename_exclusive', side_effect=InterruptedError('interrupted')):
            with self.assertRaises(InterruptedError): contract.store_evidence(self.root, receipt)
        self.assertFalse(path.exists())
        with ThreadPoolExecutor(max_workers=3) as pool:
            refs = list(pool.map(lambda _: contract.store_evidence(self.root, receipt), range(3)))
        self.assertEqual(refs, [refs[0]] * 3)
        changed = deepcopy(receipt); changed['applicabilityStatus'] = 'forged'
        with self.assertRaises(ValueError): contract.store_evidence(self.root, changed)
        self.assertEqual(self.validate(), receipt)

    def test_reclassification_requires_explicit_removal_and_bundle_honors_unset(self):
        bad = {**self.record, 'isLawRelated': False, 'lawGroundedExplanationNotNeeded': True}
        with self.assertRaises(ValueError):
            contract.validate_reference(self.root, self.references, record=bad)
        candidate = QuestionCandidate('ui-id', 'candidate', '制度問題ではないと独立に判断した。', (
            CandidateUpdate('audit', {'isLawRelated': False, 'lawGroundedExplanationNotNeeded': True},
                (contract.FIELD,)),))
        bundle = audit.verification_bundle({'questionId': 'ui-id', 'currentRecord': self.record}, candidate)
        self.assertEqual(bundle['proposedUnsetFields'], [contract.FIELD])
        audit.validate_verification_bundle(bundle, repo_root=self.root)

    def test_actual_delivery_helper_rejects_missing_native_identity_or_changed_prompt_input(self):
        from tools.question_review_console.projection import sha256_json
        prompt, _, _ = self.prompt()
        inputs = {self.scope['questionId']: self.delivered}
        delivery = {'inputsHash': sha256_json(inputs), 'promptHash': hashlib.sha256(prompt.encode()).hexdigest(),
            'threadId': 'actual-primary', 'turnId': 'actual-turn', 'inputFingerprint': 'input', 'projectedInputHash': 'projection'}
        contract.verify_delivery(inputs, prompt, delivery, input_fingerprint='input', projected_input_hash='projection')
        for key in ('threadId', 'turnId', 'promptHash', 'inputsHash', 'inputFingerprint', 'projectedInputHash'):
            bad = {**delivery, key: ''}
            with self.subTest(key=key), self.assertRaises(ValueError):
                contract.verify_delivery(inputs, prompt, bad, input_fingerprint='input', projected_input_hash='projection')
        changed = deepcopy(inputs); changed[self.scope['questionId']]['receipt']['documents'][0]['pages'][0]['text'] = '別本文'
        with self.assertRaises(ValueError):
            contract.verify_delivery(changed, prompt, delivery, input_fingerprint='input', projected_input_hash='projection')

    def test_real_03b_replay_helper_requires_same_source_bundle_hash_and_independent_turn(self):
        from tools.question_bank import feedback_source_tf_delivery as publication
        from tools.question_review_console.projection import sha256_json
        fields = {contract.FIELD: self.references, 'isLawRelated': True, 'auditStatus': 'same_as_current',
            'correctChoiceText': ['正しい', '間違い'], 'examTimeDecision': ['正しい', '間違い'],
            'currentLawDecision': ['正しい', '間違い'], 'lawRevisionFacts': [
                {'auditStatus': 'same_as_current', 'examTime': {'correctChoiceText': value},
                    'current': {'correctChoiceText': value}} for value in ('正しい', '間違い')]}
        candidate = QuestionCandidate('ui-id', 'candidate', '実監査対象', (CandidateUpdate('audit', fields, ()),))
        bundle = audit.verification_bundle({'questionId': 'ui-id', 'currentRecord': self.record}, candidate,
            proposed_official_guidance_evidence=self.delivered['receipt'])
        reply = {'schemaVersion': audit.SCHEMA_VERSION, 'questionId': 'ui-id', 'evidenceHash': bundle['evidenceHash'],
            'decision': 'approve', 'examTimeDecision': fields['examTimeDecision'], 'currentLawDecision': fields['currentLawDecision'],
            'issues': [], 'verifiedSourceUrls': [provider_fixture.CURRENT, provider_fixture.HISTORICAL]}
        with patch.object(audit, 'validate_guidance', side_effect=lambda _root, refs, **kwargs: self.validate(refs)):
            execution = {'phase': 'secondary', 'threadId': 'secondary', 'turnId': 'turn', 'executionId': 'secondary:turn',
                'review': reply, 'officialGuidanceEvidenceHash': self.delivered['receipt']['evidenceHash'],
                'promptHash': hashlib.sha256(audit.review_prompt(bundle, '二次', repo_root=self.root).encode()).hexdigest()}
            receipt = {'evidenceHash': bundle['evidenceHash'], 'bundle': bundle, 'reviews': [execution]}
            plan = {'request': {'questionId': 'ui-id'}, 'preparedCandidate': {'content': {
                'executionMetadata': {'lawAuditVerification': {'ui-id': receipt}}}}}
            child = {'threadId': 'primary', 'lawAuditReviewExecutions': [execution]}
            # The actual writer serializes immutable envelopes with sorted
            # keys; a GUID-only independent prompt must replay byte-for-byte.
            self.assertEqual(audit.review_prompt(bundle, '二次', repo_root=self.root),
                audit.review_prompt(json.loads(json.dumps(bundle, sort_keys=True)), '二次', repo_root=self.root))
            plan = json.loads(json.dumps(plan, sort_keys=True))
            child = json.loads(json.dumps(child, sort_keys=True))
            result = publication._normal_law_verification(plan, child, root=self.root)
            self.assertEqual(result['reviewCount'], 1)
            for key in ('promptHash', 'officialGuidanceEvidenceHash', 'threadId', 'turnId'):
                bad_plan, bad_child = deepcopy(plan), deepcopy(child)
                bad_plan['preparedCandidate']['content']['executionMetadata']['lawAuditVerification']['ui-id']['reviews'][0][key] = ''
                bad_child['lawAuditReviewExecutions'][0][key] = ''
                with self.subTest(key=key), self.assertRaises((ValueError, publication.GateRequired)):
                    publication._normal_law_verification(bad_plan, bad_child, root=self.root)
