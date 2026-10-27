#!/usr/bin/env python3
"""Fresh single-question review for held transport items; never formal-save here."""
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from tools.question_bank.question_issue_reports import sha256_json

BASE = ROOT / "output/gas-shunin-hei/pdf_patch_review"
DRIVER = ROOT / "docs/goals/gas-shunin-hei-pdf-patches/notes/T005-review-driver.py"


def main():
    assert hashlib.sha256(DRIVER.read_bytes()).hexdigest() == "4c794d71fc299116caba366badcaab30e2c1998cedd5c5f89b4f614e1eabcf2e"
    wanted = {}
    for path in sorted((BASE / "runs-v4").glob("[0-9]*-*/save-result.json")):
        data = json.loads(path.read_text())
        plan = json.loads((path.parent / "frozen-plan.json").read_text())["plan"]
        by_id = {x["sourceQuestionId"]: x for x in plan["items"]}
        for result in data["results"]:
            if result["decision"] == "hold":
                wanted[result["sourceQuestionId"]] = (plan, by_id[result["sourceQuestionId"]], path)
    outcomes = []
    for key, (old_plan, item, origin) in sorted(wanted.items()):
        slug = key.replace(":", "-").replace("問", "q")
        plan = {k: copy.deepcopy(v) for k, v in old_plan.items() if k != "items"}
        plan["batchId"] = "hei-pdf-recovery-" + slug
        item = copy.deepcopy(item)
        for snapshot in item["workItem"]["caseSnapshots"]:
            content = snapshot["canonicalSnapshot"]
            content["displayFormatRules"] = [
                "公式表の左欄・右欄を連結した表示では、候補に保持された半角空白と改行を保持する。",
                "候補が公式画像に忠実と判断できた場合は完全値を複写し、意味を変えない追加の空白正規化や文体変更はしない。候補の意味が誤る場合は採用せず理由を記す。",
                "科目共通注は公式冊子が適用する科目の範囲を確認する。別の科目に示された注を当該問題へ流用しない。",
            ]
            item["workItem"]["caseInputHashes"][snapshot["id"]] = sha256_json(content)
        plan["items"] = [item]
        plan_path = BASE / "recovery-plans-v4" / (slug + ".json")
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
        if plan_path.exists():
            assert plan_path.read_text() == text
        else:
            plan_path.write_text(text)
        work = BASE / "runs-v4" / ("recovery-" + slug)
        work.mkdir(parents=True, exist_ok=True)
        print(key + ": fresh single-question review", flush=True)
        with (work / "execution.log").open("a") as log:
            code = subprocess.run([sys.executable, str(DRIVER), "--plan", str(plan_path), "--work-dir", str(work), "--stage", "all"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode
        outcome = {"sourceQuestionId": key, "originHeldResult": str(origin.relative_to(ROOT)), "plan": str(plan_path.relative_to(ROOT)), "workDirectory": str(work.relative_to(ROOT)), "exitCode": code}
        if code == 0:
            outcome["results"] = json.loads((work / "stage-result.json").read_text())["results"]
        outcomes.append(outcome)
        (BASE / "recovery-stage-results-v4.json").write_text(json.dumps(outcomes, ensure_ascii=False, indent=2) + "\n")
        print(key + ": staged exit=" + str(code), flush=True)


if __name__ == "__main__":
    main()
