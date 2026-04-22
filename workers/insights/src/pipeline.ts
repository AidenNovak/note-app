/**
 * Insight Generation Pipeline — parallel multi-theme report generation.
 *
 * Replaces: backend/app/intelligence/insights/pipeline.py
 *
 * Features:
 *   - Real-time <think> → markdown quote block conversion
 *   - Buffered delta broadcasting for smoother UI updates
 *   - Visual rhythm: all group cards appear upfront, then fill in parallel
 */
import { generateText, streamText } from "ai";
import type { InsightAgent } from "./agent";
import * as db from "./db";
import type {
  InsightGeneration,
  InsightReportOutput,
  Note,
  AngleOutput,
} from "./types";

// ── Constants ──

const MAX_NOTES = 30;
const MAX_CONTENT_CHARS = 18_000;
const DELTA_BUFFER_MS = 80; // Buffer chunks for smoother UI

const FALLBACK_THEMES: Array<{ type_hint: string; angle_name: string; description: string }> = [
  { type_hint: "pattern", angle_name: "模式识别", description: "发现笔记中的重复主题、行为模式和内在结构" },
  { type_hint: "connection", angle_name: "关联分析", description: "发现笔记之间的隐藏联系、跨领域关联和知识网络" },
  { type_hint: "trend", angle_name: "趋势洞察", description: "发现时间维度的变化趋势、发展方向和演进脉络" },
];

// ── Visual theme colors for each group (passed to iOS) ──

const GROUP_COLORS = [
  { accent: "#2d5a3d", bg: "#f0f7f2" },   // Forest
  { accent: "#1a4a8c", bg: "#f0f4fa" },   // Ocean
  { accent: "#8b6914", bg: "#faf6f0" },   // Sand
  { accent: "#5b2d8e", bg: "#f6f0fa" },   // Plum
];

// ── Helpers ──

function sampleNotes(notes: Note[], maxNotes = MAX_NOTES): Note[] {
  if (notes.length <= maxNotes) return notes;
  const scored = [...notes].sort(
    (a, b) =>
      b.tags.length + (b.markdown_content?.length || 0) -
      (a.tags.length + (a.markdown_content?.length || 0))
  );
  const deterministicCount = Math.floor(maxNotes * 0.6);
  const pool = scored.slice(deterministicCount);
  const randomPick = pool.sort(() => Math.random() - 0.5).slice(0, maxNotes - deterministicCount);
  return scored.slice(0, deterministicCount).concat(randomPick);
}

/**
 * Semantic note selection using Vectorize + multi-seed strategy.
 *
 * Strategy:
 *   1. Always keep the FRESHNESS_COUNT most recent notes (new notes not yet indexed)
 *   2. Use the top SEED_COUNT recent notes as query seeds (multi-seed for breadth)
 *   3. Union Vectorize top-K results across all seeds
 *   4. Fill remaining slots by merging with freshness notes, preserving Vectorize ranking
 *   5. Fall back to sampleNotes() if Vectorize has < MIN_SEMANTIC_RESULTS (cold start)
 *
 * namespace = userId ensures strict tenant isolation without metadata index overhead.
 */
