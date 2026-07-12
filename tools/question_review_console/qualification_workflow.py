from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from tools.question_review_console.workflow_catalog import WorkflowCatalog

LAW_AUDIT_ISSUES = {
    "law_audit_metadata_incomplete",
    "law_audit_verdict_mismatch",
    "law_hold",
    "law_basis_missing",
}
RUN_MODES = {
    "remaining": "未作業のみ",
    "attention": "要確認のみ",
    "refresh": "全件洗い替え",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _has_patch(question: Mapping[str, Any], patch_dir: str) -> bool:
    marker = f"/{patch_dir}/"
    return any(
        marker in str(path)
        for path in question.get("paths", {}).get("patches") or []
    )


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
    return str(
        group_dir
        / str(stage["patchDir"])
        / f"{source.stem}_{stage['patchSuffix']}.json"
    )


def _unique(values: Iterable[str]) -> list[str]:
    return sorted({value for value in values if value})


def _ordered_unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


class QualificationWorkflow:
    def __init__(self, repo_root: Path, inventory: Any):
        self.repo_root = repo_root.resolve()
        self.inventory = inventory
        self.catalog_store = WorkflowCatalog(self.repo_root)

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
            stage["canonicalDocs"] = _ordered_unique(
                [
                    *stage_documents,
                    *(
                        qualification_documents
                        if stage.get("kind") == "human"
                        else []
                    ),
                    *(human_documents if stage.get("kind") == "human" else []),
                    *shared_documents,
                ]
            )
            stages.append(stage)
        effective_hash = hashlib.sha256(
            json.dumps(
                [loaded["catalogHash"], qualification_documents],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return {
            "qualification": qualification or None,
            "generatedAt": _now_iso(),
            "system": system,
            "catalogHash": effective_hash,
            "catalogPath": loaded["catalogPath"],
            "stages": stages,
        }

    def overview(self, qualification: str) -> dict[str, Any]:
        catalog = self.catalog(qualification)
        groups, questions = self._qualification_data(qualification)
        stages = self._build_stages(
            qualification, groups, questions, catalog["stages"]
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
        group_summaries = [self._group_summary(group) for group in groups]
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
                "issueCounts": dict(sorted(issue_counts.items())),
            },
            "stages": stages,
            "groups": group_summaries,
        }

    def plan(
        self, qualification: str, stage_id: str, mode: str = "remaining"
    ) -> dict[str, Any]:
        if mode not in RUN_MODES:
            raise ValueError(f"対象範囲が不正です: {mode}")
        catalog = self.catalog(qualification)
        definition = next(
            (stage for stage in catalog["stages"] if stage["id"] == stage_id), None
        )
        if definition is None:
            raise ValueError(f"対象工程がありません: {stage_id}")
        if stage_id == "source":
            raise ValueError("00_sourceは取得工程の正本であり、この画面から再生成しません。")

        groups, questions = self._qualification_data(qualification)
        target_questions: list[Mapping[str, Any]] = []
        output_files: list[str] = []
        target_group_ids: list[str] = []

        if stage_id == "setup":
            policy_dir = Path("prompt") / "qualification_docs" / qualification
            policy_exists = (self.repo_root / policy_dir).is_dir()
            if mode == "attention":
                target_questions = questions if not policy_exists else []
            elif mode == "remaining":
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
                    "03_category_preparation.md",
                )
            ] if target_questions else []
        elif stage_id == "law_audit":
            applicable = [
                question for question in questions if question.get("isLawRelated") is True
            ]
            if mode == "refresh":
                target_questions = applicable
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
                    or not (question.get("projected") or {}).get("lawRevisionFacts")
                ]
            source_files = _unique(
                str(question.get("paths", {}).get("source") or "")
                for question in target_questions
            )
            output_files = _unique(
                self._law_audit_output_path(question) for question in target_questions
            )
        elif stage_id == "delivery":
            if mode == "refresh":
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
            patch_dir = str(definition["patchDir"])
            issue_fields = set(definition.get("issueFields") or [])
            if mode == "refresh":
                target_questions = questions
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
                _expected_patch_path(
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
        return {
            "qualification": qualification,
            "stageId": stage_id,
            "stageCode": str(definition["code"]),
            "stageLabel": str(definition["label"]),
            "purpose": str(definition["purpose"]),
            "kind": str(definition["kind"]),
            "mode": mode,
            "modeLabel": RUN_MODES[mode],
            "targetCount": len(target_group_ids) if stage_id == "delivery" else len(target_questions),
            "targetGroupIds": target_group_ids,
            "sourceFiles": source_files,
            "outputFiles": output_files,
            "canonicalDocs": list(definition.get("canonicalDocs") or []),
            "catalogHash": catalog["catalogHash"],
            "force": stage_id == "delivery" and mode == "refresh",
        }

    def prompt(
        self, qualification: str, stage_id: str, mode: str = "remaining"
    ) -> dict[str, Any]:
        plan = self.plan(qualification, stage_id, mode)
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

        lines = [
            "# 資格単位の問題整備",
            "",
            f"- 工程: `{plan['stageCode']} {plan['stageLabel']}`",
            f"- 範囲: `{plan['modeLabel']}`",
            f"- 対象: `{plan['targetCount']}件`",
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
                f"上記正本に従い、qualification=`{qualification}`の"
                f"{plan['stageCode']} {plan['stageLabel']}を対象ごとに一件ずつ実施する。"
            ),
            *(
                ["各問題は問題文と全選択肢を結合した命題として読み、Lawzilla MCPとFirestore条文検索で一問一肢ずつ根拠を照合する。"]
                if stage_id == "law_audit"
                else []
            ),
            "既存の正本と共通workflowを優先し、資格固有の局所ルールを重複実装しない。",
            "対象外の変更と`00_source`、既存IDは変更しない。作業後は正本記載の検証を実行する。",
        ]
        return {
            "qualification": qualification,
            "stageId": stage_id,
            "mode": mode,
            "targetCount": plan["targetCount"],
            "prompt": "\n".join(lines).strip() + "\n",
        }

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
        for group in groups:
            group_id = str(group.get("listGroupId") or "")
            for raw in group.get("questions") or []:
                if raw.get("listGroupId"):
                    questions.append(raw)
                else:
                    question = dict(raw)
                    question["listGroupId"] = group_id
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

    @staticmethod
    def _group_id_from_source(question: Mapping[str, Any]) -> str:
        source = Path(str(question.get("paths", {}).get("source") or ""))
        return source.parent.parent.name if len(source.parents) >= 2 else ""

    @staticmethod
    def _law_audit_output_path(question: Mapping[str, Any]) -> str:
        existing = [
            str(path)
            for path in question.get("paths", {}).get("patches") or []
            if "/21_explanationText_added/" in str(path)
        ]
        if existing:
            return existing[-1]
        source = Path(str(question.get("paths", {}).get("source") or ""))
        if not source.name:
            return ""
        return str(
            source.parent.parent
            / "21_explanationText_added"
            / f"{source.stem}_explanationText_added.json"
        )

    def _build_stages(
        self,
        qualification: str,
        groups: list[Mapping[str, Any]],
        questions: list[Mapping[str, Any]],
        definitions: list[Mapping[str, Any]],
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
            elif stage_id == "law_audit":
                law_context_ready = coverage.get("law_context", 0) == total and total > 0
                target_questions = [
                    question
                    for question in questions
                    if question.get("isLawRelated") is True
                ]
                target_count = len(target_questions)
                incomplete = [
                    question
                    for question in target_questions
                    if set(question.get("issueCodes") or []) & LAW_AUDIT_ISSUES
                    or not (question.get("projected") or {}).get("lawRevisionFacts")
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
                complete = coverage.get(stage_id, 0)
                target_count = total
                target_questions = [
                    question
                    for question in questions
                    if not _has_patch(question, patch_dir)
                ]
                issue_count = sum(
                    _issue_count(question, set(stage.get("issueFields") or []))
                    for question in questions
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
                output_files = _unique(
                    _expected_patch_path(
                        str(question.get("paths", {}).get("source") or ""), stage
                    )
                    for question in target_questions
                    if question.get("paths", {}).get("source")
                )

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
    def _group_summary(group: Mapping[str, Any]) -> dict[str, Any]:
        questions = group.get("questions") or []
        issue_count = sum(bool(question.get("issues")) for question in questions)
        local_ready = all(
            all(
                question.get("workflow", {}).get(stage) == "match"
                for stage in ("merge", "convert", "upload")
            )
            for question in questions
        )
        return {
            "listGroupId": str(group.get("listGroupId") or ""),
            "questionCount": len(questions),
            "issueQuestionCount": issue_count,
            "localReady": local_ready,
        }
