"""既存の変更不要正答証明APIで、実三相レビューのcurrent値を24層に保存する。"""
import datetime
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / "output/gas-shunin-hei/direct_publication/20261027"
spec = importlib.util.spec_from_file_location("hei_certification_driver", ROOT / "docs/goals/gas-shunin-hei-production/notes/T007-note-text.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
driver = module.driver
driver.CATEGORY = "correct_answer"
from tools.question_bank.question_issue_reports import build_current_answer_certification_patch
from tools.question_review_console.inventory import source_answer_difference_approval

work = RUN / "answer_corrections/2022-kiso-05-r2"
transition_path = RUN / "answer_corrections/2022-kiso-05-layer-transition.json"
transition = json.loads(transition_path.read_text())
binding = transition["sourceIdentity"]
patch23 = ROOT / "output/gas-shunin-hei/questions_json/2022/23_correctChoiceText_fixed/question_2022_1_merged_correctChoiceText_fixed.json"
if transition["status"] == "detached_23_awaiting_verified_24":
    backup = ROOT / transition["backup23Path"]
    if driver.digest(backup) != transition["backup23Sha256"]:
        raise ValueError("退避済み23のhash不一致")
    rows = json.loads(patch23.read_text())
    original_rows = json.loads(backup.read_text())
    without_owned = [q for q in original_rows if q["sourceRecordRef"] != binding["sourceRecordRef"]]
    if rows != without_owned:
        raise ValueError("退避中に他問のpatchが変更された")
    patch23.write_bytes(backup.read_bytes())
    transition["status"] = "restored_23_using_existing_current_answer_certification"
    transition["restored23Sha256"] = driver.digest(patch23)
    transition["reasonForRestoration"] = "固定commitにbuild_current_answer_certification_patchが既にあることを確認。移管は不要。r3の未採用実応答は保存したまま使用しない。"
    transition_path.write_text(json.dumps(transition, ensure_ascii=False, indent=2) + "\n")
current = module.QuestionInventory(ROOT).projected_input("gas-shunin-hei", "2022", binding["sourceRecordRef"])
if current.errors or current.record != transition["beforeProjection"]:
    raise ValueError("23復元後の表示内容が退避前と一致しない")
task = driver.Driver(work / "plan.json", work / "reviews", root=ROOT)
if task.save(allow_save=True) != [{"sourceQuestionId": "2022:kiso:問5", "decision": "no_change"}]:
    raise ValueError("実三相レビューは変更不要で一致していない")
item = task.items["2022:kiso:問5"]
a, b, c = [task._reviews(phase)["2022:kiso:問5"] for phase in ("blind_a", "blind_b", "challenge")]
patch = build_current_answer_certification_patch(manifest=task.manifest, work_item=item["workItem"],
    current_record=item["currentRecord"], source_binding=item["sourceIdentity"].binding,
    blind_reviews=[a, b], challenge=c, config=task.config)
# 本証明は既存本文訂正より後のcurrent値へ束縛する。00_sourceの名前は変えない。
target = task.group / "24_questionIssueCorrections" / ("zzz_" + driver.correction_patch_filename(task.manifest, item["workItem"]))
if target.exists():
    existing = json.loads(target.read_text())
    patch["createdAt"] = existing["createdAt"]
driver.freeze(target, patch)
driver.verify_patch_against_record(target, current_record=item["currentRecord"],
    source_identity=item["sourceIdentity"], config_path=driver.DEFAULT_CONFIG_PATH)
fresh = module.QuestionInventory(ROOT).projected_input("gas-shunin-hei", "2022", binding["sourceRecordRef"])
if fresh.errors or fresh.record != current.record:
    raise ValueError("確認証明の適用で現在値が変わった")
approval = source_answer_difference_approval([target], repo_root=ROOT,
    source_question_key=binding["sourceQuestionKey"], review_question_id=binding["reviewQuestionId"],
    current_correct_choice_text=fresh.record["correctChoiceText"])
if not approval["approved"]:
    raise ValueError(approval)
receipt = {"status": "certified_current_values", "changedBusinessFields": [], "sourceIdentity": binding,
    "sourceAnswerDifferenceApproval": approval, "patchPath": str(target.relative_to(ROOT)),
    "patchSha256": driver.digest(target), "nativeReviews": [task._load_phase(p) for p in ("blind_a", "blind_b", "challenge")],
    "toolCommit": "57026e8da5ae6725535ad188d7f6953cda180cf5", "restored23Sha256": driver.digest(patch23),
    "orderingReason": "24本文注記の適用後に確認証明を重ね、正答・本文・選択肢の束縛を維持する。",
    "completedAt": datetime.datetime.now(datetime.timezone.utc).isoformat()}
driver.freeze(work / "current-answer-certification.json", receipt)
print("正答の現在値証明を保存。23復元済み、表示内容不変、source差分の正式確認", approval)
