# Aiming Cookie — 产品需求文档 (PRD)

## 2026-08-10 首发产品合同（当前生效）

本节是首发前最后一次产品重定，覆盖本文中与其冲突的旧预览、fallback 和迁移描述：

- Aiming Cookie 是 Provider-backed Agent 产品。首次 onboarding 不允许跳过 Provider；Provider 连接并测试成功、Windows 自动采集授权并启用后，才能进入 Coach、History 和 Settings 主工作区。
- 首发不迁移或展示安装前已有的 KovaaK Stats/Performance 文件；它们不含 Aiming Cookie 的 Raw Input 与受管 MP4，不进入 Run 或 Analysis。
- 新训练由 Coach 自动选择最高质量的可用分析路径：`multimodal`（Stats + Performance + Raw Input + managed MP4 + canonical window）→ `input_native`（Stats + Performance + Raw Input + canonical window）→ `video_fallback`（Stats + managed MP4）。用户不手动选择模式。
- fallback 是正式可用的有限分析，不是伪造的完整结果。每个结果必须显示缺失来源和限制，并提示修复采集以获得更高质量分析；三条路径均不可用时，Coach 说明本局不能分析及下一步。
- 多局练习中的每个可用 Run 都可独立分析；跨局比较只使用双方共同具备且已验证的指标，不把证据等级差异写成疲劳或瞄准变化。
- Provider 后续失效不把用户强制送回 onboarding；Coach 对话显示错误并引导 Settings 修复或新增 Provider。Provider fallback 暂不属于首发合同。

> **文档定位** · 建立 2026-07-08
> 这是 Aiming Cookie 的**方向锚** + **原始设想记录**。所有下游文档（spec / plan / 各子系统设计）从此派生。多轮 spec/plan 迭代若与本文冲突，**以本文为准**；本文过时则更新本文，不在下游打补丁。
>
> **维护原则**：产品级决策回写本文；子系统实现细节留在各 spec/plan。

### 2026-08-10 Coach-first IA/UI 同步

本节只同步已确认的 Coach-first 信息架构，不新增分析能力或改变数据合同：Coach 是默认主工作区，`/` 为 Coach 首页，`/s?sessionId=<id>` 为指定会话；左侧 Session rail 是唯一主导航，用户可见的一级消费面只有 Coach、History 与 Settings。Tasks 和独立 Analysis 页面不再是产品页面，旧 URL 只作有界兼容跳转。Analysis 仍是本地、owner-scoped 的内部数据对象：History 可按需显示安全摘要或把所选记录交给 Coach；指标、时间线和视频证据只在 Coach 解释需要时以确定性卡片出现。无视频时对话保持可读的居中最大宽度；打开视频证据时才进入 Session rail + 中央视频 + 右侧 Coach 的三栏形态。

桌面产品只支持正常窗口与最大化/全屏使用，Tauri 最小内容宽度为 `1180px`，不为更窄窗口维护另一套导航或 Coach 布局。History 与 Settings 的主要消费内容最大宽度为 `1040px` 并在可用区域居中，避免全屏时横向拉散。

以下较早的“右侧 Coach 侧栏 / 顶部导航 / 单主会话”文字仅保留作决策演进记录；涉及当前 IA、路由或布局时，以本同步段和 [`frontend-uiux-design.md`](frontend-uiux-design.md) 为准。

---

## 1. 产品一句话

基于物理 + 运动学的 **KovaaK's 瞄准诊断 + AI 教练**。桌面应用：采集真实训练输入 → 完整动作级数据与公平指标 → 可追溯候选诊断 → AI Coach 综合解释与训练 → 长期进步追踪。

**产品关系（2026-07-10 澄清；2026-07-13 输入原生与商业模式更新）**：这是以 **Aiming Coach** 为核心的一个开源免费产品，不分免费版 / 付费版，也不以订阅、credits 或能力墙向用户收费。长期上，**Aiming Coach 是常驻关系与产品操作层**——降低用户对界面与流程的学习成本；KovaaK Run、输入原生运动学、视频增强、确定性报告和持久化的瞄准表现记录，都是教练的客观观测、专业判断依据与长期病历，而不是与教练并列的第二套产品。

## 2. 为什么做（原始设想）

**创始人的痛**：点点自己是 KovaaK's 玩家（DPI 1600 / 51cm per 360° / FOV 103），苦于瞄准训练缺乏**客观、可量化、个性化**的诊断。现有方案两层都是主观的：社区只有主观体感交流（"今天手感好""感觉甩过了"），没有数据化反馈；**个人教练的教学也凭经验感觉，无法量化**——两层都缺客观数据。学术运动科学有成熟指标（SPARC、Fitts throughput、submovement）但没人产品化给玩家。Aiming Cookie 凭运动学数据做诊断，比主观经验更客观、更科学。

