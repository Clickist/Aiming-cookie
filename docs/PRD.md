# Aiming Cookie — 产品需求文档 (PRD)

> **文档定位** · 建立 2026-07-08
> 这是 Aiming Cookie 的**方向锚** + **原始设想记录**。所有下游文档（spec / plan / 各子系统设计）从此派生。多轮 spec/plan 迭代若与本文冲突，**以本文为准**；本文过时则更新本文，不在下游打补丁。
>
> **维护原则**：产品级决策回写本文；子系统实现细节留在各 spec/plan。

---

## 1. 产品一句话

基于物理 + 运动学的 **KovaaK's 瞄准诊断 + AI 教练**。桌面应用：采集真实训练输入 → 公平指标 → 三层根因诊断 → 对话式 AI 教练 → 长期进步追踪。

**产品关系（2026-07-10 澄清；2026-07-13 输入原生扩展）**：这是**一个产品**，不是免费版 / 付费版两套产品。付费墙只决定**解锁哪些能力**（见 §5.5），不改变产品身份。长期上，**Aiming Coach 是常驻关系层**——降低用户对界面与流程的学习成本；KovaaK Run、输入原生运动学、视频增强、确定性报告和持久化的瞄准表现记录，都是为教练（与免费用户的自助诊断）提供**客观上下文与病历**的工具，而不是与教练并列的第二套产品。

## 2. 为什么做（原始设想）

**创始人的痛**：点点自己是 KovaaK's 玩家（DPI 1600 / 51cm per 360° / FOV 103），苦于瞄准训练缺乏**客观、可量化、个性化**的诊断。现有方案两层都是主观的：社区只有主观体感交流（"今天手感好""感觉甩过了"），没有数据化反馈；**个人教练的教学也凭经验感觉，无法量化**——两层都缺客观数据。学术运动科学有成熟指标（SPARC、Fitts throughput、submovement）但没人产品化给玩家。Aiming Cookie 凭运动学数据做诊断，比主观经验更客观、更科学。

**核心信念**：
1. **公平指标**——跨距离/速度可比的运动学量（decel_frac / SPARC / linearity / throughput），比"命中率"更诚实地暴露问题
2. **减速段是诊断核心**——flick 的"刹车"段最能反映控制质量（社区 Zeonlo / Bardpill + 神经科学 Becker 2020 交叉验证）
3. **AI 教练做个性化诊断**——指标是骨架，教练把指标翻译成"你具体哪里有问题、怎么练"
4. **规则化诊断免费，LLM 教练付费**——最贵的 CV 本地跑不亏钱，LLM 按量收费，freemium 成本结构成立

**张力感知的演变**（理论诚实记录）：早期设想从录像推断"手部张力"（PTC, Pure Tension Coeff），后经审视确认**不成立**——PTC 实为 miss-frame 加速度-误差密度，不直接测肌肉张力。当前产品以减速段质量（SPARC 等）为核心诊断，手部张力留待远期手部摄像头验证。相关历史设计已归档，不再作为产品合同。

## 3. 为谁

**核心用户**：大陆为主 KovaaK's flicking 玩家（world-static clicking / 1w6ts 类），希望用真实训练输入获得客观诊断的认真训练者。Desktop 可自动发现 KovaaK Stats / Performance；在 Windows 上经用户明确开启后，还可采集本地 Raw Input。录屏仍是可选的增强证据，不再是所有基础运动学诊断的前置条件。

**扩展（后续阶段）**：
- tracking 玩家（在指标命名、准星/误差语义和真实阈值标定完成后接通）
- 国际用户（Phase 3+）
- 远期：愿装手部摄像头的深度用户

**不为**：不愿折腾录屏 / CSV 的纯休闲玩家（获取成本 > 价值）。

## 4. 核心价值主张

