# AI Aim Coach 设计：单次 Coaching 输出体验

> 日期：2026-06-28 · 状态：设计稿，待用户 review · 作者：brainstorming session
> 把 flicking 模块从「运动学分析器 + 规则诊断器」升级为「单次 coaching 输出体验」：综合画像诊断 → 可视化 → LLM 讲解。

---

## 1. 目标与范围

### 做什么
一次 flicking 分析 → 一份完整的 `CoachReport`：画像（你是什么流派）+ 按优先级排序的问题（每个含三层根因链 + 处方）+ 可视化图表 + LLM 教练讲解。

### 不做什么（明确留后续）
- **进步闭环**（历史/趋势/复测/计划调整）→ 后续 spec
- **多轮对话**（B 形态）→ 后续，且需先去瞄准社区补理论
- **web 前端** → 后续（这次可视化用 Plotly，**前端无关**设计，未来嵌 web）
- **目标检测类指标**（overshoot / reaction / target_selection）→ 后续二期
- **streaming / tool use / agent 框架** → 不需要（单向讲解是单次生成任务）

### 输入
两条路径都产**同形 fair summary**，下游统一消费：
- **reference 模式**（无 CSV，`analyze_flicking_reference`）——已跑通
- **CSV 模式**（`analyze_flicking_video`）——需前置 **PROGRESS [A]**：让它也输出公平 summary（复用 `segment_by_valleys` + `compute_fair_metrics`）

---

## 2. 产品行为（用户看到的）★ review 重点

用户分析一次后，得到一份 `CoachReport`，包含：

1. **画像卡**：「你是 **急加速-长减速-减速抖型**（匹配度 0.82），次要特征：发力不足」——一眼知道自己是什么型
2. **指标雷达图**：你 vs 参考，多维度强弱一目了然（不堆数字）
3. **减速段速度曲线**：典型 flick 的速度曲线 vs 理想 min-jerk，直观看减速段长不长 / 抖不抖
4. **对比表**：逐指标 better / worse（有参考时）
5. **优先级问题列表**：按优先级排序，每条 = 问题 + 三层根因链 + 处方
6. **教练讲解**：LLM 把以上串成一段教练话（画像 → 头号问题 + 根因 → 怎么练）

**讲解风格**：教练口吻、具体、可执行；基于诊断数据不编造指标；中文。

---

## 3. 架构与数据流

方案 2（分层独立）：新建 `kovaak_tracker/coach/` 子包，**消费** advice（不动 advice）。

```
[reference 无 CSV] ─┐                     [CSV + PROGRESS A 统一] ─┐
                    ├─→ fair summary ──────┘
                                        ▼
                          advice.advise() ──→ list[Finding]   ← 不动，已扎实
                                        ▼
                          coach/diagnosis.py
                          · 画像典型匹配（C：典型 + 规则补偏差）
                          · 根因链填充（症状→物理→训练）
                          · 优先级排序
                                        ▼
                          ━━━━━━━ CoachDiagnosis ━━━━━━━   ← 结构化契约
                          │                              │
                ┌─────────┴─────────┐         ┌──────────┴──────────┐
                ▼                   ▼         ▼                     ▼
        visualization.py      report.py    narrator.py        (未来：闭环)
        Plotly 图表           组装报告    LLM 讲解
        (前端无关)            CoachReport (Claude/本地,
                                          pi 骨架)
```

### 模块职责
| 模块 | 职责 | 依赖 |
|---|---|---|
| `coach/diagnosis.py` | findings → CoachDiagnosis（画像 + 根因 + 优先级）| advice |
| `coach/visualization.py` | CoachDiagnosis + summary → Plotly 图表 | plotly |
| `coach/narrator.py` | CoachDiagnosis → LLM 讲解文本 | anthropic/openai SDK |
| `coach/report.py` | 组装 CoachReport + 端到端入口 | 上面三个 |
| `coach/providers.py` | LLM backend 抽象 + 配置/凭据（借鉴 pi 骨架）| - |

---

## 4. 数据模型（CoachDiagnosis 契约）

```python
@dataclass(frozen=True)
class RootCause:
    level: str   # "symptom"(表现) | "physical"(物理原因) | "training"(训练原因)
    text: str

@dataclass(frozen=True)
class ProfileMatch:
    archetype_id: str          # "long_decel_jitter"
    label: str                 # "急加速-长减速-减速抖型"
    confidence: float          # 匹配度 0-1
    secondary_tags: list[str]  # 次要命中（规则补偏差）

@dataclass(frozen=True)
class DiagnosisIssue:
    signal: str                       # 来自 finding，如 "sparc low"
    severity: str                     # info/watch/fix
    root_causes: list[RootCause]      # 三层链
    prescriptions: list[Prescription]# 来自 advice
    priority: int                     # 1 = 最优先
    priority_reason: str              # 为什么排这

@dataclass(frozen=True)
class CoachDiagnosis:
    profile: ProfileMatch
    issues: list[DiagnosisIssue]       # 按 priority 排序
    summary: dict                      # 原始 fair summary（visualization 用）
    comparison: list[dict] | None      # compare_table 输出（有参考时）
    meta: dict                         # cm_per_360 / fps / 参考来源 / 时间戳

@dataclass(frozen=True)
class CoachReport:
    diagnosis: CoachDiagnosis
    figures: dict[str, object]         # {图表名: plotly Figure}，前端无关
    narration: str | None              # LLM 讲解（失败时 None）
    notes: list[str]                   # 降级/警告提示
```

