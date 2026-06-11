#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SourceQuestion:
    question_url: str
    question_body_text: str | None
    question_intent: str | None


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def normalize_intent(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text in {"select_correct", "select_incorrect"}:
        return text
    return None


def infer_question_intent(question_body_text: str | None) -> str | None:
    text = (question_body_text or "").strip()
    if not text:
        return None

    negative_keywords = (
        "最も不適当",
        "不適当",
        "不適切",
        "適切でない",
        "適当でない",
        "適合しない",
        "誤って",
        "誤り",
        "誤った",
        "正しくない",
        "ならない",
        "みられない",
        "起こり得ない",
        "適応でない",
        "してはならない",
        "必要がない",
        "要しない",
        "関係の少ない",
        "最も関係の少ない",
        "記載を要しない",
    )

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    intent_text = text[-240:]
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if "どれか" not in line and "選べ" not in line:
            continue
        if any(keyword in line for keyword in negative_keywords):
            intent_text = line
            break
        if not line.startswith(("のは", "は")) and len(line) > 8:
            intent_text = line
            break
        previous = lines[index - 1] if index > 0 else ""
        intent_text = f"{previous}\n{line}".strip() or line
        break

    if any(keyword in intent_text for keyword in negative_keywords):
        return "select_incorrect"
    return "select_correct"


def build_source_index(group_dir: Path) -> dict[str, SourceQuestion]:
    """
    優先度: 12_merged_questionType > 20_merged_1 > 00_source
    key は question_url
    """
    sources: list[Path] = []
    for sub in ("12_merged_questionType", "20_merged_1", "00_source"):
        d = group_dir / sub
        if d.exists():
            sources.extend(sorted(d.glob("*.json")))
            old_dir = d / "old"
            if old_dir.exists():
                sources.extend(sorted(old_dir.glob("*.json")))

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
            if qurl in by_url:
                continue
            by_url[qurl] = SourceQuestion(
                question_url=qurl,
                question_body_text=str(body.get("questionBodyText") or "").strip() or None,
                question_intent=normalize_intent(body.get("questionIntent")),
            )
    return by_url


def fix_file(path: Path, source_by_url: dict[str, SourceQuestion], *, apply: bool) -> tuple[int, int, int]:
    data = load_json(path)
    if not isinstance(data, list):
        return 0, 0, 0

    updated = 0
    total = 0
    unresolved = 0

    for entry in data:
        if not isinstance(entry, dict):
            continue
        total += 1
        if normalize_intent(entry.get("questionIntent")) is not None:
            continue

        qurl = str(entry.get("question_url") or "").strip()
        if not qurl:
            unresolved += 1
            continue

        src = source_by_url.get(qurl)
        if src is None:
            unresolved += 1
            continue

        intent = src.question_intent or infer_question_intent(src.question_body_text)
        if intent is None:
            unresolved += 1
            continue

        entry["questionIntent"] = intent
        updated += 1

    if apply and updated:
        save_json(path, data)
    return updated, total, unresolved


def main() -> int:
    parser = argparse.ArgumentParser(
        description="15_correctChoiceText_fixed の questionIntent をローカル一次情報から推定して埋める（上書き）",
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path("/Users/yuki/development/exam_scraper/output"),
        help="検索のベースディレクトリ（例: output や output/<資格>/questions_json）",
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

    target_files: list[Path] = sorted(base_dir.rglob("15_correctChoiceText_fixed/*.json"))

    if not target_files:
        print("[INFO] 対象ファイルがありません。")
        return 0

    total_updated = 0
    total_records = 0
    total_unresolved = 0
    touched_files = 0

    for file_path in target_files:
        # .../output/<qual>/questions_json/<list_group_id>/15_correctChoiceText_fixed/*.json
        group_dir = file_path.parent.parent
        source_by_url = build_source_index(group_dir)
        updated, total, unresolved = fix_file(file_path, source_by_url, apply=args.apply)
        total_updated += updated
        total_records += total
        total_unresolved += unresolved
        if updated:
            touched_files += 1
        if updated and not args.apply:
            print(f"[DRY-RUN] {file_path}: updated={updated}/{total} unresolved={unresolved}")
        if updated and args.apply:
            print(f"[UPDATED] {file_path}: updated={updated}/{total} unresolved={unresolved}")

    if args.apply:
        print(
            f"[OK] files_updated={touched_files} intents_filled={total_updated} "
            f"total_records={total_records} unresolved={total_unresolved}"
        )
    else:
        print(
            f"[DRY-RUN] files_to_update={touched_files} intents_to_fill={total_updated} "
            f"total_records={total_records} unresolved={total_unresolved}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
