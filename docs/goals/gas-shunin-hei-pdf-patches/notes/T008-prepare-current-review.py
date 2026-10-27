#!/usr/bin/env python3
import hashlib,json,pathlib,subprocess,sys
ROOT=pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT))
from tools.question_bank.question_issue_reports import sha256_json
B=ROOT/'output/gas-shunin-hei/pdf_patch_review';N=pathlib.Path(__file__).parent

def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,x):
 p.parent.mkdir(parents=True,exist_ok=True);s=json.dumps(x,ensure_ascii=False,indent=2)+'\n'
 if p.exists():assert p.read_text()==s,str(p)
 else:p.write_text(s)
def main():
 formal={str(p.relative_to(ROOT)):sha(p) for p in (ROOT/'output/gas-shunin-hei/questions_json').glob('*/24_questionIssueCorrections/*.json')};assert len(formal)==289
 expected={i['sourceQuestionId'] for p in (B/'plans-v2').glob('*.json') for i in read(p)['items']};records={}
 for version in ['runs-v2','runs-v3','runs-v4','runs-v5','runs-v6','runs-v7','runs-v8','runs-v9']:
  for p in sorted((B/version).glob('*/save-result.json')):
   for r in read(p)['results']:
    if r['decision'] not in ['fix','no_change']:continue
    k=r['sourceQuestionId'];assert k not in records,k
    row={'sourceQuestionId':k,'decision':r['decision'],'nativeWorkDirectory':str(p.parent.relative_to(ROOT))}
    if r['decision']=='fix':
     assert formal[r['patchPath']]==r['patchSha256'];row['patch']={'path':r['patchPath'],'sha256':r['patchSha256']}
    records[k]=row
 assert set(records)==expected and len(records)==306
 assert sum(x['decision']=='fix' for x in records.values())==289
 completion={'schemaVersion':'hei-body-review-completion/v1','records':list(records.values())}
 write(B/'body-review-completion.json',completion)
 for x in read(N/'source-baseline.json'):assert sha(ROOT/x['path'])==x['sha256']
 for y in range(2017,2026):
  log=B/f'before-metadata-merge-{y}.log'
  with log.open('w') as f:r=subprocess.run([sys.executable,'scripts/merge/00_merge_all.py',str(y),'--base-dir','output/gas-shunin-hei/questions_json'],cwd=ROOT,stdout=f,stderr=subprocess.STDOUT)
  assert r.returncode==0,log
 current={}
 for p in (ROOT/'output/gas-shunin-hei/questions_json').glob('*/30_merged_2/*.json'):
  for q in read(p)['question_bodies']:
   k=q['source_question_id'];assert k not in current;current[k]=q
 assert len(current)==522
 candidates={x['sourceQuestionId']:x for x in read(N/'T006-type-candidate-inventory.json')['candidates']};assert set(candidates)==set(current)
 old={x['sourceQuestionId']:x for x in read(ROOT/'document/temporary/20261027_gas_shunin_hei_pdf_review.json')['records']}
 def packet_row(k,kind):
  q=current[k];r={'sourceQuestionId':k,'original_question_id':q.get('original_question_id') or q['public_question_id'],'currentRecordHash':sha256_json(q),'questionBodyText':q['questionBodyText'],'choiceTextList':q['choiceTextList'],'storedExplanation':{'present':'explanationText' in q,'value':q.get('explanationText')},'questionImages':[f"output/gas-shunin-hei/pdf_review/{old[k]['year']}/mark_sheet_question/page-{p:02d}.png" for p in old[k]['primary']['questionPdfPages']]}
  if kind=='type':r['candidate']=candidates[k]
  return r
 for letter,years in [('a',{2017,2018,2019}),('b',{2020,2021,2022}),('c',{2023,2024,2025})]:
  rows=[packet_row(k,'type') for k in sorted(current) if int(k.split(':')[0]) in years];assert len(rows)==174
  write(B/'metadata-review-packets'/f'type-{letter}.json',{'role':'candidate-assisted current-content revalidation; answers/counts/old format/intent masked','records':rows})
 intents=read(N/'T006-intent-candidate-native.json')['payload']['goalbuddy_receipt_v1']['evidence'][0]['intentCandidates']
 rows=[]
 for c in intents:
  row=packet_row(c['sourceQuestionId'],'intent');row['candidate']=c;rows.append(row)
 assert len(rows)==27
 write(B/'metadata-review-packets'/'intent.json',{'role':'complete-proposition intent revalidation; answers/counts/types/old intent masked','records':rows})
 write(B/'metadata-current-records.json',current)
 print('Body complete289/17; current522; masked review packets ready')
if __name__=='__main__':main()
