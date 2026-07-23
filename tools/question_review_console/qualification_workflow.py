from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from scripts.merge.merge_utils import (
    select_latest_patch_files,
    source_stem_from_patch_filename,
)
from scripts.common.question_identity import SourceIdentityBinding
from tools.question_review_console.failed_delta import unresolved_failed_delta_paths
from tools.question_review_console.law_audit_contract import LAW_AUDIT_ISSUES
from tools.question_review_console.prompt_builder import (
    law_audit_classification_safety_contract,
)
from tools.question_review_console.projection import record_identity_aliases
from tools.question_review_console.workflow_catalog import (
    WorkflowCatalog,
    normalize_policy_version,
)
from tools.question_review_console.work_versions import (
    QuestionWorkVersionStore,
    policy_fingerprint,
)

RUN_MODES = {
    "needed": "整備が必要な問題だけ",
    "group_refresh": "選択範囲の全問題を再整備",
    "remaining": "未作業のみ",
    "attention": "要確認のみ",
    "outdated": "洗い替え必要・未整備のみ",
    "refresh": "資格全体の全問題を再整備",
}


def _group_scope(
    list_group_id: str | None,
    list_group_ids: Iterable[str] | None,
) -> tuple[list[str], bool]:
    provided = list_group_ids is not None or list_group_id is not None
    raw = list(list_group_ids or [])
    if list_group_ids is None and list_group_id:
        raw = [list_group_id]
    selected = _ordered_unique(str(value).strip() for value in raw)
    if any(value == "__all__" for value in selected):
        raise ValueError("年度は具体的なlistGroupIdで指定してください。")
    if provided and not selected:
        raise ValueError("対象年度を一つ以上選択してください。")
    return selected, provided


def _question_range(value: Mapping[str, Any] | None) -> dict[str, int] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"start", "end"}:
        raise ValueError("問題番号範囲はstartとendで指定してください。")
    start = value.get("start")
    end = value.get("end")
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or start < 1
        or end < start
    ):
        raise ValueError("問題番号範囲は1以上かつstart以下でないendを指定してください。")
    return {"start": start, "end": end}


def _select_update_targets(
    definition: Mapping[str, Any],
    update_target_ids: Iterable[str] | None,
) -> list[dict[str, Any]]:
    available = [
        dict(value)
        for value in definition.get("updateTargets") or []
        if isinstance(value, Mapping)
    ]
    if update_target_ids is None:
        return available
    requested = _ordered_unique(str(value).strip() for value in update_target_ids)
    available_by_id = {
        str(value.get("selectionId") or ""): value for value in available
    }
    unknown = [value for value in requested if value not in available_by_id]
    if unknown:
        raise ValueError("更新項目がありません: " + ", ".join(unknown))
    if available and not requested:
        raise ValueError(f"{definition['label']}の更新項目を一つ以上選択してください。")
    return [available_by_id[value] for value in requested]


def _filter_question_range(
    questions: Iterable[Mapping[str, Any]],
    question_range: Mapping[str, int] | None,
) -> list[Mapping[str, Any]]:
    values = list(questions)
    if question_range is None:
        return values
    by_group: dict[str, list[Mapping[str, Any]]] = {}
    for question in values:
        by_group.setdefault(str(question.get("listGroupId") or ""), []).append(
            question
        )
    selected: list[Mapping[str, Any]] = []
    start = int(question_range["start"]) - 1
    end = int(question_range["end"])
    for group_id in sorted(by_group):
        selected.extend(sorted(by_group[group_id], key=_progress_sort_key)[start:end])
    return selected


def _filter_question_ids(
    questions: Iterable[Mapping[str, Any]],
    question_ids: Iterable[str] | None,
) -> tuple[list[Mapping[str, Any]], list[str] | None]:
    if question_ids is None:
        return list(questions), None
    requested = _ordered_unique(str(value).strip() for value in question_ids)
    if not requested:
        raise ValueError("対象問題を一つ以上指定してください。")
    by_id = {str(question.get("id") or ""): question for question in questions}
    unknown = [question_id for question_id in requested if question_id not in by_id]
    if unknown:
        raise ValueError(
            "選択したlistGroupIdsに対象問題がありません: " + ", ".join(unknown)
        )
    return [by_id[question_id] for question_id in requested], requested


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _has_patch(question: Mapping[str, Any], patch_dir: str) -> bool:
    marker = f"/{patch_dir}/"
    failed_paths = {
        str(path) for path in question.get("failedRunChangedPaths") or []
    }
    return any(
        marker in str(path) and str(path) not in failed_paths
        for path in question.get("paths", {}).get("patches") or []
    )


def _originalization_applicable(question: Mapping[str, Any]) -> bool:
    """Only sources without examYear enter the independent-question stage."""

    source = question.get("source")
    if not isinstance(source, Mapping):
        return False
    return source.get("examYear") in {None, ""}


def _issue_count(question: Mapping[str, Any], fields: set[str]) -> int:
    count = 0
    for issue in question.get("issues") or []:
        issue_fields = {str(value).split("[")[0] for value in issue.get("fields") or []}
        if issue_fields & fields:
            count += 1
    return count


def _status_from_coverage(
    *, total: int, complete: int, issue_count: int, downstream_count: int
) -> str:
    if total <= 0:
        return "waiting"
    if complete == total:
        return "attention" if issue_count else "ready"
    if complete == 0:
        return "attention" if downstream_count else "not_started"
    return "in_progress"


def _expected_patch_path(source_path: str, stage: Mapping[str, Any]) -> str:
    source = Path(source_path)
    group_dir = source.parent.parent
    merged = (
        "_merged"
        if str(stage["patchDir"])
        in {"18_law_context_prepared", "21_explanationText_added"}
        else ""
    )
    return str(
        group_dir
        / str(stage["patchDir"])
        / f"{source.stem}{merged}_{stage['patchSuffix']}.json"
    )


def _unique(values: Iterable[str]) -> list[str]:
    return sorted({value for value in values if value})


def _ordered_unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _required_maintenance_stage_ids(
    stages: Iterable[Mapping[str, Any]],
) -> list[str]:
    """Return the backend-owned stage selection for the top maintenance action."""

    ordered = [dict(stage) for stage in stages]
    selected = [
        str(stage["id"])
        for stage in ordered
        if stage.get("batchSelectable")
        and stage.get("versionTrackingActive")
        and (
            int(stage.get("versionOutdatedCount") or 0)
            or int(stage.get("versionUnrecordedCount") or 0)
            or (
                str(stage.get("id") or "") == "law_audit"
                and int(stage.get("issueCount") or 0)
            )
        )
    ]
    selected_ids = set(selected)
    category = next(
        (stage for stage in ordered if stage.get("id") == "category_setup"),
        None,
    )
    if category and category.get("status") != "ready":
        selected_ids.add("category_setup")
        question_set = next(
            (stage for stage in ordered if stage.get("id") == "question_set"),
            None,
        )
        if question_set and question_set.get("batchSelectable"):
            # taxonomyを作り直した後は、既存の工程版がcurrentでも
            # questionSetIdを選択scope内で再検証する。
            selected_ids.add("question_set")
    return [
        str(stage["id"])
        for stage in ordered
        if str(stage.get("id") or "") in selected_ids
    ]


def _target_record_alias_group(question: Mapping[str, Any]) -> list[str]:
    aliases: set[str] = set()
    for key in ("source", "projected"):
        value = question.get(key)
        if isinstance(value, Mapping):
            aliases.update(record_identity_aliases(value))
    for value in (
        question.get("id"),
        question.get("originalQuestionId"),
        question.get("sourceQuestionKey"),
        question.get("sourceRecordRef"),
    ):
        text = str(value or "").strip()
        if text and not text.startswith(("http://", "https://")):
            aliases.add(text)
    return sorted(aliases)


def _progress_target(question: Mapping[str, Any]) -> dict[str, Any]:
    question_id = str(question.get("id") or "")
    question_key = str(
        question.get("sourceQuestionKey")
        or question.get("reviewKey")
        or question_id
    )
    source = question.get("source")
    source = source if isinstance(source, Mapping) else {}
    section_label = str(
        source.get("category")
        or question.get("examLabel")
        or source.get("sourceSubject")
        or ""
    ).strip()
    question_label = str(question.get("questionLabel") or "").strip()
    return {
        "id": question_id or question_key,
        "uiQuestionId": question_id or question_key,
        "questionKey": question_key,
        "reviewKey": str(question.get("reviewKey") or ""),
        "sourceQuestionKey": str(question.get("sourceQuestionKey") or ""),
        "sourceRecordRef": str(question.get("sourceRecordRef") or ""),
        "reviewQuestionId": str(question.get("originalQuestionId") or ""),
        "listGroupId": str(question.get("listGroupId") or ""),
        "sectionLabel": section_label,
        "questionLabel": question_label,
        "displayLabel": " ".join(
            value for value in (section_label, question_label) if value
        ),
        "bodyPreview": str(question.get("body") or "")[:240],
        "stateHash": str(question.get("stateHash") or ""),
        "aliases": _target_record_alias_group(question),
    }


