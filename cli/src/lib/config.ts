import { homedir } from "node:os";
import { join } from "node:path";
import { mkdirSync, readFileSync, writeFileSync, chmodSync, existsSync } from "node:fs";

export interface CliConfig {
  apiUrl: string;
  token?: string;
  tokenPrefix?: string;
  email?: string;
}

const DEFAULT_API_URL = "https://backend.jilly.app";

export function getConfigDir(): string {
  const xdg = process.env.XDG_CONFIG_HOME;
  if (xdg && xdg.trim()) return join(xdg, "atelier");
  return join(homedir(), ".config", "atelier");
}

export function configPath(): string {
  return join(getConfigDir(), "config.json");
}

export function loadConfig(): CliConfig {
  const envApi = process.env.ATELIER_API_URL;
  const envToken = process.env.ATELIER_TOKEN;

  let fileCfg: Partial<CliConfig> = {};
  const p = configPath();
  if (existsSync(p)) {
    try {
      fileCfg = JSON.parse(readFileSync(p, "utf8"));
    } catch {
      // Corrupt file — surface via an explicit error path later; here we
      // simply fall through to defaults so env-only use still works.
    }
  }

  return {
    apiUrl: envApi ?? fileCfg.apiUrl ?? DEFAULT_API_URL,
    token: envToken ?? fileCfg.token,
    tokenPrefix: fileCfg.tokenPrefix,
    email: fileCfg.email,
  };
}

export function saveConfig(cfg: CliConfig): string {
  const dir = getConfigDir();
  mkdirSync(dir, { recursive: true });
  const p = configPath();
  writeFileSync(p, JSON.stringify(cfg, null, 2) + "\n", { encoding: "utf8", mode: 0o600 });
  try {
    chmodSync(p, 0o600);
  } catch {
    // Windows: best-effort.
  }
  return p;
}

export function clearToken(): string {
  const cfg = loadConfig();
  return saveConfig({ apiUrl: cfg.apiUrl });
}
