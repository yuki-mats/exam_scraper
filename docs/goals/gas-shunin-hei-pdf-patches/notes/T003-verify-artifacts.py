#!/usr/bin/env python3
"""Scoped normal merge/checks, with an unpatched baseline for existing schema gaps."""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from scripts.common.question_identity import load_source_record_inventory
from scripts.merge.record_projection import project_merge_record
from tools.question_bank.question_issue_reports import DEFAULT_CONFIG_PATH, verify_patch_against_record

BASE = ROOT / "output/gas-shunin-hei/questions_json"
REPORTS = ROOT / "output/gas-shunin-hei/pdf_patch_review/verification"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args, name, commands):
    path = REPORTS / (name + ".log")
    with path.open("w") as log:
        code = subprocess.run([sys.executable, *map(str, args)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode
    item = {"name": name, "command": [sys.executable, *map(str, args)], "exitCode": code, "finishedAt": datetime.now(timezone.utc).isoformat(), "logPath": str(path.relative_to(ROOT)), "logSha256": digest(path)}
    commands.append(item)
    print(name, "exit=" + str(code), flush=True)
    return code


def main():
    paths = sorted(BASE.glob("*/24_questionIssueCorrections/*.json"))
    assert len(paths) == 289, "Wait for all confirmed patches before final verification"
    REPORTS.mkdir(parents=True, exist_ok=True)
    commands = []
    for year in range(2017, 2026):
        assert run(["scripts/merge/00_merge_all.py", year, "--base-dir", BASE], f"merge-{year}", commands) == 0
    alignment = run(["scripts/check/check_choice_text_alignment.py", "--base-dir", BASE, "--stage", "merged"], "choice-text-alignment", commands)
    required = run(["scripts/check/check_required_fields.py", "--base-dir", BASE, "--stage", "merged"], "required-fields", commands)
    # This historical instruction has no inputs. Preserve its actual usage result
    # and use the current CLI's required explicit inputs for meaningful coverage.
    run(["scripts/check/check_correct_choice_patch_coverage.py"], "coverage-cli-arguments", commands)
    coverage_codes = []
    patch_count = 0
    for year in range(2017, 2026):
        inventory = load_source_record_inventory(BASE / str(year) / "00_source", qualification="gas-shunin-hei", list_group_id=str(year))
        entries = {x.identity.binding.review_question_id: x for x in inventory}
        merged = {}
        for path in (BASE / str(year) / "30_merged_2").glob("*.json"):
            for q in json.loads(path.read_text())["question_bodies"]:
                assert q["source_question_id"] not in merged
                merged[q["source_question_id"]] = q
        for patch_path in BASE.glob(f"{year}/24_questionIssueCorrections/*.json"):
            patch = json.loads(patch_path.read_text())
            assert len(patch["entries"]) == 1
            entry = entries[patch["entries"][0]["original_question_id"]]
            staging = REPORTS / "patch-checker-inputs" / str(year) / patch_path.name
            staging.parent.mkdir(parents=True, exist_ok=True)
            if staging.exists():
                assert staging.read_bytes() == patch_path.read_bytes()
            else:
                staging.write_bytes(patch_path.read_bytes())
            verify_patch_against_record(staging, current_record=project_merge_record(entry.record).merged2, source_identity=entry.identity, config_path=DEFAULT_CONFIG_PATH)
            patch_count += 1
        by_file = {}
        for entry in inventory:
            by_file.setdefault(entry.path, []).append(entry)
        for source, rows in by_file.items():
            target = REPORTS / "projected-answer-values" / str(year) / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            values = []
            for entry in rows:
                key = entry.record["source_question_id"]
                current = merged[key]
                assert current["correctChoiceText"] == entry.record["correctChoiceText"]
                values.append({"original_question_id": entry.identity.binding.review_question_id, **entry.identity.binding.as_mapping(), "question_url": current["question_url"], "correctChoiceText": current["correctChoiceText"]})
            target.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n")
            coverage_codes.append(run(["scripts/check/check_correct_choice_patch_coverage.py", "--source", source, "--patch", target, "--require-full"], f"coverage-{year}-{source.stem}", commands))
    # Identify pre-existing required-field failures using the same normal merge,
    # source bytes and checker against a disposable, patch-free input copy.
    with tempfile.TemporaryDirectory(prefix="hei-unpatched-verification-") as temp:
        unpatched = Path(temp) / "gas-shunin-hei/questions_json"
        for year in range(2017, 2026):
            dst = unpatched / str(year) / "00_source"
            shutil.copytree(BASE / str(year) / "00_source", dst)
            assert run(["scripts/merge/00_merge_all.py", year, "--base-dir", unpatched], f"unpatched-baseline-merge-{year}", commands) == 0
        baseline_required = run(["scripts/check/check_required_fields.py", "--base-dir", unpatched, "--stage", "merged"], "unpatched-baseline-required-fields", commands)
    outcome = {"generatedAt": datetime.now(timezone.utc).isoformat(), "codeCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "commands": commands, "patchChecker": {"checked": patch_count, "status": "passed"}, "alignmentExitCode": alignment, "coveragePairs": len(coverage_codes), "coverageAllPassed": not any(coverage_codes), "requiredFieldsExitCode": required, "unpatchedBaselineRequiredExitCode": baseline_required, "fullRegression": "未実行"}
    (REPORTS / "commands.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2) + "\n")
    assert alignment == 0 and not any(coverage_codes) and patch_count == 289
    # Do not re-label a failed mandatory check as success.
    return int(bool(required))


if __name__ == "__main__":
    raise SystemExit(main())
