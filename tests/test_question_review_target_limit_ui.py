from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


class QuestionReviewTargetLimitUiTests(unittest.TestCase):
    def setUp(self) -> None:
        static = (
            Path(__file__).resolve().parents[1]
            / "tools"
            / "question_review_console"
            / "static"
        )
        self.html = (static / "index.html").read_text(encoding="utf-8")
        self.javascript_path = static / "app.js"
        self.javascript = self.javascript_path.read_text(encoding="utf-8")

    def test_dialog_exposes_one_to_five_hundred_question_limit(self) -> None:
        self.assertIn('id="qualification-run-target-count"', self.html)
        self.assertIn('min="1" max="500"', self.html)
        self.assertIn('value="100"', self.html)
        self.assertIn("一度に整備する問題数", self.html)
        self.assertIn("MAX_QUALIFICATION_TARGET_COUNT = 500", self.javascript)
        self.assertIn("DEFAULT_QUALIFICATION_TARGET_COUNT = 100", self.javascript)

    def test_preview_rebinds_limited_scope_to_exact_question_ids(self) -> None:
        preview_source = self.javascript.split(
            "async function previewQualificationRun", 1
        )[1].split("function renderQualificationRunPreview", 1)[0]
        start_source = self.javascript.split(
            "async function startQualificationRun", 1
        )[1].split("function setQualificationRunRunning", 1)[0]

        self.assertIn("qualificationRunLimitedQuestionIds", preview_source)
        self.assertIn("body: { ...requestBody, questionIds: limitedQuestionIds }", preview_source)
        self.assertIn("preview.requestedQuestionIds?.length", start_source)

    def test_limit_selection_is_deterministic_and_preserves_explicit_scope(self) -> None:
        script = r"""
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const helper = source.split("function qualificationRunLimitedQuestionIds", 2)[1]
  .split("function selectedQualificationRunSpeedMode", 1)[0];
const limited = new Function(`return function qualificationRunLimitedQuestionIds${helper}`)();
const preview = {
  kind: "orchestration",
  targetCount: 4,
  targetIdentity: { questionIds: ["q1", "q2", "q3", "q4"] },
};
if (JSON.stringify(limited(preview, [], 2)) !== JSON.stringify(["q1", "q2"])) {
  throw new Error("limit selection mismatch");
}
if (limited(preview, ["q4"], 2).length !== 0) {
  throw new Error("explicit scope must not be replaced");
}
if (limited({ ...preview, targetCount: 2, targetIdentity: { questionIds: ["q1", "q2"] } }, [], 2).length !== 0) {
  throw new Error("scope at limit must remain unchanged");
}
if (limited(preview, [], 2, "previous-run").length !== 0) {
  throw new Error("resume must inherit the server queue without a new explicit subset");
}
"""
        subprocess.run(
            ["node", "-e", script, str(self.javascript_path)],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_resume_displays_original_limit_without_allowing_scope_edits(self) -> None:
        self.assertIn(
            "questionLimit: blockedQuestionIds.length ? undefined : run.targetCount",
            self.javascript,
        )
        self.assertIn(
            "targetCountInput.disabled = targetCountFieldset.hidden || Boolean(options.resumedFrom)",
            self.javascript,
        )
        preview_source = self.javascript.split(
            "async function previewQualificationRun", 1
        )[1].split("function renderQualificationRunPreview", 1)[0]
        self.assertIn(
            "questionLimit,\n      qualificationRunResumedFrom(),",
            preview_source,
        )

    def test_previous_scope_exclusion_preserves_order_and_rejects_incomplete_identity(self) -> None:
        script = r"""
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const helper = source.split("function qualificationRunRemainingQuestionIds", 2)[1]
  .split("function selectedQualificationRunStageIds", 1)[0];
const remaining = new Function(`return function qualificationRunRemainingQuestionIds${helper}`)();
const preview = {targetCount: 4, targetIdentity: {questionIds: ["q1", "q2", "q3", "q4"]}};
const previous = {questionsIncluded: true, targetQuestionCount: 2,
  questions: [{questionId: "q2", queueStatus: "blocked"}, {questionId: "q4"}]};
if (JSON.stringify(remaining(preview, previous, 500)) !== JSON.stringify(["q1", "q3"])) {
  throw new Error("exclude all previous targets, including blocked ones, without reordering");
}
if (JSON.stringify(remaining(preview, previous, 1)) !== JSON.stringify(["q1"])) {
  throw new Error("limit applies after scope exclusion");
}
for (const invalid of [
  {...previous, questionsIncluded: false},
  {...previous, targetQuestionCount: 3},
  {...previous, questions: [{questionId: "q2"}, {questionId: "q2"}]},
]) {
  let rejected = false;
  try { remaining(preview, invalid, 500); } catch (e) { rejected = true; }
  if (!rejected) throw new Error("incomplete or duplicated previous scope accepted");
}
let rejected = false;
try { remaining({...preview, targetCount: 5}, previous, 500); } catch (e) { rejected = true; }
if (!rejected) throw new Error("incomplete preview scope accepted");
"""
        subprocess.run(["node", "-e", script, str(self.javascript_path)], check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
