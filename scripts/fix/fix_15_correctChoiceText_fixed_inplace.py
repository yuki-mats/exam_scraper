#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


FULLWIDTH_DIGIT_TRANSLATION = str.maketrans("０１２３４５６７８９", "0123456789")
ANSWER_RE = re.compile(r"正解は\s*([0-9]+(?:\s*,\s*[0-9]+)*)\s*です。")


@dataclass(frozen=True)
class SourceQuestion:
    question_url: str
    public_question_id: str
    question_body_text: str | None
    question_type: str | None
    question_intent: str | None
    answer_result_text: str | None
    answer_numbers: list[int]
    choice_count: int


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def parse_answer_numbers(answer_result_text: str) -> list[int]:
    text = (answer_result_text or "").translate(FULLWIDTH_DIGIT_TRANSLATION)
    match = ANSWER_RE.search(text)
    if not match:
        return []
    numbers: list[int] = []
    for part in match.group(1).split(","):
        part = part.strip()
        if part.isdigit():
            n = int(part)
            if n >= 1 and n not in numbers:
                numbers.append(n)
    return numbers


def expected_correct_choice_text(
    *,
    question_intent: str | None,
    answer_numbers: list[int],
    choice_count: int,
) -> list[str] | None:
    if not answer_numbers or choice_count <= 0:
        return None

    if question_intent not in ("select_correct", "select_incorrect"):
        return None

    answer_indexes = {n - 1 for n in answer_numbers if 1 <= n <= choice_count}
    if not answer_indexes:
        return None

    # ルール: answer_numbers の件数が絶対。
    # - select_correct   → answer_numbers の位置が「正しい」
    # - select_incorrect → answer_numbers の位置が「間違い」
    if question_intent == "select_incorrect":
        labels = ["正しい"] * choice_count
        for idx in answer_indexes:
            labels[idx] = "間違い"
        return labels

    labels = ["間違い"] * choice_count
    for idx in answer_indexes:
        labels[idx] = "正しい"
    return labels


def build_source_index(group_dir: Path) -> dict[str, SourceQuestion]:
    """
    15_correctChoiceText_fixed のレコードを修正するための一次情報を構築する。
    優先度: 12_merged_questionType > 20_merged_1 > 00_source
    key は question_url。
    """
    sources: list[Path] = []
    for sub in ("12_merged_questionType", "20_merged_1", "00_source"):
        d = group_dir / sub
        if d.exists():
            sources.extend(sorted(d.glob("*.json")))

    by_url: dict[str, SourceQuestion] = {}
    for path in sources:
        payload = load_json(path)
        bodies = payload.get("question_bodies") if isinstance(payload, dict) else None
        if not isinstance(bodies, list):
            continue
        for body in bodies:
            if not isinstance(body, dict):
                continue
            qurl = str(body.get("question_url") or "").strip()
            if not qurl:
                continue
            # すでに上位優先度のソースが入っていれば上書きしない
            if qurl in by_url:
                continue
            pub = str(body.get("public_question_id") or body.get("original_question_id") or "").strip()
            if not pub:
                continue
            by_url[qurl] = SourceQuestion(
                question_url=qurl,
                public_question_id=pub,
                question_body_text=str(body.get("questionBodyText") or "").strip() or None,
                question_type=str(body.get("questionType") or "").strip() or None,
                question_intent=str(body.get("questionIntent") or "").strip() or None,
                answer_result_text=str(body.get("answer_result_text") or "").strip() or None,
                answer_numbers=[
                    int(v)
                    for v in (body.get("answer_result_inferred_correct_choice_numbers") or [])
                    if isinstance(v, int) or str(v).isdigit()
                ],
                choice_count=(
                    len(body.get("choiceTextList") or [])
                    if isinstance(body.get("choiceTextList"), list)
                    else 0
                ),
            )
    return by_url


