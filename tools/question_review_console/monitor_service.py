from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


MAX_ARTIFACT_BYTES = 1024 * 1024
MAX_ARTIFACT_TOTAL_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_FILES = 64
MAX_ARTIFACT_DECLARATIONS = 256
MAX_SNAPSHOT_CHILDREN = 128
MAX_MANIFEST_FALLBACK_BYTES = 8 * 1024 * 1024
MAX_LIST_SUMMARY_BYTES = 8 * 1024 * 1024
MAX_EVENT_LIMIT = 500
MAX_WAIT_MS = 30_000
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,299}$")
_ALLOWED_ARTIFACT_SUFFIXES = frozenset(
    {".json", ".jsonl", ".md", ".txt", ".yaml", ".yml", ".toml"}
)
_TEXT_EVENT_TYPES = frozenset({"agentMessage", "reasoningSummary"})
_EVENT_TYPES = frozenset(
    {
        *_TEXT_EVENT_TYPES,
        "reasoningSummaryPart",
        "plan",
        "toolState",
        "turnState",
        "threadState",
        "tokenUsage",
        "error",
        "observationGap",
    }
)
_CORRELATION_FIELDS = (
    "qualification",
    "runId",
    "parentRunId",
    "childRunId",
    "questionId",
    "workItemKey",
    "threadId",
    "turnId",
    "itemId",
    "stageId",
    "workType",
    "phase",
    "listGroupId",
    "sessionId",
)
_CORRELATION_LIST_FIELDS = ("questionIds", "workItemKeys", "listGroupIds")
_TOKEN_FIELDS = (
    "inputTokens",
    "cachedInputTokens",
    "cacheWriteInputTokens",
    "outputTokens",
    "reasoningOutputTokens",
    "totalTokens",
)
_RUN_FIELDS = (
    "runId",
    "parentRunId",
    "qualification",
    "status",
    "workType",
    "kind",
    "stageCode",
    "stageLabel",
    "listGroupId",
    "listGroupIds",
    "targetGroupIds",
    "targetCount",
    "workItemCount",
    "createdAt",
    "startedAt",
    "updatedAt",
    "heartbeatAt",
    "finishedAt",
    "receiptValidated",
    "executionPhase",
    "currentPhaseId",
)
_BATCH_ID_FIELDS = (
    "batchId",
    "batchKey",
    "batchIndex",
    "batchNumber",
    "batchSequence",
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?"
    r"-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_FILE_URL = re.compile(r"(?i)\bfile:/+(?:[^\s\"'<>\[\]{}()]+)")
_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?i)(?<![\w])(?:[A-Z]:\\|\\\\)[^\s\"'<>\[\]{}()]+"
)
_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_./])/(?!/)[^\s\"'<>\[\]{}()]+"
)
_SECRET_TOKEN = re.compile(
    r"(?i)\b(?:Bearer\s+\S+|sk-[A-Za-z0-9_-]{8,}|"
    r"gh[pousr]_[A-Za-z0-9_]{8,}|AKIA[A-Z0-9]{12,}|"
    r"xox[baprs]-[A-Za-z0-9-]{8,}|glpat-[A-Za-z0-9_-]{8,}|"
    r"AIza[A-Za-z0-9_-]{20,})\b"
)
_URL_CREDENTIAL = re.compile(
    r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@]*:[^/\s@]+@"
)
_SECRET_VALUE = re.compile(
    r"(?i)[\"']?("
    r"[A-Za-z0-9_.-]{0,64}(?:"
    r"password|passphrase|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"id[_-]?token|auth(?:orization)?|cookie|session[_-]?(?:id|token)|"
    r"client[_-]?secret|private[_-]?key|secret|token"
    r")[A-Za-z0-9_.-]{0,64}"
    r")[\"']?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_JWT = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"(?![A-Za-z0-9_-])"
)


