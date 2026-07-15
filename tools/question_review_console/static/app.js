"use strict";

const ALL_LIST_GROUPS = "__all__";
const QUALIFICATION_PREVIEW_TIMEOUT_MS = 30000;

const ISSUE_LABELS = {
  live_mismatch: "Firestore差分",
  firestore_readback_stale: "Firestore再取得待ち",
  answer_explanation_mismatch: "正誤と解説の矛盾",
  required_field_missing: "必須field不足",
  law_audit_metadata_incomplete: "法令監査パッチ不完全",
  law_audit_verdict_mismatch: "法令監査判定の不一致",
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
  work_policy_outdated: "旧版工程あり",
  work_policy_unrecorded: "工程版未記録",
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

const EVALUATION_LABELS = {
  not_started: "評価待ち",
  running: "評価中",
  stale: "再評価が必要",
  needs_rework: "要再整備",
  passed: "公開可能",
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

const QUALIFICATION_WORKFLOW_LABELS = {
  ready: "完了",
  not_started: "未着手",
  in_progress: "作業中",
  attention: "要確認",
  waiting: "前工程待ち",
};

const QUALIFICATION_RUN_STATUS_LABELS = {
  queued: "実行待ち",
  running: "処理中",
  validating: "完了検証中",
  awaiting_changes: "Codex作業中",
  interrupted: "再開待ち",
  failed: "失敗",
  succeeded: "完了",
};

const FIELD_LABELS = {
  questionLabel: "問題番号",
  questionBodyText: "問題文",
  choiceTextList: "選択肢",
  correctChoiceText: "正誤",
  explanationText: "基本解説",
  questionType: "問題形式",
  questionIntent: "出題意図",
  questionSetId: "問題セットID",
  originalQuestionId: "元問題ID",
  original_question_id: "元問題ID",
  answer_result_text: "公式解答",
  isLawRelated: "法令関連",
  lawReferences: "条文根拠",
  lawRevisionFacts: "法令監査情報",
  lawTitle: "法令名",
  lawAlias: "法令別名",
  article: "条",
  paragraph: "項",
  item: "号",
  subitem: "枝番",
  role: "根拠の役割",
  referenceDate: "基準日",
  verificationStatus: "検証状態",
  comparisonStatus: "現行法との比較",
  reason: "判断理由",
  sourceUrl: "参照先",
  apiUrl: "API参照先",
  auditStatus: "監査結果",
  reviewState: "レビュー状態",
  auditedAt: "監査日時",
  nextAuditDueAt: "次回監査期限",
  auditMethodVersion: "監査方式",
  reconciliationStatus: "照合状態",
  examTime: "出題時点",
  current: "現行法",
  differenceFacts: "条文差分",
  answerImpactFacts: "正誤への影響",
  evidenceSummary: "根拠要約",
  verdict: "判定",
  differenceSummary: "差分の要約",
  promptContext: "AIへの判断範囲",
  notes: "監査メモ",
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
];

const REVIEW_SCOPES = new Set([
  "current_question",
  "current_group",
  "qualification",
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
  qualificationWorkflow: null,
  qualificationWorkflowStageId: "",
  qualificationRuns: [],
  qualificationActiveRun: null,
  qualificationRunDialog: {
    preview: null,
    running: false,
    previewSequence: 0,
    resumedFrom: "",
    stageIds: [],
    listGroupIds: [],
    previewController: null,
  },
  workflowGuide: {
    open: false,
    stageId: "",
    documentPath: "",
    documents: new Map(),
    refreshTimer: null,
    refreshing: false,
    returnToRun: false,
    tabSignature: "",
  },
  questionPage: { filteredCount: 0, hasMore: false, limit: 50 },
  reviewMode: "awaiting_codex",
  reviewRequestKind: "",
  reviewSelection: null,
  selectionCandidate: null,
  pendingEdit: null,
  editBaselinePairs: [],
  workflowDialog: { mode: "", preview: null, running: false },
  readbackDialog: { preview: null, running: false, requestSequence: 0 },
  selectedQuestionIds: new Set(),
  evaluationEnabled: false,
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

function helpIcon(title, content, ariaLabel = "説明") {
  const help = button("?", "help-button", (event) => {
    event.preventDefault();
    event.stopPropagation();
    openHelp(title, content);
  }, ariaLabel);
  help.setAttribute("aria-label", ariaLabel);
  return help;
}

async function api(path, options = {}) {
  const request = {
    method: options.method || "GET",
    headers: { Accept: "application/json" },
    cache: "no-store",
  };
  if (options.body !== undefined) {
    request.headers["Content-Type"] = "application/json";
    request.headers["X-Review-Session"] = state.token;
    request.body = JSON.stringify(options.body);
  }
  if (options.signal) request.signal = options.signal;
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
    const [session, inventory, codexStatus] = await Promise.all([
      api("/api/session"),
      api("/api/inventory"),
      api("/api/codex/status"),
    ]);
    state.token = session.sessionToken;
    state.inventory = inventory;
    state.evaluationEnabled = session.evaluationEnabled === true && codexStatus.allowed === true;
    $("#project-status").textContent = state.evaluationEnabled
      ? `Codex App Server: ChatGPT ${codexStatus.planType} / Standard ・ Firestore: ${session.projectId}`
      : `Codex App Server: 開始不可 ・ ${codexStatus.reason || "状態を確認できません"}`;
    initializeSelectors();
    await loadQualificationWorkflow(false);
    await loadQualificationRuns();
    await loadQuestions(false);
    window.setInterval(checkFingerprint, 2000);
  } catch (error) {
    toast(error.message, true);
    setLoading("起動に失敗しました");
  }
}

function bindControls() {
  $("#qualification-select").addEventListener("change", async (event) => {
    closeWorkflowGuide({ reopenRun: false });
    clearEvaluationSelection();
    state.qualification = event.target.value;
    state.listGroupId = ALL_LIST_GROUPS;
    populateGroups();
    await loadQualificationWorkflow(false);
    await loadQualificationRuns();
    await loadQuestions(false);
    updateUrl();
  });
  $("#group-select").addEventListener("change", async (event) => {
    clearEvaluationSelection();
    state.listGroupId = event.target.value;
    await loadQuestions(false);
    updateUrl();
  });
  let searchTimer;
  $("#search-input").addEventListener("input", () => {
    clearEvaluationSelection();
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => loadQuestions(false), 220);
  });
  $("#exceptions-button").addEventListener("click", () => setListMode(true));
  $("#all-button").addEventListener("click", () => setListMode(false));
  $("#refresh-button").addEventListener("click", async () => {
    clearEvaluationSelection();
    await loadQualificationWorkflow(true);
    await loadQualificationRuns();
    await loadQuestions(true);
  });
  $("#qualification-workflow-action").addEventListener("click", executeQualificationWorkflowAction);
  $("#qualification-workflow-guide").addEventListener("click", () => openWorkflowGuide());
  $("#qualification-active-run-action").addEventListener("click", resumeQualificationRun);
  $("#qualification-run-form").addEventListener("submit", startQualificationRun);
  $("#qualification-run-guide").addEventListener("click", openQualificationRunGuide);
  $("#qualification-run-groups-all").addEventListener("click", () => setQualificationRunGroupSelection(true));
  $("#qualification-run-groups-clear").addEventListener("click", () => setQualificationRunGroupSelection(false));
  $("#qualification-run-dialog").addEventListener("cancel", (event) => {
    if (state.qualificationRunDialog.running) event.preventDefault();
  });
  $("#qualification-run-dialog").addEventListener("close", cancelQualificationRunPreview);
  for (const node of document.querySelectorAll('input[name="qualification-run-mode"]')) {
    node.addEventListener("change", previewQualificationRun);
  }
  $("#workflow-guide-close").addEventListener("click", closeWorkflowGuide);
  $("#workflow-guide-backdrop").addEventListener("click", closeWorkflowGuide);
  $("#workflow-guide-action").addEventListener("click", executeWorkflowGuideAction);
  $("#workflow-guide-content").addEventListener("click", openLinkedWorkflowDocument);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.workflowGuide.open) {
      event.preventDefault();
      closeWorkflowGuide();
    }
  });
  $("#load-more-questions").addEventListener("click", () => loadQuestions(true, true));
  $("#bulk-readback-button").addEventListener("click", openReadbackDialog);
  $("#bulk-readback-help").addEventListener("click", () => openHelp(
    "資格のFirestoreを確認",
    "選択中の資格に含まれる全フォルダを本番Firestoreから読み取ります。書き込みは行いません。取得結果と取得日時はローカルに保存され、後から問題ごとの差分を確認できます。",
  ));
  for (const selector of ["#law-only", "#firestore-mismatch", "#issue-select", "#review-status-select", "#evaluation-status-select", "#work-version-select"]) {
    $(selector).addEventListener("change", () => {
      clearEvaluationSelection();
      loadQuestions(false);
    });
  }
  $("#select-visible").addEventListener("change", toggleVisibleQuestionSelection);
  $("#bulk-evaluate-button").addEventListener("click", () => {
    openEvaluationDialog([...state.selectedQuestionIds]);
  });
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
    const option = element("option", "", qualification.displayName || qualification.id);
    option.value = qualification.id;
    option.title = qualification.id;
    select.append(option);
  }
  select.value = state.qualification;
  populateGroups(params.get("listGroupId"));
}

function qualificationDisplayName(qualificationId = state.qualification) {
  return state.inventory.qualifications?.find((item) => item.id === qualificationId)?.displayName
    || qualificationId
    || "";
}

function populateGroups(requested = null) {
  const qualification = state.inventory.qualifications.find((item) => item.id === state.qualification);
  const groups = qualification?.listGroupIds || [];
  $("#group-select-label").textContent = scopeLabelForGroups(groups);
  state.listGroupId = requested === ALL_LIST_GROUPS
    ? ALL_LIST_GROUPS
    : groups.includes(requested)
    ? requested
    : state.listGroupId === ALL_LIST_GROUPS || groups.includes(state.listGroupId)
      ? state.listGroupId
      : ALL_LIST_GROUPS;
  const select = $("#group-select");
  select.replaceChildren();
  const allOption = element("option", "", `すべて（${groups.length}件）`);
  allOption.value = ALL_LIST_GROUPS;
  select.append(allOption);
  for (const group of groups) {
    const option = element("option", "", group);
    option.value = group;
    select.append(option);
  }
  select.value = state.listGroupId;
}

function scopeLabelForGroups(groupIds) {
  if (groupIds.length && groupIds.every((value) => /^(?:19|20)\d{2}$/.test(value))) {
    return "年度";
  }
  if (groupIds.length && groupIds.every((value) => /^(?:19|20)\d{2}(?:01|02)$/.test(value))) {
    return "年度・区分";
  }
  return "フォルダ";
}

function updateUrl() {
  const params = new URLSearchParams();
  if (state.qualification) params.set("qualification", state.qualification);
  if (state.listGroupId) params.set("listGroupId", state.listGroupId);
  history.replaceState(null, "", `${location.pathname}?${params}`);
}

async function loadQualificationWorkflow(preserveSelection = true, quiet = false) {
  if (!state.qualification) return;
  if (!quiet) {
    $("#qualification-workflow-status").textContent = "確認中";
    $("#qualification-workflow-action").disabled = true;
    $("#qualification-workflow-guide").disabled = true;
  }
  try {
    const params = new URLSearchParams({ qualification: state.qualification });
    const workflow = await api(`/api/qualification-workflow?${params}`);
    state.qualificationWorkflow = workflow;
    const selectionExists = workflow.stages.some(
      (stage) => stage.id === state.qualificationWorkflowStageId,
    );
    if (!preserveSelection || !selectionExists) {
      state.qualificationWorkflowStageId = workflow.nextStageId
        || workflow.stages[workflow.stages.length - 1]?.id
        || "";
    }
    renderQualificationWorkflow();
  } catch (error) {
    if (quiet) return;
    state.qualificationWorkflow = null;
    $("#qualification-workflow-status").textContent = "取得失敗";
    $("#qualification-workflow-title").textContent = qualificationDisplayName() || "問題整備の流れ";
    $("#qualification-workflow-next").textContent = error.message;
    $("#qualification-workflow-stages").replaceChildren();
    $("#qualification-workflow-detail").hidden = true;
  }
}

