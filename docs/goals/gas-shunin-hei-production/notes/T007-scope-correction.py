"""2022消費問21の欠落条件だけを既存24三相確認へ渡す実行記録。共有システムは変更しない。"""
import datetime
import importlib.util
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / 'output/gas-shunin-hei/direct_publication/20261027'
spec = importlib.util.spec_from_file_location('hei_scope_existing_driver', ROOT / 'docs/goals/gas-shunin-hei-production/notes/T007-note-text.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
driver = module.driver
from tools.question_review_console.primary_law_evidence import PrimaryLawEvidenceResolver

KEY = '2022:shohi:問21'
WORK = RUN / 'scope_corrections/2022-shohi-21-r1'
PLAN = WORK / 'plan.json'


def prepare():
    if PLAN.exists():
        return
    packet = json.loads((RUN / 'records.json').read_text())[KEY]
    binding = packet['sourceIdentity']
    source = next(s for s in module.load_source_record_inventory(ROOT / 'output/gas-shunin-hei/questions_json/2022/00_source',
        qualification='gas-shunin-hei', list_group_id='2022') if s.record['source_question_id'] == KEY)
    current = module.QuestionInventory(ROOT).projected_input('gas-shunin-hei', '2022', binding['sourceRecordRef'])
    if current.errors:
        raise ValueError(current.errors)
    before = '液化石油ガスの消費量の総和が14kW（ガスオーブンを有するものにあっては21kW）以下のものであって、こんろバーナー1個あたりの液化石油ガスの消費量が5.8kW以下の液化石油ガス用こんろは、液石法※の特定液化石油ガス器具等に指定されていない。'
    after = '液化石油ガスの消費量の総和が14kW（ガスオーブンを有するものにあっては21kW）以下のものであって、こんろバーナー1個あたりの液化石油ガスの消費量が5.8kW以下の液化石油ガス用こんろ（液化石油ガスを充てんした容器が部品又は附属品として取り付けられる構造のものを除く。）は、液石法※の特定液化石油ガス器具等に指定されていない。'
    if current.record['choiceTextList'].count(before) != 1:
        raise ValueError('確認済みの完全命題と現在値が異なる')
    changes = {'choiceTextList': [after if t == before else t for t in current.record['choiceTextList']]}
    resolver = PrimaryLawEvidenceResolver(ROOT)
    supplements = []
    for date in ['2022-09-25', '2026-10-28']:
        law = resolver.law_file('343CO0000000014', date)
        body = ET.fromstring(law.xml_text).find('LawBody')
        nodes = [n for n in body.findall('AppdxTable') if n.findtext('AppdxTableTitle') in {'別表第一','別表第二'}]
        if len(nodes) != 2:
            raise ValueError('根拠の二つの別表が未取得')
        supplements.append({'asOf':date, 'sourceUrl':law.source_url, 'revisionId':law.revision_id,
            'lawXmlSha256':driver.hashlib.sha256(law.xml_text.encode()).hexdigest(),
            'appendices':[{'title':n.findtext('AppdxTableTitle'),'xml':ET.tostring(n,encoding='unicode'),'text':' '.join(''.join(n.itertext()).split())} for n in nodes]})
    supplement_path = WORK / 'official-law-supplement.json'
    driver.freeze(supplement_path, supplements)
    images = ['output/gas-shunin-hei/pdf_review/2022/mark_sheet_question/page-31.png', 'output/gas-shunin-hei/pdf_review/2022/answer/page-01.png']
    created = datetime.datetime.now(datetime.timezone.utc).isoformat()
    locator = str(supplement_path.relative_to(ROOT)) + ' / 2022-09-25・2026-10-28のe-Gov施行令別表1.2イ/ロ・別表2.1の実取得XML; 公式問題=' + packet['officialQuestionPdf']['path'] + ' SHA256=' + packet['officialQuestionPdf']['sha256'] + '; 公式正答=' + packet['officialAnswerPdf']['path'] + ' SHA256=' + packet['officialAnswerPdf']['sha256']
    evidence = {'sourceClass':'official','title':'2022丙種消費問21公式冊子・正答及び出題時/現行の液石法施行令一次根拠','locator':locator,'verifiedAt':created,'contentHash':driver.digest(supplement_path)}
    transcription = '【公式問題の表示全文】\n' + current.record['questionBodyText'] + '\n' + json.dumps(current.record['choiceTextList'],ensure_ascii=False) + '\n【公式法令の実取得本文】\n' + json.dumps(supplements,ensure_ascii=False) + '\n【編集補完候補。公式PDFの逐語転記ではない】\n' + after
    canonical = {'qualificationId':'gas-shunin-hei','listGroupId':'2022','originalQuestionId':binding['reviewQuestionId'],
        'officialEvidenceCandidates':[{**evidence,'localSourcePath':str(supplement_path.relative_to(ROOT)),
            'localRenderedPagePaths':images,'localRenderedPageHashes':{p:driver.digest(ROOT/p) for p in images},
            'verifiedTranscription':transcription,'candidateChanges':changes}],
        'report':'公式正答は3だが、第4肢が容器取付型を除外せず数値条件だけを述べ、完全命題では反例がある。主担当の法令監査と別内容監査が独立に同じ欠落条件を指摘した。原PDFそのものにもこの限定は印字されていない。00_sourceは保持し、施行令の明示条件を第4肢だけへ編集補完する候補である。',
        'displayFormatRules':['原PDFとの差をOCR誤りや転記欠落と偽らない。原問題にない適用除外を一次法令に基づいて補う編集修正として判断する。',
            '別表第1第2号ロは同号イの容器取付型を除く。別表第2第1号は容器取付型を特定器具とする。この二つを独立に確認し、条件補完が正確か判断する。',
            '第4肢の限定だけが変更候補であり、残る4選択肢・本文・数値・正答番号3を変更しない。成立しない補完ならholdとし、公式正答に合わせるためだけに承認しない。']}
    case = 'operator-law-scope-' + binding['reviewQuestionId']
    item = {'sourceQuestionId':KEY,'currentRecord':current.record,'sourceBinding':binding,
        'workItem':{'workId':'2022-shohi-q21-scope','qualificationId':'gas-shunin-hei','listGroupId':'2022',
            'questionId':binding['reviewQuestionId'],'originalQuestionId':binding['reviewQuestionId'],**binding,
            'caseIds':[case],'caseInputHashes':{case:driver.sha256_json(canonical)},'caseSnapshots':[{'id':case,'canonicalSnapshot':canonical}]},
        'evidence':[evidence],'imagePaths':images,'verifiedTranscription':transcription,'candidateChanges':changes,
        'sourcePath':str(source.path.relative_to(ROOT)),'sourceSha256':driver.digest(source.path),
        'questionPdfPath':packet['officialQuestionPdf']['path'],'questionPdfSha256':packet['officialQuestionPdf']['sha256']}
    driver.freeze(PLAN,{'schemaVersion':'gas-shunin-hei-official-correction-review-plan/v1','year':2022,'createdAt':created,
        'batchId':'zz-hei-direct-scope-2022-shohi-21-r1','category':'question_content','items':[item]})


if __name__ == '__main__':
    prepare()
    task = driver.Driver(PLAN, WORK/'reviews',root=ROOT)
    if sys.argv[1]=='save':
        print(task.save(allow_save=True))
    else:
        task.phase(sys.argv[1])
