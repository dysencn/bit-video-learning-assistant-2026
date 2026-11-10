import {teacherProgramming, bindTeacherProgramming, programmingPage, programmingRecord} from "/static/programming.js";
import {quizPage} from "/static/ai-quiz.js";
import {analysisLink, analysisPage} from "/static/analysis.js";
import { esc, icon, number, radarCard } from "/static/ui.js";
import { analyticsPage, personalProfilePage, reviewCenter } from "/static/learning-pages.js";
import { mountVideo } from "/static/video.js";
import { initializeApiKey } from "/static/ai-key.js";
import { initializeXfyunCredentials } from "/static/xfyun-key.js";

const root = document.getElementById("platform");
const params = new URLSearchParams(location.search);
let config, user;
let majors = [];
const majorName = id => majors.find(major => major.id === id)?.name || "未设置专业";
const context = () => ({api, post, layout, root, tell, link, user, config, recordList, majorName});
let account = params.get("account") || "";
let page = params.get("page") || (account ? "dashboard" : "login");
const roleName = role => role === "teacher" ? "教师" : "学生";
const date = value => new Date(value).toLocaleString("zh-CN");
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
function tell(message) {
  const node = root.querySelector(".notice");
  node.textContent = message;
  node.hidden = false;
  clearTimeout(tell.timer);
  tell.timer = setTimeout(() => { node.hidden = true; }, 5000);
}
function nav() {
  const current = view => page === view || (view === "review-center" && ["review", "wrong"].includes(page));
  const item = (view,label,graphic) => link(view, `${icon(graphic)}<span>${label}</span>`, `nav-link${current(view) ? " active" : ""}`);
  return `<header class="nav"><a class="brand" href="${esc(user ? href("dashboard") : "/")}"><span class="brand-mark">${icon("layers")}</span><span>知学<small>LEARN WITH PURPOSE</small></span></a>
    ${user ? `<nav class="nav-links" aria-label="主导航">${item("dashboard", "工作台", "home")}${item("video", "视频学习", "video")}${user.role === "student" ? item("review-center", "错题与复习", "review") + item("profile", "学习画像", "chart") : ""}${item("records", user.role === "teacher" ? "学习分析" : "学习记录", "book")}${item("account", "账号", "user")}</nav><div class="nav-account"><span class="avatar small">${esc(user.name.slice(0,1))}</span><span class="identity">${esc(user.name)}<small>${user.role === "teacher" ? "教师 · 跨专业授课" : esc(majorName(user.major_id))}</small></span><button class="ghost" id="logout">退出</button></div>` : '<span class="identity">课程、练习与成长，连接在一起。</span>'}</header><div class="notice" role="status" hidden></div>`;
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
  return `<div class="record-list">${items.map(r => `<div class="record-row"><div><b>${teacher ? esc(r.student) + " · " : ""}${esc(r.course_name || r.course_id)}</b><small>${r.demo ? "演示记录 · " : ""}${date(r.submitted_at)} · ${r.type === "ai_quiz" ? "AI 自测 · " : ""}${r.type === "python" ? `Python 编程 · 用例通过 ${r.passed_cases}/${r.total_cases}` : `答对 ${r.correct_count}/${r.total} 题`}${r.chapter_title ? ` · ${esc(r.chapter_title)}` : ""}</small>${r.type === "python" ? programmingRecord(r) : ""}</div><div class="record-actions"><strong class="score">${r.score} 分</strong>${analysisLink(link, r.id)}</div></div>`).join("")}</div>`;
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
  const [courses, logs, extra] = await Promise.all([api("/api/courses"), api("/api/learning-records"),
    api(teacher ? "/api/teacher/analytics" : "/api/student/profile")]);
  const wrong = teacher ? [] : await api("/api/wrong-questions");
  const due = wrong.filter(item => item.status !== "mastered" && new Date(item.next_review_at) <= new Date()).length;
  const stats = teacher ? [[courses.length,"我的课程"],[extra.overview.student_count,"面向学生"],
    [extra.overview.submission_count,"正式提交"],[number(extra.overview.average_score),"平均成绩"]]
    : [[courses.length,"可学课程"],[logs.length,"正式练习"],[due,"今日待复习"],[number(extra.profile.average_score),"平均成绩"]];
  const courseCards = courses.map(course => {
    const entries = logs.filter(record => record.course_id === course.id);
    const chapterIds = new Set(entries.flatMap(record => [record.chapter_id, ...(record.details || []).map(detail => detail.chapter_id)]).filter(Boolean));
    const completed = course.chapters.filter(chapter => chapterIds.has(chapter.id)).length;
    const category = course.category === "deep-learning" ? "deep-learning" : "python";
    return `<article class="course-card"><div class="course-cover"><img src="/static/course-${category}.svg" alt="" loading="lazy"><span class="tag ${teacher ? course.status : "neutral"}">${teacher ? course.status === "published" ? "已发布" : "草稿" : course.level || "课程学习"}</span></div><div class="course-content"><div class="course-audience">${course.major_ids.length === majors.length ? '<span class="course-kind">所有专业</span>' : course.major_ids.map(mid => `<span class="course-kind">${esc(majorName(mid))}</span>`).join("")}${course.demo ? '<span class="demo-label">示例课程</span>' : ""}</div><h3>${esc(course.title)}</h3><p>${esc(course.description || "打开课程，开始你的下一段学习。")}</p><span class="meta">${esc(course.teacher)} · ${course.chapters.length} 章 · ${course.questions.length + (course.programming_questions || []).length} 道练习</span>${teacher ? `<p class="course-progress-label">${course.resources.length} 份学习资源 · ${entries.length} 次提交</p>` : `<div class="course-progress-label"><span>已练习 ${completed} / ${course.chapters.length} 章</span><span>${Math.round(completed/Math.max(1,course.chapters.length)*100)}%</span></div><progress class="course-progress" max="${Math.max(1,course.chapters.length)}" value="${completed}" aria-label="${esc(course.title)}章节练习进度"></progress>`}${link(teacher ? "edit-course" : "course", teacher ? "管理课程" : "继续学习", "course-link", {course:course.id})}</div></article>`;
  }).join("");
  layout(`<div class="hero dashboard-hero"><div><span class="eyebrow">${teacher ? "TEACH WITH INSIGHT" : "A LITTLE PROGRESS, EVERY DAY"}</span><h1>${esc(user.name)}，${teacher ? "让教学更有方向。" : "今天也向前一步。"}</h1><p class="muted">${teacher ? "跨专业发布课程，用真实练习反馈看见每位学生的成长。" : `${esc(majorName(user.major_id))} · 从一节课程、一份练习开始，把知识变成自己的能力。`}</p><div class="hero-actions">${teacher ? link("new-course", `${icon("plus")}创建课程`, "primary") : link(due ? "review-center" : "profile", `${icon(due ? "review" : "chart")}${due ? `复习 ${due} 道到期错题` : "查看我的学习画像"}`, "primary")}${link(teacher ? "records" : "video", teacher ? "查看学习分析" : "整理课程视频")}</div></div><div class="hero-art" aria-hidden="true">${icon(teacher ? "chart" : "book")}<span class="art-orbit orbit-one"></span><span class="art-orbit orbit-two"></span><span class="art-caption">${teacher ? "每一次反馈都有价值" : "学有所获，学有所长"}</span></div></div><div class="dashboard-stats">${stats.map(([value,label]) => `<div><b>${value}</b><span>${label}</span></div>`).join("")}</div><section><div class="section-title"><div><span class="eyebrow">${teacher ? "MY TEACHING" : "MY COURSES"}</span><h2>${teacher ? "我的教学课程" : "为你开放的课程"}</h2></div><button class="ghost" id="refresh">${icon("refresh")}刷新</button></div><div class="course-grid">${courseCards || '<div class="empty-state">暂无课程，请联系教师发布面向你所在专业的课程。</div>'}</div></section>${!teacher ? `<div class="learning-shortcuts"><a href="${esc(href("review-center"))}">${icon("review")}<div><b>错题与复习</b><small>把薄弱知识点，变成下一次的进步</small></div>${icon("arrow")}</a><a href="${esc(href("profile"))}">${icon("chart")}<div><b>我的学习雷达</b><small>从四个维度，了解学习状态</small></div>${icon("arrow")}</a></div>` : ""}<section><div class="section-title"><div><span class="eyebrow">RECENT ACTIVITY</span><h2>${teacher ? "最近的学生提交" : "最近的学习记录"}</h2></div>${link("records", "全部记录", "text-link")}</div>${recordList(logs.slice(0,5),teacher)}</section>`,teacher ? "教师工作台" : "学生工作台");
  root.querySelector("#refresh").onclick = () => dashboard().catch(error => tell(error.message));
}

