#!/usr/bin/env python3
"""Check unchanged raw answers and canonical-label read compatibility, no data writes."""
import hashlib,json,pathlib,subprocess,sys
from datetime import datetime,timezone
ROOT=pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT))
from scripts.common.question_identity import load_source_record_inventory
from scripts.merge.patch_views import normalize_correct_choice_label
from scripts.common.question_answer_contract import CORRECT_LABELS,INCORRECT_LABELS
from tools.question_bank.question_issue_reports import sha256_json
Q=ROOT/'output/gas-shunin-hei/questions_json';V=ROOT/'output/gas-shunin-hei/pdf_patch_review/verification'
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):
 p.parent.mkdir(parents=True,exist_ok=True);s=json.dumps(v,ensure_ascii=False,indent=2)+'\n'
 if p.exists():assert p.read_text()==s
 else:p.write_text(s)
def main():
 initial=V/'commands-raw-label-attempt1.json';assert initial.is_file()
 stages={'00_source','10_questionType_fixed','15_correctChoiceText_fixed','24_questionIssueCorrections','12_merged_questionType','20_merged_1','30_merged_2','23_correctChoiceText_fixed'}
 def inventory_hashes():return {str(p.relative_to(ROOT)):sha(p) for p in Q.glob('*/*/*.json') if p.parent.name in stages}
 before=inventory_hashes();assert not list(Q.glob('*/23_correctChoiceText_fixed/*.json'))
 implementation=ROOT/'scripts/merge/patch_views.py';normalizer_sha=sha(implementation)
 current={}
 for p in Q.glob('*/30_merged_2/*.json'):
  for q in read(p)['question_bodies']:
   k=q['source_question_id'];assert k not in current;current[k]=q
 assert len(current)==522
 commands=[];proof=[];changed_questions=changed_labels=0
 for year in range(2017,2026):
  entries=load_source_record_inventory(Q/str(year)/'00_source',qualification='gas-shunin-hei',list_group_id=str(year));files={}
  for e in entries:
   s=e.record;k=s['source_question_id'];m=current[k]
   protected={f for f in set(s)|set(m) if any(t in f.lower() for t in ('answer','correctchoice','judge','explanation'))}
   for f in protected:assert (f in s)==(f in m) and s.get(f)==m.get(f),(k,f)
   raw=s['correctChoiceText'];assert isinstance(raw,list) and all(isinstance(v,str) and v in CORRECT_LABELS|INCORRECT_LABELS for v in raw)
   assert len(raw)==len(s['choiceTextList'])==len(m['choiceTextList']) and s['choiceTextList']==m['choiceTextList']
   canonical=[normalize_correct_choice_label(v) for v in raw];actual=[normalize_correct_choice_label(v) for v in m['correctChoiceText']];assert canonical==actual and all(v in {'正しい','間違い'} for v in canonical)
   count=sum(a!=b for a,b in zip(raw,canonical,strict=True));changed_questions+=bool(count);changed_labels+=count
   proof.append({'sourceQuestionId':k,**e.identity.binding.as_mapping(),'rawAnswerHash':sha256_json(raw),'canonicalAnswerHash':sha256_json(canonical),'choiceCount':len(raw),'convertedLabelCount':count,'rawSourceAndMergedExactlyEqual':True})
   files.setdefault(e.path,[]).append({'original_question_id':e.identity.binding.review_question_id,**e.identity.binding.as_mapping(),'question_url':m['question_url'],'correctChoiceText':canonical})
  for source,rows in files.items():
   target=V/'canonical-answer-label-view'/str(year)/source.name;write(target,rows);log=V/f'canonical-coverage-{year}-{source.stem}.log';command=[sys.executable,'scripts/check/check_correct_choice_patch_coverage.py','--source',str(source),'--patch',str(target),'--require-full']
   with log.open('w') as f:r=subprocess.run(command,cwd=ROOT,stdout=f,stderr=subprocess.STDOUT)
   commands.append({'name':f'canonical-coverage-{year}-{source.stem}','command':command,'sourceSha256':sha(source),'projectionSha256':sha(target),'exitCode':r.returncode,'finishedAt':datetime.now(timezone.utc).isoformat(),'logPath':str(log.relative_to(ROOT)),'logSha256':sha(log)})
   print(commands[-1]['name'],'exit='+str(r.returncode),flush=True)
 assert len(proof)==len({r['sourceQuestionId'] for r in proof})==522
 assert len(commands)==27 and all(r['exitCode']==0 for r in commands)
 assert before==inventory_hashes() and normalizer_sha==sha(implementation)
 result={'schemaVersion':'hei-unchanged-answer-canonical-label-verification/v1','status':'passed','scope':'raw answer values unchanged; canonical synonym-label private projection coverage; no formal23 patches','rawQuestionsUnchanged':522,'convertedQuestionCount':changed_questions,'convertedLabelCount':changed_labels,'normalizer':{'path':str(implementation.relative_to(ROOT)),'sha256':normalizer_sha,'function':'normalize_correct_choice_label'},'allPassed':True,'pairs':27,'officialArtifactsUnchanged':before,'rawAttempt':{'path':str(initial.relative_to(ROOT)),'sha256':sha(initial)},'records':proof,'commands':commands}
 path=V/'canonical-answer-label-verification.json';write(path,result)
 summary=read(initial);summary['coverageCanonicalLabelView']={'path':str(path.relative_to(ROOT)),'sha256':sha(path),'allPassed':True,'pairs':27,'rawQuestionsUnchanged':522,'convertedQuestionCount':changed_questions,'convertedLabelCount':changed_labels,'formalCorrectChoicePatches':0};summary['commands']+=commands;summary['finalVerificationFinishedAt']=datetime.now(timezone.utc).isoformat();(V/'commands.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');print('raw preserved; private label projection passed',changed_questions,changed_labels)
if __name__=='__main__':main()
