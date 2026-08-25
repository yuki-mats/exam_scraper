"""T015 sealed-baseline continuation runner (real Ollama + real Codex audit)."""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Any

from benchmark_contract import BUILDING, IMAGE_IDS, JUDO, LAW_IDS, stratum_for
from capture_transport import CaptureTransport, canonical_bytes, digest_json, utc_now

HOLD_IDS = set(JUDO["source-answer-missing"])
TERMINAL = {"completed", "hold", "availability_reject", "unsupported_modality", "skipped_early_rejection"}
FORBIDDEN_PROMPT_KEYS = ("correctChoiceText", "answerTableCorrectChoiceNumbers", "choiceClassCorrectChoiceNumbers",
                         "answer_result_text", "existingReview", "priorModelResult", "oracle-after-run")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.replace(temp, path)


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def extract_json(text: str) -> Any:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char in "[{":
            try:
                return decoder.raw_decode(text[index:])[0]
            except json.JSONDecodeError:
                continue
    raise ValueError("provider response did not contain JSON")


def result_schema() -> dict[str, Any]:
    item = {"type": "object", "additionalProperties": False,
            "required": ["id", "selectedIndexes", "explanations", "questionType"],
            "properties": {"id": {"type": "string"},
                           "selectedIndexes": {"type": "array", "items": {"type": "integer"}},
                           "explanations": {"type": "array", "items": {"type": "string"}},
                           "questionType": {"type": "string"}}}
    return {"type": "object", "additionalProperties": False, "required": ["results"],
            "properties": {"results": {"type": "array", "items": item}}}


def audit_schema() -> dict[str, Any]:
    corrected = result_schema()["properties"]["results"]["items"]
    item = {"type": "object", "additionalProperties": False,
            "required": ["id", "acceptedTargets", "correctedResult", "issues", "criticalFlags",
                         "examTimeBasis", "imageReviewed", "calculationReviewed"],
            "properties": {"id": {"type": "string"},
                           "acceptedTargets": {"type": "array", "items": {"type": "string"}},
                           "correctedResult": corrected,
                           "issues": {"type": "array", "items": {"type": "string"}},
                           "criticalFlags": {"type": "array", "items": {"type": "string"}},
                           "examTimeBasis": {"type": "string"},
                           "imageReviewed": {"type": "boolean"},
                           "calculationReviewed": {"type": "boolean"}}}
    return {"type": "object", "additionalProperties": False, "required": ["results"],
            "properties": {"results": {"type": "array", "items": item}}}


def generation_prompt(items: list[dict[str, Any]]) -> str:
    text = ("資格試験問題を盲検で整備してください。各問について正しい選択肢番号（1始まり・複数可）、"
            "全選択肢それぞれの簡潔な根拠、questionTypeを指定JSONで返してください。正答資料はありません。\n" +
            json.dumps({"questions": items}, ensure_ascii=False, sort_keys=True))
    reject_prompt_leakage(text)
    return text


def audit_prompt(items: list[dict[str, Any]], candidates: dict[str, Any], law: list[dict[str, Any]]) -> str:
    text = ("候補を独立監査してください。候補を信用せず、各問の全assignedTargetsを検査します。受理できるtargetだけ"
            "acceptedTargetsへ入れ、受理したtargetはcorrectedResultで候補値を変更しないでください。修正したtargetは受理しません。"
            "正答、法令時点、全選択肢根拠、問題形式、画像、計算を確認し、法令時点混同、医学的捏造、画像未確認、"
            "計算破綻をcriticalFlagsへ記録してください。\n" +
            json.dumps({"questions": items, "candidates": candidates,
                        "officialLawProvenanceWithoutAnswers": law}, ensure_ascii=False, sort_keys=True))
    reject_prompt_leakage(text)
    return text


def reject_prompt_leakage(text: str) -> None:
    found = [key for key in FORBIDDEN_PROMPT_KEYS if key in text]
    if found:
        raise RuntimeError(f"oracle-derived prompt fields: {found}")


