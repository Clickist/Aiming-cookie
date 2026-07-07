# Aiming Cookie IA Redesign — 设计 Spec

> 2026-07-06 · 状态:设计待点点 review → 通过后转 writing-plans 出实现计划
>
> **与 `2026-07-05-flicking-coach-webapp-design.md` 的关系**:本 spec **演进**其 §2 认证(OTP-only → 密码为主 + OTP 为辅)与 §3 架构前提(纯 web Docker → 桌面 hybrid)。flicking/tracking 算法与 coach 管线是既有资产,本 spec 不动,只统一定 IA(导航 / login / 流程)。

## 1. 目标与范围

### 问题
`design/aiming_cookie_redesign/` 6 页导航存在三套互不一致的词汇(营销导航 / 交易态降级 / `history` 页泄露的 App 导航);`login_original` 是与产品不符的重型 3D 稿(bio-metric 文案、首屏两个 WebGL 上下文);且缺统一的"形态 / 架构 / 流程"前提,导致导航与 login 设计无锚。

### 本 spec 范围
统一 Aiming Cookie 的 IA(导航栏词汇、各页导航规则、login 认证与视觉、用户流程),作为 `design/aiming_cookie_redesign/` 落地与 `webapp/frontend/` 承接的设计依据。

### 非目标(明确排除)
- Dashboard / Academy 独立页(YAGNI,见 §3.4)
- 桌面打包工程实现(Tauri vs Electron 选型、Python sidecar 打包、Next.js 壳化、自动更新、签名)—独立 spec
- 订阅计费 / 支付 / credits 系统—独立 spec
- 社交登录(Apple/Google/GitHub)—未来国际化再加
- tracking 场景的能力定义(见 `2026-07-05-tracking-coach-design.md`)
- flicking/tracking 算法、coach agent 管线(既有资产,不动)

## 2. 产品形态与架构前提

### 2.1 形态:登录型回访工具(B)
完整服务有成本(视频 CV 处理 + LLM coach 都烧钱),故登录是必经门,用户画像与历史是核心留存价值。

> 为什么不是 A(匿名一次性)/ C(核心免费):A 无法锚定用户、计费无主键;C 在 web 架构下每次免费分析都烧服务器 CV 成本(成本黑洞)。B 配合 §2.3 桌面 hybrid 后,最贵的 CV 搬本地,freemium 重新可行(§2.2)。

### 2.2 商业:v1 开放注册 → B freemium(分阶段)
> 2026-07-08 演进(见 `docs/PRD.md` §5.2):原"D 邀请制"取消——v1 开放注册无邀请码。
- **v1 早期(开放注册)**:邮箱 + OTP + 密码注册,无门槛;无定价、无 credits;全功能免费。
- **B 阶段(freemium)**:CV 本地免费送不亏(成本在用户机器),**付费墙精准立在 coach 对话 / 深度诊断 / 长期趋势**;LLM API 调用是真实的云端成本,作为收费锚点。
- 桌面 hybrid 让 freemium 的成本结构成立:最贵的 CV 不在服务器,只有 LLM 要钱。

### 2.3 架构:桌面 hybrid(演进 webapp spec §3)
| 层 | 位置 | 说明 |
|---|---|---|
| 视频解析 + pan_tracker + flick/track 指标计算 | **本地 sidecar** | CPU 密集,搬到用户机器 = 省服务器成本、天然解并发 |
| coach agent 框架(tool-use loop、tool handlers、KB 检索) | **本地 sidecar** | 编排逻辑本地跑 |
| LLM 推理请求 | **云端 API 代理** | 藏 API key、按 token 计费、做 freemium 计量 |
| 账号 / 订阅 / 画像 / history | **云端** | 跨 session 聚合,换设备/重装不丢 |

**对 webapp 既有资产的演进**(符合 productize-dont-rebuild):
- `FastAPI` 后端(slice 1 已 merge main)角色从"跑分析"瘦身为"账号 / LLM 代理 / 数据同步"层,代码大部分复用。
- `Worker` 的 CV/分析逻辑搬到本地 sidecar;云端 Worker 仅保留 LLM narration 代理可选。
- `Next.js`/`webapp/frontend` 进 Tauri 或 Electron 壳运行;页面结构不变,只换载体。
- 桌面打包工程细节(Tauri/Electron 选型、sidecar 打包、自动更新、跨平台签名)→ 另出 spec。

### 2.4 载体划分
- **landing** = web 静态官网(营销 + 下载 + 收邮箱发邀请)。
- **产品本体** = 桌面应用(upload / processing / report / coach / history / settings / login)。
- **login** = 桌面应用启动门。

## 3. 导航 IA

### 3.1 App 顶栏词汇
```
logo · [分析 / 历史 / 教练] · [订阅状态 · 设置]
```
- **分析** = upload(新建一次分析,始终可见的主操作)
- **历史** = history(含趋势概览,登录后默认页)
- **教练** = coach_dialogue(继续对话 / 独立问教练)
- **订阅状态** = credits/plan 指示(D 阶段显示"Early Access";B 阶段显示余量/升级)
- **设置** = 齿轮 → settings 子页

### 3.2 三档导航规则
| 档 | 用于 | 形态 |
|---|---|---|
| 营销导航 | landing(web 官网) | logo + 下载 + 登录入口 |
| 交易态降级 | processing | logo + 进度状态(刻意去导航,聚焦等待) |
| App 顶栏 | upload / coach_report / coach_dialogue / history / settings | §3.1 词汇 |

