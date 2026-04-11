# 笔记软件后端接口设计文档

## 项目概述

多模态笔记采集 + AI 归一化处理系统。用户可随时随地通过文字、语音、视频、文件等方式记录信息，系统通过云端 AI 管线将所有内容转化为 Markdown + 图片的统一格式。

## 技术决策

| 维度 | 选择 |
|------|------|
| 后端框架 | Python (FastAPI) |
| 数据库 | SQLite / PostgreSQL |
| 文件存储 | 本地磁盘 |
| AI 处理 | 调用外部 AI API（Claude 等） |
| 用户模型 | 多用户注册，数据隔离 |
| 客户端 | 纯后端 REST API |

---

## 一、认证模块 `/auth`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/auth/register` | 用户注册 |
| POST | `/auth/login` | 用户登录，返回 access_token + refresh_token |
| POST | `/auth/refresh` | 刷新 access_token |

### 1.1 用户注册 `POST /auth/register`

**请求体：**
```json
{
  "username": "string",
  "email": "string",
  "password": "string"
}
```

**响应 `201`：**
```json
{
  "id": "string",
  "username": "string",
  "email": "string",
  "created_at": "datetime"
}
```

### 1.2 用户登录 `POST /auth/login`

**请求体：**
```json
{
  "email": "string",
  "password": "string"
}
```

**响应 `200`：**
```json
{
  "access_token": "string",
  "refresh_token": "string",
  "token_type": "bearer",
  "expires_in": 3600
}
```

### 1.3 刷新令牌 `POST /auth/refresh`

**请求体：**
```json
{
  "refresh_token": "string"
}
```

**响应 `200`：**
```json
{
  "access_token": "string",
  "token_type": "bearer",
  "expires_in": 3600
}
```

---

## 二、笔记模块 `/notes`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/notes` | 创建笔记（上传原始内容） |
| GET | `/notes` | 笔记列表（分页、筛选、搜索） |
| GET | `/notes/{id}` | 笔记详情（含 Markdown 结果） |
| PUT | `/notes/{id}` | 更新笔记元信息（标题、标签、分类） |
| DELETE | `/notes/{id}` | 删除笔记 |

### 2.1 创建笔记 `POST /notes`

**请求体（multipart/form-data）：**
```
title: string（可选，默认从内容提取）
content: string（文字内容，与 file 二选一）
file: file（上传的原始文件，与 content 二选一）
folder_id: string（所属文件夹 ID，可选）
tags: string[]（标签列表，可选）
```

**响应 `201`：**
```json
{
  "id": "string",
  "title": "string",
  "status": "pending",
  "created_at": "datetime",
  "task_id": "string"
}
```

> 创建后自动生成 AI 处理任务，通过 `task_id` 追踪转换进度。

### 2.2 笔记列表 `GET /notes`

**查询参数：**
| 参数 | 类型 | 说明 |
|------|------|------|
| page | int | 页码，默认 1 |
| page_size | int | 每页条数，默认 20 |
| folder_id | string | 按文件夹筛选 |
| tag | string | 按标签筛选 |
| status | string | 按状态筛选（pending / processing / completed / failed） |
| keyword | string | 标题关键词搜索 |
| sort_by | string | 排序字段（created_at / updated_at / title），默认 created_at |
| order | string | asc / desc，默认 desc |

**响应 `200`：**
```json
{
  "total": 100,
  "page": 1,
  "page_size": 20,
  "items": [
    {
      "id": "string",
      "title": "string",
      "status": "completed",
      "folder_id": "string",
      "tags": ["tag1", "tag2"],
      "created_at": "datetime",
      "updated_at": "datetime"
    }
  ]
}
```

### 2.3 笔记详情 `GET /notes/{id}`

**响应 `200`：**
```json
{
  "id": "string",
  "title": "string",
  "status": "completed",
  "markdown_content": "string",
  "attachments": [
    {
      "id": "string",
      "type": "image",
      "url": "/files/{id}",
      "filename": "string"
    }
  ],
  "folder_id": "string",
  "tags": ["tag1", "tag2"],
  "source_type": "voice | video | text | file",
  "source_file_id": "string | null",
  "current_version": 3,
  "created_at": "datetime",
  "updated_at": "datetime"
}
```

### 2.4 更新笔记 `PUT /notes/{id}`

**请求体：**
```json
{
  "title": "string（可选）",
  "folder_id": "string（可选）",
  "tags": ["string（可选）"]
}
```

**响应 `200`：** 返回更新后的笔记对象。

### 2.5 删除笔记 `DELETE /notes/{id}`

**响应 `204`：** 无内容。

---

## 三、文件/附件模块 `/files`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/files/upload` | 上传原始文件 |
| GET | `/files/{id}` | 下载/获取文件 |
| DELETE | `/files/{id}` | 删除文件 |

### 3.1 上传文件 `POST /files/upload`

**请求体（multipart/form-data）：**
```
file: file（必填）
note_id: string（关联笔记 ID，可选）
```

**响应 `201`：**
```json
{
  "id": "string",
  "filename": "string",
  "mime_type": "string",
  "size": 1024,
  "url": "/files/{id}",
  "created_at": "datetime"
}
```

### 3.2 获取文件 `GET /files/{id}`

**响应 `200`：** 返回文件二进制流。

### 3.3 删除文件 `DELETE /files/{id}`

**响应 `204`：** 无内容。

---

## 四、处理任务模块 `/tasks`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/tasks` | 查看所有处理任务 |
| GET | `/tasks/{id}` | 单个任务详情（进度、错误信息） |
| POST | `/tasks/{id}/retry` | 失败重试 |

