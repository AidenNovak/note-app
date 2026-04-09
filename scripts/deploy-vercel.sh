#!/bin/bash

# Atelier Vercel 自动部署脚本
# 使用 Claude SDK 自动化部署流程

set -e

echo "🚀 Atelier Vercel 部署脚本"
echo "================================"

# 颜色定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 检查 Vercel CLI
if ! command -v vercel &> /dev/null; then
    echo -e "${RED}❌ Vercel CLI 未安装${NC}"
    echo "请运行: npm install -g vercel"
    exit 1
fi

# 检查登录状态
echo -e "${BLUE}📝 检查 Vercel 登录状态...${NC}"
if ! vercel whoami &> /dev/null; then
    echo -e "${YELLOW}⚠️  未登录 Vercel${NC}"
    echo "请先运行: vercel login"
    exit 1
fi

echo -e "${GREEN}✅ 已登录 Vercel: $(vercel whoami)${NC}"

# 生成 SECRET_KEY
echo -e "\n${BLUE}🔑 生成 SECRET_KEY...${NC}"
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
echo -e "${GREEN}✅ SECRET_KEY 已生成${NC}"

# 部署后端
echo -e "\n${BLUE}📦 部署后端 API...${NC}"
cd backend

# 设置环境变量（如果需要）
echo -e "${YELLOW}⚠️  请确保在 Vercel Dashboard 设置以下环境变量:${NC}"
echo "  - SECRET_KEY=$SECRET_KEY"
echo "  - OPENROUTER_API_KEY=<your-key>"
echo "  - DATABASE_URL=<your-database-url>"
echo ""
read -p "是否继续部署后端? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${BLUE}🚀 部署后端到 Vercel...${NC}"
    BACKEND_URL=$(vercel --prod --yes 2>&1 | grep -o 'https://[^ ]*' | head -1)
    echo -e "${GREEN}✅ 后端部署完成: $BACKEND_URL${NC}"
else
    echo -e "${YELLOW}⏭️  跳过后端部署${NC}"
    read -p "请输入后端 URL: " BACKEND_URL
fi

cd ..

# 更新前端环境变量
echo -e "\n${BLUE}🔧 配置前端环境变量...${NC}"
cat > easystarter/apps/web/.env.production << EOF
VITE_NOTE_API_BASE_URL=${BACKEND_URL%/}/api/v1
EOF
echo -e "${GREEN}✅ 前端环境变量已配置${NC}"

# 部署前端
echo -e "\n${BLUE}📦 部署前端...${NC}"
cd easystarter
corepack enable >/dev/null 2>&1 || true

read -p "是否继续部署前端? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${BLUE}🚀 部署前端 (Cloudflare Wrangler via easystarter)...${NC}"
    pnpm install --frozen-lockfile
    FRONTEND_URL=$(pnpm deploy:web 2>&1 | grep -o 'https://[^ ]*' | head -1)
    echo -e "${GREEN}✅ 前端部署完成: $FRONTEND_URL${NC}"
else
    echo -e "${YELLOW}⏭️  跳过前端部署${NC}"
    exit 0
fi

cd ..

# 部署总结
echo -e "\n${GREEN}================================${NC}"
echo -e "${GREEN}🎉 部署完成！${NC}"
echo -e "${GREEN}================================${NC}"
echo -e "后端 URL: ${BLUE}$BACKEND_URL${NC}"
echo -e "前端 URL: ${BLUE}$FRONTEND_URL${NC}"
echo ""
echo -e "${YELLOW}⚠️  下一步操作:${NC}"
echo "1. 在 Vercel Dashboard 设置后端环境变量"
echo "2. 在后端项目设置中添加前端 URL 到 CORS_ORIGINS"
echo "3. 配置数据库（推荐使用 Vercel Postgres）"
echo ""
echo -e "${BLUE}📝 SECRET_KEY (请保存):${NC}"
echo "$SECRET_KEY"
