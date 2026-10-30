import { mountVideo } from "/static/video.js";

const root = document.getElementById("platform");
const params = new URLSearchParams(location.search);
let config, user;
let account = params.get("account") || "";
let page = params.get("page") || (account ? "dashboard" : "login");
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
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
  return `<header class="nav"><a class="brand" href="/" target="_blank" rel="noopener">知学 <small>SMART LEARNING</small></a>
    ${user ? `<nav class="nav-links" aria-label="主导航">${link("dashboard", "工作台", "nav-link")}${link("video", "视频提取", "nav-link")}${user.role === "student" ? link("review", "今日复习", "nav-link") + link("wrong", "我的错题", "nav-link") : ""}${link("records", user.role === "teacher" ? "学习情况" : "学习记录", "nav-link")}${link("account", "账号管理", "nav-link")}</nav><span class="identity">${esc(user.name)} · ${roleName(user.role)}</span><button class="ghost" id="logout">退出</button>` : '<span class="identity">智慧课程平台 · 本地演示</span>'}</header><div class="notice" role="status" hidden></div>`;
}
function layout(content, title) {
  document.title = `${title} · 知学`;
  root.innerHTML = nav() + `<main class="shell">${content}</main>`;
  const logout = root.querySelector("#logout");
  if (logout) logout.onclick = async () => {
    try { await post("/api/auth/logout"); location.href = "/"; } catch (error) { tell(error.message); }
  };
}
function recordList(items, teacher = false) {
  if (!items.length) return '<div class="record-list empty">暂无练习提交记录。</div>';
  return `<div class="record-list">${items.map(r => `<div class="record-row"><div><b>${teacher ? esc(r.student) + " · " : ""}${esc(r.course_name || r.course_id)}</b><small>${date(r.submitted_at)} · 答对 ${r.correct_count}/${r.total} 题</small></div><strong class="score">${r.score} 分</strong></div>`).join("")}</div>`;
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
  const examples = await api("/api/auth/demo-accounts");
  layout(`<div class="auth-intro"><span class="eyebrow">LEARN · PRACTICE · GROW</span><h1>让每一次学习，都有迹可循。</h1><p class="muted">发布资源与练习，获得反馈，让教师与学生看见学习进展。</p></div>
    ${message ? `<p class="warning">${esc(message)}</p>` : ""}
    <div class="auth-grid"><div class="form-card"><div class="auth-tabs" role="group" aria-label="登录或注册"><button id="login-tab" class="primary">账号登录</button><button id="register-tab" class="ghost">注册账号</button></div><div id="auth-form"></div></div>
    <section class="demo-panel"><span class="eyebrow">QUICK START</span><h2>演示账号</h2><p class="muted">点击“填入账号”，再点击登录。可分别打开教师与学生工作台。</p><div class="table-scroll"><table><thead><tr><th>身份</th><th>账号</th><th>密码</th><th>操作</th></tr></thead><tbody>${examples.map((u, i) => `<tr><td>${esc(u.name)}<small>${roleName(u.role)}</small></td><td><code>${esc(u.username)}</code></td><td><code>${esc(u.password)}</code></td><td><button class="secondary fill-account" data-index="${i}">填入账号</button></td></tr>`).join("")}</tbody></table></div><p class="muted demo-note">这是课堂演示环境，请使用演示专用密码。</p></section></div>`, "登录与注册");
  function showForm(register = false) {
    root.querySelector("#login-tab").className = register ? "ghost" : "primary";
    root.querySelector("#register-tab").className = register ? "primary" : "ghost";
    root.querySelector("#auth-form").innerHTML = `<form id="auth"><h2>${register ? "创建你的账号" : "欢迎回来"}</h2>
      ${register ? '<label>身份<select name="role"><option value="student">学生</option><option value="teacher">教师</option></select></label><label>姓名<input name="name" autocomplete="name" maxlength="30" required></label>' : ""}
      <label>账号<input name="username" autocomplete="username" placeholder="字母、数字或下划线" minlength="3" maxlength="32" pattern="[A-Za-z0-9_]{3,32}" required></label>
      <label>密码<input name="password" type="password" autocomplete="${register ? "new-password" : "current-password"}" minlength="6" maxlength="128" required></label>
      ${register ? '<label>确认密码<input name="confirm" type="password" autocomplete="new-password" minlength="6" maxlength="128" required></label>' : ""}
      <div class="actions"><button class="primary" type="submit">${register ? "注册账号" : "登录"}</button></div><div id="auth-success" role="status"></div></form>`;
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
      root.querySelector("#auth-success").innerHTML = `<div class="result"><b>${register ? "注册成功" : "登录成功"}，${esc(authenticated.name)}</b><p>打开工作台后，此页可以继续登录其他演示账号。</p><a id="enter-workspace" class="primary" href="${esc(destination)}" target="_blank" rel="noopener">进入${roleName(authenticated.role)}工作台 ↗</a></div>`;
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
  const [courses, logs] = await Promise.all([api("/api/courses"), api("/api/learning-records")]);
  const teacher = user.role === "teacher";
  const completed = new Set(logs.map(r => r.course_id));
  const completedCount = courses.filter(c => completed.has(c.id)).length;
  layout(`<div class="hero"><div><span class="eyebrow">${teacher ? "TEACHER WORKSPACE" : "STUDENT LEARNING SPACE"}</span><h1>${esc(user.name)}，${teacher ? "开始今天的教学。" : "继续你的学习。"}</h1><p class="muted">${teacher ? "创建课程、准备资料与练习，发布后查看学生学习情况。" : "学习课程资料，完成练习；也可以上传自己的课程视频。"}</p><div class="hero-actions">${link("video", "上传与提取视频")}${teacher ? link("new-course", "+ 创建课程", "primary") : link("records", "查看个人学习记录", "primary")}</div></div><div class="metric"><b>${teacher ? courses.length : `${completedCount}/${courses.length}`}</b><span>${teacher ? "门教学课程" : "门课程已完成练习"}</span></div></div>
    <section><div class="section-title"><div><span class="eyebrow">${teacher ? "COURSE MANAGEMENT" : "MY COURSES"}</span><h2>${teacher ? "我的课程" : "可学习课程"}</h2></div><button class="ghost" id="refresh">刷新数据</button></div><div class="course-grid">${courses.map(c => `<article class="course-card"><span class="tag ${c.status}">${teacher ? (c.status === "published" ? "已发布" : "草稿") : (completed.has(c.id) ? "已完成练习" : "待学习")}</span><h3>${esc(c.title)}</h3><p>${esc(c.description || "课程资料与课后练习")}</p><span class="meta">${esc(c.teacher)} · ${c.resources.length} 份资源 · ${c.questions.length} 道练习</span>${link(teacher ? "edit-course" : "course", teacher ? "编辑与发布" : "开始学习", "primary", {course:c.id})}</article>`).join("") || '<p class="muted">暂无课程。</p>'}</div></section>
    <section><div class="section-title"><div><span class="eyebrow">LEARNING RECORDS</span><h2>${teacher ? "最近的学生提交" : "最近的学习记录"}</h2></div>${link("records", "查看全部")}</div>${recordList(logs.slice(0, 5), teacher)}</section>`, teacher ? "教师工作台" : "学生工作台");
  root.querySelector("#refresh").onclick = () => dashboard().catch(e => tell(e.message));
}

async function learningPage() {
  if (user.role !== "student") throw new Error("请使用学生账号打开学习页面");
  const course = await api(`/api/courses/${encodeURIComponent(params.get("course") || "")}`);
  layout(`<div class="learning-panel"><span class="eyebrow">${esc(course.teacher)} · COURSE LEARNING</span><h1>${esc(course.title)}</h1><p class="muted">${esc(course.description)}</p><h2>学习资源</h2><div class="resource-list">${course.resources.map(r => `<div class="resource"><div><b>${esc(r.name)}</b><small>${esc(r.kind)}</small></div>${r.job_id ? `<div class="actions"><a class="secondary" href="${fileUrl(`/api/jobs/${encodeURIComponent(r.job_id)}/files/pdf?preview=true`)}" target="_blank" rel="noopener">打开课件 ↗</a>${link("video", "提取结果", "ghost", {job:r.job_id})}</div>` : '<span class="muted">资源说明已登记</span>'}</div>`).join("")}</div><form id="quiz"><h2>课后练习</h2><p class="muted">完成全部题目后提交，即可获得反馈并保存学习记录。</p><div class="questions">${course.questions.map((q, i) => `<fieldset class="question"><legend>${i + 1}. ${esc(q.text)}</legend>${q.options.map((option, oi) => `<label class="option"><input type="radio" name="${esc(q.id)}" value="${oi}" required> ${String.fromCharCode(65 + oi)}. ${esc(option)}</label>`).join("")}</fieldset>`).join("")}</div><div class="actions"><button class="primary" type="submit">提交练习</button>${link("records", "查看学习进度")}</div></form><div id="quiz-result" aria-live="polite"></div></div>`, course.title);
  bindForm("#quiz", async answers => {
    const result = await post(`/api/courses/${course.id}/submissions`, {answers:Object.fromEntries(Object.entries(answers).map(([id, value]) => [id, Number(value)]))});
    root.querySelector("#quiz-result").innerHTML = `<div class="result"><h2>本次得分 ${result.score} 分</h2><p>答对 ${result.correct_count}/${result.total} 题，已保存到个人学习记录。</p>${result.details.map((d, i) => `<p>${d.correct ? "✓" : "✗"} ${i + 1}. ${esc(d.text)} · 正确答案：${esc(d.options[d.answer])}</p>`).join("")}</div>`;
    root.querySelector("#quiz-result").scrollIntoView({behavior:"smooth", block:"nearest"});
  });
}

async function newCoursePage() {
  if (user.role !== "teacher") throw new Error("只有教师可以创建课程");
  layout(`<div class="form-card"><span class="eyebrow">NEW COURSE</span><h1>创建一门课程</h1><form id="new-course"><label>课程名称<input name="title" maxlength="80" required placeholder="例如：Python 程序设计入门"></label><label>课程简介<textarea name="description" maxlength="300" placeholder="说明课程目标和学习内容"></textarea></label><div class="actions"><button class="primary" type="submit">保存课程草稿</button></div><div id="created" role="status"></div></form></div>`, "新建课程");
  bindForm("#new-course", async (data, form) => {
    const course = await post("/api/courses", data);
    form.reset();
    root.querySelector("#created").innerHTML = `<div class="result"><p>已创建：${esc(course.title)}</p>${link("edit-course", "添加资源与练习", "primary", {course:course.id})}</div>`;
  });
}

async function editCoursePage() {
  if (user.role !== "teacher") throw new Error("只有教师可以编辑课程");
  const id = encodeURIComponent(params.get("course") || "");
  const [course, jobs] = await Promise.all([api(`/api/courses/${id}`), api("/api/jobs")]);
  if (course.teacher_id !== user.id) throw new Error("只能编辑自己创建的课程");
  layout(`<div class="learning-panel"><span class="eyebrow">COURSE EDITOR</span><h1>${esc(course.title)}</h1><p class="muted">${esc(course.description)}</p><div class="actions"><span class="tag ${course.status}">${course.status === "published" ? "已发布" : "草稿"}</span>${link("video", "提取新的视频资源")}<button class="ghost" id="refresh">刷新提取结果</button></div>
    <section class="editor"><h2>学习资源</h2><div class="editor-list">${course.resources.map(r => `<div>${esc(r.name)} · ${esc(r.kind)}${r.job_id ? " · 已关联课件" : ""}</div>`).join("") || "暂无资源"}</div><form id="resource-form"><div class="resource-fields"><label>资源名称<input name="name" maxlength="100" required></label><label>类型<select name="kind"><option>课件</option><option>视频</option><option>文档</option></select></label><label>关联提取结果<select name="job_id"><option value="">不关联（仅登记资源说明）</option>${jobs.filter(j => j.status === "completed").map(j => `<option value="${esc(j.id)}">${esc(j.filename)}</option>`).join("")}</select></label></div><div class="actions"><button class="secondary" type="submit">添加学习资源</button></div></form></section>
    <section class="editor"><h2>单选练习题</h2><div class="editor-list">${course.questions.map((q,i) => `<div>${i + 1}. ${esc(q.text)} · 答案：${esc(q.options[q.answer])}${q.knowledge_point_id ? ` · 知识点：${esc((course.knowledge_points || []).find(kp => kp.id === q.knowledge_point_id)?.name || "")}` : ""}</div>`).join("") || "暂无练习题"}</div><form id="question-form"><label>题目<input name="text" maxlength="300" required></label><label>选项（2–4 项，用 | 分隔）<input name="options" placeholder="选项 A | 选项 B | 选项 C | 选项 D" required></label><label>正确答案<select name="answer"><option value="0">第 1 项</option><option value="1">第 2 项</option><option value="2">第 3 项</option><option value="3">第 4 项</option></select></label><label>知识点（可选）<input name="knowledge_point" maxlength="50" placeholder="例如：字符串操作"></label><div class="actions"><button class="secondary" type="submit">添加练习题</button></div></form></section>
    <div class="actions"><button class="primary" id="publish" ${course.status === "published" ? "disabled" : ""}>${course.status === "published" ? "课程已发布" : "确认并发布课程"}</button>${link("dashboard", "教师工作台")}</div><p class="muted">发布前至少需要一份资源和一道练习题。</p></div>`, "编辑与发布");
  root.querySelector("#refresh").onclick = () => editCoursePage().catch(e => tell(e.message));
  bindForm("#resource-form", async data => {
    await post(`/api/courses/${id}/resources`, {...data, job_id:data.job_id || null});
    await editCoursePage(); tell("资源已加入课程");
  });
  bindForm("#question-form", async data => {
    await post(`/api/courses/${id}/questions`, {...data, options:data.options.split("|").map(o => o.trim()), answer:Number(data.answer), knowledge_point:data.knowledge_point || ""});
    await editCoursePage(); tell("练习题已加入课程");
  });
  root.querySelector("#publish").onclick = async () => {
    try { await post(`/api/courses/${id}/publish`); await editCoursePage(); tell("课程已发布，学生刷新工作台即可看到"); }
    catch (error) { tell(error.message); }
  };
}

async function recordsPage() {
  const [logs, courses] = await Promise.all([api("/api/learning-records"), api("/api/courses")]);
  const teacher = user.role === "teacher";
  const summaries = courses.map(c => {
    const items = logs.filter(r => r.course_id === c.id);
    const participants = new Set(items.map(r => r.student_id)).size;
    return `<div class="record-row"><div><b>${esc(c.title)}</b><small>${teacher ? `${participants} 人提交 · ${items.length} 次练习` : (items.length ? `已完成 ${items.length} 次练习 · 最近 ${date(items[0].submitted_at)}` : "待完成练习")}</small></div><strong class="score">${items.length ? `${Math.max(...items.map(r => r.score))} 分` : "—"}<small>最高分</small></strong></div>`;
  }).join("");
  layout(`<div class="section-title"><div><span class="eyebrow">LEARNING PROGRESS</span><h1>${teacher ? "学生学习情况" : "我的学习进度"}</h1><p class="muted">${teacher ? "查看自己课程的学生提交与成绩。" : "按课程查看练习完成情况，回顾每一次反馈。"}</p></div><button class="ghost" id="refresh">刷新记录</button></div><section><h2>课程进度</h2><div class="record-list">${summaries || '<div class="empty">暂无课程。</div>'}</div></section><section><h2>练习提交记录</h2>${recordList(logs, teacher)}</section>`, teacher ? "学生学习情况" : "学习进度");
  root.querySelector("#refresh").onclick = () => recordsPage().catch(e => tell(e.message));
}

async function wrongPage() {
  if (user.role !== "student") throw new Error("只有学生可以查看错题本");
  const items = await api("/api/wrong-questions");
  const groups = {};
  for (const item of items) (groups[item.knowledge_point_name || "未分类"] ??= []).push(item);
  const body = Object.keys(groups).length ? Object.keys(groups).map(name => `<section><div class="section-title"><div><span class="eyebrow">KNOWLEDGE POINT</span><h2>${esc(name)}</h2></div><span class="tag">${groups[name].length} 题</span></div><div class="record-list">${groups[name].map(w => `<div class="record-row"><div><b>${esc(w.question_snapshot.text)}</b><small>答错 ${w.wrong_count} 次 · ${w.status === "mastered" ? "已掌握" : "待复习"} · 最近 ${date(w.last_wrong_at)}${w.status !== "mastered" ? ` · 下次复习：${new Date(w.next_review_at) <= new Date() ? "今天到期" : `${Math.round((new Date(w.next_review_at) - new Date()) / 86400000)} 天后`}` : ""}</small></div><strong class="score">${esc(w.question_snapshot.options[w.question_snapshot.answer] ?? "")}<small>正确答案</small></strong></div><div class="actions">${w.status === "mastered" ? `<span class="tag">已掌握</span>` : `<button class="secondary master-btn" data-id="${esc(w.id)}">标记已掌握</button>`}</div></div>`).join("")}</div></section>`).join("") : '<div class="record-list empty">暂无错题，继续保持。</div>';
  layout(`<div class="section-title"><div><span class="eyebrow">WRONG QUESTION BOOK</span><h1>我的错题本</h1><p class="muted">答错的题目已自动归档，按知识点分组展示，为个性化推荐提供依据。</p></div><button class="ghost" id="refresh">刷新</button></div>${body}`, "我的错题");
  root.querySelectorAll(".master-btn").forEach(button => button.onclick = async () => {
    try { await post(`/api/wrong-questions/${encodeURIComponent(button.dataset.id)}/mastered`); await wrongPage(); tell("已标记为掌握"); }
    catch (error) { tell(error.message); }
  });
  root.querySelector("#refresh").onclick = () => wrongPage().catch(e => tell(e.message));
}

async function reviewPage(message = "") {
  if (user.role !== "student") throw new Error("只有学生可以使用复习计划");
  const items = await api("/api/review/due");
  const now = new Date();
  const due = items.filter(w => new Date(w.next_review_at) <= now);
  const pending = items.filter(w => new Date(w.next_review_at) > now);
  const daysUntil = next => Math.max(0, Math.round((new Date(next) - now) / 86400000));
  // 薄弱知识点：逾期题数优先，其次总错次
  const stats = {};
  for (const w of items) {
    const key = w.knowledge_point_name || "未分类";
    const stat = (stats[key] ??= {due: 0, count: 0, wrong: 0});
    stat.count += 1;
    stat.wrong += w.wrong_count;
    if (new Date(w.next_review_at) <= now) stat.due += 1;
  }
  const rank = Object.keys(stats).map(name => ({name, ...stats[name]})).sort((a, b) => b.due - a.due || b.wrong - a.wrong);
  const reviewForm = due.length ? `<form id="review-form"><h2>今日复习（${due.length} 题到期）</h2><p class="muted">逐题作答后提交，答对则复习间隔翻倍，答错则回到 1 天。</p><div class="questions">${due.map((w, i) => `<fieldset class="question"><legend>${i + 1}. ${esc(w.question_snapshot.text)} <span class="tag">${esc(w.knowledge_point_name)}</span></legend>${w.question_snapshot.options.map((option, oi) => `<label class="option"><input type="radio" name="${esc(w.id)}" value="${oi}" required> ${String.fromCharCode(65 + oi)}. ${esc(option)}</label>`).join("")}</fieldset>`).join("")}</div><div class="actions"><button class="primary" type="submit">提交复习结果</button></div><div id="review-result" aria-live="polite"></div></form>` : '<div class="record-list empty">今天没有到期的错题，全部复习完成，继续保持。</div>';
  layout(`<div class="section-title"><div><span class="eyebrow">SPACED REPETITION</span><h1>复习计划</h1><p class="muted">按遗忘曲线安排错题复习：间隔 1 → 2 → 4 → 8 天，答对翻倍，答错重置。</p></div><button class="ghost" id="refresh">刷新</button></div>
    ${message ? `<div class="result"><p>${esc(message)}</p></div>` : ""}
    <section><div class="section-title"><div><span class="eyebrow">WEAK POINTS</span><h2>薄弱知识点</h2></div></div><div class="record-list">${rank.map(s => `<div class="record-row"><div><b>${esc(s.name)}</b><small>${s.due} 题待复习 · 累计 ${s.count} 题错题 · 共错 ${s.wrong} 次</small></div><strong class="score">${s.due ? `${s.due} 题到期` : "复习中"}</strong></div>`).join("") || '<div class="record-list empty">暂无错题数据。</div>'}</div></section>
    <section>${reviewForm}</section>
    ${pending.length ? `<section><div class="section-title"><div><span class="eyebrow">UPCOMING</span><h2>后续复习安排</h2></div></div><div class="record-list">${pending.map(w => `<div class="record-row"><div><b>${esc(w.question_snapshot.text)}</b><small>${esc(w.knowledge_point_name)} · 已复习 ${w.review_count} 次</small></div><strong class="score">${daysUntil(w.next_review_at)} 天后<small>下次复习</small></strong></div>`).join("")}</div></section>` : ""}`, "今日复习");
  const form = root.querySelector("#review-form");
  if (form) bindForm("#review-form", async answers => {
    const entries = Object.entries(answers);
    const results = await Promise.all(entries.map(async ([id, selected]) => {
      const r = await post(`/api/wrong-questions/${encodeURIComponent(id)}/review`, {selected:Number(selected)});
      return {id, correct:r.correct, interval:r.review_interval_days, status:r.status, answer:r.answer, snapshot:r.question_snapshot};
    }));
    const summary = results.map(r => `${r.correct ? "✓" : "✗"} ${esc(r.snapshot.text)}：${r.correct ? `下次 ${r.interval} 天后复习` : "回到 1 天后复习"}`).join("<br>");
    root.querySelector("#review-result").innerHTML = `<div class="result"><h2>本次复习完成 ${results.filter(r => r.correct).length}/${results.length}</h2><p>${summary}</p></div>`;
    root.querySelector("#review-result").scrollIntoView({behavior:"smooth", block:"nearest"});
    await reviewPage("复习结果已更新，未掌握的题目继续留在复习队列。");
  });
  root.querySelector("#refresh").onclick = () => reviewPage().catch(e => tell(e.message));
}

async function accountPage() {
  layout(`<div class="form-card"><span class="eyebrow">ACCOUNT SETTINGS</span><h1>账号管理</h1><p>${esc(user.name)} · ${roleName(user.role)}</p><p class="muted">账号：${esc(user.username)}</p><form id="password-form"><label>原密码<input type="password" name="old_password" autocomplete="current-password" required></label><label>新密码<input type="password" name="new_password" minlength="6" maxlength="128" autocomplete="new-password" required></label><label>确认新密码<input type="password" name="confirm" minlength="6" maxlength="128" autocomplete="new-password" required></label><div class="actions"><button class="primary" type="submit">保存新密码</button></div></form></div>`, "账号管理");
  bindForm("#password-form", async (data, form) => {
    if (data.new_password !== data.confirm) throw new Error("两次输入的新密码不一致");
    await post("/api/auth/password", data); form.reset(); tell("密码已保存，下次请使用新密码登录");
  });
}

async function render() {
  config = await api("/api/config");
  if (!account || page === "login") return loginPage();
  try { user = await api("/api/auth/me"); }
  catch (error) { if (error.status === 401) return loginPage(error.message); throw error; }
  if (page === "video") {
    layout("", "视频学习工作台");
    root.querySelector(".shell").remove();
    root.append(document.getElementById("video-template").content.cloneNode(true));
    mountVideo(config, user, fileUrl);
    return;
  }
  const views = {dashboard, course:learningPage, "new-course":newCoursePage, "edit-course":editCoursePage, records:recordsPage, wrong:wrongPage, review:reviewPage, account:accountPage};
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
