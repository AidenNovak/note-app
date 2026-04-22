/**
 * Data layer — Supabase client for PostgreSQL access.
 *
 * Replaces: backend SQLAlchemy ORM + asyncpg
 * Why Supabase JS client: HTTP-based, no connection pool limits,
 * works natively in Cloudflare Workers.
 *
 * All queries are aligned with backend/app/models.py schema.
 */
import { createClient, SupabaseClient } from "@supabase/supabase-js";
import type {
  InsightGeneration,
  InsightReport,
  InsightReportOutput,
  InsightEvidenceItem,
  InsightActionItem,
  Note,
  MindConnection,
  TaskStatus,
} from "./types";

let _supabase: SupabaseClient | null = null;

export function getSupabase(env: Env): SupabaseClient {
  if (_supabase) return _supabase;
  _supabase = createClient(env.SUPABASE_URL, env.SUPABASE_SERVICE_KEY, {
    auth: { autoRefreshToken: false, persistSession: false },
  });
  return _supabase;
}

/** Convert any thrown value (Supabase error object, Error, string) to a message string. */
export function toErrorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (err && typeof err === "object") {
    const e = err as Record<string, unknown>;
    if (typeof e.message === "string") return e.message;
    if (typeof e.details === "string") return e.details;
    if (typeof e.hint === "string") return e.hint;
    return JSON.stringify(err);
  }
  return String(err);
}

/** Throw a proper Error from a Supabase PostgREST error object. */
function assertNoError(error: unknown, context?: string): void {
  if (!error) return;
  const msg = toErrorMessage(error);
  throw new Error(context ? `${context}: ${msg}` : msg);
}

// ── Notes ──

export async function fetchUserNotes(
  env: Env,
  userId: string,
  limit = 30
): Promise<Note[]> {
  const sb = getSupabase(env);
  const { data, error } = await sb
    .from("notes")
    .select("id, title, markdown_content, created_at, updated_at, user_id, note_tags(tag)")
    .eq("user_id", userId)
    .order("updated_at", { ascending: false })
    .limit(limit);

  assertNoError(error);

  return (data || []).map((row: any) => ({
    id: row.id,
    title: row.title || "Untitled",
    markdown_content: row.markdown_content || "",
    tags: (row.note_tags || []).map((t: any) => t.tag),
    created_at: row.created_at,
    updated_at: row.updated_at,
    user_id: row.user_id,
  }));
}

export async function fetchNotesByIds(
  env: Env,
  userId: string,
  noteIds: string[]
): Promise<Note[]> {
  const sb = getSupabase(env);
  const { data, error } = await sb
    .from("notes")
    .select("id, title, markdown_content, created_at, updated_at, user_id, note_tags(tag)")
    .in("id", noteIds)
    .eq("user_id", userId); // tenant isolation

  assertNoError(error);

  // Preserve Vectorize ranking order
  const noteMap = new Map((data || []).map((row: any) => [row.id, row]));
  return noteIds
    .map((id) => noteMap.get(id))
    .filter(Boolean)
    .map((row: any) => ({
      id: row.id,
      title: row.title || "Untitled",
      markdown_content: row.markdown_content || "",
      tags: (row.note_tags || []).map((t: any) => t.tag),
      created_at: row.created_at,
      updated_at: row.updated_at,
      user_id: row.user_id,
    }));
}

// ── Mind Connections ──

export async function fetchMindConnections(
  env: Env,
  userId: string
): Promise<MindConnection[]> {
  const sb = getSupabase(env);
  const { data, error } = await sb
    .from("mind_connections")
    .select("*")
    .eq("user_id", userId);

  assertNoError(error);
  return (data || []).map((row: any) => ({
    id: row.id,
    user_id: row.user_id,
    note_a_id: row.note_a_id,
    note_b_id: row.note_b_id,
    shared_tags: JSON.parse(row.shared_tags || "[]"),
    similarity_score: row.similarity_score,
    connection_type: row.connection_type,
  }));
}

// ── Insight Generation ──

export async function createGeneration(
  env: Env,
  userId: string
): Promise<InsightGeneration> {
  const sb = getSupabase(env);

  // Check for active non-stale generation (same logic as backend)
  const { data: existing } = await sb
    .from("insight_generations")
    .select("*")
    .eq("user_id", userId)
    .in("status", ["PENDING", "PROCESSING"])
    .order("created_at", { ascending: false })
    .limit(1)
    .single();

  if (existing) {
    const updatedAt = new Date(existing.updated_at || existing.created_at);
    const ageMs = Date.now() - updatedAt.getTime();
    const staleMs =
      existing.status === "PENDING" ? 45_000 : 20 * 60_000;
    if (ageMs < staleMs) {
      return existing as InsightGeneration;
    }
    // Mark stale as failed
    await sb
      .from("insight_generations")
      .update({ status: "FAILED", error: "Previous generation was interrupted" })
      .eq("id", existing.id);
  }

  const now = new Date().toISOString();
  const { data, error } = await sb
    .from("insight_generations")
    .insert({
      id: crypto.randomUUID(),
      user_id: userId,
      status: "PENDING",
      workflow_version: "think-v1",
      is_active: false,
      total_reports: 0,
      created_at: now,
      updated_at: now,
    })
    .select()
    .single();

  assertNoError(error);
  return data as InsightGeneration;
}

