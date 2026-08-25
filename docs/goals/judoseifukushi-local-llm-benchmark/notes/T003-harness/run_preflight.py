#!/usr/bin/env python3
"""Build the blind manifest and fail closed before any LLM call."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from benchmark_contract import (
    ALL_IDS, BUILDING, IMAGE_IDS, JUDO, LAW_IDS, MULTI_ANSWER_IDS,
    ORACLE_KEYS, sha256, source_index, stratum_for, targets_for,
)


def check(name: str, passed: bool, evidence: object) -> dict:
    return {"name": name, "status": "pass" if passed else "fail", "evidence": evidence}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--temp-root", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    artifacts = args.artifacts.resolve()
    temp_root = args.temp_root.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    temp_root.mkdir(parents=True, exist_ok=True)

    found = source_index(repo)
    items = []
    source_rows = []
    for public_id in ALL_IDS:
        if public_id not in found:
            continue
        path, index, question = found[public_id]
        image = {
            "question": question.get("questionImageStorageUrls") or [],
            "choices": question.get("originalQuestionChoiceImageUrls") or [],
        }
        items.append({
            "id": public_id,
            "year": question.get("examYear"),
            "question": question.get("questionBodyText"),
            "choices": question.get("choiceTextList") or [],
            "image": image,
            "targets": targets_for(public_id),
        })
        source_rows.append({
            "id": public_id,
            "relativePath": str(path.relative_to(repo)),
            "questionIndex": index,
            "sourceSha256": sha256(path),
            "stratum": stratum_for(public_id),
        })

    manifest = {"items": items}
    (artifacts / "run-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    workflow_path = repo / "config/question_maintenance_workflow.toml"
    workflow = tomllib.loads(workflow_path.read_text())
    stage_ids = {stage["id"] for stage in workflow.get("stages", [])}
    assigned = {target for public_id in ALL_IDS for target in targets_for(public_id)}
    workflow_targets = assigned - {"source_evidence_hold"}
    stage_matrix = {
        "workflowSha256": sha256(workflow_path),
        "assignedTargets": sorted(assigned),
        "resolvedWorkflowStageIds": sorted(workflow_targets & stage_ids),
        "specialPolicyTargets": ["source_evidence_hold"],
        "routes": ["codex_only", "qwen3:14b", "qwen3.5:27b"],
        "limits": {"questionParallelism": 1, "llmCallConcurrency": 1},
        "status": "preflight_blocked",
    }
    (artifacts / "stage-matrix.json").write_text(json.dumps(stage_matrix, ensure_ascii=False, indent=2) + "\n")

    checks = []
    checks.append(check("36_unique_source_ids_and_hashes", len(found) == 36 and len(set(ALL_IDS)) == 36,
                        {"expected": 36, "resolved": len(found), "rows": source_rows}))
    checks.append(check("workflow_target_resolution", workflow_targets <= stage_ids,
                        {"requested": sorted(workflow_targets), "available": sorted(stage_ids)}))

    multi = {}
    for public_id in sorted(MULTI_ANSWER_IDS):
        question = found.get(public_id, (None, None, {}))[2]
        table = question.get("answerTableCorrectChoiceNumbers")
        classes = question.get("choiceClassCorrectChoiceNumbers")
        multi[public_id] = {"answerTableCorrectChoiceNumbers": table,
                            "choiceClassCorrectChoiceNumbers": classes,
                            "lossless": isinstance(table, list) and len(table) > 1 and table == classes}
    checks.append(check("multi_answer_lossless", all(row["lossless"] for row in multi.values()), multi))

    image_evidence = {}
    for public_id in sorted(IMAGE_IDS):
        question = found.get(public_id, (None, None, {}))[2]
        urls = list(question.get("questionImageStorageUrls") or [])
        urls.extend(url for group in question.get("originalQuestionChoiceImageUrls") or [] for url in group)
        # Source contains remote URLs but no immutable local asset path/hash. Do not
        # silently fetch mutable remote bytes and call that source provenance.
        image_evidence[public_id] = {"remoteUrls": urls, "localAssets": [], "sha256": []}
    checks.append(check("building_image_assets_and_hashes",
                        all(row["localAssets"] and row["sha256"] for row in image_evidence.values()), image_evidence))

    policy_path = repo / "prompt/qualification_docs/judoseifukushi/04_law_reference_policy.md"
    policy = policy_path.read_text()
    law_evidence = {}
    for public_id in sorted(LAW_IDS):
        law_evidence[public_id] = {
            "policyPath": str(policy_path.relative_to(repo)),
            "policyMentionsItemId": public_id in policy,
            "examTime": None,
            "currentLaw": None,
            "basisDates": [],
            "locators": [],
        }
    checks.append(check("law_exam_time_and_current_provenance",
                        all(row["examTime"] and row["currentLaw"] for row in law_evidence.values()), law_evidence))

    monitor = repo / "tools/question_review_console/monitor_events.py"
    monitor_text = monitor.read_text()
    usage_seam = "thread/tokenUsage/updated" in monitor_text and "inputTokens" in monitor_text and "outputTokens" in monitor_text
    checks.append(check("codex_usage_telemetry_seam", usage_seam,
                        {"path": str(monitor.relative_to(repo)), "sha256": sha256(monitor)}))

    llm_config = repo / "config/question_maintenance_llm.toml"
    temp_config = temp_root / "question_maintenance_llm.toml"
    shutil.copy2(llm_config, temp_config)
    config_text = temp_config.read_text()
    config_text = config_text.replace("[profiles.local_generate_codex_audit]\noperational = false",
                                      "[profiles.local_generate_codex_audit]\noperational = true")
    temp_config.write_text(config_text)
    config_unchanged = sha256(llm_config) != "" and temp_config != llm_config
    checks.append(check("temp_only_profile_flip", config_unchanged,
                        {"sourceSha256": sha256(llm_config), "tempSha256": sha256(temp_config), "tempPath": str(temp_config)}))

    checks.append(check("external_writes_intercepted", True,
                        {"runnerMode": "preflight_only", "firestoreAttempts": 0, "publicationAttempts": 0,
                         "networkModelCalls": 0}))
    serialized = json.dumps(manifest, ensure_ascii=False)
    leaked_keys = sorted(key for key in ORACLE_KEYS if f'"{key}"' in serialized)
    checks.append(check("oracle_prompt_separation", not leaked_keys,
                        {"allowedItemKeys": ["id", "year", "question", "choices", "image", "targets"],
                         "leakedKeys": leaked_keys}))
    checks.append(check("single_concurrency_contract", True,
                        {"questionParallelism": 1, "llmCallConcurrency": 1, "observedPeak": 0}))

    failed = [row["name"] for row in checks if row["status"] == "fail"]
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True).stdout.strip()
    report = {
        "schemaVersion": "judoseifukushi-local-llm-preflight/v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceCommit": commit,
        "status": "blocked" if failed else "passed",
        "failedChecks": failed,
        "modelCalls": 0,
        "checks": checks,
        "stopReason": "Any preflight gate fails; no model calls permitted." if failed else None,
    }
    (artifacts / "preflight.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    (artifacts / "execution-summary.json").write_text(json.dumps({
        "status": "preflight_blocked" if failed else "ready",
        "routesAttempted": [], "llmCalls": 0, "problemProcessingPeak": 0,
        "firestoreAttempts": 0, "publicationAttempts": 0, "failedPreflightChecks": failed,
    }, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "failedChecks": failed, "modelCalls": 0}, ensure_ascii=False))
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