async function selectNotesForInsight(
  env: Env,
  userId: string,
  allNotes: Note[],
  maxNotes = MAX_NOTES
): Promise<Note[]> {
  if (allNotes.length <= maxNotes) return allNotes;

  const FRESHNESS_COUNT = 5;   // always included regardless of Vectorize
  const SEED_COUNT = 3;        // number of recent notes used as query seeds
  const TOPK_PER_SEED = 20;    // Vectorize results per seed
  const MIN_SEMANTIC_RESULTS = 5; // fallback threshold

  // Step 1: guaranteed freshness pool (most recent notes by updated_at)
  const recentNotes = allNotes.slice(0, FRESHNESS_COUNT);
  const freshIds = new Set(recentNotes.map((n) => n.id));

  // Step 2: multi-seed Vectorize query
  const semanticIds: string[] = [];
  const seeds = allNotes.slice(0, SEED_COUNT);

  for (const seed of seeds) {
    try {
      const seedText = `${seed.title} ${(seed.markdown_content || "").slice(0, 500)}`.slice(0, 8000);
      const aiResult = await env.AI.run("@cf/baai/bge-m3" as any, { text: seedText } as any) as any;
      const seedVector: number[] = aiResult.data[0];

      const matches = await env.VECTORIZE.query(seedVector, {
        topK: TOPK_PER_SEED,
        namespace: userId,
        returnValues: false,
        returnMetadata: "none",
      });
      for (const m of matches.matches) {
        if (!semanticIds.includes(m.id)) semanticIds.push(m.id);
      }
    } catch {
      // Vectorize unavailable or index empty for this user — skip seed
    }
  }

  // Step 3: fall back to tag/length sampling if Vectorize returned too few results
  if (semanticIds.length < MIN_SEMANTIC_RESULTS) {
    return sampleNotes(allNotes, maxNotes);
  }

  // Step 4: merge freshness + semantic, deduplicated, preserving semantic rank order
  const merged: string[] = [
    ...recentNotes.map((n) => n.id),
    ...semanticIds.filter((id) => !freshIds.has(id)),
  ];

  // Step 5: resolve IDs back to Note objects (use allNotes map to avoid extra DB fetch)
  const allNotesMap = new Map(allNotes.map((n) => [n.id, n]));  const selected = merged
    .map((id) => allNotesMap.get(id))
    .filter((n): n is Note => n !== undefined)
    .slice(0, maxNotes);

  return selected;
}

function buildNotesContent(notes: Note[], maxChars = MAX_CONTENT_CHARS): string {
  const parts: string[] = [];
  let totalChars = 0;
  for (const note of notes) {
    const tags = note.tags.join(", ") || "无标签";
    const block =
      `### ${note.title} (ID: ${note.id})\n` +
      `标签: ${tags} | 更新于: ${note.updated_at || "未知"}\n\n` +
      `${note.markdown_content || ""}\n`;
    if (totalChars + block.length > maxChars) {
      const remaining = maxChars - totalChars;
      if (remaining < 300) break;
      parts.push(block.slice(0, remaining) + "\n...(截断)");
      break;
    }
    parts.push(block);
    totalChars += block.length;
  }
  return parts.join("\n---\n");
}

// ── Streaming <think> → markdown quote transformer ──

class ThinkQuoteTransformer {
  private buffer = "";
  private inThink = false;
  private firstThink = true;

  feed(chunk: string): string {
    this.buffer += chunk;
    let output = "";

    while (this.buffer.length > 0) {
      if (!this.inThink) {
        const idx = this.buffer.indexOf("<think>");
        if (idx === -1) {
          output += this.buffer;
          this.buffer = "";
          break;
        }
        output += this.buffer.slice(0, idx);
        this.buffer = this.buffer.slice(idx + 7);
        this.inThink = true;
        if (this.firstThink) {
          output += "\n\n> **🧠 思考中...**\n> ";
          this.firstThink = false;
        } else {
          output += "\n\n> **💭 继续思考...**\n> ";
        }
      } else {
        const idx = this.buffer.indexOf("</think>");
        if (idx === -1) {
          // Output complete lines as quotes, keep partial line in buffer
          const newlineIdx = this.buffer.lastIndexOf("\n");
          if (newlineIdx >= 0) {
            const lines = this.buffer.slice(0, newlineIdx);
            this.buffer = this.buffer.slice(newlineIdx + 1);
            output += lines.split("\n").map((l) => "> " + l).join("\n") + "\n> ";
          } else {
            break; // Partial line, wait for more
          }
        } else {
          const thinkContent = this.buffer.slice(0, idx);
          this.buffer = this.buffer.slice(idx + 8);
          this.inThink = false;
          output += thinkContent.split("\n").map((l) => "> " + l).join("\n");
          output += "\n\n";
        }
      }
    }

    return output;
  }

