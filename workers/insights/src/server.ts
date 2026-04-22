/**
 * Worker entry point — routes requests to the InsightAgent Durable Object.
 *
 * Aligned with Cloudflare Agents SDK routing conventions:
 *   /agents/InsightAgent/:generationId  →  agent instance (WebSocket + RPC)
 *
 * Also exposes HTTP REST endpoints for FastAPI bridge compatibility:
 *   POST /api/v1/insights/generate       →  trigger generation
 *   GET  /api/v1/insights/generations/:id/stream  →  SSE event stream
 *
 * Replaces: backend/api/v1/insights.py (generation + streaming endpoints)
 */
import { routeAgentRequest } from "agents";
import { InsightAgent } from "./agent";
import * as db from "./db";

export { InsightAgent };

function corsHeaders(env: Env): Record<string, string> {
  return {
    "Access-Control-Allow-Origin": env.FRONTEND_URL,
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Max-Age": "86400",
  };
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext) {
    const url = new URL(request.url);

    // CORS preflight
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(env) });
    }

    // Health check
    if (url.pathname === "/health") {
      return new Response(JSON.stringify({ status: "ok", service: "insights" }), {
        headers: { "Content-Type": "application/json", ...corsHeaders(env) },
      });
    }

    // POST /embed — upsert note embedding to Vectorize + Supabase
    if (url.pathname === "/embed" && request.method === "POST") {
      return handleEmbed(request, env);
    }

    // DELETE /embed/:noteId — delete note embedding from Vectorize + Supabase
    const embedDeleteMatch = url.pathname.match(/^\/embed\/([^\/]+)$/);
    if (embedDeleteMatch && request.method === "DELETE") {
      return handleEmbedDelete(embedDeleteMatch[1], request, env);
    }

    // ── HTTP REST API (FastAPI bridge) ──

    // POST /api/v1/insights/generate — trigger generation
    if (url.pathname === "/api/v1/insights/generate" && request.method === "POST") {
      return handleGenerate(request, env, ctx);
    }

    // GET /api/v1/insights/generations/:id/stream — SSE event stream
    const streamMatch = url.pathname.match(/^\/api\/v1\/insights\/generations\/([^\/]+)\/stream$/);
    if (streamMatch && request.method === "GET") {
      return handleStream(streamMatch[1], request, env);
    }

    // GET /api/v1/insights/generations/latest
    if (url.pathname === "/api/v1/insights/generations/latest" && request.method === "GET") {
      return handleLatestGeneration(request, env);
    }

    // ── Agent WebSocket / RPC routing ──
    const agentResponse = await routeAgentRequest(request, env);
    if (agentResponse) {
      return agentResponse;
    }

    return new Response("Not found", { status: 404 });
  },
} satisfies ExportedHandler<Env>;

/**
 * POST /api/v1/insights/generate
 * Body: { user_id: string }
 * Returns: { id, status, ... }
 */
