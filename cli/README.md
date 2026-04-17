# @jilly/atelier-cli

A command-line interface for [atélier](https://jilly.app) — your second digital
mind, scriptable from the terminal. Designed for AI-native workflows (pipe into
LLMs, cron jobs, scripts, automation).

## Install

```bash
npm install -g @jilly/atelier-cli
# or: pnpm add -g @jilly/atelier-cli
```

Then `atelier --help`. Requires Node 20+.

### From source (for contributors)

```bash
cd cli
pnpm install
pnpm build
pnpm link --global
```

## Sign in

You need a **Personal Access Token** (PAT, starts with `atl_`). You can get one
two ways:

### Option A — email + password (easiest)

```bash
atelier auth login --email you@example.com --token-name 'laptop'
# prompts for password, mints a 90-day PAT, and stores it.
```

### Option B — paste an existing PAT

```bash
atelier auth login --token atl_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### Check status

```bash
atelier auth whoami
atelier auth status
```

## Commands

| Command | What it does |
|---|---|
| `atelier capture "thought"` | Fast-capture into Inbox (also accepts `--file` / stdin) |
| `atelier note add <text…>` | Create a note (supports `--tag`, `--folder`, `--file`, stdin) |
| `atelier note ls` | List notes (`--limit`, `--folder`, `--tag`, `--json`) |
| `atelier note get <id>` | Print markdown content |
| `atelier note search <q…>` | Full-text search |
| `atelier note rm <id>` | Delete (`-y` to skip confirm) |
| `atelier folder ls` | Folder tree |
| `atelier tag ls` | All tags |
| `atelier token ls/create/rm` | Manage PATs (requires a full browser/app login — not a PAT) |

Every read-style command supports `--json` for piping into `jq` or scripts.

## Configuration

Config lives at `~/.config/atelier/config.json` (or `$XDG_CONFIG_HOME/atelier/`).
Permissions are locked to `0600`.

Environment variables override the config file:

- `ATELIER_API_URL` — backend URL (default `https://backend.jilly.app`)
- `ATELIER_TOKEN` — PAT to use (bypasses the stored one)

## AI-native examples

```bash
# Pipe a long transcript into your Inbox
ffmpeg -i talk.m4a -f wav - | whisper - | atelier capture --tag meeting

# Summarise today's notes with a local LLM
atelier note ls --json --limit 20 | jq '.[].id' | \
  xargs -I{} atelier note get {} | ollama run llama3 "summarise this"

# Grep across your brain
atelier note search "RAG" --json | jq -r '.items[].id'
```

## Security notes

- PATs are prefixed `atl_`; only the prefix (`atl_xxxxxxxx`) is ever
  retrievable after creation. The plaintext is shown **once**.
- PAT scopes are enforced server-side by HTTP method: GET/HEAD need `read`,
  everything else needs `write`. `admin` is reserved.
- PATs **cannot** manage other PATs (`/tokens/*` requires an interactive
  session). This prevents a leaked PAT from creating permanent replacements.
- Rate limiting is keyed per-token, not per-IP.

## Troubleshooting

- `HTTP 401 INVALID_API_TOKEN` — run `atelier auth login` again.
- `HTTP 403 SESSION_REQUIRED` — you tried to call `/tokens/*` with a PAT; use
  the iOS app or mint a JWT.
- Network errors — check `ATELIER_API_URL` and `atelier auth status`.
