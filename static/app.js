const state = {
  result: null,
  memories: [],
  feedbackStats: null,
  feedbackCache: {},
  feedbackAvailable: false,
  canDeleteMemories: false,
  candidateIndex: 0,
  activeTab: "overview",
};

const ACTIVE_ANALYSIS_KEY = "hireagent-active-analysis";
const VIEW_MEMORY_KEY = "hireagent-view-memory";
const SIDEBAR_VIEW_KEY = "hireagent-sidebar-view";
const FEEDBACK_ISSUES = {
  hallucinated_skill: "技能幻觉",
  missed_skill: "遗漏技能",
  years_error: "年限误判",
  education_error: "学历误判",
  achievement_error: "成果误判",
  score_too_high: "评分偏高",
  score_too_low: "评分偏低",
  question_hallucination: "题目虚构经历",
  ocr_error: "OCR 识别问题",
  other: "其他",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const escapeHtml = (value = "") =>
  String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

const recommendationClass = (value) => {
  if (value === "建议推进") return "good";
  if (value === "谨慎推进") return "caution";
  return "stop";
};

const setTheme = (theme) => {
  $("#app").dataset.theme = theme;
  $("#light-theme").classList.toggle("active", theme === "light");
  $("#dark-theme").classList.toggle("active", theme === "dark");
  localStorage.setItem("hireagent-theme", theme);
};

const getViewMemory = () => {
  try {
    return JSON.parse(localStorage.getItem(VIEW_MEMORY_KEY) || "{}");
  } catch {
    return {};
  }
};

const rememberView = () => {
  if (!state.result) return;
  const memory = getViewMemory();
  memory[state.result.analysis_id] = {
    candidateIndex: state.candidateIndex,
    activeTab: state.activeTab,
  };
  const recentIds = Object.keys(memory).slice(-30);
  const boundedMemory = Object.fromEntries(recentIds.map((id) => [id, memory[id]]));
  localStorage.setItem(VIEW_MEMORY_KEY, JSON.stringify(boundedMemory));
};

const formatMemoryTime = (value) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const today = new Date();
  const sameDay = date.toDateString() === today.toDateString();
  return sameDay
    ? `今天 ${date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`
    : date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
};

const setSidebarView = (view) => {
  const activeView = view === "memory" ? "memory" : "input";
  $(".input-panel").classList.toggle("hidden", activeView !== "input");
  $(".memory-panel").classList.toggle("hidden", activeView !== "memory");
  $$(".sidebar-tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.sidebarView === activeView);
  });
  localStorage.setItem(SIDEBAR_VIEW_KEY, activeView);
};

const renderMemories = () => {
  const activeId = state.result?.analysis_id || localStorage.getItem(ACTIVE_ANALYSIS_KEY);
  $("#memory-summary").textContent = state.memories.length
    ? `已保存 ${state.memories.length} 次分析`
    : "暂无历史记录";
  $("#memory-tab-count").textContent = state.memories.length;
  $("#memory-list").innerHTML = state.memories.length
    ? state.memories
        .map(
          (memory) => `
            <div class="memory-item ${memory.analysis_id === activeId ? "active" : ""} ${state.canDeleteMemories ? "deletable" : ""}">
              <button class="memory-open" type="button" data-memory-id="${memory.analysis_id}">
                <strong>${escapeHtml(memory.job_title)}</strong>
                <span>${memory.candidate_count} 位候选人 · ${escapeHtml(formatMemoryTime(memory.created_at))}</span>
              </button>
              ${
                state.canDeleteMemories
                  ? `<button
                      class="memory-delete"
                      type="button"
                      data-delete-id="${memory.analysis_id}"
                      aria-label="删除 ${escapeHtml(memory.job_title)} 的分析记录"
                      title="删除记录"
                    >×</button>`
                  : ""
              }
            </div>
          `,
        )
        .join("")
    : '<p class="memory-empty">完成一次分析后会自动保存在这里。</p>';

  $$(".memory-open").forEach((button) => {
    button.addEventListener("click", () => loadAnalysis(button.dataset.memoryId));
  });
  $$(".memory-delete").forEach((button) => {
    button.addEventListener("click", () => deleteAnalysis(button.dataset.deleteId));
  });
};

