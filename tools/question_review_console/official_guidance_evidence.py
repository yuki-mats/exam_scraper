"""Official institutional documents and treaty texts, separate from e-Gov XML.

This provider proves acquisition and source/choice binding, not applicability or
correctness. Callers must deliver the receipt to their real model turns, bind the
receipt hash in execution/audit scopes, and refresh it before publication.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urljoin, urlsplit
import urllib.request
import xml.etree.ElementTree as ET

from tools.question_review_console.http_transport import IPv4HTTPSHandler


SCHEMA_VERSION = 'official-guidance-evidence/v1'
REQUEST_SCHEMA = 'official-guidance-request/v1'
SOURCE_KIND = 'official_institutional_guidance'
PROVIDER = 'mlit_mansion_standard_single'
INDEX_URL = 'https://www.mlit.go.jp/jutakukentiku/house/mansionkiyaku.html'
ENV_PARIS_PROVIDER = 'env_paris_agreement'
ENV_PARIS_INDEX_URL = 'https://www.env.go.jp/earth/ondanka/cop/shiryo.html'
ENV_RAMSAR_PROVIDER = 'env_ramsar_convention'
ENV_RAMSAR_INDEX_URL = 'https://www.env.go.jp/nature/ramsar/conv/About_RamarConvention.html'
BIODIC_CARTAGENA_PROVIDER = 'biodic_cartagena_protocol'
BIODIC_CARTAGENA_INDEX_URL = 'https://www.biodic.go.jp/bch/bch_1.html'
ENV_BASEL_PROVIDER = 'env_basel_convention'
ENV_BASEL_INDEX_URL = 'https://www.env.go.jp/recycle/yugai/index2.html'
ENV_BASEL_REFERENCE_PROVIDER = 'env_basel_reference_excerpt'
ENV_BASEL_REFERENCE_INDEX_URL = 'https://www.env.go.jp/press/103133.html'
ENV_FOREST_REFERENCE_PROVIDER = 'env_forest_principles_reference'
ENV_FOREST_REFERENCE_INDEX_URL = 'https://www.env.go.jp/policy/hakusyo/h24/pdf.html'
EU_SUP_PROVIDER = 'eu_sup_consolidated_20260812'
EU_SUP_CELLAR_ID = 'a7d357bb-6f48-11f1-ae88-01aa75ed71a1'
# Use the publication's observed RDF export instead of its dynamic portal HTML.
# Raw metadata bytes remain part of the same strict refresh hash as every PDF.
EU_SUP_INDEX_URL = ('https://op.europa.eu:443/en/publication-detail?'
    'p_p_id=publicationDetailsActions_PublicationDetailsActionsPortlet_INSTANCE_P7Lc96wTRLvv'
    '&p_p_lifecycle=2&p_p_state=normal&p_p_mode=view&p_p_cacheability=cacheLevelPage'
    '&_publicationDetailsActions_PublicationDetailsActionsPortlet_INSTANCE_P7Lc96wTRLvv_requestAction=rdf'
    '&cellarId=' + EU_SUP_CELLAR_ID + '&nonInferred=true')
EU_SUP_PDF_URL = ('https://publications.europa.eu/resource/cellar/'
    + EU_SUP_CELLAR_ID + '.0001.03/DOC_1')
EU_SUP_ORIGINAL_PDF_URL = ('https://publications.europa.eu/resource/cellar/'
    'ab83570c-8cc8-11e9-9369-01aa75ed71a1.0006.01/DOC_1')
EU_SUP_TITLE = ('Directive (EU) 2019/904 of the European Parliament and of the Council of '
    '5 June 2019 on the reduction of the impact of certain plastic products on the environment '
    '(Text with EEA relevance)')
ENV_BASEL_REFERENCE_SECTION = (
    '中央環境審議会循環型社会部会特定有害廃棄物等の輸出入等の規制の在り方に関する専門委員会、'
    '産業構造審議会産業技術環境分科会廃棄物・リサイクル小委員会'
    '有害廃棄物等越境移動ワーキンググループ合同会議（第１回）の開催について')
MAX_PDF_BYTES = 20_000_000
MAX_INDEX_BYTES = 2_000_000
# Bounded text must accommodate current rules/comments plus the historical
# base and amendments, rather than silently omit an exception or comment.
MAX_TEXT_BYTES = 96_000
MAX_SELECTED_PAGES = 32
REFERENCE_TIMEZONE = timezone(timedelta(hours=9))


class OfficialGuidanceEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class OfficialGuidanceFetch:
    data: bytes
    resolved_url: str
    content_type: str


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _json_hash(value):
    return _hash(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8'))


def _exact(value, fields, label):
    if not isinstance(value, Mapping) or set(value) != set(fields):
        raise OfficialGuidanceEvidenceError(label + ' fields differ')


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise OfficialGuidanceEvidenceError('nonempty text required')
    return value


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise OfficialGuidanceEvidenceError('ISO date required')
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise OfficialGuidanceEvidenceError('invalid date') from exc


def _timestamp(value):
    try:
        stamp = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise OfficialGuidanceEvidenceError('actual retrieval completion timestamp required') from exc
    if stamp.tzinfo is None:
        raise OfficialGuidanceEvidenceError('timezone-aware retrieval timestamp required')
    return stamp.astimezone(timezone.utc)


def _reference_date(value):
    # Native maintenance uses Japanese calendar dates for law/reference days.
    # Keep acquisition timestamps timezone-aware; do not confuse UTC midnight
    # with the date boundary of the source's applicable jurisdiction.
    return _timestamp(value).astimezone(REFERENCE_TIMEZONE).date().isoformat()


def _pages(value):
    if (not isinstance(value, list) or not value or len(value) > MAX_SELECTED_PAGES
            or any(type(page) is not int or page < 1 for page in value)
            or value != sorted(set(value))):
        raise OfficialGuidanceEvidenceError('sorted unique positive bounded pages required')
    return value


def _official_url(url, *, pdf=False, provider=PROVIDER):
    source = _source(provider)
    if provider == EU_SUP_PROVIDER:
        # The publication office's normal download link uses a query. Allow
        # only the two observed documents, never arbitrary portal parameters,
        # languages, publication IDs or redirects. Existing providers keep
        # their query-free HTTPS contract.
        allowed = {EU_SUP_PDF_URL, EU_SUP_ORIGINAL_PDF_URL} if pdf else {EU_SUP_INDEX_URL}
        if _text(url) not in allowed:
            raise OfficialGuidanceEvidenceError('named English EU publication URL required')
        return url
    parts = urlsplit(_text(url))
    if (parts.scheme != 'https' or parts.netloc != source.hostname
            or parts.username or parts.password or parts.query or parts.fragment
            or (pdf and not re.fullmatch(source.pdf_path_pattern, parts.path))):
        raise OfficialGuidanceEvidenceError('allowlisted official HTTPS URL required')
    return url


class _OfficialRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, provider=PROVIDER):
        super().__init__()
        self.provider = provider

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _official_url(urljoin(req.full_url, newurl),
            pdf=req.full_url != _source(self.provider).index_url, provider=self.provider)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch(url, maximum, *, provider=PROVIDER):
    index_url = _source(provider).index_url
    _official_url(url, pdf=url != index_url, provider=provider)
    opener = urllib.request.build_opener(IPv4HTTPSHandler(), _OfficialRedirects(provider))
    with opener.open(urllib.request.Request(url,
            headers={'User-Agent': 'Repaso-OfficialGuidanceEvidence/1.0'}), timeout=30) as response:
        _official_url(response.url, pdf=url != index_url, provider=provider)
        data = response.read(maximum + 1)
        return OfficialGuidanceFetch(data, response.url,
            response.headers.get_content_type())


class _Index(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections = []
        self.heading = None
        self.heading_text = []
        self.section = None
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        if tag == 'h3':
            self.heading = tag
            self.heading_text = []
            self.section = None
        if tag == 'a':
            self.anchor = [dict(attrs).get('href'), []]

    def handle_data(self, data):
        if self.heading:
            self.heading_text.append(data)
        if self.anchor:
            self.anchor[1].append(data)

    def handle_endtag(self, tag):
        if tag == 'h3' and self.heading:
            title = ''.join(self.heading_text).strip()
            self.section = {'title': title, 'anchors': []}
            self.sections.append(self.section)
            self.heading = None
        if tag == 'a' and self.anchor:
            if self.section is not None:
                self.section['anchors'].append((self.anchor[0], ''.join(self.anchor[1]).strip()))
            self.anchor = None


def _current_link(data, current_as_of):
    try:
        parser = _Index()
        parser.feed(data.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as exc:
        raise OfficialGuidanceEvidenceError('official index is not valid UTF-8 HTML') from exc
    matches = [section for section in parser.sections
        if re.search(r'マンション標準管理規約[（(].*改正[）)]', section['title'])]
    if not matches:
        raise OfficialGuidanceEvidenceError('current official revision section missing')
    dated = []
    for section in matches:
        revisions = re.findall(r'(令和|平成)(元|[0-9]+)年([0-9]+)月([0-9]+)日改正', section['title'])
        if len(revisions) != 1:
            raise OfficialGuidanceEvidenceError('published revision date is missing or ambiguous')
        era, year, month, day = revisions[0]
        try:
            era_year = 1 if year == '元' else int(year)
            published = date((2018 if era == '令和' else 1988) + era_year,
                int(month), int(day))
            if (era_year < 1 or (era == '令和' and published < date(2019, 5, 1))
                    or (era == '平成' and not date(1989, 1, 8) <= published <= date(2019, 4, 30))):
                raise ValueError('date outside named era')
        except ValueError as exc:
            raise OfficialGuidanceEvidenceError('invalid published revision date') from exc
        dated.append((published, section))
    latest = max(published for published, _ in dated)
    latest_sections = [section for published, section in dated if published == latest]
    if len(latest_sections) != 1:
        raise OfficialGuidanceEvidenceError('latest official revision section must be unique')
    if latest > _date(current_as_of):
        # The current index is not a historical as-of service. Do not silently
        # fall back to an older linked edition when the latest one is future.
        raise OfficialGuidanceEvidenceError('current index revision is after requested date')
    current = latest_sections[0]
    links = [urljoin(INDEX_URL, href) for href, title in current['anchors']
        if href and re.sub(r'\s+', '', title) == '単棟型（コメント含む）']
    if len(links) != 1:
        raise OfficialGuidanceEvidenceError('current single-building PDF link must be unique')
    _official_url(links[0], pdf=True)
    return links[0], current['title'], latest.isoformat()


class _TreatyIndex(HTMLParser):
    """Read only the named treaty section, never the neighbouring overview."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.heading = None
        self.heading_text = []
        self.section = None
        self.sections = []
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        if tag in {'h1', 'h2', 'h3', 'h4'}:
            self.heading = tag
            self.heading_text = []
            self.section = None
        if tag == 'a':
            self.anchor = [dict(attrs).get('href'), []]

    def handle_data(self, data):
        if self.heading:
            self.heading_text.append(data)
        if self.anchor:
            self.anchor[1].append(data)

    def handle_endtag(self, tag):
        if tag == self.heading:
            self.section = {'title': ''.join(self.heading_text).strip(), 'anchors': []}
            self.sections.append(self.section)
            self.heading = None
        if tag == 'a' and self.anchor:
            if self.section is not None:
                self.section['anchors'].append((self.anchor[0], ''.join(self.anchor[1]).strip()))
            self.anchor = None


