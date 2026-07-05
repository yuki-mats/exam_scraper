#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.check.check_law_revision_fact_coverage import (  # noqa: E402
    facts_for_record,
    latest_firestore_file,
)


QUEUE_SCHEMA_VERSION = "law-revision-audit-queue/v1"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, 1):
            stripped = line.strip()
            if not stripped:
                continue
            entry = json.loads(stripped)
            if not isinstance(entry, dict):
                raise ValueError(f"{path}:{line_number}: JSONL entry must be an object")
            records.append(entry)
    return records


def firestore_records(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path)
    if isinstance(payload, dict) and isinstance(payload.get("questions"), list):
        return [entry for entry in payload["questions"] if isinstance(entry, dict)]
    if isinstance(payload, list):
        return [entry for entry in payload if isinstance(entry, dict)]
    return []


def text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def locator_key(value: dict[str, Any], *, include_parts: bool) -> tuple[str, ...]:
    keys = ["lawId", "article"]
    if include_parts:
        keys.extend(["paragraph", "item", "subitem"])
    return tuple(text(value.get(key)) for key in keys)


def build_snapshot_index(snapshots: list[dict[str, Any]]) -> tuple[dict[tuple[str, ...], dict[str, Any]], dict[tuple[str, ...], list[dict[str, Any]]]]:
    exact: dict[tuple[str, ...], dict[str, Any]] = {}
    article: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for snapshot in snapshots:
        if text(snapshot.get("status")) != "fetched":
            continue
        exact_key = locator_key(snapshot, include_parts=True)
        article_key = locator_key(snapshot, include_parts=False)
        if all(exact_key[:2]) and exact_key not in exact:
            exact[exact_key] = snapshot
        if all(article_key):
            article.setdefault(article_key, []).append(snapshot)
    return exact, article


def match_snapshot(
    ref: dict[str, Any],
    *,
    exact_index: dict[tuple[str, ...], dict[str, Any]],
    article_index: dict[tuple[str, ...], list[dict[str, Any]]],
    question_id: str,
) -> tuple[str, dict[str, Any] | None]:
    exact_key = locator_key(ref, include_parts=True)
    if all(exact_key[:2]) and exact_key in exact_index:
        return "exact", exact_index[exact_key]

    article_key = locator_key(ref, include_parts=False)
    candidates = article_index.get(article_key, [])
    if not candidates:
        return "missing", None
    for candidate in candidates:
        question_ids = candidate.get("questionIds")
        if isinstance(question_ids, list) and question_id in {str(item) for item in question_ids}:
            return "article_question", candidate
    if len(candidates) == 1:
        return "article", candidates[0]
    return "ambiguous_article", None


def summarize_snapshot(snapshot: dict[str, Any] | None, *, snippet_chars: int) -> dict[str, Any]:
    if snapshot is None:
        return {}
    article_text = text(snapshot.get("articleText"))
    summary = {
        "status": snapshot.get("status"),
        "lawId": snapshot.get("lawId"),
        "lawTitle": snapshot.get("lawTitle"),
        "article": snapshot.get("article"),
        "paragraph": snapshot.get("paragraph"),
        "item": snapshot.get("item"),
        "subitem": snapshot.get("subitem"),
        "referenceDate": snapshot.get("referenceDate"),
        "apiUrl": snapshot.get("apiUrl"),
        "articleTextHash": snapshot.get("articleTextHash"),
        "rawXmlHash": snapshot.get("rawXmlHash"),
        "rawXmlPath": snapshot.get("rawXmlPath"),
    }
    if article_text and snippet_chars > 0:
        summary["articleTextSnippet"] = article_text[:snippet_chars]
    return {key: value for key, value in summary.items() if value not in ("", None)}


def compact_ref(ref: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "role",
        "scope",
        "lawTitle",
        "lawId",
        "lawAlias",
        "article",
        "paragraph",
        "item",
        "subitem",
        "referenceDate",
        "reason",
        "verificationStatus",
        "comparisonStatus",
        "differenceNote",
        "choiceIndex",
    )
    return {key: ref.get(key) for key in keys if ref.get(key) not in ("", None)}


