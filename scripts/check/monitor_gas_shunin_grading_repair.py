#!/usr/bin/env python3
"""ガス主任技術者の採点不整合修正をFirestore反映前まで読み取り専用で監視する。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.upload.upload_questions_to_firestore import (  # noqa: E402
    DOC_COMPARE_KEYS,
    EXISTING_DOC_FIELD_PATHS,
    build_doc_data_base,
    choice_only_delete_fields,
    firestore_live_fingerprint,
    init_firestore,
)


DEFAULT_PREFLIGHT_DIR = Path(
    "output/question_review_console/preupload_runs/gas-shunin-otsu/"
    "20260724-answer-grading-repair"
)
EXPECTED_CANDIDATE_ARTIFACT_COUNT = 18


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(encoded)


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON objectではありません: {path}")
    return value


def discover_current_candidate_artifacts(repo_root: Path) -> list[Path]:
    artifact_paths = []
    for qualification in ("gas-shunin-kou", "gas-shunin-otsu"):
        artifact_dir = (
            repo_root / "output" / qualification / "questions_json" / "upload_to_firestore"
        )
        for year in range(2017, 2026):
            matches = sorted(artifact_dir.glob(f"{year}_firestore_*.json"))
            if not matches:
                raise ValueError(
                    f"現行Firestore候補がありません: {qualification} {year}"
                )
            artifact_paths.append(matches[-1])
    return artifact_paths


def load_candidate_questions(artifact_paths: list[Path]) -> dict[str, dict]:
    questions_by_id: dict[str, dict] = {}
    for path in artifact_paths:
        payload = load_json(path)
        questions = payload.get("questions")
        if not isinstance(questions, list):
            raise ValueError(f"questions配列がありません: {path}")
        for question in questions:
            if not isinstance(question, dict):
                raise ValueError(f"questions要素がobjectではありません: {path}")
            question_id = str(question.get("questionId") or "").strip()
            if not question_id:
                raise ValueError(f"questionIdがありません: {path}")
            if question_id in questions_by_id:
                raise ValueError(f"questionIdが重複しています: {question_id}")
            questions_by_id[question_id] = question
    return questions_by_id


def source_snapshot_fingerprint(source_dir: Path) -> str:
    file_hashes = [
        (
            path.relative_to(source_dir).as_posix(),
            sha256_bytes(path.read_bytes()),
        )
        for path in sorted(source_dir.rglob("*.json"))
        if path.is_file()
    ]
    if not file_hashes:
        raise ValueError(f"00_source JSONがありません: {source_dir}")
    return canonical_sha256(file_hashes)


def target_candidate_fingerprint(questions: list[dict]) -> str:
    seen_ids: set[str] = set()
    for question in questions:
        question_id = str(question.get("questionId") or "").strip()
        if not question_id:
            raise ValueError("pre-upload artifactにquestionIdがありません")
        if question_id in seen_ids:
            raise ValueError(f"pre-upload artifactのquestionIdが重複しています: {question_id}")
        seen_ids.add(question_id)
    return canonical_sha256(questions)


def grading_mode_class(question_type: object) -> str:
    return "statement_verdict" if question_type == "true_false" else "problem_selection"


def upload_difference_fields(candidate: dict, live: dict) -> list[str]:
    candidate_base = build_doc_data_base(candidate)
    fields = [
        field
        for field in DOC_COMPARE_KEYS
        if field in candidate_base and live.get(field) != candidate_base.get(field)
    ]
    fields.extend(
        f"{field} (delete)"
        for field in choice_only_delete_fields(candidate_base, live)
    )
    return fields


def fetch_live_documents(db, document_ids: list[str], chunk_size: int = 500) -> dict[str, dict]:
    live_documents: dict[str, dict] = {}
    for start in range(0, len(document_ids), chunk_size):
        chunk = document_ids[start : start + chunk_size]
        refs = [db.collection("questions").document(question_id) for question_id in chunk]
        snapshots = db.get_all(refs, field_paths=EXISTING_DOC_FIELD_PATHS)
        for snapshot in snapshots:
            if snapshot.exists:
                live_documents[str(snapshot.id)] = snapshot.to_dict() or {}
    return live_documents


def build_monitor_report(
    *,
    repo_root: Path,
    candidate_paths: list[Path],
    candidate_by_id: dict[str, dict],
    preflight_dir: Path,
    live_documents: dict[str, dict],
) -> dict:
    preflight_path = preflight_dir / "preflight.json"
    artifact_path = preflight_dir / "artifact.json"
    gate_report_path = preflight_dir / "gate_report.json"
    result_path = preflight_dir / "result.json"

    preflight = load_json(preflight_path)
    target_artifact = load_json(artifact_path)
    gate_report = load_json(gate_report_path)
    result = load_json(result_path)
    target_questions = target_artifact.get("questions")
    if not isinstance(target_questions, list):
        raise ValueError(f"questions配列がありません: {artifact_path}")
    target_by_id = {
        str(question.get("questionId") or ""): question for question in target_questions
    }
    target_ids = list(preflight.get("documentIds") or [])
    candidate_ids = sorted(candidate_by_id)
    missing_live_ids = [
        question_id for question_id in candidate_ids if question_id not in live_documents
    ]

    correct_choice_mismatches = []
    question_type_mismatches = []
    grading_mode_class_mismatches = []
    for question_id in candidate_ids:
        live = live_documents.get(question_id)
        if live is None:
            continue
        candidate = build_doc_data_base(candidate_by_id[question_id])
        if live.get("correctChoiceText") != candidate.get("correctChoiceText"):
            correct_choice_mismatches.append(
                {
                    "questionId": question_id,
                    "candidate": candidate.get("correctChoiceText"),
                    "live": live.get("correctChoiceText"),
                }
            )
        if live.get("questionType") != candidate.get("questionType"):
            mismatch = {
                "questionId": question_id,
                "candidate": candidate.get("questionType"),
                "live": live.get("questionType"),
            }
            question_type_mismatches.append(mismatch)
            if grading_mode_class(live.get("questionType")) != grading_mode_class(
                candidate.get("questionType")
            ):
                grading_mode_class_mismatches.append(mismatch)

    target_differences = []
    target_correct_choice_mismatches = []
    for question_id in target_ids:
        candidate = target_by_id.get(question_id)
        live = live_documents.get(question_id)
        if candidate is None or live is None:
            continue
        difference_fields = upload_difference_fields(candidate, live)
        if difference_fields:
            target_differences.append(
                {"questionId": question_id, "fields": difference_fields}
            )
        candidate_base = build_doc_data_base(candidate)
        if live.get("correctChoiceText") != candidate_base.get("correctChoiceText"):
            target_correct_choice_mismatches.append(question_id)

    verification = preflight.get("verification") or {}
    source_dir = (
        repo_root
        / "output"
        / str(preflight.get("qualification") or "")
        / "questions_json"
        / str(preflight.get("listGroupId") or "")
        / "00_source"
    )
    target_live_documents = {
        question_id: live_documents[question_id]
        for question_id in target_ids
        if question_id in live_documents
    }

    checks = {
        "candidateArtifactCountMatches": (
            len(candidate_paths) == EXPECTED_CANDIDATE_ARTIFACT_COUNT
        ),
        "candidateCountMatches": (
            len(candidate_by_id) == verification.get("fullCandidateCount")
        ),
        "liveCountMatches": (
            len(live_documents) == verification.get("fullLiveReadCount")
        ),
        "missingLiveCountMatches": not missing_live_ids,
        "correctChoiceMismatchCountMatches": (
            len(correct_choice_mismatches)
            == verification.get("fullCorrectChoiceMismatchCount")
        ),
        "questionTypeMismatchCountMatches": (
            len(question_type_mismatches)
            == verification.get("fullQuestionTypeMismatchCount")
        ),
        "gradingModeClassMismatchCountMatches": (
            len(grading_mode_class_mismatches)
            == verification.get("gradingModeClassMismatchCount")
        ),
        "targetDocumentCountMatches": (
            len(target_ids)
            == preflight.get("documentCount")
            == target_artifact.get("total_count")
            == result.get("documentCount")
        ),
        "targetCandidateMatchesFullCandidate": all(
            candidate_by_id.get(question_id) == target_by_id.get(question_id)
            for question_id in target_ids
        ),
        "targetPendingDifferenceCountMatches": (
            len(target_differences) == preflight.get("changedCount")
        ),
        "targetCorrectChoiceMismatchCountMatches": (
            len(target_correct_choice_mismatches)
            == preflight.get("correctChoiceMismatchCount")
        ),
        "artifactHashMatches": (
            sha256_bytes(artifact_path.read_bytes()) == preflight.get("artifactSha256")
        ),
        "candidateHashMatches": (
            target_candidate_fingerprint(target_questions)
            == preflight.get("candidateSha256")
        ),
        "sourceHashMatches": (
            source_snapshot_fingerprint(source_dir) == preflight.get("sourceSha256")
        ),
        "targetLiveHashMatches": (
            firestore_live_fingerprint(target_ids, target_live_documents)
            == preflight.get("liveSha256")
        ),
        "preuploadStatusReady": (
            preflight.get("status") == "ready_for_upload"
            and result.get("status") == "succeeded"
            and result.get("outcome") == "ready_for_upload"
        ),
        "gateStillRecordedAsPassing": (
            gate_report.get("ok") is True and gate_report.get("issueCount") == 0
        ),
        "firestoreWriteStillRecordedAsFalse": (
            preflight.get("firestoreWritePerformed") is False
            and result.get("firestoreWritePerformed") is False
        ),
    }
    failed_checks = [name for name, passed in checks.items() if not passed]
    return {
        "schemaVersion": "gas-shunin-grading-repair-monitor/v1",
        "status": "healthy" if not failed_checks else "alert",
        "candidateArtifactCount": len(candidate_paths),
        "candidateQuestionCount": len(candidate_by_id),
        "liveFoundCount": len(live_documents),
        "missingLiveCount": len(missing_live_ids),
        "correctChoiceMismatchCount": len(correct_choice_mismatches),
        "questionTypeMismatchCount": len(question_type_mismatches),
        "gradingModeClassMismatchCount": len(grading_mode_class_mismatches),
        "targetPendingDifferenceCount": len(target_differences),
        "targetCorrectChoiceMismatchCount": len(target_correct_choice_mismatches),
        "checks": checks,
        "failedChecks": failed_checks,
        "samples": {
            "missingLiveIds": missing_live_ids[:10],
            "correctChoiceMismatches": correct_choice_mismatches[:10],
            "targetDifferences": target_differences,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ガス主任技術者の採点不整合修正を読み取り専用で監視する"
    )
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--preflight-dir", type=Path, default=DEFAULT_PREFLIGHT_DIR)
    parser.add_argument("--credentials-json", type=Path)
    args = parser.parse_args(argv)

    repo_root = args.root.resolve()
    preflight_dir = (
        args.preflight_dir
        if args.preflight_dir.is_absolute()
        else repo_root / args.preflight_dir
    )

    candidate_paths = discover_current_candidate_artifacts(repo_root)
    candidate_by_id = load_candidate_questions(candidate_paths)
    db = init_firestore(args.credentials_json)
    live_documents = fetch_live_documents(db, sorted(candidate_by_id))
    report = build_monitor_report(
        repo_root=repo_root,
        candidate_paths=candidate_paths,
        candidate_by_id=candidate_by_id,
        preflight_dir=preflight_dir,
        live_documents=live_documents,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
