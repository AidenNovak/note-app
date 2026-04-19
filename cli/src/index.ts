#!/usr/bin/env node
import { Command } from "commander";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { authCmd } from "./commands/auth.js";
import { noteCmd } from "./commands/note.js";
import { captureCmd } from "./commands/capture.js";
import { folderCmd, tagCmd } from "./commands/folder.js";
import { tokenCmd } from "./commands/token.js";

function readVersion(): string {
  try {
    const req = createRequire(import.meta.url);
    const pkgPath = req.resolve("../package.json");
    return (JSON.parse(readFileSync(pkgPath, "utf8")).version as string) ?? "dev";
  } catch {
    return "dev";
  }
}

const program = new Command();
program
  .name("atelier")
  .description(
    [
      "Truth Truth (T²) CLI — your second digital mind, on the command line.",
      "",
      "Brand: Truth Truth · Truth, twice. (legacy slug 'atelier' kept in env",
      "var names, the bin name, and the config dir for backward compatibility.)",
      "",
      "Environment variables:",
      "  ATELIER_API_URL    Override the backend URL (default https://backend.jilly.app)",
      "  ATELIER_TOKEN      Use this PAT instead of the one in the config file",
      "",
      "Quickstart:",
      "  atelier auth login --email you@example.com   # mints a 90-day PAT",
      "  atelier capture 'a fleeting thought'",
      "  atelier note ls --json | jq '.items[].title'",
    ].join("\n"),
  )
  .version(readVersion())
  .option(
    "--api-url <url>",
    "Override the backend URL for this invocation (sets ATELIER_API_URL)",
  )
  .hook("preAction", (thisCommand) => {
    const apiUrl = thisCommand.opts().apiUrl as string | undefined;
    if (apiUrl) process.env.ATELIER_API_URL = apiUrl;
  });

program.addCommand(authCmd);
program.addCommand(noteCmd);
program.addCommand(captureCmd);
program.addCommand(folderCmd);
program.addCommand(tagCmd);
program.addCommand(tokenCmd);

program.parseAsync(process.argv).catch((err) => {
  process.stderr.write(`${err instanceof Error ? err.message : String(err)}\n`);
  process.exit(1);
});

