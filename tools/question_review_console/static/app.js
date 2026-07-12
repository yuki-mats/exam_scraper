"use strict";

const ISSUE_LABELS = {
  live_mismatch: "Firestore差分",
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
  upstream_stale: "旧成果物と一致",
};

const EDITABLE_FIELDS = [
  "correctChoiceText",
  "explanationText",
  "suggestedQuestions",
  "suggestedQuestionDetails",
];

const state = {
  token: "",
  inventory: null,
  qualification: "",
  listGroupId: "",
  exceptionsOnly: true,
  questions: [],
  selectedId: "",
  detail: null,
  reviewMode: "needs_review",
  pendingEdit: null,
  editBaselinePairs: [],
  workflowDialog: { mode: "", preview: null, running: false },
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
  $("#add-suggestion").addEventListener("click", () => addSuggestionRow("", ""));
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
    button("指摘", "secondary-button", () => openReview("needs_review"), "要確認として記録"),
    button("Codex依頼", "primary-button", () => openReview("awaiting_codex"), "Codex用依頼を作成・コピー"),
    button("編集", "secondary-button", openEdit, "解説・正誤をpatchで修正"),
  );
  titleRow.append(titleBlock, actions);
  header.append(titleRow, renderWorkflow(question.workflow), renderPipelineActions(question));
  pane.append(header);

  const questionSection = section("問題文");
  questionSection.append(element("p", "question-body", question.body));
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

function section(title) {
  const node = element("section", "detail-section");
  node.append(element("h3", "", title));
  return node;
}

function renderWorkflow(workflow) {
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
    step.append(element("strong", "", name), element("span", "", WORKFLOW_LABELS[status] || status));
    node.append(step);
  }
  return node;
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
      element("span", "", "対象の年度・回だけをMerge、Convert、upload-readyまで再生成します。"),
    );
    actions.append(button("成果物を同期", "primary-button", openSyncDialog));
  } else {
    const messages = {
      match: ["本番Firestoreまで一致しています", "選択中の問題は最新upload-readyと一致しています。"],
      mismatch: ["本番Firestoreに差分があります", "年度・回全体の差分を確認してから本番へ反映できます。"],
      missing: ["本番Firestoreに未登録の問題があります", "年度・回全体の追加対象を確認できます。"],
      error: ["Firestoreの確認に失敗しました", "credentialと接続状態を確認してください。"],
      unread: ["ローカル成果物は最新です", "本番Firestoreはまだ確認していません。"],
      unavailable: ["Firestoreと比較できません", "upload-readyのdocument IDを確認してください。"],
    };
    const [title, detail] = messages[workflow.firestore] || messages.unread;
    status.append(element("strong", "", title), element("span", "", detail));
    actions.append(
      button("この問題を再読取", "secondary-button", runFirestoreReadback),
      button("本番差分を確認", "primary-button", openPublishDialog),
    );
  }
  node.append(status, actions);
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

async function openSyncDialog() {
  resetWorkflowDialog("sync", "成果物を同期");
  try {
    const preview = await api(groupApiPath("sync-preview"), { method: "POST", body: {} });
    state.workflowDialog.preview = preview;
    $("#workflow-dialog-message").textContent = preview.needsSync
      ? preview.allowMissingAnswerResult
        ? "既存workflowを実行します。回答結果が空の問題は、全選択肢の精査済み正誤を保持して検証します。00_sourceは変更しません。"
        : "既存workflowを対象年度・回だけ実行し、upload dry-runまで検証します。00_sourceは変更しません。"
      : "Merge、Convert、upload-readyはすでに最新です。";
    $("#workflow-dialog-summary").append(
      summaryMetric("対象", `${preview.qualification} / ${preview.listGroupId}`),
      summaryMetric("問題", `${preview.questionCount}問`),
      stageSummary(preview, "merge", "Merge"),
      stageSummary(preview, "convert", "Convert"),
      stageSummary(preview, "upload", "upload-ready"),
    );
    $("#workflow-execute").textContent = preview.needsSync ? "同期を実行" : "閉じる";
    $("#workflow-execute").disabled = false;
    if (!preview.needsSync) state.workflowDialog.mode = "";
  } catch (error) {
    showWorkflowError(error);
  }
}

