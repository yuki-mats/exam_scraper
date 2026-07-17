from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


class WriteTransactionError(RuntimeError):
    pass


def capture_write_snapshot(
    repo_root: Path,
    roots: tuple[Path, ...] | list[Path],
    backup_root: Path,
) -> dict[str, Any]:
    """Capture restorable bytes for the serialized maintenance write scope."""

    resolved_repo = repo_root.resolve()
    backup_root.mkdir(parents=True, exist_ok=False)
    entries: dict[str, dict[str, Any]] = {}
    root_keys: list[str] = []
    backup_index = 0

    def add(path: Path) -> None:
        nonlocal backup_index
        if path.is_symlink():
            raise WriteTransactionError(
                f"書込transaction対象にsymlinkは使用できません: {path}"
            )
        relative = path.relative_to(resolved_repo).as_posix()
        if not path.exists():
            entries[relative] = {"kind": "missing"}
            return
        mode = path.stat().st_mode & 0o777
        if path.is_dir():
            entries[relative] = {"kind": "directory", "mode": mode}
            return
        if not path.is_file():
            raise WriteTransactionError(
                f"書込transaction対象が通常fileではありません: {path}"
            )
        data = path.read_bytes()
        backup_index += 1
        backup_name = f"{backup_index:08d}.bin"
        backup_path = backup_root / backup_name
        temporary = backup_path.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(backup_path)
        entries[relative] = {
            "kind": "file",
            "mode": mode,
            "backupFile": backup_name,
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    for raw_root in dict.fromkeys(Path(value).resolve() for value in roots):
        if not raw_root.is_relative_to(resolved_repo):
            raise WriteTransactionError("書込transaction対象がrepository外です。")
        root_keys.append(raw_root.relative_to(resolved_repo).as_posix())
        add(raw_root)
        if not raw_root.is_dir():
            continue
        for current_root, dir_names, file_names in os.walk(
            raw_root, followlinks=False
        ):
            current = Path(current_root)
            dir_names.sort()
            file_names.sort()
            for name in dir_names:
                add(current / name)
            for name in file_names:
                add(current / name)
    return {"roots": root_keys, "entries": entries}


def restore_write_snapshot(
    repo_root: Path,
    snapshot: Mapping[str, Any],
    backup_root: Path,
) -> list[str]:
    """Restore a captured scope and return the repository paths restored."""

    resolved_repo = repo_root.resolve()
    raw_roots = snapshot.get("roots")
    raw_entries = snapshot.get("entries")
    if not isinstance(raw_roots, list) or not isinstance(raw_entries, Mapping):
        raise WriteTransactionError("書込transaction baselineの形式が不正です。")

    entries: dict[str, Mapping[str, Any]] = {}
    for raw_path, raw_entry in raw_entries.items():
        relative = _safe_relative(resolved_repo, raw_path)
        if not isinstance(raw_entry, Mapping):
            raise WriteTransactionError("書込transaction entryの形式が不正です。")
        entries[relative.as_posix()] = raw_entry
    roots = [_safe_relative(resolved_repo, value) for value in raw_roots]
    if any(root.as_posix() not in entries for root in roots):
        raise WriteTransactionError("書込transaction rootのbaselineがありません。")

    # Validate and load every backup before changing the live tree.  Detecting
    # a corrupt later backup after earlier paths were restored would leave a
    # half-rolled-back run, which is worse than keeping the whole failed delta.
    file_data: dict[str, bytes] = {}
    for relative, entry in sorted(entries.items()):
        kind = str(entry.get("kind") or "")
        if kind in {"directory", "missing"}:
            continue
        if kind != "file":
            raise WriteTransactionError(
                f"書込transaction entryのkindが不正です: {relative}"
            )
        backup_name = str(entry.get("backupFile") or "")
        backup_path = backup_root / backup_name
        if (
            not backup_name
            or Path(backup_name).name != backup_name
            or not backup_path.is_file()
            or backup_path.is_symlink()
        ):
            raise WriteTransactionError(
                f"書込transaction backupを確認できません: {relative}"
            )
        data = backup_path.read_bytes()
        expected = str(entry.get("sha256") or "")
        if not expected or hashlib.sha256(data).hexdigest() != expected:
            raise WriteTransactionError(
                f"書込transaction backupのhashが一致しません: {relative}"
            )
        file_data[relative] = data

    current_paths: set[str] = set()
    current_directories: set[str] = set()
    for relative_root in roots:
        root = resolved_repo / relative_root
        current_paths.add(relative_root.as_posix())
        if not root.is_dir() or root.is_symlink():
            continue
        current_directories.add(relative_root.as_posix())
        for current_root, dir_names, file_names in os.walk(
            root, followlinks=False
        ):
            current = Path(current_root)
            current_directories.add(
                current.relative_to(resolved_repo).as_posix()
            )
            current_paths.update(
                (current / name).relative_to(resolved_repo).as_posix()
                for name in [*dir_names, *file_names]
            )

    # A read-only directory can be part of a valid baseline.  Temporarily make
    # the live directories writable, then restore their exact modes after all
    # children have been recovered.
    for relative in sorted(
        current_directories, key=lambda value: len(Path(value).parts)
    ):
        path = resolved_repo / relative
        if path.is_dir() and not path.is_symlink():
            path.chmod((path.stat().st_mode & 0o777) | 0o700)

    restored: set[str] = set()
    for relative in sorted(
        current_paths - set(entries),
        key=lambda value: len(Path(value).parts),
        reverse=True,
    ):
        path = resolved_repo / relative
        _remove_path(path)
        restored.add(relative)

    directories = [
        relative
        for relative, entry in entries.items()
        if entry.get("kind") == "directory"
    ]
    for relative in sorted(directories, key=lambda value: len(Path(value).parts)):
        path = resolved_repo / relative
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            _remove_path(path)
        path.mkdir(parents=True, exist_ok=True)
        path.chmod((path.stat().st_mode & 0o777) | 0o700)

    for relative, entry in sorted(entries.items()):
        kind = str(entry.get("kind") or "")
        path = resolved_repo / relative
        if kind == "directory":
            continue
        if kind == "missing":
            if path.exists() or path.is_symlink():
                _remove_path(path)
                restored.add(relative)
            continue
        data = file_data[relative]
        if path.is_symlink() or (path.exists() and not path.is_file()):
            _remove_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.rollback-",
            dir=path.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            temporary.write_bytes(data)
            _apply_mode(temporary, entry.get("mode"))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        restored.add(relative)
    for relative in sorted(
        directories,
        key=lambda value: len(Path(value).parts),
        reverse=True,
    ):
        _apply_mode(resolved_repo / relative, entries[relative].get("mode"))
    return sorted(restored)


def _safe_relative(repo_root: Path, value: Any) -> Path:
    relative = Path(str(value))
    absolute = Path(os.path.abspath(repo_root / relative))
    if relative.is_absolute() or not absolute.is_relative_to(repo_root):
        raise WriteTransactionError("書込transaction pathがrepository外です。")
    return absolute.relative_to(repo_root)


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    if path.is_dir():
        for child in sorted(
            path.iterdir(), key=lambda value: len(value.parts), reverse=True
        ):
            _remove_path(child)
        path.rmdir()


def _apply_mode(path: Path, value: Any) -> None:
    if isinstance(value, int):
        path.chmod(value & 0o777)
