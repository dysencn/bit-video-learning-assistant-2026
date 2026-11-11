// The browser never persists API keys. The server keeps them only for this login.
let config, user, dialog, pending;

async function keyRequest(method = "GET", apiKey) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch("/api/ai/key", {
      method, signal: controller.signal,
      headers: {"X-Local-Token": config.token, "X-Account-ID": user.id, "Content-Type": "application/json"},
      ...(apiKey === undefined ? {} : {body: JSON.stringify({api_key: apiKey})}),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "无法设置 API 密钥");
    return body;
  } catch (error) {
    if (error.name === "AbortError" || error instanceof TypeError) throw new Error("无法连接本地服务，请检查启动窗口后重试。");
    throw error;
  } finally { clearTimeout(timeout); }
}

export function openApiKeyDialog() {
  if (pending) return pending;
  pending = new Promise(resolve => {
    const input = dialog.querySelector("input");
    const error = dialog.querySelector(".key-error");
    const submit = dialog.querySelector('[type="submit"]');
    input.value = ""; error.hidden = true; submit.disabled = false;
    dialog.onclose = () => {
      const saved = dialog.returnValue === "saved";
      input.value = ""; pending = null; resolve(saved);
    };
    dialog.querySelector("form").onsubmit = async event => {
      event.preventDefault(); submit.disabled = true; error.hidden = true;
      try {
        await keyRequest("POST", input.value.trim());
        dialog.close("saved");
      } catch (problem) {
        error.textContent = problem.message; error.hidden = false;
      } finally { submit.disabled = false; }
    };
    dialog.returnValue = "";
    dialog.showModal(); input.focus();
  });
  return pending;
}

export async function ensureApiKey() {
  const state = await keyRequest();
  return state.configured || await openApiKeyDialog();
}

export async function initializeApiKey(localConfig, account, {prompt = true} = {}) {
  config = localConfig; user = account;
  if (dialog?.isConnected) {
    const state = await keyRequest();
    if (prompt && !state.configured) await openApiKeyDialog();
    return;
  }
  dialog = document.createElement("dialog");
  dialog.id = "api-key-dialog";
  dialog.setAttribute("aria-labelledby", "api-key-title");
  dialog.innerHTML = `<form><h2 id="api-key-title">设置 DeepSeek API 密钥</h2>
    <p>未配置密钥时，可以设置环境变量 <b>DEEPSEEK_API_KEY</b>，也可以在这里输入，用于生成总结、AI 出题和学习分析。</p>
    <p>输入的密钥仅用于当前账号的本次登录，刷新后可继续使用；退出登录或重启服务后清除。不写入文件或浏览器存储。</p>
    <label for="api-key-input">API 密钥</label><input id="api-key-input" type="password" autocomplete="off" spellcheck="false" maxlength="512" placeholder="粘贴你的 DeepSeek API Key" required>
    <p class="key-error" role="alert" hidden></p>
    <div class="key-actions"><button type="button" id="api-key-cancel">稍后设置</button><button type="submit">使用此密钥</button></div></form>`;
  document.body.append(dialog);
  dialog.querySelector("#api-key-cancel").onclick = () => dialog.close("cancelled");
  const state = await keyRequest();
  if (prompt && !state.configured) await openApiKeyDialog();
}
