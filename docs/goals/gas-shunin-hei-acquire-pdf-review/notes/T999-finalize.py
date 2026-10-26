"""固定入力・全二段階記録・PM個別判断を検査し、今回の監査資料を出力する。"""
import copy
import hashlib
import json
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[4]
BASE = ROOT / "output/gas-shunin-hei/pdf_review"
GOAL = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


inventory_path = ROOT / "output/gas-shunin-hei/acquisition/20261027/source_inventory.json"
inventory = read(inventory_path)
assert inventory["questionCount"] == 522 and len(inventory["files"]) == 27
actual_files = set((ROOT / "output/gas-shunin-hei/questions_json").glob("*/00_source/*.json"))
assert actual_files == {ROOT / x["path"] for x in inventory["files"]}
for entry in inventory["files"]:
    assert sha(ROOT / entry["path"]) == entry["sha256"]
catalog_path = ROOT / "document/sources/gas-shunin/official_exam_pdf_catalog.json"
catalog = read(catalog_path)
pdf_entries = [x for x in catalog["files"] if x["grades"] == ["hei"]
               and x["documentType"] in {"mark_sheet_question", "answer"}]
assert len(pdf_entries) == 18
for entry in pdf_entries:
    assert sha(ROOT / entry["localPath"]) == entry["sha256"]

adjudication_path = GOAL / "notes/T999-adjudications.json"
adjudications = read(adjudication_path)
resolved = {x["sourceQuestionId"]: x for x in adjudications["resolutions"]}
assert len(resolved) == 16
for image in adjudications["pmViewedImages"]:
    assert sha(ROOT / image["path"]) == image["sha256"]
earlier = adjudications["additionalEarlierViewedImage"]
assert sha(ROOT / earlier["path"]) == earlier["sha256"]

