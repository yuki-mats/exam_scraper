from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from scripts.common.question_identity import SourceIdentityBinding
from tools.question_review_console.failed_delta import (
    _responsible_stages_for_path,
    resolvable_failed_delta_paths,
    unresolved_failed_delta_paths,
)
from tools.question_review_console.inventory import QuestionInventory
from tools.question_review_console.jobs import JobManager
from tools.question_review_console.qualification_runs import (
    QualificationRunCoordinator,
    QualificationRunError,
)
from tools.question_review_console.review_store import atomic_write
from tools.question_review_console.run_target_identity import target_identity_aliases
from tools.question_review_console.workflow_catalog import WorkflowCatalog


LAW_AUDIT_SCHEMA = "law-revision-audit/v2"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _run_id() -> str:
    return "reconcile-" + datetime.now().strftime("%Y%m%dT%H%M%S%f")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualificationRunError(f"JSONを読み込めません: {path}") from exc
    if not isinstance(value, dict):
        raise QualificationRunError(f"JSON objectではありません: {path}")
    return value


def _failed_manifests(
    repo_root: Path,
    qualification: str,
    list_group_id: str,
    unresolved: tuple[str, ...],
) -> list[tuple[Path, dict[str, Any]]]:
    root = (
        repo_root
        / "output/question_review_console/workflow_runs"
        / qualification
    )
    unresolved_set = set(unresolved)
    manifests: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(root.glob("*/manifest.json")):
        manifest = _json(path)
        result = manifest.get("result")
        changed = (
            {str(value) for value in result.get("changedFiles") or []}
            if isinstance(result, Mapping)
            else set()
        )
        if (
            manifest.get("status") == "failed"
            and manifest.get("qualification") == qualification
            and list_group_id
            in {str(value) for value in manifest.get("targetGroupIds") or []}
            and changed & unresolved_set
        ):
            manifests.append((path, manifest))
    if not manifests:
        raise QualificationRunError("未確定差分の失敗runを確認できません。")
    return manifests


def _verified_baseline(
    repo_root: Path,
    manifests: list[tuple[Path, dict[str, Any]]],
    unresolved: tuple[str, ...],
) -> tuple[str, dict[str, Any], str]:
    repo_root = repo_root.resolve()
    unresolved_set = set(unresolved)
    for manifest_path, manifest in manifests:
        raw_path = str(manifest.get("baselinePath") or "")
        baseline_path = (
            (repo_root / raw_path).resolve()
            if raw_path
            else (manifest_path.parent / "baseline.json").resolve()
        )
        if not baseline_path.is_relative_to(repo_root):
            continue
        if not baseline_path.is_file() or baseline_path.is_symlink():
            continue
        raw = baseline_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != str(manifest.get("baselineHash") or ""):
            continue
        try:
            baseline = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        snapshots = (
            baseline.get("recordSnapshots")
            if isinstance(baseline, Mapping)
            else None
        )
        if isinstance(snapshots, Mapping) and unresolved_set.issubset(snapshots):
            return (
                str(manifest.get("runId") or manifest_path.parent.name),
                dict(baseline),
                digest,
            )
    raise QualificationRunError(
        "未確定差分全体を含む検証済みbaselineを確認できません。"
    )


def _target_state(
    repo_root: Path,
    qualification: str,
    list_group_id: str,
) -> tuple[list[dict[str, Any]], list[list[str]], list[dict[str, Any]]]:
    questions = QuestionInventory(repo_root).group(
        qualification,
        list_group_id,
    )["questions"]
    alias_groups: list[list[str]] = []
    bindings: list[dict[str, Any]] = []
    for question in questions:
        aliases = sorted(target_identity_aliases(question))
        binding = SourceIdentityBinding.from_mapping(question)
        if not aliases or not binding.is_complete():
            raise QualificationRunError(
                f"問題のsource ID bindingが不完全です: {question.get('id')}"
            )
        alias_groups.append(aliases)
        bindings.append(
            {
                "uiQuestionId": str(question["id"]),
                **binding.as_mapping(),
                "aliases": aliases,
            }
        )
    return questions, alias_groups, bindings


