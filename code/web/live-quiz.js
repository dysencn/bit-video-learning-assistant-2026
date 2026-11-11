import {esc, icon} from "/static/ui.js";

const CELLS = 12;
const letter = index => String.fromCharCode(65 + index);

let liveStream = null;
let liveTimer = null;
let livePoll = null;
let liveOffset = 0;
let subscribedId = null;
let finishedId = null;

function closeStream() {
  if (liveStream) { try { liveStream.abort(); } catch (error) { /* 已断开 */ } liveStream = null; }
}

function stopLive() {
  closeStream();
  if (liveTimer) { clearInterval(liveTimer); liveTimer = null; }
  if (livePoll) { clearTimeout(livePoll); livePoll = null; }
  subscribedId = null;
  finishedId = null;
}

function statusTag(quiz) {
  if (quiz.status === "waiting") return '<span class="tag neutral">等待开始</span>';
  if (quiz.status === "active") return '<span class="tag published">抢答进行中</span>';
  return '<span class="tag attention">本场已结束</span>';
}

function remaining(quiz) {
  if (quiz.status === "waiting") return "等待开始";
  if (quiz.status === "closed") return "已结束";
  if (!quiz.closes_at) return "";
  const left = Math.max(0, Math.round((new Date(quiz.closes_at).getTime() - (Date.now() - liveOffset)) / 1000));
  return left > 0 ? `${left} 秒` : "已到时";
}

function cells(votes, total) {
  const filled = total ? Math.round(votes / total * CELLS) : 0;
  return Array.from({length: CELLS}, (unused, index) => `<i class="${index < filled ? "on" : ""}"></i>`).join("");
}

function ticket(quiz) {
  const rate = quiz.correct_rate === null || quiz.correct_rate === undefined ? "—" : `${quiz.correct_rate}%`;
  return `<div class="live-ticket"><div><b>${quiz.participant_count}</b><span>已参与</span></div>`
    + `<div><b>${rate}</b><span>正确率</span></div>`
    + `<div><b>${quiz.first_correct ? esc(quiz.first_correct.student) : "—"}</b><span>首个答对</span></div></div>`;
}

function answerRows(quiz) {
  if (!quiz.answers || !quiz.answers.length) return '<p class="muted">还没有人作答。</p>';
  return `<div class="live-answers">${quiz.answers.map(item => `<div class="record-row"><div><b>${esc(item.student)}</b><small>选 ${letter(item.selected)} · ${(item.latency_ms / 1000).toFixed(1)} 秒</small></div><span class="tag ${item.correct ? "published" : "attention"}">${item.correct ? "答对" : "答错"}</span></div>`).join("")}</div>`;
}

async function openStream(ctx, quizId, apply) {
  closeStream();
  const controller = new AbortController();
  liveStream = controller;
  try {
    const response = await fetch(`/api/live-quizzes/${encodeURIComponent(quizId)}/events`, {
      headers: {"X-Local-Token": ctx.config?.token || "", "X-Account-ID": ctx.params.get("account") || ""},
      signal: controller.signal
    });
    if (!response.ok || !response.body) throw new Error("实时通道连接失败");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const data = part.split("\n").filter(line => line.startsWith("data:")).map(line => line.slice(5).trim()).join("");
        if (!data) continue;
        try { apply(JSON.parse(data)); } catch (error) { /* 忽略无法解析的片段 */ }
      }
    }
  } catch (error) {
    if (error.name !== "AbortError") ctx.tell("实时连接中断，可刷新页面重连");
  }
}

function startTicker(quiz) {
  if (liveTimer) { clearInterval(liveTimer); liveTimer = null; }
  if (quiz.status !== "active") return;
  liveTimer = setInterval(() => {
    const node = document.querySelector("#live-timer");
    if (!node) { clearInterval(liveTimer); liveTimer = null; return; }
    node.textContent = remaining(quiz);
  }, 250);
}

/* ---------------- 教师端 ---------------- */

