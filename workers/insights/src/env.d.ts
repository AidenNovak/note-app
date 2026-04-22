/// <reference types="@cloudflare/workers-types" />

declare namespace Cloudflare {
  interface Env {
    // Cloudflare bindings
    AI: Ai;
    InsightAgent: DurableObjectNamespace<import("./agent").InsightAgent>;

    // Secrets (wrangler secret put)
    SUPABASE_URL: string;
    SUPABASE_SERVICE_KEY: string;
    CF_API_TOKEN?: string;
    CF_ACCOUNT_ID?: string;
    OPENROUTER_API_KEY?: string;
    BACKEND_API_KEY: string; // For calling FastAPI PNG render endpoint

    // Vars (from wrangler.jsonc)
    AI_PROVIDER: "cloudflare" | "openrouter";
    AI_MODEL: string;
    INSIGHTS_AI_MODEL: string;
    EMBEDDING_MODEL: string;
    AI_MAX_TOKENS: string;
    AI_TEMPERATURE: string;
    FRONTEND_URL: string;
    INSIGHT_MAX_CONTEXT_NOTES: string;
    INSIGHT_MAX_NOTE_CHARS: string;
    MAX_REPORTS: string;
  }
}

interface Env extends Cloudflare.Env {}
