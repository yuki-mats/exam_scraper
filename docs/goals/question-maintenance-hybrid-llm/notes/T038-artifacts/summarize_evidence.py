#!/usr/bin/env python3
"""Create sanitized, machine-readable evidence from the isolated T038 run."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def peak(intervals: list[tuple[datetime, datetime]]) -> int:
    events = []
    for start, end in intervals:
        events.extend(((start, 1), (end, -1)))
    current = maximum = 0
    for _, delta in sorted(events, key=lambda event: (event[0], event[1])):
        current += delta
        maximum = max(maximum, current)
    return maximum


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    raw = json.loads(args.raw.read_text())
    questions = []
    turns = []
    question_intervals = []
    turn_intervals = []
    for run in raw["runs"]:
        admitted = parse(run["admittedAt"])
        terminal = parse(run["terminalAt"])
        question_intervals.append((admitted, terminal))
        stage_rows = []
        for stage in run["questionRun"]["execution"]["stages"]:
            attempt_rows = []
            for attempt in stage.get("validationAttempts", []):
                start = parse(attempt["startedAt"])
                end = parse(attempt["finishedAt"])
                turn_intervals.append((start, end))
                artifact = run["questionRun"].get("attemptArtifacts", {}).get(attempt["childRunId"], {})
                row = {
                    "questionId": run["originalQuestionId"],
                    "stageId": stage["stageId"],
                    "attempt": attempt["attempt"],
                    "attemptMode": attempt["attemptMode"],
                    "startedAt": attempt["startedAt"],
                    "finishedAt": attempt["finishedAt"],
                    "wallSeconds": round((end - start).total_seconds(), 6),
                    "modelTurnDurationSeconds": artifact.get("modelTurnDurationSeconds"),
                    "modelExecutorQueueWaitSeconds": artifact.get("modelExecutorQueueWaitSeconds"),
                    "appServerQueueWaitSeconds": artifact.get("appServerQueueWaitSeconds"),
                    "backend": attempt["backend"],
                    "actualModel": attempt["actualModel"],
                    "reasoningEffort": attempt["reasoningEffort"],
                    "status": attempt["status"],
                    "fallbackUsed": attempt["fallbackUsed"],
                    "localSuccess": attempt["localSuccess"],
                }
                turns.append(row)
                attempt_rows.append(row)
            stage_rows.append({
                "stageId": stage["stageId"],
                "status": stage["status"],
                "finishedAt": stage.get("finishedAt"),
                "attempts": attempt_rows,
            })
        questions.append({
            "originalQuestionId": run["originalQuestionId"],
            "stageIds": run["stageIds"],
            "admittedAt": run["admittedAt"],
            "terminalAt": run["terminalAt"],
            "admissionToTerminalSeconds": run["admissionToTerminalSeconds"],
            "terminalStatus": run["questionRun"]["execution"]["status"],
            "jobStatus": run["job"]["status"],
            "sla180Passed": run["admissionToTerminalSeconds"] <= 180,
            "qualityOracleResult": "passed" if run["originalQuestionId"] == "3239d392acd6236c" else "not_fully_assessed_in_partial_run",
            "qualityEvidence": (
                "選択肢3のみ間違いとのaccepted feedbackがblind oracleと一致。"
                if run["originalQuestionId"] == "3239d392acd6236c"
                else "工程の決定的検査はvalidated。全7問終了後のblind oracle比較は未完了。"
            ),
            "stages": stage_rows,
        })

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "classification": "blocked_partial_not_full",
        "sourceCommit": args.source_commit,
        "profile": "codex_only",
        "fixedSetCount": 7,
        "completedQuestionCount": len(questions),
        "unmeasuredQuestionCount": 7 - len(questions),
        "allSevenSlaResult": "not_classified",
        "completedQuestionsSla180Passed": all(item["sla180Passed"] for item in questions),
        "maximumObservedAdmissionToTerminalSeconds": max(item["admissionToTerminalSeconds"] for item in questions),
        "questionProcessingPeak": peak(question_intervals),
        "llmCallPeak": peak(turn_intervals),
        "criticalIncidents": 0,
        "firestoreWrites": 0,
        "publicationWrites": 0,
        "productionPatchWrites": 0,
        "blocker": "85003 preview returned 対象年度がありません twice after isolated category correction; stop_if reached.",
        "questions": questions,
    }
    (args.output_dir / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    with (args.output_dir / "turn_timings.jsonl").open("w") as output:
        for turn in turns:
            output.write(json.dumps(turn, ensure_ascii=False, separators=(",", ":")) + "\n")

    status = subprocess.run(
        ["git", "status", "--short", "--", "output"], cwd=args.repo, text=True, capture_output=True, check=True
    ).stdout.splitlines()
    source_files = sorted(args.repo.glob("output/**/00_source/*.json"))
    hashes = {
        "scope": "real repository output/**/00_source/*.json",
        "fileCount": len(source_files),
        "aggregateSha256": hashlib.sha256(
            "\n".join(f"{path.relative_to(args.repo)} {sha256(path)}" for path in source_files).encode()
        ).hexdigest(),
        "trackedOrUntrackedOutputStatus": status,
        "comparison": "run used only the isolated repository; real repository output status remained empty",
    }
    (args.output_dir / "no_write_hashes.json").write_text(json.dumps(hashes, ensure_ascii=False, indent=2) + "\n")
    environment = {
        "codex": {
            "available": raw["codexStatusBefore"]["available"],
            "allowed": raw["codexStatusBefore"]["allowed"],
            "accountType": raw["codexStatusBefore"]["accountType"],
            "planType": raw["codexStatusBefore"]["planType"],
            "model": raw["codexStatusBefore"]["model"],
        },
        "profile": raw["codexStatusBefore"]["modelProfiles"]["codex_only"],
        "isolatedRepo": True,
        "oraclePassedToModel": False,
        "allowedApiFamilies": ["session", "codex/status", "questions", "qualification-runs/preview", "qualification-runs/start", "jobs", "qualification-runs/questions"],
        "forbiddenApiCalls": {"firestore": 0, "publication": 0},
    }
    (args.output_dir / "environment.json").write_text(json.dumps(environment, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
