"use strict";

const ISSUE_LABELS = {
  live_mismatch: "Firestore差分",
  firestore_readback_stale: "Firestore再取得待ち",
  answer_explanation_mismatch: "正誤と解説の矛盾",
  required_field_missing: "必須field不足",
  identity_mismatch: "ID不整合",
  law_hold: "法令監査保留",
  merge_stale: "merge未反映",
  convert_stale: "convert未反映",
  upload_stale: "upload-ready未反映",
  upload_missing: "upload-ready未生成",
  law_basis_missing: "法令根拠不足",
  explanation_missing: "解説不足",
  projection_error: "patch合成エラー",
  post_fix_review: "修正後確認",
  manual_flag: "手動要確認",
  direct_edit: "直接編集",
  other: "その他",
};

const REVIEW_LABELS = {
  unreviewed: "未確認",
  needs_review: "要確認",
  awaiting_codex: "Codex対応待ち",
  post_fix_review: "修正後確認",
  approved: "承認済み",
  hold: "保留",
};

const WORKFLOW_LABELS = {
  match: "一致",
  stale: "古い・差分あり",
  missing: "未生成",
  unread: "未取得",
  mismatch: "差分あり",
  error: "取得失敗",
  unavailable: "比較不可",
  upstream_stale: "前回取得・現在は古い比較",
};

const EDITABLE_FIELDS = [
  "correctChoiceText",
  "explanationText",
  "suggestedQuestions",
  "suggestedQuestionDetails",
];

const REVIEW_FIELDS = [
  ...EDITABLE_FIELDS,
  "lawReferences",
  "lawRevisionFacts",
  "questionBodyText",
  "choiceTextList",
];

const REVIEW_SCOPES = new Set([
  "current_question",
  "current_group",
  "qualification",
  "all_qualifications",
]);

const reviewTargetContexts = new WeakMap();

const state = {
  token: "",
  inventory: null,
  qualification: "",
  listGroupId: "",
  exceptionsOnly: true,
  questions: [],
  selectedId: "",
  detail: null,
  reviewMode: "awaiting_codex",
  reviewSelection: null,
  selectionCandidate: null,
  pendingEdit: null,
  editBaselinePairs: [],
  workflowDialog: { mode: "", preview: null, running: false },
  readbackDialog: { preview: null, running: false, requestSequence: 0 },
};

const $ = (selector) => document.querySelector(selector);

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function button(text, className, handler, title) {
  const node = element("button", className, text);
  node.type = "button";
  if (title) node.title = title;
  node.addEventListener("click", handler);
  return node;
}

function openHelp(title, content) {
  $("#help-dialog-title").textContent = title;
  $("#help-dialog-content").textContent = content;
  $("#help-dialog").showModal();
}

function actionWithHelp(text, className, handler, helpTitle, helpContent) {
  const wrapper = element("div", "action-with-help");
  const action = button(text, className, handler);
  const help = button(
    "?",
    "help-button",
    () => openHelp(
      helpTitle,
      typeof helpContent === "function" ? helpContent() : helpContent,
    ),
    `${text}の説明`,
  );
  help.setAttribute("aria-label", `${text}の説明`);
  wrapper.append(action, help);
  return wrapper;
}

async function api(path, options = {}) {
  const request = { method: options.method || "GET", headers: { Accept: "application/json" } };
  if (options.body !== undefined) {
    request.headers["Content-Type"] = "application/json";
    request.headers["X-Review-Session"] = state.token;
    request.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, request);
  let payload;
  try {
    payload = await response.json();
  } catch (_) {
    payload = { error: `HTTP ${response.status}` };
  }
  if (!response.ok) {
    const error = new Error(payload.error || `HTTP ${response.status}`);
    error.payload = payload;
    throw error;
  }
  return payload;
}

function toast(message, isError = false) {
  const node = $("#toast");
  node.textContent = message;
  node.className = `toast show${isError ? " error" : ""}`;
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => { node.className = "toast"; }, 3200);
}

function setLoading(message) {
  $("#list-summary").textContent = message;
}

async function initialize() {
  bindControls();
  populateIssueControls();
  try {
    const [session, inventory] = await Promise.all([api("/api/session"), api("/api/inventory")]);
    state.token = session.sessionToken;
    state.inventory = inventory;
    $("#project-status").textContent = `本番Firestore: ${session.projectId} ・ UI反映可`;
    initializeSelectors();
    await loadQuestions(false);
    window.setInterval(checkFingerprint, 2000);
  } catch (error) {
    toast(error.message, true);
    setLoading("起動に失敗しました");
  }
}

function bindControls() {
  $("#qualification-select").addEventListener("change", async (event) => {
    state.qualification = event.target.value;
    populateGroups();
    await loadQuestions(false);
    updateUrl();
  });
  $("#group-select").addEventListener("change", async (event) => {
    state.listGroupId = event.target.value;
    await loadQuestions(false);
    updateUrl();
  });
  let searchTimer;
  $("#search-input").addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => loadQuestions(false), 220);
  });
  $("#exceptions-button").addEventListener("click", () => setListMode(true));
  $("#all-button").addEventListener("click", () => setListMode(false));
  $("#refresh-button").addEventListener("click", () => loadQuestions(true));
  $("#bulk-readback-button").addEventListener("click", openReadbackDialog);
  $("#bulk-readback-help").addEventListener("click", () => openHelp(
    "資格のFirestoreを確認",
    "選択中の資格に含まれる全フォルダを本番Firestoreから読み取ります。書き込みは行いません。取得結果と取得日時はローカルに保存され、後から問題ごとの差分を確認できます。",
  ));
  for (const selector of ["#law-only", "#firestore-mismatch", "#issue-select", "#review-status-select"]) {
    $(selector).addEventListener("change", () => loadQuestions(false));
  }
  for (const node of document.querySelectorAll(".close-dialog")) {
    node.addEventListener("click", () => node.closest("dialog").close());
  }
  $("#review-form").addEventListener("submit", submitReview);
  $("#edit-form").addEventListener("submit", previewEdit);
  $("#confirm-form").addEventListener("submit", applyEdit);
  $("#workflow-form").addEventListener("submit", executeWorkflow);
  $("#production-confirm").addEventListener("change", updateWorkflowExecuteState);
  $("#workflow-dialog").addEventListener("cancel", (event) => {
    if (state.workflowDialog.running) event.preventDefault();
  });
  $("#readback-form").addEventListener("submit", executeScopedReadback);
  $("#readback-dialog").addEventListener("cancel", (event) => {
    if (state.readbackDialog.running) event.preventDefault();
  });
  $("#add-suggestion").addEventListener("click", () => addSuggestionRow("", ""));
  $("#selection-review-current").addEventListener("click", () => openSelectionReview("current_question"));
  $("#selection-review-similar").addEventListener("click", () => openSelectionReview("qualification"));
  $("#selection-toolbar-close").addEventListener("click", () => clearSelectionToolbar(true));
  document.addEventListener("selectionchange", scheduleSelectionToolbar);
}

function populateIssueControls() {
  const listFilter = $("#issue-select");
  const reviewIssue = $("#review-issue");
  for (const [value, label] of Object.entries(ISSUE_LABELS)) {
    for (const target of [listFilter, reviewIssue]) {
      const option = element("option", "", label);
      option.value = value;
      target.append(option);
    }
  }
}

function initializeSelectors() {
  const params = new URLSearchParams(location.search);
  const qualifications = state.inventory.qualifications || [];
  const requested = params.get("qualification");
  state.qualification = qualifications.some((item) => item.id === requested)
    ? requested
    : state.inventory.defaultQualification || qualifications[0]?.id || "";
  const select = $("#qualification-select");
  select.replaceChildren();
  for (const qualification of qualifications) {
    const option = element("option", "", qualification.id);
    option.value = qualification.id;
    select.append(option);
  }
  select.value = state.qualification;
  populateGroups(params.get("listGroupId"));
}

function populateGroups(requested = null) {
  const qualification = state.inventory.qualifications.find((item) => item.id === state.qualification);
  const groups = qualification?.listGroupIds || [];
  $("#group-select-label").textContent = scopeLabelForGroups(groups);
  state.listGroupId = groups.includes(requested)
    ? requested
    : groups.includes(state.listGroupId)
      ? state.listGroupId
      : groups[groups.length - 1] || "";
  const select = $("#group-select");
  select.replaceChildren();
  for (const group of groups) {
    const option = element("option", "", group);
    option.value = group;
    select.append(option);
  }
  select.value = state.listGroupId;
}

function scopeLabelForGroups(groupIds) {
  return groupIds.length && groupIds.every((value) => /^(?:19|20)\d{2}$/.test(value))
    ? "年度"
    : "フォルダ";
}

