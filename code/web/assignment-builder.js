import {esc, icon} from "/static/ui.js";
import {workspaceNav, teachingCourse} from "/static/teacher-workspace.js";

const localDate = value => {
  if (!value) return "";
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset()*60000).toISOString().slice(0,16);
};

export async function assignmentBuilderPage(ctx) {
  const selectedCourse = await teachingCourse(ctx);
  if (!selectedCourse) return;
  let {course} = selectedCourse;
  const {api, post, patch, layout, root, link, tell, params, user, href} = ctx;
  const coursePath = `/api/courses/${encodeURIComponent(course.id)}`;
  const assignmentId = params.get("assignment");
  const assignments = assignmentId ? await api(`${coursePath}/assignments`) : [];
  let item = assignmentId ? assignments.find(a => a.id === assignmentId) : null;
  if (assignmentId && !item) throw new Error("作业不存在或已归档");
  let draftKey = `assignment-draft:${user.id}:${course.id}:${assignmentId || (params.get("questions") ? `new:${params.get("questions")}` : "new")}`;
  let saved = null;
  try { saved = JSON.parse(sessionStorage.getItem(draftKey)); } catch { /* A corrupt local draft does not block editing. */ }
  const initialIds = item?.question_ids?.length ? item.question_ids
    : item ? course.questions.filter(q => q.status !== "retired" && q.chapter_id === item.chapter_id).map(q => q.id)
    : params.get("questions")?.split(",").filter(Boolean) || [];
  const selected = new Set(saved?.question_ids || initialIds);
  const chapterOptions = course.chapters.map(ch => `<option value="${esc(ch.id)}" ${ch.id === item?.chapter_id ? "selected" : ""}>${esc(ch.title)}</option>`).join("");
  const active = () => course.questions.filter(q => q.status !== "retired");
  const picked = () => active().filter(q => selected.has(q.id));
  const chapterName = id => course.chapters.find(ch => ch.id === id)?.title || "未分章节";
  let step = 1, saving = false;
  layout(`${workspaceNav(ctx, course, "assignments")}<div class="page-heading"><div><span class="eyebrow">ASSIGNMENT BUILDER</span><h1>${item ? "编辑作业" : "创建作业"}</h1><p class="muted">先准备具体题目，再设置提交规则，最后检查并发布。</p></div><a class="ghost" href="${esc(href("assignments", {course:course.id}))}">返回作业列表</a></div>
    <div class="builder-steps" role="group" aria-label="创建作业步骤">${[[1,"选择或创建题目","从题库选题，也可手动或 AI 出题"],[2,"设置作业规则","标题、截止时间与提交要求"],[3,"预览与发布","核对学生将收到的内容"]].map(([n,title,note]) => `<button type="button" data-step="${n}" aria-current="${n === 1 ? "step" : "false"}"><span>${n}</span><div><b>${title}</b><small>${note}</small></div></button>`).join("")}</div>
    <div class="builder-layout"><div class="builder-main">
    <div data-step-panel="1"><div class="question-entry-grid"><button class="entry-card" id="open-manual-question">${icon("plus")}<b>手动创建题目</b><span>填写题干、选项、答案和解析</span></button><a class="entry-card" href="${esc(href("teaching-ai", {course:course.id}))}" target="_blank" rel="noopener">${icon("target")}<b>AI 出题并导入 ↗</b><span>根据视频课程材料生成，审核后入库</span></a></div>
      <div class="builder-card" id="manual-question-panel" hidden><div class="section-title"><h2>创建一道单选题</h2><button class="ghost small" id="close-manual-question">收起</button></div><form id="builder-question-form"><label>所属章节<select name="chapter_id">${chapterOptions}</select></label><label>题干<textarea name="text" maxlength="300" required placeholder="输入学生需要回答的问题"></textarea></label><div class="editor-fields">${["A","B","C","D"].map(letter => `<label>选项 ${letter}<input name="option-${letter}" maxlength="100" required></label>`).join("")}</div><div class="editor-fields"><label>正确答案<select name="answer">${["A","B","C","D"].map((letter,i) => `<option value="${i}">${letter}</option>`).join("")}</select></label><label>知识点<input name="knowledge_point" maxlength="50" placeholder="可选，例如：循环结构"></label></div><label>答案解析<textarea name="explanation" maxlength="2000" placeholder="帮助学生理解正确思路"></textarea></label><div class="actions"><button class="primary" type="submit">保存到题库并选入作业</button></div><p class="muted">新题会保存到课程题库，之后可重复用于其他作业。</p></form></div>
      <div class="builder-card"><div class="section-title"><div><h2>从课程题库选题</h2><p class="muted">可跨章节选题；勾选后才会加入本次作业。</p></div><button class="ghost small" id="builder-refresh-bank">${icon("refresh")}刷新题库</button></div><div class="question-bank-filters"><label>筛选章节<select id="builder-chapter-filter"><option value="all">全部章节</option>${chapterOptions}</select></label><label>搜索题目<input id="builder-question-search" type="search" placeholder="输入题干或知识点"></label></div><div class="selection-toolbar"><span id="bank-result-count" class="muted"></span><div class="actions"><button class="ghost small" id="select-visible-questions">选择筛选结果</button><button class="ghost small" id="clear-selected-questions">清空已选</button></div></div><div id="builder-question-bank"></div></div>
    </div>
    <div data-step-panel="2" hidden><div class="builder-card"><h2>作业基础信息</h2><form id="builder-settings"><label>作业标题<input name="title" maxlength="100" required value="${esc(item?.title || "")}" placeholder="例如：第二章课后巩固作业"></label><label>作业说明<textarea name="description" maxlength="2000" placeholder="告诉学生作答范围和提交要求">${esc(item?.description || "")}</textarea></label><label>归属章节<select name="chapter_id">${chapterOptions}</select><small class="muted">用于课程内归类；实际题目以第一步的勾选为准。</small></label><div class="editor-fields"><label>开放时间<input type="datetime-local" name="open_at" value="${localDate(item?.open_at)}"><small class="muted">留空则保存后立即开放。</small></label><label>截止时间<input type="datetime-local" name="due_at" value="${localDate(item?.due_at)}"></label></div><div class="editor-fields"><label>最多提交次数<input name="max_attempts" type="number" min="1" max="10" required value="${item?.max_attempts ?? 1}"></label><label>成绩显示<select name="grade_release"><option value="immediate" ${item?.grade_release !== "manual" ? "selected" : ""}>提交后立即显示</option><option value="manual" ${item?.grade_release === "manual" ? "selected" : ""}>教师统一发布</option></select></label></div><details class="advanced-settings"><summary>更多规则：作答时限与迟交</summary><label>作答时限（分钟，0 表示不限时）<input name="time_limit_minutes" type="number" min="0" max="240" value="${item?.time_limit_minutes ?? 0}"></label><label class="check-row"><input type="checkbox" name="allow_late" ${item?.allow_late ? "checked" : ""}>允许迟交</label><div class="editor-fields"><label>最多迟交天数<input name="late_max_days" type="number" min="0" max="30" value="${item?.late_max_days ?? 3}"></label><label>迟交每天扣分（%）<input name="late_penalty_percent_per_day" type="number" min="0" max="100" value="${item?.late_penalty_percent_per_day ?? 10}"></label></div><label>关闭时间（可选）<input type="datetime-local" name="close_at" value="${localDate(item?.close_at)}"></label></details></form></div></div>
    <div data-step-panel="3" hidden><div class="builder-card"><div class="section-title"><div><h2>核对作业内容</h2><p class="muted">学生将收到以下题目；正确答案和解析在此仅供教师核对。</p></div></div><div id="builder-preview"></div></div></div>
    <div class="composer-actions"><button class="ghost" id="builder-previous" hidden>上一步</button><span id="builder-inline-status" class="muted" role="status"></span><button class="primary" id="builder-next">下一步：设置规则 ${icon("arrow")}</button></div></div>
    <aside class="builder-summary"><span class="eyebrow">THIS ASSIGNMENT</span><h2>本次作业</h2><div class="selection-total"><b id="builder-selected-count">0</b><span>道单选题</span></div><p id="builder-summary-chapters" class="muted"></p><div id="builder-selected-list"></div><p class="muted">本次成绩按所选题目计算为百分制。</p><div id="builder-publish-note"></div><button class="secondary" id="builder-save-draft">${item?.status === "published" ? "保存修改" : "保存草稿"}</button><button class="primary" id="builder-publish">${item?.status === "published" ? "保存并保持发布" : "确认并发布作业"}</button><p id="builder-save-state" class="muted" role="status"></p><div id="builder-save-result"></div></aside></div>`, item ? "编辑作业" : "创建作业");
  const settings = root.querySelector("#builder-settings");
  const manual = root.querySelector("#builder-question-form");
  if (saved?.fields && typeof saved.fields === "object") {
    for (const [key,value] of Object.entries(saved.fields)) {
      const input = settings.elements.namedItem(key);
      if (input) { if (input.type === "checkbox") input.checked = Boolean(value); else input.value = value; }
    }
  }
  const rawFields = () => ({...Object.fromEntries(new FormData(settings)), allow_late:settings.elements.allow_late.checked});
  function persist() {
    try { sessionStorage.setItem(draftKey, JSON.stringify({fields:rawFields(), question_ids:[...selected]})); }
    catch { /* Draft storage is optional; server saves remain available. */ }
  }
  function summary() {
    const questions = picked();
    root.querySelector("#builder-selected-count").textContent = questions.length;
    root.querySelector("#builder-summary-chapters").textContent = questions.length ? `覆盖 ${new Set(questions.map(q => q.chapter_id)).size} 个章节` : "先从题库勾选，或直接创建新题。";
    root.querySelector("#builder-selected-list").innerHTML = questions.map((q,i) => `<div class="selected-question"><span>${i+1}. ${esc(q.text)}</span><button class="ghost small" data-remove-selected="${esc(q.id)}" aria-label="移除第 ${i+1} 题">×</button></div>`).join("");
    root.querySelectorAll("[data-remove-selected]").forEach(button => { button.onclick = () => { selected.delete(button.dataset.removeSelected); renderBank(); }; });
    root.querySelector("#builder-publish-note").innerHTML = course.status !== "published" ? `<p class="muted">先发布课程，学生才能收到作业。${link("edit-course", "去发布课程", "text-link", {course:course.id})}</p>` : "";
    root.querySelector("#builder-publish").disabled = saving || !questions.length || course.status !== "published";
    root.querySelector("#builder-save-draft").disabled = saving || !questions.length;
    if (step === 3) renderPreview();
    persist();
  }
  function visibleQuestions() {
    const chapter = root.querySelector("#builder-chapter-filter").value;
    const term = root.querySelector("#builder-question-search").value.trim().toLowerCase();
    return active().filter(q => (chapter === "all" || q.chapter_id === chapter) && `${q.text} ${(course.knowledge_points || []).find(k => k.id === q.knowledge_point_id)?.name || ""}`.toLowerCase().includes(term));
  }
  function renderBank() {
    const validIds = new Set(active().map(q => q.id));
    for (const id of selected) if (!validIds.has(id)) selected.delete(id);
    const questions = visibleQuestions();
    root.querySelector("#bank-result-count").textContent = `${questions.length} 道可用题目`;
    root.querySelector("#builder-question-bank").innerHTML = questions.map(q => `<article class="bank-question ${selected.has(q.id) ? "is-selected" : ""}"><label class="check-row"><input type="checkbox" data-select-question="${esc(q.id)}" ${selected.has(q.id) ? "checked" : ""}><div><b>${esc(q.text)}</b><small>${esc(chapterName(q.chapter_id))}${q.ai_source ? " · AI 辅助出题" : ""}</small></div></label><details><summary>查看选项、答案与解析</summary><ol type="A">${q.options.map((option,i) => `<li class="${i === q.answer ? "correct-answer" : ""}">${esc(option)}${i === q.answer ? " ✓" : ""}</li>`).join("")}</ol><p class="muted">${esc(q.explanation || "暂无解析")}</p></details></article>`).join("") || '<div class="empty">暂无符合条件的题目。可点击上方“手动创建题目”或“AI 出题并导入”。</div>';
    root.querySelectorAll("[data-select-question]").forEach(input => { input.onchange = () => { if (input.checked) selected.add(input.dataset.selectQuestion); else selected.delete(input.dataset.selectQuestion); input.closest("article").classList.toggle("is-selected", input.checked); summary(); }; });
    summary();
  }
  function validateRules(requireDeadline = false) {
    settings.elements.due_at.setCustomValidity(requireDeadline && !settings.elements.due_at.value ? "发布作业前请填写截止时间" : "");
    if (!settings.checkValidity()) { if (step !== 2) setStep(2); settings.reportValidity(); return false; }
    const fields = rawFields();
    if (fields.open_at && new Date(fields.due_at) <= new Date(fields.open_at)) { tell("截止时间必须晚于开放时间"); return false; }
    if (fields.close_at && new Date(fields.close_at) < new Date(fields.due_at)) { tell("关闭时间不能早于截止时间"); return false; }
    return true;
  }
  function renderPreview() {
      const fields = rawFields();
      root.querySelector("#builder-preview").innerHTML = `<div class="assignment-preview-heading"><h3>${esc(fields.title)}</h3><p class="muted">截止 ${esc(fields.due_at ? fields.due_at.replace("T", " ") : "未设置（发布前填写）")} · 最多 ${esc(fields.max_attempts)} 次 · ${fields.grade_release === "manual" ? "教师统一发布成绩" : "提交后显示成绩"}</p>${fields.description ? `<p>${esc(fields.description)}</p>` : ""}</div>${picked().map((q,i) => `<article class="teacher-question-review"><h3>${i+1}. ${esc(q.text)}</h3><ol type="A">${q.options.map((option,j) => `<li class="${j === q.answer ? "correct-answer" : ""}">${esc(option)}${j === q.answer ? " ✓" : ""}</li>`).join("")}</ol><p class="muted">解析：${esc(q.explanation || "暂无解析")}</p></article>`).join("")}`;
  }
  function setStep(next) {
    if (next > 1 && !picked().length) { tell("请先选择至少一道作业题目"); return; }
    if (next > 2) {
      if (step !== 2 && step !== 3) setStep(2);
      if (!validateRules()) return;
    }
    step = next;
    root.querySelectorAll("[data-step-panel]").forEach(panel => { panel.hidden = Number(panel.dataset.stepPanel) !== step; });
    root.querySelectorAll("[data-step]").forEach(button => { button.setAttribute("aria-current", Number(button.dataset.step) === step ? "step" : "false"); });
    root.querySelector("#builder-previous").hidden = step === 1;
    const nextButton = root.querySelector("#builder-next");
    nextButton.hidden = step === 3;
    nextButton.innerHTML = `${step === 1 ? "下一步：设置规则" : "下一步：预览作业"} ${icon("arrow")}`;
    if (step === 3) renderPreview();
    persist();
  }
  root.querySelectorAll("[data-step]").forEach(button => { button.onclick = () => setStep(Number(button.dataset.step)); });
  root.querySelector("#builder-next").onclick = () => setStep(step+1);
  root.querySelector("#builder-previous").onclick = () => setStep(step-1);
  root.querySelector("#builder-chapter-filter").onchange = renderBank;
  root.querySelector("#builder-question-search").oninput = renderBank;
  root.querySelector("#select-visible-questions").onclick = () => { visibleQuestions().forEach(q => selected.add(q.id)); renderBank(); };
  root.querySelector("#clear-selected-questions").onclick = () => { selected.clear(); renderBank(); };
  root.querySelector("#open-manual-question").onclick = () => { root.querySelector("#manual-question-panel").hidden = false; manual.elements.text.focus(); };
  root.querySelector("#close-manual-question").onclick = () => { root.querySelector("#manual-question-panel").hidden = true; };
  settings.oninput = () => { settings.elements.due_at.setCustomValidity(""); persist(); };
  settings.onsubmit = event => { event.preventDefault(); setStep(3); };
  root.querySelector("#builder-refresh-bank").onclick = async event => {
    const button = event.currentTarget; button.disabled = true;
    try { course = await api(coursePath); renderBank(); tell("题库已刷新，已选题目和作业规则保留"); }
    catch (error) { tell(error.message); }
    finally { button.disabled = false; }
  };
  manual.onsubmit = async event => {
    event.preventDefault(); const button = manual.querySelector('[type="submit"]'); button.disabled = true;
    try {
      const data = Object.fromEntries(new FormData(manual));
      const before = new Set(course.questions.map(q => q.id));
      course = await post(`${coursePath}/questions`, {...data, options:["A","B","C","D"].map(letter => data[`option-${letter}`].trim()), answer:Number(data.answer)});
      course.questions.filter(q => !before.has(q.id)).forEach(q => selected.add(q.id));
      manual.reset(); root.querySelector("#manual-question-panel").hidden = true;
      renderBank(); tell("新题已保存到题库，并选入本次作业");
    } catch (error) { tell(error.message); }
    finally { button.disabled = false; }
  };
  async function save(publish) {
    if (saving || !picked().length) return;
    if (step !== 2 && step !== 3) setStep(2);
    if (!validateRules(publish || item?.status === "published")) { setStep(2); return; }
    if (publish && step !== 3) { setStep(3); tell("请核对题目后，再点击“确认并发布作业”"); return; }
    saving = true; summary();
    const state = root.querySelector("#builder-save-state");
    state.textContent = "正在保存…";
    try {
      const data = {...rawFields(), question_ids:picked().map(q => q.id), status:item?.status === "published" ? "published" : "draft"};
      for (const field of ["open_at","due_at","close_at"]) data[field] = data[field] ? new Date(data[field]).toISOString() : null;
      item = item ? await patch(`${coursePath}/assignments/${encodeURIComponent(item.id)}`, data) : await post(`${coursePath}/assignments`, data);
      if (publish && item.status !== "published") item = await post(`${coursePath}/assignments/${encodeURIComponent(item.id)}/publish`);
      sessionStorage.removeItem(draftKey);
      draftKey = `assignment-draft:${user.id}:${course.id}:${item.id}`;
      const target = new URL(location.href); target.searchParams.set("assignment", item.id); history.replaceState(null, "", target);
      state.textContent = item.status === "published" ? "作业已发布，学生可在开放时间后作答。" : "作业草稿已保存。";
      root.querySelector("#builder-save-result").innerHTML = `<div class="actions"><a class="secondary" href="${esc(href("assignments", {course:course.id}))}">返回作业列表</a>${link("gradebook", "查看成绩", "ghost", {course:course.id})}</div>`;
      tell(state.textContent);
    } catch (error) { state.textContent = error.message; tell(error.message); }
    finally { saving = false; root.querySelector("#builder-save-draft").textContent = item?.status === "published" ? "保存修改" : "保存草稿"; root.querySelector("#builder-publish").textContent = item?.status === "published" ? "保存并保持发布" : "确认并发布作业"; summary(); }
  }
  root.querySelector("#builder-save-draft").onclick = () => save(false);
  root.querySelector("#builder-publish").onclick = () => save(true);
  renderBank();
  if (saved) root.querySelector("#builder-inline-status").textContent = "已恢复当前标签页的未保存草稿";
}
