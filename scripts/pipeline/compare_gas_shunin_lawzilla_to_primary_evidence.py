#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


JST = timezone(timedelta(hours=9))
SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.build_gas_shunin_primary_evidence_queue import article_from_candidate  # noqa: E402


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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def compact_text(value: Any, limit: int = 140) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def normalize_for_match(value: Any) -> str:
    text = str(value or "")
    text = text.replace("...", "").replace("…", "")
    text = re.sub(r"[\s　「」（）()、。，．・,.;:：；]", "", text)
    return text


def snippet_match_status(snippet: Any, article_text: Any) -> str:
    snippet_text = normalize_for_match(snippet)
    article = normalize_for_match(article_text)
    if not snippet_text:
        return "snippet_unavailable"
    if snippet_text and snippet_text in article:
        return "snippet_exact_match"
    for length in (40, 30, 20, 14):
        if len(snippet_text) < length:
            continue
        for start in range(0, max(1, len(snippet_text) - length + 1), max(1, length // 2)):
            fragment = snippet_text[start : start + length]
            if fragment and fragment in article:
                return "snippet_fragment_match"
    terms = [term for term in re.split(r"[^\w一-龥ぁ-んァ-ン]+", str(snippet or "")) if len(term) >= 3]
    if terms:
        hits = sum(1 for term in terms if term in str(article_text or ""))
        if hits >= min(3, len(terms)):
            return "snippet_keyword_overlap"
    return "snippet_not_matched"


def snapshot_index(snapshots: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (str(snapshot.get("lawId") or ""), str(snapshot.get("article") or "")): snapshot
        for snapshot in snapshots
        if snapshot.get("status") == "fetched"
    }


def build_records(
    *,
    comparison_records: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    generated_at: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    index = snapshot_index(snapshots)
    records: list[dict[str, Any]] = []
    for record in comparison_records:
        evidence_candidates: list[dict[str, Any]] = []
        for ref in record.get("candidateLawReferences") or []:
            if not isinstance(ref, dict):
                continue
            article, article_source = article_from_candidate(ref)
            if not article:
                continue
            snapshot = index.get((str(ref.get("lawId") or ""), article))
            if not snapshot:
                continue
            match_status = snippet_match_status(ref.get("snippet"), snapshot.get("articleText"))
            evidence_candidates.append(
                {
                    "lawId": ref.get("lawId"),
                    "lawName": ref.get("lawName"),
                    "article": article,
                    "articleSource": article_source,
                    "candidateAddress": ref.get("address"),
                    "candidateKanjiaddress": ref.get("kanjiaddress"),
                    "candidateSnippet": ref.get("snippet"),
                    "snippetMatchStatus": match_status,
                    "primaryEvidence": {
                        "source": snapshot.get("source"),
                        "apiUrl": snapshot.get("apiUrl"),
                        "articleTextHash": snapshot.get("articleTextHash"),
                        "rawXmlHash": snapshot.get("rawXmlHash"),
                        "rawXmlPath": snapshot.get("rawXmlPath"),
                        "fetchedAt": snapshot.get("fetchedAt"),
                        "articleTextSnippet": compact_text(snapshot.get("articleText"), 220),
                    },
                    "verificationStatus": "candidate_primary_article_fetched",
                }
            )
        statuses = {candidate["snippetMatchStatus"] for candidate in evidence_candidates}
        if not evidence_candidates:
            primary_status = "primary_snapshot_not_in_batch"
        elif statuses & {"snippet_exact_match", "snippet_fragment_match", "snippet_keyword_overlap"}:
            primary_status = "primary_article_fetched_snippet_matched"
        else:
            primary_status = "primary_article_fetched_needs_locator_detail_review"
        records.append(
            {
                "schemaVersion": "gas-shunin-lawzilla-primary-evidence-link/v1",
                "generatedAt": generated_at,
                "queueSequence": record.get("queueSequence"),
                "priority": record.get("priority"),
                "qualification": record.get("qualification"),
                "examYear": record.get("examYear"),
                "questionLabel": record.get("questionLabel"),
                "choiceIndex": record.get("choiceIndex"),
                "displayQuestionId": record.get("displayQuestionId"),
                "sourceFile": record.get("sourceFile"),
                "choiceText": record.get("choiceText"),
                "correctChoiceText": record.get("correctChoiceText"),
                "lawzillaComparisonClassification": record.get("comparisonClassification"),
                "primaryEvidenceLinkStatus": primary_status,
                "primaryEvidenceCandidateCount": len(evidence_candidates),
                "primaryEvidenceCandidates": evidence_candidates,
                "workflowDecision": "primary_article_fetched_but_do_not_mark_verified_until_locator_and_choice_logic_are_reviewed",
            }
        )
    status_counts = Counter(record["primaryEvidenceLinkStatus"] for record in records)
    snippet_counts = Counter(
        candidate["snippetMatchStatus"]
        for record in records
        for candidate in record.get("primaryEvidenceCandidates") or []
    )
    summary = {
        "schemaVersion": "gas-shunin-lawzilla-primary-evidence-link-summary/v1",
        "generatedAt": generated_at,
        "choiceRecordCount": len(records),
        "choiceRecordsWithPrimaryEvidenceCandidates": sum(1 for record in records if record["primaryEvidenceCandidateCount"]),
        "choiceRecordsWithoutPrimaryEvidenceCandidates": sum(1 for record in records if not record["primaryEvidenceCandidateCount"]),
        "primaryEvidenceCandidateLinkCount": sum(record["primaryEvidenceCandidateCount"] for record in records),
        "primaryEvidenceLinkStatusCounts": dict(sorted(status_counts.items())),
        "snippetMatchStatusCounts": dict(sorted(snippet_counts.items())),
        "snapshotArticleCount": len(index),
        "boundary": "primary evidence link report only; no 00_source, correctChoiceText, explanationText, lawReferences, lawRevisionFacts, or existing Firestore IDs were modified.",
        "nextAction": "review matched candidate links by question, then materialize only verified lawReferences with articleTextHash/rawXmlHash.",
    }
    return records, summary


def write_markdown(path: Path, summary: dict[str, Any], records: list[dict[str, Any]]) -> None:
    lines = [
        "# Gas shunin Lawzilla to primary evidence links",
        "",
        f"- generatedAt: {summary['generatedAt']}",
        f"- choiceRecordCount: {summary['choiceRecordCount']}",
        f"- snapshotArticleCount: {summary['snapshotArticleCount']}",
        f"- choiceRecordsWithPrimaryEvidenceCandidates: {summary['choiceRecordsWithPrimaryEvidenceCandidates']}",
        f"- primaryEvidenceCandidateLinkCount: {summary['primaryEvidenceCandidateLinkCount']}",
        "",
        "## Link Status Counts",
        "",
        "| status | count |",
        "| --- | ---: |",
    ]
    for key, value in summary["primaryEvidenceLinkStatusCounts"].items():
        lines.append(f"| `{key}` | {value} |")
    lines.extend(["", "## Snippet Match Counts", "", "| status | count |", "| --- | ---: |"])
    for key, value in summary["snippetMatchStatusCounts"].items():
        lines.append(f"| `{key}` | {value} |")
    lines.extend(["", "## First Linked Choices", "", "| seq | priority | qualification | year | label | choice | id | status | candidates |", "| ---: | --- | --- | ---: | --- | ---: | --- | --- | ---: |"])
    for record in [entry for entry in records if entry["primaryEvidenceCandidateCount"]][:80]:
        lines.append(
            "| {} | {} | `{}` | {} | {} | {} | `{}` | `{}` | {} |".format(
                record.get("queueSequence"),
                record.get("priority"),
                record.get("qualification"),
                record.get("examYear"),
                record.get("questionLabel"),
                int(record.get("choiceIndex") or 0) + 1,
                record.get("displayQuestionId"),
                record.get("primaryEvidenceLinkStatus"),
                record.get("primaryEvidenceCandidateCount"),
            )
        )
    lines.extend(["", "## Boundary", "", f"- {summary['boundary']}", f"- {summary['nextAction']}"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare gas-shunin Lawzilla candidates with fetched primary evidence snapshots.")
    parser.add_argument("--comparison-jsonl", required=True)
    parser.add_argument("--snapshots-jsonl", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--timestamp", required=True)
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve()
    comparison_path = Path(args.comparison_jsonl).expanduser()
    if not comparison_path.is_absolute():
        comparison_path = repo_root / comparison_path
    snapshots_path = Path(args.snapshots_jsonl).expanduser()
    if not snapshots_path.is_absolute():
        snapshots_path = repo_root / snapshots_path
    output_dir = Path(args.output_dir).expanduser()
    if not output_dir.is_absolute():
        output_dir = repo_root / output_dir
    generated_at = now_jst()
    records, summary = build_records(
        comparison_records=load_jsonl(comparison_path),
        snapshots=load_jsonl(snapshots_path),
        generated_at=generated_at,
    )
    jsonl_path = output_dir / f"{args.timestamp}_gas_shunin_lawzilla_primary_evidence_links.jsonl"
    summary_json = output_dir / f"{args.timestamp}_gas_shunin_lawzilla_primary_evidence_links_summary.json"
    summary_md = output_dir / f"{args.timestamp}_gas_shunin_lawzilla_primary_evidence_links_summary.md"
    summary.update(
        {
            "comparisonJsonl": str(comparison_path.relative_to(repo_root) if comparison_path.is_relative_to(repo_root) else comparison_path),
            "snapshotsJsonl": str(snapshots_path.relative_to(repo_root) if snapshots_path.is_relative_to(repo_root) else snapshots_path),
            "linkJsonl": str(jsonl_path.relative_to(repo_root)),
            "summaryJson": str(summary_json.relative_to(repo_root)),
            "summaryMarkdown": str(summary_md.relative_to(repo_root)),
        }
    )
    write_jsonl(jsonl_path, records)
    write_json(summary_json, summary)
    write_markdown(summary_md, summary, records)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
