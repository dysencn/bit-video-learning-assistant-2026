import {esc, icon} from "/static/ui.js";
import {workspaceNav, teachingCourse} from "/static/teacher-workspace.js";

export async function classStudentsPage(ctx) {
  const {api, layout, root, tell, majorName, link} = ctx;
  const selection = await teachingCourse(ctx);
  if (!selection) return;
  const {course} = selection;
  const endpoint = `/api/courses/${encodeURIComponent(course.id)}/enrollments`;
  let data = await api(endpoint);
  let selected = new Set(data.student_ids);
  let search = "", filter = "all", saving = false;
  layout(`${workspaceNav(ctx, course, "students")}
    <div class="page-heading"><div><span class="eyebrow">CLASS ROSTER</span><h1>班级学生</h1><p class="muted">仅名单内学生可进入本班。移出学生会保留其历史作答。</p></div>${link("course", "预览学生页面", "secondary", {course:course.id, preview:"1"})}</div>
    <div class="catalog-stats roster-stats"><div><b id="roster-saved-count">${data.student_count}</b><span>本班学生</span></div><div><b id="roster-selected-count">${selected.size}</b><span>当前选择</span></div><div><b>${esc(majorName(course.major_ids[0]))}</b><span>学生专业</span></div></div>
    <section class="roster-panel"><div class="roster-toolbar"><label class="roster-search"><span class="sr-only">搜索学生</span><input id="roster-search" type="search" placeholder="搜索姓名或账号"></label><label><span class="sr-only">学生范围</span><select id="roster-filter"><option value="all">全部同专业学生</option><option value="enrolled">本班学生</option><option value="available">可加入学生</option><option value="other">已在其他班</option></select></label><button class="secondary" id="roster-select">全选可加入</button><button class="ghost" id="roster-clear">清空选择</button></div>
    <div class="table-scroll"><table class="roster-table"><thead><tr><th>加入本班</th><th>学生</th><th>账号</th><th>分班状态</th></tr></thead><tbody id="roster-rows"></tbody></table></div><div id="roster-error" class="roster-error" role="alert" hidden></div></section>
    <div class="roster-savebar"><div><strong id="roster-change-count">名单已保存</strong><span id="roster-save-description">${course.status === "published" ? "保存后立即生效" : "发布教学班后向名单内学生开放"}</span></div><div class="actions"><button class="ghost" id="roster-reset">撤销修改</button><button class="primary" id="roster-save">${icon("check")}保存班级名单</button></div></div>`, "班级学生");

  function canAdd(student) { return student.enrolled || !student.other_classes.length; }
  function visibleStudents() {
    return data.students.filter(student => `${student.name} ${student.username}`.toLowerCase().includes(search)
      && (filter === "all" || (filter === "enrolled" && selected.has(student.id))
        || (filter === "available" && !selected.has(student.id) && canAdd(student))
        || (filter === "other" && student.other_classes.length)));
  }
  function updateSavebar() {
    const original = new Set(data.student_ids);
    const added = [...selected].filter(id => !original.has(id)).length;
    const removed = [...original].filter(id => !selected.has(id)).length;
    root.querySelector("#roster-selected-count").textContent = selected.size;
    root.querySelector("#roster-change-count").textContent = added || removed ? `待保存：加入 ${added} 人 · 移出 ${removed} 人` : "名单已保存";
    root.querySelector("#roster-save").disabled = saving || !(added || removed);
    root.querySelector("#roster-reset").disabled = saving || !(added || removed);
  }
  function render() {
    const visible = visibleStudents();
    root.querySelector("#roster-rows").innerHTML = visible.map(student => {
      const other = student.other_classes.map(item => `${item.name} · ${item.teacher}`).join("、");
      return `<tr class="${selected.has(student.id) ? "roster-enrolled" : ""}"><td><input type="checkbox" data-roster-student="${esc(student.id)}" aria-label="将${esc(student.name)}加入本班" ${selected.has(student.id) ? "checked" : ""} ${saving || !canAdd(student) ? "disabled" : ""}></td><td><div class="roster-person"><span class="avatar small">${esc(student.name.slice(0,1))}</span><strong>${esc(student.name)}</strong></div></td><td><code>${esc(student.username)}</code></td><td>${selected.has(student.id) ? '<span class="tag published">本班</span>' : other ? `<span class="tag neutral">已在其他班</span><small>${esc(other)}</small>` : '<span class="tag neutral">可加入</span>'}</td></tr>`;
    }).join("") || '<tr><td colspan="4"><div class="empty">没有符合条件的学生</div></td></tr>';
    root.querySelectorAll("[data-roster-student]").forEach(input => { input.onchange = () => {
      if (input.checked) selected.add(input.dataset.rosterStudent); else selected.delete(input.dataset.rosterStudent);
      render();
    }; });
    root.querySelector("#roster-select").disabled = saving;
    root.querySelector("#roster-clear").disabled = saving;
    updateSavebar();
  }
  root.querySelector("#roster-search").oninput = event => { search = event.target.value.trim().toLowerCase(); render(); };
  root.querySelector("#roster-filter").onchange = event => { filter = event.target.value; render(); };
  root.querySelector("#roster-select").onclick = () => { visibleStudents().filter(canAdd).forEach(student => selected.add(student.id)); render(); };
  root.querySelector("#roster-clear").onclick = () => { selected.clear(); render(); };
  root.querySelector("#roster-reset").onclick = () => { selected = new Set(data.student_ids); render(); };
  root.querySelector("#roster-save").onclick = async () => {
    saving = true; render();
    root.querySelector("#roster-error").hidden = true;
    try {
      data = await api(endpoint, {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify({student_ids:[...selected]})});
      selected = new Set(data.student_ids);
      root.querySelector("#roster-saved-count").textContent = data.student_count;
      tell("班级名单已保存");
    } catch (error) {
      root.querySelector("#roster-error").textContent = error.message;
      root.querySelector("#roster-error").hidden = false;
    } finally { saving = false; render(); }
  };
  render();
}
