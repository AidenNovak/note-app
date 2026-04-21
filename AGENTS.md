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

## 测试账号（Simulator / 开发环境）

- **邮箱**: `aiden@jilly.app`
- **密码**: `Aiden1234!`
- **用户名**: `aiden`
- **用途**: iOS 模拟器自动登录与云端开发测试
- **配置位置**: `easystarter/apps/native/components/auth/sign-in-form.tsx`
- **数据库**: 云端 Supabase 生产数据库已同步该账号密码，user ID 为 `58cca1fa-47b3-4e0e-ba53-3605db9d4ec6`
- **备用账号**: `demo@atelier.dev` / `Demo1234!`（user ID `d00ce7f9-5faa-4232-a2c2-ccc1cd443382`，邮箱地址保留旧域名）
