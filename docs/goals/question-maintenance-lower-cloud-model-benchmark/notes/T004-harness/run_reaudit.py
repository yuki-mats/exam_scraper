#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,os,sys,time
from pathlib import Path
from recontract import (ARTIFACTS,AUDIT_MODEL,EXPAND_MODELS,INSTRUCTION,REPO,SCREEN_MODELS,audit_schema,canonical,digest,file_hash,post_validate,verify_source)
sys.path.insert(0,str(REPO));import tools.question_review_console.codex_app_server as cas

def write(path,value):
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+".tmp");tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n");os.replace(tmp,path)
def extract(text):
 d=json.JSONDecoder()
 for i,c in enumerate(text):
  if c in "[{":
   try:return d.raw_decode(text[i:])[0]
   except json.JSONDecodeError:pass
 raise RuntimeError("audit JSON missing")
def usage(events):
 found={}
 def walk(x):
  if isinstance(x,dict):
   for k,v in x.items():
    n=str(k).casefold()
    if isinstance(v,int) and not isinstance(v,bool):
     if n in {"inputtokens","input_tokens","prompt_tokens"}:found["inputTokens"]=max(found.get("inputTokens",0),v)
     if n in {"outputtokens","output_tokens","completion_tokens"}:found["outputTokens"]=max(found.get("outputTokens",0),v)
    walk(v)
  elif isinstance(x,list):
   for v in x:walk(v)
 walk(events);return found if {"inputTokens","outputTokens"}<=found.keys() else None
def main():
 p=argparse.ArgumentParser();p.add_argument("--phase",choices=["screen","expand"],required=True);p.add_argument("--source",type=Path,required=True);p.add_argument("--models",nargs="+",required=True);p.add_argument("--scored-first",type=int);p.add_argument("--audit-model",required=True);p.add_argument("--reasoning",required=True);p.add_argument("--max-concurrency",type=int,required=True);p.add_argument("--reuse-sealed-generations",action="store_true");a=p.parse_args()
 assert a.reuse_sealed_generations and a.audit_model==AUDIT_MODEL and a.reasoning=="high" and a.max_concurrency==1
 expected=SCREEN_MODELS if a.phase=="screen" else EXPAND_MODELS
 if a.models!=expected:raise RuntimeError("model order changed")
 verify_source(a.source);source=json.loads(a.source.read_text());root=ARTIFACTS/a.phase
 if (root/"oracle-after-seal.json").exists():raise RuntimeError("oracle exists before reaudit")
 cas.QUESTION_MAINTENANCE_MODELS=frozenset({AUDIT_MODEL});client=cas.CodexAppServerClient(REPO,turn_timeout=3600)
 active=peak=calls=0;records={};started=time.monotonic()
 try:
  for model in a.models:
   generation=source["records"][model]["generation"]
   fixed=source["fixedIds"][:a.scored_first] if a.scored_first else source["fixedIds"]
   generation=[x for x in generation if x["id"] in set(fixed)]
   by_id={x["id"]:x for x in generation};questions=[]
   old_questions=[]
   # T003 run does not persist question bodies, but its prompt parity source is the sealed old input snapshot.
   input_body=json.loads((REPO/"docs/goals/judoseifukushi-local-llm-benchmark/notes/T003-artifacts/T013/input-snapshot.json").read_text())
   input_by_id={x["id"]:x for x in input_body["items"]};questions=[input_by_id[x] for x in fixed]
   audits=[];usages=[];seconds=0.0;common=[]
   for offset in range(0,len(questions),5):
    batch=questions[offset:offset+5];candidates=[by_id[x["id"]] for x in batch]
    common.append(digest({"instruction":INSTRUCTION,"questions":batch}))
    prompt=INSTRUCTION+"\n"+canonical({"questions":batch,"candidates":candidates}).decode()
    events=[];active+=1;peak=max(peak,active);calls+=1
    if active>1:raise RuntimeError("peak concurrency exceeded")
    begin=time.monotonic()
    try:
     result=client.run_turn(prompt,work_type="maintenance_reaudit_candidate",sandbox="read-only",emit=lambda _:None,output_schema=audit_schema(),model=AUDIT_MODEL,reasoning_effort="high",on_model_turn_event=lambda e:events.append(dict(e)),turn_timeout=3600)
     value=extract(result.final_message);post_validate(value,batch);audits.extend(value["results"]);usages.append(usage(events));seconds+=time.monotonic()-begin
    finally:active-=1
   records[model]={"model":model,"sealedGenerationSha256":digest(generation),"audit":audits,"auditUsage":usages,"auditSeconds":seconds,"auditCommonSha256":common,"schemaFailures":0}
  if len({tuple(v["auditCommonSha256"]) for v in records.values()})!=1:raise RuntimeError("audit common hash parity failed")
  output={"phase":a.phase,"models":a.models,"fixedIds":fixed,"records":records,"auditModel":AUDIT_MODEL,"reasoning":"high","modelCalls":calls,"generationCalls":0,"peakConcurrency":peak,"usageEstimated":False,"elapsedSeconds":time.monotonic()-started,"source":{"run":str(a.source),"runSha256":file_hash(a.source),"sealSha256":file_hash(a.source.parent/"seal.json")},"buildingUnscored":4 if a.phase=="expand" else 0}
  write(root/"run.json",output);write(root/"seal.json",{"sealedAt":time.time(),"runSha256":file_hash(root/"run.json")})
  print(json.dumps({"phase":a.phase,"calls":calls,"peak":peak,"seconds":output["elapsedSeconds"]},ensure_ascii=False))
 finally:client.close()
if __name__=="__main__":main()
