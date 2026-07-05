const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;
const TOKEN_KEY = "aiw_token";

export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const setToken = (t) => localStorage.setItem(TOKEN_KEY, t);
export const clearToken = () => localStorage.removeItem(TOKEN_KEY);

export const imageUrl = (id) => `${API}/images/${id}`;

async function req(path, { method = "GET", body, auth = true } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth && getToken()) headers["Authorization"] = `Bearer ${getToken()}`;
  const res = await fetch(`${API}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = "Ошибка запроса";
    try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

export const api = {
  getConfig: () => req("/config", { auth: false }),
  telegramLogin: (payload) => req("/auth/telegram", { method: "POST", body: payload, auth: false }),
  telegramWebApp: (initData) => req("/auth/telegram-webapp", { method: "POST", body: { init_data: initData }, auth: false }),
  devLogin: (payload) => req("/auth/dev-login", { method: "POST", body: payload, auth: false }),
  me: () => req("/auth/me"),
  models: () => req("/models", { auth: false }),
  listConversations: () => req("/conversations"),
  getConversation: (id) => req(`/conversations/${id}`),
  renameConversation: (id, title) => req(`/conversations/${id}`, { method: "PATCH", body: { title } }),
  deleteConversation: (id) => req(`/conversations/${id}`, { method: "DELETE" }),
};

export async function uploadImage(file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(`${API}/images`, {
    method: "POST",
    headers: { Authorization: `Bearer ${getToken()}` },
    body: fd,
  });
  if (!res.ok) {
    let detail = "Ошибка загрузки";
    try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

// Streaming chat via SSE-over-fetch.
export async function streamChat({ conversationId, model, content, imageIds = [], signal }, cbs) {
  let res;
  try {
    res = await fetch(`${API}/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}` },
      body: JSON.stringify({ conversation_id: conversationId || null, model, content, image_ids: imageIds }),
      signal,
    });
  } catch (e) {
    if (e.name !== "AbortError") cbs.onError?.("Сеть недоступна");
    cbs.onDone?.();
    return;
  }
  if (!res.ok) {
    let detail = "Ошибка генерации";
    try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
    cbs.onError?.(detail, res.status);
    cbs.onDone?.();
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop();
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data:")) continue;
        let evt;
        try { evt = JSON.parse(line.slice(5).trim()); } catch { continue; }
        if (evt.type === "meta") cbs.onMeta?.(evt);
        else if (evt.type === "delta") cbs.onDelta?.(evt.text);
        else if (evt.type === "error") cbs.onError?.(evt.message);
        else if (evt.type === "done") cbs.onDone?.();
      }
    }
  } catch { /* aborted or dropped — treat as finished */ }
  cbs.onDone?.();
}
