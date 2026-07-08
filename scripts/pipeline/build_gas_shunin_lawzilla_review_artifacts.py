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


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    records.append(value)
    return records


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def display_id(value: dict[str, Any]) -> str:
    return str(value.get("publicQuestionId") or value.get("originalQuestionId") or value.get("reviewQuestionId") or "")


def identity_key_id(value: dict[str, Any]) -> str:
    return str(value.get("reviewQuestionId") or value.get("publicQuestionId") or value.get("originalQuestionId") or "")


def item_key(value: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(value.get("qualification") or ""),
        str(value.get("examYear") or ""),
        str(value.get("questionLabel") or ""),
        identity_key_id(value),
    )


def compact_text(value: Any, limit: int = 140) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def normalize_law_ref(ref: dict[str, Any]) -> dict[str, Any]:
    law_id = ref.get("lawId") or ref.get("law_id") or ref.get("lawNumber") or ref.get("law_number")
    law_name = ref.get("lawTitle") or ref.get("lawName") or ref.get("law_name") or ref.get("LawName")
    article = ref.get("article") or ref.get("articleNumber") or ref.get("article_number")
    paragraph = ref.get("paragraph") or ref.get("paragraphNumber") or ref.get("paragraph_number")
    item = ref.get("item") or ref.get("itemNumber") or ref.get("item_number")
    address = ref.get("address") or ref.get("representative_address")
    kanjiaddress = ref.get("kanjiaddress") or ref.get("representative_kanjiaddress")
    status = ref.get("verificationStatus") or ref.get("status")
    return {
        key: value
        for key, value in {
            "lawId": law_id,
            "lawName": law_name,
            "article": article,
            "paragraph": paragraph,
            "item": item,
            "address": address,
            "kanjiaddress": kanjiaddress,
            "verificationStatus": status,
        }.items()
        if value not in (None, "", [], {})
    }


def existing_law_refs(question: dict[str, Any]) -> list[dict[str, Any]]:
    refs = question.get("lawReferences")
    if not isinstance(refs, list):
        return []
    return [normalize_law_ref(ref) for ref in refs if isinstance(ref, dict)]


def candidate_law_refs(record: dict[str, Any], *, limit: int = 3) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for candidate in record.get("topCandidates") or []:
        if not isinstance(candidate, dict):
            continue
        law_id = candidate.get("law_id")
        law_name = candidate.get("law_name") or candidate.get("LawName")
        address = candidate.get("representative_address") or candidate.get("address")
        kanjiaddress = candidate.get("representative_kanjiaddress") or candidate.get("kanjiaddress")
        refs.append(
            {
                key: value
                for key, value in {
                    "lawId": law_id,
                    "lawName": law_name,
                    "address": address,
                    "kanjiaddress": kanjiaddress,
                    "title": candidate.get("representative_title") or candidate.get("title"),
                    "snippet": compact_text(candidate.get("snippet"), 120),
                    "totalCount": candidate.get("total_count"),
                }.items()
                if value not in (None, "", [], {})
            }
        )
        if len(refs) >= limit:
            break
    return refs


def locator_key(ref: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(ref.get("lawId") or ""),
        str(ref.get("address") or ""),
        str(ref.get("kanjiaddress") or ""),
    )


def classify_choice(record: dict[str, Any], existing_refs: list[dict[str, Any]], candidate_refs: list[dict[str, Any]]) -> str:
    if record.get("lawzillaStatus") == "error":
        return "lawzilla_call_error"
    if not candidate_refs and not record.get("seedArticleCandidates"):
        return "lawzilla_no_hit"
    if not existing_refs:
        return "no_existing_law_reference__candidate_available"
    existing_keys = {locator_key(ref) for ref in existing_refs}
    candidate_keys = {locator_key(ref) for ref in candidate_refs}
    if existing_keys & candidate_keys:
        return "same_locator_candidate"
    existing_laws = {key[0] for key in existing_keys if key[0]}
    candidate_laws = {key[0] for key in candidate_keys if key[0]}
    if existing_laws & candidate_laws:
        return "same_law_different_locator"
    return "candidate_new_or_conflicting_law"


