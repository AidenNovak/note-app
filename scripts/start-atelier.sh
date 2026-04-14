#!/bin/bash
# Atélier 启动脚本

echo "🚀 启动 Atélier 开发环境"
echo "=========================="
echo ""

APP_DIR="/Users/lijixiang/note-app"

# 检查后端虚拟环境
if [ ! -d "$APP_DIR/backend/.venv" ]; then
    echo "❌ 后端虚拟环境不存在，请先运行: make backend-install"
    exit 1
fi

# 检查 scripts 依赖
if [ ! -d "$APP_DIR/scripts/node_modules" ]; then
    echo "📦 安装 scripts 依赖..."
    cd "$APP_DIR/scripts"
    npm install
fi

echo "✅ 依赖检查通过"
echo ""

# 显示当前配置
echo "📋 当前 Insight 配置:"
if grep -q "^INSIGHT_WORKFLOW_VERSION=atelier" "$APP_DIR/backend/.env"; then
    echo "   🌿 使用新系统 (Atélier AI SDK)"
    grep "^AI_SDK_MODEL" "$APP_DIR/backend/.env" | head -1
else
    echo "   🔒 使用旧系统 (Claude Agent SDK) - 默认稳定"
    echo "   要启用新系统，运行: ./scripts/toggle-insight-workflow.sh atelier"
fi
echo ""

cd "$APP_DIR"

echo "🎯 启动选项:"
echo "   1) 仅启动后端 (backend-dev)"
echo "   2) 启动后端 + Web"
echo "   3) 启动后端 + Native (iOS 模拟器)"
echo ""
read -p "请选择 [1/2/3]: " choice

case $choice in
    1)
        echo ""
        echo "🟢 启动后端服务..."
        echo "   URL: http://localhost:8000"
        echo "   Docs: http://localhost:8000/docs"
        echo ""
        make backend-dev
        ;;
    2)
        echo ""
        echo "🟢 启动后端 + Web..."
        echo "   Backend: http://localhost:8000"
        echo "   Web:     http://localhost:5173"
        echo ""
        make dev
        ;;
    3)
        echo ""
        echo "🟢 启动后端 + Native..."
        echo "   Backend: http://localhost:8000"
        echo ""
        echo "⚠️  iOS 模拟器需要在 Xcode 中手动启动"
        echo "   或运行: npx expo start --ios"
        echo ""
        make dev-native
        ;;
    *)
        echo "无效选择"
        exit 1
        ;;
esac
