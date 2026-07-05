#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


QUALIFICATION = "2nd-class-kenchikushi"
LIST_GROUP_IDS = [str(value) for value in range(85003, 85012)]
PATCH_SUBDIR = "21_explanationText_added"
PATCH_GLOB = "question_{list_group_id}_law_merged_explanationText_added_*.json"


def now_jst() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def timestamp_for_file(value: datetime) -> str:
    return value.strftime("%Y%m%d_%H%M%S")


def timestamp_for_fact(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def latest_law_patch(group_dir: Path, list_group_id: str) -> Path:
    candidates = sorted((group_dir / PATCH_SUBDIR).glob(PATCH_GLOB.format(list_group_id=list_group_id)))
    if not candidates:
        raise FileNotFoundError(f"no law patch found: {group_dir / PATCH_SUBDIR}")
    return candidates[-1]


def non_empty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def iter_law_reference_dicts(value: Any):
    if isinstance(value, dict):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from iter_law_reference_dicts(item)


def verified_law_references(value: Any) -> tuple[bool, int]:
    refs = list(iter_law_reference_dicts(value))
    if not refs:
        return False, 0
    for ref in refs:
        if ref.get("verificationStatus") != "verified":
            return False, len(refs)
        if not non_empty_text(ref.get("lawId")) or not non_empty_text(ref.get("article")):
            return False, len(refs)
    return True, len(refs)


def evidence_refs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    summary = value.get("evidenceSummary")
    if not isinstance(summary, dict):
        return []
    refs = summary.get("refs")
    if not isinstance(refs, list):
        return []
    return [ref for ref in refs if isinstance(ref, dict)]


def can_finalize(entry: dict[str, Any]) -> tuple[bool, list[str]]:
    facts = entry.get("lawRevisionFacts")
    if not isinstance(facts, dict):
        return False, ["missing lawRevisionFacts"]
    exam = facts.get("examTime") if isinstance(facts.get("examTime"), dict) else {}
    current = facts.get("current") if isinstance(facts.get("current"), dict) else {}
    exam_choice = exam.get("correctChoiceText")
    current_choice = current.get("correctChoiceText")
    reasons: list[str] = []
    if not non_empty_text(exam_choice) or not non_empty_text(current_choice):
        reasons.append("missing correctChoiceText in examTime/current")
    elif exam_choice != current_choice:
        reasons.append("examTime/current correctChoiceText mismatch")

    refs_ok, _ = verified_law_references(entry.get("lawReferences"))
    if not refs_ok:
        reasons.append("top-level lawReferences are missing or not verified")
    if not evidence_refs(facts):
        reasons.append("missing lawRevisionFacts.evidenceSummary.refs")

    if entry.get("isLawRelated") is not True:
        reasons.append("entry is not marked isLawRelated=true")
    return not reasons, reasons


def finalize_fact(
    *,
    facts: dict[str, Any],
    list_group_id: str,
    original_question_id: str,
    stamp: str,
    audited_at: str,
    source_summary_path: str,
    ref_count: int,
) -> None:
    previous_difference = list(facts.get("differenceFacts") or [])
    previous_notes = [str(value) for value in facts.get("notes") or [] if str(value).strip()]
    exam = facts.get("examTime") if isinstance(facts.get("examTime"), dict) else {}
    current = facts.get("current") if isinstance(facts.get("current"), dict) else {}
    decision = current.get("correctChoiceText") or exam.get("correctChoiceText") or ""
    final_run_id = f"final-law-audit-all-years-{stamp}-{list_group_id}-{original_question_id}"

    facts["auditStatus"] = "same_as_current"
    facts["reviewState"] = "tertiary_verified"
    facts["auditedAt"] = audited_at
    facts["auditMethodVersion"] = "law-grounded-audit-final-v1-current-law-convergence-20260705"
    facts["auditRunId"] = final_run_id
    facts["tertiaryAuditRunId"] = final_run_id
    facts["reconciliationStatus"] = "approved"
    facts["sourceEvidenceVersionId"] = f"{source_summary_path}:{list_group_id}:{original_question_id}"

    final_difference = previous_difference + [
        "最終監査で top-level lawReferences の verificationStatus=verified/lawId/article を確認しました。",
        f"最終監査で出題当時正答と現行法ベース正答が一致することを確認しました（{decision}）。",
    ]
    facts["differenceFacts"] = [value for value in final_difference if isinstance(value, str) and value.strip()]
    facts["answerImpactFacts"] = [
        "出題当時正答と現行法ベース正答が一致し、保存済み lawReferences は verified のため、correctChoiceText / explanationText の現行法更新は不要です。",
    ]
    facts["notes"] = [
        f"二次 hold は最終監査で解消しました。保存済み lawReferences verified count={ref_count} を根拠として使用しています。",
        "二次 hold 時点の補助根拠不足・current-law-only 注意点は differenceFacts と旧監査 sidecar に保存しています。",
        *previous_notes[:2],
    ]
    facts["evidenceSummary"] = {
        "verdict": "same_as_current",
        "explanationText": "最終監査済みです。出題当時正答と現行法ベース正答は一致し、正答・基本解説への更新は不要です。",
        "differenceSummary": "二次 hold を最終監査で same_as_current に収束。correctChoiceText / explanationText 更新なし。",
        "promptContext": "最終監査で same_as_current と確定済みです。AI は現行法ベースの正答変更を推測せず、保存済み根拠の範囲で説明してください。",
        "displayRefIds": (facts.get("evidenceSummary") or {}).get("displayRefIds", []),
        "refs": evidence_refs(facts),
    }


def run(*, repo_root: Path, dry_run: bool, timestamp: str | None) -> int:
    qualified_root = repo_root / "output" / QUALIFICATION
    questions_root = qualified_root / "questions_json"
    reviewed_at = now_jst()
    stamp = timestamp or timestamp_for_file(reviewed_at)
    audited_at = timestamp_for_fact(reviewed_at)
    summary_path = (
        qualified_root
        / "review"
        / "law_revision_audit"
        / f"final_current_law_convergence_summary_{stamp}.json"
    )
    source_summary_id = f"law_revision_audit/{summary_path.name}"

    total = Counter()
    group_summaries: dict[str, Any] = {}
    skipped: list[dict[str, Any]] = []

    for list_group_id in LIST_GROUP_IDS:
        group_dir = questions_root / list_group_id
        patch_path = latest_law_patch(group_dir, list_group_id)
        entries = load_json(patch_path)
        if not isinstance(entries, list):
            raise RuntimeError(f"unsupported patch shape: {patch_path}")

        changed = False
        group_counts = Counter()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            facts = entry.get("lawRevisionFacts")
            if not isinstance(facts, dict):
                continue
            status = facts.get("auditStatus")
            group_counts[f"before_{status}"] += 1
            total[f"before_{status}"] += 1
            if status != "hold":
                continue

            original_id = str(entry.get("original_question_id") or "")
            ok, reasons = can_finalize(entry)
            if not ok:
                skipped.append(
                    {
                        "listGroupId": list_group_id,
                        "originalQuestionId": original_id,
                        "reasons": reasons,
                    }
                )
                group_counts["skippedHold"] += 1
                total["skippedHold"] += 1
                continue

            _, ref_count = verified_law_references(entry.get("lawReferences"))
            finalize_fact(
                facts=facts,
                list_group_id=list_group_id,
                original_question_id=original_id,
                stamp=stamp,
                audited_at=audited_at,
                source_summary_path=source_summary_id,
                ref_count=ref_count,
            )
            changed = True
            group_counts["finalizedHold"] += 1
            total["finalizedHold"] += 1

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            facts = entry.get("lawRevisionFacts")
            if isinstance(facts, dict):
                group_counts[f"after_{facts.get('auditStatus')}"] += 1
                total[f"after_{facts.get('auditStatus')}"] += 1

        if changed and not dry_run:
            write_json(patch_path, entries)
        group_summaries[list_group_id] = {
            "patchFile": str(patch_path),
            "counts": dict(group_counts),
            "changed": changed,
        }

    summary = {
        "schemaVersion": "2nd-class-kenchikushi-final-current-law-convergence/v1",
        "qualification": QUALIFICATION,
        "auditedAt": audited_at,
        "dryRun": dry_run,
        "groups": group_summaries,
        "counts": dict(total),
        "skipped": skipped,
    }
    if not dry_run:
        write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if skipped:
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Finalize remaining 2nd-class-kenchikushi current-law holds when saved evidence converges."
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timestamp")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run(repo_root=args.repo_root.resolve(), dry_run=args.dry_run, timestamp=args.timestamp)


if __name__ == "__main__":
    raise SystemExit(main())
