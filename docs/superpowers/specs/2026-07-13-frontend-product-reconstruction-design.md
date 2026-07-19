# Frontend Product Reconstruction — Design Contract

> **状态：active**
>
> **目的：** 冻结 Aiming Cookie 正式前端重建的局部产品设计边界，使后续前端从已确认的产品、交互与视觉合同出发，而不是从当前 prototype 或历史页面反向推导产品。
>
> **上游事实源：** [`../../PRD.md`](../../PRD.md)、[`../../frontend-uiux-design.md`](../../frontend-uiux-design.md)、[`../../../DESIGN-cursor.md`](../../../DESIGN-cursor.md)、[`../../design-system.md`](../../design-system.md)。涉及系统边界、数据归属和安全时，以 [`../../ARCHITECTURE.md`](../../ARCHITECTURE.md) 及相关 active lifecycle/evidence spec 为准。
>
> **自动采集局部合同：** [`2026-07-17-automatic-run-capture-design.md`](2026-07-17-automatic-run-capture-design.md) 冻结自动 Run 采集、选择、待分析 History 与手动存储管理；本文只展开其前端形态。
>
> **完整 Coach 局部合同：** [`2026-07-20-complete-coach-analysis-context-design.md`](2026-07-20-complete-coach-analysis-context-design.md) 冻结完整 Coach 的 aim-family 范围、L0-L3 数据边界、EvidenceSegment 与后续 Gate；本文只展开其前端可见性，不把尚未实现或未通过 Gate 的能力写成当前可用。
>
> **合同边界：** 本文展开上游已经确认的产品范围、页面关系、视觉语义和组件治理，不新增产品线，不改写 input mode、证据、所有权、Coach 或数据生命周期的上游定义。本文不包含施工任务、代码结构、框架选择、具体 React/CSS、接口路径、数据库字段、算法实现或发布排期。

## 1. 事实源与重建原则

### 1.1 权威顺序

前端产品判断按以下顺序进行：

1. PRD 决定产品目标、阶段范围、能力墙和非目标；
2. `frontend-uiux-design.md` 决定应用骨架、页面职责、信息架构和交互关系；
3. `DESIGN-cursor.md` 决定视觉方向、语义角色和两套主题的共同语言；
4. `design-system.md` 决定 token、主题、基础组件和视觉评审治理；
5. 完整 Coach 的 family、证据与工具权限由其 active 设计合同冻结；
6. 本文冻结本轮重建需要长期引用的局部前端合同；
7. 当前代码只说明“现在实际上存在什么”，不得反向覆盖上述合同。

若本文与上游事实源冲突，以上游为准，并停止在下游补丁式改写。

### 1.2 Prototype 不作为事实源

当前工作区中的 History / Run / Evidence 页面、临时 New Analysis 页面、临时 App Chrome、局部 Coach 面板、页面级样式和已删除旧页面都只提供能力接线与流程验证证据。

它们不得作为以下内容的事实源：

- 正式路由与导航层级；
- 页面布局、视觉比例和组件结构；
- Coach 的产品形态；
- 状态文案和错误模型；
- Desktop/Web 的用户体验差异；
- Benchmark 是否进入首发；
- 新设计系统的 token、主题或组件入口。

正式前端不要求保持 prototype 的视觉连续性、组件兼容性或页面结构。可复用的只有经重新审计后仍符合稳定产品合同的能力适配层。

### 1.3 统一用户心智模型

正式前端统一使用：

```text
              Aiming Coach
     observe ↕ explain ↕ act ↕ verify
Run → Evidence → Analysis → Training → Retest
```

- **Coach**：产品核心和本地长期关系层，在用户可见的确定性诊断与稳定证据上解释、行动和复测；
- **Run**：一次可识别的真实训练记录；
- **Evidence**：该 Run 实际拥有、可访问、可对齐且能支持结论的证据；
- **Analysis**：基于当时证据生成并保留来源、模式、局限和状态的诊断；
- **Training / Retest**：由 Coach 给出可执行训练、预期变化并在后续 Run 验证；
- **History**：查找 Run、Analysis 与可比较变化的组织层。

界面始终优先回答：这是哪次训练、有什么可靠证据、现在能做什么、结论来自哪里、下一步是什么。用户不应被要求理解内部目录、文件路径、数据表、队列或 schema。

## 2. 正式 v1 路由表

| 路由 | 页面职责 | 进入与退出规则 |
|---|---|---|
| `/` | 条件启动解析，不承载独立内容 | 首次 onboarding 未完成时进入 `/onboarding`；完成后无 Run/Analysis 进入 New Analysis；已有 Run 或 Analysis 进入 History。加载失败不得被误判为空数据 |
| `/onboarding` | 首次 Coach / Provider 激活 | 说明 Coach 价值、第三方费用和数据边界；连接并测试 Provider 是主路径，可明确跳过进入本地分析；不要求产品账号或登录 |
| `/analyze` | New Analysis | 选择训练来源、检查证据、确认分析模式并创建后台任务 |
| `/tasks` | 全局任务中心 | 展示分析任务的生命周期、局部失败、重试和结果入口 |
| `/history` | Run 与 Analysis 历史 | 分开组织训练记录和分析记录；只提供轻量摘要与延迟详情，不承载完整 Analysis workspace |
| `/analysis/:analysisId` | Analysis workspace | 承载诊断、视频、数据三视图及其与 Coach 的稳定联动 |
| `/settings` | 设置 | 承载完整 LLM Provider/model/auth、Profile、Theme、Raw Input 与本地数据/存储说明；不显示 Coach 对话 |