function revealSelectedQualificationStage(stageList) {
  const selected = stageList.querySelector(".qualification-stage.selected");
  if (!selected || stageList.scrollWidth <= stageList.clientWidth) return;
  const listRect = stageList.getBoundingClientRect();
  const selectedRect = selected.getBoundingClientRect();
  if (selectedRect.left >= listRect.left && selectedRect.right <= listRect.right) return;
  stageList.scrollLeft += selectedRect.left
    - listRect.left
    - (listRect.width - selectedRect.width) / 2;
}

function renderQualificationWorkflow() {
  const workflow = state.qualificationWorkflow;
  if (!workflow) return;
  const nextStage = workflow.stages.find((stage) => stage.id === workflow.nextStageId);
  const selectedStage = workflow.stages.find(
    (stage) => stage.id === state.qualificationWorkflowStageId,
  ) || nextStage || workflow.stages[0];
  const workVersionLabel = $("#work-version-label");
  const workVersionSelect = $("#work-version-select");
  if (workVersionLabel) {
    workVersionLabel.textContent = selectedStage?.policyVersion
      ? `${selectedStage.code} v${selectedStage.policyVersion}`
      : "作業バージョン";
  }
  if (workVersionSelect) {
    workVersionSelect.disabled = !selectedStage?.policyVersion;
    if (workVersionSelect.disabled) workVersionSelect.value = "";
  }
  const overallLabel = workflow.overallStatus === "ready"
    ? "ローカル整備完了"
    : workflow.overallStatus === "attention"
      ? "確認が必要"
      : "整備中";
  $("#qualification-workflow-status").textContent = overallLabel;
  $("#qualification-workflow-status").className = `workflow-overall-status ${workflow.overallStatus}`;
  $("#qualification-workflow-title").textContent = qualificationDisplayName(workflow.qualification);
  $("#qualification-workflow-next").textContent = nextStage
    ? `次は ${nextStage.code} ${nextStage.label}：${nextStage.missingSummary}`
    : "すべてのローカル工程が整っています。";
  $("#qualification-workflow-progress").textContent = `${workflow.summary.readyStageCount}/${workflow.summary.stageCount}`;
  $("#qualification-workflow-questions").textContent = `${workflow.summary.questionCount}問`;
  $("#qualification-workflow-issues").textContent = `${workflow.summary.issueQuestionCount}問`;

  const stageList = $("#qualification-workflow-stages");
  stageList.style.setProperty("--workflow-stage-count", String(workflow.stages.length));
  stageList.replaceChildren();
  for (const stage of workflow.stages) {
    const item = element(
      "button",
      `qualification-stage ${stage.status}${stage.id === selectedStage?.id ? " selected" : ""}`,
    );
    item.type = "button";
    item.dataset.stageId = stage.id;
    item.setAttribute("role", "listitem");
    item.setAttribute("aria-pressed", String(stage.id === selectedStage?.id));
    item.title = stage.purpose;
    const head = element("span", "qualification-stage-head");
    head.append(
      element("span", "qualification-stage-code", stage.code),
      element("strong", "", `${stage.label}${stage.policyVersion ? ` v${stage.policyVersion}` : ""}`),
    );
    const progress = stage.status === "waiting"
      ? QUALIFICATION_WORKFLOW_LABELS.waiting
      : stage.targetCount
        ? `${stage.completeCount}/${stage.targetCount}`
        : QUALIFICATION_WORKFLOW_LABELS[stage.status];
    item.append(
      head,
      element("span", `qualification-stage-state ${stage.status}`, QUALIFICATION_WORKFLOW_LABELS[stage.status] || stage.status),
      element("span", "qualification-stage-progress", progress),
    );
    item.addEventListener("click", () => {
      const versionFilterWasActive = Boolean($("#work-version-select")?.value);
      state.qualificationWorkflowStageId = stage.id;
      renderQualificationWorkflow();
      if (state.workflowGuide.open) openWorkflowGuide(stage.id);
      if (versionFilterWasActive) loadQuestions(false);
    });
    stageList.append(item);
  }
  revealSelectedQualificationStage(stageList);

  const detail = $("#qualification-workflow-detail");
  detail.hidden = !selectedStage;
  if (!selectedStage) return;
  $("#qualification-workflow-stage-title").textContent = `${selectedStage.code} ${selectedStage.label}`;
  $("#qualification-workflow-stage-purpose").textContent = selectedStage.purpose;
  $("#qualification-workflow-stage-count").textContent = selectedStage.missingSummary;
  const guide = $("#qualification-workflow-guide");
  guide.disabled = !selectedStage.canonicalDocs?.length;
  guide.dataset.stageId = selectedStage.id;
  const action = $("#qualification-workflow-action");
  action.textContent = selectedStage.action.label;
  action.disabled = selectedStage.action.type === "none";
  action.dataset.action = selectedStage.action.type;
  action.dataset.stageId = selectedStage.id;
}

function qualificationWorkflowStage(stageId = "") {
  const workflow = state.qualificationWorkflow;
  if (!workflow) return null;
  const selectedId = stageId || state.qualificationWorkflowStageId;
  return workflow.stages.find((stage) => stage.id === selectedId)
    || workflow.stages.find((stage) => stage.id === workflow.nextStageId)
    || workflow.stages[0]
    || null;
}

async function openWorkflowGuide(stageId = "", options = {}) {
  const stage = qualificationWorkflowStage(stageId);
  if (!stage) return;
  const wasOpen = state.workflowGuide.open;
  if (Object.prototype.hasOwnProperty.call(options, "returnToRun")) {
    state.workflowGuide.returnToRun = Boolean(options.returnToRun);
  } else if (!wasOpen) {
    state.workflowGuide.returnToRun = false;
  }
  state.workflowGuide.open = true;
  state.workflowGuide.stageId = stage.id;
  state.qualificationWorkflowStageId = stage.id;
  const documentPaths = stage.canonicalDocs || [];
  if (!documentPaths.includes(state.workflowGuide.documentPath)) {
    state.workflowGuide.documentPath = documentPaths[0] || "";
  }
  $("#workflow-guide").hidden = false;
  $("#workflow-guide-backdrop").hidden = false;
  document.documentElement.classList.add("workflow-guide-open");
  document.body.classList.add("workflow-guide-open");
  renderWorkflowGuideContext();
  renderWorkflowGuideDocuments();
  $("#workflow-guide-content").replaceChildren(
    element("p", "markdown-loading", "正本を読み込んでいます。"),
  );
  await refreshWorkflowGuideDocuments();
  window.clearInterval(state.workflowGuide.refreshTimer);
  state.workflowGuide.refreshTimer = window.setInterval(refreshWorkflowGuide, 2000);
  if (!wasOpen) $("#workflow-guide-close").focus();
}

function closeWorkflowGuide(options = {}) {
  if (!state.workflowGuide.open) return;
  const shouldReturn = state.workflowGuide.returnToRun && options.reopenRun !== false;
  window.clearInterval(state.workflowGuide.refreshTimer);
  state.workflowGuide.refreshTimer = null;
  state.workflowGuide.open = false;
  state.workflowGuide.returnToRun = false;
  $("#workflow-guide").hidden = true;
  $("#workflow-guide-backdrop").hidden = true;
  document.documentElement.classList.remove("workflow-guide-open");
  document.body.classList.remove("workflow-guide-open");
  if (shouldReturn && !$("#qualification-run-dialog").open) {
    $("#qualification-run-dialog").showModal();
  }
}

function renderWorkflowGuideContext() {
  const stage = qualificationWorkflowStage(state.workflowGuide.stageId);
  if (!stage) return;
  const statusLabel = QUALIFICATION_WORKFLOW_LABELS[stage.status] || stage.status;
  $("#workflow-guide-title").textContent = `${stage.code} ${stage.label}`;
  $("#workflow-guide-status").textContent = statusLabel;
  $("#workflow-guide-status").className = `workflow-overall-status ${stage.status}`;
  $("#workflow-guide-purpose").textContent = stage.purpose;
  $("#workflow-guide-missing").textContent = stage.missingSummary;
  const action = $("#workflow-guide-action");
  action.textContent = stage.action.label;
  action.disabled = stage.action.type === "none";
}

function workflowDocumentLabel(path) {
  const loaded = state.workflowGuide.documents.get(path);
  if (loaded?.title) return loaded.title;
  if (path === "document/operations/exam_pipeline_manual_and_automation.md") {
    return "全体フロー";
  }
  if (path === "prompt/README.md") return "工程入口";
  const filename = path.split("/").pop() || path;
  return filename.replace(/\.md$/i, "").replaceAll("_", " ");
}

function workflowGuideDocumentPaths() {
  const stage = qualificationWorkflowStage(state.workflowGuide.stageId);
  const paths = [...(stage?.canonicalDocs || [])];
  if (state.workflowGuide.documentPath && !paths.includes(state.workflowGuide.documentPath)) {
    paths.push(state.workflowGuide.documentPath);
  }
  return paths;
}

function renderWorkflowGuideDocuments() {
  const paths = workflowGuideDocumentPaths();
  const navigation = $("#workflow-guide-documents");
  navigation.replaceChildren();
  for (const path of paths) {
    const item = button(
      workflowDocumentLabel(path),
      `workflow-guide-document${path === state.workflowGuide.documentPath ? " selected" : ""}`,
      async () => {
        state.workflowGuide.documentPath = path;
        renderWorkflowGuideDocuments();
        renderWorkflowGuideDocument();
        await refreshWorkflowGuideDocuments(true);
      },
      path,
    );
    item.setAttribute("aria-pressed", String(path === state.workflowGuide.documentPath));
    navigation.append(item);
  }
  state.workflowGuide.tabSignature = JSON.stringify(
    paths.map((path) => [path, workflowDocumentLabel(path)]),
  );
}

async function refreshWorkflowGuideDocuments(currentOnly = false) {
  const stage = qualificationWorkflowStage(state.workflowGuide.stageId);
  if (!stage || !state.workflowGuide.open) return;
  const paths = workflowGuideDocumentPaths();
  const fetchPaths = currentOnly && state.workflowGuide.documentPath
    ? [state.workflowGuide.documentPath]
    : paths;
  const previousPath = state.workflowGuide.documentPath;
  const previous = state.workflowGuide.documents.get(previousPath);
  const results = await Promise.all(fetchPaths.map(async (path) => {
    try {
      const query = new URLSearchParams({ path });
      return [path, await api(`/api/document?${query}`)];
    } catch (error) {
      return [path, { path, error: error.message }];
    }
  }));
  for (const [path, payload] of results) {
    state.workflowGuide.documents.set(path, payload);
  }
  if (!currentOnly) {
    for (const path of [...state.workflowGuide.documents.keys()]) {
      if (!paths.includes(path)) state.workflowGuide.documents.delete(path);
    }
  }
  if (!state.workflowGuide.documentPath) state.workflowGuide.documentPath = paths[0] || "";
  const currentTabs = JSON.stringify(
    paths.map((path) => [path, workflowDocumentLabel(path)]),
  );
  if (state.workflowGuide.tabSignature !== currentTabs) renderWorkflowGuideDocuments();
  const current = state.workflowGuide.documents.get(state.workflowGuide.documentPath);
  if (
    previousPath !== state.workflowGuide.documentPath
    || previous?.contentHash !== current?.contentHash
    || previous?.error !== current?.error
    || $("#workflow-guide-content").querySelector(".markdown-loading")
  ) {
    renderWorkflowGuideDocument();
  }
}

