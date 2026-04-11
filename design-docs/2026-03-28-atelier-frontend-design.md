# atelier 前端设计文档

> **版本**: v0.1
> **日期**: 2026-03-28
> **状态**: 待审核

## 1. 项目概述

**atelier** 是一款"第二数字大脑"移动应用，核心理念是帮助用户随时随地采集灵感碎片（文字、语音、图片、视频、网页），通过 AI 归一化处理为结构化 Markdown，并以知识图谱的形式可视化展示笔记间的关联。

### 1.1 定位

- **目标用户**: 创意工作者、研究者、设计师、写作者等需要频繁采集和管理灵感的人群
- **核心价值**: 多渠道采集 → AI 归一化 → 知识图谱可视化 → AI 洞察生成
- **品牌调性**: 安静、极简、专注 — "A Space for Quiet Reflection"

### 1.2 技术决策

| 维度 | 选择 | 理由 |
|------|------|------|
| iOS | Swift + SwiftUI | 原生体验，系统级 API 支持 |
| Android | Kotlin + Jetpack Compose | 现代声明式 UI，与 SwiftUI 对称 |
| 仓库结构 | 双仓库独立 | 平台特性差异大，独立迭代 |
| 后端 | 现有 FastAPI 后端，前后端协同调整 | 已有核心 CRUD，需扩展 Insight/Social API |
| 图谱渲染 | 现成库快速实现 | iOS: GraphView / Android: 用 Canvas + 手势库 |

---

## 2. 设计规范

### 2.1 配色

| 用途 | 色值 | 示例 |
|------|------|------|
| 品牌绿 (Primary) | `#4CAF50` | 按钮高亮、Tab 激活态、品牌文字 |
| 辅助黄 (Secondary) | `#FFC107` | Research Lab 节点、Quick Note 标签 |
| 背景白 | `#FFFFFF` | 全局背景 |
| 浅灰背景 | `#F5F5F5` | 卡片区域背景 |
| 正文深灰 | `#333333` | 标题、正文 |
| 辅助灰 | `#666666` | 描述文字、副标题 |
| 淡灰 | `#999999` | 时间戳、非活跃 Tab |
| 标签绿底 | `rgba(76,175,80,0.15)` | WhatsApp/Instagram 标签背景 |
| 标签黄底 | `rgba(255,193,7,0.15)` | Quick Note/Browser 标签背景 |

### 2.2 字体

| 用途 | 字重 | 大小 |
|------|------|------|
| 品牌名 "atelier" | Bold, 自定义有机字体 | 32pt |
| 页面标题 | Bold | 24pt |
| 卡片标题 | SemiBold | 18pt |
| 正文 | Regular | 16pt |
| 描述/副标题 | Regular | 14pt |
| 时间戳/元数据 | Regular | 12pt |
| 标签文字 | Medium, uppercase | 11pt |

### 2.3 间距与圆角

- 卡片间距: 16pt
- 卡片内边距: 16pt
- 卡片圆角: 12pt
- 按钮圆角: 24pt (胶囊形)
- 底部 Tab 栏高度: 64pt
- FAB 直径: 56pt

### 2.4 图标

使用 SF Symbols (iOS) / Material Icons (Android)，保持语义一致：

| 功能 | iOS (SF Symbols) | Android (Material) |
|------|-------------------|---------------------|
| Inbox | `tray.full` | `inbox` |
| Mind | `brain.head.profile` | `psychology` |
| Insight | `lightbulb` | `lightbulb` |
| Ground | `mountain.2` | `terrain` |
| 添加 | `plus` | `add` |
| 设置 | `gearshape` | `settings` |
| 搜索 | `magnifyingglass` | `search` |
| 用户 | `person.circle` | `person` |

---

## 3. 页面详细设计

### 3.1 启动页 (Entry Screen)

**对应设计稿**: Body.png

```
┌─────────────────────────┐
│                         │
│        [📖 icon]        │  ← 绿色圆形背景 + 白色书本图标
│                         │
│        atelier          │  ← 品牌绿，32pt Bold
│   YOUR SECOND DIGITAL   │  ← 浅灰，14pt uppercase
│          MIND           │
│                         │
│    ─── EST. MMXXIV ───  │  ← 淡灰，两侧细线装饰
│                         │
│     [ ENTER MIND → ]    │  ← 绿底白字，胶囊按钮
│                         │
│                         │
│  A Space for Quiet      │  ← 底部，淡灰小字
│       Reflection        │
└─────────────────────────┘
```

**功能**:
- 品牌展示
- 点击 "ENTER MIND" → 若已登录进入 Inbox，否则进入登录页

**API**: `POST /auth/login` 或读取本地 token

---

### 3.2 登录/注册页 (Auth Screen)

设计稿中未单独出图，需补充。

