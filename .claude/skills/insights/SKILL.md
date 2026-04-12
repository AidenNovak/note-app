# Atélier Insight 生成系统

> *像园丁照料植物一样，让 Insight 自然生长*

本 skill 定义 atélier 的 insight 生成工作流。

⚠️ **重要提示**：
- **默认使用旧系统**（Claude Agent SDK）- 稳定、经过验证
- **新系统**（Atélier AI SDK）- 实验性，需要显式启用
- 随时可回退，零破坏性修改

---

## 快速切换

```bash
# 查看当前状态
./scripts/toggle-insight-workflow.sh status

# 启用新系统（实验性，更快）
./scripts/toggle-insight-workflow.sh atelier

# 回退到旧系统（稳定）
./scripts/toggle-insight-workflow.sh legacy

# 测试新系统
./scripts/toggle-insight-workflow.sh test
```

然后重启后端服务即可生效。

---

## 设计理念

### 不是数据分析，是「被看见」

- **读懂一个人**：从笔记中感受思维方式、关注领域、内在矛盾
- **发现隐藏联系**：笔记之间不明显但有意义的联系
- **像朋友一样**：温暖、具体、可执行的建议
- **敢于指出矛盾**：矛盾是最有价值的洞察入口

### 渐进式生长

```
🌱 quick    → 200-400字   → 日常快速回顾
🌿 standard → 600-1000字  → 平衡深度和效率（默认）
🌳 deep     → 1500-2000字 → 周期性深度分析
```

---

## 架构

### 核心文件

| 文件 | 说明 |
|------|------|
| `scripts/atelier-insight.mjs` | **主生成器**，使用 Vercel AI SDK |
| `backend/app/intelligence/insights/profiles.py` | 配置管理，支持三种模式 |
| `backend/app/intelligence/insights/agent.py` | Agent 运行器 |
| `backend/app/intelligence/agent_engine.py` | 任务调度引擎 |

### 生成流程

```
用户触发
    ↓
fetch_note_context(笔记) → write_workspace(工作区)
    ↓
run_atelier_insight_stream(mode="standard")
    ↓
atelier-insight.mjs (AI SDK)
    ↓
落库 → 推送客户端
```

---

## 启用新系统（Atélier AI SDK）

### 步骤 1：安装依赖

```bash
cd scripts
npm install
```

### 步骤 2：启用新系统（二选一）

**方式 A：使用切换脚本（推荐）**
```bash
./scripts/toggle-insight-workflow.sh atelier
```

**方式 B：手动编辑 .env**
```bash
# backend/.env

# 取消注释这两行来启用新系统
INSIGHT_WORKFLOW_VERSION=atelier-v1
INSIGHT_MODE=standard  # quick | standard | deep
```

### 步骤 3：重启后端服务

```bash
make backend-dev
```

---

## 后端调用（新系统）

```python
from app.intelligence.insights.profiles import (
    build_insight_task_config,
    select_mode_by_context,
)
from app.intelligence.insights.agent import run_atelier_insight_stream

# 方式1：根据笔记数量自动选择模式
note_count = len(notes)
mode = select_mode_by_context(note_count)  # quick | standard | deep

# 方式2：显式指定模式
config = build_insight_task_config(mode="standard")

# 方式3：使用特定 provider
config = build_insight_task_config(
    mode="deep",
    provider="anthropic",
    model="claude-3-5-sonnet-20241022",
)

# 运行
async for event in run_atelier_insight_stream(
    workspace_path,
    mode=config["mode"],
    provider=config.get("provider"),
    model=config.get("model"),
):
    if event["type"] == "progress":
        print(event["data"])
    elif event["type"] == "final":
        reports = event["data"]["reports"]
```

### 4. 手动测试（不需要启用新系统）

可以直接测试新系统，不影响线上：

```bash
# 设置环境变量
export AI_SDK_PROVIDER=openrouter
export AI_SDK_MODEL=anthropic/claude-3.5-haiku
export AI_SDK_API_KEY=your_key
export AI_SDK_BASE_URL=https://openrouter.ai/api/v1

# 测试标准模式
node scripts/atelier-insight.mjs backend/data/insights/{generation_id}

# 测试快速模式
node scripts/atelier-insight.mjs backend/data/insights/{generation_id} --mode=quick

# 测试深度模式
node scripts/atelier-insight.mjs backend/data/insights/{generation_id} --mode=deep
```

或使用测试脚本：
```bash
./scripts/test-atelier-insight.sh
```

---

## 三种模式详解

### 🌱 Quick 模式

**场景**：日常快速回顾，笔记较少时

**输出**：
- 长度：200-400 字
- 结构：一个观察 + 简要分析 + 一个小建议
- 语气：像朋友随口分享

**示例**：
```
我注意到你最近经常在笔记里提到「时间不够用」...
（一段简短分析）

一个小建议：试试看每天只给自己安排 3 件事？
```

### 🌿 Standard 模式（默认）

**场景**：常规洞察生成

**输出**：
- 长度：600-1000 字
- 结构：
  - 【为什么重要】场景开头 → 深层分析 → 代价
  - 【证据】2-3 条，含原文引用 + 解读
  - 【下一步】1-2 个具体建议

