#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


QUALIFICATION_GRADE = {
    "gas-shunin-kou": "kou",
    "gas-shunin-otsu": "otsu",
}

SUBJECT_ALIASES = {
    "hourei": "law",
    "law": "law",
    "kiso": "kiso",
    "seizo": "seizo",
    "kyokyu": "kyokyu",
    "shohi": "shohi",
    "gijutsu": "gijutsu",
    "gizyutsu": "gijutsu",
}

CATEGORY_SUBJECTS = {
    "法令": "law",
    "基礎理論": "kiso",
    "製造": "seizo",
    "供給": "kyokyu",
    "消費機器": "shohi",
}

FIRESTORE_GRADE_ALIASES = {
    "koushu": "kou",
    "kou": "kou",
    "otsushu": "otsu",
    "otsu": "otsu",
}

FIRESTORE_SUBJECT_ALIASES = {
    "hourei": "law",
    "law": "law",
    "kiso": "kiso",
    "seizo": "seizo",
    "kyokyu": "kyokyu",
    "shohi": "shohi",
}

QUESTION_LABEL_RE = re.compile(r"問\s*(\d+)")
QUESTION_URL_RE = re.compile(r"#(?P<subject>[a-z]+)-q(?P<question_no>\d+)$")
SOURCE_QUESTION_ID_RE = re.compile(r"^(?P<year>\d{4}):(?P<subject>[^:]+):問(?P<question_no>\d+)$")
FIRESTORE_ORIGINAL_ID_RE = re.compile(
    r"^gasushunin-(?P<grade>[^-]+)-(?P<subject>[^-]+)-(?P<year>\d{4})-(?P<question_no>\d+)$"
)
KEY_RE = re.compile(
    r"^gas-shunin:(?P<grade>[^:]+):(?P<year>\d{4}):(?P<subject>[^:]+):q(?P<question_no>\d+)"
)


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


def normalize_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", "", text)
    return text


def exact_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def canonical_subject(value: Any) -> str | None:
    subject = str(value or "").strip()
    if not subject:
        return None
    return SUBJECT_ALIASES.get(subject, subject)


def padded_question_no(value: int) -> str:
    return f"q{value:02d}"


def padded_statement_no(value: int) -> str:
    return f"s{value:02d}"


def build_question_key(*, grade: str, year: int, subject: str, question_no: int) -> str:
    return f"gas-shunin:{grade}:{year}:{canonical_subject(subject)}:{padded_question_no(question_no)}"


def build_statement_key(question_key: str, statement_no: int) -> str:
    return f"{question_key}:{padded_statement_no(statement_no)}"


def parse_question_no(question: dict[str, Any]) -> int | None:
    value = question.get("questionNo")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)

    for field in ("questionLabel", "source_question_id"):
        match = QUESTION_LABEL_RE.search(str(question.get(field) or ""))
        if match:
            return int(match.group(1))

    url = str(question.get("question_url") or question.get("sourceUrl") or "")
    match = QUESTION_URL_RE.search(url)
    if match:
        return int(match.group("question_no"))
    return None


def technical_subject_for_question_no(question_no: int) -> str:
    if 1 <= question_no <= 9:
        return "seizo"
    if 10 <= question_no <= 18:
        return "kyokyu"
    if 19 <= question_no <= 27:
        return "shohi"
    return "gijutsu"


def parse_firestore_original_id(original_id: str) -> tuple[str, int, str, int] | None:
    match = FIRESTORE_ORIGINAL_ID_RE.match(original_id or "")
    if not match:
        return None
    grade = FIRESTORE_GRADE_ALIASES.get(match.group("grade"))
    if not grade:
        return None
    source_subject = match.group("subject")
    question_no = int(match.group("question_no"))
    subject = FIRESTORE_SUBJECT_ALIASES.get(source_subject)
    if subject is None and source_subject in {"gijutsu", "gizyutsu"}:
        subject = technical_subject_for_question_no(question_no)
    if subject is None:
        return None
    return grade, int(match.group("year")), subject, question_no