约束：

- Logo 是静态品牌标识，不是导航入口；
- Coach 是 App shell 中的持续侧栏，不建立独立 `/coach` 产品路线；
- Tasks 是独立任务中心，不复用 History 代替；
- Analysis 详情必须有独立 workspace 路由，不嵌入 History 的万能 inspector；
- 产品不提供 `/login`、`/register`、账号、支付、套餐或结账路由；Provider 可以无需认证；如需认证，只使用对应 Provider 支持的方式，且不创建 Aiming Cookie 身份；
- 不新增 Dashboard、训练、社区、知识库或 Benchmark 一级页面。

## 3. App Shell

### 3.1 结构

onboarding 之外的产品页面共享同一桌面应用骨架：

```text
静态 Logo｜History｜＋ New Analysis        Tasks｜Coach｜Settings
──────────────────────────────────────────────────────────────
主工作区                                   Coach sidebar
```

- 顶部是应用工具栏，不使用营销网站导航心智；
- “New Analysis”是主要应用级操作；
- Tasks 持续表达后台任务是否有运行、完成或失败；
- Coach 控件只开关同一条持续 Coach 关系，不跳转页面；
- 产品没有 Account 菜单；Settings 使用工具栏中的明确入口；
- 当前页面标题、返回关系、Analysis 状态与 input mode 放在主工作区标题区，不挤入全局工具栏；
- 页面切换不得清空任务状态、Coach 会话或用户未提交草稿。

### 3.2 视觉与层级

App shell 必须展开既有视觉合同：

- 使用温暖中性的应用画布和纸面感 surface；
- 依靠 surface 层级与 hairline 建立深度，不使用装饰性重阴影；
- 橙色只承担主要操作和当前活动分析信号，保持稀缺；
- 分析事件色只表达事件语义，不替代操作层级；
- 工作区控制留有清晰呼吸空间，数据密度集中在图表、表格与时间轴内部；
- 组件在 System、Light、Dark 三种主题下保持相同结构，只改变语义 token 的值；
- 正式页面只能组合共享 primitives 和语义组件，不复制页面私有的按钮、状态卡或表面体系。

## 4. New Analysis

### 4.1 页面目标

New Analysis 是独立页面，固定流程为：

```text
等待/完成自动采集 → 单局确认或多局选一条 → 检查 Evidence → 开始后台分析
                                                └→ 独立手动 fallback
```

页面必须同时支持：

- 显示自动采集的未启用、待命、采集中、整理中、完成和失败状态；
- 单条可分析 Run 默认选中并等待确认，多条可分析 Run 必须选择一条；
- 检查 Stats、Performance、Raw Input、MP4 与时间对齐；
- 自动 MP4 由应用捕获仅 KovaaK 窗口并按 Run 切分；
- 在独立 fallback 界面由用户同时选择 MP4 与对应 Stats，不能仅凭 MP4 猜测 CSV；
- 显示本次使用的 Profile 摘要，并把复杂修改引导到 Settings；
- 解释当前模式为何可用或不可用、能生成什么、不能生成什么；
- 只在用户已选定一条 Run、满足 `Stats AND (MP4 OR (Raw + Performance))`、必要权限和一致性检查通过后允许开始。

Evidence 问题必须贴近对应来源显示，不得把可预先发现的错误延迟到任务创建后。

### 4.2 三种分析模式

| 模式 | 首发定位 | 必需证据 | 用户可获得 | 强制限制 |
|---|---|---|---|---|
| **input-native Preview / Experimental** | v1 可见但非稳定正式能力 | Stats、Performance、可用且已关联的 Raw Input | 输入运动学、事件对齐和当前已经通过验证的 native 结论 | New Analysis、Tasks、History、Analysis workspace 和 Coach 上下文均需保留 Preview/Experimental 身份；在 segmentation、fair metrics 和 Windows 实机 Gate 通过前不得包装成稳定能力；没有 MP4 时不生成视觉结论 |
| **multimodal** | native 主结果 + 视频视觉校验 | 完整 native evidence、MP4 | native 事实、视觉验证与可稳定定位的跨视图证据 | 视频失败、不可用或未对齐时，保留已完成的 native 结果；Analysis 表达为“native 完成、visual unavailable/failed”，不得整体降为失败；允许重新选择视频或重试视觉部分 |
| **video-fallback** | 没有完整 native evidence 时的正常 v1 路径 | Stats、MP4 | 视频和 Stats 能支持的确定性诊断 | 不伪造 Raw Input provenance，不把视频推断写成输入原生测量，也不把该模式描述成用户犯错或产品故障 |

前端不得仅根据文件名、平台或局部字段猜测 input mode，不得允许视觉选择覆盖真实 evidence 条件。multimodal 只用 Raw 计算输入运动学，MP4 只提供回放、视觉定位和 Coach 的直观时间点讲解。分析模式和可用性来自稳定产品合同；界面负责解释，不负责发明。

### 4.3 Evidence 表达

每个来源同时表达：

1. 是否存在；
2. 是否可用及质量如何；
3. 是否与本次 Run/Analysis 对齐；
4. 能支持什么结论；
5. 不能支持什么；
6. 用户下一步可以做什么。

不得用单一红绿灯概括 Evidence。至少需要覆盖：可用、部分可用、缺失、平台不支持、权限未授予、来源不可用、无效/质量不足、对齐失败。

