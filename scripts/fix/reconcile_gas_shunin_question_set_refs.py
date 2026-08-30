#!/usr/bin/env python3
"""ガス主任技術者のlegacy questionSetRefを安全に省略形へ収束させる。"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.export.export_firestore_gas_shunin_kou_snapshot import (  # noqa: E402
    decode_firestore_document,
    get_access_token,
)
from scripts.upload.firebase_credentials import DEFAULT_PROJECT_ID  # noqa: E402


SCHEMA_VERSION = "gas-shunin-question-set-ref-reconcile/v1"
PROTECTED_COMPONENTS = {"00_source", "old", "firestore_snapshot"}
MALFORMED_REF_RE = re.compile(
    r"^projects/undefined/databases/\(default\)/documents/questionSets/([^/]+)$"
)
MALFORMED_REF_LINE_RE = re.compile(
    r'^\s*"questionSetRef": "projects/undefined/databases/\(default\)/documents/'
    r'questionSets/[^\"]+",\s*$'
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "path"):
        return str(value.path)
    return value


def canonical_json_hash(value: Any) -> str:
    encoded = json.dumps(
        json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def resolve_portable_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def payload_hash(payload: dict[str, Any], hash_field: str = "planSha256") -> str:
    body = {key: value for key, value in payload.items() if key != hash_field}
    return canonical_json_hash(body)


def verify_plan_hash(plan: dict[str, Any]) -> None:
    expected = str(plan.get("planSha256") or "")
    actual = payload_hash(plan)
    if not expected or expected != actual:
        raise ValueError(f"plan hash mismatch: expected={expected} actual={actual}")


def raw_documents(snapshot_dir: Path, filename: str) -> dict[str, dict[str, Any]]:
    payload = load_json(snapshot_dir / "raw" / filename)
    return {str(item["_id"]): item for item in payload.get("documents", [])}


def active_questions(snapshot_dir: Path) -> list[dict[str, Any]]:
    payload = load_json(snapshot_dir / "reconstructed" / "questions.json")
    return [
        item
        for item in payload.get("questions", [])
        if item.get("isDeleted") is False and item.get("isChoiceOnly") is False
    ]


def without_question_set_ref(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: without_question_set_ref(item)
            for key, item in value.items()
            if key != "questionSetRef"
        }
    if isinstance(value, list):
        return [without_question_set_ref(item) for item in value]
    return value


def remove_and_validate_local_refs(value: Any) -> tuple[Any, int]:
    removed = 0

    def visit(item: Any) -> Any:
        nonlocal removed
        if isinstance(item, list):
            return [visit(child) for child in item]
        if not isinstance(item, dict):
            return item
        result: dict[str, Any] = {}
        for key, child in item.items():
            if key != "questionSetRef":
                result[key] = visit(child)
                continue
            reference = str(child or "")
            match = MALFORMED_REF_RE.fullmatch(reference)
            question_set_id = str(item.get("questionSetId") or "")
            if match is None:
                raise ValueError(f"unexpected local questionSetRef: {reference}")
            if not question_set_id or match.group(1) != question_set_id:
                raise ValueError(
                    f"local questionSetRef/questionSetId mismatch: {reference} != {question_set_id}"
                )
            removed += 1
        return result

    return visit(value), removed


def protected_tree_manifest(local_roots: Iterable[Path]) -> dict[str, Any]:
    rows: list[tuple[str, str]] = []
    for local_root in sorted(path.resolve() for path in local_roots):
        for path in sorted(local_root.rglob("*.json")):
            if not (set(path.parts) & PROTECTED_COMPONENTS):
                continue
            rows.append((portable_path(path), file_hash(path)))
    return {
        "fileCount": len(rows),
        "treeSha256": canonical_json_hash(rows),
    }


def build_plan(
    *,
    kou_snapshot: Path,
    otsu_snapshot: Path,
    local_roots: list[Path],
) -> dict[str, Any]:
    snapshots = {"kou": kou_snapshot.resolve(), "otsu": otsu_snapshot.resolve()}
    targets: list[dict[str, Any]] = []
    omitted_ids: list[str] = []
    by_grade: dict[str, dict[str, int]] = {}

    for grade, snapshot_dir in snapshots.items():
        questions = active_questions(snapshot_dir)
        raw_by_id = raw_documents(snapshot_dir, "questions.json")
        parent_ids = set(raw_documents(snapshot_dir, "questionSets.json"))
        target_count = 0
        omitted_count = 0
        for question in questions:
            question_id = str(question["questionId"])
            question_set_id = str(question.get("questionSetId") or "")
            if not question_set_id:
                raise ValueError(f"questionSetId missing: {question_id}")
            if question_set_id not in parent_ids:
                raise ValueError(f"questionSet parent missing: {question_id} -> {question_set_id}")
            reference = str(question.get("questionSetRef") or "")
            if not reference:
                omitted_ids.append(question_id)
                omitted_count += 1
                continue
            match = MALFORMED_REF_RE.fullmatch(reference)
            if match is None:
                raise ValueError(f"unexpected active questionSetRef: {question_id}: {reference}")
            if match.group(1) != question_set_id:
                raise ValueError(
                    f"questionSetRef/questionSetId mismatch: {question_id}: "
                    f"{match.group(1)} != {question_set_id}"
                )
            raw = raw_by_id.get(question_id)
            if raw is None:
                raise ValueError(f"raw question missing: {question_id}")
            decoded = raw.get("decoded") or {}
            if str(decoded.get("questionSetRef") or "") != reference:
                raise ValueError(f"raw/reconstructed questionSetRef mismatch: {question_id}")
            targets.append(
                {
                    "grade": grade,
                    "questionId": question_id,
                    "questionSetId": question_set_id,
                    "beforeReferenceValue": reference,
                    "snapshotUpdateTime": raw.get("updateTime"),
                    "protectedDocumentSha256": canonical_json_hash(
                        without_question_set_ref(decoded)
                    ),
                }
            )
            target_count += 1
        by_grade[grade] = {
            "activeDisplayQuestionCount": len(questions),
            "deleteFieldTargetCount": target_count,
            "alreadyOmittedCount": omitted_count,
        }

    local_targets: list[dict[str, Any]] = []
    local_ref_count = 0
    for local_root in sorted(path.resolve() for path in local_roots):
        for path in sorted(local_root.rglob("*.json")):
            display_path = portable_path(path)
            if set(path.parts) & PROTECTED_COMPONENTS:
                continue
            text = path.read_text(encoding="utf-8")
            if '"questionSetRef"' not in text:
                continue
            before_payload = json.loads(text)
            after_payload, removed_count = remove_and_validate_local_refs(before_payload)
            if removed_count <= 0:
                raise ValueError(f"questionSetRef marker was not removable: {display_path}")
            after_text = "\n".join(
                line for line in text.splitlines() if not MALFORMED_REF_LINE_RE.fullmatch(line)
            ) + ("\n" if text.endswith("\n") else "")
            if json.loads(after_text) != after_payload:
                raise ValueError(f"minimal local rewrite does not match parsed result: {display_path}")
            local_targets.append(
                {
                    "path": display_path,
                    "beforeSha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "afterSha256": hashlib.sha256(after_text.encode("utf-8")).hexdigest(),
                    "protectedPayloadSha256": canonical_json_hash(after_payload),
                    "removedReferenceCount": removed_count,
                }
            )
            local_ref_count += removed_count

    target_ids = [item["questionId"] for item in targets]
    if len(target_ids) != len(set(target_ids)):
        raise ValueError("Firestore target IDs are duplicated")
    protected_manifest = protected_tree_manifest(local_roots)
    plan: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "projectId": DEFAULT_PROJECT_ID,
        "scope": "gas-shunin active display questions and current derived local JSON",
        "sources": {key: str(value) for key, value in snapshots.items()},
        "localRoots": [str(path.resolve()) for path in local_roots],
        "summary": {
            "byGrade": by_grade,
            "activeDisplayQuestionCount": sum(
                item["activeDisplayQuestionCount"] for item in by_grade.values()
            ),
            "deleteFieldTargetCount": len(targets),
            "alreadyOmittedCount": len(omitted_ids),
            "localTargetFileCount": len(local_targets),
            "localRemovedReferenceCount": local_ref_count,
            "questionSetIdMismatchCount": 0,
            "missingParentCount": 0,
        },
        "targets": sorted(targets, key=lambda item: item["questionId"]),
        "alreadyOmittedQuestionIds": sorted(omitted_ids),
        "localTargets": local_targets,
        "protectedLocalTree": protected_manifest,
        "recovery": {
            "operation": "restore beforeReferenceValue only if an independently reviewed rollback requires it",
            "targetCount": len(targets),
            "oldReferenceValuesRecorded": True,
        },
    }
    plan["planSha256"] = payload_hash(plan)
    return plan


def apply_local(*, plan: dict[str, Any]) -> dict[str, Any]:
    verify_plan_hash(plan)
    local_roots = [Path(item) for item in plan.get("localRoots", [])]
    before_protected = protected_tree_manifest(local_roots)
    if before_protected != plan.get("protectedLocalTree"):
        raise RuntimeError("protected local tree changed after plan creation")
    changed_files: list[str] = []
    removed_total = 0
    for target in plan.get("localTargets", []):
        path = resolve_portable_path(str(target["path"]))
        text = path.read_text(encoding="utf-8")
        current_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if current_hash == target["afterSha256"]:
            continue
        if current_hash != target["beforeSha256"]:
            raise RuntimeError(f"local precondition mismatch: {target['path']}")
        after_text = "\n".join(
            line for line in text.splitlines() if not MALFORMED_REF_LINE_RE.fullmatch(line)
        ) + ("\n" if text.endswith("\n") else "")
        payload = json.loads(after_text)
        if hashlib.sha256(after_text.encode("utf-8")).hexdigest() != target["afterSha256"]:
            raise RuntimeError(f"local postcondition hash mismatch: {target['path']}")
        if canonical_json_hash(payload) != target["protectedPayloadSha256"]:
            raise RuntimeError(f"local protected payload mismatch: {target['path']}")
        path.write_text(after_text, encoding="utf-8")
        changed_files.append(str(target["path"]))
        removed_total += int(target["removedReferenceCount"])
    after_protected = protected_tree_manifest(local_roots)
    if after_protected != before_protected:
        raise RuntimeError("protected local tree changed during local apply")
    return {
        "schemaVersion": f"{SCHEMA_VERSION}/local-apply-receipt",
        "generatedAt": utc_now(),
        "planSha256": plan["planSha256"],
        "targetFileCount": len(plan.get("localTargets", [])),
        "changedFileCount": len(changed_files),
        "alreadyAppliedFileCount": len(plan.get("localTargets", [])) - len(changed_files),
        "removedReferenceCount": removed_total,
        "remainingCurrentDerivedReferenceCount": 0,
        "protectedLocalTree": after_protected,
        "changedFiles": changed_files,
        "errors": [],
    }


def chunked(values: list[Any], size: int = 400) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def request_firestore_endpoint(
    *,
    project_id: str,
    token: str,
    endpoint: str,
    payload: dict[str, Any],
) -> Any:
    url = (
        "https://firestore.googleapis.com/v1/projects/"
        f"{project_id}/databases/(default)/documents:{endpoint}"
    )
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Firestore {endpoint} error: {exc.code} {exc.reason}: {error_body}"
        ) from exc
    return json.loads(body) if body else {}


def batch_get_raw_documents(
    *,
    project_id: str,
    token: str,
    document_names: list[str],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for names in chunked(document_names):
        rows = request_firestore_endpoint(
            project_id=project_id,
            token=token,
            endpoint="batchGet",
            payload={"documents": names},
        )
        if not isinstance(rows, list):
            raise RuntimeError("Firestore batchGet response is not a list")
        for row in rows:
            document = row.get("found") if isinstance(row, dict) else None
            if not isinstance(document, dict):
                continue
            result[str(document["name"])] = document
    return result


def apply_firestore(
    *,
    plan: dict[str, Any],
    local_receipt: dict[str, Any],
    project_id: str,
    credentials_json: Path | None,
) -> dict[str, Any]:
    verify_plan_hash(plan)
    if local_receipt.get("planSha256") != plan["planSha256"]:
        raise ValueError("local receipt does not belong to plan")
    if local_receipt.get("errors") or int(local_receipt.get("remainingCurrentDerivedReferenceCount", -1)) != 0:
        raise ValueError("local reconciliation is not complete")
    token = get_access_token(credentials_json)
    targets = list(plan.get("targets", []))
    parent_ids = sorted({str(item["questionSetId"]) for item in targets})
    parent_names = [
        f"projects/{project_id}/databases/(default)/documents/questionSets/{item}"
        for item in parent_ids
    ]
    parent_documents = batch_get_raw_documents(
        project_id=project_id,
        token=token,
        document_names=parent_names,
    )
    missing_parents = [
        question_set_id
        for question_set_id, name in zip(parent_ids, parent_names, strict=True)
        if name not in parent_documents
    ]
    if missing_parents:
        raise RuntimeError(f"live questionSet parents missing: {missing_parents[:10]}")
    question_names = [
        f"projects/{project_id}/databases/(default)/documents/questions/{item['questionId']}"
        for item in targets
    ]
    raw_documents = batch_get_raw_documents(
        project_id=project_id,
        token=token,
        document_names=question_names,
    )
    pending: list[tuple[dict[str, Any], dict[str, Any]]] = []
    before_hashes: dict[str, str] = {}
    for target, name in zip(targets, question_names, strict=True):
        question_id = str(target["questionId"])
        raw_document = raw_documents.get(name)
        if raw_document is None:
            raise RuntimeError(f"Firestore question not found: {question_id}")
        document = decode_firestore_document(raw_document)
        current_hash = canonical_json_hash(without_question_set_ref(document))
        before_hashes[question_id] = current_hash
        reference = document.get("questionSetRef")
        if reference is None:
            raise RuntimeError(f"planned questionSetRef is already absent: {question_id}")
        if document.get("isDeleted") is not False or document.get("isChoiceOnly") is not False:
            raise RuntimeError(f"active display precondition changed: {question_id}")
        if str(document.get("questionSetId") or "") != target["questionSetId"]:
            raise RuntimeError(f"questionSetId changed: {question_id}")
        match = MALFORMED_REF_RE.fullmatch(str(reference))
        if match is None or match.group(1) != target["questionSetId"]:
            raise RuntimeError(f"live questionSetRef id mismatch: {question_id}")
        if str(raw_document.get("updateTime") or "") != str(target["snapshotUpdateTime"]):
            raise RuntimeError(f"Firestore updateTime precondition mismatch: {question_id}")
        if current_hash != target["protectedDocumentSha256"]:
            raise RuntimeError(f"Firestore protected fields changed: {question_id}")
        pending.append((target, raw_document))

    written: list[str] = []
    for batch_items in chunked(pending):
        writes = []
        for target, raw_document in batch_items:
            writes.append(
                {
                    "update": {
                        "name": raw_document["name"],
                        "fields": {},
                    },
                    "updateMask": {"fieldPaths": ["questionSetRef"]},
                    "currentDocument": {"updateTime": raw_document["updateTime"]},
                }
            )
        response = request_firestore_endpoint(
            project_id=project_id,
            token=token,
            endpoint="commit",
            payload={"writes": writes},
        )
        if len(response.get("writeResults", [])) != len(writes):
            raise RuntimeError("Firestore commit write result count mismatch")
        written.extend(str(target["questionId"]) for target, _ in batch_items)

    readback = batch_get_raw_documents(
        project_id=project_id,
        token=token,
        document_names=question_names,
    )
    failed: list[str] = []
    protected_mismatches: list[str] = []
    for target, name in zip(targets, question_names, strict=True):
        question_id = str(target["questionId"])
        raw_document = readback.get(name)
        if raw_document is None:
            failed.append(question_id)
            continue
        document = decode_firestore_document(raw_document)
        if "questionSetRef" in document:
            failed.append(question_id)
        if canonical_json_hash(without_question_set_ref(document)) != before_hashes[question_id]:
            protected_mismatches.append(question_id)
    if failed or protected_mismatches:
        raise RuntimeError(
            f"Firestore readback failed: remaining={failed[:10]} protected={protected_mismatches[:10]}"
        )
    return {
        "schemaVersion": f"{SCHEMA_VERSION}/firestore-apply-receipt",
        "generatedAt": utc_now(),
        "planSha256": plan["planSha256"],
        "projectId": project_id,
        "targetCount": len(targets),
        "writtenCount": len(written),
        "alreadyAppliedCount": 0,
        "readbackAbsentCount": len(targets),
        "protectedFieldMismatchCount": len(protected_mismatches),
        "errors": failed,
        "writtenQuestionIds": sorted(written),
        "alreadyAppliedQuestionIds": [],
        "recovery": copy.deepcopy(plan.get("recovery")),
    }


def verify_post_snapshots(
    *,
    plan: dict[str, Any],
    local_receipt: dict[str, Any],
    firestore_receipt: dict[str, Any],
    kou_post_snapshot: Path,
    otsu_post_snapshot: Path,
) -> dict[str, Any]:
    verify_plan_hash(plan)
    for receipt_name, receipt in (
        ("local", local_receipt),
        ("firestore", firestore_receipt),
    ):
        if receipt.get("planSha256") != plan["planSha256"]:
            raise ValueError(f"{receipt_name} receipt does not belong to plan")
        if receipt.get("errors"):
            raise ValueError(f"{receipt_name} receipt contains errors")

    pre_snapshots = {key: Path(value) for key, value in plan["sources"].items()}
    post_snapshots = {
        "kou": kou_post_snapshot.resolve(),
        "otsu": otsu_post_snapshot.resolve(),
    }
    target_ids = {str(item["questionId"]) for item in plan.get("targets", [])}
    omitted_ids = set(map(str, plan.get("alreadyOmittedQuestionIds", [])))
    target_verified = 0
    omitted_unchanged = 0
    other_unchanged = 0
    active_post_ids: set[str] = set()
    active_with_ref: list[str] = []

    for grade in ("kou", "otsu"):
        pre_snapshot = pre_snapshots[grade]
        post_snapshot = post_snapshots[grade]
        pre_questions = raw_documents(pre_snapshot, "questions.json")
        post_questions = raw_documents(post_snapshot, "questions.json")
        if set(pre_questions) != set(post_questions):
            raise RuntimeError(f"raw question ID set changed: {grade}")
        pre_active_ids = {str(item["questionId"]) for item in active_questions(pre_snapshot)}
        post_active = active_questions(post_snapshot)
        post_active_ids = {str(item["questionId"]) for item in post_active}
        if pre_active_ids != post_active_ids:
            raise RuntimeError(f"active display question ID set changed: {grade}")
        active_post_ids.update(post_active_ids)
        active_with_ref.extend(
            str(item["questionId"])
            for item in post_active
            if "questionSetRef" in item
        )

        for question_id, before in pre_questions.items():
            after = post_questions[question_id]
            if question_id in target_ids:
                expected_fields = copy.deepcopy(before.get("fields") or {})
                expected_fields.pop("questionSetRef", None)
                expected_decoded = copy.deepcopy(before.get("decoded") or {})
                expected_decoded.pop("questionSetRef", None)
                if after.get("fields") != expected_fields:
                    raise RuntimeError(f"target Firestore fields changed unexpectedly: {question_id}")
                if after.get("decoded") != expected_decoded:
                    raise RuntimeError(f"target decoded fields changed unexpectedly: {question_id}")
                for field in ("name", "createTime"):
                    if after.get(field) != before.get(field):
                        raise RuntimeError(f"target {field} changed unexpectedly: {question_id}")
                if after.get("updateTime") == before.get("updateTime"):
                    raise RuntimeError(f"target updateTime did not advance: {question_id}")
                target_verified += 1
            else:
                if after != before:
                    raise RuntimeError(f"non-target question changed: {question_id}")
                if question_id in omitted_ids:
                    omitted_unchanged += 1
                else:
                    other_unchanged += 1

        for filename in ("questionSets.json", "folders.json"):
            before_documents = raw_documents(pre_snapshot, filename)
            after_documents = raw_documents(post_snapshot, filename)
            if after_documents != before_documents:
                raise RuntimeError(f"non-target collection changed: {grade}/{filename}")

    expected_active_ids = target_ids | omitted_ids
    if active_post_ids != expected_active_ids:
        raise RuntimeError("post active IDs do not equal target plus already-omitted IDs")
    if active_with_ref:
        raise RuntimeError(f"active questionSetRef remains: {active_with_ref[:10]}")

    for target in plan.get("localTargets", []):
        path = resolve_portable_path(str(target["path"]))
        if file_hash(path) != target["afterSha256"]:
            raise RuntimeError(f"local target post hash mismatch: {target['path']}")
    protected_manifest = protected_tree_manifest(
        [Path(item) for item in plan.get("localRoots", [])]
    )
    if protected_manifest != plan.get("protectedLocalTree"):
        raise RuntimeError("protected local tree changed after Firestore apply")

    return {
        "schemaVersion": f"{SCHEMA_VERSION}/post-verification-receipt",
        "generatedAt": utc_now(),
        "planSha256": plan["planSha256"],
        "postSnapshots": {key: str(value) for key, value in post_snapshots.items()},
        "activeDisplayQuestionCount": len(active_post_ids),
        "targetFieldDeletionVerifiedCount": target_verified,
        "alreadyOmittedUnchangedCount": omitted_unchanged,
        "otherQuestionUnchangedCount": other_unchanged,
        "activeQuestionSetRefCount": len(active_with_ref),
        "questionSetAndFolderDocumentMismatchCount": 0,
        "localTargetFileCount": len(plan.get("localTargets", [])),
        "localRemainingReferenceCount": 0,
        "protectedLocalTree": protected_manifest,
        "errors": [],
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    result.add_argument("--credentials-json", type=Path)
    subparsers = result.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build-plan")
    build.add_argument("--kou-snapshot", type=Path, required=True)
    build.add_argument("--otsu-snapshot", type=Path, required=True)
    build.add_argument("--local-root", type=Path, action="append", required=True)
    build.add_argument("--output", type=Path, required=True)

    local = subparsers.add_parser("apply-local")
    local.add_argument("--plan", type=Path, required=True)
    local.add_argument("--receipt", type=Path, required=True)

    remote = subparsers.add_parser("apply-firestore")
    remote.add_argument("--plan", type=Path, required=True)
    remote.add_argument("--local-receipt", type=Path, required=True)
    remote.add_argument("--receipt", type=Path, required=True)

    verify = subparsers.add_parser("verify-post")
    verify.add_argument("--plan", type=Path, required=True)
    verify.add_argument("--local-receipt", type=Path, required=True)
    verify.add_argument("--firestore-receipt", type=Path, required=True)
    verify.add_argument("--kou-post-snapshot", type=Path, required=True)
    verify.add_argument("--otsu-post-snapshot", type=Path, required=True)
    verify.add_argument("--receipt", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "build-plan":
        plan = build_plan(
            kou_snapshot=args.kou_snapshot,
            otsu_snapshot=args.otsu_snapshot,
            local_roots=args.local_root,
        )
        write_json(args.output, plan)
        print(json.dumps(plan["summary"], ensure_ascii=False, indent=2))
        return 0
    if args.command == "apply-local":
        plan = load_json(args.plan)
        receipt = apply_local(plan=plan)
        write_json(args.receipt, receipt)
        print(json.dumps({key: value for key, value in receipt.items() if key != "changedFiles"}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "apply-firestore":
        plan = load_json(args.plan)
        receipt = apply_firestore(
            plan=plan,
            local_receipt=load_json(args.local_receipt),
            project_id=args.project_id,
            credentials_json=args.credentials_json,
        )
        write_json(args.receipt, receipt)
        print(json.dumps({key: value for key, value in receipt.items() if not key.endswith("Ids")}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "verify-post":
        receipt = verify_post_snapshots(
            plan=load_json(args.plan),
            local_receipt=load_json(args.local_receipt),
            firestore_receipt=load_json(args.firestore_receipt),
            kou_post_snapshot=args.kou_post_snapshot,
            otsu_post_snapshot=args.otsu_post_snapshot,
        )
        write_json(args.receipt, receipt)
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
