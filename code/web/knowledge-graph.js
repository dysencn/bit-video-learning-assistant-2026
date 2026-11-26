import {esc, icon} from "/static/ui.js";
import {workspaceNav} from "/static/teacher-workspace.js";

const options = (items, placeholder) => `<option value="">${placeholder}</option>${items.map(
  item => `<option value="${esc(item.id)}">${esc(item.name)}</option>`
).join("")}`;

function showGraph(canvas, nodes, edges) {
  const surface = canvas.parentElement;
  const buttons = new Map([...surface.querySelectorAll("[data-graph-point]")]
    .map(button => [button.dataset.graphPoint, button]));
  const draw = () => {
    if (!canvas.isConnected) {
      observer.disconnect();
      return;
    }
    const area = surface.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(area.width * ratio);
    canvas.height = Math.round(area.height * ratio);
    const brush = canvas.getContext("2d");
    if (!brush) return;
    brush.scale(ratio, ratio);
    const palette = getComputedStyle(surface);
    const edge = palette.getPropertyValue("--graph-edge");
    const selectedEdge = palette.getPropertyValue("--graph-edge-active");
    const active = surface.querySelector('[aria-pressed="true"]')?.dataset.graphPoint;
    const positions = new Map(nodes.map(node => [node.id, buttons.get(node.id).getBoundingClientRect()]));
    for (const {before, after} of edges) {
      const selected = before === active || after === active;
      brush.strokeStyle = selected ? selectedEdge : edge;
      brush.fillStyle = brush.strokeStyle;
      brush.lineWidth = selected ? 2.4 : 1.6;
      const from = positions.get(before), to = positions.get(after);
      const x1 = from.right - area.left, y1 = from.top + from.height / 2 - area.top;
      const x2 = to.left - area.left - 2, y2 = to.top + to.height / 2 - area.top;
      const mid = (x1 + x2) / 2;
      brush.beginPath();
      brush.moveTo(x1, y1);
      brush.bezierCurveTo(mid, y1, mid, y2, x2, y2);
      brush.stroke();
      brush.beginPath();
      brush.moveTo(x2, y2);
      brush.lineTo(x2 - 6, y2 - 4);
      brush.lineTo(x2 - 6, y2 + 4);
      brush.closePath();
      brush.fill();
    }
  };
  const observer = new ResizeObserver(draw);
  observer.observe(surface);
  draw();
  return {draw, observer};
}

function graphColumns(nodes, edges) {
  const levels = new Map(nodes.map(node => [node.id, 0]));
  const waiting = new Map(nodes.map(node => [node.id, 0]));
  const outgoing = new Map(nodes.map(node => [node.id, []]));
  for (const {before, after} of edges) {
    waiting.set(after, waiting.get(after) + 1);
    outgoing.get(before).push(after);
  }
  const queue = nodes.filter(node => waiting.get(node.id) === 0).map(node => node.id);
  for (const id of queue) {
    for (const after of outgoing.get(id)) {
      levels.set(after, Math.max(levels.get(after), levels.get(id) + 1));
      waiting.set(after, waiting.get(after) - 1);
      if (waiting.get(after) === 0) queue.push(after);
    }
  }
  const columns = [];
  for (const node of nodes) {
    const level = levels.get(node.id);
    (columns[level] ||= []).push(node);
  }
  return columns;
}

function sourceLabel(source) {
  if (source.page == null) return esc(source.name);
  const time = Math.floor(source.timestamp ?? 0);
  const stamp = `${Math.floor(time / 60)}:${String(time % 60).padStart(2, "0")}`;
  return `${esc(source.name)} · 第 ${source.page} 页${source.timestamp == null ? "" : ` · ${stamp}`}`;
}

