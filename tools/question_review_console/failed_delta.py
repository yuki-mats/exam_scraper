from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from tools.question_review_console.workflow_catalog import WorkflowCatalog


def unresolved_failed_delta_paths(
    repo_root: Path,
    qualification: str,
    list_group_id: str | None = None,
) -> tuple[str, ...]:
    """Return live paths changed by a failed run and not superseded by success."""

    return _failed_delta_paths(repo_root, qualification, list_group_id)


def resolvable_failed_delta_paths(
    repo_root: Path,
    qualification: str,
    resolver: Mapping[str, Any],
    list_group_id: str | None = None,
) -> tuple[str, ...]:
    """Return unresolved paths fully covered by a proposed run contract."""

    return _failed_delta_paths(
        repo_root,
        qualification,
        list_group_id,
        resolver=resolver,
    )


def _failed_delta_paths(
    repo_root: Path,
    qualification: str,
    list_group_id: str | None,
    *,
    resolver: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    root = (
        repo_root.resolve()
        / "output"
        / "question_review_console"
        / "workflow_runs"
        / qualification
    )
    states: dict[str, list[Mapping[str, Any]]] = {}
    record_scope_coverage: dict[tuple[str, int], set[tuple[str, ...]]] = {}
    unknown_runs: dict[str, Mapping[str, Any]] = {}
    if not root.is_dir():
        return ()
    patch_stage_by_dir = {
        str(stage["patchDir"]): str(stage["id"])
        for stage in WorkflowCatalog(repo_root).load()["stages"]
        if stage.get("patchDir")
    }
    for manifest_path in sorted(root.glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, Mapping):
            continue
        status = str(manifest.get("status") or "")
        validated_success = bool(
            status == "validating"
            and manifest.get("kind") == "human"
            and manifest.get("receiptValidated") is True
        )
        if (
            (
                status in {"running", "validating"}
                and manifest.get("kind") == "human"
                and not validated_success
            )
            or (
                status == "interrupted"
                and manifest.get("deltaUnknown") is True
            )
        ):
            if _manifest_in_scope(manifest, qualification, list_group_id):
                if _isolated_interrupted_run_is_clean(manifest):
                    continue
                unknown_runs[
                    manifest_path.relative_to(repo_root.resolve()).as_posix()
                ] = manifest
            continue
        effective_status = "succeeded" if validated_success else status
        if effective_status not in {"failed", "succeeded"}:
            continue
        if effective_status == "failed" and _rollback_restored_run(manifest):
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
            if effective_status == "failed":
                states.setdefault(key, []).append(manifest)
        if effective_status == "succeeded":
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
                            if not _success_supersedes_path(
                                failed,
                                manifest,
                                relative,
                                patch_stage_by_dir=patch_stage_by_dir,
                            )
                        ]
                        for failed in list(remaining):
                            scopes = _compatible_path_record_scopes(
                                failed,
                                manifest,
                                relative,
                                patch_stage_by_dir=patch_stage_by_dir,
                            )
                            if scopes is None:
                                continue
                            failed_scopes, succeeded_scopes = scopes
                            coverage = record_scope_coverage.setdefault(
                                (key, id(failed)),
                                set(),
                            )
                            coverage.update(succeeded_scopes)
                            if failed_scopes.issubset(coverage):
                                remaining.remove(failed)
                        if remaining:
                            states[key] = remaining
                        else:
                            states.pop(key, None)
            for key, interrupted in list(unknown_runs.items()):
                if key in resolved_keys and _success_supersedes(
                    interrupted, manifest
                ):
                    unknown_runs.pop(key, None)
    if resolver is None:
        return tuple(sorted({*states, *unknown_runs}))
    resolvable = {
        key
        for key, failures in states.items()
        if failures
        and any(
            _success_contributes_to_path(
                failed,
                resolver,
                Path(key),
                patch_stage_by_dir=patch_stage_by_dir,
            )
            for failed in failures
        )
    }
    resolvable.update(
        key
        for key, interrupted in unknown_runs.items()
        if _success_supersedes(interrupted, resolver)
    )
    return tuple(sorted(resolvable))


