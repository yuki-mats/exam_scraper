#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, os, sys, time
from pathlib import Path
from typing import Any
from contract import (ARTIFACTS, AUDIT_INSTRUCTION, AUDIT_MODEL, EXPAND_IDS, GEN_INSTRUCTION,
                      ORACLE_WORDS, REPO, SCREEN_IDS, SCREEN_MODELS, canonical, digest, items, schema)

sys.path.insert(0, str(REPO))
import tools.question_review_console.codex_app_server as cas

def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp=path.with_suffix(path.suffix+".tmp"); tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n"); os.replace(tmp,path)

def extract(text: str) -> dict:
    decoder=json.JSONDecoder()
    for i,ch in enumerate(text):
        if ch in "[{":
            try: return decoder.raw_decode(text[i:])[0]
            except json.JSONDecodeError: pass
    raise RuntimeError("response JSON missing")

def usage(events: list[dict]) -> dict | None:
    found={}
    def walk(x):
        if isinstance(x,dict):
            for k,v in x.items():
                n=str(k).casefold()
                if isinstance(v,int) and not isinstance(v,bool):
                    if n in {"inputtokens","input_tokens","prompt_tokens"}: found["inputTokens"]=max(found.get("inputTokens",0),v)
                    if n in {"outputtokens","output_tokens","completion_tokens"}: found["outputTokens"]=max(found.get("outputTokens",0),v)
                walk(v)
        elif isinstance(x,list):
            for v in x: walk(v)
    walk(events)
    return found if {"inputTokens","outputTokens"} <= found.keys() else None

def validate(result: dict, expected: list[str]) -> None:
    rows=result.get("results")
    if not isinstance(rows,list) or [r.get("id") for r in rows] != expected: raise RuntimeError("schema/id/order mismatch")

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--phase",choices=["screen","expand"],required=True)
    p.add_argument("--models",nargs="*"); p.add_argument("--models-from-screen-pass",action="store_true")
    p.add_argument("--audit-model",required=True); p.add_argument("--reasoning",required=True); p.add_argument("--max-concurrency",type=int,required=True)
    a=p.parse_args(); assert a.audit_model==AUDIT_MODEL and a.reasoning=="high" and a.max_concurrency==1
    root=ARTIFACTS/a.phase
    if (root/"oracle-after-run.json").exists(): raise RuntimeError("oracle exists before phase run")
    if a.phase=="screen": models=a.models or [] ; fixed_ids=SCREEN_IDS
    else:
        screen=json.loads((ARTIFACTS/"screen"/"score.json").read_text())
        models=["gpt-5.6-luna"] + (["gpt-5.3-codex-spark"] if screen["models"]["gpt-5.3-codex-spark"]["screenPass"] else [])
        fixed_ids=EXPAND_IDS
    if a.phase=="screen" and models != SCREEN_MODELS: raise RuntimeError("screen model order changed")
    fixed=items(fixed_ids); input_bytes=canonical(fixed)
    gen_prompt=GEN_INSTRUCTION+"\n"+input_bytes.decode()
    if any(word in gen_prompt for word in ORACLE_WORDS): raise RuntimeError("oracle-like content in generation prompt")
    cas.QUESTION_MAINTENANCE_MODELS=frozenset(set(models)|{AUDIT_MODEL})
    client=cas.CodexAppServerClient(REPO,turn_timeout=3600)
    active=peak=calls=0; records={}; started=time.monotonic()
    def call(model: str, prompt: str, work: str):
        nonlocal active,peak,calls
        active+=1; peak=max(peak,active); calls+=1
        if active>1: raise RuntimeError("peak concurrency exceeded")
        events=[]; begin=time.monotonic()
        try:
            result=client.run_turn(prompt,work_type=work,sandbox="read-only",emit=lambda _:None,output_schema=schema(),
                model=model,reasoning_effort="high",on_model_turn_event=lambda e:events.append(dict(e)),turn_timeout=3600)
            return extract(result.final_message),usage(events),time.monotonic()-begin, {"threadId":result.thread_id,"turnId":result.turn_id}
        finally: active-=1
    try:
        for model in models:
            generated=[]; gen_usage=[]; gen_seconds=0.0; run_ids=[]
            for offset in range(0,len(fixed),5):
                batch=fixed[offset:offset+5]; prompt=GEN_INSTRUCTION+"\n"+canonical(batch).decode()
                value,u,seconds,ids=call(model,prompt,"maintenance_generation_candidate")
                validate(value,[x["id"] for x in batch]); generated.extend(value["results"]); gen_usage.append(u); gen_seconds+=seconds; run_ids.append(ids)
            audit_rows=[]; audit_usage=[]; audit_seconds=0.0; audit_common=[]
            by_id={x["id"]:x for x in generated}
            for offset in range(0,len(fixed),5):
                batch=fixed[offset:offset+5]; candidates=[by_id[x["id"]] for x in batch]
                common={"instruction":AUDIT_INSTRUCTION,"questions":batch}; audit_common.append(digest(common))
                prompt=AUDIT_INSTRUCTION+"\n"+canonical({"questions":batch,"candidates":candidates}).decode()
                value,u,seconds,ids=call(AUDIT_MODEL,prompt,"maintenance_audit_candidate")
                validate(value,[x["id"] for x in batch]); audit_rows.extend(value["results"]); audit_usage.append(u); audit_seconds+=seconds; run_ids.append(ids)
            records[model]={"model":model,"generation":generated,"audit":audit_rows,"generationUsage":gen_usage,
                "auditUsage":audit_usage,"generationSeconds":gen_seconds,"auditSeconds":audit_seconds,"runIds":run_ids,
                "candidatePromptSha256":hashlib.sha256(input_bytes).hexdigest(),"generationInstructionSha256":digest(GEN_INSTRUCTION),
                "auditCommonSha256":audit_common,"schemaFailures":0}
        prompt_hashes={v["candidatePromptSha256"] for v in records.values()}
        common_hashes={tuple(v["auditCommonSha256"]) for v in records.values()}
        if len(prompt_hashes)!=1 or len(common_hashes)!=1: raise RuntimeError("prompt parity failed")
        sealed={"phase":a.phase,"models":models,"fixedIds":fixed_ids,"records":records,"auditModel":AUDIT_MODEL,
            "reasoning":"high","peakConcurrency":peak,"modelCalls":calls,"elapsedSeconds":time.monotonic()-started,
            "imageQuestionsExcluded":2,"usageEstimated":False}
        write(root/"run.json",sealed)
        write(root/"seal.json",{"sealedAt":time.time(),"runSha256":hashlib.sha256((root/"run.json").read_bytes()).hexdigest()})
        print(json.dumps({"phase":a.phase,"models":models,"calls":calls,"peak":peak,"seconds":sealed["elapsedSeconds"]},ensure_ascii=False))
    finally: client.close()
    return 0
if __name__=="__main__": raise SystemExit(main())