| # | 价值 | 实现 |
|---|---|---|
| 1 | 公平指标 | decel_frac / SPARC / linearity / throughput / reverse_ratio / path_efficiency 等（学术锚点：Balasubramanian 2012 / Fitts / Novak 2002） |
| 2 | 三层根因诊断 | 症状 → 物理 → 处方（规则引擎 `advice.py` / `advice_tracking.py`） |
| 3 | AI 教练对话 | 可调用应用能力的常驻 Coach；以项目内 Pi 源码为 runtime 基线，由 Aiming Cookie 接管并产品化改造 |
| 4 | 长期进步追踪 | 趋势 + ④ 渐进式训练计划（`progress.py` / `planning.py`） |
| 5 | freemium 成立 | 规则化诊断免费（本地）；LLM 教练付费（云端，按 token）——**同一产品上的能力墙**，不是两套产品 |
| 6 | 常驻教练降学习成本 | 付费解锁后 coach agent 可随时进入；用户少记「该点哪个菜单」，多靠对话完成回访与计划 |
| 7 | 输入原生而非视频依赖 | Raw Input + KovaaK Performance / Stats 直接生成输入运动学；视频用于视觉证据、校验和增强，而不是基础运动学的唯一来源 |

## 5. 产品形态与阶段

### 5.1 形态
- **桌面 hybrid 应用**：当前技术基线为 Tauri 2 壳 + 本地分析 runtime（Raw Input / KovaaK 数据解析 / Python CV）+ Coach Agent runtime（以项目内 Pi 源码为基线，由项目接管并产品化改造）+ 云端（LLM 代理 + 账号 + 数据）
- **Web 技术开发、桌面应用交付**：当前可用 Web 前端快速开发和验证，但最终界面按本地桌面应用而不是网站设计；营销落地页与应用分离，应用 Logo 仅作静态品牌标识，不承担导航。
- **工作区 + Coach 侧栏**：主内容位于左侧工作区；Coach 主要以右侧可收起、可调宽度侧栏呈现。常驻的是关系与会话状态，不是所有页面都强制显示聊天框。
- **登录型回访工具**：完整服务有成本，登录锚定用户 + 计费；画像 / 历史是留存核心

### 5.2 分阶段
| 阶段 | 形态 | 付费墙 |
|---|---|---|
| **内部技术预览** | 受控环境，flicking-only；验证输入原生分析、MP4 + Stats fallback 和核心闭环，不是完整 v1 | 无墙，不开放注册 |
| **v1 早期** | 开放注册（邮箱 + OTP + 密码）；支持 KovaaK Run 自动发现、输入原生 flicking 基础诊断和 Windows Raw Input beta | 无墙，全功能免费 |
| **B freemium** | 公开注册 | CV / 诊断本地免费；**墙立 coach 对话 / 深度诊断 / 长期趋势**（LLM 是收费锚） |
| **C 商业化深化** | 境外合规部署 + 大陆访问体验优化 | 订阅 / credits / 用户自带 key |

> 桌面 hybrid 让 freemium 成本结构成立：最贵的 CV 不在服务器，只有 LLM 要钱。

### 5.3 产品能力优先关系（不等于当前施工队列）

产品能力按以下依赖关系演进；具体施工顺序、当前 Gate 与未来里程碑只在 `docs/ROADMAP.md` 维护。

1. **flicking 诊断闭环**：检测/导入训练 Run → 输入原生或 MP4 + Stats 分析 → 确定性诊断 → 本地历史回访；Coach 有权限时可引用结果，但不阻塞免费诊断主路径；
2. **闭环可靠性与共同设计语言**：状态/失败/恢复、通知、日志、History 支撑，以及统一 token 和基础组件；
3. **视觉增强与 tracking 接通**：Raw Input 负责输入运动学；完成目标/准星/误差语义和真实阈值标定后，用视频与更多 tracking 数据增强同一产品闭环；
4. **运营能力**：登录、配额、付费、LLM 计量和可选云同步；
5. **远期扩展**：手部摄像头、多游戏和超出 KovaaK 输入合同的本地采集。

桌面应用不是排在远期的独立 feature，而是当前产品交付形态；installer、签名、公证、更新等发布工程的顺序由 Roadmap 决定。

