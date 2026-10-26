#!/usr/bin/env python3
"""Negative contract tests; synthetic receipts are never consumed by live work."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

PATH = Path(__file__).with_name('T004-review-driver.py')
spec = importlib.util.spec_from_file_location('hei_review_driver', PATH)
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)

class DriverContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.images = [{'sourcePath': 'page.png', 'sha256': 'a' * 64, 'byteCount': 100}]
        self.native = {'session_id': 'session-a', 'thread_id': 'thread-a', 'turn_id': 'turn-a',
                       'image_inputs': copy.deepcopy(self.images), 'final_message': '{}', 'changed_files': [], 'subagent_thread_ids': []}
    def tearDown(self):
        self.tmp.cleanup()
    def schema_fixture(self, phase='blind_a'):
        evidence = {'sourceClass': 'official', 'title': '公式', 'locator': 'PDF 3ページ / 問1',
                    'verifiedAt': '2026-10-27T00:00:00Z', 'contentHash': 'e'*64}
        rows = [{'sourceQuestionId': '2017:law:問1', 'workId': 'law-1', 'reviewerSlot': 'A', 'inputHash': 'i'*64,
                 'input': {'blindReviewHashes': ['a'*64, 'b'*64]}}]
        items = {'2017:law:問1': {'evidence': [evidence]}}
        contracts = ['c'*64, 'd'*64]
        schema = d.build_output_schema(phase, rows, items, contracts)
        if phase.startswith('blind_'):
            review = {'schemaVersion': 'question-issue-blind-review/v1', 'phase': 'blind', 'reviewerSlot': 'A',
                      'inputHash': 'i'*64, 'workflowContractHashes': contracts, 'conclusion': 'problem_found',
                      'proposedChanges': {'questionBodyText': '完全本文'}, 'findings': [], 'evidence': [evidence]}
        else:
            review = {'schemaVersion': 'question-issue-challenge-review/v1', 'phase': 'challenge', 'inputHash': 'i'*64,
                      'blindReviewHashes': ['a'*64, 'b'*64], 'decision': 'fix', 'rationale': '公式を照合',
                      'changes': {'questionBodyText': '完全本文'}, 'evidence': [evidence]}
        review.update(appRootCauseKey=None, reproductionEvidence=[])
        return schema, {'results': {'law-1': review}}
    def test_schema_fixes_each_work_id_and_metadata(self):
        schema, valid = self.schema_fixture()
        d.validate_output_schema(valid, schema)
        for field, value in [('inputHash', 'wrong'), ('reviewerSlot', 'B'), ('workflowContractHashes', ['wrong']), ('phase', 'challenge')]:
            changed = copy.deepcopy(valid)
            changed['results']['law-1'][field] = value
            with self.assertRaises(ValueError):
                d.validate_output_schema(changed, schema)
        for field in ['sourceClass', 'title', 'locator', 'verifiedAt', 'contentHash']:
            changed = copy.deepcopy(valid)
            changed['results']['law-1']['evidence'][0][field] = 'wrong'
            with self.assertRaisesRegex(ValueError, 'fixed metadata mismatch'):
                d.validate_output_schema(changed, schema)
    def test_schema_leaves_blind_meaning_and_complete_body_free(self):
        schema, valid = self.schema_fixture()
        for conclusion in ['problem_found', 'no_problem', 'insufficient_evidence', 'app_behavior_suspected']:
            changed = copy.deepcopy(valid)
            review = changed['results']['law-1']
            review['conclusion'] = conclusion
            review['proposedChanges'] = {'questionBodyText': 'モデルが独立導出した全文'} if conclusion == 'problem_found' else {}
            d.validate_output_schema(changed, schema)
        body_schema = schema['properties']['results']['properties']['law-1']['properties']['proposedChanges']['anyOf'][1]
        self.assertEqual(body_schema['properties']['questionBodyText'], {'type': 'string'})
    def test_schema_leaves_challenge_hold_no_change_fix_free(self):
        schema, valid = self.schema_fixture('challenge')
        for decision in ['fix', 'no_change', 'hold', 'app_update']:
            changed = copy.deepcopy(valid)
            changed['results']['law-1']['decision'] = decision
            changed['results']['law-1']['changes'] = {'questionBodyText': '完全値'} if decision == 'fix' else {}
            d.validate_output_schema(changed, schema)
        changed = copy.deepcopy(valid)
        changed['results']['law-1']['blindReviewHashes'].reverse()
        with self.assertRaisesRegex(ValueError, 'fixed metadata mismatch'):
            d.validate_output_schema(changed, schema)
    def test_schema_keeps_choice_field_and_app_outcomes_available(self):
        schema, valid = self.schema_fixture()
        for changes in [{'choiceTextList': ['独立した完全肢1', '肢2']}, {'questionBodyText': '別の完全本文', 'choiceTextList': ['全肢']}]:
            changed = copy.deepcopy(valid)
            changed['results']['law-1']['proposedChanges'] = changes
            d.validate_output_schema(changed, schema)
        changed = copy.deepcopy(valid)
        changed['results']['law-1'].update(conclusion='app_behavior_suspected', proposedChanges={},
                                         appRootCauseKey='app-data-rendering', reproductionEvidence=['実再現根拠'])
        d.validate_output_schema(changed, schema)
        challenge_schema, challenge = self.schema_fixture('challenge')
        challenge['results']['law-1'].update(decision='app_update', changes={},
                                            appRootCauseKey='app-data-rendering', reproductionEvidence=['実再現根拠'])
        d.validate_output_schema(challenge, challenge_schema)
    def test_schema_is_strict_at_all_object_boundaries(self):
        schema, valid = self.schema_fixture()
        for position in ['root', 'results', 'review', 'evidence', 'changes']:
            changed = copy.deepcopy(valid)
            targets = {'root': changed, 'results': changed['results'], 'review': changed['results']['law-1'],
                       'evidence': changed['results']['law-1']['evidence'][0], 'changes': changed['results']['law-1']['proposedChanges']}
            targets[position]['unexpected'] = 'value'
            with self.assertRaises(ValueError):
                d.validate_output_schema(changed, schema)
        changed = copy.deepcopy(valid)
        del changed['results']['law-1']['findings']
        with self.assertRaisesRegex(ValueError, 'required property missing'):
            d.validate_output_schema(changed, schema)
    def test_object_results_duplicate_work_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate JSON key'):
            d.split_results('{"results":{"law-1":{},"law-1":{}}}', {'law-1'})
    def test_schema_survives_app_server_adapter_without_weakening(self):
        from tools.question_review_console.codex_app_server import adapt_output_schema_for_app_server
        schema, _valid = self.schema_fixture()
        self.assertEqual(adapt_output_schema_for_app_server(schema), schema)
    def test_canonical_snapshot_hash_drift_is_rejected_without_mutation(self):
        driver = object.__new__(d.Driver)
        driver.root, driver.year = self.root, '2017'
        image = self.root / 'page.png'
        image.write_bytes(b'fixture bytes')
        evidence = {'sourceClass': 'official', 'title': '公式', 'locator': 'PDF 1ページ', 'verifiedAt': '2026-10-27T00:00:00Z', 'contentHash': 'a'*64}
        canonical = {'officialEvidenceCandidates': [{**evidence, 'verifiedTranscription': '本文', 'candidateChanges': {'questionBodyText': '本文'},
                    'localRenderedPagePaths': ['page.png'], 'localRenderedPageHashes': {'page.png': d.digest(image)}}]}
        binding = d.SourceIdentityBinding.from_values('source', 'review', 'record#0')
        work = {'originalQuestionId': 'review', **binding.as_mapping(), 'qualificationId': d.QUALIFICATION, 'listGroupId': '2017',
                'caseIds': ['case'], 'caseInputHashes': {'case': d.sha256_json(canonical)}, 'caseSnapshots': [{'id': 'case', 'canonicalSnapshot': canonical}]}
        item = {'workItem': work, 'evidence': [evidence], 'verifiedTranscription': '本文', 'candidateChanges': {'questionBodyText': '本文'}, 'imagePaths': ['page.png']}
        before = copy.deepcopy(item)
        self.assertEqual(driver._work_item(item, binding), work)
        self.assertEqual(item, before)
        item['workItem']['caseSnapshots'][0]['canonicalSnapshot']['changed'] = True
        with self.assertRaisesRegex(ValueError, 'caseInputHashes mismatch'):
            driver._work_item(item, binding)
    def test_replay_refuses_different_prompt_or_attempt_binding(self):
        row = {'workId': 'a', 'normalPrompt': '正規全文', 'slotBinding': {'slot': 'A', 'inputHash': 'fixed'}}
        executor = d.VerifiedRecordedExecutor(self.root, {'blind_a': row})
        for prompt, binding in [('要約', row['slotBinding']), ('正規全文', {'slot': 'B', 'inputHash': 'fixed'})]:
            with self.assertRaisesRegex(ValueError, 'standard replay prompt/slot'):
                executor.execute(work_id='a', phase='blind_a', prompt=prompt, replacements={'$ATTEMPT_BINDING': binding})
    def test_native_tuple_serialization_preserves_final_message(self):
        result = d.CodexAppServerClient  # Ensure test does not start a native client.
        from tools.question_review_console.codex_app_server import AppServerTurnResult
        turn = AppServerTurnResult(thread_id='t', session_id='s', turn_id='u', final_message=' {"raw":"値"} ',
                                   model='fixture-only', service_tier=None, image_inputs=tuple(self.images))
        payload = d.native_payload(turn)
        self.assertIsInstance(payload['image_inputs'], list)
        self.assertEqual(payload['final_message'], turn.final_message)
        d.validate_native(payload, self.images)
    def test_prompt_transport_preserves_every_byte_and_detects_change(self):
        shared = {}
        block = '\n\n<ROUTED_WORKFLOW_CONTRACTS>\n完全な契約本文\n</ROUTED_WORKFLOW_CONTRACTS>'
        prompts = ['prefix-A' + block + '\nINPUT-A', 'prefix-B' + block + '\nINPUT-B', 'challenge全文']
        packed = [d.pack_prompt(p, shared) for p in prompts]
        self.assertEqual(len(shared), 1)
        self.assertEqual([d.unpack_prompt(p, shared) for p in packed], prompts)
        corrupted = copy.deepcopy(shared)
        key = next(iter(corrupted))
        corrupted[key] += '変更'
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            d.unpack_prompt(packed[0], corrupted)
        changed = copy.deepcopy(packed[0])
        changed['suffix'] += '改変'
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            d.unpack_prompt(changed, shared)
    def test_duplicate_json_key_is_rejected(self):
        for text in ['{"results":[],"results":[]}', '{"results":[{"workId":"x","review":{"inputHash":"a","inputHash":"b"}}]}']:
            with self.assertRaisesRegex(ValueError, 'duplicate JSON key'):
                d.strict_json(text)
    def test_missing_unknown_duplicate_work_id_rejected(self):
        for rows in [{}, {'wrong': {}}, {'a': {}, 'wrong': {}}, []]:
            with self.assertRaises(ValueError):
                d.split_results(json.dumps({'results': rows}), {'a'})
    def test_complete_work_id_map_is_accepted_without_mutation(self):
        obj = {'results': {'a': {'proposedChanges': {'questionBodyText': '全本文'}}}}
        self.assertEqual(d.split_results(json.dumps(obj), {'a'}), obj['results'])
    def test_image_hash_byte_count_and_path_are_rejected(self):
        for field, value in [('sha256', 'b' * 64), ('byteCount', 99), ('sourcePath', 'wrong.png')]:
            native = copy.deepcopy(self.native)
            native['image_inputs'][0][field] = value
            with self.assertRaisesRegex(ValueError, 'bytes/hash'):
                d.validate_native(native, self.images)
    def test_missing_or_shared_native_identity_rejected(self):
        for field in ('session_id', 'thread_id', 'turn_id'):
            native = copy.deepcopy(self.native)
            native[field] = ''
            with self.assertRaises(ValueError):
                d.validate_native(native, self.images)
            other = {'session_id': 'session-b', 'thread_id': 'thread-b', 'turn_id': 'turn-b'}
            other[field] = self.native[field]
            with self.assertRaisesRegex(ValueError, 'distinct native'):
                d.validate_native(self.native, self.images, other_identities=[other])
    def test_readonly_and_no_subagents_required(self):
        for key in ['changed_files', 'subagent_thread_ids']:
            native = copy.deepcopy(self.native)
            native[key] = ['unexpected']
            with self.assertRaisesRegex(ValueError, 'read-only'):
                d.validate_native(native, self.images)
    def test_stale_input_freeze_rejected_and_identical_retry_accepted(self):
        path = self.root / 'frozen.json'
        d.freeze(path, {'input': 1})
        before = path.read_bytes()
        d.freeze(path, {'input': 1})
        self.assertEqual(path.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, 'stale or conflicting'):
            d.freeze(path, {'input': 2})
        self.assertEqual(path.read_bytes(), before)
    def test_blind_disagreement_is_not_auto_corrected(self):
        evidence = [{'sourceClass': 'official', 'title': '公式', 'locator': 'PDF 1ページ', 'contentHash': 'a'*64, 'verifiedAt': '2026-10-27T00:00:00Z'}]
        a = {'conclusion': 'problem_found', 'proposedChanges': {'questionBodyText': '甲'}, 'evidence': evidence}
        b = copy.deepcopy(a)
        b['proposedChanges']['questionBodyText'] = '乙'
        with self.assertRaisesRegex(ValueError, 'consensus mismatch'):
            d.validate_resume_blind_consensus(a, b)
        self.assertEqual(a['proposedChanges']['questionBodyText'], '甲')
        self.assertEqual(b['proposedChanges']['questionBodyText'], '乙')
    def test_response_receipt_tamper_rejected(self):
        driver = object.__new__(d.Driver)
        driver.work = self.root
        obj = {'native': self.native, 'inputHash': 'a'*64}
        obj['receiptHash'] = d.sha256_json(obj)
        d.freeze(self.root / 'blind_a-received.json', obj)
        self.assertEqual(driver._load_phase('blind_a'), obj)
        obj['native']['final_message'] = '{"tampered":true}'
        (self.root / 'blind_a-received.json').write_text(json.dumps(obj))
        with self.assertRaisesRegex(ValueError, 'receipt hash'):
            driver._load_phase('blind_a')
    def test_evidence_metadata_mismatch_rejected(self):
        driver = object.__new__(d.Driver)
        driver.work = self.root
        driver.images = self.images
        driver.items = {'2017:law:問1': {'workId': 'a', 'evidence': [{'contentHash': 'a'*64}]}}
        driver._prior_identities = lambda _phase: []
        inp = {'fixed': 'input', 'outputSchema': d.object_schema({'results': d.object_schema({'a': d.object_schema({'evidence': {'type': 'array', 'items': d.object_schema({'contentHash': d.fixed_string('a'*64)})}})})})}
        inp['outputSchemaSha256'] = d.sha256_json(inp['outputSchema'])
        d.freeze(self.root / 'blind_a-input.json', inp)
        native = copy.deepcopy(self.native)
        native['final_message'] = json.dumps({'results': {'a': {'evidence': [{'contentHash': 'b'*64}]}}})
        obj = {'native': native, 'expectedIds': ['2017:law:問1'], 'expectedWorkIds': ['a'], 'inputHash': d.sha256_json(inp)}
        obj['receiptHash'] = d.sha256_json(obj)
        d.freeze(self.root / 'blind_a-received.json', obj)
        with self.assertRaisesRegex(ValueError, 'fixed metadata mismatch'):
            driver._reviews('blind_a')

if __name__ == '__main__':
    unittest.main()
