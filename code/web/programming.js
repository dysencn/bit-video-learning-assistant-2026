import {esc} from "/static/ui.js";
import {analysisLink} from "/static/analysis.js";

const caseFields = () => `<div class="python-case"><label>测试输入<textarea class="case-input" maxlength="8192" placeholder="允许空输入"></textarea></label><label>预期输出<textarea class="case-output" maxlength="8192" placeholder="允许空输出"></textarea></label><button class="ghost remove-case" type="button">移除此用例</button></div>`;

export function teacherProgramming(course) {
  const all = course.programming_questions || [];
  const active = all.filter(q => q.status !== "retired");
  const retired = all.filter(q => q.status === "retired");
  const chapterName = cid => course.chapters.find(c => c.id === cid)?.title || "未分章节";
  const chapterOptionsFor = selected => course.chapters.map(c => `<option value="${esc(c.id)}" ${c.id === selected ? "selected" : ""}>${esc(c.title)}</option>`).join("");
  const questionRow = (q, index, isRetired) => `<div class="editor-item ${isRetired ? "retired-item" : ""}"><details><summary><b>${esc(q.title)}</b><small>${esc(chapterName(q.chapter_id))} · ${q.test_cases.length} 个判题用例${isRetired ? " · 已停用" : ""}</small></summary>
    <details><summary>查看题意与用例</summary><p class="python-description">${esc(q.description)}</p>${q.test_cases.map((c, i) => `<p>测试 ${i + 1}</p><pre>输入：\n${esc(c.input)}\n预期输出：\n${esc(c.output)}</pre>`).join("")}</details>
    <form class="edit-programming" data-question="${esc(q.id)}"><label>编程题标题<input name="title" maxlength="100" value="${esc(q.title)}" required></label>
      <label>题目描述<textarea name="description" maxlength="5000" required>${esc(q.description)}</textarea></label>
      <label>章节<select name="chapter_id">${chapterOptionsFor(q.chapter_id)}</select></label><label>参考代码<textarea name="reference_code" maxlength="32768">${esc(q.reference_code || "")}</textarea></label><label>解题解析<textarea name="explanation" maxlength="3000">${esc(q.explanation || "")}</textarea></label>
      <div class="actions"><button class="secondary" type="submit">保存编程题</button>${isRetired ? `<button class="ghost" type="button" data-restore-programming="${esc(q.id)}">恢复题目</button>` : `<button class="ghost danger" type="button" data-retire-programming="${esc(q.id)}">停用题目</button>`}</div>
    </form>
    ${isRetired ? "" : `<div class="item-actions"><button class="ghost small" type="button" data-move-programming="${esc(q.id)}" data-direction="-1" ${index === 0 ? "disabled" : ""}>上移</button><button class="ghost small" type="button" data-move-programming="${esc(q.id)}" data-direction="1" ${index === active.length - 1 ? "disabled" : ""}>下移</button></div>`}
  </details></div>`;
  return `<section class="editor"><h2>Python 编程题</h2><p class="muted">填写题意、公开示例、判题输入输出，并可补充学生可查看的参考代码与解析。发布课程后学生可作答；已停用的题目不会下发给学生。</p>
    <div class="editor-list">${active.map((q, index) => questionRow(q, index, false)).join("") || "暂无编程题"}</div>
    ${retired.length ? `<details class="retired-list"><summary>已停用编程题 ${retired.length} 道（学生端不显示）</summary>${retired.map(q => questionRow(q, 0, true)).join("")}</details>` : ""}
    <form id="programming-form"><label>章节<select name="chapter_id">${course.chapters.map(c => `<option value="${esc(c.id)}">${esc(c.title)}</option>`).join("")}</select></label>
    <label>编程题标题<input name="title" maxlength="100" required placeholder="例如：两个整数之和"></label>
    <label>题目描述<textarea name="description" maxlength="5000" required placeholder="说明输入格式、输出格式、取值范围，以及需要完成的任务"></textarea></label>
    <label>参考代码（可选）<textarea name="reference_code" maxlength="32768"></textarea></label><label>解题解析（可选）<textarea name="explanation" maxlength="3000"></textarea></label><div class="python-io"><label>示例输入<textarea name="example_input" maxlength="8192" placeholder="2 3"></textarea></label><label>示例输出<textarea name="example_output" maxlength="8192" placeholder="5"></textarea></label></div>
    <p class="muted">该示例同时作为第一个判题用例；可再增加最多 9 个不公开的判题用例。每个用例最多运行 3 秒，内存 256 MB，输出 64 KB。</p>
    <div id="python-test-cases"></div><button class="secondary" id="add-python-case" type="button">增加判题用例</button>
    <div class="actions"><button type="submit" class="primary">添加编程题</button></div></form></section>`;
}

