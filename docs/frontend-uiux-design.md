# Aiming Cookie 前端与 UI/UX 设计

> **状态：active 产品设计合同。** 本文冻结已经确认的桌面应用骨架、页面关系和 Coach-first 主工作区；第 8 节列出的未冻结内容不构成实施授权。
>
> **边界：** 本文展开 [`PRD.md`](PRD.md)，不得改写产品范围。视觉方向看 [`../DESIGN-cursor.md`](../DESIGN-cursor.md)，token/组件实现看 [`design-system.md`](design-system.md)，当前完成度看 [`PROGRESS.md`](PROGRESS.md)。旧 IA 与 persistent-Coach specs 已退役；若历史材料与本文冲突，以 PRD 和本文为准。
>
> **不包含：** 具体视觉值、组件库、代码结构、收费额度、后端 schema 和施工步骤。本文本身不授权编码；实施按当前任务、上游合同、代码和测试推进。
>
> **完整 Coach 数据边界：** 完整 Coach 的 aim-family 范围、证据分层与后续 Gate 可参考 [`archive/superpowers/specs/2026-07-20-complete-coach-analysis-context-design.md`](archive/superpowers/specs/2026-07-20-complete-coach-analysis-context-design.md)。本文只规定其前端可见性，不能把尚未通过的 family Gate 写成当前已实现能力。

> **2026-08-10 IA 同步：** Coach 为默认主工作区；`/` 为 Coach 首页，`/s?sessionId=<id>` 为指定会话；左侧 Session rail 为唯一主导航，用户可见一级消费面只有 Coach、History 与 Settings。Tasks 与独立 Analysis 页面退役，旧 URL 只跳转 History；Analysis 数据对象和可复用数据/视频组件继续作为内部能力保留。桌面最小内容宽度为 `1180px`，不维护更窄窗口的第二套布局；History 与 Settings 的内容最大宽度为 `1040px` 并居中。旧侧栏和独立报告页文字仅保留为决策演进记录，不得据此恢复页面。
>
> **当前实现边界：** 当前前端实现是实际能力和运行行为的事实源；若其与本文冲突，记录为实现差距并按 PRD 裁决，不能让任一方静默覆盖另一方。已退役页面和旧 prototype 只保留为历史参考。

## 1. 产品界面前提

- 当前可以使用 Web 前端技术快速开发和验证。
- 最终交付给用户的是**桌面本地应用**，不是网站。
- 因此界面按桌面应用设计：使用应用工具栏、主工作区、本地文件选择、后台分析状态和桌面窗口适配等心智模型。
- Desktop 主路径不是上传表单：用户明确启用后，应用在 KovaaK 进程 gate 内自动采集 Raw Input 与仅 KovaaK 窗口的有界回放缓冲，并在 Stats / Performance 到达后事后生成独立 Run；用户切回应用后再选择 Run 并开始 Analysis。
- 营销落地页与应用相互独立，不能把网站导航习惯直接套进应用。
- **Logo 只用于品牌展示，不可点击，也不承担返回首页或落地页的导航功能。**
- 本地应用不等于全离线应用；Coach 可直接使用用户选择的 Provider 或本地模型，Landing/release 等在线表面与本地产品分离。产品不提供 Aiming Cookie 账号、登录或鉴权服务器。

### 1.1 核心用户心智模型

前端统一使用以下心智模型组织页面和文案：

```text
              Aiming Coach
     看见 ↕ 解释 ↕ 行动 ↕ 复测
Run → Evidence → Analysis → Training → Retest
```

- **Coach** 是产品核心和长期关系层，负责把客观证据转成解释、针对性训练、产品操作和复测；完整上线目标覆盖 static clicking、dynamic clicking、continuous tracking 和 target switching；未通过相应 ScenarioProfile/analyzer/quality Gate 的 family 必须显示 unavailable 或 outcome-only，movement aiming 在缺少玩家移动遥测时只呈现 outcome-only 观察，不生成机制诊断；它不是分析结束后的附加聊天框，也不等于所有设置都通过聊天完成；
- **Run** 是一次可识别的真实训练记录，是 Stats、Performance、Raw Input 和 MP4 等来源的归属入口；
- **Evidence** 表示本次训练实际拥有哪些证据、质量如何、能支持哪些结论，不等同于“文件已经存在”；
- **Analysis** 是基于当时可用证据生成的诊断结果，必须保留 input mode、来源范围、局限和失败状态；
- **Training / Retest** 是 Coach 给出训练方法、说明预期变化并在后续 Run 中验证的闭环；
- **History** 是查找 Run、Analysis 和长期变化的组织层，不是新的数据所有者，也不把不同证据能力的记录强行比较。

界面不得让用户先理解采集管线、文件结构或内部对象关系。每个页面都应回答：这是哪次训练、有哪些证据、当前能做什么、结论来自哪里、下一步是什么。

## 2. 整体界面骨架

Aiming Cookie 的主要形态是：**顶部应用状态栏 + 左侧 Session rail + 右侧 Coach 主工作区**。

```text
┌──────────────────────────────────────────────────────────────┐
│ Logo（静态）  采集状态       分析指示       Provider 状态 │
├───────────────┬──────────────────────────────────────────┤
│ Session rail  │ Coach 主工作区（对话 / 上下文 / 操作）   │
│ 会话 / 搜索   │                                          │
│ History       │ 视频打开时：视频区 + 对话区               │
│ Settings      │                                          │
└───────────────┴──────────────────────────────────────────┘
```

- 顶部是**应用工具栏**，不是网站导航栏。
- 主工作区就是 Coach，不能再把 Coach 降级为右侧可选聊天层。
- 左侧 Session rail 是唯一主导航；History、Settings、搜索和会话管理都从 rail 进入。
- 工具栏不提供 Coach 开关、History、Tasks 或新建分析入口，只显示应用状态。
- 页面切换不能清空 Coach 会话；rail 只承载会话导航，不承载另一套对话数据层。
- 应用工具栏采用精简结构：

```text
Logo（不可点击）｜采集状态｜分析指示｜Provider 状态
```

- “新建对话”只在 Session rail 中创建草稿，首条消息成功后才持久化 Session；分析不再是顶栏主操作。
- 产品没有账户菜单；Settings 从 Session rail 底部进入。当前会话与分析状态放在主工作区，不塞进全局工具栏。
- 不加入“训练”、Dashboard、社区或知识库等未经确认的一级栏目。

## 3. 页面与信息架构

