"""Saved gas25 checker routes; all input artifacts are temporary fixtures."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.check import check_choice_text_alignment as alignment
from scripts.check import check_correct_choice_patch_coverage as coverage
from scripts.check import check_required_fields as required
from scripts.check import verified_feedback_contract as contract
from scripts.common.requirements import DEFAULT_REQUIREMENTS_PATH
from tools.question_bank import feedback_verified_save as saver


class VerifiedFeedbackCheckersTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.connection = object()
        self.directory = self.root / 'output/gas-shunin-otsu/questions_json/2021'
        self.formal = self.directory / '25_verified_publication/2021_verified_publication.json'
        self.merged = self.directory / '30_merged_2/2021_verified_publication_merged.json'
        self.converted = self.directory / '40_convert/2021_firestore_fixture.json'
        self.private = self.root / 'private-fixture'
        self.path = self.private / 'formal-save-plan.json'
        rows = []
        for index in range(202):
            rows.append(dict(
                questionId=f'gas-shunin-otsu-2021-law-q{index:03}-s01',
                questionSetId='set', originalQuestionId=f'original-{index // 5}',
                originalQuestionBodyText='公式問題本文', originalQuestionChoiceText='公式の肢',
                questionText='完全命題。', questionBodyText='公式問題本文',
                qualificationId='gas-shunin-otsu', listGroupId='2021', examYear=2021,
                examSource='令和3年度', questionType='true_false',
                isOfficial=True, isDeleted=False, isChoiceOnly=False, isGroupable=False,
                questionTags=['法令'], correctChoiceText='正しい',
                explanationText='正しい。保存前の説明。', suggestedQuestions=[],
                suggestedQuestionDetails=[]))
        rows[1]['lawRevisionFacts'] = {
            'auditStatus': 'same_as_current', 'differenceFacts': ['保存前の差分説明。'],
            'current': {'lawId': 'law', 'article': '144', 'correctChoiceText': '正しい'}}
        self.before = dict(schemaVersion=contract.gas.SCHEMA_VERSION,
                           qualificationId='gas-shunin-otsu', list_group_id='2021',
                           questions=rows, total_count=202, evidence={'fixture': True})
        self.candidate = deepcopy(self.before)
        self.candidate['questions'][0]['explanationText'] = '正しい。意味と条件を説明。'
        self.updates = [self.update(0, 'explanationText')]
        self.plan = dict(schemaVersion=saver.SCHEMA,
                         formalPath=str(self.formal.relative_to(self.root)),
                         beforeRef=str(self.private / 'before.json'),
                         candidateRef=str(self.private / 'candidate.json'),
                         updates=self.updates, inputs={})
        requirements = self.root / 'config/requirements/required_fields.toml'
        requirements.parent.mkdir(parents=True)
        shutil.copyfile(DEFAULT_REQUIREMENTS_PATH, requirements)
        evidence = self.write(self.private / 'evidence.json', {'fixture': True})
        self.plan['inputs'] = {evidence['ref']: evidence['sha256']}
        self.persist()
        # Native, registry and save receipt validation belong to load_saved's
        # existing tests. This fixture replaces that boundary only; projection,
        # DTO, requirements, count, hashes and delta checks remain real here.
        saved = patch.object(saver, 'load_saved', side_effect=self.saved_receipt)
        self.saved = saved.start()
        self.addCleanup(saved.stop)

    def write(self, path, value):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return {'ref': str(path), 'sha256': self.digest(path)}

    @staticmethod
    def digest(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def update(self, index, field):
        before = self.before['questions'][index]
        baseline = self.write(self.private / f'baseline-{index}.json', {
            'questionId': before['questionId'], 'exists': True,
            'fields': deepcopy(before), 'updateTimeExact': '2026-10-15T00:00:00.123456789Z'})
        baseline['updateTimeExact'] = '2026-10-15T00:00:00.123456789Z'
        return {'questionId': before['questionId'], 'baseline': baseline, 'fieldDiffs': [{
            'field': field, 'beforePresent': field in before, 'before': deepcopy(before.get(field)),
            'after': deepcopy(self.candidate['questions'][index][field])}]}

    def persist(self):
        self.write(self.private / 'before.json', self.before)
        self.write(self.private / 'candidate.json', self.candidate)
        self.write(self.formal, self.candidate)
        self.write(self.merged, {**deepcopy(self.candidate), 'schemaVersion': contract.gas.MERGED_SCHEMA_VERSION})
        self.write(self.converted, {k: deepcopy(self.candidate[k])
                                    for k in ('list_group_id', 'questions', 'total_count')})
        self.plan.update(beforeSha256=self.digest(self.private / 'before.json'),
                         candidateSha256=self.digest(self.private / 'candidate.json'))
        self.plan['planHash'] = saver.sha256_json({k: v for k, v in self.plan.items() if k != 'planHash'})
        self.write(self.path, self.plan)
        self.receipt = dict(
            schemaVersion=(saver.MAINTENANCE_RECEIPT
                           if self.plan['schemaVersion'] == saver.MAINTENANCE_SCHEMA
                           else saver.RECEIPT),
            planHash=self.plan['planHash'], candidateSha256=self.plan['candidateSha256'],
            beforeSha256=self.plan['beforeSha256'], FirestoreWritten=False)
        self.write(self.private / 'save-receipt.json', self.receipt)

    def saved_receipt(self, root, path, receipt_path, *, connection):
        self.assertEqual((root, path, receipt_path),
                         (self.root, self.path, self.private / 'save-receipt.json'))
        self.assertIs(connection, self.connection)
        return deepcopy(self.receipt)

    def check(self, kind):
        return contract.check(kind, self.root, self.path, connection=self.connection)

    def reject(self, kind, text=None):
        result = self.check(kind)
        self.assertEqual(result['exitCode'], 1, result)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(result['issues'])
        if text:
            self.assertIn(text, '\n'.join(result['issues']))
        return result

    def test_all_three_routes_check_full_202_with_hashes_and_no_writes(self):
        inputs = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.rglob('*') if p.is_file()}
        for kind in sorted(contract.KINDS):
            with self.subTest(kind=kind):
                result = self.check(kind)
                self.assertEqual(result['exitCode'], 0, result)
                self.assertEqual(result['checkedCount'], 202)
                self.assertEqual(result['schemaVersion'], contract.SCHEMA)
                self.assertEqual(result['planHash'], self.plan['planHash'])
                self.assertEqual(result['saveReceiptSha256'], self.digest(self.private / 'save-receipt.json'))
                self.assertIs(result['inputFilesVerified'], True)
                expected_inputs = {
                    str(self.path), str(self.private / 'save-receipt.json'),
                    str(self.private / 'before.json'), str(self.private / 'candidate.json'),
                    str(self.formal), str(self.merged), str(self.converted),
                    *self.plan['inputs'],
                    *(str(contract.IMPLEMENTATION_ROOT / p) for p in contract.IMPLEMENTATION_FILES),
                }
                if kind == 'required':
                    expected_inputs.add(str(self.root / 'config/requirements/required_fields.toml'))
                self.assertEqual(set(result['inputFiles']), expected_inputs)
                self.assertEqual(result['inputFiles'], {p: self.digest(p) for p in expected_inputs})
                ids = [q['questionId'] for q in self.candidate['questions']]
                self.assertEqual(result['checkedIdsSha256'], saver.sha256_json(sorted(ids)))
                for stage, path in [('25', self.formal), ('30', self.merged), ('40', self.converted)]:
                    self.assertEqual(result['artifacts'][stage]['sha256'], self.digest(path))
        self.assertEqual(self.saved.call_count, 6)
        self.assertEqual(inputs, {p: (p.read_bytes(), p.stat().st_mtime_ns)
                                  for p in self.root.rglob('*') if p.is_file()})

    def test_alignment_runs_actual_contract_for_all_implicit_dtos(self):
        with patch.object(contract, 'choice_text_content_alignment_issue',
                          wraps=contract.choice_text_content_alignment_issue) as inspect:
            self.assertEqual(self.check('alignment')['exitCode'], 0)
        self.assertEqual(inspect.call_count, 202)
        self.assertTrue(all('choiceTextList' not in call.args[0] for call in inspect.call_args_list))

    def test_genuine_save_or_authority_failure_is_not_bypassed(self):
        self.saved.side_effect = ValueError('native/authority rejected')
        for kind in contract.KINDS:
            self.reject(kind, 'native/authority rejected')

    def test_missing_save_receipt_rejected(self):
        (self.private / 'save-receipt.json').unlink()
        self.reject('required')
        self.saved.assert_not_called()

    def test_missing_invalid_or_changed_plan_input_hash_refused(self):
        inputs = deepcopy(self.plan['inputs'])
        for value in ({}, [], {'relative.json': '0' * 64},
                      {next(iter(inputs)): '0' * 64}):
            self.plan['inputs'] = value
            self.persist()
            result = self.reject('alignment')
            self.assertEqual(result['inputFiles'], {})
            self.assertIs(result['inputFilesVerified'], False)
        self.plan['inputs'] = inputs
        self.persist()
        Path(next(iter(inputs))).unlink()
        self.reject('required')

    def test_unsupported_schema_bad_plan_hash_and_receipt_schema_rejected(self):
        for mutation in ('schema', 'hash', 'receipt'):
            with self.subTest(mutation=mutation):
                original = deepcopy(self.plan)
                if mutation == 'schema':
                    self.plan['schemaVersion'] = 'unrecognized/v1'
                self.persist()
                if mutation == 'hash':
                    self.write(self.path, {**self.plan, 'planHash': '0' * 64})
                if mutation == 'receipt':
                    self.receipt['schemaVersion'] = 'unrecognized/v1'
                    self.write(self.private / 'save-receipt.json', self.receipt)
                self.reject('required')
                self.plan = original
                self.persist()

    def test_empty_and_fake_old_array_refused_for_every_checker(self):
        for shape in ('empty', 'old-array'):
            before = deepcopy(self.before)
            candidate = deepcopy(self.candidate)
            if shape == 'empty':
                self.before.update(questions=[], total_count=0)
                self.candidate.update(questions=[], total_count=0)
            else:
                self.before['question_bodies'] = self.before.pop('questions')
                self.candidate['question_bodies'] = self.candidate.pop('questions')
            if shape == 'old-array':
                # Construct the invalid formal source without fabricating a 40.
                self.write(self.formal, self.candidate)
                self.plan['candidateSha256'] = self.digest(self.formal)
                self.plan['planHash'] = saver.sha256_json({k: v for k, v in self.plan.items() if k != 'planHash'})
                self.write(self.path, self.plan)
                self.receipt.update(planHash=self.plan['planHash'], candidateSha256=self.plan['candidateSha256'])
                self.write(self.private / 'save-receipt.json', self.receipt)
            else:
                self.persist()
            for kind in contract.KINDS:
                with self.subTest(shape=shape, kind=kind):
                    self.reject(kind)
                    result = self.check(kind)
                    self.assertEqual(result['checkedCount'], 0)
                    self.assertEqual(result['inputFiles'], {})
                    self.assertIs(result['inputFilesVerified'], False)
            self.before, self.candidate = before, candidate
            self.persist()

    def test_duplicate_missing_extra_and_reordered_ids_rejected(self):
        for mutate in ('duplicate', 'missing', 'extra', 'order'):
            candidate = deepcopy(self.candidate)
            if mutate == 'duplicate':
                self.candidate['questions'][1]['questionId'] = self.candidate['questions'][0]['questionId']
            elif mutate == 'missing':
                self.candidate['questions'].pop()
            elif mutate == 'extra':
                self.candidate['questions'].append({**deepcopy(self.candidate['questions'][-1]), 'questionId': 'extra'})
            else:
                self.candidate['questions'].reverse()
            self.candidate['total_count'] = len(self.candidate['questions'])
            self.persist()
            self.reject('coverage')
            self.candidate = candidate
            self.persist()

    def test_wrong_count_qualification_and_server_metadata_rejected(self):
        for field, value in [('total_count', 0), ('qualificationId', 'other')]:
            old = self.candidate[field]
            self.candidate[field] = value
            self.persist()
            self.reject('required')
            self.candidate[field] = old
        self.candidate['questions'][-1]['createdAt'] = 'server-metadata'
        self.persist()
        self.reject('required', 'server-field')

    def test_25_30_and_40_tampering_or_missing_rejected(self):
        for file in (self.formal, self.merged, self.converted):
            for alteration in ('missing', 'changed'):
                with self.subTest(file=file, alteration=alteration):
                    content = file.read_bytes()
                    if alteration == 'missing':
                        file.unlink()
                    else:
                        payload = json.loads(content)
                        payload['questions'][-1]['questionId'] = 'different'
                        self.write(file, payload)
                    self.reject('alignment')
                    file.write_bytes(content)

    def test_multiple_40_and_symlink_input_rejected(self):
        extra = self.converted.with_name('2021_firestore_second.json')
        extra.write_bytes(self.converted.read_bytes())
        self.reject('alignment', 'exactly one')
        extra.unlink()
        saved = self.merged.with_suffix('.real')
        self.merged.rename(saved)
        self.merged.symlink_to(saved)
        self.reject('alignment', 'physical')

    def test_final_dto_unknown_type_boolean_and_fill_blank_contract(self):
        for field, value, message in [
            ('unknownField', True, 'disallowed'),
            ('isOfficial', 'true', 'bool'),
            ('questionType', 'unknown', 'questionType'),
            ('questionTags', 'law', 'list[str]'),
            ('fillInBlankContent', {}, 'must be omitted'),
        ]:
            candidate = deepcopy(self.candidate)
            self.candidate['questions'][-1][field] = value
            self.persist()
            self.reject('required', message)
            self.candidate = candidate
        self.candidate['questions'][-1]['questionType'] = 'fill_in_blank'
        self.persist()
        self.reject('required', 'fillInBlankContent')

    def test_required_uses_firestore_questions_rules_even_when_dto_allows_omission(self):
        del self.candidate['questions'][-1]['examSource']
        self.persist()
        self.reject('required', 'examSource')

    def test_valid_fill_blank_and_implicit_single_choice_use_final_dto_contracts(self):
        content = {'schemaVersion': 1, 'segments': [
            {'type': 'text', 'text': '空欄：'},
            {'type': 'blank', 'blankId': 'b', 'placementId': 'p'}],
            'blanks': [{'blankId': 'b', 'blankIndex': 1,
                        'correctAnswerText': 'ガス', 'acceptedAnswerTexts': ['ガス']}],
            'answerInput': {'allowedMethods': ['text_input'], 'defaultMethod': 'text_input'}}
        for bundle in (self.before, self.candidate):
            bundle['questions'][-1].update(questionType='fill_in_blank', fillInBlankContent=content)
            bundle['questions'][-2].update(questionType='single_choice', correctChoiceText='ガス',
                                            incorrectChoice1Text='空気')
        self.persist()
        for kind in contract.KINDS:
            self.assertEqual(self.check(kind)['exitCode'], 0)

    def test_invalid_requirements_fail_with_json_result(self):
        path = self.root / 'config/requirements/required_fields.toml'
        for content in ('[invalid', '[default.stages.firestore]\n'):
            path.write_text(content)
            self.reject('required')

    def test_verdict_and_explanation_disagreement_rejected(self):
        for answer, explanation in [('誤り', '正しい。説明。'), ('正しい', '間違い。説明。')]:
            self.candidate['questions'][-1].update(correctChoiceText=answer, explanationText=explanation)
            self.persist()
            for kind in contract.KINDS:
                self.reject(kind)

    def test_full_replay_rejects_protected_unapproved_sibling_and_root_changes(self):
        mutations = [lambda c: c['questions'][0].update(questionText='改変本文'),
                     lambda c: c['questions'][0].update(correctChoiceText='間違い', explanationText='間違い。説明。'),
                     lambda c: c['questions'][201].update(explanationText='正しい。対象外変更。'),
                     lambda c: c.update(evidence={'changed': True})]
        for mutate in mutations:
            candidate = deepcopy(self.candidate)
            mutate(self.candidate)
            self.persist()
            self.reject('coverage')
            self.candidate = candidate

    def test_empty_duplicate_and_incorrect_before_approved_deltas_rejected(self):
        changes = [[], self.updates + self.updates,
                   [{**deepcopy(self.updates[0]), 'fieldDiffs': []}]]
        wrong = deepcopy(self.updates)
        wrong[0]['fieldDiffs'][0]['before'] = '違う保存前'
        changes.append(wrong)
        for updates in changes:
            self.plan['updates'] = updates
            self.persist()
            self.reject('coverage')

    def test_real_save_projector_rejects_forged_baseline_hash(self):
        baseline = Path(self.updates[0]['baseline']['ref'])
        baseline.write_bytes(b'changed')
        self.reject('coverage', 'artifact hash')

    def test_dedicated_maintenance_projection_accepts_two_documents_and_exact_facts(self):
        self.candidate['questions'][1]['lawRevisionFacts']['differenceFacts'] = ['訂正した差分説明。']
        self.updates.append(self.update(1, 'lawRevisionFacts'))
        self.plan['schemaVersion'] = saver.MAINTENANCE_SCHEMA
        self.persist()
        candidate_ref = {'ref': self.plan['candidateRef'], 'sha256': self.plan['candidateSha256']}
        siblings = [r['questionId'] for r in self.before['questions'][:5]]
        proposal = dict(schemaVersion='feedback-gas-maintenance-content-scope/v1',
                        scopeHash='registered-scope', taskId='task', originalQuestionId='original-0',
                        proposedUpdates=self.updates, candidate=candidate_ref)
        reference = self.write(self.private / 'proposal.json', proposal)
        self.plan['maintenanceBinding'] = dict(
            proposalRef=reference, scopeHash='registered-scope', relatedImprovementTaskId='task',
            originalQuestionId='original-0', allSiblingIds=siblings,
            candidateSha256=candidate_ref['sha256'], publicationDocuments=[
                {'questionId': identity, 'writeFields': sorted(
                    d['field'] for u in self.updates if u['questionId'] == identity for d in u['fieldDiffs'])}
                for identity in siblings])
        self.persist()
        self.assertEqual(self.check('coverage')['exitCode'], 0)
        self.candidate['questions'][1]['lawRevisionFacts']['current']['article'] = 'other'
        self.updates[1] = self.update(1, 'lawRevisionFacts')
        proposal['proposedUpdates'] = self.updates
        self.persist()
        proposal['candidate'] = {'ref': self.plan['candidateRef'], 'sha256': self.plan['candidateSha256']}
        reference = self.write(self.private / 'proposal.json', proposal)
        self.plan['maintenanceBinding'].update(proposalRef=reference, candidateSha256=self.plan['candidateSha256'])
        self.persist()
        self.reject('coverage', 'outside explanatory difference')

    def test_second_authority_verification_and_artifact_race_rejected(self):
        def race(*args, **kwargs):
            if self.saved.call_count == 2:
                self.merged.write_bytes(self.merged.read_bytes() + b' ')
            return self.saved_receipt(*args, **kwargs)
        self.saved.side_effect = race
        self.reject('alignment', 'changed during inspection')
        self.persist()
        self.saved.reset_mock()
        self.saved.side_effect = [deepcopy(self.receipt), ValueError('authority revoked')]
        self.reject('alignment', 'authority revoked')

    def test_plan_evidence_hash_is_rechecked_after_second_native_verification(self):
        def race(*args, **kwargs):
            if self.saved.call_count == 2:
                Path(next(iter(self.plan['inputs']))).write_bytes(b'changed evidence')
            return self.saved_receipt(*args, **kwargs)
        self.saved.side_effect = race
        result = self.reject('required', 'changed during inspection')
        self.assertEqual(result['inputFiles'], {})
        self.assertIs(result['inputFilesVerified'], False)

    def test_missing_validator_implementation_does_not_emit_verified_hashes(self):
        # A physical implementation file is required, never silently omitted.
        with patch.object(contract, 'IMPLEMENTATION_FILES',
                          (*contract.IMPLEMENTATION_FILES, 'missing-validator.py')):
            result = self.reject('coverage')
        self.assertEqual(result['checkedCount'], 0)
        self.assertEqual(result['inputFiles'], {})
        self.assertIs(result['inputFilesVerified'], False)

    def test_cli_selects_explicit_route_and_emits_single_json(self):
        for module, kind in [(alignment, 'alignment'), (required, 'required'), (coverage, 'coverage')]:
            with self.subTest(kind=kind), patch.object(contract, 'readonly_connection') as connection:
                connection.return_value.__enter__.return_value = self.connection
                output = io.StringIO()
                with redirect_stdout(output):
                    code = module.main(['--verified-save-plan', str(self.path), '--repo-root', str(self.root)])
                receipt = json.loads(output.getvalue())
                self.assertEqual((code, receipt['checkKind'], receipt['checkedCount']), (0, kind, 202))

    def test_cli_validation_failure_is_nonzero_json_not_zero_row_success(self):
        self.write(self.path, [])
        for module in (alignment, required, coverage):
            with patch.object(contract, 'readonly_connection') as connection:
                connection.return_value.__enter__.return_value = self.connection
                output = io.StringIO()
                with redirect_stdout(output):
                    code = module.main(['--verified-save-plan', str(self.path), '--repo-root', str(self.root)])
                result = json.loads(output.getvalue())
                self.assertEqual((code, result['exitCode'], result['status']), (1, 1, 'failed'))
                self.assertTrue(result['issues'])
                self.assertEqual(result['inputFiles'], {})
                self.assertIs(result['inputFilesVerified'], False)

    def test_cli_mixed_missing_and_abbreviated_legacy_arguments_refused(self):
        modes = [(alignment, ['--base-dir', 'old'], ['--stage=merged']),
                 (required, ['--base-dir', 'old'], ['--requirements=old']),
                 (coverage, ['--source', 'old'], ['--require-full'])]
        full = ['--verified-save-plan', str(self.path), '--repo-root', str(self.root)]
        for module, mixed, explicit_default in modes:
            for args in ([], full + mixed, full + explicit_default,
                         ['--repo-root', str(self.root)], ['--verified-save-plan', str(self.path)]):
                with self.subTest(module=module.__name__, args=args), redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        module.main(args)
                    self.assertEqual(error.exception.code, 2)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            alignment.main(full + ['--base-d', 'old'])

    def test_legacy_routes_still_process_original_formats(self):
        source = self.root / 'legacy/2021/00_source/question_2021.json'
        self.write(source, {'question_bodies': [{'original_question_id': 'original',
                    'public_question_id': 'q', 'question_url': 'https://example.test/q',
                    'answer_result_text': '正しい', 'choiceTextList': ['公式の肢']}]})
        patch_path = self.root / 'old-patch.json'
        self.write(patch_path, [{'original_question_id': 'original', 'correctChoiceText': ['正しい']}])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(alignment.main(['--base-dir', str(self.root / 'legacy'), '--stage', 'source']), 0)
            self.assertEqual(required.main(['--base-dir', str(self.root / 'legacy'), '--stage', 'source']), 0)
            self.assertEqual(coverage.main(['--source', str(source), '--patch', str(patch_path), '--require-full']), 0)
        self.saved.assert_not_called()

        python = shutil.which('python3')
        self.assertIsNotNone(python)
        routes = [
            ('check_choice_text_alignment.py', ['--base-dir', str(self.root / 'legacy'), '--stage', 'source']),
            ('check_required_fields.py', ['--base-dir', str(self.root / 'legacy'), '--stage', 'source']),
            ('check_correct_choice_patch_coverage.py', ['--source', str(source), '--patch', str(patch_path), '--require-full']),
        ]
        # Exercise the real entry points in ordinary python3, also without any
        # third-party site-packages. The old routes must never import Firebase.
        for flags in ([], ['-S']):
            for script, arguments in routes:
                for args in (['--help'], arguments):
                    command = [python, *flags, str(contract.IMPLEMENTATION_ROOT / 'scripts/check' / script), *args]
                    with self.subTest(command=command):
                        result = subprocess.run(command, capture_output=True, text=True,
                                                cwd=contract.IMPLEMENTATION_ROOT, timeout=30)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
