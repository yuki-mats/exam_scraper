from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any


REVIEW_SCHEMA_VERSION = "aggregate-answer-review/v1"
DECOMPOSITION_SCHEMA_VERSION = "aggregate-answer-decomposition/v1"
CLASSIFICATIONS = frozenset({"target", "non_target", "hold"})
DECISIONS = frozenset({"approve", "hold"})
AGENT_ISSUE_CODES = frozenset(
    {
        "ambiguous_target",
        "ambiguous_boundary",
        "missing_statement",
        "not_self_contained",
        "source_hash_mismatch",
    }
)
SYSTEM_ISSUE_CODES = frozenset({"review_disagreement", "invalid_review"})
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REVIEW_KEYS = frozenset(
    {
        "schemaVersion",
        "sourceHash",
        "classification",
        "spans",
        "decision",
        "issueCodes",
    }
)
_SPAN_KEYS = frozenset({"start", "end"})
_DECOMPOSITION_KEYS = frozenset(
    {
        "schemaVersion",
        "sourceHash",
        "classification",
        "spans",
        "decision",
        "issueCodes",
    }
)


def source_text_hash(source_text: str) -> str:
    if not isinstance(source_text, str):
        raise ValueError("questionBodyText must be string")
    return "sha256:" + hashlib.sha256(source_text.encode("utf-8")).hexdigest()


def _normalize_spans(value: Any, *, source_length: int) -> list[dict[str, int]]:
    if not isinstance(value, list):
        raise ValueError("spans must be array")
    normalized: list[dict[str, int]] = []
    previous_end = -1
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or set(raw) != _SPAN_KEYS:
            raise ValueError(f"span {index + 1} must contain only start/end")
        start = raw.get("start")
        end = raw.get("end")
        if isinstance(start, bool) or not isinstance(start, int):
            raise ValueError(f"span {index + 1} start must be integer")
        if isinstance(end, bool) or not isinstance(end, int):
            raise ValueError(f"span {index + 1} end must be integer")
        if start < 0 or end <= start or end > source_length:
            raise ValueError(f"span {index + 1} is outside questionBodyText")
        if start < previous_end:
            raise ValueError("spans must be ordered and non-overlapping")
        normalized.append({"start": start, "end": end})
        previous_end = end
    return normalized


def normalize_review(review: Any, source_text: str) -> dict[str, Any]:
    """Validate one agent review without accepting any agent-authored text."""

    if not isinstance(review, Mapping) or set(review) != _REVIEW_KEYS:
        raise ValueError(
            "aggregate answer review must contain only schemaVersion, sourceHash, "
            "classification, spans, decision, issueCodes"
        )
    if review.get("schemaVersion") != REVIEW_SCHEMA_VERSION:
        raise ValueError("aggregate answer review schemaVersion mismatch")
    source_hash = review.get("sourceHash")
    if not isinstance(source_hash, str) or not _SHA256_RE.fullmatch(source_hash):
        raise ValueError("sourceHash must be sha256:<64 hex>")
    classification = review.get("classification")
    if classification not in CLASSIFICATIONS:
        raise ValueError("classification must be target, non_target, or hold")
    decision = review.get("decision")
    if decision not in DECISIONS:
        raise ValueError("decision must be approve or hold")
    spans = _normalize_spans(review.get("spans"), source_length=len(source_text))
    issue_codes = review.get("issueCodes")
    if (
        not isinstance(issue_codes, list)
        or any(not isinstance(value, str) for value in issue_codes)
        or len(set(issue_codes)) != len(issue_codes)
        or any(value not in AGENT_ISSUE_CODES for value in issue_codes)
    ):
        raise ValueError("issueCodes contains an unsupported or duplicate code")
    if decision == "approve" and issue_codes:
        raise ValueError("approved review cannot contain issueCodes")
    if decision == "hold" and not issue_codes:
        raise ValueError("held review requires at least one issueCode")
    if classification == "target" and decision == "approve" and len(spans) < 2:
        raise ValueError("approved target requires at least two statement spans")
    if classification != "target" and spans:
        raise ValueError("non-target or hold classification cannot contain spans")
    if classification == "hold" and decision != "hold":
        raise ValueError("hold classification requires hold decision")
    return {
        "schemaVersion": REVIEW_SCHEMA_VERSION,
        "sourceHash": source_hash,
        "classification": classification,
        "spans": spans,
        "decision": decision,
        "issueCodes": sorted(issue_codes),
    }


def reconcile_reviews(source_text: str, reviews: Any) -> dict[str, Any]:
    """Return an approved consensus or a deterministic hold for two reviews."""

    if not isinstance(reviews, list) or len(reviews) != 2:
        raise ValueError("exactly two independent aggregate answer reviews are required")
    normalized = [normalize_review(review, source_text) for review in reviews]
    actual_hash = source_text_hash(source_text)
    if any(review["sourceHash"] != actual_hash for review in normalized):
        return {
            "schemaVersion": DECOMPOSITION_SCHEMA_VERSION,
            "sourceHash": actual_hash,
            "classification": "hold",
            "spans": [],
            "decision": "hold",
            "issueCodes": ["source_hash_mismatch"],
        }
    if normalized[0] != normalized[1]:
        return {
            "schemaVersion": DECOMPOSITION_SCHEMA_VERSION,
            "sourceHash": actual_hash,
            "classification": "hold",
            "spans": [],
            "decision": "hold",
            "issueCodes": ["review_disagreement"],
        }
    agreed = normalized[0]
    return {
        "schemaVersion": DECOMPOSITION_SCHEMA_VERSION,
        "sourceHash": actual_hash,
        "classification": agreed["classification"],
        "spans": agreed["spans"],
        "decision": agreed["decision"],
        "issueCodes": agreed["issueCodes"],
    }