export async function getGeneration(
  env: Env,
  generationId: string
): Promise<InsightGeneration | null> {
  const sb = getSupabase(env);
  const { data, error } = await sb
    .from("insight_generations")
    .select("*")
    .eq("id", generationId)
    .single();

  if (error) return null;
  return data as InsightGeneration;
}

export async function updateGeneration(
  env: Env,
  generationId: string,
  updates: Partial<InsightGeneration>
): Promise<void> {
  const sb = getSupabase(env);
  const { error } = await sb
    .from("insight_generations")
    .update(updates)
    .eq("id", generationId);
  assertNoError(error);
}

// ── Insight Reports ──

export async function persistReports(
  env: Env,
  generationId: string,
  userId: string,
  reports: InsightReportOutput[],
  allNoteIds: string[]
): Promise<void> {
  const sb = getSupabase(env);

  // Deactivate old generations for this user
  await sb
    .from("insight_generations")
    .update({ is_active: false })
    .eq("user_id", userId)
    .neq("id", generationId);

  for (let idx = 0; idx < reports.length; idx++) {
    const report = reports[idx];
    const reportId = crypto.randomUUID();

    // Insert report
    const now = new Date().toISOString();
    const { error: rErr } = await sb.from("insight_reports").insert({
      id: reportId,
      generation_id: generationId,
      user_id: userId,
      type: report.type,
      title: report.title,
      description: report.description,
      status: "published",
      report_version: 1,
      confidence: report.confidence,
      importance_score: report.importance_score,
      novelty_score: report.novelty_score,
      card_rank: idx + 1,
      report_markdown: report.report_markdown,
      report_json: JSON.stringify(report),
      source_note_ids: JSON.stringify(allNoteIds),
      created_at: now,
      generated_at: now,
    });
    assertNoError(rErr, "insight_reports insert");

    // Insert evidence items
    if (report.evidence_items?.length) {
      const evidence = report.evidence_items.map((ev, i) => ({
        id: crypto.randomUUID(),
        report_id: reportId,
        note_id: ev.note_id,
        quote: ev.quote.slice(0, 500),
        rationale: ev.rationale.slice(0, 500),
        sort_order: i + 1,
        created_at: now,
      }));
      const { error: evErr } = await sb.from("insight_evidence_items").insert(evidence);
      assertNoError(evErr, "insight_evidence_items insert");
    }

    // Insert action items
    if (report.action_items?.length) {
      const actions = report.action_items.map((act, i) => ({
        id: crypto.randomUUID(),
        report_id: reportId,
        title: act.title.slice(0, 255),
        detail: act.detail.slice(0, 500),
        priority: act.priority,
        sort_order: i + 1,
        created_at: now,
      }));
      const { error: actErr } = await sb.from("insight_action_items").insert(actions);
      assertNoError(actErr, "insight_action_items insert");
    }
  }

  // Update generation status
  await sb
    .from("insight_generations")
    .update({
      status: "COMPLETED",
      total_reports: reports.length,
      is_active: true,
      summary: `生成了 ${reports.length} 篇洞察报告`,
      completed_at: new Date().toISOString(),
    })
    .eq("id", generationId);
}

// ── Event Store (for FastAPI SSE bridge) ──
// insight_events table is no longer used — events are stored in DO SQLite.
// These functions are kept as no-ops for backward compatibility.

export async function getEventsAfter(
  _env: Env,
  _generationId: string,
  _afterSequence: number,
  _limit = 100
): Promise<Array<{ sequence: number; payload_json: string | Record<string, unknown> }>> {
  return [];
}

export async function getLatestGeneration(
  env: Env,
  userId: string
): Promise<InsightGeneration | null> {
  const sb = getSupabase(env);
  const { data, error } = await sb
    .from("insight_generations")
    .select("*")
    .eq("user_id", userId)
    .order("created_at", { ascending: false })
    .limit(1)
    .single();

  if (error) return null;
  return data as InsightGeneration;
}

// ── Event appending (no-op — DO SQLite handles event persistence) ──

export async function appendEvent(
  _env: Env,
  _generationId: string,
  _event: Record<string, unknown>
): Promise<void> {
  // Events are now stored in Durable Object SQLite, not Supabase.
}

// ── FastAPI bridge (for PNG rendering) ──

export async function renderShareCardViaBackend(
  env: Env,
  reportId: string
): Promise<Uint8Array | null> {
  // Fallback: call existing FastAPI endpoint for PNG rendering
  // This avoids rewriting Pillow logic in TypeScript
  const backendUrl = env.FRONTEND_URL.replace("app.", "backend.");
  const res = await fetch(`${backendUrl}/api/v1/insights/${reportId}/share-card.png`, {
    headers: {
      "X-Worker-Api-Key": env.BACKEND_API_KEY,
    },
  });

  if (!res.ok) return null;
  return new Uint8Array(await res.arrayBuffer());
}
