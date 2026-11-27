import {initializeApiKey, ensureApiKey, openApiKeyDialog} from "/static/ai-key.js";

const active = new Set(["preparing", "generating", "validating", "saving"]);
export function analysisLink(link, recordId, courseId, source) {
  return link("analysis", "AI 分析", "secondary analysis-link", {record:recordId, start:"1", ...(courseId ? {course:courseId} : {}), ...(source ? {source} : {})});
}

export async function programmingAdvicePage(ctx, runId, autoStart) {
  if (!ctx.config.features?.python_ai_advice) throw new Error("本地服务尚未加载代码 AI 辅助，请重新启动服务后刷新页面。");
  if (!runId) throw new Error("缺少运行记录，请从编程题运行结果打开分析。");
  const endpoint = `/api/programming-runs/${encodeURIComponent(runId)}/advice`;
  const state = await ctx.api(endpoint), record = state.record;
  const title = state.kind === "debug" ? "AI Debug" : "智能复杂度分析";
  return analysisPage(ctx, runId, autoStart, {
    endpoint, title,
    description:state.kind === "debug" ? "根据本次运行的题目、代码快照和反馈分析错误原因，给出修复步骤及建议测试。" : "根据本次通过的代码分析时间与空间复杂度，说明推导、适用假设和优化建议。",
    note:"分析使用本次实际运行的代码快照；修改代码后请先重新运行。建议代码不会自动执行或替换编辑器。",
    metadata:value => `${value.title} · ${value.mode} · 通过 ${value.passed}/${value.total} 个用例`,
    backLink:ctx.link("programming", "返回编程题", "secondary", {course:record.course_id, question:record.question_id})
  });
}

