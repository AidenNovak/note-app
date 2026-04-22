# Workers AI Migration

Migrated backend AI provider from **OpenRouter** to **Cloudflare Workers AI** (REST API).

## What Changed

| Component | Before | After |
|-----------|--------|-------|
| General LLM | `moonshotai/kimi-k2.5` via OpenRouter | `@cf/meta/llama-3.3-70b-instruct-fp8-fast` via CF Workers AI |
| Reasoning LLM | `moonshotai/kimi-k2-thinking` via OpenRouter | `@cf/deepseek-ai/deepseek-r1-distill-qwen-32b` via CF Workers AI |
| Embeddings | `openai/text-embedding-3-small` (1536 dims) via OpenRouter | `@cf/baai/bge-m3` (multilingual) via CF Workers AI native API |
| Auth key | `OPENROUTER_API_KEY` | `CF_API_TOKEN` + `CF_ACCOUNT_ID` |
| Reasoning trace | OpenRouter `reasoning` delta in stream | `<think>…</think>` in content (handled by `_ThinkBlockSplitter`) |

## Files Affected

- `backend/app/config.py` — new `AI_PROVIDER`, `CF_API_TOKEN`, `CF_ACCOUNT_ID` settings; model defaults updated
- `backend/app/intelligence/ai/workers_ai.py` — **new** `WorkersAIProvider` using CF OpenAI-compatible endpoint
- `backend/app/intelligence/ai/__init__.py` — factory gates on `AI_PROVIDER`
- `backend/app/intelligence/ai/archive/openrouter_legacy.py` — archived original OpenRouter provider
- `backend/app/intelligence/insights/llm.py` — `_AIModel` dataclass; `extra_body` gated on `openrouter`; bug fixes (see below)
- `backend/app/intelligence/embeddings.py` — CF native embeddings API + OpenRouter fallback
- `backend/alembic/versions/20260720_000017_clear_embeddings_for_cf_migration.py` — clears stale embeddings
- `backend/scripts/backfill_embeddings.py` — **new** script to regenerate all missing embeddings
- `backend/.env.example` / `backend/.env.railway.example` — updated env vars

## Bug Fixes (incidental)

Two pre-existing bugs were fixed during migration:

1. **`_generate_text_sync` model hardcode** — was always using `settings.AI_MODEL` regardless of the `model` parameter passed in. Fixed to use `_resolve_model_id(model, settings.AI_MODEL)`.
2. **`write_report_markdown` model forwarding** — resolved an `_AIModel` from `get_insights_model()` but called `stream_text_async()` without passing `model_name`, so all insight reports used the general model. Fixed to extract and forward `model.name`.

## Endpoint

CF Workers AI is accessed via its **OpenAI-compatible REST endpoint**:

```
https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/v1
```

Auth: `Bearer {CF_API_TOKEN}`.

Embeddings use the native CF endpoint (different response format from OpenAI):

```
POST /accounts/{CF_ACCOUNT_ID}/ai/run/{EMBEDDING_MODEL}
Body: {"text": "..."}
Response: {"result": {"data": [[...floats...]]}}
```

## Deployment Steps

1. Set Railway env vars:
   ```
   AI_PROVIDER=cloudflare
   CF_API_TOKEN=<your token>
   CF_ACCOUNT_ID=<your account id>
   ```
2. Deploy (auto-runs Alembic migrations, which clears stale embeddings).
3. Run backfill:
   ```
   python backend/scripts/backfill_embeddings.py
   ```

## Rollback

Set `AI_PROVIDER=openrouter` and provide `OPENROUTER_API_KEY` — no code changes needed.
The old provider class is at `app/intelligence/ai/archive/openrouter_legacy.py`.

If you rollback to OpenRouter after running the CF migration, note that embeddings
are still cleared. You'll need to re-run the backfill once pointed at OpenRouter.

## Snapshot Tag

```
git checkout pre-workers-ai-migration
```