### 5.4 发布形态

在完整 v1 前，可以提供 **受控环境中的 flicking-only 技术预览**，用于验证 KovaaK Run 自动发现 / Raw Input → 可恢复分析 → deterministic Report → 可选 Coach → 最小 History 回访的核心价值链；没有 Raw Input 或运行在非 Windows 平台时，MP4 + Stats fallback 仍必须可用。

技术预览必须置于 VPN、SSO 或可信代理等访问控制后；不开放注册，不代表 Desktop 安装包、云同步、付费或 tracking 已完成。最小 History 至少包括列表、状态/摘要、分析回看和 terminal analysis 删除；趋势、对比、筛选与导入导出属于后续完整能力。

用户可见的 Coach 关系与消息不得只归属于可删除的 analysis session；删除分析只使引用变为已删除/不可用，不能删除 Coach 消息或长期档案。具体当前状态、下一里程碑和 Go/No-Go Gate 见 `docs/ROADMAP.md` 与 `docs/PROGRESS.md`。

### 5.5 单一产品与付费墙（能力分层，不是两套产品）

| 原则 | 说明 |
|---|---|
| **一个产品** | Aiming Cookie 只有一条产品身份；禁止用「免费产品 / 付费产品」两套叙事拆文档或拆主路径 |
| **付费墙 = 能力开关** | 无权限时不提供教练对话等墙后能力；有权限时多解锁能力，**不改变**首次从训练记录/上传起步、回访默认历史等主路由逻辑 |
| **墙内典型能力（B+）** | coach 对话 / 深度 LLM 讲解 / 依赖 LLM 的长期计划等（精确清单以计费 spec 为准） |
| **墙外始终可用** | KovaaK Run 自动发现、Windows Raw Input 的本地 opt-in、上传分析、确定性诊断报告、本地 History 列表与回看（规则化路径，无 LLM 仍完整） |

**教练与分析的关系（目标模型）**：

```text
用户 ⇄ 常驻 Aiming Coach（付费解锁；体验上像连续教练关系）
              ↑ 读取 / 引用
    分析记录、表现档案、确定性诊断  （上下文工具与病历）
```

- 教练对话**可以**针对某一次练枪分析深挖，也**可以**不绑定单次分析（例如总结一周进步、后续训练建议）。
- Coach 是产品的 **Agent 操作层**：分析、History、趋势、报告、训练目标和后续应用操作是它可调用的工具能力；它不是 Report 旁的只读聊天页。前端主要以支持页面中的**右侧可收起、可调宽度侧栏**承载这条关系；常驻的是关系与会话状态，不是所有页面都强制显示聊天框。
- 体验目标接近「一条长教练关系」；工程上依赖**持久化表现 / 特点档案**，以及 agent **上下文窗口顶满后的衔接**（摘要、换窗、从档案重建）——衔接策略另研究，不在本 PRD 锁死实现。
- **迁移兼容**：当前代码中的 session-bound chat/route 可在迁移期间作为旧数据与旧入口兼容层保留，但不得新增依赖，也不得作为内部预览的最终可放行状态。内部预览 Go 前，用户可见 Coach 关系与消息必须不再只归属于可删除的 analysis session；终局为教练关系可引用 0～N 次分析，并以 agent run、工具事件和产品确认驱动交互。

**删除语义（目标）**：

| 操作 | 行为 |
|---|---|
| 删除**排队中 / 分析中**的记录 | **不允许**（须等完成或失败） |
| 删除**已完成 / 失败**的分析 | 删除该 Analysis 自有的结果与 managed artifacts；不删除 KovaaK Run、Run-owned Raw Input trace 或用户原始 Stats / Performance 文件；Coach 对话与长期记忆保留，引用显示为已删除 / 不可用 |

### 5.6 默认路由（全档位一致）

