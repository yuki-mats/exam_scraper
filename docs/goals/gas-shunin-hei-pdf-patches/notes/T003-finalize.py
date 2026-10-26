#!/usr/bin/env python3
"""Verify saved native reviews and normal merged records; archive restorable patches."""
import collections
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from scripts.common.question_identity import load_source_record_inventory
from scripts.merge.record_projection import project_merge_record
from tools.question_bank.question_issue_reports import (
    DEFAULT_CONFIG_PATH, load_config, routed_workflow_contracts,
    sha256_json, validate_blind_review, validate_challenge_review,
    validate_resume_blind_consensus, verify_patch_against_record,
)

BASE = ROOT / "output/gas-shunin-hei/pdf_patch_review"
NOTES = Path(__file__).parent


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def run_records(work):
    work = Path(work).resolve()
    frozen = read(work / "frozen-plan.json")
    plan = frozen["plan"]
    driver = NOTES / {"runs-v2": "T003-review-driver.py", "runs-v3": "T004-review-driver.py", "runs-v4": "T005-review-driver.py"}[work.parent.name]
    m = module(driver, "hei_review_" + work.parent.name.replace("-", "_"))
    assert sha(driver) == frozen["driverSha256"]
    config = load_config(DEFAULT_CONFIG_PATH)
    contracts, _ = routed_workflow_contracts(config, "question_content")
    assert contracts == frozen["contracts"]
    assert sha(DEFAULT_CONFIG_PATH) == frozen["categoryConfigSha256"]
    items = {x["sourceQuestionId"]: x for x in plan["items"]}
    assert m.image_manifest([x["sourcePath"] for x in frozen["images"]], ROOT) == frozen["images"]
    for item in items.values():
        assert sha(ROOT / item["sourcePath"]) == item["sourceSha256"]
        assert sha(ROOT / item["questionPdfPath"]) == item["questionPdfSha256"]
    phase_reviews = {}
    sessions = []
    provenance = {"workDirectory": str(work.relative_to(ROOT)), "driverSha256": frozen["driverSha256"], "phases": {}}
    for phase in ("blind_a", "blind_b", "challenge"):
        received = read(work / f"{phase}-received.json")
        inp = read(work / f"{phase}-input.json")
        assert received["receiptHash"] == sha256_json({k: v for k, v in received.items() if k != "receiptHash"})
        assert received["inputHash"] == sha256_json(inp)
        assert sha(work / f"{phase}-prompt.txt") == inp["promptSha256"]
        n = received["native"]
        m.validate_native(n, frozen["images"], other_identities=sessions)
        sessions.append(n)
        raw = m.strict_json(n["final_message"])
        if "outputSchema" in inp:
            assert sha256_json(inp["outputSchema"]) == inp["outputSchemaSha256"]
            m.validate_output_schema(raw, inp["outputSchema"])
        by_work = m.split_results(n["final_message"], set(received["expectedWorkIds"]))
        rows = {x["sourceQuestionId"]: x for x in inp["rows"]}
        assert set(rows) == set(received["expectedIds"])
        transport = {x["sourceQuestionId"]: x for x in inp["transportedItems"]}
        for key, row in rows.items():
            assert m.unpack_prompt(transport[key]["normalPromptTransport"], inp["sharedContracts"]) == row["normalPrompt"]
        phase_reviews[phase] = {key: by_work[row["workId"]] for key, row in rows.items()}
        assert read(work / f"{phase}-validated.json")["reviews"] == phase_reviews[phase]
        provenance["phases"][phase] = {"sessionId": n["session_id"], "threadId": n["thread_id"], "turnId": n["turn_id"], "receivedPath": str((work / f"{phase}-received.json").relative_to(ROOT)), "receivedSha256": sha(work / f"{phase}-received.json"), "promptSha256": inp["promptSha256"], "schemaSha256": inp.get("outputSchemaSha256"), "images": n["image_inputs"]}
    inventory = load_source_record_inventory(ROOT / f"output/gas-shunin-hei/questions_json/{plan['year']}/00_source", qualification="gas-shunin-hei", list_group_id=str(plan["year"]))
    source_by_binding = {x.identity.binding.as_tuple(): x for x in inventory}
    saved = read(work / "save-result.json")["results"]
    results = []
    for result in saved:
        key = result["sourceQuestionId"]
        if result["decision"] == "hold":
            continue
        item = items[key]
        a, b, c = (phase_reviews[p][key] for p in ("blind_a", "blind_b", "challenge"))
        ai = next(x for x in read(work / "blind_a-input.json")["rows"] if x["sourceQuestionId"] == key)
        ci = next(x for x in read(work / "challenge-input.json")["rows"] if x["sourceQuestionId"] == key)
        for slot, review in (("A", a), ("B", b)):
            validate_blind_review(review, slot=slot, input_hash=ai["inputHash"], workflow_contract_hashes=[x["contentHash"] for x in contracts], category="question_content", config=config)
            assert review["evidence"] == item["evidence"]
        validate_resume_blind_consensus(a, b)
        validate_challenge_review(c, input_hash=ci["inputHash"], blind_reviews=[a, b], blind_hashes=[sha256_json(a), sha256_json(b)], category="question_content", config=config)
        replay = work / "individual" / item["workItem"]["workId"] / "standard-replay"
        assert read(replay / "blind_input.json") == ai["input"]
        for p, value in (("blind_a", a), ("blind_b", b), ("challenge", c)):
            assert read(replay / f"{p}.json") == value
        assert read(replay / "driver-replay-receipt.json")["reviews"] == [sha256_json(x) for x in (a, b, c)]
        row = {"sourceQuestionId": key, "decision": c["decision"], "sourceBinding": item["sourceBinding"], "before": item["currentRecord"], "reviews": {"blindA": a, "blindB": b, "challenge": c}, "nativeWorkDirectory": str(work.relative_to(ROOT))}
        if c["decision"] == "fix":
            assert c["changes"] == item["candidateChanges"], "New content needs individual image adjudication: " + key
            path = ROOT / result["patchPath"]
            assert sha(path) == result["patchSha256"]
            assert sha(ROOT / result["stagedPath"]) == sha(path)
            patch = read(path)
            assert patch["entries"][0]["changes"] == a["proposedChanges"] == b["proposedChanges"] == c["changes"]
            assert patch["blindReviewHashes"] == [sha256_json(a), sha256_json(b)]
            assert patch["challengeReviewHash"] == sha256_json(c)
            binding = tuple(item["sourceBinding"][k] for k in ("sourceQuestionKey", "reviewQuestionId", "sourceRecordRef"))
            entry = source_by_binding[binding]
            # The standard verifier creates a temporary current-record sidecar.
            # Keep that sidecar outside the formal overlay reader directory.
            verify_path = work / "audit-reverify" / path.name
            m.freeze(verify_path, patch)
            assert sha(verify_path) == sha(path)
            after = verify_patch_against_record(verify_path, current_record=item["currentRecord"], source_identity=entry.identity, config_path=DEFAULT_CONFIG_PATH)
            row.update(after=after, patch={"path": result["patchPath"], "sha256": sha(path), "bytesUtf8": path.read_text()})
        else:
            assert c["decision"] == "no_change" and not c.get("changes")
            row["after"] = item["currentRecord"]
        results.append(row)
    return results, provenance