function updateUrl() {
  const params = new URLSearchParams();
  if (state.qualification) params.set("qualification", state.qualification);
  if (state.listGroupId) params.set("listGroupId", state.listGroupId);
  history.replaceState(null, "", `${location.pathname}?${params}`);
}

async function setListMode(exceptionsOnly) {
  state.exceptionsOnly = exceptionsOnly;
  $("#exceptions-button").classList.toggle("active", exceptionsOnly);
  $("#all-button").classList.toggle("active", !exceptionsOnly);
  await loadQuestions(false);
}

function listQuery() {
  const params = new URLSearchParams({
    qualification: state.qualification,
    listGroupId: state.listGroupId,
    exceptionsOnly: String(state.exceptionsOnly),
    lawOnly: String($("#law-only").checked),
    firestoreMismatch: String($("#firestore-mismatch").checked),
  });
  const search = $("#search-input").value.trim();
  const issue = $("#issue-select").value;
  const reviewStatus = $("#review-status-select").value;
  if (search) params.set("search", search);
  if (issue) params.set("issue", issue);
  if (reviewStatus) params.set("status", reviewStatus);
  return params;
}

async function loadQuestions(preserveSelection) {
  if (!state.qualification || !state.listGroupId) return;
  setLoading("読み込み中");
  try {
    const payload = await api(`/api/questions?${listQuery()}`);
    state.questions = payload.questions;
    renderQueue();
    $("#list-summary").textContent = `${payload.filteredCount}件表示 / 全${payload.questionCount}問 ・ 例外${payload.issueQuestionCount}問`;
    const selectedStillExists = state.questions.some((question) => question.id === state.selectedId);
    if (!preserveSelection || !selectedStillExists) {
      state.selectedId = state.questions[0]?.id || "";
    }
    if (state.selectedId) {
      await loadDetail(state.selectedId);
    } else {
      state.detail = null;
      renderEmpty("条件に一致する問題はありません。");
    }
  } catch (error) {
    toast(error.message, true);
    setLoading("読み込み失敗");
  }
}

function renderQueue() {
  const queue = $("#queue");
  queue.replaceChildren();
  for (const question of state.questions) {
    const item = element("button", `queue-item${question.id === state.selectedId ? " selected" : ""}`);
    item.type = "button";
    item.dataset.questionId = question.id;
    item.setAttribute("role", "option");
    item.setAttribute("aria-selected", String(question.id === state.selectedId));
    const head = element("div", "queue-item-head");
    head.append(
      element("span", "queue-label", question.questionLabel || question.sourceQuestionKey || question.sourceStem),
      element("span", "queue-review", REVIEW_LABELS[question.reviewStatus] || question.reviewStatus),
    );
    const body = element("p", "queue-body", question.body || "（問題文なし）");
    const issueRow = element("div", "issue-row");
    for (const issue of question.issues.slice(0, 3)) issueRow.append(issueBadge(issue));
    item.append(head, body, issueRow);
    item.addEventListener("click", () => loadDetail(question.id));
    queue.append(item);
  }
}

function issueBadge(issue) {
  const priority = Number(issue.priority ?? 99);
  const className = priority <= 2 ? "badge high" : priority <= 8 ? "badge medium" : "badge";
  const node = element("span", className, ISSUE_LABELS[issue.code] || issue.code);
  node.title = issue.detail || "";
  return node;
}

function normalizeVerdict(value) {
  if (["正しい", "正解", "○", "〇", "true", "True"].includes(value)) return "正しい";
  if (["間違い", "不正解", "誤り", "×", "false", "False"].includes(value)) return "間違い";
  return value || "";
}

async function loadDetail(questionId) {
  state.selectedId = questionId;
  renderQueue();
  try {
    const params = new URLSearchParams({ qualification: state.qualification, listGroupId: state.listGroupId });
    state.detail = await api(`/api/questions/${questionId}?${params}`);
    renderDetail();
    renderQueue();
  } catch (error) {
    toast(error.message, true);
  }
}

function renderEmpty(message) {
  const pane = $("#detail-pane");
  pane.replaceChildren();
  const empty = element("div", "empty-state");
  empty.append(element("strong", "", message));
  if (state.exceptionsOnly) {
    empty.append(button("全問を表示", "secondary-button", () => setListMode(false)));
  }
  pane.append(empty);
}

function renderDetail() {
  const question = state.detail;
  if (!question) return;
  const pane = $("#detail-pane");
  pane.replaceChildren();

  const header = element("header", "detail-header");
  const titleRow = element("div", "detail-title-row");
  const titleBlock = element("div", "");
  titleBlock.append(
    element("h2", "", question.questionLabel || question.sourceQuestionKey || "問題詳細"),
    element("div", "detail-meta", `${question.qualification} / ${question.listGroupId} / ${question.sourceQuestionKey}`),
  );
  const actions = element("div", "detail-actions");
  actions.append(
    actionWithHelp(
      "修正を依頼",
      "primary-button",
      () => openReview("awaiting_codex"),
      "修正を依頼",
      "おかしい箇所と調査範囲を記録し、同時にCodex用の修正依頼を作成してクリップボードへコピーします。指摘記録とCodex依頼を一度で行います。",
    ),
    actionWithHelp(
      "直接編集",
      "secondary-button",
      openEdit,
      "直接編集で変更されるファイル",
      () => directEditHelpText(question),
    ),
  );
  titleRow.append(titleBlock, actions);
  header.append(titleRow, renderWorkflow(question), renderPipelineActions(question));
  pane.append(header);

  const requiredWarning = renderRequiredFieldWarning(question);
  if (requiredWarning) pane.append(requiredWarning);

  const firestoreDiff = renderFirestoreDiff(question);
  if (firestoreDiff) pane.append(firestoreDiff);

  const questionSection = section("問題文");
  const questionBody = element("p", "question-body", question.body);
  installReviewTarget(questionBody, {
    fields: ["questionBodyText"],
    targetLabel: "問題文",
    dataPath: "questionBodyText",
  });
  questionSection.append(questionBody);
  if (question.issues.length) {
    const issues = element("div", "issue-panel");
    for (const issue of question.issues) {
      issues.append(element("div", "issue-line", `${ISSUE_LABELS[issue.code] || issue.code}: ${issue.detail}`));
    }
    questionSection.append(issues);
  }
  pane.append(questionSection);

  const choicesSection = section("選択肢・正誤・基本解説");
  choicesSection.append(renderChoices(question.projected || {}));
  pane.append(choicesSection);

  const suggested = renderSuggestions(question.projected || {});
  if (suggested) {
    const suggestionSection = section("補足質問");
    suggestionSection.append(suggested);
    pane.append(suggestionSection);
  }

  if (question.isLawRelated) pane.append(renderLawSection(question.projected || {}));
  if (question.review) pane.append(renderReviewSection(question));
  pane.append(renderDataSection(question));
}

function directEditHelpText(question) {
  const patches = question.paths?.patches || [];
  const explanationPath = patches.find((path) => path.includes("/21_explanationText_added/"));
  const correctPath = patches.find((path) => path.includes("/23_correctChoiceText_fixed/"));
  const sourcePath = question.paths?.source || "";
  const generatedCorrectPath = sourcePath
    ? sourcePath
      .replace("/00_source/", "/23_correctChoiceText_fixed/")
      .replace(/\.json$/, "_correctChoiceText_fixed.json")
    : "23_correctChoiceText_fixed内の対象ファイル";
  return [
    "基本解説・補足質問:",
    explanationPath || "21_explanationText_added内の既存対象ファイル（存在しない場合は保存を停止）",
    "",
    "正誤:",
    correctPath || generatedCorrectPath,
    "",
    "00_sourceは変更しません。保存前に必須fieldと差分を確認します。法令問題の正誤は直接編集せず、Codex依頼へ切り替えます。",
  ].join("\n");
}

function renderRequiredFieldWarning(question) {
  const issues = (question.issues || []).filter((issue) => issue.code === "required_field_missing");
  if (!issues.length) return null;
  const node = element("section", "required-warning-panel");
  node.append(
    element("strong", "", "必須フィールドが不足しています"),
    element("p", "", "不足を解消するまで、パッチ変更をMerge・Convert・upload-readyへ反映できません。"),
  );
  const list = document.createElement("ul");
  for (const issue of issues) {
    const fields = (issue.fields || []).join(", ");
    list.append(element("li", "", `${fields || "field不明"}: ${issue.detail}`));
  }
  node.append(list);
  return node;
}

function section(title) {
  const node = element("section", "detail-section");
  node.append(element("h3", "", title));
  return node;
}

function renderWorkflow(question) {
  const workflow = question.workflow || {};
  const node = element("div", "workflow");
  const stages = [
    ["Patch", workflow.patch],
    ["Merge", workflow.merge],
    ["Convert", workflow.convert],
    ["upload-ready", workflow.upload],
    ["Firestore", workflow.firestore],
  ];
  for (const [name, status] of stages) {
    const step = element("div", `workflow-step ${status}`);
    step.append(
      element("strong", "", name),
      element("span", "", workflowStatusLabel(name, status, question)),
    );
    node.append(step);
  }
  return node;
}

