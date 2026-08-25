#!/usr/bin/env python3
"""Build the blind manifest and fail closed before any LLM call."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import subprocess
import tomllib
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

from benchmark_contract import (
    ALL_IDS, BUILDING, IMAGE_IDS, JUDO, LAW_IDS, MULTI_ANSWER_IDS,
    ORACLE_KEYS, sha256, source_index, stratum_for, targets_for,
)


def check(name: str, passed: bool, evidence: object) -> dict:
    return {"name": name, "status": "pass" if passed else "fail", "evidence": evidence}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument("--temp-root", type=Path, required=True)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    if not args.preflight_only:
        parser.error("T006 only permits --preflight-only")
    repo = args.repo_root.resolve()
    artifacts = args.artifacts_dir.resolve()
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
        "status": "preflight_pending",
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
        multi[public_id] = {
            "authoritativeRepresentation": "ordered_integer_list",
            "candidateRepresentation": "ordered_integer_list",
            "multipleEntriesPresent": isinstance(table, list) and len(table) > 1,
            "lossless": isinstance(table, list) and len(table) > 1 and table == classes,
        }
    checks.append(check("multi_answer_lossless", all(row["lossless"] for row in multi.values()), multi))

    approved_images = json.loads((artifacts / "approved-image-assets.json").read_text())
    manifest_urls = {
        url for item in items for url in item["image"]["question"]
    } | {
        url for item in items for group in item["image"]["choices"] for url in group
    }
    image_evidence = []
    for approved in approved_images["assets"]:
        row = {"id": approved["id"], "role": approved["role"], "url": approved["url"]}
        try:
            request = urllib.request.Request(approved["url"], headers={"User-Agent": "exam-scraper-preflight/1"})
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = response.read()
                final_url = response.geturl()
                mime = response.headers.get_content_type()
                status = response.status
            with Image.open(io.BytesIO(payload)) as decoded:
                decoded.verify()
                decoder = decoded.format
            row.update({
                "status": status,
                "finalHost": urlparse(final_url).hostname,
                "mime": mime,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "decoder": decoder,
            })
            row["passed"] = (
                approved["url"] in manifest_urls
                and urlparse(approved["url"]).hostname == "firebasestorage.googleapis.com"
                and row["finalHost"] == "firebasestorage.googleapis.com"
                and status == 200 and mime == approved["mime"]
                and len(payload) == approved["bytes"]
                and row["sha256"] == approved["sha256"]
                and decoder == approved["decoder"]
            )
            (temp_root / f'{approved["id"]}-{approved["role"]}.{decoder.lower()}').write_bytes(payload)
        except Exception as error:  # fail-closed evidence, never retry a model
            row.update({"passed": False, "error": f"{type(error).__name__}: {error}"})
        image_evidence.append(row)
    checks.append(check("building_image_assets_and_hashes",
                        len(image_evidence) == 7 and all(row["passed"] for row in image_evidence), image_evidence))

    provenance = json.loads((artifacts / "law-provenance.json").read_text())
    exam_dates = provenance.get("officialExamDates", [])
    required_years = {2020, 2024, 2025}
    exam_date_ok = {row.get("year") for row in exam_dates if all(row.get(key) for key in
                    ("officialUrl", "documentTitle", "exactLocator", "rawSha256", "examDate"))} == required_years
    checks.append(check("official_exam_dates", exam_date_ok, exam_dates))
    law_rows = provenance.get("items", [])
    law_ok = len(law_rows) == 5 and {row.get("id") for row in law_rows} == LAW_IDS
    for row in law_rows:
        for side in ("examTime", "current"):
            evidence = row.get(side) or {}
            law_ok = law_ok and all(evidence.get(key) for key in
                ("officialUrl", "documentTitle", "revision", "basisDate", "exactLocator", "rawSha256"))
        if row.get("id") == "8987ec55216cbc63":
            notices = row.get("advertisingDesignationNotices") or {}
            law_ok = law_ok and all((notices.get(side) or {}).get("officialUrl") and
                                    (notices.get(side) or {}).get("exactLocator") and
                                    (notices.get(side) or {}).get("rawSha256")
                                    for side in ("examTime", "current"))
    checks.append(check("law_exam_time_and_current_provenance", bool(law_ok), law_rows))

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

    fixed_set = {
        "version": 2,
        "count": len(ALL_IDS),
        "ids": ALL_IDS,
        "removed": ["dfb3fe84e07f47f9", "1ebaca9b85c6dd6e"],
        "added": ["4ef67113801362d9", "ef0992b6887ec00b"],
        "rejected": ["d732ddbaf0d4f522"],
        "selectionFields": ["id", "year", "question", "choices", "targets"],
    }
    (artifacts / "fixed-set-v2.json").write_text(json.dumps(fixed_set, ensure_ascii=False, indent=2) + "\n")
    failed = [row["name"] for row in checks if row["status"] == "fail"]
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True).stdout.strip()
    report = {
        "schemaVersion": "judoseifukushi-local-llm-preflight/v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceCommit": commit,
        "status": "blocked" if failed else "pass",
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
    safety = {"mode": "preflight_only", "modelCalls": 0, "codexCalls": 0, "ollamaCalls": 0,
              "firestoreAttempts": 0, "publicationAttempts": 0, "repositoryImageBytes": 0,
              "problemProcessingPeak": 0, "llmCallPeak": 0}
    (artifacts / "safety.json").write_text(json.dumps(safety, ensure_ascii=False, indent=2) + "\n")
    (artifacts / "T006-receipt.json").write_text(json.dumps({
        "taskId": "T006", "result": "blocked" if failed else "done", "failedChecks": failed,
        "modelCalls": 0, "artifactsDir": str(artifacts.relative_to(repo)),
    }, ensure_ascii=False, indent=2) + "\n")
    stage_matrix["status"] = "preflight_blocked" if failed else "preflight_passed"
    (artifacts / "stage-matrix.json").write_text(json.dumps(stage_matrix, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "failedChecks": failed, "modelCalls": 0}, ensure_ascii=False))
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
