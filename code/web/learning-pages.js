import {courseLabel} from "/static/teaching-courses.js";
import {esc, icon, radarCard, number, stateTag} from "/static/ui.js";

const expandedStudents = new Set();
const reviewState = {tab: "today", course: "all", chapter: "all", point: "all", calDay: null, results: []};
const pointName = item => item.knowledge_point_name || "未分类";
const date = value => value ? new Date(value).toLocaleString("zh-CN") : "暂无";
const day = value => new Date(value).toLocaleDateString("zh-CN", {month:"long", day:"numeric"});
const dayKey = value => {
  const item = new Date(value);
  return `${item.getFullYear()}-${String(item.getMonth()+1).padStart(2,"0")}-${String(item.getDate()).padStart(2,"0")}`;
};
const WEEK = "日一二三四五六";

export async function analyticsPage(ctx) {
  const {api, layout, root, tell, recordList, majorName} = ctx;
  const [data, logs] = await Promise.all([api("/api/teacher/analytics"), api("/api/learning-records")]);
  const overview = data.overview;
  const cards = [
    [overview.student_count, "班级学生", "user"], [overview.participant_count, "已参与学生", "check"],
    [number(overview.average_score), "提交平均分", "chart"],
    [overview.pass_rate === null ? "—" : `${overview.pass_rate}%`, "提交及格率", "target"],
    [overview.attention_count, "待关注学生", "review"], [overview.submission_count, "正式提交次数", "book"],
  ].map(([value,label,graphic]) => `<div class="analytics-card"><span class="stat-icon">${icon(graphic)}</span><b>${value}</b><span>${label}</span></div>`).join("");
  const courseRows = data.courses.map(course => `<article class="analysis-card"><div class="analysis-head"><div><h3>${esc(courseLabel(course))}</h3><small>${course.participants} 人参与 · ${course.submission_count} 次提交</small></div><strong>${number(course.average_score)}<small>平均分</small></strong></div><div class="course-audience">${course.major_ids.map(mid => `<span class="tag neutral">${esc(majorName(mid))}</span>`).join("")}</div><p class="muted">及格率 ${course.pass_rate === null ? "暂无数据" : `${course.pass_rate}%`}</p>${course.weak_questions.length ? `<div class="weak-list"><b>建议重点讲解</b>${course.weak_questions.map(q => `<span>${esc(q.knowledge_point)} · 正确率 ${q.correct_rate}%<small>${esc(q.text)}</small></span>`).join("")}</div>` : course.submission_count ? "" : '<div class="empty compact">暂无提交</div>'}</article>`).join("");
  const students = data.students.map(student => {
    const panel = `radar-${student.student_id}`, expanded = expandedStudents.has(student.student_id);
    return `<article class="student-profile"><div class="record-row"><div class="student-info"><span class="avatar">${esc(student.student.slice(0,1))}</span><div><b>${esc(student.student)}</b><span class="student-major">${esc(majorName(student.major_id))}</span><small>${student.not_started ? "还没有正式提交" : `参与 ${student.course_count} 个教学班 · ${student.submission_count} 次正式提交`}</small></div></div><div class="profile-status">${stateTag(student)}<strong class="score">${number(student.average_score)}<small>平均分</small></strong><button type="button" class="secondary profile-btn" data-student="${esc(student.student_id)}" aria-expanded="${expanded}" aria-controls="${esc(panel)}">${icon("chart")}<span>${expanded ? "折叠雷达图" : "展开雷达图"}</span>${icon("chevron")}</button></div></div><div id="${esc(panel)}" class="profile-panel" ${expanded ? "" : "hidden"}>${radarCard(student)}</div></article>`;
  }).join("");
  layout(`<div class="page-heading"><div><h1>学情总览</h1></div><button class="ghost" id="refresh">${icon("refresh")}刷新数据</button></div><section class="analytics-grid">${cards}</section><section><div class="section-title"><div><h2>教学班学情</h2></div><span class="muted">${data.courses.length} 个教学班</span></div><div class="analysis-grid">${courseRows || '<div class="empty">创建并发布第一门课程，开始收集学习反馈。</div>'}</div></section><section><div class="section-title profile-heading"><div><h2>学生画像</h2></div><div class="actions"><button class="secondary" type="button" id="expand-all" ${data.students.length ? "" : "disabled"}>${icon("plus")}全部展开</button><button class="ghost" type="button" id="collapse-all" ${data.students.length ? "" : "disabled"}>全部折叠</button></div></div><div id="student-profiles" class="student-profiles">${students || '<div class="empty">暂无班级学生。</div>'}</div></section><section><div class="section-title"><h2>自主练习记录</h2></div>${recordList(logs.filter(r=>!r.assignment_id),true)}</section><section><div class="section-title"><h2>作业提交记录</h2></div>${recordList(logs.filter(r=>r.assignment_id),true)}</section>`, "教师学习分析");
  const setExpanded = (button, expanded) => {
    const student = button.dataset.student;
    if (expanded) expandedStudents.add(student); else expandedStudents.delete(student);
    root.querySelector(`#${CSS.escape(button.getAttribute("aria-controls"))}`).hidden = !expanded;
    button.setAttribute("aria-expanded", String(expanded));
    button.querySelector("span").textContent = expanded ? "折叠雷达图" : "展开雷达图";
  };
  root.querySelectorAll(".profile-btn").forEach(button => { button.onclick = () => setExpanded(button, button.getAttribute("aria-expanded") !== "true"); });
  root.querySelector("#expand-all").onclick = () => root.querySelectorAll(".profile-btn").forEach(button => setExpanded(button, true));
  root.querySelector("#collapse-all").onclick = () => root.querySelectorAll(".profile-btn").forEach(button => setExpanded(button, false));
  root.querySelector("#refresh").onclick = () => analyticsPage(ctx).catch(error => tell(error.message));
}

