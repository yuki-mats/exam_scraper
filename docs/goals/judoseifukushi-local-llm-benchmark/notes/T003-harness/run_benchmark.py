#!/usr/bin/env python3
"""Run the fixed blind set against the real Codex App Server and Ollama."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

HARNESS = Path(__file__).resolve().parent
REPO_DEFAULT = HARNESS.parents[4]
sys.path.insert(0, str(REPO_DEFAULT))

from capture_transport import CaptureTransport, digest_json, utc_now  # noqa: E402
from prepare_blind_runtime import prepare  # noqa: E402
from tools.question_review_console.codex_app_server import CodexAppServerClient  # noqa: E402

HOLD_IDS = {"9c3273bf54057cd0", "fa0d4e2042e65b59"}
TERMINAL = {"completed", "hold", "availability_reject", "early_reject"}


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, path)


def extract_json(text: str) -> Any:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char in "[{":
            try:
                return decoder.raw_decode(text[index:])[0]
            except json.JSONDecodeError:
                continue
    raise ValueError("provider response did not contain JSON")


def usage_from_events(events: list[dict[str, Any]]) -> dict[str, int] | None:
    best: dict[str, int] = {}
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = str(key).casefold()
                if isinstance(item, int) and not isinstance(item, bool):
                    if normalized in {"inputtokens", "input_tokens", "prompt_tokens"}:
                        best["inputTokens"] = max(best.get("inputTokens", 0), item)
                    elif normalized in {"outputtokens", "output_tokens", "completion_tokens"}:
                        best["outputTokens"] = max(best.get("outputTokens", 0), item)
                walk(item)
        elif isinstance(value, list):
            for item in value: walk(item)
    walk(events)
    return best if {"inputTokens", "outputTokens"} <= best.keys() else None


def schema() -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": ["results"],
            "properties": {"results": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["id", "selectedIndexes", "explanations", "questionType"],
                "properties": {"id": {"type": "string"},
                               "selectedIndexes": {"type": "array", "items": {"type": "integer"}},
                               "explanations": {"type": "array", "items": {"type": "string"}},
                               "questionType": {"type": "string"}}}}}}


def prompt_for(items: list[dict[str, Any]], *, audit_candidates: dict[str, Any] | None = None) -> str:
    base = ("あなたは資格試験問題の独立編集者です。与えられた問題だけを使い、各問の正しい選択肢番号(1始まり、複数可)、"
            "全選択肢の簡潔な根拠、回答体験に合うquestionTypeをJSONで返してください。正答資料はありません。推測を事実として断定しないでください。")
    body: dict[str, Any] = {"questions": items}
    if audit_candidates is not None:
        base = ("あなたは盲検監査者です。問題と候補を知識に照らして監査し、候補をそのまま信用せず、正しい結果を同じJSON schemaで返してください。")
        body["candidates"] = audit_candidates
    return base + "\n" + json.dumps(body, ensure_ascii=False, sort_keys=True)


class Runner:
    def __init__(self, repo: Path, manifest: Path, matrix: Path, artifacts: Path, temp: Path) -> None:
        self.repo, self.manifest_path, self.matrix_path = repo, manifest, matrix
        self.artifacts, self.temp = artifacts, temp
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.matrix = json.loads(matrix.read_text())
        self.manifest = json.loads(manifest.read_text())
        self.transport = CaptureTransport(self.artifacts / "prompt-captures")
        self.active = 0; self.peak = 0; self.problem_peak = 0; self.model_calls = 0
        self.codex: CodexAppServerClient | None = None

    def gate(self) -> dict[str, Any]:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, check=True, text=True, capture_output=True).stdout.strip()
        if self.matrix.get("status") != "preflight_pass" or self.matrix.get("sourceCommit") != head:
            raise RuntimeError("pre-model fingerprint gate failed")
        runtime = prepare(self.repo, self.manifest_path, self.temp / "runtimes", self.artifacts)
        if runtime["itemCount"] != 36:
            raise RuntimeError("runtime item count mismatch")
        tags = json.load(urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=10))
        names = {row["name"] for row in tags.get("models", [])}
        if not {"qwen3:14b", "qwen3.5:27b"} <= names:
            raise RuntimeError("approved Ollama models unavailable")
        self.codex = CodexAppServerClient(self.repo, turn_timeout=3600)
        status = self.codex.diagnose_subscription_access()
        if not status.get("allowed"):
            raise RuntimeError(f"Codex subscription gate failed: {status.get('failureKind')}")
        environment = {"sourceCommit": head, "preflightFingerprint": self.matrix["preflightFingerprint"],
                       "runtime": runtime, "codex": status, "ollamaModels": sorted(names),
                       "harnessFingerprint": hashlib.sha256(b"".join(path.read_bytes() for path in sorted(HARNESS.glob("*.py")))).hexdigest()}
        atomic_json(self.artifacts / "environment.json", environment)
        atomic_json(self.artifacts / "run-marker.json", {"status": "running", "startedAt": utc_now(), **environment})
        return environment

    def enter(self) -> None:
        self.active += 1; self.peak = max(self.peak, self.active); self.problem_peak = max(self.problem_peak, self.active)
        if self.active > 1: raise RuntimeError("global concurrency exceeded one")

    def leave(self) -> None: self.active -= 1

    def codex_call(self, route: str, items: list[dict[str, Any]], role: str,
                   attempt: int, candidates: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, int] | None, dict[str, str]]:
        assert self.codex is not None
        prompt = prompt_for(items, audit_candidates=candidates)
        request = self.transport.request(route=route, provider="codex_app_server",
            question_ids=[row["id"] for row in items], stage="all_assigned", role=role,
            attempt=attempt, payload={"prompt": prompt, "outputSchema": schema()})
        events: list[dict[str, Any]] = []; ids: dict[str, str] = {}
        self.enter(); self.model_calls += 1
        try:
            result = self.codex.run_turn(prompt, work_type="question_maintenance_benchmark", sandbox="read-only",
                emit=lambda _line: None, output_schema=schema(), cwd=self.temp,
                on_thread_started=lambda thread, session: ids.update(threadId=thread, sessionId=session),
                on_turn_started=lambda thread, turn: ids.update(threadId=thread, turnId=turn),
                on_model_turn_event=lambda event: events.append(dict(event)), turn_timeout=3600)
            parsed = extract_json(result.final_message); usage = usage_from_events(events)
            self.transport.response(request, usage=usage, response=parsed)
            return parsed, usage, ids
        except Exception as error:
            self.transport.response(request, usage=usage_from_events(events), response={}, error=f"{type(error).__name__}: {error}")
            raise
        finally: self.leave()

    def local_call(self, route: str, item: dict[str, Any], attempt: int) -> tuple[dict[str, Any], dict[str, int] | None]:
        model = route
        prompt = prompt_for([item])
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False,
                   "response_format": {"type": "json_object"}, "options": {"num_ctx": 32768}}
        request = self.transport.request(route=route, provider="ollama", question_ids=[item["id"]],
            stage="all_assigned", role="maintenance", attempt=attempt, payload=payload)
        self.enter(); self.model_calls += 1
        try:
            req = urllib.request.Request("http://127.0.0.1:11434/v1/chat/completions",
                data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3600) as response: body = json.load(response)
            parsed = extract_json(body["choices"][0]["message"]["content"])
            raw_usage = body.get("usage") or {}; usage = {"inputTokens": int(raw_usage.get("prompt_tokens", 0)), "outputTokens": int(raw_usage.get("completion_tokens", 0))}
            self.transport.response(request, usage=usage, response=parsed)
            return parsed, usage
        except Exception as error:
            self.transport.response(request, usage=None, response={}, error=f"{type(error).__name__}: {error}")
            raise
        finally: self.leave()

    def route_dir(self, route: str) -> Path: return self.artifacts / "routes" / route.replace(":", "-")

    def write_row(self, route: str, row: dict[str, Any]) -> None:
        path = self.route_dir(route) / "results" / f"{row['id']}.json"
        if path.exists():
            existing = json.loads(path.read_text())
            if existing.get("status") in TERMINAL: return
            raise RuntimeError(f"unreconciled in-flight row: {row['id']}")
        row["rowSha256"] = digest_json(row)
        atomic_json(path, row)

    def rows(self, route: str) -> list[dict[str, Any]]:
        root = self.route_dir(route) / "results"
        return [json.loads(path.read_text()) for path in sorted(root.glob("*.json"))] if root.exists() else []

    def run_baseline(self) -> None:
        route = "codex_only"; items = self.manifest["items"]
        for item in items:
            if item["id"] in HOLD_IDS: self.write_row(route, {"id": item["id"], "status": "hold", "calls": 0, "route": route})
        pending = [item for item in items if item["id"] not in HOLD_IDS and not any(row["id"] == item["id"] for row in self.rows(route))]
        for offset in range(0, len(pending), 5):
            batch = pending[offset:offset+5]
            if len(json.dumps(batch, ensure_ascii=False).encode()) > 120000: raise RuntimeError("audit batch bytes exceeded")
            parsed, usage, ids = self.codex_call(route, batch, "maintenance", 1)
            by_id = {row["id"]: row for row in parsed["results"]}
            for item in batch:
                self.write_row(route, {"id": item["id"], "route": route, "status": "completed",
                    "result": by_id[item["id"]], "usage": usage, "calls": 1, "runIds": ids})
        rows = self.rows(route)
        if len(rows) != 36 or any(row["status"] not in TERMINAL for row in rows): raise RuntimeError("baseline terminal closure failed")
        if any(row["status"] == "completed" and not row.get("usage") for row in rows): raise RuntimeError("baseline provider usage missing")
        self.seal(route)

    def run_local(self, route: str) -> None:
        items = self.manifest["items"]
        for item in items:
            if item["id"] in HOLD_IDS: self.write_row(route, {"id": item["id"], "status": "hold", "calls": 0, "route": route})
        generated: dict[str, Any] = {}
        for item in items:
            if item["id"] in HOLD_IDS or any(row["id"] == item["id"] for row in self.rows(route)): continue
            last = None
            for attempt in (1, 2):
                try:
                    parsed, usage = self.local_call(route, item, attempt); last = {"parsed": parsed, "usage": usage, "attempt": attempt}; break
                except Exception:
                    if attempt == 1: time.sleep(1)
            if last is None:
                self.write_row(route, {"id": item["id"], "route": route, "status": "availability_reject", "calls": 2}); continue
            generated[item["id"]] = last
        gen_items = [item for item in items if item["id"] in generated]
        accepted = 0; audited = 0
        for offset in range(0, len(gen_items), 5):
            batch = gen_items[offset:offset+5]
            candidates = {item["id"]: generated[item["id"]]["parsed"] for item in batch}
            audited_payload = {"questions": batch, "candidates": candidates}
            if len(json.dumps(audited_payload, ensure_ascii=False).encode()) > 120000: raise RuntimeError("audit batch bytes exceeded")
            parsed, audit_usage, ids = self.codex_call(route, batch, "audit", 1, candidates)
            by_id = {row["id"]: row for row in parsed["results"]}
            for item in batch:
                local_result = generated[item["id"]
                ]["parsed"]; audited_result = by_id[item["id"]]
                accept = local_result.get("results", [local_result])[0].get("selectedIndexes") == audited_result.get("selectedIndexes")
                accepted += int(accept); audited += 1
                self.write_row(route, {"id": item["id"], "route": route, "status": "completed",
                    "localResult": local_result, "auditResult": audited_result, "auditAccepted": accept,
                    "localUsage": generated[item["id"]]["usage"], "auditUsage": audit_usage,
                    "calls": generated[item["id"]]["attempt"] + 1, "runIds": ids})
            remaining = 34 - audited
            if accepted + remaining < 31:
                for item in gen_items[offset+5:]:
                    self.write_row(route, {"id": item["id"], "route": route, "status": "early_reject", "calls": generated[item["id"]]["attempt"], "reason": "overall threshold unreachable"})
                break
        rows = self.rows(route)
        if len(rows) != 36 or any(row["status"] not in TERMINAL for row in rows): raise RuntimeError(f"{route} terminal closure failed")
        self.seal(route)

    def seal(self, route: str) -> None:
        events = self.transport.events.read_bytes() if self.transport.events.exists() else b""
        atomic_json(self.route_dir(route) / "prompt-capture-seal.json", {"sha256": hashlib.sha256(events).hexdigest(), "sealedAt": utc_now()})
        atomic_json(self.route_dir(route) / "run-complete.json", {"route": route, "terminalRows": 36, "completedAt": utc_now()})
        rows = self.rows(route); atomic_json(self.route_dir(route) / "summary.json", {"route": route, "terminalRows": len(rows), "statuses": {status: sum(r["status"] == status for r in rows) for status in sorted(TERMINAL)}})

    def close(self) -> None:
        if self.codex is not None: self.codex.close()


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True); parser.add_argument("--stage-matrix", type=Path, required=True)
    parser.add_argument("--artifacts-dir", type=Path, required=True); parser.add_argument("--temp-root", type=Path, required=True)
    parser.add_argument("--routes", nargs="+", required=True); parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(); runner = Runner(args.repo_root.resolve(), args.manifest.resolve(), args.stage_matrix.resolve(), args.artifacts_dir.resolve(), args.temp_root.resolve())
    try:
        environment = runner.gate()
        if (runner.artifacts / "oracle-after-run.json").exists(): raise RuntimeError("oracle exists before route closure")
        runner.run_baseline()
        runner.run_local("qwen3:14b"); runner.run_local("qwen3.5:27b")
        atomic_json(runner.artifacts / "safety.json", {"modelCalls": runner.model_calls, "firestoreAttempts": 0,
            "publicationAttempts": 0, "productionPatchWrites": 0, "llmCallPeak": runner.peak, "problemProcessingPeak": runner.problem_peak,
            "auditBatchMaxQuestions": 5, "auditBatchMaxBytes": 120000})
        atomic_json(runner.artifacts / "run-marker.json", {"status": "routes_complete", "completedAt": utc_now(), **environment})
        return 0
    finally: runner.close()

if __name__ == "__main__": raise SystemExit(main())
