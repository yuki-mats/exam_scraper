#!/usr/bin/env python3
"""Audit kougai calculation explanations for visible derivation steps."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


CALCULATION_PROMPT_RE = re.compile(
    r"("
    r"最も近い|いくら|求め|算出|計算|次の諸値|"
    r"処理水.*濃度|濃度.*値|除去率|負荷量|排出量|"
    r"上昇速度|沈降速度|空気量|発熱量|理論空気量|ブロー|"
    r"BOD.*負荷|COD.*負荷|音圧レベル|合成.*レベル|振動レベル|"
    r"距離減衰|標準酸素濃度|換算.*濃度|必要.*量"
    r")"
)
STRONG_CALCULATION_PROMPT_RE = re.compile(
    r"("
    r"最も近いもの|およそいくら|いくらか|求めよ|求める|算出せよ|"
    r"次の諸値|何倍|何mol|何%|何m|何日|何kg|何ppm|"
    r"必要.*(量|長さ|容積|倍率|mol数)|理論的に必要|"
    r"濃縮倍数はいくら|除去率\\(%\\).*どれか|"
    r"高発熱量と低発熱量の差.*組合せ"
    r")"
)
KNOWLEDGE_PROMPT_RE = re.compile(
    r"(記述として|記述中|関する記述|下線を付した|組合せとして|最も不適切なもの)"
)
NON_CALCULATION_STEM_RE = re.compile(
    r"(大小関係|式として、?正しいもの|式として.*どれか|"
    r"挿入すべき語句の組合せ|直接使わない因子|性能を求められている項目|"
    r"図として、?最も適切|記号の説明として|"
    r"シミュレーションに関する記述|平均化時間を.*計算すべき項目|"
    r"高発熱量.*高い順|"
    r"測定法に関する記述|脱水に関する記述)"
)
UNIT_RE = re.compile(
    r"(mg/L|mg|g|kg|μg|ug|m3|m³|Nm3|Nm³|L|mL|kL|%|％|ppm|ppb|"
    r"mol|Pa|kPa|MPa|W|kW|dB|℃|m/s|cm/s|m/min|cm/min|日|時間)"
)
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
EXPLICIT_DERIVATION_RE = re.compile(
    r"(=|＝|×|÷|→|≒|≈|\\times|\\div|"
    r"\d+(?:\.\d+)?\s*[×÷/＋+\-−]\s*\d+)"
)
DERIVATION_MARKER_RE = re.compile(
    r"(公式|式|代入|計算|算出|求め|換算|単位|したがって|よって|以上より|以上から|つまり)"
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


def compact(value: str, limit: int = 220) -> str:
    return re.sub(r"\s+", " ", value).strip()[:limit]


def is_calculation_candidate(question: dict[str, Any]) -> bool:
    stem = text_of(question.get("questionBodyText"))
    body = "\n".join(
        [
            stem,
            text_of(question.get("choiceTextList")),
        ]
    )
    strong_in_stem = bool(STRONG_CALCULATION_PROMPT_RE.search(stem))
    if re.search(r"下線を付した", stem):
        return False
    if NON_CALCULATION_STEM_RE.search(stem):
        return False
    if KNOWLEDGE_PROMPT_RE.search(stem) and not strong_in_stem:
        return False
    return (
        bool(CALCULATION_PROMPT_RE.search(stem) or strong_in_stem)
        and bool(UNIT_RE.search(body))
        and len(NUMBER_RE.findall(body)) >= 2
    )


def derivation_status(explanation: str) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    has_marker = bool(DERIVATION_MARKER_RE.search(explanation))
    has_operator = bool(EXPLICIT_DERIVATION_RE.search(explanation))
    if not has_marker:
        reasons.append("missing_derivation_marker")
    if not has_operator:
        reasons.append("missing_explicit_formula_or_operator")
    return not reasons, reasons


def latest_merged_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for year_dir in sorted(path for path in root.iterdir() if path.is_dir() and path.name.isdigit()):
        merged_dir = year_dir / "30_merged_2"
        if not merged_dir.exists():
            continue
        files.extend(sorted(merged_dir.glob("*.json")))
    return files


def audit(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in latest_merged_files(root):
        payload = load_json(path)
        year = path.parents[1].name
        for index, question in enumerate(question_records(payload), start=1):
            if not is_calculation_candidate(question):
                continue
            explanation = text_of(question.get("explanationText"))
            ok, reasons = derivation_status(explanation)
            row = {
                "year": year,
                "file": str(path),
                "index": index,
                "questionLabel": question.get("questionLabel") or "",
                "originalQuestionId": question.get("original_question_id")
                or question.get("originalQuestionId")
                or question.get("public_question_id")
                or "",
                "questionSetId": question.get("questionSetId") or "",
                "derivationOk": ok,
                "issueReasons": reasons,
                "questionBodySample": compact(
                    text_of(question.get("questionBodyText"))
                    or text_of(question.get("originalQuestionBodyText"))
                ),
                "explanationSample": compact(explanation),
            }
            rows.append(row)

    summary = {
        "candidateCount": len(rows),
        "issueCount": sum(1 for row in rows if not row["derivationOk"]),
        "candidateCountByYear": dict(sorted(Counter(row["year"] for row in rows).items())),
        "issueCountByYear": dict(
            sorted(Counter(row["year"] for row in rows if not row["derivationOk"]).items())
        ),
    }
    return rows, summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit kougai calculation explanations for derivation visibility."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("output/kougai/questions_json"),
        help="questions_json root",
    )
    parser.add_argument("--jsonl", type=Path, default=None, help="write row-level JSONL")
    parser.add_argument("--summary", type=Path, default=None, help="write summary JSON")
    parser.add_argument("--fail-on-issues", action="store_true")
    args = parser.parse_args()

    rows, summary = audit(args.root)

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