**功能**:
- 邮箱 + 密码登录
- 邮箱 + 密码注册
- Token 本地持久化 (Keychain / Keystore)

**API**:
- `POST /auth/register`
- `POST /auth/login`
- `POST /auth/refresh`

---

### 3.3 Inbox 页 — 碎片收集流

**对应设计稿**: image 3

```
┌─────────────────────────────┐
│ atelier  ●              ⚙️  │  ← 顶栏：品牌 + 设置
├─────────────────────────────┤
│ Collected Fragments         │  ← 页面标题
│ Bits of inspiration waiting │  ← 副标题
│ to be woven into your story │
├─────────────────────────────┤
│ ┌─────────────────────────┐ │
│ │ WHATSAPP  10:45 AM      │ │  ← 来源标签 + 时间
│ │ Downtown Cafe           │ │  ← 地点
│ │                         │ │
│ │ The Architecture of     │ │  ← 标题 Bold
│ │ Silence: An interview   │ │
│ │                         │ │
│ │ A fascinating look at   │ │  ← 描述 灰色
│ │ how space influences... │ │
│ │                         │ │
│ │ 🏷️ AI Tag: Spatial     │ │  ← AI 自动标签
│ │     Design              │ │
│ │ [thumbnail]             │ │  ← 缩略图
│ └─────────────────────────┘ │
│                             │
│ ┌─────────────────────────┐ │
│ │ QUICK NOTE  09:12 AM    │ │
│ │ On the Go               │ │
│ │                         │ │
│ │ "The way the light hit  │ │  ← 引用，斜体
│ │ the brickwork this      │ │
│ │ morning felt like..."   │ │
│ │                         │ │
│ │ 📍 Captured near        │ │
│ │    Central Station      │ │
│ └─────────────────────────┘ │
│                             │
│ ┌─────────────────────────┐ │
│ │ INSTAGRAM  Yesterday    │ │
│ │ Home Study              │ │
│ │ [full-width image]      │ │  ← 全宽图片
│ │ Visual Moodboard:       │ │
│ │ Ethereal Light          │ │
│ │ Saved from @light_...   │ │
│ │ [AUTO_AWESOME] [VISUAL] │ │  ← 标签组
│ └─────────────────────────┘ │
│                             │
│ ┌─────────────────────────┐ │
│ │ BROWSER  Yesterday 4:20 │ │
│ │ The Future of Generative│ │
│ │ Curation                │ │
│ │ the-editorial-journal.. │ │  ← URL
│ └─────────────────────────┘ │
│                             │
├─────┬──────┬────────┬──────┤
│📥   │ 🧠   │  💡    │ 🏔️  │  ← 底部导航
│INBOX│ MIND │INSIGHT │GROUND│
│[绿] │      │        │      │  ← 当前 Tab 高亮
├─────┴──────┴────────┴──────┤
│                        [+] │  ← FAB 悬浮按钮
└─────────────────────────────┘
```

**卡片类型映射**:

| 来源标签 | 颜色 | 对应后端 source_type |
|---------|------|---------------------|
| WHATSAPP | 绿底白字 | `text` (或新增 `whatsapp`) |
| QUICK NOTE | 黄底灰字 | `text` |
| INSTAGRAM | 绿底白字 | `file` (图片) |
| BROWSER | 黄底灰字 | `file` (URL) |
| VOICE MEMO | 绿底白字 | `voice` |
| VIDEO | 绿底白字 | `video` |

**功能**:
- 下拉刷新
- 按来源筛选（顶部 filter chips）
- 点击卡片 → 笔记详情
- FAB "+" → 快速采集（文字/语音/拍照/文件）

**API**:
- `GET /notes?page=1&page_size=20&status=completed`
- `GET /notes?tag=xxx` (按标签筛选)

---

### 3.4 Mind 页 — 知识图谱

**对应设计稿**: image 1, image 2

```
┌─────────────────────────────┐
│ ☰   atelier            👤   │  ← 汉堡菜单 + 品牌 + 头像
├─────────────────────────────┤
│ Neural Synthesis            │  ← "Synthesis" 为品牌绿色
│ DEEP MAPPING MODE           │  ← 灰色副标题
│                             │
│        [Research Lab]       │
│         🔬 黄色节点          │
│              \              │
│               \             │
│  [Minimalist]  ★  [Notes]  │  ← 绿色节点围绕中心
│   💬 绿色     ⬤     📝 绿色 │
│              /              │
│             /               │
│     [未标记节点]  [🎓]      │  ← 小灰色节点
│                             │
│   ┌─── Synthesis Update ──┐│
│   │ ⭐ New connections    ││  ← AI 更新卡片
│   │ detected between      ││
│   │ Minimalist Design and ││
│   │ Spatial Theory.       ││
│   │ 4 new nodes           ││
│   └───────────────────────┘│
├─────┬──────┬────────┬──────┤
│📥   │ 🧠   │  💡    │ 🏔️  │
│INBOX│ MIND │INSIGHT │GROUND│
│     │ [绿] │        │      │
└─────┴──────┴────────┴──────┘
```

