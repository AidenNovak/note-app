# Local Development

这份文档取代原来的 `CONNECT-GUIDE.md` 和 `TEST-GUIDE.md`，把本地启动、连接检查和基础验证收成一个入口。

## 前置条件

- Python 3.11+
- Node.js 18+
- Xcode 和 iOS Simulator
- `backend/.venv` 已安装依赖
- `easystarter/node_modules` 已安装依赖

## 快速启动

在仓库根目录执行：

```bash
make dev
```

如果要分开启动：

```bash
make backend-dev
make native-dev
```

默认情况下：

- Backend: `http://localhost:8000`
- Expo Metro: `http://localhost:8081`

## 首次安装

```bash
make install
```

如果只想安装某一侧：

```bash
make backend-install
make native-install
```

## 启动 iOS

推荐先跑 `make native-dev`，再在 Expo 终端按 `i` 打开模拟器。

如果需要原生工程方式：

```bash
open /Users/lijixiang/note-app/easystarter/ios/easystarter.xcodeproj
```

或者：

```bash
cd /Users/lijixiang/note-app/easystarter/ios
pod install
npx react-native run-ios --simulator="iPhone 16e"
```

## 基础连通性检查

先确认服务启动正常：

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

然后在模拟器里完成一次完整流程：

1. 登录测试账号
2. 创建 3-5 条笔记
3. 进入 Insight 标签
4. 触发一次 Insight 生成

## 常用验证命令

```bash
make backend-lint
make backend-test
cd easystarter && pnpm check-types
cd easystarter && pnpm lint
```

## 日志查看

如果你是手动把日志重定向到文件，可以直接 tail 对应文件，例如：

```bash
tail -f /tmp/backend.log
tail -f /tmp/expo.log
```

如果没有做重定向，就直接看当前终端输出。

## 故障排查

后端起不来：

```bash
cd /Users/lijixiang/note-app/backend
. .venv/bin/activate
pip install -r requirements.txt
```

Native 起不来：

```bash
cd /Users/lijixiang/note-app/easystarter
rm -rf node_modules
pnpm install
```

模拟器连不上后端：

- 确认 `make backend-dev` 正在监听 `0.0.0.0:8000`
- 确认 Native 环境变量指向正确的 backend URL
- 优先用 `/health` 和 `/ready` 排查，不要先怀疑业务接口
