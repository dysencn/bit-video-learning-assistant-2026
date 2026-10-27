const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败');
  return data;
}

function setResult(node, text, kind = 'ok') {
  node.className = `result ${kind}`;
  node.textContent = text;
}

async function loadCourses() {
  const data = await api('/api/courses');
  $('courses').innerHTML = data.courses.map(course => `<div class="course"><div><h3>${course.title}</h3><small>${course.tag} · ${course.teacher} · ${course.status}</small></div><div class="score">${course.progress}%</div></div>`).join('');
}

async function loadState() {
  const data = await api('/api/state');
  if (data.user) {
    $('userBadge').textContent = `${data.user.name} · ${data.user.role_name}`;
  }
  const progress = data.progress?.['course-001']?.percent;
  if (typeof progress === 'number') {
    $('progressText').textContent = `${progress}%`;
    $('progressBar').style.width = `${progress}%`;
  }
}

async function refreshHealth() {
  try { await api('/api/health'); $('health').textContent = '服务正常'; } catch { $('health').textContent = '服务异常'; }
}

$('loginBtn').addEventListener('click', async () => {
  const data = await api('/api/login', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ role: $('role').value }) });
  $('userBadge').textContent = `${data.user.name} · ${data.user.role_name}`;
});

$('videoForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const file = $('videoFile').files[0];
  if (!file) return;
  const result = $('videoResult');
  result.className = 'result'; result.textContent = '正在生成 PPTX、总结和题目…';
  try {
    const form = new FormData(); form.append('video', file);
    const data = await api('/api/video', { method: 'POST', body: form });
    result.className = 'result ok';
    result.innerHTML = `<strong>任务 ${data.job_id}：${data.status}</strong><br>${data.artifacts.map(a => `<a href="${a.path}" target="_blank">下载${a.name}</a>`).join('')}`;
  } catch (error) { setResult(result, error.message, 'warn'); }
});

$('quizForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const values = [...new FormData(event.target).values()];
  try {
    const data = await api('/api/quiz/submit', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ answers: values }) });
    await api('/api/progress', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ course_id: 'course-001', percent: Math.min(100, 42 + data.score * 10) }) });
    setResult($('quizResult'), `得分 ${data.score}/${data.total}（${data.percent}%）\n${data.feedback.join('\n')}`);
    await loadState();
  } catch (error) { setResult($('quizResult'), error.message, 'warn'); }
});

$('aiForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const result = $('aiResult'); result.className = 'result'; result.textContent = '正在请求…';
  try {
    const data = await api('/api/ai/ask', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ question: $('question').value }) });
    result.className = `result ${data.fallback ? 'warn' : 'ok'}`;
    result.textContent = `[${data.provider}] ${data.answer}\n耗时 ${data.latency_ms} ms${data.error ? ` · ${data.error}` : ''}`;
  } catch (error) { setResult(result, error.message, 'warn'); }
});

Promise.all([loadCourses(), loadState(), refreshHealth()]).catch(error => console.error(error));

