"use strict";

const $ = (id) => document.getElementById(id);
const search = new URLSearchParams(location.search);
const state = {
  qualification: search.get("qualification") || "",
  runId: search.get("runId") || "",
  cursor: "",
  seenEventIds: new Set(),
  seenEventOrder: [],
  eventIndex: new Map(),
  events: [],
  artifacts: [],
  snapshot: null,
  snapshotFingerprint: "",
  artifactFingerprint: "",
  lastArtifactRefreshAt: 0,
  following: true,
  unseen: 0,
  failures: 0,
  generation: 0,
  controller: null,
  artifactRefreshPromise: null,
  artifactRefreshQueued: false,
  loadMessages: {
    snapshot: "",
    runWarning: "",
    snapshotWarning: "",
    artifact: "",
    artifactWarning: "",
  },
  observation: {
    health: "unknown", gap: false, stale: false, lastObservedAt: null,
    eventCount: null, detail: "",
  },
};

const EVENT_LABELS = {
  message: "AGENT発言",
  summary: "公開推論サマリー",
  plan: "PLAN",
  tool: "TOOL",
  artifact: "成果物保存",
  state: "状態",
  error: "ERROR",
};
const TOKEN_FIELDS = [
  "inputTokens", "cachedInputTokens", "cacheWriteInputTokens",
  "outputTokens", "reasoningOutputTokens", "totalTokens",
];
const CORRELATION_FIELDS = [
  "qualification", "parentRunId", "runId", "childRunId", "listGroupId",
  "questionId", "workItemKey", "stageId", "stageCode", "threadId", "turnId", "itemId",
  "batchId", "batchKey", "batchIndex", "batchNumber", "batchSequence",
  "sourceQuestionKey", "sourceRecordRef", "reviewQuestionId",
];
const MAX_EVENT_ITEMS = 500;
const MAX_DEDUPE_EVENT_IDS = MAX_EVENT_ITEMS * 2;
const MAX_VISIBLE_LANES = 48;
const MAX_STAGE_LANES = 12;
const REFRESH_INTERVAL_MS = 4000;
const ARTIFACT_RECONCILE_INTERVAL_MS = 15000;
const STALE_AFTER_MS = 120000;

function node(tag, className, text) {
  const value = document.createElement(tag);
  if (className) value.className = className;
  if (text !== undefined && text !== null) value.textContent = String(text);
  return value;
}

function first(...values) {
  return values.find((value) => value !== undefined && value !== null && value !== "");
}

function string(...values) {
  const value = first(...values);
  return value === undefined || value === null ? "" : String(value);
}

function array(value) {
  if (Array.isArray(value)) return value;
  if (Array.isArray(value?.items)) return value.items;
  return [];
}

function object(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function bool(value) {
  return typeof value === "boolean" ? value : undefined;
}

function dateValue(value) {
  if (!value) return null;
  const date = new Date(typeof value === "number" && value < 10_000_000_000 ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function timestamp(value) {
  const date = dateValue(value);
  return date
    ? new Intl.DateTimeFormat("ja-JP", {
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
    }).format(date)
    : value ? String(value) : "—";
}

function shortTime(value) {
  const date = dateValue(value);
  return date
    ? new Intl.DateTimeFormat("ja-JP", {
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    }).format(date)
    : value ? String(value) : "—";
}

function statusClass(value) {
  const status = String(value || "").toLowerCase();
  if ([
    "running", "active", "in_progress", "inprogress", "working", "started",
    "preparing", "prepared", "committing", "validating",
  ].includes(status)) return "running";
  if (["complete", "completed", "succeeded", "success", "done", "validated"].includes(status)) return "completed";
  if (["failed", "error", "blocked", "cancelled", "interrupted"].includes(status)) return "failed";
  return "neutral";
}

function statusLabel(value) {
  return {
    running: "実行中", active: "実行中", in_progress: "実行中", working: "実行中",
    inprogress: "実行中", started: "実行中", preparing: "準備中",
    prepared: "準備済み", committing: "保存中", validating: "検証中",
    complete: "完了", completed: "完了", succeeded: "完了", success: "完了",
    done: "完了", validated: "検証済み",
    failed: "失敗", error: "エラー", blocked: "保留", cancelled: "中止",
    interrupted: "中断", queued: "待機中", pending: "待機中",
  }[String(value || "").toLowerCase()] || String(value || "確認中");
}

function runOptionModel(run) {
  const source = object(run);
  const id = string(source.runId, source.id);
  const execution = object(source.executionState);
  const status = statusLabel(first(execution.status, source.status, source.state));
  const date = first(
    source.updatedAt,
    source.startedAt,
    source.createdAt,
    source.finishedAt,
  );
  const suffix = id.length > 12 ? `…${id.slice(-12)}` : id || "—";
  const descriptor = string(source.title, source.name, source.label);
  const label = [
    status,
    date ? timestamp(date) : "日時 —",
    `ID ${suffix}`,
    descriptor,
  ].filter(Boolean).join(" · ");
  return {
    id,
    label,
    title: `stable runId: ${id || "—"}${descriptor ? `\n${descriptor}` : ""}`,
  };
}

function canonicalValue(value) {
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (!value || typeof value !== "object") {
    return ["string", "number", "boolean"].includes(typeof value) || value === null
      ? value
      : null;
  }
  return Object.keys(value).sort().reduce((result, key) => {
    result[key] = canonicalValue(value[key]);
    return result;
  }, {});
}

function pick(source, fields) {
  const value = object(source);
  return fields.reduce((result, field) => {
    if (value[field] !== undefined) result[field] = canonicalValue(value[field]);
    return result;
  }, {});
}

function hasFields(value) {
  return Object.keys(object(value)).length > 0;
}

function artifactFields(source) {
  const value = object(source);
  return Object.keys(value).sort().reduce((result, key) => {
    const normalized = key.toLowerCase();
    if (
      normalized.includes("artifact")
      || normalized === "changedfiles"
      || normalized === "receiptvalidated"
      || normalized.endsWith("hash")
      || normalized.endsWith("hashes")
    ) {
      result[key] = canonicalValue(value[key]);
    }
    return result;
  }, {});
}

function artifactFallbackRecord(source) {
  const value = object(source);
  const result = artifactFields(value);
  const resultState = object(value.result);
  const resultArtifacts = artifactFields(resultState);
  if (hasFields(resultArtifacts)) {
    result.result = {
      ...pick(resultState, ["status", "state"]),
      ...resultArtifacts,
    };
  }
  const batchResults = array(value.batchQuestionResults).flatMap((item) => {
    const artifactMetadata = artifactFields(item);
    if (!hasFields(artifactMetadata)) return [];
    return [{
      ...pick(item, [
        "questionId", "workItemKey", "sourceQuestionKey", "sourceRecordRef",
        "reviewQuestionId", "batchId", "batchKey", "batchIndex",
        "batchNumber", "batchSequence", "status", "state",
      ]),
      ...artifactMetadata,
    }];
  });
  if (batchResults.length) {
    result.batchQuestionResults = batchResults.sort(
      (left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)),
    );
  }
  return result;
}

function snapshotArtifactFingerprint(snapshot) {
  const source = object(snapshot);
  if (
    source.artifactFingerprint !== undefined
    && source.artifactFingerprint !== null
    && source.artifactFingerprint !== ""
  ) {
    return typeof source.artifactFingerprint === "string"
      ? source.artifactFingerprint
      : JSON.stringify(canonicalValue(source.artifactFingerprint));
  }
  const run = object(source.run || source);
  const lanes = array(source.lanes)
    .flatMap((lane) => {
      const artifactMetadata = artifactFallbackRecord(lane);
      if (!hasFields(artifactMetadata)) return [];
      return [{
        identity: pick(lane, [
          "runId", "parentRunId", "childRunId", "questionId", "workItemKey",
          "stageCode", "batchId", "batchKey", "batchIndex", "batchNumber",
          "batchSequence",
        ]),
        ...artifactMetadata,
      }];
    })
    .sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)));
  const shape = {
    snapshot: artifactFallbackRecord(source),
    run: artifactFallbackRecord(run),
    lanes,
  };
  return JSON.stringify(canonicalValue(shape));
}

