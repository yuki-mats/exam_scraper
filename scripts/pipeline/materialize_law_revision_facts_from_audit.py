#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.common.repaso_firestore_schema import _is_law_revision_facts


SOURCE_SUBDIR = "20_merged_1"


class LawRevisionFactsMaterializeError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def dump_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def extract_question_entries(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [entry for entry in data if isinstance(entry, dict)]
    if isinstance(data, dict):
        questions = data.get("question_bodies") or data.get("questions")
        if isinstance(questions, list):
            return [entry for entry in questions if isinstance(entry, dict)]
    return []


def question_id(entry: dict[str, Any]) -> str:
    for key in (
        "original_question_id",
        "originalQuestionId",
        "reviewQuestionId",
        "public_question_id",
    ):
        value = entry.get(key)
        if value:
            return str(value)
    return ""


def read_audit_jsonl(path: Path) -> dict[str, tuple[int, dict[str, Any]]]:
    audit_entries: dict[str, tuple[int, dict[str, Any]]] = {}
    with path.open("r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, 1):
            stripped = line.strip()
            if not stripped:
                continue
            entry = json.loads(stripped)
            if not isinstance(entry, dict):
                raise LawRevisionFactsMaterializeError(
                    f"{path}:{line_number}: JSONL entry must be an object"
                )
            key = question_id(entry)
            if not key:
                raise LawRevisionFactsMaterializeError(
                    f"{path}:{line_number}: reviewQuestionId is required"
                )
            audit_entries[key] = (line_number, entry)
    return audit_entries


def load_source_question_map(list_group_dir: Path) -> dict[str, dict[str, Any]]:
    source_dir = list_group_dir / SOURCE_SUBDIR
    source_map: dict[str, dict[str, Any]] = {}
    for source_path in sorted(source_dir.glob("*.json")):
        for question in extract_question_entries(load_json(source_path)):
            key = question_id(question)
            if key:
                source_map[key] = question
    return source_map


def load_patch_question_map(path: Path) -> dict[str, dict[str, Any]]:
    return {
        key: entry
        for entry in extract_question_entries(load_json(path))
        if (key := question_id(entry))
    }


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
    source_question: dict[str, Any],
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
        "currentLawDecision": audit.get("currentLawDecision"),
        "examTimeCorrectChoiceText": old_correct,
        "examTimeDecision": audit.get("examTimeDecision"),
        "refs": refs,
        "reviewQuestionId": question_id(audit),
        "sourceEvidenceVersionId": source_evidence_version_id,
        "choiceIndex": choice_index,
    }
    fact: dict[str, Any] = {
        "auditStatus": first_non_empty([audit.get("auditStatus"), "hold"]),
        "reviewState": "primary_verified",
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
                first_non_empty([audit.get("currentLawDecision")]),
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
            first_non_empty([audit.get("examTimeDecision")]),
            first_non_empty([audit.get("reviewedAt")]),
        )
        if text
    ]
    if notes:
        fact["notes"] = notes
    if not _is_law_revision_facts(fact):
        raise LawRevisionFactsMaterializeError(
            f"{question_id(audit)} choice {choice_index + 1}: generated lawRevisionFacts is invalid"
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
) -> int:
    audit_entries = read_audit_jsonl(audit_jsonl_path)
    source_map = load_source_question_map(list_group_dir)
    current_map = load_patch_question_map(correct_choice_patch_path)
    patch_data = load_json(explanation_patch_path)
    patch_entries = extract_question_entries(patch_data)
    updated = 0
    for entry in patch_entries:
        key = question_id(entry)
        if not key or key not in audit_entries:
            continue
        line_number, audit = audit_entries[key]
        source_question = source_map.get(key)
        current_entry = current_map.get(key)
        if not source_question:
            raise LawRevisionFactsMaterializeError(f"{key}: source question not found in {SOURCE_SUBDIR}")
        if not current_entry:
            raise LawRevisionFactsMaterializeError(f"{key}: current correctChoiceText patch not found")
        choice_count = choice_count_for(source_question, entry, current_entry)
        if choice_count <= 0:
            raise LawRevisionFactsMaterializeError(f"{key}: choice count could not be resolved")
        entry["lawRevisionFacts"] = [
            build_fact(
                audit_path=audit_jsonl_path,
                line_number=line_number,
                audit=audit,
                source_question=source_question,
                explanation_entry=entry,
                current_entry=current_entry,
                choice_index=choice_index,
            )
            for choice_index in range(choice_count)
        ]
        updated += 1
    dump_json(explanation_patch_path, patch_data)
    return updated


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