  flush(): string {
    if (this.inThink) {
      const output = this.buffer.split("\n").map((l) => "> " + l).join("\n");
      this.buffer = "";
      return output + "\n\n";
    }
    const output = this.buffer;
    this.buffer = "";
    return output;
  }
}

// ── Buffered delta broadcaster ──

async function broadcastDeltas(
  agent: InsightAgent,
  groupIndex: number,
  stream: AsyncIterable<string>
): Promise<string> {
  const transformer = new ThinkQuoteTransformer();
  let fullText = "";
  let buffer = "";
  let lastBroadcast = Date.now();

  for await (const chunk of stream) {
    fullText += chunk;
    const transformed = transformer.feed(chunk);
    buffer += transformed;

    const now = Date.now();
    if (now - lastBroadcast >= DELTA_BUFFER_MS && buffer.length > 0) {
      await agent.broadcastEvent({
        type: "markdown_delta",
        group: groupIndex,
        text: buffer,
      });
      buffer = "";
      lastBroadcast = now;
    }
  }

  // Flush remaining buffer + transformer
  const remaining = transformer.flush();
  if (buffer.length > 0 || remaining.length > 0) {
    await agent.broadcastEvent({
      type: "markdown_delta",
      group: groupIndex,
      text: buffer + remaining,
    });
  }

  return fullText;
}

// ── Phase 0: Angle Discovery ──

async function discoverAngles(
  env: Env,
  notesContent: string,
  noteCount: number,
  agent: InsightAgent
): Promise<AngleOutput[]> {
  const model = agent.getModel();

  const system = `You are an insight angle discovery engine. Analyze the user's notes and discover 1-4 distinct thematic angles for insight reports.

Return JSON with this exact shape:
{
  "angles": [
    { "angle_name": "...", "description": "...", "type_hint": "pattern|connection|trend|gap|opportunity", "note_ids": ["..."] }
  ]
}`;

  try {
    const { text } = await generateText({
      model,
      system,
      prompt: `Analyze these ${noteCount} notes and discover insight angles:\n\n${notesContent.slice(0, 20000)}`,
    });

    const parsed = JSON.parse(text);
    return (parsed.angles || []).slice(0, 4).map((a: any, i: number) => ({
      ...a,
      _color: GROUP_COLORS[i % GROUP_COLORS.length],
    }));
  } catch (err) {
    console.error("Angle discovery failed:", err);
    return FALLBACK_THEMES.map((t, i) => ({
      angle_name: t.angle_name,
      description: t.description,
      type_hint: t.type_hint,
      note_ids: [],
      _color: GROUP_COLORS[i % GROUP_COLORS.length],
    }));
  }
}

// ── Phase 1: Single Report Generation ──