def _success_contributes_to_path(
    failed: Mapping[str, Any],
    succeeded: Mapping[str, Any],
    path: Path,
    *,
    patch_stage_by_dir: Mapping[str, str],
) -> bool:
    scopes = _compatible_path_record_scopes(
        failed,
        succeeded,
        path,
        patch_stage_by_dir=patch_stage_by_dir,
    )
    if scopes is None:
        return False
    failed_scopes, succeeded_scopes = scopes
    return not failed_scopes or bool(failed_scopes & succeeded_scopes)


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
    if not _compatible_work_types(interrupted, succeeded):
        return False
    if not _responsible_stages(interrupted).issubset(
        _responsible_stages(succeeded)
    ):
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
            # 不完全contractはanchor一致だけで解決済みにせず、
            # 明示的なfile別record scopeを必須にする。
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


def _success_supersedes_path(
    failed: Mapping[str, Any],
    succeeded: Mapping[str, Any],
    path: Path,
    *,
    patch_stage_by_dir: Mapping[str, str],
) -> bool:
    """Return whether a success safely resolves one known failed path.

    A failed run can span several years and files.  A later run is allowed to
    resolve only the file it explicitly verified, so comparing the two whole
    run contracts would incorrectly require every original year to be rerun at
    once.  Unknown interrupted deltas still use the stricter whole-run check in
    ``_success_supersedes`` because their affected path is not known.
    """

    scopes = _compatible_path_record_scopes(
        failed,
        succeeded,
        path,
        patch_stage_by_dir=patch_stage_by_dir,
    )
    return scopes is not None and scopes[0].issubset(scopes[1])


def _compatible_path_record_scopes(
    failed: Mapping[str, Any],
    succeeded: Mapping[str, Any],
    path: Path,
    *,
    patch_stage_by_dir: Mapping[str, str],
) -> tuple[set[tuple[str, ...]], set[tuple[str, ...]]] | None:
    """Return compatible failed/success record scopes for one explicit path."""

    if not _compatible_work_types(failed, succeeded):
        return None
    if _is_law_audit_sidecar(path) and any(
        "law_audit" not in _responsible_stages(manifest)
        for manifest in (failed, succeeded)
    ):
        return None
    if not _responsible_stages_for_path(
        failed,
        path,
        patch_stage_by_dir=patch_stage_by_dir,
    ).issubset(
        _responsible_stages_for_path(
            succeeded,
            path,
            patch_stage_by_dir=patch_stage_by_dir,
        )
    ):
        return None
    if not _has_complete_contract(failed) or not _has_complete_contract(
        succeeded
    ):
        return None
    if not _path_allowed_by_contract(failed, path) or not _path_allowed_by_contract(
        succeeded, path
    ):
        return None

    qualification = str(failed.get("qualification") or "")
    if qualification != str(succeeded.get("qualification") or ""):
        return None
    group_id = _path_group_id(path, qualification)
    if group_id is not None:
        failed_groups = {str(value) for value in failed.get("targetGroupIds") or []}
        succeeded_groups = {
            str(value) for value in succeeded.get("targetGroupIds") or []
        }
        if group_id not in failed_groups or group_id not in succeeded_groups:
            return None

    if _requires_record_scope(path, qualification):
        failed_groups = _record_scope_groups(failed, path)
        succeeded_groups = _record_scope_groups(succeeded, path)
        if failed_groups is None or succeeded_groups is None:
            return None
        return failed_groups, succeeded_groups
    return set(), set()


def _isolated_interrupted_run_is_clean(manifest: Mapping[str, Any]) -> bool:
    """An interrupted child sandbox has no canonical delta before server commit."""

    return bool(
        manifest.get("parentRunId")
        and manifest.get("retrySafe") is True
        and manifest.get("candidateTransactionOpen") is not True
        and manifest.get("parallelStrategy")
        in {"isolated_question_batch", "structured_candidate_batch"}
        and manifest.get("sandbox") in {"workspace-write", "read-only"}
    )


def _rollback_restored_run(manifest: Mapping[str, Any]) -> bool:
    rollback = manifest.get("rollback")
    return bool(
        isinstance(rollback, Mapping)
        and rollback.get("status") == "succeeded"
        and rollback.get("deltaUnknown") is not True
        and not rollback.get("remainingChangedFiles")
    )


def _has_complete_contract(manifest: Mapping[str, Any]) -> bool:
    return all(
        field in manifest
        for field in (
            "allowedPatchDirs",
            "allowedWriteAreas",
            "allowedPatchFiles",
            "allowedWriteFiles",
            "targetRecordScopes",
        )
    )


