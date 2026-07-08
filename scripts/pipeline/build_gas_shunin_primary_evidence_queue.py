#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


JST = timezone(timedelta(hours=9))
SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.fetch_law_article_snapshots import article_query_candidates, egov_article_api_url  # noqa: E402


KANJI_TABLE_NUMBERS = {
    "一": "1",
    "二": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
    "十": "10",
}


def now_jst() -> str:
    return datetime.now(JST).replace(microsecond=0).isoformat()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected JSON object")
            records.append(value)
    return records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def compact_text(value: Any, limit: int = 140) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def article_from_ln_address(address: str) -> tuple[str, str] | None:
    match = re.match(r"ln([0-9]+(?:_[0-9]+)?)(?:\.|$)", address)
    if not match:
        return None
    raw = match.group(1)
    if "_" in raw:
        return f"第{raw.replace('_', '条の')}", "lawzilla_ln_address"
    return f"第{raw}条", "lawzilla_ln_address"


def article_from_kanjiaddress(kanjiaddress: str) -> tuple[str, str] | None:
    match = re.search(r"第([0-9０-９]+)条(?:の([0-9０-９]+))?", kanjiaddress)
    if match:
        base = match.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        suffix = (match.group(2) or "").translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        if suffix:
            return f"第{base}条の{suffix}", "kanjiaddress_article"
        return f"第{base}条", "kanjiaddress_article"
    table_match = re.search(r"別表第?([一二三四五六七八九十0-9０-９]+)", kanjiaddress)
    if table_match:
        raw = table_match.group(1)
        normalized = KANJI_TABLE_NUMBERS.get(raw, raw).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        return f"別表第{normalized}", "kanjiaddress_appendix_table"
    return None


def article_from_candidate(ref: dict[str, Any]) -> tuple[str, str] | tuple[None, str]:
    address = str(ref.get("address") or "")
    kanjiaddress = str(ref.get("kanjiaddress") or "")
    from_address = article_from_ln_address(address)
    if from_address:
        return from_address
    from_kanjiaddress = article_from_kanjiaddress(kanjiaddress)
    if from_kanjiaddress:
        return from_kanjiaddress
    return None, "article_unparsed"


def display_id(record: dict[str, Any]) -> str:
    return str(record.get("publicQuestionId") or record.get("originalQuestionId") or record.get("reviewQuestionId") or "")


