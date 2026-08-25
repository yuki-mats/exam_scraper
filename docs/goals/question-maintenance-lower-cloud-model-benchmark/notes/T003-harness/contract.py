from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
ARTIFACTS = HERE.parent / "T003-artifacts"
OLD_INPUT = REPO / "docs/goals/judoseifukushi-local-llm-benchmark/notes/T003-artifacts/T013/input-snapshot.json"
SCREEN_IDS = [
    "c5167b46942fb08e", "0eb595c2c11278f5", "130d5c77cc5c2b8b", "5c1ab42128e170ab",
    "b22f649e0b947399", "8300bc9178872872", "31fa2012e42b713a", "a28db6ba1c8e4c65",
    "01bd0255c9fc8371", "2380e0e939cce3b7",
]
EXPAND_IDS = [
    "c5167b46942fb08e", "0eb595c2c11278f5", "8987ec55216cbc63", "4ef67113801362d9",
    "ef0992b6887ec00b", "130d5c77cc5c2b8b", "5c1ab42128e170ab", "ee361042818c9b9f",
    "c582757f2a97a68a", "77eea1850fecb0ab", "b22f649e0b947399", "c1363e3f0487174c",
    "85cdb2c54567b04a", "8300bc9178872872", "31fa2012e42b713a", "23af5153f13d1a5c",
    "63ed9af2dc1cda2b", "715ae907c4b74436", "a28db6ba1c8e4c65", "01bd0255c9fc8371",
    "2380e0e939cce3b7", "1d2014527a7acabb", "da301fba93a4d48c", "ab3c9ed5d41aa3fd",
    "8168f912ed724458", "e1a6f5219f4e01d8", "a2f7e9ea703084cb", "5d85b2e35574d926",
    "dd07b4977677b7bb", "3239d392acd6236c", "64032ef7f4bac816", "3b24e06367db4222",
]
SCREEN_MODELS = ["gpt-5.6-luna", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex-spark"]
AUDIT_MODEL = "gpt-5.6-sol"
GEN_INSTRUCTION = (
    "あなたは資格試験問題の独立編集者です。与えられた問題だけを使い、各問の正しい選択肢番号"
    "（1始まり、複数可）、全選択肢の簡潔な根拠、回答体験に合うquestionTypeをJSONで返してください。"
    "正答資料はありません。推測を事実として断定しないでください。"
)
AUDIT_INSTRUCTION = (
    "あなたは盲検監査者です。問題と候補回答だけを知識に照らして独立に監査し、候補を信用せず、"
    "必要なら訂正した結果を同じJSON schemaで返してください。正答資料はありません。"
)
ORACLE_WORDS = ("correctChoice", "answerTable", "oracle", "正答資料:")


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def items(ids: list[str]) -> list[dict]:
    body = json.loads(OLD_INPUT.read_text())
    by_id = {row["id"]: row for row in body["items"]}
    if set(ids) - set(by_id):
        raise RuntimeError("fixed input ID missing")
    return [by_id[item_id] for item_id in ids]


def schema() -> dict:
    return {"type": "object", "additionalProperties": False, "required": ["results"], "properties": {
        "results": {"type": "array", "items": {"type": "object", "additionalProperties": False,
        "required": ["id", "selectedIndexes", "explanations", "questionType"], "properties": {
            "id": {"type": "string"}, "selectedIndexes": {"type": "array", "items": {"type": "integer"}},
            "explanations": {"type": "array", "items": {"type": "string"}}, "questionType": {"type": "string"}}}}}}
