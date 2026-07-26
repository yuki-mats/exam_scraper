from pathlib import Path
import subprocess
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
MONITOR = ROOT / "tools/question_review_console/static/monitor"
HTML = MONITOR / "index.html"
CSS = MONITOR / "monitor.css"
JS = MONITOR / "monitor.js"


class QuestionReviewMonitorUiTests(unittest.TestCase):
    def test_static_monitor_is_read_only_and_uses_accessible_tabs(self):
        html = HTML.read_text(encoding="utf-8")
        self.assertIn('class="monitor-grid"', html)
        for panel in ("activity", "artifact", "live"):
            self.assertIn(f'id="tab-{panel}"', html)
            self.assertIn(f'aria-controls="panel-{panel}"', html)
            self.assertIn(f'id="panel-{panel}"', html)
        self.assertIn('role="tablist"', html)
        self.assertEqual(html.count('role="tab"'), 3)
        self.assertEqual(html.count('role="tabpanel"'), 3)
        self.assertIn("保存済み artifact", html)
        self.assertIn("保存前出力", html)
        self.assertIn('id="artifact-validation"', html)
        for forbidden in (
            "停止",
            "一時停止",
            "承認",
            "再実行",
            'type="text"',
            "<textarea",
        ):
            self.assertNotIn(forbidden, html)

    def test_layout_covers_tablet_width_keyboard_and_reduced_motion(self):
        html = HTML.read_text(encoding="utf-8")
        css = CSS.read_text(encoding="utf-8")
        javascript = JS.read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 1029px)", css)
        self.assertNotIn("min-width: 980px", css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.assertIn("MAX_VISIBLE_LANES", javascript)
        self.assertIn("MAX_STAGE_LANES", javascript)
        for key in ("ArrowRight", "ArrowLeft", "Home", "End"):
            self.assertIn(key, javascript)
        self.assertIn('aria-selected="true"', html)
        self.assertIn(":focus-visible", css)

    def test_monitor_contract_polls_snapshot_and_artifacts_independent_of_events(self):
        javascript = JS.read_text(encoding="utf-8")
        self.assertIn('api("/runs"', javascript)
        self.assertIn('/snapshot`', javascript)
        self.assertIn('/events`', javascript)
        self.assertIn('/artifacts`', javascript)
        self.assertIn("REFRESH_INTERVAL_MS", javascript)
        self.assertIn("refreshLoop(context)", javascript)
        self.assertIn("Promise.allSettled([loadSnapshot(context), refreshArtifacts(context)])", javascript)
        self.assertIn("after: state.cursor", javascript)
        self.assertIn("waitMs: 25000", javascript)
        self.assertIn("new AbortController()", javascript)
        self.assertIn("state.generation", javascript)
        self.assertNotIn('method: "POST"', javascript)
        self.assertNotIn('method: "DELETE"', javascript)

    def test_plain_text_allowlist_observed_at_and_stable_deep_link_are_explicit(self):
        javascript = JS.read_text(encoding="utf-8")
        for label in (
            "AGENT発言",
            "公開推論サマリー",
            "PLAN",
            "TOOL",
            "成果物保存",
            "状態",
            "ERROR",
        ):
            self.assertIn(label, javascript)
        self.assertIn("CORRELATION_FIELDS", javascript)
        self.assertIn("observedAt", javascript)
        self.assertIn('params.set("qualification"', javascript)
        self.assertIn('params.set("listGroupId"', javascript)
        self.assertIn('params.set("questionId"', javascript)
        self.assertIn("textContent", javascript)
        self.assertNotIn("innerHTML", javascript)
        self.assertNotIn("raw reasoning", javascript.lower())
        self.assertNotIn("生の推論", javascript)

    def test_node_fake_fetch_and_state_transitions(self):
        script = textwrap.dedent(
            r"""
            const fs = require("fs");
            const vm = require("vm");
            const assert = require("assert");
            const source = fs.readFileSync(process.argv[1], "utf8");
            const sandbox = {
              __MONITOR_TEST__: true,
              location: {
                search: "?qualification=demo&runId=run-a",
                href: "http://localhost/monitor?qualification=demo&runId=run-a",
              },
              URL,
              URLSearchParams,
              AbortController,
              DOMException,
              Intl,
              Date,
              Map,
              Set,
              Promise,
              setTimeout,
              clearTimeout,
            };
            sandbox.globalThis = sandbox;
            vm.createContext(sandbox);
            vm.runInContext(source, sandbox, { filename: process.argv[1] });
            const ui = sandbox.MonitorUiTest;

            (async () => {
              let fetchCalls = 0;
              sandbox.fetch = (_url, options) => {
                fetchCalls += 1;
                return new Promise((_resolve, reject) => {
                  options.signal.addEventListener("abort", () => {
                    const error = new Error("aborted");
                    error.name = "AbortError";
                    reject(error);
                  }, { once: true });
                });
              };
              const oldRun = ui.beginGeneration("demo", "run-a");
              const oldRequest = ui.requestJson("/slow", oldRun.signal);
              const newRun = ui.beginGeneration("demo", "run-b");
              await assert.rejects(oldRequest, (error) => error.name === "AbortError");
              assert.strictEqual(fetchCalls, 1);
              assert.strictEqual(oldRun.signal.aborted, true);
              assert.strictEqual(ui.isCurrent(oldRun), false);
              assert.strictEqual(ui.isCurrent(newRun), true);

              ui.state.events = [];
              ui.state.eventIndex.clear();
              ui.state.seenEventIds.clear();
              ui.ingestEvents({ events: [{
                eventId: "server:1",
                serverInstanceId: "server",
                observedAt: 1,
                type: "agentMessage",
                correlation: { itemId: "item-1", threadId: "thread-1" },
                payload: { delta: "公", secret: "must-not-render" },
              }]});
              ui.ingestEvents({ events: [{
                eventId: "server:2",
                serverInstanceId: "server",
                observedAt: 2,
                type: "agentMessage",
                correlation: { itemId: "item-1", threadId: "thread-1" },
                payload: { delta: "公開" },
              }]});
              ui.ingestEvents({ events: [{
                eventId: "server:3",
                serverInstanceId: "server",
                observedAt: 3,
                type: "agentMessage",
                correlation: { itemId: "item-1", threadId: "thread-1" },
                payload: { delta: "です" },
              }]});
              ui.ingestEvents({ events: [{
                eventId: "server:3",
                serverInstanceId: "server",
                observedAt: 3,
                type: "agentMessage",
                correlation: { itemId: "item-1", threadId: "thread-1" },
                payload: { delta: "です" },
              }]});
              assert.strictEqual(ui.state.events.length, 1);
              assert.strictEqual(ui.state.events[0].displayText, "公開です");
              assert.strictEqual("secret" in ui.state.events[0].payload, false);
              assert.strictEqual(ui.state.observation.lastObservedAt, 3);

              const tokenEvent = ui.normalizedEvent({
                eventId: "server:token",
                type: "tokenUsage",
                payload: {
                  usage: {
                    last: { inputTokens: 10, secret: 999 },
                    total: { totalTokens: 20 },
                    modelContextWindow: 200000,
                  },
                },
              });
              assert.strictEqual(tokenEvent.payload.usage.last.inputTokens, 10);
              assert.strictEqual(tokenEvent.payload.usage.total.totalTokens, 20);
              assert.strictEqual(tokenEvent.payload.usage.modelContextWindow, 200000);
              assert.strictEqual("secret" in tokenEvent.payload.usage.last, false);
              assert(ui.eventText(tokenEvent).includes("last inputTokens: 10"));

              const planEvent = ui.normalizedEvent({
                eventId: "server:plan",
                type: "plan",
                payload: {
                  explanation: "公開計画",
                  plan: [{ step: "内容確認", status: "inProgress", prompt: "private" }],
                },
              });
              assert.strictEqual(planEvent.category, "plan");
              assert.strictEqual(planEvent.payload.plan[0].step, "内容確認");
              assert.strictEqual("prompt" in planEvent.payload.plan[0], false);
              assert(ui.eventText(planEvent).includes("inProgress · 内容確認"));

              const summaryEvent = ui.normalizedEvent({
                eventId: "server:summary",
                type: "reasoningSummary",
                payload: {
                  summaryParts: ["根拠を確認", "結論を確定"],
                  rawReasoning: "private",
                },
              });
              assert.strictEqual(summaryEvent.payload.summaryParts.length, 2);
              assert.strictEqual("rawReasoning" in summaryEvent.payload, false);
              assert.strictEqual(
                ui.eventText(summaryEvent),
                "根拠を確認\n結論を確定",
              );

              const errorEvent = ui.normalizedEvent({
                eventId: "server:error",
                type: "error",
                payload: {
                  message: "公開エラー",
                  willRetry: true,
                  traceback: "private",
                },
              });
              assert.strictEqual(errorEvent.payload.message, "公開エラー");
              assert.strictEqual(errorEvent.payload.willRetry, true);
              assert.strictEqual("traceback" in errorEvent.payload, false);

              ui.applyObservationHealth({ gap: true }, false);
              assert.strictEqual(ui.state.observation.gap, true);
              ui.applyObservationHealth({}, false);
              assert.strictEqual(ui.state.observation.gap, true);
              ui.applyObservationHealth({
                observationHealth: { status: "healthy" },
                observedAt: 4,
              }, true);
              assert.strictEqual(ui.state.observation.gap, true);
              ui.applyObservationHealth({
                observationHealth: { status: "healthy", gap: false, stale: false },
                observedAt: 5,
              }, true);
              assert.strictEqual(ui.state.observation.gap, false);
              assert.strictEqual(ui.state.observation.health, "healthy");

              const artifacts = ui.normalizeArtifacts({
                artifacts: [
                  { artifactId: "draft", path: "output/draft.json", saved: false },
                  {
                    artifactId: "saved",
                    path: "output/result.json",
                    identity: { qualification: "demo", questionId: "q-1", stageCode: "03" },
                    contentState: { status: "saved" },
                    receiptValidation: { status: "validated", validated: true },
                    artifactSync: { status: "deferred" },
                  },
                ],
                artifactState: {
                  byId: {
                    saved: { artifactId: "saved", saved: true, validated: true },
                  },
                },
              });
              assert.strictEqual(artifacts.find((item) => item.id === "draft").saved, false);
              assert.strictEqual(artifacts.find((item) => item.id === "saved").saved, true);
              assert.strictEqual(artifacts.find((item) => item.id === "saved").validated, true);
              assert.strictEqual(artifacts.find((item) => item.id === "saved").stage, "03");
              assert.strictEqual(artifacts.find((item) => item.id === "saved").syncStatus, "deferred");

              const sharedPatchRecords = ui.normalizeArtifacts({
                artifacts: [
                  {
                    path: "output/demo/shared.json",
                    identity: {
                      childRunId: "child-1",
                      questionId: "q-1",
                      batchId: "batch-1",
                      sourceQuestionKey: "demo:2026:q-1",
                    },
                    contentState: { status: "saved" },
                    content: '{"questionId":"q-1"}',
                  },
                  {
                    path: "output/demo/shared.json",
                    identity: {
                      childRunId: "child-2",
                      questionId: "q-2",
                      batchId: "batch-2",
                      sourceQuestionKey: "demo:2026:q-2",
                    },
                    contentState: { status: "saved" },
                    content: '{"questionId":"q-2"}',
                  },
                  {
                    path: "output/demo/shared.json",
                    identity: {
                      childRunId: "child-3",
                      questionId: "q-3",
                      batchIndex: 0,
                      sourceQuestionKey: "demo:2026:q-3",
                    },
                    contentState: { status: "saved" },
                    content: '{"questionId":"q-3"}',
                  },
                ],
              });
              assert.strictEqual(sharedPatchRecords.length, 3);
              assert.notStrictEqual(sharedPatchRecords[0].id, sharedPatchRecords[1].id);
              assert.strictEqual(sharedPatchRecords[0].identity.batchId, "batch-1");
              assert.strictEqual(
                sharedPatchRecords[0].scopeLabel,
                "問題 q-1 · batch-1",
              );
              assert.strictEqual(
                sharedPatchRecords[1].identity.sourceQuestionKey,
                "demo:2026:q-2",
              );
              assert.strictEqual(
                sharedPatchRecords[2].scopeLabel,
                "問題 q-3 · 0",
              );

              const link = ui.deepLink({
                qualification: "demo",
                run: { targetGroupIds: ["2026"], questionIds: ["q-1"] },
              });
              assert(link.includes("qualification=demo"));
              assert(link.includes("listGroupId=2026"));
              assert(link.includes("questionId=q-1"));

              const questions = Array.from({ length: 300 }, (_, index) => ({
                questionId: `q-${index}`,
                stageCode: "03",
                childRunId: "child-1",
                threadId: "thread-1",
                startedAt: "2026-07-27T00:00:00Z",
              }));
              const lanes = ui.buildLanes({ run: { stageCode: "03", questionExecutions: questions } }, []);
              assert.strictEqual(lanes.length, 1);
              assert.strictEqual(lanes[0].questionIds.size, 300);

              const mergedLanes = ui.buildLanes({
                run: { runId: "parent", status: "running" },
                lanes: [{
                  runId: "child-1",
                  parentRunId: "parent",
                  stageCode: "03",
                  stageLabel: "内容評価",
                  questionId: "q-1",
                  status: "running",
                  startedAt: "2026-07-27T00:00:00Z",
                }],
              }, [{
                rawType: "turnState",
                observedAt: 1,
                correlation: {
                  childRunId: "child-1",
                  threadId: "thread-1",
                  questionId: "q-1",
                  stageId: "03",
                },
                payload: { state: "inProgress" },
              }]);
              assert.strictEqual(mergedLanes.length, 1);
              assert.strictEqual(mergedLanes[0].stage, "内容評価");
              assert.strictEqual(mergedLanes[0].threadId, "thread-1");
            })().catch((error) => {
              console.error(error);
              process.exitCode = 1;
            });
            """
        )
        result = subprocess.run(
            ["node", "-e", script, str(JS)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_javascript_passes_node_syntax_check(self):
        result = subprocess.run(
            ["node", "--check", str(JS)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
