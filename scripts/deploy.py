#!/usr/bin/env python3
"""
Atelier Vercel 自动部署工具
使用 Claude SDK 自动化部署流程
"""

import os
import sys
import subprocess
import secrets
from pathlib import Path

# 颜色定义
class Colors:
    GREEN = '\033[0;32m'
    BLUE = '\033[0;34m'
    YELLOW = '\033[1;33m'
    RED = '\033[0;31m'
    BOLD = '\033[1m'
    NC = '\033[0m'

def print_header(text):
    print(f"\n{Colors.BOLD}{Colors.BLUE}{text}{Colors.NC}")

def print_success(text):
    print(f"{Colors.GREEN}✅ {text}{Colors.NC}")

def print_warning(text):
    print(f"{Colors.YELLOW}⚠️  {text}{Colors.NC}")

def print_error(text):
    print(f"{Colors.RED}❌ {text}{Colors.NC}")

def run_command(cmd, cwd=None, capture=True):
    """运行命令并返回输出"""
    try:
        if capture:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        else:
            subprocess.run(cmd, shell=True, cwd=cwd, check=True)
            return None
    except subprocess.CalledProcessError as e:
        print_error(f"命令执行失败: {cmd}")
        print(e.stderr)
        return None

def check_vercel_cli():
    """检查 Vercel CLI 是否安装"""
    print_header("📝 检查 Vercel CLI...")
    result = run_command("which vercel")
    if not result:
        print_error("Vercel CLI 未安装")
        print("请运行: npm install -g vercel")
        return False
    print_success("Vercel CLI 已安装")
    return True

def check_vercel_login():
    """检查 Vercel 登录状态"""
    print_header("🔐 检查登录状态...")
    result = run_command("vercel whoami")
    if not result:
        print_warning("未登录 Vercel")
        print("\n请先登录:")
        print("  vercel login")
        return False
    print_success(f"已登录: {result}")
    return True

def generate_secret_key():
    """生成 SECRET_KEY"""
    print_header("🔑 生成 SECRET_KEY...")
    secret_key = secrets.token_urlsafe(32)
    print_success("SECRET_KEY 已生成")
    return secret_key

def deploy_backend(project_root):
    """部署后端"""
    print_header("📦 部署后端 API...")

    backend_dir = project_root / "backend"

    print("\n请确保已在 Vercel Dashboard 设置以下环境变量:")
    print("  1. SECRET_KEY")
    print("  2. OPENROUTER_API_KEY")
    print("  3. DATABASE_URL (或使用 Vercel Postgres)")
    print("  4. APP_ENV=production")

    response = input("\n是否继续部署后端? (y/n): ").strip().lower()
    if response != 'y':
        print_warning("跳过后端部署")
        backend_url = input("请输入后端 URL: ").strip()
        return backend_url

    print(f"\n{Colors.BLUE}🚀 部署中...{Colors.NC}")
    output = run_command("vercel --prod --yes", cwd=backend_dir)

    if output:
        # 提取 URL
        lines = output.split('\n')
        for line in lines:
            if 'https://' in line:
                backend_url = line.strip()
                print_success(f"后端部署完成: {backend_url}")
                return backend_url

    print_error("无法获取后端 URL")
    backend_url = input("请手动输入后端 URL: ").strip()
    return backend_url

def deploy_frontend(project_root, backend_url):
    """部署前端"""
    print_header("📦 部署前端...")

    easystarter_dir = project_root / "easystarter"
    web_dir = easystarter_dir / "apps" / "web"

    # 创建生产环境变量文件
    env_content = f"VITE_NOTE_API_BASE_URL={backend_url.rstrip('/')}/api/v1\n"
    env_file = web_dir / ".env.production"
    env_file.write_text(env_content)
    print_success("前端环境变量已配置")

    response = input("\n是否继续部署前端? (y/n): ").strip().lower()
    if response != 'y':
        print_warning("跳过前端部署")
        return None

    print(f"\n{Colors.BLUE}🚀 部署中...{Colors.NC}")
    run_command("corepack enable", cwd=easystarter_dir)
    run_command("pnpm install --frozen-lockfile", cwd=easystarter_dir)
    output = run_command("pnpm deploy:web", cwd=easystarter_dir)

    if output:
        # 提取 URL
        lines = output.split('\n')
        for line in lines:
            if 'https://' in line:
                frontend_url = line.strip()
                print_success(f"前端部署完成: {frontend_url}")
                return frontend_url

    print_error("无法获取前端 URL")
    return None

def main():
    print(f"{Colors.BOLD}{Colors.GREEN}")
    print("=" * 50)
    print("🚀 Atelier Vercel 自动部署工具")
    print("=" * 50)
    print(Colors.NC)

    # 获取项目根目录
    project_root = Path(__file__).parent.parent

    # 检查环境
    if not check_vercel_cli():
        sys.exit(1)

    if not check_vercel_login():
        sys.exit(1)

    # 生成密钥
    secret_key = generate_secret_key()

    # 部署后端
    backend_url = deploy_backend(project_root)

    # 部署前端
    frontend_url = deploy_frontend(project_root, backend_url)

    # 部署总结
    print(f"\n{Colors.BOLD}{Colors.GREEN}")
    print("=" * 50)
    print("🎉 部署完成！")
    print("=" * 50)
    print(Colors.NC)

    print(f"\n{Colors.BLUE}后端 URL:{Colors.NC} {backend_url}")
    if frontend_url:
        print(f"{Colors.BLUE}前端 URL:{Colors.NC} {frontend_url}")

    print(f"\n{Colors.YELLOW}⚠️  下一步操作:{Colors.NC}")
    print("1. 在 Vercel Dashboard 设置后端环境变量:")
    print(f"   SECRET_KEY={secret_key}")
    print("   OPENROUTER_API_KEY=<your-key>")
    print("   DATABASE_URL=<your-database-url>")
    print(f"   CORS_ORIGINS={frontend_url if frontend_url else 'your-frontend-url'}")
    print("\n2. 配置数据库（推荐使用 Vercel Postgres）")
    print("\n3. 测试部署的应用")

    print(f"\n{Colors.BLUE}📝 请保存 SECRET_KEY:{Colors.NC}")
    print(secret_key)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}部署已取消{Colors.NC}")
        sys.exit(0)
    except Exception as e:
        print_error(f"发生错误: {e}")
        sys.exit(1)
