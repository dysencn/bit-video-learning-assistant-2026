import {esc, icon} from "/static/ui.js";

const statusName = state => ({published:"已发布", draft:"草稿", archived:"已归档"}[state] || state);
export const courseLabel = course => `${course.title}${course.teaching_class ? ` · ${course.teaching_class}` : ""}`;

export async function teachingCoursesPage(ctx) {
  const {api, layout, root, user, href, link, tell, majorName, params} = ctx;
  if (user.role !== "teacher") throw new Error("请使用教师账号查看课程与教学班");
  const courses = await api("/api/courses");
  const key = course => course.course_code?.trim() ? `subject:${course.course_code.trim().toUpperCase()}` : `class:${course.id}`;
  const activeCourses = courses.filter(course => course.status !== "archived");
  const subjects = new Set(activeCourses.map(key));
  const results = await Promise.allSettled(courses.map(course => course.status === "archived" ? [] : api(`/api/courses/${encodeURIComponent(course.id)}/assignments`)));
  const assignments = new Map(courses.map((course,i) => [course.id, results[i].status === "fulfilled" ? results[i].value : null]));
  const allAssignments = [...assignments.values()].flatMap(items => items || []);
  const majors = [...new Set(courses.flatMap(course => course.major_ids))];
  const states = ["all", "published", "draft", "archived"];
  let state = {q:params.get("q") || "", status:states.includes(params.get("status")) ? params.get("status") : "all",
    major:majors.includes(params.get("major")) ? params.get("major") : "all", sort:params.get("sort") === "title" ? "title" : "updated", page:1};
  const stats = [[subjects.size,"门课程"],[activeCourses.length,"个教学班"],[allAssignments.filter(a => ["open","late"].includes(a.state)).length,"份进行中作业"],[allAssignments.filter(a => a.status === "draft").length,"份作业草稿"]];
  layout(`<div class="page-heading catalog-heading"><div><span class="eyebrow">COURSES & TEACHING CLASSES</span><h1>课程与作业</h1><p class="muted">先找到课程，再进入对应教学班管理资料、题目、作业与成绩。</p></div><div class="actions"><button class="ghost" id="catalog-refresh">${icon("refresh")}刷新</button>${link("new-course", `${icon("plus")}创建课程`, "primary")}</div></div>
    <div class="catalog-stats">${stats.map(([n,label]) => `<div><b>${n}</b><span>${label}</span></div>`).join("")}</div>
    <div class="catalog-toolbar"><label class="catalog-search">${icon("target")}<span class="sr-only">搜索课程或教学班</span><input id="catalog-search" type="search" value="${esc(state.q)}" placeholder="搜索课程、课程代码或教学班"></label><label>教学班状态<select id="catalog-status">${states.map(value => `<option value="${value}" ${value === state.status ? "selected" : ""}>${value === "all" ? "全部在教" : statusName(value)}</option>`).join("")}</select></label><label>学生专业<select id="catalog-major"><option value="all">全部专业</option>${majors.map(id => `<option value="${esc(id)}" ${state.major === id ? "selected" : ""}>${esc(majorName(id))}</option>`).join("")}</select></label><label>排序<select id="catalog-sort"><option value="updated" ${state.sort === "updated" ? "selected" : ""}>最近更新</option><option value="title" ${state.sort === "title" ? "selected" : ""}>课程名称</option></select></label></div>
    <div class="section-title catalog-result-heading"><span class="muted" id="catalog-result-count" role="status"></span><button class="ghost small" id="catalog-clear">清除筛选</button></div><div id="catalog-results" class="subject-grid"></div><div id="catalog-pagination" class="catalog-pagination"></div>
    <p class="catalog-footnote muted">同一门课程按课程代码归组；不同教学班的资料、题目、作业和成绩分别管理。草稿班尚未向学生开放。</p>`, "课程与作业");
  function render() {
    const term = state.q.trim().toLowerCase();
    const filtered = courses.filter(course => (state.status === "all" ? course.status !== "archived" : course.status === state.status)
      && (state.major === "all" || course.major_ids.includes(state.major))
      && `${course.title} ${course.course_code || ""} ${course.teaching_class || ""} ${course.term || ""} ${course.major_ids.map(majorName).join(" ")}`.toLowerCase().includes(term));
    const grouped = new Map();
    for (const course of filtered) {
      const id = key(course);
      if (!grouped.has(id)) grouped.set(id, {title:course.title, code:course.course_code || "", description:course.description, courses:[]});
      grouped.get(id).courses.push(course);
    }
    const groups = [...grouped.values()];
    const updated = group => Math.max(...group.courses.map(c => Date.parse(c.updated_at || c.created_at) || 0));
    groups.sort((a,b) => state.sort === "title" ? a.title.localeCompare(b.title,"zh-CN") : updated(b)-updated(a) || a.title.localeCompare(b.title,"zh-CN"));
    const pages = Math.max(1,Math.ceil(groups.length/6)); state.page = Math.min(state.page,pages);
    root.querySelector("#catalog-result-count").textContent = `在教 ${subjects.size} 门课程 / ${activeCourses.length} 个教学班${courses.length > activeCourses.length ? ` · 已归档 ${courses.length-activeCourses.length} 个班` : ""} · 当前显示 ${groups.length} 门课程 / ${filtered.length} 个教学班`;
    root.querySelector("#catalog-results").innerHTML = groups.slice((state.page-1)*6,state.page*6).map(group => `<section class="subject-card"><div class="subject-heading"><span class="subject-symbol">${icon(group.code.startsWith("DL") || group.code.startsWith("ML") ? "target" : "layers")}</span><div><span class="eyebrow">${esc(group.code || "课程")}</span><h2>${esc(group.title)}</h2></div><span class="subject-class-count">${group.courses.length} 个教学班</span></div><p class="subject-description">${esc(group.description || "进入教学班，维护课程内容与学习任务。")}</p>
      <div class="teaching-class-list">${group.courses.sort((a,b) => (a.teaching_class || "").localeCompare(b.teaching_class || "","zh-CN")).map(course => {
        const items = assignments.get(course.id);
        const open = items?.filter(a => ["open","late"].includes(a.state)).length;
        const questionCount = [...course.questions,...(course.programming_questions || [])].filter(q => q.status !== "retired").length;
        return `<article class="teaching-class-card"><div class="class-heading"><h3>${esc(course.teaching_class || "默认教学班")}</h3><span class="tag ${course.status === "published" ? "published" : "neutral"}">${statusName(course.status)}</span></div><p class="class-term">${esc(course.term || "未设置学期")} · ${esc(course.teacher)}</p><div class="class-majors">${course.major_ids.map(id => `<span>${esc(majorName(id))}</span>`).join("")}</div><p class="class-content-meta">${course.chapters.length} 章 · ${course.resources.length} 份资料 · ${questionCount} 道题${items ? ` · ${items.length} 份作业` : " · 作业读取失败，请刷新"}</p>${open ? `<p class="class-assignment-note">${icon("check")}${open} 份作业进行中</p>` : ""}<div class="actions"><a class="primary" href="${esc(href("edit-course",{course:course.id}))}">${course.status === "archived" ? "查看归档班" : "进入教学班"}${icon("arrow")}</a>${course.status !== "archived" ? `<a class="secondary" href="${esc(href("assignments",{course:course.id}))}">管理作业</a><a class="ghost" href="${esc(href("gradebook",{course:course.id}))}">成绩册</a>` : ""}</div></article>`;
      }).join("")}</div>${group.code ? `<a class="add-teaching-class" href="${esc(href("new-course",{subject:group.code,title:group.title,term:group.courses[0].term || ""}))}">${icon("plus")}为此课程新增教学班</a>` : ""}</section>`).join("") || `<div class="empty-state"><h2>${courses.length ? "没有找到匹配的教学班" : "创建第一门课程"}</h2><p class="muted">${courses.length ? "试试课程名称、班级名称，或清除筛选条件。" : "创建课程后，添加教学班信息、资料与题目，再布置作业。"}</p>${courses.length ? "" : link("new-course","创建课程","primary")}</div>`;
    root.querySelector("#catalog-pagination").innerHTML = pages > 1 ? `<button class="ghost" id="catalog-previous" ${state.page === 1 ? "disabled" : ""}>上一页</button><span>${state.page} / ${pages}</span><button class="ghost" id="catalog-next" ${state.page === pages ? "disabled" : ""}>下一页</button>` : "";
    for (const [id,delta] of [["catalog-previous",-1],["catalog-next",1]]) root.querySelector(`#${id}`)?.addEventListener("click",()=>{state.page+=delta;render();});
    const url = new URL(location.href);
    for (const key of ["q","status","major","sort"]) {
      const value = state[key];
      if (!value || value === "all" || (key === "sort" && value === "updated")) { url.searchParams.delete(key); params.delete(key); }
      else { url.searchParams.set(key,value); params.set(key,value); }
    }
    history.replaceState(null,"",url);
  }
  root.querySelector("#catalog-search").oninput = event => {state.q=event.target.value;state.page=1;render();};
  for (const field of ["status","major","sort"]) root.querySelector(`#catalog-${field}`).onchange = event => {state[field]=event.target.value;state.page=1;render();};
  root.querySelector("#catalog-clear").onclick = () => {state={q:"",status:"all",major:"all",sort:"updated",page:1};root.querySelector("#catalog-search").value="";for (const field of ["status","major","sort"]) root.querySelector(`#catalog-${field}`).value=state[field];render();};
  root.querySelector("#catalog-refresh").onclick = () => teachingCoursesPage(ctx).catch(error=>tell(error.message));
  render();
}
