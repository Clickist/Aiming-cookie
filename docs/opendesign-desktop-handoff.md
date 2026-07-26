# OpenDesign Desktop Frontend Handoff

> **状态：active 派生设计交接入口。** 本文帮助 OpenDesign 快速进入 Aiming Cookie Desktop 的正式前端设计工作，但不新增产品事实、不覆盖上游文档，也不构成任何 implementation Task 的编码授权。
>
> **当前范围：** 只设计桌面产品前端。营销 Landing 保留为后续独立交付，等正式产品界面、真实截图和演示 MP4 可用后再设计与实现。

## 1. 本次任务

Aiming Cookie 是一个面向 KovaaK 训练者的本地优先 Windows 桌面应用。它把一次训练组织为：

```text
Run -> Evidence -> Analysis -> Training -> Retest
             Aiming Coach 贯穿解释、行动与复测
```

OpenDesign 的职责是补齐现有合同有意留下的最后一段设计空间：

- 具体页面 Layout、grid、比例、留白、视觉层级与组件组合；
- 宽、中、窄状态下的响应式行为与 Coach 呈现；
- 页面间一致的 loading、empty、partial、error、unavailable 与恢复体验；
- 视觉方向在真实产品页面中的落地，而不是重新定义产品范围；
- 经点点确认后的开发交接材料。

本次不负责：

- 修改 PRD、Architecture、input mode、数据归属、删除或 Provider 安全合同；
- 新增 Dashboard、产品账号、订阅、Benchmark、社区或独立 Coach 页面；
- 实现 Landing、安装器、更新器、签名或发布链；
- 在设计阶段修改正式前端、backend、Tauri、依赖或 lockfile。

## 2. 事实源读取顺序

发生冲突时，按以下顺序处理；不得让 prototype、设计稿或 OpenDesign 产物反向覆盖上游合同：

1. [`PRD.md`](PRD.md)：产品目标、范围、用户语义与非目标；
2. [`ARCHITECTURE.md`](ARCHITECTURE.md)：系统、数据、依赖与安全边界；
3. [`frontend-uiux-design.md`](frontend-uiux-design.md)：桌面产品骨架、页面职责与交互关系；
4. [`superpowers/specs/2026-07-13-frontend-product-reconstruction-design.md`](superpowers/specs/2026-07-13-frontend-product-reconstruction-design.md)：正式前端局部设计合同；
5. [`DESIGN-cursor.md`](../DESIGN-cursor.md)：视觉方向、语义色板与共同基础；
6. [`design-system.md`](design-system.md)：token、主题、primitives 与视觉评审治理；
7. [`archive/completed/plans/2026-07-13-frontend-product-reconstruction.md`](archive/completed/plans/2026-07-13-frontend-product-reconstruction.md)：已完成 Task 2-7 的施工顺序、Allowed files、Tests first 与 Stop rule；
8. [`PROGRESS.md`](PROGRESS.md) 与当前代码：当前实际完成度和可运行能力。

OpenDesign 应通过链接读取原文，不在新产物中复制整段长期事实。发现冲突时停止并报告具体文件与条目，不自行选择一个版本继续设计。

## 3. 当前实现边界

- 历史 product UI prototype 已删除；不得恢复或把旧截图当成正式视觉基线。
- `webapp/frontend/app/**` 正式路由、共享 UI/primitives、capability adapters、tests 与 `src-tauri/**` runtime 均已存在。
- Frontend reconstruction Task 1–7 已完成，production build、Browser E2E、截图、accessibility 与 focused Desktop matrix 已形成当前验证基础；这不等于 release-ready。
- 已完成的 reconstruction plan 已归档；后续 UI、Landing 或发布工程必须使用新的 active Task。
- OpenDesign 第一轮输出仍是设计证据，不反向覆盖 PRD、Architecture、UI/UX 合同或当前代码事实。

## 4. 已冻结与可设计内容

### 4.1 已冻结，OpenDesign 不得改变