| 条件 | 默认落地 |
|---|---|
| 无分析历史、无已发现 Run | **新建分析**，进入训练来源选择 |
| 有已发现 Run 或分析历史 | **历史**，同时展示训练记录和分析记录 |
| 首次使用（含已付费用户） | 仍从 **新建分析 / 训练来源选择** 开始，先建立客观记录；**不**因付费而首次直达空教练页 |
| 付费且已有记录 | 回访仍默认历史；在支持 Coach 的工作区可通过顶栏开关或内容 CTA 打开侧栏，降低「下一步点哪」的学习成本 |

### 5.7 输入与分析模式

产品不再把视频视为所有基础运动学指标的唯一事实源。分析根据可用输入选择模式：

| 模式 | 必需输入 | 主要产出 | 视频作用 |
|---|---|---|---|
| **输入原生模式（v1 Preview / Experimental）** | KovaaK Run（Stats / Performance）+ Windows Raw Input（用户 opt-in） | 当前已通过验证的输入运动学与事件对齐；完整 flick segmentation、核心 fair metrics 和正式阈值须通过算法与 Windows 实机 Gate 后才能解除 Preview | 可选；用于视觉校验、目标/准星证据和更丰富的回看 |
| **多源增强模式** | 输入原生模式 + MP4 | 输入运动学 + 目标/准星/场景/视觉时序的交叉证据 | 用于校验和补充，不得静默覆盖输入原生事实 |
| **视频 fallback** | MP4 + Stats CSV | 现有 CV pan trajectory、flick 分段和确定性诊断 | 视频是主要运动证据；没有 Raw Input 时仍保持可用 |

规则：

- Raw Input 只记录相对 `dx/dy`、时间戳和鼠标按钮；不采集键盘或桌面绝对坐标；
- Raw Input 默认关闭，首次开启必须明确告知用户本地采集范围、用途和关闭方式；
- 输入原生模式可以在没有 MP4 时生成当前已验证的基础运动学结果，但在 flick segmentation、核心 fair metrics、高 polling-rate correctness 与 Windows 实机 Gate 通过前，产品中持续标记为 Preview / Experimental；不能伪造目标相对误差、视觉反应时刻或视频证据；
- Performance / Stats 负责场景身份、挑战时间、击杀/命中事件和可用的目标配置；Raw Input 负责输入运动学；视频负责视觉证据；
- 无法可靠对齐多个来源时，结果必须标记为部分证据或回退，不把推断写成测量；multimodal 的视觉校验失败不得抹掉已经完成的 native 结果，应保留 native 结果并将视觉证据标记为不可用；
- 非 Windows 平台必须明确显示 Raw Input 不可用，并保留视频 fallback。

## 6. 核心体验流程

### 6.1 首次旅程（onboarding）——全档位

```
下载安装 → 启动 → login（email + OTP + 设密码；内部预览可简化）
  ↓ 检测 KovaaK 本地数据
训练来源选择（默认页）
  ├ 已发现 KovaaK Run → 选择 Run
  ├ Windows → 明确授权开启 Raw Input（可跳过）
  ├ 可选：选择 MP4 作为视觉增强证据
  └ 无 Run / 不支持 Raw Input → 选择 MP4 + KovaaK CSV fallback
  ↓ 开始输入原生 / 多源 / 视频 fallback 分析
processing（本地 runtime，可后台 / 可切走）
  ├ 教学时刻：指标科普 + 软件教学
  └ 切走兜底：空状态预告卡
  ↓ 完成时
全局 toast + 顶栏角标（不强制跳转）
  ↓
diagnosis_report（规则化诊断免费 / 无 LLM 仍完整）
  ├ 输入运动学 + 三层根因 + 指标 + 规则化处方 cues + 图
  ├ 明确显示证据来源：Raw Input / Performance / Stats / MP4
  └ 无可靠视觉证据时不显示或不声称目标相对误差类结论
  └ 若用户具备教练权限：点击「跟教练深聊」打开右侧 Coach 侧栏
  ↓（仅付费 / 有权限）
coach_sidebar
  └ 自动加载本次分析上下文；会话状态可跨支持 Coach 的工作区持续
  ↓
history（训练记录 + 分析记录）
```

