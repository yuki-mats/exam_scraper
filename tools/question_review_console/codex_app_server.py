from __future__ import annotations

import copy
import json
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import tomllib
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, TextIO


DEFAULT_CODEX_PATH = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
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

QUESTION_MAINTENANCE_MODEL = "gpt-5.5"
TURN_REASONING_EFFORT = "high"
MAINTENANCE_RESEARCH_WORKERS = 2
APP_SERVER_AGENT_THREAD_CAP = MAINTENANCE_RESEARCH_WORKERS + 1
APP_SERVER_AGENT_MAX_DEPTH = 1
TURN_HEARTBEAT_INTERVAL_SECONDS = 15.0
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


class CodexAppServerError(RuntimeError):
    pass


class SubscriptionGateError(CodexAppServerError):
    pass


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
    event: threading.Event = field(default_factory=threading.Event)
    messages: list[tuple[str | None, str]] = field(default_factory=list)
    changed_files: set[str] = field(default_factory=set)
    subagent_thread_ids: set[str] = field(default_factory=set)
    subagent_models: set[str] = field(default_factory=set)
    subagent_reasoning_efforts: set[str] = field(default_factory=set)
    recorded_item_ids: set[str] = field(default_factory=set)
    status: str = "inProgress"
    error: Any = None


def _as_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SubscriptionGateError(f"{label}を確認できません。")
    return value


def validate_subscription_access(
    account_response: Mapping[str, Any],
    rate_limit_response: Mapping[str, Any],
) -> dict[str, Any]:
    """ChatGPT subscription以外へ決して進まないためのfail-closed gate。"""

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
    if credits.get("hasCredits") is not False:
        raise SubscriptionGateError("追加creditが有効なaccountでは実行しません。")
    if "individualLimit" not in snapshot or snapshot.get("individualLimit") is not None:
        raise SubscriptionGateError("追加支出を許すspend controlがあるaccountでは実行しません。")
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
        if extra_credits is not None and (
            not isinstance(extra_credits, Mapping)
            or extra_credits.get("hasCredits") is not False
        ):
            raise SubscriptionGateError("補助credit状態を安全に確認できません。")
        if "individualLimit" not in value:
            raise SubscriptionGateError("補助spend controlを安全に確認できません。")
        if value.get("individualLimit") is not None:
            raise SubscriptionGateError("追加支出を許すspend controlがあるaccountでは実行しません。")

    return {
        "allowed": True,
        "accountType": "chatgpt",
        "planType": plan_type,
        "rateLimitReachedType": None,
        "creditsEnabled": False,
        "standardMode": True,
    }


