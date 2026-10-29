"""Public factual sources retain source bytes and normal native delivery proof."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tools.question_review_console import supplemental_primary_evidence as evidence
from tools.question_review_console import law_audit_verification as law
from tests import test_official_guidance_evidence as fixture


HTML = b'<html><div id="main_content"><h1>Official policy</h1>Four business categories, not households.</div></html>'
PARIS_HTML = ('<html><body><p>2015年12月12日、COP21でパリ協定が採択された。</p>'
    '<p>他の文章</p></body></html>').encode('utf-8')


class SupplementalEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.OfficialGuidanceEvidenceTests(); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.scope = deepcopy(self.f.scope)
        self.target = {'id': self.scope['questionId'], 'listGroupId': self.scope['listGroupId'],
            **self.scope['sourceBinding']}
        self.qual = self.scope['qualification']
        self.record = deepcopy(self.f.record)
        p = patch.object(evidence, 'source_scope', side_effect=lambda *_: deepcopy(self.scope))
        p.start(); self.addCleanup(p.stop)
        self.ids = ['maff_food_recycling_targets']
        self.path = evidence._path(self.f.root, self.scope)

    def register(self, **kwargs):
        return evidence.register(self.f.root, self.qual, self.target, self.record, self.ids,
            fetcher=lambda url: (HTML, 'text/html'), **kwargs)

    def load(self, **kwargs):
        day = evidence._reference_date(evidence.datetime.now(evidence.timezone.utc).isoformat())
        return evidence.load(self.f.root, self.qual, self.target, self.record,
            current_as_of=kwargs.pop('current_as_of', day), **kwargs)

    def test_absent_input_does_not_fetch_or_change_any_question(self):
        self.assertIsNone(self.load())
        self.assertFalse(self.path.parent.exists())

    def test_paris_adoption_selector_and_scope_binding_reject_tampering(self):
        self.ids = ['env_paris_agreement_adoption_2015']
        source = evidence.SOURCES[self.ids[0]]
        result = evidence.register(self.f.root, self.qual, self.target, self.record, self.ids,
            fetcher=lambda url: (PARIS_HTML, 'text/html'))
        value = self.load(); doc = value['documents'][0]
        self.assertEqual(result['inputHash'], value['inputHash'])
        self.assertEqual(doc['sourceUrl'], source.url)
        self.assertEqual(doc['extraction']['selector'], source.region)
        self.assertIn('2015年12月12日', doc['spans'][0]['text'])
        self.assertIn('パリ協定が採択', doc['spans'][0]['text'])
        self.assertIn('2017年版', doc['temporalScope'])
        self.assertEqual(value['applicabilityStatus'], 'unreviewed')
        for kind in ('url', 'text'):
            with self.subTest(kind=kind):
                changed = deepcopy(value)
                if kind == 'url': changed['documents'][0]['sourceUrl'] = 'https://example.test/other'
                else: changed['documents'][0]['spans'][0]['text'] += ' 改変'
                changed['inputHash'] = evidence._hash(evidence._bytes(
                    {k: v for k, v in changed.items() if k != 'inputHash'}))
                with self.assertRaises(ValueError):
                    evidence.validate(self.f.root, changed, record=self.record, question_id=self.target['id'])
        with self.assertRaises(ValueError):
            evidence.validate(self.f.root, value, record=self.record, question_id='other-question')
        self.record['questionBodyText'] += ' 他の問題'
        with self.assertRaises(ValueError): self.load()
        with self.assertRaises(ValueError): evidence._fetch('https://example.test/other')
        for raw in (b'<html><body><p>different body</p></body></html>', PARIS_HTML.replace(b'</body>', b'<p>2015\xe5\xb9\xb412\xe6\x9c\x8812\xe6\x97\xa5 \xe3\x83\x91\xe3\x83\xaa\xe5\x8d\x94\xe5\xae\x9a \xe6\x8e\xa1\xe6\x8a\x9e</p></body>')):
            with self.assertRaises(ValueError): evidence._extract_source(self.ids[0], raw, 'text/html')

    def test_raw_html_text_and_unreviewed_binding_survive_without_adopting_a_verdict(self):
        result = self.register(); value = self.load()
        self.assertEqual(value['inputHash'], result['inputHash'])
        self.assertEqual(value['applicabilityStatus'], 'unreviewed')
        self.assertIn('Four business categories', value['documents'][0]['spans'][0]['text'])
        self.assertEqual(evidence._raw(self.f.root, value['documents'][0]['rawRef']), HTML)
        self.assertNotIn('isLawRelated', value)
        self.assertFalse((self.f.root / 'output' / self.qual / 'questions_json').exists())
        self.record['explanationText'] = ['later stage']
        self.assertEqual(value, self.load())

    def test_whole_body_every_choice_preserved_source_and_date_are_checked(self):
        self.register(); original_record = deepcopy(self.record); original_scope = deepcopy(self.scope)
        for kind in ['body', 'last-choice', 'source', 'date']:
            with self.subTest(kind=kind):
                self.record = deepcopy(original_record); self.scope = deepcopy(original_scope)
                if kind == 'body': self.record['questionBodyText'] += 'changed'
                if kind == 'last-choice': self.record['choiceTextList'][-1] += 'changed'
                if kind == 'source': self.scope['sourceSha256'] = 'b' * 64
                with self.assertRaises(ValueError):
                    self.load(current_as_of='2000-01-01') if kind == 'date' else self.load()

    def test_tampered_raw_bytes_or_text_even_with_recomputed_self_hash_are_rejected(self):
        self.register(); value = self.load()
        changed = deepcopy(value); changed['documents'][0]['spans'][0]['text'] = 'invented'
        changed['inputHash'] = evidence._hash(evidence._bytes({k: v for k, v in changed.items() if k != 'inputHash'}))
        with self.assertRaises(ValueError): evidence.validate(self.f.root, changed)
        (self.f.root / value['documents'][0]['rawRef']['ref']).write_bytes(b'changed')
        with self.assertRaises(ValueError): self.load()

    def test_replacement_is_explicit_and_invalidates_pending_delivery(self):
        first = self.register(); old = self.load()
        for sha in [None, 'b' * 64]:
            with self.assertRaises(ValueError): self.register(replace_if_sha=sha)
        self.register(replace_if_sha=first['sha256'])
        with self.assertRaises(ValueError): evidence.validate(self.f.root, old, require_selected=True)

    def test_actual_prompt_and_native_delivery_cannot_be_omitted_or_reassigned(self):
        self.register(); value = self.load(); qid = self.target['id']
        inputs = {qid: value}; questions = {qid: {evidence.FIELD: value}}
        receipt = evidence.delivery(self.f.root, inputs, 'actual prompt', questions, thread_id='thread', turn_id='turn')
        self.assertEqual(receipt, evidence.delivery(self.f.root, inputs, 'actual prompt', questions, receipt))
        with self.assertRaises(ValueError): evidence.delivery(self.f.root, inputs, 'actual prompt', {qid: {}}, receipt)
        with self.assertRaises(ValueError): evidence.delivery(self.f.root, inputs, 'different prompt', questions, receipt)
        with self.assertRaises(ValueError): evidence.validate(self.f.root, value, question_id='foreign')

    def test_pdf_pages_and_missing_html_region_are_not_silently_replaced(self):
        with self.assertRaises(ValueError): evidence._extract_source(self.ids[0], b'<html>no region</html>', 'text/html')
        source_id = 'env_construction_recycling_2008_review'
        with self.assertRaises(ValueError): evidence._extract_source(source_id, HTML, 'application/pdf')
        with patch.object(evidence, '_extract', return_value=[{'page': 1, 'text': ''}]):
            with self.assertRaises(ValueError): evidence._extract_source(source_id, b'%PDF fixture', 'application/pdf')
        with patch.object(evidence, '_extract', return_value=[{'page': n, 'text': 'historical evidence'} for n in [1, 4, 7]]):
            doc = evidence._extract_source(source_id, b'%PDF fixture', 'application/pdf')
            self.assertEqual(doc['extraction']['pages'], [1, 4, 7])
            self.assertIn('2008', doc['temporalScope'])

    def test_oversized_complete_text_is_rejected_not_truncated(self):
        raw = b'<div id="main_content">' + b'a' * 49_000 + b'</div>'
        with self.assertRaises(ValueError): evidence._extract_source(self.ids[0], raw, 'text/html')

    def test_foreign_catalog_urls_symlinks_and_source_changes_during_fetch_fail(self):
        with self.assertRaises(ValueError): evidence._fetch('https://example.test/private')
        self.path.parent.mkdir(parents=True); outside = self.f.root / 'outside.json'; outside.write_text('{}')
        self.path.symlink_to(outside)
        with self.assertRaises(ValueError): self.load()
        self.path.unlink()
        def fetch(url):
            self.scope['sourceSha256'] = 'c' * 64
            return HTML, 'text/html'
        with self.assertRaises(ValueError): evidence.register(self.f.root, self.qual, self.target,
            self.record, self.ids, fetcher=fetch)
        self.assertFalse(self.path.exists())

    def test_independent_law_bundle_rechecks_actual_supplemental_input(self):
        self.register(); value = self.load()
        bundle = {'question': {'questionId': self.target['id'], 'currentRecord': self.record,
            evidence.FIELD: value}, 'proposedFields': {}}
        bundle['evidenceHash'] = law._json_hash(bundle)
        law.validate_verification_bundle(bundle, repo_root=self.f.root, expected_supplemental=value)
        removed = deepcopy(bundle); removed['question'].pop(evidence.FIELD)
        removed['evidenceHash'] = law._json_hash({k: v for k, v in removed.items() if k != 'evidenceHash'})
        with self.assertRaises(ValueError):
            law.validate_verification_bundle(removed, repo_root=self.f.root, expected_supplemental=value)
        (self.f.root / value['documents'][0]['rawRef']['ref']).write_bytes(b'changed')
        with self.assertRaises(ValueError): law.validate_verification_bundle(bundle, repo_root=self.f.root)


class SupplementalNativeCycleTests(unittest.TestCase):
    from tests.test_law_context_primary_cycle import LawContextPrimaryCycleTests as Flow
    write = staticmethod(Flow.write)
    prepare = Flow.prepare
    plan = Flow.plan
    apply = Flow.apply

    def setUp(self):
        self.Flow.setUp(self)
        from tools.question_bank import feedback_prepared_stage as stage
        from tools.question_review_console.inventory import QuestionInventory
        source = Path(self.request['source']['ref'])
        data = json.loads(source.read_text())
        binding = self.request['source']['binding']
        binding['sourceQuestionKey'] = 'new-exam:2026:' + binding['reviewQuestionId']
        data['question_bodies'][0]['sourceQuestionKey'] = binding['sourceQuestionKey']
        self.write(source, data)
        self.request['source']['sha256'] = stage._hash(source.read_bytes())
        self.write(self.root / stage.MANIFEST, (json.dumps({'path': str(source.relative_to(self.root)),
            'sha256': self.request['source']['sha256']}) + '\n').encode())
        reader = QuestionInventory(self.root)
        self.inventory.group = reader.group
        self.target = self.inventory.group('new-exam', '2026')['questions'][0]
        self.request['questionId'] = self.target['id']
        self.write(self.request_path, self.request)
        evidence.register(self.root, 'new-exam', self.target, self.target['projected'],
            ['maff_food_recycling_targets'], fetcher=lambda url: (HTML, 'text/html'))

    def test_initial_and_regenerated_prompts_then_save_bind_identical_acquired_text(self):
        from tests.qualification_run_test_support import PerQuestionQueueAppServer
        from tools.question_bank import feedback_prepared_stage as stage
        self.prepare(); plan = self.plan()
        questions = [PerQuestionQueueAppServer._candidate_questions(call[0])[0] for call in self.model.calls]
        self.assertEqual(len(questions), 2)
        self.assertEqual(questions[0][evidence.FIELD], questions[1][evidence.FIELD])
        metadata = plan['preparedCandidate']['content']['executionMetadata']
        self.assertTrue(metadata['supplementalPrimaryDelivery']['threadId'])
        rounds = metadata['lawContextPrimaryEvidence'][self.request['questionId']]['rounds']
        self.assertEqual(rounds[0]['execution']['supplementalPrimaryInputHash'], questions[0][evidence.FIELD]['inputHash'])
        with patch.object(stage, 'verify_independent_approval', return_value={'reviewerSessionId': 'independent'}):
            self.assertTrue(self.apply()['formalPatchSaved'])

    def test_changed_acquired_source_after_preparation_blocks_formal_save(self):
        from tools.question_bank import feedback_prepared_stage as stage
        self.prepare(); plan = self.plan()
        value = plan['preparedCandidate']['content']['executionMetadata']
        self.assertIn('supplementalPrimaryDelivery', value)
        cache = self.root / evidence.CACHE
        next(cache.glob('*.raw')).write_bytes(b'changed')
        with patch.object(stage, 'verify_independent_approval', return_value={'reviewerSessionId': 'independent'}):
            with self.assertRaises(ValueError): self.apply()