function artifactRefreshDecision(snapshot, loadedFingerprint) {
  const fingerprint = snapshotArtifactFingerprint(snapshot);
  return {
    fingerprint,
    artifactChanged: fingerprint !== String(loadedFingerprint || ""),
  };
}

function artifactReconcileRequired(
  snapshot,
  lastArtifactRefreshAt,
  now = Date.now(),
) {
  return object(snapshot).artifactFingerprintComplete === false
    && now - Number(lastArtifactRefreshAt || 0)
      >= ARTIFACT_RECONCILE_INTERVAL_MS;
}

function artifactResponseIssues(payload) {
  const source = object(payload);
  const rejected = array(source.rejected);
  const reasonCodes = [...new Set(rejected.map((item) => string(
    object(item).reasonCode,
    object(object(item).contentState).status,
    "unknown",
  )))].filter(Boolean);
  const pagination = object(source.pagination);
  const rawState = string(object(source.contentState).status).toLowerCase();
  const truncated = source.truncated === true
    || source.isTruncated === true
    || source.partial === true
    || source.hasMore === true
    || pagination.truncated === true
    || pagination.hasMore === true
    || rawState === "truncated"
    || reasonCodes.some((code) => code.includes("limit") || code.includes("truncat"));
  const parts = [];
  if (rejected.length) {
    parts.push(`拒否 ${rejected.length}件${reasonCodes.length ? `（${reasonCodes.join(", ")}）` : ""}`);
  }
  if (truncated) parts.push("上限により一部省略");
  return {
    rejectedCount: rejected.length,
    reasonCodes,
    truncated,
    message: parts.length ? `成果物API: ${parts.join(" · ")}` : "",
  };
}

function snapshotResponseIssues(payload) {
  const source = object(payload);
  const warnings = array(source.warnings)
    .filter((value) => typeof value === "string" && value)
    .slice(0, 64);
  const labels = {
    child_manifest_limit: "表示上限により一部のlaneを省略",
    child_manifest_unavailable: "一部のlane manifestを取得できません",
    child_manifest_bytes_limit: "読取上限により一部のlaneを省略",
    child_manifest_identity_mismatch: "identity不一致のlaneを除外",
    child_manifest_schema_invalid: "child一覧の形式不正によりlaneを表示できません",
    child_manifest_id_invalid: "不正なchild IDをlaneから除外",
    v2_question_summary_bytes_limit: "読取上限により質問summaryを表示できません",
    v2_question_summary_unavailable: "質問summaryを取得できません",
    v2_question_summary_schema_invalid: "質問summaryの形式不正を検出",
    v2_question_summary_identity_mismatch: "質問summaryのidentity不一致を検出",
    v2_question_state_limit: "表示上限により一部の質問状態を省略",
    v2_question_state_bytes_limit: "読取上限により一部の質問状態を省略",
    v2_question_state_unavailable: "一部の質問状態を取得できません",
    v2_question_state_schema_invalid: "形式不正の質問状態を除外",
    v2_question_state_identity_mismatch: "identity不一致の質問状態を除外",
    v2_question_state_hash_invalid: "hash不一致の質問状態を除外",
    v2_plan_unavailable: "immutable planを取得できません",
    v2_plan_bytes_limit: "読取上限によりimmutable planを検証できません",
    v2_plan_identity_mismatch: "immutable planのidentity不一致を検出",
    v2_plan_hash_invalid: "immutable planのhash不一致を検出",
    v2_output_fingerprint_invalid: "不正な成果物fingerprintを検出",
    v2_output_fingerprint_missing: "旧実行の成果物fingerprint欠落を検出",
    v2_terminal_run_has_nonterminal_lanes: "終了済み実行に未確定のlane状態が残っています",
    v2_attempt_unavailable: "一部のattemptを取得できません",
    v2_attempt_identity_mismatch: "identity不一致のattemptを除外",
    v2_active_attempt_mismatch: "active attemptと質問状態の不一致を検出",
    v2_attempt_stage_mismatch: "attemptとstageの不一致を検出",
    v2_attempt_output_mismatch: "attemptと成果物fingerprintの不一致を検出",
    v2_attempt_result_attribution_mismatch: "成果物の問題帰属不一致を検出",
    v2_attempt_projection_limit: "表示上限により一部のattemptを省略",
    v2_attempt_receipt_invalid: "確定条件を満たさないattempt receiptを除外",
    v2_attempt_receipt_unavailable: "一部のattempt receiptを取得できません",
    v2_attempt_receipt_mismatch: "内容不一致のattempt receiptを除外",
    v2_attempt_receipt_bytes_limit: "読取上限により一部のattempt receiptを省略",
    v2_lane_limit: "表示上限により一部の質問laneを省略",
    v2_lane_unavailable: "質問laneを取得できません",
    snapshot_response_bytes_limit: "応答上限により古いlaneを省略",
  };
  const details = [...new Set(warnings.map(
    (code) => labels[code] || `一部のlaneを表示できません（${code.slice(0, 100)}）`,
  ))];
  if (source.truncated === true && !details.length) {
    details.push("一部のlaneを省略");
  }
  return {
    warnings,
    truncated: source.truncated === true,
    message: details.length ? `snapshot: ${details.join(" · ")}` : "",
  };
}

function runListSelection(runs, requestedRunId) {
  const items = array(runs);
  const requested = String(requestedRunId || "");
  const listed = requested && items.some(
    (run) => String(first(run?.runId, run?.id)) === requested,
  );
  return {
    selected: requested || String(first(items[0]?.runId, items[0]?.id, "")),
    requestedMissing: Boolean(requested && !listed),
  };
}

function isAbort(error) {
  return error?.name === "AbortError";
}

async function requestJson(path, signal) {
  const response = await fetch(path, {
    method: "GET",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function api(path, parameters = {}) {
  const query = new URLSearchParams();
  Object.entries(parameters).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") query.set(key, String(value));
  });
  const encoded = query.toString();
  return `/api/monitor/v1${path}${encoded ? `?${encoded}` : ""}`;
}

function beginGeneration(qualification, runId) {
  if (state.controller) state.controller.abort();
  state.generation += 1;
  state.controller = new AbortController();
  state.qualification = String(qualification || "");
  state.runId = String(runId || "");
  return {
    generation: state.generation,
    qualification: state.qualification,
    runId: state.runId,
    signal: state.controller.signal,
  };
}

function isCurrent(context) {
  return Boolean(
    context
    && !context.signal.aborted
    && context.generation === state.generation
    && context.qualification === state.qualification
    && context.runId === state.runId
  );
}

function delay(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    }, { once: true });
  });
}

function eventCategory(rawType) {
  const raw = String(rawType || "state").toLowerCase();
  if (raw.includes("error") || raw.includes("fail")) return "error";
  if (raw.includes("artifact") || raw.includes("save")) return "artifact";
  if (raw.includes("reason") || raw.includes("summary")) return "summary";
  if (raw.includes("plan")) return "plan";
  if (raw.includes("tool") || raw.includes("command") || raw.includes("tokenusage")) return "tool";
  if (raw.includes("message") || raw.includes("agent")) return "message";
  return "state";
}

function allowCorrelation(value) {
  const source = object(value);
  const result = {};
  CORRELATION_FIELDS.forEach((field) => {
    const item = source[field];
    if (["string", "number"].includes(typeof item) && String(item)) result[field] = String(item);
  });
  return result;
}

