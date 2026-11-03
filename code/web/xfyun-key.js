// XFYun credentials remain in the authenticated server session, never in browser storage.
let config, user, dialog, pending;

async function credentialsRequest(method = "GET", value) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch("/api/ai/xfyun/credentials", {
      method, signal: controller.signal,
      headers: {"X-Local-Token": config.token, "X-Account-ID": user.id, "Content-Type": "application/json"},
      ...(value === undefined ? {} : {body: JSON.stringify(value)}),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "无法设置讯飞凭据");
    return body;
  } catch (error) {
    if (error.name === "AbortError" || error instanceof TypeError) throw new Error("无法连接本地服务，请检查启动窗口后重试。");
    throw error;
  } finally { clearTimeout(timeout); }
}

export function openXfyunDialog() {
  if (pending) return pending;
  pending = new Promise(resolve => {
    const form = dialog.querySelector("form");
    const error = dialog.querySelector(".key-error");
    const submit = form.querySelector('[type="submit"]');
    form.reset(); error.hidden = true; submit.disabled = false;
    dialog.onclose = () => {
      const saved = dialog.returnValue === "saved";
      form.reset(); pending = null; resolve(saved);
    };
    form.onsubmit = async event => {
      event.preventDefault(); submit.disabled = true; error.hidden = true;
      const fields = Object.fromEntries(new FormData(form));
      try {
        await credentialsRequest("POST", fields);
        dialog.close("saved");
      } catch (problem) {
        error.textContent = problem.message; error.hidden = false;
      } finally { submit.disabled = false; }
    };
    dialog.returnValue = "";
    dialog.showModal(); form.querySelector("input").focus();
  });
  return pending;
}

export async function ensureXfyunCredentials() {
  const state = await credentialsRequest();
  return state.configured || await openXfyunDialog();
}

export function initializeXfyunCredentials(localConfig, account) {
  config = localConfig; user = account;
  dialog = document.createElement("dialog");
  dialog.id = "xfyun-key-dialog";
  dialog.setAttribute("aria-labelledby", "xfyun-key-title");
  dialog.innerHTML = `<form><h2 id="xfyun-key-title">设置讯飞转写凭据</h2>
    <p>极速录音转写需要三项凭据，单独的 APIKey 无法完成接口鉴权。可以设置用户变量 XUNFEI_APP_ID、XUNFEI_API_KEY、XUNFEI_API_SECRET，或在这里输入。</p>
    <p>输入内容只保存在当前账号的本次服务会话中；退出登录或重启服务后清除。</p>
    <label>APPID<input name="app_id" type="password" autocomplete="off" spellcheck="false" maxlength="512" required></label>
    <label>APIKey<input name="api_key" type="password" autocomplete="off" spellcheck="false" maxlength="512" required></label>
    <label>APISecret<input name="api_secret" type="password" autocomplete="off" spellcheck="false" maxlength="512" required></label>
    <p class="key-error" role="alert" hidden></p>
    <div class="key-actions"><button type="button" id="xfyun-key-cancel">稍后设置</button><button type="submit">使用这些凭据</button></div></form>`;
  document.body.append(dialog);
  dialog.querySelector("#xfyun-key-cancel").onclick = () => dialog.close("cancelled");
}