def source_parts(
    *,
    qualification: str,
    source_path: Path,
    data: dict[str, Any],
    question: dict[str, Any],
) -> tuple[str, int, str, int] | None:
    for key_field in ("sourceQuestionKey",):
        value = str(question.get(key_field) or "").strip()
        match = KEY_RE.match(value)
        if match:
            return (
                match.group("grade"),
                int(match.group("year")),
                canonical_subject(match.group("subject")) or match.group("subject"),
                int(match.group("question_no")),
            )

    source_unique_keys = exact_list(question.get("sourceUniqueKeys"))
    if source_unique_keys:
        match = KEY_RE.match(str(source_unique_keys[0] or ""))
        if match:
            return (
                match.group("grade"),
                int(match.group("year")),
                canonical_subject(match.group("subject")) or match.group("subject"),
                int(match.group("question_no")),
            )

    parsed_original = parse_firestore_original_id(
        str(question.get("originalQuestionId") or question.get("original_question_id") or "")
    )
    if parsed_original:
        return parsed_original

    source_question_id = str(question.get("source_question_id") or "")
    match = SOURCE_QUESTION_ID_RE.match(source_question_id)
    if match:
        return (
            QUALIFICATION_GRADE[qualification],
            int(match.group("year")),
            canonical_subject(match.group("subject")) or match.group("subject"),
            int(match.group("question_no")),
        )

    url = str(question.get("question_url") or question.get("sourceUrl") or "")
    match = QUESTION_URL_RE.search(url)
    if match:
        year = int(question.get("examYear") or data.get("list_group_id") or source_path.parent.parent.name)
        return (
            QUALIFICATION_GRADE[qualification],
            year,
            canonical_subject(match.group("subject")) or match.group("subject"),
            int(match.group("question_no")),
        )

    question_no = parse_question_no(question)
    category = str(question.get("category") or "")
    subject = CATEGORY_SUBJECTS.get(category) or canonical_subject(question.get("sourceSubject"))
    year_value = question.get("examYear") or data.get("list_group_id") or source_path.parent.parent.name
    if question_no is None or not subject or not year_value:
        return None
    return QUALIFICATION_GRADE[qualification], int(year_value), subject, question_no


def statement_count(question: dict[str, Any]) -> int:
    for field in ("choiceTextList", "originalQuestionChoiceText", "correctChoiceText", "sourceUniqueKeys"):
        values = exact_list(question.get(field))
        if values:
            return len(values)
    value = question.get("sourceStatementCount")
    return int(value) if isinstance(value, int) else 0


def iter_source_files(qualification: str, *, archived: bool) -> list[Path]:
    root = ROOT_DIR / "output" / qualification / "questions_json"
    if archived:
        return sorted(root.glob("*/00_source/99_archived_gassyunin_full/question_*.json"))
    return sorted(root.glob("*/00_source/question_*.json"))


def load_source_questions(qualification: str, *, archived: bool) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in iter_source_files(qualification, archived=archived):
        data = load_json(path)
        bodies = data.get("question_bodies") if isinstance(data, dict) else None
        if not isinstance(bodies, list):
            continue
        for index, question in enumerate(bodies, start=1):
            if not isinstance(question, dict):
                continue
            parts = source_parts(qualification=qualification, source_path=path, data=data, question=question)
            if parts is None:
                question_key = None
            else:
                grade, year, subject, question_no = parts
                question_key = build_question_key(
                    grade=grade,
                    year=year,
                    subject=subject,
                    question_no=question_no,
                )
            source_origin = question.get("sourceOrigin")
            if archived:
                source_kind = "archive_site"
            elif source_origin == "firestore_snapshot" or exact_list(question.get("firestoreQuestionIds")):
                source_kind = "production_firestore"
            elif source_origin == "gassyunin_site" or question.get("sourceProvider") == "gassyunin.com":
                source_kind = "production_site"
            else:
                source_kind = "production_unknown"
            records.append(
                {
                    "qualification": qualification,
                    "sourceKind": source_kind,
                    "sourceFile": rel(path),
                    "sourceIndex": index,
                    "questionKey": question_key,
                    "storedSourceQuestionKey": question.get("sourceQuestionKey"),
                    "question": question,
                }
            )
    return records


