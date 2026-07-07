# Flicking Coach Web App — 设计 Spec

> 本 spec §2 认证 / §3 架构 / §5 设计已分别被 2026-07-06-aiming-cookie-ia-redesign-design.md 演进（密码+OTP / 桌面 hybrid / dark editorial）。其他段落（产品定位 / 数据流 / 错误处理 / 成本）仍有效。
>
> 2026-07-08 再演进：§3 部署 / §10 Phase 3 备案被 `docs/PRD.md` §5.2 + §9.1 取代——**绕过 ICP 备案**（持续境外香港 + Cloudflare），云端方案 A（香港小 VPS + CF Pages）。备案 / 迁国内云相关段落作废。

> 2026-07-05 · 状态:设计待点点 review → 通过后转 writing-plans 出实现计划

## 1. 目标与范围

把现有 KovaaK flicking 分析 + coach 系统(`kovaak_tracker` 包 + ①②③④ + knowledge.py)包装成**面向大陆 KovaaK's 玩家的 web 产品**,让小白用户(不装 Python、不配环境)打开网页就能用。

**产品定位**:
- 试用期:免费 + 点点出 LLM key
- Phase 3 商业化方向:订阅(点点包 key)或免费 + 用户自带 key

**MVP 范围**(本 spec):
- flicking 单场景(1w6ts 类 world-static clicking)
- 账号系统(邮箱验证码 OTP)
- 单次诊断 + 结果历史持久化
- 暂不做:多 session ④ 训练计划、tracking 场景、付费

**非目标**(明确排除):
- tracking 场景(方案 A 不适用)
- 多 session ④ 计划(Phase 2;账号 + 数据基础先铺)
- 国内 ICP 备案(Phase 3 商业化再做)
- 手部摄像头(产品 B,远期)

## 2. 用户与核心流程

**目标用户**:KovaaK's flicking 玩家,以大陆为主。

**核心流程**:
1. 注册/登录:邮箱 → 收 6 位验证码 → 输码 → 进(浏览器保留登录态)
2. 上传:flicking 录像(mp4)+ KovaaK's Stats CSV
3. 等待:异步分析(~160s),进度条 + 阶段文案
4. 看结果:诊断 + 社区知识 + cues + LLM 教练讲解
5. 历史页:复访过往分析

## 3. 系统架构

### 服务组件(单机 Docker Compose)

| 服务 | 作用 |
|---|---|
| Next.js 前端 | 上传/等待/结果/历史/登录页 |
| FastAPI 后端 API | 接收上传、入队、查询、auth 中间件 |
| Worker(异步进程) | 跑 `analyze_flicking_fair_summary` + `build_report` + LLM narration |
| Postgres | 用户映射 + 结果历史 + 任务队列(`FOR UPDATE SKIP LOCKED`) |
| Nginx | 反代 + HTTPS |

### 部署(香港 + Cloudflare,不备案)

```
[大陆用户]
   ↓ HTTPS(域名境外注册,不备案)
[Cloudflare 免费 CDN]   ← 大陆访问慢但通
   ↓
[香港轻量应用服务器 2核4G — Docker Compose 单机]
   ├─ Next.js / FastAPI / Worker / Postgres / Nginx
   ↓ LLM
[DeepSeek API]
```

**关键决策**:
- **不备案**:香港境外服务器 + Cloudflare,大陆可访问(点点优先省事,MVP 快速验证)
- **单机 Docker Compose**:MVP 流量小,简化运维
- **无 Redis**:Postgres 当任务队列(`FOR UPDATE SKIP LOCKED`)
- **视频源文件分析完即删**(隐私 + 省盘),只持久化结果数据

### 数据流

```
用户上传 mp4+csv
  → FastAPI 校验 + 入队 Postgres + 临时存视频(/tmp)
  → Worker 消费 → 跑 analyze_flicking_fair_summary (~160s)
                → build_report(诊断)
                → DeepSeek narration(后端调,key 在环境变量)
                → 结果写 Postgres(关联 user_id)
                → 删视频源文件
  → 前端轮询 GET /api/sessions/{id} → 完成取结果
```

### 数据模型(Postgres)

- `users`:Clerk user_id 映射(我们不存密码,Clerk 管 auth)
- `sessions`:一次分析记录(id, user_id, video_meta, status, created_at, ...)
- `diagnosis_results`:诊断结果(session_id, signals, metrics, narration, cues, ...)

