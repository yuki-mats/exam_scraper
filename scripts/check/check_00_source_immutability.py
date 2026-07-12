#!/usr/bin/env python3
"""00_source の新規追加だけを許可し、既存ファイルの差分を拒否する。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


DEFAULT_MANIFEST = Path("docs/contracts/00_source_sha256_manifest.jsonl")


def source_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in (root / "output").rglob("*.json"):
        relative = path.relative_to(root).as_posix()
        if path.is_file() and "00_source" in path.parts:
            hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(hashes.items()))


def load_manifest(path: Path) -> dict[str, str]:
    if not path.exists():
        raise ValueError(f"manifestがありません: {path}")
    rows: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line:
            continue
        row = json.loads(line)
        source_path, digest = row.get("path"), row.get("sha256")
        if not isinstance(source_path, str) or not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"manifestの{number}行目が不正です")
        if source_path in rows:
            raise ValueError(f"manifestのpathが重複しています: {source_path}")
        rows[source_path] = digest
    return rows


def save_manifest(path: Path, rows: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(
        json.dumps({"path": source_path, "sha256": rows[source_path]}, separators=(",", ":"))
        for source_path in sorted(rows)
    )
    path.write_text(text + "\n", encoding="utf-8")


def differences(manifest: dict[str, str], current: dict[str, str]) -> dict[str, list[str]]:
    return {
        "改変": sorted(path for path in manifest.keys() & current.keys() if manifest[path] != current[path]),
        "消失": sorted(manifest.keys() - current.keys()),
        "未登録": sorted(current.keys() - manifest.keys()),
    }


def show_differences(diff: dict[str, list[str]]) -> None:
    for label, paths in diff.items():
        if paths:
            print(f"[NG] {label}: {len(paths)} files", file=sys.stderr)
            for path in paths[:20]:
                print(f"  - {path}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="既存00_sourceが変更されていないか確認する")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--initialize", action="store_true")
    action.add_argument("--record-new", action="store_true")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    manifest_path = args.manifest if args.manifest.is_absolute() else root / args.manifest
    current = source_hashes(root)
    try:
        if args.initialize:
            if manifest_path.exists() and manifest_path.stat().st_size:
                raise ValueError("既存manifestは再初期化できません")
            save_manifest(manifest_path, current)
            print(f"[OK] manifest初期化: {len(current)} files")
            return 0

        manifest = load_manifest(manifest_path)
        diff = differences(manifest, current)
        if args.record_new:
            if diff["改変"] or diff["消失"]:
                show_differences(diff)
                print("[BLOCKED] 既存sourceに差分があるため登録しません", file=sys.stderr)
                return 1
            save_manifest(manifest_path, {**manifest, **{path: current[path] for path in diff["未登録"]}})
            print(f"[OK] 新規scrape登録: {len(diff['未登録'])} files")
            return 0

        if any(diff.values()):
            show_differences(diff)
            return 1
        print(f"[OK] 00_source差分なし: {len(manifest)} files")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[BLOCKED] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
