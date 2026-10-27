"""元の選択正解表示と全肢真偽の差を、既存correct_answer三相確認へ渡す。"""
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / "output/gas-shunin-hei/direct_publication/20261027"
spec = importlib.util.spec_from_file_location("hei_note_existing_driver", ROOT / "docs/goals/gas-shunin-hei-production/notes/T007-note-text.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
driver = module.driver
# 同じ既存protocolを、実際の訂正内容に対応する設定categoryで呼ぶ。
driver.CATEGORY = "correct_answer"

key = "2022:kiso:問5"
packet = json.loads((RUN / "records.json").read_text())[key]
binding = packet["sourceIdentity"]
revision = sys.argv[2] if len(sys.argv) > 2 else "r2"
if revision not in {"r2", "r3"}:
    raise ValueError("未定義の訂正revision")
work = RUN / ("answer_corrections/2022-kiso-05-" + revision)
transition = RUN / "answer_corrections/2022-kiso-05-layer-transition.json"
patch23 = ROOT / "output/gas-shunin-hei/questions_json/2022/23_correctChoiceText_fixed/question_2022_1_merged_correctChoiceText_fixed.json"
if sys.argv[1] == "migrate":
    if revision != "r3" or transition.exists():
        raise ValueError("新規r3移管だけを許可する")
    prior = json.loads((RUN / "answer_corrections/2022-kiso-05-r2/reviews/save-result.json").read_text())
    if prior["results"] != [{"sourceQuestionId": key, "decision": "no_change"}]:
        raise ValueError("修正済み内容の三相確認が未完了")
    rows = json.loads(patch23.read_text())
    matches = [q for q in rows if all(q.get(k) == v for k, v in binding.items())]
    if len(matches) != 1 or matches[0]["correctChoiceText"] != ["正しい", "正しい", "間違い", "正しい", "正しい"]:
        raise ValueError("移管する主担当patchのidentity又は判定が不一致")
    current = module.QuestionInventory(ROOT).projected_input("gas-shunin-hei", "2022", binding["sourceRecordRef"])
    if current.errors:
        raise ValueError(current.errors)
    backup = RUN / "patch_backups" / driver.digest(patch23)
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        backup.write_bytes(patch23.read_bytes())
    driver.freeze(transition, {"status": "detached_23_awaiting_verified_24", "sourceIdentity": binding,
        "backup23Path": str(backup.relative_to(ROOT)), "backup23Sha256": driver.digest(patch23),
        "entry": matches[0], "beforeProjection": current.record,
        "reason": "主担当が作成した23正答訂正だけを正式なcorrect_answer三相確認を経て24へ移管する。00_sourceと他問は不変。本番書込みなし。"})
    kept = [q for q in rows if q not in matches]
    patch23.write_text(json.dumps(kept, ensure_ascii=False, indent=2) + "\n")
    print("23の所有する1問を退避。24承認待ち。backup", backup)
    raise SystemExit(0)
if revision == "r3" and not transition.exists():
    raise ValueError("移管の退避receiptが必要")
plan_path = work / "plan.json"
if not plan_path.exists():
    source = next(x for x in module.load_source_record_inventory(ROOT / "output/gas-shunin-hei/questions_json/2022/00_source",
        qualification="gas-shunin-hei", list_group_id="2022") if x.identity.binding.as_mapping() == binding)
    current = module.QuestionInventory(ROOT).projected_input("gas-shunin-hei", "2022", binding["sourceRecordRef"])
    if current.errors:
        raise ValueError(current.errors)
    created = datetime.datetime.now(datetime.timezone.utc).isoformat()
    images = ["output/gas-shunin-hei/pdf_review/2022/mark_sheet_question/page-18.png",
        "output/gas-shunin-hei/pdf_review/2022/answer/page-01.png"]
    evidence = [{"sourceClass": "official", "title": "2022年度ガス主任技術者試験 丙種 公式" + title,
        "locator": info["path"] + " / " + locator, "verifiedAt": created, "contentHash": info["sha256"]}
        for title, info, locator in [("問題冊子", packet["officialQuestionPdf"], "PDF18ページ 基礎問5"),
            ("正答表", packet["officialAnswerPdf"], "PDF1ページ 基礎問5 正答3")]]
    evidence = [{**evidence[0], "locator": evidence[0]["locator"] + "; " + evidence[1]["locator"]
        + " (answer PDF SHA256=" + evidence[1]["contentHash"] + ")"}]
    transcript = current.record["questionBodyText"] + "\n" + json.dumps(current.record["choiceTextList"], ensure_ascii=False)
    changes = {"correctChoiceText": current.record["correctChoiceText"] if revision == "r2"
        else json.loads(transition.read_text())["entry"]["correctChoiceText"]}
    canonical = {"qualificationId": "gas-shunin-hei", "listGroupId": "2022", "originalQuestionId": binding["reviewQuestionId"],
        "sourceAnswerComparison": {"sourceCorrectChoiceText": source.record.get("correctChoiceText"),
            "currentCorrectChoiceText": current.record["correctChoiceText"],
            "sourceAnswerResult": source.record.get("answer_result_text")},
        "officialEvidenceCandidates": [{**evidence[0], "localSourcePath": packet["officialQuestionPdf"]["path"],
            "localRenderedPagePaths": images, "localRenderedPageHashes": {p: driver.digest(ROOT / p) for p in images},
            "verifiedTranscription": transcript, "candidateChanges": changes}],
        "report": "公式正答3は最も不適切な組合せの選択番号。00_sourceの正解/不正解表示と、true_falseで公開する各組合せの真偽が混在している。" +
            ("現在の23パッチは既に各肢真偽へ修正されている。" if revision == "r2" else
             "既存23の主担当訂正は、今回24へ責務を移すため、原bytesと全fieldを退避した上で当該1問だけ取り外した。現在の入力は未修正の元表示へ戻っている。23が持っていた訂正候補を正本とはせず、公式資料から独立に全肢を再計算する。最終の表示内容は変更前の確認済み内容と一致する予定だが、承認前に一致を保証しない。") +
            "元表示との差の妥当性を公式一次資料から独立に確認する。",
        "displayFormatRules": ["番号3を第3記述が正しいという意味へ転記しない。", "0℃・0.1MPaの理想気体は約22.7L/molである。冊子は22.4L/mol近似値を使うが、最も不適切なのは酸素36g・22.4Lであることを独立に確認する。", "要修正なしと判断する場合はno_changeを返し、承認のためだけにfixを作らない。"]}
    case = "operator-source-answer-comparison-" + binding["reviewQuestionId"]
    item = {"sourceQuestionId": key, "currentRecord": current.record, "sourceBinding": binding,
        "workItem": {"workId": "2022-kiso-q5-answer", "qualificationId": "gas-shunin-hei", "listGroupId": "2022",
            "questionId": binding["reviewQuestionId"], "originalQuestionId": binding["reviewQuestionId"], **binding,
            "caseIds": [case], "caseInputHashes": {case: driver.sha256_json(canonical)},
            "caseSnapshots": [{"id": case, "canonicalSnapshot": canonical}]},
        "evidence": evidence, "imagePaths": images, "verifiedTranscription": transcript, "candidateChanges": changes,
        "sourcePath": str(source.path.relative_to(ROOT)), "sourceSha256": driver.digest(source.path),
        "questionPdfPath": packet["officialQuestionPdf"]["path"], "questionPdfSha256": packet["officialQuestionPdf"]["sha256"]}
    driver.freeze(plan_path, {"schemaVersion": "gas-shunin-hei-official-correction-review-plan/v1", "year": 2022,
        "createdAt": created, "batchId": "zz-hei-direct-answer-2022-kiso-05-" + revision, "category": "correct_answer", "items": [item]})
task = driver.Driver(plan_path, work / "reviews", root=ROOT)
if sys.argv[1] == "save":
    result = task.save(allow_save=True)
    if revision == "r3":
        receipt = json.loads(transition.read_text())
        if result[0]["decision"] != "fix":
            rows = json.loads(patch23.read_text())
            if any(q["sourceRecordRef"] == binding["sourceRecordRef"] for q in rows):
                raise ValueError("復元先に予期しない同一問")
            rows.append(receipt["entry"])
            rows.sort(key=lambda q: int(q["sourceRecordRef"].rsplit("#", 1)[1]))
            patch23.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
            receipt["status"] = "restored_23_no_24_fix"
        else:
            receipt["status"] = "moved_to_verified_24"
        fresh = module.QuestionInventory(ROOT).projected_input("gas-shunin-hei", "2022", binding["sourceRecordRef"])
        if fresh.errors or fresh.record != receipt["beforeProjection"]:
            raise ValueError("移管後の表示内容が変更前と一致しない")
        receipt.update(result=result, afterProjectionUnchanged=True, completedAt=datetime.datetime.now(datetime.timezone.utc).isoformat())
        transition.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(result)
else:
    task.phase(sys.argv[1])
