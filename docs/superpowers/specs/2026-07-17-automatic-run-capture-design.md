# Automatic KovaaK Run Capture — Design Contract

> 状态：active
> 目的：冻结 Desktop 自动采集、事后 Run 切分、分析前选择、手动 fallback 与本地存储管理的产品/系统合同。
> 上游：[`../../PRD.md`](../../PRD.md)、[`../../ARCHITECTURE.md`](../../ARCHITECTURE.md)、[`../../frontend-uiux-design.md`](../../frontend-uiux-design.md)
> 相关合同：[`2026-07-13-kovaak-run-trace-lifecycle-design.md`](2026-07-13-kovaak-run-trace-lifecycle-design.md)、[`2026-07-13-analysis-evidence-coach-context-design.md`](2026-07-13-analysis-evidence-coach-context-design.md)

## 1. 范围与事实前提

本 spec 只定义：

- Windows Desktop 自动采集 Raw Input 与 KovaaK 窗口录像的统一主路径；
- Stats / Performance 到达后的事后 Challenge 切窗与独立 Run finalization；
- 单局、多局和未选择 Run 的产品状态；
- Analysis 创建前的 readiness 与 evidence 选择；
- 独立的手动 `MP4 + Stats CSV` fallback；
- Run-owned evidence 的存储占用和用户手动管理边界。

本 spec 不定义：

- 实时 KovaaK Challenge start/end hook；
- 精确数据库 schema、migration、API payload 或文件 codec；
- 录屏编码器、分段时长、码率、硬件加速或像素级悬浮窗设计；
- 自动 quota、TTL、按最旧优先清理、远端备份或云同步；
- implementation Task、Allowed files 或施工授权。

当前事实前提是：Aiming Cookie 可以检测 KovaaK 进程并监视 Stats / Performance 文件，但没有可靠的实时 Challenge start/end 事件。`Performance.challenge_start_utc + time_limit` 是事后切出 Challenge 时间窗的 canonical anchor。因此自动采集必须采用“进程 gate 内连续分段采集，文件到达后事后切窗”，不能把进程存在误写成 Challenge 正在进行。

## 2. 产品主路径与最低分析条件

正式 Desktop 主路径是：

```text
用户启用自动采集
  → KovaaK 进程出现
  → 同时采集 Raw Input + 仅 KovaaK 窗口录像
  → Stats / Performance 到达
  → 按 Challenge 时间窗生成一条独立 Run
  → 用户切回 Aiming Cookie
  → 单局确认 / 多局选择一条 Run
  → 点击“开始分析”
```

自动采集的目标是尽量为每条 Run 同时获得 `Stats + Performance + Raw + MP4`，但 Analysis 的统一最低条件冻结为：

```text
Stats AND (MP4 OR (Raw + Performance))
```

其中：

- `Stats` 提供射击、命中、击杀、场景和结果事件，是所有模式的必需来源；
- `Performance` 提供 Challenge 时间窗和自动配对锚；
- `Raw` 提供真实 `dx/dy`、时间戳和鼠标按钮，能够同时支持移动轨迹与点击信息；
- `MP4` 提供视觉轨迹、回放、时间定位和 Coach 的直观讲解，但不包含可靠的真实点击事件；
- 自动采集的 MP4 必须已被切分并对齐到同一 Run；手动 fallback 的 MP4 由用户明确选择为该次 Challenge 的录像。

三种 Analysis mode 保持不变：

| Mode | Evidence | 计算与讲解边界 |
|---|---|---|
| input-native Preview / Experimental | Stats + Performance + Raw | 运动学与点击事实来自 Raw；没有 MP4 时仍可分析，但没有视觉回放和时间点讲解 |
| multimodal | Stats + Performance + Raw + MP4 | 只用 Raw 计算输入运动学；MP4 只用于回放、视觉定位和 Coach 的直观讲解，时间轴必须对齐 |
| video-fallback | Stats + MP4 | 使用既有视频轨迹 + Stats 点击/命中信息；不假装拥有 Raw provenance |

如果缺少 Stats，或 Raw 与 MP4 都不可用，该 Run 不能创建 Analysis。它仍可作为部分采集记录保留并显示具体缺失原因。

