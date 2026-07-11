#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.common.question_identity import review_question_id  # noqa: E402
from scripts.common.repaso_firestore_schema import _is_law_revision_facts  # noqa: E402
from scripts.merge.question_issue_corrections import (  # noqa: E402
    PATCHABLE_FIELDS,
    PATCH_ORIGIN,
    PATCH_SCHEMA_VERSION,
    load_correction_patch,
    question_record_hash,
)


SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
FORBIDDEN_KEYS = {
    "detailComment",
    "untrustedUserComment",
    "reportComment",
    "reporterUid",
    "email",
    "name",
    "answerHistory",
}
ALLOWED_SOURCE_CLASSES = {"official", "primary"}
TOP_LEVEL_FIELDS = {
    "schemaVersion",
    "origin",
    "batchId",
    "category",
    "caseIds",
    "inputCaseHashes",
    "reviewProtocol",
    "blindReviewHashes",
    "challengeReviewHash",
    "createdAt",
    "entries",
}
ENTRY_FIELDS = {
    "original_question_id",
    "expectedBeforeHash",
    "changes",
    "rationale",
    "evidence",
}
EVIDENCE_FIELDS = {"sourceClass", "locator", "title", "verifiedAt", "contentHash"}


def law_facts_are_publishable(value: Any) -> bool:
    facts_list = [value] if isinstance(value, dict) else value
    return bool(facts_list) and isinstance(facts_list, list) and all(
        isinstance(facts, dict)
        and _is_law_revision_facts(facts)
        and facts.get("reviewState") == "tertiary_verified"
        and isinstance(facts.get("evidenceSummary"), dict)
        and bool(facts["evidenceSummary"])
        for facts in facts_list
    )


