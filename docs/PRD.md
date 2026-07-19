# Aiming Cookie — 产品需求文档 (PRD)

> **文档定位** · 建立 2026-07-08
> 这是 Aiming Cookie 的**方向锚** + **原始设想记录**。所有下游文档（spec / plan / 各子系统设计）从此派生。多轮 spec/plan 迭代若与本文冲突，**以本文为准**；本文过时则更新本文，不在下游打补丁。
>
> **维护原则**：产品级决策回写本文；子系统实现细节留在各 spec/plan。

---

## 1. 产品一句话

基于物理 + 运动学的 **KovaaK's 瞄准诊断 + AI 教练**。桌面应用：采集真实训练输入 → 公平指标 → 三层根因诊断 → 对话式 AI 教练 → 长期进步追踪。

**产品关系（2026-07-10 澄清；2026-07-13 输入原生与商业模式更新）**：这是以 **Aiming Coach** 为核心的一个开源免费产品，不分免费版 / 付费版，也不以订阅、credits 或能力墙向用户收费。长期上，**Aiming Coach 是常驻关系与产品操作层**——降低用户对界面与流程的学习成本；KovaaK Run、输入原生运动学、视频增强、确定性报告和持久化的瞄准表现记录，都是教练的客观观测、专业判断依据与长期病历，而不是与教练并列的第二套产品。

## 2. 为什么做（原始设想）

**创始人的痛**：点点自己是 KovaaK's 玩家（DPI 1600 / 51cm per 360° / FOV 103），苦于瞄准训练缺乏**客观、可量化、个性化**的诊断。现有方案两层都是主观的：社区只有主观体感交流（"今天手感好""感觉甩过了"），没有数据化反馈；**个人教练的教学也凭经验感觉，无法量化**——两层都缺客观数据。学术运动科学有成熟指标（SPARC、Fitts throughput、submovement）但没人产品化给玩家。Aiming Cookie 凭运动学数据做诊断，比主观经验更客观、更科学。

**核心信念**：
1. **公平指标**——跨距离/速度可比的运动学量（decel_frac / SPARC / linearity / throughput），比"命中率"更诚实地暴露问题
2. **减速段是诊断核心**——flick 的"刹车"段最能反映控制质量（社区 Zeonlo / Bardpill + 神经科学 Becker 2020 交叉验证）
3. **AI 教练做个性化诊断**——指标是骨架，教练把指标翻译成"你具体哪里有问题、怎么练"
4. **开源免费，信任优先**——产品能力不设付费墙；只有当证据与用户上下文支持外设可能成为限制时，Coach 才可提供可选推荐。官方购买链接可以包含联盟代码并产生佣金，但佣金不得影响诊断、推荐触发或商品排序，商业关系必须清晰披露

**张力感知的演变**（理论诚实记录）：早期设想从录像推断"手部张力"（PTC, Pure Tension Coeff），后经审视确认**不成立**——PTC 实为 miss-frame 加速度-误差密度，不直接测肌肉张力。当前产品以减速段质量（SPARC 等）为核心诊断，手部张力留待远期手部摄像头验证。相关历史设计已归档，不再作为产品合同。

## 3. 为谁

**核心用户**：大陆为主 KovaaK's flicking 玩家（world-static clicking / 1w6ts 类），希望用真实训练输入获得客观诊断的认真训练者。Windows Desktop 经用户明确启用后，在 KovaaK 进程 gate 内统一采集 Raw Input，并维护 KovaaK 窗口的有界硬件编码回放缓冲；Stats / Performance 到达后事后切成独立 Run，用户不需要在主路径中手动录屏、匹配 CSV 或切分连续多局。

**扩展（后续阶段）**：
- tracking 玩家（在指标命名、准星/误差语义和真实阈值标定完成后接通）
- 国际用户（Phase 3+）
- 远期：愿装手部摄像头的深度用户

**不为**：只想获得与自身训练无关的通用聊天建议、又不愿提供任何可分析训练证据的用户。自动采集主路径的目标正是消除手动录屏、CSV 配对和切片负担。

## 4. 核心价值主张

| # | 价值 | 实现 |
|---|---|---|
| 1 | 公平指标 | decel_frac / SPARC / linearity / throughput / reverse_ratio / path_efficiency 等（学术锚点：Balasubramanian 2012 / Fitts / Novak 2002） |
| 2 | 三层根因诊断 | 症状 → 物理 → 处方（规则引擎 `advice.py` / `advice_tracking.py`） |
| 3 | AI 教练对话 | 可调用应用能力的常驻 Coach；以项目内 Pi 源码为 runtime 基线，由 Aiming Cookie 接管并产品化改造 |
| 4 | 长期进步追踪 | 趋势 + ④ 渐进式训练计划（`progress.py` / `planning.py`） |
| 5 | 可配置 LLM Coach | 当前产品不设付费墙；用户在 Settings 中选择并连接可用 LLM provider，Coach 与确定性诊断属于同一产品闭环 |
| 6 | 常驻教练降学习成本 | provider 可用时 coach agent 可随时进入并调用当前用户拥有的产品能力；用户少记「该点哪个菜单」，多靠对话完成回访、分析与计划 |
| 7 | 输入原生而非视频依赖 | Raw Input + KovaaK Performance / Stats 直接生成输入运动学；MP4 主要用于直观回放、问题定位和视觉证据，不是基础运动学的主事实源 |

