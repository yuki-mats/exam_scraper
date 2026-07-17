#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.common.repaso_firestore_schema import _is_law_revision_facts
from scripts.common.law_audit_sidecar_contract import (
    LAW_AUDIT_REVIEW_STATES,
    LAW_AUDIT_SCHEMA_V2,
    LAW_AUDIT_STATUSES,
    law_audit_sidecar_metadata_errors,
    normalize_audit_review_state,
)
from scripts.merge.merge_utils import source_stem_from_patch_filename
from scripts.common.question_identity import (
    SourceIdentityBinding,
    load_source_record_inventory,
    review_question_id,
    source_identity_aliases,
    workflow_identity_aliases,
)


SOURCE_SUBDIR = "00_source"


class LawRevisionFactsMaterializeError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceQuestion:
    review_id: str
    source_key: str
    source_ref: str
    aliases: frozenset[str]
    record: dict[str, Any]

    @property
    def binding(self) -> SourceIdentityBinding:
        return SourceIdentityBinding.from_values(
            self.source_key,
            self.review_id,
            self.source_ref,
        )


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def dump_json(path: Path, data: Any) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as fh:
            temporary_path = Path(fh.name)
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def extract_question_entries(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [entry for entry in data if isinstance(entry, dict)]
    if isinstance(data, dict):
        questions = data.get("question_bodies") or data.get("questions")
        if isinstance(questions, list):
            return [entry for entry in questions if isinstance(entry, dict)]
    return []


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def read_audit_jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    audit_entries: list[tuple[int, dict[str, Any]]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise LawRevisionFactsMaterializeError(
                    f"{path}:{line_number}: invalid JSON: {exc.msg}"
                ) from exc
            if not isinstance(entry, dict):
                raise LawRevisionFactsMaterializeError(
                    f"{path}:{line_number}: JSONL entry must be an object"
                )
            if not text(entry.get("reviewQuestionId")):
                raise LawRevisionFactsMaterializeError(
                    f"{path}:{line_number}: reviewQuestionId is required"
                )
            audit_entries.append((line_number, entry))
    if not audit_entries:
        raise LawRevisionFactsMaterializeError(f"{path}: audit record is required")
    return audit_entries


def load_source_questions(
    list_group_dir: Path,
    *,
    qualification: str,
    list_group_id: str,
) -> tuple[
    dict[str, list[SourceQuestion]],
    dict[SourceIdentityBinding, SourceQuestion],
]:
    by_alias: dict[str, list[SourceQuestion]] = {}
    by_binding: dict[SourceIdentityBinding, SourceQuestion] = {}
    try:
        inventory = load_source_record_inventory(
            list_group_dir / SOURCE_SUBDIR,
            qualification=qualification,
            list_group_id=list_group_id,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise LawRevisionFactsMaterializeError(str(exc)) from exc
    for item in inventory:
        binding = item.identity.binding
        source = SourceQuestion(
            review_id=binding.review_question_id,
            source_key=binding.source_question_key,
            source_ref=binding.source_record_ref,
            aliases=item.identity.aliases,
            record=dict(item.record),
        )
        by_binding[binding] = source
        for alias in source.aliases:
            by_alias.setdefault(alias, []).append(source)
    return by_alias, by_binding


def resolve_audit_entries(
    path: Path,
    entries: list[tuple[int, dict[str, Any]]],
    *,
    source_by_alias: dict[str, list[SourceQuestion]],
    source_by_binding: dict[SourceIdentityBinding, SourceQuestion],
) -> dict[
    SourceIdentityBinding,
    tuple[int, dict[str, Any], SourceQuestion],
]:
    resolved: dict[
        SourceIdentityBinding,
        tuple[int, dict[str, Any], SourceQuestion],
    ] = {}
    seen_bindings: dict[SourceIdentityBinding, int] = {}
    for line_number, entry in entries:
        review_id = text(entry.get("reviewQuestionId"))
        schema = text(entry.get("schemaVersion")) or "law-revision-audit/v1"
        source_key = text(entry.get("sourceQuestionKey"))
        source_ref = text(entry.get("sourceRecordRef"))

        if schema == LAW_AUDIT_SCHEMA_V2:
            if not source_key:
                raise LawRevisionFactsMaterializeError(
                    f"{path}:{line_number}: sourceQuestionKey is required for v2"
                )
            if not source_ref:
                raise LawRevisionFactsMaterializeError(
                    f"{path}:{line_number}: sourceRecordRef is required for v2"
                )
            requested_binding = SourceIdentityBinding.from_values(
                source_key,
                review_id,
                source_ref,
            )
            source = source_by_binding.get(requested_binding)
            if source is None:
                raise LawRevisionFactsMaterializeError(
                    f"{path}:{line_number}: source identity binding does not join "
                    f"{SOURCE_SUBDIR}: {source_key} / {review_id} / {source_ref}"
                )
        elif schema == "law-revision-audit/v1":
            candidates = source_by_alias.get(review_id, [])
            if len(candidates) != 1:
                raise LawRevisionFactsMaterializeError(
                    f"{path}:{line_number}: legacy reviewQuestionId does not safely join "
                    f"{SOURCE_SUBDIR}: {review_id}"
                )
            source = candidates[0]
            if source_key and source.source_key != source_key:
                raise LawRevisionFactsMaterializeError(
                    f"{path}:{line_number}: sourceQuestionKey and reviewQuestionId "
                    "resolve to different source questions"
                )
        else:
            raise LawRevisionFactsMaterializeError(
                f"{path}:{line_number}: unsupported schemaVersion: {schema}"
            )

        binding = source.binding
        previous_line = seen_bindings.get(binding)
        if previous_line is not None:
            raise LawRevisionFactsMaterializeError(
                f"{path}:{line_number}: duplicate source identity binding "
                f"(first at line {previous_line})"
            )
        seen_bindings[binding] = line_number
        resolved[binding] = (line_number, entry, source)
    return resolved


def load_patch_question_map(
    path: Path,
    *,
    source_by_alias: dict[str, list[SourceQuestion]],
    source_by_binding: dict[SourceIdentityBinding, SourceQuestion],
) -> tuple[Any, dict[SourceIdentityBinding, dict[str, Any]]]:
    data = load_json(path)
    raw_entries: Any
    if isinstance(data, list):
        raw_entries = data
    elif isinstance(data, dict):
        raw_entries = data.get("question_bodies") or data.get("questions")
    else:
        raw_entries = None
    if not isinstance(raw_entries, list):
        raise LawRevisionFactsMaterializeError(
            f"{path}: patch question array is required"
        )
    if any(not isinstance(entry, dict) for entry in raw_entries):
        raise LawRevisionFactsMaterializeError(
            f"{path}: every patch record must be an object"
        )
    resolved: dict[SourceIdentityBinding, dict[str, Any]] = {}
    patch_tag = {
        "21_explanationText_added": "explanationText_added",
        "23_correctChoiceText_fixed": "correctChoiceText_fixed",
    }.get(path.parent.name)
    source_stem = (
        source_stem_from_patch_filename(path.name, patch_tag)
        if patch_tag
        else None
    )
    expected_source_filename = f"{source_stem}.json" if source_stem else ""
    for entry in raw_entries:
        explicit_source_key = text(
            entry.get("sourceQuestionKey") or entry.get("source_question_key")
        )
        source_review_id = text(review_question_id(entry))
        explicit_source_ref = text(
            entry.get("sourceRecordRef") or entry.get("source_record_ref")
        )
        explicit_binding = SourceIdentityBinding.from_values(
            explicit_source_key,
            source_review_id,
            explicit_source_ref,
        )
        if explicit_binding.is_complete():
            if explicit_binding not in source_by_binding:
                raise LawRevisionFactsMaterializeError(
                    f"{path}: source identity binding does not match source: "
                    f"{' / '.join(explicit_binding.as_tuple())}"
                )
            matches = {explicit_binding}
        elif explicit_source_key and source_review_id:
            matches = {
                binding
                for binding in source_by_binding
                if (
                    binding.source_question_key,
                    binding.review_question_id,
                )
                == (explicit_source_key, source_review_id)
                and (
                    not expected_source_filename
                    or binding.source_record_ref.split("#", 1)[0]
                    == expected_source_filename
                )
            }
        else:
            identity_values = (
                source_identity_aliases(entry) | workflow_identity_aliases(entry)
            )
            candidate_pairs = {
                (source.source_key, source.review_id)
                for alias in identity_values
                for source in source_by_alias.get(alias, [])
            }
            matches = {
                binding
                for binding in source_by_binding
                if (
                    binding.source_question_key,
                    binding.review_question_id,
                )
                in candidate_pairs
                and (
                    not expected_source_filename
                    or binding.source_record_ref.split("#", 1)[0]
                    == expected_source_filename
                )
            }
        if not matches:
            identity_values = sorted(
                source_identity_aliases(entry)
                | workflow_identity_aliases(entry)
            )
            raise LawRevisionFactsMaterializeError(
                f"{path}: patch record does not join {SOURCE_SUBDIR}: "
                f"{identity_values or ['identity field missing']}"
            )
        if len(matches) != 1:
            raise LawRevisionFactsMaterializeError(
                f"{path}: patch identity fields resolve to multiple source questions"
            )
        binding = next(iter(matches))
        source = source_by_binding[binding]
        review_id = source.review_id
        invalid_workflow_ids = workflow_identity_aliases(entry) - source.aliases
        if invalid_workflow_ids:
            raise LawRevisionFactsMaterializeError(
                f"{path}: workflow id does not match source identity for {review_id}: "
                f"{sorted(invalid_workflow_ids)}"
            )
        entry_source_key = text(
            entry.get("sourceQuestionKey") or entry.get("source_question_key")
        )
        if entry_source_key and entry_source_key != source.source_key:
            raise LawRevisionFactsMaterializeError(
                f"{path}: sourceQuestionKey does not match source identity for {review_id}"
            )
        if binding in resolved:
            raise LawRevisionFactsMaterializeError(
                f"{path}: duplicate patch record for {source.source_key} / {review_id}"
            )
        resolved[binding] = entry
    return data, resolved


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def choice_value(value: Any, index: int) -> str:
    if isinstance(value, list):
        if index < len(value) and value[index] is not None:
            return str(value[index]).strip()
        return ""
    if value is None:
        return ""
    return str(value).strip()


def first_non_empty(values: Iterable[Any]) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def normalized_ref(ref: dict[str, Any], *, choice_index: int, ref_index: int) -> dict[str, Any]:
    ref_id = first_non_empty(
        [
            ref.get("refId"),
            f"choice_{choice_index + 1}_current_basis_{ref_index + 1}",
        ]
    )
    normalized: dict[str, Any] = {
        "refId": ref_id,
        "lawTimeScope": "current",
        "relation": first_non_empty([ref.get("role"), ref.get("relation"), "basis"]),
        "primaryBasis": ref_index == 0,
    }
    for source_key, target_key in (
        ("lawId", "lawId"),
        ("lawRevisionId", "lawRevisionId"),
        ("lawTitle", "lawTitle"),
        ("elm", "elm"),
        ("encodedElm", "encodedElm"),
        ("rootArticleElm", "rootArticleElm"),
        ("article", "article"),
        ("paragraph", "paragraph"),
        ("item", "item"),
        ("subitem", "subitem"),
        ("articleTextHash", "articleTextHash"),
        ("textHash", "textHash"),
    ):
        text = first_non_empty([ref.get(source_key)])
        if text:
            normalized[target_key] = text
    highlight_elms = ref.get("highlightElms")
    if isinstance(highlight_elms, list):
        normalized["highlightElms"] = [str(item) for item in highlight_elms if str(item).strip()]
    return normalized


def snapshot_from_ref(refs: list[dict[str, Any]], *, correct_choice_text: str) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    if correct_choice_text:
        snapshot["correctChoiceText"] = correct_choice_text
    if refs:
        first_ref = refs[0]
        for key in (
            "lawId",
            "lawRevisionId",
            "lawTitle",
            "article",
            "paragraph",
            "item",
            "subitem",
            "referenceDate",
            "verificationStatus",
            "articleTextHash",
            "sourceUrl",
        ):
            text = first_non_empty([first_ref.get(key)])
            if text:
                snapshot[key] = text
    return snapshot


def verdict_from_correct_choice(value: str) -> str:
    if value == "正しい":
        return "correct"
    if value == "間違い":
        return "incorrect"
    return value or "unknown"


def sha256_canonical(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_fact(
    *,
    audit_path: Path,
    line_number: int,
    audit: dict[str, Any],
    source_review_id: str,
    source_question: dict[str, Any],
    source_record_ref_value: str,
    explanation_entry: dict[str, Any],
    current_entry: dict[str, Any],
    choice_index: int,
) -> dict[str, Any]:
    raw_refs = as_list(choice_value_container(explanation_entry.get("lawReferences"), choice_index))
    if not raw_refs:
        raw_refs = as_list(choice_value_container(audit.get("lawReferences"), choice_index))
    refs = [
        normalized_ref(ref, choice_index=choice_index, ref_index=ref_index)
        for ref_index, ref in enumerate(raw_refs)
        if isinstance(ref, dict)
    ]
    display_ref_ids = [ref["refId"] for ref in refs]
    old_correct = choice_value(source_question.get("correctChoiceText"), choice_index)
    current_correct = choice_value(current_entry.get("correctChoiceText"), choice_index)
    explanation_text = choice_value(explanation_entry.get("explanationText"), choice_index)
    notice_reason = first_non_empty([audit.get("noticeReason")])
    source_summary = first_non_empty([audit.get("sourceSummary")])
    remaining_risk = first_non_empty([audit.get("remainingRisk")])
    exam_time_decision = choice_value(audit.get("examTimeDecision"), choice_index)
    current_law_decision = choice_value(
        audit.get("currentLawDecision"), choice_index
    )
    current_snapshot = snapshot_from_ref(
        [ref for ref in raw_refs if isinstance(ref, dict)],
        correct_choice_text=current_correct,
    )
    exam_time_snapshot = {"correctChoiceText": old_correct} if old_correct else {}
    if old_correct:
        exam_time_snapshot["verificationStatus"] = "from_original_answer"

    source_evidence_version_id = (
        f"law_revision_audit/{audit_path.name}:L{line_number}"
    )
    binding_source = {
        "auditStatus": audit.get("auditStatus"),
        "currentCorrectChoiceText": current_correct,
        "currentLawDecision": current_law_decision,
        "examTimeCorrectChoiceText": old_correct,
        "examTimeDecision": exam_time_decision,
        "refs": refs,
        "reviewQuestionId": source_review_id,
        "sourceRecordRef": source_record_ref_value,
        "sourceEvidenceVersionId": source_evidence_version_id,
        "choiceIndex": choice_index,
    }
    fact: dict[str, Any] = {
        "auditStatus": first_non_empty([audit.get("auditStatus"), "hold"]),
        "reviewState": normalize_audit_review_state(audit.get("reviewState")),
        "sourceEvidenceVersionId": source_evidence_version_id,
        "evidenceBindingHash": sha256_canonical(binding_source),
        "examTime": exam_time_snapshot,
        "current": current_snapshot,
        "differenceFacts": [
            text for text in (notice_reason, source_summary) if text
        ],
        "answerImpactFacts": [
            text
            for text in (
                first_non_empty(
                    [
                        f"出題当時の正誤: {old_correct}。現行法ベースの正誤: {current_correct}。"
                        if old_correct or current_correct
                        else ""
                    ]
                ),
                current_law_decision,
            )
            if text
        ],
        "evidenceSummary": {
            "verdict": verdict_from_correct_choice(current_correct),
            "displayRefIds": display_ref_ids,
            "refs": refs,
        },
    }
    if explanation_text:
        fact["evidenceSummary"]["explanationText"] = explanation_text
    if notice_reason:
        fact["evidenceSummary"]["differenceSummary"] = notice_reason
    prompt_context = (
        "この選択肢は法令根拠監査済みです。"
        f"出題当時の正誤は「{old_correct or '未記録'}」、"
        f"現行法ベースの正誤は「{current_correct or '未記録'}」。"
        "AI解説では保存済み根拠の範囲で、必要に応じて出題当時と現行法の違いを明示してください。"
    )
    fact["evidenceSummary"]["promptContext"] = prompt_context
    notes = [
        text
        for text in (
            remaining_risk,
            exam_time_decision,
            first_non_empty([audit.get("auditedAt"), audit.get("reviewedAt")]),
        )
        if text
    ]
    if notes:
        fact["notes"] = notes
    for field in (
        "auditedAt",
        "nextAuditDueAt",
        "auditMethodVersion",
        "auditInputHash",
        "auditRunId",
        "lawCorpusSnapshotId",
        "primaryAuditRunId",
        "secondaryAuditRunId",
        "tertiaryAuditRunId",
        "reconciliationStatus",
    ):
        value = audit.get(field)
        if isinstance(value, str) and value.strip():
            fact[field] = value.strip()
    if not _is_law_revision_facts(fact):
        raise LawRevisionFactsMaterializeError(
            f"{source_review_id} choice {choice_index + 1}: "
            "generated lawRevisionFacts is invalid"
        )
    return fact


def choice_value_container(value: Any, index: int) -> Any:
    if isinstance(value, list) and index < len(value):
        return value[index]
    return []


def choice_count_for(
    source_question: dict[str, Any],
    explanation_entry: dict[str, Any],
    current_entry: dict[str, Any],
) -> int:
    lengths = [
        len(as_list(source_question.get("choiceTextList"))),
        len(as_list(source_question.get("correctChoiceText"))),
        len(as_list(current_entry.get("correctChoiceText"))),
        len(as_list(explanation_entry.get("explanationText"))),
        len(as_list(explanation_entry.get("lawReferences"))),
    ]
    return max(lengths)


def materialize_law_revision_facts(
    *,
    list_group_dir: Path,
    audit_jsonl_path: Path,
    explanation_patch_path: Path,
    correct_choice_patch_path: Path,
    qualification: str | None = None,
    list_group_id: str | None = None,
) -> int:
    inferred_group_id = str(list_group_id or list_group_dir.name).strip()
    inferred_qualification = str(qualification or "").strip()
    audit_entries = read_audit_jsonl(audit_jsonl_path)
    if not inferred_qualification and list_group_dir.parent.name == "questions_json":
        inferred_qualification = list_group_dir.parent.parent.name
    if not inferred_qualification:
        qualifications = {
            text(entry.get("qualification"))
            for _line_number, entry in audit_entries
            if entry.get("schemaVersion") == LAW_AUDIT_SCHEMA_V2
            and text(entry.get("qualification"))
        }
        if len(qualifications) == 1:
            inferred_qualification = next(iter(qualifications))
    if not inferred_qualification:
        inferred_qualification = "unknown-qualification"
    source_by_alias, source_by_binding = load_source_questions(
        list_group_dir,
        qualification=inferred_qualification,
        list_group_id=inferred_group_id,
    )
    audits = resolve_audit_entries(
        audit_jsonl_path,
        audit_entries,
        source_by_alias=source_by_alias,
        source_by_binding=source_by_binding,
    )
    patch_data, explanation_map = load_patch_question_map(
        explanation_patch_path,
        source_by_alias=source_by_alias,
        source_by_binding=source_by_binding,
    )
    _current_data, current_map = load_patch_question_map(
        correct_choice_patch_path,
        source_by_alias=source_by_alias,
        source_by_binding=source_by_binding,
    )
    if not explanation_map:
        raise LawRevisionFactsMaterializeError(
            f"{explanation_patch_path}: sourceに対応するpatch recordがありません"
        )
    for binding, explanation_entry in explanation_map.items():
        resolved_audit = audits.get(binding)
        if resolved_audit is None:
            raise LawRevisionFactsMaterializeError(
                f"{binding.review_question_id} / {binding.source_record_ref}: "
                "audit sidecar record not found"
            )
        line_number, audit, source = resolved_audit
        review_id = source.review_id
        current_entry = current_map.get(binding)
        if current_entry is None:
            raise LawRevisionFactsMaterializeError(
                f"{review_id}: current correctChoiceText patch not found"
            )
        choice_count = choice_count_for(
            source.record,
            explanation_entry,
            current_entry,
        )
        if choice_count <= 0:
            raise LawRevisionFactsMaterializeError(
                f"{review_id}: choice count could not be resolved"
            )
        if audit.get("schemaVersion") == LAW_AUDIT_SCHEMA_V2:
            metadata_errors = law_audit_sidecar_metadata_errors(
                audit,
                expected_choice_count=choice_count,
                expected_qualification=inferred_qualification,
                expected_list_group_id=inferred_group_id,
            )
            if metadata_errors:
                raise LawRevisionFactsMaterializeError(
                    f"{audit_jsonl_path}:{line_number}: invalid v2 audit metadata: "
                    + " ".join(metadata_errors[:5])
                    + (
                        f" ほか{len(metadata_errors) - 5}件。"
                        if len(metadata_errors) > 5
                        else ""
                    )
                )
        explanation_entry["lawRevisionFacts"] = [
            build_fact(
                audit_path=audit_jsonl_path,
                line_number=line_number,
                audit=audit,
                source_review_id=review_id,
                source_record_ref_value=source.source_ref,
                source_question=source.record,
                explanation_entry=explanation_entry,
                current_entry=current_entry,
                choice_index=choice_index,
            )
            for choice_index in range(choice_count)
        ]
    dump_json(explanation_patch_path, patch_data)
    return len(explanation_map)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="law_revision_audit JSONL から lawRevisionFacts を21系 explanation patchへ materialize します。"
    )
    parser.add_argument("--list-group-dir", required=True, type=Path)
    parser.add_argument("--audit-jsonl", required=True, type=Path)
    parser.add_argument("--explanation-patch", required=True, type=Path)
    parser.add_argument("--correct-choice-patch", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    updated = materialize_law_revision_facts(
        list_group_dir=args.list_group_dir,
        audit_jsonl_path=args.audit_jsonl,
        explanation_patch_path=args.explanation_patch,
        correct_choice_patch_path=args.correct_choice_patch,
    )
    print(f"materialized lawRevisionFacts for {updated} audit entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
