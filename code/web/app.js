import {teacherProgramming, bindTeacherProgramming, programmingPage, programmingRecord} from "/static/programming.js";
import {quizPage} from "/static/ai-quiz.js";
import {analysisLink, analysisPage} from "/static/analysis.js";
import { esc, icon, number, radarCard, assignmentSubmissionText, assignmentPracticeNote } from "/static/ui.js";
import { analyticsPage, reviewCenter } from "/static/learning-pages.js";
import { assignmentsPage, assignmentDetailPage, gradebookPage, studentDetailPage,
         announcementsPage } from "/static/course-pages.js";
import {teachingCoursesPage, courseLabel} from "/static/teaching-courses.js";
import {learningFilePage} from "/static/learning-file.js";
import {workspaceNav, teachingAiPage} from "/static/teacher-workspace.js";
import {assignmentBuilderPage} from "/static/assignment-builder.js";
import { mountVideo } from "/static/video.js";
import { liveQuizPage } from "/static/live-quiz.js";
import { initializeApiKey } from "/static/ai-key.js";
import { initializeXfyunCredentials } from "/static/xfyun-key.js";

const root = document.getElementById("platform");
const params = new URLSearchParams(location.search);
let config, user;
let majors = [];
let unreadAnnouncements = 0;
const majorName = id => majors.find(major => major.id === id)?.name || "未设置专业";
const context = () => ({api, post, patch, del, layout, root, tell, link, user, config, recordList,
                        majorName, fileUrl, href, params, date});
let account = params.get("account") || "";
let page = params.get("page") || (account ? "dashboard" : "login");
const roleName = role => role === "teacher" ? "教师" : "学生";
const date = value => value ? new Date(value).toLocaleString("zh-CN") : "暂无";
const assignmentStateLabel = state => ({open:"进行中", late:"迟交中", upcoming:"未开始",
  closed:"已截止", completed:"已完成", draft:"草稿", archived:"已归档"}[state] || state);
const assignmentStateClass = state => ["open", "completed"].includes(state) ? "published"
  : ["late", "upcoming"].includes(state) ? "attention" : "neutral";
const assignmentDisplayState = item => item.completed ? "completed" : item.state;
const href = (view, extra = {}) => `/?${new URLSearchParams({page:view, ...(account ? {account} : {}), ...extra})}`;
const link = (view, label, cls = "secondary", extra = {}) => `<a class="${cls}" href="${esc(href(view, extra))}" target="_blank" rel="noopener">${label} ↗</a>`;
const fileUrl = path => `${path}${path.includes("?") ? "&" : "?"}account=${encodeURIComponent(account)}`;

async function api(url, options = {}) {
  const response = await fetch(url, {...options, headers: {"X-Local-Token":config?.token || "", "X-Account-ID":account, ...options.headers}});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof data.detail === "string" ? data.detail : "请求失败，请稍后重试");
    error.status = response.status;
    throw error;
  }
  return data;
}
const post = (url, data = {}) => api(url, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(data)});
const patch = (url, data = {}) => api(url, {method:"PATCH", headers:{"Content-Type":"application/json"}, body:JSON.stringify(data)});
const del = (url, data = {}) => api(url, {method:"DELETE", headers:{"Content-Type":"application/json"}, body:JSON.stringify(data)});
function announcementBadge() {
  const bell = root.querySelector(".nav-bell");
  if (!bell) return;
  bell.querySelector(".nav-badge")?.remove();
  if (unreadAnnouncements > 0) {
    bell.insertAdjacentHTML("beforeend", `<span class="nav-badge">${unreadAnnouncements > 99 ? "99+" : unreadAnnouncements}</span>`);
  }
  bell.setAttribute("aria-label", `公告${unreadAnnouncements ? `，${unreadAnnouncements} 条未读` : ""}`);
}
document.addEventListener("announcement-read", event => {
  unreadAnnouncements = Math.max(0, unreadAnnouncements - (event.detail?.count || 1));
  announcementBadge();
});
function tell(message) {
  const node = root.querySelector(".notice");
  node.textContent = message;
  node.hidden = false;
  clearTimeout(tell.timer);
  tell.timer = setTimeout(() => { node.hidden = true; }, 5000);
}
function nav() {
  const current = view => page === view
    || (view === "teaching" && user?.role === "teacher" && ["edit-course","assignment-builder","new-course","assignments","teaching-ai","gradebook","announcements"].includes(page))
    || (view === "learning-center" && user?.role === "student" && ["profile","records"].includes(page))
    || (view === "assignments" && page === "assignment")
    || (view === "review-center" && ["review","wrong"].includes(page));
  const item = (view,label,graphic) => link(view, `${icon(graphic)}<span>${label}</span>`, `nav-link${current(view) ? " active" : ""}`);
  return `<header class="nav"><a class="brand" href="${esc(user ? href("dashboard") : "/")}"><span class="brand-mark">${icon("layers")}</span><span>知学<small>LEARN WITH PURPOSE</small></span></a>
    ${user ? `<nav class="nav-links" aria-label="主导航">${item("dashboard", "工作台", "home")}${item("video", "视频学习", "video")}${item("live-quiz", "课堂抢答", "target")}${user.role === "teacher" ? item("teaching", "课程与作业", "book") + item("records", "学情总览", "chart") : item("assignments", "作业", "book") + item("review-center", "错题与复习", "review") + item("learning-center", "学习档案", "chart")}${item("account", "账号", "user")}</nav><div class="nav-account"><a class="nav-bell" href="${esc(href("announcements"))}" target="_blank" rel="noopener" aria-label="公告">${icon("bell")}${unreadAnnouncements ? `<span class="nav-badge">${unreadAnnouncements > 99 ? "99+" : unreadAnnouncements}</span>` : ""}</a><span class="avatar small">${esc(user.name.slice(0,1))}</span><span class="identity">${esc(user.name)}<small>${user.role === "teacher" ? "教师 · 跨专业授课" : esc(majorName(user.major_id))}</small></span><button class="ghost" id="logout">退出</button></div>` : '<span class="identity">课程、练习与成长，连接在一起。</span>'}</header><div class="notice" role="status" hidden></div>`;
}
function layout(content, title) {
  document.title = `${title} · 知学`;
  root.innerHTML = nav() + `<main class="shell">${user?.demo ? '<div class="demo-banner">演示账号<span>标注“演示”的记录来自预置样例；你也可以继续真实作答。</span></div>' : ""}${content}<footer class="platform-footer"><span>知学 · 把知识变成自己的能力</span><span>课程学习 / 错题巩固 / 学习画像</span></footer></main>`;
  const logout = root.querySelector("#logout");
  if (logout) logout.onclick = async () => {
    try { await post("/api/auth/logout"); location.href = "/"; } catch (error) { tell(error.message); }
  };
}
function recordList(items, teacher = false) {
  if (!items.length) return '<div class="record-list empty">暂无练习提交记录。</div>';
  return `<div class="record-list">${items.map(r => {
    const pending = Boolean(r.pending_release);
    const score = pending ? '<span class="tag attention">待发布成绩</span>' : `<strong class="score">${r.score} 分</strong>`;
    return `<div class="record-row"><div><b>${teacher ? esc(r.student) + " · " : ""}${esc(r.course_name || r.course_id)}${r.teaching_class && !(r.course_name || "").includes(r.teaching_class) ? ` · ${esc(r.teaching_class)}` : ""}</b><small>${r.demo ? "演示记录 · " : ""}${date(r.submitted_at)} · ${r.type === "ai_quiz" ? "AI 自测 · " : r.assignment_id ? "作业提交 · " : "自主练习 · "}${pending ? "成绩待教师发布" : r.type === "python" ? `Python 编程 · 用例通过 ${r.passed_cases}/${r.total_cases}` : `答对 ${r.correct_count}/${r.total} 题`}${r.assignment_title ? ` · 作业：${esc(r.assignment_title)}` : r.chapter_title ? ` · ${esc(r.chapter_title)}` : ""}${r.late ? " · 迟交" : ""}</small>${r.type === "python" ? programmingRecord(r) : ""}</div><div class="record-actions">${score}${pending ? "" : analysisLink(link, r.id, r.course_id)}</div></div>`;
  }).join("")}</div>`;
}
function bindForm(selector, action) {
  const form = root.querySelector(selector);
  form.onsubmit = async event => {
    event.preventDefault();
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    try { await action(Object.fromEntries(new FormData(form)), form); }
    catch (error) { tell(error.message); }
    finally { button.disabled = false; }
  };
}

