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


def correction_basis_by_choice(
    correction: dict[str, Any], choice_count: int
) -> list[dict[str, Any]]:
    explicit = correction.get("basisByChoiceIndex")
    if isinstance(explicit, dict):
        bases = []
        for choice_index in range(choice_count):
            basis = explicit.get(str(choice_index))
            if not isinstance(basis, dict):
                raise ValueError(f"missing correction basis for choice {choice_index}")
            bases.append(basis)
        return bases

    item_by_choice = correction.get("itemsByChoiceIndex") or {}
    bases = []
    for choice_index in range(choice_count):
        basis = {
            key: correction[key]
            for key in (
                "lawId",
                "lawTitle",
                "article",
                "paragraph",
                "articleTextHash",
                "rawXmlHash",
            )
            if key in correction
        }
        item = item_by_choice.get(str(choice_index))
        if item not in (None, ""):
            basis["item"] = item
        bases.append(basis)
    return bases


def validate_basis(basis: dict[str, Any], choice_index: int) -> None:
    external_primary = basis.get("sourceType") == "external_primary"
    required = ("lawTitle", "article", "articleTextHash")
    if external_primary:
        required += ("sourceUrl",)
    else:
        required += ("lawId",)
    missing = [
        key
        for key in required
        if not str(basis.get(key) or "").strip()
    ]
    if missing:
        raise ValueError(
            f"correction basis for choice {choice_index} is missing: {', '.join(missing)}"
        )


def basis_references(basis: dict[str, Any]) -> list[dict[str, Any]]:
    locations = basis.get("references")
    if not isinstance(locations, list) or not locations:
        return [basis]
    common = {key: value for key, value in basis.items() if key != "references"}
    merged = []
    for location in locations:
        if not isinstance(location, dict):
            raise ValueError("correction basis reference must be object")
        merged.append({**common, **location})
    return merged


def basis_label(basis: dict[str, Any]) -> str:
    article = str(basis["article"])
    if article.startswith(("別表", "附則", "様式")):
        article_label = article
    elif "の" in article:
        base, suffix = article.split("の", 1)
        article_label = f"第{base}条の{suffix}"
    else:
        article_label = f"第{article}条"
    item = str(basis.get("item") or "")
    item_label = f"の{item}" if item.startswith("表") else (f"第{item}号" if item else "")
    return (
        f"{basis['lawTitle']}{article_label}"
        + (f"第{basis['paragraph']}項" if basis.get("paragraph") else "")
        + item_label
        + (f"{basis['subitem']}" if basis.get("subitem") else "")
    )


def basis_api_url(basis: dict[str, Any]) -> str:
    explicit = basis.get("apiUrl")
    if explicit not in (None, ""):
        return str(explicit)
    law_id = str(basis["lawId"])
    article = str(basis["article"])
    return (
        "https://laws.e-gov.go.jp/api/1/articles;"
        f"lawId={law_id};article={article}"
    )


def basis_source_url(basis: dict[str, Any]) -> str:
    explicit = basis.get("sourceUrl")
    if explicit not in (None, ""):
        return str(explicit)
    return f"https://laws.e-gov.go.jp/law/{basis['lawId']}"


def update_law_references(
    entry: dict[str, Any],
    *,
    explanations: list[str],
    correction: dict[str, Any],
    reviewed_at: str,
) -> None:
    law_references = entry.get("lawReferences")
    if law_references is None:
        law_references = [[] for _ in explanations]
        entry["lawReferences"] = law_references
    if not isinstance(law_references, list) or len(law_references) != len(explanations):
        raise ValueError("lawReferences length mismatch")
    bases = correction_basis_by_choice(correction, len(explanations))
    reference_date = reviewed_at[:10]

    for choice_index, (explanation, references, basis) in enumerate(
        zip(explanations, law_references, bases, strict=True)
    ):
        validate_basis(basis, choice_index)
        if not isinstance(references, list):
            raise ValueError("lawReferences choice entry must be list")
        if references:
            template = references[0]
            if not isinstance(template, dict):
                raise ValueError("lawReference must be object")
        else:
            template = {
                "role": "current_basis",
                "scope": "choice",
                "choiceIndex": choice_index,
                "comparisonStatus": "same_as_current",
            }
        updated_references = []
        for reference_basis in basis_references(basis):
            validate_basis(reference_basis, choice_index)
            reference = dict(template)
            external_primary = reference_basis.get("sourceType") == "external_primary"
            law_id = str(reference_basis.get("lawId") or "")
            article = str(reference_basis["article"])
            reference.update(
                {
                    "lawTitle": str(reference_basis["lawTitle"]),
                    "lawAlias": str(
                        reference_basis.get("lawAlias") or reference_basis["lawTitle"]
                    ),
                    "article": article,
                    "articleTextHash": str(reference_basis["articleTextHash"]),
                    "reason": explanation,
                    "referenceDate": reference_date,
                    "verificationStatus": "verified",
                }
            )
            if external_primary:
                reference.pop("lawId", None)
                reference.pop("apiUrl", None)
                reference.update(
                    {
                        "sourceUrl": str(reference_basis["sourceUrl"]),
                        "source": str(reference_basis.get("source") or "official_primary"),
                        "appLinkMode": "source_url",
                        "externalPrimarySource": True,
                    }
                )
            else:
                reference.update(
                    {
                        "lawId": law_id,
                        "apiUrl": basis_api_url(reference_basis),
                        "sourceUrl": basis_source_url(reference_basis),
                        "source": "egov_law",
                        "appLinkMode": "egov_api",
                    }
                )
            for key in ("paragraph", "item", "subitem", "rawXmlHash"):
                value = reference_basis.get(key)
                if value not in (None, ""):
                    reference[key] = str(value)
                else:
                    reference.pop(key, None)
            updated_references.append(reference)
        law_references[choice_index] = updated_references


