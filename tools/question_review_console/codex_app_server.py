from __future__ import annotations

import copy
import json
import math
import os
import queue
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import tomllib
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, TextIO

try:
    import resource
except ImportError:  # pragma: no cover - Windows does not provide resource.
    resource = None

from tools.question_review_console.turn_budget import (
    GLOBAL_TURN_CAPACITY,
    GlobalTurnBudget,
)


DEFAULT_CODEX_PATH = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
SAFE_SHELL_PATH = (
    "/usr/bin:/bin:/usr/sbin:/sbin:"
    "/Applications/ChatGPT.app/Contents/Resources"
)
API_CREDENTIAL_ENV_VARS = {
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "CODEX_API_KEY",
    "CHATGPT_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_ORGANIZATION",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT",
    "OPENAI_PROJECT_ID",
    "ALL_PROXY",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "all_proxy",
    "https_proxy",
    "http_proxy",
}
USAGE_BASED_PLANS = {
    "self_serve_business_usage_based",
    "enterprise_cbp_usage_based",
}
KNOWN_SUBSCRIPTION_PLANS = {
    "free",
    "go",
    "plus",
    "pro",
    "prolite",
    "team",
    "business",
    "enterprise",
    "edu",
}
APP_SERVER_PROVIDER = "Codex App Server"
RUNTIME_DIAGNOSTIC_PAIRS = frozenset(
    {
        ("runtime_environment", "auth_isolation"),
        ("protocol", "initialize_rpc"),
        ("protocol", "initialize_response"),
        ("config", "config_read"),
        ("config", "config_layers"),
        ("config", "config_shape"),
        ("config", "custom_agents"),
        ("config", "official_endpoint"),
        ("authentication", "login_method"),
        ("provider", "model_provider"),
        ("host_integration", "notify"),
        ("telemetry", "analytics"),
        ("telemetry", "otel"),
        ("capabilities", "features"),
        ("runtime_environment", "shell_environment"),
        ("capabilities", "mcp"),
    }
)
DISABLED_EXTERNAL_FEATURES = (
    "apps",
    "auth_elicitation",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "computer_use",
    "goals",
    "hooks",
    "image_generation",
    "in_app_browser",
    "memories",
    "plugin_sharing",
    "plugins",
    "remote_plugin",
    "skill_mcp_dependency_install",
    "tool_suggest",
)

QUESTION_MAINTENANCE_MODEL = "gpt-5.6-luna"
QUESTION_MAINTENANCE_RETRY_MODEL = "gpt-5.6-sol"
QUESTION_MAINTENANCE_MODELS = frozenset(
    {QUESTION_MAINTENANCE_MODEL, QUESTION_MAINTENANCE_RETRY_MODEL}
)
TURN_REASONING_EFFORT = "high"
STANDARD_SPEED_MODE = "standard"
FAST_SPEED_MODE = "fast"
SPEED_MODES = frozenset({STANDARD_SPEED_MODE})
MAINTENANCE_RESEARCH_WORKERS = 0
APP_SERVER_AGENT_THREAD_CAP = 1
APP_SERVER_AGENT_MAX_DEPTH = 1
APP_SERVER_CONTROL_PLANE_CAPACITY = 300
APP_SERVER_CONTROL_REQUEST_TIMEOUT_SECONDS = 120
MIN_APP_SERVER_FILE_DESCRIPTORS = 65_536
TURN_HEARTBEAT_INTERVAL_SECONDS = 15.0
# 公式一次資料の確認を含む工程03は、1問でも high reasoning の turn が
# 15分をわずかに超えることがある。検証済みpatchを完了直前に巻き戻さないよう、
# 1 turn の上限には十分な保存・receipt作成時間を含める。
DEFAULT_TURN_TIMEOUT_SECONDS = 1800
# 構造化応答の末尾で空白だけを生成し続ける異常は、通常の推論待ちと
# 区別して早期に再試行へ送る。短い整形用空白では発火させない。
STRUCTURED_OUTPUT_STALL_TIMEOUT_SECONDS = 30.0
STRUCTURED_OUTPUT_TRAILING_WHITESPACE_CHARS = 256
# 完成した構造化messageを受信してもturn/completedだけが欠落する場合がある。
# messageは後段のschema検証を必ず通すため、短い猶予後にturnを閉じて検証へ渡す。
STRUCTURED_OUTPUT_COMPLETION_GRACE_SECONDS = 30.0
SUBSCRIPTION_STATUS_CACHE_SECONDS = 60.0
SUBSCRIPTION_STATUS_READ_ATTEMPTS = 3
SUBSCRIPTION_STATUS_READ_RETRY_DELAY_SECONDS = 0.2
PROVIDER_RECOVERY_ATTEMPTS = 4
PROVIDER_RECOVERY_BASE_DELAY_SECONDS = 30.0
PROVIDER_RECOVERY_MAX_DELAY_SECONDS = 120.0
RESEARCH_AGENT_ROLE = "explorer"
RESEARCH_AGENT_CONFIG_FILENAME = "question-maintenance-explorer.toml"
RESEARCH_AGENT_DESCRIPTION = "問題整備のread-only事前調査担当"
RESEARCH_AGENT_DEVELOPER_INSTRUCTIONS = (
    "問題整備に必要な根拠と問題IDごとの判断案だけを調査する。"
    "ファイル、Git、Firestoreその他の外部状態を変更しない。"
    "割り当てられた対象だけを読み、結論と根拠を親threadへ簡潔に返す。"
)
RESEARCH_AGENT_CONFIG = f'''name = "{RESEARCH_AGENT_ROLE}"
description = "{RESEARCH_AGENT_DESCRIPTION}"
developer_instructions = "{RESEARCH_AGENT_DEVELOPER_INSTRUCTIONS}"
model = "{QUESTION_MAINTENANCE_MODEL}"
model_reasoning_effort = "{TURN_REASONING_EFFORT}"
sandbox_mode = "read-only"

[features]
multi_agent = false
'''
GLOBAL_AGENT_CONFIG_KEYS = {
    "interrupt_message",
    "job_max_runtime_seconds",
    "max_depth",
    "max_threads",
}
APP_SERVER_UNSUPPORTED_OUTPUT_SCHEMA_KEYWORDS = frozenset({"uniqueItems"})
HOOK_STATUS_CACHE_SECONDS = 60.0


