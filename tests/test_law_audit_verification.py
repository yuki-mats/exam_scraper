import copy
import json
import unittest

from tools.question_review_console.question_candidate import CandidateUpdate, QuestionCandidate
from tools.question_review_console.law_audit_verification import (
    candidate_fields, verification_bundle, review_schema, parse_review, promote_candidate,
    question_inputs_from_prompt, required_review_count,
)


class LawAuditVerificationTests(unittest.TestCase):
    def setUp(self):
        self.fields = {
            'isLawRelated': True, 'auditStatus': 'updated_to_current_law',
            'reviewState': 'needs_secondary_review',
            'reconciliationStatus': 'pending_secondary_review',
            'verificationSummary': '一次提案のため二次確認待ち。',
            'examTimeDecision': ['正しい', '間違い'],
            'currentLawDecision': ['間違い', '間違い'],
            'correctChoiceText': ['間違い', '間違い'],
            'lawRevisionFacts': [
                {'auditStatus': 'updated_to_current_law', 'reviewState': 'needs_secondary_review', 'reconciliationStatus': 'pending_secondary_review', 'examTime': {'correctChoiceText': '正しい'}, 'current': {'correctChoiceText': '間違い'}},
                {'auditStatus': 'same_as_current', 'reviewState': 'needs_secondary_review', 'reconciliationStatus': 'pending_secondary_review', 'examTime': {'correctChoiceText': '間違い'}, 'current': {'correctChoiceText': '間違い'}},
            ],
        }
        self.candidate = QuestionCandidate('q1', 'candidate', '一次提案', (
            CandidateUpdate('audit', self.fields, ()),
        ))
        self.bundle = verification_bundle({'questionId': 'q1', 'currentRecord': {'choiceTextList': ['法A', '法B']}}, self.candidate)
        self.approval = {
            'schemaVersion': 'law-audit-verification/v1', 'questionId': 'q1',
            'evidenceHash': self.bundle['evidenceHash'], 'decision': 'approve',
            'examTimeDecision': self.fields['examTimeDecision'],
            'currentLawDecision': self.fields['currentLawDecision'], 'issues': [],
            'verifiedSourceUrls': ['https://laws.e-gov.go.jp/law/example'],
        }

    def test_exact_review_approval_and_schema(self):
        self.assertEqual(parse_review(json.dumps(self.approval), self.bundle), self.approval)
        self.assertEqual(review_schema(self.bundle)['properties']['currentLawDecision']['minItems'], 2)

    def test_independent_review_preserves_and_hashes_source_reference_guidance(self):
        question = copy.deepcopy(self.bundle['question'])
        guidance = '公式原文: https://example.go.jp/exam.pdf / 現在化後と独立照合'
        bundle = verification_bundle(question, self.candidate, reference_guidance=guidance)
        self.assertEqual(bundle['qualificationReferenceGuidance'], guidance)
        self.assertNotEqual(bundle['evidenceHash'], self.bundle['evidenceHash'])
        question['currentRecord']['choiceTextList'][0] = '別の本文'
        self.assertEqual(bundle['question'], self.bundle['question'])
        with self.assertRaises(ValueError):
            parse_review(json.dumps(self.approval), bundle)

    def test_question_input_can_precede_document_guidance(self):
        prompt = '規則\n' + json.dumps([self.bundle['question']]) + '\n# 正本文書\n文書の末尾'
        self.assertEqual(question_inputs_from_prompt(prompt)['q1'], self.bundle['question'])

    def test_new_locator_primary_evidence_is_bound_without_replacing_original_input(self):
        evidence = {'status': 'complete', 'items': [{'declaredArticle': '597',
            'examAsOf': '2019-04-01', 'currentSnapshot': {'articleText': '期間満了による終了'}}]}
        bundle = verification_bundle(self.bundle['question'], self.candidate,
                                     proposed_primary_evidence=evidence)
        self.assertEqual(bundle['question'], self.bundle['question'])
        self.assertEqual(bundle['proposedPrimaryLawEvidence'], evidence)
        self.assertNotEqual(bundle['evidenceHash'], self.bundle['evidenceHash'])
        evidence['items'][0]['currentSnapshot']['articleText'] = '別の条文'
        self.assertEqual(bundle['proposedPrimaryLawEvidence']['items'][0]['currentSnapshot']['articleText'],
                         '期間満了による終了')
        with self.assertRaises(ValueError):
            parse_review(json.dumps(self.approval), bundle)

    def test_wrong_identity_or_hash_rejected(self):
        for key in ['questionId', 'evidenceHash']:
            with self.subTest(key=key):
                value = dict(self.approval, **{key: 'different'})
                with self.assertRaises(ValueError): parse_review(json.dumps(value), self.bundle)

    def test_verdict_disagreement_or_unverified_source_rejected(self):
        for patch in [{'currentLawDecision': ['正しい', '間違い']}, {'verifiedSourceUrls': []}, {'issues': ['根拠が不足']}]:
            with self.subTest(patch=patch):
                with self.assertRaises(ValueError): parse_review(json.dumps(dict(self.approval, **patch)), self.bundle)

    def test_two_approvals_promote_changed_law_without_mutating_proposal(self):
        original = copy.deepcopy(self.fields)
        promoted = promote_candidate(self.candidate, [self.approval, self.approval])
        fields = candidate_fields(promoted)
        self.assertEqual(fields['reviewState'], 'tertiary_verified')
        self.assertEqual([x['reviewState'] for x in fields['lawRevisionFacts']], ['tertiary_verified', 'secondary_verified'])
        self.assertEqual(fields['reconciliationStatus'], 'reconciled')
        self.assertEqual([x['reconciliationStatus'] for x in fields['lawRevisionFacts']], ['reconciled', 'reconciled'])
        self.assertIn('独立三次監査', fields['verificationSummary'])
        self.assertEqual(self.fields, original)

    def test_missing_tertiary_or_hold_never_produces_patch_updates(self):
        for reviews in [[self.approval], [self.approval, dict(self.approval, decision='hold', issues=['条文不一致'])]]:
            result = promote_candidate(self.candidate, reviews)
            self.assertEqual(result.status, 'blocked')
            self.assertEqual(result.updates, ())

    def test_unchanged_law_needs_secondary_only(self):
        self.fields['auditStatus'] = 'same_as_current'
        self.fields['lawRevisionFacts'][0]['auditStatus'] = 'same_as_current'
        self.fields['examTimeDecision'] = ['間違い', '間違い']
        self.fields['lawRevisionFacts'][0]['examTime']['correctChoiceText'] = '間違い'
        result = promote_candidate(self.candidate, [self.approval])
        fields = candidate_fields(result)
        self.assertEqual(fields['reviewState'], 'secondary_verified')
        self.assertEqual(fields['reconciliationStatus'], 'reconciled')
        self.assertIn('独立二次監査', fields['verificationSummary'])

    def test_conflicting_targets_rejected(self):
        candidate = QuestionCandidate('q1', 'candidate', '', (CandidateUpdate('a', {'isLawRelated': True}, ()), CandidateUpdate('b', {'isLawRelated': False}, ())))
        with self.assertRaises(ValueError): candidate_fields(candidate)

    def test_candidate_inconsistencies_reject_before_independent_reviews(self):
        for mutate in [
            lambda f: f.update(auditStatus='same_as_current'),
            lambda f: f['lawRevisionFacts'][0].update(auditStatus='hold'),
            lambda f: f.update(correctChoiceText=['正しい', '間違い']),
            lambda f: f['lawRevisionFacts'][0]['current'].update(correctChoiceText='正しい'),
            lambda f: f['lawRevisionFacts'][1]['examTime'].update(correctChoiceText='正しい'),
        ]:
            fields = copy.deepcopy(self.fields)
            mutate(fields)
            before = copy.deepcopy(fields)
            with self.assertRaises(ValueError):
                required_review_count(fields)
            self.assertEqual(fields, before)

    def test_changed_law_with_no_current_correct_choice_requires_tertiary(self):
        self.assertEqual(required_review_count(self.fields), 2)


class StatementJudgmentGuidanceTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from tools.question_review_console.law_audit_verification import JUDGMENT_GUIDANCE_SOURCE
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.original = (Path(__file__).resolve().parents[1] / JUDGMENT_GUIDANCE_SOURCE).read_bytes()
        self.path = self.root / JUDGMENT_GUIDANCE_SOURCE
        self.path.parent.mkdir(parents=True); self.path.write_bytes(self.original)

    def test_whole_canonical_criteria_is_read_without_output_commands_or_fixed_answers(self):
        import hashlib
        from tools.question_review_console.law_audit_verification import statement_judgment_guidance
        guidance = statement_judgment_guidance(self.root)
        source = self.original.decode()
        expected = source[source.index('## 判定基準\n'):source.index('## 出力\n')]
        self.assertEqual(guidance['sectionText'], expected)
        self.assertEqual(guidance['sourceSha256'], hashlib.sha256(self.original).hexdigest())
        self.assertEqual(guidance['sectionSha256'], hashlib.sha256(expected.encode()).hexdigest())
        for required in ('完結した記述', '肯定形の共通述語', '条項、列挙範囲、時点又は分類',
                         '述語を否定語も含めて一度だけ補い', '番号は本文中の語句又は記述へのポインタ',
                         '現在の', '自動割当しません'):
            self.assertIn(required, expected)
        self.assertNotIn('## 出力', expected)
        self.assertNotIn('materialize-patch', expected)
        self.assertNotIn('FTTTT', json.dumps(guidance))
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_missing_duplicate_empty_or_changed_section_and_contract_are_not_accepted(self):
        from unittest.mock import patch
        import tools.question_review_console.law_audit_verification as law
        before = law.statement_judgment_guidance(self.root)
        for data in (self.original.replace('## 判定基準'.encode(), b'## Missing'),
                     self.original + '\n## 判定基準\n別基準\n'.encode(),
                     b'## '+ '判定基準\n\n## 出力\n'.encode()):
            self.path.write_bytes(data)
            with self.subTest(dataHash=law._json_hash(data.hex())), self.assertRaises(ValueError):
                law.statement_judgment_guidance(self.root)
        self.path.write_bytes(self.original.replace('## 判定基準\n'.encode(), '## 判定基準\n改訂された基準。\n'.encode()))
        with self.assertRaises(ValueError):
            law.validate_statement_judgment_guidance(before, current=law.statement_judgment_guidance(self.root))
        self.path.write_bytes(self.original)
        with patch.object(law, 'JUDGMENT_GUIDANCE_SCHEMA', 'statement-judgment-guidance/v2'):
            with self.assertRaises(ValueError): law.validate_statement_judgment_guidance(before)

    def test_model_cannot_write_server_guidance_as_a_business_field(self):
        from tools.question_review_console.question_candidate import (
            CandidateTarget, parse_model_candidate_v3, QuestionCandidateError)
        payload = {'decision': 'candidate', 'summary': 'forged guidance',
            'update': {'setFields': [{'field': 'correctChoiceText', 'value': ['正しい']},
                {'field': 'statementJudgmentGuidance', 'value': {'guidanceHash': 'forged'}}], 'unsetFields': []}}
        target = CandidateTarget('q:correct_choice', 'correct_choice', 'patch.json', ('correctChoiceText',))
        with self.assertRaises(QuestionCandidateError):
            parse_model_candidate_v3(json.dumps(payload), ['q'], {'q': [target]})

    def test_same_guidance_and_candidate_are_bound_to_both_independent_phases(self):
        from tools.question_review_console.law_audit_verification import (
            statement_judgment_guidance, review_prompt, validate_verification_bundle)
        template = LawAuditVerificationTests(); template.setUp()
        question = copy.deepcopy(template.bundle['question'])
        guidance = statement_judgment_guidance(self.root)
        question['statementJudgmentGuidance'] = guidance
        bundle = verification_bundle(question, template.candidate)
        before = copy.deepcopy(template.candidate)
        for phase in ('二次', '三次'):
            prompt = review_prompt(bundle, phase)
            observed = json.loads(prompt.splitlines()[-1])
            self.assertEqual(observed['question']['statementJudgmentGuidance'], guidance)
            self.assertEqual(observed['statementJudgmentGuidanceHash'], guidance['guidanceHash'])
            self.assertIn('全肢を一律に肯定化又は否定化しない', prompt)
            self.assertIn('404や取得失敗', prompt)
        approval = dict(template.approval, evidenceHash=bundle['evidenceHash'])
        self.assertEqual(parse_review(json.dumps(approval), bundle), approval)
        disagreement = dict(approval, currentLawDecision=['正しい', '正しい'])
        with self.assertRaises(ValueError): parse_review(json.dumps(disagreement), bundle)
        changed = copy.deepcopy(bundle)
        changed['question']['statementJudgmentGuidance']['sectionText'] += '都合のよい基準'
        with self.assertRaises(ValueError): validate_verification_bundle(changed)
        self.path.write_bytes(self.original.replace('## 判定基準\n'.encode(), '## 判定基準\n新基準\n'.encode()))
        new_question = dict(question, statementJudgmentGuidance=statement_judgment_guidance(self.root))
        with self.assertRaises(ValueError):
            parse_review(json.dumps(approval), verification_bundle(new_question, template.candidate))
        self.assertEqual(template.candidate, before)