## 5. 产品形态与阶段

### 5.1 形态
- **开源免费的桌面 hybrid 应用**：当前技术基线为 Tauri 2 壳 + 本地分析 runtime（Raw Input / KovaaK 数据解析 / Python CV）+ Coach Agent runtime（以项目内 Pi 源码为基线，由项目接管并产品化改造）+ 用户自行选择并连接的 LLM provider。具体开源许可证与发布义务在 release 准备中单独确认，不改变产品能力免费开放的方向。
- **Web 技术开发、桌面应用交付**：当前可用 Web 前端快速开发和验证，但最终界面按本地桌面应用而不是网站设计；营销落地页与应用分离，应用 Logo 仅作静态品牌标识，不承担导航。
- **工作区 + Coach 侧栏**：主内容位于左侧工作区；Coach 主要以右侧可收起、可调宽度侧栏呈现。常驻的是关系与会话状态，不是所有页面都强制显示聊天框。
- **无产品账号的本地优先工具**：画像、Coach 关系与 History 都属于当前 OS 用户的本地 profile。Aiming Cookie 不要求注册、登录或产品鉴权服务器；Provider 是否需要认证由其自身决定；如需认证，只发生在用户与其选择的模型服务之间。

### 5.2 分阶段
| 阶段 | 形态 | 商业模式 |
|---|---|---|
| **内部技术预览** | 受控环境，flicking-only；验证进程 gate 内 Raw + KovaaK 窗口回放缓冲、Stats / Performance 事后 Run 切分、三种分析模式和核心闭环，不是完整 v1 | 无付费墙；用户自行配置可用 LLM provider；不引入商业推荐 |
| **v1 开源早期版** | 开源桌面应用；支持自动采集并生成待分析 Run、输入原生 flicking 基础诊断、独立手动 fallback、Windows Raw Input beta 与 Provider Settings | 全部产品能力免费；不销售订阅、credits 或托管 LLM 额度 |
| **B Coach 闭环深化** | 完善长期档案、训练计划、复测和产品命令，让 Coach 能解释、行动并跟进 | 继续开源免费；商业推荐不作为 Coach 闭环的前置条件 |
| **C 推荐与生态成熟** | tracking、国际化和经验证的外设目录逐步接通 | 官方购买链接可通过联盟代码获得佣金；必须清晰披露，且佣金不影响诊断、推荐触发或排序 |

> “免费”指 Aiming Cookie 不销售产品能力或 LLM 使用额度。用户选择的第三方 LLM provider 可能按其自身规则收费，该费用属于用户与 provider 的独立关系，不是 Aiming Cookie 收入。

### 5.3 产品能力优先关系（不等于当前施工队列）

产品能力按以下依赖关系演进；具体施工顺序、当前 Gate 与未来里程碑只在 `docs/ROADMAP.md` 维护。

1. **flicking 诊断闭环**：自动采集 Raw + KovaaK 窗口回放缓冲并事后切成独立 Run → 用户确认一条 Run → 输入原生/多源/视频 fallback 分析 → 确定性诊断 → 本地历史回访；未选择 Run 保留为待分析，手动 `MP4 + Stats` 作为独立 fallback；Coach 只在 provider 可用时引用结果并调用当前用户的产品能力；
2. **闭环可靠性与共同设计语言**：状态/失败/恢复、通知、日志、History 支撑，以及统一 token 和基础组件；
3. **视觉增强与 tracking 接通**：Raw Input 负责输入运动学；完成目标/准星/误差语义和真实阈值标定后，用视频与更多 tracking 数据增强同一产品闭环；
4. **运营与生态能力**：开源发布、Provider / 认证接入、显式导出 / 导入、经验证的外设目录、商业关系披露和联盟链接治理；
5. **远期扩展**：手部摄像头、多游戏和超出 KovaaK 输入合同的本地采集。

桌面应用不是排在远期的独立 feature，而是当前产品交付形态；installer、签名、公证、更新等发布工程的顺序由 Roadmap 决定。

### 5.4 发布形态

在完整 v1 前，可以提供 **受控环境中的 flicking-only 技术预览**，用于验证 KovaaK 进程 gate 内 Raw + 窗口回放缓冲 → Stats / Performance 事后切分 Run → 用户确认分析 → deterministic Report → 可选 Coach → 最小 History 回访的核心价值链；自动来源不足或运行在非 Windows 平台时，独立的 MP4 + Stats fallback 仍必须可用。

技术预览必须置于 VPN、SSO 或可信代理等环境访问控制后；这种预览保护不是产品账号系统。技术预览不代表 Desktop 安装包、外设推荐或 tracking 已完成。最小 History 至少包括列表、状态/摘要、分析回看和 terminal analysis 删除；趋势、对比、筛选与导入导出属于后续完整能力。