function editor(ctx, graph, course, jobs, reload) {
  const {root, api, tell} = ctx;
  const section = root.querySelector("#graph-editor");
  const nodes = new Map(graph.nodes.map(point => [point.id, point.name]));
  const materials = new Map(course.resources.map(resource => [resource.id, resource]));
  const jobPages = new Map(jobs.map(job => [job.id, job.result?.pages || []]));
  const prerequisites = graph.prerequisites.slice();
  const evidence = graph.nodes.flatMap(node => node.evidence.map(source => ({...source, knowledge_point_id:node.id})));
  const pointOptions = options(graph.nodes, "选择知识点");
  section.innerHTML = `<h2>审核图谱关系</h2>
    <div class="graph-edit-grid">
      <div><h3>先修关系</h3><ul id="graph-edges" class="graph-edit-list"></ul>
        <form id="add-graph-edge"><label>需要先学<select name="before" required>${pointOptions}</select></label>
          <label>再学<select name="after" required>${pointOptions}</select></label>
          <button type="submit" class="secondary">添加先修关系</button></form></div>
      <div><h3>材料关联</h3><ul id="graph-evidence" class="graph-edit-list"></ul>
        <form id="add-graph-source"><label>知识点<select name="point" required>${pointOptions}</select></label>
          <label>课程资料<select name="resource" required>${options(course.resources, "选择资料")}</select></label>
          <label>课件页<select name="page"><option value="">整份资料</option></select></label>
          <button type="submit" class="secondary">添加材料关联</button></form></div>
    </div><div class="actions"><button type="button" class="primary" id="save-graph" disabled>确认并保存图谱</button>
      <span id="graph-save-status" role="status"></span></div>`;
  const edgeList = section.querySelector("#graph-edges"), sourceList = section.querySelector("#graph-evidence");
  const edgeForm = section.querySelector("#add-graph-edge"), sourceForm = section.querySelector("#add-graph-source");
  const status = section.querySelector("#graph-save-status"), save = section.querySelector("#save-graph");
  const remove = i => `<button class="ghost small" type="button" data-remove-graph-item="${i}">移除</button>`;
  const updateLists = () => {
    edgeList.innerHTML = prerequisites.map(({before, after}, i) =>
      `<li><span>${esc(nodes.get(before))} → ${esc(nodes.get(after))}</span>${remove(i)}</li>`).join("")
      || '<li class="muted">暂无先修关系</li>';
    sourceList.innerHTML = evidence.map((source, i) =>
      `<li><span>${esc(nodes.get(source.knowledge_point_id))} · ${sourceLabel(source)}${source.available ? "" : " · 来源不可用"}</span>${remove(i)}</li>`).join("")
      || '<li class="muted">暂无材料关联</li>';
  };
  const changed = () => { updateLists(); save.disabled = false; status.textContent = "有待确认的修改"; };
  const handleRemove = (event, list) => {
    const button = event.target.closest("[data-remove-graph-item]");
    if (button) {
      list.splice(Number(button.dataset.removeGraphItem), 1);
      changed();
    }
  };
  edgeList.onclick = event => handleRemove(event, prerequisites);
  sourceList.onclick = event => handleRemove(event, evidence);
  edgeForm.onsubmit = event => {
    event.preventDefault();
    const {before, after} = Object.fromEntries(new FormData(edgeForm));
    if (before === after || prerequisites.some(edge => edge.before === before && edge.after === after)) {
      tell("请选择两个不同的知识点，且关系不可重复");
      return;
    }
    prerequisites.push({before, after});
    changed();
  };
  sourceForm.elements.resource.onchange = () => {
    const jobId = materials.get(sourceForm.elements.resource.value)?.job_id;
    const pages = jobPages.get(jobId) || [];
    sourceForm.elements.page.innerHTML = options(pages.map(page => ({id:page.number, name:`第 ${page.number} 页`})), "整份资料");
  };
  sourceForm.onsubmit = event => {
    event.preventDefault();
    const {point, resource, page} = Object.fromEntries(new FormData(sourceForm));
    const number = page ? Number(page) : null;
    if (evidence.some(item => item.knowledge_point_id === point && item.resource_id === resource && item.page === number)) {
      tell("该知识点已关联此材料");
      return;
    }
    const material = materials.get(resource);
    const slide = jobPages.get(material.job_id)?.find(item => item.number === number);
    evidence.push({knowledge_point_id:point, resource_id:resource, page:number,
      name:material.name, available:true, timestamp:slide?.timestamp});
    changed();
  };
  save.onclick = async () => {
    section.inert = true;
    status.textContent = "正在保存图谱…";
    try {
      const result = await api(`/api/courses/${encodeURIComponent(course.id)}/knowledge-graph`, {
        method:"PUT", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({revision:graph.revision, prerequisites,
          evidence:evidence.map(({knowledge_point_id, resource_id, page}) => ({knowledge_point_id, resource_id, page}))})
      });
      graph.revision = result.revision;
      save.disabled = true;
      status.textContent = "图谱已保存";
      try {
        await reload();
        tell("图谱已保存");
      } catch {
        tell("图谱已保存。请刷新页面查看最新关系。");
      }
    } catch (error) {
      status.textContent = error.message;
      tell(error.message);
    } finally {
      section.inert = false;
    }
  };
  updateLists();
}

