import {esc, icon, masteryLabels, progressBar} from "/static/ui.js";

const taskKinds = {
  resource: ["资料", "阅读资料", "book"],
  knowledge: ["知识点", "练习知识点", "target"],
  practice: ["练习", "开始练习", "check"],
  code: ["编程", "开始编程", "layers"],
  review: ["复习", "开始复习", "review"],
};
const dayLabel = value => esc(value?.slice(5).replace("-", "/") || "");
const countUnits = {resource:"份", knowledge:"个", practice:"题", code:"题", review:"题"};
const taskCounts = counts => Object.entries(counts || {}).filter(([kind, count]) => taskKinds[kind] && count);

function taskAction(link, task, style) {
  const label = taskKinds[task.kind]?.[1] || "查看计划";
  const course = {course: task.course_id};
  if (task.kind === "review") return link("review-center", label, style, {tab:"today", focus:task.ref_id});
  if (task.kind === "code") return link("programming", label, style, {...course, question:task.ref_id});
  if (task.kind === "resource") return link("course", label, style, {...course, focus:task.ref_id});
  if (task.point_id) return link("course", "专项练习", style, {...course, point:task.point_id});
  if (task.kind === "knowledge" || task.kind === "practice") {
    const focus = (task.question_ids || [])[0];
    return link("course", label, style, focus ? {...course, focus} : course);
  }
  return link("learning-center", label, style, {tab:"plan"});
}

function taskStatus(plan) {
  if (plan.today.complete) return '<span class="tag published">已打卡</span>';
  if (!plan.plan?.goal) return '<span class="tag neutral">未设目标</span>';
  return (plan.plan.tasks?.total || plan.today.plan_total)
    ? '<span class="tag attention">待完成</span>'
    : '<span class="tag neutral">今日无任务</span>';
}

function progressStat(label, done, total, rate) {
  return `<div class="plan-goal"><div class="plan-goal-head"><span>${label}</span><b>${done} / ${total}</b></div>
    ${progressBar(rate, `${label}，已完成 ${done} 项，共 ${total} 项`)}</div>`;
}

export function dashboardPlan(plan, link, due) {
  const tasks = plan.plan?.tasks;
  const next = tasks?.cards.find(task => !task.done);
  const hasGoal = Boolean(plan.plan?.goal);
  return `<section class="plan-card dashboard-plan" aria-labelledby="dashboard-plan-title">
    <div class="plan-section-head"><div><h2 id="dashboard-plan-title">今日任务</h2>
      ${next ? `<p class="plan-next"><span class="tag neutral">${taskKinds[next.kind]?.[0] || "任务"}</span><strong>${esc(next.title)}</strong></p>` : ""}
    </div>${taskStatus(plan)}</div>
    ${hasGoal ? `<div class="plan-progress-grid">
      ${progressStat("今日完成", tasks?.done ?? 0, tasks?.total ?? 0, tasks?.rate)}
      ${progressStat("本周计划", plan.week.plan_done, plan.week.plan_cards, plan.week.plan_rate)}
    </div>` : ""}
    <div class="actions">
      ${next ? taskAction(link, next, "primary") : ""}
      ${link("learning-center", hasGoal ? "查看计划与打卡" : "设置学习目标", next ? "secondary" : "primary", {tab:"plan"})}
      ${next?.kind !== "review" && due ? link("review-center", `复习 ${due} 道错题`, "secondary", {tab:"today"}) : ""}
    </div>
  </section>`;
}

function todayTasks(plan, link) {
  const tasks = plan.plan.tasks;
  if (!tasks) return "";
  const rows = tasks.cards.map(task => {
    const [kind, , graphic] = taskKinds[task.kind] || ["任务", "", "book"];
    return `<li class="plan-task${task.done ? " is-done" : ""}">
      <span class="plan-task-icon">${icon(graphic)}</span>
      <div><span class="plan-task-title">${esc(task.title)}</span>
      <small>${task.point_id ? "专项练习" : kind} · ${task.minutes} 分钟${(task.kind === "knowledge" || task.point_id) && task.count ? ` · ${task.count} 题` : ""}</small></div>
      <span class="plan-task-state">${task.done
        ? `<span class="tag published">${icon("check")}已完成</span>`
        : taskAction(link, task, "secondary small")}</span></li>`;
  }).join("");
  return `<section class="plan-board" aria-labelledby="plan-today-title">
    <div class="plan-section-head"><div><h2 id="plan-today-title">今日任务</h2>
      <p>${dayLabel(tasks.date)} · ${tasks.done_minutes} / ${tasks.planned_minutes} 分钟</p>
    </div>${taskStatus(plan)}</div>
    ${tasks.total ? progressStat("今日完成", tasks.done, tasks.total, tasks.rate) : ""}
    ${rows ? `<ul class="plan-task-list">${rows}</ul>` : ""}
  </section>`;
}