async function refreshWorkflowGuide() {
  if (!state.workflowGuide.open || state.workflowGuide.refreshing) return;
  state.workflowGuide.refreshing = true;
  try {
    const query = new URLSearchParams({ qualification: state.qualification });
    const catalog = await api(`/api/workflow-catalog?${query}`);
    const catalogChanged = catalog.catalogHash !== state.qualificationWorkflow?.catalogHash;
    if (catalogChanged) {
      await loadQualificationWorkflow(true, true);
      const stage = qualificationWorkflowStage(state.workflowGuide.stageId);
      state.workflowGuide.stageId = stage?.id || "";
      renderQualificationWorkflow();
      renderWorkflowGuideContext();
    }
    await refreshWorkflowGuideDocuments(!catalogChanged);
  } catch (error) {
    $("#workflow-guide-updated").textContent = `再読込できません: ${error.message}`;
  } finally {
    state.workflowGuide.refreshing = false;
  }
}

function renderWorkflowGuideDocument() {
  const container = $("#workflow-guide-content");
  const payload = state.workflowGuide.documents.get(state.workflowGuide.documentPath);
  if (!state.workflowGuide.documentPath) {
    container.replaceChildren(element("p", "markdown-loading", "この工程に正本文書がありません。"));
    $("#workflow-guide-updated").textContent = "工程カタログを確認してください。";
    return;
  }
  if (!payload) return;
  if (payload.error) {
    container.replaceChildren(element("p", "markdown-error", payload.error));
    $("#workflow-guide-updated").textContent = state.workflowGuide.documentPath;
    return;
  }
  renderMarkdownDocument(container, payload.content, payload.path);
  const modified = new Date(payload.modifiedAt).toLocaleString("ja-JP", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
  $("#workflow-guide-updated").textContent = `${payload.path} ・ 更新 ${modified}`;
}

function openQualificationRunGuide() {
  const dialog = $("#qualification-run-dialog");
  if (dialog.open) dialog.close();
  openWorkflowGuide(state.qualificationWorkflowStageId, { returnToRun: true });
}

function executeWorkflowGuideAction() {
  const stage = qualificationWorkflowStage(state.workflowGuide.stageId);
  if (!stage || stage.action.type !== "open_run") return;
  closeWorkflowGuide({ reopenRun: false });
  openQualificationRunDialog(stage);
}

function openLinkedWorkflowDocument(event) {
  const link = event.target.closest("a[data-document-path]");
  if (!link) return;
  event.preventDefault();
  state.workflowGuide.documentPath = link.dataset.documentPath;
  renderWorkflowGuideDocuments();
  const payload = state.workflowGuide.documents.get(state.workflowGuide.documentPath);
  if (payload) {
    renderWorkflowGuideDocument();
  }
  refreshWorkflowGuideDocuments(true);
}

function resolveWorkflowDocumentPath(currentPath, rawTarget) {
  const target = rawTarget.trim().replace(/^<|>$/g, "");
  if (!target || /^(?:https?:|mailto:)/i.test(target)) return null;
  const [targetPath, anchor = ""] = target.split("#", 2);
  if (!targetPath) return { path: currentPath, anchor };
  if (targetPath.startsWith("/")) return null;
  const parts = currentPath.split("/");
  parts.pop();
  for (const part of targetPath.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") parts.pop();
    else parts.push(part);
  }
  return { path: parts.join("/"), anchor };
}

function appendInlineMarkdown(container, text, currentPath) {
  const tokenPattern = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/g;
  let cursor = 0;
  for (const match of text.matchAll(tokenPattern)) {
    if (match.index > cursor) container.append(document.createTextNode(text.slice(cursor, match.index)));
    const token = match[0];
    if (token.startsWith("`")) {
      container.append(element("code", "", token.slice(1, -1)));
    } else if (token.startsWith("**")) {
      container.append(element("strong", "", token.slice(2, -2)));
    } else {
      const linkMatch = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      const label = linkMatch?.[1] || token;
      const target = linkMatch?.[2] || "";
      const resolved = resolveWorkflowDocumentPath(currentPath, target);
      if (resolved?.path.endsWith(".md")) {
        const link = element("a", "", label);
        link.href = "#";
        link.dataset.documentPath = resolved.path;
        container.append(link);
      } else if (/^(?:https?:|mailto:)/i.test(target)) {
        const link = element("a", "", label);
        link.href = target;
        if (/^https?:/i.test(target)) {
          link.target = "_blank";
          link.rel = "noreferrer";
        }
        container.append(link);
      } else {
        container.append(element("span", "", label));
      }
    }
    cursor = match.index + token.length;
  }
  if (cursor < text.length) container.append(document.createTextNode(text.slice(cursor)));
}

function markdownCells(line) {
  return line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
}

function isMarkdownBlockStart(lines, index) {
  const line = lines[index] || "";
  return !line.trim()
    || /^```/.test(line)
    || /^#{1,6}\s+/.test(line)
    || /^\s*(?:[-*+] |\d+\. )/.test(line)
    || /^>\s?/.test(line)
    || /^\s*(?:---+|\*\*\*+)\s*$/.test(line)
    || (line.includes("|") && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1] || ""));
}

function renderMarkdownDocument(container, content, currentPath) {
  const lines = content.replaceAll("\r\n", "\n").split("\n");
  container.replaceChildren();
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    const fence = line.match(/^```\s*([^\s]*)/);
    if (fence) {
      const codeLines = [];
      index += 1;
      while (index < lines.length && !/^```/.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      index += 1;
      const pre = element("pre", "");
      const code = element("code", fence[1] ? `language-${fence[1]}` : "", codeLines.join("\n"));
      pre.append(code);
      container.append(pre);
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const node = element(`h${heading[1].length}`, "");
      appendInlineMarkdown(node, heading[2], currentPath);
      container.append(node);
      index += 1;
      continue;
    }
    if (/^\s*(?:---+|\*\*\*+)\s*$/.test(line)) {
      container.append(element("hr", ""));
      index += 1;
      continue;
    }
    if (line.includes("|") && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1] || "")) {
      const table = element("table", "");
      const thead = element("thead", "");
      const headerRow = element("tr", "");
      for (const cell of markdownCells(line)) {
        const th = element("th", "");
        appendInlineMarkdown(th, cell, currentPath);
        headerRow.append(th);
      }
      thead.append(headerRow);
      table.append(thead);
      index += 2;
      const tbody = element("tbody", "");
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        const row = element("tr", "");
        for (const cell of markdownCells(lines[index])) {
          const td = element("td", "");
          appendInlineMarkdown(td, cell, currentPath);
          row.append(td);
        }
        tbody.append(row);
        index += 1;
      }
      table.append(tbody);
      container.append(table);
      continue;
    }
    const listMatch = line.match(/^\s*([-*+]|\d+\.)\s+(.+)$/);
    if (listMatch) {
      const ordered = /\d+\./.test(listMatch[1]);
      const list = element(ordered ? "ol" : "ul", "");
      while (index < lines.length) {
        const itemMatch = lines[index].match(/^\s*([-*+]|\d+\.)\s+(.+)$/);
        if (!itemMatch || /\d+\./.test(itemMatch[1]) !== ordered) break;
        const item = element("li", "");
        appendInlineMarkdown(item, itemMatch[2], currentPath);
        list.append(item);
        index += 1;
      }
      container.append(list);
      continue;
    }
    if (/^>\s?/.test(line)) {
      const quote = element("blockquote", "");
      const quoteLines = [];
      while (index < lines.length && /^>\s?/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^>\s?/, ""));
        index += 1;
      }
      appendInlineMarkdown(quote, quoteLines.join(" "), currentPath);
      container.append(quote);
      continue;
    }
    const paragraphLines = [line.trim()];
    index += 1;
    while (index < lines.length && !isMarkdownBlockStart(lines, index)) {
      paragraphLines.push(lines[index].trim());
      index += 1;
    }
    const paragraph = element("p", "");
    appendInlineMarkdown(paragraph, paragraphLines.join(" "), currentPath);
    container.append(paragraph);
  }
}

async function executeQualificationWorkflowAction() {
  const workflow = state.qualificationWorkflow;
  if (!workflow) return;
  const stageId = $("#qualification-workflow-action").dataset.stageId;
  const stage = workflow.stages.find((item) => item.id === stageId);
  if (!stage || stage.action.type !== "open_run") return;
  openQualificationRunDialog(stage);
}

async function loadQualificationRuns() {
  if (!state.qualification) return;
  try {
    const params = new URLSearchParams({ qualification: state.qualification });
    const payload = await api(`/api/qualification-runs?${params}`);
    state.qualificationRuns = payload.runs || [];
    state.qualificationActiveRun = payload.activeRun || null;
  } catch (error) {
    state.qualificationRuns = [];
    state.qualificationActiveRun = null;
  }
  renderQualificationActiveRun();
}

function renderQualificationActiveRun() {
  const run = state.qualificationActiveRun;
  const container = $("#qualification-active-run");
  const history = $("#qualification-run-history");
  const historyList = $("#qualification-run-history-list");
  history.hidden = !state.qualificationRuns.length;
  historyList.replaceChildren();
  for (const item of state.qualificationRuns) {
    const row = element("div", "qualification-run-history-row");
    row.append(
      element("span", `run-status ${item.status}`, QUALIFICATION_RUN_STATUS_LABELS[item.status] || item.status),
      element("strong", "", `${item.stageCode} ${item.stageLabel}`),
      element("span", "", item.modeLabel),
      element("span", "", item.kind === "machine"
        ? `${item.targetCount}フォルダ`
        : `${item.targetCount}問 × ${item.stageIds?.length || 1}工程`),
      element("time", "", new Date(item.updatedAt).toLocaleString("ja-JP", { dateStyle: "short", timeStyle: "short" })),
    );
    historyList.append(row);
  }
  container.hidden = !run;
  if (!run) return;
  const status = QUALIFICATION_RUN_STATUS_LABELS[run.status] || run.status;
  $("#qualification-active-run-status").textContent = status;
  $("#qualification-active-run-status").className = `run-status ${run.status}`;
  $("#qualification-active-run-title").textContent = `${run.stageCode} ${run.stageLabel}・${run.modeLabel}`;
  const completed = (run.completedGroupIds || []).length;
  $("#qualification-active-run-progress").textContent = run.kind === "machine"
    ? `${completed}/${run.targetCount}フォルダ`
    : `${run.targetCount}${["refresh", "group_refresh"].includes(run.mode) ? "問すべて" : "問"} × ${run.stageIds?.length || 1}工程を依頼済み`;
  const action = $("#qualification-active-run-action");
  action.textContent = ["queued", "running", "validating"].includes(run.status)
    ? "進捗を見る"
    : run.kind === "human"
      ? "新規threadで再実行"
      : "残りを再開";
}

function selectedQualificationRunMode() {
  return document.querySelector('input[name="qualification-run-mode"]:checked')?.value || "remaining";
}

function selectedQualificationRunStageIds() {
  const inputs = [...document.querySelectorAll('input[name="qualification-run-stage"]')];
  if (!inputs.length) return [...state.qualificationRunDialog.stageIds];
  return inputs.filter((node) => node.checked)
    .map((node) => node.value);
}

function qualificationRunSupportsGroupScope(stage) {
  return Boolean(stage?.batchSelectable || stage?.id === "delivery");
}

function qualificationRunGroupIds() {
  return state.qualificationWorkflow?.groups?.map((group) => group.listGroupId) || [];
}

function selectedQualificationRunListGroupIds() {
  const inputs = [...document.querySelectorAll('input[name="qualification-run-group"]')];
  if (!inputs.length) return [...state.qualificationRunDialog.listGroupIds];
  return inputs.filter((node) => node.checked).map((node) => node.value);
}

function defaultQualificationRunListGroupIds(stage, options = {}) {
  if (!qualificationRunSupportsGroupScope(stage)) return [];
  const available = qualificationRunGroupIds();
  if (options.listGroupIds?.length) {
    return options.listGroupIds.filter((groupId) => available.includes(groupId));
  }
  if (state.listGroupId && state.listGroupId !== ALL_LIST_GROUPS) {
    return available.includes(state.listGroupId) ? [state.listGroupId] : [];
  }
  return available;
}

function renderQualificationRunGroups(stage, selectedGroupIds) {
  const fieldset = $("#qualification-run-group-fieldset");
  const container = $("#qualification-run-groups");
  const supportsScope = qualificationRunSupportsGroupScope(stage);
  fieldset.hidden = !supportsScope;
  container.replaceChildren();
  if (!supportsScope) return;
  const groups = state.qualificationWorkflow.groups || [];
  const scopeName = scopeLabelForGroups(groups.map((group) => group.listGroupId));
  $("#qualification-run-group-legend").textContent = `対象${scopeName}（複数選択可）`;
  $("#qualification-run-group-refresh-label").textContent = `選択${scopeName}を全件洗い替え`;
  for (const group of groups) {
    const label = element("label", "");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.name = "qualification-run-group";
    input.value = group.listGroupId;
    input.checked = selectedGroupIds.includes(group.listGroupId);
    input.addEventListener("change", () => {
      state.qualificationRunDialog.listGroupIds = selectedQualificationRunListGroupIds();
      previewQualificationRun();
    });
    const content = element("span", "");
    content.append(
      element("strong", "", group.listGroupId),
      element("small", "", `${group.questionCount}問`),
    );
    label.append(input, content);
    container.append(label);
  }
}

function setQualificationRunGroupSelection(selectAll) {
  if (state.qualificationRunDialog.running) return;
  for (const input of document.querySelectorAll('input[name="qualification-run-group"]')) {
    input.checked = selectAll;
  }
  state.qualificationRunDialog.listGroupIds = selectedQualificationRunListGroupIds();
  previewQualificationRun();
}

function defaultQualificationRunStageIds(stage) {
  if (!stage.batchSelectable) return [stage.id];
  const selectable = state.qualificationWorkflow.stages.filter((item) => item.batchSelectable);
  const start = Math.max(selectable.findIndex((item) => item.id === stage.id), 0);
  const categoryReady = state.qualificationWorkflow.stages
    .find((item) => item.id === "category_setup")?.status === "ready";
  return selectable.slice(start)
    .filter((item) => item.id !== "question_set" || categoryReady)
    .map((item) => item.id);
}

function renderQualificationRunStages(stage, selectedStageIds) {
  const fieldset = $("#qualification-run-stage-fieldset");
  const container = $("#qualification-run-stages");
  const selectable = state.qualificationWorkflow.stages.filter((item) => item.batchSelectable);
  fieldset.hidden = !stage.batchSelectable;
  container.replaceChildren();
  if (!stage.batchSelectable) return;
  for (const item of selectable) {
    const label = element("label", "");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.name = "qualification-run-stage";
    input.value = item.id;
    input.checked = selectedStageIds.includes(item.id);
    input.addEventListener("change", () => {
      state.qualificationRunDialog.stageIds = selectedQualificationRunStageIds();
      updateQualificationRunHeading();
      previewQualificationRun();
    });
    const content = element("span", "");
    content.append(
      element("strong", "", `${item.code} ${item.label}`),
      element("small", "", QUALIFICATION_WORKFLOW_LABELS[item.status] || item.status),
    );
    label.append(input, content);
    container.append(label);
  }
}

function updateQualificationRunHeading() {
  const workflow = state.qualificationWorkflow;
  if (!workflow) return;
  const stages = selectedQualificationRunStageIds()
    .map((stageId) => workflow.stages.find((item) => item.id === stageId))
    .filter(Boolean);
  if (!stages.length) {
    $("#qualification-run-title").textContent = "工程を選択";
    $("#qualification-run-purpose").textContent = "実行する工程を一つ以上選択してください。";
    return;
  }
  $("#qualification-run-title").textContent = stages.length === 1
    ? `${stages[0].code} ${stages[0].label}`
    : `${stages[0].code}から${stages[stages.length - 1].code}まで`;
  $("#qualification-run-purpose").textContent = stages.length === 1
    ? stages[0].purpose
    : "一問について選択工程を順に完了してから、次の問題へ進みます。";
}

function openQualificationRunDialog(stage, options = {}) {
  cancelQualificationRunPreview();
  const selectedStageIds = options.stageIds || defaultQualificationRunStageIds(stage);
  const selectedGroupIds = defaultQualificationRunListGroupIds(stage, options);
  state.qualificationRunDialog = {
    preview: null,
    running: false,
    previewSequence: state.qualificationRunDialog.previewSequence + 1,
    resumedFrom: options.resumedFrom || "",
    stageIds: selectedStageIds,
    listGroupIds: selectedGroupIds,
    previewController: null,
  };
  state.qualificationWorkflowStageId = stage.id;
  renderQualificationRunStages(stage, selectedStageIds);
  renderQualificationRunGroups(stage, selectedGroupIds);
  updateQualificationRunHeading();
  $("#qualification-run-guide").disabled = !stage.canonicalDocs?.length;
  const groupRefresh = document.querySelector('input[name="qualification-run-mode"][value="group_refresh"]');
  const supportsScope = qualificationRunSupportsGroupScope(stage);
  const groupRefreshLabel = $("#qualification-run-group-refresh");
  const outdatedLabel = $("#qualification-run-outdated");
  const refreshLabel = $("#qualification-run-refresh");
  groupRefreshLabel.hidden = !supportsScope;
  refreshLabel.hidden = supportsScope && options.mode !== "refresh";
  outdatedLabel.hidden = !stage.policyVersion;
  groupRefresh.disabled = !supportsScope;
  const visibleModeCount = $("#qualification-run-mode-fieldset").querySelectorAll(".run-mode-options > label:not([hidden])").length;
  $("#qualification-run-mode-fieldset").style.setProperty("--run-mode-count", String(visibleModeCount));
  const defaultMode = options.mode || (stage.versionTrackingActive
    && (stage.versionOutdatedCount || stage.versionUnrecordedCount)
    ? "outdated"
    : supportsScope
      ? "group_refresh"
    : stage.status === "ready"
      ? "refresh"
      : stage.completeCount === stage.targetCount && stage.issueCount > 0
        ? "attention"
        : "remaining");
  for (const node of document.querySelectorAll('input[name="qualification-run-mode"]')) {
    node.checked = node.value === defaultMode;
  }
  $("#qualification-run-job").hidden = true;
  $("#qualification-run-job-log").textContent = "";
  setQualificationRunPreviewState("loading", "対象を確認しています。");
  $("#qualification-run-cancel").hidden = false;
  $("#qualification-run-dialog").showModal();
  previewQualificationRun();
}

function cancelQualificationRunPreview() {
  const dialog = state.qualificationRunDialog;
  dialog.previewSequence += 1;
  dialog.previewController?.abort();
  dialog.previewController = null;
}

function setQualificationRunPreviewState(status, message) {
  const action = $("#qualification-run-start");
  $("#qualification-run-preview").textContent = message;
  state.qualificationRunDialog.preview = null;
  if (status === "loading") {
    action.textContent = "確認中";
    action.disabled = true;
    return;
  }
  action.textContent = status === "error" ? "再確認" : "開始できません";
  action.disabled = status !== "error";
}

async function previewQualificationRun() {
  const workflow = state.qualificationWorkflow;
  const stageId = state.qualificationWorkflowStageId;
  if (!workflow || !stageId || state.qualificationRunDialog.running) return;
  const sequence = state.qualificationRunDialog.previewSequence + 1;
  state.qualificationRunDialog.previewSequence = sequence;
  state.qualificationRunDialog.previewController?.abort();
  state.qualificationRunDialog.previewController = null;
  const stageIds = selectedQualificationRunStageIds();
  if (!stageIds.length) {
    setQualificationRunPreviewState("blocked", "工程を一つ以上選択してください。");
    return;
  }
  const stage = qualificationWorkflowStage(stageId);
  const supportsScope = qualificationRunSupportsGroupScope(stage);
  const listGroupIds = selectedQualificationRunListGroupIds();
  if (supportsScope && !listGroupIds.length) {
    setQualificationRunPreviewState("blocked", "対象年度を一つ以上選択してください。");
    return;
  }
  const controller = new AbortController();
  state.qualificationRunDialog.previewController = controller;
  let timedOut = false;
  const timeoutId = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, QUALIFICATION_PREVIEW_TIMEOUT_MS);
  setQualificationRunPreviewState("loading", "対象を確認しています。");
  try {
    const preview = await api("/api/qualification-runs/preview", {
      method: "POST",
      signal: controller.signal,
      body: {
        qualification: workflow.qualification,
        stageId,
        stageIds,
        mode: selectedQualificationRunMode(),
        listGroupIds: supportsScope ? listGroupIds : undefined,
        resumedFrom: state.qualificationRunDialog.resumedFrom || undefined,
      },
    });
    if (sequence !== state.qualificationRunDialog.previewSequence) return;
    state.qualificationRunDialog.preview = preview;
    renderQualificationRunPreview(preview);
  } catch (error) {
    if (sequence !== state.qualificationRunDialog.previewSequence) return;
    const message = timedOut
      ? "確認に時間がかかっています。再確認してください。"
      : error.name === "AbortError"
        ? "確認を中断しました。再確認してください。"
        : `${error.message} 再確認してください。`;
    setQualificationRunPreviewState("error", message);
  } finally {
    window.clearTimeout(timeoutId);
    if (state.qualificationRunDialog.previewController === controller) {
      state.qualificationRunDialog.previewController = null;
    }
  }
}

function renderQualificationRunPreview(preview) {
  const container = $("#qualification-run-preview");
  container.replaceChildren();
  if (!preview.targetCount) {
    container.append(
      element("strong", "", "この範囲に対象はありません"),
      element("span", "", "別の範囲を選ぶか、次の工程を確認してください。"),
    );
  } else {
    const isMultiStage = preview.kind === "human" && preview.stageCount > 1;
    const questionUnit = ["refresh", "group_refresh"].includes(preview.mode)
      ? "問すべて"
      : "問";
    container.append(
      element(
        "strong",
        "run-preview-count",
        preview.kind === "machine"
          ? `${preview.targetCount}フォルダ`
          : isMultiStage
            ? `${preview.targetCount}${questionUnit} × ${preview.stageCount}工程`
            : `${preview.targetCount}問`,
      ),
      element("span", "", `${preview.stageCode} ${preview.stageLabel}`),
      element("span", "", preview.kind === "machine"
        ? "Merge・Convert・upload-readyを順番に再生成して検証します。"
        : "Codex App Serverの新規threadで対象工程を整備します。"),
    );
    if (isMultiStage) {
      container.append(
        element("span", "run-preview-work-items", `延べ${preview.workItemCount}工程判定`),
      );
    }
    if (preview.scopeListGroupIds?.length) {
      const scopeName = scopeLabelForGroups(preview.scopeListGroupIds);
      const targetSuffix = preview.targetGroupIds?.length
        && preview.targetGroupIds.length !== preview.scopeListGroupIds.length
        ? ` / 作業対象 ${preview.targetGroupIds.join("・")}`
        : "";
      container.append(
        element(
          "span",
          "run-preview-groups",
          `選択${scopeName} ${preview.scopeListGroupIds.join("・")}${targetSuffix}`,
        ),
      );
    }
  }
  container.append(
    element(
      "span",
      "run-preview-combination",
      `正本 ${preview.canonicalDocs?.length || 0}文書 × 対象source ${preview.sourceFileCount || 0}ファイル × 更新先 ${preview.outputFileCount || 0}ファイル`,
    ),
  );
  if (preview.blockingWarnings?.length) {
    container.append(element("span", "run-preview-warning", `必須field不足 ${preview.blockingWarnings.length}件`));
  }
  const action = $("#qualification-run-start");
  action.textContent = preview.kind === "machine" ? "出力を開始" : "整備を開始";
  action.disabled = !preview.canStart;
}

async function startQualificationRun(event) {
  event.preventDefault();
  const preview = state.qualificationRunDialog.preview;
  if (!preview) {
    await previewQualificationRun();
    return;
  }
  if (!preview.canStart || state.qualificationRunDialog.running) return;
  setQualificationRunRunning(true);
  try {
    const result = await api("/api/qualification-runs/start", {
      method: "POST",
      body: {
        qualification: preview.qualification,
        stageId: preview.stageId,
        stageIds: preview.stageIds,
        mode: preview.mode,
        listGroupIds: preview.scopeListGroupIds?.length ? preview.scopeListGroupIds : undefined,
        previewToken: preview.previewToken,
        resumedFrom: state.qualificationRunDialog.resumedFrom || undefined,
      },
    });
    if (!result.job) throw new Error("Codex App Serverのjobを開始できませんでした。");
    await pollQualificationRunJob(result.job.jobId);
  } catch (error) {
    setQualificationRunRunning(false);
    $("#qualification-run-job").hidden = false;
    $("#qualification-run-job-status").textContent = "処理を完了できませんでした。";
    $("#qualification-run-preview").textContent = error.message;
    await loadQualificationRuns();
    toast(error.message, true);
  }
}

function setQualificationRunRunning(running) {
  state.qualificationRunDialog.running = running;
  $("#qualification-run-start").disabled = running;
  $("#qualification-run-cancel").hidden = running;
  $("#qualification-run-job").hidden = !running;
  for (const node of $("#qualification-run-dialog").querySelectorAll(".close-dialog")) {
    node.disabled = running;
  }
  for (const node of $("#qualification-run-dialog").querySelectorAll("input")) {
    const stage = qualificationWorkflowStage();
    node.disabled = running || (
      node.value === "group_refresh" && !qualificationRunSupportsGroupScope(stage)
    );
  }
  for (const node of $("#qualification-run-dialog").querySelectorAll(".run-group-actions button")) {
    node.disabled = running;
  }
}

function qualificationRunResumeGroupIds(run) {
  if (run.scopeListGroupIds?.length) return run.scopeListGroupIds;
  if (run.scopeListGroupId) return [run.scopeListGroupId];
  return qualificationRunGroupIds();
}

async function pollQualificationRunJob(jobId) {
  setQualificationRunRunning(true);
  if (!$("#qualification-run-dialog").open) $("#qualification-run-dialog").showModal();
  while (true) {
    const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
    $("#qualification-run-job-status").textContent = QUALIFICATION_RUN_STATUS_LABELS[job.status] || job.status;
    $("#qualification-run-job-log").textContent = (job.logs || []).join("\n");
    if (job.status === "queued" || job.status === "running") {
      await new Promise((resolve) => window.setTimeout(resolve, 800));
      continue;
    }
    if (job.status === "failed") throw new Error(job.error || "出力処理に失敗しました。");
    setQualificationRunRunning(false);
    $("#qualification-run-job").hidden = false;
    $("#qualification-run-start").textContent = "閉じる";
    $("#qualification-run-start").disabled = false;
    state.qualificationRunDialog.preview = null;
    await loadQualificationWorkflow(true);
    await loadQualificationRuns();
    await loadQuestions(true);
    $("#qualification-run-job-status").textContent = job.result?.message || "完了しました。";
    toast(job.result?.message || "資格全体の出力を確認しました。");
    return;
  }
}

async function resumeQualificationRun() {
  const run = state.qualificationActiveRun;
  if (!run) return;
  if (["queued", "running", "validating"].includes(run.status) && run.jobId) {
    pollQualificationRunJob(run.jobId).catch(async (error) => {
      setQualificationRunRunning(false);
      await loadQualificationRuns();
      toast(error.message, true);
    });
    return;
  }
  if (run.kind === "human") {
    toast("工程又は問題詳細から開始すると、新しいCodex App Server threadで再実行します。");
    return;
  }
  const stage = state.qualificationWorkflow?.stages.find((item) => item.id === run.stageId);
  if (stage) openQualificationRunDialog(stage, {
    mode: run.mode,
    resumedFrom: run.runId,
    listGroupIds: qualificationRunResumeGroupIds(run),
  });
}

async function setListMode(exceptionsOnly) {
  clearEvaluationSelection();
  state.exceptionsOnly = exceptionsOnly;
  $("#exceptions-button").classList.toggle("active", exceptionsOnly);
  $("#all-button").classList.toggle("active", !exceptionsOnly);
  await loadQuestions(false);
}

function listQuery(offset = 0) {
  const params = new URLSearchParams({
    qualification: state.qualification,
    listGroupId: state.listGroupId,
    exceptionsOnly: String(state.exceptionsOnly),
    lawOnly: String($("#law-only").checked),
    firestoreMismatch: String($("#firestore-mismatch").checked),
    offset: String(offset),
    limit: String(state.questionPage.limit),
  });
  const search = $("#search-input").value.trim();
  const issue = $("#issue-select").value;
  const reviewStatus = $("#review-status-select").value;
  const evaluationStatus = $("#evaluation-status-select").value;
  const workVersionStatus = $("#work-version-select").value;
  if (search) params.set("search", search);
  if (issue) params.set("issue", issue);
  if (reviewStatus) params.set("status", reviewStatus);
  if (evaluationStatus) params.set("evaluationStatus", evaluationStatus);
  if (workVersionStatus && state.qualificationWorkflowStageId) {
    params.set("workStageId", state.qualificationWorkflowStageId);
    params.set("workVersionStatus", workVersionStatus);
  }
  return params;
}

function evaluationScopeLabel() {
  if (state.listGroupId === ALL_LIST_GROUPS) return "資格全体";
  const scopeName = $("#group-select-label")?.textContent || "年度";
  return `${scopeName} ${state.listGroupId}`;
}

async function loadQuestions(preserveSelection, append = false) {
  if (!state.qualification || !state.listGroupId) return;
  const offset = append ? state.questions.length : 0;
  if (append) {
    $("#load-more-questions").disabled = true;
    $("#load-more-questions").textContent = "読み込み中";
  } else {
    setLoading("読み込み中");
  }
  try {
    const payload = await api(`/api/questions?${listQuery(offset)}`);
    state.questions = append
      ? [...state.questions, ...payload.questions]
      : payload.questions;
    if (!append) {
      const visibleIds = new Set(state.questions.map((question) => question.id));
      for (const questionId of state.selectedQuestionIds) {
        if (!visibleIds.has(questionId)) state.selectedQuestionIds.delete(questionId);
      }
    }
    state.questionPage.filteredCount = payload.filteredCount;
    state.questionPage.hasMore = payload.hasMore;
    renderQueue();
    const counts = payload.evaluationCounts || {};
    const workCounts = payload.workVersionCounts || {};
    $("#list-summary").textContent = [
      `評価範囲 ${evaluationScopeLabel()}`,
      `${state.questions.length}/${payload.filteredCount}件表示`,
      `全${payload.questionCount}問`,
      `評価待ち${counts.unreviewed || 0}`,
      `要再整備${counts.needsRework || 0}`,
      `公開可能${counts.publishReady || 0}`,
      `反映済み${counts.published || 0}`,
      ...($("#work-version-select").value
        ? [`現行版${workCounts.current || 0}・旧版${workCounts.outdated || 0}・未記録${workCounts.unrecorded || 0}`]
        : []),
    ].join(" / ");
    updateEvaluationSelectionControls();
    const selectedStillExists = state.questions.some((question) => question.id === state.selectedId);
    if (!append && (!preserveSelection || !selectedStillExists)) {
      state.selectedId = state.questions[0]?.id || "";
    }
    if (!append && state.selectedId) {
      await loadDetail(state.selectedId);
    } else if (!append) {
      state.detail = null;
      renderEmpty("条件に一致する問題はありません。");
    }
  } catch (error) {
    toast(error.message, true);
    if (!append) setLoading("読み込み失敗");
  } finally {
    $("#load-more-questions").disabled = false;
    $("#load-more-questions").textContent = "さらに表示";
  }
}

function renderQueue() {
  const queue = $("#queue");
  queue.replaceChildren();
  for (const question of state.questions) {
    const item = element("div", `queue-item${question.id === state.selectedId ? " selected" : ""}`);
    item.dataset.questionId = question.id;
    item.setAttribute("role", "option");
    item.setAttribute("aria-selected", String(question.id === state.selectedId));
    const selectLabel = element("label", "queue-select");
    const select = document.createElement("input");
    select.type = "checkbox";
    select.checked = state.selectedQuestionIds.has(question.id);
    select.setAttribute(
      "aria-label",
      `${question.questionLabel || question.sourceQuestionKey || "問題"}を評価対象に選択`,
    );
    select.addEventListener("change", () => {
      if (select.checked) state.selectedQuestionIds.add(question.id);
      else state.selectedQuestionIds.delete(question.id);
      updateEvaluationSelectionControls();
    });
    selectLabel.append(select);
    const open = element("button", "queue-open");
    open.type = "button";
    const head = element("div", "queue-item-head");
    head.append(
      element("span", "queue-label", question.questionLabel || question.sourceQuestionKey || question.sourceStem),
      ...(state.listGroupId === ALL_LIST_GROUPS
        ? [element("span", "queue-group", question.listGroupId)]
        : []),
      workVersionBadge(question),
      evaluationBadge(question),
    );
    const body = element("p", "queue-body", question.body || "（問題文なし）");
    const issueRow = element("div", "issue-row");
    for (const issue of question.issues.slice(0, 3)) issueRow.append(issueBadge(issue));
    open.append(head, body, issueRow);
    open.addEventListener("click", () => loadDetail(question.id));
    item.append(selectLabel, open);
    queue.append(item);
  }
  $("#queue-pagination").hidden = !state.questionPage.hasMore;
  updateEvaluationSelectionControls();
}

function evaluationDisplay(question) {
  const status = question.evaluation?.status || "not_started";
  if (status === "passed" && question.workflow?.firestore === "match") {
    return { label: "反映済み", tone: "good" };
  }
  if (!question.evaluation?.machineReady) {
    return { label: "整備が必要", tone: "warning" };
  }
  if (status === "passed") return { label: "公開可能", tone: "good" };
  if (status === "needs_rework") return { label: "要再整備", tone: "danger" };
  if (status === "running") return { label: "評価中", tone: "running" };
  if (status === "stale") return { label: "再評価", tone: "warning" };
  return { label: "評価待ち", tone: "neutral" };
}

function evaluationBadge(question) {
  const display = evaluationDisplay(question);
  return element("span", `queue-quality ${display.tone}`, display.label);
}

function selectedWorkVersion(question) {
  return question.workVersions?.stages?.find(
    (stage) => stage.id === state.qualificationWorkflowStageId,
  ) || null;
}

function workVersionBadge(question) {
  const stage = selectedWorkVersion(question);
  if (!stage) return element("span", "work-version-badge neutral", "版対象外");
  const recorded = stage.recordedVersion === null ? "未記録" : `v${stage.recordedVersion}`;
  const transition = stage.status === "current"
    ? recorded
    : `${recorded}→v${stage.currentVersion}`;
  const tone = stage.status === "current"
    ? "good"
    : stage.status === "unrecorded"
      ? "neutral"
      : "warning";
  const badge = element("span", `work-version-badge ${tone}`, `${stage.code} ${transition}`);
  badge.title = stage.detail || "";
  return badge;
}

function clearEvaluationSelection() {
  state.selectedQuestionIds.clear();
  updateEvaluationSelectionControls();
}

function toggleVisibleQuestionSelection(event) {
  for (const question of state.questions) {
    if (event.target.checked) state.selectedQuestionIds.add(question.id);
    else state.selectedQuestionIds.delete(question.id);
  }
  renderQueue();
}

function updateEvaluationSelectionControls() {
  const visibleIds = state.questions.map((question) => question.id);
  const visibleSelected = visibleIds.filter((id) => state.selectedQuestionIds.has(id)).length;
  const selectVisible = $("#select-visible");
  const selectVisibleLabel = $("#select-visible-label");
  if (selectVisible) {
    selectVisible.checked = visibleIds.length > 0 && visibleSelected === visibleIds.length;
    selectVisible.indeterminate = visibleSelected > 0 && visibleSelected < visibleIds.length;
  }
  if (selectVisibleLabel) {
    selectVisibleLabel.textContent = visibleIds.length
      ? `一覧の${visibleIds.length}問を選択`
      : "一覧を選択";
  }
  const count = state.selectedQuestionIds.size;
  const action = $("#bulk-evaluate-button");
  if (action) {
    action.disabled = count === 0 || !state.evaluationEnabled;
    action.textContent = count ? `選択した${count}問を評価` : "選択した問題を評価";
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
    const summary = state.questions.find((question) => question.id === questionId);
    const params = new URLSearchParams({
      qualification: state.qualification,
      listGroupId: summary?.listGroupId || state.listGroupId,
    });
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
      "secondary-button",
      () => openReview("awaiting_codex"),
      "修正を依頼",
      "おかしい箇所と調査範囲を記録し、Codex App Serverの新規threadで整備を開始します。",
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

  pane.append(renderWorkVersionPanel(question), renderEvaluationPanel(question));

  const requiredWarning = renderRequiredFieldWarning(question);
  if (requiredWarning) pane.append(requiredWarning);
  const qualityWarning = renderLawAuditQualityWarning(question);
  if (qualityWarning) pane.append(qualityWarning);

  const firestoreDiff = renderFirestoreDiff(question);
  if (firestoreDiff) pane.append(firestoreDiff);

  const questionSection = section("問題文");
  const questionBody = element("p", "question-body", question.body);
  questionSection.append(questionBody);
  if (question.issues.length) {
    const issues = element("div", "issue-panel");
    for (const issue of question.issues) {
      let detail = issue.detail;
      if (issue.code === "required_field_missing" && question.requiredFieldWarnings?.length) {
        detail = `${question.requiredFieldWarnings.length}件の欠損を検出。上の警告欄で確認・一括依頼できます。`;
      } else if (issue.code.startsWith("law_audit_") && question.qualityWarnings?.length) {
        detail = `${question.qualityWarnings.length}件の監査メタデータ要修正を検出。上の警告欄で確認・一括依頼できます。`;
      }
      issues.append(element("div", "issue-line", `${ISSUE_LABELS[issue.code] || issue.code}: ${detail}`));
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

function renderWorkVersionPanel(question) {
  const workVersions = question.workVersions || {};
  const node = element("section", `work-version-panel ${workVersions.allCurrent ? "good" : "warning"}`);
  const heading = element("div", "work-version-heading");
  heading.append(
    element("h3", "", "作業バージョン"),
    element(
      "span",
      `work-version-overall ${workVersions.allCurrent ? "good" : "warning"}`,
      workVersions.allCurrent ? "全工程が現行版" : "洗い替えあり",
    ),
  );
  const stages = element("div", "work-version-stages");
  for (const stage of workVersions.stages || []) {
    const recorded = stage.recordedVersion === null ? "未記録" : `v${stage.recordedVersion}`;
    const text = stage.status === "current"
      ? `${stage.code} ${stage.label} ${recorded}`
      : `${stage.code} ${stage.label} ${recorded} → v${stage.currentVersion}`;
    const item = element("span", `work-version-stage ${stage.status}`, text);
    item.title = stage.detail || "";
    stages.append(item);
  }
  node.append(heading, stages);
  if (!workVersions.allCurrent) {
    node.append(element(
      "p",
      "work-version-summary",
      "旧版又は未記録の工程だけを選び、現行版で一問ずつ洗い替えます。",
    ));
  }
  return node;
}

function renderEvaluationPanel(question) {
  const evaluation = question.evaluation || {};
  const display = evaluationDisplay(question);
  const node = element("section", `evaluation-panel ${display.tone}`);
  const heading = element("div", "evaluation-heading");
  heading.append(
    element("h3", "", "別セッション評価"),
    element("span", `evaluation-status ${display.tone}`, display.label),
  );
  if (evaluation.evaluatedAt) {
    heading.append(element("time", "", formatReadbackTime(evaluation.evaluatedAt)));
  }
  const score = Number.isInteger(evaluation.explanationScore)
    ? `${evaluation.explanationScore}/100`
    : "未評価";
  const readiness = question.workflow?.firestore === "match"
    ? "反映済み"
    : question.publishReady
      ? "公開可能"
      : "未達";
  const metrics = element("div", "evaluation-metrics");
  metrics.append(
    summaryMetric(
      "正誤確認",
      `${evaluation.verifiedChoiceCount || 0}/${evaluation.choiceCount || question.choiceCount || 0}`,
      evaluation.allChoicesVerified ? "good" : "warning",
    ),
    summaryMetric(
      "解説品質",
      score,
      evaluation.explanationPassed ? "good" : Number.isInteger(evaluation.explanationScore) ? "danger" : "",
    ),
    summaryMetric(
      "公開準備",
      readiness,
      readiness === "反映済み" || readiness === "公開可能" ? "good" : "warning",
    ),
  );
  node.append(heading, metrics);
  const summary = evaluation.summary
    || (evaluation.status === "stale"
      ? "評価後に問題内容が変わりました。選択して再評価してください。"
      : evaluation.machineReady
        ? "整備済みです。一覧で他の問題とまとめて選択し、別セッション評価を開始できます。"
        : "評価前に表示中の要確認項目と後続成果物を整えてください。"
    );
  node.append(element("p", "evaluation-summary", summary));

  if (evaluation.criticalIssues?.length) {
    const critical = element("div", "evaluation-critical");
    critical.append(element("strong", "", "重大指摘"));
    const list = document.createElement("ul");
    for (const issue of evaluation.criticalIssues) list.append(element("li", "", issue));
    critical.append(list);
    node.append(critical);
  }
  if (evaluation.reworkItems?.length) {
    const rework = element("div", "evaluation-rework");
    rework.append(element("strong", "", "再整備する項目"));
    const list = document.createElement("ul");
    for (const item of evaluation.reworkItems) {
      const choices = item.choiceIndexes?.length
        ? ` / 選択肢${item.choiceIndexes.map((index) => index + 1).join("・")}`
        : "";
      list.append(element("li", "", `${item.stage}${choices}: ${item.message}`));
    }
    rework.append(list);
    node.append(rework);
  }
  if (evaluation.choiceEvaluations?.length) {
    const details = document.createElement("details");
    details.className = "evaluation-evidence";
    details.append(element("summary", "", "選択肢ごとの判定と根拠"));
    const content = element("div", "evaluation-evidence-list");
    for (const choice of evaluation.choiceEvaluations) {
      const row = element("div", `evaluation-choice ${choice.matchesCurrent ? "match" : "mismatch"}`);
      row.append(
        element(
          "strong",
          "",
          `選択肢${choice.choiceIndex + 1}: ${choice.matchesCurrent ? "現在の正誤と一致" : "要確認"}`,
        ),
        element("p", "", choice.reason),
      );
      const evidence = document.createElement("ul");
      for (const item of choice.evidence || []) {
        evidence.append(
          element("li", "", `${item.source} / ${item.locator}: ${item.summary}`),
        );
      }
      row.append(evidence);
      content.append(row);
    }
    details.append(content);
    node.append(details);
  }
  return node;
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
  const warnings = question.requiredFieldWarnings?.length
    ? question.requiredFieldWarnings
    : issues.flatMap((issue) => (issue.fields || [""]).map((field) => ({
      stage: "投影後データ",
      field,
      dataPath: field,
      detail: issue.detail,
    })));
  const node = element("section", "required-warning-panel");
  node.append(
    element("strong", "", `必須フィールドが不足しています（${warnings.length}件）`),
    element("p", "", "不足を解消するまで、パッチ変更をMerge・Convert・upload-readyへ反映できません。"),
  );
  const list = document.createElement("ul");
  for (const warning of warnings) {
    const location = [warning.stage, warning.documentId].filter(Boolean).join(" / ");
    const field = warning.dataPath || warning.field || "field不明";
    const item = element(
      "li",
      "",
      `${location ? `${location} / ` : ""}${field}: ${warning.detail}`,
    );
    installReviewTarget(item, {
      fields: [field],
      targetLabel: `必須フィールド欠損 / ${location || "投影後データ"}`,
      dataPath: field,
      issueType: "required_field_missing",
    });
    list.append(item);
  }
  node.append(
    list,
    actionWithHelp(
      "欠損をまとめて修正依頼",
      "primary-button",
      () => openRequiredFieldsReview(question),
      "欠損をまとめて修正依頼",
      "この問題で検出した必須フィールド欠損を1件のCodex依頼にまとめます。欠損の一覧と対象document IDも依頼に含めます。",
    ),
  );
  return node;
}

function openRequiredFieldsReview(question) {
  openFindingsReview({
    question,
    warnings: question.requiredFieldWarnings || [],
    issueType: "required_field_missing",
    title: "必須フィールド欠損をまとめて修正依頼",
    targetLabel: "必須フィールド欠損の一括報告",
    note: "検出された必須フィールド欠損をすべて調査し、適切なpatchを修正してupload-readyまで再生成する。",
    investigationScope: "current_question",
  });
}

function renderLawAuditQualityWarning(question) {
  const warnings = question.qualityWarnings || [];
  if (!warnings.length) return null;
  const node = element("section", "quality-warning-panel");
  node.append(
    element("strong", "", `法令監査パッチの修正が必要です（${warnings.length}件）`),
    element(
      "p",
      "",
      "トップレベルの正答は存在しますが、法令監査メタデータが不完全です。パッチ再生成は実行できますが、Firestoreへの公開は修正まで停止します。",
    ),
  );
  const list = document.createElement("ul");
  for (const warning of warnings) {
    const location = [warning.stage, warning.documentId].filter(Boolean).join(" / ");
    const field = warning.dataPath || warning.field || "lawRevisionFacts";
    const item = element("li", "", `${location} / ${field}: ${warning.detail}`);
    installReviewTarget(item, {
      fields: [field],
      targetLabel: `法令監査パッチ要修正 / ${location}`,
      dataPath: field,
      issueType: warning.code || "law_audit_metadata_incomplete",
    });
    list.append(item);
  }
  node.append(
    list,
    actionWithHelp(
      "監査パッチをまとめて修正依頼",
      "primary-button",
      () => openLawAuditQualityReview(question),
      "監査パッチをまとめて修正依頼",
      "対象patch一覧を作り、Codex組み込みweb検索でe-Gov又は所管官庁の一次情報を開き、全対象を一問一肢ずつ監査します。",
    ),
  );
  return node;
}

function summarizedFindingsText(warnings) {
  const fieldCounts = new Map();
  const documentIds = [];
  for (const warning of warnings) {
    const field = warning.dataPath || warning.field || "lawRevisionFacts";
    fieldCounts.set(field, (fieldCounts.get(field) || 0) + 1);
    if (warning.documentId) documentIds.push(warning.documentId);
  }
  const fields = [...fieldCounts.entries()]
    .map(([field, count]) => `${field}: ${count}件`)
    .join(", ");
  const uniqueDocs = [...new Set(documentIds)];
  const examples = uniqueDocs.slice(0, 6).join(", ");
  const more = uniqueDocs.length > 6 ? ` ほか${uniqueDocs.length - 6}件` : "";
  return [
    `法令監査メタデータ不備: ${warnings.length}件`,
    fields ? `fields: ${fields}` : "",
    examples ? `document例: ${examples}${more}` : "",
    "各対象は条文本文を開いて一問一肢ずつ確認する。",
  ].filter(Boolean).join("\n");
}

function openLawAuditQualityReview(question) {
  const warnings = question.qualityWarnings || [];
  openFindingsReview({
    question,
    warnings,
    issueType: warnings[0]?.code || "law_audit_metadata_incomplete",
    title: "法令監査パッチをまとめて修正依頼",
    targetLabel: "法令監査メタデータの一括報告",
    note: "資格内の法令監査不備をCodex組み込みweb検索と公的な一次情報で一問一肢ずつ監査する。",
    investigationScope: "qualification",
    requestKind: "qualification_law_audit",
    selectedText: summarizedFindingsText(warnings),
  });
}

function openFindingsReview({
  question,
  warnings,
  issueType,
  title,
  targetLabel,
  note,
  investigationScope,
  requestKind = "",
  selectedText,
}) {
  const uploadDocs = question.uploadReadyDocs || [];
  const choiceIndexes = [...new Set(warnings
    .map((warning) => uploadDocs.findIndex((doc) => doc.questionId === warning.documentId))
    .filter((index) => index >= 0))];
  const fields = [...new Set(warnings
    .map((warning) => warning.dataPath || warning.field)
    .filter(Boolean))];
  const defaultSelectedText = warnings.map((warning) => {
    const location = [warning.stage, warning.documentId].filter(Boolean).join(" / ");
    const stateLabel = warning.code === "law_audit_verdict_mismatch" ? "不一致" : "fieldなし";
    return `${location ? `${location} / ` : ""}${warning.dataPath || warning.field}: ${stateLabel} - ${warning.detail}`;
  }).join("\n");
  openReview(
    "awaiting_codex",
    {
      targetLabel,
      dataPath: fields.join(", "),
      fields,
      choiceIndexes,
      selectedText: selectedText || defaultSelectedText,
    },
    issueType,
    investigationScope,
    requestKind,
  );
  $("#review-dialog-title").textContent = title;
  $("#review-note").value = note;
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

function patchSyncAction() {
  return actionWithHelp(
    "パッチ変更を反映",
    "primary-button",
    () => openSyncDialog(true),
    "パッチ変更を反映",
    "現在の資格・フォルダだけを対象に、最新patchからMerge、Convert、upload-readyを再生成し、upload dry-runまで自動で検証します。成果物が一致済みでも再実行できます。Firestoreへの書き込みは行いません。必須field不足がある場合は開始しません。",
  );
}

function renderPipelineActions(question) {
  const workflow = question.workflow || {};
  const localReady = ["merge", "convert", "upload"].every((stage) => workflow[stage] === "match");
  const firestoreNeedsAttention = ["mismatch", "missing", "error", "upstream_stale"]
    .includes(workflow.firestore);
  const evaluation = question.evaluation || {};
  const node = element(
    "div",
    `pipeline-action-bar ${question.nextAction === "complete" ? "ready" : "attention"}`,
  );
  const status = element("div", "pipeline-message");
  const actions = element("div", "pipeline-buttons");

  if (!localReady) {
    status.append(
      element("strong", "", "最新patchが後続成果物へ未反映です"),
      element("span", "", "対象フォルダだけをMerge、Convert、upload-readyまで再生成します。"),
    );
    actions.append(patchSyncAction());
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
  } else if (!evaluation.machineReady) {
    status.append(
      element("strong", "", "評価前の整備が残っています"),
      element("span", "", "上の要確認項目を修正すると、評価待ちへ進みます。"),
    );
    actions.append(actionWithHelp(
      "修正を依頼",
      "primary-button",
      () => openReview("awaiting_codex"),
      "修正を依頼",
      "現在の要確認項目を、正本と対象pathを含むCodex依頼として作成します。",
    ));
  } else if (["not_started", "stale"].includes(evaluation.status)) {
    status.append(
      element("strong", "", evaluation.status === "stale" ? "再評価が必要です" : "評価待ちです"),
      element("span", "", "一覧で他の整備済み問題とまとめて選択し、別セッション評価を開始できます。"),
    );
    actions.append(actionWithHelp(
      evaluation.status === "stale" ? "この問題を再評価" : "この問題を評価",
      "primary-button",
      () => openEvaluationDialog([question.id]),
      "別セッション評価",
      "この問題だけを選択した評価runを開始します。一覧では複数問題をまとめて選択できます。",
    ));
  } else if (evaluation.status === "running") {
    status.append(
      element("strong", "", "別セッションで評価中です"),
      element("span", "", "完了後に正誤確認数、解説点数、合否を表示します。"),
    );
  } else if (evaluation.status === "needs_rework") {
    status.append(
      element("strong", "", "評価基準を満たしていません"),
      element("span", "", "不合格理由と根拠を付けて、この問題だけ再整備します。"),
    );
    actions.append(actionWithHelp(
      "再整備を開始",
      "primary-button",
      () => openEvaluationRework(question),
      "再整備を開始",
      "別セッション評価の不合格理由、対象選択肢、推奨工程をCodex依頼へ含めます。",
    ));
  } else if (question.nextAction === "complete") {
    status.append(
      element("strong", "", "評価合格・Firestore反映済みです"),
      element("span", "", "この問題に属する全documentのreadbackが一致しています。"),
    );
  } else {
    const stats = firestoreDiffStats(question);
    const differenceSummary = [
      stats.missingCount ? `未登録${stats.missingCount}件` : "",
      stats.fieldCount ? `差分${stats.fieldCount}項目` : "",
    ].filter(Boolean).join("・");
    status.append(
      element("strong", "", "評価に合格し、公開可能です"),
      element("span", "", differenceSummary || "この問題だけを本番Firestoreへ反映できます。"),
    );
    if (firestoreNeedsAttention && ["mismatch", "missing"].includes(workflow.firestore)) {
      actions.append(actionWithHelp(
        "保存済み差分を見る",
        "secondary-button",
        scrollToFirestoreDiff,
        "保存済み差分を見る",
        "最後に資格単位で取得したFirestore値とローカル成果物の差分へ移動します。",
      ));
    }
    actions.append(actionWithHelp(
      "この問題をFirestoreへ反映",
      "primary-button",
      openPublishDialog,
      "Firestoreへ反映",
      "評価に合格したこの元問題に属する全documentだけを、確認画面を経て本番へ反映します。",
    ));
  }
  if (localReady && question.nextAction !== "complete") {
    actions.append(actionWithHelp(
      "資格のFirestoreを確認",
      "secondary-button",
      openReadbackDialog,
      "資格のFirestoreを確認",
      "選択中の資格全体を読み取り、問題ごとの保存済み差分を更新します。Firestoreは変更しません。",
    ));
  }
  node.append(status, actions);
  return node;
}

function openEvaluationRework(question) {
  const items = question.evaluation?.reworkItems || [];
  const choiceIndexes = [...new Set(items.flatMap((item) => item.choiceIndexes || []))];
  const stages = [...new Set(items.map((item) => item.stage).filter(Boolean))];
  const fields = stages.flatMap((stage) => {
    if (stage === "02a") return ["correctChoiceText"];
    if (stage === "02b" || stage === "03b") return ["lawReferences", "lawRevisionFacts"];
    if (stage === "03") return ["explanationText"];
    if (stage === "01") return ["questionType"];
    if (stage === "02") return ["questionIntent"];
    return [];
  });
  openReview(
    "awaiting_codex",
    {
      targetLabel: "別セッション評価の基準未達",
      dataPath: [...new Set(fields)].join(", "),
      fields: [...new Set(fields)],
      choiceIndexes,
      selectedText: items.map((item) => `${item.stage}: ${item.message}`).join("\n"),
    },
    "other",
    "current_question",
    "evaluation_rework",
  );
  $("#review-note").value = [
    "別セッション評価で基準未達となった問題を再整備する。",
    question.evaluation?.summary || "",
    ...items.map((item) => `${item.stage}: ${item.message}`),
  ].filter(Boolean).join("\n");
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
  if (value === undefined || value === null) {
    const node = element(
      "span",
      "firestore-diff-empty",
      value === undefined ? "fieldなし" : "null",
    );
    if (reviewContext) installReviewTarget(node, reviewContext);
    return node;
  }
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
  const listGroupId = state.detail?.listGroupId || state.listGroupId;
  return `/api/groups/${encodeURIComponent(state.qualification)}/${encodeURIComponent(listGroupId)}/${action}`;
}

function questionApiPath(action) {
  if (!state.detail?.id) throw new Error("対象問題を選択してください。");
  return `/api/questions/${encodeURIComponent(state.detail.id)}/${action}`;
}

function resetWorkflowDialog(mode, title) {
  state.workflowDialog = { mode, preview: null, running: false, questionIds: [] };
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
      summaryMetric("対象", `${qualificationDisplayName(preview.qualification)} / ${preview.listGroupId}`),
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

async function openEvaluationDialog(questionIds = []) {
  const selected = [...new Set(questionIds)].filter(Boolean);
  resetWorkflowDialog("evaluation", "選択した問題を別セッションで評価");
  state.workflowDialog.questionIds = selected;
  try {
    const preview = await api("/api/evaluations/preview", {
      method: "POST",
      body: { questionIds: selected },
    });
    state.workflowDialog.preview = preview;
    $("#workflow-dialog-summary").append(
      summaryMetric("資格", qualificationDisplayName(preview.qualification)),
      summaryMetric("年度", preview.listGroupIds?.join("・") || "-"),
      summaryMetric("選択", `${preview.selectedCount}問`),
      summaryMetric("評価可能", `${preview.evaluableCount}問`, preview.evaluableCount ? "good" : "danger"),
      summaryMetric("別セッション", `${preview.sessionCount}回`, preview.sessionCount ? "warning" : ""),
      summaryMetric("評価前整備", `${preview.blockedCount}問`, preview.blockedCount ? "warning" : "good"),
      summaryMetric("実行方式", preview.provider || "未設定"),
    );
    if (!preview.canStart) {
      $("#workflow-dialog-message").textContent =
        "選択した問題に評価可能な対象がありません。整備状態又はCodex App Serverを確認してください。";
      state.workflowDialog.mode = "";
      $("#workflow-execute").textContent = "閉じる";
      $("#workflow-execute").disabled = false;
      return;
    }
    $("#workflow-dialog-message").textContent = preview.blockedCount
      ? "評価可能な問題だけを開始し、整備が必要な問題はスキップします。各問題は独立した新しい別セッションで評価します。"
      : "選択した各問題を、独立した新しい別セッションで順に評価します。一問が失敗しても残りは続行します。";
    $("#workflow-execute").textContent = `${preview.evaluableCount}問の評価を開始`;
    $("#workflow-execute").disabled = false;
  } catch (error) {
    showWorkflowError(error);
  }
}

async function openPublishDialog() {
  resetWorkflowDialog("publish", "この問題をFirestoreへ反映");
  try {
    const preview = await api(questionApiPath("publish-preview"), { method: "POST", body: {} });
    state.workflowDialog.preview = preview;
    const summary = $("#workflow-dialog-summary");
    summary.append(
      summaryMetric("対象問題", preview.questionLabel || preview.originalQuestionId),
      summaryMetric("対象年度", preview.listGroupId),
      summaryMetric("本番project", preview.projectId),
    );
    if (!preview.publishReady) {
      $("#workflow-dialog-message").textContent = preview.reason || "本番反映の前提条件を満たしていません。";
      summary.append(summaryMetric("評価状態", EVALUATION_LABELS[preview.evaluationStatus] || preview.evaluationStatus, "danger"));
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
      $("#workflow-dialog-message").textContent = "この問題のupload-readyと本番Firestoreは一致しています。反映は不要です。";
      state.workflowDialog.mode = "";
      $("#workflow-execute").textContent = "閉じる";
      $("#workflow-execute").disabled = false;
      return;
    }
    $("#workflow-dialog-message").textContent =
      "評価に合格したこの元問題の全documentだけを本番へ差分反映し、直後にreadbackします。";
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
  const { mode, preview, questionIds } = state.workflowDialog;
  if (!mode || !preview) {
    $("#workflow-dialog").close();
    return;
  }
  if (mode === "publish" && !$("#production-confirm").checked) return;

  setWorkflowRunning(true);
  try {
    let path;
    let body;
    if (mode === "sync") {
      path = groupApiPath("sync");
      body = { previewToken: preview.previewToken };
    } else if (mode === "evaluation") {
      path = "/api/evaluations/start";
      body = { questionIds, previewToken: preview.previewToken };
    } else {
      path = questionApiPath("publish");
      body = { preflightToken: preview.preflightToken, confirmedProduction: true };
    }
    const job = await api(path, {
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
  if (mode === "evaluation") clearEvaluationSelection();
  await loadQualificationWorkflow(true);
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
  $("#readback-qualification").textContent = qualificationDisplayName();
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

function fieldLabel(key) {
  return FIELD_LABELS[key] || key;
}

function structuredDataPath(basePath, tokens) {
  return tokens.reduce(
    (path, token) => path + (Number.isInteger(token) ? `[${token}]` : `.${token}`),
    basePath,
  );
}

function structuredTargetContext(baseContext, pathTokens) {
  const dataPath = structuredDataPath(baseContext.dataPath, pathTokens);
  const pathLabel = pathTokens.map(fieldLabel).join(" / ");
  return {
    fields: baseContext.fields,
    choiceIndexes: baseContext.choiceIndexes
      || (Number.isInteger(pathTokens[0]) ? [pathTokens[0]] : []),
    targetLabel: pathLabel ? `${baseContext.targetLabel} / ${pathLabel}` : baseContext.targetLabel,
    dataPath,
  };
}

function renderStructuredValue(value, baseContext, pathTokens = []) {
  if (value === undefined || value === null || typeof value !== "object") {
    const text = value === undefined
      ? "fieldなし"
      : value === null
        ? "null"
        : typeof value === "boolean"
          ? value ? "はい" : "いいえ"
          : String(value);
    const node = /^https?:\/\//.test(text)
      ? element("a", "structured-link", text)
      : element("span", "structured-value", text);
    if (node.tagName === "A") {
      node.href = text;
      node.target = "_blank";
      node.rel = "noreferrer";
    }
    installReviewTarget(node, structuredTargetContext(baseContext, pathTokens));
    return node;
  }

  if (Array.isArray(value)) {
    if (!value.length) return element("span", "structured-empty", "なし");
    const list = element("ol", "structured-list");
    value.forEach((item, index) => {
      const row = document.createElement("li");
      row.append(renderStructuredValue(item, baseContext, [...pathTokens, index]));
      list.append(row);
    });
    return list;
  }

  const entries = Object.entries(value);
  if (!entries.length) return element("span", "structured-empty", "なし");
  const object = element("div", "structured-object");
  for (const [key, item] of entries) {
    const row = element("div", "structured-row");
    row.append(
      element("div", "structured-key", fieldLabel(key)),
      element("div", "structured-cell"),
    );
    row.children[1].append(
      renderStructuredValue(item, baseContext, [...pathTokens, key]),
    );
    object.append(row);
  }
  return object;
}

function articleLocation(value) {
  if (!value || typeof value !== "object") return "";
  return [
    value.article ? `第${value.article}条` : "",
    value.paragraph ? `第${value.paragraph}項` : "",
    value.item ? `第${value.item}号` : "",
  ].filter(Boolean).join(" ");
}

function lawReferenceEntries(value) {
  const entries = [];
  const choices = Array.isArray(value) ? value : value ? [value] : [];
  choices.forEach((choiceValue, outerIndex) => {
    const references = Array.isArray(choiceValue) ? choiceValue : [choiceValue];
    references.filter((reference) => reference && typeof reference === "object")
      .forEach((reference, referenceIndex) => {
        const choiceIndex = Number.isInteger(reference.choiceIndex)
          ? reference.choiceIndex
          : outerIndex;
        entries.push({ reference, choiceIndex, outerIndex, referenceIndex });
      });
  });
  return entries;
}

function renderLawReferences(value) {
  const entries = lawReferenceEntries(value);
  if (!entries.length) return element("p", "structured-empty", "条文根拠はありません。");
  const list = element("div", "law-entry-list");
  entries.forEach(({ reference, choiceIndex, outerIndex, referenceIndex }, index) => {
    const details = document.createElement("details");
    details.className = "law-entry";
    details.open = index === 0;
    const title = [
      `選択肢${choiceIndex + 1}`,
      reference.lawTitle || reference.lawAlias || "法令名なし",
      articleLocation(reference),
    ].filter(Boolean).join(" / ");
    details.append(element("summary", "", title));
    const content = element("div", "details-content");
    content.append(renderStructuredValue(reference, {
      fields: ["lawReferences"],
      choiceIndexes: [choiceIndex],
      targetLabel: `条文根拠 / 選択肢${choiceIndex + 1}`,
      dataPath: `lawReferences[${outerIndex}][${referenceIndex}]`,
    }));
    details.append(content);
    list.append(details);
  });
  return list;
}

function renderLawRevisionFacts(value) {
  const facts = Array.isArray(value) ? value : value ? [value] : [];
  if (!facts.length) return element("p", "structured-empty", "法令監査情報はありません。");
  const list = element("div", "law-entry-list");
  facts.forEach((fact, index) => {
    const details = document.createElement("details");
    details.className = "law-entry";
    details.open = index === 0;
    const auditStatus = fact?.auditStatus || "監査状態なし";
    const currentVerdict = fact?.current?.correctChoiceText || "現行法判定なし";
    details.append(element(
      "summary",
      "",
      `選択肢${index + 1} / ${auditStatus} / ${currentVerdict}`,
    ));
    const content = element("div", "details-content");
    content.append(renderStructuredValue(fact, {
      fields: ["lawRevisionFacts"],
      choiceIndexes: [index],
      targetLabel: `法令監査情報 / 選択肢${index + 1}`,
      dataPath: `lawRevisionFacts[${index}]`,
    }));
    details.append(content);
    list.append(details);
  });
  return list;
}

function renderLawSection(projected) {
  const node = section("法令根拠");
  const references = document.createElement("details");
  references.open = true;
  references.append(element("summary", "", "条文根拠"));
  const content = element("div", "details-content");
  content.append(renderLawReferences(projected.lawReferences));
  references.append(content);
  const facts = document.createElement("details");
  facts.append(element("summary", "", "法令監査情報"));
  const factContent = element("div", "details-content");
  factContent.append(renderLawRevisionFacts(projected.lawRevisionFacts));
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

function structuredValueSummary(value) {
  if (Array.isArray(value)) return `${value.length}件`;
  if (value && typeof value === "object") return `${Object.keys(value).length}項目`;
  if (value === undefined) return "fieldなし";
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "はい" : "いいえ";
  const text = String(value);
  return text.length > 42 ? `${text.slice(0, 41)}…` : text;
}

function renderProjectedData(projected) {
  const details = document.createElement("details");
  details.className = "projected-data";
  const summary = element("summary", "structured-summary");
  summary.append(
    element("span", "", "パッチ適用後データ"),
    helpIcon(
      "パッチ適用後データとは",
      "00_sourceに各patchを重ねた、Merge直前の問題データです。パッチの修正が想定したfieldに反映され、別のfieldを壊していないか確認するために表示します。JSONファイルの表示ではなく、fieldごとに確認できます。",
      "パッチ適用後データの説明",
    ),
  );
  details.append(summary);
  const content = element("div", "details-content projected-field-list");
  const entries = Object.entries(projected || {});
  if (!entries.length) {
    content.append(element("p", "structured-empty", "パッチ適用後データがありません。"));
  }
  for (const [field, value] of entries) {
    const fieldDetails = document.createElement("details");
    fieldDetails.className = "projected-field";
    const fieldSummary = element("summary", "projected-field-summary");
    fieldSummary.append(
      element("span", "", fieldLabel(field)),
      element("code", "", field),
      element("span", "projected-field-preview", structuredValueSummary(value)),
    );
    fieldDetails.append(fieldSummary);
    const fieldContent = element("div", "details-content");
    fieldContent.append(renderStructuredValue(value, {
      fields: [field],
      targetLabel: `パッチ適用後データ / ${fieldLabel(field)}`,
      dataPath: field,
    }));
    fieldDetails.append(fieldContent);
    content.append(fieldDetails);
  }
  details.append(content);
  return details;
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

  node.append(diffDetails, pathDetails, renderProjectedData(question.projected));
  return node;
}

function topLevelDifferences(left, right) {
  if (!left || !right) return ["対応データなし"];
  const ignored = new Set(["updatedAt", "createdAt"]);
  return [...new Set([...Object.keys(left), ...Object.keys(right)])]
    .filter((key) => !ignored.has(key) && JSON.stringify(left[key]) !== JSON.stringify(right[key]))
    .sort();
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

function openReview(
  mode,
  selection = null,
  issueType = "",
  investigationScope = "current_question",
  requestKind = "",
) {
  if (!state.detail) return;
  state.reviewMode = mode;
  state.reviewRequestKind = requestKind;
  state.reviewSelection = selection;
  const rework = requestKind === "evaluation_rework";
  $("#review-dialog-title").textContent = rework
    ? "再整備を開始"
    : selection
      ? "選択した箇所を整備"
      : "整備を開始";
  $("#review-submit").textContent = rework ? "再整備を開始" : "整備を開始";
  const firstIssue = issueType || state.detail.issueCodes[0] || "other";
  $("#review-issue").value = ISSUE_LABELS[firstIssue] ? firstIssue : "other";
  $("#review-note").value = "";
  $("#review-expected").value = "";
  const qualificationLawAudit = requestKind === "qualification_law_audit";
  $("#review-scope").value = qualificationLawAudit
    ? "qualification"
    : REVIEW_SCOPES.has(investigationScope)
      ? investigationScope
      : "current_question";
  $("#review-scope-wrap").hidden = qualificationLawAudit;

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
        startCodex: state.reviewMode === "awaiting_codex",
        review: {
          choiceIndexes,
          fields,
          issueTypes: [$("#review-issue").value],
          requestKind: state.reviewRequestKind,
          note: $("#review-note").value,
          expectedOutcome: $("#review-expected").value,
          selection: state.reviewSelection,
          investigationScope: $("#review-scope").value,
        },
      },
    });
    $("#review-dialog").close();
    if (review.job) {
      resetWorkflowDialog(
        "codexReview",
        state.reviewRequestKind === "evaluation_rework" ? "再整備を実行" : "整備を実行",
      );
      state.workflowDialog.preview = {};
      setWorkflowRunning(true);
      try {
        await pollJob(review.job.jobId, "codexReview");
      } catch (error) {
        showWorkflowError(error);
      }
    } else {
      toast("指摘を記録しました。");
    }
    await loadQuestions(true);
  } catch (error) {
    toast(error.message, true);
  }
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
      await loadQualificationWorkflow(true);
      await loadQualificationRuns();
      await loadQuestions(true);
    }
  } catch (_) {
    // 常時pollの一時的エラーは次回の取得で回復させる。
  }
}

initialize();