async function learningPage() {
  if (user.role !== "student") throw new Error("请使用学生账号打开学习页面");
  const course = await api(`/api/courses/${encodeURIComponent(params.get("course") || "")}`);
  const chapters = course.chapters || [];
  const chapterBlocks = chapters.map((ch, ci) => {
    const resources = course.resources.filter(r => r.chapter_id === ch.id);
    const questions = course.questions.filter(q => q.chapter_id === ch.id);
    const programming = (course.programming_questions || []).filter(q => q.chapter_id === ch.id);
    return `<section class="chapter"><div class="section-title"><div><span class="eyebrow">CHAPTER ${ci + 1}</span><h2>${esc(ch.title)}</h2></div><span class="tag">${questions.length} 道单选 · ${programming.length} 道编程</span></div>
      ${resources.length ? `<div class="resource-list">${resources.map(r => `<div class="resource"><div><b>${esc(r.name)}</b><small>${esc(r.kind)}</small></div>${r.job_id ? `<div class="actions"><a class="secondary" href="${fileUrl(`/api/jobs/${encodeURIComponent(r.job_id)}/files/pdf?preview=true`)}" target="_blank" rel="noopener">打开课件 ↗</a>${course.teacher_id === user.id ? link("video", "提取结果", "ghost", {job:r.job_id}) : ""}</div>` : '<span class="muted">资源说明</span>'}</div>`).join("")}</div>` : ""}
      ${resources.filter(r => r.content).map(r => `<details class="study-note" open><summary>${icon("book")}${esc(r.name)}</summary><div>${r.content.split("\n").map(paragraph => `<p>${esc(paragraph)}</p>`).join("")}${r.code ? `<pre><code>${esc(r.code)}</code></pre>` : ""}</div></details>`).join("")}
      ${questions.length ? `<form class="quiz" data-chapter="${esc(ch.id)}"><div class="questions">${questions.map((q, i) => `<fieldset class="question"><legend>${i + 1}. ${esc(q.text)}</legend>${q.options.map((option, oi) => `<label class="option"><input type="radio" name="${esc(q.id)}" value="${oi}" required> ${String.fromCharCode(65 + oi)}. ${esc(option)}</label>`).join("")}</fieldset>`).join("")}</div><div class="actions"><button class="primary" type="submit">提交本章练习</button></div><div class="quiz-result" role="status"></div></form>` : '<p class="muted">本章暂无单选题。</p>'}
      ${programming.map(q => `<div class="python-question-link"><div><b>${esc(q.title)}</b><small>Python 编程 · 3 秒 / 256 MB</small></div>${link("programming", "编写代码", "primary", {course:course.id, question:q.id})}</div>`).join("")}
    </section>`;
  }).join("");
  layout(`<div class="learning-panel"><span class="eyebrow">${esc(course.teacher)} · ${esc(course.chapters.length)} 个章节 · COURSE LEARNING</span><h1>${esc(course.title)}</h1><p class="muted">${esc(course.description)}</p>${chapterBlocks}<div class="actions">${link("records", "查看学习进度")}</div></div>`, course.title);
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
  return `<fieldset class="audience-fields"><legend>面向学生专业（可多选）</legend><p class="muted">教师可跨专业授课。同一门课程共享内容，各学生记录独立保存。</p><div class="audience-options">${majors.map(major => `<label><input type="checkbox" name="major_ids" value="${esc(major.id)}" ${selected.includes(major.id) ? "checked" : ""}><span>${esc(major.name)}</span></label>`).join("")}</div></fieldset>`;
}

