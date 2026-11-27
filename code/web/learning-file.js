import {esc, icon, number, radarCard} from "/static/ui.js";
import {courseLabel} from "/static/teaching-courses.js";
import {bindLearningPlan, learningPlanPanel} from "/static/learning-plan.js";

function renderPortrait(portrait) {
  if (!portrait) return "";
  const {investment, knowledge_points:points, rhythm, metacognition, reading, events} = portrait;
  const categories = portrait.categories?.length ? portrait.categories : portrait.dimensions || [];
  const cards = categories.map(item => `<article class="portrait-dim" data-category="${esc(item.id)}">
    <h3>${esc(item.label)}</h3><strong>${esc(item.value)}</strong><p>${esc(item.detail)}</p></article>`).join("");
  const section = (label, content, value = "") => `<details class="section-disclosure portrait-section">
    <summary>${label}${value ? `<span class="tag neutral">${esc(value)}</span>` : ""}</summary>
    <div class="portrait-section-content">${content}</div></details>`;
  const bar = (label, value) => `<progress class="investment-track" max="100" value="${value ?? 0}" aria-label="${esc(label)}"></progress>`;
  const investmentRows = investment.parts.map(item => `<div class="investment-row">
    <b>${esc(item.label)} <small>权重 ${item.weight}%</small></b>
    ${bar(item.label, item.value)}<strong class="investment-value">${item.value ?? "暂无"}</strong>
    <small class="muted">${esc(item.detail)}</small></div>`).join("");
  const reviewRows = (points || []).map(item => `<div class="point-row${item.due ? " is-due" : ""}">
    <strong class="point-name">${esc(item.name)}</strong><span>正确率 ${item.accuracy}%</span><span>连对 ${item.streak}</span>
    <span class="tag ${item.mastered ? "published" : "neutral"}">${item.mastered ? "已掌握" : `间隔 ${item.interval_days} 天`}</span>
    <span class="${item.due ? "tag attention" : "muted"}">${item.due ? "今天复习" : `下次 ${esc(item.next_review_at.slice(0, 10))}`}</span></div>`).join("");
  const rhythmRows = rhythm?.dated_events ? Object.entries(rhythm.windows).map(([label, count]) => {
    const percent = Math.round(count / rhythm.dated_events * 100);
    return `<div class="investment-row"><b>${esc(label)}</b>${bar(label, percent)}
      <strong class="investment-value">${percent}%</strong><small class="muted">${count} 次</small></div>`;
  }).join("") : "";
  const weekRows = (metacognition?.weeks || []).map(week => `<div class="meta-week${week.met ? " is-met" : ""}${week.current ? " is-current" : ""}">
    <b class="meta-week-label">${esc(week.start.slice(5, 10))} 起</b>${bar(week.start + " 周达成", week.rate)}
    <strong class="investment-value">${week.rate}%</strong><small>${week.current ? "本周进行中" : week.met ? "已达标" : "未达标"} · 练习 ${week.submissions} 次 · 复习 ${week.reviews} 题</small></div>`).join("");
  const readings = (reading?.recent || []).map(item => `<span class="event-chip">${esc(item.name || "资料")}<b>${item.percent}%</b></span>`).join("");
  const eventCounts = events.by_type.map(item => `<span class="event-chip">${esc(item.label)}<b>${item.count}</b></span>`).join("");
  return `<section class="portrait-card">
    <div class="section-title"><h2>画像详情</h2></div>
    <div class="portrait-grid">${cards}</div>
    <div class="portrait-details">
      ${section("学习投入度", `<div class="investment-bars">${investmentRows}</div><p class="muted">仅对有数据的分量加权。</p>`, investment.score === null ? "暂无" : `${investment.score} 分`)}
      ${section("知识点复习", reviewRows ? `<div class="point-table">${reviewRows}</div>` : '<p class="muted">暂无复习安排</p>', `${(points || []).filter(item => item.due).length} 个待复习`)}
      ${section("学习时段", rhythmRows ? `<div class="investment-bars">${rhythmRows}</div>
        <div class="event-chips"><span class="event-chip">周末学习<b>${rhythm.weekend_ratio}%</b></span><span class="event-chip">迟交<b>${rhythm.late_count} 次</b></span>
        <span class="muted">累计 ${rhythm.dated_events} 次</span></div>` : '<p class="muted">暂无学习记录</p>', rhythm?.label)}
      ${section("每周计划达成", weekRows ? `<div class="meta-weeks">${weekRows}</div>` : '<p class="muted">暂无计划记录</p>', `${metacognition?.met_count ?? 0} / ${metacognition?.closed_count ?? 0} 周`)}
      ${section("资料阅读", reading?.total ? `${bar("资料阅读完成度", reading.percent)}<div class="event-chips">${readings}</div>` : '<p class="muted">暂无课程资料</p>', reading?.total ? `${reading.done} / ${reading.total} 份` : "暂无")}
      ${section("学习活动", `<div class="event-chips">${eventCounts}<span class="muted">累计 ${events.total} 条</span></div>`, `近 7 天 ${events.last7} 条`)}
    </div></section>`;
}