const updateSelectedFiles = () => {
  const jd = $("#jd-file").files[0];
  const jdText = $("#jd-text").value.trim();
  const resumes = Array.from($("#resume-files").files);
  $("#jd-file-name").textContent = jdText
    ? "将优先使用上方粘贴内容"
    : jd?.name || "PDF、Word 或文本";
  $("#jd-char-count").textContent = `${jdText.length} 字`;
  $("#resume-file-name").textContent = resumes.length
    ? `${resumes.length} 份简历`
    : "支持多选，最多 10 份";
  $("#selected-files").innerHTML = resumes
    .slice(0, 10)
    .map((file) => `<div class="selected-file">${escapeHtml(file.name)}</div>`)
    .join("");
};

const setBusy = (busy, message = "") => {
  $("#analyze-btn").disabled = busy;
  $("#analyze-btn").textContent = busy ? "分析中..." : "开始分析";
  if (message) $("#service-state").textContent = message;
};

const showError = (message) => {
  document.querySelector(".error-banner")?.remove();
  const node = document.createElement("div");
  node.className = "error-banner";
  node.textContent = message;
  $(".workspace").prepend(node);
};

const callApi = async (url, options = {}, timeoutMs = 30000) => {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetch(url, { ...options, signal: controller.signal });
  } catch (error) {
    if (error.name === "AbortError") {
      throw new Error("请求超时，请检查模型网络或切换 AI_MODE=off 后重试。");
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {
      const payload = await response.json();
      message = payload.detail || message;
    } catch {
      // Keep the status message.
    }
    throw new Error(message);
  }
  return response.json();
};

const renderTrace = () => {
  const trace = state.result?.trace || [];
  const warnings = state.result?.warnings || [];
  $("#workflow-trace").innerHTML = trace.length || warnings.length
    ? [
        ...trace.map((item) => `<li>${escapeHtml(item.message)}</li>`),
        ...warnings.map((item) => `<li class="trace-warning">${escapeHtml(item)}</li>`),
      ].join("")
    : "<li>等待任务</li>";
};

const renderCandidates = () => {
  const candidates = state.result.candidates;
  $("#candidate-list").innerHTML = candidates
    .map(
      (item, index) => `
        <button class="candidate-card ${index === state.candidateIndex ? "active" : ""}" data-index="${index}">
          <span class="score-ring">${item.score}</span>
          <span>
            <strong>${escapeHtml(item.profile.name)}</strong>
            <p>${escapeHtml(item.profile.source_file)}</p>
            <span class="recommendation ${recommendationClass(item.recommendation)}">
              ${escapeHtml(item.recommendation)}
            </span>
          </span>
        </button>
      `,
    )
    .join("");
  $$(".candidate-card").forEach((button) => {
    button.addEventListener("click", () => {
      state.candidateIndex = Number(button.dataset.index);
      rememberView();
      renderCandidates();
      renderCandidateDetail();
    });
  });
};

const renderCandidateHeader = (candidate) => {
  $("#candidate-header").innerHTML = `
    <h3>${escapeHtml(candidate.profile.name)}</h3>
    <p>
      ${escapeHtml(candidate.profile.education || "学历未明确")} ·
      ${candidate.profile.years_experience || 0} 年经验 ·
      ${candidate.profile.skills.length} 项已识别技能 ·
      匹配度 ${candidate.score}/100
    </p>
  `;
};

const tags = (items, kind) =>
  items.length
    ? items.map((item) => `<span class="tag ${kind}">${escapeHtml(item)}</span>`).join("")
    : '<span class="tag">暂无</span>';

