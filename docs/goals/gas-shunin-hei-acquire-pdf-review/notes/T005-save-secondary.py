"""実native応答と明示訂正を保持して二次記録へ投影する単発実行記録。"""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from scripts.check.record_gas_shunin_pdf_review_receipts import project, store_new

ROLLOUT = Path(sys.argv[1])
DEST = ROOT / "output/gas-shunin-hei/pdf_review/secondary"
accepted, rejected, amendments = {}, [], []
for line in ROLLOUT.read_text().splitlines():
    item = json.loads(line).get("payload", {})
    if item.get("type") != "agent_message" or not item.get("author", "").startswith("/root/hei_secondary_"):
        continue
    content = "\n".join(x.get("text", "") for x in item.get("content", []))
    if not content.startswith("Message Type: FINAL_ANSWER") or "Payload:\n" not in content:
        continue
    try:
        envelope = json.loads(content.split("Payload:\n", 1)[1])
    except json.JSONDecodeError:
        continue
    receipt = envelope.get("goalbuddy_receipt_v1", {})
    if receipt.get("task_id") != "T005" or receipt.get("result") != "done":
        continue
    if "amendment" in receipt:
        amendments.append((item, envelope))
        continue
    if "rows" not in receipt:
        continue
    try:
        report = project(receipt, item["author"], item["id"], "secondary")
    except ValueError as error:
        rejected.append((item, envelope))
        store_new(DEST / "rejected" / f"{item['id']}.json", {
            "adoptionStatus": "rejected", "validationError": str(error),
            "nativeMessageId": item["id"], "author": item["author"], "receipt": envelope,
        })
        continue
    year = receipt["year"]
    store_new(DEST / "receipts" / f"{year}.json", envelope)
    store_new(DEST / f"{year}.json", report)
    accepted[year] = report

for item, envelope in amendments:
    amended = envelope["goalbuddy_receipt_v1"]
    change = amended["amendment"]
    candidates = [(old_item, old) for old_item, old in rejected
                  if old_item["author"] == item["author"]
                  and old["goalbuddy_receipt_v1"]["year"] == amended["year"]
                  and old["goalbuddy_receipt_v1"]["packetSha256"] == amended["packetSha256"]]
    if len(candidates) != 1:
        raise ValueError("訂正対象のnative応答が一意でない")
    old_item, old = candidates[0]
    derived = copy.deepcopy(old["goalbuddy_receipt_v1"])
    rows = [r for r in derived["rows"] if r[0] == change["sourceQuestionId"]]
    if len(rows) != 1 or not change["reason"] or change["viewedAnswerPages"] != [1]:
        raise ValueError("明示訂正の対象・理由・再読証跡が不足")
    row = rows[0]
    row[4] = change["answerStatus"]
    row[5] = change["officialCorrectChoiceNumber"]
    row[6] = change["note"]
    report = project(derived, item["author"], item["id"], "secondary")
    report["supersedesNativeMessageId"] = old_item["id"]
    report["nativeAmendment"] = change
    year = amended["year"]
    store_new(DEST / "amendments" / f"{year}.json", {
        "nativeMessageId": item["id"], "author": item["author"], "receipt": envelope,
    })
    store_new(DEST / "receipts" / f"{year}.json", {
        "schemaVersion": "explicit-native-review-amendment/v1",
        "baseNativeMessageId": old_item["id"], "amendmentNativeMessageId": item["id"],
        "sourceQuestionId": change["sourceQuestionId"],
        "note": "元の不整合応答と実画像再読後の同作者明示訂正を別保存し、自然keyで投影。sourceは変更しない。",
    })
    store_new(DEST / f"{year}.json", report)
    accepted[year] = report

if set(accepted) != set(range(2017, 2026)):
    raise ValueError(f"二次全年度未完了: {sorted(accepted)}")
print(json.dumps({"acceptedYears": sorted(accepted), "records": sum(len(x['records']) for x in accepted.values()),
                  "rejectedNativeReceipts": len(rejected), "explicitNativeAmendments": len(amendments)}))
