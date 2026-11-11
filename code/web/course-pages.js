import {esc, icon, radarCard, assignmentSubmissionText} from "/static/ui.js";
import {teachingCoursesPage, courseLabel} from "/static/teaching-courses.js";
import {workspaceNav} from "/static/teacher-workspace.js";
import {analysisLink} from "/static/analysis.js";

const dateTime = value => value ? new Date(value).toLocaleString("zh-CN", {month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit"}) : "未设置";
const dateLocal = value => {
  if (!value) return "";
  const date = new Date(value);
  const offset = date.getTimezoneOffset();
  return new Date(date.getTime() - offset * 60000).toISOString().slice(0, 16);
};
const stateLabel = state => ({open:"进行中", late:"迟交中", upcoming:"未开始", closed:"已截止",
  completed:"已完成", draft:"草稿", archived:"已归档"}[state] || state);
const stateClass = state => ["open", "completed"].includes(state) ? "published"
  : ["late", "upcoming"].includes(state) ? "attention" : "neutral";
const displayState = item => item.completed ? "completed" : item.state;
const score = value => value === null || value === undefined ? "—" : String(value);
const courseName = (courses, id) => courses.find(course => course.id === id)?.title || "已删除课程";

function courseSelector(courses, selected, id) {
  return `<label class="inline-select">课程<select id="${id}">${courses.map(course =>
    `<option value="${esc(course.id)}" ${course.id === selected ? "selected" : ""}>${esc(courseLabel(course))}</option>`).join("")}</select></label>`;
}

function emptyPage(ctx, title, message, linkLabel = "返回工作台", view = "dashboard") {
  const {layout, link} = ctx;
  layout(`<div class="empty-state">${icon("book")}<h2>${esc(title)}</h2><p class="muted">${esc(message)}</p>${link(view, linkLabel, "secondary")}</div>`, title);
}

// ------------------------------------------------------------------ assignments
export async function assignmentsPage(ctx) {
  if (ctx.user.role === "teacher" && !ctx.params.get("course")) return teachingCoursesPage(ctx);
  if (ctx.user.role === "teacher") return teacherAssignmentsPage(ctx);
  return studentAssignmentsPage(ctx);
}

async function teacherAssignmentsPage(ctx) {
  const {api, post, del, layout, root, tell, link, params, href, majorName} = ctx;
  const courses = (await api("/api/courses")).filter(course => course.status !== "archived");
  if (!courses.length) return emptyPage(ctx, "还没有课程", "创建课程后即可准备题目和布置作业。", "创建课程", "new-course");
  const courseId = params.get("course") || courses[0].id;
  if (!courses.some(course => course.id === courseId)) throw new Error("课程不存在或已归档");
  const [course, assignments] = await Promise.all([api(`/api/courses/${courseId}`), api(`/api/courses/${courseId}/assignments`)]);
  const questionCount = item => item.questions?.length || 0;
  const counts = [[assignments.filter(a => a.status === "draft").length, "草稿"], [assignments.filter(a => ["open", "late"].includes(a.state)).length, "进行中"], [assignments.filter(a => a.state === "closed").length, "已截止"]];
  const rows = assignments.map(item => `<article class="assignment-manage-card" data-assignment-state="${esc(item.status === "draft" ? "draft" : ["open", "late"].includes(item.state) ? "open" : "other")}">
    <div class="assignment-card-head"><span class="tag ${stateClass(item.state)}">${stateLabel(item.state)}</span><small>${esc(item.chapter_title)} · ${questionCount(item)} 道单选题</small></div>
    <h3>${esc(item.title)}</h3><p class="muted">截止 ${dateTime(item.due_at)} · ${item.grade_release === "manual" ? (item.grades_released ? "成绩已发布" : "成绩待发布") : "提交后显示成绩"}</p>
    <div class="assignment-stats"><span><b>${item.submitted_count}</b>已交</span><span><b>${item.missing_count}</b>未交</span><span><b>${score(item.average_score)}</b>平均分</span><span><b>${item.late_count}</b>迟交</span></div>
    <div class="actions"><a class="secondary" href="${esc(href("assignment-builder", {course:courseId, assignment:item.id}))}">编辑题目与规则</a><button class="ghost" data-view-assignment="${esc(item.id)}">提交情况 / AI 分析</button></div>
    <details class="assignment-more"><summary>发布、提醒与其他操作</summary><div class="actions">${item.status === "draft" ? `<button class="secondary small" data-publish-assignment="${esc(item.id)}">发布作业</button>` : ""}${!item.grades_released ? `<button class="secondary small" data-release-assignment="${esc(item.id)}">发布成绩</button>` : ""}<button class="ghost small" data-remind-assignment="${esc(item.id)}">提醒未交</button><button class="ghost small danger" data-delete-assignment="${esc(item.id)}">归档作业</button></div></details>
  </article>`).join("");
  layout(`${workspaceNav(ctx, course, "assignments")}<div class="page-heading"><div><span class="eyebrow">ASSIGNMENTS</span><h1>作业管理</h1><p class="muted">一份作业，包含具体题目、提交规则和学生作答结果。</p></div><div class="actions">${courseSelector(courses, courseId, "assignment-course")}<a class="primary" href="${esc(href("assignment-builder", {course:courseId}))}">${icon("plus")}创建作业</a></div></div>
    <div class="workflow-note"><div><b>创建作业：独立出题 → 设置提交次数 → 预览与发布</b><p>作业题目单独保存。学生提交后先看得分，用完全部提交次数后开放答案与解析。</p></div>${link("teaching-ai", "AI 出题与分析", "secondary", {course:courseId})}</div>
    <div class="assignment-list-toolbar"><div class="assignment-counts">${counts.map(([count,label]) => `<span><b>${count}</b>${label}</span>`).join("")}</div><label>显示<select id="assignment-status-filter"><option value="all">全部作业</option><option value="draft">草稿</option><option value="open">进行中</option><option value="other">其他状态</option></select></label></div>
    <div class="assignment-manage-grid">${rows || `<div class="empty-state"><h2>创建第一份作业</h2><p class="muted">进入编辑器后，先为本次作业创建独立题目。</p><a class="primary" href="${esc(href("assignment-builder", {course:courseId}))}">创建作业</a></div>`}</div><section id="assignment-submissions"></section>`, "作业管理");
  root.querySelector("#assignment-course").onchange = event => { location.href = href("assignments", {course:event.target.value}); };
  root.querySelector("#assignment-status-filter").onchange = event => root.querySelectorAll("[data-assignment-state]").forEach(card => { card.hidden = event.target.value !== "all" && card.dataset.assignmentState !== event.target.value; });
  const reload = () => teacherAssignmentsPage(ctx);
  for (const [attribute, action, message] of [["publish", "publish", "作业已发布"], ["release", "release", "成绩已发布"], ["remind", "remind", "提醒已发送"]]) {
    root.querySelectorAll(`[data-${attribute}-assignment]`).forEach(button => { button.onclick = async () => {
      button.disabled = true;
      try {
        const id = button.getAttribute(`data-${attribute}-assignment`);
        const result = await post(`/api/courses/${courseId}/assignments/${encodeURIComponent(id)}/${action}`);
        await reload(); tell(action === "remind" ? `已向 ${result.recipient_count} 名未交学生发送公告提醒` : message);
      } catch (error) { tell(error.message); button.disabled = false; }
    }; });
  }
  root.querySelectorAll("[data-delete-assignment]").forEach(button => { button.onclick = async () => {
    if (!confirm("归档后学生端不再显示该作业，已有提交记录仍保留。继续？")) return;
    try { await del(`/api/courses/${courseId}/assignments/${encodeURIComponent(button.dataset.deleteAssignment)}`); await reload(); tell("作业已归档"); }
    catch (error) { tell(error.message); }
  }; });
  root.querySelectorAll("[data-view-assignment]").forEach(button => { button.onclick = async () => {
    button.disabled = true;
    try {
      const data = await api(`/api/courses/${courseId}/assignments/${encodeURIComponent(button.dataset.viewAssignment)}/submissions`);
      const target = root.querySelector("#assignment-submissions");
      target.innerHTML = `<div class="section-title"><div><h2>${esc(data.assignment.title)} · 提交与分析</h2><p class="muted">${data.assignment.submitted_count} / ${data.assignment.student_count} 人已交。点击 AI 分析，查看该次作答的错因与建议。</p></div>${link("teaching-ai", "全部 AI 分析", "secondary", {course:courseId})}</div><div class="table-scroll"><table class="gradebook"><thead><tr><th>学生</th><th>专业</th><th>状态</th><th>次数</th><th>最近得分</th><th>最高分</th><th>最近提交</th><th>作答分析</th></tr></thead><tbody>${data.students.map(student => `<tr><th>${link("student-detail", esc(student.student), "text-link", {student:student.student_id, course:courseId})}</th><td>${esc(majorName(student.major_id))}</td><td>${student.status === "submitted" ? "已交" : "未交"}${student.late ? " · 迟交" : ""}</td><td>${student.attempts}</td><td>${score(student.latest_score)}</td><td>${score(student.best_score)}</td><td>${dateTime(student.last_submitted_at)}</td><td>${student.submission_id ? analysisLink(link, student.submission_id, courseId) : '<span class="muted">等待提交</span>'}</td></tr>`).join("")}</tbody></table></div>`;
      target.scrollIntoView({behavior:"smooth", block:"start"});
    } catch (error) { tell(error.message); }
    finally { button.disabled = false; }
  }; });
}

async function studentAssignmentsPage(ctx) {
  const {api, layout, root, link, tell} = ctx;
  const items = await api("/api/assignments");
  const pending = items.filter(item => item.can_start);
  const finished = items.filter(item => !item.can_start);
  const card = item => `<article class="assignment-card"><div class="assignment-card-head"><span class="tag ${stateClass(displayState(item))}">${stateLabel(displayState(item))}</span><small>${esc(item.course_name)}</small></div><h3>${esc(item.title)}</h3><p class="muted">${esc(item.chapter_title)} · ${item.due_at ? `截止 ${dateTime(item.due_at)}` : "未设置截止时间"} · ${assignmentSubmissionText(item)}</p><div class="assignment-stats"><span><b>${item.question_count}</b>道题</span><span><b>${item.time_limit_minutes || "不限"}</b>${item.time_limit_minutes ? "分钟" : "时限"}</span><span><b>${item.best_score !== null ? item.best_score : item.pending_release ? "待发布" : "—"}</b>最高分</span></div><div class="actions">${item.can_start ? link("assignment", item.attempts ? "继续作业" : "开始作业", "primary", {assignment:item.id}) : link("assignment", item.answers_locked ? "查看详情" : "查看答案", "secondary", {assignment:item.id})}${item.pending_release ? '<span class="tag attention">成绩待教师发布</span>' : ""}</div></article>`;
  layout(`<div class="page-heading"><div><span class="eyebrow">MY ASSIGNMENTS</span><h1>我的作业</h1><p class="muted">按截止时间完成作业；迟交会按教师设置扣分，成绩是否立即显示由教师决定。</p></div><button class="ghost" id="refresh">${icon("refresh")}刷新作业</button></div>
    <section><div class="section-title"><div><h2>待完成</h2><span class="muted">${pending.length} 个作业</span></div></div><div class="assignment-grid">${pending.map(card).join("") || '<div class="empty">暂无待完成作业。</div>'}</div></section>
    <section><div class="section-title"><div><h2>其他作业</h2><span class="muted">已完成、已截止或尚未开放 · ${finished.length} 个</span></div></div><div class="assignment-grid">${finished.map(card).join("") || '<div class="empty">还没有其他作业。</div>'}</div></section>`, "我的作业");
  root.querySelector("#refresh").onclick = () => studentAssignmentsPage(ctx).catch(error => tell(error.message));
}

export async function assignmentDetailPage(ctx) {
  const {api, post, layout, root, tell, link, params, user} = ctx;
  if (user.role !== "student") throw new Error("请使用学生账号打开作业");
  const id = params.get("assignment");
  if (!id) return emptyPage(ctx, "没有选择作业", "请从作业列表打开一份作业。", "返回作业列表", "assignments");
  const data = await api(`/api/assignments/${encodeURIComponent(id)}`);
  const {assignment, course} = data;
  const questions = assignment.questions || [];
  let attempt = null;
  let startError = "";
  if (assignment.can_start) {
    try { attempt = await post(`/api/assignments/${encodeURIComponent(id)}/start`, {}); }
    catch (error) { startError = error.message; }
  }
  const resultText = assignment.pending_release
    ? '<div class="result"><h2>已提交，等待教师发布成绩</h2><p>本次作答已保存。教师发布成绩后，可在学习记录中查看得分与解析。</p></div>'
    : assignment.latest_submission_id
      ? `<div class="result"><h2>最近一次得分 ${score(assignment.latest_score)} 分</h2><p>${assignment.latest_score === null ? "成绩尚未发布。" : "可继续查看下方题目；如仍有次数，可以再次提交。"}</p>${link("learning-center", "查看作业记录", "secondary", {tab:"assignments"})}</div>`
      : "";
  const answerHtml = !assignment.answers_locked ? `<section class="study-card"><h2>标准答案与解析</h2>${questions.map((q,i)=>`<article class="teacher-question-review"><b>${i+1}. ${esc(q.text)}</b><p>标准答案：${String.fromCharCode(65+q.answer)}. ${esc(q.options[q.answer])}</p><p>${esc(q.explanation || "暂无解析")}</p></article>`).join("")}</section>` : `<p class="muted">剩余 ${assignment.remaining_attempts} 次提交机会，全部次数用完后可查看标准答案和解析。</p>`;
  const closed = ["closed", "upcoming"].includes(assignment.state) && !assignment.can_start;
  const questionHtml = questions.length ? questions.map((question, index) => `<fieldset class="question"><legend>${index + 1}. ${esc(question.text)}</legend>${question.options.map((option, optionIndex) => `<label class="option"><input type="radio" name="${esc(question.id)}" value="${optionIndex}" ${assignment.can_start ? "required" : "disabled"}> ${String.fromCharCode(65 + optionIndex)}. ${esc(option)}</label>`).join("")}</fieldset>`).join("") : '<p class="muted">本次作业暂时没有可用题目，请联系教师。</p>';
  layout(`<div class="learning-panel"><div class="page-heading"><div><span class="eyebrow">ASSIGNMENT</span><h1>${esc(assignment.title)}</h1><p class="muted">${esc(assignment.course_name)} · ${esc(assignment.chapter_title)} · ${assignment.due_at ? `截止 ${dateTime(assignment.due_at)}` : "未设置截止时间"}</p></div><div class="actions">${link("assignments", "返回作业列表", "secondary")}${link("course", "返回课程", "ghost", {course:course.id})}</div></div>
    <div class="assignment-detail-meta"><span class="tag ${stateClass(displayState(assignment))}">${stateLabel(displayState(assignment))}</span><span id="assignment-attempts">${assignmentSubmissionText(assignment)}</span><span>${assignment.time_limit_minutes ? `限时 ${assignment.time_limit_minutes} 分钟` : "不限时"}</span>${assignment.allow_late ? `<span>迟交每天扣 ${assignment.late_penalty_percent_per_day}%</span>` : ""}${attempt?.expires_at ? `<span id="assignment-timer" class="assignment-timer"></span>` : ""}</div>
    ${startError ? `<p class="warning">${esc(startError)}</p>` : ""}${resultText}${answerHtml}
    ${assignment.can_start && questions.length ? `<form id="assignment-submit" class="study-card"><div class="questions">${questionHtml}</div><div class="actions"><button class="primary" type="submit">提交作业</button><span class="muted">提交后立即显示得分。</span></div><div id="assignment-result" role="status"></div></form>` : closed ? `<div class="empty">${assignment.state === "upcoming" ? "作业尚未开放，请在开放时间后进入。" : "作业已截止，不能再提交。"}</div>` : ""}
    ${assignment.description ? `<section class="study-note"><summary>${icon("book")}作业说明</summary><div><p>${esc(assignment.description).replace(/\n/g, "<br>")}</p></div></section>` : ""}
  </div>`, assignment.title);
  if (attempt?.expires_at) {
    if (window.__assignmentTimer) clearInterval(window.__assignmentTimer);
    const updateTimer = () => {
      const timer = root.querySelector("#assignment-timer");
      const form = root.querySelector("#assignment-submit");
      if (!timer) return;
      const left = new Date(attempt.expires_at).getTime() - Date.now();
      if (left <= 0) {
        timer.textContent = "作答时间已结束，请提交或重新进入";
        timer.classList.add("expired");
        if (form) form.querySelector('[type="submit"]').disabled = true;
        clearInterval(window.__assignmentTimer);
        return;
      }
      const minutes = Math.floor(left / 60000), seconds = Math.floor(left % 60000 / 1000);
      timer.textContent = `剩余 ${minutes}:${String(seconds).padStart(2, "0")}`;
    };
    updateTimer();
    window.__assignmentTimer = setInterval(updateTimer, 1000);
  }
  const form = root.querySelector("#assignment-submit");
  if (!form) return;
  form.onsubmit = async event => {
    event.preventDefault();
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    const answers = Object.fromEntries([...new FormData(form)].map(([key, value]) => [key, Number(value)]));
    try {
      const result = await post(`/api/courses/${encodeURIComponent(course.id)}/submissions`, {
        assignment_id: id, attempt_id: attempt?.id, answers});
      if (result.pending_release) {
        form.querySelector("#assignment-result").innerHTML = '<div class="result"><h2>已提交</h2><p>本次成绩由教师统一发布，请耐心等待。</p></div>';
      } else if (result.answers_locked) {
        form.querySelector("#assignment-result").innerHTML = `<div class="result"><h2>本次得分 ${result.score} 分</h2><p>作答已保存，全部提交次数用完后可查看答案和解析。</p><button type="button" class="secondary" id="assignment-continue">继续作答</button></div>`;
        form.querySelector("#assignment-continue").onclick = () => location.reload();
      } else {
        form.querySelector("#assignment-result").innerHTML = `<div class="result"><h2>本次得分 ${result.score} 分</h2><p>答对 ${result.correct_count}/${result.total} 题${result.late ? ` · 迟交扣分 ${result.penalty_percent}%` : ""}，已保存到学习记录。</p>${result.details.map((detail, index) => `<p>${detail.correct ? "✓" : "✗"} ${index + 1}. ${esc(detail.text)} · 正确答案：${esc(detail.options[detail.answer])}${detail.explanation ? `<small class="answer-explanation">${esc(detail.explanation)}</small>` : ""}</p>`).join("")}<div class="actions">${analysisLink(link, result.id, course.id, "assignment")}</div></div>`;
      }
      button.textContent = "已提交";
      root.querySelector("#assignment-attempts").textContent = assignmentSubmissionText({...assignment, attempts:assignment.attempts + 1});
      form.querySelectorAll("input").forEach(input => { input.disabled = true; });
      form.querySelector("#assignment-result").scrollIntoView({behavior:"smooth", block:"nearest"});
    } catch (error) { tell(error.message); button.disabled = false; }
  };
}

// -------------------------------------------------------------------- gradebook
export async function gradebookPage(ctx) {
  const {api, layout, root, tell, link, params, href, fileUrl, majorName, user} = ctx;
  if (user.role !== "teacher") throw new Error("请使用教师账号查看成绩册");
  const courses = (await api("/api/courses")).filter(course => course.status !== "archived");
  if (!courses.length) return emptyPage(ctx, "还没有课程", "发布课程后，学生提交会进入成绩册。", "创建课程", "new-course");
  const courseId = params.get("course") || courses[0].id;
  if (!courses.some(course => course.id === courseId)) return emptyPage(ctx, "课程不存在", "请从工作台重新选择课程。");
  const mode = params.get("mode") || "latest";
  const status = params.get("status") || "all";
  const chapter = params.get("chapter") || "all";
  const query = new URLSearchParams({course_id:courseId, mode, status});
  if (chapter !== "all") query.set("chapter_id", chapter);
  const [course, data] = await Promise.all([
    api(`/api/courses/${encodeURIComponent(courseId)}`),
    api(`/api/teacher/gradebook?${query.toString()}`),
  ]);
  const summary = data.summary;
  const cards = [[summary.student_count, "学生", "user"], [summary.submitted_students, "已提交学生", "check"],
    [score(summary.average_score), "平均分", "chart"], [summary.pass_rate === null ? "—" : `${summary.pass_rate}%`, "及格率", "target"],
    [summary.column_count, "成绩列", "book"]].map(([value, label, graphic]) =>
    `<div class="analytics-card"><span class="stat-icon">${icon(graphic)}</span><b>${value}</b><span>${label}</span></div>`).join("");
  const header = `<tr><th class="sticky">学生</th>${data.columns.map(column => `<th><span>${column.kind === "assignment" ? "作业" : "章节"}</span><small>${esc(column.title)}</small></th>`).join("")}<th>平均分</th><th>提交项</th></tr>`;
  const rows = data.students.map(row => `<tr><th class="sticky"><a href="${href("student-detail", {student:row.student_id, course:courseId})}" target="_blank" rel="noopener">${esc(row.student)}</a><small>${esc(majorName(row.major_id))}</small></th>${data.columns.map(column => {
    const cell = row.cells[column.id] || {};
    return `<td class="${cell.submission_id ? "has-score" : "missing"}"><b>${score(cell.score)}</b>${cell.attempts > 1 ? `<small>${cell.attempts} 次</small>` : ""}${cell.late ? '<span class="late-tag">迟</span>' : ""}${cell.submitted_at ? `<small>${dateTime(cell.submitted_at)}</small>` : ""}${cell.submission_id ? analysisLink(link, cell.submission_id, courseId) : ""}</td>`;
  }).join("")}<td><b>${score(row.average_score)}</b></td><td>${row.submitted_count}</td></tr>`).join("");
  const chapterOptions = [["all", "全部章节"], ...course.chapters.map(chapter => [chapter.id, chapter.title])]
    .map(([value, label]) => `<option value="${esc(value)}" ${value === chapter ? "selected" : ""}>${esc(label)}</option>`).join("");
  const exportUrl = `/api/teacher/gradebook.csv?${query.toString()}`;
  layout(`${workspaceNav(ctx, course, "gradebook")}<div class="page-heading"><div><span class="eyebrow">GRADEBOOK</span><h1>成绩册</h1><p class="muted">只汇总已发布作业的成绩，可切换最高分、最近一次或首次成绩，并导出 CSV。自主练习在学情总览中查看。</p></div><div class="actions">${courseSelector(courses, courseId, "gradebook-course")}<a class="secondary" href="${fileUrl(exportUrl)}" download>${icon("arrow")}导出 CSV</a></div></div>
    <div class="gradebook-toolbar"><label>取值方式<select id="gradebook-mode"><option value="latest" ${mode === "latest" ? "selected" : ""}>最近一次</option><option value="best" ${mode === "best" ? "selected" : ""}>最高分</option><option value="first" ${mode === "first" ? "selected" : ""}>第一次</option></select></label><label>学生<select id="gradebook-status"><option value="all" ${status === "all" ? "selected" : ""}>全部</option><option value="submitted" ${status === "submitted" ? "selected" : ""}>已有提交</option><option value="missing" ${status === "missing" ? "selected" : ""}>完全未交</option></select></label><label>章节<select id="gradebook-chapter">${chapterOptions}</select></label><button class="ghost" id="gradebook-refresh">${icon("refresh")}刷新</button></div>
    <section class="analytics-grid gradebook-summary">${cards}</section>
    <section><div class="section-title"><div><h2>${esc(course.title)}</h2><span class="muted">${data.columns.length} 列 · ${data.students.length} 名学生 · 成绩按 ${mode === "best" ? "最高分" : mode === "first" ? "第一次" : "最近一次"} 统计</span></div></div>${data.columns.length ? `<div class="table-scroll gradebook-scroll"><table class="gradebook"><thead>${header}</thead><tbody>${rows || '<tr><td colspan="99" class="empty">没有符合筛选条件的学生。</td></tr>'}</tbody></table></div>` : '<div class="empty">当前筛选范围没有已发布作业，因此没有作业成绩。</div>'}</section>
    <p class="muted">成绩册仅统计已发布的作业。自主练习与错题复习不计入作业成绩；点学生姓名可查看完整学习记录。</p>`, "成绩册");
  root.querySelector("#gradebook-course").onchange = event => { location.href = href("gradebook", {course:event.target.value, mode, status, ...(chapter !== "all" ? {chapter} : {})}); };
  root.querySelector("#gradebook-mode").onchange = event => { location.href = href("gradebook", {course:courseId, mode:event.target.value, status, ...(chapter !== "all" ? {chapter} : {})}); };
  root.querySelector("#gradebook-status").onchange = event => { location.href = href("gradebook", {course:courseId, mode, status:event.target.value, ...(chapter !== "all" ? {chapter} : {})}); };
  root.querySelector("#gradebook-chapter").onchange = event => { location.href = href("gradebook", {course:courseId, mode, status, ...(event.target.value !== "all" ? {chapter:event.target.value} : {})}); };
  root.querySelector("#gradebook-refresh").onclick = () => gradebookPage(ctx).catch(error => tell(error.message));
}

// ---------------------------------------------------------------- student detail
export async function studentDetailPage(ctx) {
  const {api, layout, root, tell, link, params, recordList, majorName, user} = ctx;
  if (user.role !== "teacher") throw new Error("请使用教师账号查看学生详情");
  const studentId = params.get("student");
  const courseId = params.get("course");
  if (!studentId) return emptyPage(ctx, "没有选择学生", "请从成绩册打开学生详情。", "返回成绩册", "gradebook");
  const query = courseId ? `?course_id=${encodeURIComponent(courseId)}` : "";
  const data = await api(`/api/teacher/students/${encodeURIComponent(studentId)}${query}`);
  const courses = data.courses;
  const courseTitle = id => courses.find(course => course.id === id)?.title || "历史课程";
  const profile = data.profile || {student:data.student.name, student_id:studentId, average_score:null,
    course_participation:0, practice_activity:0, mastery_score:0, course_count:0, submission_count:0,
    weak_points:[], not_started:true};
  const assignmentRows = data.assignments.map(item => `<tr><th class="sticky">${esc(item.title)}</th><td>${stateLabel(displayState(item))}</td><td>${score(item.latest_score)}</td><td>${item.attempts}/${item.max_attempts}</td><td>${dateTime(item.due_at)}</td></tr>`).join("");
  const wrongRows = data.wrong_questions.map(item => `<div class="record-row"><div><b>${esc(item.question_snapshot.text)}</b><small>${esc(courseTitle(item.course_id))} · ${esc(item.chapter_title || "未分章节")} · 答错 ${item.wrong_count} 次</small></div><span class="tag ${item.status === "mastered" ? "published" : "attention"}">${item.status === "mastered" ? "已掌握" : "待巩固"}</span></div>`).join("");
  layout(`<div class="page-heading"><div><span class="eyebrow">STUDENT DETAIL</span><h1>${esc(data.student.name)}</h1><p class="muted">${esc(majorName(data.student.major_id))} · ${courses.map(course => esc(course.title)).join("、")}</p></div><div class="actions">${link("gradebook", "返回成绩册", "secondary", courseId ? {course:courseId} : {})}${link("records", "全部学习分析", "ghost")}</div></div>
    <section class="personal-profile">${radarCard(profile)}</section>
    <section><div class="section-title"><div><h2>作业情况</h2><span class="muted">仅显示当前教师课程内的作业</span></div></div>${assignmentRows ? `<div class="table-scroll"><table class="gradebook"><thead><tr><th>作业</th><th>状态</th><th>最近得分</th><th>次数</th><th>截止</th></tr></thead><tbody>${assignmentRows}</tbody></table></div>` : '<div class="empty">暂无作业记录。</div>'}</section>
    <section><div class="section-title"><div><h2>自主练习记录</h2><span class="muted">${data.submissions.filter(r=>!r.assignment_id).length} 次</span></div></div>${recordList(data.submissions.filter(r=>!r.assignment_id), true)}</section><section><div class="section-title"><h2>作业提交记录</h2></div>${recordList(data.submissions.filter(r=>r.assignment_id),true)}</section>
    <section><div class="section-title"><div><h2>错题与掌握情况</h2><span class="muted">${data.wrong_questions.length} 道</span></div></div><div class="record-list">${wrongRows || '<div class="empty compact">暂无错题。</div>'}</div></section>`, `${data.student.name} · 学生详情`);
  root.querySelector("#refresh")?.addEventListener("click", () => studentDetailPage(ctx).catch(error => tell(error.message)));
}

// ----------------------------------------------------------------- announcements
export async function announcementsPage(ctx) {
  if (ctx.user.role === "teacher") return teacherAnnouncementsPage(ctx);
  return studentAnnouncementsPage(ctx);
}

async function teacherAnnouncementsPage(ctx) {
  const {api, post, del, layout, root, tell, link, params, href, user} = ctx;
  const courses = (await api("/api/courses")).filter(course => course.status !== "archived");
  if (!courses.length) return emptyPage(ctx, "还没有课程", "发布课程后即可给学生发送公告。", "创建课程", "new-course");
  const courseId = params.get("course") || courses[0].id;
  if (!courses.some(course => course.id === courseId)) return emptyPage(ctx, "课程不存在", "请从工作台重新选择课程。");
  const [course, announcements] = await Promise.all([
    api(`/api/courses/${encodeURIComponent(courseId)}`),
    api(`/api/courses/${encodeURIComponent(courseId)}/announcements`),
  ]);
  const cards = announcements.map(item => `<article class="announcement-card ${item.pinned ? "pinned" : ""}"><div class="announcement-card-head"><span class="tag ${item.pinned ? "attention" : "neutral"}">${item.pinned ? "置顶" : "公告"}</span><small>${dateTime(item.created_at)}</small></div><h3>${esc(item.title)}</h3><p>${esc(item.body).replace(/\n/g, "<br>")}</p><div class="announcement-meta"><span>收件 ${item.recipient_count} 人</span><span>已读 ${item.read_count} 人</span><span>${item.audience === "all" ? "全体学生" : item.audience === "non_submitters" ? "未提交学生" : "需关注学生"}</span><button class="ghost small danger" type="button" data-delete-announcement="${esc(item.id)}">删除</button></div></article>`).join("");
  layout(`${workspaceNav(ctx, course, "announcements")}<div class="page-heading"><div><span class="eyebrow">ANNOUNCEMENTS</span><h1>公告与提醒</h1><p class="muted">向全体、未提交或需关注的学生发送站内公告；学生端会显示未读提醒。</p></div><div class="actions">${courseSelector(courses, courseId, "announcement-course")}${link("edit-course", "返回课程编辑", "secondary", {course:courseId})}</div></div>
    <section><div class="section-title"><div><h2>${esc(course.title)}</h2><span class="muted">${announcements.length} 条公告</span></div></div><div class="announcement-list">${cards || '<div class="empty">还没有公告。</div>'}</div></section>
    <section><div class="section-title"><h2>发布公告</h2><span class="muted">收件人由服务端按课程范围计算</span></div><div class="editor"><form id="announcement-create"><label>标题<input name="title" maxlength="100" required placeholder="例如：本周作业提醒"></label><label>内容<textarea name="body" maxlength="5000" required placeholder="写清时间、任务和需要学生做的事"></textarea></label><div class="editor-fields"><label>受众<select name="audience"><option value="all">全体学生</option><option value="non_submitters">未提交学生</option><option value="at_risk">需关注学生</option></select></label><label class="check-row"><input type="checkbox" name="pinned"> 置顶公告</label></div><div class="actions"><button class="secondary" type="submit">发布公告</button></div></form></div></section>`, "公告管理");
  root.querySelector("#announcement-course").onchange = event => { location.href = href("announcements", {course:event.target.value}); };
  root.querySelector("#announcement-create").onsubmit = async event => {
    event.preventDefault();
    try { await post(`/api/courses/${courseId}/announcements`, Object.fromEntries(new FormData(event.target))); await teacherAnnouncementsPage(ctx); tell("公告已发布"); }
    catch (error) { tell(error.message); }
  };
  root.querySelectorAll("[data-delete-announcement]").forEach(button => {
    button.onclick = async () => {
      if (!confirm("删除这条公告？")) return;
      try { await del(`/api/courses/${courseId}/announcements/${encodeURIComponent(button.dataset.deleteAnnouncement)}`); await teacherAnnouncementsPage(ctx); tell("公告已删除"); }
      catch (error) { tell(error.message); }
    };
  });
}

async function studentAnnouncementsPage(ctx) {
  const {api, post, layout, root, tell} = ctx;
  const inbox = await api("/api/announcements");
  const cards = inbox.items.map(item => `<article class="announcement-card ${item.read ? "" : "unread"} ${item.pinned ? "pinned" : ""}"><div class="announcement-card-head"><span class="tag ${item.pinned ? "attention" : "neutral"}">${item.pinned ? "置顶公告" : esc(item.course_name)}</span><small>${dateTime(item.created_at)}</small></div><h3>${esc(item.title)}</h3><p>${esc(item.body).replace(/\n/g, "<br>")}</p><div class="announcement-meta"><span>${esc(item.teacher)} · ${esc(item.course_name)}</span>${item.read ? '<span class="tag neutral">已读</span>' : `<button class="secondary small" type="button" data-read-announcement="${esc(item.id)}">标记已读</button>`}</div></article>`).join("");
  layout(`<div class="page-heading"><div><span class="eyebrow">ANNOUNCEMENTS</span><h1>公告</h1><p class="muted">教师发布的课程通知与提醒；未读公告会在导航显示红点。</p></div><div class="actions">${inbox.unread_count ? '<button class="secondary" id="read-all-announcements">全部标记已读</button>' : ""}<button class="ghost" id="refresh">${icon("refresh")}刷新公告</button></div></div>
    <div class="announcement-list">${cards || '<div class="empty">暂无公告。</div>'}</div>`, "公告");
  root.querySelector("#refresh").onclick = () => studentAnnouncementsPage(ctx).catch(error => tell(error.message));
  root.querySelectorAll("[data-read-announcement]").forEach(button => {
    button.onclick = async () => {
      try { await post(`/api/announcements/${encodeURIComponent(button.dataset.readAnnouncement)}/read`, {}); document.dispatchEvent(new CustomEvent("announcement-read")); await studentAnnouncementsPage(ctx); }
      catch (error) { tell(error.message); }
    };
  });
  const readAll = root.querySelector("#read-all-announcements");
  if (readAll) readAll.onclick = async () => {
    try {
      const unread = inbox.items.filter(item => !item.read);
      for (const item of unread) await post(`/api/announcements/${encodeURIComponent(item.id)}/read`, {});
      document.dispatchEvent(new CustomEvent("announcement-read", {detail:{count:unread.length}}));
      await studentAnnouncementsPage(ctx);
      tell("已全部标记为已读");
    } catch (error) { tell(error.message); }
  };
}
