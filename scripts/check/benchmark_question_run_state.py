"""Compare state persistence on fixed copies; never write to the source run.

Run from repo root with PYTHONPATH=. and a pre-refactor --baseline-ref.
This is a storage microbenchmark, not an end-to-end model throughput forecast.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import statistics
import subprocess
import tempfile
import time
import types
from pathlib import Path

from tools.question_review_console.question_run_state import QuestionRunStateStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--baseline-ref", required=True)
    parser.add_argument("--sample", type=int, default=100)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--min-attempts", type=int, default=4)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    source_run = args.run_dir.resolve()
    reader = QuestionRunStateStore(root)
    parent = json.loads((source_run / "manifest.json").read_text())
    reader.verify_plan(source_run, parent)
    paths = sorted((source_run / "questions").glob("*.json"))
    random.Random(20260908).shuffle(paths)
    samples = []
    for path in paths:
        raw = json.loads(path.read_text())
        state = reader._validate_question_state(parent, raw, question_id=raw["questionId"])
        state = reader._hydrate_attempts(source_run, state, True)
        if len(state.get("attemptArtifacts", {})) >= args.min_attempts:
            samples.append(state)
        if len(samples) == args.sample:
            break
    if len(samples) != args.sample or args.sample < 1 or args.rounds < 1:
        raise ValueError("requested sample unavailable or invalid benchmark size")
    baseline_module = types.ModuleType("baseline_question_run_state")
    baseline_code = subprocess.check_output(
        ["git", "show", f"{args.baseline_ref}:tools/question_review_console/question_run_state.py"], text=True,
    )
    exec(compile(baseline_code, "<baseline_question_run_state>", "exec"), baseline_module.__dict__)
    report = {"baselineRef": args.baseline_ref, "sourceRun": str(source_run.relative_to(root)),
              "sampleCount": len(samples), "minimumAttempts": args.min_attempts, "rounds": args.rounds,
              "inputDigest": hashlib.sha256(json.dumps(samples, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
              "metrics": {}, "payloadParity": True}
    with tempfile.TemporaryDirectory(prefix="question-state-benchmark-") as directory:
        variants = {}
        for name, cls in (("before", baseline_module.QuestionRunStateStore), ("after", QuestionRunStateStore)):
            scratch = Path(directory) / name
            run = scratch / "run"
            run.mkdir(parents=True)
            store = cls(scratch)
            manifest = store.initialize(run, {"questionExecutions": [copy.deepcopy(s["execution"]) for s in samples]}, {"createdAt": "fixed"})
            for sample in samples:
                def seed(state, sample=sample):
                    for field in ("activeAttemptId", "attemptArtifacts", "validatedReceipts"):
                        state[field] = copy.deepcopy(sample.get(field))
                store.update_question(run, manifest, sample["questionId"], seed)
            variants[name] = (store, run, manifest)
            report["metrics"][name] = {"stateBytes": sum(p.stat().st_size for p in (run / "questions").glob("*.json")),
                                       "payloadBytes": sum(p.stat().st_size for p in (run / "attempt_payloads").rglob("*.json")),
                                       "payloadFiles": len(list((run / "attempt_payloads").rglob("*.json"))),
                                       "updateSeconds": [], "attemptReadSeconds": [], "summarySeconds": [], "unchangedSummarySeconds": []}
        # Alternate order each round to reduce order/cache/host-load bias.
        for round_index in range(args.rounds):
            for name in (("before", "after") if round_index % 2 == 0 else ("after", "before")):
                store, run, manifest = variants[name]
                metrics = report["metrics"][name]
                started = time.perf_counter()
                for sample in samples:
                    kwargs = {"hydrate_attempts": False} if name == "after" else {}
                    store.update_question(run, manifest, sample["questionId"],
                                          lambda s: s["execution"].update(status=s["execution"]["status"]), **kwargs)
                metrics["updateSeconds"].append(time.perf_counter() - started)
                started = time.perf_counter()
                for sample in samples:
                    attempt_id = sample.get("activeAttemptId") or next(reversed(sample["attemptArtifacts"]))
                    kwargs = {"hydrate_attempts": attempt_id} if name == "after" else {}
                    state = store.load_question(run, manifest, sample["questionId"], **kwargs)
                    assert state["attemptArtifacts"][attempt_id] == sample["attemptArtifacts"][attempt_id]
                metrics["attemptReadSeconds"].append(time.perf_counter() - started)
                started = time.perf_counter()
                kwargs = {"incremental": True} if name == "after" else {}
                summary = store.rebuild_summary(run, manifest, **kwargs)
                metrics["summarySeconds"].append(time.perf_counter() - started)
                assert summary["questionCount"] == args.sample
                started = time.perf_counter()
                unchanged = store.rebuild_summary(run, manifest, **kwargs)
                metrics["unchangedSummarySeconds"].append(time.perf_counter() - started)
                assert unchanged["queueSummary"] == summary["queueSummary"]
        before, after = variants["before"], variants["after"]
        for sample in samples:
            b = before[0].load_question(before[1], before[2], sample["questionId"])
            a = after[0].load_question(after[1], after[2], sample["questionId"])
            for field in ("execution", "attemptArtifacts", "validatedReceipts", "activeAttemptId"):
                assert a[field] == b[field], (sample["questionId"], field)
        assert before[0].rebuild_summary(before[1], before[2])["queueSummary"] == after[0].rebuild_summary(after[1], after[2])["queueSummary"]
    for metrics in report["metrics"].values():
        for field in ("updateSeconds", "attemptReadSeconds", "summarySeconds", "unchangedSummarySeconds"):
            metrics[field + "Median"] = statistics.median(metrics[field])
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
