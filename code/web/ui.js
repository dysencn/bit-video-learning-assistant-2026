export const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));

const paths = {
  home: '<path d="m3 10 9-7 9 7v10H3Z"/><path d="M9 20v-7h6v7"/>',
  book: '<path d="M12 5C8 2 3 4 3 4v15s5-2 9 1c4-3 9-1 9-1V4s-5-2-9 1Z"/><path d="M12 5v15"/>',
  video: '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="m10 9 5 3-5 3Z"/>',
  review: '<path d="M3 10a9 9 0 1 1 2 8M3 4v6h6"/><path d="M12 7v5l3 2"/>',
  chart: '<path d="M4 3v17h17M8 15l4-5 4 2 5-7"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  plus: '<path d="M12 4v16M4 12h16"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5M5 7a8 8 0 0 1 13-2l2 2M4 17l2 2a8 8 0 0 0 13-2"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  chevron: '<path d="m6 9 6 6 6-6"/>',
  layers: '<path d="m12 3 10 5-10 5L2 8Zm-10 9 10 5 10-5M2 17l10 5 10-5"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
};
export const icon = name => `<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.book}</svg>`;
export const number = value => value === null || value === undefined ? "—" : value;
export const stateTag = student => student.not_started ? '<span class="tag neutral">待开始</span>'
  : student.needs_attention ? '<span class="tag attention">需关注</span>' : '<span class="tag published">正常</span>';

export function radarCard(student, personal = false) {
  const dimensions = [["课程参与", student.course_participation], ["练习积极", student.practice_activity],
    ["平均成绩", student.average_score], ["答题掌握", student.mastery_score]];
  const point = (i, score) => {
    const angle = -Math.PI/2 + Math.PI/2*i;
    const r = 85*Math.max(0, Math.min(100, Number(score) || 0))/100;
    return `${(180+Math.cos(angle)*r).toFixed(1)},${(155+Math.sin(angle)*r).toFixed(1)}`;
  };
  const rings = [25, 50, 75, 100].map(level => `<polygon class="radar-ring" points="${dimensions.map((_, i) => point(i, level)).join(" ")}"/>`).join("");
  const axes = dimensions.map((_, i) => `<line x1="180" y1="155" x2="${point(i,100).split(",")[0]}" y2="${point(i,100).split(",")[1]}"/>`).join("");
  const labels = dimensions.map(([name, score], i) => {
    const angle = -Math.PI/2 + Math.PI/2*i, x = 180+Math.cos(angle)*130, y = 155+Math.sin(angle)*121;
    return `<text x="${x}" y="${y}" text-anchor="middle"><tspan x="${x}">${name}</tspan><tspan x="${x}" dy="18" class="radar-value">${score === null ? "暂无" : Math.round(score ?? 0)}</tspan></text>`;
  }).join("");
  return `<div class="radar-card"><div class="radar-visual"><svg class="radar" viewBox="0 0 360 310" role="img" aria-label="${esc(student.student)}的四维学习雷达图">${rings}${axes}<polygon class="radar-area" points="${dimensions.map(([,v],i) => point(i,v)).join(" ")}"/>${dimensions.map(([,v],i) => `<circle class="radar-dot" cx="${point(i,v).split(",")[0]}" cy="${point(i,v).split(",")[1]}" r="3.5"/>`).join("")}${labels}</svg></div>
    <div class="radar-copy"><span class="eyebrow">LEARNING PROFILE</span><h3>${personal ? "我的学习雷达" : `${esc(student.student)}的学习雷达`}</h3>
    <p class="muted">${student.not_started ? "还没有正式练习记录。完成第一份练习后，这里会生成你的学习画像。" : `已参与 ${student.course_count} 门课程，留下 ${student.submission_count} 次正式练习记录。`}</p>
    <div class="radar-legend">${dimensions.map(([name, score]) => `<span><b>${number(score)}</b>${name}</span>`).join("")}</div>
    ${student.weak_points.length ? `<p class="radar-weak">${icon("target")}优先巩固：${esc(student.weak_points.join("、"))}</p>` : ""}
    <details class="metric-help"><summary>这四个维度如何计算？</summary><p>课程参与：已提交的可学课程数 / 本视图范围内本人可学的已发布课程数。练习积极：本视图课程范围内，与其他学生最高提交次数相比的相对值。平均成绩与答题掌握分别使用提交均分和逐题正确率。复习不计入成绩。${personal ? "学生画像覆盖本人可学及历史参与课程；教师画像仅覆盖该教师的课程，范围不同可能产生不同数值。" : "不同专业学生的可学课程数分别计算。"}</p></details></div></div>`;
}
