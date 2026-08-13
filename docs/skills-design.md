# Coach Skills Architecture — Design Spec

> **状态：active。** 本文档冻结 Coach 把 coaching flow 封装为模块化 skills 的局部设计合同，定义三类 skill、状态管理决策（Route A）、系统提示词更新方向和 `teaching_session.update` 命令设计。
> **上游：** [`PRD.md`](PRD.md) §5.5（常驻 Coach 是 Agent 操作层）、[`ARCHITECTURE.md`](ARCHITECTURE.md) §4.3（Coach 数据归属）、§5.1（product command authority）。
> **相关合同：** [`archive/superpowers/specs/2026-07-27-coach-guided-teaching-loop-design.md`](archive/superpowers/specs/2026-07-27-coach-guided-teaching-loop-design.md)（teaching loop）、[`archive/superpowers/specs/2026-08-06-coach-problem-hypothesis-diagnosis-design.md`](archive/superpowers/specs/2026-08-06-coach-problem-hypothesis-diagnosis-design.md)（problem compilation）。

## 1. 目标

旧的硬编码状态机和 regex 规则已移除。新方向是把 coaching flow 封装为模块化 skills，由 Coach AI 根据自身理解激活。Skills 不是新的代码模块或注册表条目——它们是系统提示词中的软引导概念，帮助 Coach 在对话中判断"现在该做什么"。实际执行仍通过现有 product command bridge、TeachingSession 和 confirmation 流程。

### 成功语义

- Coach 不需要外部 dispatcher 或规则引擎来选择 skill；它从上下文（有/无 Analysis、用户意图、TeachingSession 状态）自行判断。
- 每个 skill 有明确的触发条件、执行步骤和所需工具。
- TeachingSession 状态机保留为轻量记录，不阻塞对话； staleness 可容忍。
- 新增 `teaching_session.update` 命令让 Coach 在对话转换时显式更新状态。

### 非目标

- 不建立 skill 注册表、skill dispatcher 或 runtime skill 选择引擎。
- 不把 skills 变成独立代码模块；它们是提示词层概念 + 现有工具。
- 不改变 product command 的授权、确认、owner scope 或审计边界。
- 不改变 Analysis、Training Plan 或 retest 的数据合同。

## 2. 三类 Skill

### 一、引导类 (Guidance)

| Skill | 触发 | Coach 做什么 |
|---|---|---|
| 新手引导 | 首次用户，无 Analysis | 说明 Coach 价值，引导连接 KovaaK 并跑分析 |
| 数据解读引导 | 用户有 Analysis 但问得模糊（"我怎么样""帮我看看"） | 帮用户找到最值得看的一个问题 |
| 能力介绍 | 用户问 Coach 能做什么 | 自然语言介绍当前可用能力 |

### 二、执行类 (Operations)

| Skill | 触发 | Coach 做什么 |
|---|---|---|
| 删除分析 | 用户要删除 | 调 `analysis.delete`，推确认卡 |
| 调整训练计划 | 用户接受调整 + 有可比复测 | 调 `training_plan.adjust`，推确认卡 |
| 查成绩 | 用户粘贴 `steam_profile:N` | 调 `kovaak_scores.lookup` |
| 刷新成绩 | 用户要求刷新 | 调 `kovaak_scores.refresh_connected` |

### 三、教学类 (Teaching/Review)

| Skill | 触发 | Coach 做什么 |
|---|---|---|
| 问题定位 | 用户有 Analysis，想知道练什么 | 编译一个主问题，用白话解释 |
| 带教练习 | 问题已定位，用户准备好 | 给一个 cue + 一个心智模型，只改变一个变量 |
| 复测验证 | 用户练过，想知道是否进步 | 安排同条件复测，比较，决定 retain/lower/reject |
| 进度回顾 | 用户有多条 Analysis / 复测 | 回顾进步，决定继续当前方向或换方向 |

## 3. 状态管理决策（Route A）

### 决策

TeachingSession 状态机保留为轻量记录，不做对话的硬性门控。

| 方面 | 决策 |
|---|---|
| 状态更新方式 | 只通过显式 Coach 工具调用（product command）更新 |
| 系统提示词角色 | 提供软引导：告诉 Coach "什么时候适合更新状态"，但不做语法强制 |
| State 的地位 | 补充上下文，不是对话的前置条件 |
| 过时容忍 | 可容忍。如果状态和对话不一致，以对话事实为准 |

### 理由

