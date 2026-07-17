#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.common.question_identity import (
    SOURCE_IDENTITY_BINDING_FIELDS,
    SourceIdentityBinding,
)
from scripts.common.repaso_firestore_schema import _is_law_revision_facts


class LawRevisionHoldMaterializeError(RuntimeError):
    pass


SOURCE_IDENTITY_BINDING_FIELD_SET = frozenset(SOURCE_IDENTITY_BINDING_FIELDS)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def sha256_canonical(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def extract_patch_entries(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict):
        for key in ("patched_questions", "question_bodies", "questions"):
            value = data.get(key)
            if isinstance(value, list):
                entries = value
                break
        else:
            raise LawRevisionHoldMaterializeError(
                "explanation patch question array is required"
            )
    else:
        raise LawRevisionHoldMaterializeError(
            "explanation patch question array is required"
        )
    if any(not isinstance(entry, dict) for entry in entries):
        raise LawRevisionHoldMaterializeError(
            "every explanation patch record must be an object"
        )
    return entries


def patch_question_id(entry: dict[str, Any]) -> str:
    for key in ("original_question_id", "originalQuestionId", "reviewQuestionId"):
        value = text(entry.get(key))
        if value:
            return value
    return ""


def load_queue(path: Path) -> list[tuple[int, dict[str, Any]]]:
    records: list[tuple[int, dict[str, Any]]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, 1):
            stripped = line.strip()
            if not stripped:
                continue
            record = json.loads(stripped)
            if not isinstance(record, dict):
                raise LawRevisionHoldMaterializeError(
                    f"{path}:{line_number}: queue record must be an object"
                )
            records.append((line_number, record))
    return records


WRONG_CHOICE_SUFFIX_RE = re.compile(r"_w(\d+)$")


def law_reference_choice_indexes(record: dict[str, Any]) -> set[int]:
    refs = record.get("lawReferences")
    return {
        ref.get("choiceIndex")
        for ref in refs
        if isinstance(ref, dict) and isinstance(ref.get("choiceIndex"), int)
    } if isinstance(refs, list) else set()


def direct_queue_choice_index(
    record: dict[str, Any],
    *,
    line_number: int,
) -> int:
    indexes = law_reference_choice_indexes(record)
    if len(indexes) != 1:
        raise LawRevisionHoldMaterializeError(
            f"queue line {line_number}: exactly one choiceIndex is required, got {sorted(indexes)}"
        )
    return int(next(iter(indexes)))


def suffix_mapped_queue_choice_index(
    record: dict[str, Any],
    *,
    line_number: int,
    choice_count: int,
) -> int:
    indexes = law_reference_choice_indexes(record)
    original_id = text(record.get("originalQuestionId"))
    question_id = text(record.get("questionId"))
    suffix_match = WRONG_CHOICE_SUFFIX_RE.search(question_id)
    if suffix_match and original_id and question_id.startswith(f"{original_id}_"):
        if len(indexes) != 1:
            raise LawRevisionHoldMaterializeError(
                f"queue line {line_number}: one basis choiceIndex is required for {question_id}"
            )
        correct_index = int(next(iter(indexes)))
        wrong_number = int(suffix_match.group(1))
        wrong_choice_indexes = [index for index in range(choice_count) if index != correct_index]
        if 1 <= wrong_number <= len(wrong_choice_indexes):
            return wrong_choice_indexes[wrong_number - 1]
        raise LawRevisionHoldMaterializeError(
            f"queue line {line_number}: {question_id} is out of range for choice_count={choice_count}"
        )
    return direct_queue_choice_index(record, line_number=line_number)


@dataclass(frozen=True)
class PatchTarget:
    key: str
    original_id: str
    binding: SourceIdentityBinding
    entry: dict[str, Any]


def explicit_source_binding(
    record: dict[str, Any],
    *,
    label: str,
) -> SourceIdentityBinding:
    provided = set(record) & SOURCE_IDENTITY_BINDING_FIELD_SET
    binding = SourceIdentityBinding.from_mapping(record)
    if provided and (
        provided != SOURCE_IDENTITY_BINDING_FIELD_SET
        or not all(text(record.get(field)) for field in SOURCE_IDENTITY_BINDING_FIELDS)
    ):
        raise LawRevisionHoldMaterializeError(
            f"{label}: source identity fields must contain all three non-empty values"
        )
    return binding


def build_patch_targets(patch_entries: list[dict[str, Any]]) -> list[PatchTarget]:
    targets: list[PatchTarget] = []
    seen_exact: set[SourceIdentityBinding] = set()
    seen_legacy: set[str] = set()
    exact_original_ids: set[str] = set()
    legacy_original_ids: set[str] = set()
    for position, entry in enumerate(patch_entries, start=1):
        original_id = patch_question_id(entry)
        if not original_id:
            raise LawRevisionHoldMaterializeError(
                f"patch entry {position}: question identity is required"
            )
        binding = explicit_source_binding(entry, label=f"patch entry {position}")
        if binding.is_complete():
            if binding.review_question_id != original_id:
                raise LawRevisionHoldMaterializeError(
                    f"patch entry {position}: reviewQuestionId must match original question id"
                )
            if binding in seen_exact:
                raise LawRevisionHoldMaterializeError(
                    "duplicate patch source identity: "
                    + " / ".join(binding.as_tuple())
                )
            seen_exact.add(binding)
            exact_original_ids.add(original_id)
            key = "exact:" + "|".join(binding.as_tuple())
        else:
            if original_id in seen_legacy:
                raise LawRevisionHoldMaterializeError(
                    f"duplicate legacy patch question id: {original_id}"
                )
            seen_legacy.add(original_id)
            legacy_original_ids.add(original_id)
            key = f"legacy:{original_id}"
        targets.append(
            PatchTarget(
                key=key,
                original_id=original_id,
                binding=binding,
                entry=entry,
            )
        )
    mixed = sorted(exact_original_ids & legacy_original_ids)
    if mixed:
        raise LawRevisionHoldMaterializeError(
            f"exact and legacy patch identities are mixed: {mixed}"
        )
    return targets


def resolve_queue_target(
    record: dict[str, Any],
    targets: list[PatchTarget],
    *,
    line_number: int,
) -> PatchTarget | None:
    original_id = text(record.get("originalQuestionId"))
    if not original_id:
        raise LawRevisionHoldMaterializeError(
            f"queue line {line_number}: originalQuestionId is required"
        )
    binding = explicit_source_binding(record, label=f"queue line {line_number}")
    candidates = [target for target in targets if target.original_id == original_id]
    if not candidates:
        return None
    if binding.is_complete():
        if binding.review_question_id != original_id:
            raise LawRevisionHoldMaterializeError(
                f"queue line {line_number}: reviewQuestionId must match originalQuestionId"
            )
        exact_candidates = [
            target for target in candidates if target.binding.is_complete()
        ]
        if exact_candidates:
            matches = [
                target for target in exact_candidates if target.binding == binding
            ]
            if len(matches) != 1:
                raise LawRevisionHoldMaterializeError(
                    f"queue line {line_number}: exact source identity does not match patch"
                )
            return matches[0]
    if len(candidates) != 1:
        raise LawRevisionHoldMaterializeError(
            f"queue line {line_number}: legacy originalQuestionId is ambiguous: "
            f"{original_id} matches={len(candidates)}"
        )
    return candidates[0]


def group_queue_records_by_target(
    queue_records: list[tuple[int, dict[str, Any]]],
    targets: list[PatchTarget],
    *,
    skip_missing_patch_ids: bool,
) -> dict[str, tuple[PatchTarget, list[tuple[int, dict[str, Any]]]]]:
    grouped: dict[str, tuple[PatchTarget, list[tuple[int, dict[str, Any]]]]] = {}
    for line_number, record in queue_records:
        target = resolve_queue_target(
            record,
            targets,
            line_number=line_number,
        )
        if target is None:
            if skip_missing_patch_ids:
                continue
            raise LawRevisionHoldMaterializeError(
                "queue originalQuestionId not found in explanation patch: "
                f"{record.get('originalQuestionId')}"
            )
        grouped.setdefault(target.key, (target, []))[1].append(
            (line_number, record)
        )
    return grouped


def list_len(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def choice_count(entry: dict[str, Any], queue_records: list[tuple[int, dict[str, Any]]]) -> int:
    return max(
        len(queue_records),
        list_len(entry.get("explanationText")),
        list_len(entry.get("lawReferences")),
        list_len(entry.get("isLawRelated")),
        list_len(entry.get("lawGroundedExplanationNotNeeded")),
    )


def require_complete_choice_set(
    *,
    original_id: str,
    count: int,
    queue_by_choice: dict[int, tuple[int, dict[str, Any]]],
) -> None:
    expected = set(range(count))
    actual = set(queue_by_choice)
    if actual != expected:
        raise LawRevisionHoldMaterializeError(
            f"{original_id}: queue must contain all choices for hold materialization "
            f"(expected={sorted(expected)} actual={sorted(actual)})"
        )


def build_queue_by_choice(
    original_id: str,
    count: int,
    original_queue_records: list[tuple[int, dict[str, Any]]],
    *,
    use_suffix_mapping: bool,
) -> dict[int, tuple[int, dict[str, Any]]]:
    queue_by_choice: dict[int, tuple[int, dict[str, Any]]] = {}
    for line_number, record in original_queue_records:
        index = (
            suffix_mapped_queue_choice_index(
                record,
                line_number=line_number,
                choice_count=count,
            )
            if use_suffix_mapping
            else direct_queue_choice_index(record, line_number=line_number)
        )
        if index in queue_by_choice:
            raise LawRevisionHoldMaterializeError(
                f"{original_id}: duplicate queue record for choiceIndex={index}"
            )
        queue_by_choice[index] = (line_number, record)
    require_complete_choice_set(
        original_id=original_id,
        count=count,
        queue_by_choice=queue_by_choice,
    )
    return queue_by_choice


def evidence_refs(record: dict[str, Any]) -> list[dict[str, Any]]:
    current_evidence = record.get("currentEvidence")
    refs = current_evidence.get("refs") if isinstance(current_evidence, dict) else None
    if not isinstance(refs, list):
        return []
    return [ref for ref in refs if isinstance(ref, dict)]


def ref_value(law_ref: dict[str, Any], snapshot: dict[str, Any], key: str) -> str:
    return text(law_ref.get(key)) or text(snapshot.get(key))


def normalized_ref(
    evidence_ref: dict[str, Any],
    *,
    choice_index: int,
    ref_index: int,
) -> dict[str, Any]:
    law_ref = evidence_ref.get("lawReference")
    snapshot = evidence_ref.get("snapshot")
    law_ref = law_ref if isinstance(law_ref, dict) else {}
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    ref_id = f"choice_{choice_index + 1}_hold_current_basis_{ref_index + 1}"
    normalized: dict[str, Any] = {
        "refId": ref_id,
        "lawTimeScope": "current",
        "relation": text(law_ref.get("role")) or "current_basis",
        "primaryBasis": ref_index == 0,
    }
    for key in (
        "lawId",
        "lawRevisionId",
        "lawTitle",
        "elm",
        "encodedElm",
        "rootArticleElm",
        "article",
        "paragraph",
        "item",
        "subitem",
        "articleTextHash",
        "textHash",
    ):
        value = ref_value(law_ref, snapshot, key)
        if value:
            normalized[key] = value
    return normalized


def current_snapshot(
    *,
    record: dict[str, Any],
    refs: list[dict[str, Any]],
) -> dict[str, str]:
    question = record.get("question")
    question = question if isinstance(question, dict) else {}
    snapshot: dict[str, str] = {}
    existing_correct = text(question.get("correctChoiceText"))
    if existing_correct:
        snapshot["correctChoiceText"] = existing_correct
    if refs:
        first = refs[0]
        law_ref = first.get("lawReference")
        raw_snapshot = first.get("snapshot")
        law_ref = law_ref if isinstance(law_ref, dict) else {}
        raw_snapshot = raw_snapshot if isinstance(raw_snapshot, dict) else {}
        for key in (
            "lawId",
            "lawRevisionId",
            "lawTitle",
            "article",
            "paragraph",
            "item",
            "subitem",
            "referenceDate",
            "articleTextHash",
        ):
            value = ref_value(law_ref, raw_snapshot, key)
            if value:
                snapshot[key] = value
        source_url = text(raw_snapshot.get("apiUrl"))
        if source_url:
            snapshot["sourceUrl"] = source_url
        verification_status = text(law_ref.get("verificationStatus"))
        if verification_status:
            snapshot["verificationStatus"] = verification_status
    return snapshot


def build_hold_fact(
    *,
    queue_path: Path,
    line_number: int,
    record: dict[str, Any],
    choice_index: int,
) -> dict[str, Any]:
    refs = evidence_refs(record)
    if not refs:
        raise LawRevisionHoldMaterializeError(
            f"queue line {line_number}: at least one currentEvidence ref is required"
        )
    normalized_refs = [
        normalized_ref(ref, choice_index=choice_index, ref_index=ref_index)
        for ref_index, ref in enumerate(refs)
    ]
    display_ref_ids = [ref["refId"] for ref in normalized_refs]
    existing_correct = text((record.get("question") or {}).get("correctChoiceText"))
    source_evidence_version_id = f"law_revision_audit_queue/{queue_path.name}:L{line_number}"
    binding_source = {
        "auditStatus": "hold",
        "questionId": record.get("questionId"),
        "originalQuestionId": record.get("originalQuestionId"),
        "choiceIndex": choice_index,
        "refs": normalized_refs,
        "sourceEvidenceVersionId": source_evidence_version_id,
    }
    fact: dict[str, Any] = {
        "auditStatus": "hold",
        "reviewState": "needs_secondary_review",
        "sourceEvidenceVersionId": source_evidence_version_id,
        "evidenceBindingHash": sha256_canonical(binding_source),
        "current": current_snapshot(record=record, refs=refs),
        "differenceFacts": [
            "出題当時法令と現行法の差分は二次監査待ちです。",
        ],
        "answerImpactFacts": [
            "現行法ベースの正答変更は未確定です。",
        ],
        "notes": [
            "lawRevisionFacts は監査 queue から hold として初期化しました。",
            "現行法条文 snapshot は取得済みです。",
            "出題当時法令との差分と正答への影響は二次監査で確定してください。",
        ],
        "evidenceSummary": {
            "verdict": "hold",
            "explanationText": (
                "現行法の関連条文は取得済みですが、出題当時法令との差分と"
                "正答への影響は二次確認中です。正答変更は断定しません。"
            ),
            "differenceSummary": "出題当時法令と現行法の差分は二次確認中です。",
            "promptContext": (
                "この問題は法令根拠監査の二次確認待ちです。"
                "保存済みの現行法参照だけを使い、出題当時との差分や"
                "正答変更を推測して断定しないでください。"
            ),
            "displayRefIds": display_ref_ids,
            "refs": normalized_refs,
        },
    }
    if existing_correct:
        fact["examTime"] = {
            "correctChoiceText": existing_correct,
            "verificationStatus": "from_existing_question_pending_secondary_review",
        }
    if not _is_law_revision_facts(fact):
        raise LawRevisionHoldMaterializeError(
            f"queue line {line_number}: generated lawRevisionFacts is invalid"
        )
    return fact


def materialize_hold_facts(
    *,
    queue_jsonl_path: Path,
    explanation_patch_path: Path,
    output_path: Path,
    overwrite_existing: bool = False,
    skip_missing_patch_ids: bool = False,
) -> tuple[int, int]:
    queue_records = load_queue(queue_jsonl_path)
    patch_data = load_json(explanation_patch_path)
    patch_entries = extract_patch_entries(patch_data)
    targets = build_patch_targets(patch_entries)
    grouped = group_queue_records_by_target(
        queue_records,
        targets,
        skip_missing_patch_ids=skip_missing_patch_ids,
    )

    updated_questions = 0
    updated_choices = 0
    for target, original_queue_records in grouped.values():
        original_id = target.original_id
        entry = target.entry
        if "lawRevisionFacts" in entry and not overwrite_existing:
            raise LawRevisionHoldMaterializeError(
                f"{original_id}: lawRevisionFacts already exists; pass --overwrite-existing to replace it"
            )
        count = choice_count(entry, original_queue_records)
        try:
            queue_by_choice = build_queue_by_choice(
                original_id,
                count,
                original_queue_records,
                use_suffix_mapping=False,
            )
        except LawRevisionHoldMaterializeError:
            queue_by_choice = build_queue_by_choice(
                original_id,
                count,
                original_queue_records,
                use_suffix_mapping=True,
            )
        facts: list[dict[str, Any]] = []
        for choice_index in range(count):
            line_number, record = queue_by_choice[choice_index]
            facts.append(
                build_hold_fact(
                    queue_path=queue_jsonl_path,
                    line_number=line_number,
                    record=record,
                    choice_index=choice_index,
                )
            )
            updated_choices += 1
        entry["lawRevisionFacts"] = facts
        updated_questions += 1

    dump_json(output_path, patch_data)
    return updated_questions, updated_choices


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "law_revision_audit_queue JSONL から hold の lawRevisionFacts を "
            "21系 explanation patch へ materialize します。"
        )
    )
    parser.add_argument("--queue-jsonl", required=True, type=Path)
    parser.add_argument("--explanation-patch", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--overwrite-existing", action="store_true")
    parser.add_argument(
        "--skip-missing-patch-ids",
        action="store_true",
        help="Ignore queue records whose originalQuestionId is not present in the selected patch.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        updated_questions, updated_choices = materialize_hold_facts(
            queue_jsonl_path=args.queue_jsonl,
            explanation_patch_path=args.explanation_patch,
            output_path=args.output,
            overwrite_existing=args.overwrite_existing,
            skip_missing_patch_ids=args.skip_missing_patch_ids,
        )
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1
    print(f"materialized hold lawRevisionFacts questions: {updated_questions}")
    print(f"materialized hold lawRevisionFacts choices: {updated_choices}")
    print(f"output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
