"""2021法令問16の対象条件を明示する編集補足を、既存24審査へ渡す。"""
import datetime
import importlib.util
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / 'output/gas-shunin-hei/direct_publication/20261027'
spec = importlib.util.spec_from_file_location('hei_closed_scope_review', ROOT / 'docs/goals/gas-shunin-hei-production/notes/T007-note-text.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
driver = module.driver
from tools.question_review_console.primary_law_evidence import PrimaryLawEvidenceResolver


def prepare():
    year, key = '2021', '2021:law:問16'
    work = RUN / 'scope_corrections/2021-law-16-closed-scope-r2'
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
    if '【適用範囲の補足】' in body:
        raise ValueError('既存補足を重複させない')
    note = ('【適用範囲の補足】\n第3肢のガス衣類乾燥機は、密閉燃焼式を除くものとする。'
        'これは出題時及び現行のガス事業法施行規則第202条第1号の適用範囲を明示する編集補足である。')
    changes = {'questionBodyText': body + '\n\n' + note}
    resolver = PrimaryLawEvidenceResolver(ROOT)
    supplements = []
    for date in ['2021-09-26', '2026-10-28']:
        law = resolver.law_file('345M50000400097', date)
        article = next(a for a in ET.fromstring(law.xml_text).find('LawBody/MainProvision').findall('.//Article') if a.get('Num') == '202')
        item = article.find("Paragraph/Item[@Num='1']")
        supplements.append({'asOf': date, 'sourceUrl': law.source_url, 'revisionId': law.revision_id,
            'lawXmlSha256': driver.hashlib.sha256(law.xml_text.encode()).hexdigest(),
            'locator': '第202条第1号', 'xml': ET.tostring(item, encoding='unicode'),
            'text': ' '.join(''.join(item.itertext()).split())})
    supplement = work / 'official-law-supplement.json'
    driver.freeze(supplement, supplements)
    created = datetime.datetime.now(datetime.timezone.utc).isoformat()
    images = [f'output/gas-shunin-hei/pdf_review/{year}/mark_sheet_question/page-14.png',
        f'output/gas-shunin-hei/pdf_review/{year}/answer/page-01.png']
    evidence = {'sourceClass': 'official', 'title': '2021丙種法令問16公式冊子・正答及び両時点の第202条第1号',
        'locator': str(supplement.relative_to(ROOT)) + ' / 公式画像14頁 / 第3肢と第202条第1号柱書・ト',
        'verifiedAt': created, 'contentHash': driver.digest(supplement)}
    content_result = RUN / 'content_audits/2021_law_問16_r2/result.json'
    transcription = ('【公式原文と全5肢・保持対象】\n' + body + '\n' + json.dumps(current.record['choiceTextList'], ensure_ascii=False)
        + '\n【両時点の実取得公式条文】\n' + json.dumps(supplements, ensure_ascii=False)
        + '\n【主担当の編集補足候補。公式PDFには印字されていない】\n' + note
        + '\n【先行内容監査の指摘。判断の代用ではなく検討経緯】\n' + json.dumps(json.loads(content_result.read_text())['review'], ensure_ascii=False))
    canonical = {'qualificationId': 'gas-shunin-hei', 'listGroupId': year, 'originalQuestionId': binding['reviewQuestionId'],
        'officialEvidenceCandidates': [{**evidence, 'localSourcePath': str(supplement.relative_to(ROOT)),
            'localRenderedPagePaths': images, 'localRenderedPageHashes': {p: driver.digest(ROOT / p) for p in images},
            'verifiedTranscription': transcription, 'candidateChanges': changes,
            'transcriptionRole': '原PDFと異なる意味に読み替えるOCR修正ではなく、法令の対象範囲を明示する編集注の候補。'}],
        'report': ('第3肢は公式原文にも密閉燃焼式除外がない。一方、試験時と現行の第202条第1号は密閉燃焼式を除いている。'
            '先行の原文照合のみのr1ではno_changeとなったが、新たなr2全肢内容監査は原文のまま第3肢を正しいとする結論を完全命題の対象過大として否認した。'
            '法令監査と内容監査にも判定差があるため、その差を隠さず一次条文と原文を再提示する。'
            '原文・全肢・正答表を保持したまま、第3肢を密閉式以外に限定する編集注の必要性と妥当性を独立に審査する。'),
        'displayFormatRules': ['公式原文の逐語不一致だけで誤り又は修正不要と決めず、添付した両時点の一次条文の対象範囲から編集補足の要否を判断する。',
            '候補は公式PDFに印字されていないと明示する。00_source、全5肢、既存ID、原文を保持し、本文末の編集注だけを変更候補とする。',
            '先行監査の採点・判定は変更しない。公式正答番号を維持するためだけに承認せず、補足不要又は条件不適切ならholdとする。']}
    case = 'operator-closed-scope-' + binding['reviewQuestionId']
    item = {'sourceQuestionId': key, 'currentRecord': current.record, 'sourceBinding': binding,
        'workItem': {'workId': '2021-law-q16-closed-scope', 'qualificationId': 'gas-shunin-hei', 'listGroupId': year,
            'questionId': binding['reviewQuestionId'], 'originalQuestionId': binding['reviewQuestionId'], **binding,
            'caseIds': [case], 'caseInputHashes': {case: driver.sha256_json(canonical)},
            'caseSnapshots': [{'id': case, 'canonicalSnapshot': canonical}]},
        'evidence': [evidence], 'imagePaths': images, 'verifiedTranscription': transcription, 'candidateChanges': changes,
        'sourcePath': str(source.path.relative_to(ROOT)), 'sourceSha256': driver.digest(source.path),
        'questionPdfPath': packet['officialQuestionPdf']['path'], 'questionPdfSha256': packet['officialQuestionPdf']['sha256']}
    driver.freeze(plan, {'schemaVersion': 'gas-shunin-hei-official-correction-review-plan/v1', 'year': 2021,
        'createdAt': created, 'batchId': 'zz-hei-direct-scope-2021-law16-closed-scope-r2', 'category': 'question_content', 'items': [item]})
    return plan


if __name__ == '__main__':
    plan = prepare()
    task = driver.Driver(plan, plan.parent / 'reviews', root=ROOT)
    if sys.argv[1] == 'save':
        print(task.save(allow_save=True))
    else:
        task.phase(sys.argv[1])