1. **避免回归硬编码**。旧状态机通过 regex 和 if-else 强制推进阶段，已证明僵化。Route A 让 Coach 自主判断阶段转换，状态只记录不门控。
2. **与现有 TeachingTurnContract 一致**。Contract 已经在每轮冻结一个不可变快照（phase、cue、question 等），这保证了单轮内的安全和一致性。Route A 只放松了跨轮的状态同步要求。
3. **降级安全**。状态过时不会阻塞对话；用户随时可以问新问题，Coach 不需要"先修好状态再回答"。

### 现有实现

`teaching_session_store.py` 已实现：
- `get_or_create_primary_session`：每个 owner/thread 一条 active session
- `validate_state`：bounded v1 state schema（phase、observation、primary_candidate、cue、retest 等）
- `claim_active_run` / `release_active_run`：乐观锁 + contract 不可变性
- `replace_state`：直接替换状态（由后端 planner 调用）

这些不变。新增的是 `teaching_session.update` 命令。

## 4. 各 Skill 详细设计

### 4.1 新手引导

| | |
|---|---|
| **触发** | `product.readiness.get` 返回无 Analysis、无 pending Run；或用户第一次进入 Coach |
| **Coach 做什么** | 说明 Coach 能做什么（一句话）；说明需要 KovaaK 数据；引导用户回到采集流程或 History |
| **工具/状态** | `product.readiness.get`、`navigation.open`（target=history） |
| **已有支持** | `product.readiness.get` 已在 Python `_QUERY_COMMANDS` 中实现 |
| **缺失** | `product.readiness.get` 不在 TS `PRODUCT_COMMAND_NAMES` 中，Coach 工具无法直接调用。提示词无新手引导段。 |

### 4.2 数据解读引导

| | |
|---|---|
| **触发** | 用户有已完成 Analysis，但消息模糊（"帮我看看""我怎么样""哪个问题最大"） |
| **Coach 做什么** | 用 `analysis.evidence.list` + `analysis.events.aggregate` 找到最值得先看的一个问题；用白话说明为什么先看这个 |
| **工具/状态** | Analysis evidence query tools；problem compiler contract（problem_id、evidence_strength） |
| **已有支持** | 所有 evidence query 工具已实现。Problem compilation contract 已在 `teaching_session_store.py` 的 `validate_contract` 中支持。提示词已有"检查整局聚合、支持事件和反例"规则。 |
| **缺失** | 提示词未把"模糊提问 → 先做一次 problem identification"明确为 skill 路径。 |

### 4.3 能力介绍

| | |
|---|---|
| **触发** | 用户问"你能做什么""你能帮我什么""你是什么" |
| **Coach 做什么** | 用自然语言介绍当前可用能力：分析诊断、教学带练、复测验证、进度回顾、删分析、查成绩 |
| **工具/状态** | 无工具调用；纯文本回复 |
| **已有支持** | 无。 |
| **缺失** | 提示词无能力介绍段。需要在系统提示词中加一小段，告诉 Coach 当用户问能力时如何回应。 |

### 4.4 删除分析

| | |
|---|---|
| **触发** | 用户明确要求删除某条 Analysis（"删除分析:3"） |
| **Coach 做什么** | 调 `run_product_command` 的 `analysis.delete`，参数为 `{"analysis_ref":"analysis:N"}` |
| **工具/状态** | `analysis.delete`（write command，走 confirmation 流程） |
| **已有支持** | **完整支持。** 提示词有明确规则（第 24 行）；`turn.ts` 有 `DELETE_REFERENCE_PATTERN` 检测；command handler 已实现 confirmation + tombstone。 |
| **缺失** | 无。 |

### 4.5 调整训练计划

| | |
|---|---|
| **触发** | 用户明确接受调整，且有具体反馈/可比复测、证据引用和下一次验证目标 |
| **Coach 做什么** | 调 `training_plan.adjust`（plan_ref + plan_payload + adjustment_reason + evidence_refs + verification_targets） |
| **工具/状态** | `training_plan.adjust`（write command，走 confirmation 流程） |
| **已有支持** | **完整支持。** 提示词第 47 行有规则；command handler 已实现。 |
| **缺失** | 无。 |

### 4.6 查成绩

| | |
|---|---|
| **触发** | 用户在本轮消息中提供 `steam_profile:N` |
| **Coach 做什么** | 调 `kovaak_scores.lookup`，传入 `{"profile_ref":"steam_profile:N"}` |
| **工具/状态** | `kovaak_scores.lookup`（bridge-only，不进 audit/ui_event） |
| **已有支持** | **完整支持。** 提示词第 21 行；TS + Python 参数校验已实现。 |
| **缺失** | 无。 |

### 4.7 刷新成绩