def _record_scopes(
    unresolved: tuple[str, ...],
    questions: list[dict[str, Any]],
    alias_groups: list[list[str]],
) -> dict[str, list[list[str]]]:
    scopes: dict[str, list[list[str]]] = {}
    for relative in unresolved:
        if relative.endswith("_law_revision_audit.jsonl"):
            scopes[relative] = alias_groups
            continue
        matches = [
            aliases
            for question, aliases in zip(questions, alias_groups, strict=True)
            if relative in set(question.get("paths", {}).get("patches") or [])
        ]
        if not matches:
            raise QualificationRunError(
                f"未確定patchの対象問題を確認できません: {relative}"
            )
        scopes[relative] = matches
    return scopes


def _migrated_sidecars(
    repo_root: Path,
    paths: tuple[str, ...],
    bindings: list[dict[str, Any]],
) -> tuple[dict[str, str], list[str]]:
    candidates: dict[str, str] = {}
    migrated_ids: list[str] = []
    for relative in paths:
        if not relative.endswith("_law_revision_audit.jsonl"):
            continue
        path = repo_root / relative
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            1,
        ):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise QualificationRunError(
                    f"監査sidecarの{line_number}行目が不正です: {relative}"
                ) from exc
            if not isinstance(row, dict):
                raise QualificationRunError(
                    f"監査sidecarの{line_number}行目がobjectではありません: {relative}"
                )
            actual = SourceIdentityBinding.from_mapping(row)
            if row.get("schemaVersion") != LAW_AUDIT_SCHEMA or not actual.is_complete():
                aliases = {
                    str(value)
                    for value in (
                        row.get("reviewQuestionId"),
                        row.get("sourceQuestionKey"),
                        row.get("sourceRecordRef"),
                    )
                    if value
                }
                matches = [
                    binding
                    for binding in bindings
                    if aliases & set(binding["aliases"])
                ]
                if len(matches) != 1:
                    raise QualificationRunError(
                        "旧監査sidecarの対象問題を一意に確認できません: "
                        f"{relative}:{line_number}"
                    )
                binding = matches[0]
                row.update(
                    {
                        "schemaVersion": LAW_AUDIT_SCHEMA,
                        "sourceQuestionKey": binding["sourceQuestionKey"],
                        "sourceRecordRef": binding["sourceRecordRef"],
                    }
                )
                migrated_ids.append(str(binding["uiQuestionId"]))
            rows.append(row)
        candidates[relative] = "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in rows
        )
    return candidates, sorted(set(migrated_ids))


def _build_contract(
    repo_root: Path,
    *,
    qualification: str,
    list_group_id: str,
    unresolved: tuple[str, ...],
    failed: list[tuple[Path, dict[str, Any]]],
    alias_groups: list[list[str]],
    bindings: list[dict[str, Any]],
    scopes: dict[str, list[list[str]]],
    source_files: list[str],
) -> dict[str, Any]:
    patch_files = [path for path in unresolved if "/questions_json/" in path]
    write_files = [path for path in unresolved if path not in patch_files]
    stage_ids = sorted(
        {
            str(stage)
            for _path, manifest in failed
            for stage in manifest.get("stageIds") or [manifest.get("stageId")]
            if stage
        }
    )
    patch_stage_by_dir = {
        str(stage["patchDir"]): str(stage["id"])
        for stage in WorkflowCatalog(repo_root).load()["stages"]
        if stage.get("patchDir")
    }
    verified_stages_by_path: dict[str, list[str]] = {}
    for relative in unresolved:
        path = Path(relative)
        stages = {
            stage
            for _manifest_path, manifest in failed
            if relative
            in {
                str(value)
                for value in (
                    manifest.get("result", {}).get("changedFiles")
                    if isinstance(manifest.get("result"), Mapping)
                    else []
                )
            }
            for stage in _responsible_stages_for_path(
                manifest,
                path,
                patch_stage_by_dir=patch_stage_by_dir,
            )
        }
        if not stages:
            raise QualificationRunError(
                f"未確定差分の責任工程を確認できません: {relative}"
            )
        verified_stages_by_path[relative] = sorted(stages)
    return {
        "qualification": qualification,
        "workType": "maintenance",
        "stageIds": stage_ids,
        "targetGroupIds": [list_group_id],
        "scopeListGroupId": list_group_id,
        "scopeListGroupIds": [list_group_id],
        "sourceFiles": source_files,
        "allowedPatchDirs": sorted({Path(path).parent.name for path in patch_files}),
        "allowedWriteAreas": ["review"] if write_files else [],
        "allowedPatchFiles": patch_files,
        "allowedWriteFiles": write_files,
        "targetRecordAliasGroups": alias_groups,
        "targetRecordAliases": sorted(
            {alias for group in alias_groups for alias in group}
        ),
        "targetRecordBindings": bindings,
        "targetRecordScopes": scopes,
        "verifiedStageIdsByPath": verified_stages_by_path,
        "legacyFailedDeltaReconciliation": True,
    }


