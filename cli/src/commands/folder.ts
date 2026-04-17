import { Command } from "commander";
import pc from "picocolors";
import { apiRequest, printApiError } from "../lib/client.js";
import { asJson } from "../lib/io.js";

interface FolderOut {
  id: string;
  name: string;
  parent_id: string | null;
  children?: FolderOut[];
}

export const folderCmd = new Command("folder").description("List and inspect folders");

folderCmd
  .command("ls")
  .description("List all folders (tree view).")
  .option("--json", "Output as JSON")
  .action(async (opts: { json?: boolean }) => {
    try {
      const folders = await apiRequest<FolderOut[]>("/api/v1/folders");
      if (opts.json) {
        process.stdout.write(asJson(folders) + "\n");
        return;
      }
      const printNode = (f: FolderOut, indent: number) => {
        process.stdout.write(
          `${" ".repeat(indent * 2)}${pc.dim(f.id.slice(0, 8))}  ${pc.bold(f.name)}\n`,
        );
        (f.children ?? []).forEach((c) => printNode(c, indent + 1));
      };
      if (!folders.length) {
        process.stdout.write(pc.gray("(no folders)\n"));
        return;
      }
      folders.forEach((f) => printNode(f, 0));
    } catch (err) {
      printApiError(err);
      process.exit(1);
    }
  });

// ── Tags (read-only listing) ──────────────────────────────────────────────

interface TagListResponse {
  tags: string[];
}

export const tagCmd = new Command("tag").description("List tags");

tagCmd
  .command("ls")
  .description("List all tags used across your notes.")
  .option("--json", "Output as JSON")
  .action(async (opts: { json?: boolean }) => {
    try {
      const res = await apiRequest<TagListResponse>("/api/v1/tags");
      const tags = res.tags ?? [];
      if (opts.json) {
        process.stdout.write(asJson(tags) + "\n");
        return;
      }
      if (!tags.length) {
        process.stdout.write(pc.gray("(no tags)\n"));
        return;
      }
      for (const t of tags) {
        process.stdout.write(`${pc.cyan(`#${t}`)}\n`);
      }
    } catch (err) {
      printApiError(err);
      process.exit(1);
    }
  });
