import {courseLabel} from "/static/teaching-courses.js";
import {esc, icon} from "/static/ui.js";
import {initializeApiKey, ensureApiKey, openApiKeyDialog} from "/static/ai-key.js";

export function workspaceNav(ctx, course, active) {
  const tabs = [
    ["settings", "edit-course", "课程设置", "layers", {section:"settings"}],
    ["content", "edit-course", "章节与资料", "book", {section:"content"}],
    ["questions", "edit-course", "课程题库", "target", {section:"questions"}],
    ["assignments", "assignments", "作业管理", "check", {}],
    ["ai", "teaching-ai", "AI 出题与分析", "chart", {}],
    ["gradebook", "gradebook", "成绩册", "chart", {}],
    ["announcements", "announcements", "课程公告", "bell", {}],
  ];
  return `<div class="teaching-context"><a href="${esc(ctx.href("teaching"))}" class="all-courses-link">${icon("book")}全部课程</a><span class="context-divider">/</span><b>${esc(course.title)}</b>${course.teaching_class ? `<span class="context-class">${esc(course.teaching_class)}</span>` : ""}<span class="tag ${course.status === "published" ? "published" : "neutral"}">${course.status === "published" ? "已发布" : course.status === "archived" ? "已归档" : "课程草稿"}</span></div><nav class="teaching-tabs" aria-label="课程管理栏目">${tabs.map(([key, view, label, graphic, extra]) => `<a href="${esc(ctx.href(view, {course:course.id, ...extra}))}" class="${active === key ? "active" : ""}" ${active === key ? 'aria-current="page"' : ""} data-workspace-tab="${key}">${icon(graphic)}${label}</a>`).join("")}</nav>`;
}

export async function teachingCourse(ctx) {
  if (ctx.user.role !== "teacher") throw new Error("请使用教师账号进入教学空间");
  const courses = (await ctx.api("/api/courses")).filter(c => c.status !== "archived");
  if (!courses.length) {
    ctx.layout(`<div class="empty-state"><h1>先创建一门课程</h1><p class="muted">课程创建后，就可以添加题目、布置作业和分析学生作答。</p>${ctx.link("new-course", "创建课程", "primary")}</div>`, "教学空间");
    return null;
  }
  const courseId = ctx.params.get("course") || courses[0].id;
  if (!courses.some(c => c.id === courseId)) throw new Error("课程不存在或已归档，请从工作台重新进入");
  const course = await ctx.api(`/api/courses/${encodeURIComponent(courseId)}`);
  return {course, courses};
}

