#!/usr/bin/env python3
"""Score T015 only after all three routes are terminal and capture-sealed."""
from __future__ import annotations
import argparse, json, os
from pathlib import Path
from typing import Any
from benchmark_contract import BUILDING, JUDO, source_index, stratum_for

ROUTES = ("codex_only", "qwen3:14b", "qwen3.5:27b")
HOLD_IDS = set(JUDO["source-answer-missing"])
THRESHOLDS = {"judoseifukushi": 26, "overall": 31, "law": 5, "numeric": 4, "long": 2,
              "negative": 4, "current-medical": 9, "building": 5}

def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp=path.with_suffix(path.suffix+".tmp");temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n");os.replace(temp,path)

def rows(root: Path, route: str) -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted((root/"routes"/route.replace(":","-")/"results").glob("*.json"))]

def selected(row: dict[str, Any], route: str, *, audited: bool=False) -> list[int] | None:
    if row["status"] != "completed": return None
    if route == "codex_only": value=row.get("result",{})
    elif audited: value=row.get("auditResult",{}).get("correctedResult",{})
    else: value=row.get("localResult",{})
    return sorted(int(x) for x in value.get("selectedIndexes",[]))

def codex_requests(path: Path) -> list[dict[str, Any]]:
    if not path.exists(): return []
    return [json.loads(x) for x in path.read_text().splitlines() if json.loads(x).get("kind")=="request" and json.loads(x).get("provider")=="codex_app_server"]

def main() -> int:
    p=argparse.ArgumentParser();p.add_argument("--repo-root",type=Path,required=True);p.add_argument("--manifest",type=Path,required=True)
    p.add_argument("--results-root",type=Path,required=True);p.add_argument("--token-policy",required=True,
        choices=["inconclusive-provider-usage-unavailable"]);p.add_argument("--baseline-codex-calls",type=int,required=True);args=p.parse_args()
    root=args.results_root.resolve(); manifest=json.loads(args.manifest.read_text()); found=source_index(args.repo_root.resolve())
    for route in ROUTES:
        d=root/"routes"/route.replace(":","-")
        if len(rows(root,route))!=36 or not (d/"run-complete.json").exists() or not (d/"prompt-capture-seal.json").exists():
            raise RuntimeError(f"route not closed: {route}")
    oracle={"createdAfterAllRoutes":True,"items":[]}
    for item in manifest["items"]:
        question=found[item["id"]][2]
        truth=question.get("answerTableCorrectChoiceNumbers") or question.get("choiceClassCorrectChoiceNumbers") or []
        oracle["items"].append({"id":item["id"],"correctIndexes":sorted(truth),"stratum":stratum_for(item["id"]),"hold":item["id"] in HOLD_IDS})
    write(root/"oracle-after-run.json",oracle);truth={x["id"]:x for x in oracle["items"]}
    summaries={}
    for route in ROUTES:
        route_rows=rows(root,route); scored=[]; critical=[]
        for row in route_rows:
            expected=truth[row["id"]]
            oracle_match=(row["status"]=="hold") if expected["hold"] else selected(row,route)==expected["correctIndexes"]
            audited_match=True if route=="codex_only" else selected(row,route,audited=True)==expected["correctIndexes"]
            flags=[] if route=="codex_only" else list(row.get("auditResult",{}).get("criticalFlags",[]))
            if route!="codex_only" and row.get("auditAccepted") is True and not oracle_match: flags.append("audit_accepted_wrong_answer")
            if flags: critical.append({"id":row["id"],"flags":flags})
            local_pass=(route!="codex_only" and row.get("localAttemptMode")=="local_primary" and row.get("auditAccepted") is True
                        and oracle_match and not flags)
            scored.append({"id":row["id"],"stratum":expected["stratum"],"status":row["status"],"oracleMatch":oracle_match,
                           "auditedOracleMatch":audited_match,"localOnlyPass":local_pass,"criticalFlags":flags})
        nonhold=[x for x in scored if x["id"] not in HOLD_IDS]
        if route=="codex_only": calls=args.baseline_codex_calls
        else:
            continuation = "T015" if route == "qwen3:14b" else "T016"
            calls=len(codex_requests(root/continuation/(route.replace(":","-")+"-transport.jsonl")))
        stratum_passes={name:sum(x["localOnlyPass"] for x in nonhold if x["stratum"]==name) for name in JUDO if name!="source-answer-missing"}
        judo_pass=sum(x["localOnlyPass"] for x in nonhold if x["id"] not in BUILDING)
        summary={"route":route,"terminalRows":len(route_rows),"codexCalls":calls,"localCalls":sum(int(x.get("localCalls",0)) for x in route_rows),
                 "oracleMatches":sum(x["oracleMatch"] for x in nonhold),"auditedOracleMatches":sum(x["auditedOracleMatch"] for x in nonhold),
                 "localOnlyPasses":sum(x["localOnlyPass"] for x in nonhold),"judoseifukushiPasses":judo_pass,
                 "stratumPasses":stratum_passes,"buildingPasses":sum(x["localOnlyPass"] for x in nonhold if x["stratum"]=="building"),
                 "holdCorrect":sum(x["status"]=="hold" for x in scored if x["id"] in HOLD_IDS),"critical":critical,"items":scored}
        summaries[route]=summary;write(root/"routes"/route.replace(":","-")/"telemetry.json",summary)
    baseline=summaries["codex_only"]; comparisons={}
    for route in ROUTES[1:]:
        s=summaries[route]
        gates={"overall":s["localOnlyPasses"]>=THRESHOLDS["overall"],"judoseifukushi":s["judoseifukushiPasses"]>=THRESHOLDS["judoseifukushi"],
               "law":s["stratumPasses"].get("law",0)>=5,"numeric":s["stratumPasses"].get("numeric",0)>=4,
               "long":s["stratumPasses"].get("long",0)>=2,"negative":s["stratumPasses"].get("negative",0)>=4,
               "current-medical":s["stratumPasses"].get("current-medical",0)>=9,"building":s["buildingPasses"]>=5,
               "holds":s["holdCorrect"]==2,"finalAuditedOracle":s["auditedOracleMatches"]==34,"critical":len(s["critical"])==0}
        quality=all(gates.values())
        quality_complete = s["auditedOracleMatches"] == 34
        reduction=(1-s["codexCalls"]/args.baseline_codex_calls) if quality_complete else None
        call_pass=reduction is not None and reduction>=.30
        verdict="rejected_critical" if s["critical"] else ("rejected_quality" if not quality else ("quality_capable_but_not_cloud_call_reducing" if not call_pass else "conditional_candidate_token_unverified"))
        comparisons[route]={"gates":gates,"qualityPass":quality,"qualityComplete":quality_complete,
                            "codexCallReduction":reduction,"callPass":call_pass,"verdict":verdict}
    result={"thresholds":THRESHOLDS,"baseline":{**baseline,"codexCalls":args.baseline_codex_calls},"tokenMetric":{
        "status":"inconclusive_provider_usage_unavailable","reduction":None,"estimated":False},"comparisons":comparisons,
        "adoption":{route:False for route in ROUTES[1:]},"operationalPromotionAllowed":False}
    write(root/"comparison.json",comparisons);write(root/"result.json",result)
    write(root/"T016-receipt.json",{"taskId":"T016","status":"completed","tokenMetric":result["tokenMetric"],"verdicts":{k:v["verdict"] for k,v in comparisons.items()}})
    return 0
if __name__=="__main__":raise SystemExit(main())
