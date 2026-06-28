# 计划调整（Plan Adjustment / 动态处方）设计

> 日期 2026-06-29 · coach 闭环最后一块（progress loop scope ④）。基于跨次趋势的自适应训练计划——纯规则引擎（学术根基），LLM 仅可选翻译。autonomous brainstorming（用户授权"推进 ④"后入睡，开放问题待醒后拍板）。
> 上游 spec：`2026-06-28-progress-loop-design.md`（scope B：持久化 + 趋势 + 对比，明确把 ④ 留后续）。

## 1. 目标与范围

用户积累了多次 session 历史 → `build_progress_report` 已能出趋势 + 对比 + 进步讲解。④ 在此之上加一层：**基于趋势判断，输出"下次该怎么练"的自适应计划**。

**核心原则（三条理论汇成的统一判断）**：指标停滞时，**不默认"练不够 / 加量 / 加频"**，而是 (a) 调结构——交错多场景（§1.3），(b) 把"练习量已超收益递减区"列为诊断假设（§1.2），(c) 提示间隔练习而非每天复测（§2.2）。

**做**：
- 确定性规则引擎：趋势/对比 → `TrainingPlan`（焦点指标 + 调整项 + 场景编排 + 复测频率建议 + 理论锚点）
- 挂进 `ProgressReport`（向后兼容）
- 可选 LLM 翻译（best-effort，复用 narrator 范式，防幻觉）

**不做**（留后续）：
- 多轮对话 Socratic / guidance fading（B 形态，§2.3，需对话状态管理 + 几乎只能 Claude tool-use）
- 自动定期复测调度 / 推送提醒（产品层）
- 多用户 / 云端

## 2. 理论锚点（均【学术·同行评审·经久根基】，可进诊断逻辑）

| 理论 | 经久结论（原文要点） | ④ 规则映射 |
|---|---|---|
| **§1.3 Contextual Interference**（Schorn & Knowlton 2020 CogSci；2024 Czyż 元分析）| 交错练习习得期更差但**长期保留更好**（desirable difficulty）；实验室精细运动任务稳健（SMD=0.92），应用场域弱（SMD=0.23）| stall/regress 指标 → 推**交错多场景**（不只刷一个 scenario）。措辞标注"基于实验室精细运动证据的合理推论，不夸大为已证实的 aiming 场域效应" |
| **§1.2 Deliberate Practice 上限**（Ericsson 1993 *Psychological Review*）| 每日 >4h 无收益、>2h 递减；精英 session 均 80min；过度练习→staleness/overtraining/burnout | stall 时把"练习量已超收益递减区"列为**假设之一**，不默认练不够；高频复测提示"休息也是训练" |
| **§2.2 Guidance Hypothesis**（Salmoni 1984）| 反馈太频繁→依赖→损害长期学习；异步分析风险低但原理适用 | **不鼓励高频复测**，指导间隔练习（每周 2-3 次而非每天），给隐式适应留空间 |

**铁律（沿用 coach 既有约束）**：诊断/计划规则**只用学术根基**；社区/经验层（Voltaic 流派、target selection、sensitivity）只进 narrator 文案 + profile 标签，**不进规则**。系统不默认"更多反馈=更好"。

## 3. 设计决策（autonomous，开放问题见 §11）

| 维度 | 决定 | 理由 |
|---|---|---|
| 范式 | 确定性规则引擎 + 可选 LLM 翻译 | 与 `advice`（规则）+ `narrator`（翻译）100% 一致；可测、防幻觉、不依赖 LLM |
| stall 判定 | 复用 `build_comparison` 的 `verdict`（current vs baseline ±5% band），不引入斜率阈值 | YAGNI；verdict 已处理方向 + 归一化；避免新阈值拍脑袋 |
| 处方场景池 | `build_progress_report` 内部对 `current_summary` 现跑 `advise()` 取 findings → prescriptions | 自包含，调用方不多传参数；`advise` 是纯函数、开销可忽略 |
| 输出位置 | `ProgressReport` 加 `plan: TrainingPlan \| None = None` | frozen dataclass + 默认值，向后兼容；内聚 |
| LLM | `generate_plan_narration`（best-effort，backend 失败→None+note）| 与现有 narration 同级降级 |
| 隔离 | 实现阶段开分支 `coach/plan-adjustment`，不碰 main、不 commit；spec 先停工作树等 review | brainstorming HARD-GATE + 对点点的承诺 |