const list = (items) =>
  items.length
    ? `<ul class="clean-list">${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
    : '<p class="service-state">暂无</p>';

const auditHtml = () => {
  const audit = state.result?.quality_audit;
  if (!audit?.checks?.length) {
    return '<p class="service-state">质量审计尚未运行。</p>';
  }
  return `
    <ul class="audit-list">
      ${audit.checks
        .map(
          (check) => {
            const checkName = check.name === "风险提示" ? "运行提示" : check.name;
            return `
            <li class="audit-item ${check.status}">
              <span>${check.status === "pass" ? "通过" : check.status === "warn" ? "提示" : "失败"}</span>
              <strong>${escapeHtml(checkName)}</strong>
              <p>${escapeHtml(check.message)}</p>
            </li>
          `;
          },
        )
        .join("")}
    </ul>
  `;
};

const overviewHtml = (candidate) => {
  const b = candidate.breakdown;
  return `
    <div class="metric-grid">
      <div class="metric"><span>技能匹配</span><strong>${b.skills}/45</strong></div>
      <div class="metric"><span>经验匹配</span><strong>${b.experience}/20</strong></div>
      <div class="metric"><span>学历匹配</span><strong>${b.education}/10</strong></div>
      <div class="metric"><span>成果质量</span><strong>${b.achievements}/15</strong></div>
      <div class="metric"><span>证据质量</span><strong>${b.evidence_quality}/10</strong></div>
    </div>
    <div class="content-grid">
      <section class="content-block">
        <h4>已匹配要求</h4>
        <div class="tag-list">${tags(candidate.matched_requirements, "matched")}</div>
      </section>
      <section class="content-block">
        <h4>待验证要求</h4>
        <div class="tag-list">${tags(candidate.missing_requirements, "missing")}</div>
      </section>
      <section class="content-block">
        <h4>推荐理由</h4>
        ${list(candidate.reasons)}
      </section>
      <section class="content-block">
        <h4>量化成果</h4>
        ${list(candidate.profile.achievements)}
      </section>
      <section class="content-block wide">
        <h4>质量审计</h4>
        <p class="service-state">${escapeHtml(state.result.quality_audit?.summary || "质量审计尚未运行")}</p>
        ${auditHtml()}
      </section>
    </div>
  `;
};

const questionsHtml = (candidate) => `
  <div class="question-list">
    ${candidate.interview_questions
      .map(
        (item, index) => `
          <article class="question-card">
            <div class="question-head">
              <h4>${index + 1}. ${escapeHtml(item.question)}</h4>
              <span class="difficulty">${escapeHtml(item.difficulty)}</span>
            </div>
            <p><strong>考察点：</strong>${escapeHtml(item.focus)}</p>
            <p><strong>评分标准：</strong>${escapeHtml(item.scoring_criteria.join("；"))}</p>
          </article>
        `,
      )
      .join("")}
  </div>
`;

const followupsHtml = (candidate) => `
  <div class="content-grid">
    <section class="content-block">
      <h4>动态追问</h4>
      ${list(candidate.follow_up_questions)}
    </section>
    <section class="content-block">
      <h4>风险与信息缺口</h4>
      ${list(candidate.profile.risks)}
    </section>
    <section class="content-block">
      <h4>经历亮点</h4>
      ${list(candidate.profile.experience_highlights)}
    </section>
    <section class="content-block">
      <h4>已识别技能</h4>
      <div class="tag-list">${tags(candidate.profile.skills, "matched")}</div>
    </section>
  </div>
`;

const evidenceHtml = (candidate) => `
  <div class="evidence-list">
    ${
      candidate.profile.evidence.length
        ? candidate.profile.evidence
            .map(
              (item) => `
                <article class="evidence-card">
                  <span class="tag matched">${escapeHtml(item.field)}</span>
                  <p>${escapeHtml(item.snippet)}</p>
                </article>
              `,
            )
            .join("")
        : '<p class="service-state">未提取到可定位证据。</p>'
    }
  </div>
