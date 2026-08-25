#!/usr/bin/env python3
"""Verify either a completed benchmark or its fail-closed preflight receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from benchmark_contract import ALL_IDS, ORACLE_KEYS


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--stage-matrix", type=Path)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--sealed-baseline-only", action="store_true")
    parser.add_argument("--expected-baseline-calls", type=int)
    parser.add_argument("--expected-usage-missing", type=int)
    parser.add_argument("--allow-token-inconclusive", action="store_true")
    parser.add_argument("--check-oracle-separation", action="store_true")
    parser.add_argument("--check-usage", action="store_true")
    parser.add_argument("--check-concurrency", action="store_true")
    parser.add_argument("--check-safety", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--completed-run", action="store_true")
    args = parser.parse_args()
    if args.sealed_baseline_only:
        route = args.results_root / "routes" / "codex_only"
        rows = [json.loads(path.read_text()) for path in sorted((route / "results").glob("*.json"))]
        events_path = args.results_root / "prompt-captures" / "transport-events.jsonl"
        events = [json.loads(line) for line in events_path.read_text().splitlines()]
        requests = [row for row in events if row["kind"] == "request" and row["provider"] == "codex_app_server"]
        responses = [row for row in events if row["kind"] == "response" and row["provider"] == "codex_app_server"]
        assert len(rows) == 36 and sum(row["status"] == "completed" for row in rows) == 34
        assert sum(row["status"] == "hold" for row in rows) == 2
        for row in rows:
            value = dict(row); expected = value.pop("rowSha256")
            from capture_transport import digest_json
            assert digest_json(value) == expected
        assert len(requests) == args.expected_baseline_calls == len(responses)
        assert sum(row.get("usage") is None for row in responses) == args.expected_usage_missing
        seal = json.loads((route / "prompt-capture-seal.json").read_text())
        assert seal["sha256"] == hashlib.sha256(events_path.read_bytes()).hexdigest()
        print("PASS: sealed Codex baseline is unchanged and internally consistent")
        return 0
    assert args.manifest and args.stage_matrix
    manifest = json.loads(args.manifest.read_text())
    matrix = json.loads(args.stage_matrix.read_text())
    items = manifest["items"]
    assert len(items) == 36
    assert [item["id"] for item in items] == ALL_IDS
    assert all(set(item) == {"id", "year", "question", "choices", "image", "targets"} for item in items)
    if args.check_oracle_separation:
        text = args.manifest.read_text()
        assert not [key for key in ORACLE_KEYS if f'"{key}"' in text]
        if not args.completed_run:
            assert not (args.results_root / "oracle-after-run.json").exists(), "oracle must not exist before terminal model runs"
        forbidden = ("correctChoiceText", "answerTableCorrectChoiceNumbers", "answer_result_text",
                     "choiceClassCorrectChoiceNumbers", "existingReview", "priorModelResult")
        for name in ("fixed-set-v2.json", "approved-image-assets.json", "exam-dates.json", "law-provenance.json",
                     "run-manifest.json", "stage-matrix.json", "preflight.json",
                     "execution-summary.json", "safety.json", "T006-receipt.json", "T008-receipt.json"):
            path = args.results_root / name
            if path.exists():
                assert not [key for key in forbidden if key in path.read_text()], f"oracle-derived field in {name}"
    assert matrix["limits"] == {"questionParallelism": 1, "llmCallConcurrency": 1}
    if args.completed_run:
        oracle = json.loads((args.results_root / "oracle-after-run.json").read_text())
        result = json.loads((args.results_root / "result.json").read_text())
        safety = json.loads((args.results_root / "final-safety.json").read_text())
        assert oracle["createdAfterAllRoutes"] is True and len(oracle["items"]) == 36
        assert safety["firestoreAttempts"] == safety["publicationAttempts"] == safety["productionPatchWrites"] == 0
        assert safety["llmCallPeak"] == safety["problemProcessingPeak"] == 1
        assert safety["auditBatchMaxQuestions"] <= 5 and safety["auditBatchMaxBytes"] <= 120000
        terminal = {"completed", "hold", "availability_reject", "unsupported_modality", "skipped_early_rejection"}
        for route in ("codex_only", "qwen3-14b", "qwen3.5-27b"):
            route_dir = args.results_root / "routes" / route
            rows = [json.loads(path.read_text()) for path in (route_dir / "results").glob("*.json")]
            assert len(rows) == 36 and all(row["status"] in terminal for row in rows)
            assert json.loads((route_dir / "run-complete.json").read_text())["terminalRows"] == 36
            assert (route_dir / "prompt-capture-seal.json").exists()
        for route in ("qwen3-14b", "qwen3.5-27b"):
            rows = [json.loads(path.read_text()) for path in (args.results_root / "routes" / route / "results").glob("*.json")]
            holds = [row for row in rows if row["id"] in {"9c3273bf54057cd0", "fa0d4e2042e65b59"}]
            assert len(holds) == 2 and all(row["status"] == "hold" and row["localCalls"] == row["codexCalls"] == 0 for row in holds)
            capture = args.results_root / "T015" / (route + "-transport.jsonl")
            text = capture.read_text()
            assert not any(key in text for key in ("correctChoiceText", "answerTableCorrectChoiceNumbers", "existingReview", "priorModelResult", "data:image", "base64,"))
            requests = [json.loads(line) for line in text.splitlines() if json.loads(line)["kind"] == "request"]
            assert all(len(row["questionIds"]) <= 5 and row["canonicalRequestBytes"] <= 120000 for row in requests if row["role"] == "audit")
        if args.allow_token_inconclusive:
            assert result["tokenMetric"] == {"status": "inconclusive_provider_usage_unavailable", "reduction": None, "estimated": False}
        else:
            assert result["baseline"]["codexUsageMissing"] is False
        print("PASS: completed real benchmark is sealed and safe")
        return 0
    preflight = json.loads((args.results_root / "preflight.json").read_text())
    summary = json.loads((args.results_root / "execution-summary.json").read_text())
    if preflight["status"] == "blocked":
        assert preflight["failedChecks"]
        assert preflight["modelCalls"] == 0
        assert summary["routesAttempted"] == []
        if args.check_usage:
            assert any(row["name"] == "codex_usage_telemetry_seam" and row["status"] == "pass" for row in preflight["checks"])
        if args.check_concurrency:
            assert summary["problemProcessingPeak"] == 0
        if args.check_safety:
            assert summary["firestoreAttempts"] == summary["publicationAttempts"] == 0
        print("PASS: preflight blocked fail-closed before all model calls")
        return 0
    if args.preflight_only:
        assert preflight["status"] == "pass"
        assert preflight["failedChecks"] == [] and preflight["modelCalls"] == 0
        safety_path = args.results_root / ("final-safety.json" if (args.results_root / "final-safety.json").exists() else "safety.json")
        safety = json.loads(safety_path.read_text())
        assert safety["modelCalls"] == safety["codexCalls"] == safety["ollamaCalls"] == 0
        dates = json.loads((args.results_root / "exam-dates.json").read_text())
        laws = json.loads((args.results_root / "law-provenance.json").read_text())
        assert len(dates["dates"]) == 3 and len(laws["items"]) == 5
        assert all(item["examTime"] is not item["current"] for item in laws["items"])
        print("PASS: preflight passed with zero model calls")
        return 0
    raise AssertionError("completed-run verification requires model results")


if __name__ == "__main__":
    raise SystemExit(main())
