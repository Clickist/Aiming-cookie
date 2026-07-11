# Aiming Cookie — 产品与工程路线图

> **状态**：当前执行基线
> **建立日期**：2026-07-10
> **产品上游**：`docs/PRD.md`
> **架构上游**：`docs/ARCHITECTURE.md`
>
> 本路线图按纵向产品闭环组织，不按前端、后端、Desktop 或页面数量堆任务。日期是执行窗口，不代表在 Gate 未通过时自动发布。

## 1. 发布定义

### 1.1 长期目标：完整产品 v1

完整产品仍按 PRD 定义，包括：

- Desktop hybrid 与本地分析；
- flicking deterministic diagnosis、Report 和 Coach；
- 本地 History、趋势、删除、导出和导入；
- login / verified identity；
- 完成通知、失败处理和日志；
- 可安装、可恢复、可维护的发布形态。

### 1.2 2026-07-13 至 2026-07-19：内部技术预览

本窗口交付的是：

> **受控环境中的 flicking-only 内部技术预览**

它用于验证核心价值链和运行可靠性，不命名为“完整产品 v1”，也不作为公开注册 Web、正式受邀 Web 或 Desktop 正式版发布。

目标闭环：

```text
真实 MP4 + Stats CSV
→ 创建分析
→ 可观察、可恢复地完成
→ deterministic Report
→ 可选 Coach（用户级常驻关系；旧 session-bound route 仅作迁移兼容）
→ 写入并找回最小 History（列表、回看、删除；不含趋势）
→ 仅删除 done/failed analysis 与相关文件；Coach 引用变为已删除，消息/长期档案保留
```

明确不包含：

- 公开注册和支付；
- 云同步；
- tracking Web 接通；
- Tauri/Electron 安装包；
- 签名、公证、自动更新；
- 全面 IA/视觉扩张。

## 2. 当前基线与差距

截至 2026-07-12 当前工作区对账（线 B 薄切片 Task 5 回归后）：

- 全仓单一 pytest 入口 `245 passed, 1 skipped`；分开核验为 core `116 passed`、`webapp/tests` `129 passed, 1 skipped`；frontend `tsc --noEmit` 与 production build 已通过；
- versioned result/error/artifact contracts 及 legacy adapters 已完成集成验收，runtime-contracts plan 已归档，不得重复实施；
- worker lease、heartbeat、stale recovery、retry 和 lease ownership 修复已实现并有测试；
- 最小 History 的列表、详情回看、done/failed 删除和前端页面已实现；趋势/对比按本路线图后移到 P1；
- `[x]` **常驻 Coach 数据归属（线 A）**：`coach_threads` / `coach_messages` / `coach_analysis_refs`、删除分析不级联抹 Coach 消息、用户级 `/coach` API 与页面、旧 `chat_messages` 迁移路径 — **当前代码已具备**（部分改动待 commit；仍以 P0 集成 Gate 为准，非预览 Go）；
- `[x]` **线 B Pi Coach runtime 薄切片**：`third_party/pi` vendored、`webapp/coach-runtime` 单轮 turn、Python subprocess 桥、primary/session chat 默认 `COACH_RUNTIME=pi` + `COACH_RUNTIME_FALLBACK_PYTHON` — **当前代码已具备**（无长期 daemon / 云账单 / Desktop 沙箱；待 commit + 集成 Gate）；
- 显式 session workspace、流式上传、完整文件生命周期、可信身份、可运营部署和 browser E2E 尚未完成；
- Desktop 仍处于研究阶段，没有可执行工程。

因此当前判断是：

- **完整产品 v1：No-Go**；
- **内部技术预览：完成以下 P0 Gate 后有条件 Go**。

## 3. 优先级

### P0 — 内部预览阻塞项

| 项目 | 用户/产品价值 | 依赖 | 完成定义 | 不做的代价 |
|---|---|---|---|---|
| 发布范围与文档对齐 | 团队对同一目标工作 | 本文与 PRD | PRD、Architecture、Roadmap、Progress 无范围冲突 | 继续并行扩 scope，无法判断完成 |
| Versioned contracts | History/Desktop 不再依赖偶然 JSON | 当前 result/job | result/job/error/artifact 有版本、NaN、迁移和 TS 校验策略 | 数据写入后无法可靠读取或迁移 |
| Worker recovery/retry | 分析不因进程退出永久卡死 | Job contract | lease、heartbeat、attempt、stale recovery、显式 retry 有自动测试 | 用户 session 永久停在 running |
| 最小 History 闭环 | 用户能找回分析而非一次性页面 | Result contract | done 自动写入；列表、状态/摘要、详情回看、仅删除 done/failed 可用；**不含趋势** | 分析仍是一次性页面 |
| 常驻 Coach 数据归属迁移 | 删除分析不抹掉教练关系 | Pi assessment + Spike + 线 A plan（Task 1–5） | Coach 关系/消息不再由 analysis session 独占；分析引用支持已删除态；旧 chat 可回退迁移 — **实现已具备，待 commit + 集成验收** | P0 删除 Gate 与 PRD 冲突、用户对话丢失 |
| 文件生命周期 | 可控磁盘和隐私 | Workspace/manifest | 流式写入、无自动 TTL、用户主动删除、orphan scan、quota/低磁盘保护 | 磁盘泄漏、隐私和服务中断 |
| 可信预览访问边界 | 防止客户端伪造 owner | 部署入口 | VPN/SSO/可信代理注入身份；浏览器 header 不作为信任源 | 任意用户可伪造身份读写 session |
| 可运营运行基线 | 故障可发现、可恢复 | Runtime | supervisor、health/readiness、structured logs 和基本指标 | 只能靠开发者盯进程救火 |
| Release test gate | 避免“单测绿但产品断” | 以上闭环 | 统一测试入口、真实素材 E2E、browser E2E、build 可重复通过 | 发布回归只能由用户发现 |

