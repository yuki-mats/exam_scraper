import copy
import json
import unittest

from tools.question_review_console.question_candidate import CandidateUpdate, QuestionCandidate
from tools.question_review_console.law_audit_verification import (
    candidate_fields, verification_bundle, review_schema, parse_review, promote_candidate,
    question_inputs_from_prompt,
)


class LawAuditVerificationTests(unittest.TestCase):
    def setUp(self):
        self.fields = {
            'isLawRelated': True, 'auditStatus': 'updated_to_current_law',
            'reviewState': 'needs_secondary_review',
            'examTimeDecision': ['正しい', '間違い'],
            'currentLawDecision': ['間違い', '間違い'],
            'correctChoiceText': ['間違い', '間違い'],
            'lawRevisionFacts': [
                {'auditStatus': 'updated_to_current_law', 'reviewState': 'needs_secondary_review'},
                {'auditStatus': 'same_as_current', 'reviewState': 'needs_secondary_review'},
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
        self.assertEqual(self.fields, original)

    def test_missing_tertiary_or_hold_never_produces_patch_updates(self):
        for reviews in [[self.approval], [self.approval, dict(self.approval, decision='hold', issues=['条文不一致'])]]:
            result = promote_candidate(self.candidate, reviews)
            self.assertEqual(result.status, 'blocked')
            self.assertEqual(result.updates, ())

    def test_unchanged_law_needs_secondary_only(self):
        self.fields['auditStatus'] = 'same_as_current'
        result = promote_candidate(self.candidate, [self.approval])
        self.assertEqual(candidate_fields(result)['reviewState'], 'secondary_verified')

    def test_conflicting_targets_rejected(self):
        candidate = QuestionCandidate('q1', 'candidate', '', (CandidateUpdate('a', {'isLawRelated': True}, ()), CandidateUpdate('b', {'isLawRelated': False}, ())))
        with self.assertRaises(ValueError): candidate_fields(candidate)