**核心信念**：
1. **公平指标**——跨距离/速度可比的运动学量（decel_frac / SPARC / linearity / throughput），比"命中率"更诚实地暴露问题
2. **减速段是诊断核心**——flick 的"刹车"段最能反映控制质量（社区 Zeonlo / Bardpill + 神经科学 Becker 2020 交叉验证）
3. **AI 教练做个性化综合诊断**——测量与完整的动作级 processed data 是骨架，规则层提供候选观察；教练必须结合支持证据、反例、历史和知识体系判断"你具体哪里有问题、为什么、怎么练"，而不是复述规则结论
4. **开源免费，信任优先**——产品能力不设付费墙；只有当证据与用户上下文支持外设可能成为限制时，Coach 才可提供可选推荐。官方购买链接可以包含联盟代码并产生佣金，但佣金不得影响诊断、推荐触发或商品排序，商业关系必须清晰披露

**张力感知的演变**（理论诚实记录）：早期设想从录像推断"手部张力"（PTC, Pure Tension Coeff），后经审视确认**不成立**——PTC 实为 miss-frame 加速度-误差密度，不直接测肌肉张力。当前产品以减速段质量（SPARC 等）为核心诊断，手部张力留待远期手部摄像头验证。相关历史设计已归档，不再作为产品合同。

## 3. 为谁

**核心用户**：大陆为主、希望用真实训练输入获得客观诊断的认真 KovaaK's 玩家。首发覆盖 static/dynamic clicking、continuous tracking 和 target switching；movement aiming 在缺少玩家移动遥测时只提供 outcome-only 观察，不生成移动机制诊断。Windows Desktop 经用户明确启用后，在 KovaaK 进程 gate 内统一采集 Raw Input，并维护 KovaaK 窗口的有界硬件编码回放缓冲；Stats / Performance 到达后事后切成独立 Run，用户不需要在主路径中手动录屏、匹配 CSV 或切分连续多局。

**扩展（后续阶段）**：
- 国际用户（Phase 3+）
- 远期：愿装手部摄像头的深度用户

**不为**：只想获得与自身训练无关的通用聊天建议、又不愿提供任何可分析训练证据的用户。自动采集主路径的目标正是消除手动录屏、CSV 配对和切片负担。

## 4. 核心价值主张

| # | 价值 | 实现 |
|---|---|---|
| 1 | 公平指标 | decel_frac / SPARC / linearity / throughput / reverse_ratio / path_efficiency 等（学术锚点：Balasubramanian 2012 / Fitts / Novak 2002） |
| 2 | 三层候选诊断 | 观察 → 候选机制/替代解释 → 处方（规则引擎 `advice.py` / `advice_tracking.py`）；不把未测因果升级为事实 |
| 3 | AI 教练对话 | 可调用应用能力的常驻 Coach；能查询整局完整动作级 processed data、寻找规律与反例并形成最终教学解释，而不是确定性报告的转述层 |
| 4 | 长期进步追踪 | 趋势 + ④ 渐进式训练计划（`progress.py` / `planning.py`） |
| 5 | 可配置 LLM Coach | 当前产品不设付费墙；用户在 Settings 中选择并连接可用 LLM provider，Coach 与确定性诊断属于同一产品闭环 |
| 6 | 常驻教练降学习成本 | provider 可用时 coach agent 可随时进入并调用当前用户拥有的产品能力；用户少记「该点哪个菜单」，多靠对话完成回访、分析与计划 |
| 7 | 输入原生而非视频依赖 | Raw Input + KovaaK Performance / Stats 直接生成输入运动学；MP4 主要用于直观回放、问题定位和视觉证据，不是基础运动学的主事实源 |

## 5. 产品形态与阶段

### 5.1 形态
- **开源免费的桌面 hybrid 应用**：当前技术基线为 Tauri 2 壳 + 本地分析 runtime（Raw Input / KovaaK 数据解析 / Python CV）+ Coach Agent runtime（以项目内 Pi 源码为基线，由项目接管并产品化改造）+ 用户自行选择并连接的 LLM provider。具体开源许可证与发布义务在 release 准备中单独确认，不改变产品能力免费开放的方向。
- **Web 技术开发、桌面应用交付**：当前可用 Web 前端快速开发和验证，但最终界面按本地桌面应用而不是网站设计；营销落地页与应用分离，应用 Logo 仅作静态品牌标识，不承担导航。
- **Coach-first 工作区 + 左侧会话 rail**：Coach 是默认主工作区；`/` 进入 Coach 首页，`/s/:sessionId` 打开指定会话。左侧 Session rail 负责会话、History 与 Settings 导航；顶部只承载应用状态，不再提供右侧 Coach 开关。
- **无产品账号的本地优先工具**：画像、Coach 关系与 History 都属于当前 OS 用户的本地 profile。Aiming Cookie 不要求注册、登录或产品鉴权服务器；Provider 是否需要认证由其自身决定；如需认证，只发生在用户与其选择的模型服务之间。

