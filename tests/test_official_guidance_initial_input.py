"""Initial acquisition is private input, not an adopted institutional judgment."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_official_guidance_evidence as fixture
from tools.question_review_console import official_guidance_initial_input as initial
from tools.question_review_console import official_guidance_evidence as provider


class InitialGuidanceInputTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.OfficialGuidanceEvidenceTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.target = {'id': self.f.scope['questionId'], 'listGroupId': self.f.scope['listGroupId'],
            **self.f.scope['sourceBinding']}
        self.qual = self.f.scope['qualification']
        self.scope = deepcopy(self.f.scope)
        patched = patch.object(initial, 'source_scope', side_effect=lambda *a: deepcopy(self.scope))
        patched.start(); self.addCleanup(patched.stop)
        self.path = initial.input_path(self.f.root, self.qual, self.target)

    def register(self, **kwargs):
        return initial.register_initial_input(self.f.root, self.qual, self.target,
            self.f.record, self.f.request, resolver=self.f.resolver, **kwargs)

    def load(self, **kwargs):
        return initial.load_initial_input(self.f.root, self.qual, self.target,
            self.f.record, current_as_of=kwargs.pop('current_as_of', '2026-10-25'),
            resolver=self.f.resolver, **kwargs)

    def test_missing_input_does_not_fetch_or_create_directories(self):
        self.assertIsNone(self.load())
        self.assertFalse(self.path.parent.exists())
        self.assertEqual(self.f.calls, [])

    def test_actual_proof_is_loaded_without_inventing_applicability_or_patches(self):
        result = self.register()
        value = self.load()
        self.assertEqual(value['evidenceRef'], result['evidenceRef'])
        self.assertEqual(value['receipt']['applicabilityStatus'], 'unreviewed')
        self.assertEqual(value['receipt']['scope'], self.scope)
        self.assertNotIn('applicabilityByChoice', value)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertFalse((self.f.root / 'output' / self.qual / 'questions_json').exists())
        self.assertEqual(len(self.f.calls), 3)
        self.f.record['explanationText'] = ['後続工程の追加']
        self.assertEqual(self.load(), value)
        self.assertEqual(len(self.f.calls), 3)

    def test_body_choices_source_date_and_proof_tampering_are_rejected(self):
        self.register()
        record, scope, raw = deepcopy(self.f.record), deepcopy(self.scope), self.path.read_bytes()
        for kind in ('body', 'choices', 'source', 'date', 'request', 'proof', 'foreign-scope'):
            with self.subTest(kind=kind):
                self.f.record, self.scope = deepcopy(record), deepcopy(scope)
                self.path.write_bytes(raw)
                if kind == 'body': self.f.record['questionBodyText'] += '別命題'
                if kind == 'choices': self.f.record['choiceTextList'].reverse()
                if kind == 'source': self.scope['sourceSha256'] = 'b' * 64
                value = json.loads(raw)
                if kind == 'request': value['request']['currentPages'] = [8]
                if kind == 'proof': value['evidenceRef']['sha256'] = 'b' * 64
                if kind == 'foreign-scope': value['scope']['questionId'] = 'another'
                if kind in {'request', 'proof', 'foreign-scope'}:
                    self.path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    self.load(current_as_of='2026-10-26' if kind == 'date' else '2026-10-25')

    def test_replacement_requires_prior_hash_and_preserves_history(self):
        self.register(); raw = self.path.read_bytes(); count = len(self.f.calls)
        for expected in (None, 'b' * 64):
            with self.assertRaises(ValueError): self.register(replace_if_sha=expected)
            self.assertEqual(self.path.read_bytes(), raw)
            self.assertEqual(len(self.f.calls), count)
        self.f.resolver.now = lambda: '2026-10-25T09:12:13+00:00'
        self.register(replace_if_sha=hashlib.sha256(raw).hexdigest())
        history = self.path.parent / 'history' / (hashlib.sha256(raw).hexdigest() + '.json')
        self.assertEqual(history.read_bytes(), raw)
        self.assertNotEqual(self.path.read_bytes(), raw)
        self.assertEqual(self.load()['receipt']['retrievalCompletedAt'], '2026-10-25T09:12:13+00:00')

    def test_registration_detects_preserved_source_change_during_get(self):
        original = self.f.resolver.fetcher
        def fetch(*args):
            result = original(*args)
            self.scope['sourceSha256'] = 'b' * 64
            return result
        self.f.resolver.fetcher = fetch
        with self.assertRaises(ValueError): self.register()
        self.assertFalse(self.path.exists())

    def test_symlink_and_traversal_cannot_supply_an_input(self):
        self.path.parent.mkdir(parents=True)
        outside = self.f.root / 'outside.json'; outside.write_text('{}')
        self.path.symlink_to(outside)
        with self.assertRaises(ValueError): self.load()
        with self.assertRaises(ValueError): self.register()
        with self.assertRaises(ValueError):
            initial.input_path(self.f.root, '../outside', self.target)


class ForestPrinciplesProviderTests(unittest.TestCase):
    HTML = ('<h1>平成24年版 環境・循環型社会・生物多様性白書(PDF版)</h1>'
        '<h3><a href="pdf/4_term.pdf">語句説明 [PDF 1,000KB]</a></h3>').encode()

    def setUp(self):
        self.f = fixture.OfficialGuidanceEvidenceTests()
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.request = deepcopy(self.f.request)
        self.request.update(provider=provider.ENV_FOREST_REFERENCE_PROVIDER,
            examAsOf=None, currentPages=[11], historicalDocuments=[],
            choiceBindings=[{'choiceIndex': n, 'currentPages': [11], 'historicalSelections': []}
                for n in [0, 1]])
        self.index = provider.ENV_FOREST_REFERENCE_INDEX_URL
        self.pdf = 'https://www.env.go.jp/policy/hakusyo/h24/pdf/4_term.pdf'
        self.f.payloads[self.index] = provider.OfficialGuidanceFetch(self.HTML, self.index, 'text/html')
        self.f.payloads[self.pdf] = provider.OfficialGuidanceFetch(b'%PDF-glossary', self.pdf, 'application/pdf')

    def resolve(self):
        return self.f.resolver.resolve(self.request, record=self.f.record, scope=self.f.scope)

    def test_named_glossary_retains_historical_identity_and_unreviewed_applicability(self):
        receipt = self.resolve()
        self.assertEqual(receipt['sourceKind'], 'official_institutional_guidance')
        self.assertIsNone(receipt['currentIndex']['publishedRevisionDate'])
        self.assertIn('平成24年版', receipt['documents'][0]['revisionLabel'])
        self.assertIn('語句説明', receipt['documents'][0]['revisionLabel'])
        self.assertEqual(receipt['applicabilityStatus'], 'unreviewed')
        self.assertEqual(receipt['historicalCompleteness'], 'unreviewed')
        self.assertEqual([c[0] for c in self.f.calls], [self.index, self.pdf])
        self.assertEqual(self.f.resolver.validate(receipt, request=self.request,
            record=self.f.record, scope=self.f.scope, refresh=True), receipt)

    def test_changed_publication_ambiguous_label_external_or_other_pdf_is_rejected(self):
        for html in (self.HTML.replace('平成24年版'.encode(), '平成25年版'.encode()),
                self.HTML + self.HTML,
                self.HTML.replace(b'pdf/4_term.pdf', b'https://evil.example/4_term.pdf'),
                self.HTML.replace(b'pdf/4_term.pdf', b'pdf/other.pdf'),
                self.HTML.replace('語句説明'.encode(), '全体版'.encode()), b'\xff\xfe'):
            with self.subTest(digest=hashlib.sha256(html).hexdigest()):
                self.f.payloads[self.index] = provider.OfficialGuidanceFetch(html, self.index, 'text/html')
                with self.assertRaises(ValueError): self.resolve()
        self.assertTrue(all(call[0] == self.index for call in self.f.calls))


class PlasticStrategyProviderTests(unittest.TestCase):
    INDEX = ('<title>「プラスチック資源循環戦略」の策定について</title>'
        '<span>2019年05月31日</span>'
        '<a href="/content/900513722.pdf">プラスチック資源循環戦略 [PDF 272 KB]</a>').encode()

    def setUp(self):
        self.f = fixture.OfficialGuidanceEvidenceTests()
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.request = deepcopy(self.f.request)
        self.request.update(provider=provider.ENV_PLASTIC_STRATEGY_PROVIDER,
            examAsOf=None, currentPages=[4], historicalDocuments=[],
            choiceBindings=[{'choiceIndex': n, 'currentPages': [4], 'historicalSelections': []}
                for n in [0, 1]])
        self.index = provider.ENV_PLASTIC_STRATEGY_INDEX_URL
        self.pdf = 'https://www.env.go.jp/content/900513722.pdf'
        self.f.payloads[self.index] = provider.OfficialGuidanceFetch(self.INDEX, self.index, 'text/html')
        self.f.payloads[self.pdf] = provider.OfficialGuidanceFetch(b'%PDF-strategy', self.pdf, 'application/pdf')
        self.f.resolver.extractor = lambda data, pages: [
            {'page': page, 'text': ('プラスチック資源循環戦略 令和元年５月 31 日'
                if page == 1 else '２．基本原則 ― ３Ｒ + Renewable （持続可能な資源）')}
            for page in pages]

    def test_named_strategy_is_acquired_and_bound_to_all_choices(self):
        receipt = self.f.resolver.resolve(self.request, record=self.f.record, scope=self.f.scope)
        self.assertEqual(receipt['currentIndex']['publishedRevisionDate'], '2019-05-31')
        self.assertEqual(receipt['documents'][0]['sourceUrl'], self.pdf)
        self.assertEqual(receipt['choiceBindings'], self.request['choiceBindings'])
        self.assertEqual(self.f.resolver.validate(receipt, request=self.request,
            record=self.f.record, scope=self.f.scope), receipt)

    def test_other_strategy_announcement_or_pdf_is_rejected(self):
        for index in (self.INDEX.replace(b'2019', b'2020'),
                self.INDEX.replace(b'900513722', b'900513721'),
                self.INDEX + self.INDEX, b'\xff\xfe'):
            with self.subTest(index=index[:30]):
                self.f.payloads[self.index] = provider.OfficialGuidanceFetch(index, self.index, 'text/html')
                with self.assertRaises(ValueError):
                    self.f.resolver.resolve(self.request, record=self.f.record, scope=self.f.scope)
        self.f.payloads[self.index] = provider.OfficialGuidanceFetch(self.INDEX, self.index, 'text/html')
        self.f.resolver.extractor = lambda data, pages: [
            {'page': page, 'text': '別のプラスチック資料'} for page in pages]
        with self.assertRaises(ValueError):
            self.f.resolver.resolve(self.request, record=self.f.record, scope=self.f.scope)
