"""Bind the official Japanese protocol row, not a nearby protocol or PDF."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request

from tools.question_review_console import official_guidance_contract as contract
from tools.question_review_console import official_guidance_evidence as evidence
from tools.question_review_console import law_audit_verification as audit
from tools.question_review_console import qualification_runs as runs
from tools.question_review_console.question_candidate import CandidateTarget, output_schema


PDF_URL = 'https://www.biodic.go.jp/bch/download/protocol/protocol_j.pdf'
INDEX_TEXT = ('''<meta charset="Shift_JIS"><table><tr><td>① 関連情報
<table><tr><td>○ 議定書の概要</td><td><a href="download/protocol/protocol_outline.pdf"><img></a></td></tr>
<tr><td><a href="javascript:page_open('gitei2.html','gitei2')">○ 議定書全文（和文）</a></td>
<td><a href="download/protocol/protocol_j.pdf" onclick="ignored()"><img src="pdf.gif"></a></td></tr>
<tr><td>○ 議定書全文（英文）</td><td><a href="download/protocol/protocol_en.pdf"><img></a></td></tr>
<tr><td>○ 補足議定書全文（和文）</td><td><a href="download/protocol/supplementary%20protocol_j.pdf"><img></a></td></tr>
</table></td></tr></table>''')
INDEX = INDEX_TEXT.encode('cp932')


class OfficialCartagenaEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.record = {'questionBodyText': '遺伝子組換え生物の越境移動を扱う議定書を選ぶ。',
            'choiceTextList': ['カルタヘナ議定書', '別の議定書']}
        self.scope = {'qualification': 'eco-kentei', 'listGroupId': 'group',
            'questionId': 'ui-id', 'sourceSha256': 'a' * 64,
            'sourceBinding': {'sourceQuestionKey': 'eco-kentei:group:source-id',
                'reviewQuestionId': 'source-id', 'sourceRecordRef': 'question.json#0'}}
        self.request = {'schemaVersion': evidence.REQUEST_SCHEMA,
            'provider': evidence.BIODIC_CARTAGENA_PROVIDER, 'currentAsOf': '2026-10-27',
            'examAsOf': None, 'currentPages': [3, 4], 'historicalDocuments': [],
            'choiceBindings': [
                {'choiceIndex': 0, 'currentPages': [3, 4], 'historicalSelections': []},
                {'choiceIndex': 1, 'currentPages': [3, 4], 'historicalSelections': []}]}
        self.payloads = {
            evidence.BIODIC_CARTAGENA_INDEX_URL: evidence.OfficialGuidanceFetch(
                INDEX, evidence.BIODIC_CARTAGENA_INDEX_URL, 'text/html'),
            PDF_URL: evidence.OfficialGuidanceFetch(b'%PDF-japanese-protocol', PDF_URL, 'application/pdf')}
        self.calls = []

        def fetch(url, maximum):
            self.calls.append(url)
            return self.payloads[url]

        self.resolver = evidence.OfficialGuidanceEvidenceResolver(self.root, fetcher=fetch,
            extractor=lambda data, pages: [{'page': p, 'text': data.decode() + str(p)} for p in pages],
            now=lambda: '2026-10-26T21:00:00+00:00')

    def resolve(self):
        return self.resolver.resolve(self.request, record=self.record, scope=self.scope)

    def test_named_row_selects_japanese_full_text_and_retains_unreviewed_applicability(self):
        receipt = self.resolve()
        self.assertEqual(self.calls, [evidence.BIODIC_CARTAGENA_INDEX_URL, PDF_URL])
        self.assertEqual(receipt['sourceKind'], 'official_treaty_text')
        self.assertEqual(receipt['documents'][0]['revisionLabel'], '議定書全文（和文）')
        self.assertIsNone(receipt['currentIndex']['publishedRevisionDate'])
        self.assertEqual(receipt['applicabilityStatus'], 'unreviewed')
        self.assertEqual(receipt['historicalCompleteness'], 'unreviewed')
        self.assertEqual(self.resolver.validate(receipt, request=self.request,
            record=self.record, scope=self.scope), receipt)

    def test_missing_duplicate_wrong_language_or_wrong_row_link_fails_before_pdf_fetch(self):
        own_row = ('<tr><td>○ 議定書全文（和文）</td><td>'
            '<a href="download/protocol/protocol_j.pdf"><img></a></td></tr>')
        variants = [
            INDEX_TEXT.replace('○ 議定書全文（和文）', '○ 補足議定書全文（和文）').encode('cp932'),
            (INDEX_TEXT + own_row).encode('cp932'),
            INDEX_TEXT.replace('download/protocol/protocol_j.pdf',
                'download/protocol/protocol_en.pdf').encode('cp932'),
            INDEX_TEXT.replace('download/protocol/protocol_j.pdf',
                'download/protocol/supplementary%20protocol_j.pdf').encode('cp932'),
            INDEX_TEXT.replace('download/protocol/protocol_j.pdf',
                'https://evil.example/bch/download/protocol/protocol_j.pdf').encode('cp932'),
            INDEX_TEXT.replace('<img src="pdf.gif">',
                '<img src="pdf.gif"></a><a href="download/protocol/protocol_j_v2.pdf"><img>').encode('cp932'),
            b'\x81',
        ]
        for html in variants:
            with self.subTest(index_hash=evidence._hash(html)):
                self.payloads[evidence.BIODIC_CARTAGENA_INDEX_URL] = evidence.OfficialGuidanceFetch(
                    html, evidence.BIODIC_CARTAGENA_INDEX_URL, 'text/html')
                before = len(self.calls)
                with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
                    self.resolve()
                self.assertEqual(self.calls[before:], [evidence.BIODIC_CARTAGENA_INDEX_URL])

    def test_redirect_and_source_binding_cannot_substitute_another_document_or_question(self):
        redirects = evidence._OfficialRedirects(evidence.BIODIC_CARTAGENA_PROVIDER)
        for url in ('https://www.env.go.jp/content/900444088.pdf',
                'https://www.biodic.go.jp/bch/download/protocol/protocol_en.pdf',
                PDF_URL + '?access_token=x'):
            with self.subTest(url=url), self.assertRaises(evidence.OfficialGuidanceEvidenceError):
                redirects.redirect_request(Request(PDF_URL), None, 302, 'redirect', {}, url)
        receipt = self.resolve()
        changed = deepcopy(self.record)
        changed['choiceTextList'].reverse()
        with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
            self.resolver.validate(receipt, request=self.request, record=changed, scope=self.scope)

    def test_refresh_rejects_changed_protocol_bytes(self):
        receipt = self.resolve()
        self.payloads[PDF_URL] = replace(self.payloads[PDF_URL], data=b'%PDF-new-protocol')
        with self.assertRaisesRegex(evidence.OfficialGuidanceEvidenceError, 'new audit required'):
            self.resolver.refresh(receipt, request=self.request, record=self.record, scope=self.scope)
        self.assertIsNone(self.resolver.last_refresh_receipt)

    def test_normal_prompt_and_schema_receive_the_prepared_protocol_proof(self):
        with patch.object(contract, 'source_scope', return_value=self.scope), \
                patch.object(contract, 'OfficialGuidanceEvidenceResolver', return_value=self.resolver):
            delivered = contract.prepare_evidence(self.root, self.request,
                record=self.record, scope=self.scope)
            refs = {k: delivered[k] for k in ('request', 'evidenceRef')}
            refs['applicabilityByChoice'] = [
                {'choiceIndex': 0, 'currentBasis': '議定書の越境移動の規定に対応する。',
                    'examTimeBasis': '出題時点の適用性は独立監査に残す。'},
                {'choiceIndex': 1, 'currentBasis': '本問の定義へ別議定書を補った命題に対応しない。',
                    'examTimeBasis': '出題時点の適用性は独立監査に残す。'}]
            record = {**self.record, 'isLawRelated': True, 'lawGroundedExplanationNotNeeded': False,
                'lawReferences': [[], []], contract.FIELD: refs}
            view = runs._law_context_evidence_view(self.root, record,
                {'status': 'not_applicable', 'items': []})
            self.assertEqual(view['officialGuidance']['provider'], evidence.BIODIC_CARTAGENA_PROVIDER)
            qid = self.scope['questionId']
            target = CandidateTarget('law', 'law_context', '18_law_context_prepared/q.json',
                ('isLawRelated', 'lawGroundedExplanationNotNeeded', 'lawReferences', contract.FIELD))
            raw = {'id': qid, 'listGroupId': self.scope['listGroupId'], **self.scope['sourceBinding']}
            prompt = runs._structured_candidate_prompt('各命題と条文を独立に確認する。', [raw],
                stage_id='law_context', records_by_question={qid: record},
                candidate_targets_by_question={qid: (target,)}, feedback_by_question={},
                official_guidance_evidence_by_question={qid: delivered})
            self.assertEqual(audit.question_inputs_from_prompt(prompt)[qid]['officialGuidanceEvidence'], delivered)
            schema = json.dumps(output_schema([qid], {qid: (target,)},
                official_guidance_inputs={qid: delivered}), ensure_ascii=False)
            self.assertIn(evidence.BIODIC_CARTAGENA_PROVIDER, schema)
            self.assertIn(delivered['evidenceRef']['sha256'], schema)


if __name__ == '__main__':
    unittest.main()