function weeklyPlan(plan) {
  const week = plan.plan?.week;
  if (!week) return "";
  const kinds = taskCounts(week.summary?.counts);
  const days = (week.days || []).map(row => {
    const counts = taskCounts(row.counts).map(([kind, count]) => taskKinds[kind][0] + " " + count + countUnits[kind]);
    return `<li class="plan-day${row.rest ? " is-rest" : ""}${row.date === plan.today.date ? " is-today" : ""}">
      <div class="plan-day-date">${dayLabel(row.date)} <span>${esc(row.weekday)}</span></div>
      <span>${row.rest ? "休息日" : `${row.minutes} 分钟`}</span>
      ${!row.rest && row.total ? `<b>${row.done} / ${row.total} 项</b>` : ""}
      ${counts.length ? `<small>${counts.join(" · ")}</small>` : ""}</li>`;
  }).join("");
  const points = week.summary?.points || [];
  return `<section class="plan-board" aria-labelledby="plan-week-title">
    <div class="plan-section-head"><div><h2 id="plan-week-title">本周计划</h2>
      <p>第 ${week.index} 周 · ${dayLabel(week.start)} – ${dayLabel(week.end)}</p></div><span class="tag neutral">${week.minutes} 分钟</span></div>
    <div class="plan-progress-grid">
      ${progressStat("计划任务", plan.week.plan_done, plan.week.plan_cards, plan.week.plan_rate)}
      ${progressStat("计划内复习", plan.week.plan_review_done, plan.week.plan_review_cards, plan.week.plan_review_rate)}
    </div>
    ${kinds.length ? `<ul class="plan-kind-list">${kinds.map(([kind, count]) => `<li>${taskKinds[kind][0]} <b>${count} ${countUnits[kind]}</b></li>`).join("")}</ul>` : ""}
    ${days ? `<details class="section-disclosure"><summary>每日安排</summary><ol class="plan-day-grid">${days}</ol>
      ${points.length ? `<p class="muted">知识点 · ${esc(points.join("、"))}</p>` : ""}</details>` : ""}
  </section>`;
}

function checkinBoard(plan) {
  const statuses = {done:"已打卡", partial:"未完成", outside:"计划外学习", none:"暂无记录"};
  const cells = plan.calendar.map(day => {
    const state = statuses[day.state] ? day.state : "none";
    const progress = day.has_plan ? ` · ${day.plan_done}/${day.plan_total} 项`
      : state === "outside" ? ` · 练习 ${day.submissions} 次、复习 ${day.reviews} 题` : "";
    const description = `${day.date} · ${statuses[state]}${progress}`;
    return `<li class="checkin-cell is-${state}${day.today ? " is-today" : ""}" ${day.today ? 'aria-current="date"' : ""} title="${esc(description)}">
      <span aria-hidden="true">${esc(day.label)}</span><span class="sr-only">${esc(description)}</span>
      ${state === "done" ? icon("check") : state === "partial" ? `<i aria-hidden="true">${day.plan_done}/${day.plan_total}</i>` : ""}</li>`;
  }).join("");
  const unlocked = plan.achievements.filter(item => item.unlocked).length;
  return `<section class="plan-board checkin-board" aria-labelledby="checkin-title">
    <div class="plan-section-head"><div><h2 id="checkin-title">打卡记录</h2><p>最近 ${plan.calendar.length} 天</p></div><div class="plan-streak"><b>${plan.streak.current} <small>天</small></b><span>连续打卡</span><small>最长 ${plan.streak.longest} 天</small></div></div>
    <ol class="checkin-grid">${cells}</ol>
    <div class="checkin-legend"><span><i class="is-done">${icon("check")}</i>已打卡</span><span><i class="is-partial"></i>计划未完成</span><span><i class="is-outside"></i>计划外学习</span></div>
    <details class="section-disclosure"><summary>成就 <span class="tag neutral">${unlocked} / ${plan.achievements.length}</span></summary>
      <div class="achievement-grid">${plan.achievements.map(item => `<div class="achievement${item.unlocked ? " is-unlocked" : ""}"><span class="achievement-mark">${icon(item.unlocked ? "check" : "target")}</span><div><b>${esc(item.title)}</b><small>${esc(item.desc)}</small></div></div>`).join("")}</div>
    </details>
  </section>`;
}

