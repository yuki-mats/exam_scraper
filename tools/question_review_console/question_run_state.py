from __future__ import annotations

import copy
import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from tools.question_review_console.question_work_queue import queue_summary
from tools.question_review_console.review_store import atomic_write


RUN_SCHEMA_VERSION = "question-maintenance-run/v2"
PLAN_SCHEMA_VERSION = "question-maintenance-plan/v2"
QUESTION_SCHEMA_VERSION = "question-maintenance-question/v2"
SUMMARY_SCHEMA_VERSION = "question-maintenance-summary/v2"

# These values are immutable inputs or per-question execution records.  They
# belong in plan.json / questions/*.json, never in the frequently rewritten
# parent manifest.
PLAN_OWNED_FIELDS = frozenset(
    {
        "allowedPatchDirs",
        "allowedPatchFiles",
        "allowedWriteAreas",
        "allowedWriteFiles",
        "canonicalDocs",
        "outputFiles",
        "policyFingerprints",
        "policyTargets",
        "policyVersions",
        "progressStages",
        "progressTargets",
        "questionExecutions",
        "questionIds",
        "readFieldsByStage",
        "resolvableFailedDeltaPaths",
        "resumeWorkItemKeys",
        "selectedFieldsByStage",
        "selectedUpdateTargets",
        "sourceFiles",
        "stagePlans",
        "targetQuestionIds",
        "targetQuestionKeys",
        "targetRecordAliasGroups",
        "targetRecordAliases",
        "targetRecordBindings",
        "targetRecordScopes",
        "targetSourceRecordScopes",
        "updateTargets",
        "workVersionReceipt",
    }
)

_COMPACT_STAGE_FIELDS = (
    "stageId",
    "stageCode",
    "stageLabel",
    "workItemKey",
    "status",
    "error",
    "retryDeferred",
    "startedAt",
    "finishedAt",
    "projectedInputPath",
    "projectedInputHash",
    "outputFingerprint",
)
_COMPACT_QUESTION_FIELDS = (
    "questionId",
    "uiQuestionId",
    "questionKey",
    "reviewKey",
    "sourceQuestionKey",
    "sourceRecordRef",
    "reviewQuestionId",
    "listGroupId",
    "sectionLabel",
    "questionLabel",
    "displayLabel",
    "displayOrder",
    "bodyPreview",
    "status",
)


class QuestionRunStateError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _without_self_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): copy.deepcopy(item)
        for key, item in value.items()
        if key != "selfHash"
    }


def _with_self_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = _without_self_hash(value)
    payload["selfHash"] = _sha256(payload)
    return payload


def question_state_filename(question_id: str) -> str:
    normalized = str(question_id).strip()
    if not normalized:
        raise QuestionRunStateError("一問stateにはquestionIdが必要です。")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() + ".json"


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QuestionRunStateError(f"{label}を読めません: {path}") from exc
    if not isinstance(value, Mapping):
        raise QuestionRunStateError(f"{label}がobjectではありません: {path}")
    return dict(value)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    atomic_write(
        path,
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
    )