用户可见的 Coach 关系与消息不得只归属于可删除的 analysis session；删除分析只使引用变为已删除/不可用，不能删除 Coach 消息或长期档案。具体当前状态、下一里程碑和 Go/No-Go Gate 见 `docs/ROADMAP.md` 与 `docs/PROGRESS.md`。

### 5.5 开源免费与商业化边界

| 原则 | 说明 |
|---|---|
| **一个开源产品** | Aiming Cookie 只有一条产品身份和一套能力，不拆免费版 / 付费版，不用闭源高级版承载核心 Coach 能力 |
| **产品能力不收费** | 分析、确定性诊断、Coach、History、长期档案、训练计划和产品命令不设订阅、credits 或能力墙；未配置 provider 时没有 Coach 对话、AI 解释、长期档案维护、训练计划或 Coach 产品命令，但本地指标、确定性诊断、规则化提示和 History 继续可用 |
| **无产品账号** | Aiming Cookie 不要求注册、登录、云端用户身份、session/JWT、entitlement 或鉴权服务器；本地 profile 是 Desktop 数据和 Coach 关系的归属边界 |
| **Provider 成本与连接方式独立** | 用户自行选择并连接 LLM provider；Provider 本身可以没有账号或登录概念，连接方式可以是无需认证、本地 endpoint、API key、OAuth 或 device-code。需要认证时，它只属于用户与 Provider 的关系。第三方 provider 可能产生的费用由其直接定义，Aiming Cookie 不转售 token 或托管额度 |
| **Pi catalog 即产品 catalog** | pinned Pi 的内置 provider/model catalog 直接作为 Aiming Cookie 的产品目录，不另设 provider/model allow-list；所有 Pi 支持的 built-in 都应暴露并可用。自定义 OpenAI-compatible profile 由用户提供 provider name、base URL、API key 和 model ID |
| **联盟佣金是收入来源** | 当 Coach 有合理依据认为外设可能帮助用户解决问题时，可以提供可选商品推荐；官方购买链接可包含 Aiming Cookie 的联盟代码并产生佣金 |
| **商业防火墙** | 佣金、品牌合作、库存或转化率不得进入诊断、推荐触发或商品排序；推荐必须说明依据、不确定性、免费替代方案和商业关系 |
| **购买不是产品条件** | 用户不购买、忽略推荐或使用非联盟渠道，不得降低 Coach、分析或其他产品能力；证据不足时 Coach 应明确不建议购买 |

**教练与分析的关系（目标模型）**：

```text
用户 ⇄ 常驻 Aiming Coach（provider 可用；体验上像连续教练关系）
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

### 5.6 首次启动与默认路由

| 条件 | 默认落地 |
|---|---|
| 首次启动、尚未完成 onboarding | **激活 Coach / Provider onboarding**：先说明 Coach 价值、第三方 Provider 费用和数据边界；连接 Provider 是主路径，底部次级文字可跳过，并明确跳过后没有任何 Coach 功能 |
| onboarding 已完成、无分析历史且无已发现 Run | **新建分析**，先显示自动采集启用/待命状态与独立手动 fallback，不要求用户预先上传文件 |
| 有待分析 Run、其它 Run 或分析历史 | **历史**，顶部先显示待分析训练，下面区分其它训练记录和分析记录 |
| Provider 未配置或需要恢复 | 不阻塞本地 Analysis、History、指标、确定性报告和规则化提示；Coach 只显示可恢复的“连接 / 重新连接 Provider”激活入口，不提供 Coach 功能 |
| 已有记录且 Provider 可用 | 回访默认 History；保留用户上次 Coach 展开状态，并允许通过顶栏或内容 CTA 打开同一条长期 Coach 关系 |

### 5.7 输入与分析模式

产品不再把视频视为所有基础运动学指标的唯一事实源。分析根据可用输入选择模式：

| 模式 | 必需输入 | 主要产出 | 视频作用 |
|---|---|---|---|
| **输入原生模式（v1 Preview / Experimental）** | KovaaK Run（Stats / Performance）+ Windows Raw Input（用户 opt-in） | 当前已通过验证的输入运动学与事件对齐；完整 flick segmentation、核心 fair metrics 和正式阈值须通过算法与 Windows 实机 Gate 后才能解除 Preview | 可选；主要用于直观回放、问题发生位置、目标/准星证据和 Coach 可点击引用 |
| **多源可视化模式** | 输入原生模式 + MP4 | 输入原生结果仍是主事实；视频补充可视化定位和可验证的视觉证据 | 不重新定义输入运动学，不得静默覆盖 native 事实；视频失败只影响可视化/视觉证据 |
| **视频 compatibility fallback** | MP4 + Stats CSV | 在没有可用 Raw Input 时保留现有 CV pan trajectory、flick 分段和确定性诊断 | 兼容非 Windows、未开启 Raw Input 和旧工作流；不作为产品长期主分析方向 |

规则：

- 所有模式统一遵守最低条件 `Stats AND (MP4 OR (Raw + Performance))`；MP4 必须已经明确对应当前 Challenge，自动来源通常由 Performance 事后切窗，手动 fallback 由用户同时选择 MP4 与 Stats；
- Raw Input 只记录相对 `dx/dy`、时间戳和鼠标按钮；不采集键盘或桌面绝对坐标；
- Raw Input 默认关闭，首次开启必须明确告知用户本地采集范围、用途和关闭方式；
- 输入原生模式可以在没有 MP4 时生成当前已验证的基础运动学结果，但在 flick segmentation、核心 fair metrics、高 polling-rate correctness 与 Windows 实机 Gate 通过前，产品中持续标记为 Preview / Experimental；不能伪造目标相对误差、视觉反应时刻或视频证据；
- Performance / Stats 负责场景身份、挑战时间、击杀/命中事件和可用的目标配置；Raw Input 负责输入运动学；视频主要负责直观回放、问题定位和可验证的视觉证据；
- 自动采集不能依赖不存在的实时 Challenge hook；应用在 KovaaK 进程 gate 内连续采集 Raw，并把仅 KovaaK 窗口的硬件编码码流保留在最近 300 秒的有界回放缓冲中，再用稳定 Stats / Performance 事后把连续多局切成独立 Run；300 秒按墙上时间计算，但 v1 仅对 `Pause Count = 0` 的 normal/timescale-only Challenge 生成永久 Run-owned MP4；检测到 `Pause Count > 0` 时保留可诊断的 partial/unavailable evidence，不生成永久 MP4，也不把 Raw/Performance 标记为 canonical aligned；超出该范围或任一来源覆盖不完整时明确降级，不伪造完整 Raw / MP4；
- 单次只产生一条可分析 Run 时默认选中并等待用户确认；产生多条时用户必须选择一条开始分析，其余保留在 History 的待分析训练中，不进入 Tasks、不合并、不自动删除；
- 无法可靠对齐多个来源时，结果必须标记为部分证据或回退，不把推断写成测量；multimodal 的视觉校验失败不得抹掉已经完成的 native 结果，应保留 native 结果并将视觉证据标记为不可用；
- 非 Windows 平台必须明确显示 Raw Input 不可用，并保留视频 fallback。

## 6. 核心体验流程

### 6.1 首次旅程（onboarding）——所有用户

```
下载安装 → 启动（无需 Aiming Cookie 注册或登录）
  ↓
