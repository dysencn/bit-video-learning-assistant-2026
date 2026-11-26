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
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/>',
};
export const icon = name => `<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.book}</svg>`;
export const number = value => value === null || value === undefined ? "—" : value;
export const progressBar = (rate, label) => {
  const percent = Math.max(0, Math.min(100, Number(rate) || 0));
  return `<progress class="plan-progress" max="100" value="${percent}" aria-label="${esc(label || `已完成 ${percent}%`)}"></progress>`;
};
export const assignmentSubmissionText = item => item.attempts
  ? `本作业已提交 ${item.attempts} / ${item.max_attempts} 次`
  : `本作业尚未提交 · 最多 ${item.max_attempts} 次`;

export const stateTag = student => student.not_started ? '<span class="tag neutral">待开始</span>'
  : student.needs_attention ? '<span class="tag attention">需关注</span>' : '<span class="tag published">正常</span>';

const radarDimensions = [
  ["course_participation", "课程参与率", "课程参与", "已参与", "%"],
  ["practice_activity", "练习活跃度", "练习活跃", "提交", "%"],
  ["practice_average", "练习均分", "练习均分", "次练习", "分"],
  ["practice_accuracy", "练习正确率", "练习正确", "答对", "%"],
  ["assignment_completion", "作业提交率", "作业提交", "已交", "%"],
  ["assignment_on_time", "作业按时率", "作业按时", "按时", "%"],
  ["assignment_average", "作业均分", "作业均分", "份作业", "分"],
  ["reading_completion", "资料阅读完成率", "资料阅读", "已读", "%"],
  ["review_accuracy", "错题复习正确率", "错题复习", "答对", "%"],
];

function radarPlot(dimensions, studentName) {
  const centerX = 205, centerY = 166;
  const angle = i => -Math.PI / 2 + 2 * Math.PI * i / dimensions.length;
  const point = (i, score) => {
    const radius = 104 * Math.max(0, Math.min(100, score)) / 100;
    return [(centerX + Math.cos(angle(i)) * radius).toFixed(1),
      (centerY + Math.sin(angle(i)) * radius).toFixed(1)];
  };
  const positions = dimensions.map(({metric}, i) => point(i, metric.value));
  const rings = [25, 50, 75, 100].map(level => `<polygon class="radar-ring" points="${dimensions.map((_, i) => point(i, level)).join(" ")}"/>`).join("");
  const axes = dimensions.map((_, i) => {
    const [x, y] = point(i, 100);
    return `<line class="radar-axis" x1="${centerX}" y1="${centerY}" x2="${x}" y2="${y}"/>`;
  }).join("");
  const labels = dimensions.map((dimension, i) => {
    const direction = Math.cos(angle(i));
    const x = (centerX + direction * 121).toFixed(1);
    const y = (centerY + Math.sin(angle(i)) * 126).toFixed(1);
    const anchor = direction < -0.25 ? "end" : direction > 0.25 ? "start" : "middle";
    return `<text x="${x}" y="${y}" text-anchor="${anchor}">${dimension.axis}</text>`;
  }).join("");
  const dots = positions.map(([x, y]) => `<circle class="radar-dot" cx="${x}" cy="${y}" r="4"/>`).join("");
  return `<svg class="radar" viewBox="0 0 410 330" role="img" aria-label="${esc(studentName)}的学习雷达图，共 ${dimensions.length} 项有数据">
    ${rings}${axes}<polygon class="radar-area" points="${positions.join(" ")}"/>${dots}${labels}</svg>`;
}

function radarRows(dimensions) {
  return `<dl class="radar-legend">${dimensions.map(({label, countLabel, unit, metric}) => {
    const value = Number.isFinite(metric.value) ? `${metric.value}${unit}` : "暂无";
    const count = metric.denominator ? (metric.numerator === undefined ? `${metric.denominator} ${countLabel}`
      : `${countLabel} ${metric.numerator} / ${metric.denominator}`) : "";
    const pending = metric.pending_count ? `待交 ${metric.pending_count} 份` : "";
    const evidence = [count, pending].filter(Boolean).join(" · ");
    return `<div class="radar-metric"><dt>${label}</dt><dd><strong>${value}</strong>${evidence ? `<small>${esc(evidence)}</small>` : ""}</dd></div>`;
  }).join("")}</dl>`;
}

export function radarCard(student, personal = false) {
  const metrics = student.radar_dimensions || {};
  const available = [], missing = [];
  for (const [id, label, axis, countLabel, unit] of radarDimensions) {
    const metric = metrics[id] || {};
    const group = Number.isFinite(metric.value) ? available : missing;
    group.push({id, label, axis, countLabel, unit, metric});
  }
  let chart = '<p class="radar-empty">暂无可绘制的学习数据</p>';
  if (available.length >= 3) {
    chart = radarPlot(available, student.student);
  } else if (available.length) {
    chart = `<div class="radar-bars">${available.map(({label, axis, metric}) => `
      <div><b>${axis}</b><meter min="0" max="100" value="${metric.value}" aria-label="${label}"></meter></div>`).join("")}
      <div class="radar-bar-scale" aria-hidden="true"><span>0</span><span>50</span><span>100</span></div></div>`;
  }
  const pending = missing.find(({id}) => id === "assignment_completion")?.metric.pending_count;
  const title = personal ? "我的学习雷达" : `${esc(student.student)}的学习雷达`;
  return `<div class="radar-card">
    <div class="radar-header"><h3>${title}</h3><span class="radar-coverage">${available.length} / ${radarDimensions.length} 项有数据</span></div>
    <div class="radar-visual">${chart}</div>
    <div class="radar-copy">
    ${available.length ? radarRows(available) : ""}
    ${missing.length ? `<details class="radar-missing"><summary>暂无数据（${missing.length} 项）${pending ? `<small>待交 ${esc(pending)} 份</small>` : ""}</summary>
      ${radarRows(missing)}</details>` : ""}
    ${student.weak_points?.length ? `<p class="radar-weak">${icon("target")}优先巩固：${esc(student.weak_points.join("、"))}</p>` : ""}
    <details class="metric-help"><summary>九个维度如何计算？</summary>
      <p>课程参与按当前可学班级计算。练习活跃度以同一统计范围内的最高自主练习次数为基准。练习均分与正确率来自正式自主练习。</p>
      <p>作业提交率按已交或已到截止时间的作业计算。按时率看首次正式提交，均分取每份作业最近一次正式得分。</p>
      <p>讲义读到 60% 计入已读。错题复习正确率按有判分结果的复习次数计算。${personal ? "教师画像按该教师的课程统计，数值可能因范围不同而有所差异。" : ""}</p>
    </details></div></div>`;
}