def load_category_config(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    categories = payload.get("categories") if isinstance(payload, dict) else None
    if not isinstance(categories, dict):
        raise ValueError(f"question issue category config is invalid: {path}")
    return categories


def find_forbidden_keys(value: Any, prefix: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{prefix}.{key}"
            if key in FORBIDDEN_KEYS:
                found.append(child_path)
            found.extend(find_forbidden_keys(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(find_forbidden_keys(child, f"{prefix}[{index}]"))
    return found


def validate_evidence(evidence: Any, *, entry_index: int) -> list[str]:
    errors: list[str] = []
    if not isinstance(evidence, list) or not evidence:
        return [f"entry {entry_index}: evidence must be a non-empty list"]
    if len(evidence) > 20:
        errors.append(f"entry {entry_index}: evidence must contain at most 20 items")
    for evidence_index, item in enumerate(evidence, start=1):
        if not isinstance(item, dict):
            errors.append(f"entry {entry_index}: evidence {evidence_index} must be an object")
            continue
        if set(item) != EVIDENCE_FIELDS:
            errors.append(
                f"entry {entry_index}: evidence {evidence_index} fields must match contract"
            )
        if item.get("sourceClass") not in ALLOWED_SOURCE_CLASSES:
            errors.append(
                f"entry {entry_index}: evidence {evidence_index} sourceClass must be official/primary"
            )
        for field in ("locator", "title", "verifiedAt"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                errors.append(
                    f"entry {entry_index}: evidence {evidence_index} {field} must be non-empty"
                )
        if isinstance(item.get("locator"), str) and len(item["locator"]) > 2048:
            errors.append(f"entry {entry_index}: evidence {evidence_index} locator is too long")
        if isinstance(item.get("title"), str) and len(item["title"]) > 512:
            errors.append(f"entry {entry_index}: evidence {evidence_index} title is too long")
        content_hash = str(item.get("contentHash") or "")
        if not SHA256_RE.fullmatch(content_hash):
            errors.append(
                f"entry {entry_index}: evidence {evidence_index} contentHash must be sha256"
            )
    return errors


def current_records(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("question_bodies") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise ValueError(f"current JSON missing question_bodies: {path}")
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        question_id = review_question_id(record)
        if question_id:
            result[str(question_id)] = record
    return result


def validate_patch(
    patch_path: Path,
    *,
    config_path: Path,
    current_path: Path | None = None,
) -> list[str]:
    payload = load_correction_patch(patch_path)
    errors: list[str] = []
    if patch_path.stat().st_size > 2_000_000:
        errors.append("correction patch exceeds 2 MB")
    forbidden_paths = find_forbidden_keys(payload)
    if forbidden_paths:
        errors.append(f"raw/private report fields are forbidden: {forbidden_paths}")
    if set(payload) != TOP_LEVEL_FIELDS:
        errors.append("top-level fields must exactly match correction patch contract")

    if payload.get("schemaVersion") != PATCH_SCHEMA_VERSION:
        errors.append("schemaVersion mismatch")
    if payload.get("origin") != PATCH_ORIGIN:
        errors.append("origin mismatch")
    if not isinstance(payload.get("batchId"), str) or not payload["batchId"].strip():
        errors.append("batchId must be non-empty")
    case_ids = payload.get("caseIds")
    if (
        not isinstance(case_ids, list)
        or not case_ids
        or len(case_ids) > 100
        or len(set(str(value) for value in case_ids)) != len(case_ids)
        or any(not str(value).strip() or len(str(value)) > 256 for value in case_ids)
    ):
        errors.append("caseIds must contain at least one ID")
    input_case_hashes = payload.get("inputCaseHashes")
    if (
        not isinstance(input_case_hashes, dict)
        or not isinstance(case_ids, list)
        or set(input_case_hashes) != set(str(value) for value in case_ids)
        or any(not SHA256_RE.fullmatch(str(value)) for value in input_case_hashes.values())
    ):
        errors.append("inputCaseHashes must exactly bind every caseId to sha256")
    if payload.get("reviewProtocol") != "blind-a-b-challenge/v1":
        errors.append("reviewProtocol mismatch")
    if not isinstance(payload.get("createdAt"), str) or not payload["createdAt"].strip():
        errors.append("createdAt must be non-empty")
    blind_hashes = payload.get("blindReviewHashes")
    if (
        not isinstance(blind_hashes, list)
        or len(blind_hashes) != 2
        or any(not SHA256_RE.fullmatch(str(value)) for value in blind_hashes)
    ):
        errors.append("blindReviewHashes must contain exactly two sha256 values")
    if not SHA256_RE.fullmatch(str(payload.get("challengeReviewHash") or "")):
        errors.append("challengeReviewHash must be sha256")

    categories = load_category_config(config_path)
    category = str(payload.get("category") or "")
    category_config = categories.get(category)
    if not isinstance(category_config, dict):
        errors.append(f"unsupported category: {category}")
        allowed_fields: set[str] = set()
    else:
        allowed_fields = set(category_config.get("allowedChangeFields") or [])

    records = current_records(current_path)
    seen_ids: set[str] = set()
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        return errors + ["entries must be non-empty"]
    if len(entries) > 100:
        errors.append("entries must contain at most 100 items")
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            errors.append(f"entry {index} must be an object")
            continue
        if set(entry) != ENTRY_FIELDS:
            errors.append(f"entry {index}: fields must exactly match contract")
        original_id = str(entry.get("original_question_id") or "").strip()
        if not original_id:
            errors.append(f"entry {index}: original_question_id is required")
        elif original_id in seen_ids:
            errors.append(f"entry {index}: duplicate original_question_id={original_id}")
        seen_ids.add(original_id)
        expected_hash = str(entry.get("expectedBeforeHash") or "")
        if not SHA256_RE.fullmatch(expected_hash):
            errors.append(f"entry {index}: expectedBeforeHash must be sha256")
        changes = entry.get("changes")
        if not isinstance(changes, dict) or not changes:
            errors.append(f"entry {index}: changes must be non-empty")
            continue
        unknown = sorted(set(changes) - PATCHABLE_FIELDS)
        if unknown:
            errors.append(f"entry {index}: unsupported fields {unknown}")
        disallowed = sorted(set(changes) - allowed_fields)
        if disallowed:
            errors.append(f"entry {index}: fields not allowed for {category}: {disallowed}")
        if not isinstance(entry.get("rationale"), str) or not entry["rationale"].strip():
            errors.append(f"entry {index}: rationale must be non-empty")
        elif len(entry["rationale"]) > 4000:
            errors.append(f"entry {index}: rationale is too long")
        errors.extend(validate_evidence(entry.get("evidence"), entry_index=index))
        if category == "outdated_law_or_information":
            facts = changes.get("lawRevisionFacts")
            if not law_facts_are_publishable(facts):
                errors.append(
                    f"entry {index}: current-law correction requires schema-valid "
                    "tertiary_verified lawRevisionFacts with evidenceSummary"
                )
        if records:
            current = records.get(original_id)
            if current is None:
                errors.append(f"entry {index}: current record not found: {original_id}")
            elif question_record_hash(current) != expected_hash:
                errors.append(f"entry {index}: expectedBeforeHash does not match current record")
            else:
                unchanged_fields = sorted(
                    field
                    for field, value in changes.items()
                    if current.get(field) == value
                )
                if unchanged_fields:
                    errors.append(
                        f"entry {index}: changes must differ from current values: "
                        f"{unchanged_fields}"
                    )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a user-problem-report correction overlay patch."
    )
    parser.add_argument("--patch", required=True, type=Path)
    parser.add_argument("--current", type=Path, help="Pre-overlay question_bodies JSON")
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "config" / "question_issue_reports.json",
    )
    args = parser.parse_args()
    errors = validate_patch(
        args.patch.resolve(),
        config_path=args.config.resolve(),
        current_path=args.current.resolve() if args.current else None,
    )
    if errors:
        for error in errors:
            print(f"[ERROR] {error}")
        return 1
    print("[OK] question issue correction patch is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