def _target_record_binding(target: Mapping[str, Any]) -> dict[str, Any]:
    identity = SourceIdentityBinding.from_mapping(target)
    return {
        "uiQuestionId": str(target.get("uiQuestionId") or ""),
        **identity.as_mapping(),
        "aliases": list(target.get("aliases") or []),
    }


def _natural_parts(value: Any) -> tuple[tuple[int, Any], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", str(value or ""))
        if part
    )


def _source_logical_question_id(question: Mapping[str, Any]) -> str:
    source = question.get("source")
    source = source if isinstance(source, Mapping) else {}
    projected = question.get("projected")
    projected = projected if isinstance(projected, Mapping) else {}
    for record in (source, question, projected):
        for field in (
            "originalQuestionId",
            "original_question_id",
            "sourceQuestionId",
            "source_question_id",
            "sourceQuestionKey",
        ):
            value = str(record.get(field) or "").strip()
            if value:
                return value
    return ""


def _progress_sort_key(question: Mapping[str, Any]) -> tuple[Any, ...]:
    logical_id = _source_logical_question_id(question)
    return (
        _natural_parts(question.get("listGroupId")),
        0 if logical_id else 1,
        _natural_parts(logical_id),
        _natural_parts(question.get("sourceStem")),
        int(question.get("sourceIndex") or 0),
        _natural_parts(question.get("questionLabel")),
        str(question.get("id") or ""),
    )


