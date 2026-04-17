import { readFileSync } from "node:fs";

export async function readStdin(): Promise<string> {
  if (process.stdin.isTTY) return "";
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) {
    chunks.push(typeof chunk === "string" ? Buffer.from(chunk) : chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
}

export async function resolveContent(opts: {
  file?: string;
  stdin?: boolean;
  positional?: string;
}): Promise<string> {
  if (opts.file) {
    return readFileSync(opts.file, "utf8");
  }
  if (opts.stdin || (!process.stdin.isTTY && !opts.positional)) {
    const text = await readStdin();
    if (text.trim()) return text;
  }
  if (opts.positional) return opts.positional;
  throw new Error(
    "No content provided. Pass text as an argument, --file <path>, or pipe via stdin.",
  );
}

export function asJson(obj: unknown): string {
  return JSON.stringify(obj, null, 2);
}

export function truncate(s: string, n = 80): string {
  const flat = s.replace(/\s+/g, " ").trim();
  return flat.length > n ? flat.slice(0, n - 1) + "…" : flat;
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().replace("T", " ").slice(0, 16);
}
