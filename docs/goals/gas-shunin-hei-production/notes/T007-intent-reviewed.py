"""主担当が一問ずつ確定した設問意図の限定確認・15層保存。共有実装は変更しない。"""
from concurrent.futures import ThreadPoolExecutor
import dataclasses
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / "output/gas-shunin-hei/direct_publication/20261027"
REVIEW_TAG = sys.argv[3] if len(sys.argv) > 3 else ""
if REVIEW_TAG and (not REVIEW_TAG.replace("-", "").isalnum()):
    raise ValueError("review tagは英数字とハイフンに限る")


def review_directory(year):
    return RUN / "intent_reviews" / (year + ("-" + REVIEW_TAG if REVIEW_TAG else ""))


sys.path.insert(0, str(RUN / "tool_snapshot"))
from scripts.common.question_identity import load_source_record_inventory
from scripts.fix.materialize_minimal_patch import SourcePatchInput, materialize_entries
from tools.question_review_console.codex_app_server import CodexAppServerClient, QUESTION_MAINTENANCE_AUDIT_MODEL
from tools.question_review_console.inventory import QuestionInventory


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def freeze(path, value):
    if path.exists() and json.loads(path.read_text()) != value:
        raise ValueError(f"固定入力が変更された: {path}")
    dump(path, value)


