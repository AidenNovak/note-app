# Atélier 测试指南

## 前置条件
- Node.js 18+, Python 3.11+, Xcode (iOS 模拟器)
- 后端依赖已安装 (`backend/.venv`)
- Native 依赖已安装 (`easystarter/node_modules`)

## 快速启动

```bash
cd /Users/lijixiang/note-app

# 同时启动后端 + Native
make dev

# 或分别启动
make backend-dev    # Terminal 1
make native-dev     # Terminal 2
```

## 测试 Insight 系统

### 步骤

1. 启动服务: `make dev`
2. 等待: 后端 `http://0.0.0.0:8000` + Expo Metro 就绪
3. 在 Expo 终端按 `i` 打开 iOS 模拟器
4. 创建 3-5 条笔记 → 进入 Insight 标签 → 生成 Insight

### 预期生成时间

| 模式 | 时间 |
|------|------|
| Quick | 10-15 秒 |
| Standard | 30-45 秒 |
| Deep | 50-70 秒 |

## 运行测试

```bash
# 后端测试
make backend-test

# 后端 lint
make backend-lint

# Native 类型检查
cd easystarter/apps/native && pnpm exec tsc --noEmit
```

## 故障排除

### 后端启动失败
```bash
cd backend && . .venv/bin/activate && pip install -r requirements.txt
```

### Native 启动失败
```bash
cd easystarter && rm -rf node_modules && pnpm install
```

### 模拟器无法连接后端
确保后端监听 `0.0.0.0`（Makefile 默认配置）。Expo 会自动使用 LAN 地址。

## 测试清单

- [ ] 后端启动成功 (http://localhost:8000/health)
- [ ] Native 启动成功 (iOS 模拟器打开)
- [ ] 可以创建笔记
- [ ] Insight 生成成功（中文报告，引用正确笔记）
