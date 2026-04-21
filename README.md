# Truth Truth (T²) — Truth, twice.

Native-first note app: Expo iOS client + FastAPI backend.

> The brand is **Truth Truth** (formerly _atélier_). Some technical identifiers
> (`app.jilly.atelier` bundle ID, `atelier-note` npm CLI, `atelier-bucket` R2
> bucket, `atelier_pro_*` IAP product IDs) keep the legacy slug — changing them
> would break Apple/RevenueCat/storage. Anything user-facing has been rebranded.

## Architecture

```text
note-app/
├── _local/           Local-only screenshots, exports, and archive (gitignored)
├── backend/          FastAPI backend (Railway: backend.jilly.app)
├── cli/              `atelier` command-line client (TypeScript)
├── docs/             Workspace-level docs and runbooks
├── easystarter/      Native workspace (separate git repo)
│   ├── apps/native/  Expo 55 + React Native 0.83 app
│   └── packages/     Shared packages used by native
├── jilly/            Simple landing / legal site
└── design-docs/      Product and design docs
```

## Current stack

| Layer | Current runtime |
|---|---|
| **Native app** | Expo 55, Expo Router, React Native 0.83, React 19, TanStack Query |
| **Backend** | FastAPI, SQLAlchemy 2, Alembic |
| **Database** | SQLite for local dev, **Supabase PostgreSQL (Singapore, ap-southeast-1)** in production, pooled via Supavisor |
| **File storage** | Local filesystem in dev, Cloudflare R2 for production file APIs |
| **Auth** | JWT + Apple / Google / GitHub OAuth |
| **Payments** | RevenueCat (native IAP) + Stripe webhooks |
| **AI** | OpenRouter chat + embeddings, OpenAI Whisper |
| **Notifications** | Expo push tokens + backend notification APIs |
| **Email** | Resend |

## Quick start

### 1. Backend

```bash
make backend-install
make backend-dev
```

Local backend runs on `http://localhost:8000`.

### 2. Native

```bash
make native-install
make native-dev
```

### 3. Run both

```bash
make install
make dev
```

### 4. CLI (optional, AI-native scripting)

```bash
npm install -g atelier-note
atelier auth login --email you@example.com
atelier note add "hello from the terminal"
```

See [`cli/README.md`](cli/README.md) for full usage.

## Documentation

- [`docs/repository-layout.md`](docs/repository-layout.md): where code, docs, and local artifacts belong
- [`docs/local-development.md`](docs/local-development.md): local startup, connectivity checks, and validation flow
- [`design-docs/`](design-docs/): product and design materials

## Workspace conventions

- Keep product code inside its owning project (`backend/`, `easystarter/`, `cli/`, `jilly/`).
- Keep workspace-level operational docs in `docs/`.
- Keep local screenshots, exports, and throwaway artifacts under `_local/` only.
- Avoid adding new root-level Markdown files unless they are true entrypoints like `README.md`.

## Environment variables

### Backend (`backend/.env`)

For local development, the backend defaults to SQLite:

```bash
APP_ENV=development
SECRET_KEY=<secure-random-string>
DATABASE_URL=sqlite+aiosqlite:///./data/notes.db
STORAGE_PATH=./data/files
EASYSTARTER_SERVER_URL=http://localhost:8000
FRONTEND_URL=https://app.jilly.app
OPENROUTER_API_KEY=sk-or-...
OPENAI_API_KEY=sk-...
R2_ACCOUNT_ID=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET_NAME=atelier-bucket
RESEND_API_KEY=re_...
STRIPE_SECRET_KEY=sk_...
STRIPE_WEBHOOK_SECRET=whsec_...
REVENUECAT_WEBHOOK_AUTHORIZATION=...
APPLE_APP_BUNDLE_IDENTIFIER=app.jilly.atelier
```

In production, switch `DATABASE_URL` to PostgreSQL, typically:

```bash
DATABASE_URL=postgresql+asyncpg://...
EASYSTARTER_SERVER_URL=https://backend.jilly.app
```

### Native (`easystarter/apps/native/.env.development.local`)

```bash
EXPO_PUBLIC_ATELIER_API_URL=https://backend.jilly.app
EXPO_PUBLIC_WEB_APP_URL=https://app.jilly.app
EXPO_PUBLIC_PROJECT_ID=<expo-project-id>
EXPO_PUBLIC_REVENUECAT_IOS_API_KEY=<revenuecat-ios-public-key>
EXPO_PUBLIC_REVENUECAT_ENTITLEMENT_ID=pro
```

If you want local simulator builds to behave like TestFlight, point
`EXPO_PUBLIC_ATELIER_API_URL` at the same deployed backend.

## Development commands

```bash
make backend-lint
make backend-test
cd easystarter && pnpm lint
cd easystarter && pnpm check-types
cd easystarter/apps/native && pnpm ios
```

## TestFlight

```bash
cd easystarter/apps/native
pnpm eas:build:ios:production
pnpm eas:submit:ios:production
```

## Deployment

- **Backend app**: Railway (region `asia-southeast1-eqsg3a`, Singapore), health endpoint at `/health`, DB-aware readiness at `/ready`
- **Production database**: Supabase PostgreSQL (Singapore, `ap-southeast-1`) via Supavisor
- **File storage**: Cloudflare R2 (public CDN: `cdn.jilly.app`)
- **Landing / legal web**: `jilly/`, built and deployed separately
