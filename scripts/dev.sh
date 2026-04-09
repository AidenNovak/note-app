#!/bin/bash

# atelier 开发环境启动脚本

echo "🚀 Starting atelier development environment..."
echo ""

# 检查是否在项目根目录
if [ ! -d "backend" ] || [ ! -d "easystarter" ]; then
    echo "❌ Error: Please run this script from the project root directory"
    exit 1
fi

# 启动后端
echo "📦 Starting backend API..."
cd backend
if [ ! -d ".venv" ]; then
    echo "⚠️  Virtual environment not found. Creating one..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
else
    source .venv/bin/activate
fi

# 启动 FastAPI
uvicorn app.main:app --reload --port 8000 &
BACKEND_PID=$!
echo "✅ Backend started (PID: $BACKEND_PID) at http://localhost:8000"
cd ..

# 等待后端启动
sleep 3

# 启动 Web 前端
echo ""
echo "🌐 Starting web frontend..."
cd easystarter
corepack enable >/dev/null 2>&1 || true
if [ ! -d "node_modules" ]; then
    echo "⚠️  Dependencies not found. Installing..."
    pnpm install
fi

pnpm dev:web+server &
WEB_PID=$!
echo "✅ Web frontend started (PID: $WEB_PID) at http://localhost:3000"
cd ..

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✨ atelier is ready!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📱 Web App:        http://localhost:3000"
echo "🔧 Backend API:    http://localhost:8000"
echo "📚 API Docs:       http://localhost:8000/docs"
echo "🎨 Product Page:   http://localhost:8000/product"
echo ""
echo "Press Ctrl+C to stop all services"
echo ""

# 等待用户中断
trap "echo ''; echo '🛑 Stopping services...'; kill $BACKEND_PID $WEB_PID 2>/dev/null; echo '✅ All services stopped'; exit 0" INT

# 保持脚本运行
wait