export function bindTeacherProgramming(ctx, course, reload) {
  const {root, post, patch, del, tell} = ctx;
  const form = root.querySelector("#programming-form"), cases = root.querySelector("#python-test-cases");
  root.querySelector("#add-python-case").onclick = () => {
    if (cases.children.length >= 9) return tell("最多 10 个判题用例（包括示例）");
    cases.insertAdjacentHTML("beforeend", caseFields());
  };
  cases.onclick = event => { if (event.target.classList.contains("remove-case")) event.target.closest(".python-case").remove(); };
  form.onsubmit = async event => {
    event.preventDefault(); const button = form.querySelector('[type="submit"]'); button.disabled = true;
    try {
      const fields = Object.fromEntries(new FormData(form));
      const example = {input:fields.example_input, output:fields.example_output};
      const tests = [...cases.children].map(row => ({input:row.querySelector(".case-input").value, output:row.querySelector(".case-output").value}));
      await post(`/api/courses/${encodeURIComponent(course.id)}/programming-questions`, {...fields, examples:[example], test_cases:[example, ...tests]});
      await reload(); tell("编程题已添加");
    } catch (error) { tell(error.message); }
    finally { button.disabled = false; }
  };
  root.querySelectorAll("form.edit-programming").forEach(item => {
    item.onsubmit = async event => {
      event.preventDefault();
      try {
        await patch(`/api/courses/${encodeURIComponent(course.id)}/programming-questions/${encodeURIComponent(item.dataset.question)}`,
          Object.fromEntries(new FormData(item)));
        await reload(); tell("编程题已保存");
      } catch (error) { tell(error.message); }
    };
  });
  root.querySelectorAll("[data-retire-programming]").forEach(button => {
    button.onclick = async () => {
      if (!confirm("停用后学生端不再显示该题，历史判题记录仍保留。继续？")) return;
      try { await del(`/api/courses/${encodeURIComponent(course.id)}/programming-questions/${encodeURIComponent(button.dataset.retireProgramming)}`); await reload(); tell("编程题已停用"); }
      catch (error) { tell(error.message); }
    };
  });
  root.querySelectorAll("[data-restore-programming]").forEach(button => {
    button.onclick = async () => {
      try { await patch(`/api/courses/${encodeURIComponent(course.id)}/programming-questions/${encodeURIComponent(button.dataset.restoreProgramming)}`, {status:"active"}); await reload(); tell("编程题已恢复"); }
      catch (error) { tell(error.message); }
    };
  });
  root.querySelectorAll("[data-move-programming]").forEach(button => {
    button.onclick = async () => {
      const ids = (course.programming_questions || []).map(question => question.id);
      const index = ids.indexOf(button.dataset.moveProgramming);
      const target = index + Number(button.dataset.direction);
      if (index < 0 || target < 0 || target >= ids.length) return;
      [ids[index], ids[target]] = [ids[target], ids[index]];
      try { await post(`/api/courses/${encodeURIComponent(course.id)}/programming-questions/reorder`, {question_ids:ids}); await reload(); tell("编程题顺序已更新"); }
      catch (error) { tell(error.message); }
    };
  });
}

