import { Command } from "commander";
import pc from "picocolors";
import prompts from "prompts";
import { apiRequest, printApiError } from "../lib/client.js";
import { clearToken, configPath, loadConfig, saveConfig } from "../lib/config.js";

interface MeResponse {
  id: string;
  username: string;
  email: string;
  display_name?: string | null;
}

interface JwtTokenResponse {
  access_token: string;
  refresh_token?: string;
  token_type?: string;
}

interface PatCreateResponse {
  id: string;
  name: string;
  token: string;
  scopes: string;
}

export const authCmd = new Command("auth").description("Manage CLI authentication");

authCmd
  .command("login")
  .description(
    "Sign in. Either paste an existing PAT, or log in with email/password to mint one.",
  )
  .option("--token <token>", "Paste a PAT directly (starts with atl_)")
  .option("--email <email>", "Email for password-based bootstrap")
  .option("--password <password>", "Password (omit to prompt)")
  .option("--token-name <name>", "Name for the PAT minted via password flow", "atelier-cli")
  .option("--scopes <scopes>", "Scopes for the new PAT", "write")
  .option("--expires-days <n>", "PAT lifetime in days (0/never = no expiry)", "90")
  .option("--api-url <url>", "Override the API base URL")
  .action(
    async (opts: {
      token?: string;
      email?: string;
      password?: string;
      tokenName: string;
      scopes: string;
      expiresDays: string;
      apiUrl?: string;
    }) => {
      try {
        const cfg = loadConfig();
        const apiUrl = opts.apiUrl ?? cfg.apiUrl;

        let token = opts.token;

        // Password-based bootstrap: exchange email+password for JWT, then mint a PAT.
        if (!token && opts.email) {
          let password = opts.password;
          if (!password) {
            const a = await prompts({
              type: "password",
              name: "v",
              message: `Password for ${opts.email}`,
            });
            password = a.v as string | undefined;
          }
          if (!password) {
            process.stderr.write(pc.red("✗ Password required.\n"));
            process.exit(1);
          }

          // Point the client at the chosen API URL for this bootstrap call.
          saveConfig({ apiUrl });

          const jwt = await apiRequest<JwtTokenResponse>("/api/v1/auth/login", {
            method: "POST",
            body: { email: opts.email, password },
            anonymous: true,
          });

          const expires = opts.expiresDays === "0" || opts.expiresDays.toLowerCase() === "never"
            ? null
            : Number(opts.expiresDays);
          const pat = await apiRequest<PatCreateResponse>("/api/v1/tokens", {
            method: "POST",
            body: {
              name: opts.tokenName,
              scopes: opts.scopes,
              expires_in_days: expires,
            },
            headers: { Authorization: `Bearer ${jwt.access_token}` },
            anonymous: true,
          });
          token = pat.token;
          process.stdout.write(
            pc.gray(`  Minted PAT '${pat.name}' (scopes=${pat.scopes}).\n`),
          );
        }

        // Interactive prompt if still no token.
        if (!token) {
          const answer = await prompts({
            type: "password",
            name: "token",
            message: "Paste your Truth Truth PAT (starts with atl_)",
          });
          token = (answer.token as string | undefined)?.trim();
        }
        if (!token) {
          process.stderr.write(pc.red("✗ No token provided.\n"));
          process.exit(1);
        }
        if (!token.startsWith("atl_")) {
          process.stderr.write(
            pc.yellow("⚠ Token doesn't look like a PAT (expected `atl_` prefix).\n"),
          );
        }

        saveConfig({ apiUrl, token, tokenPrefix: token.slice(0, 12) });

        // Verify the token works before declaring victory.
        const me = await apiRequest<MeResponse>("/api/v1/auth/me");
        saveConfig({
          apiUrl,
          token,
          tokenPrefix: token.slice(0, 12),
          email: me.email,
        });

        process.stdout.write(
          pc.green(`✓ Signed in as ${pc.bold(me.email)} (${apiUrl}).\n`),
        );
        process.stdout.write(pc.gray(`  Config: ${configPath()}\n`));
      } catch (err) {
        printApiError(err);
        clearToken();
        process.exit(1);
      }
    },
  );

authCmd
  .command("logout")
  .description("Delete the locally stored token.")
  .action(() => {
    const path = clearToken();
    process.stdout.write(pc.green(`✓ Logged out. Config cleared: ${path}\n`));
  });

authCmd
  .command("whoami")
  .description("Show the currently signed-in user.")
  .action(async () => {
    try {
      const cfg = loadConfig();
      if (!cfg.token) {
        process.stdout.write(pc.yellow("Not signed in.\n"));
        process.exit(1);
      }
      const me = await apiRequest<MeResponse>("/api/v1/auth/me");
      process.stdout.write(
        `${pc.bold(me.email)}  ${pc.gray(`(${me.username}, ${cfg.apiUrl})`)}\n`,
      );
    } catch (err) {
      printApiError(err);
      process.exit(1);
    }
  });

authCmd
  .command("status")
  .description("Print current API URL and token prefix (safe to share).")
  .action(() => {
    const cfg = loadConfig();
    process.stdout.write(`API URL: ${cfg.apiUrl}\n`);
    process.stdout.write(`Token:   ${cfg.tokenPrefix ?? (cfg.token ? "set (env)" : "—")}\n`);
    process.stdout.write(`Email:   ${cfg.email ?? "—"}\n`);
    process.stdout.write(`Config:  ${configPath()}\n`);
  });

