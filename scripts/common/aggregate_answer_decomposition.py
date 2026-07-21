from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


REVIEW_SCHEMA_VERSION = "aggregate-answer-review/v2"
CANDIDATE_SCHEMA_VERSION = "aggregate-answer-candidates/v1"
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
        "candidateId",
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

_LIST_SPACE = r"[ \t\u3000\u00a0]"
_LIST_BOUNDARY = rf"(?P<boundary>^|[\r\n。！？]){_LIST_SPACE}*"
_CIRCLED_DIGITS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
_KANA_LABELS = "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワ"
_MARKER_PATTERNS = (
    (
        "latin_bracket",
        re.compile(
            rf"{_LIST_BOUNDARY}(?P<marker>【{_LIST_SPACE}*(?P<label>[A-ZＡ-Ｚ]){_LIST_SPACE}*】){_LIST_SPACE}*",
            re.MULTILINE,
        ),
    ),
    (
        "latin",
        re.compile(
            rf"{_LIST_BOUNDARY}(?P<marker>(?P<label>[A-ZＡ-Ｚ])){_LIST_SPACE}+",
            re.MULTILINE,
        ),
    ),
    (
        "kana_bracket",
        re.compile(
            rf"{_LIST_BOUNDARY}(?P<marker>【{_LIST_SPACE}*(?P<label>[{_KANA_LABELS}]){_LIST_SPACE}*】){_LIST_SPACE}*",
            re.MULTILINE,
        ),
    ),
    (
        "circled_digit",
        re.compile(
            rf"{_LIST_BOUNDARY}(?P<marker>(?P<label>[{_CIRCLED_DIGITS}])){_LIST_SPACE}*",
            re.MULTILINE,
        ),
    ),
    (
        "number_parenthesis",
        re.compile(
            rf"{_LIST_BOUNDARY}(?P<marker>[（(]{_LIST_SPACE}*(?P<label>[1-9][0-9]?){_LIST_SPACE}*[）)]){_LIST_SPACE}*",
            re.MULTILINE,
        ),
    ),
    (
        "kana",
        re.compile(
            rf"{_LIST_BOUNDARY}(?P<marker>(?P<label>[{_KANA_LABELS}])){_LIST_SPACE}+",
            re.MULTILINE,
        ),
    ),
)


def _marker_ordinal(family: str, label: str) -> int:
    if family.startswith("latin"):
        normalized = chr(ord(label) - 0xFEE0) if "Ａ" <= label <= "Ｚ" else label
        return ord(normalized) - ord("A")
    if family == "circled_digit":
        return _CIRCLED_DIGITS.index(label) + 1
    if family == "number_parenthesis":
        return int(label)
    if family.startswith("kana"):
        return _KANA_LABELS.index(label)
    raise ValueError("unsupported statement marker family")


def statement_boundary_id(source_hash: str, start: int, end: int) -> str:
    """Return a source-owned boundary ID without exposing an editable offset."""

    if not _SHA256_RE.fullmatch(source_hash):
        raise ValueError("sourceHash must be sha256:<64 hex>")
    if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
        raise ValueError("statement boundary offsets must be integers")
    if start < 0 or end <= start:
        raise ValueError("statement boundary offsets are invalid")
    digest = hashlib.sha256(f"{source_hash}:{start}:{end}".encode("utf-8")).hexdigest()
    return f"boundary:{digest[:24]}"