### 4.1 任务列表 `GET /tasks`

**查询参数：**
| 参数 | 类型 | 说明 |
|------|------|------|
| status | string | pending / processing / completed / failed |
| page | int | 页码 |
| page_size | int | 每页条数 |

**响应 `200`：**
```json
{
  "total": 50,
  "page": 1,
  "page_size": 20,
  "items": [
    {
      "id": "string",
      "note_id": "string",
      "type": "voice_to_text | video_to_frames | file_to_markdown | text_to_markdown",
      "status": "processing",
      "progress": 0.6,
      "created_at": "datetime",
      "updated_at": "datetime"
    }
  ]
}
```

### 4.2 任务详情 `GET /tasks/{id}`

**响应 `200`：**
```json
{
  "id": "string",
  "note_id": "string",
  "type": "video_to_frames",
  "status": "completed",
  "progress": 1.0,
  "error": null,
  "input_file_id": "string",
  "output": {
    "markdown_file_id": "string",
    "attachment_file_ids": ["string"]
  },
  "created_at": "datetime",
  "updated_at": "datetime",
  "completed_at": "datetime"
}
```

### 4.3 重试任务 `POST /tasks/{id}/retry`

**响应 `200`：**
```json
{
  "id": "string",
  "status": "pending",
  "message": "Task retried"
}
```

---

## 五、分类组织模块

### 5.1 文件夹 `/folders`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/folders` | 文件夹列表（树形结构） |
| POST | `/folders` | 创建文件夹 |
| PUT | `/folders/{id}` | 重命名 / 移动 |
| DELETE | `/folders/{id}` | 删除文件夹 |

#### 创建文件夹 `POST /folders`

```json
{
  "name": "string",
  "parent_id": "string | null"
}
```

#### 更新文件夹 `PUT /folders/{id}`

```json
{
  "name": "string（可选）",
  "parent_id": "string（可选，移动到其他父文件夹）"
}
```

### 5.2 标签 `/tags`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/tags` | 标签列表 |
| POST | `/notes/{id}/tags` | 给笔记添加标签 |
| DELETE | `/notes/{id}/tags/{tag}` | 移除笔记标签 |

#### 添加标签 `POST /notes/{id}/tags`

```json
{
  "tags": ["tag1", "tag2"]
}
```

**响应 `200`：** 返回笔记当前所有标签。

---

## 六、搜索模块 `/search`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/search` | 全文搜索 |
| GET | `/search/suggest` | 搜索建议（自动补全） |

### 6.1 全文搜索 `GET /search`

**查询参数：**
| 参数 | 类型 | 说明 |
|------|------|------|
| q | string | 搜索关键词 |
| type | string | all / note / file，默认 all |
| folder_id | string | 限定文件夹范围 |
| tag | string | 限定标签 |
| date_from | datetime | 起始日期 |
| date_to | datetime | 截止日期 |
| page | int | 页码 |
| page_size | int | 每页条数 |

**响应 `200`：**
```json
{
  "total": 15,
  "page": 1,
  "page_size": 20,
  "items": [
    {
      "id": "string",
      "type": "note",
      "title": "string",
      "highlight": "...匹配关键词高亮片段...",
      "created_at": "datetime"
    }
  ]
}
```

### 6.2 搜索建议 `GET /search/suggest`

**查询参数：**
| 参数 | 类型 | 说明 |
|------|------|------|
| q | string | 前缀关键词 |
| limit | int | 返回条数，默认 10 |

**响应 `200`：**
```json
{
  "suggestions": ["keyword1", "keyword2", "keyword3"]
}
```

---

## 七、版本管理模块 `/notes/{id}/versions`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/notes/{id}/versions` | 版本历史列表 |
| GET | `/notes/{id}/versions/{v}` | 某版本内容 |
| POST | `/notes/{id}/versions/{v}/restore` | 回滚到某版本 |

### 7.1 版本历史 `GET /notes/{id}/versions`

**响应 `200`：**
```json
{
  "note_id": "string",
  "versions": [
    {
      "version": 3,
      "summary": "AI 转换完成（视频→Markdown）",
      "created_at": "datetime"
    },
    {
      "version": 2,
      "summary": "AI 转换完成（修正图片引用）",
      "created_at": "datetime"
    },
    {
      "version": 1,
      "summary": "原始上传",
      "created_at": "datetime"
    }
  ]
}
```

### 7.2 版本内容 `GET /notes/{id}/versions/{v}`

**响应 `200`：** 返回该版本的完整笔记详情（同 2.3 结构）。

### 7.3 回滚版本 `POST /notes/{id}/versions/{v}/restore`

**响应 `200`：** 返回当前（回滚后的）笔记详情。

---

## 通用约定

### 认证方式

所有接口（除 `/auth/register` 和 `/auth/login` 外）均需在请求头携带：
```
Authorization: Bearer <access_token>
```

### 错误响应格式

```json
{
  "error": {
    "code": "NOTE_NOT_FOUND",
    "message": "Note with id xxx not found"
  }
}
```

### 常见状态码

| 状态码 | 含义 |
|--------|------|
| 200 | 成功 |
| 201 | 创建成功 |
| 204 | 删除成功（无返回体） |
| 400 | 请求参数错误 |
| 401 | 未认证 |
| 403 | 无权限 |
| 404 | 资源不存在 |
| 422 | 请求体验证失败 |
| 500 | 服务器内部错误 |

### 笔记状态流转

```
pending → processing → completed
                    → failed → (retry) → pending
```

### 文件大小限制

- 单文件上传上限：500MB
- 请求体总大小上限：600MB
