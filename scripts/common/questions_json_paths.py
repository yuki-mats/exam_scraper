from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def is_list_group_dir(path: Path) -> bool:
    return path.is_dir() and path.name.isdigit()


def list_group_ids_in_base_dir(base_dir: Path) -> list[str]:
    resolved = base_dir.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"questions_json ディレクトリが見つかりません: {resolved}")
    return [child.name for child in sorted(resolved.iterdir()) if is_list_group_dir(child)]


def resolve_list_group_base_dir(
    list_group_id: str,
    base_dir: str | None,
    *,
    repo_root: Path = REPO_ROOT,
) -> Path:
    if base_dir:
        resolved = Path(base_dir).expanduser().resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"--base-dir が存在しません: {resolved}")
        if not (resolved / list_group_id).exists():
            raise FileNotFoundError(f"list_group_id ディレクトリが見つかりません: {resolved / list_group_id}")
        return resolved

    candidates = list((repo_root / "output").glob(f"*/questions_json/{list_group_id}"))
    if not candidates:
        raise FileNotFoundError(
            f"list_group_id={list_group_id} が見つかりません。--base-dir を指定してください。"
        )
    if len(candidates) > 1:
        choices = "\n".join(str(path.parent) for path in candidates)
        raise FileNotFoundError(
            "候補が複数見つかりました。--base-dir を明示してください:\n" + choices
        )
    return candidates[0].parent.resolve()


def resolve_qualification_questions_json_dir(
    qualification: str,
    base_dir: str | None,
    *,
    repo_root: Path = REPO_ROOT,
) -> Path:
    if base_dir:
        resolved = Path(base_dir).expanduser().resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"--base-dir が存在しません: {resolved}")
        if resolved.name != "questions_json":
            raise FileNotFoundError(f"--base-dir は questions_json ディレクトリを指定してください: {resolved}")
        if resolved.parent.name != qualification:
            raise FileNotFoundError(
                f"--base-dir の資格コードが target_id と一致しません: {resolved.parent.name} != {qualification}"
            )
        return resolved

    resolved = (repo_root / "output" / qualification / "questions_json").resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"資格コードに対応する questions_json が見つかりません: {resolved}")
    return resolved


def resolve_category_json_path(base_dir: Path, category_json: str | None) -> Path:
    if category_json:
        return Path(category_json).expanduser().resolve()
    return (base_dir.parent / "category" / "category.json").resolve()
