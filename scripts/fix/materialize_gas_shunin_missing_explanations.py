#!/usr/bin/env python3
"""Materialize guarded Firestore repair artifacts for missing gas-shunin explanations."""

from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from google.cloud.firestore_v1.base_query import FieldFilter

from scripts.upload.firebase_credentials import initialize_firebase_app
from scripts.upload.upload_questions_to_firestore import (
    DOC_COMPARE_KEYS,
    PRODUCTION_CLIENT_OMITTED_FIELDS,
    firestore_live_fingerprint,
    validate_explanation_patch_questions,
    validate_question_patch_questions,
)


SCHEMA_VERSION = "gas-shunin-missing-explanations-v1"
READ_FIELDS = tuple(
    dict.fromkeys(
        (
            *DOC_COMPARE_KEYS,
            "createdAt",
            "createdById",
        )
    )
)
TRUE_FALSE_PREFIX = {"正しい": "正しい", "間違い": "間違い"}
CANONICAL_CORRECTNESS = {
    "正しい": "正しい",
    "正解": "正しい",
    "間違い": "間違い",
    "不正解": "間違い",
    "誤り": "間違い",
}


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"\s+", "", text)
    return text.replace("−", "-").replace("‐", "-").replace("―", "-")


def normalize_choice_identity(value: object) -> str:
    """Normalize choice identity without confusing short choices from other questions."""

    text = normalize_text(value).replace("～", "~").replace("〜", "~")
    numeric = text.replace(",", "")
    try:
        return f"number:{Decimal(numeric).normalize()}"
    except InvalidOperation:
        pass
    return re.sub(r"[。．、，,.・「」『』（）()【】]", "", text)


def split_true_false_question(question_text: object) -> tuple[str, str] | None:
    match = re.search(r"\[quote\](.*)\[/quote\]\s*$", str(question_text or ""), re.DOTALL)
    if not match:
        return None
    return str(question_text or "")[: match.start()], match.group(1)