## 3. Capture Coordinator

Capture Coordinator 是 Desktop 本地能力，不是 Analysis job。它至少表达以下状态：

```text
disabled | waiting_for_kovaak | capturing | finalizing | ready | degraded | error
```

规则：

1. 自动采集必须由用户明确启用；Raw Input 继续遵守 Windows-only、相对输入、本地保存和 KovaaK process gate；
2. 进程出现后开始连续分段采集，进程退出后停止接收新数据并进入 finalization；不得声称已实时知道 Challenge start/end；
3. 录屏只允许捕获 KovaaK 应用窗口，不捕获完整桌面、其它应用窗口或通知；
4. Raw 与录像使用可对齐的时间基准，并保留足以事后切出 Performance Challenge window 的时间信息；
5. Stats / Performance 可以延迟到达；finalizer 必须等待稳定文件并以幂等方式补全 Run；
6. Capture Coordinator 不自动创建 Analysis task。只有用户明确点击“开始分析”后，选中的 Run 才进入 Tasks；
7. 可提供低干扰的托盘/悬浮状态，至少表达待命、采集中、整理中、完成和失败；默认不得抢焦点，用户可在 Settings 中关闭状态浮层；
8. Capture error 只影响对应 evidence。Raw 失败但 MP4 + Stats 可用时仍可走 video-fallback；录像失败但 Raw + Performance + Stats 可用时仍可走 input-native。

## 4. Run Finalizer 与连续多局

Run Finalizer 使用稳定的 Stats / Performance source identity 与 canonical Challenge window，把连续采集流切成独立 Run：

```text
一条 Performance Challenge window
  → 一条 KovaaKRun
  → 对应 Stats / Performance refs
  → 对应时间窗内的 Raw trace（若有）
  → 对应时间窗内的 MP4（若有）
```

规则：

- 连续打五局必须生成五条独立 Run，不能合并为一条训练，也不能要求用户手动切视频；
- 同一 source revision 重复被 watcher 观察时必须幂等，不重复创建 Run 或 Run-owned evidence；
- Stats / Performance 后到时允许把 partial Run 补全为 ready；补全前不得用猜测的文件名或相邻时间静默配对；
- 自动 MP4 与 Raw 都按同一 Challenge-relative timeline 对齐；任一来源对齐失败时禁止跨来源结论，但保留独立成立的 evidence；
- finalization 完成后，Run 的分析状态默认为 `pending_analysis`，而不是 queued/running；
- 未达到最低分析条件的 Run 使用 `incomplete_evidence` 或等价可解释状态，不进入 Tasks。

## 5. Analysis 前选择规则

用户从 KovaaK 切回 Aiming Cookie 后，在 New Analysis 或 History 的“待分析训练”区域确认本次 Run：

- 本次只产生一条可分析 Run：默认选中该 Run，但仍由用户点击“开始分析”确认；
- 本次产生两条或更多可分析 Run：用户必须明确选择一条再开始分析；第一版每次只创建一条 Analysis task；
- 未选择的其它 Run 保持 `pending_analysis`，进入 History 顶部“待分析训练”；
- 未选择 Run 不进入 Tasks、不自动分析、不自动合并、不自动删除；
- 用户之后可以从 History 选择任何仍可分析的 Run；同一 Run 可以拥有零到多个 Analysis，重新分析不能静默覆盖旧结果。

History 的固定组织顺序是：

1. **待分析训练**：满足最低条件、尚未创建 Analysis 的 Run；
2. **训练记录**：其它 Run 状态与 evidence 详情；
3. **分析记录**：queued、running、done、failed 与 retry attempt。

## 6. 手动 fallback

手动导入是独立 fallback 界面，不与自动采集主流程混成一张复杂 evidence 表单。

规则：

- 用户明确选择一段 Challenge MP4 和对应 Stats CSV；
- Aiming Cookie 不能仅凭用户选择的 MP4 猜测它对应哪个 Stats / Performance 文件；
- 手动 fallback 不假装拥有 Raw Input，也不承诺自动获取对应 Stats / Performance；
- 满足 `MP4 + Stats` 后可创建 video-fallback Analysis；Performance 可作为额外来源，但不是该模式必需输入；
- 手动来源的原文件仍归用户；如为 Analysis 创建 managed copy，继续遵守 Analysis-owned lifecycle。

