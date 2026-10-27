"""周知頻度の原問へ現行法の対象条件を補う直接編集候補。共有実装は変更しない。"""
import datetime
import importlib.util
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / 'output/gas-shunin-hei/direct_publication/20261027'
spec = importlib.util.spec_from_file_location('hei_shuchi_existing_review', ROOT / 'docs/goals/gas-shunin-hei-production/notes/T007-note-text.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
driver = module.driver
from tools.question_review_console.primary_law_evidence import PrimaryLawEvidenceResolver

SCOPE_NOTE = ('【現行法で解くための条件補足】\n'
    '周知の頻度は、経済産業大臣等の承認による特例と、安全設備の設置による頻度緩和のいずれも適用されない場合について答える。'
    '後者は、自動ガス遮断装置又はガス漏れ警報器が設置され、かつ、技術基準に適合するガス栓が設置されている建物を対象とする。')


def prepare(year):
    if year not in {'2018', '2019'}:
        raise ValueError('直接照合した2問だけを扱う')
    key = year + ':law:問14'
    work = RUN / ('scope_corrections/' + year + '-law-14-current-shuchi-r1')
    plan = work / 'plan.json'
    if plan.exists():
        return plan
    packet = json.loads((RUN / 'records.json').read_text())[key]
    binding = packet['sourceIdentity']
    source = next(s for s in module.load_source_record_inventory(ROOT / f'output/gas-shunin-hei/questions_json/{year}/00_source',
        qualification='gas-shunin-hei', list_group_id=year) if s.record['source_question_id'] == key)
    current = module.QuestionInventory(ROOT).projected_input('gas-shunin-hei', year, binding['sourceRecordRef'])
    if current.errors:
        raise ValueError(current.errors)
    body = current.record['questionBodyText']
    if '周知' not in body or '【現行法で解くための条件補足】' in body:
        raise ValueError('直接確認した現行本文と異なる')
    changes = {'questionBodyText': body + '\n\n' + SCOPE_NOTE}
    resolver = PrimaryLawEvidenceResolver(ROOT)
    supplements = []
    for date in [year + ('-09-30' if year == '2018' else '-09-29'), '2026-10-28']:
        law = resolver.law_file('345M50000400097', date)
        main = ET.fromstring(law.xml_text).find('LawBody/MainProvision')
        article = next(a for a in main.findall('.//Article') if a.get('Num') == '197')
        paragraph = article.find("Paragraph[@Num='1']")
        item = paragraph.find("Item[@Num='2']")
        supplements.append({'asOf': date, 'sourceUrl': law.source_url, 'revisionId': law.revision_id,
            'lawXmlSha256': driver.hashlib.sha256(law.xml_text.encode()).hexdigest(),
            'locator': '第197条第1項第2号', 'xml': ET.tostring(item, encoding='unicode'),
            'text': ' '.join(''.join(item.itertext()).split())})
    supplement = work / 'official-law-supplement.json'
    driver.freeze(supplement, supplements)
    page = 13 if year == '2018' else 15
    images = [f'output/gas-shunin-hei/pdf_review/{year}/mark_sheet_question/page-{page:02}.png',
        f'output/gas-shunin-hei/pdf_review/{year}/answer/page-01.png']
    created = datetime.datetime.now(datetime.timezone.utc).isoformat()
    evidence = {'sourceClass': 'official', 'title': year + '丙種法令問14公式冊子・正答と出題時及び現行の周知頻度の一次法令',
        'locator': str(supplement.relative_to(ROOT)) + ' / e-Gov本則197条1項2号の両時点実取得本文; 公式問題=' + packet['officialQuestionPdf']['path'],
        'verifiedAt': created, 'contentHash': driver.digest(supplement)}
    transcription = ('【公式問題原文の現在表示・全肢】\n' + body + '\n' + json.dumps(current.record['choiceTextList'], ensure_ascii=False)
        + '\n【両時点の実取得一次法令本文】\n' + json.dumps(supplements, ensure_ascii=False)
        + '\n【主担当の現行法向け条件補足案。原PDFには印字されていない】\n' + SCOPE_NOTE)
    canonical = {'qualificationId': 'gas-shunin-hei', 'listGroupId': year, 'originalQuestionId': binding['reviewQuestionId'],
        'officialEvidenceCandidates': [{**evidence, 'localSourcePath': str(supplement.relative_to(ROOT)),
            'localRenderedPagePaths': images, 'localRenderedPageHashes': {p: driver.digest(ROOT / p) for p in images},
            'verifiedTranscription': transcription, 'candidateChanges': changes,
            'transcriptionRole': '原文の転記訂正ではなく、現行法で解く際の不足条件を明示する編集補足。'}],
        'report': ('出題時には特定建物の一般周知は1年に1回であったが、現行197条1項2号イは所定の安全設備を備える建物を除く。'
            '原問の設問だけでは現行の設備条件が未指定である。現行法向けの問題として解くため、特例・頻度緩和に該当しない条件を本文末の明示的な編集注で補う。'
            '公式原文のOCR欠落と偽らず、原文と全5組合せは保持する。承認された場合でも正答・解説・現行法監査はこの工程後に別途実施する。'),
        'displayFormatRules': ['現行法向けの条件補足が一次条文の適用範囲と一致するかを審査する。公式原文との逐語一致だけで正誤を確定しない。',
            '候補は原PDFに印字されていないと明示する。00_source、原文、全5組合せ、数値、既存IDを保持し、本文末の編集注だけを変更候補とする。',
            '新しい根拠で現行法の不足条件を補う必要がない、又は候補の適用条件が誤っていればholdとし、公式正答を維持するためだけに承認しない。']}
    case = 'operator-current-shuchi-' + binding['reviewQuestionId']
    item = {'sourceQuestionId': key, 'currentRecord': current.record, 'sourceBinding': binding,
        'workItem': {'workId': year + '-law-q14-current-shuchi', 'qualificationId': 'gas-shunin-hei', 'listGroupId': year,
            'questionId': binding['reviewQuestionId'], 'originalQuestionId': binding['reviewQuestionId'], **binding,
            'caseIds': [case], 'caseInputHashes': {case: driver.sha256_json(canonical)},
            'caseSnapshots': [{'id': case, 'canonicalSnapshot': canonical}]},
        'evidence': [evidence], 'imagePaths': images, 'verifiedTranscription': transcription, 'candidateChanges': changes,
        'sourcePath': str(source.path.relative_to(ROOT)), 'sourceSha256': driver.digest(source.path),
        'questionPdfPath': packet['officialQuestionPdf']['path'], 'questionPdfSha256': packet['officialQuestionPdf']['sha256']}
    driver.freeze(plan, {'schemaVersion': 'gas-shunin-hei-official-correction-review-plan/v1', 'year': int(year),
        'createdAt': created, 'batchId': 'zz-hei-direct-scope-' + year + '-law14-current-shuchi-r1', 'category': 'question_content', 'items': [item]})
    return plan


if __name__ == '__main__':
    plan = prepare(sys.argv[1])
    task = driver.Driver(plan, plan.parent / 'reviews', root=ROOT)
    if sys.argv[2] == 'save':
        print(task.save(allow_save=True))
    else:
        task.phase(sys.argv[2])