- App shell 是主工作区加右侧持续 Coach 关系层；没有独立 `/coach` 页面。
- 正式路由、页面职责和页面间进入/退出关系使用 active frontend reconstruction spec。
- Tasks 是独立全局任务中心，History 不能替代 Tasks。
- Analysis workspace 包含 Diagnosis、Video、Data 三个内容视图。
- New Analysis 支持自动采集后的 Run 选择，以及 `input_native`、`multimodal`、`video_fallback` 三种真实能力模式。
- Provider onboarding 不等于产品登录；产品没有 Account 菜单、注册、订阅或额度墙。
- Coach、History、Analysis 与本地能力在 Provider 不可用时按合同局部降级，不能整页失败。
- Browser 与 Desktop 使用同一产品 IA 和视觉语言，只通过 capability state 表达能力差异。
- Run、Analysis、Raw trace、自动 MP4、用户 Stats/Performance 和 managed copy 的归属与删除语义彼此分离。
- Benchmark 不进入 v1 正式 UI。

### 4.2 留给 OpenDesign 的设计空间

- 页面最终 grid、内容轨道、区域比例与视觉节奏；
- 组件在页面中的组合方式和信息展开层级；
- Coach 并排、drawer、临时占满内容区三种形态的具体 Layout；
- 导航、页面标题区、状态区和主要操作的具体排布；
- onboarding 的最终像素布局；
- 精确 breakpoint、动效时长、过渡方式与视觉微调；
- 图表、时间轴、视频、EvidenceSegment 和 Coach 引用之间的视觉联动；
- 空态、局部失败、刷新与恢复的具体呈现；
- 在现有语义 palette 内提出 token 映射和最小 primitives 组合。

这些设计选择先作为提案交给点点确认，不能在未确认时静默升级成产品或代码合同。

## 5. 设计方向

### 5.1 产品气质

- 安静、精密、克制、可信，适合长时间查看训练证据；
- 是工作型桌面工具，不是营销型 SaaS Dashboard；
- 信息密度集中在图表、时间轴和数据表，控制区保持清晰呼吸空间；
- 暖中性 canvas、纸面感 surfaces、机械感的小圆角和 hairline 层级；
- orange 只承担主要操作和 active analysis 信号，保持稀缺；
- blue、green、red 只承担已冻结的分析事件或状态语义。

### 5.2 避免的方向

- 超大标题、营销 hero、功能卡片墙或页面 section 卡片化；
- cards inside cards、每个区块都漂浮、重阴影或大面积胶囊；
- 通用紫色渐变、装饰性光球、无含义的 3D 或游戏 HUD 模仿；
- 用头像、账户菜单、在线状态或付费入口暗示不存在的产品账号；
- 用颜色、动画或百分比单独表达任务和证据状态；
- 为了展示“功能丰富”同时展开所有详情、所有指标和所有 Coach 内容。

### 5.3 第一轮工作文案

第一轮设计稿使用简体中文工作文案，以便点点直接评审信息层级和真实文案长度。这只是设计阶段输入，不冻结未来国际化范围或代码方案。布局同时使用长中文错误说明和约 `1.3x` 的英文扩展长度做压力检查；不得通过缩小到难以阅读的字号解决溢出。

## 6. 正式页面范围

| 路由或表面 | 主要任务 | OpenDesign 必须解决的设计问题 |
|---|---|---|
| Conditional `/` | 根据真实状态进入 onboarding、New Analysis 或 History | 启动判断期间如何避免错误空态和闪烁 |
| `/onboarding` | 连接 Provider 或明确选择本地模式 | 价值、费用、数据边界和连接步骤如何不压垮首次用户 |
| App shell | 承载全局操作、Tasks、Coach、Settings 与主工作区 | 宽度分配、导航层级、Coach 开关和跨页面连续性 |
| `/analyze` | 选择 Run、检查 evidence、选择可用 mode 并开始 Analysis | 自动采集、多局选择、模式解释、局部错误和主操作优先级 |
| `/tasks` | 查看 queued/running/done/failed/retry 状态 | 后台阶段、失败归因、恢复动作和离开页面后的持续可见性 |
| `/history` | 组织 pending Run、其它 Run、Analysis 与趋势 | 轻列表、lazy detail、状态差异和不制造伪趋势 |
| Run inspector | 查看训练身份、evidence 和来源状态 | 来源影响、修复入口、Storage 入口和路径隐藏 |
| `/analysis/:analysisId` | 在 Diagnosis、Video、Data 中理解一次 Analysis | 结论、证据、回放、指标、limitations 与跨视图定位 |
| Coach sidebar | 解释、训练、复测与产品操作 | 上下文可见性、引用、工具确认、停止/重试和三档响应式 |
| `/settings` | Provider、主题、Profile、自动采集、Raw Input、Storage | 多组设置的信息架构、安全状态和删除影响说明 |