def main():
    baseline = read(NOTES / "source-baseline.json")
    for x in baseline:
        assert sha(ROOT / x["path"]) == x["sha256"]
    original_review = read(ROOT / "document/temporary/20261027_gas_shunin_hei_pdf_review.json")
    old_flags = {x["sourceQuestionId"] for x in original_review["records"] if x["needsReview"]}
    expected = set(old_flags)
    preparation = read(NOTES / "T003-preparation-native.json")["payload"]["goalbuddy_receipt_v1"]["evidence"][0]
    for x in preparation["commonNotes"]:
        expected.update(x["lawApplySourceIds"])
        expected.update(x["kisoApplySourceIds"])
    works = [BASE / "runs-v2/000-2017", *sorted((BASE / "runs-v3").glob("*")), *sorted((BASE / "runs-v4").glob("*"))]
    records = {}
    native_batches = []
    for work in works:
        if not (work / "save-result.json").is_file():
            continue
        if not any(r["decision"] in {"fix", "no_change"} for r in read(work / "save-result.json")["results"]):
            continue
        rows, provenance = run_records(work)
        native_batches.append(provenance)
        for row in rows:
            key = row["sourceQuestionId"]
            assert key not in records, "multiple accepted final reviews: " + key
            records[key] = row
    assert set(records) == expected, {"missing": sorted(expected - set(records)), "extra": sorted(set(records) - expected)}
    assert len(records) == 306
    group_stats = []
    all_ids = set()
    for year in range(2017, 2026):
        group = ROOT / f"output/gas-shunin-hei/questions_json/{year}"
        inventory = load_source_record_inventory(group / "00_source", qualification="gas-shunin-hei", list_group_id=str(year))
        merged_paths = sorted((group / "30_merged_2").glob("*.json"))
        assert len(merged_paths) == 3
        merged = {}
        for p in merged_paths:
            for q in read(p)["question_bodies"]:
                key = q["source_question_id"]
                assert key not in merged
                merged[key] = q
        assert len(merged) == len(inventory) == 58
        for entry in inventory:
            key = entry.record["source_question_id"]
            before = project_merge_record(entry.record).merged2
            if key in records:
                assert records[key]["before"] == before
            after = records[key]["after"] if key in records else before
            assert merged[key] == after, key
            assert entry.identity.binding.review_question_id not in all_ids
            all_ids.add(entry.identity.binding.review_question_id)
            changed = {k for k in set(before) | set(after) if (k in before) != (k in after) or before.get(k) != after.get(k)}
            assert changed <= {"questionBodyText"}, (key, changed)
        common = next(x for x in preparation["commonNotes"] if x["year"] == year)
        for field, note in (("lawApplySourceIds", "lawNotes"), ("kisoApplySourceIds", "kisoNotes")):
            prefix = "【科目共通注】\n" + "\n".join(common[note]) + "\n\n"
            for key in common[field]:
                assert merged[key]["questionBodyText"].startswith(prefix)
        yr = [r for k, r in records.items() if k.startswith(str(year) + ":")]
        group_stats.append({"year": year, "sourceQuestions": 58, "originalFlagged": len([k for k in old_flags if k.startswith(str(year) + ":")]), "patched": sum(r["decision"] == "fix" for r in yr), "noChange": sum(r["decision"] == "no_change" for r in yr), "commonNotePatched": 31})
    assert len(all_ids) == 522
    assert sum(r["decision"] == "fix" for r in records.values()) == 289
    assert sum(r["decision"] == "no_change" for r in records.values()) == 17
    assert sum(records[k]["decision"] == "fix" for k in old_flags) == 53
    gate_results = read(BASE / "verification/commands.json")
    output = ROOT / "document/temporary/20261027_gas_shunin_hei_pdf_patches.json"
    payload = {"schemaVersion": "gas-shunin-hei-official-pdf-patches/v1", "generatedAt": datetime.now(timezone.utc).isoformat(), "scope": "70 flagged source questions and section-wide common notes, local correction overlays only", "summary": {"originalFlagged": 70, "originalFlaggedFixed": 53, "originalFlaggedNoChange": 17, "sectionCommonNotes": 279, "formalPatchedQuestions": 289, "sourceFilesUnchanged": 27, "sourceQuestionIdentitiesUnchanged": 522, "hold": 0}, "interpretation": ["未採用の公式転記候補を実画像で確認したA/BとChallenge。各phaseは新規sessionで、batch内は問別完全値。候補非提示の独立導出ではない。", "固定metadataは送信schemaで構造保証し、判断・findings・完全changesはモデルが返した実値。", "図の軸・位置・接続・境界と脚注を本文へ補完。正答・解説・既存IDを変更していない。", "試験受験・採点注意は年度単位補助資料に保持し、各学習問題へ一律追加しない。", "元HTMLの余計SVGは取得証跡として保持。該当JSONが既に公式と一致する17問は同値patchを作らない。", "機械ゲートの成功・未整備由来の不通過を区別し、Firestore/Storage公開又は全工程整備完了と解釈しない。"], "sourceBaseline": baseline, "years": group_stats, "verification": gate_results, "examCommonNotes": read(BASE / "exam-common-notes.json"), "examNotesSecondCheck": read(NOTES / "T003-exam-notes-second-check.json"), "nativeBatches": native_batches, "records": [records[k] for k in sorted(records)]}
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    lines = ["# 丙種・公式PDFに基づくパッチ修正（2026年10月27日）", "", "前回の要確認70問を一問ずつ確認し、53問の不足を修正、保存JSONが既に公式どおりの17問は修正不要として解消した。科目共通注は法令144問・基礎135問の全279問へ補完し、個別補完を合わせて289問の正式24パッチを保存した。保留は0問。", "", "図は分類の親子関係、U字管の接続・水面高低、一次空気率／インプット図の軸・領域・境界を本文へ補完した。PS LPG表示の形状と法令脚注も補った。2024年の特例除外と、2025年の明示出題を優先する条件・承認による適用除外条件を保持している。", "", "`00_source`全27ファイル、全522問の既存ID、正答・解説のpresenceと値は不変。受験・採点の共通注意45項目は年度別資料へ保存し、二次の実画像照合でも一致した。", "", "|年度|元の要確認|パッチ保存|JSON修正不要|共通注適用|", "|---|---:|---:|---:|---:|"]
    for r in group_stats:
        lines.append(f"|{r['year']}|{r['originalFlagged']}|{r['patched']}|{r['noChange']}|31|")
    lines += ["", "A/Bは未採用の転記候補を同年度の公式画像で別sessionから確認し、Challengeで完全値を照合した。問ごとの独立session又は候補を見ない独立導出とはしていない。元応答・画像実送信hash・schema・正規prompt・標準replayを保存し、判定と修正文を後補正していない。", "", "検証結果は機械可読報告のverificationに実コマンド・終了値・ログを保存した。通常mergeで全289問の反映と対象外不変を確認した。全体回帰テストは実行していない。", "", "保存先:", "", "- 正式パッチ: `output/gas-shunin-hei/questions_json/<年度>/24_questionIssueCorrections/`", "- 通常merge: 各年度の`30_merged_2/`", "- 実レビュー・共通注意・検証ログ: `output/gas-shunin-hei/pdf_patch_review/`", "- [全問結果・復元可能なパッチ実bytes](20261027_gas_shunin_hei_pdf_patches.json)", "", "取得原文と原HTMLは保持した。Firestore・Storageへの反映、現行法監査、問題形式・解説などの全工程整備は実施していない。", "", "機械可読報告SHA256: `" + sha(output) + "`", ""]
    (output.with_suffix(".md")).write_text("\n".join(lines))
    print(json.dumps(payload["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