def build_queue(records: list[dict[str, Any]], *, generated_at: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    unparsed: list[dict[str, Any]] = []
    source_choice_seen: dict[tuple[str, str], set[tuple[Any, ...]]] = defaultdict(set)
    source_question_seen: dict[tuple[str, str], set[tuple[Any, ...]]] = defaultdict(set)
    locator_counter: Counter[tuple[str, str, str, str]] = Counter()

    for record in records:
        for ref in record.get("candidateLawReferences") or []:
            if not isinstance(ref, dict):
                continue
            law_id = str(ref.get("lawId") or "")
            law_name = str(ref.get("lawName") or "")
            article, source = article_from_candidate(ref)
            if not law_id or not article:
                unparsed.append(
                    {
                        "schemaVersion": "gas-shunin-primary-evidence-unparsed-candidate/v1",
                        "generatedAt": generated_at,
                        "reason": source,
                        "lawId": law_id,
                        "lawName": law_name,
                        "address": ref.get("address"),
                        "kanjiaddress": ref.get("kanjiaddress"),
                        "snippet": ref.get("snippet"),
                        "queueSequence": record.get("queueSequence"),
                        "qualification": record.get("qualification"),
                        "examYear": record.get("examYear"),
                        "questionLabel": record.get("questionLabel"),
                        "choiceIndex": record.get("choiceIndex"),
                        "displayQuestionId": display_id(record),
                    }
                )
                continue
            key = (law_id, article)
            choice_key = (
                record.get("qualification"),
                record.get("examYear"),
                record.get("questionLabel"),
                display_id(record),
                record.get("choiceIndex"),
            )
            question_key = (
                record.get("qualification"),
                record.get("examYear"),
                record.get("questionLabel"),
                display_id(record),
            )
            source_choice_seen[key].add(choice_key)
            source_question_seen[key].add(question_key)
            locator_counter[(law_id, article, str(ref.get("address") or ""), str(ref.get("kanjiaddress") or ""))] += 1
            entry = grouped.setdefault(
                key,
                {
                    "schemaVersion": "gas-shunin-primary-evidence-fetch-queue/v1",
                    "generatedAt": generated_at,
                    "lawId": law_id,
                    "lawName": law_name,
                    "article": article,
                    "articleSource": source,
                    "articleQueryCandidates": article_query_candidates(article),
                    "egovApiUrls": [egov_article_api_url(law_id, query) for query in article_query_candidates(article)],
                    "candidateLocatorCount": 0,
                    "choiceRecordCount": 0,
                    "questionRecordCount": 0,
                    "sampleLocators": [],
                    "sampleQuestions": [],
                    "workflowDecision": "fetch_primary_evidence_before_materializing_lawReferences",
                },
            )
            entry["candidateLocatorCount"] += 1
            locator = {
                "address": ref.get("address"),
                "kanjiaddress": ref.get("kanjiaddress"),
                "title": ref.get("title"),
                "snippet": compact_text(ref.get("snippet"), 160),
            }
            if locator not in entry["sampleLocators"] and len(entry["sampleLocators"]) < 12:
                entry["sampleLocators"].append(locator)
            sample_question = {
                "queueSequence": record.get("queueSequence"),
                "priority": record.get("priority"),
                "qualification": record.get("qualification"),
                "examYear": record.get("examYear"),
                "questionLabel": record.get("questionLabel"),
                "choiceIndex": record.get("choiceIndex"),
                "displayQuestionId": display_id(record),
                "choiceText": compact_text(record.get("choiceText"), 120),
            }
            if sample_question not in entry["sampleQuestions"] and len(entry["sampleQuestions"]) < 8:
                entry["sampleQuestions"].append(sample_question)

    queue_records = list(grouped.values())
    for entry in queue_records:
        key = (entry["lawId"], entry["article"])
        entry["choiceRecordCount"] = len(source_choice_seen[key])
        entry["questionRecordCount"] = len(source_question_seen[key])
        if entry["articleSource"].endswith("appendix_table"):
            entry["fetchPriority"] = "table_manual_review"
        else:
            entry["fetchPriority"] = "primary_api_fetch"
    queue_records.sort(key=lambda entry: (-entry["choiceRecordCount"], entry["lawId"], entry["article"]))

    high_priority = [entry for entry in queue_records if entry["fetchPriority"] == "primary_api_fetch"][:40]
    table_manual = [entry for entry in queue_records if entry["fetchPriority"] == "table_manual_review"]
    high_priority_choice_keys = {
        choice_key
        for entry in high_priority
        for choice_key in source_choice_seen[(entry["lawId"], entry["article"])]
    }
    summary = {
        "schemaVersion": "gas-shunin-primary-evidence-fetch-queue-summary/v1",
        "generatedAt": generated_at,
        "sourceChoiceRecordCount": len(records),
        "fetchQueueArticleCount": len(queue_records),
        "highPriorityFetchBatchArticleCount": len(high_priority),
        "unparsedCandidateCount": len(unparsed),
        "tableManualReviewArticleCount": len(table_manual),
        "highPriorityFetchBatchCandidateChoiceLinkCount": sum(entry["choiceRecordCount"] for entry in high_priority),
        "highPriorityFetchBatchDistinctChoiceRecordCount": len(high_priority_choice_keys),
        "articleSourceCounts": dict(sorted(Counter(entry["articleSource"] for entry in queue_records).items())),
        "lawCounts": dict(sorted(Counter(entry["lawId"] for entry in queue_records).items())),
        "fetchPriorityCounts": dict(sorted(Counter(entry["fetchPriority"] for entry in queue_records).items())),
        "boundary": "primary evidence queue only; no 00_source, correctChoiceText, explanationText, lawReferences, lawRevisionFacts, or existing Firestore IDs were modified.",
        "nextAction": "fetch e-Gov article snapshots for highPriorityFetchBatch, then compare article text with Lawzilla candidate snippets before patching lawReferences.",
    }
    return queue_records, unparsed, summary


def write_markdown(path: Path, summary: dict[str, Any], high_priority: list[dict[str, Any]], unparsed: list[dict[str, Any]]) -> None:
    lines = [
        "# Gas shunin primary evidence fetch queue",
        "",
        f"- generatedAt: {summary['generatedAt']}",
        f"- sourceChoiceRecordCount: {summary['sourceChoiceRecordCount']}",
        f"- fetchQueueArticleCount: {summary['fetchQueueArticleCount']}",
        f"- highPriorityFetchBatchArticleCount: {summary['highPriorityFetchBatchArticleCount']}",
        f"- highPriorityFetchBatchCandidateChoiceLinkCount: {summary['highPriorityFetchBatchCandidateChoiceLinkCount']}",
        f"- highPriorityFetchBatchDistinctChoiceRecordCount: {summary['highPriorityFetchBatchDistinctChoiceRecordCount']}",
        f"- unparsedCandidateCount: {summary['unparsedCandidateCount']}",
        "",
        "## Fetch Priority Counts",
        "",
        "| priority | count |",
        "| --- | ---: |",
    ]
    for key, value in summary["fetchPriorityCounts"].items():
        lines.append(f"| `{key}` | {value} |")
    lines.extend(["", "## High Priority Fetch Batch", "", "| rank | lawId | article | choices | questions |", "| ---: | --- | --- | ---: | ---: |"])
    for index, entry in enumerate(high_priority, 1):
        lines.append(f"| {index} | `{entry['lawId']}` | {entry['article']} | {entry['choiceRecordCount']} | {entry['questionRecordCount']} |")
    lines.extend(["", "## Unparsed Examples", "", "| lawId | address | kanjiaddress | count/context |", "| --- | --- | --- | --- |"])
    for entry in unparsed[:40]:
        lines.append(
            "| `{}` | `{}` | `{}` | {} {} {} choice {} |".format(
                entry.get("lawId"),
                entry.get("address"),
                entry.get("kanjiaddress"),
                entry.get("qualification"),
                entry.get("examYear"),
                entry.get("questionLabel"),
                entry.get("choiceIndex"),
            )
        )
    lines.extend(["", "## Boundary", "", f"- {summary['boundary']}", f"- {summary['nextAction']}"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build e-Gov primary evidence fetch queue from gas-shunin Lawzilla candidates.")
    parser.add_argument("--comparison-jsonl", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--timestamp", required=True)
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve()
    comparison_path = Path(args.comparison_jsonl).expanduser()
    if not comparison_path.is_absolute():
        comparison_path = repo_root / comparison_path
    output_dir = Path(args.output_dir).expanduser()
    if not output_dir.is_absolute():
        output_dir = repo_root / output_dir
    generated_at = now_jst()
    records = load_jsonl(comparison_path)
    queue_records, unparsed, summary = build_queue(records, generated_at=generated_at)
    high_priority = [entry for entry in queue_records if entry["fetchPriority"] == "primary_api_fetch"][:40]

    queue_jsonl = output_dir / f"{args.timestamp}_gas_shunin_primary_evidence_fetch_queue.jsonl"
    high_priority_json = output_dir / f"{args.timestamp}_gas_shunin_primary_evidence_fetch_batch_top40.json"
    unparsed_jsonl = output_dir / f"{args.timestamp}_gas_shunin_primary_evidence_unparsed_candidates.jsonl"
    summary_json = output_dir / f"{args.timestamp}_gas_shunin_primary_evidence_fetch_queue_summary.json"
    summary_md = output_dir / f"{args.timestamp}_gas_shunin_primary_evidence_fetch_queue_summary.md"
    summary.update(
        {
            "comparisonJsonl": str(comparison_path.relative_to(repo_root) if comparison_path.is_relative_to(repo_root) else comparison_path),
            "fetchQueueJsonl": str(queue_jsonl.relative_to(repo_root)),
            "highPriorityFetchBatchJson": str(high_priority_json.relative_to(repo_root)),
            "unparsedCandidatesJsonl": str(unparsed_jsonl.relative_to(repo_root)),
            "summaryJson": str(summary_json.relative_to(repo_root)),
            "summaryMarkdown": str(summary_md.relative_to(repo_root)),
        }
    )
    write_jsonl(queue_jsonl, queue_records)
    write_json(
        high_priority_json,
        {
            "schemaVersion": "gas-shunin-primary-evidence-fetch-batch/v1",
            "generatedAt": generated_at,
            "batchId": f"{args.timestamp}_top40_primary_api_fetch",
            "sourceQueuePath": str(queue_jsonl.relative_to(repo_root)),
            "itemCount": len(high_priority),
            "items": high_priority,
            "boundary": summary["boundary"],
        },
    )
    write_jsonl(unparsed_jsonl, unparsed)
    write_json(summary_json, summary)
    write_markdown(summary_md, summary, high_priority, unparsed)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
