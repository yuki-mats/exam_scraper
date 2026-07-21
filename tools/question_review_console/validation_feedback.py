from __future__ import annotations

import json
import os
import re
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping


_MESSAGE_LIMIT = 320
_FAILED_COMMAND_STATUSES = {"error", "fail", "failed", "failure"}
_PASSED_COMMAND_STATUSES = {"ok", "pass", "passed", "success", "succeeded"}
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)\S+"),
    re.compile(r"(?i)\bsk-[a-z0-9_-]{8,}\b"),
    re.compile(
        r"(?i)((?:--)?(?:api[_-]?key|password|secret|token)"
        r"(?:\s*[=:]\s*|\s+))"
        r"(?:\"[^\"]*\"|'[^']*'|\S+)"
    ),
)


def build_child_feedback(
    child: Mapping[str, Any],
    *,
    attempt: int,
    question_id: str,
    stage_id: str,
) -> dict[str, Any]:
    """Normalize a rejected child run into bounded, model-safe feedback."""

    if attempt < 1:
        raise ValueError("attemptは1以上で指定してください。")

    issues: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(code: str, field: str, message: str, retryable: bool) -> None:
        key = (code, field)
        if key in seen:
            return
        seen.add(key)
        issues.append(
            {
                "code": code,
                "field": field,
                "message": _safe_message(message),
                "retryable": retryable,
            }
        )

    error = str(child.get("error") or "").strip()
    receipt_error = str(child.get("receiptError") or "").strip()
    result = child.get("result")
    result = result if isinstance(result, Mapping) else {}
    result_summary = str(result.get("summary") or "").strip()
    commands = [
        value for value in result.get("commands") or [] if isinstance(value, Mapping)
    ]
    failed_commands = [value for value in commands if _command_failed(value)]
    failed_checks = [_safe_failed_check(value) for value in failed_commands[:5]]

    rejected_summary = bool(
        result_summary
        and (
            str(child.get("status") or "") != "succeeded"
            or str(result.get("status") or "succeeded") != "succeeded"
        )
    )
    for message in (error, result_summary if rejected_summary else ""):
        if not message:
            continue
        code, field, retryable = _classify_message(message)
        add(code, field, message, retryable)

    if receipt_error:
        code, field, retryable = _classify_message(receipt_error)
        if code in {"execution_failure", "receipt_validation"}:
            code, field, retryable = "receipt_validation", "receipt", True
        add(code, field, receipt_error, retryable)
    elif (
        str(child.get("status") or "") == "succeeded"
        and child.get("receiptValidated") is not True
    ):
        add(
            "receipt_validation",
            "receipt",
            "完了receiptをサーバー側で検証できませんでした。",
            True,
        )

    if failed_commands:
        if any(
            str(value.get("command") or "").strip() == "server commit"
            for value in failed_commands
        ):
            add(
                "server_validation",
                "server.commit",
                "server-owned ID/source/scope/transaction検証に失敗しました。",
                False,
            )
        add(
            "machine_validation",
            "result.commands",
            f"完了receipt内で失敗した機械検査を{len(failed_commands)}件検出しました。",
            True,
        )

    rollback = child.get("rollback")
    if isinstance(rollback, Mapping) and (
        str(rollback.get("status") or "") == "failed"
        or rollback.get("deltaUnknown") is True
        or bool(rollback.get("remainingChangedFiles"))
    ):
        add(
            "rollback_unsafe",
            "rollback",
            "失敗前の状態へ安全に戻せたことを確認できません。",
            False,
        )

    accepted = bool(
        str(child.get("status") or "") == "succeeded"
        and child.get("receiptValidated") is True
        and str(result.get("status") or "succeeded") == "succeeded"
        and not issues
    )
    if not accepted and not issues:
        add(
            "execution_failure",
            "child",
            "子作業を完了として検証できませんでした。",
            True,
        )

    model_pass_server_reject = bool(
        not accepted and commands and all(_command_passed(value) for value in commands)
    )
    status = (
        "accepted"
        if accepted
        else "blocked"
        if any(not issue["retryable"] for issue in issues)
        else "retryable"
    )
    return {
        "childRunId": str(child.get("runId") or child.get("childRunId") or ""),
        "questionId": str(question_id),
        "stageId": str(stage_id),
        "attempt": attempt,
        "status": status,
        "modelPassServerReject": model_pass_server_reject,
        "reason": _safe_optional_message(error),
        "resultSummary": _safe_optional_message(result_summary),
        "receiptError": _safe_optional_message(receipt_error),
        "failedChecks": failed_checks,
        "issues": issues,
    }