def adapt_output_schema_for_app_server(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Return an App Server-compatible deep copy of an output schema."""

    def adapt(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): adapt(item)
                for key, item in value.items()
                if key not in APP_SERVER_UNSUPPORTED_OUTPUT_SCHEMA_KEYWORDS
            }
        if isinstance(value, list):
            return [adapt(item) for item in value]
        return copy.deepcopy(value)

    return adapt(schema)


class CodexAppServerError(RuntimeError):
    pass


class CodexRequestTimeoutError(CodexAppServerError):
    """One JSON-RPC response did not arrive before its request deadline."""


class CodexProcessExitError(CodexAppServerError):
    """The App Server process exited while its runtime was initialized."""


class CodexRpcError(CodexAppServerError):
    """JSON-RPC failure with only diagnostic-safe metadata retained."""

    def __init__(
        self,
        message: str,
        *,
        method: str,
        code: int | str | None,
        data_type: str | None,
    ) -> None:
        super().__init__(message)
        self.method = method
        self.code = code
        self.data_type = data_type


class CodexControlRequestTimeoutError(CodexAppServerError):
    """A question-scoped control-plane RPC exceeded its deadline."""


class CodexTurnTimeoutError(CodexAppServerError):
    """The deadline of one active model turn expired."""


class CodexTerminalTurnFailedError(CodexAppServerError):
    """The protocol reported that one model turn terminally failed."""

    def __init__(
        self,
        message: str,
        *,
        thread_id: str,
        turn_id: str,
        status: str,
        error: Any,
    ) -> None:
        super().__init__(message)
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.status = status
        self.error = copy.deepcopy(error)


class SubscriptionGateError(CodexAppServerError):
    pass


def ensure_app_server_file_descriptor_capacity(
    minimum: int = MIN_APP_SERVER_FILE_DESCRIPTORS,
) -> int | None:
    """Raise the inherited soft nofile limit before the long-lived server starts."""

    if resource is None:
        return None
    soft_limit, hard_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft_limit >= minimum:
        return int(soft_limit)
    if hard_limit != resource.RLIM_INFINITY and hard_limit < minimum:
        raise CodexAppServerError(
            "100問同時整備に必要なfile descriptor上限を確保できません。"
            f"soft={soft_limit}, hard={hard_limit}, required={minimum}"
        )
    try:
        resource.setrlimit(
            resource.RLIMIT_NOFILE,
            (minimum, hard_limit),
        )
    except (OSError, ValueError) as exc:
        raise CodexAppServerError(
            "100問同時整備に必要なfile descriptor上限を引き上げられません。"
            f"soft={soft_limit}, hard={hard_limit}, required={minimum}"
        ) from exc
    updated_soft, _updated_hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if updated_soft < minimum:
        raise CodexAppServerError(
            "100問同時整備に必要なfile descriptor上限を確認できません。"
            f"soft={updated_soft}, required={minimum}"
        )
    return int(updated_soft)


def normalize_speed_mode(value: Any) -> str:
    normalized = str(value or STANDARD_SPEED_MODE).strip().casefold()
    if normalized not in SPEED_MODES:
        raise ValueError(
            "問題整備は追加課金を避けるためStandard modeだけを使用します。"
        )
    return normalized


@dataclass(frozen=True)
class AppServerTurnResult:
    thread_id: str
    session_id: str
    turn_id: str
    final_message: str
    model: str
    service_tier: str | None
    reasoning_effort: str = TURN_REASONING_EFFORT
    changed_files: tuple[str, ...] = ()
    subagent_thread_ids: tuple[str, ...] = ()
    subagent_models: tuple[str, ...] = ()
    subagent_reasoning_efforts: tuple[str, ...] = ()
    completion_mode: str = "turn_completed"
    model_turn_started_at: str | None = None
    model_turn_finished_at: str | None = None
    model_turn_duration_seconds: float | None = None
    model_turn_queue_wait_seconds: float | None = None


@dataclass
class _PendingResponse:
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: Any = None


@dataclass
class _TurnState:
    thread_id: str
    turn_id: str
    emit: Callable[[str], None]
    structured_output: bool = False
    requested_monotonic: float | None = None
    on_model_turn_event: Callable[[Mapping[str, Any]], None] | None = None
    event: threading.Event = field(default_factory=threading.Event)
    messages: list[tuple[str | None, str]] = field(default_factory=list)
    changed_files: set[str] = field(default_factory=set)
    subagent_thread_ids: set[str] = field(default_factory=set)
    subagent_models: set[str] = field(default_factory=set)
    subagent_reasoning_efforts: set[str] = field(default_factory=set)
    recorded_item_ids: set[str] = field(default_factory=set)
    last_semantic_delta_at: float | None = None
    trailing_whitespace_chars: int = 0
    completed_message_at: float | None = None
    protocol_started_at: str | None = None
    protocol_started_monotonic: float | None = None
    protocol_finished_at: str | None = None
    protocol_finished_monotonic: float | None = None
    status: str = "inProgress"
    error: Any = None


def _as_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SubscriptionGateError(f"{label}を確認できません。")
    return value


def validate_subscription_access(
    account_response: Mapping[str, Any],
    rate_limit_response: Mapping[str, Any],
    *,
    speed_mode: str = STANDARD_SPEED_MODE,
) -> dict[str, Any]:
    """ChatGPT subscription以外へ決して進まないためのfail-closed gate。"""

    speed_mode = normalize_speed_mode(speed_mode)
    account = _as_mapping(account_response.get("account"), "Codex account")
    if account.get("type") != "chatgpt":
        raise SubscriptionGateError(
            "ChatGPTサブスクリプション認証ではありません。API key又は外部providerは使用しません。"
        )
    plan_type = str(account.get("planType") or "")
    if plan_type in USAGE_BASED_PLANS:
        raise SubscriptionGateError("従量課金planでは実行できません。")
    if plan_type not in KNOWN_SUBSCRIPTION_PLANS:
        raise SubscriptionGateError("subscription planを安全に判定できません。")
    snapshot = _as_mapping(rate_limit_response.get("rateLimits"), "利用上限")
    snapshot_plan = snapshot.get("planType")
    if str(snapshot_plan or "") != plan_type:
        raise SubscriptionGateError("accountと利用上限のplan情報が一致しません。")
    if "rateLimitReachedType" not in snapshot:
        raise SubscriptionGateError("利用上限到達状態を安全に判定できません。")
    if snapshot.get("rateLimitReachedType") is not None:
        raise SubscriptionGateError("サブスクリプションの利用上限に達しています。")
    for window_name in ("primary", "secondary"):
        window = snapshot.get(window_name)
        if window is None and window_name == "secondary":
            continue
        window = _as_mapping(window, f"{window_name}利用上限")
        used_percent = window.get("usedPercent")
        if isinstance(used_percent, bool) or not isinstance(used_percent, (int, float)):
            raise SubscriptionGateError("利用率を安全に判定できません。")
        if not math.isfinite(float(used_percent)):
            raise SubscriptionGateError("利用率を安全に判定できません。")
        if used_percent >= 100:
            raise SubscriptionGateError("サブスクリプションの利用上限に達しています。")

    credits = _as_mapping(snapshot.get("credits"), "credit状態")
    credits_enabled = credits.get("hasCredits")
    if not isinstance(credits_enabled, bool):
        raise SubscriptionGateError("credit状態を安全に判定できません。")
    if credits_enabled:
        raise SubscriptionGateError(
            "追加Codex creditsが有効なため実行できません。"
            "問題整備はサブスクリプション範囲内のStandard modeだけを使用します。"
        )
    if "individualLimit" not in snapshot:
        raise SubscriptionGateError("spend control状態を安全に確認できません。")
    individual_limit = snapshot.get("individualLimit")
    if individual_limit is not None and not isinstance(individual_limit, Mapping):
        raise SubscriptionGateError("spend control状態を安全に確認できません。")
    if "rateLimitsByLimitId" not in rate_limit_response:
        raise SubscriptionGateError("補助利用上限を安全に確認できません。")
    auxiliary = _as_mapping(
        rate_limit_response.get("rateLimitsByLimitId"), "補助利用上限"
    )
    for value in auxiliary.values():
        if not isinstance(value, Mapping):
            raise SubscriptionGateError("補助利用上限を安全に判定できません。")
        if "rateLimitReachedType" not in value:
            raise SubscriptionGateError("補助利用上限の到達状態を確認できません。")
        if value.get("rateLimitReachedType") is not None:
            raise SubscriptionGateError("サブスクリプションの利用上限に達しています。")
        if "credits" not in value:
            raise SubscriptionGateError("補助credit状態を安全に確認できません。")
        extra_credits = value.get("credits")
        if extra_credits is not None and not isinstance(extra_credits, Mapping):
            raise SubscriptionGateError("補助credit状態を安全に確認できません。")
        if extra_credits is not None and not isinstance(
            extra_credits.get("hasCredits"), bool
        ):
            raise SubscriptionGateError("補助credit状態を安全に確認できません。")
        if extra_credits is not None and extra_credits.get("hasCredits"):
            raise SubscriptionGateError(
                "補助Codex creditsが有効なため実行できません。"
                "問題整備はサブスクリプション範囲内のStandard modeだけを使用します。"
            )
        if "individualLimit" not in value:
            raise SubscriptionGateError("補助spend controlを安全に確認できません。")
        extra_limit = value.get("individualLimit")
        if extra_limit is not None and not isinstance(extra_limit, Mapping):
            raise SubscriptionGateError("補助spend controlを安全に確認できません。")

    return {
        "allowed": True,
        "accountType": "chatgpt",
        "planType": plan_type,
        "rateLimitReachedType": None,
        "creditsEnabled": credits_enabled,
        "fastModeAvailable": False,
        "speedMode": STANDARD_SPEED_MODE,
        "standardMode": True,
        "fastMode": False,
    }


MONITOR_NOTIFICATION_METHODS = frozenset(
    {
        "error",
        "item/agentMessage/delta",
        "item/plan/delta",
        "item/reasoning/summaryPartAdded",
        "item/reasoning/summaryTextDelta",
        "item/started",
        "item/completed",
        "thread/archived",
        "thread/closed",
        "thread/deleted",
        "thread/started",
        "thread/status/changed",
        "thread/tokenUsage/updated",
        "thread/unarchived",
        "turn/plan/updated",
        "turn/started",
        "turn/completed",
    }
)

OBSERVER_ADAPTER_BINDING_CAPACITY = 512
OBSERVER_ADAPTER_GAP_CAPACITY = 64
OBSERVER_ADAPTER_CONTROL_BATCH_SIZE = 16
OBSERVER_ADAPTER_CLOSE_TIMEOUT_SECONDS = 5.0
OBSERVER_ADAPTER_COALESCED_MAX_CHARS = 4096
OBSERVER_ADAPTER_COALESCED_MAX_FRAGMENTS = 512
OBSERVER_ADAPTER_COALESCIBLE_METHODS = frozenset(
    {
        "item/agentMessage/delta",
        "item/reasoning/summaryTextDelta",
    }
)


@dataclass
class _CoalescedObserverNotification:
    ordinal: int
    message: Mapping[str, Any]
    observed_at: float
    inline_binding: tuple[dict[str, Any], str, str | None] | None
    stream_key: tuple[str, ...]
    deltas: list[str]
    delta_chars: int


class _NonBlockingObserverAdapter:
    """Bounded, non-blocking boundary between stdout and an observer."""

    def __init__(
        self,
        observer: Any | None,
        *,
        capacity: int = 4096,
        binding_capacity: int = OBSERVER_ADAPTER_BINDING_CAPACITY,
        gap_capacity: int = OBSERVER_ADAPTER_GAP_CAPACITY,
        control_batch_size: int = OBSERVER_ADAPTER_CONTROL_BATCH_SIZE,
    ) -> None:
        self._observer = observer
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=max(1, capacity))
        self._binding_capacity = max(1, int(binding_capacity))
        self._gap_capacity = max(1, int(gap_capacity))
        self._control_batch_size = max(1, int(control_batch_size))
        self._gap_lock = threading.Lock()
        self._work_event = threading.Event()
        self._next_notification_ordinal = 0
        self._last_accepted_notification_ordinal = 0
        self._last_finished_notification_ordinal = 0
        self._gap_segments: OrderedDict[
            tuple[Any, ...],
            list[Any],
        ] = OrderedDict()
        self._gap_overflow_count = 0
        self._gap_overflow_boundary = 0
        self._pending_bindings: OrderedDict[
            str,
            tuple[dict[str, Any], str, str | None],
        ] = OrderedDict()
        self._thread_route_groups: OrderedDict[
            str,
            tuple[str, str, tuple[tuple[str, str], ...]],
        ] = OrderedDict()
        self._route_snapshot: tuple[
            tuple[str, str, tuple[tuple[str, str], ...]],
            ...,
        ] = ()
        self._active_callbacks = 0
        self._worker_busy = False
        self._worker_failure: BaseException | None = None
        self._pending_coalesced: OrderedDict[
            tuple[str, ...],
            _CoalescedObserverNotification,
        ] = OrderedDict()
        self._coalesced_notifications = 0
        self._coalesced_by_method = {
            method: 0 for method in sorted(OBSERVER_ADAPTER_COALESCIBLE_METHODS)
        }
        self._queue_peak = 0
        self._closed = threading.Event()
        self._worker: threading.Thread | None = None
        if observer is not None:
            self._worker = threading.Thread(
                target=self._run,
                daemon=True,
                name="question-review-monitor-observer-adapter",
            )
            self._worker.start()

    def put_nowait(self, message: Mapping[str, Any]) -> None:
        if self._observer is None or self._closed.is_set():
            return
        thread_id = self._notification_thread_id(message)
        terminal = self._terminal_notification(message)
        observed_at = time.time()
        with self._gap_lock:
            if self._closed.is_set():
                return
            inline_binding = (
                self._pending_bindings.pop(thread_id, None)
                if thread_id
                else None
            )
            route_group = (
                self._thread_route_groups.get(thread_id)
                if thread_id
                else None
            )
            routes = (
                (route_group,)
                if route_group is not None
                else self._route_snapshot
            )
            if terminal and thread_id:
                # Freeze correlation before deleting live routing state. A
                # terminal notification that overflows the queue must still
                # attribute its observation gap to the completed route.
                self._thread_route_groups.pop(thread_id, None)
                self._refresh_route_snapshot_locked()
            coalescing = self._coalescing_stream(message)
            if coalescing is not None and inline_binding is None:
                stream_key, delta = coalescing
                pending = self._pending_coalesced.get(stream_key)
                if pending is not None:
                    if (
                        len(pending.deltas)
                        < OBSERVER_ADAPTER_COALESCED_MAX_FRAGMENTS
                        and pending.delta_chars + len(delta)
                        <= OBSERVER_ADAPTER_COALESCED_MAX_CHARS
                    ):
                        pending.observed_at = observed_at
                        pending.deltas.append(delta)
                        pending.delta_chars += len(delta)
                        self._pending_coalesced.move_to_end(stream_key)
                        self._coalesced_notifications += 1
                        self._coalesced_by_method[stream_key[0]] += 1
                        self._work_event.set()
                        return
                    self._pending_coalesced.pop(stream_key, None)
            else:
                # A lifecycle/control notification is an ordering boundary.
                # Existing queue entries remain ahead of it, but later deltas
                # must start a new aggregate after this notification.
                self._pending_coalesced.clear()
            self._next_notification_ordinal += 1
            ordinal = self._next_notification_ordinal
            try:
                if coalescing is None:
                    entry = (
                        "notification",
                        (ordinal, message, observed_at, inline_binding),
                    )
                else:
                    stream_key, delta = coalescing
                    aggregate = _CoalescedObserverNotification(
                        ordinal=ordinal,
                        message=message,
                        observed_at=observed_at,
                        inline_binding=inline_binding,
                        stream_key=stream_key,
                        deltas=[delta],
                        delta_chars=len(delta),
                    )
                    entry = ("coalesced_notification", aggregate)
                # Do not normalize, serialize, or persist on the App Server
                # stdout reader thread. Lossless delta joining is also left to
                # the monitor worker.
                self._queue.put_nowait(entry)
                if coalescing is not None:
                    self._pending_coalesced[stream_key] = aggregate
                self._last_accepted_notification_ordinal = ordinal
                self._queue_peak = max(self._queue_peak, self._queue.qsize())
            except Exception:
                boundary = self._last_accepted_notification_ordinal
                self._record_gap_locked(
                    count=1,
                    boundary=boundary,
                    routes=routes,
                )
                if inline_binding is not None and not terminal:
                    self._pending_bindings[thread_id] = inline_binding
                    self._pending_bindings.move_to_end(thread_id)
        self._work_event.set()

    def telemetry(self) -> dict[str, Any]:
        with self._gap_lock:
            return {
                "queueCapacity": self._queue.maxsize,
                "queueDepth": self._queue.qsize(),
                "queuePeak": self._queue_peak,
                "coalescedNotifications": self._coalesced_notifications,
                "coalescedByMethod": dict(self._coalesced_by_method),
                "pendingStreams": len(self._pending_coalesced),
            }

    def bind_runtime(
        self,
        context: Mapping[str, Any],
        thread_id: str,
        turn_id: str | None = None,
    ) -> None:
        if self._observer is None or self._closed.is_set():
            return
        resolved_thread_id = str(thread_id)
        binding = (dict(context), resolved_thread_id, turn_id)
        route_group = self._route_group(binding[0])
        with self._gap_lock:
            if self._closed.is_set():
                return
            refresh_routes = False
            if route_group is not None and resolved_thread_id:
                previous_route = self._thread_route_groups.get(
                    resolved_thread_id
                )
                if (
                    previous_route is None
                    and len(self._thread_route_groups)
                    >= self._binding_capacity
                ):
                    evicted_thread_id, _evicted_route = (
                        self._thread_route_groups.popitem(last=False)
                    )
                    self._pending_bindings.pop(evicted_thread_id, None)
                    self._record_gap_locked(
                        count=1,
                        boundary=self._last_accepted_notification_ordinal,
                        routes=(),
                        scope_truncated=True,
                    )
                    refresh_routes = True
                self._thread_route_groups[resolved_thread_id] = route_group
                self._thread_route_groups.move_to_end(resolved_thread_id)
                refresh_routes = (
                    refresh_routes or previous_route != route_group
                )
            if refresh_routes:
                self._refresh_route_snapshot_locked()
            if resolved_thread_id in self._pending_bindings:
                self._pending_bindings[resolved_thread_id] = binding
                self._pending_bindings.move_to_end(resolved_thread_id)
            else:
                if len(self._pending_bindings) >= self._binding_capacity:
                    self._pending_bindings.popitem(last=False)
                    self._record_gap_locked(
                        count=1,
                        boundary=self._last_accepted_notification_ordinal,
                        routes=(),
                        scope_truncated=True,
                    )
                self._pending_bindings[resolved_thread_id] = binding
        self._work_event.set()

    def record_observation_gap(self, count: int = 1) -> None:
        """Queue an explicit continuity break without touching the reader path."""

        if self._observer is None or self._closed.is_set():
            return
        resolved_count = max(1, int(count))
        with self._gap_lock:
            if self._closed.is_set():
                return
            boundary = self._last_accepted_notification_ordinal
            self._record_gap_locked(
                count=resolved_count,
                boundary=boundary,
                routes=self._route_snapshot,
            )
        self._work_event.set()

    def close(
        self,
        timeout: float = OBSERVER_ADAPTER_CLOSE_TIMEOUT_SECONDS,
    ) -> None:
        resolved_timeout = float(timeout)
        if not math.isfinite(resolved_timeout) or resolved_timeout < 0:
            raise ValueError(
                "observer adapter close timeout must be finite and nonnegative"
            )
        with self._gap_lock:
            self._closed.set()
        self._work_event.set()
        worker = self._worker
        if worker is None:
            return
        if worker is threading.current_thread():
            raise RuntimeError("observer adapter cannot close from its worker")
        worker.join(timeout=resolved_timeout)
        if worker.is_alive():
            raise TimeoutError(
                "observer adapter did not drain accepted work before "
                f"the {resolved_timeout:g}s close deadline"
            )
        if self._worker_failure is not None:
            raise RuntimeError("observer adapter worker failed") from (
                self._worker_failure
            )
        if self._has_pending_work():
            raise RuntimeError(
                "observer adapter worker stopped with accepted work pending"
            )

    def drain_for_test(self, timeout: float = 1.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while self._has_pending_work() and time.monotonic() < deadline:
            time.sleep(0.001)
        return not self._has_pending_work()

    def _run(self) -> None:
        try:
            self._run_loop()
        except BaseException as exc:  # pragma: no cover - defensive boundary.
            with self._gap_lock:
                self._worker_failure = exc
            self._work_event.set()
        finally:
            with self._gap_lock:
                self._worker_busy = False

    def _run_loop(self) -> None:
        while True:
            with self._gap_lock:
                self._worker_busy = True
            did_work = False
            bindings = self._take_binding_batch()
            for binding in bindings:
                did_work = True
                self._deliver_binding(binding)

            try:
                entry = self._queue.get_nowait()
            except queue.Empty:
                entry = None
            if entry is not None:
                did_work = True
                notification_ordinal: int | None = None
                try:
                    kind, payload = entry
                    coalesced_count = 0
                    coalesced_method = ""
                    if kind == "coalesced_notification":
                        (
                            notification_ordinal,
                            notification,
                            observed_at,
                            inline_binding,
                            coalesced_count,
                            coalesced_method,
                        ) = self._claim_coalesced_notification(payload)
                    elif kind == "notification":
                        (
                            notification_ordinal,
                            notification,
                            observed_at,
                            inline_binding,
                        ) = payload
                    else:
                        raise RuntimeError(
                            f"unknown observer adapter entry: {kind}"
                        )
                    if inline_binding is not None:
                        self._deliver_binding(inline_binding)
                    if coalesced_count:
                        self._record_lossless_coalescing(
                            coalesced_count,
                            coalesced_method,
                        )
                    self._deliver_notification(notification, observed_at)
                except Exception:
                    pass
                finally:
                    self._queue.task_done()
                    if notification_ordinal is not None:
                        self._notification_finished(notification_ordinal)

            if self._flush_ready_gaps():
                did_work = True
            with self._gap_lock:
                self._worker_busy = False
            if self._should_stop():
                return
            if not did_work:
                self._work_event.wait(timeout=0.05)
                self._work_event.clear()

    def _take_binding_batch(
        self,
    ) -> list[tuple[dict[str, Any], str, str | None]]:
        bindings: list[tuple[dict[str, Any], str, str | None]] = []
        with self._gap_lock:
            for _index in range(self._control_batch_size):
                if not self._pending_bindings:
                    break
                _thread_id, binding = self._pending_bindings.popitem(
                    last=False
                )
                bindings.append(binding)
        return bindings

    def _claim_coalesced_notification(
        self,
        aggregate: _CoalescedObserverNotification,
    ) -> tuple[
        int,
        dict[str, Any],
        float,
        tuple[dict[str, Any], str, str | None] | None,
        int,
        str,
    ]:
        with self._gap_lock:
            if self._pending_coalesced.get(aggregate.stream_key) is aggregate:
                self._pending_coalesced.pop(aggregate.stream_key, None)
            observed_at = aggregate.observed_at
            deltas = tuple(aggregate.deltas)
        notification = dict(aggregate.message)
        params = notification.get("params")
        if not isinstance(params, Mapping):
            raise RuntimeError("coalesced notification has no params")
        joined_params = dict(params)
        joined_params["delta"] = "".join(deltas)
        notification["params"] = joined_params
        return (
            aggregate.ordinal,
            notification,
            observed_at,
            aggregate.inline_binding,
            max(0, len(deltas) - 1),
            aggregate.stream_key[0],
        )

    def _record_lossless_coalescing(self, count: int, method: str) -> None:
        try:
            record = getattr(
                self._observer,
                "record_lossless_coalescing",
                None,
            )
        except Exception:
            return
        if not callable(record):
            return
        self._callback_started()
        try:
            try:
                record(
                    count,
                    method=method,
                    queue_capacity=self._queue.maxsize,
                    queue_peak=self._queue_peak,
                )
            except TypeError:
                record(count, method=method)
        except Exception:
            pass
        finally:
            self._callback_finished()

    def _deliver_binding(
        self,
        binding: tuple[dict[str, Any], str, str | None],
    ) -> None:
        try:
            bind = getattr(self._observer, "bind_runtime", None)
        except Exception:
            return
        if not callable(bind):
            return
        self._callback_started()
        try:
            bind(*binding)
        except Exception:
            pass
        finally:
            self._callback_finished()

    def _deliver_notification(
        self,
        notification: Mapping[str, Any],
        observed_at: float,
    ) -> None:
        callback: Callable[..., Any] | None = None
        arguments: tuple[Any, ...] = ()
        try:
            put_observed = getattr(
                self._observer,
                "put_observed_nowait",
                None,
            )
            if callable(put_observed):
                callback = put_observed
                arguments = (notification, observed_at)
            else:
                put_nowait = getattr(self._observer, "put_nowait", None)
                if callable(put_nowait):
                    callback = put_nowait
                    arguments = (notification,)
                else:
                    observe = getattr(self._observer, "observe", None)
                    if callable(observe):
                        callback = observe
                        arguments = (notification,)
        except Exception:
            return
        if callback is None:
            return
        self._callback_started()
        try:
            callback(*arguments)
        except Exception:
            pass
        finally:
            self._callback_finished()

    def _callback_started(self) -> None:
        with self._gap_lock:
            self._active_callbacks += 1

    def _callback_finished(self) -> None:
        with self._gap_lock:
            self._active_callbacks -= 1
        self._work_event.set()

    def _has_pending_work(self) -> bool:
        if self._queue.unfinished_tasks:
            return True
        with self._gap_lock:
            return bool(
                self._pending_bindings
                or self._pending_coalesced
                or self._gap_segments
                or self._gap_overflow_count
                or self._active_callbacks
                or self._worker_busy
            )

    def _should_stop(self) -> bool:
        if not self._closed.is_set() or not self._queue.empty():
            return False
        with self._gap_lock:
            return not (
                self._pending_bindings
                or self._pending_coalesced
                or self._gap_segments
                or self._gap_overflow_count
                or self._active_callbacks
            )

    @staticmethod
    def _route_group(
        context: Mapping[str, Any],
    ) -> tuple[str, str, tuple[tuple[str, str], ...]] | None:
        qualification = str(context.get("qualification") or "")
        storage_run_id = str(
            context.get("parentRunId")
            or context.get("runId")
            or context.get("childRunId")
            or ""
        )
        if not qualification or not storage_run_id:
            return None
        routes = {
            (qualification, str(value))
            for value in (
                context.get("runId"),
                context.get("parentRunId"),
                context.get("childRunId"),
            )
            if isinstance(value, (str, int)) and str(value)
        }
        routes.add((qualification, storage_run_id))
        return qualification, storage_run_id, tuple(sorted(routes))

    def _refresh_route_snapshot_locked(self) -> None:
        grouped: dict[tuple[str, str], set[tuple[str, str]]] = {}
        for qualification, storage_run_id, routes in (
            self._thread_route_groups.values()
        ):
            grouped.setdefault((qualification, storage_run_id), set()).update(
                routes
            )
        self._route_snapshot = tuple(
            (
                qualification,
                storage_run_id,
                tuple(sorted(routes)),
            )
            for (qualification, storage_run_id), routes in sorted(
                grouped.items()
            )
        )

    @staticmethod
    def _notification_thread_id(message: Mapping[str, Any]) -> str:
        params = message.get("params")
        if not isinstance(params, Mapping):
            return ""
        thread = params.get("thread")
        return str(
            params.get("threadId")
            or (
                thread.get("id")
                if isinstance(thread, Mapping)
                else ""
            )
            or ""
        )

    @staticmethod
    def _terminal_notification(message: Mapping[str, Any]) -> bool:
        method = str(message.get("method") or "")
        if method in {
            "turn/completed",
            "thread/closed",
            "thread/deleted",
        }:
            return True
        if method != "error":
            return False
        params = message.get("params")
        return not (
            isinstance(params, Mapping)
            and params.get("willRetry") is True
        )

    @classmethod
    def _coalescing_stream(
        cls,
        message: Mapping[str, Any],
    ) -> tuple[tuple[str, ...], str] | None:
        method = str(message.get("method") or "")
        if method not in OBSERVER_ADAPTER_COALESCIBLE_METHODS:
            return None
        params = message.get("params")
        if not isinstance(params, Mapping):
            return None
        delta = params.get("delta")
        if not isinstance(delta, str):
            return None
        turn = params.get("turn")
        item = params.get("item")
        turn_id = str(
            params.get("turnId")
            or (turn.get("id") if isinstance(turn, Mapping) else "")
            or (item.get("turnId") if isinstance(item, Mapping) else "")
            or ""
        )
        item_id = str(
            params.get("itemId")
            or (item.get("id") if isinstance(item, Mapping) else "")
            or ""
        )
        thread_id = cls._notification_thread_id(message)
        if not thread_id or not turn_id:
            return None
        summary_index = params.get("summaryIndex")
        public_summary_index = (
            str(summary_index)
            if isinstance(summary_index, int)
            and not isinstance(summary_index, bool)
            and summary_index >= 0
            else ""
        )
        return (
            (
                method,
                thread_id,
                turn_id,
                item_id,
                public_summary_index,
            ),
            delta,
        )

    def _record_gap_locked(
        self,
        *,
        count: int,
        boundary: int,
        routes: Any,
        scope_truncated: bool = False,
    ) -> None:
        resolved_count = max(1, int(count))
        resolved_boundary = max(0, int(boundary))
        if scope_truncated:
            self._gap_overflow_count += resolved_count
            self._gap_overflow_boundary = max(
                self._gap_overflow_boundary,
                resolved_boundary,
            )
            return
        normalized_routes = tuple(routes)
        key = (resolved_boundary, normalized_routes)
        existing = self._gap_segments.get(key)
        if existing is not None:
            existing[1] += resolved_count
            return
        if len(self._gap_segments) >= self._gap_capacity:
            self._gap_overflow_count += resolved_count
            self._gap_overflow_boundary = max(
                self._gap_overflow_boundary,
                resolved_boundary,
            )
            return
        self._gap_segments[key] = [
            resolved_boundary,
            resolved_count,
            normalized_routes,
        ]

    def _record_observer_gap(
        self,
        count: int,
        routes: Any,
        *,
        scope_truncated: bool,
    ) -> None:
        try:
            record_gap = getattr(
                self._observer,
                "record_observation_gap",
                None,
            )
        except Exception:
            return
        if not callable(record_gap):
            return
        self._callback_started()
        try:
            try:
                record_gap(
                    count,
                    affected_routes=routes,
                    scope_truncated=scope_truncated,
                )
            except TypeError:
                try:
                    record_gap(count, affected_routes=routes)
                except TypeError:
                    record_gap(count)
        except Exception:
            pass
        finally:
            self._callback_finished()

    def _notification_finished(self, ordinal: int) -> None:
        with self._gap_lock:
            self._last_finished_notification_ordinal = max(
                self._last_finished_notification_ordinal,
                int(ordinal),
            )

    def _flush_ready_gaps(self) -> bool:
        ready: list[tuple[int, int, Any, bool]] = []
        with self._gap_lock:
            finished = self._last_finished_notification_ordinal
            for key, segment in tuple(self._gap_segments.items()):
                boundary, count, routes = segment
                if boundary > finished:
                    continue
                self._gap_segments.pop(key, None)
                ready.append((boundary, count, routes, False))
            if (
                self._gap_overflow_count
                and self._gap_overflow_boundary <= finished
            ):
                ready.append(
                    (
                        self._gap_overflow_boundary,
                        self._gap_overflow_count,
                        (),
                        True,
                    )
                )
                self._gap_overflow_count = 0
                self._gap_overflow_boundary = 0
        ready.sort(key=lambda item: (item[0], item[3]))
        for _boundary, count, routes, scope_truncated in ready:
            self._record_observer_gap(
                count,
                routes,
                scope_truncated=scope_truncated,
            )
        return bool(ready)


class CodexAppServerClient:
    """One long-lived stdio Codex App Server connection for the local UI."""

    def __init__(
        self,
        repo_root: Path,
        *,
        binary_path: Path | None = None,
        request_timeout: int = 30,
        turn_timeout: int = DEFAULT_TURN_TIMEOUT_SECONDS,
        structured_output_stall_timeout: float = (
            STRUCTURED_OUTPUT_STALL_TIMEOUT_SECONDS
        ),
        structured_output_completion_grace: float = (
            STRUCTURED_OUTPUT_COMPLETION_GRACE_SECONDS
        ),
        status_cache_seconds: float = SUBSCRIPTION_STATUS_CACHE_SECONDS,
        turn_budget: GlobalTurnBudget | None = None,
        control_plane_budget: GlobalTurnBudget | None = None,
        observer: Any | None = None,
        monitor_context: Mapping[str, Any] | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.binary_path = self._resolve_binary(binary_path)
        self.request_timeout = request_timeout
        self.turn_timeout = turn_timeout
        self.structured_output_stall_timeout = max(
            0.0,
            float(structured_output_stall_timeout),
        )
        self.structured_output_completion_grace = max(
            0.0,
            float(structured_output_completion_grace),
        )
        self.status_cache_seconds = status_cache_seconds
        self.provider = APP_SERVER_PROVIDER
        self.provider_retry_attempts = PROVIDER_RECOVERY_ATTEMPTS
        self.turn_budget = turn_budget or GlobalTurnBudget(GLOBAL_TURN_CAPACITY)
        self.control_plane_budget = control_plane_budget or GlobalTurnBudget(
            APP_SERVER_CONTROL_PLANE_CAPACITY
        )
        self._observer = observer
        self._monitor_observer_adapter = _NonBlockingObserverAdapter(observer)
        self._monitor_context = dict(monitor_context or {})

        self._process: subprocess.Popen[str] | None = None
        self._stdin: TextIO | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._write_lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._status_refresh_lock = threading.Lock()
        self._hook_check_locks: dict[tuple[int, str], threading.Lock] = {}
        self._hook_check_cache: dict[tuple[int, str], dict[str, Any]] = {}
        self._app_server_generation = 0
        self._next_id = 1
        self._pending: dict[int | str, _PendingResponse] = {}
        self._turns: dict[tuple[str, str], _TurnState] = {}
        self._notified_active_turns: set[tuple[str, str]] = set()
        self._notified_turn_started: dict[tuple[str, str], tuple[str, float]] = {}
        self._peak_active_turns = 0
        self._early_notifications: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._stderr_lines: deque[str] = deque(maxlen=80)
        self._closed = False
        self._initialized = False
        self._observation_connection_lost = False
        self._last_status: dict[str, Any] | None = None
        self._last_status_at = 0.0
        self._last_status_speed_mode: str | None = None
        self._effective_model = ""
        self._configured_reasoning_effort = ""
        self._source_codex_home = Path(
            os.environ.get("CODEX_HOME") or Path.home() / ".codex"
        ).resolve()
        self._runtime_home_context: tempfile.TemporaryDirectory[str] | None = None
        self._runtime_home: Path | None = None
        self._isolated_model_workspace: Path | None = None
        self._runtime_diagnostic_phase: str | None = None
        self._runtime_diagnostic_rule: str | None = None

    def _mark_runtime_diagnostic(self, phase: str, rule: str) -> None:
        """Record only fixed, non-sensitive startup diagnostic identifiers."""
        if (phase, rule) not in RUNTIME_DIAGNOSTIC_PAIRS:
            self._runtime_diagnostic_phase = None
            self._runtime_diagnostic_rule = None
            raise SubscriptionGateError(
                "起動診断の固定識別子を安全に確認できません。"
            )
        self._runtime_diagnostic_phase = phase
        self._runtime_diagnostic_rule = rule

    @property
    def configured(self) -> bool:
        return self.binary_path is not None

    def public_status(
        self,
        *,
        refresh: bool = False,
        speed_mode: str = STANDARD_SPEED_MODE,
    ) -> dict[str, Any]:
        speed_mode = normalize_speed_mode(speed_mode)
        turn_budget = self.turn_budget.snapshot()
        control_plane_budget = self.control_plane_budget.snapshot()
        model_turns = self._model_turn_snapshot()
        if not self.configured:
            return {
                "available": False,
                "allowed": False,
                "provider": self.provider,
                "reason": "Codex App Server binaryが見つかりません。",
                "turnBudget": turn_budget,
                "controlPlaneBudget": control_plane_budget,
                "modelTurns": model_turns,
            }
        if not refresh:
            with self._state_lock:
                cached = (
                    copy.deepcopy(self._last_status)
                    if self._last_status is not None
                    else None
                )
                cached_at = self._last_status_at
            if cached is None:
                cached = {
                    "allowed": None,
                    "reason": (
                        "実行開始時にChatGPT認証と利用上限を確認します。"
                    ),
                }
            return {
                "available": True,
                "provider": self.provider,
                **cached,
                "statusCached": cached_at > 0,
                "turnBudget": turn_budget,
                "controlPlaneBudget": control_plane_budget,
                "modelTurns": model_turns,
            }
        try:
            return {
                "available": True,
                "provider": self.provider,
                **self.assert_subscription_access(
                    force=refresh,
                    speed_mode=speed_mode,
                ),
                "turnBudget": self.turn_budget.snapshot(),
                "controlPlaneBudget": self.control_plane_budget.snapshot(),
                "modelTurns": self._model_turn_snapshot(),
            }
        except CodexAppServerError as exc:
            return {
                "available": True,
                "allowed": False,
                "provider": self.provider,
                "reason": str(exc),
                "turnBudget": self.turn_budget.snapshot(),
                "controlPlaneBudget": self.control_plane_budget.snapshot(),
                "modelTurns": self._model_turn_snapshot(),
            }

    def diagnose_subscription_access(self) -> dict[str, Any]:
        """Run one fail-closed subscription diagnostic without raw error data."""

        base: dict[str, Any] = {
            "stage": "binary",
            "allowed": False,
            "failureKind": None,
            "rpcMethod": None,
            "rpcCode": None,
            "rpcDataType": None,
            "runtimePhase": None,
            "runtimeRule": None,
        }
        if not self.configured:
            return {**base, "failureKind": "binary_missing"}

        try:
            self._ensure_started()
        except CodexAppServerError as exc:
            return self._subscription_diagnostic_failure(
                "runtime_initialize", exc
            )

        try:
            account = self._request("account/read", {"refreshToken": False})
        except CodexAppServerError as exc:
            return self._subscription_diagnostic_failure("account_read", exc)

        try:
            rate_limits = self._request("account/rateLimits/read", None)
        except CodexAppServerError as exc:
            return self._subscription_diagnostic_failure(
                "rate_limits_read", exc
            )

        try:
            status = validate_subscription_access(
                _as_mapping(account, "Codex account response"),
                _as_mapping(rate_limits, "Codex rate limit response"),
            )
        except SubscriptionGateError as exc:
            return self._subscription_diagnostic_failure(
                "subscription_validation", exc
            )

        return {
            **base,
            "stage": "complete",
            "allowed": True,
            "accountType": status["accountType"],
            "planType": status["planType"],
            "creditsEnabled": status["creditsEnabled"],
            "standardMode": status["standardMode"],
            "fastMode": status["fastMode"],
        }

    def _subscription_diagnostic_failure(
        self,
        stage: str,
        error: CodexAppServerError,
    ) -> dict[str, Any]:
        rpc_method = error.method if isinstance(error, CodexRpcError) else None
        rpc_code = error.code if isinstance(error, CodexRpcError) else None
        rpc_data_type = (
            error.data_type if isinstance(error, CodexRpcError) else None
        )
        runtime_phase = (
            self._runtime_diagnostic_phase
            if stage == "runtime_initialize"
            and isinstance(error, SubscriptionGateError)
            else None
        )
        runtime_rule = (
            self._runtime_diagnostic_rule
            if stage == "runtime_initialize"
            and isinstance(error, SubscriptionGateError)
            else None
        )
        if isinstance(error, CodexProcessExitError):
            failure_kind = "process_exit"
        elif isinstance(error, CodexRequestTimeoutError):
            failure_kind = "timeout"
        elif isinstance(error, SubscriptionGateError):
            failure_kind = "subscription_validation_failed"
        elif rpc_code == -32601:
            failure_kind = "rpc_method_not_found"
        elif rpc_code == -32602:
            failure_kind = "invalid_params"
        elif rpc_code in {"auth_required", "session_expired"}:
            failure_kind = str(rpc_code)
        elif rpc_code == "service_unavailable":
            failure_kind = "service_unavailable"
        elif rpc_code == "quota_reached":
            failure_kind = "quota_reached"
        elif rpc_code == "credits_enabled":
            failure_kind = "credits_enabled"
        else:
            failure_kind = "transport_failure"
        return {
            "stage": stage,
            "allowed": False,
            "failureKind": failure_kind,
            "rpcMethod": rpc_method,
            "rpcCode": rpc_code,
            "rpcDataType": rpc_data_type,
            "runtimePhase": runtime_phase,
            "runtimeRule": runtime_rule,
        }

    def _model_turn_snapshot(self) -> dict[str, int]:
        with self._state_lock:
            return {
                "capacity": self.turn_budget.capacity,
                "inFlight": len(self._notified_active_turns),
                "peakInFlight": self._peak_active_turns,
            }

    def assert_subscription_access(
        self,
        *,
        force: bool = True,
        speed_mode: str = STANDARD_SPEED_MODE,
    ) -> dict[str, Any]:
        speed_mode = normalize_speed_mode(speed_mode)
        requested_at = time.monotonic()
        with self._state_lock:
            if (
                not force
                and self._last_status is not None
                and self._last_status_speed_mode == speed_mode
                and requested_at - self._last_status_at <= self.status_cache_seconds
            ):
                return copy.deepcopy(self._last_status)
        # 同時に始まるturnは、最初の1本が取得した直近値を共有する。
        # account/readを一斉に重複実行してtimeoutさせない。
        with self._status_refresh_lock:
            with self._state_lock:
                refreshed_after_request = (
                    self._last_status is not None
                    and self._last_status_speed_mode == speed_mode
                    and self._last_status_at >= requested_at
                )
                cache_is_fresh = (
                    self._last_status is not None
                    and self._last_status_speed_mode == speed_mode
                    and time.monotonic() - self._last_status_at
                    <= self.status_cache_seconds
                )
                if refreshed_after_request or (not force and cache_is_fresh):
                    return copy.deepcopy(self._last_status)
            self._ensure_started()

            def read_status(method: str, params: Mapping[str, Any] | None) -> Any:
                last_error: CodexAppServerError | None = None
                for attempt in range(SUBSCRIPTION_STATUS_READ_ATTEMPTS):
                    try:
                        return self._request(method, params)
                    except CodexAppServerError as exc:
                        last_error = exc
                        if attempt + 1 < SUBSCRIPTION_STATUS_READ_ATTEMPTS:
                            time.sleep(
                                SUBSCRIPTION_STATUS_READ_RETRY_DELAY_SECONDS
                                * (2**attempt)
                            )
                assert last_error is not None
                raise last_error

            account = read_status("account/read", {"refreshToken": False})
            rate_limits = read_status("account/rateLimits/read", None)
            status = validate_subscription_access(
                _as_mapping(account, "Codex account response"),
                _as_mapping(rate_limits, "Codex rate limit response"),
                speed_mode=speed_mode,
            )
            status.update(
                {
                    "model": QUESTION_MAINTENANCE_MODEL,
                    "retryModel": QUESTION_MAINTENANCE_RETRY_MODEL,
                    "configuredModel": self._effective_model,
                    "configuredReasoningEffort": self._configured_reasoning_effort,
                    "turnReasoningEffort": TURN_REASONING_EFFORT,
                }
            )
            with self._state_lock:
                self._last_status = dict(status)
                self._last_status_at = time.monotonic()
                self._last_status_speed_mode = speed_mode
            return status

    def run_turn(
        self,
        prompt: str,
        *,
        work_type: str,
        sandbox: str,
        emit: Callable[[str], None],
        output_schema: Mapping[str, Any] | None = None,
        on_thread_started: Callable[[str, str], None] | None = None,
        on_turn_started: Callable[[str, str], None] | None = None,
        on_model_turn_event: Callable[[Mapping[str, Any]], None] | None = None,
        cwd: Path | None = None,
        writable_roots: Iterable[Path] = (),
        completion_probe: Callable[[], bool] | None = None,
        heartbeat: Callable[[], None] | None = None,
        model: str = QUESTION_MAINTENANCE_MODEL,
        reasoning_effort: str = TURN_REASONING_EFFORT,
        speed_mode: str = STANDARD_SPEED_MODE,
        turn_group: str | None = None,
        monitor_context: Mapping[str, Any] | None = None,
        turn_timeout: float | None = None,
    ) -> AppServerTurnResult:
        return self._run_turn_unbudgeted(
            prompt,
            work_type=work_type,
            sandbox=sandbox,
            emit=emit,
            output_schema=output_schema,
            on_thread_started=on_thread_started,
            on_turn_started=on_turn_started,
            on_model_turn_event=on_model_turn_event,
            cwd=cwd,
            writable_roots=writable_roots,
            completion_probe=completion_probe,
            heartbeat=heartbeat,
            model=model,
            reasoning_effort=reasoning_effort,
            speed_mode=speed_mode,
            turn_group=turn_group,
            monitor_context=monitor_context,
            turn_timeout=turn_timeout,
        )

    def turn_group(self, qualification: str):
        return self.turn_budget.register(qualification)

    def _run_turn_unbudgeted(
        self,
        prompt: str,
        *,
        work_type: str,
        sandbox: str,
        emit: Callable[[str], None],
        output_schema: Mapping[str, Any] | None = None,
        on_thread_started: Callable[[str, str], None] | None = None,
        on_turn_started: Callable[[str, str], None] | None = None,
        on_model_turn_event: Callable[[Mapping[str, Any]], None] | None = None,
        cwd: Path | None = None,
        writable_roots: Iterable[Path] = (),
        completion_probe: Callable[[], bool] | None = None,
        heartbeat: Callable[[], None] | None = None,
        model: str = QUESTION_MAINTENANCE_MODEL,
        reasoning_effort: str = TURN_REASONING_EFFORT,
        speed_mode: str = STANDARD_SPEED_MODE,
        turn_group: str | None = None,
        monitor_context: Mapping[str, Any] | None = None,
        turn_timeout: float | None = None,
    ) -> AppServerTurnResult:
        turn_requested_monotonic = time.monotonic()
        speed_mode = normalize_speed_mode(speed_mode)
        resolved_turn_timeout = float(self.turn_timeout)
        if turn_timeout is not None:
            requested_turn_timeout = float(turn_timeout)
            if (
                not math.isfinite(requested_turn_timeout)
                or requested_turn_timeout <= 0
            ):
                raise ValueError("turn timeout must be finite and positive")
            resolved_turn_timeout = min(
                resolved_turn_timeout,
                requested_turn_timeout,
            )
        requested_service_tier = None
        if sandbox not in {"read-only", "workspace-write"}:
            raise ValueError(f"unsupported sandbox: {sandbox}")
        if model not in QUESTION_MAINTENANCE_MODELS:
            raise ValueError(f"unsupported maintenance model: {model}")
        if reasoning_effort != TURN_REASONING_EFFORT:
            raise ValueError(
                f"unsupported maintenance reasoning effort: {reasoning_effort}"
            )
        evaluation_work = work_type in {"evaluation", "reevaluation"}
        research_work = work_type == "maintenance_research"
        official_source_work = work_type == "official_source_review"
        instruction_candidate_work = work_type == "maintenance_instruction_candidate"
        candidate_work = work_type.startswith("maintenance_") and work_type.endswith(
            "_candidate"
        )
        read_only_work = (
            evaluation_work
            or research_work
            or official_source_work
            or candidate_work
        )
        model_only_work = evaluation_work or candidate_work
        # 同じwaveのturnは直前60秒以内に取得した一つの検証結果を共有する。
        # cacheが無い又は期限切れなら最初のturnだけが再取得し、後続は
        # single-flight結果を待つ。UIのrun開始時確認とは独立にfail-closed。
        self.assert_subscription_access(force=False, speed_mode=speed_mode)
        # 構造化候補と評価のpromptは一問分の入力と品質規則を自己完結で持つ。
        # repositoryをcwdにすると64 threadが同時にworkspace初期化を行うため、
        # modelだけが判断するturnは空の隔離workspaceから起動する。
        turn_cwd = (
            self._isolated_model_cwd()
            if model_only_work
            else (cwd or self.repo_root).resolve()
        )
        resolved_writable_roots = tuple(
            dict.fromkeys(Path(path).resolve() for path in writable_roots)
        )
        if sandbox == "read-only" and resolved_writable_roots:
            raise ValueError("read-only sandboxにはwritable rootを指定できません。")
        if any(
            not path.is_relative_to(self.repo_root)
            for path in resolved_writable_roots
        ):
            raise ValueError("writable rootはrepository内に限定してください。")
        approval_policy = "never"
        config = {
            "features": {
                **{name: False for name in DISABLED_EXTERNAL_FEATURES},
                "fast_mode": False,
                "multi_agent": False,
            },
            "agents": {
                "max_threads": APP_SERVER_AGENT_THREAD_CAP,
                "max_depth": APP_SERVER_AGENT_MAX_DEPTH,
            },
            "service_tier": requested_service_tier,
            "web_search": "live",
        }
        if evaluation_work:
            developer_instructions = (
                "このthreadは問題品質の客観評価専用である。過去thread、memory、整備会話を参照せず、"
                "入力された現在の1問だけを評価する。subagentは使わない。file又は外部状態を変更しない。"
            )
        elif research_work:
            developer_instructions = (
                "このthreadは問題整備のread-only事前調査専用である。file又は外部状態を変更しない。"
                "subagentは使わず、対象問題の根拠と問題IDごとの最終判断案を一つのthreadで返す。"
                "思考過程は返さない。"
            )
        elif official_source_work:
            developer_instructions = (
                "このthreadは公式問題冊子とのread-only照合専用である。"
                "指定されたrepository内の公式資料と入力JSONだけを確認し、"
                "file又は外部状態を変更しない。subagentは使わず、"
                "指定された構造化JSONだけを返す。思考過程は返さない。"
            )
        elif instruction_candidate_work:
            developer_instructions = (
                "このthreadは自然言語の問題整備指示を構造化計画へ変換する専用である。"
                "subagentは使わず、file、command、外部状態を変更しない。"
                "指定されたJSON Schemaと許可IDに一致する最終objectだけを返す。"
                "開始、検証、patch反映はserverが行う。思考過程は返さない。"
            )
        elif candidate_work:
            developer_instructions = (
                "このthreadは問題整備の構造化候補生成専用である。subagentは使わない。"
                "file、command、外部状態を変更せず、入力された各問題を独立に判断する。"
                "許可fieldだけを使い、指定されたJSON Schemaに一致する最終objectだけを返す。"
                "識別、検証、patch反映、progress、receiptはserverが行う。思考過程は返さない。"
            )
        else:
            developer_instructions = (
                "このthreadは問題整備の保存専用である。subagentは使わない。"
                "00_sourceと既存IDを変更せず、責務に合うpatchだけを変更する。"
                "merge、convert、upload-ready生成は別工程に残す。git add、commit、pushは行わず、"
                "Firestore、Storage、GitHub等の外部状態は変更しない。"
            )
        control_heartbeat = heartbeat or getattr(emit, "heartbeat", None)
        self._assert_no_active_hooks(
            turn_cwd,
            turn_group=turn_group,
            heartbeat=control_heartbeat,
        )
        if research_work:
            self._assert_no_custom_agents(turn_cwd)
        thread_response = self._control_request(
            "thread/start",
            {
                "cwd": str(turn_cwd),
                "model": model,
                "modelProvider": "openai",
                "approvalPolicy": approval_policy,
                "approvalsReviewer": "user",
                "sandbox": sandbox,
                "serviceTier": requested_service_tier,
                "config": config,
                "developerInstructions": developer_instructions,
                "ephemeral": read_only_work,
                "threadSource": f"exam_scraper_{work_type}",
            },
            turn_group=turn_group,
            heartbeat=control_heartbeat,
        )
        thread_response = _as_mapping(thread_response, "thread/start response")
        thread = _as_mapping(thread_response.get("thread"), "thread")
        thread_id = str(thread.get("id") or "")
        session_id = str(thread.get("sessionId") or "")
        if not thread_id or not session_id:
            raise CodexAppServerError("Codex App Serverがthread又はsession IDを返しませんでした。")
        runtime_monitor_context = {
            **self._monitor_context,
            **dict(monitor_context or {}),
            "sessionId": session_id,
        }
        self._bind_monitor_runtime(runtime_monitor_context, thread_id)
        if on_thread_started is not None:
            on_thread_started(thread_id, session_id)
        service_tier = thread_response.get("serviceTier")
        if service_tier not in {None, "default", "standard"}:
            raise SubscriptionGateError("要求したStandard modeが適用されませんでした。")
        model_provider = str(thread_response.get("modelProvider") or "")
        if model_provider != "openai":
            raise SubscriptionGateError("外部model providerでは実行しません。")
        actual_model = str(thread_response.get("model") or "")
        if actual_model != model:
            raise SubscriptionGateError(
                f"指定model {model}が適用されませんでした。"
            )
        sandbox_response = _as_mapping(thread_response.get("sandbox"), "sandbox response")
        expected_sandbox = "readOnly" if sandbox == "read-only" else "workspaceWrite"
        if sandbox_response.get("type") != expected_sandbox:
            raise CodexAppServerError("要求したsandboxが適用されませんでした。")
        if sandbox_response.get("networkAccess") is not False:
            raise CodexAppServerError("commandのnetwork無効化を確認できません。")
        self._assert_no_external_mcp(
            thread_id,
            turn_group=turn_group,
            heartbeat=control_heartbeat,
        )

        sandbox_policy: dict[str, Any]
        if sandbox == "read-only":
            sandbox_policy = {"type": "readOnly", "networkAccess": False}
        else:
            sandbox_policy = {
                "type": "workspaceWrite",
                "writableRoots": [str(path) for path in resolved_writable_roots],
                "networkAccess": False,
                "excludeTmpdirEnvVar": True,
                "excludeSlashTmp": True,
            }
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt, "text_elements": []}],
            "cwd": str(turn_cwd),
            "approvalPolicy": approval_policy,
            "approvalsReviewer": "user",
            "sandboxPolicy": sandbox_policy,
            "serviceTier": requested_service_tier,
            "effort": reasoning_effort,
        }
        if output_schema is not None:
            params["outputSchema"] = adapt_output_schema_for_app_server(output_schema)
        heartbeat_callback = heartbeat or getattr(emit, "heartbeat", None)
        with self.turn_budget.slot(
            turn_group,
            heartbeat=heartbeat_callback,
        ):
            try:
                turn_response = self._control_request(
                    "turn/start",
                    params,
                    turn_group=turn_group,
                    heartbeat=control_heartbeat,
                )
                turn_response = _as_mapping(
                    turn_response,
                    "turn/start response",
                )
                turn = _as_mapping(turn_response.get("turn"), "turn")
                turn_id = str(turn.get("id") or "")
                if not turn_id:
                    raise CodexAppServerError(
                        "Codex App Serverがturn IDを返しませんでした。"
                    )
                self._bind_monitor_runtime(
                    runtime_monitor_context,
                    thread_id,
                    turn_id,
                )
            except Exception:
                self._interrupt_active_turns(
                    thread_id,
                    on_turn_started,
                )
                raise
            state = _TurnState(
                thread_id=thread_id,
                turn_id=turn_id,
                emit=emit,
                structured_output=output_schema is not None,
                requested_monotonic=turn_requested_monotonic,
                on_model_turn_event=on_model_turn_event,
            )
            key = (thread_id, turn_id)
            with self._state_lock:
                notified_started = self._notified_turn_started.get(key)
                if notified_started is not None:
                    (
                        state.protocol_started_at,
                        state.protocol_started_monotonic,
                    ) = notified_started
                self._turns[key] = state
                early = self._early_notifications.pop(key, [])
            try:
                if state.protocol_started_monotonic is not None:
                    self._emit_model_turn_event(state, "started")
                for notification in early:
                    self._handle_turn_notification(notification)
                if on_turn_started is not None:
                    on_turn_started(thread_id, turn_id)
                emit(f"Codex App Server thread: {thread_id}")

                receipt_interrupted = False
                completed_message_interrupted = False
                deadline = time.monotonic() + resolved_turn_timeout
                next_heartbeat = (
                    time.monotonic() + TURN_HEARTBEAT_INTERVAL_SECONDS
                    if callable(heartbeat_callback)
                    else math.inf
                )
                while not state.event.is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise CodexTurnTimeoutError(
                            "Codex App Serverのturnが時間切れになりました。"
                        )
                    heartbeat_wait = max(
                        0.0,
                        next_heartbeat - time.monotonic(),
                    )
                    if state.event.wait(
                        min(0.25, remaining, heartbeat_wait)
                    ):
                        break
                    now = time.monotonic()
                    if (
                        state.structured_output
                        and state.last_semantic_delta_at is not None
                        and state.trailing_whitespace_chars
                        >= STRUCTURED_OUTPUT_TRAILING_WHITESPACE_CHARS
                        and now - state.last_semantic_delta_at
                        >= self.structured_output_stall_timeout
                    ):
                        raise CodexTurnTimeoutError(
                            "Codex App Serverの構造化応答が、実質的な"
                            "出力進捗のない空白生成で停止しました。"
                        )
                    if (
                        state.structured_output
                        and state.completed_message_at is not None
                        and now - state.completed_message_at
                        >= self.structured_output_completion_grace
                    ):
                        completed_message_interrupted = True
                        emit(
                            "完成した構造化応答を受信したため、欠落した"
                            "turn完了通知を待たず最終検証へ進みます。"
                        )
                        self._interrupt_turn(thread_id, turn_id)
                        if not state.event.wait(30):
                            raise CodexAppServerError(
                                "構造化応答受信後のturn停止を確認できませんでした。"
                            )
                        break
                    if now >= next_heartbeat:
                        try:
                            heartbeat_callback()
                        except Exception:
                            pass
                        next_heartbeat = (
                            now + TURN_HEARTBEAT_INTERVAL_SECONDS
                        )
                    if completion_probe is None or not completion_probe():
                        continue
                    receipt_interrupted = True
                    emit(
                        "成功receiptを検出したため、追加操作を止めて"
                        "最終検証へ進みます。"
                    )
                    self._interrupt_turn(thread_id, turn_id)
                    if not state.event.wait(30):
                        raise CodexAppServerError(
                            "成功receipt保存後のturn停止を確認できませんでした。"
                        )
                    break
            except BaseException:
                self._interrupt_turn(thread_id, turn_id)
                raise
            finally:
                with self._state_lock:
                    self._turns.pop(key, None)
                    self._notified_active_turns.discard(key)
                    self._notified_turn_started.pop(key, None)
        receipt_interrupted = bool(
            receipt_interrupted and state.status == "interrupted"
        )
        completed_message_interrupted = bool(
            completed_message_interrupted and state.status == "interrupted"
        )
        if (
            state.status != "completed"
            and not receipt_interrupted
            and not completed_message_interrupted
        ):
            detail = self._turn_error_message(state.error)
            if (
                state.status == "failed"
                and state.protocol_finished_monotonic is not None
            ):
                raise CodexTerminalTurnFailedError(
                    "Codex App Serverのturnを完了できませんでした"
                    f"（{state.status}）{detail}",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    status=state.status,
                    error=state.error,
                )
            raise CodexAppServerError(
                f"Codex App Serverのturnを完了できませんでした（{state.status}）{detail}"
            )
        final_message = next(
            (
                message
                for phase, message in reversed(state.messages)
                if phase == "final_answer" and message.strip()
            ),
            next(
                (message for _phase, message in reversed(state.messages) if message.strip()),
                "",
            ),
        )
        if receipt_interrupted and not final_message:
            final_message = "成功receipt保存後にturnを停止しました。"
        if not final_message:
            raise CodexAppServerError("Codex App Serverが最終応答を返しませんでした。")
        if research_work:
            if len(state.subagent_thread_ids) > MAINTENANCE_RESEARCH_WORKERS:
                raise CodexAppServerError(
                    "read-only調査subagentが上限を超えました。"
                )
            if state.subagent_thread_ids and (
                not state.subagent_models
                or not state.subagent_reasoning_efforts
            ):
                raise SubscriptionGateError(
                    "read-only調査subagentのmodel又は推論強度を確認できません。"
                )
            unexpected_models = state.subagent_models - {model}
            if unexpected_models:
                raise SubscriptionGateError(
                    "read-only調査subagentで指定外modelを検出しました: "
                    + ", ".join(sorted(unexpected_models))
                )
            unexpected_efforts = state.subagent_reasoning_efforts - {
                reasoning_effort
            }
            if unexpected_efforts:
                raise SubscriptionGateError(
                    "read-only調査subagentで指定外の推論強度を検出しました: "
                    + ", ".join(sorted(unexpected_efforts))
                )
        if official_source_work and state.subagent_thread_ids:
            raise SubscriptionGateError(
                "公式問題冊子の照合threadでsubagentを検出しました。"
            )
        return AppServerTurnResult(
            thread_id=thread_id,
            session_id=session_id,
            turn_id=turn_id,
            final_message=final_message,
            model=actual_model,
            service_tier=service_tier if isinstance(service_tier, str) else None,
            reasoning_effort=reasoning_effort,
            changed_files=tuple(sorted(state.changed_files)),
            subagent_thread_ids=tuple(sorted(state.subagent_thread_ids)),
            subagent_models=tuple(sorted(state.subagent_models)),
            subagent_reasoning_efforts=tuple(
                sorted(state.subagent_reasoning_efforts)
            ),
            completion_mode=(
                "receipt_interrupted"
                if receipt_interrupted
                else "completed_message_interrupted"
                if completed_message_interrupted
                else "turn_completed"
            ),
            model_turn_started_at=state.protocol_started_at,
            model_turn_finished_at=state.protocol_finished_at,
            model_turn_duration_seconds=(
                round(
                    state.protocol_finished_monotonic
                    - state.protocol_started_monotonic,
                    6,
                )
                if state.protocol_started_monotonic is not None
                and state.protocol_finished_monotonic is not None
                else None
            ),
            model_turn_queue_wait_seconds=(
                round(
                    state.protocol_started_monotonic
                    - turn_requested_monotonic,
                    6,
                )
                if state.protocol_started_monotonic is not None
                else None
            ),
        )

    def close(self) -> None:
        with self._lifecycle_lock:
            self._closed = True
            process = self._process
            stream = self._stdin
            runtime_home_context = self._runtime_home_context
            self._process = None
            self._stdin = None
            self._runtime_home_context = None
            self._runtime_home = None
            self._isolated_model_workspace = None
        self._stop_process(process, stream)
        self._fail_all("Codex App Serverを停止しました。")
        self._monitor_observer_adapter.close()
        if runtime_home_context is not None:
            runtime_home_context.cleanup()

    def recover_after_provider_failure(
        self,
        *,
        attempt: int,
        emit: Callable[[str], None] | None = None,
    ) -> None:
        """Recreate the stdio client after a transient provider failure.

        Subscription access remains fail-closed: the next turn still performs
        the normal forced account/rate-limit check.  This method only prevents
        a stale connection or a short provider outage from consuming all queue
        retries without a recovery interval.
        """

        retry_number = max(1, int(attempt))
        delay = min(
            PROVIDER_RECOVERY_MAX_DELAY_SECONDS,
            PROVIDER_RECOVERY_BASE_DELAY_SECONDS * (2 ** (retry_number - 1)),
        )
        with self._lifecycle_lock:
            if self._closed:
                raise CodexAppServerError("Codex App Server clientは停止済みです。")
            process = self._process
            stream = self._stdin
            self._process = None
            self._stdin = None
            self._initialized = False
            self._observation_connection_lost = False
        with self._state_lock:
            self._last_status = None
            self._last_status_at = 0.0
            self._last_status_speed_mode = None
        self._stop_process(process, stream)
        self._fail_all("外部障害からの回復のためCodex App Server接続を更新します。")
        try:
            # The Python server remains alive, but notifications emitted while
            # the App Server connection is being replaced are unknowable.
            # Preserve that distinction as an observation gap; never let the
            # optional monitor affect provider recovery.
            self._monitor_observer_adapter.record_observation_gap()
        except Exception:  # noqa: BLE001 - monitoring is best effort.
            pass
        if emit is not None:
            emit(
                "Codex App Serverの一時障害を検出したため、"
                f"接続を更新して{int(delay)}秒後に再試行します。"
            )
        time.sleep(delay)
        self._ensure_started()

    def _prepare_isolated_codex_home(self) -> Path:
        if self._runtime_home is not None:
            return self._runtime_home
        source_auth = self._source_codex_home / "auth.json"
        if not source_auth.is_file():
            raise SubscriptionGateError(
                "ChatGPT認証情報を隔離実行環境へ準備できません。"
            )
        context = tempfile.TemporaryDirectory(prefix="question-review-codex-home-")
        runtime_home = Path(context.name).resolve()
        try:
            runtime_home.chmod(0o700)
            runtime_auth = runtime_home / "auth.json"
            shutil.copyfile(source_auth, runtime_auth)
            runtime_auth.chmod(0o600)
            isolated_model_workspace = runtime_home / "model-workspace"
            isolated_model_workspace.mkdir(mode=0o700)
        except OSError as exc:
            context.cleanup()
            raise SubscriptionGateError(
                "ChatGPT認証情報を隔離実行環境へ準備できません。"
            ) from exc
        self._runtime_home_context = context
        self._runtime_home = runtime_home
        self._isolated_model_workspace = isolated_model_workspace
        return runtime_home

    def _isolated_model_cwd(self) -> Path:
        if self._isolated_model_workspace is None:
            self._prepare_isolated_codex_home()
        path = self._isolated_model_workspace
        runtime_home = self._runtime_home
        if (
            path is None
            or runtime_home is None
            or path.is_symlink()
            or not path.is_dir()
            or path.parent.resolve() != runtime_home.resolve()
            or stat.S_IMODE(path.stat().st_mode) != 0o700
        ):
            raise SubscriptionGateError(
                "read-only model turnの隔離workspaceを安全に確認できません。"
            )
        return path.resolve()

    def _trusted_research_agent_config(self) -> Path:
        runtime_home = self._runtime_home
        if runtime_home is None:
            raise SubscriptionGateError(
                "read-only調査agentの隔離設定を準備できません。"
            )
        path = runtime_home / RESEARCH_AGENT_CONFIG_FILENAME
        try:
            if path.is_symlink():
                raise SubscriptionGateError(
                    "read-only調査agentの隔離設定pathが不正です。"
                )
            if not path.exists():
                path.write_text(RESEARCH_AGENT_CONFIG, encoding="utf-8")
                path.chmod(0o600)
            if (
                path.is_symlink()
                or not path.is_file()
                or path.parent.resolve() != runtime_home.resolve()
                or stat.S_IMODE(path.stat().st_mode) != 0o600
            ):
                raise SubscriptionGateError(
                    "read-only調査agentの隔離設定を安全に確認できません。"
                )
            content = path.read_text(encoding="utf-8")
            if content != RESEARCH_AGENT_CONFIG:
                raise SubscriptionGateError(
                    "read-only調査agentの隔離設定を安全に確認できません。"
                )
            parsed = tomllib.loads(content)
        except SubscriptionGateError:
            raise
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise SubscriptionGateError(
                "read-only調査agentの隔離設定を安全に確認できません。"
            ) from exc
        if parsed != {
            "name": RESEARCH_AGENT_ROLE,
            "description": RESEARCH_AGENT_DESCRIPTION,
            "developer_instructions": RESEARCH_AGENT_DEVELOPER_INSTRUCTIONS,
            "model": QUESTION_MAINTENANCE_MODEL,
            "model_reasoning_effort": TURN_REASONING_EFFORT,
            "sandbox_mode": "read-only",
            "features": {"multi_agent": False},
        }:
            raise SubscriptionGateError(
                "read-only調査agentの許可fieldを確認できません。"
            )
        return path

    def _ensure_started(self) -> None:
        if self.binary_path is None:
            raise CodexAppServerError("Codex App Server binaryが見つかりません。")
        with self._lifecycle_lock:
            if self._closed:
                raise CodexAppServerError("Codex App Server clientは停止済みです。")
            if self._process is not None and self._process.poll() is None and self._initialized:
                return
            previous_process = self._process
            previous_stream = self._stdin
            continuity_gap = self._observation_connection_lost
            if previous_process is not None:
                continuity_gap = True
                self._process = None
                self._stdin = None
                self._initialized = False
                self._stop_process(previous_process, previous_stream)
                self._fail_all("前回のCodex App Server接続が終了しました。")
            self._observation_connection_lost = False
            if continuity_gap:
                try:
                    self._monitor_observer_adapter.record_observation_gap()
                except Exception:  # noqa: BLE001 - monitoring is best effort.
                    pass
            env = dict(os.environ)
            for key in API_CREDENTIAL_ENV_VARS:
                env.pop(key, None)
            self._mark_runtime_diagnostic("runtime_environment", "auth_isolation")
            runtime_home = self._prepare_isolated_codex_home()
            env["CODEX_HOME"] = str(runtime_home)
            ensure_app_server_file_descriptor_capacity()
            try:
                process = subprocess.Popen(
                    self._app_server_command(),
                    cwd=runtime_home,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    bufsize=1,
                    env=env,
                )
            except OSError as exc:
                raise CodexAppServerError(f"Codex App Serverを起動できません: {exc}") from exc
            if process.stdin is None or process.stdout is None or process.stderr is None:
                process.kill()
                raise CodexAppServerError("Codex App Serverのstdioを作成できません。")
            self._process = process
            self._stdin = process.stdin
            self._reader = threading.Thread(
                target=self._read_stdout,
                args=(process.stdout, process),
                daemon=True,
                name="question-review-codex-app-server",
            )
            self._stderr_reader = threading.Thread(
                target=self._read_stderr,
                args=(process.stderr,),
                daemon=True,
                name="question-review-codex-app-server-stderr",
            )
            self._reader.start()
            self._stderr_reader.start()
            try:
                self._mark_runtime_diagnostic("protocol", "initialize_rpc")
                initialize_result = self._request(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "exam-scraper-question-maintenance",
                            "title": "問題整備システム",
                            "version": "1.0.0",
                        },
                        "capabilities": {
                            "experimentalApi": False,
                            "requestAttestation": False,
                        },
                    },
                )
                self._mark_runtime_diagnostic("protocol", "initialize_response")
                _as_mapping(initialize_result, "initialize response")
                self._send({"method": "initialized"})
                self._initialized = True
                with self._state_lock:
                    self._app_server_generation += 1
                    self._hook_check_cache.clear()
                    self._hook_check_locks.clear()
                self._assert_official_chatgpt_endpoint()
            except BaseException as exc:
                process_exited = process.poll() is not None
                self._process = None
                self._stdin = None
                self._initialized = False
                self._stop_process(process, process.stdin)
                if process_exited and isinstance(exc, CodexAppServerError):
                    raise CodexProcessExitError(str(exc)) from exc
                raise

    def _app_server_command(self) -> list[str]:
        if self.binary_path is None:
            raise CodexAppServerError("Codex App Server binaryが見つかりません。")
        command = [
            str(self.binary_path),
            "app-server",
            "--listen",
            "stdio://",
            "-c",
            'shell_environment_policy.inherit="none"',
            "-c",
            f'shell_environment_policy.set={{PATH="{SAFE_SHELL_PATH}"}}',
            "-c",
            'forced_login_method="chatgpt"',
            "-c",
            "notify=[]",
            "-c",
            "analytics.enabled=false",
            "-c",
            'otel.exporter="none"',
            "-c",
            'otel.metrics_exporter="none"',
            "-c",
            'otel.trace_exporter="none"',
            "-c",
            "otel.log_user_prompt=false",
            "-c",
            f'model="{QUESTION_MAINTENANCE_MODEL}"',
            "-c",
            f'model_reasoning_effort="{TURN_REASONING_EFFORT}"',
            "--disable",
            "multi_agent",
        ]
        for feature in DISABLED_EXTERNAL_FEATURES:
            command.extend(["--disable", feature])
        for name in self._configured_mcp_names():
            if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                raise CodexAppServerError(
                    f"安全に無効化できないMCP server名です: {name}"
                )
            command.extend(
                [
                    "-c",
                    f'mcp_servers.{name}={{command="/usr/bin/false",enabled=false}}',
                ]
            )
        return command

    def _configured_mcp_names(self) -> list[str]:
        candidates = [self._source_codex_home / "config.toml"]
        candidates.extend(
            parent / ".codex" / "config.toml"
            for parent in (self.repo_root, *self.repo_root.parents)
        )
        names: set[str] = set()
        for path in dict.fromkeys(candidates):
            if not path.is_file():
                continue
            try:
                value = tomllib.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
                raise CodexAppServerError(
                    f"Codex configのMCP設定を安全に確認できません: {path}"
                ) from exc
            servers = value.get("mcp_servers")
            if servers is None:
                continue
            if not isinstance(servers, Mapping):
                raise CodexAppServerError("Codex configのMCP設定形式が不正です。")
            names.update(str(name) for name in servers)
        return sorted(names)

    def _assert_no_custom_agents(self, cwd: Path) -> None:
        directories = []
        if self._runtime_home is not None:
            directories.append(self._runtime_home / "agents")
        directories.extend(
            parent / ".codex" / "agents"
            for parent in (cwd.resolve(), *cwd.resolve().parents)
        )
        for directory in dict.fromkeys(path.resolve() for path in directories):
            if not directory.is_dir():
                continue
            for path in directory.glob("*.toml"):
                try:
                    value = tomllib.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
                    raise SubscriptionGateError(
                        f"custom agent設定を安全に確認できません: {path}"
                    ) from exc
                if not isinstance(value, Mapping):
                    raise SubscriptionGateError(
                        f"custom agent設定を安全に確認できません: {path}"
                    )
                raise SubscriptionGateError(
                    "custom agentがあるため並列調査を開始しません: "
                    f"{path}"
                )

    def _assert_isolated_config_layers(self, response: Mapping[str, Any]) -> None:
        layers = response.get("layers")
        if not isinstance(layers, list):
            raise SubscriptionGateError("Codex config layerを確認できません。")
        for layer in layers:
            if not isinstance(layer, Mapping):
                raise SubscriptionGateError("Codex config layerを確認できません。")
            name = layer.get("name")
            layer_type = name.get("type") if isinstance(name, Mapping) else None
            isolated_user_file = (
                self._runtime_home / "config.toml"
                if self._runtime_home is not None
                else None
            )
            layer_file = name.get("file") if isinstance(name, Mapping) else None
            isolated_user_layer = (
                layer_type == "user"
                and isolated_user_file is not None
                and isinstance(layer_file, str)
                and Path(layer_file).resolve() == isolated_user_file.resolve()
            )
            if layer_type not in {"sessionFlags", "system"} and not isolated_user_layer:
                raise SubscriptionGateError(
                    "隔離外のCodex config layerがあるため実行しません。"
                )

    @staticmethod
    def _assert_official_endpoint_provenance(
        response: Mapping[str, Any], config: Mapping[str, Any]
    ) -> None:
        endpoint_keys = ("openai_base_url", "chatgpt_base_url")
        origins = response.get("origins")
        layers = response.get("layers")
        if not isinstance(origins, Mapping) or not isinstance(layers, list):
            raise SubscriptionGateError("Codex endpoint provenanceを確認できません。")
        if any(key in origins for key in endpoint_keys):
            raise SubscriptionGateError(
                "公式ChatGPT以外の接続先設定があるため実行しません。"
            )
        for layer in layers:
            if not isinstance(layer, Mapping):
                raise SubscriptionGateError(
                    "Codex endpoint provenanceを確認できません。"
                )
            layer_config = layer.get("config")
            if not isinstance(layer_config, Mapping):
                raise SubscriptionGateError(
                    "Codex endpoint provenanceを確認できません。"
                )
            if any(key in layer_config for key in endpoint_keys):
                raise SubscriptionGateError(
                    "公式ChatGPT以外の接続先設定があるため実行しません。"
                )
        for key in endpoint_keys:
            if key in config and config[key] is not None and not isinstance(
                config[key], str
            ):
                raise SubscriptionGateError(
                    "Codex effective endpoint設定を確認できません。"
                )

    @staticmethod
    def _assert_no_custom_agent_config(config: Mapping[str, Any]) -> None:
        agents = config.get("agents")
        if agents is None:
            return
        agents = _as_mapping(agents, "agent設定")
        custom_roles = set(str(key) for key in agents) - GLOBAL_AGENT_CONFIG_KEYS
        if custom_roles:
            raise SubscriptionGateError(
                "custom agent roleがあるため並列調査を開始しません: "
                + ", ".join(sorted(custom_roles))
            )

    @staticmethod
    def _assert_safe_shell_environment(config: Mapping[str, Any]) -> None:
        shell_environment = _as_mapping(
            config.get("shell_environment_policy"),
            "shell environment設定",
        )
        if (
            shell_environment.get("inherit") != "none"
            or shell_environment.get("set") != {"PATH": SAFE_SHELL_PATH}
            or shell_environment.get("experimental_use_profile") not in {None, False}
        ):
            raise SubscriptionGateError("shell環境変数の遮断を確認できません。")

    def _assert_official_chatgpt_endpoint(self) -> None:
        self._mark_runtime_diagnostic("config", "config_read")
        response = _as_mapping(
            self._request(
                "config/read",
                {
                    "cwd": str(self._runtime_home or self.repo_root),
                    "includeLayers": True,
                },
            ),
            "Codex config",
        )
        self._mark_runtime_diagnostic("config", "config_layers")
        self._assert_isolated_config_layers(response)
        self._mark_runtime_diagnostic("config", "config_shape")
        config = _as_mapping(response.get("config"), "Codex effective config")
        self._mark_runtime_diagnostic("config", "custom_agents")
        self._assert_no_custom_agent_config(config)
        self._mark_runtime_diagnostic("config", "official_endpoint")
        self._assert_official_endpoint_provenance(response, config)
        self._mark_runtime_diagnostic("authentication", "login_method")
        if config.get("forced_login_method") != "chatgpt":
            raise SubscriptionGateError(
                "ChatGPTログイン経路への固定を確認できません。"
            )
        self._mark_runtime_diagnostic("provider", "model_provider")
        if config.get("model_provider") not in {None, "openai"}:
            raise SubscriptionGateError("外部model provider設定があるため実行しません。")
        model_providers = _as_mapping(
            config.get("model_providers"), "model provider設定"
        )
        if model_providers:
            raise SubscriptionGateError("追加model provider設定があるため実行しません。")
        self._mark_runtime_diagnostic("host_integration", "notify")
        if config.get("notify") != []:
            raise SubscriptionGateError("host通知commandの無効化を確認できません。")
        self._mark_runtime_diagnostic("telemetry", "analytics")
        analytics = _as_mapping(config.get("analytics"), "analytics設定")
        if analytics.get("enabled") is not False:
            raise SubscriptionGateError("analyticsの無効化を確認できません。")
        self._mark_runtime_diagnostic("telemetry", "otel")
        otel = _as_mapping(config.get("otel"), "OpenTelemetry設定")
        if (
            otel.get("exporter") != "none"
            or otel.get("metrics_exporter") != "none"
            or otel.get("trace_exporter") != "none"
            or otel.get("log_user_prompt") is not False
        ):
            raise SubscriptionGateError("OpenTelemetryの無効化を確認できません。")
        self._mark_runtime_diagnostic("capabilities", "features")
        features = _as_mapping(config.get("features"), "Codex feature設定")
        if any(features.get(name) is not False for name in DISABLED_EXTERNAL_FEATURES):
            raise SubscriptionGateError("外部作用機能の無効化を確認できません。")
        if features.get("multi_agent") is not False:
            raise SubscriptionGateError("multi-agent機能の無効化を確認できません。")
        self._mark_runtime_diagnostic("runtime_environment", "shell_environment")
        self._assert_safe_shell_environment(config)
        self._mark_runtime_diagnostic("capabilities", "mcp")
        servers = _as_mapping(config.get("mcp_servers"), "MCP設定")
        expected_names = set(self._configured_mcp_names())
        if set(str(name) for name in servers) != expected_names:
            raise SubscriptionGateError("全MCP serverの無効化を確認できません。")
        for server in servers.values():
            if (
                not isinstance(server, Mapping)
                or server.get("enabled") is not False
                or server.get("command") != "/usr/bin/false"
            ):
                raise SubscriptionGateError("全MCP serverの無効化を確認できません。")
        self._effective_model = str(config.get("model") or "")
        self._configured_reasoning_effort = str(
            config.get("model_reasoning_effort") or ""
        )

    def _assert_no_active_hooks(
        self,
        cwd: Path,
        *,
        turn_group: str | None = None,
        heartbeat: Callable[[], None] | None = None,
    ) -> None:
        requested_at = time.monotonic()
        self._ensure_started()
        normalized_cwd = str(cwd.resolve())
        with self._state_lock:
            generation = self._app_server_generation
            key = (generation, normalized_cwd)
            lock = self._hook_check_locks.setdefault(key, threading.Lock())

        def replay(snapshot: Mapping[str, Any]) -> bool:
            checked_at = float(snapshot.get("checkedAt") or 0.0)
            error_type = snapshot.get("errorType")
            error_message = str(snapshot.get("errorMessage") or "")
            if error_type is not None and checked_at >= requested_at:
                exception_type = (
                    error_type
                    if isinstance(error_type, type)
                    and issubclass(error_type, BaseException)
                    else CodexAppServerError
                )
                raise exception_type(error_message)
            return bool(
                snapshot.get("succeeded") is True
                and time.monotonic() - checked_at <= HOOK_STATUS_CACHE_SECONDS
            )

        with self._state_lock:
            cached = self._hook_check_cache.get(key)
            if cached is not None and replay(cached):
                return

        with lock:
            with self._state_lock:
                cached = self._hook_check_cache.get(key)
                if cached is not None and replay(cached):
                    return
            try:
                response = _as_mapping(
                    self._control_request(
                        "hooks/list",
                        {"cwds": [normalized_cwd]},
                        turn_group=turn_group,
                        heartbeat=heartbeat,
                    ),
                    "Codex hooks",
                )
                entries = response.get("data")
                if not isinstance(entries, list) or not entries:
                    raise SubscriptionGateError(
                        "hook無効化を確認できません。"
                    )
                for entry in entries:
                    if not isinstance(entry, Mapping):
                        raise SubscriptionGateError(
                            "hook無効化を確認できません。"
                        )
                    if entry.get("errors"):
                        raise SubscriptionGateError(
                            "hook設定を安全に確認できません。"
                        )
                    hooks = entry.get("hooks")
                    if not isinstance(hooks, list):
                        raise SubscriptionGateError(
                            "hook無効化を確認できません。"
                        )
                    if any(
                        not isinstance(hook, Mapping)
                        or hook.get("enabled") is not False
                        for hook in hooks
                    ):
                        raise SubscriptionGateError(
                            "有効なhookがあるため実行しません。"
                        )
            except Exception as exc:
                with self._state_lock:
                    self._hook_check_cache[key] = {
                        "checkedAt": time.monotonic(),
                        "succeeded": False,
                        "errorType": type(exc),
                        "errorMessage": str(exc),
                    }
                raise
            with self._state_lock:
                self._hook_check_cache[key] = {
                    "checkedAt": time.monotonic(),
                    "succeeded": True,
                    "errorType": None,
                    "errorMessage": "",
                }

    @staticmethod
    def _stop_process(
        process: subprocess.Popen[str] | None,
        stream: TextIO | None,
    ) -> None:
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
        if process is None or process.poll() is not None:
            return
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def _request(
        self,
        method: str,
        params: Any,
        *,
        timeout: int | None = None,
    ) -> Any:
        if method != "initialize":
            self._ensure_started()
        with self._state_lock:
            request_id = self._next_id
            self._next_id += 1
            pending = _PendingResponse()
            self._pending[request_id] = pending
        message: dict[str, Any] = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        try:
            self._send(message)
            if not pending.event.wait(timeout or self.request_timeout):
                raise CodexRequestTimeoutError(
                    f"Codex App Serverの{method}が時間切れになりました。"
                )
            if pending.error is not None:
                rpc_error = pending.error
                code = rpc_error.get("code") if isinstance(rpc_error, Mapping) else None
                safe_code = (
                    code
                    if (
                        isinstance(code, int)
                        and not isinstance(code, bool)
                    )
                    or code
                    in {
                        "auth_required",
                        "credits_enabled",
                        "quota_reached",
                        "service_unavailable",
                        "session_expired",
                    }
                    else None
                )
                data = rpc_error.get("data") if isinstance(rpc_error, Mapping) else None
                data_type = (
                    "null"
                    if data is None
                    else "boolean"
                    if isinstance(data, bool)
                    else "number"
                    if isinstance(data, (int, float))
                    else "string"
                    if isinstance(data, str)
                    else "object"
                    if isinstance(data, Mapping)
                    else "array"
                    if isinstance(data, (list, tuple))
                    else "unknown"
                )
                raise CodexRpcError(
                    f"Codex App Serverの{method}に失敗しました: {self._rpc_error(rpc_error)}",
                    method=method,
                    code=safe_code,
                    data_type=data_type,
                )
            return pending.result
        finally:
            with self._state_lock:
                self._pending.pop(request_id, None)

    def _control_request(
        self,
        method: str,
        params: Any,
        *,
        timeout: int | None = None,
        turn_group: str | None = None,
        heartbeat: Callable[[], None] | None = None,
    ) -> Any:
        """Bound short App Server control RPCs without limiting active turns."""

        with self.control_plane_budget.slot(
            turn_group,
            heartbeat=heartbeat,
            priority=method == "turn/start",
        ):
            try:
                return self._request(
                    method,
                    params,
                    timeout=(
                        timeout
                        if timeout is not None
                        else max(
                            self.request_timeout,
                            APP_SERVER_CONTROL_REQUEST_TIMEOUT_SECONDS,
                        )
                    ),
                )
            except CodexRequestTimeoutError as exc:
                raise CodexControlRequestTimeoutError(str(exc)) from exc

    def _send(self, message: Mapping[str, Any]) -> None:
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._write_lock:
            stream = self._stdin
            process = self._process
            if stream is None or process is None or process.poll() is not None:
                detail = " ".join(self._stderr_lines)[-1000:]
                suffix = f": {detail}" if detail else ""
                raise CodexAppServerError(f"Codex App Serverが停止しています{suffix}")
            try:
                stream.write(line)
                stream.flush()
            except OSError as exc:
                raise CodexAppServerError("Codex App Serverへの送信に失敗しました。") from exc

    def _assert_no_external_mcp(
        self,
        thread_id: str,
        *,
        turn_group: str | None = None,
        heartbeat: Callable[[], None] | None = None,
    ) -> None:
        cursor: str | None = None
        configured_disabled = set(self._configured_mcp_names())
        for _page in range(20):
            params: dict[str, Any] = {
                "limit": 100,
                "detail": "toolsAndAuthOnly",
                "threadId": thread_id,
            }
            if cursor is not None:
                params["cursor"] = cursor
            response = _as_mapping(
                self._control_request(
                    "mcpServerStatus/list",
                    params,
                    turn_group=turn_group,
                    heartbeat=heartbeat,
                ),
                "MCP server status",
            )
            servers = response.get("data")
            if not isinstance(servers, list):
                raise SubscriptionGateError("MCP server無効化を確認できません。")
            for server in servers:
                if not isinstance(server, Mapping):
                    raise SubscriptionGateError("MCP server無効化を確認できません。")
                if str(server.get("name") or "") not in configured_disabled:
                    raise SubscriptionGateError(
                        "想定外のMCP serverが読み込まれたため実行しません。"
                    )
                if (
                    server.get("serverInfo") is not None
                    or bool(server.get("tools"))
                    or bool(server.get("resources"))
                    or bool(server.get("resourceTemplates"))
                ):
                    raise SubscriptionGateError(
                        "外部MCP serverが有効なため実行しません。"
                    )
            next_cursor = response.get("nextCursor")
            if next_cursor is None:
                return
            if not isinstance(next_cursor, str) or not next_cursor:
                break
            cursor = next_cursor
        raise SubscriptionGateError("MCP server一覧を完了まで確認できません。")

    def _interrupt_turn(self, thread_id: str, turn_id: str) -> None:
        try:
            self._request(
                "turn/interrupt",
                {"threadId": thread_id, "turnId": turn_id},
                timeout=10,
            )
        except CodexAppServerError:
            pass

    def _interrupt_active_turns(
        self,
        thread_id: str,
        on_turn_started: Callable[[str, str], None] | None = None,
    ) -> None:
        try:
            response = _as_mapping(
                self._request(
                    "thread/read",
                    {"threadId": thread_id, "includeTurns": True},
                    timeout=10,
                ),
                "thread/read response",
            )
            thread = _as_mapping(response.get("thread"), "thread/read thread")
            turns = thread.get("turns")
            if not isinstance(turns, list):
                return
            for turn in turns:
                if isinstance(turn, Mapping):
                    turn_id = str(turn.get("id") or "")
                    if turn_id and on_turn_started is not None:
                        try:
                            on_turn_started(thread_id, turn_id)
                        except Exception:  # noqa: BLE001
                            pass
                    if turn_id and turn.get("status") == "inProgress":
                        self._interrupt_turn(thread_id, turn_id)
        except CodexAppServerError:
            pass

    def _read_stdout(
        self, stream: TextIO, process: subprocess.Popen[str]
    ) -> None:
        try:
            for raw_line in stream:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                self._handle_message(message)
        finally:
            with self._lifecycle_lock:
                is_current = self._process is process
                if is_current:
                    self._initialized = False
                    self._process = None
                    self._stdin = None
                    if not self._closed:
                        self._observation_connection_lost = True
                    self._fail_all("Codex App Serverとの接続が終了しました。")

    def _read_stderr(self, stream: TextIO) -> None:
        for raw_line in stream:
            line = " ".join(raw_line.strip().split())
            if line:
                self._stderr_lines.append(line[:2000])

    def _handle_message(self, message: dict[str, Any]) -> None:
        if "id" in message and "method" not in message:
            with self._state_lock:
                pending = self._pending.get(message["id"])
                if pending is not None:
                    pending.result = message.get("result")
                    pending.error = message.get("error")
                    pending.event.set()
            return
        if "id" in message and "method" in message:
            self._handle_server_request(message)
            return
        method = str(message.get("method") or "")
        if method in MONITOR_NOTIFICATION_METHODS:
            self._observe_monitor_notification(message)
        if method in {"account/updated", "account/rateLimits/updated"}:
            with self._state_lock:
                self._last_status = None
                self._last_status_at = 0.0
            return
        if method in {
            "turn/started",
            "item/completed",
            "item/agentMessage/delta",
            "turn/completed",
            "error",
        }:
            self._handle_turn_notification(message)

    def _observe_monitor_notification(self, message: Mapping[str, Any]) -> None:
        """Reader-side boundary: one best-effort non-blocking enqueue only."""
        try:
            self._monitor_observer_adapter.put_nowait(message)
        except Exception:  # noqa: BLE001 - monitoring must never affect a turn.
            pass

    def _bind_monitor_runtime(
        self,
        context: Mapping[str, Any],
        thread_id: str,
        turn_id: str | None = None,
    ) -> None:
        try:
            self._monitor_observer_adapter.bind_runtime(
                dict(context), thread_id, turn_id
            )
        except Exception:  # noqa: BLE001 - monitoring is an optional projection.
            pass

    @staticmethod
    def _failure_output_tail(value: Any) -> str:
        text = " ".join(str(value or "").split())[-600:]
        sensitive = re.compile(
            r"(?i)(?:\b(?:authorization|api[_-]?key|token|secret|password|cookie)\b"
            r"\s*[:=]|\bBearer\s+\S+|\bsk-[A-Za-z0-9_-]{8,}|"
            r"\bgh[pousr]_[A-Za-z0-9_]{8,}|\bAKIA[A-Z0-9]{12,})"
        )
        return "<redacted sensitive output>" if sensitive.search(text) else text

    def _display_change_path(self, value: str) -> str:
        path = Path(value)
        if not path.is_absolute():
            return path.as_posix()[:300]
        try:
            resolved = path.resolve()
            if resolved.is_relative_to(self.repo_root):
                return resolved.relative_to(self.repo_root).as_posix()[:300]
        except (OSError, RuntimeError):
            pass
        return (path.name or "repository外のfile")[:300]

    @staticmethod
    def _emit_turn_event(
        state: _TurnState,
        event: Mapping[str, Any],
    ) -> None:
        structured_emit = getattr(state.emit, "event", None)
        if callable(structured_emit):
            structured_emit(event)
            return
        state.emit(str(event.get("message") or "")[:1200])

    def _record_turn_item(self, state: _TurnState, item: Mapping[str, Any]) -> None:
        item_id = str(item.get("id") or "").strip()
        if item_id:
            if item_id in state.recorded_item_ids:
                return
            state.recorded_item_ids.add(item_id)
        item_type = str(item.get("type") or "")
        if item_type == "agentMessage":
            message_text = str(item.get("text") or "")
            if message_text:
                phase = item.get("phase")
                value = (
                    str(phase) if isinstance(phase, str) else None,
                    message_text,
                )
                if value not in state.messages:
                    state.messages.append(value)
                if state.structured_output and phase == "final_answer":
                    state.completed_message_at = time.monotonic()
            return
        if item_type == "commandExecution":
            command = self._failure_output_tail(item.get("command"))[:240]
            status = str(item.get("status") or "")
            exit_code = item.get("exitCode")
            exit_detail = (
                f" exitCode={exit_code}"
                if isinstance(exit_code, int) and not isinstance(exit_code, bool)
                else ""
            )
            log = f"command {status}{exit_detail}: {command}"
            failed = status == "failed" or (
                isinstance(exit_code, int)
                and not isinstance(exit_code, bool)
                and exit_code != 0
            )
            output_tail = self._failure_output_tail(
                item.get("aggregatedOutput") or item.get("output")
            )
            if failed and output_tail:
                log += f" / output: {output_tail}"
            self._emit_turn_event(
                state,
                {
                    "level": "error" if failed else "info",
                    "message": log[:1200],
                    "commandStatus": status,
                    "exitCode": exit_code,
                    "outputTail": output_tail,
                },
            )
            return
        if item_type == "fileChange":
            changes = item.get("changes")
            count = len(changes) if isinstance(changes, list) else 0
            display_paths: list[str] = []
            if isinstance(changes, list):
                for change in changes:
                    if isinstance(change, Mapping):
                        path = str(change.get("path") or "").strip()
                        if path:
                            state.changed_files.add(path)
                            display_paths.append(self._display_change_path(path))
            visible_paths = display_paths[:5]
            suffix = (
                f"、ほか{len(display_paths) - len(visible_paths)}件"
                if len(display_paths) > len(visible_paths)
                else ""
            )
            path_detail = (
                f": {', '.join(visible_paths)}{suffix}" if visible_paths else ""
            )
            self._emit_turn_event(
                state,
                {
                    "level": "info",
                    "message": f"file change: {count}件{path_detail}"[:1200],
                    "changedPaths": display_paths,
                },
            )
            return
        if item_type != "collabAgentToolCall" or item.get("tool") != "spawnAgent":
            return
        receivers = {
            str(value)
            for value in item.get("receiverThreadIds") or []
            if str(value)
        }
        added = receivers - state.subagent_thread_ids
        state.subagent_thread_ids.update(receivers)
        model = str(item.get("model") or "").strip()
        if model:
            state.subagent_models.add(model)
        effort = str(item.get("reasoningEffort") or "").strip()
        if effort:
            state.subagent_reasoning_efforts.add(effort)
        if added:
            state.emit(
                f"read-only調査担当を{len(state.subagent_thread_ids)}件開始しました。"
            )

    @staticmethod
    def _emit_model_turn_event(state: _TurnState, event: str) -> None:
        callback = state.on_model_turn_event
        if not callable(callback):
            return
        observed_at = (
            state.protocol_started_at
            if event == "started"
            else state.protocol_finished_at
        )
        observed_monotonic = (
            state.protocol_started_monotonic
            if event == "started"
            else state.protocol_finished_monotonic
        )
        if observed_at is None or observed_monotonic is None:
            return
        payload: dict[str, Any] = {
            "event": event,
            "threadId": state.thread_id,
            "turnId": state.turn_id,
            "observedAt": observed_at,
            "observedMonotonic": observed_monotonic,
        }
        if (
            event == "started"
            and state.requested_monotonic is not None
        ):
            payload["queueWaitSeconds"] = round(
                max(0.0, observed_monotonic - state.requested_monotonic),
                6,
            )
        if (
            event == "finished"
            and state.protocol_started_monotonic is not None
        ):
            payload["durationSeconds"] = round(
                max(
                    0.0,
                    observed_monotonic - state.protocol_started_monotonic,
                ),
                6,
            )
        try:
            # The qualification callback only updates a small in-memory
            # accumulator. Telemetry must never affect turn completion.
            callback(payload)
        except Exception:
            pass

    def _handle_turn_notification(self, message: dict[str, Any]) -> None:
        method = str(message.get("method") or "")
        params = message.get("params")
        if not isinstance(params, Mapping):
            return
        thread_id = str(params.get("threadId") or "")
        turn_value = params.get("turn")
        turn_id = str(
            params.get("turnId")
            or (turn_value.get("id") if isinstance(turn_value, Mapping) else "")
            or ""
        )
        if not thread_id or not turn_id:
            return
        key = (thread_id, turn_id)
        notify_started = False
        with self._state_lock:
            if method == "turn/started":
                observed = time.monotonic()
                observed_at = datetime.now().astimezone().isoformat()
                self._notified_active_turns.add(key)
                self._notified_turn_started.setdefault(
                    key,
                    (observed_at, observed),
                )
                self._peak_active_turns = max(
                    self._peak_active_turns,
                    len(self._notified_active_turns),
                )
                state = self._turns.get(key)
                if state is not None and state.protocol_started_monotonic is None:
                    state.protocol_started_at = observed_at
                    state.protocol_started_monotonic = observed
                    notify_started = True
                if state is None:
                    return
            if method == "turn/completed" or (
                method == "error" and params.get("willRetry") is not True
            ):
                self._notified_active_turns.discard(key)
            state = self._turns.get(key)
            if state is None:
                self._early_notifications.setdefault(key, []).append(copy.deepcopy(message))
                return
        if method == "turn/started":
            if notify_started:
                self._emit_model_turn_event(state, "started")
            return
        if method == "item/completed":
            item = params.get("item")
            if isinstance(item, Mapping):
                self._record_turn_item(state, item)
            return
        if method == "item/agentMessage/delta" and state.structured_output:
            delta = params.get("delta")
            if not isinstance(delta, str) or not delta:
                return
            semantic_prefix = delta.rstrip()
            if semantic_prefix:
                state.last_semantic_delta_at = time.monotonic()
                state.trailing_whitespace_chars = len(delta) - len(semantic_prefix)
            elif state.last_semantic_delta_at is not None:
                state.trailing_whitespace_chars += len(delta)
            return
        if method == "error":
            state.error = params.get("error")
            if params.get("willRetry") is not True:
                if state.protocol_finished_monotonic is None:
                    state.protocol_finished_at = (
                        datetime.now().astimezone().isoformat()
                    )
                    state.protocol_finished_monotonic = time.monotonic()
                    self._emit_model_turn_event(state, "finished")
                state.status = "failed"
                state.event.set()
            return
        if method == "turn/completed":
            if state.protocol_finished_monotonic is None:
                state.protocol_finished_at = (
                    datetime.now().astimezone().isoformat()
                )
                state.protocol_finished_monotonic = time.monotonic()
                self._emit_model_turn_event(state, "finished")
            turn = params.get("turn")
            if isinstance(turn, Mapping):
                state.status = str(turn.get("status") or "failed")
                state.error = turn.get("error")
                items = turn.get("items")
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, Mapping):
                            self._record_turn_item(state, item)
            else:
                state.status = "failed"
            state.event.set()

    def _handle_server_request(self, message: dict[str, Any]) -> None:
        method = str(message.get("method") or "")
        request_id = message.get("id")
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            self._send({"id": request_id, "result": {"decision": "decline"}})
            return
        if method == "item/permissions/requestApproval":
            self._send(
                {"id": request_id, "result": {"permissions": {}, "scope": "turn"}}
            )
            return
        if method in {"applyPatchApproval", "execCommandApproval"}:
            self._send({"id": request_id, "result": {"decision": "denied"}})
            return
        self._send(
            {
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": "問題整備システムではこの対話要求を受け付けません。",
                },
            }
        )

    def _fail_all(self, message: str) -> None:
        with self._state_lock:
            pending = list(self._pending.values())
            turns = list(self._turns.values())
            self._notified_active_turns.clear()
        for item in pending:
            item.error = {"message": message}
            item.event.set()
        for state in turns:
            state.status = "failed"
            state.error = {"message": message}
            state.event.set()

    def _resolve_binary(self, explicit: Path | None) -> Path | None:
        candidates: list[Path]
        if explicit is not None:
            candidates = [explicit.expanduser()]
        else:
            candidates = [DEFAULT_CODEX_PATH]
        for candidate in candidates:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate.resolve()
        return None

    @staticmethod
    def _rpc_error(error: Any) -> str:
        if isinstance(error, Mapping):
            return str(error.get("message") or error)[:1200]
        return str(error)[:1200]

    @staticmethod
    def _turn_error_message(error: Any) -> str:
        if error is None:
            return ""
        if isinstance(error, Mapping):
            value = error.get("message") or error.get("additionalDetails") or error
        else:
            value = error
        text = " ".join(str(value).split())
        return f": {text[:1200]}" if text else ""