async function teacherPage(ctx) {
  const {api, post, layout, root, tell} = ctx;
  const courses = await api("/api/courses");
  if (!courses.length) {
    layout('<div class="form-card"><h2>还没有课程</h2><p class="muted">请先创建并发布一门课程，再来发起课堂抢答。</p></div>', "课堂抢答");
    return;
  }
  layout(`<div class="page-heading"><div><span class="eyebrow">LIVE CLASSROOM</span><h1>课堂抢答</h1><p class="muted">学生端通过 SSE 实时收到题目，作答后看板即时更新。抢答只做课堂互动，不计入课程成绩。</p></div></div>
    <section class="form-card live-setup" id="live-setup">
      <h2>发起一场抢答</h2>
      <div class="live-fields">
        <label>课程<select id="live-course">${courses.map(course => `<option value="${esc(course.id)}">${esc(course.title)}</option>`).join("")}</select></label>
        <label>题目<select id="live-question"><option value="">手填题目</option></select></label>
        <label>时长（秒）<input id="live-duration" type="number" min="10" max="300" value="30"></label>
      </div>
      <div id="live-manual" class="live-fields">
        <label class="span-2">题干<input id="live-text" maxlength="200" placeholder="例如：Python 中用于输出内容的是哪个函数？"></label>
        <label>选项 A<input id="live-option-0" maxlength="120"></label>
        <label>选项 B<input id="live-option-1" maxlength="120"></label>
        <label>选项 C<input id="live-option-2" maxlength="120"></label>
        <label>选项 D<input id="live-option-3" maxlength="120"></label>
        <label>正确答案<select id="live-answer"><option value="0">A</option><option value="1">B</option><option value="2">C</option><option value="3">D</option></select></label>
        <label class="span-2">解析（可选）<input id="live-explanation" maxlength="400" placeholder="揭晓时展示给学生的讲解"></label>
      </div>
      <div class="actions"><button class="primary" id="live-create">${icon("target")}发起抢答</button></div>
    </section>
    <section class="live-panel" id="live-board" hidden></section>`, "课堂抢答");

  const courseSelect = root.querySelector("#live-course");
  const questionSelect = root.querySelector("#live-question");
  const manual = root.querySelector("#live-manual");

  function syncManual() {
    manual.hidden = Boolean(questionSelect.value);
  }

  async function loadQuestions() {
    questionSelect.innerHTML = '<option value="">手填题目</option>';
    try {
      const course = await api(`/api/courses/${encodeURIComponent(courseSelect.value)}`);
      const questions = course.questions || [];
      questionSelect.innerHTML = '<option value="">手填题目</option>' + questions.map(question =>
        `<option value="${esc(question.id)}">${esc(question.text.slice(0, 42))}</option>`).join("");
    } catch (error) { tell(error.message); }
    syncManual();
  }

  courseSelect.onchange = () => loadQuestions().catch(error => tell(error.message));
  questionSelect.onchange = syncManual;
  await loadQuestions();

  function bindBoard(quiz) {
    const board = root.querySelector("#live-board");
    const startButton = board.querySelector("#live-start");
    const closeButton = board.querySelector("#live-close");
    const newButton = board.querySelector("#live-new");
    if (startButton) startButton.onclick = async () => {
      startButton.disabled = true;
      try { applyTeacher(await post(`/api/live-quizzes/${encodeURIComponent(quiz.id)}/start`)); }
      catch (error) { tell(error.message); startButton.disabled = false; }
    };
    if (closeButton) closeButton.onclick = async () => {
      closeButton.disabled = true;
      try { applyTeacher(await post(`/api/live-quizzes/${encodeURIComponent(quiz.id)}/close`)); }
      catch (error) { tell(error.message); closeButton.disabled = false; }
    };
    if (newButton) newButton.onclick = () => {
      stopLive();
      board.hidden = true;
      root.querySelector("#live-setup").hidden = false;
    };
  }

  function applyTeacher(quiz) {
    liveOffset = Date.now() - new Date(quiz.server_now || Date.now()).getTime();
    root.querySelector("#live-setup").hidden = true;
    const board = root.querySelector("#live-board");
    board.hidden = false;
    board.innerHTML = `<div class="live-status">${statusTag(quiz)}<span class="live-timer" id="live-timer">${remaining(quiz)}</span></div>
      <h2 class="live-question">${esc(quiz.question.text)}</h2>
      <p class="muted">${esc(quiz.course_title)}${quiz.question.knowledge_point_name ? ` · ${esc(quiz.question.knowledge_point_name)}` : ""}</p>
      <div class="live-options">${quiz.question.options.map((option, index) => `<div class="live-option ${quiz.status === "closed" && index === quiz.question.answer ? "correct" : ""}"><span class="live-letter">${letter(index)}</span><span class="live-text">${esc(option)}</span><span class="live-count">${quiz.votes[index]} 人</span><span class="live-cells">${cells(quiz.votes[index], quiz.participant_count)}</span>${quiz.status === "closed" && index === quiz.question.answer ? `<span class="tag published">正确答案</span>` : ""}</div>`).join("")}</div>
      ${ticket(quiz)}
      <div class="live-answers-title"><b>作答明细</b><span class="muted">按提交时间排序</span></div>
      ${answerRows(quiz)}
      <div class="actions">${quiz.status === "waiting" ? '<button class="primary" id="live-start">开始抢答</button>' : ""}${quiz.status === "active" ? '<button class="secondary" id="live-close">结束本场</button>' : ""}${quiz.status === "closed" ? '<button class="secondary" id="live-new">再来一题</button>' : ""}</div>`;
    bindBoard(quiz);
    startTicker(quiz);
  }

  root.querySelector("#live-create").onclick = async () => {
    const button = root.querySelector("#live-create");
    button.disabled = true;
    try {
      const payload = {duration_seconds: Number(root.querySelector("#live-duration").value)};
      if (questionSelect.value) {
        payload.question_id = questionSelect.value;
      } else {
        const options = [0, 1, 2, 3].map(index => root.querySelector(`#live-option-${index}`).value.trim());
        payload.question = {text: root.querySelector("#live-text").value.trim(), options,
                           answer: Number(root.querySelector("#live-answer").value),
                           explanation: root.querySelector("#live-explanation").value.trim()};
      }
      const quiz = await post(`/api/courses/${encodeURIComponent(courseSelect.value)}/live-quizzes`, payload);
      applyTeacher(quiz);
      openStream(ctx, quiz.id, applyTeacher);
    } catch (error) { tell(error.message); button.disabled = false; }
  };
}

