from __future__ import annotations

import copy
import hashlib
import json
import threading
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from tools.question_review_console.review_store import atomic_write
from tools.question_review_console.workflow_catalog import (
    WorkflowCatalog,
    normalize_policy_version,
    policy_version_major,
)


SCHEMA_VERSION = "question-work-versions/v4"
LEGACY_GROUP_SCHEMA_VERSIONS = {
    "question-work-versions/v1",
    "question-work-versions/v2",
    "question-work-versions/v3",
}
READABLE_SCHEMA_VERSIONS = {
    *LEGACY_GROUP_SCHEMA_VERSIONS,
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
    # Natural-language aliases only resolve a UI instruction to an existing
    # update target. They do not change the model prompt, writable fields, or
    # generated content policy, so changing an alias must not age every
    # question's work version.
    if isinstance(normalized_policy.get("updateTargets"), list):
        normalized_policy["updateTargets"] = [
            {
                key: value
                for key, value in target.items()
                if key != "instructionAliases"
            }
            if isinstance(target, Mapping)
            else target
            for target in normalized_policy["updateTargets"]
        ]
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
        self._path_locks: dict[Path, threading.RLock] = {}

    @contextmanager
    def _path_transaction(self, paths: Iterable[Path]):
        normalized_paths = tuple(
            sorted(
                {path.resolve() for path in paths},
                key=lambda path: path.as_posix(),
            )
        )
        with self._lock:
            locks = [
                self._path_locks.setdefault(path, threading.RLock())
                for path in normalized_paths
            ]
        with ExitStack() as stack:
            for lock in locks:
                stack.enter_context(lock)
            yield

    def legacy_group_path_for(
        self,
        qualification: str,
        list_group_id: str,
    ) -> Path:
        return (
            self.root
            / _safe_segment(qualification)
            / _safe_segment(list_group_id)
            / "work_versions.json"
        )

    def question_directory_for(
        self,
        qualification: str,
        list_group_id: str,
    ) -> Path:
        return (
            self.root
            / _safe_segment(qualification)
            / _safe_segment(list_group_id)
            / "work_versions"
        )

    def question_path_for(self, question: Mapping[str, Any]) -> Path:
        qualification = _safe_segment(str(question.get("qualification") or ""))
        list_group_id = _safe_segment(str(question.get("listGroupId") or ""))
        return (
            self.question_directory_for(qualification, list_group_id)
            / f"{_question_key_hash(question)}.json"
        )

    def transaction_paths_for_questions(
        self,
        questions: Iterable[Mapping[str, Any]],
    ) -> tuple[Path, ...]:
        paths: set[Path] = set()
        for question in questions:
            canonical_path = self.question_path_for(question)
            paths.add(canonical_path)
            question_id = str(question.get("id") or "")
            if question_id:
                alias_path = (
                    canonical_path.parent
                    / f"{_identity_hash(question_id)}.json"
                )
                if alias_path.is_file():
                    paths.add(alias_path)
            legacy_path = self.legacy_group_path_for(
                str(question.get("qualification") or ""),
                str(question.get("listGroupId") or ""),
            )
            if legacy_path.is_file():
                paths.add(legacy_path)
        return tuple(sorted(paths, key=lambda path: path.as_posix()))

    def load_group(self, qualification: str, list_group_id: str) -> dict[str, Any]:
        legacy_path = self.legacy_group_path_for(
            qualification,
            list_group_id,
        )
        question_paths = tuple(
            sorted(
                self.question_directory_for(
                    qualification,
                    list_group_id,
                ).glob("*.json"),
                key=lambda path: path.as_posix(),
            )
        )
        paths = (
            *((legacy_path,) if legacy_path.is_file() else ()),
            *question_paths,
        )
        with self._path_transaction(paths):
            payload = self._empty_group(qualification, list_group_id)
            if legacy_path.is_file():
                legacy = self._load_payload(
                    legacy_path,
                    qualification,
                    list_group_id,
                )
                payload["questions"].update(
                    copy.deepcopy(legacy["questions"])
                )
                payload["updatedAt"] = legacy.get("updatedAt")
            for path in question_paths:
                current = self._load_payload(
                    path,
                    qualification,
                    list_group_id,
                    require_single_question=True,
                )
                key, record = next(iter(current["questions"].items()))
                existing = payload["questions"].get(key)
                if isinstance(existing, Mapping):
                    payload["questions"][key] = _merge_question_records(
                        record,
                        existing,
                    )
                else:
                    payload["questions"][key] = copy.deepcopy(record)
                payload["updatedAt"] = max(
                    str(payload.get("updatedAt") or ""),
                    str(current.get("updatedAt") or ""),
                ) or None
            return payload

    def _load_payload(
        self,
        path: Path,
        qualification: str,
        list_group_id: str,
        *,
        require_single_question: bool = False,
    ) -> dict[str, Any]:
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
        if require_single_question:
            questions = payload["questions"]
            if (
                payload.get("schemaVersion") != SCHEMA_VERSION
                or len(questions) != 1
                or path.stem != next(iter(questions))
            ):
                raise ValueError(
                    f"一問作業バージョンfileの形式が不正です: {path}"
                )
        with self._lock:
            self._cache[path] = (stat.st_size, stat.st_mtime_ns, payload)
        return payload

    def record_for(self, question: Mapping[str, Any]) -> dict[str, Any] | None:
        if not question.get("qualification") or not question.get("listGroupId"):
            return None
        qualification = str(question["qualification"])
        list_group_id = str(question["listGroupId"])
        key = _question_key_hash(question)
        path = self.question_path_for(question)
        if path.is_file():
            with self._path_transaction((path,)):
                payload = self._load_payload(
                    path,
                    qualification,
                    list_group_id,
                    require_single_question=True,
                )
        else:
            legacy_path = self.legacy_group_path_for(
                qualification,
                list_group_id,
            )
            if not legacy_path.is_file():
                return None
            with self._path_transaction((legacy_path,)):
                payload = self._load_payload(
                    legacy_path,
                    qualification,
                    list_group_id,
                )
        record = payload["questions"].get(key)
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

    def _canonical_payload_for_question(
        self,
        question: Mapping[str, Any],
    ) -> tuple[Path, dict[str, Any], tuple[Path, ...], int]:
        qualification = str(question.get("qualification") or "")
        list_group_id = str(question.get("listGroupId") or "")
        canonical_path = self.question_path_for(question)
        key = _question_key_hash(question)
        identity = str(question.get("reviewKey") or question.get("id") or "")
        question_id = str(question.get("id") or "")
        original_question_id = str(question.get("originalQuestionId") or "")
        publication_qualification_id = str(
            question.get("publicationQualificationId")
            or qualification
            or ""
        )
        alias_key = _identity_hash(question_id) if question_id else ""
        alias_path = (
            canonical_path.parent / f"{alias_key}.json"
            if alias_key and alias_key != key
            else None
        )

        def alias_matches(record: Mapping[str, Any]) -> bool:
            return bool(
                str(record.get("reviewKey") or "") == question_id
                and (
                    not original_question_id
                    or not record.get("originalQuestionId")
                    or str(record.get("originalQuestionId"))
                    == original_question_id
                )
                and (
                    not publication_qualification_id
                    or not record.get("publicationQualificationId")
                    or str(record.get("publicationQualificationId"))
                    == publication_qualification_id
                )
            )

        existing: dict[str, Any] | None = None
        aliases: list[dict[str, Any]] = []
        obsolete_paths: list[Path] = []
        if canonical_path.is_file():
            payload = self._load_payload(
                canonical_path,
                qualification,
                list_group_id,
                require_single_question=True,
            )
            record = payload["questions"].get(key)
            if (
                not isinstance(record, dict)
                or str(record.get("reviewKey") or "") != identity
            ):
                raise ValueError(
                    f"一問作業バージョンのidentityが一致しません: {canonical_path}"
                )
            existing = copy.deepcopy(record)
        else:
            legacy_path = self.legacy_group_path_for(
                qualification,
                list_group_id,
            )
            if legacy_path.is_file():
                legacy = self._load_payload(
                    legacy_path,
                    qualification,
                    list_group_id,
                )
                record = legacy["questions"].get(key)
                if isinstance(record, Mapping):
                    if str(record.get("reviewKey") or "") != identity:
                        raise ValueError(
                            "旧作業バージョンのidentityが一致しません: "
                            f"{legacy_path}"
                        )
                    existing = copy.deepcopy(dict(record))
                alias = (
                    legacy["questions"].get(alias_key)
                    if alias_key and alias_key != key
                    else None
                )
                if isinstance(alias, Mapping) and alias_matches(alias):
                    aliases.append(copy.deepcopy(dict(alias)))

        if alias_path is not None and alias_path.is_file():
            alias_payload = self._load_payload(
                alias_path,
                qualification,
                list_group_id,
                require_single_question=True,
            )
            alias = next(iter(alias_payload["questions"].values()))
            if not isinstance(alias, Mapping) or not alias_matches(alias):
                raise ValueError(
                    f"旧identityの一問作業バージョンが一致しません: {alias_path}"
                )
            aliases.append(copy.deepcopy(dict(alias)))
            obsolete_paths.append(alias_path)

        if existing is None:
            existing = {
                "reviewKey": identity,
                "questionId": question_id,
                "originalQuestionId": original_question_id,
                "publicationQualificationId": publication_qualification_id,
                "stages": {},
            }
        for alias in aliases:
            existing = _merge_question_records(existing, alias)
        existing.update(
            reviewKey=identity,
            questionId=question_id,
            originalQuestionId=original_question_id,
            publicationQualificationId=publication_qualification_id,
        )
        payload = self._empty_group(qualification, list_group_id)
        payload["questions"][key] = existing
        self._normalize_payload_versions(payload)
        return (
            canonical_path,
            payload,
            tuple(obsolete_paths),
            len(aliases),
        )

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
        items_by_path: dict[Path, Mapping[str, Any]] = {}
        for question in questions:
            path = self.question_path_for(question)
            existing_item = items_by_path.get(path)
            if existing_item is not None and (
                str(existing_item.get("reviewKey") or existing_item.get("id") or "")
                != str(question.get("reviewKey") or question.get("id") or "")
            ):
                raise ValueError(f"作業バージョンpathが重複しています: {path}")
            items_by_path[path] = question
        recorded_count = 0
        skipped_count = 0
        reconciled_count = 0
        paths: list[str] = []
        transaction_paths = self.transaction_paths_for_questions(
            items_by_path.values()
        )
        with self._path_transaction(transaction_paths):
            prepared: list[
                tuple[Path, dict[str, Any], tuple[Path, ...]]
            ] = []
            for path, question in sorted(
                items_by_path.items(),
                key=lambda value: value[0].as_posix(),
            ):
                (
                    canonical_path,
                    payload,
                    obsolete_paths,
                    reconciled,
                ) = self._canonical_payload_for_question(question)
                reconciled_count += reconciled
                record = next(iter(payload["questions"].values()))
                stages = record.get("stages")
                if not isinstance(stages, dict):
                    stages = {}
                    record["stages"] = stages
                previous = stages.get(stage_id)
                previous_targets = (
                    previous.get("targets")
                    if isinstance(previous, Mapping)
                    and isinstance(previous.get("targets"), Mapping)
                    else {}
                )
                should_skip = only_missing and (
                    stage_id in stages
                    if not partial_target_ids
                    else all(
                        target_id in previous_targets
                        for target_id in partial_target_ids
                    )
                )
                if should_skip:
                    skipped_count += 1
                else:

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
                    recorded_count += 1
                if (
                    not should_skip
                    or not canonical_path.is_file()
                    or obsolete_paths
                ):
                    payload["updatedAt"] = _now()
                    prepared.append(
                        (canonical_path, payload, obsolete_paths)
                    )
            for path, payload, obsolete_paths in prepared:
                atomic_write(
                    path,
                    json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n",
                )
                with self._lock:
                    self._cache.pop(path, None)
                for obsolete_path in obsolete_paths:
                    obsolete_path.unlink(missing_ok=True)
                    with self._lock:
                        self._cache.pop(obsolete_path, None)
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

        group = self.load_group(qualification, list_group_id)
        self._normalize_payload_versions(group)
        matched: list[
            tuple[dict[str, Any], list[str] | None]
        ] = []
        skipped_ids = set(target_ids)
        for record in group["questions"].values():
            if not isinstance(record, dict):
                continue
            question_id = str(record.get("questionId") or "")
            if question_id not in target_ids:
                continue
            skipped_ids.discard(question_id)
            stages = record.get("stages")
            current = stages.get(stage_id) if isinstance(stages, dict) else None
            if not isinstance(current, dict):
                skipped_ids.add(question_id)
                continue
            matching_target_ids = [
                str(target_id)
                for target_id, target in (current.get("targets") or {}).items()
                if isinstance(target, Mapping) and target.get("runId") == run_id
            ]
            if current.get("runId") == run_id or matching_target_ids:
                matched.append(
                    (
                        {
                            **record,
                            "id": question_id,
                            "qualification": qualification,
                            "listGroupId": list_group_id,
                        },
                        None
                        if current.get("runId") == run_id
                        else matching_target_ids,
                    )
                )
            else:
                skipped_ids.add(question_id)

        written_paths: list[str] = []
        invalidated_question_ids: list[str] = []
        if execute and matched:
            transaction_paths = self.transaction_paths_for_questions(
                question for question, _target_ids in matched
            )
            with self._path_transaction(transaction_paths):
                recorded_at = _now()
                prepared: list[
                    tuple[Path, dict[str, Any], tuple[Path, ...]]
                ] = []
                for question, matching_target_ids in matched:
                    (
                        path,
                        payload,
                        obsolete_paths,
                        _reconciled,
                    ) = self._canonical_payload_for_question(question)
                    record = next(iter(payload["questions"].values()))
                    stages = record.get("stages")
                    previous = (
                        stages.get(stage_id)
                        if isinstance(stages, dict)
                        else None
                    )
                    if not isinstance(previous, dict):
                        skipped_ids.add(str(question.get("id") or ""))
                        continue
                    current_target_ids = [
                        str(target_id)
                        for target_id, target in (
                            previous.get("targets") or {}
                        ).items()
                        if isinstance(target, Mapping)
                        and target.get("runId") == run_id
                    ]
                    if previous.get("runId") == run_id:
                        matching_target_ids = None
                    elif current_target_ids:
                        matching_target_ids = current_target_ids
                    else:
                        skipped_ids.add(str(question.get("id") or ""))
                        continue
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
                    else:
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
                    payload["updatedAt"] = recorded_at
                    prepared.append((path, payload, obsolete_paths))
                    invalidated_question_ids.append(
                        str(question.get("id") or "")
                    )
                for path, payload, obsolete_paths in prepared:
                    atomic_write(
                        path,
                        json.dumps(
                            payload,
                            ensure_ascii=False,
                            indent=2,
                            sort_keys=True,
                        )
                        + "\n",
                    )
                    written_paths.append(
                        str(path.relative_to(self.repo_root))
                    )
                    with self._lock:
                        self._cache.pop(path, None)
                    for obsolete_path in obsolete_paths:
                        obsolete_path.unlink(missing_ok=True)
                        with self._lock:
                            self._cache.pop(obsolete_path, None)

        return {
            "qualification": qualification,
            "listGroupId": list_group_id,
            "stageId": stage_id,
            "invalidatedRunId": run_id,
            "executed": execute,
            "targetCount": len(target_ids),
            "invalidatedCount": (
                len(invalidated_question_ids) if execute else len(matched)
            ),
            "invalidatedQuestionIds": (
                sorted(invalidated_question_ids)
                if execute
                else sorted(
                    str(question.get("id") or "")
                    for question, _target_ids in matched
                )
            ),
            "skippedQuestionIds": sorted(skipped_ids),
            "paths": written_paths,
        }

    def migrate_all(self, *, execute: bool = False) -> dict[str, Any]:
        """Normalize and split every legacy group ledger into one file per question."""

        legacy_paths = tuple(
            sorted(
                self.root.glob("*/*/work_versions.json"),
                key=lambda path: path.as_posix(),
            )
        )
        existing_question_paths = tuple(
            sorted(
                self.root.glob("*/*/work_versions/*.json"),
                key=lambda path: path.as_posix(),
            )
        )
        source_paths = (*legacy_paths, *existing_question_paths)
        planned_records: dict[
            Path,
            tuple[str, str, dict[str, Any], str],
        ] = {}
        obsolete_paths: set[Path] = set()
        with self._path_transaction(source_paths):
            for path in source_paths:
                if path.name == "work_versions.json":
                    qualification = path.parent.parent.name
                    list_group_id = path.parent.name
                    require_single_question = False
                    obsolete_paths.add(path)
                else:
                    qualification = path.parent.parent.parent.name
                    list_group_id = path.parent.parent.name
                    require_single_question = True
                payload = self._load_payload(
                    path,
                    qualification,
                    list_group_id,
                    require_single_question=False,
                )
                self._normalize_payload_versions(payload)
                if require_single_question and len(payload["questions"]) != 1:
                    raise ValueError(
                        f"一問作業バージョンfileの件数が不正です: {path}"
                    )
                if (
                    require_single_question
                    and payload.get("schemaVersion") == SCHEMA_VERSION
                    and path.stem != next(iter(payload["questions"]))
                ):
                    raise ValueError(
                        f"一問作業バージョンfileのidentityが不正です: {path}"
                    )
                for record in payload["questions"].values():
                    if not isinstance(record, Mapping):
                        raise ValueError(
                            f"作業バージョンfileのquestion形式が不正です: {path}"
                        )
                    question = {
                        **record,
                        "id": str(record.get("questionId") or ""),
                        "qualification": qualification,
                        "listGroupId": list_group_id,
                    }
                    target_path = self.question_path_for(question)
                    existing = planned_records.get(target_path)
                    merged = (
                        _merge_question_records(record, existing[2])
                        if existing is not None
                        else copy.deepcopy(dict(record))
                    )
                    updated_at = max(
                        str(payload.get("updatedAt") or ""),
                        existing[3] if existing is not None else "",
                    )
                    planned_records[target_path] = (
                        qualification,
                        list_group_id,
                        merged,
                        updated_at,
                    )
                    if require_single_question and path != target_path:
                        obsolete_paths.add(path)

            records_by_question_id: dict[
                tuple[str, str, str],
                list[Path],
            ] = {}
            for path, (
                qualification,
                list_group_id,
                record,
                _updated_at,
            ) in planned_records.items():
                question_id = str(record.get("questionId") or "")
                if question_id:
                    records_by_question_id.setdefault(
                        (qualification, list_group_id, question_id),
                        [],
                    ).append(path)
            for (
                qualification,
                list_group_id,
                question_id,
            ), paths_for_question in records_by_question_id.items():
                if len(paths_for_question) < 2:
                    continue
                canonical_paths = [
                    path
                    for path in paths_for_question
                    if str(
                        planned_records[path][2].get("reviewKey") or ""
                    )
                    != question_id
                ]
                if len(canonical_paths) != 1:
                    raise ValueError(
                        "同じquestionIdに複数のcanonical reviewKeyがあります: "
                        f"{qualification}/{list_group_id}/{question_id}"
                    )
                canonical_path = canonical_paths[0]
                (
                    _qualification,
                    _list_group_id,
                    canonical_record,
                    canonical_updated_at,
                ) = planned_records[canonical_path]
                for alias_path in paths_for_question:
                    if alias_path == canonical_path:
                        continue
                    alias_record = planned_records[alias_path][2]
                    for field in (
                        "originalQuestionId",
                        "publicationQualificationId",
                    ):
                        canonical_value = str(
                            canonical_record.get(field) or ""
                        )
                        alias_value = str(alias_record.get(field) or "")
                        if (
                            canonical_value
                            and alias_value
                            and canonical_value != alias_value
                        ):
                            raise ValueError(
                                "旧identityの作業バージョンが別問題を示しています: "
                                f"{qualification}/{list_group_id}/{question_id}"
                            )
                    canonical_record = _merge_question_records(
                        canonical_record,
                        alias_record,
                    )
                    canonical_updated_at = max(
                        canonical_updated_at,
                        planned_records[alias_path][3],
                    )
                    del planned_records[alias_path]
                    if alias_path.is_file():
                        obsolete_paths.add(alias_path)
                planned_records[canonical_path] = (
                    qualification,
                    list_group_id,
                    canonical_record,
                    canonical_updated_at,
                )

            prepared: list[tuple[Path, str]] = []
            stage_record_count = 0
            for path, (
                qualification,
                list_group_id,
                record,
                updated_at,
            ) in sorted(
                planned_records.items(),
                key=lambda value: value[0].as_posix(),
            ):
                payload = self._empty_group(qualification, list_group_id)
                payload["questions"][_question_key_hash(record)] = record
                stage_record_count += self._normalize_payload_versions(payload)
                payload["updatedAt"] = updated_at or _now()
                content = (
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                )
                current = (
                    path.read_text(encoding="utf-8")
                    if path.is_file()
                    else None
                )
                if current != content:
                    prepared.append((path, content))

            obsolete_paths.difference_update(planned_records)
            changed_paths = [
                str(path.relative_to(self.repo_root))
                for path, _content in prepared
            ]
            removed_paths = [
                str(path.relative_to(self.repo_root))
                for path in sorted(
                    obsolete_paths,
                    key=lambda value: value.as_posix(),
                )
            ]
            if execute and (prepared or obsolete_paths):
                touched_paths = {
                    *(path for path, _content in prepared),
                    *obsolete_paths,
                }
                originals = {
                    path: (
                        path.read_text(encoding="utf-8")
                        if path.is_file()
                        else None
                    )
                    for path in touched_paths
                }
                try:
                    for path, content in prepared:
                        atomic_write(path, content)
                        with self._lock:
                            self._cache.pop(path, None)
                    for path in planned_records:
                        self._load_payload(
                            path,
                            planned_records[path][0],
                            planned_records[path][1],
                            require_single_question=True,
                        )
                    for path in obsolete_paths:
                        path.unlink(missing_ok=True)
                        with self._lock:
                            self._cache.pop(path, None)
                except Exception:
                    for path, content in originals.items():
                        if content is None:
                            path.unlink(missing_ok=True)
                        else:
                            atomic_write(path, content)
                        with self._lock:
                            self._cache.pop(path, None)
                    raise
        return {
            "schemaVersion": SCHEMA_VERSION,
            "executed": execute,
            "sourceFileCount": len(source_paths),
            "legacyGroupFileCount": len(legacy_paths),
            "existingQuestionFileCount": len(existing_question_paths),
            "questionCount": len(planned_records),
            "changedFileCount": len(prepared) + len(obsolete_paths),
            "writtenFileCount": len(prepared),
            "removedFileCount": len(obsolete_paths),
            "stageRecordCount": stage_record_count,
            "changedPaths": [*changed_paths, *removed_paths],
            "writtenPaths": changed_paths,
            "removedPaths": removed_paths,
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
