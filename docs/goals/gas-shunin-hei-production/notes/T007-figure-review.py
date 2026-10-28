"""公式PDF由来の図を一問ずつ24層で独立確認する直接作業。"""
import datetime, importlib.util, json, shutil, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / 'output/gas-shunin-hei/direct_publication/20261027'
spec = importlib.util.spec_from_file_location('hei_note_driver', ROOT / 'docs/goals/gas-shunin-hei-production/notes/T007-note-text.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); driver = m.driver
driver.CATEGORY = 'image'
_base_schema = driver.build_output_schema
def image_output_schema(phase, rows, items, hashes):
    schema = _base_schema(phase, rows, items, hashes)
    field = 'proposedChanges' if phase.startswith('blind_') else 'changes'
    empty = driver.object_schema({})
    image_change = driver.object_schema({'questionImageStorageUrls': {'type':'array','items':{'type':'string'}}})
    for review in schema['properties']['results']['properties'].values():
        review['properties'][field] = {'anyOf':[empty, image_change]}
    return schema
driver.build_output_schema = image_output_schema
from scripts.common.image_storage_urls import build_public_storage_url

def prepare(key):
    figures = json.loads((RUN/'pdf_figure_candidates/manifest.json').read_text())
    fig = next(x for x in figures if x['sourceQuestionId'] == key)
    year, subject, num = key.split(':'); work=RUN/'figure_review'/(key.replace(':','-').replace('問','q')+'-image-r3'); plan=work/'plan.json'
    if plan.exists(): return plan
    packet=json.loads((RUN/'records.json').read_text())[key]; binding=packet['sourceIdentity']
    source=next(x for x in m.load_source_record_inventory(ROOT/f'output/gas-shunin-hei/questions_json/{year}/00_source',qualification='gas-shunin-hei',list_group_id=year) if x.record['source_question_id']==key)
    projected=m.QuestionInventory(ROOT).projected_input('gas-shunin-hei',year,binding['sourceRecordRef'])
    if projected.errors: raise ValueError(projected.errors)
    record=projected.record
    if record.get('questionImageStorageUrls'): raise ValueError('既存画像があるため個別確認が必要')
    candidate=ROOT/fig['candidatePath']
    if driver.digest(candidate)!=fig['sha256']: raise ValueError('候補図のhash不一致')
    filename='official-source-'+year+'-'+binding['reviewQuestionId']+'-'+fig['sha256'][:16]+'.png'
    target=ROOT/'output/gas-shunin-hei/question_images'/year/filename;target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists() and driver.digest(target)!=fig['sha256']:raise ValueError('既存画像と衝突')
    if not target.exists(): shutil.copyfile(candidate,target)
    changes={'questionImageStorageUrls':[build_public_storage_url('gas-shunin-hei',filename)]}
    pdf=Path(packet['officialQuestionPdf']['path']);pdf=pdf if not pdf.is_absolute() else pdf.relative_to(ROOT)
    page=f'output/gas-shunin-hei/pdf_review/{year}/mark_sheet_question/page-{fig["page"]:02}.png'; ans=f'output/gas-shunin-hei/pdf_review/{year}/answer/page-01.png'; images=[page,fig['candidatePath'],ans]
    stamp=datetime.datetime.now(datetime.timezone.utc).isoformat()
    evidence={'sourceClass':'official','title':f'{year}年度丙種の公式問題・正答と図','locator':f'{pdf} / PDF {fig["page"]}頁 / {key} / 当該図の切出し', 'verifiedAt':stamp,'contentHash':driver.digest(ROOT/pdf)}
    transcription='【現在の問題本文・全選択肢】\n'+record['questionBodyText']+'\n'+json.dumps(record['choiceTextList'],ensure_ascii=False)+'\n【図の候補】\n'+str(candidate.relative_to(ROOT))+'\n【画像URL候補】\n'+changes['questionImageStorageUrls'][0]+'\nURLは公開先予定であり、Storage未反映。'
    canonical={'qualificationId':'gas-shunin-hei','listGroupId':year,'originalQuestionId':binding['reviewQuestionId'],
       'officialEvidenceCandidates':[{**evidence,'localSourcePath':str(pdf),'localRenderedPagePaths':images,'localRenderedPageHashes':{p:driver.digest(ROOT/p) for p in images},'verifiedTranscription':transcription,'candidateChanges':changes,'transcriptionRole':'公式図の切出しを問題全体の図として追加。問題文・選択肢・答えの変更は行わない。'}],
       'officialImagePublicationCandidate':{'localImagePath':str(target.relative_to(ROOT)),'publicUrl':changes['questionImageStorageUrls'][0],'contentHash':fig['sha256'],'proposedChanges':changes},
       'displayFormatRules':['公式PDFの該当問題と図の全記号・接続関係・位置が切出しに残るか照合する。','追加先は問題全体の画像。選択肢や答えを変えない。','候補URLは未公開の予定値。配信成功を推定しない。']}
    case='operator-figure-'+binding['reviewQuestionId']; work_id=year+'-'+subject+'-'+num.replace('問','q')+'-figure'
    wi={'workId':work_id,'qualificationId':'gas-shunin-hei','listGroupId':year,'questionId':binding['reviewQuestionId'],'originalQuestionId':binding['reviewQuestionId'],**binding,'caseIds':[case],'caseInputHashes':{case:driver.sha256_json(canonical)},'caseSnapshots':[{'id':case,'canonicalSnapshot':canonical}]}
    item={'sourceQuestionId':key,'currentRecord':record,'sourceBinding':binding,'workItem':wi,'evidence':[evidence],'imagePaths':images,'verifiedTranscription':transcription,'candidateChanges':changes,'sourcePath':str(source.path.relative_to(ROOT)),'sourceSha256':driver.digest(source.path),'questionPdfPath':str(pdf),'questionPdfSha256':evidence['contentHash']}
    driver.freeze(plan,{'schemaVersion':'gas-shunin-hei-official-correction-review-plan/v1','year':int(year),'createdAt':stamp,'batchId':'zzzz-hei-direct-figure-'+year+'-'+subject+'-'+num.replace('問','q')+'-image-r3','category':'image','items':[item]})
    return plan
if __name__=='__main__':
    plan=prepare(sys.argv[1]); task=driver.Driver(plan,plan.parent/'reviews',root=ROOT)
    if sys.argv[2]=='save':print(task.save(allow_save=True))
    else:task.phase(sys.argv[2])
