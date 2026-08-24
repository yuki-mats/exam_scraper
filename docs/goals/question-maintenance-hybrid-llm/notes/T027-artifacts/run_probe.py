#!/usr/bin/env python3
"""T027: run the fixed q3/q4 route probe against an isolated review server."""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path


FIXED = (
    ("2nd-class-kenchikushi", "85010", "64032ef7f4bac816", ("question_type",)),
    (
        "2nd-class-kenchikushi",
        "85010",
        "64bd269e44533561",
        ("question_type", "explanation"),
    ),
)


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
            headers={
                "Content-Type": "application/json",
                "Origin": self.base,
                "X-Review-Session": self.token,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)


def resolve_question_id(api: Api, qualification: str, group: str, original_id: str) -> str:
    query = urllib.parse.urlencode(
        {"qualification": qualification, "listGroupId": group, "limit": 500}
    )
    questions = api.get("/api/questions?" + query)["questions"]
    matches = [
        item["id"]
        for item in questions
        if original_id in str(item.get("reviewKey") or "")
    ]
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
        "modelProfile": "local_generate_codex_audit",
    }
    preview = api.post("/api/qualification-runs/preview", body)
    if not preview.get("canStart"):
        raise RuntimeError(f"preview blocked for {original_id}: {preview}")
    started = api.post(
        "/api/qualification-runs/start",
        {**body, "previewToken": preview["previewToken"]},
    )
    while True:
        job = api.get("/api/jobs/" + urllib.parse.quote(started["job"]["jobId"]))
        if job["status"] in {"succeeded", "failed"}:
            break
        time.sleep(3)
    detail = api.get(
        "/api/qualification-runs/"
        + urllib.parse.quote(started["run"]["runId"])
        + "/questions/"
        + urllib.parse.quote(question_id)
        + "?"
        + urllib.parse.urlencode({"qualification": qualification})
    )
    return {
        "originalQuestionId": original_id,
        "questionId": question_id,
        "stageIds": list(stages),
        "runId": started["run"]["runId"],
        "job": job,
        "questionRun": detail,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    api = Api(args.base)
    result = {
        "profile": "local_generate_codex_audit",
        "codexStatusBefore": api.get("/api/codex/status"),
        "runs": [],
    }
    for item in FIXED:
        print(f"starting {item[2]}", flush=True)
        result["runs"].append(run_one(api, item))
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    result["codexStatusAfter"] = api.get("/api/codex/status")
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