**图谱结构**:
- **Core Mind** (中心): 绿色圆形，白色大脑图标，代表用户的核心知识库
- **主题节点** (第一圈): 4-6 个大节点，如 Minimalist Design、Research Lab、Personal Notes
- **子节点** (第二圈): 更小的节点，从主题节点延伸
- **连线**: 节点间灰色细线，表示关联

**交互**:
- 双指缩放/平移图谱
- 点击节点 → 展开该主题下的笔记列表
- 长按节点 → 查看关联
- Synthesis Update 卡片 → 展示 AI 发现的新关联

**实现方案**:
- **iOS**: 使用 `GraphLayout` 或自定义 `UIView` + `UIPanGestureRecognizer` / `UIPinchGestureRecognizer`
- **Android**: 自定义 `Compose Canvas` + 手势检测

**需新增后端 API**:
- `GET /mind/graph` — 返回节点和边的图谱结构
- `GET /mind/nodes/{node_id}/notes` — 节点下的笔记列表
- `GET /mind/synthesis` — Synthesis Update 数据

---

### 3.5 Insight 页 — AI 洞察

**对应设计稿**: image 4

AI 根据用户的笔记内容，自动生成趋势分析和关联洞察。

**功能**:
- 趋势卡片列表（"你最近在关注空间设计..."）
- 关联推荐（"这篇笔记和上周的那篇有关联"）
- 知识空白提醒（"你对 XX 领域还没有记录"）

**需新增后端 API**:
- `GET /insights` — AI 生成的洞察列表
- `GET /insights/trends` — 趋势分析
- `POST /insights/generate` — 手动触发洞察生成

---

### 3.6 Ground 页 — 社交空间

**对应设计稿**: image 5, atélier_ Ground (Social).png

**功能**:
- 探索其他用户公开分享的笔记/知识碎片
- 关注用户
- 收藏/转发到自己的 Inbox

**需新增后端 API**:
- `GET /ground/feed` — 公开动态流
- `GET /ground/users/{id}` — 用户公开资料
- `POST /notes/{id}/share` — 分享笔记到 Ground
- `POST /ground/notes/{id}/like` — 点赞
- `GET /ground/explore` — 探索推荐

---

### 3.7 笔记详情页 (Note Detail)

**对应设计稿**: image 6

```
┌─────────────────────────────┐
│ ←  atelier              ••• │  ← 返回 + 更多菜单
├─────────────────────────────┤
│ 📁 Design Thinking         │  ← 文件夹位置
│ 🏷️ minimalism  spatial     │  ← 标签
│ 🕐 2h ago  ✅ Completed    │  ← 时间 + 状态
├─────────────────────────────┤
│                             │
│ # The Architecture of       │  ← Markdown 渲染
│ Silence                     │
│                             │
│ An interview with Peter     │
│ Zumthor exploring how       │
│ architectural space         │
│ influences human emotion.   │
│                             │
│ [image]                     │  ← 附件图片
│                             │
│ > "Space is the material   │  ← 引用块
│ > of architecture."         │
│                             │
├─────────────────────────────┤
│ Version 2 of 3  ↩ Restore  │  ← 版本信息
├─────┬──────┬────────┬──────┤
│📥   │ 🧠   │  💡    │ 🏔️  │
│INBOX│ MIND │INSIGHT │GROUND│
└─────┴──────┴────────┴──────┘
```

**API**:
- `GET /notes/{id}` — 笔记详情
- `GET /notes/{id}/versions` — 版本历史
- `POST /notes/{id}/versions/{v}/restore` — 版本回滚
- `PUT /notes/{id}` — 编辑元信息
- `GET /files/{id}` — 获取附件

---

### 3.8 快速采集 (Quick Capture)

点击 FAB "+" 弹出底部 Sheet：

```
┌─────────────────────────────┐
│ ────── (拖拽手柄) ──────     │
│                             │
│  Quick Capture              │
│                             │
│  📝 Text    🎤 Voice        │
│  📷 Photo   🎬 Video        │
│  📎 File    🔗 Link         │
│                             │
│  ┌───────────────────────┐  │
│  │ What's on your mind?  │  │  ← 文字输入框
│  └───────────────────────┘  │
│                             │
└─────────────────────────────┘
```

**采集类型 → 后端映射**:

| 前端入口 | 采集内容 | 后端处理 |
|---------|---------|---------|
| 📝 Text | 文字内容 | `POST /notes` content=text |
| 🎤 Voice | 音频文件 | `POST /files/upload` + `POST /notes` file=audio |
| 📷 Photo | 拍照/选图 | `POST /files/upload` + `POST /notes` file=image |
| 🎬 Video | 录像/选视频 | `POST /files/upload` + `POST /notes` file=video |
| 📎 File | 任意文件 | `POST /files/upload` + `POST /notes` file=file |
| 🔗 Link | URL | `POST /notes` content=url |

