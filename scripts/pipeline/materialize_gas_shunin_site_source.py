#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.pipeline import build_gas_shunin_source_key_mapping as key_mapping  # noqa: E402


RAW_SITE_FILENAME_PATTERN = re.compile(r"^question_(?P<year>\d{4})_(?P<chunk>\d+)\.json$")
SITE_ONLY_SUFFIX = "gassyunin_site"
ARCHIVE_DIR_NAME = "99_archived_gassyunin_full"


def utc_now_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def is_raw_site_source_path(path: Path) -> bool:
    return RAW_SITE_FILENAME_PATTERN.match(path.name) is not None


def load_source_file(path: Path) -> tuple[str | None, list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    bodies = data.get("question_bodies") if isinstance(data, dict) else None
    if not isinstance(bodies, list):
        return data.get("list_group_id") if isinstance(data, dict) else None, []

    records: list[dict[str, Any]] = []
    for index, body in enumerate(bodies):
        if not isinstance(body, dict):
            continue
        record = dict(body)
        record["_sourceFile"] = str(path)
        record["_sourceIndex"] = index
        record["_listGroupId"] = data.get("list_group_id")
        records.append(record)
    return data.get("list_group_id"), records


def collect_raw_site_paths(questions_root: Path, years: list[int] | None) -> list[Path]:
    year_filter = {str(year) for year in years} if years else None
    paths: list[Path] = []
    for source_dir in sorted(questions_root.glob("*/00_source")):
        year = source_dir.parent.name
        if year_filter is not None and year not in year_filter:
            continue
        immediate = sorted(path for path in source_dir.glob("question_*.json") if is_raw_site_source_path(path))
        archived = sorted(
            path
            for path in (source_dir / ARCHIVE_DIR_NAME).glob("question_*.json")
            if is_raw_site_source_path(path)
        )
        paths.extend(immediate or archived)
    return paths


def load_raw_site_questions(questions_root: Path, years: list[int] | None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in collect_raw_site_paths(questions_root, years):
        _, loaded = load_source_file(path)
        records.extend(loaded)
    return records


def build_firestore_key_set(snapshot_dir: Path) -> set[str]:
    firestore_questions = key_mapping.load_firestore_questions(snapshot_dir)
    records, invalid = key_mapping.build_firestore_statement_records(firestore_questions)
    if invalid:
        reasons = Counter(str(item.get("reason")) for item in invalid)
        raise ValueError(f"Firestore source key 化できない record があります: {dict(sorted(reasons.items()))}")
    return {record["sourceUniqueKey"] for record in records}


def build_site_only_record(
    *,
    qualification: str,
    question: dict[str, Any],
    firestore_keys: set[str],
    empty_choice_slot_count: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    question_records, statement_records, invalid = key_mapping.build_gassyunin_question_records(
        qualification=qualification,
        questions=[question],
        empty_choice_slot_count=empty_choice_slot_count,
    )
    if invalid:
        return None, invalid
    if not question_records:
        return None, []

    statuses: list[dict[str, Any]] = []
    site_only_statement_numbers: list[int] = []
    firestore_registered_statement_numbers: list[int] = []
    for statement in statement_records:
        statement_no = int(statement["statementNo"])
        source_unique_key = statement["sourceUniqueKey"]
        firestore_registered = source_unique_key in firestore_keys
        if firestore_registered:
            firestore_registered_statement_numbers.append(statement_no)
        else:
            site_only_statement_numbers.append(statement_no)
        statuses.append(
            {
                "statementNo": statement_no,
                "sourceUniqueKey": source_unique_key,
                "firestoreRegistered": firestore_registered,
                "siteOnly": not firestore_registered,
            }
        )

    if not site_only_statement_numbers:
        return None, []

    source_question = question_records[0]
    record = {
        key: value
        for key, value in question.items()
        if not key.startswith("_")
    }
    record.update(
        {
            "isSiteSourced": True,
            "sourceProvider": "gassyunin.com",
            "sourceAcquisitionMethod": "site_html",
            "sourcePriority": 2,
            "sourceFilter": "firestore_unregistered_statement_only",
            "sourceUrl": question.get("question_url"),
            "sourceQuestionKey": source_question["sourceQuestionKey"],
            "sourceUniqueKeys": [status["sourceUniqueKey"] for status in statuses],
            "sourceStatementCount": len(statuses),
            "hasSiteOnlyStatements": True,
            "hasFirestoreRegisteredStatements": bool(firestore_registered_statement_numbers),
            "siteOnlyStatementNumbers": site_only_statement_numbers,
            "firestoreRegisteredStatementNumbers": firestore_registered_statement_numbers,
            "statementSourceStatuses": statuses,
        }
    )
    return record, []


def remove_existing_site_only_files(output_dir: Path, year: int) -> list[str]:
    removed: list[str] = []
    for path in sorted(output_dir.glob(f"question_{year}_{SITE_ONLY_SUFFIX}_*.json")):
        path.unlink()
        removed.append(str(path))
    return removed


def archive_raw_site_files(output_dir: Path, year: int) -> list[str]:
    archived: list[str] = []
    archive_dir = output_dir / ARCHIVE_DIR_NAME
    for path in sorted(output_dir.glob(f"question_{year}_*.json")):
        if not is_raw_site_source_path(path):
            continue
        archive_dir.mkdir(parents=True, exist_ok=True)
        target = archive_dir / path.name
        shutil.move(str(path), str(target))
        archived.append(str(target))
    return archived


def write_year_site_only_files(
    *,
    output_dir: Path,
    year: int,
    records: list[dict[str, Any]],
    chunk_size: int,
) -> list[str]:
    written: list[str] = []
    if not records:
        return written
    output_dir.mkdir(parents=True, exist_ok=True)
    for index, start in enumerate(range(0, len(records), chunk_size), start=1):
        chunk = records[start : start + chunk_size]
        path = output_dir / f"question_{year}_{SITE_ONLY_SUFFIX}_{index}.json"
        write_json(
            path,
            {
                "list_group_id": str(year),
                "sourceProvider": "gassyunin.com",
                "sourceAcquisitionMethod": "site_html",
                "sourceFilter": "firestore_unregistered_statement_only",
                "question_bodies": chunk,
            },
        )
        written.append(str(path))
    return written


def materialize_site_only(args: argparse.Namespace) -> Path:
    output_root = args.output_root.expanduser().resolve()
    questions_root = output_root / args.qualification / "questions_json"
    snapshot_dir = (
        args.firestore_snapshot_dir.expanduser().resolve()
        if args.firestore_snapshot_dir
        else key_mapping.latest_snapshot_dir(args.qualification, output_root)
    )
    report_root = (
        args.report_root.expanduser().resolve()
        if args.report_root
        else output_root / args.qualification / "site_source_filter"
    )
    timestamp = args.timestamp or utc_now_label()
    report_dir = report_root / timestamp

    firestore_keys = build_firestore_key_set(snapshot_dir)
    raw_questions = load_raw_site_questions(questions_root, args.years)
    site_only_by_year: dict[int, list[dict[str, Any]]] = {}
    invalid_records: list[dict[str, Any]] = []
    for question in raw_questions:
        record, invalid = build_site_only_record(
            qualification=args.qualification,
            question=question,
            firestore_keys=firestore_keys,
            empty_choice_slot_count=args.empty_choice_slot_count,
        )
        invalid_records.extend(invalid)
        if record is None:
            continue
        year = int(record.get("examYear") or record.get("list_group_id") or record.get("_listGroupId"))
        site_only_by_year.setdefault(year, []).append(record)

    written_by_year: dict[str, list[str]] = {}
    archived_by_year: dict[str, list[str]] = {}
    removed_by_year: dict[str, list[str]] = {}
    target_years = sorted({int(year) for year in site_only_by_year} | ({int(year) for year in args.years} if args.years else set()))
    if not args.dry_run:
        for year in target_years:
            output_dir = questions_root / str(year) / "00_source"
            removed_by_year[str(year)] = remove_existing_site_only_files(output_dir, year)
            archived_by_year[str(year)] = archive_raw_site_files(output_dir, year) if args.archive_raw_site else []
            written_by_year[str(year)] = write_year_site_only_files(
                output_dir=output_dir,
                year=year,
                records=site_only_by_year.get(year, []),
                chunk_size=args.chunk_size,
            )

    site_only_statement_count = sum(
        len(record["siteOnlyStatementNumbers"])
        for records in site_only_by_year.values()
        for record in records
    )
    report = {
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "qualification": args.qualification,
        "firestoreSnapshotDir": str(snapshot_dir),
        "questionsRoot": str(questions_root),
        "textPolicy": "No question, choice, or explanation text is generated; source records are copied and metadata is deterministic",
        "sourcePolicy": {
            "primary": "firestore",
            "secondary": "gassyunin.com",
            "filter": "only gassyunin statements whose sourceUniqueKey is absent from Firestore are marked siteOnly",
        },
        "summary": {
            "rawSiteQuestionCount": len(raw_questions),
            "firestoreStatementKeyCount": len(firestore_keys),
            "siteOnlyQuestionCount": sum(len(records) for records in site_only_by_year.values()),
            "siteOnlyStatementCount": site_only_statement_count,
            "invalidSiteRecordCount": len(invalid_records),
        },
        "siteOnlyQuestionsByYear": {
            str(year): len(records)
            for year, records in sorted(site_only_by_year.items())
        },
        "siteOnlyStatementsByYear": {
            str(year): sum(len(record["siteOnlyStatementNumbers"]) for record in records)
            for year, records in sorted(site_only_by_year.items())
        },
        "writtenFilesByYear": written_by_year,
        "archivedRawSiteFilesByYear": archived_by_year,
        "removedPreviousSiteOnlyFilesByYear": removed_by_year,
        "invalidRecords": invalid_records,
        "dryRun": args.dry_run,
    }
    write_json(report_dir / "validation_report.json", report)

    print(f"report_dir: {report_dir}")
    print(f"qualification: {args.qualification}")
    print(f"raw site questions: {report['summary']['rawSiteQuestionCount']}")
    print(f"site-only questions: {report['summary']['siteOnlyQuestionCount']}")
    print(f"site-only statements: {report['summary']['siteOnlyStatementCount']}")
    print(f"invalid site records: {report['summary']['invalidSiteRecordCount']}")
    return report_dir


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Firestore 未登録の gassyunin.com 由来 record だけを 00_source に materialize する",
    )
    parser.add_argument(
        "qualification",
        choices=sorted(key_mapping.QUALIFICATION_GRADE),
        help="対象資格コード",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT_DIR / "output",
        help="output root",
    )
    parser.add_argument(
        "--firestore-snapshot-dir",
        type=Path,
        default=None,
        help="Firestore snapshot dir。未指定時は最新 snapshot を使う。",
    )
    parser.add_argument(
        "--years",
        nargs="+",
        type=int,
        default=None,
        help="対象年。未指定時は raw site source がある全年度。",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=25,
        help="1 file あたりの question_bodies 件数",
    )
    parser.add_argument(
        "--empty-choice-slot-count",
        type=int,
        default=5,
        help="choiceTextList が空で answer_result がある場合の空 statement slot 数",
    )
    parser.add_argument(
        "--archive-raw-site",
        action="store_true",
        help=f"raw gassyunin question_YYYY_N.json を {ARCHIVE_DIR_NAME}/ へ退避する",
    )
    parser.add_argument(
        "--report-root",
        type=Path,
        default=None,
        help="validation report root",
    )
    parser.add_argument(
        "--timestamp",
        default=None,
        help="report dir timestamp",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="write/退避せず report のみ作る",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    materialize_site_only(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
