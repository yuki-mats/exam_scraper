#!/usr/bin/env python3
"""公式PDFで個別確認したガス主任技術者53問を安全に収束させる。"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from collections import Counter
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


SCHEMA_VERSION = "gas-shunin-individual-official-pdf-reconciliation/v1"
UPDATED_BY_ID = "aMpBCmAEGSQPbhUMzbHvFiM1cYK2"
EXPECTED_TARGET_COUNT = 53
EXPECTED_UPDATE_COUNT = 19
PRECONDITION_FIELDS = (
    "choiceNumber",
    "correctChoiceText",
    "examYear",
    "explanationText",
    "isChoiceOnly",
    "isDeleted",
    "originalQuestionBodyText",
    "originalQuestionChoiceText",
    "questionBodyText",
    "questionNumber",
    "questionSetId",
    "questionText",
    "questionType",
)
OFFICIAL_VERIFICATION_FIELDS = (
    "questionText",
    "originalQuestionBodyText",
    "questionBodyText",
    "originalQuestionChoiceText",
    "correctChoiceText",
    "explanationText",
    "questionSetId",
    "questionType",
    "examYear",
)


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


def official_content_hash(question: dict[str, Any]) -> str:
    return canonical_hash(selected_fields(question, OFFICIAL_VERIFICATION_FIELDS))


def plan_hash(plan: dict[str, Any]) -> str:
    return canonical_hash({key: value for key, value in plan.items() if key != "planSha256"})


def verify_plan_hash(plan: dict[str, Any]) -> None:
    actual = plan_hash(plan)
    if actual != plan.get("planSha256"):
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


def text_fields(document: dict[str, Any], *, body: str | None = None, choice: str | None = None) -> dict[str, str]:
    body_value = body if body is not None else str(document.get("originalQuestionBodyText") or document.get("questionBodyText") or "")
    choice_value = choice if choice is not None else str(document.get("originalQuestionChoiceText") or "")
    result = {
        "originalQuestionBodyText": body_value,
        "questionBodyText": body_value,
        "originalQuestionChoiceText": choice_value,
    }
    if document.get("questionType") == "true_false":
        result["questionText"] = f"{body_value}[quote]{choice_value}[/quote]"
    else:
        result["questionText"] = body_value
    return result


BODY_2022_Q1 = "法令で規定されているガス事業法の目的、用語の定義等に関する次の記述のうち、誤っているものはいくつあるか。"


REPAIR_SPECS: dict[str, dict[str, Any]] = {
    **{
        f"chiefgasengineerlicense-A-10-024{number}": {"body": BODY_2022_Q1}
        for number in range(1, 6)
    },
    "chiefgasengineerlicense-A-10-0244": {
        "body": BODY_2022_Q1,
        "choice": "「液化ガス」とは、常用の温度において、圧力が0.2 MPa以上となる液化ガスであって、現にその圧力が0.2 MPa以上であるもの又は圧力が0.2 MPaとなる場合の温度が35℃以下である液化ガスをいう。",
    },
    "chiefgasengineerlicense-A-10-0276": {
        "choice": "容器であって、最高使用圧力が0.2 MPa以上のガスを通ずるもの（内容積が0.04 m³以上又は内径が200 mm以上で、長さが1000 mm以上のものに限る。）",
        "explanationText": "正しい。最高使用圧力が0.2 MPa以上のガスを通ずる一定規模以上の容器は、溶接施工方法等の事前確認を要する対象に含まれる。この対象は、ガス工作物の技術上の基準を定める省令第16条第2項第1号イに定められている。",
    },
    "chiefgasengineerlicense-A-10-0277": {
        "choice": "容器であって、液化ガスを通ずるもの（最高使用圧力をメガパスカルで表した数値と内容積を立方メートルで表した数値との積が0.004以下のものを除く。）",
        "explanationText": "正しい。液化ガスを通ずる容器は、最高使用圧力をMPaで表した数値と内容積を立方メートルで表した数値との積が0.004以下のものを除き、溶接施工方法等の事前確認を要する対象に含まれる。この対象は、ガス工作物の技術上の基準を定める省令第16条第2項第1号ロに定められている。",
    },
    "chiefgasengineerlicense-A-40-1165": {
        "choice": "粒界腐食は、金属や合金の粒界又は粒界に沿った狭い部分が優先的に腐食する現象である。",
        "explanationText": "正しい。粒界腐食は、金属や合金を構成する結晶粒の境界、または粒界に沿った狭い部分が、結晶粒の内部よりも優先的に腐食する現象である。",
    },
    "chiefgasengineerlicense-A-40-1519": {
        "choice": "付臭室の空気を活性炭にて脱臭を行う場合は、活性炭層の破過に留意し、定期的な活性炭の交換を行う必要がある。",
        "explanationText": "正しい。付臭室の空気を活性炭で脱臭する場合、活性炭層が破過すると臭気成分の吸着性能が低下する。そのため、破過に留意し、定期的に活性炭を交換する必要がある。",
    },
    "chiefgasengineerlicense-A-80-1426": {
        "choice": "電気防食により管の電位を下げ過ぎると、鋼の表面に水素ガスが発生し、鋼の組織に拡散するとともに、塗覆装の剥離が発生しやすくなる。",
        "explanationText": "正しい。電気防食で管の電位を必要以上に低くすると過防食となり、鋼の表面で水素ガスが発生しやすくなる。発生した水素は鋼材に影響を及ぼすおそれがあり、塗覆装の密着性低下や剥離も生じやすくなるため、記述は正しい。",
    },
    "chiefgasengineerlicense-A-80-1428": {
        "choice": "地表面電位勾配とは、土壌、コンクリート等の電解質に設置した照合電極に対する導管の電位である。",
        "explanationText": "間違い。土壌やコンクリートなどの電解質中に設置した照合電極を基準として表す導管の電位は、管対地電位である。地表面電位勾配は地表面上の電位差や勾配を調べる概念であり、選択肢の定義とは異なる。",
    },
    "gas-shunin-kou-2017-law-q02-s03": {
        "choice": "ガス工作物(ガス栓を除く。)を操作することにより人が酸素欠乏症となった事故",
    },
    "gas-shunin-kou-2017-kiso-q11-s04": {
        "explanationText": "正しい。管摩擦による損失ヘッドは、ダルシー・ワイスバッハの式 h＝λ(L/D)×v²/(2g) で求める。内径100 mmは0.1 mなので、L/D＝10/0.1＝100である。速度ヘッドは v²/(2g)＝2²/(2×10)＝0.2 m となる。したがって、h＝0.03×100×0.2＝0.6 m であり、最も近い値は0.6である。",
    },
    "gasushunin-koushu-gizyutsu-2021-24-4": {
        "explanationText": "正しい。排気フードⅠ型の必要換気量は、V=30×K×Qで求める。ここで、排気フードⅠ型の定数は30、理論排ガス量Kは0.93m³/kWh、燃料消費量Qは5kWなので、V=30×0.93×5=139.5m³/hとなる。したがって、選択肢の中で最も近い値は140m³/hである。",
    },
    "gas-shunin-kou-2024-shohi-q20-s05": {
        "choice": "ガス種Aからガス種Bへの熱量変更する場合、インプットを一定にするためのノズル口径の変更率は、DB/DA=√(WIB√PB/WIA√PA)で計算される。",
        "explanationText": "間違い。インプットは、ノズル口径の二乗、ウォッベ指数、標準圧力の平方根に比例する。したがって、ガス種Aからガス種Bへ変更してインプットを一定にする場合は、DB/DA＝√(WIA√PA／WIB√PB)となる。選択肢の式は分子と分母が逆であるため誤りである。",
    },
    "gas-shunin-kou-2024-shohi-q24-s01": {
        "choice": "調理室の換気に排気フードⅠ型を使用すると、排気フードを使用しない場合と比べて、必要換気量を75%にすることができる。",
        "explanationText": "正しい。調理室の換気設備で排気フード付き排気筒に換気扇等を設ける場合、昭和45年建設省告示第1826号「換気設備の構造方法を定める件」第三第四号イでは有効換気量を V=NKQ とし、通常の排気口又は排気筒に換気扇等を設ける場合の第三第二号イの V=40KQ に対し、N=30の排気フードでは75%となる。この基準は建築基準法施行令第20条の3第2項第1号イに基づくため、記述は正しい。",
    },
    "gas-shunin-kou-2024-shohi-q24-s02": {
        "choice": "自然換気回数は、一般的に次式で表される。n=Q/V（n：自然換気回数（回/h）、Q：自然換気量（m³/h）、V：室の容積（m³））",
        "explanationText": "正しい。自然換気回数nは、一定時間内に入れ替わる空気量Qを室容積Vで割った値として表す。したがって、一般に n=Q/V で表される。",
    },
    "gas-shunin-kou-2025-shohi-q20-s05": {
        "choice": "フラッシュバックは、ガス・空気混合気体の噴出速度に比べて、燃焼速度がバランス点以下に遅くなった時に起こる現象である。",
        "explanationText": "間違い。フラッシュバックは、ガス・空気混合気の噴出速度より燃焼速度が速くなり、炎がバーナー内部へ燃え戻る現象である。記述は「燃焼速度がバランス点以下に遅くなった時」としており、発生条件が逆である。",
    },
    "chiefgasengineerlicense-C-10298": {
        "choice": "体積及び温度一定条件の下で化学反応が起こったとき、生成系の内部エネルギーのほうが反応系の内部エネルギーよりも低い場合、発熱反応である。",
        "explanationText": "正しい。体積一定では反応に伴う熱の出入りは内部エネルギー変化に対応し、生成系の内部エネルギーが反応系より低い場合は差分のエネルギーを放出するため発熱反応である。",
    },
}


def apply_spec(document: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    projected = copy.deepcopy(document)
    if "body" in spec or "choice" in spec:
        projected.update(
            text_fields(
                document,
                body=spec.get("body"),
                choice=spec.get("choice"),
            )
        )
    if "explanationText" in spec:
        projected["explanationText"] = spec["explanationText"]
    return projected


def load_audit_targets(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        if item.get("reviewIssues"):
            result[str(item["questionId"])] = item
    if len(result) != EXPECTED_TARGET_COUNT:
        raise ValueError(f"unexpected audit target count: {len(result)}")
    return result


def rendered_page_for(record: dict[str, Any], rendered_dir: Path) -> Path:
    rendered_dir = rendered_dir.resolve()
    question_pdf = record["officialQuestionPdf"]
    stem = Path(question_pdf["path"]).stem.lower()
    page = int(question_pdf["pdfPage"])
    matches = [
        path
        for path in rendered_dir.glob("*.png")
        if f"_{stem}_p{page:02d}" in path.stem.lower()
    ]
    if len(matches) != 1:
        raise ValueError(f"rendered official page is not unique: {record['questionId']}")
    return matches[0]


def decision_reason(audit: dict[str, Any], changed: bool) -> str:
    issues = set(audit.get("reviewIssues") or [])
    if changed:
        return "official_pdf_requires_question_or_explanation_repair"
    if "source_unmapped" in issues:
        return "live_content_matches_official_pdf_local_source_is_incomplete_or_typographical"
    if "calculation_explanation_style_requires_review" in issues:
        return "calculation_explanation_is_sufficient_after_dimensionless_or_formula_detection_fix"
    if "exact_live_duplicate" in issues:
        return "distinct_official_question_choice_shares_only_the_stem"
    raise ValueError(f"unsupported review issue: {audit.get('questionId')} {sorted(issues)}")


def build_plan(
    *,
    kou_snapshot: Path,
    otsu_snapshot: Path,
    audit_ledger: Path,
    document_index: Path,
    rendered_dir: Path,
) -> dict[str, Any]:
    snapshots = {"kou": kou_snapshot.resolve(), "otsu": otsu_snapshot.resolve()}
    raw_by_grade = {grade: raw_documents(path) for grade, path in snapshots.items()}
    active_by_grade = {grade: active_documents(path) for grade, path in snapshots.items()}
    current: dict[str, tuple[str, dict[str, Any], dict[str, Any]]] = {}
    for grade, documents in active_by_grade.items():
        for question_id, document in documents.items():
            current[question_id] = (grade, document, raw_by_grade[grade][question_id])

    audit_targets = load_audit_targets(audit_ledger)
    index_records = {
        str(item["questionId"]): item for item in load_json(document_index).get("records", [])
    }
    target_ids = set(audit_targets)
    if not target_ids <= set(current):
        raise ValueError(f"snapshot missing targets: {sorted(target_ids - set(current))}")
    if not target_ids <= set(index_records):
        raise ValueError(f"official index missing targets: {sorted(target_ids - set(index_records))}")
    if set(REPAIR_SPECS) - target_ids:
        raise ValueError(f"repair IDs outside target set: {sorted(set(REPAIR_SPECS)-target_ids)}")
    if len(REPAIR_SPECS) != EXPECTED_UPDATE_COUNT:
        raise ValueError(f"unexpected repair count: {len(REPAIR_SPECS)}")

    decisions: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    for question_id in sorted(target_ids):
        grade, document, raw = current[question_id]
        if document.get("isDeleted") is not False or document.get("isChoiceOnly") is not False:
            raise ValueError(f"target is not an active display question: {question_id}")
        record = index_records[question_id]
        for evidence_key in ("officialQuestionPdf", "officialAnswerPdf"):
            evidence_path = ROOT / record[evidence_key]["path"]
            if not evidence_path.exists() or file_hash(evidence_path) != record[evidence_key]["sha256"]:
                raise ValueError(f"official PDF hash mismatch: {question_id} {evidence_key}")
        rendered = rendered_page_for(record, rendered_dir)
        spec = REPAIR_SPECS.get(question_id)
        projected = apply_spec(document, spec) if spec else copy.deepcopy(document)
        changed_fields = {
            field: projected.get(field)
            for field in set(projected) | set(document)
            if projected.get(field) != document.get(field)
        }
        if bool(changed_fields) != bool(spec):
            raise ValueError(f"repair spec does not produce an exact change: {question_id}")
        evidence = {
            "officialQuestionPdf": record["officialQuestionPdf"],
            "officialAnswerPdf": record["officialAnswerPdf"],
            "officialQuestionNumber": record.get("questionNumber"),
            "officialCorrectChoiceNumber": record.get("officialCorrectChoiceNumber"),
            "renderedQuestionPage": {
                "path": str(rendered.relative_to(ROOT)),
                "sha256": file_hash(rendered),
            },
        }
        decision = {
            "questionId": question_id,
            "grade": grade,
            "examYear": document.get("examYear"),
            "questionNumber": record.get("questionNumber"),
            "choiceNumber": document.get("choiceNumber"),
            "verificationStatus": "official_pdf_verified",
            "action": "update" if changed_fields else "verified_no_change",
            "reason": decision_reason(audit_targets[question_id], bool(changed_fields)),
            "originalReviewIssues": audit_targets[question_id].get("reviewIssues") or [],
            "changedFields": sorted(changed_fields),
            "verifiedContentSha256": official_content_hash(projected),
            "officialEvidence": evidence,
            "verifiedContent": selected_fields(projected, OFFICIAL_VERIFICATION_FIELDS),
        }
        decisions.append(decision)
        if changed_fields:
            before = selected_fields(document, PRECONDITION_FIELDS)
            updates.append(
                {
                    "questionId": question_id,
                    "grade": grade,
                    "setFields": changed_fields,
                    "changedFields": sorted(changed_fields),
                    "precondition": before,
                    "preconditionSha256": canonical_hash(before),
                    "snapshotUpdateTime": raw.get("updateTime"),
                    "officialEvidence": evidence,
                }
            )

    if len(decisions) != EXPECTED_TARGET_COUNT or len(updates) != EXPECTED_UPDATE_COUNT:
        raise ValueError(f"unexpected result counts: decisions={len(decisions)} updates={len(updates)}")
    plan: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "projectId": DEFAULT_PROJECT_ID,
        "scope": "gas-shunin active display questions individually verified against official PDFs",
        "sourcePolicy": "official question and answer PDFs only; listing sites were not used",
        "filters": {"isDeleted": False, "isChoiceOnly": False},
        "sources": {
            "kouSnapshot": str(kou_snapshot.resolve()),
            "otsuSnapshot": str(otsu_snapshot.resolve()),
            "auditLedger": str(audit_ledger.resolve()),
            "officialDocumentIndex": str(document_index.resolve()),
            "renderedOfficialPages": str(rendered_dir.resolve()),
        },
        "summary": {
            "individualDecisionCount": len(decisions),
            "updateTargetCount": len(updates),
            "verifiedNoChangeCount": len(decisions) - len(updates),
            "targetCountByGrade": dict(Counter(item["grade"] for item in decisions)),
            "updateCountByGrade": dict(Counter(item["grade"] for item in updates)),
            "decisionReasonCounts": dict(Counter(item["reason"] for item in decisions)),
            "userDataWrites": 0,
            "hardDeletes": 0,
        },
        "decisions": decisions,
        "updates": updates,
        "recovery": {
            "beforeFieldsByQuestionId": {
                item["questionId"]: {
                    field: item["precondition"].get(field) for field in item["changedFields"]
                }
                for item in updates
            }
        },
    }
    plan["planSha256"] = plan_hash(plan)
    return plan


def firestore_client(project_id: str, credentials_json: Path | None):
    initialize_firebase_app(project_id=project_id, credentials_json=credentials_json)
    from firebase_admin import firestore

    return firestore.client(), firestore


def apply_firestore(
    *, plan: dict[str, Any], project_id: str, credentials_json: Path | None
) -> dict[str, Any]:
    verify_plan_hash(plan)
    updates = plan["updates"]
    db, firestore = firestore_client(project_id, credentials_json)
    refs = [db.collection("questions").document(item["questionId"]) for item in updates]
    snapshots = {
        snapshot.id: snapshot
        for snapshot in db.get_all(refs, field_paths=list(PRECONDITION_FIELDS))
    }
    pending: list[tuple[dict[str, Any], Any]] = []
    already_applied: list[str] = []
    for item in updates:
        question_id = item["questionId"]
        snapshot = snapshots.get(question_id)
        if snapshot is None or not snapshot.exists:
            raise RuntimeError(f"Firestore question missing: {question_id}")
        document = snapshot.to_dict() or {}
        current = selected_fields(document, PRECONDITION_FIELDS)
        if canonical_hash(current) == item["preconditionSha256"]:
            pending.append((item, snapshot))
            continue
        if all(document.get(field) == value for field, value in item["setFields"].items()):
            already_applied.append(question_id)
            continue
        raise RuntimeError(f"Firestore precondition mismatch: {question_id}")

    written: list[str] = []
    for item, snapshot in pending:
        payload = copy.deepcopy(item["setFields"])
        payload.update({"updatedAt": datetime.now(timezone.utc), "updatedById": UPDATED_BY_ID})
        snapshot.reference.update(
            payload, option=firestore.LastUpdateOption(snapshot.update_time)
        )
        written.append(item["questionId"])

    readback = {
        snapshot.id: snapshot
        for snapshot in db.get_all(refs, field_paths=list(OFFICIAL_VERIFICATION_FIELDS))
    }
    errors: list[str] = []
    readback_content: dict[str, dict[str, Any]] = {}
    for item in updates:
        document = readback[item["questionId"]].to_dict() or {}
        if any(document.get(field) != value for field, value in item["setFields"].items()):
            errors.append(item["questionId"])
        readback_content[item["questionId"]] = selected_fields(
            document, OFFICIAL_VERIFICATION_FIELDS
        )
    if errors:
        raise RuntimeError(f"Firestore readback failed: {errors}")
    return {
        "schemaVersion": f"{SCHEMA_VERSION}/firestore-apply-receipt",
        "generatedAt": utc_now(),
        "projectId": project_id,
        "planSha256": plan["planSha256"],
        "operationCount": len(updates),
        "writtenCount": len(written),
        "alreadyAppliedCount": len(already_applied),
        "readbackMatchCount": len(updates),
        "writtenQuestionIds": sorted(written),
        "alreadyAppliedQuestionIds": sorted(already_applied),
        "readbackContent": readback_content,
        "errors": errors,
        "userDataWrites": 0,
        "hardDeletes": 0,
    }


def write_local_mirror(*, plan: dict[str, Any], output_root: Path) -> dict[str, Any]:
    verify_plan_hash(plan)
    paths: list[str] = []
    for grade in ("kou", "otsu"):
        grade_decisions = [item for item in plan["decisions"] if item["grade"] == grade]
        payload = {
            "schemaVersion": f"{SCHEMA_VERSION}/local-mirror",
            "generatedAt": utc_now(),
            "planSha256": plan["planSha256"],
            "grade": grade,
            "sourcePolicy": plan["sourcePolicy"],
            "decisions": grade_decisions,
        }
        path = output_root / f"gas-shunin-{grade}" / "firestore_repairs" / "20260830_individual_official_pdf_reconciliation.json"
        write_json(path, payload)
        paths.append(str(path.resolve()))
    return {
        "schemaVersion": f"{SCHEMA_VERSION}/local-mirror-receipt",
        "generatedAt": utc_now(),
        "planSha256": plan["planSha256"],
        "decisionCount": len(plan["decisions"]),
        "writtenFiles": paths,
        "protected00SourceWriteCount": 0,
    }


def verify_post(
    *,
    plan: dict[str, Any],
    kou_snapshot: Path,
    otsu_snapshot: Path,
    audit_summary: Path,
) -> dict[str, Any]:
    verify_plan_hash(plan)
    snapshots = {"kou": kou_snapshot.resolve(), "otsu": otsu_snapshot.resolve()}
    active_by_grade = {grade: active_documents(path) for grade, path in snapshots.items()}
    current = {
        question_id: document
        for documents in active_by_grade.values()
        for question_id, document in documents.items()
    }
    mismatches: list[str] = []
    for decision in plan["decisions"]:
        question_id = decision["questionId"]
        document = current.get(question_id)
        if document is None or official_content_hash(document) != decision["verifiedContentSha256"]:
            mismatches.append(question_id)

    audit = load_json(audit_summary)
    validation = {
        grade: load_json(path / "validation_report.json") for grade, path in snapshots.items()
    }
    count_mismatches = {
        grade: {
            "folders": len(report.get("countMismatches", {}).get("folders", [])),
            "questionSets": len(report.get("countMismatches", {}).get("questionSets", [])),
        }
        for grade, report in validation.items()
    }
    checks = {
        "all53OfficialContentHashesMatch": not mismatches,
        "allActiveQuestionsPassAudit": audit.get("overallStatusCounts") == {
            "pass": audit.get("questionCount")
        },
        "contentIssueCount": sum(audit.get("contentIssueCounts", {}).values()),
        "reviewIssueCount": sum(audit.get("reviewIssueCounts", {}).values()),
        "schemaIssueCount": sum(audit.get("schemaIssueCounts", {}).values()),
        "answerMatchCount": audit.get("answerStatusCounts", {}).get("match"),
        "activeQuestionCount": audit.get("questionCount"),
        "folderCountMismatchCount": sum(item["folders"] for item in count_mismatches.values()),
        "questionSetCountMismatchCount": sum(item["questionSets"] for item in count_mismatches.values()),
        "exactDuplicateExcessCount": sum(
            grade.get("exactDuplicateExcessCount", 0)
            for grade in audit.get("grades", {}).values()
        ),
    }
    if (
        mismatches
        or not checks["allActiveQuestionsPassAudit"]
        or checks["contentIssueCount"]
        or checks["reviewIssueCount"]
        or checks["schemaIssueCount"]
        or checks["folderCountMismatchCount"]
        or checks["questionSetCountMismatchCount"]
        or checks["exactDuplicateExcessCount"]
    ):
        raise RuntimeError(f"post verification failed: checks={checks} mismatches={mismatches}")
    return {
        "schemaVersion": f"{SCHEMA_VERSION}/post-verification-receipt",
        "generatedAt": utc_now(),
        "planSha256": plan["planSha256"],
        "snapshots": {grade: str(path) for grade, path in snapshots.items()},
        "checks": checks,
        "officialContentHashMismatchQuestionIds": mismatches,
        "countMismatchesByGrade": count_mismatches,
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
    build.add_argument("--audit-ledger", type=Path, required=True)
    build.add_argument("--document-index", type=Path, required=True)
    build.add_argument("--rendered-dir", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)

    local = subparsers.add_parser("write-local-mirror")
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
    verify.add_argument("--audit-summary", type=Path, required=True)
    verify.add_argument("--receipt", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "build-plan":
        plan = build_plan(
            kou_snapshot=args.kou_snapshot,
            otsu_snapshot=args.otsu_snapshot,
            audit_ledger=args.audit_ledger,
            document_index=args.document_index,
            rendered_dir=args.rendered_dir,
        )
        write_json(args.output, plan)
        print(json.dumps(plan["summary"], ensure_ascii=False, indent=2))
        return 0
    if args.command == "write-local-mirror":
        receipt = write_local_mirror(plan=load_json(args.plan), output_root=args.output_root)
        write_json(args.receipt, receipt)
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0
    if args.command == "apply-firestore":
        receipt = apply_firestore(
            plan=load_json(args.plan),
            project_id=args.project_id,
            credentials_json=args.credentials_json,
        )
        write_json(args.receipt, receipt)
        print(json.dumps({key: value for key, value in receipt.items() if key != "readbackContent"}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "verify-post":
        receipt = verify_post(
            plan=load_json(args.plan),
            kou_snapshot=args.kou_snapshot,
            otsu_snapshot=args.otsu_snapshot,
            audit_summary=args.audit_summary,
        )
        write_json(args.receipt, receipt)
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
