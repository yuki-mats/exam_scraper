"""02b normal preparation with real XML resolution and an injected native model."""
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from tests import test_feedback_prepared_stage as fixtures
from tests.test_feedback_prepared_stage import NativeProfileFixture
from tests.qualification_run_test_support import PerQuestionQueueAppServer
from tools.question_bank import feedback_prepared_stage as stage
from tools.question_review_console.primary_law_evidence import PrimaryLawEvidenceResolver, LawFileSnapshot
from tools.question_review_console.qualification_runs import (
    QualificationRunError, _law_context_current_evidence, _question_candidates_payload,
)
from tools.question_review_console.law_audit_verification import candidate_fields
from tools.question_review_console.question_candidate import parse_prepared_candidate_payload
from tools.question_review_console.question_candidate import CandidateTarget, CandidateUpdate, QuestionCandidate
from tools.question_review_console.qualification_runs import _structured_candidate_prompt


LAW_ID = '123AC0000000001'
XML = '''<Law><LawBody><LawTitle>試験法</LawTitle><MainProvision>
<Article Num="11"><ArticleTitle>第十一条</ArticleTitle><Paragraph><Sentence>制度の定義を定める。</Sentence></Paragraph></Article>
<Article Num="12"><ArticleTitle>第十二条</ArticleTitle><Paragraph><Sentence>登録の手続を定める。</Sentence></Paragraph></Article>
</MainProvision></LawBody></Law>'''


class LawModel(NativeProfileFixture):
    def __init__(self):
        super().__init__(); self.articles = ['11']; self.title = '試験法'; self.force_unverified = False
        self.source = 'egov_xml'; self.use_observed_source_url = False

    def run_turn(self, prompt, **kwargs):
        result = super().run_turn(prompt, **kwargs)
        if kwargs['work_type'] != 'maintenance_law_context_candidate': return result
        question = PerQuestionQueueAppServer._candidate_questions(prompt)[0]
        call = len(self.calls)
        article = self.articles[min(call - 1, len(self.articles) - 1)]
        reference = {'role': 'current_basis', 'scope': 'choice', 'choiceIndex': 0,
            'lawId': LAW_ID, 'lawTitle': self.title, 'article': article,
            'referenceDate': question['primaryLawEvidence']['currentAsOf'],
            'verificationStatus': 'unverified' if call == 1 or self.force_unverified else 'verified', 'source': self.source}
        if self.use_observed_source_url and question['primaryLawEvidence']['items']:
            reference['source'] = question['primaryLawEvidence']['items'][0]['currentSnapshot']['sourceUrl']
        values = {'isLawRelated': True, 'lawGroundedExplanationNotNeeded': False,
            'lawReferences': [[reference]], 'lawContextForExplanation': '試験法の本文と設問の制度を照合した。'}
        payload = {'decision': 'candidate', 'summary': '法令根拠を確認する。',
            'update': {'setFields': [{'field': key, 'value': value} for key, value in values.items()],
                'unsetFields': ['officialGuidanceReferences'] if any('officialGuidanceReferences' in target['allowedFields']
                    for target in question['candidateTargets']) else []}}
        return replace(result, final_message=json.dumps(payload, ensure_ascii=False))