### 5.2 分阶段
| 阶段 | 形态 | 商业模式 |
|---|---|---|
| **内部技术预览** | 受控环境，flicking-only；验证进程 gate 内 Raw + KovaaK 窗口回放缓冲、Stats / Performance 事后 Run 切分、三种分析模式和核心闭环，不是完整 v1 | 无付费墙；用户自行配置可用 LLM provider；不引入商业推荐 |
| **v1 开源早期版** | 开源桌面应用；支持自动采集并生成待分析 Run、static/dynamic clicking、continuous tracking 与 target switching 的完整 Coach 闭环、独立手动 fallback、Windows Raw Input beta 与 Provider Settings；movement aiming 无移动遥测时保持 outcome-only | 全部产品能力免费；不销售订阅、credits 或托管 LLM 额度 |
| **B Coach 闭环深化** | 在首发闭环之上迭代档案、训练计划、复测和产品命令的质量与覆盖 | 继续开源免费；商业推荐不作为 Coach 闭环的前置条件 |
| **C 推荐与生态成熟** | 国际化和经验证的外设目录逐步接通 | 官方购买链接可通过联盟代码获得佣金；必须清晰披露，且佣金不影响诊断、推荐触发或排序 |

> “免费”指 Aiming Cookie 不销售产品能力或 LLM 使用额度。用户选择的第三方 LLM provider 可能按其自身规则收费，该费用属于用户与 provider 的独立关系，不是 Aiming Cookie 收入。

### 5.3 产品能力优先关系（不等于当前施工队列）

产品能力按以下依赖关系演进；具体施工顺序、当前 Gate 与未来里程碑只在 `docs/ROADMAP.md` 维护。

1. **完整 Coach 诊断闭环**：自动采集 Raw + KovaaK 窗口回放缓冲并事后切成独立 Run → 用户确认一条 Run → static/dynamic clicking、continuous tracking 或 target switching 的专项分析 → 完整动作级 processed data、指标与候选诊断 → Coach 基于支持证据和反例综合解释 → 本地历史、计划与复测；movement aiming 没有移动遥测时只保留 outcome-only；未选择 Run 保留为待分析，手动 `MP4 + Stats` 作为独立 fallback；
2. **闭环可靠性与共同设计语言**：状态/失败/恢复、通知、日志、History 支撑，以及统一 token 和基础组件；
3. **本地视觉预处理与质量 Gate**：Raw Input 负责输入运动学；当前 MP4 仅在本地确定性预处理为目标、准星、误差和事件数值证据，并受质量 Gate 约束。未来若让视觉模型读取片段，必须另立版本化、显式授权且有预算上限的合同；
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
- Coach 是产品的 **Agent 操作层**：分析、History、趋势、报告、训练目标和后续应用操作是它可调用的工具能力；它不是 Report 旁的只读聊天页。当前前端以 Coach 主工作区承载这条关系，左侧 Session rail 是唯一主导航；安全的 Analysis 指标、时间线和证据可以作为 Coach 消息卡片出现，视频讲解时才在 rail 右侧形成视频 + 对话并行区域。
- **操作授权与代办**：用户在当前 Coach 消息中对某个已注册产品操作给出明确、无歧义的自然语言指令时，该指令就是这一次操作的直接授权；包括删除在内的有后果操作不再要求第二次确认。Coach 自主建议、从上下文推断或参数仍有歧义的有后果操作仍须先说明影响并等待确认或澄清。API key、OAuth/device-code、系统与隐私权限、文件选择、现实训练和主观事实仍由用户亲自完成或明确陈述；Coach 负责准备、导航、等待和验证，不得代填、代答或推断。
- 所有代办仍只通过与 UI 共用的已注册产品命令执行，并保留 owner scope、稳定引用、能力校验、幂等、审计与隐私边界；采用 Pi 不等于开放 shell、filesystem、任意 network、DOM 或系统控制权限。
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
| 首次启动、尚未完成 onboarding | **激活 Coach / Provider onboarding**：先说明 Coach 价值、第三方 Provider 费用和数据边界；连接 Provider 是必须完成的主路径 |
| onboarding 已完成、无分析历史且无已发现 Run | **进入 Coach 主工作区**，显示自动采集启用/待命状态；不提供 Provider-less 分析入口 |
| 有待分析 Run、其它 Run 或分析历史 | **历史**，顶部先显示待分析训练，下面区分其它训练记录和分析记录 |
| Provider 未配置或需要恢复 | 采集与已有 Run/History 记录可以保留，但不创建 Provider-less Analysis、确定性报告或 Coach 回答；Coach 只显示可恢复的“连接 / 重新连接 Provider”入口 |
| 已有记录且 Provider 可用 | 回访进入 History 或指定 Coach 会话；保留会话与草稿状态，Coach 不以独立右侧开关作为入口 |

