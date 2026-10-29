"""Named EU publications retain the normal acquisition and binding gates."""
from copy import deepcopy
import html
import json
import unittest

from tests import test_official_guidance_evidence as fixture
from tools.question_review_console import official_guidance_evidence as evidence


def index_link(**changes):
    value = {'data-language': 'ENG', 'data-id': evidence.EU_ROHS_CELLAR_ID,
        'data-publication-title': evidence.EU_ROHS_TITLE,
        'data-uri': evidence.EU_ROHS_PDF_URL.replace('https:', 'http:')}
    value.update(changes)
    work = 'http://publications.europa.eu/resource/consolidation/2011L0065%2F20260701'
    alias = 'http://publications.europa.eu/resource/cellar/' + value['data-id'] + '.0006.03'
    title = html.escape(value['data-publication-title'])
    item = html.escape(value['data-uri'], quote=True)
    return f'''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        xmlns:cdm="http://publications.europa.eu/ontology/cdm#"
        xmlns:owl="http://www.w3.org/2002/07/owl#">
      <rdf:Description rdf:about="{work}">
        <cdm:act_consolidated_date>2026-07-01</cdm:act_consolidated_date>
        <cdm:act_consolidated_based_on_resource_legal rdf:resource="http://publications.europa.eu/resource/celex/32011L0065"/>
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
        <owl:sameAs rdf:resource="http://publications.europa.eu/resource/celex/02011L0065-20260701.ENG.pdfa2a"/>
      </rdf:Description>
      <rdf:Description rdf:about="{item}">
        <cdm:item_belongs_to_manifestation rdf:resource="{alias}"/>
        <cdm:item_identifier>DOC_1</cdm:item_identifier>
      </rdf:Description>
    </rdf:RDF>'''.encode()


def versions_index(rows=None):
    if rows is None:
        rows = [('2026-07-01', evidence.EU_ROHS_CELLAR_ID),
            ('2025-01-01', 'f573bd1a-c206-11ef-91ed-01aa75ed71a1')]
    return json.dumps({'head': {'link': [], 'vars': ['work', 'celex', 'date']}, 'results': {'distinct': False, 'ordered': True, 'bindings': [
        {'work': {'type': 'uri', 'value': 'http://publications.europa.eu/resource/cellar/' + ident},
         'celex': {'type': 'literal', 'datatype': 'http://www.w3.org/2001/XMLSchema#string',
             'value': '02011L0065-' + day.replace('-', '')},
         'date': {'type': 'literal', 'datatype': 'http://www.w3.org/2001/XMLSchema#date', 'value': day}}
        for day, ident in rows]}}).encode()


class EURohsOfficialEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.OfficialGuidanceEvidenceTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.request.update(provider=evidence.EU_ROHS_PROVIDER,
            currentAsOf='2026-10-28', examAsOf='2026-10-28')
        self.f.request['historicalDocuments'][0]['sourceUrl'] = evidence.EU_ROHS_ORIGINAL_PDF_URL
        self.f.request['historicalDocuments'][0]['revisionLabel'] = 'Original Official Journal, 2011'
        self.current_header = ('This text is meant purely as a documentation tool and has no legal effect. DIRECTIVE 2011/65/EU of 8 June 2011 '
            '02011L0065 — EN — 01.07.2026 — 001.001 — 1')
        self.original_header = ('L 174/88 Official Journal of the European Union '
            'DIRECTIVE 2011/65/EU of 8 June 2011')
        self.current_raw = b'%PDF-eu-consolidated'
        self.original_raw = b'%PDF-eu-original'
        self.f.payloads = {
            evidence.EU_ROHS_INDEX_URL: evidence.OfficialGuidanceFetch(
                index_link(), evidence.EU_ROHS_INDEX_URL, 'application/rdf+xml'),
            evidence.EU_ROHS_VERSIONS_URL: evidence.OfficialGuidanceFetch(
                versions_index(), evidence.EU_ROHS_VERSIONS_URL, 'application/sparql-results+json'),
            evidence.EU_ROHS_PDF_URL: evidence.OfficialGuidanceFetch(
                self.current_raw, evidence.EU_ROHS_PDF_URL, 'application/pdf'),
            evidence.EU_ROHS_ORIGINAL_PDF_URL: evidence.OfficialGuidanceFetch(
                self.original_raw, evidence.EU_ROHS_ORIGINAL_PDF_URL, 'application/pdf')}
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
            [evidence.EU_ROHS_PDF_URL, evidence.EU_ROHS_ORIGINAL_PDF_URL])
        self.assertIn('1 July 2026', value['documents'][0]['revisionLabel'])
        self.assertIsNone(value['currentIndex']['publishedRevisionDate'])
        self.assertEqual(value['applicabilityStatus'], 'unreviewed')
        self.assertEqual(value['historicalCompleteness'], 'unreviewed')
        self.assertEqual(self.f.validate(value), value)
        self.assertNotIn('lawId', value)

    def test_article_and_annex_pages_are_bound_without_asserting_applicability(self):
        self.f.request['currentPages'] = [8, 22]
        self.f.request['choiceBindings'][0]['currentPages'] = [8]
        self.f.request['choiceBindings'][1]['currentPages'] = [22]
        result = self.f.resolve()
        self.assertEqual([page['page'] for page in result['documents'][0]['pages']], [8, 22])
        self.assertEqual(result['applicabilityStatus'], 'unreviewed')
        self.assertEqual(self.f.validate(result), result)

    def test_english_pdf_is_selected_without_using_other_languages(self):
        raw = index_link().replace(b'</rdf:RDF>', b'''<rdf:Description rdf:about="other-expression">
            <cdm:expression_belongs_to_work rdf:resource="http://publications.europa.eu/resource/consolidation/2011L0065%2F20260701"/>
            <cdm:expression_uses_language rdf:resource="http://publications.europa.eu/resource/authority/language/FRA"/>
            </rdf:Description></rdf:RDF>''')
        self.f.payloads[evidence.EU_ROHS_INDEX_URL] = evidence.OfficialGuidanceFetch(
            raw, evidence.EU_ROHS_INDEX_URL, 'application/rdf+xml')
        self.assertEqual(self.f.resolve()['documents'][0]['sourceUrl'], evidence.EU_ROHS_PDF_URL)

    def test_missing_duplicate_wrong_title_language_or_publication_fails_before_pdf_fetch(self):
        for raw in [b'<html>JavaScript is disabled; verify you are not a robot</html>',
                index_link() * 2, index_link(**{'data-language': 'FRA'}),
                index_link(**{'data-id': 'different-publication'}),
                index_link(**{'data-publication-title': 'Different directive'}),
                index_link(**{'data-uri': evidence.EU_ROHS_ORIGINAL_PDF_URL})]:
            with self.subTest(raw=raw[:90]):
                self.f.calls.clear()
                self.f.payloads[evidence.EU_ROHS_INDEX_URL] = evidence.OfficialGuidanceFetch(
                    raw, evidence.EU_ROHS_INDEX_URL, 'application/rdf+xml')
                with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
                    self.f.resolve()
                self.assertEqual([url for url, _ in self.f.calls], [evidence.EU_ROHS_INDEX_URL, evidence.EU_ROHS_VERSIONS_URL])

    def test_query_exception_does_not_allow_other_publications_languages_hosts_or_schemes(self):
        for url in [evidence.EU_ROHS_PDF_URL + '&extra=1',
                evidence.EU_ROHS_PDF_URL.replace('.0006.03/', '.0007.03/'),
                evidence.EU_ROHS_PDF_URL.replace(evidence.EU_ROHS_CELLAR_ID, 'another-id'),
                evidence.EU_ROHS_PDF_URL.replace('https:', 'http:'),
                evidence.EU_ROHS_PDF_URL.replace('publications.europa.eu/', 'publications.europa.eu.evil.example/'),
                evidence.EU_ROHS_ORIGINAL_PDF_URL.replace('DOC_1', 'DOC_2')]:
            with self.subTest(url=url):
                with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
                    evidence._official_url(url, pdf=True, provider=evidence.EU_ROHS_PROVIDER)
        with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
            evidence._official_url(evidence.EU_ROHS_PDF_URL, pdf=True)

    def test_future_edition_is_not_used_for_an_earlier_reference_date(self):
        with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
            evidence._eu_rohs_link(index_link(), '2026-06-30', versions_index())

    def test_current_uses_latest_at_reference_date_and_rejects_later_version(self):
        later = ('2040-01-01', '12345678-abcd-1234-abcd-123456789abc')
        indexed = versions_index([later, ('2026-07-01', evidence.EU_ROHS_CELLAR_ID)])
        self.assertEqual(evidence._eu_rohs_link(index_link(), '2026-10-29', indexed)[0], evidence.EU_ROHS_PDF_URL)
        with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
            evidence._eu_rohs_link(index_link(), '2040-01-01', indexed)
        with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
            evidence._eu_rohs_link(index_link(), '2040-01-01')

    def test_version_index_rejects_empty_future_duplicate_and_bad_responses(self):
        valid = versions_index()
        row = json.loads(valid)['results']['bindings'][0]
        malformed = [b'not json', b'{}', versions_index([]),
            versions_index([('2027-01-01', '12345678-abcd-1234-abcd-123456789abc')]),
            versions_index([('2026-07-01', evidence.EU_ROHS_CELLAR_ID),
                ('2026-07-01', '12345678-abcd-1234-abcd-123456789abc')]),
            valid.replace(b'02011L0065-20260701', b'02011L0066-20260701'),
            valid.replace(b'2026-07-01', b'2026-02-30'),
            valid.replace(b'"work": {', b'"work": {}, "work": {', 1)]
        for raw in malformed:
            with self.subTest(raw=raw[:100]):
                with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
                    evidence._eu_rohs_versions(raw, '2026-10-28')
        changed = deepcopy(row)
        changed['work']['value'] = 'http://publications.europa.eu/resource/cellar/12345678-abcd-1234-abcd-123456789abc'
        result = json.loads(valid)
        result['results']['bindings'][0] = changed
        with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
            evidence._eu_rohs_versions(json.dumps(result).encode(), '2026-10-28')

    def test_version_index_refresh_failure_and_replacement_preserve_no_fresh_receipt(self):
        original = self.f.resolve()
        version = self.f.payloads[evidence.EU_ROHS_VERSIONS_URL]
        self.assertEqual(original['currentIndex']['versionIndex']['byteHash'], evidence._hash(version.data))
        self.assertEqual(original['currentIndex']['versionIndex']['sourceUrl'], evidence.EU_ROHS_VERSIONS_URL)
        self.f.validate(original, refresh=True)
        self.assertEqual(self.f.resolver.last_refresh_receipt['checkedAt'], original['retrievalCompletedAt'])
        newer = versions_index([('2026-10-01', '12345678-abcd-1234-abcd-123456789abc'),
            ('2026-07-01', evidence.EU_ROHS_CELLAR_ID)])
        self.f.payloads[evidence.EU_ROHS_VERSIONS_URL] = evidence.OfficialGuidanceFetch(
            newer, evidence.EU_ROHS_VERSIONS_URL, 'application/sparql-results+json')
        with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
            self.f.validate(original, refresh=True)
        self.assertIsNone(self.f.resolver.last_refresh_receipt)
        self.f.payloads[evidence.EU_ROHS_VERSIONS_URL] = evidence.OfficialGuidanceFetch(
            version.data, 'https://evil.example/redirect', 'application/sparql-results+json')
        with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
            self.f.validate(original, refresh=True)
        self.assertIsNone(self.f.resolver.last_refresh_receipt)

    def test_version_index_fetch_exception_is_not_a_fallback(self):
        self.f.payloads.pop(evidence.EU_ROHS_VERSIONS_URL)
        with self.assertRaises(KeyError):
            self.f.resolve()

    def test_page_one_identity_is_checked_even_when_not_in_selected_spans(self):
        original = self.current_header
        for header in [original.replace('01.07.2026', '01.07.2025'),
                original.replace('EN', 'FR'), original.replace('2011/65/EU', '2011/66/EU')]:
            with self.subTest(header=header):
                self.current_header = header
                with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
                    self.f.resolve()

    def test_rdf_graph_requires_matching_work_language_manifestation_and_item(self):
        original = index_link()
        for raw in [original.replace(b'2026-07-01', b'2026-07-02'),
                original.replace(b'32011L0065', b'32011L0066'),
                original.replace(b'<cdm:manifestation_type>pdfa2a', b'<cdm:manifestation_type>html'),
                original.replace(b'<cdm:item_identifier>DOC_1', b'<cdm:item_identifier>DOC_2'),
                b'<!DOCTYPE RDF [<!ENTITY x "value">]>' + original]:
            with self.subTest(raw=raw[:80]):
                with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
                    evidence._eu_rohs_link(raw, '2026-10-28', versions_index())

    def test_unauthorized_redirect_or_response_type_is_rejected(self):
        original = self.f.payloads[evidence.EU_ROHS_PDF_URL]
        for resolved, media in [(evidence.EU_ROHS_PDF_URL.replace('publications.europa.eu/',
                'publications.europa.eu.evil.example/'), 'application/pdf'),
                (evidence.EU_ROHS_PDF_URL, 'text/html')]:
            with self.subTest(resolved=resolved, media=media):
                self.f.payloads[evidence.EU_ROHS_PDF_URL] = evidence.OfficialGuidanceFetch(
                    original.data, resolved, media)
                with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
                    self.f.resolve()
        self.f.payloads[evidence.EU_ROHS_PDF_URL] = original

    def test_celex_alias_and_documentation_status_are_required(self):
        raw = index_link().replace(b'02011L0065-20260701.ENG.pdfa2a',
            b'02011L0065-20260702.ENG.pdfa2a')
        with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
            evidence._eu_rohs_link(raw, '2026-10-28', versions_index())
        self.current_header = self.current_header.replace('and has no legal effect', 'with full legal effect')
        with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
            self.f.resolve()

    def test_cached_pdf_tampering_is_detected(self):
        value = self.f.resolve()
        path = self.f.resolver._blob_path(value['documents'][0]['byteHash'])
        path.write_bytes(b'%PDF-tampered')
        with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
            self.f.validate(value)

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
        self.assertEqual(len(self.f.calls), calls_before + 4)
        self.assertEqual(self.f.resolver.last_refresh_receipt['pinnedEvidenceHash'], value['evidenceHash'])
        index = self.f.payloads[evidence.EU_ROHS_INDEX_URL]
        self.f.payloads[evidence.EU_ROHS_INDEX_URL] = evidence.OfficialGuidanceFetch(
            index.data + b' ', index.resolved_url, index.content_type)
        with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
            self.f.validate(value, refresh=True)
        self.assertIsNone(self.f.resolver.last_refresh_receipt)
        self.f.payloads[evidence.EU_ROHS_INDEX_URL] = index
        self.f.payloads[evidence.EU_ROHS_PDF_URL] = evidence.OfficialGuidanceFetch(
            b'%PDF-replaced', evidence.EU_ROHS_PDF_URL, 'application/pdf')
        with self.assertRaises(evidence.OfficialGuidanceEvidenceError):
            self.f.validate(value, refresh=True)
        self.assertIsNone(self.f.resolver.last_refresh_receipt)


if __name__ == '__main__':
    unittest.main()
