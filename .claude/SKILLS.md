# Skills 总览（本仓库）

本仓库 skill 的“物理文件”分布在两处，但统一按一个目录策略管理（减少模块数量与重复）：

- `/.claude/skills/*`：主仓库可维护技能集（新增/合并/废弃都在这里完成）
- `/easystarter/.claude/skills/*`：模板自带（vendor），原则上不再新增/拆分，只在主仓库做引用与规则收口

统一管理入口：
- `/.claude/skills/CATALOG.yml`：技能登记与生命周期（active/incubating/deprecated/vendor）
- `/.claude/skills/README.md`：技能管理规范

这些 skill 主要用于给 Agent 提供“何时用/怎么做/质量标准”的约束与流程。

## 1) note-app（根目录）技能

### note-app-db
- 位置：`/.claude/skills/note-app-db/`
- 目的：直接与 note-app 后端数据库交互（查询、读 note 内容）
- 适用场景：需要从 SQLite 里定位某类笔记、标签/文件关系、或按 note_id 拉全文

### insights
- 位置：`/.claude/skills/insights/`
- 目的：把 retrieval/evidence/card/review 合并为一个端到端工作流（减少碎片模块）
- 适用场景：insight 全流程（选材→证据→卡片/报告→发布前审核）

## 2) easystarter（Web/Server/Native 相关技能）

### easystarter-web-ui
- 位置：`/.claude/skills/easystarter-web-ui/`
- 目的：把 shadcn + component + form-page + data-table 合并为一个 web UI 工作流（减少碎片模块）
- 适用场景：web 端组件新增/组合、dashboard 列表页、表单页

### easystarter-api-route
- 位置：`/easystarter/.claude/skills/easystarter-api-route/`
- 目的：生成 oRPC 路由（list/get/create/update/delete）与输入输出 schema 约定
- 适用场景：新增 server API、标准 CRUD 资源

### easystarter-db-schema
- 位置：`/easystarter/.claude/skills/easystarter-db-schema/`
- 目的：生成 Drizzle（D1/SQLite）schema 文件 + 迁移/推送流程
- 适用场景：新增 D1 表、补字段/索引/外键

### easystarter-i18n
- 位置：`/easystarter/.claude/skills/easystarter-i18n/`
- 目的：为 web i18n（en/zh/jp）补齐翻译键值与组织方式
- 适用场景：新增文案、补多语言

注：旧的 web UI skill 已清理，统一由 `easystarter-web-ui` 承接。

### heroui-native
- 位置：`/easystarter/.claude/skills/heroui-native/`
- 目的：HeroUI Native（Uniwind + RN）组件使用规范与获取 docs 的方法
- 适用场景：Expo/React Native 里使用 `heroui-native` 组件与主题

### building-native-ui
- 位置：`/easystarter/.claude/skills/building-native-ui/`
- 目的：Expo Router 原生 UI 的通用规范与设计/交互/路由约束（偏“指南”）
- 适用场景：做原生 UI、路由结构、动画、表单弹层、tabs 等

### upgrading-expo
- 位置：`/easystarter/.claude/skills/upgrading-expo/`
- 目的：Expo SDK 升级路径与破坏性变更清单
- 适用场景：升级 Expo、排依赖冲突、New Architecture 迁移等

### migrate-nativewind-to-uniwind
- 位置：`/easystarter/.claude/skills/migrate-nativewind-to-uniwind/`
- 目的：从 NativeWind 迁移到 Uniwind 的操作清单（Tailwind v4、metro/babel/css 等）
- 适用场景：替换 NativeWind、处理 cssInterop 相关改造

### vercel-react-native-skills
- 位置：`/easystarter/.claude/skills/vercel-react-native-skills/`
- 目的：React Native/Expo 的性能与工程最佳实践（列表/动画/导航/monorepo）
- 适用场景：性能优化、FlashList、Reanimated、RN 工程结构