### 5.7 输入与分析模式

产品不再让用户在分析模式之间选择。历史记录仍可展示旧 mode，但新 Run 只进入固定多源 pipeline：

| 模式 | 必需输入 | 主要产出 | 视频作用 |
|---|---|---|---|
| **固定多源 multimodal** | KovaaK Run（Stats + Performance）+ Windows Raw Input + managed MP4 + canonical window | Provider-backed Coach 消费完整来源；视觉失败保留 Run failed/incomplete 记录，不生成替代分析 | 用于可点击回放和证据定位，不覆盖输入事实 |

规则：

- 新 Run 统一要求 `Stats + Performance + Raw Input + MP4 + canonical window`；MP4 必须已经明确对应当前 Challenge，缺任一来源时不入队；
- Raw Input 只记录相对 `dx/dy`、时间戳和鼠标按钮；不采集键盘或桌面绝对坐标；
- Aiming Cookie 不修改鼠标硬件 polling rate；无论设备以何种 polling rate 上报，Raw Input 的 canonical 运动时间粒度固定为 1 ms、最高 1000 Hz。同一毫秒内的 `dx/dy` 分别累加为至多一条运动记录，不生成补零记录；鼠标按钮按下/抬起边沿不受该上限约束并保持顺序。该归一化保留每毫秒 X/Y 净位移，但有意不保留亚毫秒路径形状；产品不得把它描述为硬件 polling rate 测量或亚毫秒运动证据；
- Raw Input 默认关闭，首次开启必须明确告知用户本地采集范围、用途和关闭方式；
- 缺 MP4、Raw Input、Performance 或 canonical window 时只保留 incomplete Run；不能生成替代分析，也不能伪造目标相对误差、视觉反应时刻或视频证据；
- Performance / Stats 负责场景身份、挑战时间、击杀/命中事件和可用的目标配置；Raw Input 负责输入运动学；视频主要负责直观回放、问题定位和可验证的视觉证据；
- 自动采集不能依赖不存在的实时 Challenge hook；应用在 KovaaK 进程 gate 内连续采集 Raw，并把仅 KovaaK 窗口的硬件编码码流保留在最近 300 秒的有界回放缓冲中，再用稳定 Stats / Performance 事后把连续多局切成独立 Run；300 秒按墙上时间计算，但 v1 仅对 `Pause Count = 0` 的 normal/timescale-only Challenge 生成永久 Run-owned MP4；检测到 `Pause Count > 0` 时保留可诊断的 partial/unavailable evidence，不生成永久 MP4，也不把 Raw/Performance 标记为 canonical aligned；超出该范围或任一来源覆盖不完整时明确降级，不伪造完整 Raw / MP4；
- 单次只产生一条可分析 Run 时默认选中并等待用户确认；产生多条时用户必须选择一条开始分析，其余保留在 History 的待分析训练中，不进入 Tasks、不合并、不自动删除；
- 无法可靠对齐多个来源时，保留 Run 的 incomplete/failed 记录并说明缺失原因，不把推断写成测量；固定多源分析的视觉校验失败不得生成 native 或 video fallback 替代结果；
- 新 KovaaK 场景应优先由本机可验证的场景结构和已冻结的 Challenge 配置自动识别训练类型；场景名称只能作为候选提示，不能单独决定分析器。训练类型识别与精确场景档案/视觉标定分离：前者允许进入该类型已验证的基础 native 分析，后者才解锁目标相对误差、目标身份或速度、命中关联和场景专属训练处方。结构证据不足时保留训练记录并说明限制，不得借用相似场景的精确结论或把未知类型伪装成可诊断结果；
- 非 Windows 平台必须明确显示 Raw Input 不可用；由于固定多源合同缺少 Raw Input 时不创建新的 Analysis。

### Current Coach Run analysis gate

New Run-based Analysis created from the Coach flow uses one fixed `multimodal`
contract. A Run is analysis-ready only when Stats, Performance (`.perf`), Raw
Input, managed KovaaK-window video, and a resolved canonical time window are all
available and valid. The source gate returns bounded missing-source codes and
never exposes paths or raw payloads.

Capture-pending and analysis-ready are separate states. A Run with incomplete
sources remains retained as a pending/incomplete capture record with explicit
missing reasons; it is not enqueued and cannot silently become input-native or
video-fallback. Existing historical Analysis rows and their recorded modes
remain readable, but those modes are not new Run creation choices in the
Coach-first pipeline.