def review(year, proposals):
    packets = json.loads((RUN / "records.json").read_text())
    inventory = QuestionInventory(ROOT)
    rows, pages = {}, set()
    for key, proposal in proposals.items():
        packet = packets[key]
        current = inventory.projected_input("gas-shunin-hei", year, packet["sourceIdentity"]["sourceRecordRef"])
        if current.errors or current.record.get("questionIntent"):
            raise ValueError(f"未設定の対象ではない: {key}")
        rows[key] = {"currentRecord": current.record, "recordHash": sha(current.record),
                     "sourceIdentity": packet["sourceIdentity"], "sourceSha256": packet["sourceSha256"],
                     "proposal": proposal}
        pages.update(packet["questionPages"])
        pages.update(proposal.get("officialPagesVisuallyReviewed", []))
    images = [ROOT / f"output/gas-shunin-hei/pdf_review/{year}/mark_sheet_question/page-{p:02}.png" for p in sorted(pages)]
    work = review_directory(year)
    bundle = {"rows": rows, "images": [{"path": str(p.relative_to(ROOT)), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in images]}
    prompt = ("設問意図だけを独立確認する。ファイル・外部状態を変更せずsubagentを使用しない。"
              "主担当が一問ずつ作成したproposalを、当該問の問題文・全選択肢・回答操作と添付公式画像から確認する。"
              "select_correctは正しい記述・適切な値・成立する組合せを選ぶ向き、select_incorrectは誤りを選ぶ向き、"
              "judge_each_statementは記述それぞれの真偽を答える操作である。"
              "既存正答の正しい/間違いの数から推定せず、問の要求で判断する。"
              "対象をsourceQuestionIdで特定し、隣接問題や配列順に結び付けない。"
              "今回は正答・解説の変更や全工程評価は行わない。各問の承認可否と独立した理由を返す。\n"
              + json.dumps(bundle, ensure_ascii=False))
    judgment = {"type": "object", "properties": {"approved": {"type": "boolean"},
        "intent": {"type": "string", "enum": ["select_correct", "select_incorrect", "judge_each_statement"]},
        "rationale": {"type": "string"}}, "required": ["approved", "intent", "rationale"], "additionalProperties": False}
    schema = {"type": "object", "properties": {key: judgment for key in rows}, "required": list(rows), "additionalProperties": False}
    fixed = {"bundle": bundle, "prompt": prompt, "schema": schema}
    freeze(work / "input.json", fixed)
    native_path = work / "native.json"
    if native_path.exists():
        native = json.loads(native_path.read_text())
    else:
        client = CodexAppServerClient(ROOT)
        try:
            native = dataclasses.asdict(client.run_turn(prompt, model=QUESTION_MAINTENANCE_AUDIT_MODEL,
                sandbox="read-only", work_type="official_source_review", image_paths=images, output_schema=schema,
                cwd=ROOT, emit=lambda _: None, turn_group="gas-shunin-hei-direct-intent"))
        finally:
            client.close()
        dump(native_path, native)
    if native.get("changed_files") or native.get("subagent_thread_ids") or not all(native.get(k) for k in ("session_id", "thread_id", "turn_id")):
        raise ValueError("実read-only監査を確認できない")
    actual_images = {x["sourcePath"]: x["sha256"] for x in native["image_inputs"]}
    if actual_images != {x["path"]: x["sha256"] for x in bundle["images"]}:
        raise ValueError("実画像の入力が不一致")
    actual = json.loads(native["final_message"])
    if set(actual) != set(rows):
        raise ValueError("対象IDが一致しない")
    accepted = all(actual[k]["approved"] and actual[k]["intent"] == v["proposal"]["questionIntent"] and actual[k]["rationale"].strip() for k, v in rows.items())
    result = {"accepted": accepted, "inputHash": sha(fixed), "nativeSha256": hashlib.sha256(native_path.read_bytes()).hexdigest(), "reviews": actual}
    dump(work / "result.json", result)
    print(year, "intent reviewed", len(rows), "accepted", accepted, flush=True)


def save(year):
    work = review_directory(year)
    fixed = json.loads((work / "input.json").read_text())
    result = json.loads((work / "result.json").read_text())
    if not result["accepted"] or result["inputHash"] != sha(fixed) or result["nativeSha256"] != hashlib.sha256((work / "native.json").read_bytes()).hexdigest():
        raise ValueError("実監査が未合格又は変更された")
    for image in fixed["bundle"]["images"]:
        if hashlib.sha256((ROOT / image["path"]).read_bytes()).hexdigest() != image["sha256"]:
            raise ValueError("画像が変更された")
    identities = {x.record["source_question_id"]: x for x in load_source_record_inventory(ROOT / f"output/gas-shunin-hei/questions_json/{year}/00_source", qualification="gas-shunin-hei", list_group_id=year)}
    planned, receipts = {}, []
    for key, row in fixed["bundle"]["rows"].items():
        source = identities[key]
        current = QuestionInventory(ROOT).projected_input("gas-shunin-hei", year, row["sourceIdentity"]["sourceRecordRef"])
        if current.errors or sha(current.record) != row["recordHash"] or source.identity.binding.as_mapping() != row["sourceIdentity"] or hashlib.sha256(source.path.read_bytes()).hexdigest() != row["sourceSha256"]:
            raise ValueError(f"監査対象が変更された: {key}")
        raw = {"questionIntent": row["proposal"]["questionIntent"], **row["sourceIdentity"], "original_question_id": source.identity.binding.review_question_id,
               "questionIntent_change_reason": row["proposal"]["rationale"]}
        entry = materialize_entries("question_intent", [SourcePatchInput(current.record, source.identity)], [raw])[0]
        target = source.path.parent.parent / "15_correctChoiceText_fixed" / f"{source.path.stem}_merged_correctChoiceText_fixed.json"
        legacy = target.with_name(f"{source.path.stem}_correctChoiceText_fixed.json")
        if legacy.exists():
            if target.exists():
                raise ValueError("既存形式との重複")
            target = legacy
        planned.setdefault(target, []).append(entry)
        receipts.append({"sourceQuestionId": key, "sourceIdentity": row["sourceIdentity"], "beforeRecordHash": row["recordHash"], "questionIntent": entry["questionIntent"]})
    for target, entries in planned.items():
        old = json.loads(target.read_text()) if target.exists() else []
        if target.exists():
            backup = RUN / "patch_backups" / hashlib.sha256(target.read_bytes()).hexdigest()
            if not backup.exists():
                backup.write_bytes(target.read_bytes())
        rows = {q["sourceRecordRef"]: q for q in old}
        if len(rows) != len(old):
            raise ValueError("既存15層のidentity重複")
        for entry in entries:
            rows[entry["sourceRecordRef"]] = entry
        dump(target, sorted(rows.values(), key=lambda q: int(q["sourceRecordRef"].rsplit("#", 1)[1])))
    for row in receipts:
        current = QuestionInventory(ROOT).projected_input("gas-shunin-hei", year, row["sourceIdentity"]["sourceRecordRef"])
        prior = fixed["bundle"]["rows"][row["sourceQuestionId"]]["currentRecord"]
        changed = {k for k in set(prior) | set(current.record) if prior.get(k) != current.record.get(k)}
        allowed = {"questionIntent", "questionIntent_changed", "questionIntent_change_detail", "questionIntent_change_reason"}
        if current.errors or current.record.get("questionIntent") != row["questionIntent"] or changed - allowed:
            raise ValueError(f"設問意図以外の値が変わった: {changed}")
        row["changedFields"] = sorted(changed)
        row["afterRecordHash"] = sha(current.record)
    dump(work / "saved.json", {"questions": receipts, "inputHash": result["inputHash"], "nativeSha256": result["nativeSha256"],
        "patches": [{"path": str(p.relative_to(ROOT)), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in planned]})
    print(year, "saved intent only", len(receipts), flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "review":
        proposals = json.loads(Path(sys.argv[2]).read_text())
        grouped = {}
        for key, row in proposals.items():
            grouped.setdefault(key.split(":")[0], {})[key] = row
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda pair: review(*pair), grouped.items()))
    elif sys.argv[1] == "save":
        save(sys.argv[2])
