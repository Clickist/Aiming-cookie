# Versioned Coach Knowledge Registry Design

> **状态：active。** 点点于 2026-07-14 明确授权优先完成后端 Knowledge Registry，并要求 Flicking、Tracking、身体/张力和设置实验一起纳入。本文只冻结 Knowledge 的事实源、版本、检索、claim 与审计合同；不授权正式前端。

## 1. 目标与非目标

目标是把当前分散在 Python、TypeScript 和研究文档中的运行时知识，收敛为一份随产品版本发布、可审查、可复现的 canonical Registry：

```text
Analysis facts / deterministic diagnosis
              ↓ signal / metric / topic / use
Versioned Knowledge Registry
              ↓ bounded deterministic retrieval
get_coach_knowledge
              ↓ selected entry/version/source refs
Pi Coach explanation + training + verification
```

非目标：

- 不把知识正文放进 system prompt；system prompt 只保留行为与 claim discipline；
- 不让模型直接读取 Markdown、任意文件、数据库或目录；
- 不用知识层重新计算 metric、改写 Analysis 或产生确定性 diagnosis；
- 第一版不引入 embedding、vector database、在线搜索或 LLM 自动索引；
- 不实现正式前端、知识管理 UI 或 Coach sidebar；
- 不把身体、张力、握持、灵敏度或硬件候选解释写成已测事实。

## 2. 单一事实源与数据归属

Canonical asset 固定为仓库内、随 release 打包、受 Git review 的静态 Registry：

```text
knowledge/coach/
  schema.v1.json
  registry.v1.json
  migration-audit.v1.json
```

- `registry.v1.json` 是运行时知识正文与索引字段的唯一事实源；Python 与 TypeScript 只能读取它，不能各自维护正文副本。
- `schema.v1.json` 是完整的 Draft 2020-12 structural wire contract（entry properties、enum、长度、唯一性与 source/claim 条件）；Python/TypeScript loader 继续叠加 unsafe sentinel、alias chain、duplicate active version 等 fail-closed 规则，并由标准 schema validation 与 parity tests 保证一致。
- `migration-audit.v1.json` 记录每个旧 Python chunk、legacy signal 和 TS seed 的 `migrate | rewrite | merge | experimental_only | reject` 处置及目标 entry refs。
- Markdown 研究、理论、社区和处方文档继续是来源证据与审查材料，不是 runtime retrieval source。
- SQLite 只保存 Coach 实际使用的 registry/entry/version/source refs；不复制 Registry 正文，不与 Git asset 双写。
- Registry 是产品级只读资产，不按 owner 变化；用户数据、Analysis、Training Plan 和对话仍按本地 owner 隔离。

## 3. Registry 与 entry contract

顶层：

```text
coach_knowledge_registry.v1
  registry_version
  signal_aliases
  entries[]
```

每条 entry 至少包含：

```text
entry_id
entry_version
status = active | retired
category
  = metric_definition
  | kinematic_mechanism
  | diagnostic_scope
  | research
  | training_cue
  | prescription_verification
  | practice_structure
  | body_tension_hypothesis
  | settings_experiment
  | limitation_counterevidence
topics[]
signals[]
metric_refs[]
text
sources[]
  source_ref
  source_level
max_claim_level
limitations[]
counterevidence[]
supported_uses[]
```

规则：

- `entry_id` 稳定且有命名空间；正文或边界发生语义变化时递增 `entry_version`，不得覆盖历史含义。
- 同一 `entry_id` 最多一个 active version；retired version 继续可按 ref 解析，不能进入默认检索。
- `signal_aliases` 只做显式 canonical normalization；模型和相似度不得自行猜别名。
- `source_level` 允许 `product_contract | academic_peer_reviewed | community_consensus | personal_experience_unverified | experimental`。
- `max_claim_level` 允许 `deterministic_rule | research_supported | community_consensus | experimental`，不得为 `measured`。
- `product_contract` 只能解释已冻结的 metric/diagnosis contract，不能把阈值或外部因果升级成 product fact。
- community entry 的最高 claim 不得超过 `community_consensus`；personal/experimental、身体/张力与 settings entry 的最高 claim 必须为 `experimental`。
- limitations 必填；身体/张力 entry 必须明确“没有身体传感器/EMG/握力测量”；未校准指标必须明确不得使用绝对健康线。
- 单条正文、数组长度、嵌套深度和总 Registry 大小均有上限；绝对路径、secret、raw trace、URL payload 和任意原始数据不得进入 Registry。

稳定引用：

```text
knowledge:<entry_id>@<entry_version>
registry:<registry_version>
```

## 4. 内容范围与证据纪律

首版同时覆盖：