def make_statement_records(question_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for qrec in question_records:
        question = qrec["question"]
        count = statement_count(question)
        stored_unique_keys = exact_list(question.get("sourceUniqueKeys"))
        original_choices = exact_list(question.get("originalQuestionChoiceText"))
        choices = exact_list(question.get("choiceTextList"))
        corrects = exact_list(question.get("correctChoiceText"))
        firestore_ids = exact_list(question.get("firestoreQuestionIds"))
        statuses = exact_list(question.get("statementSourceStatuses"))
        for index in range(1, count + 1):
            question_key = qrec.get("questionKey")
            records.append(
                {
                    "qualification": qrec["qualification"],
                    "sourceKind": qrec["sourceKind"],
                    "sourceFile": qrec["sourceFile"],
                    "sourceIndex": qrec["sourceIndex"],
                    "statementNo": index,
                    "questionKey": question_key,
                    "canonicalStatementKey": build_statement_key(question_key, index) if question_key else None,
                    "storedSourceQuestionKey": qrec.get("storedSourceQuestionKey"),
                    "storedSourceUniqueKey": stored_unique_keys[index - 1] if index <= len(stored_unique_keys) else None,
                    "questionLabel": question.get("questionLabel"),
                    "questionBodyText": question.get("questionBodyText"),
                    "originalQuestionBodyText": question.get("originalQuestionBodyText"),
                    "choiceText": choices[index - 1] if index <= len(choices) else None,
                    "originalChoiceText": original_choices[index - 1] if index <= len(original_choices) else None,
                    "correctChoiceText": corrects[index - 1] if index <= len(corrects) else None,
                    "firestoreQuestionId": firestore_ids[index - 1] if index <= len(firestore_ids) else None,
                    "statementStatus": statuses[index - 1] if index <= len(statuses) else None,
                    "rawQuestion": question,
                }
            )
    return records


def compare_value(left: Any, right: Any) -> dict[str, Any]:
    exact_equal = left == right
    normalized_equal = normalize_text(left) == normalize_text(right)
    return {
        "exactEqual": exact_equal,
        "normalizedEqual": normalized_equal,
        "left": left,
        "right": right,
    }


def compact_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "qualification": record.get("qualification"),
        "sourceKind": record.get("sourceKind"),
        "sourceFile": record.get("sourceFile"),
        "sourceIndex": record.get("sourceIndex"),
        "questionLabel": record.get("questionLabel"),
        "statementNo": record.get("statementNo"),
        "questionKey": record.get("questionKey"),
        "canonicalStatementKey": record.get("canonicalStatementKey"),
        "storedSourceQuestionKey": record.get("storedSourceQuestionKey"),
        "storedSourceUniqueKey": record.get("storedSourceUniqueKey"),
        "firestoreQuestionId": record.get("firestoreQuestionId"),
    }


def latest_firestore_snapshot_dir(qualification: str) -> Path | None:
    root = ROOT_DIR / "output" / qualification / "firestore_snapshot"
    if not root.exists():
        return None
    candidates = sorted(path for path in root.iterdir() if path.is_dir())
    return candidates[-1] if candidates else None


def load_firestore_snapshot_questions(snapshot_dir: Path | None) -> dict[str, dict[str, Any]]:
    if snapshot_dir is None:
        return {}
    path = snapshot_dir / "reconstructed" / "questions.json"
    if not path.exists():
        return {}
    data = load_json(path)
    questions = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(questions, list):
        return {}
    return {
        str(question.get("questionId")): question
        for question in questions
        if isinstance(question, dict) and str(question.get("questionId") or "").strip()
    }


def add_sample(bucket: list[dict[str, Any]], item: dict[str, Any], max_samples: int) -> None:
    if len(bucket) < max_samples:
        bucket.append(item)


