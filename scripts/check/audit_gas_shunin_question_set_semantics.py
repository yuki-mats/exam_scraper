#!/usr/bin/env python3
"""Audit flagged gas-shunin question-set assignments without writing Firestore."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TARGET_ISSUE = "question_set_classification_requires_review"
GRADE_CONFIG = {
    "甲種": ("gas-shunin-kou", "kou"),
    "乙種": ("gas-shunin-otsu", "otsu"),
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def base_name(name: str) -> str:
    return re.sub(r"（[甲乙]種）$", "", name).strip()


def category_maps() -> tuple[dict[str, dict[str, str]], dict[str, dict[str, Any]]]:
    by_grade: dict[str, dict[str, str]] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for grade, (qualification, _) in GRADE_CONFIG.items():
        payload = load_json(ROOT / "output" / qualification / "category" / "category.json")
        grade_map: dict[str, str] = {}
        for item in payload["questionSets"]:
            if item.get("isDeleted") is not False:
                continue
            question_set_id = str(item["questionSetId"])
            name = str(item["name"])
            grade_map[base_name(name)] = question_set_id
            by_id[question_set_id] = item
        by_grade[grade] = grade_map
    return by_grade, by_id


def source_question_sets() -> dict[tuple[str, str, int], str]:
    import importlib.util
    import sys

    check_dir = ROOT / "scripts" / "check"
    sys.path.insert(0, str(check_dir))
    source_path = check_dir / "audit_gas_shunin_firestore_live_questions.py"
    spec = importlib.util.spec_from_file_location("gas_live_audit", source_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {source_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    result: dict[tuple[str, str, int], str] = {}
    for _, (qualification, _) in GRADE_CONFIG.items():
        catalog = module.load_source_catalog(qualification)
        for row in module.source_rows(catalog):
            result[(qualification, str(row["sourceKey"]), int(row["choiceIndex"]))] = str(
                row.get("questionSetId") or ""
            )
    return result


def choose(available: dict[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        if name in available:
            return available[name]
    return None


def contains(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None


def technical_law_candidate(
    grade: str,
    available: dict[str, str],
    body: str,
    choice: str,
    explanation: str,
) -> tuple[str | None, str, str | None]:
    text = " ".join((choice, explanation))
    whole = " ".join((body, text))

    if contains(r"供用中の荷重.*最高使用温度|構造は、?供用中の荷重", body):
        return choose(available, ("技省令_構造等",)), "設問全体が構造基準の適用対象を問う", None
    if contains(r"構造は", choice):
        return choose(available, ("技省令_構造等",)), "選択肢がガス工作物の構造要件を直接問う", None
    if contains(r"漏えい検査", body) and not contains(r"整圧器", choice):
        candidate = choose(available, ("技省令_漏えい検査", "技省令_導管"))
        return candidate, "設問全体が漏えい検査の対象・周期を問う", None if grade == "甲種" else "乙種taxonomyに漏えい検査の独立分野がない"
    if contains(r"主要材料|材料に及ぼす.*影響", choice):
        return choose(available, ("技省令_材料",)), "ガス工作物の主要材料に求める性質を問う", None
    if contains(r"ガスメーター", choice):
        return choose(available, ("ガスメーター", "技省令_遮断装置")), "ガスメーターの安全機能を問う", None
    if contains(r"ガス栓|迅速継手|ゴム管口", choice):
        return choose(available, ("接続具及びガス栓", "ガス栓、接続具及び警報器", "技省令_遮断装置")), "ガス栓又は接続部の構造を問う", None
    if contains(r"緊急時に迅速な通信|通信設備", text):
        return choose(available, ("技省令_保安通信設備",)), "保安通信設備を直接問う", None
    if contains(r"防消火設備|消火設備", text):
        return choose(available, ("技省令_防消火設備", "製造設備の保安及び防災")), "防消火設備を直接問う", None
    if contains(r"漏えい.*検知.*(?:警報|放出)|警報する設備|警報装置|状態を検知し警報", text):
        return choose(available, ("技省令_警報装置", "製造設備の保安及び防災")), "漏えい検知・警報設備を直接問う", None
    if contains(r"安全に回収", text):
        return choose(available, ("製造設備の保安及び防災",)), "漏えいした液化ガスの安全回収を問う", "安全回収の独立分野がないため製造防災へ収容"
    if contains(r"滞留しない|ガスの滞留", text):
        return choose(available, ("技省令_ガスの滞留防止",)), "漏えいガスの滞留防止を問う", None
    if contains(r"安全に(?:置換|放出)|廃棄できる構造", text):
        return choose(available, ("技省令_ガスの置換等", "技省令_ガス発生設備等")), "ガスの置換・安全放出を問う", None
    if contains(r"逆流", text):
        candidate = choose(available, ("技省令_ガスの逆流防止", "技省令_ガス発生設備等"))
        return candidate, "ガスの逆流防止を問う", None if grade == "甲種" else "乙種taxonomyに逆流防止の独立分野がない"
    if contains(r"ガスホルダー.*(?:流出|流入).*遮断|ガスホルダー.*遮断", text):
        return choose(available, ("技省令_ガスホルダーの遮断装置",)), "ガスホルダー配管の遮断を問う", None
    if contains(r"低圧の(?:移動式)?ガス発生設備.*(?:負圧|過圧|圧力上昇防止装置)|負圧防止装置", text):
        candidate = choose(available, ("技省令_低圧ガス発生設備等の圧力上昇防止装置", "技省令_ガス発生設備等"))
        return candidate, "低圧ガス発生設備の圧力異常防止を問う", None if grade == "甲種" else "乙種taxonomyに圧力異常防止の独立分野がない"
    if contains(r"安全弁|圧力を逃", text):
        return choose(available, ("技省令_安全弁",)), "安全弁による過圧防止を問う", None
    if contains(r"耐圧試験", whole):
        return choose(available, ("技省令_耐圧試験",)), "耐圧試験の対象又は方法を問う", None
    if contains(r"気密試験", whole):
        return choose(available, ("技省令_気密試験", "技省令_耐圧試験")), "気密試験を問う", None
    if contains(r"防爆", text):
        return choose(available, ("技省令_電気設備の防爆構造", "電気設備の防爆構造", "電気設備及び計装設備")), "電気設備の防爆性能を問う", None
    if contains(r"水取り器", text):
        return choose(available, ("技省令_水取り器",)), "水取り器を直接問う", None
    if contains(r"みだりに操作", text):
        return choose(available, ("技省令_立ち入りの防止等",)), "公衆による設備操作の防止を問う", None
    if contains(r"整圧器", text):
        return choose(available, ("技省令_整圧器",)), "整圧器の設置・附属設備を問う", None
    if contains(r"漏えい検査|基準日前.*月", text):
        candidate = choose(available, ("技省令_漏えい検査", "技省令_導管"))
        return candidate, "導管の漏えい検査を問う", None if grade == "甲種" else "乙種taxonomyに漏えい検査の独立分野がない"
    if contains(r"腐食を防止|腐食を生ずる", text):
        return choose(available, ("腐食と防食",)), "設備の腐食防止措置を問う", None
    if contains(r"保安物件|離隔|必要な距離|距離を有", text):
        return choose(available, ("技省令_離隔距離",)), "保安物件等との離隔距離を問う", None
    if contains(r"防液堤", text):
        candidate = choose(available, ("技省令_液化ガスの流出防止措置", "技省令_構造等"))
        return candidate, "防液堤による液化ガス災害の拡大防止を問う", None if grade == "甲種" else "乙種taxonomyに流出防止の独立分野がない"
    if contains(r"危急の場合.*遮断|ガスの供給.*遮断することができる.*装置", text):
        return choose(available, ("技省令_ガス遮断装置等", "技省令_遮断装置")), "危急時のガス遮断装置を問う", None
    if contains(r"つり防護|受け防護|抜出しを防止", text):
        candidate = choose(available, ("技省令_防護の基準", "技省令_導管"))
        return candidate, "露出導管の防護・抜出し防止を問う", None if grade == "甲種" else "乙種taxonomyに防護の独立分野がない"
    if contains(r"保安区画|適切な区画", text):
        return choose(available, ("技省令_保安区画", "保安区画")), "保安区画を直接問う", None
    if contains(r"溶接", choice):
        return choose(available, ("技省令_溶接部分",)), "溶接部分の技術基準を問う", None
    if contains(r"本支管|供給管|内管|道路.*埋設|地盤面下.*埋設|導管", text):
        return choose(available, ("技省令_導管",)), "導管の設置・防護基準を問う", None
    if contains(r"みだりに立ち入|みだりに操作", text):
        return choose(available, ("技省令_立ち入りの防止等",)), "立入り又は誤操作の防止を問う", None
    if contains(r"停電|保安電力", text):
        candidate = choose(available, ("技省令_保安電力等", "製造設備の保安及び防災"))
        return candidate, "停電時も保安設備の機能を維持する措置を問う", None if grade == "甲種" else "乙種taxonomyに保安電力の独立分野がない"
    if contains(r"操作用電源", text):
        return choose(available, ("技省令_操作用電源停止時の措置",)), "操作用電源停止時の措置を問う", None
    if contains(r"誤操作|インターロック", text):
        if grade == "乙種" and contains(r"計装回路", text):
            candidate = choose(available, ("電気設備及び計装設備",))
        else:
            candidate = choose(available, ("技省令_誤操作防止及びインターロック", "技省令_遮断装置"))
        return candidate, "誤操作防止又はインターロックを問う", None if grade == "甲種" else "乙種taxonomyにインターロックの独立分野がない"
    if contains(r"緊急停止|異常.*(?:停止|処理)", text):
        return choose(available, ("技省令_緊急停止装置", "技省令_ガス発生設備等")), "異常時の緊急停止・安全処理を問う", None
    if contains(r"計測|使用の状態を.*(?:確認|記録)|液位|圧力計", text):
        candidate = choose(available, ("技省令_計測装置等",))
        return candidate, "設備状態の計測・確認を問う", None if grade == "甲種" else "乙種taxonomyに法令上の計測装置の独立分野がない"
    if contains(r"移動式ガス発生設備", text) and not contains(r"移動式ガス発生設備を除く", text):
        candidate = choose(available, ("技省令_移動式ガス発生設備の設置等", "技省令_ガス発生設備等"))
        return candidate, "移動式ガス発生設備の設置・保安を問う", None if grade == "甲種" else "乙種taxonomyに移動式設備の独立分野がない"
    if contains(r"液化ガス.{0,30}流出", text):
        candidate = choose(available, ("技省令_液化ガスの流出防止措置", "技省令_構造等", "製造設備の保安及び防災"))
        return candidate, "液化ガスの流出・拡大防止を問う", None if grade == "甲種" else "乙種taxonomyに流出防止の独立分野がない"
    if contains(r"特定ガス発生設備", text):
        candidate = choose(available, ("技省令_特定ガス発生設備", "技省令_ガス発生設備等"))
        return candidate, "特定ガス発生設備を問う", None if grade == "甲種" else "乙種taxonomyに特定設備の独立分野がない"
    if contains(r"気化装置|液化ガス.*気化する装置", text):
        candidate = choose(available, ("技省令_気化装置の構造", "技省令_ガス発生設備等"))
        return candidate, "気化装置の構造を問う", None if grade == "甲種" else "乙種taxonomyに気化装置の独立分野がない"
    if contains(r"ガス発生設備", text):
        return choose(available, ("技省令_ガス発生設備等",)), "ガス発生設備を問う", None
    if contains(r"熱に対し十分に耐|冷却装置|耐熱", text):
        return choose(available, ("技省令_耐熱措置", "技省令_構造等")), "設備の耐熱・冷却措置を問う", None
    if contains(r"構造|支持物|低温貯槽", text):
        return choose(available, ("技省令_構造等",)), "設備の構造・表示等を問う", None
    if contains(r"材料", text):
        return choose(available, ("技省令_材料",)), "材料の適合性を問う", None
    if contains(r"昇圧供給装置", text):
        return choose(available, ("技省令_昇圧供給装置",)), "昇圧供給装置の能力・点検・保護を問う", None
    if contains(r"付臭", text):
        return choose(available, ("技省令_付臭措置", "都市ガスの付臭")), "付臭措置を問う", None
    if contains(r"液化ガス用貯槽.*表示|ガスホルダー.*表示", text):
        candidate = choose(available, ("技省令_ガスホルダー及び液化ガス用貯槽", "技省令_構造等"))
        return candidate, "ガスホルダー・液化ガス用貯槽の表示を問う", None if grade == "甲種" else "乙種taxonomyに貯槽・ホルダー共通の独立分野がない"
    if contains(r"静電気", text):
        candidate = choose(available, ("製造設備の保安及び防災",))
        return candidate, "静電気による引火防止を問う", "静電気の独立分野がないため製造防災へ収容"
    return None, "技術基準の論点を既存分野名だけでは一意に特定できない", "既存taxonomyの境界説明が不足"


def business_law_candidate(
    grade: str,
    available: dict[str, str],
    body: str,
    choice: str,
    explanation: str,
) -> tuple[str | None, str, str | None]:
    detail = " ".join((choice, explanation))
    text = " ".join((body, detail))
    if contains(r"保安業務規程", choice):
        candidate = choose(available, ("保安業務規程", "保安規定"))
        issue = None if grade == "甲種" else "『保安規定』は保安規程と保安業務規程を混在させた不正確な分野名"
        return candidate, "保安業務規程の制定・届出・遵守を問う", issue
    if contains(r"保安規程", choice):
        candidate = choose(available, ("保安規程", "保安規定"))
        issue = None if grade == "甲種" else "『保安規定』は法令上の用語『保安規程』と一致しない"
        return candidate, "法令上の保安規程を問う", issue
    if contains(r"(?:ガス工作物|一般ガス導管).*(?:技術上の基準に適合するように維持|所有者.*措置.*協力|使用を一時停止)", choice):
        return choose(available, ("ガス工作物の維持等", "ガス工作物の技術基準適合")), "ガス工作物の技術基準適合維持・改善措置を問う", None
    if contains(r"ガス工作物.*技術上の基準に適合していない|ガス工作物.*技術上の基準に適合するよう", choice):
        return choose(available, ("ガス工作物の技術基準適合", "ガス工作物の維持等")), "ガス工作物の技術基準適合命令を問う", None
    if contains(r"ガス主任技術者", detail):
        return choose(available, ("ガス主任技術者",)), "ガス主任技術者の選任・職務・解任を問う", None
    if contains(r"特定ガス消費機器|特定工事|工事監督者|表示板", detail):
        return choose(available, ("特監法",)), "特定ガス消費機器の設置工事監督を問う", None
    if contains(r"ガス用品|特定ガス用品", detail):
        if contains(r"販売|陳列|基準適合表示|表示が付", detail):
            return choose(available, ("販売及び表示の制限", "事業の届出等")), "ガス用品の販売・表示制限を問う", None
        if contains(r"届出事業者|製造又は輸入|適合性検査|証明書", detail):
            return choose(available, ("事業の届出等",)), "ガス用品事業者の届出・適合性検査を問う", None
        return choose(available, ("目的及び用語の定義", "特監法")), "ガス用品・特定ガス用品の定義又は範囲を問う", None
    if contains(r"消費機器", detail):
        if contains(r"周知|調査", detail) and not contains(r"保安規程", detail):
            return choose(available, ("消費機器の周知及び調査",)), "消費機器に関する周知・調査を問う", None
        return choose(available, ("消費機器の技術上の基準",)), "消費機器の設置・技術基準適合を問う", None
    if contains(r"燃焼器|排気筒|給排気部|ガス瞬間湯沸器|ガスふろがま", detail):
        return choose(available, ("消費機器の技術上の基準",)), "消費機器の具体的な設置基準を問う", None
    if contains(r"保安業務規程", body):
        candidate = choose(available, ("保安業務規程", "保安規定"))
        issue = None if grade == "甲種" else "『保安規定』は保安規程と保安業務規程を混在させた不正確な分野名"
        return candidate, "穴埋めを含む設問全体が保安業務規程を問う", issue
    if contains(r"保安規程", body):
        candidate = choose(available, ("保安規程", "保安規定"))
        issue = None if grade == "甲種" else "『保安規定』は法令上の用語『保安規程』と一致しない"
        return candidate, "穴埋めを含む設問全体が保安規程を問う", issue
    if contains(r"事故.*報告|報告をしなければ", text):
        return choose(available, ("ガス事故の報告",)), "ガス事故の報告義務を問う", None
    if contains(r"立入検査|立ち入り.*検査|帳簿.*検査", text):
        return choose(available, ("立入検査",)), "行政庁等の立入検査を問う", None
    if contains(r"工事計画|使用前検査|定期自主検査|登録ガス工作物検査", text):
        return choose(available, ("工事計画及び検査",)), "工事計画又は検査制度を問う", None
    if contains(r"成分.*検査|硫黄全量|硫化水素|アンモニア", text):
        return choose(available, ("ガス事業の業務",)), "供給ガスの成分検査を問う", None
    if contains(r"託送供給|供給区域.*拒", text):
        return choose(available, ("ガス事業の業務",)), "託送供給等の事業者業務を問う", None
    if contains(r"技術上の基準に適合するように維持|所有者.*措置.*協力|使用を一時停止", text):
        return choose(available, ("ガス工作物の維持等", "ガス工作物の技術基準適合")), "ガス工作物の技術基準適合維持・改善措置を問う", None
    if contains(r"届け出|届出|許可|登録", text):
        return choose(available, ("事業の届出等",)), "事業者の届出・許可等を問う", None
    if contains(r"とは|目的", text):
        return choose(available, ("目的及び用語の定義",)), "制度の目的又は用語の定義を問う", None
    return None, "事業法等の論点を既存分野名だけでは一意に特定できない", "既存taxonomyの境界説明が不足"


def recommendation(
    row: dict[str, Any],
    source_question_set_id: str,
    available: dict[str, str],
    qsets: dict[str, dict[str, Any]],
) -> tuple[str, str, str, str | None]:
    grade = str(row["grade"])
    current_id = str(row["questionSetId"])
    section = str(row["official"]["section"])
    body = str(row["live"].get("originalQuestionBodyText") or "")
    choice = str(row["live"].get("originalQuestionChoiceText") or "")
    explanation = str(row["live"].get("explanationText") or "")

    if section == "basic":
        return source_question_set_id, "high", "基礎理論は設問全体の専門単元を優先する", None
    if section == "gas":
        current_name = base_name(str(qsets[current_id]["name"]))
        source_name = base_name(str(qsets[source_question_set_id]["name"]))
        if current_name == "家庭用ガス機器" and source_name in {
            "ガス機器の給排気",
            "ガス機器の安全装置及び制御装置",
        }:
            return source_question_set_id, "high", "給排気又は安全装置という具体的な復習単元を優先する", None
        return current_id, "high", "ガス技術の選択肢固有論点に合う具体的な現行分野を維持する", None

    current_name = base_name(str(qsets[current_id]["name"]))
    source_name = base_name(str(qsets[source_question_set_id]["name"]))
    is_technical_law = (
        "技術基準" in body
        or current_name.startswith("技省令_")
        or source_name.startswith("技省令_")
    )
    if is_technical_law:
        candidate, reason, taxonomy_issue = technical_law_candidate(
            grade, available, body, choice, explanation
        )
    else:
        candidate, reason, taxonomy_issue = business_law_candidate(
            grade, available, body, choice, explanation
        )
    if candidate is None:
        return current_id, "medium", reason, taxonomy_issue
    return candidate, "high", reason, taxonomy_issue


def ideal_display_name(grade: str, reason: str, taxonomy_issue: str | None) -> str | None:
    if not taxonomy_issue:
        return None
    suffix = f"（{grade}）"
    mapping = {
        "ガスの逆流防止を問う": "技省令_ガスの逆流防止",
        "低圧ガス発生設備の圧力異常防止を問う": "技省令_低圧ガス発生設備等の圧力異常防止",
        "移動式ガス発生設備の設置・保安を問う": "技省令_移動式ガス発生設備の設置等",
        "液化ガスの流出・拡大防止を問う": "技省令_液化ガスの流出防止措置",
        "設備状態の計測・確認を問う": "技省令_計測装置等",
        "導管の漏えい検査を問う": "技省令_漏えい検査",
        "設問全体が漏えい検査の対象・周期を問う": "技省令_漏えい検査",
        "露出導管の防護・抜出し防止を問う": "技省令_防護の基準",
        "停電時も保安設備の機能を維持する措置を問う": "技省令_保安電力等",
        "誤操作防止又はインターロックを問う": "技省令_誤操作防止及びインターロック",
        "静電気による引火防止を問う": "技省令_静電気除去措置",
        "漏えいした液化ガスの安全回収を問う": "技省令_漏えい液化ガスの回収",
        "ガスホルダー・液化ガス用貯槽の表示を問う": "技省令_ガスホルダー及び液化ガス用貯槽",
        "技術基準の論点を既存分野名だけでは一意に特定できない": "技省令_ガスホルダー及び液化ガス用貯槽",
    }
    if reason in {"保安業務規程の制定・届出・遵守を問う", "穴埋めを含む設問全体が保安業務規程を問う"}:
        return f"保安業務規程{suffix}"
    if reason in {"法令上の保安規程を問う", "穴埋めを含む設問全体が保安規程を問う"}:
        return f"保安規程{suffix}"
    base = mapping.get(reason)
    return f"{base}{suffix}" if base else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--audit-jsonl",
        type=Path,
        default=ROOT / "docs/goals/gas-shunin-missing-basic-explanations-firestore/notes/T030-post-live-content-audit.jsonl",
    )
    parser.add_argument(
        "--official-index",
        type=Path,
        default=ROOT / "docs/goals/gas-shunin-missing-basic-explanations-firestore/notes/T030-official-document-index.json",
    )
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--summary-json", type=Path, required=True)
    parser.add_argument("--summary-md", type=Path)
    args = parser.parse_args()

    target_rows = [row for row in load_jsonl(args.audit_jsonl) if TARGET_ISSUE in row.get("reviewIssues", [])]
    if len(target_rows) != 442:
        raise ValueError(f"expected 442 target rows, got {len(target_rows)}")
    official = {row["questionId"]: row for row in load_json(args.official_index)["records"]}
    available_by_grade, qsets = category_maps()
    source_qsets = source_question_sets()

    results: list[dict[str, Any]] = []
    for row in sorted(target_rows, key=lambda item: (item["grade"], item["examYear"], item["questionId"])):
        grade = str(row["grade"])
        qualification, _ = GRADE_CONFIG[grade]
        source_key = str(row["sourceMatch"]["sourceKey"])
        choice_index = int(row["sourceMatch"]["choiceIndex"])
        source_id = source_qsets[(qualification, source_key, choice_index)]
        current_id = str(row["questionSetId"])
        official_row = official[str(row["questionId"])]
        official_pdf = official_row["officialQuestionPdf"]
        audit_row = dict(row)
        audit_row["official"] = {
            "section": official_row["section"],
            "questionNumber": official_row["questionNumber"],
            "questionPdfPath": official_pdf["path"],
            "questionPdfPage": official_pdf["pdfPage"],
            "questionPdfSha256": official_pdf["sha256"],
        }
        recommended_id, confidence, reason, taxonomy_issue = recommendation(
            audit_row, source_id, available_by_grade[grade], qsets
        )
        if current_id == "chiefgasengineerlicense-A-10-120":
            taxonomy_issue = "『保安規定』は法令用語ではなく、『保安規程』問題セットと重複している"
        if current_id in {"chiefgasengineerlicense-A-40-174", "chiefgasengineerlicense-A-40-185"}:
            taxonomy_issue = "『計装設備及び電気設備』と『電気設備及び計装設備』が同義の重複問題セット"
        proposed_name = ideal_display_name(grade, reason, taxonomy_issue)
        if proposed_name:
            confidence = "high"
        status = "retain_current" if recommended_id == current_id else "move_to_existing_question_set"
        if taxonomy_issue:
            status = "taxonomy_change_required"
        results.append(
            {
                "questionId": row["questionId"],
                "qualification": qualification,
                "grade": grade,
                "examYear": row["examYear"],
                "questionType": row["questionType"],
                "sourceQuestionKey": source_key,
                "sourceChoiceIndex": choice_index,
                "official": audit_row["official"],
                "questionBody": row["live"].get("originalQuestionBodyText"),
                "questionChoice": row["live"].get("originalQuestionChoiceText"),
                "currentQuestionSetId": current_id,
                "currentQuestionSetName": qsets[current_id]["name"],
                "legacySourceQuestionSetId": source_id,
                "legacySourceQuestionSetName": qsets[source_id]["name"],
                "recommendedQuestionSetId": recommended_id,
                "recommendedQuestionSetName": qsets[recommended_id]["name"],
                "idealQuestionSetDisplayName": proposed_name,
                "auditStatus": status,
                "confidence": confidence,
                "semanticReason": reason,
                "taxonomyIssue": taxonomy_issue,
            }
        )

    generated_at = utc_now()
    status_counts = Counter(row["auditStatus"] for row in results)
    grade_counts = {
        grade: {
            "target": sum(row["grade"] == grade for row in results),
            "statuses": dict(Counter(row["auditStatus"] for row in results if row["grade"] == grade)),
        }
        for grade in GRADE_CONFIG
    }
    summary = {
        "schemaVersion": "gas-shunin-question-set-semantic-audit/v1",
        "generatedAt": generated_at,
        "mode": "read_only",
        "firestoreWrites": 0,
        "targetCount": len(results),
        "officialQuestionIdentityCount": len({row["sourceQuestionKey"] for row in results}),
        "statusCounts": dict(status_counts),
        "gradeCounts": grade_counts,
        "taxonomyIssueCount": sum(bool(row["taxonomyIssue"]) for row in results),
        "taxonomyIssues": dict(Counter(row["taxonomyIssue"] for row in results if row["taxonomyIssue"])),
        "questionPdfCount": len({row["official"]["questionPdfPath"] for row in results}),
        "sourcePolicy": "official question PDFs only; listing sites are not used",
    }
    write_jsonl(args.output_jsonl, results)
    write_json(args.summary_json, summary)
    if args.summary_md:
        moves = Counter(
            (row["currentQuestionSetName"], row["recommendedQuestionSetName"])
            for row in results
            if row["auditStatus"] == "move_to_existing_question_set"
        )
        lines = [
            "# ガス主任技術者 442問の問題セット意味監査",
            "",
            f"- 対象: {summary['targetCount']}問（公式問題単位 {summary['officialQuestionIdentityCount']}問）",
            f"- 甲種: {grade_counts['甲種']['target']}問",
            f"- 乙種: {grade_counts['乙種']['target']}問",
            f"- 現状維持: {status_counts.get('retain_current', 0)}問",
            f"- 既存問題セットへ移動: {status_counts.get('move_to_existing_question_set', 0)}問",
            f"- taxonomyの追加・分割・統合が必要: {status_counts.get('taxonomy_change_required', 0)}問",
            "- Firestore書込み: 0件（読取り監査のみ）",
            "- 出典: 公式問題PDFのみ。掲載サイトは不使用。",
            "",
            "## 件数の多い移動候補",
            "",
        ]
        for (current_name, recommended_name), count in moves.most_common(15):
            lines.append(f"- {count}問: {current_name} → {recommended_name}")
        lines.extend(
            [
                "",
                "## 分野名・taxonomy上の主な問題",
                "",
                "- 甲種の『保安規定』は法令上の『保安規程』と重複しており、対象問題は正しい既存セットへ寄せる必要がある。",
                "- 乙種の『保安規定』は『保安規程』と『保安業務規程』を一つに混在させており、分割が必要である。",
                "- 甲種の『計装設備及び電気設備』と『電気設備及び計装設備』は同義の重複セットで、統合対象である。",
                "- 乙種には、甲種にある漏えい検査、保安電力、計測装置、逆流防止、防護などの復習単元が不足している。",
                "",
                "一問ごとの問題文、選択肢、現在・旧ローカル・推奨問題セット、公式PDFのページとSHA-256はJSONL台帳に記録した。",
            ]
        )
        args.summary_md.parent.mkdir(parents=True, exist_ok=True)
        args.summary_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
