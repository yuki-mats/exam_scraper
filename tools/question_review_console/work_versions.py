from __future__ import annotations

import copy
import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from tools.question_review_console.review_store import atomic_write
from tools.question_review_console.workflow_catalog import WorkflowCatalog


SCHEMA_VERSION = "question-work-versions/v1"
LEGACY_VERSION = 0
MAINTENANCE_STAGE_IDS = (
    "question_type",
    "question_intent",
    "correct_choice",
    "law_context",
    "explanation",
    "law_audit",
    "question_set",
)


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


def _content_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "missing"


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
    recorded_version = int(recorded.get("version") or 0)
    current_version = int(policy.get("policyVersion") or 0)
    if recorded_version < current_version:
        return (
            "outdated",
            f"v{recorded_version}で作業済み、現行はv{current_version}です。",
        )
    if recorded_version > current_version:
        return (
            "future",
            f"記録v{recorded_version}が現行v{current_version}より新しい状態です。",
        )
    return "current", f"現行v{current_version}で作業済みです。"


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
            or payload.get("schemaVersion") != SCHEMA_VERSION
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
                    stage.setdefault("history", [])
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
            if stage_id not in {*MAINTENANCE_STAGE_IDS, "evaluation"}:
                continue
            if stage_id == "law_audit" and question.get("isLawRelated") is not True:
                continue
            recorded = recorded_stages.get(stage_id)
            recorded = dict(recorded) if isinstance(recorded, Mapping) else None
            status, detail = version_state(recorded, policy)
            stages.append(
                {
                    "id": stage_id,
                    "code": str(policy.get("code") or stage_id),
                    "label": str(policy.get("label") or stage_id),
                    "currentVersion": int(policy.get("policyVersion") or 0),
                    "recordedVersion": (
                        int(recorded.get("version") or 0) if recorded else None
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
                }
            )
        maintenance = [
            stage for stage in stages if stage["id"] in MAINTENANCE_STAGE_IDS
        ]
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
        version: int | None = None,
        policy_fingerprint_override: str | None = None,
    ) -> dict[str, Any]:
        stage_id = str(policy.get("id") or "")
        if stage_id not in {*MAINTENANCE_STAGE_IDS, "evaluation"}:
            raise ValueError(f"作業バージョン対象外の工程です: {stage_id}")
        grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
        for question in questions:
            if stage_id == "law_audit" and question.get("isLawRelated") is not True:
                continue
            key = (str(question["qualification"]), str(question["listGroupId"]))
            grouped.setdefault(key, []).append(question)
        recorded_count = 0
        skipped_count = 0
        paths: list[str] = []
        with self._lock:
            prepared: list[tuple[Path, dict[str, Any]]] = []
            for (qualification, list_group_id), items in sorted(grouped.items()):
                payload = self.load_group(qualification, list_group_id)
                changed = False
                for question in items:
                    key = _question_key_hash(question)
                    existing = payload["questions"].get(key)
                    identity = str(
                        question.get("reviewKey") or question.get("id") or ""
                    )
                    if not isinstance(existing, dict) or existing.get("reviewKey") != identity:
                        existing = {
                            "reviewKey": identity,
                            "questionId": str(question.get("id") or ""),
                            "originalQuestionId": str(question.get("originalQuestionId") or ""),
                            "publicationQualificationId": str(
                                question.get("publicationQualificationId")
                                or question.get("qualification")
                                or ""
                            ),
                            "stages": {},
                        }
                    stages = existing.get("stages")
                    if not isinstance(stages, dict):
                        stages = {}
                        existing["stages"] = stages
                    if only_missing and stage_id in stages:
                        skipped_count += 1
                        continue
                    previous = stages.get(stage_id)
                    history = (
                        list(previous.get("history") or [])
                        if isinstance(previous, Mapping)
                        else []
                    )
                    if isinstance(previous, Mapping):
                        history.append(
                            {
                                str(key): copy.deepcopy(value)
                                for key, value in previous.items()
                                if key != "history"
                            }
                        )
                    stages[stage_id] = {
                        "version": int(
                            policy.get("policyVersion") if version is None else version
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
            "version": int(policy.get("policyVersion") if version is None else version),
            "recordedCount": recorded_count,
            "skippedCount": skipped_count,
            "paths": paths,
        }

    @staticmethod
    def _empty_group(qualification: str, list_group_id: str) -> dict[str, Any]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "qualification": qualification,
            "listGroupId": list_group_id,
            "updatedAt": None,
            "questions": {},
        }