`;

const feedbackKey = (candidate) =>
  `${state.result.analysis_id}:${candidate.profile.candidate_id}`;

const feedbackHtml = (candidate, feedback) => `
  <div class="feedback-layout">
    <section class="feedback-form">
      <div class="feedback-intro">
        <div>
          <p class="eyebrow">Human Review</p>
          <h4>人工复核结果</h4>
        </div>
        <span class="feedback-save-state">${feedback ? "已保存" : "待复核"}</span>
      </div>

      <fieldset class="feedback-fieldset">
        <legend>分析准确性</legend>
        <div class="segmented-control">
          ${[
            ["accurate", "准确"],
            ["partially_accurate", "部分准确"],
            ["inaccurate", "不准确"],
          ]
            .map(
              ([value, label]) => `
                <label>
                  <input
                    type="radio"
                    name="review-status"
                    value="${value}"
                    ${feedback?.review_status === value ? "checked" : ""}
                  />
                  <span>${label}</span>
                </label>
              `,
            )
            .join("")}
        </div>
      </fieldset>

      <label class="feedback-label" for="human-recommendation">人工推进建议</label>
      <select id="human-recommendation" class="feedback-select">
        <option value="">暂不判断</option>
        ${["建议推进", "谨慎推进", "暂不推进"]
          .map(
            (item) => `
              <option value="${item}" ${feedback?.human_recommendation === item ? "selected" : ""}>
                ${item}
              </option>
            `,
          )
          .join("")}
      </select>

      <fieldset class="feedback-fieldset">
        <legend>发现的问题</legend>
        <div class="issue-grid">
          ${Object.entries(FEEDBACK_ISSUES)
            .map(
              ([value, label]) => `
                <label class="issue-option">
                  <input
                    type="checkbox"
                    value="${value}"
                    ${(feedback?.issue_types || []).includes(value) ? "checked" : ""}
                  />
                  <span>${label}</span>
                </label>
              `,
            )
            .join("")}
        </div>
      </fieldset>

      <label class="feedback-label" for="feedback-notes">复核备注</label>
      <textarea
        id="feedback-notes"
        class="feedback-notes"
        maxlength="1000"
        placeholder="记录需要回看的具体问题。备注仅用于人工审计，不会直接进入模型 Prompt。"
      >${escapeHtml(feedback?.notes || "")}</textarea>

      <button
        id="save-feedback"
        class="primary-btn feedback-submit"
        type="button"
        ${state.feedbackAvailable ? "" : "disabled"}
      >
        ${state.feedbackAvailable ? "保存复核" : "重启后可保存"}
      </button>
      <p id="feedback-message" class="service-state">
        ${
          state.feedbackAvailable
            ? "系统只学习结构化错误类型，不会自动学习录用偏好。"
            : "反馈飞轮后端尚未加载，请重启 HireAgent。"
        }
      </p>
    </section>

    <aside class="flywheel-panel">
      <p class="eyebrow">Controlled Flywheel</p>
      <h4>受控反馈飞轮</h4>
      <div class="flywheel-metrics">
        <div><span>累计复核</span><strong>${state.feedbackStats?.total_reviews || 0}</strong></div>
        <div><span>建议一致率</span><strong>${state.feedbackStats?.agreement_rate || 0}%</strong></div>
        <div><span>本次加载</span><strong>${state.result.feedback_memory_used || 0}</strong></div>
      </div>
      <h5>当前可靠性策略</h5>
      ${
        state.feedbackStats?.reliability_guidance?.length
          ? list(state.feedbackStats.reliability_guidance)
          : '<p class="service-state">积累人工复核后，系统会在这里形成可靠性经验。</p>'
      }
    </aside>
  </div>
