import { Command } from "commander";
import pc from "picocolors";
import { apiRequest, printApiError } from "../lib/client.js";
import { resolveContent } from "../lib/io.js";

interface NoteOut {
  id: string;
  title: string;
  tags: string[];
}

export const captureCmd = new Command("capture")
  .description("Fast-capture a thought into your Inbox. Reads args, --file, or stdin.")
  .argument("[text...]", "Inline text to capture")
  .option("-f, --file <path>", "Read content from a file")
  .option("--folder <folderId>", "Target folder id (defaults to Inbox)")
  .option("--tag <tag...>", "Tag(s) to attach", (val, prev: string[] = []) => [...prev, val])
  .option("--stdin", "Force reading from stdin")
  .option("--json", "Print the created note as JSON (for piping into jq / scripts)")
  .action(
    async (
      text: string[],
      opts: { file?: string; folder?: string; tag?: string[]; stdin?: boolean; json?: boolean },
    ) => {
      try {
        const content = await resolveContent({
          file: opts.file,
          stdin: opts.stdin,
          positional: text?.length ? text.join(" ") : undefined,
        });
        const note = await apiRequest<NoteOut>("/api/v1/notes", {
          method: "POST",
          body: {
            markdown_content: content,
            folder_id: opts.folder,
            tags: opts.tag,
          },
        });
        if (opts.json) {
          process.stdout.write(JSON.stringify(note, null, 2) + "\n");
          return;
        }
        process.stdout.write(
          pc.green(`✓ Captured — ${pc.bold(note.title)}  ${pc.gray(note.id)}\n`),
        );
      } catch (err) {
        printApiError(err);
        process.exit(1);
      }
    },
  );
