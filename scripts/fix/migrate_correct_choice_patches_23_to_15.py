#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from pathlib import Path


OLD_SUBDIR = "23_correctChoiceText_fixed"
NEW_SUBDIR = "15_correctChoiceText_fixed"


def files_are_same(left: Path, right: Path) -> bool:
    return left.read_bytes() == right.read_bytes()


def collect_moves(base_dir: Path) -> tuple[list[tuple[Path, Path]], list[str]]:
    moves: list[tuple[Path, Path]] = []
    errors: list[str] = []

    for group_dir in sorted(path for path in base_dir.iterdir() if path.is_dir()):
        old_dir = group_dir / OLD_SUBDIR
        if not old_dir.exists():
            continue
        new_dir = group_dir / NEW_SUBDIR
        for source_path in sorted(old_dir.glob("*.json")):
            target_path = new_dir / source_path.name
            if target_path.exists() and not files_are_same(source_path, target_path):
                errors.append(f"移動先に異なる同名ファイルがあります: {target_path}")
                continue
            moves.append((source_path, target_path))

    return moves, errors


def migrate(base_dir: Path, apply: bool) -> int:
    if not base_dir.exists():
        raise FileNotFoundError(f"base_dir not found: {base_dir}")

    moves, errors = collect_moves(base_dir)
    if errors:
        for error in errors:
            print(f"[ERROR] {error}")
        return 1

    if not moves:
        print(f"[INFO] active {OLD_SUBDIR} JSON not found under {base_dir}")
        return 0

    moved = 0
    removed_duplicate = 0
    for source_path, target_path in moves:
        if not apply:
            action = "REMOVE-DUPLICATE" if target_path.exists() else "MOVE"
            print(f"[DRY-RUN] {action}: {source_path} -> {target_path}")
            continue

        target_path.parent.mkdir(parents=True, exist_ok=True)
        if target_path.exists():
            source_path.unlink()
            removed_duplicate += 1
            print(f"[REMOVE-DUPLICATE] {source_path} (same content exists at {target_path})")
            continue

        shutil.move(str(source_path), str(target_path))
        moved += 1
        print(f"[MOVE] {source_path} -> {target_path}")

    if apply:
        print(f"[OK] moved={moved} removed_duplicate={removed_duplicate}")
    else:
        print(f"[OK] dry-run targets={len(moves)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="active 23_correctChoiceText_fixed JSON を 15_correctChoiceText_fixed へ移行する"
    )
    parser.add_argument(
        "--base-dir",
        default="/Users/yuki/development/exam_scraper/output/kounin-shinrishi/questions_json",
        help="questions_json のベースディレクトリ",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="実際に移動する。省略時は dry-run のみ。",
    )
    args = parser.parse_args()
    return migrate(Path(args.base_dir).resolve(), args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