class QuestionRunStateStore:
    """Durable current state for one maintenance run.

    plan.json is immutable, manifest.json is a small coordinator record, and
    each question owns exactly one mutable JSON state file.  Derived summary
    data is replaceable and can always be rebuilt from the question files.
    """

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()
        self._plan_cache_lock = threading.RLock()
        self._plan_cache: dict[
            Path,
            tuple[tuple[int, int, int, int], str, dict[str, Any]],
        ] = {}

    @staticmethod
    def is_current(manifest: Mapping[str, Any]) -> bool:
        return manifest.get("schemaVersion") == RUN_SCHEMA_VERSION

    def initialize(
        self,
        run_dir: Path,
        plan: Mapping[str, Any],
        parent_manifest: Mapping[str, Any],
    ) -> dict[str, Any]:
        run_dir = run_dir.resolve()
        if not run_dir.is_relative_to(self.repo_root):
            raise QuestionRunStateError("run directoryがrepository外です。")
        executions = [
            copy.deepcopy(dict(value))
            for value in plan.get("questionExecutions") or []
            if isinstance(value, Mapping)
        ]
        if not executions:
            raise QuestionRunStateError(
                "現行maintenance runには一問queueが必要です。"
            )
        question_ids = [
            str(value.get("questionId") or "").strip() for value in executions
        ]
        if any(not value for value in question_ids):
            raise QuestionRunStateError("一問queueにquestionIdがありません。")
        if len(question_ids) != len(set(question_ids)):
            raise QuestionRunStateError("一問queueのquestionIdが重複しています。")

        plan_path = run_dir / "plan.json"
        question_dir = run_dir / "questions"
        summary_path = run_dir / "question_summary.json"
        if plan_path.exists() or question_dir.exists() or summary_path.exists():
            raise QuestionRunStateError("現行run stateの保存先が既に存在します。")
        question_dir.mkdir(parents=True, exist_ok=False)

        immutable_plan = copy.deepcopy(dict(plan))
        plan_payload_without_hash = {
            "schemaVersion": PLAN_SCHEMA_VERSION,
            "createdAt": str(parent_manifest.get("createdAt") or _now()),
            "plan": immutable_plan,
        }
        plan_hash = _sha256(plan_payload_without_hash)
        plan_payload = {
            **plan_payload_without_hash,
            "planHash": plan_hash,
        }
        _write_json(plan_path, plan_payload)

        for execution in executions:
            question_id = str(execution["questionId"])
            state = _with_self_hash(
                {
                    "schemaVersion": QUESTION_SCHEMA_VERSION,
                    "planHash": plan_hash,
                    "questionId": question_id,
                    "revision": 0,
                    "activeAttemptId": None,
                    "updatedAt": str(parent_manifest.get("createdAt") or _now()),
                    "execution": execution,
                    "attemptArtifacts": {},
                    "validatedReceipts": {},
                }
            )
            path = question_dir / question_state_filename(question_id)
            if path.exists():
                raise QuestionRunStateError(
                    "questionId hashが同じstate fileへ衝突しました。"
                )
            _write_json(path, state)

        compact_parent = {
            str(key): copy.deepcopy(value)
            for key, value in parent_manifest.items()
            if key not in PLAN_OWNED_FIELDS
        }
        compact_parent.update(
            schemaVersion=RUN_SCHEMA_VERSION,
            planPath=str(plan_path.relative_to(self.repo_root)),
            planHash=plan_hash,
            questionStateDirectory=str(question_dir.relative_to(self.repo_root)),
            questionSummaryPath=str(summary_path.relative_to(self.repo_root)),
            questionStateCount=len(executions),
            workVersionRecordedCount=self._receipt_count_from_plan(plan),
            sharedWorkVersionReceipts=[],
        )
        summary = self.rebuild_summary(run_dir, compact_parent)
        compact_parent.update(
            questionExecutionSummary=copy.deepcopy(summary["queueSummary"]),
            blockedQuestionCount=int(
                summary["queueSummary"]["blockedQuestionCount"]
            ),
            blockedWorkItemCount=int(
                summary["queueSummary"]["blockedWorkItemCount"]
            ),
            validatedQuestionCount=int(
                summary["queueSummary"]["validatedQuestionCount"]
            ),
            validatedWorkItemCount=int(
                summary["queueSummary"]["validatedWorkItemCount"]
            ),
        )
        return compact_parent

    def hydrate(
        self,
        run_dir: Path,
        manifest: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not self.is_current(manifest):
            return copy.deepcopy(dict(manifest))
        plan = self.load_plan(run_dir, manifest)
        if "selectedUpdateTargetIds" in manifest:
            manifest_target_ids = manifest.get("selectedUpdateTargetIds")
            if (
                not isinstance(manifest_target_ids, list)
                or [str(value) for value in manifest_target_ids]
                != [
                    str(value)
                    for value in plan.get("selectedUpdateTargetIds") or []
                ]
            ):
                raise QuestionRunStateError(
                    "親manifestの再開項目がimmutable planと一致しません。"
                )
        executions = self.load_executions(run_dir, manifest, plan=plan)
        hydrated = {
            **copy.deepcopy(plan),
            **copy.deepcopy(dict(manifest)),
            "questionExecutions": executions,
        }
        summary = queue_summary(executions)
        hydrated["questionExecutionSummary"] = summary
        hydrated["workVersionReceipt"] = self.aggregate_receipts(
            run_dir,
            manifest,
            plan=plan,
        )
        return hydrated

    def load_plan(
        self,
        run_dir: Path,
        manifest: Mapping[str, Any],
    ) -> dict[str, Any]:
        return copy.deepcopy(
            self._validated_plan(run_dir, manifest)
        )

    def verify_plan(
        self,
        run_dir: Path,
        manifest: Mapping[str, Any],
    ) -> None:
        """Verify immutable-plan identity without copying its full payload."""

        self._validated_plan(run_dir, manifest)

    def _validated_plan(
        self,
        run_dir: Path,
        manifest: Mapping[str, Any],
    ) -> dict[str, Any]:
        path = self._owned_path(
            run_dir,
            manifest.get("planPath"),
            expected_name="plan.json",
        )
        expected_hash = str(manifest.get("planHash") or "")
        if not expected_hash:
            raise QuestionRunStateError(
                "immutable planのhashがmanifestにありません。"
            )
        signature = self._file_signature(path, label="immutable plan")
        with self._plan_cache_lock:
            cached = self._plan_cache.get(path)
            if (
                cached is not None
                and cached[0] == signature
                and cached[1] == expected_hash
            ):
                return cached[2]
        payload = _read_json(path, label="immutable plan")
        if self._file_signature(path, label="immutable plan") != signature:
            raise QuestionRunStateError(
                "immutable planが読取中に変更されました。"
            )
        if payload.get("schemaVersion") != PLAN_SCHEMA_VERSION:
            raise QuestionRunStateError("immutable planのschemaVersionが不正です。")
        plan = payload.get("plan")
        if not isinstance(plan, Mapping):
            raise QuestionRunStateError("immutable planにplan objectがありません。")
        without_hash = {
            str(key): copy.deepcopy(value)
            for key, value in payload.items()
            if key != "planHash"
        }
        actual_hash = _sha256(without_hash)
        if (
            payload.get("planHash") != expected_hash
            or actual_hash != expected_hash
        ):
            raise QuestionRunStateError(
                "immutable planのhashが一致しないためrunを停止しました。"
            )
        validated = copy.deepcopy(dict(plan))
        with self._plan_cache_lock:
            self._plan_cache[path] = (
                signature,
                expected_hash,
                validated,
            )
        return validated

    def load_question(
        self,
        run_dir: Path,
        manifest: Mapping[str, Any],
        question_id: str,
    ) -> dict[str, Any]:
        path = self.question_path(run_dir, manifest, question_id)
        state = _read_json(path, label="一問state")
        return self._validate_question_state(
            manifest,
            state,
            question_id=question_id,
        )

    def load_question_by_hash(
        self,
        run_dir: Path,
        manifest: Mapping[str, Any],
        question_hash: str,
    ) -> dict[str, Any]:
        """Resolve one question directly without scanning the immutable plan."""

        normalized = str(question_hash).strip()
        if (
            len(normalized) != 64
            or any(character not in "0123456789abcdef" for character in normalized)
        ):
            raise QuestionRunStateError("一問stateのquestion hashが不正です。")
        self.verify_plan(run_dir, manifest)
        directory = self._owned_path(
            run_dir,
            manifest.get("questionStateDirectory"),
            expected_name="questions",
            directory=True,
        )
        state = _read_json(
            directory / f"{normalized}.json",
            label="一問state",
        )
        question_id = str(state.get("questionId") or "")
        if question_state_filename(question_id).removesuffix(".json") != normalized:
            raise QuestionRunStateError(
                "一問stateのquestion hashがquestionIdと一致しません。"
            )
        return self._validate_question_state(
            manifest,
            state,
            question_id=question_id,
        )

    @staticmethod
    def _validate_question_state(
        manifest: Mapping[str, Any],
        state: Mapping[str, Any],
        *,
        question_id: str,
    ) -> dict[str, Any]:
        if (
            state.get("schemaVersion") != QUESTION_SCHEMA_VERSION
            or state.get("questionId") != question_id
            or state.get("planHash") != manifest.get("planHash")
        ):
            raise QuestionRunStateError(
                f"一問stateのidentityが一致しません: {question_id}"
            )
        expected_hash = str(state.get("selfHash") or "")
        actual_hash = _sha256(_without_self_hash(state))
        if not expected_hash or actual_hash != expected_hash:
            raise QuestionRunStateError(
                f"一問stateのselfHashが一致しません: {question_id}"
            )
        execution = state.get("execution")
        if (
            not isinstance(execution, Mapping)
            or execution.get("questionId") != question_id
        ):
            raise QuestionRunStateError(
                f"一問stateのexecutionが不正です: {question_id}"
            )
        revision = state.get("revision")
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 0
        ):
            raise QuestionRunStateError(
                f"一問stateのrevisionが不正です: {question_id}"
            )
        return dict(state)

    def load_executions(
        self,
        run_dir: Path,
        manifest: Mapping[str, Any],
        *,
        plan: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        current_plan = dict(plan) if plan is not None else self.load_plan(
            run_dir, manifest
        )
        question_ids = self._plan_question_ids(current_plan)
        executions = [
            copy.deepcopy(
                dict(
                    self.load_question(
                        run_dir,
                        manifest,
                        question_id,
                    )["execution"]
                )
            )
            for question_id in question_ids
        ]
        expected_count = int(manifest.get("questionStateCount") or 0)
        if expected_count != len(executions):
            raise QuestionRunStateError(
                "一問state数がimmutable planと一致しません。"
            )
        return executions

    def update_question(
        self,
        run_dir: Path,
        manifest: Mapping[str, Any],
        question_id: str,
        update: Callable[[dict[str, Any]], None],
        *,
        expected_revision: int | None = None,
        expected_active_attempt_id: str | None = None,
    ) -> dict[str, Any]:
        state = self.load_question(run_dir, manifest, question_id)
        revision = int(state["revision"])
        if expected_revision is not None and revision != expected_revision:
            raise QuestionRunStateError(
                f"一問stateのstale updateを拒否しました: {question_id}"
            )
        if (
            expected_active_attempt_id is not None
            and state.get("activeAttemptId") != expected_active_attempt_id
        ):
            raise QuestionRunStateError(
                f"一問stateのattemptが更新済みです: {question_id}"
            )
        next_state = copy.deepcopy(state)
        update(next_state)
        if next_state.get("questionId") != question_id:
            raise QuestionRunStateError("一問stateのquestionIdは変更できません。")
        if next_state.get("planHash") != manifest.get("planHash"):
            raise QuestionRunStateError("一問stateのplanHashは変更できません。")
        if next_state.get("revision") != revision:
            raise QuestionRunStateError("一問stateのrevisionは直接変更できません。")
        next_state["revision"] = revision + 1
        next_state["updatedAt"] = _now()
        next_state = _with_self_hash(next_state)
        _write_json(
            self.question_path(run_dir, manifest, question_id),
            next_state,
        )
        return copy.deepcopy(next_state)

    def rebuild_summary(
        self,
        run_dir: Path,
        manifest: Mapping[str, Any],
    ) -> dict[str, Any]:
        plan = self.load_plan(run_dir, manifest)
        executions = self.load_executions(run_dir, manifest, plan=plan)
        compact_questions = [
            self._compact_execution(execution) for execution in executions
        ]
        summary = {
            "schemaVersion": SUMMARY_SCHEMA_VERSION,
            "planHash": str(manifest.get("planHash") or ""),
            "updatedAt": _now(),
            "questionCount": len(compact_questions),
            "queueSummary": queue_summary(executions),
            "questions": compact_questions,
        }
        path = self._owned_path(
            run_dir,
            manifest.get("questionSummaryPath"),
            expected_name="question_summary.json",
        )
        _write_json(path, summary)
        return summary

    def load_summary(
        self,
        run_dir: Path,
        manifest: Mapping[str, Any],
    ) -> dict[str, Any]:
        path = self._owned_path(
            run_dir,
            manifest.get("questionSummaryPath"),
            expected_name="question_summary.json",
        )
        summary = _read_json(path, label="question summary")
        questions = summary.get("questions")
        question_count = int(manifest.get("questionStateCount") or 0)
        if (
            summary.get("schemaVersion") != SUMMARY_SCHEMA_VERSION
            or summary.get("planHash") != manifest.get("planHash")
            or summary.get("questionCount") != question_count
            or not isinstance(questions, list)
            or len(questions) != question_count
            or any(not isinstance(value, Mapping) for value in questions)
        ):
            raise QuestionRunStateError("question summaryのidentityが不正です。")
        return summary

    def aggregate_receipts(
        self,
        run_dir: Path,
        manifest: Mapping[str, Any],
        *,
        plan: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        current_plan = dict(plan) if plan is not None else self.load_plan(
            run_dir, manifest
        )
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        initial = current_plan.get("workVersionReceipt")
        initial_items = (
            initial.get("items") if isinstance(initial, Mapping) else []
        )
        for raw in [
            *(initial_items or []),
            *(
                manifest.get("sharedWorkVersionReceipts")
                if isinstance(
                    manifest.get("sharedWorkVersionReceipts"),
                    list,
                )
                else []
            ),
            *(
                receipt
                for question_id in self._plan_question_ids(current_plan)
                for receipt in (
                    self.load_question(
                        run_dir,
                        manifest,
                        question_id,
                    ).get("validatedReceipts")
                    or {}
                ).values()
            ),
        ]:
            if not isinstance(raw, Mapping):
                continue
            item = copy.deepcopy(dict(raw))
            encoded = json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if encoded in seen:
                continue
            seen.add(encoded)
            items.append(item)
        return {
            "recordedCount": sum(
                int(value.get("recordedCount") or 0) for value in items
            ),
            "items": items,
        }

    def question_path(
        self,
        run_dir: Path,
        manifest: Mapping[str, Any],
        question_id: str,
    ) -> Path:
        directory = self._owned_path(
            run_dir,
            manifest.get("questionStateDirectory"),
            expected_name="questions",
            directory=True,
        )
        return directory / question_state_filename(question_id)

    def question_ids(
        self,
        run_dir: Path,
        manifest: Mapping[str, Any],
    ) -> list[str]:
        return self._plan_question_ids(self.load_plan(run_dir, manifest))

    def _owned_path(
        self,
        run_dir: Path,
        raw_path: Any,
        *,
        expected_name: str,
        directory: bool = False,
    ) -> Path:
        if not raw_path:
            raise QuestionRunStateError(
                f"現行runに{expected_name} pathがありません。"
            )
        path = (self.repo_root / str(raw_path)).resolve()
        run_dir = run_dir.resolve()
        if (
            not path.is_relative_to(run_dir)
            or path.name != expected_name
            or (directory and path.parent != run_dir)
            or (not directory and path.parent != run_dir)
        ):
            raise QuestionRunStateError(
                f"現行runの{expected_name} pathが不正です。"
            )
        return path

    @staticmethod
    def _file_signature(
        path: Path,
        *,
        label: str,
    ) -> tuple[int, int, int, int]:
        try:
            stat = path.stat()
        except OSError as exc:
            raise QuestionRunStateError(
                f"{label}のfile情報を確認できません: {path}"
            ) from exc
        return (
            int(stat.st_ino),
            int(stat.st_size),
            int(stat.st_mtime_ns),
            int(stat.st_ctime_ns),
        )

    @staticmethod
    def _plan_question_ids(plan: Mapping[str, Any]) -> list[str]:
        raw = plan.get("questionExecutions")
        if not isinstance(raw, list):
            raise QuestionRunStateError(
                "immutable planにquestionExecutionsがありません。"
            )
        ids = [
            str(value.get("questionId") or "").strip()
            for value in raw
            if isinstance(value, Mapping)
        ]
        if len(ids) != len(raw) or any(not value for value in ids):
            raise QuestionRunStateError(
                "immutable planのquestionExecutionsが不正です。"
            )
        if len(ids) != len(set(ids)):
            raise QuestionRunStateError(
                "immutable planのquestionIdが重複しています。"
            )
        return ids

    @staticmethod
    def _compact_execution(execution: Mapping[str, Any]) -> dict[str, Any]:
        compact = {
            field: copy.deepcopy(execution[field])
            for field in _COMPACT_QUESTION_FIELDS
            if field in execution
        }
        compact["stages"] = [
            {
                field: copy.deepcopy(stage[field])
                for field in _COMPACT_STAGE_FIELDS
                if field in stage
            }
            for stage in execution.get("stages") or []
            if isinstance(stage, Mapping)
        ]
        return compact

    @staticmethod
    def _receipt_count_from_plan(plan: Mapping[str, Any]) -> int:
        receipt = plan.get("workVersionReceipt")
        return (
            int(receipt.get("recordedCount") or 0)
            if isinstance(receipt, Mapping)
            else 0
        )


def update_active_attempt_from_execution(state: dict[str, Any]) -> None:
    """Derive activeAttemptId from the current question execution.

    Attempts are append-only.  Only a non-terminal last validation attempt may
    be active; completed attempts remain immutable historical evidence.
    """

    execution = state.get("execution")
    if not isinstance(execution, Mapping):
        raise QuestionRunStateError("一問stateにexecutionがありません。")
    active: str | None = None
    for stage in execution.get("stages") or []:
        if not isinstance(stage, Mapping):
            continue
        attempts = [
            value
            for value in stage.get("validationAttempts") or []
            if isinstance(value, Mapping)
        ]
        if not attempts:
            continue
        last = attempts[-1]
        if str(last.get("status") or "") in {"queued", "running", "preparing"}:
            active = str(
                last.get("attemptId")
                or last.get("childRunId")
                or ""
            ) or None
    state["activeAttemptId"] = active


def validated_receipt_key(question_id: str, stage_id: str) -> str:
    return hashlib.sha256(
        f"{question_id}\0{stage_id}".encode("utf-8")
    ).hexdigest()
