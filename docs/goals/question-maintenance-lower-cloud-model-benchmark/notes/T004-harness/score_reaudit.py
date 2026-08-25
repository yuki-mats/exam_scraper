#!/usr/bin/env python3
import argparse,json,os
from pathlib import Path
from recontract import ARTIFACTS,T003
def write(path,value):
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+".tmp");tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n");os.replace(tmp,path)
def selected(rows):return {x["id"]:sorted(x["selectedIndexes"]) for x in rows}
def main():
 p=argparse.ArgumentParser();p.add_argument("--phase",required=True);p.add_argument("--seal-before-oracle",action="store_true");p.add_argument("--oracle-source",type=Path,required=True);p.add_argument("--exclude-empty-oracle",action="store_true");a=p.parse_args();assert a.seal_before_oracle
 root=ARTIFACTS/a.phase
 if not (root/"seal.json").exists():raise RuntimeError("reaudit not sealed")
 run=json.loads((root/"run.json").read_text());oracle_body=json.loads(a.oracle_source.read_text());truth={x["id"]:x["correctIndexes"] for x in oracle_body["items"]}
 ids=run["fixedIds"]
 if a.exclude_empty_oracle:ids=[x for x in ids if truth[x]]
 write(root/"oracle-after-seal.json",{"createdAfterSeal":True,"source":str(a.oracle_source),"scoredIds":ids,"excludedEmptyOracle":len(run["fixedIds"])-len(ids)})
 t003=json.loads((T003/a.phase/"run.json").read_text());out={"phase":a.phase,"scoredCount":len(ids),"buildingUnscored":4 if a.phase=="expand" else 0,"models":{}}
 for model,record in run["records"].items():
  raw=selected(t003["records"][model]["generation"]);audit=selected(record["audit"])
  raw_correct=sum(raw[x]==truth[x] for x in ids);audit_correct=sum(audit[x]==truth[x] for x in ids)
  value={"rawCorrect":raw_correct,"auditedCorrect":audit_correct,"schemaFailures":record["schemaFailures"],"usage":None if any(x is None for x in record["auditUsage"]) else record["auditUsage"],"usageEstimated":False,"auditSeconds":record["auditSeconds"],"items":[{"id":x,"raw":raw[x],"audit":audit[x],"oracle":truth[x],"rawCorrect":raw[x]==truth[x],"auditedCorrect":audit[x]==truth[x]} for x in ids]}
  if a.phase=="screen":value["screenPass"]=raw_correct>=9 and audit_correct==10 and record["schemaFailures"]==0
  else:value["expandPass"]=raw_correct>=27 and audit_correct==28 and record["schemaFailures"]==0
  out["models"][model]=value
 write(root/"score.json",out);print(json.dumps({m:{"raw":v["rawCorrect"],"audit":v["auditedCorrect"],"pass":v.get("screenPass",v.get("expandPass"))} for m,v in out["models"].items()},ensure_ascii=False))
if __name__=="__main__":main()
