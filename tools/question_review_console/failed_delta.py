from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping


def unresolved_failed_delta_paths(
    repo_root: Path,
    qualification: str,
    list_group_id: str | None = None,
) -> tuple[str, ...]:
    """Return live paths changed by a failed run and not superseded by success."""

    root = (
        repo_root.resolve()
        / "output"
        / "question_review_console"
        / "workflow_runs"
        / qualification
    )
    states: dict[str, list[Mapping[str, Any]]] = {}
    unknown_runs: dict[str, Mapping[str, Any]] = {}
    if not root.is_dir():
        return ()
    for manifest_path in sorted(root.glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, Mapping):
            continue
        status = str(manifest.get("status") or "")
        if (
            (
                status in {"running", "validating"}
                and manifest.get("kind") == "human"
            )
            or (
                status == "interrupted"
                and manifest.get("deltaUnknown") is True
            )
        ):
            if _manifest_in_scope(manifest, qualification, list_group_id):
                unknown_runs[
                    manifest_path.relative_to(repo_root.resolve()).as_posix()
                ] = manifest
            continue
        if status not in {"failed", "succeeded"}:
            continue
        result = manifest.get("result")
        if not isinstance(result, Mapping):
            continue
        changed_files = result.get("changedFiles")
        if not isinstance(changed_files, list):
            continue
        for raw in changed_files:
            relative = _safe_relative_path(repo_root, raw)
            if relative is None or not _in_scope(
                relative, qualification, list_group_id
            ):
                continue
            key = relative.as_posix()
            if status == "failed":
                states.setdefault(key, []).append(manifest)
        if status == "succeeded":
            resolved_paths = result.get("resolvedFailedDeltaPaths")
            resolved_keys: set[str] = set()
            if isinstance(resolved_paths, list):
                for raw in resolved_paths:
                    relative = _safe_relative_path(repo_root, raw)
                    if relative is None:
                        continue
                    resolved_keys.add(relative.as_posix())
                    if _in_scope(relative, qualification, list_group_id):
                        key = relative.as_posix()
                        remaining = [
                            failed
                            for failed in states.get(key, [])
                            if not _success_supersedes(failed, manifest)
                        ]
                        if remaining:
                            states[key] = remaining
                        else:
                            states.pop(key, None)
            for key, interrupted in list(unknown_runs.items()):
                if key in resolved_keys and _success_supersedes(
                    interrupted, manifest
                ):
                    unknown_runs.pop(key, None)
    return tuple(sorted({*states, *unknown_runs}))


def _safe_relative_path(repo_root: Path, raw: Any) -> Path | None:
    path = Path(str(raw))
    resolved_root = repo_root.resolve()
    absolute = Path(
        os.path.abspath(path if path.is_absolute() else resolved_root / path)
    )
    if not absolute.is_relative_to(resolved_root):
        return None
    return absolute.relative_to(resolved_root)


def _in_scope(
    path: Path,
    qualification: str,
    list_group_id: str | None,
) -> bool:
    parts = path.parts
    if parts[:3] == ("prompt", "qualification_docs", qualification):
        return True
    if parts[:2] != ("output", qualification):
        return False
    if list_group_id is None:
        return True
    if len(parts) >= 4 and parts[2] == "questions_json":
        return parts[3] == list_group_id
    if len(parts) >= 4 and parts[2] == "law_evidence":
        return parts[3] == list_group_id
    if len(parts) >= 5 and parts[2:4] == (
        "review",
        "law_revision_audit",
    ):
        return parts[4] == f"{list_group_id}_law_revision_audit.jsonl"
    return True


def _manifest_in_scope(
    manifest: Mapping[str, Any],
    qualification: str,
    list_group_id: str | None,
) -> bool:
    if str(manifest.get("qualification") or "") != qualification:
        return False
    if list_group_id is None:
        return True
    return list_group_id in {
        str(value) for value in manifest.get("targetGroupIds") or []
    }


