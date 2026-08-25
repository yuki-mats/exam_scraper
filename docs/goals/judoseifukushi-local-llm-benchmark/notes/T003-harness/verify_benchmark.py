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
    raise AssertionError("completed-run verification is not available until preflight passes")


if __name__ == "__main__":
    raise SystemExit(main())
