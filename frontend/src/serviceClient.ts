export type ServiceOptions = { method?: string; body?: unknown; signal?: AbortSignal };
export type DesktopApiResponse = { status: number; body: unknown; headers?: Record<string, string> };

export const serviceBase = new URLSearchParams(window.location.search).get("backendUrl") ?? "";
export const serviceUrl = (path: string) => `${serviceBase}${path}`;
let csrfTokenPromise: Promise<string> | null = null;

async function sessionToken(): Promise<string> {
  if (!csrfTokenPromise) csrfTokenPromise = fetch(serviceUrl("/api/session"), { credentials: "same-origin" })
    .then(async response => response.ok ? await response.json() as { csrfToken?: string } : {})
    .then(body => body.csrfToken ?? "")
    .catch(() => { csrfTokenPromise = null; return ""; });
  return csrfTokenPromise;
}

export async function serviceRequest<T>(path: string, options: ServiceOptions = {}): Promise<T> {
  if (!path.startsWith("/api/") && path !== "/health") throw new Error("无效的任务服务路径");
  const desktop = window.langbaiDesktop as (typeof window.langbaiDesktop & {
    requestApi?: (path: string, options: { method: string; body?: unknown }) => Promise<DesktopApiResponse>;
  });
  let status: number;
  let body: unknown;
  if (desktop?.requestApi) {
    const result = await desktop.requestApi(path, { method: options.method ?? "GET", body: options.body });
    status = result.status;
    body = result.body;
  } else {
    const method = options.method ?? "GET";
    const token = ["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase()) ? "" : await sessionToken();
    const response = await fetch(serviceUrl(path), {
      method, signal: options.signal, credentials: "same-origin",
      headers: { ...(options.body === undefined ? {} : { "Content-Type": "application/json" }), ...(token ? { "X-Langbai-CSRF": token } : {}) },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    status = response.status;
    body = await response.json().catch(() => null);
  }
  if (status < 200 || status >= 300) {
    if (status === 403) csrfTokenPromise = null;
    const detail = body && typeof body === "object" ? (body as { detail?: unknown }).detail : body;
    throw new Error(typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : `任务服务返回 HTTP ${status}`);
  }
  return body as T;
}