def feedback_prompt(feedback: Mapping[str, Any]) -> str:
    """Build the bounded correction instruction for the next App Server turn."""

    issues = []
    for value in feedback.get("issues") or []:
        if not isinstance(value, Mapping):
            continue
        issues.append(
            {
                "code": str(value.get("code") or "unknown"),
                "field": str(value.get("field") or "unknown"),
                "message": _safe_message(str(value.get("message") or "")),
                "retryable": bool(value.get("retryable")),
            }
        )
    failed_checks = []
    for value in list(feedback.get("failedChecks") or [])[:5]:
        if not isinstance(value, Mapping):
            continue
        failed_checks.append(_safe_failed_check(value, require_failure=False))
    payload = {
        "questionId": str(feedback.get("questionId") or ""),
        "stageId": str(feedback.get("stageId") or ""),
        "attempt": feedback.get("attempt"),
        "status": str(feedback.get("status") or ""),
        "reason": _safe_optional_message(feedback.get("reason")),
        "resultSummary": _safe_optional_message(feedback.get("resultSummary")),
        "receiptError": _safe_optional_message(feedback.get("receiptError")),
        "failedChecks": failed_checks,
        "issues": issues,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if payload["status"] == "accepted":
        instruction = "機械検査は通過しました。追加修正は不要です。"
    elif payload["status"] == "blocked":
        instruction = "自動再試行できない安全上の指摘です。fileを変更せず停止してください。"
    else:
        instruction = (
            "この一問・工程だけを指摘に従って修正し、正本の機械検査を再実行して"
            "完了receiptを更新してください。00_sourceは変更しないでください。"
        )
    return f"{instruction}\n検査フィードバック: {serialized}"


def build_improvement_report(
    question_executions: Iterable[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Aggregate validation feedback without turning a live run self-modifying."""

    grouped: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "questions": set(),
            "attempts": 0,
            "modelPassServerReject": False,
        }
    )
    logged_questions: set[str] = set()
    total_attempts = 0

    for question_index, question in enumerate(question_executions or ()):
        if not isinstance(question, Mapping):
            continue
        question_id = str(
            question.get("questionId")
            or question.get("sourceQuestionKey")
            or question.get("questionKey")
            or f"question-{question_index + 1}"
        )
        stages = question.get("stages")
        if not isinstance(stages, list):
            continue
        for stage in stages:
            if not isinstance(stage, Mapping):
                continue
            stage_id = str(stage.get("stageId") or "unknown")
            attempts = stage.get("validationAttempts")
            if not isinstance(attempts, list):
                continue
            for raw_attempt in attempts:
                if not isinstance(raw_attempt, Mapping):
                    continue
                feedback = raw_attempt.get("feedback")
                feedback = feedback if isinstance(feedback, Mapping) else raw_attempt
                issues = feedback.get("issues")
                if not isinstance(issues, list):
                    issues = []
                total_attempts += 1
                logged_questions.add(question_id)
                model_rejected = bool(
                    feedback.get("modelPassServerReject")
                    or raw_attempt.get("modelPassServerReject")
                )
                attempt_keys: set[tuple[str, str, str]] = set()
                for issue in issues:
                    if not isinstance(issue, Mapping):
                        continue
                    code = str(issue.get("code") or "unknown")
                    field = str(issue.get("field") or "unknown")
                    attempt_keys.add((stage_id, code, field))
                for key in attempt_keys:
                    grouped[key]["questions"].add(question_id)
                    grouped[key]["attempts"] += 1
                    grouped[key]["modelPassServerReject"] |= model_rejected

    findings: list[dict[str, Any]] = []
    for (stage_id, code, field), values in sorted(grouped.items()):
        distinct_question_count = len(values["questions"])
        model_rejected = bool(values["modelPassServerReject"])
        findings.append(
            {
                "stageId": stage_id,
                "code": code,
                "field": field,
                "distinctQuestionCount": distinct_question_count,
                "attemptCount": int(values["attempts"]),
                "modelPassServerReject": model_rejected,
                "candidate": distinct_question_count >= 3 or model_rejected,
            }
        )
    return {
        "schemaVersion": "question-maintenance-improvement-report/v1",
        "distinctQuestionCount": len(logged_questions),
        "attemptCount": total_attempts,
        "findings": findings,
    }


def write_improvement_report(
    run_dir: Path, report: Mapping[str, Any]
) -> Path:
    """Atomically write the terminal improvement report without following links."""

    run_dir = Path(run_dir)
    _reject_symlink(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    _reject_symlink(run_dir)
    target = run_dir / "improvement_report.json"
    if target.is_symlink():
        raise ValueError("improvement_report.jsonにsymlinkは使用できません。")

    data = (
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".improvement_report.", suffix=".tmp", dir=run_dir
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _classify_message(message: str) -> tuple[str, str, bool]:
    folded = message.casefold()
    categories = (
        (
            (
                "aggregate review execution evidenceが予約契約と一致しません",
                "aggregate review checkpointに未知のslotがあります",
                "aggregate review slotの形式が不正です",
                "aggregate review slotの番号又は状態が不正です",
                "確定済みaggregate review slotの証拠が不正です",
                "aggregate review slotとlegacy配列が一致しません",
                "aggregate review checkpoint slotsの形式が不正です",
                "legacy aggregate review checkpointの形式が不正です",
                "legacy aggregate review executionの順序が不正です",
                "aggregate review slot予約を再読検証できません",
                "aggregate review予約取消を再読検証できません",
                "aggregate review予約を原子的に取消できません",
                "aggregate review checkpoint signatureが一致しません",
                "開始済みaggregate review slotを確認できません",
                "aggregate review slot確定を再読検証できません",
                "aggregate review consensus signatureが一致しません",
                "二つのaggregate review slot確定前にconsensusを保存できません",
                "aggregate review consensusを再読検証できません",
            ),
            (
                "aggregate_review_checkpoint_integrity",
                "aggregateAnswerReview.checkpoint",
                False,
            ),
        ),
        (
            ("stable source identity", "stable parent identity"),
            ("stable_parent_identity", "record.stableParentIdentity", False),
        ),
        (
            ("集約回答レビューを保留", "aggregate review checkpoint mismatch"),
            ("aggregate_review_hold", "aggregateAnswerReview", False),
        ),
        (
            ("00_source", "source immut", "source不変"),
            ("source_immutability", "00_source", False),
        ),
        (
            (
                "policyfingerprint",
                "policy fingerprint",
                "policy_version",
                "policy version",
                "方針版",
                "正本文書fingerprint",
                "正本文書のfingerprint",
                "作業版又は正本文書が変更",
                "作業版または正本文書が変更",
            ),
            ("policy_drift", "workflow.policy", False),
        ),
        (
            (
                "baseline",
                "manifest",
                "manifest安全",
                "manifest integrity",
                "成功receiptの保存後",
                "成功receiptの検出後",
                "書込transaction",
                "未確定差分の解決記録",
            ),
            ("transaction_integrity", "transaction", False),
        ),
        (
            ("symlink", "symbolic link"),
            ("symlink_violation", "filesystem.symlink", False),
        ),
        (
            (
                "整備責務外",
                "責務外のfile",
                "責務外file",
                "allowed scope",
                "allowed_scope",
                "out of scope",
                "書込範囲外",
                "許可範囲外",
                "許可された範囲外",
                "writable root外",
                "repository外",
                "repository外のfile変更",
                "allowed write",
                "許可された書込file",
                "agent_output",
                "対象外file",
                "対象外のfile",
                "未定義のpatch層",
                "未定義の整備領域",
                "書込範囲を安全に判定できない",
                "完了receiptに未記載のfile変更",
                "実際の最終差分にないfile",
            ),
            ("scope_violation", "writeScope", False),
        ),
        (
            (
                "rollback failed",
                "rollbackでき",
                "rollback又は",
                "rollback後も",
                "残存差分",
                "差分を確認でき",
            ),
            ("rollback_unsafe", "rollback", False),
        ),
        (
            (
                "sourcequestionkey",
                "sourcerecordref",
                "source id binding",
                "source binding",
            ),
            ("source_binding", "record.sourceBinding", True),
        ),
        (
            (
                "reviewquestionid",
                "review_question_id",
                "publicquestionid",
                "public_question_id",
                "record identity",
                "record scope",
                "対象record",
                "問題id",
                "questionid",
            ),
            ("record_identity", "record.identity", True),
        ),
        (
            ("receipt", "完了記録"),
            ("receipt_validation", "receipt", True),
        ),
        (
            ("機械検査", "機械検証", "検証に失敗", "quality-gate", "checker"),
            ("machine_validation", "validation", True),
        ),
    )
    for markers, result in categories:
        if any(marker in folded for marker in markers):
            return result
    return "execution_failure", "child", True


def _command_failed(command: Mapping[str, Any]) -> bool:
    status = str(command.get("status") or "").casefold()
    if status in _FAILED_COMMAND_STATUSES:
        return True
    exit_code = command.get("exitCode")
    return (
        isinstance(exit_code, int)
        and not isinstance(exit_code, bool)
        and exit_code != 0
    )


def _command_passed(command: Mapping[str, Any]) -> bool:
    status = str(command.get("status") or "").casefold()
    if status:
        return status in _PASSED_COMMAND_STATUSES
    exit_code = command.get("exitCode")
    return (
        isinstance(exit_code, int)
        and not isinstance(exit_code, bool)
        and exit_code == 0
    )


def _safe_message(value: str) -> str:
    message = " ".join(str(value).split())
    for pattern in _SECRET_PATTERNS:
        message = pattern.sub(
            lambda match: (
                f"{match.group(1) if match.lastindex else ''}[REDACTED]"
            ),
            message,
        )
    if len(message) > _MESSAGE_LIMIT:
        message = message[: _MESSAGE_LIMIT - 1].rstrip() + "…"
    return message or "検査に失敗しました。"


def _safe_optional_message(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    return _safe_message(str(value))


def _safe_failed_check(
    command: Mapping[str, Any], *, require_failure: bool = True
) -> dict[str, Any]:
    if require_failure and not _command_failed(command):
        raise ValueError("failedChecksには失敗した検査だけを指定してください。")
    raw_command = command.get("command") or command.get("name") or "機械検査"
    exit_code = command.get("exitCode")
    return {
        "command": _safe_message(str(raw_command)),
        "status": _safe_message(str(command.get("status") or "failed")),
        "exitCode": (
            exit_code
            if isinstance(exit_code, int) and not isinstance(exit_code, bool)
            else None
        ),
    }


def _reject_symlink(path: Path) -> None:
    # The repository itself may be reached through a stable checkout symlink.
    # Reject the caller-controlled run directory and report file, rather than
    # unrelated platform aliases such as macOS /var -> /private/var.
    if path.is_symlink():
        raise ValueError(f"保存先にsymlinkは使用できません: {path}")