async function loginPage(message = "") {
  user = null;
  const [examples, majors] = await Promise.all([api("/api/auth/demo-accounts"), api("/api/majors")]);
  const majorName = id => majors.find(m => m.id === id)?.name || "跨专业授课";
  layout(`<div class="auth-intro"><span class="eyebrow">LEARN · PRACTICE · GROW</span><h1>让每一次学习，都有迹可循。</h1><p class="muted">发布资源与练习，获得反馈，让教师与学生看见学习进展。</p></div>
    ${message ? `<p id="login-message" class="warning">${esc(message)}</p>` : ""}
    <div class="auth-grid"><div class="form-card"><div class="auth-tabs" role="group" aria-label="登录或注册"><button id="login-tab" class="primary">账号登录</button><button id="register-tab" class="ghost">注册账号</button></div><div id="auth-form"></div></div>
    <section class="demo-panel"><span class="eyebrow">QUICK START</span><h2>演示账号</h2><p class="muted">点击“填入账号”，再点击登录。可分别打开教师与学生工作台。</p><div class="table-scroll"><table><thead><tr><th>身份</th><th>学生专业 / 授课</th><th>账号</th><th>密码</th><th>操作</th></tr></thead><tbody>${examples.map((u, i) => `<tr><td>${esc(u.name)}<small>${roleName(u.role)}</small></td><td><span class="tag">${esc(majorName(u.major_id))}</span></td><td><code>${esc(u.username)}</code></td><td><code>${esc(u.password)}</code></td><td><button class="secondary fill-account" data-index="${i}">填入账号</button></td></tr>`).join("")}</tbody></table></div><p class="muted demo-note">这是课堂演示环境，请使用演示专用密码。</p></section></div>`, "登录与注册");
  function showForm(register = false) {
    root.querySelector(".auth-tabs").hidden = false;
    root.querySelectorAll(".fill-account").forEach(button => { button.disabled = false; });
    root.querySelector("#login-tab").className = register ? "ghost" : "primary";
    root.querySelector("#register-tab").className = register ? "primary" : "ghost";
    root.querySelector("#auth-form").innerHTML = `<form id="auth"><h2>${register ? "创建你的账号" : "欢迎回来"}</h2>
      ${register ? '<label>身份<select name="role" aria-label="身份"><option value="student">学生</option><option value="teacher">教师</option></select></label><label id="student-major-field">学生专业<select name="major_id" aria-label="学生专业" required><option value="">请选择专业</option>' + majors.map(m => `<option value="${esc(m.id)}">${esc(m.name)}</option>`).join("") + '</select></label><label>姓名<input name="name" autocomplete="name" maxlength="30" required></label>' : ""}
      <label>账号<input name="username" autocomplete="username" placeholder="字母、数字或下划线" minlength="3" maxlength="32" pattern="[A-Za-z0-9_]{3,32}" required></label>
      <label>密码<input name="password" type="password" autocomplete="${register ? "new-password" : "current-password"}" minlength="6" maxlength="128" required></label>
      ${register ? '<label>确认密码<input name="confirm" type="password" autocomplete="new-password" minlength="6" maxlength="128" required></label>' : ""}
      <div class="actions"><button class="primary" type="submit">${register ? "注册账号" : "登录"}</button></div></form>`;
    if (register) {
      const role = root.querySelector('[name="role"]');
      role.onchange = () => {
        const teacher = role.value === "teacher";
        root.querySelector("#student-major-field").hidden = teacher;
        root.querySelector('[name="major_id"]').required = !teacher;
        root.querySelector('[name="major_id"]').disabled = teacher;
      };
    }
    bindForm("#auth", async (data, form) => {
      if (register) {
        if (data.password !== data.confirm) throw new Error("两次输入的密码不一致");
        await post("/api/auth/register", data);
      }
      const authenticated = await post("/api/auth/login", data);
      form.reset();
      account = authenticated.id;
      const destination = href("dashboard");
      // Use an explicit link after asynchronous login so popup blockers cannot swallow the tab.
      root.querySelector(".auth-tabs").hidden = true;
      if (root.querySelector("#login-message")) root.querySelector("#login-message").hidden = true;
      root.querySelector(".notice").hidden = true;
      root.querySelectorAll(".fill-account").forEach(button => { button.disabled = true; });
      root.querySelector("#auth-form").innerHTML = `<div id="auth-success"><p role="status">${register ? "注册成功" : "登录成功"}，${esc(authenticated.name)}</p><div class="actions"><a id="enter-workspace" class="primary" href="${esc(destination)}" target="_blank" rel="noopener">进入工作台 ↗</a><button id="login-another" class="secondary" type="button">登录其它账号</button></div></div>`;
      root.querySelector("#login-another").onclick = () => {
        account = "";
        showForm(false);
        root.querySelector('[name="username"]').focus();
      };
      root.querySelector("#enter-workspace").focus();
    });
  }
  root.querySelector("#login-tab").onclick = () => showForm(false);
  root.querySelector("#register-tab").onclick = () => showForm(true);
  root.querySelectorAll(".fill-account").forEach(button => button.onclick = () => {
    showForm(false);
    const example = examples[Number(button.dataset.index)];
    root.querySelector('[name="username"]').value = example.username;
    root.querySelector('[name="password"]').value = example.password;
    root.querySelector('#auth [type="submit"]').focus();
  });
  showForm(false);
}

