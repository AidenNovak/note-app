# Atélier Insight E2E 测试指南

## 快速开始

### 方法一：使用一键测试脚本

```bash
cd /Users/lijixiang/note-app/scripts
./test-e2e-atelier.sh
```

### 方法二：手动测试

#### 步骤 1: 安装依赖

```bash
cd /Users/lijixiang/note-app/scripts
npm install ai @ai-sdk/openai
```

#### 步骤 2: 设置环境变量

```bash
export AI_SDK_PROVIDER=openrouter
export AI_SDK_MODEL=anthropic/claude-3.5-haiku
export AI_SDK_API_KEY=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
export AI_SDK_BASE_URL=https://openrouter.ai/api/v1
```

#### 步骤 3: 创建测试数据

```bash
TEST_DIR="/tmp/test-insight-$$"
mkdir -p "$TEST_DIR/notes"

# 创建笔记
cat > "$TEST_DIR/notes/note-1.md" << 'EOF'
# 最近的焦虑

最近总是感觉时间不够用，每天都在忙，但回头看好像什么都没做成。
想做的事情很多，但每天下班后就只想躺着刷手机。
EOF

# 创建 context.json
cat > "$TEST_DIR/context.json" << EOF
{
  "generation_id": "test-$$",
  "note_count": 1,
  "notes": [
    {
      "id": "note-1",
      "title": "最近的焦虑",
      "tags": ["反思"],
      "updated_at": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
      "path": "notes/note-1.md"
    }
  ]
}
EOF
```

#### 步骤 4: 运行测试

```bash
# Quick 模式 (200-400字，最快)
node atelier-insight.mjs "$TEST_DIR" --mode=quick

# Standard 模式 (600-1000字)
node atelier-insight.mjs "$TEST_DIR" --mode=standard

# Deep 模式 (1500-2000字)
node atelier-insight.mjs "$TEST_DIR" --mode=deep
```

#### 步骤 5: 验证输出

成功的输出应该是这样的 JSON：

```json
{
  "workflow_version": "atelier-quick-v1",
  "summary": "一句话总结",
  "reports": [
    {
      "type": "pattern",
      "title": "标题",
      "description": "摘要",
      "report_markdown": "# 标题\n\n内容...",
      "evidence_items": [...],
      "action_items": [...]
    }
  ]
}
```

---

## 常见问题排查

### 问题 1: "Cannot find module 'ai'"

**解决**: 
```bash
cd scripts
npm install ai @ai-sdk/openai
```

### 问题 2: "AI_SDK_API_KEY is required"

**解决**:
```bash
export AI_SDK_API_KEY=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
```

### 问题 3: "Invalid JSON response"

**原因**: AI 返回的内容格式不对

**解决**:
- 检查模型是否支持 JSON 输出
- 尝试使用更强的模型如 `claude-3.5-sonnet`
- 查看原始输出调试

### 问题 4: OpenRouter 返回 401/403

**解决**:
- 检查 API Key 是否正确
- 确认账户有余额
- 检查模型名称格式是否正确（如 `anthropic/claude-3.5-haiku`）

### 问题 5: 输出为空或太短

**解决**:
- 检查笔记内容是否足够（至少几条笔记）
- 使用 `standard` 或 `deep` 模式
- 查看 `context.json` 是否正确生成

---

## 测试后端集成

### 步骤 1: 启用新系统

```bash
./toggle-insight-workflow.sh atelier
```

### 步骤 2: 重启后端

```bash
# 根据你的启动方式
make backend-dev
# 或
cd backend && python -m uvicorn app.main:app --reload
```

### 步骤 3: 触发 Insight 生成

在 App 中：
1. 创建几条笔记
2. 进入 Insight 页面
3. 点击生成

### 步骤 4: 查看日志

后端日志应该显示：
```
PROGRESS: {"type": "starting", "message": "Atélier Insight (standard)..."}
PROGRESS: {"type": "progress", "message": "标准洞察 - 平衡深度和效率..."}
...
```

---

## 回退到旧系统

如果测试发现问题：

```bash
./toggle-insight-workflow.sh legacy
make backend-dev
```

---

## 性能对比测试

可以对比新旧系统的性能：

```bash
# 旧系统（如果还能运行）
time node claude_insight_agent.mjs /path/to/workspace

# 新系统
time node atelier-insight.mjs /path/to/workspace --mode=standard
```

预期新系统：
- 速度快 2-5 倍
- Token 消耗少 30-60%
