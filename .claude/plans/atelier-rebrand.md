# atélier Product Rebrand Plan

## Goal
Transform the easystarter template into an independent note-taking product called "atélier" with its own landing page, branding, and app-specific navigation.

## Design Reference
Mockups at `/Users/lijixiang/Downloads/v0.1 (2)/` show:
- Dark theme, purple/blue gradient accents
- Landing: "Your Second Brain, Beautifully Organized" hero + 4 module cards + pricing
- App: TOP BAR navigation (Notes, Mind, Insights, Ground) — not sidebar
- Inbox: split-view (list + editor)
- Mind: graph visualization
- Ground: social feed cards

---

## Phase 1: Branding (app-config + logos)

### 1a. `packages/app-config/src/app-config.ts`
- `name`: "EasyStarter" → "atélier"
- `supportEmail`: → "support@atelier-app.com"
- `websiteUrl`: → "https://atelier-app.com"
- `socialUrl`: update
- `native.app.name`: → "atelier"
- `native.app.nativeScheme`: → "atelier"

### 1b. Logo
- Replace `apps/web/src/components/logos/png/logo.png` with atélier logo (placeholder for now — a simple text-based SVG)
- Update `apps/web/src/components/logos/logo.tsx` if needed

### 1c. Web config
- `apps/web/src/configs/web-config.ts` — landing page SEO title: remove "Full-stack TypeScript SaaS Template"
- `apps/web/src/routes/_public/(marketing)/(landing-page)/index.tsx` — update SEO title/description

---

## Phase 2: Landing Page

Replace the generic SaaS landing with atélier-specific content.

### 2a. i18n copy (`packages/i18n/src/messages/web/en.json`, zh.json, jp.json)
Rewrite these keys:
- `landingPage.hero.title` → "Your Second Brain, Beautifully Organized"
- `landingPage.hero.subtitle` → note-app description
- `landingPage.hero.announcement` → "AI-Powered Note Intelligence"
- `landingPage.footer.description` → atélier description
- `seo.home.description` → atélier SEO description

### 2b. Landing page sections
- `apps/web/src/configs/web-config.ts` — change `defaultLandingPageComponents` to use the tailark hero (which reads i18n) instead of `hero-section-23`
- Remove irrelevant sections (integrations, logo-cloud, stats, testimonials)
- Keep: hero, features, pricing, FAQs, call-to-action

### 2c. Footer
- `apps/web/src/components/layout/tailark/footer/footer.tsx` — replace hardcoded GitHub/Twitter social links

---

## Phase 3: Dashboard Navigation — Sidebar → Top Bar

This is the biggest structural change. The design shows a horizontal top nav with 4 tabs, not a sidebar.

### 3a. Create new `AppTopNav` component
- New file: `apps/web/src/components/dashboard/app-top-nav.tsx`
- Horizontal bar with: logo (left), 4 nav tabs (Notes, Mind, Insights, Ground — center), user menu + theme switch (right)
- Use existing shadcn Tabs or simple Link-based nav with active state
- Responsive: on mobile, collapse to hamburger or bottom tabs

### 3b. Rewrite `AuthenticatedLayout`
- `apps/web/src/components/dashboard/authed-layout.tsx`
- Remove `SidebarProvider`, `AppSidebar`, `SidebarInset`, `SidebarTrigger`
- Replace with: `AppTopNav` + full-width content area
- Keep `SearchProvider`, `LayoutProvider`

### 3c. Remove sidebar-specific files (or leave unused)
- `sidebar-data.ts` — no longer drives navigation
- `app-sidebar.tsx` — no longer rendered

### 3d. Remove irrelevant routes
- `routes/_authed/(dashboard)/dashboard/` — generic stats dashboard (template artifact)
- `routes/_authed/(dashboard)/users.tsx` — user management (template artifact)
- Make `/notes` the default authenticated route (redirect from `/dashboard` → `/notes`)

---

## Phase 4: Theme — Dark Mode with Purple Accents

### 4a. Add atélier theme preset
- `apps/web/src/configs/theme-presets.ts` — add an "atelier" preset as the first entry (becomes default)
- Dark background: ~#0a0a0f
- Primary/accent: purple #7c3aed / blue #3b82f6
- Cards: dark surface #1a1a2e
- Text: white/gray

### 4b. Set dark as default
- `apps/web/src/configs/theme-config.ts` — default theme mode to "dark"

---

## Phase 5: Native App Branding

### 5a. `apps/native/app.json`
- `name`: → "atélier"
- `slug`: → "atelier"
- `scheme`: → "atelier"

### 5b. Onboarding copy
- Update i18n keys for `onboarding.page1/2/3` to reflect atélier's 4 modules

### 5c. Tab labels already correct
- Native tabs are: Notes, Insights, Mind, Ground, Profile — already aligned

---

## Execution Order

1. **Phase 1** (branding) — quick, propagates everywhere
2. **Phase 4** (theme) — visual impact, sets the mood
3. **Phase 2** (landing page) — first thing users see
4. **Phase 3** (top nav) — biggest change, do last on web
5. **Phase 5** (native) — separate, can be parallel

## Files Modified (summary)

| File | Change |
|------|--------|
| `packages/app-config/src/app-config.ts` | Rename to atélier |
| `apps/web/src/components/logos/png/logo.png` | Replace logo |
| `apps/web/src/configs/web-config.ts` | Landing sections, SEO |
| `apps/web/src/configs/theme-presets.ts` | Add atelier dark theme |
| `packages/i18n/src/messages/web/en.json` | Landing copy |
| `packages/i18n/src/messages/web/zh.json` | Landing copy (zh) |
| `packages/i18n/src/messages/web/jp.json` | Landing copy (jp) |
| `apps/web/src/components/dashboard/app-top-nav.tsx` | NEW — top navigation |
| `apps/web/src/components/dashboard/authed-layout.tsx` | Sidebar → top nav |
| `apps/web/src/components/dashboard/site-header.tsx` | Simplify or remove |
| `apps/web/src/routes/_public/.../index.tsx` | SEO title |
| `apps/web/src/components/layout/tailark/footer/footer.tsx` | Social links |
| `apps/native/app.json` | App name/scheme |
