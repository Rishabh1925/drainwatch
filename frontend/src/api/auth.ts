export type UserRole = "OPERATOR" | "ADMIN";

export type AuthUser = {
  id: string;
  name: string;
  email: string;
  role: UserRole;
  title?: string;
  organization?: string;
  jurisdiction?: string;
  status?: string;
  last_sign_in?: string;
  timezone?: string;
  notifications?: boolean;
  map_preference?: string;
  avatar_url?: string;
};

export class AuthApiError extends Error {
  code?: string;
  status?: number;
  constructor(message: string, status?: number, code?: string) {
    super(message);
    this.name = "AuthApiError";
    this.status = status;
    this.code = code;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      credentials: "include",
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new AuthApiError("The authentication service is unavailable. Check your connection and try again.");
  }
  const body = await response.json().catch(() => null) as { message?: string; code?: string } | null;
  if (!response.ok) {
    if (response.status === 401 && path !== "/api/auth/session" && !path.endsWith("/login")) {
      window.dispatchEvent(new CustomEvent("drainwatch:session-expired"));
    }
    const fallback = response.status === 401
      ? "Email or password is incorrect."
      : response.status === 403
        ? "This account is disabled or does not have access."
        : "The request could not be completed. Try again.";
    const message = response.status === 401 && path.endsWith("/login") ? fallback : body?.message || fallback;
    throw new AuthApiError(message, response.status, body?.code);
  }
  return body as T;
}

function unwrapUser(payload: AuthUser | { user: AuthUser }) {
  return "user" in payload ? payload.user : payload;
}

export async function getSession(): Promise<AuthUser | null> {
  try {
    return unwrapUser(await request<AuthUser | { user: AuthUser }>("/api/auth/session"));
  } catch (error) {
    if (error instanceof AuthApiError && (error.status === 401 || error.status === 403)) return null;
    throw error;
  }
}

export async function signIn(email: string, password: string, admin = false): Promise<AuthUser> {
  const payload = await request<AuthUser | { user: AuthUser }>(admin ? "/api/auth/admin/login" : "/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  const user = unwrapUser(payload);
  if (admin && user.role !== "ADMIN") {
    await signOut().catch(() => undefined);
    throw new AuthApiError("This account does not have administrator access.", 403, "ADMIN_REQUIRED");
  }
  return user;
}

export async function signOut() {
  await request<unknown>("/api/auth/logout", { method: "POST", body: "{}" });
}

export async function requestPasswordReset(email: string) {
  await request<unknown>("/api/auth/forgot-password", { method: "POST", body: JSON.stringify({ email }) });
}

export async function requestAccess(email: string) {
  await request<unknown>("/api/auth/request-access", { method: "POST", body: JSON.stringify({ email }) });
}

export async function signUp(fields: {
  name: string;
  email: string;
  password: string;
  organization?: string;
  jurisdiction?: string;
}): Promise<AuthUser> {
  const payload = await request<AuthUser | { user: AuthUser }>("/api/auth/signup", {
    method: "POST",
    body: JSON.stringify(fields),
  });
  return unwrapUser(payload);
}

export async function updateProfile(changes: Partial<AuthUser>) {
  return unwrapUser(await request<AuthUser | { user: AuthUser }>("/api/profile", {
    method: "PATCH",
    body: JSON.stringify(changes),
  }));
}

export type AdminUser = Pick<AuthUser, "id" | "name" | "email" | "role" | "status" | "last_sign_in">;
export type AdminSource = {
  id: string;
  type: string;
  status: string;
  coverage: string;
  last_frame?: string;
  last_analysis?: string;
  latitude?: number;
  longitude?: number;
};

export async function getAdminUsers() {
  const payload = await request<AdminUser[] | { users: AdminUser[] }>("/api/admin/users");
  return Array.isArray(payload) ? payload : payload.users;
}

export async function getAdminSources() {
  const payload = await request<AdminSource[] | { sources: AdminSource[] }>("/api/admin/sources");
  return Array.isArray(payload) ? payload : payload.sources;
}

export async function setUserEnabled(id: string, enabled: boolean) {
  return request<unknown>(`/api/admin/users/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify({ enabled }),
  });
}

export async function setSourceEnabled(id: string, enabled: boolean) {
  return request<unknown>(`/api/admin/sources/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify({ enabled }),
  });
}

export async function createSource(source: Omit<AdminSource, "status" | "last_frame" | "last_analysis">) {
  return request<AdminSource>("/api/admin/sources", { method: "POST", body: JSON.stringify(source) });
}
