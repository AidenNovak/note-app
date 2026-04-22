/**
 * InsightAgent — Cloudflare Think-based agent for insight generation.
 *
 * Built on Cloudflare's Think base class with:
 *   - getSystemPrompt()    →  personality / system prompt
 *   - getModel()           →  Cloudflare Workers AI (configurable)
 *   - getTools()           →  fetch_notes, discover_angles, write_report
 *   - getMaxSteps()        →  max tool-call steps per turn
 *   - onStart()            →  initialize generation tracking
 *
 * Replaces: backend/app/intelligence/insights/agent.py
 */
import { Think } from "@cloudflare/think";
import { routeAgentRequest, callable } from "agents";
import { createWorkersAI } from "workers-ai-provider";
import { tool, generateText } from "ai";
import { z } from "zod";
import type { LanguageModel, ToolSet, UIMessage } from "ai";

import * as db from "./db";
import { runPipeline } from "./pipeline";
import type { AgentConfig } from "./types";

// ── InsightAgent ──

export class InsightAgent extends Think<Env, AgentConfig> {
  declare env: Env;
  declare ctx: DurableObjectState<{}>;
  chatRecovery = true;
  private _sequence = 0;
  private _generationId = "";

  onStart(): void {
    // Store generation ID from agent name (used in routing)
    this._generationId = (this as any).name || "";
  }

  /**
   * Broadcast an event to both WebSocket clients and Supabase.
   *
   * Renamed from `broadcast` to avoid signature collision with Think.broadcast
   * (which sends raw strings/binary to WebSocket connections).
   *
   * Calls Think.broadcast(JSON.stringify(event)) for live WebSocket delivery,
   * then persists the event to Supabase so the FastAPI SSE bridge can replay it.
   */
  async broadcastEvent(event: Record<string, unknown>): Promise<void> {
    // WebSocket delivery to all connected clients
    try {
      this.broadcast(JSON.stringify({ ...event, sequence: this._sequence + 1 }));
    } catch {
      // Ignore if no clients are connected
    }

    // Supabase persistence for SSE replay
    if (this._generationId && this.env.SUPABASE_URL) {
      try {
        this._sequence++;
        await db.appendEvent(this.env, this._generationId, {
          ...event,
          sequence: this._sequence,
        });
      } catch (err) {
        console.error("Failed to persist event to Supabase:", err);
      }
    }
  }

  getModel(): LanguageModel {
    const config = this.getConfig();
    const tier = config?.modelTier ?? "fast";
    const models: Record<string, string> = {
      fast: this.env.AI_MODEL,
      capable: this.env.INSIGHTS_AI_MODEL,
    };
    const modelId = models[tier] ?? this.env.AI_MODEL;
    return createWorkersAI({ binding: this.env.AI })(modelId);
  }

  /** Returns the capable (reasoning) model — used only for report generation. */
  getCapableModel(): LanguageModel {
    return createWorkersAI({ binding: this.env.AI })(this.env.INSIGHTS_AI_MODEL);
  }

  getSystemPrompt(): string {
    const config = this.getConfig();
    const persona =
      config?.persona ||
      "You are a capable insight analyst. You synthesise personal notes into meaningful reports, spot patterns, connections, and trends. You are concise, evidence-based, and always ground claims in the user's own writing.";

    return `${persona}\n\nBe concise. Prefer short, direct answers over lengthy explanations. When you learn something about the user or their project, save it to memory.`;
  }

  getMaxSteps(): number {
    return 10;
  }

  getTools(): ToolSet {
    return {
      // Data access tools
      fetch_notes: tool({
        description: "Fetch the user's notes from the database",
        inputSchema: z.object({
          user_id: z.string().describe("User ID"),
          limit: z.number().optional().describe("Max notes to fetch"),
        }),
        execute: async ({ user_id, limit }) => {
          const notes = await db.fetchUserNotes(this.env, user_id, limit);
          return { count: notes.length, notes };
        },
      }),

      fetch_connections: tool({
        description: "Fetch mind graph connections for the user",
        inputSchema: z.object({
          user_id: z.string().describe("User ID"),
        }),
        execute: async ({ user_id }) => {
          const connections = await db.fetchMindConnections(this.env, user_id);
          return { count: connections.length, connections };
        },
      }),

      // Generation tools
      discover_angles: tool({
        description:
          "Discover insight angles from note content. Returns 1-4 thematic angles for parallel report generation.",
        inputSchema: z.object({
          notes_content: z.string().describe("Concatenated note markdown"),
          note_count: z.number().describe("Number of notes"),
        }),
        execute: async ({ notes_content, note_count }) => {
          const result = await this._discoverAngles(notes_content, note_count);
          return result;
        },
      }),

      write_report: tool({
        description:
          "Write a single insight report for a given angle. Streams markdown and returns structured metadata.",
        inputSchema: z.object({
          angle_name: z.string(),
          angle_description: z.string(),
          type_hint: z.string(),
          notes_content: z.string(),
          note_count: z.number(),
          date: z.string(),
        }),
        execute: async () => {
          // Handled by pipeline directly; this tool is for chat-mode
          return { status: "ok", message: "Use run_pipeline for batch generation" };
        },
      }),

      // Utility
      calculate: tool({
        description: "Perform a math calculation",
        inputSchema: z.object({
          a: z.number(),
          b: z.number(),
          operator: z.enum(["+", "-", "*", "/"]),
        }),
        execute: async ({ a, b, operator }) => {
          const ops: Record<string, (x: number, y: number) => number> = {
            "+": (x, y) => x + y,
            "-": (x, y) => x - y,
            "*": (x, y) => x * y,
            "/": (x, y) => (y === 0 ? NaN : x / y),
          };
          return {
            expression: `${a} ${operator} ${b}`,
            result: ops[operator](a, b),
          };
        },
      }),
    };
  }

