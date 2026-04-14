# Note App (atélier) — Agent Notes

## 产品

**atélier** — "Your Second Digital Mind"，四模块：Inbox / Mind / Insight / Ground。

## 结构

- **jilly/** — Web 前端主目录（React 19 + Vite 6 + React Router v7，纯 CSS，JSX）
  - `src/pages/LandingPage.jsx` + `src/styles/landing.css` — atélier Landing Page
  - `public/images/atelier/` — 产品截图
  - Dev: `cd jilly && npx vite`（port 3000）
- **backend/** — Python 后端（FastAPI + SQLAlchemy + Alembic）
- **easystarter/** — 独立 SaaS starter 子项目，有自己的 git 和 AGENTS.md，修改前先读它
- **archive/** — 归档（Flutter 等旧方案，仅参考）
- **design.md/** — 设计文档（landing-plan, landing-review, uiux 等）

## 约定

- Expo (native) 是移动端主线，Flutter 在 archive 仅作参考
- Landing Page CSS 以 `.atelier-landing` 作用域 + `al-` 前缀，避免与 global.css 冲突
- 设计语言：奶油底 #fafaf5 / 森林绿 #2d5a3d / 黄绿 #c8e64a，Playfair Display + Inter
