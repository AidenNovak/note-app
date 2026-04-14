#!/bin/bash
# Atélier Insight 安装脚本
# 安装 AI SDK 依赖

set -e

echo "🌿 Atélier Insight Setup"
echo "========================"

# Check Node.js version
if ! command -v node &> /dev/null; then
    echo "❌ Node.js not found. Please install Node.js 18+ first."
    exit 1
fi

NODE_VERSION=$(node -v | cut -d'v' -f2 | cut -d'.' -f1)
if [ "$NODE_VERSION" -lt 18 ]; then
    echo "❌ Node.js 18+ required. Current: $(node -v)"
    exit 1
fi

echo "✓ Node.js $(node -v)"

# Install dependencies
echo ""
echo "📦 Installing dependencies..."
npm install

echo ""
echo "✅ Installation complete!"
echo ""
echo "Usage:"
echo "  npm run insight <workspace_path>          # Standard mode (default)"
echo "  npm run insight:quick <workspace_path>    # Quick mode (200-400字)"
echo "  npm run insight:deep <workspace_path>     # Deep mode (1500-2000字)"
echo ""
echo "Environment variables:"
echo "  AI_SDK_PROVIDER    # openai | anthropic | google | openrouter"
echo "  AI_SDK_MODEL       # e.g., gpt-4o-mini, claude-3-haiku"
echo "  AI_SDK_API_KEY     # Your API key"
echo ""