function topLevelFirestoreField(path) {
  return String(path || "").match(/^[^.[\]]+/)?.[0] || String(path || "");
}

function firestoreDiffStats(question) {
  const documents = question.liveReadback?.documents || [];
  const mismatched = documents.filter((document) => document.status === "mismatch");
  const missing = documents.filter((document) => document.status === "missing");
  const fields = new Set();
  for (const document of mismatched) {
    for (const path of document.differences || []) fields.add(topLevelFirestoreField(path));
  }
  return {
    fieldCount: fields.size,
    mismatchedCount: mismatched.length,
    missingCount: missing.length,
  };
}

function workflowStatusLabel(name, status, question) {
  if (name !== "Firestore") return WORKFLOW_LABELS[status] || status;
  const stats = firestoreDiffStats(question);
  if (status === "mismatch") return `差分あり ${stats.fieldCount}項目`;
  if (status === "missing") return `未登録 ${stats.missingCount}件`;
  return WORKFLOW_LABELS[status] || status;
}

function formatReadbackTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("ja-JP", {
      timeZone: "Asia/Tokyo",
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).formatToParts(date).map((part) => [part.type, part.value]),
  );
  return `${parts.year}年${parts.month}月${parts.day}日 ${parts.hour}時${parts.minute}分時点`;
}

function questionReadbackTime(question) {
  return formatReadbackTime(question.liveReadback?.readbackMeta?.storedAt);
}