改变 Run、MP4、Profile 或来源后，页面必须重新解释受影响的模式和结论范围，不保留已经失效的“可开始”状态。

## 5. Tasks

### 5.1 产品职责

Tasks 是全局、持久、可恢复的后台任务中心。它不只是 Analysis 列表中的状态标签，也不要求用户停留在 New Analysis 或 processing 页面。

任务至少表达：

- importing / preparing；
- queued；
- running，并显示当前可解释阶段；
- done；
- failed；
- retrying 或新的 retry attempt；
- 局部能力完成、局部能力不可用的 partial outcome。

用户可理解阶段至少区分：训练来源准备、证据检查/对齐、本地运动学、视频分析、诊断完成。Coach/Provider 的失败不得伪装成本地确定性分析失败。

### 5.2 交互规则

- 创建任务后用户可以离开页面；
- App shell 持续显示运行、完成和失败状态；
- 完成与失败通过低干扰全局提示和任务角标表达，不强制跳转；
- 运行中与排队中的任务不可删除；
- 失败任务显示具体失败域、已保留结果、是否可重试以及替代路径；
- multimodal 的视觉失败不得覆盖 native 成功；
- 重启或页面恢复后，仍可找到非终态任务和它们的最新状态；
- Tasks 提供进入结果、返回相关 Run/History 和执行允许重试的入口；
- 一个子系统不可用时，不得把无关的已完成内容清空。
- `pending_analysis` Run 不是任务；未选择 Run 不进入 Tasks、不合并、不自动删除。

## 6. History

### 6.1 信息架构

History 固定按“待分析训练 → 训练记录 → 分析记录”组织：

#### Pending analysis runs

每条待分析 Run 必须满足最低分析条件且尚未创建 Analysis。单条可以直接确认，多条必须选择一条；未选择项继续保留，不进入 Tasks。

#### Run records

每条 Run 摘要至少回答：

- 场景与训练时间；
- Stats、Performance、Raw Input、MP4 的存在和可用性；
- trace/对齐是否足以支持 native 分析；
- 已有关联 Analysis 及其状态；
- 当前可执行的下一步，例如开始分析、修复来源、进入 Storage 管理 Run-owned evidence 或查看已有结果。

#### Analysis records

每条 Analysis 摘要至少回答：

- 对应 Run/场景与完成时间；
- input mode，包括 input-native Preview 标识；
- evidence 摘要、状态和主要局限；
- 最重要的确定性结论；
- 可比较性是否成立；
- 失败或 partial outcome 的原因；
- 进入 Analysis workspace 或允许重试的入口。

### 6.2 History 边界

- History 使用轻列表与延迟详情，不能加载完整视频、全部图表、完整诊断和 Coach 对话代替 Analysis workspace；
- Run 与 Analysis 不合并成一个所有权模糊的记录类型；
- pending Run 与 queued Analysis 必须是不同状态；只有用户明确开始后才产生 Analysis task；
- API/数据读取失败不得渲染为“没有记录”；
- source moved/deleted、trace unavailable 或 Analysis deleted 必须保留历史语义并显示不可用原因；
- 趋势只比较满足场景、模式、指标、单位、校准、质量与分类条件的记录；不可比较时显示原因，不生成伪趋势、伪 PB 或差异百分比；
- History 不承载 Benchmark v1 面板或 external comparison。

## 7. Analysis Workspace

### 7.1 共同框架

`/analysis/:analysisId` 是正式分析工作区。工作区标题区持续显示：

- 返回 History 的关系；
- 场景、训练时间与 Analysis 状态；
- input mode 和 Preview/partial 标识；
- aim family 与 `supported / descriptive / unavailable / outcome-only` 状态；
- Evidence 总览与主要限制；
- 与当前 Analysis 关联的 Coach 上下文状态。

主工作区只提供三个一级视图：

```text
Diagnosis | Video | Data
```

三视图共享稳定的 Analysis、Evidence 和时间定位语义。切换视图不得丢失当前选择、时间点或 Coach 上下文。

### 7.2 Diagnosis

Diagnosis 负责把确定性分析翻译成用户可行动的判断，至少包含：

- 一句话结论；
- 当前诊断 Profile；
- 有明确优先级的主要问题；
- 白话解释与为什么重要；
- 每个结论的 Evidence 来源、coverage、limitations 与稳定引用入口；
- 与问题直接对应的训练处方；
- 把当前问题交给 Coach 继续解释的入口。

不得把实验性、inferred 或不可用指标混入正式 severity 和处方。无法形成结论时，显示证据不足和下一步，不用默认“正常/optimal”填空。

### 7.3 Video

Video 负责回答“问题发生在哪里”，至少包含：

- 视频播放、暂停、seek 和当前时间；
- 与稳定事件/时间范围对齐的时间轴；
- 从诊断、数据或 Coach reference 打开的 EvidenceSegment 本地回放；片段显示 coverage、confidence、rank reason 与来源不可用状态；
- 可定位的 kill、miss、corrective、peak 等已授权事件语义；
- 从诊断或 Coach 引用跳转到对应位置；
- 视频缺失、不可用、未对齐或视觉分析失败的明确状态；
- native-only Analysis 中清楚说明视觉证据不可用，而不是显示空播放器或伪造帧定位。

multimodal 中视觉部分失败时，本视图显示局部失败和重试入口；Diagnosis/Data 中成立的 native 结果继续可用。

