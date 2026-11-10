import {esc} from "/static/ui.js";
import {analysisLink} from "/static/analysis.js";

export const quizActive = new Set(["preparing", "generating", "validating", "saving"]);
const modeNames = {combined:"PPT + 演讲文字", slides:"仅 PPT", transcript:"仅演讲文字"};
const exerciseUrl = (user, jobId, extra={}) => `/?${new URLSearchParams({page:"ai-quiz", account:user.id, job:jobId, ...extra})}`;

export function mountQuizControls({api, user, config, ensureApiKey, onJob}) {
  const $ = id => document.getElementById(id);
  let job, timer, submitting = false, lastAlert = "";
  function failure(message, key=message) {
    $("quiz-status").hidden = false;
    $("quiz-message").textContent = message;
    $("quiz-progress").value = 0;
    $("quiz-detail").textContent = "课程资料已保留，可在解决问题后重试。";
    if (lastAlert !== key) { lastAlert = key; window.alert(message); }
  }
  function update(value) {
    clearTimeout(timer);
    job = value;
    const state = job?.quiz || {}, active = quizActive.has(state.status);
    const option = job?.quiz_options || {ready:false, reason:config.features?.ai_quiz ? "请先成功生成课程总结。" : "请重新启动本地服务并刷新页面，以加载 AI 出题功能。"};
    $("quiz-generate").disabled = submitting || active || !option.ready;
    $("quiz-generate").textContent = active ? "正在出题…" : ["failed", "interrupted"].includes(state.status) ? "重试出题" : "一键出题";
    $("quiz-generate").title = option.reason || "使用已保存总结的同类材料生成 10 道单选题";
    $("quiz-guide").textContent = option.ready ? `出题依据：${modeNames[option.mode]}，与当前保存的总结一致。首次出题会调用 DeepSeek API。` : option.reason;
    $("quiz-view").hidden = !state.available;
    $("quiz-view").href = job ? exerciseUrl(user, job.id) : "#";
    $("quiz-view").title = state.available_mode ? `已有练习依据：${modeNames[state.available_mode]}` : "打开已保存的练习";
    $("quiz-status").hidden = !state.status;
    $("quiz-message").textContent = state.message || "";
    if (state.progress === null) $("quiz-progress").removeAttribute("value");
    else $("quiz-progress").value = state.progress || 0;
    const elapsed = Math.max(0, Math.floor((Date.now() - Date.parse(state.started_at)) / 1000));
    $("quiz-detail").textContent = active ? `已等待 ${elapsed || 0} 秒 · 生成期间请保持本地服务运行；刷新后会恢复进度。` : state.status === "completed" ? "10 道单选题已保存，打开练习即可作答。" : "课程资料已保留，可重试出题。";
    if (!submitting && ["failed", "interrupted"].includes(state.status)) failure(state.message, `${job.id}:${state.generation_id}:${state.status}`);
    if (submitting && !active) {
      $("quiz-status").hidden = false;
      $("quiz-message").textContent = "正在检查密钥并准备出题任务…";
      $("quiz-progress").removeAttribute("value");
      $("quiz-detail").textContent = "请稍候";
    }
    if (active) timer = setTimeout(() => poll(job.id), 1000);
  }
  async function poll(jobId) {
    clearTimeout(timer);
    try {
      const value = await api(`/api/jobs/${jobId}`);
      if (job?.id !== jobId) return;
      onJob(value);
    } catch (error) {
      if (job?.id !== jobId) return;
      failure(`无法读取出题进度：${error.message}`, `network:${jobId}`);
      if (![401,403,404].includes(error.status)) timer = setTimeout(() => poll(jobId), 3000);
    }
  }
  $("quiz-generate").onclick = async () => {
    if (!job?.quiz_options?.ready || submitting || quizActive.has(job.quiz?.status)) return;
    const jobId = job.id;
    // Reserve the tab during the click so a completed async request isn't blocked as a popup.
    const practice = window.open(exerciseUrl(user, jobId, {wait:"start"}), "_blank");
    if (practice) practice.opener = null;
    submitting = true; update(job);
    try {
      if (!(await ensureApiKey())) {
        if (practice && !practice.closed) practice.close();
        submitting = false; update(job); return;
      }
      const state = await api(`/api/jobs/${jobId}/quiz`, {method:"POST"});
      if (practice && !practice.closed) practice.location.replace(exerciseUrl(user, jobId, {run:state.generation_id}));
      if (job?.id === jobId) { submitting = false; onJob({...job, quiz:state}); }
    } catch (error) {
      if (practice && !practice.closed) {
        if (!error.status) practice.location.replace(exerciseUrl(user, jobId, {wait:"recover"}));
        else practice.close();
      }
      if (job?.id === jobId) {
        failure(`无法出题：${error.message}`, `request:${jobId}:${Date.now()}`);
        if (!error.status) timer = setTimeout(() => poll(jobId), 3000);
      }
    } finally {
      submitting = false;
      if (job?.id === jobId) $("quiz-generate").disabled = quizActive.has(job.quiz?.status) || !job.quiz_options?.ready;
      else update(job);
    }
  };
  return {update, isActive:() => submitting || quizActive.has(job?.quiz?.status)};
}

