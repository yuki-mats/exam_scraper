from __future__ import annotations

import copy
import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from tools.question_review_console.review_store import atomic_write
from tools.question_review_console.workflow_catalog import (
    WorkflowCatalog,
    normalize_policy_version,
    policy_version_major,
)


SCHEMA_VERSION = "question-work-versions/v3"
READABLE_SCHEMA_VERSIONS = {
    "question-work-versions/v1",
    "question-work-versions/v2",
    SCHEMA_VERSION,
}
LEGACY_VERSION = "0.0"
def _now() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _safe_segment(value: str) -> str:
    if value in {"", ".", ".."} or any(
        not (character.isalnum() or character in "-._") for character in value
    ):
        raise ValueError(f"invalid work-version path segment: {value}")
    return value


def _question_key_hash(question: Mapping[str, Any]) -> str:
    review_key = str(question.get("reviewKey") or question.get("id") or "").strip()
    if not review_key:
        raise ValueError("work versionの保存にはreviewKeyが必要です。")
    return hashlib.sha256(review_key.encode("utf-8")).hexdigest()[:24]


def _identity_hash(identity: str) -> str:
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _version_record_snapshot(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): copy.deepcopy(value)
        for key, value in record.items()
        if key not in {"history", "targets"}
    }


def _merge_version_records(
    primary: Mapping[str, Any],
    alias: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge records written under two identities without losing target history."""

    primary_at = str(primary.get("recordedAt") or "")
    alias_at = str(alias.get("recordedAt") or "")
    current = alias if alias_at > primary_at else primary
    other = primary if current is alias else alias
    merged = copy.deepcopy(dict(current))

    current_snapshot = _version_record_snapshot(current)
    history_candidates = [
        *(
            copy.deepcopy(list(primary.get("history") or []))
            if isinstance(primary.get("history"), list)
            else []
        ),
        *(
            copy.deepcopy(list(alias.get("history") or []))
            if isinstance(alias.get("history"), list)
            else []
        ),
    ]
    other_snapshot = _version_record_snapshot(other)
    if other_snapshot and other_snapshot != current_snapshot:
        history_candidates.append(other_snapshot)
    deduplicated_history: dict[str, dict[str, Any]] = {}
    for value in history_candidates:
        if not isinstance(value, Mapping):
            continue
        normalized = copy.deepcopy(dict(value))
        key = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        deduplicated_history[key] = normalized
    merged["history"] = sorted(
        deduplicated_history.values(),
        key=lambda value: (
            str(value.get("recordedAt") or ""),
            str(value.get("runId") or ""),
        ),
    )

    primary_targets = (
        primary.get("targets")
        if isinstance(primary.get("targets"), Mapping)
        else {}
    )
    alias_targets = (
        alias.get("targets")
        if isinstance(alias.get("targets"), Mapping)
        else {}
    )
    target_ids = set(primary_targets) | set(alias_targets)
    if target_ids:
        merged_targets: dict[str, Any] = {}
        for target_id in sorted(target_ids):
            primary_target = primary_targets.get(target_id)
            alias_target = alias_targets.get(target_id)
            if isinstance(primary_target, Mapping) and isinstance(
                alias_target, Mapping
            ):
                merged_targets[target_id] = _merge_version_records(
                    primary_target,
                    alias_target,
                )
            elif isinstance(primary_target, Mapping):
                merged_targets[target_id] = copy.deepcopy(dict(primary_target))
            elif isinstance(alias_target, Mapping):
                merged_targets[target_id] = copy.deepcopy(dict(alias_target))
        merged["targets"] = merged_targets
    else:
        merged.pop("targets", None)
    return merged


def _merge_question_records(
    canonical: Mapping[str, Any],
    alias: Mapping[str, Any],
) -> dict[str, Any]:
    merged = copy.deepcopy(dict(canonical))
    for field in (
        "questionId",
        "originalQuestionId",
        "publicationQualificationId",
    ):
        if not merged.get(field) and alias.get(field):
            merged[field] = copy.deepcopy(alias[field])
    canonical_stages = (
        canonical.get("stages")
        if isinstance(canonical.get("stages"), Mapping)
        else {}
    )
    alias_stages = (
        alias.get("stages")
        if isinstance(alias.get("stages"), Mapping)
        else {}
    )
    stages: dict[str, Any] = {}
    for stage_id in sorted(set(canonical_stages) | set(alias_stages)):
        canonical_stage = canonical_stages.get(stage_id)
        alias_stage = alias_stages.get(stage_id)
        if isinstance(canonical_stage, Mapping) and isinstance(alias_stage, Mapping):
            stages[stage_id] = _merge_version_records(
                canonical_stage,
                alias_stage,
            )
        elif isinstance(canonical_stage, Mapping):
            stages[stage_id] = copy.deepcopy(dict(canonical_stage))
        elif isinstance(alias_stage, Mapping):
            stages[stage_id] = copy.deepcopy(dict(alias_stage))
    merged["stages"] = stages
    return merged


def _content_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "missing"


def _manual_policy_is_selected(
    question: Mapping[str, Any], policy: Mapping[str, Any]
) -> bool:
    """Return whether an opt-in stage already has a committed patch."""

    if policy.get("automatic", True):
        return True
    patch_dir = str(policy.get("patchDir") or "").strip()
    if not patch_dir:
        return False
    marker = f"/{patch_dir}/"
    failed_paths = {
        str(path) for path in question.get("failedRunChangedPaths") or []
    }
    paths = question.get("paths")
    patch_paths = paths.get("patches") if isinstance(paths, Mapping) else []
    return any(
        marker in str(path) and str(path) not in failed_paths
        for path in patch_paths or []
    )


def _catalog_repo_root(catalog_path: str) -> Path:
    path = Path(catalog_path).resolve()
    return path.parent.parent if path.parent.name == "config" else path.parent


def policy_fingerprint(
    repo_root: Path,
    catalog_path: str,
    policy: Mapping[str, Any],
    *,
    canonical_docs: Iterable[str],
    inputs: Iterable[str] = (),
) -> str:
    """Hash the exact policy inputs while keeping the human version explicit."""

    fallback_root = _catalog_repo_root(catalog_path)
    paths = list(dict.fromkeys([*canonical_docs, *inputs]))
    artifacts: list[dict[str, str]] = []
    for relative in paths:
        primary = repo_root / relative
        path = primary if primary.is_file() else fallback_root / relative
        artifacts.append({"path": relative, "sha256": _content_hash(path)})
    normalized_policy = {
        key: value
        for key, value in policy.items()
        if key not in {"canonicalDocs", "policyFingerprint", "documents", "inputs"}
    }
    payload = {
        "policy": normalized_policy,
        "artifacts": artifacts,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evaluation_policy(repo_root: Path) -> dict[str, Any]:
    loaded = WorkflowCatalog(repo_root).load()
    raw = loaded.get("evaluation")
    if not isinstance(raw, Mapping):
        raise ValueError("workflow catalogに[evaluation]がありません。")
    policy = dict(raw)
    documents = list(policy.pop("documents", []))
    inputs = list(policy.pop("inputs", []))
    policy["canonicalDocs"] = documents
    policy["inputs"] = inputs
    policy["policyFingerprint"] = policy_fingerprint(
        repo_root.resolve(),
        str(loaded["catalogPath"]),
        policy,
        canonical_docs=documents,
        inputs=inputs,
    )
    return policy


def version_state(
    recorded: Mapping[str, Any] | None,
    policy: Mapping[str, Any],
) -> tuple[str, str]:
    if not recorded:
        return "unrecorded", "この工程の作業バージョンが未記録です。"
    recorded_version = normalize_policy_version(
        recorded.get("version", LEGACY_VERSION), "recorded.version"
    )
    current_version = normalize_policy_version(
        policy.get("policyVersion"), "policy.policyVersion"
    )
    recorded_major = policy_version_major(recorded_version)
    current_major = policy_version_major(current_version)
    if recorded_major < current_major:
        return (
            "outdated",
            f"v{recorded_version}で作業済み、現行はv{current_version}です。",
        )
    if recorded_major > current_major:
        return (
            "future",
            f"記録v{recorded_version}が現行v{current_version}より新しい状態です。",
        )
    if recorded_version == current_version:
        return "current", f"現行v{current_version}で作業済みです。"
    return (
        "current",
        f"v{recorded_version}で作業済みです。現行v{current_version}は"
        "マイナー改訂のため洗い替え不要です。",
    )


class QuestionWorkVersionStore:
    """Stores operational policy history outside source, patches, and Firestore."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.root = self.repo_root / "output" / "question_review_console"
        self._cache: dict[Path, tuple[int, int, dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def path_for(self, qualification: str, list_group_id: str) -> Path:
        return (
            self.root
            / _safe_segment(qualification)
            / _safe_segment(list_group_id)
            / "work_versions.json"
        )

    def load_group(self, qualification: str, list_group_id: str) -> dict[str, Any]:
        return copy.deepcopy(self._load_group_payload(qualification, list_group_id))

    def _load_group_payload(
        self,
        qualification: str,
        list_group_id: str,
    ) -> dict[str, Any]:
        path = self.path_for(qualification, list_group_id)
        if not path.is_file():
            return self._empty_group(qualification, list_group_id)
        stat = path.stat()
        with self._lock:
            cached = self._cache.get(path)
            if cached and cached[:2] == (stat.st_size, stat.st_mtime_ns):
                return cached[2]
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"作業バージョンfileを読めません: {path}") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schemaVersion") not in READABLE_SCHEMA_VERSIONS
            or payload.get("qualification") != qualification
            or payload.get("listGroupId") != list_group_id
            or not isinstance(payload.get("questions"), dict)
        ):
            raise ValueError(f"作業バージョンfileの形式が不正です: {path}")
        with self._lock:
            self._cache[path] = (stat.st_size, stat.st_mtime_ns, payload)
        return payload

    def record_for(self, question: Mapping[str, Any]) -> dict[str, Any] | None:
        if not question.get("qualification") or not question.get("listGroupId"):
            return None
        payload = self._load_group_payload(
            str(question["qualification"]), str(question["listGroupId"])
        )
        record = payload["questions"].get(_question_key_hash(question))
        if not isinstance(record, Mapping):
            return None
        identity = str(question.get("reviewKey") or question.get("id") or "")
        if record.get("reviewKey") != identity:
            return None
        normalized = copy.deepcopy(dict(record))
        stages = normalized.get("stages")
        if isinstance(stages, dict):
            for stage in stages.values():
                if isinstance(stage, dict):
                    self._normalize_stage_versions(stage)
        return normalized

    def status_for(
        self,
        question: Mapping[str, Any],
        policies: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any]:
        record = self.record_for(question) or {}
        recorded_stages = record.get("stages")
        recorded_stages = recorded_stages if isinstance(recorded_stages, Mapping) else {}
        stages: list[dict[str, Any]] = []
        for raw_policy in policies:
            policy = dict(raw_policy)
            stage_id = str(policy.get("id") or "")
            if not stage_id or (
                stage_id != "evaluation" and policy.get("policyVersion") is None
            ):
                continue
            if not _manual_policy_is_selected(question, policy):
                continue
            recorded = recorded_stages.get(stage_id)
            recorded = dict(recorded) if isinstance(recorded, Mapping) else None
            update_targets = [
                dict(value)
                for value in policy.get("updateTargets") or []
                if isinstance(value, Mapping) and value.get("selectionId")
            ]
            selected_target_ids = {
                str(value)
                for value in policy.get("selectedUpdateTargetIds") or []
                if value
            }
            if selected_target_ids:
                update_targets = [
                    value
                    for value in update_targets
                    if str(value.get("selectionId") or "") in selected_target_ids
                ]
            target_statuses: list[dict[str, Any]] = []
            if update_targets:
                recorded_targets = (
                    recorded.get("targets")
                    if isinstance(recorded, Mapping)
                    and isinstance(recorded.get("targets"), Mapping)
                    else {}
                )
                base_record = (
                    recorded
                    if isinstance(recorded, Mapping) and recorded.get("version") is not None
                    else None
                )
                for target in update_targets:
                    target_id = str(target["selectionId"])
                    target_record = recorded_targets.get(target_id)
                    target_record = (
                        dict(target_record)
                        if isinstance(target_record, Mapping)
                        else base_record
                    )
                    target_status, target_detail = version_state(target_record, policy)
                    target_statuses.append(
                        {
                            "id": target_id,
                            "label": str(target.get("label") or target_id),
                            "fields": list(target.get("fields") or []),
                            "status": target_status,
                            "detail": target_detail,
                            "recordedVersion": (
                                normalize_policy_version(
                                    target_record.get("version", LEGACY_VERSION),
                                    "recorded.version",
                                )
                                if target_record
                                else None
                            ),
                            "recordedAt": (
                                target_record.get("recordedAt")
                                if target_record
                                else None
                            ),
                            "runId": target_record.get("runId") if target_record else None,
                        }
                    )
                if all(value["status"] == "current" for value in target_statuses):
                    status = "current"
                elif any(
                    value["status"] in {"outdated", "future"}
                    for value in target_statuses
                ):
                    status = "outdated"
                else:
                    status = "unrecorded"
                detail = (
                    f"更新項目 {sum(value['status'] == 'current' for value in target_statuses)}"
                    f"/{len(target_statuses)}件が現行です。"
                )
                recorded_values = [
                    value for value in target_statuses if value["recordedVersion"]
                ]
                display_recorded = (
                    recorded_values[0] if len(recorded_values) == 1 else None
                )
            else:
                status, detail = version_state(recorded, policy)
                display_recorded = None
            stages.append(
                {
                    "id": stage_id,
                    "code": str(policy.get("code") or stage_id),
                    "label": str(policy.get("label") or stage_id),
                    "currentVersion": normalize_policy_version(
                        policy.get("policyVersion"), "policy.policyVersion"
                    ),
                    "recordedVersion": (
                        display_recorded["recordedVersion"]
                        if display_recorded
                        else normalize_policy_version(
                            recorded.get("version", LEGACY_VERSION),
                            "recorded.version",
                        )
                        if recorded and recorded.get("version") is not None
                        else None
                    ),
                    "status": status,
                    "detail": detail,
                    "recordedAt": recorded.get("recordedAt") if recorded else None,
                    "runId": recorded.get("runId") if recorded else None,
                    "source": recorded.get("source") if recorded else None,
                    "policyFingerprintMatches": bool(
                        recorded
                        and recorded.get("policyFingerprint")
                        and recorded.get("policyFingerprint")
                        == policy.get("policyFingerprint")
                    ),
                    "targets": target_statuses,
                }
            )
        maintenance = [stage for stage in stages if stage["id"] != "evaluation"]
        noncurrent = [stage for stage in maintenance if stage["status"] != "current"]
        if not maintenance:
            overall = "unrecorded"
        elif not noncurrent:
            overall = "current"
        elif any(stage["status"] in {"outdated", "future"} for stage in noncurrent):
            overall = "outdated"
        else:
            overall = "unrecorded"
        return {
            "status": overall,
            "allCurrent": bool(maintenance) and not noncurrent,
            "currentCount": len(maintenance) - len(noncurrent),
            "applicableCount": len(maintenance),
            "outdatedStageIds": [
                stage["id"]
                for stage in maintenance
                if stage["status"] in {"outdated", "future"}
            ],
            "unrecordedStageIds": [
                stage["id"] for stage in maintenance if stage["status"] == "unrecorded"
            ],
            "stages": stages,
        }

    def record_stage(
        self,
        questions: Iterable[Mapping[str, Any]],
        policy: Mapping[str, Any],
        *,
        run_id: str | None,
        source: str,
        only_missing: bool = False,
        version: str | int | None = None,
        policy_fingerprint_override: str | None = None,
        target_ids: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        stage_id = str(policy.get("id") or "")
        if not stage_id or (
            stage_id != "evaluation" and policy.get("policyVersion") is None
        ):
            raise ValueError(f"作業バージョン対象外の工程です: {stage_id}")
        available_target_ids = {
            str(value.get("selectionId") or "")
            for value in policy.get("updateTargets") or []
            if isinstance(value, Mapping) and value.get("selectionId")
        }
        selected_target_ids = (
            {str(value) for value in target_ids if str(value)}
            if target_ids is not None
            else set()
        )
        if selected_target_ids and not selected_target_ids <= available_target_ids:
            raise ValueError(
                "作業バージョンの更新項目が不正です: "
                + ", ".join(sorted(selected_target_ids - available_target_ids))
            )
        partial_target_ids = (
            selected_target_ids
            if selected_target_ids and selected_target_ids != available_target_ids
            else set()
        )
        grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
        for question in questions:
            key = (str(question["qualification"]), str(question["listGroupId"]))
            grouped.setdefault(key, []).append(question)
        recorded_count = 0
        skipped_count = 0
        reconciled_count = 0
        paths: list[str] = []
        with self._lock:
            prepared: list[tuple[Path, dict[str, Any]]] = []
            for (qualification, list_group_id), items in sorted(grouped.items()):
                payload = self.load_group(qualification, list_group_id)
                self._normalize_payload_versions(payload)
                payload["schemaVersion"] = SCHEMA_VERSION
                changed = False
                for question in items:
                    key = _question_key_hash(question)
                    existing = payload["questions"].get(key)
                    identity = str(
                        question.get("reviewKey") or question.get("id") or ""
                    )
                    question_id = str(question.get("id") or "")
                    alias_key = _identity_hash(question_id) if question_id else ""
                    alias = (
                        payload["questions"].get(alias_key)
                        if alias_key and alias_key != key
                        else None
                    )
                    original_question_id = str(
                        question.get("originalQuestionId") or ""
                    )
                    publication_qualification_id = str(
                        question.get("publicationQualificationId")
                        or question.get("qualification")
                        or ""
                    )
                    alias_matches = bool(
                        isinstance(alias, Mapping)
                        and str(alias.get("reviewKey") or "") == question_id
                        and (
                            not original_question_id
                            or not alias.get("originalQuestionId")
                            or str(alias.get("originalQuestionId"))
                            == original_question_id
                        )
                        and (
                            not publication_qualification_id
                            or not alias.get("publicationQualificationId")
                            or str(alias.get("publicationQualificationId"))
                            == publication_qualification_id
                        )
                    )
                    if not isinstance(existing, dict) or existing.get("reviewKey") != identity:
                        existing = {
                            "reviewKey": identity,
                            "questionId": question_id,
                            "originalQuestionId": original_question_id,
                            "publicationQualificationId": publication_qualification_id,
                            "stages": {},
                        }
                    if alias_matches:
                        existing = _merge_question_records(existing, alias)
                        del payload["questions"][alias_key]
                        reconciled_count += 1
                        changed = True
                    existing.update(
                        reviewKey=identity,
                        questionId=question_id,
                        originalQuestionId=original_question_id,
                        publicationQualificationId=publication_qualification_id,
                    )
                    stages = existing.get("stages")
                    if not isinstance(stages, dict):
                        stages = {}
                        existing["stages"] = stages
                    previous = stages.get(stage_id)
                    previous_targets = (
                        previous.get("targets")
                        if isinstance(previous, Mapping)
                        and isinstance(previous.get("targets"), Mapping)
                        else {}
                    )
                    if only_missing and (
                        stage_id in stages
                        if not partial_target_ids
                        else all(
                            target_id in previous_targets
                            for target_id in partial_target_ids
                        )
                    ):
                        skipped_count += 1
                        continue

                    def version_record(
                        old: Mapping[str, Any] | None,
                    ) -> dict[str, Any]:
                        history = (
                            list(old.get("history") or [])
                            if isinstance(old, Mapping)
                            else []
                        )
                        if isinstance(old, Mapping) and old.get("version") is not None:
                            history.append(
                                {
                                    str(history_key): copy.deepcopy(history_value)
                                    for history_key, history_value in old.items()
                                    if history_key not in {"history", "targets"}
                                }
                            )
                        return {
                            "version": normalize_policy_version(
                                policy.get("policyVersion")
                                if version is None
                                else version,
                                f"{stage_id}.version",
                            ),
                            "policyFingerprint": str(
                                policy_fingerprint_override
                                if policy_fingerprint_override is not None
                                else policy.get("policyFingerprint") or ""
                            ),
                            "runId": run_id,
                            "source": source,
                            "recordedAt": _now(),
                            "history": history,
                        }
                    if partial_target_ids:
                        stage_record = (
                            copy.deepcopy(dict(previous))
                            if isinstance(previous, Mapping)
                            else {"targets": {}}
                        )
                        targets = stage_record.get("targets")
                        if not isinstance(targets, dict):
                            targets = {}
                            stage_record["targets"] = targets
                        for target_id in sorted(partial_target_ids):
                            old_target = targets.get(target_id)
                            targets[target_id] = version_record(
                                old_target if isinstance(old_target, Mapping) else None
                            )
                        stages[stage_id] = stage_record
                    else:
                        stages[stage_id] = version_record(
                            previous if isinstance(previous, Mapping) else None
                        )
                    payload["questions"][key] = existing
                    recorded_count += 1
                    changed = True
                if changed:
                    payload["updatedAt"] = _now()
                    path = self.path_for(qualification, list_group_id)
                    prepared.append((path, payload))
            for path, payload in prepared:
                atomic_write(
                    path,
                    json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n",
                )
                self._cache.pop(path, None)
                paths.append(str(path.relative_to(self.repo_root)))
        return {
            "stageId": stage_id,
            "version": normalize_policy_version(
                policy.get("policyVersion") if version is None else version,
                f"{stage_id}.version",
            ),
            "recordedCount": recorded_count,
            "skippedCount": skipped_count,
            "reconciledCount": reconciled_count,
            "paths": paths,
            "targetIds": sorted(partial_target_ids),
            "partial": bool(partial_target_ids),
        }

    def invalidate_stage_run(
        self,
        qualification: str,
        list_group_id: str,
        *,
        stage_id: str,
        run_id: str,
        question_ids: Iterable[str],
        reason: str,
        receipt_id: str,
        execute: bool = False,
    ) -> dict[str, Any]:
        """Invalidate one validated run without deleting its audit history."""

        if not stage_id or stage_id == "evaluation":
            raise ValueError(f"作業バージョン対象外の工程です: {stage_id}")
        target_ids = {str(value).strip() for value in question_ids if str(value).strip()}
        if not target_ids:
            raise ValueError("無効化対象のquestionIdがありません。")
        if not str(run_id).strip():
            raise ValueError("無効化対象のrunIdがありません。")
        if not str(reason).strip():
            raise ValueError("無効化理由がありません。")
        if not str(receipt_id).strip():
            raise ValueError("無効化receipt IDがありません。")

        with self._lock:
            payload = self.load_group(qualification, list_group_id)
            self._normalize_payload_versions(payload)
            matched: list[tuple[str, dict[str, Any], list[str] | None]] = []
            skipped_ids: list[str] = []
            for record in payload["questions"].values():
                if not isinstance(record, dict):
                    continue
                question_id = str(record.get("questionId") or "")
                if question_id not in target_ids:
                    continue
                stages = record.get("stages")
                current = stages.get(stage_id) if isinstance(stages, dict) else None
                if not isinstance(current, dict):
                    skipped_ids.append(question_id)
                    continue
                matching_target_ids = [
                    str(target_id)
                    for target_id, target in (current.get("targets") or {}).items()
                    if isinstance(target, Mapping) and target.get("runId") == run_id
                ]
                if current.get("runId") == run_id:
                    matched.append((question_id, record, None))
                elif matching_target_ids:
                    matched.append((question_id, record, matching_target_ids))
                else:
                    skipped_ids.append(question_id)

            if execute and matched:
                recorded_at = _now()
                for _, record, matching_target_ids in matched:
                    stages = record["stages"]
                    previous = stages[stage_id]
                    if matching_target_ids is not None:
                        targets = previous["targets"]
                        for target_id in matching_target_ids:
                            previous_target = targets[target_id]
                            history = list(previous_target.get("history") or [])
                            history.append(
                                {
                                    str(key): copy.deepcopy(value)
                                    for key, value in previous_target.items()
                                    if key != "history"
                                }
                            )
                            targets[target_id] = {
                                "version": LEGACY_VERSION,
                                "policyFingerprint": "invalidated",
                                "runId": receipt_id,
                                "source": "invalidated_run",
                                "recordedAt": recorded_at,
                                "invalidatedRunId": run_id,
                                "reason": str(reason).strip(),
                                "history": history,
                            }
                        continue
                    history = list(previous.get("history") or [])
                    history.append(
                        {
                            str(key): copy.deepcopy(value)
                            for key, value in previous.items()
                            if key != "history"
                        }
                    )
                    stages[stage_id] = {
                        "version": LEGACY_VERSION,
                        "policyFingerprint": "invalidated",
                        "runId": receipt_id,
                        "source": "invalidated_run",
                        "recordedAt": recorded_at,
                        "invalidatedRunId": run_id,
                        "reason": str(reason).strip(),
                        "history": history,
                    }
                payload["schemaVersion"] = SCHEMA_VERSION
                payload["updatedAt"] = recorded_at
                path = self.path_for(qualification, list_group_id)
                atomic_write(
                    path,
                    json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n",
                )
                self._cache.pop(path, None)

        return {
            "qualification": qualification,
            "listGroupId": list_group_id,
            "stageId": stage_id,
            "invalidatedRunId": run_id,
            "executed": execute,
            "targetCount": len(target_ids),
            "invalidatedCount": len(matched),
            "invalidatedQuestionIds": sorted(
                question_id for question_id, _, _ in matched
            ),
            "skippedQuestionIds": sorted(skipped_ids),
            "path": str(self.path_for(qualification, list_group_id).relative_to(self.repo_root)),
        }

    def migrate_all(self, *, execute: bool = False) -> dict[str, Any]:
        """Convert legacy integer versions to MAJOR.MINOR after full validation."""

        paths = sorted(self.root.glob("*/*/work_versions.json"))
        prepared: list[tuple[Path, dict[str, Any]]] = []
        stage_record_count = 0
        changed_paths: list[str] = []
        with self._lock:
            for path in paths:
                qualification = path.parent.parent.name
                list_group_id = path.parent.name
                original = self.load_group(qualification, list_group_id)
                payload = copy.deepcopy(original)
                stage_record_count += self._normalize_payload_versions(payload)
                payload["schemaVersion"] = SCHEMA_VERSION
                if payload != original:
                    payload["updatedAt"] = _now()
                    prepared.append((path, payload))
                    changed_paths.append(str(path.relative_to(self.repo_root)))
            if execute:
                for path, payload in prepared:
                    atomic_write(
                        path,
                        json.dumps(
                            payload, ensure_ascii=False, indent=2, sort_keys=True
                        )
                        + "\n",
                    )
                    self._cache.pop(path, None)
        return {
            "schemaVersion": SCHEMA_VERSION,
            "executed": execute,
            "fileCount": len(paths),
            "changedFileCount": len(prepared),
            "stageRecordCount": stage_record_count,
            "changedPaths": changed_paths,
        }

    @classmethod
    def _normalize_payload_versions(cls, payload: dict[str, Any]) -> int:
        questions = payload.get("questions")
        if not isinstance(questions, Mapping):
            raise ValueError("作業バージョンfileのquestions形式が不正です。")
        stage_record_count = 0
        for record in questions.values():
            if not isinstance(record, Mapping):
                raise ValueError("作業バージョンfileのquestion形式が不正です。")
            stages = record.get("stages")
            if not isinstance(stages, Mapping):
                raise ValueError("作業バージョンfileのstages形式が不正です。")
            for stage in stages.values():
                if not isinstance(stage, dict):
                    raise ValueError("作業バージョンfileのstage形式が不正です。")
                cls._normalize_stage_versions(stage)
                stage_record_count += 1
        return stage_record_count

    @staticmethod
    def _normalize_stage_versions(stage: dict[str, Any]) -> None:
        def normalize_record(record: dict[str, Any], field: str) -> None:
            if "version" not in record:
                raise ValueError("作業バージョン記録にversionがありません。")
            record["version"] = normalize_policy_version(
                record["version"], f"{field}.version"
            )
            history = record.get("history")
            if history is None:
                record["history"] = []
                return
            if not isinstance(history, list):
                raise ValueError("作業バージョン履歴の形式が不正です。")
            for entry in history:
                if not isinstance(entry, dict) or "version" not in entry:
                    raise ValueError("作業バージョン履歴の形式が不正です。")
                entry["version"] = normalize_policy_version(
                    entry["version"], f"{field}.history.version"
                )

        if "version" in stage:
            normalize_record(stage, "recorded")
        targets = stage.get("targets")
        if targets is not None:
            if not isinstance(targets, dict) or not targets:
                raise ValueError("作業バージョン更新項目の形式が不正です。")
            for target_id, target in targets.items():
                if not isinstance(target_id, str) or not isinstance(target, dict):
                    raise ValueError("作業バージョン更新項目の形式が不正です。")
                normalize_record(target, f"recorded.targets.{target_id}")
        if "version" not in stage and not targets:
            raise ValueError("作業バージョン記録にversion又はtargetsがありません。")

    @staticmethod
    def _empty_group(qualification: str, list_group_id: str) -> dict[str, Any]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "qualification": qualification,
            "listGroupId": list_group_id,
            "updatedAt": None,
            "questions": {},
        }
