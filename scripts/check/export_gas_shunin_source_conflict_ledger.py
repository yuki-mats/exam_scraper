#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
GAS_SHUNIN_REPORT_DIR = ROOT_DIR / "output" / "gas-shunin" / "reports"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR))
    except ValueError:
        return str(path.resolve())


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def question_key_from_statement_key(statement_key: str) -> str:
    parts = statement_key.split(":")
    return ":".join(parts[:5]) if len(parts) >= 6 else statement_key


def severity_for(field: str) -> str:
    if "correctChoiceText" in field:
        return "high"
    if "choiceText" in field:
        return "medium"
    return "medium"


def conflict_id(statement_key: str, field: str, left: Any, right: Any) -> str:
    payload = json.dumps([statement_key, field, left, right], ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def build_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    samples = report.get("productionFirestoreVsArchivedSite", {}).get("normalizedMismatchSamples")
    if not isinstance(samples, list):
        return []
    rows: list[dict[str, Any]] = []
    generated_at = utc_now()
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        statement_key = str(sample.get("canonicalStatementKey") or "")
        field = str(sample.get("field") or "")
        firestore_record = sample.get("productionFirestore") if isinstance(sample.get("productionFirestore"), dict) else {}
        site_record = sample.get("archivedSite") if isinstance(sample.get("archivedSite"), dict) else {}
        left = sample.get("leftValue")
        right = sample.get("rightValue")
        rows.append(
            {
                "schemaVersion": "gas-shunin-source-conflict-ledger/v1",
                "generatedAt": generated_at,
                "conflictId": conflict_id(statement_key, field, left, right),
                "conflictStatus": "needs_source_review",
                "severity": severity_for(field),
                "field": field,
                "canonicalQuestionKey": question_key_from_statement_key(statement_key),
                "canonicalStatementKey": statement_key,
                "qualification": firestore_record.get("qualification") or site_record.get("qualification"),
                "statementNo": firestore_record.get("statementNo") or site_record.get("statementNo"),
                "firestoreSource": firestore_record,
                "siteSource": site_record,
                "firestoreValue": left,
                "siteValue": right,
                "resolutionPolicy": "preserve_firestore_until_pdf_or_source_review",
            }
        )
    return rows


def build_summary(rows: list[dict[str, Any]], report_path: Path, ledger_path: Path) -> dict[str, Any]:
    field_counts = Counter(str(row.get("field")) for row in rows)
    severity_counts = Counter(str(row.get("severity")) for row in rows)
    question_counts: dict[str, int] = Counter(str(row.get("canonicalQuestionKey")) for row in rows)
    question_field_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        question_field_counts[str(row.get("canonicalQuestionKey"))][str(row.get("field"))] += 1
    high_question_count = sum(
        1 for question_key in question_counts
        if question_field_counts[question_key].get("correctChoiceText->correctChoiceText", 0) > 0
    )
    return {
        "schemaVersion": "gas-shunin-source-conflict-ledger-summary/v1",
        "generatedAt": utc_now(),
        "sourceConsistencyReport": rel(report_path),
        "ledgerPath": rel(ledger_path),
        "conflictCount": len(rows),
        "conflictQuestionCount": len(question_counts),
        "highSeverityQuestionCount": high_question_count,
        "fieldCounts": dict(sorted(field_counts.items())),
        "severityCounts": dict(sorted(severity_counts.items())),
        "questionCounts": dict(sorted(question_counts.items())),
        "questionFieldCounts": {
            key: dict(sorted(value.items()))
            for key, value in sorted(question_field_counts.items())
        },
        "policy": {
            "questionId": "existing Firestore document IDs are preserved",
            "sourceText": "no question body or choice text is generated by this ledger",
            "resolution": "each conflict must be reviewed against Firestore/source site/PDF before upload overwrite",
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export gas-shunin Firestore/site source mismatch ledger.")
    parser.add_argument(
        "--report",
        type=Path,
        default=GAS_SHUNIN_REPORT_DIR / "gas-shunin-source-consistency-final.json",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=ROOT_DIR / "output" / "gas-shunin-kou" / "review" / "source_conflicts" / "firestore_site_conflicts.jsonl",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=ROOT_DIR / "output" / "gas-shunin-kou" / "review" / "source_conflicts" / "summary.json",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = load_json(args.report)
    rows = build_rows(report)
    summary = build_summary(rows, args.report, args.ledger)
    write_jsonl(args.ledger, rows)
    write_json(args.summary, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