async function dashboard() {
  const teacher = user.role === "teacher";
  const requests = [api("/api/courses"), api("/api/learning-records"),
    api(teacher ? "/api/teacher/analytics" : "/api/student/profile")];
  if (!teacher) requests.push(api("/api/assignments").catch(() => []),
    api("/api/announcements").catch(() => ({items: [], unread_count: 0})));
  const [courses, logs, extra, assignments = [], inbox = {items: [], unread_count: 0}] = await Promise.all(requests);
  const wrong = teacher ? [] : await api("/api/wrong-questions");
  unreadAnnouncements = inbox.unread_count || 0;
  const due = wrong.filter(item => item.status !== "mastered" && new Date(item.next_review_at) <= new Date()).length;
  const openAssignments = assignments.filter(item => item.can_start).length;
  const stats = teacher ? [[courses.length,"我的教学班"],[extra.overview.student_count,"面向学生"],
    [extra.overview.submission_count,"正式提交"],[number(extra.overview.average_score),"平均成绩"]]
    : [[courses.length,"可学课程"],[openAssignments,"待完成作业"],[due,"今日待复习"],[number(extra.profile.average_score),"平均成绩"]];
  const courseCards = courses.map(course => {
    const entries = logs.filter(record => record.course_id === course.id);
    const chapterIds = new Set(entries.flatMap(record => [record.chapter_id, ...(record.details || []).map(detail => detail.chapter_id)]).filter(Boolean));
    const completed = course.chapters.filter(chapter => chapterIds.has(chapter.id)).length;
    const category = ["deep-learning", "web", "machine-learning"].includes(course.category) ? course.category : "python";
    const statusLabel = course.status === "published" ? "已发布" : course.status === "archived" ? "已归档" : "草稿";
    const questionCount = course.questions.filter(q => q.status !== "retired").length
      + (course.programming_questions || []).filter(q => q.status !== "retired").length;
    return `<article class="course-card"><div class="course-cover"><img src="/static/course-${category}.svg" alt="" loading="lazy"><span class="tag ${teacher ? course.status : "neutral"}">${teacher ? statusLabel : course.level || "课程学习"}</span></div><div class="course-content"><div class="course-audience">${course.major_ids.length === majors.length ? '<span class="course-kind">所有专业</span>' : course.major_ids.map(mid => `<span class="course-kind">${esc(majorName(mid))}</span>`).join("")}${course.demo ? '<span class="demo-label">示例课程</span>' : ""}</div><h3>${esc(course.title)}</h3>${course.teaching_class ? `<span class="dashboard-class-label">${esc(course.teaching_class)} · ${esc(course.term || "")}</span>` : ""}<p>${esc(course.description || "打开课程，开始你的下一段学习。")}</p><span class="meta">${esc(course.teacher)} · ${course.chapters.length} 章 · ${questionCount} 道练习</span>${teacher ? `<p class="course-progress-label">${course.resources.length} 份学习资源 · ${entries.length} 次提交</p>` : `<div class="course-progress-label"><span>已练习 ${completed} / ${course.chapters.length} 章</span><span>${Math.round(completed/Math.max(1,course.chapters.length)*100)}%</span></div><progress class="course-progress" max="${Math.max(1,course.chapters.length)}" value="${completed}" aria-label="${esc(course.title)}章节练习进度"></progress>`}${link(teacher ? "edit-course" : "course", teacher ? (course.status === "archived" ? "查看归档课程" : "进入教学空间") : "继续学习", "course-link", {course:course.id})}</div></article>`;
  }).join("");
  const assignmentCards = assignments.filter(item => item.can_start || item.pending_release || item.latest_submission_id)
    .slice(0, 3).map(item => `<article class="assignment-card"><div class="assignment-card-head"><span class="tag ${assignmentStateClass(assignmentDisplayState(item))}">${assignmentStateLabel(assignmentDisplayState(item))}</span><small>${esc(item.course_name)}</small></div><h3>${esc(item.title)}</h3><p class="muted">${item.due_at ? `截止 ${date(item.due_at)}` : "未设置截止时间"} · ${assignmentSubmissionText(item)}${item.best_score !== null ? ` · 最高 ${item.best_score} 分` : ""}</p>${assignmentPracticeNote(item, link)}<div class="actions">${link("assignment", item.latest_submission_id ? "查看结果" : "去完成", "secondary", {assignment:item.id})}</div></article>`).join("");
  const announcementCards = (inbox.items || []).slice(0, 2).map(item => `<article class="announcement-card ${item.read ? "" : "unread"}"><div class="announcement-card-head"><span class="tag ${item.pinned ? "attention" : "neutral"}">${item.pinned ? "置顶公告" : esc(item.course_name)}</span><small>${date(item.created_at)}</small></div><h3>${esc(item.title)}</h3><p>${esc(item.body).replace(/\n/g, "<br>")}</p></article>`).join("");
  layout(`<div class="hero dashboard-hero"><div><span class="eyebrow">${teacher ? "TEACH WITH INSIGHT" : "A LITTLE PROGRESS, EVERY DAY"}</span><h1>${esc(user.name)}，${teacher ? "让教学更有方向。" : "今天也向前一步。"}</h1><p class="muted">${teacher ? "跨专业发布课程，用真实练习反馈看见每位学生的成长。" : `${esc(majorName(user.major_id))} · 从一节课程、一份练习开始，把知识变成自己的能力。`}</p><div class="hero-actions">${teacher ? link("new-course", `${icon("plus")}创建课程`, "primary") + link("teaching", "课程与作业") : link(due ? "review-center" : "learning-center", `${icon(due ? "review" : "chart")}${due ? `复习 ${due} 道到期错题` : "查看我的学习档案"}`, "primary") + link("assignments", "我的作业") + link("video", "整理课程视频")}</div></div><div class="hero-art" aria-hidden="true">${icon(teacher ? "chart" : "book")}<span class="art-orbit orbit-one"></span><span class="art-orbit orbit-two"></span><span class="art-caption">${teacher ? "每一次反馈都有价值" : "学有所获，学有所长"}</span></div></div><div class="dashboard-stats">${stats.map(([value,label]) => `<div><b>${value}</b><span>${label}</span></div>`).join("")}</div><section><div class="section-title"><div><span class="eyebrow">${teacher ? "MY TEACHING" : "MY COURSES"}</span><h2>${teacher ? "我的教学班" : "为你开放的课程"}</h2></div><button class="ghost" id="refresh">${icon("refresh")}刷新</button></div><div class="course-grid">${courseCards || '<div class="empty-state">暂无课程，请联系教师发布面向你所在专业的课程。</div>'}</div></section>${!teacher ? `<section><div class="section-title"><div><span class="eyebrow">ASSIGNMENTS</span><h2>我的作业</h2></div>${link("assignments", "全部作业", "text-link")}</div><div class="assignment-grid">${assignmentCards || '<div class="empty">暂无待完成作业。</div>'}</div></section><section><div class="section-title"><div><span class="eyebrow">ANNOUNCEMENTS</span><h2>最新公告</h2></div>${link("announcements", "全部公告", "text-link")}</div><div class="announcement-list">${announcementCards || '<div class="empty">暂无公告。</div>'}</div></section><div class="learning-shortcuts"><a href="${esc(href("review-center"))}">${icon("review")}<div><b>错题与复习</b><small>把薄弱知识点，变成下一次的进步</small></div>${icon("arrow")}</a><a href="${esc(href("learning-center"))}">${icon("chart")}<div><b>我的学习档案</b><small>查看个人画像、课程进度与提交记录</small></div>${icon("arrow")}</a></div>` : ""}<section><div class="section-title"><div><span class="eyebrow">RECENT ACTIVITY</span><h2>${teacher ? "最近的学生提交" : "最近的学习记录"}</h2></div>${link(teacher ? "records" : "learning-center", "全部记录", "text-link", teacher ? {} : {tab:"records"})}</div>${recordList(logs.slice(0,5),teacher)}</section>`,teacher ? "教师工作台" : "学生工作台");
  root.querySelector("#refresh").onclick = () => dashboard().catch(error => tell(error.message));
}

