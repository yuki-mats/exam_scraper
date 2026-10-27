"""直接整備の実保存・実検査を、復旧可能な日付付きcheckpointへ記録する。"""
import datetime
import hashlib
import json
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
        p.parent.name == "24_questionIssueCorrections" and ("direct-note-text" in p.name or "direct-answer" in p.name)):
        formal.append(snapshot(p))
format_complete = all("_format/" in str(selected.get(f"2017:law:問{n}", {}).get("lawAuditPath", "")) for n in range(1, 9))
report = {"schemaVersion": "hei-direct-maintenance-checkpoint/v1",
    "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(), "status": "in_progress",
    "fullOutcomeComplete": False, "productionWritten": False, "systemCodeModified": False,
    "maintenanceServerStarted": False, "sourceQuestionCount": 522, "newDirectSavedQuestionCount": len(selected),
    "remainingDirectReviewQuestionCount": 522 - len(selected), "savedQuestions": selected,
    "latestChecks": {"path": str(check_path.relative_to(ROOT)), "sha256": hashlib.sha256(check_path.read_bytes()).hexdigest(), "summary": check},
    "restorableFormalPatches": formal,
    "lawAuditSidecars": [snapshot(p) for p in sorted((ROOT / "output/gas-shunin-hei/review/law_revision_audit").glob("*.jsonl"))],
    "primaryDrafts": [snapshot(p) for p in sorted((RUN / "primary").glob("*.json"))],
    "commonNoteTextCorrection": {"savedYears": list(range(2017, 2026)), "questions": 135, "meaningUnchanged": True,
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

基礎の共通注135問は意味を保って文章化し、既存のBlind A/BとChallengeを経て24層へ保存した。2022年度基礎問5の正答は同じ三相確認で変更不要と確認し、既存の現在値確認証明APIから24へ保存した。一時的な23移管は取りやめ、退避時のbytesへ完全復元した。三相の実応答は保持し、判定・採点・出力fieldは書き換えていない。

2017年度の法令1〜8の法令番号・公式参照・別表内位置の表記補完: {"実独立再確認・保存済み" if format_complete else "実独立再確認中"}。保存済み法令問題にも同じ基準を適用する。

対象{len(selected)}問の正答整合性・必須項目・部分patch coverageが成功し、00_source 27ファイルと停止中の改修10ファイルの不変を確認した。対象検査は現在projectionを私有検査領域へ出したものであり、全年度の公開前検証・正式評価・本番反映は未実施。2017年度の標準mergeは成功したが、同年度の未整備問題に設問意図がなく標準convertが停止した。条件を緩めず、残問整備後に再生成する。

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