**设计理由**：三层根因链是教练诊断的经典模型（表现→物理→训练）；`ProfileMatch.secondary_tags` 对应画像 C（主典型 + 规则补偏差）；`meta` 让 narrator 能讲「sens 48cm 偏快」这种上下文；消费层只依赖 `CoachDiagnosis` 一个对象，接口干净。

---

## 5. 诊断生成（diagnosis.py）

### 5.1 画像典型集（提案，可调）

每个典型 = 一组信号条件（信号来自 advice findings）：

| archetype_id | label | 触发信号 | 物理含义 |
|---|---|---|---|
| `long_decel` | 急加速-长减速型 | `decel_frac high`（+ `peak_position low`）| 减速段在蹭，制动不果断 |
| `decel_jitter` | 减速抖动型 | `sparc low` 或 `reverse_ratio high` | 张力释放不平滑 |
| `two_stage` | 两段式型 | `submovement_overlap` 低 + `corrective_count≥1` | corrective 与 primary 分离 |
| `underpowered` | 发力不足型 | `peak_speed below ref` 或 `throughput below ref` | 手腕主导，arm 发力弱 |
| `inefficient_path` | 路径低效型 | `path_efficiency low` | flick 路径绕 |
| `fluid_precise` | 流体精度型 | 各项健康（**正面画像/目标**）| 参考形态 |

> 典型集是内容，theory 锚定见 `aim-kinematics-research.md`。review 时可增删/改名。

### 5.2 匹配机制
- 每个典型定义为一组信号条件（带权重）
- 对 summary 计算每个典型的**命中度** = 命中条件数 / 总条件数（加权）
- **主典型** = 命中度最高且 > 0.5；否则标 `unclassified`（次要标签照列）
- **secondary_tags** = 其他命中（未达主典型阈值但有信号）
- 置信度 = 主典型命中度

### 5.3 根因链填充（信号 → 三层映射表）

| signal | symptom（表现）| physical（物理）| training（训练）|
|---|---|---|---|
| `decel_frac high` | 减速段占 75% 在蹭 | 制动释放不果断 | 减速一次到位意识弱 |
| `sparc low` | 减速段抖动 | 张力释放不平滑（高频成分多）| 减速段控制稳定性 |
| `reverse_ratio high` | 减速段反复修正 | 制动方向不稳 | 单次制动 + 流体修正 |
| `two_stage` | flick→停→micro | corrective 与 primary 分离 | 转流体派（overlapping）|
| `peak/throughput low` | 甩得偏慢 | 发力不足（手腕主导）| arm 发力 + speed 场景 |
| `linearity high` | 制动不匀 | 减速节奏不稳 | 匀速制动练习 |
| `path_efficiency low` | 路径绕 | flick 几何不直 | linetrace 直线练习 |

映射表是**数据**（dict），不是硬编码 if-else——便于 review/调整。每条 finding 按 signal 查表填三层。

### 5.4 优先级
```
priority_score = severity_weight × 0.6 + deviation × 0.4
  severity_weight: fix=3, watch=2, info=1
  deviation: 信号偏离阈值的归一化程度（0-1）
按 priority_score 降序排 issues，priority 从 1 起。
priority_reason = "[fix] 严重度 + 偏离阈值 X%"
```

---

## 6. 可视化层（visualization.py）

5 类图表，全部生成 **plotly Figure 对象**（不绑 Streamlit，可 `to_html` / 嵌 web，前端无关）：

1. **画像卡**：主典型标签 + 匹配度 + 次要标签（HTML 组件或 annotation）
2. **指标雷达图**：你 vs 参考，维度 = decel_frac / linearity / sparc / reverse / path_eff / peak_speed（归一化到 0-1，linearity/reverse/sparc 反向）
3. **减速段速度曲线**：典型 flick（中位 peak_speed 那条）的速度曲线 + 理想 min-jerk overlay，标注峰位
4. **对比柱状图**：compare_table 的 self vs ref，better/worse 配色
5. **优先级问题列表**：issues 排序渲染（signal + 根因链缩进 + 处方），文本/表格组件

**归一化规则**（雷达图）：每个维度按健康区间映射到 0-1（健康=中心外，偏离=中心内），让你 vs 参考的形状差异直观。

---

## 7. narrator LLM 层（narrator.py）

