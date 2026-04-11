# Skills 管理规范

目标：把 skill 当作“可维护的工程资产”，避免数量膨胀、重复与失配，确保团队知道哪些是主线、哪些是参考。

## 单一事实来源（Single Source of Truth）

- 统一登记：`/.claude/skills/CATALOG.yml`
- 允许存在“物理位置分散”（例如 easystarter 子仓库自带技能），但必须在 catalog 里标注 `vendor` 或 `deprecated`，并且不要让它们成为默认入口。

## 生命周期

每个 skill 必须有 `status`：

- `active`：主线可用，默认推荐
- `incubating`：试用阶段，允许频繁调整
- `deprecated`：已被替代，仅保留兼容/参考；必须指向替代 skill
- `vendor`：外部/模板自带技能，原则上不改动正文，仅做引用与约束补充

## 命名与边界

- skill 目录名使用小写 kebab-case（例如 `insights`、`note-app-db`）
- 一个 skill 只解决一个“工作流闭环”，不要拆成多个碎片文件（例如 “检索/证据/卡片/审核”应归到同一工作流 skill）
- UI 类指南优先合并成“平台/栈级” skill（例如 `native-ui`），避免按组件库碎片化

## 新增/合并原则

- 新增之前先在 `CATALOG.yml` 搜索是否已有等价能力；若有，优先补齐现有 skill
- 当出现 2 个以上高度重叠的 skill：合并为 1 个 `active`，其余标记 `deprecated`
- `vendor` skill 只在必要时补充“本仓库差异点”，不复制整份内容到主仓库

## 目录策略（推荐）

- `/.claude/skills/*`：主仓库“可维护技能集”（active/incubating/deprecated）
- `/easystarter/.claude/skills/*`：模板自带（vendor）

