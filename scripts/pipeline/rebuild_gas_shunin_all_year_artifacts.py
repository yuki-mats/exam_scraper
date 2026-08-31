#!/usr/bin/env python3
"""公式PDF検証済み正本からガス主任技術者の公開成果物を再生成する。

初回移行だけ、公式PDF台帳で全件照合済みのFirestore snapshotを
`25_verified_publication`へ固定する。通常実行はそのローカル正本だけを読み、
`30_merged_2`、`40_convert`、upload artifactを再生成する。

退避済みmerged、旧40_convert、Firestore readbackは通常実行の入力にしない。
`00_source`とFirestoreはこのスクリプトでは変更しない。
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


ROOT_DIR = Path(__file__).resolve().parents[2]
QUALIFICATIONS = ("gas-shunin-kou", "gas-shunin-otsu")
YEARS = tuple(range(2017, 2026))
EXPECTED_COUNTS = {"gas-shunin-kou": 2212, "gas-shunin-otsu": 1913}
SCHEMA_VERSION = "gas-shunin-verified-publication/v1"
MERGED_SCHEMA_VERSION = "gas-shunin-verified-merged/v1"
METADATA_FIELDS = {
    "createdAt",
    "createdById",
    "updatedAt",
    "updatedById",
    "questionSetRef",
}
SUGGESTION_FIELDS = ("suggestedQuestions", "suggestedQuestionDetails")
ROUTING_FIELDS = ("qualificationId", "listGroupId")
DEFAULT_DOCUMENT_INDEX = (
    ROOT_DIR
    / "docs/goals/gas-shunin-missing-basic-explanations-firestore/notes"
    / "T030-official-document-index.json"
)
DEFAULT_PDF_VERIFICATION = (
    ROOT_DIR
    / "docs/goals/gas-shunin-missing-basic-explanations-firestore/notes"
    / "T030-official-pdf-index-verification.json"
)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON objectではありません: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def active_display(question: dict[str, Any]) -> bool:
    return question.get("isDeleted") is False and question.get("isChoiceOnly") is False


def publication_document(question: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in question.items()
        if key not in METADATA_FIELDS
    }


def normalized_suggestions(question: dict[str, Any]) -> tuple[list[str], list[dict[str, str]]]:
    raw = question.get("suggestedQuestionDetails")
    details: list[dict[str, str]] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            prompt = str(item.get("question") or "").strip()
            answer = str(item.get("answer") or "").strip()
            if not prompt or not answer:
                continue
            details.append({"question": prompt, "answer": answer})
            if len(details) == 3:
                break
    return [item["question"] for item in details], details


def normalize_publication_document(
    question: dict[str, Any], *, qualification: str | None = None
) -> dict[str, Any]:
    result = publication_document(question)
    if qualification is not None:
        result["qualificationId"] = qualification
        result["listGroupId"] = str(int(result.get("examYear") or 0))
    questions, details = normalized_suggestions(result)
    if details:
        result["suggestedQuestions"] = questions
        result["suggestedQuestionDetails"] = details
    else:
        result.pop("suggestedQuestions", None)
        result.pop("suggestedQuestionDetails", None)
    return result


def official_index_by_id(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    payload = load_json(path)
    summary = payload.get("summary") or {}
    if summary.get("holdCount") != 0 or summary.get("contentRepairRequiredCount") != 0:
        raise ValueError("公式PDF document indexに未解決項目があります。")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError(f"公式PDF document indexが不正です: {path}")
    indexed = {
        str(item["questionId"]): item
        for item in records
        if isinstance(item, dict) and item.get("questionId")
    }
    if len(indexed) != len(records):
        raise ValueError("公式PDF document indexのquestionIdが重複しています。")
    return indexed, payload


def verify_official_pdf_evidence(
    *, document_index: Path, pdf_verification: Path
) -> dict[str, Any]:
    indexed, payload = official_index_by_id(document_index)
    verification = load_json(pdf_verification)
    if verification.get("result") != "pass":
        raise ValueError("公式PDF index verificationがpassではありません。")
    if verification.get("questionIndex", {}).get("holdCount") != 0:
        raise ValueError("公式問題PDF indexにholdがあります。")
    if verification.get("questionIndex", {}).get("currentPdfHashMismatchCount") != 0:
        raise ValueError("公式問題PDFのhashが検証時から変わっています。")
    if verification.get("answerIndex", {}).get("currentPdfHashMismatchCount") != 0:
        raise ValueError("公式正答PDFのhashが検証時から変わっています。")

    pdfs: dict[str, str] = {}
    for record in payload["records"]:
        for field in ("officialQuestionPdf", "officialAnswerPdf"):
            item = record.get(field) or {}
            path_text = str(item.get("path") or "")
            expected_hash = str(item.get("sha256") or "")
            if not path_text or not expected_hash:
                raise ValueError(f"公式PDF evidenceが不完全です: {record.get('questionId')}")
            previous = pdfs.setdefault(path_text, expected_hash)
            if previous != expected_hash:
                raise ValueError(f"同一PDFに異なるhashがあります: {path_text}")
    mismatches = []
    for path_text, expected_hash in sorted(pdfs.items()):
        path = (ROOT_DIR / path_text).resolve()
        if not path.is_file() or sha256(path) != expected_hash:
            mismatches.append(path_text)
    if mismatches:
        raise ValueError(f"公式PDFの現物hashが一致しません: {mismatches}")
    return {
        "indexedQuestionDocumentCount": len(indexed),
        "officialPdfCount": len(pdfs),
        "documentIndexSha256": sha256(document_index),
        "pdfVerificationSha256": sha256(pdf_verification),
    }


def active_snapshot_documents(snapshot_dir: Path) -> list[dict[str, Any]]:
    questions = load_json(snapshot_dir / "reconstructed/questions.json").get("questions")
    if not isinstance(questions, list):
        raise ValueError(f"snapshotのquestions配列が不正です: {snapshot_dir}")
    return [item for item in questions if isinstance(item, dict) and active_display(item)]


def build_canonical_documents(
    *,
    qualification: str,
    snapshot_dir: Path,
    official_documents: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    live = active_snapshot_documents(snapshot_dir)
    if len(live) != EXPECTED_COUNTS[qualification]:
        raise ValueError(
            f"表示対象件数が期待値と異なります: {qualification} "
            f"expected={EXPECTED_COUNTS[qualification]} actual={len(live)}"
        )
    ids = [str(item.get("questionId") or "") for item in live]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError(f"snapshotのquestionIdが空又は重複しています: {qualification}")
    missing_evidence = sorted(set(ids) - set(official_documents))
    if missing_evidence:
        raise ValueError(
            f"公式PDFに対応しない表示対象があります: {qualification} {missing_evidence}"
        )

    normalized = [
        normalize_publication_document(item, qualification=qualification)
        for item in live
    ]
    fields = Counter()
    changed_ids = []
    for before, after in zip(live, normalized, strict=True):
        changed = sorted(
            key
            for key in set(before) | set(after)
            if key not in METADATA_FIELDS and before.get(key) != after.get(key)
        )
        if changed:
            changed_ids.append(str(after["questionId"]))
            fields.update(changed)
    if set(fields) - set(SUGGESTION_FIELDS + ROUTING_FIELDS):
        raise ValueError(f"正本移行で補足質問・配信経路以外が変化します: {dict(fields)}")
    return sorted(normalized, key=lambda item: str(item["questionId"])), {
        "questionCount": len(normalized),
        "normalizedQuestionCount": len(changed_ids),
        "normalizedFieldCounts": dict(sorted(fields.items())),
        "normalizedQuestionIds": changed_ids,
        "snapshotQuestionsSha256": sha256(
            snapshot_dir / "reconstructed/questions.json"
        ),
    }


def canonical_path(qualification: str, year: int) -> Path:
    return (
        ROOT_DIR
        / "output"
        / qualification
        / "questions_json"
        / str(year)
        / "25_verified_publication"
        / f"{year}_verified_publication.json"
    )


def build_year_bundle(
    *,
    qualification: str,
    year: int,
    questions: Iterable[dict[str, Any]],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    values = sorted(
        (copy.deepcopy(item) for item in questions),
        key=lambda item: str(item["questionId"]),
    )
    if any(int(item.get("examYear") or 0) != year for item in values):
        raise ValueError(f"年度外の問題が混在しています: {qualification} {year}")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "sourcePolicy": "official question and answer PDFs verified; listing site not used",
        "qualificationId": qualification,
        "list_group_id": str(year),
        "questions": values,
        "total_count": len(values),
        "evidence": copy.deepcopy(evidence),
    }


def load_canonical_bundles() -> dict[str, dict[int, dict[str, Any]]]:
    result: dict[str, dict[int, dict[str, Any]]] = {}
    all_ids: set[str] = set()
    for qualification in QUALIFICATIONS:
        result[qualification] = {}
        count = 0
        for year in YEARS:
            path = canonical_path(qualification, year)
            if not path.is_file():
                raise FileNotFoundError(f"検証済み公開正本がありません: {path}")
            bundle = load_json(path)
            if bundle.get("schemaVersion") != SCHEMA_VERSION:
                raise ValueError(f"正本schemaが不正です: {path}")
            questions = bundle.get("questions")
            if not isinstance(questions, list):
                raise ValueError(f"正本questions配列が不正です: {path}")
            for question in questions:
                if not isinstance(question, dict) or not active_display(question):
                    raise ValueError(f"正本に表示対象外recordがあります: {path}")
                question["qualificationId"] = qualification
                question["listGroupId"] = str(year)
                details = question.get("suggestedQuestionDetails")
                prompts = question.get("suggestedQuestions")
                if isinstance(details, list) and len(details) > 3:
                    raise ValueError(f"補足質問が3件を超えています: {question.get('questionId')}")
                if details is not None and prompts != [item["question"] for item in details]:
                    raise ValueError(f"補足質問fieldが一致しません: {question.get('questionId')}")
                question_id = str(question.get("questionId") or "")
                if not question_id or question_id in all_ids:
                    raise ValueError(f"正本questionIdが空又は重複です: {question_id}")
                all_ids.add(question_id)
            count += len(questions)
            result[qualification][year] = bundle
        if count != EXPECTED_COUNTS[qualification]:
            raise ValueError(
                f"正本件数が不正です: {qualification} "
                f"expected={EXPECTED_COUNTS[qualification]} actual={count}"
            )
    return result


def compare_with_snapshot(
    *, canonical_questions: list[dict[str, Any]], snapshot_dir: Path
) -> dict[str, Any]:
    local = {str(item["questionId"]): publication_document(item) for item in canonical_questions}
    live_values = active_snapshot_documents(snapshot_dir)
    live = {str(item["questionId"]): publication_document(item) for item in live_values}
    missing = sorted(set(live) - set(local))
    extra = sorted(set(local) - set(live))
    different: dict[str, list[str]] = {}
    field_counts = Counter()
    for question_id in sorted(set(local) & set(live)):
        fields = sorted(
            key
            for key in set(local[question_id]) | set(live[question_id])
            if local[question_id].get(key) != live[question_id].get(key)
        )
        if fields:
            different[question_id] = fields
            field_counts.update(fields)
    return {
        "localCount": len(local),
        "liveCount": len(live),
        "missingQuestionIds": missing,
        "extraQuestionIds": extra,
        "differentQuestionCount": len(different),
        "differentFieldCounts": dict(sorted(field_counts.items())),
        "differentQuestions": different,
        "status": "pass" if not (missing or extra or different) else "different",
    }


def build_suggestion_patch(
    *, canonical_questions: list[dict[str, Any]], snapshot_dir: Path
) -> dict[str, Any]:
    local = {str(item["questionId"]): item for item in canonical_questions}
    live = {
        str(item["questionId"]): item
        for item in active_snapshot_documents(snapshot_dir)
    }
    if set(local) != set(live):
        raise ValueError("補足質問patchのローカルIDとlive IDが一致しません。")
    questions = []
    for question_id in sorted(local):
        source = local[question_id]
        current = live[question_id]
        if all(current.get(field) == source.get(field) for field in SUGGESTION_FIELDS):
            continue
        details = source.get("suggestedQuestionDetails")
        prompts = source.get("suggestedQuestions")
        if not isinstance(details, list) or not details:
            raise ValueError(f"限定patchでは補足質問削除を扱いません: {question_id}")
        questions.append(
            {
                "questionId": question_id,
                "questionText": source.get("questionText"),
                "isDeleted": False,
                "isChoiceOnly": False,
                "suggestedQuestions": prompts,
                "suggestedQuestionDetails": details,
            }
        )
    return {
        "schemaVersion": "gas-shunin-suggestion-normalization/v1",
        "writeFields": list(SUGGESTION_FIELDS),
        "questions": questions,
        "total_count": len(questions),
    }


def build_suggestion_rollback(
    *, canonical_questions: list[dict[str, Any]], snapshot_dir: Path
) -> dict[str, Any]:
    local = {str(item["questionId"]): item for item in canonical_questions}
    live = {
        str(item["questionId"]): item
        for item in active_snapshot_documents(snapshot_dir)
    }
    if set(local) != set(live):
        raise ValueError("補足質問rollbackのローカルIDとlive IDが一致しません。")
    questions = []
    for question_id in sorted(local):
        target = local[question_id]
        current = live[question_id]
        if all(current.get(field) == target.get(field) for field in SUGGESTION_FIELDS):
            continue
        details = current.get("suggestedQuestionDetails")
        prompts = current.get("suggestedQuestions")
        if not isinstance(details, list) or not details:
            raise ValueError(f"rollback元の補足質問がありません: {question_id}")
        questions.append(
            {
                "questionId": question_id,
                "questionText": current.get("questionText"),
                "isDeleted": False,
                "isChoiceOnly": False,
                "suggestedQuestions": prompts,
                "suggestedQuestionDetails": details,
            }
        )
    return {
        "schemaVersion": "gas-shunin-suggestion-normalization-rollback/v1",
        "allowLegacySuggestionRollback": True,
        "writeFields": list(SUGGESTION_FIELDS),
        "questions": questions,
        "total_count": len(questions),
    }


def build_routing_patch(
    *, canonical_questions: list[dict[str, Any]], snapshot_dir: Path
) -> dict[str, Any]:
    local = {str(item["questionId"]): item for item in canonical_questions}
    live = {
        str(item["questionId"]): item
        for item in active_snapshot_documents(snapshot_dir)
    }
    if set(local) != set(live):
        raise ValueError("配信経路patchのローカルIDとlive IDが一致しません。")
    questions = []
    for question_id in sorted(local):
        target = local[question_id]
        current = live[question_id]
        if all(current.get(field) == target.get(field) for field in ROUTING_FIELDS):
            continue
        questions.append(
            {
                "questionId": question_id,
                "questionText": target.get("questionText"),
                "examYear": target.get("examYear"),
                "isDeleted": False,
                "isChoiceOnly": False,
                "qualificationId": target.get("qualificationId"),
                "listGroupId": target.get("listGroupId"),
            }
        )
    return {
        "schemaVersion": "gas-shunin-routing-normalization/v1",
        "writeFields": list(ROUTING_FIELDS),
        "questions": questions,
        "total_count": len(questions),
    }


def build_routing_rollback(
    *, canonical_questions: list[dict[str, Any]], snapshot_dir: Path
) -> dict[str, Any]:
    local = {str(item["questionId"]): item for item in canonical_questions}
    live = {
        str(item["questionId"]): item
        for item in active_snapshot_documents(snapshot_dir)
    }
    questions = []
    for question_id in sorted(local):
        target = local[question_id]
        current = live[question_id]
        if all(current.get(field) == target.get(field) for field in ROUTING_FIELDS):
            continue
        questions.append(
            {
                "questionId": question_id,
                "questionText": current.get("questionText"),
                "examYear": current.get("examYear"),
                "isDeleted": False,
                "isChoiceOnly": False,
                "qualificationId": current.get("qualificationId"),
                "listGroupId": current.get("listGroupId"),
            }
        )
    return {
        "schemaVersion": "gas-shunin-routing-normalization-rollback/v1",
        "writeFields": list(ROUTING_FIELDS),
        "questions": questions,
        "total_count": len(questions),
    }


def archive_and_write(directory: Path, filename: str, payload: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    existing = sorted(path for path in directory.glob("*.json") if path.is_file())
    if existing:
        old_dir = directory / "old"
        old_dir.mkdir(parents=True, exist_ok=True)
        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        for path in existing:
            target = old_dir / path.name
            if target.exists():
                target = old_dir / f"{path.stem}_{timestamp}{path.suffix}"
            shutil.move(str(path), str(target))
    target = directory / filename
    write_json(target, payload)
    return target


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ガス主任技術者2017〜2025年を検証済み公開正本から再生成する"
    )
    parser.add_argument("--bootstrap-from-snapshots", action="store_true")
    parser.add_argument("--kou-snapshot", type=Path)
    parser.add_argument("--otsu-snapshot", type=Path)
    parser.add_argument("--document-index", type=Path, default=DEFAULT_DOCUMENT_INDEX)
    parser.add_argument("--pdf-verification", type=Path, default=DEFAULT_PDF_VERIFICATION)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--receipt", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    snapshot_dirs = {
        "gas-shunin-kou": args.kou_snapshot.resolve() if args.kou_snapshot else None,
        "gas-shunin-otsu": args.otsu_snapshot.resolve() if args.otsu_snapshot else None,
    }
    evidence = verify_official_pdf_evidence(
        document_index=args.document_index.resolve(),
        pdf_verification=args.pdf_verification.resolve(),
    )
    indexed, _ = official_index_by_id(args.document_index.resolve())
    receipt: dict[str, Any] = {
        "schemaVersion": "gas-shunin-local-all-year-rebuild/v2",
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "apply": args.apply,
        "bootstrapFromSnapshots": args.bootstrap_from_snapshots,
        "sourcePolicy": "official PDFs plus local verified publication; listing site not used",
        "officialEvidence": evidence,
        "firestoreWriteCount": 0,
        "protected00SourceWriteCount": 0,
        "qualifications": {},
        "years": [],
    }

    built: dict[str, dict[int, dict[str, Any]]] = {}
    if args.bootstrap_from_snapshots:
        if any(path is None for path in snapshot_dirs.values()):
            raise ValueError("初回移行には甲種・乙種のsnapshotが必要です。")
        for qualification in QUALIFICATIONS:
            snapshot_dir = snapshot_dirs[qualification]
            assert snapshot_dir is not None
            questions, summary = build_canonical_documents(
                qualification=qualification,
                snapshot_dir=snapshot_dir,
                official_documents=indexed,
            )
            by_year = {year: [] for year in YEARS}
            for question in questions:
                year = int(question.get("examYear") or 0)
                if year not in by_year:
                    raise ValueError(f"対象外年度です: {qualification} {year}")
                by_year[year].append(question)
            built[qualification] = {}
            for year in YEARS:
                bundle = build_year_bundle(
                    qualification=qualification,
                    year=year,
                    questions=by_year[year],
                    evidence={
                        **evidence,
                        "bootstrapSnapshotQuestionsSha256": summary[
                            "snapshotQuestionsSha256"
                        ],
                    },
                )
                built[qualification][year] = bundle
            receipt["qualifications"][qualification] = summary
    else:
        built = load_canonical_bundles()

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    for qualification in QUALIFICATIONS:
        all_questions: list[dict[str, Any]] = []
        for year in YEARS:
            bundle = built[qualification][year]
            questions = bundle.get("questions") or []
            if args.apply:
                write_json(canonical_path(qualification, year), bundle)
            all_questions.extend(copy.deepcopy(questions))
            merged = copy.deepcopy(bundle)
            merged["schemaVersion"] = MERGED_SCHEMA_VERSION
            converted = {
                "list_group_id": str(year),
                "questions": copy.deepcopy(questions),
                "total_count": len(questions),
            }
            year_receipt = {
                "qualification": qualification,
                "year": year,
                "questionCount": len(questions),
                "canonicalSha256": canonical_hash(bundle),
                "mergedSha256": canonical_hash(merged),
                "convertSha256": canonical_hash(converted),
            }
            if args.apply:
                base = ROOT_DIR / "output" / qualification / "questions_json" / str(year)
                merged_path = archive_and_write(
                    base / "30_merged_2",
                    f"{year}_verified_publication_merged.json",
                    merged,
                )
                convert_path = archive_and_write(
                    base / "40_convert",
                    f"{year}_firestore_{timestamp}.json",
                    converted,
                )
                year_receipt["mergedPath"] = str(merged_path.relative_to(ROOT_DIR))
                year_receipt["convertPath"] = str(convert_path.relative_to(ROOT_DIR))
            receipt["years"].append(year_receipt)

        if len(all_questions) != EXPECTED_COUNTS[qualification]:
            raise ValueError(f"全年度件数が不正です: {qualification}")
        qualification_receipt = receipt["qualifications"].setdefault(
            qualification, {"questionCount": len(all_questions)}
        )
        snapshot_dir = snapshot_dirs[qualification]
        if snapshot_dir is not None:
            parity = compare_with_snapshot(
                canonical_questions=all_questions,
                snapshot_dir=snapshot_dir,
            )
            qualification_receipt["snapshotParity"] = parity
            patch = build_suggestion_patch(
                canonical_questions=all_questions,
                snapshot_dir=snapshot_dir,
            )
            qualification_receipt["suggestionPatchCount"] = patch["total_count"]
            rollback = build_suggestion_rollback(
                canonical_questions=all_questions,
                snapshot_dir=snapshot_dir,
            )
            if rollback["total_count"] != patch["total_count"]:
                raise ValueError("補足質問patchとrollbackの件数が一致しません。")
            if args.apply:
                repair_path = (
                    ROOT_DIR
                    / "output"
                    / qualification
                    / "questions_json"
                    / "firestore_repairs"
                    / timestamp
                    / "suggestion_normalization.json"
                )
                write_json(repair_path, patch)
                rollback_path = repair_path.with_name("suggestion_rollback.json")
                write_json(rollback_path, rollback)
                qualification_receipt["suggestionPatchPath"] = str(
                    repair_path.relative_to(ROOT_DIR)
                )
                qualification_receipt["suggestionRollbackPath"] = str(
                    rollback_path.relative_to(ROOT_DIR)
                )
            routing_patch = build_routing_patch(
                canonical_questions=all_questions,
                snapshot_dir=snapshot_dir,
            )
            routing_rollback = build_routing_rollback(
                canonical_questions=all_questions,
                snapshot_dir=snapshot_dir,
            )
            if routing_patch["total_count"] != routing_rollback["total_count"]:
                raise ValueError("配信経路patchとrollbackの件数が一致しません。")
            qualification_receipt["routingPatchCount"] = routing_patch["total_count"]
            if args.apply:
                routing_path = repair_path.with_name("routing_normalization.json")
                write_json(routing_path, routing_patch)
                routing_rollback_path = repair_path.with_name("routing_rollback.json")
                write_json(routing_rollback_path, routing_rollback)
                qualification_receipt["routingPatchPath"] = str(
                    routing_path.relative_to(ROOT_DIR)
                )
                qualification_receipt["routingRollbackPath"] = str(
                    routing_rollback_path.relative_to(ROOT_DIR)
                )
        if args.apply:
            upload_path = (
                ROOT_DIR
                / "output"
                / qualification
                / "questions_json"
                / "upload_to_firestore"
                / f"all_years_firestore_{timestamp}.json"
            )
            write_json(
                upload_path,
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "questions": all_questions,
                    "total_count": len(all_questions),
                },
            )
            qualification_receipt["uploadArtifactPath"] = str(
                upload_path.relative_to(ROOT_DIR)
            )
            qualification_receipt["uploadArtifactSha256"] = sha256(upload_path)

    receipt["activeDisplayTotal"] = sum(EXPECTED_COUNTS.values())
    receipt["status"] = "pass"
    receipt_path = args.receipt
    if receipt_path is None:
        receipt_path = (
            ROOT_DIR
            / "docs/goals/gas-shunin-missing-basic-explanations-firestore/notes"
            / "T035-canonical-local-rebuild-receipt.json"
        )
    write_json(receipt_path.resolve(), receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
