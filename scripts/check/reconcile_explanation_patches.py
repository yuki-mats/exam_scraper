#!/usr/bin/env python3
"""
Reconcile explanationText patch files against source question_*_merged.json files.

Goal:
- For each list_group_id directory under <root>, ensure every source file in 20_merged_1 has a
  corresponding patch file in 21_explanationText_added.
- For each (source, patch) pair, ensure the patch array is in the same order as source question_bodies,
  and that every patch entry's (original_question_id, question_url) matches the source.

This script NEVER generates explanationText content. It only moves/reorders existing patch entries.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
import shutil


PATCH_DIR = "21_explanationText_added"
SOURCE_DIR = "20_merged_1"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def is_list_group_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    if not path.name.isdigit():
        return False
    return (path / SOURCE_DIR).is_dir() and (path / PATCH_DIR).is_dir()


def patch_key(entry: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    oid = entry.get("original_question_id")
    url = entry.get("question_url")
    if isinstance(oid, str) and isinstance(url, str) and oid and url:
        return (oid, url)
    return None


def source_keys(source_question_bodies: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    keys: List[Tuple[str, str]] = []
    for q in source_question_bodies:
        oid = q.get("original_question_id")
        url = q.get("question_url")
        if not isinstance(oid, str) or not isinstance(url, str):
            raise ValueError("source question_bodies must contain original_question_id and question_url")
        keys.append((oid, url))
    return keys


def build_patch_entry_index(patch_files: List[Path]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    index: Dict[Tuple[str, str], Dict[str, Any]] = {}
    duplicates: List[Tuple[Tuple[str, str], Path]] = []
    for p in patch_files:
        data = load_json(p)
        if not isinstance(data, list):
            raise ValueError(f"patch must be an array: {p}")
        for entry in data:
            if not isinstance(entry, dict):
                continue
            key = patch_key(entry)
            if not key:
                continue
            if key in index:
                duplicates.append((key, p))
                continue
            index[key] = entry
    if duplicates:
        # Be strict: duplicates mean we cannot safely choose which entry is canonical.
        dupe_str = ", ".join([f"{k[0]}@{path.name}" for k, path in duplicates[:10]])
        raise ValueError(f"duplicate patch entries detected (showing up to 10): {dupe_str}")
    return index


_PATCH_SUFFIX_RE = re.compile(r"_explanationText_added_\\d{8}_\\d{4}(?:_\\d+)?\\.json$")


def guess_patch_filename_from_source(source_file: Path, patch_dir: Path) -> Path:
    """
    Prefer creating patch files with the same base as the source file, plus a stable suffix.
    Use a deterministic suffix so repeated runs are idempotent.
    """
    base = source_file.name
    if not base.endswith(".json"):
        raise ValueError(f"unexpected source filename: {source_file}")
    stem = base[:-5]
    # If there is an existing patch that matches the stem prefix, reuse its exact filename.
    # This avoids churn for already-existing files.
    for existing in sorted(patch_dir.glob("*.json")):
        if existing.name.startswith(stem + "_merged_explanationText_added_") or existing.name.startswith(
            stem + "_explanationText_added_"
        ):
            return existing
    # Otherwise create a deterministic "reconciled" filename (no timestamp) to keep stable diffs.
    return patch_dir / f"{stem}_explanationText_added_reconciled.json"


@dataclass
class ReconcileResult:
    changed_files: List[Path]
    created_files: List[Path]
    missing_entries: List[Tuple[Tuple[str, str], Path]]
    unused_entries: List[Tuple[str, str]]


def reconcile_list_group(list_group_dir: Path, apply: bool) -> ReconcileResult:
    source_dir = list_group_dir / SOURCE_DIR
    patch_dir = list_group_dir / PATCH_DIR
    source_files = sorted(source_dir.glob("question_*_merged*.json"))
    patch_files = sorted(patch_dir.glob("*.json"))

    patch_index = build_patch_entry_index(patch_files)

    changed: List[Path] = []
    created: List[Path] = []
    missing: List[Tuple[Tuple[str, str], Path]] = []

    used_keys: set[Tuple[str, str]] = set()

    backup_root: Optional[Path] = None
    if apply:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_root = patch_dir / "old" / f"reconcile_{stamp}"
        backup_root.mkdir(parents=True, exist_ok=True)

    for src in source_files:
        src_data = load_json(src)
        if not isinstance(src_data, dict) or not isinstance(src_data.get("question_bodies"), list):
            raise ValueError(f"invalid source JSON: {src}")
        keys = source_keys([q for q in src_data["question_bodies"] if isinstance(q, dict)])

        out_path = guess_patch_filename_from_source(src, patch_dir)
        out_entries: List[Dict[str, Any]] = []
        for k in keys:
            entry = patch_index.get(k)
            if entry is None:
                missing.append((k, src))
                # Keep placeholder to preserve array length; caller must fix manually.
                out_entries.append(
                    {
                        "original_question_id": k[0],
                        "question_url": k[1],
                        "explanationText": [],
                    }
                )
                continue
            used_keys.add(k)
            out_entries.append(entry)

        # Determine if content differs from current file.
        cur_data: Optional[Any] = None
        if out_path.exists():
            cur_data = load_json(out_path)
        if cur_data != out_entries:
            if apply:
                # Backup the current file before overwriting.
                if out_path.exists() and backup_root is not None:
                    shutil.copy2(out_path, backup_root / out_path.name)
                dump_json(out_path, out_entries)
            if out_path.exists():
                changed.append(out_path)
            else:
                created.append(out_path)

    unused = sorted([k for k in patch_index.keys() if k not in used_keys])
    return ReconcileResult(
        changed_files=changed,
        created_files=created,
        missing_entries=missing,
        unused_entries=unused,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="Root directory that contains list_group_id folders.")
    ap.add_argument("--dry-run", action="store_true", help="Do not write files; just report.")
    ap.add_argument("--apply", action="store_true", help="Write reconciled patch files.")
    args = ap.parse_args()

    if args.dry_run == args.apply:
        raise SystemExit("Specify exactly one of --dry-run or --apply.")

    root = Path(args.root)
    if not root.exists():
        raise SystemExit(f"root not found: {root}")

    targets = sorted([p for p in root.iterdir() if is_list_group_dir(p)])
    if not targets:
        raise SystemExit("no list_group_id dirs found under root (expected digits/*/{20_merged_1,21_explanationText_added})")

    any_missing = 0
    any_unused = 0
    any_changed = 0
    any_created = 0

    for t in targets:
        res = reconcile_list_group(t, apply=args.apply)
        any_changed += len(res.changed_files)
        any_created += len(res.created_files)
        any_missing += len(res.missing_entries)
        any_unused += len(res.unused_entries)

        print(f"[{t.name}] changed={len(res.changed_files)} created={len(res.created_files)} missing={len(res.missing_entries)} unused={len(res.unused_entries)}")
        if res.missing_entries:
            # Show a few examples
            for (oid, url), src in res.missing_entries[:5]:
                print(f"  [MISSING] {oid} {url} (source={src.name})")
        if res.unused_entries:
            for oid, url in res.unused_entries[:5]:
                print(f"  [UNUSED] {oid} {url}")

    if any_missing:
        print(f"[WARN] missing entries detected: {any_missing} (placeholders written when --apply)")
    if any_unused:
        print(f"[WARN] unused patch entries detected: {any_unused} (these will be orphaned unless addressed)")

    if args.dry_run:
        print("[DRY-RUN] no files written")
    else:
        print("[APPLY] reconciliation written")

    # Non-zero exit when missing/unused, to force manual review.
    if any_missing or any_unused:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