Coach 当前不接收、上传或播放视频内容；它只可引用 EvidenceSegment 和由本地确定性预处理产生的有界结构化证据。该限制不排除未来版本在显式启用、只读 segment、预算和 provenance 合同均冻结后加入视觉模型。

### 7.4 Data

Data 负责回答“证据和稳定性是什么”，至少包含：

- 指标总览及单位；
- Flick/事件级趋势与分布；
- 输入运动学、时间序列和允许展示的派生结果；
- 每个关键指标的来源、分类、coverage、limitations 和可比较性；
- 当前记录与历史记录不可比较时的具体原因；
- 图表的文本替代和可读取的数据摘要。

Data 不暴露 raw trace samples、用户本地绝对路径、完整原始 CSV/Performance payload、secret 或内部调试信息。

### 7.5 三种模式的工作区差异

- **input-native Preview**：Diagnosis/Data 只展示已验证的 native 结论；Video 明确不可用；Preview 身份在工作区持续可见。
- **multimodal**：native 是主事实，Video 主要提供直观回放、问题定位和可验证视觉证据；视觉不可用时工作区保留 native 结果并显示 partial outcome。
- **video-fallback**：作为 compatibility fallback 展示视频与 Stats 支持的诊断，明确没有 Raw Input measurement，不显示暗示 native 证据存在的组件或文案，也不把它包装成长期主分析方向。

## 8. Coach Sidebar

### 8.1 产品形态

Coach 是应用级、跨页面持续的右侧侧栏，不是 Analysis 详情底部的小面板，也不是独立聊天页面。

- 普通回访记住用户上次展开状态；没有偏好时默认收起；
- 第一次 Analysis 完成且 Provider ready 时自动展开一次；
- onboarding 和 Settings 使用结构化 Provider UI，不显示空 Coach 对话；
- 宽窗口中与主工作区并排并可调宽；
- 页面切换保留会话、草稿、展开状态、宽度和当前上下文；
- 进入不支持 Coach 的页面时临时隐藏，不销毁会话；
- 视频全屏等专注状态可临时隐藏，退出后恢复；
- 未配置可用 provider、认证需要恢复或 runtime 不可用时，确定性分析、History 和视频回看仍完整可用；Coach 显示可恢复的配置/重试状态，不显示付费墙。

### 8.2 上下文与证据

Coach 默认接收有界、版本化、类型化的 L1-L3 context/tool results，而不只是用户可见摘要：L1 是 allow-listed 的 CanonicalSourceFacts/Stats/Performance outcome facts，L2 是 MetricRecord、分布、EvidenceSegment、SignalWindow 与 comparison，L3 是 diagnosis、机制边界、knowledge、prescription、profile、plan 与 retest。L1 可在预算内完整投影当前已知安全字段；L2/L3 默认摘要并仅通过受限工具查询下钻。所有输出必须带 provenance、completeness、quality、coverage、alignment 与 limitation。

发送前，用户必须能看到并移除本次附带的上下文，例如：

- 当前 Analysis；
- 当前诊断问题；
- 当前时间点或时间范围；
- 当前指标；
- 当前选择的对比记录。

当前合同下，L0 OriginalCarrier 不进入 context/tool results：raw trace、`dx/dy/timestamp` samples、MP4/video/frame/thumbnail、原始 CSV、`.perf` protobuf bytes/text、绝对路径、私有 parser object、unknown future fields、secret、token、未验证 heuristic 或隐藏调试数据。不得通过“用户可见”或前端字符串过滤绕过这一边界；未来视觉模型读取受限片段必须由新的版本化合同和实施授权定义。

用户可在发送前查看并移除 Analysis、问题、时间点/范围、指标、EvidenceSegment 与对比记录引用；用户已选择 Provider 时，合同允许的 L1-L3 数据可作为正常 Coach turn 数据发送，不增加逐 Run 同意状态机。无 Provider 时，确定性 Analysis/History 与本地结构化 evidence 仍可用。

Analysis 被删除或 Evidence 失效后：

- 已发生的 Coach 消息和会话保留；
- 对应引用变为 deleted/unavailable；
- Coach 不得继续把失效引用当作当前可用证据；
- 长期资料若引用失效来源，保留历史但标记来源不可用。

### 8.3 与主工作区联动

Coach 使用“建议 → 用户点击 → 页面定位 → 明确反馈”的模式：

- 时间引用定位到 Video；
- 指标引用定位到 Data；
- 诊断引用定位到 Diagnosis；
- EvidenceSegment 引用定位到同一段本地视频；Coach 当前不读取、上传或播放视频内容；
- 定位完成后提供非侵入式反馈；
- Coach 不得无缘无故切页、滚动主区、播放视频或抢夺焦点。

Coach 的产品命令与当前本地 profile 能力对齐，不限制为只读：查询、导航和用户本轮明确要求的普通可恢复动作可直接执行；删除、覆盖、credential 变更、上传/分享、打开外部购买链接或 Coach 自主推断的副作用操作需要确认。每次回答必须把指标转成动作现象、证据、诊断、训练 cue、预期变化和复测方法，并让因果措辞匹配证据等级。

输入区支持多行文本、发送、停止、上下文查看/移除和少量与当前页面有关的建议问题。第一版不提供任意文件附件、图片生成、通用联网搜索、模型选择器、Prompt 商城或复杂斜杠命令。

### 8.4 页面可用性