export async function learningFilePage(ctx, initialTab = "overview") {
  const {api, layout, root, user, link, tell, params, majorName, recordList} = ctx;
  if (user.role !== "student") throw new Error("请使用学生账号查看自己的画像");
  const [data, logs, courses, plan, portrait] = await Promise.all([api("/api/student/profile"), api("/api/learning-records"), api("/api/courses"), api("/api/student/plan").catch(() => null), api("/api/student/portrait").catch(() => null)]);
  const profile = data.profile;
  const records = logs.map(record => ({...record, course_name:record.teaching_class ? `${record.course_name} · ${record.teaching_class}` : record.course_name}));
  const courseNames = new Map(courses.map(course => [course.id,courseLabel(course)]));
  for (const record of records) if (!courseNames.has(record.course_id)) courseNames.set(record.course_id,record.course_name || "历史课程");
  const tabs = [
    ["overview", "画像总览", "chart", "learning-overview"],
    ...(plan ? [["plan", "计划与打卡", "target", "learning-plan"]] : []),
    ["practice", "自主练习记录", "book", "learning-records"],
    ["assignments", "作业记录", "check", "learning-records"],
  ];
  const tabIds = tabs.map(([id]) => id);
  const graphTarget = params.get("graph_course") ? {course_id:params.get("graph_course"), point_id:params.get("graph_point")} : null;
  const requested = params.get("tab") || (graphTarget || params.get("panel") === "plan" ? "plan"
    : params.get("source") === "assignment" ? "assignments" : initialTab);
  let tab = tabIds.includes(requested) ? requested : initialTab;
  const filters = {course:courseNames.has(params.get("course")) ? params.get("course") : "all",
    type:"all", search:""};
  const cards = courses.map(course => {
    const entries = records.filter(record => record.course_id === course.id && record.type !== "ai_quiz");
    const attempted = new Set(entries.flatMap(record => [record.chapter_id,...(record.details || []).map(detail=>detail.chapter_id)]).filter(Boolean));
    const practiced = course.chapters.filter(chapter=>attempted.has(chapter.id)).length;
    const scores = entries.filter(record=>!record.pending_release && typeof record.score === "number").map(record=>record.score);
    return `<article class="learning-course-card"><div><span class="eyebrow">${esc(course.course_code || "课程学习")}</span><h3>${esc(course.title)}</h3><p class="muted">${esc(course.teaching_class || "默认教学班")} · ${esc(course.teacher)}</p></div><div class="learning-course-progress"><span>已练习 ${practiced} / ${course.chapters.length} 章</span><span>自主练习 ${entries.filter(r=>!r.assignment_id).length} 次 · 作业提交 ${entries.filter(r=>r.assignment_id).length} 次</span></div><progress max="${Math.max(1,course.chapters.length)}" value="${practiced}" aria-label="${esc(courseLabel(course))}章节练习进度"></progress>
      <div class="learning-course-result"><span>${scores.length ? `已公布最高分 ${Math.max(...scores)} 分` : "暂无已公布成绩"}</span></div>
      <div class="actions">${link("knowledge-graph", "知识图谱", "secondary small", {course:course.id})}
      <button class="ghost small" data-course-records="${esc(course.id)}">自主练习记录</button></div></article>`;
  }).join("");
  const stats = [[profile.course_count,"已参与教学班"],[records.filter(record => !record.assignment_id && record.type !== "ai_quiz").length,"自主练习次数"],
    [records.filter(record => record.assignment_id).length,"作业提交次数"],[number(profile.average_score),"平均成绩"]];
  const portraitBlock = renderPortrait(portrait);
  const planBadge = plan?.today.plan_total ? `<span>${plan.today.plan_done}/${plan.today.plan_total}</span>` : "";
  layout(`
    <div class="page-heading learning-file-heading">
      <div><h1>学生画像</h1><p class="muted">${esc(user.name)} · ${esc(majorName(user.major_id))}</p></div>
      <button class="ghost" id="learning-file-refresh">${icon("refresh")}刷新</button>
    </div>
    <div class="catalog-stats learning-file-stats">${stats.map(([n,label]) => `<div><b>${n}</b><span>${label}</span></div>`).join("")}</div>
    <div class="learning-file-tabs" role="tablist" aria-label="学生画像">
      ${tabs.map(([id, label, graphic, panel]) => {
        const count = panel === "learning-records" ? records.filter(record => Boolean(record.assignment_id) === (id === "assignments")).length : null;
        return `<button type="button" role="tab" id="learning-${id}-tab" data-learning-tab="${id}" aria-selected="${tab === id}" aria-controls="${panel}">
          ${icon(graphic)}${label}${id === "plan" ? planBadge : count === null ? "" : `<span>${count}</span>`}</button>`;
      }).join("")}
    </div>
    <div id="learning-overview" role="tabpanel" aria-labelledby="learning-overview-tab" ${tab === "overview" ? "" : "hidden"}>
      <section class="personal-profile">${radarCard(profile,true)}</section>
      <section><div class="section-title"><h2>课程进度</h2></div>
        <div class="learning-course-grid">${cards || '<div class="empty">尚未加入已发布的教学班。</div>'}</div></section>
      ${portraitBlock}
    </div>
    ${plan ? `<div id="learning-plan" role="tabpanel" aria-labelledby="learning-plan-tab" ${tab === "plan" ? "" : "hidden"}>${learningPlanPanel(plan, link)}</div>` : ""}
    <div id="learning-records" role="tabpanel" aria-labelledby="learning-practice-tab" ${["practice","assignments"].includes(tab) ? "" : "hidden"}>
      <div class="learning-record-toolbar">
        <label>课程 / 教学班<select id="learning-record-course"><option value="all">全部课程与自测</option>${[...courseNames].map(([id,name]) => `<option value="${esc(id)}">${esc(name)}</option>`).join("")}</select></label>
        <label>记录类型<select id="learning-record-type"><option value="all">全部类型</option><option value="choice">单选题</option><option value="python">Python 编程</option><option value="ai_quiz">视频 AI 自测</option></select></label>
        <label>搜索<input type="search" id="learning-record-search" placeholder="课程或章节名称"></label>
      </div>
      <div class="section-title"><span class="muted" id="learning-record-count" role="status"></span><button class="ghost small" id="learning-record-clear">清除筛选</button></div>
      <div id="learning-record-list"></div><div class="catalog-pagination" id="learning-record-pagination"></div>
      <details class="section-disclosure"><summary>统计与答案规则</summary><p class="muted">视频 AI 自测不计入课程成绩和雷达图。作业用完提交次数后开放答案、解析和 AI 分析。</p></details>
    </div>`, "学生画像");
  let recordPage = 1;
  function renderRecords() {
    const selected = records.filter(record => Boolean(record.assignment_id) === (tab === "assignments") && (filters.course === "all" || record.course_id === filters.course)
      && (filters.type === "all" || (record.type === "python" || record.type === "ai_quiz" ? record.type : "choice") === filters.type)
      && `${record.course_name} ${record.chapter_title || ""}`.toLowerCase().includes(filters.search.trim().toLowerCase()));
    const pages = Math.max(1,Math.ceil(selected.length/10)); recordPage=Math.min(recordPage,pages);
    root.querySelector("#learning-record-count").textContent = `${selected.length} 条记录`;
    root.querySelector("#learning-record-list").innerHTML=recordList(selected.slice((recordPage-1)*10,recordPage*10),false);
    root.querySelector("#learning-record-pagination").innerHTML=pages>1?`<button class="ghost" id="records-previous" ${recordPage===1?"disabled":""}>上一页</button><span>${recordPage} / ${pages}</span><button class="ghost" id="records-next" ${recordPage===pages?"disabled":""}>下一页</button>`:"";
    for(const [id,delta] of [["records-previous",-1],["records-next",1]]) root.querySelector(`#${id}`)?.addEventListener("click",()=>{recordPage+=delta;renderRecords();});
  }
  function showTab(next) {
    tab = next;
    recordPage = 1;
    const recordsPanel = root.querySelector("#learning-records");
    const openRecords = tab === "practice" || tab === "assignments";
    root.querySelector("#learning-overview").hidden = tab !== "overview";
    if (plan) root.querySelector("#learning-plan").hidden = tab !== "plan";
    recordsPanel.hidden = !openRecords;
    if (openRecords) recordsPanel.setAttribute("aria-labelledby", `learning-${tab}-tab`);
    for (const button of root.querySelectorAll("[data-learning-tab]")) {
      const active = button.dataset.learningTab === tab;
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
    }
    if (openRecords) renderRecords();
    params.set("tab", tab);
    const url = new URL(location.href);
    url.searchParams.set("tab", tab);
    url.searchParams.delete("source");
    url.searchParams.delete("panel");
    history.replaceState(null, "", url);
  }
  root.querySelectorAll("[data-learning-tab]").forEach(button=>{button.onclick=()=>showTab(button.dataset.learningTab);});
  root.querySelector(".learning-file-tabs").onkeydown = event => {
    const current = tabIds.indexOf(tab);
    const direction = {ArrowLeft:-1, ArrowRight:1}[event.key];
    if (direction === undefined && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    const selected = event.key === "Home" ? 0
      : event.key === "End" ? tabIds.length - 1 : (current + direction + tabIds.length) % tabIds.length;
    showTab(tabIds[selected]);
    root.querySelector(`[data-learning-tab="${tab}"]`).focus();
  };
  root.querySelectorAll("[data-course-records]").forEach(button=>{button.onclick=()=>{filters.course=button.dataset.courseRecords;filters.type="all";filters.search="";root.querySelector("#learning-record-course").value=filters.course;root.querySelector("#learning-record-type").value="all";root.querySelector("#learning-record-search").value="";recordPage=1;renderRecords();showTab("practice");};});
  for(const field of ["course","type"])root.querySelector(`#learning-record-${field}`).onchange=event=>{filters[field]=event.target.value;recordPage=1;renderRecords();};
  root.querySelector("#learning-record-search").oninput=event=>{filters.search=event.target.value;recordPage=1;renderRecords();};
  root.querySelector("#learning-record-clear").onclick=()=>{filters.course="all";filters.type="all";filters.search="";root.querySelector("#learning-record-course").value="all";root.querySelector("#learning-record-type").value="all";root.querySelector("#learning-record-search").value="";recordPage=1;renderRecords();};
  root.querySelector("#learning-file-refresh").onclick=()=>learningFilePage(ctx,tab).catch(error=>tell(error.message));
  root.querySelector("#learning-record-course").value=filters.course;
  root.querySelector("#learning-record-type").value=filters.type;
  showTab(tab);
  bindLearningPlan(ctx, plan, tab === "plan" ? graphTarget : null);
}
