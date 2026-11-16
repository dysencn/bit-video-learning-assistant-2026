import {esc} from "/static/ui.js";
import {courseLabel} from "/static/teaching-courses.js";

// These actions only copy already generated questions. They never call the AI API.
export function questionAction(question, role) {
  if (role === "teacher") return `<div class="actions"><label><input type="checkbox" data-publish-question="${esc(question.id)}" checked> <span>选入课程练习</span></label></div><details><summary>审核正确答案与解析</summary><p>正确答案：${String.fromCharCode(65+question.answer)}. ${esc(question.options[question.answer])}</p><p>${esc(question.explanation)}</p></details>`;
  return `<div class="actions"><button type="button" class="secondary" data-add-wrong="${esc(question.id)}" disabled>正在读取错题本…</button><small role="status" data-wrong-message="${esc(question.id)}"></small></div>`;
}

export async function mountQuestionActions({ctx, quiz, jobId, request}) {
  const {root, user, config, link} = ctx;
  const panel = root.querySelector("#ai-quiz-actions");
  if (!config.features?.ai_quiz_actions) {
    panel.textContent = "请重启本地服务并刷新页面，以使用发布课程练习或加入错题本。";
    root.querySelectorAll("[data-add-wrong]").forEach(button => { button.textContent = "请先重启服务"; });
    return;
  }
  const send = (url, body) => request(url, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
  const errorText = error => error.name === "AbortError" ? "本地服务响应超时，请检查启动窗口后重试。" : error.message;
  if (user.role === "student") {
    panel.innerHTML = `<p class="muted">任何题目都可以主动加入错题本，无需先作答，不计答错次数或课程成绩。</p>${link("review-center", "打开错题本", "secondary")}`;
    const endpoint = `/api/jobs/${encodeURIComponent(jobId)}/quiz/wrong-questions`;
    const buttons = [...root.querySelectorAll("[data-add-wrong]")];
    function added(button) { button.disabled = true; button.textContent = "已加入错题本"; }
    buttons.forEach(button => {
      button.onclick = async () => {
        const message = root.querySelector(`[data-wrong-message="${CSS.escape(button.dataset.addWrong)}"]`);
        button.disabled = true; button.textContent = "正在加入…"; message.textContent = "";
        try {
          const result = await send(endpoint, {quiz_id:quiz.id, question_id:button.dataset.addWrong});
          added(button); message.textContent = result.added ? "已保存，可在错题本查看和复习。" : "这道题已在错题本中。";
        } catch(error) {
          button.disabled = false; button.textContent = "重试加入错题本"; message.textContent = errorText(error);
        }
      };
    });
    try {
      const membership = await request(endpoint);
      if (membership.quiz_id !== quiz.id) throw new Error("AI 练习版本已变化，请刷新后再操作。");
      for (const button of buttons) {
        if (membership.question_ids.includes(button.dataset.addWrong)) added(button);
        else { button.disabled = false; button.textContent = "加入错题本"; }
      }
    } catch(error) {
      for (const button of buttons) { button.disabled = false; button.textContent = "加入错题本"; }
      panel.insertAdjacentHTML("beforeend", `<p class="error-box" role="alert">错题本状态读取失败：${esc(errorText(error))}；点击加入时会再次检查。</p>`);
    }
    return;
  }
  panel.innerHTML = '<p role="status">正在读取本人课程…</p>';
  const courses = (await request("/api/courses")).filter(course => course.status !== "archived");
  if (!courses.length) {
    panel.innerHTML = `<p class="muted">还没有可编辑的课程，请先创建课程，再回到这里发布题目。</p>${link("new-course", "创建课程", "secondary")}`;
    return;
  }
  courses.sort((a,b) => Number(b.status === "published")-Number(a.status === "published"));
  panel.innerHTML = `<h2>发布为课程练习</h2><p class="muted">先核对每题的正确答案与解析，再勾选需要发布的题目。题目加入所选章节的自主练习题库。</p><div class="actions"><label>目标课程 / 教学班<select id="quiz-publish-course">${courses.map(course => `<option value="${esc(course.id)}">${esc(courseLabel(course))}${course.status === "published" ? " · 已发布" : " · 草稿"}</option>`).join("")}</select></label><label>目标章节<select id="quiz-publish-chapter"></select></label></div><p class="muted" id="quiz-publish-guide"></p><div class="actions"><button type="button" class="secondary" id="quiz-publish-select-all">全选未发布题目</button><button type="button" class="primary" id="quiz-publish-submit">发布到课程练习</button><span id="quiz-publish-count" role="status"></span></div><div id="quiz-publish-result" role="status"></div>`;
  const $ = id => panel.querySelector(`#${id}`);
  const checks = [...root.querySelectorAll("[data-publish-question]")];
  let course, loading = false, saving = false, selection = 0;
  function count() {
    const n = checks.filter(input => input.checked && !input.disabled).length;
    $("quiz-publish-count").textContent = `已勾选 ${n} 道题`;
    $("quiz-publish-submit").disabled = loading || saving || !course || !n || !$("quiz-publish-chapter").value;
    $("quiz-publish-submit").textContent = saving ? "正在保存题目…" : course?.status === "published" ? "发布到课程练习" : "加入草稿题库";
  }
  checks.forEach(input => { input.onchange = count; });
  root.querySelector("#ai-quiz-form").addEventListener("reset", () => setTimeout(count, 0));
  $("quiz-publish-select-all").onclick = () => { checks.forEach(input => { if (!input.disabled) input.checked = true; }); count(); };
  $("quiz-publish-chapter").onchange = count;
  async function chooseCourse() {
    const current = ++selection, id = $("quiz-publish-course").value;
    loading = true; course = null; count(); $("quiz-publish-result").replaceChildren();
    try {
      const selected = await request(`/api/courses/${encodeURIComponent(id)}`);
      if (current !== selection) return;
      course = selected;
      $("quiz-publish-chapter").innerHTML = course.chapters.map(ch => `<option value="${esc(ch.id)}">${esc(ch.title)}</option>`).join("");
      const imported = new Set(course.questions.filter(q => q.ai_source?.job_id === jobId && q.ai_source?.quiz_id === quiz.id).map(q => q.ai_source.question_id));
      checks.forEach(input => { input.disabled = imported.has(input.dataset.publishQuestion); input.checked = !input.disabled; input.defaultChecked = input.checked; input.nextElementSibling.textContent = input.disabled ? "已加入本课程题库" : "选入课程练习"; });
      $("quiz-publish-guide").textContent = course.status === "published" ? "已发布课程：保存后，面向该课程的学生刷新页面即可练习。重复发布会自动跳过已存在的题目。" : "草稿课程：本次只保存题库，需在课程管理完成发布后学生才可见。";
    } catch(error) { if (current === selection) $("quiz-publish-result").textContent = `读取课程失败：${errorText(error)}，请重新选择课程重试。`; }
    finally { if (current === selection) { loading = false; count(); } }
  }
  $("quiz-publish-course").onchange = chooseCourse;
  $("quiz-publish-submit").onclick = async () => {
    if (saving || loading || !course) return;
    const ids = checks.filter(input => input.checked && !input.disabled).map(input => input.dataset.publishQuestion);
    if (!ids.length) return;
    saving = true; count(); $("quiz-publish-course").disabled = true; $("quiz-publish-chapter").disabled = true;
    $("quiz-publish-result").textContent = "正在保存课程练习…";
    try {
      const result = await send(`/api/courses/${encodeURIComponent(course.id)}/questions/import-ai`, {job_id:jobId, quiz_id:quiz.id, chapter_id:$("quiz-publish-chapter").value, question_ids:ids});
      course = result.course;
      const imported = new Set(course.questions.filter(q => q.ai_source?.job_id === jobId && q.ai_source?.quiz_id === quiz.id).map(q => q.ai_source.question_id));
      checks.forEach(input => { input.disabled = imported.has(input.dataset.publishQuestion); input.checked = !input.disabled; input.defaultChecked = input.checked; input.nextElementSibling.textContent = input.disabled ? "已加入本课程题库" : "选入课程练习"; });
      $("quiz-publish-result").innerHTML = `<div class="result"><b>新增 ${result.imported_ids.length} 道练习，${result.existing_ids.length} 道已存在。</b><p>${course.status === "published" ? "已发布到课程自主练习，学生刷新课程即可作答。" : "已保存到草稿课程题库；发布课程后学生才可作答。"}</p>${link("edit-course", "查看课程题库", "secondary", {course:course.id, section:"questions"})}</div>`;
    } catch(error) { $("quiz-publish-result").textContent = `发布失败：${errorText(error)}，可重试；重复请求不会重复添加。`; }
    finally { saving = false; $("quiz-publish-course").disabled = false; $("quiz-publish-chapter").disabled = false; count(); }
  };
  await chooseCourse();
}