## 4. 架构

新建 `kovaak_tracker/coach/planning.py`；扩展 `progress.py`（ProgressReport 加字段）、`narrator.py`（plan prompt）、`report.py`（build_progress_report 调 build_plan）。

```
build_progress_report(history_path, current_summary, ref_summary?, meta?, backend?)
  → load_history
  → trend = build_trend(history)
  → comparison = build_comparison(history, current_summary, ref_summary)
  → findings = advise(current_summary, ref_summary, cm_per_360=meta?)   # 现跑，取场景池
  → plan = build_plan(trend, comparison, history, findings)             # ④ 新增
  → trend_figure + comparison_figure + comparison_table
  →（若 backend）progress_narration + plan_narration                     # 两个独立 best-effort
  → ProgressReport(..., plan=plan)
```

## 5. 数据模型（planning.py）

```python
@dataclass(frozen=True)
class PlanAdjustment:
    kind: str                       # "interleave" | "regress_focus" | "maintain" | "rest"
    target_metric: str | None       # TREND_METRICS 之一；rest/maintain 可为 None
    scenarios: list[Prescription]   # 交错编排的训练场景（来自 advice 处方池）
    reason: str                     # 人话理由（给 narrator / UI）
    evidence: str                   # 理论锚点引用，如 "§1.3 CI（Schorn & Knowlton 2020）"

@dataclass(frozen=True)
class TrainingPlan:
    focus_metrics: list[str]        # stall/worse 的指标（下阶段重点）
    adjustments: list[PlanAdjustment]
    schedule_note: str              # 复测频率建议（每周 2-3 次，§2.2）
    evidence_anchors: list[str]     # 本 plan 引用的理论条目
    notes: list[str]                # 降级 / 数据不足说明
```

`Prescription` 复用 `advice.Prescription`（scenario + reason），不新造。

## 6. 规则引擎（`build_plan`，确定性）

```python
def build_plan(trend, comparison, history, findings,
               now=None) -> TrainingPlan:
```

输入均来自 scope B 既有产物 + `advise` 现跑结果。`now` 注入便于测试（默认 `datetime.now()`）。

**规则（按优先级，互斥于单指标）**：

| 条件 | 触发 | 产出 |
|---|---|---|
| 某指标 `verdict == "worse"` | regress | `regress_focus`：该指标进 focus_metrics，换/补处方场景（交错），reason 指向退步，evidence §1.3 |
| 某指标 `verdict == "same"` 且 `len(history) >= N_MIN` | stall | `interleave`：该指标进 focus_metrics，推交错多场景，reason "相对基线无变化，换结构而非加量"，evidence §1.3 + §1.2（超收益递减区假设）|
| 最近两次 session 间隔 `< REST_GAP_DAYS` | 过频 | `rest`：scenarios 空，reason "复测过频，间隔练习 + 休息"，evidence §1.2 + §2.2 |
| 某指标 `verdict == "better"` | 进步 | `maintain`：reason "保持当前训练，别乱改"。**scenarios 可空**——better 常意味着该指标已进健康带、`advise` 不再为它产 finding；若仍有 finding 则附其处方，否则空 |
| `len(history) < N_MIN` | 数据不足 | 不判 stall/regress；只给 schedule_note + notes "历史不足 N_MIN 次，仅观测不判定停滞" |

**焦点排序**：focus_metrics 按 advice 的 severity 权重（fix>watch>info）排，与 `diagnosis._build_issues` 一致。

**场景交错编排**：从 focus 指标对应的 findings.prescriptions 取场景；若多个 focus 指标，合并去重场景池，reason 注明"交错练习这些场景"（不指定顺序——CI 效应本就不依赖固定顺序）。

**复测频率**：schedule_note 固定建议"每周 2-3 次，间隔练习"（§2.2），不论是否触发 rest——因为 guidance hypothesis 是普适约束，不只是过频时才提。

## 7. 集成（report.py 扩展）

