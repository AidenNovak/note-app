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
  .description("atélier CLI — your second digital mind, on the command line.")
  .version(readVersion());

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
