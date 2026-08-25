#!/usr/bin/env python3
"""Read the root oracle only after all routes are sealed and terminal."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmark_contract import JUDO, source_index, stratum_for

ROUTES = ("codex_only", "qwen3:14b", "qwen3.5:27b")
HOLD_IDS = set(JUDO["source-answer-missing"])


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def selected(row: dict, route: str) -> list[int] | None:
    if row["status"] != "completed": return None
    result = row.get("result") if route == "codex_only" else row.get("auditResult")
    return sorted(int(value) for value in (result or {}).get("selectedIndexes", []))


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True); parser.add_argument("--results-root", type=Path, required=True)
    args = parser.parse_args(); repo = args.repo_root.resolve(); root = args.results_root.resolve()
    manifest = json.loads(args.manifest.read_text()); found = source_index(repo)
    for route in ROUTES:
        route_dir = root / "routes" / route.replace(":", "-")
        if not (route_dir / "run-complete.json").exists() or not (route_dir / "prompt-capture-seal.json").exists():
            raise RuntimeError(f"route is not sealed: {route}")
        if len(list((route_dir / "results").glob("*.json"))) != 36: raise RuntimeError(f"route not terminal: {route}")
    oracle = {"createdAfterAllRoutes": True, "items": []}
    for item in manifest["items"]:
        question = found[item["id"]][2]
        indexes = question.get("answerTableCorrectChoiceNumbers") or question.get("choiceClassCorrectChoiceNumbers") or []
        oracle["items"].append({"id": item["id"], "correctIndexes": sorted(indexes), "stratum": stratum_for(item["id"]), "hold": item["id"] in HOLD_IDS})
    write(root / "oracle-after-run.json", oracle)
    oracle_by_id = {row["id"]: row for row in oracle["items"]}
    route_summaries = {}
    for route in ROUTES:
        rows = [json.loads(path.read_text()) for path in sorted((root / "routes" / route.replace(":", "-") / "results").glob("*.json"))]
        scored = []
        for row in rows:
            truth = oracle_by_id[row["id"]]
            match = row["status"] == "hold" if truth["hold"] else selected(row, route) == truth["correctIndexes"]
            local_only = route != "codex_only" and row["status"] == "completed" and row.get("auditAccepted") is True and match
            scored.append({"id": row["id"], "stratum": truth["stratum"], "status": row["status"], "oracleMatch": match, "localOnlyPass": local_only})
        scored_nonhold = [row for row in scored if row["id"] not in HOLD_IDS]
        calls = sum(int(row.get("calls", 0)) for row in rows)
        codex_tokens = 0; usage_missing = False
        for row in rows:
            usage = row.get("usage") if route == "codex_only" else row.get("auditUsage")
            if row["status"] == "completed":
                if not usage: usage_missing = True
                else: codex_tokens += int(usage.get("inputTokens", 0)) + int(usage.get("outputTokens", 0))
        summary = {"route": route, "terminalRows": len(rows), "oracleMatches": sum(r["oracleMatch"] for r in scored_nonhold),
                   "localOnlyPasses": sum(r["localOnlyPass"] for r in scored_nonhold), "calls": calls,
                   "codexTokens": codex_tokens, "codexUsageMissing": usage_missing, "items": scored}
        route_summaries[route] = summary; write(root / "routes" / route.replace(":", "-") / "telemetry.json", summary)
    baseline = route_summaries["codex_only"]
    comparisons = {}
    for route in ROUTES[1:]:
        summary = route_summaries[route]
        call_reduction = 1 - summary["calls"] / baseline["calls"] if baseline["calls"] else None
        token_reduction = 1 - summary["codexTokens"] / baseline["codexTokens"] if baseline["codexTokens"] and not summary["codexUsageMissing"] else None
        comparisons[route] = {"localOnlyPasses": summary["localOnlyPasses"], "oracleMatches": summary["oracleMatches"],
                              "codexCallReduction": call_reduction, "codexTokenReduction": token_reduction,
                              "qualityPass": summary["localOnlyPasses"] >= 31 and summary["oracleMatches"] == 34,
                              "cloudPass": call_reduction is not None and call_reduction >= .30 and token_reduction is not None and token_reduction >= .20}
    result = {"thresholds": {"overall": 31, "oracleMatches": 34, "callReduction": .30, "tokenReduction": .20},
              "baseline": baseline, "comparisons": comparisons,
              "adoption": {route: values["qualityPass"] and values["cloudPass"] for route, values in comparisons.items()}}
    write(root / "comparison.json", comparisons); write(root / "result.json", result)
    write(root / "receipt.json", {"taskId": "T013", "status": "completed", "adoption": result["adoption"]})
    return 0

if __name__ == "__main__": raise SystemExit(main())
