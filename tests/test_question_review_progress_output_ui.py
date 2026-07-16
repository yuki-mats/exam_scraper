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


if __name__ == "__main__":
    unittest.main()
