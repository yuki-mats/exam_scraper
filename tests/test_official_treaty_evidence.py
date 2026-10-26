"""Treaty acquisition must retain the same source, delivery and refresh gates."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request

from tools.question_review_console import official_guidance_evidence as evidence
from tools.question_review_console import official_guidance_contract as contract
from tools.question_review_console import qualification_runs as runs
from tools.question_review_console import law_audit_verification as audit
from tools.question_review_console.question_candidate import CandidateTarget, output_schema


PDF_URL = 'https://www.env.go.jp/content/900444088.pdf'
INDEX = ('<h4>京都議定書に関する基礎資料</h4><a href="/content/1.pdf">別の資料</a>'
    '<h4 id="03">パリ協定に関する基礎資料</h4>'
    '<a href="/content/900444088.pdf">パリ協定（和訳）</a>'
    '<a href="/content/900440463.pdf">パリ協定の概要</a>'
    '<h4>その他の資料</h4>').encode()


class OfficialTreatyEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.record = {'questionBodyText': '協定には緩和策と適応策の双方が定められている。',
            'choiceTextList': ['正しい', '誤っている']}
        self.scope = {'qualification': 'eco-kentei', 'listGroupId': 'knoun-2026-mock-1',
            'questionId': 'ui-id', 'sourceSha256': 'a' * 64,
            'sourceBinding': {'sourceQuestionKey': 'eco-kentei:knoun-2026-mock-1:source-id',
                'reviewQuestionId': 'source-id', 'sourceRecordRef': 'question_mondai1_4.json#0'}}
        self.request = {'schemaVersion': evidence.REQUEST_SCHEMA, 'provider': evidence.ENV_PARIS_PROVIDER,
            'currentAsOf': '2026-10-27', 'examAsOf': None, 'currentPages': [6, 14],
            'historicalDocuments': [], 'choiceBindings': [
                {'choiceIndex': index, 'currentPages': [6, 14], 'historicalSelections': []}
                for index in range(2)]}
        self.payloads = {
            evidence.ENV_PARIS_INDEX_URL: evidence.OfficialGuidanceFetch(INDEX, evidence.ENV_PARIS_INDEX_URL, 'text/html'),
            PDF_URL: evidence.OfficialGuidanceFetch(b'%PDF-treaty', PDF_URL, 'application/pdf')}
        self.calls = []
        def fetch(url, maximum):
            self.calls.append(url)
            return self.payloads[url]
        self.resolver = evidence.OfficialGuidanceEvidenceResolver(self.root, fetcher=fetch,
            extractor=lambda data, pages: [{'page': p, 'text': data.decode() + ' article page ' + str(p)} for p in pages],
            now=lambda: '2026-10-27T09:00:00+00:00')

    def resolve(self):
        return self.resolver.resolve(self.request, record=self.record, scope=self.scope)

    def validate(self, receipt):
        return self.resolver.validate(receipt, request=self.request, record=self.record, scope=self.scope)

    def test_treaty_text_is_acquired_without_fabricating_revision_or_applicability(self):
        receipt = self.resolve()
        self.assertEqual(receipt['sourceKind'], 'official_treaty_text')
        self.assertEqual(receipt['documents'][0]['sourceUrl'], PDF_URL)
        self.assertEqual(receipt['documents'][0]['revisionLabelOrigin'], 'actual_official_index_link')
        self.assertIsNone(receipt['currentIndex']['publishedRevisionDate'])
        self.assertIsNone(receipt['examAsOf'])
        self.assertEqual(receipt['historicalCompleteness'], 'unreviewed')
        self.assertEqual(receipt['applicabilityStatus'], 'unreviewed')
        self.assertEqual(self.validate(receipt), receipt)
        self.assertEqual(self.calls, [evidence.ENV_PARIS_INDEX_URL, PDF_URL])
        changed = deepcopy(self.record); changed['choiceTextList'].reverse()
        with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
            self.resolver.validate(receipt, request=self.request, record=changed, scope=self.scope)

    def test_exact_unique_section_and_text_link_are_required(self):
        variants = [
            INDEX.replace('パリ協定（和訳）'.encode(), 'パリ協定の概要'.encode()),
            INDEX.replace('パリ協定に関する基礎資料'.encode(), '別の協定'.encode()),
            INDEX + '<h4>パリ協定に関する基礎資料</h4>'.encode(),
            INDEX.replace(b'<h4>\xe3\x81\x9d', b'<a href="/content/2.pdf">' + 'パリ協定（和訳）'.encode() + b'</a><h4>\xe3\x81\x9d'),
            INDEX.replace(b'/content/900444088.pdf', b'https://www.mlit.go.jp/content/900444088.pdf'),
            b'\xff\xfe',
        ]
        for html in variants:
            with self.subTest(index_hash=evidence._hash(html)):
                self.payloads[evidence.ENV_PARIS_INDEX_URL] = evidence.OfficialGuidanceFetch(
                    html, evidence.ENV_PARIS_INDEX_URL, 'text/html')
                before = len(self.calls)
                with self.assertRaises(evidence.OfficialGuidanceEvidenceError): self.resolve()
                self.assertEqual(self.calls[before:], [evidence.ENV_PARIS_INDEX_URL])

    def test_provider_isolation_applies_before_fetch_and_on_redirect_response(self):
        for url in ['https://www.mlit.go.jp/content/123.pdf',
                'https://www.env.go.jp.evil.example/content/123.pdf',
                'https://user@www.env.go.jp/content/123.pdf',
                'https://www.env.go.jp:443/content/123.pdf',
                'https://www.env.go.jp/content/123.pdf?token=x',
                'https://www.env.go.jp/content/../123.pdf']:
            with self.subTest(url=url):
                with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
                    evidence._official_url(url, pdf=True, provider=evidence.ENV_PARIS_PROVIDER)
        with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
            evidence._OfficialRedirects(evidence.ENV_PARIS_PROVIDER).redirect_request(
                Request(PDF_URL), None, 302, 'redirect', {}, 'https://www.mlit.go.jp/content/123.pdf')
        self.payloads[PDF_URL] = replace(self.payloads[PDF_URL], resolved_url='https://www.mlit.go.jp/content/123.pdf')
        with self.assertRaises(evidence.OfficialGuidanceEvidenceError): self.resolve()
        self.request['provider'] = 'arbitrary-url-provider'
        before = len(self.calls)
        with self.assertRaises(evidence.OfficialGuidanceEvidenceError): self.resolve()
        self.assertEqual(len(self.calls), before)

    def test_changed_official_pdf_requires_new_independent_review(self):
        receipt = self.resolve()
        self.payloads[PDF_URL] = replace(self.payloads[PDF_URL], data=b'%PDF-changed-treaty')
        with self.assertRaisesRegex(evidence.OfficialGuidanceEvidenceError, 'new audit required'):
            self.resolver.refresh(receipt, request=self.request, record=self.record, scope=self.scope)
        self.assertIsNone(self.resolver.last_refresh_receipt)
        self.assertEqual(self.validate(receipt), receipt)  # Old observation remains an old observation.

    def test_native_japanese_reference_date_accepts_utc_timestamp_before_utc_midnight(self):
        self.resolver.now = lambda: '2026-10-26T16:31:00+00:00'
        receipt = self.resolve()
        self.assertEqual(receipt['currentAsOf'], '2026-10-27')
        self.assertEqual(self.validate(receipt), receipt)
        self.resolver.now = lambda: '2026-10-27T01:31:00+09:00'
        self.assertEqual(self.resolve()['contentHash'], receipt['contentHash'])
        self.request['currentAsOf'] = '2026-10-26'
        before = len(self.calls)
        with self.assertRaisesRegex(evidence.OfficialGuidanceEvidenceError, 'actual current date'):
            self.resolve()
        self.assertEqual(len(self.calls), before)

    def test_treaty_receipt_reaches_normal_prompt_and_gate_with_no_fake_law_id(self):
        with patch.object(contract, 'source_scope', return_value=self.scope), \
                patch.object(contract, 'OfficialGuidanceEvidenceResolver', return_value=self.resolver):
            delivered = contract.prepare_evidence(self.root, self.request, record=self.record, scope=self.scope)
            refs = {k: delivered[k] for k in ('request', 'evidenceRef')}
            refs['applicabilityByChoice'] = [{'choiceIndex': index, 'currentBasis': '第4条と第7条を完全命題と照合した。',
                'examTimeBasis': '出題時点の適用性は独立監査で確認する。'} for index in range(2)]
            record = {**self.record, 'isLawRelated': True, 'lawGroundedExplanationNotNeeded': False,
                'lawReferences': [[], []], contract.FIELD: refs}
            empty = {'status': 'not_applicable', 'items': []}
            view = runs._law_context_evidence_view(self.root, record, empty)
            self.assertEqual(view['officialGuidance']['sourceKind'], 'official_treaty_text')
            qid = self.scope['questionId']
            target = CandidateTarget('law', 'law_context', '18_law_context_prepared/q.json',
                ('isLawRelated', 'lawGroundedExplanationNotNeeded', 'lawReferences', contract.FIELD))
            raw = {'id': qid, 'listGroupId': self.scope['listGroupId'], **self.scope['sourceBinding']}
            prompt = runs._structured_candidate_prompt('本文を独立に確認する。', [raw], stage_id='law_context',
                records_by_question={qid: record}, candidate_targets_by_question={qid: (target,)},
                feedback_by_question={}, official_guidance_evidence_by_question={qid: delivered})
            self.assertEqual(audit.question_inputs_from_prompt(prompt)[qid]['officialGuidanceEvidence'], delivered)
            self.assertTrue(output_schema([qid], {qid: (target,)}, official_guidance_inputs={qid: delivered}))
            mixed = {**record, 'lawReferences': [[{'lawId': 'not-acquired', 'article': '1'}], []]}
            with self.assertRaises(runs.QualificationRunError):
                runs._law_context_evidence_view(self.root, mixed, empty)

