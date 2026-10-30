"""Separate span adoption from format judgment and reject stale prepared inputs."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
import unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch

from scripts.common.aggregate_answer_decomposition import reconcile_reviews
from tests.test_aggregate_answer_decomposition import SOURCE_TEXT, FIRST, SECOND, approved_review
from tools.question_review_console.qualification_runs import (
    QualificationRunError, _aggregate_type_candidate_prompt,
    _validate_aggregate_type_candidates, _validate_aggregate_type_delivery,
)
from tools.question_review_console.question_candidate import CandidateUpdate, QuestionCandidate
from tools.question_review_console.projection import sha256_json
from tests import test_feedback_prepared_stage as fixture
from tests.qualification_run_test_support import PerQuestionQueueAppServer
from tools.question_bank import feedback_prepared_stage as stage


class AggregateTypeProjectionDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.source = {'original_question_id': 'q', 'questionBodyText': SOURCE_TEXT,
            'choiceTextList': ['組合せ1', '組合せ2']}
        self.reviews = [approved_review(), approved_review()]
        self.consensus = reconcile_reviews(SOURCE_TEXT, self.reviews)
        self.questions = [{'questionId': 'q', 'currentRecord': deepcopy(self.source),
            'feedbackTaskRequirement': {'inputHash': 'task'}, 'preparedPrimaryEvidence': {'bundleHash': 'primary'}}]
        self.original = 'stage instruction\n' + json.dumps(self.questions, ensure_ascii=False)
        self.prompt, self.projections = _aggregate_type_candidate_prompt(self.original,
            {'q': self.source}, {'q': self.reviews}, {'q': self.consensus})
        self.proof = {'schemaVersion': 'aggregate-type-projection-delivery/v1',
            'promptHash': hashlib.sha256(self.prompt.encode()).hexdigest(),
            'projectionsHash': sha256_json(self.projections), 'inputFingerprint': 'source-hash',
            'projectedInputHash': 'prior-projection-hash', 'threadId': 'classification', 'turnId': 'turn'}

    def validate(self, actual=None, proof=None):
        _validate_aggregate_type_delivery(self.prompt if actual is None else actual,
            self.prompt, self.projections, self.proof if proof is None else proof,
            'source-hash', 'prior-projection-hash', ['review-A', 'review-B'])

    def test_source_spans_are_delivered_and_task_and_primary_evidence_are_preserved(self):
        delivered = json.loads(self.prompt.splitlines()[-1])[0]
        self.assertEqual(delivered['currentRecord']['choiceTextList'], [FIRST, SECOND])
        self.assertEqual(delivered['currentRecord']['questionBodyText'], SOURCE_TEXT)
        self.assertEqual(delivered['feedbackTaskRequirement'], self.questions[0]['feedbackTaskRequirement'])
        self.assertEqual(delivered['preparedPrimaryEvidence'], self.questions[0]['preparedPrimaryEvidence'])
        self.assertEqual(self.source['choiceTextList'], ['組合せ1', '組合せ2'])
        self.validate(); self.validate()  # Same captured input is safe to replay.

    def test_missing_or_stale_delivery_never_reaches_save(self):
        for field in self.proof:
            proof = deepcopy(self.proof); proof.pop(field)
            with self.subTest(missing=field), self.assertRaises(QualificationRunError):
                self.validate(proof=proof)
        for field in ('promptHash', 'projectionsHash', 'inputFingerprint', 'projectedInputHash'):
            proof = deepcopy(self.proof); proof[field] = 'stale'
            with self.subTest(changed=field), self.assertRaises(QualificationRunError):
                self.validate(proof=proof)
        with self.assertRaises(QualificationRunError): self.validate(actual=self.original)

    def test_classification_cannot_reuse_an_aggregate_review_thread(self):
        proof = deepcopy(self.proof); proof['threadId'] = 'review-A'
        with self.assertRaises(QualificationRunError): self.validate(proof=proof)

    def test_model_classification_disagreement_is_rejected_instead_of_rewritten(self):
        targets = {'q': (SimpleNamespace(target_id='q:type', role='question_type'),)}
        for fields in ({'questionType': 'group_choice'}, {'isCalculationQuestion': False}):
            candidate = QuestionCandidate('q', 'candidate', 'independent judgment',
                (CandidateUpdate('q:type', fields, ()),))
            with self.subTest(fields=fields), self.assertRaises(QualificationRunError):
                _validate_aggregate_type_candidates([candidate], self.projections, targets)
        candidate = QuestionCandidate('q', 'candidate', 'independent judgment',
            (CandidateUpdate('q:type', {'questionType': 'true_false'}, ()),))
        _validate_aggregate_type_candidates([candidate], self.projections, targets)

    def test_projection_refuses_changed_consensus_or_missing_question(self):
        changed = deepcopy(self.consensus); changed['spans'] = []
        with self.assertRaises(QualificationRunError):
            _aggregate_type_candidate_prompt(self.original, {'q': self.source}, {'q': self.reviews}, {'q': changed})
        with self.assertRaises(QualificationRunError):
            _aggregate_type_candidate_prompt('[]', {'q': self.source}, {'q': self.reviews}, {'q': self.consensus})
        self.assertEqual(_aggregate_type_candidate_prompt(self.original, {}, {}, {}), (self.original, {}))


class PreparedAggregateTypeDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.f = f = fixture.PreparedStageTests('test_prepare_uses_normal_candidate_and_writer_preview_without_formal_save')
        f.setUp(); self.addCleanup(f.doCleanups)
        source = Path(f.request['source']['ref'])
        raw = json.loads(source.read_bytes())['question_bodies'][0]
        raw.update(questionBodyText=SOURCE_TEXT, choiceTextList=['組合せ1', '組合せ2'], questionType='flash_card')
        group = f.inventory.group('new-exam', '2026')
        for key in ('source', 'projected'): group['questions'][0][key].update(deepcopy(raw))
        f.inventory.group = lambda *_: deepcopy(group)
        f.write(source, {'question_bodies': [raw]})
        f.request['source']['sha256'] = stage._hash(source.read_bytes())
        f.write(f.request_path, f.request)
        f.write(f.root / stage.MANIFEST, (json.dumps({'path': str(source.relative_to(f.root)),
            'sha256': stage._hash(source.read_bytes())}) + '\n').encode())
        original_turn = f.model.run_turn
        self.disagree = False
        def run(prompt, **kwargs):
            result = original_turn(prompt, **kwargs)
            if '_aggregate_review_' in kwargs['work_type']:
                questions = PerQuestionQueueAppServer._candidate_questions(prompt)
                payload = json.loads(result.final_message)
                for review, question in zip(payload['questionReviews'], questions, strict=True):
                    review.update(classification='target', decision='approve',
                        candidateId=question['candidateSets'][0]['candidateId'])
                return replace(result, final_message=json.dumps(payload))
            if self.disagree:
                payload = json.loads(result.final_message)
                next(field for field in payload['update']['setFields']
                    if field['field'] == 'questionType')['value'] = 'group_choice'
                return replace(result, final_message=json.dumps(payload))
            return result
        f.model.run_turn = run

    def test_real_prepare_preview_apply_and_retry_use_same_projected_classification(self):
        f = self.f; original = Path(f.request['source']['ref']).read_bytes()
        f.prepare(); plan = f.plan()
        prompt = f.model.calls[0][0]
        actual = PerQuestionQueueAppServer._candidate_questions(prompt)[0]['currentRecord']
        self.assertEqual(actual['choiceTextList'], [FIRST, SECOND])
        proof = plan['preparedCandidate']['content']['executionMetadata']['aggregateTypeProjectionDelivery']
        self.assertEqual(proof['promptHash'], hashlib.sha256(prompt.encode()).hexdigest())
        with patch.object(stage, 'verify_independent_approval', return_value={'reviewerSessionId': 'fixture-only'}):
            receipt = f.apply(); self.assertTrue(receipt['formalPatchSaved'])
            self.assertEqual(receipt, f.apply())
        self.assertEqual(Path(f.request['source']['ref']).read_bytes(), original)
        self.assertFalse(receipt['FirestoreWritten'])

    def test_real_disagreement_never_issues_plan_or_writes_patch(self):
        self.disagree = True; f = self.f
        with self.assertRaisesRegex(Exception, '投影と実形式判定が不一致'): f.prepare()
        self.assertFalse((f.output / 'plan.json').exists())
        self.assertEqual(list((f.root / 'output/new-exam').rglob('*questionType_fixed.json')), [])


if __name__ == '__main__':
    unittest.main()
