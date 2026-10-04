import subprocess
from pathlib import Path


def test_exact_scope_includes_completed_and_blocked_and_rejects_incomplete_scope():
    root = Path(__file__).resolve().parents[1]
    javascript = (root / "tools/question_review_console/static/app.js").read_text()
    helper = javascript.split("function qualificationRunExactScopeQuestionIds", 1)[1].split(
        "function selectedQualificationRunUpdateTargetIds", 1
    )[0]
    script = "const MAX_QUALIFICATION_TARGET_COUNT = 500; function qualificationRunExactScopeQuestionIds" + helper
    script += """
const assert = require('node:assert/strict');
const run = {runId: 'original', targetCount: 2};
const progress = {runId: 'original', questionsIncluded: true, questions: [
  {questionId: 'completed', status: 'validated'}, {questionId: 'held', status: 'blocked'}
]};
assert.deepEqual(qualificationRunExactScopeQuestionIds(run, progress), ['completed', 'held']);
for (const invalid of [
  {...progress, runId: 'other'}, {...progress, questionsIncluded: false},
  {...progress, questions: progress.questions.slice(0, 1)},
  {...progress, questions: [{questionId: 'same'}, {questionId: 'same'}]},
  {...progress, questions: [{questionId: 'valid'}, {}]},
]) assert.throws(() => qualificationRunExactScopeQuestionIds(run, invalid));
const all = Array.from({length: 500}, (_, i) => ({questionId: `q${i}`}));
assert.equal(qualificationRunExactScopeQuestionIds({...run, targetCount: 500}, {...progress, questions: all}).length, 500);
assert.throws(() => qualificationRunExactScopeQuestionIds({...run, targetCount: 501}, {...progress, questions: all}));
"""
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