function scrollToFirestoreDiff() {
  $("#firestore-diff-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderPipelineActions(question) {
  const workflow = question.workflow || {};
  const localReady = ["merge", "convert", "upload"].every((stage) => workflow[stage] === "match");
  const node = element("div", `pipeline-action-bar ${localReady ? "ready" : "attention"}`);
  const status = element("div", "pipeline-message");
  const actions = element("div", "pipeline-buttons");

  if (!localReady) {
    status.append(
      element("strong", "", "最新patchが後続成果物へ未反映です"),
      element("span", "", "対象フォルダだけをMerge、Convert、upload-readyまで再生成します。"),
    );
    const readbackTime = questionReadbackTime(question);
    if (readbackTime) status.append(element("span", "", `Firestore取得: ${readbackTime}`));
    const hasStoredDiff = ["mismatch", "missing"].includes(question.liveReadback?.status);
    if (["mismatch", "missing"].includes(workflow.firestore) || hasStoredDiff) {
      actions.append(actionWithHelp(
        "保存済み差分を見る",
        "secondary-button",
        scrollToFirestoreDiff,
        "保存済み差分を見る",
        "最後に資格単位で取得したFirestore値と、その取得時点のローカル成果物との差分へ移動します。現在の成果物より古い比較の場合は、その旨を表示します。",
      ));
    }
    actions.append(actionWithHelp(
      "パッチ変更を反映",
      "primary-button",
      () => openSyncDialog(true),
      "パッチ変更を反映",
      "現在の資格・フォルダだけを対象に、最新patchからMerge、Convert、upload-readyを再生成し、upload dry-runまで自動で検証します。Firestoreへの書き込みは行いません。必須field不足がある場合は開始しません。",
    ));
  } else {
    const stats = firestoreDiffStats(question);
    const differenceSummary = [
      stats.missingCount ? `未登録${stats.missingCount}件` : "",
      stats.fieldCount ? `差分${stats.fieldCount}項目` : "",
    ].filter(Boolean).join("・");
    const messages = {
      match: ["本番Firestoreまで一致しています", "選択中の問題は最新upload-readyと一致しています。"],
      mismatch: ["本番Firestoreに差分があります", `${differenceSummary || "field差分あり"}。下の比較表ですぐ確認できます。`],
      missing: ["本番Firestoreに未登録の問題があります", `${differenceSummary || "未登録documentあり"}。下の比較表ですぐ確認できます。`],
      error: ["Firestoreの確認に失敗しました", "credentialと接続状態を確認してください。"],
      unread: ["ローカル成果物は最新です", "本番Firestoreはまだ確認していません。"],
      unavailable: ["Firestoreと比較できません", "upload-readyのdocument IDを確認してください。"],
      upstream_stale: ["最新成果物とは未比較です", "前回取得結果は保持されています。パッチ変更を反映後、資格のFirestoreを確認してください。"],
    };
    const [title, detail] = messages[workflow.firestore] || messages.unread;
    status.append(element("strong", "", title), element("span", "", detail));
    const readbackTime = questionReadbackTime(question);
    if (readbackTime) status.append(element("span", "", `Firestore取得: ${readbackTime}`));
    if (["mismatch", "missing"].includes(workflow.firestore)) {
      actions.append(actionWithHelp(
        "保存済み差分を見る",
        "primary-button",
        scrollToFirestoreDiff,
        "保存済み差分を見る",
        "最後に資格単位で取得したFirestore値とローカル成果物の差分へ移動します。",
      ));
    }
    actions.append(
      actionWithHelp(
        "資格のFirestoreを確認",
        "secondary-button",
        openReadbackDialog,
        "資格のFirestoreを確認",
        "選択中の資格全体を本番Firestoreから読み取り、結果と取得日時をローカルへ保存します。読み取り専用で、Firestoreは変更しません。",
      ),
      actionWithHelp(
        "Firestoreへ反映",
        ["mismatch", "missing"].includes(workflow.firestore) ? "secondary-button" : "primary-button",
        openPublishDialog,
        "Firestoreへ反映",
        "現在の資格・フォルダのupload-readyと本番Firestoreを比較し、差分がある場合だけ確認画面を経て本番へ書き込みます。資格全体の読取とは別の操作です。",
      ),
    );
  }
  node.append(status, actions);
  return node;
}

function parseDataPath(path) {
  const tokens = [];
  const pattern = /([^.[\]]+)|\[(\d+)\]/g;
  let match;
  while ((match = pattern.exec(String(path || "")))) {
    tokens.push(match[1] !== undefined ? match[1] : Number(match[2]));
  }
  return tokens;
}

function relativeDiffPath(path, field) {
  const tokens = parseDataPath(path);
  if (tokens[0] === field) return tokens.slice(1);
  return tokens;
}

function dataPathLabel(tokens) {
  if (!tokens.length) return "全体";
  return tokens
    .map((token) => (Number.isInteger(token) ? `${token + 1}件目` : String(token)))
    .join(" / ");
}

function valueAtPath(value, tokens) {
  let current = value;
  for (const token of tokens) {
    if (current === undefined || current === null) return undefined;
    current = current[token];
  }
  return current;
}

function isPlainValue(value) {
  return value === undefined || value === null || typeof value !== "object";
}

function plainValueText(value) {
  if (value === undefined) return "fieldなし";
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

function comparableValue(value) {
  if (Array.isArray(value)) return value.map(comparableValue);
  if (value && typeof value === "object") {
    return Object.keys(value)
      .sort()
      .reduce((accumulator, key) => {
        accumulator[key] = comparableValue(value[key]);
        return accumulator;
      }, {});
  }
  return value === undefined ? "__undefined__" : value;
}

function valuesEqual(left, right) {
  return JSON.stringify(comparableValue(left)) === JSON.stringify(comparableValue(right));
}

function noDiffValue() {
  return element("span", "firestore-diff-no-change", "差分なし");
}

function collectLeafEntries(value, prefix = [], entries = []) {
  if (isPlainValue(value)) {
    entries.push([prefix, value]);
    return entries;
  }
  if (Array.isArray(value)) {
    if (!value.length) entries.push([prefix, "[]"]);
    value.forEach((item, index) => collectLeafEntries(item, [...prefix, index], entries));
    return entries;
  }
  const keys = Object.keys(value);
  if (!keys.length) {
    entries.push([prefix, "{}"]);
    return entries;
  }
  for (const key of keys) collectLeafEntries(value[key], [...prefix, key], entries);
  return entries;
}

function firestoreReviewContext(context, tokens = []) {
  if (!context) return null;
  const field = context.fields?.[0] || "";
  const pathTokens = [...(context.pathTokens || []), ...tokens];
  const suffix = pathTokens.map((token) => (Number.isInteger(token) ? `[${token}]` : `.${token}`)).join("");
  return {
    ...context,
    pathTokens,
    choiceIndexes: Number.isInteger(pathTokens[0]) ? [pathTokens[0]] : [],
    dataPath: `${field}${suffix}`,
  };
}

function renderReadableValue(value, otherValue, reviewContext = null) {
  if (valuesEqual(value, otherValue)) return noDiffValue();
  if (value === undefined) return element("span", "firestore-diff-empty", "fieldなし");
  if (value === null) return element("span", "firestore-diff-empty", "null");
  if (isPlainValue(value)) {
    const node = element("span", "firestore-diff-text");
    node.textContent = plainValueText(value);
    if (reviewContext) installReviewTarget(node, reviewContext);
    return node;
  }

  const entries = collectLeafEntries(value);
  const list = element("div", "firestore-diff-leaf-list");
  for (const [tokens, entryValue] of entries) {
    const row = element("div", "firestore-diff-leaf");
    row.append(element("code", "firestore-diff-leaf-path", dataPathLabel(tokens)));
    row.append(renderReadableValue(
      entryValue,
      valueAtPath(otherValue, tokens),
      firestoreReviewContext(reviewContext, tokens),
    ));
    list.append(row);
  }
  return list;
}

function uniqueRelativePaths(paths, field) {
  const seen = new Set();
  const values = [];
  for (const path of paths || []) {
    const tokens = relativeDiffPath(path, field);
    const key = JSON.stringify(tokens);
    if (seen.has(key)) continue;
    seen.add(key);
    values.push(tokens);
  }
  return values;
}

function firestoreDiffValue(value, otherValue, paths, field, sourceLabel) {
  const relativePaths = uniqueRelativePaths(paths, field);
  const reviewContext = {
    fields: [field],
    issueType: "live_mismatch",
    targetLabel: `Firestore差分 / ${sourceLabel} / ${field}`,
  };
  if (!relativePaths.length || (relativePaths.length === 1 && !relativePaths[0].length)) {
    return renderReadableValue(value, otherValue, firestoreReviewContext(reviewContext));
  }

  const list = element("div", "firestore-diff-item-list");
  for (const tokens of relativePaths) {
    const item = element("div", "firestore-diff-item");
    item.append(element("code", "firestore-diff-item-path", dataPathLabel(tokens)));
    item.append(renderReadableValue(
      valueAtPath(value, tokens),
      valueAtPath(otherValue, tokens),
      firestoreReviewContext(reviewContext, tokens),
    ));
    list.append(item);
  }
  return list;
}

function renderFirestoreDiff(question) {
  const workflowStatus = question.workflow?.firestore;
  const liveReadback = question.liveReadback;
  if (!["mismatch", "missing", "error", "unavailable", "upstream_stale"].includes(workflowStatus)) {
    return null;
  }

  const node = section("Firestore差分");
  node.id = "firestore-diff-panel";
  node.classList.add("firestore-diff-panel");
  const readbackTime = questionReadbackTime(question);
  if (readbackTime) {
    node.append(element("p", "firestore-readback-time", `Firestore取得: ${readbackTime}`));
  }

  if (workflowStatus === "upstream_stale") {
    node.append(element(
      "p",
      "firestore-diff-notice",
      "表示中の差分は取得時点のローカル成果物との比較です。現在の成果物とは異なるため、最新比較には資格全体の再取得が必要です。",
    ));
    if (liveReadback?.status === "match") return node;
  }
  if (["error", "unavailable"].includes(liveReadback?.status || workflowStatus)) {
    node.append(element(
      "p",
      "firestore-diff-notice error",
      liveReadback?.error || "Firestoreの比較結果を取得できませんでした。",
    ));
    return node;
  }

  const stats = firestoreDiffStats(question);
  const summary = element("div", "firestore-diff-summary");
  summary.append(
    element("strong", "", `${stats.mismatchedCount + stats.missingCount}件のdocumentに差分`),
    element("span", "", `値の差分 ${stats.fieldCount}項目 / 未登録 ${stats.missingCount}件`),
  );
  node.append(summary);

  const expectedDocuments = question.uploadReadyDocs?.length
    ? question.uploadReadyDocs
    : question.convertedDocs || [];
  const expectedById = new Map(
    expectedDocuments.map((document) => [String(document.questionId || ""), document]),
  );
  const changedDocuments = (liveReadback?.documents || [])
    .filter((document) => document.status !== "match");

  if (!changedDocuments.length) {
    node.append(element("p", "firestore-diff-notice", "比較結果の詳細がありません。資格のFirestoreを確認してください。"));
    return node;
  }

  const list = element("div", "firestore-diff-documents");
  for (const readbackDocument of changedDocuments) {
    const expected = expectedById.get(String(readbackDocument.questionId || "")) || {};
    const block = element("article", "firestore-diff-document");
    const heading = element("div", "firestore-diff-document-heading");
    heading.append(
      element(
        "strong",
        "",
        expected.originalQuestionChoiceText || expected.questionText || readbackDocument.questionId || "document IDなし",
      ),
      element("code", "", readbackDocument.questionId || "questionIdなし"),
      element(
        "span",
        `firestore-diff-status ${readbackDocument.status}`,
        readbackDocument.status === "missing" ? "Firestore未登録" : "値に差分",
      ),
    );
    block.append(heading);

    if (readbackDocument.status === "missing") {
      block.append(element(
        "p",
        "firestore-diff-missing",
        "upload-readyにはこのdocumentがありますが、本番Firestoreには存在しません。",
      ));
      list.append(block);
      continue;
    }

    const fields = [...new Set((readbackDocument.differences || []).map(topLevelFirestoreField))].filter(Boolean);
    if (!fields.length) {
      block.append(element("p", "firestore-diff-missing", "差分なし"));
      list.append(block);
      continue;
    }

    const tableWrap = element("div", "firestore-diff-table-wrap");
    const table = element("table", "firestore-diff-table");
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    headRow.append(
      element("th", "", "field"),
      element("th", "", "upload-ready"),
      element("th", "", "Firestore（取得値）"),
    );
    head.append(headRow);
    table.append(head);
    const body = document.createElement("tbody");
    for (const field of fields) {
      const row = document.createElement("tr");
      const fieldCell = element("th", "");
      fieldCell.append(element("strong", "", field));
      const nestedPaths = (readbackDocument.differences || [])
        .filter((path) => topLevelFirestoreField(path) === field && path !== field);
      if (nestedPaths.length) {
        const pathList = element("div", "firestore-diff-paths");
        for (const path of nestedPaths) pathList.append(element("code", "", path));
        fieldCell.append(pathList);
      }
      row.append(
        fieldCell,
        element("td", ""),
        element("td", ""),
      );
      const fieldPaths = nestedPaths.length ? nestedPaths : [field];
      row.children[1].append(
        firestoreDiffValue(expected[field], readbackDocument.live?.[field], fieldPaths, field, "upload-ready"),
      );
      row.children[2].append(
        firestoreDiffValue(readbackDocument.live?.[field], expected[field], fieldPaths, field, "Firestore取得値"),
      );
      body.append(row);
    }
    table.append(body);
    tableWrap.append(table);
    block.append(tableWrap);
    list.append(block);
  }
  node.append(list);
  return node;
}

function groupApiPath(action) {
  return `/api/groups/${encodeURIComponent(state.qualification)}/${encodeURIComponent(state.listGroupId)}/${action}`;
}

function resetWorkflowDialog(mode, title) {
  state.workflowDialog = { mode, preview: null, running: false };
  $("#workflow-dialog-title").textContent = title;
  $("#workflow-dialog-message").textContent = "確認情報を取得しています。";
  $("#workflow-dialog-summary").replaceChildren();
  $("#production-confirm-wrap").hidden = true;
  $("#production-confirm").checked = false;
  $("#job-status").hidden = true;
  $("#job-status").textContent = "";
  $("#job-log-wrap").hidden = true;
  $("#job-log").textContent = "";
  $("#workflow-execute").textContent = "確認中";
  $("#workflow-execute").disabled = true;
  $("#workflow-cancel").hidden = false;
  for (const node of $("#workflow-dialog").querySelectorAll(".close-dialog")) node.disabled = false;
  if (!$("#workflow-dialog").open) $("#workflow-dialog").showModal();
}

function summaryMetric(label, value, tone = "") {
  const item = element("div", `workflow-summary-item ${tone}`.trim());
  item.append(element("span", "", label), element("strong", "", value));
  return item;
}

function stageSummary(preview, stage, label) {
  const data = preview.stages?.[stage] || { status: "missing", counts: {} };
  const counts = data.counts || {};
  const value = data.status === "match"
    ? `一致 ${counts.match || 0}問`
    : `${WORKFLOW_LABELS[data.status] || data.status} ${Number(counts.stale || 0) + Number(counts.missing || 0)}問`;
  return summaryMetric(label, value, data.status === "match" ? "good" : "warning");
}

async function openSyncDialog(autoStart = false) {
  resetWorkflowDialog("sync", "パッチ変更を反映");
  try {
    const preview = await api(groupApiPath("sync-preview"), { method: "POST", body: {} });
    state.workflowDialog.preview = preview;
    $("#workflow-dialog-message").textContent = preview.needsSync
      ? preview.allowMissingAnswerResult
        ? "既存workflowを実行します。回答結果が空の問題は、全選択肢の精査済み正誤を保持して検証します。00_sourceは変更しません。"
        : "既存workflowを対象フォルダだけで実行し、upload dry-runまで検証します。00_sourceは変更しません。"
      : "Merge、Convert、upload-readyはすでに最新です。";
    $("#workflow-dialog-summary").append(
      summaryMetric("対象", `${preview.qualification} / ${preview.listGroupId}`),
      summaryMetric("問題", `${preview.questionCount}問`),
      stageSummary(preview, "merge", "Merge"),
      stageSummary(preview, "convert", "Convert"),
      stageSummary(preview, "upload", "upload-ready"),
    );
    if (preview.requiredFieldWarnings?.length) {
      $("#workflow-dialog-message").textContent =
        "必須field不足があるため、パッチ変更を反映できません。問題詳細の警告を修正してください。";
      $("#workflow-dialog-summary").append(
        summaryMetric("必須field不足", `${preview.requiredFieldWarnings.length}問`, "danger"),
      );
      state.workflowDialog.mode = "";
      $("#workflow-execute").textContent = "閉じる";
      $("#workflow-execute").disabled = false;
      return;
    }
    $("#workflow-execute").textContent = preview.needsSync ? "反映を開始" : "閉じる";
    $("#workflow-execute").disabled = false;
    if (!preview.needsSync) state.workflowDialog.mode = "";
    if (autoStart && preview.needsSync) await startWorkflowExecution();
  } catch (error) {
    showWorkflowError(error);
  }
}

async function openPublishDialog() {
  resetWorkflowDialog("publish", "Firestoreへ反映");
  try {
    const preview = await api(groupApiPath("publish-preview"), { method: "POST", body: {} });
    state.workflowDialog.preview = preview;
    const summary = $("#workflow-dialog-summary");
    summary.append(
      summaryMetric("対象", `${preview.qualification} / ${preview.listGroupId}`),
      summaryMetric("本番project", preview.projectId),
    );
    if (!preview.localReady || preview.blockingIssues && Object.keys(preview.blockingIssues).length) {
      $("#workflow-dialog-message").textContent = preview.reason || "本番反映の前提条件を満たしていません。";
      if (preview.stages) {
        summary.append(
          stageSummary(preview, "merge", "Merge"),
          stageSummary(preview, "convert", "Convert"),
          stageSummary(preview, "upload", "upload-ready"),
        );
      }
      for (const [code, count] of Object.entries(preview.blockingIssues || {})) {
        summary.append(summaryMetric(ISSUE_LABELS[code] || code, `${count}問`, "danger"));
      }
      state.workflowDialog.mode = "";
      $("#workflow-execute").textContent = "閉じる";
      $("#workflow-execute").disabled = false;
      return;
    }

    summary.append(
      summaryMetric("全document", `${preview.documentCount}件`),
      summaryMetric("変更・追加", `${preview.changedCount}件`, preview.changedCount ? "warning" : "good"),
      summaryMetric("未登録", `${preview.missingCount}件`, preview.missingCount ? "warning" : "good"),
      summaryMetric("成果物SHA", String(preview.artifactHash || "").slice(0, 12)),
    );
    if (!preview.canPublish) {
      $("#workflow-dialog-message").textContent = "対象フォルダのupload-readyと本番Firestoreは一致しています。反映は不要です。";
      state.workflowDialog.mode = "";
      $("#workflow-execute").textContent = "閉じる";
      $("#workflow-execute").disabled = false;
      return;
    }
    $("#workflow-dialog-message").textContent =
      "表示中のupload-readyを本番へ差分反映します。questionsに加えて公式試験年度manifestも更新されます。";
    $("#production-confirm-wrap").hidden = false;
    $("#workflow-execute").textContent = "本番へ反映";
    updateWorkflowExecuteState();
  } catch (error) {
    showWorkflowError(error);
  }
}

function updateWorkflowExecuteState() {
  if (state.workflowDialog.running) return;
  if (state.workflowDialog.mode === "publish") {
    $("#workflow-execute").disabled = !$("#production-confirm").checked;
  }
}

async function executeWorkflow(event) {
  event.preventDefault();
  await startWorkflowExecution();
}

async function startWorkflowExecution() {
  const { mode, preview } = state.workflowDialog;
  if (!mode || !preview) {
    $("#workflow-dialog").close();
    return;
  }
  if (mode === "publish" && !$("#production-confirm").checked) return;

  setWorkflowRunning(true);
  try {
    const body = mode === "sync"
      ? { previewToken: preview.previewToken }
      : { preflightToken: preview.preflightToken, confirmedProduction: true };
    const job = await api(groupApiPath(mode === "sync" ? "sync" : "publish"), {
      method: "POST",
      body,
    });
    await pollJob(job.jobId, mode);
  } catch (error) {
    showWorkflowError(error);
  }
}

function setWorkflowRunning(running) {
  state.workflowDialog.running = running;
  $("#workflow-execute").disabled = running;
  $("#workflow-cancel").hidden = running;
  $("#production-confirm-wrap").hidden = true;
  $("#job-status").hidden = !running;
  $("#job-status").textContent = running ? "処理を開始しています。" : "";
  $("#job-log-wrap").hidden = !running;
  for (const node of $("#workflow-dialog").querySelectorAll(".close-dialog")) node.disabled = running;
}

async function pollJob(jobId, mode) {
  while (true) {
    const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
    $("#job-status").textContent = job.status === "queued" ? "実行待ち" : job.status === "running" ? "処理中" : "完了";
    $("#job-log").textContent = (job.logs || []).join("\n");
    $("#job-log").scrollTop = $("#job-log").scrollHeight;
    if (job.status === "queued" || job.status === "running") {
      await new Promise((resolve) => window.setTimeout(resolve, 800));
      continue;
    }
    if (job.status === "failed") throw new Error(job.error || "処理に失敗しました。");
    await refreshAfterWorkflow(mode);
    state.workflowDialog.running = false;
    state.workflowDialog.mode = "";
    $("#job-status").textContent = job.result?.message || "完了しました。";
    $("#workflow-dialog-message").textContent = job.result?.message || "処理が完了しました。";
    $("#workflow-execute").textContent = "閉じる";
    $("#workflow-execute").disabled = false;
    $("#workflow-cancel").hidden = true;
    for (const node of $("#workflow-dialog").querySelectorAll(".close-dialog")) node.disabled = false;
    toast(job.result?.message || "処理が完了しました。");
    return;
  }
}

async function refreshAfterWorkflow(mode) {
  if (mode === "sync" && state.exceptionsOnly) {
    state.exceptionsOnly = false;
    $("#exceptions-button").classList.remove("active");
    $("#all-button").classList.add("active");
  }
  await loadQuestions(true);
}

function showWorkflowError(error) {
  const message = error.message === "APIが見つかりません。"
    ? "画面とサーバーの版が一致していません。画面を再読み込みしてください。"
    : error.message;
  state.workflowDialog.running = false;
  state.workflowDialog.mode = "";
  $("#workflow-dialog-message").textContent = message;
  $("#job-status").hidden = false;
  $("#job-status").textContent = "処理を完了できませんでした。";
  $("#workflow-execute").textContent = "閉じる";
  $("#workflow-execute").disabled = false;
  $("#workflow-cancel").hidden = true;
  for (const node of $("#workflow-dialog").querySelectorAll(".close-dialog")) node.disabled = false;
  toast(message, true);
}

function openReadbackDialog() {
  state.readbackDialog = { preview: null, running: false, requestSequence: 0 };
  $("#readback-qualification").textContent = state.qualification;
  $("#readback-message").textContent =
    "この資格の全フォルダをまとめて読み取り、結果をローカルに保存します。";
  $("#readback-summary").replaceChildren();
  $("#readback-job-status").hidden = true;
  $("#readback-job-log-wrap").hidden = true;
  $("#readback-job-log").textContent = "";
  $("#readback-execute").textContent = "資格全体を読み取る";
  $("#readback-execute").onclick = null;
  $("#readback-cancel").hidden = false;
  setReadbackRunning(false);
  if (!$("#readback-dialog").open) $("#readback-dialog").showModal();
  refreshReadbackPreview();
}

async function refreshReadbackPreview() {
  if (state.readbackDialog.running) return;
  $("#readback-execute").onclick = null;
  $("#readback-execute").textContent = "資格全体を読み取る";
  const sequence = ++state.readbackDialog.requestSequence;
  state.readbackDialog.preview = null;
  $("#readback-summary").replaceChildren();
  $("#readback-execute").disabled = true;
  $("#readback-message").textContent = "ローカル成果物から読取範囲を計算しています。";
  try {
    const preview = await api("/api/firestore-readback/preview", {
      method: "POST",
      body: { qualification: state.qualification },
    });
    if (sequence !== state.readbackDialog.requestSequence) return;
    state.readbackDialog.preview = preview;
    $("#readback-message").textContent =
      "この確認ではFirestoreへアクセスしていません。実行すると資格全体を読み取り、結果をローカル保存します。";
    $("#readback-summary").append(
      summaryMetric("資格", preview.qualification),
      summaryMetric("対象フォルダ", `${preview.groupCount}件`),
      summaryMetric("元問題", `${preview.questionCount}問`),
      summaryMetric("読取対象", `${preview.documentCount} documents`, preview.documentCount ? "warning" : ""),
      summaryMetric("比較不可", `${preview.unavailableQuestionCount}問`, preview.unavailableQuestionCount ? "danger" : "good"),
      summaryMetric("本番project", preview.projectId),
      summaryMetric(
        "前回取得",
        formatReadbackTime(preview.lastReadback?.storedAt) || "未取得",
        preview.lastReadback ? "good" : "",
      ),
    );
    $("#readback-execute").disabled = preview.documentCount === 0;
  } catch (error) {
    if (sequence !== state.readbackDialog.requestSequence) return;
    showReadbackError(error);
  }
}

async function executeScopedReadback(event) {
  event.preventDefault();
  const preview = state.readbackDialog.preview;
  if (!preview || state.readbackDialog.running) return;
  setReadbackRunning(true);
  try {
    const job = await api("/api/firestore-readback/run", {
      method: "POST",
      body: {
        qualification: preview.qualification,
        previewToken: preview.previewToken,
      },
    });
    await pollReadbackJob(job.jobId);
  } catch (error) {
    showReadbackError(error);
  }
}

function setReadbackRunning(running) {
  state.readbackDialog.running = running;
  $("#readback-execute").disabled = running || !state.readbackDialog.preview;
  $("#readback-cancel").hidden = running;
  $("#readback-job-status").hidden = !running;
  $("#readback-job-log-wrap").hidden = !running;
  for (const node of $("#readback-dialog").querySelectorAll("button, input")) {
    if (node.id !== "readback-execute") node.disabled = running;
  }
}

async function pollReadbackJob(jobId) {
  while (true) {
    const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
    $("#readback-job-status").textContent = job.status === "queued"
      ? "実行待ち"
      : job.status === "running"
        ? "Firestoreを読み取り中"
        : "完了";
    $("#readback-job-log").textContent = (job.logs || []).join("\n");
    $("#readback-job-log").scrollTop = $("#readback-job-log").scrollHeight;
    if (job.status === "queued" || job.status === "running") {
      await new Promise((resolve) => window.setTimeout(resolve, 800));
      continue;
    }
    if (job.status === "failed") throw new Error(job.error || "Firestoreの読み取りに失敗しました。");
    state.readbackDialog.running = false;
    state.readbackDialog.preview = null;
    const completedAt = formatReadbackTime(job.result?.readAt);
    $("#readback-message").textContent = completedAt
      ? `${job.result?.message || "Firestore状態を更新しました。"} ${completedAt}`
      : job.result?.message || "Firestore状態を更新しました。";
    $("#readback-job-status").textContent = formatReadbackStatusCounts(job.result?.statusCounts || {});
    $("#readback-execute").textContent = "閉じる";
    $("#readback-execute").disabled = false;
    $("#readback-execute").onclick = () => $("#readback-dialog").close();
    for (const node of $("#readback-dialog").querySelectorAll(".close-dialog")) node.disabled = false;
    await loadQuestions(true);
    toast(job.result?.message || "Firestore状態を更新しました。");
    return;
  }
}

function formatReadbackStatusCounts(counts) {
  const labels = {
    match: "一致",
    mismatch: "差分あり",
    missing: "未登録",
    unavailable: "比較不可",
    error: "取得失敗",
  };
  const parts = Object.entries(counts).map(([status, count]) => `${labels[status] || status} ${count}問`);
  return parts.length ? parts.join(" / ") : "完了";
}

function showReadbackError(error) {
  state.readbackDialog.running = false;
  state.readbackDialog.preview = null;
  $("#readback-message").textContent = error.message;
  $("#readback-job-status").hidden = false;
  $("#readback-job-status").textContent = "処理を完了できませんでした。";
  $("#readback-execute").textContent = "閉じる";
  $("#readback-execute").disabled = false;
  $("#readback-execute").onclick = () => $("#readback-dialog").close();
  $("#readback-cancel").hidden = true;
  for (const node of $("#readback-dialog").querySelectorAll(".close-dialog")) node.disabled = false;
  toast(error.message, true);
}

function renderChoices(projected) {
  const choices = Array.isArray(projected.choiceTextList) ? projected.choiceTextList : [];
  const correctness = Array.isArray(projected.correctChoiceText) ? projected.correctChoiceText : [];
  const explanations = Array.isArray(projected.explanationText) ? projected.explanationText : [];
  const node = element("div", "choices");
  choices.forEach((choice, index) => {
    const rawVerdict = correctness[index] || "未設定";
    const verdictValue = normalizeVerdict(rawVerdict);
    const card = element("article", "choice-card");
    const indexNode = element("div", "choice-index");
    const verdict = element("span", `verdict ${verdictValue === "正しい" ? "correct" : "incorrect"}`, rawVerdict);
    installReviewTarget(verdict, {
      fields: ["correctChoiceText"],
      choiceIndexes: [index],
      targetLabel: `選択肢${index + 1}の正誤`,
      dataPath: `correctChoiceText[${index}]`,
    });
    indexNode.append(
      element("span", "", String(index + 1)),
      verdict,
    );
    const choiceText = element("div", "choice-text", choice);
    installReviewTarget(choiceText, {
      fields: ["choiceTextList"],
      choiceIndexes: [index],
      targetLabel: `選択肢${index + 1}`,
      dataPath: `choiceTextList[${index}]`,
    });
    const explanation = element("div", "choice-explanation", explanations[index] || "（解説なし）");
    installReviewTarget(explanation, {
      fields: ["explanationText"],
      choiceIndexes: [index],
      targetLabel: `選択肢${index + 1}の基本解説`,
      dataPath: `explanationText[${index}]`,
    });
    card.append(
      indexNode,
      choiceText,
      explanation,
    );
    node.append(card);
  });
  if (!choices.length) node.append(element("p", "", "選択肢がありません。"));
  return node;
}

function renderSuggestions(projected) {
  const questions = Array.isArray(projected.suggestedQuestions) ? projected.suggestedQuestions : [];
  const details = Array.isArray(projected.suggestedQuestionDetails) ? projected.suggestedQuestionDetails : [];
  if (!questions.length && !details.length) return null;
  const table = element("table", "suggestion-table");
  const count = Math.max(questions.length, details.length);
  for (let index = 0; index < count; index += 1) {
    const detail = details[index] || {};
    const row = document.createElement("tr");
    const question = element("th", "", detail.question || questions[index] || "");
    const questionField = detail.question ? "suggestedQuestionDetails" : "suggestedQuestions";
    const questionPath = detail.question
      ? `suggestedQuestionDetails[${index}].question`
      : `suggestedQuestions[${index}]`;
    installReviewTarget(question, {
      fields: [questionField],
      targetLabel: `補足質問${index + 1}`,
      dataPath: questionPath,
    });
    const answer = element("td", "", detail.answer || "（回答なし）");
    installReviewTarget(answer, {
      fields: ["suggestedQuestionDetails"],
      targetLabel: `補足質問${index + 1}の回答`,
      dataPath: `suggestedQuestionDetails[${index}].answer`,
    });
    row.append(question, answer);
    table.append(row);
  }
  return table;
}

function renderLawSection(projected) {
  const node = section("法令根拠");
  const references = document.createElement("details");
  references.open = true;
  references.append(element("summary", "", "lawReferences"));
  const content = element("div", "details-content");
  const referenceData = jsonPre(projected.lawReferences || []);
  installReviewTarget(referenceData, {
    fields: ["lawReferences"],
    targetLabel: "法令根拠 / lawReferences",
    dataPath: "lawReferences",
  });
  content.append(referenceData);
  references.append(content);
  const facts = document.createElement("details");
  facts.append(element("summary", "", "lawRevisionFacts"));
  const factContent = element("div", "details-content");
  const factData = jsonPre(projected.lawRevisionFacts || []);
  installReviewTarget(factData, {
    fields: ["lawRevisionFacts"],
    targetLabel: "法令根拠 / lawRevisionFacts",
    dataPath: "lawRevisionFacts",
  });
  factContent.append(factData);
  facts.append(factContent);
  node.append(references, facts);
  return node;
}

function renderReviewSection(question) {
  const review = question.review;
  const node = section("人間レビュー");
  const status = element("p", "", `状態: ${REVIEW_LABELS[question.reviewStatus] || question.reviewStatus} / ${review.reviewId}`);
  const note = element("p", "", review.note || "（指摘なし）");
  const actions = element("div", "detail-actions");
  actions.append(
    button("承認", "secondary-button", () => updateReviewStatus("approved")),
    button("修正を再依頼", "primary-button", () => openReview("awaiting_codex")),
    button("保留", "secondary-button", () => updateReviewStatus("hold")),
  );
  node.append(status, note, actions);
  return node;
}

function renderDataSection(question) {
  const node = section("データ差分・ファイル");
  const diffDetails = document.createElement("details");
  diffDetails.append(element("summary", "", "field差分"));
  const diffContent = element("div", "details-content");
  const rows = [];
  for (const field of topLevelDifferences(question.source || {}, question.projected || {})) {
    rows.push(["source → projected", field]);
  }
  for (const field of topLevelDifferences(question.projected || {}, question.merged || {})) {
    rows.push(["projected → merged", field]);
  }
  for (const field of question.liveReadback?.differences || []) rows.push(["upload-ready → live", field]);
  if (rows.length) {
    const table = element("table", "diff-table");
    const head = document.createElement("tr");
    head.append(element("th", "", "比較"), element("th", "", "field"));
    table.append(head);
    for (const [stage, field] of rows) {
      const row = document.createElement("tr");
      row.append(element("td", "", stage), element("td", "", field));
      table.append(row);
    }
    diffContent.append(table);
  } else {
    diffContent.append(element("p", "", "検出されたfield差分はありません。"));
  }
  diffDetails.append(diffContent);

  const pathDetails = document.createElement("details");
  pathDetails.append(element("summary", "", "関連ファイル"));
  const pathContent = element("div", "details-content");
  const pathList = element("ul", "path-list");
  for (const [label, value] of Object.entries(question.paths || {})) {
    const values = Array.isArray(value) ? value : value ? [value] : [];
    for (const path of values) pathList.append(element("li", "", `${label}: ${path}`));
  }
  pathContent.append(pathList);
  pathDetails.append(pathContent);

  const rawDetails = document.createElement("details");
  rawDetails.append(element("summary", "", "投影後JSON"));
  const rawContent = element("div", "details-content");
  rawContent.append(jsonPre(question.projected));
  rawDetails.append(rawContent);
  node.append(diffDetails, pathDetails, rawDetails);
  return node;
}

function topLevelDifferences(left, right) {
  if (!left || !right) return ["対応データなし"];
  const ignored = new Set(["updatedAt", "createdAt"]);
  return [...new Set([...Object.keys(left), ...Object.keys(right)])]
    .filter((key) => !ignored.has(key) && JSON.stringify(left[key]) !== JSON.stringify(right[key]))
    .sort();
}

function jsonPre(value) {
  return element("pre", "", JSON.stringify(value, null, 2));
}

function normalizedReviewSelection(node, context, selectedText = "") {
  return {
    targetLabel: String(context.targetLabel || "表示箇所"),
    dataPath: String(context.dataPath || ""),
    fields: [...new Set((context.fields || []).map(String).filter(Boolean))],
    choiceIndexes: [...new Set((context.choiceIndexes || []).map(Number).filter(Number.isInteger))],
    selectedText: selectedText || String(context.selectedText || node.textContent || "").trim(),
  };
}

function installReviewTarget(node, context) {
  node.classList.add("reviewable-content");
  node.title = "文字列を選択してCodexに確認";
  reviewTargetContexts.set(node, context);
}

let selectionToolbarTimer = null;

function reviewTargetFromSelectionNode(node) {
  const elementNode = node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
  return elementNode?.closest?.(".reviewable-content") || null;
}

function selectionCandidate() {
  const selection = window.getSelection?.();
  const selectedText = selection?.toString().trim() || "";
  if (!selection || selection.isCollapsed || !selectedText) return null;
  const anchorTarget = reviewTargetFromSelectionNode(selection.anchorNode);
  const focusTarget = reviewTargetFromSelectionNode(selection.focusNode);
  if (!anchorTarget || anchorTarget !== focusTarget) return null;
  const context = reviewTargetContexts.get(anchorTarget);
  if (!context) return null;
  return {
    selection: normalizedReviewSelection(anchorTarget, context, selectedText),
    issueType: context.issueType || "",
  };
}

function scheduleSelectionToolbar() {
  window.clearTimeout(selectionToolbarTimer);
  selectionToolbarTimer = window.setTimeout(renderSelectionToolbar, 120);
}

function renderSelectionToolbar() {
  const candidate = selectionCandidate();
  const toolbar = $("#selection-toolbar");
  if (!candidate || document.querySelector("dialog[open]")) {
    if (!candidate && toolbar.matches(":focus-within")) return;
    toolbar.hidden = true;
    state.selectionCandidate = null;
    return;
  }
  state.selectionCandidate = candidate;
  $("#selection-toolbar-label").textContent = candidate.selection.targetLabel;
  $("#selection-toolbar-text").textContent = candidate.selection.selectedText;
  toolbar.hidden = false;
}

function clearSelectionToolbar(clearBrowserSelection = false) {
  window.clearTimeout(selectionToolbarTimer);
  $("#selection-toolbar").hidden = true;
  state.selectionCandidate = null;
  if (clearBrowserSelection) window.getSelection?.().removeAllRanges();
}

function openSelectionReview(investigationScope) {
  const candidate = state.selectionCandidate;
  if (!candidate) return;
  const { selection, issueType } = candidate;
  clearSelectionToolbar(true);
  openReview("awaiting_codex", selection, issueType, investigationScope);
}

function openReview(mode, selection = null, issueType = "", investigationScope = "current_question") {
  if (!state.detail) return;
  state.reviewMode = mode;
  state.reviewSelection = selection;
  $("#review-dialog-title").textContent = selection
    ? "選択した箇所の修正を依頼"
    : "修正を依頼";
  $("#review-submit").textContent = "依頼を作成してコピー";
  const firstIssue = issueType || state.detail.issueCodes[0] || "other";
  $("#review-issue").value = ISSUE_LABELS[firstIssue] ? firstIssue : "other";
  $("#review-note").value = "";
  $("#review-expected").value = "";
  $("#review-scope").value = REVIEW_SCOPES.has(investigationScope)
    ? investigationScope
    : "current_question";
  $("#review-scope-wrap").hidden = false;

  const selectionNode = $("#review-selection");
  selectionNode.hidden = !selection;
  $("#review-selection-label").textContent = selection?.targetLabel || "";
  $("#review-selection-path").textContent = selection?.dataPath ? `field: ${selection.dataPath}` : "";
  $("#review-selection-text").textContent = selection?.selectedText || "";

  const choiceList = $("#review-choice-list");
  choiceList.replaceChildren();
  const choices = state.detail.projected?.choiceTextList || [];
  const selectedChoices = new Set(selection?.choiceIndexes || []);
  choices.forEach((_, index) => choiceList.append(
    checkbox(`choice-${index}`, `選択肢${index + 1}`, String(index), selectedChoices.has(index)),
  ));

  const fieldList = $("#review-field-list");
  fieldList.replaceChildren();
  const selectedFields = new Set(selection?.fields || []);
  for (const field of [...new Set([...REVIEW_FIELDS, ...selectedFields])]) {
    fieldList.append(checkbox(`field-${field}`, field, field, selectedFields.has(field)));
  }
  $("#review-dialog").showModal();
  $("#review-note").focus();
}

function checkbox(id, label, value, checked = false) {
  const wrapper = document.createElement("label");
  const input = document.createElement("input");
  input.type = "checkbox";
  input.id = id;
  input.value = value;
  input.checked = checked;
  wrapper.append(input, document.createTextNode(label));
  return wrapper;
}

async function submitReview(event) {
  event.preventDefault();
  const choiceIndexes = [...$("#review-choice-list").querySelectorAll("input:checked")].map((node) => Number(node.value));
  const fields = [...$("#review-field-list").querySelectorAll("input:checked")].map((node) => node.value);
  try {
    const review = await api("/api/reviews", {
      method: "POST",
      body: {
        questionId: state.detail.id,
        status: state.reviewMode,
        review: {
          choiceIndexes,
          fields,
          issueTypes: [$("#review-issue").value],
          note: $("#review-note").value,
          expectedOutcome: $("#review-expected").value,
          selection: state.reviewSelection,
          investigationScope: $("#review-scope").value,
        },
      },
    });
    $("#review-dialog").close();
    if (state.reviewMode === "awaiting_codex") {
      await copyText(review.prompt);
      toast("指摘を記録し、Codex用依頼をクリップボードへコピーしました。");
    } else {
      toast("指摘を記録しました。");
    }
    await loadQuestions(true);
  } catch (error) {
    toast(error.message, true);
  }
}

async function copyText(value) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.append(textarea);
  textarea.select();
  document.execCommand("copy");
  textarea.remove();
}

async function updateReviewStatus(status) {
  if (!state.detail?.review) return;
  try {
    await api(`/api/reviews/${state.detail.review.reviewId}/status`, {
      method: "POST",
      body: { status },
    });
    toast(`レビューを「${REVIEW_LABELS[status]}」に更新しました。`);
    await loadQuestions(true);
  } catch (error) {
    toast(error.message, true);
  }
}

function openEdit() {
  if (!state.detail) return;
  const projected = state.detail.projected || {};
  const choices = projected.choiceTextList || [];
  const correctness = projected.correctChoiceText || [];
  const explanations = projected.explanationText || [];
  $("#edit-guidance").textContent = state.detail.isLawRelated
    ? "法令問題の正誤変更は根拠監査が必要なため、「修正を依頼」から行います。解説と補足質問は直接編集できます。"
    : "保存先は21_explanationText_addedと23_correctChoiceText_fixedです。00_sourceは変更しません。";
  const list = $("#edit-choice-list");
  list.replaceChildren();
  choices.forEach((choice, index) => {
    const row = element("div", "edit-choice");
    row.dataset.index = String(index);
    row.append(element("strong", "", `選択肢${index + 1}: ${choice}`));
    const selectLabel = document.createElement("label");
    selectLabel.append(element("span", "", "正誤"));
    const select = document.createElement("select");
    select.className = "edit-verdict";
    for (const value of ["正しい", "間違い"]) {
      const option = element("option", "", value);
      option.value = value;
      select.append(option);
    }
    select.value = normalizeVerdict(correctness[index]) || "間違い";
    select.disabled = state.detail.isLawRelated;
    selectLabel.append(select);
    const explanationLabel = document.createElement("label");
    explanationLabel.append(element("span", "", "基本解説"));
    const textarea = document.createElement("textarea");
    textarea.className = "edit-explanation";
    textarea.rows = 4;
    textarea.value = explanations[index] || "";
    explanationLabel.append(textarea);
    row.append(selectLabel, explanationLabel);
    list.append(row);
  });

  const questions = Array.isArray(projected.suggestedQuestions) ? projected.suggestedQuestions : [];
  const details = Array.isArray(projected.suggestedQuestionDetails) ? projected.suggestedQuestionDetails : [];
  state.editBaselinePairs = Array.from({ length: Math.max(questions.length, details.length) }, (_, index) => ({
    question: details[index]?.question || questions[index] || "",
    answer: details[index]?.answer || "",
  }));
  const suggestions = $("#suggestion-edit-list");
  suggestions.replaceChildren();
  for (const pair of state.editBaselinePairs) addSuggestionRow(pair.question, pair.answer);
  $("#edit-reason").value = "";
  $("#edit-dialog").showModal();
}

function addSuggestionRow(question, answer) {
  const row = element("div", "suggestion-edit-row");
  const questionInput = document.createElement("textarea");
  questionInput.className = "suggestion-question";
  questionInput.placeholder = "質問";
  questionInput.value = question;
  const answerInput = document.createElement("textarea");
  answerInput.className = "suggestion-answer";
  answerInput.placeholder = "回答";
  answerInput.value = answer;
  const remove = button("×", "icon-button", () => row.remove(), "補足質問を削除");
  row.append(questionInput, answerInput, remove);
  $("#suggestion-edit-list").append(row);
}

function collectEditChanges() {
  const projected = state.detail.projected || {};
  const verdicts = [...document.querySelectorAll(".edit-verdict")].map((node) => node.value);
  const explanations = [...document.querySelectorAll(".edit-explanation")].map((node) => node.value.trim());
  const pairs = [...document.querySelectorAll(".suggestion-edit-row")]
    .map((row) => ({
      question: row.querySelector(".suggestion-question").value.trim(),
      answer: row.querySelector(".suggestion-answer").value.trim(),
    }))
    .filter((pair) => pair.question || pair.answer);
  if (pairs.some((pair) => !pair.question || !pair.answer)) {
    throw new Error("補足質問は質問と回答を両方入力してください。");
  }
  const changes = {};
  const currentVerdicts = (projected.correctChoiceText || []).map(normalizeVerdict);
  if (!state.detail.isLawRelated && !same(verdicts, currentVerdicts)) {
    changes.correctChoiceText = verdicts;
  }
  if (!same(explanations, projected.explanationText || [])) changes.explanationText = explanations;
  if (!same(pairs, state.editBaselinePairs)) {
    changes.suggestedQuestions = pairs.map((pair) => pair.question);
    changes.suggestedQuestionDetails = pairs;
  }
  return changes;
}

function same(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

async function previewEdit(event) {
  event.preventDefault();
  try {
    const changes = collectEditChanges();
    const reason = $("#edit-reason").value;
    const preview = await api("/api/direct-edits/preview", {
      method: "POST",
      body: {
        questionId: state.detail.id,
        stateHash: state.detail.stateHash,
        changes,
        reason,
      },
    });
    state.pendingEdit = { changes, reason, preview };
    renderConfirmDiffs(preview.diffs, preview.validationWarnings || []);
    $("#confirm-dialog").showModal();
  } catch (error) {
    if (error.payload?.codexRequired) {
      switchEditToCodex(error.message);
      return;
    }
    toast(error.message, true);
  }
}

function renderConfirmDiffs(diffs, validationWarnings) {
  const validation = $("#confirm-validation");
  validation.replaceChildren();
  validation.hidden = !validationWarnings.length;
  if (validationWarnings.length) {
    validation.append(element("strong", "", "保存後も確認が必要な必須fieldがあります"));
    const list = document.createElement("ul");
    for (const warning of validationWarnings) {
      list.append(element("li", "", `${warning.field}: ${warning.detail}`));
    }
    validation.append(list);
  }
  const container = $("#confirm-diffs");
  container.replaceChildren();
  for (const diff of diffs) {
    const block = element("div", "confirm-diff");
    block.append(element("strong", "", diff.field));
    const values = element("div", "confirm-values");
    values.append(
      element("pre", "", `変更前\n${JSON.stringify(diff.before, null, 2)}`),
      element("pre", "", `変更後\n${JSON.stringify(diff.after, null, 2)}`),
    );
    block.append(values);
    container.append(block);
  }
}

async function applyEdit(event) {
  event.preventDefault();
  if (!state.pendingEdit) return;
  const pending = state.pendingEdit;
  try {
    const result = await api("/api/direct-edits/apply", {
      method: "POST",
      body: {
        questionId: state.detail.id,
        stateHash: state.detail.stateHash,
        changes: pending.changes,
        reason: pending.reason,
        previewToken: pending.preview.previewToken,
      },
    });
    state.pendingEdit = null;
    $("#confirm-dialog").close();
    $("#edit-dialog").close();
    state.detail = result.question;
    const requiredWarnings = (result.question?.issues || [])
      .filter((issue) => issue.code === "required_field_missing");
    toast(
      requiredWarnings.length
        ? `patchを更新しました。必須field不足 ${requiredWarnings.length}件を確認してください。`
        : `patchを更新しました: ${result.changedPaths.join(", ")}`,
      Boolean(requiredWarnings.length),
    );
    await loadQuestions(true);
  } catch (error) {
    if (error.payload?.codexRequired) {
      switchEditToCodex(error.message);
      return;
    }
    toast(error.message, true);
  }
}

function switchEditToCodex(message) {
  let changes = {};
  try { changes = collectEditChanges(); } catch (_) { changes = {}; }
  if ($("#confirm-dialog").open) $("#confirm-dialog").close();
  if ($("#edit-dialog").open) $("#edit-dialog").close();
  openReview("awaiting_codex");
  $("#review-note").value = `${message}\n直接編集で入力した内容を調査し、適切なpatchへ反映してほしい。`;
  $("#review-expected").value = JSON.stringify(changes, null, 2);
  for (const field of Object.keys(changes)) {
    const input = $(`#field-${CSS.escape(field)}`);
    if (input) input.checked = true;
  }
  toast("修正依頼に切り替えました。", true);
}

async function checkFingerprint() {
  if (!state.detail || document.hidden || document.querySelector("dialog[open]")) return;
  const current = state.detail;
  const params = new URLSearchParams({ qualification: current.qualification, listGroupId: current.listGroupId });
  try {
    const fingerprint = await api(`/api/questions/${current.id}/fingerprint?${params}`);
    if (
      fingerprint.stateHash !== current.stateHash
      || fingerprint.reviewStatus !== current.reviewStatus
      || !same(fingerprint.issueCodes || [], current.issueCodes || [])
    ) {
      toast("対象問題の更新を検出しました。");
      await loadQuestions(true);
    }
  } catch (_) {
    // 常時pollの一時的エラーは次回の取得で回復させる。
  }
}

initialize();
