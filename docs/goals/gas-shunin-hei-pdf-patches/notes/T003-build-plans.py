#!/usr/bin/env python3
"""Assemble ID-bound correction review inputs from the native image reader receipt."""
import copy
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from scripts.common.question_identity import load_source_record_inventory
from scripts.merge.record_projection import project_merge_record


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_once(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if path.exists():
        assert path.read_text() == encoded, str(path)
    else:
        path.write_text(encoded)


def main():
    notes = Path(__file__).parent
    preparation = json.loads((notes / "T003-preparation-native.json").read_text())
    prep = preparation["payload"]["goalbuddy_receipt_v1"]["evidence"][0]
    previous = json.loads((ROOT / "document/temporary/20261027_gas_shunin_hei_pdf_review.json").read_text())
    page_index = {r["sourceQuestionId"]: r for r in previous["records"]}
    individual = {r["sourceQuestionId"]: r for r in prep["individual"]}
    assert len(individual) == 70
    base = ROOT / "output/gas-shunin-hei/pdf_patch_review"
    stamp_path = base / "created-at.txt"
    base.mkdir(parents=True, exist_ok=True)
    if not stamp_path.exists():
        stamp_path.write_text(datetime.now(timezone.utc).isoformat())
    stamp = stamp_path.read_text()
    all_items = []
    exam_notes = []
    for common in prep["commonNotes"]:
        year = common["year"]
        group = ROOT / f"output/gas-shunin-hei/questions_json/{year}"
        assert not list(group.glob("24_questionIssueCorrections/*.json")), "Plans precede patch save"
        inventory = load_source_record_inventory(group / "00_source", qualification="gas-shunin-hei", list_group_id=str(year))
        source_map = {row.record["source_question_id"]: row for row in inventory}
        assert len(source_map) == 58
        apply = {}
        for subject, field, page_field in [("law", "lawApplySourceIds", "lawPdfPage"), ("kiso", "kisoApplySourceIds", "kisoPdfPage")]:
            ids = common[field]
            expected = {k for k in source_map if k.split(":")[1] == subject}
            assert len(ids) == len(set(ids)) and set(ids) == expected
            for source_id in ids:
                apply[source_id] = (common[f"{subject}Notes"], common[page_field])
        target_ids = set(apply) | {k for k in individual if k.startswith(f"{year}:")}
        for source_id in sorted(target_ids):
            source = source_map[source_id]
            original = source.record
            current = project_merge_record(original).merged2
            specific = individual.get(source_id)
            body = (specific or {}).get("bodyReplacement") or original["questionBodyText"]
            if (specific or {}).get("bodyAppend"):
                body += "\n\n" + specific["bodyAppend"]
            if source_id in apply:
                body = "【科目共通注】\n" + "\n".join(apply[source_id][0]) + "\n\n" + body
            changes = {"questionBodyText": body} if body != current["questionBodyText"] else {}
            assert (specific or {}).get("choicesReplacement") is None
            r = page_index[source_id]
            assert digest(ROOT / r["source"]["path"]) == r["source"]["sha256"]
            assert digest(ROOT / r["questionPdf"]["path"]) == r["questionPdf"]["sha256"]
            pages = set(r["secondary"]["questionPdfPages"])
            if specific:
                pages.update(specific["pdfPagesViewed"])
            if source_id in apply:
                pages.add(apply[source_id][1])
            images = [f"output/gas-shunin-hei/pdf_review/{year}/mark_sheet_question/page-{p:02}.png" for p in sorted(pages)]
            images.append(f"output/gas-shunin-hei/pdf_review/{year}/answer/page-01.png")
            for image in images:
                assert (ROOT / image).is_file()
            evidence = [{"sourceClass": "official", "title": f"{year}年度ガス主任技術者試験 丙種 公式問題冊子", "locator": r["questionPdf"]["path"] + " / PDF " + ",".join(map(str, sorted(pages))) + "ページ / " + source_id, "verifiedAt": stamp, "contentHash": r["questionPdf"]["sha256"]}]
            transcription = "【表示本文（共通注・図の忠実な文章表示・脚注を含む）】\n" + body + "\n\n【全選択肢（取得時の記述分解を維持）】\n" + json.dumps(original["choiceTextList"], ensure_ascii=False)
            case_id = "operator-pdf-" + source.identity.binding.review_question_id
            snapshot = {"qualificationId": "gas-shunin-hei", "listGroupId": str(year), "originalQuestionId": source.identity.binding.review_question_id, "officialEvidenceCandidates": [{**evidence[0], "localSourcePath": r["questionPdf"]["path"], "localRenderedPagePaths": images, "localRenderedPageHashes": {p: digest(ROOT / p) for p in images}, "verifiedTranscription": transcription, "candidateChanges": changes, "transcriptionRole": "公式画像を直接確認した読取担当の転記。図の位置関係は文章表示であり逐語引用ではない。実画像と比較し誤りがあれば採用しない。"}]}
            work = {"workId": source_id.replace(":", "-").replace("問", "q"), "qualificationId": "gas-shunin-hei", "listGroupId": str(year), "questionId": source.identity.binding.review_question_id, "originalQuestionId": source.identity.binding.review_question_id, **source.identity.binding.as_mapping(), "caseIds": [case_id], "caseInputHashes": {case_id: hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}, "caseSnapshots": [{"id": case_id, "canonicalSnapshot": snapshot}]}
            item = {"sourceQuestionId": source_id, "currentRecord": current, "sourceBinding": source.identity.binding.as_mapping(), "workItem": work, "evidence": evidence, "imagePaths": images, "verifiedTranscription": transcription, "candidateChanges": changes, "sourcePath": str(source.path.relative_to(ROOT)), "sourceSha256": digest(source.path), "questionPdfPath": r["questionPdf"]["path"], "questionPdfSha256": r["questionPdf"]["sha256"]}
            all_items.append(item)
        exam_notes.append({"year": year, "pdfPage": 1, "imagePath": f"output/gas-shunin-hei/pdf_review/{year}/mark_sheet_question/page-01.png", "notes": common["examNotes"]})
    assert len(all_items) == 306
    assert sum(bool(x["candidateChanges"]) for x in all_items) == 289
    write_once(base / "exam-common-notes.json", {"sourceReceipt": str((notes / "T003-preparation-native.json").relative_to(ROOT)), "sourceReceiptSha256": digest(notes / "T003-preparation-native.json"), "years": exam_notes})
    # Start with one common-note question, then bounded transport batches.
    first = next(x for x in all_items if x["sourceQuestionId"] == "2017:law:問1")
    batch_items = [[first]]
    for year in range(2017, 2026):
        rows = [x for x in all_items if x["sourceQuestionId"] != first["sourceQuestionId"] and x["sourceQuestionId"].startswith(f"{year}:")]
        batch_items.extend(rows[offset:offset + 12] for offset in range(0, len(rows), 12))
    for number, items in enumerate(batch_items):
        year = int(items[0]["sourceQuestionId"].split(":")[0])
        plan = {"schemaVersion": "gas-shunin-hei-official-correction-review-plan/v1", "year": year, "createdAt": stamp, "batchId": f"hei-pdf-{year}-{number:03}", "category": "question_content", "items": items}
        write_once(base / "plans-v2" / f"{number:03}-{year}.json", plan)
    print(json.dumps({"items": len(all_items), "changed": 289, "noChange": 17, "plans": len(batch_items)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