### 6.2 回访旅程（有分析历史）——全档位

```
启动（已登录）→ 有 Run 或分析历史 → history（默认页）
  ├ 训练记录列表：Run、来源完整度、Raw Input 状态、分析状态
  ├ 分析记录列表 +（规划中）趋势
  ├ 大「新建分析」按钮
  └ 若具备教练权限：通过顶栏开关或内容 CTA 打开 Coach 侧栏
  ↓ 分支
看历史 / 新开分析 / 在当前工作区询问 Coach
```

无 Run 且无分析历史时回访仍落 **新建分析 / 训练来源选择**（与 §5.6 一致）。

### 6.3 关键状态
- **完成通知**：全局 toast + 顶栏角标（任意页可见，不强制跳转）；Run 发现、Raw Input 授权结果、输入原生分析完成和视频增强分析完成分别表达
- **空状态**：没有 Run 时解释如何启动 KovaaK 或手动选择 MP4 + Stats；没有 Raw Input 时解释视频 fallback，不把平台限制写成用户错误
- **失败态**：Run 发现、Raw Input、输入对齐、本地 CV / 云端 LLM / 网络断分别写明白 + 重试或 fallback
- **删除**：进行中不可删；完成/失败可删分析，不默认删教练记忆（§5.5）

## 7. 功能边界

### v1（早期，开放注册）
- flicking 诊断（公平指标 + 三层根因 + 处方）
- KovaaK Run 自动发现与本地训练记录（Stats / Performance）
- Windows Raw Input opt-in 与输入原生 flicking 基础诊断；非 Windows 明确降级到视频 fallback
- 输入原生 / 多源增强 / 视频 fallback 三种分析模式，报告显示证据来源和缺失范围
- coach 对话（agent loop + KB）
- 本地 history（趋势 + 列表 + 删 / 导出 / 导入）
- 开放注册（邮箱 + OTP + 密码）+ login
- 完成通知 + 失败态
- 日志（本地 CV / agent / 云端请求 各层）

### B 阶段（freemium）
- 付费墙（coach / 深度诊断 / 长期趋势）
- credits / 订阅（另 spec）
- 云端 history 同步（跨设备）
- ④ 渐进式训练计划接前端

### C 阶段（商业化深化）
- tracking 接通（指标命名、准星/误差语义和真实阈值标定完成后）
- 境外合规部署与大陆访问体验优化
- 订阅 / 用户自带 key

### 远期（不并入当前，留扩展位）
- 手部摄像头（握姿 / 发力 / 微颤 / 疲劳）
- 跨平台录屏和通用鼠标采集；当前只承诺 KovaaK Desktop 的 Windows Raw Input
- 外设推荐（数据驱动佣金）
- 多游戏支持

## 8. 关键 UIUX 决策

> 产品级决策，实现细节在各 spec / plan。