> **实现状态(2026-07-05)**:当前 webapp 后端(`webapp/backend/db.py`)只有 **`sessions` 一张表**(SQLite,`user_id` 硬编码 `'dev'`),没有 `users` 表、没有 Clerk auth、没有 per-user 配额。`diagnosis_results` 内联在 sessions 表(`signals` / `metrics` / `narration` / `cues` 列)。`users` + auth + per-user 配额 **Phase 2D 决定要加**(Wave 2D,见 `docs/PROGRESS.md` 2026-07-05 续 + webapp 切片 1+2 记录)。

## 4. 技术栈

| 层 | 选型 | 备注 |
|---|---|---|
| 前端 | Next.js 16 + Tailwind v4 + shadcn/ui | 遵循 `DESIGN-cursor.md`(Cursor 风:暖奶油 + hairline + 杂志感)。实际版本见 `webapp/frontend/package.json`(Next.js 16.2.10 / React 19) |
| 后端 API | FastAPI | `import kovaak_tracker` 直接用 |
| Auth | Clerk(email code OTP) | 大陆邮件可达,浏览器保留登录态 |
| DB | Postgres 16 | 单机 Docker |
| 任务队列 | Postgres(`FOR UPDATE SKIP LOCKED`) | 不引入 Redis |
| 视频存储 | 服务器临时盘 `/tmp`,分析完删 | MVP 不上 OSS |
| LLM | DeepSeek(`deepseek-chat`) | `providers.json` 已配,改默认 provider 即可 |
| 部署 | Docker Compose 单机 + Cloudflare | 香港轻量服务器 |

## 5. 页面设计(5 页)

| 页 | 内容 |
|---|---|
| 登录 | 邮箱框 + "发验证码" → 输 6 位码 → 进 |
| 上传(首页) | 拖拽 mp4 + csv + "开始分析" |
| 等待 | 进度条 + 阶段文案("追踪中 / 算指标 / 教练生成")+ 预计剩余 |
| 结果 | 见 wireframe |
| 历史 | 过往分析列表(日期 + 头号信号 + 一句话)→ 点进详情 |

**结果页 wireframe**(Cursor 风:聚焦头号问题,不堆图表):

```
┌──────────────────────────────────────┐
│  2026-07-04 · Multiclick 180         │
│                                      │
│  你的头号问题                         │
│  ┌─────────────────────────────┐    │
│  │ 减速段拖太长                  │    │
│  │ decel_frac 0.75 · 偏高       │    │
│  └─────────────────────────────┘    │
│                                      │
│  根因                                 │
│  flick 后刹车那段拖太长,效率低       │
│                                      │
│  社区归因(你做的 12 条知识库)        │
│  静态点击共识:arm flick 到位 →       │
│  wrist micro → hit-confirm           │
│                                      │
│  具体怎么练                           │
│  • 当 tracking 练:快接近、慢落地     │
│  • 落地确认后再点,别边甩边点         │
│                                      │
│  教练讲解                             │
│  [LLM narration 150-300 字]          │
│                                      │
│  ─── 其他信号(点击展开)─────────    │
│  · SPARC 偏低 · path_efficiency ...  │
└──────────────────────────────────────┘
```

**设计要点**:聚焦"头号问题"大字 + 根因 + 知识 + cues + narration,其他信号折叠。**不堆 metrics 卡片墙**(那是 dashboard 难看的根源)。`knowledge.py` 12 条知识库 + cues 第一次直接亮给用户,而不是藏在 LLM prompt 里。

### 设计规范(遵循 `DESIGN-cursor.md`)

点点已定:前端直接用 `DESIGN-cursor.md`(Cursor 编辑器营销站设计系统)。关键 token:

- **画布**:暖奶油 `#f7f7f4`(非纯白);墨色暖近黑 `#26251e`
- **品牌色**:Cursor Orange `#f54e00`(CTA + wordmark,极少用)— 保留或换"瞄准主题色",实现时点点定
- **字体**:CursorGothic(licensed)→ MVP 开源替代 **Inter**(weight 400 + 负 letter-spacing 杂志感);代码用 JetBrains Mono
- **深度**:hairline(1px 边框)only,无阴影
- **AI timeline pills**(peach/mint/blue/lavender/gold)→ 用于"分析进度"展示(追踪中 / 算指标 / 教练生成),高度契合
- **圆角**:CTA 8px,卡片 12px
- **节律**:80px section
- **light + dark 双模式**:`DESIGN-cursor.md` 主要描述 light(暖奶油),dark 模式实现时按反相补(暖墨画布 + 奶油文字)

## 6. 错误处理