export async function quizPage(ctx, jobId, runId, waiting) {
  const {api, layout, root, user, link} = ctx;
  if (!jobId) throw new Error("缺少视频任务，请从视频学习页面打开练习。");
  const back = link("video", "返回视频学习", "secondary", {job:jobId});
  layout(`<div class="learning-panel"><span class="eyebrow">AI COURSE PRACTICE</span><h1>课程自测</h1><div id="ai-quiz-content"><p id="ai-quiz-message" role="status">正在读取练习…</p><progress id="ai-quiz-progress" max="100" aria-label="AI 出题进度"></progress><p id="ai-quiz-detail" class="muted"></p></div><div class="actions">${back}</div></div>`, "AI 课程练习");
  const content = root.querySelector("#ai-quiz-content");
  if (waiting === "start") {
    root.querySelector("#ai-quiz-message").textContent = "正在准备出题；如需设置 API 密钥，请先在原页面填写。";
    return;
  }
  let timer, stopped = false;
  window.addEventListener("pagehide", () => { stopped = true; clearTimeout(timer); }, {once:true});
  async function read(url, options={}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try { return await api(url, {...options, signal:controller.signal}); }
    finally { clearTimeout(timeout); }
  }
  function problem(message, retry) {
    content.innerHTML = `<div class="error-box" role="alert">${esc(message)}</div><p class="muted">返回视频学习页面可查看详情或重试出题。</p>${retry ? '<button class="secondary" id="ai-quiz-retry">重新读取</button>' : ""}`;
    if (retry) root.querySelector("#ai-quiz-retry").onclick = () => location.reload();
  }
  async function poll() {
    try {
      const job = await read(`/api/jobs/${encodeURIComponent(jobId)}`);
      if (stopped) return;
      const state = job.quiz || {};
      if (runId && state.generation_id !== runId) { problem("这次练习生成状态已经变化，请从视频页面重新打开练习。", false); return; }
      if (quizActive.has(state.status) && (runId || waiting)) {
        root.querySelector("#ai-quiz-message").textContent = state.message;
        const progress = root.querySelector("#ai-quiz-progress");
        if (state.progress === null) progress.removeAttribute("value"); else progress.value = state.progress || 0;
        const seconds = Math.max(0, Math.floor((Date.now()-Date.parse(state.started_at))/1000));
        root.querySelector("#ai-quiz-detail").textContent = `已等待 ${seconds} 秒 · 校验通过后将在此显示 10 道单选题。`;
        timer = setTimeout(poll, 1000); return;
      }
      if ((runId || waiting) && ["failed", "interrupted"].includes(state.status)) { problem(state.message, false); return; }
      if (!state.available) { problem(state.message || "练习尚未生成，请返回视频页面点击一键出题。", true); return; }
      const quiz = await read(`/api/jobs/${encodeURIComponent(jobId)}/quiz`);
      if (stopped) return;
      if (runId && quiz.id !== runId) { problem("本次出题尚未成功保存，请返回视频页面查看生成状态。", true); return; }
      renderQuiz(quiz, job);
    } catch (error) {
      if (!stopped) problem(`无法读取练习：${error.name === "AbortError" ? "本地服务响应超时，请稍后重试。" : error.message}`, true);
    }
  }
  function renderQuiz(quiz, job) {
    const questions = quiz.questions;
    root.querySelector("h1").textContent = quiz.title;
    content.innerHTML = `<p class="muted">${esc(job.filename)} · ${esc(modeNames[quiz.source_mode])} · 10 道单选题，每题 10 分</p><p class="muted">题目由 DeepSeek 根据课程材料生成，请结合原课核对题意与解析。</p><form id="ai-quiz-form" class="quiz"><div class="questions">${questions.map((q,i) => `<fieldset class="question"><legend>${i+1}. ${esc(q.text)}</legend>${q.options.map((option,oi) => `<label class="option"><input type="radio" name="${esc(q.id)}" value="${oi}" required> ${String.fromCharCode(65+oi)}. ${esc(option)}</label>`).join("")}</fieldset>`).join("")}</div><div class="actions"><button class="primary" type="submit">提交并查看得分</button><button class="secondary" type="button" id="ai-quiz-reset">重新作答</button></div><div id="ai-quiz-result" class="quiz-result" role="status"></div></form>`;
    const form = root.querySelector("#ai-quiz-form"), result = root.querySelector("#ai-quiz-result");
    const draftKey = `ai-quiz:${user.id}:${jobId}:${quiz.id}`;
    try {
      const saved = JSON.parse(sessionStorage.getItem(draftKey) || "{}");
      for (const input of form.querySelectorAll('input[type="radio"]')) input.checked = saved[input.name] === input.value;
    } catch {}
    form.onchange = () => { try { sessionStorage.setItem(draftKey, JSON.stringify(Object.fromEntries(new FormData(form)))); } catch {} };
    form.onsubmit = async event => {
      event.preventDefault();
      const answers = Object.fromEntries(new FormData(form));
      if (questions.some(q => !Object.hasOwn(answers,q.id))) { form.reportValidity(); return; }
      const correct = questions.filter(q => Number(answers[q.id]) === q.answer).length;
      result.innerHTML = `<div class="result"><h2>本次得分 ${correct*10} 分</h2><p>答对 ${correct}/10 题，每题 10 分，已在本机完成判分。</p>${questions.map((q,i) => `<div class="ai-answer"><p>${Number(answers[q.id]) === q.answer ? "✓" : "✗"} ${i+1}. ${esc(q.text)}</p><p>你的选择：${String.fromCharCode(65+Number(answers[q.id]))} · 正确答案：${String.fromCharCode(65+q.answer)}. ${esc(q.options[q.answer])}</p><small class="answer-explanation">${esc(q.explanation)}</small></div>`).join("")}</div>`;
      result.scrollIntoView({behavior:"smooth",block:"start"});
      if (user.role === "student") {
        const actions = document.createElement("div"); actions.className = "actions"; result.append(actions);
        const buttons = [form.querySelector('[type="submit"]'), root.querySelector("#ai-quiz-reset")];
        async function saveAttempt() {
          buttons.forEach(button => { button.disabled = true; });
          actions.textContent = "正在保存本次作答，以便生成 AI 分析…";
          try {
            const saved = await read(`/api/jobs/${encodeURIComponent(jobId)}/quiz/submissions`, {
              method:"POST", headers:{"Content-Type":"application/json"},
              body:JSON.stringify({quiz_id:quiz.id, answers:Object.fromEntries(Object.entries(answers).map(([id,value]) => [id,Number(value)]))})
            });
            actions.innerHTML = `<span class="muted">本次自测已保存到学习记录。</span>${analysisLink(link, saved.id)}`;
          } catch(error) {
            const message = error.name === "AbortError" ? "本地服务响应超时，请检查启动窗口后重试。" : error.message;
            actions.innerHTML = `<span class="error-box">保存作答失败：${esc(message)}</span><button type="button" class="secondary" id="ai-attempt-retry">重试保存</button>`;
            actions.querySelector("#ai-attempt-retry").onclick = saveAttempt;
          } finally { buttons.forEach(button => { button.disabled = false; }); }
        }
        await saveAttempt();
      }
    };
    root.querySelector("#ai-quiz-reset").onclick = () => {
      form.reset(); result.replaceChildren();
      try { sessionStorage.removeItem(draftKey); } catch {}
      form.querySelector("input").focus();
    };
  }
  await poll();
}
