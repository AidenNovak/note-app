import { Command } from "commander";
import pc from "picocolors";
import prompts from "prompts";
import { apiRequest, printApiError } from "../lib/client.js";
import { asJson, fmtDate } from "../lib/io.js";

interface ApiTokenOut {
  id: string;
  name: string;
  token_prefix: string;
  scopes: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

interface ApiTokenCreateResponse extends ApiTokenOut {
  token: string;
}

export const tokenCmd = new Command("token").description(
  "Manage Personal Access Tokens (requires an interactive session, not a PAT).",
);

tokenCmd
  .command("ls")
  .description("List your PATs.")
  .option("--json", "Output as JSON")
  .action(async (opts: { json?: boolean }) => {
    try {
      const tokens = await apiRequest<ApiTokenOut[]>("/api/v1/tokens");
      if (opts.json) {
        process.stdout.write(asJson(tokens) + "\n");
        return;
      }
      if (!tokens.length) {
        process.stdout.write(pc.gray("(no tokens)\n"));
        return;
      }
      for (const t of tokens) {
        const status = t.revoked_at
          ? pc.red("revoked")
          : t.expires_at && new Date(t.expires_at) < new Date()
            ? pc.red("expired")
            : pc.green("active");
        process.stdout.write(
          `${pc.bold(t.name)}  ${pc.dim(t.token_prefix + "…")}  ${status}  ` +
            `${pc.gray(`scopes=${t.scopes}`)}  ${pc.gray(`last_used=${fmtDate(t.last_used_at)}`)}  ` +
            `${pc.gray(`expires=${fmtDate(t.expires_at)}`)}  ${pc.dim(t.id)}\n`,
        );
      }
    } catch (err) {
      printApiError(err);
      process.exit(1);
    }
  });

tokenCmd
  .command("create")
  .description("Create a new PAT.")
  .option("-n, --name <name>", "Friendly name (required)")
  .option("-s, --scopes <scopes>", "Space-separated subset of read/write/admin", "write")
  .option(
    "-e, --expires <days>",
    "Days until expiry (0 or 'never' for no expiry)",
    "90",
  )
  .option("--json", "Output JSON (contains plaintext token — store securely)")
  .action(async (opts: { name?: string; scopes: string; expires: string; json?: boolean }) => {
    try {
      const name = opts.name ?? (await prompts({
        type: "text",
        name: "v",
        message: "Token name (e.g. 'CLI laptop')",
      })).v as string | undefined;
      if (!name) {
        process.stderr.write(pc.red("✗ name is required.\n"));
        process.exit(1);
      }
      const expiresInDays =
        opts.expires === "0" || opts.expires.toLowerCase() === "never"
          ? null
          : Number(opts.expires);
      if (expiresInDays !== null && Number.isNaN(expiresInDays)) {
        process.stderr.write(pc.red("✗ --expires must be a number or 'never'.\n"));
        process.exit(1);
      }
      const res = await apiRequest<ApiTokenCreateResponse>("/api/v1/tokens", {
        method: "POST",
        body: { name, scopes: opts.scopes, expires_in_days: expiresInDays },
      });
      if (opts.json) {
        process.stdout.write(asJson(res) + "\n");
        return;
      }
      process.stdout.write(
        pc.green(`✓ Created ${pc.bold(res.name)}  ${pc.gray(`scopes=${res.scopes}`)}\n`),
      );
      process.stdout.write(
        `\n${pc.bold("Your token (shown only once):")}\n  ${pc.yellow(res.token)}\n\n`,
      );
      process.stdout.write(
        pc.gray(`Store it safely. Use it with: atelier auth login --token ${res.token}\n`),
      );
    } catch (err) {
      printApiError(err);
      process.exit(1);
    }
  });

tokenCmd
  .command("rm <id>")
  .description("Revoke a PAT by id.")
  .option("-y, --yes", "Skip confirmation")
  .action(async (id: string, opts: { yes?: boolean }) => {
    try {
      if (!opts.yes) {
        const a = await prompts({
          type: "confirm",
          name: "ok",
          message: `Revoke token ${id}?`,
          initial: false,
        });
        if (!a.ok) {
          process.stdout.write(pc.gray("Cancelled.\n"));
          return;
        }
      }
      await apiRequest(`/api/v1/tokens/${encodeURIComponent(id)}`, { method: "DELETE" });
      process.stdout.write(pc.green(`✓ Revoked ${id}\n`));
    } catch (err) {
      printApiError(err);
      process.exit(1);
    }
  });
