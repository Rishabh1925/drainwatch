// Shared HTTP client for the operations API.
//
// One place decides the base URL, credential mode, and how a failed response becomes a
// typed error — so every call across the app behaves the same, and a 401 anywhere routes
// to the same re-authentication path. api/auth.ts predates this and keeps its own request
// helper (it works and is load-bearing for the session flow); everything new goes through
// here.
//
// Base URL is empty by default, which means same-origin: in development the Vite proxy
// forwards /api to the backend, and in production FastAPI serves this app and the API
// from one origin. Set VITE_API_BASE_URL only to point a build at a remote backend.

const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  status?: number;
  code?: string;
  constructor(message: string, status?: number, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

function messageForStatus(status: number): string {
  if (status === 401) return "Your session has expired. Sign in again to continue.";
  if (status === 403) return "You do not have permission to perform this action.";
  if (status === 404) return "That record could not be found.";
  if (status === 409) return "That action conflicts with the current state of the record.";
  if (status === 422) return "The request was incomplete or invalid.";
  if (status === 429) return "Too many requests. Wait a moment and try again.";
  if (status === 503) return "The service is unavailable. Try again or check System Health.";
  if (status >= 500) return "The request failed on the server. Try again or check System Health.";
  return "The request could not be completed.";
}

type RequestOptions = Omit<RequestInit, "body"> & { body?: unknown };

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, headers, ...rest } = options;
  const isForm = body instanceof FormData;

  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      ...rest,
      credentials: "include",
      headers: isForm
        ? headers
        : { "Content-Type": "application/json", ...(headers as Record<string, string>) },
      body: isForm ? (body as FormData) : body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("The service is unreachable. Check your connection and try again.");
  }

  if (response.status === 401) {
    // Let the app clear its session and route to login, exactly as api/auth.ts does.
    // The session-check endpoint is exempt: a 401 there is the normal "not signed in"
    // answer, not an expiry mid-session.
    if (path !== "/api/auth/session") {
      window.dispatchEvent(new CustomEvent("drainwatch:session-expired"));
    }
  }

  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      (payload && (payload.detail || payload.message)) || messageForStatus(response.status);
    throw new ApiError(
      typeof detail === "string" ? detail : messageForStatus(response.status),
      response.status,
      response.headers.get("x-error-code") || undefined,
    );
  }
  return payload as T;
}

export const get = <T>(path: string) => api<T>(path);
export const post = <T>(path: string, body?: unknown) => api<T>(path, { method: "POST", body });
export const patch = <T>(path: string, body?: unknown) => api<T>(path, { method: "PATCH", body });
