import { Command } from "commander";
import pc from "picocolors";
import prompts from "prompts";
import { apiRequest, printApiError } from "../lib/client.js";
import { asJson, fmtDate, resolveContent, truncate } from "../lib/io.js";

interface NoteOut {
  id: string;
  title: string;
  status: string;
  folder_id: string | null;
  tags: string[];
  content_preview: string;
  created_at: string;
  updated_at: string;
}

interface NoteListResponse {
  total: number;
  page: number;
  page_size: number;
  items: NoteOut[];
}

interface NoteDetail extends NoteOut {
  markdown_content: string | null;
}

interface SearchItem {
  id: string;
  type: string;
  title: string;
  preview?: string;
  created_at?: string;
}

interface SearchResponse {
  total: number;
  items: SearchItem[];
}

export const noteCmd = new Command("note").description("Create, list, read, update, and delete notes");

noteCmd
  .command("add [content...]")
  .description("Create a new note. Content can be passed as args, --file, or stdin.")
  .option("-f, --file <path>", "Read content from a file")
  .option("-t, --title <title>", "Explicit title (otherwise AI / first line)")
  .option("--tag <tag...>", "Attach one or more tags", (val, prev: string[] = []) => [
    ...prev,
    val,
  ])
  .option("--folder <folderId>", "Attach to a folder by id")
  .option("--stdin", "Force reading from stdin")
  .option("--json", "Print the created note as JSON")
  .action(
    async (
      content: string[],
      opts: {
        file?: string;
        title?: string;
        tag?: string[];
        folder?: string;
        stdin?: boolean;
        json?: boolean;
      },
    ) => {
      try {
        const text = await resolveContent({
          file: opts.file,
          stdin: opts.stdin,
          positional: content?.length ? content.join(" ") : undefined,
        });
        const note = await apiRequest<NoteOut>("/api/v1/notes", {
          method: "POST",
          body: {
            markdown_content: text,
            title: opts.title,
            tags: opts.tag,
            folder_id: opts.folder,
          },
        });
        if (opts.json) {
          process.stdout.write(asJson(note) + "\n");
        } else {
          process.stdout.write(
            pc.green(`✓ Created ${pc.bold(note.id)} — ${pc.bold(note.title)}\n`),
          );
          if (note.tags.length) {
            process.stdout.write(pc.gray(`  tags: ${note.tags.join(", ")}\n`));
          }
        }
      } catch (err) {
        printApiError(err);
        process.exit(1);
      }
    },
  );

noteCmd
  .command("ls")
  .description("List notes (most recent first).")
  .option("-n, --limit <n>", "Max results", "20")
  .option("-p, --page <n>", "Page number", "1")
  .option("--folder <id>", "Filter by folder id")
  .option("--tag <tag>", "Filter by a single tag")
  .option("--status <status>", "Filter by AI-processing status: pending|processing|completed|failed")
  .option("-q, --keyword <q>", "Keyword match on title/content")
  .option("--sort <col>", "Sort column: created_at | updated_at | title | status", "updated_at")
  .option("--order <dir>", "asc | desc", "desc")
  .option("--json", "Output as JSON")
  .action(
    async (opts: {
      limit: string;
      page: string;
      folder?: string;
      tag?: string;
      status?: string;
      keyword?: string;
      sort: string;
      order: string;
      json?: boolean;
    }) => {
      try {
        const res = await apiRequest<NoteListResponse>("/api/v1/notes", {
          query: {
            page_size: opts.limit,
            page: opts.page,
            folder_id: opts.folder,
            tag: opts.tag,
            status: opts.status,
            keyword: opts.keyword,
            sort_by: opts.sort,
            order: opts.order,
          },
        });
        if (opts.json) {
          process.stdout.write(asJson(res) + "\n");
          return;
        }
        if (!res.items.length) {
          process.stdout.write(pc.gray("(no notes)\n"));
          return;
        }
        for (const n of res.items) {
          const tags = n.tags.length ? pc.cyan(` #${n.tags.join(" #")}`) : "";
          process.stdout.write(
            `${pc.gray(fmtDate(n.updated_at))}  ${pc.dim(n.id.slice(0, 8))}  ${pc.bold(truncate(n.title, 60))}${tags}\n`,
          );
          if (n.content_preview) {
            process.stdout.write(`    ${pc.gray(truncate(n.content_preview, 100))}\n`);
          }
        }
        process.stdout.write(
          pc.gray(`\n${res.items.length} of ${res.total} (page ${res.page})\n`),
        );
      } catch (err) {
        printApiError(err);
        process.exit(1);
      }
    },
  );

