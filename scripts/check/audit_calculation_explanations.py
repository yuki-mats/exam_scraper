#!/usr/bin/env python3
"""Audit calculation explanations across question_json roots."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


CALCULATION_PROMPT_RE = re.compile(
    r"("
    r"最も近い|いくら|求め|算出|計算|次の諸値|次の条件|値は|量は|何倍|"
    r"処理水.*濃度|濃度.*値|除去率|負荷量|排出量|歩掛|所要時間|"
    r"上昇速度|沈降速度|空気量|発熱量|理論空気量|ブロー|"
    r"BOD.*負荷|COD.*負荷|音圧レベル|合成.*レベル|振動レベル|"
    r"距離減衰|標準酸素濃度|換算.*濃度|必要.*量|"
    r"圧力|温度|体積|密度|比重|流量|熱量|モル|mol|"
    r"消費電力|電力量|容量|速度|濃度|面積|体積|容積|質量|"
    r"合格率|正答率|期待値|確率"
    r")"
)
STRONG_CALCULATION_PROMPT_RE = re.compile(
    r"("
    r"最も近いもの|およそいくら|いくらか|求めよ|算出せよ|"
    r"最も近い値|最も近いのは|次の諸値|何倍|"
    r"必要.*(量|長さ|容積|倍率|mol数|面積|時間)|理論的に必要|"
    r"濃縮倍数はいくら|除去率\(%\).*どれか|"
    r"高発熱量と低発熱量の差.*組合せ|"
    r"計算.*(正しい|適切|不適切|近い)|"
    r"値として.*(正しい|適切|近い|どれか)"
    r")"
)
CALCULATION_ACTION_RE = re.compile(
    r"(求め|算出|計算|最も近い|いくら|何倍|合計|総和|判定基準|許容差|必要.*量|所要|時間当たり作業量)"
)
KNOWLEDGE_PROMPT_RE = re.compile(
    r"(記述として|記述中|記述のうち|関する記述|関する次の記述|下線を付した|組合せとして|最も不適切なもの|最も適切なもの|もっとも適切なもの)"
)
LEGAL_PROMPT_RE = re.compile(
    r"(法令|技術基準|省令|告示|要綱|基づく|規定されている|規定する|"
    r"ガス事業法|憲法|訴訟)"
)
CASE_JUDGMENT_PROMPT_RE = re.compile(
    r"(次の事例を読んで|事例を読んで|助言として|提案として|対応として|回答として)"
)
CASE_PROFILE_RE = re.compile(r"\d+歳の(?:男性|女性|男子|女子|男児|女児|男|女)[A-ZＡ-Ｚ]")
DIRECT_CALCULATION_INTENT_RE = re.compile(
    r"(計算|算出|求め|最も近い|いくら|何倍|何%|何％|およそ)"
)
CASE_DIRECT_CALCULATION_INTENT_RE = re.compile(
    r"(計算せよ|計算し|算出|求めよ|求めなさい|値を求め|量を求め|"
    r"何倍|何%|何％|最も近い|いくら|およそ)"
)
NON_CALCULATION_STEM_RE = re.compile(
    r"("
    r"大小関係|式として、?正しいもの|式として.*どれか|"
    r"挿入すべき語句の組合せ|語句の組み合わせ|語句等の組合せ|文章に入る数値|"
    r"定義に関する|計算式について|直接使わない因子|性能を求められている項目|"
    r"図として、?最も適切|記号の説明として|"
    r"シミュレーションに関する記述|平均化時間を.*計算すべき項目|"
    r"高発熱量.*高い順|"
    r"測定法に関する記述|脱水に関する記述|用語の定義"
    r")"
)
UNIT_RE = re.compile(
    r"("
    r"mg/L|mg|g|kg|t|μg|ug|m3|m³|Nm3|Nm³|cm3|cm³|L|mL|kL|"
    r"%|％|ppm|ppb|mol|Pa|kPa|MPa|W|kW|MW|J|kJ|MJ|kWh|"
    r"dB|℃|K|m/s|cm/s|m/min|cm/min|km/h|mm|cm|m|km|ha|m2|m²|"
    r"日|時間|分|秒|円|人|点"
    r")"
)
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
EXPLICIT_DERIVATION_RE = re.compile(
    r"(=|＝|×|÷|→|->|≒|≈|√|"
    r"掛けると|割ると|加えると|足すと|"
    r"\d+(?:\.\d+)?\s*[×÷/＋+\-−:]\s*\d+)"
)
DERIVATION_MARKER_RE = re.compile(
    r"(公式|式|代入|計算|算出|求め|換算|単位|したがって|よって|以上より|以上から|つまり|整理すると)"
)


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def question_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("question_bodies", "questions", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def text_of(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(text_of(item) for item in value)
    if isinstance(value, dict):
        return "\n".join(text_of(item) for item in value.values())
    return "" if value is None else str(value)


def compact(value: str, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", value).strip()[:limit]


def qualification_name(root: Path) -> str:
    parts = root.parts
    if "output" in parts:
        index = parts.index("output")
        if index + 1 < len(parts):
            return parts[index + 1]
    return root.parent.name


def is_excluded(path: Path, excluded_dirs: set[str]) -> bool:
    return any(part in excluded_dirs for part in path.parts)


def is_calculation_candidate(question: dict[str, Any]) -> bool:
    stem = text_of(
        question.get("questionBodyText")
        or question.get("questionText")
        or question.get("originalQuestionBodyText")
    )
    body = "\n".join(
        [
            stem,
            text_of(question.get("choiceTextList")),
            text_of(question.get("originalQuestionChoiceText")),
        ]
    )
    strong_in_stem = bool(STRONG_CALCULATION_PROMPT_RE.search(stem))
    number_count = len(NUMBER_RE.findall(body))
    if re.search(r"下線を付した", stem):
        return False
    if re.search(
        r"(挿入すべき語句|入れるべき最も適切な語句|語句.*組合せ|正しいもののみの組合せ)",
        stem,
    ):
        return False
    if re.search(r"記述.*(正しいもの|誤っているもの|すべてを選び)", stem):
        return False
    if re.search(r"文章に入る数値", stem):
        return False
    if re.search(r"構造計算によって安全性を確かめる必要があるもの", stem):
        return False
    if re.search(r"(モデル計算|計算過程|計算で用いる).{0,40}記述", stem):
        return False
    if re.search(r"(数値|語句).*組み合わせ", stem) and not re.search(r"(計算|算出)", stem):
        return False
    if (
        (CASE_JUDGMENT_PROMPT_RE.search(stem) or CASE_PROFILE_RE.search(stem))
        and not CASE_DIRECT_CALCULATION_INTENT_RE.search(stem)
    ):
        return False
    if LEGAL_PROMPT_RE.search(stem) and not strong_in_stem:
        return False
    if not strong_in_stem and not CALCULATION_ACTION_RE.search(stem):
        return False
    if NON_CALCULATION_STEM_RE.search(stem) and not strong_in_stem:
        return False
    if KNOWLEDGE_PROMPT_RE.search(stem) and not strong_in_stem:
        return False
    return (
        bool(CALCULATION_PROMPT_RE.search(stem) or strong_in_stem)
        and bool(UNIT_RE.search(body))
        and number_count >= 2
    )


def derivation_status(explanation: str) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    has_marker = bool(DERIVATION_MARKER_RE.search(explanation))
    has_operator = bool(EXPLICIT_DERIVATION_RE.search(explanation))
    if not has_marker and not has_operator:
        reasons.append("missing_derivation_marker")
    if not has_operator:
        reasons.append("missing_explicit_formula_or_operator")
    return not reasons, reasons


def merged_files(root: Path, excluded_dirs: set[str]) -> list[Path]:
    files: list[Path] = []
    for merged_dir in sorted(root.glob("*/30_merged_2")):
        if not merged_dir.is_dir() or is_excluded(merged_dir, excluded_dirs):
            continue
        files.extend(
            path
            for path in sorted(merged_dir.glob("*.json"))
            if not is_excluded(path, excluded_dirs)
        )
    return files


def audit_roots(roots: list[Path], excluded_dirs: set[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for root in roots:
        qualification = qualification_name(root)
        for path in merged_files(root, excluded_dirs):
            payload = load_json(path)
            year = path.parents[1].name
            for index, question in enumerate(question_records(payload), start=1):
                if not is_calculation_candidate(question):
                    continue
                explanation = text_of(question.get("explanationText"))
                ok, reasons = derivation_status(explanation)
                row = {
                    "qualification": qualification,
                    "year": year,
                    "file": str(path),
                    "index": index,
                    "questionLabel": question.get("questionLabel") or "",
                    "questionNo": question.get("questionNo") or "",
                    "publicQuestionId": question.get("public_question_id")
                    or question.get("publicQuestionId")
                    or "",
                    "originalQuestionId": question.get("original_question_id")
                    or question.get("originalQuestionId")
                    or question.get("source_question_id")
                    or "",
                    "questionSetId": question.get("questionSetId") or "",
                    "derivationOk": ok,
                    "issueReasons": reasons,
                    "questionBodySample": compact(
                        text_of(
                            question.get("questionBodyText")
                            or question.get("questionText")
                            or question.get("originalQuestionBodyText")
                        )
                    ),
                    "explanationSample": compact(explanation),
                }
                rows.append(row)

    summary = {
        "candidateCount": len(rows),
        "issueCount": sum(1 for row in rows if not row["derivationOk"]),
        "candidateCountByQualification": dict(
            sorted(Counter(row["qualification"] for row in rows).items())
        ),
        "issueCountByQualification": dict(
            sorted(Counter(row["qualification"] for row in rows if not row["derivationOk"]).items())
        ),
        "candidateCountByQualificationYear": dict(
            sorted(Counter(f"{row['qualification']}:{row['year']}" for row in rows).items())
        ),
        "issueCountByQualificationYear": dict(
            sorted(
                Counter(
                    f"{row['qualification']}:{row['year']}"
                    for row in rows
                    if not row["derivationOk"]
                ).items()
            )
        ),
    }
    return rows, summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit calculation explanations for visible derivation steps."
    )
    parser.add_argument(
        "--root",
        type=Path,
        action="append",
        required=True,
        help="questions_json root. Can be specified multiple times.",
    )
    parser.add_argument(
        "--exclude-dir",
        action="append",
        default=["old", "upload_ready"],
        help="directory name to skip. Can be specified multiple times.",
    )
    parser.add_argument("--jsonl", type=Path, default=None, help="write row-level JSONL")
    parser.add_argument("--summary", type=Path, default=None, help="write summary JSON")
    parser.add_argument("--fail-on-issues", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    excluded_dirs = set(args.exclude_dir or [])
    rows, summary = audit_roots(args.root, excluded_dirs)

    if args.jsonl:
        args.jsonl.parent.mkdir(parents=True, exist_ok=True)
        with args.jsonl.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    if args.fail_on_issues and summary["issueCount"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