### 🌳 Deep 模式

**场景**：周期性深度分析，笔记较多时

**输出**：
- 长度：1500-2000 字
- 结构：
  - 【为什么重要】3-4 段，编织连贯叙事
  - 【证据】逐条深入分析，至少 3 篇笔记
  - 【建议的下一步】2-3 个，从易到难

---

## Provider 支持

| Provider | 推荐模型 | 说明 |
|----------|---------|------|
| OpenAI | gpt-4o-mini | 快速、便宜 |
| OpenAI | gpt-4o | 质量更好 |
| Anthropic | claude-3-haiku | 快速 |
| Anthropic | claude-3-5-sonnet | 质量优秀 |
| Google | gemini-1.5-flash | 免费额度多 |
| OpenRouter | openai/gpt-4o-mini | 统一接口 |

---

## 提示词系统

### 系统提示

```
你是一位温暖、细腻的朋友，正在帮朋友翻阅 TA 的笔记。

你的任务不是做冷冰冰的数据分析，而是像一个懂 TA 的朋友，帮 TA 发现：
- 最近 TA 在想什么？
- 有什么反复出现的念头或模式？
- 有什么 TA 可能忽略了的信号？

风格要求：
- 像朋友聊天一样自然、温暖
- 不用专业术语，不说教
- 敢于指出矛盾，但语气温柔
- 让 TA 读完有「被看见了」的感觉

所有输出必须是中文。
```

### 用户提示（Standard 示例）

```
帮朋友整理笔记，想深入聊聊 TA 最近的思考：

共有 {n} 条笔记：

【笔记标题】(标签)
笔记内容预览...

---

请像写一封简短的信一样，用 JSON 格式返回...
```

---

## 输出格式

```json
{
  "workflow_version": "atelier-standard-v1",
  "summary": "一句话总结",
  "agent_runs": [...],
  "reports": [
    {
      "type": "pattern | connection | gap | trend",
      "status": "published",
      "title": "标题",
      "description": "摘要",
      "confidence": 0.8,
      "importance_score": 0.75,
      "novelty_score": 0.6,
      "report_markdown": "# 标题\n\n## 为什么重要\n...",
      "source_note_ids": ["note-id"],
      "evidence_items": [
        {"note_id": "...", "quote": "...", "rationale": "..."}
      ],
      "action_items": [
        {"title": "...", "detail": "...", "priority": "medium"}
      ]
    }
  ]
}
```

---

## 与旧版对比

| 维度 | 旧版 (Claude SDK) | 新版 (Atélier AI SDK) |
|------|------------------|----------------------|
| 依赖 | 需要本地安装 Claude Agent SDK | 纯 npm 包，一键安装 |
| Provider | 仅 Claude | OpenAI/Anthropic/Google/OpenRouter |
| 输出长度 | 固定 1500-2500 字 | 动态 200-2000 字 |
| 模式 | 单模式 | quick/standard/deep 三模式 |
| Prompt | 英文为主 | 全中文，温暖语气 |
| Token 消耗 | 较高 | 降低 30-60% |
| 生成速度 | 慢 | 快 2-5 倍 |

---

## 回退指南（重要）

新系统可随时回退到旧系统，**零数据丢失**。

### 方法一：使用切换脚本
```bash
./scripts/toggle-insight-workflow.sh legacy
# 然后重启后端服务
```

### 方法二：手动修改 .env
```bash
# backend/.env

# 注释掉这两行
# INSIGHT_WORKFLOW_VERSION=atelier-v1
# INSIGHT_MODE=standard
```

### 方法三：环境变量（临时）
```bash
# 不修改任何文件，仅当前会话生效
unset INSIGHT_WORKFLOW_VERSION
```

然后重启后端服务即可。

---

## 对比与选择

| 场景 | 推荐系统 | 原因 |
|------|---------|------|
| 生产环境，要求稳定 | 旧系统 | 经过充分验证 |
| 想尝鲜，更快更便宜 | 新系统 | Token 省 30-60%，快 2-5 倍 |
| 需要特定模型 | 新系统 | 支持多 Provider |
| 笔记量少 | 新系统 quick 模式 | 轻量快速 |
| 调试问题 | 旧系统 | 更成熟的错误处理 |

---

## 调试与复现

### 查看工作区

每次 generation 会在 `backend/data/insights/{generation_id}/` 创建：
- `context.json`：笔记快照
- `notes/*.md`：笔记内容
- `task_config.json`：任务配置

### 手动复现

```bash
cd /path/to/note-app

# 设置环境
export AI_SDK_PROVIDER=openai
export AI_SDK_API_KEY=sk-xxx

# 运行
node scripts/atelier-insight.mjs backend/data/insights/{generation_id} --mode=standard
```

---

## Roadmap

- [ ] 支持多 insight 一次生成
- [ ] 与 Mind Graph 数据联动
- [ ] 用户反馈学习（thumbs up/down）
- [ ] 个性化语气调节
- [ ] Insight 版本对比