async function generateSingleReport(
  env: Env,
  agent: InsightAgent,
  generationId: string,
  theme: AngleOutput,
  groupIndex: number,
  totalGroups: number,
  notesContent: string,
  noteCount: number,
  date: string
): Promise<InsightReportOutput | null> {
  // Visual color for this group
  const color = (theme as any)._color || GROUP_COLORS[0];

  // Broadcast start with visual theme
  await agent.broadcastEvent({
    type: "group_started",
    group: groupIndex,
    total_groups: totalGroups,
    theme: theme.angle_name,
    angle: theme.description,
    note_count: noteCount,
    accent_color: color.accent,
    bg_color: color.bg,
  });

  try {
    // Use capable (reasoning) model for report streaming so <think> blocks appear
    const reportModel = agent.getCapableModel();
    const fastModel = agent.getModel();

    const systemPrompt = `You are an insight analyst. Write a deep, evidence-based insight report in markdown.

Angle: ${theme.angle_name}
Description: ${theme.description}
Type: ${theme.type_hint}
Date: ${date}

Requirements:
- Ground every claim in the user's notes (use specific quotes)
- Structure: Overview → Evidence → Analysis → Conclusion
- Be concise but substantive (800-1500 words)
- Include a thinking trace wrapped in <think>...</think> tags at the top
- Tone: thoughtful, reflective, slightly challenging`;

    const streamResult = await streamText({
      model: reportModel,
      system: systemPrompt,
      prompt: `Analyze these notes and write the report:\n\n${notesContent}`,
      // maxSteps removed: not supported in ai v6
    });

    // Stream with <think> → quote conversion and buffering
    const fullText = await broadcastDeltas(agent, groupIndex, streamResult.textStream);

    // Extract thinking trace for metadata
    const thinkMatch = fullText.match(/<think>([\s\S]*?)<\/think>/);
    const reasoning = thinkMatch ? thinkMatch[1].trim() : null;

    // Clean markdown for DB persistence
    const markdown = fullText.replace(/<think>[\s\S]*?<\/think>\n?/g, "").trim();

    // Step 2: Extract structured metadata
    const extractionSystem = `Extract structured metadata from the following insight report markdown.

Return JSON with this exact shape:
{
  "title": "...",
  "description": "1-2 sentence summary",
  "type": "pattern|connection|trend|gap|opportunity|synthesis",
  "confidence": 0.0-1.0,
  "importance_score": 0.0-1.0,
  "novelty_score": 0.0-1.0,
  "evidence_items": [{ "note_id": "...", "quote": "...", "rationale": "..." }],
  "action_items": [{ "title": "...", "detail": "...", "priority": "high|medium|low" }],
  "share_card": {
    "theme": "...", "eyebrow": "...", "headline": "...", "summary": "...",
    "highlight": "...", "evidence_quote": "...", "evidence_source": "...",
    "action_title": "...", "action_detail": "...", "metrics": [{"label":"...","value":"..."}],
    "footer": "..."
  }
}`;

    let extraction;
    try {
      const { text: extractionText } = await generateText({
        model: fastModel,
        system: extractionSystem,
        prompt: `Report markdown:\n\n${markdown.slice(0, 8000)}`,
      });
      extraction = JSON.parse(extractionText);
    } catch (err) {
      console.error("Metadata extraction failed:", err);
      extraction = {
        title: theme.angle_name,
        description: theme.description,
        type: theme.type_hint,
        confidence: 0.7,
        importance_score: 0.7,
        novelty_score: 0.5,
        evidence_items: [],
        action_items: [],
        share_card: null,
      };
    }

    const report: InsightReportOutput = {
      title: extraction.title || theme.angle_name,
      description: extraction.description || theme.description,
      type: extraction.type || theme.type_hint,
      report_markdown: markdown,
      thinking_trace: reasoning || null,
      confidence: Math.max(0, Math.min(1, extraction.confidence ?? 0.7)),
      importance_score: Math.max(0, Math.min(1, extraction.importance_score ?? 0.7)),
      novelty_score: Math.max(0, Math.min(1, extraction.novelty_score ?? 0.5)),
      evidence_items: (extraction.evidence_items || []).map((ev: any) => ({
        note_id: ev.note_id || "",
        quote: String(ev.quote || "").slice(0, 500),
        rationale: String(ev.rationale || "").slice(0, 500),
      })),
      action_items: (extraction.action_items || []).map((act: any) => ({
        title: String(act.title || "").slice(0, 255),
        detail: String(act.detail || "").slice(0, 500),
        priority: ["high", "medium", "low"].includes(act.priority) ? act.priority : "medium",
      })),
      share_card: extraction.share_card || null,
    };

    // Broadcast completion with raw text (includes transformed <think> quotes)
    const displayText = fullText.replace(/<think>[\s\S]*?<\/think>/g, (match) => {
      const inner = match.slice(7, -8).trim();
      return "\n\n> **🧠 思考中...**\n> " + inner.split("\n").map((l: string) => "> " + l).join("\n") + "\n\n";
    });

    await agent.broadcastEvent({
      type: "group_completed",
      group: groupIndex,
      total_groups: totalGroups,
      theme: theme.angle_name,
      title: report.title,
      description: report.description,
      thinking_trace: report.thinking_trace || "",
      report_markdown: displayText,
      accent_color: color.accent,
      bg_color: color.bg,
    });

    return report;
  } catch (err) {
    console.error(`Report generation failed for theme '${theme.angle_name}':`, err);
    await agent.broadcastEvent({
      type: "group_completed",
      group: groupIndex,
      total_groups: totalGroups,
      theme: theme.angle_name,
      title: "",
      description: `生成失败: ${err}`,
    });
    return null;
  }
}