激活 Coach / Provider onboarding
  ├ 说明：Aiming Cookie 开源免费；第三方 Provider 可能收费
  ├ 说明：连接后可获得解释、针对性训练、长期跟进和复测
  ├ 说明：默认发送结构化诊断上下文，不自动发送 Raw Input 原始 trace
  ├ 主路径：选择 Pi catalog 中的 Provider/model，并按其能力直接连接、填写 API key、完成 OAuth/device-code 授权，或创建自定义 OpenAI-compatible profile
  └ 次级路径：底部文字“暂时只使用本地分析”（无 Coach；可稍后恢复 onboarding）
  ↓
启用 Desktop 自动采集并说明范围
  ├ KovaaK 进程出现 → 自动采集 Raw + 仅 KovaaK 窗口的有界回放缓冲
  ├ Stats / Performance 到达 → 事后切成独立 Run
  └ 自动采集不可用 → 进入独立 MP4 + Stats 手动 fallback
  ↓ 用户从 KovaaK 切回应用
选择本次 Run
  ├ 只有一条 → 默认选中，等待确认
  └ 两条及以上 → 用户选择一条；其它保留为待分析
  ↓ 点击“开始分析”进入 input-native / multimodal / video-fallback
processing（本地 runtime，可后台 / 可切走）
  ├ 教学时刻：指标科普 + 软件教学
  └ 切走兜底：空状态预告卡
  ↓ 完成时
全局 toast + 顶栏角标（不强制跳转）
  ↓
diagnosis_report（确定性诊断 / 无 LLM 仍完整）
  ├ 输入运动学 + 三层根因 + 指标 + 规则化处方 cues + 图
  ├ 明确显示证据来源：Raw Input / Performance / Stats / MP4
  └ 无可靠视觉证据时不显示或不声称目标相对误差类结论
  ↓
provider 可用 → 第一次分析完成后自动展开 Coach
  └ 观察 → 白话解释 → 证据 → 训练方法 → 预期变化 → 复测
provider 不可用 → 保留本地指标、确定性诊断、规则化提示和 History；显示“连接 Provider 以激活 Coach”
  ↓
history（待分析训练 + 训练记录 + 分析记录）
```

### 6.2 回访旅程（有分析历史）——所有用户

```
启动（无需产品登录）→ 有 Run 或分析历史 → History（默认页）
  ├ 顶部待分析训练：选择 Run 并开始分析
  ├ 训练记录列表：其它 Run、来源完整度、Raw / MP4 状态、分析状态
  ├ 分析记录列表 +（规划中）趋势
  ├ 大「新建分析」按钮
  └ provider 可用时：通过顶栏开关或内容 CTA 打开 Coach 侧栏
  ↓ 分支