| 页面 | Coach 行为 |
|---|---|
| Onboarding | 不显示空 Coach 对话；使用结构化 Provider 激活和本地 fallback |
| New Analysis | 可打开；普通状态遵循已保存展开偏好 |
| Tasks | 可打开，不阻塞任务 |
| History | 可打开，遵循已保存展开偏好 |
| Analysis workspace | 可打开并与三视图联动；第一次分析完成且 Provider ready 时自动展开一次 |
| Settings | 不显示 Coach 对话 |
| 错误/空状态 | 只有 Coach 确实能提供有效帮助时才显示 |

## 9. Onboarding、Settings、Provider 与 Raw Input

### 9.1 Onboarding 与 Settings 分工

首次 onboarding 只提供价值/成本/数据边界说明、已验证连接方式、按需认证、默认 model、连接测试、跳过和恢复；完整 Settings 负责多 Provider 管理、替换/删除 credential、重新认证、停用和高级配置。跳过只使用底部一行次级文字，指针 hover 或键盘 focus 时明确说明：没有 Coach 对话、解释、长期档案、训练计划或产品命令，只保留本地指标、确定性诊断、规则化提示和 History。两者复用同一 capability API、状态机和安全组件，不复制 secret 处理逻辑。

v1 Settings 至少包含：

- **Profile**：当前 `cm/360`、FOV 和分析所需校准摘要；
- **LLM Provider**：provider、model、连接方式、按需认证状态、连接测试、默认选择与恢复操作；
- **Automatic capture / Raw Input**：平台支持、授权、capture、KovaaK process、窗口回放缓冲、runtime、Run finalization 和 trace/video attachment 状态；
- **Theme**：System、Light、Dark；
- **Storage / ownership**：显示总占用和 Run 录像、Raw trace、Analysis artifacts、未完成采集数据的分类占用；说明用户源文件、Run-owned evidence、Analysis-owned artifact 和删除影响。

Theme 默认 System，跟随系统变化；显式 Light/Dark 固定；主题偏好仅属于本地 UI，不进入 Analysis、Provider 认证数据或 Coach payload。

### 9.2 Provider 状态与 secret 边界

Provider UI 至少区分 `unconfigured / ready / testing / connection_failed / disabled`；仅对需要认证的 Provider 增加 `auth_required / auth_expired / needs_reauth`。API key 保存后不得回显，只允许替换或删除；OAuth/device-code 必须呈现等待、取消、超时、成功与重新认证；自定义 OpenAI-compatible provider 才允许编辑 base URL。

前端只能读取非 secret profile 和状态，例如 Provider/model、auth mode、credential source、configured、needs re-auth、最近测试结果。credential 不得进入 localStorage、AnalysisResult、Coach context、消息、普通日志或导出数据。未配置 Provider 时 Coach 只显示恢复 onboarding 或进入 Settings 的激活入口，不提供任何 Coach 功能；本地指标、确定性诊断、规则化提示、报告和 History 不受影响。

### 9.3 Raw Input 状态必须分离

自动采集与 Raw Input 不得被压缩成一个模糊开关。界面分别表达：

1. 当前平台是否支持；
2. 用户是否明确授权；
3. capture 是否启用；
4. KovaaK process 是否存在；
5. runtime/capture 是否健康；
6. 是否捕获到近期数据；
7. trace 是否已与 Run 关联；
8. 未关联 buffer 是否存在及其保留边界。
9. KovaaK 窗口硬件编码是否可用、300 秒回放缓冲是否正在维护；
10. Stats / Performance 是否已触发 Run finalization；
11. 自动 MP4 是否已切窗并与 Run 对齐。

规则：

- Windows 首次开启必须明确说明只采集 KovaaK process gate 内的相对鼠标输入、用途、保存位置类别和关闭方式；
- 自动回放缓冲只捕获 KovaaK 窗口，不捕获完整桌面、其它应用窗口或系统通知；
- 可选托盘/悬浮状态只表达待命、采集中、整理中、完成和失败，不抢焦点，并可关闭；
- 非 Windows 显示不支持，并提供 video-fallback，不把平台限制描述为用户错误；
- disable 只停止新采集，不等同于删除 Run-owned trace 或清空未关联 buffer；
- “清理未关联 buffer”若可用，必须是单独、明确且有后果说明的操作；
- source unavailable、trace quality insufficient、alignment failed 和 runtime error 必须分别表达，并给出可行下一步；
- 不显示绝对路径、raw samples 或敏感 runtime 信息。
- Storage 允许用户分别移除 Run-owned 自动 MP4、Run-owned Raw trace 或未完成采集数据；不提供自动 TTL、自动删除最旧 Run 或一键清空，并保留 Run metadata、Analysis 和用户源文件。

## 10. Desktop / Web Capability 差异

Desktop 和 Web 使用同一产品结构、信息架构、语义组件和视觉语言；能力状态不同，不形成两套产品。