function allowEventPayload(event, category) {
  const payload = object(event.payload);
  const result = {};
  if (category === "message" || category === "summary") {
    for (const key of ["text", "delta", "summary"]) {
      if (typeof payload[key] === "string") result[key] = payload[key];
    }
    if (Array.isArray(payload.summaryParts)) {
      result.summaryParts = payload.summaryParts
        .filter((value) => typeof value === "string")
        .slice(0, 200);
    }
    if (Number.isInteger(payload.summaryIndex) && payload.summaryIndex >= 0) {
      result.summaryIndex = payload.summaryIndex;
    }
    if (typeof payload.phase === "string") result.phase = payload.phase;
    if (typeof payload.state === "string") result.state = payload.state;
  } else if (category === "plan") {
    for (const key of ["text", "delta", "explanation", "state"]) {
      if (typeof payload[key] === "string") result[key] = payload[key];
    }
    if (Array.isArray(payload.plan)) {
      result.plan = payload.plan.slice(0, 200).flatMap((value) => {
        const item = object(value);
        if (typeof item.step !== "string" || typeof item.status !== "string") return [];
        return [{ step: item.step, status: item.status }];
      });
    }
  } else if (category === "tool") {
    if (typeof payload.toolType === "string") result.toolType = payload.toolType;
    if (typeof payload.state === "string") result.state = payload.state;
    const usage = object(payload.usage);
    result.usage = {};
    for (const section of ["last", "total"]) {
      const source = object(usage[section]);
      const publicSection = {};
      TOKEN_FIELDS.forEach((key) => {
        if (Number.isInteger(source[key]) && source[key] >= 0) publicSection[key] = source[key];
      });
      if (Object.keys(publicSection).length) result.usage[section] = publicSection;
    }
    TOKEN_FIELDS.forEach((key) => {
      if (Number.isInteger(usage[key]) && usage[key] >= 0) result.usage[key] = usage[key];
    });
    if (Number.isInteger(usage.modelContextWindow) && usage.modelContextWindow >= 0) {
      result.usage.modelContextWindow = usage.modelContextWindow;
    }
    if (!Object.keys(result.usage).length) delete result.usage;
  } else if (category === "error") {
    if (typeof payload.message === "string") result.message = payload.message;
    if (typeof payload.state === "string") result.state = payload.state;
    if (typeof payload.willRetry === "boolean") result.willRetry = payload.willRetry;
  } else {
    for (const key of ["state", "status", "title", "name", "detail"]) {
      if (typeof payload[key] === "string") result[key] = payload[key];
    }
    for (const key of [
      "fromSequence", "toSequence", "droppedNotifications",
      "totalDroppedNotifications",
    ]) {
      if (Number.isInteger(payload[key]) && payload[key] >= 0) result[key] = payload[key];
    }
    if (payload.scopeTruncated === true) result.scopeTruncated = true;
  }
  return result;
}

function normalizedEvent(event, index = 0) {
  const source = object(event);
  const rawType = String(first(source.type, source.kind, source.eventType, "state"));
  const category = eventCategory(rawType);
  const correlation = allowCorrelation(source.correlation);
  const payload = allowEventPayload(source, category);
  const eventId = String(first(
    source.eventId,
    source.id,
    source.cursor,
    `${first(source.serverInstanceId, "unknown")}:${first(source.sequence, index)}`,
  ));
  const observedAt = first(source.observedAt, source.timestamp, source.createdAt, source.updatedAt);
  const occurredAt = first(source.occurredAt, source.eventTime);
  const itemId = correlation.itemId;
  const server = String(first(source.serverInstanceId, eventId.split(":")[0], "unknown"));
  const itemKey = itemId
    ? `${server}:${category}:${correlation.threadId || ""}:${correlation.turnId || ""}:${itemId}`
    : eventId;
  return {
    eventId,
    itemKey,
    rawType,
    category,
    correlation,
    payload,
    observedAt,
    occurredAt,
  };
}

function eventText(event) {
  const payload = event.payload;
  if (event.category === "tool" && payload.usage) {
    const values = [];
    for (const section of ["last", "total"]) {
      const usage = object(payload.usage[section]);
      const text = Object.entries(usage).map(([key, value]) => `${key}: ${value}`).join(" · ");
      if (text) values.push(`${section} ${text}`);
    }
    const flat = Object.entries(payload.usage)
      .filter(([, value]) => Number.isInteger(value))
      .map(([key, value]) => `${key}: ${value}`)
      .join(" · ");
    if (flat) values.push(flat);
    return values.join("\n");
  }
  if (event.category === "summary" && array(payload.summaryParts).length) {
    return payload.summaryParts.join("\n");
  }
  if (event.category === "plan" && array(payload.plan).length) {
    const plan = payload.plan
      .map((item) => `${string(item.status)} · ${string(item.step)}`)
      .join("\n");
    return [
      string(payload.text, payload.delta, payload.explanation),
      plan,
    ].filter(Boolean).join("\n");
  }
  if (event.category === "error") {
    const suffix = payload.willRetry ? "\n再試行予定" : "";
    return `${string(payload.message, payload.state)}${suffix}`;
  }
  if (event.rawType === "observationGap" && payload.droppedNotifications !== undefined) {
    const scope = payload.scopeTruncated ? "（対象scopeを特定できない欠落を含む）" : "";
    return `観測できなかったnotification: ${payload.droppedNotifications}件${scope}`;
  }
  return string(
    payload.text,
    payload.delta,
    payload.summary,
    payload.detail,
    payload.state,
    payload.status,
  );
}

function mergeItemEvent(previous, incoming) {
  const merged = { ...previous, ...incoming, payload: { ...previous.payload, ...incoming.payload } };
  const previousText = string(previous.displayText, eventText(previous));
  const incomingText = eventText(incoming);
  if (incoming.payload.text !== undefined || incoming.payload.summary !== undefined) {
    merged.displayText = incomingText;
  } else if (incoming.payload.delta !== undefined) {
    merged.displayText = incomingText.startsWith(previousText)
      ? incomingText
      : `${previousText}${incomingText}`;
  } else {
    merged.displayText = incomingText || previousText;
  }
  return merged;
}

function eventChangesArtifact(event) {
  if (event.category === "artifact") return true;
  if (
    event.category !== "tool"
    || string(event.payload?.toolType).toLowerCase() !== "filechange"
  ) {
    return false;
  }
  const lifecycle = string(
    event.payload?.state,
    event.payload?.status,
    event.rawType,
  ).toLowerCase();
  return ["completed", "complete", "changed", "succeeded", "success", "saved"]
    .some((value) => lifecycle === value || lifecycle.endsWith(`/${value}`));
}

function ingestEvents(payload) {
  const incoming = array(first(payload?.events, payload?.items, payload));
  let added = 0;
  let updated = 0;
  let artifactChanged = false;
  let lastObservedAt = null;
  incoming.forEach((rawEvent, index) => {
    const event = normalizedEvent(rawEvent, index);
    if (state.seenEventIds.has(event.eventId)) return;
    state.seenEventIds.add(event.eventId);
    state.seenEventOrder.push(event.eventId);
    const previousIndex = state.eventIndex.get(event.itemKey);
    if (previousIndex !== undefined) {
      state.events[previousIndex] = mergeItemEvent(state.events[previousIndex], event);
      updated += 1;
    } else {
      event.displayText = eventText(event);
      state.eventIndex.set(event.itemKey, state.events.length);
      state.events.push(event);
      added += 1;
    }
    if (eventChangesArtifact(event)) artifactChanged = true;
    if (String(event.rawType).toLowerCase() === "observationgap") {
      state.observation.gap = true;
      state.observation.health = "gap";
      state.observation.detail = "観測できなかった区間があります";
    }
    if (event.observedAt) {
      lastObservedAt = event.observedAt;
      state.observation.lastObservedAt = event.observedAt;
      const observed = dateValue(event.observedAt);
      if (observed && Date.now() - observed.getTime() <= STALE_AFTER_MS) {
        state.observation.stale = false;
      }
    }
  });
  if (state.events.length > MAX_EVENT_ITEMS) {
    state.events.splice(0, state.events.length - MAX_EVENT_ITEMS);
    state.eventIndex.clear();
    state.events.forEach((event, index) => state.eventIndex.set(event.itemKey, index));
  }
  if (state.seenEventIds.size > MAX_DEDUPE_EVENT_IDS) {
    const retained = new Set(state.events.map((event) => event.eventId));
    const recent = state.seenEventOrder.slice(-MAX_EVENT_ITEMS);
    recent.forEach((eventId) => retained.add(eventId));
    state.seenEventIds = retained;
    state.seenEventOrder = [...retained];
  }
  state.cursor = string(payload?.nextCursor, payload?.cursor, state.cursor);
  if (added || updated) {
    state.observation.eventCount = (
      Number.isInteger(state.observation.eventCount)
        ? state.observation.eventCount
        : 0
    ) + added + updated;
  }
  return { added, updated, artifactChanged, lastObservedAt };
}

function observationPayload(payload) {
  return object(first(payload?.observationHealth, payload?.observation, payload?.health));
}

