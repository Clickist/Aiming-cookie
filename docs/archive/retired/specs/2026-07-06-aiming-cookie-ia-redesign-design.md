# Aiming Cookie IA Redesign — 当前 IA 合同

> **状态：2026-07-10 已按新 `docs/PRD.md` 重写。**
>
> 此文替代 2026-07-06 版本中「登录后总是进入 History」「`/coach` 是脱离分析上下文的独立终局页」和将 session-bound chat 视为终局的内容。产品范围以 `docs/PRD.md` 为准；常驻 Coach 的产品边界以 `2026-07-10-persistent-coach-design.md` 为准，技术迁移须先完成 Pi adoption assessment 后的替代 implementation plan。

## 1. 目标与范围

本 spec 只冻结当前产品 IA：默认落地、一级导航、分析与 Coach 的关系、以及内部技术预览中的页面边界。

不在本 spec 决定：

- Desktop shell、sidecar、IPC、安装分发；
- 登录、OTP、密码、订阅、credits 的具体交互；
- Coach 长上下文摘要、换窗和长期画像的具体算法；
- tracking 的能力定义；
- 页面视觉稿或独立设计语言（遵循 `docs/design-system.md` 和 `webapp/frontend/app/globals.css`）。

## 2. 产品 IA 约束

### 2.1 一个产品，付费墙只是能力开关

Aiming Cookie 只有一条主路径。上传分析、deterministic Report 和 History 不因套餐而分叉；Coach、深度 LLM 讲解和长期计划可按权限解锁，但不能改变首次或回访的默认落地。

内部技术预览是受控环境中的 flicking-only 技术验证，不是公开注册、支付或完整桌面产品。它只可部署在 VPN、SSO 或可信代理后的访问层。

### 2.2 默认路由

| 条件 | 默认落地 | 说明 |
|---|---|---|
| 没有分析历史 | 上传 | 首次使用必须先建立客观记录。即使已有 Coach 权限，也不进入空教练页。 |
| 有分析历史 | History | 回访先看已有记录与下一次分析入口。 |
| 从 Report 主动进入 Coach | 当前用户的常驻 Coach | 自动附加该次已完成分析作为可选引用；不把分析变成对话父级。 |
| 直接进入 Coach | 当前用户的常驻 Coach | 可无分析引用开始对话。 |

「有历史」按当前 owner 可读取的分析记录判断；不是按付费、登录次数或是否存在旧 chat message 判断。

### 2.3 分析与 Coach

目标关系：

```text
用户 ⇄ 常驻 Aiming Coach
       ├─ 可引用 0～N 次已完成分析
       ├─ 读取表现/特点档案
       └─ 保持跨分析的连续对话
分析记录 ─→ deterministic metrics / diagnosis / artifacts
```

- 分析是 Coach 的可选上下文，不是 Coach 对话的所有者。
- Report 的「跟教练深聊」进入或创建当前用户的主 Coach 线程，并附加该次 `done` 分析；再次从同一报告进入不得重复添加引用。
- 删除 `done/failed` 分析会删除该记录的输入与产物，但不得删除 Coach 线程、消息或长期档案。界面应把原引用显示为「引用已删除分析」。
- `queued/running` 分析不可删除。
- 现有 `/sessions/[id]/coach` 是过渡实现，只可在迁移期间兼容或重定向；不得继续作为新功能的目标接口。

### 2.4 一级导航

```text
logo · 分析 · 历史 · 教练 · 设置/订阅状态
```

- **分析**：新建一次分析，指向上传。
- **历史**：分析记录、回看与后续趋势入口；它不是 Coach 的替代聊天记录。
- **教练**：常驻 Coach 入口；有权限时可用，无权限时显示能力边界，但不改变上传/History 默认路由。
- **设置/订阅状态**：后续 auth 与计费接入点；内部预览不伪装为已完成的公开订阅体验。

处理中的分析仍可后台运行；导航不应把用户锁死在 processing 页。完成通知和任务找回由独立计划实现。

## 3. 页面与迁移边界

| 页面/路由 | 当前目标 | 迁移规则 |
|---|---|---|
| `/` | 启动分流：无历史显示上传；有历史跳转 `/history` | 不按套餐分流。 |
| `/history` | 本地分析记录与回看 | 删除遵循 PRD 的状态和 artifact 语义。 |
| `/sessions/[id]/report` | 确定性诊断报告 | Coach CTA 进入常驻线程并附加当前 `done` 分析。 |
| `/coach` | 常驻 Coach 入口 | 可无分析引用；最终入口。 |
| `/sessions/[id]/coach` | 旧 session-bound Coach 入口 | 迁移后重定向/兼容到 `/coach`，并附加该 session（仅 done 且 owner 一致）。 |
| `/sessions/[id]` | 分析 processing/failed 状态 | 保持分析任务页，不承担 Coach 对话。 |

## 4. 当前实施顺序

1. 先完成 Pi adoption assessment 与隔离 Spike，固定源码基线、纳入/删改边界和运行方式；
2. 由 Sol 根据 assessment/Spike 证据编写替代 Coach migration implementation plan，并由点点批准；
3. 按获批 plan 迁移 Coach 数据归属，建立 owner-safe 的 thread/message/reference API 与旧路由兼容；
4. 再把 `/coach`、Report CTA 和 History 删除切到常驻 Coach 语义，验证删除分析不删除 Coach；
5. 默认路由作为独立、小范围前端任务完成，不与 Coach schema 施工混在一起。

旧 `docs/archive/retired/plans/2026-07-10-persistent-coach-migration.md` 已退役。任何 Coach schema、迁移或删除策略，必须等 Pi adoption assessment 后由替代 implementation plan 中一个明确 Task 执行；Fast/executor 不得从本 spec 自行推导。

## 5. 已退役内容

以下旧方向不再是实施依据：

- 「已登录后总是直达 History」；
- 「首次付费用户可以直接进入空 Coach」；
- 将 `/coach` 设计成与历史分析脱节、但又不具备独立持久化线程的页面；
- 删除 session 时级联删除 chat；
- 把 dashboard/趋势卡或视觉稿当作默认路由的决定依据。