async function handleGenerate(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
  let body: { user_id?: string };
  try {
    body = await request.json();
  } catch {
    body = {};
  }

  const userId = body.user_id;
  if (!userId) {
    return jsonResponse({ error: "user_id required" }, 400, env);
  }

  try {
    // Create generation in Supabase
    const generation = await db.createGeneration(env, userId);

    // Get DO stub for this generation (keyed by generation ID)
    const doId = env.InsightAgent.idFromName(generation.id);
    const stub = env.InsightAgent.get(doId);

    // Kick off pipeline via internal DO route /run/:generationId
    // (bypasses Agents SDK routing header validation — see InsightAgent.fetch override)
    ctx.waitUntil(
      stub.fetch(
        new Request(`http://internal/run/${generation.id}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ user_id: userId, generation_id: generation.id }),
        })
      ).catch(async (err: unknown) => {
        const msg = err instanceof Error ? err.message : JSON.stringify(err);
        console.error("DO trigger failed:", msg);
        await db.updateGeneration(env, generation.id, {
          status: "FAILED",
          error: msg.slice(0, 500),
        });
      })
    );

    return jsonResponse(
      {
        id: generation.id,
        status: generation.status,
        workflow_version: generation.workflow_version,
        is_active: generation.is_active,
        total_reports: generation.total_reports,
        created_at: generation.created_at,
      },
      202,
      env
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : JSON.stringify(err);
    console.error("Generate handler failed:", msg);
    return jsonResponse({ error: msg }, 500, env);
  }
}

/**
 * GET /api/v1/insights/generations/:id/stream
 * SSE event stream (FastAPI-compatible)
 */
async function handleStream(generationId: string, request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const lastSequence = parseInt(url.searchParams.get("last_sequence") || "0", 10);

  const encoder = new TextEncoder();
  let closed = false;

  const stream = new ReadableStream({
    async start(controller) {
      // Send retry hint
      controller.enqueue(encoder.encode("retry: 2000\n\n"));

      let currentSequence = lastSequence;

      // Poll loop: read new events from Supabase every 500ms
      while (!closed) {
        try {
          const events = await db.getEventsAfter(env, generationId, currentSequence);

          for (const ev of events) {
            const payload = typeof ev.payload_json === "string"
              ? JSON.parse(ev.payload_json)
              : ev.payload_json;
            const data = JSON.stringify(payload);
            controller.enqueue(encoder.encode(`data: ${data}\n\n`));
            currentSequence = ev.sequence;

            // Stop on terminal events
            if (payload.type === "completed" || payload.type === "error") {
              closed = true;
              controller.close();
              return;
            }
          }
        } catch (err) {
          console.error("Stream poll error:", err);
        }

        if (!closed) {
          await sleep(500);
        }
      }
    },
    cancel() {
      closed = true;
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      ...corsHeaders(env),
    },
  });
}

/**
 * GET /api/v1/insights/generations/latest
 */
async function handleLatestGeneration(request: Request, env: Env): Promise<Response> {
  // Extract user_id from JWT (simplified — should verify token)
  const authHeader = request.headers.get("Authorization");
  const token = authHeader?.replace("Bearer ", "");
  if (!token) {
    return jsonResponse({ error: "Unauthorized" }, 401, env);
  }

  // TODO: verify JWT and extract user_id
  // For now, this endpoint requires the caller to pass user_id via query
  const url = new URL(request.url);
  const userId = url.searchParams.get("user_id");
  if (!userId) {
    return jsonResponse({ error: "user_id required" }, 400, env);
  }

  const gen = await db.getLatestGeneration(env, userId);
  if (!gen) {
    return jsonResponse(null, 200, env);
  }

  return jsonResponse(
    {
      id: gen.id,
      status: gen.status,
      workflow_version: gen.workflow_version,
      is_active: gen.is_active,
      total_reports: gen.total_reports,
      summary: gen.summary,
      error: gen.error,
      created_at: gen.created_at,
    },
    200,
    env
  );
}

// ── Helpers ──

/**
 * POST /embed
 * Body: { note_id, content, user_id, updated_at? }
 * Auth: X-Worker-Api-Key header
 *
 * Generates embedding via Workers AI binding, then upserts to:
 *   1. Vectorize (namespace = user_id, for pipeline semantic search)
 *   2. Supabase note_embeddings (for note_collaboration.py backward compat)
 * Both writes must succeed; returns 500 if either fails.
 */
async function handleEmbed(request: Request, env: Env): Promise<Response> {
  const apiKey = request.headers.get("X-Worker-Api-Key");
  if (!apiKey || apiKey !== env.BACKEND_API_KEY) {
    return jsonResponse({ error: "Unauthorized" }, 401, env);
  }

  let body: { note_id?: string; content?: string; user_id?: string; updated_at?: string };
  try {
    body = await request.json();
  } catch {
    return jsonResponse({ error: "Invalid JSON" }, 400, env);
  }

  const { note_id, content, user_id, updated_at } = body;
  if (!note_id || !content || !user_id) {
    return jsonResponse({ error: "note_id, content, user_id required" }, 400, env);
  }

  const trimmed = content.trim().slice(0, 8000);
  if (!trimmed) {
    return jsonResponse({ error: "content is empty" }, 400, env);
  }

  try {
    // Generate embedding via native Workers AI binding
    const aiResult = await env.AI.run("@cf/baai/bge-m3" as any, { text: trimmed } as any) as any;
    if (!aiResult?.data?.[0]) {
      throw new Error(`Unexpected AI response shape: ${JSON.stringify(aiResult)}`);
    }
    const vector: number[] = aiResult.data[0];

    // Write 1: Vectorize (namespace = user_id for tenant isolation)
    await env.VECTORIZE.upsert([{
      id: note_id,
      values: vector,
      namespace: user_id,
      metadata: { updated_at: updated_at || new Date().toISOString() },
    }]);

    // Write 2: Supabase note_embeddings (backward compat for note_collaboration.py)
    // id has no server-side default; generate one, but use onConflict to UPDATE on note_id match.
    const sb = db.getSupabase(env);
    const now = new Date().toISOString();
    const { error: sbError } = await sb.from("note_embeddings").upsert({
      id: crypto.randomUUID(),
      note_id,
      embedding_json: JSON.stringify(vector),
      model: "@cf/baai/bge-m3",
      dimension: vector.length,
      created_at: now,
      updated_at: now,
    }, { onConflict: "note_id" });
    if (sbError) throw new Error(`Supabase upsert failed: ${sbError.message}`);

    return jsonResponse({ ok: true, dimension: vector.length }, 200, env);
  } catch (err) {
    const msg = err instanceof Error ? err.message : JSON.stringify(err);
    console.error("Embed failed:", msg);
    return jsonResponse({ error: msg }, 500, env);
  }
}

/**
 * DELETE /embed/:noteId?user_id=...
 * Auth: X-Worker-Api-Key header
 *
 * Removes note embedding from Vectorize (by namespace + id) and Supabase.
 */
async function handleEmbedDelete(noteId: string, request: Request, env: Env): Promise<Response> {
  const apiKey = request.headers.get("X-Worker-Api-Key");
  if (!apiKey || apiKey !== env.BACKEND_API_KEY) {
    return jsonResponse({ error: "Unauthorized" }, 401, env);
  }

  const url = new URL(request.url);
  const userId = url.searchParams.get("user_id");
  if (!userId) {
    return jsonResponse({ error: "user_id query param required" }, 400, env);
  }

  try {
    // Delete from Vectorize (namespace-scoped vectors are identified by id alone)
    await env.VECTORIZE.deleteByIds([noteId]);

    // Delete from Supabase note_embeddings
    const sb = db.getSupabase(env);
    const { error: sbError } = await sb.from("note_embeddings").delete().eq("note_id", noteId);
    if (sbError) throw sbError;

    return jsonResponse({ ok: true }, 200, env);
  } catch (err) {
    const msg = err instanceof Error ? err.message : JSON.stringify(err);
    console.error("Embed delete failed:", msg);
    return jsonResponse({ error: msg }, 500, env);
  }
}

function jsonResponse(data: unknown, status: number, env: Env): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json",
      ...corsHeaders(env),
    },
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
