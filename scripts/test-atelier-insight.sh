#!/bin/bash
# 测试 Atélier Insight 生成器

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "🧪 Testing Atélier Insight Generator"
echo "====================================="

# Check dependencies
if [ ! -d "node_modules" ]; then
    echo "Installing dependencies..."
    npm install
fi

# Create test workspace
TEST_WORKSPACE="/tmp/test-atelier-insight-$$"
mkdir -p "$TEST_WORKSPACE/notes"

echo ""
echo "📁 Creating test workspace: $TEST_WORKSPACE"

# Create test notes
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

cat > "$TEST_WORKSPACE/notes/note-2.md" << 'EOF'
# 周末的思考

周六去公园走了走，突然意识到自己已经很久没有这样慢下来了。

平时总是急着回复消息，急着完成任务，急着...生活。

看到公园里有人下棋，有人发呆，有人慢慢地走。感觉自己好像错过了什么。

 Tags: #生活 #慢下来
EOF

cat > "$TEST_WORKSPACE/notes/note-3.md" << 'EOF'
# 关于工作的想法

今天开会的时候，老板提到要提升效率。我心里想，真的是效率问题吗？

还是我们在做的事情本身就有问题？

感觉自己像是在跑步机上，跑得很快，但没有前进。

 Tags: #工作 #困惑
EOF

# Create context.json
cat > "$TEST_WORKSPACE/context.json" << EOF
{
  "generation_id": "test-$$",
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

echo "✓ Test workspace created with 3 notes"
echo ""

# Check API key
if [ -z "$AI_SDK_API_KEY" ] && [ -z "$OPENAI_API_KEY" ] && [ -z "$OPENROUTER_API_KEY" ]; then
    echo "⚠️  Warning: No API key found"
    echo "Please set one of: AI_SDK_API_KEY, OPENAI_API_KEY, or OPENROUTER_API_KEY"
    echo ""
    echo "Example:"
    echo "  export AI_SDK_PROVIDER=openai"
    echo "  export AI_SDK_API_KEY=sk-xxx"
    echo ""
    echo "Skipping actual test..."
    rm -rf "$TEST_WORKSPACE"
    exit 0
fi

# Run tests for each mode
for mode in quick standard; do
    echo "🚀 Testing $mode mode..."
    echo ""
    
    node atelier-insight.mjs "$TEST_WORKSPACE" --mode=$mode 2>&1 | while read line; do
        if echo "$line" | grep -q "^PROGRESS:"; then
            # Parse and pretty print progress
            json=$(echo "$line" | sed 's/^PROGRESS: //')
            type=$(echo "$json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('type',''))" 2>/dev/null || echo "")
            message=$(echo "$json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('message',''))" 2>/dev/null || echo "")
            if [ -n "$message" ]; then
                echo "  → $message"
            fi
        elif echo "$line" | grep -q "^\{"; then
            # Final output - save to file
            echo "$line" > "$TEST_WORKSPACE/output-$mode.json"
            echo ""
            echo "✓ $mode mode completed"
            
            # Extract report summary
            title=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['reports'][0]['title'] if d.get('reports') else 'N/A')" 2>/dev/null || echo "N/A")
            desc=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['reports'][0]['description'][:100] + '...' if d.get('reports') and len(d['reports'][0].get('description','')) > 100 else d.get('reports',[{}])[0].get('description','N/A'))" 2>/dev/null || echo "N/A")
            
            echo "  Title: $title"
            echo "  Desc:  $desc"
            echo ""
        fi
    done
    
    echo "---"
    echo ""
done

# Cleanup
echo "🧹 Cleaning up..."
rm -rf "$TEST_WORKSPACE"

echo ""
echo "✅ Test complete!"