function applyObservationHealth(payload, authoritative = false) {
  const health = observationPayload(payload);
  const rawStatus = string(health.status, payload?.observationStatus).toLowerCase();
  const gap = bool(first(health.gap, health.hasGap, payload?.gap, payload?.cursorGap, payload?.resetRequired));
  const stale = bool(first(health.stale, payload?.stale));
  const eventCount = first(health.eventCount, payload?.eventCount);
  const observedAt = first(
    payload?.observedAt,
    health.observedAt,
    health.lastObservedAt,
    health.lastEventAt,
  );
  if (observedAt) state.observation.lastObservedAt = observedAt;
  if (Number.isInteger(eventCount) && eventCount >= 0) {
    state.observation.eventCount = eventCount;
  }
  if (gap === true || rawStatus === "gap" || Number(health.gapCount) > 0) {
    state.observation.gap = true;
  }
  if (stale === true || rawStatus === "stale") state.observation.stale = true;
  if (rawStatus && rawStatus !== "healthy" && rawStatus !== "live") {
    state.observation.health = rawStatus;
  }
  if (authoritative && ["healthy", "live"].includes(rawStatus)) {
    if (gap === false || payload?.gapResolved === true || payload?.continuityRestored === true) {
      state.observation.gap = false;
    }
    if (stale === false) state.observation.stale = false;
    state.observation.health = rawStatus;
    state.observation.detail = "";
  }
  return { ...state.observation };
}

function artifactIdentity(item, fallbackKind, index) {
  const explicit = first(item.artifactId, item.id);
  if (explicit !== undefined) return String(explicit);
  const path = string(item.path, item.relativePath);
  const identity = allowCorrelation(item.identity);
  const scope = [
    identity.childRunId,
    identity.questionId,
    identity.workItemKey,
    identity.batchId,
    identity.batchKey,
    identity.batchIndex,
    identity.batchNumber,
    identity.batchSequence,
    identity.sourceQuestionKey,
    identity.sourceRecordRef,
  ].filter((value) => value !== undefined && value !== null && value !== "");
  if (path && scope.length) return `${path}::${scope.join("|")}`;
  return String(first(
    path,
    item.name && `${first(item.stage, item.stageCode, "")}:${item.name}`,
    `${fallbackKind}:${index}`,
  ));
}

function artifactStateRecord(artifactState, identity, item) {
  const source = object(artifactState);
  const candidates = [
    ...array(source.artifacts),
    ...array(source.items),
    ...array(source.files),
    ...Object.values(object(source.byId)),
    ...Object.values(object(source.byPath)),
    source[identity],
  ].filter((value) => value && typeof value === "object");
  return object(candidates.find((candidate) => {
    const id = artifactIdentity(candidate, "", -1);
    return id === identity
      || (item.path && first(candidate.path, candidate.relativePath) === item.path)
      || (item.artifactId && first(candidate.artifactId, candidate.id) === item.artifactId);
  }));
}

function artifactRecord(item, fallbackSaved, artifactState, index = 0) {
  const source = object(item);
  const identity = allowCorrelation(source.identity);
  const id = artifactIdentity(source, fallbackSaved ? "saved" : "draft", index);
  const perArtifact = artifactStateRecord(artifactState, id, source);
  const contentState = object(source.contentState);
  const receiptValidation = object(source.receiptValidation);
  const artifactSync = object(source.artifactSync);
  const syncStates = [
    string(artifactSync.status).toLowerCase(),
    string(artifactSync.parentStatus).toLowerCase(),
  ].filter(Boolean);
  const syncStatus = syncStates.includes("failed")
    ? "failed"
    : first(...syncStates, "");
  const rawStatus = string(
    contentState.status,
    perArtifact.status,
    source.status,
    source.state,
  ).toLowerCase();
  const explicitSaved = first(
    bool(contentState.saved),
    bool(perArtifact.saved),
    bool(source.saved),
  );
  const explicitValidated = first(
    bool(receiptValidation.validated),
    bool(perArtifact.validated), bool(perArtifact.verified),
    bool(source.validated), bool(source.verified),
  );
  const saved = explicitSaved !== undefined
    ? explicitSaved
    : ["saved", "validated", "verified", "synced"].includes(rawStatus) || fallbackSaved;
  const validated = explicitValidated !== undefined
    ? explicitValidated
    : ["validated", "verified"].includes(rawStatus);
  const content = first(source.content, source.text, source.preview, source.body, source.output, "");
  const path = string(source.path, source.relativePath);
  const scopeLabel = [
    identity.questionId && `問題 ${identity.questionId}`,
    first(
      identity.batchId,
      identity.batchKey,
      identity.batchIndex,
      identity.batchNumber,
      identity.batchSequence,
    ),
  ].filter((value) => value !== undefined && value !== null && value !== "")
    .join(" · ") || first(identity.childRunId, identity.workItemKey, "") || "";
  return {
    id,
    identity,
    scopeLabel: String(scopeLabel),
    name: String(first(source.name, source.fileName, source.title, path.split("/").pop(), saved ? "artifact" : "出力")),
    path,
    stage: String(first(source.stageLabel, source.stage, source.stageCode, identity.stageCode, source.phase, "—")),
    savedAt: first(source.savedAt, perArtifact.savedAt, source.updatedAt, source.timestamp, source.createdAt),
    observedAt: first(source.observedAt, perArtifact.observedAt),
    saved,
    validated,
    validationStatus: String(first(
      perArtifact.validationStatus,
      source.validationStatus,
      receiptValidation.status,
      validated ? "validated" : saved ? "not_validated" : "not_saved",
    )),
    syncStatus: String(syncStatus),
    content: typeof content === "string" ? content : "",
  };
}

function normalizeArtifacts(payload) {
  const artifactState = object(payload?.artifactState);
  const savedSources = array(first(payload?.artifacts, payload?.savedArtifacts, payload?.saved, payload?.items));
  const draftSources = array(first(payload?.drafts, payload?.outputs, payload?.unsavedOutputs, payload?.unsaved));
  const merged = new Map();
  savedSources.forEach((item, index) => {
    const record = artifactRecord(item, true, artifactState, index);
    merged.set(record.id, record);
  });
  draftSources.forEach((item, index) => {
    const record = artifactRecord(item, false, artifactState, index);
    const previous = merged.get(record.id);
    if (!previous || (!previous.saved && record.saved)) merged.set(record.id, { ...previous, ...record });
  });
  return [...merged.values()];
}

function deepLink(snapshot, preferred = {}) {
  const run = object(snapshot?.run);
  const identities = object(snapshot?.identities);
  const qualification = string(
    preferred.qualification, snapshot?.qualification, run.qualification, state.qualification, "",
  );
  const listGroupId = string(
    preferred.listGroupId,
    run.listGroupId,
    run.scopeListGroupId,
    array(run.scopeListGroupIds)[0],
    array(run.targetGroupIds)[0],
    array(identities.listGroupId)[0],
  );
  const questionId = string(
    preferred.questionId,
    run.questionId,
    array(run.questionIds)[0],
    array(identities.questionId)[0],
  );
  const params = new URLSearchParams();
  if (qualification) params.set("qualification", qualification);
  if (listGroupId) params.set("listGroupId", listGroupId);
  if (questionId) {
    params.set("view", "questions");
    params.set("questionId", questionId);
  }
  const encoded = params.toString();
  return `/${encoded ? `?${encoded}` : ""}`;
}

function seedTime(value, names) {
  return first(...names.map((name) => value[name]));
}

