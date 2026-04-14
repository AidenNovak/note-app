#!/bin/bash
# 本地测试（不调用 API）

echo "本地测试 Atélier Insight"
echo "========================"
echo ""

# 检查文件存在
echo "1. 检查文件..."
if [ -f "scripts/atelier-insight.mjs" ]; then
    echo "   ✅ atelier-insight.mjs 存在"
else
    echo "   ❌ atelier-insight.mjs 不存在"
    exit 1
fi

# 语法检查
echo ""
echo "2. 语法检查..."
if node --check scripts/atelier-insight.mjs 2>&1; then
    echo "   ✅ 语法正确"
else
    echo "   ❌ 语法错误"
    exit 1
fi

# 检查依赖
echo ""
echo "3. 检查 node_modules..."
if [ -d "scripts/node_modules/ai" ]; then
    echo "   ✅ ai 包已安装"
else
    echo "   ⚠️  ai 包未安装，运行: cd scripts && npm install"
fi

# 创建最小测试数据
echo ""
echo "4. 创建测试数据..."
TEST_DIR="/tmp/test-local-$$"
mkdir -p "$TEST_DIR/notes"
echo "# Test" > "$TEST_DIR/notes/test.md"
cat > "$TEST_DIR/context.json" << 'INNEREOF'
{
  "generation_id": "test",
  "note_count": 1,
  "notes": [
    {
      "id": "test",
      "title": "Test",
      "tags": [],
      "updated_at": "2024-01-01T00:00:00Z",
      "path": "notes/test.md"
    }
  ]
}
INNEREOF
echo "   ✅ 测试数据创建在 $TEST_DIR"

# 测试缺少 API key 的错误处理
echo ""
echo "5. 测试错误处理（缺少 API key）..."
OUTPUT=$(node scripts/atelier-insight.mjs "$TEST_DIR" --mode=quick 2>&1)
if echo "$OUTPUT" | grep -q "AI_SDK_API_KEY"; then
    echo "   ✅ 错误处理正确：提示缺少 API key"
else
    echo "   ⚠️  错误处理可能需要检查"
    echo "   输出: $OUTPUT"
fi

# 清理
rm -rf "$TEST_DIR"

echo ""
echo "========================"
echo "本地测试完成"
echo ""
echo "下一步：运行 E2E 测试"
echo "  ./scripts/test-e2e-atelier.sh"