def _responsible_stages(manifest: Mapping[str, Any]) -> set[str]:
    policy_versions = manifest.get("policyVersions")
    if isinstance(policy_versions, Mapping) and policy_versions:
        return {str(value) for value in policy_versions}
    return {
        str(value)
        for value in manifest.get("stageIds") or [manifest.get("stageId")]
        if value
    }


def _responsible_stages_for_path(
    manifest: Mapping[str, Any],
    path: Path,
    *,
    patch_stage_by_dir: Mapping[str, str],
) -> set[str]:
    """Narrow a multi-stage run to the stage that owns one known path."""

    stages = _responsible_stages(manifest)
    parts = path.parts
    if _is_law_audit_sidecar(path):
        return {"law_audit"}

    patch_dir = next((part for part in parts if part in patch_stage_by_dir), "")
    # 03b may update the 02b, 03, and 02a patch layers.  When a run includes
    # law_audit, those shared paths must be reverified by law_audit rather than
    # cleared by an unrelated child stage.
    if patch_dir in {
        "18_law_context_prepared",
        "21_explanationText_added",
        "23_correctChoiceText_fixed",
    } and "law_audit" in stages:
        return {"law_audit"}

    owner = patch_stage_by_dir.get(patch_dir)
    if owner and owner in stages:
        return {owner}
    return stages


def _is_law_audit_sidecar(path: Path) -> bool:
    parts = path.parts
    return len(parts) >= 5 and parts[2:4] == (
        "review",
        "law_revision_audit",
    )


def _compatible_work_types(
    failed: Mapping[str, Any], succeeded: Mapping[str, Any]
) -> bool:
    failed_type = str(failed.get("workType") or "")
    succeeded_type = str(succeeded.get("workType") or "")
    if failed_type == succeeded_type:
        return True

    # 問題詳細からの個別整備は ``maintenance``、年度整備の工程runは
    # ``maintenance_<stage>`` になる。同じ責任工程・write contract・
    # record scopeを後段で満たす限り、起動入口の違いだけで失敗差分を
    # 相互に解消不能にしない。
    maintenance_types = {failed_type, succeeded_type}
    return all(
        value == "maintenance" or value.startswith("maintenance_")
        for value in maintenance_types
    )


def _path_allowed_by_contract(manifest: Mapping[str, Any], path: Path) -> bool:
    key = path.as_posix()
    patch_dirs = {str(value) for value in manifest.get("allowedPatchDirs") or []}
    if set(path.parts) & patch_dirs:
        files = {str(value) for value in manifest.get("allowedPatchFiles") or []}
        return not files or key in files

    qualification = str(manifest.get("qualification") or "")
    write_areas = {str(value) for value in manifest.get("allowedWriteAreas") or []}
    write_roots = {
        (
            Path("prompt", "qualification_docs", qualification)
            if area == "qualification_docs"
            else Path("output", qualification, area)
        )
        for area in write_areas
    }
    if not any(path == root or path.is_relative_to(root) for root in write_roots):
        return False
    files = {str(value) for value in manifest.get("allowedWriteFiles") or []}
    return not files or key in files


def _path_group_id(path: Path, qualification: str) -> str | None:
    parts = path.parts
    if parts[:3] == ("output", qualification, "questions_json") and len(parts) >= 4:
        return parts[3]
    if parts[:3] == ("output", qualification, "law_evidence") and len(parts) >= 4:
        return parts[3]
    if (
        parts[:4] == ("output", qualification, "review", "law_revision_audit")
        and len(parts) >= 5
        and parts[4].endswith("_law_revision_audit.jsonl")
    ):
        return parts[4][: -len("_law_revision_audit.jsonl")]
    return None


def _requires_record_scope(path: Path, qualification: str) -> bool:
    parts = path.parts
    return path.suffix.lower() in {".json", ".jsonl"} and (
        parts[:3] == ("output", qualification, "questions_json")
        or parts[:4]
        == ("output", qualification, "review", "law_revision_audit")
    )


def _record_scope_groups(
    manifest: Mapping[str, Any], path: Path
) -> set[tuple[str, ...]] | None:
    scopes = manifest.get("targetRecordScopes")
    if not isinstance(scopes, Mapping):
        return None
    groups = scopes.get(path.as_posix())
    if not isinstance(groups, list) or not groups:
        return None
    normalized: set[tuple[str, ...]] = set()
    for group in groups:
        if not isinstance(group, list):
            return None
        aliases = tuple(sorted({str(alias) for alias in group if alias}))
        if not aliases:
            return None
        normalized.add(aliases)
    return normalized
