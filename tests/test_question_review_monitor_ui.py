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
        for status_id in (
            "monitor-alerts",
            "snapshot-load-error",
            "run-api-warning",
            "snapshot-api-warning",
            "artifact-load-error",
            "artifact-api-warning",
        ):
            self.assertIn(f'id="{status_id}"', html)
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
        self.assertIn("@media (max-width: 520px)", css)
        self.assertNotIn("min-width: 980px", css)
        self.assertIn("[hidden] { display: none !important; }", css)
        self.assertIn(".connection-summary div { display: grid; }", css)
        self.assertIn("display: inline-flex;", css)
        self.assertNotIn(
            ".connection-summary div, .maintenance-link { display: none; }",
            css,
        )
        self.assertIn("grid-template-columns: minmax(0, 1fr);", css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.assertIn(
            ".artifact-button em.failed { color: var(--red);",
            css,
        )
        self.assertIn("MAX_VISIBLE_LANES", javascript)
        self.assertIn("MAX_STAGE_LANES", javascript)
        for key in ("ArrowRight", "ArrowLeft", "Home", "End"):
            self.assertIn(key, javascript)
        self.assertIn('aria-selected="true"', html)
        self.assertIn(":focus-visible", css)

    def test_monitor_contract_polls_snapshot_but_refreshes_artifacts_on_change(self):
        javascript = JS.read_text(encoding="utf-8")
        self.assertIn('api("/runs"', javascript)
        self.assertIn('/snapshot`', javascript)
        self.assertIn('/events`', javascript)
        self.assertIn('/artifacts`', javascript)
        self.assertIn("REFRESH_INTERVAL_MS", javascript)
        self.assertIn("refreshLoop(context)", javascript)
        self.assertIn("snapshotArtifactFingerprint", javascript)
        self.assertIn("source.artifactFingerprint", javascript)
        self.assertIn("artifactRefreshDecision", javascript)
        self.assertIn("eventChangesArtifact", javascript)
        self.assertIn('"filechange"', javascript)
        self.assertIn("snapshotResult.result?.artifactChanged", javascript)
        self.assertIn("consumed.artifactChanged", javascript)
        self.assertIn("refreshArtifactsAfterSnapshotChange(context)", javascript)
        self.assertIn("queueArtifactRefresh(context)", javascript)
        self.assertIn("loadSnapshotWithStatus", javascript)
        self.assertIn("refreshArtifactsWithStatus", javascript)
        self.assertIn('setLoadMessage("snapshot"', javascript)
        self.assertIn('setLoadMessage(\n    "runWarning"', javascript)
        self.assertIn('setLoadMessage(\n    "snapshotWarning"', javascript)
        self.assertIn('setLoadMessage("artifact"', javascript)
        self.assertIn('setLoadMessage("artifactWarning"', javascript)
        self.assertIn("state.unseen += result.added + result.updated", javascript)
        self.assertNotIn("Promise.allSettled", javascript)
        self.assertIn("after: state.cursor", javascript)
        self.assertIn("waitMs: 25000", javascript)
        self.assertIn("new AbortController()", javascript)
        self.assertIn("state.generation", javascript)
        self.assertIn("function showNoRuns()", javascript)
        self.assertIn('setConnection("error", "実行なし")', javascript)
        self.assertGreaterEqual(javascript.count("showNoRuns();"), 2)
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
              const runA = ui.runOptionModel({
                runId: "question-maintenance-20260727-aaaaaaaaaaaa",
                status: "running",
                updatedAt: "2026-07-27T01:02:03Z",
              });
              const runB = ui.runOptionModel({
                runId: "question-maintenance-20260727-bbbbbbbbbbbb",
                status: "running",
                updatedAt: "2026-07-27T01:02:03Z",
              });
              assert.notStrictEqual(runA.label, runB.label);
              assert(runA.label.includes("実行中"));
              assert(runA.label.includes("aaaaaaaaaaaa"));
              assert(runB.label.includes("bbbbbbbbbbbb"));
              assert(runA.title.includes("question-maintenance-20260727-aaaaaaaaaaaa"));

              const deepLinked = ui.runListSelection([
                { runId: "newest-run" },
              ], "old-run-outside-first-100");
              assert.strictEqual(deepLinked.selected, "old-run-outside-first-100");
              assert.strictEqual(deepLinked.requestedMissing, true);
              const defaultRun = ui.runListSelection([
                { runId: "newest-run" },
              ], "");
              assert.strictEqual(defaultRun.selected, "newest-run");
              assert.strictEqual(defaultRun.requestedMissing, false);

              const fingerprintBase = {
                artifactFingerprint: "sha256:artifact-a",
                run: {
                  runId: "run-a",
                  status: "running",
                  updatedAt: "2026-07-27T01:00:00Z",
                },
                executionState: { status: "running", phase: "candidate:03" },
                lanes: [{
                  runId: "child-1",
                  parentRunId: "run-a",
                  status: "running",
                  updatedAt: "2026-07-27T01:00:00Z",
                }],
                artifactState: { receiptValidation: { status: "pending" } },
                observationHealth: { status: "healthy", eventCount: 0 },
              };
              const baseFingerprint = ui.snapshotArtifactFingerprint(fingerprintBase);
              assert.strictEqual(
                ui.artifactReconcileRequired({
                  artifactFingerprintComplete: false,
                }, 1000, 15999),
                false,
              );
              assert.strictEqual(
                ui.artifactReconcileRequired({
                  artifactFingerprintComplete: false,
                }, 1000, 16000),
                true,
              );
              assert.strictEqual(
                ui.artifactReconcileRequired({
                  artifactFingerprintComplete: true,
                }, 0, 999999),
                false,
              );
              let loadedArtifactFingerprint = "";
              let artifactApiCalls = 0;
              const applySnapshot = (snapshot) => {
                const decision = ui.artifactRefreshDecision(
                  snapshot,
                  loadedArtifactFingerprint,
                );
                if (decision.artifactChanged) {
                  artifactApiCalls += 1;
                  loadedArtifactFingerprint = decision.fingerprint;
                }
                return decision;
              };
              const initialDecision = applySnapshot(fingerprintBase);
              assert.strictEqual(initialDecision.artifactChanged, true);
              assert.strictEqual(initialDecision.fingerprint, baseFingerprint);
              assert.strictEqual(artifactApiCalls, 1);
              assert.strictEqual(
                baseFingerprint,
                ui.snapshotArtifactFingerprint({
                  ...fingerprintBase,
                  observationHealth: { status: "degraded", eventCount: 99 },
                }),
              );
              assert.strictEqual(
                baseFingerprint,
                ui.snapshotArtifactFingerprint({
                  ...fingerprintBase,
                  run: {
                    ...fingerprintBase.run,
                    status: "succeeded",
                    updatedAt: "2026-07-27T01:00:01Z",
                  },
                  executionState: { status: "completed", phase: "candidate:04" },
                }),
              );
              assert.strictEqual(
                baseFingerprint,
                ui.snapshotArtifactFingerprint({
                  ...fingerprintBase,
                  lanes: [{
                    ...fingerprintBase.lanes[0],
                    status: "succeeded",
                    startedAt: "2026-07-27T00:59:59Z",
                    finishedAt: "2026-07-27T01:00:01Z",
                    updatedAt: "2026-07-27T01:00:01Z",
                  }],
                }),
              );
              assert.strictEqual(
                applySnapshot({
                  ...fingerprintBase,
                  artifactState: { receiptValidation: { status: "validated" } },
                }).artifactChanged,
                false,
              );
              assert.strictEqual(
                applySnapshot({
                  ...fingerprintBase,
                  lanes: [{
                    ...fingerprintBase.lanes[0],
                    status: "succeeded",
                    finishedAt: "2026-07-27T01:00:01Z",
                  }],
                }).artifactChanged,
                false,
              );
              assert.strictEqual(artifactApiCalls, 1);
              assert.strictEqual(
                applySnapshot({
                  ...fingerprintBase,
                  artifactFingerprint: "sha256:artifact-b",
                }).artifactChanged,
                true,
              );
              assert.strictEqual(artifactApiCalls, 2);

              const snapshotIssues = ui.snapshotResponseIssues({
                truncated: true,
                warnings: [
                  "child_manifest_limit",
                  "child_manifest_unavailable",
                  "child_manifest_bytes_limit",
                  "child_manifest_identity_mismatch",
                  "snapshot_response_bytes_limit",
                ],
              });
              assert.strictEqual(snapshotIssues.truncated, true);
              assert(snapshotIssues.message.includes("表示上限"));
              assert(snapshotIssues.message.includes("取得できません"));
              assert(snapshotIssues.message.includes("読取上限"));
              assert(snapshotIssues.message.includes("identity不一致"));
              assert(snapshotIssues.message.includes("応答上限"));
              assert.strictEqual(
                ui.snapshotResponseIssues({ truncated: false, warnings: [] }).message,
                "",
              );

              const fallbackBase = {
                run: {
                  runId: "run-a",
                  status: "running",
                  updatedAt: "2026-07-27T01:00:00Z",
                  artifactRevision: 7,
                  receiptValidated: false,
                  artifactSync: { status: "pending" },
                  result: {
                    status: "succeeded",
                    changedFiles: ["output/demo/result.json"],
                  },
                  batchQuestionResults: [{
                    questionId: "q-1",
                    status: "succeeded",
                    changedFiles: ["output/demo/result.json"],
                  }],
                },
                lanes: [{
                  runId: "child-1",
                  status: "running",
                  updatedAt: "2026-07-27T01:00:00Z",
                  artifactState: { receiptValidation: { status: "pending" } },
                }],
                artifactDeclarations: [{
                  path: "output/demo/result.json",
                  revision: 7,
                  contentHash: "sha256:content-a",
                  updatedAt: "2026-07-27T01:00:00Z",
                }],
              };
              const fallbackFingerprint =
                ui.snapshotArtifactFingerprint(fallbackBase);
              assert.strictEqual(
                fallbackFingerprint,
                ui.snapshotArtifactFingerprint({
                  ...fallbackBase,
                  run: {
                    ...fallbackBase.run,
                    status: "succeeded",
                    updatedAt: "2026-07-27T01:00:02Z",
                  },
                  lanes: [{
                    ...fallbackBase.lanes[0],
                    status: "succeeded",
                    startedAt: "2026-07-27T00:59:59Z",
                    finishedAt: "2026-07-27T01:00:02Z",
                    updatedAt: "2026-07-27T01:00:02Z",
                  }],
                }),
              );
              for (const changedDeclaration of [
                { ...fallbackBase.artifactDeclarations[0], revision: 8 },
                {
                  ...fallbackBase.artifactDeclarations[0],
                  contentHash: "sha256:content-b",
                },
                {
                  ...fallbackBase.artifactDeclarations[0],
                  updatedAt: "2026-07-27T01:00:02Z",
                },
                {
                  ...fallbackBase.artifactDeclarations[0],
                  path: "output/demo/result-v2.json",
                },
              ]) {
                assert.notStrictEqual(
                  fallbackFingerprint,
                  ui.snapshotArtifactFingerprint({
                    ...fallbackBase,
                    artifactDeclarations: [changedDeclaration],
                  }),
                );
              }

              const artifactIssues = ui.artifactResponseIssues({
                artifacts: [],
                rejected: [{
                  path: "<response-limit>",
                  contentState: { status: "rejected" },
                  reasonCode: "response_bytes_limit",
                }],
                truncated: true,
              });
              assert.strictEqual(artifactIssues.rejectedCount, 1);
              assert.strictEqual(artifactIssues.truncated, true);
              assert(artifactIssues.message.includes("拒否 1件"));
              assert(artifactIssues.message.includes("一部省略"));
              assert.strictEqual(ui.artifactResponseIssues({ artifacts: [] }).message, "");

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
              const firstIngest = ui.ingestEvents({ events: [{
                eventId: "server:1",
                serverInstanceId: "server",
                observedAt: 1,
                type: "agentMessage",
                correlation: { itemId: "item-1", threadId: "thread-1" },
                payload: { delta: "公", secret: "must-not-render" },
              }]});
              const deltaUpdate = ui.ingestEvents({ events: [{
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
              assert.strictEqual(firstIngest.added, 1);
              assert.strictEqual(deltaUpdate.updated, 1);
              assert.strictEqual(ui.state.observation.eventCount, 3);

              const timedEvent = ui.normalizedEvent({
                eventId: "server:timed",
                type: "turnState",
                observedAt: "2026-07-27T01:00:02Z",
                occurredAt: "2026-07-27T01:00:01Z",
                payload: { state: "started" },
              });
              assert.strictEqual(timedEvent.observedAt, "2026-07-27T01:00:02Z");
              assert.strictEqual(timedEvent.occurredAt, "2026-07-27T01:00:01Z");
              const timedLane = ui.buildLanes({ run: {} }, [{
                ...timedEvent,
                correlation: {
                  childRunId: "timed-child",
                  threadId: "timed-thread",
                  stageId: "03",
                },
              }]);
              assert.strictEqual(timedLane[0].startedAt, "2026-07-27T01:00:01Z");

              const artifactIngest = ui.ingestEvents({ events: [{
                eventId: "server:artifact",
                serverInstanceId: "server",
                observedAt: 4,
                type: "artifactSaved",
                correlation: { childRunId: "child-1", threadId: "thread-1" },
                payload: { state: "saved" },
              }]});
              assert.strictEqual(artifactIngest.artifactChanged, true);
              const fileChangeStarted = ui.normalizedEvent({
                eventId: "server:file-started",
                type: "toolState",
                payload: { toolType: "fileChange", state: "started" },
              });
              const fileChangeCompleted = ui.normalizedEvent({
                eventId: "server:file-completed",
                type: "toolState",
                payload: { toolType: "fileChange", state: "completed" },
              });
              assert.strictEqual(ui.eventChangesArtifact(fileChangeStarted), false);
              assert.strictEqual(ui.eventChangesArtifact(fileChangeCompleted), true);
              const fileChangeIngest = ui.ingestEvents({ events: [{
                eventId: "server:file-ingest",
                serverInstanceId: "server",
                observedAt: 5,
                type: "toolState",
                payload: { toolType: "fileChange", state: "changed" },
              }]});
              assert.strictEqual(fileChangeIngest.artifactChanged, true);

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

              const scopeGapEvent = ui.normalizedEvent({
                eventId: "server:scope-gap",
                type: "observationGap",
                payload: {
                  droppedNotifications: 17,
                  totalDroppedNotifications: 20,
                  scopeTruncated: true,
                  private: "hidden",
                },
              });
              assert.strictEqual(scopeGapEvent.payload.scopeTruncated, true);
              assert(ui.eventText(scopeGapEvent).includes("対象scopeを特定できない"));
              assert.strictEqual("private" in scopeGapEvent.payload, false);

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
              ui.applyObservationHealth({
                observationHealth: {
                  status: "healthy",
                  gap: false,
                  stale: false,
                  eventCount: 0,
                },
              }, true);
              assert.strictEqual(ui.state.observation.eventCount, 0);
              const waitingObservation = ui.observationDisplay(
                { health: "healthy", gap: false, stale: false, eventCount: 0 },
                "running",
              );
              assert(waitingObservation.text.includes("観測イベント待ち"));
              assert.strictEqual(waitingObservation.text.includes("観測live"), false);
              const historicalObservation = ui.observationDisplay(
                { health: "healthy", gap: false, stale: false, eventCount: 0 },
                "succeeded",
                "2026-07-27T01:00:00Z",
              );
              assert(historicalObservation.text.includes("観測イベントなし"));
              assert.strictEqual(historicalObservation.text.includes("観測live"), false);
              const liveObservation = ui.observationDisplay(
                { health: "healthy", gap: false, stale: false, eventCount: 1 },
                "running",
              );
              assert(liveObservation.text.includes("観測live"));
              const degradedWithoutEvents = ui.observationDisplay(
                { health: "degraded", gap: false, stale: false, eventCount: 0 },
                "running",
              );
              assert(degradedWithoutEvents.text.includes("観測状態 degraded"));
              assert.strictEqual(
                degradedWithoutEvents.text.includes("観測イベント待ち"),
                true,
              );

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
              const parentSyncFailure = ui.artifactRecord({
                artifactId: "parent-failed",
                path: "output/result.json",
                contentState: { status: "saved" },
                artifactSync: { status: "unknown", parentStatus: "failed" },
              }, true, {});
              assert.strictEqual(parentSyncFailure.syncStatus, "failed");

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

              const parentAggregate = ui.buildLanes({
                run: {
                  runId: "parent-only",
                  stageCode: "03",
                  stageLabel: "内容評価",
                  status: "running",
                  startedAt: "2026-07-27T00:00:00Z",
                },
              }, []);
              assert.strictEqual(parentAggregate.length, 0);
              const selectedChildLane = ui.buildLanes({
                run: {
                  runId: "child-selected",
                  parentRunId: "parent-only",
                  stageCode: "03",
                  status: "running",
                  startedAt: "2026-07-27T00:00:00Z",
                },
              }, []);
              assert.strictEqual(selectedChildLane.length, 1);
              assert.strictEqual(selectedChildLane[0].childRunId, "child-selected");

              const earlierCompleted = Array.from({ length: 50 }, (_, index) => ({
                stage: "先行wave",
                childRunId: `finished-${index}`,
                status: "succeeded",
                startedAt: `2026-07-27T00:00:${String(index % 60).padStart(2, "0")}Z`,
              }));
              const laterRunning = {
                stage: "後続wave",
                childRunId: "running-later",
                status: "running",
                startedAt: "2026-07-27T01:00:00Z",
              };
              const visible = ui.selectVisibleLanes(
                [...earlierCompleted, laterRunning],
                4,
                3,
              );
              assert.strictEqual(visible.length, 4);
              assert(visible.some((lane) => lane.childRunId === "running-later"));
              const sameStageVisible = ui.selectVisibleLanes(
                [...earlierCompleted.slice(0, 5), {
                  stage: "先行wave",
                  childRunId: "running-same-stage",
                  status: "running",
                  startedAt: "2026-07-27T01:01:00Z",
                }],
                2,
                2,
              );
              assert(sameStageVisible.some(
                (lane) => lane.childRunId === "running-same-stage",
              ));

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
                occurredAt: "2026-07-27T00:00:01Z",
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
              assert.strictEqual(
                mergedLanes[0].startedAt,
                "2026-07-27T00:00:00Z",
              );
              const workItemMerged = ui.buildLanes({
                run: { runId: "parent", status: "running" },
                lanes: [{
                  stageCode: "03",
                  questionId: "q-v2",
                  workItemKey: "work-v2",
                  status: "preparing",
                }],
              }, [{
                rawType: "turnState",
                observedAt: 2,
                occurredAt: "2026-07-27T00:00:02Z",
                correlation: {
                  childRunId: "child-v2",
                  threadId: "thread-v2",
                  questionId: "q-v2",
                  workItemKey: "work-v2",
                  stageId: "03",
                },
                payload: { state: "inProgress" },
              }]);
              assert.strictEqual(workItemMerged.length, 1);
              assert.strictEqual(workItemMerged[0].childRunId, "child-v2");
              assert.strictEqual(workItemMerged[0].threadId, "thread-v2");
              assert.strictEqual(workItemMerged[0].workItemKey, "work-v2");
              const retryLanes = ui.buildLanes({
                run: { runId: "parent", status: "running" },
                lanes: [{
                  childRunId: "attempt-current",
                  stageCode: "03",
                  questionId: "q-v2",
                  workItemKey: "work-v2",
                  status: "preparing",
                }],
              }, [{
                rawType: "turnState",
                observedAt: 3,
                occurredAt: "2026-07-27T00:00:03Z",
                correlation: {
                  childRunId: "attempt-previous",
                  threadId: "thread-previous",
                  questionId: "q-v2",
                  workItemKey: "work-v2",
                  stageId: "03",
                },
                payload: { state: "failed" },
              }]);
              assert.strictEqual(retryLanes.length, 2);
              assert(retryLanes.some(
                (lane) => lane.childRunId === "attempt-current",
              ));
              assert(retryLanes.some(
                (lane) => lane.childRunId === "attempt-previous",
              ));
              const terminalLane = ui.buildLanes({
                run: { runId: "parent", status: "completed" },
                lanes: [{
                  runId: "child-terminal",
                  parentRunId: "parent",
                  stageCode: "03",
                  status: "completed",
                  startedAt: "2026-07-27T00:00:00Z",
                  finishedAt: "2026-07-27T00:01:00Z",
                }],
              }, [{
                rawType: "turnState",
                category: "turn",
                observedAt: "2026-07-27T00:00:30Z",
                correlation: {
                  childRunId: "child-terminal",
                  threadId: "thread-terminal",
                  stageId: "03",
                },
                payload: { state: "inProgress" },
              }]);
              assert.strictEqual(terminalLane[0].status, "completed");
              assert.strictEqual(
                terminalLane[0].finishedAt,
                "2026-07-27T00:01:00Z",
              );
              const succeededWithoutFinishedAt = ui.buildLanes({
                run: { runId: "parent", status: "succeeded" },
                lanes: [{
                  runId: "child-succeeded",
                  parentRunId: "parent",
                  stageCode: "03",
                  status: "succeeded",
                }],
              }, [{
                rawType: "turnState",
                category: "turn",
                observedAt: "2026-07-27T00:00:30Z",
                correlation: {
                  childRunId: "child-succeeded",
                  threadId: "thread-succeeded",
                  stageId: "03",
                },
                payload: { state: "inProgress" },
              }]);
              assert.strictEqual(
                succeededWithoutFinishedAt[0].status,
                "succeeded",
              );
              const failedAuthority = ui.buildLanes({
                run: { runId: "parent", status: "failed" },
                lanes: [{
                  runId: "child-failed",
                  parentRunId: "parent",
                  stageCode: "03",
                  status: "failed",
                  finishedAt: "2026-07-27T02:00:00Z",
                }],
              }, [{
                rawType: "turnState",
                category: "turn",
                occurredAt: "2026-07-27T01:00:00Z",
                correlation: {
                  childRunId: "child-failed",
                  threadId: "thread-failed",
                  stageId: "03",
                },
                payload: { state: "completed" },
              }]);
              assert.strictEqual(failedAuthority[0].status, "failed");
              assert.strictEqual(
                failedAuthority[0].finishedAt,
                "2026-07-27T02:00:00Z",
              );
              const completedAuthority = ui.buildLanes({
                run: { runId: "parent", status: "completed" },
                lanes: [{
                  runId: "child-completed",
                  parentRunId: "parent",
                  stageCode: "03",
                  status: "completed",
                  finishedAt: "2026-07-27T02:00:00Z",
                }],
              }, [{
                rawType: "error",
                category: "error",
                occurredAt: "2026-07-27T01:00:00Z",
                correlation: {
                  childRunId: "child-completed",
                  threadId: "thread-completed",
                  stageId: "03",
                },
                payload: { message: "old", willRetry: false },
              }]);
              assert.strictEqual(completedAuthority[0].status, "completed");
              const toolDoesNotFinishLane = ui.buildLanes({
                run: { runId: "parent", status: "running" },
                lanes: [{
                  runId: "child-tool",
                  parentRunId: "parent",
                  stageCode: "03",
                  status: "running",
                  startedAt: "2026-07-27T00:00:00Z",
                }],
              }, [{
                rawType: "toolState",
                category: "tool",
                observedAt: "2026-07-27T00:00:30Z",
                correlation: {
                  childRunId: "child-tool",
                  threadId: "thread-tool",
                  stageId: "03",
                },
                payload: { state: "failed", toolType: "commandExecution" },
              }]);
              assert.strictEqual(toolDoesNotFinishLane[0].status, "running");
              assert.strictEqual(toolDoesNotFinishLane[0].finishedAt, null);

              ui.state.events = [];
              ui.state.eventIndex.clear();
              ui.state.seenEventIds.clear();
              ui.state.seenEventOrder = [];
              ui.state.observation.eventCount = 0;
              ui.ingestEvents({
                events: Array.from({ length: 1200 }, (_, index) => ({
                  eventId: `bounded:${index}`,
                  serverInstanceId: "bounded",
                  observedAt: index + 1,
                  type: "threadState",
                  correlation: { threadId: `thread-${index}` },
                  payload: { state: "active" },
                })),
              });
              assert.strictEqual(ui.state.events.length, 500);
              assert(ui.state.seenEventIds.size <= 1000);
              assert.strictEqual(ui.state.observation.eventCount, 1200);
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