## 7. 第一轮代表页面

不要第一轮同时精修全部路由。先用以下组合证明设计方向能够承受真实产品复杂度：

1. **App shell + History**：证明全局层级、信息密度和 pending Run 入口；
2. **New Analysis**：证明 Run/evidence/mode 的复杂状态可以被普通用户理解；
3. **Analysis Diagnosis + Video/Data 切换**：证明结论与证据不是通用 Dashboard；
4. **Analysis + Coach 并排**：证明主工作区和长期 Coach 关系可以同时成立；
5. **一张状态板**：覆盖 loading、empty、partial、offline、source unavailable、permission denied、alignment failed 和 retryable error。

第一轮应提供：

- 一个主方向；
- 一个结构或视觉取向明显不同的备选方向；
- 两个方向各自的取舍说明；
- 默认 `1280 x 820` 和最小桌面窗口 `960 x 640` 的关键截图；
- Coach 宽、中、窄三种形态。精确 breakpoint 由 OpenDesign 提议，确认前不是合同。

## 8. 数据与状态输入

设计不得只在理想 mock data 下成立。第一轮至少覆盖以下数据压力：

- 没有 Run；一条可分析 Run；多条待分析 Run；
- 完整 multimodal、native-only、video fallback；
- Raw available 但 MP4 unavailable；视觉校验失败但 native 结果成立；
- long scenario name、较长中文错误说明、多个 limitation；
- Tasks 中同时存在 queued、running、failed 和 done；
- History refresh 失败但保留旧内容；
- Analysis 是 `supported`、`descriptive`、`unavailable` 或 `outcome-only`；
- EvidenceSegment 可播放、来源不可用、引用已删除；
- Provider 未配置、测试中、ready、连接失败、需要重新认证；
- Coach 正在生成、用户停止、单轮失败、重试、上下文已移除；
- Storage 有 Run MP4、Raw trace、Analysis artifacts 和 incomplete recovery 四类占用。

生产实现中的 fixture 必须从公开 DTO、contract tests 或经过脱敏的真实响应生成。设计稿不得包含 absolute path、Raw samples/trace、secret、token、内部堆栈或私有 payload。

## 9. Browser 与 Tauri 边界

Browser 是快速设计和大部分 UI 状态验收环境，不是 Desktop capability 的替代证明。

| 能力 | Browser 设计/验收 | Tauri 验收 |
|---|---|---|
| Layout、主题、响应式、键盘、状态矩阵 | 主要环境 | 复核窗口和 WebView 差异 |
| 普通 API、History、Analysis、Coach | 通过代理或 fixture | 通过 per-launch loopback runtime |
| 文件输入 | 浏览器可访问文件 | 原生 scoped picker 与 path import |
| Run discovery、Raw Input、自动采集 | 只能表达 unsupported 或 fixture | 必须验证真实 capability state |
| managed MP4 | Browser API URL | Tauri asset protocol |
| 启动、退出、后台恢复、CSP | 不构成证明 | 必须真实验证 |

实施顺序是 Browser-first、Desktop-aware：先在 Browser 快速迭代，每完成一个垂直流程就运行一次 `tauri dev` smoke；不能等全部 Browser 页面完成后才第一次接 Tauri。安装包和 release packaging 最后处理。

当前 Next/Tauri production build 输出仍需要独立 implementation Task 冻结。OpenDesign 设计阶段不得修改 `next.config.ts`、`tauri.conf.json`、`package.json` 或 lockfile 来绕过它。

## 10. 组件与无障碍最低要求

设计必须能落到 Task 2 约束的最小 primitives，而不是依赖一套未经批准的新通用组件库：

- Button、IconButton；
- Badge/Status、Notice；
- Field；
- Panel/Surface；
- Tabs；
- Drawer/Sheet；
- Toast；
- Loading/Empty/Error；
- Dialog。