| 场景 | 处理 |
|---|---|
| 上传格式错 / 缺 CSV | 前端校验,红字提示 |
| 视频太大 | 限 100MB,超出提示 |
| 目标检测失败 / 视频分析异常 | job `failed` + 返回"已知限制"说明(`PROGRESS.md` 那 6 条) |
| LLM 超时 / 失败 | **降级**:展示诊断 + cues,不显示 narration |
| Worker 崩 | 自动重试 1 次,再失败标 `failed`,用户可手动重试 |
| 并发滥用 | 单用户同时只 1 个 job |
| **LLM 限额(试用期)** | **不限次数,限金额**:每用户每天 ¥X 上限(后端按 token 计费累计);DeepSeek 单次约 ¥0.003 → 金额限制宽松,故**叠加并发限制**(单用户同时 1 个 job)防脚本刷。具体金额睡醒定 |

## 7. 测试策略

- **后端 API**:FastAPI TestClient + pytest,每 endpoint 单测
- **Worker**:拿 `6月23日.mp4` 跑端到端集成测试,断言结果结构
- **前端**:React Testing Library(组件)+ Playwright(上传→等待→结果 E2E)
- **LLM**:测试用 mock,不每次真调(省钱)
- **现有 57 个 coach 测试**:全保留,新代码加新测试

## 8. 成本(试用期,点点出)

| 项 | 月费 |
|---|---|
| 域名(.com,境外注册) | ~$10/年(≈¥6/月) |
| 香港轻量服务器(2核4G) | ~¥60-120/月 |
| Cloudflare CDN | 免费 |
| Clerk auth(<1000 月活) | 免费 |
| DeepSeek API | 按用量(MVP 试用期低) |
| **合计** | **~¥70-130/月 + LLM 用量** |

## 9. 实现切片(交付节奏)

1. **后端 API + Worker**(纯 Python,FastAPI 包 `kovaak_tracker`,本地端到端跑通)— 点点深度审代码
2. **前端骨架**:上传 → 等待 → 结果(无 auth,本地)
3. **加 auth + DB 持久化 + 历史页**
4. **shadcn 美化**(A+D 风)— 点点看渲染效果调样式
5. **部署香港 + Cloudflare + 真实端到端验证**

第 1 步纯 Python 点点能审;后面前端步骤用 shadcn 出,点点看渲染调。

## 10. 已知限制 / 未来阶段

**MVP 已知限制**:
- 视频上传慢(大陆家庭宽带上行小 + 香港服务器,需进度条 + 文案安抚)
- Cloudflare 大陆偶抽风
- **Clerk 验证码邮件大陆偶有延迟 / 进垃圾箱风险**(MVP 可接受,用户重试;Phase 3 可换自建 SMTP 或国内邮件服务)
- **产品名:Aiming Cookie**(对应 GitHub repo `Clickist/Aiming-cookie`);品牌色 MVP 默认 Cursor Orange(点点看渲染再调);字体 Inter 替代 licensed CursorGothic;DESIGN-cursor.md 视觉系统(暖奶油/hairline/杂志感)直接借用
- 无多 session 计划(④ 暂未接前端)

**Phase 2**(下次 spec):
- 多 session 历史 → ④ `build_plan` 训练计划上线
- 趋势图
- 支持更多 flicking 场景

**Phase 3**(商业化):
- 付费订阅 或 用户自带 key
- 国内 ICP 备案 + 迁国内云(用户体验 + 合规)
- 可能要企业主体(个人备案不能明显商业化)

---

## 决策记录

- **部署香港不备案**:点点优先省事 + MVP 快速验证;Phase 3 商业化再迁国内备案
- **LLM 用 DeepSeek**:MVP 省钱(比 Claude 便宜 10-50×),中文质量够;切回 Claude 是一行配置(`providers.json` 改默认)
- **账号提到 MVP**:点点要结果对应账号持久化 + 访问,且为 Phase 2 ④ 计划铺路
- **单机 Docker Compose**:MVP 流量小,简化运维;Phase 3 扩展再拆服务
- **邮箱 OTP 而非 magic link**:留在本页输码,体验连贯(Linear/Vercel 同款)
- **设计遵循 DESIGN-cursor.md**:点点否决 dashboard(信息密度高),选 Cursor 风(暖奶油 + hairline + 杂志感 + AI timeline pills 契合分析进度展示)
- **产品名 Aiming Cookie**:点点定,对应 GitHub repo
- **LLM 限金额不限次数**:DeepSeek 单次约 ¥0.003(极便宜),金额限制宽松,故叠加并发限制(单用户同时 1 个 job)防脚本刷

## 关联

- ④ 计划调整(已 merge main):`docs/superpowers/specs/2026-06-29-plan-adjustment-design.md`
- 产品战略(两产品线):`docs/product-strategy.md`
- flicking 平移方案:memory `flicking-pan-tracker`