| | |
|---|---|
| **触发** | 用户要求刷新自己已连接的 KovaaK 成绩 |
| **Coach 做什么** | 调无参 `kovaak_scores.refresh_connected`；无已连接账号时直接说明 |
| **工具/状态** | `kovaak_scores.refresh_connected`（bridge-only） |
| **已有支持** | **完整支持。** 提示词第 22 行；command 已实现。 |
| **缺失** | 无。 |

### 4.8 问题定位

| | |
|---|---|
| **触发** | 用户有已完成 Analysis，问"该练什么""最大的问题是什么" |
| **Coach 做什么** | 1. 用 evidence query 工具检查整局聚合和反例；2. 编译一个主问题（problem_id、evidence_strength、supporting_evidence、counterevidence）；3. 用白话解释问题，说明不确定性；4. 提一个区分问题 |
| **工具/状态** | `analysis.evidence.list`、`analysis.events.aggregate`、`analysis.events.rank` 等；TeachingSession phase 推进到 `hypothesize`；TeachingTurnContract 带 diagnostic fields |
| **已有支持** | **大部分支持。** Evidence query 工具完整。Problem compilation contract 已在 `validate_contract` 中实现（problem_id、problem_label、evidence_strength、supporting_evidence、counterevidence、discriminator）。提示词有"问题定位"相关规则。 |
| **缺失** | 提示词未把"用户问练什么 → 进入 problem identification skill"明确为路径。Coach 需要知道编译问题后应推进 TeachingSession。 |

### 4.9 带教练习

| | |
|---|---|
| **触发** | 问题已定位，用户说"开始吧""好""明白了"或主动要求练习 |
| **Coach 做什么** | 给一个 cue + 一个心智模型；说明这组只改变一个变量；推进到 `practice_ready` 或直接进入练习 |
| **工具/状态** | TeachingSession phase: `teach` → `practice_ready`；`training_plan.item.add`（如有 prepared item）；`training_plan.execution.record` |
| **已有支持** | **完整支持。** Teaching loop 设计已冻结并实现。提示词有详细的教学带练规则（第 27-49 行）。TeachingTurnContract 约束每轮唯一动作。 |
| **缺失** | 无核心缺失。`teaching_session.update` 命令会让 phase 推进更显式。 |

### 4.10 复测验证

| | |
|---|---|
| **触发** | 用户说练过了，想知道有没有进步 |
| **Coach 做什么** | 安排同条件复测；用 `analysis.compare` 比较前后；决定 retain/lower/reject |
| **工具/状态** | `analysis.compare`、`training_plan.retest.record`；TeachingSession phase: `retest_ready` → `await_retest_confirmation` → `revise` |
| **已有支持** | **完整支持。** Retest recording 带自动 comparability 判定（`coach_retest_decision.decide_two_analysis_retest`）。提示词有复测验证规则。 |
| **缺失** | 无。 |

### 4.11 进度回顾

| | |
|---|---|
| **触发** | 用户有多条 Analysis 或多次复测，问"我的进步""这段时间效果怎么样" |
| **Coach 做什么** | 用 `history.list` + `history.trend` 回顾；用 `profile.aiming.snapshot` 综合画像；决定继续当前方向或换方向 |
| **工具/状态** | `history.list`、`history.trend`、`profile.aiming.snapshot`、`training_plan.review` |
| **已有支持** | **工具完整。** History trend、profile snapshot 和 plan review 命令均已实现。 |
| **缺失** | 提示词无进度回顾的明确引导。Coach 需要知道该综合多来源，而不是只复述趋势数字。 |

## 5. 系统提示词更新方向

### 现状

`coach-system.md` 是纯规则列表，没有 skill 概念。Coach 靠规则之间的隐含关系推断"现在该做什么"。这可以工作，但增加新 Coach 能力时容易遗漏。

### 更新原则

- Skills 是提示词层的**软引导概念**，不是硬编码分支。提示词用自然语言描述"当你观察到 X 时，通常应做 Y"。
- 不新增 skill 注册表、skill 参数或 skill schema。Skills 只存在于提示词文本。
- 现有规则（measured facts、confirmation、teaching loop 等）保留不动。
- 提示词更新应增加一小段 skill 概述（不超过 15 行），告诉 Coach 三类 skill 的大致分工和触发直觉，然后用现有的逐条规则提供细节。

### 拟增内容（概念草稿，最终文本由 prompt 维护者定）

提示词开头或"教学带练"段之前，增加类似以下内容：

