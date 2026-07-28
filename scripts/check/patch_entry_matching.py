"""Match stage patch entries to source records without depending on array order."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.common.question_identity import (
    SourceIdentityBinding,
    review_question_id,
    source_identity_aliases,
    source_record_ref,
    workflow_identity_aliases,
)


@dataclass(frozen=True)
class MatchedPatchEntry:
    source_index: int
    patch_index: int
    source: dict[str, Any]
    patch: dict[str, Any]


def bind_source_records(
    source_questions: Sequence[dict[str, Any]],
    source_path: Path,
) -> list[dict[str, Any]]:
    """Add the exact source binding that the one-question writer persists."""

    bound: list[dict[str, Any]] = []
    for index, question in enumerate(source_questions):
        record = dict(question)
        record.setdefault("reviewQuestionId", review_question_id(question))
        record.setdefault(
            "sourceRecordRef",
            source_record_ref(source_path.name, index),
        )
        bound.append(record)
    return bound


def _aliases(record: Mapping[str, Any]) -> set[str]:
    return {
        value.strip()
        for value in (
            source_identity_aliases(record)
            | workflow_identity_aliases(record)
        )
        if value and value.strip()
    }


def _display_identity(record: Mapping[str, Any]) -> str:
    binding = SourceIdentityBinding.from_mapping(record)
    return (
        binding.source_question_key
        or binding.review_question_id
        or review_question_id(record)
        or str(record.get("question_url") or "")
        or "<unknown>"
    )


def match_patch_entries(
    source_questions: Sequence[dict[str, Any]],
    patch_entries: Sequence[dict[str, Any]],
    *,
    require_full: bool = True,
) -> tuple[list[MatchedPatchEntry], list[str], list[str]]:
    """Resolve each patch entry to exactly one source record.

    Current writer output is one question per model turn, so shared stage arrays
    may be stored in completion order.  Exact source bindings are authoritative;
    legacy entries without all three binding fields may fall back only to a
    unique source alias.
    """

    errors: list[str] = []
    warnings: list[str] = []
    if require_full and len(source_questions) != len(patch_entries):
        errors.append(
            "count mismatch: source={} patch={}".format(
                len(source_questions),
                len(patch_entries),
            )
        )

    complete_sources: dict[SourceIdentityBinding, int] = {}
    alias_owners: dict[str, set[int]] = {}
    for source_index, source in enumerate(source_questions):
        binding = SourceIdentityBinding.from_mapping(source)
        if binding.is_complete():
            if binding in complete_sources:
                errors.append(
                    "duplicate source identity: {}".format(
                        binding.source_question_key
                    )
                )
            else:
                complete_sources[binding] = source_index
        for alias in _aliases(source):
            alias_owners.setdefault(alias, set()).add(source_index)

    matched: list[MatchedPatchEntry] = []
    claimed_sources: dict[int, int] = {}
    for patch_index, patch in enumerate(patch_entries):
        display_index = patch_index + 1
        patch_binding = SourceIdentityBinding.from_mapping(patch)
        source_index: int | None = None
        if patch_binding.is_complete() and complete_sources:
            source_index = complete_sources.get(patch_binding)
            if source_index is None:
                errors.append(
                    "index {}: exact source identity mismatch ({})".format(
                        display_index,
                        patch_binding.source_question_key,
                    )
                )
                continue
        else:
            candidates: set[int] = set()
            for alias in _aliases(patch):
                candidates.update(alias_owners.get(alias, set()))
            if len(candidates) == 1:
                source_index = next(iter(candidates))
            elif not candidates:
                errors.append(
                    "index {}: source record not found ({})".format(
                        display_index,
                        _display_identity(patch),
                    )
                )
                continue
            else:
                errors.append(
                    "index {}: source identity is ambiguous ({})".format(
                        display_index,
                        _display_identity(patch),
                    )
                )
                continue

        if source_index in claimed_sources:
            errors.append(
                "index {}: duplicate source record ({})".format(
                    display_index,
                    _display_identity(source_questions[source_index]),
                )
            )
            continue
        claimed_sources[source_index] = patch_index
        matched.append(
            MatchedPatchEntry(
                source_index=source_index,
                patch_index=patch_index,
                source=dict(source_questions[source_index]),
                patch=dict(patch),
            )
        )

    if require_full:
        missing = [
            _display_identity(source)
            for source_index, source in enumerate(source_questions)
            if source_index not in claimed_sources
        ]
        if missing:
            errors.append(f"missing source records: {missing}")

    return matched, errors, warnings