export async function knowledgeGraphPage(ctx, initialPoint) {
  const {api, date, fileUrl, href, layout, link, params, root, user, tell} = ctx;
  const courseId = params.get("course") || "";
  const courseApi = `/api/courses/${encodeURIComponent(courseId)}`;
  const teacher = user.role === "teacher";
  const graph = await api(`${courseApi}/knowledge-graph`);
  const [course, jobs] = teacher
    ? await Promise.all([api(courseApi), graph.editable && graph.nodes.length ? api("/api/jobs") : []])
    : [null, []];
  const nodes = new Map(graph.nodes.map(node => [node.id, node]));
  const requested = initialPoint || params.get("point");
  let active = nodes.has(requested) ? requested : graph.nodes[0]?.id;
  const columns = graphColumns(graph.nodes, graph.prerequisites);
  layout(`${teacher ? workspaceNav(ctx, course, "knowledge-graph") : ""}
    <div class="page-heading"><div><h1>学习顺序</h1>${teacher ? "" : `<p class="muted">${esc(graph.title)}${graph.teaching_class ? ` · ${esc(graph.teaching_class)}` : ""}</p>`}</div>
    <div class="actions">${teacher ? "" : link("course", "返回课程", "secondary", {course:courseId})}
    <button id="refresh-graph" class="ghost" type="button">${icon("refresh")}刷新</button></div></div>
    ${columns.length ? `<div class="graph-workspace"><section class="editor"><div class="section-title"><h2>知识点</h2><span class="tag neutral">${graph.prerequisites.length ? `${icon("arrow")}先修关系` : "暂无先修关系"}</span></div>
      <div class="graph-scroll"><div class="graph-surface"><canvas aria-hidden="true"></canvas>
        ${columns.map(column => `<div class="graph-column">${column.map(node =>
          `<button type="button" class="graph-point" data-graph-point="${esc(node.id)}" aria-pressed="false">${esc(node.name)}</button>`
        ).join("")}</div>`).join("")}</div></div></section><section class="editor graph-details" id="graph-point-details" aria-live="polite"></section></div>`
      : `<div class="empty-state"><h2>本班暂无知识点</h2>${graph.editable ? link("edit-course", "在课程题库添加知识点", "secondary", {course:courseId, section:"questions"}) : ""}</div>`}
    ${graph.editable && columns.length ? '<section class="editor" id="graph-editor"></section>' : ""}`, "学习顺序");
  const canvas = root.querySelector(".graph-surface canvas");
  const drawing = canvas ? showGraph(canvas, graph.nodes, graph.prerequisites) : null;
  const renderPoint = id => {
    active = id;
    const point = nodes.get(id);
    for (const button of root.querySelectorAll("[data-graph-point]")) {
      button.setAttribute("aria-pressed", String(button.dataset.graphPoint === id));
    }
    drawing?.draw();
    const sources = point.evidence.map(source => {
      const label = sourceLabel(source);
      if (!source.available) return `<li>${label} <span class="muted">来源不可用</span></li>`;
      if (source.job_id && source.page != null) {
        const url = fileUrl(`/api/jobs/${encodeURIComponent(source.job_id)}/pages/${source.page}`);
        return `<li><a href="${esc(url)}" target="_blank" rel="noopener">${label} ↗</a></li>`;
      }
      return `<li>${link("course", label, "text-link", {course:courseId, resource:source.resource_id, ...(teacher ? {preview:"1"} : {})})}</li>`;
    }).join("");
    const earlier = graph.prerequisites.filter(edge => edge.after === id).map(edge => nodes.get(edge.before).name);
    const later = graph.prerequisites.filter(edge => edge.before === id).map(edge => nodes.get(edge.after).name);
    const now = new Date();
    now.setHours(23, 59, 59, 999);
    const reviewLink = !teacher && point.pending_review_count ? link("review-center", "查看错题与复习", "secondary",
      {course:courseId, knowledge_point_id:id, tab:new Date(point.next_review_at) <= now ? "today" : "all"}) : "";
    const announcement = teacher && course.status === "published"
      ? link("announcements", "发布知识点讲评", "secondary", {course:courseId, knowledge_point:point.name}) : "";
    root.querySelector("#graph-point-details").innerHTML = `<h2>${esc(point.name)}</h2>
      ${earlier.length || later.length ? `<dl class="graph-sequence">
        ${earlier.length ? `<div><dt>先修</dt><dd>${esc(earlier.join("、"))}</dd></div>` : ""}
        ${later.length ? `<div><dt>后续</dt><dd>${esc(later.join("、"))}</dd></div>` : ""}</dl>` : ""}
      <dl class="graph-statistics">
        <div><dt>自主练习</dt><dd>${point.answered ? `${point.correct} / ${point.answered} 题答对 · ${point.accuracy}%` : "暂无作答"}</dd></div>
        <div><dt>复习次数</dt><dd>${point.review_count} 次${point.review_count ? ` · 答对 ${point.review_correct} 次` : ""}</dd></div>
        ${!teacher && point.next_review_at ? `<div><dt>下次复习</dt><dd>${esc(date(point.next_review_at))} · ${point.pending_review_count} 道待巩固</dd></div>` : ""}
      </dl><h3>学习材料</h3>${sources ? `<ul class="graph-source-list">${sources}</ul>` : '<p class="muted">暂无已确认的材料</p>'}
      <div class="actions">${point.practice_chapters.map(chapter => link("course",
        `${teacher ? "预览" : "练习"} · ${esc(chapter.title)}`, "secondary", {course:courseId, chapter:chapter.id, ...(teacher ? {preview:"1"} : {})})).join("")}
        ${reviewLink}${announcement}</div>`;
  };
  if (active) {
    renderPoint(active);
    root.querySelectorAll("[data-graph-point]").forEach(button => {
      button.onclick = () => renderPoint(button.dataset.graphPoint);
    });
  }
  const reload = async () => {
    await knowledgeGraphPage(ctx, active);
    drawing?.observer.disconnect();
  };
  root.querySelector("#refresh-graph").onclick = () => reload().catch(error => tell(error.message));
  if (graph.editable && active) editor(ctx, graph, course, jobs, reload);
}