```text
启动应用（无需产品账号或登录）
├─ 首次 onboarding 未完成 → 激活 Coach / Provider onboarding
├─ onboarding 已完成 → `/` Coach 主工作区
└─ 进入既有会话 → `/s/:sessionId`，History 从左侧 Session rail 底部进入

Provider onboarding
├─ 说明 Coach 价值、第三方费用和数据边界
├─ 主路径：选择 Provider → 无认证直连 / API key / OAuth 或 device-code / 本地模型 / 已验证自定义 Provider
├─ 测试连接并建立 Coach-ready 状态
└─ Provider 未完成时停留在 onboarding，不进入 Provider-less Coach 分析

Coach 主工作区 `/`
├─ 空态 / 草稿：建议问题与当前可用证据入口
├─ 首条消息成功后创建 Session
└─ 分析与训练动作以对话、状态卡和可点击引用呈现

Coach 指定会话 `/s/:sessionId`
├─ 从左侧 Session rail 选择
├─ 恢复会话、草稿和上下文
└─ 视频讲解时临时打开视频区，不创建另一条会话

History
├─ 待分析训练 → 选择一条开始分析
├─ 训练记录（KovaaKRun）→ 查看来源与存储状态
└─ 分析记录 → 本页查看安全摘要 / 选择后交给 Coach

Coach 消息中的 Analysis 内容
├─ 指标卡：少量关键数据与白话解释
├─ 时间线卡：有界事件分布与定位
└─ 证据卡：需要时打开中央视频讲解区

独立功能页
├─ 首次 onboarding
└─ 设置
```

### 3.1 首次启动与 Provider onboarding

首次启动使用独立、可恢复的结构化 onboarding，不要求 Aiming Cookie 注册或登录，也不把 credential 配置伪装成聊天任务。

固定主流程是：

```text
理解 Coach 价值与边界 → 选择 Provider 连接方式 → 认证 / 测试 → Coach Ready
                                           └→ 未完成时停留 onboarding，可恢复连接
```

onboarding 必须：

- 在连接前说明 Aiming Cookie 开源免费、第三方 Provider 可能收费以及连接边界；
- 具体说明连接后可获得白话解释、针对性训练、长期档案、产品操作和复测，而不是只写“解锁 AI”；
- 说明发送给 Provider 的是用户可见的结构化诊断上下文，Raw Input 原始 trace、绝对路径和 secret 默认不发送；
- 动态展示完整 pinned Pi built-in provider/model catalog，不另设前端 allow-list；用面向普通用户的选项表达无需认证的直接连接、API key、可选 OAuth/device-code、本地模型和高级自定义 Provider，每个 Provider 只展示 capability API 实际支持并已完成产品接线的方式；
- API key、OAuth/device-code 和连接测试使用结构化控件；不得让用户把 credential 粘贴到 Coach 对话；
- 保存进度，允许失败恢复和稍后继续；Provider 未完成时不进入 Provider-less Coach 分析；
- 连接成功后进入 Coach 主工作区，由对话引导检测 KovaaK 数据并完成第一次分析；
- 后续启动进入 Coach 首页或指定会话，保留会话与草稿状态。

营销 Landing 与应用内 onboarding 分工：

- Landing 在下载前解释 Coach 价值、Provider 成本、数据边界和本地 fallback；
- 演示 MP4 先展示“训练 → 诊断 → Coach 解释 → 针对性训练 → 复测”，再教学 Provider 连接；
- MP4 必须配字幕、文字步骤和可更新的错误恢复说明，不能成为唯一文档，也不得展示真实 credential；
- 实际 Provider 认证和 credential 输入只在应用内完成，Landing 不收集 secret。

### 3.2 Coach 中的 Run / Analysis 上下文

Run 选择、Evidence 检查和 Analysis 状态通过 Coach 主工作区内的对话、卡片和引用完成，不再依赖独立的 `/analyze` 或 `/tasks` 页面。固定主流程是：

```text
等待/完成自动采集 → 单局确认或多局选一条 → 检查 Evidence → Coach 发起 Analysis
                                                └→ 缺失来源卡片与可恢复下一步
```

Coach 主工作区按需要提供以下信息：

- 自动采集状态：显示未启用 / 待命 / 采集中 / 整理中 / 完成 / 失败，并说明应用不是实时监听 Challenge start/end，而是在 Stats / Performance 到达后事后切 Run；
- Run 选择：显示场景、时间、Stats / Performance / Raw / MP4 可用性和是否已有分析；本次只有一条可分析 Run 时默认选中，有两条及以上时要求用户明确选择一条；
- 自动录像：主路径由应用维护仅 KovaaK 窗口的有界回放缓冲，并在 Stats / Performance 到达后按 Run 生成 MP4；用户无需手动录屏。手动选择 MP4 只属于 fallback 或明确补充来源；
- Raw Input 状态：Windows 显示未开启 / 待命 / 正在采集 / 已关联 / 不可用 / 错误；首次开启使用明确 opt-in，而不是普通模糊开关；
- 本次分析设置摘要：按需显示当前 `cm/360` 与 FOV，修改入口进入 Settings；
- 历史 input mode 只作为 provenance 摘要，不作为当前用户可选择的模式；
- 文件/证据检查结果：分别说明 Run、Raw Input、Performance、Stats、MP4 和时间对齐状态；
- Coach 自动选择 `multimodal > input_native > video_fallback` 的最高有效证据等级；用户不选择 mode。三条路径均不可用的 Run 不进入普通 History，由 Coach 说明失败与修复动作。

文件有问题时，错误紧邻对应来源或 Coach 状态卡显示，不把错误拖到后台以后才暴露。

#### 3.2.1 Analysis 的内部兼容边界

AnalysisResult、历史 input mode、指标投影、EvidenceSegment 和视频播放器继续作为内部数据与复用组件保留。退役独立 Analysis 页面不等于删除这些能力，也不允许把 Analysis 结果直接拼进模型正文。

- 后端只根据已成功执行的已识别 Analysis 工具和消息发送时保存的有效 owner-scoped Analysis 引用生成有界卡片提示；模型正文不能自定义图表 JSON；
- 前端通过既有安全投影读取指标、时间线和视频状态，卡片不得暴露原始 tool result、trace、绝对路径或 secret；
- 删除或不可用的 Analysis 引用 fail closed，不显示伪造空卡；
- 历史模式和旧结果保持可读，但不恢复用户可见的 mode selector、Tasks 页面或独立 Analysis 页面。

#### 3.2.2 Evidence 状态与可用性

Stats、Performance、Raw Input 和 MP4 必须分别显示三层信息：

1. **是否存在**：已发现、未添加、缺失、当前平台不支持；
2. **是否可用**：可用、部分可用、无效、来源不可访问、质量不足、对齐失败；
3. **能支持什么**：可用于当前分析、只提供事件锚点、只提供视觉增强、暂不能用于结论。

用户可见状态至少覆盖：

