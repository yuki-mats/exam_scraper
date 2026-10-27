"""Named EU publications retain the normal acquisition and binding gates."""
from copy import deepcopy
import html
import unittest

from tests import test_official_guidance_evidence as fixture
from tools.question_review_console import official_guidance_evidence as evidence


def index_link(**changes):
    value = {'data-language': 'ENG', 'data-id': evidence.EU_SUP_CELLAR_ID,
        'data-publication-title': evidence.EU_SUP_TITLE,
        'data-uri': evidence.EU_SUP_PDF_URL.replace('https:', 'http:')}
    value.update(changes)
    work = 'http://publications.europa.eu/resource/consolidation/2019L0904%2F20260812_0010010'
    alias = 'http://publications.europa.eu/resource/cellar/' + value['data-id'] + '.0001.03'
    title = html.escape(value['data-publication-title'])
    item = html.escape(value['data-uri'], quote=True)
    return f'''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        xmlns:cdm="http://publications.europa.eu/ontology/cdm#"
        xmlns:owl="http://www.w3.org/2002/07/owl#">
      <rdf:Description rdf:about="{work}">
        <cdm:act_consolidated_date>2026-08-12</cdm:act_consolidated_date>
        <cdm:act_consolidated_based_on_resource_legal rdf:resource="http://publications.europa.eu/resource/celex/32019L0904"/>
      </rdf:Description>
      <rdf:Description rdf:about="{work}.ENG">
        <cdm:expression_belongs_to_work rdf:resource="{work}"/>
        <cdm:expression_uses_language rdf:resource="http://publications.europa.eu/resource/authority/language/{value['data-language']}"/>
        <cdm:expression_title>{title}</cdm:expression_title>
      </rdf:Description>
      <rdf:Description rdf:about="{work}.ENG.pdfa2a">
        <cdm:manifestation_manifests_expression rdf:resource="{work}.ENG"/>
        <cdm:manifestation_type>pdfa2a</cdm:manifestation_type>
      </rdf:Description>
      <rdf:Description rdf:about="{alias}">
        <owl:sameAs rdf:resource="{work}.ENG.pdfa2a"/>
      </rdf:Description>
      <rdf:Description rdf:about="{item}">
        <cdm:item_belongs_to_manifestation rdf:resource="{alias}"/>
        <cdm:item_identifier>DOC_1</cdm:item_identifier>
      </rdf:Description>
    </rdf:RDF>'''.encode()


class EUSupOfficialEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.OfficialGuidanceEvidenceTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.request.update(provider=evidence.EU_SUP_PROVIDER,
            currentAsOf='2026-10-28', examAsOf='2026-10-28')
        self.f.request['historicalDocuments'][0]['sourceUrl'] = evidence.EU_SUP_ORIGINAL_PDF_URL
        self.f.request['historicalDocuments'][0]['revisionLabel'] = 'Original Official Journal, 2019'
        self.current_header = ('DIRECTIVE (EU) 2019/904 of 5 June 2019 '
            '02019L0904 — EN — 12.08.2026 — 001.001 — 1')
        self.original_header = ('Official Journal of the European Union '
            'DIRECTIVE (EU) 2019/904 of 5 June 2019')
        self.current_raw = b'%PDF-eu-consolidated'
        self.original_raw = b'%PDF-eu-original'
        self.f.payloads = {
            evidence.EU_SUP_INDEX_URL: evidence.OfficialGuidanceFetch(
                index_link(), evidence.EU_SUP_INDEX_URL, 'application/rdf+xml'),
            evidence.EU_SUP_PDF_URL: evidence.OfficialGuidanceFetch(
                self.current_raw, evidence.EU_SUP_PDF_URL, 'application/pdf'),
            evidence.EU_SUP_ORIGINAL_PDF_URL: evidence.OfficialGuidanceFetch(
                self.original_raw, evidence.EU_SUP_ORIGINAL_PDF_URL, 'application/pdf')}
        def extract(raw, pages):
            header = self.current_header if raw == self.current_raw else self.original_header
            return [{'page': page, 'text': header if page == 1 else raw.decode() + f' page {page}'}
                for page in pages]
        self.f.resolver.extractor = extract
        self.f.resolver.now = lambda: '2026-10-28T03:00:00+00:00'

    def test_current_and_original_are_distinct_bound_sources_without_adoption(self):
        value = self.f.resolve()
        self.assertEqual(value['sourceKind'], 'official_consolidated_eu_act')
        self.assertEqual([d['sourceUrl'] for d in value['documents']],
            [evidence.EU_SUP_PDF_URL, evidence.EU_SUP_ORIGINAL_PDF_URL])
        self.assertIn('12 August 2026', value['documents'][0]['revisionLabel'])
        self.assertIsNone(value['currentIndex']['publishedRevisionDate'])
        self.assertEqual(value['applicabilityStatus'], 'unreviewed')
        self.assertEqual(value['historicalCompleteness'], 'unreviewed')
        self.assertEqual(self.f.validate(value), value)
        self.assertNotIn('lawId', value)

    def test_english_pdf_is_selected_without_using_other_languages(self):
        raw = index_link().replace(b'</rdf:RDF>', b'''<rdf:Description rdf:about="other-expression">
            <cdm:expression_belongs_to_work rdf:resource="http://publications.europa.eu/resource/consolidation/2019L0904%2F20260812_0010010"/>
            <cdm:expression_uses_language rdf:resource="http://publications.europa.eu/resource/authority/language/FRA"/>
            </rdf:Description></rdf:RDF>''')
        self.f.payloads[evidence.EU_SUP_INDEX_URL] = evidence.OfficialGuidanceFetch(
            raw, evidence.EU_SUP_INDEX_URL, 'application/rdf+xml')
        self.assertEqual(self.f.resolve()['documents'][0]['sourceUrl'], evidence.EU_SUP_PDF_URL)

    def test_missing_duplicate_wrong_title_language_or_publication_fails_before_pdf_fetch(self):
        for raw in [b'<html>JavaScript is disabled; verify you are not a robot</html>',
                index_link() * 2, index_link(**{'data-language': 'FRA'}),
                index_link(**{'data-id': 'different-publication'}),
                index_link(**{'data-publication-title': 'Different directive'}),
                index_link(**{'data-uri': evidence.EU_SUP_ORIGINAL_PDF_URL})]:
            with self.subTest(raw=raw[:90]):
                self.f.calls.clear()
                self.f.payloads[evidence.EU_SUP_INDEX_URL] = evidence.OfficialGuidanceFetch(
                    raw, evidence.EU_SUP_INDEX_URL, 'application/rdf+xml')
                with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
                    self.f.resolve()
                self.assertEqual([url for url, _ in self.f.calls], [evidence.EU_SUP_INDEX_URL])

    def test_query_exception_does_not_allow_other_publications_languages_hosts_or_schemes(self):
        for url in [evidence.EU_SUP_PDF_URL + '&extra=1',
                evidence.EU_SUP_PDF_URL.replace('.0001.03/', '.0002.03/'),
                evidence.EU_SUP_PDF_URL.replace(evidence.EU_SUP_CELLAR_ID, 'another-id'),
                evidence.EU_SUP_PDF_URL.replace('https:', 'http:'),
                evidence.EU_SUP_PDF_URL.replace('publications.europa.eu/', 'publications.europa.eu.evil.example/'),
                evidence.EU_SUP_ORIGINAL_PDF_URL.replace('DOC_1', 'DOC_2')]:
            with self.subTest(url=url):
                with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
                    evidence._official_url(url, pdf=True, provider=evidence.EU_SUP_PROVIDER)
        with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
            evidence._official_url(evidence.EU_SUP_PDF_URL, pdf=True)

    def test_future_edition_is_not_used_for_an_earlier_reference_date(self):
        with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
            evidence._eu_sup_link(index_link(), '2026-08-11')

    def test_page_one_identity_is_checked_even_when_not_in_selected_spans(self):
        original = self.current_header
        for header in [original.replace('12.08.2026', '12.08.2025'),
                original.replace('EN', 'FR'), original.replace('2019/904', '2019/905')]:
            with self.subTest(header=header):
                self.current_header = header
                with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
                    self.f.resolve()

    def test_rdf_graph_requires_matching_work_language_manifestation_and_item(self):
        original = index_link()
        for raw in [original.replace(b'2026-08-12', b'2026-08-13'),
                original.replace(b'32019L0904', b'32019L0905'),
                original.replace(b'<cdm:manifestation_type>pdfa2a', b'<cdm:manifestation_type>html'),
                original.replace(b'<cdm:item_identifier>DOC_1', b'<cdm:item_identifier>DOC_2'),
                b'<!DOCTYPE RDF [<!ENTITY x "value">]>' + original]:
            with self.subTest(raw=raw[:80]):
                with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
                    evidence._eu_sup_link(raw, '2026-10-28')

    def test_consolidation_cannot_be_supplied_as_original_official_journal(self):
        self.original_header = self.current_header + ' Official Journal of the European Union'
        with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
            self.f.resolve()

    def test_question_body_last_choice_and_source_drift_still_invalidate(self):
        value = self.f.resolve()
        original = deepcopy(self.f.record)
        for key in ['body', 'last-choice', 'source']:
            with self.subTest(key=key):
                self.f.record = deepcopy(original)
                if key == 'body': self.f.record['questionBodyText'] += ' changed'
                if key == 'last-choice': self.f.record['choiceTextList'][-1] += ' changed'
                if key == 'source': self.f.scope['sourceSha256'] = 'b' * 64
                with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
                    self.f.validate(value)

    def test_refresh_retains_binding_and_rejects_a_changed_publication(self):
        value = self.f.resolve()
        calls_before = len(self.f.calls)
        self.f.validate(value, refresh=True)
        self.assertEqual(len(self.f.calls), calls_before + 3)
        self.assertEqual(self.f.resolver.last_refresh_receipt['pinnedEvidenceHash'], value['evidenceHash'])
        index = self.f.payloads[evidence.EU_SUP_INDEX_URL]
        self.f.payloads[evidence.EU_SUP_INDEX_URL] = evidence.OfficialGuidanceFetch(
            index.data + b' ', index.resolved_url, index.content_type)
        with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
            self.f.validate(value, refresh=True)
        self.assertIsNone(self.f.resolver.last_refresh_receipt)
        self.f.payloads[evidence.EU_SUP_INDEX_URL] = index
        self.f.payloads[evidence.EU_SUP_PDF_URL] = evidence.OfficialGuidanceFetch(
            b'%PDF-replaced', evidence.EU_SUP_PDF_URL, 'application/pdf')
        with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
            self.f.validate(value, refresh=True)
        self.assertIsNone(self.f.resolver.last_refresh_receipt)


if __name__ == '__main__':
    unittest.main()
