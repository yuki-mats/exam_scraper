from __future__ import annotations
import hashlib, json
from pathlib import Path

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[4]
ARTIFACTS=HERE.parent/"T004-artifacts"
T003=HERE.parent/"T003-artifacts"
SCREEN_MODELS=["gpt-5.6-luna","gpt-5.4","gpt-5.4-mini","gpt-5.3-codex-spark"]
EXPAND_MODELS=["gpt-5.6-luna","gpt-5.3-codex-spark"]
AUDIT_MODEL="gpt-5.6-sol"
INSTRUCTION=("あなたは盲検監査者です。問題と候補回答だけを知識に照らして独立に監査し、候補を信用せず、必要なら訂正した結果をJSONで返してください。"
"selectedIndexesは必ず1始まりの整数とし、1以上かつ各問の選択肢数以下にしてください。0始まりは禁止です。"
"explanationsは選択肢順に全選択肢分を返し、selectedIndexesで示す判断と矛盾させないでください。正答資料はありません。")

def canonical(x): return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()
def digest(x): return hashlib.sha256(canonical(x)).hexdigest()
def file_hash(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def verify_source(path: Path):
    seal=path.parent/"seal.json"; expected=json.loads(seal.read_text())["runSha256"]
    if file_hash(path)!=expected: raise RuntimeError("T003 seal mismatch")

def audit_schema(choice_count=4):
    return {"type":"object","additionalProperties":False,"required":["results"],"properties":{"results":{"type":"array","items":{
      "type":"object","additionalProperties":False,"required":["id","selectedIndexes","explanations","questionType"],"properties":{
      "id":{"type":"string"},"selectedIndexes":{"type":"array","uniqueItems":True,"items":{"type":"integer","minimum":1,"maximum":choice_count}},
      "explanations":{"type":"array","minItems":choice_count,"maxItems":choice_count,"items":{"type":"string"}},"questionType":{"type":"string"}}}}}}

def post_validate(result, batch):
    rows=result.get("results")
    if not isinstance(rows,list) or [x.get("id") for x in rows] != [x["id"] for x in batch]: raise RuntimeError("audit schema/id/order mismatch")
    for row,item in zip(rows,batch):
        count=len(item["choices"]); indexes=row["selectedIndexes"]
        if any(type(v) is not int or v<1 or v>count for v in indexes): raise RuntimeError("audit index out of range")
        if len(set(indexes))!=len(indexes) or len(row["explanations"])!=count: raise RuntimeError("audit index/explanation contract failed")