`;

const loadCandidateFeedback = async (candidate) => {
  const key = feedbackKey(candidate);
  if (Object.hasOwn(state.feedbackCache, key)) return;
  try {
    state.feedbackCache[key] = await callApi(
      `/api/v1/analyses/${encodeURIComponent(state.result.analysis_id)}` +
        `/candidates/${encodeURIComponent(candidate.profile.candidate_id)}/feedback`,
    );
    if (
      state.activeTab === "feedback" &&
      state.result.candidates[state.candidateIndex].profile.candidate_id === candidate.profile.candidate_id
    ) {
      renderCandidateDetail();
    }
  } catch {
    state.feedbackCache[key] = null;
    if (state.activeTab === "feedback") renderCandidateDetail();
  }
};

const saveCandidateFeedback = async (candidate) => {
  if (!state.feedbackAvailable) return;
  const reviewStatus = document.querySelector('input[name="review-status"]:checked')?.value;
  if (!reviewStatus) {
    $("#feedback-message").textContent = "请先选择分析准确性。";
    return;
  }
  const button = $("#save-feedback");
  button.disabled = true;
  button.textContent = "保存中...";
  const payload = {
    review_status: reviewStatus,
    human_recommendation: $("#human-recommendation").value || null,
    issue_types: $$(".issue-option input:checked").map((item) => item.value),
    notes: $("#feedback-notes").value.trim(),
  };
  try {
    const saved = await callApi(
      `/api/v1/analyses/${encodeURIComponent(state.result.analysis_id)}` +
        `/candidates/${encodeURIComponent(candidate.profile.candidate_id)}/feedback`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
    state.feedbackCache[feedbackKey(candidate)] = saved;
    await loadFeedbackStats();
    renderCandidateDetail();
    $("#feedback-message").textContent = "复核已保存，将用于后续分析的可靠性策略。";
  } catch (error) {
    $("#feedback-message").textContent = error.message;
    button.disabled = false;
    button.textContent = "保存复核";
  }
};

const renderCandidateDetail = () => {
  const candidate = state.result.candidates[state.candidateIndex];
  renderCandidateHeader(candidate);
  if (state.activeTab === "feedback") {
    const key = feedbackKey(candidate);
    $("#tab-content").innerHTML = Object.hasOwn(state.feedbackCache, key)
      ? feedbackHtml(candidate, state.feedbackCache[key])
      : '<p class="service-state">正在读取人工复核...</p>';
    if (!Object.hasOwn(state.feedbackCache, key)) {
      loadCandidateFeedback(candidate);
      return;
    }
    $("#save-feedback")?.addEventListener("click", () => saveCandidateFeedback(candidate));
    return;
  }
  const html =
    state.activeTab === "questions"
      ? questionsHtml(candidate)
      : state.activeTab === "followups"
        ? followupsHtml(candidate)
        : state.activeTab === "evidence"
          ? evidenceHtml(candidate)
          : overviewHtml(candidate);
  $("#tab-content").innerHTML = html;
};

const renderJobProfile = (job) => {
  $("#job-profile-panel").classList.remove("hidden");
  $("#job-required-tags").innerHTML = tags(job.required_skills || [], "matched");
  $("#job-preferred-tags").innerHTML = tags(job.preferred_skills || [], "");
  const yearText = job.minimum_years > 0 ? `${job.minimum_years} 年经验` : "年限未明确";
  const educationText = job.education ? `${job.education}及以上` : "学历未明确";
  $("#job-threshold").textContent = `${yearText} · ${educationText}`;
};

const renderResult = (result, restoreView = false) => {
  state.result = result;
  const view = restoreView ? getViewMemory()[result.analysis_id] : null;
  const candidateIndex = Number(view?.candidateIndex || 0);
  state.candidateIndex = Math.min(candidateIndex, Math.max(result.candidates.length - 1, 0));
  state.activeTab = ["overview", "questions", "followups", "evidence", "feedback"].includes(view?.activeTab)
    ? view.activeTab
    : "overview";
  localStorage.setItem(ACTIVE_ANALYSIS_KEY, result.analysis_id);
  $("#empty-state").classList.add("hidden");
  $("#result-view").classList.remove("hidden");
  $("#job-title").textContent = result.job.title;
  const modelCalls = result.model_calls || [];
  const successfulCalls = modelCalls.filter((item) => item.status === "success").length;
  const modelLabel = !modelCalls.length
    ? result.ai_enhanced
      ? "Qwen 增强分析（历史记录）"
      : "确定性降级模式"
    : result.ai_enhanced
    ? successfulCalls === modelCalls.length
      ? "Qwen 增强分析"
      : `Qwen 部分增强 ${successfulCalls}/${modelCalls.length}`
    : "确定性降级模式";
  $("#job-meta").textContent =
    `${result.job.required_skills.length} 项核心要求 · ` +
    `${result.job.preferred_skills.length} 项加分项 · ` +
    modelLabel;
  $("#candidate-count").textContent = `${result.candidates.length} 位候选人`;
  const audit = result.quality_audit;
  $("#audit-status").textContent = audit
    ? audit.passed
      ? `质量审计通过 · ${audit.summary}`
      : `质量审计失败 · ${audit.summary}`
    : "质量审计待运行";
  $("#audit-status").classList.toggle("audit-pass", audit?.passed === true);
  $("#audit-status").classList.toggle("audit-fail", audit?.passed === false);
  $("#analysis-id").textContent = `ID ${result.analysis_id}`;
  $("#report-link").href = `/api/v1/analyses/${encodeURIComponent(result.analysis_id)}/report`;
  $("#report-link").download = `hireagent-${result.analysis_id}-report.md`;
  $("#report-link").classList.remove("hidden");
  $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === state.activeTab));
  renderJobProfile(result.job);
  renderTrace();
  renderCandidates();
  renderCandidateDetail();
  renderMemories();
  rememberView();
};

const loadAnalysis = async (analysisId, silent = false) => {
  if (!analysisId) return;
  if (!silent) $("#service-state").textContent = "正在恢复分析记录...";
  try {
    const result = await callApi(`/api/v1/analyses/${encodeURIComponent(analysisId)}`);
    renderResult(result, true);
    $("#service-state").textContent = "已恢复历史分析";
  } catch (error) {
    localStorage.removeItem(ACTIVE_ANALYSIS_KEY);
    if (!silent) showError(error.message);
  }
};

const loadMemories = async ({ restore = false } = {}) => {
  try {
    state.memories = await callApi("/api/v1/analyses");
    renderMemories();
    if (restore && state.memories.length) {
      const rememberedId = localStorage.getItem(ACTIVE_ANALYSIS_KEY);
      const target = state.memories.some((item) => item.analysis_id === rememberedId)
        ? rememberedId
        : state.memories[0].analysis_id;
      await loadAnalysis(target, true);
    }
  } catch (error) {
    $("#memory-summary").textContent = "历史记录读取失败";
    if (!restore) showError(error.message);
  }
};

const deleteAnalysis = async (analysisId) => {
  const memory = state.memories.find((item) => item.analysis_id === analysisId);
  if (!window.confirm(`确定删除“${memory?.job_title || "这条"}”分析记录吗？`)) return;
  try {
    await callApi(`/api/v1/analyses/${encodeURIComponent(analysisId)}`, { method: "DELETE" });
    const wasActive = state.result?.analysis_id === analysisId;
    const viewMemory = getViewMemory();
    delete viewMemory[analysisId];
    localStorage.setItem(VIEW_MEMORY_KEY, JSON.stringify(viewMemory));
    if (wasActive) {
      state.result = null;
      localStorage.removeItem(ACTIVE_ANALYSIS_KEY);
      $("#report-link").classList.add("hidden");
    }
    await loadMemories({ restore: wasActive });
  } catch (error) {
    showError(error.message);
  }
};

const analyzeUploads = async () => {
  const jdText = $("#jd-text").value.trim();
  const jdFile = $("#jd-file").files[0];
  const resumes = Array.from($("#resume-files").files).slice(0, 10);
  if (!jdText && !jdFile) {
    showError("请粘贴职位描述或上传一份 JD 文件。");
    return;
  }
  if (!resumes.length) {
    showError("请至少上传一份候选人简历。");
    return;
  }
  const form = new FormData();
  const jd = jdText
    ? new File([jdText], "pasted_job_description.txt", { type: "text/plain" })
    : jdFile;
  form.append("jd", jd);
  resumes.forEach((file) => form.append("resumes", file));
  setBusy(true, "正在解析文档并执行招聘分析...");
  try {
    renderResult(await callApi("/api/v1/analyze", { method: "POST", body: form }, 300000));
    await loadMemories();
    $("#service-state").textContent = "分析完成";
  } catch (error) {
    showError(error.message);
    $("#service-state").textContent = "分析失败";
  } finally {
    setBusy(false);
  }
};

const checkHealth = async () => {
  try {
    const health = await callApi("/api/v1/health");
    state.feedbackAvailable = health.feedback_flywheel === true;
    state.canDeleteMemories = health.memory_management === true;
    renderMemories();
    $("#service-state").textContent =
      `服务已连接 · ${health.ocr_available ? "OCR 可用" : "未检测到 OCR"}`;
    $("#ai-status").textContent = health.ai_enabled
      ? `${health.model} 已启用 · ${health.dashscope_proxy_mode || "direct"}`
      : "规则引擎演示模式";
  } catch {
    $("#service-state").textContent = "服务未连接";
    $("#ai-status").textContent = "后端离线";
  }
};

const loadFeedbackStats = async () => {
  try {
    state.feedbackStats = await callApi("/api/v1/feedback/stats");
    $("#feedback-status").textContent = `反馈飞轮 ${state.feedbackStats.total_reviews}`;
  } catch {
    state.feedbackStats = null;
    $("#feedback-status").textContent = "反馈飞轮待重启";
  }
};

$("#jd-file").addEventListener("change", updateSelectedFiles);
$("#jd-text").addEventListener("input", () => {
  updateSelectedFiles();
});
$("#resume-files").addEventListener("change", updateSelectedFiles);
$("#analyze-btn").addEventListener("click", analyzeUploads);
$("#refresh-memory").addEventListener("click", () => loadMemories());
$$(".sidebar-tab").forEach((tab) => {
  tab.addEventListener("click", () => setSidebarView(tab.dataset.sidebarView));
});
$("#light-theme").addEventListener("click", () => setTheme("light"));
$("#dark-theme").addEventListener("click", () => setTheme("dark"));
$$(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    state.activeTab = tab.dataset.tab;
    rememberView();
    $$(".tab").forEach((item) => item.classList.toggle("active", item === tab));
    renderCandidateDetail();
  });
});

const initialize = async () => {
  setTheme(localStorage.getItem("hireagent-theme") || "dark");
  setSidebarView(localStorage.getItem(SIDEBAR_VIEW_KEY) || "input");
  localStorage.removeItem("hireagent-jd-draft");
  $("#jd-text").value = "";
  updateSelectedFiles();
  await Promise.all([
    checkHealth(),
    loadFeedbackStats(),
    loadMemories({ restore: true }),
  ]);
};

initialize();