async function openPublishDialog() {
  resetWorkflowDialog("publish", "本番Firestoreの差分確認");
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
      $("#workflow-dialog-message").textContent = "年度・回のupload-readyと本番Firestoreは一致しています。反映は不要です。";
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
  if (state.selectedId) {
    try {
      await api(`/api/questions/${state.selectedId}/live-readback`, { method: "POST", body: {} });
    } catch (_) {
      // 一覧再読込で取得失敗状態を表示するため、ここでは処理完了を妨げない。
    }
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
    indexNode.append(
      element("span", "", String(index + 1)),
      element("span", `verdict ${verdictValue === "正しい" ? "correct" : "incorrect"}`, rawVerdict),
    );
    card.append(
      indexNode,
      element("div", "choice-text", choice),
      element("div", "choice-explanation", explanations[index] || "（解説なし）"),
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
    row.append(
      element("th", "", detail.question || questions[index] || ""),
      element("td", "", detail.answer || "（回答なし）"),
    );
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
  content.append(jsonPre(projected.lawReferences || []));
  references.append(content);
  const facts = document.createElement("details");
  facts.append(element("summary", "", "lawRevisionFacts"));
  const factContent = element("div", "details-content");
  factContent.append(jsonPre(projected.lawRevisionFacts || []));
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
    button("再度Codexへ", "primary-button", () => openReview("awaiting_codex")),
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

function openReview(mode) {
  if (!state.detail) return;
  state.reviewMode = mode;
  $("#review-dialog-title").textContent = mode === "awaiting_codex" ? "Codex用依頼を作成" : "指摘を記録";
  $("#review-submit").textContent = mode === "awaiting_codex" ? "作成してコピー" : "記録";
  const firstIssue = state.detail.issueCodes[0] || "other";
  $("#review-issue").value = ISSUE_LABELS[firstIssue] ? firstIssue : "other";
  $("#review-note").value = "";
  $("#review-expected").value = "";

  const choiceList = $("#review-choice-list");
  choiceList.replaceChildren();
  const choices = state.detail.projected?.choiceTextList || [];
  choices.forEach((_, index) => choiceList.append(checkbox(`choice-${index}`, `選択肢${index + 1}`, String(index))));

  const fieldList = $("#review-field-list");
  fieldList.replaceChildren();
  for (const field of [...EDITABLE_FIELDS, "lawReferences", "lawRevisionFacts", "questionBodyText", "choiceTextList"]) {
    fieldList.append(checkbox(`field-${field}`, field, field));
  }
  $("#review-dialog").showModal();
}

function checkbox(id, label, value) {
  const wrapper = document.createElement("label");
  const input = document.createElement("input");
  input.type = "checkbox";
  input.id = id;
  input.value = value;
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
        },
      },
    });
    $("#review-dialog").close();
    if (state.reviewMode === "awaiting_codex") {
      await copyText(review.prompt);
      toast("Codex用依を作成し、クリップボードへコピーしました。");
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

async function runFirestoreReadback() {
  if (!state.detail) return;
  toast("対象documentを読み取っています。");
  try {
    const result = await api(`/api/questions/${state.detail.id}/live-readback`, {
      method: "POST",
      body: {},
    });
    const message = result.status === "match"
      ? `Firestoreと${result.expectedSource}は一致しています。`
      : result.error || `Firestore差分: ${result.differenceCount || result.missingDocumentIds?.length || 0}件`;
    toast(message, result.status === "error");
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
    ? "法令問題の正誤変更は根拠監査が必要なため、Codex依頼で行います。解説と補足質問は直接編集できます。"
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
    renderConfirmDiffs(preview.diffs);
    $("#confirm-dialog").showModal();
  } catch (error) {
    if (error.payload?.codexRequired) {
      switchEditToCodex(error.message);
      return;
    }
    toast(error.message, true);
  }
}

function renderConfirmDiffs(diffs) {
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
    toast(`patchを更新しました: ${result.changedPaths.join(", ")}`);
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
  toast("Codex依頼に切り替えました。", true);
}

async function checkFingerprint() {
  if (!state.detail || document.hidden || document.querySelector("dialog[open]")) return;
  const current = state.detail;
  const params = new URLSearchParams({ qualification: current.qualification, listGroupId: current.listGroupId });
  try {
    const fingerprint = await api(`/api/questions/${current.id}/fingerprint?${params}`);
    if (fingerprint.stateHash !== current.stateHash || fingerprint.reviewStatus !== current.reviewStatus) {
      toast("対象問題の更新を検出しました。");
      await loadQuestions(true);
    }
  } catch (_) {
    // 常時pollの一時的エラーは次回の取得で回復させる。
  }
}

initialize();