async function newCoursePage() {
  if (user.role !== "teacher") throw new Error("只有教师可以创建课程");
  layout(`<div class="page-heading"><div><span class="eyebrow">CREATE A COURSE</span><h1>把知识，分享给更多学生</h1><p class="muted">先创建课程并选择面向专业，再添加章节、讲义与练习。</p></div></div><div class="form-card wide-form"><form id="new-course"><label>课程名称<input name="title" maxlength="80" required placeholder="例如：深度学习基础"></label><label>课程简介<textarea name="description" maxlength="300" placeholder="介绍课程目标、适合对象与主要内容"></textarea></label>${audienceFields(majors.map(major => major.id))}<div class="actions"><button class="primary" type="submit">创建课程草稿 ${icon("arrow")}</button></div><div id="created" role="status"></div></form></div>`, "创建课程");
  bindForm("#new-course", async (data,form) => {
    const major_ids = new FormData(form).getAll("major_ids");
    if (!major_ids.length) throw new Error("请至少选择一个面向的学生专业");
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
  const chapters = course.chapters || [];
  const chapterName = cid => chapters.find(c => c.id === cid)?.title || "未分章节";
  const chapterOptions = chapters.map(c => `<option value="${esc(c.id)}">${esc(c.title)}</option>`).join("");
  layout(`<div class="learning-panel"><span class="eyebrow">COURSE EDITOR</span><h1>${esc(course.title)}</h1><p class="muted">${esc(course.description)}</p><div class="actions"><span class="tag ${course.status}">${course.status === "published" ? "已发布" : "草稿"}</span>${link("video", "提取新的视频资源")}<button class="ghost" id="refresh">刷新提取结果</button></div>
    <section class="editor"><h2>课程面向专业</h2><form id="audience-form">${audienceFields(course.major_ids)}<div class="actions"><button class="secondary" type="submit">保存授课范围</button></div></form></section>
    <section class="editor"><h2>课程章节</h2><div class="editor-list">${chapters.map(ch => { const qn = [...course.questions, ...(course.programming_questions || [])].filter(q => q.chapter_id === ch.id).length, rn = course.resources.filter(r => r.chapter_id === ch.id).length; return `<div class="chapter-row"><b>${esc(ch.title)}</b><small>${qn} 道练习 · ${rn} 份资源</small></div><form class="rename-chapter" data-chapter="${esc(ch.id)}"><input name="title" maxlength="50" value="${esc(ch.title)}" aria-label="章节名称"><button class="ghost" type="submit">重命名</button></form>`; }).join("") || "暂无章节"}</div><form id="chapter-form" class="inline-form"><input name="title" maxlength="50" placeholder="新章节名称，如：第三章 函数" required><button class="secondary" type="submit">新增章节</button></form></section>
    <section class="editor"><h2>学习资源</h2><div class="editor-list">${course.resources.map(r => `<div><b>${esc(r.name)}</b><small>${esc(r.kind)} · ${esc(chapterName(r.chapter_id))}${r.job_id ? " · 已关联课件" : ""}</small></div>`).join("") || "暂无资源"}</div><form id="resource-form"><div class="resource-fields"><label>章节<select name="chapter_id">${chapterOptions}</select></label><label>资源名称<input name="name" maxlength="100" required></label><label>类型<select name="kind"><option>课件</option><option>视频</option><option>文档</option></select></label><label>关联提取结果<select name="job_id"><option value="">不关联（仅登记资源说明）</option>${jobs.filter(j => j.status === "completed").map(j => `<option value="${esc(j.id)}">${esc(j.filename)}</option>`).join("")}</select></label></div><div class="actions"><button class="secondary" type="submit">添加学习资源</button></div></form></section>
    <section class="editor"><h2>单选练习题</h2><div class="editor-list">${course.questions.map((q,i) => `<div>${i + 1}. ${esc(q.text)} · 答案：${esc(q.options[q.answer])} · ${esc(chapterName(q.chapter_id))}${q.knowledge_point_id ? ` · 知识点：${esc((course.knowledge_points || []).find(kp => kp.id === q.knowledge_point_id)?.name || "")}` : ""}</div>`).join("") || "暂无练习题"}</div><form id="question-form"><label>章节<select name="chapter_id">${chapterOptions}</select></label><label>题目<input name="text" maxlength="300" required></label><label>选项（2–4 项，用 | 分隔）<input name="options" placeholder="选项 A | 选项 B | 选项 C | 选项 D" required></label><label>正确答案<select name="answer"><option value="0">第 1 项</option><option value="1">第 2 项</option><option value="2">第 3 项</option><option value="3">第 4 项</option></select></label><label>答案解析（可选）<textarea name="explanation" maxlength="2000" placeholder="解释正确答案与易错点，提交后向学生展示"></textarea></label><label>知识点（可选）<input name="knowledge_point" maxlength="50" placeholder="例如：字符串操作"></label><div class="actions"><button class="secondary" type="submit">添加练习题</button></div></form></section>
    ${teacherProgramming(course)}
    <div class="actions"><button class="primary" id="publish" ${course.status === "published" ? "disabled" : ""}>${course.status === "published" ? "课程已发布" : "确认并发布课程"}</button>${link("dashboard", "教师工作台")}</div><p class="muted">发布前至少需要一份资源和一道练习题。</p></div>`, "编辑与发布");
  bindTeacherProgramming(context(), course, editCoursePage);
  root.querySelector("#refresh").onclick = () => editCoursePage().catch(e => tell(e.message));
  bindForm("#audience-form", async (data,form) => {
    const major_ids = new FormData(form).getAll("major_ids");
    if (!major_ids.length) throw new Error("请至少选择一个面向的学生专业");
    await post(`/api/courses/${id}/audience`, {major_ids});
    tell("课程授课范围已更新，学生刷新后按新范围访问；历史作答保留");
  });
  bindForm("#resource-form", async data => {
    await post(`/api/courses/${id}/resources`, {...data, job_id:data.job_id || null});
    await editCoursePage(); tell("资源已加入课程");
  });
  bindForm("#question-form", async data => {
    await post(`/api/courses/${id}/questions`, {...data, options:data.options.split("|").map(o => o.trim()), answer:Number(data.answer), knowledge_point:data.knowledge_point || ""});
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
        await post(`/api/courses/${id}/chapters/${encodeURIComponent(form.dataset.chapter)}`, {title: new FormData(form).get("title")});
        await editCoursePage(); tell("章节已重命名");
      } catch (error) { tell(error.message); }
    };
  });
  root.querySelector("#publish").onclick = async () => {
    try { await post(`/api/courses/${id}/publish`); await editCoursePage(); tell("课程已发布，学生刷新工作台即可看到"); }
    catch (error) { tell(error.message); }
  };
}

