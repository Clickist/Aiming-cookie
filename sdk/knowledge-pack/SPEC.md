# Aiming Cookie 知识库包规范（SPEC）

> English version: [SPEC.en.md](SPEC.en.md)
> 版本：2026-09-20（随产品 v1 合同冻结） · 适用：`coach_knowledge_pack.v1` / `coach_knowledge_registry.v3` / `coach_mapping.v1`
> 读者：想给 Aiming Cookie 做知识库的第三方作者（教练 / UP 主 / 高手）。**不需要会编程**——你只写 JSON 文本文件，产品负责执行。
> 本文引用的代码路径均为 Aiming Cookie 仓库相对路径；校验器代码在 `kovaak_tracker/coach/knowledge_pack.py`（包级）、`kovaak_tracker/coach/knowledge_registry.py`（registry 层）、`kovaak_tracker/coach/mapping_rules.py`（mapping 层）。

---

## 目录

1. [快速上手：从零到导入](#1-快速上手从零到导入)
2. [包格式与目录布局](#2-包格式与目录布局)
3. [manifest.json 逐字段说明](#3-manifestjson-逐字段说明)
4. [knowledge/registry.json：知识层条目速查](#4-knowledgeregistryjson知识层条目速查)
5. [六条包级收窄规则（违规码与修法）](#5-六条包级收窄规则违规码与修法)
6. [mapping.json：映射层规则参考（coach_mapping.v1）](#6-mappingjson映射层规则参考coach_mappingv1)
7. [安装、激活、切换与回退：装了会怎样](#7-安装激活切换与回退装了会怎样)
8. [导入校验流程](#8-导入校验流程)
9. [证据档位：claim_level 与 source_level](#9-证据档位claim_level-与-source_level)
10. [scenario_prescription：官方场景引用约束](#10-scenario_prescription官方场景引用约束)
11. [词汇表冻结承诺](#11-词汇表冻结承诺)
12. [校验器 CLI 用法](#12-校验器-cli-用法)
13. [常见错误速查表](#13-常见错误速查表)

---

## 1. 快速上手：从零到导入

一条知识在 Aiming Cookie 里分两层：

- **知识层（registry）**：每个现象的"是什么、为什么、怎么练、练多少、怎么复测、何时停"——长正文。
- **映射层（mapping，可选）**："数值到现象"的规则，比如"SPARC 低于 -5 算 sparc low"——只有键、数值和一句短文案。

完整路径：

```text
第 1 步  复制 sdk/knowledge-pack/template/ 目录，改名 my-kb/
第 2 步  改 manifest.json：换成你的 pack_id / 作者名 / 版本号
第 3 步  改 knowledge/registry.json：registry_version 改成 "<pack_id>@<pack_version>"，
         把 3 条示例条目替换成你的条目（照字段结构写）
第 4 步  （可选）改 mapping.json：调整触发规则；不想动规则就删掉此文件
第 5 步  本地校验：
         python -m kovaak_tracker.coach.knowledge_pack validate my-kb
         看到 "OK <pack_id>@<版本>" 即合法
第 6 步  分发：把目录压成 zip 发给你的读者（zip 内允许有一层文件夹包裹）
第 7 步  用户在 Aiming Cookie 设置页「知识库」导入 zip，校验通过后激活
```

最佳参照物是仓库里的官方包：`knowledge/mapping/official.v1.json`（23 条映射规则）与官方知识 registry（当前 active 版本 `knowledge/coach/registry.v13.json`，118 条知识条目）。本模板的 3 条示例条目覆盖三种最常用的能力档位。

---

## 2. 包格式与目录布局

一个包是**一个目录或一个 zip**，最多包含四个文件，多一个都会被拒（错误码 `pack_unknown_file`）：

```text
my-kb/
  manifest.json            # 必需。包身份与兼容性声明
  knowledge/
    registry.json          # 必需。知识层（schema v3 registry）
  mapping.json             # 可选。映射层；缺失 = 纯知识替换包
  README.md                # 可选。给用户看的说明，不进产品上下文
```

**zip 打包规则**：入口路径必须是相对路径（不允许绝对路径、`..`、反斜杠——错误码 `zip_unsafe_entry`）；允许一层同名文件夹包裹（比如右键压缩整个 `my-kb/` 目录）；不允许空压缩包（`pack_empty_archive`）。

**大小上限**（超限错误码 `pack_file_too_large` / `pack_too_large`）：

| 文件 | 上限 |
|---|---|
| `manifest.json` | 64 KB |
| `knowledge/registry.json` | 1 MiB（且条目数 ≤ 512） |
| `mapping.json` | 256 KB |
| `README.md` | 256 KB |
| 包总体（zip） | 8 MiB |

registry 单个正文段落（section 的 `text`）另有 1200 字符上限，见第 4 节。

---

## 3. manifest.json 逐字段说明

完整示例见 `template/manifest.json`。

| 字段 | 必填 | 约束 | 说明 |
|---|---|---|---|
| `schema_version` | 是 | 固定 `"coach_knowledge_pack.v1"` | 包格式的版本。写错报 `manifest_schema_version_invalid` |
| `pack_id` | 是 | 正则 `^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$`（小写字母/数字/点/连字符，首尾不能是点或连字符） | **安装键，发布后不可改**。它同时是用户数据目录 `DATA_ROOT/knowledge-packs/<pack_id>` 的目录名，因此不允许 `.` / `..` 这类危险值。错误码 `manifest_pack_id_invalid` |
| `display_name` | 是 | 非空文本，≤120 字符 | 设置页里显示的名字 |
| `author` | 是 | 非空文本，≤120 字符 | 作者/组织名 |
| `homepage` | 否 | `http(s)://` 开头，≤300 字符 | 主页链接，仅展示用 |
| `pack_version` | 是 | semver 字符串，如 `1.2.0`（可带 `-beta` / `+build` 后缀） | 你的包版本。**每次内容有实质变化必须升版本**（见下文版本联动）。错误码 `manifest_pack_version_invalid` |
| `license` | 是 | 非空文本，≤120 字符 | 包内容的许可证（如 `CC-BY-4.0`、`MIT`） |
| `ac_compat` | 是 | 对象，见下 | 兼容性声明 |

`ac_compat` 字段：

| 字段 | 必填 | 约束 |
|---|---|---|
| `knowledge_schema` | 是 | 非空列表，当前只接受 `["coach_knowledge_registry.v3"]` |
| `mapping_schema` | 仅当包内含 `mapping.json` 时必填 | 非空列表，当前只接受 `["coach_mapping.v1"]` |

**声明与文件必须一致**：包里有 `mapping.json` 但没声明 `mapping_schema` → `ac_compat_mapping_mismatch`；声明了 `mapping_schema` 但没有 `mapping.json` → 同样报 `ac_compat_mapping_mismatch`。不想带映射层就把两边一起删掉。

**版本联动（强约束）**：`knowledge/registry.json` 顶层的 `registry_version` 必须严格等于字符串 `"<pack_id>@<pack_version>"`，例如 `com.example.my-kb@1.0.0`。`@` 保证第三方包的版本空间与官方（`2026-09-12.v12` 这类日期版本）永不混淆——用户的每条历史分析都记着当时用的知识版本号，靠这个精确回溯。不匹配报 `registry_version_mismatch`。

**manifest 内容安全**（错误码 `manifest_unsafe_content`）：manifest 里不允许出现疑似路径的文本（以 `/`、`\`、`~/`、`盘符:`、`file://` 开头）、疑似密钥的文本（api key / password / bearer token 等），也不允许指令类字段名（`command`、`exec`、`shell`、`prompt`、`instruction` 等）或敏感字段名（`apikey`、`password`、`secret`、`payload`、`rawtrace` 等）。包内容只能是知识数据，不能携带任何行为指令。

---

## 4. knowledge/registry.json：知识层条目速查

权威 schema 是仓库内 `knowledge/coach/schema.v3.json`（JSON Schema，机器可读）；校验实现在 `kovaak_tracker/coach/knowledge_registry.py`。本节是作者视角的常用字段表。

### 4.1 顶层结构

```jsonc
{
  "schema_version": "coach_knowledge_registry.v3",   // 固定
  "registry_version": "com.example.my-kb@1.0.0",     // "<pack_id>@<pack_version>"，见第 3 节
  "signal_aliases": { },                             // 可选：自创同义词 → 官方信号，如 {"sparc smoothness": "sparc low"}
  "sources":  [ ... ],                               // 来源声明，1-512 条，不允许重复
  "entries":  [ ... ]                                // 知识条目，1-512 条
}
```

### 4.2 sources[]：来源声明

每条条目的每个段落都必须挂来源，这是"教练可以讲什么、以什么资格讲"的机制。

| 字段 | 约束 |
|---|---|
| `source_ref` | token 格式（≤160 字符，字母/数字/`.` `_` `:` `/` 空格 `-`），如 `src.my-kb.author` |
| `source_level` | 见第 9 节；**第三方包禁用 `product_contract` 与 `coach_first_party`** |
| `title` / `author_or_org` / `locator` | 文本 ≤1200 字符；`locator` 写"这个来源在哪里能查到" |
| `retrieved_at` | `YYYY-MM-DD` |
| `published_at` | 可选，≤32 字符或 null |
| `applicability` | 适用的家族范围；写 `["all_families"]` 最简单 |
| `supports_sections` | 该来源支持为哪些段落作证（`definition`、`cue`、`scenario_prescription` 等名字的列表） |

### 4.3 entries[]：知识条目常用字段

| 字段 | 约束 | 说明 |
|---|---|---|
| `entry_id` | 正则 `^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$`（小写，至少两段），如 `kb.my-kb.explanation.sparc` | 条目名。约定 `prescription.*` 前缀的条目会被当作"训练推荐池"优先给到 Coach，处方类条目请用这个前缀 |
| `entry_version` | 整数 ≥1 | **内容一变就升版本**；一个条目同一时刻只有一个生效版本 |
| `status` | `"active"` 或 `"retired"` | retired 条目保留作历史追溯，不参与匹配 |
| `category` | 枚举：`observation_definition` / `mechanism` / `training_cue` / `prescription_verification` / `limitation` / `outcome_only` | 条目性质 |
| `topics` | 1-64 个 token | 主题标签，供检索 |
| `signals` | 0-64 个，**必须在官方词汇表 `signals` 内**（收窄规则 4） | 这条知识解释哪些信号，如 `["sparc low"]` |
| `metric_refs` | 0-64 个，**必须在官方词汇表 `metric_keys` 内** | 关联的指标，如 `["sparc"]` |
| `family_scope` | 1-8 个，枚举：`static_clicking` / `dynamic_clicking` / `predictable_tracking` / `reactive_tracking` / `control_tracking` / `target_switching` / `movement_aiming` | 注意：registry 里追踪分为三支，没有 `continuous_tracking` 这个值（那是 mapping 侧的家族键） |
| `observation_refs` | 0-64 个 | 关联的观察对象（如 `metric.terminal_control`）。**只要条目支持 `diagnosis_support`，就至少要 1 个** |
| `quality_prerequisites` | 0-64 个 | 引用本条结论前要满足的数据质量前提。**支持 `diagnosis_support` 时至少 1 个** |
| `sources` | 1-64 个，必须指向顶层 `sources` 里存在的 `source_ref` | 条目挂的来源 |
| `supported_uses` | 严格前缀阶梯，见下 | 这条知识允许被用到什么深度 |

**`supported_uses` 能力阶梯**（只能四选一，逐级加码，不能跳级）：

1. `["explanation_only"]` —— 纯解释。**禁止**携带 `cue` / `dose_guardrail` / `matched_retest` / `near_transfer_retest` / `stop_adjust_rule` / `scenario_prescription` 任何训练字段。
2. `["explanation_only", "diagnosis_support"]` —— 可参与诊断。仍禁训练字段；要求 `observation_refs` 与 `quality_prerequisites` 至少各 1 个。
3. `["explanation_only", "diagnosis_support", "candidate_experiment"]` —— 可给实验建议。**必须**带 `cue` + `dose_guardrail` + `matched_retest` + `stop_adjust_rule`；禁 `near_transfer_retest` 与 `scenario_prescription`。
4. `["explanation_only", "diagnosis_support", "candidate_experiment", "scenario_prescription"]` —— 全量处方。在第 3 档基础上**必须**再加 `near_transfer_retest` + `scenario_prescription`。

### 4.4 section：正文字段

`definition`、`scope`、`expected_direction`、`cue`、`matched_retest`、`near_transfer_retest` 是单个 section 对象；`mechanisms`、`dose_guardrail`、`stop_adjust_rule` 是 section 数组（至少 1 条）。`alternative_explanations` / `forbidden_inferences` / `limitations` / `counterevidence` 是纯字符串数组（各至少 1 条，每条 ≤500 字符）。

section 结构：

```jsonc
{
  "section_ref": "kb.my-kb.explanation.sparc.definition",  // 必须以 "<entry_id>." 开头
  "claim_level": "community_practice",                      // 证据档位，见第 9 节
  "source_refs": ["src.my-kb.author"],                      // 必须 ⊆ 条目的 sources，
                                                            // 且来源的 supports_sections 要包含本段落名
  "text": "……"                                              // ≤1200 字符
}
```

`expected_direction.text` 是枚举：`lower_better` / `higher_better` / `target_band` / `descriptive_only` / `comparison_only`。

**claim 天花板**：section 的 `claim_level` 不能超过其来源 `source_level` 允许的上限，否则报 `claim_level exceeds its source ceiling`。对照表：

| source_level | 允许的最高 claim_level |
|---|---|
| `experimental` / `personal_experience_unverified` | `experimental` |
| `community_organization` / `coach_first_party` | `community_practice` |
| `community_consensus` | `community_consensus` |
| `academic_peer_reviewed` | `research_supported` |
| `product_contract`（第三方禁用） | `deterministic_rule` |

---

## 5. 六条包级收窄规则（违规码与修法）

包校验器在 schema v3 之上加六条包级规则（实现：`kovaak_tracker/coach/knowledge_pack.py`）。任何一条不过，整个包被拒。

| # | 规则 | 违规错误码 | 常见原因与修法 |
|---|---|---|---|
| 1 | **规模沿用 v3**：≤512 条目 / registry ≤1 MiB / 单段正文 ≤1200 字符 | `registry_invalid`、`pack_file_too_large` | 条目太多就拆包；正文太长就拆成多个 mechanisms 段落 |
| 2 | **来源天花板**：`sources[].source_level` 禁 `product_contract`、`coach_first_party`。第三方最高 `community_organization` / `community_consensus` | `source_level_forbidden` | 这两个等级是产品/官方专属；把你的来源改成 `community_consensus` 等如实等级 |
| 3 | **处方开放但场景引用官方化**：`prescription.*` 条目与 `scenario_prescription` 能力均允许，但 `scenario_prescription.scenario_profile_ref` 必须命中**官方** `knowledge/scenarios/registry.v1.json` 已审核场景。不能自造场景档案 | `scenario_ref_not_official`、`scenario_registry_unavailable` | 从官方场景 registry 里挑已审核条目；ref 形如 `scenario:static.1wall_6targets_small@1`。场景 registry 本身永不随包替换 |
| 4 | **词汇表边界**：条目 `signals` / `metric_refs` 必须落在官方词汇表（`knowledge/mapping/vocabulary.v1.json`）内；`signal_aliases` 可自由扩展同义词 | `signal_out_of_vocabulary`、`metric_out_of_vocabulary` | 自创说法放进 `signal_aliases` 指向官方信号；指标名只能在官方 `metric_keys` 里挑 |
| 5 | **mapping 交叉校验**：`mapping.json` 存在时过完整 `coach_mapping.v1` 校验，且每条 `expected_entry_ref` 必须能被**本包** registry 解析为 **active** 条目 | `mapping_invalid`、`ac_compat_mapping_mismatch` | 把运行时的"引用落空即整条丢弃"提前到导入时报错；ref 写法是 `knowledge:<entry_id>@<entry_version>`，指向本包条目 |
| 6 | **unsafe-shape 全跑**：路径/密钥正则、字段名黑名单、深度/大小上限，覆盖 manifest、registry、mapping、zip 布局 | `manifest_unsafe_content`、`zip_unsafe_entry`、`pack_unknown_file` 等 | 包内容只能是知识数据，不得含行为指令字段；不要在包里夹带其他文件 |

---

## 6. mapping.json：映射层规则参考（coach_mapping.v1）

校验与求值实现在 `kovaak_tracker/coach/mapping_rules.py`。mapping 只携带**键、数值和短文案**（诊断句 ≤600 字符）；长讲解正文必须放 registry 条目。

### 6.1 顶层结构（五个键全部必填）

```jsonc
{
  "schema_version": "coach_mapping.v1",
  "static_clicking": [ /* StaticRule，≤32 条 */ ],
  "families": {
    "continuous_tracking": [ /* FamilyRule，≤32 条 */ ],
    "dynamic_clicking":    [ /* FamilyRule，≤32 条 */ ],
    "target_switching":    [ /* FamilyRule，≤32 条 */ ]
  },
  "archetypes":  [ /* Archetype，≤32 个 */ ],
  "root_causes": { /* "<signal>": ["症状层文案","物理层文案","训练层文案"]，≤64 键 */ }
}
```

没有规则就写空数组/空对象，但五个键一个都不能少。规则总数（static + 全部家族）≤128，文件 ≤256 KB，嵌套深度 ≤6。

### 6.2 StaticRule：静态点击触发规则

求值输入是本次分析的指标汇总 `summary`（形如 `{指标名: {med: 中位数, metric_version: 版本}}`）、可选的参考对比 `reference`（同形）、可选的设置 `settings`（如 `{"cm_per_360": 23.5}`）。

| 字段 | 必填 | 约束 | 说明 |
|---|---|---|---|
| `signal` | 是 | 官方词汇表 `signals` | 触发后发出的信号名，即这条规则的"现象" |
| `severity` | 是 | `info` / `watch` / `fix` | 意在表达严重度（见 6.7 当前运行时收尾说明） |
| `text` | 是 | ≤600 字符 | 中文诊断句；可含插值槽位，见 6.7 |
| `plain_language_meaning` | 否 | ≤400 字符 | 大白话版现象描述 |
| `expected_result` | 否 | ≤400 字符 | 预期改善方向 |
| `claim_level` | 是 | 5 档枚举，见第 9 节 | 这条规则的证据档位 |
| `metric_refs` | 是 | 非空，官方词汇表 `metric_keys` | 规则涉及的指标 |
| `limitations` | 否 | 官方词汇表 `limitation_tokens` | 局限标注 |
| `observation_ref` | 否 | 官方词汇表 `observation_refs` | 关联观察对象 |
| `conditions` | 是 | 1-4 条 Condition，AND 组合 | 全部成立才触发；缺数据 = 不成立 |
| `prescriptions` | 否 | ≤8 条 Prescription | 训练建议 |

### 6.3 Condition 与算子

```jsonc
{ "input": "self_summary", "metric": "decel_frac", "stat": "med", "op": ">", "value": 0.65 }
```

- `input`（取值通道）：`self_summary`（本次）/ `reference_summary`（参考对比，如你的历史 baseline）/ `settings`（设置值通道，`metric` 直接是键名如 `cm_per_360`，不带 `{med:...}` 包装）。
- `stat`：v1 固定 `med`（中位数），可省略。
- `metric`：官方词汇表 `metric_keys`。

| 算子 | `value` 形态 | 语义（引擎口径） |
|---|---|---|
| `>` `<` `>=` `<=` | 数字 | 与通道值比较。**通道值缺失 → 条件不成立**（不报错、不猜） |
| `in_band` | `[lo, hi]`，必须 lo < hi | **开区间**：lo < 值 < hi 才成立（边界值不触发）。无界侧用 `±1e308` 哨兵，如 `[60.0, 1e308]` 表示">60" |
| `ratio_to_ref_lt` | 正数 | `self_summary[metric].med / reference_summary[metric].med < value`。**任一侧缺失或为 0 → 静默**（不产生信号，绝不输出 0% 之类的误导比值） |
| `metric_version_not_in` | 字符串数组（≤16） | 指标的 `metric_version` 缺失或不在名单里才成立。用于"旧版算法才触发"的版本门 |

### 6.4 Prescription：训练建议

| 字段 | 必填 | 约束 |
|---|---|---|
| `scenario` | 是 | ≤120 字符，练什么场景/方法 |
| `reason` | 是 | ≤400 字符，为什么 |
| `cue` / `purpose` / `stop_or_adjust_rule` | 否 | 各 ≤400 字符，怎么做 / 为什么有效 / 何时停 |
| `retest_after` | 否 | ≤200 字符，怎么复测 |
| `target_metrics` / `expected_direction` | 是 | 字符串列表（≤16 项；允许空列表，收尾时会自动补全） |
| `source_level` | 否 | 默认 `community_consensus` |

### 6.5 FamilyRule：三个家族（追踪 / 动态点击 / 转火）的候选观察规则

家族规则的语义与 StaticRule 不同：它不走"阈值触发"，而是**与你的历史 baseline 对比**——当前值比 baseline 更差才产生候选观察，再从 registry 里找对应解释条目挂上去。

| 字段 | 必填 | 约束 | 说明 |
|---|---|---|---|
| `signal` | 是 | 词汇表 `signals` | 候选观察的信号 |
| `metric` | 是 | 词汇表 `metric_keys`（家族键，如 `continuous_tracking.sparc`） | 参与对比的指标 |
| `row_field` | 是 | 词汇表 `row_fields` | 用哪个字段拆"支持行 / 反例行"（逐次事件的证据行） |
| `direction` | 是 | `higher` / `lower` / `absolute_higher` | 哪边算更差。`higher`/`lower` 按带符号值比，`absolute_higher` 按绝对值比（相位类指标用）；**等于 baseline 不算更差** |
| `knowledge_metric_ref` | 是 | 词汇表 `knowledge_metric_tokens`（形如 `metric:sparc`） | 用来在 registry 里检索解释条目的指标 token |
| `expected_entry_ref` | 否 | `knowledge:<entry_id>@<entry_version>` | 指定解释条目。**给了就收窄 + fail-closed**：检索结果不含它 → 整条规则丢弃（导入时校验它必须指向本包 active 条目） |
| `observation_ref` | 是 | 词汇表 `observation_refs` | 关联观察对象 |
| `requires_metric_availability` | 否 | 只接受 `"available"`（默认值） | 指标不可用时规则不触发 |
| `blocking_limitations` | 否 | 词汇表 `limitation_tokens` | **否定清单**：指标自带 limitations 与之相交 → 规则不触发。用于"视觉证据质量不够就别下结论" |
| `guardrails` | 否 | `{"all": [{"metric", "op"}]}`，`op ∈ {"<=baseline", ">=baseline"}` | 前置闸门：全部成立才继续（如"误差没变大且在圈时间没缩水，才允许下平滑度结论"）。不能闸规则自己的指标 |
| `row_filter` | 否 | v1 仅 `"observable_switch_chain"` | 命名行过滤器：只在可观察的转火链行里取证据；过滤后无支持行 → 规则不触发 |
| `claim_level` | 否 | 默认 `deterministic_rule` | 证据档位 |
| `requested_knowledge_sections` | 否 | 默认 7 段：`definition`、`mechanisms`、`alternative_explanations`、`cue`、`dose_guardrail`、`matched_retest`、`stop_adjust_rule` | 触发后请 Coach 读条目的哪些段落（可选全集 11 段，含 `scope`、`expected_direction`、`forbidden_inferences`、`near_transfer_retest`） |

### 6.6 Archetype 与 root_causes

- **Archetype**（画像，≤32 个）：`{"id", "label", "conditions": {"<signal>": 权重}, "positive"}`。`conditions` 的键是官方信号，权重 0 < w ≤ 1；产品按"信号 → 加权匹配画像"给玩家贴类型标签。`positive: true` 表示正向兜底画像（此时 `conditions` 必须为空）；空条件与 positive 必须一一对应。
- **root_causes**（≤64 键）：键是官方信号，值是**恰好三条**文案 `[症状层, 物理层, 训练层]`，每条 ≤400 字符。产品按信号取出三层归因文案给 Coach。

### 6.7 运行时引擎语义（作者必读，按当前代码如实描述）

校验通过只是入场券；运行时还有一套**硬编码**的求值语义，不会因包内容改变：

1. **缺数据不触发**。任何条件、家族指标、baseline 缺失或不可用，规则静默跳过。宁可不说话，不说没依据的话。
2. **`text` 槽位插值**。诊断句里可以写 `{槽位名:格式}`（Python `str.format` 风格），运行时按信号代入当前数值，格式 spec 写在你的数据里。**当前支持的槽位按信号固定**：
   - `decel_frac high` / `decel_frac low` → `{decel_frac_pct}`
   - `linearity high` → `{linearity_med}`
   - `sparc low` → `{sparc_med}`
   - `reverse_ratio high` → `{reverse_ratio_pct}`
   - `peak_position low` / `peak_position high` → `{peak_position_pct}`
   - `path_efficiency low` → `{path_efficiency}`
   - `peak_speed below reference` → `{self_peak}` `{ref_peak}` `{ratio_pct}`
   - `throughput below reference` → `{self_throughput}` `{ref_throughput}` `{throughput_ratio_pct}`
   - `sensitivity high` → `{cm_per_360}`
   - 其他信号不支持槽位（text 写纯文案即可）。**用了不支持的槽位 → 该规则在运行时被丢弃**（分析继续，不失败）。
3. **静态信号的统一校准收尾**。当前产品把所有静态点击信号视为"阈值未经产品校准"：静态规则无论声明什么 `severity` / `claim_level` / `limitations`，求值结果在收尾阶段（`advice._finalize_uncalibrated_findings`）统一改为 `severity=info`、`claim_level=experimental`、`limitations=[threshold_requires_product_calibration]`。**你的声明字段仍要如实写**——校验约束不变，将来校准放开后它们才会在输出中生效。家族规则的 `claim_level` 等字段不受此收尾影响，按声明输出。
4. **家族前置**：该家族本次分析 `support_status == "outcome_only"`（只有结果数据、没有机制数据）或两次对比不可比（`comparable != true`）→ 该家族候选观察为空。
5. **知识解析 fail-closed**：家族规则触发后要从当前 registry 解析解释条目。带 `expected_entry_ref` 的规则，解析结果不含它 → **整条丢弃**；不带 `expected_entry_ref` 的规则，解析结果为空 → **整条丢弃**（三个家族统一此语义；没有解释的触发不算诊断）。
6. **blocking_limitations 相交 → 不触发**（见 6.5）。
7. **规则级异常只丢规则**：某条规则求值出错 → 丢弃该规则并记录诊断，本次分析照常完成。
8. **整体回退**：激活状态下 mapping.json 缺失（纯 registry 包）、损坏或校验失败 → **回退到产品内置的冻结官方规则**，分析不失败。某家族没有规则 → 该家族回退内置规则。注意一个不对称点：`static_clicking` 数组存在但为空，会在静态路径得到"零条信号"（不会回退内置的 12 条）——不想动静态规则的作者应直接不携带 mapping.json。
9. **官方档同样走这套引擎**：官方规则已数据化为 `knowledge/mapping/official.v1.json`，与你的包同格式同引擎；内置 Python 规则只是冻结的兜底。

---

## 7. 安装、激活、切换与回退：装了会怎样

实现：`kovaak_tracker/coach/knowledge_pack.py`（存储）、`kovaak_tracker/coach/knowledge_active.py`（激活解析）。

**安装布局**（用户数据目录 `DATA_ROOT` 下）：

```text
DATA_ROOT/
  config/knowledge.json          # 激活状态（knowledge_config.v1）
  knowledge-packs/<pack_id>/     # 安装目录：平铺覆盖，重导入同 pack_id 直接覆盖
    manifest.json | knowledge/registry.json | mapping.json? | README.md?
```

`config/knowledge.json`：

```jsonc
{ "schema_version": "knowledge_config.v1",
  "active": "official",            // "official" 或某个已安装的 pack_id
  "installed": [ { "pack_id", "pack_version", "display_name", "author",
                   "installed_at", "has_mapping" }, ... ] }
```

**用户视角的行为**：

- **装了会怎样**：导入通过校验后，包进入 `knowledge-packs/<pack_id>/` 并登记到 `installed`。**安装 ≠ 激活**：安装后默认仍是官方档，需要用户在设置页点激活。
- **整库替换**：激活某个包时，官方知识 registry 与官方 mapping **完全不加载**——不叠加、不混合、不做命名空间隔离。Coach 的讲解、理论、训练推荐全部只来自该包。同一时刻只有一个激活档。
- **切换生效**：切换 = 写 `config.active` + backend 即时调用 sidecar 的 `POST /knowledge/rematerialize` 重建知识物化目录，切换后下一次对话即生效；sidecar 不可达时降级为「重启应用后生效」，切换界面如实提示。
- **场景 registry 永远官方**：场景档案（`knowledge/scenarios/`）是测量合同，**永不随包替换**；你的 `scenario_prescription` 只能引用官方已审核场景（第 10 节）。
- **坏了会怎样（fail-closed 回官方）**：配置缺失 / 配置损坏 / `active` 指向未安装的包 / 激活后包文件被改坏 → 自动回退官方档，分析照常完成，回退原因会被记录（Python 侧 `knowledge_active.last_fallback_reason()`；sidecar 侧 console.error 并给出用户可见提示）。**你永远不会因为一个坏包而拿不到分析结果。**
- **历史分析不受影响**：每条历史分析记录着当时的 `knowledge_registry_version`，按版本号精确回读。你升级包（同 `pack_id` 重导入新 `pack_version`，覆盖安装）后，旧分析仍指向旧版本字符串。**注意**：v1 平铺覆盖安装不保留旧版本文件，被覆盖后旧版本无法重读原文，历史条目按"知识库已更新"口径展示。
- **卸载**：删除安装目录并注销登记；若卸载的是激活包 → 自动切回 `official`。卸载后，引用过该包的历史分析保留，知识引用显示为"来自已移除的知识库"（display-only，不删数据）。

---

## 8. 导入校验流程

**CLI 校验**（第 12 节）与产品内导入共用同一个 Python 校验核心，顺序如下：

```text
1. 布局检查    目录/zip → 白名单文件 → 大小上限 → zip 路径安全
2. manifest    JSON 解析 → 字段/格式/unsafe-shape → ac_compat 一致性
3. registry    读取 + 大小门 → 收窄规则 2/3/4（来源天花板、场景 ref、词汇表）
               → v3 校验器全量校验（结构、claim 天花板、fail-closed 引用合同）
               → registry_version == "<pack_id>@<pack_version>"
4. mapping     （可选文件）读取 → ac_compat 声明核对 → coach_mapping.v1 全量校验
               → 交叉校验：每条 expected_entry_ref 能被本包 registry 解析为 active 条目
5. 结论        任何一步失败 → 整包拒绝，逐条输出 {code, message, path}
```

产品内导入在此之上还有两步（CLI 不做）：**TS 双端一致性校验**（同一份 registry 再过一遍 TS 校验器，两侧都接受才放行）与**安装登记**（写入 `DATA_ROOT` 并登记 config）。官方场景 registry 不可用时场景引用校验按 fail-closed 处理（报 `scenario_registry_unavailable`）。

---

## 9. 证据档位：claim_level 与 source_level

Aiming Cookie 的原则：**教练可以讲任何东西，但必须声明"以什么资格讲"**。产品据此决定一条知识能被用到什么深度，玩家也能看到每句话的证据成色。

**claim_level（一条结论的证据档位，5 档）**：

| 档位 | 含义 |
|---|---|
| `deterministic_rule` | 确定性规则：从测量定义直接推出（产品/官方专属档） |
| `research_supported` | 有同行评审研究支持 |
| `community_consensus` | 社区/教练群体有广泛共识 |
| `community_practice` | 社区常见做法，共识程度低于上一档 |
| `experimental` | 个人假设/实验性说法，需受控实验验证 |

**source_level（一个来源的资格等级，7 档）**：

| 档位 | 第三方可用？ |
|---|---|
| `product_contract` | **禁用**（产品合同专属） |
| `coach_first_party` | **禁用**（官方第一方专属） |
| `academic_peer_reviewed` | 可用（天花板 `research_supported`） |
| `community_organization` | 可用（天花板 `community_practice`） |
| `community_consensus` | 可用（天花板 `community_consensus`） |
| `personal_experience_unverified` | 可用（天花板 `experimental`） |
| `experimental` | 可用（天花板 `experimental`） |

硬约束是单向的：**section 的 claim_level 不得超过其来源的天花板**（对照表见 4.4）。第三方作者无法通过声明把个人经验抬成确定性规则——这正是收窄规则 2 的意义。

---

## 10. scenario_prescription：官方场景引用约束

`scenario_prescription` 把一个训练处方绑定到**具体场景档案**（含哪些分析管线被允许运行的测量合同）。因为场景档案直接决定"哪些结论被允许生成"，它属于产品测量事实，**不能由第三方包自定义**：

- 场景 registry（`knowledge/scenarios/registry.v1.json`）永远官方，永不随包替换；
- 你的条目里 `scenario_prescription.scenario_profile_ref` 只能引用官方已审核条目，格式 `scenario:<场景entry_id>@<版本>`，例如官方当前已审核的 `scenario:static.1wall_6targets_small@1`（1wall 6targets small）。可选清单以官方 registry 为准，导入校验会逐条核对（规则 3）；
- 自造 `scenario:xxx` 引用会被拒（`scenario_ref_not_official`）。

---

## 11. 词汇表冻结承诺

官方词汇表是 `knowledge/mapping/vocabulary.v1.json`（`coach_mapping_vocabulary.v1`），mapping 校验与包收窄都以它为准。对作者的承诺分两档强度：

**作者硬合同（只加、不改、不删）**：

- `signals` —— 全部合法信号名（如 `sparc low`、`tracking lag high`）；
- `metric_keys` —— 条件与规则可引用的指标键（如 `sparc`、`continuous_tracking.sparc`）；
- `observation_refs` —— 合法观察对象（如 `metric.terminal_control`、`event.switch_chain`）。

产品升级只会往这三节**追加**；改名或删除都构成破坏性变更（breaking），不会发生在不升 schema 大版本的前提下。你的包在这些集合内的引用长期有效。

**产品拥有（作者只读引用）**：

- `limitation_tokens`（局限标注词，如 `threshold_requires_product_calibration`）、`row_fields`、`row_classifications`、`row_filters`、`knowledge_metric_tokens`；
- `enums`（`severity` / `direction` / `claim_level` / `op` / `input` 的取值全集）。

这些词汇由测量侧产品演进拥有；**产品若改名或删除，构成 breaking，会随产品版本明确公告**，届时校验器会明确指出哪个引用失效。作者应避免把它们硬编码进面向读者的说明里。

---

## 12. 校验器 CLI 用法

在 Aiming Cookie 仓库根目录（或可访问本仓库 Python 环境的任意目录）：

```bash
# 校验一个包目录
python -m kovaak_tracker.coach.knowledge_pack validate <包目录或zip路径>

# Windows venv 环境
.venv/Scripts/python.exe -m kovaak_tracker.coach.knowledge_pack validate my-kb
```

- 退出码：`0` = 合法（输出 `OK <pack_id>@<pack_version> has_mapping=<true/false>`）；`1` = 不合法。
- 不合法时逐条输出错误，格式 `ERROR <错误码> [文件路径]: <原因>`，最后一行 `FAILED with N error(s)`。
- 校验器只读你的包，不会写入、不会联网。

示例：

```text
$ python -m kovaak_tracker.coach.knowledge_pack validate my-kb
OK com.coachwang.static-kb@1.0.0 has_mapping=True

$ python -m kovaak_tracker.coach.knowledge_pack validate my-broken-kb
ERROR signal_out_of_vocabulary [knowledge/registry.json]: entries[0].signals item '我的自定义信号' is not in the frozen official vocabulary (signals); extend signal_aliases instead
FAILED with 1 error(s)
```

---

## 13. 常见错误速查表

| 错误码 | 含义 | 修法 |
|---|---|---|
| `manifest_missing` | 缺 manifest.json | 补上 |
| `pack_invalid_json` | JSON 语法错误 | 用 JSON 校验工具查逗号/引号/注释（JSON 不允许注释与尾逗号） |
| `pack_unknown_file` | 包里有白名单之外的文件 | 只保留四个合法文件；打包时排除 `.DS_Store`、桌面快捷方式等 |
| `zip_unsafe_entry` | zip 内含绝对路径或 `..` | 重新压缩：从包目录内部选中内容打包，或允许一层文件夹包裹 |
| `pack_file_too_large` / `pack_too_large` | 文件/整包超限 | 见第 2 节上限表 |
| `manifest_field_missing` / `manifest_field_unknown` | manifest 缺必填字段 / 有未知字段 | 对照第 3 节字段表 |
| `manifest_pack_id_invalid` | pack_id 格式非法 | 小写字母/数字/点/连字符，首尾不能是点或连字符 |
| `manifest_pack_version_invalid` | 版本号不是 semver | 用 `1.0.0` 三段式 |
| `manifest_schema_version_invalid` | schema_version 写错 | 固定 `coach_knowledge_pack.v1` |
| `ac_compat_invalid` | ac_compat 字段非法 | knowledge_schema 只能是 `["coach_knowledge_registry.v3"]` |
| `ac_compat_mapping_mismatch` | mapping.json 与 mapping_schema 声明不一致 | 两边同步：有文件就声明，没文件就删声明 |
| `manifest_unsafe_content` | manifest 含路径/密钥样文本或指令类字段名 | 删掉相关内容；manifest 只放第 3 节的字段 |
| `registry_version_mismatch` | registry_version ≠ `<pack_id>@<pack_version>` | 同步改 registry 的 registry_version |
| `source_level_forbidden` | 用了官方专属来源等级 | 改为 `community_consensus` 等可用等级（第 9 节） |
| `signal_out_of_vocabulary` / `metric_out_of_vocabulary` | signals / metric_refs 越出官方词汇表 | 换官方词汇；自创同义词放 `signal_aliases` |
| `scenario_ref_not_official` | 场景引用不是官方已审核条目 | 从官方场景 registry 挑 ref（第 10 节） |
| `scenario_registry_unavailable` | 官方场景 registry 无法加载，校验按 fail-closed 拒绝 | 产品安装损坏；重装产品后再试 |
| `registry_invalid` | registry 未过 v3 校验（附具体原因） | 按消息修；对照 `knowledge/coach/schema.v3.json` 与模板 |
| `mapping_invalid` | mapping 未过 coach_mapping.v1 校验（附具体原因）；含 expected_entry_ref 悬空/指向 retired 条目 | 按消息修；expected_entry_ref 必须指向本包 active 条目 |

---

## 附：本规范与实现合同的对应

- 包格式、收窄规则、安装布局与回退语义对应工程合同 C1/C2/C3（`.zcode/kb-sdk-impl-plan-2026-09-20.md` §2）；
- 词汇表冻结口径对应 C7；
- 官方包实例：`knowledge/mapping/official.v1.json`；官方场景 registry：`knowledge/scenarios/registry.v1.json`；
- 模板包：`sdk/knowledge-pack/template/`（被自动化测试守护，始终可通过校验）。