| Evidence 状态 | 界面含义 | 必须提供的下一步 |
|---|---|---|
| 可用 | 来源完整且满足当前用途 | 说明它会支持哪些分析能力 |
| 部分可用 | 只有部分覆盖或部分字段可信 | 说明受影响的结论，允许继续执行仍然成立的部分 |
| 缺失 | 本次没有该来源 | 提供添加来源、选择其他 Run 或改用可行模式的入口 |
| 平台不支持 | 当前环境不能提供该来源 | 使用中性说明，并让 Coach 自动选择仍有效的证据等级 |
| 来源不可用 | 曾存在，但当前无法访问 | 保留稳定引用和历史状态，提供重新定位、重新添加或重试入口 |
| 无效或质量不足 | 来源存在，但不满足本次分析要求 | 说明具体影响，不把它显示成“已完成证据” |
| 对齐失败 | 多个来源无法可靠对应到同一训练时间范围 | 禁止使用未对齐部分生成跨来源结论；multimodal 中保留可独立成立的 native 结果 |

Evidence 不使用单一红绿状态概括。状态表达必须同时包含文本、影响范围和下一步，不能只依靠颜色或图标。

发起 Analysis 后，状态留在 Coach 对话和 History 记录中，不创建独立 Tasks 页面或空白报告：

- 创建分析任务后允许用户离开当前页面，分析在后台继续运行；
- Coach 状态卡与 History 记录显示正在运行、完成和失败，并提供重试或回到相关记录的入口；
- 分析完成后可使用全局 toast 或低干扰状态提醒，不强制跳转页面；
- 只有存在可信的真实百分比时才显示百分比；否则显示可理解的任务阶段：

```text
准备训练记录 → 对齐输入事件 → 计算运动学 → 可选分析视频 → 生成诊断
```

- 失败信息必须区分 Run 发现、Raw Input、源文件、输入对齐、本地运动学、视频分析、Provider LLM 和 Coach runtime，不能只显示统一的“分析失败”；
- 失败时提供重试入口；运行中和排队中的 Analysis 不可删除。
- `pending_analysis` Run 不是 Analysis 状态；它只保留在 History，直到用户交给 Coach 处理。

### 3.3 历史

History 使用同一页面承载待分析训练、其它训练记录与分析记录；主要内容在可用区域内以 `1040px` 最大宽度居中。它不是独立报告页，也不把数据和图表横向撑满全屏。

History 固定按以下顺序组织：

1. **待分析训练**：满足最低分析条件、尚未创建 Analysis 的 Run；单条可直接确认，多条先选择一条；
2. **训练记录**：已分析、evidence 不完整、source unavailable 等其它 Run；
3. **分析记录**：queued、running、done、failed 和 retry attempt。

未被用户选择的 Run 只停留在“待分析训练”，不进入任务中心、不自动合并、不自动删除。

训练记录区必须显示：

- 日期、场景和稳定 source key 对应的用户可读身份；
- Stats、Performance、Raw Input trace、自动 MP4 和源文件可用性；
- 待分析 / evidence 不完整 / 分析中 / 已分析 / source unavailable；
- “让 Coach 分析”“查看 Run”“查看存储占用”等主操作；
- Raw Input 未开启或平台不支持时使用中性说明，不把 fallback 描述成故障。

长期趋势规则：

- 只展示多次分析之间可比较的变化；单次分析的少量安全摘要在 History 按需打开，详细指标和图表由 Coach 在解释需要时发出；
- 第一版只突出少量核心指标和一句近期变化摘要，不制作复杂 Dashboard；
- 只有一条记录时提示完成更多分析后生成趋势；没有记录时不显示空图表，直接引导开始第一次分析。

分析记录采用便于纵向比较的列表，而不是卡片瀑布流。每条记录必须显示：

- 日期、场景和任务状态；
- input mode 与 evidence provenance 摘要；
- 完成记录的诊断类型与一句最重要结论；
- 运行中记录的当前阶段；
- 失败记录的明确失败原因和重试入口。

分析记录提供“查看摘要”和选择后“交给 Coach”，不导航到独立 Analysis 页面；训练 Run 进入 Run inspector 或交给 Coach。删除等低频操作收进次级菜单。进行中和排队中的 Analysis 不可删除；Run metadata、Run-owned Raw/MP4、用户源文件必须使用不同文案与确认。第一版不预先堆叠复杂筛选和排序；等记录数量证明有需要后再增加。

实现前需要确保历史列表数据合同能够提供场景名称、最重要结论和当前任务阶段；当前接口若缺失，应在实施计划中明确补齐，不能由前端猜测。

#### 3.3.1 Run inspector

Run 详情是训练记录与来源状态的检查器，不是缩小版分析报告。它必须按以下层级展示：

1. **训练身份**：场景、发生时间、稳定 source key 的用户可读表示和当前 Run 状态；
2. **Evidence 来源**：Stats、Performance、Raw Input、MP4 分别显示存在性、可用性、质量、覆盖范围和对齐结果；
3. **分析能力**：Coach 自动选中的证据等级、缺失来源，以及该等级能生成和不能生成的结论；
4. **关联 Analysis**：已有、进行中、失败和可重试的 Analysis，不能把重新分析误写成覆盖旧结果；
5. **主要操作**：开始分析、处理来源不可用、查看已有分析、进入 Storage 管理 Run-owned evidence；手动补充/替换 MP4 只在合同允许的 fallback 或修复路径出现。

Run inspector 不显示绝对路径、原始 trace 或内部标识。用户只看到必要的文件名、稳定引用、可理解状态和影响；需要重新定位本地来源时，使用明确的文件选择动作，不把本地目录结构暴露为产品信息。

#### 3.3.2 KovaaK 成绩边界

v1 只提供一组经过审核的 KovaaK 训练项目成绩同步，不建立独立 Benchmark 页面、排行榜浏览、社交比较或通用 provider。用户侧只称“KovaaK 成绩”或“训练项目成绩”，不突出外部作者、课程代号或阶段体系。

- onboarding 和 Settings 提供同一个 Steam Profile URL 输入，同时兼容 17 位 ID，并提供用途说明、明确同意、保存本地已连接账号、手动同步和最近结果状态；不得暗示这是 Aiming Cookie 账号，也不得回显完整 URL/ID；
- 设置状态区显示最近成功同步时间、整体完成度、当前是否有可用快照，以及失败后仍保留上次成功数据；不得显示内部接口、请求 URL、外部课程品牌或 Provider payload；
- 第一轮只要求最小成绩视图：默认显示完成度和项目列表，课程大类用于轻量分组，子分类只在项目行作为弱化的“训练侧重”，不增加新的 Tab、导航或用户必须学习的术语。视图显示项目名称、最高分、项目档位与未完成状态；它不进入 History 长期趋势，也不把一次分数变化写成训练效果；
- Coach 可以读取去身份成绩摘要来决定先检查哪个项目；用户临时发来的其它 Profile 只能在当次查询且不保存。Coach 不得显示或转述 Steam ID，也不得从低分直接生成 reading、张力、握持、外设或其它原因判断。