def _success_supersedes(
    interrupted: Mapping[str, Any], succeeded: Mapping[str, Any]
) -> bool:
    if str(interrupted.get("workType") or "") != str(
        succeeded.get("workType") or ""
    ):
        return False
    interrupted_stages = {
        str(value)
        for value in interrupted.get("stageIds") or [interrupted.get("stageId")]
        if value
    }
    succeeded_stages = {
        str(value)
        for value in succeeded.get("stageIds") or [succeeded.get("stageId")]
        if value
    }
    if not interrupted_stages.issubset(succeeded_stages):
        return False
    interrupted_groups = {
        str(value) for value in interrupted.get("targetGroupIds") or []
    }
    succeeded_groups = {
        str(value) for value in succeeded.get("targetGroupIds") or []
    }
    if not interrupted_groups.issubset(succeeded_groups):
        return False
    # An unknown delta can only be cleared by a run whose write contract covers
    # the interrupted run.  Older manifests do not carry the full contract, so
    # they deliberately remain blocked until a person resolves them explicitly.
    contract_fields = (
        "allowedPatchDirs",
        "allowedWriteAreas",
        "allowedPatchFiles",
        "allowedWriteFiles",
        "targetRecordScopes",
    )
    if any(
        field not in interrupted or field not in succeeded
        for field in contract_fields
    ):
        return False

    for field in ("allowedPatchDirs", "allowedWriteAreas"):
        interrupted_values = {
            str(value) for value in interrupted.get(field) or []
        }
        succeeded_values = {
            str(value) for value in succeeded.get(field) or []
        }
        if not interrupted_values.issubset(succeeded_values):
            return False

    for field in ("allowedPatchFiles", "allowedWriteFiles"):
        interrupted_values = {
            str(value) for value in interrupted.get(field) or []
        }
        succeeded_values = {
            str(value) for value in succeeded.get(field) or []
        }
        # Empty means every file under the corresponding dir/area, not no file.
        if not interrupted_values:
            if succeeded_values:
                return False
        elif succeeded_values and not interrupted_values.issubset(
            succeeded_values
        ):
            return False
    interrupted_scopes = interrupted.get("targetRecordScopes")
    succeeded_scopes = succeeded.get("targetRecordScopes")
    if not isinstance(interrupted_scopes, Mapping) or not isinstance(
        succeeded_scopes, Mapping
    ):
        return False
    for scopes in (interrupted_scopes, succeeded_scopes):
        for path, groups in scopes.items():
            if (
                not str(path)
                or not isinstance(groups, list)
                or not groups
                or any(
                    not isinstance(group, list)
                    or not {str(alias) for alias in group if alias}
                    for group in groups
                )
            ):
                return False
    if not interrupted_scopes:
        if str(interrupted.get("workType") or "") in {
            "maintenance",
            "rework",
        } and (
            interrupted.get("targetQuestionIds")
            or interrupted.get("allowedPatchDirs")
        ):
            # 問題単位の旧manifest又は不完全contractは、anchor一致だけで
            # 解決済みにせず、明示的なfile別record scopeを必須にする。
            return False
        interrupted_questions = {
            str(value) for value in interrupted.get("targetQuestionIds") or []
        }
        succeeded_questions = {
            str(value) for value in succeeded.get("targetQuestionIds") or []
        }
        if not interrupted_questions.issubset(succeeded_questions):
            return False
    for path, interrupted_groups in interrupted_scopes.items():
        succeeded_groups = succeeded_scopes.get(path)
        if not isinstance(interrupted_groups, list) or not isinstance(
            succeeded_groups, list
        ):
            return False
        interrupted_values = {
            tuple(sorted(str(alias) for alias in group if alias))
            for group in interrupted_groups
            if isinstance(group, list) and group
        }
        succeeded_values = {
            tuple(sorted(str(alias) for alias in group if alias))
            for group in succeeded_groups
            if isinstance(group, list) and group
        }
        if not interrupted_values.issubset(succeeded_values):
            return False
    return True