def text_hash(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def explanation_hash(value: object) -> str:
    return hashlib.sha256(normalize_text(value).encode("utf-8")).hexdigest()


def canonicalize_explanation_prefix(explanation: str, correctness: object) -> str:
    canonical = CANONICAL_CORRECTNESS.get(str(correctness or "").strip())
    text = explanation.strip()
    if canonical == "正しい" and text.startswith("正解"):
        return "正しい" + text[len("正解") :]
    if canonical == "間違い" and text.startswith("不正解"):
        return "間違い" + text[len("不正解") :]
    if canonical and not text.startswith(("正しい", "間違い")):
        return f"{canonical}。{text}"
    return text


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def firestore_client():
    from firebase_admin import firestore

    initialize_firebase_app()
    return firestore.client()


def fetch_question_sets(db, question_set_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    ids = sorted(set(question_set_ids))
    result: dict[str, dict[str, Any]] = {}
    for offset in range(0, len(ids), 30):
        query = db.collection("questions").where(
            filter=FieldFilter("questionSetId", "in", ids[offset : offset + 30])
        )
        for snapshot in query.select(READ_FIELDS).stream():
            data = snapshot.to_dict() or {}
            data["_id"] = snapshot.id
            result[snapshot.id] = data
    return result


def fetch_question_ids(db, question_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    from firebase_admin import firestore

    ids = list(dict.fromkeys(str(item) for item in question_ids))
    refs = [db.collection("questions").document(question_id) for question_id in ids]
    snapshots = db.get_all(refs, field_paths=READ_FIELDS)
    result: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        if snapshot.exists:
            result[snapshot.id] = snapshot.to_dict() or {}
    return result


def build_local_exact_index(repo_root: Path, qualification: str, year: int):
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    patch_dir = (
        repo_root
        / "output"
        / qualification
        / "questions_json"
        / str(year)
        / "21_explanationText_added"
    )
    for patch_path in sorted(patch_dir.glob("*.json")):
        raw = read_json(patch_path)
        records = raw if isinstance(raw, list) else raw.get("question_bodies", [])
        for record_index, record in enumerate(records):
            choices = record.get("choiceTextList") or []
            explanations = record.get("explanationText")
            if not isinstance(explanations, list):
                explanations = [explanations] * len(choices)
            body = str(record.get("questionBodyText") or "").replace("\n", "")
            for choice_index, choice in enumerate(choices):
                explanation = explanations[choice_index] if choice_index < len(explanations) else None
                if not isinstance(explanation, str) or not explanation.strip():
                    continue
                full_text = f"{body}[quote]{str(choice).replace(chr(10), '')}[/quote]"
                index[normalize_text(full_text)].append(
                    {
                        "kind": "local_21_patch",
                        "patchFile": str(patch_path.relative_to(repo_root)),
                        "sourceQuestionKey": record.get("sourceQuestionKey"),
                        "recordIndex": record_index,
                        "choiceIndex": choice_index + 1,
                        "explanationText": explanation,
                        "explanationHash": explanation_hash(explanation),
                    }
                )
    return index


def build_manual_review_choice_index(repo_root: Path, qualification: str, year: int):
    review_path = (
        repo_root
        / "output"
        / qualification
        / "review"
        / "01_04_manual_review"
        / f"{qualification}_01_04_manual_review.jsonl"
    )
    exact: dict[str, list[dict[str, Any]]] = defaultdict(list)
    candidates: list[tuple[str, dict[str, Any]]] = []
    if not review_path.exists():
        return exact, candidates
    for line_number, line in enumerate(review_path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("reviewDecision") != "ok":
            continue
        source_year = int(record.get("examYear") or 0)
        choices = record.get("choiceTextList")
        explanations = record.get("explanationText")
        correctness = record.get("correctChoiceText")
        if not (
            isinstance(choices, list)
            and isinstance(explanations, list)
            and isinstance(correctness, list)
            and len(choices) == len(explanations) == len(correctness)
        ):
            continue
        for choice_index, (choice, explanation, answer) in enumerate(
            zip(choices, explanations, correctness), 1
        ):
            if not isinstance(explanation, str) or not explanation.strip():
                continue
            canonical_answer = CANONICAL_CORRECTNESS.get(str(answer or "").strip())
            canonical_explanation = canonicalize_explanation_prefix(explanation, answer)
            source = {
                "kind": "manual_01_04_review",
                "reviewFile": str(review_path.relative_to(repo_root)),
                "reviewLine": line_number,
                "sourceExamYear": source_year,
                "sameExamYear": source_year == year,
                "sourceQuestionKey": record.get("sourceQuestionKey"),
                "choiceIndex": choice_index,
                "sourceCorrectChoiceText": canonical_answer,
                "explanationText": canonical_explanation,
                "explanationHash": explanation_hash(canonical_explanation),
            }
            normalized_choice = normalize_text(choice)
            exact[normalized_choice].append(source)
            if source_year == year:
                candidates.append((normalized_choice, source))
    return exact, candidates


def select_manual_review_source(
    question_text: object,
    exact: dict[str, list[dict[str, Any]]],
    candidates: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any] | None:
    match = re.search(r"\[quote\](.*)\[/quote\]\s*$", str(question_text or ""), re.DOTALL)
    if not match:
        return None
    normalized_choice = normalize_text(match.group(1))
    exact_candidates = exact.get(normalized_choice, [])
    exact_answers = {item.get("sourceCorrectChoiceText") for item in exact_candidates}
    if len(exact_answers) == 1 and exact_candidates:
        preferred = next(
            (item for item in exact_candidates if item.get("sameExamYear")),
            exact_candidates[-1],
        )
        return copy.deepcopy(preferred)
    if exact_candidates or len(normalized_choice) < 20:
        return None
    scored = sorted(
        [
            (
            difflib.SequenceMatcher(None, normalized_choice, candidate_choice).ratio(),
            candidate_choice,
            source,
            )
            for candidate_choice, source in candidates
        ],
        key=lambda item: item[0],
    )
    if not scored:
        return None
    best_ratio, _, best_source = scored[-1]
    second_ratio = scored[-2][0] if len(scored) > 1 else 0.0
    if best_ratio < 0.94 or best_ratio - second_ratio < 0.10:
        return None
    selected = copy.deepcopy(best_source)
    selected["choiceSimilarity"] = round(best_ratio, 6)
    return selected


def _active_explained(document: dict[str, Any]) -> bool:
    return (
        document.get("isDeleted") is False
        and document.get("isChoiceOnly") is False
        and isinstance(document.get("explanationText"), str)
        and bool(document["explanationText"].strip())
    )


def _candidate_source(document: dict[str, Any]) -> dict[str, Any]:
    explanation = document["explanationText"]
    return {
        "kind": "firestore_same_question",
        "questionId": document["_id"],
        "explanationHash": explanation_hash(explanation),
    }


def _unique_or_review(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    by_id = {candidate["_id"]: candidate for candidate in candidates}
    if len(by_id) == 1:
        return _candidate_source(next(iter(by_id.values())))
    return None


def bootstrap_decisions(
    inventory_path: Path,
    repo_root: Path,
    grade: str,
    year: int,
    output_path: Path,
) -> dict[str, Any]:
    inventory = read_json(inventory_path)
    targets = [
        record
        for record in inventory["records"]
        if record.get("grade") == grade and int(record.get("examYear")) == year
    ]
    if not targets:
        raise ValueError(f"no inventory targets for {grade} {year}")

    db = firestore_client()
    documents = fetch_question_sets(db, (record["questionSetId"] for record in targets))
    present = [document for document in documents.values() if _active_explained(document)]
    local_exact = build_local_exact_index(repo_root, targets[0]["qualification"], year)
    manual_exact, manual_candidates = build_manual_review_choice_index(
        repo_root, targets[0]["qualification"], year
    )

    by_qset_text: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    by_year_text: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    by_original: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for document in present:
        answer = normalize_text(document.get("correctChoiceText"))
        by_qset_text[
            (
                document.get("questionSetId"),
                document.get("examYear"),
                normalize_text(document.get("questionText")),
                answer,
            )
        ].append(document)
        by_year_text[
            (document.get("examYear"), normalize_text(document.get("questionText")), answer)
        ].append(document)
        original_key = (
            document.get("examYear"),
            normalize_text(document.get("originalQuestionBodyText")),
            normalize_text(document.get("originalQuestionChoiceText")),
            answer,
        )
        if all(value not in (None, "") for value in original_key):
            by_original[original_key].append(document)

    decisions = []
    for target in targets:
        question_id = target["firestoreQuestionId"]
        live = documents.get(question_id)
        if live is None:
            raise ValueError(f"target not found in Firestore: {question_id}")
        answer = normalize_text(live.get("correctChoiceText"))
        qset_key = (
            live.get("questionSetId"),
            live.get("examYear"),
            normalize_text(live.get("questionText")),
            answer,
        )
        year_key = (live.get("examYear"), normalize_text(live.get("questionText")), answer)
        original_key = (
            live.get("examYear"),
            normalize_text(live.get("originalQuestionBodyText")),
            normalize_text(live.get("originalQuestionChoiceText")),
            answer,
        )

        source = _unique_or_review(by_qset_text.get(qset_key, []))
        if source is None and not by_qset_text.get(qset_key):
            source = _unique_or_review(by_year_text.get(year_key, []))
        if source is None and not by_qset_text.get(qset_key) and not by_year_text.get(year_key):
            original_candidates = (
                by_original.get(original_key, [])
                if all(value not in (None, "") for value in original_key)
                else []
            )
            source = _unique_or_review(original_candidates)
        if source is None:
            source = select_manual_review_source(
                live.get("questionText"), manual_exact, manual_candidates
            )
        if source is None:
            local_candidates = local_exact.get(normalize_text(live.get("questionText")), [])
            if len(local_candidates) == 1:
                source = local_candidates[0]
        if source is None and target.get("localExplanationPresent"):
            explanation = str(target.get("localExplanationText") or "")
            if explanation.strip():
                source = {
                    "kind": "local_21_patch",
                    "patchFile": target.get("explanationPatchFile"),
                    "sourceQuestionKey": target.get("sourceQuestionKey"),
                    "choiceIndex": (target.get("planChoiceIndex") or 0) + 1,
                    "explanationText": explanation,
                    "explanationHash": explanation_hash(explanation),
                }

        rejected_source = None
        rejected_reason = None
        if source is not None:
            if source.get("kind") == "firestore_same_question":
                source_document = documents.get(str(source.get("questionId") or "")) or {}
                candidate_explanation = source_document.get("explanationText")
            else:
                candidate_explanation = source.get("explanationText")
            try:
                validate_answer_alignment(
                    {
                        "questionId": question_id,
                        "questionType": live.get("questionType"),
                        "correctChoiceText": live.get("correctChoiceText"),
                    },
                    str(candidate_explanation or ""),
                )
            except ValueError as exc:
                rejected_source = source
                rejected_reason = str(exc)
                source = None

        decision = {
            "questionId": question_id,
            "grade": grade,
            "examYear": year,
            "questionSetId": live.get("questionSetId"),
            "questionText": live.get("questionText"),
            "questionTextHash": text_hash(live.get("questionText")),
            "questionType": live.get("questionType"),
            "correctChoiceText": live.get("correctChoiceText"),
            "status": "ready" if source else "needs_review",
            "source": source,
        }
        if source is None:
            decision["reviewContext"] = {
                "sameQuestionSetExamples": target.get("similarExplanationCandidates", []),
                "liveQsetTextCandidates": [
                    {
                        "questionId": item["_id"],
                        "explanationText": item.get("explanationText"),
                    }
                    for item in by_qset_text.get(qset_key, [])
                ],
            }
            if rejected_source is not None:
                decision["reviewContext"]["rejectedSource"] = rejected_source
                decision["reviewContext"]["rejectedReason"] = rejected_reason
        decisions.append(decision)

    output = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "selection": {"grade": grade, "examYear": year},
        "inventory": str(inventory_path),
        "counts": {
            "total": len(decisions),
            "ready": sum(item["status"] == "ready" for item in decisions),
            "needsReview": sum(item["status"] != "ready" for item in decisions),
        },
        "decisions": decisions,
    }
    write_json(output_path, output)
    return output


def apply_overrides(decisions_path: Path, overrides_path: Path, output_path: Path) -> dict[str, Any]:
    ledger = read_json(decisions_path)
    overrides = read_json(overrides_path)
    by_id = {item["questionId"]: item for item in ledger["decisions"]}
    seen: set[str] = set()
    for override in overrides["overrides"]:
        question_id = override["questionId"]
        if question_id not in by_id:
            raise ValueError(f"override target not in ledger: {question_id}")
        if question_id in seen:
            raise ValueError(f"duplicate override: {question_id}")
        seen.add(question_id)
        requested_status = str(override.get("status") or "ready")
        if requested_status == "hold":
            by_id[question_id]["source"] = None
            by_id[question_id]["status"] = "hold"
            by_id[question_id]["holdReason"] = str(override.get("reason") or "")
            by_id[question_id]["holdEvidence"] = copy.deepcopy(override.get("evidence") or [])
            if override.get("proposedCorrectChoiceText") is not None:
                by_id[question_id]["proposedCorrectChoiceText"] = override[
                    "proposedCorrectChoiceText"
                ]
            if override.get("proposedExplanationText") is not None:
                by_id[question_id]["proposedExplanationText"] = override[
                    "proposedExplanationText"
                ]
            continue
        if requested_status != "ready":
            raise ValueError(f"unsupported override status: {requested_status}")
        source = copy.deepcopy(override["source"])
        explanation = source.get("explanationText")
        if isinstance(explanation, str) and explanation.strip():
            source["explanationHash"] = explanation_hash(explanation)
        by_id[question_id]["source"] = source
        by_id[question_id]["status"] = "ready"
        by_id[question_id].pop("reviewContext", None)
    ledger["generatedAt"] = datetime.now(timezone.utc).isoformat()
    ledger["counts"] = {
        "total": len(by_id),
        "ready": sum(item["status"] == "ready" for item in by_id.values()),
        "needsReview": sum(item["status"] == "needs_review" for item in by_id.values()),
        "hold": sum(item["status"] == "hold" for item in by_id.values()),
    }
    ledger["overridesSource"] = str(overrides_path)
    write_json(output_path, ledger)
    return ledger


def mark_rejected_answer_conflicts(decisions_path: Path, output_path: Path) -> dict[str, Any]:
    """Turn rejected, answer-opposite verified sources into explicit holds."""
    ledger = read_json(decisions_path)
    marked = 0
    for decision in ledger.get("decisions", []):
        if decision.get("status") != "needs_review":
            continue
        rejected = (decision.get("reviewContext") or {}).get("rejectedSource") or {}
        target_answer = CANONICAL_CORRECTNESS.get(
            str(decision.get("correctChoiceText") or "").strip()
        )
        source_answer = CANONICAL_CORRECTNESS.get(
            str(rejected.get("sourceCorrectChoiceText") or "").strip()
        )
        explanation = rejected.get("explanationText")
        if (
            target_answer not in {"正しい", "間違い"}
            or source_answer not in {"正しい", "間違い"}
            or target_answer == source_answer
            or not isinstance(explanation, str)
            or not explanation.strip()
        ):
            continue
        decision["source"] = None
        decision["status"] = "hold"
        decision["holdReason"] = (
            "FirestoreのcorrectChoiceTextと、同文の手動レビュー済み解説の正誤が反対であるため"
        )
        decision["holdEvidence"] = [copy.deepcopy(rejected)]
        decision["proposedCorrectChoiceText"] = source_answer
        decision["proposedExplanationText"] = canonicalize_explanation_prefix(
            explanation, source_answer
        )
        marked += 1

    decisions = ledger.get("decisions", [])
    ledger["generatedAt"] = datetime.now(timezone.utc).isoformat()
    ledger["counts"] = {
        "total": len(decisions),
        "ready": sum(item.get("status") == "ready" for item in decisions),
        "needsReview": sum(item.get("status") == "needs_review" for item in decisions),
        "hold": sum(item.get("status") == "hold" for item in decisions),
    }
    ledger["answerConflictSource"] = str(decisions_path)
    ledger["answerConflictsMarked"] = marked
    write_json(output_path, ledger)
    return ledger


def resolve_explanation(source: dict[str, Any], live_documents: dict[str, dict[str, Any]]) -> str:
    kind = source.get("kind")
    if kind == "firestore_same_question":
        question_id = str(source.get("questionId") or "")
        document = live_documents.get(question_id)
        if document is None:
            raise ValueError(f"Firestore explanation source missing: {question_id}")
        explanation = document.get("explanationText")
    elif kind in {"local_21_patch", "manual_01_04_review", "authored_from_similar"}:
        explanation = source.get("explanationText")
    else:
        raise ValueError(f"unsupported explanation source kind: {kind}")
    if not isinstance(explanation, str) or not explanation.strip():
        raise ValueError(f"blank explanation source: {source}")
    expected_hash = str(source.get("explanationHash") or "")
    if expected_hash and explanation_hash(explanation) != expected_hash:
        raise ValueError(f"explanation source hash changed: {source}")
    return explanation.strip()


def validate_answer_alignment(decision: dict[str, Any], explanation: str) -> None:
    if decision.get("questionType") != "true_false":
        return
    answer = str(decision.get("correctChoiceText") or "").strip()
    prefix = TRUE_FALSE_PREFIX.get(answer)
    if prefix and not explanation.startswith(prefix):
        raise ValueError(
            f"explanation/answer prefix mismatch: {decision['questionId']} "
            f"answer={answer} explanation={explanation[:30]}"
        )


def validate_answer_correction_decision(decision: dict[str, Any]) -> None:
    """Validate the proposed answer and explanation carried by an explicit hold."""

    question_id = str(decision.get("questionId") or "")
    current = CANONICAL_CORRECTNESS.get(
        str(decision.get("correctChoiceText") or "").strip()
    )
    proposed = CANONICAL_CORRECTNESS.get(
        str(decision.get("proposedCorrectChoiceText") or "").strip()
    )
    explanation = str(decision.get("proposedExplanationText") or "").strip()
    if decision.get("status") != "hold":
        raise ValueError(f"answer correction must originate from hold: {question_id}")
    if current not in {"正しい", "間違い"} or proposed not in {"正しい", "間違い"}:
        raise ValueError(f"answer correction requires binary answers: {question_id}")
    if current == proposed:
        raise ValueError(f"answer correction does not change answer: {question_id}")
    if not explanation.startswith(proposed):
        raise ValueError(f"answer correction explanation prefix mismatch: {question_id}")
    if not decision.get("holdReason") or not decision.get("holdEvidence"):
        raise ValueError(f"answer correction lacks hold evidence: {question_id}")


def materialize(
    decisions_path: Path,
    artifact_path: Path,
    audit_path: Path,
    allow_already_filled: bool = False,
    allow_holds: bool = False,
) -> dict[str, Any]:
    ledger = read_json(decisions_path)
    all_decisions = ledger.get("decisions", [])
    if not all_decisions:
        raise ValueError("decision ledger is empty")
    held = [item for item in all_decisions if item.get("status") == "hold"]
    unresolved = [
        item
        for item in all_decisions
        if item.get("status") not in ({"ready", "hold"} if allow_holds else {"ready"})
    ]
    if unresolved or (held and not allow_holds):
        raise ValueError("all decisions must be ready, or explicit holds must be allowed")
    decisions = [item for item in all_decisions if item.get("status") == "ready"]
    if not decisions:
        raise ValueError("no ready decisions to materialize")
    target_ids = [item["questionId"] for item in decisions]
    if len(target_ids) != len(set(target_ids)):
        raise ValueError("duplicate target questionId in decisions")
    source_ids = [
        item["source"]["questionId"]
        for item in decisions
        if item.get("source", {}).get("kind") == "firestore_same_question"
    ]
    db = firestore_client()
    live_documents = fetch_question_ids(db, [*target_ids, *source_ids])

    questions = []
    audit_records = []
    non_explanation_drift: list[dict[str, Any]] = []
    for decision in decisions:
        question_id = decision["questionId"]
        existing = live_documents.get(question_id)
        if existing is None:
            raise ValueError(f"target document missing: {question_id}")
        if existing.get("isDeleted") is not False or existing.get("isChoiceOnly") is not False:
            raise ValueError(f"target left requested selection: {question_id}")
        if text_hash(existing.get("questionText")) != decision.get("questionTextHash"):
            raise ValueError(f"target questionText changed: {question_id}")
        if existing.get("correctChoiceText") != decision.get("correctChoiceText"):
            raise ValueError(f"target answer changed: {question_id}")
        current_explanation = existing.get("explanationText")
        if (
            isinstance(current_explanation, str)
            and current_explanation.strip()
            and not allow_already_filled
        ):
            raise ValueError(f"target explanation is no longer blank: {question_id}")

        explanation = resolve_explanation(decision["source"], live_documents)
        validate_answer_alignment(decision, explanation)
        payload = {
            key: copy.deepcopy(existing[key])
            for key in DOC_COMPARE_KEYS
            if key in existing and key not in PRODUCTION_CLIENT_OMITTED_FIELDS
        }
        payload["questionId"] = question_id
        payload["explanationText"] = explanation
        for key in DOC_COMPARE_KEYS:
            if key == "explanationText" or key not in payload:
                continue
            if existing.get(key) != payload.get(key):
                non_explanation_drift.append(
                    {
                        "questionId": question_id,
                        "field": key,
                        "existing": existing.get(key),
                        "payload": payload.get(key),
                    }
                )
        questions.append(payload)
        audit_records.append(
            {
                "questionId": question_id,
                "questionTextHash": decision["questionTextHash"],
                "correctChoiceText": decision["correctChoiceText"],
                "source": decision["source"],
                "explanationHash": explanation_hash(explanation),
            }
        )

    if non_explanation_drift:
        raise ValueError(f"non-explanation drift detected: {non_explanation_drift[:5]}")
    validation_questions = copy.deepcopy(questions)
    validate_explanation_patch_questions(validation_questions, str(artifact_path))
    target_live = {question_id: live_documents[question_id] for question_id in target_ids}
    live_hash = firestore_live_fingerprint(target_ids, target_live)
    artifact = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "expectedLiveFingerprint": live_hash,
        "writeFields": ["explanationText"],
        "questions": questions,
    }
    audit = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": artifact["generatedAt"],
        "decisions": str(decisions_path),
        "targetCount": len(questions),
        "uniqueTargetCount": len(set(target_ids)),
        "nonExplanationDriftCount": 0,
        "heldCount": len(held),
        "heldQuestionIds": [item["questionId"] for item in held],
        "expectedLiveFingerprint": live_hash,
        "artifactSha256": hashlib.sha256(
            json.dumps(artifact, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "records": audit_records,
    }
    write_json(artifact_path, artifact)
    write_json(audit_path, audit)
    return audit


def materialize_answer_corrections(
    decisions_path: Path,
    artifact_path: Path,
    audit_path: Path,
) -> dict[str, Any]:
    """Materialize answer+explanation repairs from reviewed answer-conflict holds."""

    ledger = read_json(decisions_path)
    decisions = [
        item for item in ledger.get("decisions", []) if item.get("status") == "hold"
    ]
    if not decisions:
        raise ValueError("no answer-conflict holds to materialize")
    target_ids = [str(item.get("questionId") or "") for item in decisions]
    if not all(target_ids) or len(target_ids) != len(set(target_ids)):
        raise ValueError("answer correction target IDs are blank or duplicated")
    for decision in decisions:
        validate_answer_correction_decision(decision)

    db = firestore_client()
    live_documents = fetch_question_ids(db, target_ids)
    questions: list[dict[str, Any]] = []
    audit_records: list[dict[str, Any]] = []
    for decision in decisions:
        question_id = decision["questionId"]
        existing = live_documents.get(question_id)
        if existing is None:
            raise ValueError(f"answer correction target missing: {question_id}")
        if existing.get("isDeleted") is not False or existing.get("isChoiceOnly") is not False:
            raise ValueError(f"answer correction target left selection: {question_id}")
        if text_hash(existing.get("questionText")) != decision.get("questionTextHash"):
            raise ValueError(f"answer correction questionText changed: {question_id}")
        if existing.get("correctChoiceText") != decision.get("correctChoiceText"):
            raise ValueError(f"answer correction current answer changed: {question_id}")
        if str(existing.get("explanationText") or "").strip():
            raise ValueError(f"answer correction explanation is no longer blank: {question_id}")

        proposed_answer = CANONICAL_CORRECTNESS[
            str(decision["proposedCorrectChoiceText"]).strip()
        ]
        proposed_explanation = str(decision["proposedExplanationText"]).strip()
        payload = {
            key: copy.deepcopy(existing[key])
            for key in DOC_COMPARE_KEYS
            if key in existing and key not in PRODUCTION_CLIENT_OMITTED_FIELDS
        }
        payload["questionId"] = question_id
        payload["correctChoiceText"] = proposed_answer
        payload["explanationText"] = proposed_explanation
        questions.append(payload)
        audit_records.append(
            {
                "questionId": question_id,
                "questionTextHash": decision["questionTextHash"],
                "currentCorrectChoiceText": decision["correctChoiceText"],
                "proposedCorrectChoiceText": proposed_answer,
                "proposedExplanationHash": explanation_hash(proposed_explanation),
                "holdReason": decision["holdReason"],
                "holdEvidence": copy.deepcopy(decision["holdEvidence"]),
            }
        )

    write_fields = ("correctChoiceText", "explanationText")
    validate_question_patch_questions(questions, write_fields, str(artifact_path))
    live_hash = firestore_live_fingerprint(target_ids, live_documents)
    artifact = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "expectedLiveFingerprint": live_hash,
        "writeFields": list(write_fields),
        "questions": questions,
    }
    audit = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": artifact["generatedAt"],
        "decisions": str(decisions_path),
        "targetCount": len(questions),
        "uniqueTargetCount": len(set(target_ids)),
        "nonApprovedFieldDriftCount": 0,
        "writeFields": list(write_fields),
        "expectedLiveFingerprint": live_hash,
        "artifactSha256": hashlib.sha256(
            json.dumps(artifact, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "records": audit_records,
    }
    write_json(artifact_path, artifact)
    write_json(audit_path, audit)
    return audit


def _manual_review_records(repo_root: Path, qualifications: Iterable[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for qualification in sorted(set(qualifications)):
        path = (
            repo_root
            / "output"
            / qualification
            / "review"
            / "01_04_manual_review"
            / f"{qualification}_01_04_manual_review.jsonl"
        )
        if not path.exists():
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            choices = raw.get("choiceTextList")
            answers = raw.get("correctChoiceText")
            explanations = raw.get("explanationText")
            if not (
                raw.get("reviewDecision") == "ok"
                and isinstance(choices, list)
                and isinstance(answers, list)
                and isinstance(explanations, list)
                and len(choices) == len(answers) == len(explanations)
            ):
                continue
            mapped_choices = []
            for choice_index, (choice, answer, explanation) in enumerate(
                zip(choices, answers, explanations), 1
            ):
                canonical_answer = CANONICAL_CORRECTNESS.get(str(answer or "").strip())
                if canonical_answer not in {"正しい", "間違い"}:
                    mapped_choices = []
                    break
                mapped_choices.append(
                    {
                        "choiceText": str(choice),
                        "choiceIdentity": normalize_choice_identity(choice),
                        "correctChoiceText": canonical_answer,
                        "explanationText": canonicalize_explanation_prefix(
                            str(explanation or ""), canonical_answer
                        ),
                        "choiceIndex": choice_index,
                    }
                )
            if not mapped_choices:
                continue
            records.append(
                {
                    "qualification": qualification,
                    "examYear": int(raw.get("examYear") or 0),
                    "questionBodyText": str(raw.get("questionBodyText") or ""),
                    "choices": mapped_choices,
                    "source": {
                        "kind": "manual_01_04_review_group",
                        "reviewFile": str(path.relative_to(repo_root)),
                        "reviewLine": line_number,
                        "reviewId": raw.get("reviewId"),
                    },
                }
            )
    return records


def _choice_group_mapping(
    current_choices: list[str], manual_choices: list[dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], float] | None:
    if len(set(current_choices)) != len(current_choices):
        return None
    unused = set(range(len(manual_choices)))
    mapping: dict[str, dict[str, Any]] = {}
    scores: list[float] = []
    for current in current_choices:
        ranked = sorted(
            (
                difflib.SequenceMatcher(
                    None, current, manual_choices[index]["choiceIdentity"]
                ).ratio(),
                index,
            )
            for index in unused
        )
        if not ranked:
            return None
        score, index = ranked[-1]
        exact = current == manual_choices[index]["choiceIdentity"]
        if (len(current) <= 8 and not exact) or (not exact and score < 0.94):
            return None
        unused.remove(index)
        mapping[current] = manual_choices[index]
        scores.append(score)
    if unused or not scores or sum(scores) / len(scores) < 0.97:
        return None
    return mapping, sum(scores) / len(scores)


def _match_manual_groups(
    current_groups: dict[tuple[str, int, str, str], list[dict[str, Any]]],
    manual_records: list[dict[str, Any]],
) -> dict[tuple[str, int, str, str], tuple[dict[str, Any], dict[str, dict[str, Any]]]]:
    matches = {}
    for key, group in current_groups.items():
        qualification, year, _, body = key
        current_choices = [item["choiceIdentity"] for item in group]
        candidates = []
        for manual in manual_records:
            if manual["qualification"] != qualification or manual["examYear"] != year:
                continue
            mapped = _choice_group_mapping(current_choices, manual["choices"])
            if mapped is None:
                continue
            mapping, choice_score = mapped
            body_score = difflib.SequenceMatcher(
                None, normalize_choice_identity(body), normalize_choice_identity(manual["questionBodyText"])
            ).ratio()
            candidates.append((choice_score + body_score * 0.05, manual, mapping))
        candidates.sort(key=lambda item: item[0])
        if not candidates:
            continue
        if len(candidates) > 1 and candidates[-1][0] - candidates[-2][0] < 0.02:
            continue
        _, manual, mapping = candidates[-1]
        matches[key] = (manual, mapping)
    return matches


def _previous_decisions(repo_root: Path) -> dict[str, dict[str, Any]]:
    base = (
        repo_root
        / "output"
        / "gas-shunin-otsu"
        / "questions_json"
        / "firestore_repairs"
        / "20260829"
    )
    result = {}
    for path in sorted(base.glob("decisions_final_*.json")):
        for decision in read_json(path).get("decisions", []):
            result[str(decision.get("questionId") or "")] = decision
    return result


def validate_recovery_records(records: list[dict[str, Any]]) -> None:
    allowed_changed_fields = {
        "originalQuestionBodyText",
        "originalQuestionChoiceText",
        "questionText",
        "correctChoiceText",
        "explanationText",
    }
    for record in records:
        question_id = str(record.get("questionId") or "")
        if record.get("status") != "ready" or not isinstance(record.get("source"), dict):
            raise ValueError(f"recovery record is not source-backed ready: {question_id}")
        if not set(record.get("changedFields") or []).issubset(allowed_changed_fields):
            raise ValueError(f"recovery changedFields exceed contract: {question_id}")
        if record.get("questionType") != "true_false":
            continue
        proposed = record.get("proposed") or {}
        answer = CANONICAL_CORRECTNESS.get(
            str(proposed.get("correctChoiceText") or "").strip()
        )
        explanation = str(proposed.get("explanationText") or "").strip()
        if answer not in {"正しい", "間違い"} or not explanation.startswith(answer):
            raise ValueError(f"recovery answer/explanation mismatch: {question_id}")
        parts = split_true_false_question(
            proposed.get("questionText", record.get("questionText"))
        )
        if parts is None:
            continue
        _, choice = parts
        if normalize_text(proposed.get("originalQuestionChoiceText")) != normalize_text(choice):
            raise ValueError(f"recovery quote/original choice mismatch: {question_id}")


def build_recovery_ledger(
    inventory_path: Path,
    repo_root: Path,
    output_path: Path,
    overrides_path: Path | None = None,
) -> dict[str, Any]:
    """Build one semantic decision per fixed target without writing Firestore."""

    inventory = read_json(inventory_path)
    targets = inventory.get("records", [])
    if len(targets) != 782:
        raise ValueError(f"recovery inventory must contain 782 records: {len(targets)}")
    target_ids = [str(item.get("firestoreQuestionId") or "") for item in targets]
    if not all(target_ids) or len(target_ids) != len(set(target_ids)):
        raise ValueError("recovery inventory IDs are blank or duplicated")

    db = firestore_client()
    qset_ids = {str(item.get("questionSetId") or "") for item in targets}
    documents = fetch_question_sets(db, qset_ids)
    if not set(target_ids).issubset(documents):
        raise ValueError("recovery inventory target is missing from Firestore")
    qset_qualification = {
        str(item["questionSetId"]): str(item["qualification"]) for item in targets
    }

    current_groups: dict[tuple[str, int, str, str], list[dict[str, Any]]] = defaultdict(list)
    document_group: dict[str, tuple[str, int, str, str]] = {}
    by_question_text: dict[tuple[int, str], list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for question_id, document in documents.items():
        year = int(document.get("examYear") or 0)
        by_question_text[(year, normalize_text(document.get("questionText")))].append(
            (question_id, document)
        )
        parts = split_true_false_question(document.get("questionText"))
        qualification = qset_qualification.get(str(document.get("questionSetId") or ""))
        if parts is None or qualification is None:
            continue
        body, choice = parts
        key = (qualification, year, str(document.get("questionSetId") or ""), normalize_text(body))
        current_groups[key].append(
            {
                "questionId": question_id,
                "choiceIdentity": normalize_choice_identity(choice),
                "choiceText": choice,
            }
        )
        document_group[question_id] = key

    manual_records = _manual_review_records(
        repo_root, (item["qualification"] for item in targets)
    )
    manual_matches = _match_manual_groups(current_groups, manual_records)
    manual_exact_choices: dict[tuple[str, int, str], list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for manual in manual_records:
        for manual_choice in manual["choices"]:
            manual_exact_choices[
                (
                    manual["qualification"],
                    manual["examYear"],
                    manual_choice["choiceIdentity"],
                )
            ].append((manual, manual_choice))
    previous = _previous_decisions(repo_root)
    local_indexes = {
        (qualification, year): build_local_exact_index(repo_root, qualification, year)
        for qualification in {item["qualification"] for item in targets}
        for year in {int(item["examYear"]) for item in targets}
    }

    ledger_records = []
    unresolved = []
    for target in targets:
        question_id = target["firestoreQuestionId"]
        live = documents[question_id]
        current = {
            "originalQuestionBodyText": live.get("originalQuestionBodyText"),
            "originalQuestionChoiceText": live.get("originalQuestionChoiceText"),
            "questionText": live.get("questionText"),
            "correctChoiceText": live.get("correctChoiceText"),
            "explanationText": live.get("explanationText"),
        }
        proposed = copy.deepcopy(current)
        source: dict[str, Any] | None = None
        status = "ready"
        reason = "existing_flash_card_fields_reviewed"

        if live.get("questionType") != "true_false":
            source = {"kind": "current_live_flash_card", "questionId": question_id}

        if live.get("questionType") == "true_false":
            parts = split_true_false_question(live.get("questionText"))
            if parts is None:
                local_explanation = str(target.get("localExplanationText") or "").strip()
                current_explanation = str(current.get("explanationText") or "").strip()
                current_answer = CANONICAL_CORRECTNESS.get(
                    str(current.get("correctChoiceText") or "").strip()
                )
                if (
                    str(current.get("originalQuestionBodyText") or "").strip()
                    and not str(current.get("originalQuestionChoiceText") or "").strip()
                    and target.get("localExplanationPresent") is True
                    and local_explanation == current_explanation
                    and current_answer in {"正しい", "間違い"}
                    and current_explanation.startswith(current_answer)
                ):
                    source = {
                        "kind": "local_21_patch_body_statement",
                        "patchFile": target.get("explanationPatchFile"),
                        "sourceQuestionKey": target.get("sourceQuestionKey"),
                    }
                    reason = "true_false_statement_is_original_body_without_quote"
                else:
                    status = "hold"
                    reason = "true_false_without_quote"
            else:
                body, choice = parts
                identity_aligned = normalize_text(
                    current["originalQuestionChoiceText"]
                ) == normalize_text(choice)
                if not identity_aligned:
                    proposed["originalQuestionBodyText"] = body
                    proposed["originalQuestionChoiceText"] = choice
                key = document_group.get(question_id)
                manual_match = manual_matches.get(key) if key else None
                manual_choice = None
                if manual_match is not None:
                    manual, mapping = manual_match
                    manual_choice = mapping.get(normalize_choice_identity(choice))
                    if (
                        manual_choice is not None
                        and normalize_choice_identity(choice)
                        == manual_choice["choiceIdentity"]
                    ):
                        proposed["correctChoiceText"] = manual_choice["correctChoiceText"]
                        proposed["explanationText"] = manual_choice["explanationText"]
                        source = {
                            **manual["source"],
                            "choiceIndex": manual_choice["choiceIndex"],
                            "choiceText": manual_choice["choiceText"],
                        }
                        reason = "manual_reviewed_whole_choice_group"

                if source is None:
                    candidates = []
                    for source_id, candidate in by_question_text[
                        (int(live.get("examYear") or 0), normalize_text(live.get("questionText")))
                    ]:
                        if source_id == question_id:
                            continue
                        if normalize_text(candidate.get("originalQuestionChoiceText")) != normalize_text(choice):
                            continue
                        explanation = str(candidate.get("explanationText") or "").strip()
                        answer = CANONICAL_CORRECTNESS.get(
                            str(candidate.get("correctChoiceText") or "").strip()
                        )
                        if answer in {"正しい", "間違い"} and explanation.startswith(answer):
                            candidates.append((source_id, answer, explanation))
                    candidate_values = {(answer, explanation) for _, answer, explanation in candidates}
                    if len(candidate_values) == 1:
                        source_id, answer, explanation = candidates[0]
                        proposed["correctChoiceText"] = answer
                        proposed["explanationText"] = explanation
                        source = {"kind": "firestore_exact_question_identity_aligned", "questionId": source_id}
                        reason = "exact_question_duplicate_with_aligned_choice"

                if source is None and identity_aligned:
                    source = {"kind": "current_live_identity_aligned", "questionId": question_id}
                    reason = "current_quote_and_original_choice_aligned"

                if source is None:
                    local_candidates = local_indexes[
                        (target["qualification"], int(target["examYear"]))
                    ].get(normalize_text(live.get("questionText")), [])
                    if len(local_candidates) == 1:
                        explanation = canonicalize_explanation_prefix(
                            str(local_candidates[0]["explanationText"]),
                            str(local_candidates[0]["explanationText"]).split("。", 1)[0],
                        )
                        answer = next(
                            (item for item in ("正しい", "間違い") if explanation.startswith(item)),
                            None,
                        )
                        if answer:
                            proposed["correctChoiceText"] = answer
                            proposed["explanationText"] = explanation
                            source = copy.deepcopy(local_candidates[0])
                            reason = "exact_question_local_21_patch"

                if source is None and len(normalize_choice_identity(choice)) >= 20:
                    exact_manual = manual_exact_choices.get(
                        (
                            target["qualification"],
                            int(target["examYear"]),
                            normalize_choice_identity(choice),
                        ),
                        [],
                    )
                    exact_answers = {
                        item[1]["correctChoiceText"] for item in exact_manual
                    }
                    if len(exact_manual) == 1 and len(exact_answers) == 1:
                        manual, manual_choice = exact_manual[0]
                        proposed["correctChoiceText"] = manual_choice[
                            "correctChoiceText"
                        ]
                        proposed["explanationText"] = manual_choice[
                            "explanationText"
                        ]
                        source = {
                            **manual["source"],
                            "kind": "manual_01_04_review_unique_long_choice",
                            "choiceIndex": manual_choice["choiceIndex"],
                            "choiceText": manual_choice["choiceText"],
                        }
                        reason = "unique_long_choice_in_same_qualification_and_year"

                if source is None and len(normalize_choice_identity(choice)) >= 20:
                    fuzzy_manual = []
                    current_identity = normalize_choice_identity(choice)
                    for manual in manual_records:
                        if (
                            manual["qualification"] != target["qualification"]
                            or manual["examYear"] != int(target["examYear"])
                        ):
                            continue
                        for manual_choice in manual["choices"]:
                            score = difflib.SequenceMatcher(
                                None,
                                current_identity,
                                manual_choice["choiceIdentity"],
                            ).ratio()
                            fuzzy_manual.append((score, manual, manual_choice))
                    fuzzy_manual.sort(key=lambda item: item[0])
                    if fuzzy_manual:
                        best_score, manual, manual_choice = fuzzy_manual[-1]
                        second_score = fuzzy_manual[-2][0] if len(fuzzy_manual) > 1 else 0.0
                        if best_score >= 0.97 and best_score - second_score >= 0.10:
                            proposed["correctChoiceText"] = manual_choice[
                                "correctChoiceText"
                            ]
                            proposed["explanationText"] = manual_choice[
                                "explanationText"
                            ]
                            source = {
                                **manual["source"],
                                "kind": "manual_01_04_review_unique_fuzzy_long_choice",
                                "choiceIndex": manual_choice["choiceIndex"],
                                "choiceText": manual_choice["choiceText"],
                                "choiceSimilarity": round(best_score, 6),
                            }
                            reason = "unique_high_similarity_long_choice_in_same_year"

                if source is None:
                    prior = previous.get(question_id) or {}
                    prior_source = prior.get("source") or {}
                    prior_explanation = prior_source.get("explanationText")
                    if prior_source.get("kind") == "authored_from_similar" and isinstance(
                        prior_explanation, str
                    ):
                        answer = next(
                            (item for item in ("正しい", "間違い") if prior_explanation.startswith(item)),
                            None,
                        )
                        if answer:
                            proposed["correctChoiceText"] = answer
                            proposed["explanationText"] = prior_explanation
                            source = copy.deepcopy(prior_source)
                            reason = "previous_individually_authored_calculation_or_similar"

                if source is None:
                    status = "hold"
                    reason = "no_identity_safe_source"

        answer = CANONICAL_CORRECTNESS.get(str(proposed.get("correctChoiceText") or "").strip())
        explanation = str(proposed.get("explanationText") or "").strip()
        if status == "ready" and live.get("questionType") == "true_false":
            if answer not in {"正しい", "間違い"} or not explanation.startswith(answer):
                status = "hold"
                reason = "proposed_answer_explanation_mismatch"
        changed_fields = [
            field for field in proposed if proposed.get(field) != current.get(field)
        ]
        record = {
            "questionId": question_id,
            "grade": target["grade"],
            "qualification": target["qualification"],
            "examYear": int(target["examYear"]),
            "questionSetId": live.get("questionSetId"),
            "questionType": live.get("questionType"),
            "questionText": live.get("questionText"),
            "questionTextHash": text_hash(live.get("questionText")),
            "status": status,
            "reason": reason,
            "source": source,
            "current": current,
            "proposed": proposed,
            "changedFields": changed_fields,
        }
        ledger_records.append(record)
        if status != "ready":
            unresolved.append(question_id)

    if overrides_path is not None:
        overrides = read_json(overrides_path).get("overrides", [])
        by_id = {item["questionId"]: item for item in ledger_records}
        seen = set()
        for override in overrides:
            question_id = str(override.get("questionId") or "")
            if question_id not in by_id or question_id in seen:
                raise ValueError(f"invalid or duplicate recovery override: {question_id}")
            seen.add(question_id)
            record = by_id[question_id]
            if record["questionTextHash"] != override.get("expectedCurrentQuestionTextHash"):
                raise ValueError(f"recovery override questionText hash mismatch: {question_id}")
            proposed_override = override.get("proposed") or {}
            if not set(proposed_override).issubset(record["proposed"]):
                raise ValueError(f"recovery override field exceeds contract: {question_id}")
            record["proposed"].update(copy.deepcopy(proposed_override))
            record["source"] = copy.deepcopy(override.get("source"))
            record["reason"] = str(override.get("reason") or "")
            record["changedFields"] = [
                field
                for field in record["proposed"]
                if record["proposed"].get(field) != record["current"].get(field)
            ]

    validate_recovery_records(ledger_records)
    output = {
        "schemaVersion": "gas-shunin-semantic-recovery-v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "inventory": str(inventory_path),
        "counts": {
            "targetCount": len(ledger_records),
            "uniqueTargetCount": len({item["questionId"] for item in ledger_records}),
            "readyCount": sum(item["status"] == "ready" for item in ledger_records),
            "unresolvedCount": len(unresolved),
            "changedCount": sum(bool(item["changedFields"]) for item in ledger_records),
            "answerChangeCount": sum(
                "correctChoiceText" in item["changedFields"] for item in ledger_records
            ),
            "explanationChangeCount": sum(
                "explanationText" in item["changedFields"] for item in ledger_records
            ),
            "identityChangeCount": sum(
                "originalQuestionChoiceText" in item["changedFields"] for item in ledger_records
            ),
            "questionTextChangeCount": sum(
                "questionText" in item["changedFields"] for item in ledger_records
            ),
        },
        "unresolvedQuestionIds": unresolved,
        "records": ledger_records,
    }
    write_json(output_path, output)
    return output


def materialize_recovery(
    ledger_path: Path,
    artifact_path: Path,
    audit_path: Path,
) -> dict[str, Any]:
    ledger = read_json(ledger_path)
    records = ledger.get("records", [])
    if len(records) != 782 or any(item.get("status") != "ready" for item in records):
        raise ValueError("all 782 recovery decisions must be ready")
    changed = [item for item in records if item.get("changedFields")]
    target_ids = [item["questionId"] for item in changed]
    if not target_ids:
        raise ValueError("recovery ledger has no changes")
    live_documents = fetch_question_ids(firestore_client(), target_ids)
    write_fields = (
        "originalQuestionBodyText",
        "originalQuestionChoiceText",
        "questionText",
        "correctChoiceText",
        "explanationText",
    )
    questions = []
    for record in changed:
        question_id = record["questionId"]
        live = live_documents.get(question_id)
        if live is None:
            raise ValueError(f"recovery target missing: {question_id}")
        if text_hash(live.get("questionText")) != record.get("questionTextHash"):
            raise ValueError(f"recovery questionText changed: {question_id}")
        for field, expected in record["current"].items():
            if live.get(field) != expected:
                raise ValueError(f"recovery live field changed: {question_id} {field}")
        payload = {
            key: copy.deepcopy(live[key])
            for key in DOC_COMPARE_KEYS
            if key in live and key not in PRODUCTION_CLIENT_OMITTED_FIELDS
        }
        payload["questionId"] = question_id
        payload.update(copy.deepcopy(record["proposed"]))
        questions.append(payload)
    validate_question_patch_questions(questions, write_fields, str(artifact_path))
    live_hash = firestore_live_fingerprint(target_ids, live_documents)
    artifact = {
        "schemaVersion": "gas-shunin-semantic-recovery-v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "expectedLiveFingerprint": live_hash,
        "writeFields": list(write_fields),
        "questions": questions,
    }
    audit = {
        "schemaVersion": artifact["schemaVersion"],
        "generatedAt": artifact["generatedAt"],
        "ledger": str(ledger_path),
        "targetCount": len(target_ids),
        "uniqueTargetCount": len(set(target_ids)),
        "writeFields": list(write_fields),
        "expectedLiveFingerprint": live_hash,
        "artifactSha256": hashlib.sha256(
            json.dumps(artifact, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "records": [
            {
                "questionId": item["questionId"],
                "changedFields": item["changedFields"],
                "source": item["source"],
            }
            for item in changed
        ],
    }
    write_json(artifact_path, artifact)
    write_json(audit_path, audit)
    return audit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    bootstrap = subparsers.add_parser("bootstrap")
    bootstrap.add_argument("--inventory", type=Path, required=True)
    bootstrap.add_argument("--repo-root", type=Path, default=Path.cwd())
    bootstrap.add_argument("--grade", choices=("甲種", "乙種"), required=True)
    bootstrap.add_argument("--year", type=int, required=True)
    bootstrap.add_argument("--output", type=Path, required=True)
    override = subparsers.add_parser("apply-overrides")
    override.add_argument("--decisions", type=Path, required=True)
    override.add_argument("--overrides", type=Path, required=True)
    override.add_argument("--output", type=Path, required=True)
    conflicts = subparsers.add_parser("mark-answer-conflicts")
    conflicts.add_argument("--decisions", type=Path, required=True)
    conflicts.add_argument("--output", type=Path, required=True)
    make = subparsers.add_parser("materialize")
    make.add_argument("--decisions", type=Path, required=True)
    make.add_argument("--artifact", type=Path, required=True)
    make.add_argument("--audit", type=Path, required=True)
    make.add_argument("--allow-already-filled", action="store_true")
    make.add_argument("--allow-holds", action="store_true")
    corrections = subparsers.add_parser("materialize-answer-corrections")
    corrections.add_argument("--decisions", type=Path, required=True)
    corrections.add_argument("--artifact", type=Path, required=True)
    corrections.add_argument("--audit", type=Path, required=True)
    recovery = subparsers.add_parser("build-recovery-ledger")
    recovery.add_argument("--inventory", type=Path, required=True)
    recovery.add_argument("--repo-root", type=Path, default=Path.cwd())
    recovery.add_argument("--output", type=Path, required=True)
    recovery.add_argument("--overrides", type=Path)
    recovery_materialize = subparsers.add_parser("materialize-recovery")
    recovery_materialize.add_argument("--ledger", type=Path, required=True)
    recovery_materialize.add_argument("--artifact", type=Path, required=True)
    recovery_materialize.add_argument("--audit", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "bootstrap":
        result = bootstrap_decisions(
            args.inventory, args.repo_root.resolve(), args.grade, args.year, args.output
        )
    elif args.command == "apply-overrides":
        result = apply_overrides(args.decisions, args.overrides, args.output)
    elif args.command == "mark-answer-conflicts":
        result = mark_rejected_answer_conflicts(args.decisions, args.output)
    elif args.command == "materialize-answer-corrections":
        result = materialize_answer_corrections(
            args.decisions,
            args.artifact,
            args.audit,
        )
    elif args.command == "build-recovery-ledger":
        result = build_recovery_ledger(
            args.inventory,
            args.repo_root.resolve(),
            args.output,
            args.overrides,
        )
    elif args.command == "materialize-recovery":
        result = materialize_recovery(
            args.ledger,
            args.artifact,
            args.audit,
        )
    else:
        result = materialize(
            args.decisions,
            args.artifact,
            args.audit,
            args.allow_already_filled,
            args.allow_holds,
        )
    print(json.dumps(result.get("counts", result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
