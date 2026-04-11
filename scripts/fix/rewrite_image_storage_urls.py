#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

if __package__ in {None, ""}:
    ROOT_DIR = Path(__file__).resolve().parents[2]
    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))
    from scripts.common.image_storage_urls import normalize_image_url_fields
else:
    ROOT_DIR = Path(__file__).resolve().parents[2]
    from scripts.common.image_storage_urls import normalize_image_url_fields


DEFAULT_OUTPUT_ROOT = ROOT_DIR / "output"


def resolve_qualification_dirs(
    output_root: Path,
    qualifications: list[str] | None,
) -> list[Path]:
    if qualifications:
        dirs = [output_root / qualification for qualification in qualifications]
    else:
        dirs = [path for path in sorted(output_root.iterdir()) if path.is_dir()]

    resolved_dirs: list[Path] = []
    for directory in dirs:
        if not directory.exists():
            raise FileNotFoundError(f"資格ディレクトリが見つかりません: {directory}")
        questions_json_dir = directory / "questions_json"
        if not questions_json_dir.exists():
            print(f"[WARN] questions_json がないためスキップ: {directory}")
            continue
        resolved_dirs.append(directory)
    return resolved_dirs


def iter_current_json_files(questions_json_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in questions_json_dir.rglob("*.json")
        if "old" not in path.parts
    )


def build_archive_dir(directory: Path, run_timestamp: str) -> Path:
    archive_root = directory / "old"
    archive_dir = archive_root / run_timestamp
    suffix = 1
    while archive_dir.exists():
        archive_dir = archive_root / f"{run_timestamp}_{suffix:02d}"
        suffix += 1
    archive_dir.mkdir(parents=True, exist_ok=False)
    return archive_dir


def rewrite_single_file(path: Path, qualification: str) -> tuple[bool, int, dict]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    changes = normalize_image_url_fields(data, qualification)
    return changes > 0, changes, data


def rewrite_qualification(
    qualification_dir: Path,
    *,
    dry_run: bool,
    run_timestamp: str,
) -> tuple[int, int]:
    qualification = qualification_dir.name
    questions_json_dir = qualification_dir / "questions_json"
    target_files = iter_current_json_files(questions_json_dir)

    changed_files = 0
    changed_fields = 0
    archive_dirs: dict[Path, Path] = {}

    for path in target_files:
        changed, changes, rewritten_data = rewrite_single_file(path, qualification)
        if not changed:
            continue

        changed_files += 1
        changed_fields += changes
        print(f"[CHANGE] {path} (fields={changes})")

        if dry_run:
            continue

        archive_dir = archive_dirs.get(path.parent)
        if archive_dir is None:
            archive_dir = build_archive_dir(path.parent, run_timestamp)
            archive_dirs[path.parent] = archive_dir

        archive_path = archive_dir / path.name
        shutil.copy2(path, archive_path)
        with path.open("w", encoding="utf-8") as f:
            json.dump(rewritten_data, f, ensure_ascii=False, indent=2)

    print(
        f"[SUMMARY] qualification={qualification} changed_files={changed_files} changed_fields={changed_fields}"
    )
    return changed_files, changed_fields


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="questions_json 配下の画像 Storage URL を正規形へ揃える"
    )
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="output ディレクトリのパス",
    )
    parser.add_argument(
        "--qualification",
        action="append",
        dest="qualifications",
        help="対象資格コード。複数指定可。未指定時は output 配下の全資格を対象にする。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="実際の書き換えを行わず、対象件数のみ確認する。",
    )
    args = parser.parse_args(argv)

    output_root = Path(args.output_root).expanduser().resolve()
    if not output_root.exists():
        raise FileNotFoundError(f"output ルートが見つかりません: {output_root}")

    qualification_dirs = resolve_qualification_dirs(output_root, args.qualifications)
    if not qualification_dirs:
        print("[INFO] 対象資格がありません。")
        return 0

    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    total_files = 0
    total_fields = 0

    for qualification_dir in qualification_dirs:
        changed_files, changed_fields = rewrite_qualification(
            qualification_dir,
            dry_run=args.dry_run,
            run_timestamp=run_timestamp,
        )
        total_files += changed_files
        total_fields += changed_fields

    mode = "DRY RUN" if args.dry_run else "WRITE"
    print(
        f"[DONE] mode={mode} qualifications={len(qualification_dirs)} changed_files={total_files} changed_fields={total_fields}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