function masteryPlanPoints(points) {
  if (!points?.length) return '<p class="muted">当前范围暂无需要安排的讲义或练习。</p>';
  return `<div class="plan-mastery"><h4>学习顺序</h4>
    <ol class="plan-point-list">${points.map(point => `<li>
      <strong>${esc(point.name)}</strong>
      ${point.prerequisite ? '<span class="tag neutral">先修</span>' : ""}
      <span class="graph-mastery-status" data-mastery="${esc(point.status)}">${masteryLabels[point.status]}${point.score == null ? "" : ` · ${point.score}%`}</span>
    </li>`).join("")}</ol></div>`;
}

export function learningPlanPanel(plan, link) {
  const goal = plan.plan?.goal;
  return `
    ${todayTasks(plan, link)}
    <section class="plan-board plan-goal-plan" aria-labelledby="plan-goal-title">
      <div class="plan-section-head"><div><h2 id="plan-goal-title">学习目标</h2>
        ${goal ? `<p class="plan-goal-text">${esc(goal.text)}</p>` : ""}</div>
        ${goal ? `<div class="plan-goal-badges">${goal.parsed.graph ? '<span class="tag published">图谱补弱计划</span>' : ""}
          <span class="tag neutral">${esc(goal.parsed.deadline)} 截止</span></div>` : ""}</div>
      ${goal ? `<dl class="plan-facts"><div><dt>周期</dt><dd>${goal.parsed.weeks} 周</dd></div>
        <div><dt>每周投入</dt><dd>${goal.parsed.weekly_minutes} 分钟</dd></div>
        <div><dt>每周可学</dt><dd>${goal.parsed.allowed_days} 天</dd></div>
        <div><dt>预计工作量</dt><dd>${plan.plan.workload?.minutes ?? 0} 分钟</dd></div>
        <div><dt>预算占比</dt><dd>${Math.round((plan.plan.verdict?.ratio || 0) * 100)}%</dd></div></dl>` : ""}
      ${goal && plan.plan.verdict?.advice ? `<p class="plan-verdict ${esc(plan.plan.verdict.verdict)}">${esc(plan.plan.verdict.advice)}</p>` : ""}
      ${goal?.parsed.graph ? masteryPlanPoints(plan.plan.workload?.points) : ""}
      <details class="section-disclosure" ${goal ? "" : "open"}><summary>${goal ? "调整目标" : "设定目标"}</summary>
        <form class="plan-goal-form" id="plan-goal-form"><label>我的目标<textarea id="plan-goal-text" rows="2" maxlength="200" minlength="4" required placeholder="例如：4 周内学完 Python 的循环和函数，每周投入 5 小时">${esc(goal?.text || "")}</textarea></label>
          <div class="actions"><button class="${goal?.parsed.graph ? "secondary" : "primary"}" type="submit">${icon("target")}${goal?.parsed.graph ? "解析文字目标" : "解析目标"}</button>
          ${goal?.parsed.graph ? '<button class="primary" type="button" id="plan-graph-refresh">重新按掌握度预览</button>' : ""}
          ${goal ? '<button class="ghost" type="button" id="plan-goal-clear">清除目标</button>' : ""}</div></form>
        <div id="plan-goal-preview" aria-live="polite"></div>
      </details>
    </section>
    ${weeklyPlan(plan)}${checkinBoard(plan)}`;
}