### 3.4 Coach 消息卡与视频讲解工作区

用户不直接消费独立“诊断 / 视频 / 数据”页面。Coach 先用白话解释问题，只在解释需要时附加以下确定性内容：

1. **指标卡**：最多显示少量支持当前解释的关键指标、单位和安全摘要；
2. **时间线卡**：显示有界事件分布与可定位范围，并提供文字替代；
3. **证据卡**：引用安全投影中的观察，只有受控本地视频可用时才提供“在视频中查看”。

卡片属于对应 Coach 消息，不是新的导航、嵌套页面或任意 JSON renderer。无可用投影时整张卡 fail closed；不得把模型正文解析为卡片，也不得显示路径、原始 trace、私有 payload 或未知字段。

默认无视频时，Coach 对话列以可读的最大宽度居中，不随全屏无限拉伸。打开证据卡后，工作区变为：

```text
Session rail | 中央视频讲解区 | 右侧 Coach 对话
```

关闭视频后回到单列居中对话；切换或关闭视频不能清空会话。视频区复用现有受控媒体协议、播放器和 EvidenceSegment，不引入任意 URL、路径或新的视频访问规则。

### 3.5 自动采集、Raw Input 与状态

自动采集是 Desktop 主路径；Raw Input 与窗口回放缓冲必须以明确授权、可见状态和局部失败呈现：

- 设置页显示平台支持状态、自动采集开关、Raw Input 授权、窗口录制范围、仅在 KovaaK 进程运行时维护 300 秒瞬态缓冲、只保存在本地的说明；
- Windows 首次启用必须使用解释性确认，并作为完成 onboarding 的硬门槛；拒绝授权时不能进入主工作区。
- 首发只支持 Windows Desktop 自动采集；浏览器预览和非 Windows 不提供可完成的 onboarding。
- 自动回放缓冲只捕获 KovaaK 窗口，不捕获完整桌面、其它应用窗口或系统通知；
- 运行中显示待命 / 采集中 / 整理中 / 完成 / 失败状态，但不持续打扰用户；可选托盘/悬浮状态不得抢焦点，并可在 Settings 关闭；
- Raw Input trace 是否存在、是否已与 Run 对齐、是否可用于本次分析必须在 Run inspector、History 摘要或 Coach 安全投影中可见；
- 自动 MP4 是否存在、是否完成切窗、是否与 Raw/Stats/Performance 对齐必须分别可见；Raw 或录像单独失败时保留仍满足最低条件的 mode；
- Raw Input 数据不作为 Coach 上下文发送；如需解释，只能引用合同允许的 L1-L3 结构化 facts、derived evidence 或稳定 EvidenceSegment reference，不能发送 Raw trace 或 samples；
- Run metadata、Raw trace、自动 MP4 和原始 Stats / Performance 文件必须分别说明归属和删除影响，不能用一个“删除训练记录”按钮掩盖不同生命周期。

### 3.6 LLM Provider 与认证设置

Provider 有两层界面：首次 onboarding 提供最小成功路径，Settings 提供完整管理。两者复用同一 capability API、状态和安全组件，不依赖 Aiming Cookie 账号、开发者环境变量或隐藏配置。

Provider 列表至少显示：

- provider 名称、认证方式、当前 model；
- `未配置 / 可用 / 测试中 / 连接失败 / 已停用`；仅对需要认证的 Provider 增加 `需要认证 / 认证过期 / 需要重新认证`；
- credential 来源，例如 API key、OAuth、环境变量或本地服务；只显示来源和末尾掩码，不回显 secret；
- 当前是否为 Coach 默认 provider；
- 最近一次连接测试结果和时间。

用户操作包括：

- 从完整 pinned Pi built-in catalog 添加 provider/model，或创建自定义 OpenAI-compatible provider；
- 填写或替换 API key；
- 仅在 provider 要求、支持且产品已接线时使用浏览器 OAuth 或 device-code 认证；
- 选择 model、刷新动态 model 列表、测试连接；
- 设为当前默认、停用、重新认证、撤销授权或移除 credential；
- 本地 provider 可填写 base URL 和 model，但必须明确它由用户自己的本地服务提供。

交互边界：

- API key 输入默认隐藏，保存后不可再次读回，只能替换或删除；
- OAuth/device-code 认证显示等待浏览器或 device code、取消、超时、成功和需要重新认证状态；
- Provider profile 与 credential 分开删除；移除 profile 前说明是否同时移除 credential；
- 未配置 Provider 时，Coach 入口只作为激活入口，可恢复首次 onboarding 或进入 Provider Settings；此时不提供 Coach 对话、解释、长期档案、训练计划或产品命令，也不显示升级、订阅、额度墙或产品登录；
- 确定性诊断、History 和本地分析不依赖 LLM provider；provider 故障不能让这些页面失败；
- 第一版不在聊天输入区放模型选择器；切换 provider/model 只在 Settings 中完成，避免每轮对话状态不透明。

### 3.7 后台 Analysis 状态

产品不提供独立 Tasks 页面。后台 Analysis 状态由 Coach 状态卡、History 记录和低干扰全局通知共同承担：

- 显示排队中、运行中、已完成、失败和可重试的分析任务；
- 显示当前阶段，而不是在没有可信进度时伪造百分比；
- 允许用户离开当前会话后在 History 继续找到对应 Analysis；
- 完成后由 Coach 解释或在 History 查看安全摘要，失败后提供与失败类型对应的说明和重试；
- 应用重启或页面切换后恢复仍然存在的任务状态；
- 使用全局角标和低干扰通知提示完成或失败，不强制抢夺当前页面；
- 排队中和运行中的任务不可删除，重试必须产生可追溯的新尝试，不能静默覆盖失败历史。

后台 Analysis 的主要阶段沿用：

```text
准备训练记录 → 对齐输入事件 → 计算运动学 → 可选分析视频 → 生成诊断
```

阶段只能在真实能力支持时出现，并遵守当前 Analysis 来源 Gate；前端不得根据历史 mode 自行创造回退路径。

### 3.8 归属、删除与保留的 UX 边界

Analysis、Run、Raw Input trace、自动 Run-owned MP4、用户 Stats / Performance 和手动 fallback managed copy 使用彼此独立的生命周期。用户从手动 UI 发起时，界面必须先说明“将删除什么、保留什么、哪些引用会失效”，再允许确认；用户已在当前 Coach 消息中明确、无歧义指定同一已注册删除操作时，该消息已构成授权，不再追加第二次确认：