function buildLanes(snapshot, events = state.events) {
  const run = object(snapshot?.run || snapshot);
  const lanes = new Map();
  const stageByChild = new Map();

  function upsert(raw, inheritedStage = "", runtimeUpdate = false) {
    const value = object(raw);
    const stage = String(first(
      value.stageLabel, value.stageCode, value.stageId, value.stage, inheritedStage, run.stageLabel, run.stageCode, "run",
    ));
    const childRunId = string(
      value.childRunId,
      value.parentRunId ? value.runId : "",
      array(value.childRunIds)[0],
    );
    const threadId = string(value.threadId);
    const questionId = string(value.questionId, value.id && value.listGroupId ? value.id : "");
    const workItemKey = string(value.workItemKey);
    if (!childRunId && !threadId && !workItemKey) return;
    let key = `${stage}|${childRunId}|${threadId}|${workItemKey}`;
    if ((childRunId || workItemKey) && !lanes.has(key)) {
      const existingEntry = [...lanes.entries()].find(([, lane]) => (
        lane.stage === stage
        && (
          (childRunId && lane.childRunId === childRunId)
          || (
            workItemKey
            && lane.workItemKey === workItemKey
            && (!childRunId || !lane.childRunId)
          )
        )
      ));
      if (existingEntry) {
        const [existingKey, existing] = existingEntry;
        lanes.delete(existingKey);
        existing.childRunId = existing.childRunId || childRunId;
        existing.threadId = existing.threadId || threadId;
        existing.workItemKey = existing.workItemKey || workItemKey;
        key = `${stage}|${existing.childRunId}|${existing.threadId}|${existing.workItemKey}`;
        existing.key = key;
        lanes.set(key, existing);
      }
    }
    const current = lanes.get(key) || {
      key, stage, childRunId, threadId, workItemKey, questionIds: new Set(),
      status: "pending", startedAt: null, finishedAt: null,
    };
    if (questionId) current.questionIds.add(questionId);
    const status = first(value.status, value.state);
    const currentStatusClass = statusClass(current.status);
    const currentTerminal = Boolean(current.finishedAt)
      || currentStatusClass === "completed"
      || currentStatusClass === "failed";
    if (status && !(runtimeUpdate && currentTerminal)) {
      current.status = String(status);
    }
    const startedAt = seedTime(value, ["startedAt", "startAt", "actualStartedAt"]);
    const finishedAt = seedTime(value, ["finishedAt", "completedAt", "endedAt", "actualFinishedAt"]);
    if (startedAt && (!current.startedAt || dateValue(startedAt) < dateValue(current.startedAt))) current.startedAt = startedAt;
    if (
      finishedAt
      && !(runtimeUpdate && currentTerminal)
      && (!current.finishedAt || dateValue(finishedAt) > dateValue(current.finishedAt))
    ) current.finishedAt = finishedAt;
    lanes.set(key, current);
    if (childRunId) stageByChild.set(childRunId, stage);
  }

  function visit(raw, inheritedStage = "", includeSelf = true) {
    if (!raw || typeof raw !== "object") return;
    const value = object(raw);
    const stage = string(value.stageLabel, value.stageCode, value.stageId, value.stage, inheritedStage);
    if (includeSelf) upsert(value, inheritedStage);
    [
      "lanes", "phaseExecutions", "stageExecutions", "questionExecutions",
      "validationAttempts", "attempts", "children",
    ].forEach((key) => array(value[key]).forEach((child) => visit(child, stage, true)));
    const stages = object(value.stages);
    Object.entries(stages).forEach(([key, child]) => visit(
      child,
      first(object(child).stageCode, key, stage),
      true,
    ));
  }

  visit(run, "", Boolean(run.parentRunId || run.childRunId || run.threadId));
  array(snapshot?.lanes).forEach((lane) => visit(lane));

  events.forEach((event) => {
    const correlation = event.correlation;
    if (!correlation.childRunId && !correlation.threadId && !correlation.workItemKey) return;
    const rawType = event.rawType.toLowerCase();
    const lifecycle = string(
      event.payload.state,
      event.payload.status,
    ).toLowerCase();
    const turnLifecycle = rawType === "turnstate";
    const terminalError = (
      event.category === "error"
      && event.payload.willRetry === false
    );
    if (!turnLifecycle && !terminalError) return;
    const stage = String(first(
      stageByChild.get(correlation.childRunId),
      correlation.stageCode,
      correlation.stageId,
      run.stageLabel,
      run.stageCode,
      "runtime",
    ));
    const seed = {
      stage,
      childRunId: correlation.childRunId,
      threadId: correlation.threadId,
      questionId: correlation.questionId,
      workItemKey: correlation.workItemKey,
    };
    if (
      turnLifecycle
      && ["started", "inprogress"].includes(lifecycle)
    ) {
      seed.startedAt = first(event.occurredAt, event.observedAt);
      seed.status = "running";
    }
    if (
      terminalError
      || (
        turnLifecycle
        && ["completed", "failed", "interrupted"].includes(lifecycle)
      )
    ) {
      seed.finishedAt = first(event.occurredAt, event.observedAt);
      seed.status = terminalError || lifecycle === "failed"
        ? "failed"
        : lifecycle === "interrupted"
          ? "interrupted"
          : "completed";
    }
    upsert(seed, stage, true);
  });

  return [...lanes.values()].filter(
    (lane) => lane.startedAt || lane.finishedAt || lane.childRunId || lane.threadId || lane.workItemKey,
  );
}

function selectVisibleLanes(
  lanes,
  maxTotal = MAX_VISIBLE_LANES,
  maxPerStage = MAX_STAGE_LANES,
) {
  const values = array(lanes).map((lane, index) => ({ lane, index }));
  const stageOrder = new Map();
  values.forEach(({ lane }, index) => {
    if (!stageOrder.has(lane.stage)) stageOrder.set(lane.stage, index);
  });
  const byPriority = [...values].sort((left, right) => {
    const leftRunning = statusClass(left.lane.status) === "running" ? 1 : 0;
    const rightRunning = statusClass(right.lane.status) === "running" ? 1 : 0;
    if (leftRunning !== rightRunning) return rightRunning - leftRunning;
    const leftTime = dateValue(left.lane.startedAt)?.getTime() || -Infinity;
    const rightTime = dateValue(right.lane.startedAt)?.getTime() || -Infinity;
    if (leftTime !== rightTime) return rightTime - leftTime;
    return left.index - right.index;
  });
  const stageCounts = new Map();
  const selected = [];
  for (const item of byPriority) {
    if (selected.length >= Math.max(0, maxTotal)) break;
    const stage = item.lane.stage;
    const count = stageCounts.get(stage) || 0;
    if (count >= Math.max(0, maxPerStage)) continue;
    stageCounts.set(stage, count + 1);
    selected.push(item);
  }
  return selected.sort((left, right) => {
    const stageDifference = stageOrder.get(left.lane.stage) - stageOrder.get(right.lane.stage);
    if (stageDifference) return stageDifference;
    const leftRunning = statusClass(left.lane.status) === "running" ? 1 : 0;
    const rightRunning = statusClass(right.lane.status) === "running" ? 1 : 0;
    if (leftRunning !== rightRunning) return rightRunning - leftRunning;
    const leftTime = dateValue(left.lane.startedAt)?.getTime() || Infinity;
    const rightTime = dateValue(right.lane.startedAt)?.getTime() || Infinity;
    if (leftTime !== rightTime) return leftTime - rightTime;
    return left.index - right.index;
  }).map(({ lane }) => lane);
}

function observationDisplay(observation, executionStatus, finishedAt = null, now = Date.now()) {
  const source = object(observation);
  const last = dateValue(source.lastObservedAt);
  const running = statusClass(executionStatus) === "running";
  const terminal = ["completed", "failed"].includes(statusClass(executionStatus)) || Boolean(finishedAt);
  const stale = Boolean(source.stale || (
    last && now - last.getTime() > STALE_AFTER_MS && running
  ));
  let primary = "観測状態を確認中";
  if (source.gap) {
    primary = "観測gapあり · snapshotで連続性を再確認中";
  } else if (stale) {
    primary = `観測stale · 最終観測 ${timestamp(source.lastObservedAt)}`;
  } else if (
    source.health
    && !["unknown", "healthy", "live"].includes(source.health)
  ) {
    primary = `観測状態 ${source.health}`;
  } else if (["healthy", "live"].includes(source.health)) {
    primary = `観測live${source.lastObservedAt ? ` · ${timestamp(source.lastObservedAt)}` : ""}`;
  }
  const zero = source.eventCount === 0
    ? terminal
      ? "観測イベントなし · 終了済み又は過去の実行"
      : "観測イベント待ち · 実行中のeventを待っています"
    : "";
  if (zero && ["観測状態を確認中", "観測live"].some((value) => primary.startsWith(value))) {
    primary = zero;
  } else if (zero) {
    primary = `${primary} · ${zero}`;
  }
  return { text: `${primary}（実行状態とは別）`, stale };
}

function renderObservation() {
  const observation = state.observation;
  const executionStatus = first(state.snapshot?.executionState?.status, state.snapshot?.run?.status);
  const finishedAt = first(
    state.snapshot?.executionState?.finishedAt,
    state.snapshot?.run?.finishedAt,
  );
  const display = observationDisplay(observation, executionStatus, finishedAt);
  observation.stale = display.stale;
  $("freshness-status").textContent = display.text;
}