| # | 决策 | 阶段 |
|---|---|---|
| 1 | 默认页动态：无 Run/分析历史 → 新建分析/训练来源选择，有 → history；**首次（含付费）一律先建立客观训练记录** | v1 |
| 2 | 训练来源选择优先使用已发现 KovaaK Run；无 Run 时仍支持 MP4 + Stats CSV；手动 profile 修改走 settings | v1 |
| 3 | processing 可后台；教学时刻 = 指标科普 + 软件教学；空状态给预告卡 | v1 |
| 4 | diagnosis_report 免费（规则化，含处方 cues）；有教练权限时底部显示教练入口 | v1 |
| 5 | coach_dialogue = LLM；内部预览/v1 早期可不立墙，B 立墙（形态待计费 spec）；**墙 = 能力开关，非第二产品** | 预览→B |
| 6 | history 本地优先，同时呈现训练 Run 与分析记录；支持删 / 导出 / 导入，云端同步推后 | v1 本地 / B 云 |
| 7 | v1 登录收窄为"计费 + 身份"，不背 history | v1 |
| 8 | 失败态：本地 CV / 云端 LLM / 网络断 分开写明白 | v1 |
| 9 | 日志 cross-cutting：本地 CV / agent / 云端 各层埋 | v1 |
| 10 | KovaaK Run 自动发现和 Windows Raw Input opt-in 提前进入 v1；录屏仍是可选视觉增强，跨平台/通用采集留远期 | v1 / 远期 |
| 11 | 分析完成：全局 toast + 顶栏角标，不强制跳转 | v1 |
| 12 | 有教练权限时：分析完可被 coach 立即引用；coach 亦可发起不绑定单次分析的对话 | v1→B |
| 13 | upload 视频/CSV 来源文件夹分别记忆：**非代码 bug**（Chromium 同 origin 共享目录记忆=浏览器原生行为；当前两个独立 input 已分别记忆）。强行隔离需 File System Access API（新功能）。**后续打包桌面应用不用浏览器，此问题目标形态不复现** | 不修（web 中间态；桌面形态消失）|
| 14 | 分析记录的删除不级联删除 Coach 记忆；Run 的源文件仍归用户，Run metadata/trace 的删除、源文件失效和引用关系须由独立 spec 冻结 | v1 目标 |
| 15 | 常驻 Coach（有权限时）是 Agent 操作层；分析与表现档案为上下文工具，工具活动、确认和结果跳转是产品交互的一部分 | B 完整；预览先做最小纵向切片 |

## 9. 架构分工（桌面 hybrid）

| 层 | 位置 | 说明 |
|---|---|---|
| Raw Input / KovaaK Run 解析 + 输入原生运动学 + 视频 pan_tracker/flick 指标计算 | **本地 sidecar** | 输入原生路径优先；视频用于多源增强和 fallback，搬用户机器省成本 + 解并发 |
| Coach Agent runtime（agent loop / tool registry / workspace / event stream） | **本地 sidecar** | 以项目内 Pi 源码为基线并允许产品化修改，不以持续兼容或跟随 Pi 上游升级为约束；Aiming Cookie 通过稳定领域工具、权限边界与持久 Coach 状态连接业务数据 |
| LLM 推理请求 | **云端 API 代理** | 藏 key / 按 token 计费 / freemium 计量 |
| 账号 / 订阅 / 画像 / history | **云端**（B+ 阶段） | 跨设备聚合（v1 history 先本地） |

> webapp 既有资产演进不浪费：FastAPI 从"跑分析"瘦身为"账号 / LLM 代理 / 数据"；Worker CV 搬本地 sidecar；Next.js 进桌面壳。

### 9.1 云端部署（方案 A：一台香港小 VPS）

桌面 hybrid 后云端只剩轻量级（鉴权 / LLM 代理 / 计费），无 CV 重活，一台小机足够：

| 组件 | 部署 | 说明 |
|---|---|---|
| landing 落地页 | 静态托管 / CDN | 营销与下载入口；供应商按发布时可用性和合规性选择 |
| 桌面安装包分发 | 版本化对象存储或 release 托管 | 支持校验、回滚和目标地区可用性验证 |
| 后端 API（鉴权 + LLM 代理 + 计费） | 合规境外节点 | 不承担本地 CV 重活；具体供应商和区域在发布前复核 |
| DB | v1 可从单机数据库起步，进入运营阶段前评估托管数据库 | 账号、计量与可选同步数据 |
| 域名 | 合规注册与解析 | 不把规避备案作为产品目标 |

具体成本、供应商、地区可用性和备案/数据义务属于上线决策，不在 PRD 中冻结。history v1 本地优先，B 阶段再引入可选云同步。

## 10. 成功标准