## 6. 核心体验流程

### 6.1 首次旅程（onboarding）——所有用户

```
下载安装 → 启动（无需 Aiming Cookie 注册或登录）
  ↓
激活 Coach / Provider onboarding
  ├ 说明：Aiming Cookie 开源免费；第三方 Provider 可能收费
  ├ 说明：连接后可获得解释、针对性训练、长期跟进和复测
  ├ 说明：可向所选 Provider 发送 L1-L3 规范化事实、证据与诊断上下文；不发送 Raw trace、MP4、原始 CSV / protobuf 或私有 parser payload
  ├ 主路径：选择 Pi catalog 中的 Provider/model，并按其能力直接连接、填写 API key、完成 OAuth/device-code 授权，或创建自定义 OpenAI-compatible profile
  └ Provider 未完成时停留在 onboarding，不进入 Provider-less 分析
  ↓
启用 Desktop 自动采集并说明范围
  ├ KovaaK 进程出现 → 自动采集 Raw + 仅 KovaaK 窗口的有界回放缓冲
  ├ Stats / Performance 到达 → 事后切成独立 Run
  └ 任一必需来源不可用 → 保留 incomplete Run，等待来源恢复或明确失败
  ↓ 用户从 KovaaK 切回应用
选择本次 Run
  ├ 只有一条 → 默认选中，等待确认
  └ 两条及以上 → 用户选择一条；其它保留为待分析
  ↓ 点击“开始分析”进入唯一 multimodal pipeline（Stats + Performance + Raw Input + MP4）
processing（本地 runtime，可后台 / 可切走）
  ├ 教学时刻：指标科普 + 软件教学
  └ 切走兜底：空状态预告卡
  ↓ 完成时
全局 toast + 顶栏角标（不强制跳转）
  ↓
Coach-backed diagnosis（Provider 可用后生成；Run/失败记录仍保留）
  ├ 输入运动学 + 动作级数据概况 + 候选机制/替代解释 + 指标 + 规则化处方 cues + 图
  ├ 明确显示证据来源：Raw Input / Performance / Stats / MP4
  └ 无可靠视觉证据时不显示或不声称目标相对误差类结论
  ↓
provider 可用 → 第一次分析完成后自动展开 Coach
  └ 检查整局动作与反例 → 观察 → 白话解释 → 证据 → 训练方法 → 预期变化 → 复测
provider 不可用 → 保留本地指标、确定性诊断、规则化提示和 History；显示“连接 Provider 以激活 Coach”
  ↓
history（待分析训练 + 训练记录 + 分析记录）
```

### 6.2 回访旅程（有分析历史）——所有用户

```
启动（无需产品登录）→ 有 Run 或分析历史 → History（从左侧 Session rail 进入）
  ├ 顶部待分析训练：选择 Run 并开始分析
  ├ 训练记录列表：其它 Run、来源完整度、Raw / MP4 状态、分析状态
  ├ 分析记录列表 +（规划中）趋势
  ├ 分析记录可在本页查看摘要或选择后交给 Coach
  └ provider 可用时：从左侧 Session rail 进入 Coach 会话，或由上下文动作定位到当前会话
  ↓ 分支
看历史 / 从 rail 新建草稿 / 在 Coach 主工作区继续对话
```

无 Run 且无分析历史时回访进入 **Coach 主工作区**，由 Coach 说明采集待命状态与下一步（与 §5.6 一致）。

### 6.3 关键状态
- **采集状态**：待命、采集中、整理中、完成和失败分别表达；可选托盘/悬浮状态不抢焦点，并可在 Settings 关闭
- **完成通知**：全局 toast + 顶栏角标（任意页可见，不强制跳转）；Run finalization、Raw/录像局部失败、分析完成和视频增强结果分别表达
- **空状态**：没有 Run 时解释如何启用自动采集、启动 KovaaK 或进入独立 MP4 + Stats fallback；没有 Raw 或 MP4 时说明当前是否满足最低分析条件
- **失败态**：进程检测、窗口录制、Raw Input、Stats/Performance、切窗、输入对齐、本地 CV / Provider LLM / 网络断分别写明白 + 重试或 fallback
- **删除**：进行中不可删；完成/失败可删分析，不默认删教练记忆（§5.5）

## 7. 功能边界

