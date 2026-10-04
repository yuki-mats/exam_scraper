#!/usr/bin/env python3
"""Read-only independent content audit; never promotes publication evaluation."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sys
import threading

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.question_review_console.codex_app_server import (
    CodexAppServerClient, QUESTION_MAINTENANCE_AUDIT_MODEL, SubscriptionGateError,
)
from tools.question_review_console.evaluation import (
    EvaluationError, EvaluationStore, QuestionEvaluationService,
    _image_input_receipt, _image_receipt_valid, _json_hash,
)
from tools.question_review_console.inventory import QuestionInventory
from tools.question_review_console.model_backend import (
    ProfileModelRouter, load_model_backend_config,
)


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def load_scope(root, run_id):
    if Path(run_id).name != run_id:
        raise ValueError("source run IDにpathは指定できません。")
    directory = root / "output/question_review_console/workflow_runs/sc" / run_id
    manifest = json.loads((directory / "manifest.json").read_text())
    if (manifest.get("qualification") != "sc" or not manifest.get("receiptValidated")
            or manifest.get("status") != "succeeded"):
        raise ValueError("検証済みSC runが必要です。")
    states = [json.loads(p.read_text()) for p in sorted((directory / "questions").glob("*.json"))]
    executions = [q["execution"] for q in states]
    ids = [e["questionId"] for e in executions]
    if not ids or len(ids) != len(set(ids)) or len(ids) != manifest["targetCount"]:
        raise ValueError("元runの対象集合が不完全又は重複しています。")
    inv = QuestionInventory(root)
    questions, hashes = [], {}
    groups = sorted({e["listGroupId"] for e in executions})
    with inv.projection_snapshot("sc", groups):
        for e in executions:
            projection = inv.projected_input("sc", e["listGroupId"], e["sourceRecordRef"])
            if projection.errors:
                raise ValueError(f"{e['questionId']}: {projection.errors}")
            source = inv.source_input("sc", e["listGroupId"], e["sourceRecordRef"])
            question = {
                "id": e["questionId"], "reviewKey": e["questionKey"],
                "qualification": "sc", "listGroupId": e["listGroupId"],
                "sourceRecordRef": e["sourceRecordRef"], "source": source,
                "originalQuestionId": e["reviewQuestionId"],
                "questionLabel": projection.record.get("examLabel"),
                "projected": projection.record, "stateHash": _json_hash(projection.record),
                "choiceCount": len(projection.record.get("choiceTextList") or []),
            }
            questions.append(question)
            source_path = root / "output/sc/questions_json" / e["listGroupId"] / "00_source" / e["sourceRecordRef"].split("#")[0]
            for path in [source_path, *(root / p for p in projection.applied_files)]:
                hashes[str(path.relative_to(root))] = digest(path)
    return questions, hashes


class ContentAuditService(QuestionEvaluationService):
    def __init__(self, root, **kwargs):
        super().__init__(root, secrets.token_urlsafe(32), **kwargs)
        self.explanation_policy = (root / "prompt/03_prompt_add_explanationText.md").read_text()
        self.frozen_prompts = {}

    def _build_batch_prompt(self, questions):
        key = tuple(q["id"] for q in questions)
        if key in self.frozen_prompts:
            return self.frozen_prompts[key]
        return (
            "# 公開準備前の独立内容評価\n"
            "評価対象は問題文・全選択肢・正答対応・解説・画像です。"
            "問題集割当や公開用成果物の生成はこの内容評価の対象外です。"
            "公開承認やfield更新を行わず、以下の既存評価schemaで判定してください。\n\n"
            "## 解説方針の正本\n" + self.explanation_policy + "\n\n"
            + super()._build_batch_prompt(questions)
        )


def validated_content_result(question, worker, metadata):
    if not all(metadata.get(k) for k in ("threadId", "sessionId", "turnId", "model")):
        raise EvaluationError("真正の評価session証拠がありません。")
    if metadata["model"] != QUESTION_MAINTENANCE_AUDIT_MODEL:
        raise EvaluationError("監査用modelと実行証拠が一致しません。")
    result = EvaluationStore._validate_result(question, worker)
    receipt = _image_input_receipt(question, metadata)
    if not _image_receipt_valid(question, receipt):
        raise EvaluationError("宣言画像の送信bytes証拠が不足しています。")
    return {
        **result, "questionId": question["id"], "stateHash": question["stateHash"],
        "originalQuestionId": question["originalQuestionId"],
        "questionLabel": question["questionLabel"], "scope": "content_only",
        "publicationReady": False, "publicationEvaluationPromoted": False,
        "imageInputReceipt": receipt, "sessionEvidence": metadata,
        "evaluatedAt": now(),
    }


def run(root, source_run, destination, concurrency=20, resume=False):
    if destination.exists() and not resume:
        raise ValueError("既存runへは--resumeを明示してください。")
    questions, protected = load_scope(root, source_run)
    config = load_model_backend_config(root / "config/question_maintenance_llm.toml")
    client = CodexAppServerClient(root)
    router = ProfileModelRouter(config, client)
    policy_paths = ["prompt/01_prompt_fix_questionType.md", "prompt/03_prompt_add_explanationText.md",
                    "config/question_maintenance_workflow.toml", "tools/question_review_console/evaluation.py",
                    "tools/question_review_console/evaluation_result.schema.json"]
    policy_hashes = {p: digest(root / p) for p in policy_paths}
    service = ContentAuditService(root, app_server=router)
    if hashlib.sha256(service.explanation_policy.encode()).hexdigest() != policy_hashes[policy_paths[1]]:
        raise ValueError("準備中に03正本が変わりました。")
    binding = {"sourceRunId": source_run, "questionStates": {q["id"]: q["stateHash"] for q in questions},
               "protectedInputHashes": protected, "policyHashes": policy_hashes,
               "modelProfile": router.snapshot_for("codex_only")}
    binding_hash = _json_hash(binding)
    manifest_path = destination / "manifest.json"
    if resume:
        previous = json.loads(manifest_path.read_text())
        if previous["inputBindingHash"] != binding_hash:
            raise ValueError("再開時に入力又は評価policyが変わりました。新しいrunで評価してください。")
    results = {}
    by_id = {q["id"]: q for q in questions}
    for path in (destination / "questions").glob("*.json"):
        value = json.loads(path.read_text())
        if value.get("status") in {"passed", "needs_rework"}:
            question_id = value["questionId"]
            if (question_id not in by_id or path.stem != question_id
                    or value.get("stateHash") != by_id[question_id]["stateHash"]
                    or value.get("scope") != "content_only"
                    or value.get("publicationReady") is not False):
                raise ValueError("保存済み評価の入力identityが一致しません。")
            validated_content_result(by_id[question_id], value, value.get("sessionEvidence", {}))
            results[value["questionId"]] = value
    manifest = {
        "schemaVersion": "independent-content-evaluation/v1", "scope": "content_only",
        "inputBindingHash": binding_hash, "inputs": binding, "startedAt": now(),
        "targetCount": len(questions), "concurrency": concurrency,
        "publicationEvaluationPromoted": False, "firestoreWritten": False,
        "existingCreditsApproved": False, "status": "running",
    }
    lock = threading.RLock()
    subscription_stopped = threading.Event()

    def persist():
        counts = Counter(r["status"] for r in results.values())
        manifest.update(completedCount=len(results), statusCounts=dict(counts), updatedAt=now())
        write_json(manifest_path, manifest)

    def emit(message):
        with lock:
            with (destination / "progress.jsonl").open("a") as stream:
                stream.write(json.dumps({"at": now(), "message": str(message)}, ensure_ascii=False) + "\n")

    def evaluate_batch(batch):
        batch_questions = [by_id[item["questionId"]] for _, item in batch]
        if subscription_stopped.is_set():
            return [(q, {"status": "failed", "errorType": "SubscriptionGateError",
                         "error": "subscription gateの停止後は開始しません。"}) for q in batch_questions]
        attempt = secrets.token_hex(8)
        attempt_dir = destination / "attempts" / attempt
        write_json(attempt_dir / "input.json", batch_questions)
        (attempt_dir / "prompt.md").write_text(service._build_batch_prompt(batch_questions))
        try:
            workers, metadata = service._run_batch_result(batch_questions, emit)
            write_json(attempt_dir / "returned.json", {"workers": workers, "metadata": metadata})
            evaluated = []
            for q in batch_questions:
                if q["id"] not in workers:
                    evaluated.append((q, {"status": "failed", "error": "schema又は問題ID/stateHashの検証不合格"}))
                    continue
                try:
                    value = validated_content_result(q, workers[q["id"]], metadata)
                except EvaluationError as exc:
                    value = {"status": "failed", "error": str(exc)}
                evaluated.append((q, value))
            return evaluated
        except Exception as exc:
            if isinstance(exc, SubscriptionGateError):
                subscription_stopped.set()
            write_json(attempt_dir / "failure.json", {"errorType": type(exc).__name__, "error": str(exc)})
            return [(q, {"status": "failed", "errorType": type(exc).__name__, "error": str(exc)}) for q in batch_questions]

    persist()
    try:
        router.assert_profile_access("codex_only", force=True, allow_existing_credits=False)
        pending = [{"questionId": q["id"]} for q in questions if q["id"] not in results]
        # Grouping avoids interleaving image/law conditions while retaining exact identities.
        pending.sort(key=lambda item: (bool(by_id[item["questionId"]]["projected"].get("isLawRelated")),
                                      bool(by_id[item["questionId"]]["projected"].get("questionImageStorageUrls")
                                           or any(by_id[item["questionId"]]["projected"].get("originalQuestionChoiceImageUrls") or []))))
        batches = service._audit_batches(pending, by_id)
        for batch in batches:
            batch_questions = [by_id[item["questionId"]] for _, item in batch]
            key = tuple(q["id"] for q in batch_questions)
            service.frozen_prompts[key] = service._build_batch_prompt(batch_questions)
        if any(digest(root / p) != h for p, h in policy_hashes.items()):
            raise ValueError("準備中に評価policyが変わりました。model開始前に停止します。")
        manifest["batchCount"] = len(batches)
        persist()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(evaluate_batch, batch) for batch in batches]
            for future in as_completed(futures):
                for q, value in future.result():
                    value.update(questionId=q["id"], stateHash=q["stateHash"], scope="content_only",
                                 publicationReady=False, publicationEvaluationPromoted=False)
                    with lock:
                        write_json(destination / "questions" / (q["id"] + ".json"), value)
                        results[q["id"]] = value
                        persist()
                print(f"{len(results)}/{len(questions)} {dict(Counter(r['status'] for r in results.values()))}", flush=True)
        changed = [p for p, h in protected.items() if digest(root / p) != h]
        policy_changed = [p for p, h in policy_hashes.items() if digest(root / p) != h]
        manifest.update(protectedInputsUnchanged=not changed, changedProtectedPaths=changed,
                        policyUnchanged=not policy_changed, changedPolicyPaths=policy_changed,
                        finishedAt=now(), status="completed" if not changed and not policy_changed
                        and all(r["status"] in {"passed", "needs_rework"} for r in results.values()) else "needs_followup")
        persist()
    finally:
        client.close()
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    destination = args.output.resolve()
    if not destination.is_relative_to(ROOT / "output/sc/reports/content_evaluations"):
        parser.error("出力先はoutput/sc/reports/content_evaluations内に限定してください。")
    if not 1 <= args.concurrency <= 100:
        parser.error("監査batch並列数は1〜100です。")
    manifest = run(ROOT, args.source_run, destination, args.concurrency, args.resume)
    print(json.dumps({k: manifest[k] for k in ("status", "completedCount", "statusCounts")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