async function learningPage() {
  const preview = params.get("preview") === "1";
  if (user.role !== "student" && !(user.role === "teacher" && preview)) throw new Error("请使用学生账号打开学习页面");
  const courseId = encodeURIComponent(params.get("course") || "");
  const course = await api(preview ? `/api/courses/${courseId}/preview` : `/api/courses/${courseId}`);
  const assignments = user.role === "student" ? await api("/api/assignments").catch(() => []) : [];
  const chapters = course.chapters || [];
  const chapterBlocks = chapters.map((ch, ci) => {
    const resources = course.resources.filter(r => r.chapter_id === ch.id);
    const questions = course.questions.filter(q => q.chapter_id === ch.id);
    const programming = (course.programming_questions || []).filter(q => q.chapter_id === ch.id);
    const chapterAssignments = assignments.filter(item => item.course_id === course.id && item.chapter_id === ch.id);
    const assignmentBlock = chapterAssignments.length ? `<div class="chapter-assignments">${chapterAssignments.map(item => `<article class="assignment-card compact"><div class="assignment-card-head"><span class="tag ${assignmentStateClass(assignmentDisplayState(item))}">${assignmentStateLabel(assignmentDisplayState(item))}</span><small>${esc(item.course_name)}</small></div><div class="assignment-card-body"><b>${esc(item.title)}</b><p class="muted">${item.due_at ? `截止 ${date(item.due_at)}` : "未设置截止时间"} · ${assignmentSubmissionText(item)}${item.pending_release ? " · 待发布成绩" : ""}</p>${assignmentPracticeNote(item, link)}</div><div class="actions">${item.can_start ? link("assignment", "开始作业", "primary", {assignment:item.id}) : link("assignment", "查看作业", "secondary", {assignment:item.id})}</div></article>`).join("")}</div>` : "";
    const questionBlock = questions.length
      ? (preview
        ? `<div class="quiz preview-quiz"><p class="preview-note">学生视角预览：答案与解析已隐藏，提交按钮不可用。</p><div class="questions">${questions.map((q, i) => `<fieldset class="question" disabled><legend>${i + 1}. ${esc(q.text)}</legend>${q.options.map((option, oi) => `<label class="option"><input type="radio" disabled> ${String.fromCharCode(65 + oi)}. ${esc(option)}</label>`).join("")}</fieldset>`).join("")}</div></div>`
        : `<form class="quiz" data-chapter="${esc(ch.id)}"><div class="questions">${questions.map((q, i) => `<fieldset class="question"><legend>${i + 1}. ${esc(q.text)}</legend>${q.options.map((option, oi) => `<label class="option"><input type="radio" name="${esc(q.id)}" value="${oi}" required> ${String.fromCharCode(65 + oi)}. ${esc(option)}</label>`).join("")}</fieldset>`).join("")}</div><div class="actions"><button class="primary" type="submit">提交本章练习</button></div><div class="quiz-result" role="status"></div></form>`)
      : '<p class="muted">本章暂无单选题。</p>';
    return `<section class="chapter"><div class="section-title"><div><span class="eyebrow">CHAPTER ${ci + 1}</span><h2>${esc(ch.title)}</h2></div><span class="tag">${questions.length} 道单选 · ${programming.length} 道编程</span></div>
      ${preview ? '<p class="preview-note">此页面为学生视角预览，不显示正确答案、解析和隐藏用例。</p>' : ""}
      ${assignmentBlock}
      ${resources.length ? `<div class="resource-list">${resources.map(r => `<div class="resource"><div><b>${esc(r.name)}</b><small>${esc(r.kind)}</small></div>${r.job_id ? `<div class="actions"><a class="secondary" href="${fileUrl(`/api/jobs/${encodeURIComponent(r.job_id)}/files/pdf?preview=true`)}" target="_blank" rel="noopener">打开课件 ↗</a>${course.teacher_id === user.id ? link("video", "提取结果", "ghost", {job:r.job_id}) : ""}</div>` : '<span class="muted">资源说明</span>'}</div>`).join("")}</div>` : ""}
      ${resources.filter(r => r.content).map(r => `<details class="study-note" open><summary>${icon("book")}${esc(r.name)}</summary><div>${r.content.split("\n").map(paragraph => `<p>${esc(paragraph)}</p>`).join("")}${r.code ? `<pre><code>${esc(r.code)}</code></pre>` : ""}</div></details>`).join("")}
      ${questionBlock}
      ${programming.map(q => `<div class="python-question-link"><div><b>${esc(q.title)}</b><small>Python 编程 · 3 秒 / 256 MB</small></div>${preview ? '<span class="tag neutral">学生可编写代码</span>' : link("programming", "编写代码", "primary", {course:course.id, question:q.id})}</div>`).join("")}
    </section>`;
  }).join("");
  layout(`<div class="learning-panel">${preview ? '<div class="preview-banner">学生视角预览 · 不显示答案与解析，不可提交</div>' : ""}<span class="eyebrow">${esc(course.teacher)} · ${esc(course.chapters.length)} 个章节 · COURSE LEARNING</span><h1>${esc(course.title)}</h1>${course.teaching_class ? `<p class="dashboard-class-label">${esc(course.teaching_class)} · ${esc(course.term || "")}</p>` : ""}<p class="muted">${esc(course.description)}</p>${chapterBlocks}<div class="actions">${preview ? link("edit-course", "返回课程编辑", "primary", {course:course.id}) : link("learning-center", "查看学习档案")}</div></div>`, `${course.title}${preview ? " · 预览" : ""}`);
  if (preview) return;
  root.querySelectorAll("form.quiz").forEach(form => {
    form.onsubmit = async event => {
      event.preventDefault();
      const button = form.querySelector('[type="submit"]');
      button.disabled = true;
      const answers = Object.fromEntries([...new FormData(form)].map(([id, value]) => [id, Number(value)]));
      try {
        const result = await post(`/api/courses/${course.id}/submissions`, {chapter_id: form.dataset.chapter, answers});
        form.querySelector(".quiz-result").innerHTML = `<div class="result"><h2>本章得分 ${result.score} 分</h2><p>答对 ${result.correct_count}/${result.total} 题，已保存到个人学习记录。</p>${result.details.map((d, i) => `<p>${d.correct ? "✓" : "✗"} ${i + 1}. ${esc(d.text)} · 正确答案：${esc(d.options[d.answer])}${d.explanation ? `<small class="answer-explanation">${esc(d.explanation)}</small>` : ""}</p>`).join("")}</div>`;
        form.querySelector(".quiz-result").insertAdjacentHTML("beforeend", `<div class="actions">${analysisLink(link, result.id)}</div>`);
        form.querySelector(".quiz-result").scrollIntoView({behavior:"smooth", block:"nearest"});
      } catch (error) { tell(error.message); }
      finally { button.disabled = false; }
    };
  });
}

function audienceFields(selected) {
  return `<fieldset class="audience-fields"><legend>教学班学生专业（单选）</legend><p class="muted">一个教学班对应一个学生专业。同一课程可为不同专业分别开班，教师可以跨专业授课。</p>${selected.length > 1 ? '<p class="muted">请为这个原共享课程指定一个专业，其他专业可另建教学班。</p>' : ""}<div class="audience-options">${majors.map(major => `<label><input type="radio" name="major_ids" value="${esc(major.id)}" ${selected.length === 1 && selected[0] === major.id ? "checked" : ""} required><span>${esc(major.name)}</span></label>`).join("")}</div></fieldset>`;
}