async function recordsPage() {
  if (user.role === "teacher") return teacherAnalyticsPage();
  const [logs, courses] = await Promise.all([api("/api/learning-records"), api("/api/courses")]);
  const summaries = courses.map(c => {
    const items = logs.filter(r => r.course_id === c.id);
    return `<div class="record-row"><div><b>${esc(c.title)}</b><small>${items.length ? `已完成 ${items.length} 次练习 · 最近 ${date(items[0].submitted_at)}` : "待完成练习"}</small></div><strong class="score">${items.length ? `${Math.max(...items.map(r => r.score))} 分` : "—"}<small>最高分</small></strong></div>`;
  }).join("");
  layout(`<div class="section-title"><div><span class="eyebrow">LEARNING PROGRESS</span><h1>我的学习进度</h1><p class="muted">按课程查看练习完成情况，回顾每一次反馈。</p></div><button class="ghost" id="refresh">刷新记录</button></div><section><h2>课程进度</h2><div class="record-list">${summaries || '<div class="empty">暂无课程。</div>'}</div></section><section><h2>练习提交记录</h2>${recordList(logs, false)}</section>`, "学习进度");
  root.querySelector("#refresh").onclick = () => recordsPage().catch(e => tell(e.message));
}

async function teacherAnalyticsPage() { return analyticsPage(context()); }
async function profilePage() {
  if (user.role !== "student") throw new Error("请使用学生账号查看本人画像");
  return personalProfilePage(context());
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
  if (!config.features?.course_audiences) throw new Error("后台仍在运行旧版本。请在原终端按 Ctrl+C 停止服务，重新运行 run.py，再打开新终端显示的地址。");
  if (!account || page === "login") return loginPage();
  try { user = await api("/api/auth/me"); }
  catch (error) { if (error.status === 401) return loginPage(error.message); throw error; }
  if (page === "video") {
    layout("", "视频学习工作台");
    root.querySelector(".shell").remove();
    root.append(document.getElementById("video-template").content.cloneNode(true));
    mountVideo(config, user, fileUrl);
    initializeXfyunCredentials(config, user);
    initializeApiKey(config, user).catch(error => tell(error.message));
    return;
  }
  const views = {analysis:() => analysisPage(context(), params.get("record"), params.get("start") === "1"), "ai-quiz":() => quizPage(context(), params.get("job"), params.get("run"), params.get("wait")), programming:() => programmingPage(context(), params.get("course"), params.get("question")), dashboard, course:learningPage, "new-course":newCoursePage, "edit-course":editCoursePage, records:recordsPage, wrong:reviewPage, review:reviewPage, "review-center":reviewPage, profile:profilePage, account:accountPage};
  if (!views[page]) throw new Error("页面不存在");
  await views[page]();
}
render().catch(error => layout(`<div class="form-card"><h2>页面暂时无法打开</h2><p>${esc(error.message)}</p><div class="actions"><a class="secondary" href="/" target="_blank" rel="noopener">打开登录页 ↗</a><button class="ghost" id="retry">重试</button></div></div>`, "页面提示"));
root.addEventListener("click", event => { if (event.target.id === "retry") location.reload(); });
// Reconcile progress when returning from another tab, without disrupting open forms.
window.addEventListener("focus", () => {
  if (!user) return;
  if (page === "dashboard") dashboard().catch(e => tell(e.message));
  if (page === "records") recordsPage().catch(e => tell(e.message));
});