export async function analysisPage(ctx, recordId, autoStart, options={}) {
  const {api, layout, root, user, config, link} = ctx;
  if (!recordId) throw new Error("缺少提交记录，请从答题结果或学习记录打开 AI 分析。");
  if (!config.features?.ai_analysis) throw new Error("本地服务尚未加载 AI 分析，请重新启动服务后刷新页面。");
  const title = options.title || "AI 学习分析";
  const description = options.description || "根据本次全部题目、学生作答和判分结果生成错题解析、掌握情况与复习建议。";
  const backLink = options.backLink || (user.role === "teacher" && ctx.params.get("course")
    ? link("teaching-ai", "返回课程 AI 分析", "secondary", {course:ctx.params.get("course")})
    : user.role === "teacher" ? link("records", "返回学情总览", "secondary") : link("learning-center", "返回学生画像", "secondary", {tab:ctx.params.get("source") === "assignment" ? "assignments" : "practice"}));
  layout(`<div class="learning-panel"><span class="eyebrow">DEEPSEEK · LEARNING FEEDBACK</span><h1>${title}</h1><p id="analysis-record" class="muted"></p><p class="muted">${description}</p><p id="analysis-note" class="muted"></p><div id="analysis-status" role="status" aria-live="polite"><p id="analysis-message">正在读取分析状态…</p><progress id="analysis-progress" max="100" aria-label="AI 分析进度"></progress><small id="analysis-detail"></small></div><div class="actions"><button class="primary" id="analysis-start">生成 AI 分析</button><a class="secondary" id="analysis-view" href="#" target="_blank" rel="noopener" hidden>查看分析报告 ↗</a><a class="secondary" id="analysis-download" href="#" download hidden>下载 HTML ↓</a></div><button type="button" class="key-settings" id="analysis-key">设置 / 更换 DeepSeek 密钥</button><div class="actions">${backLink}</div></div>`, title);
  root.querySelector("#analysis-note").textContent = options.note || "";
  await initializeApiKey(config, user, {prompt:false});
  const $ = id => root.querySelector(`#${id}`);
  const endpoint = options.endpoint || `/api/learning-records/${encodeURIComponent(recordId)}/analysis`;
  const fileUrl = action => `${endpoint}/${action}?account=${encodeURIComponent(user.id)}`;
  let timer, stopped = false, submitting = false, running = false, openOnComplete = false, lastAlert = "";
  window.addEventListener("pagehide", () => { stopped = true; clearTimeout(timer); }, {once:true});
  async function request(method="GET") {
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 15000);
    try { return await api(endpoint, {method, signal:controller.signal}); }
    catch(error) {
      if (error.name === "AbortError") throw new Error("本地服务响应超时，请检查启动窗口后重试。");
      throw error;
    } finally { clearTimeout(timeout); }
  }
  function showError(message, key=message) {
    $("analysis-message").textContent = message;
    $("analysis-progress").value = 0;
    $("analysis-detail").textContent = "原始作答记录已保留，解决问题后可重试。";
    $("analysis-start").disabled = false;
    $("analysis-start").textContent = "重试分析";
    if (lastAlert !== key) { lastAlert = key; window.alert(message); }
  }
  function update(state) {
    running = active.has(state.status);
    $("analysis-start").disabled = running || submitting;
    $("analysis-start").hidden = state.status === "completed" && state.available;
    $("analysis-start").textContent = running ? "正在分析…" : state.status === "idle" ? (options.title ? `生成${title}` : "生成 AI 分析") : "重试分析";
    $("analysis-message").textContent = state.message || "点击生成，向 DeepSeek 发送本次题目与作答信息。";
    if (state.progress === null) $("analysis-progress").removeAttribute("value"); else $("analysis-progress").value = state.progress || 0;
    const seconds = Math.max(0, Math.floor((Date.now()-Date.parse(state.started_at))/1000));
    $("analysis-detail").textContent = running ? `已等待 ${seconds || 0} 秒 · 请保持本地服务运行，刷新后可恢复进度。` : "分析以本次练习为依据，请结合原课核对。";
    for (const action of ["view", "download"]) {
      $(`analysis-${action}`).hidden = !state.available;
      $(`analysis-${action}`).href = fileUrl(action);
    }
    if (["failed", "interrupted"].includes(state.status)) showError(state.message, `${state.id}:${state.started_at}:${state.status}`);
    if (running) timer = setTimeout(poll, 1000);
    else if (state.status === "completed" && state.available && openOnComplete) location.replace(fileUrl("view"));
  }
  async function poll() {
    clearTimeout(timer);
    try { const state = await request(); if (!stopped) update(state); }
    catch(error) {
      if (stopped) return;
      running = false;
      showError(`无法读取分析进度：${error.message}`);
      if (![401,403,404].includes(error.status)) timer = setTimeout(poll, 3000);
    }
  }
  async function start() {
    if (submitting || running) return;
    submitting = true; $("analysis-start").disabled = true;
    clearTimeout(timer);
    $("analysis-message").textContent = "正在检查密钥并整理提交记录…";
    $("analysis-progress").removeAttribute("value");
    try {
      if (!await ensureApiKey()) {
        $("analysis-message").textContent = "设置 DeepSeek 密钥后可生成分析。";
        $("analysis-progress").value = 0;
        return;
      }
      const state = await request("POST");
      openOnComplete = true;
      submitting = false; update(state);
    } catch(error) {
      showError(`无法生成 AI 分析：${error.message}`, `request:${Date.now()}`);
      if (!error.status) timer = setTimeout(poll, 3000);
    } finally { submitting = false; if (!stopped) $("analysis-start").disabled = running; }
  }
  $("analysis-start").onclick = start;
  $("analysis-key").onclick = () => openApiKeyDialog();
  const state = await request();
  $("analysis-record").textContent = options.metadata ? options.metadata(state.record) : `${state.record.title} · ${state.record.student} · ${state.record.score} 分 · ${state.record.total} 题`;
  if (autoStart && state.available && state.status === "completed") { location.replace(fileUrl("view")); return; }
  if (autoStart && state.status === "idle") await start();
  else { openOnComplete = active.has(state.status); update(state); }
}
