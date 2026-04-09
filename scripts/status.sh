#!/bin/bash

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  atelier 服务状态检查"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# 检查后端
echo "🔧 后端 API (http://localhost:8000)"
BACKEND_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null)
if [ "$BACKEND_STATUS" = "200" ]; then
    echo "   ✅ 运行正常"
    BACKEND_INFO=$(curl -s http://localhost:8000/health 2>/dev/null)
    echo "   📊 $BACKEND_INFO"
else
    echo "   ❌ 未运行 (HTTP $BACKEND_STATUS)"
fi

echo ""

# 检查前端
echo "🌐 Web 前端 (http://localhost:3000)"
FRONTEND_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3000 2>/dev/null)
if [ "$FRONTEND_STATUS" = "200" ]; then
    echo "   ✅ 运行正常"
else
    echo "   ❌ 未运行 (HTTP $FRONTEND_STATUS)"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  访问地址"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "🌐 Web 应用:     http://localhost:3000"
echo "🔧 后端 API:     http://localhost:8000"
echo "📚 API 文档:     http://localhost:8000/docs"
echo "🎨 产品介绍:     http://localhost:8000/product"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  测试账号"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "邮箱: demo@atelier.com"
echo "密码: demo12345"
echo "笔记: 3 条测试笔记（含中文）"
echo ""
