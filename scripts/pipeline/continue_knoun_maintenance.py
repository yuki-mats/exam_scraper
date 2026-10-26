"""Knounの画像照合、source登録、整備、独立評価を逐次実行する。公開は行わない。"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from uuid import uuid4

import requests

from scripts.common.question_identity import load_source_record_inventory
from scripts.scrape.common import load_local_secure_env
from scripts.scrape.knoun import import_group, source_record
from scripts.scrape.knoun_images import BOOK_ID, REPO_ROOT, atomic_json, now, read_manifest
from scripts.scrape.knoun_visual_review import wait_for_console_idle


class Console:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.session = requests.Session()

    def get(self, path, **params):
        response = self.session.get(self.base + path, params=params, timeout=60)
        response.raise_for_status()
        return response.json()

    def post(self, path, value):
        # 認証値を成果物又は標準出力へ保存しない。
        token = self.get("/api/session")["sessionToken"]
        response = self.session.post(self.base + path, json=value,
                                     headers={"X-Review-Session": token, "Origin": self.base}, timeout=120)
        response.raise_for_status()
        return response.json()

    def questions(self, group):
        result = self.get("/api/questions", qualification="eco-kentei", listGroupId=group, limit=100)
        if result.get("hasMore"):
            raise ValueError("Knoun一組の問題一覧を全件取得できませんでした")
        return result["questions"]

    def wait_run(self, run_id):
        previous = None
        while True:
            progress = self.get(f"/api/qualification-runs/{run_id}/progress", qualification="eco-kentei")
            marker = (progress.get("completedQuestionCount"), progress.get("blockedQuestionCount"), progress.get("executionPhase"))
            if marker != previous:
                print(f"[MAINTENANCE] {marker}", flush=True)
                previous = marker
            if progress.get("status") not in ("running", "pending", "queued"):
                return progress
            time.sleep(10)

    def start_run(self, request):
        preview = self.post("/api/qualification-runs/preview", request)
        if not preview.get("canStart"):
            return {"status": "not_started", "preview": preview}
        if preview.get("isProductionWrite") or preview.get("questionConcurrency") != 1:
            raise ValueError("逐次・ローカル整備の実行境界が一致しません")
        started = self.post("/api/qualification-runs/start", request | {"previewToken": preview["previewToken"]})
        return self.wait_run(started["run"]["runId"])

    def evaluate(self, question_id):
        preview = self.post(f"/api/questions/{question_id}/evaluation-preview", {})
        if not preview.get("canEvaluate"):
            return {"status": "not_started", "preview": preview}
        job = self.post(f"/api/questions/{question_id}/evaluation", {"previewToken": preview["previewToken"]})
        deadline = time.monotonic() + 2400
        while time.monotonic() < deadline:
            result = self.get("/api/jobs/" + job["jobId"])
            if result["status"] not in ("running", "pending", "queued"):
                return result
            time.sleep(10)
        raise TimeoutError("独立評価の終了を確認できませんでした")


def eligible_sources(root: Path, group: int):
    manifest = read_manifest(root / "question_images" / f"knoun-2026-mock-{group}" / "capture_manifest.json", group, BOOK_ID)
    eligible, holds = [], []
    for source_id in manifest["records"]:
        try:
            candidate = {"question_bodies": [
                source_record(root, group, source_id, verified_manifest=manifest)
            ]}
            existing = (root / "questions_json" / f"knoun-2026-mock-{group}"
                        / "00_source" / f"question_{Path(source_id).stem}.json")
            if existing.exists() and json.loads(existing.read_text(encoding="utf-8")) != candidate:
                raise ValueError(
                    "登録済み00_sourceと現在の転記に差異があります。"
                    "sourceを保持し、差異を個別に確認してください"
                )
            eligible.append(source_id)
        except (ValueError, KeyError, FileNotFoundError) as exc:
            holds.append({"sourceQuestionId": source_id, "reason": str(exc)})
    return eligible, holds


def current_bindings(questions, inventory):
    by_pair = {(e.identity.binding.source_question_key, e.identity.binding.source_record_ref): e for e in inventory}
    result = []
    for question in questions:
        if not question.get("workVersions", {}).get("allCurrent"):
            continue
        key = (question["sourceQuestionKey"], question["sourceRecordRef"])
        if key not in by_pair:
            raise ValueError("整備済み問題と固定source identityの対応が一致しません")
        result.append(by_pair[key].identity.binding.as_mapping())
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--console-url", required=True)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    if not args.run:
        print("原画像照合 → 対応を検証したsource登録 → 既存整備 → 限定merge/convert/dry-run → 一問ずつ独立評価。全工程同時処理1。")
        return 0
    root = REPO_ROOT / "output/eco-kentei"
    run_dir = root / "reports" / ("continuation_" + uuid4().hex)
    run_dir.mkdir(parents=True)
    report = {"schemaVersion": "knoun-maintenance-continuation/v1", "startedAt": now(),
              "status": "running", "concurrency": 1, "publication": "not_requested", "sourceHolds": [], "evaluations": []}
    def save(phase):
        report.update(phase=phase, heartbeatAt=now())
        atomic_json(run_dir / "progress.json", report)
        print(f"[PHASE] {phase}", flush=True)
    def command(arguments):
        subprocess.run([sys.executable, *arguments], cwd=REPO_ROOT, check=True)
    console = Console(args.console_url)
    try:
        save("waiting_for_existing_work")
        wait_for_console_idle(args.console_url)
        save("visual_transcription")
        visual = subprocess.run([sys.executable, "-m", "scripts.scrape.knoun_visual_review", "--run",
                                 "--wait-console-url", args.console_url], cwd=REPO_ROOT)
        if visual.returncode not in (0, 1):
            raise RuntimeError("画像照合処理が中断しました")
        # 中断時のexit=1と、全対象を確認して個別保留が残ったexit=1を区別する。
        latest = max((root / "reports").glob("knoun_visual_review_*.json"), key=lambda p: p.stat().st_mtime_ns)
        visual_report = json.loads(latest.read_text())
        report["visualReviewReport"] = str(latest.relative_to(REPO_ROOT))
        if visual_report["status"] not in ("selected_scope_complete", "finished_with_holds"):
            raise RuntimeError("画像照合の全対象処理が未完了です")
        save("figure_source_layout")
        layout_run = subprocess.run([sys.executable, "-m", "scripts.scrape.knoun_source_layout", "--run",
                                     "--wait-console-url", args.console_url], cwd=REPO_ROOT)
        if layout_run.returncode not in (0, 1):
            raise RuntimeError("図表を含む問題の領域照合が中断しました")
        layout_report_path = max((root / "reports").glob("source_layout_*.json"), key=lambda p: p.stat().st_mtime_ns)
        layout_report = json.loads(layout_report_path.read_text())
        report["sourceLayoutReport"] = str(layout_report_path.relative_to(REPO_ROOT))
        if layout_report.get("status") not in ("selected_scope_complete", "finished_with_holds"):
            raise RuntimeError("図表の領域照合の全対象処理が未完了です")
        load_local_secure_env()
        save("source_intake")
        groups = []
        report["sourceIntake"] = []
        for group in range(1, 6):
            ids, holds = eligible_sources(root, group)
            report["sourceHolds"].extend(holds)
            if not ids:
                continue
            group_id = f"knoun-2026-mock-{group}"
            scope = f"output/eco-kentei/questions_json/{group_id}/00_source"
            command(["scripts/check/check_00_source_immutability.py", "--check-scrape-refresh", "--scope", scope])
            receipt = import_group(REPO_ROOT / "output", group, source_ids=ids)
            command(["scripts/check/check_00_source_immutability.py", "--record-scrape-refresh", "--scope", scope])
            command(["tools/question_bank/question_bank.py", "quality-gate", "--qualification", "eco-kentei", "--list-group-id", group_id, "--mode", "source"])
            report["sourceIntake"].append(receipt)
            groups.append(group_id)
            save("source_intake")
        if not groups:
            raise RuntimeError("標準整備へ進められるsourceがありません")
        workflow = tomllib.loads((REPO_ROOT / "config/question_maintenance_workflow.toml").read_text())
        stages = [stage["id"] for stage in workflow["stages"] if stage.get("batch_selectable")]
        if "originalize" not in stages:
            raise ValueError("独自問題化工程が含まれていません")
        save("native_maintenance")
        report["maintenance"] = console.start_run({"qualification": "eco-kentei", "stageIds": stages,
                                                  "listGroupIds": groups, "questionConcurrency": 1, "mode": "needed"})
        save("delivery_and_evaluation")
        def prepare(group):
            questions = console.questions(group)
            inventory = load_source_record_inventory(root / "questions_json" / group / "00_source", qualification="eco-kentei", list_group_id=group)
            bindings = current_bindings(questions, inventory)
            if not bindings:
                return []
            binding_path = run_dir / (group + "_maintained_bindings.json")
            atomic_json(binding_path, bindings)
            command(["scripts/merge/00_merge_all.py", group, "--base-dir", str(root / "questions_json"), "--source-bindings", str(binding_path)])
            for name in ("check_choice_text_alignment.py", "check_required_fields.py"):
                command(["scripts/check/" + name, "--base-dir", str(root / "questions_json"), "--list-group-id", group, "--stage", "merged"])
            selected_refs = {b["sourceRecordRef"] for b in bindings}
            for entry in inventory:
                if entry.identity.binding.source_record_ref not in selected_refs:
                    continue
                base = root / "questions_json" / group
                command(["scripts/check/check_correct_choice_patch_coverage.py", "--source", str(entry.path),
                         "--patch", str(base / "23_correctChoiceText_fixed" / (entry.path.stem + "_correctChoiceText_fixed.json")),
                         "--question-type-patch", str(base / "10_questionType_fixed" / (entry.path.stem + "_questionType_fixed.json"))])
            # A partial delivery must retain the full taxonomy for later04 and
            # evaluation rework; count-derived hiding belongs to final delivery.
            command(["scripts/pipeline/prepare_firestore_upload.py", group, "-b", str(root / "questions_json"), "--skip-merge", "--skip-update-category-counts", "--upload-dry-run"])
            command(["tools/question_bank/question_bank.py", "quality-gate", "--qualification", "eco-kentei",
                     "--list-group-id", group, "--mode", "firestore", "--skip-upload-dry-run",
                     "--require-law-revision-facts", "--require-law-revision-evidence-summary",
                     "--require-law-references-for-law-related", "--fail-on-law-revision-hold"])
            console.questions(group)
            return [q for q in questions if q["sourceRecordRef"] in selected_refs]
        for group in groups:
            for question in prepare(group):
                if any(stage.get("id") == "evaluation" and stage.get("status") == "current"
                       for stage in question.get("workVersions", {}).get("stages", [])):
                    report["evaluations"].append({"questionId": question["id"], "listGroupId": group,
                        "status": "current_evaluation_retained"})
                    save("delivery_and_evaluation")
                    continue
                for attempt in range(3):
                    result = console.evaluate(question["id"])
                    evaluation = result.get("result", {}).get("evaluation", {})
                    report["evaluations"].append({"questionId": question["id"], "listGroupId": group, "attempt": attempt + 1, "result": result})
                    save("delivery_and_evaluation")
                    if evaluation.get("status") != "needs_rework" or attempt == 2:
                        break
                    console.questions(group)
                    report.setdefault("rework", []).append(console.start_run({"qualification": "eco-kentei", "stageIds": stages,
                        "questionIds": [question["id"]], "evaluationRework": True, "questionConcurrency": 1}))
                    prepare(group)
        report.update(status="finished_requires_final_audit", finishedAt=now())
        save("finished_requires_final_audit")
        return 0
    except BaseException as exc:
        report.update(status="interrupted", error=str(exc), finishedAt=now())
        save("interrupted")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
