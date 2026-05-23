from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


INTENT_SELECT_CORRECT = "select_correct"
INTENT_SELECT_INCORRECT = "select_incorrect"

LABEL_TRUE = "正しい"
LABEL_FALSE = "間違い"

LABEL_NORMALIZE_MAP = {
    "正解": LABEL_TRUE,
    "不正解": LABEL_FALSE,
    "誤り": LABEL_FALSE,
}

FULLWIDTH_DIGIT_TRANSLATION = str.maketrans("０１２３４５６７８９", "0123456789")


def _normalize_digits(text: str) -> str:
    return (text or "").translate(FULLWIDTH_DIGIT_TRANSLATION)


def parse_answer_numbers_from_answer_result_text(text: Any) -> list[int]:
    """
    answer_result_text: "正解は 1, 3 です。" のような形式から番号を抽出する（フォールバック用）。
    """
    import re

    value = _normalize_digits(str(text or "")).strip()
    m = re.search(r"正解は\s*([0-9]+(?:\s*,\s*[0-9]+)*)\s*です。", value)
    if not m:
        return []
    numbers: list[int] = []
    for part in m.group(1).split(","):
        part = part.strip()
        if not part.isdigit():
            continue
        n = int(part)
        if n not in numbers:
            numbers.append(n)
    return numbers


def get_answer_numbers(qb: dict) -> list[int]:
    """
    answer_result_inferred_correct_choice_numbers を優先し、無ければ answer_result_text をパースする。
    """
    inferred = qb.get("answer_result_inferred_correct_choice_numbers")
    if isinstance(inferred, list) and inferred:
        numbers: list[int] = []
        for v in inferred:
            if isinstance(v, int):
                numbers.append(v)
            elif str(v).isdigit():
                numbers.append(int(str(v)))
        normalized: list[int] = []
        for n in numbers:
            if n > 0 and n not in normalized:
                normalized.append(n)
        return normalized
    return parse_answer_numbers_from_answer_result_text(qb.get("answer_result_text"))


def detect_choice_count(qb: dict) -> int:
    """
    選択肢数を推定する。
    """
    choice_text_list = qb.get("choiceTextList")
    if isinstance(choice_text_list, list) and choice_text_list:
        return len(choice_text_list)

    correct_choice_text = qb.get("correctChoiceText")
    if isinstance(correct_choice_text, list) and correct_choice_text:
        return len(correct_choice_text)

    choice_image_urls = qb.get("originalQuestionChoiceImageUrls")
    if isinstance(choice_image_urls, list) and choice_image_urls:
        return len(choice_image_urls)

    return 0


def normalize_label(value: Any) -> Any:
    if isinstance(value, str):
        return LABEL_NORMALIZE_MAP.get(value, value)
    return value


@dataclass(frozen=True)
class Violation:
    source_path: Path
    question_index: int
    original_question_id: str | None
    question_url: str | None
    question_intent: str
    correct_choice_text: Any
    reason: str


def _iter_question_bodies(payload: Any) -> Iterable[dict]:
    if not isinstance(payload, dict):
        return []
    bodies = payload.get("question_bodies")
    if not isinstance(bodies, list):
        return []
    return [b for b in bodies if isinstance(b, dict)]