class NormalLawJudgmentDeliveryTests(unittest.TestCase):
    def setUp(self):
        from tests.test_feedback_prepared_stage import PreparedStageTests
        PreparedStageTests.setUp(self)
        self.request['live']['questionIds'] = ['live-a']
        self.snapshots = {'live-a': self.snapshots['live-a']}
        source_record = json.loads(__import__('pathlib').Path(self.request['source']['ref']).read_text())['question_bodies'][0]
        self.snapshots['live-a']['fields']['originalQuestionChoiceText'] = source_record['choiceTextList'][0]
        self.request['stageId'] = 'law_audit'; self.write(self.request_path, self.request)
        original_group = self.inventory.group
        def legal_group(*args):
            group = original_group(*args)
            for question in group['questions']:
                question['isLawRelated'] = True
                question['projected']['isLawRelated'] = True
            return group
        self.inventory.group = legal_group
        from pathlib import Path
        from tools.question_bank import feedback_prepared_stage as stage
        source = Path(self.request['source']['ref'])
        payload = json.loads(source.read_text()); payload['question_bodies'][0]['isLawRelated'] = True
        self.write(source, payload); self.request['source']['sha256'] = stage._hash(source.read_bytes())
        self.write(self.request_path, self.request)
        self.write(self.root / stage.MANIFEST, (json.dumps({'path': str(source.relative_to(self.root)),
            'sha256': stage._hash(source.read_bytes())}) + '\n').encode())

    @staticmethod
    def write(path, value):
        from tests.test_feedback_prepared_stage import PreparedStageTests
        PreparedStageTests.write(path, value)

    def test_normal_primary_and_required_secondary_tertiary_receive_one_guidance_hash(self):
        from dataclasses import replace
        from unittest.mock import patch, Mock
        from tools.question_bank import feedback_prepared_stage as stage
        from tools.question_review_console.codex_app_server import AppServerTurnResult
        from tools.question_review_console.law_audit_verification import statement_judgment_guidance
        observed = []; original = self.model.run_turn
        def run(prompt, **kwargs):
            observed.append((prompt, kwargs['work_type']))
            if kwargs['work_type'] in {'maintenance_law_audit_secondary_audit_candidate', 'maintenance_law_audit_tertiary_audit_candidate'}:
                bundle = json.loads(prompt.splitlines()[-1]); fields = bundle['proposedFields']
                number = len(observed)
                return AppServerTurnResult(thread_id='review-' + str(number), session_id='session-' + str(number),
                    turn_id='turn-' + str(number), model='fixture', service_tier=None,
                    final_message=json.dumps({'schemaVersion': 'law-audit-verification/v1',
                        'questionId': bundle['question']['questionId'], 'evidenceHash': bundle['evidenceHash'],
                        'decision': 'approve', 'issues': [], 'verifiedSourceUrls': ['https://laws.e-gov.go.jp/fixture'],
                        'feedbackTaskRequirementInputHash': bundle['feedbackTaskRequirementInputHash'], 'taskRequirementSatisfied': True,
                        'examTimeDecision': fields['examTimeDecision'], 'currentLawDecision': fields['currentLawDecision']}))
            result = original(prompt, **kwargs); payload = json.loads(result.final_message)
            fields = {item['field']: item['value'] for item in payload['update']['setFields']}
            fields.update(isLawRelated=True, auditStatus='updated_to_current_law', reviewState='needs_secondary_review',
                reconciliationStatus='pending_secondary_review', examTimeDecision=['間違い'],
                currentLawDecision=['正しい'], correctChoiceText=['正しい'])
            facts = fields['lawRevisionFacts']; facts[0].update(auditStatus='updated_to_current_law',
                reviewState='needs_secondary_review', reconciliationStatus='pending_secondary_review')
            facts[0]['examTime']['correctChoiceText'] = '間違い'; facts[0]['current']['correctChoiceText'] = '正しい'
            payload['update']['setFields'] = [{'field': key, 'value': value} for key, value in fields.items()]
            return replace(result, final_message=json.dumps(payload, ensure_ascii=False))
        self.model.run_turn = run
        self.coordinator.primary_law_evidence.resolve = Mock(return_value={'status': 'complete', 'items': []})
        class StopBeforePrivatePreview(Exception): pass
        with patch.object(stage, '_run', side_effect=StopBeforePrivatePreview) as preview:
            with self.assertRaises(StopBeforePrivatePreview):
                stage.prepare(self.root, self.request_path, self.output,
                    coordinator=self.coordinator, connection=self.connection)
        self.assertEqual([kind for _, kind in observed], ['maintenance_law_audit_candidate',
            'maintenance_law_audit_secondary_audit_candidate', 'maintenance_law_audit_tertiary_audit_candidate'])
        guidance = statement_judgment_guidance(self.root)
        primary = question_inputs_from_prompt(observed[0][0])[self.request['questionId']]
        self.assertEqual(primary['statementJudgmentGuidance'], guidance)
        self.assertEqual(primary['feedbackTaskRequirement']['definition']['need'], '設問の性質')
        self.assertNotIn('sources', primary['feedbackTaskRequirement'])
        self.assertNotIn('正式patchは同じ', observed[0][0])
        for prompt, _kind in observed[1:]:
            bundle = json.loads(prompt.splitlines()[-1])
            self.assertEqual(bundle['question']['statementJudgmentGuidance'], guidance)
            self.assertEqual(bundle['question']['feedbackTaskRequirement'], primary['feedbackTaskRequirement'])
            self.assertEqual(bundle['statementJudgmentGuidanceHash'], guidance['guidanceHash'])
            self.assertEqual(bundle['proposedFields']['currentLawDecision'], ['正しい'])
        fixed = preview.call_args.args[1]
        metadata = fixed['preparedCandidate']['content']['executionMetadata']
        delivery = metadata['lawAuditJudgmentGuidance']
        self.assertEqual(delivery['guidance'], guidance)
        self.assertEqual(delivery['primaryPromptHash'], stage._hash(observed[0][0].encode()))
        self.assertEqual(delivery['inputFingerprint'], fixed['inputFingerprint'])
        verification = metadata['lawAuditVerification'][self.request['questionId']]
        self.assertEqual(len(verification['reviews']), 2)
        self.assertEqual(len({review['threadId'] for review in verification['reviews']}), 2)
        candidate_payload = json.dumps(fixed['preparedCandidate']['content']['candidatePayload'], ensure_ascii=False)
        self.assertNotIn('statementJudgmentGuidance', candidate_payload)
        self.assertNotIn('sectionText', candidate_payload)
        self.assertFalse((self.output / 'plan.json').exists())
        self.assertGreaterEqual(self.coordinator.primary_law_evidence.resolve.call_count, 2)
        # A later source change invalidates the genuine prepared metadata at
        # the normal writer boundary, before any baseline or canonical write.
        path = self.root / 'prompt/02a_prompt_review_correctChoiceText.md'
        path.write_text(path.read_text().replace('## 判定基準\n', '## 判定基準\n改訂された命題基準。\n'))
        with patch.object(self.coordinator.store, 'write_baseline', wraps=self.coordinator.store.write_baseline) as baseline:
            with self.assertRaisesRegex(ValueError, 'guidance'):
                stage._run(self.coordinator, fixed, preview=True)
            baseline.assert_not_called()
        self.assertEqual(len(observed), 3)

    def test_new_dependency_and_policy_version_change_only_law_audit_fingerprint(self):
        from tools.question_review_console.qualification_workflow import QualificationWorkflow
        before = self.coordinator.workflow.versioned_policies('new-exam')
        self.assertEqual(before['law_audit']['policyVersion'], '6.7')
        self.assertEqual(before['correct_choice']['policyVersion'], '5.11')
        path = self.root / 'config/question_maintenance_workflow.toml'
        source = path.read_text(); start = source.index('id = "law_audit"', source.index('policy_version = "11.4"'))
        end = source.index('[[stages.update_targets]]', start)
        old = source[start:end].replace('policy_version = "6.7"', 'policy_version = "6.6"').replace(
            '  "prompt/02a_prompt_review_correctChoiceText.md",\n', '')
        path.write_text(source[:start] + old + source[end:])
        after = QualificationWorkflow(self.root, self.inventory).versioned_policies('new-exam')
        self.assertNotEqual(before['law_audit']['policyFingerprint'], after['law_audit']['policyFingerprint'])
        for stage_id in set(before) - {'law_audit'}:
            with self.subTest(stageId=stage_id):
                self.assertEqual(before[stage_id]['policyFingerprint'], after[stage_id]['policyFingerprint'])