### v1（开源早期版）
- static/dynamic clicking、continuous tracking 与 target switching 的专项分析（完整动作级 processed data + 公平指标 + 候选诊断 + Coach 综合解释与处方）；movement aiming 缺少玩家移动遥测时只提供 outcome-only
- KovaaK Run 自动发现与本地训练记录（Stats / Performance）
- Windows Desktop 自动采集 Raw Input 与仅 KovaaK 窗口的有界硬件编码回放缓冲，Stats / Performance 到达后事后切成独立待分析 Run
- Windows Raw Input opt-in 与输入原生 flicking 基础诊断；非 Windows 明确降级到视频 fallback
- 输入原生 / 多源增强 / 视频 fallback 三种分析模式，报告显示证据来源和缺失范围
- coach 对话、长期档案、训练计划与复测（agent loop + KB）；Provider 可用后，L1-L3 的 bounded 规范化 facts/evidence 可作为普通 Coach context，不增加逐 Run consent
- 首次 Provider onboarding：价值 / 成本 / 数据边界说明、连接、测试、跳过与恢复
- Settings 中填写、编辑、测试、选择和移除 LLM provider/model；完整暴露 pinned Pi built-in catalog，并支持由 provider name、base URL、API key、model ID 组成的自定义 OpenAI-compatible profile
- 本地 history（趋势 + 列表 + 删 / 导出 / 导入）
- Storage 显示总占用和 Run 录像、Raw trace、Analysis artifact、未完成采集数据的分类占用；用户手动管理，不静默自动清理 Run-owned evidence
- 无 Aiming Cookie 注册、登录、账号或产品鉴权服务器
- 开源发布，全部产品能力免费
- 完成通知 + 失败态
- 日志（本地 CV / agent / provider 请求各层）

### B 阶段（Coach 闭环深化）
- 长期表现 / 特点档案与跨上下文衔接的质量提升
- 训练计划、证据定位和复测体验深化
- 本地长期档案的导出 / 导入与迁移恢复；不建立账号型云同步

### C 阶段（推荐与生态成熟）
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
| 1 | 首次启动先进入 Provider onboarding；连接 Provider 是主路径。完成 onboarding 后，无 Run/Analysis → Coach 主工作区，有待处理记录 → History；不再进入独立新建分析页面 | v1 |
| 2 | Desktop 主路径自动采集 Raw + KovaaK 窗口回放缓冲，并在 Stats / Performance 到达后事后切成独立 Run；手动 MP4 + Stats 是独立 fallback 界面，不与主路径混在一起 | v1 |
| 3 | processing 可后台；教学时刻 = 指标科普 + 软件教学；空状态给预告卡 | v1 |
| 4 | Analysis 作为内部本地对象保留；用户通过 History 安全摘要或 Coach 解释消费结果。Provider 可用时 Coach 可在消息中发出确定性的指标、时间线和证据卡片，未配置时提供可恢复的激活入口 | v1 |
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
| 15 | 常驻 Coach 是 Agent 操作层；其产品能力与当前用户对齐，分析与表现档案为上下文工具。用户当前消息明确、无歧义要求的已注册产品操作可直接执行，包括删除等有后果操作；Coach 自主推断/提议的有后果操作仍须确认，secret/授权/文件选择/现实与主观事实仍由用户完成 | v1 |
| 16 | MP4 在 input-native 路径中主要承担直观回放、问题定位和视觉证据；保留 video compatibility fallback，但不继续把 MP4 作为长期主运动学事实源 | v1 |
| 17 | Coach 只有在证据与用户上下文支持时才可推荐外设；每次商业推荐必须披露联盟关系，展示依据、不确定性和免费替代方案，佣金不得影响诊断、触发或排序 | C |
| 18 | 单局默认选中并等待确认；多局必须选择一条开始分析；其余 Run 保留为 History 顶部“待分析训练”，不进入 Tasks、不合并、不自动删除 | v1 |
| 19 | 自动 Raw 与自动 MP4 属于 Run-owned evidence。Settings 先显示分类存储占用，由用户分别手动管理；不启用自动 TTL、自动删除最旧 Run 或一键清空 | v1 |
| 20 | 用户可见一级消费面只保留 Coach、History 与 Settings；Tasks 和独立 Analysis 页面退役，旧 URL 跳转 History。桌面最小内容宽度 `1180px`，History/Settings 内容最大宽度 `1040px` 并居中 | v1 |

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
- 只有同时满足 `Stats + Performance + Raw Input + MP4 + canonical window` 的 Run 才能进入固定 multimodal Analysis；缺任一来源只保留失败/待补齐记录；
- 异常退出不会留下无法恢复的永久进行中状态，失败可识别、可恢复或可重试；
- 无 LLM 时确定性测量、候选观察和规则化 prescription 仍完整可用；
- 分析和相关 managed 文件按规则删除，且不级联删除 Coach 消息或长期档案；
- Storage 显示分类占用，Run-owned 自动录像与 Raw trace 只由用户显式管理，不静默自动清理；
- 受控访问、真实素材 E2E、关键 Browser/Desktop 交互、build 和健康检查通过。

当前、客观的 Go/No-Go Gates 只在 `docs/ROADMAP.md` 维护。