| 对象 | 用户可见归属 | 删除或移除时的 UX 边界 |
|---|---|---|
| Analysis | 某次基于固定 evidence 生成的诊断 | 只影响该 Analysis 及其自有输出；不连带删除 Run、trace 或用户源文件。Coach 历史消息保留，相关引用显示已删除或不可用 |
| Run metadata | 一次训练记录的组织入口 | 与 Analysis 和来源文件分开处理；删除前列出仍关联的 Analysis 与 evidence，不能用“删除训练”暗示会清理所有数据 |
| Raw Input trace | 本地捕获并关联到 Run 的 evidence | 与开关、Run metadata 和 Analysis 分开管理；是否清理必须使用独立动作和明确确认。关闭捕获不应被界面描述为已经删除历史 trace |
| 用户 Stats / Performance | 用户拥有的原始来源 | Aiming Cookie 不自动删除用户源文件；从应用中移除引用或删除 Run 时，必须明确说明原文件仍保留 |
| 自动 MP4 | Run-owned 的本地 managed evidence | 不随 Analysis 删除；用户可在 Storage 中独立移除，删除后回放和视觉引用变为不可用 |
| 手动 MP4 managed copy | 为 fallback Analysis 管理的本地副本 | 与用户原始录像分开说明；删除 managed copy 不等于删除用户原文件，删除后依赖它的视觉证据和回放会变为不可用 |

第一版不提供模糊的“一键清空所有数据”，也不自动删除最旧 Run。Storage 显示总占用以及 Run 录像、Raw trace、Analysis artifacts、未完成采集数据的分类占用；用户可按 Run 分别移除自动 MP4、Raw trace 或未完成采集数据，手动 UI 确认或 Coach 主动提议前必须说明对未来 Analysis mode、回放和既有引用的影响；当前 Coach 消息已明确指定的已注册删除操作不再重复确认。Run metadata、Analysis 记录和用户 Stats / Performance 原文件保留。精确事务与恢复由后续 implementation plan 冻结。

## 4. Coach 主工作区与会话 rail

“Coach 全局存在”表示关系和会话状态可以跨合适的页面持续；当前形态是 Coach 主工作区，不是右侧可收起 sidebar。

### 4.0 OpenDesign Coach-first 布局取代说明

以下上下文、引用、确认和错误恢复规则继续有效，但早期关于“右侧侧栏打开/收起”“工具栏 Coach 开关”“单条主会话”的布局与入口描述已被本合同第 2-3 节取代。实现必须使用：

- `/` 作为 Coach 首页，`/s/:sessionId` 作为指定会话；
- 左侧 Session rail 作为唯一主导航，History 与 Settings 位于 rail 底部；
- 顶部 48px 应用状态栏只保留静态 Logo、采集状态、分析指示与 Provider 状态；
- 默认形态为 rail + 居中 Coach 对话，视频开启时才增加视频区；Tauri 最小内容宽度 `1180px`，不为更窄窗口维护另一套产品布局。

### 4.1 基本形态、打开与收起

历史设计曾采用**一条持续存在的主会话**；当前采用可从 rail 管理的 Coach sessions：

- 会话按场景分组并支持搜索、归档和软删除；
- 用户切换页面或分析记录，主会话不会消失；
- rail 收起不结束会话，也不丢失草稿或当前上下文；
- 用户可以“开始新话题”，清理当前临时话题摘要与聚焦上下文，但不删除可回看的聊天记录和长期用户资料；
- 后台自动整理过长的对话，用户不需要通过新建会话管理模型上下文窗口。

Coach 会话有两种主要进入方式：

1. 用户从左侧 Session rail 选择或新建草稿会话；
2. 用户点击内容附近的“问 Coach”“跟 Coach 深聊”等上下文操作，定位到同一会话。

已确认规则：

- Coach 主工作区默认可见；Provider 未配置时显示连接 Provider 的受限空态，不把 Coach 变成右侧激活 sidebar；
- 分析完成且 Provider 可用时，Coach 可在当前或相关会话中给出解释与确定性卡片；
- onboarding 和 Provider Settings 使用结构化界面，不显示空 Coach 对话；
- 用户可以随时收起或展开 Session rail；
- 视频区在需要时打开或关闭，关闭后对话列恢复占满 rail 右侧；
- 在支持 Coach 的页面之间切换时，保留会话、草稿和展开状态；
- Session rail 使用稳定的桌面宽度；无视频时 Coach 对话保持居中最大宽度，打开视频时右侧对话保持可读的受限宽度；
- 产品支持 `1180px` 以上正常桌面窗口以及最大化/全屏，不把 `<1180px` 的浏览器测试形态承诺为产品模式。

### 4.2 顶部状态与当前上下文

Coach 对话顶部必须显示：

- Coach 名称；
- 当前连接或回复状态，例如可用、回复中、离线；
- 当前正在查看的分析；
- 收起按钮；
- “开始新话题”等低频操作入口。

Coach 必须让用户看懂“它现在在看什么”。当前可见上下文分为两层：

1. **分析上下文**：当前分析记录；
2. **聚焦上下文**：某个诊断问题、视频时间点、Flick、指标或图表数据点。

例如：

```text
当前分析：1wall 6targets small · 7月12日
当前聚焦：视频 01:24 · Flick #37
```

规则：

- 从 Session rail 进入 Coach：只带当前页面或当前分析，不自动选择具体内容；
- 从“问 Coach”打开：自动带入用户刚刚点击的具体内容；
- 同一个上下文不能因重复点击而重复附加；
- 发送前用户可以看见并移除本次携带的上下文；
- 每条消息记录自己实际使用的上下文，后续页面切换不能改写旧消息的引用；
- 切换分析时明确提示上下文已变化，不能静默混用两次分析；
- Coach 可以引用过去的分析，但必须标明日期或分析记录；
- Coach 可以引用 Run 的场景、Stats / Performance 规范化 facts、已生成的确定性指标和稳定 EvidenceSegment reference；L0 原始载体（Raw trace、MP4、原始 CSV / protobuf、路径、私有 parser object 和 unknown fields）不因用户选择而进入上下文；
- 分析被删除后，历史消息保留，但原引用显示为不可用。

### 4.3 对话上下文与准确资料的边界

Coach 默认使用有界、版本化、类型化的 L1-L3 context/tool results：L1 为 allow-listed 的规范化 Stats/Performance Run facts 与 outcome records，L2 为 MetricRecord、分布、EvidenceSegment、SignalWindow 与可比对结果，L3 为诊断、机制边界、知识、处方、profile、计划与复测。L1 可以在预算内保留完整的已知安全字段；L2/L3 默认摘要并仅通过受限查询下钻。它们必须携带 provenance、completeness、quality、coverage、alignment 与 limitation，回答的因果措辞不得高于这些边界。

用户可以在发送前查看并移除当前 Analysis、问题、指标、时间点/范围、EvidenceSegment 或对比记录等引用；这不是对每个 Run 另设逐次同意 Gate。用户已经选择 Provider 时，合同允许的 L1-L3 数据可作为普通 Coach turn 数据发送；没有 Provider 时，确定性 Analysis/History 与这些本地结构化 evidence 仍可使用。