def statement_candidate_id(
    source_hash: str,
    spans: Sequence[Mapping[str, int]],
) -> str:
    """Return a deterministic ID for one ordered set of source boundaries."""

    boundaries = [
        statement_boundary_id(source_hash, int(span["start"]), int(span["end"]))
        for span in spans
    ]
    encoded = json.dumps(
        {"sourceHash": source_hash, "boundaryIds": boundaries},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return f"candidate:{digest[:24]}"


def generate_statement_candidates(source_text: str) -> dict[str, Any]:
    """Mechanically enumerate sequential list-marker runs in the immutable source."""

    source_hash = source_text_hash(source_text)
    detected: list[dict[str, Any]] = []
    occupied_starts: set[int] = set()
    for family, pattern in _MARKER_PATTERNS:
        for match in pattern.finditer(source_text):
            start = match.start("marker")
            if start in occupied_starts:
                continue
            occupied_starts.add(start)
            detected.append(
                {
                    "family": family,
                    "ordinal": _marker_ordinal(family, match.group("label")),
                    "start": start,
                }
            )
    detected.sort(key=lambda value: int(value["start"]))

    runs: list[tuple[list[dict[str, Any]], int]] = []
    for family in dict.fromkeys(str(value["family"]) for value in detected):
        family_markers = [
            value for value in detected if value["family"] == family
        ]
        current: list[dict[str, Any]] = []
        for marker in family_markers:
            if current and marker["ordinal"] == current[-1]["ordinal"] + 1:
                current.append(marker)
                continue
            if len(current) >= 2:
                next_start = int(marker["start"])
                runs.append((current, next_start))
            current = [marker]
        if len(current) >= 2:
            runs.append((current, len(source_text)))

    candidates: list[dict[str, Any]] = []
    for run, next_detected_start in runs:
        first_ordinal = int(run[0]["ordinal"])
        if first_ordinal not in {0, 1}:
            continue
        spans: list[dict[str, Any]] = []
        for index, marker in enumerate(run):
            start = int(marker["start"])
            raw_end = (
                int(run[index + 1]["start"])
                if index + 1 < len(run)
                else next_detected_start
            )
            end = raw_end
            while end > start and source_text[end - 1].isspace():
                end -= 1
            if end <= start:
                spans = []
                break
            spans.append(
                {
                    "boundaryId": statement_boundary_id(source_hash, start, end),
                    "start": start,
                    "end": end,
                }
            )
        if len(spans) < 2:
            continue
        offset_spans = [
            {"start": int(span["start"]), "end": int(span["end"])}
            for span in spans
        ]
        candidates.append(
            {
                "candidateId": statement_candidate_id(source_hash, offset_spans),
                "spans": spans,
            }
        )
    candidates.sort(key=lambda value: str(value["candidateId"]))
    return {
        "schemaVersion": CANDIDATE_SCHEMA_VERSION,
        "sourceHash": source_hash,
        "candidates": candidates,
    }


def candidate_set_hash(candidate_set: Mapping[str, Any]) -> str:
    normalized = {
        "schemaVersion": candidate_set.get("schemaVersion"),
        "sourceHash": candidate_set.get("sourceHash"),
        "candidates": candidate_set.get("candidates"),
    }
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _candidate_spans(
    source_text: str,
    candidate_set: Mapping[str, Any],
    candidate_id: str,
) -> list[dict[str, int]]:
    if candidate_set.get("schemaVersion") != CANDIDATE_SCHEMA_VERSION:
        raise ValueError("statement candidate schemaVersion mismatch")
    actual_hash = source_text_hash(source_text)
    if candidate_set.get("sourceHash") != actual_hash:
        raise ValueError("statement candidate sourceHash mismatch")
    matches = [
        candidate
        for candidate in candidate_set.get("candidates") or []
        if isinstance(candidate, Mapping)
        and candidate.get("candidateId") == candidate_id
    ]
    if len(matches) != 1:
        raise ValueError("candidateId is not present exactly once")
    raw_spans = matches[0].get("spans")
    if not isinstance(raw_spans, list):
        raise ValueError("statement candidate spans are invalid")
    spans = _normalize_spans(
        [
            {"start": span.get("start"), "end": span.get("end")}
            for span in raw_spans
            if isinstance(span, Mapping)
        ],
        source_length=len(source_text),
    )
    if len(spans) < 2 or len(spans) != len(raw_spans):
        raise ValueError("statement candidate requires at least two valid spans")
    expected_id = statement_candidate_id(actual_hash, spans)
    if expected_id != candidate_id:
        raise ValueError("candidateId does not match source boundaries")
    for raw_span, span in zip(raw_spans, spans):
        if raw_span.get("boundaryId") != statement_boundary_id(
            actual_hash,
            span["start"],
            span["end"],
        ):
            raise ValueError("boundaryId does not match source offsets")
    return spans


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


def normalize_review(
    review: Any,
    source_text: str,
    candidate_set: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate one agent review without accepting any agent-authored text."""

    if not isinstance(review, Mapping) or set(review) != _REVIEW_KEYS:
        raise ValueError(
            "aggregate answer review must contain only schemaVersion, sourceHash, "
            "classification, candidateId, decision, issueCodes"
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
    candidate_id = review.get("candidateId")
    if candidate_id is not None and (
        not isinstance(candidate_id, str) or not candidate_id.startswith("candidate:")
    ):
        raise ValueError("candidateId must be null or a candidate ID")
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
    if classification == "target" and decision == "approve":
        if not candidate_id:
            raise ValueError("approved target requires candidateId")
        _candidate_spans(
            source_text,
            candidate_set or generate_statement_candidates(source_text),
            candidate_id,
        )
    elif candidate_id is not None:
        raise ValueError("only an approved target can contain candidateId")
    if classification == "hold" and decision != "hold":
        raise ValueError("hold classification requires hold decision")
    return {
        "schemaVersion": REVIEW_SCHEMA_VERSION,
        "sourceHash": source_hash,
        "classification": classification,
        "candidateId": candidate_id,
        "decision": decision,
        "issueCodes": sorted(issue_codes),
    }


def reconcile_reviews(
    source_text: str,
    reviews: Any,
    candidate_set: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an approved consensus or a deterministic hold for two reviews."""

    if not isinstance(reviews, list) or len(reviews) != 2:
        raise ValueError("exactly two independent aggregate answer reviews are required")
    effective_candidates = candidate_set or generate_statement_candidates(source_text)
    normalized = [
        normalize_review(review, source_text, effective_candidates)
        for review in reviews
    ]
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
    spans = (
        _candidate_spans(source_text, effective_candidates, agreed["candidateId"])
        if agreed["classification"] == "target"
        and agreed["decision"] == "approve"
        else []
    )
    return {
        "schemaVersion": DECOMPOSITION_SCHEMA_VERSION,
        "sourceHash": actual_hash,
        "classification": agreed["classification"],
        "spans": spans,
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


STABLE_PARENT_IDENTITY_FIELDS = (
    "canonical_question_key",
    "canonicalQuestionKey",
    "source_question_id",
    "sourceQuestionKey",
    "public_question_id",
    "original_question_id",
    "originalQuestionId",
)


def stable_parent_identity(question: Mapping[str, Any]) -> dict[str, str]:
    """Return the one source-owned identity used for every derived key check."""

    for field in STABLE_PARENT_IDENTITY_FIELDS:
        value = str(question.get(field) or "").strip()
        if value:
            return {"field": field, "value": value}
    raise ValueError("stable source identity is required for derived statement IDs")


def stable_parent_key(question: Mapping[str, Any]) -> str:
    return stable_parent_identity(question)["value"]


def derived_source_unique_keys_for_parent(
    parent_key: str,
    source_text: str,
    decomposition: Any,
) -> list[str]:
    if not isinstance(parent_key, str) or not parent_key.strip():
        raise ValueError("stable source identity is required for derived statement IDs")
    normalized = normalize_decomposition(decomposition, source_text)
    statements = extract_source_statements(source_text, normalized)
    keys: list[str] = []
    for index, statement in enumerate(statements, start=1):
        statement_hash = hashlib.sha256(statement.encode("utf-8")).hexdigest()[:16]
        keys.append(
            f"{parent_key.strip()}:aggregate-statement:{index}:{statement_hash}"
        )
    return keys


def derived_source_unique_keys(
    question: Mapping[str, Any],
    decomposition: Any,
) -> list[str]:
    source_text = question.get("questionBodyText")
    if not isinstance(source_text, str):
        raise ValueError("questionBodyText must be string")
    return derived_source_unique_keys_for_parent(
        stable_parent_key(question),
        source_text,
        decomposition,
    )


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