**产品成功**：
- v1 阶段：首发四类目标 aim family（static clicking、dynamic clicking、continuous tracking、target switching）均通过各自分析、质量与知识 Gate；Provider onboarding 和固定多源采集门槛清晰，留存和反馈质量高
- 用户认可诊断准确（"这说的就是我"）+ Coach 能从完整动作数据中主动发现规律、反例和优先问题（"比我自己看指标和规则报告懂多了"）
- B 阶段：Coach 能基于证据给出针对性训练方法，用户愿意执行并完成复测，长期档案能够支持后续调整
- C 阶段：外设推荐被用户认为相关、透明且可忽略；联盟收入来自有帮助的推荐，不以牺牲诊断信任或训练效果为代价

**技术成功**：
- 分析稳定（失败率可接受，具体阈值待真实数据校准）
- 性能（本地 CV ~160s 可接受）
- 指标可信（用户跨次比较有意义）
- 固定 multimodal Run 必须同时具备 Raw Input、Performance、Stats、MP4 与 canonical window；target-relative claims 仅在本地预处理的视觉质量 Gate 通过后生成；Coach 能明确区分四类证据
- 没有 Raw Input、非 Windows 或用户拒绝授权时，不创建新的 Analysis，只保留 Run 的缺失原因
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
- 通用 Benchmark 平台、排行榜浏览、社交比较、后台自动抓取和任意 Benchmark provider 不进入 v1。v1 只允许用户明确同意后，以 Steam Profile URL 或 17 位 ID 手动读取一组随产品审核的 KovaaK 训练项目最高分、项目档位和完成度；用户可在本地保存一个本人已连接账号以便后续手动刷新。聊天中临时提交的其它 Profile 只在该回合查询，身份和成绩都不持久化；两类身份均不进入 Coach Provider。用户界面只称“KovaaK 成绩”或“训练项目成绩”，不突出外部作者、课程代号或难度体系。Coach 可用去身份成绩决定先检查哪个项目，但不能凭分数或课程标签直接诊断 reading、动作机制、身体状态或外设问题

## 12. 约束与依赖

- **技术边界**：Raw Input / KovaaK 数据解析 + Python CV + 项目内 Pi-based Coach runtime + Next.js/React 前端 + FastAPI 服务 + Tauri 2 桌面壳；具体版本以依赖文件和 lockfile 为准
- **输入事实源分工**：Raw Input 测量用户输入运动学与真实鼠标按钮；Performance 提供 Challenge 时间窗和自动配对锚；Stats 提供场景、射击、击杀/命中事件和可用配置；MP4 提供视觉目标/准星/场景证据；任何单一来源都不得静默冒充其它来源
- **隐私与平台**：Raw Input 第一版仅 Windows、默认关闭、用户 opt-in、只在检测到 KovaaK 进程时采集、只保存本地。用户启用 Coach 并选择 Provider 后，版本化、字段白名单化的 L1-L3 facts/evidence 可作为普通 Coach context；Raw trace、MP4、原始 CSV / protobuf、私有 parser payload 和未知字段不发送。非 Windows 必须有可用 fallback
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
| `docs/frontend-uiux-design.md` | 已确认的桌面应用骨架、Coach 消息卡/视频工作区与 Coach-first IA 设计 |
| `docs/archive/README.md` | 已退役、冻结和完成资料索引，仅供历史追溯 |

## 14. 决策日志（关键选择 + 为什么）

