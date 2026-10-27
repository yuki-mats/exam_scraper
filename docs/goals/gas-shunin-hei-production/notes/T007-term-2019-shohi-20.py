"""公式原問の曖昧な燃焼用語を、一次資料に基づく編集候補として24へ渡す。"""
import datetime
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / 'output/gas-shunin-hei/direct_publication/20261027'
spec = importlib.util.spec_from_file_location('hei_term_review', ROOT / 'docs/goals/gas-shunin-hei-production/notes/T007-note-text.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
driver = module.driver
KEY = '2019:shohi:問20'
WORK = RUN / 'scope_corrections/2019-shohi-20-term-r1'
PLAN = WORK / 'plan.json'


def prepare():
    if PLAN.exists():
        return
    packet = json.loads((RUN / 'records.json').read_text())[KEY]
    binding = packet['sourceIdentity']
    source = next(s for s in module.load_source_record_inventory(ROOT / 'output/gas-shunin-hei/questions_json/2019/00_source', qualification='gas-shunin-hei', list_group_id='2019') if s.record['source_question_id'] == KEY)
    current = module.QuestionInventory(ROOT).projected_input('gas-shunin-hei', '2019', binding['sourceRecordRef'])
    if current.errors:
        raise ValueError(current.errors)
    before = '空気過剰率は、過剰空気量を理論空気量で除して算出する。'
    after = '空気過剰率は、実際に供給した空気の総量を理論空気量で除して算出する。'
    if current.record['choiceTextList'].count(before) != 1:
        raise ValueError('確認済み命題と現在値が異なる')
    changes = {'choiceTextList': [after if t == before else t for t in current.record['choiceTextList']]}
    images = ['output/gas-shunin-hei/pdf_review/2019/mark_sheet_question/page-31.png', 'output/gas-shunin-hei/pdf_review/2019/answer/page-01.png']
    created = datetime.datetime.now(datetime.timezone.utc).isoformat()
    files = ['titech-air-excess-ratio.html', 'jemai-combustion-air-qa.html', 'ace-2019-cogeneration.pdf']
    supplement = {
        'directAuthorDecision': '過剰空気量は理論量を上回る供給総量を意味する用例と、総量と理論量の差分を意味する用例がある。公式冊子にも同じ語があるため転記ミスとはしない。空気過剰率の定義を総供給量と明示する編集候補である。',
        'verifiedPrimaryFindings': [
            {'source': files[0], 'url': 'https://www.titech.ac.jp/news/2020/046821', 'locator': '用語説明 用語1', 'finding': '研究者自身の研究発表本文は、空気過剰率を実際の空気質量と燃料の完全燃焼に必要な最小空気質量との比として定義している。主担当が実HTMLを読んだ。'},
            {'source': files[1], 'url': 'https://www.e-jemai.jp/qualification/2023/01/detail_6150.html', 'locator': 'Lesson3 気体燃料の燃焼計算', 'finding': '協会自身の通信教育回答は、所要空気量Aと理論空気量A0の差を余剰空気量とし、空気比mについてA=mA0としている。総量と差分を区別する必要がある。主担当が実本文を読んだ。'},
            {'source': files[2], 'url': 'https://www.ace.or.jp/web/info/pdf/seminar0022/semi0022-1.pdf', 'locator': 'PDF10頁 ガスエンジン燃焼方式比較表', 'finding': '財団自身の講演資料は希薄燃焼の空気量を過剰空気量、空気過剰率を2前後と表す。元の用語だけで公式問題を誤植と断定できない反証として保持する。主担当が実PDFの表を読んだ。'}],
        'primaryFiles': [{'path': str((RUN/'legal'/n).relative_to(ROOT)), 'sha256': driver.digest(RUN/'legal'/n)} for n in files],
        'candidateChanges': changes,
    }
    supplement_path = WORK / 'primary-technical-supplement.json'
    driver.freeze(supplement_path, supplement)
    pdf = ROOT / packet['officialQuestionPdf']['path']
    evidence = {'sourceClass': 'official', 'title': '2019年度丙種公式問題・正答及び一次技術資料に基づく用語明確化候補', 'locator': str(pdf.relative_to(ROOT))+' PDF31頁及び '+str(supplement_path.relative_to(ROOT)), 'verifiedAt': created, 'contentHash': driver.digest(pdf)}
    transcription = '【公式原文・変更前】\n'+current.record['questionBodyText']+'\n'+json.dumps(current.record['choiceTextList'],ensure_ascii=False)+'\n【主担当が一次本文で確認した用語の関係】\n'+json.dumps(supplement,ensure_ascii=False)+'\n【公式問題からの逐語転記ではない編集候補】\n'+after
    canonical = {'qualificationId':'gas-shunin-hei','listGroupId':'2019','originalQuestionId':binding['reviewQuestionId'],
        'officialEvidenceCandidates':[{**evidence,'localSourcePath':str(pdf.relative_to(ROOT)), 'localRenderedPagePaths':images,'localRenderedPageHashes':{p:driver.digest(ROOT/p) for p in images},'verifiedTranscription':transcription,'candidateChanges':changes}],
        'report':'原PDFの第1肢の過剰空気量は、供給総量と余剰分の二通りに読める。公式正答4を唯一の根拠に真偽を決めず、一次技術資料で確定した空気過剰率の定義を明確な文で表示する編集候補を直接作成した。',
        'displayFormatRules':['転記誤りとは主張しない。原PDFにも同じ曖昧な語が印字されている。', '変更候補は第1肢の用語だけ。公式正答4に合わせるための補正ではなく、空気過剰率の定義に合うかを独立に確認する。', '原問における語の用例が正しいと判断される場合も、総量と差分を混同しない教育的な明確化として必要性を判断する。不要ならno_change又はholdとし、採点や合意を捏造しない。']}
    case='operator-term-'+binding['reviewQuestionId']
    item={'sourceQuestionId':KEY,'currentRecord':current.record,'sourceBinding':binding,'workItem':{'workId':'2019-shohi-q20-term','qualificationId':'gas-shunin-hei','listGroupId':'2019','questionId':binding['reviewQuestionId'],'originalQuestionId':binding['reviewQuestionId'],**binding,'caseIds':[case],'caseInputHashes':{case:driver.sha256_json(canonical)},'caseSnapshots':[{'id':case,'canonicalSnapshot':canonical}]},'evidence':[evidence],'imagePaths':images,'verifiedTranscription':transcription,'candidateChanges':changes,'sourcePath':str(source.path.relative_to(ROOT)),'sourceSha256':driver.digest(source.path),'questionPdfPath':str(pdf.relative_to(ROOT)),'questionPdfSha256':driver.digest(pdf)}
    driver.freeze(PLAN,{'schemaVersion':'gas-shunin-hei-official-correction-review-plan/v1','year':2019,'createdAt':created,'batchId':'zz-hei-direct-term-2019-shohi-20-r1','category':'question_content','items':[item]})


if __name__ == '__main__':
    prepare()
    task=driver.Driver(PLAN,WORK/'reviews',root=ROOT)
    print(task.save(allow_save=True)) if sys.argv[1]=='save' else task.phase(sys.argv[1])