class MonitorReadModel:
    """Strict, read-only projection over persisted workflow run artifacts."""

    def __init__(
        self,
        repo_root: Path,
        run_store: Any,
        event_hub: Any | None = None,
    ):
        self.repo_root = repo_root.resolve()
        self.run_store = run_store
        self.event_hub = event_hub

    def runs(self, qualification: str, *, limit: int = 100) -> dict[str, Any]:
        qualification = self._safe_id(qualification, "qualification")
        limit = max(1, min(int(limit), 500))
        runs = self._existing_dashboard_index(qualification)
        if runs is None:
            # Compatibility fallback for stores without the derived index.
            runs = self.run_store.dashboard_runs(qualification, limit=limit)
        return {
            "schemaVersion": "monitor-run-list/v1",
            "qualification": qualification,
            "runs": [
                self._run_summary(run)
                for run in runs[:limit]
                if isinstance(run, Mapping)
            ],
            "monitorModelRequests": 0,
        }

    def snapshot(
        self, run_id: str, *, qualification: str = ""
    ) -> dict[str, Any]:
        qualification, manifest = self._load_manifest(run_id, qualification)
        children = self._child_manifests(qualification, manifest)
        run = self._run_summary(manifest)
        lanes = [self._lane_summary(child) for child in children]
        return {
            "schemaVersion": "monitor-snapshot/v1",
            "qualification": qualification,
            "run": run,
            "lanes": lanes,
            "identities": self._compact_identities(manifest, children),
            "executionState": self._execution_state(manifest),
            "artifactState": self._artifact_state(manifest),
            "observationHealth": self._observation_health(
                qualification, run_id
            ),
            "monitorModelRequests": 0,
        }

    def events(
        self,
        run_id: str,
        *,
        qualification: str = "",
        after: str = "",
        limit: int = 100,
        wait_ms: int = 0,
    ) -> dict[str, Any]:
        # The manifest lookup proves that the requested run exists. It neither
        # scans nested fields nor invokes the App Server.
        qualification, _manifest = self._load_manifest(run_id, qualification)
        limit = max(1, min(int(limit), MAX_EVENT_LIMIT))
        wait_ms = max(0, min(int(wait_ms), MAX_WAIT_MS))
        if self.event_hub is None:
            return {
                "schemaVersion": "monitor-events/v1",
                "qualification": qualification,
                "runId": run_id,
                "events": [],
                "cursor": self._text(after, 500),
                "observationHealth": {"status": "unavailable"},
                "monitorModelRequests": 0,
            }
        reader = getattr(self.event_hub, "events", None) or getattr(
            self.event_hub, "read_events", None
        )
        if not callable(reader):
            raise RuntimeError("MonitorEventHubにevents readerがありません。")
        try:
            payload = reader(
                qualification,
                run_id,
                after=after,
                limit=limit,
                wait_ms=wait_ms,
            )
        except TypeError:
            payload = reader(run_id, after, limit, wait_ms)
        source = payload if isinstance(payload, Mapping) else {
            "events": list(payload or [])
        }
        raw_events = source.get("events")
        raw_events = raw_events if isinstance(raw_events, list) else []
        events = [
            event
            for value in raw_events[:limit]
            for event in [self._public_event(value)]
            if event is not None
        ]
        return {
            # Collection schema is owned here, never inherited from the hub.
            "schemaVersion": "monitor-events/v1",
            "qualification": qualification,
            "runId": run_id,
            "events": events,
            "cursor": self._text(source.get("cursor") or after, 500),
            "observationHealth": self._health_from_source(source),
            "monitorModelRequests": 0,
        }

    def artifacts(
        self, run_id: str, *, qualification: str = ""
    ) -> dict[str, Any]:
        qualification, manifest = self._load_manifest(run_id, qualification)
        manifest_path = (
            Path(self.run_store.root)
            / qualification
            / self._safe_id(run_id, "runId")
            / "manifest.json"
        )
        # A parent queue manifest can exceed the bounded fallback size while
        # its compact list_summary remains current. Keep serving the latest
        # child-batch artifacts from that projection instead of parsing or
        # rejecting the giant parent manifest.
        try:
            if (
                not manifest_path.is_symlink()
                and manifest_path.stat().st_size <= MAX_MANIFEST_FALLBACK_BYTES
            ):
                manifest = self._read_manifest_full(manifest_path)
        except OSError:
            pass
        children = self._child_manifests(qualification, manifest, full=True)
        parent = self._parent_manifest(qualification, manifest)
        declarations = self._artifact_declarations(
            qualification,
            manifest,
            children,
            parent=parent,
        )
        artifacts: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        content_cache: dict[str, dict[str, Any]] = {}
        total_bytes = 0
        unique_paths = list(dict.fromkeys(item["path"] for item in declarations))

        if len(unique_paths) > MAX_ARTIFACT_FILES:
            rejected.append(
                {
                    "path": "<artifact-limit>",
                    "contentState": {"status": "rejected"},
                    "reasonCode": "file_count_limit",
                }
            )
            allowed_paths = set(unique_paths[:MAX_ARTIFACT_FILES])
        else:
            allowed_paths = set(unique_paths)

        for declaration in declarations:
            relative = declaration["path"]
            if relative not in allowed_paths:
                continue
            if relative not in content_cache:
                remaining = MAX_ARTIFACT_TOTAL_BYTES - total_bytes
                try:
                    if remaining <= 0:
                        raise ArtifactReadError("total_bytes_limit")
                    content = self._read_artifact(
                        qualification,
                        relative,
                        max_bytes=min(MAX_ARTIFACT_BYTES, remaining),
                    )
                    total_bytes += content["size"]
                    content_cache[relative] = content
                except ArtifactReadError as exc:
                    content_cache[relative] = {
                        "rejected": True,
                        "reasonCode": exc.reason_code,
                    }
            content = content_cache[relative]
            if content.get("rejected"):
                rejected.append(
                    {
                        "path": self._public_path(relative),
                        "identity": declaration["identity"],
                        "contentState": {"status": "rejected"},
                        "reasonCode": content["reasonCode"],
                    }
                )
                continue
            try:
                public_content = self._artifact_content(
                    content,
                    declaration,
                )
            except ArtifactReadError as exc:
                rejected.append(
                    {
                        "path": self._public_path(relative),
                        "identity": declaration["identity"],
                        "contentState": {"status": "rejected"},
                        "reasonCode": exc.reason_code,
                    }
                )
                continue
            artifacts.append(
                {
                    "path": self._public_path(relative),
                    "size": content["size"],
                    "contentType": content["contentType"],
                    "content": public_content,
                    "identity": declaration["identity"],
                    "contentState": {"status": "saved"},
                    "receiptValidation": declaration["receiptValidation"],
                    "artifactSync": declaration["artifactSync"],
                }
            )
        return {
            "schemaVersion": "monitor-artifacts/v1",
            "qualification": qualification,
            "runId": run_id,
            "artifacts": artifacts,
            "rejected": rejected,
            "limits": {
                "maxFiles": MAX_ARTIFACT_FILES,
                "maxFileBytes": MAX_ARTIFACT_BYTES,
                "maxTotalBytes": MAX_ARTIFACT_TOTAL_BYTES,
            },
            "artifactState": self._artifact_state(manifest),
            "monitorModelRequests": 0,
        }

    def _load_manifest(
        self, run_id: str, qualification: str
    ) -> tuple[str, dict[str, Any]]:
        run_id = self._safe_id(run_id, "runId")
        root = Path(self.run_store.root)
        if qualification:
            qualification = self._safe_id(qualification, "qualification")
            path = root / qualification / run_id / "manifest.json"
            return qualification, self._read_manifest_projection(path)
        matches = [
            path
            for path in root.glob(f"*/{run_id}/manifest.json")
            if path.is_file() and not path.is_symlink()
        ]
        if not matches:
            raise FileNotFoundError(f"runが見つかりません: {run_id}")
        if len(matches) != 1:
            raise ValueError("runIdが一意ではありません。qualificationを指定してください。")
        return matches[0].parent.parent.name, self._read_manifest_projection(
            matches[0]
        )

    def _read_manifest_projection(self, manifest_path: Path) -> dict[str, Any]:
        if not manifest_path.is_file() or manifest_path.is_symlink():
            raise FileNotFoundError(f"runが見つかりません: {manifest_path.parent.name}")
        sidecar = manifest_path.with_name("list_summary.json")
        try:
            manifest_stat = manifest_path.stat()
            if sidecar.is_symlink() or sidecar.stat().st_size > MAX_LIST_SUMMARY_BYTES:
                raise ValueError("unsafe summary")
            value = json.loads(sidecar.read_text(encoding="utf-8"))
            if (
                not isinstance(value, Mapping)
                or value.get("schemaVersion")
                != "qualification-run-list-summary/v1"
                or value.get("manifestSignature")
                != [
                    manifest_stat.st_ino,
                    manifest_stat.st_mtime_ns,
                    manifest_stat.st_size,
                ]
                or not isinstance(value.get("summary"), Mapping)
            ):
                raise ValueError("stale summary")
            return dict(value["summary"])
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            # Old/test runs may predate the derived sidecar. Parse once and
            # immediately project; never deepcopy or recursively inspect it.
            if manifest_path.stat().st_size > MAX_MANIFEST_FALLBACK_BYTES:
                raise ValueError(
                    "compact list_summaryがない巨大run manifestは表示できません。"
                )
            value = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(value, Mapping):
                raise ValueError("run manifestがobjectではありません。")
            return dict(value)

    def _child_manifests(
        self,
        qualification: str,
        manifest: Mapping[str, Any],
        *,
        full: bool = False,
    ) -> list[dict[str, Any]]:
        values = manifest.get("childRunIds")
        if not isinstance(values, list):
            return []
        children: list[dict[str, Any]] = []
        seen: set[str] = set()
        for value in values[-MAX_SNAPSHOT_CHILDREN:]:
            child_id = str(value or "")
            if child_id in seen or not _SAFE_ID.fullmatch(child_id):
                continue
            seen.add(child_id)
            path = (
                Path(self.run_store.root)
                / qualification
                / child_id
                / "manifest.json"
            )
            try:
                child = (
                    self._read_manifest_full(path)
                    if full
                    else self._read_manifest_projection(path)
                )
            except FileNotFoundError:
                continue
            if str(child.get("parentRunId") or "") != str(
                manifest.get("runId") or ""
            ):
                continue
            children.append(child)
        return children

    @staticmethod
    def _read_manifest_full(manifest_path: Path) -> dict[str, Any]:
        if (
            not manifest_path.is_file()
            or manifest_path.is_symlink()
            or manifest_path.stat().st_size > MAX_MANIFEST_FALLBACK_BYTES
        ):
            raise FileNotFoundError(
                f"runが見つかりません: {manifest_path.parent.name}"
            )
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ValueError("run manifestがobjectではありません。")
        return dict(value)

    def _parent_manifest(
        self,
        qualification: str,
        manifest: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        parent_id = str(manifest.get("parentRunId") or "")
        if not _SAFE_ID.fullmatch(parent_id):
            return None
        path = (
            Path(self.run_store.root)
            / qualification
            / parent_id
            / "manifest.json"
        )
        try:
            parent = self._read_manifest_projection(path)
        except FileNotFoundError:
            return None
        child_ids = parent.get("childRunIds")
        if not isinstance(child_ids, list) or str(manifest.get("runId") or "") not in {
            str(value) for value in child_ids[-MAX_SNAPSHOT_CHILDREN:]
        }:
            return None
        return parent

    def _existing_dashboard_index(
        self, qualification: str
    ) -> list[dict[str, Any]] | None:
        path = Path(self.run_store.root) / qualification / "dashboard_runs.json"
        try:
            if path.is_symlink():
                return None
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if (
            not isinstance(value, Mapping)
            or value.get("schemaVersion")
            != "qualification-dashboard-run-index/v1"
            or value.get("qualification") != qualification
            or value.get("complete") is not True
            or not isinstance(value.get("runs"), list)
        ):
            return None
        return [
            dict(item)
            for item in value["runs"][:500]
            if isinstance(item, Mapping)
        ]

    @staticmethod
    def _safe_id(value: str, label: str) -> str:
        value = str(value or "").strip()
        if not _SAFE_ID.fullmatch(value):
            raise ValueError(f"{label}が不正です。")
        return value

    @classmethod
    def _run_summary(cls, run: Mapping[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        for key in _RUN_FIELDS:
            value = run.get(key)
            if value is None:
                continue
            if isinstance(value, str):
                summary[key] = cls._text(value, 500)
            elif isinstance(value, (int, float, bool)):
                summary[key] = value
            elif key in {"listGroupIds", "targetGroupIds"} and isinstance(
                value, list
            ):
                summary[key] = cls._text_list(value, 100)
        cls._ensure_list_group_id(summary)
        summary["executionState"] = cls._execution_state(run)
        summary["artifactState"] = cls._artifact_state(run)
        return summary

    @classmethod
    def _lane_summary(cls, run: Mapping[str, Any]) -> dict[str, Any]:
        allowed = (
            "runId",
            "parentRunId",
            "status",
            "stageCode",
            "stageLabel",
            "listGroupId",
            "listGroupIds",
            "targetGroupIds",
            "questionId",
            "workItemKey",
            *_BATCH_ID_FIELDS,
            "startedAt",
            "updatedAt",
            "finishedAt",
        )
        lane: dict[str, Any] = {}
        for key in allowed:
            value = run.get(key)
            if isinstance(value, str):
                lane[key] = cls._text(value, 500)
            elif isinstance(value, (int, float, bool)):
                lane[key] = value
            elif key in {"listGroupIds", "targetGroupIds"} and isinstance(
                value, list
            ):
                lane[key] = cls._text_list(value, 100)
        cls._ensure_list_group_id(lane)
        return lane

    @staticmethod
    def _execution_state(manifest: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "status": MonitorReadModel._text(
                manifest.get("status") or "unknown", 100
            ),
            "phase": MonitorReadModel._text(
                manifest.get("executionPhase") or "", 200
            ),
            "heartbeatAt": MonitorReadModel._text(
                manifest.get("heartbeatAt"), 100
            )
            if isinstance(manifest.get("heartbeatAt"), str)
            else None,
            "finishedAt": MonitorReadModel._text(
                manifest.get("finishedAt"), 100
            )
            if isinstance(manifest.get("finishedAt"), str)
            else None,
        }

    @staticmethod
    def _artifact_state(manifest: Mapping[str, Any]) -> dict[str, Any]:
        sync = manifest.get("artifactSync")
        sync = sync if isinstance(sync, Mapping) else {}
        receipt_validated = manifest.get("receiptValidated") is True
        sync_status = MonitorReadModel._text(
            sync.get("status") or "unknown", 100
        )
        return {
            "content": {"status": "declared"},
            "receiptValidation": {
                "status": "validated" if receipt_validated else "pending",
                "validated": receipt_validated,
            },
            "artifactSync": {
                "status": sync_status,
            },
            # Flat compatibility fields contain only the same state values.
            "receiptValidated": receipt_validated,
            "syncStatus": sync_status,
        }

    def _observation_health(
        self, qualification: str, run_id: str
    ) -> dict[str, Any]:
        if self.event_hub is None:
            return {"status": "unavailable"}
        health_reader = getattr(self.event_hub, "health", None)
        if callable(health_reader):
            try:
                value = health_reader(qualification, run_id)
            except TypeError:
                value = health_reader(run_id)
            return self._health_from_source(value)
        snapshot = getattr(self.event_hub, "snapshot", None)
        if not callable(snapshot):
            return {"status": "unknown"}
        try:
            value = snapshot(qualification, run_id)
        except TypeError:
            value = snapshot(run_id)
        return self._health_from_source(value)

    @staticmethod
    def _health_from_source(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {"status": "unknown"}
        health = value.get("observationHealth")
        if isinstance(health, Mapping):
            source = health
            status = MonitorReadModel._text(
                source.get("status") or "unknown", 100
            )
        elif value.get("status") is not None:
            source = value
            status = MonitorReadModel._text(
                source.get("status") or "unknown", 100
            )
        else:
            observation = value.get("observation")
            source = observation if isinstance(observation, Mapping) else {}
            dropped = MonitorReadModel._nonnegative_int(
                source.get("droppedNotifications")
            )
            failures = MonitorReadModel._nonnegative_int(
                source.get("diskFailures")
            )
            status = "degraded" if dropped or failures else "healthy"
        result: dict[str, Any] = {"status": status}
        for key in (
            "gapCount",
            "droppedNotifications",
            "diskFailures",
        ):
            number = MonitorReadModel._nonnegative_int(source.get(key))
            if number is not None:
                result[key] = number
        return result

    @staticmethod
    def _nonnegative_int(value: Any) -> int | None:
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        return None

    @classmethod
    def _compact_identities(
        cls,
        manifest: Mapping[str, Any],
        children: list[Mapping[str, Any]],
    ) -> dict[str, list[str]]:
        fields = (
            "runId",
            "childRunId",
            "questionId",
            "workItemKey",
            "threadId",
            "turnId",
            "itemId",
        )
        found = {key: set() for key in fields}
        values = [manifest, *children]
        for current in values:
            for key in fields:
                value = current.get(key)
                if isinstance(value, (str, int)) and str(value):
                    found[key].add(cls._text(value, 300))
            if current is manifest:
                child_ids = current.get("childRunIds")
                if isinstance(child_ids, list):
                    found["childRunId"].update(
                        cls._text(value, 300)
                        for value in child_ids[-MAX_SNAPSHOT_CHILDREN:]
                        if isinstance(value, (str, int)) and str(value)
                    )
            elif current.get("runId"):
                found["childRunId"].add(
                    cls._text(current["runId"], 300)
                )
        executions = manifest.get("questionExecutions")
        if isinstance(executions, list):
            # Known identity fields only; never recursively scan an execution.
            for execution in executions[:MAX_ARTIFACT_DECLARATIONS]:
                if not isinstance(execution, Mapping):
                    continue
                for key in (
                    "questionId",
                    "workItemKey",
                    "threadId",
                    "turnId",
                    "itemId",
                ):
                    value = execution.get(key)
                    if isinstance(value, (str, int)) and str(value):
                        found[key].add(cls._text(value, 300))
        return {key: sorted(values) for key, values in found.items()}

    def _artifact_declarations(
        self,
        qualification: str,
        manifest: Mapping[str, Any],
        children: list[Mapping[str, Any]],
        *,
        parent: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        state_parent = (
            parent
            if manifest.get("parentRunId")
            else manifest
        )
        declarations: list[dict[str, Any]] = []
        for current in [manifest, *children]:
            declarations.extend(
                self._run_artifact_declarations(
                    qualification,
                    current,
                    parent=state_parent,
                )
            )
            if len(declarations) >= MAX_ARTIFACT_DECLARATIONS:
                break
        return declarations[:MAX_ARTIFACT_DECLARATIONS]

    def _run_artifact_declarations(
        self,
        qualification: str,
        manifest: Mapping[str, Any],
        *,
        parent: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        declarations: list[dict[str, Any]] = []
        attributed: set[str] = set()
        batch_results = manifest.get("batchQuestionResults")
        if isinstance(batch_results, list):
            for index, item in enumerate(
                batch_results[:MAX_ARTIFACT_DECLARATIONS]
            ):
                if len(declarations) >= MAX_ARTIFACT_DECLARATIONS:
                    break
                if not isinstance(item, Mapping):
                    continue
                paths = item.get("changedFiles")
                if not isinstance(paths, list):
                    continue
                for value in paths:
                    if len(declarations) >= MAX_ARTIFACT_DECLARATIONS:
                        break
                    if not isinstance(value, str) or not value.strip():
                        continue
                    relative = value.strip()
                    attributed.add(relative)
                    declarations.append(
                        self._declaration(
                            qualification,
                            manifest,
                            relative,
                            parent=parent,
                            question_result=item,
                            fallback_batch_index=index,
                        )
                    )
                if len(declarations) >= MAX_ARTIFACT_DECLARATIONS:
                    break
        result = manifest.get("result")
        result = result if isinstance(result, Mapping) else {}
        direct_paths: list[Any] = []
        for source in (result.get("changedFiles"), manifest.get("changedFiles")):
            if isinstance(source, list):
                direct_paths.extend(
                    source[
                        : max(
                            0,
                            MAX_ARTIFACT_DECLARATIONS - len(direct_paths),
                        )
                    ]
                )
        for value in direct_paths[
            : max(0, MAX_ARTIFACT_DECLARATIONS - len(declarations))
        ]:
            if (
                not isinstance(value, str)
                or not value.strip()
                or value.strip() in attributed
            ):
                continue
            declarations.append(
                self._declaration(
                    qualification,
                    manifest,
                    value.strip(),
                    parent=parent,
                )
            )
        return declarations

    def _declaration(
        self,
        qualification: str,
        manifest: Mapping[str, Any],
        relative: str,
        *,
        parent: Mapping[str, Any] | None,
        question_result: Mapping[str, Any] | None = None,
        fallback_batch_index: int | None = None,
    ) -> dict[str, Any]:
        identity: dict[str, Any] = {
            "qualification": self._text(qualification, 300)
        }
        parent_run_id = str(
            manifest.get("parentRunId")
            or (
                (parent or {}).get("runId")
                if parent is not None and parent is not manifest
                else ""
            )
            or ""
        )
        run_id = str(manifest.get("runId") or "")
        if parent_run_id:
            identity["parentRunId"] = self._text(parent_run_id, 300)
            identity["childRunId"] = self._text(run_id, 300)
        elif run_id:
            identity["runId"] = self._text(run_id, 300)
        for key in ("questionId", "workItemKey", *_BATCH_ID_FIELDS):
            value = (
                question_result.get(key)
                if isinstance(question_result, Mapping)
                and question_result.get(key) is not None
                else manifest.get(key)
            )
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                identity[key] = (
                    self._text(value, 500)
                    if isinstance(value, str)
                    else value
                )
        group_values: list[Any] = []
        for source in (question_result, manifest):
            if not isinstance(source, Mapping):
                continue
            if source.get("listGroupId") is not None:
                identity["listGroupId"] = self._text(
                    source["listGroupId"], 300
                )
                break
            for key in ("listGroupIds", "targetGroupIds"):
                values = source.get(key)
                if isinstance(values, list):
                    group_values.extend(values)
        if group_values:
            identity["listGroupIds"] = self._text_list(group_values, 100)
            self._ensure_list_group_id(identity)
        if (
            fallback_batch_index is not None
            and not any(key in identity for key in _BATCH_ID_FIELDS)
        ):
            identity["batchIndex"] = fallback_batch_index
        if manifest.get("stageCode") is not None:
            identity["stageCode"] = self._text(manifest["stageCode"], 100)
        record_identity = self._record_identity(
            manifest,
            question_result,
        )
        for key, value in record_identity.items():
            if value:
                identity[key] = self._text(value, 1000)
        if identity.get("listGroupId") is None and record_identity.get(
            "listGroupId"
        ):
            identity["listGroupId"] = self._text(
                record_identity["listGroupId"], 300
            )

        receipt_validated = manifest.get("receiptValidated") is True
        result_status = (
            str(question_result.get("status") or "")
            if isinstance(question_result, Mapping)
            else str((manifest.get("result") or {}).get("status") or "")
            if isinstance(manifest.get("result"), Mapping)
            else ""
        )
        receipt_status = (
            "failed"
            if result_status == "failed"
            else "validated"
            if receipt_validated and result_status in {"", "succeeded"}
            else "pending"
        )
        sync = manifest.get("artifactSync")
        sync = sync if isinstance(sync, Mapping) else {}
        parent_sync = (parent or {}).get("artifactSync")
        parent_sync = parent_sync if isinstance(parent_sync, Mapping) else {}
        artifact_sync: dict[str, Any] = {
            "status": self._text(sync.get("status") or "unknown", 100)
        }
        if parent is not None and parent is not manifest:
            artifact_sync["parentStatus"] = self._text(
                parent_sync.get("status") or "unknown", 100
            )
        return {
            "path": relative,
            "identity": identity,
            "_recordIdentity": record_identity,
            # Every batchQuestionResults declaration is question-scoped. If
            # its exact question identity is absent, shared JSON/JSONL must
            # fail closed instead of falling back to the manifest identity
            # and exposing every record in the shared patch.
            "_questionScoped": isinstance(question_result, Mapping),
            "receiptValidation": {
                "status": receipt_status,
                "validated": receipt_status == "validated",
            },
            "artifactSync": artifact_sync,
        }

    def _record_identity(
        self,
        manifest: Mapping[str, Any],
        question_result: Mapping[str, Any] | None,
    ) -> dict[str, str]:
        if not isinstance(question_result, Mapping):
            return {}
        question_id = str(question_result.get("questionId") or "")
        if not question_id:
            return {}
        candidates: list[Mapping[str, Any]] = []
        for field in ("targetRecordBindings", "progressTargets"):
            values = manifest.get(field)
            if not isinstance(values, list):
                continue
            for value in values[:MAX_ARTIFACT_DECLARATIONS]:
                if not isinstance(value, Mapping):
                    continue
                aliases = {
                    str(candidate)
                    for candidate in (
                        value.get("id"),
                        value.get("uiQuestionId"),
                        value.get("reviewQuestionId"),
                        value.get("sourceQuestionKey"),
                        value.get("sourceRecordRef"),
                        *(value.get("aliases") or []),
                    )
                    if candidate
                }
                if question_id in aliases:
                    candidates.append(value)
        fields = (
            "sourceQuestionKey",
            "sourceRecordRef",
            "reviewQuestionId",
            "listGroupId",
        )
        resolved: dict[str, str] = {}
        for field in fields:
            values = {
                str(value.get(field) or "")
                for value in candidates
                if value.get(field)
            }
            if len(values) > 1:
                return {}
            resolved[field] = next(iter(values), "")
        if not (
            resolved["sourceQuestionKey"] or resolved["sourceRecordRef"]
        ):
            return {}
        return resolved

    def _read_artifact(
        self,
        qualification: str,
        relative: str,
        *,
        max_bytes: int,
    ) -> dict[str, Any]:
        pure = PurePosixPath(relative)
        if (
            pure.is_absolute()
            or not pure.parts
            or "." in pure.parts
            or ".." in pure.parts
            or "\\" in relative
        ):
            raise ArtifactReadError("unsafe_path")
        allowed_root = PurePosixPath("output", qualification)
        if not (pure == allowed_root or pure.is_relative_to(allowed_root)):
            raise ArtifactReadError("outside_artifact_root")
        if (
            "question_review_console" in pure.parts
            or pure.suffix.lower() not in _ALLOWED_ARTIFACT_SUFFIXES
        ):
            raise ArtifactReadError("unsupported_artifact_type")

        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        directory_flags = (
            flags
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        file_flags = flags | getattr(os, "O_NOFOLLOW", 0)
        opened: list[int] = []
        try:
            descriptor = os.open(self.repo_root, directory_flags)
            opened.append(descriptor)
            for part in pure.parts[:-1]:
                descriptor = os.open(part, directory_flags, dir_fd=descriptor)
                opened.append(descriptor)
            file_descriptor = os.open(
                pure.parts[-1],
                file_flags,
                dir_fd=descriptor,
            )
            opened.append(file_descriptor)
            before = os.fstat(file_descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ArtifactReadError("not_regular_file")
            if before.st_size > MAX_ARTIFACT_BYTES:
                raise ArtifactReadError("file_bytes_limit")
            if before.st_size > max_bytes:
                raise ArtifactReadError("total_bytes_limit")
            remaining = min(max_bytes, MAX_ARTIFACT_BYTES) + 1
            chunks: list[bytes] = []
            while remaining > 0:
                chunk = os.read(file_descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            after = os.fstat(file_descriptor)
            if (
                len(data) > min(max_bytes, MAX_ARTIFACT_BYTES)
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or len(data) != after.st_size
            ):
                raise ArtifactReadError("file_changed_during_read")
        except ArtifactReadError:
            raise
        except OSError as exc:
            raise ArtifactReadError("unavailable") from exc
        finally:
            for descriptor in reversed(opened):
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        try:
            text = data.decode("utf-8")
        except UnicodeError as exc:
            raise ArtifactReadError("invalid_utf8") from exc
        return {
            "size": len(data),
            "contentType": self._content_type(pure),
            "_rawContent": text,
        }

    def _artifact_content(
        self,
        content: Mapping[str, Any],
        declaration: Mapping[str, Any],
    ) -> str:
        raw = str(content.get("_rawContent") or "")
        content_type = str(content.get("contentType") or "")
        if not declaration.get("_questionScoped") or content_type not in {
            "application/json",
            "application/x-ndjson",
        }:
            return self._text(raw, MAX_ARTIFACT_BYTES)
        identity = declaration.get("_recordIdentity")
        if not isinstance(identity, Mapping) or not (
            identity.get("sourceQuestionKey") or identity.get("sourceRecordRef")
        ):
            raise ArtifactReadError("record_resolution_failed")
        try:
            if content_type == "application/x-ndjson":
                records = [
                    value
                    for line in raw.splitlines()
                    if line.strip()
                    for value in [json.loads(line)]
                    if isinstance(value, Mapping)
                ]
            else:
                value = json.loads(raw)
                records = self._top_level_records(value)
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ArtifactReadError("record_resolution_failed") from exc
        source_key = str(identity.get("sourceQuestionKey") or "")
        source_ref = str(identity.get("sourceRecordRef") or "")
        matches = []
        for record in records:
            record_key = str(record.get("sourceQuestionKey") or "")
            record_ref = str(record.get("sourceRecordRef") or "")
            if not (
                (source_key and record_key == source_key)
                or (source_ref and record_ref == source_ref)
            ):
                continue
            if source_key and record_key and record_key != source_key:
                continue
            if source_ref and record_ref and record_ref != source_ref:
                continue
            matches.append(record)
        if len(matches) != 1:
            raise ArtifactReadError("record_resolution_failed")
        return self._text(
            json.dumps(matches[0], ensure_ascii=False, indent=2),
            MAX_ARTIFACT_BYTES,
        )

    @staticmethod
    def _top_level_records(value: Any) -> list[Mapping[str, Any]]:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
        if not isinstance(value, Mapping):
            return []
        if value.get("sourceQuestionKey") or value.get("sourceRecordRef"):
            return [value]
        records: list[Mapping[str, Any]] = []
        for child in value.values():
            if isinstance(child, list):
                records.extend(
                    item for item in child if isinstance(item, Mapping)
                )
        return records

    @classmethod
    def _public_event(cls, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, Mapping):
            return None
        event_type = str(value.get("type") or "")
        if event_type not in _EVENT_TYPES:
            return None
        result: dict[str, Any] = {
            "schemaVersion": "monitor-event/v1",
            "eventId": cls._text(value.get("eventId"), 500),
            "serverInstanceId": cls._text(value.get("serverInstanceId"), 300),
            "sequence": cls._nonnegative_int(value.get("sequence")) or 0,
            "observedAt": (
                value.get("observedAt")
                if isinstance(value.get("observedAt"), (int, float))
                and not isinstance(value.get("observedAt"), bool)
                else 0
            ),
            "type": event_type,
            "correlation": {},
            "payload": {},
        }
        correlation = value.get("correlation")
        if isinstance(correlation, Mapping):
            result["correlation"] = {
                key: cls._text(correlation[key], 300)
                for key in _CORRELATION_FIELDS
                if correlation.get(key) is not None
            }
            for key in _CORRELATION_LIST_FIELDS:
                values = correlation.get(key)
                if isinstance(values, list):
                    result["correlation"][key] = cls._text_list(values, 200)
        payload = value.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        if event_type in _TEXT_EVENT_TYPES:
            result["payload"] = {
                key: cls._text(payload[key], 100_000)
                for key in ("delta", "text", "phase", "state")
                if payload.get(key) is not None
            }
            if event_type == "reasoningSummary":
                parts = payload.get("summaryParts")
                if isinstance(parts, list):
                    result["payload"]["summaryParts"] = [
                        cls._text(part, 100_000) for part in parts[:200]
                    ]
                summary_index = cls._nonnegative_int(payload.get("summaryIndex"))
                if summary_index is not None:
                    result["payload"]["summaryIndex"] = summary_index
        elif event_type == "reasoningSummaryPart":
            summary_index = cls._nonnegative_int(payload.get("summaryIndex"))
            if summary_index is not None:
                result["payload"] = {"summaryIndex": summary_index}
        elif event_type == "plan":
            public_plan: list[dict[str, str]] = []
            raw_plan = payload.get("plan")
            if isinstance(raw_plan, list):
                for item in raw_plan[:200]:
                    if not isinstance(item, Mapping):
                        continue
                    step = cls._text(item.get("step"), 100_000)
                    status = cls._text(item.get("status"), 100)
                    if step and status:
                        public_plan.append({"step": step, "status": status})
            result["payload"] = {
                key: cls._text(payload[key], 100_000)
                for key in ("delta", "text", "state", "explanation")
                if payload.get(key) is not None
            }
            if public_plan:
                result["payload"]["plan"] = public_plan
        elif event_type == "toolState":
            result["payload"] = {
                key: cls._text(payload[key], 100)
                for key in ("toolType", "state")
                if payload.get(key) is not None
            }
        elif event_type == "turnState":
            if payload.get("state") is not None:
                result["payload"] = {
                    "state": cls._text(payload["state"], 100)
                }
        elif event_type == "threadState":
            result["payload"] = {
                "state": cls._text(payload.get("state"), 100)
            }
            active_flags = payload.get("activeFlags")
            if isinstance(active_flags, list):
                result["payload"]["activeFlags"] = cls._text_list(
                    active_flags, 20
                )
        elif event_type == "tokenUsage":
            usage = payload.get("usage")
            usage = usage if isinstance(usage, Mapping) else {}
            public_usage: dict[str, Any] = {}
            for section in ("last", "total"):
                breakdown = usage.get(section)
                breakdown = breakdown if isinstance(breakdown, Mapping) else {}
                public_usage[section] = {
                    key: number
                    for key in _TOKEN_FIELDS
                    for number in [cls._nonnegative_int(breakdown.get(key))]
                    if number is not None
                }
            context_window = cls._nonnegative_int(
                usage.get("modelContextWindow")
            )
            if context_window is not None:
                public_usage["modelContextWindow"] = context_window
            result["payload"] = {
                "usage": public_usage
            }
        elif event_type == "error":
            result["payload"] = {
                "message": cls._text(payload.get("message"), 100_000),
                "willRetry": payload.get("willRetry") is True,
            }
        else:
            result["payload"] = {
                key: number
                for key in ("fromSequence", "toSequence")
                for number in [cls._nonnegative_int(payload.get(key))]
                if number is not None
            }
        return result

    @staticmethod
    def _public_path(value: str) -> str:
        pure = PurePosixPath(value)
        if pure.is_absolute() or ".." in pure.parts:
            return "<unsafe-path>"
        return MonitorReadModel._text(value, 2000)

    @staticmethod
    def _content_type(path: PurePosixPath) -> str:
        if path.suffix.lower() == ".json":
            return "application/json"
        if path.suffix.lower() == ".jsonl":
            return "application/x-ndjson"
        return "text/plain"

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        text = str(value or "")[:limit]
        folded = text.casefold()
        if "private key" in folded:
            text = _PRIVATE_KEY.sub("<redacted-private-key>", text)
        if "file:" in folded:
            text = _FILE_URL.sub("<absolute-path>", text)
        if "\\" in text:
            text = _WINDOWS_ABSOLUTE_PATH.sub("<absolute-path>", text)
        if "://" in text and "@" in text:
            text = _URL_CREDENTIAL.sub(r"\1<redacted>@", text)
        text = _ABSOLUTE_PATH.sub("<absolute-path>", text)
        if "eyj" in folded:
            text = _JWT.sub("<redacted-jwt>", text)
        if any(
            prefix in folded
            for prefix in (
                "bearer ",
                "sk-",
                "ghp_",
                "akia",
                "xoxb-",
                "xoxa-",
                "xoxp-",
                "xoxr-",
                "xoxs-",
                "glpat-",
                "aiza",
            )
        ):
            text = _SECRET_TOKEN.sub("<redacted>", text)
        if any(
            key in folded
            for key in (
                "password",
                "passphrase",
                "api_key",
                "api-key",
                "token",
                "authorization",
                "cookie",
                "secret",
            )
        ):
            text = _SECRET_VALUE.sub(
                lambda match: f"{match.group(1)}=<redacted>",
                text,
            )
        return text

    @classmethod
    def _text_list(cls, values: list[Any], limit: int) -> list[str]:
        return list(
            dict.fromkeys(
                cls._text(value, 300)
                for value in values[:limit]
                if isinstance(value, (str, int)) and str(value)
            )
        )

    @staticmethod
    def _ensure_list_group_id(value: dict[str, Any]) -> None:
        if value.get("listGroupId"):
            return
        groups = value.get("listGroupIds") or value.get("targetGroupIds")
        if isinstance(groups, list) and len(groups) == 1:
            value["listGroupId"] = groups[0]


class ArtifactReadError(ValueError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code