```python
# progress.py
@dataclass(frozen=True)
class ProgressReport:
    trend_figure: Any
    comparison_figure: Any
    comparison_table: list[dict]
    progress_narration: str | None
    plan: TrainingPlan | None = None        # ④ 新增（默认 None，向后兼容）
    plan_narration: str | None = None        # ④ 新增
    notes: list[str] = field(default_factory=list)

# report.py build_progress_report 末尾
findings = advise(current_summary, ref_summary, cm_per_360=(meta or {}).get("cm_per_360"))
plan = build_plan(trend, comparison, history, findings)
plan_narration = None
if backend is not None:
    try:
        plan_narration = generate_plan_narration(plan, backend)
    except Exception as e:
        notes.append(f"计划讲解不可用: {e}")
return ProgressReport(..., plan=plan, plan_narration=plan_narration, notes=notes)
```

## 8. narrator 扩展（plan 讲解）

- `PLAN_SYSTEM_PROMPT`：教练口吻讲"下次该怎么练"（基于 TrainingPlan 结构），先 KR（进步/停滞的结果）再 KP（怎么调训练结构），引用 CI/训练上限/guidance。
- `generate_plan_narration(plan, backend) -> str`：best-effort，铁律同现有——只基于 plan 数据讲解，不编造数值。
- 英文术语配人话（如「交错练习（interleaved）——不要只刷一个场景，多个场景交替练」）。

## 9. 边界与降级

| 情况 | 行为 |
|---|---|
| 历史空（首次）| plan 非 None 但 adjustments 空，schedule_note 仍给，notes "首次，无趋势可判停滞" |
| `len(history) < N_MIN` | 不判 stall/regress；better/worse 仍可判（verdict 不依赖 N）|
| 无 ref_summary | 影响 advice 的 ref 类 finding，不影响 plan 规则（plan 看 verdict + history）|
| 指标 NaN | trend/comparison 已跳过；plan 跳过对应指标 |
| 间隔无法解析（timestamp 坏）| 跳过 rest 规则，notes 标记 |
| LLM 失败 | plan_narration=None + note；plan 结构化部分照常返回 |

## 10. 测试（TDD 矩阵，合成历史，全 mock 不依赖真实 LLM/SDK）

| 层 | 测试 |
|---|---|
| planning.build_plan stall | 合成 comparison（某指标 verdict=same）+ history≥N_MIN → 触发 interleave adjustment + focus_metrics 含该指标 |
| planning.build_plan regress | verdict=worse → regress_focus |
| planning.build_plan progress | verdict=better → maintain，不进 focus |
| planning.build_plan rest | 合成 history 最近两次间隔 <REST_GAP_DAYS → rest adjustment |
| planning.build_plan 不足 | history<N_MIN → 不判 stall，notes 含"不足" |
| planning.build_plan 焦点排序 | 多个 focus 按 severity 权重排 |
| planning 场景交错 | 多 focus → scenarios 去重合并 |
| progress ProgressReport | 新字段 plan/plan_narration 默认 None，向后兼容（旧调用不破）|
| narrator plan | mock backend → PLAN_SYSTEM_PROMPT 含防幻觉铁律 + plan 结构化 JSON |
| report 集成 | build_progress_report 端到端 → plan 非空 + schedule_note 给出；backend=None 时 plan_narration=None |

## 11. 开放问题（点点醒来拍板，均已给默认）

| Q | 问题 | 默认 | 备注 |
|---|---|---|---|
| Q1 | stall 判定最少历史次数 `N_MIN` | **3** | <3 时只观测不判停滞；防两次波动误判 |
| Q2 | stall 语义：verdict（same vs baseline）够吗，还是要近期斜率≈0？ | **verdict**（YAGNI）| 复用现有 ±5% band，不引入斜率阈值；若点点要更灵敏再加斜率 |
| Q3 | 复测过频阈值 `REST_GAP_DAYS` | **1.0**（<1 天=过频）| 理论给"每周 2-3 次"，≈间隔 ≥2 天；<1 天明显过频 |
| Q4 | TrainingPlan 挂 ProgressReport 还是独立函数？ | **挂 ProgressReport（plan 字段）** | 向后兼容；内聚 |
| Q5 | plan narrator（LLM 翻译）这次做不做？ | **做（best-effort）** | 复用 narrator 范式；不做就留 None，纯结构化 |
| Q6 | 处方场景池：build_progress_report 内现跑 advise，还是加参数传入？ | **内部现跑**（自包含）| advise 纯函数开销可忽略 |
| Q7 | 真实历史现 <N_MIN（点点的 sessions.jsonl 可能空/仅 1 条）—验证策略？ | **合成历史 TDD**（本次不依赖真实历史）| 真实联调等点点积累 ≥3 次 session 后做（与 PROGRESS 一致）|

