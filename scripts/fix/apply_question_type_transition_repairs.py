#!/usr/bin/env python3
"""個別精査済みのquestionType修正を本番questionsへ限定反映する。"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
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


SCHEMA_VERSION = "question-type-transition-firestore-repair/v1"
UPDATED_BY_ID = "aMpBCmAEGSQPbhUMzbHvFiM1cYK2"
TARGET_QUALIFICATION = "gas-shunin-otsu"
EXPECTED_QUESTION_COUNT = 23
EXPECTED_DOCUMENT_COUNT = 115
DELETE_FIELDS = ("suggestedQuestionDetails", "suggestedQuestions")
PRECONDITION_FIELDS = (
    "qualificationId",
    "originalQuestionId",
    "originalQuestionBodyText",
    "originalQuestionChoiceText",
    "questionBodyText",
    "questionType",
    "isChoiceOnly",
    "isGroupable",
    "isDeleted",
    "correctChoiceText",
    "explanationText",
    "questionSetId",
    "questionText",
    "suggestedQuestionDetails",
    "suggestedQuestions",
    "lawRevisionFacts",
    "lawReferences",
)
AFTER_FIELDS = (
    "questionType",
    "isChoiceOnly",
    "isGroupable",
    "correctChoiceText",
    "explanationText",
    "questionSetId",
    "questionText",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        json_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("question_bodies", "patched_questions", "questions"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    raise ValueError("問題配列を特定できません。")


def original_id(record: dict[str, Any]) -> str:
    return str(
        record.get("original_question_id")
        or record.get("public_question_id")
        or record.get("originalQuestionId")
        or ""
    )


def find_record(path: Path, expected_id: str) -> dict[str, Any]:
    matches = [
        record
        for record in records(load_json(path))
        if original_id(record) == expected_id
    ]
    if len(matches) != 1:
        raise ValueError(
            f"{expected_id}: {path.relative_to(ROOT)} の一致件数が{len(matches)}件です。"
        )
    return matches[0]


def stage_path(question_type_path: str, stage: str, suffix: str) -> Path:
    relative = question_type_path.replace(
        "/10_questionType_fixed/", f"/{stage}/"
    ).replace("_questionType_fixed.json", suffix)
    return ROOT / relative


def presence_snapshot(
    document: dict[str, Any], fields: Iterable[str]
) -> dict[str, dict[str, Any]]:
    return {
        field: {"present": field in document, "value": json_safe(document.get(field))}
        for field in fields
    }


def choice_index(question_id: str) -> int:
    match = re.search(r"-s(\d+)$", question_id)
    if match is None:
        raise ValueError(f"choice suffixを確認できません: {question_id}")
    return int(match.group(1)) - 1


def explanation_verdict(text: str) -> str:
    value = text.lstrip()
    if value.startswith("正しい。"):
        return "正しい"
    if value.startswith("間違い。"):
        return "間違い"
    raise ValueError("true_false解説が正誤で始まっていません。")


def make_question_text(body: str, choice: str) -> str:
    return f"{body}[quote]{choice}[/quote]"


def plan_hash(plan: dict[str, Any]) -> str:
    return canonical_hash({key: value for key, value in plan.items() if key != "planSha256"})


def verify_plan_hash(plan: dict[str, Any]) -> None:
    actual = plan_hash(plan)
    if actual != plan.get("planSha256"):
        raise ValueError(f"plan hash mismatch: {plan.get('planSha256')} != {actual}")


def build_plan(review_path: Path, live_snapshot_path: Path) -> dict[str, Any]:
    review = load_json(review_path)
    live_snapshot = load_json(live_snapshot_path)
    live_by_original_id = {
        str(item["originalQuestionId"]): item
        for item in live_snapshot.get("targets") or []
    }
    review_rows = [
        row
        for row in review.get("reviews") or []
        if row.get("qualification") == TARGET_QUALIFICATION
        and row.get("reviewDecision") == "change"
        and row.get("questionTypeAfter") == "true_false"
    ]
    if len(review_rows) != EXPECTED_QUESTION_COUNT:
        raise ValueError(f"更新対象問題数が{len(review_rows)}問です。")

    operations: list[dict[str, Any]] = []
    for row in review_rows:
        expected_id = str(row["recordId"])
        qtype_path = str(row["questionTypePath"])
        qtype = find_record(ROOT / qtype_path, expected_id)
        explanation = find_record(
            stage_path(
                qtype_path,
                "21_explanationText_added",
                "_explanationText_added.json",
            ),
            expected_id,
        )
        correct = find_record(
            stage_path(
                qtype_path,
                "23_correctChoiceText_fixed",
                "_correctChoiceText_fixed.json",
            ),
            expected_id,
        )
        question_set = find_record(
            stage_path(
                qtype_path,
                "22_questionSetId_linked",
                "_questionSetId_linked.json",
            ),
            expected_id,
        )
        choices = qtype.get("choiceTextList")
        explanations = explanation.get("explanationText")
        verdicts = correct.get("correctChoiceText")
        choice_question_sets = question_set.get("choiceQuestionSetIds")
        if not all(isinstance(value, list) for value in (choices, explanations, verdicts)):
            raise ValueError(f"{expected_id}: 選択肢別fieldの型が不正です。")
        if not (len(choices) == len(explanations) == len(verdicts) == 5):
            raise ValueError(f"{expected_id}: 選択肢別fieldが5件ではありません。")
        if choice_question_sets is not None and (
            not isinstance(choice_question_sets, list)
            or len(choice_question_sets) != len(choices)
        ):
            raise ValueError(f"{expected_id}: choiceQuestionSetIdsが不正です。")

        target = live_by_original_id.get(expected_id)
        if target is None:
            raise ValueError(f"{expected_id}: live snapshotに対象がありません。")
        documents = [
            item
            for item in target.get("documents") or []
            if (item.get("data") or {}).get("qualificationId")
            == TARGET_QUALIFICATION
        ]
        if len(documents) != len(choices):
            raise ValueError(f"{expected_id}: 本番document数が{len(documents)}件です。")

        seen_indexes: set[int] = set()
        for item in documents:
            question_id = str(item["questionId"])
            index = choice_index(question_id)
            if index < 0 or index >= len(choices) or index in seen_indexes:
                raise ValueError(f"{expected_id}: choice suffixが不正です。")
            seen_indexes.add(index)
            document = item.get("data") or {}
            if (
                document.get("originalQuestionId") != expected_id
                or document.get("qualificationId") != TARGET_QUALIFICATION
                or document.get("isDeleted") is not False
                or document.get("questionType") != "group_choice"
                or document.get("originalQuestionChoiceText") != choices[index]
            ):
                raise ValueError(f"{question_id}: 本番preconditionが対象外です。")
            expected_verdict = str(verdicts[index])
            expected_explanation = str(explanations[index])
            if explanation_verdict(expected_explanation) != expected_verdict:
                raise ValueError(f"{question_id}: 正誤と解説が一致しません。")
            law_current = ((document.get("lawRevisionFacts") or {}).get("current") or {})
            law_verdict = law_current.get("correctChoiceText")
            if law_verdict is not None and law_verdict != expected_verdict:
                raise ValueError(f"{question_id}: 現行法監査の正誤と一致しません。")
            body = str(document.get("questionBodyText") or "")
            choice = str(choices[index])
            qset = (
                choice_question_sets[index]
                if isinstance(choice_question_sets, list)
                else question_set.get("questionSetId")
            )
            if not body or not choice or not qset:
                raise ValueError(f"{question_id}: 公開必須fieldが不足しています。")
            after = {
                "questionType": "true_false",
                "isChoiceOnly": False,
                "isGroupable": True,
                "correctChoiceText": expected_verdict,
                "explanationText": expected_explanation,
                "questionSetId": str(qset),
                "questionText": make_question_text(body, choice),
            }
            before = presence_snapshot(document, PRECONDITION_FIELDS)
            operations.append(
                {
                    "questionId": question_id,
                    "originalQuestionId": expected_id,
                    "choiceIndex": index,
                    "snapshotUpdateTime": str(item["updateTime"]),
                    "precondition": before,
                    "preconditionSha256": canonical_hash(before),
                    "setFields": after,
                    "deleteFields": [field for field in DELETE_FIELDS if field in document],
                }
            )
        if seen_indexes != set(range(len(choices))):
            raise ValueError(f"{expected_id}: 全選択肢を対応できません。")

    if len(operations) != EXPECTED_DOCUMENT_COUNT:
        raise ValueError(f"更新対象document数が{len(operations)}件です。")
    plan: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "projectId": DEFAULT_PROJECT_ID,
        "qualification": TARGET_QUALIFICATION,
        "scope": "個別精査でgroup_choice誤上書きを確認した既存questions documentのみ",
        "reviewArtifact": {
            "path": str(review_path.resolve()),
            "sha256": file_hash(review_path),
        },
        "liveSnapshot": {
            "path": str(live_snapshot_path.resolve()),
            "sha256": file_hash(live_snapshot_path),
        },
        "summary": {
            "questionCount": EXPECTED_QUESTION_COUNT,
            "documentCount": EXPECTED_DOCUMENT_COUNT,
            "userDataWrites": 0,
            "hardDeletes": 0,
        },
        "operations": sorted(operations, key=lambda item: item["questionId"]),
        "recovery": {
            "restorePresenceSnapshot": {
                item["questionId"]: item["precondition"] for item in operations
            }
        },
    }
    plan["planSha256"] = plan_hash(plan)
    return plan


def firestore_client(project_id: str, credentials_json: Path | None):
    initialize_firebase_app(project_id=project_id, credentials_json=credentials_json)
    from firebase_admin import firestore

    return firestore.client(), firestore


def apply_plan(
    plan: dict[str, Any], project_id: str, credentials_json: Path | None
) -> dict[str, Any]:
    verify_plan_hash(plan)
    if project_id != DEFAULT_PROJECT_ID or plan.get("projectId") != DEFAULT_PROJECT_ID:
        raise ValueError("本番projectIdが想定値と一致しません。")
    operations = plan.get("operations") or []
    if len(operations) != EXPECTED_DOCUMENT_COUNT:
        raise ValueError("Firestore更新対象件数が想定値と一致しません。")
    db, firestore = firestore_client(project_id, credentials_json)
    refs = [db.collection("questions").document(item["questionId"]) for item in operations]
    snapshots = {snapshot.id: snapshot for snapshot in db.get_all(refs)}
    batch = db.batch()
    already_applied: list[str] = []
    pending: list[str] = []
    for item in operations:
        question_id = str(item["questionId"])
        snapshot = snapshots.get(question_id)
        if snapshot is None or not snapshot.exists:
            raise RuntimeError(f"Firestore question missing: {question_id}")
        document = snapshot.to_dict() or {}
        current = presence_snapshot(document, PRECONDITION_FIELDS)
        is_after = all(
            document.get(field) == value
            for field, value in item["setFields"].items()
        ) and all(field not in document for field in item["deleteFields"])
        if is_after:
            already_applied.append(question_id)
            continue
        if (
            canonical_hash(current) != item["preconditionSha256"]
            or snapshot.update_time.isoformat() != item["snapshotUpdateTime"]
        ):
            raise RuntimeError(f"Firestore precondition mismatch: {question_id}")
        payload = copy.deepcopy(item["setFields"])
        for field in item["deleteFields"]:
            payload[field] = firestore.DELETE_FIELD
        payload.update(
            {"updatedAt": datetime.now(timezone.utc), "updatedById": UPDATED_BY_ID}
        )
        batch.update(
            snapshot.reference,
            payload,
            option=firestore.LastUpdateOption(snapshot.update_time),
        )
        pending.append(question_id)
    if pending:
        batch.commit()

    readback = {snapshot.id: snapshot for snapshot in db.get_all(refs)}
    errors = []
    for item in operations:
        document = readback[item["questionId"]].to_dict() or {}
        if any(
            document.get(field) != value
            for field, value in item["setFields"].items()
        ) or any(field in document for field in item["deleteFields"]):
            errors.append(item["questionId"])
    if errors:
        raise RuntimeError(f"Firestore readback failed: {errors}")
    return {
        "schemaVersion": f"{SCHEMA_VERSION}/apply-receipt",
        "generatedAt": utc_now(),
        "projectId": project_id,
        "qualification": TARGET_QUALIFICATION,
        "planSha256": plan["planSha256"],
        "operationCount": len(operations),
        "writtenCount": len(pending),
        "alreadyAppliedCount": len(already_applied),
        "readbackMatchCount": len(operations),
        "writtenQuestionIds": pending,
        "alreadyAppliedQuestionIds": already_applied,
        "errors": errors,
        "userDataWrites": 0,
        "hardDeletes": 0,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--live-snapshot", type=Path, required=True)
    parser.add_argument("--plan-output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--receipt-output", type=Path)
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--credentials-json", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    plan = build_plan(args.review, args.live_snapshot)
    write_json(args.plan_output, plan)
    print(
        json.dumps(
            {"plan": str(args.plan_output), **plan["summary"]},
            ensure_ascii=False,
        )
    )
    if not args.apply:
        return 0
    if args.receipt_output is None:
        raise ValueError("--applyには--receipt-outputが必要です。")
    receipt = apply_plan(plan, args.project_id, args.credentials_json)
    write_json(args.receipt_output, receipt)
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
