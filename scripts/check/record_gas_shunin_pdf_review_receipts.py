"""このgoalの実agent応答をsource IDで検証し、照合記録として保存する。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REVIEW = ROOT / "output/gas-shunin-hei/pdf_review"
BOARD = "docs/goals/gas-shunin-hei-acquire-pdf-review/state.yaml"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def store_new(path: Path, value) -> None:
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"既存の照合記録と異なります: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def project(receipt: dict, author: str, message_id: str, stage: str) -> dict:
    year = receipt["year"]
    packet_path = REVIEW / str(year) / "packet.json"
    if receipt["packetSha256"] != sha(packet_path):
        raise ValueError("packet hashが不一致です")
    packet = json.loads(packet_path.read_text())
    cases = {q["sourceQuestionId"]: q for q in packet["questions"]}
    rows = receipt["rows"]
    if len(rows) != 58 or {r[0] for r in rows} != set(cases):
        raise ValueError("一問別receiptの完全ID集合が不一致です")
    records = []
    for row in rows:
        if len(row) != 8:
            raise ValueError("row schema不一致")
        sid, pages, body, choices, answer, official, note, issues = row
        q = cases[sid]
        if not pages or not set(pages) <= set(receipt["viewedQuestionPages"]):
            raise ValueError(f"問題頁の読取記録が不足: {sid}")
        if receipt["viewedAnswerPages"] != [1]:
            raise ValueError("正答画像の読取記録が不足")
        if not all(v in {"match", "difference", "hold"} for v in (body, choices, answer)):
            raise ValueError(f"不正な判定: {sid}")
        number_valid = type(official) is int and 1 <= official <= 5
        if not number_valid and not (official is None and answer == "hold"):
            raise ValueError(f"正答番号又は保留状態が不正: {sid}")
        if not note.strip():
            raise ValueError(f"正答番号又は問別noteが不足: {sid}")
        site = q["source"]["record"]["answer_result_inferred_correct_choice_numbers"]
        if answer == "match" and site != [official]:
            raise ValueError(f"一致判定と番号が矛盾: {sid}")
        source_path = ROOT / q["source"]["path"]
        if sha(source_path) != q["source"]["sha256"]:
            raise ValueError(f"source hash不一致: {sid}")
        records.append({
            "sourceQuestionId": sid, "sourceSha256": q["source"]["sha256"],
            "questionPdfSha256": q["officialQuestionPdf"]["sha256"],
            "answerPdfSha256": q["officialAnswerPdf"]["sha256"],
            "questionPdfPages": pages, "bodyStatus": body, "choicesStatus": choices,
            "answerStatus": answer, "siteCorrectChoiceNumber": site[0],
            "officialCorrectChoiceNumber": official, "issues": issues, "note": note,
        })
    return {"year": year, "stage": stage, "reviewerSession": author,
            "nativeMessageId": message_id, "packetSha256": receipt["packetSha256"],
            "viewedQuestionPages": receipt["viewedQuestionPages"],
            "viewedAnswerPages": receipt["viewedAnswerPages"], "records": records,
            "summary": receipt.get("summary"),
            "commonSourceNoteGaps": receipt.get("commonSourceNoteGaps", [])}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rollout", type=Path, required=True)
    parser.add_argument("--stage", choices=["primary", "secondary"], required=True)
    args = parser.parse_args()
    expected_task = {"primary": "T004", "secondary": "T005"}[args.stage]
    saved = []
    for line in args.rollout.read_text().splitlines():
        event = json.loads(line)
        item = event.get("payload", {})
        if event.get("type") != "response_item" or item.get("type") != "agent_message":
            continue
        author = item.get("author", "")
        if not author.startswith("/root/hei_") or item.get("recipient") != "/root":
            continue
        content = "\n".join(x.get("text", "") for x in item.get("content", []))
        if not content.startswith("Message Type: FINAL_ANSWER") or "Payload:\n" not in content:
            continue
        try:
            envelope = json.loads(content.split("Payload:\n", 1)[1])
        except json.JSONDecodeError:
            continue
        receipt = envelope.get("goalbuddy_receipt_v1", {})
        if receipt.get("task_id") != expected_task or receipt.get("board_path") != BOARD or "rows" not in receipt:
            continue
        if receipt.get("result") != "done":
            raise ValueError("未完了のreceiptは保存しません")
        report = project(receipt, author, item["id"], args.stage)
        year = receipt["year"]
        store_new(REVIEW / args.stage / "receipts" / f"{year}.json", envelope)
        store_new(REVIEW / args.stage / f"{year}.json", report)
        saved.append(year)
    print(json.dumps({"stage": args.stage, "years": saved, "recordCount": len(saved) * 58}))


if __name__ == "__main__":
    main()