class LawContextPrimaryCycleTests(unittest.TestCase):
    write = staticmethod(fixtures.PreparedStageTests.write)
    prepare = fixtures.PreparedStageTests.prepare
    plan = fixtures.PreparedStageTests.plan
    apply = fixtures.PreparedStageTests.apply

    def setUp(self):
        fixtures.PreparedStageTests.setUp(self)
        self.request['stageId'] = 'law_context'; self.write(self.request_path, self.request)
        self.model = LawModel(); self.coordinator.app_server = self.model
        self.fetches = []
        def fetch(law_id, as_of):
            self.fetches.append((law_id, as_of))
            return LawFileSnapshot(law_id, as_of,
                f'https://laws.e-gov.go.jp/api/2/law_file/xml/{law_id}?asof={as_of}', 'revision-fixture', XML)
        self.fetch = fetch
        self.resolver = PrimaryLawEvidenceResolver(self.root, fetcher=fetch)
        self.coordinator.primary_law_evidence = self.resolver

    def proof(self, plan):
        return plan['preparedCandidate']['content']['executionMetadata']['lawContextPrimaryEvidence'][self.request['questionId']]

    def candidate(self, plan):
        child = self.coordinator.store.get('new-exam', plan['childRunId'])
        spec = stage._stage_inputs(self.coordinator, self.request, plan['stagePlan'],
            self.coordinator.store.get('new-exam', plan['parentRunId']), '')
        targets = {self.request['questionId']: spec['candidateTargets']}
        candidate = parse_prepared_candidate_payload(plan['preparedCandidate']['content']['candidatePayload'],
            [self.request['questionId']], targets)[0]
        return candidate, spec['candidateRecord']

    def test_empty_input_actual_xml_then_second_prompt_and_candidate_hash_are_fixed(self):
        self.prepare(); plan = self.plan(); proof = self.proof(plan)
        self.assertEqual(len(self.model.calls), 2)
        initial, final = [PerQuestionQueueAppServer._candidate_questions(call[0])[0] for call in self.model.calls]
        self.assertEqual(initial['primaryLawEvidence']['items'], [])
        snapshot = final['primaryLawEvidence']['items'][0]['currentSnapshot']
        self.assertIn('制度の定義', snapshot['text'])
        self.assertTrue(snapshot['xmlHash']); self.assertTrue(snapshot['textHash'])
        self.assertEqual(initial['currentRecord'], final['currentRecord'])
        self.assertTrue(self.fetches)
        candidate, record = self.candidate(plan)
        self.assertEqual(proof['candidateHash'], stage.sha256_json(_question_candidates_payload([candidate])))
        self.assertEqual(record['correctChoiceText'], ['正しい'])
        self.assertEqual(candidate_fields(candidate)['lawReferences'][0][0]['verificationStatus'], 'verified')
        with patch.object(stage, 'verify_independent_approval', return_value={'reviewerSessionId': 'independent'}):
            receipt = self.apply(); self.assertTrue(receipt['formalPatchSaved'])
            self.assertEqual(receipt, self.apply())
        self.assertEqual(len(self.model.calls), 2)

    def test_explicit_primary_spans_reach_discovery_and_every_regeneration_without_changing_normal_fingerprints(self):
        xml = self.private / 'actual-primary.xml'; self.write(xml, XML.encode())
        metadata = self.private / 'actual-primary-receipt.json'
        self.write(metadata, {'lawId': LAW_ID, 'sourceUrl': 'https://laws.e-gov.go.jp/api/2/law_file/xml/' + LAW_ID,
            'unused': 'not selected'})
        xml_ref = {'ref': str(xml), 'sha256': stage._hash(xml.read_bytes())}
        meta_ref = {'ref': str(metadata), 'sha256': stage._hash(metadata.read_bytes())}
        self.request['primaryEvidence'].extend([xml_ref, meta_ref])
        self.request['primaryEvidenceSelections'] = [
            {'evidence': xml_ref, 'format': 'xml_articles', 'articles': ['11']},
            {'evidence': meta_ref, 'format': 'json_pointers', 'pointers': ['/lawId', '/sourceUrl']}]
        self.write(self.request_path, self.request)
        original_turn = self.model.run_turn
        def read_evidence(prompt, **kwargs):
            question = PerQuestionQueueAppServer._candidate_questions(prompt)[0]
            bundle = question['preparedPrimaryEvidence']
            self.assertIn('制度の定義', bundle['entries'][0]['text'])
            self.assertNotIn('登録の手続', bundle['entries'][0]['text'])
            self.assertEqual(json.loads(bundle['entries'][1]['text'])['/lawId'], LAW_ID)
            return original_turn(prompt, **kwargs)
        self.model.run_turn = read_evidence
        self.prepare(); plan = self.plan()
        questions = [PerQuestionQueueAppServer._candidate_questions(call[0])[0] for call in self.model.calls]
        self.assertEqual(len(questions), 2)
        self.assertEqual(questions[0]['preparedPrimaryEvidence'], questions[1]['preparedPrimaryEvidence'])
        bundle = questions[0]['preparedPrimaryEvidence']
        self.assertEqual(bundle['scope']['inputFingerprint'], plan['inputFingerprint'])
        self.assertEqual(bundle['scope']['projectedInputHash'], plan['projectedInputHash'])
        proof = self.proof(plan)
        self.assertEqual(proof['rounds'][0]['execution']['primaryEvidenceBundleHash'], bundle['bundleHash'])
        self.assertEqual(proof['rounds'][0]['execution']['promptHash'], stage._hash(self.model.calls[1][0].encode()))
        with patch.object(stage, 'verify_independent_approval', return_value={'reviewerSessionId': 'independent'}):
            self.assertTrue(self.apply()['formalPatchSaved'])
        self.assertEqual(len(self.model.calls), 2)

    def test_changed_locator_is_resolved_before_accepting_the_final_candidate(self):
        self.model.articles = ['11', '12', '12']
        self.prepare(); proof = self.proof(self.plan())
        self.assertEqual(len(self.model.calls), 3)
        self.assertEqual([item['lawReferences'][0][0]['article'] for item in proof['rounds']], ['11', '12'])
        question = PerQuestionQueueAppServer._candidate_questions(self.model.calls[-1][0])[0]
        self.assertEqual(question['primaryLawEvidence']['items'][0]['declaredArticle'], '12')
        self.assertIn('登録の手続', question['primaryLawEvidence']['items'][0]['currentSnapshot']['text'])

    def test_fetch_denied_mismatched_title_and_unverified_final_never_prepare_a_formal_candidate(self):
        for mutation in ('403', 'wrong-title', 'wrong-article', 'unverified', 'new-locators'):
            with self.subTest(mutation=mutation):
                self.output = self.private / mutation
                self.model.calls.clear(); self.model.writer_count = 0
                self.model.title = '別の法律' if mutation == 'wrong-title' else '試験法'
                self.model.force_unverified = mutation == 'unverified'
                self.model.articles = (['11', '12', '11', '12'] if mutation == 'new-locators'
                    else ['999'] if mutation == 'wrong-article' else ['11'])
                resolver = PrimaryLawEvidenceResolver(self.root, fetcher=self.fetch)
                if mutation == '403':
                    resolver = PrimaryLawEvidenceResolver(self.root / '403-cache', fetcher=Mock(side_effect=OSError('HTTP 403')))
                self.coordinator.primary_law_evidence = resolver
                with self.assertRaisesRegex(QualificationRunError, '3周'): self.prepare()
                self.assertEqual(len(self.model.calls), 4)
                self.assertFalse((self.output / 'plan.json').exists())

    def test_interrupted_native_regeneration_does_not_continue_or_prepare_a_candidate(self):
        def interrupted(work_type):
            if self.model.writer_count == 2: raise InterruptedError('native regeneration interrupted')
        self.model.before_receipt = interrupted
        with self.assertRaisesRegex(InterruptedError, 'regeneration interrupted'): self.prepare()
        self.assertEqual(len(self.model.calls), 2)
        self.assertFalse((self.output / 'plan.json').exists())

    def test_missing_exam_snapshot_does_not_discard_verified_current_snapshot(self):
        def fetch(law_id, as_of):
            if as_of == '2020-01-01': raise OSError('historical unavailable')
            return self.fetch(law_id, as_of)
        self.coordinator.primary_law_evidence = PrimaryLawEvidenceResolver(self.root, fetcher=fetch)
        with patch.object(self.coordinator.primary_law_evidence, '_default_exam_date', return_value=('2020-01-01', 'fixture')):
            self.prepare(); proof = self.proof(self.plan())
        evidence = proof['rounds'][0]['primaryLawEvidence']
        self.assertEqual(evidence['status'], 'partial')
        self.assertTrue(proof['currentEvidence'])

    def test_all_five_declared_choice_locators_require_their_own_current_snapshot(self):
        references = [[{'role': 'current_basis', 'scope': 'choice', 'choiceIndex': index,
            'lawId': LAW_ID, 'lawTitle': '試験法', 'article': '11', 'referenceDate': '2026-10-22',
            'source': 'egov_xml'}] for index in range(5)]
        record = {'choiceTextList': ['記述'] * 5, 'lawReferences': references}
        evidence = self.resolver.resolve(record, current_as_of='2026-10-22')
        self.assertEqual(len(_law_context_current_evidence(record, evidence)), 5)
        evidence['items'].pop()
        with self.assertRaisesRegex(QualificationRunError, '全locator'):
            _law_context_current_evidence(record, evidence)

    def test_classification_only_selection_does_not_claim_or_require_a_grounding_write(self):
        question_id = self.request['questionId']
        record = self.inventory.group('new-exam', '2026')['questions'][0]['projected']
        target = {'id': question_id, 'listGroupId': '2026', **self.request['source']['binding']}
        candidate_target = CandidateTarget('classification', 'law_context', 'fixture',
            ('isLawRelated', 'lawGroundedExplanationNotNeeded'))
        candidate = QuestionCandidate(question_id, 'candidate', '法令関連性だけを判断する。', (
            CandidateUpdate('classification', {'isLawRelated': True, 'lawGroundedExplanationNotNeeded': False}, ()),))
        prompt = _structured_candidate_prompt('分類する。', [target], stage_id='law_context',
            records_by_question={question_id: record}, candidate_targets_by_question={question_id: (candidate_target,)},
            feedback_by_question={})
        regenerate = Mock(side_effect=AssertionError('classification-only does not generate locators'))
        actual, proof = self.coordinator._ground_law_context_candidate('new-exam', candidate, prompt,
            batch_plan={}, records_by_question={question_id: record},
            targets_by_question={question_id: (candidate_target,)}, raw_target_by_question={question_id: target}, regenerate=regenerate)
        self.assertEqual(actual, candidate); self.assertEqual(proof['currentEvidence'], [])
        self.coordinator._verify_law_context_primary_candidate('new-exam', candidate, record, proof, list_group_id='2026')
        regenerate.assert_not_called(); self.assertEqual(self.fetches, [])

    def test_final_source_cannot_self_claim_an_unfetched_provider_or_url(self):
        for source in ('厚生労働省', 'https://www.mhlw.go.jp/unfetched-document.html',
                       f'https://laws.e-gov.go.jp/law/{LAW_ID}'):
            with self.subTest(source=source):
                self.output = self.private / ('source-' + str(len(self.model.calls)))
                self.model.source = source
                self.model.calls.clear(); self.model.writer_count = 0
                with self.assertRaisesRegex(QualificationRunError, '最終source'):
                    self.prepare()
                self.assertEqual(len(self.model.calls), 2)
                self.assertFalse((self.output / 'plan.json').exists())

    def test_exact_observed_xml_url_can_be_the_final_source(self):
        self.model.use_observed_source_url = True
        self.prepare(); proof = self.proof(self.plan())
        current = proof['currentEvidence'][0]
        self.assertEqual(current['declaredSource'], current['snapshot']['sourceUrl'])
        self.assertEqual(current['sourceProvider'], 'egov_xml')

    def test_legal_prose_only_selection_resolves_existing_references_and_regenerates(self):
        question_id = self.request['questionId']
        record = deepcopy(self.inventory.group('new-exam', '2026')['questions'][0]['projected'])
        record.update(isLawRelated=True, lawReferences=[[{'role': 'current_basis', 'scope': 'choice',
            'choiceIndex': 0, 'lawId': LAW_ID, 'lawTitle': '試験法', 'article': '11',
            'referenceDate': '2026-10-22', 'verificationStatus': 'verified', 'source': 'egov_xml'}]])
        original = deepcopy(record)
        target = {'id': question_id, 'listGroupId': '2026', **self.request['source']['binding']}
        candidate_target = CandidateTarget('prose', 'law_context', 'fixture', ('lawContextForExplanation',))
        candidate = QuestionCandidate(question_id, 'candidate', 'メモだけ更新する。', (
            CandidateUpdate('prose', {'lawContextForExplanation': '別の法律第99条で判断する。'}, ()),))
        prompt = _structured_candidate_prompt('根拠メモを確認する。', [target], stage_id='law_context',
            records_by_question={question_id: record}, candidate_targets_by_question={question_id: (candidate_target,)},
            feedback_by_question={}, primary_law_evidence_by_question={question_id: {'currentAsOf': '2026-10-22'}})
        observed = []
        def regenerate(value):
            question = PerQuestionQueueAppServer._candidate_questions(value)[0]
            observed.append(question['primaryLawEvidence'])
            result = SimpleNamespace(thread_id='fixture-thread', session_id='fixture-session', turn_id='fixture-turn',
                model='fixture-model', final_message='fixture-native-response')
            final = QuestionCandidate(question_id, 'candidate', '実本文に照らしてメモを再生成した。', (
                CandidateUpdate('prose', {'lawContextForExplanation': '試験法第11条の制度の定義を確認した。'}, ()),))
            return result, (final,)
        with patch.object(self.coordinator.workflow, 'prompt_from_plan', return_value={'prompt': '根拠メモを確認する。'}):
            actual, proof = self.coordinator._ground_law_context_candidate('new-exam', candidate, prompt,
                batch_plan={}, records_by_question={question_id: record}, targets_by_question={question_id: (candidate_target,)},
                raw_target_by_question={question_id: target}, regenerate=regenerate)
        self.assertTrue(self.fetches); self.assertEqual(len(observed), 1)
        self.assertIn('制度の定義', observed[0]['items'][0]['currentSnapshot']['text'])
        self.assertNotEqual(actual, candidate); self.assertEqual(record, original)
        self.coordinator._verify_law_context_primary_candidate('new-exam', actual, record, proof, list_group_id='2026')
        self.assertTrue(proof['currentEvidence'])
        bad = deepcopy(record); bad['lawReferences'][0][0]['lawTitle'] = '別の法律'
        bad_prompt = _structured_candidate_prompt('根拠メモを確認する。', [target], stage_id='law_context',
            records_by_question={question_id: bad}, candidate_targets_by_question={question_id: (candidate_target,)},
            feedback_by_question={}, primary_law_evidence_by_question={question_id: {'currentAsOf': '2026-10-22'}})
        observed.clear()
        with patch.object(self.coordinator.workflow, 'prompt_from_plan', return_value={'prompt': '根拠メモを確認する。'}):
            with self.assertRaisesRegex(QualificationRunError, '3周'):
                self.coordinator._ground_law_context_candidate('new-exam', candidate, bad_prompt,
                    batch_plan={}, records_by_question={question_id: bad}, targets_by_question={question_id: (candidate_target,)},
                    raw_target_by_question={question_id: target}, regenerate=regenerate)
        self.assertEqual(len(observed), 3)
        self.assertEqual(bad['lawReferences'][0][0]['lawTitle'], '別の法律')

    def test_evidence_tampering_and_input_conflict_are_rejected(self):
        self.prepare(); plan = self.plan(); candidate, record = self.candidate(plan)
        proof = deepcopy(self.proof(plan))
        proof['rounds'][-1]['primaryLawEvidence']['items'][0]['currentSnapshot']['xmlHash'] = '0' * 64
        proof['rounds'][-1]['evidenceHash'] = stage.sha256_json(proof['rounds'][-1]['primaryLawEvidence'])
        proof['currentEvidence'] = _law_context_current_evidence(
            {**record, 'lawReferences': candidate_fields(candidate)['lawReferences']}, proof['rounds'][-1]['primaryLawEvidence'])
        proof['proofHash'] = stage.sha256_json({key: value for key, value in proof.items() if key != 'proofHash'})
        with self.assertRaisesRegex(QualificationRunError, '保存前現行本文'):
            self.coordinator._verify_law_context_primary_candidate('new-exam', candidate, record, proof, list_group_id='2026')
        self.request_path.write_bytes(self.request_path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'drift'): self.plan()

    def test_lost_save_receipt_is_not_replayed_with_a_second_model_or_write(self):
        self.prepare()
        original = stage._exclusive
        def interrupt(path, value):
            if path.name == 'save-receipt.json': raise OSError('receipt interrupted')
            return original(path, value)
        with patch.object(stage, 'verify_independent_approval', return_value={'reviewerSessionId': 'independent'}), \
                patch.object(stage, '_exclusive', side_effect=interrupt):
            with self.assertRaisesRegex(OSError, 'receipt interrupted'): self.apply()
            with self.assertRaisesRegex(ValueError, 'third value|incomplete'): self.apply()
        self.assertEqual(len(self.model.calls), 2)
