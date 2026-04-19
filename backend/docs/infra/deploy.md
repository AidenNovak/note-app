# Deployment & Release

This doc is the playbook for shipping new backend versions to Railway and
publishing new `atelier-note` versions to npm.

## Backend (Railway)

### Topology

- **Host**: Railway (`backend.jilly.app`), region `asia-southeast1-eqsg3a` (Singapore).
- **Container**: `backend/Dockerfile` (python:3.12-slim).
- **Entrypoint**: `backend/entrypoint.sh`
  - Runs `alembic upgrade head` on every boot (disable with `RUN_MIGRATIONS=0`).
  - Then `exec uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- **Database**: Supabase PostgreSQL (Singapore, `ap-southeast-1`) via Supavisor.
- **Health check**: `GET /health` (configured in `railway.toml`, 30s timeout).

### Deploying

Railway auto-deploys on push to `main`.

```bash
git push origin main
# Watch the deploy log in Railway dashboard until /health goes green.
```

On each boot the container will:

1. Print `[entrypoint] Running alembic upgrade head…`
2. Apply any new migrations against the production Supabase DB.
3. Start uvicorn.

If migrations fail, the container exits and Railway retries up to 3× per
`railway.toml` `restartPolicyMaxRetries`. **Never merge a migration whose
`downgrade` path isn't tested** — a broken migration will block the deploy.

### Rolling back

1. In Railway dashboard → Deployments → pick the previous good deploy → Redeploy.
2. If the new migration was schema-incompatible with the old code, run the
   downgrade manually first from a local shell with production `DATABASE_URL`:

   ```bash
   cd backend && . .venv/bin/activate
   DATABASE_URL="postgresql+asyncpg://…" alembic downgrade -1
   ```

### One-off migration (no code change)

```bash
cd backend && . .venv/bin/activate
DATABASE_URL="postgresql+asyncpg://…supabase…" alembic upgrade head
```

(Requires `psycopg[binary]` locally — already pinned in `requirements.txt`.)

## CLI (`atelier-note`)

### First-time publish

1. Create the `jilly` npm organization if it doesn't exist:

   ```bash
   npm org create jilly          # needs paid plan or public-only scope
   # Or: create via https://www.npmjs.com/org/create
   ```

   Public scoped packages are free.

2. Log in as a member of the org:

   ```bash
   npm login
   npm whoami                    # sanity
   ```

3. Publish:

   ```bash
   cd cli
   pnpm install
   pnpm build
   npm publish --access public
   ```

   (`publishConfig.access: public` is already set in `package.json`, so
    `--access public` is redundant but explicit.)

### Subsequent releases

```bash
cd cli
# bump patch/minor/major as appropriate
npm version patch               # writes 0.1.1, creates git tag
pnpm build
npm publish
git push --follow-tags
```

### Verifying the release

```bash
npm info atelier-note version
npx atelier-note@latest --help
```

### User install

```bash
npm install -g atelier-note
atelier auth login --email you@example.com
```

## PAT (Personal Access Token) lifecycle

- End-users currently get their first PAT via `atelier auth login --email X`,
  which JWT-logs-in and mints a 90-day PAT in one step.
- PAT management UI in the iOS app is **not yet shipped**; when it lands,
  `atelier token ls/create/rm` via the app-issued session cookie will work
  too (server-side already supports it via `require_session`).
- Rate-limiting: keyed per-token (see `backend/app/middleware.py::_rate_limit_key`).
- Revoking a compromised PAT: user runs `atelier token rm <id>` from an
  interactive session, or admin can `UPDATE api_tokens SET revoked_at = now() WHERE id = …`.