class CodexAppServerClient:
    """One long-lived stdio Codex App Server connection for the local UI."""

    def __init__(
        self,
        repo_root: Path,
        *,
        binary_path: Path | None = None,
        request_timeout: int = 30,
        turn_timeout: int = 1800,
        status_cache_seconds: float = 3.0,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.binary_path = self._resolve_binary(binary_path)
        self.request_timeout = request_timeout
        self.turn_timeout = turn_timeout
        self.status_cache_seconds = status_cache_seconds
        self.provider = APP_SERVER_PROVIDER

        self._process: subprocess.Popen[str] | None = None
        self._stdin: TextIO | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._write_lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._next_id = 1
        self._pending: dict[int | str, _PendingResponse] = {}
        self._turns: dict[tuple[str, str], _TurnState] = {}
        self._early_notifications: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._stderr_lines: deque[str] = deque(maxlen=80)
        self._closed = False
        self._initialized = False
        self._last_status: dict[str, Any] | None = None
        self._last_status_at = 0.0
        self._effective_model = ""
        self._configured_reasoning_effort = ""
        self._source_codex_home = Path(
            os.environ.get("CODEX_HOME") or Path.home() / ".codex"
        ).resolve()
        self._runtime_home_context: tempfile.TemporaryDirectory[str] | None = None
        self._runtime_home: Path | None = None

    @property
    def configured(self) -> bool:
        return self.binary_path is not None

    def public_status(self, *, refresh: bool = False) -> dict[str, Any]:
        if not self.configured:
            return {
                "available": False,
                "allowed": False,
                "provider": self.provider,
                "reason": "Codex App Server binaryが見つかりません。",
            }
        if not refresh and self._last_status is None:
            return {
                "available": True,
                "allowed": None,
                "provider": self.provider,
                "reason": "実行開始時にChatGPT認証と利用上限を確認します。",
            }
        try:
            return {"available": True, "provider": self.provider, **self.assert_subscription_access(force=refresh)}
        except CodexAppServerError as exc:
            return {
                "available": True,
                "allowed": False,
                "provider": self.provider,
                "reason": str(exc),
            }

    def assert_subscription_access(self, *, force: bool = True) -> dict[str, Any]:
        now = time.monotonic()
        with self._state_lock:
            if (
                not force
                and self._last_status is not None
                and now - self._last_status_at <= self.status_cache_seconds
            ):
                return copy.deepcopy(self._last_status)
        self._ensure_started()
        account = self._request("account/read", {"refreshToken": False})
        rate_limits = self._request("account/rateLimits/read", None)
        status = validate_subscription_access(
            _as_mapping(account, "Codex account response"),
            _as_mapping(rate_limits, "Codex rate limit response"),
        )
        status.update(
            {
                "model": QUESTION_MAINTENANCE_MODEL,
                "configuredModel": self._effective_model,
                "configuredReasoningEffort": self._configured_reasoning_effort,
                "turnReasoningEffort": TURN_REASONING_EFFORT,
            }
        )
        with self._state_lock:
            self._last_status = dict(status)
            self._last_status_at = time.monotonic()
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
        cwd: Path | None = None,
        writable_roots: Iterable[Path] = (),
        completion_probe: Callable[[], bool] | None = None,
        heartbeat: Callable[[], None] | None = None,
    ) -> AppServerTurnResult:
        if sandbox not in {"read-only", "workspace-write"}:
            raise ValueError(f"unsupported sandbox: {sandbox}")
        # UIの事前表示とは別に、thread/start直前の実値を必ず確認する。
        self.assert_subscription_access(force=True)
        turn_cwd = (cwd or self.repo_root).resolve()
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
        evaluation_work = work_type in {"evaluation", "reevaluation"}
        research_work = work_type == "maintenance_research"
        read_only_work = evaluation_work or research_work
        config = {
            "features": {
                **{name: False for name in DISABLED_EXTERNAL_FEATURES},
                "fast_mode": False,
                # 並列subagentはread-only調査threadだけに限定する。
                "multi_agent": research_work,
            },
            "agents": {
                "max_threads": APP_SERVER_AGENT_THREAD_CAP,
                "max_depth": APP_SERVER_AGENT_MAX_DEPTH,
            },
            "service_tier": None,
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
                f"対象問題を重複なく分け、最大{MAINTENANCE_RESEARCH_WORKERS}つのexplorer subagentで並列に読み取り、"
                "根拠と問題IDごとの最終判断案を親threadで統合する。思考過程は返さない。"
            )
        else:
            developer_instructions = (
                "このthreadは問題整備の保存専用である。subagentは使わない。"
                "00_sourceと既存IDを変更せず、責務に合うpatchだけを変更する。"
                "merge、convert、upload-ready生成は別工程に残す。git add、commit、pushは行わず、"
                "Firestore、Storage、GitHub等の外部状態は変更しない。"
            )
        self._assert_no_active_hooks(turn_cwd)
        if research_work:
            self._assert_no_custom_agents(turn_cwd)
            research_agent_config = self._trusted_research_agent_config()
            config["agents"][RESEARCH_AGENT_ROLE] = {
                "description": RESEARCH_AGENT_DESCRIPTION,
                "config_file": str(research_agent_config),
            }
        thread_response = self._request(
            "thread/start",
            {
                "cwd": str(turn_cwd),
                "model": QUESTION_MAINTENANCE_MODEL,
                "modelProvider": "openai",
                "approvalPolicy": approval_policy,
                "approvalsReviewer": "user",
                "sandbox": sandbox,
                "serviceTier": None,
                "config": config,
                "developerInstructions": developer_instructions,
                "ephemeral": read_only_work,
                "threadSource": f"exam_scraper_{work_type}",
            },
        )
        thread_response = _as_mapping(thread_response, "thread/start response")
        thread = _as_mapping(thread_response.get("thread"), "thread")
        thread_id = str(thread.get("id") or "")
        session_id = str(thread.get("sessionId") or "")
        if not thread_id or not session_id:
            raise CodexAppServerError("Codex App Serverがthread又はsession IDを返しませんでした。")
        if on_thread_started is not None:
            on_thread_started(thread_id, session_id)
        service_tier = thread_response.get("serviceTier")
        if service_tier not in {None, "default", "standard"}:
            raise SubscriptionGateError("Standard mode以外では実行しません。")
        model_provider = str(thread_response.get("modelProvider") or "")
        if model_provider != "openai":
            raise SubscriptionGateError("外部model providerでは実行しません。")
        actual_model = str(thread_response.get("model") or "")
        if actual_model != QUESTION_MAINTENANCE_MODEL:
            raise SubscriptionGateError(
                f"指定model {QUESTION_MAINTENANCE_MODEL}が適用されませんでした。"
            )
        sandbox_response = _as_mapping(thread_response.get("sandbox"), "sandbox response")
        expected_sandbox = "readOnly" if sandbox == "read-only" else "workspaceWrite"
        if sandbox_response.get("type") != expected_sandbox:
            raise CodexAppServerError("要求したsandboxが適用されませんでした。")
        if sandbox_response.get("networkAccess") is not False:
            raise CodexAppServerError("commandのnetwork無効化を確認できません。")
        self._assert_no_external_mcp(thread_id)

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
            "serviceTier": None,
            "effort": TURN_REASONING_EFFORT,
        }
        if output_schema is not None:
            params["outputSchema"] = copy.deepcopy(dict(output_schema))
        try:
            turn_response = self._request("turn/start", params)
            turn_response = _as_mapping(turn_response, "turn/start response")
            turn = _as_mapping(turn_response.get("turn"), "turn")
            turn_id = str(turn.get("id") or "")
            if not turn_id:
                raise CodexAppServerError(
                    "Codex App Serverがturn IDを返しませんでした。"
                )
        except Exception:
            self._interrupt_active_turns(thread_id, on_turn_started)
            raise
        state = _TurnState(thread_id=thread_id, turn_id=turn_id, emit=emit)
        key = (thread_id, turn_id)
        with self._state_lock:
            self._turns[key] = state
            early = self._early_notifications.pop(key, [])
        try:
            for notification in early:
                self._handle_turn_notification(notification)
            if on_turn_started is not None:
                on_turn_started(thread_id, turn_id)
            emit(f"Codex App Server thread: {thread_id}")

            receipt_interrupted = False
            deadline = time.monotonic() + self.turn_timeout
            heartbeat_callback = heartbeat or getattr(emit, "heartbeat", None)
            next_heartbeat = (
                time.monotonic() + TURN_HEARTBEAT_INTERVAL_SECONDS
                if callable(heartbeat_callback)
                else math.inf
            )
            while not state.event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CodexAppServerError(
                        "Codex App Serverのturnが時間切れになりました。"
                    )
                heartbeat_wait = max(0.0, next_heartbeat - time.monotonic())
                if state.event.wait(min(0.25, remaining, heartbeat_wait)):
                    break
                now = time.monotonic()
                if now >= next_heartbeat:
                    try:
                        heartbeat_callback()
                    except Exception:
                        pass
                    next_heartbeat = now + TURN_HEARTBEAT_INTERVAL_SECONDS
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
        receipt_interrupted = bool(
            receipt_interrupted and state.status == "interrupted"
        )
        if state.status != "completed" and not receipt_interrupted:
            detail = self._turn_error_message(state.error)
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
            unexpected_models = state.subagent_models - {QUESTION_MAINTENANCE_MODEL}
            if unexpected_models:
                raise SubscriptionGateError(
                    "read-only調査subagentで指定外modelを検出しました: "
                    + ", ".join(sorted(unexpected_models))
                )
            unexpected_efforts = state.subagent_reasoning_efforts - {
                TURN_REASONING_EFFORT
            }
            if unexpected_efforts:
                raise SubscriptionGateError(
                    "read-only調査subagentで指定外の推論強度を検出しました: "
                    + ", ".join(sorted(unexpected_efforts))
                )
        return AppServerTurnResult(
            thread_id=thread_id,
            session_id=session_id,
            turn_id=turn_id,
            final_message=final_message,
            model=actual_model,
            service_tier=service_tier if isinstance(service_tier, str) else None,
            reasoning_effort=TURN_REASONING_EFFORT,
            changed_files=tuple(sorted(state.changed_files)),
            subagent_thread_ids=tuple(sorted(state.subagent_thread_ids)),
            subagent_models=tuple(sorted(state.subagent_models)),
            subagent_reasoning_efforts=tuple(
                sorted(state.subagent_reasoning_efforts)
            ),
            completion_mode=(
                "receipt_interrupted" if receipt_interrupted else "turn_completed"
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
        self._stop_process(process, stream)
        self._fail_all("Codex App Serverを停止しました。")
        if runtime_home_context is not None:
            runtime_home_context.cleanup()

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
        except OSError as exc:
            context.cleanup()
            raise SubscriptionGateError(
                "ChatGPT認証情報を隔離実行環境へ準備できません。"
            ) from exc
        self._runtime_home_context = context
        self._runtime_home = runtime_home
        return runtime_home

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
            if previous_process is not None:
                self._process = None
                self._stdin = None
                self._initialized = False
                self._stop_process(previous_process, previous_stream)
                self._fail_all("前回のCodex App Server接続が終了しました。")
            env = dict(os.environ)
            for key in API_CREDENTIAL_ENV_VARS:
                env.pop(key, None)
            runtime_home = self._prepare_isolated_codex_home()
            env["CODEX_HOME"] = str(runtime_home)
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
                _as_mapping(initialize_result, "initialize response")
                self._send({"method": "initialized"})
                self._initialized = True
                self._assert_official_chatgpt_endpoint()
            except BaseException:
                self._process = None
                self._stdin = None
                self._initialized = False
                self._stop_process(process, process.stdin)
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
            "--enable",
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

    def _assert_official_chatgpt_endpoint(self) -> None:
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
        self._assert_isolated_config_layers(response)
        config = _as_mapping(response.get("config"), "Codex effective config")
        self._assert_no_custom_agent_config(config)
        for key in ("openai_base_url", "chatgpt_base_url"):
            if config.get(key) is not None:
                raise SubscriptionGateError(
                    "公式ChatGPT以外の接続先設定があるため実行しません。"
                )
        if config.get("forced_login_method") != "chatgpt":
            raise SubscriptionGateError(
                "ChatGPTログイン経路への固定を確認できません。"
            )
        if config.get("model_provider") not in {None, "openai"}:
            raise SubscriptionGateError("外部model provider設定があるため実行しません。")
        model_providers = _as_mapping(
            config.get("model_providers"), "model provider設定"
        )
        if model_providers:
            raise SubscriptionGateError("追加model provider設定があるため実行しません。")
        if config.get("notify") != []:
            raise SubscriptionGateError("host通知commandの無効化を確認できません。")
        analytics = _as_mapping(config.get("analytics"), "analytics設定")
        if analytics.get("enabled") is not False:
            raise SubscriptionGateError("analyticsの無効化を確認できません。")
        otel = _as_mapping(config.get("otel"), "OpenTelemetry設定")
        if (
            otel.get("exporter") != "none"
            or otel.get("metrics_exporter") != "none"
            or otel.get("trace_exporter") != "none"
            or otel.get("log_user_prompt") is not False
        ):
            raise SubscriptionGateError("OpenTelemetryの無効化を確認できません。")
        features = _as_mapping(config.get("features"), "Codex feature設定")
        if any(features.get(name) is not False for name in DISABLED_EXTERNAL_FEATURES):
            raise SubscriptionGateError("外部作用機能の無効化を確認できません。")
        if features.get("multi_agent") is not True:
            raise SubscriptionGateError("整備判断の並列機能を確認できません。")
        shell_environment = _as_mapping(
            config.get("shell_environment_policy"),
            "shell environment設定",
        )
        explicit_environment = shell_environment.get("set")
        if (
            shell_environment.get("inherit") != "none"
            or (
                explicit_environment is not None
                and (
                    not isinstance(explicit_environment, Mapping)
                    or bool(explicit_environment)
                )
            )
            or shell_environment.get("experimental_use_profile") not in {None, False}
        ):
            raise SubscriptionGateError("shell環境変数の遮断を確認できません。")
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

    def _assert_no_active_hooks(self, cwd: Path) -> None:
        response = _as_mapping(
            self._request("hooks/list", {"cwds": [str(cwd)]}),
            "Codex hooks",
        )
        entries = response.get("data")
        if not isinstance(entries, list) or not entries:
            raise SubscriptionGateError("hook無効化を確認できません。")
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise SubscriptionGateError("hook無効化を確認できません。")
            if entry.get("errors"):
                raise SubscriptionGateError("hook設定を安全に確認できません。")
            hooks = entry.get("hooks")
            if not isinstance(hooks, list):
                raise SubscriptionGateError("hook無効化を確認できません。")
            if any(not isinstance(hook, Mapping) or hook.get("enabled") is not False for hook in hooks):
                raise SubscriptionGateError("有効なhookがあるため実行しません。")

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
                raise CodexAppServerError(f"Codex App Serverの{method}が時間切れになりました。")
            if pending.error is not None:
                raise CodexAppServerError(
                    f"Codex App Serverの{method}に失敗しました: {self._rpc_error(pending.error)}"
                )
            return pending.result
        finally:
            with self._state_lock:
                self._pending.pop(request_id, None)

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

    def _assert_no_external_mcp(self, thread_id: str) -> None:
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
                self._request("mcpServerStatus/list", params),
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
        if method in {"account/updated", "account/rateLimits/updated"}:
            with self._state_lock:
                self._last_status = None
                self._last_status_at = 0.0
            return
        if method in {"item/completed", "turn/completed", "error"}:
            self._handle_turn_notification(message)

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
        with self._state_lock:
            state = self._turns.get(key)
            if state is None:
                self._early_notifications.setdefault(key, []).append(copy.deepcopy(message))
                return
        if method == "item/completed":
            item = params.get("item")
            if isinstance(item, Mapping):
                self._record_turn_item(state, item)
            return
        if method == "error":
            state.error = params.get("error")
            if params.get("willRetry") is not True:
                state.status = "failed"
                state.event.set()
            return
        if method == "turn/completed":
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
