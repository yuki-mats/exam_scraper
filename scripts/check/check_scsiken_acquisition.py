#!/usr/bin/env python3
"""支援士の取得元HTML・問題集合・IPA正答・PDF・画像を独立に照合する。"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata
from urllib.parse import unquote, urljoin, urlparse

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from bs4 import BeautifulSoup
from bs4.element import Comment
from pypdf import PdfReader
from PIL import Image
from scripts.scrape.common import create_http_session, fetch_html_text, slow_down, download_and_save_images, extract_image_urls_from_element, is_placeholder_image_url, SUBSCRIPT_MAP, SUPERSCRIPT_MAP
from scripts.scrape.qualification_presets import build_list_first_page_url, load_scrape_preset

INDEX_URL = 'https://www.sc-siken.com/sckakomon.php'


def discover_targets(html: str) -> list[dict[str, str]]:
    targets = []
    seen = set()
    for link in BeautifulSoup(html, 'html.parser').find_all('a', href=True):
        match = re.fullmatch(r'/kakomon/(\d{2}_(haru|aki|toku))/', link['href'])
        if not match or match[1] in seen:
            continue
        token, season = match[1], match[2]
        era = int(token[:2])
        # 平成21年以降と令和の公開回。元号をリンク文字でも確認する。
        label = link.get_text(' ', strip=True)
        if '平成' in label:
            year = 1988 + era
        elif '令和' in label:
            year = 2018 + era
        elif label == '過去問題解説':
            continue
        else:
            raise ValueError(f'試験回の元号を確認できません: {label} {token}')
        group = f'{year}{"02" if season == "aki" else "01"}'
        targets.append({'source_list_group_id': token, 'output_list_group_id': group})
        seen.add(token)
    if not targets or len({t['output_list_group_id'] for t in targets}) != len(targets):
        raise ValueError('公開回が空又は出力groupが重複しています')
    return targets


def official_answers(path: Path, expected_count: int) -> dict[int, int]:
    text = '\n'.join(p.extract_text() or '' for p in PdfReader(path).pages)
    # 古いPDFでは二桁の番号が「問1 0」と抽出される。行をまたがず数字だけを結合する。
    matches = re.findall(r'問[ \t]*([0-9０-９][0-9０-９ \t]*)[ \t]*([アイウエ])', text)
    if not matches and path.is_file():
        # 一部の旧日本語CMapはpypdfで読めないため、別の抽出器で確認する。
        import pdfplumber
        with pdfplumber.open(path) as reader:
            text = '\n'.join(p.extract_text() or '' for p in reader.pages)
        matches = re.findall(r'問[ \t]*([0-9０-９][0-9０-９ \t]*)[ \t]*([アイウエ])', text)
    if not matches and path.with_suffix('.answers.json').is_file():
        # 画像PDFは目視した正答表を使う。PDFが変われば再確認が必須。
        proof = json.loads(path.with_suffix('.answers.json').read_text())
        if proof.get('reviewMethod') != 'visual_transcription' or proof.get('pdfSha256') != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError(f'画像PDFの目視確認receiptが現在のPDFと一致しません: {path}')
        matches = list(proof.get('answers', {}).items())
    answers = {}
    for number, marker in matches:
        number = int(re.sub(r'\s+', '', unicodedata.normalize('NFKC', number)))
        if number in answers:
            raise ValueError(f'公式解答PDFの問番号が重複: {path} 問{number}')
        answers[number] = 'アイウエ'.index(marker) + 1
    if set(answers) != set(range(1, expected_count + 1)):
        raise ValueError(f'公式解答PDFの問番号集合が不完全: {path} {sorted(answers)}')
    return answers


def compact(text: str) -> str:
    text = unicodedata.normalize('NFKC', text).replace('−', '-')
    return ''.join(c for c in text if not c.isspace() and not unicodedata.combining(c))


def assert_text_coverage(node, saved: str, context: str) -> None:
    if node is None:
        raise ValueError(f'取得元の本文要素がありません: {context}')
    target = compact(saved)
    for fragment in node.find_all(string=True):
        if isinstance(fragment, Comment) or fragment.parent.name in {'script', 'style', 'button'}:
            continue
        text = compact(str(fragment))
        if text and text not in target:
            raise ValueError(f'取得元テキストが欠落: {context}: {str(fragment)[:100]}')


def visible_source(node) -> str:
    """parserとは別のDOM変換で、本文・選択肢の順序と不可視文字の混入も照合する。"""
    soup = BeautifulSoup(str(node), 'html.parser')
    for hidden in soup.select('button, script, style'):
        hidden.decompose()
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    for listing in soup.select('ul, ol'):
        ordinal = int(listing.get('start', '1'))
        css_counter = 0
        for item in listing.find_all('li', recursive=False):
            classes = item.get('class', [])
            if any(re.fullmatch(r'li\d+', c) for c in classes):
                css_counter = 1 if 'li1' in classes else css_counter + 1
                item.insert(0, f'({css_counter}) ')
            elif listing.name == 'ol':
                ordinal = int(item.get('value', ordinal))
                style = listing.get('type', '1'); marker = str(ordinal)
                if style in {'a', 'A'}:
                    number = ordinal; marker = ''
                    while number > 0:
                        number, digit = divmod(number - 1, 26)
                        marker = chr(ord('a') + digit) + marker
                    if style == 'A': marker = marker.upper()
                elif style in {'i', 'I'}:
                    number = ordinal; marker = ''
                    for value, symbol in [(1000,'M'),(900,'CM'),(500,'D'),(400,'CD'),(100,'C'),(90,'XC'),(50,'L'),(40,'XL'),(10,'X'),(9,'IX'),(5,'V'),(4,'IV'),(1,'I')]:
                        while number >= value:
                            marker += symbol; number -= value
                    if style == 'i': marker = marker.lower()
                item.insert(0, marker + '. '); ordinal += 1
    for tag in reversed(soup.select('sup, sub, .ol, .dol, .frac, .root')):
        text = tag.get_text(); classes = tag.get('class', [])
        if tag.name in {'sup', 'sub'}:
            mapping = SUPERSCRIPT_MAP if tag.name == 'sup' else SUBSCRIPT_MAP
            text = ''.join(mapping[c] for c in text) if all(c in mapping for c in text) else ('^' if tag.name == 'sup' else '_') + f'({text})'
        elif 'frac' in classes:
            divider = next((c for c in tag.find_all(recursive=False) if c.name == 'span' and not c.has_attr('class')), None)
            if divider is None: raise ValueError('照合器で未対応の分数DOM')
            numerator = ''.join(BeautifulSoup(str(c), 'html.parser').get_text() for c in reversed(list(divider.previous_siblings))) + divider.get_text()
            denominator = ''.join(BeautifulSoup(str(c), 'html.parser').get_text() for c in divider.next_siblings)
            text = f'({numerator.strip()})/({denominator.strip()})'
        elif 'root' in classes:
            text = f'√({text.strip()})'
        else:
            text = ''.join(c + '\u0305' if not c.isspace() and not unicodedata.combining(c) else c for c in text)
        tag.replace_with(text)
    return compact(soup.get_text())


def question_content_hash(page: BeautifulSoup) -> str:
    text = ''.join(str(page.find(id=key)) for key in ['mondai', 'answerChar']) + str(page.select_one('ul.selectList'))
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def official_difference_receipt(output: Path, url: str, page: BeautifulSoup, source_answer: int,
                                official_answer: int, documents: list[dict]) -> dict:
    path = output / 'verification' / 'source_variants.json'
    proofs = json.loads(path.read_text()) if path.is_file() else {}
    proof = proofs.get(url)
    question_pdf = next((d for d in documents if d['label'] == '午前Ⅱ問題' and d['kind'] == 'official'), None)
    answer_pdf = next((d for d in documents if d['label'] == '午前Ⅱ解答' and d['kind'] == 'official'), None)
    if not proof or not question_pdf or not answer_pdf or any([
        proof.get('sourceContentSha256') != question_content_hash(page),
        proof.get('officialQuestionPdfSha256') != question_pdf['sha256'],
        proof.get('officialAnswerPdfSha256') != answer_pdf['sha256'],
        proof.get('sourceAnswer') != source_answer,
        proof.get('officialAnswer') != official_answer,
        proof.get('reviewMethod') != 'visual_comparison',
        proof.get('publicationStatus') != 'hold',
    ]):
        raise ValueError(f'取得元・IPA正答の差に現在内容の目視照合receiptがありません: {url}')
    return proof


def document_links(html: str, list_url: str) -> list[dict[str, str]]:
    documents = []
    seen = set()
    for link in BeautifulSoup(html, 'html.parser').find_all('a', href=True):
        url = urljoin(list_url, link['href'])
        if not urlparse(url).path.endswith('.pdf') or url in seen:
            continue
        # 成績分布は問題・正答・解説ではない。
        if 'bunpu' in url or link.get_text(strip=True) == '得点分布':
            continue
        host = urlparse(url).hostname
        if host not in {'www.ipa.go.jp', 'www.sc-siken.com'}:
            raise ValueError(f'未確認のPDF配布元: {url}')
        documents.append({'url': url, 'label': link.get_text(' ', strip=True),
                          'filename': urlparse(url).path.rsplit('/', 1)[-1],
                          'kind': 'official' if host == 'www.ipa.go.jp' else 'explanation'})
        seen.add(url)
    if not documents:
        raise ValueError(f'問題PDFへのリンクがありません: {list_url}')
    return documents


def afternoon_links(html: str, list_url: str) -> list[str]:
    return list(dict.fromkeys(
        urljoin(list_url, a['href'])
        for a in BeautifulSoup(html, 'html.parser').find_all('a', href=True)
        if re.search(r'(?:^|/)pm\d+(?:_\d+)?\.html$', a['href'])
    ))


def validate_image_file(path: Path) -> None:
    if path.suffix.lower() == '.svg':
        from xml.etree import ElementTree
        if not ElementTree.parse(path).getroot().tag.endswith('svg'):
            raise ValueError(f'SVGではない画像応答: {path}')
    else:
        with Image.open(path) as im:
            im.verify()


def download_afternoon_html(group: str, list_url: str, output: Path) -> dict:
    html = gzip.open(output / 'verification' / 'official' / group / 'index.html.gz', 'rt', encoding='utf-8').read()
    directory = output / 'afternoon_html' / group
    directory.mkdir(parents=True, exist_ok=True)
    session = create_http_session()
    documents = []
    for url in afternoon_links(html, list_url):
        page_html = fetch_html_text(session, url)
        page = BeautifulSoup(page_html, 'html.parser')
        if page.find('h2') is None or '午後' not in page.find('h2').get_text():
            raise ValueError(f'午後問題のHTMLを確認できません: {url}')
        name = urlparse(url).path.rsplit('/', 1)[-1]
        path = directory / (name + '.gz')
        with gzip.open(path, 'wt', encoding='utf-8') as stream:
            stream.write(page_html)
        urls = [u for u in extract_image_urls_from_element(page.select_one('.main'), url) if not is_placeholder_image_url(u)]
        filenames = download_and_save_images(session, urls, name.removesuffix('.html'), base_dir=str(directory / 'images'))
        if len(filenames) != len(urls):
            raise ValueError(f'午後HTMLの画像が欠落: {url}')
        documents.append({'url': url, 'path': str(path.relative_to(output)),
                          'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                          'images': [{'url': u, 'path': str((directory / 'images' / f).relative_to(output)),
                                      'sha256': hashlib.sha256((directory / 'images' / f).read_bytes()).hexdigest()}
                                     for u, f in zip(urls, filenames, strict=True)]})
    receipt = {'listGroupId': group, 'documents': documents}
    (directory / 'documents.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print(f'[HTML] {group}: {len(documents)} afternoon pages', flush=True)
    return receipt


def download_documents(group: str, list_url: str, output: Path) -> dict:
    session = create_http_session()
    html = fetch_html_text(session, list_url)
    evidence = output / 'verification' / 'official' / group
    evidence.mkdir(parents=True, exist_ok=True)
    with gzip.open(evidence / 'index.html.gz', 'wt', encoding='utf-8') as stream:
        stream.write(html)
    docs = document_links(html, list_url)
    for doc in docs:
        path = output / ('official_pdfs' if doc['kind'] == 'official' else 'afternoon_explanations') / group / doc['filename']
        for attempt in range(3):
            try:
                slow_down(0.5, 0.5)
                response = session.get(doc['url'], timeout=30)
                response.raise_for_status()
                data = response.content
                if not data.startswith(b'%PDF-'):
                    raise ValueError(f'PDFではない応答: {doc["url"]}')
                # 完全なPDFとして解析できることも確認する。
                import io
                pages = len(PdfReader(io.BytesIO(data)).pages)
                if pages < 1:
                    raise ValueError('PDFが0ページです')
                path.parent.mkdir(parents=True, exist_ok=True)
                temp = path.with_suffix('.pdf.tmp')
                temp.write_bytes(data)
                temp.replace(path)
                doc.update(path=str(path.relative_to(output)), sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), pages=pages)
                break
            except Exception:
                if attempt == 2:
                    raise
                slow_down(2, 1)
    receipt = {'listGroupId': group, 'sourceListUrl': list_url, 'documents': docs}
    (evidence / 'documents.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print(f'[PDF] {group}: {len(docs)} files', flush=True)
    return receipt


def verify_group(group: str, list_url: str, output: Path) -> dict:
    evidence = output / 'verification' / 'dojo' / group
    html = gzip.open(evidence / 'index.html.gz', 'rt', encoding='utf-8').read()
    soup = BeautifulSoup(html, 'html.parser')
    # parserのURL収集関数を使わず、sectionと問番号を取得元から独立に列挙する。
    expected = {}
    for link in soup.find_all('a', href=True):
        match = re.fullmatch(r'am([12])_([0-9]+)\.html', link['href'])
        if match:
            expected[urljoin(list_url, link['href'])] = (int(match[1]), int(match[2]))
    for section, count in [(1, 30), (2, 25)]:
        if {n for s, n in expected.values() if s == section} != set(range(1, count + 1)):
            raise ValueError(f'取得元の午前{section}問番号集合が不完全: {group}')
    if len(expected) != 55:
        raise ValueError(f'取得元問題URLが55件ではありません: {group}')
    records = []
    for path in sorted((output / 'questions_json' / group / '00_source').glob('question_*.json')):
        records.extend(json.loads(path.read_text())['question_bodies'])
    urls = [r['question_url'] for r in records]
    if len(urls) != len(set(urls)) or set(urls) != set(expected):
        raise ValueError(f'取得元と保存済みURL集合が不一致: {group}')
    manifest = json.loads((output / 'verification' / 'official' / group / 'documents.json').read_text())
    official = {}
    documents = manifest['documents']
    for doc in documents:
        path = output / doc['path']
        if hashlib.sha256(path.read_bytes()).hexdigest() != doc['sha256']:
            raise ValueError(f'保存PDFのhash不一致: {path}')
        for section, label, count in [(1, '午前Ⅰ解答', 30), (2, '午前Ⅱ解答', 25)]:
            if doc['label'] == label and doc['kind'] == 'official':
                official[section] = official_answers(path, count)
    if set(official) != {1, 2}:
        raise ValueError(f'公式午前解答PDFがそろっていません: {group}')
    afternoon = json.loads((output / 'afternoon_html' / group / 'documents.json').read_text())['documents']
    if {d['url'] for d in afternoon} != set(afternoon_links(html, list_url)):
        raise ValueError(f'午後HTML集合が不一致: {group}')
    afternoon_images = 0
    for doc in afternoon:
        for asset in [doc, *doc['images']]:
            if hashlib.sha256((output / asset['path']).read_bytes()).hexdigest() != asset['sha256']:
                raise ValueError(f'午後資料hash不一致: {asset["path"]}')
        for asset in doc['images']:
            validate_image_file(output / asset['path'])
        afternoon_images += len(doc['images'])
    images = 0
    ids = set()
    question_checks = []
    publication_holds = []
    for record in records:
        url = record['question_url']; section, number = expected[url]
        if record['source_question_id'] in ids:
            raise ValueError(f'source ID重複: {url}')
        ids.add(record['source_question_id'])
        page = BeautifulSoup(gzip.open(evidence / (urlparse(url).path.rsplit('/', 1)[-1] + '.gz'), 'rt', encoding='utf-8').read(), 'html.parser')
        heading = page.find('h2').get_text(' ', strip=True)
        roman = 'Ⅰ' if section == 1 else 'Ⅱ'
        if f'午前{roman}' not in heading or not re.search(rf'問\s*{number}(?:\D|$)', heading):
            raise ValueError(f'HTMLの試験区分・問番号がURLと不一致: {url}')
        if record['examLabel'] != heading.replace('  ', ' ').strip() or record['examYear'] != int(group[:4]) or record['list_group_id'] != group:
            raise ValueError(f'年度・見出し・group不一致: {url}')
        assert_text_coverage(page.find(id='mondai'), record['questionBodyText'], url + ' 本文')
        if visible_source(page.find(id='mondai')) != compact(record['questionBodyText']):
            raise ValueError(f'本文の順序・表記・不可視文字が取得元表示と不一致: {url}')
        first = page.find('button', class_='selectBtn')
        if first is None:
            raise ValueError(f'選択肢ボタンがありません: {url}')
        items = first.find_parent('ul').find_all('li')
        markers = [b.get_text(strip=True) for b in first.find_parent('ul').find_all('button', class_='selectBtn')]
        if markers != list('アイウエ') or len(record['choiceTextList']) != 4:
            raise ValueError(f'選択肢集合がアイウエの4件ではありません: {url}')
        if len(items) == 4:
            for marker, item, saved in zip(markers, items, record['choiceTextList'], strict=True):
                assert_text_coverage(item, saved, url + f' 選択肢{marker}')
                visible = visible_source(item)
                # 画像だけの選択肢では、本文文字列は選択肢記号、内容は画像参照に保存される。
                if not visible and item.find('img') is not None:
                    visible = marker
                if visible != compact(saved):
                    raise ValueError(f'選択肢の順序・表記が取得元表示と不一致: {url} {marker}')
        elif len(items) == 1:
            if record['choiceTextList'] != markers or not all(record['originalQuestionChoiceImageUrls']):
                raise ValueError(f'共有選択肢画像の抽出が不完全: {url}')
        else:
            raise ValueError(f'未対応の選択肢DOM: {url}')
        site_answer = page.find(id='answerChar')
        if site_answer is None or site_answer.get_text(strip=True) not in markers:
            raise ValueError(f'取得元の正答が不完全: {url}')
        answer = markers.index(site_answer.get_text(strip=True)) + 1
        if record['answer_result_inferred_correct_choice_numbers'] != [answer]:
            raise ValueError(f'取得元・保存正答が不一致: {url}')
        official_status = 'matched'
        if official[section][number] != answer:
            proof = official_difference_receipt(output, url, page, answer, official[section][number], documents)
            publication_holds.append({'url': url, 'reason': proof['reason'], 'sourceAnswer': answer,
                                      'officialAnswer': official[section][number]})
            official_status = 'verified_source_variant_publication_hold'
        explanation = '\n'.join(record.get('explanation_common_prefix', []) + record.get('explanation_common_summary', []) + [s for snippets in record.get('explanation_choice_snippets', []) for s in snippets])
        assert_text_coverage(page.find(id='kaisetsu'), explanation, url + ' 解説')
        image_groups = [record.get('questionImageStorageUrls', [])] + record['originalQuestionChoiceImageUrls']
        for references in image_groups:
            for reference in references:
                filename = unquote(urlparse(reference).path).rsplit('/', 1)[-1]
                path = output / 'question_images' / group / filename
                if not path.is_file() or path.stat().st_size == 0:
                    raise ValueError(f'参照画像が欠落: {path}')
                validate_image_file(path)
                images += 1
        # HTMLに含まれる問題・選択肢画像の件数と参照件数を照合する。
        body_images = [i for i in page.find(id='mondai').find_all('img') if i.get('src')]
        choice_images = [i for i in first.find_parent('ul').find_all('img') if i.get('src')]
        if len(body_images) != len(record.get('questionImageStorageUrls', [])):
            raise ValueError(f'問題画像件数が不一致: {url}')
        expected_choice_images = len(choice_images) * (4 if len(items) == 1 else 1)
        if expected_choice_images != sum(map(len, record['originalQuestionChoiceImageUrls'])):
            raise ValueError(f'選択肢画像件数が不一致: {url}')
        question_checks.append({'url': url, 'section': f'am{section}', 'number': number, 'sourceAnswer': answer,
                                'officialAnswer': official[section][number], 'officialComparison': official_status, 'status': 'passed'})
    return {'listGroupId': group, 'questionCount': len(records), 'imageReferenceCount': images, 'pdfCount': len(documents),
            'afternoonHtmlCount': len(afternoon), 'afternoonImageCount': afternoon_images,
            'publicationHolds': publication_holds, 'questions': question_checks}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download-pdfs', action='store_true', help='公開回をlive確認し、公式問題・解答と午後解説PDFを更新する')
    parser.add_argument('--download-afternoon-html', action='store_true', help='午後記述式HTMLと画像を、問題JSONとは別の資料として更新する')
    parser.add_argument('--list-group-id', action='append')
    parser.add_argument('--pdfs-only', action='store_true', help='資料取得だけを行い、source照合は後で行う')
    args = parser.parse_args()
    preset = load_scrape_preset('sc'); output = ROOT / 'output' / 'sc'
    groups = args.list_group_id or preset.list_group_ids
    for group in groups:
        preset.get_target(group)
    try:
        if args.download_pdfs:
            html = fetch_html_text(create_http_session(), INDEX_URL)
            live = discover_targets(html)
            configured = [{'source_list_group_id': t.source_list_group_id, 'output_list_group_id': t.output_list_group_id} for t in preset.scrape_targets]
            if live != configured:
                raise ValueError(f'公開回とpresetが不一致です。sc presetを取得元に基づいて更新してください: {live}')
            directory = output / 'verification'; directory.mkdir(parents=True, exist_ok=True)
            with gzip.open(directory / 'sckakomon.html.gz', 'wt', encoding='utf-8') as stream:
                stream.write(html)
            with ThreadPoolExecutor(max_workers=2) as executor:
                list(executor.map(lambda g: download_documents(g, build_list_first_page_url(preset, g), output), groups))
        if args.download_afternoon_html:
            with ThreadPoolExecutor(max_workers=2) as executor:
                list(executor.map(lambda g: download_afternoon_html(g, build_list_first_page_url(preset, g), output), groups))
        if args.pdfs_only:
            if not args.download_pdfs and not args.download_afternoon_html:
                raise ValueError('--pdfs-onlyには資料取得オプションが必要です')
            return 0
        results = [verify_group(g, build_list_first_page_url(preset, g), output) for g in groups]
        all_records = [r for g in groups for p in (output / 'questions_json' / g / '00_source').glob('*.json') for r in json.loads(p.read_text())['question_bodies']]
        for field in ['source_question_id', 'public_question_id', 'original_question_id']:
            ids = [r[field] for r in all_records]
            if len(ids) != len(set(ids)):
                raise ValueError(f'全groupでID重複: {field}')
        holds = [h for r in results for h in r['publicationHolds']]
        report = {'status': 'passed_with_publication_holds' if holds else 'passed', 'checkedAt': datetime.now(timezone.utc).isoformat(), 'groupCount': len(results), 'questionCount': sum(r['questionCount'] for r in results), 'imageReferenceCount': sum(r['imageReferenceCount'] for r in results), 'pdfCount': sum(r['pdfCount'] for r in results),
                  'afternoonHtmlCount': sum(r['afternoonHtmlCount'] for r in results), 'afternoonImageCount': sum(r['afternoonImageCount'] for r in results), 'groups': results}
        report['officialAnswerMatchedCount'] = report['questionCount'] - len(holds)
        report['publicationHolds'] = holds
        directory = output / 'reports'; directory.mkdir(parents=True, exist_ok=True)
        filename = 'acquisition_verification.json' if set(groups) == set(preset.list_group_ids) else 'acquisition_verification_' + '_'.join(groups) + '.json'
        (directory / filename).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({k: v for k, v in report.items() if k != 'groups'}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f'[FAILED] {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
