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
  ? `已提交 ${item.attempts} / ${item.max_attempts} 次`
  : `未提交 · 最多 ${item.max_attempts} 次`;

export const stateTag = student => student.not_started ? '<span class="tag neutral">待开始</span>'
  : student.needs_attention ? '<span class="tag attention">需关注</span>' : '<span class="tag published">正常</span>';

export const masteryLabels = {
  mastered: "已掌握", in_progress: "学习中", needs_review: "待巩固", unassessed: "待测评",
};

const radarGroups = [
  {title:"学习投入", graphic:"layers", tone:"engagement", axes:[
    ["course_participation", "课程参与率", "课程参与", "已参与", "%"],
    ["practice_activity", "练习活跃度", "练习活跃", "提交", "%"],
    ["assignment_completion", "作业提交率", "作业提交", "已交", "%"],
    ["assignment_on_time", "作业按时率", "作业按时", "按时", "%"],
    ["reading_completion", "资料阅读完成率", "资料阅读", "已读", "%"],
  ]},
  {title:"练习与作业表现", graphic:"target", tone:"performance", axes:[
    ["practice_average", "练习均分", "练习均分", "次练习", "分"],
    ["practice_accuracy", "练习正确率", "练习正确", "答对", "%"],
    ["review_accuracy", "错题复习正确率", "复习正确", "答对", "%"],
    ["assignment_average", "作业均分", "作业均分", "份作业", "分"],
  ]},
];

function radarValue({metric, unit}) {
  return Number.isFinite(metric.value) ? `${metric.value}${unit}` : "暂无";
}

function radarPlot(dimensions, label) {
  const centerX = 190, centerY = 152;
  const angle = i => -Math.PI / 2 + 2 * Math.PI * i / dimensions.length;
  const point = (i, score) => {
    const radius = 85 * Math.max(0, Math.min(100, score)) / 100;
    return [(centerX + Math.cos(angle(i)) * radius).toFixed(1),
      (centerY + Math.sin(angle(i)) * radius).toFixed(1)];
  };
  const positions = dimensions.map(({metric}, i) => Number.isFinite(metric.value) ? point(i, metric.value) : null);
  const rings = [25, 50, 75, 100].map(level => `<polygon class="radar-ring" points="${dimensions.map((_, i) => point(i, level)).join(" ")}"/>`).join("");
  const axes = dimensions.map((_, i) => {
    const [x, y] = point(i, 100);
    return `<line class="radar-axis${positions[i] ? "" : " radar-axis-missing"}" x1="${centerX}" y1="${centerY}" x2="${x}" y2="${y}"/>`;
  }).join("");
  const labels = dimensions.map((dimension, i) => {
    const direction = Math.cos(angle(i));
    const sine = Math.sin(angle(i));
    const x = (centerX + direction * 103).toFixed(1);
    const labelOffset = sine < -.95 || Math.abs(sine) < .36 ? 7 : 0;
    const y = (centerY + sine * 110 - labelOffset).toFixed(1);
    const anchor = direction < -0.25 ? "end" : direction > 0.25 ? "start" : "middle";
    return `<text x="${x}" y="${y}" text-anchor="${anchor}">${dimension.axis}
      <tspan x="${x}" dy="21" class="${positions[i] ? "radar-axis-value" : "radar-value-missing"}">${radarValue(dimension)}</tspan></text>`;
  }).join("");
  const dots = positions.filter(Boolean).map(([x, y]) => `<circle class="radar-dot" cx="${x}" cy="${y}" r="4.5"/>`).join("");
  const marks = positions.every(Boolean) ? `<polygon class="radar-area" points="${positions.join(" ")}"/>`
    : positions.map((from, index) => {
      const to = positions[(index + 1) % positions.length];
      return from && to ? `<line class="radar-segment" x1="${from[0]}" y1="${from[1]}" x2="${to[0]}" y2="${to[1]}"/>` : "";
    }).join("");
  return `<svg class="radar" viewBox="0 0 380 294" role="img" aria-label="${esc(label)}">
    ${rings}${axes}${marks}${dots}${labels}</svg>`;
}

function radarRows(dimensions) {
  return `<dl class="radar-legend">${dimensions.map(dimension => {
    const {label, countLabel, metric} = dimension;
    const count = metric.denominator ? (metric.numerator === undefined ? `${metric.denominator} ${countLabel}`
      : `${countLabel} ${metric.numerator} / ${metric.denominator}`) : "";
    const pending = metric.pending_count ? `待交 ${metric.pending_count} 份` : "";
    const evidence = [count, pending].filter(Boolean).join(" · ");
    return `<div class="radar-metric${Number.isFinite(metric.value) ? "" : " radar-metric-missing"}"><dt>${label}</dt><dd><strong>${radarValue(dimension)}</strong>${evidence ? `<small>${esc(evidence)}</small>` : ""}</dd></div>`;
  }).join("")}</dl>`;
}

export function radarCard(student, personal = false) {
  const metrics = student.radar_dimensions || {};
  const title = personal ? "我的学习雷达" : `${esc(student.student)}的学习雷达`;
  const charts = radarGroups.map(({title, graphic, tone, axes}) => {
    const dimensions = axes.map(([id, label, axis, countLabel, unit]) => ({label, axis, countLabel, unit, metric:metrics[id] || {}}));
    const known = dimensions.filter(({metric}) => Number.isFinite(metric.value)).length;
    return `<section class="radar-section radar-${tone}">
      <div class="radar-group-heading"><span class="radar-icon">${icon(graphic)}</span><h4>${title}</h4>
        <span class="radar-coverage">${known} / ${axes.length} 项有数据</span></div>
      <div class="radar-visual">${radarPlot(dimensions, `${student.student}的${title}雷达图，${axes.length} 项中 ${known} 项有数据`)}</div>
      ${radarRows(dimensions)}</section>`;
  }).join("");
  return `<div class="radar-card">
    <div class="radar-header"><h3>${title}</h3><span class="radar-coverage">0–100 刻度 · 九项指标</span></div>
    <div class="radar-compare">${charts}</div>
    <div class="radar-foot">
    ${!personal && student.weak_points?.length ? `<p class="radar-weak">${icon("target")}优先巩固：${esc(student.weak_points.join("、"))}</p>` : ""}
    <details class="metric-help"><summary>九个维度如何计算？</summary>
      <p>课程参与按当前可学班级计算。练习活跃度以同一统计范围内的最高自主练习次数为基准。练习均分与正确率来自正式自主练习。</p>
      <p>作业提交率按已交或已到截止时间的作业计算。按时率看首次正式提交，均分取每份作业最近一次正式得分。</p>
      <p>讲义读到 60% 计入已读。错题复习正确率按有判分结果的复习次数计算。未取得数据的轴显示「暂无」。${personal ? "教师按自己所教的班级统计。" : ""}</p>
    </details></div></div>`;
}
