#!/usr/bin/env python3
"""公式PDF監査に基づきガス主任技術者問題を安全に収束させる。"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.upload.firebase_credentials import (  # noqa: E402
    DEFAULT_PROJECT_ID,
    initialize_firebase_app,
)


SCHEMA_VERSION = "gas-shunin-official-pdf-repair/v1"
UPDATED_BY_ID = "aMpBCmAEGSQPbhUMzbHvFiM1cYK2"
PROTECTED_COMPONENTS = {"00_source", "old", "firestore_snapshot"}
LOCAL_SKIP_COMPONENTS = PROTECTED_COMPONENTS | {"firestore_repairs"}
PRECONDITION_FIELDS = (
    "choiceNumber",
    "correctChoiceText",
    "examSource",
    "examYear",
    "explanationText",
    "isChoiceOnly",
    "isDeleted",
    "lawRevisionFacts",
    "originalQuestionChoiceText",
    "originalQuestionId",
    "questionNumber",
    "questionSetId",
    "questionText",
    "questionType",
)
EXPECTED_ACTIVE_BEFORE = 4326
EXPECTED_SOFT_DELETE = {"kou": 6, "otsu": 195}
EXPECTED_PREFIX_REPAIRS = 540
METADATA_IDS = {
    *(f"chiefgasengineerlicense-A-10-{number:04d}" for number in range(241, 251)),
    *(f"chiefgasengineerlicense-A-10-{number:04d}" for number in (326, 327, 328, 330)),
    *(f"chiefgasengineerlicense-A-10-{number:04d}" for number in range(352, 356)),
    "chiefgasengineerlicense-C-10138",
}
AGGREGATE_SOURCE_CORRECTIONS = {
    "8e30472ec04b7830": {
        "choiceIndex": 5,
        "correctChoiceText": "正しい",
        "explanationText": (
            "正しい。公式正答に基づくと、この設問では、誘電率の小さい液体は"
            "液位変化に伴う静電容量の変化が小さく、静電容量式レベル計では"
            "感度を得にくいという趣旨で扱われている。なお、例示された蒸留水は"
            "一般に比誘電率が大きいため、この例示自体には技術的な疑義がある。"
            "試験上の正誤と測定原理を分けて確認する必要がある。"
        ),
    },
    "58726aadc48afa5f": {
        "choiceIndex": 1,
        "correctChoiceText": "正しい",
        "explanationText": (
            "正しい。2018年の出題時点では、屋内式ガス瞬間湯沸器は"
            "長期使用製品安全点検制度の特定保守製品に指定されていた。"
            "なお、令和3年8月1日施行の改正で指定から外れたため、現行制度では"
            "特定保守製品ではない。公式過去問の正誤は出題時点の制度で判断する。"
        ),
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


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


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        json_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def selected_fields(document: dict[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    return {field: json_safe(document.get(field)) for field in fields}


def payload_hash(payload: dict[str, Any]) -> str:
    return canonical_hash({key: value for key, value in payload.items() if key != "planSha256"})


def verify_plan_hash(plan: dict[str, Any]) -> None:
    actual = payload_hash(plan)
    if plan.get("planSha256") != actual:
        raise ValueError(f"plan hash mismatch: {plan.get('planSha256')} != {actual}")


def raw_documents(snapshot_dir: Path) -> dict[str, dict[str, Any]]:
    payload = load_json(snapshot_dir / "raw" / "questions.json")
    return {str(item["_id"]): item for item in payload.get("documents", [])}


def active_documents(snapshot_dir: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for question_id, raw in raw_documents(snapshot_dir).items():
        document = raw.get("decoded") or {}
        if document.get("isDeleted") is False and document.get("isChoiceOnly") is False:
            result[question_id] = document
    return result


def protected_tree_manifest(local_roots: Iterable[Path]) -> dict[str, Any]:
    rows: list[tuple[str, str]] = []
    for local_root in sorted(path.resolve() for path in local_roots):
        for path in sorted(local_root.rglob("*.json")):
            if not (set(path.parts) & PROTECTED_COMPONENTS):
                continue
            rows.append((str(path.resolve()), file_hash(path)))
    return {"fileCount": len(rows), "treeSha256": canonical_hash(rows)}


def identity_key(record: dict[str, Any]) -> tuple[str, int, str, int]:
    return (
        str(record["grade"]),
        int(record["examYear"]),
        str(record["section"]),
        int(record["questionNumber"]),
    )


def is_publication_id(question_id: str) -> bool:
    return question_id.startswith("gas-shunin-") and "site-shadow" not in question_id


def build_soft_delete_ids(
    document_records: list[dict[str, Any]],
) -> tuple[set[str], list[dict[str, Any]]]:
    groups: dict[tuple[str, int, str, int], list[str]] = defaultdict(list)
    for record in document_records:
        groups[identity_key(record)].append(str(record["questionId"]))

    targets: set[str] = set()
    decisions: list[dict[str, Any]] = []
    for identity, ids in sorted(groups.items()):
        publication = sorted(item for item in ids if is_publication_id(item))
        if publication:
            extras = sorted(item for item in ids if item not in publication)
            if extras:
                targets.update(extras)
                decisions.append(
                    {
                        "identity": list(identity),
                        "keepIds": publication,
                        "softDeleteIds": extras,
                        "reason": "official identity retains canonical publication documents",
                    }
                )
            continue
        if identity == ("kou", 2023, "law", 2):
            shadow_choice_1 = [item for item in ids if "q02-s01-site-shadow" in item]
            if len(shadow_choice_1) != 1:
                raise ValueError("Kou 2023 law question 2 shadow choice 1 is not unique")
            targets.add(shadow_choice_1[0])
            decisions.append(
                {
                    "identity": list(identity),
                    "keepIds": sorted(item for item in ids if item not in shadow_choice_1),
                    "softDeleteIds": shadow_choice_1,
                    "reason": "choice 1 site shadow duplicates the retained contiguous import",
                }
            )

    counts = Counter("kou" if item.startswith(("chiefgasengineerlicense-A-", "gas-shunin-kou-")) else "otsu" for item in targets)
    if dict(counts) != EXPECTED_SOFT_DELETE:
        raise ValueError(f"unexpected soft-delete counts: {dict(counts)}")
    return targets, decisions


def expected_prefix(document: dict[str, Any]) -> str:
    if document.get("questionType") == "true_false":
        verdict = str(document.get("correctChoiceText") or "")
        if verdict not in {"正しい", "間違い"}:
            raise ValueError(f"invalid true_false verdict: {verdict}")
        return f"{verdict}。"
    return "正しい。"


def metadata_fields(question_id: str, document: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    if question_id not in METADATA_IDS:
        return {}
    grade_label = "甲種" if record["grade"] == "kou" else "乙種"
    year = int(record["examYear"])
    question_number = int(record["questionNumber"])
    choice_number = int(document["choiceNumber"])
    section_token = {"law": "hourei", "basic": "kiso", "gas": "gizyutsu"}[record["section"]]
    grade_token = "koushu" if record["grade"] == "kou" else "otsushu"
    return {
        "examYear": year,
        "questionNumber": question_number,
        "examSource": (
            f"ガス主任技術者（{grade_label}）, {year}年, "
            f"問{question_number}, 設問{choice_number}"
        ),
        "originalQuestionId": (
            f"gasushunin-{grade_token}-{section_token}-{year}-{question_number}"
        ),
    }


def manual_fields(question_id: str, document: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    if question_id == "gas-shunin-otsu-2018-seizo-q05-s05":
        law_facts = copy.deepcopy(document.get("lawRevisionFacts") or {})
        law_facts.setdefault("current", {})["correctChoiceText"] = "正しい"
        law_facts.setdefault("examTime", {})["correctChoiceText"] = "正しい"
        law_facts.setdefault("evidenceSummary", {})["verdict"] = (
            "公式正答では本問の誤りは選択肢1であり、選択肢5は正しい扱い。"
            "ただし、蒸留水を誘電率の小さい液体とする例示には技術的な疑義がある。"
        )
        return (
            {
                "correctChoiceText": "正しい",
                "explanationText": (
                    "正しい。公式正答に基づくと、この設問では、誘電率の小さい液体は"
                    "液位変化に伴う静電容量の変化が小さく、静電容量式レベル計では"
                    "感度を得にくいという趣旨で扱われている。なお、例示された蒸留水は"
                    "一般に比誘電率が大きいため、この例示自体には技術的な疑義がある。"
                    "試験上の正誤と測定原理を分けて確認する必要がある。"
                ),
                "lawRevisionFacts": law_facts,
            },
            ["official_answer_alignment", "technical_caveat"],
        )
    if question_id == "gas-shunin-otsu-2018-shohi-q22-s01":
        return (
            {
                "correctChoiceText": "正しい",
                "explanationText": (
                    "正しい。2018年の出題時点では、屋内式ガス瞬間湯沸器は"
                    "長期使用製品安全点検制度の特定保守製品に指定されていた。"
                    "なお、令和3年8月1日施行の改正で指定から外れたため、現行制度では"
                    "特定保守製品ではない。公式過去問の正誤は出題時点の制度で判断する。"
                ),
            },
            ["official_answer_alignment", "exam_time_law_scope"],
        )
    if question_id == "chiefgasengineerlicense-C-10137":
        statement = str(document.get("originalQuestionBodyText") or "").strip()
        if not statement:
            raise ValueError("C-10137 source statement is blank")
        body = str(document.get("questionBodyText") or "").strip()
        return (
            {
                "originalQuestionChoiceText": statement,
                "questionText": f"{body}[quote]{statement}[/quote]",
            },
            ["restore_official_statement_display"],
        )
    return {}, []


def official_evidence(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "identity": {
            "grade": record["grade"],
            "examYear": record["examYear"],
            "section": record["section"],
            "questionNumber": record["questionNumber"],
        },
        "questionPdf": record["officialQuestionPdf"],
        "answerPdf": record["officialAnswerPdf"],
        "officialCorrectChoiceNumber": record["officialCorrectChoiceNumber"],
        "mappingStatus": record["match"]["status"],
    }


def build_plan(
    *,
    kou_snapshot: Path,
    otsu_snapshot: Path,
    document_index: Path,
    local_roots: list[Path],
) -> dict[str, Any]:
    raw_by_grade = {
        "kou": raw_documents(kou_snapshot.resolve()),
        "otsu": raw_documents(otsu_snapshot.resolve()),
    }
    active_by_grade = {
        "kou": active_documents(kou_snapshot.resolve()),
        "otsu": active_documents(otsu_snapshot.resolve()),
    }
    active_count = sum(len(items) for items in active_by_grade.values())
    if active_count != EXPECTED_ACTIVE_BEFORE:
        raise ValueError(f"active display drift: {active_count}")

    index_payload = load_json(document_index.resolve())
    records = list(index_payload.get("records", []))
    if len(records) != active_count or index_payload["summary"].get("holdCount") != 0:
        raise ValueError("official document index does not cover all active documents")
    record_by_id = {str(item["questionId"]): item for item in records}
    all_active_ids = set().union(*(set(items) for items in active_by_grade.values()))
    if set(record_by_id) != all_active_ids:
        raise ValueError("official document index IDs differ from active snapshot IDs")

    soft_delete_ids, group_decisions = build_soft_delete_ids(records)
    updates: list[dict[str, Any]] = []
    prefix_repair_ids: list[str] = []
    metadata_repair_ids: list[str] = []
    manual_repair_ids: list[str] = []

    for question_id in sorted(all_active_ids - soft_delete_ids):
        record = record_by_id[question_id]
        grade = str(record["grade"])
        document = active_by_grade[grade][question_id]
        set_fields: dict[str, Any] = {}
        reasons: list[str] = []

        metadata = metadata_fields(question_id, document, record)
        if metadata:
            set_fields.update(metadata)
            reasons.append("official_identity_metadata")
            metadata_repair_ids.append(question_id)

        manual, manual_reasons = manual_fields(question_id, document)
        if manual:
            set_fields.update(manual)
            reasons.extend(manual_reasons)
            manual_repair_ids.append(question_id)

        projected = {**document, **set_fields}
        prefix = expected_prefix(projected)
        explanation = str(projected.get("explanationText") or "").strip()
        if not explanation.startswith(prefix):
            if not explanation:
                raise ValueError(f"blank explanation cannot be prefixed: {question_id}")
            set_fields["explanationText"] = prefix + explanation
            reasons.append("basic_explanation_prefix")
            prefix_repair_ids.append(question_id)

        changed = {key: value for key, value in set_fields.items() if document.get(key) != value}
        if not changed:
            continue
        raw = raw_by_grade[grade][question_id]
        before = selected_fields(document, PRECONDITION_FIELDS)
        updates.append(
            {
                "grade": grade,
                "questionId": question_id,
                "setFields": changed,
                "changedFields": sorted(changed),
                "reasons": reasons,
                "officialEvidence": official_evidence(record),
                "precondition": before,
                "preconditionSha256": canonical_hash(before),
                "snapshotUpdateTime": raw.get("updateTime"),
            }
        )

    if len(prefix_repair_ids) != EXPECTED_PREFIX_REPAIRS:
        raise ValueError(f"unexpected explanation prefix repairs: {len(prefix_repair_ids)}")
    if set(metadata_repair_ids) != METADATA_IDS:
        raise ValueError(
            f"metadata repair IDs differ: missing={sorted(METADATA_IDS-set(metadata_repair_ids))} "
            f"extra={sorted(set(metadata_repair_ids)-METADATA_IDS)}"
        )
    if set(manual_repair_ids) != {
        "gas-shunin-otsu-2018-seizo-q05-s05",
        "gas-shunin-otsu-2018-shohi-q22-s01",
        "chiefgasengineerlicense-C-10137",
    }:
        raise ValueError(f"manual repair IDs differ: {manual_repair_ids}")

    soft_deletes: list[dict[str, Any]] = []
    for question_id in sorted(soft_delete_ids):
        record = record_by_id[question_id]
        grade = str(record["grade"])
        document = active_by_grade[grade][question_id]
        raw = raw_by_grade[grade][question_id]
        before = selected_fields(document, PRECONDITION_FIELDS)
        soft_deletes.append(
            {
                "grade": grade,
                "questionId": question_id,
                "questionSetId": document.get("questionSetId"),
                "officialEvidence": official_evidence(record),
                "precondition": before,
                "preconditionSha256": canonical_hash(before),
                "snapshotUpdateTime": raw.get("updateTime"),
            }
        )

    plan: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "projectId": DEFAULT_PROJECT_ID,
        "scope": "gas-shunin active display questions",
        "sourcePolicy": "official question and answer PDFs only; listing site not used",
        "sources": {
            "kouSnapshot": str(kou_snapshot.resolve()),
            "otsuSnapshot": str(otsu_snapshot.resolve()),
            "officialDocumentIndex": str(document_index.resolve()),
        },
        "localRoots": [str(path.resolve()) for path in local_roots],
        "protectedLocalTree": protected_tree_manifest(local_roots),
        "summary": {
            "activeDisplayQuestionCountBefore": active_count,
            "softDeleteTargetCount": len(soft_deletes),
            "softDeleteByGrade": dict(Counter(item["grade"] for item in soft_deletes)),
            "projectedActiveDisplayQuestionCount": active_count - len(soft_deletes),
            "updateTargetCount": len(updates),
            "explanationPrefixRepairCount": len(prefix_repair_ids),
            "metadataRepairCount": len(metadata_repair_ids),
            "manualContentOrAnswerRepairCount": len(manual_repair_ids),
            "holdCount": 0,
        },
        "duplicateGroupDecisions": group_decisions,
        "softDeletes": soft_deletes,
        "updates": updates,
        "recovery": {
            "softDeleteIdsToReactivate": sorted(soft_delete_ids),
            "beforeFieldsByQuestionId": {
                item["questionId"]: {
                    field: item["precondition"].get(field)
                    for field in item.get("changedFields", [])
                }
                for item in updates
            },
        },
    }
    plan["planSha256"] = payload_hash(plan)
    return plan


def looks_like_question_record(item: dict[str, Any]) -> bool:
    return bool(
        (item.get("_id") or item.get("questionId"))
        and ({"questionText", "correctChoiceText", "isDeleted"} & set(item))
    )


def apply_changes_to_value(
    value: Any,
    *,
    updates: dict[str, dict[str, Any]],
    soft_delete_ids: set[str],
) -> tuple[Any, Counter[str]]:
    counts: Counter[str] = Counter()

    def visit(item: Any) -> Any:
        if isinstance(item, list):
            return [visit(child) for child in item]
        if not isinstance(item, dict):
            return item
        result = {key: visit(child) for key, child in item.items()}
        if not looks_like_question_record(item):
            return result
        question_id = str(item.get("_id") or item.get("questionId") or "")
        if question_id in soft_delete_ids and "isDeleted" in result:
            if result.get("isDeleted") is not True:
                result["isDeleted"] = True
                counts[question_id] += 1
        update = updates.get(question_id)
        if update:
            for field, after in update["setFields"].items():
                if field not in result:
                    continue
                if result.get(field) != after:
                    result[field] = copy.deepcopy(after)
                    counts[question_id] += 1
        return result

    return visit(value), counts


def apply_aggregate_source_corrections(value: Any) -> tuple[Any, Counter[str]]:
    counts: Counter[str] = Counter()

    def visit(item: Any) -> Any:
        if isinstance(item, list):
            return [visit(child) for child in item]
        if not isinstance(item, dict):
            return item
        result = {key: visit(child) for key, child in item.items()}
        public_id = str(item.get("public_question_id") or "")
        correction = AGGREGATE_SOURCE_CORRECTIONS.get(public_id)
        if not correction:
            return result
        index = int(correction["choiceIndex"]) - 1
        for field in ("correctChoiceText", "explanationText"):
            values = result.get(field)
            if not isinstance(values, list) or index >= len(values):
                raise ValueError(f"aggregate source field missing: {public_id}:{field}")
            if values[index] != correction[field]:
                values[index] = correction[field]
                counts[f"source:{public_id}"] += 1
        return result

    return visit(value), counts


def projected_documents(plan: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    by_grade: dict[str, list[dict[str, Any]]] = {"kou": [], "otsu": []}
    updates = {item["questionId"]: item for item in plan["updates"]}
    deleted = {item["questionId"] for item in plan["softDeletes"]}
    for grade, source_key in (("kou", "kouSnapshot"), ("otsu", "otsuSnapshot")):
        for question_id, raw in raw_documents(Path(plan["sources"][source_key])).items():
            document = copy.deepcopy(raw.get("decoded") or {})
            if question_id in deleted:
                document["isDeleted"] = True
            if question_id in updates:
                document.update(copy.deepcopy(updates[question_id]["setFields"]))
            by_grade[grade].append(document)
        by_grade[grade].sort(key=lambda item: str(item.get("_id") or ""))
    return by_grade


def apply_local(*, plan: dict[str, Any], output_root: Path) -> dict[str, Any]:
    verify_plan_hash(plan)
    local_roots = [Path(item) for item in plan.get("localRoots", [])]
    before_protected = protected_tree_manifest(local_roots)
    if before_protected != plan.get("protectedLocalTree"):
        raise RuntimeError("protected local tree changed after plan creation")

    updates = {item["questionId"]: item for item in plan["updates"]}
    deleted = {item["questionId"] for item in plan["softDeletes"]}
    changed_files: list[dict[str, Any]] = []
    changed_occurrences: Counter[str] = Counter()
    for local_root in sorted(path.resolve() for path in local_roots):
        for path in sorted(local_root.rglob("*.json")):
            if set(path.parts) & LOCAL_SKIP_COMPONENTS:
                continue
            try:
                before_payload = load_json(path)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            after_payload, counts = apply_changes_to_value(
                before_payload, updates=updates, soft_delete_ids=deleted
            )
            after_payload, aggregate_counts = apply_aggregate_source_corrections(
                after_payload
            )
            counts.update(aggregate_counts)
            if not counts:
                continue
            before_sha = file_hash(path)
            write_json(path, after_payload)
            changed_files.append(
                {
                    "path": str(path.resolve()),
                    "beforeSha256": before_sha,
                    "afterSha256": file_hash(path),
                    "changedFieldOccurrenceCount": sum(counts.values()),
                }
            )
            changed_occurrences.update(counts)

    projected = projected_documents(plan)
    for grade, documents in projected.items():
        grade_root = output_root / grade
        write_json(
            grade_root / "post_repair_questions.json",
            {
                "schemaVersion": f"{SCHEMA_VERSION}/local-projected-snapshot",
                "planSha256": plan["planSha256"],
                "questions": documents,
            },
        )
        grade_updates = [item for item in plan["updates"] if item["grade"] == grade]
        grade_deletes = [item for item in plan["softDeletes"] if item["grade"] == grade]
        write_json(
            grade_root / "repair_records.json",
            {
                "schemaVersion": f"{SCHEMA_VERSION}/local-repair-records",
                "planSha256": plan["planSha256"],
                "updates": grade_updates,
                "softDeletes": grade_deletes,
            },
        )

    after_protected = protected_tree_manifest(local_roots)
    if after_protected != before_protected:
        raise RuntimeError("protected local tree changed during apply")
    receipt = {
        "schemaVersion": f"{SCHEMA_VERSION}/local-apply-receipt",
        "generatedAt": utc_now(),
        "planSha256": plan["planSha256"],
        "changedDerivedFileCount": len(changed_files),
        "changedDerivedFieldOccurrenceCount": sum(changed_occurrences.values()),
        "changedQuestionIdCount": len(changed_occurrences),
        "projectedSnapshotQuestionCount": sum(len(items) for items in projected.values()),
        "projectedActiveDisplayQuestionCount": sum(
            1
            for items in projected.values()
            for item in items
            if item.get("isDeleted") is False and item.get("isChoiceOnly") is False
        ),
        "protectedLocalTreeBefore": before_protected,
        "protectedLocalTreeAfter": after_protected,
        "changedFiles": changed_files,
        "changedOccurrencesByQuestionId": dict(sorted(changed_occurrences.items())),
    }
    return receipt


def firestore_client(project_id: str, credentials_json: Path | None):
    initialize_firebase_app(project_id=project_id, credentials_json=credentials_json)
    from firebase_admin import firestore

    return firestore.client(), firestore


def chunks(values: list[Any], size: int = 400) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def apply_firestore(
    *, plan: dict[str, Any], project_id: str, credentials_json: Path | None
) -> dict[str, Any]:
    verify_plan_hash(plan)
    db, firestore = firestore_client(project_id, credentials_json)
    operations = [
        *(dict(item, operation="update") for item in plan["updates"]),
        *(dict(item, operation="soft_delete") for item in plan["softDeletes"]),
    ]
    refs = [db.collection("questions").document(item["questionId"]) for item in operations]
    snapshots = {
        snapshot.id: snapshot
        for snapshot in db.get_all(refs, field_paths=list(PRECONDITION_FIELDS))
    }
    pending: list[tuple[dict[str, Any], Any]] = []
    already_applied: list[str] = []
    for item in operations:
        question_id = item["questionId"]
        snapshot = snapshots.get(question_id)
        if snapshot is None or not snapshot.exists:
            raise RuntimeError(f"Firestore question missing: {question_id}")
        document = snapshot.to_dict() or {}
        current = selected_fields(document, PRECONDITION_FIELDS)
        if canonical_hash(current) == item["preconditionSha256"]:
            pending.append((item, snapshot))
            continue
        if item["operation"] == "soft_delete" and document.get("isDeleted") is True:
            already_applied.append(question_id)
            continue
        if item["operation"] == "update" and all(
            document.get(field) == value for field, value in item["setFields"].items()
        ):
            already_applied.append(question_id)
            continue
        raise RuntimeError(f"Firestore precondition mismatch: {question_id}")

    written: list[str] = []
    for items in chunks(pending):
        batch = db.batch()
        updated_at = datetime.now(timezone.utc)
        for item, snapshot in items:
            if item["operation"] == "soft_delete":
                payload = {"isDeleted": True}
            else:
                payload = copy.deepcopy(item["setFields"])
            payload.update({"updatedAt": updated_at, "updatedById": UPDATED_BY_ID})
            batch.update(
                snapshot.reference,
                payload,
                option=firestore.LastUpdateOption(snapshot.update_time),
            )
        batch.commit()
        written.extend(item["questionId"] for item, _ in items)

    readback = {
        snapshot.id: snapshot
        for snapshot in db.get_all(refs, field_paths=list(PRECONDITION_FIELDS))
    }
    errors: list[str] = []
    for item in operations:
        document = (readback[item["questionId"]].to_dict() or {})
        if item["operation"] == "soft_delete":
            if document.get("isDeleted") is not True:
                errors.append(item["questionId"])
        elif any(document.get(field) != value for field, value in item["setFields"].items()):
            errors.append(item["questionId"])
    if errors:
        raise RuntimeError(f"Firestore readback failed: {errors[:10]}")
    return {
        "schemaVersion": f"{SCHEMA_VERSION}/firestore-apply-receipt",
        "generatedAt": utc_now(),
        "planSha256": plan["planSha256"],
        "projectId": project_id,
        "operationCount": len(operations),
        "writtenCount": len(written),
        "alreadyAppliedCount": len(already_applied),
        "readbackMatchCount": len(operations),
        "errors": errors,
        "writtenQuestionIds": sorted(written),
        "alreadyAppliedQuestionIds": sorted(already_applied),
        "userDataWrites": 0,
        "hardDeletes": 0,
        "recovery": plan.get("recovery"),
    }


def count_mismatches(snapshot_dir: Path) -> list[str]:
    category = load_json(snapshot_dir / "reconstructed" / "category.json")
    mismatches: list[str] = []
    for collection, filename, id_field, list_field in (
        ("folders", "folders.json", "folderId", "folders"),
        ("questionSets", "questionSets.json", "questionSetId", "questionSets"),
    ):
        raw = {
            item["_id"]: item.get("decoded") or {}
            for item in load_json(snapshot_dir / "raw" / filename).get("documents", [])
        }
        for desired in category.get(list_field, []):
            document_id = str(desired[id_field])
            actual_count = int(raw[document_id].get("questionCount") or 0)
            desired_count = int(desired.get("questionCount") or 0)
            if actual_count != desired_count:
                mismatches.append(f"{collection}/{document_id}:{actual_count}!={desired_count}")
    return mismatches


def without_volatile_fields(document: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in document.items()
        if key not in {"createdAt", "createdById", "updatedAt", "updatedById"}
    }


def verify_post(
    *,
    plan: dict[str, Any],
    kou_snapshot: Path,
    otsu_snapshot: Path,
    document_index: Path,
    local_output_root: Path,
) -> dict[str, Any]:
    verify_plan_hash(plan)
    snapshots = {"kou": kou_snapshot.resolve(), "otsu": otsu_snapshot.resolve()}
    raw_by_grade = {grade: raw_documents(path) for grade, path in snapshots.items()}
    live_by_grade = {
        grade: {question_id: raw.get("decoded") or {} for question_id, raw in items.items()}
        for grade, items in raw_by_grade.items()
    }
    active_by_grade = {
        grade: {
            question_id: document
            for question_id, document in items.items()
            if document.get("isDeleted") is False and document.get("isChoiceOnly") is False
        }
        for grade, items in live_by_grade.items()
    }
    active_count = sum(len(items) for items in active_by_grade.values())
    expected_active = int(plan["summary"]["projectedActiveDisplayQuestionCount"])
    if active_count != expected_active:
        raise RuntimeError(f"post active display count mismatch: {active_count} != {expected_active}")

    operation_errors: list[str] = []
    for item in plan["softDeletes"]:
        if live_by_grade[item["grade"]][item["questionId"]].get("isDeleted") is not True:
            operation_errors.append(item["questionId"])
    for item in plan["updates"]:
        document = live_by_grade[item["grade"]][item["questionId"]]
        if any(document.get(field) != value for field, value in item["setFields"].items()):
            operation_errors.append(item["questionId"])
    if operation_errors:
        raise RuntimeError(f"post operation mismatch: {operation_errors[:10]}")

    active = {
        question_id: document
        for items in active_by_grade.values()
        for question_id, document in items.items()
    }
    prefix_mismatches: list[str] = []
    statement_missing: list[str] = []
    body_missing: list[str] = []
    question_set_ref_present: list[str] = []
    for question_id, document in active.items():
        if not str(document.get("explanationText") or "").startswith(expected_prefix(document)):
            prefix_mismatches.append(question_id)
        if document.get("questionType") == "true_false" and "[quote]" not in str(
            document.get("questionText") or ""
        ):
            statement_missing.append(question_id)
        if not str(document.get("questionBodyText") or "").strip():
            body_missing.append(question_id)
        if "questionSetRef" in document:
            question_set_ref_present.append(question_id)

    records = load_json(document_index.resolve()).get("records", [])
    record_by_id = {str(item["questionId"]): item for item in records}
    if not set(active).issubset(record_by_id):
        raise RuntimeError("post active IDs are not covered by official document index")
    identity_groups: dict[tuple[str, int, str, int], list[str]] = defaultdict(list)
    for question_id in active:
        identity_groups[identity_key(record_by_id[question_id])].append(question_id)
    mixed_groups: list[list[str]] = []
    for ids in identity_groups.values():
        has_publication = any(is_publication_id(item) for item in ids)
        has_other = any(not is_publication_id(item) for item in ids)
        if has_publication and has_other:
            mixed_groups.append(sorted(ids))

    count_errors = [
        item
        for snapshot in snapshots.values()
        for item in count_mismatches(snapshot)
    ]
    projected_by_grade: dict[str, dict[str, dict[str, Any]]] = {}
    local_mismatches: list[str] = []
    for grade in ("kou", "otsu"):
        projected_path = local_output_root / grade / "post_repair_questions.json"
        projected_payload = load_json(projected_path)
        projected = {
            str(item.get("_id") or ""): item
            for item in projected_payload.get("questions", [])
        }
        projected_by_grade[grade] = projected
        if set(projected) != set(live_by_grade[grade]):
            local_mismatches.append(f"{grade}:id_set")
            continue
        for question_id, live_document in live_by_grade[grade].items():
            if without_volatile_fields(projected[question_id]) != without_volatile_fields(
                live_document
            ):
                local_mismatches.append(f"{grade}:{question_id}")

        write_json(
            local_output_root / grade / "live_readback_questions.json",
            {
                "schemaVersion": f"{SCHEMA_VERSION}/live-readback-snapshot",
                "generatedAt": utc_now(),
                "planSha256": plan["planSha256"],
                "questions": sorted(
                    live_by_grade[grade].values(), key=lambda item: str(item.get("_id") or "")
                ),
            },
        )

    checks = {
        "operationMismatchCount": len(operation_errors),
        "explanationPrefixMismatchCount": len(prefix_mismatches),
        "trueFalseStatementMissingCount": len(statement_missing),
        "questionBodyTextMissingCount": len(body_missing),
        "questionSetRefPresentCount": len(question_set_ref_present),
        "mixedPublicationLegacyGroupCount": len(mixed_groups),
        "questionSetAndFolderCountMismatchCount": len(count_errors),
        "localProjectedContentMismatchCount": len(local_mismatches),
    }
    if any(checks.values()):
        raise RuntimeError(f"post verification failed: {checks}")
    if len(identity_groups) != 1044:
        raise RuntimeError(f"official identity coverage changed: {len(identity_groups)}")
    return {
        "schemaVersion": f"{SCHEMA_VERSION}/post-verification-receipt",
        "generatedAt": utc_now(),
        "planSha256": plan["planSha256"],
        "activeDisplayQuestionCount": active_count,
        "activeByGrade": {grade: len(items) for grade, items in active_by_grade.items()},
        "representedOfficialIdentityCount": len(identity_groups),
        "verifiedOperationCount": len(plan["updates"]) + len(plan["softDeletes"]),
        "checks": checks,
        "localLiveReadbackQuestionCount": sum(len(items) for items in live_by_grade.values()),
        "userDataWrites": 0,
        "hardDeletes": 0,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    result.add_argument("--credentials-json", type=Path, default=None)
    subparsers = result.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build-plan")
    build.add_argument("--kou-snapshot", type=Path, required=True)
    build.add_argument("--otsu-snapshot", type=Path, required=True)
    build.add_argument("--document-index", type=Path, required=True)
    build.add_argument("--local-root", type=Path, action="append", required=True)
    build.add_argument("--output", type=Path, required=True)

    local = subparsers.add_parser("apply-local")
    local.add_argument("--plan", type=Path, required=True)
    local.add_argument("--output-root", type=Path, required=True)
    local.add_argument("--receipt", type=Path, required=True)

    apply = subparsers.add_parser("apply-firestore")
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--receipt", type=Path, required=True)

    verify = subparsers.add_parser("verify-post")
    verify.add_argument("--plan", type=Path, required=True)
    verify.add_argument("--kou-snapshot", type=Path, required=True)
    verify.add_argument("--otsu-snapshot", type=Path, required=True)
    verify.add_argument("--document-index", type=Path, required=True)
    verify.add_argument("--local-output-root", type=Path, required=True)
    verify.add_argument("--receipt", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "build-plan":
        plan = build_plan(
            kou_snapshot=args.kou_snapshot,
            otsu_snapshot=args.otsu_snapshot,
            document_index=args.document_index,
            local_roots=args.local_root,
        )
        write_json(args.output, plan)
        print(json.dumps(plan["summary"], ensure_ascii=False, indent=2))
        return 0
    if args.command == "apply-local":
        receipt = apply_local(plan=load_json(args.plan), output_root=args.output_root)
        write_json(args.receipt, receipt)
        print(json.dumps({key: value for key, value in receipt.items() if key not in {"changedFiles", "changedOccurrencesByQuestionId"}}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "apply-firestore":
        receipt = apply_firestore(
            plan=load_json(args.plan),
            project_id=args.project_id,
            credentials_json=args.credentials_json,
        )
        write_json(args.receipt, receipt)
        print(json.dumps({key: value for key, value in receipt.items() if not key.endswith("QuestionIds") and key != "recovery"}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "verify-post":
        receipt = verify_post(
            plan=load_json(args.plan),
            kou_snapshot=args.kou_snapshot,
            otsu_snapshot=args.otsu_snapshot,
            document_index=args.document_index,
            local_output_root=args.local_output_root,
        )
        write_json(args.receipt, receipt)
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
