# Note App (Truth Truth · T²) — Agent Notes

## 产品

**Truth Truth (T²)** — "Truth, twice." / "Capture once. See it twice." 四模块：Inbox / Mind / Insight / Ground。

> 历史名 "atélier" 仍保留在技术接口中（bundle ID `app.jilly.atelier`、R2 bucket `atelier-bucket`、IAP product IDs `atelier_pro_*`、CLI bin/env `atelier*`、storage key `atelier_token`、CSS class `.atelier-landing`、demo 邮箱 `demo@atelier.dev`）— 改这些会破坏现有用户/账单/部署，**不要改**。所有用户可见文案统一用 Truth Truth。

## 架构（Native-Only）

本项目专注 **iOS Native 应用 + FastAPI 后端**，Web 端已移除。

## 结构

- **backend/** — Python 后端（FastAPI + SQLAlchemy + Alembic），部署在 Railway
- **easystarter/** — Native App monorepo（独立 git），修改前先读其 AGENTS.md
  - `apps/native/` — Expo 55 + React Native 0.83（主产品）
  - `packages/` — 共享包（i18n / app-config / shared）
- **jilly/** — Landing Page（React 19 + Vite 6，独立项目，仅营销用途）
  - Dev: `cd jilly && npx vite`（port 3000）
- **design-docs/** — 设计文档

## 约定

- 仅支持 Native (Expo/iOS)，无 Web 端交付
- 后端 API: `backend.jilly.app`（**Railway**，region `asia-southeast1-eqsg3a` / 新加坡）
- 生产数据库: **Supabase PostgreSQL（新加坡 ap-southeast-1）**，通过 Supavisor 连接；2026-04 从日本区迁入，迁移记录见 `backend/docs/infra/db-migration-2026-04-sg.md`
- 本地开发数据库: SQLite (`backend/data/notes.db`)，`DATABASE_URL=sqlite+aiosqlite:///./data/notes.db`
- 文件存储: Cloudflare R2 (S3 API)，公开域 `cdn.jilly.app`
- 支付: RevenueCat (iOS IAP) + Stripe (webhook)
- AI: OpenRouter (chat + embeddings，`moonshotai/kimi-k2.5`) + OpenAI Whisper (音频转写)
- 邮件: Resend；推送: Expo push
- 健康检查: `GET /health`（基础）、`GET /ready`（含 DB 探活）
- 设计语言：奶油底 #fafaf5 / 森林绿 #2d5a3d / 黄绿 #c8e64a，Playfair Display + Inter

---

## 🔮 Insights Cloudflare Worker 迁移（进行中）

> **目标**: 将 `backend/app/intelligence/insights/` 从 FastAPI + Python 迁移到 **Cloudflare Workers + Durable Objects + Think (Agents SDK)**，实现真正的边缘 Agent 架构。

### 为什么迁移

| 痛点 | 现状 (FastAPI) | 目标 (Cloudflare Think) |
|------|---------------|------------------------|
| 状态持久化 | 手动 `workspace_json` + `insight_events` 表 | DO 内置 SQLite，自动持久化 |
| 流式恢复 | SSE + DB 轮询实现 `last_sequence` | Think 内置可恢复流式 |
| Agent 生命周期 | 自定义代码管理 | Think 基类完整生命周期 |
| 冷启动/扩展 | Railway 单实例 | Workers 全球边缘，按请求扩展 |
| 调度任务 | 无（需外部 cron） | DO Alarm 内置 `schedule()` |

### 项目结构

```
workers/insights/               ← 新增 Cloudflare Worker 项目
├── src/
│   ├── server.ts              # Worker 入口 (routeAgentRequest)
│   ├── agent.ts               # InsightAgent extends Think
│   ├── pipeline.ts            # 并行生成流水线 (discover → reports)
│   ├── db.ts                  # Supabase 数据层 (替换 SQLAlchemy)
│   ├── types.ts               # TypeScript 类型 (对齐 Pydantic)
│   └── env.d.ts               # Wrangler Env 类型声明
├── package.json               # deps: agents, @cloudflare/think, ai, zod, @supabase/supabase-js
├── tsconfig.json              # extends agents/tsconfig
└── wrangler.jsonc             # DO bindings, AI binding, migrations
```

### 架构对比

```
Before (FastAPI)                          After (Cloudflare Think)
─────────────────────────────────         ─────────────────────────────────
Client ──SSE──→ FastAPI (Railway)         Client ──WebSocket──→ CF Worker
                    │                                         │
                    ├── SQLAlchemy ──→ Supabase PG           ├── routeAgentRequest
                    ├── pipeline.py (async func)             └── InsightAgent (DO)
                    ├── agent.py (state machine-ish)              ├── DO SQLite (state)
                    ├── event_store.py (batch flush)              ├── Think (agentic loop)
                    └── llm.py (OpenAI SDK)                     ├── streamText (Vercel AI SDK)
                                                                 └── Supabase JS (数据层)
```

### 配置映射（Python → TypeScript/Workers）

| Python (`backend/app/config.py`) | TypeScript (`wrangler.jsonc` vars) | 说明 |
|----------------------------------|-----------------------------------|------|
| `AI_PROVIDER = "cloudflare"` | `AI_PROVIDER` | 当前仅支持 cloudflare |
| `AI_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"` | `AI_MODEL` | Workers AI 模型 ID |
| `INSIGHTS_AI_MODEL = "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b"` | `INSIGHTS_AI_MODEL` | 推理模型 |
| `EMBEDDING_MODEL = "@cf/baai/bge-m3"` | `EMBEDDING_MODEL` | 嵌入模型（预留） |
| `AI_MAX_TOKENS = 4096` | `AI_MAX_TOKENS` | 最大 token |
| `AI_TEMPERATURE = 0.7` | `AI_TEMPERATURE` | 温度 |
| `INSIGHT_MAX_CONTEXT_NOTES = 12` | `INSIGHT_MAX_CONTEXT_NOTES` | 上下文笔记数 |
| `INSIGHT_MAX_NOTE_CHARS = 4000` | `INSIGHT_MAX_NOTE_CHARS` | 单笔记最大字符 |
| `DATABASE_URL` | `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` | Supabase HTTP API |
| `CF_API_TOKEN` | `CF_API_TOKEN` | Workers AI Token |
| `CF_ACCOUNT_ID` | `CF_ACCOUNT_ID` | Cloudflare Account ID |
| `OPENROUTER_API_KEY` | `OPENROUTER_API_KEY` | 回退 Provider（预留） |

### Secrets（`wrangler secret put`）

```bash
cd workers/insights
npx wrangler secret put SUPABASE_URL           # https://xxxx.supabase.co
npx wrangler secret put SUPABASE_SERVICE_KEY   # service_role key（绕过 RLS）
npx wrangler secret put CF_API_TOKEN           # Workers AI Read token
npx wrangler secret put CF_ACCOUNT_ID          # Cloudflare Account ID
npx wrangler secret put OPENROUTER_API_KEY     # 可选，回退用
npx wrangler secret put BACKEND_API_KEY        # 调用 FastAPI PNG 渲染接口的密钥
```

### 数据库 Schema（复用现有，零迁移）

Worker **不新建表**，直接读写现有 Supabase 表：

| 表名 | Worker 操作 | 说明 |
|------|------------|------|
| `notes` | SELECT | 获取用户笔记 |
| `note_tags` | SELECT (via join) | 笔记标签 |
| `mind_connections` | SELECT | 图关联 |
| `insight_generations` | INSERT/UPDATE | 生成任务状态 |
| `insight_reports` | INSERT | 报告数据 |
| `insight_evidence_items` | INSERT | 证据条目 |
| `insight_action_items` | INSERT | 行动条目 |
| `insight_events` | **不再写入** | 由 DO SQLite 替代 |

### 数据流变化

**生成请求：**
```
1. iOS App POST /api/v1/insights/generate  →  FastAPI (保留)
2. FastAPI 创建 generation 记录后，HTTP 调用 Worker:
   POST /agents/InsightAgent/:generationId/generate
   { user_id: "..." }
3. Worker 的 InsightAgent (DO) 执行 pipeline
4. Pipeline 通过 Supabase JS 读写数据
5. 客户端通过 WebSocket 连接 DO 接收流式事件
```

**分享卡片 PNG：**
- Worker **不渲染 PNG**（Pillow 在 TypeScript 中不可用）
- Worker 保存报告后，调用 FastAPI `GET /api/v1/insights/{id}/share-card.png`
- FastAPI 保留 Pillow 渲染逻辑

### 部署步骤

```bash
# 1. 安装依赖
cd workers/insights
npm install

# 2. 配置 secrets
npx wrangler secret put SUPABASE_URL
npx wrangler secret put SUPABASE_SERVICE_KEY
npx wrangler secret put CF_API_TOKEN
npx wrangler secret put BACKEND_API_KEY

# 3. 本地开发
npm run dev

# 4. 部署
npm run deploy
```

### 已知限制与风险

| 限制 | 影响 | 缓解方案 |
|------|------|----------|
| **TypeScript 重写** | ~2000 行逻辑需重写 | 已对齐 Think 设计模式，代码结构清晰 |
| **Supabase JS 无 SQLAlchemy 关系加载** | `selectinload(Note.tags)` → `notes.select('*, note_tags(tag)')` | 已封装在 `db.ts` |
| **networkx 图聚类不可用** | `graph_clustering.py` 的 Louvain 算法 | 当前 pipeline 已不依赖聚类（动态角度发现）；如需恢复，可在 Worker 中用 `graphology` 替代 |
| **Pillow PNG 渲染** | `share_cards.py` 无法移植 | 保留在 FastAPI，Worker 通过 HTTP 调用 |
| **JWT 认证** | Worker 需独立验证 token | 共享 `SECRET_KEY`，Worker 内验证 JWT |
| **推送通知** | Worker 无法直接调用 Expo Push | 生成完成后 HTTP 回调 FastAPI 发送推送 |

### 回滚策略

1. **Feature flag**: FastAPI `/insights/generate` 增加 `?worker=false` 参数，切回本地 pipeline
2. **数据库兼容**: Worker 和 FastAPI 使用同一套表，无数据格式差异
3. **事件兼容**: Worker 广播的事件类型与现有 SSE 完全一致（`starting`, `progress`, `group_started`, `thinking_delta`, `markdown_delta`, `group_completed`, `completed`, `error`）

### 进度

- [x] 项目骨架 (`package.json`, `wrangler.jsonc`, `tsconfig.json`)
- [x] 类型定义 (`types.ts`) — 对齐 Pydantic schema
- [x] 数据层 (`db.ts`) — Supabase 客户端封装
- [x] Think Agent (`agent.ts`) — 生命周期 hooks + 工具注册
- [x] 生成流水线 (`pipeline.ts`) — discover angles → parallel reports
- [x] Worker 入口 (`server.ts`)
- [ ] JWT 认证中间件
- [ ] FastAPI → Worker 调用桥接
- [ ] 客户端 WebSocket 连接（替换 SSE）
- [ ] 端到端测试
- [ ] 生产部署

## 测试账号（Simulator / 开发环境）

- **邮箱**: `aiden@jilly.app`
- **密码**: `Aiden1234!`
- **用户名**: `aiden`
- **用途**: iOS 模拟器自动登录与云端开发测试
- **配置位置**: `easystarter/apps/native/components/auth/sign-in-form.tsx`
- **数据库**: 云端 Supabase 生产数据库已同步该账号密码，user ID 为 `58cca1fa-47b3-4e0e-ba53-3605db9d4ec6`
- **备用账号**: `demo@atelier.dev` / `Demo1234!`（user ID `d00ce7f9-5faa-4232-a2c2-ccc1cd443382`，邮箱地址保留旧域名）
