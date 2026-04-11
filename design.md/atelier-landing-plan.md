# atélier Landing Page & Product Web 整体设计方案

## 品牌定义

- **名称**: atélier
- **Tagline**: Your Second Digital Mind
- **Sub**: A Space for Quiet Reflection
- **EST.**: MMXXIV (2024)
- **气质**: 极简、自然、沉思、有机

---

## 色彩体系 (来自设计稿)

| Token | Light Mode | 用途 |
|-------|-----------|------|
| primary | `#2d5a3d` | 主按钮、导航高亮、标题 |
| primary-hover | `#3d6b4f` | 按钮 hover |
| accent | `#c8e64a` | CTA 高亮、徽章、标签背景 |
| background | `#fafaf5` | 页面底色（米白/奶油色） |
| card | `#ffffff` | 卡片背景 |
| card-border | `#e2e8d9` | 卡片描边 |
| foreground | `#1a2e1a` | 主文字 |
| muted | `#6b7264` | 次要文字 |
| muted-bg | `#f5f5f0` | 灰色区块 |
| tag-green | `#d4f5c8` | WHATSAPP/QUICK NOTE 标签 |
| tag-yellow | `#fef5c8` | 高亮引用背景 |

---

## Landing Page 页面结构

### Section 1: Hero (全屏)

**参考**: `Body.png`

```
┌─────────────────────────────────────────┐
│           [书本 icon - 圆形]              │
│                                         │
│            atélier                       │
│    YOUR SECOND DIGITAL MIND             │
│                                         │
│         ── EST. MMXXIV ──               │
│                                         │
│      [ ENTER MIND →  ] (绿色圆角按钮)     │
│                                         │
│     A Space for Quiet Reflection        │
└─────────────────────────────────────────┘
```

- 背景: 渐变米白，右上角有浅色水墨/和平符号装饰
- 字体: atélier 用大号衬线体，其余用 tracking-widest 的小写大写字母
- CTA 按钮: `#2d5a3d` 背景, 白字, 大圆角 (pill shape)
- 动效: 淡入 + 轻微上浮

### Section 2: 产品概览 (What is atélier?)

```
┌─────────────────────────────────────────┐
│  A digital sanctuary where your         │
│  scattered thoughts become connected    │
│  wisdom. Four spaces, one mind.         │
│                                         │
│  [Inbox] [Mind] [Insight] [Ground]      │
│   四个圆形 icon，hover 时展开描述          │
└─────────────────────────────────────────┘
```

### Section 3: 四大模块详细展示

每个模块占一整个 viewport 区域，左文右图（交替布局）。

#### 3a. Inbox — Collected Fragments
**参考**: `image 1.png`, `image 4.png`

```
┌──────────────────┬──────────────────────┐
│ COLLECTED        │                      │
│ FRAGMENTS        │  [image 1 截图]       │
│                  │  手机端 Inbox 界面     │
│ Bits of          │                      │
│ inspiration      │                      │
│ waiting to be    │                      │
│ woven into       │                      │
│ your story.      │                      │
│                  │                      │
│ 特点:             │                      │
│ · 多源捕获         │                      │
│   (WhatsApp,     │                      │
│    Instagram,    │                      │
│    Browser...)   │                      │
│ · AI 自动标签      │                      │
│ · 快速笔记         │                      │
└──────────────────┴──────────────────────┘
```

#### 3b. Mind — Neural Synthesis
**参考**: `image 3.png`, `image 5.png`

```
┌──────────────────────┬──────────────────┐
│                      │ NEURAL           │
│  [image 5 截图]       │ SYNTHESIS        │
│  桌面端 Mind 界面     │                  │
│  知识图谱可视化        │ Deep Mapping     │
│                      │ Mode             │
│                      │                  │
│                      │ 特点:             │
│                      │ · 思维网络可视化    │
│                      │ · AI 发现连接     │
│                      │ · Synthesis      │
│                      │   Snippets       │
└──────────────────────┴──────────────────┘
```

#### 3c. Insight — Analytical Mirror
**参考**: `image 2.png`, `image 6.png`

```
┌──────────────────┬──────────────────────┐
│ ANALYTICAL       │                      │
│ MIRROR           │  [image 2 截图]       │
│                  │  Insight 分析界面     │
│ Adjust how the   │                      │
│ atélier perceives│                      │
│ your patterns.   │                      │
│                  │                      │
│ 特点:             │                      │
│ · 能量波形分析      │                      │
│ · 创意输出追踪      │                      │
│ · 版本化洞察        │                      │
└──────────────────┴──────────────────────┘
```

#### 3d. Ground — Shared Sanctuary
**参考**: `atélier_ Ground (Social).png`, `image 7.png`