## 7. Ownership、存储占用与手动管理

| 对象 | 归属 | 删除边界 |
|---|---|---|
| 用户 Stats / Performance 源文件 | 用户所有 | Aiming Cookie 不移动、不改写、不删除 |
| 自动 Raw trace | Run-owned managed evidence | 删除 Analysis 不删除；用户可在 Storage 中独立移除，Run/Analysis 引用改为 unavailable |
| 自动录制并切分的 MP4 | Run-owned managed evidence | 删除 Analysis 不删除；用户可在 Storage 中独立移除，视频回放与视觉引用改为 unavailable |
| 手动导入 MP4/Stats 的用户源文件 | 用户所有 | 应用只移除引用或自己的 managed copy，不删除用户原文件 |
| Analysis inputs/outputs | Analysis-owned | 只随 terminal Analysis lifecycle 清理，不扩散到 Run-owned evidence |
| 未完成/未配对采集数据 | app-managed recovery data | 必须计入 Storage，并允许用户明确丢弃；不得伪装成已完成 Run evidence |

Settings 的 Storage 第一版必须：

- 显示 Aiming Cookie 总存储占用；
- 分别显示 Run 录像、Raw trace、Analysis artifacts 和未完成采集数据的占用；
- 支持按 Run 查看自动录像与 Raw trace 的大小和引用影响；
- 允许用户分别手动删除 Run-owned MP4、Run-owned Raw trace 或未完成采集数据；
- 删除前说明哪些未来 Analysis mode、回放和 Coach/Analysis 引用会变为 unavailable；
- 删除后保留 Run metadata、既有 Analysis 记录和用户源 Stats / Performance；
- 不提供静默自动清理、自动 TTL、自动删除最旧 Run 或模糊的“一键清空所有数据”。

低层 capture buffer 可以继续遵守已有的有界瞬态缓冲合同；它不是用户已经获得的 Run evidence。任何已经 finalization 为 Run-owned evidence、或仍被标记为可恢复的未完成采集数据，都不得在没有用户动作和明确合同的情况下静默删除。

精确 DB/file 原子顺序、tombstone、失败恢复和并发删除必须由后续 active implementation plan tests-first 冻结；本 spec 不授权 executor 临场实现。

## 8. 失败与部分 Evidence

至少区分：

- KovaaK process detection unavailable；
- window capture permission / window unavailable；
- Raw Input permission denied / runtime error；
- Stats missing / invalid；
- Performance missing / invalid；
- source pairing conflict；
- Raw / MP4 cut failed；
- timeline alignment partial / failed；
- disk low / capture write failed；
- finalization interrupted / recoverable data present。

失败呈现必须说明：保留了什么、缺了什么、当前能否分析、可使用哪个 mode、用户下一步是什么。不得把 Raw 失败、录像失败、文件延迟和整个 Analysis 失败合并为同一个“采集失败”。

## 9. 验收条件

1. 自动采集启用后，KovaaK 进程期间同时获得 Raw 与仅 KovaaK 窗口录像；
2. 不依赖实时 Challenge hook，能够通过 Stats / Performance 事后把连续多局切成独立 Run；
3. 单局默认选中并等待确认，多局要求选一条；其余 Run 保留为 `pending_analysis`；
4. 未选择 Run 不进入 Tasks、不合并、不自动删除；
5. Analysis readiness 严格遵守 `Stats AND (MP4 OR (Raw + Performance))`；
6. multimodal 只用 Raw 计算输入运动学，MP4 仅提供回放、视觉定位和 Coach 直观讲解；
7. 手动 `MP4 + Stats` fallback 在独立界面可达，且不自动猜测对应 CSV；
8. History 顶部可找到待分析 Run，之后仍能回到任意保留 Run；
9. Storage 显示分类占用并由用户手动管理，不静默自动清理 Run-owned evidence；
10. 删除 terminal Analysis 不删除自动 Raw、自动 MP4、Run metadata 或用户源文件；
11. 状态浮层不抢焦点且可关闭；
12. API/UI 不泄露绝对路径、原始 Raw samples 或其它应用窗口内容。
