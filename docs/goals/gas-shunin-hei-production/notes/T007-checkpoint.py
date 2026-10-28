"""直接整備の実保存・実検査を、復旧可能な日付付きcheckpointへ記録する。"""
import datetime
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / "output/gas-shunin-hei/direct_publication/20261027"
REPORT = ROOT / "document/temporary/20261027_gas_shunin_hei_direct_checkpoint.json"


def snapshot(path):
    return {"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "payload": ([json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            if path.suffix == ".jsonl" else json.loads(path.read_text()))}


selected = {}
for receipt in sorted((RUN / "saved").glob("*.json"), key=lambda p: p.stat().st_mtime_ns):
    for q in json.loads(receipt.read_text())["candidates"]:
        selected[q["sourceQuestionId"]] = {**q, "saveReceipt": str(receipt.relative_to(ROOT))}
check_path = sorted((RUN / "checks").glob("*/result.json"))[-1]
check = json.loads(check_path.read_text())
if not check["pass"] or check["selectedCount"] != len(selected):
    raise ValueError("現在の保存件数を対象とする成功検査が必要")
transition_path = RUN / "answer_corrections/2022-kiso-05-layer-transition.json"
if transition_path.exists() and json.loads(transition_path.read_text())["status"] == "detached_23_awaiting_verified_24":
    raise ValueError("23移管の一時状態を完成checkpointにしない")
formal = []
for p in sorted((ROOT / "output/gas-shunin-hei/questions_json").glob("*/*/*.json")):
    if p.parent.name in {"10_questionType_fixed", "15_correctChoiceText_added", "15_correctChoiceText_fixed",
        "18_law_context_prepared", "21_explanationText_added", "22_questionSetId_linked", "23_correctChoiceText_fixed"} or (
        p.parent.name == "24_questionIssueCorrections" and any(marker in p.name for marker in
            ("direct-note-text", "direct-answer", "direct-scope", "direct-public-text"))):
        formal.append(snapshot(p))
sys.path.insert(0, str(RUN / "tool_snapshot"))
from tools.question_review_console.inventory import QuestionInventory
inv = QuestionInventory(ROOT)
law_display_checks = []
for n in range(1, 9):
    item = selected.get(f"2017:law:問{n}")
    projection = inv.projected_input("gas-shunin-hei", "2017", item["sourceIdentity"]["sourceRecordRef"]) if item else None
    record = projection.record if projection is not None and not projection.errors else {}
    law_display_checks.append(bool(record.get("explanationReferences")) and all(
        ("昭和" in text or "平成" in text or "令和" in text) and "号" in text
        for text in record.get("explanationText") or []))
format_complete = all(law_display_checks)
note_saved_years, note_holds, note_saved_count = [], [], 0
for year in range(2017, 2026):
    result_path = RUN / f"note_text/{year}-r2/reviews/save-result.json"
    result = json.loads(result_path.read_text())
    fixed = [q for q in result["results"] if q["decision"] == "fix"]
    note_saved_count += len(fixed)
    if len(fixed) == 15:
        note_saved_years.append(year)
    note_holds.extend(q["sourceQuestionId"] for q in result["results"] if q["decision"] == "hold")
intent_receipts = [snapshot(p) for p in sorted((RUN / "intent_reviews").glob("*/saved.json"))]
intent_missing = []
for key, item in selected.items():
    current = inv.projected_input("gas-shunin-hei", key.split(":")[0], item["sourceIdentity"]["sourceRecordRef"])
    if current.errors:
        raise ValueError(current.errors)
    if current.record.get("questionType") in {"flash_card", "group_choice"} and not current.record.get("questionIntent"):
        intent_missing.append(key)
report = {"schemaVersion": "hei-direct-maintenance-checkpoint/v1",
    "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(), "status": "in_progress",
    "fullOutcomeComplete": False, "productionWritten": False, "systemCodeModified": False,
    "maintenanceServerStarted": False, "sourceQuestionCount": 522, "newDirectSavedQuestionCount": len(selected),
    "remainingDirectReviewQuestionCount": 522 - len(selected), "savedQuestions": selected,
    "latestChecks": {"path": str(check_path.relative_to(ROOT)), "sha256": hashlib.sha256(check_path.read_bytes()).hexdigest(), "summary": check},
    "concurrentWorkspaceObservation": snapshot(RUN / "workspace-code-observation.json"),
    "restorableFormalPatches": formal,
    "lawAuditSidecars": [snapshot(p) for p in sorted((ROOT / "output/gas-shunin-hei/review/law_revision_audit").glob("*.jsonl"))],
    "primaryDrafts": [snapshot(p) for p in sorted((RUN / "primary").glob("*.json"))],
    "scopeCorrectionReceipts": [snapshot(p) for p in sorted((RUN / "scope_corrections").glob("*/reviews/save-result.json"))],
    "scopeCorrectionPlans": [snapshot(p) for p in sorted((RUN / "scope_corrections").glob("*/plan.json"))],
    "publicEditingNoteReadback": snapshot(RUN / "public-editing-notes-readback.json")
        if (RUN / "public-editing-notes-readback.json").exists() else None,
    "publicEditingNoteScopedVerification": snapshot(RUN / "public-editing-notes-scoped-verification.json")
        if (RUN / "public-editing-notes-scoped-verification.json").exists() else None,
    "latestConvertDisplayDiagnostic": snapshot(sorted((RUN / "convert_display").glob("*/result.json"))[-1])
        if list((RUN / "convert_display").glob("*/result.json")) else None,
    "holdResolutions": [snapshot(p) for p in sorted((RUN / "hold_resolutions").glob("*.json"))],
    "openContentHolds": [snapshot(p) for p in sorted((RUN / "holds").glob("*.json"))],
    "intentCompletion": {"savedReceipts": intent_receipts, "remainingMissing": intent_missing},
    "commonNoteTextCorrection": {"savedYears": note_saved_years, "questions": note_saved_count, "heldQuestions": note_holds, "meaningUnchanged": True,
        "reportCorrection": "以前の135問保存済みという集計は誤り。実save-resultは105 fix / 30 hold。共通注自体は先行24層にあるが、2021・2023年度の文章化は未保存。",
        "orderingReceipt": str((RUN / "note_text/2018-r2/formal-ordering-moves.json").relative_to(ROOT))},
    "sourceAnswerCertification": snapshot(RUN / "answer_corrections/2022-kiso-05-r2/current-answer-certification.json"),
    "temporary23Transition": snapshot(transition_path),
    "knownRework": [] if format_complete else ["2017法令1〜8の法令番号・公式参照・別表内位置の表記補完を独立再確認中。未承認候補は正式保存しない。"],
    "pending": ["残問の直接整備・紐付け・全肢照合", "実成功receiptに基づく工程版記録と標準独立評価",
        "全年度merge/convert/quality-gate/upload dry-run", "画像category公式年度マニフェストを含む本番反映",
        "全対象本番読み戻しとGit保存"]}
REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
by_section = {}
for key in selected:
    section = key.split(":")[1]
    by_section[section] = by_section.get(section, 0) + 1
REPORT.with_suffix(".md").write_text(f"""# 丙種の直接整備・途中記録

システム改修と自動整備runは停止したまま、主担当が公式PDFを目視し、正答・解説・分類を直接作成した。共有codeは変更していない。実行にはcommit `57026e8da5ae6725535ad188d7f6953cda180cf5` の固定実装を使った。

{len(selected)}問を実画像付きの別read-only sessionの全肢確認後に18・21・22・23層へ保存した。内訳は{json.dumps(by_section, ensure_ascii=False)}。法令問題はe-Gov公式APIから出題時と現行の本文・改正ID・hashを取得し、既存の独立法令監査・昇格・metadata付与APIを経て保存した。実primary候補、native session、sidecarをJSONへ結合している。

基礎の共通注の文章化は{note_saved_count}問を、既存のBlind A/BとChallengeを経て24層へ保存した。2021・2023年度の計{len(note_holds)}問はChallengeが保留とし、文章化patchは未保存である。先行24層の共通注の内容は保持している。以前の「135問保存済み」は実save-resultのfix/holdを区別しなかった誤集計であり、ここで訂正する。2022年度基礎問5の正答は同じ三相確認で変更不要と確認し、既存の現在値確認証明APIから24へ保存した。一時的な23移管は取りやめ、退避時のbytesへ完全復元した。三相の実応答は保持し、判定・採点・出力fieldは書き換えていない。

設問意図の未設定61問は、主担当が問題と全選択肢から一問ずつ直接判定し、公式画像付きの実read-only確認後に15層だけへ保存した。これは設問意図の限定確認であり、正答・解説の再評価や全工程評価の代用ではない。保存時のprojection読み戻しで他のfieldが変わらないことを確認した。現時点の選択式・組合せ式の未設定は{len(intent_missing)}問。各問の直接判断・実監査・保存receiptはJSONに収録する。

2017年度の法令1〜8の法令番号・公式参照・別表内位置の表記補完: {"実独立再確認・保存済み" if format_complete else "実独立再確認中"}。保存済み法令問題にも同じ基準を適用する。

対象{len(selected)}問の正答整合性・必須項目・部分patch coverageが成功し、00_source 27ファイルの不変を確認した。実行した固定snapshotの{check.get('snapshotFilesChecked', '未集計')}ファイルはGit objectと一致した。共有checkoutのqualification_runs.pyには並行作業の変更を検出したため、commit・差分・hashを観測記録へ残した。丙種の実行には取り込んでいない。対象検査は現在projectionを私有検査領域へ出したものであり、全年度の公開前検証・正式評価・本番反映は未実施。2017年度の標準merge・convertは成功した。その後の標準検査では、同年度の未整備7問に由来するquestionSetIdの空値35件で停止した。quality-gateとupload dry-runは成功していない。条件を緩めず、残問整備後に再生成する。

添付JSONは正式patchの復旧用実値・固定候補・検査証跡を含む途中checkpointであり、仕様又は公開可能判定の代用ではない。残る{522-len(selected)}問と、正式評価、本番反映、全件読み戻しまで継続する。
""")
board = ROOT / "docs/goals/gas-shunin-hei-production/state.yaml"
import re
text = board.read_text()
text = re.sub(r"newDirectSavedQuestions: \d+", f"newDirectSavedQuestions: {len(selected)}", text)
text = re.sub(r"primaryAuthoredQuestions: \d+", f"primaryAuthoredQuestions: {len(selected)}", text)
text = re.sub(r"    questions: \d+\n    receipt: output/gas-shunin-hei/direct_publication/20261027/checks/[^\n]+",
    f"    questions: {len(selected)}\n    receipt: {check_path.relative_to(ROOT)}", text)
board.write_text(text)
print("checkpoint", len(selected), "questions", len(formal), "formal patch files", by_section)