def recommended_action(classification: str) -> str:
    if classification == "same_locator_candidate":
        return "compare article text hash and promote to verified lawReferences if primary evidence matches"
    if classification == "same_law_different_locator":
        return "check whether Lawzilla locator gives finer paragraph/item granularity before updating lawReferences"
    if classification == "no_existing_law_reference__candidate_available":
        return "verify candidate against primary evidence before adding candidate/verified lawReferences"
    if classification == "lawzilla_no_hit":
        return "review existing explanation/source and improve query terms or mark as non-law-related after manual check"
    if classification == "lawzilla_call_error":
        return "retry Lawzilla candidate collection for this choice"
    return "manual secondary review required before changing question artifacts"


def build_lawzilla_review(
    *,
    repo_root: Path,
    queue_items: list[dict[str, Any]],
    candidate_records: list[dict[str, Any]],
    generated_at: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    queue_by_key = {item_key(item): item for item in queue_items}
    question_cache: dict[tuple[str, str, str, str], tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    review_records: list[dict[str, Any]] = []
    missing_queue_count = 0

    for record in candidate_records:
        key = item_key(record)
        item = queue_by_key.get(key)
        if item is None:
            missing_queue_count += 1
            item = record
        if key not in question_cache:
            try:
                question = stage_question(item, repo_root)
                refs = existing_law_refs(question)
            except Exception as exc:  # noqa: BLE001
                question = {}
                refs = []
                question_cache[key] = ({"stageQuestionLoadError": str(exc)}, refs)
            else:
                question_cache[key] = (question, refs)
        question, refs = question_cache[key]
        candidate_refs = candidate_law_refs(record)
        classification = classify_choice(record, refs, candidate_refs)
        review_records.append(
            {
                "schemaVersion": "gas-shunin-lawzilla-evidence-comparison/v1",
                "generatedAt": generated_at,
                "queueSequence": record.get("queueSequence"),
                "priority": record.get("priority"),
                "targetTracks": record.get("targetTracks"),
                "qualification": record.get("qualification"),
                "examYear": record.get("examYear"),
                "questionLabel": record.get("questionLabel"),
                "choiceIndex": record.get("choiceIndex"),
                "displayQuestionId": display_id(record),
                "publicQuestionId": record.get("publicQuestionId"),
                "originalQuestionId": record.get("originalQuestionId"),
                "reviewQuestionId": record.get("reviewQuestionId"),
                "sourceFile": record.get("sourceFile"),
                "stagePatchFiles": record.get("stagePatchFiles"),
                "choiceText": compact_text(record.get("choiceText"), 240),
                "correctChoiceText": record.get("correctChoiceText"),
                "existingLawReferenceCount": len(refs),
                "existingLawReferences": refs[:5],
                "lawzillaStatus": record.get("lawzillaStatus"),
                "lawzillaResultCount": record.get("resultCount"),
                "lawzillaTotalResults": record.get("totalResults"),
                "lawzillaResponseHash": record.get("lawzillaResponseHash"),
                "lawzillaSearchParams": record.get("lawzillaSearchParams"),
                "candidateLawReferences": candidate_refs,
                "seedArticleTargetCount": len(record.get("seedArticleCandidates") or []),
                "comparisonClassification": classification,
                "recommendedAction": recommended_action(classification),
                "workflowDecision": "candidate_only_do_not_patch_question_until_primary_evidence_verified",
                "stageQuestionLoadError": question.get("stageQuestionLoadError") if isinstance(question, dict) else None,
            }
        )

    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in review_records:
        grouped[item_key(record)].append(record)
    question_records: list[dict[str, Any]] = []
    for key, records in sorted(grouped.items(), key=lambda item: (min(r.get("queueSequence") or 999999 for r in item[1]), item[0])):
        classifications = Counter(str(record.get("comparisonClassification")) for record in records)
        candidate_locators = sorted(
            {
                "{} {}".format(ref.get("lawId", ""), ref.get("kanjiaddress") or ref.get("address") or "").strip()
                for record in records
                for ref in record.get("candidateLawReferences") or []
                if ref.get("lawId") or ref.get("kanjiaddress") or ref.get("address")
            }
        )
        first = records[0]
        question_records.append(
            {
                "schemaVersion": "gas-shunin-lawzilla-evidence-question-summary/v1",
                "generatedAt": generated_at,
                "queueSequence": first.get("queueSequence"),
                "priority": first.get("priority"),
                "targetTracks": first.get("targetTracks"),
                "qualification": first.get("qualification"),
                "examYear": first.get("examYear"),
                "questionLabel": first.get("questionLabel"),
                "displayQuestionId": first.get("displayQuestionId"),
                "sourceFile": first.get("sourceFile"),
                "choiceRecordCount": len(records),
                "choicesWithCandidates": sum(bool(record.get("candidateLawReferences")) for record in records),
                "choicesWithoutCandidates": sum(not bool(record.get("candidateLawReferences")) for record in records),
                "existingLawReferenceCount": max(int(record.get("existingLawReferenceCount") or 0) for record in records),
                "comparisonClassifications": dict(sorted(classifications.items())),
                "candidateLawLocatorCount": len(candidate_locators),
                "candidateLawLocators": candidate_locators[:20],
                "recommendedAction": "verify candidate law locators against primary evidence before patching question documents",
            }
        )

    classification_counts = Counter(str(record.get("comparisonClassification")) for record in review_records)
    summary = {
        "schemaVersion": "gas-shunin-lawzilla-evidence-comparison-summary/v1",
        "generatedAt": generated_at,
        "choiceRecordCount": len(review_records),
        "questionRecordCount": len(question_records),
        "missingQueueRecordCount": missing_queue_count,
        "classificationCounts": dict(sorted(classification_counts.items())),
        "choicesWithExistingLawReferences": sum(1 for record in review_records if record.get("existingLawReferenceCount")),
        "choicesWithoutExistingLawReferences": sum(1 for record in review_records if not record.get("existingLawReferenceCount")),
        "choicesWithCandidateLawReferences": sum(bool(record.get("candidateLawReferences")) for record in review_records),
        "choicesWithoutCandidateLawReferences": sum(not bool(record.get("candidateLawReferences")) for record in review_records),
        "questionCountWithAllChoicesCandidateAvailable": sum(
            1 for record in question_records if record.get("choicesWithoutCandidates") == 0
        ),
        "boundary": "comparison artifact only; no 00_source, correctChoiceText, explanationText, lawReferences, lawRevisionFacts, or existing Firestore IDs were modified.",
        "nextAction": "verify candidate law locators against primary evidence and only then materialize candidate or verified lawReferences.",
    }
    return review_records, question_records, summary


def parse_answer_count(text: Any) -> int | None:
    match = re.search(r"正解は\s*([0-9]+)", str(text or ""))
    if not match:
        return None
    return int(match.group(1))


def compact_choices(correct_compact: Any) -> list[str]:
    return [part.strip() for part in str(correct_compact or "").split("|") if part.strip()]


def is_count_question(item: dict[str, Any]) -> bool:
    return "いくつ" in str(item.get("questionBodyText") or "")


def derived_count(correct_compact: Any, intent: Any) -> int | None:
    choices = compact_choices(correct_compact)
    if not choices:
        return None
    if intent == "select_correct":
        return sum(1 for part in choices if part == "正しい")
    if intent == "select_incorrect":
        return sum(1 for part in choices if part == "間違い")
    return None


def expected_label_for_intent(intent: Any) -> str | None:
    if intent == "select_correct":
        return "正しい"
    if intent == "select_incorrect":
        return "間違い"
    return None


def answer_result_interpretation(item: dict[str, Any]) -> str:
    if parse_answer_count(item.get("answerResultText")) is None:
        return "unavailable"
    return "count" if is_count_question(item) else "choice_index"


def count_mapping_status(item: dict[str, Any]) -> str:
    answer_value = parse_answer_count(item.get("answerResultText"))
    choices = compact_choices(item.get("correctChoiceTextCompact"))
    if answer_value is None:
        return "answer_result_unavailable"
    if not choices:
        return "correctChoiceTextCompact_unavailable"
    if is_count_question(item):
        inferred_count = derived_count(item.get("correctChoiceTextCompact"), item.get("questionIntent"))
        if inferred_count is None:
            return "derived_count_unavailable"
        if answer_value == inferred_count:
            return "count_matches_correctChoiceTextCompact"
        return "count_mismatch_needs_review"
    expected_label = expected_label_for_intent(item.get("questionIntent"))
    if expected_label is None:
        return "question_intent_unhandled"
    if answer_value < 1 or answer_value > len(choices):
        return "answer_index_out_of_range_needs_review"
    if choices[answer_value - 1] == expected_label:
        return "answer_index_matches_correctChoiceTextCompact"
    return "answer_index_mismatch_needs_review"


def mismatch_status(status: str) -> bool:
    return status in {
        "count_mismatch_needs_review",
        "answer_index_out_of_range_needs_review",
        "answer_index_mismatch_needs_review",
    }


def derived_answer_value(item: dict[str, Any]) -> int | None:
    if is_count_question(item):
        return derived_count(item.get("correctChoiceTextCompact"), item.get("questionIntent"))
    expected_label = expected_label_for_intent(item.get("questionIntent"))
    if expected_label is None:
        return None
    matches = [index + 1 for index, choice in enumerate(compact_choices(item.get("correctChoiceTextCompact"))) if choice == expected_label]
    if len(matches) == 1:
        return matches[0]
    return None


def derived_answer_interpretation(item: dict[str, Any]) -> str:
    return "count" if is_count_question(item) else "choice_index"


def p2_review_priority(item: dict[str, Any], status: str) -> str:
    tracks = set(item.get("targetTracks") or [])
    if mismatch_status(status):
        return "P2A_answer_mapping_mismatch"
    if "answer_recheck" in tracks:
        return "P2B_answer_recheck_confirm_mapping"
    if "source_conflict_review" in tracks:
        return "P2C_source_conflict_review"
    return "P2D_manual_review"


def build_p2_review(*, p2_items: list[dict[str, Any]], generated_at: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in p2_items:
        status = count_mapping_status(item)
        answer_value = parse_answer_count(item.get("answerResultText"))
        inferred_value = derived_answer_value(item)
        records.append(
            {
                "schemaVersion": "gas-shunin-p2-source-answer-review-priority/v1",
                "generatedAt": generated_at,
                "queueSequence": item.get("queueSequence"),
                "priority": item.get("priority"),
                "reviewPriority": p2_review_priority(item, status),
                "targetTracks": item.get("targetTracks"),
                "targetReasons": item.get("targetReasons"),
                "qualification": item.get("qualification"),
                "examYear": item.get("examYear"),
                "questionLabel": item.get("questionLabel"),
                "displayQuestionId": display_id(item),
                "publicQuestionId": item.get("publicQuestionId"),
                "originalQuestionId": item.get("originalQuestionId"),
                "reviewQuestionId": item.get("reviewQuestionId"),
                "sourceCategory": item.get("sourceCategory"),
                "questionIntent": item.get("questionIntent"),
                "questionType": item.get("questionType"),
                "questionBodyText": compact_text(item.get("questionBodyText"), 240),
                "answerResultText": item.get("answerResultText"),
                "answerResultValue": answer_value,
                "answerResultInterpretation": answer_result_interpretation(item),
                "correctChoiceTextCompact": item.get("correctChoiceTextCompact"),
                "derivedAnswerValueFromCorrectChoiceTextCompact": inferred_value,
                "derivedAnswerValueInterpretation": derived_answer_interpretation(item),
                "countMappingStatus": status,
                "sourceFile": item.get("sourceFile"),
                "stagePatchFiles": item.get("stagePatchFiles"),
                "recommendedWorkflowSteps": item.get("recommendedWorkflowSteps"),
                "stopIf": item.get("stopIf"),
                "workflowDecision": "review_only_do_not_change_correctChoiceText_without_source_or_tertiary_evidence",
            }
        )
    summary = {
        "schemaVersion": "gas-shunin-p2-source-answer-review-priority-summary/v1",
        "generatedAt": generated_at,
        "itemCount": len(records),
        "reviewPriorityCounts": dict(sorted(Counter(record["reviewPriority"] for record in records).items())),
        "countMappingStatusCounts": dict(sorted(Counter(record["countMappingStatus"] for record in records).items())),
        "trackCounts": dict(
            sorted(
                Counter(track for record in records for track in (record.get("targetTracks") or [])).items()
            )
        ),
        "boundary": "review prioritization only; no 00_source, correctChoiceText, explanationText, lawReferences, lawRevisionFacts, or existing Firestore IDs were modified.",
        "nextAction": "start with P2A mismatches if any, then P2B answer recheck records, then P2C source conflict records.",
    }
    return records, summary


def write_lawzilla_markdown(path: Path, summary: dict[str, Any], question_records: list[dict[str, Any]]) -> None:
    lines = [
        "# Gas shunin Lawzilla evidence comparison",
        "",
        f"- generatedAt: {summary['generatedAt']}",
        f"- questionRecordCount: {summary['questionRecordCount']}",
        f"- choiceRecordCount: {summary['choiceRecordCount']}",
        f"- choicesWithCandidateLawReferences: {summary['choicesWithCandidateLawReferences']}",
        f"- choicesWithoutCandidateLawReferences: {summary['choicesWithoutCandidateLawReferences']}",
        f"- choicesWithExistingLawReferences: {summary['choicesWithExistingLawReferences']}",
        f"- choicesWithoutExistingLawReferences: {summary['choicesWithoutExistingLawReferences']}",
        "",
        "## Classification Counts",
        "",
        "| classification | count |",
        "| --- | ---: |",
    ]
    for key, value in summary["classificationCounts"].items():
        lines.append(f"| `{key}` | {value} |")
    lines.extend(["", "## First Questions Needing Primary Evidence", "", "| seq | priority | qualification | year | label | id | choices with candidates | choices without candidates |", "| ---: | --- | --- | ---: | --- | --- | ---: | ---: |"])
    for record in question_records[:80]:
        lines.append(
            "| {} | {} | `{}` | {} | {} | `{}` | {} | {} |".format(
                record.get("queueSequence"),
                record.get("priority"),
                record.get("qualification"),
                record.get("examYear"),
                record.get("questionLabel"),
                record.get("displayQuestionId"),
                record.get("choicesWithCandidates"),
                record.get("choicesWithoutCandidates"),
            )
        )
    lines.extend(["", "## Boundary", "", f"- {summary['boundary']}", f"- {summary['nextAction']}"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_p2_markdown(path: Path, summary: dict[str, Any], records: list[dict[str, Any]]) -> None:
    lines = [
        "# Gas shunin P2 source/answer review priority",
        "",
        f"- generatedAt: {summary['generatedAt']}",
        f"- itemCount: {summary['itemCount']}",
        "",
        "## Review Priority Counts",
        "",
        "| priority | count |",
        "| --- | ---: |",
    ]
    for key, value in summary["reviewPriorityCounts"].items():
        lines.append(f"| `{key}` | {value} |")
    lines.extend(["", "## Count Mapping Status", "", "| status | count |", "| --- | ---: |"])
    for key, value in summary["countMappingStatusCounts"].items():
        lines.append(f"| `{key}` | {value} |")
    lines.extend(["", "## Items", "", "| seq | reviewPriority | qualification | year | label | id | countStatus | tracks |", "| ---: | --- | --- | ---: | --- | --- | --- | --- |"])
    for record in records[:120]:
        lines.append(
            "| {} | `{}` | `{}` | {} | {} | `{}` | `{}` | {} |".format(
                record.get("queueSequence"),
                record.get("reviewPriority"),
                record.get("qualification"),
                record.get("examYear"),
                record.get("questionLabel"),
                record.get("displayQuestionId"),
                record.get("countMappingStatus"),
                ", ".join(record.get("targetTracks") or []),
            )
        )
    lines.extend(["", "## Boundary", "", f"- {summary['boundary']}", f"- {summary['nextAction']}"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build gas-shunin Lawzilla review artifacts from candidate sidecars.")
    parser.add_argument("--queue", required=True, help="Latest workflow queue JSONL.")
    parser.add_argument("--candidate-jsonl", action="append", required=True, help="Lawzilla candidate JSONL. Repeatable.")
    parser.add_argument("--p2-batch", required=True, help="P2 batch JSON.")
    parser.add_argument("--output-dir", required=True, help="Output directory.")
    parser.add_argument("--timestamp", required=True, help="Artifact timestamp prefix.")
    parser.add_argument("--repo-root", default=".", help="Repository root.")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve()
    queue_path = Path(args.queue).expanduser()
    if not queue_path.is_absolute():
        queue_path = repo_root / queue_path
    p2_batch_path = Path(args.p2_batch).expanduser()
    if not p2_batch_path.is_absolute():
        p2_batch_path = repo_root / p2_batch_path
    output_dir = Path(args.output_dir).expanduser()
    if not output_dir.is_absolute():
        output_dir = repo_root / output_dir
    generated_at = now_jst()

    queue_items = load_jsonl(queue_path)
    candidate_records: list[dict[str, Any]] = []
    for raw_path in args.candidate_jsonl:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = repo_root / path
        candidate_records.extend(load_jsonl(path))

    lawzilla_records, question_records, lawzilla_summary = build_lawzilla_review(
        repo_root=repo_root,
        queue_items=queue_items,
        candidate_records=candidate_records,
        generated_at=generated_at,
    )
    p2_payload = load_json(p2_batch_path)
    p2_items = [item for item in p2_payload.get("items", []) if isinstance(item, dict)]
    p2_records, p2_summary = build_p2_review(p2_items=p2_items, generated_at=generated_at)

    lawzilla_jsonl = output_dir / f"{args.timestamp}_gas_shunin_lawzilla_evidence_comparison.jsonl"
    lawzilla_questions_jsonl = output_dir / f"{args.timestamp}_gas_shunin_lawzilla_evidence_question_summary.jsonl"
    lawzilla_summary_json = output_dir / f"{args.timestamp}_gas_shunin_lawzilla_evidence_comparison_summary.json"
    lawzilla_summary_md = output_dir / f"{args.timestamp}_gas_shunin_lawzilla_evidence_comparison_summary.md"
    p2_jsonl = output_dir / f"{args.timestamp}_gas_shunin_P2_source_answer_review_priority.jsonl"
    p2_summary_json = output_dir / f"{args.timestamp}_gas_shunin_P2_source_answer_review_priority_summary.json"
    p2_summary_md = output_dir / f"{args.timestamp}_gas_shunin_P2_source_answer_review_priority_summary.md"

    lawzilla_summary.update(
        {
            "queuePath": str(queue_path.relative_to(repo_root) if queue_path.is_relative_to(repo_root) else queue_path),
            "candidateJsonlPaths": [
                str((repo_root / raw_path).relative_to(repo_root)) if not Path(raw_path).is_absolute() else raw_path
                for raw_path in args.candidate_jsonl
            ],
            "reviewJsonl": str(lawzilla_jsonl.relative_to(repo_root)),
            "questionSummaryJsonl": str(lawzilla_questions_jsonl.relative_to(repo_root)),
            "summaryJson": str(lawzilla_summary_json.relative_to(repo_root)),
            "summaryMarkdown": str(lawzilla_summary_md.relative_to(repo_root)),
        }
    )
    p2_summary.update(
        {
            "p2BatchPath": str(p2_batch_path.relative_to(repo_root) if p2_batch_path.is_relative_to(repo_root) else p2_batch_path),
            "reviewJsonl": str(p2_jsonl.relative_to(repo_root)),
            "summaryJson": str(p2_summary_json.relative_to(repo_root)),
            "summaryMarkdown": str(p2_summary_md.relative_to(repo_root)),
        }
    )

    write_jsonl(lawzilla_jsonl, lawzilla_records)
    write_jsonl(lawzilla_questions_jsonl, question_records)
    write_json(lawzilla_summary_json, lawzilla_summary)
    write_lawzilla_markdown(lawzilla_summary_md, lawzilla_summary, question_records)
    write_jsonl(p2_jsonl, p2_records)
    write_json(p2_summary_json, p2_summary)
    write_p2_markdown(p2_summary_md, p2_summary, p2_records)

    print(
        json.dumps(
            {
                "lawzilla": {
                    "choiceRecordCount": lawzilla_summary["choiceRecordCount"],
                    "questionRecordCount": lawzilla_summary["questionRecordCount"],
                    "classificationCounts": lawzilla_summary["classificationCounts"],
                },
                "p2": {
                    "itemCount": p2_summary["itemCount"],
                    "reviewPriorityCounts": p2_summary["reviewPriorityCounts"],
                    "countMappingStatusCounts": p2_summary["countMappingStatusCounts"],
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
