#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.common.repaso_firestore_schema import _is_law_revision_facts
from tools.question_bank.question_bank import timestamp_sort_key
from tools.question_review_console.law_audit_quality import (
    law_revision_current_verdict_issues,
)
from tools.question_review_console.explanation_quality import (
    law_evidence_utilization_issues,
)


LAW_REVISION_FACT_STATUSES = {
    "same_as_current",
    "updated_to_current_law",
    "hold",
    "not_law_related",
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def latest_firestore_file(list_group_dir: Path) -> Path | None:
    files = sorted((list_group_dir / "40_convert").glob("*_firestore_*.json"), key=timestamp_sort_key)
    return files[-1] if files else None


def latest_merged_files(list_group_dir: Path) -> list[Path]:
    merged_dir = list_group_dir / "30_merged_2"
    latest_by_source_stem: dict[str, Path] = {}
    for path in sorted(merged_dir.glob("*.json"), key=timestamp_sort_key):
        stem = path.stem
        if "_merged_" in stem:
            source_stem = stem.split("_merged_", 1)[0]
        elif stem.endswith("_merged"):
            source_stem = stem[: -len("_merged")]
        else:
            continue
        latest_by_source_stem[source_stem] = path
    return [latest_by_source_stem[key] for key in sorted(latest_by_source_stem)]


def firestore_records(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path)
    if isinstance(payload, dict) and isinstance(payload.get("questions"), list):
        return [entry for entry in payload["questions"] if isinstance(entry, dict)]
    if isinstance(payload, list):
        return [entry for entry in payload if isinstance(entry, dict)]
    return []


def merged_records(paths: Iterable[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        payload = load_json(path)
        if isinstance(payload, dict) and isinstance(payload.get("question_bodies"), list):
            records.extend(entry for entry in payload["question_bodies"] if isinstance(entry, dict))
        elif isinstance(payload, list):
            records.extend(entry for entry in payload if isinstance(entry, dict))
    return records


def has_non_empty_law_references(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value)
    if isinstance(value, list):
        return any(has_non_empty_law_references(entry) for entry in value)
    return False


def has_verified_law_reference(value: Any) -> bool:
    if isinstance(value, dict):
        return value.get("verificationStatus") == "verified" or any(
            has_verified_law_reference(entry) for entry in value.values()
        )
    if isinstance(value, list):
        return any(has_verified_law_reference(entry) for entry in value)
    return False


def original_question_id(record: dict[str, Any]) -> str:
    for key in (
        "originalQuestionId",
        "original_question_id",
        "public_question_id",
    ):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def select_original_questions(
    records: list[dict[str, Any]],
    requested_ids: Iterable[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    requested = {
        str(value).strip() for value in requested_ids if str(value).strip()
    }
    if not requested:
        return records, []
    selected = [
        record
        for record in records
        if original_question_id(record) in requested
    ]
    found = {original_question_id(record) for record in selected}
    return selected, sorted(requested - found)


def _combined_field(records: list[dict[str, Any]], field: str) -> list[Any]:
    values: list[Any] = []
    for record in records:
        value = record.get(field)
        if isinstance(value, list):
            values.extend(value)
        elif value is not None:
            values.append(value)
    return values


def audit_question_level_law_evidence(
    records: list[dict[str, Any]],
    *,
    require_law_references: bool = False,
    require_verified_law_references: bool = False,
    require_public_law_evidence: bool = False,
) -> list[str]:
    """Apply question-level evidence rules to merged or choice-split records."""

    groups: dict[str, list[dict[str, Any]]] = {}
    for index, record in enumerate(records, start=1):
        key = original_question_id(record) or f"record:{index}"
        groups.setdefault(key, []).append(record)

    errors: list[str] = []
    for key, group in groups.items():
        law_records = [
            record for record in group if record.get("isLawRelated") is True
        ]
        if not law_records:
            continue
        label = (
            f"originalQuestionId={key}"
            if not key.startswith("record:")
            else record_label(law_records[0], int(key.removeprefix("record:")))
        )
        law_references = _combined_field(law_records, "lawReferences")
        has_law_references = has_non_empty_law_references(law_references)
        if require_law_references and not has_law_references:
            errors.append(f"{label}: missing lawReferences for law-related question")
        if require_verified_law_references and not has_verified_law_reference(
            law_references
        ):
            errors.append(
                f"{label}: no verified lawReferences for law-related question"
            )
        if require_public_law_evidence:
            combined = {
                "isLawRelated": True,
                "lawReferences": law_references,
                "lawRevisionFacts": _combined_field(
                    law_records, "lawRevisionFacts"
                ),
                "explanationText": _combined_field(
                    law_records, "explanationText"
                ),
                "suggestedQuestions": _combined_field(
                    law_records, "suggestedQuestions"
                ),
                "suggestedQuestionDetails": _combined_field(
                    law_records, "suggestedQuestionDetails"
                ),
            }
            errors.extend(
                f"{label}: {issue}"
                for issue in law_evidence_utilization_issues(
                    combined,
                    has_law_references=has_law_references,
                )
            )
    return errors


def facts_for_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    facts = record.get("lawRevisionFacts")
    if isinstance(facts, dict):
        return [facts]
    if isinstance(facts, list):
        return [entry for entry in facts if isinstance(entry, dict)]
    return []


def is_law_revision_facts_for_record(
    facts: dict[str, Any],
    record: dict[str, Any],
    *,
    allow_question_level_choice_verdicts: bool,
) -> bool:
    """Validate Firestore facts and question-level merged facts.

    Firestore records contain one scalar verdict.  Patch and merged records
    contain every choice verdict in one question-level list.  The Firestore
    schema validator deliberately accepts only the scalar representation, so
    normalize a well-formed merged verdict list solely for shape validation.
    The original list is still compared with the published verdict below by
    ``law_revision_current_verdict_issues``.
    """

    if _is_law_revision_facts(facts):
        return True
    if not allow_question_level_choice_verdicts:
        return False

    expected_verdicts = record.get("correctChoiceText")
    if (
        not isinstance(expected_verdicts, list)
        or not expected_verdicts
        or any(
            not isinstance(verdict, str) or not verdict.strip()
            for verdict in expected_verdicts
        )
    ):
        return False

    normalized_facts = dict(facts)
    normalized_any = False
    for snapshot_key in ("examTime", "current"):
        snapshot = facts.get(snapshot_key)
        if not isinstance(snapshot, dict):
            continue
        verdicts = snapshot.get("correctChoiceText")
        if not isinstance(verdicts, list):
            continue
        if (
            len(verdicts) != len(expected_verdicts)
            or any(
                not isinstance(verdict, str) or not verdict.strip()
                for verdict in verdicts
            )
        ):
            return False
        normalized_snapshot = dict(snapshot)
        normalized_snapshot["correctChoiceText"] = verdicts[0]
        normalized_facts[snapshot_key] = normalized_snapshot
        normalized_any = True

    return normalized_any and _is_law_revision_facts(normalized_facts)


def record_label(record: dict[str, Any], index: int) -> str:
    for key in ("questionId", "originalQuestionId", "original_question_id", "public_question_id"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"index:{index}"


def audit_records(
    records: list[dict[str, Any]],
    *,
    require_all_law_related: bool,
    fail_on_hold: bool,
    require_evidence_summary: bool,
    require_law_references: bool,
    require_current_correct_choice: bool = False,
    allow_question_level_choice_verdicts: bool = False,
) -> tuple[list[str], Counter[str]]:
    errors: list[str] = []
    counts: Counter[str] = Counter()
    for index, record in enumerate(records, start=1):
        label = record_label(record, index)
        is_law_related = record.get("isLawRelated")
        has_refs = has_non_empty_law_references(record.get("lawReferences"))
        if has_refs and is_law_related is not True:
            errors.append(f"{label}: lawReferences exists but isLawRelated is not true")
        if is_law_related is True:
            counts["law_related"] += 1
            if not has_refs:
                counts["missing_law_references"] += 1
                if require_law_references:
                    errors.append(f"{label}: missing lawReferences for law-related record")
            law_grounded = record.get("lawGroundedExplanationNotNeeded")
            if law_grounded is True:
                errors.append(
                    f"{label}: lawGroundedExplanationNotNeeded cannot be true when isLawRelated=true"
                )
            raw_facts = record.get("lawRevisionFacts")
            if isinstance(raw_facts, list):
                if not allow_question_level_choice_verdicts:
                    counts["invalid"] += 1
                    errors.append(
                        f"{label}: lawRevisionFacts must be an object for Firestore records"
                    )
                    continue
                if not raw_facts or any(
                    not isinstance(facts, dict) for facts in raw_facts
                ):
                    counts["invalid"] += 1
                    errors.append(
                        f"{label}: lawRevisionFacts must be a non-empty list of objects"
                    )
                    continue
            facts_list = facts_for_record(record)
            if not facts_list:
                counts["missing"] += 1
                if require_all_law_related:
                    errors.append(f"{label}: missing lawRevisionFacts for law-related record")
                continue
            counts["with_facts"] += 1
            for facts_index, facts in enumerate(facts_list, start=1):
                if not is_law_revision_facts_for_record(
                    facts,
                    record,
                    allow_question_level_choice_verdicts=(
                        allow_question_level_choice_verdicts
                    ),
                ):
                    errors.append(f"{label}: lawRevisionFacts[{facts_index}] is invalid")
                    continue
                status = facts.get("auditStatus")
                if status in LAW_REVISION_FACT_STATUSES:
                    counts[str(status)] += 1
                else:
                    errors.append(f"{label}: invalid auditStatus={status!r}")
                if fail_on_hold and status == "hold":
                    errors.append(f"{label}: lawRevisionFacts auditStatus is hold")
                if require_evidence_summary and not facts.get("evidenceSummary"):
                    errors.append(f"{label}: missing lawRevisionFacts.evidenceSummary")
            if require_current_correct_choice:
                errors.extend(
                    f"{label}: {issue['detail']}"
                    for issue in law_revision_current_verdict_issues(
                        correct_choice_text=record.get("correctChoiceText"),
                        law_revision_facts=record.get("lawRevisionFacts"),
                    )
                )
        elif is_law_related is False:
            counts["not_law_related"] += 1
        else:
            counts["unknown_law_related"] += 1
    return errors, counts


def write_report(path: Path, *, source_files: list[Path], counts: Counter[str], errors: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schemaVersion": "law-revision-fact-coverage/v1",
        "sourceFiles": [str(source_file) for source_file in source_files],
        "counts": dict(counts),
        "errors": errors,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(
    *,
    list_group_dir: Path,
    stage: str,
    require_all_law_related: bool,
    fail_on_hold: bool,
    require_evidence_summary: bool,
    require_law_references: bool,
    require_current_correct_choice: bool,
    require_verified_law_references: bool = False,
    require_public_law_evidence: bool = False,
    original_question_ids: Iterable[str] = (),
    report: Path | None = None,
) -> int:
    if stage == "firestore":
        source_file = latest_firestore_file(list_group_dir)
        if source_file is None:
            print(f"[ERROR] no Firestore JSON under {list_group_dir / '40_convert'}")
            return 2
        source_files = [source_file]
        records = firestore_records(source_file)
    elif stage == "merged":
        source_files = latest_merged_files(list_group_dir)
        if not source_files:
            print(f"[ERROR] no merged JSON under {list_group_dir / '30_merged_2'}")
            return 2
        records = merged_records(source_files)
    else:
        raise ValueError(f"unsupported stage: {stage}")

    records, missing_question_ids = select_original_questions(
        records,
        original_question_ids,
    )
    errors, counts = audit_records(
        records,
        require_all_law_related=require_all_law_related,
        fail_on_hold=fail_on_hold,
        require_evidence_summary=require_evidence_summary,
        require_law_references=False,
        require_current_correct_choice=require_current_correct_choice,
        allow_question_level_choice_verdicts=(stage == "merged"),
    )
    errors.extend(
        audit_question_level_law_evidence(
            records,
            require_law_references=require_law_references,
            require_verified_law_references=require_verified_law_references,
            require_public_law_evidence=require_public_law_evidence,
        )
    )
    errors.extend(
        f"originalQuestionId={question_id}: record not found"
        for question_id in missing_question_ids
    )
    print(f"stage: {stage}")
    for source_file in source_files:
        print(f"source: {source_file}")
    print("counts: " + json.dumps(dict(counts), ensure_ascii=False, sort_keys=True))
    if report is not None:
        write_report(report, source_files=source_files, counts=counts, errors=errors)
        print(f"report: {report}")
    if errors:
        for error in errors[:100]:
            print(f"[ERROR] {error}")
        if len(errors) > 100:
            print(f"... and {len(errors) - 100} more")
        return 1
    print("[OK] lawRevisionFacts coverage check passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Check lawRevisionFacts coverage on merged/Firestore data.")
    parser.add_argument("--list-group-dir", required=True, type=Path)
    parser.add_argument("--stage", choices=("merged", "firestore"), default="firestore")
    parser.add_argument(
        "--require-all-law-related",
        action="store_true",
        help="Fail when isLawRelated=true records do not have lawRevisionFacts.",
    )
    parser.add_argument(
        "--fail-on-hold",
        action="store_true",
        help="Fail when lawRevisionFacts.auditStatus=hold remains.",
    )
    parser.add_argument(
        "--require-evidence-summary",
        action="store_true",
        help="Fail when lawRevisionFacts.evidenceSummary is missing.",
    )
    parser.add_argument(
        "--require-law-references",
        action="store_true",
        help="Fail when an isLawRelated=true question has no lawReferences.",
    )
    parser.add_argument(
        "--require-current-correct-choice",
        action="store_true",
        help=(
            "Fail when lawRevisionFacts.current.correctChoiceText is missing "
            "or differs from the published verdict."
        ),
    )
    parser.add_argument(
        "--require-verified-law-references",
        action="store_true",
        help="Fail when scoped law-related questions have no verified lawReferences.",
    )
    parser.add_argument(
        "--require-public-law-evidence",
        action="store_true",
        help="Fail when scoped public explanations do not use law evidence.",
    )
    parser.add_argument(
        "--original-question-id",
        action="append",
        default=[],
        help="Limit validation to this original question ID. Repeatable.",
    )
    parser.add_argument("--report", type=Path, help="Optional JSON report output path.")
    args = parser.parse_args()
    return run(
        list_group_dir=args.list_group_dir,
        stage=args.stage,
        require_all_law_related=args.require_all_law_related,
        fail_on_hold=args.fail_on_hold,
        require_evidence_summary=args.require_evidence_summary,
        require_law_references=args.require_law_references,
        require_current_correct_choice=args.require_current_correct_choice,
        require_verified_law_references=args.require_verified_law_references,
        require_public_law_evidence=args.require_public_law_evidence,
        original_question_ids=args.original_question_id,
        report=args.report,
    )


if __name__ == "__main__":
    raise SystemExit(main())
