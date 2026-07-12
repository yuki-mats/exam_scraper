from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


STAGE_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "source",
        "code": "00",
        "label": "取得",
        "purpose": "出題時の問題文・選択肢・解答情報を保存する",
        "kind": "source",
        "canonicalDocs": ["document/operations/exam_pipeline_manual_and_automation.md"],
    },
    {
        "id": "setup",
        "code": "準備",
        "label": "資格方針",
        "purpose": "出題範囲・解説・分類・法令確認の方針を固定する",
        "kind": "human",
        "canonicalDocs": [
            "prompt/qualification_docs/README.md",
            "prompt/qualification_docs/_template/01_law_reference_policy.md",
        ],
    },
    {
        "id": "question_type",
        "code": "01",
        "label": "問題形式",
        "purpose": "回答体験に合うquestionTypeを一問ずつ確定する",
        "kind": "human",
        "patchDir": "10_questionType_fixed",
        "patchSuffix": "questionType_fixed",
        "issueFields": ["questionType", "questionBodyText", "choiceTextList"],
        "canonicalDocs": ["prompt/01_prompt_fix_questionType.md"],
    },
    {
        "id": "question_intent",
        "code": "02",
        "label": "設問意図",
        "purpose": "正しいもの・誤っているもののどちらを選ぶ設問か確定する",
        "kind": "human",
        "patchDir": "15_correctChoiceText_fixed",
        "patchSuffix": "correctChoiceText_fixed",
        "issueFields": ["questionIntent", "correctChoiceText", "answer_result_text"],
        "canonicalDocs": ["prompt/02_prompt_fix_questionIntent.md"],
    },
    {
        "id": "correct_choice",
        "code": "02a",
        "label": "正答精査",
        "purpose": "設問・公式解答・全選択肢を一問ずつ照合し正誤を確定する",
        "kind": "human",
        "patchDir": "23_correctChoiceText_fixed",
        "patchSuffix": "correctChoiceText_fixed",
        "issueFields": ["correctChoiceText", "answer_result_text"],
        "canonicalDocs": ["prompt/02a_prompt_review_correctChoiceText.md"],
    },
    {
        "id": "law_context",
        "code": "02b",
        "label": "法令根拠",
        "purpose": "法令関連性と現行法の根拠候補を全問で整理する",
        "kind": "human",
        "patchDir": "18_law_context_prepared",
        "patchSuffix": "lawContext_prepared",
        "issueFields": [
            "isLawRelated",
            "lawGroundedExplanationNotNeeded",
            "lawReferences",
        ],
        "canonicalDocs": ["prompt/02b_prompt_prepare_law_context.md"],
    },
    {
        "id": "explanation",
        "code": "03",
        "label": "解説",
        "purpose": "正誤理由と学習用の補足質問を選択肢単位で整える",
        "kind": "human",
        "patchDir": "21_explanationText_added",
        "patchSuffix": "explanationText_added",
        "issueFields": [
            "explanationText",
            "suggestedQuestions",
            "suggestedQuestionDetails",
        ],
        "canonicalDocs": ["prompt/03_prompt_add_explanationText.md"],
    },
    {
        "id": "law_audit",
        "code": "03b",
        "label": "現行法監査",
        "purpose": "条文根拠を一問一肢ずつ照合し現行法判定を固定する",
        "kind": "human",
        "issueFields": ["lawReferences", "lawRevisionFacts"],
        "canonicalDocs": [
            "prompt/03b_prompt_audit_current_law_and_patch.md",
            "document/operations/lawzilla_mcp_question_maintenance_workflow.md",
        ],
    },
    {
        "id": "question_set",
        "code": "04",
        "label": "問題集",
        "purpose": "category方針に沿って問題集へ意味的に紐付ける",
        "kind": "human",
        "patchDir": "22_questionSetId_linked",
        "patchSuffix": "questionSetId_linked",
        "issueFields": ["questionSetId"],
        "canonicalDocs": ["prompt/04_prompt_link_questionSetId.md"],
    },
    {
        "id": "delivery",
        "code": "出力",
        "label": "公開準備",
        "purpose": "patchをmerge・convertしupload-readyを検証する",
        "kind": "machine",
        "canonicalDocs": [
            "document/operations/exam_pipeline_manual_and_automation.md",
            "tools/question_bank/README.md",
        ],
    },
)

STAGE_BY_ID = {stage["id"]: stage for stage in STAGE_CATALOG}
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


class QualificationWorkflow:
    def __init__(self, repo_root: Path, inventory: Any):
        self.repo_root = repo_root.resolve()
        self.inventory = inventory

    def overview(self, qualification: str) -> dict[str, Any]:
        groups, questions = self._qualification_data(qualification)
        stages = self._build_stages(qualification, groups, questions)
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
        definition = STAGE_BY_ID.get(stage_id)
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
            "canonicalDocs": ["prompt/README.md", *definition.get("canonicalDocs", [])],
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
        qualification_policy = self.repo_root / "prompt" / "qualification_docs" / qualification
        if qualification_policy.is_dir():
            canonical.append(str(qualification_policy))

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
    ) -> list[dict[str, Any]]:
        total = len(questions)
        coverage: dict[str, int] = {
            str(stage["id"]): sum(
                _has_patch(question, str(stage["patchDir"]))
                for question in questions
            )
            for stage in STAGE_CATALOG
            if stage.get("patchDir")
        }
        stages: list[dict[str, Any]] = []
        for index, definition in enumerate(STAGE_CATALOG):
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
                        for item in STAGE_CATALOG[index + 1 :]
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
                    "canonicalDocs": [
                        "prompt/README.md",
                        *stage.get("canonicalDocs", []),
                    ],
                }
            )
            stage["action"] = self._stage_action(stage)
            stages.append(stage)
        return stages

    @staticmethod
    def _stage_action(stage: Mapping[str, Any]) -> dict[str, Any]:
        if stage.get("id") == "source":
            return {"type": "none", "label": "取得済み"}
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
