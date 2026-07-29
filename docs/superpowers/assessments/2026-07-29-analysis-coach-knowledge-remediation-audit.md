# Analysis / Coach 知识语义修复审计台账

> 状态：只读审计完成，实施入口已建立
> 目标：在修改实现前，确认旧 Analysis narration、Analysis 语义、稳定 observation 引用、Knowledge Registry、Provider Coach 表达、本地化与复测决策的完整影响面。
> 边界：本文只保存审计证据和恢复状态，不是产品合同或实施授权。

## 用户目标

- 修复当前知识体系的结构问题，不把新文章默认升级为诊断规则。
- 无 Provider 时只有本地确定性 Analysis，不伪造“无 Provider Coach”。
- Provider Coach 共享已审核事实和知识边界，但保留自然表达、暂定综合判断与教学组织自由。
- 保留旧 Analysis / History 读取兼容，不破坏 TeachingSession、Training Plan、confirmation、owner/privacy 和 exact-scenario 边界。
- 复用现有 Registry、resolver、contract、store 和 validator，不建第二套知识或状态系统。

## 审计分工

| 路线 | 范围 | 状态 |
|---|---|---|
| A | 旧 Analysis narration 生产调用、Provider 副作用、wire/history 兼容、文档和测试 | 完成 |
| B | advice/analyzer -> Diagnosis -> frontend 语义、稳定 observation ref、Registry 双正文源 | 完成 |
| C | Registry supported uses、Provider 自然表达、本地化、TeachingSession/plan/retest 边界 | 完成 |
| Root | PRD / Architecture / active plan 冲突、方案选择、去重、实施分批与最终验证 | 完成 |

## 不可破坏条件

1. Analysis 完成不得依赖 Provider，Provider 不得改写测量事实。
2. 新结果停止产生已废弃 narration，但旧持久化结果必须继续可读。
3. Provider 的自然改写不能回退为逐字脚本；数值、单位、claim ceiling、cue/dose/retest 和写入仍受确定性边界限制。
4. Analysis observation 是候选观察，不将未测的 physical mechanism 宣称为已知根因。
5. 新知识先按现有 `supported_uses` 约束消费者；不为九篇文章新建平行 Registry。
6. 不覆盖当前 dirty worktree 中的用户改动，不提交、不推送、不清理无关文件。

## 已确认的生产者 / 消费者矩阵

| 关注点 | 当前生产者 | 当前消费者 | 修复边界 |
|---|---|---|---|
| Analysis `narration` | `webapp/backend/worker.py` 的 video fallback 在本地 report 完成后额外加载 owner selected Provider 并调用 `run_report()` | 正式前端不展示；旧 adapter、wire/history fixture 仍允许读取 | 只停止新 v2 结果生成和 Provider 请求；保留 v1/unversioned/history 读取兼容，不删除 `report.py` 或旧 Python Coach runtime |
| Static/flicking issue 正文 | `kovaak_tracker/advice.py` 与 `kovaak_tracker/coach/diagnosis.py` 从 `profiles.ROOT_CAUSES` 生成 symptom/physical/training 与 prescription | Analysis UI、Coach context | Analysis 只保留确定性 observation/candidate projection；完整教学正文归 Registry；旧字段继续可读 |
| 新 family issue refs | dynamic/tracking/switching advice 已发 `knowledge_registry_version` 与 `knowledge_entry_refs` | `webapp/backend/coach_context.py` 当前忽略稳定 refs，按 `signal + metric_refs` 重新查找 | producer 增加稳定 `observation_ref`；resolver exact-ref first；文本 fallback 只用于旧历史展示 |
| Registry v3 | `knowledge/coach/registry.v3.json`；Python / TypeScript validator 与 JSON Schema | Coach context、TeachingTurn、prepared Training Plan item、registry events | v3 已有真实 trace，必须精确保留；新 capability 进入 versioned v4，不原地改 v3 语义 |
| Provider Coach 表达 | 已完成的 `2026-07-29-real-coach-semantic-remediation-v1.md` | 普通 Coach、TeachingSession、计划确认 | 不重做；Provider 可以自然改写，确定性 validator 只锁事实、claim ceiling、剂量、问题数、状态和写入边界 |
| Analysis UI 根因/处方标签 | `frontend/lib/contracts.ts` 与 Analysis 展示组件，设计文档也使用“三层根因”“处方” | Analysis Data 页面 | 新结果显示“重点观察 / 候选解释 / 规则化练习建议”；legacy adapter 保留旧数据展示，不改持久化字段 |
| Retest decision | `webapp/backend/coach_retest_decision.py` 复用 `history_trends.compare_analysis_results()`，但非零 delta 均 fail closed | TeachingSession 后续动作与计划调整 | meaningful-change policy 必须来自 Analysis/history metric 层；不得在 Coach 层发明通用百分比阈值 |