class ContinuationRunner:
    def __init__(self, *, repo: Path, manifest: Path, artifacts: Path, continuation: Path,
                 temp: Path, codex_client_factory: Any) -> None:
        self.repo, self.artifacts, self.continuation, self.temp = repo, artifacts, continuation, temp
        self.manifest = json.loads(manifest.read_text())
        self.items = self.manifest["items"]
        self.by_id = {item["id"]: item for item in self.items}
        self.codex_client_factory = codex_client_factory
        self.codex = None
        self.active = self.peak = self.problem_peak = 0
        self.model_calls = self.local_calls = self.codex_calls = 0
        self.audit_max_questions = self.audit_max_bytes = 0
        self.continuation.mkdir(parents=True, exist_ok=True)

    def route_name(self, route: str) -> str:
        return route.replace(":", "-")

    def route_dir(self, route: str) -> Path:
        return self.artifacts / "routes" / self.route_name(route)

    def transport(self, route: str) -> CaptureTransport:
        return CaptureTransport(self.continuation, self.route_name(route) + "-transport.jsonl")

    def rows(self, route: str) -> list[dict[str, Any]]:
        root = self.route_dir(route) / "results"
        return [json.loads(path.read_text()) for path in sorted(root.glob("*.json"))] if root.exists() else []

    def write_row(self, route: str, row: dict[str, Any]) -> None:
        path = self.route_dir(route) / "results" / f"{row['id']}.json"
        if path.exists():
            existing = json.loads(path.read_text())
            if existing.get("status") in TERMINAL:
                return
            raise RuntimeError(f"unreconciled in-flight row: {row['id']}")
        row["rowSha256"] = digest_json(row)
        atomic_json(path, row)

    def enter(self) -> None:
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.problem_peak = max(self.problem_peak, self.active)
        if self.active > 1:
            raise RuntimeError("global concurrency exceeded one")

    def leave(self) -> None:
        self.active -= 1

    def pre_run(self) -> None:
        self.readback_baseline()
        if (self.artifacts / "oracle-after-run.json").exists():
            raise RuntimeError("oracle exists before local route closure")
        image_report = self.verify_images()
        tags = json.load(urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=10))
        models = {row["name"] for row in tags.get("models", [])}
        if not {"qwen3:14b", "qwen3.5:27b"} <= models:
            raise RuntimeError("approved Ollama models unavailable")
        self.codex = self.codex_client_factory(self.repo, turn_timeout=3600)
        codex_status = self.codex.diagnose_subscription_access()
        if not codex_status.get("allowed"):
            raise RuntimeError(f"Codex audit unavailable: {codex_status.get('failureKind')}")
        atomic_json(self.continuation / "environment.json", {"checkedAt": utc_now(), "ollamaModels": sorted(models),
                    "codex": codex_status, "images": image_report, "globalParallelism": 1,
                    "sanitizedItemKeys": sorted(self.items[0]), "itemCount": len(self.items)})

    def readback_baseline(self) -> None:
        route = self.route_dir("codex_only")
        rows = self.rows("codex_only")
        capture = self.artifacts / "prompt-captures" / "transport-events.jsonl"
        events = [json.loads(line) for line in capture.read_text().splitlines()]
        requests = [row for row in events if row["kind"] == "request"]
        responses = [row for row in events if row["kind"] == "response"]
        if (len(rows), sum(x["status"] == "completed" for x in rows), sum(x["status"] == "hold" for x in rows)) != (36, 34, 2):
            raise RuntimeError("sealed baseline terminal mismatch")
        for row in rows:
            value = dict(row); expected = value.pop("rowSha256", None)
            if digest_json(value) != expected:
                raise RuntimeError("sealed baseline row hash mismatch")
        if len(requests) != 7 or len(responses) != 7 or any(x.get("usage") is not None for x in responses):
            raise RuntimeError("sealed baseline call/usage mismatch")
        seal = json.loads((route / "prompt-capture-seal.json").read_text())
        if hashlib.sha256(capture.read_bytes()).hexdigest() != seal["sha256"]:
            raise RuntimeError("sealed baseline capture mismatch")
        fixed = [route / "run-complete.json", route / "summary.json", self.artifacts / "receipt.json",
                 self.artifacts / "blocked.json", self.artifacts / "safety.json", self.artifacts / "environment.json",
                 self.artifacts / "input-snapshot.json"]
        atomic_json(self.continuation / "sealed-baseline-readback.json", {"terminalRows": 36, "completed": 34,
                    "hold": 2, "codexCalls": 7, "usageMissing": 7, "captureSha256": seal["sha256"],
                    "sealedFileSha256": {str(path.relative_to(self.artifacts)): hashlib.sha256(path.read_bytes()).hexdigest() for path in fixed}})
        # Reconstruct with the exact T013 schema/prompt contract, then record bytes only after both hashes match.
        from run_benchmark import prompt_for, schema
        results = {row["id"]: row["result"] for row in rows if row["status"] == "completed"}
        attempts = []
        for request, response in zip(requests, responses):
            payload = {"prompt": prompt_for([self.by_id[qid] for qid in request["questionIds"]]), "outputSchema": schema()}
            parsed = {"results": [results[qid] for qid in request["questionIds"]]}
            if digest_json(payload) != request["payloadSha256"] or digest_json(parsed) != response["responseSha256"]:
                raise RuntimeError("baseline byte reconstruction hash mismatch")
            attempts.append({"questionIds": request["questionIds"], "promptUtf8Bytes": len(payload["prompt"].encode()),
                             "canonicalRequestBytes": canonical_bytes(payload), "canonicalResponseBytes": canonical_bytes(parsed),
                             "verified": True})
        atomic_json(self.continuation / "baseline-byte-readback.json", {"metric": "application-level observed/verified bytes",
                    "tokenSubstitute": False, "attempts": attempts})

    def verify_images(self) -> list[dict[str, Any]]:
        approved = json.loads((self.artifacts.parent / "approved-image-assets.json").read_text())["assets"]
        report = []
        for asset in approved:
            request = urllib.request.Request(asset["url"], headers={"User-Agent": "T015-benchmark/1"})
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read(); mime = response.headers.get_content_type(); final_host = urllib.parse.urlparse(response.url).hostname
            valid = (final_host == "firebasestorage.googleapis.com" and mime == asset["mime"] and len(body) == asset["bytes"]
                     and hashlib.sha256(body).hexdigest() == asset["sha256"])
            if not valid:
                raise RuntimeError(f"image verification failed: {asset['id']} {asset['role']}")
            target = self.temp / "images" / (asset["sha256"] + Path(urllib.parse.urlparse(asset["url"]).path).suffix)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            report.append({"id": asset["id"], "role": asset["role"], "sha256": asset["sha256"],
                           "bytes": len(body), "mime": mime, "decoder": asset["decoder"], "verified": True})
        return report

    def local_call(self, route: str, item: dict[str, Any], attempt: int) -> tuple[dict[str, Any], dict[str, int]]:
        prompt = generation_prompt([item])
        payload = {"model": route, "messages": [{"role": "user", "content": prompt}], "stream": False,
                   "response_format": {"type": "json_object"}, "options": {"num_ctx": 32768}}
        transport = self.transport(route)
        request_row = transport.request(route=route, provider="ollama", question_ids=[item["id"]], stage="all_assigned",
                                        role="local_primary" if attempt == 1 else "local_retry", attempt=attempt, payload=payload)
        self.enter(); self.model_calls += 1; self.local_calls += 1
        try:
            request = urllib.request.Request("http://127.0.0.1:11434/v1/chat/completions", data=json.dumps(payload).encode(),
                                             headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=3600) as response:
                body = json.load(response)
            parsed = extract_json(body["choices"][0]["message"]["content"])
            raw = body.get("usage") or {}
            usage = {"inputTokens": int(raw.get("prompt_tokens", 0)), "outputTokens": int(raw.get("completion_tokens", 0))}
            transport.response(request_row, usage=usage, response=parsed)
            return parsed, usage
        except Exception as error:
            transport.response(request_row, usage=None, response={}, error=f"{type(error).__name__}: {error}")
            raise
        finally:
            self.leave()

    def audit_call(self, route: str, batch: list[dict[str, Any]], candidates: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        law_file = json.loads((self.artifacts.parent / "law-provenance.json").read_text())
        law = [row for row in law_file["items"] if row["id"] in {item["id"] for item in batch}]
        prompt = audit_prompt(batch, candidates, law)
        payload = {"prompt": prompt, "outputSchema": audit_schema()}
        size = canonical_bytes(payload)
        if len(batch) > 5 or size > 120000:
            raise RuntimeError("audit batch limit exceeded")
        self.audit_max_questions = max(self.audit_max_questions, len(batch)); self.audit_max_bytes = max(self.audit_max_bytes, size)
        transport = self.transport(route)
        request_row = transport.request(route=route, provider="codex_app_server", question_ids=[x["id"] for x in batch],
                                        stage="all_assigned", role="audit", attempt=1, payload=payload)
        ids: dict[str, str] = {}; self.enter(); self.model_calls += 1; self.codex_calls += 1
        try:
            result = self.codex.run_turn(prompt, work_type="question_maintenance_benchmark", sandbox="read-only",
                                         emit=lambda _line: None, output_schema=audit_schema(), cwd=self.temp,
                                         on_thread_started=lambda thread, session: ids.update(threadId=thread, sessionId=session),
                                         on_turn_started=lambda thread, turn: ids.update(threadId=thread, turnId=turn), turn_timeout=3600)
            parsed = extract_json(result.final_message)
            transport.response(request_row, usage=None, response=parsed)
            return parsed, ids
        except Exception as error:
            transport.response(request_row, usage=None, response={}, error=f"{type(error).__name__}: {error}")
            raise
        finally:
            self.leave()

    def candidate_result(self, parsed: dict[str, Any], item_id: str) -> dict[str, Any]:
        found = [row for row in parsed.get("results", []) if row.get("id") == item_id]
        if len(found) != 1:
            raise ValueError("local response missing or duplicated id")
        return found[0]

    def accepted(self, item: dict[str, Any], candidate: dict[str, Any], audit: dict[str, Any], primary: bool) -> bool:
        targets = set(item["targets"])
        corrected = audit.get("correctedResult") or {}
        same = True
        if "correct_choice" in targets:
            same &= sorted(candidate.get("selectedIndexes", [])) == sorted(corrected.get("selectedIndexes", []))
        if "question_type" in targets:
            same &= candidate.get("questionType") == corrected.get("questionType")
        if "explanation" in targets:
            same &= (candidate.get("explanations") == corrected.get("explanations")
                     and len(candidate.get("explanations", [])) == len(item["choices"]))
        if targets & {"law_context", "law_audit"}:
            same &= bool(audit.get("examTimeBasis"))
        return bool(primary and same and targets <= set(audit.get("acceptedTargets", [])) and not audit.get("criticalFlags"))

    def run_route(self, route: str) -> None:
        prior = {row["id"] for row in self.rows(route) if row.get("status") in TERMINAL}
        for item in self.items:
            if item["id"] in HOLD_IDS and item["id"] not in prior:
                self.write_row(route, {"id": item["id"], "route": route, "status": "hold", "reason": "source_answer_missing",
                                       "localCalls": 0, "codexCalls": 0})
        pending = [item for item in self.items if item["id"] not in HOLD_IDS and item["id"] not in prior]
        critical_stop = False
        for offset in range(0, len(pending), 5):
            batch = pending[offset:offset + 5]
            if critical_stop:
                for item in batch:
                    self.write_row(route, {"id": item["id"], "route": route, "status": "skipped_early_rejection",
                                           "reason": "critical_error_in_prior_batch", "localCalls": 0, "codexCalls": 0})
                continue
            generated: dict[str, dict[str, Any]] = {}; meta: dict[str, dict[str, Any]] = {}
            for item in batch:
                if item["id"] in IMAGE_IDS:
                    self.write_row(route, {"id": item["id"], "route": route, "status": "unsupported_modality",
                                           "reason": "approved Ollama model has no vision capability", "imageReviewed": False,
                                           "localCalls": 0, "codexCalls": 0})
                    continue
                last_error = None
                for attempt in (1, 2):
                    try:
                        parsed, usage = self.local_call(route, item, attempt)
                        generated[item["id"]] = self.candidate_result(parsed, item["id"])
                        meta[item["id"]] = {"attempt": attempt, "usage": usage}
                        break
                    except Exception as error:
                        last_error = f"{type(error).__name__}: {error}"
                        if attempt == 1:
                            time.sleep(1)
                if item["id"] not in generated:
                    self.write_row(route, {"id": item["id"], "route": route, "status": "availability_reject",
                                           "reason": last_error, "localCalls": 2, "codexCalls": 0})
            audit_items = [item for item in batch if item["id"] in generated]
            if not audit_items:
                continue
            candidates = {item["id"]: generated[item["id"]] for item in audit_items}
            try:
                parsed, ids = self.audit_call(route, audit_items, candidates)
            except Exception:
                # One clean Codex client restart and one retry; never replay a resolved batch.
                self.codex.close(); self.codex = self.codex_client_factory(self.repo, turn_timeout=3600)
                parsed, ids = self.audit_call(route, audit_items, candidates)
            audits = {row["id"]: row for row in parsed.get("results", [])}
            if set(audits) != set(candidates):
                raise RuntimeError("audit response missing or duplicated reservation")
            for item in audit_items:
                candidate = candidates[item["id"]]; audit = audits[item["id"]]
                primary = meta[item["id"]]["attempt"] == 1
                accepted = self.accepted(item, candidate, audit, primary)
                critical = list(audit.get("criticalFlags", []))
                critical_stop |= bool(critical)
                self.write_row(route, {"id": item["id"], "route": route, "status": "completed",
                    "localResult": candidate, "auditResult": audit, "auditAccepted": accepted,
                    "localAttemptMode": "local_primary" if primary else "local_retry", "localUsage": meta[item["id"]]["usage"],
                    "localCalls": meta[item["id"]]["attempt"], "codexCalls": 1, "runIds": ids})
        rows = self.rows(route)
        if len(rows) != 36 or any(row["status"] not in TERMINAL for row in rows):
            raise RuntimeError(f"{route} terminal closure failed")
        capture = self.transport(route).events
        atomic_json(self.route_dir(route) / "prompt-capture-seal.json", {"sha256": hashlib.sha256(capture.read_bytes()).hexdigest(),
                    "capture": str(capture.relative_to(self.artifacts)), "sealedAt": utc_now()})
        atomic_json(self.route_dir(route) / "run-complete.json", {"route": route, "terminalRows": 36, "completedAt": utc_now()})
        summary = {"route": route, "terminalRows": 36,
                   "statuses": {status: sum(row["status"] == status for row in rows) for status in sorted(TERMINAL)},
                   "localPrimaryAccepted": sum(row.get("auditAccepted") is True for row in rows)}
        atomic_json(self.route_dir(route) / "summary.json", summary)
        atomic_json(self.continuation / (self.route_name(route) + "-summary.json"), summary)
        append_jsonl(self.continuation / "events.jsonl", {"event": "route_terminal", "route": route, "at": utc_now(), **summary})

    def finish(self) -> None:
        atomic_json(self.artifacts / "final-safety.json", {"modelCalls": self.model_calls, "codexCalls": self.codex_calls,
                    "ollamaCalls": self.local_calls, "firestoreAttempts": 0, "publicationAttempts": 0,
                    "productionPatchWrites": 0, "llmCallPeak": self.peak, "problemProcessingPeak": self.problem_peak,
                    "auditBatchMaxQuestions": self.audit_max_questions, "auditBatchMaxBytes": self.audit_max_bytes})

    def close(self) -> None:
        if self.codex is not None:
            self.codex.close()