records, years, comparison_ids, all_public_ids = [], [], set(), set()
image_count = 0
for year in range(2017, 2026):
    packet_path = BASE / str(year) / "packet.json"
    packet = read(packet_path)
    cases = {q["sourceQuestionId"]: q for q in packet["questions"]}
    assert len(cases) == 58
    for pdf in packet["pdfs"].values():
        assert sha(ROOT / pdf["path"]) == pdf["sha256"]
        for page in pdf["pages"]:
            assert sha(ROOT / page["imagePath"]) == page["imageSha256"]
            image_count += 1
    reports = {stage: read(BASE / stage / f"{year}.json") for stage in ["primary", "secondary"]}
    assert reports["primary"]["reviewerSession"] != reports["secondary"]["reviewerSession"]
    by_stage = {stage: {x["sourceQuestionId"]: copy.deepcopy(x) for x in r["records"]}
                for stage, r in reports.items()}
    for stage, rows in by_stage.items():
        assert len(rows) == 58 and set(rows) == set(cases)
        assert reports[stage]["viewedAnswerPages"] == [1]
    if year == 2018:
        amendment = read(BASE / "primary/amendments/2018-01.json")
        assert amendment["baseReportSha256"] == sha(BASE / "primary/2018.json")
        for change in amendment["changes"]:
            row = by_stage["primary"][change["sourceQuestionId"]]
            row["bodyStatus"] = change["bodyStatus"]
            row["issues"].append(change["appendIssue"])
        reports["primary"]["commonSourceNoteGaps"] = amendment["commonSourceNoteGaps"]
    stage_differences = Counter()
    archive_differences = flagged = 0
    for sid, q in cases.items():
        pri, sec = by_stage["primary"][sid], by_stage["secondary"][sid]
        actual_source = read(ROOT / q["source"]["path"])["question_bodies"]
        actual_record = next(x for x in actual_source if x["source_question_id"] == sid)
        assert actual_record == q["source"]["record"]
        public_id = actual_record["public_question_id"]
        assert public_id not in all_public_ids
        all_public_ids.add(public_id)
        assert sha(ROOT / q["htmlSnapshot"]["path"]) == q["htmlSnapshot"]["sha256"]
        for stage, row in [("primary", pri), ("secondary", sec)]:
            assert row["sourceSha256"] == sha(ROOT / q["source"]["path"])
            assert row["questionPdfSha256"] == q["officialQuestionPdf"]["sha256"]
            assert row["answerPdfSha256"] == q["officialAnswerPdf"]["sha256"]
            assert set(row["questionPdfPages"]) <= set(reports[stage]["viewedQuestionPages"])
            assert row["note"] and row["answerStatus"] == "match"
            assert actual_record["answer_result_inferred_correct_choice_numbers"] == [row["officialCorrectChoiceNumber"]]
            if any(row[k] == "difference" for k in ["bodyStatus", "choicesStatus", "answerStatus"]):
                stage_differences[stage] += 1
        assert pri["officialCorrectChoiceNumber"] == sec["officialCorrectChoiceNumber"]
        different_fields = [k for k in ["bodyStatus", "choicesStatus", "answerStatus", "officialCorrectChoiceNumber"]
                            if pri[k] != sec[k]]
        if different_fields:
            comparison_ids.add(sid)
            assert sid in resolved
            final = copy.deepcopy(resolved[sid])
            assert final["packetSha256"] == sha(packet_path)
            assert final["sourceSha256"] == q["source"]["sha256"]
        else:
            needs = any(sec[k] == "difference" for k in ["bodyStatus", "choicesStatus", "answerStatus"])
            final = {"sourceQuestionId": sid, "archiveStatus": "difference" if needs else "match",
                     "sourceRecordStatus": "not_certified_as_standalone", "needsReview": needs,
                     "reason": "一次・二次の問題別判定が一致。科目共通注の欠落は年度情報へ別記。"}
        archive_gap = final["archiveStatus"] != "match"
        archive_differences += int(archive_gap)
        flagged += int(final["needsReview"])
        records.append({"sourceQuestionId": sid, "year": year, "publicQuestionId": public_id,
                        "source": {"path": q["source"]["path"], "sha256": q["source"]["sha256"]},
                        "questionPdf": q["officialQuestionPdf"], "answerPdf": q["officialAnswerPdf"],
                        "htmlSnapshot": q["htmlSnapshot"], "primary": pri, "secondary": sec,
                        "pmResolution": final if different_fields else None,
                        "archiveDifference": archive_gap, "needsReview": final["needsReview"],
                        "sourceRecordStatus": final["sourceRecordStatus"]})
    years.append({"year": year, "questions": 58, "officialAnswerMatches": 58,
                  "primaryFlagged": stage_differences["primary"], "secondaryFlagged": stage_differences["secondary"],
                  "archiveDifferenceQuestions": archive_differences, "flaggedQuestions": flagged,
                  "packetSha256": sha(packet_path),
                  "reviewReportAmendments": [{"path": "output/gas-shunin-hei/pdf_review/primary/amendments/2018-01.json",
                                               "sha256": sha(BASE / "primary/amendments/2018-01.json")}]
                  if year == 2018 else [],
                  "reviewReports": {stage: {"path": str((BASE/stage/f'{year}.json').relative_to(ROOT)),
                                            "sha256": sha(BASE/stage/f'{year}.json'),
                                            "reviewer": reports[stage]["reviewerSession"],
                                            "nativeMessageId": reports[stage].get("nativeMessageId")}
                                    for stage in reports},
                  "commonSourceNoteGaps": {stage: reports[stage].get("commonSourceNoteGaps", reports[stage].get("sectionNotes", []))
                                           for stage in reports}})

assert len(records) == 522 and len({r['sourceQuestionId'] for r in records}) == 522
assert comparison_ids == set(resolved)
assert image_count == 321
foreign_inputs, foreign_count = [], 0
for qualification in ["gas-shunin-kou", "gas-shunin-otsu"]:
    paths = sorted((ROOT / "output" / qualification / "questions_json").glob("*/00_source/*.json"))
    assert paths
    for path in paths:
        rows = read(path)["question_bodies"]
        foreign_count += len(rows)
        assert not {q.get("public_question_id") for q in rows} & all_public_ids
        foreign_inputs.append({"path": str(path.relative_to(ROOT)), "sha256": sha(path)})
