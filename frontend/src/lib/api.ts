/** API client: every call carries this tab's session id; errors keep the catalog shape. */

const SESSION_KEY = "fv-session-id";

export function sessionId(): string {
  let id: string | null = null;
  try {
    id = sessionStorage.getItem(SESSION_KEY);
  } catch {
    /* storage blocked: fall back to a per-load id */
  }
  if (!id) {
    id = crypto.randomUUID();
    try {
      sessionStorage.setItem(SESSION_KEY, id);
    } catch {
      /* ignore */
    }
  }
  return id;
}

export type ApiErrorBody = { code: string; title: string; message: string; detail?: unknown };

export class ApiError extends Error {
  constructor(public status: number, public body: ApiErrorBody) {
    super(`${body.code}: ${body.message}`);
  }
}

function headers(extra?: Record<string, string>): Record<string, string> {
  return { "X-Session-Id": sessionId(), ...extra };
}

async function parse<T>(r: Response): Promise<T> {
  if (r.ok) return (await r.json()) as T;
  let body: ApiErrorBody = { code: "FV-SYS-001", title: "Internal error", message: r.statusText };
  try {
    body = (await r.json()).error ?? body;
  } catch {
    /* non-JSON error */
  }
  throw new ApiError(r.status, body);
}

export async function get<T>(path: string): Promise<T> {
  return parse<T>(await fetch(path, { headers: headers() }));
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  return parse<T>(await fetch(path, { method: "POST", headers: headers({ "Content-Type": "application/json" }), body: body === undefined ? undefined : JSON.stringify(body) }));
}

export async function postForm<T>(path: string, form: FormData): Promise<T> {
  return parse<T>(await fetch(path, { method: "POST", headers: headers(), body: form }));
}

/** POST that answers with server-sent events (`data: {...}` lines); calls onItem for each. */
export async function postStream(path: string, onItem: (item: Record<string, unknown>) => void): Promise<void> {
  const r = await fetch(path, { method: "POST", headers: headers() });
  if (!r.ok || !r.body) {
    await parse(r);
    return;
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let k: number;
    while ((k = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, k);
      buf = buf.slice(k + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (line) onItem(JSON.parse(line.slice(6)));
    }
  }
}

export function errorText(e: unknown): string {
  if (e instanceof ApiError) return `${e.body.title} (${e.body.code}): ${e.body.message}`;
  return e instanceof Error ? e.message : String(e);
}
