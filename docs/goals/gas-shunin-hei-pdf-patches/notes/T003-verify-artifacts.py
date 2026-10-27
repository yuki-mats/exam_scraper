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
from scripts.common.requirements import load_requirements, get_stage_rules, validate_records
from scripts.common.question_answer_contract import choice_text_content_alignment_issue
from scripts.check.check_required_fields import fill_in_blank_errors
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
    # The metadata materializer already ran the normal merge on the final inputs.
    # Reuse that successful generation receipt; the checks below read real files.
    metadata_root = ROOT / "output/gas-shunin-hei/pdf_patch_review/metadata"
    adopted = digest(ROOT / "output/gas-shunin-hei/pdf_patch_review/metadata-adoption.json")
    completed = [p for p in metadata_root.glob("*/metadata-result.json")
                 if json.loads(p.read_text()).get("status") == "applied_and_verified"
                 and json.loads(p.read_text())["adoption"]["sha256"] == adopted]
    assert len(completed) == 1
    metadata = json.loads(completed[0].read_text())
    for row in metadata["patches"]:
        formal = ROOT / row["formalPatch"]["path"]
        assert digest(formal) == row["formalPatch"]["sha256"]
    merge_commands = [x for x in metadata["commands"] if len(x["command"]) > 2 and x["command"][1] == "scripts/merge/00_merge_all.py"]
    assert len(merge_commands) == 9 and all(x["exitCode"] == 0 for x in merge_commands)
    for item in merge_commands:
        log = ROOT / item["log"]["path"]
        assert digest(log) == item["log"]["sha256"]
        commands.append({"name": "merge-" + item["command"][2], "command": item["command"], "exitCode": item["exitCode"], "logPath": item["log"]["path"], "logSha256": item["log"]["sha256"], "evidenceRecordedAt": metadata["finishedAt"], "reusedEvidence": str(completed[0].relative_to(ROOT))})
    alignment = run(["scripts/check/check_choice_text_alignment.py", "--base-dir", BASE, "--stage", "merged"], "choice-text-alignment", commands)
    required = run(["scripts/check/check_required_fields.py", "--base-dir", BASE, "--stage", "merged"], "required-fields", commands)
    # This historical instruction has no inputs. Preserve its actual usage result
    # and use the current CLI's required explicit inputs for meaningful coverage.
    run(["scripts/check/check_correct_choice_patch_coverage.py"], "coverage-cli-arguments", commands)
    coverage_codes = []
    patch_count = 0
    before_issues = []
    after_issues = []
    rules = get_stage_rules(load_requirements(), stage="merged", record_array="question_bodies", qualification="gas-shunin-hei")
    def issues(record, source_id, stage):
        marker = Path("identity-issue-comparison")
        found = validate_records(records=[record], rules=rules, source_path=marker)
        found += fill_in_blank_errors([record], marker)
        alignment_issue = choice_text_content_alignment_issue(record)
        if alignment_issue:
            found.append(alignment_issue)
        return [{"sourceQuestionId": source_id, "stage": stage, "issue": message} for message in found]
    for year in range(2017, 2026):
        inventory = load_source_record_inventory(BASE / str(year) / "00_source", qualification="gas-shunin-hei", list_group_id=str(year))
        entries = {x.identity.binding.review_question_id: x for x in inventory}
        merged = {}
        for path in (BASE / str(year) / "30_merged_2").glob("*.json"):
            for q in json.loads(path.read_text())["question_bodies"]:
                assert q["source_question_id"] not in merged
                merged[q["source_question_id"]] = q
        for stage, attribute in (("20_merged_1", "merged1"), ("30_merged_2", "merged2")):
            current_stage = {}
            for path in (BASE / str(year) / stage).glob("*.json"):
                for q in json.loads(path.read_text())["question_bodies"]:
                    assert q["source_question_id"] not in current_stage
                    current_stage[q["source_question_id"]] = q
            for entry in inventory:
                key = entry.record["source_question_id"]
                baseline_record = getattr(project_merge_record(entry.record), attribute)
                before_issues.extend(issues(baseline_record, key, stage))
                after_issues.extend(issues(current_stage[key], key, stage))
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
    def issue_key(x):
        return json.dumps(x, ensure_ascii=False, sort_keys=True)
    old = {issue_key(x) for x in before_issues}
    new = {issue_key(x) for x in after_issues}
    comparison = {"beforeIssueCount": len(before_issues), "afterIssueCount": len(after_issues), "newIssues": [json.loads(x) for x in sorted(new - old)], "resolvedIssues": [json.loads(x) for x in sorted(old - new)], "before": before_issues, "after": after_issues}
    comparison_path = REPORTS / "required-issues-before-after.json"
    comparison_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2) + "\n")
    outcome = {"generatedAt": datetime.now(timezone.utc).isoformat(), "codeCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "commands": commands, "patchChecker": {"checked": patch_count, "status": "passed"}, "alignmentExitCode": alignment, "coveragePairs": len(coverage_codes), "coverageAllPassed": not any(coverage_codes), "requiredFieldsExitCode": required, "unpatchedBaselineRequiredExitCode": baseline_required, "requiredIssuesComparison": {"path": str(comparison_path.relative_to(ROOT)), "sha256": digest(comparison_path), "beforeIssueCount": len(before_issues), "afterIssueCount": len(after_issues), "newIssueCount": len(new - old), "resolvedIssueCount": len(old - new)}, "fullRegression": "未実行"}
    (REPORTS / "commands.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2) + "\n")
    assert alignment == 0 and not any(coverage_codes) and patch_count == 289
    assert not new - old, "Correction introduced required-field issues"
    # Do not re-label a failed mandatory check as success.
    return int(bool(required))


if __name__ == "__main__":
    raise SystemExit(main())
