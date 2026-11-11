import {esc, icon} from "/static/ui.js";
import {workspaceNav, teachingCourse} from "/static/teacher-workspace.js";

const localDate = value => value ? new Date(new Date(value).getTime()-new Date(value).getTimezoneOffset()*60000).toISOString().slice(0,16) : "";

export async function assignmentBuilderPage(ctx) {
  const selected = await teachingCourse(ctx);
  if (!selected) return;
  const {course} = selected;
  const {api, post, patch, layout, root, href, params, user, tell} = ctx;
  const path = `/api/courses/${encodeURIComponent(course.id)}/assignments`;
  const id = params.get("assignment");
  let item = id ? (await api(path)).find(a => a.id === id) : null;
  if (id && !item) throw new Error("作业不存在或已归档");
  let draftKey = `independent-assignment-draft:${user.id}:${course.id}:${id || "new"}`;
  let saved;
  try { saved = JSON.parse(sessionStorage.getItem(draftKey)); } catch { /* Optional draft. */ }
  let questions = structuredClone(saved?.questions || item?.questions || []);
  let editing = -1, step = 1, saving = false;
  const frozen = Boolean(item?.submitted_count);
  const chapters = course.chapters.map(ch => `<option value="${esc(ch.id)}">${esc(ch.title)}</option>`).join("");
  layout(`${workspaceNav(ctx,course,"assignments")}<div class="page-heading"><div><span class="eyebrow">ASSIGNMENT BUILDER</span><h1>${item ? "编辑作业" : "创建作业"}</h1><p class="muted">为本次作业单独出题，再设置提交次数并发布。</p></div><a class="ghost" href="${esc(href("assignments",{course:course.id}))}">返回作业列表</a></div>
    <div class="builder-steps" role="group" aria-label="创建作业步骤">${[[1,"创建作业题目","题目仅保存在本次作业中"],[2,"设置提交规则","用完次数后开放标答与解析"],[3,"预览与发布","核对题目、答案与提交要求"]].map(([n,title,note])=>`<button type="button" data-step="${n}"><span>${n}</span><div><b>${title}</b><small>${note}</small></div></button>`).join("")}</div>
    <div class="builder-layout"><div class="builder-main"><div data-step-panel="1"><div class="builder-card"><div class="section-title"><div><h2>本次作业题目</h2><p class="muted">课程题库供学生自主练习；作业题目在这里独立创建。</p></div><button class="primary" id="add-paper-question" ${frozen ? "disabled" : ""}>${icon("plus")}新建题目</button></div>${frozen ? '<p class="muted">已有学生提交，题目与提交次数已锁定。需要更换题目时请创建新作业。</p>' : ""}<div id="paper-questions"></div></div>
    <div class="builder-card" id="paper-question-editor" hidden><h2 id="paper-editor-title">新建作业题目</h2><form id="paper-question-form"><label>所属章节<select name="chapter_id">${chapters}</select></label><label>题干<textarea name="text" maxlength="3000" required></textarea></label><div class="editor-fields">${["A","B","C","D"].map(letter=>`<label>选项 ${letter}<input name="option-${letter}" maxlength="1000" ${["A","B"].includes(letter) ? "required" : ""}></label>`).join("")}</div><label>正确答案<select name="answer">${["A","B","C","D"].map((letter,i)=>`<option value="${i}">${letter}</option>`).join("")}</select></label><label>答案解析<textarea name="explanation" maxlength="3000"></textarea></label><div class="actions"><button class="primary" type="submit">保存到本次作业</button><button class="ghost" type="button" id="cancel-paper-question">取消</button></div></form></div></div>
    <div data-step-panel="2" hidden><div class="builder-card"><h2>作业规则</h2><form id="builder-settings"><label>作业标题<input name="title" maxlength="100" required value="${esc(item?.title || "")}"></label><label>作业说明<textarea name="description" maxlength="2000">${esc(item?.description || "")}</textarea></label><label>归属章节<select name="chapter_id">${chapters}</select></label><div class="editor-fields"><label>开放时间<input name="open_at" type="datetime-local" value="${localDate(item?.open_at)}"></label><label>截止时间<input name="due_at" type="datetime-local" value="${localDate(item?.due_at)}"></label></div><label>最多提交次数<input name="max_attempts" type="number" min="1" max="10" required value="${item?.max_attempts ?? 3}" ${frozen ? "readonly" : ""}></label><p class="muted">每次提交立即显示得分；全部提交次数用完后，才能查看标准答案、解析和 AI 分析。</p><details class="advanced-settings"><summary>作答时限与迟交设置</summary><label>时限（分钟，0 表示不限时）<input name="time_limit_minutes" type="number" min="0" max="240" value="${item?.time_limit_minutes ?? 0}"></label><label class="check-row"><input name="allow_late" type="checkbox" ${item?.allow_late ? "checked" : ""}>允许迟交</label><label>最多迟交天数<input name="late_max_days" type="number" min="0" max="30" value="${item?.late_max_days ?? 3}"></label><label>每天扣分（%）<input name="late_penalty_percent_per_day" type="number" min="0" max="100" value="${item?.late_penalty_percent_per_day ?? 10}"></label><label>关闭时间<input name="close_at" type="datetime-local" value="${localDate(item?.close_at)}"></label></details></form></div></div>
    <div data-step-panel="3" hidden><div class="builder-card"><h2>发布前核对</h2><p class="muted">以下标准答案和解析仅供教师核对。</p><div id="paper-preview"></div></div></div><div class="composer-actions"><button class="ghost" id="builder-previous">上一步</button><span id="builder-step-note" class="muted"></span><button class="primary" id="builder-next">下一步</button></div></div>
    <aside class="builder-summary"><span class="eyebrow">THIS ASSIGNMENT</span><h2>本次作业</h2><div class="selection-total"><b id="paper-question-count">0</b><span>道独立题目</span></div><p class="muted">题目不会加入自主练习题库。</p><button class="secondary" id="builder-save-draft">保存草稿 / 修改</button><button class="primary" id="builder-publish">预览并发布</button><p id="builder-save-state" class="muted" role="status"></p><div id="builder-save-result"></div></aside></div>`,"作业编辑器");
  const settings = root.querySelector("#builder-settings"), editor = root.querySelector("#paper-question-form");
  settings.elements.chapter_id.value = item?.chapter_id || course.chapters[0].id;
  if (saved?.fields) for (const [key,value] of Object.entries(saved.fields)) {
    const field=settings.elements.namedItem(key); if(field) {if(field.type==="checkbox")field.checked=Boolean(value);else field.value=value;}
  }
  const fields=()=>({...Object.fromEntries(new FormData(settings)),allow_late:settings.elements.allow_late.checked});
  function persist(){try{sessionStorage.setItem(draftKey,JSON.stringify({questions,fields:fields()}));}catch{/* Optional draft. */}}
  function preview(){root.querySelector("#paper-preview").innerHTML=questions.map((q,i)=>`<article class="teacher-question-review"><b>${i+1}. ${esc(q.text)}</b><ol type="A">${q.options.map((option,n)=>`<li class="${n===q.answer ? "correct-answer" : ""}">${esc(option)}${n===q.answer ? " ✓" : ""}</li>`).join("")}</ol><p>${esc(q.explanation || "暂无解析")}</p></article>`).join("");}
  function renderQuestions(){
    root.querySelector("#paper-question-count").textContent=questions.length;
    root.querySelector("#paper-questions").innerHTML=questions.map((q,i)=>`<article class="bank-question"><b>${i+1}. ${esc(q.text)}</b><small>${q.options.length} 个选项 · 标答 ${String.fromCharCode(65+q.answer)}</small>${!frozen ? `<div class="actions"><button class="ghost small" data-edit-paper="${i}">编辑</button><button class="ghost small" data-remove-paper="${i}">移除</button></div>` : ""}</article>`).join("") || '<div class="empty">还没有题目，请点击“新建题目”。</div>';
    root.querySelectorAll("[data-edit-paper]").forEach(button=>{button.onclick=()=>openEditor(Number(button.dataset.editPaper));});
    root.querySelectorAll("[data-remove-paper]").forEach(button=>{button.onclick=()=>{questions.splice(Number(button.dataset.removePaper),1);editing=-1;root.querySelector("#paper-question-editor").hidden=true;renderQuestions();};});
    preview();persist();
  }
  function openEditor(index=-1){
    editing=index;editor.reset();root.querySelector("#paper-question-editor").hidden=false;
    root.querySelector("#paper-editor-title").textContent=index<0 ? "新建作业题目" : "编辑作业题目";
    const q=questions[index];if(q){for(const field of ["text","answer","explanation","chapter_id"])editor.elements[field].value=q[field] ?? "";q.options.forEach((option,i)=>{editor.elements[`option-${String.fromCharCode(65+i)}`].value=option;});}
    editor.elements.text.focus();
  }
  function showStep(next){if(next>1&&!questions.length){tell("请先创建作业题目");return;}step=next;root.querySelectorAll("[data-step-panel]").forEach(panel=>{panel.hidden=Number(panel.dataset.stepPanel)!==step;});root.querySelectorAll("[data-step]").forEach(button=>button.setAttribute("aria-current",Number(button.dataset.step)===step ? "step" : "false"));root.querySelector("#builder-previous").hidden=step===1;root.querySelector("#builder-next").hidden=step===3;preview();}
  root.querySelector("#add-paper-question").onclick=()=>openEditor();
  root.querySelector("#cancel-paper-question").onclick=()=>{root.querySelector("#paper-question-editor").hidden=true;};
  editor.onsubmit=event=>{event.preventDefault();const data=Object.fromEntries(new FormData(editor));const values=["A","B","C","D"].map(letter=>data[`option-${letter}`].trim());while(values.length&&!values.at(-1))values.pop();if(values.length<2||values.some(value=>!value)||Number(data.answer)>=values.length){tell("请连续填写至少两个选项，并选择有效的正确答案");return;}const q={...(questions[editing]?.id ? {id:questions[editing].id} : {}),text:data.text.trim(),options:values,answer:Number(data.answer),explanation:data.explanation.trim(),chapter_id:data.chapter_id};if(editing<0)questions.push(q);else questions[editing]=q;root.querySelector("#paper-question-editor").hidden=true;renderQuestions();};
  root.querySelectorAll("[data-step]").forEach(button=>{button.onclick=()=>showStep(Number(button.dataset.step));});
  root.querySelector("#builder-next").onclick=()=>showStep(Math.min(3,step+1));root.querySelector("#builder-previous").onclick=()=>showStep(Math.max(1,step-1));settings.oninput=persist;settings.onsubmit=event=>event.preventDefault();
  async function save(publish){
    if(saving||!questions.length)return;
    if(publish&&step!==3){showStep(3);tell("请核对题目后再次点击发布");return;}
    if(!settings.checkValidity()){showStep(2);settings.reportValidity();return;}
    const data={...fields(),questions,status:publish||item?.status==="published" ? "published" : "draft"};
    if(data.status==="published"&&!data.due_at){showStep(2);tell("发布前请设置截止时间");return;}
    for(const field of ["open_at","due_at","close_at"])data[field]=data[field] ? new Date(data[field]).toISOString() : null;
    saving=true;root.querySelector("#builder-save-state").textContent="正在保存…";
    try{item=item ? await patch(`${path}/${encodeURIComponent(item.id)}`,data) : await post(path,data);questions=structuredClone(item.questions);sessionStorage.removeItem(draftKey);draftKey=`independent-assignment-draft:${user.id}:${course.id}:${item.id}`;const url=new URL(location.href);url.searchParams.set("assignment",item.id);history.replaceState(null,"",url);root.querySelector("#builder-save-state").textContent=item.status==="published" ? "作业已发布" : "作业草稿已保存";root.querySelector("#builder-save-result").innerHTML=`<a class="secondary" href="${esc(href("assignments",{course:course.id}))}">返回作业列表</a>`;renderQuestions();}catch(error){tell(error.message);root.querySelector("#builder-save-state").textContent=error.message;}finally{saving=false;}
  }
  root.querySelector("#builder-save-draft").onclick=()=>save(false);root.querySelector("#builder-publish").onclick=()=>save(true);renderQuestions();showStep(1);
}
