
# 笔记数据库访问

本 skill 允许 Agent 与 note-app 后端数据库交互。

## 工具

### `db_query`
直接查询 SQLite 数据库。

- **用法**: `db_query(sql: string)`
- **说明**: 在笔记数据库上执行原始 SQL 查询。可用于查找笔记、标签或文件夹结构。

### `get_note_content`
通过 ID 读取笔记的完整内容。

- **用法**: `get_note_content(note_id: string)`
- **说明**: 返回指定笔记的 markdown 内容。

## 实现（概念性）
Agent SDK 将通过其内置的工具执行机制调用这些工具。