看历史 / 新开分析 / 在当前工作区询问 Coach
```

无 Run 且无分析历史时回访仍落 **新建分析 / 训练来源选择**（与 §5.6 一致）。

### 6.3 关键状态
- **采集状态**：待命、采集中、整理中、完成和失败分别表达；可选托盘/悬浮状态不抢焦点，并可在 Settings 关闭
- **完成通知**：全局 toast + 顶栏角标（任意页可见，不强制跳转）；Run finalization、Raw/录像局部失败、分析完成和视频增强结果分别表达
- **空状态**：没有 Run 时解释如何启用自动采集、启动 KovaaK 或进入独立 MP4 + Stats fallback；没有 Raw 或 MP4 时说明当前是否满足最低分析条件
- **失败态**：进程检测、窗口录制、Raw Input、Stats/Performance、切窗、输入对齐、本地 CV / Provider LLM / 网络断分别写明白 + 重试或 fallback
- **删除**：进行中不可删；完成/失败可删分析，不默认删教练记忆（§5.5）

## 7. 功能边界

### v1（开源早期版）
- flicking 诊断（公平指标 + 三层根因 + 处方）
- KovaaK Run 自动发现与本地训练记录（Stats / Performance）
- Windows Desktop 自动采集 Raw Input 与仅 KovaaK 窗口的有界硬件编码回放缓冲，Stats / Performance 到达后事后切成独立待分析 Run
- Windows Raw Input opt-in 与输入原生 flicking 基础诊断；非 Windows 明确降级到视频 fallback
- 输入原生 / 多源增强 / 视频 fallback 三种分析模式，报告显示证据来源和缺失范围
- coach 对话（agent loop + KB）
- 首次 Provider onboarding：价值 / 成本 / 数据边界说明、连接、测试、跳过与恢复
- Settings 中填写、编辑、测试、选择和移除 LLM provider/model；完整暴露 pinned Pi built-in catalog，并支持由 provider name、base URL、API key、model ID 组成的自定义 OpenAI-compatible profile
- 本地 history（趋势 + 列表 + 删 / 导出 / 导入）
- Storage 显示总占用和 Run 录像、Raw trace、Analysis artifact、未完成采集数据的分类占用；用户手动管理，不静默自动清理 Run-owned evidence
- 无 Aiming Cookie 注册、登录、账号或产品鉴权服务器
- 开源发布，全部产品能力免费
- 完成通知 + 失败态
- 日志（本地 CV / agent / provider 请求各层）

### B 阶段（Coach 闭环深化）
- 长期表现 / 特点档案与跨上下文衔接
- ④ 渐进式训练计划接前端
- Coach 接通 History、证据定位、复测和训练计划等产品命令
- 本地长期档案的导出 / 导入与迁移恢复；不建立账号型云同步

### C 阶段（推荐与生态成熟）
- tracking 接通（指标命名、准星/误差语义和真实阈值标定完成后）
- 境外合规部署与大陆访问体验优化
- 经验证的外设目录、推荐解释和透明联盟链接；佣金不影响诊断、推荐触发或商品排序

### 远期（不并入当前，留扩展位）
- 手部摄像头（握姿 / 发力 / 微颤 / 疲劳）
- 跨平台录屏和通用鼠标采集；当前只承诺 Windows Desktop 的 KovaaK process-gated Raw Input 与 KovaaK 窗口录制
- 多游戏支持

## 8. 关键 UIUX 决策

> 产品级决策，实现细节在各 spec / plan。

| # | 决策 | 阶段 |
|---|---|---|
| 1 | 首次启动先进入 Provider onboarding；连接 Provider 是主路径。跳过入口是底部次级文字，hover 或键盘 focus 显示“跳过后没有 Coach，只保留本地指标、确定性诊断、规则化提示和 History”。完成 onboarding 后，无 Run/Analysis → 新建分析，有 → History | v1 |
| 2 | Desktop 主路径自动采集 Raw + KovaaK 窗口回放缓冲，并在 Stats / Performance 到达后事后切成独立 Run；手动 MP4 + Stats 是独立 fallback 界面，不与主路径混在一起 | v1 |
| 3 | processing 可后台；教学时刻 = 指标科普 + 软件教学；空状态给预告卡 | v1 |
| 4 | diagnosis_report 无 LLM 仍完整；Provider 可用时第一次分析完成后自动展开 Coach，后续启动记住用户的展开状态；未配置时提供可恢复的激活入口 | v1 |
| 5 | coach_dialogue 与其他产品能力永久不设订阅、credits 或能力墙；Settings 只管理用户选择的 provider、model 与认证状态，Aiming Cookie 不转售 LLM 额度 | 全阶段 |
| 6 | History 与 Coach 长期资料以本地 profile 为 canonical；支持删 / 导出 / 导入和显式迁移，不规划账号型云同步 | 全阶段 |
| 7 | 产品不提供注册、登录、Aiming Cookie 账号、session/JWT、entitlement 或用户鉴权服务器；Provider 可无需认证，也可使用其自身支持的认证方式，这些都不创建产品身份 | 全阶段 |
| 8 | 失败态：本地 CV / Provider LLM / 网络断分开写明白，Provider 故障不阻塞本地闭环 | v1 |
| 9 | 日志 cross-cutting：本地 CV / agent / Provider 请求各层分离，secret 不进入普通日志 | v1 |
| 10 | KovaaK Run 自动发现、Windows Raw Input opt-in 与仅 KovaaK 窗口自动录屏进入 v1；没有实时 Challenge hook 时使用进程 gate 连续硬件编码和 300 秒有界回放缓冲，并按 Stats / Performance 事后切窗；normal/timescale-only 生成永久 MP4，暂停局 fail closed | v1 |
| 11 | 分析完成：全局 toast + 顶栏角标，不强制跳转 | v1 |
| 12 | provider 可用时：分析完成即可被 Coach 引用；Coach 亦可发起不绑定单次分析的对话，并通过与 UI 相同的产品命令操作当前用户可用能力 | v1 |
| 13 | upload 视频/CSV 来源文件夹分别记忆：**非代码 bug**（Chromium 同 origin 共享目录记忆=浏览器原生行为；当前两个独立 input 已分别记忆）。强行隔离需 File System Access API（新功能）。**后续打包桌面应用不用浏览器，此问题目标形态不复现** | 不修（web 中间态；桌面形态消失）|
| 14 | 分析记录的删除不级联删除 Coach 记忆；Run 的源文件仍归用户。自动 MP4 / Raw trace 可按 Storage 合同由用户分别移除，Run metadata 整体删除、源文件失效和引用关系继续由 lifecycle spec/plan 冻结 | v1 目标 |
| 15 | 常驻 Coach 是 Agent 操作层；其产品能力与当前用户对齐，分析与表现档案为上下文工具。用户明确要求的普通可恢复动作可直接执行；删除、覆盖、上传/分享、打开外部购买链接或 Coach 自主推断的副作用动作必须确认 | v1 |
| 16 | MP4 在 input-native 路径中主要承担直观回放、问题定位和视觉证据；保留 video compatibility fallback，但不继续把 MP4 作为长期主运动学事实源 | v1 |
| 17 | Coach 只有在证据与用户上下文支持时才可推荐外设；每次商业推荐必须披露联盟关系，展示依据、不确定性和免费替代方案，佣金不得影响诊断、触发或排序 | C |
| 18 | 单局默认选中并等待确认；多局必须选择一条开始分析；其余 Run 保留为 History 顶部“待分析训练”，不进入 Tasks、不合并、不自动删除 | v1 |
| 19 | 自动 Raw 与自动 MP4 属于 Run-owned evidence。Settings 先显示分类存储占用，由用户分别手动管理；不启用自动 TTL、自动删除最旧 Run 或一键清空 | v1 |

## 9. 架构分工（桌面 hybrid）

| 层 | 位置 | 说明 |
|---|---|---|
| Capture Coordinator + KovaaK Run 解析 + 输入原生运动学 + 视频 pan_tracker/flick 指标计算 | **本地 sidecar / Tauri native layer** | 进程 gate 内统一采集 Raw 与 KovaaK 窗口回放缓冲，Stats / Performance 事后切 Run；输入原生路径优先，视频用于多源增强和 fallback |
| Coach Agent runtime（agent loop / tool registry / workspace / event stream） | **本地 sidecar** | 以项目内 Pi 源码为基线并允许产品化修改，不以持续兼容或跟随 Pi 上游升级为约束；Aiming Cookie 通过稳定领域工具、权限边界与持久 Coach 状态连接业务数据 |
| LLM 推理请求 | **用户选择的 Provider 或本地模型** | Desktop 直接使用用户配置的 Provider credential；Aiming Cookie 不销售 token、托管额度或中转用户请求 |
| Landing / release / 可选外设目录 | **无用户身份的在线表面** | 不保存用户 profile、Coach、History 或 credential，不建立产品账号和鉴权服务 |

> FastAPI、worker 和 Coach sidecar 服务本地产品能力；不得为了复用既有 Web 资产恢复产品账号、session/JWT、云端 LLM 代理或用户数据库。营销落地页、release 分发和未来外设目录与 Desktop 本地数据边界分离。

### 9.1 无鉴权服务器的在线表面

Aiming Cookie 不需要产品账号后端或用户鉴权服务器。官方在线表面只承担不依赖用户身份的发布与教学职责：

| 组件 | 部署 | 说明 |
|---|---|---|
| landing 落地页 | 静态托管 / CDN | 说明 Coach 价值、Provider 成本与数据边界，提供带字幕/文字步骤的演示和下载入口；不收集 credential |
| 桌面安装包分发 | 版本化对象存储或 release 托管 | 支持校验、回滚和目标地区可用性验证 |
| 可选外设目录 | 静态版本化数据或无身份服务 | 只提供商品事实、适配信息、商业披露和联盟链接，不保存用户训练档案 |
| 域名 | 合规注册与解析 | 不把规避备案作为产品目标 |

Provider OAuth/device-code 若被支持，必须通过经过审查的 Desktop/local callback 或 Provider 官方流程完成；不得以此为由建立 Aiming Cookie 用户账号、中心 credential broker 或鉴权服务器。具体托管、地区可用性和合规义务在发布前复核。

## 10. 成功标准

**技术预览成功**：
- 自动采集能够把连续 KovaaK Challenge 事后切成独立 Run；单局默认确认、多局选一条，其余待分析 Run 可在 History 找回；
- 满足 `Stats AND (MP4 OR (Raw + Performance))` 的 Run 可通过用户界面完成对应模式分析；自动采集不足或非 Windows 时，真实 MP4 + Stats CSV 仍可完成 video-fallback 分析、Report 和最小 History 回看；
- 异常退出不会留下无法恢复的永久进行中状态，失败可识别、可恢复或可重试；
- 无 LLM 时 deterministic diagnosis / prescription 仍完整可用；
- 分析和相关 managed 文件按规则删除，且不级联删除 Coach 消息或长期档案；
- Storage 显示分类占用，Run-owned 自动录像与 Raw trace 只由用户显式管理，不静默自动清理；
- 受控访问、真实素材 E2E、关键 Browser/Desktop 交互、build 和健康检查通过。

当前、客观的 Go/No-Go Gates 只在 `docs/ROADMAP.md` 维护。

**产品成功**：
- v1 阶段：开源发布获得活跃测试用户（具体规模待定），首次 Provider onboarding 与本地 fallback 的预期清晰，留存和反馈质量高
- 用户认可诊断准确（"这说的就是我"）+ coach 有用（"比我自己看指标懂多了"）
- B 阶段：Coach 能基于证据给出针对性训练方法，用户愿意执行并完成复测，长期档案能够支持后续调整
- C 阶段：外设推荐被用户认为相关、透明且可忽略；联盟收入来自有帮助的推荐，不以牺牲诊断信任或训练效果为代价

**技术成功**：
- 分析稳定（失败率可接受，具体阈值待真实数据校准）
- 性能（本地 CV ~160s 可接受）
- 指标可信（用户跨次比较有意义）
- 输入原生模式在有 Raw Input 时不依赖视频即可稳定生成基础运动学诊断；多源模式能明确区分 Raw Input、Performance、Stats 与 MP4 证据
- 没有 Raw Input、非 Windows 或用户拒绝授权时，视频 fallback 仍能完成原有 flicking 诊断
- Raw Input 的采集范围、授权状态、关闭方式和本地保留边界对用户清楚可见
- 自动录屏只捕获 KovaaK 窗口，Raw/MP4/Stats/Performance 的时间轴可追溯；连续多局不会被错误合并

## 11. 非目标（明确排除）

- Dashboard 独立页（合并进 history 趋势卡）
- Academy（有真实训练内容前不进导航）
- Aiming Cookie 产品账号、注册、登录、社交登录、session/JWT、entitlement 和用户鉴权服务器
- 多游戏（先 KovaaK's）
- 手部摄像头 v1（远期）
- Raw Input 目前不扩展到键盘、桌面绝对坐标、后台任意应用或非 KovaaK 进程
- Raw Input 不直接替代目标/准星视觉证据；没有可靠来源时不输出目标相对误差、视觉反应时刻等结论
- 桌面发布工程细节（Python bundling、installer、签名、公证、自动更新，另 plan）
- 订阅、credits、能力付费墙或 LLM 额度转售
- Benchmark、external progress、在线 Benchmark provider、外部身份和 leaderboard 不进入 v1 正式 UI；后端或研究能力存在不等于产品已交付

## 12. 约束与依赖

- **技术边界**：Raw Input / KovaaK 数据解析 + Python CV + 项目内 Pi-based Coach runtime + Next.js/React 前端 + FastAPI 服务 + Tauri 2 桌面壳；具体版本以依赖文件和 lockfile 为准
- **输入事实源分工**：Raw Input 测量用户输入运动学与真实鼠标按钮；Performance 提供 Challenge 时间窗和自动配对锚；Stats 提供场景、射击、击杀/命中事件和可用配置；MP4 提供视觉目标/准星/场景证据；任何单一来源都不得静默冒充其它来源
- **隐私与平台**：Raw Input 第一版仅 Windows、默认关闭、用户 opt-in、只在检测到 KovaaK 进程时采集、只保存本地，不上传云端或自动发送给 Coach；非 Windows 必须有可用 fallback
- **合规**：Landing、release 分发和可选外设目录按实际托管地区遵守适用要求；不把“规避备案”作为产品目标或表述，具体上线方案发布前复核
- **成本**：CV 本地运行以降低服务器成本；LLM 使用成本属于用户与其选择的 Provider 的独立关系；官方静态站、分发和外设目录成本在上线前按实际方案复核
- **大陆访问**：以合法合规的静态托管、release 分发和 CDN 方案优化体验，实际可用性需发布前验证

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

- **桌面 hybrid 而非纯 web**：省 CV 服务器成本 + 解并发；LLM 使用用户选择的 Provider 或本地模型；产品不建立账号、登录、鉴权服务器或账号型同步；资产演进不浪费
- **开源免费 + 透明联盟佣金**（2026-07-13）：分析、Coach、History、训练计划和产品命令不设付费墙；用户自行承担其所选第三方 provider 的可能费用。只有当证据与上下文支持外设可能成为限制时才可推荐商品，官方联盟链接可产生佣金，但佣金不得影响诊断、推荐触发或排序
- **History 与 Coach 档案长期本地优先**：本地 profile 是 canonical owner；导出 / 导入负责显式迁移，不建立账号型云同步
- **Provider-first onboarding，不是硬门槛**（2026-07-13）：首次启动先说明 Coach 价值、Provider 成本和数据边界；连接 Provider 是主路径，可明确跳过进入本地分析。第一次分析完成且 Provider 可用时自动展开 Coach；后续回访记住用户状态
- **Pi catalog 与本地 credential**（2026-07-13）：pinned Pi built-in provider/model catalog 就是产品 catalog，不维护 Aiming Cookie allow-list；支持自定义 OpenAI-compatible profile。API key 可作为 local-first 权衡明文保存在本地 SQLite/config，secure store 不是前置 Gate，但 secret 绝不进入 AnalysisResult、Coach 上下文/消息、普通日志、诊断或导出
- **v1 → B → C 分阶段**：v1 建立开源免费的基础闭环；B 深化 Coach 的长期档案、训练计划和复测能力；C 在保持信任边界的前提下接通经验证的外设目录与透明联盟链接
- **输入原生 flicking 先行、完整 tracking 后接**：Raw Input 先解决用户输入运动学事实源，flicking 基础诊断可以脱离视频运行；目标/准星/误差语义和真实阈值标定完成后，再用视频和更多 tracking 数据接通完整 tracking
- **按依赖演进而非删 feature**：保留 flicking、tracking、桌面 hybrid、显式导出/导入、长期趋势和推荐生态等完整路线；先验证 flicking 与 Coach 闭环及其可靠性，再接 tracking 与运营能力
- **统一设计系统先于全面美化**：可执行 token 集中于前端；页面不得各自硬编码视觉值。现有设计稿和 Stitch 产物保留为参考，不与运行时代码争夺事实源
- **技术预览不冒充完整 v1**：受控预览只验证 flicking 核心闭环；长期完整产品范围不变，发布日期由 Roadmap Gates 和真实验证决定
- **职责边界**：Domain Core 保持确定性；Local Analysis Runtime 负责 job、文件和本地 History；Coach Agent Runtime 负责本地长期关系、工具编排与交互事件；用户选择的 Provider 负责 LLM 推理；在线表面只承担 Landing、release 分发和无身份外设目录
- **单一开源免费且无账号的产品**：不存在免费/付费两套产品、能力墙、注册、登录或产品鉴权服务器。首次启动先进入 Provider onboarding；购买外设与否不影响任何产品能力
- **教练与分析**：目标上教练可跨次、可不绑单次分析；分析/表现档案是上下文与病历。过渡实现可挂在分析 session 下，终局不锁死为「对话从属于分析」
- **删除分析不抹教练记忆**：进行中不可删；删完成分析只去该次产物与输入文件
- **常驻 Coach 是 Agent 操作层**：provider 可用时 agent 可随时进入、调用稳定的应用工具，减少用户对多页面流程的记忆负担；项目内 Pi 源码是 Coach runtime 基线，项目可直接修改且不承诺跟随上游升级；已有可用的 workspace、权限或 sandbox 能力优先保留，不无证据重写
- **持久表现档案 + 上下文衔接**：支撑长教练关系体验；窗口顶满后的 session 衔接另研究，不在本条锁实现
- **输入原生分析提前进入产品，但首发保持 Preview / Experimental**（2026-07-13）：Raw Input 不再只是远期采集基础设施。KovaaK Run + Performance / Stats + Windows Raw Input 组成 v1 的输入原生 flicking 预览路径；视频降级为可选视觉增强或无 Raw Input 时的 fallback。完整 flick segmentation、核心 fair metrics、高 polling-rate correctness 与 Windows 实机 Gate 通过后，才能解除 Preview。基础运动学、目标语义和视觉证据必须按来源分层，不把 Raw Input 过度解释为完整视觉测量。
- **自动采集统一主路径**（2026-07-18，2026-07-19 暂停裁决）：应用不依赖实时 Challenge hook，而是在 KovaaK 进程 gate 内统一采集 Raw Input，并以硬件编码维护仅 KovaaK 窗口最近 300 秒的有界回放缓冲；Stats / Performance 到达后按 Challenge 时间窗事后切成独立 Run。v1 仅为 `Pause Count = 0` 的 normal/timescale-only Challenge 生成永久 MP4；`Pause Count > 0` 的暂停局 fail closed，证据只能保留为 partial/unavailable，不能声明 canonical Raw/Performance 对齐。超过 300 秒、长时间中断或来源覆盖不完整时明确降级。Analysis 最低条件为 `Stats AND (MP4 OR (Raw + Performance))`；单局确认、多局选一条，其余保留待分析；手动 `MP4 + Stats` 是独立 fallback。
- **Run-owned evidence 由用户手动管理**（2026-07-17）：自动 Raw 和自动切分 MP4 随 Run 保存，不随 Analysis 删除。Settings 显示分类占用，用户可分别移除大体积 evidence；不静默自动清理、不自动删除最旧 Run，也不连带删除 Run metadata、Analysis 或用户源文件。
