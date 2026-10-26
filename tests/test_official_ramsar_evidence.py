"""Ramsar's Japanese text must reach the normal private evidence path."""
from copy import deepcopy
from dataclasses import replace
import json
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


PDF_URL = ('https://www.env.go.jp/nature/ramsar/conv/treaty/'
    'RamsarConventionText_JP_rev171222.pdf')
INDEX = ('<h1>ラムサール条約とは</h1><h2>条約の内容</h2>'
    '<a href="./treaty/RamsarConventionText_JP_rev171222.pdf">'
    'ラムサール条約全文の日本語版[PDF 150KB]</a>'
    '<a href="./treaty/RamsarConventionText_EN.pdf">'
    'ラムサール条約全文の英語版 [PDF 85KB]</a>'
    '<h2>別の資料</h2><a href="/content/123.pdf">条約の概要</a>').encode()


class OfficialRamsarEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.record = {'questionBodyText': '国際的に重要な湿地の登録を定める条約を選ぶ。',
            'choiceTextList': ['ラムサール条約', '別の条約']}
        self.scope = {'qualification': 'eco-kentei', 'listGroupId': 'group',
            'questionId': 'ui-id', 'sourceSha256': 'a' * 64,
            'sourceBinding': {'sourceQuestionKey': 'eco-kentei:group:source-id',
                'reviewQuestionId': 'source-id', 'sourceRecordRef': 'question.json#0'}}
        self.request = {'schemaVersion': evidence.REQUEST_SCHEMA,
            'provider': evidence.ENV_RAMSAR_PROVIDER, 'currentAsOf': '2026-10-27',
            'examAsOf': None, 'currentPages': [1, 2], 'historicalDocuments': [],
            'choiceBindings': [
                {'choiceIndex': 0, 'currentPages': [1, 2], 'historicalSelections': []},
                {'choiceIndex': 1, 'currentPages': [1, 2], 'historicalSelections': []}]}
        self.payloads = {
            evidence.ENV_RAMSAR_INDEX_URL: evidence.OfficialGuidanceFetch(
                INDEX, evidence.ENV_RAMSAR_INDEX_URL, 'text/html'),
            PDF_URL: evidence.OfficialGuidanceFetch(b'%PDF-japanese-treaty', PDF_URL, 'application/pdf')}
        self.calls = []

        def fetch(url, maximum):
            self.calls.append(url)
            return self.payloads[url]

        self.resolver = evidence.OfficialGuidanceEvidenceResolver(self.root, fetcher=fetch,
            extractor=lambda data, pages: [{'page': p, 'text': data.decode() + str(p)} for p in pages],
            now=lambda: '2026-10-26T21:00:00+00:00')

    def resolve(self):
        return self.resolver.resolve(self.request, record=self.record, scope=self.scope)

    def test_named_japanese_text_is_acquired_and_replayed_without_inventing_revision_date(self):
        receipt = self.resolve()
        self.assertEqual(self.calls, [evidence.ENV_RAMSAR_INDEX_URL, PDF_URL])
        self.assertEqual(receipt['sourceKind'], 'official_treaty_text')
        self.assertEqual(receipt['documents'][0]['sourceUrl'], PDF_URL)
        self.assertIsNone(receipt['currentIndex']['publishedRevisionDate'])
        self.assertIsNone(receipt['examAsOf'])
        self.assertEqual(receipt['applicabilityStatus'], 'unreviewed')
        self.assertEqual(receipt['historicalCompleteness'], 'unreviewed')
        self.assertEqual(self.resolver.validate(receipt, request=self.request,
            record=self.record, scope=self.scope), receipt)

    def test_missing_duplicate_foreign_or_wrong_section_text_link_stops_before_pdf_fetch(self):
        for html in (
                INDEX.replace('日本語版'.encode(), '英語版'.encode()),
                INDEX.replace('条約の内容'.encode(), '別の内容'.encode()),
                INDEX.replace('<h2>別の資料</h2>'.encode(),
                    '<a href="./treaty/Another.pdf">ラムサール条約全文の日本語版[PDF 12KB]</a><h2>別の資料</h2>'.encode()),
                INDEX + '<h2>条約の内容</h2>'.encode(),
                INDEX.replace(b'./treaty/RamsarConventionText_JP_rev171222.pdf',
                    b'https://evil.example/treaty/JP.pdf'),
                INDEX.replace(b'./treaty/RamsarConventionText_JP_rev171222.pdf', b'/content/123.pdf'),
                b'\xff\xfe'):
            with self.subTest(index_hash=evidence._hash(html)):
                self.payloads[evidence.ENV_RAMSAR_INDEX_URL] = evidence.OfficialGuidanceFetch(
                    html, evidence.ENV_RAMSAR_INDEX_URL, 'text/html')
                before = len(self.calls)
                with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
                    self.resolve()
                self.assertEqual(self.calls[before:], [evidence.ENV_RAMSAR_INDEX_URL])

    def test_provider_redirects_cannot_substitute_another_treaty_or_publisher(self):
        redirects = evidence._OfficialRedirects(evidence.ENV_RAMSAR_PROVIDER)
        for url in ('https://www.env.go.jp/content/900444088.pdf',
                'https://other.example/nature/ramsar/conv/treaty/JP.pdf',
                PDF_URL + '?access_token=x'):
            with self.subTest(url=url):
                with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
                    redirects.redirect_request(Request(PDF_URL), None, 302, 'redirect', {}, url)
        self.payloads[PDF_URL] = replace(self.payloads[PDF_URL],
            resolved_url='https://www.env.go.jp/content/900444088.pdf')
        with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
            self.resolve()

    def test_changed_pdf_or_question_cannot_reuse_the_prepared_receipt(self):
        receipt = self.resolve()
        other = deepcopy(self.record)
        other['choiceTextList'].reverse()
        with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
            self.resolver.validate(receipt, request=self.request, record=other, scope=self.scope)
        self.payloads[PDF_URL] = replace(self.payloads[PDF_URL], data=b'%PDF-revised')
        with self.assertRaisesRegex(evidence.OfficialGuidanceEvidenceError, 'new audit required'):
            self.resolver.refresh(receipt, request=self.request, record=self.record, scope=self.scope)
        self.assertIsNone(self.resolver.last_refresh_receipt)

    def test_normal_prompt_and_law_context_gate_receive_real_source_bound_receipt(self):
        with patch.object(contract, 'source_scope', return_value=self.scope), \
                patch.object(contract, 'OfficialGuidanceEvidenceResolver', return_value=self.resolver):
            delivered = contract.prepare_evidence(self.root, self.request,
                record=self.record, scope=self.scope)
            refs = {k: delivered[k] for k in ('request', 'evidenceRef')}
            refs['applicabilityByChoice'] = [
                {'choiceIndex': 0, 'currentBasis': '第2条の湿地登録に対応する。',
                    'examTimeBasis': '出題日が不明なため適用時点は独立監査で確認する。'},
                {'choiceIndex': 1, 'currentBasis': '本文へ別の条約を補った命題は第2条と対応しない。',
                    'examTimeBasis': '出題日が不明なため適用時点は独立監査で確認する。'}]
            record = {**self.record, 'isLawRelated': True, 'lawGroundedExplanationNotNeeded': False,
                'lawReferences': [[], []], contract.FIELD: refs}
            view = runs._law_context_evidence_view(self.root, record,
                {'status': 'not_applicable', 'items': []})
            self.assertEqual(view['officialGuidance']['provider'], evidence.ENV_RAMSAR_PROVIDER)
            qid = self.scope['questionId']
            target = CandidateTarget('law', 'law_context', '18_law_context_prepared/q.json',
                ('isLawRelated', 'lawGroundedExplanationNotNeeded', 'lawReferences', contract.FIELD))
            raw = {'id': qid, 'listGroupId': self.scope['listGroupId'], **self.scope['sourceBinding']}
            prompt = runs._structured_candidate_prompt('各命題と条文を独立に確認する。', [raw],
                stage_id='law_context', records_by_question={qid: record},
                candidate_targets_by_question={qid: (target,)}, feedback_by_question={},
                official_guidance_evidence_by_question={qid: delivered})
            self.assertEqual(audit.question_inputs_from_prompt(prompt)[qid]['officialGuidanceEvidence'], delivered)
            schema = output_schema([qid], {qid: (target,)}, official_guidance_inputs={qid: delivered})
            serialized = json.dumps(schema, ensure_ascii=False)
            self.assertIn(evidence.ENV_RAMSAR_PROVIDER, serialized)
            self.assertIn(delivered['evidenceRef']['sha256'], serialized)


if __name__ == '__main__':
    unittest.main()