所有主流程同时设计：

- visible focus、合理 focus order、skip link 与 landmarks；
- drawer/dialog focus trap、Escape 与非指针调整 Coach 宽度；
- 不只依靠颜色的状态、事件和趋势表达；
- reduced motion；
- 200% zoom 和长文案；
- 主要触控/点击目标、视频键盘控制和图表文本替代；
- Light、Dark、System 使用同一组件结构和相同 token key 集。

## 11. OpenDesign 交付阶段

### Stage A：方向探索

- 读取本文和第 2 节事实源；
- 输出第 7 节代表页面的主方向与备选方向；
- 对两者执行 UX、响应式、状态、无障碍和 anti-pattern 自审；
- 不修改 production frontend。

### Stage B：选定方向深化

点点确认方向后：

- 完成全部正式页面的高保真设计和交互说明；
- 冻结被采用的 Layout、breakpoint、motion 与 component composition；
- 提供状态板、响应式板和页面间联动说明；
- 标明任何仍缺 backend DTO、真实 fixture、产品决定或资产的区域。

### Stage C：生产实现交接

只有点点明确指定 active frontend reconstruction plan 的具体 Task 后，executor 才能编码：

1. Task 2：executable tokens、theme、最小 primitives；
2. Task 3：Provider onboarding、App shell、New Analysis、Tasks；
3. Task 4：History；
4. Task 5：Analysis workspace；
5. Task 6：Coach sidebar、Settings；
6. Task 7：Browser/Desktop E2E、screenshots 与 accessibility。

设计稿不能被用来跳过 Task 顺序、Allowed files、Tests first 或 Stop rule。

## 12. 验收标准

OpenDesign 的设计方向只有在以下条件全部满足时才可进入生产实现：

1. 用户不需要理解内部对象或文件结构，就能知道当前是哪次训练、拥有哪些证据、能做什么和下一步是什么；
2. Run、Evidence、Analysis、Tasks、History 和 Coach 的职责不混淆；
3. 主流程在完整、partial、unavailable 和失败状态下都可继续理解和操作；
4. Provider 故障不阻塞本地 Analysis、History 或视频回看；
5. Analysis 的 Diagnosis、Video、Data 各有清晰职责，没有复制成三个完整报告；
6. Coach 在宽、中、窄窗口均可用，不遮挡或压坏关键内容；
7. 视觉与 `DESIGN-cursor.md` 的语义 palette 和克制方向一致；
8. 没有新增账号、Dashboard、Benchmark、付费墙或未经批准的能力；
9. 设计可以由现有技术栈和 active plan 的最小 primitives 实现；
10. 截图中不存在路径、Raw trace、secret、token、假指标或过度承诺。

## 13. Landing 延后

营销 Landing 不属于本次 OpenDesign 桌面前端工作。当前只保留以下上游边界：它将解释 Coach 价值、Provider 成本、本地 fallback 和数据边界，并提供真实演示与 Windows 下载入口；不收集 credential，不建立产品账号。

Landing 的最终 Layout、演示时长和具体教程留到以下条件满足后再交给 OpenDesign：

- 正式桌面视觉方向已经确认；
- 主要页面和状态已经在 Browser 与 Tauri 中验收；
- 可以生成真实产品截图和演示 MP4。

installer 完成不是开始设计 Landing 的前置条件；但 Landing 发布前必须接入经过发布 Gate 的真实 installer、版本、校验值与下载地址，不能使用占位下载或未验证的发布承诺。

## 14. 建议的首次 OpenDesign 指令

```text
Read docs/opendesign-desktop-handoff.md completely, then follow its source-order links.
This is a design-only first round: do not edit production frontend, backend, Tauri,
package files, plans, specs, PRD, or Architecture.

Design Aiming Cookie Desktop as a quiet, precise, information-dense Windows analysis
tool. Produce one primary direction and one meaningfully different alternative for
the representative screens in section 7. Show default 1280x820, minimum 960x640,
and all three Coach responsive forms. Exercise the state pressure in section 8.
Explain trade-offs and self-review each direction against section 12.

Do not design the marketing Landing in this round.
```
