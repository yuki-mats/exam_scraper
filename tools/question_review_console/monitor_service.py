from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
import re
import stat
import threading
from collections import OrderedDict
from itertools import islice
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


MAX_ARTIFACT_BYTES = 1024 * 1024
MAX_ARTIFACT_TOTAL_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_FILES = 64
MAX_ARTIFACT_DECLARATIONS = 256
MAX_SNAPSHOT_CHILDREN = 128
MAX_ARTIFACT_CHILDREN = 512
MAX_V2_QUESTION_STATES = 1024
MAX_V2_PLAN_BYTES = 16 * 1024 * 1024
MAX_V2_PLAN_CACHE_ENTRIES = 8
MAX_V2_PLAN_CACHE_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_FALLBACK_BYTES = 8 * 1024 * 1024
MAX_LIST_SUMMARY_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_COLLECTION_BYTES = 16 * 1024 * 1024
MAX_DASHBOARD_INDEX_BYTES = 8 * 1024 * 1024
MAX_DASHBOARD_SCAN_ENTRIES = 4096
MAX_EVENT_LIMIT = 500
MAX_EVENT_TOTAL_BYTES = 4 * 1024 * 1024
MAX_MONITOR_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_WAIT_MS = 30_000
MAX_PUBLIC_EVENT_TEXT = 4096
MAX_PUBLIC_EVENT_COLLECTION_ITEMS = 64
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,299}$")
_SAFE_ATTEMPT_TOKEN = re.compile(r"^[0-9a-f]{16}$")
_SAFE_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_V2_RUN_SCHEMA = "question-maintenance-run/v2"
_V2_SUMMARY_SCHEMA = "question-maintenance-summary/v2"
_V2_QUESTION_SCHEMA = "question-maintenance-question/v2"
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
_CORRELATION_LIST_FIELDS = (
    "questionIds",
    "workItemKeys",
    "listGroupIds",
    "affectedRunIds",
)
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
    r"(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|$)",
    re.IGNORECASE | re.DOTALL,
)
_FILE_URL = re.compile(r"(?i)\bfile:/+(?:[^\s\"'<>\[\]{}()]+)")
_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?i)(?<![\w])(?:[A-Z]:\\|\\\\)[^\s\"'<>\[\]{}()]+"
)
_ABSOLUTE_PATH = re.compile(
    r"(?<![\w./+])/(?:"
    r"Users|home|root|workspace|workspaces|tmp|private|var|etc|opt|usr|"
    r"bin|sbin|lib|Library|Applications|Volumes|mnt|srv|dev|proc|sys|run|"
    r"app|data|nix"
    r")(?:/[^\s\"'<>\[\]{}()]*)?"
)
_AUTHORIZATION_HEADER = re.compile(
    r"(?im)\b([\"']?(?:Authorization|Proxy-Authorization)[\"']?"
    r"\s*[:=]\s*)[^\r\n]*"
)
_COOKIE_HEADER = re.compile(
    r"(?im)\b([\"']?(?:Cookie|Set-Cookie)[\"']?\s*[:=]\s*)[^\r\n]*"
)
_SECRET_TOKEN = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:(?:Bearer|Basic)\s+"
    r"[A-Za-z0-9._~+/=-]+|sk-[A-Za-z0-9_-]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|"
    r"gh[pousr]_[A-Za-z0-9_]{8,}|AKIA[A-Z0-9]{12,}|"
    r"xox[baprs]-[A-Za-z0-9-]{8,}|glpat-[A-Za-z0-9_-]{8,}|"
    r"AIza[A-Za-z0-9_-]{20,})(?![A-Za-z0-9_])"
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
        self._run_store_lexical_root = Path(run_store.root).absolute()
        self.run_store_root = Path(run_store.root).resolve()
        try:
            self.run_store_root.relative_to(self.repo_root)
        except ValueError as exc:
            raise ValueError("run storeがrepository外です。") from exc
        self._v2_plan_cache_lock = threading.RLock()
        self._v2_plan_cache: OrderedDict[
            tuple[str, str, str],
            tuple[tuple[int, ...], list[dict[str, Any]], int],
        ] = OrderedDict()
        self._v2_plan_cache_bytes = 0
        self.event_hub = event_hub

    def runs(self, qualification: str, *, limit: int = 100) -> dict[str, Any]:
        qualification = self._safe_id(qualification, "qualification")
        limit = max(1, min(int(limit), 500))
        indexed, index_truncated, index_bytes = (
            self._existing_dashboard_index(qualification)
        )
        if indexed is None:
            # A monitor GET must never ask QualificationRunStore to rebuild
            # dashboard/list-summary projections because that path can also
            # reconcile human receipts into the authoritative manifest.
            remaining_bytes = MAX_MANIFEST_COLLECTION_BYTES - index_bytes
            if remaining_bytes <= 0:
                runs, truncated = [], True
            else:
                runs, truncated = self._read_only_dashboard_runs(
                    qualification,
                    limit=limit,
                    max_bytes=remaining_bytes,
                )
            truncated = truncated or index_truncated
        else:
            runs, truncated = indexed, index_truncated
        summaries = [
            self._run_summary(run)
            for run in runs[:limit]
            if isinstance(run, Mapping)
        ]
        response: dict[str, Any] = {
            "schemaVersion": "monitor-run-list/v1",
            "qualification": qualification,
            "runs": [],
            "truncated": truncated or len(runs) > limit,
            "limits": {"maxTotalBytes": MAX_MONITOR_RESPONSE_BYTES},
            "monitorModelRequests": 0,
        }
        used_bytes = self._compact_json_size(response)
        for summary in summaries:
            increment = self._compact_json_size(summary) + (
                1 if response["runs"] else 0
            )
            if used_bytes + increment > MAX_MONITOR_RESPONSE_BYTES - 4096:
                response["truncated"] = True
                break
            response["runs"].append(summary)
            used_bytes += increment
        while (
            response["runs"]
            and self._compact_json_size(response) > MAX_MONITOR_RESPONSE_BYTES
        ):
            response["runs"].pop()
            response["truncated"] = True
        return response

    def snapshot(
        self, run_id: str, *, qualification: str = ""
    ) -> dict[str, Any]:
        qualification, manifest = self._load_manifest(run_id, qualification)
        manifest = self._prefer_full_manifest(
            qualification,
            run_id,
            manifest,
        )
        parent = self._parent_manifest(qualification, manifest)
        if manifest.get("schemaVersion") == _V2_RUN_SCHEMA:
            summary, summary_bytes, child_issues = self._v2_question_summary(
                qualification,
                manifest,
            )
            lanes, lane_issues = self._v2_lanes(
                summary,
                [],
            )
            child_issues.extend(lane_issues)
            selected_question_ids = {
                str(lane.get("questionId") or "")
                for lane in lanes
                if str(lane.get("questionId") or "")
            }
            lane_attempts, state_issues = self._v2_attempt_projections(
                qualification,
                manifest,
                summary,
                consumed_bytes=summary_bytes,
                selected_question_ids=selected_question_ids,
                include_active=True,
                max_states=MAX_SNAPSHOT_CHILDREN,
                max_projections=MAX_SNAPSHOT_CHILDREN * 2,
            )
            child_issues.extend(state_issues)
            lanes, _overlay_issues = self._v2_lanes(
                summary,
                lane_attempts,
            )
            if (
                str(manifest.get("status") or "").casefold()
                in {
                    "completed",
                    "succeeded",
                    "validated",
                    "failed",
                    "interrupted",
                    "cancelled",
                }
                and any(
                    str(lane.get("status") or "").casefold()
                    in {
                        "queued",
                        "running",
                        "active",
                        "in_progress",
                        "inprogress",
                        "working",
                        "started",
                        "preparing",
                        "prepared",
                        "committing",
                        "validating",
                    }
                    for lane in lanes
                )
            ):
                child_issues.append(
                    "v2_terminal_run_has_nonterminal_lanes"
                )
            children = lanes
            artifact_children = [
                attempt
                for attempt in lane_attempts
                if attempt.get("receiptValidated") is True
            ]
            artifact_fingerprint_complete = (
                self._v2_artifact_fingerprint_complete(
                    summary,
                    artifact_children,
                )
            )
            artifact_fingerprint = self._v2_combined_artifact_fingerprint(
                qualification,
                manifest,
                summary,
                artifact_children,
                parent=parent,
            )
        else:
            artifact_children, child_issues = self._child_manifests(
                qualification,
                manifest,
                full=True,
                max_children=MAX_ARTIFACT_CHILDREN,
            )
            raw_child_ids = manifest.get("childRunIds")
            if (
                isinstance(raw_child_ids, list)
                and len(raw_child_ids) > MAX_SNAPSHOT_CHILDREN
            ):
                child_issues.append("child_manifest_limit")
            children = artifact_children[-MAX_SNAPSHOT_CHILDREN:]
            lanes = [self._lane_summary(child) for child in children]
            artifact_fingerprint = self._artifact_fingerprint(
                qualification,
                manifest,
                artifact_children,
                parent=parent,
            )
            artifact_fingerprint_complete = True
        run = self._run_summary(manifest)
        identities = self._compact_identities(
            manifest,
            [*children, *artifact_children],
        )
        response: dict[str, Any] = {
            "schemaVersion": "monitor-snapshot/v1",
            "qualification": qualification,
            "run": run,
            "lanes": [],
            "identities": identities,
            "executionState": self._execution_state(manifest),
            "artifactState": self._artifact_state(manifest),
            "artifactFingerprint": artifact_fingerprint,
            "artifactFingerprintComplete": artifact_fingerprint_complete,
            "observationHealth": self._observation_health(
                qualification, run_id
            ),
            "truncated": bool(child_issues),
            "warnings": sorted(set(child_issues)),
            "limits": {"maxTotalBytes": MAX_MONITOR_RESPONSE_BYTES},
            "monitorModelRequests": 0,
        }
        used_bytes = self._compact_json_size(response)
        selected_lanes: list[dict[str, Any]] = []
        for lane in reversed(lanes):
            increment = self._compact_json_size(lane) + (
                1 if selected_lanes else 0
            )
            if used_bytes + increment > MAX_MONITOR_RESPONSE_BYTES - 4096:
                child_issues.append("snapshot_response_bytes_limit")
                break
            selected_lanes.append(lane)
            used_bytes += increment
        response["lanes"] = list(reversed(selected_lanes))
        response["truncated"] = bool(child_issues)
        response["warnings"] = sorted(set(child_issues))
        while (
            response["lanes"]
            and self._compact_json_size(response) > MAX_MONITOR_RESPONSE_BYTES
        ):
            response["lanes"].pop(0)
            response["truncated"] = True
            response["warnings"] = sorted(
                {*response["warnings"], "snapshot_response_bytes_limit"}
            )
        return response

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
        iterable_truncated = False
        if isinstance(payload, Mapping):
            source = payload
        else:
            try:
                bounded_events = list(islice(iter(payload or ()), limit + 1))
            except TypeError:
                bounded_events = []
            iterable_truncated = len(bounded_events) > limit
            source = {"events": bounded_events[:limit]}
        raw_events = source.get("events")
        raw_events = raw_events if isinstance(raw_events, list) else []
        health = self._health_from_source(source)
        events: list[dict[str, Any]] = []
        event_bytes = 0
        processed_cursor = self._text(after, 500)
        exhausted = len(raw_events) <= limit and not iterable_truncated
        for value in raw_events[:limit]:
            public = self._public_event(value)
            raw_cursor = self._public_scalar_text(
                value.get("eventId") if isinstance(value, Mapping) else None,
                500,
            )
            if not raw_cursor and isinstance(value, Mapping):
                server_instance_id = self._public_scalar_text(
                    value.get("serverInstanceId"),
                    300,
                )
                sequence = self._nonnegative_int(value.get("sequence"))
                if server_instance_id and sequence is not None:
                    raw_cursor = f"{server_instance_id}:{sequence}"
            if public is None:
                if raw_cursor:
                    processed_cursor = raw_cursor
                continue
            increment = self._compact_json_size(public) + (1 if events else 0)
            if event_bytes + increment > MAX_EVENT_TOTAL_BYTES - 4096:
                exhausted = False
                break
            events.append(public)
            event_bytes += increment
            if raw_cursor:
                processed_cursor = raw_cursor
        cursor = (
            self._text(source.get("cursor") or processed_cursor, 500)
            if exhausted
            else processed_cursor
        )
        response = {
            # Collection schema is owned here, never inherited from the hub.
            "schemaVersion": "monitor-events/v1",
            "qualification": qualification,
            "runId": run_id,
            "events": events,
            "cursor": cursor,
            "observationHealth": health,
            "truncated": not exhausted,
            "limits": {"maxTotalBytes": MAX_EVENT_TOTAL_BYTES},
            "monitorModelRequests": 0,
        }
        while (
            events
            and self._compact_json_size(response) > MAX_EVENT_TOTAL_BYTES
        ):
            events.pop()
            response["truncated"] = True
            response["cursor"] = (
                events[-1]["eventId"]
                if events and events[-1].get("eventId")
                else self._text(after, 500)
            )
        return response

    def artifacts(
        self, run_id: str, *, qualification: str = ""
    ) -> dict[str, Any]:
        qualification, manifest = self._load_manifest(run_id, qualification)
        # A parent queue manifest can exceed the bounded fallback size while
        # its compact list_summary remains current. Keep serving the latest
        # child-batch artifacts from that projection instead of parsing or
        # rejecting the giant parent manifest.
        manifest = self._prefer_full_manifest(
            qualification,
            run_id,
            manifest,
        )
        if manifest.get("schemaVersion") == _V2_RUN_SCHEMA:
            summary, summary_bytes, child_issues = self._v2_question_summary(
                qualification,
                manifest,
            )
            children, state_issues = self._v2_attempt_projections(
                qualification,
                manifest,
                summary,
                consumed_bytes=summary_bytes,
                include_active=False,
                max_states=MAX_V2_QUESTION_STATES,
                max_projections=MAX_ARTIFACT_CHILDREN,
            )
            child_issues.extend(state_issues)
        else:
            children, child_issues = self._child_manifests(
                qualification,
                manifest,
                full=True,
            )
        parent = self._parent_manifest(qualification, manifest)
        declarations, declarations_truncated = self._artifact_declarations(
            qualification,
            manifest,
            children,
            parent=parent,
        )
        artifacts: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = [
            {
                "path": "<run-projections>",
                "contentState": {"status": "rejected"},
                "reasonCode": reason,
                "truncated": True,
            }
            for reason in sorted(set(child_issues))
        ]
        if declarations_truncated:
            rejected.append(
                {
                    "path": "<declaration-limit>",
                    "contentState": {"status": "rejected"},
                    "reasonCode": "declaration_limit",
                    "truncated": True,
                }
            )
        response: dict[str, Any] = {
            "schemaVersion": "monitor-artifacts/v1",
            "qualification": qualification,
            "runId": run_id,
            "artifacts": artifacts,
            "rejected": rejected,
            "truncated": declarations_truncated or bool(child_issues),
            "limits": {
                "maxFiles": MAX_ARTIFACT_FILES,
                "maxFileBytes": MAX_ARTIFACT_BYTES,
                "maxTotalBytes": MAX_ARTIFACT_TOTAL_BYTES,
            },
            "artifactState": self._artifact_state(manifest),
            "monitorModelRequests": 0,
        }
        payload_bytes = self._compact_json_size(response)
        truncation_rejection = {
            "path": "<response-limit>",
            "contentState": {"status": "rejected"},
            "reasonCode": "response_bytes_limit",
            "truncated": True,
        }
        truncation_rejection_bytes = self._compact_json_size(
            truncation_rejection
        )

        response_limit_marked = False

        def set_truncated() -> None:
            nonlocal payload_bytes
            if not response["truncated"]:
                response["truncated"] = True
                # Compact JSON renders ``false`` with one more byte than ``true``.
                payload_bytes -= 1

        def mark_payload_truncated() -> None:
            nonlocal payload_bytes
            nonlocal response_limit_marked
            if response_limit_marked:
                return
            response_limit_marked = True
            set_truncated()
            increment = truncation_rejection_bytes + (1 if rejected else 0)
            if payload_bytes + increment <= MAX_ARTIFACT_TOTAL_BYTES:
                rejected.append(truncation_rejection)
                payload_bytes += increment

        def append_bounded(
            bucket: list[dict[str, Any]],
            item: dict[str, Any],
        ) -> bool:
            nonlocal payload_bytes
            item_increment = self._compact_json_size(item) + (
                1 if bucket else 0
            )
            future_rejections = len(rejected) + (
                1 if bucket is rejected else 0
            )
            marker_reserve = truncation_rejection_bytes + (
                1 if future_rejections else 0
            )
            if (
                payload_bytes + item_increment + marker_reserve
                > MAX_ARTIFACT_TOTAL_BYTES
            ):
                mark_payload_truncated()
                return False
            bucket.append(item)
            payload_bytes += item_increment
            return True

        content_cache: dict[str, dict[str, Any]] = {}
        total_bytes = 0
        unique_paths = list(
            dict.fromkeys(
                item["path"]
                for item in declarations
                if not item.get("_preRejected")
            )
        )

        if len(unique_paths) > MAX_ARTIFACT_FILES:
            set_truncated()
            append_bounded(
                rejected,
                {
                    "path": "<artifact-limit>",
                    "contentState": {"status": "rejected"},
                    "reasonCode": "file_count_limit",
                    "truncated": True,
                },
            )
            allowed_paths = set(unique_paths[:MAX_ARTIFACT_FILES])
        else:
            allowed_paths = set(unique_paths)

        for declaration in declarations:
            relative = declaration["path"]
            pre_rejected = declaration.get("_preRejected")
            if isinstance(pre_rejected, str) and pre_rejected:
                if not append_bounded(
                    rejected,
                    {
                        "path": self._public_path(relative),
                        "identity": declaration["identity"],
                        "contentState": {"status": "rejected"},
                        "reasonCode": pre_rejected,
                    },
                ):
                    break
                continue
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
                    if exc.reason_code == "total_bytes_limit":
                        set_truncated()
                    content_cache[relative] = {
                        "rejected": True,
                        "reasonCode": exc.reason_code,
                    }
            content = content_cache[relative]
            if content.get("rejected"):
                if not append_bounded(
                    rejected,
                    {
                        "path": self._public_path(relative),
                        "identity": declaration["identity"],
                        "contentState": {"status": "rejected"},
                        "reasonCode": content["reasonCode"],
                        **(
                            {"truncated": True}
                            if content["reasonCode"]
                            == "total_bytes_limit"
                            else {}
                        ),
                    },
                ):
                    break
                continue
            try:
                public_content = self._artifact_content(
                    content,
                    declaration,
                )
            except ArtifactReadError as exc:
                if not append_bounded(
                    rejected,
                    {
                        "path": self._public_path(relative),
                        "identity": declaration["identity"],
                        "contentState": {"status": "rejected"},
                        "reasonCode": exc.reason_code,
                    },
                ):
                    break
                continue
            if not append_bounded(
                artifacts,
                {
                    "path": self._public_path(relative),
                    "size": content["size"],
                    "contentType": content["contentType"],
                    "content": public_content,
                    "identity": declaration["identity"],
                    "contentState": {"status": "saved"},
                    "receiptValidation": declaration["receiptValidation"],
                    "artifactSync": declaration["artifactSync"],
                },
            ):
                break
        if self._compact_json_size(response) > MAX_ARTIFACT_TOTAL_BYTES:
            # Accounting above is exact; this is a fail-closed safeguard if
            # the response shape changes without updating the byte budget.
            response["artifacts"] = []
            response["rejected"] = [truncation_rejection]
            response["truncated"] = True
        return response

    def _load_manifest(
        self, run_id: str, qualification: str
    ) -> tuple[str, dict[str, Any]]:
        run_id = self._safe_id(run_id, "runId")
        if qualification:
            qualification = self._safe_id(qualification, "qualification")
            path = self.run_store_root / qualification / run_id / "manifest.json"
            manifest = self._read_manifest_projection(path)
            self._validate_manifest_identity(manifest, qualification, run_id)
            return qualification, manifest

        matches: list[tuple[str, dict[str, Any]]] = []
        truncated = False
        consumed_bytes = 0
        try:
            entries = os.scandir(self.run_store_root)
        except OSError as exc:
            raise FileNotFoundError(f"runが見つかりません: {run_id}") from exc
        with entries:
            for index, entry in enumerate(entries):
                if index >= MAX_DASHBOARD_SCAN_ENTRIES:
                    truncated = True
                    break
                if (
                    not _SAFE_ID.fullmatch(entry.name)
                    or not entry.is_dir(follow_symlinks=False)
                ):
                    continue
                path = (
                    self.run_store_root
                    / entry.name
                    / run_id
                    / "manifest.json"
                )
                try:
                    remaining_bytes = (
                        MAX_MANIFEST_COLLECTION_BYTES - consumed_bytes
                    )
                    if remaining_bytes <= 0:
                        truncated = True
                        break
                    manifest, manifest_bytes = (
                        self._read_manifest_projection_with_size(
                            path,
                            max_bytes=remaining_bytes,
                        )
                    )
                    consumed_bytes += manifest_bytes
                    self._validate_manifest_identity(
                        manifest,
                        entry.name,
                        run_id,
                    )
                except MonitorStoreReadError as exc:
                    consumed_bytes += min(
                        exc.bytes_read,
                        MAX_MANIFEST_COLLECTION_BYTES - consumed_bytes,
                    )
                    if (
                        exc.reason_code == "file_bytes_limit"
                        or consumed_bytes >= MAX_MANIFEST_COLLECTION_BYTES
                    ):
                        truncated = True
                        break
                    continue
                except (
                    FileNotFoundError,
                    OSError,
                    UnicodeError,
                    ValueError,
                ):
                    continue
                matches.append((entry.name, manifest))
        if truncated:
            raise ValueError("探索上限のためqualificationを指定してください。")
        if not matches:
            raise FileNotFoundError(f"runが見つかりません: {run_id}")
        if len(matches) != 1:
            raise ValueError("runIdが一意ではありません。qualificationを指定してください。")
        return matches[0]

    def _prefer_full_manifest(
        self,
        qualification: str,
        run_id: str,
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        path = (
            self.run_store_root
            / qualification
            / self._safe_id(run_id, "runId")
            / "manifest.json"
        )
        try:
            manifest = self._read_manifest_full(path)
            self._validate_manifest_identity(
                manifest,
                qualification,
                run_id,
            )
            return manifest
        except (
            FileNotFoundError,
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
        ):
            return fallback

    def _read_manifest_projection(self, manifest_path: Path) -> dict[str, Any]:
        value, _size = self._read_manifest_projection_with_size(manifest_path)
        return value

    def _read_manifest_projection_with_size(
        self,
        manifest_path: Path,
        *,
        max_bytes: int = MAX_MANIFEST_FALLBACK_BYTES,
    ) -> tuple[dict[str, Any], int]:
        max_bytes = max(1, min(max_bytes, MAX_MANIFEST_FALLBACK_BYTES))
        manifest_fd = self._open_store_file(manifest_path)
        try:
            manifest_stat = self._checked_file_stat(manifest_fd)
            bytes_consumed = 0
            try:
                sidecar_value, _sidecar_stat, sidecar_size = (
                    self._secure_read_json(
                        manifest_path.with_name("list_summary.json"),
                        min(MAX_LIST_SUMMARY_BYTES, max_bytes),
                    )
                )
                bytes_consumed += sidecar_size
                manifest_after = self._checked_file_stat(manifest_fd)
                if (
                    self._stat_identity(manifest_stat)
                    != self._stat_identity(manifest_after)
                    or not isinstance(sidecar_value, Mapping)
                    or sidecar_value.get("schemaVersion")
                    != "qualification-run-list-summary/v1"
                    or sidecar_value.get("manifestSignature")
                    != [
                        manifest_stat.st_ino,
                        manifest_stat.st_mtime_ns,
                        manifest_stat.st_size,
                    ]
                    or not isinstance(sidecar_value.get("summary"), Mapping)
                ):
                    raise ValueError("stale summary")
                return dict(sidecar_value["summary"]), bytes_consumed
            except MonitorStoreReadError as exc:
                bytes_consumed += exc.bytes_read
            except (
                FileNotFoundError,
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                ValueError,
            ):
                pass

            # Old/test runs may predate the derived sidecar. Parse once and
            # immediately project; never deepcopy or recursively inspect it.
            remaining_bytes = max_bytes - bytes_consumed
            if remaining_bytes <= 0 or manifest_stat.st_size > remaining_bytes:
                raise MonitorStoreReadError(
                    "file_bytes_limit",
                    bytes_read=bytes_consumed,
                )
            data, final_stat = self._read_file_descriptor(
                manifest_fd,
                remaining_bytes,
                expected=manifest_stat,
            )
            bytes_consumed += len(data)
            try:
                value = json.loads(data.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise MonitorStoreReadError(
                    "invalid_json",
                    bytes_read=bytes_consumed,
                ) from exc
            if not isinstance(value, Mapping):
                raise MonitorStoreReadError(
                    "invalid_manifest",
                    bytes_read=bytes_consumed,
                )
            return dict(value), bytes_consumed
        finally:
            os.close(manifest_fd)

    def _v2_owned_run_path(
        self,
        qualification: str,
        manifest: Mapping[str, Any],
        field: str,
        *tail: str,
    ) -> Path:
        run_id = str(manifest.get("runId") or "")
        expected = self.run_store_root / qualification / run_id
        for part in tail:
            expected /= part
        try:
            expected_relative = expected.absolute().relative_to(
                self.repo_root
            ).as_posix()
        except ValueError as exc:
            raise ValueError("v2 run pathがrepository外です。") from exc
        if str(manifest.get(field) or "") != expected_relative:
            raise ValueError(f"v2 {field}がrun所有pathと一致しません。")
        return expected

    def _v2_plan_projection(
        self,
        qualification: str,
        manifest: Mapping[str, Any],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        try:
            path = self._v2_owned_run_path(
                qualification,
                manifest,
                "planPath",
                "plan.json",
            )
            file_stat = self._secure_file_stat(path)
        except (
            FileNotFoundError,
            OSError,
            ValueError,
            MonitorStoreReadError,
        ):
            return [], ["v2_plan_unavailable"]
        if file_stat.st_size > MAX_V2_PLAN_BYTES:
            return [], ["v2_plan_bytes_limit"]
        expected_plan_hash = str(manifest.get("planHash") or "")
        key = (
            qualification,
            str(manifest.get("runId") or ""),
            expected_plan_hash,
        )
        signature = self._stat_identity(file_stat)
        with self._v2_plan_cache_lock:
            cached = self._v2_plan_cache.get(key)
            if cached is not None and cached[0] == signature:
                self._v2_plan_cache.move_to_end(key)
                return cached[1], []
        try:
            value, verified_stat, _size = self._secure_read_json(
                path,
                MAX_V2_PLAN_BYTES,
            )
        except MonitorStoreReadError as exc:
            return [], [
                "v2_plan_bytes_limit"
                if exc.reason_code == "file_bytes_limit"
                else "v2_plan_unavailable"
            ]
        except (
            FileNotFoundError,
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
        ):
            return [], ["v2_plan_unavailable"]
        if (
            not isinstance(value, Mapping)
            or value.get("schemaVersion")
            != "question-maintenance-plan/v2"
            or value.get("planHash") != manifest.get("planHash")
        ):
            return [], ["v2_plan_identity_mismatch"]
        material = {
            str(field): item
            for field, item in value.items()
            if field != "planHash"
        }
        calculated = hashlib.sha256(
            json.dumps(
                material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if calculated != str(manifest.get("planHash") or ""):
            return [], ["v2_plan_hash_invalid"]
        plan = value.get("plan")
        executions = (
            plan.get("questionExecutions")
            if isinstance(plan, Mapping)
            else None
        )
        expected_count = self._nonnegative_int(
            manifest.get("questionStateCount")
        )
        if (
            expected_count is None
            or expected_count <= 0
            or not isinstance(executions, list)
            or len(executions) != expected_count
        ):
            return [], ["v2_plan_identity_mismatch"]
        projection: list[dict[str, Any]] = []
        seen_questions: set[str] = set()
        seen_work_items: set[str] = set()
        for execution in executions:
            if not isinstance(execution, Mapping):
                return [], ["v2_plan_identity_mismatch"]
            question_id = str(execution.get("questionId") or "")
            normalized_question_id = question_id.strip()
            stages = execution.get("stages")
            if (
                not normalized_question_id
                or len(question_id) > 1000
                or normalized_question_id in seen_questions
                or not isinstance(stages, list)
                or not stages
                or any(not isinstance(stage, Mapping) for stage in stages)
            ):
                return [], ["v2_plan_identity_mismatch"]
            compact_question = {
                field: execution[field]
                for field in (
                    "questionId",
                    "uiQuestionId",
                    "questionKey",
                    "reviewKey",
                    "sourceQuestionKey",
                    "sourceRecordRef",
                    "reviewQuestionId",
                    "listGroupId",
                )
                if field in execution
            }
            compact_stages: list[dict[str, Any]] = []
            seen_stage_ids: set[str] = set()
            for stage in stages:
                stage_id = str(stage.get("stageId") or "")
                work_item_key = str(stage.get("workItemKey") or "")
                if (
                    not stage_id
                    or len(stage_id) > 500
                    or stage_id in seen_stage_ids
                    or not work_item_key
                    or len(work_item_key) > 500
                    or work_item_key in seen_work_items
                ):
                    return [], ["v2_plan_identity_mismatch"]
                compact_stages.append(
                    {
                        field: stage[field]
                        for field in (
                            "stageId",
                            "stageCode",
                            "stageLabel",
                            "workItemKey",
                        )
                        if field in stage
                    }
                )
                seen_stage_ids.add(stage_id)
                seen_work_items.add(work_item_key)
            compact_question["stages"] = compact_stages
            projection.append(compact_question)
            seen_questions.add(normalized_question_id)
        verified_signature = self._stat_identity(verified_stat)
        projection_bytes = len(
            json.dumps(
                projection,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        with self._v2_plan_cache_lock:
            previous = self._v2_plan_cache.pop(key, None)
            if previous is not None:
                self._v2_plan_cache_bytes -= previous[2]
            if projection_bytes <= MAX_V2_PLAN_CACHE_BYTES:
                while self._v2_plan_cache and (
                    len(self._v2_plan_cache)
                    >= MAX_V2_PLAN_CACHE_ENTRIES
                    or self._v2_plan_cache_bytes + projection_bytes
                    > MAX_V2_PLAN_CACHE_BYTES
                ):
                    _evicted_key, evicted = (
                        self._v2_plan_cache.popitem(last=False)
                    )
                    self._v2_plan_cache_bytes -= evicted[2]
                self._v2_plan_cache[key] = (
                    verified_signature,
                    projection,
                    projection_bytes,
                )
                self._v2_plan_cache_bytes += projection_bytes
        return projection, []

    def _v2_question_summary(
        self,
        qualification: str,
        manifest: Mapping[str, Any],
    ) -> tuple[dict[str, Any], int, list[str]]:
        plan_projection, issues = self._v2_plan_projection(
            qualification,
            manifest,
        )
        if issues:
            return {}, 0, issues
        try:
            path = self._v2_owned_run_path(
                qualification,
                manifest,
                "questionSummaryPath",
                "question_summary.json",
            )
            value, _file_stat, size = self._secure_read_json(
                path,
                min(
                    MAX_LIST_SUMMARY_BYTES,
                    MAX_MANIFEST_COLLECTION_BYTES,
                ),
            )
        except MonitorStoreReadError as exc:
            return (
                {},
                min(exc.bytes_read, MAX_MANIFEST_COLLECTION_BYTES),
                [
                    *issues,
                    "v2_question_summary_bytes_limit"
                    if exc.reason_code == "file_bytes_limit"
                    else "v2_question_summary_unavailable"
                ],
            )
        except (
            FileNotFoundError,
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
        ):
            return {}, 0, [*issues, "v2_question_summary_unavailable"]
        if not isinstance(value, Mapping):
            return {}, size, [*issues, "v2_question_summary_schema_invalid"]
        questions = value.get("questions")
        question_count = self._nonnegative_int(value.get("questionCount"))
        manifest_count = self._nonnegative_int(
            manifest.get("questionStateCount")
        )
        if (
            value.get("schemaVersion") != _V2_SUMMARY_SCHEMA
            or not _SAFE_SHA256.fullmatch(
                str(manifest.get("planHash") or "")
            )
            or value.get("planHash") != manifest.get("planHash")
            or question_count is None
            or manifest_count is None
            or question_count != manifest_count
            or not isinstance(questions, list)
            or len(questions) != question_count
        ):
            return {}, size, [
                *issues,
                "v2_question_summary_identity_mismatch",
            ]
        plan_by_question_id = {
            str(question.get("questionId") or ""): question
            for question in plan_projection
        }
        if len(plan_by_question_id) != len(questions):
            return {}, size, ["v2_question_summary_identity_mismatch"]
        seen: set[str] = set()
        seen_work_items: set[str] = set()
        for question in questions:
            if not isinstance(question, Mapping):
                return {}, size, [
                    *issues,
                    "v2_question_summary_schema_invalid",
                ]
            question_id = str(question.get("questionId") or "")
            normalized_question_id = question_id.strip()
            stages = question.get("stages")
            planned_question = plan_by_question_id.get(question_id)
            if (
                not normalized_question_id
                or len(question_id) > 1000
                or normalized_question_id in seen
                or not isinstance(stages, list)
                or any(not isinstance(stage, Mapping) for stage in stages)
                or not isinstance(planned_question, Mapping)
                or any(
                    (
                        question.get(field) is not None
                        and (
                            not isinstance(question.get(field), str)
                            or len(str(question.get(field))) > 1000
                        )
                    )
                    for field in (
                        "questionKey",
                        "reviewQuestionId",
                        "sourceQuestionKey",
                        "sourceRecordRef",
                        "listGroupId",
                    )
                )
            ):
                return {}, size, [
                    *issues,
                    "v2_question_summary_schema_invalid",
                ]
            for field in (
                "questionId",
                "uiQuestionId",
                "questionKey",
                "reviewKey",
                "sourceQuestionKey",
                "sourceRecordRef",
                "reviewQuestionId",
                "listGroupId",
            ):
                if (field in question) != (field in planned_question) or (
                    field in planned_question
                    and question.get(field) != planned_question.get(field)
                ):
                    return {}, size, [
                        "v2_question_summary_identity_mismatch"
                    ]
            planned_stages = planned_question.get("stages")
            if (
                not isinstance(planned_stages, list)
                or len(planned_stages) != len(stages)
            ):
                return {}, size, [
                    "v2_question_summary_identity_mismatch"
                ]
            seen_stage_ids: set[str] = set()
            for stage, planned_stage in zip(
                stages,
                planned_stages,
                strict=True,
            ):
                if not isinstance(planned_stage, Mapping):
                    return {}, size, [
                        "v2_question_summary_identity_mismatch"
                    ]
                for field in (
                    "stageId",
                    "stageCode",
                    "stageLabel",
                    "workItemKey",
                ):
                    if (field in stage) != (
                        field in planned_stage
                    ) or (
                        field in planned_stage
                        and stage.get(field)
                        != planned_stage.get(field)
                    ):
                        return {}, size, [
                            "v2_question_summary_identity_mismatch"
                        ]
                work_item_key = str(stage.get("workItemKey") or "")
                stage_id = str(stage.get("stageId") or "")
                status = str(stage.get("status") or "")
                output_fingerprint = str(
                    stage.get("outputFingerprint") or ""
                )
                if (
                    not work_item_key
                    or len(work_item_key) > 500
                    or not stage_id
                    or len(stage_id) > 500
                    or not status
                    or len(status) > 100
                    or work_item_key in seen_work_items
                    or stage_id in seen_stage_ids
                ):
                    return {}, size, [
                        *issues,
                        "v2_question_summary_schema_invalid",
                    ]
                seen_work_items.add(work_item_key)
                seen_stage_ids.add(stage_id)
                if (
                    output_fingerprint
                    and not _SAFE_SHA256.fullmatch(output_fingerprint)
                ):
                    issues.append(
                        "v2_output_fingerprint_invalid"
                    )
                if (
                    not output_fingerprint
                    and status.casefold()
                    in {"validated", "succeeded", "completed"}
                ):
                    issues.append(
                        "v2_output_fingerprint_missing"
                    )
            seen.add(normalized_question_id)
        return dict(value), size, issues

    @staticmethod
    def _v2_status_priority(status: Any) -> int:
        normalized = str(status or "").casefold()
        if normalized in {
            "running",
            "active",
            "in_progress",
            "working",
            "started",
            "preparing",
            "prepared",
            "committing",
            "validating",
            "inprogress",
        }:
            return 0
        if normalized in {
            "blocked",
            "failed",
            "interrupted",
            "needs_rework",
            "partial",
        }:
            return 1
        if normalized in {
            "validated",
            "succeeded",
            "completed",
            "not_applicable",
        }:
            return 2
        return 3

    @classmethod
    def _v2_question_priority(
        cls,
        question: Mapping[str, Any],
    ) -> tuple[int, float, str]:
        stages = question.get("stages")
        stage_priorities = [
            cls._v2_status_priority(stage.get("status"))
            for stage in stages
            if isinstance(stage, Mapping)
        ] if isinstance(stages, list) else [3]
        order = question.get("displayOrder")
        resolved_order = (
            float(order)
            if isinstance(order, (int, float))
            and not isinstance(order, bool)
            and math.isfinite(float(order))
            else math.inf
        )
        return (
            min(stage_priorities, default=3),
            resolved_order,
            str(question.get("questionId") or ""),
        )

    def _v2_attempt_projections(
        self,
        qualification: str,
        manifest: Mapping[str, Any],
        summary: Mapping[str, Any],
        *,
        consumed_bytes: int,
        selected_question_ids: set[str] | None = None,
        include_active: bool,
        max_states: int,
        max_projections: int,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        questions = summary.get("questions")
        if not isinstance(questions, list):
            return [], ["v2_question_state_unavailable"]
        try:
            self._v2_owned_run_path(
                qualification,
                manifest,
                "questionStateDirectory",
                "questions",
            )
        except ValueError:
            return [], ["v2_question_state_identity_mismatch"]
        eligible_questions = []
        for question in questions:
            if not isinstance(question, Mapping):
                continue
            question_id = str(question.get("questionId") or "")
            if selected_question_ids is not None:
                if question_id not in selected_question_ids:
                    continue
            elif not self._v2_has_validated_stage(question):
                continue
            eligible_questions.append(dict(question))
        ordered = sorted(
            eligible_questions,
            key=self._v2_question_priority,
        )
        issues: list[str] = []
        max_states = max(1, min(max_states, MAX_V2_QUESTION_STATES))
        max_projections = max(
            1,
            min(max_projections, MAX_ARTIFACT_CHILDREN),
        )
        if len(ordered) > max_states:
            issues.append("v2_question_state_limit")
        projections: list[dict[str, Any]] = []
        for question in ordered[:max_states]:
            if len(projections) >= max_projections:
                issues.append("v2_attempt_projection_limit")
                break
            question_id = str(question.get("questionId") or "")
            filename = (
                hashlib.sha256(
                    question_id.strip().encode("utf-8")
                ).hexdigest()
                + ".json"
            )
            path = (
                self.run_store_root
                / qualification
                / str(manifest.get("runId") or "")
                / "questions"
                / filename
            )
            remaining = MAX_MANIFEST_COLLECTION_BYTES - consumed_bytes
            if remaining <= 0:
                issues.append("v2_question_state_bytes_limit")
                break
            try:
                value, _file_stat, size = self._secure_read_json(
                    path,
                    min(remaining, MAX_MANIFEST_FALLBACK_BYTES),
                )
            except MonitorStoreReadError as exc:
                consumed_bytes += min(exc.bytes_read, remaining)
                issues.append(
                    "v2_question_state_bytes_limit"
                    if exc.reason_code == "file_bytes_limit"
                    else "v2_question_state_unavailable"
                )
                if (
                    exc.reason_code == "file_bytes_limit"
                    or consumed_bytes >= MAX_MANIFEST_COLLECTION_BYTES
                ):
                    break
                continue
            except (
                FileNotFoundError,
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                ValueError,
            ):
                issues.append("v2_question_state_unavailable")
                continue
            consumed_bytes += size
            if not isinstance(value, Mapping):
                issues.append("v2_question_state_schema_invalid")
                continue
            state = dict(value)
            if (
                state.get("schemaVersion") != _V2_QUESTION_SCHEMA
                or state.get("planHash") != manifest.get("planHash")
                or str(state.get("questionId") or "") != question_id
            ):
                issues.append("v2_question_state_identity_mismatch")
                continue
            self_hash = str(state.get("selfHash") or "")
            hash_material = {
                str(key): value
                for key, value in state.items()
                if key != "selfHash"
            }
            calculated = hashlib.sha256(
                json.dumps(
                    hash_material,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if not self_hash or self_hash != calculated:
                issues.append("v2_question_state_hash_invalid")
                continue
            state_projections, state_issues, receipt_bytes = (
                self._v2_state_attempt_projections(
                    qualification,
                    manifest,
                    state,
                    expected_outputs=self._v2_expected_outputs(question),
                    expected_identity=self._v2_expected_identity(question),
                    expected_stages=self._v2_expected_stages(question),
                    include_active=include_active,
                    max_projections=max_projections - len(projections),
                    max_receipt_bytes=(
                        MAX_MANIFEST_COLLECTION_BYTES - consumed_bytes
                    ),
                )
            )
            consumed_bytes += receipt_bytes
            projections.extend(state_projections)
            issues.extend(state_issues)
        return projections, issues

    @staticmethod
    def _v2_has_validated_stage(
        question: Mapping[str, Any],
    ) -> bool:
        stages = question.get("stages")
        return bool(
            isinstance(stages, list)
            and any(
                isinstance(stage, Mapping)
                and str(stage.get("status") or "").casefold()
                in {"validated", "succeeded", "completed"}
                for stage in stages
            )
        )

    @staticmethod
    def _v2_artifact_fingerprint_complete(
        summary: Mapping[str, Any],
        validated_attempts: list[Mapping[str, Any]],
    ) -> bool:
        questions = summary.get("questions")
        if not isinstance(questions, list):
            return False
        expected = {
            (
                str(question.get("questionId") or ""),
                str(stage.get("stageId") or ""),
                str(stage.get("workItemKey") or ""),
            )
            for question in questions
            if isinstance(question, Mapping)
            for stage in (
                question.get("stages")
                if isinstance(question.get("stages"), list)
                else []
            )
            if isinstance(stage, Mapping)
            and str(stage.get("status") or "").casefold()
            in {"validated", "succeeded", "completed"}
        }
        actual = {
            (
                str(attempt.get("questionId") or ""),
                str(attempt.get("stageId") or ""),
                str(attempt.get("workItemKey") or ""),
            )
            for attempt in validated_attempts
            if attempt.get("receiptValidated") is True
        }
        return expected == actual

    @staticmethod
    def _v2_expected_outputs(
        question: Mapping[str, Any],
    ) -> dict[str, str]:
        outputs: dict[str, str] = {}
        stages = question.get("stages")
        if not isinstance(stages, list):
            return outputs
        for stage in stages:
            if (
                not isinstance(stage, Mapping)
                or str(stage.get("status") or "").casefold()
                not in {"validated", "succeeded", "completed"}
            ):
                continue
            work_item_key = str(stage.get("workItemKey") or "")
            output_fingerprint = str(
                stage.get("outputFingerprint") or ""
            )
            if (
                work_item_key
                and len(work_item_key) <= 500
            ):
                outputs[work_item_key] = output_fingerprint
        return outputs

    @staticmethod
    def _v2_expected_identity(
        question: Mapping[str, Any],
    ) -> dict[str, str]:
        return {
            field: str(question.get(field) or "")
            for field in (
                "questionId",
                "uiQuestionId",
                "questionKey",
                "reviewQuestionId",
                "sourceQuestionKey",
                "sourceRecordRef",
                "listGroupId",
            )
            if field == "questionId"
            or (
                isinstance(question.get(field), str)
                and str(question.get(field) or "")
            )
        }

    @staticmethod
    def _v2_expected_stages(
        question: Mapping[str, Any],
    ) -> dict[str, dict[str, str]]:
        stages = question.get("stages")
        if not isinstance(stages, list):
            return {}
        return {
            str(stage.get("stageId") or ""): {
                field: str(stage.get(field) or "")
                for field in (
                    "stageId",
                    "stageCode",
                    "stageLabel",
                    "workItemKey",
                )
            }
            for stage in stages
            if isinstance(stage, Mapping)
            and str(stage.get("stageId") or "")
        }

    def _v2_state_attempt_projections(
        self,
        qualification: str,
        manifest: Mapping[str, Any],
        state: Mapping[str, Any],
        *,
        expected_outputs: Mapping[str, str],
        expected_identity: Mapping[str, str],
        expected_stages: Mapping[str, Mapping[str, str]],
        include_active: bool,
        max_projections: int,
        max_receipt_bytes: int,
    ) -> tuple[list[dict[str, Any]], list[str], int]:
        execution = state.get("execution")
        attempts = state.get("attemptArtifacts")
        if not isinstance(execution, Mapping) or not isinstance(
            attempts, Mapping
        ):
            return [], ["v2_question_state_schema_invalid"], 0
        question_id = str(state.get("questionId") or "")
        if any(
            str(execution.get(field) or "") != expected
            for field, expected in expected_identity.items()
        ):
            return [], ["v2_question_state_identity_mismatch"], 0
        issues: list[str] = []
        stage_by_attempt: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        stages = execution.get("stages")
        if not isinstance(stages, list):
            return [], ["v2_question_state_schema_invalid"], 0
        state_stage_ids = [
            str(stage.get("stageId") or "")
            for stage in stages
            if isinstance(stage, Mapping)
        ]
        if (
            len(state_stage_ids) != len(stages)
            or len(state_stage_ids) != len(set(state_stage_ids))
        ):
            return [], ["v2_question_state_identity_mismatch"], 0

        def stage_matches_summary(stage: Mapping[str, Any]) -> bool:
            expected = expected_stages.get(
                str(stage.get("stageId") or "")
            )
            if not isinstance(expected, Mapping):
                return False
            for field in (
                "stageId",
                "stageCode",
                "stageLabel",
                "workItemKey",
            ):
                expected_value = str(expected.get(field) or "")
                if (
                    expected_value
                    and str(stage.get(field) or "") != expected_value
                ):
                    return False
            return True
        active_attempt_id = (
            str(state.get("activeAttemptId") or "")
            if include_active
            else ""
        )
        if active_attempt_id:
            active_attempt = attempts.get(active_attempt_id)
            active_stage_id = str(
                active_attempt.get("stageId") or ""
                if isinstance(active_attempt, Mapping)
                else ""
            )
            active_status = str(
                active_attempt.get("status") or ""
                if isinstance(active_attempt, Mapping)
                else ""
            ).casefold()
            matched_active = False
            for stage in stages:
                if (
                    isinstance(stage, Mapping)
                    and stage_matches_summary(stage)
                    and active_stage_id
                    and str(stage.get("stageId") or "") == active_stage_id
                    and str(stage.get("status") or "").casefold()
                    in {
                        "queued",
                        "running",
                        "preparing",
                        "prepared",
                        "committing",
                        "validating",
                        "inprogress",
                    }
                    and active_status
                    in {
                        "queued",
                        "running",
                        "preparing",
                        "prepared",
                        "committing",
                        "validating",
                        "inprogress",
                        "succeeded",
                        "failed",
                        "interrupted",
                    }
                ):
                    stage_by_attempt[active_attempt_id] = (
                        dict(stage),
                        {"status": str(stage.get("status") or active_status)},
                    )
                    matched_active = True
                    break
            if not matched_active:
                issues.append("v2_active_attempt_mismatch")
        for stage in stages:
            if not isinstance(stage, Mapping):
                continue
            if not stage_matches_summary(stage):
                issues.append("v2_attempt_stage_mismatch")
                continue
            stage_status = str(stage.get("status") or "").casefold()
            validations = stage.get("validationAttempts")
            if (
                include_active
                and stage_status
                not in {"validated", "succeeded", "completed"}
                and isinstance(validations, list)
            ):
                for validation in reversed(validations):
                    if not isinstance(validation, Mapping):
                        continue
                    validation_status = str(
                        validation.get("status") or ""
                    ).casefold()
                    if validation_status not in {
                        "blocked",
                        "failed",
                        "interrupted",
                        "rejected",
                        "declined",
                    }:
                        continue
                    attempt_id = str(
                        validation.get("childRunId")
                        or validation.get("attemptId")
                        or ""
                    )
                    if attempt_id and attempt_id not in stage_by_attempt:
                        if len(stage_by_attempt) >= max_projections:
                            issues.append("v2_attempt_projection_limit")
                            break
                        stage_by_attempt[attempt_id] = (
                            dict(stage),
                            dict(validation),
                        )
                    break
            if stage_status not in {"validated", "succeeded", "completed"}:
                continue
            work_item_key = str(stage.get("workItemKey") or "")
            summary_output = str(
                expected_outputs.get(work_item_key) or ""
            )
            state_output = str(stage.get("outputFingerprint") or "")
            if (
                work_item_key not in expected_outputs
                or (
                    summary_output
                    and (
                        not _SAFE_SHA256.fullmatch(summary_output)
                        or state_output != summary_output
                    )
                )
                or (
                    not summary_output
                    and state_output
                    and not _SAFE_SHA256.fullmatch(state_output)
                )
            ):
                issues.append("v2_attempt_output_mismatch")
                continue
            if not isinstance(validations, list):
                issues.append("v2_attempt_unavailable")
                continue
            validated_attempt_found = False
            for validation in reversed(validations):
                if (
                    not isinstance(validation, Mapping)
                    or str(validation.get("status") or "").casefold()
                    != "validated"
                ):
                    continue
                attempt_id = str(validation.get("childRunId") or "")
                if attempt_id:
                    validated_attempt_found = True
                    if (
                        len(stage_by_attempt) >= max_projections
                        and attempt_id not in stage_by_attempt
                    ):
                        issues.append("v2_attempt_projection_limit")
                        break
                    stage_by_attempt[attempt_id] = (
                        dict(stage),
                        dict(validation),
                    )
                    break
            if not validated_attempt_found:
                issues.append("v2_attempt_unavailable")
        projections: list[dict[str, Any]] = []
        receipt_bytes = 0
        parent_run_id = str(manifest.get("runId") or "")
        question_hash = hashlib.sha256(
            question_id.strip().encode("utf-8")
        ).hexdigest()
        prefix = f"qa-{parent_run_id}-{question_hash}-"
        for attempt_id, (stage, validation) in stage_by_attempt.items():
            if len(projections) >= max_projections:
                issues.append("v2_attempt_projection_limit")
                break
            anchored_stage = expected_stages.get(
                str(stage.get("stageId") or ""),
                {},
            )
            attempt = attempts.get(attempt_id)
            if not isinstance(attempt, Mapping):
                issues.append("v2_attempt_unavailable")
                continue
            validation_status = str(validation.get("status") or "")
            token = (
                attempt_id[len(prefix) :]
                if attempt_id.startswith(prefix)
                else ""
            )
            expected_directory = (
                self.run_store_root
                / qualification
                / parent_run_id
                / "attempts"
                / token
            )
            try:
                expected_directory_relative = (
                    expected_directory.absolute()
                    .relative_to(self.repo_root)
                    .as_posix()
                )
            except ValueError:
                expected_directory_relative = ""
            expected_result = f"{expected_directory_relative}/result.json"
            expected_progress = (
                f"{expected_directory_relative}/progress.jsonl"
            )
            if (
                not _SAFE_ID.fullmatch(attempt_id)
                or not _SAFE_ATTEMPT_TOKEN.fullmatch(token)
                or str(attempt.get("attemptId") or "") != attempt_id
                or str(attempt.get("parentRunId") or "") != parent_run_id
                or str(attempt.get("questionId") or "") != question_id
                or str(attempt.get("artifactDirectory") or "")
                != expected_directory_relative
                or str(attempt.get("resultReceiptPath") or "")
                != expected_result
                or str(attempt.get("progressReceiptPath") or "")
                != expected_progress
                or not str(stage.get("stageId") or "")
                or str(attempt.get("stageId") or "")
                != str(stage.get("stageId") or "")
            ):
                issues.append("v2_attempt_identity_mismatch")
                continue
            result = attempt.get("result")
            result = result if isinstance(result, Mapping) else {}
            validated = validation_status.casefold() == "validated"
            if validated and (
                str(attempt.get("status") or "").casefold() != "succeeded"
                or attempt.get("receiptValidated") is not True
                or not str(stage.get("stageId") or "")
                or str(attempt.get("stageId") or "")
                != str(stage.get("stageId") or "")
                or result.get("status") != "succeeded"
            ):
                issues.append("v2_attempt_receipt_invalid")
                continue
            if validated:
                remaining_receipt_bytes = (
                    max_receipt_bytes - receipt_bytes
                )
                if remaining_receipt_bytes <= 0:
                    issues.append("v2_attempt_receipt_bytes_limit")
                    break
                try:
                    receipt, _receipt_stat, receipt_size = (
                        self._secure_read_json(
                            expected_directory / "result.json",
                            min(
                                remaining_receipt_bytes,
                                MAX_MANIFEST_FALLBACK_BYTES,
                            ),
                        )
                    )
                except MonitorStoreReadError as exc:
                    receipt_bytes += min(
                        exc.bytes_read,
                        remaining_receipt_bytes,
                    )
                    issues.append(
                        "v2_attempt_receipt_bytes_limit"
                        if exc.reason_code == "file_bytes_limit"
                        else "v2_attempt_receipt_unavailable"
                    )
                    if exc.reason_code == "file_bytes_limit":
                        break
                    continue
                except (
                    FileNotFoundError,
                    OSError,
                    UnicodeError,
                    json.JSONDecodeError,
                    ValueError,
                ):
                    issues.append("v2_attempt_receipt_unavailable")
                    continue
                receipt_bytes += receipt_size
                if (
                    not isinstance(receipt, Mapping)
                    or dict(receipt) != dict(result)
                ):
                    issues.append("v2_attempt_receipt_mismatch")
                    continue
            safe_result: dict[str, Any] = {}
            batch_results: list[dict[str, Any]] = []
            if validated:
                raw_batch_results = attempt.get("batchQuestionResults")
                matches = (
                    [
                        value
                        for value in raw_batch_results
                        if isinstance(value, Mapping)
                        and str(value.get("questionId") or "")
                        == question_id
                    ]
                    if isinstance(raw_batch_results, list)
                    else []
                )
                batch_result = matches[0] if len(matches) == 1 else None
                result_files = result.get("changedFiles")
                batch_files = (
                    batch_result.get("changedFiles")
                    if isinstance(batch_result, Mapping)
                    else None
                )
                output_fingerprint = hashlib.sha256(
                    json.dumps(
                        dict(batch_result or {}),
                        ensure_ascii=False,
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()
                if (
                    not isinstance(raw_batch_results, list)
                    or len(raw_batch_results) != 1
                    or not isinstance(batch_result, Mapping)
                    or batch_result.get("status") != "succeeded"
                    or not isinstance(result_files, list)
                    or not isinstance(batch_files, list)
                    or any(
                        not isinstance(value, str)
                        for value in [*result_files, *batch_files]
                    )
                    or result_files != batch_files
                    or (
                        str(
                            expected_outputs.get(
                                str(stage.get("workItemKey") or "")
                            )
                            or stage.get("outputFingerprint")
                            or ""
                        )
                        and output_fingerprint
                        != str(
                            expected_outputs.get(
                                str(stage.get("workItemKey") or "")
                            )
                            or stage.get("outputFingerprint")
                            or ""
                        )
                    )
                ):
                    issues.append(
                        "v2_attempt_result_attribution_mismatch"
                    )
                    continue
                safe_result = {
                    "status": "succeeded",
                    "changedFiles": list(result_files),
                }
                batch_results = [
                    {
                        "questionId": question_id,
                        "workItemKey": anchored_stage.get(
                            "workItemKey"
                        ),
                        "status": "succeeded",
                        "changedFiles": list(batch_files),
                    }
                ]
            aliases = [
                value
                for value in (
                    question_id,
                    expected_identity.get("uiQuestionId"),
                    expected_identity.get("reviewQuestionId"),
                    expected_identity.get("sourceQuestionKey"),
                    expected_identity.get("sourceRecordRef"),
                )
                if isinstance(value, (str, int)) and str(value)
            ]
            binding = {
                key: value
                for key, value in {
                    "id": question_id,
                    "uiQuestionId": expected_identity.get("uiQuestionId")
                    or question_id,
                    "reviewQuestionId": expected_identity.get(
                        "reviewQuestionId"
                    ),
                    "sourceQuestionKey": expected_identity.get(
                        "sourceQuestionKey"
                    ),
                    "sourceRecordRef": expected_identity.get(
                        "sourceRecordRef"
                    ),
                    "listGroupId": expected_identity.get("listGroupId"),
                    "aliases": aliases,
                }.items()
                if value not in (None, "", [])
            }
            projection: dict[str, Any] = {
                "runId": attempt_id,
                "parentRunId": parent_run_id,
                "qualification": qualification,
                "questionId": question_id,
                "workItemKey": anchored_stage.get("workItemKey"),
                "stageId": anchored_stage.get("stageId")
                or attempt.get("stageId"),
                "stageCode": anchored_stage.get("stageCode"),
                "stageLabel": anchored_stage.get("stageLabel"),
                "status": validation_status
                or attempt.get("status")
                or stage.get("status"),
                "startedAt": validation.get("startedAt")
                or attempt.get("startedAt"),
                "finishedAt": validation.get("finishedAt")
                or attempt.get("finishedAt"),
                "threadId": attempt.get("threadId"),
                "turnId": attempt.get("turnId"),
                "sessionId": attempt.get("sessionId"),
                "listGroupId": expected_identity.get("listGroupId"),
                "targetRecordBindings": [binding],
                "progressTargets": [binding],
                "receiptValidated": validated,
                "artifactSync": {
                    "status": str(
                        (
                            attempt.get("artifactSync")
                            if isinstance(
                                attempt.get("artifactSync"),
                                Mapping,
                            )
                            else {}
                        ).get("status")
                        or "unknown"
                    )
                },
                "result": safe_result,
            }
            if batch_results:
                projection["batchQuestionResults"] = batch_results
            projections.append(projection)
        return projections, issues, receipt_bytes

    def _v2_lanes(
        self,
        summary: Mapping[str, Any],
        attempts: list[Mapping[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        questions = summary.get("questions")
        if not isinstance(questions, list):
            return [], ["v2_lane_unavailable"]
        attempt_by_work_item: dict[str, Mapping[str, Any]] = {}
        for attempt in attempts:
            work_item_key = str(attempt.get("workItemKey") or "")
            if not work_item_key:
                continue
            current = attempt_by_work_item.get(work_item_key)
            if current is None or self._v2_status_priority(
                attempt.get("status")
            ) < self._v2_status_priority(current.get("status")):
                attempt_by_work_item[work_item_key] = attempt
        ranked: list[tuple[tuple[int, float, str], dict[str, Any]]] = []
        for question in questions:
            if not isinstance(question, Mapping):
                continue
            order = question.get("displayOrder")
            resolved_order = (
                float(order)
                if isinstance(order, (int, float))
                and not isinstance(order, bool)
                and math.isfinite(float(order))
                else math.inf
            )
            for stage in question.get("stages") or []:
                if not isinstance(stage, Mapping):
                    continue
                work_item_key = str(stage.get("workItemKey") or "")
                lane: dict[str, Any] = {
                    "questionId": question.get("questionId"),
                    "workItemKey": work_item_key,
                    "listGroupId": question.get("listGroupId"),
                    "stageId": stage.get("stageId"),
                    "stageCode": stage.get("stageCode"),
                    "stageLabel": stage.get("stageLabel"),
                    "status": stage.get("status"),
                    "startedAt": stage.get("startedAt"),
                    "finishedAt": stage.get("finishedAt"),
                }
                attempt = attempt_by_work_item.get(work_item_key)
                if attempt is not None:
                    terminal_stage = str(
                        stage.get("status") or ""
                    ).casefold() in {
                        "validated",
                        "succeeded",
                        "completed",
                        "not_applicable",
                        "blocked",
                        "failed",
                        "interrupted",
                        "needs_rework",
                        "partial",
                    }
                    lane.update(
                        runId=attempt.get("runId"),
                        parentRunId=attempt.get("parentRunId"),
                        childRunId=attempt.get("runId"),
                        threadId=attempt.get("threadId"),
                        turnId=attempt.get("turnId"),
                        sessionId=attempt.get("sessionId"),
                        startedAt=attempt.get("startedAt")
                        or lane.get("startedAt"),
                        finishedAt=(
                            attempt.get("finishedAt")
                            or lane.get("finishedAt")
                            if terminal_stage
                            else lane.get("finishedAt")
                        ),
                    )
                ranked.append(
                    (
                        (
                            self._v2_status_priority(stage.get("status")),
                            resolved_order,
                            work_item_key,
                        ),
                        self._lane_summary(lane),
                    )
                )
        ranked.sort(key=lambda value: value[0])
        issues = (
            ["v2_lane_limit"]
            if len(ranked) > MAX_SNAPSHOT_CHILDREN
            else []
        )
        return (
            [lane for _rank, lane in ranked[:MAX_SNAPSHOT_CHILDREN]],
            issues,
        )

    def _child_manifests(
        self,
        qualification: str,
        manifest: Mapping[str, Any],
        *,
        full: bool = False,
        max_children: int | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        values = manifest.get("childRunIds")
        if not isinstance(values, list):
            return (
                [],
                ["child_manifest_schema_invalid"]
                if "childRunIds" in manifest
                and manifest.get("childRunIds") is not None
                else [],
            )
        max_children = max_children or (
            MAX_ARTIFACT_CHILDREN if full else MAX_SNAPSHOT_CHILDREN
        )
        issues: list[str] = []
        if len(values) > max_children:
            issues.append("child_manifest_limit")
        children: list[dict[str, Any]] = []
        seen: set[str] = set()
        consumed_bytes = 0
        parent_run_id = str(manifest.get("runId") or "")
        selected_values = values[-max_children:]
        for value in reversed(selected_values):
            child_id = value if isinstance(value, str) else ""
            if not child_id or not _SAFE_ID.fullmatch(child_id):
                issues.append("child_manifest_id_invalid")
                continue
            if child_id in seen:
                continue
            seen.add(child_id)
            path = (
                Path(self.run_store.root)
                / qualification
                / child_id
                / "manifest.json"
            )
            try:
                remaining_bytes = (
                    MAX_MANIFEST_COLLECTION_BYTES - consumed_bytes
                )
                if remaining_bytes <= 0:
                    issues.append("child_manifest_bytes_limit")
                    break
                child, manifest_bytes = (
                    self._read_manifest_full_with_size(
                        path,
                        max_bytes=remaining_bytes,
                    )
                    if full
                    else self._read_manifest_projection_with_size(
                        path,
                        max_bytes=remaining_bytes,
                    )
                )
            except MonitorStoreReadError as exc:
                consumed_bytes += min(
                    exc.bytes_read,
                    MAX_MANIFEST_COLLECTION_BYTES - consumed_bytes,
                )
                issues.append(
                    "child_manifest_bytes_limit"
                    if exc.reason_code == "file_bytes_limit"
                    else "child_manifest_unavailable"
                )
                if consumed_bytes >= MAX_MANIFEST_COLLECTION_BYTES:
                    issues.append("child_manifest_bytes_limit")
                    break
                continue
            except (
                FileNotFoundError,
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                ValueError,
            ):
                issues.append("child_manifest_unavailable")
                continue
            if consumed_bytes + manifest_bytes > MAX_MANIFEST_COLLECTION_BYTES:
                issues.append("child_manifest_bytes_limit")
                break
            consumed_bytes += manifest_bytes
            try:
                self._validate_manifest_identity(
                    child,
                    qualification,
                    child_id,
                    parent_run_id=parent_run_id,
                )
            except ValueError:
                issues.append("child_manifest_identity_mismatch")
                continue
            children.append(child)
        children.reverse()
        return children, issues

    def _read_manifest_full(self, manifest_path: Path) -> dict[str, Any]:
        value, _size = self._read_manifest_full_with_size(manifest_path)
        return value

    def _read_manifest_full_with_size(
        self,
        manifest_path: Path,
        *,
        max_bytes: int = MAX_MANIFEST_FALLBACK_BYTES,
    ) -> tuple[dict[str, Any], int]:
        max_bytes = max(1, min(max_bytes, MAX_MANIFEST_FALLBACK_BYTES))
        value, file_stat, size = self._secure_read_json(
            manifest_path,
            max_bytes,
        )
        if not isinstance(value, Mapping):
            raise MonitorStoreReadError(
                "invalid_manifest",
                bytes_read=size,
            )
        return dict(value), min(size, file_stat.st_size)

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
            self._validate_manifest_identity(
                parent,
                qualification,
                parent_id,
            )
        except (
            FileNotFoundError,
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
        ):
            return None
        child_ids = parent.get("childRunIds")
        if not isinstance(child_ids, list) or str(manifest.get("runId") or "") not in {
            str(value) for value in child_ids
        }:
            return None
        return parent

    def _existing_dashboard_index(
        self, qualification: str
    ) -> tuple[list[dict[str, Any]] | None, bool, int]:
        path = self.run_store_root / qualification / "dashboard_runs.json"
        try:
            value, _file_stat, size = self._secure_read_json(
                path,
                MAX_DASHBOARD_INDEX_BYTES,
            )
        except MonitorStoreReadError as exc:
            return None, exc.reason_code == "file_bytes_limit", exc.bytes_read
        except (
            FileNotFoundError,
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
        ):
            return None, False, 0
        if (
            not isinstance(value, Mapping)
            or value.get("schemaVersion")
            != "qualification-dashboard-run-index/v1"
            or value.get("qualification") != qualification
            or value.get("complete") is not True
            or not isinstance(value.get("runs"), list)
        ):
            return None, False, size
        runs: list[dict[str, Any]] = []
        consumed_bytes = size
        for item in value["runs"][:500]:
            if not isinstance(item, Mapping):
                return None, True, consumed_bytes
            run_id = str(item.get("runId") or "")
            try:
                if not _SAFE_ID.fullmatch(run_id):
                    raise ValueError("dashboard runIdが不正です。")
                remaining_bytes = (
                    MAX_MANIFEST_COLLECTION_BYTES - consumed_bytes
                )
                if remaining_bytes <= 0:
                    return None, True, consumed_bytes
                manifest, manifest_bytes = (
                    self._read_manifest_projection_with_size(
                        self.run_store_root
                        / qualification
                        / run_id
                        / "manifest.json",
                        max_bytes=remaining_bytes,
                    )
                )
                consumed_bytes += manifest_bytes
                self._validate_manifest_identity(
                    manifest,
                    qualification,
                    run_id,
                )
                if (
                    manifest.get("parentRunId")
                    or manifest.get("workType")
                    in {"evaluation", "reevaluation"}
                    or manifest.get("schemaVersion")
                    == "failed-delta-reconciliation/v1"
                ):
                    raise ValueError("dashboard itemがtop-level runではありません。")
            except MonitorStoreReadError as exc:
                consumed_bytes += min(
                    exc.bytes_read,
                    MAX_MANIFEST_COLLECTION_BYTES - consumed_bytes,
                )
                return None, True, consumed_bytes
            except (
                FileNotFoundError,
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                ValueError,
            ):
                return None, True, consumed_bytes
            runs.append(manifest)
        return (
            runs,
            len(value["runs"]) > 500 or len(value["runs"]) >= 32,
            consumed_bytes,
        )

    def _read_only_dashboard_runs(
        self,
        qualification: str,
        *,
        limit: int,
        max_bytes: int = MAX_MANIFEST_COLLECTION_BYTES,
    ) -> tuple[list[dict[str, Any]], bool]:
        max_bytes = max(1, min(max_bytes, MAX_MANIFEST_COLLECTION_BYTES))
        directory = self.run_store_root / qualification
        candidate_limit = min(
            MAX_DASHBOARD_SCAN_ENTRIES,
            max(128, limit * 4),
        )
        truncated = False
        candidates: list[tuple[int, str, Path]] = []
        try:
            entries = os.scandir(directory)
        except OSError:
            return [], False
        with entries:
            for index, entry in enumerate(entries):
                if index >= MAX_DASHBOARD_SCAN_ENTRIES:
                    truncated = True
                    break
                if (
                    not _SAFE_ID.fullmatch(entry.name)
                    or not entry.is_dir(follow_symlinks=False)
                ):
                    continue
                manifest_path = Path(entry.path) / "manifest.json"
                try:
                    manifest_stat = self._secure_file_stat(manifest_path)
                except (FileNotFoundError, OSError, ValueError):
                    continue
                candidate = (
                    manifest_stat.st_mtime_ns,
                    entry.name,
                    manifest_path,
                )
                if len(candidates) < candidate_limit:
                    heapq.heappush(candidates, candidate)
                else:
                    truncated = True
                    if candidate[:2] > candidates[0][:2]:
                        heapq.heapreplace(candidates, candidate)
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)

        runs: list[dict[str, Any]] = []
        consumed_bytes = 0
        for _modified, expected_run_id, manifest_path in candidates:
            if len(runs) >= limit:
                truncated = True
                break
            try:
                remaining_bytes = max_bytes - consumed_bytes
                if remaining_bytes <= 0:
                    truncated = True
                    break
                run, manifest_bytes = self._read_manifest_projection_with_size(
                    manifest_path,
                    max_bytes=remaining_bytes,
                )
                consumed_bytes += manifest_bytes
                self._validate_manifest_identity(
                    run,
                    qualification,
                    expected_run_id,
                )
            except MonitorStoreReadError as exc:
                consumed_bytes += min(
                    exc.bytes_read,
                    max_bytes - consumed_bytes,
                )
                if (
                    remaining_bytes < MAX_MANIFEST_FALLBACK_BYTES
                    and exc.reason_code == "file_bytes_limit"
                ) or consumed_bytes >= max_bytes:
                    truncated = True
                    break
                if exc.reason_code == "file_bytes_limit":
                    truncated = True
                continue
            except (
                FileNotFoundError,
                OSError,
                ValueError,
                UnicodeError,
                json.JSONDecodeError,
            ):
                continue
            if (
                run.get("parentRunId")
                or str(run.get("qualification") or "") != qualification
                or not run.get("runId")
                or run.get("workType") in {"evaluation", "reevaluation"}
                or run.get("schemaVersion")
                == "failed-delta-reconciliation/v1"
            ):
                continue
            runs.append(run)
        runs.sort(
            key=lambda run: (
                str(run.get("updatedAt") or run.get("createdAt") or ""),
                str(run.get("runId") or ""),
            ),
            reverse=True,
        )
        return runs[:limit], truncated or len(runs) > limit

    def _open_store_file(self, path: Path) -> int:
        # Keep the caller's path lexical so an interior symlink reaches the
        # O_NOFOLLOW walk below. The store root itself may have a platform
        # alias such as macOS /var -> /private/var, so accept either spelling
        # only for that trusted root prefix.
        absolute_path = path.absolute()
        relative: Path | None = None
        for root in (
            self.run_store_root,
            self._run_store_lexical_root,
        ):
            try:
                relative = absolute_path.relative_to(root)
                break
            except ValueError:
                continue
        if relative is None:
            raise FileNotFoundError("run store外のpathです。")
        if (
            not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise FileNotFoundError("run store内pathが不正です。")

        read_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        directory_flags = (
            read_flags
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        file_flags = read_flags | getattr(os, "O_NOFOLLOW", 0)
        directories: list[int] = []
        try:
            descriptor = os.open(self.run_store_root, directory_flags)
            directories.append(descriptor)
            for part in relative.parts[:-1]:
                descriptor = os.open(
                    part,
                    directory_flags,
                    dir_fd=descriptor,
                )
                directories.append(descriptor)
            return os.open(
                relative.parts[-1],
                file_flags,
                dir_fd=descriptor,
            )
        except OSError as exc:
            raise FileNotFoundError(f"fileが見つかりません: {path.name}") from exc
        finally:
            for descriptor in reversed(directories):
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    @staticmethod
    def _stat_identity(value: os.stat_result) -> tuple[int, ...]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_nlink,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )

    @staticmethod
    def _checked_file_stat(
        descriptor: int,
        max_bytes: int | None = None,
    ) -> os.stat_result:
        value = os.fstat(descriptor)
        if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
            raise MonitorStoreReadError("unsafe_file")
        if max_bytes is not None and value.st_size > max_bytes:
            raise MonitorStoreReadError("file_bytes_limit")
        return value

    def _secure_file_stat(self, path: Path) -> os.stat_result:
        descriptor = self._open_store_file(path)
        try:
            return self._checked_file_stat(descriptor)
        finally:
            os.close(descriptor)

    def _read_file_descriptor(
        self,
        descriptor: int,
        max_bytes: int,
        *,
        expected: os.stat_result | None = None,
    ) -> tuple[bytes, os.stat_result]:
        before = self._checked_file_stat(descriptor, max_bytes)
        if (
            expected is not None
            and self._stat_identity(expected) != self._stat_identity(before)
        ):
            raise MonitorStoreReadError("file_changed_during_read")
        os.lseek(descriptor, 0, os.SEEK_SET)
        remaining = max_bytes + 1
        chunks: list[bytes] = []
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = self._checked_file_stat(descriptor, max_bytes)
        if (
            len(data) > max_bytes
            or len(data) != after.st_size
            or self._stat_identity(before) != self._stat_identity(after)
        ):
            raise MonitorStoreReadError("file_changed_during_read")
        return data, after

    def _secure_read_json(
        self,
        path: Path,
        max_bytes: int,
    ) -> tuple[Any, os.stat_result, int]:
        descriptor = self._open_store_file(path)
        try:
            data, file_stat = self._read_file_descriptor(
                descriptor,
                max_bytes,
            )
        finally:
            os.close(descriptor)
        try:
            value = json.loads(data.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise MonitorStoreReadError(
                "invalid_json",
                bytes_read=len(data),
            ) from exc
        return value, file_stat, len(data)

    @staticmethod
    def _validate_manifest_identity(
        manifest: Mapping[str, Any],
        qualification: str,
        run_id: str,
        *,
        parent_run_id: str | None = None,
    ) -> None:
        if (
            not run_id
            or str(manifest.get("runId") or "") != run_id
            or str(manifest.get("qualification") or "") != qualification
        ):
            raise ValueError("manifest identityがpathと一致しません。")
        if (
            parent_run_id is not None
            and str(manifest.get("parentRunId") or "") != parent_run_id
        ):
            raise ValueError("child manifestのparentRunIdが一致しません。")

    def _artifact_fingerprint(
        self,
        qualification: str,
        manifest: Mapping[str, Any],
        children: list[Mapping[str, Any]],
        *,
        parent: Mapping[str, Any] | None,
    ) -> str:
        declarations, declarations_truncated = self._artifact_declarations(
            qualification,
            manifest,
            children,
            parent=parent,
        )
        public_declarations: list[dict[str, Any]] = []
        file_signatures: dict[str, list[Any]] = {}
        for declaration in declarations:
            relative = str(declaration.get("path") or "")
            if relative not in file_signatures:
                file_signatures[relative] = self._artifact_file_signature(
                    qualification,
                    relative,
                )
            public_declarations.append(
                {
                    "path": relative,
                    "identity": declaration.get("identity"),
                    "receiptValidation": declaration.get(
                        "receiptValidation"
                    ),
                    "artifactSync": declaration.get("artifactSync"),
                    "preRejected": declaration.get("_preRejected"),
                    "fileSignature": file_signatures[relative],
                }
            )
        material = {
            "declarations": public_declarations,
            "declarationsTruncated": declarations_truncated,
            "states": [
                self._run_artifact_fingerprint_state(current)
                for current in [
                    manifest,
                    *children,
                    *([parent] if parent is not None else []),
                ]
            ],
        }
        encoded = json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _v2_summary_artifact_fingerprint(
        manifest: Mapping[str, Any],
        summary: Mapping[str, Any],
    ) -> str:
        outputs: list[dict[str, str]] = []
        questions = summary.get("questions")
        if isinstance(questions, list):
            for question in questions:
                if not isinstance(question, Mapping):
                    continue
                question_id = str(question.get("questionId") or "")
                stages = question.get("stages")
                if not isinstance(stages, list):
                    continue
                for stage in stages:
                    if not isinstance(stage, Mapping):
                        continue
                    output_fingerprint = str(
                        stage.get("outputFingerprint") or ""
                    )
                    status = str(stage.get("status") or "").casefold()
                    if (
                        not output_fingerprint
                        and status
                        not in {"validated", "succeeded", "completed"}
                    ):
                        continue
                    output = {
                        "questionId": question_id,
                        "workItemKey": str(
                            stage.get("workItemKey") or ""
                        ),
                        "stageId": str(stage.get("stageId") or ""),
                        "outputFingerprint": output_fingerprint,
                    }
                    if not output_fingerprint:
                        output["status"] = status
                    outputs.append(output)
        outputs.sort(
            key=lambda value: (
                value["questionId"],
                value["stageId"],
                value["workItemKey"],
                value["outputFingerprint"],
            )
        )
        sync = manifest.get("artifactSync")
        material = {
            "runId": str(manifest.get("runId") or ""),
            "planHash": str(manifest.get("planHash") or ""),
            "artifactSyncStatus": (
                str(sync.get("status") or "")
                if isinstance(sync, Mapping)
                else ""
            ),
            "outputs": outputs,
        }
        return hashlib.sha256(
            json.dumps(
                material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def _v2_combined_artifact_fingerprint(
        self,
        qualification: str,
        manifest: Mapping[str, Any],
        summary: Mapping[str, Any],
        validated_attempts: list[Mapping[str, Any]],
        *,
        parent: Mapping[str, Any] | None,
    ) -> str:
        material = {
            "summary": self._v2_summary_artifact_fingerprint(
                manifest,
                summary,
            ),
            "selectedArtifacts": self._artifact_fingerprint(
                qualification,
                manifest,
                validated_attempts,
                parent=parent,
            ),
        }
        return hashlib.sha256(
            json.dumps(
                material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @classmethod
    def _run_artifact_fingerprint_state(
        cls,
        manifest: Mapping[str, Any],
    ) -> dict[str, Any]:
        result = manifest.get("result")
        result = result if isinstance(result, Mapping) else {}
        batch_results = manifest.get("batchQuestionResults")
        public_batch_results: list[dict[str, Any]] = []
        if isinstance(batch_results, list):
            for item in batch_results[:MAX_ARTIFACT_DECLARATIONS]:
                if not isinstance(item, Mapping):
                    continue
                public_batch_results.append(
                    {
                        key: item.get(key)
                        for key in (
                            "questionId",
                            "workItemKey",
                            "status",
                            "changedFiles",
                            "artifactHash",
                            "hash",
                            "revision",
                        )
                        if isinstance(
                            item.get(key),
                            (str, int, float, list),
                        )
                        and not isinstance(item.get(key), bool)
                    }
                )
        return {
            "runId": str(manifest.get("runId") or ""),
            "receiptValidated": manifest.get("receiptValidated") is True,
            "artifactSyncStatus": (
                str(manifest["artifactSync"].get("status") or "")
                if isinstance(manifest.get("artifactSync"), Mapping)
                else ""
            ),
            "changedFiles": (
                manifest.get("changedFiles")
                if isinstance(manifest.get("changedFiles"), list)
                else []
            ),
            "result": {
                key: result.get(key)
                for key in (
                    "status",
                    "changedFiles",
                    "artifactHash",
                    "hash",
                    "revision",
                )
                if isinstance(result.get(key), (str, int, float, list))
                and not isinstance(result.get(key), bool)
            },
            "batchQuestionResults": public_batch_results,
            "batchQuestionResultsValid": isinstance(batch_results, list),
        }

    def _artifact_file_signature(
        self,
        qualification: str,
        relative: str,
    ) -> list[Any]:
        pure = PurePosixPath(relative)
        if (
            pure.is_absolute()
            or not pure.parts
            or "." in pure.parts
            or ".." in pure.parts
            or "\\" in relative
            or pure.suffix.lower() not in _ALLOWED_ARTIFACT_SUFFIXES
            or "question_review_console" in pure.parts
        ):
            return ["rejected"]
        allowed_root = PurePosixPath("output", qualification)
        if not (pure == allowed_root or pure.is_relative_to(allowed_root)):
            return ["rejected"]

        read_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        directory_flags = (
            read_flags
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        file_flags = read_flags | getattr(os, "O_NOFOLLOW", 0)
        opened: list[int] = []
        try:
            descriptor = os.open(self.repo_root, directory_flags)
            opened.append(descriptor)
            for part in pure.parts[:-1]:
                descriptor = os.open(
                    part,
                    directory_flags,
                    dir_fd=descriptor,
                )
                opened.append(descriptor)
            file_descriptor = os.open(
                pure.parts[-1],
                file_flags,
                dir_fd=descriptor,
            )
            opened.append(file_descriptor)
            file_stat = os.fstat(file_descriptor)
            if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
                return ["rejected"]
            return [
                file_stat.st_dev,
                file_stat.st_ino,
                file_stat.st_size,
                file_stat.st_mtime_ns,
                file_stat.st_ctime_ns,
            ]
        except OSError:
            return ["unavailable"]
        finally:
            for descriptor in reversed(opened):
                try:
                    os.close(descriptor)
                except OSError:
                    pass

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
            elif isinstance(value, bool):
                summary[key] = value
            elif (
                isinstance(value, (int, float))
                and math.isfinite(float(value))
            ):
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
            "stageId",
            "listGroupId",
            "listGroupIds",
            "targetGroupIds",
            "questionId",
            "workItemKey",
            "threadId",
            "turnId",
            "sessionId",
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
            elif isinstance(value, bool):
                lane[key] = value
            elif (
                isinstance(value, (int, float))
                and math.isfinite(float(value))
            ):
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
            "eventCount",
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

    @staticmethod
    def _compact_json_size(value: Any) -> int:
        return len(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )

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
            "sessionId",
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
    ) -> tuple[list[dict[str, Any]], bool]:
        state_parent = (
            parent
            if manifest.get("parentRunId")
            else manifest
        )
        declarations: list[dict[str, Any]] = []
        declaration_limit = MAX_ARTIFACT_DECLARATIONS + 1
        for current in [manifest, *children]:
            current_declarations = self._run_artifact_declarations(
                qualification,
                current,
                parent=state_parent,
            )
            remaining = declaration_limit - len(declarations)
            declarations.extend(current_declarations[:remaining])
            if len(declarations) >= declaration_limit:
                break
        return (
            declarations[:MAX_ARTIFACT_DECLARATIONS],
            len(declarations) > MAX_ARTIFACT_DECLARATIONS,
        )

    def _run_artifact_declarations(
        self,
        qualification: str,
        manifest: Mapping[str, Any],
        *,
        parent: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        declaration_limit = MAX_ARTIFACT_DECLARATIONS + 1
        declarations: list[dict[str, Any]] = []
        batch_results = manifest.get("batchQuestionResults")
        batch_results_invalid = (
            "batchQuestionResults" in manifest
            and not isinstance(batch_results, list)
        )
        question_scoped_paths: set[str] = set()
        if isinstance(batch_results, list):
            # The manifest itself is byte-bounded before reaching this method.
            # Inspect every question result for attribution even though the
            # public declaration list is capped. Otherwise a path declared
            # after item 256 could be downgraded to an unscoped result path.
            for item in batch_results:
                if not isinstance(item, Mapping):
                    continue
                paths = item.get("changedFiles")
                if not isinstance(paths, list):
                    continue
                question_scoped_paths.update(
                    value.strip()
                    for value in paths
                    if isinstance(value, str) and value.strip()
                )
        if isinstance(batch_results, list):
            for index, item in enumerate(batch_results):
                if len(declarations) >= declaration_limit:
                    break
                if not isinstance(item, Mapping):
                    continue
                paths = item.get("changedFiles")
                if not isinstance(paths, list):
                    continue
                for value in paths:
                    if len(declarations) >= declaration_limit:
                        break
                    if not isinstance(value, str) or not value.strip():
                        continue
                    relative = value.strip()
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
                if len(declarations) >= declaration_limit:
                    break
        result = manifest.get("result")
        result = result if isinstance(result, Mapping) else {}
        for source in (result.get("changedFiles"), manifest.get("changedFiles")):
            if not isinstance(source, list):
                continue
            for value in source:
                if len(declarations) >= declaration_limit:
                    break
                if (
                    not isinstance(value, str)
                    or not value.strip()
                    or value.strip() in question_scoped_paths
                ):
                    continue
                relative = value.strip()
                if (
                    (
                        batch_results_invalid
                        or (
                            isinstance(batch_results, list)
                            and bool(batch_results)
                        )
                    )
                    and PurePosixPath(relative).suffix.lower()
                    in {".json", ".jsonl"}
                ):
                    rejected_declaration = self._declaration(
                        qualification,
                        manifest,
                        relative,
                        parent=parent,
                    )
                    rejected_declaration["_preRejected"] = (
                        "question_attribution_required"
                    )
                    declarations.append(rejected_declaration)
                    continue
                direct_question_result = (
                    manifest
                    if isinstance(manifest.get("questionId"), (str, int))
                    and str(manifest.get("questionId") or "")
                    else None
                )
                declarations.append(
                    self._declaration(
                        qualification,
                        manifest,
                        relative,
                        parent=parent,
                        question_result=direct_question_result,
                    )
                )
            if len(declarations) >= declaration_limit:
                break
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
            if (
                isinstance(value, str)
                or (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                )
            ):
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
            for value in values:
                if not isinstance(value, Mapping):
                    continue
                aliases = value.get("aliases")
                aliases = aliases if isinstance(aliases, list) else []
                aliases = {
                    str(candidate)
                    for candidate in (
                        value.get("id"),
                        value.get("uiQuestionId"),
                        value.get("reviewQuestionId"),
                        value.get("sourceQuestionKey"),
                        value.get("sourceRecordRef"),
                        *aliases,
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
            if before.st_nlink != 1:
                raise ArtifactReadError("hardlink_not_allowed")
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
                or (
                    before.st_dev,
                    before.st_ino,
                    before.st_mode,
                    before.st_nlink,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                )
                != (
                    after.st_dev,
                    after.st_ino,
                    after.st_mode,
                    after.st_nlink,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                )
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
        declared_identity = {
            field: str(identity[field])
            for field in (
                "sourceQuestionKey",
                "sourceRecordRef",
                "reviewQuestionId",
            )
            if identity.get(field)
        }
        if not (
            declared_identity.get("sourceQuestionKey")
            or declared_identity.get("sourceRecordRef")
        ):
            raise ArtifactReadError("record_resolution_failed")
        matches = []
        for record in records:
            if any(
                field not in record
                or record.get(field) is None
                or str(record[field]) != expected
                for field, expected in declared_identity.items()
            ):
                continue
            matches.append(record)
        if len(matches) != 1:
            raise ArtifactReadError("record_resolution_failed")
        rendered = json.dumps(matches[0], ensure_ascii=False, indent=2)
        if len(rendered.encode("utf-8")) > MAX_ARTIFACT_BYTES:
            raise ArtifactReadError("rendered_record_bytes_limit")
        return self._text(rendered, MAX_ARTIFACT_BYTES)

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
        event_type = (
            value.get("type")
            if isinstance(value.get("type"), str)
            else ""
        )
        if event_type not in _EVENT_TYPES:
            return None
        observed_at = value.get("observedAt")
        if not (
            isinstance(observed_at, (int, float))
            and not isinstance(observed_at, bool)
            and math.isfinite(float(observed_at))
            and observed_at >= 0
        ):
            observed_at = 0
        result: dict[str, Any] = {
            "schemaVersion": "monitor-event/v1",
            "eventId": cls._public_scalar_text(value.get("eventId"), 500),
            "serverInstanceId": cls._public_scalar_text(
                value.get("serverInstanceId"),
                300,
            ),
            "sequence": cls._nonnegative_int(value.get("sequence")) or 0,
            "observedAt": observed_at,
            "type": event_type,
            "correlation": {},
            "payload": {},
        }
        occurred_at = cls._public_event_time(value.get("occurredAt"))
        if occurred_at is not None:
            result["occurredAt"] = occurred_at
        correlation = value.get("correlation")
        if isinstance(correlation, Mapping):
            public_correlation: dict[str, Any] = {}
            for key in _CORRELATION_FIELDS:
                public = cls._public_scalar_text(
                    correlation.get(key),
                    300,
                )
                if public:
                    public_correlation[key] = public
            result["correlation"] = public_correlation
            for key in _CORRELATION_LIST_FIELDS:
                values = correlation.get(key)
                if isinstance(values, list):
                    result["correlation"][key] = cls._text_list(values, 200)
        payload = value.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        if event_type in _TEXT_EVENT_TYPES:
            public_payload: dict[str, Any] = {}
            for key in ("delta", "text", "phase", "state"):
                public = cls._public_scalar_text(
                    payload.get(key),
                    MAX_PUBLIC_EVENT_TEXT,
                )
                if public:
                    public_payload[key] = public
            result["payload"] = public_payload
            if event_type == "reasoningSummary":
                parts = payload.get("summaryParts")
                if isinstance(parts, list):
                    result["payload"]["summaryParts"] = [
                        public
                        for part in parts[:MAX_PUBLIC_EVENT_COLLECTION_ITEMS]
                        for public in [
                            cls._public_scalar_text(
                                part,
                                MAX_PUBLIC_EVENT_TEXT,
                            )
                        ]
                        if public
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
                for item in raw_plan[:MAX_PUBLIC_EVENT_COLLECTION_ITEMS]:
                    if not isinstance(item, Mapping):
                        continue
                    step = cls._public_scalar_text(
                        item.get("step"),
                        MAX_PUBLIC_EVENT_TEXT,
                    )
                    status = cls._public_scalar_text(
                        item.get("status"),
                        100,
                    )
                    if step and status:
                        public_plan.append({"step": step, "status": status})
            result["payload"] = {}
            for key in ("delta", "text", "state", "explanation"):
                public = cls._public_scalar_text(
                    payload.get(key),
                    MAX_PUBLIC_EVENT_TEXT,
                )
                if public:
                    result["payload"][key] = public
            if public_plan:
                result["payload"]["plan"] = public_plan
        elif event_type == "toolState":
            result["payload"] = {}
            for key in ("toolType", "state"):
                public = cls._public_scalar_text(payload.get(key), 100)
                if public:
                    result["payload"][key] = public
        elif event_type == "turnState":
            public_state = cls._public_scalar_text(
                payload.get("state"),
                100,
            )
            if public_state:
                result["payload"] = {
                    "state": public_state
                }
        elif event_type == "threadState":
            result["payload"] = {}
            public_state = cls._public_scalar_text(
                payload.get("state"),
                100,
            )
            if public_state:
                result["payload"]["state"] = public_state
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
            message = cls._public_scalar_text(
                payload.get("message"),
                MAX_PUBLIC_EVENT_TEXT,
            )
            result["payload"] = {
                "message": message,
                "willRetry": payload.get("willRetry") is True,
            }
        else:
            result["payload"] = {
                key: number
                for key in (
                    "fromSequence",
                    "toSequence",
                    "droppedNotifications",
                    "totalDroppedNotifications",
                )
                for number in [cls._nonnegative_int(payload.get(key))]
                if number is not None
            }
            if payload.get("scopeTruncated") is True:
                result["payload"]["scopeTruncated"] = True
        return result

    @classmethod
    def _public_scalar_text(cls, value: Any, limit: int) -> str:
        if isinstance(value, str):
            return cls._text(value, limit)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        ):
            return cls._text(value, limit)
        return ""

    @staticmethod
    def _public_event_time(value: Any) -> int | float | str | None:
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and value >= 0
        ):
            return value
        if isinstance(value, str):
            public = MonitorReadModel._text(value, 100).strip()
            return public or None
        return None

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
        # Redact the complete bounded source before applying a display limit.
        # Truncating first could expose a credential prefix split at the limit.
        text = str(value or "")
        folded = text.casefold()
        if "private key" in folded:
            text = _PRIVATE_KEY.sub("<redacted-private-key>", text)
        if "file:" in folded:
            text = _FILE_URL.sub("<absolute-path>", text)
        if "\\" in text:
            text = _WINDOWS_ABSOLUTE_PATH.sub("<absolute-path>", text)
        if "://" in text and "@" in text:
            text = _URL_CREDENTIAL.sub(r"\1<redacted>@", text)
        if any(
            header in folded
            for header in (
                "authorization",
                "proxy-authorization",
                "cookie",
                "set-cookie",
            )
        ):
            text = _AUTHORIZATION_HEADER.sub(r"\1<redacted>", text)
            text = _COOKIE_HEADER.sub(r"\1<redacted>", text)
        text = _ABSOLUTE_PATH.sub("<absolute-path>", text)
        if "eyj" in folded:
            text = _JWT.sub("<redacted-jwt>", text)
        if any(
            prefix in folded
            for prefix in (
                "bearer ",
                "basic ",
                "sk-",
                "github_pat_",
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
        return text[:limit]

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


class MonitorStoreReadError(ValueError):
    def __init__(self, reason_code: str, *, bytes_read: int = 0):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.bytes_read = max(0, int(bytes_read))


class ArtifactReadError(ValueError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code