---

### 3.9 设置/个人页 (Settings)

**对应设计稿**: image 7

**功能**:
- 用户资料编辑
- 主题偏好（暗色模式预留）
- 存储/缓存管理
- 关于 atelier
- 退出登录

**API**:
- `GET /auth/me`
- `PUT /auth/profile` (需新增)

---

## 4. 后端 API 扩展清单

现有 FastAPI 后端需新增以下接口以支撑前端：

### 4.1 Mind 图谱 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/mind/graph` | 返回完整图谱（节点 + 边） |
| GET | `/mind/nodes/{node_id}/notes` | 节点下笔记列表 |
| GET | `/mind/synthesis` | AI 关联更新 |

### 4.2 Insight 洞察 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/insights` | AI 洞察列表 |
| GET | `/insights/trends` | 趋势分析 |
| POST | `/insights/generate` | 手动触发 AI 洞察 |

### 4.3 Ground 社交 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/ground/feed` | 公开动态流 |
| GET | `/ground/explore` | 探索推荐 |
| GET | `/ground/users/{id}` | 用户公开资料 |
| POST | `/notes/{id}/share` | 分享笔记到 Ground |
| POST | `/ground/notes/{id}/like` | 点赞 |
| DELETE | `/ground/notes/{id}/like` | 取消点赞 |

### 4.4 用户资料 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/auth/me` | 已有 |
| PUT | `/auth/profile` | 更新用户资料 |

### 4.5 现有 API 调整

| 调整项 | 说明 |
|--------|------|
| `GET /notes` 响应扩展 | 增加 `source_type` 标签、`thumbnail_url`、`location` 字段 |
| `POST /notes` 扩展 | 支持 `source` 字段（whatsapp/instagram/browser/manual） |
| 搜索增强 | `GET /search` 支持全文搜索 Markdown 内容 |
| WebSocket/SSE | AI 处理进度实时推送，替代前端轮询 |

---

## 5. 本地数据架构

### 5.1 本地缓存策略

```
┌─────────────────────────────────────┐
│         Local Storage               │
├─────────────────────────────────────┤
│ Keychain / Keystore                 │
│   ├── access_token                  │
│   └── refresh_token                 │
├─────────────────────────────────────┤
│ SQLite (Core Data / Room)           │
│   ├── notes (同步缓存)              │
│   ├── folders                       │
│   ├── tags                          │
│   ├── graph_nodes (图谱缓存)        │
│   └── pending_uploads (离线队列)     │
├─────────────────────────────────────┤
│ File System                         │
│   ├── cached_images/                │
│   ├── cached_thumbnails/            │
│   └── pending_uploads/              │
└─────────────────────────────────────┘
```

### 5.2 离线支持

- **离线创建**: 本地 SQLite 先存，网络恢复后同步
- **离线查看**: 已缓存笔记可离线浏览
- **队列上传**: pending_uploads 按顺序自动重试

---

## 6. 双端对照表

| 功能模块 | iOS 实现 | Android 实现 |
|---------|----------|-------------|
| UI 框架 | SwiftUI | Jetpack Compose |
| 网络层 | URLSession + async/await | Retrofit + Kotlin Coroutines |
| 本地数据库 | Core Data + SwiftData | Room |
| 安全存储 | Keychain | EncryptedSharedPreferences |
| 图片缓存 | Kingfisher / SDWebImage | Coil |
| Markdown 渲染 | MarkdownUI | Markwon |
| 图谱渲染 | 自定义 UIView + Gestures | Compose Canvas + Gestures |
| 音频录制 | AVFoundation | MediaRecorder |
| 相机 | AVFoundation | CameraX |
| 推送通知 | APNs | FCM |
| DI | SwiftUI Environment | Hilt / Koin |
| 状态管理 | @Observable / @State | ViewModel + StateFlow |

---

## 7. 开发阶段规划

### Phase 1: MVP 基础框架 (2-3 周)
- 项目骨架搭建（双端）
- 网络层 + 认证流程
- Inbox 页（笔记列表 + 详情）
- 快速采集（文字 + 文件上传）

### Phase 2: 核心功能 (2-3 周)
- Mind 知识图谱页
- 文件夹 + 标签管理
- 搜索功能
- 版本历史

### Phase 3: AI & 社交 (2-3 周)
- Insight AI 洞察页
- Ground 社交页
- 后端 API 扩展（Mind graph / Insight / Social）

### Phase 4: 打磨上线 (1-2 周)
- 离线支持
- 性能优化
- 动画打磨
- App Store / Google Play 提审