### 3.3 各页导航映射(交付物)
| 页面 | 载体 | 导航 | 改动 |
|---|---|---|---|
| **landing** | web | 营销导航 | CTA 改"下载 Aiming Cookie" + 系统要求;保留营销导航 |
| **login** | 应用 | 无(门) | **新增** `login.html`,方向见 §4 |
| **upload** | 应用 | App 顶栏("分析" active) | 去掉 DOCS/GITHUB,进 App 顶栏 |
| **processing** | 应用 | 交易态降级 | 保持(已对) |
| **coach_report** | 应用 | App 顶栏 + `#session` 面包屑 | 替换单纯"返回"为 App 顶栏 + 面包屑 |
| **coach_dialogue** | 应用 | App 顶栏("教练" active) | 去掉 DOCS/GITHUB 与"开始分析"按钮 |
| **history** | 应用 | App 顶栏("历史" active) | 替换 Dashboard/History/Coach/Academy 为 §3.1 词汇;顶部加趋势概览卡(吸收 Dashboard) |
| **settings** | 应用 | 顶栏齿轮 → 子页 | **新增**:aim profile(DPI/sens/cm360/FOV)+ 订阅状态 |

### 3.4 砍掉 / 合并
- **Dashboard 砍独立页**:其"概览/趋势"价值用 history 列表顶部的趋势卡覆盖,避免造新页 + 趋势聚合数据依赖。
- **Academy 砍**:在出现真实训练计划内容之前不进导航(YAGNI)。

## 4. login

### 4.1 认证:密码为主 + OTP 为辅(演进 webapp spec §2)
| 场景 | 流程 |
|---|---|
| **注册** | email → OTP 验证邮箱 → 设密码 → 进（v1 即开放注册，无邀请码；演进原 D/B 双行）|
| **常规登录** | email + 密码 |
| **找回密码 / 备用登录** | email → OTP → 重置密码 / 直接进 |
| **未来可选** | OTP 作无密码登录 / 二次验证 |

> 为什么不只留一种:纯 OTP 每次登录都要收码(烦 + 依赖邮箱送达率);纯密码找回难、无邮箱验证。**密码为主 + OTP 为辅**兼顾常规便捷与注册验证/找回,且调和了 brainstorm 与 webapp spec §2 的 OTP-only。

### 4.2 状态流
- 未登录 → login 启动门。
- 已登录 → 直接进 history(默认页),免每次看门。
- 未受邀用户从 landing 官网收邮箱;login 页底部放"没有账号?申请邀请"链接兜底。

### 4.3 视觉:方向 C(全屏 shader aura + 居中半透明玻璃卡片)
- 复用 `login_original` 的 WebGL shader 代码(橙+蓝 noise flow),容器改全屏。
- **砍掉 three.js 漂浮卡片**(首屏性能/视觉干扰)。
- 表单用半透明 `backdrop-blur` 卡片浮在 aura 上。
- 性能实现期评估:必要时把 WebGL shader 降级为 CSS 渐变 fallback(保持氛围、降首屏开销)。

### 4.4 文案(去 `login_original` 的空炫词)
- 去掉:`AURA.IDENTITY` / `Secure bio-metric or credential access` / `INITIALIZE UPLINK` / `STANDARD PROTOCOL` / `IDENTIFIER`(产品无生物认证、无 magic)。
- 标题:`登录 / 注册`
- 副标题:`登录以保存分析、解锁 AI 教练与长期趋势`
- 主按钮:`继续`
- 底部链接:`没有账号?申请邀请` / `忘记密码?`

## 5. 用户流程

### 5.1 首次
```
web 官网 → 下载安装 → 启动应用 → login(email + OTP + 设密码)
→ history(默认页) → 分析(upload) → processing → coach_report → coach_dialogue
```

### 5.2 回访
```
启动应用(已登录,直进 history) → 看趋势 / 新开一次分析 / 继续教练对话
```

## 6. 决策日志(关键选择 + 为什么)
- **B 形态**:完整服务有成本,登录是锚用户 + 计费的前提。
- **桌面 hybrid**:省 CV 服务器成本 + 解并发;LLM/账号/画像必须云端(藏 key、跨设备);webapp 资产演进不浪费。
- **D→B 分阶段**:pre-launch 不背支付/credits 包袱;桌面让 freemium 成本结构成立。
- **导航方案 A(3 核心)**:每个一级都落在已存在/已落地页;Dashboard 合并进 history、Academy 砍,不扩 scope。
- **login 方向 C**:沉浸感配启动门第一印象;复用 `login_original` shader 资产;砍 3D 卡片解性能/干扰。
- **密码 + OTP**:纯任一都不合理(便捷 vs 验证/找回),双支持调和且与 webapp spec 演进一致。

## 7. 开放问题(实现期定,不阻塞本 spec)
- login shader:WebGL 真跑 vs CSS 渐变 fallback——实现期性能测后定。
- `login_original` 的 three.js 卡片:完全弃用 vs 降为单张静态预览——已在原型内（`design/login_original/` 仅 `code.html` + `screen.png`，无独立 three.js 资源），实现期定。
- settings 形态:独立子页 vs modal——实现期定(倾向子页,aim profile 字段不少)。
- webapp 前端路由如何承接:Next.js/前端路由 → §3.1 顶栏词汇的映射,实现计划里拆。
- 订阅状态指示的精确形态(D 阶段 "Early Access" badge vs B 阶段 credits 数字)——待计费 spec。