async function newCoursePage() {
  if (user.role !== "teacher") throw new Error("只有教师可以创建课程");
  layout(`<div class="page-heading"><div><span class="eyebrow">CREATE A COURSE</span><h1>把知识，分享给更多学生</h1><p class="muted">填写课程与教学班信息，再添加章节、讲义与练习。同一课程的不同班使用相同课程代码归组。</p></div></div><div class="form-card wide-form"><form id="new-course"><label>课程名称<input name="title" maxlength="80" required value="${esc(params.get("title") || "")}" placeholder="例如：深度学习基础"></label><div class="editor-fields"><label>课程代码<input name="course_code" maxlength="40" value="${esc(params.get("subject") || "")}" placeholder="例如 PY101，同一课程的不同班使用相同代码"></label><label>教学班<input name="teaching_class" maxlength="60" required placeholder="例如：2026级 02班"></label><label>学期<input name="term" maxlength="40" value="${esc(params.get("term") || "")}" placeholder="例如：2026年秋季"></label></div><label>课程简介<textarea name="description" maxlength="300" placeholder="介绍课程目标、适合对象与主要内容"></textarea></label>${audienceFields([])}<div class="actions"><button class="primary" type="submit">创建课程草稿 ${icon("arrow")}</button></div><div id="created" role="status"></div></form></div>`, "创建课程");
  bindForm("#new-course", async (data,form) => {
    const major_ids = new FormData(form).getAll("major_ids");
    if (!major_ids.length) throw new Error("请选择本教学班的学生专业");
    const course = await post("/api/courses", {...data, major_ids});
    root.querySelector("#created").innerHTML = `<div class="result"><h3>课程草稿已创建</h3><p>${esc(course.title)}</p>${link("edit-course", "继续添加章节与练习", "primary", {course:course.id})}</div>`;
    form.querySelector('[type="submit"]').hidden = true;
  });
}

