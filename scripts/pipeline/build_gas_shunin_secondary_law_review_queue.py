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

from scripts.pipeline.collect_lawzilla_mcp_candidates import stage_question  # noqa: E402


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


def compact_text(value: Any, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def display_id(value: dict[str, Any]) -> str:
    return str(value.get("publicQuestionId") or value.get("originalQuestionId") or value.get("reviewQuestionId") or value.get("displayQuestionId") or "")


def item_key(value: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(value.get("qualification") or ""),
        str(value.get("examYear") or ""),
        str(value.get("questionLabel") or ""),
        display_id(value),
    )


def exact_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def safe_get(values: list[Any], index: int) -> Any:
    if 0 <= index < len(values):
        return values[index]
    return None


def count_nested_refs(value: Any) -> int:
    if not isinstance(value, list):
        return 0
    count = 0
    for item in value:
        if isinstance(item, list):
            count += sum(1 for ref in item if isinstance(ref, dict))
        elif isinstance(item, dict):
            count += 1
    return count


def summarize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    primary = candidate.get("primaryEvidence") if isinstance(candidate.get("primaryEvidence"), dict) else {}
    return {
        key: value
        for key, value in {
            "lawId": candidate.get("lawId"),
            "lawTitle": candidate.get("lawName"),
            "article": candidate.get("article"),
            "candidateAddress": candidate.get("candidateAddress"),
            "candidateKanjiaddress": candidate.get("candidateKanjiaddress"),
            "candidateSnippet": compact_text(candidate.get("candidateSnippet"), 220),
            "snippetMatchStatus": candidate.get("snippetMatchStatus"),
            "articleTextHash": primary.get("articleTextHash"),
            "rawXmlHash": primary.get("rawXmlHash"),
            "rawXmlPath": primary.get("rawXmlPath"),
            "apiUrl": primary.get("apiUrl"),
            "verificationStatus": candidate.get("verificationStatus"),
        }.items()
        if value not in (None, "", [], {})
    }


def has_verified_law_review(question: dict[str, Any]) -> bool:
    if question.get("isLawRelated") is not True:
        return False
    refs = question.get("lawReferences")
    if not isinstance(refs, list) or not refs:
        return False
    verified_ref_count = 0
    for choice_refs in refs:
        if not isinstance(choice_refs, list):
            continue
        for ref in choice_refs:
            if not isinstance(ref, dict):
                continue
            if ref.get("verificationStatus") == "verified" and ref.get("lawId") and ref.get("article"):
                verified_ref_count += 1
    if verified_ref_count == 0:
        return False
    facts = question.get("lawRevisionFacts")
    return isinstance(facts, dict) or (isinstance(facts, list) and any(isinstance(item, dict) for item in facts))


def readiness_for_records(records: list[dict[str, Any]], queue_item: dict[str, Any], question: dict[str, Any]) -> tuple[str, list[str]]:
    if has_verified_law_review(question):
        return "secondary_verified_in_question_patch", [
            "Verified lawReferences and lawRevisionFacts already exist in the question patch.",
            "Keep in regression queue, but skip unless source/evidence changes.",
        ]
    if not records:
        tracks = set(queue_item.get("targetTracks") or [])
        if "lawzilla_law_evidence" in tracks:
            return "needs_candidate_discovery_or_primary_source_review", [
                "Lawzilla/primary evidence link record is missing for this queue item.",
                "Do not add lawReferences until primary source locator is found and checked.",
            ]
        if "answer_recheck" in tracks:
            return "answer_recheck_without_lawzilla_evidence", [
                "This item was routed for answer mapping review, not Lawzilla materialization.",
                "Check source answer and correctChoiceText before changing question artifacts.",
            ]
        return "non_lawzilla_workflow_item", ["This item has no Lawzilla evidence track in the current run."]

    status_counts = Counter(str(record.get("primaryEvidenceLinkStatus") or "") for record in records)
    no_candidate_count = sum(1 for record in records if not record.get("primaryEvidenceCandidateCount"))
    any_locator_detail = any(
        record.get("primaryEvidenceLinkStatus") == "primary_article_fetched_needs_locator_detail_review"
        for record in records
    )
    all_matched = all(
        record.get("primaryEvidenceCandidateCount")
        and record.get("primaryEvidenceLinkStatus") == "primary_article_fetched_snippet_matched"
        for record in records
    )
    if any_locator_detail:
        return "manual_review_required_locator_detail", [
            "At least one choice has fetched primary article text but no reliable snippet match.",
            "Confirm article/paragraph/item manually before any lawReferences patch.",
        ]
    if no_candidate_count:
        return "manual_review_required_partial_candidates", [
            f"{no_candidate_count} choice record(s) do not have fetched primary evidence candidates.",
            "Improve query terms or inspect primary sources manually for missing choices.",
        ]
    if all_matched:
        return "manual_review_required_all_choices_have_primary_evidence", [
            "All linked choices have fetched primary article candidates and snippet matches.",
            "This is still not verified: compare choice logic, explanation, and exact article/paragraph/item manually.",
        ]
    return "manual_review_required_mixed_status", [
        f"Mixed primary evidence statuses: {dict(sorted(status_counts.items()))}",
        "Review each choice before materializing lawReferences.",
    ]


def correct_choice_values(question: dict[str, Any], queue_item: dict[str, Any]) -> list[Any]:
    values = exact_list(question.get("correctChoiceText"))
    if values:
        return values
    compact = str(queue_item.get("correctChoiceTextCompact") or "").strip()
    if compact:
        return compact.split("|")
    return []


def build_choice_reviews(question: dict[str, Any], records: list[dict[str, Any]], queue_item: dict[str, Any]) -> list[dict[str, Any]]:
    by_choice: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        try:
            choice_index = int(record.get("choiceIndex"))
        except (TypeError, ValueError):
            continue
        by_choice[choice_index].append(record)

    choice_texts = exact_list(question.get("choiceTextList") or question.get("originalQuestionChoiceText"))
    corrects = correct_choice_values(question, queue_item)
    explanations = exact_list(question.get("explanationText"))
    refs = exact_list(question.get("lawReferences"))
    max_len = max(len(choice_texts), len(corrects), len(explanations), len(by_choice))

    reviews: list[dict[str, Any]] = []
    for index in range(max_len):
        choice_records = sorted(by_choice.get(index, []), key=lambda item: int(item.get("primaryEvidenceCandidateCount") or 0), reverse=True)
        candidates = [
            summarize_candidate(candidate)
            for record in choice_records
            for candidate in exact_list(record.get("primaryEvidenceCandidates"))
            if isinstance(candidate, dict)
        ]
        reviews.append(
            {
                "choiceIndex": index,
                "choiceText": compact_text(safe_get(choice_texts, index), 280),
                "correctChoiceText": safe_get(corrects, index),
                "explanationText": compact_text(safe_get(explanations, index), 300),
                "existingLawReferenceCount": count_nested_refs(safe_get(refs, index)),
                "primaryEvidenceLinkStatus": sorted({str(record.get("primaryEvidenceLinkStatus") or "") for record in choice_records if record.get("primaryEvidenceLinkStatus")}),
                "primaryEvidenceCandidateCount": len(candidates),
                "primaryEvidenceCandidates": candidates[:8],
            }
        )
    return reviews


def build_queue(
    *,
    repo_root: Path,
    maintenance_queue: list[dict[str, Any]],
    evidence_links: list[dict[str, Any]],
    generated_at: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    links_by_key: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in evidence_links:
        links_by_key[item_key(record)].append(record)

    records: list[dict[str, Any]] = []
    load_errors: list[dict[str, Any]] = []
    queue_keys = [item_key(item) for item in maintenance_queue]
    duplicate_queue_key_count = sum(1 for count in Counter(queue_keys).values() if count > 1)
    max_queue_key_multiplicity = max(Counter(queue_keys).values()) if queue_keys else 0
    for item in maintenance_queue:
        links = sorted(links_by_key.get(item_key(item), []), key=lambda record: int(record.get("choiceIndex") or 0))
        try:
            question = stage_question(item, repo_root)
        except Exception as exc:  # noqa: BLE001
            question = {}
            load_errors.append(
                {
                    "queueSequence": item.get("queueSequence"),
                    "qualification": item.get("qualification"),
                    "examYear": item.get("examYear"),
                    "questionLabel": item.get("questionLabel"),
                    "displayQuestionId": display_id(item),
                    "error": str(exc),
                }
            )
        readiness, reasons = readiness_for_records(links, item, question)
        choice_reviews = build_choice_reviews(question, links, item)
        records.append(
            {
                "schemaVersion": "gas-shunin-secondary-law-review-queue/v1",
                "generatedAt": generated_at,
                "queueSequence": item.get("queueSequence"),
                "priority": item.get("priority"),
                "targetTracks": item.get("targetTracks"),
                "qualification": item.get("qualification"),
                "examYear": item.get("examYear"),
                "questionLabel": item.get("questionLabel"),
                "displayQuestionId": display_id(item),
                "publicQuestionId": item.get("publicQuestionId"),
                "originalQuestionId": item.get("originalQuestionId"),
                "reviewQuestionId": item.get("reviewQuestionId"),
                "sourceFile": item.get("sourceFile"),
                "stagePatchFiles": item.get("stagePatchFiles"),
                "questionType": question.get("questionType") or item.get("questionType"),
                "questionIntent": question.get("questionIntent") or item.get("questionIntent"),
                "answerResultText": question.get("answer_result_text") or item.get("answerResultText"),
                "choiceCount": len(exact_list(question.get("choiceTextList") or question.get("originalQuestionChoiceText"))),
                "existingIsLawRelated": question.get("isLawRelated"),
                "existingLawGroundedExplanationNotNeeded": question.get("lawGroundedExplanationNotNeeded"),
                "existingLawReferenceCount": count_nested_refs(question.get("lawReferences")),
                "primaryEvidenceChoiceRecordCount": len(links),
                "primaryEvidenceCandidateCount": sum(int(record.get("primaryEvidenceCandidateCount") or 0) for record in links),
                "primaryEvidenceLinkStatusCounts": dict(sorted(Counter(str(record.get("primaryEvidenceLinkStatus") or "") for record in links).items())),
                "secondaryReviewReadiness": readiness,
                "secondaryReviewReasons": reasons,
                "choiceReviews": choice_reviews,
                "workflowDecision": "manual_secondary_review_required_before_marking_lawReferences_verified",
            }
        )

    records.sort(key=lambda record: (str(record.get("priority") or "P9"), int(record.get("queueSequence") or 999999)))
    readiness_counts = Counter(str(record.get("secondaryReviewReadiness")) for record in records)
    priority_counts = Counter(str(record.get("priority")) for record in records)
    track_counts = Counter(
        str(track)
        for record in records
        for track in exact_list(record.get("targetTracks"))
    )
    summary = {
        "schemaVersion": "gas-shunin-secondary-law-review-queue-summary/v1",
        "generatedAt": generated_at,
        "queueItemCount": len(records),
        "uniqueQueueItemKeyCount": len(set(queue_keys)),
        "duplicateQueueKeyCount": duplicate_queue_key_count,
        "maxQueueKeyMultiplicity": max_queue_key_multiplicity,
        "questionLoadErrorCount": len(load_errors),
        "readinessCounts": dict(sorted(readiness_counts.items())),
        "priorityCounts": dict(sorted(priority_counts.items())),
        "trackCounts": dict(sorted(track_counts.items())),
        "primaryEvidenceChoiceRecordCount": sum(int(record.get("primaryEvidenceChoiceRecordCount") or 0) for record in records),
        "primaryEvidenceCandidateCount": sum(int(record.get("primaryEvidenceCandidateCount") or 0) for record in records),
        "sourcePrimaryEvidenceLinkChoiceRecordCount": len(evidence_links),
        "sourcePrimaryEvidenceCandidateCount": sum(int(record.get("primaryEvidenceCandidateCount") or 0) for record in evidence_links),
        "sourcePrimaryEvidenceLinkStatusCounts": dict(sorted(Counter(str(record.get("primaryEvidenceLinkStatus") or "") for record in evidence_links).items())),
        "existingLawReferenceCount": sum(int(record.get("existingLawReferenceCount") or 0) for record in records),
        "questionLoadErrors": load_errors[:20],
        "boundary": "review queue only; no 00_source, correctChoiceText, explanationText, lawReferences, lawRevisionFacts, or existing Firestore IDs were modified.",
        "nextAction": "perform one-question secondary review in queue order, then patch only manually verified lawReferences/lawRevisionFacts.",
    }
    return records, summary


def write_markdown(path: Path, summary: dict[str, Any], records: list[dict[str, Any]]) -> None:
    lines = [
        "# Gas shunin secondary law review queue",
        "",
        f"- generatedAt: {summary['generatedAt']}",
        f"- queueItemCount: {summary['queueItemCount']}",
        f"- uniqueQueueItemKeyCount: {summary['uniqueQueueItemKeyCount']}",
        f"- duplicateQueueKeyCount: {summary['duplicateQueueKeyCount']}",
        f"- questionLoadErrorCount: {summary['questionLoadErrorCount']}",
        f"- primaryEvidenceChoiceRecordCount: {summary['primaryEvidenceChoiceRecordCount']}",
        f"- primaryEvidenceCandidateCount: {summary['primaryEvidenceCandidateCount']}",
        f"- sourcePrimaryEvidenceLinkChoiceRecordCount: {summary['sourcePrimaryEvidenceLinkChoiceRecordCount']}",
        f"- sourcePrimaryEvidenceCandidateCount: {summary['sourcePrimaryEvidenceCandidateCount']}",
        f"- existingLawReferenceCount: {summary['existingLawReferenceCount']}",
        "",
        "## Readiness Counts",
        "",
        "| readiness | count |",
        "| --- | ---: |",
    ]
    for key, value in summary["readinessCounts"].items():
        lines.append(f"| `{key}` | {value} |")
    lines.extend(["", "## Priority Counts", "", "| priority | count |", "| --- | ---: |"])
    for key, value in summary["priorityCounts"].items():
        lines.append(f"| `{key}` | {value} |")
    lines.extend(
        [
            "",
            "## First Manual Review Items",
            "",
            "| seq | priority | qualification | year | label | id | readiness | choices | candidates |",
            "| ---: | --- | --- | ---: | --- | --- | --- | ---: | ---: |",
        ]
    )
    for record in records[:80]:
        lines.append(
            "| {} | {} | `{}` | {} | {} | `{}` | `{}` | {} | {} |".format(
                record.get("queueSequence"),
                record.get("priority"),
                record.get("qualification"),
                record.get("examYear"),
                record.get("questionLabel"),
                record.get("displayQuestionId"),
                record.get("secondaryReviewReadiness"),
                record.get("choiceCount"),
                record.get("primaryEvidenceCandidateCount"),
            )
        )
    lines.extend(["", "## Boundary", "", f"- {summary['boundary']}", f"- {summary['nextAction']}"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build gas-shunin secondary review queue from Lawzilla and primary evidence artifacts.")
    parser.add_argument("--maintenance-queue-jsonl", required=True)
    parser.add_argument("--primary-evidence-links-jsonl", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--timestamp", required=True)
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve()
    maintenance_path = Path(args.maintenance_queue_jsonl).expanduser()
    if not maintenance_path.is_absolute():
        maintenance_path = repo_root / maintenance_path
    links_path = Path(args.primary_evidence_links_jsonl).expanduser()
    if not links_path.is_absolute():
        links_path = repo_root / links_path
    output_dir = Path(args.output_dir).expanduser()
    if not output_dir.is_absolute():
        output_dir = repo_root / output_dir

    generated_at = now_jst()
    records, summary = build_queue(
        repo_root=repo_root,
        maintenance_queue=load_jsonl(maintenance_path),
        evidence_links=load_jsonl(links_path),
        generated_at=generated_at,
    )
    queue_path = output_dir / f"{args.timestamp}_gas_shunin_secondary_law_review_queue.jsonl"
    summary_json = output_dir / f"{args.timestamp}_gas_shunin_secondary_law_review_queue_summary.json"
    summary_md = output_dir / f"{args.timestamp}_gas_shunin_secondary_law_review_queue_summary.md"
    summary.update(
        {
            "maintenanceQueueJsonl": str(maintenance_path.relative_to(repo_root) if maintenance_path.is_relative_to(repo_root) else maintenance_path),
            "primaryEvidenceLinksJsonl": str(links_path.relative_to(repo_root) if links_path.is_relative_to(repo_root) else links_path),
            "secondaryReviewQueueJsonl": str(queue_path.relative_to(repo_root)),
            "summaryJson": str(summary_json.relative_to(repo_root)),
            "summaryMarkdown": str(summary_md.relative_to(repo_root)),
        }
    )
    write_jsonl(queue_path, records)
    write_json(summary_json, summary)
    write_markdown(summary_md, summary, records)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