def build_group_ref_index(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        original_id = text(record.get("originalQuestionId"))
        if not original_id:
            continue
        refs = [entry for entry in record.get("lawReferences", []) if isinstance(entry, dict)]
        if refs:
            index.setdefault(original_id, []).extend(refs)
    return index


def unique_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for ref in refs:
        key = json.dumps(compact_ref(ref), ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        results.append(ref)
    return results


def current_status(record: dict[str, Any]) -> str:
    facts = facts_for_record(record)
    if not facts:
        return "missing_lawRevisionFacts"
    statuses = [text(fact.get("auditStatus")) for fact in facts if text(fact.get("auditStatus"))]
    if "hold" in statuses:
        return "hold"
    return "already_facted"


def should_include(record: dict[str, Any], *, include_existing: bool, include_hold: bool) -> bool:
    if record.get("isLawRelated") is not True:
        return False
    status = current_status(record)
    if status == "missing_lawRevisionFacts":
        return True
    if include_hold and status == "hold":
        return True
    return include_existing


def build_queue_record(
    record: dict[str, Any],
    *,
    source_file: Path,
    group_ref_index: dict[str, list[dict[str, Any]]],
    exact_index: dict[tuple[str, ...], dict[str, Any]],
    article_index: dict[tuple[str, ...], list[dict[str, Any]]],
    snippet_chars: int,
) -> tuple[dict[str, Any], Counter[str]]:
    counts: Counter[str] = Counter()
    question_id = text(record.get("questionId"))
    law_refs = [entry for entry in record.get("lawReferences", []) if isinstance(entry, dict)]
    law_references_source = "record"
    evidence_refs: list[dict[str, Any]] = []
    if not law_refs:
        counts["records_with_no_law_references"] += 1
        fallback_refs = unique_refs(group_ref_index.get(text(record.get("originalQuestionId")), []))
        if fallback_refs:
            law_refs = fallback_refs
            law_references_source = "same_original_question_fallback"
            counts["records_using_group_law_reference_fallback"] += 1
        else:
            counts["records_with_snapshot_gap"] += 1
    for ref_index, ref in enumerate(law_refs, start=1):
        match_level, snapshot = match_snapshot(
            ref,
            exact_index=exact_index,
            article_index=article_index,
            question_id=question_id,
        )
        counts[f"snapshot_{match_level}"] += 1
        evidence_ref = {
            "refIndex": ref_index,
            "matchLevel": match_level,
            "lawReferencesSource": law_references_source,
            "lawReference": compact_ref(ref),
            "snapshot": summarize_snapshot(snapshot, snippet_chars=snippet_chars),
        }
        evidence_refs.append(evidence_ref)

    status = current_status(record)
    counts[status] += 1
    if any(ref["matchLevel"] in {"missing", "ambiguous_article"} for ref in evidence_refs):
        counts["records_with_snapshot_gap"] += 1

    queue_record = {
        "schemaVersion": QUEUE_SCHEMA_VERSION,
        "sourceFile": str(source_file),
        "auditReason": status,
        "questionId": question_id,
        "originalQuestionId": record.get("originalQuestionId"),
        "listGroupId": record.get("listGroupId"),
        "qualificationId": record.get("qualificationId"),
        "examYear": record.get("examYear"),
        "question": {
            "bodyText": record.get("questionBodyText"),
            "choiceText": record.get("questionText") or record.get("choiceText"),
            "correctChoiceText": record.get("correctChoiceText"),
            "explanationText": record.get("explanationText"),
        },
        "currentFacts": facts_for_record(record),
        "lawReferencesSource": law_references_source,
        "lawReferences": [compact_ref(ref) for ref in law_refs],
        "currentEvidence": {
            "snapshotSource": "current_article_snapshots",
            "refs": evidence_refs,
        },
        "auditTodo": {
            "decideAuditStatus": [
                "same_as_current",
                "updated_to_current_law",
                "hold",
                "not_law_related",
            ],
            "compareExamTimeAndCurrent": True,
            "doNotInferIfExamTimeLawUnknown": True,
            "requiredOutput": [
                "auditStatus",
                "examTime.correctChoiceText",
                "current.correctChoiceText",
                "differenceFacts",
                "answerImpactFacts",
                "evidenceSummary",
            ],
        },
    }
    return queue_record, counts


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            fh.write("\n")


def write_summary(path: Path, *, source_file: Path, snapshots_path: Path, counts: Counter[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schemaVersion": "law-revision-audit-queue-summary/v1",
        "sourceFile": str(source_file),
        "snapshotsFile": str(snapshots_path),
        "counts": dict(sorted(counts.items())),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_queue(
    *,
    list_group_dir: Path,
    snapshots_path: Path,
    output_path: Path,
    summary_path: Path | None,
    include_existing: bool,
    include_hold: bool,
    require_snapshots: bool,
    snippet_chars: int,
) -> tuple[int, Counter[str]]:
    source_file = latest_firestore_file(list_group_dir)
    if source_file is None:
        raise FileNotFoundError(f"no Firestore JSON under {list_group_dir / '40_convert'}")
    records = firestore_records(source_file)
    snapshots = load_jsonl(snapshots_path)
    exact_index, article_index = build_snapshot_index(snapshots)
    group_ref_index = build_group_ref_index(records)

    queue_records: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for record in records:
        if record.get("isLawRelated") is True:
            counts["law_related"] += 1
        if not should_include(record, include_existing=include_existing, include_hold=include_hold):
            continue
        queue_record, record_counts = build_queue_record(
            record,
            source_file=source_file,
            group_ref_index=group_ref_index,
            exact_index=exact_index,
            article_index=article_index,
            snippet_chars=snippet_chars,
        )
        queue_records.append(queue_record)
        counts.update(record_counts)
        counts["queued"] += 1

    if require_snapshots and counts.get("records_with_snapshot_gap", 0):
        raise RuntimeError(
            f"{counts['records_with_snapshot_gap']} queued record(s) have missing or ambiguous snapshots"
        )

    write_jsonl(output_path, queue_records)
    if summary_path is not None:
        write_summary(summary_path, source_file=source_file, snapshots_path=snapshots_path, counts=counts)
    return len(queue_records), counts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a review queue for law-related records missing lawRevisionFacts."
    )
    parser.add_argument("--list-group-dir", required=True, type=Path)
    parser.add_argument("--snapshots", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--include-existing", action="store_true")
    parser.add_argument("--include-hold", action="store_true")
    parser.add_argument("--require-snapshots", action="store_true")
    parser.add_argument("--snippet-chars", type=int, default=600)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        queued, counts = build_queue(
            list_group_dir=args.list_group_dir,
            snapshots_path=args.snapshots,
            output_path=args.output,
            summary_path=args.summary,
            include_existing=args.include_existing,
            include_hold=args.include_hold,
            require_snapshots=args.require_snapshots,
            snippet_chars=args.snippet_chars,
        )
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1
    print(f"queued: {queued}")
    print("counts: " + json.dumps(dict(sorted(counts.items())), ensure_ascii=False, sort_keys=True))
    print(f"output: {args.output}")
    if args.summary:
        print(f"summary: {args.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
