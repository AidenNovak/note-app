# atélier — Your Second Digital Mind

iOS 笔记应用：Expo (React Native) + FastAPI 后端。

## 架构

```
note-app/
├── backend/          FastAPI 后端 (Railway: backend.jilly.app)
├── easystarter/      Native App monorepo (独立 git)
│   ├── apps/native/  Expo 55 + React Native 0.83 (iOS)
│   └── packages/     共享包 (i18n / app-config / shared)
├── jilly/            Landing Page (React + Vite, 仅营销)
└── design-docs/      设计文档
```

### 技术栈

| 层 | 技术 |
|---|------|
| **iOS 客户端** | Expo 55, React Native 0.83, TypeScript 5.9, React Query v5 |
| **后端** | FastAPI, SQLAlchemy 2.0, Alembic, PostgreSQL (Railway) |
| **存储** | Cloudflare R2 (S3 API) |
| **认证** | JWT (HS256) + OAuth (Apple/Google/GitHub) |
| **支付** | RevenueCat (iOS IAP) + Stripe (webhook) |
| **AI** | OpenRouter (Kimi K2.5), OpenAI Whisper, text-embedding-3-small |
| **推送** | Expo Push Notifications |
| **邮件** | Resend |

## 快速开始

### 1) 后端

```bash
make backend-install
make backend-dev          # http://localhost:8000
```

### 2) Native (iOS)

```bash
make native-install
make native-dev           # Expo → iOS 模拟器
```

### 3) 同时启动

```bash
make install              # 安装 backend + native
make dev                  # 并行启动 backend + native
```

### 4) 环境变量

**后端** (`backend/.env`):

```bash
APP_ENV=production
SECRET_KEY=<cryptographically-random-32+chars>
DATABASE_URL=postgresql+asyncpg://...
OPENROUTER_API_KEY=sk-or-...
OPENAI_API_KEY=sk-...
R2_ACCOUNT_ID=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET_NAME=atelier-files
RESEND_API_KEY=re_...
STRIPE_SECRET_KEY=sk_...
REVENUECAT_WEBHOOK_AUTHORIZATION=...
APPLE_APP_BUNDLE_IDENTIFIER=app.jilly.atelier
```

**Native** (`easystarter/apps/native/.env`):

```bash
EXPO_PUBLIC_API_BASE_URL=https://backend.jilly.app
EXPO_PUBLIC_PROJECT_ID=6b054564-fb28-40dc-9f93-88029cd5facb
```

## 开发命令

```bash
make backend-lint         # Ruff 代码检查
make backend-test         # Pytest 单元/集成测试
cd easystarter && pnpm -F native lint          # Native lint
cd easystarter/apps/native && pnpm exec tsc --noEmit  # TS 类型检查
```

## TestFlight 发布

```bash
cd easystarter/apps/native
eas build --platform ios --profile production --auto-submit
```

## 部署

- **后端**: Railway (`backend.jilly.app`), Dockerfile 构建, 健康检查 `/health`
- **Landing Page**: `cd jilly && npx vite build` → 静态部署