def update_law_revision_facts(
    entry: dict[str, Any],
    *,
    explanations: list[str],
    correction: dict[str, Any],
    reviewed_at: str,
) -> None:
    facts = entry.get("lawRevisionFacts")
    if facts is None:
        facts = [{} for _ in explanations]
        entry["lawRevisionFacts"] = facts
    if not isinstance(facts, list) or len(facts) != len(explanations):
        raise ValueError("lawRevisionFacts length mismatch")
    bases = correction_basis_by_choice(correction, len(explanations))
    reference_date = reviewed_at[:10]

    for choice_index, (fact, explanation, basis) in enumerate(
        zip(facts, explanations, bases, strict=True)
    ):
        if not isinstance(fact, dict):
            raise ValueError(f"lawRevisionFacts[{choice_index}] must be object")
        validate_basis(basis, choice_index)
        external_primary = basis.get("sourceType") == "external_primary"
        law_id = str(basis.get("lawId") or "")
        law_title = str(basis["lawTitle"])
        article = str(basis["article"])
        paragraph = str(basis.get("paragraph") or "")
        item = str(basis.get("item") or "")
        subitem = str(basis.get("subitem") or "")
        source_url = (
            str(basis["sourceUrl"])
            if external_primary
            else basis_api_url(basis)
        )
        current = {
            "lawTitle": law_title,
            "article": article,
            "referenceDate": reference_date,
            "verificationStatus": "verified",
            "articleTextHash": str(basis["articleTextHash"]),
            "sourceUrl": source_url,
        }
        if law_id:
            current["lawId"] = law_id
        if external_primary:
            current["sourceType"] = "external_primary"
            current["source"] = str(basis.get("source") or "official_primary")
        if paragraph:
            current["paragraph"] = paragraph
        if item:
            current["item"] = item
        if subitem:
            current["subitem"] = subitem
        fact["auditStatus"] = "same_as_current"
        fact["reviewState"] = "refresh_reviewed"
        fact["auditedAt"] = reviewed_at
        fact["auditMethodVersion"] = "gas-shunin-law-explanation-refresh/v1"
        fact["lawCorpusSnapshotId"] = (
            f"official-primary-{reference_date}"
            if external_primary
            else f"egov-current-{reference_date}"
        )
        fact["reconciliationStatus"] = "direct_primary_basis_reverified"
        fact["current"] = current
        fact["differenceFacts"] = [
            "現行条文と照合し、選択肢の正誤に影響する差異は確認されなかった。"
        ]
        fact["answerImpactFacts"] = [
            "correctChoiceTextは維持し、解説と直接根拠の紐付けを更新した。"
        ]
        fact["notes"] = [
            (
                "所管省庁が公開する一次資料の該当箇所と選択肢の文言を照合した。"
                if external_primary
                else "e-Gov法令APIの直接根拠条文と選択肢の文言を照合した。"
            )
        ]
        refs = []
        reference_bases = basis_references(basis)
        for ref_index, reference_basis in enumerate(reference_bases, start=1):
            validate_basis(reference_basis, choice_index)
            ref = {
                "refId": f"choice-{choice_index}-ref-{ref_index}",
                "lawTimeScope": "current",
                "relation": "direct_basis",
                "primaryBasis": True,
                "lawTitle": str(reference_basis["lawTitle"]),
                "article": str(reference_basis["article"]),
                "articleTextHash": str(reference_basis["articleTextHash"]),
            }
            reference_law_id = str(reference_basis.get("lawId") or "")
            if reference_law_id:
                ref["lawId"] = reference_law_id
            if reference_basis.get("sourceType") == "external_primary":
                ref["sourceUrl"] = str(reference_basis["sourceUrl"])
            for key in ("paragraph", "item", "subitem"):
                value = reference_basis.get(key)
                if value not in (None, ""):
                    ref[key] = str(value)
            refs.append(ref)
        fact["evidenceSummary"] = {
            "verdict": "same_as_current",
            "explanationText": strip_verdict(explanation),
            "differenceSummary": "現行条文上、解説の結論に影響する差異なし。",
            "promptContext": (
                "、".join(basis_label(reference_basis) for reference_basis in reference_bases)
                + f"を選択肢{choice_index}の直接根拠として確認。"
            ),
            "refs": refs,
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
    if "proposedSuggestedQuestions" in review_decision:
        entry["suggestedQuestions"] = review_decision["proposedSuggestedQuestions"]
    if "proposedSuggestedQuestionDetails" in review_decision:
        entry["suggestedQuestionDetails"] = review_decision["proposedSuggestedQuestionDetails"]
    correction = review_decision.get("lawRevisionFactsCorrection")
    if isinstance(correction, dict):
        update_law_references(
            entry,
            explanations=explanations,
            correction=correction,
            reviewed_at=str(decision["reviewedAt"]),
        )
        update_law_revision_facts(
            entry,
            explanations=explanations,
            correction=correction,
            reviewed_at=str(decision["reviewedAt"]),
        )
    else:
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
