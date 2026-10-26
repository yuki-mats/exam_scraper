#!/usr/bin/env python3
"""Run reviewed transport sequentially, reopening fresh inputs before formal save."""
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from tools.question_bank.question_issue_reports import DEFAULT_CONFIG_PATH, load_config, routed_workflow_contracts

DRIVER = ROOT / "docs/goals/gas-shunin-hei-pdf-patches/notes/T005-review-driver.py"
APPROVED_DRIVER_SHA256 = "4c794d71fc299116caba366badcaab30e2c1998cedd5c5f89b4f614e1eabcf2e"
BASE = ROOT / "output/gas-shunin-hei/pdf_patch_review"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contracts():
    c, text = routed_workflow_contracts(load_config(DEFAULT_CONFIG_PATH), "question_content")
    return {"contracts": c, "textSha256": hashlib.sha256(text.encode()).hexdigest(), "configSha256": sha(DEFAULT_CONFIG_PATH)}


def run():
    expected_contracts = contracts()
    results = []
    for plan in sorted((BASE / "plans-v4").glob("*.json")):
        if plan.name.startswith("000-"):
            continue  # Separately audited pilot; preserve its native evidence.
        work = BASE / "runs-v4" / plan.stem
        work.mkdir(parents=True, exist_ok=True)
        if sha(DRIVER) != APPROVED_DRIVER_SHA256 or contracts() != expected_contracts:
            raise ValueError("approved driver or contracts changed; fresh revision required")
        started = datetime.now(timezone.utc).isoformat()
        print(f"{plan.stem}: review starting", flush=True)
        common = [sys.executable, str(DRIVER), "--plan", str(plan), "--work-dir", str(work)]
        # Independent read-only A/B write only separate phase evidence files.
        # Shared frozen-plan uses identical bytes and atomic exclusive creation.
        phase_codes = {}
        with (work / "blind_a-execution.log").open("a") as log_a, (work / "blind_b-execution.log").open("a") as log_b:
            a = subprocess.Popen(common + ["--stage", "blind_a"], cwd=ROOT, stdout=log_a, stderr=subprocess.STDOUT)
            b = subprocess.Popen(common + ["--stage", "blind_b"], cwd=ROOT, stdout=log_b, stderr=subprocess.STDOUT)
            phase_codes["blind_a"] = a.wait()
            phase_codes["blind_b"] = b.wait()
        code = max(phase_codes.values())
        with (work / "execution.log").open("a") as log:
            if code == 0:
                phase_codes["challenge"] = subprocess.run(common + ["--stage", "challenge"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode
                code = phase_codes["challenge"]
            if code == 0:
                # Fresh process reopens policy and inputs immediately before save.
                if sha(DRIVER) != APPROVED_DRIVER_SHA256 or contracts() != expected_contracts:
                    raise ValueError("contracts changed after model review; formal save refused")
                code = subprocess.run(common + ["--stage", "save", "--save"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode
        item = {"plan": str(plan.relative_to(ROOT)), "workDirectory": str(work.relative_to(ROOT)), "startedAt": started, "finishedAt": datetime.now(timezone.utc).isoformat(), "exitCode": code, "phaseExitCodes": phase_codes}
        if code == 0:
            item["results"] = json.loads((work / "save-result.json").read_text())["results"]
        results.append(item)
        (BASE / "batch-execution-results-v4.json").write_text(json.dumps({"driverSha256": APPROVED_DRIVER_SHA256, "policy": expected_contracts, "batches": results}, ensure_ascii=False, indent=2) + "\n")
        counts = {}
        for r in item.get("results", []):
            counts[r["decision"]] = counts.get(r["decision"], 0) + 1
        print(f"{plan.stem}: exit={code} {counts}", flush=True)
    return int(any(x["exitCode"] for x in results))


if __name__ == "__main__":
    raise SystemExit(run())
