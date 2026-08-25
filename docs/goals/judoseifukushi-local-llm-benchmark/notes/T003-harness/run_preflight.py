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
from pypdf import PdfReader

from benchmark_contract import (
    ALL_IDS, BUILDING, IMAGE_IDS, JUDO, LAW_IDS, MULTI_ANSWER_IDS,
    ORACLE_KEYS, sha256, source_index, stratum_for, targets_for,
)


def check(name: str, passed: bool, evidence: object) -> dict:
    return {"name": name, "status": "pass" if passed else "fail", "evidence": evidence}


def fetch_official(source: dict, temp_root: Path) -> dict:
    request = urllib.request.Request(source["url"], headers={"User-Agent": "exam-scraper-preflight/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
        status = response.status
        mime = response.headers.get_content_type()
        final_url = response.geturl()
    digest = hashlib.sha256(raw).hexdigest()
    raw_path = temp_root / "official" / digest[:20]
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(raw)
    locator = source["locator"]
    if mime == "application/json":
        parsed = json.loads(raw)
        searchable = json.dumps(parsed.get("law_full_text"), ensure_ascii=False)
        revision = parsed.get("revision_info") or {}
        revision_ok = revision.get("law_revision_id") == source["revisionId"]
        effective_ok = revision.get("amendment_enforcement_date") == source["effective"]
    elif mime == "application/pdf":
        searchable = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(raw)).pages)
        revision_ok = effective_ok = True
    else:
        for encoding in ("utf-8", "cp932", "shift_jis"):
            try:
                searchable = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("official HTML encoding is not supported")
        revision_ok = effective_ok = True
    needles = locator.get("needles") or []
    locator_ok = bool(needles) and all(needle in searchable for needle in needles)
    passed = (status == 200 and urlparse(final_url).hostname == urlparse(source["url"]).hostname
              and mime == source["mime"] and len(raw) == source["rawBytes"]
              and digest == source["rawSha256"] and locator_ok and revision_ok and effective_ok)
    return {"url": source["url"], "asOf": source["asOf"], "status": status,
            "finalHost": urlparse(final_url).hostname, "mime": mime, "rawBytes": len(raw),
            "rawSha256": digest, "locatorVerified": locator_ok,
            "revisionVerified": revision_ok, "effectiveVerified": effective_ok, "passed": passed}


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

    exam_dates_doc = json.loads((artifacts / "exam-dates.json").read_text())
    provenance = json.loads((artifacts / "law-provenance.json").read_text())
    exam_dates = exam_dates_doc.get("dates", [])
    required_years = {2020, 2024, 2025}
    exam_fetches = []
    for row in exam_dates:
        result = fetch_official(row["source"], temp_root)
        result.update({"year": row["year"], "examDate": row["examDate"]})
        exam_fetches.append(result)
    exam_date_ok = ({row.get("year") for row in exam_dates} == required_years
                    and len(exam_fetches) == 3 and all(row["passed"] for row in exam_fetches))
    checks.append(check("official_exam_dates", exam_date_ok, exam_fetches))
    law_rows = provenance.get("items", [])
    law_ok = len(law_rows) == 5 and {row.get("id") for row in law_rows} == LAW_IDS
    law_fetches = []
    for row in law_rows:
        for side in ("examTime", "current"):
            evidence = row.get(side) or {}
            sources = evidence.get("sources") or []
            law_ok = law_ok and evidence.get("asOf") is not None and bool(sources)
            for source in sources:
                result = fetch_official(source, temp_root)
                result.update({"id": row["id"], "side": side,
                               "sourceId": source.get("lawId") or source.get("noticeId")})
                law_fetches.append(result)
                law_ok = law_ok and result["passed"]
        if row.get("id") == "8987ec55216cbc63":
            for side in ("examTime", "current"):
                ids = {source.get("noticeId") for source in row[side]["sources"]}
                law_ok = law_ok and {"厚生省告示第70号", "平成28年厚生労働省告示第272号"} <= ids
    checks.append(check("law_exam_time_and_current_provenance", bool(law_ok), law_fetches))

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
    (artifacts / "T011-receipt.json").write_text(json.dumps({
        "taskId": "T011", "result": "blocked" if failed else "done", "failedChecks": failed,
        "modelCalls": 0, "artifactsDir": str(artifacts.relative_to(repo)),
    }, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "failedChecks": failed, "modelCalls": 0}, ensure_ascii=False))
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
