"""Read-only, v2/v3-compatible maintenance throughput and quality observation.

No payload generation, repair, summary writes, or external publication.
Timestamps inherited from a previous run are excluded from this run's rate.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from tools.question_review_console.question_run_state import QuestionRunStateStore
from tools.question_review_console.explanation_quality import (
    explanation_style_issues, law_evidence_utilization_issues,
)
from tools.question_review_console.patch_validation import law_audit_quality_warnings


def instant(value):
    return datetime.fromisoformat(value) if value else None


def distribution(values):
    values = sorted(v for v in values if v is not None and v >= 0)
    return {"n": len(values), **{
        label: round(values[int((len(values) - 1) * percentile)], 3) if values else None
        for label, percentile in (("p50", .5), ("p90", .9))
    }}


def audit(root: Path, run: Path, *, as_of=None, quality=False):
    reader = QuestionRunStateStore(root)
    parent = json.loads((run / "manifest.json").read_text())
    reader.verify_plan(run, parent)
    now = instant(as_of) if as_of else datetime.now().astimezone()
    started = instant(parent.get("startedAt") or parent["createdAt"])
    stages, by_stage, holds, statuses, checks = (Counter() for _ in range(5))
    attempts, completed_items, completed_questions = [], [], []
    quality_counts, forms = Counter(), Counter()
    quality_issues, duplicates = [], defaultdict(set)
    for question_id in reader.question_ids(run, parent):
        state = reader.load_question(run, parent, question_id, hydrate_attempts=False)
        execution_stages = state["execution"]["stages"]
        stages.update(s["status"] for s in execution_stages)
        if all(s["status"] in {"validated", "not_applicable"} for s in execution_stages):
            dates = [instant(s.get("finishedAt")) for s in execution_stages if s.get("finishedAt")]
            completed_questions.append(max(dates) if dates else None)
        elif any(s["status"] == "blocked" for s in execution_stages):
            holds[next(s.get("error") or "理由なし" for s in execution_stages if s["status"] == "blocked")] += 1
        for stage in execution_stages:
            if stage["status"] == "validated":
                by_stage[stage["stageId"]] += 1
                completed_items.append((instant(stage.get("finishedAt")), stage["stageId"]))
        raw_attempts = state.get("attemptArtifacts", {})
        attempts.extend(raw_attempts.values())
        statuses.update(a.get("status") for a in raw_attempts.values())
        for attempt in raw_attempts.values():
            for result in attempt.get("batchQuestionResults") or []:
                for check in result.get("commands") or []:
                    checks[(str(check.get("command")), str(check.get("status")))] += 1
        if not quality:
            continue
        explanation = next((s for s in execution_stages if s["stageId"] == "explanation" and s["status"] == "validated"), None)
        if explanation is None:
            continue
        attempt_id = next((a.get("childRunId") for a in reversed(explanation.get("validationAttempts", [])) if a.get("status") == "validated"), None)
        if not attempt_id or attempt_id not in raw_attempts:
            quality_counts["missingCandidate"] += 1
            continue
        state = reader.load_question(run, parent, question_id, hydrate_attempts=attempt_id)
        attempt = state["attemptArtifacts"][attempt_id]
        if attempt.get("plan", {}).get("stageId") != "explanation" or not attempt.get("preparedCandidate"):
            quality_counts["missingOrMismatchedCandidate"] += 1
            continue
        candidate = attempt["preparedCandidate"]["content"]["candidatePayload"]["questionResults"][0]
        if candidate["status"] != "candidate":
            quality_counts["nonCandidate"] += 1
            continue
        projected_path = (root / attempt["plan"]["progressTargets"][0]["_projectedInputPath"]).resolve()
        if not projected_path.is_relative_to(root):
            raise ValueError("projected input is outside repository")
        record = json.loads(projected_path.read_text())["question_bodies"][0]
        for update in candidate["updates"]:
            for field in update.get("setFields", []):
                record[field["field"]] = field["value"]
            for field in update.get("unsetFields", []):
                record.pop(field, None)
        texts = record.get("explanationText", [])
        quality_counts.update(questions=1, explanations=len(texts), empty=sum(not str(t).strip() for t in texts),
                              law=int(record.get("isLawRelated") is True), calculation=int(record.get("isCalculationQuestion") is True))
        forms[record.get("questionType")] += 1
        issues = explanation_style_issues(texts, record.get("correctChoiceText"), choice_texts=record.get("choiceTextList"),
                                         question_type=record.get("questionType"), is_calculation_question=record.get("isCalculationQuestion") is True)
        issues += law_evidence_utilization_issues(record) + law_audit_quality_warnings(record)
        if issues:
            quality_issues.append({"questionId": question_id, "issues": issues})
        for text in texts:
            if len(str(text)) >= 40:
                duplicates[str(text)].add(question_id)
    windows = []
    for minutes in (30, 60, 120, 240):
        cutoff = max(started, now - timedelta(minutes=minutes))
        hours = max((now - cutoff).total_seconds() / 3600, .000001)
        recent = Counter(stage for at, stage in completed_items if at and cutoff <= at <= now)
        recent_attempts = [a for a in attempts if instant(a.get("finishedAt")) and cutoff <= instant(a["finishedAt"]) <= now]
        timings = defaultdict(list)
        for attempt in recent_attempts:
            telemetry = attempt.get("modelTurnTelemetry") or {}
            stage = attempt.get("stageId", "unknown")
            for field in ("modelExecutorQueueWaitSeconds", "patchToolQueueWaitSeconds", "patchToolLockWaitSeconds"):
                timings[field].append(attempt.get(field))
            for field in ("appServerQueueWaitSeconds", "modelTurnDurationSeconds"):
                timings[field].append(telemetry.get(field))
            for field, begin in (("attemptSeconds", attempt.get("createdAt")), ("afterModelSeconds", telemetry.get("modelTurnFinishedAt"))):
                if begin:
                    seconds = (instant(attempt["finishedAt"]) - instant(begin)).total_seconds()
                    timings[field].append(seconds)
                    timings[f"{stage}.{field}"].append(seconds)
        windows.append({"minutes": minutes, "actualMinutes": round(hours * 60, 2),
                        "validatedItemsPerHour": round(sum(recent.values()) / hours, 1),
                        "completedQuestionsPerHour": round(sum(bool(at and cutoff <= at <= now) for at in completed_questions) / hours, 1),
                        "byStage": dict(recent), "timingCohort": "attempts finished inside window",
                        "timings": {k: distribution(v) for k, v in timings.items()}})
    return {"runId": parent["runId"], "observedAt": now.isoformat(), "status": parent["status"],
            "targetQuestions": parent["targetCount"], "completedQuestions": len(completed_questions),
            "remainingQuestions": max(parent["targetCount"] - len(completed_questions), 0),
            "blockedQuestions": sum(holds.values()), "stages": dict(stages), "byStage": dict(by_stage),
            "attemptStatuses": dict(statuses), "topHoldReasons": holds.most_common(5),
            "checks": [{"command": k[0], "status": k[1], "count": v} for k, v in checks.items()],
            "windows": windows, "quality": {"enabled": quality, **quality_counts, "forms": dict(forms),
                "issueQuestions": len(quality_issues), "issueExamples": quality_issues[:5],
                "duplicateGroups": sum(len(ids) > 1 for ids in duplicates.values()), "independentEvaluation": "not performed by this audit"}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--as-of")
    parser.add_argument("--quality", action="store_true")
    args = parser.parse_args()
    print(json.dumps(audit(Path.cwd().resolve(), args.run_dir.resolve(), as_of=args.as_of, quality=args.quality), ensure_ascii=False, indent=2))