function resultsHtml(run) {
  if (run.error) return `<p class="python-error">${esc(run.error)}</p>`;
  const finished = run.status === "completed";
  return `<p><b>${finished ? `通过 ${run.passed}/${run.total} 个用例 · ${run.score} 分` : `正在判题 ${run.completed}/${run.total}`}</b></p>
    ${run.results.map(r => `<details class="python-case-result" ${r.status !== "accepted" ? "open" : ""}><summary>${r.status === "accepted" ? "✓" : "✗"} 用例 ${r.case}：${esc(r.message)} · ${r.seconds} 秒</summary>${run.mode === "sample" ? `<div class="python-io"><div>预期输出<pre>${esc(r.expected || "（空输出）")}</pre></div><div>实际输出<pre>${esc(r.stdout || "（空输出）")}</pre></div></div>${r.stderr ? `<pre class="python-error">${esc(r.stderr)}</pre>` : ""}` : "<p>判题用例不公开输入输出；可运行示例查看调试信息。</p>"}</details>`).join("")}
    ${finished && run.completed < run.total ? '<p class="muted">因运行异常已停止，未执行用例按未通过计分。</p>' : ""}
    ${finished ? `<p class="muted">${run.submission_id ? "已保存到学习记录，并计入教师学习分析。" : "运行示例仅用于调试，不保存成绩。"}</p>` : ""}`;
}

