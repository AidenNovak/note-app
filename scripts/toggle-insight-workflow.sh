#!/bin/bash
# Atélier Insight Workflow 切换脚本
# 用于在旧系统和新系统之间切换

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/../backend"
ENV_FILE="$BACKEND_DIR/.env"

show_usage() {
    echo "Atélier Insight Workflow Toggle"
    echo "================================"
    echo ""
    echo "Usage:"
    echo "  $0 status       - 查看当前配置状态"
    echo "  $0 legacy       - 切换到旧系统（默认，稳定）"
    echo "  $0 atelier      - 切换到新系统（实验性，更快）"
    echo "  $0 test         - 测试新系统"
    echo ""
}

show_status() {
    echo "当前配置状态"
    echo "============"
    echo ""
    
    if [ -f "$ENV_FILE" ]; then
        WORKFLOW=$(grep "^INSIGHT_WORKFLOW_VERSION=" "$ENV_FILE" | cut -d'=' -f2 || echo "未设置")
        MODE=$(grep "^INSIGHT_MODE=" "$ENV_FILE" | cut -d'=' -f2 || echo "未设置")
        PROVIDER=$(grep "^AI_SDK_PROVIDER=" "$ENV_FILE" | cut -d'=' -f2 || echo "未设置")
        MODEL=$(grep "^AI_SDK_MODEL=" "$ENV_FILE" | cut -d'=' -f2 || echo "未设置")
        
        if [ "$WORKFLOW" = "atelier-v1" ]; then
            echo "✅ 当前使用: 新系统 (Atélier AI SDK)"
            echo "   模式: ${MODE:-standard}"
            echo "   Provider: ${PROVIDER}"
            echo "   模型: ${MODEL}"
        else
            echo "✅ 当前使用: 旧系统 (Claude Agent SDK)"
            echo "   状态: 稳定，默认"
        fi
    else
        echo "❌ 未找到 .env 文件: $ENV_FILE"
    fi
    echo ""
}

switch_to_legacy() {
    echo "切换到旧系统 (Claude Agent SDK)..."
    
    if [ -f "$ENV_FILE" ]; then
        # 注释掉新系统的环境变量
        sed -i.bak 's/^INSIGHT_WORKFLOW_VERSION=atelier-v1/# INSIGHT_WORKFLOW_VERSION=atelier-v1/' "$ENV_FILE"
        sed -i.bak 's/^INSIGHT_MODE=/# INSIGHT_MODE=/' "$ENV_FILE"
        rm -f "$ENV_FILE.bak"
        
        echo "✅ 已切换到旧系统"
        echo "   请重启后端服务生效"
    else
        echo "❌ 未找到 .env 文件"
        exit 1
    fi
}

switch_to_atelier() {
    echo "切换到新系统 (Atélier AI SDK)..."
    
    if [ -f "$ENV_FILE" ]; then
        # 启用新系统
        sed -i.bak 's/^# INSIGHT_WORKFLOW_VERSION=atelier-v1/INSIGHT_WORKFLOW_VERSION=atelier-v1/' "$ENV_FILE"
        
        # 如果还没有 INSIGHT_MODE，添加它
        if ! grep -q "^INSIGHT_MODE=" "$ENV_FILE"; then
            echo "INSIGHT_MODE=standard" >> "$ENV_FILE"
        fi
        
        rm -f "$ENV_FILE.bak"
        
        echo "✅ 已切换到新系统"
        echo "   模式: standard (可通过 INSIGHT_MODE 修改: quick/standard/deep)"
        echo "   请重启后端服务生效"
    else
        echo "❌ 未找到 .env 文件"
        exit 1
    fi
}

test_atelier() {
    echo "测试 Atélier 新系统..."
    echo ""
    
    # 检查依赖
    if [ ! -d "$SCRIPT_DIR/node_modules" ]; then
        echo "📦 安装依赖..."
        cd "$SCRIPT_DIR"
        npm install
    fi
    
    # 运行测试
    cd "$SCRIPT_DIR"
    ./test-atelier-insight.sh
}

# Main
case "${1:-status}" in
    status)
        show_status
        ;;
    legacy)
        switch_to_legacy
        ;;
    atelier)
        switch_to_atelier
        ;;
    test)
        test_atelier
        ;;
    help|--help|-h)
        show_usage
        ;;
    *)
        echo "未知命令: $1"
        show_usage
        exit 1
        ;;
esac
