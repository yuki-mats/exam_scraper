"""賃貸管理の番号式過去問を取得HTMLから独立に照合する。sourceは変更しない。"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup
from PIL import Image

ROOT = Path(__file__).resolve().parents[2] / 'output/chintaikanrishi'


def compact(text: str) -> str:
    return re.sub(r'\s+', '', text)


def visible(node) -> str:
    dom = BeautifulSoup(str(node), 'html.parser')
    for hidden in dom.select('script, style, h3'):
        hidden.decompose()
    for ordered in dom.select('ol.kanaList'):
        for index, item in enumerate(ordered.find_all('li', recursive=False)):
            item.insert(0, 'アイウエオカキクケコ'[index])
    for sup in dom.select('sup'):
        value = sup.get_text().strip()
        sup.replace_with(value.translate(str.maketrans('0123456789+-', '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻')) if all(c in '0123456789+-' for c in value) else f'^({value})')
    for sub in dom.select('sub'):
        value = sub.get_text().strip()
        sub.replace_with(value.translate(str.maketrans('0123456789+-', '₀₁₂₃₄₅₆₇₈₉₊₋')) if all(c in '0123456789+-' for c in value) else f'_({value})')
    return compact(dom.get_text())  # BeautifulSoupのget_textはCommentを除外する。


def own_li(node):
    item = BeautifulSoup(str(node), 'html.parser').find('li')
    for nested in item.find_all('li', recursive=False):
        nested.decompose()
    return item


def main() -> int:
    rows, failures, ids, image_proofs = [], [], set(), {}
    for year in range(2015, 2026):
        group = str(year)
        count = 40 if year < 2020 else 50
        cache = ROOT / 'verification/dojo' / group
        index = BeautifulSoup(gzip.open(cache / 'index.html.gz', 'rt', encoding='utf-8').read(), 'html.parser')
        numbers = {int(a['href'][:2]) for a in index.find_all('a', href=re.compile(r'^\d{2}\.html$'))}
        assert numbers == set(range(1, count + 1)), (year, 'index_inventory', numbers)
        records = [r for p in sorted((ROOT / 'questions_json' / group / '00_source').glob('*.json'))
                   for r in json.loads(p.read_text())['question_bodies']]
        seen_numbers = set()
        for record in records:
            url = record['question_url']; num = int(urlparse(url).path.rsplit('/', 1)[-1][:2])
            assert num not in seen_numbers and record['source_question_id'] not in ids
            ids.add(record['source_question_id']); seen_numbers.add(num)
            page = BeautifulSoup(gzip.open(cache / f'{num:02}.html.gz', 'rt', encoding='utf-8').read(), 'html.parser')
            body = page.select_one('.mondai'); choices = [own_li(li) for li in page.select('ol.selectList li')]
            explanation = page.select_one('section.kaisetsu')
            errors = []
            if visible(body) != compact(record['questionBodyText']): errors.append('body')
            if [visible(li) for li in choices] != [compact(t) for t in record['choiceTextList']]: errors.append('choices')
            if visible(explanation) != compact(''.join(record['explanation_common_prefix'])): errors.append('explanation')
            answers = [int(n) for n in re.findall(r'\d+', page.select_one('.answerBox .answerChar').get_text())]
            if answers != record['answer_result_inferred_correct_choice_numbers']: errors.append('answer')
            if answers != [i for i, li in enumerate(choices, 1) if li.get('data-answer') == 't']: errors.append('answer_marks')
            selected = [record['choiceTextList'][i - 1] for i in answers]
            if record['correctChoiceText'] != (selected[0] if len(selected) == 1 else selected): errors.append('correct_choice_text')
            if record['examYear'] != year or record['list_group_id'] != group or not re.search(rf'問\s*{num}(?:\D|$)', page.find('h2').get_text()): errors.append('identity')
            roles = [(body, record['questionImageStorageUrls']), (explanation, record['explanationImageStorageUrls'])]
            roles += list(zip(choices, record['originalQuestionChoiceImageUrls'], strict=True))
            for node, refs in roles:
                if len(node.select('img')) != len(refs): errors.append('image_count')
                for ref in refs:
                    filename = unquote(urlparse(ref).path).rsplit('/', 1)[-1]
                    path = ROOT / 'question_images' / group / filename
                    if not path.is_file(): errors.append('image_missing'); continue
                    with Image.open(path) as img: img.verify()
                    image_proofs[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
            row = {'year': year, 'number': num, 'sourceQuestionId': record['source_question_id'], 'failures': errors}
            rows.append(row)
            if errors: failures.append(row)
        assert seen_numbers == set(range(1, count + 1)), (year, 'source_inventory')
    report = {'status': 'failed' if failures else 'passed', 'questionCount': len(rows),
              'imageHashes': image_proofs, 'failureCount': len(failures), 'questions': rows}
    path = ROOT / 'verification/dojo_acquisition_check.json'; path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k not in {'questions', 'imageHashes'}}, ensure_ascii=False))
    for row in failures[:12]: print(row)
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