**技术预览成功**：
- 已发现 KovaaK Run 可通过用户界面完成输入原生分析；没有 Raw Input 或非 Windows 时，真实 MP4 + Stats CSV 仍可完成 video-fallback 分析、Report 和最小 History 回看；
- 异常退出不会留下无法恢复的永久进行中状态，失败可识别、可恢复或可重试；
- 无 LLM 时 deterministic diagnosis / prescription 仍完整可用；
- 分析和相关 managed 文件按规则删除，且不级联删除 Coach 消息或长期档案；
- 受控访问、真实素材 E2E、关键 Browser/Desktop 交互、build 和健康检查通过。

当前、客观的 Go/No-Go Gates 只在 `docs/ROADMAP.md` 维护。

**产品成功**：
- v1 阶段：开放注册获活跃测试用户（具体规模待定），留存 + 反馈质量高
- 用户认可诊断准确（"这说的就是我"）+ coach 有用（"比我自己看指标懂多了"）
- B 阶段：freemium 转化率（免费 → 付费教练）健康

**技术成功**：
- 分析稳定（失败率可接受，具体阈值待真实数据校准）
- 性能（本地 CV ~160s 可接受）
- 指标可信（用户跨次比较有意义）
- 输入原生模式在有 Raw Input 时不依赖视频即可稳定生成基础运动学诊断；多源模式能明确区分 Raw Input、Performance、Stats 与 MP4 证据
- 没有 Raw Input、非 Windows 或用户拒绝授权时，视频 fallback 仍能完成原有 flicking 诊断
- Raw Input 的采集范围、授权状态、关闭方式和本地保留边界对用户清楚可见

## 11. 非目标（明确排除）

- Dashboard 独立页（合并进 history 趋势卡）
- Academy（有真实训练内容前不进导航）
- 社交登录（Apple / Google / GitHub，国际化再加）
- 多游戏（先 KovaaK's）
- 手部摄像头 v1（远期）
- Raw Input 目前不扩展到键盘、桌面绝对坐标、后台任意应用或非 KovaaK 进程
- Raw Input 不直接替代目标/准星视觉证据；没有可靠来源时不输出目标相对误差、视觉反应时刻等结论
- 桌面发布工程细节（Python bundling、installer、签名、公证、自动更新，另 plan）
- 订阅计费 / 支付（另 spec）
- Benchmark、external progress、在线 provider、外部身份和 leaderboard 不进入 v1 正式 UI；后端或研究能力存在不等于产品已交付

## 12. 约束与依赖

- **技术边界**：Raw Input / KovaaK 数据解析 + Python CV + 项目内 Pi-based Coach runtime + Next.js/React 前端 + FastAPI 服务 + Tauri 2 桌面壳；具体版本以依赖文件和 lockfile 为准
- **输入事实源分工**：Raw Input 测量用户输入运动学；Performance / Stats 提供场景、挑战时间、击杀/命中事件和可用配置；MP4 提供视觉目标/准星/场景证据；任何单一来源都不得静默冒充其它来源
- **隐私与平台**：Raw Input 第一版仅 Windows、默认关闭、用户 opt-in、只在检测到 KovaaK 进程时采集、只保存本地，不上传云端或自动发送给 Coach；非 Windows 必须有可用 fallback
- **合规**：采用境外合规部署；若不提供中国大陆境内托管服务，则不把“规避备案”作为产品目标或表述，具体上线方案上线前复核
- **成本**：CV 本地运行以降低服务器成本；LLM 按 token 计量，模型和部署成本在上线前按实际方案复核
- **大陆访问**：以合法合规前提下的香港/境外节点和 CDN 方案优化体验，实际可用性需发布前验证

## 13. 关联文档

| 文档 | 角色 |
|---|---|
| 本 PRD | **方向锚**（产品级） |
| `docs/ARCHITECTURE.md` | 当前/目标架构、领域边界、稳定合同与演进顺序 |
| `docs/ROADMAP.md` | 当前优先级、下一施工切片与 Go/No-Go Gates |
| `docs/PROGRESS.md` | 最近完成、当前阻塞、验证与交接快照 |
| `docs/design-system.md` | 前端视觉 token、组件边界与设计资产治理 |
| `docs/frontend-uiux-design.md` | 已确认的桌面应用骨架、分析工作区与 Coach 侧栏设计 |
| `docs/superpowers/specs/README.md` | 当前有效的局部设计入口 |
| `docs/superpowers/plans/README.md` | 当前可执行 implementation plan 与 Task 状态 |
| `docs/archive/README.md` | 已退役、冻结和完成资料索引，仅供历史追溯 |