| 能力 | Desktop | Web |
|---|---|---|
| KovaaK Run 自动发现 | 支持进程 gate 内自动采集，并在 Stats / Performance 到达后事后切成独立 Run | 不支持本地自动发现；使用浏览器可提供的手动来源 |
| Stats / Performance 来源 | 可引用本地来源并表达 source unavailable | 由用户显式选择/上传；受浏览器文件权限限制 |
| Raw Input | 仅 Windows、默认关闭、明确 opt-in | 不支持，显示平台/形态限制并提供 fallback |
| MP4 | 自动维护仅 KovaaK 窗口回放缓冲并事后生成 Run-owned MP4；手动 fallback 可选择本地视频 | 由用户显式选择/上传 |
| 本地后台分析 | 使用 Desktop runtime，页面可切走 | 只显示当前 Web 部署真实提供的能力，不伪装成本地 runtime |
| Managed video playback | 使用 Desktop 受控本地能力 | 使用 Web 可访问的受控媒体能力 |
| 产品身份 | 不提供产品账号；当前 OS 用户 / 本地 profile 是数据归属边界 | 不提供产品账号；仅用于受控开发/预览，不成为正式用户数据 owner |
| Provider / credential | 由 app-owned 本地 profile/credential store 与 native/sidecar 命令管理；允许按 local-first 合同在 app-owned SQLite/config 明文持久化，OS secure store 可后续增强但不是 Gate；UI/API 只能 set/replace/delete，不得读回 secret | 浏览器不保存长期 secret；没有经过审查的本地或受控 credential bridge 时不提供持久 Provider credential |
| Coach | 同一产品 Coach shell；按 provider/auth/runtime 状态开放 | 同一产品 Coach shell；按 provider/auth/服务状态开放 |
| 视觉与 IA | 与 Web 相同 | 与 Desktop 相同 |

任何 Desktop/Web 分叉必须以 capability availability 表达。不得让同一路由在两端变成不同产品，也不得用 Web preview 的成功替代 Desktop 验收。

## 11. 完整状态矩阵

### 11.1 通用状态词汇

| 状态 | 用户含义 | 必须行为 |
|---|---|---|
| Initial loading | 首次确认真实数据 | 显示稳定骨架，不先渲染“空” |
| Refreshing | 已有内容正在更新 | 保留旧内容和选择，局部显示更新，不整页闪空 |
| Empty | 请求成功且确实无记录/内容 | 解释为什么为空并提供主要下一步 |
| Ready | 当前能力完整可用 | 显示主要操作及其影响 |
| Partial | 部分结果或 Evidence 有效 | 保留成立部分，明确缺失范围与局部下一步 |
| Unsupported | 当前平台/形态不提供能力 | 中性说明并提供替代路径 |
| Permission required | 能力可用但尚未明确授权 | 解释范围与用途后请求授权 |
| Permission denied | 用户拒绝或系统拒绝权限 | 尊重选择，说明恢复方式和 fallback |
| Offline | 当前网络不可用 | 保留本地可用内容，隔离 Provider/Coach 失败 |
| Service unavailable | 某个 runtime 或服务暂不可用 | 指明受影响能力，保留无关内容并允许重试 |
| Source unavailable | 历史来源曾存在但当前不可访问 | 保留稳定引用和历史记录，提供重新定位/重新添加路径 |
| Invalid / quality insufficient | 来源存在但不能支持当前用途 | 说明具体影响和可修复方式 |
| Alignment failed | 多个来源不能可靠对应 | 禁止跨来源结论；保留可以独立成立的结果 |
| Queued | 任务已创建但尚未执行 | 允许离开，持续可找回，不允许删除 |
| Running | 正在执行可解释阶段 | 显示当前阶段、已保留结果和退出后继续的规则 |
| Retrying | 正在执行明确的新尝试 | 保留前一 attempt 的可解释结果，不伪装成首次运行 |
| Done | 任务完成 | 提供查看结果与回到相关 Run/History 的入口 |
| Failed | 当前 attempt 失败 | 指明失败域、可重试性、保留内容和替代路径 |
| Deleted reference | 被引用对象已删除或不可用 | 保留历史上下文，显示引用失效，不把它当作一般加载错误 |
| Pending analysis | Run 已完成采集但用户尚未开始 Analysis | 留在 History 顶部；单条确认、多条选一条，不进入 Tasks |

“请求失败”与“结果为空”永远是不同状态。页面不得把异常静默转换为空数组、空卡片或默认成功。

### 11.2 页面状态覆盖

| 页面/区域 | 必须覆盖的状态 |
|---|---|
| 条件启动 / Onboarding | Initial loading、onboarding 未完成、Provider unconfigured/configuring/ready/recoverable failure、明确跳过、Empty（无 Run/Analysis）、Ready（已有记录）、本地/runtime 不可用；失败时不得错误跳到 Empty |
| New Analysis | Loading、capture disabled/idle/running/finalizing/failed、Empty Run、single pending Run、multiple pending Runs、Ready、Partial Evidence、Unsupported、Permission required/denied、Source unavailable、Invalid、Alignment failed、创建任务失败、manual fallback |
| Tasks | Empty、Queued、Running、Retrying、Done、Failed、Partial outcome、Service unavailable、重启恢复 |
| History | Loading、Refreshing、Empty、Pending analysis、Ready、Partial records、Source unavailable、Deleted reference、Offline/Service unavailable |
| Analysis workspace | Loading、Ready、Partial、native-only、visual unavailable、Source unavailable、Alignment failed、Failed、Deleted reference |
| Coach | Unconfigured、Ready、generating、stopped、Offline、Provider auth required/expired/needs reauth（仅需要认证时）、connection failed/model unavailable、runtime unavailable、Deleted reference |
| Settings / Automatic capture / Storage | Unsupported、Permission required/denied、capture disabled/enabled、process absent/present、window capture unavailable/active、runtime healthy/error、buffer empty/present、finalizing、trace/video unattached/attached/unavailable、disk low、storage ready/error |

### 11.3 状态呈现规则

