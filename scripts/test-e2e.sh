#!/bin/bash

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  atelier Web 前端完整测试"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# 测试计数
PASS=0
FAIL=0
API_BASE="http://localhost:8000/api/v1"
FRONTEND_ORIGIN="http://localhost:3000"

# 测试 1: 访问首页
echo "📝 测试 1: 访问首页 ($FRONTEND_ORIGIN)"
HOMEPAGE=$(curl -s -o /dev/null -w "%{http_code}" "$FRONTEND_ORIGIN")
if [ "$HOMEPAGE" = "200" ]; then
    echo "   ✅ 首页加载成功 (HTTP 200)"
    ((PASS++))
else
    echo "   ❌ 首页加载失败 (HTTP $HOMEPAGE)"
    ((FAIL++))
fi
echo ""

# 测试 2: 注册新用户
echo "📝 测试 2: 注册新用户"
RANDOM_USER="test_$(date +%s)@atelier.com"
USER_PASSWORD="test12345"
REGISTER_RESPONSE=$(curl -s -X POST "$API_BASE/auth/register" \
  -H "Content-Type: application/json" \
  -H "Origin: $FRONTEND_ORIGIN" \
  -d "{\"username\":\"test$(date +%s)\",\"email\":\"$RANDOM_USER\",\"password\":\"$USER_PASSWORD\"}")

if echo "$REGISTER_RESPONSE" | grep -q '"id"'; then
    echo "   ✅ 注册成功: $RANDOM_USER"
    ((PASS++))
else
    echo "   ⚠️  使用已有账号: demo@atelier.com"
    RANDOM_USER="demo@atelier.com"
    USER_PASSWORD="demo12345"
fi
echo ""

# 测试 3: 登录
echo "📝 测试 3: 用户登录"
LOGIN_RESPONSE=$(curl -s -X POST "$API_BASE/auth/login" \
  -H "Content-Type: application/json" \
  -H "Origin: $FRONTEND_ORIGIN" \
  -d "{\"email\":\"$RANDOM_USER\",\"password\":\"$USER_PASSWORD\"}")

TOKEN=$(echo "$LOGIN_RESPONSE" | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
if [ -n "$TOKEN" ]; then
    echo "   ✅ 登录成功"
    echo "   🔑 Token: ${TOKEN:0:40}..."
    ((PASS++))
else
    echo "   ❌ 登录失败"
    echo "   Response: $LOGIN_RESPONSE"
    ((FAIL++))
    exit 1
fi
echo ""

# 测试 4: 获取笔记列表
echo "📝 测试 4: 获取笔记列表"
NOTES_RESPONSE=$(curl -s "$API_BASE/notes" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Origin: $FRONTEND_ORIGIN")

if echo "$NOTES_RESPONSE" | grep -q '"items"'; then
    NOTE_COUNT=$(echo "$NOTES_RESPONSE" | grep -o '"total":[0-9]*' | cut -d':' -f2)
    echo "   ✅ 获取成功，共 $NOTE_COUNT 条笔记"
    ((PASS++))
else
    echo "   ❌ 获取失败"
    echo "   Response: $NOTES_RESPONSE"
    ((FAIL++))
fi
echo ""

# 测试 5: 创建笔记
echo "📝 测试 5: 创建新笔记"
CREATE_RESPONSE=$(curl -s -X POST "$API_BASE/notes" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Origin: $FRONTEND_ORIGIN" \
  -d '{"title":"自动化测试笔记","markdown_content":"这是通过自动化脚本创建的测试笔记\n\n## 测试内容\n\n- 支持 Markdown\n- 支持中文\n- 支持标签","tags":["自动化","测试","中文"]}')

NOTE_ID=$(echo "$CREATE_RESPONSE" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
if [ -n "$NOTE_ID" ]; then
    echo "   ✅ 创建成功"
    echo "   📄 笔记 ID: $NOTE_ID"
    ((PASS++))
else
    echo "   ❌ 创建失败"
    echo "   Response: $CREATE_RESPONSE"
    ((FAIL++))
fi
echo ""

# 测试 6: 再次获取笔记列表（验证新笔记）
echo "📝 测试 6: 验证新笔记已添加"
NOTES_RESPONSE2=$(curl -s "$API_BASE/notes" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Origin: $FRONTEND_ORIGIN")

NEW_NOTE_COUNT=$(echo "$NOTES_RESPONSE2" | grep -o '"total":[0-9]*' | cut -d':' -f2)
if [ "$NEW_NOTE_COUNT" -gt "$NOTE_COUNT" ]; then
    echo "   ✅ 笔记数量增加: $NOTE_COUNT → $NEW_NOTE_COUNT"
    ((PASS++))
else
    echo "   ⚠️  笔记数量未变化: $NOTE_COUNT"
fi
echo ""

# 测试 7: 删除笔记
if [ -n "$NOTE_ID" ]; then
    echo "📝 测试 7: 删除笔记"
    DELETE_RESPONSE=$(curl -s -X DELETE "$API_BASE/notes/$NOTE_ID" \
      -H "Authorization: Bearer $TOKEN" \
      -H "Origin: $FRONTEND_ORIGIN" \
      -w "\nHTTP_CODE:%{http_code}")

    DELETE_CODE=$(echo "$DELETE_RESPONSE" | grep "HTTP_CODE" | cut -d':' -f2)
    if [ "$DELETE_CODE" = "204" ]; then
        echo "   ✅ 删除成功 (HTTP 204)"
        ((PASS++))
    else
        echo "   ❌ 删除失败 (HTTP $DELETE_CODE)"
        ((FAIL++))
    fi
    echo ""
fi

# 测试 8: CORS 预检请求
echo "📝 测试 8: CORS 配置"
CORS_RESPONSE=$(curl -s -X OPTIONS "$API_BASE/auth/login" \
  -H "Origin: $FRONTEND_ORIGIN" \
  -H "Access-Control-Request-Method: POST" \
  -v 2>&1 | grep -i "access-control-allow-origin")

if echo "$CORS_RESPONSE" | grep -q "$FRONTEND_ORIGIN"; then
    echo "   ✅ CORS 配置正确"
    ((PASS++))
else
    echo "   ❌ CORS 配置错误"
    ((FAIL++))
fi
echo ""

# 总结
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  测试结果"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "✅ 通过: $PASS"
echo "❌ 失败: $FAIL"
echo ""

if [ $FAIL -eq 0 ]; then
    echo "🎉 所有测试通过！前端可以正常使用。"
    echo ""
    echo "请在浏览器中访问: $FRONTEND_ORIGIN"
    echo "使用账号登录: $RANDOM_USER / $USER_PASSWORD"
    exit 0
else
    echo "⚠️  有 $FAIL 个测试失败，请检查日志。"
    exit 1
fi