// ── Main Pipeline Entry ──

export async function runPipeline(
  env: Env,
  generation: InsightGeneration,
  agent: InsightAgent
): Promise<void> {
  const generationId = generation.id;
  const userId = generation.user_id;
  const today = new Date().toISOString().split("T")[0];

  console.log(`[pipeline] START generationId=${generationId} userId=${userId}`);
  await db.updateGeneration(env, generationId, { status: "PROCESSING" });
  console.log(`[pipeline] status → PROCESSING`);

  // Fetch notes (always fetch more than MAX_NOTES so semantic selection has a full pool)
  const notes = await db.fetchUserNotes(env, userId, 200);
  console.log(`[pipeline] fetchUserNotes → ${notes.length} notes`);
  if (notes.length === 0) {
    await agent.broadcastEvent({
      type: "error",
      message: "请先添加一些笔记再生成洞察。",
    });
    await db.updateGeneration(env, generationId, {
      status: "FAILED",
      error: "请先添加一些笔记再生成洞察。",
    });
    return;
  }

  const sampled = await selectNotesForInsight(env, userId, notes);
  const noteCount = sampled.length;
  const notesContent = buildNotesContent(sampled);
  console.log(`[pipeline] selectNotes → ${noteCount} notes, content ${notesContent.length} chars`);

  // Phase 0: Discover angles
  await agent.broadcastEvent({
    type: "progress",
    message: `正在分析 ${noteCount} 条笔记，发现洞察角度...`,
  });

  console.log(`[pipeline] discoverAngles START`);
  const angles = await discoverAngles(env, notesContent, noteCount, agent);
  console.log(`[pipeline] discoverAngles → ${angles.length} angles: ${angles.map((a) => a.angle_name).join(", ")}`);
  await agent.broadcastEvent({
    type: "progress",
    message: `发现 ${angles.length} 个分析角度：${angles.map((a) => a.angle_name).join(", ")}`,
  });

  const totalGroups = angles.length;

  // Phase 1: Parallel generation
  console.log(`[pipeline] generating ${totalGroups} reports in parallel`);
  const tasks = angles.map((angle, i) =>
    generateSingleReport(
      env,
      agent,
      generationId,
      angle,
      i + 1,
      totalGroups,
      notesContent,
      noteCount,
      today
    )
  );

  const results = await Promise.all(tasks);
  console.log(`[pipeline] all reports done, ${results.filter(Boolean).length}/${results.length} succeeded`);

  // Filter out failures
  const reports = results.filter((r): r is InsightReportOutput => r !== null);

  if (reports.length === 0) {
    await agent.broadcastEvent({
      type: "error",
      message: "所有报告生成均失败。",
    });
    await db.updateGeneration(env, generationId, {
      status: "FAILED",
      error: "所有报告生成均失败。",
    });
    return;
  }

  // Persist to database
  const noteIds = sampled.map((n) => n.id);
  console.log(`[pipeline] persistReports START`);
  await db.persistReports(env, generationId, userId, reports, noteIds);
  console.log(`[pipeline] persistReports DONE`);

  // Final broadcast
  await agent.broadcastEvent({
    type: "completed",
    summary: `生成了 ${reports.length} 篇洞察报告，分析了 ${noteCount} 条笔记`,
  });

  console.log(`[pipeline] COMPLETED: ${reports.length} reports, generation=${generationId}`);
}