def normalize_decomposition(value: Any, source_text: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _DECOMPOSITION_KEYS:
        raise ValueError("aggregateAnswerDecomposition has unsupported fields")
    if value.get("schemaVersion") != DECOMPOSITION_SCHEMA_VERSION:
        raise ValueError("aggregateAnswerDecomposition schemaVersion mismatch")
    source_hash = value.get("sourceHash")
    if source_hash != source_text_hash(source_text):
        raise ValueError("aggregateAnswerDecomposition sourceHash mismatch")
    classification = value.get("classification")
    decision = value.get("decision")
    if classification not in CLASSIFICATIONS or decision not in DECISIONS:
        raise ValueError("aggregateAnswerDecomposition classification/decision is invalid")
    spans = _normalize_spans(value.get("spans"), source_length=len(source_text))
    issue_codes = value.get("issueCodes")
    allowed_codes = AGENT_ISSUE_CODES | SYSTEM_ISSUE_CODES
    if (
        not isinstance(issue_codes, list)
        or any(not isinstance(code, str) for code in issue_codes)
        or len(set(issue_codes)) != len(issue_codes)
        or any(code not in allowed_codes for code in issue_codes)
    ):
        raise ValueError("aggregateAnswerDecomposition issueCodes is invalid")
    if decision == "approve" and issue_codes:
        raise ValueError("approved decomposition cannot contain issueCodes")
    if decision == "hold" and not issue_codes:
        raise ValueError("held decomposition requires an issueCode")
    if classification == "target" and decision == "approve" and len(spans) < 2:
        raise ValueError("approved target decomposition requires at least two spans")
    if classification != "target" and spans:
        raise ValueError("non-target or hold decomposition cannot contain spans")
    if classification == "hold" and decision != "hold":
        raise ValueError("hold classification requires hold decision")
    return {
        "schemaVersion": DECOMPOSITION_SCHEMA_VERSION,
        "sourceHash": source_hash,
        "classification": classification,
        "spans": spans,
        "decision": decision,
        "issueCodes": sorted(issue_codes),
    }


def is_approved_target(value: Any, source_text: str) -> bool:
    try:
        normalized = normalize_decomposition(value, source_text)
    except ValueError:
        return False
    return (
        normalized["classification"] == "target"
        and normalized["decision"] == "approve"
    )


def extract_source_statements(source_text: str, decomposition: Any) -> list[str]:
    normalized = normalize_decomposition(decomposition, source_text)
    if not (
        normalized["classification"] == "target"
        and normalized["decision"] == "approve"
    ):
        return []
    statements = [
        source_text[span["start"] : span["end"]]
        for span in normalized["spans"]
    ]
    if any(not statement.strip() for statement in statements):
        raise ValueError("statement span extracts only whitespace")
    return statements


def stable_parent_key(question: Mapping[str, Any]) -> str:
    for field in (
        "canonical_question_key",
        "canonicalQuestionKey",
        "source_question_id",
        "sourceQuestionKey",
        "public_question_id",
        "original_question_id",
        "originalQuestionId",
    ):
        value = str(question.get(field) or "").strip()
        if value:
            return value
    raise ValueError("stable source identity is required for derived statement IDs")


def derived_source_unique_keys(
    question: Mapping[str, Any],
    decomposition: Any,
) -> list[str]:
    source_text = question.get("questionBodyText")
    if not isinstance(source_text, str):
        raise ValueError("questionBodyText must be string")
    normalized = normalize_decomposition(decomposition, source_text)
    statements = extract_source_statements(source_text, normalized)
    parent = stable_parent_key(question)
    keys: list[str] = []
    for index, statement in enumerate(statements, start=1):
        statement_hash = hashlib.sha256(statement.encode("utf-8")).hexdigest()[:16]
        keys.append(f"{parent}:aggregate-statement:{index}:{statement_hash}")
    return keys


def materialize_decomposition(
    source_question: Mapping[str, Any],
    reviews: Any,
) -> dict[str, Any]:
    source_text = source_question.get("questionBodyText")
    if not isinstance(source_text, str):
        raise ValueError("questionBodyText must be string")
    decomposition = reconcile_reviews(source_text, reviews)
    result: dict[str, Any] = {
        "aggregateAnswerDecomposition": decomposition,
    }
    if is_approved_target(decomposition, source_text):
        result.update(
            questionType="true_false",
            choiceTextList=extract_source_statements(source_text, decomposition),
            sourceUniqueKeys=derived_source_unique_keys(
                source_question,
                decomposition,
            ),
        )
    return result
