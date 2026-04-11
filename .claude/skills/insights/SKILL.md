# Insights（单 Agent 人文洞察模式，single-agent-v1 / ai-sdk-v1）

本 skill 定义 note-app 的 insight 生成工作流。支持两种实现方式：
- `single-agent-v1`: 使用 Claude Agent SDK（默认）
- `ai-sdk-v1`: 使用 Vercel AI SDK（推荐，支持多 provider）

## 设计理念

- 不是数据分析，而是"读懂一个人"——从笔记中编织出个人理念、行为准则、擅长领域和可能性
- 找笔记间的深层联系，不是表面主题分类
- 洞察作者真正擅长什么、在意什么、可能忽略了什么
- 行动建议像了解你的朋友给的建议——具体、温暖、可执行
- 语言温暖有洞见，不空洞不说教

## 架构

单 agent（`insight-analyst`）单次调用，用一个精心设计的中文 prompt 完成：
- 笔记阅读与理解
- 深层联系发现
- 一篇深度洞察报告
- 分享卡片生成

每次调用只生成 1 篇报告，把一个发现写深写透。如需多篇洞察，由后端多次调度。

### 实现文件

| 组件 | 文件 |
|------|------|
| Claude Agent SDK 脚本 | `/scripts/claude_insight_agent.mjs` |
| **AI SDK 脚本（新）** | `/scripts/ai_sdk_insight_agent.mjs` |
| Profile 配置 | `backend/app/intelligence/insights/profiles.py` |
| Agent 运行器 | `backend/app/intelligence/insights/agent.py` |
| 落库与 schema | `backend/app/intelligence/insights/service.py`、`backend/app/schemas.py` |

### 支持的 AI Provider（AI SDK 版本）

| Provider | 配置值 | 说明 |
|----------|--------|------|
| OpenAI | `openai` | 默认，支持 gpt-4o, gpt-4o-mini 等 |
| Anthropic | `anthropic` | 支持 claude-3-opus, claude-3-sonnet 等 |
| Google | `google` | 支持 gemini-1.5-pro 等 |
| OpenRouter | `openrouter` | 统一接口，支持多种模型 |

## 产出契约

每次调用产出两个层次的内容：

### 报告（report_markdown）— 在 app 内阅读的主体

一篇 1500-2500 字的深度分析文章，结构：
- `## 为什么重要`（3-5 段，场景开头 → 深层分析 → 代价）
- `## 证据`（至少 3 条，每条含原文引用 + 100-200 字分析）
- `## 建议的下一步`（2-3 个建议，从易到难，语气像朋友）

写作风格：像写给聪明朋友的私人信件，用具体细节，敢指出矛盾，让人有「被看见了」的感觉。

### 分享卡片（share_card → PNG）— 报告的可视化封面

报告的浓缩版，用于社交分享。包含标题、3-5 句摘要、一段原文引用、一个行动建议。
渲染为 1200px 宽的杂志质感 PNG（暖纸色背景 + 噪点纹理 + accent bar）。

### JSON 形状

```json
{
  "summary": "一句话生成摘要",
  "reports": [
    {
      "type": "trend | connection | gap | opportunity",
      "status": "published",
      "title": "简短标题",
      "description": "3-5 句摘要",
      "report_markdown": "# 标题\n\n## 为什么重要\n...\n\n## 证据\n...\n\n## 建议的下一步\n...",
      "source_note_ids": ["note-id"],
      "evidence_items": [
        { "note_id": "note-id", "quote": "原文引用", "rationale": "详细分析" }
      ],
      "action_items": [
        { "title": "行动标题", "detail": "具体做什么", "priority": "low | medium | high" }
      ],
      "share_card": {
        "theme": "trend | connection | gap | opportunity",
        "eyebrow": "分类标签",
        "headline": "分享标题",
        "summary": "3-5 句分享摘要",
        "evidence_quote": "最有力的原文引用",
        "evidence_source": "笔记标题",
        "action_title": "行动建议",
        "action_detail": "具体怎么做",
        "footer": "底部文字"
      }
    }
  ]
}
```

硬性约束：
- `reports` 数组只含 1 个元素，每次调用聚焦一个最有价值的发现
- `report_markdown` 必须 1500-2500 字，包含 `## 为什么重要` / `## 证据` / `## 建议的下一步`
- `evidence_items` 至少 3 条，来自不同笔记，`quote` 与 `rationale` 非空
- `source_note_ids` 与 `evidence_items[*].note_id` 必须来自上下文快照中的真实 note_id
- `share_card` 内嵌在 report 中，是报告的浓缩版封面
- 所有面向用户的文本必须使用中文

## 切换 Workflow 版本

### 使用 AI SDK（推荐）

```python
# backend 调用方式
from app.intelligence.insights.profiles import build_insight_task_config
from app.intelligence.insights.agent import run_ai_sdk_insight_agent_stream

# 使用 AI SDK workflow
config = build_insight_task_config(use_ai_sdk=True)
# 或设置环境变量：AI_PROVIDER=ai-sdk
```

环境变量配置（`.env`）：
```bash
# AI SDK Provider: openai | anthropic | google | openrouter
AI_SDK_PROVIDER=openrouter
AI_SDK_MODEL=anthropic/claude-3.5-sonnet
AI_SDK_API_KEY=your_api_key
AI_SDK_BASE_URL=https://openrouter.ai/api/v1  # 仅 openrouter 需要
AI_SDK_MAX_TOKENS=8000
AI_SDK_TEMPERATURE=0.7
AI_SDK_STREAMING=true
```

安装依赖：
```bash
cd scripts
npm install
```

### 使用 Claude Agent SDK（原有）

```python
config = build_insight_task_config(use_ai_sdk=False)  # 默认
```

### 回退到 4-stage 工作流

原有 4-stage 工作流（retrieval → editor → review → card）代码保留在 `claude_insight_agent.mjs` 中。
如需回退，将 `profiles.py` 的 `workflow_version` 改回 `"cloud-sdk-v1"` 并恢复 4 个 stage 配置即可。

## 调试与复现

后端会为每次 generation 写入 workspace（默认在 `backend/data/insights/{generation_id}/`），其中包含：
- `context.json`：本次 insight 的 note 快照
- `notes/`：按 note_id 组织的内容片段文件
- `task_config.json`：本次任务配置

手动运行 AI SDK 版本：
```bash
cd /path/to/note-app
export AI_SDK_PROVIDER=openai
export AI_SDK_API_KEY=your_key
export AI_SDK_MODEL=gpt-4o
node scripts/ai_sdk_insight_agent.mjs backend/data/insights/{generation_id}
```