- 状态必须同时有文字、必要图标和影响说明，不能只使用颜色；
- 局部能力失败只影响对应区域；
- 刷新失败时优先保留最近可信内容并标记陈旧状态；
- 错误信息分为用户可理解说明、可执行下一步和内部诊断边界；
- token、secret、绝对路径、raw payload 和内部堆栈不得进入用户文案；
- disabled 操作必须解释缺少什么，而不是只降低透明度；
- 对齐失败、Preview、实验性和不可比较是产品语义，不是装饰 badge。

## 12. 响应式与无障碍

### 12.1 三档工作区形态

Coach 根据可用内容宽度采用：

1. **宽窗口：并排侧栏**——主工作区与 Coach 同时可见，Coach 可调宽；
2. **中等窗口：右侧覆盖面板**——不继续压窄视频/图表，不改变主工作区状态；
3. **极窄窗口：临时占满内容区**——提供明确返回主工作区，仍是同一会话而非独立页面。

正式前端还必须保证：

- App shell 在窄窗口中保持主要操作、任务状态和当前页面关系可达；
- 表格、时间轴和图表可以重排或渐进披露，但不得隐藏证据限制和主要下一步；
- Coach 打开/关闭、三视图切换和窗口尺寸变化不得重置视频位置、选择、滚动或草稿；
- 不以设备名称替代可用宽度判断，也不把 Desktop-only 能力等同于宽屏布局。

### 12.2 无障碍验收合同

正式前端以 WCAG 2.2 AA 为最低目标，并至少满足：

- 全部核心流程可用键盘完成；
- 提供清晰、持续可见的 focus，不因主题切换消失；
- 应用工具栏、主工作区、侧栏、页面标题和主要区域使用明确语义与 landmark；
- 提供跳过重复导航的方式；
- Coach drawer、确认对话和文件/权限流程正确管理焦点，支持 Escape 返回；
- 可调宽侧栏提供非指针操作方式；
- 任务进度、完成、失败和 Coach 生成状态使用不过度打扰的 live announcement；
- 视频提供键盘播放、暂停和 seek；
- 图表提供可读标题、单位、摘要和等价文本/数据表达；
- 颜色不是状态、事件、趋势或可比较性的唯一载体；
- 200% zoom 和系统文字放大下，核心操作和信息不丢失；
- 尊重 reduced motion；动效不作为状态的唯一表达，也不阻止用户中断；
- System、Light、Dark 均满足正文、次级文本、outline、focus、disabled、事件标注和主要操作的可读性要求。

## 13. Benchmark v1 边界

Benchmark 不进入本轮 v1 正式前端：

- 不提供 Benchmark 一级路由、History 面板、首页卡片或 external comparison；
- 不进入默认 Coach 上下文；
- 不做 leaderboard、在线 provider、Steam 身份或外部排名；
- 后端或本地 capability 的存在不构成用户可见产品承诺；
- 未来若进入产品，必须先形成单独的产品决策、comparability 合同、隐私/许可边界和前端 spec。

History v1 只负责用户自己的 Run、Analysis 和满足正式 comparability 条件的本地趋势。

## 14. 所有权与删除分离

### 14.1 所有权模型

前端必须用用户可理解的语言区分：

| 对象 | 所有权/归属 | v1 界面原则 |
|---|---|---|
| Stats / Performance 用户源文件 | 用户所有 | Aiming Cookie 不自动移动或删除；失效时标记 source unavailable |
| KovaaK Run metadata | 本地产品记录 | 与源文件、trace、Analysis 分开表达，不把 Run 当作全部文件的容器删除开关 |
| Raw Input trace | KovaaK Run-owned、本地 managed artifact | Analysis 只能引用；默认 local-only；不能随 Analysis 删除 |
| 自动 MP4 | KovaaK Run-owned、本地 managed artifact | Analysis 只能引用；用户可在 Storage 独立移除；不能随 Analysis 删除 |
| 手动 MP4 | 用户源文件；必要时存在 Analysis-owned managed copy | 应用不删除用户原文件；managed copy 只按 Analysis lifecycle 清理 |
| Analysis-owned managed inputs/outputs | Analysis 所有 | terminal Analysis 删除时按 Analysis lifecycle 清理，不扩散到 Run-owned 或 user-owned 对象 |
| Coach conversation / messages | Coach 关系层所有 | Analysis 删除不级联删除对话；引用变为 deleted/unavailable |
| Profile / confirmed long-term facts | 用户资料层 | 不随单次 Analysis 删除；失效来源保留历史标记 |

### 14.2 删除行为

- 进行中、排队中或 retrying 的 Analysis 不可删除；
- 删除 terminal Analysis 只删除该 Analysis 记录及其 Analysis-owned managed artifacts；
- 删除 Analysis 不删除 KovaaK Run、Raw Input trace、用户源 Stats/Performance 或 Coach 历史；
- 删除 Analysis 不删除自动 MP4；
- Run metadata、Run-owned trace、未关联 buffer 和用户源文件不得被合并为一个“删除训练”操作；
- disable Raw Input 只停止未来采集，不触发历史 trace 或未关联 buffer 的隐式删除；
- orphan trace 保持 quarantine，不自动清理；
- Storage 可以按自动采集合同分别移除 Run-owned MP4、Run-owned Raw trace 或未完成采集数据；Run metadata、既有 Analysis 和用户源文件保留，相关引用变为 unavailable；
- 不提供自动 TTL、自动删除最旧 Run 或一键清空；精确 tombstone、事务和恢复若没有 active implementation plan，不得由前端猜测；
- 每个破坏性操作在确认前必须说明：将删除什么、保留什么、哪些 Analysis/Coach 引用会变为 unavailable、是否可恢复。