/* ---------------- 学生端 ---------------- */

async function studentPage(ctx) {
  const {api, post, layout, root, tell} = ctx;
  layout(`<div class="page-heading"><div><span class="eyebrow">LIVE CLASSROOM</span><h1>课堂抢答</h1><p class="muted">教师发起后自动出现在这里；作答后即时揭晓结果。抢答不计入课程成绩。</p></div></div>
    <div id="live-student"><div class="live-panel live-waiting"><span class="live-pulse">${icon("bell")}</span><h2>正在检查有没有进行中的抢答</h2><p class="muted">教师发起后这里会自动出现题目。</p></div></div>`, "课堂抢答");

  function schedule() {
    if (livePoll) { clearTimeout(livePoll); livePoll = null; }
    livePoll = setTimeout(() => { refresh().catch(() => schedule()); }, 3000);
  }

  function renderWaiting() {
    const container = root.querySelector("#live-student");
    if (!container) return;
    container.innerHTML = `<div class="live-panel live-waiting"><span class="live-pulse">${icon("bell")}</span><h2>暂时没有进行中的抢答</h2><p class="muted">等待教师发起，页面会自动检测（每 3 秒）。</p></div>`;
  }

  function renderStudent(quiz) {
    const container = root.querySelector("#live-student");
    if (!container) { stopLive(); return; }
    if (quiz.status === "closed") finishedId = quiz.id;
    else if (quiz.id !== finishedId) finishedId = null;
    liveOffset = Date.now() - new Date(quiz.server_now || Date.now()).getTime();
    if (quiz.status === "waiting") {
      container.innerHTML = `<div class="live-panel live-waiting"><span class="live-pulse">${icon("bell")}</span><h2>教师已发起抢答</h2><p class="muted">${esc(quiz.course_title)} · 等待教师开始…</p></div>`;
      return;
    }
    if (quiz.status === "active" && !quiz.my_answer) {
      container.innerHTML = `<div class="live-panel"><div class="live-status">${statusTag(quiz)}<span class="live-timer" id="live-timer">${remaining(quiz)}</span></div>
        <h2 class="live-question">${esc(quiz.question.text)}</h2>
        <p class="muted">${esc(quiz.course_title)} · 选择你认为正确的答案，每题只能提交一次。</p>
        <div class="live-choices">${quiz.question.options.map((option, index) => `<button class="live-choice" data-live-option="${index}"><span class="live-letter">${letter(index)}</span><span>${esc(option)}</span></button>`).join("")}</div>
        <p class="muted live-participants">已参与 ${quiz.participant_count} 人</p></div>`;
      container.querySelectorAll("[data-live-option]").forEach(button => {
        button.onclick = async () => {
          container.querySelectorAll("[data-live-option]").forEach(item => { item.disabled = true; });
          try { renderStudent(await post(`/api/live-quizzes/${encodeURIComponent(quiz.id)}/answers`, {selected: Number(button.dataset.liveOption)})); }
          catch (error) { tell(error.message); container.querySelectorAll("[data-live-option]").forEach(item => { item.disabled = false; }); }
        };
      });
      startTicker(quiz);
      return;
    }
    const mine = quiz.my_answer;
    const answer = quiz.question.answer;
    const correctText = answer === undefined ? "" : `<p><b>正确答案：</b>${letter(answer)}. ${esc(quiz.question.options[answer])}</p>${quiz.question.explanation ? `<p><b>解析：</b>${esc(quiz.question.explanation)}</p>` : ""}`;
    const own = mine ? `<div class="live-result ${mine.correct ? "correct" : "incorrect"}"><b>${mine.correct ? "答对了" : "答错了"}</b><p>你选择了 ${letter(mine.selected)} · 用时 ${(mine.latency_ms / 1000).toFixed(1)} 秒</p></div>` : "";
    container.innerHTML = `<div class="live-panel"><div class="live-status">${statusTag(quiz)}<span class="live-timer">${remaining(quiz)}</span></div>
      <h2 class="live-question">${esc(quiz.question.text)}</h2>
      <p class="muted">${esc(quiz.course_title)}${quiz.question.knowledge_point_name ? ` · ${esc(quiz.question.knowledge_point_name)}` : ""}</p>
      ${own}${correctText}
      <div class="live-options">${quiz.question.options.map((option, index) => `<div class="live-option ${index === answer ? "correct" : ""}"><span class="live-letter">${letter(index)}</span><span class="live-text">${esc(option)}</span><span class="live-count">${quiz.votes[index]} 人</span><span class="live-cells">${cells(quiz.votes[index], quiz.participant_count)}</span></div>`).join("")}</div>
      ${quiz.status === "closed" ? ticket(quiz) : `<p class="muted">已参与 ${quiz.participant_count} 人，等待其他同学作答…</p>`}</div>`;
    if (quiz.status === "closed") {
      // 本场结束后改回轮询，教师再发起一场时无需刷新页面。
      closeStream();
      schedule();
    }
  }

  async function refresh() {
    if (!root.querySelector("#live-student")) { stopLive(); return; }
    const data = await api("/api/live-quizzes/current");
    if (!root.querySelector("#live-student")) { stopLive(); return; }
    if (!data.quiz) {
      // 刚结束的那一场继续留在页面上，同时等待教师发起下一场。
      if (!finishedId) renderWaiting();
      schedule();
      return;
    }
    if (subscribedId !== data.quiz.id) {
      subscribedId = data.quiz.id;
      openStream(ctx, data.quiz.id, quiz => renderStudent(quiz));
    }
    renderStudent(data.quiz);
  }

  await refresh();
}

export async function liveQuizPage(ctx) {
  stopLive();
  if (ctx.user.role === "teacher") return teacherPage(ctx);
  return studentPage(ctx);
}