  // ── Internal HTTP trigger (bypasses Agents SDK routing header validation) ──

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);

    // Internal trigger from the Worker's handleGenerate:
    //   POST /run/:generationId  { user_id, generation_id }
    // This path bypasses the Agents SDK header check so ctx.waitUntil works
    // without needing proper WebSocket routing headers.
    const runMatch = url.pathname.match(/^\/run\/([^/]+)$/);
    console.log("[InsightAgent.fetch] path:", url.pathname, "method:", request.method, "runMatch:", !!runMatch);
    if (runMatch && request.method === "POST") {
      let body: { user_id?: string; generation_id?: string } = {};
      try { body = await request.json(); } catch (parseErr) {
        console.error("[InsightAgent.fetch] JSON parse error:", parseErr);
      }
      const genId = body.generation_id || runMatch[1];
      const userId = body.user_id;
      console.log("[InsightAgent.fetch] genId:", genId, "userId:", userId);
      if (genId && userId) {
        this._generationId = genId;
        console.log("[InsightAgent.fetch] fetching generation from Supabase...");
        const gen = await db.getGeneration(this.env, genId);
        console.log("[InsightAgent.fetch] generation found:", gen ? "yes" : "no");
        if (gen) {
          this.ctx.waitUntil(
            runPipeline(this.env, gen, this).catch(async (err: unknown) => {
              const msg = db.toErrorMessage(err);
              console.error("Pipeline failed:", msg);
              await db.updateGeneration(this.env, genId, {
                status: "FAILED",
                error: msg.slice(0, 500),
              });
            })
          );
          console.log("[InsightAgent.fetch] pipeline queued via ctx.waitUntil");
        }
      }
      return new Response(JSON.stringify({ ok: true }), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      });
    }

    return super.fetch(request);
  }

  // ── HTTP request handler (called by Think/Agent base for routed requests) ──

  async onRequest(request: Request): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/generate" && request.method === "POST") {
      const body = await request.json<{ user_id?: string }>();
      if (!body.user_id) {
        return new Response(JSON.stringify({ error: "user_id required" }), { status: 400 });
      }
      this.ctx.waitUntil(this.generate(body.user_id));
      return new Response(JSON.stringify({ ok: true }), {
        headers: { "Content-Type": "application/json" },
      });
    }

    return new Response("Not found", { status: 404 });
  }

  // ── Callable RPC methods ──

  @callable()
  async generate(userId: string): Promise<{ generationId: string }> {
    const generation = await db.createGeneration(this.env, userId);

    // Kick off pipeline in background (non-blocking)
    this.ctx.waitUntil(
      (async () => {
        try {
          await runPipeline(this.env, generation, this);
        } catch (err) {
          console.error("Pipeline failed:", err);
          await db.updateGeneration(this.env, generation.id, {
            status: "FAILED",
            error: String(err).slice(0, 500),
          });
        }
      })()
    );

    return { generationId: generation.id };
  }

  @callable()
  async getGenerationStatus(generationId: string) {
    const gen = await db.getGeneration(this.env, generationId);
    if (!gen) return { error: "Generation not found" };
    return {
      id: gen.id,
      status: gen.status,
      total_reports: gen.total_reports,
      summary: gen.summary,
      error: gen.error,
    };
  }

  @callable()
  async chat(message: string): Promise<void> {
    // Use Think's built-in chat mechanism
    const userMessage: UIMessage = {
      id: crypto.randomUUID(),
      role: "user",
      parts: [{ type: "text", text: message }],
    };
    // Save message for context assembly
    this.messages.push(userMessage);
  }

  @callable()
  updateConfig(config: AgentConfig) {
    this.configure(config);
  }

  @callable()
  currentConfig(): AgentConfig | null {
    return this.getConfig() as AgentConfig | null;
  }

  // ── Internal: angle discovery (Phase 0) ──

  private async _discoverAngles(
    notesContent: string,
    noteCount: number
  ): Promise<{ angles: Array<{ angle_name: string; description: string; type_hint: string }> }> {
    const model = this.getModel();
    const system = `You are an insight angle discovery engine. Analyze the user's notes and discover 1-4 distinct thematic angles for insight reports.

Return JSON with this shape:
{
  "angles": [
    { "angle_name": "...", "description": "...", "type_hint": "pattern|connection|trend|gap|opportunity" }
  ]
}`;

    try {
      const { text } = await generateText({
        model,
        system,
        prompt: `Notes (${noteCount} notes):\n\n${notesContent.slice(0, 20000)}`,
      });

      const parsed = JSON.parse(text);
      return {
        angles: (parsed.angles || []).slice(0, 4),
      };
    } catch (err) {
      console.error("Angle discovery failed:", err);
      return {
        angles: [
          { angle_name: "模式识别", description: "发现笔记中的重复主题、行为模式和内在结构", type_hint: "pattern" },
          { angle_name: "关联分析", description: "发现笔记之间的隐藏联系、跨领域关联和知识网络", type_hint: "connection" },
          { angle_name: "趋势洞察", description: "发现时间维度的变化趋势、发展方向和演进脉络", type_hint: "trend" },
        ],
      };
    }
  }
}

// ── Worker entry ──

export default {
  async fetch(request: Request, env: Env) {
    return (
      (await routeAgentRequest(request, env as any)) ||
      new Response("Not found", { status: 404 })
    );
  },
} satisfies ExportedHandler<Env>;
