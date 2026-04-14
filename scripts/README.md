# Atélier Insight Generator

使用 **Vercel AI SDK** 实现的轻量级、渐进式 Insight 生成器。

> *像园丁照料植物一样，让 Insight 自然生长*

## 特性

- 🌱 **渐进式生成** - quick/standard/deep 三种模式
- 🚀 **多 Provider 支持** - OpenAI / Anthropic / Google / OpenRouter
- 📝 **中文优先** - 温暖的对话式提示词
- ⚡ **轻量快速** - 比旧版快 2-5 倍，节省 30-60% token

## 快速开始

### 安装

```bash
cd scripts
./install.sh
```

### 配置

```bash
# 编辑 backend/.env
AI_SDK_PROVIDER=openai
AI_SDK_MODEL=gpt-4o-mini
OPENAI_API_KEY=sk-xxx
```

### 使用

```bash
# 标准模式（默认）
npm run insight <workspace_path>

# 快速模式
npm run insight:quick <workspace_path>

# 深度模式  
npm run insight:deep <workspace_path>
```

## 三种模式

| 模式 | 长度 | 场景 | 推荐模型 |
|------|------|------|---------|
| **quick** | 200-400字 | 日常快速回顾 | gpt-4o-mini |
| **standard** | 600-1000字 | 常规洞察（默认） | gpt-4o-mini |
| **deep** | 1500-2000字 | 周期性深度分析 | gpt-4o / claude-3-5-sonnet |

## 后端集成

```python
from app.intelligence.insights.profiles import build_insight_task_config
from app.intelligence.insights.agent import run_atelier_insight_stream

# 创建配置
config = build_insight_task_config(mode="standard")

# 运行
async for event in run_atelier_insight_stream(
    workspace_path,
    mode=config["mode"],
):
    if event["type"] == "progress":
        print(event["data"])
    elif event["type"] == "final":
        reports = event["data"]["reports"]
```

## 测试

```bash
# 运行测试（需要配置 API key）
./test-atelier-insight.sh
```

## 文件说明

| 文件 | 说明 |
|------|------|
| `atelier-insight.mjs` | 主生成器 |
| `ai_sdk_insight_agent.mjs` | 旧版 AI SDK 生成器（兼容） |
| `claude_insight_agent.mjs` | 旧版 Claude SDK 生成器（兼容） |
| `install.sh` | 安装脚本 |
| `test-atelier-insight.sh` | 测试脚本 |

## 与旧版对比

| 维度 | 旧版 (Claude SDK) | 新版 (Atélier) |
|------|------------------|----------------|
| 依赖 | Claude Agent SDK | 纯 npm 包 |
| Provider | 仅 Claude | 多 Provider |
| 输出长度 | 固定 1500-2500字 | 动态 200-2000字 |
| Prompt | 英文为主 | 全中文 |
| Token 消耗 | 高 | 低 30-60% |
| 生成速度 | 慢 | 快 2-5 倍 |

## 详细文档

见 `.claude/skills/insights/SKILL.md`
