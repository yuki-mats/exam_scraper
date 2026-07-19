from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar


MODEL_BATCH_SIZE = 5


class QuestionBatchReceiptError(ValueError):
    pass


T = TypeVar("T")


def batch_size(question_concurrency: int) -> int:
    return max(1, min(MODEL_BATCH_SIZE, int(question_concurrency)))


def model_worker_limit(question_concurrency: int) -> int:
    size = batch_size(question_concurrency)
    return max(1, math.ceil(int(question_concurrency) / size))


def chunks(values: Sequence[T], size: int) -> list[list[T]]:
    normalized = max(1, int(size))
    return [
        list(values[index : index + normalized])
        for index in range(0, len(values), normalized)
    ]


@dataclass(frozen=True)
class BatchQuestionResult:
    question_id: str
    status: str
    summary: str
    commands: tuple[dict[str, str], ...]
    changed_files: tuple[str, ...]


def _commands(
    value: Any,
    *,
    question_id: str,
    status: str,
) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list):
        raise QuestionBatchReceiptError(
            f"{question_id}: questionResults.commandsは配列で保存してください。"
        )
    normalized: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise QuestionBatchReceiptError(
                f"{question_id}: questionResults.commandsの要素がobjectではありません。"
            )
        command = str(item.get("command") or "").strip()
        command_status = {
            "passed": "pass",
            "failed": "fail",
        }.get(
            str(item.get("status") or "").strip(),
            str(item.get("status") or "").strip(),
        )
        if not command or command_status not in {"pass", "fail"}:
            raise QuestionBatchReceiptError(
                f"{question_id}: commandとpass/failのstatusが必要です。"
            )
        normalized.append({"command": command[:2000], "status": command_status})
    if status == "succeeded" and (
        not normalized or any(item["status"] != "pass" for item in normalized)
    ):
        raise QuestionBatchReceiptError(
            f"{question_id}: 合格結果には1件以上のpass検証が必要です。"
        )
    return tuple(normalized)


def validate_batch_question_results(
    receipt: Mapping[str, Any],
    expected_question_ids: Iterable[str],
) -> tuple[BatchQuestionResult, ...]:
    expected = tuple(
        dict.fromkeys(str(value) for value in expected_question_ids if value)
    )
    raw_results = receipt.get("questionResults")
    if not isinstance(raw_results, list):
        raise QuestionBatchReceiptError(
            "複数問receiptにquestionResults配列がありません。"
        )

    results: dict[str, BatchQuestionResult] = {}
    for raw in raw_results:
        if not isinstance(raw, Mapping):
            raise QuestionBatchReceiptError("questionResultsの要素がobjectではありません。")
        question_id = str(raw.get("questionId") or "").strip()
        if question_id not in expected:
            raise QuestionBatchReceiptError(
                f"対象外の問題IDがquestionResultsにあります: {question_id or '(empty)'}"
            )
        if question_id in results:
            raise QuestionBatchReceiptError(
                f"questionResultsの問題IDが重複しています: {question_id}"
            )
        status = str(raw.get("status") or "").strip()
        if status not in {"succeeded", "failed"}:
            raise QuestionBatchReceiptError(
                f"{question_id}: statusはsucceeded又はfailedです。"
            )
        summary = str(raw.get("summary") or "").strip()
        if not summary:
            raise QuestionBatchReceiptError(f"{question_id}: summaryがありません。")
        changed_files = raw.get("changedFiles") or []
        if not isinstance(changed_files, list) or not all(
            isinstance(value, str) for value in changed_files
        ):
            raise QuestionBatchReceiptError(
                f"{question_id}: changedFilesは文字列配列で保存してください。"
            )
        results[question_id] = BatchQuestionResult(
            question_id=question_id,
            status=status,
            summary=summary[:4000],
            commands=_commands(
                raw.get("commands") or [],
                question_id=question_id,
                status=status,
            ),
            changed_files=tuple(dict.fromkeys(str(value) for value in changed_files)),
        )

    missing = [question_id for question_id in expected if question_id not in results]
    if missing:
        raise QuestionBatchReceiptError(
            "questionResultsに未記録の問題があります: " + ", ".join(missing)
        )
    return tuple(results[question_id] for question_id in expected)


def normalize_batch_question_results(
    receipt: Mapping[str, Any],
    expected_question_ids: Iterable[str],
) -> tuple[BatchQuestionResult, ...]:
    """Keep one malformed result from rejecting valid sibling results."""

    expected = tuple(
        dict.fromkeys(str(value) for value in expected_question_ids if value)
    )
    raw_results = receipt.get("questionResults")
    if not isinstance(raw_results, list):
        raise QuestionBatchReceiptError(
            "複数問receiptにquestionResults配列がありません。"
        )
    grouped: dict[str, list[Mapping[str, Any]]] = {
        question_id: [] for question_id in expected
    }
    for raw in raw_results:
        if not isinstance(raw, Mapping):
            raise QuestionBatchReceiptError(
                "questionResultsの要素がobjectではありません。"
            )
        question_id = str(raw.get("questionId") or "").strip()
        if question_id not in grouped:
            raise QuestionBatchReceiptError(
                f"対象外の問題IDがquestionResultsにあります: {question_id or '(empty)'}"
            )
        grouped[question_id].append(raw)

    normalized: list[BatchQuestionResult] = []
    for question_id in expected:
        candidates = grouped[question_id]
        if len(candidates) == 1:
            try:
                normalized.extend(
                    validate_batch_question_results(
                        {"questionResults": candidates},
                        [question_id],
                    )
                )
                continue
            except QuestionBatchReceiptError as exc:
                reason = str(exc)
        elif not candidates:
            reason = f"{question_id}: questionResultsに結果がありません。"
        else:
            reason = f"{question_id}: questionResultsが重複しています。"
        changed_files = tuple(
            dict.fromkeys(
                str(path)
                for candidate in candidates
                for path in (
                    candidate.get("changedFiles")
                    if isinstance(candidate.get("changedFiles"), list)
                    else []
                )
                if isinstance(path, str) and path
            )
        )
        normalized.append(
            BatchQuestionResult(
                question_id=question_id,
                status="failed",
                summary=reason,
                commands=(
                    {"command": "batch receipt contract", "status": "fail"},
                ),
                changed_files=changed_files,
            )
        )
    return tuple(normalized)


def validate_changed_file_attribution(
    question_results: Iterable[BatchQuestionResult],
    aggregate_changed_files: Iterable[str],
) -> None:
    attributed = {
        value
        for result in question_results
        for value in result.changed_files
        if value
    }
    aggregate = {str(value) for value in aggregate_changed_files if value}
    if attributed != aggregate:
        missing = sorted(aggregate - attributed)
        extra = sorted(attributed - aggregate)
        details = []
        if missing:
            details.append("問題別未帰属: " + ", ".join(missing))
        if extra:
            details.append("batch差分なし: " + ", ".join(extra))
        raise QuestionBatchReceiptError(
            "batch差分とquestionResults.changedFilesが一致しません。" + " ".join(details)
        )
