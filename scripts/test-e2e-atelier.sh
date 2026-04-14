#!/bin/bash
# Atélier Insight E2E 测试脚本

set -e

APP_DIR="/Users/lijixiang/note-app"
SCRIPT_DIR="$APP_DIR/scripts"
TEST_WORKSPACE="/tmp/test-insight-e2e-$$"

echo "🧪 Atélier Insight E2E Test"
echo "============================"
echo ""

# 1. 检查依赖
echo "1. 检查依赖..."
if [ ! -d "$SCRIPT_DIR/node_modules" ]; then
    echo "   安装依赖..."
    cd "$SCRIPT_DIR"
    npm install ai @ai-sdk/openai 2>&1 | tail -5
fi
if [ ! -f "$SCRIPT_DIR/atelier-insight.mjs" ]; then
    echo "❌ 未找到 atelier-insight.mjs"
    exit 1
fi
echo "   ✅ 依赖检查通过"
echo ""

# 2. 创建测试 workspace
echo "2. 创建测试 workspace..."
mkdir -p "$TEST_WORKSPACE/notes"

# 创建测试笔记 1
cat > "$TEST_WORKSPACE/notes/note-1.md" << 'EOF'
# 最近的焦虑

最近总是感觉时间不够用，每天都在忙，但回头看好像什么都没做成。

想做的事情很多：
- 学习新的编程语言
- 读完那本买了很久的书
- 开始锻炼
- 学做饭

但每天下班后就只想躺着刷手机。

 Tags: #反思 #时间管理
EOF

# 创建测试笔记 2
cat > "$TEST_WORKSPACE/notes/note-2.md" << 'EOF'
# 周末的思考

周六去公园走了走，突然意识到自己已经很久没有这样慢下来了。

平时总是急着回复消息，急着完成任务，急着...生活。

看到公园里有人下棋，有人发呆，有人慢慢地走。感觉自己好像错过了什么。

 Tags: #生活 #慢下来
EOF

# 创建测试笔记 3
cat > "$TEST_WORKSPACE/notes/note-3.md" << 'EOF'
# 关于工作的想法

今天开会的时候，老板提到要提升效率。我心里想，真的是效率问题吗？

还是我们在做的事情本身就有问题？

感觉自己像是在跑步机上，跑得很快，但没有前进。

 Tags: #工作 #困惑
EOF

# 创建 context.json
cat > "$TEST_WORKSPACE/context.json" << EOF
{
  "generation_id": "test-e2e-$$",
  "generated_at": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "note_count": 3,
  "notes": [
    {
      "id": "note-1",
      "title": "最近的焦虑",
      "tags": ["反思", "时间管理"],
      "updated_at": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
      "path": "notes/note-1.md"
    },
    {
      "id": "note-2",
      "title": "周末的思考",
      "tags": ["生活", "慢下来"],
      "updated_at": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
      "path": "notes/note-2.md"
    },
    {
      "id": "note-3",
      "title": "关于工作的想法",
      "tags": ["工作", "困惑"],
      "updated_at": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
      "path": "notes/note-3.md"
    }
  ]
}
EOF

echo "   ✅ 测试数据创建完成"
echo "   Workspace: $TEST_WORKSPACE"
echo ""

# 3. 检查 API Key
echo "3. 检查 API Key..."
if [ -z "$AI_SDK_API_KEY" ] && [ -z "$OPENROUTER_API_KEY" ]; then
    echo "   使用 .env 文件中的配置"
    # 从 .env 读取
    if [ -f "$APP_DIR/backend/.env" ]; then
        export $(grep -E "^(AI_SDK|OPENROUTER)_" "$APP_DIR/backend/.env" | xargs)
    fi
fi

if [ -z "$AI_SDK_API_KEY" ] && [ -n "$OPENROUTER_API_KEY" ]; then
    export AI_SDK_API_KEY="$OPENROUTER_API_KEY"
fi

if [ -z "$AI_SDK_API_KEY" ]; then
    echo "   ❌ 未找到 API Key"
    echo "   请设置: export AI_SDK_API_KEY=sk-or-xxx"
    rm -rf "$TEST_WORKSPACE"
    exit 1
fi

echo "   ✅ API Key 已配置"
echo ""

# 4. 运行测试
echo "4. 运行 insight 生成测试 (quick 模式)..."
echo "   (预计需要 10-30 秒)"
echo ""

cd "$SCRIPT_DIR"

# 设置 OpenRouter 配置
export AI_SDK_PROVIDER=openrouter
export AI_SDK_MODEL=${AI_SDK_MODEL:-anthropic/claude-3.5-haiku}
export AI_SDK_BASE_URL=${AI_SDK_BASE_URL:-https://openrouter.ai/api/v1}

echo "   配置:"
echo "   - Provider: $AI_SDK_PROVIDER"
echo "   - Model: $AI_SDK_MODEL"
echo ""

# 运行并捕获输出
OUTPUT_FILE="$TEST_WORKSPACE/output.json"
node atelier-insight.mjs "$TEST_WORKSPACE" --mode=quick > "$OUTPUT_FILE" 2>&1

# 5. 验证结果
echo "5. 验证结果..."
echo ""

if [ -f "$OUTPUT_FILE" ]; then
    # 检查是否是有效的 JSON
    if python3 -c "import json; json.load(open('$OUTPUT_FILE'))" 2>/dev/null; then
        echo "   ✅ 输出是有效的 JSON"
        
        # 提取关键信息
        python3 << PYEOF
import json
import sys

try:
    with open('$OUTPUT_FILE') as f:
        data = json.load(f)
    
    print("   结果摘要:")
    print(f"   - Workflow: {data.get('workflow_version', 'N/A')}")
    print(f"   - Summary: {data.get('summary', 'N/A')[:60]}...")
    
    reports = data.get('reports', [])
    print(f"   - Reports: {len(reports)}")
    
    if reports:
        report = reports[0]
        print(f"   - Title: {report.get('title', 'N/A')}")
        print(f"   - Type: {report.get('type', 'N/A')}")
        print(f"   - Confidence: {report.get('confidence', 'N/A')}")
        
        content = report.get('report_markdown', '')
        print(f"   - Content length: {len(content)} chars")
        
        evidence = report.get('evidence_items', [])
        actions = report.get('action_items', [])
        print(f"   - Evidence items: {len(evidence)}")
        print(f"   - Action items: {len(actions)}")
        
        print("")
        print("   内容预览:")
        preview = content[:300].replace('\n', ' ')
        print(f"   {preview}...")
    
    print("")
    print("   ✅ E2E 测试通过!")
    
except Exception as e:
    print(f"   ❌ 解析错误: {e}")
    sys.exit(1)
PYEOF
    else
        echo "   ❌ 输出不是有效的 JSON"
        echo "   原始输出:"
        cat "$OUTPUT_FILE" | head -20
    fi
else
    echo "   ❌ 未找到输出文件"
fi

echo ""
echo "6. 清理..."
rm -rf "$TEST_WORKSPACE"
echo "   ✅ 完成"
echo ""
echo "测试工作空间已清理"
