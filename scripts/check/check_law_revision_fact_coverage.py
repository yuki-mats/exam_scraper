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


def facts_for_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    facts = record.get("lawRevisionFacts")
    if isinstance(facts, dict):
        return [facts]
    if isinstance(facts, list):
        return [entry for entry in facts if isinstance(entry, dict)]
    return []


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
            law_grounded = record.get("lawGroundedExplanationNotNeeded")
            if law_grounded is True:
                errors.append(
                    f"{label}: lawGroundedExplanationNotNeeded cannot be true when isLawRelated=true"
                )
            facts_list = facts_for_record(record)
            if not facts_list:
                counts["missing"] += 1
                if require_all_law_related:
                    errors.append(f"{label}: missing lawRevisionFacts for law-related record")
                continue
            counts["with_facts"] += 1
            for facts_index, facts in enumerate(facts_list, start=1):
                if not _is_law_revision_facts(facts):
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
    report: Path | None,
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

    errors, counts = audit_records(
        records,
        require_all_law_related=require_all_law_related,
        fail_on_hold=fail_on_hold,
        require_evidence_summary=require_evidence_summary,
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
    parser.add_argument("--report", type=Path, help="Optional JSON report output path.")
    args = parser.parse_args()
    return run(
        list_group_dir=args.list_group_dir,
        stage=args.stage,
        require_all_law_related=args.require_all_law_related,
        fail_on_hold=args.fail_on_hold,
        require_evidence_summary=args.require_evidence_summary,
        report=args.report,
    )


if __name__ == "__main__":
    raise SystemExit(main())