class QualificationWorkflow:
    def __init__(
        self,
        repo_root: Path,
        inventory: Any,
        *,
        work_versions: QuestionWorkVersionStore | None = None,
    ):
        self.repo_root = repo_root.resolve()
        self.inventory = inventory
        self.catalog_store = WorkflowCatalog(self.repo_root)
        self.work_versions = work_versions or QuestionWorkVersionStore(self.repo_root)

    def catalog(self, qualification: str = "") -> dict[str, Any]:
        loaded = self.catalog_store.load()
        system = dict(loaded["system"])
        shared_documents = _ordered_unique(
            [system["trunkDocument"], *system.get("defaultDocuments", [])]
        )
        human_documents = list(system.get("humanDocuments") or [])
        qualification_documents = self._qualification_documents(qualification)
        stages: list[dict[str, Any]] = []
        for definition in loaded["stages"]:
            stage = dict(definition)
            stage_documents = list(stage.pop("documents", []))
            patterns = list(stage.get("qualificationDocumentPatterns") or [])
            stage_qualification_documents = (
                [
                    path
                    for path in qualification_documents
                    if any(
                        fnmatch.fnmatch(Path(path).name, pattern)
                        for pattern in patterns
                    )
                ]
                if patterns
                else qualification_documents
            )
            stage["canonicalDocs"] = _ordered_unique(
                [
                    *stage_documents,
                    *(
                        stage_qualification_documents
                        if stage.get("kind") == "human"
                        else []
                    ),
                    *(human_documents if stage.get("kind") == "human" else []),
                    *shared_documents,
                ]
            )
            if stage.get("policyVersion") is not None:
                stage["policyFingerprint"] = policy_fingerprint(
                    self.repo_root,
                    str(loaded["catalogPath"]),
                    stage,
                    canonical_docs=stage["canonicalDocs"],
                )
            stages.append(stage)
        effective_hash = hashlib.sha256(
            json.dumps(
                [
                    loaded["catalogHash"],
                    qualification_documents,
                    [
                        [stage["id"], stage.get("policyFingerprint")]
                        for stage in stages
                        if stage.get("policyVersion") is not None
                    ],
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return {
            "qualification": qualification or None,
            "generatedAt": _now_iso(),
            "system": system,
            "sessionGroups": [
                dict(value) for value in loaded.get("sessionGroups") or []
            ],
            "catalogHash": effective_hash,
            "catalogPath": loaded["catalogPath"],
            "catalogWarning": str(loaded.get("catalogWarning") or ""),
            "restartRequired": bool(loaded.get("restartRequired")),
            "stages": stages,
        }

    def versioned_policies(self, qualification: str) -> dict[str, dict[str, Any]]:
        return {
            str(stage["id"]): dict(stage)
            for stage in self.catalog(qualification)["stages"]
            if stage.get("policyVersion") is not None
            and stage.get("supportsGroupScope")
        }

    def _selected_or_expected_patch_path(
        self, source_path: str, stage: Mapping[str, Any]
    ) -> str:
        expected = Path(_expected_patch_path(source_path, stage))
        source = Path(source_path)
        patch_root = self.repo_root / expected.parent
        patch_tag = str(stage["patchSuffix"])
        selected = select_latest_patch_files(
            sorted(patch_root.glob("*.json")), patch_tag
        )
        source_stems = {source.stem, f"{source.stem}_merged"}
        preferred = [
            path
            for path in selected
            if source_stem_from_patch_filename(path.name, patch_tag)
            in source_stems
        ]
        if preferred:
            return str(sorted(preferred)[-1].relative_to(self.repo_root))
        return str(expected)

    def _stage_version_status(
        self,
        question: Mapping[str, Any],
        stage: Mapping[str, Any],
        selected_update_target_ids: Iterable[str] | None = None,
    ) -> str:
        policy = dict(stage)
        if selected_update_target_ids is not None:
            policy["selectedUpdateTargetIds"] = list(selected_update_target_ids)
        status = self.work_versions.status_for(question, [policy])
        stages = status.get("stages") or []
        return str(stages[0].get("status") or "unrecorded") if stages else "unrecorded"

    @staticmethod
    def _require_runnable_catalog(catalog: Mapping[str, Any]) -> None:
        if catalog.get("restartRequired"):
            detail = str(catalog.get("catalogWarning") or "").strip()
            raise ValueError(
                detail
                or "workflow設定の更新を反映するため、問題整備システムを再起動してください。"
            )

    @staticmethod
    def _artifact_blockers_for_stage(
        groups: Iterable[Mapping[str, Any]],
        stage: Mapping[str, Any],
    ) -> list[Mapping[str, Any]]:
        stage_id = str(stage.get("id") or "")
        patch_dirs = {str(stage.get("patchDir") or "")}
        if stage_id == "law_audit":
            patch_dirs.update(
                {
                    "18_law_context_prepared",
                    "21_explanationText_added",
                    "23_correctChoiceText_fixed",
                }
            )
        return [
            blocker
            for group in groups
            for blocker in group.get("artifactResolutionBlockers") or []
            if isinstance(blocker, Mapping)
            and (
                stage_id == "delivery"
                or str(blocker.get("patchDir") or "") in patch_dirs
            )
        ]

    def overview(self, qualification: str) -> dict[str, Any]:
        catalog = self.catalog(qualification)
        groups, questions = self._qualification_data(qualification)
        versioned_definitions = [
            stage
            for stage in catalog["stages"]
            if stage.get("policyVersion") is not None
            and stage.get("supportsGroupScope")
        ]
        version_statuses = [
            (
                question,
                self.work_versions.status_for(
                    question,
                    [
                        definition
                        for definition in versioned_definitions
                        if (
                            str(definition.get("id") or "") != "originalize"
                            or _originalization_applicable(question)
                        )
                    ],
                ),
            )
            for question in questions
        ]
        stages = self._build_stages(
            qualification,
            groups,
            questions,
            catalog["stages"],
            version_statuses=version_statuses,
        )
        next_stage = next(
            (stage for stage in stages if stage["status"] != "ready"), None
        )
        ready_count = sum(stage["status"] == "ready" for stage in stages)
        issue_counts = Counter(
            str(code)
            for question in questions
            for code in question.get("issueCodes") or []
        )
        category_required = any(
            stage.get("id") == "category_setup" and stage.get("status") != "ready"
            for stage in stages
        )
        progress = self._maintenance_progress(
            version_statuses,
            force_all_required=category_required,
        )
        required_stage_ids = _required_maintenance_stage_ids(stages)
        group_summaries = [
            self._group_summary(
                group,
                self._maintenance_progress(
                    [
                        item
                        for item in version_statuses
                        if str(item[0].get("listGroupId") or "")
                        == str(group.get("listGroupId") or "")
                    ],
                    force_all_required=category_required,
                ),
            )
            for group in groups
        ]
        overall_status = (
            "ready"
            if next_stage is None
            else "attention"
            if next_stage["status"] == "attention"
            else "in_progress"
        )
        return {
            "qualification": qualification,
            "generatedAt": _now_iso(),
            "system": catalog["system"],
            "catalogHash": catalog["catalogHash"],
            "catalogWarning": catalog.get("catalogWarning") or "",
            "restartRequired": bool(catalog.get("restartRequired")),
            "overallStatus": overall_status,
            "nextStageId": next_stage["id"] if next_stage else None,
            "summary": {
                "groupCount": len(groups),
                "questionCount": len(questions),
                "lawQuestionCount": sum(
                    question.get("isLawRelated") is True for question in questions
                ),
                "issueQuestionCount": sum(bool(question.get("issues")) for question in questions),
                "holdQuestionCount": sum(
                    "law_hold" in set(question.get("issueCodes") or [])
                    or str(question.get("reviewStatus") or "") == "hold"
                    for question in questions
                ),
                "readyStageCount": ready_count,
                "stageCount": len(stages),
                "maintenanceProgress": progress,
                "requiredMaintenance": {
                    "stageIds": required_stage_ids,
                    "mode": "outdated",
                },
                "issueCounts": dict(sorted(issue_counts.items())),
            },
            "stages": stages,
            "groups": group_summaries,
        }

    def plan(
        self,
        qualification: str,
        stage_id: str,
        mode: str = "remaining",
        *,
        list_group_id: str | None = None,
        list_group_ids: Iterable[str] | None = None,
        update_target_ids: Iterable[str] | None = None,
        question_range: Mapping[str, Any] | None = None,
        question_ids: Iterable[str] | None = None,
        allow_category_pending: bool = False,
        _catalog: Mapping[str, Any] | None = None,
        _qualification_data: tuple[
            list[Mapping[str, Any]],
            list[Mapping[str, Any]],
        ]
        | None = None,
    ) -> dict[str, Any]:
        if mode not in RUN_MODES:
            raise ValueError(f"対象範囲が不正です: {mode}")
        catalog = _catalog or self.catalog(qualification)
        self._require_runnable_catalog(catalog)
        definition = next(
            (stage for stage in catalog["stages"] if stage["id"] == stage_id), None
        )
        if definition is None:
            raise ValueError(f"対象工程がありません: {stage_id}")
        normalized_question_range = _question_range(question_range)
        if normalized_question_range is not None and question_ids is not None:
            raise ValueError("questionIdsとquestionRangeは同時に指定できません。")
        selected_update_targets = _select_update_targets(
            definition, update_target_ids
        )
        selected_update_target_ids = [
            str(value["selectionId"]) for value in selected_update_targets
        ]
        selected_fields = _ordered_unique(
            field
            for target in selected_update_targets
            for field in target.get("fields") or []
        )
        read_fields = _ordered_unique(
            field
            for target in selected_update_targets
            for field in target.get("readFields") or []
        )
        if stage_id == "source":
            raise ValueError("00_sourceは取得工程の正本であり、この画面から再生成しません。")
        if (
            mode == "outdated"
            and definition.get("policyVersion") is None
            and stage_id != "category_setup"
        ):
            raise ValueError("洗い替え必要・未整備だけの選択は工程版対象でのみ使えます。")

        selected_group_ids, scope_provided = _group_scope(
            list_group_id, list_group_ids
        )
        requested_group_ids = list(selected_group_ids)
        qualification_scope_stage = stage_id in {"setup", "category_setup"}
        if qualification_scope_stage and scope_provided:
            # 親の年度選択は後続の問題工程へ残すが、この前提工程自体は
            # 資格全体に対して一度だけ実行する。
            selected_group_ids = []
            scope_provided = False
            if mode == "group_refresh":
                mode = "refresh"
        if not definition.get("supportsGroupScope") and scope_provided:
            raise ValueError(f"{definition['label']}は年度ではなく資格単位で整備します。")
        if normalized_question_range and (
            not definition.get("supportsGroupScope")
            or definition.get("kind") != "human"
        ):
            raise ValueError(f"{definition['label']}は問題番号範囲を指定できません。")
        if question_ids is not None and (
            not definition.get("supportsGroupScope")
            or definition.get("kind") != "human"
        ):
            raise ValueError(f"{definition['label']}はquestionIdsを指定できません。")
        if mode == "group_refresh" and not selected_group_ids:
            raise ValueError("対象年度を一つ以上選択してください。")

        if _qualification_data is None:
            groups, questions = self._qualification_data(qualification)
        else:
            groups = list(_qualification_data[0])
            questions = list(_qualification_data[1])
        validation_group_ids = (
            requested_group_ids
            if qualification_scope_stage
            else selected_group_ids
        )
        if validation_group_ids:
            available_group_ids = {
                str(group.get("listGroupId") or "") for group in groups
            }
            unknown = [
                group_id
                for group_id in validation_group_ids
                if group_id not in available_group_ids
            ]
            if unknown:
                raise ValueError("対象年度がありません: " + ", ".join(unknown))
        if selected_group_ids:
            selected_set = set(selected_group_ids)
            groups = [
                group
                for group in groups
                if str(group.get("listGroupId") or "") in selected_set
            ]
            questions = [
                question
                for question in questions
                if str(question.get("listGroupId") or "") in selected_set
            ]
        if stage_id == "originalize":
            questions = [
                question
                for question in questions
                if _originalization_applicable(question)
            ]
        questions, normalized_question_ids = _filter_question_ids(
            questions, question_ids
        )
        questions = _filter_question_range(questions, normalized_question_range)
        artifact_blockers = self._artifact_blockers_for_stage(
            groups,
            definition,
        )
        if artifact_blockers:
            raise ValueError(
                "source recordへ対応できないartifactがあるため、"
                "対象工程を開始できません: "
                + " / ".join(
                    str(blocker.get("message") or blocker.get("path") or "")
                    for blocker in artifact_blockers[:3]
                )
            )
        if stage_id == "law_audit":
            identity_blockers = [
                blocker
                for group in groups
                for blocker in group.get("identityBlockers") or []
                if isinstance(blocker, Mapping)
            ]
            if identity_blockers:
                raise ValueError(
                    "現行法監査のsource identityに重複又は欠損があるため、"
                    "安全に開始できません: "
                    + " / ".join(
                        str(blocker.get("message") or blocker.get("code") or "")
                        for blocker in identity_blockers[:3]
                    )
                )
        target_questions: list[Mapping[str, Any]] = []
        output_files: list[str] = []
        target_group_ids: list[str] = []

        if stage_id == "setup":
            policy_dir = Path("prompt") / "qualification_docs" / qualification
            policy_exists = (self.repo_root / policy_dir).is_dir()
            if mode == "attention":
                target_questions = questions if not policy_exists else []
            elif mode in {"needed", "remaining"}:
                target_questions = questions if not policy_exists else []
            else:
                target_questions = questions
            source_files = _unique(
                str(Path(str(question.get("paths", {}).get("source") or "")).parent)
                for question in target_questions
                if question.get("paths", {}).get("source")
            )
            output_files = [
                str(policy_dir / name)
                for name in (
                    "README.md",
                    "01_exam_profile.md",
                    "02_explanation_strategy.md",
                )
            ] if target_questions else []
        elif stage_id == "category_setup":
            category = self._category_state(qualification)
            if mode == "outdated":
                should_run = not category["ready"]
            else:
                should_run = mode == "refresh" or not category["ready"]
            if mode == "attention":
                should_run = bool(category["error"])
            target_questions = questions if should_run else []
            source_files = _unique(
                str(question.get("paths", {}).get("source") or "")
                for question in questions
            ) if should_run else []
            output_files = [
                category["path"],
                str(
                    Path("prompt")
                    / "qualification_docs"
                    / qualification
                    / "03_category_preparation.md"
                ),
            ] if should_run else []
        elif stage_id == "law_audit":
            applicable = [
                question
                for question in questions
                if question.get("isLawRelated") is not False
                or set(question.get("issueCodes") or []) & LAW_AUDIT_ISSUES
            ]
            if mode in {"refresh", "group_refresh"}:
                # 全問題再整備では既存の法令フラグ自体も再判定する。
                target_questions = questions
            elif mode in {"needed", "outdated"}:
                target_questions = [
                    question
                    for question in questions
                    if self._stage_version_status(
                        question, definition, selected_update_target_ids
                    )
                    != "current"
                    or set(question.get("issueCodes") or []) & LAW_AUDIT_ISSUES
                    or (
                        question.get("isLawRelated") is not False
                        and not (question.get("projected") or {}).get(
                            "lawRevisionFacts"
                        )
                    )
                ]
            elif mode == "attention":
                target_questions = [
                    question
                    for question in applicable
                    if set(question.get("issueCodes") or []) & LAW_AUDIT_ISSUES
                ]
            else:
                target_questions = [
                    question
                    for question in applicable
                    if set(question.get("issueCodes") or []) & LAW_AUDIT_ISSUES
                    or (
                        question.get("isLawRelated") is not False
                        and not (question.get("projected") or {}).get(
                            "lawRevisionFacts"
                        )
                    )
                ]
            source_files = _unique(
                str(question.get("paths", {}).get("source") or "")
                for question in target_questions
            )
            output_files = _unique(
                self._law_audit_output_path(question) for question in target_questions
            )
        elif stage_id == "delivery":
            if mode == "group_refresh":
                target_group_ids = [str(group.get("listGroupId") or "") for group in groups]
            elif mode == "refresh":
                target_group_ids = [str(group.get("listGroupId") or "") for group in groups]
            else:
                target_group_ids = [
                    str(group.get("listGroupId") or "")
                    for group in groups
                    if not self._group_summary(group)["localReady"]
                ]
            source_files = [
                str(Path("output") / qualification / "questions_json" / group_id)
                for group_id in target_group_ids
            ]
        else:
            category_pending = False
            if stage_id == "question_set":
                category = self._category_state(qualification)
                category_pending = not category["ready"]
                if not category["ready"] and not allow_category_pending:
                    detail = category["error"] or "category.jsonが未作成です。"
                    raise ValueError(f"03c カテゴリ設計を先に完了してください: {detail}")
            patch_dir = str(definition["patchDir"])
            issue_fields = set(definition.get("issueFields") or [])
            if mode in {"refresh", "group_refresh"}:
                target_questions = questions
            elif mode == "needed":
                target_questions = (
                    list(questions)
                    if category_pending and allow_category_pending
                    else [
                        question
                        for question in questions
                        if not _has_patch(question, patch_dir)
                        or self._stage_version_status(
                            question, definition, selected_update_target_ids
                        )
                        != "current"
                        or _issue_count(question, issue_fields)
                    ]
                )
            elif mode == "outdated":
                target_questions = (
                    list(questions)
                    if category_pending and allow_category_pending
                    else [
                        question
                        for question in questions
                        if self._stage_version_status(
                            question, definition, selected_update_target_ids
                        )
                        != "current"
                    ]
                )
            elif mode == "attention":
                target_questions = [
                    question
                    for question in questions
                    if _issue_count(question, issue_fields)
                ]
            else:
                target_questions = [
                    question for question in questions if not _has_patch(question, patch_dir)
                ]
            source_files = _unique(
                str(question.get("paths", {}).get("source") or "")
                for question in target_questions
            )
            output_files = _unique(
                self._selected_or_expected_patch_path(
                    str(question.get("paths", {}).get("source") or ""), definition
                )
                for question in target_questions
                if question.get("paths", {}).get("source")
            )

        if not target_group_ids:
            target_group_ids = _unique(
                str(question.get("listGroupId") or self._group_id_from_source(question))
                for question in target_questions
            )
        ordered_target_questions = sorted(
            target_questions,
            key=_progress_sort_key,
        )
        target_question_keys = _unique(
            self._question_key(question) for question in ordered_target_questions
        )
        target_record_alias_groups = [
            _target_record_alias_group(question)
            for question in ordered_target_questions
        ]
        all_progress_targets = [
            {
                **_progress_target(question),
                "displayOrder": index,
            }
            for index, question in enumerate(
                ordered_target_questions,
                start=1,
            )
        ]
        target_record_bindings = [
            _target_record_binding(target)
            for target in all_progress_targets
        ]
        progress_targets = (
            []
            if str(definition.get("scope") or "") == "qualification"
            else all_progress_targets
        )
        if stage_id == "law_audit":
            incomplete_bindings = [
                binding
                for binding in target_record_bindings
                if not SourceIdentityBinding.from_mapping(binding).is_complete()
            ]
            if incomplete_bindings:
                raise ValueError(
                    "現行法監査の対象問題にsource由来のreviewQuestionId又は"
                    "sourceQuestionKey又はsourceRecordRefがなく、"
                    "安全に開始できません。"
                )
            identity_bindings = [
                SourceIdentityBinding.from_mapping(binding)
                for binding in target_record_bindings
            ]
            if len(identity_bindings) != len(set(identity_bindings)):
                raise ValueError(
                    "現行法監査のsourceQuestionKey/reviewQuestionId/"
                    "sourceRecordRefの組が重複しているため、"
                    "安全に開始できません。"
                )
        if (
            str(definition["kind"]) == "human"
            and stage_id not in {"setup", "category_setup"}
            and any(not aliases for aliases in target_record_alias_groups)
        ):
            raise ValueError(
                "対象問題に一意IDがなく、安全なrecord書込範囲を作成できません。"
            )
        target_record_alias_groups = [
            aliases for aliases in target_record_alias_groups if aliases
        ]
        target_source_record_scopes: dict[str, list[list[str]]] = {}
        for question in ordered_target_questions:
            aliases = _target_record_alias_group(question)
            source_path = str(question.get("paths", {}).get("source") or "")
            if source_path and aliases:
                target_source_record_scopes.setdefault(source_path, []).append(
                    aliases
                )
        scope_label = (
            "・".join(selected_group_ids)
            if len(selected_group_ids) <= 3
            else f"{selected_group_ids[0]}ほか{len(selected_group_ids) - 1}件"
        )
        if selected_group_ids:
            mode_label = {
                "group_refresh": f"{scope_label}の全問題を再整備",
                "refresh": f"{scope_label}の全問題を再整備",
                "remaining": f"{scope_label}の未作業のみ",
                "attention": f"{scope_label}の要確認のみ",
                "outdated": f"{scope_label}の洗い替え必要・未整備のみ",
                "needed": f"{scope_label}の整備が必要な問題だけ",
            }[mode]
        else:
            mode_label = RUN_MODES[mode]
        if normalized_question_range:
            mode_label += (
                f"（各選択範囲の第{normalized_question_range['start']}問〜"
                f"第{normalized_question_range['end']}問）"
            )
        policy_versions = (
            {
                stage_id: normalize_policy_version(definition["policyVersion"])
            }
            if definition.get("policyVersion") is not None
            and definition.get("supportsGroupScope")
            else {}
        )
        policy_fingerprints = (
            {stage_id: str(definition.get("policyFingerprint") or "")}
            if definition.get("policyVersion") is not None
            and definition.get("supportsGroupScope")
            else {}
        )
        policy_targets = (
            {stage_id: target_question_keys}
            if definition.get("policyVersion") is not None
            and definition.get("supportsGroupScope")
            else {}
        )
        return {
            "qualification": qualification,
            "stageId": stage_id,
            "stageCode": str(definition["code"]),
            "stageLabel": str(definition["label"]),
            "purpose": str(definition["purpose"]),
            "kind": str(definition["kind"]),
            "sessionGroup": str(definition.get("sessionGroup") or ""),
            "sessionLabel": str(definition.get("sessionLabel") or ""),
            "mode": mode,
            "modeLabel": mode_label,
            "targetCount": (
                len(target_group_ids)
                if stage_id == "delivery"
                else int(bool(target_questions))
                if stage_id == "category_setup"
                else len(target_questions)
            ),
            "targetQuestionKeys": target_question_keys,
            "progressTargets": progress_targets,
            "targetRecordBindings": target_record_bindings,
            "targetRecordAliasGroups": target_record_alias_groups,
            "targetSourceRecordScopes": target_source_record_scopes,
            "targetGroupIds": target_group_ids,
            "scopeListGroupId": (
                selected_group_ids[0] if len(selected_group_ids) == 1 else None
            ),
            "scopeListGroupIds": selected_group_ids,
            "questionRange": normalized_question_range,
            "questionIds": normalized_question_ids,
            "updateTargets": [dict(value) for value in definition.get("updateTargets") or []],
            "selectedUpdateTargets": selected_update_targets,
            "selectedUpdateTargetIds": selected_update_target_ids,
            "selectedFieldsByStage": (
                {stage_id: selected_fields} if selected_update_targets else {}
            ),
            "readFieldsByStage": (
                {stage_id: read_fields} if selected_update_targets else {}
            ),
            "sourceFiles": source_files,
            "outputFiles": output_files,
            "canonicalDocs": list(definition.get("canonicalDocs") or []),
            "catalogHash": catalog["catalogHash"],
            "policyVersions": policy_versions,
            "policyFingerprints": policy_fingerprints,
            "policyTargets": policy_targets,
            "force": stage_id == "delivery" and mode in {"refresh", "group_refresh"},
            "allQuestionGate": bool(
                stage_id == "law_audit" and mode in {"refresh", "group_refresh"}
            ),
        }

    def plan_many(
        self,
        qualification: str,
        stage_ids: Iterable[str],
        mode: str = "remaining",
        *,
        list_group_id: str | None = None,
        list_group_ids: Iterable[str] | None = None,
        update_target_ids: Iterable[str] | None = None,
        question_range: Mapping[str, Any] | None = None,
        question_ids: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        requested = _ordered_unique(str(stage_id) for stage_id in stage_ids)
        normalized_group_ids, scope_provided = _group_scope(
            list_group_id,
            list_group_ids,
        )
        scoped_group_ids = normalized_group_ids if scope_provided else None
        if not requested:
            raise ValueError("工程を一つ以上選択してください。")
        catalog = self.catalog(qualification)
        self._require_runnable_catalog(catalog)
        definitions = {str(stage["id"]): stage for stage in catalog["stages"]}
        unknown = [stage_id for stage_id in requested if stage_id not in definitions]
        if unknown:
            raise ValueError("対象工程がありません: " + ", ".join(unknown))
        ordered = [
            str(stage["id"])
            for stage in catalog["stages"]
            if str(stage["id"]) in requested
        ]
        normalized_question_range = _question_range(question_range)
        if normalized_question_range is not None and question_ids is not None:
            raise ValueError("questionIdsとquestionRangeは同時に指定できません。")
        if normalized_question_range and not any(
            definitions[stage_id].get("supportsGroupScope")
            and definitions[stage_id].get("kind") == "human"
            for stage_id in ordered
        ):
            raise ValueError("選択工程は問題番号範囲を指定できません。")
        requested_update_target_ids = (
            _ordered_unique(str(value).strip() for value in update_target_ids)
            if update_target_ids is not None
            else None
        )
        available_update_target_ids = {
            str(target.get("selectionId") or "")
            for stage_id in ordered
            for target in definitions[stage_id].get("updateTargets") or []
        }
        if requested_update_target_ids is not None:
            unknown_targets = [
                value
                for value in requested_update_target_ids
                if value not in available_update_target_ids
            ]
            if unknown_targets:
                raise ValueError(
                    "更新項目がありません: " + ", ".join(unknown_targets)
                )
            missing_target_stages = [
                definitions[stage_id]["label"]
                for stage_id in ordered
                if definitions[stage_id].get("updateTargets")
                and not any(
                    value.startswith(f"{stage_id}.")
                    for value in requested_update_target_ids
                )
            ]
            if missing_target_stages:
                raise ValueError(
                    "更新項目を一つ以上選択してください: "
                    + ", ".join(missing_target_stages)
                )
        if len(ordered) == 1:
            plan = self.plan(
                qualification,
                ordered[0],
                mode,
                list_group_ids=scoped_group_ids,
                update_target_ids=requested_update_target_ids,
                question_range=normalized_question_range,
                question_ids=question_ids,
                _catalog=catalog,
            )
            plan["stageIds"] = ordered
            plan["stageCount"] = 1
            plan["workItemCount"] = plan["targetCount"]
            plan["stagePlans"] = [dict(plan)]
            return plan

        invalid = [
            stage_id
            for stage_id in ordered
            if not definitions[stage_id].get("batchSelectable")
            and stage_id not in {"setup", "category_setup"}
        ]
        if invalid:
            raise ValueError(
                "一問ずつまとめて実行できない工程が含まれています: "
                + ", ".join(invalid)
            )
        qualification_data = self._qualification_data(qualification)
        stage_plans = [
            self.plan(
                qualification,
                stage_id,
                (
                    "refresh"
                    if mode == "group_refresh"
                    and stage_id in {"setup", "category_setup"}
                    else mode
                ),
                list_group_ids=(
                    scoped_group_ids
                    if definitions[stage_id].get("supportsGroupScope")
                    and definitions[stage_id].get("kind") == "human"
                    else None
                ),
                update_target_ids=(
                    None
                    if requested_update_target_ids is None
                    else [
                        value
                        for value in requested_update_target_ids
                        if value.startswith(f"{stage_id}.")
                    ]
                ),
                question_range=(
                    normalized_question_range
                    if definitions[stage_id].get("supportsGroupScope")
                    else None
                ),
                question_ids=(
                    question_ids
                    if definitions[stage_id].get("supportsGroupScope")
                    and definitions[stage_id].get("kind") == "human"
                    else None
                ),
                allow_category_pending=(
                    stage_id == "question_set" and "category_setup" in ordered
                ),
                _catalog=catalog,
                _qualification_data=qualification_data,
            )
            for stage_id in ordered
        ]
        aggregate_plans = stage_plans
        group_scoped_plans = [
            plan
            for plan in stage_plans
            if definitions[str(plan["stageId"])].get("supportsGroupScope")
        ]
        if group_scoped_plans:
            # 資格全体の前提工程は子runのphase表示だけで追跡する。親runの
            # 問題進捗へ重複加算せず、年度指定時の最終同期も全年度へ広げない。
            aggregate_plans = group_scoped_plans
        target_question_keys = _unique(
            key
            for plan in aggregate_plans
            for key in plan.get("targetQuestionKeys") or []
        )
        return {
            "qualification": qualification,
            "stageId": "multi",
            "stageIds": ordered,
            "stageCount": len(ordered),
            "stageCode": " → ".join(str(plan["stageCode"]) for plan in stage_plans),
            "stageLabel": "複数工程",
            "purpose": "選択した工程を一問単位で順番に完了する",
            "kind": "human",
            "mode": mode,
            "modeLabel": (
                group_scoped_plans[0]
                if group_scoped_plans
                else stage_plans[0]
            )["modeLabel"],
            "targetCount": len(target_question_keys),
            "workItemCount": sum(
                int(plan["targetCount"]) for plan in aggregate_plans
            ),
            "targetQuestionKeys": target_question_keys,
            "progressTargets": list(
                {
                    str(target.get("id") or target.get("questionKey")): dict(target)
                    for plan in aggregate_plans
                    for target in plan.get("progressTargets") or []
                    if target.get("id") or target.get("questionKey")
                }.values()
            ),
            "targetRecordBindings": list(
                {
                    str(binding.get("uiQuestionId") or ""): dict(binding)
                    for plan in aggregate_plans
                    for binding in plan.get("targetRecordBindings") or []
                    if binding.get("uiQuestionId")
                }.values()
            ),
            "targetRecordAliasGroups": [
                list(aliases)
                for aliases in dict.fromkeys(
                    tuple(aliases)
                    for plan in aggregate_plans
                    for aliases in plan.get("targetRecordAliasGroups") or []
                    if aliases
                )
            ],
            "targetSourceRecordScopes": {
                path: [
                    list(group)
                    for group in dict.fromkeys(
                        tuple(group)
                        for plan in aggregate_plans
                        for group in (
                            plan.get("targetSourceRecordScopes") or {}
                        ).get(path, [])
                        if group
                    )
                ]
                for path in _unique(
                    path
                    for plan in aggregate_plans
                    for path in (
                        plan.get("targetSourceRecordScopes") or {}
                    )
                )
            },
            "targetGroupIds": _unique(
                group_id
                for plan in aggregate_plans
                for group_id in plan.get("targetGroupIds") or []
            ),
            "scopeListGroupId": (
                scoped_group_ids[0]
                if scoped_group_ids is not None and len(scoped_group_ids) == 1
                else None
            ),
            "scopeListGroupIds": list(scoped_group_ids or []),
            "questionRange": normalized_question_range,
            "questionIds": (
                _ordered_unique(str(value).strip() for value in question_ids)
                if question_ids is not None
                else None
            ),
            "updateTargets": [
                dict(target)
                for stage_id in ordered
                for target in definitions[stage_id].get("updateTargets") or []
            ],
            "selectedUpdateTargets": [
                dict(target)
                for plan in stage_plans
                for target in plan.get("selectedUpdateTargets") or []
            ],
            "selectedUpdateTargetIds": _ordered_unique(
                value
                for plan in stage_plans
                for value in plan.get("selectedUpdateTargetIds") or []
            ),
            "selectedFieldsByStage": {
                str(stage_id): list(fields)
                for plan in stage_plans
                for stage_id, fields in (
                    plan.get("selectedFieldsByStage") or {}
                ).items()
            },
            "readFieldsByStage": {
                str(stage_id): list(fields)
                for plan in stage_plans
                for stage_id, fields in (plan.get("readFieldsByStage") or {}).items()
            },
            "sourceFiles": _unique(
                path
                for plan in stage_plans
                for path in plan.get("sourceFiles") or []
            ),
            "outputFiles": _unique(
                path
                for plan in stage_plans
                for path in plan.get("outputFiles") or []
            ),
            "canonicalDocs": _ordered_unique(
                path
                for plan in stage_plans
                for path in plan.get("canonicalDocs") or []
            ),
            "catalogHash": catalog["catalogHash"],
            "policyVersions": {
                str(stage_id): normalize_policy_version(version)
                for plan in stage_plans
                for stage_id, version in (plan.get("policyVersions") or {}).items()
            },
            "policyFingerprints": {
                str(stage_id): str(fingerprint)
                for plan in stage_plans
                for stage_id, fingerprint in (
                    plan.get("policyFingerprints") or {}
                ).items()
            },
            "policyTargets": {
                str(stage_id): list(keys)
                for plan in stage_plans
                for stage_id, keys in (plan.get("policyTargets") or {}).items()
            },
            "force": False,
            "stagePlans": stage_plans,
        }

    def prompt(
        self,
        qualification: str,
        stage_id: str,
        mode: str = "remaining",
        *,
        list_group_id: str | None = None,
        list_group_ids: Iterable[str] | None = None,
        update_target_ids: Iterable[str] | None = None,
        question_range: Mapping[str, Any] | None = None,
        question_ids: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        return self.prompt_many(
            qualification,
            [stage_id],
            mode,
            list_group_id=list_group_id,
            list_group_ids=list_group_ids,
            update_target_ids=update_target_ids,
            question_range=question_range,
            question_ids=question_ids,
        )

    def prompt_many(
        self,
        qualification: str,
        stage_ids: Iterable[str],
        mode: str = "remaining",
        *,
        list_group_id: str | None = None,
        list_group_ids: Iterable[str] | None = None,
        update_target_ids: Iterable[str] | None = None,
        question_range: Mapping[str, Any] | None = None,
        question_ids: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        plan = self.plan_many(
            qualification,
            stage_ids,
            mode,
            list_group_id=list_group_id,
            list_group_ids=list_group_ids,
            update_target_ids=update_target_ids,
            question_range=question_range,
            question_ids=question_ids,
        )
        if plan["kind"] != "human":
            raise ValueError("この工程はCodex依頼ではなく既存の実行導線を使います。")
        if not plan["targetCount"]:
            raise ValueError("選択した範囲に対象はありません。")

        def absolute(path: str) -> str:
            candidate = (self.repo_root / path).resolve()
            if not candidate.is_relative_to(self.repo_root):
                raise ValueError(f"repo外のpathです: {path}")
            return str(candidate)

        canonical = [absolute(path) for path in plan["canonicalDocs"]]
        source_files = [absolute(path) for path in plan["sourceFiles"]]
        output_files = [absolute(path) for path in plan["outputFiles"]]

        selected_stage_ids = list(plan.get("stageIds") or [plan["stageId"]])
        stage_plans = list(plan.get("stagePlans") or [plan])
        selected_fields_by_stage = {
            str(stage_id): {
                str(field)
                for field in fields
                if field
            }
            for stage_id, fields in (
                plan.get("selectedFieldsByStage") or {}
            ).items()
        }
        reference_only_fields_by_stage = {
            str(stage_id): [
                str(field)
                for field in fields
                if field
                and str(field)
                not in selected_fields_by_stage.get(str(stage_id), set())
            ]
            for stage_id, fields in (
                plan.get("readFieldsByStage") or {}
            ).items()
        }
        stage_summary = " / ".join(
            f"{item['stageCode']} {item['stageLabel']}（{item['targetCount']}件）"
            for item in stage_plans
        )
        target_label = (
            f"{plan['targetCount']}問すべて"
            if mode in {"refresh", "group_refresh"}
            else f"{plan['targetCount']}問"
        )
        lines = [
            (
                "# 選択年度・フォルダの問題整備"
                if plan.get("scopeListGroupIds")
                else "# 資格単位の問題整備"
            ),
            "",
            f"- 工程: `{stage_summary}`",
            f"- 範囲: `{plan['modeLabel']}`",
            *(
                [
                    "- 対象listGroupId: `"
                    + "`, `".join(plan.get("scopeListGroupIds") or [])
                    + "`"
                ]
                if plan.get("scopeListGroupIds")
                else []
            ),
            *(
                [
                    f"- 問題番号: `各listGroupIdの第{plan['questionRange']['start']}問〜"
                    f"第{plan['questionRange']['end']}問`"
                ]
                if plan.get("questionRange")
                else []
            ),
            *(
                [
                    "- 更新項目: `"
                    + " / ".join(
                        str(target.get("label") or target.get("selectionId") or "")
                        for target in plan.get("selectedUpdateTargets") or []
                    )
                    + "`",
                    "- 更新許可field: `"
                    + " / ".join(
                        f"{stage_id}=" + ",".join(fields)
                        for stage_id, fields in (
                            plan.get("selectedFieldsByStage") or {}
                        ).items()
                    )
                    + "`",
                ]
                if plan.get("selectedUpdateTargets")
                else []
            ),
            f"- 対象問題: `{target_label}`",
            f"- 工程判定: `延べ{plan.get('workItemCount', plan['targetCount'])}件`",
            *(
                [
                    "- 作業バージョン: `"
                    + " / ".join(
                        f"{stage_id}=v{version}"
                        for stage_id, version in (
                            plan.get("policyVersions") or {}
                        ).items()
                    )
                    + "`"
                ]
                if plan.get("policyVersions")
                else []
            ),
            "",
            "## 正本",
            "",
            *(f"- `{path}`" for path in _unique(canonical)),
            "",
            "## 対象source",
            "",
            *(f"- `{path}`" for path in source_files),
            "",
            "## 更新先",
            "",
            *(f"- `{path}`" for path in output_files),
            "",
            "## 作業",
            "",
            (
                f"上記正本に従い、qualification=`{qualification}`の選択工程を"
                "対象問題ごとに一問ずつ実施する。"
            ),
            (
                "一問を読み、その問題について選択工程を上記順序で完了してから次の問題へ進む。"
                if len(selected_stage_ids) > 1
                else "対象を一問ずつ読み、判断とpatch更新を完了してから次の問題へ進む。"
            ),
            "`未作業のみ`又は`要確認のみ`では、各工程の対象に該当する問題だけを更新する。",
            *(
                [
                    "更新許可fieldだけをset又はunsetする。"
                    "参照用fieldでも更新許可fieldに含まれるfieldは変更できる。"
                    "更新許可fieldに含まれない参照用fieldと"
                    "選択外fieldは変更しない。",
                    *(
                        [
                            "更新不可の参照用field: `"
                            + " / ".join(
                                f"{stage_id}=" + ",".join(fields)
                                for stage_id, fields in (
                                    reference_only_fields_by_stage.items()
                                )
                                if fields
                            )
                            + "`"
                        ]
                        if any(reference_only_fields_by_stage.values())
                        else []
                    ),
                ]
                if plan.get("selectedUpdateTargets")
                else []
            ),
            "同じ入力でも判断又は出力が変わり得る正本変更は、該当工程だけを+1する。",
            *(
                law_audit_classification_safety_contract(
                    self.repo_root,
                    qualification,
                )
                .rstrip()
                .splitlines()
                if "law_audit" in selected_stage_ids
                else []
            ),
            *(
                ["各問題は問題文と全選択肢を結合した命題として読み、Codex組み込みweb検索でe-Gov又は所管官庁の一次情報を開き、一問一肢ずつ根拠を照合する。"]
                if "law_audit" in selected_stage_ids
                else []
            ),
            *(
                [
                    "法令関連と確定した各問題では、正誤を変更しない場合もlawRevisionFacts.current.correctChoiceTextを省略しない。patchでは各選択肢と同じ順序・件数の判定を保存し、トップレベルcorrectChoiceText及び解説先頭と一致させる。",
                    "law_audit_metadata_incomplete又はlaw_audit_verdict_mismatchが残る法令関連問題をno-opで完了しない。法改正差分又は適用条文を確認できない場合は推測で補完せずholdへ戻す。",
                    "hold以外の法令関連問題は、公開用explanationTextに検証済みlawReferencesと対応する具体的な法令名、条項又は別表を記載する。「正しい。燃焼器は……。この基準はガス事業法施行規則第202条に定められている。」のように、結論と内容を先に示し、法令名・条文を機械的に文頭の主語にしない。",
                    "suggestedQuestionDetailsByChoiceは0〜3件とし、法令関連でも件数合わせのために作らない。必要な場合だけ、検証済み根拠と基本解説に整合する内容を作る。",
                    "監査sidecarのexamTimeDecisionとcurrentLawDecisionは選択肢と同じ順序・件数の非空string配列とし、lawReferencesも選択肢と同じ件数の配列にする。",
                ]
                if "law_audit" in selected_stage_ids
                else []
            ),
            *(
                ["全問題再整備の現行法監査は、既存のisLawRelatedだけで対象を絞らず、各問題で法令該当性を再確認する。非該当なら確認結果を確定して次工程へ進む。"]
                if any(
                    item.get("allQuestionGate")
                    for item in stage_plans
                    if item.get("stageId") == "law_audit"
                )
                else []
            ),
            "既存の正本と共通workflowを優先し、資格固有の局所ルールを重複実装しない。",
            "対象外の変更と`00_source`、既存IDは変更しない。作業後は正本記載の検証を実行する。",
        ]
        return {
            "qualification": qualification,
            "stageId": plan["stageId"],
            "stageIds": selected_stage_ids,
            "mode": mode,
            "targetCount": plan["targetCount"],
            "workItemCount": plan.get("workItemCount", plan["targetCount"]),
            "questionRange": plan.get("questionRange"),
            "questionIds": list(plan.get("questionIds") or []),
            "selectedUpdateTargetIds": list(
                plan.get("selectedUpdateTargetIds") or []
            ),
            "selectedFieldsByStage": dict(plan.get("selectedFieldsByStage") or {}),
            "prompt": "\n".join(lines).strip() + "\n",
        }

    def category_ready(self, qualification: str) -> bool:
        return bool(self._category_state(qualification)["ready"])

    def _qualification_data(
        self, qualification: str
    ) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
        qualification_info = next(
            (
                item
                for item in self.inventory.inventory().get("qualifications") or []
                if item.get("id") == qualification
            ),
            None,
        )
        if qualification_info is None:
            raise FileNotFoundError(f"対象資格がありません: {qualification}")
        groups = [
            self.inventory.group(qualification, str(list_group_id))
            for list_group_id in qualification_info.get("listGroupIds") or []
        ]
        questions: list[Mapping[str, Any]] = []
        failed_paths = unresolved_failed_delta_paths(
            self.repo_root, qualification
        )
        for group in groups:
            group_id = str(group.get("listGroupId") or "")
            for raw in group.get("questions") or []:
                if raw.get("listGroupId"):
                    question = dict(raw)
                    question["failedRunChangedPaths"] = failed_paths
                    questions.append(question)
                else:
                    question = dict(raw)
                    question["listGroupId"] = group_id
                    question["failedRunChangedPaths"] = failed_paths
                    questions.append(question)
        return groups, questions

    def _qualification_documents(self, qualification: str) -> list[str]:
        if not qualification:
            return []
        directory = (
            self.repo_root / "prompt" / "qualification_docs" / qualification
        )
        if not directory.is_dir():
            return []
        return [
            str(path.relative_to(self.repo_root))
            for path in sorted(directory.rglob("*.md"))
            if path.is_file()
        ]

    def _category_state(self, qualification: str) -> dict[str, Any]:
        relative = Path("output") / qualification / "category" / "category.json"
        path = self.repo_root / relative
        if not path.is_file():
            return {"path": str(relative), "ready": False, "error": ""}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return {
                "path": str(relative),
                "ready": False,
                "error": f"category.jsonを読み込めません: {exc}",
            }
        if not isinstance(value, Mapping):
            return {
                "path": str(relative),
                "ready": False,
                "error": "category.jsonのルートはobjectである必要があります。",
            }
        folders = value.get("folders")
        question_sets = value.get("questionSets")
        if not isinstance(folders, list) or not folders:
            return {
                "path": str(relative),
                "ready": False,
                "error": "foldersが未定義又は空です。",
            }
        if not isinstance(question_sets, list) or not question_sets:
            return {
                "path": str(relative),
                "ready": False,
                "error": "questionSetsが未定義又は空です。",
            }
        folder_ids = [
            str(item.get("folderId") or "")
            for item in folders
            if isinstance(item, Mapping)
        ]
        question_set_ids = [
            str(item.get("questionSetId") or "")
            for item in question_sets
            if isinstance(item, Mapping)
        ]
        if (
            len(folder_ids) != len(folders)
            or not all(folder_ids)
            or len(folder_ids) != len(set(folder_ids))
        ):
            return {
                "path": str(relative),
                "ready": False,
                "error": "folders[].folderIdが欠損又は重複しています。",
            }
        if (
            len(question_set_ids) != len(question_sets)
            or not all(question_set_ids)
            or len(question_set_ids) != len(set(question_set_ids))
        ):
            return {
                "path": str(relative),
                "ready": False,
                "error": "questionSets[].questionSetIdが欠損又は重複しています。",
            }
        unknown_folders = [
            str(item.get("folderId") or "")
            for item in question_sets
            if not isinstance(item, Mapping)
            or str(item.get("folderId") or "") not in set(folder_ids)
        ]
        if unknown_folders:
            return {
                "path": str(relative),
                "ready": False,
                "error": "questionSets[].folderIdに未定義IDがあります。",
            }
        return {"path": str(relative), "ready": True, "error": ""}

    @staticmethod
    def _question_key(question: Mapping[str, Any]) -> str:
        for field in ("id", "sourceQuestionKey", "reviewKey"):
            value = str(question.get(field) or "")
            if value:
                return value
        projected = question.get("projected") or {}
        original_id = str(
            question.get("originalQuestionId")
            or question.get("original_question_id")
            or projected.get("originalQuestionId")
            or projected.get("original_question_id")
            or ""
        )
        source = str(question.get("paths", {}).get("source") or "")
        label = str(question.get("questionLabel") or "")
        return "#".join(part for part in (source, original_id, label) if part)

    @staticmethod
    def _group_id_from_source(question: Mapping[str, Any]) -> str:
        source = Path(str(question.get("paths", {}).get("source") or ""))
        return source.parent.parent.name if len(source.parents) >= 2 else ""

    def _law_audit_output_path(self, question: Mapping[str, Any]) -> str:
        existing = [
            str(path)
            for path in question.get("paths", {}).get("patches") or []
            if "/21_explanationText_added/" in str(path)
        ]
        if existing:
            return existing[-1]
        source = str(question.get("paths", {}).get("source") or "")
        if not Path(source).name:
            return ""
        return self._selected_or_expected_patch_path(
            source,
            {
                "patchDir": "21_explanationText_added",
                "patchSuffix": "explanationText_added",
            },
        )

    def _build_stages(
        self,
        qualification: str,
        groups: list[Mapping[str, Any]],
        questions: list[Mapping[str, Any]],
        definitions: list[Mapping[str, Any]],
        *,
        version_statuses: list[
            tuple[Mapping[str, Any], Mapping[str, Any]]
        ] | None = None,
    ) -> list[dict[str, Any]]:
        total = len(questions)
        coverage: dict[str, int] = {
            str(stage["id"]): sum(
                _has_patch(question, str(stage["patchDir"]))
                for question in questions
            )
            for stage in definitions
            if stage.get("patchDir")
        }
        versioned_definitions = [
            definition
            for definition in definitions
            if definition.get("policyVersion") is not None
            and definition.get("supportsGroupScope")
        ]
        version_items_by_stage: dict[
            str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]
        ] = {
            str(definition["id"]): [] for definition in versioned_definitions
        }
        if version_statuses is None:
            version_statuses = [
                (
                    question,
                    self.work_versions.status_for(question, versioned_definitions),
                )
                for question in questions
            ]
        for question, status in version_statuses:
            for item in status["stages"]:
                version_items_by_stage[str(item["id"])].append((question, item))
        stages: list[dict[str, Any]] = []
        for index, definition in enumerate(definitions):
            stage = dict(definition)
            stage_id = str(stage["id"])
            target_questions: list[Mapping[str, Any]] = []
            output_files: list[str] = []
            issue_count = 0

            if stage_id == "source":
                complete = total
                target_count = total
                status = "ready" if total else "not_started"
            elif stage_id == "setup":
                policy_dir = self.repo_root / "prompt" / "qualification_docs" / qualification
                complete = int(policy_dir.is_dir() and any(policy_dir.iterdir()))
                target_count = 1
                status = "ready" if complete else "not_started"
                if not complete:
                    output_files = [str(Path("prompt") / "qualification_docs" / qualification)]
            elif stage_id == "category_setup":
                category = self._category_state(qualification)
                complete = int(category["ready"])
                target_count = 1
                status = (
                    "ready"
                    if category["ready"]
                    else "attention"
                    if category["error"]
                    else "not_started"
                )
                issue_count = int(bool(category["error"]))
                if not category["ready"]:
                    output_files = [category["path"]]
            elif stage_id == "law_audit":
                law_context_ready = coverage.get("law_context", 0) == total and total > 0
                target_questions = [
                    question
                    for question in questions
                    if question.get("isLawRelated") is not False
                    or set(question.get("issueCodes") or []) & LAW_AUDIT_ISSUES
                ]
                target_count = len(target_questions)
                incomplete = [
                    question
                    for question in target_questions
                    if set(question.get("issueCodes") or []) & LAW_AUDIT_ISSUES
                    or (
                        question.get("isLawRelated") is not False
                        and not (question.get("projected") or {}).get(
                            "lawRevisionFacts"
                        )
                    )
                ]
                complete = target_count - len(incomplete)
                target_questions = incomplete
                issue_count = len(incomplete)
                if not law_context_ready:
                    status = "waiting"
                elif target_count == 0:
                    status = "ready"
                elif incomplete:
                    status = "not_started" if complete == 0 else "in_progress"
                else:
                    status = "ready"
                output_files = _unique(
                    path
                    for question in target_questions
                    for path in question.get("paths", {}).get("patches") or []
                    if "/21_explanationText_added/" in str(path)
                )
            elif stage_id == "delivery":
                target_groups = [
                    str(group.get("listGroupId") or "")
                    for group in groups
                    if not all(
                        all(
                            question.get("workflow", {}).get(name) == "match"
                            for name in ("merge", "convert", "upload")
                        )
                        for question in group.get("questions") or []
                    )
                ]
                target_count = len(groups)
                complete = len(groups) - len(target_groups)
                status = _status_from_coverage(
                    total=target_count,
                    complete=complete,
                    issue_count=0,
                    downstream_count=0,
                )
                stage["targetGroupIds"] = target_groups
            else:
                patch_dir = str(stage["patchDir"])
                automatic = bool(stage.get("automatic", True))
                tracked_questions = (
                    questions
                    if automatic
                    else [
                        question
                        for question in questions
                        if _has_patch(question, patch_dir)
                    ]
                )
                complete = sum(
                    _has_patch(question, patch_dir)
                    for question in tracked_questions
                )
                target_count = len(tracked_questions)
                target_questions = [
                    question
                    for question in tracked_questions
                    if not _has_patch(question, patch_dir)
                ]
                issue_count = sum(
                    _issue_count(question, set(stage.get("issueFields") or []))
                    for question in tracked_questions
                    if _has_patch(question, patch_dir)
                )
                downstream_count = max(
                    (
                        coverage.get(str(item["id"]), 0)
                        for item in definitions[index + 1 :]
                        if item.get("patchDir")
                    ),
                    default=0,
                )
                status = _status_from_coverage(
                    total=target_count,
                    complete=complete,
                    issue_count=issue_count,
                    downstream_count=downstream_count,
                )
                if not automatic and target_count == 0:
                    status = "ready"
                output_files = _unique(
                    self._selected_or_expected_patch_path(
                        str(question.get("paths", {}).get("source") or ""), stage
                    )
                    for question in target_questions
                    if question.get("paths", {}).get("source")
                )
                if stage_id == "question_set" and not self.category_ready(qualification):
                    status = "waiting"

            if (
                stage.get("policyVersion") is not None
                and stage.get("supportsGroupScope")
            ):
                version_items = version_items_by_stage[stage_id]
                current_version_count = sum(
                    item["status"] == "current" for _, item in version_items
                )
                outdated_version_count = sum(
                    item["status"] in {"outdated", "future"}
                    for _, item in version_items
                )
                unrecorded_version_count = sum(
                    item["status"] == "unrecorded" for _, item in version_items
                )
                version_tracking_active = True
                stage.update(
                    {
                        "versionTrackingActive": version_tracking_active,
                        "versionCurrentCount": current_version_count,
                        "versionOutdatedCount": outdated_version_count,
                        "versionUnrecordedCount": unrecorded_version_count,
                    }
                )
                if outdated_version_count or unrecorded_version_count:
                    target_count = len(version_items)
                    complete = current_version_count
                    version_target_questions = [
                        question
                        for question, item in version_items
                        if item["status"] != "current"
                    ]
                    combined_targets: list[Mapping[str, Any]] = []
                    seen_targets: set[str] = set()
                    for question in [*target_questions, *version_target_questions]:
                        key = self._question_key(question)
                        if key not in seen_targets:
                            seen_targets.add(key)
                            combined_targets.append(question)
                    target_questions = combined_targets
                    if stage_id == "law_audit":
                        output_files = _unique(
                            [
                                *output_files,
                                *(
                                    self._law_audit_output_path(question)
                                    for question in version_target_questions[:3]
                                ),
                            ]
                        )
                    elif stage.get("patchDir"):
                        output_files = _unique(
                            [
                                *output_files,
                                *(
                                    self._selected_or_expected_patch_path(
                                        str(question.get("paths", {}).get("source") or ""),
                                        stage,
                                    )
                                    for question in version_target_questions[:3]
                                    if question.get("paths", {}).get("source")
                                ),
                            ]
                        )
                    if status != "waiting":
                        status = (
                            "attention"
                            if outdated_version_count
                            else "not_started"
                            if complete == 0
                            else "in_progress"
                        )

            artifact_blockers = self._artifact_blockers_for_stage(
                groups,
                stage,
            )
            if artifact_blockers:
                status = "attention"
                issue_count += sum(
                    int(blocker.get("count") or 0)
                    for blocker in artifact_blockers
                )
                output_files = _unique(
                    [
                        *output_files,
                        *(
                            str(blocker.get("path") or "")
                            for blocker in artifact_blockers
                        ),
                    ]
                )
                stage["artifactResolutionBlockers"] = [
                    dict(blocker) for blocker in artifact_blockers
                ]

            target_files = _unique(
                str(question.get("paths", {}).get("source") or "")
                for question in target_questions
            )
            stage.update(
                {
                    "status": status,
                    "completeCount": complete,
                    "targetCount": target_count,
                    "remainingCount": max(target_count - complete, 0),
                    "issueCount": issue_count,
                    "targetPreview": target_files[:3],
                    "outputPreview": output_files[:3],
                }
            )
            stage["missingSummary"] = self._missing_summary(stage)
            stage["action"] = self._stage_action(stage)
            stages.append(stage)
        return stages

    @staticmethod
    def _missing_summary(stage: Mapping[str, Any]) -> str:
        status = str(stage.get("status") or "")
        stage_id = str(stage.get("id") or "")
        remaining = int(stage.get("remainingCount") or 0)
        issues = int(stage.get("issueCount") or 0)
        if status == "ready":
            return "この工程に不足はありません。"
        if status == "waiting":
            return "前工程の完了が必要です。"
        if stage_id == "source":
            return "00_sourceの取得が必要です。"
        if stage_id == "setup":
            return "資格固有の方針文書が未作成です。"
        if stage_id == "category_setup":
            return "資格全体のcategory.jsonが未作成又は不正です。"
        outdated = int(stage.get("versionOutdatedCount") or 0)
        unrecorded = int(stage.get("versionUnrecordedCount") or 0)
        if stage.get("versionTrackingActive") and (outdated or unrecorded):
            parts = [
                value
                for value in (
                    f"洗い替え必要{outdated}問" if outdated else "",
                    f"未記録{unrecorded}問" if unrecorded else "",
                )
                if value
            ]
            return "・".join(parts) + "を現在の基準で整備します。"
        if issues:
            return f"要確認項目が{issues}件あります。"
        unit = "フォルダ" if stage_id == "delivery" else "問"
        return f"{remaining}{unit}の作業が残っています。"

    @staticmethod
    def _stage_action(stage: Mapping[str, Any]) -> dict[str, Any]:
        if stage.get("id") == "source":
            return {
                "type": "none",
                "label": (
                    "取得済み"
                    if stage.get("status") == "ready"
                    else "取得手順を確認"
                ),
            }
        if stage.get("status") == "waiting":
            return {"type": "none", "label": "前工程待ち"}
        return {
            "type": "open_run",
            "label": "再確認する" if stage.get("status") == "ready" else "この工程を開始",
        }

    @staticmethod
    def _maintenance_progress(
        version_statuses: list[tuple[Mapping[str, Any], Mapping[str, Any]]],
        *,
        force_all_required: bool = False,
    ) -> dict[str, int]:
        total = len(version_statuses)
        if force_all_required:
            return {
                "totalCount": total,
                "currentCount": 0,
                "requiredCount": total,
            }
        current = sum(
            bool(status.get("allCurrent"))
            and not (
                set(question.get("issueCodes") or []) & LAW_AUDIT_ISSUES
            )
            and not (
                question.get("isLawRelated") is not False
                and not (question.get("projected") or {}).get("lawRevisionFacts")
            )
            for question, status in version_statuses
        )
        return {
            "totalCount": total,
            "currentCount": current,
            "requiredCount": total - current,
        }

    @staticmethod
    def _group_summary(
        group: Mapping[str, Any],
        maintenance_progress: Mapping[str, int],
    ) -> dict[str, Any]:
        questions = group.get("questions") or []
        issue_count = sum(bool(question.get("issues")) for question in questions)
        local_ready = all(
            all(
                question.get("workflow", {}).get(stage) == "match"
                for stage in ("merge", "convert", "upload")
            )
            for question in questions
        ) and not (group.get("artifactResolutionBlockers") or [])
        return {
            "listGroupId": str(group.get("listGroupId") or ""),
            "displayName": str(
                group.get("displayName") or group.get("listGroupId") or ""
            ),
            "questionCount": len(questions),
            "issueQuestionCount": issue_count,
            "localReady": local_ready,
            "artifactResolutionBlockers": [
                dict(blocker)
                for blocker in group.get("artifactResolutionBlockers") or []
                if isinstance(blocker, Mapping)
            ],
            "maintenanceProgress": dict(maintenance_progress),
        }
