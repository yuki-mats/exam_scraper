"""直接執筆・実別session確認済みの候補だけを、既存materializerでpatchへ保存する。"""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / "output/gas-shunin-hei/direct_publication/20261027"
sys.path.insert(0, str(RUN / "tool_snapshot"))
from scripts.common.question_identity import load_source_record_inventory
from scripts.fix.materialize_minimal_patch import SourcePatchInput, materialize_entries


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def save(candidates_path):
    candidates = json.loads(candidates_path.read_text())
    records = json.loads((RUN / "records.json").read_text())
    identities = {}
    for year in {q["sourceQuestionId"].split(":")[0] for q in candidates}:
        for item in load_source_record_inventory(ROOT / f"output/gas-shunin-hei/questions_json/{year}/00_source",
                                                qualification="gas-shunin-hei", list_group_id=year):
            identities[item.record["source_question_id"]] = item
    planned = {}
    receipts = []
    for candidate in candidates:
        key = candidate["sourceQuestionId"]
        packet = records[key]
        if candidate["isLawRelated"] is not False:
            raise ValueError("この保存helperは独立確認済みの非法令問題だけを扱う")
        accepted = []
        for audit_dir in (RUN / "content_audits").glob(key.replace(":", "_") + "*"):
            result_path = audit_dir / "result.json"
            if not result_path.exists():
                continue
            result = json.loads(result_path.read_text())
            fixed = json.loads((audit_dir / "input.json").read_text())
            if fixed["bundle"]["candidate"] != candidate or not result["accepted"]:
                continue
            native_path = ROOT / result["nativePath"]
            if hashlib.sha256(native_path.read_bytes()).hexdigest() != result["nativeSha256"]:
                raise ValueError("native audit hash changed")
            native = json.loads(native_path.read_text())
            if native.get("changed_files") or native.get("subagent_thread_ids"):
                raise ValueError("read-only監査境界を超えた")
            if not all(native.get(name) for name in ("session_id", "thread_id", "turn_id")):
                raise ValueError("実session証跡がない")
            if sha(fixed["bundle"]) != result["inputHash"]:
                raise ValueError("固定input hash不一致")
            attached = {i["sourcePath"]: i["sha256"] for i in native["image_inputs"]}
            if attached != {i["path"]: i["sha256"] for i in fixed["bundle"]["images"]}:
                raise ValueError("実画像入力が公式PDF画像と一致しない")
            for image in fixed["bundle"]["images"]:
                if hashlib.sha256((ROOT / image["path"]).read_bytes()).hexdigest() != image["sha256"]:
                    raise ValueError("公式画像が変更された")
            accepted.append((result, result_path))
        if not accepted:
            raise ValueError(f"同一候補の独立確認が未合格: {key}")
        audit, audit_path = accepted[-1]
        source = identities[key]
        if hashlib.sha256(source.path.read_bytes()).hexdigest() != packet["sourceSha256"]:
            raise ValueError("00_source hash changed")
        record = packet["record"]
        choices = record["choiceTextList"]
        if len(choices) != len(candidate["correctChoiceText"]):
            raise ValueError("同一問の選択肢・正答件数不一致")
        expected_explanations = len(choices) if candidate.get("questionType", record["questionType"]) == "true_false" else 1
        if len(candidate["explanationText"]) != expected_explanations:
            raise ValueError("解説数が確定済み形式と不一致")
        facts = []
        for text, verdict in zip(choices, candidate["correctChoiceText"]):
            judgment = audit["review"]["choiceJudgments"][sha(text)]
            if judgment["verdict"] != verdict:
                raise ValueError("独立判定と不一致")
            facts.append({"auditStatus": "not_law_related", "reviewState": "secondary_verified",
                "examTime": {"correctChoiceText": verdict}, "current": {"correctChoiceText": verdict},
                "differenceFacts": [], "answerImpactFacts": [], "reconciliationStatus": "not_applicable",
                "evidenceSummary": {"verdict": "not_law_related", "explanationText": candidate["classificationReason"],
                    "differenceSummary": "非法令問題。法令差分は適用しない。", "promptContext": text,
                    "refs": [], "displayRefIds": []},
                "notes": ["主担当の直接判定と別sessionの全肢確認が一致。", str(audit_path.relative_to(ROOT))]})
        raw = {**candidate, **source.identity.binding.as_mapping(),
               "original_question_id": source.identity.binding.review_question_id,
               "lawGroundedExplanationNotNeeded": True, "lawReferences": [[] for text in choices],
               "lawContextForExplanation": candidate["classificationReason"], "lawRevisionFacts": facts,
               "suggestedQuestionDetailsByChoice": []}
        source_input = SourcePatchInput(record, source.identity)
        tasks = [
            ("correct_choice", "23_correctChoiceText_fixed", "correctChoiceText_fixed"),
            ("law_context", "18_law_context_prepared", "lawContext_prepared"),
            ("explanation", "21_explanationText_added", "explanationText_added"),
            ("question_set", "22_questionSetId_linked", "questionSetId_linked"),
        ]
        if "questionType" in candidate or "isCalculationQuestion" in candidate:
            if not isinstance(candidate.get("isCalculationQuestion"), bool) or not candidate.get("questionType"):
                raise ValueError("形式を更新する候補には独立確定した2fieldが必要")
            tasks.append(("question_type", "10_questionType_fixed", "questionType_fixed"))
        if "questionIntent" in candidate:
            tasks.append(("question_intent", "15_correctChoiceText_fixed", "correctChoiceText_fixed"))
        for task, directory, suffix in tasks:
            target = source.path.parent.parent / directory / f"{source.path.stem}_merged_{suffix}.json"
            if task == "question_type":
                target = source.path.parent.parent / directory / f"{source.path.stem}_{suffix}.json"
            entry = materialize_entries(task, [source_input], [raw])[0]
            planned.setdefault(target, []).append(entry)
        receipts.append({"sourceQuestionId": key, "sourceIdentity": source.identity.binding.as_mapping(),
                         "candidateHash": sha(candidate), "auditPath": str(audit_path.relative_to(ROOT)),
                         "nativeSha256": audit["nativeSha256"]})
    # 全候補を検証してから正式patchを変更する。既存の別問題はexact bindingで保持する。
    for target, entries in planned.items():
        old = json.loads(target.read_text()) if target.exists() else []
        original = target.read_bytes() if target.exists() else None
        by_identity = {q["sourceRecordRef"]: q for q in old}
        if len(by_identity) != len(old):
            raise ValueError(f"既存patchに重複identity: {target}")
        for entry in entries:
            by_identity[entry["sourceRecordRef"]] = entry
        new = sorted(by_identity.values(), key=lambda row: int(row["sourceRecordRef"].rsplit("#", 1)[1]))
        if original is not None:
            backup = RUN / "patch_backups" / hashlib.sha256(original).hexdigest()
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                backup.write_bytes(original)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n")
    receipt_path = RUN / "saved" / (candidates_path.stem + ".json")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps({"candidates": receipts,
        "patches": [{"path": str(p.relative_to(ROOT)), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                    for p in planned]}, ensure_ascii=False, indent=2) + "\n")
    print("saved", len(receipts), "reviewed questions", len(planned), "patch files")


if __name__ == "__main__":
    save(Path(sys.argv[1]))