- **桌面 hybrid 而非纯 web**：省 CV 服务器成本 + 解并发；LLM 使用用户选择的 Provider 或本地模型；产品不建立账号、登录、鉴权服务器或账号型同步；资产演进不浪费
- **开源免费 + 透明联盟佣金**（2026-07-13）：分析、Coach、History、训练计划和产品命令不设付费墙；用户自行承担其所选第三方 provider 的可能费用。只有当证据与上下文支持外设可能成为限制时才可推荐商品，官方联盟链接可产生佣金，但佣金不得影响诊断、推荐触发或排序
- **History 与 Coach 档案长期本地优先**：本地 profile 是 canonical owner；导出 / 导入负责显式迁移，不建立账号型云同步
- **Provider-first onboarding 是硬门槛**（2026-08-09）：首次启动先说明 Coach 价值、Provider 成本和数据边界；连接 Provider 后才进入 Coach-backed 分析。Provider 不可用时采集可继续，但不生成 Provider-less Analysis 或报告；后续回访从既有入口恢复连接
- **Pi catalog 与本地 credential**（2026-07-13）：pinned Pi built-in provider/model catalog 就是产品 catalog，不维护 Aiming Cookie allow-list；支持自定义 OpenAI-compatible profile。API key 可作为 local-first 权衡明文保存在本地 config/provider.json，secure store 不是前置 Gate，但 secret 绝不进入 AnalysisResult、Coach 上下文/消息、普通日志、诊断或导出
- **v1 → B → C 分阶段**：v1 建立开源免费的完整 Coach 闭环；B 深化长期档案、训练计划和复测体验；C 在保持信任边界的前提下接通经验证的外设目录与透明联盟链接
- **首发 aim-family 范围与证据边界**：v1 覆盖 static/dynamic clicking、continuous tracking 与 target switching；Raw Input 只解决输入运动学事实，目标/准星/误差结论须经本地视觉预处理和质量 Gate。movement aiming 没有玩家移动遥测时只保留 outcome-only。
- **按依赖演进而非删 feature**：保留完整 Coach、桌面 hybrid、显式导出/导入、长期趋势和推荐生态等路线；先验证采集、专项 analyzer 与 Coach 闭环可靠性，再扩展运营能力
- **统一设计系统先于全面美化**：可执行 token 集中于前端；页面不得各自硬编码视觉值。现有设计稿和 Stitch 产物保留为参考，不与运行时代码争夺事实源
- **技术预览不冒充完整 v1**：受控预览只验证 flicking 核心闭环；长期完整产品范围不变，发布日期由 Roadmap Gates 和真实验证决定
- **职责边界**：Domain Core 保持确定性；Local Analysis Runtime 负责 job、文件和本地 History；Coach Agent Runtime 负责本地长期关系、工具编排与交互事件；用户选择的 Provider 负责 LLM 推理；在线表面只承担 Landing、release 分发和无身份外设目录
- **单一开源免费且无账号的产品**：不存在免费/付费两套产品、能力墙、注册、登录或产品鉴权服务器。首次启动先进入 Provider onboarding；购买外设与否不影响任何产品能力
- **教练与分析**：目标上教练可跨次、可不绑单次分析；分析/表现档案是上下文与病历。过渡实现可挂在分析 session 下，终局不锁死为「对话从属于分析」
- **删除分析不抹教练记忆**：进行中不可删；删除 terminal Analysis 只清理该 Analysis 自有结果与 managed artifacts，不删除 KovaaK Run、Run-owned Raw/MP4、用户源文件或 Coach 历史
- **常驻 Coach 是 Agent 操作层**：provider 可用时 agent 可随时进入、调用稳定的应用工具，减少用户对多页面流程的记忆负担；项目内 Pi 源码是 Coach runtime 基线，项目可直接修改且不承诺跟随上游升级；已有可用的 workspace、权限或 sandbox 能力优先保留，不无证据重写
- **持久表现档案 + 上下文衔接**：支撑长教练关系体验；窗口顶满后的 session 衔接另研究，不在本条锁实现
- **自动证据层级 Coach 分析**（2026-08-10，取代 2026-08-09 的固定多源决定）：新 Run 由服务端按 `multimodal > input_native > video_fallback` 自动选择最高可用路径；三档都必须按来源分层并显示 limitations，不把单一来源过度解释为完整视觉测量。
- **Raw Input canonical 运动上限固定为 1000 Hz**（2026-08-04）：不改变鼠标硬件 polling rate；native layer 继续接收 Windows Raw Input，但进入 canonical trace 前按 1 ms 聚合运动增量。同毫秒 `dx/dy` 分别求和，按钮边沿按顺序单独保留，因此不会因限频丢失每毫秒 X/Y 净位移或点击边沿；亚毫秒路径形状明确不属于产品分析事实。新语义必须版本化，旧 trace 保持只读兼容，不允许在同一未标版本中混用两种采样语义。
- **自动采集统一主路径**（2026-07-18，2026-07-19 暂停裁决）：应用不依赖实时 Challenge hook，而是在 KovaaK 进程 gate 内统一采集 Raw Input，并以硬件编码维护仅 KovaaK 窗口最近 300 秒的有界回放缓冲；Stats / Performance 到达后按 Challenge 时间窗事后切成独立 Run。v1 仅为 `Pause Count = 0` 的 normal/timescale-only Challenge 生成永久 MP4；`Pause Count > 0` 的暂停局 fail closed，证据只能保留为 partial/unavailable，不能声明 canonical Raw/Performance 对齐。超过 300 秒、长时间中断或来源覆盖不完整时明确降级。Analysis 最低条件为 `Stats AND (MP4 OR (Raw + Performance))`；单局确认、多局选一条，其余保留待分析；手动 `MP4 + Stats` 是独立 fallback。
- **Run-owned evidence 由用户手动管理**（2026-07-17）：自动 Raw 和自动切分 MP4 随 Run 保存，不随 Analysis 删除。Settings 显示分类占用，用户可分别移除大体积 evidence；不静默自动清理、不自动删除最旧 Run，也不连带删除 Run metadata、Analysis 或用户源文件。