export async function reviewCenter(ctx, initialTab, initialFilters) {
  const {api, post, layout, root, tell, link, fileUrl} = ctx;
  if (["today", "all", "mastered"].includes(initialTab)) reviewState.tab = initialTab;
  if (initialFilters?.course && initialFilters.point) {
    reviewState.course = initialFilters.course;
    reviewState.chapter = "all";
    reviewState.point = `id:${initialFilters.point}`;
  }
  const [items, courses] = await Promise.all([api("/api/wrong-questions"), api("/api/courses")]);
  const now = Date.now(), nowKey = dayKey(now);
  const unresolved = items.filter(item => item.status !== "mastered");
  const mastered = items.filter(item => item.status === "mastered");
  const names = new Map(courses.map(course => [course.id, courseLabel(course)]));
  items.forEach(item => { if (!names.has(item.course_id)) names.set(item.course_id, item.course_name || "历史课程"); });
  if (reviewState.course !== "all" && !names.has(reviewState.course)) { reviewState.course = "all"; reviewState.chapter = "all"; }
  const scoped = items.filter(item => reviewState.course === "all" || item.course_id === reviewState.course);
  const chapters = [...new Set(scoped.map(item => item.chapter_title || "未分章节"))];
  if (!chapters.includes(reviewState.chapter)) reviewState.chapter = "all";
  const pointItems = scoped.filter(item => reviewState.chapter === "all" || (item.chapter_title || "未分章节") === reviewState.chapter);
  const points = new Map(pointItems.map(item => [`name:${pointName(item)}`, pointName(item)]));
  const graphItem = pointItems.find(item => item.knowledge_point_id && `id:${item.knowledge_point_id}` === reviewState.point);
  if (graphItem) points.set(reviewState.point, `${pointName(graphItem)}（图谱节点）`);
  if (!points.has(reviewState.point)) reviewState.point = "all";
  const matches = item => (reviewState.course === "all" || item.course_id === reviewState.course)
    && (reviewState.chapter === "all" || (item.chapter_title || "未分章节") === reviewState.chapter)
    && (reviewState.point === "all" || `name:${pointName(item)}` === reviewState.point
        || `id:${item.knowledge_point_id}` === reviewState.point);
  const calendarDays = Array.from({length: 14}, (_, index) => {
    const value = new Date(now); value.setHours(0, 0, 0, 0); value.setDate(value.getDate() + index);
    const key = dayKey(value);
    return {key, month: value.getMonth() + 1, day: value.getDate(), week: WEEK[value.getDay()], today: key === nowKey};
  });
  const buckets = new Map(calendarDays.map(item => [item.key, []]));
  const beyond = [];
  unresolved.forEach(item => {
    const key = dayKey(item.next_review_at);
    if (buckets.has(key)) buckets.get(key).push(item);
    else if (key < nowKey) buckets.get(nowKey).push(item);
    else beyond.push(item);
  });
  if (!reviewState.calDay || !buckets.has(reviewState.calDay)) reviewState.calDay = nowKey;
  const dayItems = (buckets.get(reviewState.calDay) || []).filter(matches)
    .sort((a,b) => a.next_review_at.localeCompare(b.next_review_at));
  const editable = reviewState.calDay === nowKey;
  const dueToday = (buckets.get(nowKey) || []).length;
  const selected = reviewState.tab === "today" ? dayItems : (reviewState.tab === "mastered" ? mastered : items).filter(matches);
  const upcoming = unresolved.filter(item => new Date(item.next_review_at).getTime() > now && matches(item)).sort((a,b) => a.next_review_at.localeCompare(b.next_review_at));
  const counts = [[dueToday, "今日到期", "review"], [unresolved.length, "待巩固错题", "book"], [mastered.length, "已掌握", "check"]];
  const empty = reviewState.tab === "today" ? (editable ? "今天暂时没有到期题目" : "这一天暂时没有到期题目") : reviewState.tab === "mastered" ? "还没有标记为已掌握的题目" : "这里还没有错题";
  const meta = item => `<div class="question-meta"><span class="tag neutral">${esc(names.get(item.course_id))}</span><span>${esc(item.chapter_title || "未分章节")}</span><span>${esc(item.knowledge_point_name || "未分类")}</span>${item.source === "ai_quiz_manual" ? '<span class="tag neutral">主动加入</span>' : ""}</div>`;
  const answerDetail = item => {
    const snapshot = item.question_snapshot;
    return `<details class="answer-detail"><summary>查看选项、答案与解析</summary><ol class="answer-options" type="A">${snapshot.options.map((option,i) => `<li class="${i === snapshot.answer ? "correct-option" : ""}">${esc(option)}${i === snapshot.answer ? " ✓" : ""}</li>`).join("")}</ol><p><b>解析：</b>${esc(snapshot.explanation || "结合本章讲义回顾对应知识点。")}</p></details>`;
  };
  const body = selected.length ? selected.map((item,i) => {
    const snapshot = item.question_snapshot;
    if (reviewState.tab === "today") {
      if (!editable) {
        return `<article class="study-card wrong-card"><div class="wrong-heading"><h3>${esc(snapshot.text)}</h3><span class="tag neutral">${day(item.next_review_at)}</span></div>${meta(item)}${answerDetail(item)}</article>`;
      }
      return `<form class="review-question study-card" data-review-id="${esc(item.id)}">${meta(item)}<h3><span class="question-number">${String(i+1).padStart(2,"0")}</span>${esc(snapshot.text)}</h3><fieldset class="review-options"><legend class="sr-only">${esc(snapshot.text)}</legend>${snapshot.options.map((option,oi) => `<label class="option"><input type="radio" name="selected" value="${oi}" required><span class="option-letter">${String.fromCharCode(65+oi)}</span><span>${esc(option)}</span></label>`).join("")}</fieldset><div class="question-footer"><span class="muted">累计答错 ${item.wrong_count} 次 · 已复习 ${item.review_count || 0} 次</span><button class="primary" type="submit">提交本题 ${icon("arrow")}</button></div></form>`;
    }
    return `<article class="study-card wrong-card">${meta(item)}<div class="wrong-heading"><h3>${esc(snapshot.text)}</h3><span class="tag ${item.status === "mastered" ? "published" : "attention"}">${item.status === "mastered" ? "已掌握" : "待巩固"}</span></div><p class="muted">答错 ${item.wrong_count} 次 · ${item.last_wrong_at ? `最近答错 ${date(item.last_wrong_at)}` : `加入时间 ${date(item.added_at)}`}${item.status === "mastered" ? "" : ` · 下次复习 ${day(item.next_review_at)}`}</p>${answerDetail(item)}${item.status === "mastered" ? "" : `<div class="actions"><button type="button" class="secondary master-btn" data-id="${esc(item.id)}">${icon("check")}标记已掌握</button></div>`}</article>`;
  }).join("") : `<div class="empty-state">${icon(reviewState.tab === "today" ? "check" : "book")}<h2>${empty}</h2><p class="muted">${reviewState.course !== "all" || reviewState.chapter !== "all" || reviewState.point !== "all" ? "试试调整课程、章节或知识点筛选。" : "可以继续课程练习，或切换到全部错题回顾知识点。"}</p>${link("dashboard", "继续学习", "secondary")}</div>`;
  const filters = new URLSearchParams();
  if (reviewState.course !== "all") filters.set("course_id", reviewState.course);
  if (reviewState.chapter !== "all") filters.set("chapter", reviewState.chapter);
  if (reviewState.point.startsWith("id:")) filters.set("knowledge_point_id", reviewState.point.slice(3));
  else if (reviewState.point.startsWith("name:")) filters.set("knowledge_point", reviewState.point.slice(5));
  const exportUrl = fileUrl(`/api/wrong-questions/export.csv${filters.toString() ? `?${filters}` : ""}`);
  const calendar = `<section class="review-calendar"><div class="section-title"><div><h2>复习日历</h2><p class="muted">逾期题目并入今天</p></div><span class="tag neutral">${calendarDays[0].month}/${calendarDays[0].day} – ${calendarDays.at(-1).month}/${calendarDays.at(-1).day}</span></div><ul class="calendar-strip">${calendarDays.map(item => {
    const count = (buckets.get(item.key) || []).filter(matches).length;
    return `<li><button type="button" class="calendar-day ${item.today ? "is-today" : ""} ${item.key === reviewState.calDay ? "is-active" : ""}" data-day="${item.key}" aria-label="${item.month} 月 ${item.day} 日，${count} 题" aria-pressed="${item.key === reviewState.calDay}" ${item.today ? 'aria-current="date"' : ""}><span class="calendar-week">${item.today ? "今天" : `周${item.week}`}</span><b><span class="calendar-prefix">${item.month}/</span>${item.day}</b><small class="${count ? "has-load" : ""}">${count ? `${count} 题` : "—"}</small></button></li>`;
  }).join("")}</ul>${beyond.length ? `<p class="muted">日历范围外另有 ${beyond.length} 题</p>` : ""}</section>`;
  const results = reviewState.results.length ? `<section class="review-results" aria-live="polite"><div class="section-title"><h2>本次复习反馈</h2><span class="tag published">完成 ${reviewState.results.length} 题</span></div>${reviewState.results.map(result => `<div class="review-result ${result.correct ? "correct" : "incorrect"}"><b>${result.correct ? "答对了" : "再巩固一下"} · ${esc(result.question_snapshot.text)}</b><p>正确答案：${esc(result.question_snapshot.options[result.answer])}</p>${result.question_snapshot.explanation ? `<p>${esc(result.question_snapshot.explanation)}</p>` : ""}<small>${result.status === "mastered" ? "本题已掌握，已退出复习队列。" : `下次复习 ${day(result.next_review_at)}，间隔 ${result.review_interval_days} 天。`}</small></div>`).join("")}</section>` : "";
  layout(`<div class="page-heading"><div><h1>错题与复习</h1></div><button class="ghost" id="refresh">${icon("refresh")}刷新</button></div><div class="review-stats">${counts.map(([count,label,graphic]) => `<div class="analytics-card"><span class="stat-icon">${icon(graphic)}</span><b>${count}</b><span>${label}</span></div>`).join("")}</div>${calendar}<section class="review-workspace"><div class="review-toolbar"><div class="review-tabs" role="tablist" aria-label="错题与复习视图">${[["today","今日复习",dueToday],["all","全部错题",items.length],["mastered","已掌握",mastered.length]].map(([tab,label,count]) => `<button type="button" role="tab" id="review-tab-${tab}" data-tab="${tab}" aria-selected="${reviewState.tab === tab}" aria-controls="review-content" tabindex="${reviewState.tab === tab ? 0 : -1}">${label}<span>${count}</span></button>`).join("")}</div><div class="review-filters"><label>课程<select id="course-filter"><option value="all">全部课程</option>${[...names].map(([id,name]) => `<option value="${esc(id)}" ${reviewState.course === id ? "selected" : ""}>${esc(name)}</option>`).join("")}</select></label><label>章节<select id="chapter-filter"><option value="all">全部章节</option>${chapters.map(chapter => `<option ${reviewState.chapter === chapter ? "selected" : ""}>${esc(chapter)}</option>`).join("")}</select></label><label>知识点<select id="point-filter"><option value="all">全部知识点</option>${[...points].map(([id, point]) => `<option value="${esc(id)}" ${reviewState.point === id ? "selected" : ""}>${esc(point)}</option>`).join("")}</select></label><a class="secondary" href="${esc(exportUrl)}" download>${icon("arrow")}导出 CSV</a></div></div><div id="review-content" role="tabpanel" aria-labelledby="review-tab-${reviewState.tab}" class="review-content">${body}</div></section>${results}${reviewState.tab === "today" && upcoming.length ? `<section><div class="section-title"><h2>接下来的复习安排</h2><span class="muted">还有 ${upcoming.length} 题未到期</span></div><div class="record-list">${upcoming.map(item => `<div class="record-row"><div><b>${esc(item.question_snapshot.text)}</b><small>${esc(names.get(item.course_id))} · ${esc(item.knowledge_point_name)}</small></div><span class="tag neutral">${day(item.next_review_at)}</span></div>`).join("")}</div></section>` : ""}`, "错题与复习");
  const reload = () => reviewCenter(ctx).catch(error => tell(error.message));
  root.querySelectorAll("[data-tab]").forEach(button => { button.onclick = () => {
    reviewState.tab = button.dataset.tab;
    reload().then(() => root.querySelector(`#review-tab-${reviewState.tab}`)?.focus({preventScroll:true}));
  }; });
  root.querySelector(".review-tabs").onkeydown = event => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    const buttons = [...root.querySelectorAll("[data-tab]")], index = buttons.indexOf(document.activeElement);
    if (index < 0) return;
    event.preventDefault();
    const next = buttons[(index + (event.key === "ArrowRight" ? 1 : buttons.length-1)) % buttons.length];
    reviewState.tab = next.dataset.tab;
    reviewCenter(ctx).then(() => root.querySelector(`[data-tab="${reviewState.tab}"]`).focus()).catch(error => tell(error.message));
  };
  root.querySelector("#course-filter").onchange = event => { reviewState.course = event.target.value; reviewState.chapter = "all"; reviewState.point = "all"; reload(); };
  root.querySelector("#chapter-filter").onchange = event => { reviewState.chapter = event.target.value; reviewState.point = "all"; reload(); };
  root.querySelector("#point-filter").onchange = event => { reviewState.point = event.target.value; reload(); };
  root.querySelectorAll("[data-day]").forEach(button => { button.onclick = () => {
    reviewState.calDay = button.dataset.day;
    reviewState.tab = "today";
    reload().then(() => root.querySelector(".calendar-day[aria-pressed=true]")?.focus({preventScroll:true}));
  }; });
  root.querySelectorAll(".master-btn").forEach(button => { button.onclick = async () => {
    button.disabled = true;
    try {
      const updated = await post(`/api/wrong-questions/${encodeURIComponent(button.dataset.id)}/mastered`);
      reviewState.results = reviewState.results.map(result => result.id === updated.id ? {...result, ...updated} : result);
      await reviewCenter(ctx); tell("已标记为掌握，本题不再进入复习队列");
    }
    catch(error) { tell(error.message); button.disabled = false; }
  }; });
  root.querySelectorAll(".review-question").forEach(form => { form.onsubmit = async event => {
    event.preventDefault();
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      const selected = Number(new FormData(form).get("selected"));
      const result = await post(`/api/wrong-questions/${encodeURIComponent(form.dataset.reviewId)}/review`, {selected});
      reviewState.results.unshift(result);
      await reviewCenter(ctx);
    } catch(error) { tell(error.message); button.disabled = false; }
  }; });
  root.querySelector("#refresh").onclick = reload;
  if (initialFilters?.focus) {
    const question = root.querySelector(`[data-review-id="${CSS.escape(initialFilters.focus)}"]`);
    question?.classList.add("is-focus");
    question?.scrollIntoView({block:"center"});
  }
}