export async function teachingAiPage(ctx) {
  const selected = await teachingCourse(ctx);
  if (!selected) return;
  const {course, courses} = selected;
  const {api, post, layout, root, href, link, tell, user, config} = ctx;
  const [jobs, logs, assignments] = await Promise.all([api("/api/jobs"), api("/api/learning-records"), api(`/api/courses/${encodeURIComponent(course.id)}/assignments`)]);
  const records = logs.filter(record => record.course_id === course.id).map(record => ({...record, assignment_title:assignments.find(item => item.id === record.assignment_id)?.title}));
  const currentChapter = ctx.params.get("chapter") || course.chapters[0]?.id;
  layout(`${workspaceNav(ctx, course, "ai")}<div class="page-heading"><div><span class="eyebrow">TEACHING WITH AI</span><h1>AI 出题与分析</h1><p class="muted">用课程材料准备题目，用学生的真实作答生成学习反馈。</p></div><label class="inline-select">当前课程<select id="ai-course">${courses.map(c => `<option value="${esc(c.id)}" ${c.id === course.id ? "selected" : ""}>${esc(courseLabel(c))}</option>`).join("")}</select></label></div>
    <div class="ai-workflow"><a href="#ai-authoring"><span>01</span><div><b>AI 辅助出题</b><small>选择材料 → 生成题目 → 审核导入</small></div>${icon("arrow")}</a><a href="#ai-feedback"><span>02</span><div><b>AI 学习分析</b><small>选择学生提交 → 查看错因与复习建议</small></div>${icon("arrow")}</a></div>
    <section id="ai-authoring"><div class="section-title"><div><h2>AI 辅助出题</h2><p class="muted">从本人视频任务中选择已保存的总结，每次生成 10 道单选题。</p></div><button class="ghost" id="ai-refresh">${icon("refresh")}刷新材料</button></div>
    <div class="workflow-note"><div><b>还没有出题材料？</b><p>先上传课程视频，完成提取与课程总结，再回到这里生成题目。</p></div>${link("video", "上传视频 / 生成总结", "secondary")}</div>
    <div class="ai-source-list">${jobs.map(job => `<article class="ai-source-card" data-source="${esc(job.id)}"><div class="ai-source-heading">${icon("video")}<div><h3>${esc(job.filename)}</h3><p class="muted">${job.quiz_options?.ready ? "课程总结已就绪，可以出题" : esc(job.quiz_options?.reason || "请先完成材料提取与课程总结")}</p></div></div><p class="ai-job-status" role="status">${esc(job.quiz?.message || (job.quiz?.available ? "已有 AI 题目，可预览并导入课程" : "尚未生成题目"))}</p><div class="actions"><button class="primary small" data-generate="${esc(job.id)}" ${!job.quiz_options?.ready ? "disabled" : ""}>${job.quiz?.available ? "检查 / 更新 AI 题目" : "生成 10 道题"}</button><button class="secondary small" data-preview="${esc(job.id)}" ${!job.quiz?.available ? "disabled" : ""}>预览并导入题库</button>${link("video", "管理材料", "ghost small", {job:job.id})}</div></article>`).join("") || '<div class="empty-state compact"><h3>还没有课程材料</h3><p class="muted">点击上方“上传视频 / 生成总结”，准备第一份 AI 出题材料。</p></div>'}</div>
    <div id="ai-import-preview" hidden></div></section>
    <section id="ai-feedback"><div class="section-title"><div><h2>AI 学习分析</h2><p class="muted">选择一条学生提交，分析本次题目、答案和得分；结果不改变原成绩。</p></div>${link("gradebook", "查看成绩册", "secondary", {course:course.id})}</div>
    <div class="analysis-filters"><label>查找学生或题目<input id="ai-record-search" type="search" placeholder="输入学生、章节或作业名称"></label><span class="muted">${records.length} 条课程作答记录</span></div><div id="ai-record-list"><h3>自主练习记录</h3>${ctx.recordList(records.filter(r=>!r.assignment_id),true)}<h3>作业提交记录</h3>${ctx.recordList(records.filter(r=>r.assignment_id),true)}</div>
    ${!records.length ? '<p class="muted">学生提交课程练习或作业后，会在这里出现可分析的记录。</p>' : ""}</section>
    <div class="ai-key-footer"><span class="muted">生成题目与分析会使用 DeepSeek API 额度；已保存结果可重复查看。</span><button class="ghost" id="teaching-ai-key">设置 / 更换 API 密钥</button></div>`, "AI 出题与分析");
  await initializeApiKey(config, user, {prompt:false});
  root.querySelector("#teaching-ai-key").onclick = () => openApiKeyDialog();
  root.querySelector("#ai-course").onchange = event => { location.href = href("teaching-ai", {course:event.target.value}); };
  root.querySelector("#ai-refresh").onclick = () => teachingAiPage(ctx).catch(error => tell(error.message));
  root.querySelector("#ai-record-search").oninput = event => {
    const term = event.target.value.trim().toLowerCase();
    root.querySelectorAll("#ai-record-list .record-row").forEach(row => { row.hidden = !row.textContent.toLowerCase().includes(term); });
  };
  let disposed = false;
  const authoringPanel = root.querySelector("#ai-authoring");
  const timers = new Set();
  window.addEventListener("pagehide", () => { disposed = true; timers.forEach(clearTimeout); }, {once:true});
  const running = new Set(["preparing", "generating", "validating", "saving"]);
  async function monitor(jobId) {
    const card = root.querySelector(`[data-source="${CSS.escape(jobId)}"]`);
    if (disposed || !authoringPanel.isConnected || !card?.isConnected) return;
    try {
      const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
      const busy = running.has(job.quiz?.status);
      card.querySelector(".ai-job-status").textContent = job.quiz?.message || "等待生成";
      card.querySelector("[data-generate]").disabled = busy || !job.quiz_options?.ready;
      card.querySelector("[data-preview]").disabled = !job.quiz?.available;
      if (busy) {
        const timer = setTimeout(() => { timers.delete(timer); monitor(jobId); }, 1500);
        timers.add(timer);
      }
    } catch (error) {
      card.querySelector(".ai-job-status").textContent = `读取进度失败：${error.message}，请点击刷新材料。`;
      card.querySelector("[data-generate]").disabled = false;
    }
  }
  root.querySelectorAll("[data-generate]").forEach(button => {
    button.onclick = async () => {
      button.disabled = true;
      try {
        if (!await ensureApiKey()) return;
        await post(`/api/jobs/${encodeURIComponent(button.dataset.generate)}/quiz`);
      } catch (error) { tell(error.message); }
      finally { if (button.isConnected) await monitor(button.dataset.generate); }
    };
  });
  for (const job of jobs) if (running.has(job.quiz?.status)) monitor(job.id);
  root.querySelectorAll("[data-preview]").forEach(button => {
    button.onclick = async () => {
      button.disabled = true;
      try {
        const jobId = button.dataset.preview;
        const quiz = await api(`/api/jobs/${encodeURIComponent(jobId)}/quiz`);
        const panel = root.querySelector("#ai-import-preview");
        const imported = new Set(course.questions.filter(q => q.ai_source?.job_id === jobId && q.ai_source?.quiz_id === quiz.id).map(q => q.ai_source.question_id));
        panel.hidden = false;
        panel.innerHTML = `<div class="section-title"><div><span class="eyebrow">REVIEW BEFORE IMPORT</span><h2>审核 AI 题目</h2><p class="muted">核对题干、正确答案和解析；勾选的题目会加入课程题库，供学生自主练习和查看解析。</p></div><button class="ghost" id="close-ai-preview">收起预览</button></div><label>导入到章节<select id="ai-import-chapter">${course.chapters.map(ch => `<option value="${esc(ch.id)}" ${ch.id === currentChapter ? "selected" : ""}>${esc(ch.title)}</option>`).join("")}</select></label><div class="ai-question-review">${quiz.questions.map((q, i) => `<article class="teacher-question-review"><label class="check-row"><input type="checkbox" name="import-question" value="${esc(q.id)}" ${imported.has(q.id) ? "disabled" : "checked"}><b>${i + 1}. ${esc(q.text)}</b><span class="tag neutral">${imported.has(q.id) ? "已导入" : "待审核"}</span></label><ol type="A">${q.options.map((option, j) => `<li class="${j === q.answer ? "correct-answer" : ""}">${esc(option)}${j === q.answer ? " ✓" : ""}</li>`).join("")}</ol><p class="muted">解析：${esc(q.explanation)}</p></article>`).join("")}</div><div class="composer-actions"><span id="ai-import-count" role="status"></span><div class="actions"><button class="ghost" id="ai-select-all">全选待导入题目</button><button class="primary" id="ai-import-submit">确认导入课程题库</button></div></div><div id="ai-import-result" role="status"></div>`;
        const count = () => { const n = panel.querySelectorAll('[name="import-question"]:checked').length; panel.querySelector("#ai-import-count").textContent = `已勾选 ${n} 道题`; panel.querySelector("#ai-import-submit").disabled = !n; };
        panel.onchange = count; count();
        panel.querySelector("#close-ai-preview").onclick = () => { panel.hidden = true; };
        panel.querySelector("#ai-select-all").onclick = () => { panel.querySelectorAll('[name="import-question"]:not(:disabled)').forEach(input => { input.checked = true; }); count(); };
        panel.querySelector("#ai-import-submit").onclick = async event => {
          const submit = event.currentTarget; submit.disabled = true;
          try {
            const result = await post(`/api/courses/${encodeURIComponent(course.id)}/questions/import-ai`, {job_id:jobId, quiz_id:quiz.id, chapter_id:panel.querySelector("#ai-import-chapter").value, question_ids:[...panel.querySelectorAll('[name="import-question"]:checked')].map(input => input.value)});
            course.questions = result.course.questions;
            panel.querySelectorAll('[name="import-question"]:checked').forEach(input => { input.checked = false; input.disabled = true; input.closest("label").querySelector(".tag").textContent = "已导入"; });
            panel.querySelector("#ai-import-result").innerHTML = `<div class="result"><b>已导入 ${result.imported_ids.length} 道题${result.existing_ids.length ? `，${result.existing_ids.length} 道已存在` : ""}</b><p>题目已保存到自主练习题库。作业需另行创建独立题目。</p><div class="actions">${link("assignment-builder", "创建独立作业", "primary", {course:course.id})}${link("edit-course", "打开课程题库", "secondary", {course:course.id, section:"questions"})}</div></div>`;
          } catch (error) { tell(error.message); }
          finally { count(); }
        };
        panel.scrollIntoView({behavior:"smooth", block:"start"});
      } catch (error) { tell(error.message); }
      finally { button.disabled = false; }
    };
  });
}