借鉴 pi 的 provider 骨架（Python 简化实现），**不引入 agent 框架**。

### 7.1 Backend 抽象（按 API 协议分，借鉴 pi）
```python
class LLMBackend(Protocol):
    def generate(self, system: str, user: str) -> str: ...
```
- `AnthropicBackend`：anthropic-messages 协议（anthropic SDK，Claude）
- `OpenAICompatBackend`：openai-completions 协议（本地 Ollama + 未来 OpenAI 兼容 provider）

**按协议分而非按厂商分** ← pi 核心洞察。一个协议适配器覆盖一类 provider。

### 7.2 配置与凭据（借鉴 pi auth.json）
- `coach/providers.toml`（或 json）：每个 provider 的 baseUrl / api_key_env / model / cost
- 凭据 resolution 优先级：env var > 配置文件 > CLI（对齐 pi）
- 默认 provider 可配置（如 `coach.provider = "anthropic"`）

### 7.3 Prompt 结构
- **system**：教练人设 + 运动学知识约束 + 「只基于以下诊断数据讲解，不要编造指标/数值」
- **user**：`CoachDiagnosis` 的结构化 JSON（profile + issues + 根因 + comparison + meta）
- **输出**：一段中文教练讲解（画像 → 头号问题 + 根因 → 怎么练，~150-300 字）

### 7.4 防幻觉
诊断在规则引擎（确定性），`CoachDiagnosis` 作为事实约束注入 prompt。LLM 只做语言表达，不做诊断推理——这样输出稳定、可复现。

---

## 8. 前置：PROGRESS [A] 统一 CSV summary

`analyze_flicking_video` 当前走旧 `run_flicking_analysis`（旧切分 + 旧减速指标）。改为：
```
compute_pan_trajectory → segment_by_valleys → compute_fair_metrics → _summarize
```
（复用 reference 模式逻辑）输出与 reference 同形的 fair summary，进 coaching 管线。

这是最小改动，且是 PROGRESS 已记录的待办——纳入这次 spec 顺手做，让用户自己的有 CSV 录像能进 coaching。

---

## 9. 边界与降级

| 情况 | 行为 |
|---|---|
| flick 数 < 5 | diagnosis 降级：画像置信度标注「样本少」、narrator 说明 |
| LLM 调用失败（网络/key/超时）| report 仍出结构化 + 可视化，narration=None，notes 标注「讲解不可用」|
| 无参考（comparison=None）| 跳过对比图表/讲解；画像匹配不受影响 |
| 某指标 NaN（如 throughput 无目标宽度）| 雷达图跳过该维度；narrator 不提 |
| 画像未匹配任何典型（confidence<0.5）| 标 `unclassified`，列 secondary_tags，narrator 如实说明 |

降级原则：**永远产出可用的结构化 + 可视化**，讲解是锦上添花（失败不阻塞）。

---

## 10. 测试策略

| 层 | 测试 |
|---|---|
| `diagnosis.py` | 合成 findings → 验证画像匹配 / 根因链填充 / 优先级排序（确定性，规则引擎）|
| `visualization.py` | 合成 CoachDiagnosis → 5 类图表生成不报错 + 关键元素存在 |
| `narrator.py` | mock LLMBackend → 验证 prompt 构造（含 CoachDiagnosis JSON）+ 降级路径；真实 backend 烟测（可选，需 key）|
| `report.py` 端到端 | 合成 summary → CoachReport，含降级场景（无参考 / LLM 失败）|
| PROGRESS [A] | 改造后 `analyze_flicking_video` 输出 fair summary，与 reference 同形 |

---

## 11. 后续（不在这次 spec）

- **进步闭环**：summary 落库 + 历史趋势 + 复测对比 + 计划调整（独立 spec）
- **多轮对话（B）**：需先去瞄准社区补理论（用户明确），用 Claude 原生 tool-use，可能接入 pi 代码（TS）
- **web 前端**：Plotly 图表层已前端无关，迁 web 时嵌入
- **目标检测类指标**：overshoot / reaction / target_selection（二期）
- **画像典型集校准**：用真实数据验证/调整典型与阈值（同 linearity 当初）

---

## 附：关键设计决策回顾（brainstorming 澄清结论）

| 维度 | 决定 |
|---|---|
| 输入 | reference + 统一 CSV（含 PROGRESS A）|
| 画像 | C：典型匹配 + 规则补偏差 |
| 产物 | 结构化 first-class + 人话（从结构化生成）|
| 可视化 | Plotly，前端无关，web 后续 |
| 讲解 | LLM 单向（A）；对话 B 后续 |
| LLM 后端 | Claude + 本地两后端（C）|
| agent 框架 | 不要，单纯 API + prompt |
| LLM 复用 | Python 借鉴 pi 骨架（协议分类 + 配置 + 凭据），不引 streaming/tool |
| 范围 | 单次 coaching 输出一体；闭环留后续 |
