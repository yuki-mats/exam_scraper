from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "tools/question_review_console/static/app.js"
STYLE_PATH = ROOT / "tools/question_review_console/static/styles.css"


class ProgressOutputUiContractTests(unittest.TestCase):
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

    def test_validated_work_and_artifact_sync_have_separate_ui_states(self):
        javascript = APP_PATH.read_text(encoding="utf-8")

        self.assertIn("function artifactSyncNeedsAttention", javascript)
        self.assertIn('["succeeded", "current", "not_required"]', javascript)
        self.assertIn(
            'run?.status === "succeeded" && artifactSyncNeedsAttention(run)',
            javascript,
        )
        self.assertIn("公開用データは更新待ちです", javascript)
        self.assertIn("公開用データ更新待ち・手動再生成可", javascript)
        self.assertIn('statusLabel = "公開用データ更新待ち"', javascript)
        self.assertIn('? "公開用データ更新待ち"', javascript)
        self.assertIn("整備結果を承認済み", javascript)

    def test_manual_sync_preview_shows_strict_validation_reason(self):
        javascript = APP_PATH.read_text(encoding="utf-8")

        self.assertIn("preview.strictValidationWarnings?.length", javascript)
        self.assertIn("warning.detail", javascript)
        self.assertIn('warning.field || "lawRevisionFacts"', javascript)
        self.assertIn("現行法監査済み問題を再生成できません", javascript)


if __name__ == "__main__":
    unittest.main()
