"""Acquisition and immutable scope gates; no institutional correctness fixtures."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
from urllib.request import Request

from tools.question_review_console import official_guidance_evidence as guidance


CURRENT = 'https://www.mlit.go.jp/jutakukentiku/house/content/001999013.pdf'
HISTORICAL = 'https://www.mlit.go.jp/common/001417735.pdf'
INDEX = ('''<html><h3><div>【New】マンション標準管理規約（令和7年10月17日改正）</div></h3>
<ul><li><a href="/jutakukentiku/house/content/001999013.pdf">単棟型（コメント含む）</a></li></ul>
<h3>マンション標準管理規約（令和3年6月22日改正）</h3>
<a href="/common/001417735.pdf">単棟型（コメント含む）</a></html>''').encode()


class OfficialGuidanceEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.scope = {'qualification': 'kanrigyoumu', 'listGroupId': '94009', 'questionId': 'ui-id',
            'sourceBinding': {'sourceQuestionKey': 'kanrigyoumu:94009:source-id',
                'reviewQuestionId': 'source-id', 'sourceRecordRef': 'question_94009_2.json#10'},
            'sourceSha256': 'a' * 64}
        self.record = {'questionBodyText': '最も適切なものはどれか。', 'choiceTextList': ['第1の完全命題。', '第2の完全命題。']}
        self.request = {'schemaVersion': guidance.REQUEST_SCHEMA, 'provider': guidance.PROVIDER,
            'currentAsOf': '2026-10-25', 'examAsOf': '2023-04-01', 'currentPages': [5, 7],
            'historicalDocuments': [{'sourceUrl': HISTORICAL, 'revisionLabel': '令和3年の対照表（適用性未監査）', 'pages': [17]}],
            'choiceBindings': [
                {'choiceIndex': 0, 'currentPages': [5], 'historicalSelections': [{'documentIndex': 0, 'pages': [17]}]},
                {'choiceIndex': 1, 'currentPages': [7], 'historicalSelections': []}]}
        self.payloads = {guidance.INDEX_URL: guidance.OfficialGuidanceFetch(INDEX, guidance.INDEX_URL, 'text/html'),
            CURRENT: guidance.OfficialGuidanceFetch(b'%PDF-current', CURRENT, 'application/pdf'),
            HISTORICAL: guidance.OfficialGuidanceFetch(b'%PDF-historical', HISTORICAL, 'application/pdf')}
        self.calls = []
        self.extractor = lambda data, pages: [{'page': page, 'text': data.decode() + ' page ' + str(page)} for page in pages]
        def fetch(url, maximum):
            self.calls.append((url, maximum))
            return self.payloads[url]
        self.resolver = guidance.OfficialGuidanceEvidenceResolver(self.root, fetcher=fetch,
            extractor=self.extractor, now=lambda: '2026-10-25T09:10:11+00:00')

    def resolve(self):
        return self.resolver.resolve(self.request, record=self.record, scope=self.scope)

    def validate(self, receipt, **kwargs):
        return self.resolver.validate(receipt, request=self.request, record=self.record, scope=self.scope, **kwargs)

    def test_actual_retrieval_receipt_fixes_source_all_choices_current_and_historical(self):
        receipt = self.resolve()
        self.assertEqual(receipt['sourceKind'], 'official_institutional_guidance')
        self.assertNotIn('primaryLawEvidence', receipt)
        self.assertEqual(receipt['currentIndex']['publishedRevisionDate'], '2025-10-17')
        self.assertEqual(receipt['documents'][0]['sourceUrl'], CURRENT)
        self.assertEqual(receipt['documents'][0]['revisionLabelOrigin'], 'actual_current_index_heading')
        self.assertEqual(receipt['documents'][1]['revisionLabelOrigin'], 'declared_unreviewed')
        self.assertEqual(receipt['historicalCompleteness'], 'unreviewed')
        self.assertEqual(receipt['applicabilityStatus'], 'unreviewed')
        self.assertEqual(len(receipt['choiceHashes']), 2)
        self.assertEqual([call[0] for call in self.calls], [guidance.INDEX_URL, CURRENT, HISTORICAL])
        self.assertEqual(self.validate(receipt), receipt)
        self.assertEqual(len(self.calls), 3)  # Local integrity replay is not another retrieval.
        self.assertEqual(self.resolver.cache.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in self.resolver.cache.glob('*.bin')))

    def test_explanation_and_audit_growth_preserve_protected_binding(self):
        receipt = self.resolve()
        self.record.update(explanationText=['新解説'], lawReferences=[[], []], lawRevisionFacts=[{}, {}])
        self.assertEqual(self.validate(receipt), receipt)

    def test_source_body_choice_order_full_text_or_scope_change_invalidates_receipt(self):
        receipt = self.resolve()
        original_record, original_scope = deepcopy(self.record), deepcopy(self.scope)
        for mutation in ('body', 'choice', 'order', 'source', 'question', 'group', 'binding'):
            with self.subTest(mutation=mutation):
                self.record, self.scope = deepcopy(original_record), deepcopy(original_scope)
                if mutation == 'body': self.record['questionBodyText'] += '変更'
                if mutation == 'choice': self.record['choiceTextList'][0] += '変更'
                if mutation == 'order': self.record['choiceTextList'].reverse()
                if mutation == 'source': self.scope['sourceSha256'] = 'b' * 64
                if mutation == 'question': self.scope['questionId'] = 'another'
                if mutation == 'group': self.scope['listGroupId'] = '94008'
                if mutation == 'binding': self.scope['sourceBinding']['sourceRecordRef'] = 'question_94009_2.json#11'
                with self.assertRaises(guidance.OfficialGuidanceEvidenceError): self.validate(receipt)

    def test_missing_duplicate_or_page_outside_choice_bindings_never_fetches(self):
        original = deepcopy(self.request)
        for mutation in ('missing', 'duplicate', 'page', 'empty', 'unbound', 'bool-index', 'history-page', 'history-duplicate'):
            with self.subTest(mutation=mutation):
                self.request = deepcopy(original)
                choices = self.request['choiceBindings']
                if mutation == 'missing': choices.pop()
                if mutation == 'duplicate': choices[1]['choiceIndex'] = 0
                if mutation == 'page': choices[0]['currentPages'] = [9]
                if mutation == 'empty': choices[0]['currentPages'] = []
                if mutation == 'unbound': self.request['currentPages'].append(8)
                if mutation == 'bool-index': choices[0]['choiceIndex'] = False
                if mutation == 'history-page': choices[0]['historicalSelections'][0]['pages'] = [18]
                if mutation == 'history-duplicate': choices[0]['historicalSelections'] *= 2
                with self.assertRaises(guidance.OfficialGuidanceEvidenceError): self.resolve()
        self.assertEqual(self.calls, [])

    def test_index_requires_exact_unique_current_single_building_link(self):
        for html in (INDEX.replace('単棟型（コメント含む）'.encode(), '団地型（コメント含む）'.encode()),
                INDEX.replace(b'</ul>', b'<a href="/common/009999999.pdf">' + '単棟型（コメント含む）'.encode() + b'</a></ul>'),
                INDEX.replace(b'www.mlit.go.jp', b'evil.example').replace(b'href="/jutaku', b'href="https://evil.example/jutaku'),
                b'<h3>missing revision</h3>', b'\xff\xfe'):
            with self.subTest(html_hash=guidance._hash(html)):
                self.payloads[guidance.INDEX_URL] = guidance.OfficialGuidanceFetch(html, guidance.INDEX_URL, 'text/html')
                with self.assertRaises(guidance.OfficialGuidanceEvidenceError): self.resolve()

    def test_current_revision_is_selected_by_latest_date_in_either_index_order(self):
        old = ('<h3>マンション標準管理規約（令和3年6月22日改正）</h3>'
            '<a href="/common/001417735.pdf">単棟型（コメント含む）</a>')
        current = ('<h3>マンション標準管理規約（令和7年10月17日改正）</h3>'
            '<a href="/jutakukentiku/house/content/001999013.pdf">単棟型（コメント含む）</a>')
        for html in (old + current, current + old):
            with self.subTest(order_hash=guidance._hash(html.encode())):
                self.payloads[guidance.INDEX_URL] = guidance.OfficialGuidanceFetch(html.encode(), guidance.INDEX_URL, 'text/html')
                receipt = self.resolve()
                self.assertEqual(receipt['currentIndex']['publishedRevisionDate'], '2025-10-17')
                self.assertEqual(receipt['documents'][0]['sourceUrl'], CURRENT)
                self.assertEqual(self.validate(receipt), receipt)

    def test_duplicate_latest_future_or_ambiguous_revision_cannot_fall_back_to_old(self):
        old = ('<h3>マンション標準管理規約（令和3年6月22日改正）</h3>'
            '<a href="/common/001417735.pdf">単棟型（コメント含む）</a>')
        current = ('<h3>マンション標準管理規約（令和7年10月17日改正）</h3>'
            '<a href="/jutakukentiku/house/content/001999013.pdf">単棟型（コメント含む）</a>')
        for latest in (current + current,
                current + current.replace('001999013.pdf', '001999014.pdf'),
                current.replace('令和7年10月17日改正', '令和9年4月1日改正'),
                current.replace('令和7年10月17日改正', '令和7年改正'),
                current.replace('令和7年10月17日改正', '令和7年2月30日改正'),
                current.replace('令和7年10月17日改正', '平成37年10月17日改正'),
                current.replace('令和7年10月17日改正', '令和0年10月17日改正'),
                current.replace('令和7年10月17日改正', '令和7年10月17日改正・令和8年4月1日改正')):
            for html in (old + latest, latest + old):
                with self.subTest(index_hash=guidance._hash(html.encode())):
                    self.payloads[guidance.INDEX_URL] = guidance.OfficialGuidanceFetch(html.encode(), guidance.INDEX_URL, 'text/html')
                    before = len(self.calls)
                    with self.assertRaises(guidance.OfficialGuidanceEvidenceError): self.resolve()
                    self.assertEqual([call[0] for call in self.calls[before:]], [guidance.INDEX_URL])

    def test_nonofficial_redirects_credentials_ports_and_wrong_response_types_are_rejected(self):
        urls = ('http://www.mlit.go.jp/common/001.pdf', 'https://www.mlit.go.jp.evil.example/common/001.pdf',
            'https://user@www.mlit.go.jp/common/001.pdf', 'https://www.mlit.go.jp:443/common/001.pdf',
            'https://www.mlit.go.jp/common/001.pdf?access_token=x', 'https://www.mlit.go.jp/common/../001.pdf')
        for url in urls:
            with self.subTest(url=url):
                with self.assertRaises(guidance.OfficialGuidanceEvidenceError):
                    guidance._official_url(url, pdf=True)
        original = self.payloads[CURRENT]
        for response in (replace(original, resolved_url='https://evil.example/001.pdf'),
                replace(original, content_type='text/html'), replace(original, data=b'<html>not a PDF</html>'),
                replace(original, data=b'%PDF-' + b'x' * guidance.MAX_PDF_BYTES)):
            self.payloads[CURRENT] = response
            with self.assertRaises(guidance.OfficialGuidanceEvidenceError): self.resolve()
        redirect = guidance._OfficialRedirects()
        with self.assertRaises(guidance.OfficialGuidanceEvidenceError):
            redirect.redirect_request(Request(CURRENT), None, 302, 'redirect', {}, 'https://evil.example/001.pdf')

    def test_current_retrieval_is_not_a_historical_asof_service(self):
        self.request['currentAsOf'] = '2025-10-16'
        with self.assertRaises(guidance.OfficialGuidanceEvidenceError): self.resolve()
        self.assertEqual(self.calls, [])
        with self.assertRaises(guidance.OfficialGuidanceEvidenceError): guidance._current_link(INDEX, '2025-10-16')

    def test_refresh_retrieves_again_and_rejects_changed_index_pdf_or_extraction(self):
        receipt = self.resolve()
        self.resolver.now = lambda: '2026-10-25T09:11:12+00:00'
        self.assertEqual(self.validate(receipt, refresh=True), receipt)
        self.assertEqual(len(self.calls), 6)
        fresh = self.resolver.last_refresh_receipt
        self.assertEqual(fresh['pinnedEvidenceHash'], receipt['evidenceHash'])
        self.assertEqual(fresh['contentHash'], receipt['contentHash'])
        self.assertEqual(fresh['checkedAt'], '2026-10-25T09:11:12+00:00')
        self.assertNotEqual(fresh['freshEvidenceHash'], receipt['evidenceHash'])
        self.assertEqual(fresh['freshReceipt']['contentHash'], receipt['contentHash'])
        originals = dict(self.payloads)
        for mutation in ('index', 'pdf', 'history', 'extraction'):
            with self.subTest(mutation=mutation):
                self.payloads = dict(originals)
                self.resolver.extractor = self.extractor
                if mutation == 'index': self.payloads[guidance.INDEX_URL] = replace(originals[guidance.INDEX_URL], data=INDEX + b'changed')
                if mutation == 'pdf': self.payloads[CURRENT] = replace(originals[CURRENT], data=b'%PDF-changed')
                if mutation == 'history': self.payloads[HISTORICAL] = replace(originals[HISTORICAL], data=b'%PDF-history-changed')
                if mutation == 'extraction': self.resolver.extractor = lambda data, pages: [{'page': p, 'text': 'changed'} for p in pages]
                with self.assertRaises(guidance.OfficialGuidanceEvidenceError): self.validate(receipt, refresh=True)
                self.assertIsNone(self.resolver.last_refresh_receipt)

    def test_receipt_hash_and_rehashed_forged_page_never_replace_actual_bytes(self):
        receipt = self.resolve()
        corrupted = deepcopy(receipt)
        corrupted['documents'][0]['pages'][0]['text'] = 'fabricated verified source'
        with self.assertRaises(guidance.OfficialGuidanceEvidenceError): self.validate(corrupted)
        corrupted['documents'][0]['pageSelectionHash'] = guidance._json_hash(corrupted['documents'][0]['pages'])
        corrupted['contentHash'] = guidance._json_hash({k: v for k, v in corrupted.items()
            if k not in {'contentHash', 'retrievalCompletedAt', 'evidenceHash'}})
        corrupted['evidenceHash'] = guidance._json_hash({k: v for k, v in corrupted.items() if k != 'evidenceHash'})
        with self.assertRaises(guidance.OfficialGuidanceEvidenceError): self.validate(corrupted)
        path = self.resolver._blob_path(receipt['documents'][0]['byteHash'])
        path.write_bytes(b'%PDF-replaced')
        with self.assertRaises(guidance.OfficialGuidanceEvidenceError): self.validate(receipt)

    def test_interrupted_fetch_has_no_receipt_and_fresh_retry_recovers_from_partial_cache(self):
        actual_fetch = self.resolver.fetcher
        def interrupted(url, maximum):
            if url == HISTORICAL: raise InterruptedError('retrieval interrupted')
            return actual_fetch(url, maximum)
        self.resolver.fetcher = interrupted
        with self.assertRaises(InterruptedError): self.resolve()
        self.assertFalse(list(self.resolver.cache.glob('*.json')))
        self.resolver.fetcher = actual_fetch
        receipt = self.resolve()
        self.assertEqual(self.validate(receipt, refresh=True), receipt)
        self.assertFalse(list(self.resolver.cache.glob('.retrieval-*')))

    def test_concurrent_identical_retrievals_and_resume_keep_content_addressed_bytes(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(pool.map(lambda _: self.resolve(), range(2)))
        self.assertEqual(receipts[0], receipts[1])
        self.assertEqual(self.validate(receipts[0]), receipts[0])
        self.assertEqual(len(list(self.resolver.cache.glob('*.bin'))), 3)
        self.assertFalse(list(self.resolver.cache.glob('.retrieval-*')))

    def test_empty_truncated_misnumbered_or_excessive_page_extraction_is_rejected(self):
        for extractor in (lambda data, pages: [],
                lambda data, pages: [{'page': p, 'text': ''} for p in pages],
                lambda data, pages: [{'page': 99, 'text': 'text'} for p in pages],
                lambda data, pages: [{'page': p, 'text': 'x' * (guidance.MAX_TEXT_BYTES + 1)} for p in pages]):
            self.resolver.extractor = extractor
            with self.assertRaises(guidance.OfficialGuidanceEvidenceError): self.resolve()

    def test_cache_symlink_is_rejected_before_writing_or_changing_permissions(self):
        outside = self.root / 'outside'
        outside.mkdir(mode=0o755)
        self.resolver.cache.parent.mkdir(parents=True)
        self.resolver.cache.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(guidance.OfficialGuidanceEvidenceError): self.resolve()
        self.assertEqual(outside.stat().st_mode & 0o777, 0o755)
        self.assertEqual(list(outside.iterdir()), [])

    def test_date_change_during_fetch_and_naive_timestamp_never_return_a_receipt(self):
        self.resolver.now = iter(['2026-10-25T14:59:59+00:00', '2026-10-25T15:00:01+00:00']).__next__
        with self.assertRaisesRegex(guidance.OfficialGuidanceEvidenceError, 'date changed'): self.resolve()
        self.resolver.now = lambda: '2026-10-25T09:00:00'
        with self.assertRaisesRegex(guidance.OfficialGuidanceEvidenceError, 'timezone'): self.resolve()

    def test_real_pdfium_extracts_only_selected_page_and_rejects_out_of_range(self):
        # A small real PDF: this checks the installed decoder, not a mocked page list.
        objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
            b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
            b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
        stream = b'BT /F1 12 Tf 10 100 Td (Actual selected official PDF page) Tj ET'
        objects.append(b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream')
        data = b'%PDF-1.4\n'
        offsets = [0]
        for number, value in enumerate(objects, 1):
            offsets.append(len(data)); data += str(number).encode() + b' 0 obj\n' + value + b'\nendobj\n'
        xref = len(data)
        data += b'xref\n0 6\n0000000000 65535 f \n'
        data += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
        data += b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n' + str(xref).encode() + b'\n%%EOF'
        selected = guidance._extract(data, [1])
        self.assertEqual(selected[0]['page'], 1)
        self.assertIn('Actual selected official PDF page', selected[0]['text'])
        with self.assertRaises(guidance.OfficialGuidanceEvidenceError): guidance._extract(data, [2])


if __name__ == '__main__':
    unittest.main()