def _treaty_text_link(data, current_as_of, *, index_url, provider, section_title, link_pattern):
    _date(current_as_of)
    parser = _TreatyIndex()
    try:
        parser.feed(data.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as exc:
        raise OfficialGuidanceEvidenceError('official treaty index is not UTF-8 HTML') from exc
    sections = [s for s in parser.sections if s['title'] == section_title]
    if len(sections) != 1:
        raise OfficialGuidanceEvidenceError('unique official treaty section required')
    links = [(urljoin(index_url, href), title)
        for href, title in sections[0]['anchors']
        if href and re.fullmatch(link_pattern, re.sub(r'\s+', '', title))]
    if len(links) != 1:
        raise OfficialGuidanceEvidenceError('unique official treaty text link required')
    _official_url(links[0][0], pdf=True, provider=provider)
    # The index supplies no revision date. Acquisition today does not prove
    # historical completeness, applicability or the absence of amendments.
    return links[0][0], links[0][1], None


def _paris_link(data, current_as_of):
    return _treaty_text_link(data, current_as_of,
        index_url=ENV_PARIS_INDEX_URL, provider=ENV_PARIS_PROVIDER,
        section_title='パリ協定に関する基礎資料', link_pattern='パリ協定（和訳）')


def _ramsar_link(data, current_as_of):
    return _treaty_text_link(data, current_as_of,
        index_url=ENV_RAMSAR_INDEX_URL, provider=ENV_RAMSAR_PROVIDER,
        section_title='条約の内容', link_pattern=r'ラムサール条約全文の日本語版\[PDF[0-9]+KB\]')


def _basel_link(data, current_as_of):
    return _treaty_text_link(data, current_as_of,
        index_url=ENV_BASEL_INDEX_URL, provider=ENV_BASEL_PROVIDER,
        section_title='ア　条約等', link_pattern=r'和文\[PDF[0-9]+(?:\.[0-9]+)?(?:KB|MB)\]')


def _basel_reference_link(data, current_as_of):
    # This is a separately named 2016 reference collection containing excerpts,
    # never a silent replacement for the current treaty index's PDF. The common
    # receipt retains unreviewed applicability and historical completeness.
    return _treaty_text_link(data, current_as_of,
        index_url=ENV_BASEL_REFERENCE_INDEX_URL, provider=ENV_BASEL_REFERENCE_PROVIDER,
        section_title=ENV_BASEL_REFERENCE_SECTION,
        link_pattern=r'廃棄物等の越境移動等の適正化に関する検討会報告書関係資料集'
            r'\[PDF[0-9,]+(?:\.[0-9]+)?(?:KB|MB)\]')


class _CartagenaIndex(HTMLParser):
    """Keep the named table row and its PDF icon link together."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.stack.append({'text': [], 'anchors': []})
        if tag == 'a' and self.stack:
            self.stack[-1]['anchors'].append(dict(attrs).get('href'))

    def handle_data(self, data):
        for row in self.stack:
            row['text'].append(data)

    def handle_endtag(self, tag):
        if tag == 'tr' and self.stack:
            self.rows.append(self.stack.pop())


def _cartagena_link(data, current_as_of):
    _date(current_as_of)
    parser = _CartagenaIndex()
    try:
        # The official page declares Shift_JIS and uses Windows-31J extension
        # characters. Decode strictly; never erase bytes to recover a label.
        parser.feed(data.decode('cp932'))
    except (UnicodeDecodeError, ValueError) as exc:
        raise OfficialGuidanceEvidenceError('official Cartagena index is not Windows-31J HTML') from exc
    rows = [row for row in parser.rows
        if re.sub(r'\s+', '', ''.join(row['text'])).removeprefix('○') == '議定書全文（和文）']
    if len(rows) != 1:
        raise OfficialGuidanceEvidenceError('unique official Japanese protocol row required')
    links = [urljoin(BIODIC_CARTAGENA_INDEX_URL, href) for href in rows[0]['anchors']
        if href and urlsplit(urljoin(BIODIC_CARTAGENA_INDEX_URL, href)).path.lower().endswith('.pdf')]
    if len(links) != 1:
        raise OfficialGuidanceEvidenceError('unique Japanese protocol PDF icon link required')
    _official_url(links[0], pdf=True, provider=BIODIC_CARTAGENA_PROVIDER)
    label = re.sub(r'\s+', '', ''.join(rows[0]['text'])).removeprefix('○')
    return links[0], label, None


class _PublicationIndex(HTMLParser):
    """Read the publication heading and complete labels of its PDF links."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.headings = []
        self.heading = None
        self.anchors = []
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        if tag == 'h1':
            self.heading = []
        if tag == 'a':
            self.anchor = [dict(attrs).get('href'), []]

    def handle_data(self, data):
        if self.heading is not None:
            self.heading.append(data)
        if self.anchor is not None:
            self.anchor[1].append(data)

    def handle_endtag(self, tag):
        if tag == 'h1' and self.heading is not None:
            self.headings.append(''.join(self.heading))
            self.heading = None
        if tag == 'a' and self.anchor is not None:
            self.anchors.append((self.anchor[0], ''.join(self.anchor[1])))
            self.anchor = None


def _forest_reference_link(data, current_as_of):
    _date(current_as_of)
    parser = _PublicationIndex()
    try:
        parser.feed(data.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as exc:
        raise OfficialGuidanceEvidenceError('official reference index is not UTF-8 HTML') from exc
    heading = '平成24年版環境・循環型社会・生物多様性白書(PDF版)'
    if [re.sub(r'\s+', '', text) for text in parser.headings] != [heading]:
        raise OfficialGuidanceEvidenceError('named official publication required')
    links = [(urljoin(ENV_FOREST_REFERENCE_INDEX_URL, href), label.strip())
        for href, label in parser.anchors if href and re.fullmatch(
            r'語句説明\[PDF[0-9,]+(?:\.[0-9]+)?(?:KB|MB)\]', re.sub(r'\s+', '', label))]
    if len(links) != 1:
        raise OfficialGuidanceEvidenceError('unique official glossary PDF link required')
    _official_url(links[0][0], pdf=True, provider=ENV_FOREST_REFERENCE_PROVIDER)
    # A historical government explanation, not the declaration's full text or
    # a current statutory edition. The index gives no precise publication day.
    return links[0][0], heading + ' / ' + links[0][1], None


def _eu_sup_link(data, current_as_of):
    if _date(current_as_of) < date(2026, 8, 12):
        raise OfficialGuidanceEvidenceError('named EU consolidation is after requested date')
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise OfficialGuidanceEvidenceError('plain official RDF metadata required')
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise OfficialGuidanceEvidenceError('official publication RDF required') from exc
    rdf = '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}'
    cdm = '{http://publications.europa.eu/ontology/cdm#}'
    owl = '{http://www.w3.org/2002/07/owl#}'
    if root.tag != rdf + 'RDF':
        raise OfficialGuidanceEvidenceError('official publication RDF root required')
    nodes = {}
    for node in root:
        subject = node.get(rdf + 'about')
        if node.tag != rdf + 'Description' or not subject or subject in nodes:
            raise OfficialGuidanceEvidenceError('unique RDF publication subjects required')
        nodes[subject] = node

    def values(node, property):
        return [child.get(rdf + 'resource', child.text or '')
            for child in node.findall(property)]

    work = 'http://publications.europa.eu/resource/consolidation/2019L0904%2F20260812_0010010'
    work_node = nodes.get(work)
    if (work_node is None or values(work_node, cdm + 'act_consolidated_date') != ['2026-08-12']
            or values(work_node, cdm + 'act_consolidated_based_on_resource_legal')
            != ['http://publications.europa.eu/resource/celex/32019L0904']):
        raise OfficialGuidanceEvidenceError('named directive consolidation identity differs')
    expressions = [(subject, node) for subject, node in nodes.items()
        if values(node, cdm + 'expression_belongs_to_work') == [work]
        and values(node, cdm + 'expression_uses_language')
        == ['http://publications.europa.eu/resource/authority/language/ENG']]
    if len(expressions) != 1:
        raise OfficialGuidanceEvidenceError('unique English EU expression required')
    expression, node = expressions[0]
    titles = values(node, cdm + 'expression_title')
    if len(titles) != 1 or ' '.join(titles[0].split()) != EU_SUP_TITLE:
        raise OfficialGuidanceEvidenceError('actual English directive title differs')
    manifestations = [subject for subject, node in nodes.items()
        if values(node, cdm + 'manifestation_manifests_expression') == [expression]
        and values(node, cdm + 'manifestation_type') == ['pdfa2a']]
    if len(manifestations) != 1:
        raise OfficialGuidanceEvidenceError('unique English PDF manifestation required')
    aliases = [subject for subject, node in nodes.items()
        if values(node, owl + 'sameAs') == manifestations]
    if aliases != [EU_SUP_PDF_URL.rsplit('/', 1)[0].replace('https:', 'http:')]:
        raise OfficialGuidanceEvidenceError('unique English PDF Cellar identity required')
    items = [subject for subject, node in nodes.items()
        if values(node, cdm + 'item_belongs_to_manifestation') == aliases
        and values(node, cdm + 'item_identifier') == ['DOC_1']]
    if len(items) != 1:
        raise OfficialGuidanceEvidenceError('unique English PDF item required')
    url = items[0].replace('http://publications.europa.eu/',
        'https://publications.europa.eu/', 1)
    _official_url(url, pdf=True, provider=EU_SUP_PROVIDER)
    if url != EU_SUP_PDF_URL:
        raise OfficialGuidanceEvidenceError('current EU item must be the named consolidation')
    # Fixed consolidation, not latest-edition discovery. The PDF independently
    # confirms the date; retrieval/date-of-consolidation is not publication day.
    return url, 'Directive (EU) 2019/904 — consolidation as at 12 August 2026', None


@dataclass(frozen=True)
class _Source:
    index_url: str
    hostname: str
    pdf_path_pattern: str
    label_origin: str
    source_kind: str
    select: Callable


_SOURCES = {
    PROVIDER: _Source(INDEX_URL, 'www.mlit.go.jp', r'/(?:[A-Za-z0-9_-]+/)*[0-9]+\.pdf',
        'actual_current_index_heading', SOURCE_KIND, _current_link),
    ENV_PARIS_PROVIDER: _Source(ENV_PARIS_INDEX_URL, 'www.env.go.jp', r'/content/[0-9]+\.pdf',
        'actual_official_index_link', 'official_treaty_text', _paris_link),
    ENV_RAMSAR_PROVIDER: _Source(ENV_RAMSAR_INDEX_URL, 'www.env.go.jp',
        r'/nature/ramsar/conv/treaty/[A-Za-z0-9_-]+\.pdf',
        'actual_official_index_link', 'official_treaty_text', _ramsar_link),
    BIODIC_CARTAGENA_PROVIDER: _Source(BIODIC_CARTAGENA_INDEX_URL, 'www.biodic.go.jp',
        r'/bch/download/protocol/protocol_j(?:_[A-Za-z0-9]+)*\.pdf',
        'actual_official_index_link', 'official_treaty_text', _cartagena_link),
    ENV_BASEL_PROVIDER: _Source(ENV_BASEL_INDEX_URL, 'www.env.go.jp',
        r'/recycle/yugai/law/(?:h[0-9]+)?conv_j(?:_[A-Za-z0-9]+)*\.pdf',
        'actual_official_index_link', 'official_treaty_text', _basel_link),
    ENV_BASEL_REFERENCE_PROVIDER: _Source(ENV_BASEL_REFERENCE_INDEX_URL, 'www.env.go.jp',
        r'/content/[0-9]+\.pdf', 'actual_official_index_link',
        'official_treaty_excerpt', _basel_reference_link),
    ENV_FOREST_REFERENCE_PROVIDER: _Source(ENV_FOREST_REFERENCE_INDEX_URL, 'www.env.go.jp',
        r'/policy/hakusyo/h24/pdf/4_term\.pdf', 'actual_official_index_link',
        SOURCE_KIND, _forest_reference_link),
    EU_SUP_PROVIDER: _Source(EU_SUP_INDEX_URL, 'op.europa.eu',
        r'/resource/cellar/[^/]+/DOC_1', 'actual_official_rdf_and_pdf_identity',
        'official_consolidated_eu_act', _eu_sup_link),
}
PROVIDERS = tuple(_SOURCES)


def _source(provider):
    if not isinstance(provider, str) or provider not in _SOURCES:
        raise OfficialGuidanceEvidenceError('registered official primary-document provider required')
    return _SOURCES[provider]


def _extract(data, pages):
    # PDFium correctly decodes Adobe-Japan1 PDFs which some other readers render
    # as blank pages. No network, scripts or links in PDFs are executed here.
    import pypdfium2 as pdfium
    document = pdfium.PdfDocument(data)
    try:
        if max(pages) > len(document):
            raise OfficialGuidanceEvidenceError('selected PDF page outside document')
        result = []
        for number in pages:
            page = document[number - 1]
            try:
                text_page = page.get_textpage()
                try:
                    text = text_page.get_text_range()
                finally:
                    text_page.close()
            finally:
                page.close()
            result.append({'page': number, 'text': text})
        return result
    finally:
        document.close()


def _binding(record, scope):
    _exact(scope, {'qualification', 'listGroupId', 'questionId', 'sourceBinding', 'sourceSha256'}, 'scope')
    _exact(scope['sourceBinding'], {'sourceQuestionKey', 'reviewQuestionId', 'sourceRecordRef'}, 'source binding')
    for key in ('qualification', 'listGroupId', 'questionId'):
        _text(scope[key])
    for value in scope['sourceBinding'].values():
        _text(value)
    if not re.fullmatch(r'[a-f0-9]{64}', str(scope['sourceSha256'])):
        raise OfficialGuidanceEvidenceError('preserved source bytes hash required')
    if scope['sourceBinding']['sourceQuestionKey'] != ':'.join((scope['qualification'], scope['listGroupId'],
            scope['sourceBinding']['reviewQuestionId'])):
        raise OfficialGuidanceEvidenceError('source binding qualification/group differs')
    body = record.get('questionBodyText')
    if body is None:
        body = record.get('originalQuestionBodyText')
    _text(body)
    choices = record.get('choiceTextList')
    if not isinstance(choices, list) or not choices or len(choices) > 30:
        raise OfficialGuidanceEvidenceError('complete choice list required')
    for choice in choices:
        _text(choice)
    protected = {'questionBodyText': body, 'choiceTextList': choices, 'scope': dict(scope)}
    return _json_hash(protected), [_hash(choice.encode('utf-8')) for choice in choices]


def validate_request(request, *, record, scope):
    _exact(request, {'schemaVersion', 'provider', 'currentAsOf', 'examAsOf', 'currentPages',
        'historicalDocuments', 'choiceBindings'}, 'official guidance request')
    if request['schemaVersion'] != REQUEST_SCHEMA:
        raise OfficialGuidanceEvidenceError('dedicated official guidance request required')
    _source(request['provider'])
    current = _date(request['currentAsOf'])
    if request['examAsOf'] is not None and _date(request['examAsOf']) > current:
        raise OfficialGuidanceEvidenceError('exam date after current date')
    _pages(request['currentPages'])
    historical = request['historicalDocuments']
    if not isinstance(historical, list) or len(historical) > 6:
        raise OfficialGuidanceEvidenceError('bounded historical documents required')
    urls = []
    total_pages = len(request['currentPages'])
    for document in historical:
        _exact(document, {'sourceUrl', 'revisionLabel', 'pages'}, 'historical document')
        urls.append(_official_url(document['sourceUrl'], pdf=True, provider=request['provider']))
        _text(document['revisionLabel'])
        total_pages += len(_pages(document['pages']))
    if len(set(urls)) != len(urls) or total_pages > MAX_SELECTED_PAGES:
        raise OfficialGuidanceEvidenceError('duplicate documents or excessive selected pages')
    record_hash, choice_hashes = _binding(record, scope)
    bindings = request['choiceBindings']
    if not isinstance(bindings, list) or len(bindings) != len(choice_hashes):
        raise OfficialGuidanceEvidenceError('every full choice requires its own binding')
    current_used, historical_used = set(), [set() for _ in historical]
    for index, binding in enumerate(bindings):
        _exact(binding, {'choiceIndex', 'currentPages', 'historicalSelections'}, 'choice binding')
        if type(binding['choiceIndex']) is not int or binding['choiceIndex'] != index:
            raise OfficialGuidanceEvidenceError('choice binding omitted, duplicated or reordered')
        pages = _pages(binding['currentPages'])
        if not set(pages).issubset(request['currentPages']):
            raise OfficialGuidanceEvidenceError('choice page outside current selection')
        current_used.update(pages)
        selections = binding['historicalSelections']
        if not isinstance(selections, list):
            raise OfficialGuidanceEvidenceError('historical selections must be an array')
        seen = set()
        for selection in selections:
            _exact(selection, {'documentIndex', 'pages'}, 'historical selection')
            number = selection['documentIndex']
            if type(number) is not int or not 0 <= number < len(historical) or number in seen:
                raise OfficialGuidanceEvidenceError('invalid or duplicate historical document binding')
            seen.add(number)
            if not set(_pages(selection['pages'])).issubset(historical[number]['pages']):
                raise OfficialGuidanceEvidenceError('choice page outside historical selection')
            historical_used[number].update(selection['pages'])
    if current_used != set(request['currentPages']) or any(used != set(doc['pages'])
            for used, doc in zip(historical_used, historical)):
        raise OfficialGuidanceEvidenceError('selected page lacks full-choice attribution')
    return record_hash, choice_hashes


class OfficialGuidanceEvidenceResolver:
    def __init__(self, repo_root, *, fetcher=None, extractor=None, now=None):
        self.root = Path(repo_root).resolve()
        self.cache = self.root / 'output/question_review_console/cache/official_guidance_evidence'
        self.fetcher = fetcher
        self.extractor = extractor or _extract
        self.now = now or (lambda: datetime.now(timezone.utc).isoformat())
        self.last_refresh_receipt = None

    def _blob_path(self, digest):
        if not re.fullmatch(r'[a-f0-9]{64}', str(digest)):
            raise OfficialGuidanceEvidenceError('invalid source byte hash')
        path = self.cache / (digest + '.bin')
        if path != path.resolve() or self.cache != self.cache.resolve():
            raise OfficialGuidanceEvidenceError('physical private evidence cache required')
        return path

    def _store(self, data):
        digest = _hash(data)
        path = self._blob_path(digest)
        self.cache.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.cache, 0o700)
        if path.exists():
            if path.read_bytes() != data:
                raise OfficialGuidanceEvidenceError('content-addressed cache conflict')
            return digest
        descriptor, temporary = tempfile.mkstemp(prefix='.retrieval-', dir=self.cache)
        try:
            with os.fdopen(descriptor, 'wb') as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != data:
                    raise OfficialGuidanceEvidenceError('concurrent evidence cache conflict')
        finally:
            os.unlink(temporary)
        return digest

    def _bytes(self, digest):
        data = self._blob_path(digest).read_bytes()
        if _hash(data) != digest:
            raise OfficialGuidanceEvidenceError('official source cache bytes changed')
        return data

    def _get(self, url, *, pdf, provider=PROVIDER):
        _official_url(url, pdf=pdf, provider=provider)
        maximum = MAX_PDF_BYTES if pdf else MAX_INDEX_BYTES
        fetched = (self.fetcher(url, maximum) if self.fetcher is not None
            else _fetch(url, maximum, provider=provider))
        if not isinstance(fetched, OfficialGuidanceFetch) or not isinstance(fetched.data, bytes):
            raise OfficialGuidanceEvidenceError('actual retrieval response required')
        _official_url(fetched.resolved_url, pdf=pdf, provider=provider)
        expected = ('application/pdf' if pdf else
            'application/rdf+xml' if provider == EU_SUP_PROVIDER else 'text/html')
        if (not fetched.data or len(fetched.data) > maximum or fetched.content_type != expected
                or (pdf and not fetched.data.startswith(b'%PDF-'))):
            raise OfficialGuidanceEvidenceError('official response type/size/content differs')
        self._document_identity(provider, url, fetched.data, pdf=pdf)
        self._store(fetched.data)
        return fetched

    def _document_identity(self, provider, url, data, *, pdf):
        if provider != EU_SUP_PROVIDER or not pdf:
            return
        first = ' '.join(self._selection(data, [1])[0]['text'].split())
        if 'DIRECTIVE (EU) 2019/904' not in first.upper():
            raise OfficialGuidanceEvidenceError('actual EU directive title missing from PDF')
        if url == EU_SUP_PDF_URL:
            if not re.search(r'02019L0904\s*[—-]\s*EN\s*[—-]\s*12\.08\.2026', first):
                raise OfficialGuidanceEvidenceError('actual English EU consolidation date differs')
        elif ('02019L0904' in first or '5 June 2019' not in first
                or 'Official Journal of the European Union' not in first):
            raise OfficialGuidanceEvidenceError('actual original Official Journal text required')

    def _selection(self, data, pages):
        extracted = self.extractor(data, pages)
        if (not isinstance(extracted, list) or len(extracted) != len(pages)
                or [item.get('page') for item in extracted] != pages):
            raise OfficialGuidanceEvidenceError('actual selected PDF pages differ')
        result = []
        for item in extracted:
            _exact(item, {'page', 'text'}, 'extracted page')
            text = _text(item['text'])
            if len(text.encode('utf-8')) > MAX_TEXT_BYTES:
                raise OfficialGuidanceEvidenceError('selected PDF text exceeds bound')
            result.append({'page': item['page'], 'text': text, 'textHash': _hash(text.encode('utf-8'))})
        return result

    def resolve(self, request, *, record, scope):
        record_hash, choices = validate_request(request, record=record, scope=scope)
        source = _source(request['provider'])
        if request['currentAsOf'] != _reference_date(self.now()):
            raise OfficialGuidanceEvidenceError('current official guidance requires actual current date')
        index = self._get(source.index_url, pdf=False, provider=request['provider'])
        url, revision, published = source.select(index.data, request['currentAsOf'])
        specifications = [{'kind': 'current', 'sourceUrl': url, 'revisionLabel': revision,
            'revisionLabelOrigin': source.label_origin, 'pages': request['currentPages']}]
        specifications += [{'kind': 'historical', **document,
            'revisionLabelOrigin': 'declared_unreviewed'} for document in request['historicalDocuments']]
        if len({item['sourceUrl'] for item in specifications}) != len(specifications):
            raise OfficialGuidanceEvidenceError('current and historical source URLs must differ')
        documents = []
        for spec in specifications:
            fetched = self._get(spec['sourceUrl'], pdf=True, provider=request['provider'])
            selected = self._selection(fetched.data, spec['pages'])
            documents.append({**spec, 'pages': selected, 'resolvedUrl': fetched.resolved_url,
                'byteHash': _hash(fetched.data), 'pageSelectionHash': _json_hash(selected)})
        if sum(len(page['text'].encode('utf-8')) for doc in documents for page in doc['pages']) > MAX_TEXT_BYTES:
            raise OfficialGuidanceEvidenceError('selected official guidance text exceeds total bound')
        content = {'schemaVersion': SCHEMA_VERSION, 'sourceKind': source.source_kind, 'provider': request['provider'],
            'retrievalStatus': 'retrieved',
            'scope': dict(scope), 'requestHash': _json_hash(request), 'recordHash': record_hash,
            'choiceHashes': choices, 'currentAsOf': request['currentAsOf'], 'examAsOf': request['examAsOf'],
            'currentIndex': {'sourceUrl': source.index_url, 'resolvedUrl': index.resolved_url,
                'byteHash': _hash(index.data), 'currentPdfUrl': url, 'publishedRevisionDate': published},
            'documents': documents, 'choiceBindings': request['choiceBindings'],
            'applicabilityStatus': 'unreviewed', 'historicalCompleteness': 'unreviewed'}
        completed = self.now()
        if _reference_date(completed) != request['currentAsOf']:
            raise OfficialGuidanceEvidenceError('date changed during retrieval; new current request required')
        value = {**content, 'contentHash': _json_hash(content), 'retrievalCompletedAt': completed}
        value['evidenceHash'] = _json_hash(value)
        return json.loads(json.dumps(value, ensure_ascii=False))

    def validate(self, receipt, *, request, record, scope, refresh=False):
        if refresh:
            # A failed fresh check must never leave a previous successful check
            # visible as the evidence for the latest attempt.
            self.last_refresh_receipt = None
        record_hash, choices = validate_request(request, record=record, scope=scope)
        source = _source(request['provider'])
        expected_fields = {'schemaVersion', 'sourceKind', 'provider', 'scope', 'requestHash', 'recordHash',
            'choiceHashes', 'currentAsOf', 'examAsOf', 'currentIndex', 'documents', 'choiceBindings',
            'retrievalStatus', 'applicabilityStatus', 'historicalCompleteness', 'contentHash', 'retrievalCompletedAt', 'evidenceHash'}
        _exact(receipt, expected_fields, 'official guidance receipt')
        content = {key: value for key, value in receipt.items()
            if key not in {'contentHash', 'retrievalCompletedAt', 'evidenceHash'}}
        checks = {
            'schemaVersion': receipt['schemaVersion'] == SCHEMA_VERSION,
            'sourceKind': receipt['sourceKind'] == source.source_kind,
            'provider': receipt['provider'] == request['provider'],
            'retrievalStatus': receipt['retrievalStatus'] == 'retrieved',
            'scope': receipt['scope'] == scope,
            'requestHash': receipt['requestHash'] == _json_hash(request),
            'recordHash': receipt['recordHash'] == record_hash,
            'choiceHashes': receipt['choiceHashes'] == choices,
            'currentAsOf': receipt['currentAsOf'] == request['currentAsOf'],
            'examAsOf': receipt['examAsOf'] == request['examAsOf'],
            'choiceBindings': receipt['choiceBindings'] == request['choiceBindings'],
            'applicabilityStatus': receipt['applicabilityStatus'] == 'unreviewed',
            'historicalCompleteness': receipt['historicalCompleteness'] == 'unreviewed',
            'contentHash': receipt['contentHash'] == _json_hash(content),
            'evidenceHash': receipt['evidenceHash'] == _json_hash({k: v for k, v in receipt.items() if k != 'evidenceHash'}),
        }
        if not all(checks.values()):
            # Fixed field names only; never print protected values or model text.
            raise OfficialGuidanceEvidenceError('official guidance receipt binding/hash differs: '
                + ','.join(key for key, valid in checks.items() if not valid))
        if _reference_date(receipt['retrievalCompletedAt']) != receipt['currentAsOf']:
            raise OfficialGuidanceEvidenceError('retrieval date differs from current-as-of')
        index = receipt['currentIndex']
        _exact(index, {'sourceUrl', 'resolvedUrl', 'byteHash', 'currentPdfUrl', 'publishedRevisionDate'}, 'index receipt')
        if index['sourceUrl'] != source.index_url:
            raise OfficialGuidanceEvidenceError('fixed official index required')
        _official_url(index['resolvedUrl'], provider=request['provider'])
        url, revision, published = source.select(self._bytes(index['byteHash']), receipt['currentAsOf'])
        if index['currentPdfUrl'] != url or index['publishedRevisionDate'] != published:
            raise OfficialGuidanceEvidenceError('official index link/revision changed')
        documents = receipt['documents']
        if not isinstance(documents, list) or len(documents) != 1 + len(request['historicalDocuments']):
            raise OfficialGuidanceEvidenceError('complete official document receipts required')
        for number, document in enumerate(documents):
            _exact(document, {'kind', 'sourceUrl', 'resolvedUrl', 'byteHash', 'revisionLabel',
                'revisionLabelOrigin', 'pages', 'pageSelectionHash'}, 'document receipt')
            expected_url = url if number == 0 else request['historicalDocuments'][number - 1]['sourceUrl']
            expected_revision = revision if number == 0 else request['historicalDocuments'][number - 1]['revisionLabel']
            pages = request['currentPages'] if number == 0 else request['historicalDocuments'][number - 1]['pages']
            if (document['sourceUrl'] != expected_url or document['revisionLabel'] != expected_revision
                    or document['kind'] != ('current' if number == 0 else 'historical')
                    or document['revisionLabelOrigin'] != (source.label_origin if number == 0 else 'declared_unreviewed')):
                raise OfficialGuidanceEvidenceError('official document source/revision binding differs')
            _official_url(document['sourceUrl'], pdf=True, provider=request['provider'])
            _official_url(document['resolvedUrl'], pdf=True, provider=request['provider'])
            raw = self._bytes(document['byteHash'])
            if not raw.startswith(b'%PDF-') or len(raw) > MAX_PDF_BYTES:
                raise OfficialGuidanceEvidenceError('official PDF bytes/type differ')
            self._document_identity(request['provider'], document['sourceUrl'], raw, pdf=True)
            selected = self._selection(raw, pages)
            if document['pages'] != selected or document['pageSelectionHash'] != _json_hash(selected):
                raise OfficialGuidanceEvidenceError('official PDF selected page bytes/text changed')
        if sum(len(page['text'].encode('utf-8')) for doc in documents for page in doc['pages']) > MAX_TEXT_BYTES:
            raise OfficialGuidanceEvidenceError('selected official guidance text exceeds total bound')
        if refresh:
            self.last_refresh_receipt = self.refresh(receipt, request=request, record=record, scope=scope)
        return json.loads(json.dumps(receipt, ensure_ascii=False))

    def refresh(self, receipt, *, request, record, scope):
        """Return actual fresh acquisition evidence without changing old approval.

        Consumers must persist this result in their publication/save execution
        artifact. It is not itself a content approval or publication receipt.
        """
        self.last_refresh_receipt = None
        self.validate(receipt, request=request, record=record, scope=scope, refresh=False)
        fresh = self.resolve(request, record=record, scope=scope)
        if fresh['contentHash'] != receipt['contentHash']:
            raise OfficialGuidanceEvidenceError('fresh official index/PDF/page/revision differs; new audit required')
        value = {'schemaVersion': 'official-guidance-refresh/v1',
            'pinnedEvidenceHash': receipt['evidenceHash'], 'contentHash': receipt['contentHash'],
            'freshEvidenceHash': fresh['evidenceHash'], 'freshReceipt': fresh,
            'checkedAt': fresh['retrievalCompletedAt']}
        value['validationHash'] = _json_hash(value)
        self.last_refresh_receipt = json.loads(json.dumps(value, ensure_ascii=False))
        return json.loads(json.dumps(value, ensure_ascii=False))