foreign_digest = hashlib.sha256(json.dumps(foreign_inputs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
report = {
    "schemaVersion": "gas-shunin-hei-acquisition-double-pdf-review/v1",
    "generatedAt": datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(),
    "scope": "丙種2017〜2025マークシート全522問の取得・公式問題/正答PDF一次及び独立二次照合。差異の是正・解説全面監査・公開は含まない。",
    "inputCodeCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    "workingTreeAtAudit": subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True).splitlines(),
    "verifierInputs": [{"path": str(p.relative_to(ROOT)), "sha256": sha(p)} for p in [
        ROOT / "scripts/check/prepare_gas_shunin_pdf_review.py",
        ROOT / "scripts/check/record_gas_shunin_pdf_review_receipts.py",
        GOAL / "notes/T005-save-secondary.py", Path(__file__).resolve()]],
    "summary": {"years": 9, "sourceFiles": 27, "questions": 522, "uniquePublicQuestionIds": 522,
                "primaryReviewed": 522, "secondaryReviewed": 522, "officialAnswerMatches": 522,
                "archiveDifferenceQuestions": sum(y['archiveDifferenceQuestions'] for y in years),
                "flaggedQuestions": sum(y['flaggedQuestions'] for y in years),
                "jsonOnlyAdditionalFindingIds": [r['sourceQuestionId'] for r in records if r['needsReview'] and not r['archiveDifference']],
                "pmReconciledFieldDisagreements": len(resolved), "verifiedOfficialPdfs": 18,
                "verifiedRenderedImages": image_count, "rejectedReviewReceipts": 1, "explicitNativeAmendments": 1},
    "acquisition": {"sourceUrl": "https://gassyunin.com/exam/hei/", "standardRequests": len(inventory['requests']),
                    "minimumMeasuredEndToNextStartSeconds": inventory['requestGapMinimumSeconds'],
                    "method": "site専用sessionが各リクエスト前に5〜9秒待機し排他。標準取得9回は全HTTP200。別に事前調査2ページを単発取得。",
                    "inventoryPath": str(inventory_path.relative_to(ROOT)), "inventorySha256": sha(inventory_path)},
    "officialCatalog": {"path": str(catalog_path.relative_to(ROOT)), "sha256": sha(catalog_path)},
    "idCollisionCheck": {"foreignQualifications": ["gas-shunin-kou", "gas-shunin-otsu"],
                         "foreignSourceFiles": len(foreign_inputs), "foreignSourceRecords": foreign_count,
                         "foreignInventorySha256": foreign_digest, "collisions": []},
    "pmAdjudications": {"path": str(adjudication_path.relative_to(ROOT)), "sha256": sha(adjudication_path)},
    "interpretation": [
        "archiveDifferenceはrawHTMLとsourceを併せた取得成果物と公式原問の差異。JSON単体での図・注記の完全性を保証しない。",
        "標準状態の数値が一致する問も、公式の共通絶対圧力注が未収録の場合は文脈補完の指摘として含む。数値・公式選択答の誤りとは区別する。",
        "共通注欠落は年度・科目全体の問題として別記。個別の指摘問数は全適用問の法的影響を網羅的に確定した数ではない。",
        "JSONだけに図情報がない問題を別区分へ残す。差異のない判定を公開可能又は全工程整備済みと解釈しない。",
        "公式番号答から個数/組合せ問題の各記述の真偽やサイト解説の全面正確性を確定していない。",
    ],
    "verification": {"targetedTests": "55 tests total: 50 passed, 5 live-dependent skipped",
                     "sourceQualityGate": "9 years passed", "scopedImmutability": "27 new, 0 modified",
                     "globalImmutability": "横断登録は対象外資格2946欠損・33未登録を検出し不通過。対象外を変更せず丙種9scopeのみ登録。",
                     "broadRegression": "未実行。変更範囲のテスト・実取得・source gate・全証跡hash/coverageに限定。",
                     "patchMergeChecks": "patch作成/mergeなし。mergedの3checkerは適用対象外。"},
    "reviewCorrection": {"sourceQuestionId": "2022:shohi:問23", "incorrectReceiptNumber": 3,
                         "officialNumberAfterImageRecheck": 5, "sourceNumber": 5,
                         "evidence": "secondary/rejected と secondary/amendments/2022.json を保持。明示source IDで同作者の画像再読訂正を投影しsourceは変更しない。"},
    "years": years, "records": records,
}
out = ROOT / "document/temporary/20261027_gas_shunin_hei_pdf_review.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
md = ["**丙種の取得・公式PDF二次照合（2026年10月27日）**", "",
      "2017〜2025年度の全522問を既存scraperで取得し、全問を公式問題PDF・正答PDFに一次照合した後、一次の判断を継承しない別セッションで二次照合した。公式正答番号は522問すべて一致した。取得原文の訂正、問題整備、Firestore公開は今回の作業範囲に含めていない。", "",
      f"取得成果物全体と原問の差異は**{report['summary']['archiveDifferenceQuestions']}問**。これに加え、JSON単体に分類図の関係がない2017年度・供給問17を記録し、個別の差異・要確認は計**{report['summary']['flaggedQuestions']}問**となった。年度・科目共通注の欠落はこの個別件数と別に全9年度へ記録している。", "",
      "| 年度 | 取得・一次・二次 | 正答番号一致 | 原本との差異 | JSON単体の追加指摘を含む |",
      "| --- | ---: | ---: | ---: | ---: |"]
for y in years:
    md.append(f"| {y['year']} | {y['questions']} | {y['officialAnswerMatches']} | {y['archiveDifferenceQuestions']} | {y['flaggedQuestions']} |")
md += ["", "主な差異は、原問にない導管図・WI/MCP図の混入、2023年度・消費問20の一次空気率/インプット図から別図への置換、2024年度・基礎問6の水位高低関係の欠落、法令略称や車両定義の脚注欠落、科目共通の圧力基準・特例除外条件の未収録である。正答番号の一致だけで問題全体の一致とはしていない。", "",
       "標準状態の本文・数値が一致する問題での絶対圧力注の未収録は、文脈の補完対象として区別した。図の文章化も、必要な関係を保持する場合は表現差として扱った。一次・二次のfield判定が分かれた16箇所はPMが公式画像と保存内容へ戻って確認し、原本の差異とJSON単体の不足を分けて記録した。", "",
       "二次記録の2022年度・消費問23には番号3と一致判定の矛盾があり、機械検査が保存を拒否した。同作者が公式正答表と当該問を再表示して番号5を確認したため、元の不整合応答と明示訂正を両方保持して結合した。取得データの正答番号は元から5であり、sourceの修正ではない。", "",
       "保存先は次のとおり。ローカルの問題・原HTML・画像・全review記録を正本とし、この報告書と機械可読の全522件結果をGitへ保存する。", "",
       "- 問題: `output/gas-shunin-hei/questions_json/<年度>/00_source/`（27ファイル）",
       "- 原HTML・アクセス証跡: `output/gas-shunin-hei/acquisition/<年度>/`",
       "- 公式原PDF: `output/pdf/gas-shunin-official/<年度>/`（既存catalog固定版を使用）",
       "- 画像・一問別packet・一次/二次記録: `output/gas-shunin-hei/pdf_review/`",
       "- 個別の全結果: [20261027_gas_shunin_hei_pdf_review.json](20261027_gas_shunin_hei_pdf_review.json)", "",
       "標準取得は並列化せず、ページ・画像・再試行の各リクエスト前に5〜9秒待機するsite専用sessionを使用した。実取得は9回すべてHTTP200、前の応答完了から次の開始までの最短は6.964059秒だった。原HTMLのhashとUTCアクセス時刻を保存した。", "",
       "関連テスト55件中50件成功、実サイト依存5件skip。全9年度のsource品質ゲート、522IDの一意性、甲乙の現sourceとの公開ID非衝突、公式18PDF・描画321枚・全source・全一次二次記録のhash/ID対応を確認した。全体回帰テストは実行していない。横断source保全登録では対象外資格の2946ファイル欠損・33未登録を検出し不通過となったため、丙種9scopeだけを追加27・更新0で登録し、対象外を変更しなかった。", "",
       "この確認は取得成果物全体の問題文・全選択肢・公式番号答の照合である。科目共通注の欠落は年度単位の補完が必要で、個別差異件数をすべての影響問の確定数とはしていない。JSON単体で全図を保持したという認定、サイト独自解説の全面的正確性、個数・組合せ問題の全記述の真偽、現行法適合性、公開可能性は別の整備・評価で確認する。", "",
       f"機械可読結果SHA-256: `{sha(out)}`", ""]
(ROOT / "document/temporary/20261027_gas_shunin_hei_pdf_review.md").write_text("\n".join(md))
print(json.dumps(report["summary"], ensure_ascii=False))
