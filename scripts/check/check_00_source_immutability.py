#!/usr/bin/env python3
"""00_sourceの手作業改変を防ぎ、scraperによる追加・更新を管理する。"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


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


def record_parent_moves(
    manifest: dict[str, str],
    current: dict[str, str],
    diff: dict[str, list[str]],
) -> dict[str, str]:
    """00_source以下の相対名と内容を保った親ディレクトリ移動だけを反映する。"""
    if diff["改変"]:
        raise ValueError("内容が変わった00_sourceがあるため移動を登録できません")

    def move_key(path: str, digest: str) -> tuple[str, str]:
        marker = "/00_source/"
        if marker not in path:
            raise ValueError(f"00_source配下ではありません: {path}")
        return digest, path.split(marker, 1)[1]

    missing_by_key: dict[tuple[str, str], list[str]] = {}
    new_by_key: dict[tuple[str, str], list[str]] = {}
    for path in diff["消失"]:
        missing_by_key.setdefault(move_key(path, manifest[path]), []).append(path)
    for path in diff["未登録"]:
        new_by_key.setdefault(move_key(path, current[path]), []).append(path)

    if {
        key: len(paths) for key, paths in missing_by_key.items()
    } != {
        key: len(paths) for key, paths in new_by_key.items()
    }:
        raise ValueError(
            "消失と未登録が同一内容・同一ファイル名で対応しないため、親ディレクトリ移動として扱えません"
        )

    updated = dict(manifest)
    for paths in missing_by_key.values():
        for path in paths:
            updated.pop(path)
    for paths in new_by_key.values():
        for path in paths:
            updated[path] = current[path]
    return updated


def normalize_source_scope(scope: str) -> str:
    """repo相対の00_source directoryを正規化する。"""
    normalized = str(scope or "").strip().replace("\\", "/").strip("/")
    parts = normalized.split("/") if normalized else []
    if (
        not parts
        or parts[-1] != "00_source"
        or any(part in {"", ".", ".."} for part in parts)
        or parts[0] != "output"
    ):
        raise ValueError(
            "--scopeはoutput/配下の00_source directoryをrepo相対pathで指定してください"
        )
    return "/".join(parts)


def record_scrape_refresh(
    manifest: dict[str, str],
    current: dict[str, str],
    diff: dict[str, list[str]],
    *,
    scope: str,
) -> dict[str, str]:
    """scraper成功後、対象groupの新規・更新hashだけを登録する。"""
    normalized_scope = normalize_source_scope(scope)
    scope_prefix = normalized_scope + "/"
    if diff["消失"]:
        raise ValueError("00_sourceの消失があるためscrape更新を登録できません")
    changed_paths = [*diff["改変"], *diff["未登録"]]
    outside_scope = [
        path for path in changed_paths if not path.startswith(scope_prefix)
    ]
    if outside_scope:
        raise ValueError(
            "scrape対象外の00_source差分があるため登録できません: "
            + ", ".join(outside_scope[:10])
        )
    updated = dict(manifest)
    for path in changed_paths:
        updated[path] = current[path]
    return updated


def staged_source_changes(root: Path) -> list[tuple[str, ...]]:
    """index上の00_source変更をname-statusのtupleとして返す。"""
    result = subprocess.run(
        [
            "git",
            "diff",
            "--cached",
            "--name-status",
            "-z",
            "--find-renames=100%",
            "--diff-filter=MDR",
            "--",
            ":(glob)**/00_source/**",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    tokens = result.stdout.decode("utf-8").split("\0")
    if tokens and not tokens[-1]:
        tokens.pop()

    changes: list[tuple[str, ...]] = []
    index = 0
    while index < len(tokens):
        status = tokens[index]
        path_count = 2 if status.startswith(("R", "C")) else 1
        paths = tokens[index + 1 : index + 1 + path_count]
        if len(paths) != path_count:
            raise ValueError("git diffの00_source変更を解析できません")
        changes.append((status, *paths))
        index += 1 + path_count
    return changes


def staged_source_change_violations(changes: list[tuple[str, ...]]) -> list[str]:
    """内容・00_source以下の相対名を保つ親移動以外を列挙する。"""
    marker = "/00_source/"
    violations: list[str] = []
    for change in changes:
        status, *paths = change
        if status == "R100" and len(paths) == 2:
            old_path, new_path = paths
            if marker in old_path and marker in new_path:
                old_suffix = old_path.split(marker, 1)[1]
                new_suffix = new_path.split(marker, 1)[1]
                if old_suffix == new_suffix:
                    continue
        violations.append("\t".join(change))
    return violations


def show_differences(diff: dict[str, list[str]]) -> None:
    for label, paths in diff.items():
        if paths:
            print(f"[NG] {label}: {len(paths)} files", file=sys.stderr)
            for path in paths[:20]:
                print(f"  - {path}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="00_sourceの保護manifestを確認・更新する")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--initialize", action="store_true")
    action.add_argument("--record-new", action="store_true")
    action.add_argument("--record-scrape-refresh", action="store_true")
    action.add_argument("--record-moves", action="store_true")
    action.add_argument("--check-staged", action="store_true")
    parser.add_argument(
        "--scope",
        default="",
        help="--record-scrape-refreshの対象00_source directory。repo相対pathで指定する",
    )
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
        if args.record_moves:
            updated = record_parent_moves(manifest, current, diff)
            save_manifest(manifest_path, updated)
            print(f"[OK] 00_source親ディレクトリ移動登録: {len(diff['消失'])} files")
            return 0
        if args.record_new:
            if diff["改変"] or diff["消失"]:
                show_differences(diff)
                print("[BLOCKED] 既存sourceに差分があるため登録しません", file=sys.stderr)
                return 1
            save_manifest(manifest_path, {**manifest, **{path: current[path] for path in diff["未登録"]}})
            print(f"[OK] 新規scrape登録: {len(diff['未登録'])} files")
            return 0
        if args.record_scrape_refresh:
            if not args.scope:
                raise ValueError("--record-scrape-refreshには--scopeが必要です")
            updated = record_scrape_refresh(
                manifest,
                current,
                diff,
                scope=args.scope,
            )
            save_manifest(manifest_path, updated)
            print(
                "[OK] scrape更新登録: "
                f"更新={len(diff['改変'])} 新規={len(diff['未登録'])} files"
            )
            return 0

        if any(diff.values()):
            show_differences(diff)
            return 1
        if args.check_staged:
            changes = staged_source_changes(root)
            violations = staged_source_change_violations(changes)
            if violations:
                print(
                    "[BLOCKED] 既存00_sourceの内容・ファイル名変更はできません",
                    file=sys.stderr,
                )
                for violation in violations:
                    print(f"  - {violation}", file=sys.stderr)
                return 1
            print(f"[OK] staged 00_source親ディレクトリ移動: {len(changes)} files")
            return 0
        print(f"[OK] 00_source差分なし: {len(manifest)} files")
        return 0
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"[BLOCKED] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