function renderRun(snapshot) {
  const run = object(snapshot?.run || snapshot);
  const execution = object(snapshot?.executionState);
  const rawStatus = first(execution.status, run.status, run.state, "pending");
  const chip = $("run-status");
  chip.className = `status-chip ${statusClass(rawStatus)}`;
  chip.textContent = statusLabel(rawStatus);
  $("run-summary").textContent = first(
    run.summary,
    first(execution.phase, run.executionPhase, run.currentStage)
      && `${first(execution.phase, run.executionPhase, run.currentStage)} · ${first(run.progress, "処理状況を取得中")}`,
    "選択した実行の状態を監視しています。",
  );
  $("maintenance-link").href = deepLink(snapshot);
  renderLanes(buildLanes(snapshot));
  renderObservation();
}

function renderLanes(lanes) {
  const list = $("lane-list");
  list.replaceChildren();
  $("lane-empty").hidden = lanes.length > 0;
  if (!lanes.length) return;
  const parentStatus = first(
    state.snapshot?.executionState?.status,
    state.snapshot?.run?.status,
  );
  const parentTerminal = ["completed", "failed"].includes(statusClass(parentStatus));
  const visibleLanes = selectVisibleLanes(lanes);
  const starts = lanes.map((lane) => dateValue(lane.startedAt)?.getTime()).filter(Number.isFinite);
  const ends = lanes.map((lane) => dateValue(lane.finishedAt)?.getTime()).filter(Number.isFinite);
  const activeNow = !parentTerminal && lanes.some((lane) => lane.startedAt && !lane.finishedAt);
  const min = Math.min(...starts);
  const max = Math.max(...ends, ...starts, activeNow ? Date.now() : -Infinity);
  const span = Math.max(1, max - min);
  const stageTotals = new Map();
  lanes.forEach((lane) => {
    stageTotals.set(lane.stage, (stageTotals.get(lane.stage) || 0) + 1);
  });
  const grouped = new Map();
  visibleLanes.forEach((lane) => {
    if (!grouped.has(lane.stage)) grouped.set(lane.stage, []);
    grouped.get(lane.stage).push(lane);
  });
  for (const [stage, stageLanes] of grouped) {
    const group = node("li", "lane-stage-group");
    const head = node("div", "lane-stage-head");
    const stageTotal = stageTotals.get(stage) || stageLanes.length;
    head.append(node("strong", "", stage), node("span", "", `${stageTotal} lanes`));
    group.append(head);
    const cluster = node("ol", "lane-cluster");
    stageLanes.forEach((lane, index) => {
      const rawLaneClass = statusClass(lane.status);
      const historicalActive = parentTerminal && rawLaneClass === "running";
      const item = node(
        "li",
        `lane-item ${historicalActive ? "neutral" : rawLaneClass}`,
      );
      item.append(node("span", "lane-node", String(index + 1).padStart(2, "0")));
      const copy = node("div", "lane-copy");
      copy.append(node("strong", "", first(lane.childRunId, lane.threadId, "stable ID不明")));
      const identities = [
        lane.threadId && `thread ${lane.threadId}`,
        lane.questionIds.size && `${lane.questionIds.size}問`,
      ].filter(Boolean).join(" · ");
      copy.append(node("p", "", identities || "stable IDを確認中"));
      const meta = node("div", "lane-meta");
      meta.append(node(
        "span",
        "",
        `${statusLabel(lane.status)}${historicalActive ? "（実行終了時）" : ""}`,
      ));
      if (lane.startedAt) meta.append(node("time", "", shortTime(lane.startedAt)));
      if (lane.finishedAt) meta.append(node("time", "", `→ ${shortTime(lane.finishedAt)}`));
      copy.append(meta);
      if (lane.startedAt) {
        const start = dateValue(lane.startedAt)?.getTime() || min;
        const finish = dateValue(lane.finishedAt)?.getTime() || max;
        const track = node("div", "lane-track");
        const bar = node("span", "lane-bar");
        bar.style.left = `${Math.max(0, Math.min(100, ((start - min) / span) * 100))}%`;
        bar.style.width = `${Math.max(1, Math.min(100, ((finish - start) / span) * 100))}%`;
        track.append(bar);
        copy.append(track);
      }
      item.append(copy);
      cluster.append(item);
    });
    group.append(cluster);
    if (stageTotal > stageLanes.length) {
      group.append(node("div", "lane-more", `ほか ${stageTotal - stageLanes.length} lanes（集約表示）`));
    }
    list.append(group);
  }
  if (lanes.length > visibleLanes.length) {
    list.append(node("li", "lane-more", `全${lanes.length} lanes中 ${visibleLanes.length} lanesを表示。実行中を優先し、残りは工程別件数へ集約しています。`));
  }
}

function renderArtifacts(records, preserveSelection = true) {
  const selectedId = preserveSelection ? document.querySelector(".artifact-button.active")?.dataset.id : "";
  state.artifacts = records;
  const saved = records.filter((item) => item.saved);
  const draft = records.filter((item) => !item.saved);
  $("saved-count").textContent = String(saved.length);
  $("draft-count").textContent = String(draft.length);
  renderArtifactGroup($("saved-artifacts"), saved, selectedId);
  renderArtifactGroup($("draft-artifacts"), draft, selectedId);
  const selected = records.find((item) => item.id === selectedId);
  if (selected) showArtifact(selected);
  else if (saved[0] || draft[0]) showArtifact(saved[0] || draft[0]);
  else clearArtifact();
}

function artifactValidationLabel(artifact) {
  const status = string(artifact.validationStatus).toLowerCase();
  if (status === "failed") return "検証失敗";
  if (artifact.validated || ["validated", "verified"].includes(status)) {
    return "検証済み";
  }
  return artifact.saved ? "未検証" : "未保存";
}

function artifactSyncLabel(artifact) {
  const status = string(artifact.syncStatus).toLowerCase();
  if (!status || status === "unknown") return "";
  if (status === "failed") return "同期失敗";
  return `同期 ${artifact.syncStatus}`;
}

function artifactStateLabel(artifact) {
  return [
    artifactValidationLabel(artifact),
    artifactSyncLabel(artifact),
  ].filter(Boolean).join(" · ");
}

function renderArtifactGroup(container, records, selectedId) {
  container.replaceChildren();
  if (!records.length) {
    container.append(node("div", "empty-copy", "なし"));
    return;
  }
  records.forEach((artifact) => {
    const button = node("button", `artifact-button${artifact.id === selectedId ? " active" : ""}`);
    button.type = "button";
    button.dataset.id = artifact.id;
    button.append(node(
      "strong",
      "",
      artifact.scopeLabel ? `${artifact.scopeLabel} · ${artifact.name}` : artifact.name,
    ));
    button.append(node("span", "", artifact.path || artifact.stage));
    const failed = string(
      artifact.validationStatus,
      artifact.syncStatus,
    ).toLowerCase() === "failed"
      || string(artifact.validationStatus).toLowerCase() === "failed"
      || string(artifact.syncStatus).toLowerCase() === "failed";
    button.append(node(
      "em",
      failed ? "failed" : artifact.validated ? "validated" : "",
      artifactStateLabel(artifact),
    ));
    button.addEventListener("click", () => showArtifact(artifact));
    container.append(button);
  });
}

function clearArtifact() {
  $("artifact-placeholder").hidden = false;
  $("artifact-detail-head").hidden = true;
  $("artifact-content").hidden = true;
  $("artifact-save-state").className = "save-state draft";
  $("artifact-save-state").textContent = "成果物なし";
  $("artifact-updated").textContent = "更新 —";
}

