import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import pc from "picocolors";
import { loadConfig } from "./config.js";

const require = createRequire(import.meta.url);

export class ApiError extends Error {
  public readonly status: number;
  public readonly code?: string;
  public readonly body: unknown;

  constructor(status: number, message: string, code: string | undefined, body: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.body = body;
  }
}

export interface RequestOptions {
  method?: string;
  query?: Record<string, string | number | boolean | undefined | null>;
  body?: unknown;
  headers?: Record<string, string>;
  anonymous?: boolean;
}

let _cachedVersion: string | null = null;
function pkgVersion(): string {
  if (_cachedVersion !== null) return _cachedVersion;
  try {
    const pkgPath = require.resolve("../../package.json");
    const pkg = JSON.parse(readFileSync(pkgPath, "utf8")) as { version?: string };
    _cachedVersion = pkg.version ?? "dev";
  } catch {
    _cachedVersion = "dev";
  }
  return _cachedVersion;
}

export async function apiRequest<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const cfg = loadConfig();
  const url = new URL(path.startsWith("/") ? path : `/${path}`, cfg.apiUrl);
  if (opts.query) {
    for (const [k, v] of Object.entries(opts.query)) {
      if (v === undefined || v === null || v === "") continue;
      url.searchParams.set(k, String(v));
    }
  }

  const headers: Record<string, string> = {
    "User-Agent": `atelier-cli/${pkgVersion()}`,
    Accept: "application/json",
    ...opts.headers,
  };
  if (!opts.anonymous && cfg.token) {
    headers["Authorization"] = `Bearer ${cfg.token}`;
  }
  let body: BodyInit | undefined;
  if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }

  let res: Response;
  try {
    res = await fetch(url, { method: opts.method ?? "GET", headers, body });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new ApiError(0, `Network error: ${msg}`, "NETWORK_ERROR", null);
  }

  if (res.status === 204) {
    return undefined as T;
  }

  const text = await res.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  }

  if (!res.ok) {
    let code: string | undefined;
    let message = `HTTP ${res.status}`;
    if (parsed && typeof parsed === "object") {
      const detail = (parsed as { detail?: unknown }).detail;
      if (detail && typeof detail === "object") {
        const inner = (detail as { error?: { code?: string; message?: string } }).error;
        if (inner) {
          code = inner.code;
          if (inner.message) message = inner.message;
        }
      } else if (typeof detail === "string") {
        message = detail;
      }
    }
    throw new ApiError(res.status, message, code, parsed);
  }

  return parsed as T;
}

export function printApiError(err: unknown): void {
  if (err instanceof ApiError) {
    const label = err.status ? `HTTP ${err.status}` : "network";
    const codeStr = err.code ? ` [${err.code}]` : "";
    process.stderr.write(pc.red(`✗ ${label}${codeStr}: ${err.message}\n`));
    if (err.status === 401) {
      process.stderr.write(
        pc.gray(`  Run \`atelier auth login\` to sign in, or set ATELIER_TOKEN.\n`),
      );
    } else if (err.status === 403 && err.code === "SESSION_REQUIRED") {
      process.stderr.write(
        pc.gray(
          `  This endpoint can't be called with a PAT — sign in via the app\n` +
            `  or use email+password (\`atelier auth login --email …\`) to get a JWT session.\n`,
        ),
      );
    } else if (err.status === 404 && err.message === "Not Found") {
      process.stderr.write(
        pc.gray(
          `  This endpoint doesn't exist on the backend you're talking to.\n` +
            `  Either the URL is wrong, or the server is older than this CLI.\n` +
            `  Check: \`atelier auth status\`\n`,
        ),
      );
    } else if (err.status === 0) {
      process.stderr.write(
        pc.gray(`  Couldn't reach the API. Check your network and \`atelier auth status\`.\n`),
      );
    }
    return;
  }
  const msg = err instanceof Error ? err.message : String(err);
  process.stderr.write(pc.red(`✗ ${msg}\n`));
}