async function editCoursePage() {
  if (user.role !== "teacher") throw new Error("只有教师可以编辑课程");
  const id = encodeURIComponent(params.get("course") || "");
  const [course, jobs] = await Promise.all([api(`/api/courses/${id}`), api("/api/jobs")]);
  if (course.teacher_id !== user.id) throw new Error("只能编辑自己创建的课程");
  const archived = course.status === "archived";
  const sectionKeys = ["settings", "content", "questions"];
  let section = sectionKeys.includes(params.get("section")) ? params.get("section") : "content";
  const chapters = course.chapters || [];
  const chapterName = cid => chapters.find(c => c.id === cid)?.title || "未分章节";
  const chapterOptionsFor = selected => chapters.map(c => `<option value="${esc(c.id)}" ${c.id === selected ? "selected" : ""}>${esc(c.title)}</option>`).join("");
  const chapterOptions = chapters.map(c => `<option value="${esc(c.id)}">${esc(c.title)}</option>`).join("");
  const kpName = kpid => (course.knowledge_points || []).find(kp => kp.id === kpid)?.name || "";
  const activeQuestions = course.questions.filter(q => q.status !== "retired");
  const retiredQuestions = course.questions.filter(q => q.status === "retired");
  const activeProgramming = (course.programming_questions || []).filter(q => q.status !== "retired");
  const chapterItemCount = ch => course.questions.filter(q => q.chapter_id === ch.id).length
    + (course.programming_questions || []).filter(q => q.chapter_id === ch.id).length
    + course.resources.filter(r => r.chapter_id === ch.id).length;
  const statusText = archived ? "已归档" : course.status === "published" ? "已发布" : "草稿";
  const chapterRows = chapters.map((ch, index) => {
    const qn = [...course.questions, ...(course.programming_questions || [])].filter(q => q.chapter_id === ch.id).length;
    const rn = course.resources.filter(r => r.chapter_id === ch.id).length;
    return `<div class="editor-item chapter-item" data-chapter="${esc(ch.id)}">
      <div class="editor-item-head"><div><b>${esc(ch.title)}</b><small>${qn} 道练习 · ${rn} 份资源</small></div>
      <div class="item-actions"><button class="ghost small" type="button" data-move-chapter="${esc(ch.id)}" data-direction="-1" ${index === 0 ? "disabled" : ""}>上移</button><button class="ghost small" type="button" data-move-chapter="${esc(ch.id)}" data-direction="1" ${index === chapters.length - 1 ? "disabled" : ""}>下移</button><button class="ghost small danger" type="button" data-delete-chapter="${esc(ch.id)}" data-items="${chapterItemCount(ch)}">删除</button></div></div>
      <form class="rename-chapter" data-chapter="${esc(ch.id)}"><input name="title" maxlength="50" value="${esc(ch.title)}" aria-label="章节名称" ${archived ? "disabled" : ""}><button class="ghost" type="submit" ${archived ? "disabled" : ""}>重命名</button></form>
    </div>`;
  }).join("");
  const resourceRows = course.resources.map(r => {
    const kinds = [...new Set(["课件", "视频", "文档", "图文讲义", "实验讲义", r.kind || "课件"])];
    return `<div class="editor-item"><details><summary><b>${esc(r.name)}</b><small>${esc(r.kind)} · ${esc(chapterName(r.chapter_id))}${r.job_id ? " · 已关联课件" : ""}</small></summary>
      <form class="edit-resource" data-resource="${esc(r.id)}">
        <div class="editor-fields"><label>名称<input name="name" maxlength="100" value="${esc(r.name)}" required></label>
        <label>类型<select name="kind">${kinds.map(k => `<option ${k === r.kind ? "selected" : ""}>${esc(k)}</option>`).join("")}</select></label>
        <label>章节<select name="chapter_id">${chapterOptionsFor(r.chapter_id)}</select></label></div>
        <label>讲义正文<textarea name="content" maxlength="8000" placeholder="可选：按换行分段展示给学生">${esc(r.content || "")}</textarea></label>
        <label>代码示例<textarea name="code" maxlength="8000" placeholder="可选：示例代码">${esc(r.code || "")}</textarea></label>
        <div class="actions"><button class="secondary" type="submit">保存资源</button><button class="ghost danger" type="button" data-delete-resource="${esc(r.id)}">删除资源</button></div>
      </form></details></div>`;
  }).join("");
  const questionForm = q => `<form class="edit-question" data-question="${esc(q.id)}">
    <label>题目<textarea name="text" maxlength="3000" required>${esc(q.text)}</textarea></label>
    <div class="editor-fields">${q.options.map((option,i) => `<label>选项 ${String.fromCharCode(65+i)}<input name="option-${i}" maxlength="1000" value="${esc(option)}" required></label>`).join("")}</div>
    <div class="editor-fields"><label>正确答案<select name="answer">${q.options.map((option, oi) => `<option value="${oi}" ${q.answer === oi ? "selected" : ""}>${String.fromCharCode(65 + oi)}. ${esc(option)}</option>`).join("")}</select></label>
    <label>章节<select name="chapter_id">${chapterOptionsFor(q.chapter_id)}</select></label>
    <label>知识点<input name="knowledge_point" maxlength="50" value="${esc(kpName(q.knowledge_point_id))}" placeholder="可留空"></label></div>
    <label>解析<textarea name="explanation" maxlength="3000">${esc(q.explanation || "")}</textarea></label>
    <div class="actions"><button class="secondary" type="submit">保存题目</button><button class="ghost danger" type="button" data-retire-question="${esc(q.id)}">停用</button></div>
  </form>`;
  const questionRows = activeQuestions.map((q, index) => `<div class="editor-item" data-bank-chapter="${esc(q.chapter_id)}"><details><summary><b>${index + 1}. ${esc(q.text)}</b><small>答案：${esc(q.options[q.answer])} · ${esc(chapterName(q.chapter_id))}${q.knowledge_point_id ? ` · ${esc(kpName(q.knowledge_point_id))}` : ""}</small></summary>
    ${questionForm(q)}
    <div class="item-actions"><button class="ghost small" type="button" data-move-question="${esc(q.id)}" data-direction="-1" ${index === 0 ? "disabled" : ""}>上移</button><button class="ghost small" type="button" data-move-question="${esc(q.id)}" data-direction="1" ${index === activeQuestions.length - 1 ? "disabled" : ""}>下移</button></div>
  </details></div>`).join("");
  const retiredRows = retiredQuestions.length ? `<details class="retired-list"><summary>已停用题目 ${retiredQuestions.length} 道（学生端不显示）</summary>${retiredQuestions.map(q => `<div class="editor-item retired-item"><div class="editor-item-head"><div><b>${esc(q.text)}</b><small>已停用 · ${esc(chapterName(q.chapter_id))}</small></div><button class="ghost small" type="button" data-restore-question="${esc(q.id)}">恢复题目</button></div>${questionForm(q)}</div>`).join("")}</details>` : "";
  layout(`${workspaceNav(context(), course, section)}<div class="course-editor-layout"><div class="page-heading"><div><span class="eyebrow">COURSE WORKSPACE</span><h1 id="course-panel-title"></h1><p class="muted" id="course-panel-description"></p></div><div class="actions">${link("course", "学生视角预览", "secondary", {course:course.id, preview:"1"})}<button class="ghost" id="refresh">${icon("refresh")}刷新</button></div></div>
    ${archived ? '<p class="warning">课程已归档，学生无法访问；恢复后可继续编辑。</p>' : ""}
    <div id="course-action-result" role="status"></div>
    <div data-course-panel="settings"><section class="editor"><h2>课程信息</h2><form id="course-settings"><div class="editor-fields"><label>课程名称<input name="title" maxlength="80" value="${esc(course.title)}" required></label><label>分类<input name="category" maxlength="40" value="${esc(course.category || "")}" placeholder="python / deep-learning"></label><label>难度<input name="level" maxlength="40" value="${esc(course.level || "")}" placeholder="零基础 / 进阶"></label><label>学时<input name="duration" maxlength="40" value="${esc(course.duration || "")}" placeholder="约 3 小时"></label></div><div class="editor-fields"><label>课程代码<input name="course_code" maxlength="40" value="${esc(course.course_code || "")}" placeholder="同一课程的不同班使用相同代码"></label><label>教学班<input name="teaching_class" maxlength="60" value="${esc(course.teaching_class || "")}" placeholder="例如：2026级 01班"></label><label>学期<input name="term" maxlength="40" value="${esc(course.term || "")}" placeholder="例如：2026年秋季"></label></div><label>课程简介<textarea name="description" maxlength="300">${esc(course.description || "")}</textarea></label><div class="actions"><button class="secondary" type="submit">保存课程信息</button></div></form></section>
    <section class="editor"><h2>课程面向专业</h2><form id="audience-form">${audienceFields(course.major_ids)}<div class="actions"><button class="secondary" type="submit">保存授课范围</button></div></form></section>
    <details class="course-maintenance"><summary>更多课程操作：复制与归档</summary><div class="actions"><button class="ghost" id="duplicate-course" ${archived ? "disabled" : ""}>复制课程</button>${archived ? '<button class="primary" id="restore-course">恢复课程</button>' : '<button class="ghost danger" id="archive-course">归档课程</button>'}</div></details></div>
    <div data-course-panel="content"><section class="editor"><h2>课程章节</h2><p class="muted">支持排序与删除；删除含内容的章节时，可把题目和资源迁移到其他章节。</p><div class="editor-list">${chapterRows || "暂无章节"}</div><form id="chapter-form" class="inline-form"><input name="title" maxlength="50" placeholder="新章节名称，如：第三章 函数" required><button class="secondary" type="submit">新增章节</button></form></section>
    <section class="editor"><h2>学习资源</h2><div class="editor-list">${resourceRows || "暂无资源"}</div><details class="create-content"><summary>${icon("plus")}添加学习资源</summary><form id="resource-form"><div class="resource-fields"><label>章节<select name="chapter_id">${chapterOptions}</select></label><label>资源名称<input name="name" maxlength="100" required></label><label>类型<select name="kind"><option>课件</option><option>视频</option><option>文档</option><option>图文讲义</option><option>实验讲义</option></select></label><label>关联提取结果<select name="job_id"><option value="">不关联（仅登记资源说明）</option>${jobs.filter(j => j.status === "completed").map(j => `<option value="${esc(j.id)}">${esc(j.filename)}</option>`).join("")}</select></label></div><label>讲义正文（可选）<textarea name="content" maxlength="8000" placeholder="按换行分段展示给学生"></textarea></label><label>代码示例（可选）<textarea name="code" maxlength="8000"></textarea></label><div class="actions"><button class="secondary" type="submit">添加学习资源</button></div></form></details></section></div>
    <div data-course-panel="questions"><div class="workflow-note"><div><b>题库是课程练习和作业的共同来源</b><p>可手动创建单选题、添加 Python 编程题，或审核 AI 生成的题目后导入。</p></div><div class="actions">${link("teaching-ai", "AI 出题并导入", "secondary", {course:course.id})}<a class="primary" href="${esc(href("assignment-builder", {course:course.id}))}">用题库创建作业</a></div></div>
    <section class="editor"><div class="section-title"><div><h2>单选题库</h2><p class="muted">${activeQuestions.length} 道可用题目</p></div><button class="secondary" id="open-question-create">${icon("plus")}手动创建题目</button></div><details class="create-content" id="manual-question-create"><summary>手动创建单选题</summary><form id="question-form"><label>章节<select name="chapter_id">${chapterOptions}</select></label><label>题目<textarea name="text" maxlength="3000" required></textarea></label><div class="editor-fields">${["A","B","C","D"].map((letter,i) => `<label>选项 ${letter}<input name="option-${i}" maxlength="1000" required></label>`).join("")}</div><label>正确答案<select name="answer"><option value="0">第 1 项</option><option value="1">第 2 项</option><option value="2">第 3 项</option><option value="3">第 4 项</option></select></label><label>答案解析（可选）<textarea name="explanation" maxlength="2000" placeholder="解释正确答案与易错点，提交后向学生展示"></textarea></label><label>知识点（可选）<input name="knowledge_point" maxlength="50" placeholder="例如：字符串操作"></label><div class="actions"><button class="secondary" type="submit">添加练习题</button></div></form></details><div class="question-bank-filters"><label>筛选章节<select id="course-bank-chapter"><option value="all">全部章节</option>${chapterOptions}</select></label><label>搜索题目<input type="search" id="course-bank-search" placeholder="输入题干关键词"></label></div><div class="editor-list">${questionRows || '<div class="empty">暂无单选题，可点击上方按钮创建或使用 AI 出题。</div>'}</div>${retiredRows}</section>
    <details class="programming-bank"><summary>${icon("book")}Python 编程题库 · ${activeProgramming.length} 道可用题目</summary>${teacherProgramming(course)}</details></div>
    <div class="course-publish-bar"><div><b>${course.status === "published" ? "课程已对学生开放" : "准备好后，发布课程"}</b><p class="muted">${chapters.length} 个章节 · ${course.resources.length} 份资料 · ${activeQuestions.length + activeProgramming.length} 道题目</p></div><button class="primary" id="publish" ${course.status === "published" || archived ? "disabled" : ""}>${course.status === "published" ? "课程已发布" : "确认并发布课程"}</button></div><p class="muted">发布前至少需要一份资源和一道练习题。</p>
    <dialog id="chapter-delete-dialog" class="platform-dialog"><h2>删除章节</h2><p id="chapter-delete-message"></p><label>把章节内内容移动到<select id="chapter-move-target"></select></label><label class="check-row"><input type="checkbox" id="chapter-cascade"> 直接删除章节内全部题目和资源</label><div class="actions"><button class="ghost" type="button" id="chapter-delete-cancel">取消</button><button class="danger" type="button" id="chapter-delete-confirm">确认删除</button></div></dialog>
  </div>`, "课程管理");
  function showSection(key) {
    section = key; params.set("section", key);
    const url = new URL(location.href); url.searchParams.set("section", key); history.replaceState(null, "", url);
    root.querySelectorAll("[data-course-panel]").forEach(panel => { panel.hidden = panel.dataset.coursePanel !== key; });
    root.querySelectorAll("[data-workspace-tab]").forEach(tab => { const active = tab.dataset.workspaceTab === key; tab.classList.toggle("active", active); if (active) tab.setAttribute("aria-current", "page"); else tab.removeAttribute("aria-current"); });
    const labels = {settings:["课程设置", "维护课程信息与学生专业范围。"], content:["章节与学习资料", "按章节整理课程内容，添加学生可阅读的讲义与视频课件。"], questions:["课程题库", "集中维护单选题和编程题，再将具体题目选入作业。"]};
    root.querySelector("#course-panel-title").textContent = labels[key][0];
    root.querySelector("#course-panel-description").textContent = labels[key][1];
  }
  root.querySelectorAll("[data-workspace-tab]").forEach(tab => { if (sectionKeys.includes(tab.dataset.workspaceTab)) tab.onclick = event => { event.preventDefault(); showSection(tab.dataset.workspaceTab); }; });
  showSection(section);
  root.querySelector("#open-question-create").onclick = () => { const panel = root.querySelector("#manual-question-create"); panel.open = true; panel.scrollIntoView({behavior:"smooth", block:"start"}); };
  const filterBank = () => { const chapter = root.querySelector("#course-bank-chapter").value; const term = root.querySelector("#course-bank-search").value.trim().toLowerCase(); root.querySelectorAll("[data-bank-chapter]").forEach(row => { row.hidden = (chapter !== "all" && row.dataset.bankChapter !== chapter) || !row.textContent.toLowerCase().includes(term); }); };
  root.querySelector("#course-bank-chapter").onchange = filterBank;
  root.querySelector("#course-bank-search").oninput = filterBank;
  root.querySelector("#refresh").onclick = () => editCoursePage().catch(error => tell(error.message));
  root.querySelector("#duplicate-course").onclick = async () => {
    try {
      const copy = await post(`/api/courses/${id}/duplicate`);
      tell(`已复制为《${copy.title}》`);
      location.href = href("edit-course", {course:copy.id});
    } catch (error) { tell(error.message); }
  };
  const archive = root.querySelector("#archive-course");
  if (archive) archive.onclick = async () => {
    if (!confirm("归档后学生将无法访问该课程，历史提交仍会保留。继续？")) return;
    try { await del(`/api/courses/${id}`); tell("课程已归档"); location.href = href("dashboard"); }
    catch (error) { tell(error.message); }
  };
  const restore = root.querySelector("#restore-course");
  if (restore) restore.onclick = async () => {
    try { await post(`/api/courses/${id}/restore`); await editCoursePage(); tell("课程已恢复"); }
    catch (error) { tell(error.message); }
  };
  if (archived) {
    root.querySelectorAll(".editor input, .editor textarea, .editor select, .editor button").forEach(node => { node.disabled = true; });
    return;
  }
  bindTeacherProgramming(context(), course, editCoursePage);
  bindForm("#course-settings", async data => {
    await patch(`/api/courses/${id}`, data);
    await editCoursePage(); tell("课程信息已保存");
  });
  bindForm("#audience-form", async (data, form) => {
    const major_ids = new FormData(form).getAll("major_ids");
    if (!major_ids.length) throw new Error("请选择本教学班的学生专业");
    await post(`/api/courses/${id}/audience`, {major_ids});
    tell("课程授课范围已更新，学生刷新后按新范围访问；历史作答保留");
  });
  bindForm("#resource-form", async data => {
    await post(`/api/courses/${id}/resources`, {...data, job_id:data.job_id || null});
    await editCoursePage(); tell("资源已加入课程");
  });
  bindForm("#question-form", async data => {
    await post(`/api/courses/${id}/questions`, {...data, options:[0,1,2,3].map(i => data[`option-${i}`].trim()), answer:Number(data.answer), knowledge_point:data.knowledge_point || ""});
    await editCoursePage(); tell("练习题已加入课程");
  });
  bindForm("#chapter-form", async data => {
    await post(`/api/courses/${id}/chapters`, data);
    await editCoursePage(); tell("章节已添加");
  });
  root.querySelectorAll("form.rename-chapter").forEach(form => {
    form.onsubmit = async event => {
      event.preventDefault();
      try {
        await post(`/api/courses/${id}/chapters/${encodeURIComponent(form.dataset.chapter)}`, {title:new FormData(form).get("title")});
        await editCoursePage(); tell("章节已重命名");
      } catch (error) { tell(error.message); }
    };
  });
  root.querySelectorAll("[data-move-chapter]").forEach(button => {
    button.onclick = async () => {
      const ids = chapters.map(chapter => chapter.id);
      const index = ids.indexOf(button.dataset.moveChapter);
      const target = index + Number(button.dataset.direction);
      if (index < 0 || target < 0 || target >= ids.length) return;
      [ids[index], ids[target]] = [ids[target], ids[index]];
      try { await post(`/api/courses/${id}/chapters/reorder`, {chapter_ids:ids}); await editCoursePage(); tell("章节顺序已更新"); }
      catch (error) { tell(error.message); }
    };
  });
  const deleteDialog = root.querySelector("#chapter-delete-dialog");
  let deleteChapterId = "";
  root.querySelectorAll("[data-delete-chapter]").forEach(button => {
    button.onclick = () => {
      deleteChapterId = button.dataset.deleteChapter;
      const items = Number(button.dataset.items || 0);
      const chapter = chapters.find(item => item.id === deleteChapterId);
      const others = chapters.filter(item => item.id !== deleteChapterId);
      root.querySelector("#chapter-delete-message").textContent = items
        ? `《${chapter.title}》内有 ${items} 项内容。请选择迁移到其他章节，或确认级联删除。`
        : `确认删除章节《${chapter.title}》？`;
      root.querySelector("#chapter-move-target").innerHTML = others.map(item => `<option value="${esc(item.id)}">${esc(item.title)}</option>`).join("");
      root.querySelector("#chapter-move-target").disabled = !items;
      root.querySelector("#chapter-cascade").disabled = !items;
      root.querySelector("#chapter-cascade").checked = false;
      deleteDialog.showModal();
    };
  });
  root.querySelector("#chapter-delete-cancel").onclick = () => deleteDialog.close();
  root.querySelector("#chapter-delete-confirm").onclick = async () => {
    const cascade = root.querySelector("#chapter-cascade").checked;
    const move_to = root.querySelector("#chapter-move-target").value;
    try {
      await del(`/api/courses/${id}/chapters/${encodeURIComponent(deleteChapterId)}`, cascade ? {cascade:true} : {move_to});
      deleteDialog.close(); await editCoursePage(); tell("章节已删除");
    } catch (error) { tell(error.message); }
  };
  root.querySelectorAll("form.edit-resource").forEach(form => {
    form.onsubmit = async event => {
      event.preventDefault();
      try { await patch(`/api/courses/${id}/resources/${encodeURIComponent(form.dataset.resource)}`, Object.fromEntries(new FormData(form))); await editCoursePage(); tell("资源已保存"); }
      catch (error) { tell(error.message); }
    };
  });
  root.querySelectorAll("[data-delete-resource]").forEach(button => {
    button.onclick = async () => {
      if (!confirm("删除该学习资源？")) return;
      try { await del(`/api/courses/${id}/resources/${encodeURIComponent(button.dataset.deleteResource)}`); await editCoursePage(); tell("资源已删除"); }
      catch (error) { tell(error.message); }
    };
  });
  root.querySelectorAll("form.edit-question").forEach(form => {
    form.onsubmit = async event => {
      event.preventDefault();
      const data = Object.fromEntries(new FormData(form));
      try {
        await patch(`/api/courses/${id}/questions/${encodeURIComponent(form.dataset.question)}`,
          {...data, options:[...form.querySelectorAll('[name^="option-"]')].map(input => input.value.trim()), answer:Number(data.answer), knowledge_point:data.knowledge_point || ""});
        await editCoursePage(); tell("题目已保存");
      } catch (error) { tell(error.message); }
    };
  });
  root.querySelectorAll("[data-retire-question]").forEach(button => {
    button.onclick = async () => {
      if (!confirm("停用后学生端不再显示该题，历史作答仍保留。继续？")) return;
      try { await del(`/api/courses/${id}/questions/${encodeURIComponent(button.dataset.retireQuestion)}`); await editCoursePage(); tell("题目已停用"); }
      catch (error) { tell(error.message); }
    };
  });
  root.querySelectorAll("[data-restore-question]").forEach(button => {
    button.onclick = async () => {
      try { await patch(`/api/courses/${id}/questions/${encodeURIComponent(button.dataset.restoreQuestion)}`, {status:"active"}); await editCoursePage(); tell("题目已恢复"); }
      catch (error) { tell(error.message); }
    };
  });
  root.querySelectorAll("[data-move-question]").forEach(button => {
    button.onclick = async () => {
      const ids = course.questions.map(question => question.id);
      const index = ids.indexOf(button.dataset.moveQuestion);
      const target = index + Number(button.dataset.direction);
      if (index < 0 || target < 0 || target >= ids.length) return;
      [ids[index], ids[target]] = [ids[target], ids[index]];
      try { await post(`/api/courses/${id}/questions/reorder`, {question_ids:ids}); await editCoursePage(); tell("题目顺序已更新"); }
      catch (error) { tell(error.message); }
    };
  });
  root.querySelector("#publish").onclick = async () => {
    try { await post(`/api/courses/${id}/publish`); await editCoursePage(); tell("课程已发布，学生刷新工作台即可看到"); }
    catch (error) { tell(error.message); }
  };
}