function showArtifact(artifact) {
  document.querySelectorAll(".artifact-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.id === artifact.id);
  });
  $("artifact-placeholder").hidden = true;
  $("artifact-detail-head").hidden = false;
  $("artifact-content").hidden = false;
  $("artifact-kind").textContent = artifact.saved ? "SAVED ARTIFACT" : "UNSAVED OUTPUT";
  $("artifact-name").textContent = artifact.scopeLabel
    ? `${artifact.scopeLabel} · ${artifact.name}`
    : artifact.name;
  $("artifact-path").textContent = artifact.path || "保存前のためpathはありません";
  $("artifact-stage").textContent = artifact.stage;
  $("artifact-saved-at").textContent = artifact.saved ? timestamp(artifact.savedAt) : "未保存";
  $("artifact-validation").textContent = artifactStateLabel(artifact);
  $("artifact-content").textContent = artifact.content || "内容プレビューはありません。";
  $("artifact-save-state").className = `save-state ${artifact.saved ? "saved" : "draft"}`;
  $("artifact-save-state").textContent = artifact.saved
    ? `保存済み · ${artifactStateLabel(artifact)}`
    : "保存前出力";
  $("artifact-updated").textContent = `更新 ${timestamp(first(artifact.observedAt, artifact.savedAt))}`;
  if (state.snapshot) $("maintenance-link").href = deepLink(state.snapshot, artifact.identity);
}

function eventTitle(event) {
  return first(
    event.payload.title,
    event.payload.name,
    event.payload.toolType,
    event.correlation.questionId && `問題 ${event.correlation.questionId}`,
    EVENT_LABELS[event.category],
  );
}

function renderEvents() {
  const stream = $("event-stream");
  stream.replaceChildren();
  state.events.forEach((event) => {
    const item = node("li", "event-item");
    const top = node("div", "event-top");
    top.append(node("span", `event-label ${event.category}`, EVENT_LABELS[event.category]));
    top.append(node("time", "", shortTime(event.observedAt)));
    item.append(top);
    item.append(node("strong", "", eventTitle(event)));
    if (event.displayText) item.append(node("p", "", event.displayText));
    stream.append(item);
  });
  $("event-count").textContent = String(state.events.length);
  $("event-empty").hidden = state.events.length > 0;
  if (state.following) stream.scrollTop = stream.scrollHeight;
}

function pulseEvent() {
  const dot = $("connection-dot");
  dot.classList.remove("pulse");
  void dot.offsetWidth;
  dot.classList.add("pulse");
}

function consumeEvents(payload) {
  applyObservationHealth(payload, false);
  const result = ingestEvents(payload);
  $("cursor-status").textContent = `cursor ${state.cursor || "—"}`;
  if (result.added || result.updated) {
    pulseEvent();
    const time = first(result.lastObservedAt, state.observation.lastObservedAt);
    if (time) $("last-event-time").textContent = `最終観測 ${timestamp(time)}`;
    if (!state.following) {
      state.unseen += result.added + result.updated;
      $("stream-notice").hidden = false;
      $("stream-notice-text").textContent = `新着 ${state.unseen}件`;
    }
    renderEvents();
    if (state.snapshot) renderLanes(buildLanes(state.snapshot));
  }
  renderObservation();
  return result;
}

function setLoadMessage(kind, message) {
  const ids = {
    snapshot: "snapshot-load-error",
    runWarning: "run-api-warning",
    snapshotWarning: "snapshot-api-warning",
    artifact: "artifact-load-error",
    artifactWarning: "artifact-api-warning",
  };
  state.loadMessages[kind] = String(message || "");
  const target = $(ids[kind]);
  target.textContent = state.loadMessages[kind];
  target.hidden = !state.loadMessages[kind];
  $("monitor-alerts").hidden = !Object.values(state.loadMessages).some(Boolean);
}

function errorMessage(error) {
  return string(error?.message, error, "不明なエラー");
}

async function refreshArtifacts(context) {
  if (!isCurrent(context)) return;
  const requestedFingerprint = state.snapshotFingerprint;
  const payload = await requestJson(api(`/runs/${encodeURIComponent(context.runId)}/artifacts`, {
    qualification: context.qualification,
  }), context.signal);
  if (!isCurrent(context)) return;
  const records = normalizeArtifacts(payload);
  renderArtifacts(records);
  const issues = artifactResponseIssues(payload);
  setLoadMessage("artifactWarning", issues.message);
  state.artifactFingerprint = requestedFingerprint;
  state.lastArtifactRefreshAt = Date.now();
  return { records, issues };
}

async function loadSnapshot(context) {
  if (!isCurrent(context)) return;
  const payload = await requestJson(api(`/runs/${encodeURIComponent(context.runId)}/snapshot`, {
    qualification: context.qualification,
  }), context.signal);
  if (!isCurrent(context)) return;
  const { fingerprint, artifactChanged } = artifactRefreshDecision(
    payload,
    state.artifactFingerprint,
  );
  state.snapshotFingerprint = fingerprint;
  state.snapshot = payload;
  setLoadMessage(
    "snapshotWarning",
    snapshotResponseIssues(payload).message,
  );
  applyObservationHealth(payload, true);
  const snapshotEvents = first(payload.events, payload.recentEvents);
  if (snapshotEvents) consumeEvents({
    events: snapshotEvents,
    cursor: first(payload.cursor, payload.nextCursor, state.cursor),
    observationHealth: payload.observationHealth,
    observedAt: payload.observedAt,
  });
  renderRun(payload);
  return { payload, artifactChanged, fingerprint };
}

async function loadSnapshotWithStatus(context) {
  try {
    const result = await loadSnapshot(context);
    if (isCurrent(context)) setLoadMessage("snapshot", "");
    return { ok: true, result };
  } catch (error) {
    if (isAbort(error) || !isCurrent(context)) return { ok: false, aborted: true };
    setLoadMessage("snapshot", `snapshot取得失敗: ${errorMessage(error)}`);
    return { ok: false, error };
  }
}

async function refreshArtifactsWithStatus(context) {
  try {
    const result = await refreshArtifacts(context);
    if (isCurrent(context)) setLoadMessage("artifact", "");
    return { ok: true, result };
  } catch (error) {
    if (isAbort(error) || !isCurrent(context)) return { ok: false, aborted: true };
    setLoadMessage("artifact", `artifact取得失敗: ${errorMessage(error)}`);
    return { ok: false, error };
  }
}

async function queueArtifactRefresh(context) {
  if (!isCurrent(context)) return { ok: false, aborted: true };
  if (state.artifactRefreshPromise) {
    state.artifactRefreshQueued = true;
    return state.artifactRefreshPromise;
  }
  let task;
  task = (async () => {
    let result = { ok: false };
    do {
      state.artifactRefreshQueued = false;
      result = await refreshArtifactsWithStatus(context);
    } while (isCurrent(context) && state.artifactRefreshQueued);
    return result;
  })();
  state.artifactRefreshPromise = task;
  try {
    return await task;
  } finally {
    if (state.artifactRefreshPromise === task) {
      state.artifactRefreshPromise = null;
      state.artifactRefreshQueued = false;
    }
  }
}

async function refreshArtifactsAfterSnapshotChange(context) {
  const snapshotResult = await loadSnapshotWithStatus(context);
  if (
    !isCurrent(context)
    || !snapshotResult.ok
  ) {
    return snapshotResult;
  }
  const forceReconcile = artifactReconcileRequired(
    snapshotResult.result?.payload,
    state.lastArtifactRefreshAt,
  );
  if (
    !snapshotResult.result?.artifactChanged
    && !forceReconcile
  ) return snapshotResult;
  return queueArtifactRefresh(context);
}

async function loadRuns(qualification, signal) {
  if (!qualification) throw new Error("URLのqualificationを指定してください。");
  const payload = await requestJson(api("/runs", { qualification }), signal);
  const runs = array(first(payload.runs, payload.items, payload));
  setLoadMessage(
    "runWarning",
    payload.truncated === true
      ? "実行一覧は上限により一部省略されています。URLで指定したrunは一覧外でも直接表示します。"
      : "",
  );
  const qualifications = array(first(payload.qualifications, payload.availableQualifications));
  const qualificationSelect = $("qualification-select");
  if (qualifications.length) {
    qualificationSelect.replaceChildren();
    qualifications.forEach((item) => {
      const id = String(first(item.id, item.qualification, item));
      const option = node("option", "", first(item.displayName, item.name, id));
      option.value = id;
      qualificationSelect.append(option);
    });
    qualificationSelect.value = qualification;
  } else {
    const option = node("option", "", qualification);
    option.value = qualification;
    qualificationSelect.replaceChildren(option);
  }
  const select = $("run-select");
  select.replaceChildren();
  const selection = runListSelection(runs, state.runId);
  if (selection.requestedMissing) {
    const model = runOptionModel({
      runId: selection.selected,
      status: "requested",
    });
    const option = node("option", "", `指定run · ${model.label}`);
    option.value = model.id;
    option.title = model.title;
    select.append(option);
  }
  runs.forEach((run) => {
    const model = runOptionModel(run);
    const option = node("option", "", model.label);
    option.value = model.id;
    option.title = model.title;
    select.append(option);
  });
  if (!runs.length && !selection.selected) {
    const option = node("option", "", "実行はありません");
    option.value = "";
    select.append(option);
    return "";
  }
  select.value = selection.selected;
  return selection.selected;
}