def duplicate_groups(records: list[dict[str, Any]], key_name: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        key = record.get(key_name)
        if key:
            grouped[str(key)].append(record)
    return {key: values for key, values in sorted(grouped.items()) if len(values) > 1}


def is_known_legacy_conflict(records: list[dict[str, Any]]) -> bool:
    for record in records:
        raw = record.get("rawQuestion") or {}
        if raw.get("sourceKeyConflict") or raw.get("sourceNaturalUniqueKeys"):
            return True
    return False


def check_statement_statuses(records: list[dict[str, Any]], max_samples: int) -> tuple[int, list[dict[str, Any]]]:
    issue_count = 0
    samples: list[dict[str, Any]] = []
    seen_questions: set[tuple[str, int]] = set()
    for record in records:
        marker = (record["sourceFile"], record["sourceIndex"])
        if marker in seen_questions:
            continue
        seen_questions.add(marker)
        raw = record.get("rawQuestion") or {}
        count = statement_count(raw)
        statuses = exact_list(raw.get("statementSourceStatuses"))
        source_unique_keys = exact_list(raw.get("sourceUniqueKeys"))
        firestore_ids = exact_list(raw.get("firestoreQuestionIds"))
        registered_numbers = set(int(v) for v in exact_list(raw.get("firestoreRegisteredStatementNumbers")) if str(v).isdigit())
        site_only_numbers = set(int(v) for v in exact_list(raw.get("siteOnlyStatementNumbers")) if str(v).isdigit())
        source_kind = record["sourceKind"]

        def record_issue(reason: str, detail: dict[str, Any]) -> None:
            nonlocal issue_count
            issue_count += 1
            add_sample(
                samples,
                {
                    "reason": reason,
                    "sourceFile": record["sourceFile"],
                    "sourceIndex": record["sourceIndex"],
                    "questionKey": record.get("questionKey"),
                    "questionLabel": record.get("questionLabel"),
                    **detail,
                },
                max_samples,
            )

        if len(statuses) != count:
            record_issue("statementSourceStatuses_length_mismatch", {"expected": count, "actual": len(statuses)})
            continue

        for index, status in enumerate(statuses, start=1):
            if not isinstance(status, dict):
                record_issue("statementSourceStatus_not_object", {"statementNo": index, "status": status})
                continue
            expected_key = source_unique_keys[index - 1] if index <= len(source_unique_keys) else None
            if expected_key and status.get("sourceUniqueKey") != expected_key:
                record_issue(
                    "statementSourceStatus_sourceUniqueKey_mismatch",
                    {"statementNo": index, "expected": expected_key, "actual": status.get("sourceUniqueKey")},
                )
            if source_kind == "production_firestore":
                expected_id = firestore_ids[index - 1] if index <= len(firestore_ids) else None
                if status.get("firestoreQuestionId") != expected_id:
                    record_issue(
                        "statementSourceStatus_firestoreQuestionId_mismatch",
                        {"statementNo": index, "expected": expected_id, "actual": status.get("firestoreQuestionId")},
                    )
                if status.get("firestoreRegistered") is not True or status.get("siteOnly") is not False:
                    record_issue(
                        "statementSourceStatus_firestore_flags_mismatch",
                        {
                            "statementNo": index,
                            "expected": {"firestoreRegistered": True, "siteOnly": False},
                            "actual": {
                                "firestoreRegistered": status.get("firestoreRegistered"),
                                "siteOnly": status.get("siteOnly"),
                            },
                        },
                    )
            elif source_kind == "production_site":
                if registered_numbers or site_only_numbers:
                    expected_registered = index in registered_numbers
                    expected_site_only = index in site_only_numbers or not expected_registered
                    if (
                        status.get("firestoreRegistered") is not expected_registered
                        or status.get("siteOnly") is not expected_site_only
                    ):
                        record_issue(
                            "statementSourceStatus_site_filter_flags_mismatch",
                            {
                                "statementNo": index,
                                "expected": {
                                    "firestoreRegistered": expected_registered,
                                    "siteOnly": expected_site_only,
                                },
                                "actual": {
                                    "firestoreRegistered": status.get("firestoreRegistered"),
                                    "siteOnly": status.get("siteOnly"),
                                },
                                "firestoreRegisteredStatementNumbers": sorted(registered_numbers),
                                "siteOnlyStatementNumbers": sorted(site_only_numbers),
                            },
                        )
                elif status.get("firestoreRegistered") is not False or status.get("siteOnly") is not True:
                    record_issue(
                        "statementSourceStatus_site_flags_mismatch",
                        {
                            "statementNo": index,
                            "expected": {"firestoreRegistered": False, "siteOnly": True},
                            "actual": {
                                "firestoreRegistered": status.get("firestoreRegistered"),
                                "siteOnly": status.get("siteOnly"),
                            },
                        },
                    )
    return issue_count, samples


def compare_statement_records(
    left_records: list[dict[str, Any]],
    right_records: list[dict[str, Any]],
    *,
    left_name: str,
    right_name: str,
    fields: list[tuple[str, str]],
    max_samples: int,
) -> dict[str, Any]:
    left_by_key = {record["canonicalStatementKey"]: record for record in left_records if record.get("canonicalStatementKey")}
    right_by_key = {record["canonicalStatementKey"]: record for record in right_records if record.get("canonicalStatementKey")}
    common_keys = sorted(set(left_by_key) & set(right_by_key))
    exact_mismatch_count = 0
    normalized_mismatch_count = 0
    samples: list[dict[str, Any]] = []
    field_exact_counts: Counter[str] = Counter()
    field_normalized_counts: Counter[str] = Counter()
    for key in common_keys:
        left = left_by_key[key]
        right = right_by_key[key]
        for left_field, right_field in fields:
            comparison = compare_value(left.get(left_field), right.get(right_field))
            field_label = f"{left_field}->{right_field}"
            if not comparison["exactEqual"]:
                exact_mismatch_count += 1
                field_exact_counts[field_label] += 1
            if not comparison["normalizedEqual"]:
                normalized_mismatch_count += 1
                field_normalized_counts[field_label] += 1
                add_sample(
                    samples,
                    {
                        "canonicalStatementKey": key,
                        "field": field_label,
                        left_name: compact_record(left),
                        right_name: compact_record(right),
                        "leftValue": comparison["left"],
                        "rightValue": comparison["right"],
                    },
                    max_samples,
                )
    return {
        "left": left_name,
        "right": right_name,
        "commonStatementCount": len(common_keys),
        "leftOnlyStatementCount": len(set(left_by_key) - set(right_by_key)),
        "rightOnlyStatementCount": len(set(right_by_key) - set(left_by_key)),
        "exactMismatchCount": exact_mismatch_count,
        "normalizedMismatchCount": normalized_mismatch_count,
        "exactMismatchesByField": dict(sorted(field_exact_counts.items())),
        "normalizedMismatchesByField": dict(sorted(field_normalized_counts.items())),
        "normalizedMismatchSamples": samples,
    }


def run_check(max_samples: int) -> dict[str, Any]:
    qualifications = sorted(QUALIFICATION_GRADE)
    direct_questions: list[dict[str, Any]] = []
    archive_questions: list[dict[str, Any]] = []
    for qualification in qualifications:
        direct_questions.extend(load_source_questions(qualification, archived=False))
        archive_questions.extend(load_source_questions(qualification, archived=True))

    production_records = make_statement_records(direct_questions)
    archive_records = make_statement_records(archive_questions)
    production_firestore = [record for record in production_records if record["sourceKind"] == "production_firestore"]
    production_site = [record for record in production_records if record["sourceKind"] == "production_site"]

    snapshot_dir = latest_firestore_snapshot_dir("gas-shunin-kou")
    snapshot_by_id = load_firestore_snapshot_questions(snapshot_dir)
    firestore_snapshot_issues: list[dict[str, Any]] = []
    firestore_snapshot_issue_count = 0
    referenced_firestore_ids: Counter[str] = Counter()
    for record in production_firestore:
        firestore_id = str(record.get("firestoreQuestionId") or "").strip()
        if not firestore_id:
            firestore_snapshot_issue_count += 1
            add_sample(firestore_snapshot_issues, {"reason": "missing_firestoreQuestionId", **compact_record(record)}, max_samples)
            continue
        referenced_firestore_ids[firestore_id] += 1
        snapshot = snapshot_by_id.get(firestore_id)
        if snapshot is None:
            firestore_snapshot_issue_count += 1
            add_sample(firestore_snapshot_issues, {"reason": "firestoreQuestionId_not_in_snapshot", **compact_record(record)}, max_samples)
            continue
        comparisons = [
            ("originalQuestionBodyText", record.get("originalQuestionBodyText"), snapshot.get("originalQuestionBodyText")),
            ("choiceText", record.get("choiceText"), snapshot.get("originalQuestionChoiceText")),
            ("originalChoiceText", record.get("originalChoiceText"), snapshot.get("originalQuestionChoiceText")),
            ("correctChoiceText", record.get("correctChoiceText"), snapshot.get("correctChoiceText")),
        ]
        for field, source_value, snapshot_value in comparisons:
            result = compare_value(source_value, snapshot_value)
            if not result["normalizedEqual"]:
                firestore_snapshot_issue_count += 1
                add_sample(
                    firestore_snapshot_issues,
                    {
                        "reason": "firestore_snapshot_text_mismatch",
                        "field": field,
                        **compact_record(record),
                        "sourceValue": source_value,
                        "snapshotValue": snapshot_value,
                    },
                    max_samples,
                )

    duplicate_referenced_ids = {
        key: value for key, value in sorted(referenced_firestore_ids.items()) if value > 1
    }
    for firestore_id, count in duplicate_referenced_ids.items():
        firestore_snapshot_issue_count += count - 1
        add_sample(
            firestore_snapshot_issues,
            {"reason": "duplicate_firestoreQuestionId_reference", "firestoreQuestionId": firestore_id, "count": count},
            max_samples,
        )

    stored_subject_counts = Counter()
    noncanonical_stored_subjects: list[dict[str, Any]] = []
    for record in production_records:
        stored = str(record.get("storedSourceQuestionKey") or "")
        match = KEY_RE.match(stored)
        if not match:
            continue
        stored_subject = match.group("subject")
        stored_subject_counts[stored_subject] += 1
        if canonical_subject(stored_subject) != stored_subject:
            add_sample(
                noncanonical_stored_subjects,
                {
                    **compact_record(record),
                    "storedSubject": stored_subject,
                    "canonicalSubject": canonical_subject(stored_subject),
                },
                max_samples,
            )

    stored_duplicates = duplicate_groups(production_records, "storedSourceUniqueKey")
    canonical_duplicates = duplicate_groups(production_records, "canonicalStatementKey")
    known_legacy_duplicate_count = 0
    unexpected_duplicate_samples: list[dict[str, Any]] = []
    unexpected_duplicate_count = 0
    for key, values in canonical_duplicates.items():
        if is_known_legacy_conflict(values):
            known_legacy_duplicate_count += len(values)
            continue
        unexpected_duplicate_count += len(values)
        add_sample(
            unexpected_duplicate_samples,
            {
                "canonicalStatementKey": key,
                "records": [compact_record(value) for value in values],
            },
            max_samples,
        )

    status_issue_count, status_issue_samples = check_statement_statuses(production_records, max_samples)

    archive_by_key_counts = Counter(record.get("canonicalStatementKey") for record in archive_records if record.get("canonicalStatementKey"))
    archive_unique = [record for record in archive_records if archive_by_key_counts.get(record.get("canonicalStatementKey")) == 1]
    firestore_by_key_counts = Counter(record.get("canonicalStatementKey") for record in production_firestore if record.get("canonicalStatementKey"))
    firestore_unique = [
        record for record in production_firestore if firestore_by_key_counts.get(record.get("canonicalStatementKey")) == 1
    ]
    site_by_key_counts = Counter(record.get("canonicalStatementKey") for record in production_site if record.get("canonicalStatementKey"))
    site_unique = [record for record in production_site if site_by_key_counts.get(record.get("canonicalStatementKey")) == 1]

    firestore_vs_archive = compare_statement_records(
        firestore_unique,
        archive_unique,
        left_name="productionFirestore",
        right_name="archivedSite",
        fields=[
            ("originalQuestionBodyText", "questionBodyText"),
            ("choiceText", "choiceText"),
            ("correctChoiceText", "correctChoiceText"),
        ],
        max_samples=max_samples,
    )
    production_site_vs_archive = compare_statement_records(
        site_unique,
        archive_unique,
        left_name="productionSite",
        right_name="archivedSite",
        fields=[
            ("originalQuestionBodyText", "questionBodyText"),
            ("choiceText", "choiceText"),
            ("correctChoiceText", "correctChoiceText"),
        ],
        max_samples=max_samples,
    )

    severity_counts = {
        "firestoreSnapshotIssues": firestore_snapshot_issue_count,
        "storedSourceUniqueKeyDuplicates": sum(len(values) for values in stored_duplicates.values()),
        "unexpectedCanonicalStatementDuplicates": unexpected_duplicate_count,
        "statementStatusIssues": status_issue_count,
        "productionSiteVsArchiveNormalizedMismatches": production_site_vs_archive["normalizedMismatchCount"],
        "firestoreVsArchiveNormalizedMismatches": firestore_vs_archive["normalizedMismatchCount"],
    }
    issue_count = sum(severity_counts.values())
    warning_counts = {
        "noncanonicalStoredSubjectStatementCount": sum(
            count for subject, count in stored_subject_counts.items() if canonical_subject(subject) != subject
        ),
        "knownLegacyCanonicalDuplicateStatementRecordCount": known_legacy_duplicate_count,
    }

    return {
        "schemaVersion": "gas-shunin-source-consistency/v1",
        "generatedAt": utc_now(),
        "snapshotDir": rel(snapshot_dir) if snapshot_dir else None,
        "summary": {
            "productionQuestionCount": len(direct_questions),
            "productionStatementCount": len(production_records),
            "productionFirestoreStatementCount": len(production_firestore),
            "productionSiteStatementCount": len(production_site),
            "archiveSiteQuestionCount": len(archive_questions),
            "archiveSiteStatementCount": len(archive_records),
            "firestoreSnapshotQuestionCount": len(snapshot_by_id),
            "issueCount": issue_count,
            "warningCount": sum(warning_counts.values()),
        },
        "sourceKindCounts": dict(sorted(Counter(record["sourceKind"] for record in production_records).items())),
        "productionStatementsByQualification": dict(sorted(Counter(record["qualification"] for record in production_records).items())),
        "storedSubjectCounts": dict(sorted(stored_subject_counts.items())),
        "issueCounts": severity_counts,
        "warningCounts": warning_counts,
        "firestoreSnapshotVsProduction": {
            "referencedFirestoreIdCount": len(referenced_firestore_ids),
            "duplicateReferencedFirestoreIdCount": len(duplicate_referenced_ids),
            "unreferencedSnapshotQuestionCount": len(set(snapshot_by_id) - set(referenced_firestore_ids)),
            "issueCount": firestore_snapshot_issue_count,
            "issueSamples": firestore_snapshot_issues,
        },
        "productionSiteVsArchivedSite": production_site_vs_archive,
        "productionFirestoreVsArchivedSite": firestore_vs_archive,
        "duplicateChecks": {
            "storedSourceUniqueKeyDuplicateGroupCount": len(stored_duplicates),
            "canonicalStatementKeyDuplicateGroupCount": len(canonical_duplicates),
            "knownLegacyCanonicalDuplicateStatementRecordCount": known_legacy_duplicate_count,
            "unexpectedCanonicalDuplicateStatementRecordCount": unexpected_duplicate_count,
            "unexpectedCanonicalDuplicateSamples": unexpected_duplicate_samples,
        },
        "statementStatusCheck": {
            "issueCount": status_issue_count,
            "issueSamples": status_issue_samples,
        },
        "noncanonicalStoredSubjectSamples": noncanonical_stored_subjects,
        "notes": [
            "productionSiteVsArchivedSite verifies adopted gassyunin.com records against archived full scrape where archive exists.",
            "productionFirestoreVsArchivedSite compares Firestore-derived source against gassyunin.com by canonical natural key; differences here do not mutate source files.",
            "hourei is normalized to law for canonical comparison; stored-key warnings indicate metadata that can block exact key joins.",
        ],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check gas-shunin 00_source consistency across Firestore and site sources.")
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT_DIR / "output" / "gas-shunin-source-consistency-final.json",
    )
    parser.add_argument("--max-samples", type=int, default=50)
    parser.add_argument("--fail-on-issues", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_check(max_samples=args.max_samples)
    write_json(args.report, report)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2, sort_keys=True))
    print(f"report: {rel(args.report)}")
    if args.fail_on_issues and int(report["summary"]["issueCount"]) > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