## 14. 决策日志（关键选择 + 为什么）

- **桌面 hybrid 而非纯 web**：省 CV 服务器成本 + 解并发；LLM / 账号必须云端；资产演进不浪费
- **诊断免费 / 教练付费**：规则化诊断（advise / diagnosis）零 LLM 成本可免费；LLM 是唯一硬成本，作收费锚；切分点干净（`build_report` `backend=None` 跳过 narration）
- **history 本地优先（v1）**：简化 v1；导出 / 导入做手动跨设备；云端同步推后 B 阶段
- **默认页动态分支**：首次无 Run/分析历史不该看空页 → 默认训练来源选择；回访有 Run/分析历史 → 默认 history
- **v1 → B → C 分阶段**：v1 开放注册无门槛（不卡邀请码、不背支付）；桌面让 freemium 成立；C 在合规境外部署基础上深化商业化
- **输入原生 flicking 先行、完整 tracking 后接**：Raw Input 先解决用户输入运动学事实源，flicking 基础诊断可以脱离视频运行；目标/准星/误差语义和真实阈值标定完成后，再用视频和更多 tracking 数据接通完整 tracking
- **按依赖演进而非删 feature**：保留 flicking、tracking、桌面 hybrid、登录/计费、长期趋势等完整路线；先验证 flicking 闭环及其可靠性，再接 tracking 与运营能力
- **统一设计系统先于全面美化**：可执行 token 集中于前端；页面不得各自硬编码视觉值。现有设计稿和 Stitch 产物保留为参考，不与运行时代码争夺事实源
- **技术预览不冒充完整 v1**：受控预览只验证 flicking 核心闭环；长期完整产品范围不变，发布日期由 Roadmap Gates 和真实验证决定
- **四层架构边界**：Domain Core 保持确定性；Local Analysis Runtime 负责 job、文件和本地 History；Coach Agent Runtime 负责常驻关系、工具编排与交互事件；Cloud Services 负责可信身份、LLM 代理、计量和可选同步
- **单一产品 + 付费墙能力分层**：不是免费/付费两套产品；墙只开关能力。首次（含付费）从训练来源选择起步；回访有 Run/历史→历史、无→训练来源选择
- **教练与分析**：目标上教练可跨次、可不绑单次分析；分析/表现档案是上下文与病历。过渡实现可挂在分析 session 下，终局不锁死为「对话从属于分析」
- **删除分析不抹教练记忆**：进行中不可删；删完成分析只去该次产物与输入文件
- **常驻 Coach 是 Agent 操作层**：有权限时 agent 可随时进入、调用稳定的应用工具，减少用户对多页面流程的记忆负担；项目内 Pi 源码是 Coach runtime 基线，项目可直接修改且不承诺跟随上游升级；已有可用的 workspace、权限或 sandbox 能力优先保留，不无证据重写
- **持久表现档案 + 上下文衔接**：支撑长教练关系体验；窗口顶满后的 session 衔接另研究，不在本条锁实现
- **输入原生分析提前进入产品，但首发保持 Preview / Experimental**（2026-07-13）：Raw Input 不再只是远期采集基础设施。KovaaK Run + Performance / Stats + Windows Raw Input 组成 v1 的输入原生 flicking 预览路径；视频降级为可选视觉增强或无 Raw Input 时的 fallback。完整 flick segmentation、核心 fair metrics、高 polling-rate correctness 与 Windows 实机 Gate 通过后，才能解除 Preview。基础运动学、目标语义和视觉证据必须按来源分层，不把 Raw Input 过度解释为完整视觉测量。