async function recordsPage() {
  if (user.role === "teacher") return teacherAnalyticsPage();
  return learningFilePage(context(), "records");
}

async function teacherAnalyticsPage() { return analyticsPage(context()); }
async function profilePage() {
  if (user.role !== "student") throw new Error("请使用学生账号查看本人画像");
  return learningFilePage(context(), "overview");
}
async function reviewPage() {
  if (user.role !== "student") throw new Error("请使用学生账号打开错题与复习");
  return reviewCenter(context(), page === "wrong" ? "all" : undefined);
}

async function accountPage() {
  layout(`<div class="form-card"><span class="eyebrow">ACCOUNT SETTINGS</span><h1>账号管理</h1><p>${esc(user.name)} · ${roleName(user.role)}</p><p class="muted">账号：${esc(user.username)}</p><form id="password-form"><label>原密码<input type="password" name="old_password" autocomplete="current-password" required></label><label>新密码<input type="password" name="new_password" minlength="6" maxlength="128" autocomplete="new-password" required></label><label>确认新密码<input type="password" name="confirm" minlength="6" maxlength="128" autocomplete="new-password" required></label><div class="actions"><button class="primary" type="submit">保存新密码</button></div></form></div>`, "账号管理");
  bindForm("#password-form", async (data, form) => {
    if (data.new_password !== data.confirm) throw new Error("两次输入的新密码不一致");
    await post("/api/auth/password", data); form.reset(); tell("密码已保存，下次请使用新密码登录");
  });
}

