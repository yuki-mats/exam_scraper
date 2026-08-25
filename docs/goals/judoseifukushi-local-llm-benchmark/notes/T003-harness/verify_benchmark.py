#!/usr/bin/env python3
"""Verify either a completed benchmark or its fail-closed preflight receipt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmark_contract import ALL_IDS, ORACLE_KEYS


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--stage-matrix", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--check-oracle-separation", action="store_true")
    parser.add_argument("--check-usage", action="store_true")
    parser.add_argument("--check-concurrency", action="store_true")
    parser.add_argument("--check-safety", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    matrix = json.loads(args.stage_matrix.read_text())
    preflight = json.loads((args.results_root / "preflight.json").read_text())
    summary = json.loads((args.results_root / "execution-summary.json").read_text())
    items = manifest["items"]
    assert len(items) == 36
    assert [item["id"] for item in items] == ALL_IDS
    assert all(set(item) == {"id", "year", "question", "choices", "image", "targets"} for item in items)
    if args.check_oracle_separation:
        text = args.manifest.read_text()
        assert not [key for key in ORACLE_KEYS if f'"{key}"' in text]
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
        safety = json.loads((args.results_root / "safety.json").read_text())
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