```text
Skill 直觉（软引导，不是硬性分支）：
- 引导类：当用户没有 Analysis 或不知道从哪开始时，先帮用户理清下一步。
- 执行类：当用户明确要求删除、查成绩、刷新或调整计划时，直接用对应产品命令。
- 教学类：当用户有 Analysis 且想改善时，按问题定位 → 带教练习 → 复测验证 → 进度回顾的顺序推进。
这些分类帮助你判断当前对话重心，不限制你响应用户的任何具体问题。
```

这段不改任何行为约束，只给 Coach 一个组织思路的框架。

## 6. `teaching_session.update` 命令设计

### 目的

让 Coach 在对话转换时显式更新 TeachingSession 状态，而不依赖后端 planner 的隐式推进。

### 参数

```json
{
  "command_name": "teaching_session.update",
  "parameters": {
    "session_ref": "teaching_session:<hex>",
    "expected_version": 0,
    "next_phase": "hypothesize",
    "updates": {
      "observation": { "summary": "...", "source_refs": ["analysis:3"] },
      "primary_candidate": { "label": "...", "source_refs": [...] },
      "cue": "...",
      "changed_variable": "...",
      "retest_intent": "none",
      "pause_reason": null
    }
  }
}
```

### 约束

| 方面 | 设计 |
|---|---|
| 分类 | Write command（需要 idempotency_key） |
| 授权 | `coach_inferred`（Coach 主动推进），走 `needs_confirmation` 流程 |
| 验证 | 复用 `validate_state`；参数只允许更新 state 子集，不允许设置 `schema_version` 或伪造 `pending_confirmation_ref` |
| 版本检查 | `expected_version` 乐观锁；不匹配时返回 conflict |
| 副作用 | 更新 `teaching_sessions.state_json`，`version += 1`，清除 `active_run_ref`（如果无活跃 run） |
| 原子性 | 在 `BEGIN IMMEDIATE` 事务中执行 |

### 安全边界

- 不允许通过此命令设置 `active_run_ref`（仍由 `claim_active_run` 管理）。
- 不允许设置 `pending_confirmation_ref`（仍由 write command confirmation 流程管理）。
- 不允许跳过 `validate_state` 的所有现有约束（phase 白名单、文本长度、forbidden text、source_refs 格式）。
- 参数中的 `updates` 是对当前 state 的 partial merge；未提供的字段保留当前值。

### 与 Route A 的一致性

这个命令是 Route A 中"状态只通过显式 Coach 工具调用更新"的实现。它不门控对话——Coach 可以选择不调用它，状态保持过时但对话继续。

## 7. 实施优先级

| 优先级 | 工作 | 理由 |
|---|---|---|
| P0 | 把 `product.readiness.get` 加入 TS `PRODUCT_COMMAND_NAMES` | 新手引导和所有 readiness 判断的前置条件 |
| P0 | 系统提示词增加 skill 概述段 | 让 Coach 有 skill 直觉，所有后续 skill 依赖这一认知框架 |
| P0 | 系统提示词增加能力介绍引导 | 纯文本变更，无代码改动 |
| P1 | 实现 `teaching_session.update` 命令 | 让状态推进显式化；需先写测试验证 partial merge、版本冲突和边界 |
| P1 | 系统提示词增加数据解读引导和进度回顾引导 | 这两个 skill 的工具已完整，只缺提示词引导 |
| P2 | 系统提示词细化问题定位 skill 路径 | 工具和 contract 已有，需要更明确的"用户问练什么 → 编译问题 → 推进 session"路径描述 |

### 不在优先级列表中的

- 删除分析、查成绩、刷新成绩、调整训练计划、带教练习、复测验证：**已完整支持**，不需要改动。
- Skill 注册表/dispatcher：**明确不做**。Skills 是提示词概念。

## 8. 与现有合同的关系

| 现有合同 | 本文档的关系 |
|---|---|
| [`PRD.md`](PRD.md) §5.5 常驻 Coach 是 Agent 操作层 | Skills 是 Coach 自主判断的操作封装，不改变 Agent 操作层定位 |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) §4.3 Coach 数据归属 | TeachingSession 仍属于 Coach 层，不替代 Training Plan |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) §5.1 product command authority | `teaching_session.update` 遵循现有授权、确认和审计边界 |
| [`2026-07-27-coach-guided-teaching-loop-design.md`](archive/superpowers/specs/2026-07-27-coach-guided-teaching-loop-design.md) | 带教练习 skill 是该合同的 skill 化封装，不改变 teaching loop 合同 |
| [`2026-08-06-coach-problem-hypothesis-diagnosis-design.md`](archive/superpowers/specs/2026-08-06-coach-problem-hypothesis-diagnosis-design.md) | 问题定位 skill 是该合同的 skill 化封装，不改变 problem compilation 合同 |