async function render() {
  [config, majors] = await Promise.all([api("/api/config"), api("/api/majors")]);
  if (["course_audiences", "teacher_content", "assignments", "gradebook", "announcements", "teacher_workspace", "teaching_classes", "professional_classes", "assignment_practice_progress", "live_quiz"]
      .some(key => !config.features?.[key])) {
    throw new Error("后台仍在运行旧版本。请在原终端按 Ctrl+C 停止服务，重新运行 run.py，再打开新终端显示的地址。");
  }
  if (!account || page === "login") return loginPage();
  try { user = await api("/api/auth/me"); }
  catch (error) { if (error.status === 401) return loginPage(error.message); throw error; }
  if (user.role === "student") {
    try { const inbox = await api("/api/announcements"); unreadAnnouncements = inbox.unread_count || 0; }
    catch { unreadAnnouncements = 0; }
  }
  if (page === "video") {
    layout("", "视频学习工作台");
    root.querySelector(".shell").remove();
    root.append(document.getElementById("video-template").content.cloneNode(true));
    mountVideo(config, user, fileUrl);
    initializeXfyunCredentials(config, user);
    initializeApiKey(config, user).catch(error => tell(error.message));
    return;
  }
  const views = {"live-quiz":() => liveQuizPage(context()), analysis:() => analysisPage(context(), params.get("record"), params.get("start") === "1"), "ai-quiz":() => quizPage(context(), params.get("job"), params.get("run"), params.get("wait")), programming:() => programmingPage(context(), params.get("course"), params.get("question")), dashboard, teaching:() => teachingCoursesPage(context()), "learning-center":() => learningFilePage(context()), course:learningPage, "new-course":newCoursePage, "edit-course":editCoursePage, records:recordsPage, wrong:reviewPage, review:reviewPage, "review-center":reviewPage, profile:profilePage, account:accountPage, assignments:() => assignmentsPage(context()), "assignment-builder":() => assignmentBuilderPage(context()), "teaching-ai":() => teachingAiPage(context()), assignment:() => assignmentDetailPage(context()), gradebook:() => gradebookPage(context()), "student-detail":() => studentDetailPage(context()), announcements:() => announcementsPage(context())};
  if (!views[page]) throw new Error("页面不存在");
  await views[page]();
}
render().catch(error => layout(`<div class="form-card"><h2>页面暂时无法打开</h2><p>${esc(error.message)}</p><div class="actions"><a class="secondary" href="/" target="_blank" rel="noopener">打开登录页 ↗</a><button class="ghost" id="retry">重试</button></div></div>`, "页面提示"));
root.addEventListener("click", event => { if (event.target.id === "retry") location.reload(); });
// Reconcile progress when returning from another tab, without disrupting open forms.
window.addEventListener("focus", () => {
  if (!user) return;
  if (page === "dashboard") dashboard().catch(e => tell(e.message));
  if (page === "records" && user.role === "teacher") recordsPage().catch(e => tell(e.message));
});