- Flicking：movement timing、peak/time-to-peak、decel fraction、linearity、SPARC、stopping/settle、reverse、corrective/submovement、path geometry、speed–precision、distribution/history comparability；
- Tracking：on-target、loss/off-target/reacquisition、average error、speed mismatch、acceleration mismatch、PTC scope、smooth/reactive/control tracking cue；
- 身体/张力：只作为 candidate hypothesis 与单变量 cue experiment，不能产生 measured/deterministic root cause；
- Settings/hardware：只作为可撤销单变量实验，不生成通用最佳 sensitivity/FOV/hardware 结论；
- Practice/prescription：external focus、contextual interference、feedback fading、剂量限制、training target、comparability、retest 与 insufficient-evidence behavior；
- limitations/counterevidence：研究任务和人群外推、短时鼠标 flick 的校准缺口、community 流派争议、指标时长/任务依赖。

知识层可以解释 Analysis 中已经存在的 deterministic rule，但不得凭知识 entry 自己触发 severity、issue 或正式处方。身体/张力内容可以给候选实验；实验结果仍由后续可比 Analysis 判断，不能自动确认为根因。

## 5. 确定性检索

`get_coach_knowledge` 查询允许：

```text
topic?
issue_signal?
metric_refs[]?
supported_use?
```

至少提供一个检索条件。第一版顺序与评分固定：

1. canonical signal exact match；
2. explicit signal alias normalization 后 exact match；
3. metric ref intersection；
4. topic exact match；
5. supported use exact match；
6. 过滤 retired/不安全/claim discipline 不合法 entry；
7. 按 score descending、`entry_id` ascending、`entry_version` descending 稳定排序；
8. 最多返回 3 条，不足则如实返回，零命中不回退整库。

不得让模型设置任意返回上限、source level 或 claim 上限来提升弱知识。所有 entry 都连同自身 `max_claim_level`、limitations 和 counterevidence 返回。

## 6. Pi、Python compatibility 与审计

- 所有 `coach_runtime_turn.v1` 都注册 `get_analysis_summary` 和 `get_coach_knowledge`；只有存在固定 loopback product bridge 时才额外注册 `run_product_command`。
- Knowledge tool 读取固定 Registry asset 不等于给模型 filesystem tool；模型不能提交路径或选择 Registry 文件。
- Tool result 返回完整的 bounded selected entries；tool event 只保存安全引用：registry version、entry refs、entry versions、source refs/levels、max claim levels、topic/signal。
- Python legacy Coach tools 改为 Registry compatibility adapter；`knowledge.py`、`agent_kb.py` 不再维护独立正文。
- 历史 Coach message trace 保留当时实际使用的 entry/version refs；Registry 更新不重写历史 trace。
- Analysis facts 与 Knowledge refs 分开：entry 不能冒充 metric/event/evidence ref。

## 7. Migration audit

必须逐项覆盖：

- `kovaak_tracker/coach/agent_kb.py` 的 37 个 topic chunk；
- `kovaak_tracker/coach/knowledge.py` 的 19 个 signal 条目；
- `webapp/coach-runtime/src/knowledge-tools.ts` 的 11 个 TS seed topic。

处置定义：

- `migrate`：语义和 claim 可原样结构化；
- `rewrite`：保留主题但修正过强、过时或混合 claim；
- `merge`：多个重复来源合并到同一 canonical entry；
- `experimental_only`：只保留为候选假设/实验；
- `reject`：失实、无法核实、危险或不属于产品知识边界。

所有 source asset 必须有 audit row；所有非 reject row 必须指向至少一个存在的 Registry entry。

## 8. Tests / Gate

- Draft 2020-12 schema 必须能接受 canonical Registry/合法 fixture，并拒绝 unknown fields、非法 enum、空 limitation 与 source/claim 冲突；大小、重复 ID/version、signal alias chain 与 unsafe sentinel 继续由 runtime validator fail closed；
- Python/TS 对同一 query 返回相同 entry refs、versions 和顺序；
- 37 + 19 + 11 migration audit coverage 完整；
- Flicking 19 个 canonical signals 均至少有一个 entry；Tracking、身体/张力、settings 与 verification 均有覆盖；
- body/tension/settings 只能 experimental；community 不得 deterministic；Knowledge 不得 measured；
- 所有 v1 turn 无 bridge 仍有 knowledge tool；v0 无 knowledge/product tools；
- 每次最多 3 条，未知条件不全库 fallback；
- trace 保存 registry/entry/version/source/claim refs，且不含正文、路径、secret、raw payload；
- 真实 Analysis signal/metric → retrieval → Pi tool event E2E；
- Python full suite、Pi runtime tests、strict TypeScript、diff/sentinel 检查通过。

## 9. Stop rules

- 需要模型直接读取/解析 Markdown 或任意文件；
- 需要把 Registry 正文写入 SQLite 并与 Git asset 双写；
- 需要用 LLM/embedding 结果触发确定性 diagnosis、severity 或自动确认身体根因；
- 迁移要求保留未经审查的绝对健康阈值、医疗建议或强制 sensitivity 数值；
- 无法让 Python 与 TypeScript 共享同一 Registry 或通过 parity test；
- 需要扩大到正式前端、在线知识服务或用户可编辑知识。
