import {esc, icon, number, radarCard} from "/static/ui.js";
import {courseLabel} from "/static/teaching-courses.js";

export async function learningFilePage(ctx, initialTab = "overview") {
  const {api, layout, root, user, link, tell, params, majorName, recordList, date} = ctx;
  if (user.role !== "student") throw new Error("请使用学生账号查看自己的学习档案");
  const [data, logs, courses] = await Promise.all([api("/api/student/profile"), api("/api/learning-records"), api("/api/courses")]);
  const profile = data.profile;
  const records = logs.map(record => ({...record, course_name:record.teaching_class ? `${record.course_name} · ${record.teaching_class}` : record.course_name}));
  const courseNames = new Map(courses.map(course => [course.id,courseLabel(course)]));
  for (const record of records) if (!courseNames.has(record.course_id)) courseNames.set(record.course_id,record.course_name || "历史课程");
  let tab = ["overview","records"].includes(params.get("tab")) ? params.get("tab") : initialTab;
  const filters = {course:courseNames.has(params.get("course")) ? params.get("course") : "all",
    type:["self_practice", "assignment"].includes(params.get("source")) ? params.get("source") : "all", search:""};
  const cards = courses.map(course => {
    const entries = records.filter(record => record.course_id === course.id && record.type !== "ai_quiz");
    const attempted = new Set(entries.flatMap(record => [record.chapter_id,...(record.details || []).map(detail=>detail.chapter_id)]).filter(Boolean));
    const practiced = course.chapters.filter(chapter=>attempted.has(chapter.id)).length;
    const scores = entries.filter(record=>!record.pending_release && typeof record.score === "number").map(record=>record.score);
    return `<article class="learning-course-card"><div><span class="eyebrow">${esc(course.course_code || "课程学习")}</span><h3>${esc(course.title)}</h3><p class="muted">${esc(course.teaching_class || "默认教学班")} · ${esc(course.teacher)}</p></div><div class="learning-course-progress"><span>已练习 ${practiced} / ${course.chapters.length} 章</span><span>${entries.length} 次作答</span></div><progress max="${Math.max(1,course.chapters.length)}" value="${practiced}" aria-label="${esc(courseLabel(course))}章节练习进度"></progress><div class="learning-course-result"><span>${scores.length ? `已公布最高分 ${Math.max(...scores)} 分` : "暂无已公布成绩"}</span><button class="ghost small" data-course-records="${esc(course.id)}">查看此课记录</button></div></article>`;
  }).join("");
  const stats = [[profile.course_count,"已参与教学班"],[profile.submission_count,"课程作答"],[number(profile.average_score),"平均成绩"],[profile.weak_points.length,"待巩固知识点"]];
  layout(`<div class="page-heading learning-file-heading"><div><span class="eyebrow">MY LEARNING FILE</span><h1>我的学习档案</h1><p class="muted">${esc(user.name)} · ${esc(majorName(user.major_id))} · 画像、课程进度与作答记录，一处查看。</p></div><button class="ghost" id="learning-file-refresh">${icon("refresh")}刷新档案</button></div><div class="catalog-stats learning-file-stats">${stats.map(([n,label])=>`<div><b>${n}</b><span>${label}</span></div>`).join("")}</div>
    <div class="learning-file-tabs" role="tablist" aria-label="学习档案视图"><button role="tab" id="learning-overview-tab" data-learning-tab="overview" aria-selected="${tab === "overview"}" aria-controls="learning-overview">${icon("chart")}画像与课程进度</button><button role="tab" id="learning-records-tab" data-learning-tab="records" aria-selected="${tab === "records"}" aria-controls="learning-records">${icon("book")}全部学习记录<span>${records.length}</span></button></div>
    <div id="learning-overview" role="tabpanel" aria-labelledby="learning-overview-tab"><section class="personal-profile">${radarCard(profile,true)}</section><div class="learning-followup"><div><b>${icon("target")}下一步学习建议</b><p class="muted">${profile.weak_points.length ? `优先巩固 ${esc(profile.weak_points.join("、"))}，再完成一次对应章节练习。` : "从一份章节练习开始积累数据，逐步了解自己的学习状态。"}</p></div>${link("review-center","打开错题与复习","secondary")}</div><section><div class="section-title"><div><h2>我的课程进度</h2><p class="muted">按课程作答统计已练习章节，不代表整门课程已学完；待发布成绩不参与最高分。</p></div></div><div class="learning-course-grid">${cards || '<div class="empty">暂无面向当前专业的已发布课程。</div>'}</div></section></div>
    <div id="learning-records" role="tabpanel" aria-labelledby="learning-records-tab"><div class="learning-record-toolbar"><label>课程 / 教学班<select id="learning-record-course"><option value="all">全部课程与自测</option>${[...courseNames].map(([id,name])=>`<option value="${esc(id)}">${esc(name)}</option>`).join("")}</select></label><label>记录类型<select id="learning-record-type"><option value="all">全部类型</option><option value="choice">全部单选练习 / 作业</option><option value="self_practice">自主练习</option><option value="assignment">作业提交</option><option value="python">Python 编程</option><option value="ai_quiz">视频 AI 自测</option></select></label><label>搜索<input type="search" id="learning-record-search" placeholder="搜索课程或章节名称"></label></div><div class="section-title"><span class="muted" id="learning-record-count" role="status"></span><button class="ghost small" id="learning-record-clear">清除筛选</button></div><div id="learning-record-list"></div><div class="catalog-pagination" id="learning-record-pagination"></div><p class="catalog-footnote muted">视频 AI 自测单独保存，不计入课程成绩和雷达图。每条已公布结果都可以继续查看 AI 分析。</p></div>`, "我的学习档案");
  let recordPage = 1;
  function renderRecords() {
    const selected = records.filter(record => (filters.course === "all" || record.course_id === filters.course)
      && (filters.type === "all" || (filters.type === "assignment" ? Boolean(record.assignment_id)
        : filters.type === "self_practice" ? !record.assignment_id && !["python","ai_quiz"].includes(record.type)
        : (record.type === "python" || record.type === "ai_quiz" ? record.type : "choice") === filters.type))
      && `${record.course_name} ${record.chapter_title || ""}`.toLowerCase().includes(filters.search.trim().toLowerCase()));
    const pages = Math.max(1,Math.ceil(selected.length/10)); recordPage=Math.min(recordPage,pages);
    root.querySelector("#learning-record-count").textContent=`${selected.length} 条记录 · 按提交时间排序`;
    root.querySelector("#learning-record-list").innerHTML=recordList(selected.slice((recordPage-1)*10,recordPage*10),false);
    root.querySelector("#learning-record-pagination").innerHTML=pages>1?`<button class="ghost" id="records-previous" ${recordPage===1?"disabled":""}>上一页</button><span>${recordPage} / ${pages}</span><button class="ghost" id="records-next" ${recordPage===pages?"disabled":""}>下一页</button>`:"";
    for(const [id,delta] of [["records-previous",-1],["records-next",1]]) root.querySelector(`#${id}`)?.addEventListener("click",()=>{recordPage+=delta;renderRecords();});
  }
  function showTab(next) {
    tab=next;
    for(const value of ["overview","records"]) {
      root.querySelector(`#learning-${value}`).hidden=tab!==value;
      const button = root.querySelector(`[data-learning-tab="${value}"]`); button.setAttribute("aria-selected",String(tab===value)); button.tabIndex = tab===value ? 0 : -1;
    }
    params.set("tab",tab); const url=new URL(location.href);url.searchParams.set("tab",tab);history.replaceState(null,"",url);
  }
  root.querySelectorAll("[data-learning-tab]").forEach(button=>{button.onclick=()=>showTab(button.dataset.learningTab);});
  root.querySelector(".learning-file-tabs").onkeydown=event=>{
    if(!["ArrowLeft","ArrowRight"].includes(event.key))return;event.preventDefault();showTab(tab==="overview"?"records":"overview");root.querySelector(`[data-learning-tab="${tab}"]`).focus();
  };
  root.querySelectorAll("[data-course-records]").forEach(button=>{button.onclick=()=>{filters.course=button.dataset.courseRecords;filters.type="all";filters.search="";root.querySelector("#learning-record-course").value=filters.course;root.querySelector("#learning-record-type").value="all";root.querySelector("#learning-record-search").value="";recordPage=1;renderRecords();showTab("records");};});
  for(const field of ["course","type"])root.querySelector(`#learning-record-${field}`).onchange=event=>{filters[field]=event.target.value;recordPage=1;renderRecords();};
  root.querySelector("#learning-record-search").oninput=event=>{filters.search=event.target.value;recordPage=1;renderRecords();};
  root.querySelector("#learning-record-clear").onclick=()=>{filters.course="all";filters.type="all";filters.search="";root.querySelector("#learning-record-course").value="all";root.querySelector("#learning-record-type").value="all";root.querySelector("#learning-record-search").value="";recordPage=1;renderRecords();};
  root.querySelector("#learning-file-refresh").onclick=()=>learningFilePage(ctx,tab).catch(error=>tell(error.message));
  root.querySelector("#learning-record-course").value=filters.course;
  root.querySelector("#learning-record-type").value=filters.type;
  renderRecords();showTab(tab);
}
