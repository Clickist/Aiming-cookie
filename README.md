# Aiming Cookie

Aiming Cookie 是面向 KovaaK's 认真训练者的桌面瞄准诊断与 AI 教练产品。它优先使用 KovaaK Run、Performance/Stats 与 Windows opt-in Raw Input 生成输入原生运动学诊断，并以本地视频作为可选视觉增强或无 Raw Input 时的 fallback；再结合长期表现记录和可调用应用能力的 Coach，帮助用户理解问题并形成训练闭环。

> 产品目标与范围以 [`docs/PRD.md`](docs/PRD.md) 为准；当前代码能力以代码、测试和真实运行结果为准。

## 从哪里开始

- 文档导航与事实源边界：[`docs/README.md`](docs/README.md)
- 安装、启动、测试与代码入口：[`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)
- 当前进展与阻塞：[`docs/PROGRESS.md`](docs/PROGRESS.md)

## 仓库概览

| 路径 | 职责 |
|---|---|
| `kovaak_tracker/` | 确定性的 flicking/tracking 分析、诊断与报告领域逻辑 |
| `webapp/backend/` | FastAPI、任务队列、本地/服务端运行时、Coach 编排与持久化 |
| `webapp/frontend/` | Next.js 前端与 Tauri 桌面壳 |
| `third_party/pi/` | 项目接管的 Pi runtime 源码基线 |
| `docs/` | 产品、架构、交付、设计、开发与历史资料 |
| `tests/`, `webapp/tests/` | 核心与 Web/Desktop 回归测试 |

本 README 只作为仓库入口，不复制 PRD、架构、路线图、进度或理论正文。
