# 📱 Atélier 连接指南

## ✅ 当前状态

| 服务 | 状态 | URL/位置 |
|------|------|---------|
| 后端 (Uvicorn) | 🟢 运行中 | http://localhost:8000 |
| Expo (Metro) | 🟢 运行中 | http://localhost:8081 |
| iOS 模拟器 | 🟢 已启动 | iPhone 16e |

## 🚀 完成最后一步

由于 iOS 构建需要较长时间，请选择以下方式之一：

### 方式 1: 使用 Xcode 直接运行 (推荐)

```bash
# 1. 打开 Xcode 项目
open /Users/lijixiang/note-app/easystarter/ios/easystarter.xcodeproj

# 2. 在 Xcode 中:
#    - 选择 iPhone 16e 模拟器
#    - 点击运行按钮 (▶)
```

### 方式 2: 命令行构建

```bash
cd /Users/lijixiang/note-app/easystarter/ios

# 安装依赖 (首次需要 5-10 分钟)
pod install

# 构建并运行
npx react-native run-ios --simulator="iPhone 16e"
```

### 方式 3: 使用 Expo Go (最快)

1. 在 iPhone 16e 模拟器中打开 App Store
2. 搜索 "Expo Go" 并安装
3. 打开 Expo Go，扫描终端中的二维码
4. 应用将在 Expo Go 中运行

## 🧪 测试 Insight 新系统

应用启动后：

1. 登录账户
2. 创建 3-5 条测试笔记
3. 进入 Insight 标签
4. 点击"生成 Insight"
5. 观察后端日志，应该显示：
   ```
   PROGRESS: {"type": "starting", "message": "Atélier Insight (standard)..."}
   ```

## 📊 监控日志

```bash
# 后端日志
tail -f /tmp/backend.log

# Expo 日志  
tail -f /tmp/expo.log
```

## 🛑 停止所有服务

```bash
# 停止后端
pkill -f uvicorn

# 停止 Expo
pkill -f "expo start"

# 停止所有
pkill -f "uvicorn\|expo"
```

## 🔄 切换回旧系统

```bash
cd /Users/lijixiang/note-app
./scripts/toggle-insight-workflow.sh legacy
pkill -f uvicorn
make backend-dev
```