### P1 — Alpha 闭环质量

| 项目 | 完成定义 |
|---|---|
| 完成通知与任务找回 | 任意页面可看到完成/失败；重启后能回到未读结果 |
| 错误分类与可操作文案 | CV、输入、LLM、网络、磁盘错误使用稳定 code，并展示 retry/修复动作 |
| History 趋势/对比/筛选 | 在最小 P0 回看基础上补趋势、跨次对比和筛选；不反向扩大 P0 Gate |
| History export/import | 导出包有 schema/version/manifest；重复导入和旧版本行为有测试 |
| 真实 LLM usage | 记录 provider/model/input/output usage 和实际成本，不再只靠固定估算 |
| CV 资源限制 | 单 job timeout、并发限制和取消/失败策略明确 |
| Retention 与磁盘告警 | 可配置保留策略；达到阈值前拒绝新任务并给出清理动作 |
| 默认路由 | 无历史默认 Upload；有历史默认 History；查询失败行为有测试 |

### P2 — 验证后扩展

- tracking Web 接通与真实阈值标定；
- verified public auth、credits、subscription；
- optional cloud sync；
- Desktop shell、sidecar、installer、签名、公证、更新；
- ④ 动态训练计划的完整前端体验；
- 深度 IA 与视觉收敛。

P2 不代表取消；它表示这些能力不能抢占 P0 的发布可靠性和 History 纵向闭环。

## 4. 2–4 周纵向路线

### Week 1：2026-07-10 至 2026-07-19

**结果目标**：内部 flicking 技术预览达到可放行状态。

工作顺序（`[x]` 表示当前代码已具备，仍以集成 Gate 为准）：

1. `[x]` 实现并完成最小 result/job/error/artifact contracts 集成验收；plan 已归档；
2. `[x]` 实现 worker lease、heartbeat、retry、stale recovery 和 lease ownership；
3. `[x]` 完成最小 History：done 写入 → 列表/状态摘要 → 详情回看 → 仅删除 done/failed；**趋势不在 P0**；
4. `[x]` 完成 Pi 源码接管 assessment 与隔离 Spike，固定基线版本/package、纳入/删改边界、许可证/依赖义务和运行边界；
5. `[x]` 常驻 Coach **数据归属** implementation plan（`2026-07-11-persistent-coach-data-ownership.md`）Task 1–5 — 用户级 Coach 关系/消息/分析引用与旧 session chat 迁移 — **代码已具备**；
6. `[x]` 线 B Pi Coach runtime 薄切片（`2026-07-12-pi-coach-runtime-integration.md`）Task 1–5 — vendor Pi、coach-runtime、API 默认 Pi + Python fallback — **代码已具备**（加厚项另 plan）；
7. 建立显式 session workspace、流式上传与文件删除规则（默认无自动 TTL）；
8. `[x]` 修复全仓单命令 pytest collection；
9. 增加 health/readiness、supervisor、structured logs 和基本指标；
10. 建立真实素材 E2E 和 browser E2E release gate；
11. 仅在可信访问层后部署预览。

**冻结项**：tracking 接通、支付、云同步、Desktop 正式工程、横向新页面和新的视觉方向。


### P0 实施计划队列与 executor 边界（2026-07-11）

Roadmap 只决定顺序，不授权 executor 直接施工。剩余 P0 必须分别形成小型 implementation plan：

1. Pi 源码接管 assessment + 隔离 Spike；
2. 基于已确认 Pi 源码基线与产品化边界的常驻 Coach migration 替代 plan；
3. session workspace + streaming upload；
4. artifact deletion / no-auto-TTL / orphan / quota / low-disk；
5. health/readiness + structured logging + supervisor；
6. trusted preview deployment boundary；
7. 真实素材 E2E + browser E2E release gate。

交给 Fast 或较弱模型的每个 Task 必须冻结：单一 Task、Allowed files、精确 Tests first、schema/default/migration 决策、acceptance checklist 和 Stop conditions。出现代码与 plan 不一致、需要扩大文件范围、测试无法运行或需要新增架构决策时立即停止；不得从本 Roadmap 自选任务，也不得自动进入下一 Task。

### Week 2：2026-07-20 至 2026-07-26

**结果目标**：从“能跑”升级为完整的本地 History 回访闭环。

```text
分析成功
→ History 持久化
→ 列表/筛选
→ session 回看
→ 趋势/对比
→ 删除
→ export/import
```

