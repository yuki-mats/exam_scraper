#!/usr/bin/env python3
"""Audit calculation explanations for beginner-readable derivation quality."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from audit_calculation_explanations import (
    compact,
    is_calculation_candidate,
    load_json,
    merged_files,
    qualification_name,
    question_records,
    text_of,
)


FORMULA_INTRO_RE = re.compile(
    r"(公式|計算式|使う式|用いる式|関係式|定義|求め方|"
    r"求める式|算出式|で求め|で算出|で計算|で表され|"
    r"(?:は|を|で)\s*(?:次の|以下の)?式|=|＝|≤|≦|/|／|÷|×)"
)
SUBSTITUTION_RE = re.compile(
    r"(代入|"
    r"\d+(?:\.\d+)?\s*(?:×|÷|/|＋|\+|-|−|:)\s*\d+(?:\.\d+)?|"
    r"\d+(?:\.\d+)?(?:[（(][^）)]*[）)])?\s*(?:×|÷|/|／|＋|\+|-|−|:)\s*"
    r"\d+(?:\.\d+)?|"
    r"\d+(?:\.\d+)?\s*(?:×|÷|/|／)\s*[（(]\d+(?:\.\d+)?|"
    r"\d+(?:\.\d+)?[^。\n]{0,16}(?:×|÷|/|／)[^。\n]{0,16}\d+(?:\.\d+)?|"
    r"[=＝]\s*\d+(?:\.\d+)?)"
)
STEP_MARKER_RE = re.compile(
    r"(まず|次に|ここで|このため|したがって|よって|以上より|以上から|"
    r"整理すると|代入|当てはめ|これに|本症例では|この問題では|ゆえに|"
    r"計算|換算|単位|小数|四捨五入|丸め|割ると|掛けると|加えると|"
    r"となり|となる|ため|ではない|なので|より|解く|比較|求める|"
    r"であり|される|と考え|ここから|続いて|①|②|③)"
)
UNIT_RE = re.compile(
    r"(mg/L|mg|g|kg|t|μg|ug|m3|m³|Nm3|Nm³|cm3|cm³|L|mL|kL|"
    r"%|％|ppm|ppb|mol|Pa|kPa|MPa|W|kW|MW|J|kJ|MJ|kWh|"
    r"dB|℃|K|m/s|cm/s|m/min|cm/min|km/h|mm|cm|m|km|ha|m2|m²|"
    r"日|時間|分|秒|円|人|点|Torr|mEq|mEq/L|mOsm|mOsm/kg)"
)
ANSWER_REASON_RE = re.compile(
    r"(答え|解答|正答|正解|正しい|不正解|間違い|誤り|選択肢|したがって|よって|以上より|以上から|"
    r"となるため|に最も近い|最も近い|ではない|が正しい|が適切|不適切である)"
)

BEGINNER_NON_CALCULATION_RE = re.compile(
    r"(人口置換水準|平均余命|基準値として|正常値として|代表値として|"
    r"高血圧.*基準|基準.*血圧|"
    r"算出するのに有用|算出.*必要な情報|所要時間.*一般的)"
)


def question_stem(question: dict[str, Any]) -> str:
    return text_of(
        question.get("questionBodyText")
        or question.get("questionText")
        or question.get("originalQuestionBodyText")
    )


def is_beginner_audit_candidate(question: dict[str, Any]) -> bool:
    stem = question_stem(question)
    if BEGINNER_NON_CALCULATION_RE.search(stem):
        return False
    return is_calculation_candidate(question)


def beginner_flags(explanation: str) -> dict[str, bool]:
    return {
        "hasFormulaIntro": bool(FORMULA_INTRO_RE.search(explanation)),
        "hasSubstitution": bool(SUBSTITUTION_RE.search(explanation)),
        "hasStepMarker": bool(STEP_MARKER_RE.search(explanation)),
        "hasUnit": bool(
            UNIT_RE.search(explanation)
            or re.search(r"単位はない|単位なし|比なので単位|リスク比|オッズ比|尤度比|符号語|ビット|mod\s*2", explanation)
        ),
        "hasAnswerReason": bool(ANSWER_REASON_RE.search(explanation)),
    }


def question_id(question: dict[str, Any]) -> str:
    return (
        question.get("public_question_id")
        or question.get("publicQuestionId")
        or question.get("original_question_id")
        or question.get("originalQuestionId")
        or question.get("source_question_id")
        or ""
    )


def audit_roots(roots: list[Path], excluded_dirs: set[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for root in roots:
        qualification = qualification_name(root)
        for path in merged_files(root, excluded_dirs):
            payload = load_json(path)
            year = path.parents[1].name
            for index, question in enumerate(question_records(payload), start=1):
                if not is_beginner_audit_candidate(question):
                    continue
                explanation = text_of(question.get("explanationText"))
                flags = beginner_flags(explanation)
                missing_flags = [name for name, ok in flags.items() if not ok]
                score = sum(1 for ok in flags.values() if ok)
                rows.append(
                    {
                        "qualification": qualification,
                        "year": year,
                        "file": str(path),
                        "index": index,
                        "questionLabel": question.get("questionLabel") or "",
                        "questionNo": question.get("questionNo") or "",
                        "publicQuestionId": question_id(question),
                        "originalQuestionId": question.get("original_question_id")
                        or question.get("originalQuestionId")
                        or question.get("source_question_id")
                        or "",
                        "questionSetId": question.get("questionSetId") or "",
                        "beginnerScore": score,
                        "beginnerOk": not missing_flags,
                        "missingFlags": missing_flags,
                        **flags,
                        "questionBodySample": compact(question_stem(question)),
                        "explanationSample": compact(explanation, 320),
                    }
                )

    rows.sort(
        key=lambda row: (
            row["beginnerOk"],
            row["beginnerScore"],
            row["qualification"],
            row["year"],
            row["file"],
            row["index"],
        )
    )
    for position, row in enumerate(rows, start=1):
        row["reviewPosition"] = position

    issue_rows = [row for row in rows if not row["beginnerOk"]]
    summary = {
        "candidateCount": len(rows),
        "beginnerIssueCount": len(issue_rows),
        "scoreDistribution": dict(sorted(Counter(str(row["beginnerScore"]) for row in rows).items())),
        "candidateCountByQualification": dict(
            sorted(Counter(row["qualification"] for row in rows).items())
        ),
        "issueCountByQualification": dict(
            sorted(Counter(row["qualification"] for row in issue_rows).items())
        ),
        "issueCountByQualificationYear": dict(
            sorted(Counter(f"{row['qualification']}:{row['year']}" for row in issue_rows).items())
        ),
        "flagMissingCounts": {
            flag: sum(1 for row in rows if not row[flag])
            for flag in (
                "hasFormulaIntro",
                "hasSubstitution",
                "hasStepMarker",
                "hasUnit",
                "hasAnswerReason",
            )
        },
    }
    return rows, summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit calculation explanations for beginner-readable derivation quality."
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
    rows, summary = audit_roots(args.root, set(args.exclude_dir or []))

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
    if args.fail_on_issues and summary["beginnerIssueCount"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