export async function programmingPage(ctx, courseId, questionId) {
  const {api, post, root, layout, user, link} = ctx;
  if (user.role !== "student") throw new Error("请使用学生账号打开编程练习");
  const [course, logs] = await Promise.all([api(`/api/courses/${encodeURIComponent(courseId)}`), api("/api/learning-records")]);
  const question = (course.programming_questions || []).find(q => q.id === questionId);
  if (!question) throw new Error("编程题不存在，请刷新课程页面");
  const previous = logs.find(r => r.type === "python" && r.question_id === questionId);
  const key = `python-draft:${user.id}:${courseId}:${questionId}`;
  let draft = previous?.code || "# 使用 input() 读取输入，print() 输出结果\n";
  try { draft = sessionStorage.getItem(key) ?? draft; } catch {}
  layout(`<div class="page-heading"><div><span class="eyebrow">PYTHON PRACTICE</span><h1>${esc(question.title)}</h1><p class="muted">${esc(course.title)} · ${esc(course.chapters.find(c => c.id === question.chapter_id)?.title)}</p></div>${link("course", "返回课程", "secondary", {course:course.id})}</div>
    <div class="python-workspace"><section class="study-card"><h2>题目描述</h2><div class="python-description">${esc(question.description)}</div>${question.examples.map((sample,i) => `<h3>示例 ${i+1}</h3><div class="python-io"><div>输入<pre>${esc(sample.input || "（空输入）")}</pre></div><div>输出<pre>${esc(sample.output || "（空输出）")}</pre></div></div>`).join("")}
    ${question.reference_code || question.explanation ? `<details class="study-note"><summary>查看参考代码与解析</summary><div>${question.reference_code ? `<pre><code>${esc(question.reference_code)}</code></pre>` : ""}<p>${esc(question.explanation || "暂无解析")}</p></div></details>` : ""}<p class="muted">每个用例：${question.limits.seconds} 秒 / ${question.limits.memory_mb} MB / 输出 64 KB。超限自动停止。</p><p class="muted">从标准输入读取，不要输出提示语。逐行比较输出，忽略行尾空格与末尾换行，其余内容必须一致。</p></section>
    <section class="study-card"><div class="section-title"><h2>Python 编辑器</h2><span class="tag">本地运行</span></div><label for="python-code" class="muted">Tab 插入四个空格 · 最多 32 KB · 当前标签页自动保留草稿</label><div class="python-editor"><pre id="python-lines" aria-hidden="true"></pre><textarea id="python-code" spellcheck="false" autocapitalize="off" autocomplete="off" wrap="off" aria-label="Python 代码">${esc(draft)}</textarea></div>
    <div class="actions"><button class="secondary" id="python-sample">运行示例</button><button class="primary" id="python-submit">提交判题</button></div>
    <div id="python-running" hidden><progress id="python-progress" max="1" value="0"></progress><p id="python-stage" role="status"></p></div><div id="python-result" aria-live="polite">${previous ? `<p class="muted">上次提交：${previous.score} 分，已载入上次代码。</p>` : ""}</div></section></div>`, question.title);
  const editor = root.querySelector("#python-code"), lines = root.querySelector("#python-lines"), result = root.querySelector("#python-result");
  const update = () => { lines.textContent = editor.value.split("\n").map((_,i) => i+1).join("\n"); try { sessionStorage.setItem(key, editor.value); } catch {} };
  editor.addEventListener("input", update); update();
  editor.onscroll = () => { lines.scrollTop = editor.scrollTop; };
  editor.onkeydown = event => {
    if (event.key === "Tab") { event.preventDefault(); editor.setRangeText("    ", editor.selectionStart, editor.selectionEnd, "end"); update(); }
    if (event.key === "Enter") {
      event.preventDefault(); const prefix = editor.value.slice(0,editor.selectionStart).split("\n").pop();
      editor.setRangeText("\n" + (prefix.match(/^ */)?.[0] || "") + (prefix.trimEnd().endsWith(":") ? "    " : ""),editor.selectionStart,editor.selectionEnd,"end"); update();
    }
  };
  let busy = false;
  async function execute(mode, existingId) {
    if (busy) return; busy = true;
    const buttons = [root.querySelector("#python-sample"),root.querySelector("#python-submit")];
    buttons.forEach(b => b.disabled = true); root.querySelector("#python-running").hidden = false;
    const progress = root.querySelector("#python-progress"), stage = root.querySelector("#python-stage");
    progress.value = 0; stage.textContent = "正在准备运行…"; result.textContent = "";
    const began = Date.now();
    let runId = existingId;
    try {
      if (!runId) {
        const run = await post(`/api/courses/${encodeURIComponent(courseId)}/programming-questions/${encodeURIComponent(questionId)}/runs`, {mode,code:editor.value});
        runId = run.id;
        try { sessionStorage.setItem(key+":run", runId); } catch {}
      }
      while (true) {
        const run = await api(`/api/programming-runs/${runId}`);
        progress.max = run.total; progress.value = run.completed;
        stage.textContent = `已完成 ${run.completed}/${run.total} 个用例 · 等待 ${Math.floor((Date.now()-began)/1000)} 秒`;
        result.innerHTML = resultsHtml(run);
        if (run.submission_id) result.insertAdjacentHTML("beforeend", `<div class="actions">${analysisLink(link, run.submission_id)}</div>`);
        if (run.status === "completed") { try { sessionStorage.removeItem(key+":run"); } catch {} break; }
        if (Date.now()-began > 60000) throw new Error("等待时间较长，请刷新页面继续查看结果，不要重复提交。");
        await new Promise(resolve => setTimeout(resolve,350));
      }
    } catch (error) {
      result.innerHTML = `<p class="python-error">${esc(error.message)}</p>${runId && error.status !== 404 ? '<button class="secondary" id="python-reconnect">继续查看此次运行</button>' : ""}`;
      const retry = root.querySelector("#python-reconnect");
      if (retry) retry.onclick = () => execute(mode, runId);
      if (error.status === 404) { try { sessionStorage.removeItem(key+":run"); } catch {} }
    } finally { busy = false; buttons.forEach(b => b.disabled = false); root.querySelector("#python-running").hidden = true; }
  }
  root.querySelector("#python-sample").onclick = () => execute("sample");
  root.querySelector("#python-submit").onclick = () => execute("submit");
  try { const runId = sessionStorage.getItem(key+":run"); if (runId) await execute("submit",runId); } catch {}
}

export function programmingRecord(record) {
  return `<details class="python-history"><summary>Python 编程 · 用例通过 ${record.passed_cases}/${record.total_cases} · 查看代码与反馈</summary><pre>${esc(record.code)}</pre>${record.results.map(r => `<p>用例 ${r.case}：${esc(r.message)} · ${r.seconds} 秒</p>`).join("")}</details>`;
}