同时补齐完成通知、错误文案、任务找回、真实 LLM usage、存储占用提示和 quota 告警。

### Week 3：2026-07-27 至 2026-08-02

**结果目标**：抽出可被 Web worker 与 Desktop sidecar 共用的 Local Analysis Runtime。

完成定义：

- Domain Core 不依赖 FastAPI/SQLite/全局 output；
- workspace 和 artifact manifest 显式传入；
- 同一输入 + 同一分析版本得到稳定 result；
- worker crash/retry/recovery 有集成测试；
- Web API 只做身份、输入校验和 Runtime adapter；
- 形成 Desktop spike 所需的最小 IPC surface。

### Week 4：2026-08-03 至 2026-08-09

**结果目标**：基于实际反馈选择一个分支，不并行开两条正式化路线。

#### 分支 A：Web 正式化

适用条件：内部用户价值反馈成立，并且近期需要跨设备访问。

范围：

- verified auth；
- production DB/queue 选择；
- backup、monitoring、rate limit；
- 受邀用户发布和数据安全核验。

#### 分支 B：Desktop spike

适用条件：本地分析、隐私和成本优势得到确认，完整产品坚持 Desktop hybrid。

范围：

- 选择一个 shell 做 time-boxed spike；
- 启停 Python sidecar；
- 本地文件选择 → Runtime → Report → History；
- 验证安装体积、启动时间、杀进程恢复和开发调试体验；
- spike 后再决定 Tauri/Electron、IPC 和打包工具。

**不允许**：Week 4 同时启动正式 Web hardening 和正式 Desktop 产品化。

## 5. 内部技术预览 Go/No-Go Gates

只有全部满足才 Go：

### 产品闭环

- 用户通过 UI 完成 MP4 + CSV 上传、处理、Report、Coach（配置 LLM 时）和 History 回看；
- deterministic report 的指标、诊断和处方在无 LLM 时仍完整可用；
- 删除 done/failed session 后，History 与受管理 artifact 被移除；已存在的 Coach 消息/长期档案不被级联删除，相关引用显示为已删除；
- 完成或失败后，用户不需要工程师查数据库才能找回任务。

### 可靠性

- worker 在 job `running` 期间被终止后，job 可按策略自动恢复或进入可重试失败态；
- 不存在无期限 `running`；
- 相同输入重复执行不会污染其他 session workspace；
- 磁盘不足、无效输入、CV 失败和 LLM 失败可区分并给出动作；
- API/worker 重启后已完成 History 仍可读取。

### 安全与数据

- 预览入口位于 VPN、SSO 或可信代理后；
- 服务端 owner 不信任浏览器自行提供的 user id；
- 跨 owner session、chat、video、timeline、History 访问被拒绝；
- 原视频保留与删除规则对测试用户可见且实际生效；
- 日志不记录 API key、token 或完整敏感输入。

### 验证与运行

- core tests 和 Web backend tests 通过；
- 全仓测试有单一可重复执行入口，不再发生 `tests.conftest` import mismatch；
- 仓库真实 MP4 + CSV E2E 连续通过；
- browser E2E 覆盖 upload → processing → report → History；
- frontend type-check 和 production build 通过；
- health/readiness 可被部署系统检查；
- API 与 worker 由 supervisor 拉起，异常退出可观测。

任一 P0 Gate 未完成时，结论是 **No-Go**，不得通过改名为“完整产品”绕过。

## 6. 完整产品 v1 的排期规则

完整产品 v1 的发布日期在 Week 3 Runtime gate 后确定，原因是 Desktop sidecar、verified auth、安装分发和更新链当前尚不存在，无法基于文档研究给出可信日程。

可进入 v1 Release Candidate 排期的前提：

1. 内部技术预览证明诊断价值成立；
2. History 本地闭环完成；
3. Runtime contracts 经真实数据和 crash/retry 测试稳定；
4. Web 正式化或 Desktop 路线已做单一选择；
5. 对应分支的发布供应链和安全模型有可执行 spike 结果。

## 7. 开放决策

以下事项不阻塞本次文档落库，但会影响后续实施计划：

1. Desktop 首发是否 Windows-first；
2. 内部预览使用哪一种可信访问层；
3. Week 4 优先 Web 正式化还是 Desktop spike；
4. 完整产品是否要求无网络时支持 CV、规则诊断和 History（当前架构建议：要求）；
5. Coach 长上下文的摘要、换窗与长期表现档案策略；该决策不得阻塞已冻结的 thread/message/reference 数据归属迁移。

文件保留默认已经由 PRD 固定：原视频不按时间自动删除，用户删除 `done/failed` 分析时一并删除输入与产物。未来如需 TTL 或“分析后立即删除”，先更新 PRD，不在实施计划中临时决定。

## 8. 进度维护规则

- 本文只在优先级、里程碑、Gate 或日期发生变化时更新；
- 日常完成情况写入 `docs/PROGRESS.md`；
- 单个功能的实现步骤写入 `docs/superpowers/plans/`；
- 产品范围变化先改 PRD，再同步本文；
- 技术边界变化先改 Architecture，再同步实施计划。
