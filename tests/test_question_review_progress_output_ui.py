from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "tools/question_review_console/static/app.js"
STYLE_PATH = ROOT / "tools/question_review_console/static/styles.css"
INDEX_PATH = ROOT / "tools/question_review_console/static/index.html"


class ProgressOutputUiContractTests(unittest.TestCase):
    def test_partial_refresh_ui_uses_year_fields_and_two_simple_modes(self):
        javascript = APP_PATH.read_text(encoding="utf-8")
        html = INDEX_PATH.read_text(encoding="utf-8")
        css = STYLE_PATH.read_text(encoding="utf-8")

        self.assertIn('id="qualification-run-update-fieldset"', html)
        self.assertNotIn('id="qualification-run-question-start"', html)
        self.assertNotIn('id="qualification-run-question-end"', html)
        self.assertIn('id="maintenance-group-progress-title"', html)
        self.assertIn('id="maintenance-entry-guidance"', html)
        self.assertNotIn('id="maintenance-start"', html)
        self.assertIn('id="qualification-run-update-all"', html)
        self.assertIn('id="qualification-run-update-clear"', html)
        self.assertIn('value="needed" checked', html)
        self.assertIn("整備が必要な問題だけ", html)
        self.assertIn("選択年度の全問題を洗い替える", html)
        self.assertIn("function openListGroupMaintenance", javascript)
        self.assertIn('fieldFirst: true', javascript)
        self.assertIn(
            "function qualificationRunStageIdsForUpdateTargetIds",
            javascript,
        )
        self.assertIn(
            'action.addEventListener("click", () => openListGroupMaintenance(group.listGroupId))',
            javascript,
        )
        self.assertIn('action.disabled = isRunning || workflow.restartRequired', javascript)
        self.assertIn('"整備・洗い替え"', javascript)
        self.assertIn("function returnToMaintenanceGroupList", javascript)
        self.assertIn("function qualificationRunUpdateTargets", javascript)
        self.assertNotIn("function selectedQualificationRunQuestionRange", javascript)
        self.assertIn("function selectAllQualificationRunUpdateTargets", javascript)
        self.assertIn("scopeLabelForGroups(groupIds)", javascript)
        self.assertIn(": selectableTargetIds", javascript)
        self.assertIn("questionRange: questionRange || undefined", javascript)
        self.assertIn("preview.selectedUpdateTargets", javascript)
        self.assertNotIn("examYear", javascript[
            javascript.index("function qualificationRunSelectableUpdateTargets") :
            javascript.index("function qualificationRunSupportsGroupScope")
        ])
        self.assertIn(".run-update-options", css)
        self.assertIn(".run-update-actions", css)
        self.assertNotIn(".run-question-range", css)

    def test_list_group_entry_derives_stages_from_selected_update_targets(self):
        javascript = APP_PATH.read_text(encoding="utf-8")

        target_section = javascript[
            javascript.index("function qualificationRunSelectableUpdateTargets") :
            javascript.index("function qualificationRunSupportsGroupScope")
        ]
        dialog_section = javascript[
            javascript.index("function openQualificationRunDialog") :
            javascript.index("function cancelQualificationRunPreview")
        ]

        self.assertIn('stage.kind === "human"', target_section)
        self.assertIn("stage.batchSelectable", target_section)
        self.assertIn("stage.supportsGroupScope", target_section)
        self.assertIn("selected.has(target.selectionId)", target_section)
        self.assertIn("qualificationRunStageIdsForUpdateTargetIds", dialog_section)
        list_group_section = javascript[
            javascript.index("function openListGroupMaintenance") :
            javascript.index("function returnToMaintenanceGroupList")
        ]
        self.assertNotIn("updateTargetIds", list_group_section)
        self.assertIn('mode: "needed"', list_group_section)
        self.assertIn("更新する項目を一つ以上選択してください。", javascript)
        self.assertNotIn("examYear", target_section)

    def test_question_output_uses_structured_stage_and_value_nodes(self):
        javascript = APP_PATH.read_text(encoding="utf-8")
        output_section = javascript[
            javascript.index("function progressQuestionOutputSection") :
            javascript.index("async function openProgressQuestion")
        ]
        question_dialog = javascript[
            javascript.index("async function openProgressQuestion") :
            javascript.index("function enterQualificationProgressView")
        ]
        list_preview = javascript[
            javascript.index("function progressQuestionOutputText") :
            javascript.index("function setQualificationRunStatusDetail")
        ]

        self.assertIn('element("section", "progress-output-stage")', output_section)
        self.assertIn("progressValueNode(", output_section)
        self.assertIn('entry.field === "explanationText"', output_section)
        self.assertIn(
            "content.append(progressQuestionOutputSection(",
            question_dialog,
        )
        self.assertNotIn('.join("\\n")', question_dialog)
        self.assertIn("const entry = progressResultEntry(event);", list_preview)
        self.assertNotIn("progressResultText(event)", list_preview)

    def test_question_dialog_shows_anki_plus_display_fields(self):
        javascript = APP_PATH.read_text(encoding="utf-8")
        css = STYLE_PATH.read_text(encoding="utf-8")
        suggestions = javascript[
            javascript.index("function progressQuestionSuggestionsSection") :
            javascript.index("function progressQuestionOutputSection")
        ]
        question_dialog = javascript[
            javascript.index("async function openProgressQuestion") :
            javascript.index("function enterQualificationProgressView")
        ]

        self.assertIn("projected.questionType", question_dialog)
        self.assertIn("questionType（問題形式）", question_dialog)
        self.assertIn("progressQuestionSuggestionsSection(projected)", question_dialog)
        self.assertNotIn("suggestedQuestions（補足質問）", question_dialog)
        self.assertNotIn("suggestedQuestionDetails（補足質問と回答）", question_dialog)
        self.assertIn("suggestionGroups(projected)", suggestions)
        self.assertIn("group.choiceIndex", suggestions)
        self.assertIn("補足質問と回答", suggestions)
        self.assertIn("detail.answer", suggestions)
        self.assertIn("progress-suggestion-card", suggestions)
        self.assertIn(".progress-suggestion-card", css)

    def test_source_answer_difference_has_filter_badge_and_comparison(self):
        javascript = APP_PATH.read_text(encoding="utf-8")
        html = INDEX_PATH.read_text(encoding="utf-8")
        css = STYLE_PATH.read_text(encoding="utf-8")

        self.assertIn('id="source-answer-difference"', html)
        self.assertIn("sourceAnswerDifference", javascript)
        self.assertIn("sourceCorrectChoiceComparison", javascript)
        self.assertIn("00_sourceと現在の正答", javascript)
        self.assertIn("現在のcorrectChoiceText", javascript)
        self.assertIn(".source-answer-comparison-card", css)

    def test_choice_results_have_mobile_readable_cards_and_verdict_labels(self):
        javascript = APP_PATH.read_text(encoding="utf-8")
        css = STYLE_PATH.read_text(encoding="utf-8")

        self.assertIn("function progressVerdictParts", javascript)
        self.assertIn('text: explanation ? text : ""', javascript)
        self.assertIn("progress-value-verdict ${parts.tone}", javascript)
        for selector in (
            ".progress-value-list",
            ".progress-value-item",
            ".progress-value-verdict.correct",
            ".progress-value-verdict.incorrect",
        ):
            self.assertIn(selector, css)
        self.assertIn(".progress-value-item p { font-size: 13px;", css)

    def test_active_run_explains_safe_parallelism(self):
        javascript = APP_PATH.read_text(encoding="utf-8")

        self.assertIn("run.parallelWorkerLimit", javascript)
        self.assertIn("run.researchSubagentCount", javascript)
        self.assertIn('run.executionPhase === "parallel_research"', javascript)
        self.assertIn("判断調査中（最大${parallelWorkers}並列・読取専用）", javascript)
        self.assertIn("判断${actualResearchWorkers}並列完了・保存中", javascript)
        self.assertIn('researchStatus === "failed"', javascript)
        self.assertIn("並列調査失敗・単独保存中", javascript)
        self.assertIn("判断${actualResearchWorkers}並列完了（保存は1件ずつ）", javascript)
        self.assertIn("並列調査実績0・単独処理", javascript)
        self.assertIn("判断最大${parallelWorkers}並列（保存は1件ずつ）", javascript)
        self.assertIn(
            "入力別に最大${modelBatchSize}問・最大${questionConcurrency}turn・検査と確定は1問ずつ",
            javascript,
        )

    def test_validated_work_and_artifact_sync_have_separate_ui_states(self):
        javascript = APP_PATH.read_text(encoding="utf-8")

        self.assertIn("function artifactSyncNeedsAttention", javascript)
        self.assertIn('["succeeded", "current", "not_required"]', javascript)
        self.assertIn(
            "const artifactSyncPending = verified && artifactSyncNeedsAttention(run)",
            javascript,
        )
        self.assertIn(
            'const unverified = run?.status === "succeeded" && !verified',
            javascript,
        )
        self.assertIn("公開用データは更新待ちです", javascript)
        self.assertIn("公開用データ更新待ち・手動再生成可", javascript)
        self.assertIn('statusLabel = "公開用データ更新待ち"', javascript)
        self.assertIn('? "公開用データ更新待ち"', javascript)
        self.assertIn("整備結果を承認済み", javascript)

    def test_partial_failed_run_uses_touched_questions_for_stop_state(self):
        javascript = APP_PATH.read_text(encoding="utf-8")
        view_state = javascript.split(
            "function qualificationRunViewState", 1
        )[1].split("function renderQualificationRunPhases", 1)[0]

        self.assertIn("progress?.touchedQuestionCount", view_state)
        self.assertIn(
            'phase = touchedQuestions ? "問題整備中に停止"',
            view_state,
        )
        self.assertIn("対象${targetQuestions}問のうち${touchedQuestions}問", view_state)
        self.assertNotIn(
            'phase = completedQuestions ? "問題整備中に停止"',
            view_state,
        )

    def test_manual_sync_preview_shows_strict_validation_reason(self):
        javascript = APP_PATH.read_text(encoding="utf-8")

        self.assertIn("preview.strictValidationWarnings?.length", javascript)
        self.assertIn("warning.detail", javascript)
        self.assertIn('warning.field || "lawRevisionFacts"', javascript)
        self.assertIn("現行法監査済み問題を再生成できません", javascript)


if __name__ == "__main__":
    unittest.main()