长期对话和长期用户资料是两套机制：

- **对话 compaction** 只负责保持聊天连贯，把较早对话整理为临时摘要；
- **渐进式 Markdown 用户仓库**负责保存需要准确调回的长期文字信息；
- 完整聊天记录仍可回看，compaction 只改变每轮发送给模型的内容；
- Coach 需要准确回答历史目标、限制、主观反馈或已形成的长期认识时，应读取对应用户资料，不能只依赖对话摘要猜测；
- 第一版默认通过首页索引、分类指引、目录导航和必要的全文搜索按需读取，不以 RAG 作为基础召回机制。

渐进式读取原则为：

```text
用户首页 / 总索引 → 对应分类 → 具体 Markdown 资料 → 必要时追溯来源记录
```

本次实际参考的用户资料，应在对应回复中以低干扰方式显示，例如：

```text
本次参考：当前表现画像 · 训练目标 · 7月12日分析记录
```

侧栏不内嵌完整文件管理器；用户资料的完整查看、编辑和管理应由独立界面承载。具体目录结构、文件 schema、更新策略和存储实现由后续用户信息架构文档与 implementation plan 冻结。

### 4.4 消息类型与回答结构

第一版只需要六种消息：

1. 用户消息；
2. Coach 文字回答；
3. 内容引用：时间点、指标、诊断或处方；
4. 工具状态：正在读取、定位、比较或分析；
5. 操作确认；
6. 错误与恢复提示。

不为了显得“Agent 化”制造大量复杂卡片。Coach 回答默认遵循：

```text
结论
→ 用白话说明观察到的动作现象
→ 引用本次 Flick / 指标 / 历史变化
→ 区分测量事实、规则诊断和社区经验
→ 给出训练场景、动作 cue 和练习重点
→ 说明预期改善的指标方向与复测方法
```

避免一开始输出大段理论。用户不应只得到指标值或术语释义；回答必须把数据转成“哪里有问题、为什么这样判断、怎么练、预计改善什么、之后如何验证”。时间点、指标和诊断结论使用简短、可点击的引用，不把整张图表复制进聊天消息。

因果措辞必须匹配证据等级：指标定义直接支持的现象可以确定陈述；多指标规则支持的根因写作“说明/更符合”；社区或未验证经验只能写作“可能/常见于/可以尝试验证”，不能把握持张力、身体状态或灵敏度猜测写成已测事实。

### 4.5 工具操作与确认

用户在当前消息中对已注册产品操作给出明确、无歧义的自然语言指令时，不需要再次确认；这包括删除等有后果操作。Coach 直接执行同一产品命令，并在消息内显示目标、进度和结果：

- 定位视频时间点；
- 打开某个指标；
- 高亮图表；
- 切换诊断、视频或数据视图；
- 读取和比较分析记录。
- 创建普通分析任务、打开结果或应用一个尚可撤销的视图/筛选状态，只要用户本轮已经明确要求且执行参数没有歧义。
- 删除当前消息明确指定、且命令层确认可删除的 Analysis、Run-owned evidence、Storage item 或其它已注册对象。

以下操作必须先说明动作与后果，再由用户确认：

- Coach 根据推断主动提出、但用户没有明确要求的副作用操作。
- 当前消息虽然表达了目标，但目标对象或必要参数仍有歧义的操作；这时先问一个最小区分问题，而不是让用户重新填写完整表单。

API key、OAuth/device-code、系统/隐私权限、文件选择、现实训练和主观事实不是可由 Coach 代办的普通操作。Coach 打开并聚焦可信控件、预填非敏感信息、等待用户完成，然后读取状态继续；不得把这些值带入对话、tool event 或预填 intent。用户明确陈述的校准、训练完成或主观事实可以按现有事实命令直接记录，但 Coach 不得自行推断。

Coach 主动提议的确认不能只问“确定吗？”。至少应说明命令、目标对象、关键影响和取消入口。

### 4.6 回复过程与错误恢复

- Coach 回复时显示正在进行，用户可以停止生成；
- 失败后可以重试，已经生成的部分不因失败全部消失；
- 工具执行和文字生成是两个状态，不能一直只显示模糊的“思考中”；
- 网络、模型、权限和工具执行失败分别说明；
- Coach 不确定时必须明确说不确定，不能假装数据支持某个结论；
- compaction 或历史资料读取失败时，可以降级使用更短的最近对话和已确认资料，但必须说明当前信息可能不完整，不能静默编造。

### 4.7 长期用户资料的写入规则

普通聊天不会自动变成永久用户资料。Coach 希望记录灵敏度、训练目标、伤病限制、主观反馈或长期表现认识时：

- 先向用户展示准备写入的内容、目标资料和用途；
- 用户可以允许、拒绝或之后撤销；
- 用户确认后显示明确结果，例如“已写入：你目前使用 51 cm/360”；
- 用户亲自记录的内容不能被 Coach 静默覆盖；
- 观察、推断和用户确认的事实必须区分；
- 编译后的长期结论应保留来源，失效时标记过期，不直接抹掉历史；
- 删除一次分析不会自动删除由交流形成的长期资料，但失效来源应标为不可用。

Markdown 用户仓库是准确资料的事实载体，不是 Coach 主工作区中的“AI 记忆黑箱”。具体数据文件仍可由数据库或结构化文件承载；本节只冻结与 Coach 关系有关的文字资料交互。

### 4.8 输入区、主工作区联动与行为边界

输入区必须支持：

- 多行文本；
- 发送和停止；
- 查看本次附带的上下文；
- 移除不想发送的上下文；
- 空会话时提供少量与当前页面有关的建议问题。

第一版不做：任意文件附件、图片生成、通用联网搜索入口、模型选择器、Prompt 模板商城和复杂斜杠命令。

Coach 与内容采用“自然语言或点击 → Coach 编排 → UI 导航/聚焦/安全预填 → 产品命令或用户动作 → 状态验收 → 自动继续”的联动规则：

- 点击时间点引用：切换到视频视图、seek 到对应位置，并短暂高亮时间轴；
- 点击 EvidenceSegment 引用：切换到视频视图并播放同一段本地受控视频；Coach 当前不读取、上传或播放视频内容；
- 点击指标引用：切换到数据视图、滚动到对应图表，并短暂高亮曲线或数据点；
- 点击诊断引用：切换到诊断视图并定位对应观察项、候选解释或历史候选说明；
- 页面成功定位后，Coach 在对应消息附近显示“已定位”等反馈。
- 需要用户亲自完成时，只显示一个结构化下一步，包含可信控件、完成条件、取消和失败恢复；Coach 不从聊天语气猜测完成。
- 跨路由导航不得清空当前 Coach session、草稿或待完成目标；动作后必须重新读取 canonical product state 验收。

