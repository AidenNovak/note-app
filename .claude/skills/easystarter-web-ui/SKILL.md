# EasyStarter Web UI（统一工作流）

把 web UI 相关的碎片 skill 合并为一个“端到端工作流”，用于在本仓库的 web 端（`easystarter/apps/web`）快速、稳定地产出页面与组件。

覆盖范围：
- shadcn/ui 组件使用与新增（以组合为主）
- Dashboard 列表页（分页/排序/搜索的数据表格）
- 表单页（TanStack Form + Zod 校验 + mutation）

## 1) 基本原则（先组合，后新增）

- 先找现成组件：优先复用 `apps/web/src/components/ui/*`
- 先组合再定制：页面 = Card + FieldGroup + Table + Dialog/Sheet/Drawer 的组合，不要先写自定义 div 布局
- 只在必要时新增组件：并沿用项目既有目录结构与约定

## 2) shadcn/ui（组件策略）

推荐路径：
- 先确认项目是否已有该组件（目录：`apps/web/src/components/ui/`）
- 需要新增时，使用项目包管理器的 runner 执行 shadcn CLI，在 `apps/web` 目录下完成组件落地

约束（保持一致性）：
- 用语义 token（`bg-background`、`text-muted-foreground` 等），避免硬编码色值
- 用 `gap-*` 控制间距，避免 `space-x/space-y`
- `Dialog/Sheet/Drawer` 必须有 Title（可用 `sr-only` 隐藏但不可缺）

## 3) Dashboard 表格页（data table 工作流）

目标：实现“服务端分页/排序/搜索 + 表格组件拆分 + Query 缓存策略”的标准形态。

目录结构建议（每个 resource 一套）：
- `apps/web/src/routes/_authed/(dashboard)/{resource}.tsx`
- `apps/web/src/components/dashboard/{resource}/`（table、container、hook、types、skeleton 等）

要点：
- queryKey 需要包含分页/搜索/排序参数
- 使用 `placeholderData: keepPreviousData` 让分页切换更平滑
- 排序字段通过 enum/映射表收敛（避免随意字符串）

## 4) 表单页（TanStack Form + Zod 工作流）

目标：实现“统一字段布局 + 提交校验 + mutation + 错误处理 + i18n”的标准形态。

推荐结构：
- 路由文件：`apps/web/src/routes/_authed/(dashboard)/...`
- UI：`Card` + `FieldGroup` + `Field`（避免散落的 label/input）

要点：
- validators 以 `z.object({...})` 为入口，错误信息走 i18n key
- 提交成功后要 `invalidateQueries()` 或按资源粒度刷新
- 错误提示以 toast 为主，避免页面静默失败

## 5) vendor 来源（只引用，不拆分）

本 skill 合并了以下 vendor skill 的要点；原始文档仍在 easystarter 子仓库下：
- `/easystarter/.claude/skills/shadcn/`
- `/easystarter/.claude/skills/easystarter-component/`
- `/easystarter/.claude/skills/easystarter-form-page/`
- `/easystarter/.claude/skills/easystarter-data-table/`

