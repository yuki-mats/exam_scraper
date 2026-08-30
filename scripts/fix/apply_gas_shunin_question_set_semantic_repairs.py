#!/usr/bin/env python3
"""ガス主任技術者の問題セット意味監査をローカルとFirestoreへ安全に反映する。"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
import unicodedata
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


SCHEMA_VERSION = "gas-shunin-question-set-semantic-repair/v1"
UPDATED_BY_ID = "aMpBCmAEGSQPbhUMzbHvFiM1cYK2"
PROTECTED_COMPONENTS = {"00_source", "old", "firestore_snapshot"}
LOCAL_SKIP_COMPONENTS = PROTECTED_COMPONENTS | {"firestore_repairs"}
QUESTION_PRECONDITION_FIELDS = (
    "questionSetId",
    "questionType",
    "isDeleted",
    "isChoiceOnly",
)
QUESTION_SET_PRECONDITION_FIELDS = (
    "folderId",
    "isDeleted",
    "isOfficial",
    "name",
    "qualificationId",
    "questionCount",
)
FOLDER_PRECONDITION_FIELDS = (
    "isDeleted",
    "name",
    "qualificationId",
    "questionCount",
)
GRADE_CONFIG = {
    "甲種": {"key": "kou", "qualification": "gas-shunin-kou"},
    "乙種": {"key": "otsu", "qualification": "gas-shunin-otsu"},
}
EXPECTED_ACTIVE = {"kou": 2212, "otsu": 1913}

# 既存の論点別セットはIDを維持して再有効化する。B-1600だけは、負圧も含む
# 出題内容に合わせて表示名を「圧力異常防止」へ直す。
IDEAL_NAME_TO_TARGET = {
    "保安規程（甲種）": "chiefgasengineerlicense-A-10-130",
    "保安業務規程（乙種）": "chiefgasengineerlicense-B-1250",
    "技省令_ガスの逆流防止（乙種）": "chiefgasengineerlicense-B-1320",
    "技省令_ガスホルダー及び液化ガス用貯槽（乙種）": "chiefgasengineerlicense-B-1370",
    "技省令_低圧ガス発生設備等の圧力異常防止（乙種）": "chiefgasengineerlicense-B-1600",
    "技省令_保安電力等（乙種）": "chiefgasengineerlicense-B-1680",
    "技省令_液化ガスの流出防止措置（乙種）": "chiefgasengineerlicense-B-1420",
    "技省令_漏えい検査（乙種）": "chiefgasengineerlicense-B-1740",
    "技省令_漏えい液化ガスの回収（乙種）": "chiefgasengineerlicense-B-2200",
    "技省令_移動式ガス発生設備の設置等（乙種）": "chiefgasengineerlicense-B-1410",
    "技省令_計測装置等（乙種）": "chiefgasengineerlicense-B-1460",
    "技省令_誤操作防止及びインターロック（乙種）": "chiefgasengineerlicense-B-1480",
    "技省令_防護の基準（乙種）": "chiefgasengineerlicense-B-1690",
    "技省令_静電気除去措置（乙種）": "chiefgasengineerlicense-B-2190",
}
NEW_QUESTION_SETS = {
    "chiefgasengineerlicense-B-2190": {
        "folderId": "chiefgasengineerlicense-B-14",
        "isDeleted": False,
        "isOfficial": True,
        "name": "技省令_静電気除去措置（乙種）",
        "qualificationId": "gas-shunin-otsu",
    },
    "chiefgasengineerlicense-B-2200": {
        "folderId": "chiefgasengineerlicense-B-14",
        "isDeleted": False,
        "isOfficial": True,
        "name": "技省令_漏えい液化ガスの回収（乙種）",
        "qualificationId": "gas-shunin-otsu",
    },
}
MISSING_LOCAL_SOURCE_PATCHES = {
    "gas-shunin:kou:2018:law:q02": (
        "output/gas-shunin-kou/questions_json/2018/22_questionSetId_linked/"
        "question_2018_1_questionSetId_linked.json"
    ),
}
DISCOVERED_SEMANTIC_OVERRIDES = {
    "gas-shunin-otsu-2018-law-q06-s03": "chiefgasengineerlicense-B-1710",
    "gas-shunin-otsu-2018-law-q07-s05": "chiefgasengineerlicense-B-1320",
    "gas-shunin-otsu-2021-law-q11-s05": "chiefgasengineerlicense-B-1130",
    "gas-shunin-otsu-2023-law-q07-s03": "chiefgasengineerlicense-B-1760",
    "gas-shunin-otsu-2023-law-q08-s01": "chiefgasengineerlicense-B-1500",
}
REACTIVATED_QUESTION_SET_NAMES = {
    name: question_set_id
    for name, question_set_id in IDEAL_NAME_TO_TARGET.items()
    if question_set_id.startswith("chiefgasengineerlicense-B-")
    and question_set_id not in NEW_QUESTION_SETS
}
RETIRED_QUESTION_SETS = {
    "chiefgasengineerlicense-A-10-120",
    "chiefgasengineerlicense-A-40-185",
    "chiefgasengineerlicense-B-1230",
}
CANONICAL_RENAMES = {
    "chiefgasengineerlicense-A-40-174": "電気設備及び計装設備（甲種）",
    "chiefgasengineerlicense-B-1600": "技省令_低圧ガス発生設備等の圧力異常防止（乙種）",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_json_preserving_order(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "path"):
        return str(value.path)
    return value


def selected_fields(document: dict[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    return {field: json_safe(document.get(field)) for field in fields}


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(json_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


def normalize_choice_match(value: Any) -> str:
    return re.sub(
        r"[。．、，,.・「」『』（）()【】]",
        "",
        normalize_text(value).replace("～", "~").replace("〜", "~"),
    )


def question_number_from_id(question_id: str) -> int | None:
    match = re.search(r"-q(\d+)(?:-|$)", question_id)
    return int(match.group(1)) if match else None


def question_section_from_id(question_id: str) -> str | None:
    match = re.search(r"-(law|seizo|kyokyu|shohi|kiso)-q\d+", question_id)
    return match.group(1) if match else None


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def payload_hash(payload: dict[str, Any], field: str = "planSha256") -> str:
    return canonical_hash({key: value for key, value in payload.items() if key != field})


def verify_plan_hash(plan: dict[str, Any]) -> None:
    expected = str(plan.get("planSha256") or "")
    actual = payload_hash(plan)
    if not expected or expected != actual:
        raise ValueError(f"plan hash mismatch: expected={expected} actual={actual}")


def portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def protected_tree_manifest(local_roots: Iterable[Path]) -> dict[str, Any]:
    rows: list[tuple[str, str]] = []
    for local_root in sorted(path.resolve() for path in local_roots):
        for path in sorted(local_root.rglob("*.json")):
            if set(path.parts) & PROTECTED_COMPONENTS:
                rows.append((portable_path(path), file_hash(path)))
    return {"fileCount": len(rows), "treeSha256": canonical_hash(rows)}


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


def supplemental_target(grade: str, question: dict[str, Any]) -> tuple[str | None, str | None]:
    current = str(question.get("questionSetId") or "")
    body = str(question.get("questionBodyText") or question.get("originalQuestionBodyText") or "")
    choice = str(question.get("originalQuestionChoiceText") or "")
    text = f"{body} {choice}"
    if grade == "kou" and current == "chiefgasengineerlicense-A-10-120":
        if "保安規程" in choice or (
            "保安規程" in body and "ガス工作物及び保安規程" not in body
        ):
            return "chiefgasengineerlicense-A-10-130", "retire_mislabeled_hoan_kitei"
        return "chiefgasengineerlicense-A-10-010", "retire_mislabeled_hoan_kitei"
    if grade == "kou" and current == "chiefgasengineerlicense-A-40-185":
        return "chiefgasengineerlicense-A-40-174", "merge_synonymous_instrumentation_set"
    if grade == "otsu" and current == "chiefgasengineerlicense-B-1230":
        if "保安業務規程" in text:
            return "chiefgasengineerlicense-B-1250", "split_hoan_gyomu_kitei"
        if "保安規程" in choice or (
            "保安規程" in body and "ガス工作物及び保安規程" not in body
        ):
            return "chiefgasengineerlicense-B-1240", "split_hoan_kitei"
        return "chiefgasengineerlicense-B-1120", "split_gas_work_maintenance"
    return None, None


def audit_target(row: dict[str, Any]) -> str:
    ideal_name = str(row.get("idealQuestionSetDisplayName") or "").strip()
    if ideal_name:
        target = IDEAL_NAME_TO_TARGET.get(ideal_name)
        if not target:
            raise ValueError(f"ideal question-set name is unmapped: {ideal_name}")
        return target
    return str(row.get("recommendedQuestionSetId") or "").strip()


def official_evidence(index_by_id: dict[str, dict[str, Any]], question_id: str) -> dict[str, Any]:
    record = index_by_id.get(question_id)
    if record is None:
        raise ValueError(f"official PDF index is missing question: {question_id}")
    if record.get("match", {}).get("status") != "verified_question_block":
        raise ValueError(f"official PDF identity is not verified: {question_id}")
    question_pdf = record.get("officialQuestionPdf") or {}
    return {
        "examYear": record.get("examYear"),
        "grade": record.get("grade"),
        "section": record.get("section"),
        "questionNumber": record.get("questionNumber"),
        "questionPdfPath": question_pdf.get("path"),
        "questionPdfPage": question_pdf.get("pdfPage"),
        "questionPdfSha256": question_pdf.get("sha256"),
        "mappingStatus": record.get("match", {}).get("status"),
    }


def local_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for field in ("question_bodies", "questions", "items", "entries"):
            value = payload.get(field)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    raise ValueError("local question-set projection file has no record list")


def local_record_identity(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "sourceQuestionKey": str(record.get("sourceQuestionKey") or ""),
        "embeddedQuestionIds": sorted(embedded_question_ids(record)),
        "choiceTexts": [normalize_text(value) for value in record.get("choiceTextList", [])],
        "questionSetId": record.get("questionSetId"),
        "choiceQuestionSetIds": record.get("choiceQuestionSetIds"),
        "questionSetIds": record.get("questionSetIds"),
    }


def resolve_local_projection_target(
    projection: dict[str, Any], live_row: dict[str, Any]
) -> list[dict[str, Any]]:
    evidence = (live_row.get("sourceMatch") or {}).get("evidence") or {}
    path_values = sorted(
        {
            str(value)
            for value in evidence.values()
            if isinstance(value, str)
            and ("/22_questionSetId_linked/" in value or "/23_correctChoiceText_fixed/" in value)
        }
    )
    if not path_values:
        path_value = MISSING_LOCAL_SOURCE_PATCHES.get(projection["sourceQuestionKey"], "")
        if not path_value:
            raise ValueError(
                f"local question-set evidence is missing: {projection['sourceQuestionKey']}"
            )
        path = ROOT / path_value
        if path.exists():
            raise ValueError(f"planned local patch already exists: {path_value}")
        return [
            {
                "operation": "create_patch",
                "path": path_value,
                "recordIndex": 0,
                "preconditionSha256": None,
            }
        ]

    targets: list[dict[str, Any]] = []
    for path_value in path_values:
        required_path = path_value in {
            str(evidence.get("questionSetId") or ""),
            str(evidence.get("choiceQuestionSetIds") or ""),
        }
        path = ROOT / path_value
        records = local_records(load_json(path))
        question_ids = set(projection["questionIds"])
        choice_key = tuple(normalize_choice_match(value) for value in projection.get("choiceTexts", []))
        choice_texts_by_index = projection.get("choiceTextsByIndex") or {}
        identity_candidates: list[int] = []
        choice_candidates: list[int] = []
        body_candidates: list[int] = []
        number_candidates: list[int] = []
        for index, record in enumerate(records):
            if (
                str(record.get("sourceQuestionKey") or "") == projection["sourceQuestionKey"]
                or embedded_question_ids(record) & question_ids
            ):
                identity_candidates.append(index)
            record_choices = [normalize_choice_match(value) for value in record.get("choiceTextList", [])]
            full_choice_match = bool(choice_key and tuple(record_choices) == choice_key)
            sparse_choice_match = bool(
                choice_texts_by_index
                and all(
                    int(index) <= len(record_choices)
                    and record_choices[int(index) - 1] == normalize_choice_match(choice)
                    for index, choice in choice_texts_by_index.items()
                )
            )
            if full_choice_match or sparse_choice_match:
                choice_candidates.append(index)
            if normalize_text(
                record.get("questionBodyText") or record.get("originalQuestionBodyText")
            ) == normalize_text(projection.get("bodyText")):
                body_candidates.append(index)
            record_number = record.get("questionNo")
            if record_number is None:
                key_match = re.search(r":q0*(\d+)(?:$|:)", str(record.get("sourceQuestionKey") or ""))
                record_number = int(key_match.group(1)) if key_match else None
            record_key = str(record.get("sourceQuestionKey") or "")
            section = projection.get("questionSection")
            section_matches = not section or f":{section}:" in record_key
            if record_number == projection.get("questionNumber") and section_matches:
                number_candidates.append(index)
        candidates = (
            identity_candidates
            if len(identity_candidates) == 1
            else choice_candidates
            if len(choice_candidates) == 1
            else number_candidates
            if len(number_candidates) == 1
            else body_candidates
        )
        if len(candidates) != 1:
            if not required_path and not candidates:
                continue
            raise ValueError(
                "local source record is not unique: "
                f"{projection['sourceQuestionKey']}:{path_value}:"
                f"identity={identity_candidates}:choices={choice_candidates}:"
                f"number={number_candidates}:body={body_candidates}"
            )
        record_index = candidates[0]
        record = records[record_index]
        if not ({"questionSetId", "choiceQuestionSetIds", "questionSetIds"} & set(record)):
            continue
        targets.append(
            {
                "operation": "update",
                "path": path_value,
                "recordIndex": record_index,
                "preconditionSha256": canonical_hash(local_record_identity(record)),
            }
        )
    if not targets:
        patch_path = MISSING_LOCAL_SOURCE_PATCHES.get(projection["sourceQuestionKey"])
        if patch_path:
            path = ROOT / patch_path
            if path.exists():
                raise ValueError(f"planned local patch already exists: {patch_path}")
            return [
                {
                    "operation": "create_patch",
                    "path": patch_path,
                    "recordIndex": 0,
                    "preconditionSha256": None,
                }
            ]
        raise ValueError(f"local classification record is missing: {projection['sourceQuestionKey']}")
    return targets


def build_source_projections(
    *,
    live_audit_rows: list[dict[str, Any]],
    target_by_question_id: dict[str, str],
) -> list[dict[str, Any]]:
    by_question_id = {str(row["questionId"]): row for row in live_audit_rows}
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in live_audit_rows:
        source_match = row.get("sourceMatch") or {}
        source_key = str(source_match.get("sourceKey") or "")
        if source_match.get("status") == "matched" and source_key:
            groups[(str(row["qualification"]), source_key)].append(row)

    changed_groups: set[tuple[str, str]] = set()
    for question_id in target_by_question_id:
        row = by_question_id.get(question_id)
        if row is None:
            raise ValueError(f"post-live audit is missing changed question: {question_id}")
        source_match = row.get("sourceMatch") or {}
        if source_match.get("status") != "matched" or not source_match.get("sourceKey"):
            raise ValueError(f"changed question is not mapped to local source: {question_id}")
        changed_groups.add((str(row["qualification"]), str(source_match["sourceKey"])))

    projections: list[dict[str, Any]] = []
    for qualification, source_key in sorted(changed_groups):
        rows = groups[(qualification, source_key)]
        qtypes = {str(row.get("questionType") or "") for row in rows}
        target_rows = []
        for row in rows:
            question_id = str(row["questionId"])
            target_rows.append(
                {
                    "questionId": question_id,
                    "choiceIndex": int((row.get("sourceMatch") or {}).get("choiceIndex") or 0),
                    "questionSetId": target_by_question_id.get(
                        question_id, str(row.get("questionSetId") or "")
                    ),
                    "currentQuestionSetId": str(row.get("questionSetId") or ""),
                    "choiceText": str((row.get("live") or {}).get("originalQuestionChoiceText") or ""),
                    "bodyText": str((row.get("live") or {}).get("originalQuestionBodyText") or ""),
                }
            )
        if qtypes == {"true_false"}:
            by_index: dict[int, set[str]] = defaultdict(set)
            for row in target_rows:
                if row["choiceIndex"] <= 0:
                    raise ValueError(f"choice index is missing: {qualification}:{source_key}")
                by_index[row["choiceIndex"]].add(row["questionSetId"])
            if any(len(values) != 1 for values in by_index.values()):
                raise ValueError(f"choice-level question-set conflict: {qualification}:{source_key}")
            indexes = sorted(by_index)
            choice_targets = {
                str(index): next(iter(by_index[index])) for index in indexes
            }
            contiguous = indexes == list(range(1, max(indexes) + 1))
            choice_ids = [choice_targets[str(index)] for index in indexes] if contiguous else None
            projection = {
                "qualification": qualification,
                "sourceQuestionKey": source_key,
                "questionType": "true_false",
                "questionNumber": question_number_from_id(target_rows[0]["questionId"]),
                "questionSection": question_section_from_id(target_rows[0]["questionId"]),
                "bodyText": target_rows[0]["bodyText"],
                "questionSetId": choice_targets.get("1", target_rows[0]["currentQuestionSetId"]),
                "choiceQuestionSetIds": choice_ids,
                "choiceQuestionSetIdsByIndex": choice_targets,
                "choiceTextsByIndex": {
                    str(row["choiceIndex"]): row["choiceText"] for row in target_rows
                },
                "choiceTexts": [
                    next(
                        row["choiceText"]
                        for row in target_rows
                        if row["choiceIndex"] == index
                    )
                    for index in indexes
                ] if contiguous else [],
                "questionIds": sorted(row["questionId"] for row in target_rows),
            }
            projection["localTargets"] = resolve_local_projection_target(
                projection, by_question_id[projection["questionIds"][0]]
            )
            projections.append(projection)
            continue
        if "true_false" in qtypes or len(qtypes) != 1:
            raise ValueError(f"mixed question types in source group: {qualification}:{source_key}:{qtypes}")
        question_type = next(iter(qtypes))
        if question_type == "group_choice":
            by_index: dict[int, set[str]] = defaultdict(set)
            for row in target_rows:
                if row["choiceIndex"] <= 0:
                    raise ValueError(f"group choice index is missing: {qualification}:{source_key}")
                by_index[row["choiceIndex"]].add(row["questionSetId"])
            if any(len(values) != 1 for values in by_index.values()):
                raise ValueError(f"group choice classification conflict: {qualification}:{source_key}")
            choice_targets = {
                str(index): next(iter(values)) for index, values in sorted(by_index.items())
            }
            projection = {
                "qualification": qualification,
                "sourceQuestionKey": source_key,
                "questionType": question_type,
                "questionNumber": question_number_from_id(target_rows[0]["questionId"]),
                "questionSection": question_section_from_id(target_rows[0]["questionId"]),
                "bodyText": target_rows[0]["bodyText"],
                "questionSetId": choice_targets.get("1", target_rows[0]["currentQuestionSetId"]),
                "choiceQuestionSetIds": None,
                "choiceQuestionSetIdsByIndex": choice_targets,
                "choiceTextsByIndex": {
                    str(row["choiceIndex"]): row["choiceText"] for row in target_rows
                },
                "choiceTexts": [],
                "questionIds": sorted(row["questionId"] for row in target_rows),
            }
            projection["localTargets"] = resolve_local_projection_target(
                projection, by_question_id[projection["questionIds"][0]]
            )
            projections.append(projection)
            continue
        target_ids = {row["questionSetId"] for row in target_rows}
        if len(target_ids) != 1:
            raise ValueError(f"group question has multiple target sets: {qualification}:{source_key}")
        projection = {
            "qualification": qualification,
            "sourceQuestionKey": source_key,
            "questionType": question_type,
            "questionNumber": question_number_from_id(target_rows[0]["questionId"]),
            "questionSection": question_section_from_id(target_rows[0]["questionId"]),
            "bodyText": target_rows[0]["bodyText"],
            "questionSetId": next(iter(target_ids)),
            "choiceQuestionSetIds": None,
            "choiceTexts": [row["choiceText"] for row in target_rows],
            "questionIds": sorted(row["questionId"] for row in target_rows),
        }
        projection["localTargets"] = resolve_local_projection_target(
            projection, by_question_id[projection["questionIds"][0]]
        )
        projections.append(projection)
    return projections


def build_plan(
    *,
    semantic_audit: Path,
    live_audit: Path,
    official_index: Path,
    kou_snapshot: Path,
    otsu_snapshot: Path,
    local_roots: list[Path],
) -> dict[str, Any]:
    snapshots = {"kou": kou_snapshot.resolve(), "otsu": otsu_snapshot.resolve()}
    questions_by_grade: dict[str, dict[str, dict[str, Any]]] = {}
    raw_questions_by_grade: dict[str, dict[str, dict[str, Any]]] = {}
    all_questions: dict[str, tuple[str, dict[str, Any]]] = {}
    for grade, snapshot in snapshots.items():
        questions = active_questions(snapshot)
        if len(questions) != EXPECTED_ACTIVE[grade]:
            raise ValueError(f"active question count drift: {grade}:{len(questions)}")
        questions_by_grade[grade] = {str(item["questionId"]): item for item in questions}
        raw_questions_by_grade[grade] = raw_documents(snapshot, "questions.json")
        for question in questions:
            question_id = str(question["questionId"])
            if question_id in all_questions:
                raise ValueError(f"question ID is duplicated across grades: {question_id}")
            all_questions[question_id] = (grade, question)

    audit_rows = load_jsonl(semantic_audit)
    if len(audit_rows) != 442 or len({row["questionId"] for row in audit_rows}) != 442:
        raise ValueError("semantic audit must contain 442 unique questions")
    target_by_question_id: dict[str, str] = {}
    local_target_by_question_id: dict[str, str] = {}
    reason_by_question_id: dict[str, list[str]] = defaultdict(list)
    audit_change_ids: set[str] = set()
    for row in audit_rows:
        question_id = str(row["questionId"])
        if question_id not in all_questions:
            raise ValueError(f"audited question is not active: {question_id}")
        _, question = all_questions[question_id]
        current = str(question.get("questionSetId") or "")
        if current != str(row.get("currentQuestionSetId") or ""):
            raise ValueError(f"semantic audit current ID drift: {question_id}:{current}")
        target = audit_target(row)
        if not target:
            raise ValueError(f"semantic audit target is blank: {question_id}")
        local_target_by_question_id[question_id] = target
        if target != current:
            target_by_question_id[question_id] = target
            audit_change_ids.add(question_id)
            reason_by_question_id[question_id].append("442_question_semantic_audit")

    supplemental_ids: set[str] = set()
    for question_id, (grade, question) in all_questions.items():
        target, reason = supplemental_target(grade, question)
        if target is None or target == str(question.get("questionSetId") or ""):
            continue
        existing = target_by_question_id.get(question_id)
        if existing and existing != target:
            raise ValueError(f"supplemental target conflicts with audit: {question_id}")
        target_by_question_id[question_id] = target
        local_target_by_question_id[question_id] = target
        reason_by_question_id[question_id].append(str(reason))
        if question_id not in audit_change_ids:
            supplemental_ids.add(question_id)

    discovered_semantic_ids: set[str] = set()
    for question_id, target in DISCOVERED_SEMANTIC_OVERRIDES.items():
        if question_id not in all_questions:
            raise ValueError(f"discovered semantic question is not active: {question_id}")
        _, question = all_questions[question_id]
        current = str(question.get("questionSetId") or "")
        local_target_by_question_id[question_id] = target
        if target == current:
            continue
        existing = target_by_question_id.get(question_id)
        if existing and existing != target:
            raise ValueError(f"discovered semantic target conflicts: {question_id}")
        target_by_question_id[question_id] = target
        reason_by_question_id[question_id].append("strict_choice_mapping_followup_official_pdf_audit")
        discovered_semantic_ids.add(question_id)

    if len(audit_change_ids) != 201:
        raise ValueError(f"unexpected audited question update count: {len(audit_change_ids)}")
    if len(supplemental_ids) != 34:
        raise ValueError(f"unexpected duplicate-set supplemental count: {len(supplemental_ids)}")
    if len(discovered_semantic_ids) != 3:
        raise ValueError(f"unexpected strict-mapping followup count: {len(discovered_semantic_ids)}")
    if len(target_by_question_id) != 238:
        raise ValueError(f"unexpected total question update count: {len(target_by_question_id)}")

    official_payload = load_json(official_index)
    official_by_id = {str(item["questionId"]): item for item in official_payload.get("records", [])}
    question_updates: list[dict[str, Any]] = []
    for question_id, target in sorted(target_by_question_id.items()):
        grade, question = all_questions[question_id]
        raw = raw_questions_by_grade[grade].get(question_id)
        if raw is None:
            raise ValueError(f"raw snapshot question is missing: {question_id}")
        before = selected_fields(question, QUESTION_PRECONDITION_FIELDS)
        question_updates.append(
            {
                "grade": grade,
                "questionId": question_id,
                "beforeQuestionSetId": before["questionSetId"],
                "afterQuestionSetId": target,
                "reasons": sorted(set(reason_by_question_id[question_id])),
                "precondition": before,
                "preconditionSha256": canonical_hash(before),
                "snapshotUpdateTime": raw.get("updateTime"),
                "officialEvidence": official_evidence(official_by_id, question_id),
            }
        )

    # 全表示問題へ投影し、questionSet・folderの件数を独立に再計算する。
    projected_counts: Counter[str] = Counter()
    for question_id, (_, question) in all_questions.items():
        projected_counts[target_by_question_id.get(question_id, str(question.get("questionSetId") or ""))] += 1
    for retired_id in RETIRED_QUESTION_SETS:
        if projected_counts[retired_id] != 0:
            raise ValueError(f"retired question set still has active questions: {retired_id}")

    raw_qsets: dict[str, dict[str, Any]] = {}
    raw_folders: dict[str, dict[str, Any]] = {}
    qsets: dict[str, dict[str, Any]] = {}
    folders: dict[str, dict[str, Any]] = {}
    qset_grade: dict[str, str] = {}
    for grade, snapshot in snapshots.items():
        raw_qsets.update(raw_documents(snapshot, "questionSets.json"))
        raw_folders.update(raw_documents(snapshot, "folders.json"))
        category = load_json(snapshot / "reconstructed" / "category.json")
        for item in category.get("questionSets", []):
            question_set_id = str(item["questionSetId"])
            qsets[question_set_id] = copy.deepcopy(item)
            qset_grade[question_set_id] = grade
        for item in category.get("folders", []):
            folders[str(item["folderId"])] = copy.deepcopy(item)

    for question_set_id, definition in NEW_QUESTION_SETS.items():
        if question_set_id in qsets or question_set_id in raw_qsets:
            raise ValueError(f"new question-set ID already exists: {question_set_id}")
        qsets[question_set_id] = {"questionSetId": question_set_id, **copy.deepcopy(definition)}
        qset_grade[question_set_id] = "otsu"

    target_ids = set(projected_counts)
    missing_targets = sorted(target_ids - set(qsets))
    if missing_targets:
        raise ValueError(f"target question sets are missing: {missing_targets}")

    desired_qsets: dict[str, dict[str, Any]] = {}
    for question_set_id, item in qsets.items():
        desired = copy.deepcopy(item)
        desired["questionCount"] = int(projected_counts.get(question_set_id, 0))
        if question_set_id in target_ids:
            desired["isDeleted"] = False
        if question_set_id in RETIRED_QUESTION_SETS:
            desired["isDeleted"] = True
            desired["questionCount"] = 0
        if question_set_id in CANONICAL_RENAMES:
            desired["name"] = CANONICAL_RENAMES[question_set_id]
        for ideal_name, target_id in REACTIVATED_QUESTION_SET_NAMES.items():
            if question_set_id == target_id:
                desired["name"] = ideal_name
        desired_qsets[question_set_id] = desired

    folder_counts: Counter[str] = Counter()
    for question_set_id, desired in desired_qsets.items():
        if desired.get("isDeleted") is False:
            folder_counts[str(desired.get("folderId") or "")] += int(desired.get("questionCount") or 0)
    if sum(folder_counts.values()) != sum(EXPECTED_ACTIVE.values()):
        raise ValueError("projected folder counts do not cover all active display questions")

    question_set_mutations: list[dict[str, Any]] = []
    for question_set_id, desired in sorted(desired_qsets.items()):
        raw = raw_qsets.get(question_set_id)
        before = (
            selected_fields(raw.get("decoded") or {}, QUESTION_SET_PRECONDITION_FIELDS)
            if raw is not None
            else None
        )
        after = selected_fields(desired, QUESTION_SET_PRECONDITION_FIELDS)
        if before == after:
            continue
        question_set_mutations.append(
            {
                "grade": qset_grade[question_set_id],
                "questionSetId": question_set_id,
                "operation": "create" if raw is None else "update",
                "precondition": before,
                "preconditionSha256": canonical_hash(before) if before is not None else None,
                "snapshotUpdateTime": raw.get("updateTime") if raw else None,
                "after": after,
            }
        )

    folder_mutations: list[dict[str, Any]] = []
    for folder_id, item in sorted(folders.items()):
        raw = raw_folders[folder_id]
        before = selected_fields(raw.get("decoded") or {}, FOLDER_PRECONDITION_FIELDS)
        desired = copy.deepcopy(item)
        desired["questionCount"] = int(folder_counts.get(folder_id, 0))
        after = selected_fields(desired, FOLDER_PRECONDITION_FIELDS)
        if before == after:
            continue
        folder_mutations.append(
            {
                "folderId": folder_id,
                "precondition": before,
                "preconditionSha256": canonical_hash(before),
                "snapshotUpdateTime": raw.get("updateTime"),
                "after": after,
            }
        )

    source_projections = build_source_projections(
        live_audit_rows=load_jsonl(live_audit),
        target_by_question_id=local_target_by_question_id,
    )
    plan: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "projectId": DEFAULT_PROJECT_ID,
        "scope": "gas-shunin 442-question semantic audit plus duplicate-set closure",
        "sourcePolicy": "official question PDFs only; listing sites are not used",
        "sources": {
            "semanticAudit": portable_path(semantic_audit),
            "liveAudit": portable_path(live_audit),
            "officialDocumentIndex": portable_path(official_index),
            "kouSnapshot": portable_path(kou_snapshot),
            "otsuSnapshot": portable_path(otsu_snapshot),
        },
        "localRoots": [portable_path(path) for path in local_roots],
        "protectedLocalTree": protected_tree_manifest(local_roots),
        "summary": {
            "auditedQuestionCount": len(audit_rows),
            "auditedQuestionUpdateCount": len(audit_change_ids),
            "duplicateSetClosureSupplementalCount": len(supplemental_ids),
            "strictChoiceMappingFollowupUpdateCount": len(discovered_semantic_ids),
            "questionUpdateCount": len(question_updates),
            "questionUpdateByGrade": dict(Counter(item["grade"] for item in question_updates)),
            "questionSetMutationCount": len(question_set_mutations),
            "questionSetCreateCount": sum(item["operation"] == "create" for item in question_set_mutations),
            "folderMutationCount": len(folder_mutations),
            "sourceProjectionCount": len(source_projections),
            "activeDisplayQuestionCount": sum(EXPECTED_ACTIVE.values()),
            "hardDeleteCount": 0,
            "userDataWriteCount": 0,
        },
        "questionUpdates": question_updates,
        "questionSetMutations": question_set_mutations,
        "folderMutations": folder_mutations,
        "sourceProjections": source_projections,
        "desiredCategories": {
            grade: {
                "questionSets": sorted(
                    [item for question_set_id, item in desired_qsets.items() if qset_grade[question_set_id] == grade],
                    key=lambda item: (str(item.get("folderId") or ""), str(item.get("questionSetId") or "")),
                ),
                "folderCounts": {
                    folder_id: int(folder_counts.get(folder_id, 0))
                    for folder_id in sorted(folders)
                    if folder_id.startswith("chiefgasengineerlicense-A-" if grade == "kou" else "chiefgasengineerlicense-B-")
                },
            }
            for grade in ("kou", "otsu")
        },
        "recovery": {
            "questionSetIdBeforeByQuestionId": {
                item["questionId"]: item["beforeQuestionSetId"] for item in question_updates
            },
            "questionSetBeforeById": {
                item["questionSetId"]: item["precondition"] for item in question_set_mutations
            },
            "folderBeforeById": {
                item["folderId"]: item["precondition"] for item in folder_mutations
            },
        },
    }
    plan["planSha256"] = payload_hash(plan)
    return plan


def looks_like_question_record(item: dict[str, Any]) -> bool:
    return bool(
        (item.get("_id") or item.get("questionId"))
        and ({"questionText", "questionType", "isDeleted", "correctChoiceText"} & set(item))
    )


def embedded_question_ids(item: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for field in (
        "original_question_id",
        "reviewQuestionId",
        "questionId",
        "publicQuestionId",
        "public_question_id",
    ):
        value = str(item.get(field) or "").strip()
        if value.startswith("firestore:"):
            result.update(
                part.strip()
                for part in value.removeprefix("firestore:").split(",")
                if part.strip()
            )
        elif value:
            result.add(value)
    for field in ("firestoreQuestionIds", "questionIds"):
        values = item.get(field)
        if isinstance(values, list):
            result.update(str(value).strip() for value in values if str(value or "").strip())
    return result


def apply_projection_to_record(
    record: dict[str, Any], projection: dict[str, Any]
) -> Counter[str]:
    counts: Counter[str] = Counter()
    source_key = str(projection["sourceQuestionKey"])
    target = projection["questionSetId"]
    if record.get("questionSetId") != target:
        record["questionSetId"] = target
        counts[f"source:{source_key}:questionSetId"] += 1
    choice_ids = projection.get("choiceQuestionSetIds")
    choice_targets = projection.get("choiceQuestionSetIdsByIndex") or {}
    if choice_targets and not choice_ids:
        existing_ids = record.get("choiceQuestionSetIds") or record.get("questionSetIds")
        choice_count = len(record.get("choiceTextList") or [])
        if not choice_count:
            choice_count = max(int(index) for index in choice_targets)
        if isinstance(existing_ids, list) and len(existing_ids) == choice_count:
            choice_ids = copy.deepcopy(existing_ids)
        else:
            choice_ids = [str(record.get("questionSetId") or target)] * choice_count
        for index, question_set_id in choice_targets.items():
            if int(index) <= choice_count:
                choice_ids[int(index) - 1] = question_set_id
    if choice_ids:
        choice_field = "choiceQuestionSetIds"
        if "questionSetIds" in record and "choiceQuestionSetIds" not in record:
            choice_field = "questionSetIds"
        if record.get(choice_field) != choice_ids:
            record[choice_field] = copy.deepcopy(choice_ids)
            counts[f"source:{source_key}:{choice_field}"] += 1
        record["questionSetIdResolution"] = "choiceQuestionSetIds"
        record["questionSetReviewDecision"] = "choiceQuestionSetIds"
        record["questionSetReviewNote"] = (
            "Official-PDF semantic audit: preserve the reviewed per-choice learning units."
        )
    elif "choiceQuestionSetIds" in record:
        record.pop("choiceQuestionSetIds", None)
        counts[f"source:{source_key}:choiceQuestionSetIdsRemoved"] += 1
    elif "questionSetIds" in record:
        record.pop("questionSetIds", None)
        counts[f"source:{source_key}:questionSetIdsRemoved"] += 1
    return counts


def apply_local(*, plan: dict[str, Any]) -> dict[str, Any]:
    verify_plan_hash(plan)
    local_roots = [ROOT / path if not Path(path).is_absolute() else Path(path) for path in plan["localRoots"]]
    before_protected = protected_tree_manifest(local_roots)
    if before_protected != plan.get("protectedLocalTree"):
        raise RuntimeError("protected local tree changed after plan creation")
    changed_files: list[dict[str, Any]] = []
    changed_occurrences: Counter[str] = Counter()

    for _, config in GRADE_CONFIG.items():
        key = config["key"]
        qualification = config["qualification"]
        qualification_root = ROOT / "output" / qualification
        category_path = qualification_root / "category" / "category.json"
        category = load_json(category_path)
        desired = plan["desiredCategories"][key]
        desired_by_id = {item["questionSetId"]: item for item in desired["questionSets"]}
        existing_by_id = {
            str(item["questionSetId"]): item for item in category.get("questionSets", [])
        }
        category_changed = False
        for question_set_id, desired_item in desired_by_id.items():
            existing = existing_by_id.get(question_set_id)
            if existing is None:
                local_item = copy.deepcopy(desired_item)
                local_item["qualificationId"] = "chiefgasengineerlicense"
                local_item["updatedAt"] = plan["generatedAt"]
                category.setdefault("questionSets", []).append(local_item)
                category_changed = True
                continue
            item_changed = False
            for field in ("folderId", "isDeleted", "isOfficial", "name", "questionCount"):
                if existing.get(field) != desired_item.get(field):
                    existing[field] = copy.deepcopy(desired_item.get(field))
                    item_changed = True
            if item_changed:
                existing["updatedAt"] = plan["generatedAt"]
                category_changed = True
        folder_counts = desired["folderCounts"]
        for folder in category.get("folders", []):
            folder_id = str(folder["folderId"])
            target_count = int(folder_counts[folder_id])
            if folder.get("questionCount") != target_count:
                folder["questionCount"] = target_count
                folder["updatedAt"] = plan["generatedAt"]
                category_changed = True
        if category_changed:
            category.setdefault("metadata", {})["generatedAt"] = plan["generatedAt"]
        if {item["questionSetId"] for item in category["questionSets"]} != set(desired_by_id):
            raise AssertionError("category question-set projection failed")
        before_sha = file_hash(category_path)
        before_text = category_path.read_text(encoding="utf-8")
        after_text = json.dumps(category, ensure_ascii=False, indent=2) + "\n"
        if before_text != after_text:
            category_path.write_text(after_text, encoding="utf-8")
            changed_files.append(
                {
                    "path": portable_path(category_path),
                    "beforeSha256": before_sha,
                    "afterSha256": file_hash(category_path),
                    "changedFieldOccurrenceCount": 1,
                }
            )

    projections_by_path: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for projection in plan["sourceProjections"]:
        for target in projection["localTargets"]:
            projections_by_path[str(target["path"])].append((projection, target))
    for path_value, projection_targets in sorted(projections_by_path.items()):
        path = ROOT / path_value
        operations = {target["operation"] for _, target in projection_targets}
        if operations == {"create_patch"}:
            if len(projection_targets) != 1 or path.exists():
                raise RuntimeError(f"local patch create precondition mismatch: {path_value}")
            projection, _ = projection_targets[0]
            record = {"sourceQuestionKey": projection["sourceQuestionKey"]}
            counts = apply_projection_to_record(record, projection)
            write_json_preserving_order(path, [record])
            changed_files.append(
                {
                    "path": path_value,
                    "beforeSha256": None,
                    "afterSha256": file_hash(path),
                    "changedFieldOccurrenceCount": sum(counts.values()),
                }
            )
            changed_occurrences.update(counts)
            continue
        if operations != {"update"}:
            raise RuntimeError(f"mixed local projection operations: {path_value}:{operations}")
        payload = load_json(path)
        records = local_records(payload)
        before_sha = file_hash(path)
        file_counts: Counter[str] = Counter()
        seen_indexes: set[int] = set()
        for projection, target in projection_targets:
            record_index = int(target["recordIndex"])
            if record_index in seen_indexes:
                raise RuntimeError(f"duplicate local record target: {path_value}#{record_index}")
            seen_indexes.add(record_index)
            record = records[record_index]
            if canonical_hash(local_record_identity(record)) != target["preconditionSha256"]:
                raise RuntimeError(f"local record precondition mismatch: {path_value}#{record_index}")
            file_counts.update(apply_projection_to_record(record, projection))
        if not file_counts:
            continue
        write_json_preserving_order(path, payload)
        changed_files.append(
            {
                "path": path_value,
                "beforeSha256": before_sha,
                "afterSha256": file_hash(path),
                "changedFieldOccurrenceCount": sum(file_counts.values()),
            }
        )
        changed_occurrences.update(file_counts)

    after_protected = protected_tree_manifest(local_roots)
    if after_protected != before_protected:
        raise RuntimeError("protected local tree changed during local apply")
    return {
        "schemaVersion": f"{SCHEMA_VERSION}/local-apply-receipt",
        "generatedAt": utc_now(),
        "planSha256": plan["planSha256"],
        "changedFileCount": len(changed_files),
        "changedFieldOccurrenceCount": sum(changed_occurrences.values()),
        "changedFiles": changed_files,
        "changedOccurrences": dict(sorted(changed_occurrences.items())),
        "protectedLocalTreeBefore": before_protected,
        "protectedLocalTreeAfter": after_protected,
        "sourceProjectionCount": len(plan["sourceProjections"]),
        "questionUpdateCount": len(plan["questionUpdates"]),
    }


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
    if project_id != plan.get("projectId"):
        raise ValueError(f"project ID differs from plan: {project_id} != {plan.get('projectId')}")
    db, firestore = firestore_client(project_id, credentials_json)

    question_items = list(plan["questionUpdates"])
    question_refs = [db.collection("questions").document(item["questionId"]) for item in question_items]
    question_snapshots = {
        snapshot.id: snapshot
        for snapshot in db.get_all(question_refs, field_paths=list(QUESTION_PRECONDITION_FIELDS))
    }
    pending_questions: list[tuple[dict[str, Any], Any]] = []
    already_questions: list[str] = []
    for item in question_items:
        snapshot = question_snapshots.get(item["questionId"])
        if snapshot is None or not snapshot.exists:
            raise RuntimeError(f"Firestore question is missing: {item['questionId']}")
        current = selected_fields(snapshot.to_dict() or {}, QUESTION_PRECONDITION_FIELDS)
        if canonical_hash(current) == item["preconditionSha256"]:
            pending_questions.append((item, snapshot))
        elif current.get("questionSetId") == item["afterQuestionSetId"]:
            already_questions.append(item["questionId"])
        else:
            raise RuntimeError(f"Firestore question precondition mismatch: {item['questionId']}")

    qset_items = list(plan["questionSetMutations"])
    qset_refs = [db.collection("questionSets").document(item["questionSetId"]) for item in qset_items]
    qset_snapshots = {
        snapshot.id: snapshot
        for snapshot in db.get_all(
            qset_refs, field_paths=list(QUESTION_SET_PRECONDITION_FIELDS)
        )
    }
    pending_qsets: list[tuple[dict[str, Any], Any | None]] = []
    already_qsets: list[str] = []
    for item in qset_items:
        snapshot = qset_snapshots.get(item["questionSetId"])
        if item["operation"] == "create":
            if snapshot is None or not snapshot.exists:
                pending_qsets.append((item, None))
            elif selected_fields(snapshot.to_dict() or {}, QUESTION_SET_PRECONDITION_FIELDS) == item["after"]:
                already_qsets.append(item["questionSetId"])
            else:
                raise RuntimeError(f"new Firestore question set already exists: {item['questionSetId']}")
            continue
        if snapshot is None or not snapshot.exists:
            raise RuntimeError(f"Firestore question set is missing: {item['questionSetId']}")
        current = selected_fields(snapshot.to_dict() or {}, QUESTION_SET_PRECONDITION_FIELDS)
        if canonical_hash(current) == item["preconditionSha256"]:
            pending_qsets.append((item, snapshot))
        elif current == item["after"]:
            already_qsets.append(item["questionSetId"])
        else:
            raise RuntimeError(f"Firestore question-set precondition mismatch: {item['questionSetId']}")

    folder_items = list(plan["folderMutations"])
    folder_refs = [db.collection("folders").document(item["folderId"]) for item in folder_items]
    folder_snapshots = {
        snapshot.id: snapshot
        for snapshot in db.get_all(folder_refs, field_paths=list(FOLDER_PRECONDITION_FIELDS))
    }
    pending_folders: list[tuple[dict[str, Any], Any]] = []
    already_folders: list[str] = []
    for item in folder_items:
        snapshot = folder_snapshots.get(item["folderId"])
        if snapshot is None or not snapshot.exists:
            raise RuntimeError(f"Firestore folder is missing: {item['folderId']}")
        current = selected_fields(snapshot.to_dict() or {}, FOLDER_PRECONDITION_FIELDS)
        if canonical_hash(current) == item["preconditionSha256"]:
            pending_folders.append((item, snapshot))
        elif current == item["after"]:
            already_folders.append(item["folderId"])
        else:
            raise RuntimeError(f"Firestore folder precondition mismatch: {item['folderId']}")

    # 先に参照先セットを作成・再有効化し、問題が一時的に削除済みセットを参照しないようにする。
    activation_items = [
        (item, snapshot)
        for item, snapshot in pending_qsets
        if item["after"].get("isDeleted") is False
    ]
    for group in chunks(activation_items):
        batch = db.batch()
        now = datetime.now(timezone.utc)
        for item, snapshot in group:
            payload = copy.deepcopy(item["after"])
            payload.update({"updatedAt": now, "updatedById": UPDATED_BY_ID})
            ref = db.collection("questionSets").document(item["questionSetId"])
            if snapshot is None:
                batch.set(ref, payload)
            else:
                batch.update(ref, payload, option=firestore.LastUpdateOption(snapshot.update_time))
        batch.commit()

    for group in chunks(pending_questions):
        batch = db.batch()
        now = datetime.now(timezone.utc)
        for item, snapshot in group:
            batch.update(
                snapshot.reference,
                {
                    "questionSetId": item["afterQuestionSetId"],
                    "updatedAt": now,
                    "updatedById": UPDATED_BY_ID,
                },
                option=firestore.LastUpdateOption(snapshot.update_time),
            )
        batch.commit()

    # 削除済み化するセットと、最終件数をここで確定する。
    retirement_items = [
        (item, snapshot)
        for item, snapshot in pending_qsets
        if item["after"].get("isDeleted") is True
    ]
    for group in chunks(retirement_items):
        batch = db.batch()
        now = datetime.now(timezone.utc)
        for item, snapshot in group:
            payload = copy.deepcopy(item["after"])
            payload.update({"updatedAt": now, "updatedById": UPDATED_BY_ID})
            if snapshot is None:
                raise AssertionError("retirement cannot create a question set")
            batch.update(snapshot.reference, payload, option=firestore.LastUpdateOption(snapshot.update_time))
        batch.commit()

    for group in chunks(pending_folders):
        batch = db.batch()
        now = datetime.now(timezone.utc)
        for item, snapshot in group:
            payload = copy.deepcopy(item["after"])
            payload.update({"updatedAt": now, "updatedById": UPDATED_BY_ID})
            batch.update(snapshot.reference, payload, option=firestore.LastUpdateOption(snapshot.update_time))
        batch.commit()

    # Firestoreの更新時刻が変わるため、全対象を読み直して意味fieldだけを照合する。
    question_readback = {
        snapshot.id: snapshot
        for snapshot in db.get_all(question_refs, field_paths=["questionSetId", "isDeleted", "isChoiceOnly"])
    }
    qset_readback = {
        snapshot.id: snapshot
        for snapshot in db.get_all(
            qset_refs, field_paths=list(QUESTION_SET_PRECONDITION_FIELDS)
        )
    }
    folder_readback = {
        snapshot.id: snapshot
        for snapshot in db.get_all(folder_refs, field_paths=list(FOLDER_PRECONDITION_FIELDS))
    }
    errors: list[str] = []
    for item in question_items:
        document = question_readback[item["questionId"]].to_dict() or {}
        if document.get("questionSetId") != item["afterQuestionSetId"]:
            errors.append(f"questions/{item['questionId']}")
    for item in qset_items:
        document = qset_readback[item["questionSetId"]].to_dict() or {}
        if selected_fields(document, QUESTION_SET_PRECONDITION_FIELDS) != item["after"]:
            errors.append(f"questionSets/{item['questionSetId']}")
    for item in folder_items:
        document = folder_readback[item["folderId"]].to_dict() or {}
        if selected_fields(document, FOLDER_PRECONDITION_FIELDS) != item["after"]:
            errors.append(f"folders/{item['folderId']}")
    if errors:
        raise RuntimeError(f"Firestore readback mismatch: {errors[:20]}")
    return {
        "schemaVersion": f"{SCHEMA_VERSION}/firestore-apply-receipt",
        "generatedAt": utc_now(),
        "planSha256": plan["planSha256"],
        "projectId": project_id,
        "questionWriteCount": len(pending_questions),
        "questionAlreadyAppliedCount": len(already_questions),
        "questionSetWriteCount": len(pending_qsets),
        "questionSetAlreadyAppliedCount": len(already_qsets),
        "folderWriteCount": len(pending_folders),
        "folderAlreadyAppliedCount": len(already_folders),
        "readbackMismatchCount": len(errors),
        "hardDeleteCount": 0,
        "userDataWriteCount": 0,
        "changedQuestionIds": sorted(item["questionId"] for item, _ in pending_questions),
        "changedQuestionSetIds": sorted(item["questionSetId"] for item, _ in pending_qsets),
        "changedFolderIds": sorted(item["folderId"] for item, _ in pending_folders),
        "recovery": plan.get("recovery"),
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    subparsers = root.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build-plan")
    build.add_argument("--semantic-audit", type=Path, required=True)
    build.add_argument("--live-audit", type=Path, required=True)
    build.add_argument("--official-index", type=Path, required=True)
    build.add_argument("--kou-snapshot", type=Path, required=True)
    build.add_argument("--otsu-snapshot", type=Path, required=True)
    build.add_argument("--local-root", type=Path, action="append", required=True)
    build.add_argument("--output", type=Path, required=True)

    local = subparsers.add_parser("apply-local")
    local.add_argument("--plan", type=Path, required=True)
    local.add_argument("--receipt", type=Path, required=True)

    firestore_parser = subparsers.add_parser("apply-firestore")
    firestore_parser.add_argument("--plan", type=Path, required=True)
    firestore_parser.add_argument("--receipt", type=Path, required=True)
    firestore_parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    firestore_parser.add_argument("--credentials-json", type=Path)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "build-plan":
        plan = build_plan(
            semantic_audit=args.semantic_audit,
            live_audit=args.live_audit,
            official_index=args.official_index,
            kou_snapshot=args.kou_snapshot,
            otsu_snapshot=args.otsu_snapshot,
            local_roots=args.local_root,
        )
        write_json(args.output, plan)
        print(json.dumps(plan["summary"], ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "apply-local":
        receipt = apply_local(plan=load_json(args.plan))
        write_json(args.receipt, receipt)
        print(json.dumps({key: value for key, value in receipt.items() if key not in {"changedFiles", "changedOccurrences"}}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "apply-firestore":
        receipt = apply_firestore(
            plan=load_json(args.plan),
            project_id=args.project_id,
            credentials_json=args.credentials_json,
        )
        write_json(args.receipt, receipt)
        print(json.dumps({key: value for key, value in receipt.items() if key not in {"changedQuestionIds", "changedQuestionSetIds", "changedFolderIds", "recovery"}}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