function resetRunView() {
  const runWarning = state.loadMessages.runWarning || "";
  state.cursor = "";
  state.seenEventIds.clear();
  state.seenEventOrder = [];
  state.eventIndex.clear();
  state.events = [];
  state.artifacts = [];
  state.snapshot = null;
  state.snapshotFingerprint = "";
  state.artifactFingerprint = "";
  state.lastArtifactRefreshAt = 0;
  state.following = true;
  state.unseen = 0;
  state.failures = 0;
  state.artifactRefreshPromise = null;
  state.artifactRefreshQueued = false;
  state.loadMessages = {
    snapshot: "",
    runWarning,
    snapshotWarning: "",
    artifact: "",
    artifactWarning: "",
  };
  state.observation = {
    health: "unknown", gap: false, stale: false, lastObservedAt: null,
    eventCount: null, detail: "",
  };
  for (const id of [
    "snapshot-load-error",
    "run-api-warning",
    "snapshot-api-warning",
    "artifact-load-error",
    "artifact-api-warning",
  ]) {
    const message = id === "run-api-warning" ? runWarning : "";
    $(id).textContent = message;
    $(id).hidden = !message;
  }
  $("monitor-alerts").hidden = !Object.values(state.loadMessages).some(Boolean);
  $("stream-notice").hidden = true;
  $("last-event-time").textContent = "最終観測 —";
  $("cursor-status").textContent = "cursor —";
  renderEvents();
  renderArtifacts([], false);
  renderLanes([]);
  renderObservation();
}

function setConnection(mode, label) {
  const dot = $("connection-dot");
  dot.classList.toggle("live", mode === "live");
  dot.classList.toggle("error", mode === "error");
  $("connection-label").textContent = label;
}

async function eventLoop(context) {
  while (isCurrent(context)) {
    try {
      const payload = await requestJson(api(`/runs/${encodeURIComponent(context.runId)}/events`, {
        qualification: context.qualification,
        after: state.cursor,
        limit: 100,
        waitMs: 25000,
      }), context.signal);
      if (!isCurrent(context)) return;
      const hasGapEvent = array(payload.events).some(
        (event) => String(event.type || "").toLowerCase() === "observationgap",
      );
      if (payload.gap || payload.cursorGap || payload.resetRequired || hasGapEvent) {
        state.observation.gap = true;
        state.observation.health = "gap";
      }
      const consumed = consumeEvents(payload);
      if (
        consumed.artifactChanged
        || hasGapEvent
        || payload.gap
        || payload.cursorGap
        || payload.resetRequired
      ) {
        refreshArtifactsAfterSnapshotChange(context).catch(showFatal);
      }
      state.failures = 0;
      setConnection("live", "接続中");
    } catch (error) {
      if (isAbort(error) || !isCurrent(context)) return;
      state.failures += 1;
      setConnection("error", "再接続中");
      state.observation.detail = `通信切断 · ${Math.min(30, 2 ** state.failures)}秒後に再接続`;
      renderObservation();
      try {
        await delay(Math.min(30000, 1000 * 2 ** state.failures), context.signal);
      } catch (delayError) {
        if (isAbort(delayError)) return;
        throw delayError;
      }
    }
  }
}

async function refreshLoop(context) {
  while (isCurrent(context)) {
    try {
      await delay(REFRESH_INTERVAL_MS, context.signal);
      if (!isCurrent(context)) return;
      await refreshArtifactsAfterSnapshotChange(context);
    } catch (error) {
      if (isAbort(error) || !isCurrent(context)) return;
      showFatal(error);
    }
  }
}

async function selectRun(runId = $("run-select").value) {
  const context = beginGeneration(state.qualification, runId);
  resetRunView();
  if (!context.runId) return;
  const url = new URL(location.href);
  url.searchParams.set("runId", context.runId);
  url.searchParams.set("qualification", context.qualification);
  history.replaceState(null, "", url);
  setConnection("live", "同期中");
  const snapshotResult = await loadSnapshotWithStatus(context);
  if (!isCurrent(context)) return;
  const artifactResult = await queueArtifactRefresh(context);
  if (!isCurrent(context)) return;
  if (snapshotResult.ok && artifactResult.ok) {
    state.artifactFingerprint = state.snapshotFingerprint;
  }
  if (!snapshotResult.ok && !artifactResult.ok) {
    setConnection("error", "初期取得エラー");
  }
  eventLoop(context).catch(showFatal);
  refreshLoop(context).catch(showFatal);
}

function showNoRuns() {
  setConnection("error", "実行なし");
  $("run-summary").textContent = "監視できる実行がありません。";
}

function activateTab(button, focus = false) {
  document.querySelectorAll("[data-mobile-tab]").forEach((item) => {
    const active = item === button;
    item.classList.toggle("active", active);
    item.setAttribute("aria-selected", String(active));
    item.tabIndex = active ? 0 : -1;
  });
  document.querySelectorAll("[data-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.panel === button.dataset.mobileTab);
  });
  if (focus) button.focus();
}

function bind() {
  $("run-select").addEventListener("change", () => selectRun().catch(showFatal));
  $("qualification-select").addEventListener("change", async () => {
    const qualification = $("qualification-select").value;
    const context = beginGeneration(qualification, "");
    resetRunView();
    try {
      const runId = await loadRuns(qualification, context.signal);
      if (!isCurrent(context)) return;
      if (!runId) {
        showNoRuns();
        return;
      }
      await selectRun(runId);
    } catch (error) {
      if (!isAbort(error)) showFatal(error);
    }
  });
  $("event-stream").addEventListener("scroll", () => {
    const stream = $("event-stream");
    state.following = stream.scrollHeight - stream.scrollTop - stream.clientHeight < 28;
    if (state.following) {
      state.unseen = 0;
      $("stream-notice").hidden = true;
    }
  });
  $("jump-latest").addEventListener("click", () => {
    state.following = true;
    state.unseen = 0;
    $("stream-notice").hidden = true;
    $("event-stream").scrollTop = $("event-stream").scrollHeight;
  });
  const tabs = [...document.querySelectorAll("[data-mobile-tab]")];
  tabs.forEach((button, index) => {
    button.addEventListener("click", () => activateTab(button));
    button.addEventListener("keydown", (event) => {
      let target = null;
      if (event.key === "ArrowRight") target = tabs[(index + 1) % tabs.length];
      if (event.key === "ArrowLeft") target = tabs[(index - 1 + tabs.length) % tabs.length];
      if (event.key === "Home") target = tabs[0];
      if (event.key === "End") target = tabs.at(-1);
      if (!target) return;
      event.preventDefault();
      activateTab(target, true);
    });
  });
}

function showFatal(error) {
  if (isAbort(error)) return;
  setConnection("error", "読込エラー");
  state.observation.health = "unavailable";
  state.observation.detail = `取得できません: ${error.message}`;
  renderObservation();
}

async function start() {
  bind();
  const bootstrap = new AbortController();
  const runId = await loadRuns(state.qualification, bootstrap.signal);
  if (runId) {
    state.runId = runId;
    await selectRun(runId);
  } else {
    showNoRuns();
  }
}

globalThis.MonitorUiTest = {
  state,
  api,
  requestJson,
  beginGeneration,
  isCurrent,
  runOptionModel,
  snapshotArtifactFingerprint,
  artifactRefreshDecision,
  artifactReconcileRequired,
  artifactResponseIssues,
  snapshotResponseIssues,
  runListSelection,
  normalizedEvent,
  eventText,
  eventChangesArtifact,
  ingestEvents,
  applyObservationHealth,
  observationDisplay,
  artifactRecord,
  normalizeArtifacts,
  deepLink,
  buildLanes,
  selectVisibleLanes,
};

if (!globalThis.__MONITOR_TEST__) start().catch(showFatal);