def _remaining_after_receipt(
    repo_root: Path,
    qualification: str,
    list_group_id: str,
    receipt: Mapping[str, Any],
) -> tuple[str, ...]:
    """Evaluate the complete failed-delta ledger with a proposed receipt."""

    source_root = (
        repo_root
        / "output/question_review_console/workflow_runs"
        / qualification
    )
    with tempfile.TemporaryDirectory(
        prefix="failed-delta-receipt-preview-"
    ) as directory:
        preview_root = Path(directory)
        for source in source_root.glob("*/manifest.json"):
            destination = (
                preview_root
                / source.relative_to(repo_root)
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        candidate = (
            preview_root
            / "output/question_review_console/workflow_runs"
            / qualification
            / "proposed-reconciliation"
            / "manifest.json"
        )
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text(
            json.dumps(dict(receipt), ensure_ascii=False),
            encoding="utf-8",
        )
        return unresolved_failed_delta_paths(
            preview_root,
            qualification,
            list_group_id,
        )


def reconcile_failed_deltas(
    repo_root: Path,
    *,
    qualification: str,
    list_group_id: str,
    execute: bool,
) -> dict[str, Any]:
    """Verify and close old failed-run deltas without rewriting run history."""

    repo_root = repo_root.resolve()
    unresolved = unresolved_failed_delta_paths(
        repo_root,
        qualification,
        list_group_id,
    )
    if not unresolved:
        return {
            "status": "unchanged",
            "qualification": qualification,
            "listGroupId": list_group_id,
            "unresolvedPathCount": 0,
        }
    for relative in unresolved:
        path = repo_root / relative
        if not path.is_file() or path.is_symlink():
            raise QualificationRunError(
                f"未確定差分が通常fileではありません: {relative}"
            )

    failed = _failed_manifests(
        repo_root,
        qualification,
        list_group_id,
        unresolved,
    )
    baseline_run_id, baseline, baseline_hash = _verified_baseline(
        repo_root,
        failed,
        unresolved,
    )
    questions, alias_groups, bindings = _target_state(
        repo_root,
        qualification,
        list_group_id,
    )
    scopes = _record_scopes(unresolved, questions, alias_groups)
    source_files = sorted(
        {
            str(question.get("paths", {}).get("source"))
            for question in questions
            if question.get("paths", {}).get("source")
        }
    )
    contract = _build_contract(
        repo_root,
        qualification=qualification,
        list_group_id=list_group_id,
        unresolved=unresolved,
        failed=failed,
        alias_groups=alias_groups,
        bindings=bindings,
        scopes=scopes,
        source_files=source_files,
    )
    sidecar_candidates, migrated_ids = _migrated_sidecars(
        repo_root,
        unresolved,
        bindings,
    )
    before_hashes = {
        relative: _sha256(repo_root / relative)
        for relative in unresolved
    }
    coordinator = QualificationRunCoordinator(
        repo_root,
        object(),
        object(),
        JobManager(),
        "failed-delta-reconciliation",
    )
    coordinator._check_source_immutability(lambda _line: None, source_files=source_files)
    with tempfile.TemporaryDirectory(prefix="failed-delta-reconciliation-") as directory:
        validation_root = Path(directory)
        for relative in unresolved:
            destination = validation_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repo_root / relative, destination)
        for relative, content in sidecar_candidates.items():
            (validation_root / relative).write_text(content, encoding="utf-8")
        coordinator._validate_record_scope(
            qualification,
            baseline_run_id,
            contract,
            {Path(relative) for relative in unresolved},
            validation_root=validation_root,
            baseline_payload=baseline,
        )

    resolvable = resolvable_failed_delta_paths(
        repo_root,
        qualification,
        contract,
        list_group_id,
    )
    if set(resolvable) != set(unresolved):
        missing = sorted(set(unresolved) - set(resolvable))
        raise QualificationRunError(
            "解消receiptで未確定差分全体を覆えません: " + ", ".join(missing)
        )
    proposed_receipt = {
        "qualification": qualification,
        "kind": "system",
        "status": "succeeded",
        **contract,
        "result": {
            "status": "succeeded",
            "changedFiles": [],
            "resolvedFailedDeltaPaths": list(unresolved),
        },
    }
    preview_remaining = _remaining_after_receipt(
        repo_root,
        qualification,
        list_group_id,
        proposed_receipt,
    )
    if preview_remaining:
        raise QualificationRunError(
            "解消receiptの読み戻しpreviewで未確定差分が残ります: "
            + ", ".join(preview_remaining)
        )
    result: dict[str, Any] = {
        "status": "ready",
        "qualification": qualification,
        "listGroupId": list_group_id,
        "unresolvedPathCount": len(unresolved),
        "verifiedQuestionCount": len(questions),
        "migratedSidecarQuestionIds": migrated_ids,
        "baselineRunId": baseline_run_id,
        "failedRunIds": sorted(
            {
                str(manifest.get("runId") or path.parent.name)
                for path, manifest in failed
            }
        ),
        "verifiedFileHashes": before_hashes,
    }
    if not execute:
        return result

    if before_hashes != {
        relative: _sha256(repo_root / relative)
        for relative in unresolved
    }:
        raise QualificationRunError("dry-run後に未確定差分が変化しました。再実行してください。")

    originals = {
        relative: (repo_root / relative).read_text(encoding="utf-8")
        for relative in sidecar_candidates
    }
    run_id = _run_id()
    run_dir = (
        repo_root
        / "output/question_review_console/workflow_runs"
        / qualification
        / run_id
    )
    manifest_path = run_dir / "manifest.json"
    changed_files = [
        relative
        for relative, content in sidecar_candidates.items()
        if content != originals[relative]
    ]
    try:
        for relative in changed_files:
            atomic_write(repo_root / relative, sidecar_candidates[relative])
        completed_at = _now()
        post_hashes = {
            relative: _sha256(repo_root / relative)
            for relative in unresolved
        }
        receipt = {
            "schemaVersion": "failed-delta-reconciliation/v1",
            "runId": run_id,
            "kind": "system",
            "status": "succeeded",
            "receiptValidated": True,
            "createdAt": completed_at,
            "startedAt": completed_at,
            "finishedAt": completed_at,
            "updatedAt": completed_at,
            **contract,
            "targetCount": len(questions),
            "sourceFailedRunIds": result["failedRunIds"],
            "baselineRunId": baseline_run_id,
            "baselineHash": baseline_hash,
            "sourceImmutabilityVerified": True,
            "recordScopeVerified": True,
            "verifiedFileHashes": post_hashes,
            "result": {
                "status": "succeeded",
                "summary": (
                    f"旧runの未確定差分{len(unresolved)}fileを"
                    f"{len(questions)}問のrecord scopeで検証しました。"
                ),
                "changedFiles": changed_files,
                "resolvedFailedDeltaPaths": list(unresolved),
                "migratedSidecarQuestionIds": migrated_ids,
            },
        }
        atomic_write(
            manifest_path,
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        remaining = unresolved_failed_delta_paths(
            repo_root,
            qualification,
            list_group_id,
        )
        if remaining:
            raise QualificationRunError(
                "解消receiptの読み戻し後も未確定差分が残っています: "
                + ", ".join(remaining)
            )
    except Exception:
        manifest_path.unlink(missing_ok=True)
        for relative, content in originals.items():
            atomic_write(repo_root / relative, content)
        if run_dir.is_dir() and not any(run_dir.iterdir()):
            run_dir.rmdir()
        raise

    result.update(
        {
            "status": "succeeded",
            "receiptPath": manifest_path.relative_to(repo_root).as_posix(),
            "remainingUnresolvedPathCount": 0,
            "verifiedFileHashes": post_hashes,
        }
    )
    return result