def infer_question_intent(question_body_text: str | None) -> str | None:
    text = (question_body_text or "").strip()
    if not text:
        return None
    negative_keywords = (
        "最も不適当",
        "不適当",
        "適合しない",
        "誤って",
        "誤り",
        "してはならない",
        "必要がない",
        "要しない",
        "関係の少ない",
        "最も関係の少ない",
    )
    if any(keyword in text for keyword in negative_keywords):
        return "select_incorrect"
    return "select_correct"


def fix_file(path: Path, source_by_url: dict[str, SourceQuestion], *, apply: bool) -> tuple[int, int]:
    data = load_json(path)
    if not isinstance(data, list):
        return 0, 0

    updated = 0
    total = 0
    for entry in data:
        if not isinstance(entry, dict):
            continue
        total += 1
        qurl = str(entry.get("question_url") or "").strip()
        if not qurl:
            continue
        source = source_by_url.get(qurl)
        if source is None:
            continue

        answer_numbers = parse_answer_numbers(source.answer_result_text or "")
        if source.answer_numbers:
            # inferred を優先
            answer_numbers = []
            for v in source.answer_numbers:
                if v not in answer_numbers:
                    answer_numbers.append(v)
        intent = source.question_intent or infer_question_intent(source.question_body_text)
        expected = expected_correct_choice_text(
            question_intent=intent,
            answer_numbers=answer_numbers,
            choice_count=(source.choice_count or (len(entry.get("correctChoiceText") or []) if isinstance(entry.get("correctChoiceText"), list) else 0) or 5),
        )
        if expected is None:
            continue

        current = entry.get("correctChoiceText")
        if current == expected:
            continue

        entry["correctChoiceText"] = expected
        entry["correctChoiceText_changed"] = True
        entry["correctChoiceText_change_reason"] = (
            "answer_result_text と questionIntent に基づき 5肢の正誤を再計算"
        )
        entry["correctChoiceText_change_detail"] = (
            f"question_url={qurl} answer_result_text={source.answer_result_text!s} questionIntent={intent!s}"
        )
        updated += 1

    if apply and updated:
        save_json(path, data)

    return updated, total


def main() -> int:
    parser = argparse.ArgumentParser(
        description="15_correctChoiceText_fixed 配下の correctChoiceText をローカル一次情報(12_merged_questionType 等)から再計算して上書きする",
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path("/Users/yuki/development/exam_scraper/output/2nd-class-kenchikushi/questions_json"),
        help="questions_json のベースディレクトリ",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="実際に 15_correctChoiceText_fixed の JSON を上書きする（省略時は dry-run）",
    )
    args = parser.parse_args()

    base_dir: Path = args.base_dir.resolve()
    if not base_dir.exists():
        raise SystemExit(f"base_dir not found: {base_dir}")

    group_dirs = sorted(path for path in base_dir.iterdir() if path.is_dir())
    target_files: list[Path] = []
    for group_dir in group_dirs:
        patch_dir = group_dir / "15_correctChoiceText_fixed"
        if not patch_dir.exists():
            continue
        target_files.extend(sorted(patch_dir.glob("*.json")))

    if not target_files:
        print("[INFO] 対象ファイルがありません。")
        return 0

    total_updated = 0
    total_records = 0
    touched_files = 0

    for file_path in target_files:
        group_dir = file_path.parent.parent
        source_by_url = build_source_index(group_dir)
        updated, total = fix_file(file_path, source_by_url, apply=args.apply)
        total_updated += updated
        total_records += total
        if updated:
            touched_files += 1
        if updated and not args.apply:
            print(f"[DRY-RUN] {file_path}: updated={updated}/{total}")
        if updated and args.apply:
            print(f"[UPDATED] {file_path}: updated={updated}/{total}")

    if args.apply:
        print(f"[OK] files_updated={touched_files} records_updated={total_updated} total_records={total_records}")
    else:
        print(f"[DRY-RUN] files_to_update={touched_files} records_to_update={total_updated} total_records={total_records}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