Coach 可以解释、比较、定位证据、给出训练建议，并通过与 UI 共用的产品命令调用当前用户拥有的能力。它不是只读助手，也不得拥有高于当前用户的权限。Coach 不可以：

- 无缘无故切换页面、滚动主区或抢夺焦点；
- 静默修改数据或用户资料；
- 把推测说成测量结果；
- 在没有可靠数据时宣称“最好、最差”；
- 把所有问题都归因于用户身体或张力；
- 变成与瞄准训练无关的通用聊天机器人。
- 发送 URL、DOM selector、script、绝对路径、secret 或任意 Tauri invoke，或通过模拟鼠标/键盘绕过现有控件和产品命令。

视频在 Coach 主工作区的中央讲解区展示；指标和时间线以受限的确定性消息卡片出现，不能退化成原始数据附件或模型自定义渲染。

## 5. 哪些页面显示 Coach

| 页面类型 | Coach 规则 | 初始状态 |
|---|---|---|
| 首次 onboarding | 不提供空 Coach 对话；使用结构化 Provider 激活和本地 fallback | 不显示侧栏 |
| 设置（含 Provider） | 不提供 Coach 对话；Settings 自身显示 Provider/auth 状态 | 不显示侧栏 |
| Coach 首页 `/` | Coach 主工作区，展示草稿引导或当前会话 | 默认可见 |
| 指定会话 `/s/:sessionId` | Coach 主工作区，恢复该会话上下文 | 默认可见 |
| History / Settings | 通过 Session rail 进入；页面自身不渲染 Coach 对话 | 按页面职责 |
| Coach Analysis 卡片 / 视频讲解 | 指标、时间线和证据卡属于消息；视频按需打开在中央讲解区 | 由对应消息上下文进入 |
| 有明确讨论对象的内容页 | 定位到同一 Coach 会话并带入当前内容 | 由上下文进入 |
| 错误或空状态 | 只有 Coach 确实能帮助解决时才提供 | 按场景决定 |
| 视频全屏 | 临时隐藏，退出全屏后恢复 | 继承原状态 |

新增页面必须先判断是否存在有价值的 Coach 上下文，不能默认复制 Coach 主工作区或创建第二套会话入口。

## 6. 跨页面连续性与窗口适配

### 6.1 跨页面状态

- Analysis 在用户切换页面后继续运行，Coach 状态卡和 History 记录保持可恢复；
- 分析完成后使用低干扰通知提醒，不强制用户跳转；
- 返回对应 Coach 会话时，尽量恢复滚动位置、已打开的视频时间点和选中证据；
- Coach 在支持它的页面之间保留会话、草稿和当前上下文；
- 进入不支持 Coach 的页面时可以临时隐藏 Session rail，但不能因此销毁会话状态；
- Coach 回复和已开始的后台操作在 rail 收起或页面切换后可以继续，完成后使用低干扰状态提醒；
- 视频全屏和系统文件选择器等临时专注状态可以隐藏 Coach，退出后恢复原状态。

### 6.2 支持的桌面窗口形态

产品只承诺正常桌面窗口与最大化/全屏：

1. **正常桌面（`1180–1359px`）**：Session rail 保持可用；无视频时 Coach 对话居中，视频开启时为 rail + 视频 + 受限宽度对话。
2. **宽窗口 / 最大化（`≥1360px`）**：增加中央内容留白或视频宽度，Coach 对话、History 与 Settings 不随屏幕无限拉伸。

通用规则：

- rail 收起/展开时保留会话、草稿、上下文和回复进度；
- 视频关闭后，对话列恢复占满 rail 右侧；
- History 与 Settings 的消费内容使用 `1040px` 最大宽度并居中；
- Coach 卡片必须在对话宽度内完整显示，视频通过引用打开中央区域；
- `<1180px` 不属于 Tauri 支持窗口，前端无需为该范围新增覆盖式导航、抽屉或纵向视频布局。

### 6.3 Desktop / Web 能力差异

Desktop 与 Web 使用同一套产品结构、术语、视觉语言和页面职责，只在能力可用性与输入方式上不同，不能演变为两套产品。

本节的 Web 只承担开发、受控技术预览与 Browser E2E，不代表另行承诺一个公开 Web 产品；正式用户交付仍是 Windows Desktop。Browser 验证不能替代 Tauri 的本地能力、生命周期或发布 Gate。

| 能力 | Desktop | Web |
|---|---|---|
| KovaaK Run 发现 | 可自动采集并在 Stats / Performance 到达后事后切成独立 Run | 不依赖本地自动发现，使用浏览器可访问的文件输入 |
| Stats / Performance | 可使用已发现来源或本地选择 | 由用户选择或上传浏览器可访问的文件 |
| Raw Input | 仅在受支持的 Windows Desktop 环境中完成授权并启用；用于自动质量分级 | 不支持完成产品 onboarding |
| MP4 | 自动维护仅 KovaaK 窗口回放缓冲并事后生成 Run-owned MP4 | 不支持完成产品 onboarding |
| 后台本地能力 | 可以在应用内持续运行并由 Coach / History 恢复状态 | 只表达实际可持续的服务状态，不伪装成本地常驻能力 |
| 视频回放 | 使用应用管理的可播放来源 | 使用浏览器可访问的受管来源 |
| Coach 卡片与视频讲解 | 与 Web 保持相同数据与安全边界 | 与 Desktop 保持相同产品关系，不伪装 Tauri 窗口约束 |

能力不可用时，页面应保留相同的信息架构，只替换来源说明、选择动作和可用模式。不得在 Web 中显示不可执行的本地路径操作，也不得在 Desktop 中把本地来源伪装成普通网站上传流程。

## 7. 空态、加载、失败与权限状态

### 7.1 空态与数据不足

- 没有 Run 或分析记录时，不显示空趋势图，直接解释价值并引导开始第一次分析；
- 有 Run 但没有分析时，优先引导“从这条训练记录开始分析”，不要要求用户重复导出同一份 Stats；
- 有多条待分析 Run 时要求用户选择一条；其余继续保留在 History，不制造任务或合并记录；
- 没有 Raw Input、没有 MP4 或 Performance 不完整时，明确说明当前可生成的证据范围，不把部分证据伪装成完整诊断；
- 只有一次分析时，不伪造长期趋势，提示完成更多可比较分析后再生成趋势；
- 当前视图没有可展示内容时，说明缺少什么、为什么缺少以及用户下一步可以做什么；
- Coach 建议问题必须与当前页面和真实可用数据有关，不能用通用问题填满空态。

### 7.2 加载与局部失败