noteCmd
  .command("get <id>")
  .description("Print a note's full markdown content.")
  .option("--json", "Output full detail as JSON")
  .action(async (id: string, opts: { json?: boolean }) => {
    try {
      const note = await apiRequest<NoteDetail>(`/api/v1/notes/${encodeURIComponent(id)}`);
      if (opts.json) {
        process.stdout.write(asJson(note) + "\n");
        return;
      }
      process.stdout.write(pc.bold(`# ${note.title}\n\n`));
      if (note.tags.length) {
        process.stdout.write(pc.gray(`Tags: ${note.tags.join(", ")}\n`));
      }
      process.stdout.write(
        pc.gray(`Created ${fmtDate(note.created_at)} · Updated ${fmtDate(note.updated_at)}\n\n`),
      );
      process.stdout.write((note.markdown_content ?? "") + "\n");
    } catch (err) {
      printApiError(err);
      process.exit(1);
    }
  });

noteCmd
  .command("rm <id>")
  .description("Delete a note.")
  .option("-y, --yes", "Skip confirmation")
  .action(async (id: string, opts: { yes?: boolean }) => {
    try {
      if (!opts.yes) {
        const a = await prompts({
          type: "confirm",
          name: "ok",
          message: `Delete note ${id}?`,
          initial: false,
        });
        if (!a.ok) {
          process.stdout.write(pc.gray("Cancelled.\n"));
          return;
        }
      }
      await apiRequest(`/api/v1/notes/${encodeURIComponent(id)}`, { method: "DELETE" });
      process.stdout.write(pc.green(`✓ Deleted ${id}\n`));
    } catch (err) {
      printApiError(err);
      process.exit(1);
    }
  });

noteCmd
  .command("edit <id>")
  .description("Update an existing note's content / title / tags / folder.")
  .option("-f, --file <path>", "Replace content from a file")
  .option("-t, --title <title>", "Set the title")
  .option(
    "--tag <tag...>",
    "Replace tags (pass --tag multiple times). Use --tag '' to clear.",
    (val, prev: string[] = []) => [...prev, val],
  )
  .option("--folder <folderId>", "Move to a folder (use 'null' or '' to detach)")
  .option("--stdin", "Replace content from stdin")
  .option("--json", "Print updated note as JSON")
  .action(
    async (
      id: string,
      opts: {
        file?: string;
        title?: string;
        tag?: string[];
        folder?: string;
        stdin?: boolean;
        json?: boolean;
      },
    ) => {
      try {
        const body: Record<string, unknown> = {};
        if (opts.title !== undefined) body.title = opts.title;
        if (opts.tag !== undefined) body.tags = opts.tag.filter((t) => t !== "");
        if (opts.folder !== undefined) {
          body.folder_id = opts.folder === "" || opts.folder === "null" ? null : opts.folder;
        }
        if (opts.file || opts.stdin) {
          body.markdown_content = await resolveContent({
            file: opts.file,
            stdin: opts.stdin,
          });
        }
        if (Object.keys(body).length === 0) {
          process.stderr.write(
            pc.red(
              "✗ Nothing to update. Pass --title / --tag / --folder / --file / --stdin.\n",
            ),
          );
          process.exit(1);
        }
        const note = await apiRequest<NoteOut>(`/api/v1/notes/${encodeURIComponent(id)}`, {
          method: "PATCH",
          body,
        });
        if (opts.json) {
          process.stdout.write(asJson(note) + "\n");
        } else {
          process.stdout.write(
            pc.green(`✓ Updated ${pc.bold(note.id)} — ${pc.bold(note.title)}\n`),
          );
        }
      } catch (err) {
        printApiError(err);
        process.exit(1);
      }
    },
  );

noteCmd
  .command("search <query...>")
  .description("Full-text search across notes.")
  .option("-n, --limit <n>", "Page size", "20")
  .option("--type <t>", "all | note | file", "note")
  .option("--tag <tag>", "Filter by tag")
  .option("--folder <id>", "Filter by folder id")
  .option("--json", "Output as JSON")
  .action(
    async (
      query: string[],
      opts: { limit: string; type: string; tag?: string; folder?: string; json?: boolean },
    ) => {
      try {
        const q = query.join(" ");
        const res = await apiRequest<SearchResponse>("/api/v1/search", {
          query: {
            q,
            type: opts.type,
            tag: opts.tag,
            folder_id: opts.folder,
            page_size: opts.limit,
          },
        });
        if (opts.json) {
          process.stdout.write(asJson(res) + "\n");
          return;
        }
        if (!res.items.length) {
          process.stdout.write(pc.gray("(no matches)\n"));
          return;
        }
        for (const item of res.items) {
          process.stdout.write(
            `${pc.dim(item.type.padEnd(4))}  ${pc.dim(item.id.slice(0, 8))}  ${pc.bold(truncate(item.title, 60))}\n`,
          );
          if (item.preview) {
            process.stdout.write(`    ${pc.gray(truncate(item.preview, 100))}\n`);
          }
        }
        process.stdout.write(pc.gray(`\n${res.items.length} of ${res.total}\n`));
      } catch (err) {
        printApiError(err);
        process.exit(1);
      }
    },
  );