```
┌──────────────────────┬──────────────────┐
│                      │ SHARED           │
│  [Ground 截图]        │ SANCTUARY        │
│  社区内容流           │                  │
│                      │ A sanctuary for  │
│                      │ collective       │
│                      │ resonance.       │
│                      │                  │
│                      │ 特点:             │
│                      │ · 洞察分享         │
│                      │ · 集体智慧         │
│                      │ · 策展化内容       │
└──────────────────────┴──────────────────┘
```

### Section 4: 产品理念 (Philosophy)

```
┌─────────────────────────────────────────┐
│        "Wisdom is the shared            │
│         architecture of our             │
│         collective quiet."              │
│                                         │
│  参考 Ground 截图底部的 CURATED INSIGHT    │
│  用大号衬线体，居中，米黄背景              │
└─────────────────────────────────────────┘
```

### Section 5: Pricing (复用已有 tailark-pricing)

保留现有定价组件，仅更新配色和文案。

### Section 6: CTA (底部)

```
┌─────────────────────────────────────────┐
│        Ready to enter your mind?        │
│                                         │
│       [ START YOUR JOURNEY →  ]         │
│                                         │
│     Free to begin. No credit card.      │
└─────────────────────────────────────────┘
```

### Section 7: Footer

```
┌─────────────────────────────────────────┐
│ atélier                                 │
│ © 2024 ATÉLIER. YOUR SECOND DIGITAL MIND│
│                                         │
│ PHILOSOPHY  PRIVACY  TERMS  ATÉLIER JOURNAL │
└─────────────────────────────────────────┘
```

---

## 实现方案

### 方案: 自定义 Landing Page 组件

**替换** `dynamic-landing-page.tsx`，不再使用可组合模板系统，直接渲染 atélier 专属页面。

#### 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `components/landing-page/atelier-landing.tsx` | **新建** | 完整的 atélier landing page |
| `components/landing-page/dynamic-landing-page.tsx` | **改写** | 直接渲染 `<AtelierLanding />` |
| `configs/theme-presets.ts` | **已改** | 绿色主题 ✅ |
| `configs/theme-config.ts` | **已改** | light 默认 ✅ |
| `configs/web-config.ts` | 无需改 | defaultLandingPageComponents 不再使用 |
| `packages/i18n/src/messages/web/en.json` | **改写** | landing 文案 |
| `packages/i18n/src/messages/web/zh.json` | **改写** | landing 文案 |
| `packages/i18n/src/messages/web/jp.json` | **改写** | landing 文案 |
| `public/images/` | **新建** | 放设计稿截图作为 feature 展示图 |
| `components/layout/tailark/footer/footer.tsx` | **改写** | atélier footer |
| `routes/_public/(marketing)/(landing-page)/index.tsx` | **微调** | SEO meta |

#### atelier-landing.tsx 结构

```tsx
export function AtelierLanding() {
  return (
    <>
      <HeroSection />        {/* 全屏 hero, Body.png 风格 */}
      <OverviewSection />     {/* 四模块概览 */}
      <InboxSection />        {/* Collected Fragments */}
      <MindSection />         {/* Neural Synthesis */}
      <InsightSection />      {/* Analytical Mirror */}
      <GroundSection />       {/* Shared Sanctuary */}
      <PhilosophySection />   {/* 引用/理念 */}
      <PricingSection />      {/* 复用 tailark-pricing */}
      <CTASection />          {/* 底部 CTA */}
    </>
  );
}
```

每个 Section 都是独立的函数组件，样式用 Tailwind CSS。

#### 设计稿图片使用

将 `/Users/lijixiang/Downloads/v0.1 (2)/` 中的截图复制到 `apps/web/public/images/atelier/`:
- `inbox-mobile.png` ← image 1.png
- `inbox-desktop.png` ← image 4.png
- `mind-mobile.png` ← image 3.png
- `mind-desktop.png` ← image 5.png
- `insight-mobile.png` ← image 2.png
- `ground-mobile.png` ← atélier_ Ground (Social).png

这些图用在模块展示区域，作为产品截图。

---

## 导航栏 (已改好)

Web 端已从侧边栏改为顶部导航 (`app-top-nav.tsx`):
- Logo + "atélier" (左)
- Notes / Insights / Mind / Ground (中)
- Search + Theme + User Menu (右)
- 移动端: 汉堡菜单

---

## 后续阶段 (本次不做)

1. **Auth 桥接**: Better Auth → 后端 JWT 自动同步
2. **后端连接**: 配置 .env 连 localhost:8000
3. **部署**: 前端 Vercel/Cloudflare Pages, 后端 Vercel
4. **Native**: app.json 已更新, onboarding 文案待改

---

## 时间估算

| 任务 | 工作量 |
|------|--------|
| atelier-landing.tsx (hero + 4模块 + philosophy + CTA) | 主体 |
| 复制/优化截图 | 小 |
| i18n 文案 | 小 |
| footer 改写 | 小 |
| 调试/微调样式 | 中 |
