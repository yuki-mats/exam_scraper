"""共通注の表組を自然な文へつなぐ候補を、既存24レビュー経路に渡す。"""
import copy
import datetime
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / "output/gas-shunin-hei/direct_publication/20261027"
TOOLS = RUN / "tool_snapshot"
sys.path.insert(0, str(TOOLS))
# 再利用する既存driverの全project importsを、先に固定済み実装から読ませる。
for name in ("scripts.common.question_identity", "scripts.merge.question_issue_corrections",
             "tools.question_review_console.projection", "tools.question_review_console.codex_app_server",
             "tools.question_bank.question_issue_reports"):
    loaded = importlib.import_module(name)
    if not Path(loaded.__file__).is_relative_to(TOOLS):
        raise ValueError("固定外の実装を読み込もうとした")
from tools.question_review_console.inventory import QuestionInventory
from scripts.common.question_identity import load_source_record_inventory

driver_path = ROOT / "docs/goals/gas-shunin-hei-pdf-patches/notes/T007-review-driver.py"
spec = importlib.util.spec_from_file_location("hei_existing_note_review", driver_path)
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)
sys.path.insert(0, str(TOOLS))


def prepare(year):
    revision = sys.argv[4] if len(sys.argv) > 4 else "r2"
    work = RUN / "note_text" / (year + "-" + revision)
    plan_path = work / "plan.json"
    if plan_path.exists():
        return plan_path
    inv = QuestionInventory(ROOT)
    packets = json.loads((RUN / "records.json").read_text())
    created = datetime.datetime.now(datetime.timezone.utc).isoformat()
    items = []
    for source in load_source_record_inventory(ROOT / f"output/gas-shunin-hei/questions_json/{year}/00_source",
                                              qualification="gas-shunin-hei", list_group_id=year):
        key = source.record["source_question_id"]
        if not key.startswith(year + ":kiso:"):
            continue
        binding = source.identity.binding
        current = inv.projected_input("gas-shunin-hei", year, binding.source_record_ref)
        if current.errors:
            raise ValueError(current.errors)
        body = current.record["questionBodyText"]
        old = body.split("\n\n", 1)[0]
        if not old.startswith("【科目共通注】\n注 問題文中の「圧力」"):
            raise ValueError(f"未確認の注形式: {key}")
        new = "【科目共通注】\n注 問題文中の「圧力」は、指定がない限り「絶対圧力」のことである。"
        next_body = new + "\n\n" + body.split("\n\n", 1)[1]
        pages = sorted(set(packets[key]["questionPages"]) | {int(sys.argv[3])})
        images = [f"output/gas-shunin-hei/pdf_review/{year}/mark_sheet_question/page-{page:02}.png" for page in pages]
        pdf_path = packets[key]["officialQuestionPdf"]["path"]
        if str(pdf_path).startswith(str(ROOT)):
            pdf_path = str(Path(pdf_path).relative_to(ROOT))
        evidence = {"sourceClass": "official", "title": f"{year}年度ガス主任技術者試験 丙種 公式問題冊子",
                    "locator": f"{pdf_path} / PDF {','.join(map(str,pages))}ページ / {key}",
                    "verifiedAt": created, "contentHash": hashlib.sha256((ROOT / pdf_path).read_bytes()).hexdigest()}
        transcription = "【公式表組の意味を保って主担当が文章化した表示候補】\n" + next_body + "\n【全選択肢・変更なし】\n" + json.dumps(current.record["choiceTextList"], ensure_ascii=False)
        candidate = {"questionBodyText": next_body}
        canonical = {"qualificationId": "gas-shunin-hei", "listGroupId": year,
            "originalQuestionId": binding.review_question_id,
            "officialEvidenceCandidates": [{**evidence, "localSourcePath": pdf_path,
                "localRenderedPagePaths": images, "localRenderedPageHashes": {p: driver.digest(ROOT / p) for p in images},
                "verifiedTranscription": transcription, "candidateChanges": candidate,
                "transcriptionRole": "表の左右欄を自然な文へ接続した表示候補。助詞は原本の逐語引用ではない。実画像で用語・例外の意味を確認する。"}],
            "displayFormatRules": ["科目共通注の表組を文章表示にするため、両欄の関係を示す助詞と読点を補う。",
                "変更候補へ現在値と同値のfieldを再掲しない。choiceTextListは全要素同一であり、今回の変更fieldは本文だけである。内容相違を発見した場合はfindingsへ記録する。",
                "圧力の意味、指定がある場合の例外、単位と数値、設問及び全選択肢は変えない。",
                "表示上の助詞を原PDFに印字された文字とは主張しない。候補の意味が公式表と異なる場合は保留する。"]}
        case_id = "operator-note-text-" + binding.review_question_id
        work_item = {"workId": year + "-kiso-q" + key.rsplit("問", 1)[1], "qualificationId": "gas-shunin-hei",
            "listGroupId": year, "questionId": binding.review_question_id, "originalQuestionId": binding.review_question_id,
            **binding.as_mapping(), "caseIds": [case_id], "caseInputHashes": {case_id: driver.sha256_json(canonical)},
            "caseSnapshots": [{"id": case_id, "canonicalSnapshot": canonical}]}
        items.append({"sourceQuestionId": key, "currentRecord": current.record, "sourceBinding": binding.as_mapping(),
            "workItem": work_item, "evidence": [evidence], "imagePaths": images, "verifiedTranscription": transcription,
            "candidateChanges": candidate, "sourcePath": str(source.path.relative_to(ROOT)), "sourceSha256": driver.digest(source.path),
            "questionPdfPath": pdf_path, "questionPdfSha256": evidence["contentHash"]})
    driver.freeze(plan_path, {"schemaVersion": "gas-shunin-hei-official-correction-review-plan/v1", "year": int(year),
        "createdAt": created, "batchId": "zz-hei-direct-note-text-" + year + "-" + revision, "category": "question_content", "items": items})
    driver.freeze(work / "execution.json", {"driverPath": str(driver_path.relative_to(ROOT)), "driverSha256": driver.digest(driver_path),
        "frozenToolCommit": "57026e8da5ae6725535ad188d7f6953cda180cf5", "systemCodeModified": False})
    return plan_path


if __name__ == "__main__":
    year = sys.argv[1]
    phase = sys.argv[2]
    plan = prepare(year)
    task = driver.Driver(plan, plan.parent / "reviews", root=ROOT)
    if phase == "save":
        if (plan.parent / "formal-ordering-moves.json").exists():
            raise ValueError("このrunは保存後のファイル順序調整済み。元のsaveは再実行しない。")
        result = task.save(allow_save=True)
        print("formal save", {decision: sum(q["decision"] == decision for q in result) for decision in {q["decision"] for q in result}})
    else:
        task.phase(phase)
