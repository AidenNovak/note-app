# Repository Layout

`note-app/` 现在承载的是一个工作区，不是单一项目。根目录只保留入口文件和项目目录，减少“文档、代码、临时产物”混放。

## 顶层目录

| 路径 | 用途 | 是否主代码 |
|---|---|---|
| `backend/` | FastAPI 后端、Alembic、测试、后端脚本 | 是 |
| `easystarter/` | Native App monorepo，实际 iOS 产品代码 | 是 |
| `cli/` | `atelier` CLI | 是 |
| `jilly/` | Landing / legal site，独立项目 | 是 |
| `_local/` | 本地截图、导出文件、临时归档，已 gitignore | 否 |
| `docs/` | 仓库级说明文档 | 否 |
| `design-docs/` | 产品设计文档、评审稿、原型 | 否 |

## 根目录文件

- `README.md`：项目总入口，适合第一次进入仓库时先看
- `AGENTS.md`：给自动化 agent 的仓库规则
- `Makefile`：最常用的本地开发命令入口
- `package.json`：把根目录命令映射到 `make`
- `.gitignore`：统一说明哪些目录是本地产物，不参与版本管理

`_local/` 下面当前统一放三类内容：

- `_local/screenshots/`：本地截图
- `_local/output/`：导出文件、提交材料、生成物
- `_local/archive/`：历史遗留和临时归档

## 放置规则

- 新的业务代码放进对应项目里，不要直接放在仓库根目录。
- 仓库级操作文档放进 `docs/`。
- 产品设计、信息架构、视觉方案放进 `design-docs/`。
- 截图、导出文件、临时材料统一放进 `_local/`，不要把这些内容塞进代码目录。
- 如果某个文件只服务于 `backend/` 或 `easystarter/`，优先放进对应子项目内部，而不是顶层。

## 判断标准

当你不确定一个新文件应该放哪里时，先问两个问题：

1. 这是运行时代码，还是说明/产物？
2. 它属于整个工作区，还是只属于某一个子项目？

如果答案是“说明 + 整个工作区”，就放 `docs/`。如果答案是“代码 + 某个子项目”，就放进对应子项目。
