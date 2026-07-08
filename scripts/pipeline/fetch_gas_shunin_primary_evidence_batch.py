#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


JST = timezone(timedelta(hours=9))
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.fetch_law_article_snapshots import build_snapshot  # noqa: E402


def now_jst() -> str:
    return datetime.now(JST).replace(microsecond=0).isoformat()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def snapshot_ref(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "lawId": item.get("lawId"),
        "lawTitle": item.get("lawName"),
        "article": item.get("article"),
        "referenceDate": "current",
        "questionIds": [
            str(sample.get("displayQuestionId"))
            for sample in item.get("sampleQuestions", [])
            if sample.get("displayQuestionId")
        ],
        "originalQuestionIds": [
            str(sample.get("displayQuestionId"))
            for sample in item.get("sampleQuestions", [])
            if sample.get("displayQuestionId")
        ],
    }


def enriched_snapshot(snapshot: dict[str, Any], item: dict[str, Any], *, batch_id: str) -> dict[str, Any]:
    result = dict(snapshot)
    result.update(
        {
            "schemaVersion": "gas-shunin-primary-evidence-snapshot/v1",
            "batchId": batch_id,
            "lawzillaArticleSource": item.get("articleSource"),
            "candidateLocatorCount": item.get("candidateLocatorCount"),
            "choiceRecordCount": item.get("choiceRecordCount"),
            "questionRecordCount": item.get("questionRecordCount"),
            "sampleLocators": item.get("sampleLocators"),
            "sampleQuestions": item.get("sampleQuestions"),
            "workflowDecision": "primary_evidence_snapshot_for_candidate_lawReference_review",
        }
    )
    return result


def write_markdown(path: Path, summary: dict[str, Any], records: list[dict[str, Any]]) -> None:
    lines = [
        "# Gas shunin primary evidence snapshot batch",
        "",
        f"- generatedAt: {summary['generatedAt']}",
        f"- batchId: `{summary['batchId']}`",
        f"- itemCount: {summary['itemCount']}",
        f"- fetchedCount: {summary['statusCounts'].get('fetched', 0)}",
        f"- fetchFailedCount: {summary['fetchFailedCount']}",
        "",
        "## Status Counts",
        "",
        "| status | count |",
        "| --- | ---: |",
    ]
    for key, value in summary["statusCounts"].items():
        lines.append(f"| `{key}` | {value} |")
    lines.extend(["", "## Snapshots", "", "| rank | status | lawId | article | chars | hash | choices |", "| ---: | --- | --- | --- | ---: | --- | ---: |"])
    for index, record in enumerate(records, 1):
        lines.append(
            "| {} | `{}` | `{}` | {} | {} | `{}` | {} |".format(
                index,
                record.get("status"),
                record.get("lawId"),
                record.get("article"),
                len(str(record.get("articleText") or "")),
                str(record.get("articleTextHash") or "")[:16],
                record.get("choiceRecordCount"),
            )
        )
    if summary["fetchFailedCount"]:
        lines.extend(["", "## Failures", "", "| lawId | article | error |", "| --- | --- | --- |"])
        for record in records:
            if record.get("status") == "fetch_failed":
                lines.append(f"| `{record.get('lawId')}` | {record.get('article')} | {record.get('error')} |")
    lines.extend(["", "## Boundary", "", f"- {summary['boundary']}", f"- {summary['nextAction']}"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch e-Gov snapshots for gas-shunin primary evidence batch.")
    parser.add_argument("--batch-json", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--timestamp", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--delay-seconds", type=float, default=0.2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fail-on-fetch-error", action="store_true")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve()
    batch_path = Path(args.batch_json).expanduser()
    if not batch_path.is_absolute():
        batch_path = repo_root / batch_path
    output_dir = Path(args.output_dir).expanduser()
    if not output_dir.is_absolute():
        output_dir = repo_root / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_xml_dir = output_dir / "raw_xml" / args.timestamp
    batch = load_json(batch_path)
    items = [item for item in batch.get("items", []) if isinstance(item, dict)]
    if args.limit is not None:
        items = items[: args.limit]
    generated_at = now_jst()
    batch_id = str(batch.get("batchId") or f"{args.timestamp}_primary_evidence")

    records: list[dict[str, Any]] = []
    failures = 0
    for index, item in enumerate(items, 1):
        ref = snapshot_ref(item)
        try:
            snapshot = build_snapshot(
                ref,
                fetched_at=generated_at,
                timeout_seconds=args.timeout_seconds,
                raw_xml_dir=raw_xml_dir,
                dry_run=args.dry_run,
            )
        except Exception as exc:  # noqa: BLE001
            failures += 1
            snapshot = {
                "status": "fetch_failed",
                "lawId": ref.get("lawId"),
                "lawTitle": ref.get("lawTitle"),
                "article": ref.get("article"),
                "referenceDate": ref.get("referenceDate"),
                "source": "e-gov-law-api-v1",
                "fetchedAt": generated_at,
                "error": str(exc),
            }
        records.append(enriched_snapshot(snapshot, item, batch_id=batch_id))
        print(f"[{index}/{len(items)}] {records[-1].get('status')} {ref.get('lawId')} {ref.get('article')}", flush=True)
        if args.delay_seconds > 0 and index < len(items):
            time.sleep(args.delay_seconds)

    status_counts = Counter(str(record.get("status") or "") for record in records)
    snapshot_jsonl = output_dir / f"{args.timestamp}_gas_shunin_primary_evidence_snapshots_top40.jsonl"
    summary_json = output_dir / f"{args.timestamp}_gas_shunin_primary_evidence_snapshots_top40_summary.json"
    summary_md = output_dir / f"{args.timestamp}_gas_shunin_primary_evidence_snapshots_top40_summary.md"
    summary = {
        "schemaVersion": "gas-shunin-primary-evidence-snapshot-batch-summary/v1",
        "generatedAt": generated_at,
        "batchId": batch_id,
        "batchPath": str(batch_path.relative_to(repo_root) if batch_path.is_relative_to(repo_root) else batch_path),
        "snapshotJsonl": str(snapshot_jsonl.relative_to(repo_root)),
        "summaryJson": str(summary_json.relative_to(repo_root)),
        "summaryMarkdown": str(summary_md.relative_to(repo_root)),
        "itemCount": len(records),
        "statusCounts": dict(sorted(status_counts.items())),
        "fetchFailedCount": failures,
        "dryRun": args.dry_run,
        "rawXmlDir": str(raw_xml_dir.relative_to(repo_root) if raw_xml_dir.is_relative_to(repo_root) else raw_xml_dir),
        "boundary": "primary evidence snapshots only; no 00_source, correctChoiceText, explanationText, lawReferences, lawRevisionFacts, or existing Firestore IDs were modified.",
        "nextAction": "compare articleTextHash and articleText with Lawzilla snippets, then materialize only verified lawReferences.",
    }
    write_jsonl(snapshot_jsonl, records)
    write_json(summary_json, summary)
    write_markdown(summary_md, summary, records)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    if failures and args.fail_on_fetch_error:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
