import { ensureApiKey, openApiKeyDialog } from "/static/ai-key.js";

export function mountVideo(config, user, fileUrl) {


const $ = (id) => document.getElementById(id);
const activeStates = new Set(["uploading", "queued", "running", "cancelling"]);
let selectedFile, currentJob, currentJobData, upload, timer, busy = false;
let summaryTimer, summarySubmitting = false, summaryLastAlert = "", summaryNetworkAlert = false;
const summaryActive = new Set(["preparing", "generating", "saving"]);
const stageOrder = {uploading: 0, queued: 1, probing: 1, scanning: 1, extracting: 2, pdf: 2, audio: 3, completed: 4};
const stateNames = {uploading: "传入中", queued: "等待处理", running: "处理中", cancelling: "取消中", completed: "已完成", failed: "失败", cancelled: "已取消", interrupted: "已中断"};

function formatSize(bytes) {
  return bytes > 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(2)} GB` : `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}
function formatTime(seconds) {
  if (seconds === null || seconds === undefined) return "时长未知";
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600), m = Math.floor(total / 60) % 60, s = total % 60;
  return `${h ? String(h).padStart(2, "0") + ":" : ""}${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
function showError(message) { $("error").textContent = message; $("error").hidden = false; }
function summaryError(message, key = message) {
  $("summary-status").hidden = false;
  $("summary-message").textContent = message;
  $("summary-progress").value = 0;
  $("summary-detail").textContent = "提取结果已保留，解决问题后可重试。";
  if (summaryLastAlert !== key) { summaryLastAlert = key; window.alert(message); }
}
function summaryView(job) {
  currentJobData = job;
  const state = job.summary || {};
  const active = summaryActive.has(state.status);
  const available = state.available ?? state.status === "completed";
  $("summary-section").hidden = job.status !== "completed";
  $("summary-generate").hidden = job.owner_id !== user.id;
  $("summary-generate").disabled = active || summarySubmitting;
  $("summary-generate").textContent = active ? "正在生成总结…" : state.status === "completed" ? "总结已生成" : state.status ? "重试生成总结" : "生成课程总结";
  $("summary-generate").hidden ||= available;
  $("summary-view").hidden = !available;
  $("summary-view").href = fileUrl(`/api/jobs/${job.id}/summary/view`);
  $("summary-download").hidden = !available;
  $("summary-download").href = fileUrl(`/api/jobs/${job.id}/summary/download`);
  $("summary-regenerate").hidden = !available || job.owner_id !== user.id;
  $("summary-delete").hidden = !state.status || job.owner_id !== user.id;
  $("summary-regenerate").disabled = active || summarySubmitting;
  $("summary-delete").disabled = active || summarySubmitting;
  $("summary-status").hidden = !state.status;
  $("summary-message").textContent = state.message || "";
  if (state.progress === null) $("summary-progress").removeAttribute("value");
  else $("summary-progress").value = state.progress || 0;
  const elapsed = Math.max(0, Math.floor((Date.now() - Date.parse(state.started_at)) / 1000));
  $("summary-detail").textContent = active ? `已等待 ${elapsed || 0} 秒 · 可离开本页，保持本地服务运行即可；刷新后会恢复进度。` : state.status === "completed" ? `已保存 · ${state.image_count} 张截图 · 用时 ${state.elapsed_seconds} 秒` : "提取结果已保留，可以重新生成。";
  if (["failed", "interrupted"].includes(state.status)) summaryError(state.message, `${job.id}:${state.started_at}:${state.status}`);
}
async function pollSummary(jobId) {
  clearTimeout(summaryTimer);
  try {
    const job = await api(`/api/jobs/${jobId}`);
    if (currentJob !== jobId) return;
    summaryNetworkAlert = false;
    summaryView(job);
    if (summaryActive.has(job.summary?.status)) summaryTimer = setTimeout(() => pollSummary(jobId), 1000);
    else await refreshHistory();
  } catch (error) {
    if (currentJob !== jobId) return;
    if (!summaryNetworkAlert) {
      summaryNetworkAlert = true;
      summaryError(`无法读取总结进度：${error.message}`, `network:${jobId}`);
    }
    if (![401, 403, 404].includes(error.status)) summaryTimer = setTimeout(() => pollSummary(jobId), 3000);
    else { $("summary-generate").disabled = false; }
  }
}
function setBusy(value) {
  busy = value;
  $("start").disabled = value || !selectedFile || !config;
  $("file").disabled = value;
  $("profile").disabled = value;
  $("dropzone").classList.toggle("disabled", value);
  $("dropzone").setAttribute("aria-disabled", String(value));
}
function chooseFile(file) {
  if (!file || busy) return;
  if (!config) { showError("本地服务尚未就绪，请稍后刷新。 "); return; }
  if (!/\.(mp4|mov|mkv|avi|webm|m4v|ts|mts)$/i.test(file.name)) {
    showError("请选择 MP4、MOV、MKV、AVI、WebM 等视频文件。"); return;
  }
  if (file.size === 0 || file.size > config.max_bytes) {
    showError(file.size === 0 ? "这个文件是空的，请重新选择。" : "Demo 单个视频上限为 8 GB。"); return;
  }
  selectedFile = file;
  $("file-title").textContent = file.name;
  $("file-detail").textContent = `${formatSize(file.size)} · 已选择，确认后开始处理`;
  $("error").hidden = true;
  setBusy(false);
}
async function api(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  let response, data;
  try {
    response = await fetch(url, {...options, signal: controller.signal, headers: {"X-Local-Token": config?.token || "", "X-Account-ID": user.id, ...options.headers}});
    data = await response.json();
  } catch (error) {
    throw new Error(error.name === "AbortError" ? "本地服务响应超时，请检查启动窗口和网络连接。" : "无法连接或读取本地服务，请确认启动窗口仍在运行，并检查网络连接。");
  } finally { clearTimeout(timeout); }
  if (!response.ok) {
    const error = new Error(typeof data.detail === "string" ? data.detail : `请求失败（${response.status}）`);
    error.status = response.status;
    throw error;
  }
  return data;
}
function progressView(stage, percent, message) {
  $("idle").hidden = true;
  $("result").hidden = true;
  $("processing").hidden = false;
  $("progress").value = percent;
  $("percent").textContent = `${Math.round(percent)}%`;
  $("message").textContent = message;
  $("state-label").textContent = stage === "uploading" ? "正在传入本机" : "正在整理课程";
  document.querySelectorAll(".stage-list li").forEach((item, index) => {
    item.classList.toggle("active", index === stageOrder[stage]);
    item.classList.toggle("done", index < stageOrder[stage]);
  });
}
function showJob(job) {
  clearTimeout(summaryTimer);
  $("summary-section").hidden = true;
  currentJob = job.id;
  currentJobData = job;
  const current = new URL(location.href); current.searchParams.set("job", job.id); history.replaceState(null, "", current);
  $("error").hidden = true;
  $("cancel").disabled = job.status === "cancelling";
  if (activeStates.has(job.status)) {
    setBusy(true);
    $("preview-section").hidden = true;
    progressView(job.stage, job.progress, job.message);
    return;
  }
  setBusy(false);
  $("processing").hidden = true;
  $("idle").hidden = true;
  if (job.status !== "completed") {
    $("result").hidden = true;
    $("preview-section").hidden = true;
    showError(job.message);
    return;
  }
  const result = job.result;
  $("result").hidden = false;
  $("result-meta").textContent = `${job.filename} · ${formatTime(result.metadata.duration)} · ${result.pages.length} 页`;
  $("warnings").replaceChildren();
  for (const message of result.warnings) {
    const note = document.createElement("p"); note.className = "warning"; note.textContent = message;
    $("warnings").append(note);
  }
  for (const [id, kind] of [["pdf-download", "pdf"], ["zip-download", "zip"], ["audio-download", "audio"]]) {
    $(id).href = fileUrl(`/api/jobs/${job.id}/files/${kind}`);
  }
  $("audio-download").hidden = !result.audio;
  $("page-count").textContent = `${result.pages.length} 页`;
  $("gallery").replaceChildren();
  for (const page of result.pages) {
    const card = document.createElement("button"); card.className = "page-card";
    const image = document.createElement("img"); image.loading = "lazy";
    image.src = fileUrl(`/api/jobs/${job.id}/pages/${page.number}`);
    image.alt = `第 ${page.number} 页课件`;
    const caption = document.createElement("span"); caption.className = "page-caption";
    const label = document.createElement("b"); label.textContent = `第 ${String(page.number).padStart(2, "0")} 页`;
    const timestamp = document.createElement("span"); timestamp.textContent = formatTime(page.timestamp);
    caption.append(label, timestamp); card.append(image, caption);
    card.addEventListener("click", () => {
      $("dialog-image").src = image.src;
      $("dialog-caption").textContent = `第 ${page.number} 页 · ${formatTime(page.timestamp)}`;
      $("image-dialog").showModal();
    });
    $("gallery").append(card);
  }
  $("preview-section").hidden = false;
  summaryView(job);
  if (summaryActive.has(job.summary?.status)) summaryTimer = setTimeout(() => pollSummary(job.id), 500);
}
async function poll(jobId) {
  clearTimeout(timer);
  try {
    const job = await api(`/api/jobs/${jobId}`);
    if (currentJob !== jobId) return;
    showJob(job);
    if (activeStates.has(job.status)) timer = setTimeout(() => poll(jobId), 800);
    else await refreshHistory();
  } catch (error) {
    if ([401, 403, 404].includes(error.status)) {
      setBusy(false); showError(error.message); return;
    }
    showError(`暂时无法读取进度：${error.message}。服务恢复后会继续尝试。`);
    timer = setTimeout(() => poll(jobId), 3000);
  }
}
async function refreshHistory(restore = false) {
  const jobs = await api("/api/jobs");
  $("history").replaceChildren();
  $("history-section").hidden = jobs.length === 0;
  for (const job of jobs) {
    const row = document.createElement("div"); row.className = "history-row"; row.dataset.jobId = job.id;
    const info = document.createElement("a"); info.className = "history-info";
    info.href = `/?page=video&account=${encodeURIComponent(user.id)}&job=${job.id}`; info.target = "_blank"; info.rel = "noopener";
    const name = document.createElement("span"); name.textContent = job.filename;
    const available = job.summary?.available ?? job.summary?.status === "completed";
    const generating = summaryActive.has(job.summary?.status);
    const summaryName = generating ? "总结生成中" : available ? "已有总结" : "";
    const status = document.createElement("small"); status.textContent = `${stateNames[job.status] || job.status} ${summaryName ? "· " + summaryName : ""} · ${new Date(job.created_at).toLocaleString("zh-CN", {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"})}`;
    info.append(name, status);
    const actions = document.createElement("div"); actions.className = "history-actions";
    if (available) {
      for (const [action, label] of [["view", "查看总结 ↗"], ["download", "下载总结 ↓"]]) {
        const link = document.createElement("a"); link.textContent = label;
        link.href = fileUrl(`/api/jobs/${job.id}/summary/${action}`);
        if (action === "view") { link.target = "_blank"; link.rel = "noopener"; }
        else link.download = "";
        actions.append(link);
      }
    }
    const remove = document.createElement("button"); remove.className = "danger"; remove.textContent = "删除任务";
    remove.disabled = activeStates.has(job.status) || generating;
    remove.title = remove.disabled ? "请等待处理完成，或先取消视频提取" : "删除任务记录及全部输出文件";
    remove.onclick = async () => {
      if (!window.confirm(`确定删除“${job.filename}”？任务记录、JPG/PDF/MP3、总结及关联课程资源将一并删除，无法恢复。`)) return;
      remove.disabled = true;
      try {
        await api(`/api/jobs/${job.id}`, {method: "DELETE"});
        if (currentJob === job.id) resetJob();
        await refreshHistory();
      } catch (error) { window.alert(`删除失败：${error.message}`); remove.disabled = false; }
    };
    actions.append(remove); row.append(info, actions);
    $("history").append(row);
  }
  if (restore) {
    const requested = new URLSearchParams(location.search).get("job");
    const active = jobs.find(job => activeStates.has(job.status));
    const job = requested ? await api(`/api/jobs/${encodeURIComponent(requested)}`) : active || jobs[0];
    if (job) { showJob(job); if (activeStates.has(job.status)) poll(job.id); }
  }
}
function resetJob() {
  clearTimeout(timer); clearTimeout(summaryTimer); currentJob = null; currentJobData = null;
  const url = new URL(location.href); url.searchParams.delete("job"); history.replaceState(null, "", url);
  for (const id of ["result", "processing", "preview-section", "summary-section", "error"]) $(id).hidden = true;
  $("idle").hidden = false; setBusy(false);
}
async function refreshCurrent() {
  await refreshHistory();
  if (!currentJob || summarySubmitting || upload) return;
  const jobId = currentJob;
  try {
    const job = await api(`/api/jobs/${jobId}`);
    if (currentJob === jobId) { showJob(job); if (activeStates.has(job.status)) poll(jobId); }
  } catch (error) {
    if (error.status === 404 && currentJob === jobId) { resetJob(); showError("当前任务已删除或不属于这个账号。"); }
    else throw error;
  }
}
$("history-refresh").onclick = () => refreshCurrent().catch(error => window.alert(error.message));
window.addEventListener("focus", () => {
  if (!summarySubmitting && !upload && !document.querySelector("dialog[open]")) refreshCurrent().catch(error => showError(error.message));
});
$("file").addEventListener("change", event => chooseFile(event.target.files[0]));
$("dropzone").addEventListener("keydown", event => {
  if (["Enter", " "].includes(event.key) && !busy) { event.preventDefault(); $("file").click(); }
});
for (const eventName of ["dragenter", "dragover"]) $("dropzone").addEventListener(eventName, event => {
  event.preventDefault(); if (!busy) $("dropzone").classList.add("dragging");
});
for (const eventName of ["dragleave", "drop"]) $("dropzone").addEventListener(eventName, event => {
  event.preventDefault(); $("dropzone").classList.remove("dragging");
  if (eventName === "drop") chooseFile(event.dataTransfer.files[0]);
});
window.addEventListener("dragover", event => event.preventDefault());
window.addEventListener("drop", event => event.preventDefault());
$("profile").addEventListener("change", () => {
  $("profile-help").textContent = {balanced:"每秒检查 5 帧，等待画面稳定后保留页面，并去除重复内容。", detailed:"对短暂停留和局部内容变化更敏感，可能保留更多动画中间页。", compact:"每秒检查 2 帧，要求页面停留更久，适合重复画面较多的视频。"}[$("profile").value];
});
$("start").addEventListener("click", () => {
  if (!selectedFile || busy) return;
  clearTimeout(timer); clearTimeout(summaryTimer); currentJob = null;
  $("summary-section").hidden = true;
  setBusy(true); $("error").hidden = true; $("preview-section").hidden = true;
  $("cancel").disabled = false;
  progressView("uploading", 0, "正在将视频写入项目目录…");
  upload = new XMLHttpRequest();
  upload.open("POST", `/api/jobs?profile=${encodeURIComponent($("profile").value)}`);
  upload.setRequestHeader("Content-Type", "application/octet-stream");
  upload.setRequestHeader("X-Filename", encodeURIComponent(selectedFile.name));
  upload.setRequestHeader("X-Local-Token", config.token);
  upload.setRequestHeader("X-Account-ID", user.id);
  upload.upload.onprogress = event => {
    const percent = event.lengthComputable ? event.loaded / event.total * 100 : 0;
    progressView("uploading", percent, `视频传入本机 ${Math.round(percent)}% · 完成后开始识别页面`);
  };
  upload.onload = async () => {
    const xhr = upload; upload = null;
    let job; try { job = JSON.parse(xhr.responseText); } catch { job = {}; }
    if (xhr.status >= 200 && xhr.status < 300) {
      showJob(job); await refreshHistory(); poll(job.id);
    } else {
      setBusy(false); $("processing").hidden = true; $("idle").hidden = false;
      showError(job.detail || "视频传入失败，请重试。");
    }
  };
  upload.onerror = () => { upload = null; setBusy(false); showError("无法连接本地服务，请检查启动窗口是否仍在运行。"); };
  upload.onabort = () => { upload = null; setBusy(false); $("processing").hidden = true; $("idle").hidden = false; showError("已取消视频传入。"); };
  upload.send(selectedFile);
});
async function generateSummary(regenerate = false) {
  if (!currentJob || summarySubmitting) return;
  if (regenerate && !window.confirm("重新生成会再次调用 DeepSeek 并使用 API 额度。成功后替换当前总结；失败时保留旧总结。是否继续？")) return;
  const jobId = currentJob;
  summarySubmitting = true; summaryLastAlert = ""; summaryNetworkAlert = false;
  $("summary-generate").disabled = true;
  $("summary-status").hidden = false;
  $("summary-message").textContent = "正在检查密钥并创建总结任务…";
  $("summary-progress").removeAttribute("value");
  $("summary-detail").textContent = "请稍候";
  try {
    if (!await ensureApiKey()) {
      summarySubmitting = false;
      if (currentJob === jobId) summaryView(currentJobData);
      return;
    }
    await api(`/api/jobs/${jobId}/summary`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({regenerate})});
    summarySubmitting = false;
    await refreshHistory();
    await pollSummary(jobId);
  } catch (error) {
    if (currentJob === jobId) {
      summaryError(`无法生成总结：${error.message}`);
      $("summary-generate").disabled = false;
      $("summary-generate").textContent = "重试生成总结";
      $("summary-regenerate").disabled = false;
      $("summary-delete").disabled = false;
      // A lost POST response does not imply the server failed to start the task.
      if (!error.status) summaryTimer = setTimeout(() => pollSummary(jobId), 3000);
    }
  } finally { summarySubmitting = false; }
}
$("summary-generate").onclick = () => generateSummary(false);
$("summary-regenerate").onclick = () => generateSummary(true);
$("summary-key").onclick = () => openApiKeyDialog();
$("summary-delete").onclick = async () => {
  if (!currentJob || summarySubmitting || !window.confirm("确定删除这份总结？课件 JPG、PDF 和音频会保留，之后可以重新生成总结。")) return;
  const jobId = currentJob;
  $("summary-delete").disabled = true;
  try {
    const job = await api(`/api/jobs/${jobId}/summary`, {method: "DELETE"});
    if (currentJob === jobId) { clearTimeout(summaryTimer); summaryLastAlert = ""; summaryView(job); }
    await refreshHistory();
  } catch (error) { window.alert(`删除总结失败：${error.message}`); $("summary-delete").disabled = false; }
};
$("cancel").addEventListener("click", async () => {
  if (upload) { upload.abort(); return; }
  if (!currentJob) return;
  $("cancel").disabled = true;
  try { showJob(await api(`/api/jobs/${currentJob}/cancel`, {method: "POST"})); }
  catch (error) { showError(error.message); $("cancel").disabled = false; }
});
$("close-dialog").addEventListener("click", () => $("image-dialog").close());
$("image-dialog").addEventListener("click", event => { if (event.target === $("image-dialog")) $("image-dialog").close(); });
(async () => {
  try { await refreshHistory(true); setBusy(busy); }
  catch (error) { showError(`本地服务连接失败：${error.message}`); }
})();
}
