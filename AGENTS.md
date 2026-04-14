# Note App (atélier) — Agent Notes

## 产品

**atélier** — "Your Second Digital Mind"，四模块：Inbox / Mind / Insight / Ground。

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
- 后端 API: `backend.jilly.app`（Railway）
- 存储: Cloudflare R2 (S3 API)
- 支付: RevenueCat (iOS IAP) + Stripe (webhook)
- 设计语言：奶油底 #fafaf5 / 森林绿 #2d5a3d / 黄绿 #c8e64a，Playfair Display + Inter
