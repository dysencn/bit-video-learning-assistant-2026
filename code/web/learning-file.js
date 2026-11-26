import {esc, icon, number, radarCard, progressBar} from "/static/ui.js";
import {courseLabel} from "/static/teaching-courses.js";

export async function learningFilePage(ctx, initialTab = "overview") {
  const {api, post, layout, root, user, link, tell, params, majorName, recordList, date} = ctx;
  if (user.role !== "student") throw new Error("请使用学生账号查看自己的学习档案");
  const [data, logs, courses, plan, portrait] = await Promise.all([api("/api/student/profile"), api("/api/learning-records"), api("/api/courses"), api("/api/student/plan").catch(() => null), api("/api/student/portrait").catch(() => null)]);
  const profile = data.profile;
  const records = logs.map(record => ({...record, course_name:record.teaching_class ? `${record.course_name} · ${record.teaching_class}` : record.course_name}));
  const courseNames = new Map(courses.map(course => [course.id,courseLabel(course)]));
  for (const record of records) if (!courseNames.has(record.course_id)) courseNames.set(record.course_id,record.course_name || "历史课程");
  const requested = params.get("tab") || initialTab;
  let tab = ["overview","practice","assignments"].includes(requested) ? requested : params.get("source") === "assignment" ? "assignments" : "practice";
  const filters = {course:courseNames.has(params.get("course")) ? params.get("course") : "all",
    type:"all", search:""};
  const cards = courses.map(course => {
    const entries = records.filter(record => record.course_id === course.id && record.type !== "ai_quiz");
    const attempted = new Set(entries.flatMap(record => [record.chapter_id,...(record.details || []).map(detail=>detail.chapter_id)]).filter(Boolean));
    const practiced = course.chapters.filter(chapter=>attempted.has(chapter.id)).length;
    const scores = entries.filter(record=>!record.pending_release && typeof record.score === "number").map(record=>record.score);
    return `<article class="learning-course-card"><div><span class="eyebrow">${esc(course.course_code || "课程学习")}</span><h3>${esc(course.title)}</h3><p class="muted">${esc(course.teaching_class || "默认教学班")} · ${esc(course.teacher)}</p></div><div class="learning-course-progress"><span>已练习 ${practiced} / ${course.chapters.length} 章</span><span>自主练习 ${entries.filter(r=>!r.assignment_id).length} 次 · 作业提交 ${entries.filter(r=>r.assignment_id).length} 次</span></div><progress max="${Math.max(1,course.chapters.length)}" value="${practiced}" aria-label="${esc(courseLabel(course))}章节练习进度"></progress><div class="learning-course-result"><span>${scores.length ? `已公布最高分 ${Math.max(...scores)} 分` : "暂无已公布成绩"}</span><button class="ghost small" data-course-records="${esc(course.id)}">查看此课记录</button></div></article>`;
  }).join("");
  const stats = [[profile.course_count,"已参与教学班"],[profile.submission_count,"课程作答"],[number(profile.average_score),"平均成绩"],[profile.weak_points.length,"待巩固知识点"]];
  const goalBar = (label, value, goal, rate) => `<div class="plan-goal"><div class="plan-goal-head"><span>${label}</span><b>${value} / ${goal}</b></div>${progressBar(rate, `${label} ${value} / ${goal}（${rate}%）`)}<small class="muted">已完成 ${rate}%</small></div>`;
  const planGoal = plan?.plan || null;
  const taskKindLabel = {resource:"资料", knowledge:"知识点", practice:"练习", code:"编程", review:"复习"};
  const taskHint = {resource:"读完这份资料", knowledge:"把这个知识点的题做一遍", practice:"提交这一组练习",
    code:"提交编程题并拿到通过", review:"在错题本里复习这道题"};
  const planWeekCounts = summary => { const c = summary?.counts || {};
    return `资料 ${c.resource || 0} 份 · 知识点 ${c.knowledge || 0} 个 · 练习 ${c.practice || 0} 题 · 编程 ${c.code || 0} 题 · 复习 ${c.review || 0} 题`; };
  const planDayCounts = counts => { const c = counts || {}; const bits = [];
    if (c.resource) bits.push(`资料 ${c.resource} 份`);
    if (c.knowledge) bits.push(`知识点 ${c.knowledge} 个`);
    if (c.practice) bits.push(`练习 ${c.practice} 题`);
    if (c.code) bits.push(`编程 ${c.code} 题`);
    if (c.review) bits.push(`复习 ${c.review} 题`);
    return bits.join(" · ") || "无任务"; };
  const planDayRows = (planGoal?.week?.days || []).map(row => `<div class="plan-day${row.rest ? " is-rest" : ""}${row.date === planGoal.tasks?.date ? " is-today" : ""}"><span class="plan-day-date">${esc(row.date.slice(5))} ${esc(row.weekday)}</span>${row.rest ? `<span class="muted">休息日</span><span></span><b>—</b>` : `<span class="muted">${planDayCounts(row.counts)}</span><span>${row.minutes} 分钟</span><b>${row.total ? `${row.done}/${row.total}` : "—"}</b>`}</div>`).join("");
  const planTaskLink = card => {
    const course = card.course_id ? {course: card.course_id} : {};
    if (card.kind === "review") return link("review-center", "去完成", "ghost small");
    if (card.kind === "code") return link("programming", "去完成", "ghost small", {...course, question: card.ref_id});
    if (card.kind === "resource") return link("course", "去完成", "ghost small", {...course, focus: card.ref_id});
    const firstQuestion = (card.question_ids || [])[0];
    if (card.kind === "knowledge" || card.kind === "practice") return link("course", "去完成", "ghost small", {...course, ...(firstQuestion ? {focus: firstQuestion} : {})});
    return link("learning-center", "去完成", "ghost small");
  };
  const planTaskRows = (planGoal?.tasks?.cards || []).map(card => {
    const hint = card.kind === "knowledge" && card.count ? "把这个知识点的 " + card.count + " 道题都做完" : (taskHint[card.kind] || "");
    const extra = card.kind === "knowledge" && card.count ? " · " + card.count + " 题" : "";
    return `<li class="plan-task${card.done ? " is-done" : ""}"><span class="plan-task-kind">${esc(taskKindLabel[card.kind] || "任务")}</span><span class="plan-task-title">${esc(card.title)}${extra}</span><small class="muted">${card.minutes} 分钟 · ${esc(hint)}</small><span class="plan-task-state">${card.done ? "已完成" : planTaskLink(card)}</span></li>`;
  }).join("");
  const planGoalBlock = `<div class="plan-goal-plan"><div class="section-title"><div><span class="eyebrow">GOAL TO TASKS</span><h2>目标与今日任务</h2><p class="muted">用一句话说出目标，平台折算成每周与每天的任务；完成情况按真实提交与复习记录回算，不做手工打勾。</p></div>${planGoal?.goal ? `<span class="tag published">${esc(planGoal.goal.parsed.deadline)} 前</span>` : ""}</div>
    ${planGoal?.goal ? `<div class="plan-goal-summary"><p><b>目标</b>${esc(planGoal.goal.text)}</p><div class="plan-goal-metrics"><span>周期 <b>${planGoal.goal.parsed.weeks} 周</b></span><span>每周可投入 <b>${planGoal.goal.parsed.weekly_minutes} 分钟</b></span><span>可学天数 <b>${planGoal.goal.parsed.allowed_days} 天</b></span><span>工作量 <b>${planGoal.workload?.minutes ?? 0} 分钟</b></span><span>预算占比 <b>${Math.round((planGoal.verdict?.ratio || 0) * 100)}%</b></span></div><p class="plan-verdict ${esc(planGoal.verdict?.verdict || "")}">${esc(planGoal.verdict?.advice || "")}</p></div>
      <div class="plan-week"><div class="section-title"><div><h3>本周任务</h3><p class="muted">第 ${planGoal.week?.index ?? 1} 周 · ${esc(planGoal.week?.start || "")} 至 ${esc(planGoal.week?.end || "")} · 计划 ${planGoal.week?.minutes ?? 0} 分钟 · ${planGoal.week?.count ?? 0} 项任务</p></div></div><p class="plan-week-counts">${planWeekCounts(planGoal.week?.summary)}</p>${(planGoal.week?.summary?.points || []).length ? `<p class="muted plan-week-points">涉及知识点：${esc((planGoal.week.summary.points || []).join("、"))}</p>` : ""}<div class="plan-day-grid">${planDayRows}</div></div><div class="plan-today"><div class="section-title"><div><h3>今日任务</h3><p class="muted">${esc(planGoal.tasks?.date || "")} · 计划 ${planGoal.tasks?.planned_minutes ?? 0} 分钟，已完成 ${planGoal.tasks?.done_minutes ?? 0} 分钟 · 本周第 ${planGoal.week?.index ?? 1} 周（${esc(planGoal.week?.start || "")} 至 ${esc(planGoal.week?.end || "")}）</p></div><span class="tag ${planGoal.tasks?.total && planGoal.tasks.done === planGoal.tasks.total ? "published" : "attention"}">${planGoal.tasks?.done ?? 0} / ${planGoal.tasks?.total ?? 0}</span></div>${planTaskRows ? `<ul class="plan-task-list">${planTaskRows}</ul>` : `<p class="muted">今天没有排到任务：目标范围里的内容已经做完，可以重新设一个更长或更大的目标。</p>`}<small class="muted">今日完成率 ${planGoal.tasks?.rate ?? 0}%${(planGoal.tasks?.rate ?? 0) >= 100 ? " · 今日已打卡" : " · 完成全部任务才算打卡"} · 本周计划 ${planGoal.week?.minutes ?? 0} 分钟</small></div>` : `<p class="muted">还没有设定目标。写一句话，平台会折算成每周与每天的任务。</p>`}
    <form class="plan-goal-form" id="plan-goal-form"><label>我的目标<textarea id="plan-goal-text" rows="2" maxlength="200" placeholder="例如：4 周内掌握 Python 的循环和函数，每周能投入 5 小时，周末不学">${esc(planGoal?.goal?.text || "")}</textarea></label><div class="plan-goal-actions"><button class="secondary" type="submit">${icon("target")}${planGoal?.goal ? "重新解析" : "解析目标"}</button>${planGoal?.goal ? `<button class="ghost" type="button" id="plan-goal-clear">清除目标</button>` : ""}</div></form><div id="plan-goal-preview" role="status"></div></div>`;
  const planBlock = plan ? `<section class="plan-board"><div class="plan-board-head"><div><span class="eyebrow">CHECK IN</span><p class="muted">${esc(plan.scope)}</p></div><div class="plan-streak"><b>${plan.streak.current}</b><span>连续学习天数</span><small>最长连续 ${plan.streak.longest} 天 · 近 30 天打卡 ${plan.calendar.filter(item => item.state === "done").length} 天</small></div></div><div class="plan-goals wide"><div class="plan-goal is-primary"><div class="plan-goal-head"><span>本周计划完成</span><b>${plan.week.plan_done ?? 0} / ${plan.week.plan_cards ?? 0} 项</b></div>${progressBar(plan.week.plan_rate ?? 0, `本周计划完成 ${plan.week.plan_done ?? 0} / ${plan.week.plan_cards ?? 0} 项（${plan.week.plan_rate ?? 0}%）`)}<small class="muted">按目标生成的每日任务回算，不做手工打勾</small></div>${goalBar("计划内复习题", plan.week.plan_review_done ?? 0, plan.week.plan_review_cards ?? 0, plan.week.plan_review_rate ?? 0)}</div><p class="plan-note muted">统计周期 ${plan.week.start} 至 ${plan.week.end}（本地自然周）· 今日已练习 ${plan.today.submissions} 次、复习 ${plan.today.reviews} 题、待复习 ${plan.today.due} 题</p><p class="plan-checkin-note${plan.today.complete ? " is-met" : ""}">${plan.today.complete ? "今日计划的全部任务都完成了，打卡成功" : `今日计划完成 ${plan.today.plan_done ?? 0} / ${plan.today.plan_total ?? 0} 项，全部完成才算打卡`}</p><div class="checkin-grid">${plan.calendar.map(item => `<div class="checkin-cell${item.state === "done" ? " is-active" : ""}${item.state === "partial" ? " is-partial" : ""}${item.state === "outside" ? " is-outside" : ""}${item.today ? " is-today" : ""}" title="${item.date} · ${item.has_plan ? `计划 ${item.plan_done}/${item.plan_total} 项` : item.state === "outside" ? `当天无计划 · 练习 ${item.submissions} 次、复习 ${item.reviews} 题` : "当天无计划、无学习记录"}"><span>${item.label}</span><i>${item.state === "done" || item.state === "partial" ? item.plan_done : item.state === "outside" ? item.submissions + item.reviews : ""}</i></div>`).join("")}</div><div class="checkin-legend"><span><i class="is-active"></i>完成当日计划（打卡）</span><span><i class="is-partial"></i>计划未完成</span><span><i class="is-outside"></i>当天无计划（数字为练习/复习次数）</span><span class="muted">最近 30 天 · 全部任务完成才算打卡</span></div></section><section class="achievement-board"><div class="section-title"><div><span class="eyebrow">ACHIEVEMENTS</span><h2>成就</h2></div><span class="muted">已解锁 ${plan.achievements.filter(item => item.unlocked).length} / ${plan.achievements.length}</span></div><div class="achievement-grid">${plan.achievements.map(item => `<div class="achievement${item.unlocked ? " is-unlocked" : ""}"><span class="achievement-mark">${icon(item.unlocked ? "check" : "target")}</span><div><b>${esc(item.title)}</b><small>${esc(item.desc)}</small></div></div>`).join("")}</div></section>` : "";
  const planPanel = plan ? `<section class="learning-plan-details" id="learning-plan"><div class="learning-plan-head">${icon("check")}<span class="learning-plan-title">打卡记录</span><span class="learning-plan-meta">今日 ${plan.today.plan_done ?? 0}/${plan.today.plan_total ?? 0} 项 · 连续 ${plan.streak.current} 天 · 本周 ${plan.week.plan_done ?? 0}/${plan.week.plan_cards ?? 0} 项</span></div>${planGoalBlock}${planBlock}</section>` : "";
  const portraitParts = portrait?.investment?.parts || [];
  const portraitCategories = portrait?.categories || [];
  const knowledgeRows = portrait?.knowledge_points || [];
  const rhythm = portrait?.rhythm, meta = portrait?.metacognition, reading = portrait?.reading;
  const catItems = portraitCategories.length ? portraitCategories : (portrait?.dimensions || []);
  const share = count => Math.round(count / Math.max(1, rhythm?.dated_events || 1) * 100);
  const catGrid = `<div class="portrait-grid">${catItems.map(item => `<article class="portrait-dim" data-category="${esc(item.id)}"><span class="eyebrow">${esc(item.id)}</span><b>${esc(item.label)}</b><h3>${esc(item.value)}</h3><p class="muted">${esc(item.detail)}</p></article>`).join("")}</div>`;
  const knowledgeBlock = `<div class="portrait-section"><div class="section-title"><div><span class="eyebrow">REVIEW SCHEDULE</span><h2>知识点复习调度</h2><p class="muted">按遗忘曲线排期：连续答对越多间隔越长，正确率低于 50% 的知识点退回上一档。</p></div><span class="muted">${knowledgeRows.filter(item => item.due).length} 个待复习</span></div>${knowledgeRows.length ? `<div class="point-table">${knowledgeRows.map(item => `<div class="point-row${item.due ? " is-due" : ""}"><span class="point-name">${esc(item.name)}</span><span class="muted">正确率 ${item.accuracy}%</span><span class="muted">连对 ${item.streak}</span><span class="tag ${item.mastered ? "published" : ""}">${item.mastered ? "已掌握" : `间隔 ${item.interval_days} 天`}</span><span class="muted">${item.due ? "今天该复习" : `下次 ${item.next_review_at.slice(0, 10)}`}</span></div>`).join("")}</div>` : `<p class="muted">完成练习后，知识点会按遗忘曲线自动排进复习。</p>`}</div>`;
  const rhythmBlock = `<div class="portrait-section"><div class="section-title"><div><span class="eyebrow">RHYTHM</span><h2>学习节律</h2><p class="muted">从每次学习动作的时间戳统计"什么时候学"，再从迟交看"是否赶截止"。</p></div><span class="tag published">${esc(rhythm?.label || "暂无")}</span></div>${rhythm && rhythm.dated_events ? `<div class="investment-bars">${Object.entries(rhythm.windows).map(([label, count]) => `<div class="investment-row"><span class="investment-label">${esc(label)}</span><progress class="investment-track" max="100" value="${share(count)}" aria-label="${esc(label)} ${share(count)}%"></progress><span class="investment-value">${count}</span><small class="muted">占 ${share(count)}%</small></div>`).join("")}</div><div class="event-chips"><span class="event-chip">周末学习<b>${rhythm.weekend_ratio}%</b></span><span class="event-chip">迟交<b>${rhythm.late_count} 次</b></span><span class="muted">累计 ${rhythm.dated_events} 次学习动作</span></div>` : `<p class="muted">开始提交练习、复习或抢答后自动统计。</p>`}</div>`;
  const metaBlock = `<div class="portrait-section"><div class="section-title"><div><span class="eyebrow">PLAN REVIEW</span><h2>计划达成</h2><p class="muted">把"设了目标"和"做到了"放在一起看：两项目标都完成才算达标一周。</p></div><span class="muted">已结束 ${meta?.closed_count ?? 0} 周中达标 ${meta?.met_count ?? 0} 周</span></div>${meta ? `<div class="meta-weeks">${meta.weeks.map(item => `<div class="meta-week${item.met ? " is-met" : ""}${item.current ? " is-current" : ""}"><span class="meta-week-label">${item.start.slice(5, 10)} 起</span><progress class="investment-track" max="100" value="${item.rate}" aria-label="${item.start} 周达成 ${item.rate}%"></progress><span class="investment-value">${item.rate}%</span><small class="muted">${item.current ? "本周进行中" : item.met ? "达标" : "未达标"} · 练习 ${item.submissions} 次 · 复习 ${item.reviews} 题</small></div>`).join("")}</div>` : ""}</div>`;
  const readingBlock = `<div class="portrait-section"><div class="section-title"><div><span class="eyebrow">READING</span><h2>学习资料阅读</h2><p class="muted">用资料阅读进度替代视频进度：读到 60% 才算读过一份，打开页面本身不算。</p></div><span class="muted">${reading ? `${reading.done} / ${reading.total} 份` : "暂无资料"}</span></div>${reading && reading.total ? `<progress class="investment-track" max="100" value="${reading.percent ?? 0}" aria-label="资料阅读完成度 ${reading.percent ?? 0}%"></progress><small class="muted">完成度 ${reading.percent}%</small>${reading.recent.length ? `<div class="event-chips">${reading.recent.map(item => `<span class="event-chip">${esc(item.name || "资料")}<b>${item.percent}%</b></span>`).join("")}</div>` : ""}` : `<p class="muted">课程发布学习资料后开始统计。</p>`}</div>`;
  const portraitBlock = portrait ? `<section class="portrait-card"><div class="portrait-head"><div><span class="eyebrow">STUDENT PORTRAIT</span><h2>画像总览</h2><p class="muted">${esc(portrait.headline)}</p></div><span class="tag published">8 个平行类别</span></div>${catGrid}<div class="portrait-section"><div class="section-title"><div><span class="eyebrow">INVESTMENT</span><h2>学习投入度 ${portrait.investment.score === null ? "暂无" : portrait.investment.score}</h2></div><span class="muted">四分量加权</span></div><div class="investment-bars">${portraitParts.map(item => `<div class="investment-row"><span class="investment-label">${esc(item.label)}<small>权重 ${item.weight}%</small></span><progress class="investment-track" max="100" value="${item.value ?? 0}" aria-label="${esc(item.label)} ${item.value ?? 0}%"></progress><span class="investment-value">${item.value === null ? "暂无" : item.value}</span><small class="muted">${esc(item.detail)}</small></div>`).join("")}</div><small class="muted">缺失的分量不计入权重，不把「没有数据」当成「表现差」。</small></div>${knowledgeBlock}${rhythmBlock}${metaBlock}${readingBlock}<div class="event-chips">${portrait.events.by_type.map(item => `<span class="event-chip">${esc(item.label)}<b>${item.count}</b></span>`).join("")}<span class="muted">近 7 天 ${portrait.events.last7} 条 · 累计 ${portrait.events.total} 条</span></div></section>` : "";
  layout(`<div class="page-heading learning-file-heading"><div><span class="eyebrow">STUDENT PORTRAIT</span><h1>学生画像</h1><p class="muted">${esc(user.name)} · ${esc(majorName(user.major_id))}</p></div><button class="ghost" id="learning-file-refresh">${icon("refresh")}刷新画像</button></div><div class="catalog-stats learning-file-stats">${stats.map(([n,label])=>`<div><b>${n}</b><span>${label}</span></div>`).join("")}</div>
    ${planPanel}<div class="learning-file-tabs" role="tablist" aria-label="学生画像视图"><button role="tab" id="learning-overview-tab" data-learning-tab="overview" aria-selected="${tab === "overview"}" aria-controls="learning-overview">${icon("chart")}画像与课程进度</button>${[["practice","自主练习记录"],["assignments","作业记录"]].map(([value,label])=>`<button role="tab" id="learning-${value}-tab" data-learning-tab="${value}" aria-selected="${tab === value}" aria-controls="learning-records">${icon("book")}${label}<span>${records.filter(record=>Boolean(record.assignment_id)===(value==="assignments")).length}</span></button>`).join("")}</div>
    <div id="learning-overview" role="tabpanel" aria-labelledby="learning-overview-tab"><section class="personal-profile">${radarCard(profile,true)}</section>${portraitBlock}<div class="learning-followup"><div><b>${icon("target")}下一步学习建议</b><p class="muted">${profile.weak_points.length ? `优先巩固 ${esc(profile.weak_points.join("、"))}，再完成一次对应章节练习。` : "从一份章节练习开始积累数据，逐步了解自己的学习状态。"}</p></div>${link("review-center","打开错题与复习","secondary")}</div><section><div class="section-title"><div><h2>我的课程进度</h2><p class="muted">已练习章节数，不代表整门课程已学完。</p></div></div><div class="learning-course-grid">${cards || '<div class="empty">暂无面向当前专业的已发布课程。</div>'}</div></section></div>
    <div id="learning-records" role="tabpanel" aria-labelledby="learning-practice-tab"><div class="learning-record-toolbar"><label>课程 / 教学班<select id="learning-record-course"><option value="all">全部课程与自测</option>${[...courseNames].map(([id,name])=>`<option value="${esc(id)}">${esc(name)}</option>`).join("")}</select></label><label>记录类型<select id="learning-record-type"><option value="all">全部类型</option><option value="choice">全部单选练习 / 作业</option><option value="python">Python 编程</option><option value="ai_quiz">视频 AI 自测</option></select></label><label>搜索<input type="search" id="learning-record-search" placeholder="搜索课程或章节名称"></label></div><div class="section-title"><span class="muted" id="learning-record-count" role="status"></span><button class="ghost small" id="learning-record-clear">清除筛选</button></div><div id="learning-record-list"></div><div class="catalog-pagination" id="learning-record-pagination"></div><p class="catalog-footnote muted">视频 AI 自测单独保存，不计入课程成绩和雷达图。作业还有提交次数时只显示得分；用完次数后开放答案、解析和 AI 分析。</p></div>`, "学生画像");
  let recordPage = 1;
  function renderRecords() {
    const selected = records.filter(record => Boolean(record.assignment_id) === (tab === "assignments") && (filters.course === "all" || record.course_id === filters.course)
      && (filters.type === "all" || (record.type === "python" || record.type === "ai_quiz" ? record.type : "choice") === filters.type)
      && `${record.course_name} ${record.chapter_title || ""}`.toLowerCase().includes(filters.search.trim().toLowerCase()));
    const pages = Math.max(1,Math.ceil(selected.length/10)); recordPage=Math.min(recordPage,pages);
    root.querySelector("#learning-record-count").textContent=`${tab === "assignments" ? "作业" : "自主练习"} · ${selected.length} 条记录 · 按提交时间排序`;
    root.querySelector("#learning-record-list").innerHTML=recordList(selected.slice((recordPage-1)*10,recordPage*10),false);
    root.querySelector("#learning-record-pagination").innerHTML=pages>1?`<button class="ghost" id="records-previous" ${recordPage===1?"disabled":""}>上一页</button><span>${recordPage} / ${pages}</span><button class="ghost" id="records-next" ${recordPage===pages?"disabled":""}>下一页</button>`:"";
    for(const [id,delta] of [["records-previous",-1],["records-next",1]]) root.querySelector(`#${id}`)?.addEventListener("click",()=>{recordPage+=delta;renderRecords();});
  }
  function showTab(next) {
    tab=next;recordPage=1;
    root.querySelector("#learning-overview").hidden=tab!=="overview";
    root.querySelector("#learning-records").hidden=tab==="overview";
    root.querySelector("#learning-records").setAttribute("aria-labelledby",`learning-${tab==="assignments" ? "assignments" : "practice"}-tab`);
    for(const value of ["overview","practice","assignments"]) {
      const button=root.querySelector(`[data-learning-tab="${value}"]`);button.setAttribute("aria-selected",String(tab===value));button.tabIndex=tab===value?0:-1;
    }
    renderRecords();
    params.set("tab",tab); const url=new URL(location.href);url.searchParams.set("tab",tab);url.searchParams.delete("source");history.replaceState(null,"",url);
  }
  root.querySelectorAll("[data-learning-tab]").forEach(button=>{button.onclick=()=>showTab(button.dataset.learningTab);});
  root.querySelector(".learning-file-tabs").onkeydown=event=>{
    if(!["ArrowLeft","ArrowRight"].includes(event.key))return;event.preventDefault();showTab(["overview","practice","assignments"][( ["overview","practice","assignments"].indexOf(tab)+(event.key==="ArrowRight"?1:2))%3]);root.querySelector(`[data-learning-tab="${tab}"]`).focus();
  };
  root.querySelectorAll("[data-course-records]").forEach(button=>{button.onclick=()=>{filters.course=button.dataset.courseRecords;filters.type="all";filters.search="";root.querySelector("#learning-record-course").value=filters.course;root.querySelector("#learning-record-type").value="all";root.querySelector("#learning-record-search").value="";recordPage=1;renderRecords();showTab("practice");};});
  for(const field of ["course","type"])root.querySelector(`#learning-record-${field}`).onchange=event=>{filters[field]=event.target.value;recordPage=1;renderRecords();};
  root.querySelector("#learning-record-search").oninput=event=>{filters.search=event.target.value;recordPage=1;renderRecords();};
  root.querySelector("#learning-record-clear").onclick=()=>{filters.course="all";filters.type="all";filters.search="";root.querySelector("#learning-record-course").value="all";root.querySelector("#learning-record-type").value="all";root.querySelector("#learning-record-search").value="";recordPage=1;renderRecords();};
  root.querySelector("#learning-file-refresh").onclick=()=>learningFilePage(ctx,tab).catch(error=>tell(error.message));
  const planPanelEl = root.querySelector("#learning-plan");
  if (planPanelEl && params.get("panel") === "plan") planPanelEl.scrollIntoView({block: "start"});
  const goalForm = root.querySelector("#plan-goal-form");
  const goalPreview = root.querySelector("#plan-goal-preview");
  const sendJson = (url, method, body) => api(url, {method,
    headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const renderGoalPreview = data => {
    const {parsed, workload, verdict} = data;
    const kinds = Object.entries(workload.by_kind || {}).map(([kind, minutes]) => `${taskKindLabel[kind] || kind} ${minutes} 分钟`).join(" · ");
    goalPreview.innerHTML = `<div class="plan-goal-preview"><div class="plan-goal-metrics"><span>周期 <b>${parsed.weeks} 周</b>（到 ${esc(parsed.deadline)}）</span><span>每周可投入 <b>${parsed.weekly_minutes} 分钟</b></span><span>可学 <b>${parsed.allowed_days} 天/周</b></span><span>每天约 <b>${parsed.daily_capacity} 分钟</b></span><span>识别的课程 <b>${esc((data.courses || []).map(item => item.title).join("、") || "全部已加入课程")}</b></span></div><p class="muted">工作量 ${workload.minutes} 分钟（${esc(kinds || "暂无待做内容")}），预算 ${verdict.budget} 分钟，占比 ${Math.round(verdict.ratio * 100)}%。</p><p class="plan-verdict ${esc(verdict.verdict)}">${esc(verdict.advice)}</p>${(parsed.notes || []).length ? `<small class="muted">${esc(parsed.notes.join("；"))}</small>` : ""}<div class="plan-goal-actions"><button class="primary" type="button" id="plan-goal-confirm">${icon("check")}确认并生成任务</button><button class="ghost" type="button" id="plan-goal-adjust">${icon("refresh")}改预算重算</button></div><div id="plan-goal-adjust-box" hidden><div class="plan-goal-metrics"><label>周期（周）<input type="number" id="plan-goal-weeks" min="1" max="26" value="${parsed.weeks}"></label><label>每周分钟<input type="number" id="plan-goal-minutes" min="30" max="1800" step="10" value="${parsed.weekly_minutes}"></label><label>可学天数<input type="number" id="plan-goal-days" min="1" max="7" value="${parsed.allowed_days}"></label></div><button class="secondary small" type="button" id="plan-goal-reparse">按新预算重新核算</button></div></div>`;
    goalPreview.querySelector("#plan-goal-confirm").onclick = async event => {
      const button = event.target.closest("button");
      button.disabled = true;
      try {
        await sendJson("/api/student/plan/goal", "PUT", {text: parsed.text, weeks: parsed.weeks,
          weekly_minutes: parsed.weekly_minutes, allowed_days: parsed.allowed_days});
        tell("目标已保存，今日任务已按新目标生成");
        location.reload();
      } catch (error) { tell(error.message); button.disabled = false; }
    };
    goalPreview.querySelector("#plan-goal-adjust").onclick = () => {
      const box = goalPreview.querySelector("#plan-goal-adjust-box");
      box.hidden = !box.hidden;
    };
    goalPreview.querySelector("#plan-goal-reparse").onclick = async () => {
      const weeks = Number(goalPreview.querySelector("#plan-goal-weeks").value);
      const minutes = Number(goalPreview.querySelector("#plan-goal-minutes").value);
      const days = Number(goalPreview.querySelector("#plan-goal-days").value);
      try {
        renderGoalPreview(await sendJson("/api/student/plan/parse", "POST",
          {text: parsed.text, weeks, weekly_minutes: minutes, allowed_days: days}));
        tell("已按新预算重新核算");
      } catch (error) { tell(error.message); }
    };
  };
  if (goalForm) goalForm.onsubmit = async event => {
    event.preventDefault();
    const text = root.querySelector("#plan-goal-text").value.trim();
    if (text.length < 4) { tell("请写清目标，例如 4 周内掌握 Python 的循环和函数"); return; }
    const button = event.target.querySelector("button[type=submit]");
    button.disabled = true;
    try { renderGoalPreview(await sendJson("/api/student/plan/parse", "POST", {text})); }
    catch (error) { tell(error.message); } finally { button.disabled = false; }
  };
  root.querySelector("#plan-goal-clear")?.addEventListener("click", async () => {
    try { await sendJson("/api/student/plan/goal", "DELETE", {}); tell("已清除目标"); location.reload(); }
    catch (error) { tell(error.message); }
  });
  root.querySelector("#learning-record-course").value=filters.course;
  root.querySelector("#learning-record-type").value=filters.type;
  renderRecords();showTab(tab);
}
