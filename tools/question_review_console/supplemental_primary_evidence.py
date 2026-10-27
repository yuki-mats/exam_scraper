"""Acquired public factual sources for normal maintenance, never question patches.

Named sources and extraction regions are fixed here. Receipts bind original
bytes, extracted text, the preserved source and the complete current question.
Models still judge relevance, dates and correctness in the usual stages.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.request

from tools.question_review_console.http_transport import IPv4HTTPSHandler
from tools.question_review_console.official_guidance_contract import source_scope, _immutable_bytes
from tools.question_review_console.official_guidance_evidence import _binding, _extract, _reference_date
from tools.question_review_console.review_store import atomic_write

FIELD = 'supplementalPrimaryEvidence'
SCHEMA = 'normal-supplemental-primary-evidence/v1'
CACHE = Path('output/question_review_console/supplemental_primary_cache')
MAX_BYTES = 8_000_000
MAX_TEXT_BYTES = 48_000


@dataclass(frozen=True)
class Source:
    url: str
    title: str
    region: str | tuple[int, ...]
    temporal_scope: str


SOURCES = {
    'maff_food_recycling_targets': Source(
        'https://www.maff.go.jp/j/shokusan/recycle/syokuhin/s_info/saiseiriyo_mokuhyou.html',
        '食品廃棄物等の再生利用等実施率目標', '#main_content',
        '掲載された方針・目標年度を本文で確認する。取得日を方針改訂日としない。'),
    'env_construction_recycling_2008_review': Source(
        'https://www.env.go.jp/press/files/jp/12743.pdf',
        '建設リサイクル制度の施行状況の評価・検討について とりまとめ', (1, 4, 7),
        '2008年12月の歴史的な施行状況評価。現在の統計又は法令本文ではない。'),
    'env_vehicle_recycling_overview': Source(
        'https://www.env.go.jp/recycle/car/gaiyo.html',
        '自動車リサイクルの概要', 'main#main',
        '法律制定の背景・制定年・完全施行時期の説明。現在の廃棄割合とは区別する。'),
    'mlit_vehicle_tax_2026_result': Source(
        'https://www.mlit.go.jp/jidosha/content/001993968.pdf',
        '令和8年度税制改正結果概要（車体課税関係）', (1, 5, 6, 11),
        '2026年度の改正結果。大綱・結果概要と施行済み法令本文は別に照合し、要望段階の資料と混同しない。'),
    'mlit_road_traffic_terms': Source(
        'https://www.ktr.mlit.go.jp/syuto/syuto_yougo001.html',
        '首都国道事務所 みちに関する用語辞典 道づくり',
        '#main_content table:-soup-contains("ロードプライシング")',
        'ITS・TDM・ロードプライシング等の用語定義。掲載された過去の事業進捗を現在の実績としない。'),
    'ntsel_modal_shift_definition': Source(
        'https://www.ntsel.go.jp/Portals/0/resources/report/files16/ronbungaiyo16.pdf',
        '交通安全環境研究所 論文等概要 モーダルシフト（大野寛之、2011年5月）', (102,),
        'PDF第102頁（紙面110頁）の旅客・貨物の輸送手段転換の定義。2011年の普及状況・排出割合を現在の統計としない。'),
}


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def _path(root, scope):
    parts = [scope['qualification'], scope['listGroupId'], scope['questionId']]
    if any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', v) for v in parts):
        raise ValueError('single safe source identity required')
    return (root / 'output/question_review_console' / parts[0] / parts[1]
        / 'supplemental_primary_inputs' / (parts[2] + '.json'))


def _physical(path, root):
    if path != path.resolve() or not path.is_relative_to(root):
        raise ValueError('physical private evidence path required')


def _raw(root, ref):
    root = Path(root).resolve()
    if not isinstance(ref, dict) or set(ref) != {'ref', 'sha256'}:
        raise ValueError('exact private evidence reference required')
    path = root / ref['ref']
    _physical(path, root)
    if path.parent != root / CACHE or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('bounded supplemental cache file required')
    raw = path.read_bytes()
    if _hash(raw) != ref['sha256']:
        raise ValueError('supplemental evidence bytes changed')
    return raw


def _store(root, raw, suffix):
    if len(raw) > MAX_BYTES:
        raise ValueError('supplemental evidence exceeds byte limit')
    path = root / CACHE / (_hash(raw) + suffix)
    _physical(path, root)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _immutable_bytes(path, raw)
    return {'ref': str(path.relative_to(root)), 'sha256': _hash(raw)}


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if newurl != req.full_url:
            raise ValueError('named source URL changed; review source catalog first')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch(url):
    if url not in {s.url for s in SOURCES.values()}:
        raise ValueError('registered public primary source required')
    opener = urllib.request.build_opener(IPv4HTTPSHandler(), _Redirects())
    with opener.open(urllib.request.Request(url,
            headers={'User-Agent': 'Repaso-SupplementalPrimaryEvidence/1'}), timeout=40) as response:
        if response.url != url or response.status != 200:
            raise ValueError('actual named official source acquisition required')
        raw = response.read(MAX_BYTES + 1)
        mime = response.headers.get_content_type()
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError('empty or oversized official source')
    return raw, mime


def _extract_source(source_id, raw, mime):
    source = SOURCES[source_id]
    if isinstance(source.region, tuple):
        if mime != 'application/pdf' or not raw.startswith(b'%PDF'):
            raise ValueError('actual PDF required for named PDF source')
        spans = _extract(raw, list(source.region))
        spec = {'format': 'pdf_pages', 'pages': list(source.region), 'version': 'pdfium/v1'}
    else:
        if mime != 'text/html' or raw.startswith(b'%PDF'):
            raise ValueError('actual HTML required for named HTML source')
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(raw.decode('utf-8-sig'), 'html.parser')
        nodes = soup.select(source.region)
        if len(nodes) != 1:
            raise ValueError('named official HTML region missing or ambiguous')
        node = nodes[0]
        for child in node.select('script, style, nav'):
            child.decompose()
        spans = [{'selector': source.region, 'text': node.get_text('\n', strip=True)}]
        spec = {'format': 'html_region', 'selector': source.region, 'version': 'bs4-utf8-lines/v1'}
    if any(not str(s.get('text', '')).strip() for s in spans):
        raise ValueError('empty primary-source span')
    if sum(len(s['text'].encode('utf-8')) for s in spans) > MAX_TEXT_BYTES:
        raise ValueError('source spans exceed limit; do not truncate')
    return {'sourceId': source_id, 'sourceUrl': source.url, 'title': source.title,
        'temporalScope': source.temporal_scope, 'contentType': mime, 'extraction': spec,
        'spans': spans, 'extractedTextSha256': _hash(_bytes(spans))}


def register(root, qualification, target, record, source_ids, *, replace_if_sha=None, fetcher=None):
    root = Path(root).resolve()
    path = _path(root, source_scope(root, qualification, target))
    _physical(path, root)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = path.parent / '.prepare.lock'
    _physical(lock, root)
    with lock.open('a+b') as stream:
        os.chmod(lock, 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX)
        return _register(root, qualification, target, record, source_ids,
            replace_if_sha=replace_if_sha, fetcher=fetcher)


def _register(root, qualification, target, record, source_ids, *, replace_if_sha=None, fetcher=None):
    root = Path(root).resolve()
    scope = source_scope(root, qualification, target)
    fingerprint, choice_hashes = _binding(record, scope)
    if (not isinstance(source_ids, list) or not 1 <= len(source_ids) <= 8
            or len(set(source_ids)) != len(source_ids) or any(s not in SOURCES for s in source_ids)):
        raise ValueError('unique registered supplemental sources required')
    path = _path(root, scope)
    _physical(path, root)
    before = path.read_bytes() if path.exists() else None
    if (before is None and replace_if_sha is not None) or (before is not None and _hash(before) != replace_if_sha):
        raise ValueError('inspect existing input and supply exact prior SHA before replacement')
    documents = []
    for source_id in source_ids:
        raw, mime = (fetcher or _fetch)(SOURCES[source_id].url)
        if not raw or len(raw) > MAX_BYTES:
            raise ValueError('bounded actual source bytes required')
        doc = _extract_source(source_id, raw, mime)
        doc['rawRef'] = _store(root, raw, '.raw')
        documents.append(doc)
    if sum(len(s['text'].encode('utf-8')) for d in documents for s in d['spans']) > MAX_TEXT_BYTES:
        raise ValueError('complete supplemental input exceeds text limit')
    acquired = datetime.now(timezone.utc).isoformat()
    value = {'schemaVersion': SCHEMA, 'scope': scope, 'recordFingerprint': fingerprint,
        'choiceHashes': choice_hashes, 'acquiredAt': acquired, 'currentAsOf': _reference_date(acquired),
        'applicabilityStatus': 'unreviewed', 'documents': documents}
    value['inputHash'] = _hash(_bytes(value))
    validate(root, value, record=record, question_id=scope['questionId'])
    if source_scope(root, qualification, target) != scope:
        raise ValueError('preserved source changed during acquisition')
    ref = _store(root, _bytes(value), '.json')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _physical(path, root)
    if (path.read_bytes() if path.exists() else None) != before:
        raise ValueError('supplemental input changed during registration')
    if before is not None:
        _store(root, before, '.prior-input.json')
    atomic_write(path, _bytes(ref).decode('utf-8'))
    os.chmod(path, 0o600)
    return {'path': str(path), 'sha256': _hash(path.read_bytes()),
        'receiptRef': ref, 'inputHash': value['inputHash'], 'applicabilityStatus': 'unreviewed'}


def validate(root, value, *, record=None, question_id=None, current_as_of=None, require_selected=False):
    root = Path(root).resolve()
    fields = {'schemaVersion', 'scope', 'recordFingerprint', 'choiceHashes', 'acquiredAt',
        'currentAsOf', 'applicabilityStatus', 'documents', 'inputHash'}
    if (not isinstance(value, dict) or set(value) != fields or value.get('schemaVersion') != SCHEMA
            or value.get('applicabilityStatus') != 'unreviewed'
            or value.get('inputHash') != _hash(_bytes({k: v for k, v in value.items() if k != 'inputHash'}))
            or value.get('currentAsOf') != _reference_date(value.get('acquiredAt'))
            or (current_as_of is not None and value.get('currentAsOf') != current_as_of)):
        raise ValueError('supplemental input schema, date or content hash differs')
    scope = value['scope']
    if question_id is not None and scope['questionId'] != question_id:
        raise ValueError('supplemental evidence belongs to another question')
    target = {'id': scope['questionId'], 'listGroupId': scope['listGroupId'], **scope['sourceBinding']}
    if source_scope(root, scope['qualification'], target) != scope:
        raise ValueError('supplemental preserved source binding changed')
    if record is not None and _binding(record, scope) != (value['recordFingerprint'], value['choiceHashes']):
        raise ValueError('whole question or one of its choices changed')
    docs = value['documents']
    if not isinstance(docs, list) or not 1 <= len(docs) <= 8:
        raise ValueError('bounded acquired documents required')
    ids = [doc['sourceId'] for doc in docs]
    if len(set(ids)) != len(ids) or any(s not in SOURCES for s in ids):
        raise ValueError('registered unique source catalog entries required')
    for doc in docs:
        raw = _raw(root, doc['rawRef'])
        expected = _extract_source(doc['sourceId'], raw, doc['contentType'])
        if {k: v for k, v in doc.items() if k != 'rawRef'} != expected:
            raise ValueError('source URL, extraction or delivered text differs from acquired bytes')
    if sum(len(s['text'].encode('utf-8')) for d in docs for s in d['spans']) > MAX_TEXT_BYTES:
        raise ValueError('complete supplemental input exceeds text limit')
    if require_selected:
        path = _path(root, scope)
        _physical(path, root)
        if not path.is_file() or json.loads(_raw(root, json.loads(path.read_text()))) != value:
            raise ValueError('supplemental acquisition selection changed after preparation')
    return copy.deepcopy(value)


def load(root, qualification, target, record, *, current_as_of):
    root = Path(root).resolve()
    path = _path(root, source_scope(root, qualification, target))
    if not path.exists():
        return None
    _physical(path, root)
    value = json.loads(_raw(root, json.loads(path.read_text())))
    return validate(root, value, record=record, question_id=target.get('id') or target.get('uiQuestionId'),
        current_as_of=current_as_of, require_selected=True)


def delivery(root, inputs, prompt, questions, receipt=None, *, thread_id=None, turn_id=None):
    actual = {qid: q[FIELD] for qid, q in questions.items() if FIELD in q}
    if actual != inputs:
        raise ValueError('actual native prompt omitted or changed supplemental source text')
    for qid, value in inputs.items():
        validate(root, value, question_id=qid, require_selected=True)
    expected = {'inputsHash': _hash(_bytes(inputs)), 'promptHash': _hash(prompt.encode('utf-8'))}
    if receipt is None:
        if not thread_id or not turn_id:
            raise ValueError('actual supplemental native thread and turn required')
        return {**expected, 'threadId': thread_id, 'turnId': turn_id}
    if (any(receipt.get(k) != v for k, v in expected.items())
            or not receipt.get('threadId') or not receipt.get('turnId')):
        raise ValueError('supplemental native delivery proof differs')
    return copy.deepcopy(receipt)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--qualification', required=True)
    parser.add_argument('--list-group-id', required=True)
    parser.add_argument('--source-record-ref', required=True)
    parser.add_argument('--source', choices=tuple(SOURCES), action='append', required=True)
    parser.add_argument('--replace-if-sha')
    args = parser.parse_args(argv)
    from tools.question_review_console.inventory import QuestionInventory
    matches = [q for q in QuestionInventory(args.repo_root).group(args.qualification,
        args.list_group_id)['questions'] if q['sourceRecordRef'] == args.source_record_ref]
    if len(matches) != 1:
        raise ValueError('one exact preserved question required')
    target = matches[0]
    print(json.dumps(register(args.repo_root, args.qualification, target, target['projected'],
        args.source, replace_if_sha=args.replace_if_sha), ensure_ascii=False))


if __name__ == '__main__':
    main()
