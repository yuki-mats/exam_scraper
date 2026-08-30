#!/usr/bin/env python3
"""ガス主任技術者の重複問題を論理削除し、集計件数を実数へ合わせる。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.upload.firebase_credentials import (  # noqa: E402
    DEFAULT_PROJECT_ID,
    initialize_firebase_app,
)


SCHEMA_VERSION = "gas-shunin-firestore-duplicate-reconcile/v1"
COUNT_SCHEMA_VERSION = "gas-shunin-firestore-count-reconcile/v1"
UPDATED_BY_ID = "aMpBCmAEGSQPbhUMzbHvFiM1cYK2"
BATCH_SIZE = 450
QUESTION_PRECONDITION_FIELDS = (
    "examYear",
    "originalQuestionBodyText",
    "originalQuestionChoiceText",
    "questionText",
    "questionType",
    "correctChoiceText",
    "questionSetId",
    "isDeleted",
    "isChoiceOnly",
)
COUNT_PRECONDITION_FIELDS = (
    "questionCount",
    "folderId",
    "qualificationId",
    "isDeleted",
)
Q21_PRECONDITION_FIELDS = (
    "originalQuestionBodyText",
    "originalQuestionChoiceText",
    "questionText",
    "correctChoiceText",
    "explanationText",
    "knowledgeText",
    "suggestedQuestions",
    "suggestedQuestionDetails",
    "questionSetId",
    "questionType",
    "isDeleted",
    "isChoiceOnly",
)
Q21_PREFIX = "gas-shunin-otsu-2020-shohi-q21-"
Q22_PREFIX = "gas-shunin-otsu-2020-shohi-q22-"
LEGACY_Q22_PREFIX = "gasushunin-otsushu-gizyutsu-2020-22-"


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


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", "", text).replace("−", "-").replace("‐", "-").replace("―", "-")


def normalize_choice(value: Any) -> str:
    text = normalize_text(value).replace("～", "~").replace("〜", "~")
    try:
        return f"number:{Decimal(text.replace(',', '')).normalize()}"
    except InvalidOperation:
        return re.sub(r"[。．、，,.・「」『』（）()【】]", "", text)


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


def selected_fields(document: dict[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    return {field: json_safe(document.get(field)) for field in fields}


def fingerprint(document: dict[str, Any], fields: Iterable[str]) -> str:
    encoded = json.dumps(
        selected_fields(document, fields),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def payload_hash(payload: dict[str, Any], hash_field: str) -> str:
    body = {key: value for key, value in payload.items() if key != hash_field}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def active_display_questions(snapshot_dir: Path) -> list[dict[str, Any]]:
    payload = load_json(snapshot_dir / "reconstructed" / "questions.json")
    return [
        item
        for item in payload.get("questions", [])
        if item.get("isDeleted") is False and item.get("isChoiceOnly") is False
    ]


def raw_documents(snapshot_dir: Path, collection_file: str) -> dict[str, dict[str, Any]]:
    payload = load_json(snapshot_dir / "raw" / collection_file)
    return {str(item["_id"]): item for item in payload.get("documents", [])}


class UnionFind:
    def __init__(self, values: Iterable[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def content_duplicate_groups(questions: list[dict[str, Any]]) -> list[list[str]]:
    by_id = {str(item["questionId"]): item for item in questions}
    union_find = UnionFind(by_id)
    indexes: list[dict[tuple[Any, ...], list[str]]] = [defaultdict(list), defaultdict(list)]
    for question_id, question in by_id.items():
        year = int(question.get("examYear") or 0)
        choice = normalize_choice(question.get("originalQuestionChoiceText"))
        if not choice:
            continue
        body = normalize_text(question.get("originalQuestionBodyText"))
        text = normalize_text(question.get("questionText"))
        if body:
            indexes[0][(year, body, choice)].append(question_id)
        if text:
            indexes[1][(year, text, choice)].append(question_id)
    for index in indexes:
        for question_ids in index.values():
            for question_id in question_ids[1:]:
                union_find.union(question_ids[0], question_id)
    groups: dict[str, list[str]] = defaultdict(list)
    for question_id in by_id:
        groups[union_find.find(question_id)].append(question_id)
    return sorted(
        (sorted(group) for group in groups.values() if len(group) > 1),
        key=lambda group: tuple(group),
    )


def direct_content_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if int(left.get("examYear") or 0) != int(right.get("examYear") or 0):
        return False
    if normalize_choice(left.get("originalQuestionChoiceText")) != normalize_choice(
        right.get("originalQuestionChoiceText")
    ):
        return False
    body_match = normalize_text(left.get("originalQuestionBodyText")) == normalize_text(
        right.get("originalQuestionBodyText")
    )
    text_match = normalize_text(left.get("questionText")) == normalize_text(right.get("questionText"))
    return bool(body_match or text_match)


def classify_group(
    grade: str,
    group: list[str],
    questions_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if grade == "kou":
        canonical = [item for item in group if item.startswith("chiefgasengineerlicense-A-")]
        shadows = [item for item in group if "site-shadow" in item]
        if len(canonical) == 1 and sorted(canonical + shadows) == sorted(group):
            return {
                "status": "apply",
                "keepIds": canonical,
                "softDeleteIds": shadows,
                "reason": "primary_firestore_id_over_site_shadow",
            }
        return {"status": "hold", "keepIds": [], "softDeleteIds": [], "reason": "kou_canonical_ambiguous"}

    canonical = [item for item in group if item.startswith("gas-shunin-otsu-")]
    legacy = [item for item in group if item.startswith("gasushunin-")]
    legacy_c = [item for item in group if item.startswith("chiefgasengineerlicense-C-")]
    has_q21 = any(item.startswith(Q21_PREFIX) for item in canonical)
    has_q22 = any(item.startswith(Q22_PREFIX) for item in canonical)
    if has_q21 and has_q22:
        delete_ids = [item for item in legacy if item.startswith(LEGACY_Q22_PREFIX)]
        return {
            "status": "apply" if delete_ids else "distinct_only",
            "keepIds": canonical,
            "softDeleteIds": delete_ids,
            "reason": "official_q21_and_q22_are_distinct_q21_content_requires_repair",
            "contentRepairIds": sorted(item for item in canonical if item.startswith(Q21_PREFIX)),
        }
    if len(canonical) == 1 and len(canonical) + len(legacy) + len(legacy_c) == len(group):
        return {
            "status": "apply",
            "keepIds": canonical,
            "softDeleteIds": sorted(legacy + legacy_c),
            "reason": "canonical_publication_id_over_legacy_id",
        }
    if not canonical and len(group) == 2:
        technology = [item for item in group if item.startswith("gasushunin-otsushu-gizyutsu-")]
        law = [item for item in group if item.startswith("gasushunin-otsushu-hourei-")]
        if len(technology) == 1 and len(law) == 1:
            return {
                "status": "apply",
                "keepIds": technology,
                "softDeleteIds": law,
                "reason": "technology_question_misregistered_under_law_id",
            }
    return {"status": "hold", "keepIds": [], "softDeleteIds": [], "reason": "otsu_canonical_ambiguous"}


def build_duplicate_plan(
    *,
    kou_snapshot: Path,
    otsu_snapshot: Path,
) -> dict[str, Any]:
    sources = {"kou": kou_snapshot.resolve(), "otsu": otsu_snapshot.resolve()}
    plan_groups: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    repair_ids: set[str] = set()
    by_grade: dict[str, dict[str, int]] = {}

    for grade, snapshot_dir in sources.items():
        questions = active_display_questions(snapshot_dir)
        questions_by_id = {str(item["questionId"]): item for item in questions}
        raw_by_id = raw_documents(snapshot_dir, "questions.json")
        groups = content_duplicate_groups(questions)
        grade_target_count = 0
        grade_apply_group_count = 0
        grade_hold_count = 0
        for group in groups:
            decision = classify_group(grade, group, questions_by_id)
            keep_ids = decision.get("keepIds", [])
            delete_ids = decision.get("softDeleteIds", [])
            for delete_id in delete_ids:
                if not any(
                    direct_content_match(questions_by_id[delete_id], questions_by_id[keep_id])
                    for keep_id in keep_ids
                ):
                    raise ValueError(f"delete target has no direct content match: {delete_id}")
            if decision["status"] == "hold":
                grade_hold_count += 1
            elif delete_ids:
                grade_apply_group_count += 1
            repair_ids.update(decision.get("contentRepairIds", []))
            group_record = {
                "grade": grade,
                "groupId": hashlib.sha256("\n".join(group).encode("utf-8")).hexdigest()[:20],
                "memberIds": group,
                **decision,
            }
            plan_groups.append(group_record)
            for delete_id in delete_ids:
                raw = raw_by_id.get(delete_id)
                if raw is None:
                    raise ValueError(f"raw snapshot record not found: {delete_id}")
                document = raw.get("decoded") or {}
                targets.append(
                    {
                        "grade": grade,
                        "questionId": delete_id,
                        "keepIds": keep_ids,
                        "reason": decision["reason"],
                        "precondition": selected_fields(document, QUESTION_PRECONDITION_FIELDS),
                        "preconditionSha256": fingerprint(document, QUESTION_PRECONDITION_FIELDS),
                        "snapshotUpdateTime": raw.get("updateTime"),
                    }
                )
                grade_target_count += 1
        by_grade[grade] = {
            "activeDisplayQuestionCountBefore": len(questions),
            "contentIdentityGroupCount": len(groups),
            "contentIdentityExcessCount": sum(len(group) - 1 for group in groups),
            "duplicateApplyGroupCount": grade_apply_group_count,
            "softDeleteTargetCount": grade_target_count,
            "holdGroupCount": grade_hold_count,
        }

    target_ids = [item["questionId"] for item in targets]
    if len(target_ids) != len(set(target_ids)):
        raise ValueError("soft-delete target IDs are duplicated")
    plan: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "mode": "soft_delete_only",
        "scope": "gas-shunin active display questions: isDeleted=false and isChoiceOnly=false",
        "sources": {key: str(value) for key, value in sources.items()},
        "summary": {
            "byGrade": by_grade,
            "contentIdentityGroupCount": sum(item["contentIdentityGroupCount"] for item in by_grade.values()),
            "duplicateApplyGroupCount": sum(item["duplicateApplyGroupCount"] for item in by_grade.values()),
            "softDeleteTargetCount": len(targets),
            "projectedActiveDisplayQuestionCount": sum(
                item["activeDisplayQuestionCountBefore"] for item in by_grade.values()
            )
            - len(targets),
            "holdGroupCount": sum(item["holdGroupCount"] for item in by_grade.values()),
            "contentRepairRequiredCount": len(repair_ids),
        },
        "contentRepairRequiredQuestionIds": sorted(repair_ids),
        "groups": plan_groups,
        "targets": sorted(targets, key=lambda item: item["questionId"]),
        "recovery": {
            "operation": "set isDeleted=false for target IDs after independent review",
            "targetIds": sorted(target_ids),
        },
    }
    plan["planSha256"] = payload_hash(plan, "planSha256")
    return plan


def verify_plan_hash(plan: dict[str, Any], field: str = "planSha256") -> None:
    expected = str(plan.get(field) or "")
    actual = payload_hash(plan, field)
    if not expected or expected != actual:
        raise ValueError(f"plan hash mismatch: expected={expected} actual={actual}")


def firestore_client(project_id: str, credentials_json: Path | None):
    initialize_firebase_app(project_id=project_id, credentials_json=credentials_json)
    from firebase_admin import firestore

    return firestore.client(), firestore


def chunked(values: list[Any], size: int = BATCH_SIZE) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def apply_soft_delete(
    *,
    plan: dict[str, Any],
    project_id: str,
    credentials_json: Path | None,
) -> dict[str, Any]:
    verify_plan_hash(plan)
    db, firestore = firestore_client(project_id, credentials_json)
    targets = list(plan.get("targets", []))
    refs = [db.collection("questions").document(item["questionId"]) for item in targets]
    snapshots = {
        snapshot.id: snapshot
        for snapshot in db.get_all(refs, field_paths=list(QUESTION_PRECONDITION_FIELDS))
    }
    pending: list[tuple[dict[str, Any], Any]] = []
    already_applied: list[str] = []
    for target in targets:
        question_id = target["questionId"]
        snapshot = snapshots.get(question_id)
        if snapshot is None or not snapshot.exists:
            raise RuntimeError(f"Firestore question not found: {question_id}")
        document = snapshot.to_dict() or {}
        current = fingerprint(document, QUESTION_PRECONDITION_FIELDS)
        if current == target["preconditionSha256"]:
            pending.append((target, snapshot))
            continue
        expected_without_deleted = dict(target["precondition"])
        expected_without_deleted["isDeleted"] = True
        if selected_fields(document, QUESTION_PRECONDITION_FIELDS) == expected_without_deleted:
            already_applied.append(question_id)
            continue
        raise RuntimeError(f"Firestore precondition mismatch: {question_id}")

    updated_at = datetime.now(timezone.utc)
    written: list[str] = []
    for batch_items in chunked(pending):
        batch = db.batch()
        for target, snapshot in batch_items:
            batch.update(
                snapshot.reference,
                {"isDeleted": True, "updatedAt": updated_at, "updatedById": UPDATED_BY_ID},
                option=firestore.LastUpdateOption(snapshot.update_time),
            )
        batch.commit()
        written.extend(target["questionId"] for target, _ in batch_items)

    readback = {
        snapshot.id: snapshot
        for snapshot in db.get_all(refs, field_paths=["isDeleted"])
    }
    failed = sorted(
        question_id
        for question_id, snapshot in readback.items()
        if not snapshot.exists or (snapshot.to_dict() or {}).get("isDeleted") is not True
    )
    if failed:
        raise RuntimeError(f"soft-delete readback failed: {failed[:10]}")
    return {
        "schemaVersion": f"{SCHEMA_VERSION}/apply-receipt",
        "generatedAt": utc_now(),
        "planSha256": plan["planSha256"],
        "projectId": project_id,
        "targetCount": len(targets),
        "writtenCount": len(written),
        "alreadyAppliedCount": len(already_applied),
        "readbackDeletedCount": len(targets) - len(failed),
        "errors": failed,
        "writtenQuestionIds": sorted(written),
        "alreadyAppliedQuestionIds": sorted(already_applied),
        "recovery": plan.get("recovery"),
    }


def build_count_plan(*, kou_snapshot: Path, otsu_snapshot: Path) -> dict[str, Any]:
    sources = {"kou": kou_snapshot.resolve(), "otsu": otsu_snapshot.resolve()}
    targets: list[dict[str, Any]] = []
    for grade, snapshot_dir in sources.items():
        category = load_json(snapshot_dir / "reconstructed" / "category.json")
        for collection, filename, key, items_key in (
            ("questionSets", "questionSets.json", "questionSetId", "questionSets"),
            ("folders", "folders.json", "folderId", "folders"),
        ):
            raw_by_id = raw_documents(snapshot_dir, filename)
            for desired in category.get(items_key, []):
                document_id = str(desired[key])
                raw = raw_by_id[document_id]
                document = raw.get("decoded") or {}
                before = int(document.get("questionCount") or 0)
                after = int(desired.get("questionCount") or 0)
                if before == after:
                    continue
                targets.append(
                    {
                        "grade": grade,
                        "collection": collection,
                        "documentId": document_id,
                        "beforeQuestionCount": before,
                        "afterQuestionCount": after,
                        "precondition": selected_fields(document, COUNT_PRECONDITION_FIELDS),
                        "preconditionSha256": fingerprint(document, COUNT_PRECONDITION_FIELDS),
                        "snapshotUpdateTime": raw.get("updateTime"),
                    }
                )
    by_collection = Counter(item["collection"] for item in targets)
    plan: dict[str, Any] = {
        "schemaVersion": COUNT_SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "scope": "gas-shunin selected folders and questionSets",
        "sources": {key: str(value) for key, value in sources.items()},
        "summary": {
            "targetCount": len(targets),
            "questionSetTargetCount": by_collection["questionSets"],
            "folderTargetCount": by_collection["folders"],
        },
        "targets": sorted(targets, key=lambda item: (item["collection"], item["documentId"])),
        "recovery": {
            "operation": "restore beforeQuestionCount values",
            "beforeCounts": {
                f"{item['collection']}/{item['documentId']}": item["beforeQuestionCount"]
                for item in targets
            },
        },
    }
    plan["planSha256"] = payload_hash(plan, "planSha256")
    return plan


def apply_counts(
    *,
    plan: dict[str, Any],
    project_id: str,
    credentials_json: Path | None,
) -> dict[str, Any]:
    verify_plan_hash(plan)
    db, firestore = firestore_client(project_id, credentials_json)
    targets = list(plan.get("targets", []))
    refs = [db.collection(item["collection"]).document(item["documentId"]) for item in targets]
    snapshots = {
        snapshot.reference.path: snapshot
        for snapshot in db.get_all(refs, field_paths=list(COUNT_PRECONDITION_FIELDS))
    }
    pending: list[tuple[dict[str, Any], Any]] = []
    already_applied: list[str] = []
    for target in targets:
        path = f"{target['collection']}/{target['documentId']}"
        snapshot = snapshots.get(path)
        if snapshot is None or not snapshot.exists:
            raise RuntimeError(f"Firestore count target not found: {path}")
        document = snapshot.to_dict() or {}
        if fingerprint(document, COUNT_PRECONDITION_FIELDS) == target["preconditionSha256"]:
            pending.append((target, snapshot))
            continue
        if int(document.get("questionCount") or 0) == target["afterQuestionCount"]:
            already_applied.append(path)
            continue
        raise RuntimeError(f"Firestore count precondition mismatch: {path}")

    updated_at = datetime.now(timezone.utc)
    written: list[str] = []
    for batch_items in chunked(pending):
        batch = db.batch()
        for target, snapshot in batch_items:
            batch.update(
                snapshot.reference,
                {
                    "questionCount": target["afterQuestionCount"],
                    "updatedAt": updated_at,
                    "updatedById": UPDATED_BY_ID,
                },
                option=firestore.LastUpdateOption(snapshot.update_time),
            )
        batch.commit()
        written.extend(f"{target['collection']}/{target['documentId']}" for target, _ in batch_items)

    readback = {
        snapshot.reference.path: snapshot
        for snapshot in db.get_all(refs, field_paths=["questionCount"])
    }
    failed = []
    for target in targets:
        path = f"{target['collection']}/{target['documentId']}"
        snapshot = readback.get(path)
        actual = None if snapshot is None or not snapshot.exists else (snapshot.to_dict() or {}).get("questionCount")
        if actual != target["afterQuestionCount"]:
            failed.append(path)
    if failed:
        raise RuntimeError(f"count readback failed: {failed[:10]}")
    return {
        "schemaVersion": f"{COUNT_SCHEMA_VERSION}/apply-receipt",
        "generatedAt": utc_now(),
        "planSha256": plan["planSha256"],
        "projectId": project_id,
        "targetCount": len(targets),
        "writtenCount": len(written),
        "alreadyAppliedCount": len(already_applied),
        "readbackMatchCount": len(targets),
        "errors": failed,
        "writtenPaths": sorted(written),
        "alreadyAppliedPaths": sorted(already_applied),
        "recovery": plan.get("recovery"),
    }


def build_q21_repair_plan(*, snapshot_dir: Path, recovery_ledger: Path) -> dict[str, Any]:
    raw_by_id = raw_documents(snapshot_dir.resolve(), "questions.json")
    recovery = load_json(recovery_ledger.resolve())
    recovery_by_id = {
        str(item["questionId"]): item
        for item in recovery.get("records", [])
        if str(item.get("questionId") or "").startswith("gasushunin-otsushu-gizyutsu-2020-21-")
    }
    updates: list[dict[str, Any]] = []
    soft_deletes: list[dict[str, Any]] = []
    for choice_index in range(1, 6):
        canonical_id = f"gas-shunin-otsu-2020-shohi-q21-s0{choice_index}"
        legacy_id = f"gasushunin-otsushu-gizyutsu-2020-21-{choice_index}"
        canonical_raw = raw_by_id[canonical_id]
        legacy_raw = raw_by_id[legacy_id]
        canonical = canonical_raw.get("decoded") or {}
        legacy = legacy_raw.get("decoded") or {}
        recovery_record = recovery_by_id.get(legacy_id)
        if recovery_record is None or recovery_record.get("status") != "ready":
            raise ValueError(f"reviewed Q21 recovery record not found: {legacy_id}")
        proposed = recovery_record.get("proposed") or {}
        set_fields = {
            "originalQuestionBodyText": proposed["originalQuestionBodyText"],
            "originalQuestionChoiceText": proposed["originalQuestionChoiceText"],
            "questionText": proposed["questionText"],
            "correctChoiceText": proposed["correctChoiceText"],
            "explanationText": proposed["explanationText"],
            "knowledgeText": legacy.get("knowledgeText"),
        }
        if any(value in (None, "") for value in set_fields.values()):
            raise ValueError(f"Q21 repair contains blank required value: {canonical_id}")
        if canonical.get("isDeleted") is not False or legacy.get("isDeleted") is not False:
            raise ValueError(f"Q21 repair requires active source and target: {choice_index}")
        if canonical.get("questionSetId") != legacy.get("questionSetId"):
            raise ValueError(f"Q21 source and target questionSetId differ: {choice_index}")
        updates.append(
            {
                "questionId": canonical_id,
                "sourceQuestionId": legacy_id,
                "setFields": set_fields,
                "deleteFields": ["suggestedQuestions", "suggestedQuestionDetails"],
                "precondition": selected_fields(canonical, Q21_PRECONDITION_FIELDS),
                "preconditionSha256": fingerprint(canonical, Q21_PRECONDITION_FIELDS),
                "snapshotUpdateTime": canonical_raw.get("updateTime"),
            }
        )
        soft_deletes.append(
            {
                "questionId": legacy_id,
                "replacementQuestionId": canonical_id,
                "precondition": selected_fields(legacy, Q21_PRECONDITION_FIELDS),
                "preconditionSha256": fingerprint(legacy, Q21_PRECONDITION_FIELDS),
                "snapshotUpdateTime": legacy_raw.get("updateTime"),
            }
        )
    plan: dict[str, Any] = {
        "schemaVersion": "gas-shunin-otsu-2020-q21-canonical-repair/v1",
        "generatedAt": utc_now(),
        "sources": {
            "snapshot": str(snapshot_dir.resolve()),
            "reviewedRecoveryLedger": str(recovery_ledger.resolve()),
        },
        "summary": {
            "canonicalUpdateCount": len(updates),
            "legacySoftDeleteCount": len(soft_deletes),
            "projectedActiveDisplayQuestionCountChange": 0,
        },
        "updates": updates,
        "softDeletes": soft_deletes,
        "recovery": {
            "canonicalBefore": {
                item["questionId"]: item["precondition"] for item in updates
            },
            "legacyIdsToReactivate": [item["questionId"] for item in soft_deletes],
        },
    }
    plan["planSha256"] = payload_hash(plan, "planSha256")
    return plan


def apply_q21_repair(
    *,
    plan: dict[str, Any],
    project_id: str,
    credentials_json: Path | None,
) -> dict[str, Any]:
    verify_plan_hash(plan)
    db, firestore = firestore_client(project_id, credentials_json)
    updates = list(plan.get("updates", []))
    soft_deletes = list(plan.get("softDeletes", []))
    all_items = updates + soft_deletes
    refs = [db.collection("questions").document(item["questionId"]) for item in all_items]
    snapshots = {
        snapshot.id: snapshot
        for snapshot in db.get_all(refs, field_paths=list(Q21_PRECONDITION_FIELDS))
    }
    for item in all_items:
        snapshot = snapshots.get(item["questionId"])
        if snapshot is None or not snapshot.exists:
            raise RuntimeError(f"Q21 Firestore document not found: {item['questionId']}")
        current = fingerprint(snapshot.to_dict() or {}, Q21_PRECONDITION_FIELDS)
        if current != item["preconditionSha256"]:
            raise RuntimeError(f"Q21 Firestore precondition mismatch: {item['questionId']}")

    updated_at = datetime.now(timezone.utc)
    batch = db.batch()
    for item in updates:
        snapshot = snapshots[item["questionId"]]
        payload = dict(item["setFields"])
        for field in item.get("deleteFields", []):
            payload[field] = firestore.DELETE_FIELD
        payload.update({"updatedAt": updated_at, "updatedById": UPDATED_BY_ID})
        batch.update(
            snapshot.reference,
            payload,
            option=firestore.LastUpdateOption(snapshot.update_time),
        )
    for item in soft_deletes:
        snapshot = snapshots[item["questionId"]]
        batch.update(
            snapshot.reference,
            {"isDeleted": True, "updatedAt": updated_at, "updatedById": UPDATED_BY_ID},
            option=firestore.LastUpdateOption(snapshot.update_time),
        )
    batch.commit()

    readback_fields = sorted(
        set(Q21_PRECONDITION_FIELDS) | {field for item in updates for field in item["setFields"]}
    )
    readback = {
        snapshot.id: snapshot
        for snapshot in db.get_all(refs, field_paths=readback_fields)
    }
    errors: list[str] = []
    for item in updates:
        document = readback[item["questionId"]].to_dict() or {}
        if any(document.get(field) != value for field, value in item["setFields"].items()):
            errors.append(item["questionId"])
        if any(field in document for field in item.get("deleteFields", [])):
            errors.append(item["questionId"])
        if document.get("isDeleted") is not False:
            errors.append(item["questionId"])
    for item in soft_deletes:
        document = readback[item["questionId"]].to_dict() or {}
        if document.get("isDeleted") is not True:
            errors.append(item["questionId"])
    if errors:
        raise RuntimeError(f"Q21 readback failed: {sorted(set(errors))}")
    return {
        "schemaVersion": f"{plan['schemaVersion']}/apply-receipt",
        "generatedAt": utc_now(),
        "planSha256": plan["planSha256"],
        "projectId": project_id,
        "canonicalUpdateCount": len(updates),
        "legacySoftDeleteCount": len(soft_deletes),
        "readbackMatchCount": len(all_items),
        "errors": [],
        "recovery": plan.get("recovery"),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    result.add_argument("--credentials-json", type=Path, default=None)
    subparsers = result.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build-plan")
    build.add_argument("--kou-snapshot", type=Path, required=True)
    build.add_argument("--otsu-snapshot", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)

    apply_delete = subparsers.add_parser("apply-soft-delete")
    apply_delete.add_argument("--plan", type=Path, required=True)
    apply_delete.add_argument("--receipt", type=Path, required=True)

    build_counts = subparsers.add_parser("build-count-plan")
    build_counts.add_argument("--kou-snapshot", type=Path, required=True)
    build_counts.add_argument("--otsu-snapshot", type=Path, required=True)
    build_counts.add_argument("--output", type=Path, required=True)

    apply_count_values = subparsers.add_parser("apply-counts")
    apply_count_values.add_argument("--plan", type=Path, required=True)
    apply_count_values.add_argument("--receipt", type=Path, required=True)

    build_q21 = subparsers.add_parser("build-q21-repair-plan")
    build_q21.add_argument("--snapshot", type=Path, required=True)
    build_q21.add_argument("--recovery-ledger", type=Path, required=True)
    build_q21.add_argument("--output", type=Path, required=True)

    apply_q21 = subparsers.add_parser("apply-q21-repair")
    apply_q21.add_argument("--plan", type=Path, required=True)
    apply_q21.add_argument("--receipt", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "build-plan":
        plan = build_duplicate_plan(kou_snapshot=args.kou_snapshot, otsu_snapshot=args.otsu_snapshot)
        write_json(args.output, plan)
        print(json.dumps(plan["summary"], ensure_ascii=False, indent=2))
        return 0
    if args.command == "apply-soft-delete":
        receipt = apply_soft_delete(
            plan=load_json(args.plan),
            project_id=args.project_id,
            credentials_json=args.credentials_json,
        )
        write_json(args.receipt, receipt)
        print(json.dumps({key: value for key, value in receipt.items() if not key.endswith("Ids")}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "build-count-plan":
        plan = build_count_plan(kou_snapshot=args.kou_snapshot, otsu_snapshot=args.otsu_snapshot)
        write_json(args.output, plan)
        print(json.dumps(plan["summary"], ensure_ascii=False, indent=2))
        return 0
    if args.command == "apply-counts":
        receipt = apply_counts(
            plan=load_json(args.plan),
            project_id=args.project_id,
            credentials_json=args.credentials_json,
        )
        write_json(args.receipt, receipt)
        print(json.dumps({key: value for key, value in receipt.items() if not key.endswith("Paths")}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "build-q21-repair-plan":
        plan = build_q21_repair_plan(
            snapshot_dir=args.snapshot,
            recovery_ledger=args.recovery_ledger,
        )
        write_json(args.output, plan)
        print(json.dumps(plan["summary"], ensure_ascii=False, indent=2))
        return 0
    if args.command == "apply-q21-repair":
        receipt = apply_q21_repair(
            plan=load_json(args.plan),
            project_id=args.project_id,
            credentials_json=args.credentials_json,
        )
        write_json(args.receipt, receipt)
        print(json.dumps({key: value for key, value in receipt.items() if key != "recovery"}, ensure_ascii=False, indent=2))
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