def validate_question_intent_correct_choice_distribution(
    *,
    payload: Any,
    source_path: Path,
    expected_choice_count: int | None = None,
) -> list[Violation]:
    """
    questionIntent と correctChoiceText の整合性を検査する。

    ルール（絶対）:
      - answer_result_inferred_correct_choice_numbers の件数が「正しい/間違い」の件数になる
        - select_correct   → 正解番号の位置が「正しい」
        - select_incorrect → 正解番号の位置が「間違い」
    """
    violations: list[Violation] = []
    for idx, qb in enumerate(_iter_question_bodies(payload)):
        intent = qb.get("questionIntent")
        if intent not in (INTENT_SELECT_CORRECT, INTENT_SELECT_INCORRECT):
            continue

        cct = qb.get("correctChoiceText")
        if not isinstance(cct, list):
            violations.append(
                Violation(
                    source_path=source_path,
                    question_index=idx,
                    original_question_id=qb.get("original_question_id") or qb.get("public_question_id"),
                    question_url=qb.get("question_url"),
                    question_intent=intent,
                    correct_choice_text=cct,
                    reason="correctChoiceText_not_list",
                )
            )
            continue

        normalized = [normalize_label(v) for v in cct]
        choice_count = detect_choice_count(qb)
        if choice_count <= 0:
            violations.append(
                Violation(
                    source_path=source_path,
                    question_index=idx,
                    original_question_id=qb.get("original_question_id") or qb.get("public_question_id"),
                    question_url=qb.get("question_url"),
                    question_intent=intent,
                    correct_choice_text=normalized,
                    reason="choice_count_unresolved",
                )
            )
            continue
        if expected_choice_count is not None and choice_count != expected_choice_count:
            violations.append(
                Violation(
                    source_path=source_path,
                    question_index=idx,
                    original_question_id=qb.get("original_question_id") or qb.get("public_question_id"),
                    question_url=qb.get("question_url"),
                    question_intent=intent,
                    correct_choice_text=normalized,
                    reason=f"choice_count_unexpected:{choice_count}",
                )
            )
            continue
        if len(normalized) != choice_count:
            violations.append(
                Violation(
                    source_path=source_path,
                    question_index=idx,
                    original_question_id=qb.get("original_question_id") or qb.get("public_question_id"),
                    question_url=qb.get("question_url"),
                    question_intent=intent,
                    correct_choice_text=normalized,
                    reason=f"correctChoiceText_length_mismatch:{len(normalized)}!=choice_count:{choice_count}",
                )
            )
            continue

        if any(v not in (LABEL_TRUE, LABEL_FALSE) for v in normalized):
            violations.append(
                Violation(
                    source_path=source_path,
                    question_index=idx,
                    original_question_id=qb.get("original_question_id") or qb.get("public_question_id"),
                    question_url=qb.get("question_url"),
                    question_intent=intent,
                    correct_choice_text=normalized,
                    reason="label_unexpected",
                )
            )
            continue

        answer_numbers = get_answer_numbers(qb)
        if not answer_numbers:
            violations.append(
                Violation(
                    source_path=source_path,
                    question_index=idx,
                    original_question_id=qb.get("original_question_id") or qb.get("public_question_id"),
                    question_url=qb.get("question_url"),
                    question_intent=intent,
                    correct_choice_text=normalized,
                    reason="answer_numbers_missing",
                )
            )
            continue
        if any((n < 1 or n > choice_count) for n in answer_numbers):
            violations.append(
                Violation(
                    source_path=source_path,
                    question_index=idx,
                    original_question_id=qb.get("original_question_id") or qb.get("public_question_id"),
                    question_url=qb.get("question_url"),
                    question_intent=intent,
                    correct_choice_text=normalized,
                    reason=f"answer_number_out_of_range:{answer_numbers}",
                )
            )
            continue

        if intent == INTENT_SELECT_CORRECT:
            expected = [
                LABEL_TRUE if (i + 1) in set(answer_numbers) else LABEL_FALSE
                for i in range(choice_count)
            ]
            if normalized != expected:
                violations.append(
                    Violation(
                        source_path=source_path,
                        question_index=idx,
                        original_question_id=qb.get("original_question_id") or qb.get("public_question_id"),
                        question_url=qb.get("question_url"),
                        question_intent=intent,
                        correct_choice_text=normalized,
                        reason=f"labels_mismatch expected={expected} answer_numbers={answer_numbers}",
                    )
                )
        else:
            expected = [
                LABEL_FALSE if (i + 1) in set(answer_numbers) else LABEL_TRUE
                for i in range(choice_count)
            ]
            if normalized != expected:
                violations.append(
                    Violation(
                        source_path=source_path,
                        question_index=idx,
                        original_question_id=qb.get("original_question_id") or qb.get("public_question_id"),
                        question_url=qb.get("question_url"),
                        question_intent=intent,
                        correct_choice_text=normalized,
                        reason=f"labels_mismatch expected={expected} answer_numbers={answer_numbers}",
                    )
                )

    return violations


def raise_on_violations(
    *,
    payload: Any,
    source_path: Path,
    expected_choice_count: int | None = None,
    max_examples: int = 20,
) -> None:
    violations = validate_question_intent_correct_choice_distribution(
        payload=payload,
        source_path=source_path,
        expected_choice_count=expected_choice_count,
    )
    if not violations:
        return

    lines = [
        "questionIntent と correctChoiceText の整合性エラーが見つかりました。",
        f"file={source_path}",
        f"violations={len(violations)}",
        "",
        f"examples (first {min(max_examples, len(violations))}):",
    ]
    for v in violations[:max_examples]:
        lines.append(
            " - "
            f"question_index={v.question_index} "
            f"original_question_id={v.original_question_id} "
            f"intent={v.question_intent} "
            f"reason={v.reason} "
            f"url={v.question_url} "
            f"correctChoiceText={v.correct_choice_text}"
        )
    raise RuntimeError("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="questionIntent(select_correct/select_incorrect) と correctChoiceText(正しい/間違い) の整合性チェック"
    )
    parser.add_argument(
        "--output-root",
        type=str,
        default="output",
        help="output ディレクトリ（デフォルト: output）",
    )
    parser.add_argument(
        "--expected-choice-count",
        type=int,
        default=None,
        help="想定する選択肢数（省略時は、選択肢数は固定せず「answer_numbers + questionIntent で導出したラベル」と一致するかを検査）",
    )
    parser.add_argument(
        "--glob",
        dest="glob_pattern",
        type=str,
        default="*/questions_json/*/30_merged_2/*.json",
        help="output-root 配下を探索する glob（デフォルト: */questions_json/*/30_merged_2/*.json）",
    )
    args = parser.parse_args(argv)

    root = Path(args.output_root)
    paths = sorted(root.glob(args.glob_pattern))
    if not paths:
        print(f"[WARN] 対象ファイルが見つかりません: {root}/{args.glob_pattern}")
        return 0

    total = 0
    violations_total = 0
    for path in paths:
        total += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"[ERROR] JSON読込失敗: {path}: {exc}")
            return 1
        violations = validate_question_intent_correct_choice_distribution(
            payload=payload,
            source_path=path,
            expected_choice_count=args.expected_choice_count,
        )
        if violations:
            violations_total += len(violations)
            print(f"[NG] {path} violations={len(violations)}")
            for v in violations[:5]:
                print(
                    f"  - idx={v.question_index} id={v.original_question_id} intent={v.question_intent} reason={v.reason}"
                )

    if violations_total:
        print(f"[SUMMARY] files={total} violations={violations_total}")
        return 1
    print(f"[SUMMARY] files={total} violations=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
