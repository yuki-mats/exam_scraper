#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEBUG_FIELDS = (
    "answer_result_selected_choice_numbers",
    "answer_result_is_selected_choice_correct",
)


@dataclass(frozen=True)
class Removal:
    json_path: Path
    removed_count: int


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def remove_from_payload(payload: Any) -> int:
    """
    question_bodies 配列内の各 dict から DEBUG_FIELDS を削除する。
    それ以外の構造は変更しない。
    """
    if not isinstance(payload, dict):
        return 0
    bodies = payload.get("question_bodies")
    if not isinstance(bodies, list):
        return 0

    removed = 0
    for body in bodies:
        if not isinstance(body, dict):
            continue
        for key in DEBUG_FIELDS:
            if key in body:
                body.pop(key, None)
                removed += 1
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="output 配下の JSON から answer_result_* デバッグ用フィールドを削除する",
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path("/Users/yuki/development/exam_scraper/output"),
        help="検索のベースディレクトリ（デフォルト: output）",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="実際にファイルを書き換える（省略時は dry-run）",
    )
    args = parser.parse_args()

    base_dir: Path = args.base_dir.resolve()
    if not base_dir.exists():
        raise SystemExit(f"base_dir not found: {base_dir}")

    removals: list[Removal] = []
    for json_path in sorted(base_dir.rglob("*.json")):
        try:
            payload = load_json(json_path)
        except Exception:
            continue
        removed_count = remove_from_payload(payload)
        if removed_count:
            removals.append(Removal(json_path=json_path, removed_count=removed_count))
            if args.apply:
                save_json(json_path, payload)

    total_removed = sum(item.removed_count for item in removals)
    if args.apply:
        print(f"[OK] files_updated={len(removals)} fields_removed={total_removed}")
    else:
        print(f"[DRY-RUN] files_to_update={len(removals)} fields_to_remove={total_removed}")
        for item in removals[:50]:
            print(f"- {item.json_path} ({item.removed_count} fields)")
        if len(removals) > 50:
            print(f"... and {len(removals) - 50} more")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