## 15. 重建时的保留与删除边界

### 15.1 保留并重新审计

当前前端之外的纯 capability/adapter 层可以保留，但必须重新审计其稳定合同、安全边界和 Desktop/Web 行为。保留范围包括：

- 类型化的产品 transport 与数据读取适配；
- 稳定 contract 解析与前端领域类型；
- Stats/CSV 等用户输入的前置解析能力；
- Desktop 文件选择、managed media 与本地 runtime bridge；
- Tauri shell、launch-scoped 安全边界和 Raw Input/Desktop capability integration；
- 与上述能力直接对应、且不绑定 prototype 页面结构的测试与 fixtures。

“保留”不代表原样信任，也不授权把适配层中的临时默认值升级为产品决策。

### 15.2 删除且不继承

正式重建可以删除或隔离：

- 当前 prototype 的产品路由页面；
- 当前临时 App shell / navigation；
- 当前 History、New Analysis、Benchmark 与局部 Coach 产品组件；
- 当前页面级布局、视觉样式和临时全局样式入口；
- 已删除旧 Report、固定 Coach 页面、旧 Settings/Storage/Theme 页面留下的产品结构假设；
- 仅为 prototype 页面形状服务、没有稳定产品合同的 UI 测试和快照。

不得因删除产品 UI 同时删除后端能力、稳定类型合同、Desktop bridge、Tauri runtime、用户数据或必要回归证据。删除范围必须以“产品呈现层”与“能力适配层”分离为前提。

## 16. 验收条件

正式前端只有同时满足以下条件，才可替代 prototype：

1. 正式路由表、无账号条件启动、Provider-first onboarding、App shell 和页面间关系与本文一致；
2. Prototype 的视觉、组件和 IA 未被当作默认事实源复刻；
3. New Analysis 覆盖自动采集状态、单局确认、多局选一条、独立手动 fallback、Evidence 检查和三种 input mode；
4. input-native 在所有相关表面持续标记 Preview / Experimental；
5. multimodal 视觉失败能够保留 native 结果，并在 Tasks 与 workspace 中表现为局部不可用而非整体失败；
6. Tasks 支持后台持续、重启找回、具体失败域、重试和全局完成/失败提醒；
7. History 顶部组织待分析 Run，下面分开组织其它 Run 和 Analysis；未选择 Run 不进入 Tasks、不合并、不自动删除，失败不伪装为空，可比较性 fail closed；
8. Analysis workspace 完整提供 Diagnosis、Video、Data 三视图及稳定定位联动，用户可播放 EvidenceSegment 对应的本地视频段；family 支持状态明确区分正式可用、descriptive、unavailable 与 outcome-only；
9. Coach 以应用级持续侧栏存在，发送前上下文可见/可移除；默认 context/tool results 只使用有界 L1-L3，L0 raw/video/path/原始 CSV/protobuf/private parser/unknown fields 和敏感本地数据不进入；第一次 Analysis 完成且 Provider ready 时自动展开一次；
10. Onboarding 与 Settings 共用 Provider capability/status/secret 边界；跳过 Provider 的次级文字明确没有 Coach；Settings 清楚分离 Provider、Profile、Theme、自动采集、Raw Input、Storage/ownership，并显示分类占用与用户手动管理；
11. Desktop/Web 使用同一 IA 与视觉语言，并分别通过真实 capability 路径验证；
12. 完整状态矩阵在主要页面有可见、可操作且不混淆 Empty/Error/Partial 的实现；
13. System、Light、Dark 使用同一语义 token 集和组件结构，符合视觉合同与设计系统 review gate；
14. 宽、中、极窄三档 Coach 形态、键盘流程、焦点、live 状态、视频和图表替代信息通过无障碍评审；
15. 删除 terminal Analysis 不会删除 Run-owned trace、自动 MP4、用户源文件或 Coach 历史；用户手动移除 Run-owned evidence 后失效引用正确显示；
16. Benchmark 不出现在 v1 正式 UI 或默认 Coach 上下文；
17. 真实 Browser 与 Desktop/Tauri 关键旅程、主要空态/失败态和截图基线完成验证；
18. 正式前端建立唯一 executable token/theme 入口，当前实际路径和验证结果由设计系统与进度文档记录。

任何一项未满足时，应记录为实现差距，不通过修改本文来迁就未完成代码。

## 17. 非目标

本文不授权或不定义：

- Benchmark、leaderboard、在线 provider、Steam 身份和 external ranking；
- 将 input-native Preview 提升为稳定正式能力；
- 新的 flick segmentation、fair metric、CV、alignment 或 Coach 推理算法；
- AnalysisResult、Evidence、artifact、数据库、migration、queue、retry 或 Provider credential/auth schema；
- 后端接口、前端框架、代码目录、组件实现、具体 CSS、动画参数或 token 数值；
- 恢复当前 prototype 或已删除旧 UI 的视觉与组件结构；
- 营销网站、移动原生应用、社区、知识库或未经 PRD 确认的一级产品页面；
- 通用 AI 助手、任意文件附件、图片生成、模型选择器、Prompt 商城或无边界联网/系统权限；
- 自动 retention、静默清理、远端备份、删除最旧 Run 或一键清空；Run-owned evidence 的手动管理只按 active automatic capture spec 的最小边界实现；
- Aiming Cookie 产品账号、登录、注册、鉴权服务器、支付、套餐或结账页面；
- 施工任务、Allowed files、执行顺序、工期或发布排期。