- 加载时保留应用工具栏、页面结构和已经可用的内容；只有首次加载且没有旧内容的局部区域使用骨架或明确进度；
- 页面刷新数据时，不先清空旧内容再显示整页加载；
- 一个 Coach 卡片、视频讲解区或 Coach 请求失败时，只影响对应区域，不把整个主工作区替换为错误页；
- 已成功生成或加载的部分保留，失败区域提供说明、重试或替代入口；
- 部分数据缺失时明确标注影响范围，不伪造图表、指标或结论；
- 文件、本地分析、网络、Provider LLM、Coach runtime、系统授权和单次工具执行错误必须分别说明。
- Raw Input 授权拒绝、平台不支持、进程未检测到、窗口录制失败、Stats/Performance 延迟、切窗失败、snapshot 失败和时间对齐失败必须分别说明；先完成有界恢复，再由 Coach 自动选择仍成立的证据等级或说明本局无法分析。

### 7.3 离线与服务不可用

- 离线、Coach 服务不可用、分析服务不可用和单次请求失败是不同状态；
- 只禁用真正受影响的能力，不让 Coach 故障阻塞历史、上传或已经存在的本地分析内容；
- 状态提示必须说明受影响的能力和可恢复方式，不能统一写成“出了点问题”；
- 恢复连接后允许用户主动重试，不能静默重复会产生费用或改变数据的操作。

### 7.4 Coach 可用性边界

产品没有账号权限、试用档位或 entitlement。Coach 可用性只由本地 runtime 与用户选择的 Provider 状态决定：

- 前端必须分别表达 `未配置 / 配置中 / 测试中 / 可用 / 连接失败 / model 不可用 / 本地服务未启动 / 已停用`；需要认证的 Provider 另行表达 `需要认证 / 认证过期 / 需要重新认证`；
- 未配置时显示“连接 Provider 以激活 Coach”，并可恢复 onboarding；
- Provider 或网络不可用时明确说明受影响能力和恢复动作，不显示升级、登录 Aiming Cookie、额度或付费提示；
- Provider 故障不能影响 Run、Analysis、History、确定性报告和视频回看；
- 本地模式用户可以长期使用指标、确定性诊断、规则化提示和 History，并随时从 Coach 激活入口或 Settings 连接 Provider；连接前没有 Coach 功能。

### 7.5 页面状态矩阵

所有正式页面都必须覆盖与自身相关的状态，不能只实现 happy path：

| 状态 | 全局表达规则 | 页面行为 |
|---|---|---|
| 初次加载 | 保留应用骨架，局部显示骨架或明确进度 | 不提前显示空态或错误态 |
| 刷新中 | 保留旧内容并标记正在刷新 | 不闪烁清空，不阻止仍可执行的操作 |
| 空态 | 解释价值、缺少内容和下一步 | 不展示空 Dashboard、空趋势图或无意义占位卡片 |
| 部分数据 | 明确哪些内容可用、哪些受限 | 保留可成立的结果，不把整个页面降级为失败 |
| 离线 | 说明当前离线以及受影响能力 | 已有本地内容继续可读，不静默触发有副作用的重试 |
| 服务不可用 | 指明是分析、Coach 或其他服务 | 只禁用对应能力，提供主动重试 |
| 授权拒绝 | 说明被拒绝的能力和替代路径 | Raw Input 系统授权与 Provider OAuth/API key 失败分别处理，不统一成通用错误 |
| 来源不可用 | 保留来源曾存在的事实与稳定引用 | 提供重新定位、重新添加或改用其他模式的入口 |
| 对齐失败 | 说明哪些来源不能共同使用 | 禁止跨来源结论；multimodal 保留独立成立的 native 结果 |
| 待分析 Run | 已完成采集但用户尚未创建 Analysis | 留在 History 顶部，不进入 Tasks；单条确认、多条选一条 |
| 排队中 | 显示排队与可理解的等待状态 | 可离开页面，Coach / History 持续可见，不允许删除 |
| 运行中 | 显示真实阶段 | 可离开页面，不允许删除，不伪造百分比 |
| 完成 | 显示结果入口和 evidence 摘要 | 不强制跳转，使用低干扰通知 |
| 失败 | 显示失败类型、影响范围和是否可重试 | 保留已完成部分；重试不覆盖原失败记录 |
| 重试中 | 区分原失败记录与新尝试 | 显示新的任务状态和来源变化 |
| 引用已删除 | 历史内容仍可读，引用明确失效 | 不继续把已删除 Analysis 或 evidence 显示为当前可用上下文 |

每个页面的低保真设计和验收用例必须从该矩阵选择适用状态。请求失败不能被转换成“没有记录”，未知状态也不能默认为“可用”。

## 8. 仍未冻结

以下内容仍不构成实施者可自行决定的合同：

- 各页面最终像素级 Layout、精确尺寸、断点数值、动画参数和视觉微调；
- Landing 与 onboarding 的最终像素布局、演示 MP4 时长和具体 Provider 教程内容；
- 自动采集悬浮/托盘状态的最终像素布局、位置、动画和录屏编码参数；
- 外设推荐、联盟披露、免费替代方案与外部购买跳转的具体 UI；进入 C 阶段前须由独立 active spec 冻结，当前 Task 不得自行实现；
- 多 thread、归档、文件夹和 thread 删除 UI；
- 具体前端组件库、Agent UI 框架和 wire protocol；
- 安装包、签名、更新和最终发布形态。

第一版不建立独立 `/coach` 页面，不恢复旧的固定“视频 + timeline + chat”页面。若未来改变这些边界，必须先更新相应上游事实源或新增局部 spec，不能由 implementation Task 临场决定。

## 9. 已完成合同与施工入口

页面职责、信息优先级、联动、input mode、evidence、生命周期、状态规则和低保真重建边界已经冻结：

- 正式局部设计合同：[`archive/superpowers/specs/2026-07-13-frontend-product-reconstruction-design.md`](archive/superpowers/specs/2026-07-13-frontend-product-reconstruction-design.md)；
- 自动采集、Run 选择与存储合同：[`archive/superpowers/specs/2026-07-17-automatic-run-capture-design.md`](archive/superpowers/specs/2026-07-17-automatic-run-capture-design.md)；
- 已完成的 tests-first 施工合同：[`archive/completed/plans/2026-07-13-frontend-product-reconstruction.md`](archive/completed/plans/2026-07-13-frontend-product-reconstruction.md)。

原 prototype 只提供过能力接线证据，不作为正式设计起点；它已在 frontend reconstruction Task 1 经点点确认范围后删除。Task 2–7 已按本设计合同逐项授权、实现并完成当前 Browser/Desktop 自动化验收；正式产品路由、capability adapters 与 Tauri/runtime 均已存在。后续不得恢复 prototype，也不得把已完成 plan 当作新增 UI 或发布工程的授权入口。