## 11.1 research 回填（2026-06-29 deep research `wf_7793cb5f-265` 后定稿）

处方层 deep research 完成（产出 `docs/coach-prescription-manual.md`：14 确认 findings / 3 否决 / 7 caveats / 4 开放问题）。据此 close §11 的开放问题——保留原 §11 草案供追溯，定稿决策如下：

| Q | 原默认 | research 定稿 | 依据（手册章节）|
|---|---|---|---|
| Q1 `N_MIN` | 3 | **3（保持）** | research 无直接给数字；§2.2 元认知过度自信支撑"用客观指标判 stall"，N_MIN=3 防两次波动误判 |
| Q2 stall 语义 | verdict | **verdict（确认）** | §2.2 Simon & Bjork 2001：块状练习者过度自信、感觉进步≠真实学习 → **必须用客观指标（verdict）判 stall，不能用用户感知** |
| Q3 `REST_GAP_DAYS` | 1.0 | **1.0（保持，标需校准）** | §5 guidance hypothesis 只给方向（间隔练习），无具体天数字；保持经验默认 |
| Q4 挂 ProgressReport | 是 | **是（不变）** | — |
| Q5 plan narrator | 做 | **做（确认）** | narrator 补话术：外部焦点（§2.1）、元认知对抗（§2.2）、交错渐进（§4.2）|
| Q6 处方池现跑 advise | 是 | **是（不变）** | advise 已产出 §1 诊断→处方映射，plan 复用 |
| Q7 合成历史 TDD | 是 | **是（不变）** | 真实历史 <N_MIN，合成验证；真实联调等积累 ≥3 次 |

**research 带来的设计增量（超出原 §6）**：

1. **interleave 升级为"渐进 hybrid"**（手册 §4.2，Lee & Simon 2004 + Al-Ameer & Toole 1993）：最佳编排是块状（习得/热身）→ 递增交错（保留/迁移），非无脑全交错。plan 的 `interleave` adjustment 在 scenarios 编排注明"渐进"。切换阈值（几轮后切）在 aim training 无直接证据（手册 §9 Q4），**首版简化为直接交错 + reason 注明"新手可先块状几轮建立模式"，标注后续调参**。
2. **interleave 的 reason 必带元认知对抗措辞**（手册 §2.2）："感觉进步快 ≠ 长期记住；交错虽当下手感差，但长期保留/迁移更好"——主动对抗块状过度自信偏置。
3. **stall/regress 的 evidence 锚点更新**：§1.3 CI + §1.2 Ericsson + 手册 §1.1（制动代价：减速段不规则是高速接近的制动代价，非纯修正）+ §4.1（交错 > 块状的保留优势）。

**advice.py 改进（标后续，不阻塞 ④）**：submovement 处方分支化（Type 1 终止型 vs Type 2/3 修正型，手册 §1.2，需分类算法）、`reverse_ratio` 时长归一化（手册 §1.4）。④ 实现不依赖这些，它们是 advice 自身的精度提升项。

## 12. 后续（不在本次 spec）

- 真实数据校准 `N_MIN` / `REST_GAP_DAYS`（同 linearity/threshold 当初，需真实趋势样本）
- B 形态多轮对话：Socratic + guidance fading（§2.3），随熟练度增减 KP 比重
- 自动复测提醒调度（产品层）
- web 前端展示 TrainingPlan

## 附：autonomous 决策回顾

- **纯规则 + 可选 LLM 翻译**（非 LLM 主导）：守住"诊断只用学术根基、不委托 LLM 推理"铁律，可测、防幻觉。
- **复用 verdict 不造斜率阈值**：YAGNI，避免拍脑袋新参数。
- **三理论统一于"stall 不加量"**：CI 调结构 + 训练上限防过量 + guidance 防过频，三者协同指向同一处方。
- **挂 ProgressReport + 向后兼容字段**：最小侵入。
- **停车点**：spec 写完即停，不进 writing-plans、不写代码、不 commit，等点点 review §11 开放问题后继续。
