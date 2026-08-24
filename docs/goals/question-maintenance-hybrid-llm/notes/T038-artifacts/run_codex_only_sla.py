#!/usr/bin/env python3
"""Measure the fixed acceptance set through an isolated review server."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


FIXED = (
    ("2nd-class-kenchikushi", "85010", "dd07b4977677b7bb", ("explanation",)),
    ("2nd-class-kenchikushi", "85010", "3239d392acd6236c", ("correct_choice",)),
    ("2nd-class-kenchikushi", "85010", "64032ef7f4bac816", ("question_type",)),
    ("2nd-class-kenchikushi", "85010", "64bd269e44533561", ("question_type", "explanation")),
    ("2nd-class-kenchikushi", "85003", "3b24e06367db4222", ("law_context", "law_audit", "explanation")),
    ("2nd-class-kenchikushi", "85003", "ed7d14b661421a12", ("question_type", "explanation")),
    ("aws-solutions-architect-associate", "udemy-aws-saa-c03", "754c574d738ad390", ("originalize", "explanation")),
)


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).astimezone()


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.token = self.get("/api/session")["sessionToken"]

    def get(self, path: str) -> dict:
        with urllib.request.urlopen(self.base + path, timeout=30) as response:
            return json.load(response)

    def post(self, path: str, body: dict) -> dict:
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Origin": self.base, "X-Review-Session": self.token},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"HTTP {error.code}: {error.read().decode()}") from error


def resolve_question_id(api: Api, qualification: str, group: str, original_id: str) -> str:
    query = urllib.parse.urlencode({"qualification": qualification, "listGroupId": group, "limit": 500})
    payload = api.get("/api/questions?" + query)
    matches = [item["id"] for item in payload["questions"] if original_id in str(item.get("reviewKey") or "")]
    if len(matches) != 1:
        raise RuntimeError(f"question identity mismatch: {original_id} -> {matches}")
    return matches[0]


def run_one(api: Api, item: tuple) -> dict:
    qualification, group, original_id, stages = item
    question_id = resolve_question_id(api, qualification, group, original_id)
    body = {
        "qualification": qualification,
        "stageIds": list(stages),
        "mode": "group_refresh",
        "listGroupIds": [group],
        "questionIds": [question_id],
        "questionConcurrency": 1,
        "modelProfile": "codex_only",
    }
    preview = api.post("/api/qualification-runs/preview", body)
    if not preview.get("canStart"):
        raise RuntimeError(f"preview blocked for {original_id}: {preview}")
    admitted = now()
    started = api.post("/api/qualification-runs/start", {**body, "previewToken": preview["previewToken"]})
    job_id = started["job"]["jobId"]
    while True:
        job = api.get("/api/jobs/" + urllib.parse.quote(job_id))
        if job["status"] in {"succeeded", "failed"}:
            break
        time.sleep(1)
    terminal = now()
    run_id = started["run"]["runId"]
    detail = api.get(
        "/api/qualification-runs/" + urllib.parse.quote(run_id) + "/questions/" +
        urllib.parse.quote(question_id) + "?" + urllib.parse.urlencode({"qualification": qualification})
    )
    return {
        "qualification": qualification,
        "listGroupId": group,
        "originalQuestionId": original_id,
        "questionId": question_id,
        "stageIds": list(stages),
        "admittedAt": admitted.isoformat(timespec="microseconds"),
        "terminalAt": terminal.isoformat(timespec="microseconds"),
        "admissionToTerminalSeconds": round((terminal - admitted).total_seconds(), 6),
        "preview": preview,
        "started": started,
        "job": job,
        "questionRun": detail,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--max-questions", type=int, default=7)
    parser.add_argument("--stop-after-sla-failure", action="store_true")
    args = parser.parse_args()
    api = Api(args.base)
    result = {
        "profile": "codex_only",
        "fixedSetCount": 7,
        "partial": args.max_questions < 7,
        "codexStatusBefore": api.get("/api/codex/status"),
        "runs": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    selected = FIXED[args.start_index : args.start_index + args.max_questions]
    for item in selected:
        print(f"starting codex_only {item[2]} {','.join(item[3])}", flush=True)
        result["runs"].append(run_one(api, item))
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        if args.stop_after_sla_failure and result["runs"][-1]["admissionToTerminalSeconds"] > 180:
            result["stoppedAfterFirstDecisiveSlaFailure"] = True
            break
    result["codexStatusAfter"] = api.get("/api/codex/status")
    result["completedQuestionCount"] = len(result["runs"])
    result["partial"] = len(result["runs"]) != 7
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
