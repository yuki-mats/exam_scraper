#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.pipeline.finalize_gas_shunin_law_explanations import (  # noqa: E402
    normalize_text,
    write_json,
)


DEFAULT_REFRESH_DIR = (
    ROOT_DIR / "output" / "gas-shunin-all" / "review" / "law_explanation_refresh"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_ledger_row(path: Path, audit_key: str) -> dict[str, Any]:
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("auditKey") == audit_key:
            return row
    raise ValueError(f"auditKey not found in ledger: {audit_key}")


def find_patch_entry(payload: list[Any], ledger_row: dict[str, Any]) -> dict[str, Any]:
    expected_choices = [
        normalize_text(choice.get("choiceText")) for choice in ledger_row.get("choices") or []
    ]
    candidates = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        choices = entry.get("choiceTextList")
        if not isinstance(choices, list):
            continue
        if [normalize_text(choice) for choice in choices] == expected_choices:
            candidates.append(entry)
    if len(candidates) != 1:
        raise ValueError(
            f"patch entry match count={len(candidates)}: {ledger_row.get('auditKey')}"
        )
    return candidates[0]


def strip_verdict(explanation: str) -> str:
    for prefix in ("正しい。", "間違い。"):
        if explanation.startswith(prefix):
            return explanation.removeprefix(prefix).strip()
    return explanation


def update_law_revision_facts(
    entry: dict[str, Any],
    *,
    explanations: list[str],
    correction: dict[str, Any],
    reviewed_at: str,
) -> None:
    facts = entry.get("lawRevisionFacts")
    if not isinstance(facts, list) or len(facts) != len(explanations):
        raise ValueError("lawRevisionFacts length mismatch")
    law_id = str(correction["lawId"])
    law_title = str(correction["lawTitle"])
    article = str(correction["article"])
    paragraph = str(correction.get("paragraph") or "")
    item_by_choice = correction.get("itemsByChoiceIndex") or {}
    source_url = f"https://laws.e-gov.go.jp/api/1/articles;lawId={law_id};article={article}"
    reference_date = reviewed_at[:10]

    for choice_index, (fact, explanation) in enumerate(zip(facts, explanations, strict=True)):
        if not isinstance(fact, dict):
            raise ValueError(f"lawRevisionFacts[{choice_index}] must be object")
        item = str(item_by_choice.get(str(choice_index)) or "")
        current = {
            "lawId": law_id,
            "lawTitle": law_title,
            "article": article,
            "referenceDate": reference_date,
            "verificationStatus": "verified",
            "articleTextHash": correction.get("articleTextHash"),
            "sourceUrl": source_url,
        }
        if paragraph:
            current["paragraph"] = paragraph
        if item:
            current["item"] = item
        fact["auditStatus"] = "same_as_current"
        fact["reviewState"] = "refresh_reviewed"
        fact["auditedAt"] = reviewed_at
        fact["auditMethodVersion"] = "gas-shunin-law-explanation-refresh/v1"
        fact["lawCorpusSnapshotId"] = f"egov-current-{reference_date}"
        fact["reconciliationStatus"] = "direct_primary_basis_reverified"
        fact["current"] = current
        fact["differenceFacts"] = [
            "現行条文と照合し、選択肢の正誤に影響する差異は確認されなかった。"
        ]
        fact["answerImpactFacts"] = [
            "correctChoiceTextは維持し、解説と直接根拠の紐付けを更新した。"
        ]
        fact["notes"] = [
            "e-Gov法令APIの直接根拠条文と選択肢の文言を照合した。"
        ]
        ref = {
            "refId": f"choice-{choice_index}-ref-1",
            "lawTimeScope": "current",
            "relation": "direct_basis",
            "primaryBasis": True,
            "lawId": law_id,
            "lawTitle": law_title,
            "article": article,
            "articleTextHash": correction.get("articleTextHash"),
        }
        if paragraph:
            ref["paragraph"] = paragraph
        if item:
            ref["item"] = item
        fact["evidenceSummary"] = {
            "verdict": "same_as_current",
            "explanationText": strip_verdict(explanation),
            "differenceSummary": "現行条文上、解説の結論に影響する差異なし。",
            "promptContext": (
                f"{law_title}第{article}条"
                + (f"第{paragraph}項" if paragraph else "")
                + (f"第{item}号" if item else "")
                + f"を選択肢{choice_index}の直接根拠として確認。"
            ),
            "refs": [ref],
        }


def apply_decision(decision_path: Path, refresh_dir: Path) -> dict[str, Any]:
    decision = load_json(decision_path)
    audit_key = str(decision.get("auditKey") or "")
    ledger_row = load_ledger_row(refresh_dir / "review_ledger.jsonl", audit_key)
    if decision.get("auditInputHash") != ledger_row.get("auditInputHash"):
        raise ValueError(f"stale decision input hash: {audit_key}")
    correct_choice_review = decision.get("reviewDecision", {}).get("correctChoiceTextReview")
    if not isinstance(correct_choice_review, dict) or correct_choice_review.get("status") != "confirmed":
        raise ValueError(f"correctChoiceText is not confirmed: {audit_key}")
    if decision.get("status") not in {"reviewed_needs_update", "patch_applied"}:
        raise ValueError(f"decision is not applicable: status={decision.get('status')}")

    patch_path = ROOT_DIR / str(ledger_row["explanationPatchFile"])
    payload = load_json(patch_path)
    if not isinstance(payload, list):
        raise ValueError(f"patch root must be list: {patch_path}")
    entry = find_patch_entry(payload, ledger_row)
    review_decision = decision["reviewDecision"]
    explanations = review_decision.get("proposedExplanationText")
    if not isinstance(explanations, list) or len(explanations) != ledger_row["choiceCount"]:
        raise ValueError("proposedExplanationText length mismatch")

    entry["explanationText"] = explanations
    entry["suggestedExplanationText"] = list(explanations)
    entry["lawGroundedExplanationText"] = list(explanations)
    law_references = entry.get("lawReferences")
    if not isinstance(law_references, list) or len(law_references) != len(explanations):
        raise ValueError("lawReferences length mismatch")
    for explanation, references in zip(explanations, law_references, strict=True):
        if not isinstance(references, list) or not references:
            raise ValueError("lawReferences choice entry must be non-empty list")
        for reference in references:
            if not isinstance(reference, dict):
                raise ValueError("lawReference must be object")
            reference["reason"] = explanation
            reference["referenceDate"] = str(decision["reviewedAt"])[:10]

    if "proposedSuggestedQuestions" in review_decision:
        entry["suggestedQuestions"] = review_decision["proposedSuggestedQuestions"]
    if "proposedSuggestedQuestionDetails" in review_decision:
        entry["suggestedQuestionDetails"] = review_decision["proposedSuggestedQuestionDetails"]
    correction = review_decision.get("lawRevisionFactsCorrection")
    if isinstance(correction, dict):
        update_law_revision_facts(
            entry,
            explanations=explanations,
            correction=correction,
            reviewed_at=str(decision["reviewedAt"]),
        )

    write_json(patch_path, payload)
    decision["status"] = "patch_applied"
    decision["appliedAt"] = utc_now()
    decision["application"] = {
        "patchFile": str(patch_path.relative_to(ROOT_DIR)),
        "choiceCount": len(explanations),
        "correctChoiceTextChanged": False,
    }
    write_json(decision_path, decision)
    return decision["application"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply one reviewed gas-shunin law explanation decision.")
    parser.add_argument("decision", type=Path)
    parser.add_argument("--refresh-dir", type=Path, default=DEFAULT_REFRESH_DIR)
    args = parser.parse_args(argv)
    application = apply_decision(args.decision.resolve(), args.refresh_dir.resolve())
    print(json.dumps(application, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
