#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


GAS_SHUNIN_REPORT_DIR = ROOT_DIR / "output" / "gas-shunin" / "reports"
QUALIFICATIONS = ("gas-shunin-kou", "gas-shunin-otsu")
VARIANT_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")
SOURCE_KEY_RE = re.compile(r"^gas-shunin:(?P<grade>[^:]+):(?P<year>\d{4}):(?P<subject>[^:]+):q(?P<q>\d+)")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR))
    except ValueError:
        return str(path.resolve())


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def iter_source_paths(qualifications: list[str]) -> list[tuple[str, Path]]:
    paths: list[tuple[str, Path]] = []
    for qualification in qualifications:
        root = ROOT_DIR / "output" / qualification / "questions_json"
        paths.extend(
            (qualification, path)
            for path in sorted(root.glob("*/00_source/question_*.json"))
            if "99_archived" not in path.parts
        )
    return paths


def canonicalize_source_key(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return value.replace(":hourei:", ":law:")


def canonicalize_source_key_list(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    return [canonicalize_source_key(item) for item in value]


def add_legacy_value(question: dict[str, Any], field: str, value: Any) -> None:
    legacy_field = f"sourceLegacy{field[0].upper()}{field[1:]}"
    if legacy_field not in question:
        question[legacy_field] = value
        return
    existing = question[legacy_field]
    if existing == value:
        return
    if isinstance(existing, list):
        if value not in existing:
            existing.append(value)
        return
    question[legacy_field] = [existing, value] if existing != value else existing


def maybe_set(question: dict[str, Any], field: str, value: Any, changes: Counter[str]) -> None:
    if question.get(field) == value:
        return
    old_value = question.get(field)
    if old_value not in (None, "", []):
        add_legacy_value(question, field, copy.deepcopy(old_value))
    question[field] = value
    changes[field] += 1


def canonicalize_question_metadata(question: dict[str, Any], changes: Counter[str]) -> None:
    for field in ("sourceQuestionKey", "sourceNaturalQuestionKey"):
        value = question.get(field)
        canonical = canonicalize_source_key(value)
        if canonical != value:
            maybe_set(question, field, canonical, changes)

    for field in ("sourceUniqueKeys", "sourceNaturalUniqueKeys"):
        value = question.get(field)
        canonical = canonicalize_source_key_list(value)
        if canonical != value:
            maybe_set(question, field, canonical, changes)

    if question.get("sourceSubject") == "hourei":
        maybe_set(question, "sourceSubject", "law", changes)

    key_parts = question.get("sourceKeyParts")
    if isinstance(key_parts, dict) and key_parts.get("subject") == "hourei":
        old_parts = copy.deepcopy(key_parts)
        key_parts = dict(key_parts)
        key_parts["subject"] = "law"
        question.setdefault("sourceLegacyKeyParts", old_parts)
        question["sourceKeyParts"] = key_parts
        changes["sourceKeyParts"] += 1

    statuses = question.get("statementSourceStatuses")
    if isinstance(statuses, list):
        new_statuses: list[Any] = []
        status_changed = False
        for status in statuses:
            if not isinstance(status, dict):
                new_statuses.append(status)
                continue
            new_status = dict(status)
            for field in ("sourceUniqueKey", "sourceNaturalUniqueKey"):
                value = new_status.get(field)
                canonical = canonicalize_source_key(value)
                if canonical != value:
                    new_status[field] = canonical
                    status_changed = True
            new_statuses.append(new_status)
        if status_changed:
            question["statementSourceStatuses"] = new_statuses
            changes["statementSourceStatuses"] += 1

    conflict = question.get("sourceKeyConflict")
    if isinstance(conflict, dict):
        new_conflict = copy.deepcopy(conflict)
        conflict_changed = False
        for field in ("naturalSourceQuestionKey",):
            value = new_conflict.get(field)
            canonical = canonicalize_source_key(value)
            if canonical != value:
                new_conflict[field] = canonical
                conflict_changed = True
        for field in ("naturalSourceUniqueKeys", "duplicateNaturalSourceUniqueKeys", "resolvedSourceUniqueKeys"):
            value = new_conflict.get(field)
            canonical = canonicalize_source_key_list(value)
            if canonical != value:
                new_conflict[field] = canonical
                conflict_changed = True
        if conflict_changed:
            question["sourceKeyConflict"] = new_conflict
            changes["sourceKeyConflict"] += 1


def source_unique_keys(question: dict[str, Any]) -> list[str]:
    values = question.get("sourceUniqueKeys")
    if isinstance(values, list):
        return [str(value) for value in values if isinstance(value, str)]
    return []


def stable_site_variant_key(question: dict[str, Any]) -> str:
    for field in ("public_question_id", "source_question_id", "question_url", "sourceQuestionKey"):
        value = str(question.get(field) or "").strip()
        if value:
            return VARIANT_SAFE_RE.sub("_", value).strip("_")[:96]
    return "site-shadow"


def apply_site_conflict_variant(
    question: dict[str, Any],
    duplicate_keys: set[str],
    changes: Counter[str],
) -> dict[str, Any] | None:
    keys = source_unique_keys(question)
    intersecting = sorted(set(keys) & duplicate_keys)
    if not intersecting:
        return None
    if question.get("sourceOrigin") != "gassyunin_site" and question.get("sourceProvider") != "gassyunin.com":
        return None

    variant_key = stable_site_variant_key(question)
    resolved_keys = [f"{key}:site-shadow:{variant_key}" for key in keys]
    question_key = str(question.get("sourceQuestionKey") or "")
    conflict = {
        "reason": "site_record_overlaps_existing_firestore_statements",
        "variantKey": variant_key,
        "variantSource": "public_question_id_or_source_url",
        "naturalSourceQuestionKey": question_key,
        "naturalSourceUniqueKeys": keys,
        "duplicateNaturalSourceUniqueKeys": intersecting,
        "resolvedSourceUniqueKeys": resolved_keys,
        "resolvedAt": utc_now(),
    }

    question["sourceNaturalQuestionKey"] = question_key
    question["sourceNaturalUniqueKeys"] = keys
    question["sourceConflictVariantKey"] = variant_key
    question["sourceKeyConflict"] = conflict
    question["sourceUniqueKeys"] = resolved_keys
    changes["siteConflictVariant"] += 1
    return {
        "variantKey": variant_key,
        "sourceQuestionKey": question_key,
        "duplicateNaturalSourceUniqueKeys": intersecting,
        "resolvedSourceUniqueKeys": resolved_keys,
    }


def int_set(value: Any) -> set[int]:
    result: set[int] = set()
    if not isinstance(value, list):
        return result
    for item in value:
        try:
            result.add(int(item))
        except (TypeError, ValueError):
            continue
    return result


def apply_statement_status_policy(question: dict[str, Any], changes: Counter[str]) -> None:
    statuses = question.get("statementSourceStatuses")
    keys = source_unique_keys(question)
    if not isinstance(statuses, list) or len(statuses) != len(keys):
        return

    natural_keys = question.get("sourceNaturalUniqueKeys")
    if not isinstance(natural_keys, list):
        natural_keys = []

    registered_numbers = int_set(question.get("firestoreRegisteredStatementNumbers"))
    site_only_numbers = int_set(question.get("siteOnlyStatementNumbers"))
    is_site = question.get("sourceOrigin") == "gassyunin_site" or question.get("sourceProvider") == "gassyunin.com"
    is_firestore = isinstance(question.get("firestoreQuestionIds"), list) and bool(question.get("firestoreQuestionIds"))

    new_statuses: list[Any] = []
    changed = False
    for index, status in enumerate(statuses, start=1):
        if not isinstance(status, dict):
            new_statuses.append(status)
            continue
        new_status = dict(status)
        expected_key = keys[index - 1]
        if new_status.get("sourceUniqueKey") != expected_key:
            new_status["sourceUniqueKey"] = expected_key
            changed = True
        if index <= len(natural_keys) and natural_keys[index - 1] != expected_key:
            if new_status.get("sourceNaturalUniqueKey") != natural_keys[index - 1]:
                new_status["sourceNaturalUniqueKey"] = natural_keys[index - 1]
                changed = True

        if registered_numbers or site_only_numbers:
            expected_registered = index in registered_numbers
            expected_site_only = index in site_only_numbers or not expected_registered
        elif is_firestore:
            expected_registered = True
            expected_site_only = False
        elif is_site:
            expected_registered = False
            expected_site_only = True
        else:
            new_statuses.append(new_status)
            continue

        if new_status.get("firestoreRegistered") is not expected_registered:
            new_status["firestoreRegistered"] = expected_registered
            changed = True
        if new_status.get("siteOnly") is not expected_site_only:
            new_status["siteOnly"] = expected_site_only
            changed = True
        new_statuses.append(new_status)

    if changed:
        question["statementSourceStatuses"] = new_statuses
        changes["statementSourceStatuses"] += 1


def text_signature(payload: dict[str, Any]) -> list[tuple[Any, ...]]:
    signatures: list[tuple[Any, ...]] = []
    bodies = payload.get("question_bodies") if isinstance(payload, dict) else None
    if not isinstance(bodies, list):
        return signatures
    for index, question in enumerate(bodies, start=1):
        if not isinstance(question, dict):
            continue
        signatures.append(
            (
                index,
                question.get("questionBodyText"),
                tuple(question.get("choiceTextList") or []),
                question.get("originalQuestionBodyText"),
                tuple(question.get("originalQuestionChoiceText") or []),
            )
        )
    return signatures


def run(args: argparse.Namespace) -> dict[str, Any]:
    loaded: list[tuple[str, Path, dict[str, Any], list[tuple[Any, ...]]]] = []
    changes: Counter[str] = Counter()
    changed_files: set[str] = set()
    repairs: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for qualification, path in iter_source_paths(args.qualifications):
        payload = load_json(path)
        loaded.append((qualification, path, payload, text_signature(payload)))
        bodies = payload.get("question_bodies") if isinstance(payload, dict) else None
        if not isinstance(bodies, list):
            continue
        before = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        for question in bodies:
            if isinstance(question, dict):
                canonicalize_question_metadata(question, changes)
                apply_statement_status_policy(question, changes)
        if json.dumps(payload, ensure_ascii=False, sort_keys=True) != before:
            changed_files.add(rel(path))

    key_locations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for qualification, path, payload, _signature in loaded:
        bodies = payload.get("question_bodies") if isinstance(payload, dict) else None
        if not isinstance(bodies, list):
            continue
        for index, question in enumerate(bodies, start=1):
            if not isinstance(question, dict):
                continue
            for key in source_unique_keys(question):
                key_locations[key].append(
                    {
                        "qualification": qualification,
                        "sourceFile": rel(path),
                        "questionIndex": index,
                        "questionLabel": question.get("questionLabel"),
                        "sourceOrigin": question.get("sourceOrigin"),
                    }
                )

    duplicate_keys = {key for key, locations in key_locations.items() if len(locations) > 1}
    for qualification, path, payload, _signature in loaded:
        bodies = payload.get("question_bodies") if isinstance(payload, dict) else None
        if not isinstance(bodies, list):
            continue
        before = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        for index, question in enumerate(bodies, start=1):
            if not isinstance(question, dict):
                continue
            record = apply_site_conflict_variant(question, duplicate_keys, changes)
            if record is not None:
                repairs.append(
                    {
                        "qualification": qualification,
                        "sourceFile": rel(path),
                        "questionIndex": index,
                        "questionLabel": question.get("questionLabel"),
                        **record,
                    }
                )
                apply_statement_status_policy(question, changes)
        if json.dumps(payload, ensure_ascii=False, sort_keys=True) != before:
            changed_files.add(rel(path))

    for _qualification, path, payload, before_signature in loaded:
        after_signature = text_signature(payload)
        if before_signature != after_signature:
            errors.append(
                {
                    "sourceFile": rel(path),
                    "error": "source_text_changed",
                }
            )

    if args.write and not errors:
        for _qualification, path, payload, _before_signature in loaded:
            if rel(path) in changed_files:
                save_json(path, payload)

    return {
        "schemaVersion": "gas-shunin-source-metadata-repair/v1",
        "generatedAt": utc_now(),
        "writeApplied": bool(args.write),
        "qualifications": args.qualifications,
        "changedFileCount": len(changed_files),
        "changedFiles": sorted(changed_files),
        "changeCounts": dict(sorted(changes.items())),
        "siteConflictVariantRepairCount": len(repairs),
        "siteConflictVariantRepairs": repairs,
        "duplicateSourceUniqueKeyCountBeforeSiteRepair": len(duplicate_keys),
        "errors": errors,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair gas-shunin 00_source metadata without changing source text.")
    parser.add_argument(
        "--qualifications",
        nargs="+",
        default=list(QUALIFICATIONS),
        choices=sorted(QUALIFICATIONS),
    )
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--report", type=Path, default=GAS_SHUNIN_REPORT_DIR / "gas-shunin-source-metadata-repair.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run(args)
    save_json(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