## 已有能力与本次去重

- `TeachingSession`、Training Plan、confirmation、idempotency、owner/privacy、prepared-item exact equality 和 retain/reject 状态机已经存在，本次不新建 store、route 或平行状态机。
- Provider 自然表达与普通问答/带练分流已经由 `2026-07-29-real-coach-semantic-remediation-v1.md` 完成并 field verified，本次不把 Coach 改成逐字朗读器。
- Registry v3 已有 exact scenario、candidate hypothesis、cue/dose/retest 和单变量实验；本次只补 capability 表达、稳定引用与社区知识，不复制 Registry。
- `history_trends` 和 `aiming_profile_store` 已有可比性、metric direction 与 profile trend；复测只在这些事实源中补有证据的 policy，不在 Coach 层复制算法。

## 九篇社区文章的准入结论

| 主题 / 作者 | v4 允许用途 | 明确禁止 |
|---|---|---|
| Friction / immie | explanation、可逆单变量实验 | 自动身体归因、exact scenario 处方 |
| Perfect Sensitivity / Keeah | explanation、灵敏度实验 | 宣称唯一完美灵敏度 |
| TacFPS / linear clicking / MattyOW | explanation | 自动 diagnosis rule |
| Flick strategy / pinguefy、Viscose | explanation、用户反馈实验 | 未测因果、通用确定性 cue |
| Mouse grip / Viscose | explanation、安全澄清问题 | 从瞄准数据推断握法或伤病 |
| Score Farming / MattyOW | goal/context explanation | 将刷分策略当作技能机制 |
| Top 10 transfer / Viscose | goal/context explanation | 把社区列表当作本地 exact scenario |
| Muscle memory / Viscose | explanation、变化实验 | 把“肌肉记忆”当作单一已证机制 |
| Mouse acceleration / Viscose | explanation、可逆设置实验 | 自动修改设置或宣称普遍优劣 |

## 方案比较

1. **硬切 schema 并删除旧字段**：实现表面最直接，但会破坏 v3 field trace、旧 Analysis/History 和未提交工作，不采用。
2. **versioned Registry v4 + staged consumers**：保留 v1/v2/v3 精确解析，新结果使用稳定 refs 和 capability，消费者逐步切换；历史兼容和 Provider 表达边界最清晰，采用。
3. **只改 UI 文案**：能降低表面归因，却保留双正文源、fragile signal lookup 和不真实的 supported use，不采用。

## 实施与验证矩阵

| Task | Tests first | 核心验证 |
|---|---|---|
| 1. 退役活跃 narration | worker provider-call negative test、v1/v2 history compatibility | focused pytest；确认新 v2 无 `narration` 且不加载 Provider |
| 2. Registry v4 | Python / TypeScript / JSON Schema parity、v3 exact compatibility、九篇准入负例 | migration audit；完整 Registry suites；registry event version |
| 3. 稳定 refs 与单一 projection | 各 family producer refs、exact resolver、legacy fallback/write denial | Python Coach/context、Node summary tool、frontend contract tests |
| 4. Analysis UI 语义 | contract/source/component/E2E tests | Browser E2E、accessibility、`1280x820` 与 `960x640` 截图 |
| 5. metric change policy | version/direction/comparability/no-policy fail-closed tests | history/profile/retest suites；无依据阈值则按 Stop rule 保留 blocker |

正式 Task 合同见 [`2026-07-29-analysis-coach-knowledge-boundary-remediation-v1.md`](../../archive/completed/plans/2026-07-29-analysis-coach-knowledge-boundary-remediation-v1.md)。

## 审计结论

- [x] 生产者 / 消费者 / 兼容路径文件矩阵。
- [x] 现有 active plan 已完成内容与本次未完成内容的去重。
- [x] 三种修复方案与推荐理由。
- [x] 分 Task 的 Allowed files、Tests first、冻结决策和 Stop rule。
- [x] 自动化、历史 fixture、前端语义与真实 Provider 需要的验证矩阵。