export function bindLearningPlan({api, root, tell}, plan, graphTarget) {
  const goalForm = root.querySelector("#plan-goal-form");
  if (!goalForm) return;
  const preview = root.querySelector("#plan-goal-preview");
  const saved = plan?.plan?.goal?.parsed;
  const sendJson = (url, method, body) => api(url, {method,
    headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const planInput = parsed => ({text:parsed.text, weeks:parsed.weeks,
    weekly_minutes:parsed.weekly_minutes, allowed_days:parsed.allowed_days,
    ...(parsed.graph ? {graph:parsed.graph} : {})});
  const reloadPlan = () => {
    const url = new URL(location.href);
    url.searchParams.delete("graph_course");
    url.searchParams.delete("graph_point");
    url.searchParams.set("tab", "plan");
    history.replaceState(null, "", url);
    location.reload();
  };
  const renderPreview = ({parsed, workload, verdict, courses}, adjusted = false) => {
    const names = (courses || []).map(item => [item.title, item.teaching_class].filter(Boolean).join(" · ")).join("、");
    const kinds = Object.entries(workload.by_kind || {}).filter(([, minutes]) => minutes)
      .map(([kind, minutes]) => `${taskKinds[kind]?.[0] || kind} ${minutes} 分钟`).join(" · ");
    const canSave = !parsed.graph || workload.points.length > 0;
    preview.innerHTML = `<section class="plan-goal-preview" aria-labelledby="plan-preview-title">
      <div class="plan-section-head"><div><h3 id="plan-preview-title" tabindex="-1">${parsed.graph ? "补弱计划预览" : "计划预览"}</h3>
      <p>${esc(names || "全部已加入课程")} · 至 ${esc(parsed.deadline)}</p></div></div>
      <dl class="plan-facts"><div><dt>周期</dt><dd>${parsed.weeks} 周</dd></div><div><dt>每周投入</dt><dd>${parsed.weekly_minutes} 分钟</dd></div>
      <div><dt>每周可学</dt><dd>${parsed.allowed_days} 天</dd></div><div><dt>每天约</dt><dd>${parsed.daily_capacity} 分钟</dd></div>
      <div><dt>预计 / 可用</dt><dd>${workload.minutes} / ${verdict.budget} 分钟</dd></div><div><dt>预算占比</dt><dd>${Math.round(verdict.ratio * 100)}%</dd></div></dl>
      ${kinds ? `<p class="muted">${esc(kinds)}</p>` : ""}
      ${canSave ? `<p class="plan-verdict ${esc(verdict.verdict)}">${esc(verdict.advice)}</p>` : ""}
      ${!adjusted && parsed.notes?.length ? `<small class="muted">${esc(parsed.notes.join("。"))}</small>` : ""}
      ${parsed.graph ? masteryPlanPoints(workload.points) : ""}
      ${canSave ? `<div class="actions"><button class="primary" type="button" id="plan-goal-confirm">${icon("check")}${parsed.graph && saved ? "替换当前计划" : "确认并生成任务"}</button></div>` : ""}
      <details class="section-disclosure"><summary>调整时间预算</summary>
        <form id="plan-goal-adjust-box" class="plan-budget"><label>周期（周）<input name="weeks" type="number" min="1" max="26" value="${parsed.weeks}" required></label>
        <label>每周投入（分钟）<input name="weekly_minutes" type="number" min="30" max="1800" value="${parsed.weekly_minutes}" required></label>
        <label>每周可学天数<input name="allowed_days" type="number" min="1" max="7" value="${parsed.allowed_days}" required></label>
        <button class="secondary" type="submit" id="plan-goal-reparse">重新核算</button></form></details></section>`;
    preview.querySelector("#plan-preview-title").focus({preventScroll: true});
    preview.querySelector("#plan-goal-confirm")?.addEventListener("click", async event => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        await sendJson("/api/student/plan/goal", "PUT", planInput(parsed));
        reloadPlan();
      } catch (error) { tell(error.message); button.disabled = false; }
    });
    preview.querySelector("#plan-goal-adjust-box").onsubmit = async event => {
      event.preventDefault();
      const form = event.currentTarget;
      const data = new FormData(form);
      const button = form.querySelector("button");
      button.disabled = true;
      try {
        renderPreview(await sendJson("/api/student/plan/parse", "POST", {...planInput(parsed),
          weeks:Number(data.get("weeks")), weekly_minutes:Number(data.get("weekly_minutes")),
          allowed_days:Number(data.get("allowed_days"))}), true);
      } catch (error) { tell(error.message); button.disabled = false; }
    };
  };
  const showGraphPreview = async input => {
    goalForm.closest("details").open = true;
    const controls = [...goalForm.querySelectorAll("button")];
    controls.forEach(button => {button.disabled = true;});
    preview.textContent = "正在生成补弱计划…";
    try {
      renderPreview(await sendJson("/api/student/plan/parse", "POST", input));
      if (graphTarget) preview.scrollIntoView({block:"nearest"});
    } catch (error) {
      preview.textContent = error.message;
    } finally {
      controls.forEach(button => {button.disabled = false;});
    }
  };
  goalForm.onsubmit = async event => {
    event.preventDefault();
    const input = goalForm.querySelector("#plan-goal-text");
    const text = input.value.trim();
    if (text.length < 4) { tell("请填写至少 4 个字的学习目标"); return; }
    const button = goalForm.querySelector("button[type=submit]");
    button.disabled = true;
    preview.replaceChildren();
    try { renderPreview(await sendJson("/api/student/plan/parse", "POST", {text})); }
    catch (error) { tell(error.message); }
    finally { button.disabled = false; }
  };
  root.querySelector("#plan-goal-clear")?.addEventListener("click", async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try { await sendJson("/api/student/plan/goal", "DELETE", {}); reloadPlan(); }
    catch (error) { tell(error.message); button.disabled = false; }
  });
  root.querySelector("#plan-graph-refresh")?.addEventListener("click", () => showGraphPreview(planInput(saved)));
  if (graphTarget) {
    const budget = saved ? {weeks:saved.weeks, weekly_minutes:saved.weekly_minutes, allowed_days:saved.allowed_days} : {};
    showGraphPreview({...budget, graph:graphTarget});
  }
}
