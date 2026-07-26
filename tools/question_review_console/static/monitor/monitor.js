"use strict";

const $ = (id) => document.getElementById(id);
const search = new URLSearchParams(location.search);
const state = {
  qualification: search.get("qualification") || "",
  runId: search.get("runId") || "",
  cursor: "",
  seenEventIds: new Set(),
  eventIndex: new Map(),
  events: [],
  artifacts: [],
  snapshot: null,
  following: true,
  unseen: 0,
  failures: 0,
  generation: 0,
  controller: null,
  observation: {
    health: "unknown",
    gap: false,
    stale: false,
    lastObservedAt: null,
    detail: "",
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
const MAX_VISIBLE_LANES = 48;
const MAX_STAGE_LANES = 12;
const REFRESH_INTERVAL_MS = 4000;
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
  if (["running", "active", "in_progress", "working", "started", "committing"].includes(status)) return "running";
  if (["complete", "completed", "succeeded", "success", "done", "validated"].includes(status)) return "completed";
  if (["failed", "error", "blocked", "cancelled", "interrupted"].includes(status)) return "failed";
  return "neutral";
}

function statusLabel(value) {
  return {
    running: "実行中", active: "実行中", in_progress: "実行中", working: "実行中",
    started: "実行中", committing: "保存中",
    complete: "完了", completed: "完了", succeeded: "完了", success: "完了",
    done: "完了", validated: "検証済み",
    failed: "失敗", error: "エラー", blocked: "保留", cancelled: "中止",
    interrupted: "中断", queued: "待機中", pending: "待機中",
  }[String(value || "").toLowerCase()] || String(value || "確認中");
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
  const itemId = correlation.itemId;
  const server = String(first(source.serverInstanceId, eventId.split(":")[0], "unknown"));
  const itemKey = itemId
    ? `${server}:${category}:${correlation.threadId || ""}:${correlation.turnId || ""}:${itemId}`
    : eventId;
  return { eventId, itemKey, rawType, category, correlation, payload, observedAt };
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
    return `観測できなかったnotification: ${payload.droppedNotifications}件`;
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

function ingestEvents(payload) {
  const incoming = array(first(payload?.events, payload?.items, payload));
  let added = 0;
  let updated = 0;
  let lastObservedAt = null;
  incoming.forEach((rawEvent, index) => {
    const event = normalizedEvent(rawEvent, index);
    if (state.seenEventIds.has(event.eventId)) return;
    state.seenEventIds.add(event.eventId);
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
  state.cursor = string(payload?.nextCursor, payload?.cursor, state.cursor);
  return { added, updated, lastObservedAt };
}

function observationPayload(payload) {
  return object(first(payload?.observationHealth, payload?.observation, payload?.health));
}

function applyObservationHealth(payload, authoritative = false) {
  const health = observationPayload(payload);
  const rawStatus = string(health.status, payload?.observationStatus).toLowerCase();
  const gap = bool(first(health.gap, health.hasGap, payload?.gap, payload?.cursorGap, payload?.resetRequired));
  const stale = bool(first(health.stale, payload?.stale));
  const observedAt = first(
    payload?.observedAt,
    health.observedAt,
    health.lastObservedAt,
    health.lastEventAt,
  );
  if (observedAt) state.observation.lastObservedAt = observedAt;
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
    .join(" · ") || first(identity.childRunId, identity.workItemKey, "");
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
    syncStatus: string(artifactSync.status),
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

  function upsert(raw, inheritedStage = "") {
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
    if (!childRunId && !threadId && !questionId && stage === "run") return;
    let key = `${stage}|${childRunId}|${threadId}`;
    if (threadId && childRunId && !lanes.has(key)) {
      const childOnlyKey = `${stage}|${childRunId}|`;
      if (lanes.has(childOnlyKey)) {
        const childOnly = lanes.get(childOnlyKey);
        lanes.delete(childOnlyKey);
        childOnly.threadId = threadId;
        key = `${stage}|${childRunId}|${threadId}`;
        lanes.set(key, childOnly);
      }
    }
    const current = lanes.get(key) || {
      key, stage, childRunId, threadId, questionIds: new Set(),
      status: "pending", startedAt: null, finishedAt: null,
    };
    if (questionId) current.questionIds.add(questionId);
    const status = first(value.status, value.state);
    if (status) current.status = String(status);
    const startedAt = seedTime(value, ["startedAt", "startAt", "actualStartedAt"]);
    const finishedAt = seedTime(value, ["finishedAt", "completedAt", "endedAt", "actualFinishedAt"]);
    if (startedAt && (!current.startedAt || dateValue(startedAt) < dateValue(current.startedAt))) current.startedAt = startedAt;
    if (finishedAt && (!current.finishedAt || dateValue(finishedAt) > dateValue(current.finishedAt))) current.finishedAt = finishedAt;
    lanes.set(key, current);
    if (childRunId) stageByChild.set(childRunId, stage);
  }

  function visit(raw, inheritedStage = "") {
    if (!raw || typeof raw !== "object") return;
    const value = object(raw);
    const stage = string(value.stageLabel, value.stageCode, value.stageId, value.stage, inheritedStage);
    upsert(value, inheritedStage);
    [
      "lanes", "phaseExecutions", "stageExecutions", "questionExecutions",
      "validationAttempts", "attempts", "children",
    ].forEach((key) => array(value[key]).forEach((child) => visit(child, stage)));
    const stages = object(value.stages);
    Object.entries(stages).forEach(([key, child]) => visit(child, first(object(child).stageCode, key, stage)));
  }

  visit(run);
  array(snapshot?.lanes).forEach((lane) => visit(lane));

  events.forEach((event) => {
    const correlation = event.correlation;
    if (!correlation.childRunId && !correlation.threadId) return;
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
    };
    const lifecycle = string(event.payload.state, event.payload.status, event.rawType).toLowerCase();
    if (lifecycle.includes("start") || event.rawType.toLowerCase().includes("started")) {
      seed.startedAt = event.observedAt;
      seed.status = "running";
    }
    if (lifecycle.includes("complet") || lifecycle.includes("succeed") || event.rawType.toLowerCase().includes("completed")) {
      seed.finishedAt = event.observedAt;
      seed.status = lifecycle.includes("fail") ? "failed" : "completed";
    }
    if (lifecycle.includes("fail") || lifecycle.includes("error")) seed.status = "failed";
    upsert(seed, stage);
  });

  return [...lanes.values()].filter((lane) => lane.startedAt || lane.finishedAt || lane.childRunId || lane.threadId);
}

function renderObservation() {
  const observation = state.observation;
  const last = dateValue(observation.lastObservedAt);
  const executionStatus = first(state.snapshot?.executionState?.status, state.snapshot?.run?.status);
  const clockStale = Boolean(
    last && Date.now() - last.getTime() > STALE_AFTER_MS && statusClass(executionStatus) === "running",
  );
  if (clockStale) observation.stale = true;
  let text = "観測状態を確認中（実行状態とは別）";
  if (observation.gap) text = "観測gapあり · snapshotで連続性を再確認中（実行状態とは別）";
  else if (observation.stale) text = `観測stale · 最終観測 ${timestamp(observation.lastObservedAt)}（実行状態とは別）`;
  else if (["healthy", "live"].includes(observation.health)) {
    text = `観測live${observation.lastObservedAt ? ` · ${timestamp(observation.lastObservedAt)}` : ""}（実行状態とは別）`;
  } else if (observation.health && observation.health !== "unknown") {
    text = `観測状態 ${observation.health}（実行状態とは別）`;
  }
  $("freshness-status").textContent = text;
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
  const starts = lanes.map((lane) => dateValue(lane.startedAt)?.getTime()).filter(Number.isFinite);
  const ends = lanes.map((lane) => dateValue(lane.finishedAt)?.getTime()).filter(Number.isFinite);
  const activeNow = lanes.some((lane) => lane.startedAt && !lane.finishedAt);
  const min = Math.min(...starts);
  const max = Math.max(...ends, ...starts, activeNow ? Date.now() : -Infinity);
  const span = Math.max(1, max - min);
  const grouped = new Map();
  lanes.forEach((lane) => {
    if (!grouped.has(lane.stage)) grouped.set(lane.stage, []);
    grouped.get(lane.stage).push(lane);
  });
  let visibleTotal = 0;
  for (const [stage, stageLanes] of grouped) {
    if (visibleTotal >= MAX_VISIBLE_LANES) break;
    const group = node("li", "lane-stage-group");
    const head = node("div", "lane-stage-head");
    head.append(node("strong", "", stage), node("span", "", `${stageLanes.length} lanes`));
    group.append(head);
    const cluster = node("ol", "lane-cluster");
    const visible = stageLanes
      .sort((a, b) => (dateValue(a.startedAt)?.getTime() || Infinity) - (dateValue(b.startedAt)?.getTime() || Infinity))
      .slice(0, Math.min(MAX_STAGE_LANES, MAX_VISIBLE_LANES - visibleTotal));
    visible.forEach((lane, index) => {
      const item = node("li", `lane-item ${statusClass(lane.status)}`);
      item.append(node("span", "lane-node", String(index + 1).padStart(2, "0")));
      const copy = node("div", "lane-copy");
      copy.append(node("strong", "", first(lane.childRunId, lane.threadId, `${stage} aggregate`)));
      const identities = [
        lane.threadId && `thread ${lane.threadId}`,
        lane.questionIds.size && `${lane.questionIds.size}問`,
      ].filter(Boolean).join(" · ");
      copy.append(node("p", "", identities || "stable IDを確認中"));
      const meta = node("div", "lane-meta");
      meta.append(node("span", "", statusLabel(lane.status)));
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
    visibleTotal += visible.length;
    group.append(cluster);
    if (stageLanes.length > visible.length) {
      group.append(node("div", "lane-more", `ほか ${stageLanes.length - visible.length} lanes（集約表示）`));
    }
    list.append(group);
  }
  if (lanes.length > visibleTotal) {
    list.append(node("li", "lane-more", `全${lanes.length} lanes中 ${visibleTotal} lanesを表示。残りは工程別件数へ集約しています。`));
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
    button.append(node("em", artifact.validated ? "validated" : "", artifact.validated ? "検証済み" : artifact.saved ? "未検証" : "未保存"));
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
  $("artifact-validation").textContent = artifact.validated ? "検証済み" : artifact.saved ? "未検証" : "保存後に検証";
  $("artifact-content").textContent = artifact.content || "内容プレビューはありません。";
  $("artifact-save-state").className = `save-state ${artifact.saved ? "saved" : "draft"}`;
  $("artifact-save-state").textContent = artifact.saved
    ? artifact.validated ? "保存済み · 検証済み" : "保存済み · 未検証"
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
      state.unseen += result.added;
      $("stream-notice").hidden = false;
      $("stream-notice-text").textContent = `新着 ${state.unseen}件`;
    }
    renderEvents();
    if (state.snapshot) renderLanes(buildLanes(state.snapshot));
  }
  renderObservation();
  return result;
}

async function refreshArtifacts(context) {
  if (!isCurrent(context)) return;
  const payload = await requestJson(api(`/runs/${encodeURIComponent(context.runId)}/artifacts`, {
    qualification: context.qualification,
  }), context.signal);
  if (!isCurrent(context)) return;
  const records = normalizeArtifacts(payload);
  renderArtifacts(records);
}

async function loadSnapshot(context) {
  if (!isCurrent(context)) return;
  const payload = await requestJson(api(`/runs/${encodeURIComponent(context.runId)}/snapshot`, {
    qualification: context.qualification,
  }), context.signal);
  if (!isCurrent(context)) return;
  state.snapshot = payload;
  applyObservationHealth(payload, true);
  const snapshotEvents = first(payload.events, payload.recentEvents);
  if (snapshotEvents) consumeEvents({
    events: snapshotEvents,
    cursor: first(payload.cursor, payload.nextCursor, state.cursor),
    observationHealth: payload.observationHealth,
    observedAt: payload.observedAt,
  });
  const embedded = normalizeArtifacts(payload);
  if (embedded.length) renderArtifacts(embedded);
  renderRun(payload);
}

function runName(run) {
  const id = string(run.runId, run.id);
  const execution = object(run.executionState);
  return first(
    run.title,
    run.name,
    run.label,
    `${statusLabel(first(execution.status, run.status, run.state))} · ${id.slice(0, 10)}`,
  );
}

async function loadRuns(qualification, signal) {
  if (!qualification) throw new Error("URLのqualificationを指定してください。");
  const payload = await requestJson(api("/runs", { qualification }), signal);
  const runs = array(first(payload.runs, payload.items, payload));
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
  runs.forEach((run) => {
    const id = String(first(run.runId, run.id));
    const option = node("option", "", runName(run));
    option.value = id;
    select.append(option);
  });
  if (!runs.length) {
    const option = node("option", "", "実行はありません");
    option.value = "";
    select.append(option);
    return "";
  }
  const requested = state.runId;
  const selected = runs.some((run) => String(first(run.runId, run.id)) === requested)
    ? requested
    : String(first(runs[0].runId, runs[0].id));
  select.value = selected;
  return selected;
}

function resetRunView() {
  state.cursor = "";
  state.seenEventIds.clear();
  state.eventIndex.clear();
  state.events = [];
  state.artifacts = [];
  state.snapshot = null;
  state.unseen = 0;
  state.failures = 0;
  state.observation = {
    health: "unknown", gap: false, stale: false, lastObservedAt: null, detail: "",
  };
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
      consumeEvents(payload);
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
      await Promise.allSettled([loadSnapshot(context), refreshArtifacts(context)]);
      if (!isCurrent(context)) return;
    } catch (error) {
      if (isAbort(error) || !isCurrent(context)) return;
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
  const results = await Promise.allSettled([loadSnapshot(context), refreshArtifacts(context)]);
  if (!isCurrent(context)) return;
  if (results.every((result) => result.status === "rejected")) throw results[0].reason;
  eventLoop(context).catch(showFatal);
  refreshLoop(context).catch(showFatal);
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
    setConnection("error", "実行なし");
    $("run-summary").textContent = "監視できる実行がありません。";
  }
}

globalThis.MonitorUiTest = {
  state,
  api,
  requestJson,
  beginGeneration,
  isCurrent,
  normalizedEvent,
  eventText,
  ingestEvents,
  applyObservationHealth,
  artifactRecord,
  normalizeArtifacts,
  deepLink,
  buildLanes,
};

if (!globalThis.__MONITOR_TEST__) start().catch(showFatal);
