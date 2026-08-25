#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os
from pathlib import Path
from contract import ARTIFACTS, EXPAND_IDS, REPO, SCREEN_IDS

LAW=set(EXPAND_IDS[:5]); NUMERIC=set(EXPAND_IDS[5:10]); LONG=set(EXPAND_IDS[10:13]); NEG=set(EXPAND_IDS[13:18]); MED=set(EXPAND_IDS[18:28]); BUILD=set(EXPAND_IDS[28:])
def stratum(x):
    return "law" if x in LAW else "numeric" if x in NUMERIC else "long" if x in LONG else "negative" if x in NEG else "current-medical" if x in MED else "building"
def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True); temp=path.with_suffix(path.suffix+".tmp"); temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n"); os.replace(temp,path)
def oracle(ids):
    wanted=set(ids); found={}
    for path in REPO.glob("output/**/00_source/*.json"):
        try: body=json.loads(path.read_text())
        except Exception: continue
        for q in body.get("question_bodies",[]):
            qid=q.get("public_question_id") or q.get("original_question_id")
            if qid in wanted:
                if qid in found: raise RuntimeError(f"duplicate oracle ID {qid}")
                truth=q.get("answerTableCorrectChoiceNumbers") or q.get("choiceClassCorrectChoiceNumbers") or []
                found[qid]=sorted(int(v) for v in truth)
    if set(found)!=wanted: raise RuntimeError("oracle source missing")
    return found
def selected(rows): return {x["id"]:sorted(int(v) for v in x["selectedIndexes"]) for x in rows}
def main():
    p=argparse.ArgumentParser(); p.add_argument("--phase",choices=["screen","expand"],required=True); p.add_argument("--seal-before-oracle",action="store_true"); a=p.parse_args()
    assert a.seal_before_oracle; root=ARTIFACTS/a.phase
    if not (root/"seal.json").exists(): raise RuntimeError("run not sealed")
    run=json.loads((root/"run.json").read_text()); ids=SCREEN_IDS if a.phase=="screen" else EXPAND_IDS
    truth=oracle(ids); write(root/"oracle-after-run.json",{"createdAfterSeal":True,"items":[{"id":x,"correctIndexes":truth[x],"stratum":stratum(x)} for x in ids]})
    output={"phase":a.phase,"models":{}}
    for model,record in run["records"].items():
        raw=selected(record["generation"]); audited=selected(record["audit"])
        raw_ok={x:raw[x]==truth[x] for x in ids}; audit_ok={x:audited[x]==truth[x] for x in ids}
        schema=record["schemaFailures"]; critical=[]
        result={"rawCorrect":sum(raw_ok.values()),"auditedCorrect":sum(audit_ok.values()),"schemaFailures":schema,
            "criticalErrors":critical,"rawByStratum":{s:sum(raw_ok[x] for x in ids if stratum(x)==s) for s in {stratum(x) for x in ids}},
            "items":[{"id":x,"rawCorrect":raw_ok[x],"auditedCorrect":audit_ok[x],"raw":raw[x],"audited":audited[x],"oracle":truth[x]} for x in ids],
            "usage":None if any(v is None for v in record["generationUsage"]) else record["generationUsage"],"usageEstimated":False,
            "elapsedSeconds":record["generationSeconds"]+record["auditSeconds"]}
        if a.phase=="screen": result["screenPass"]=result["rawCorrect"]>=9 and result["auditedCorrect"]==10 and schema==0 and not critical and run["peakConcurrency"]==1
        else:
            r=result["rawByStratum"]
            result["expandPass"]=(result["rawCorrect"]>=30 and sum(raw_ok[x] for x in ids if x not in BUILD)>=27 and sum(raw_ok[x] for x in ids if x in BUILD)>=3
                and r.get("law",0)>=5 and r.get("numeric",0)>=5 and r.get("long",0)>=3 and r.get("negative",0)>=5
                and r.get("current-medical",0)>=9 and r.get("building",0)>=3 and result["auditedCorrect"]==32 and schema==0 and not critical and run["peakConcurrency"]==1)
        output["models"][model]=result
    write(root/"score.json",output); print(json.dumps({m:{k:v[k] for k in v if k in {"rawCorrect","auditedCorrect","screenPass","expandPass"}} for m,v in output["models"].items()},ensure_ascii=False))
if __name__=="__main__": main()
