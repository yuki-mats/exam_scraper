from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.question_bank.feedback_improvement_plan import project_verified_bundle, prepare_plan


class VerifiedExplanationPlanTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.doc = dict(questionId="display", originalQuestionId="original",
            originalQuestionBodyText="問題", originalQuestionChoiceText="対象の肢",
            qualificationId="gas-shunin-otsu", examYear=2021, questionType="true_false",
            isChoiceOnly=False, correctChoiceText="正しい", explanationText="正しい。旧説明。",
            suggestedQuestions=[], suggestedQuestionDetails=[])
        self.bundle = dict(schemaVersion="gas-shunin-verified-publication/v1",
            qualificationId="gas-shunin-otsu", total_count=2,
            questions=[deepcopy(self.doc), {**deepcopy(self.doc), "questionId":"sibling", "originalQuestionChoiceText":"別の肢"}])
        self.snapshot = self.root / "before.json"
        self.snapshot.write_text(json.dumps(dict(questionId="display", exists=True,
            updateTimeExact="2026-10-08T00:00:00.000001Z", fields=self.doc)))
        self.update = dict(questionId="display", baseline=dict(ref=str(self.snapshot),
            sha256=hashlib.sha256(self.snapshot.read_bytes()).hexdigest(), updateTimeExact="2026-10-08T00:00:00.000001Z"),
            fieldDiffs=[dict(field="explanationText", beforePresent=True,
                before="正しい。旧説明。", after="正しい。理由を具体化した説明。")])

    def project(self, bundle=None, updates=None):
        return project_verified_bundle(self.bundle if bundle is None else bundle,
            [self.update] if updates is None else updates)

    def test_preserves_order_siblings_identity_and_inputs(self):
        original = deepcopy(self.bundle)
        projected = self.project()
        self.assertEqual(self.bundle, original)
        self.assertEqual(projected['questions'][1], original['questions'][1])
        self.assertEqual([r['questionId'] for r in projected['questions']], ['display','sibling'])
        self.assertEqual(projected['questions'][0]['correctChoiceText'], '正しい')
        self.assertNotEqual(projected['questions'][0]['explanationText'], original['questions'][0]['explanationText'])

    def test_canonical_before_conflict_fails_without_touching_input(self):
        self.bundle['questions'][0]['explanationText'] = '正しい。別作業による更新。'
        before = deepcopy(self.bundle)
        with self.assertRaisesRegex(ValueError, 'before field'):
            self.project()
        self.assertEqual(before, self.bundle)

    def test_snapshot_tampering_and_wrong_version_fail(self):
        self.snapshot.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'hash differs'):
            self.project()

    def test_version_binding_is_required(self):
        self.update['baseline']['updateTimeExact'] = 'different'
        with self.assertRaisesRegex(ValueError, 'version differs'):
            self.project()

    def test_public_identity_and_verdict_changes_are_rejected(self):
        for field in ['questionId', 'originalQuestionId', 'correctChoiceText', 'updatedAt', 'lawRevisionFacts']:
            update = deepcopy(self.update)
            update['fieldDiffs'][0]['field'] = field
            with self.assertRaisesRegex(ValueError, 'unsupported'):
                self.project(updates=[update])
        self.bundle['questions'][0]['originalQuestionChoiceText'] = '別の対象肢'
        with self.assertRaisesRegex(ValueError, 'identity or verdict'):
            self.project()

    def test_duplicate_unknown_ids_and_duplicate_fields_fail(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.project(updates=[self.update, self.update])
        update = deepcopy(self.update)
        update['questionId'] = 'unknown'
        with self.assertRaisesRegex(ValueError, 'identity absent'):
            self.project(updates=[update])
        update = deepcopy(self.update)
        update['fieldDiffs'] *= 2
        with self.assertRaisesRegex(ValueError, 'duplicate delta'):
            self.project(updates=[update])
        self.bundle['questions'][1]['questionId'] = 'display'
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.project()

    def test_missing_and_null_before_are_distinct(self):
        self.update['fieldDiffs'][0]['beforePresent'] = False
        self.update['fieldDiffs'][0]['before'] = None
        with self.assertRaisesRegex(ValueError, 'presence differs'):
            self.project()

    def test_deletion_wrong_verdict_and_divergent_suggestions_fail(self):
        update = deepcopy(self.update)
        update['fieldDiffs'][0].update(afterPresent=False, after=None)
        with self.assertRaisesRegex(ValueError, 'replacement'):
            self.project(updates=[update])
        update = deepcopy(self.update)
        update['fieldDiffs'][0]['after'] = '間違い。誤った判定。'
        with self.assertRaisesRegex(ValueError, 'verdict differs'):
            self.project(updates=[update])
        update = deepcopy(self.update)
        update['fieldDiffs'].append(dict(field='suggestedQuestionDetails', beforePresent=True,
            before=[], after=[dict(question='補足', answer='追加説明')]))
        with self.assertRaisesRegex(ValueError, 'mirror differs'):
            self.project(updates=[update])

    def test_private_destination_and_no_overwrite_boundary(self):
        destination = self.root / 'output/user_feedback_response_system/execution/run'
        destination.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'fresh destination'):
            prepare_plan(self.root, self.root/'absent', self.root/'absent', destination)
        with self.assertRaisesRegex(ValueError, 'private execution'):
            prepare_plan(self.root, self.root/'absent', self.root/'absent', self.root/'output/gas-shunin-otsu/formal')

    def test_plan_creates_private_bytes_without_formal_changes_or_readiness(self):
        def save(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value))
            return dict(ref=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        source = self.root/'output/gas-shunin-otsu/questions_json/2021/25_verified_publication/2021_verified_publication.json'
        self.bundle.update(list_group_id='2021', evidence=dict(documentIndexSha256='a'*64))
        binding = save(source, self.bundle)
        source_bytes = source.read_bytes()
        patch = save(self.root/'patch.json', dict(schemaVersion='feedback-improvement-private-patch/v1',
            taskId='task', questionId='display', fieldDiffs=self.update['fieldDiffs'],
            precondition=dict(updateTimeExact=self.update['baseline']['updateTimeExact'], baselineSha256=self.update['baseline']['sha256']),
            formalPatchSaved=False, FirestoreWritten=False, approvalReady=False,
            executablePatch=False, fullDocumentUploadAllowed=False))
        self.update['preparedPatch'] = patch
        result = save(self.root/'result.json', dict(taskId='task', status='review_pending',
            formalPatchSaved=False, FirestoreWritten=False, approvalReady=False,
            proposedUpdates=[self.update], primaryEvidence=[]))
        manifest = self.root/'manifest.json'
        save(manifest, dict(tasks=[dict(taskId='task', status='review_pending', artifactRef=result['ref'], artifactSha256=result['sha256'])]))
        bindings = self.root/'bindings.json'
        save(bindings, dict(results=[dict(questionId='display', status='matched', candidates=[{**binding,'sourceKind':'verified_25'}])]))
        destination = self.root/'output/user_feedback_response_system/execution/run'
        plan = prepare_plan(self.root, manifest, bindings, destination)
        self.assertEqual(source.read_bytes(), source_bytes)
        self.assertEqual(len(plan['artifacts']), 1)
        self.assertEqual(plan['artifacts'][0]['completeSiblingIds'], ['display','sibling'])
        self.assertFalse(plan['approvalReady'])
        self.assertFalse(plan['formalPatchSaved'])
        self.assertFalse(plan['FirestoreWritten'])
        candidate = Path(plan['artifacts'][0]['candidateRef'])
        self.assertTrue(candidate.is_relative_to(destination))
        self.assertEqual(candidate.stat().st_mode & 0o777, 0o600)
        self.assertIn('independent_content_review', plan['requiredNext'])


if __name__ == '__main__':
    unittest.main()
