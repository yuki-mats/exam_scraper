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
