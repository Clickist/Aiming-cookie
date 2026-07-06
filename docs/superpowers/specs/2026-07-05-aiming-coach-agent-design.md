# Aiming Coach Agent 设计：从单次 LLM 翻译到 Tool-Use Agent

> 日期：2026-07-05 · 状态：设计稿，待点点 review · 作者：design session
> 上游 spec：`2026-06-28-ai-aim-coach-design.md`（单次教练输出）、`2026-06-29-plan-adjustment-design.md`（动态处方）
> 替换目标：`kovaak_tracker/coach/narrator.py` 的单次 `generate_narration` / `generate_progress_narration` / `generate_plan_narration` 三套并列 prompt
> 一句话：**把"规则引擎算诊断 → LLM 一次性翻译成中文教练话"升级为"LLM 用 tool use 自己决定调哪些知识 tool，渐进式生成讲解"，但诊断仍然是规则引擎的产物，LLM 不参与诊断推理。**

---

## 1. 目标与非目标

### 1.1 做什么

把 `narrator.py` 的"单次 LLM 调用"升级为"tool use agent loop"。Agent 接收规则引擎产出的结构化诊断（`CoachDiagnosis` / 趋势数据 / `TrainingPlan`），用 tool calls 按需检索知识库（5 份文档 + 现有 `KNOWLEDGE` dict），最后产出中文教练讲解。

核心动机：当前 narrator 把所有相关 signal 的 `KNOWLEDGE` 一次性预加载进 system prompt（`build_system_prompt`），但当知识库扩到 5 份文档（运动学/理论/社区/处方/YouTube）后，全量预加载不现实，单文档切片又割裂上下文。Agent 模式让 LLM 自己决定"讲解这条 sparc low 我要不要去翻处方手册的 SPARC 章节、要不要查社区关于减速抖动的创作者经验"——**渐进式知识披露**。

三个 narration 入口全部 agent 化（保留同一 agent，不同入口函数）：
1. **单次分析讲解** `generate_narration(diagnosis, ...)` —— 画像 + 头号问题 + 根因 + 训练建议
2. **进步讲解** `generate_progress_narration(trend, comparison, ...)` —— 趋势 + 多基准对比 + 下阶段重点
3. **计划讲解** `generate_plan_narration(plan, ...)` —— 交错编排 / 退步 / 保持 / 休息的解释

### 1.2 不做什么

- **不让 LLM 做诊断推理**。诊断走 `advice.advise()`（规则引擎，确定性，阈值来自 `docs/aim-kinematics-research.md`）。Agent 拿到的是已经定型的 `CoachDiagnosis`。
- **不让 LLM 编造指标数值**。所有数值（decel_frac=0.75、SPARC=-6.2 等）来自规则引擎传入的 payload。
- **不让 LLM 自由生成"知识"**。知识 tool 只返回预备好的文档片段（切片+元数据），LLM 引用而不重写。
- **不引入 pi 或任何 agent 框架作为运行时依赖**。`providers.py` 的设计注释提到"borrows pi's provider-skeleton design"，意思是借鉴思路；运行时仍是 anthropic / openai SDK 直连。本设计沿用同一原则：**借鉴 pi 的 agent loop + tool_use block 思路，但用原生 SDK 实现**。
- **不做多轮对话 / 用户回问**。本次只做"诊断→讲解"的单向生成，agent 的多步只发生在模型内部的 tool use loop，不和用户对话。
- **不动 `advice.py` / `diagnosis.py` / `profiles.py` / `planning.py`**（这些是规则层，agent 在它们之上加 narrate 层）。
- **不替换 `providers.py`**（继续用作 LLM 客户端工厂；agent 在它之上加 tool use loop）。
- **不做向量嵌入 / RAG**（见 §4，最简方案是结构化 dict 索引）。

### 1.3 与现有代码的关系

| 模块 | 状态 |
|---|---|
| `advice.py` | **不动**。Ground truth 规则引擎。 |
| `coach/diagnosis.py` | **不动**。`CoachDiagnosis` 数据契约。 |
| `coach/profiles.py` | **不动**。画像 + 三层根因映射。 |
| `coach/knowledge.py` | **保留**。其 `KNOWLEDGE` dict 是 signal→{community,cues} 的核心索引，作为 `fetch_knowledge` tool 的数据源之一。 |
| `coach/planning.py` | **不动**。`TrainingPlan` 数据契约。 |
| `coach/providers.py` | **不动**。继续提供 `LLMBackend`，但需补一个 tool-use capable 的客户端（见 §7）。 |
| `coach/report.py` | **改**。`build_report` / `build_progress_report` 把 narration 调用从 `narrator.generate_*` 换成 `agent.narrate_*`。 |
| `coach/narrator.py` | **删**。三个 `generate_*` 函数被 agent 取代；`build_system_prompt` / `build_user_prompt` 等 prompt 拼装逻辑迁移到 agent 模块（其中 user prompt 序列化逻辑可基本复用）。 |

---

## 2. 架构总览

### 2.1 分层

```
┌─────────────────────────────────────────────────────────────┐
│ 规则层（确定性，不动）                                       │
│   advice.advise() → list[Finding]                          │
│   diagnosis.build_diagnosis() → CoachDiagnosis             │
│   planning.build_plan() → TrainingPlan                     │
│   progress.build_trend/comparison() → 趋势 + 对比          │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼ （结构化数据 payload，已定型）
┌─────────────────────────────────────────────────────────────┐
│ Agent 层（新，tool use loop）                                │
│   入口: narrate_diagnosis / narrate_progress / narrate_plan │
│   loop:                                                     │
│     1. 组装初始 messages（system + user payload）           │
│     2. call LLM with tools 定义                             │
│     3. 若 stop_reason == "tool_use" → 执行 tool calls       │
│        → 把 tool_result 塞回 messages → 回到 2              │
│     4. 若 stop_reason == "end_turn" → 返回最终文本          │
│   防护: max_turns=8, token budget, timeout                  │
└─────────────────────────────────────────────────────────────┘
                            │   ▲
              tool calls    │   │ tool results
                            ▼   │
┌─────────────────────────────────────────────────────────────┐
│ Tool 层（确定性 Python 函数，返回预备好的片段）              │
│   get_diagnosis() / get_meta()                              │
│   fetch_knowledge(signal)   ← coach/knowledge.py KNOWLEDGE  │
│   fetch_kinematics(topic)   ← docs/aim-kinematics-research  │
│   fetch_coaching_theory(topic) ← docs/coach-theory          │
│   fetch_prescription(topic) ← docs/coach-prescription       │
│   fetch_community_example(topic) ← docs/coach-community +   │
│                                     youtube doc             │
│   list_signals() / list_topics()                            │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 关键约束：advice 怎么作 ground truth

- **诊断 payload 在 user message 里一次性给全**。Agent 入口前，序列化 `CoachDiagnosis` 成 JSON（基本复用 `narrator.build_user_prompt` 的逻辑），LLM 看到的就是"诊断已定，请基于此讲解"。
- **诊断不可被 LLM 修改**。Agent 没有"改诊断"tool，没有"算指标"tool。
- **knowledge tool 只补背景**。LLM 调 `fetch_prescription(topic="sparc_low")` 拿到处方手册的 SPARC 章节，是为了"讲解时引用 Wulf 外部焦点 / Fradet Type 2/3 子动作"，不是为了让它自己判定 SPARC 是不是低——判定已经由 advice 做完了。
- **铁律落到 system prompt + tool 描述里**（见 §6）。

### 2.3 为什么不直接把所有文档塞 system prompt

当前 narrator 的 `build_system_prompt` 把触发的 signal 对应 `KNOWLEDGE` 拼进 system prompt——这是合理的，因为 `KNOWLEDGE` 只有 12 条且每条很短。但扩到 5 份文档（每份 200-500 行 markdown）后：

- 全量塞 system prompt：token 爆炸（粗估 30k+ tokens），且大部分内容对本次诊断无关
- 按 signal 切片塞：每份文档对同一 signal 的描述分散在多个章节，切片逻辑要手写且易漏

Agent 模式的好处：让模型自己看 payload 里有哪些 signal，自己决定去翻哪些章节。**等于把"signal → 相关文档章节"的检索逻辑从硬编码（容易漏）改为模型驱动（更全更灵活）**。

---

## 3. Tool 列表

所有 tool 都是确定性 Python 函数，返回**预备好的片段**（不返回 LLM 生成的文本）。命名空间前缀统一 `coach_`，避免和将来其他 agent 混淆。

### 3.1 数据型 tool（返回本次诊断的子结构，方便 LLM 不漏字段）

#### `coach_get_diagnosis`

- **用途**：取本次诊断的完整 JSON payload（agent 已在 user message 里见过，但 tool 提供结构化回查，避免模型遗漏字段）。
- **入参**：无（绑定本次会话的诊断 context）
- **出参 schema**：
  ```json
  {
    "profile": {"archetype_id": "...", "label": "...", "confidence": 0.82, "secondary_tags": [...]},
    "issues": [
      {"priority": 1, "signal": "...", "severity": "...",
       "root_causes": [{"level": "symptom|physical|training", "text": "..."}],
       "prescriptions": [{"scenario": "...", "reason": "..."}]}
    ],
    "comparison": [{"metric": "...", "self": ..., "ref": ..., "verdict": "..."}],
    "meta": {...}
  }
  ```
- **来源**：`CoachDiagnosis` dataclass（`coach/diagnosis.py`）

#### `coach_get_meta`

- **用途**：取本次分析的 meta 信息（cm_per_360、fps、参考玩家标签、录制时间等）
- **入参**：无
- **出参**：`{"cm_per_360": ..., "fps": ..., "reference_label": "...", "recorded_at": "..."}`
- **来源**：`diagnosis.meta`

#### `coach_list_signals` / `coach_list_topics`

- **用途**：让 LLM 知道有哪些可用的 signal key / topic key（避免它瞎猜 key 调 tool 失败）
- **入参**：无
- **出参**：`{"signals": ["sparc low", "decel_frac high", ...], "topics": {"prescription": [...], "theory": [...], ...}}`
- **来源**：`KNOWLEDGE.keys()` + 文档索引（见 §4）

### 3.2 知识型 tool（按 signal 或 topic 取文档片段）

每个 tool 入参统一形态：`signal` 或 `topic`（字符串 key）。

#### `coach_fetch_knowledge(signal: str)`

- **用途**：取某个 signal 对应的"社区归因 + 可操作提示"。这是当前 `KNOWLEDGE[signal]` 的内容，agent 第一轮几乎必调（替代 narrator 旧版的预加载）。
- **出参 schema**：
  ```json
  {"signal": "sparc low",
   "community": "MattyOW/Viscose 张力预算...",
   "cues": ["暴露疗法...", "侧向挤压...", "..."]}
  ```
- **来源**：`coach/knowledge.py` 的 `KNOWLEDGE` dict
- **契约**：`signal` 必须是 `advice.advise()` 输出的 `Finding.signal` 之一（见 §6 防幻觉铁律）

#### `coach_fetch_kinematics(topic: str)`

- **用途**：取运动学理论的某章节片段。`topic` 取值：`"thresholds"`（指标健康区间+阈值，advice.py 知识底座）、`"min_jerk_vs_uniform_decel"`（linearity vs SPARC 区分）、`"sparc"`（SPARC 频域理论）、`"submovement"`（Type 1/2/3 + overlap）、`"fitts"`（throughput 跨距离归一化）、`"sensitivity"`（cm/360 决策框架）、`"scenarios"`（Voltaic 场景处方库）
- **出参**：`{"topic": "...", "content": "...", "source_ref": "docs/aim-kinematics-research.md §X"}`
- **来源**：`docs/aim-kinematics-research.md` 按章节切片（见 §4）

#### `coach_fetch_prescription(topic: str)`

- **用途**：取处方手册章节。`topic` 取值：`"external_focus"`（外部焦点）、`"submovement_types"`（Type 1/2/3 分支处方）、`"sparc_low"`（SPARC 低处方）、`"reverse_ratio"`（reverse 高 + 时长归一化陷阱）、`"interleaving"`（交错编排）、`"meta_cognition"`（元认知对抗）
- **出参**：`{"topic": "...", "content": "...", "source_level": "academic_peer_reviewed", "source_ref": "docs/coach-prescription-manual.md §X"}`
- **来源**：`docs/coach-prescription-manual.md` 切片

#### `coach_fetch_coaching_theory(topic: str)`

- **用途**：取教练理论章节。`topic` 取值：`"fitts_posner"`（三阶段 + Taylor&Ivry 双过程）、`"deliberate_practice"`（Ericsson 训练量上限）、`"contextual_interference"`（交错 vs 块状）、`"kr_kp"`（KR/KP 反馈分类）、`"guidance_hypothesis"`（反馈过频损害学习）、`"socratic"`（苏格拉底式）
- **出参**：`{"topic": "...", "content": "...", "source_level": "academic_peer_reviewed", "source_ref": "docs/coach-theory-foundation.md §X"}`
- **来源**：`docs/coach-theory-foundation.md` 切片

#### `coach_fetch_community_example(topic: str)`

- **用途**：取社区前沿 + YouTube 创作者经验片段。`topic` 取值：`"voltaic_s5"`（S5 分类）、`"static_clicking_three_step"`（big flick→micro→confirm 三步）、`"bardoz_method"`（bardOZ 三段重构 + underflick）、`"tension_management"`（MattyOW 张力预算）、`"vod_review"`（复盘方法论）
- **出参**：`{"topic": "...", "content": "...", "source_level": "community_consensus | personal_experience_unverified", "source_ref": "..."}`
- **来源**：`docs/coach-community-frontier.md` + `youtube doc/YouTube 瞄准训练内容综合.md`

> **关键**：所有 `coach_fetch_*` 出参的 `content` 字段是**文档原文切片**（带原始 markdown），不是 LLM 生成。`source_level` 字段强制带信源等级（academic_peer_reviewed / community_consensus / personal_experience_unverified），让 LLM 在引用时能区分措辞强度。

### 3.3 Tool 列表汇总

| Tool | 数据源 | 防幻觉性质 |
|---|---|---|
| `coach_get_diagnosis` | 本次 `CoachDiagnosis` | ground truth 直读 |
| `coach_get_meta` | `diagnosis.meta` | ground truth 直读 |
| `coach_list_signals` / `coach_list_topics` | `KNOWLEDGE.keys()` + 文档索引 | 静态 key 列表 |
| `coach_fetch_knowledge` | `KNOWLEDGE` dict | 预备好的 community+cues |
| `coach_fetch_kinematics` | `aim-kinematics-research.md` 切片 | 预备好的文档片段 |
| `coach_fetch_prescription` | `coach-prescription-manual.md` 切片 | 预备好的文档片段 |
| `coach_fetch_coaching_theory` | `coach-theory-foundation.md` 切片 | 预备好的文档片段 |
| `coach_fetch_community_example` | `coach-community-frontier.md` + YouTube 综合切片 | 预备好的文档片段 |

---

## 4. 知识库索引方案

### 4.1 推荐：最简可行 = 模块级 Python dict 索引 + 静态切片

不引入 SQLite FTS / 向量嵌入 / 任何外部检索系统。直接在 `kovaak_tracker/coach/agent_kb.py`（新模块）里维护：

```python
# 形态示意（非最终实现）
KINEMATICS_TOPICS = {
    "thresholds": {
        "content": "...aim-kinematics-research.md §2 表格的 markdown...",
        "source_ref": "aim-kinematics-research.md §2",
        "source_level": "academic_peer_reviewed",
    },
    "min_jerk_vs_uniform_decel": {...},
    "sparc": {...},
    ...
}
PRESCRIPTION_TOPICS = {...}
THEORY_TOPICS = {...}
COMMUNITY_TOPICS = {...}  # 跨两份文档
```

切片方式：**人工切片**（不写自动 splitter）。每份文档人工挑出 5-8 个 topic，每个 topic 对应一段 200-600 字的 markdown 片段，直接以 Python 字符串字面量形式存进 `agent_kb.py`。

#### 为什么不用 SQLite FTS / 向量

| 方案 | 优点 | 缺点 | 何时升级 |
|---|---|---|---|
| **Python dict（推荐）** | 零依赖、可读、可 diff review、改文档=改源码 | 切片要手写；扩到 50+ topic 后难维护 | 当 topic 数 >30 或要全库检索时 |
| SQLite FTS | 全文检索、跨文档查 | 需要预处理器把 markdown 灌进 DB；运行时多一层 I/O；测试难 | 当出现"模糊关键词检索"需求时（如用户输入"我的手腕累"→找相关片段） |
| 向量嵌入 | 语义检索 | 引入 embedding 依赖（openai/本地模型）、向量存储、不可解释（为什么这条命中？） | **不推荐**。本项目知识库规模小且结构化（每段都有清晰的 signal/topic 标签），向量检索是杀鸡用牛刀，且不可解释性破坏防幻觉铁律 |

#### 切片约定

- 每个 topic 片段控制在 200-600 字（防 token 膨胀）
- 每段必须带 `source_ref`（精确到文档 §章节）+ `source_level`（信源等级）
- 每个 signal 至少能在 `KNOWLEDGE`（核心索引）+ `prescription`（处方手册）找到两条对应片段——LLM 不调用也至少有最低保障
- `agent_kb.py` 是**只写不读**的源码模块，文档改了改源码（和现在 `knowledge.py` 是同一模式）

### 4.2 切片工作量预估

5 份文档 × 平均 6 个 topic/份 = ~30 个切片。每切片人工挑片段 + 写元数据 ≈ 5-10 分钟，总 3-5 小时。可作为 Phase 2 实现的独立子任务。

---

## 5. Agent loop 伪代码

Python 风格伪代码（**不是最终实现**，只是结构示意）：

```python
# kovaak_tracker/coach/agent.py（新模块）

def narrate_diagnosis(diagnosis: CoachDiagnosis, backend: ToolUseBackend,
                      *, max_turns: int = 8) -> str:
    """Agent 入口：诊断 → 中文教练讲解。"""
    system = _build_diagnosis_system_prompt()
    user_payload = _serialize_diagnosis(diagnosis)  # 复用 narrator.build_user_prompt 逻辑
    messages = [
        {"role": "user", "content": user_payload},
    ]
    tools = _build_tool_definitions([
        coach_get_diagnosis_binder(diagnosis),
        coach_get_meta_binder(diagnosis.meta),
        coach_list_signals_binder(),
        coach_list_topics_binder(),
        coach_fetch_knowledge,
        coach_fetch_kinematics,
        coach_fetch_prescription,
        coach_fetch_coaching_theory,
        coach_fetch_community_example,
    ])

    for _ in range(max_turns):
        resp = backend.messages_create(
            system=system, messages=messages, tools=tools, max_tokens=2048,
        )
        # 处理 tool_use blocks
        if resp.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": resp.content})
            tool_results = []
            for block in resp.content:
                if block.type == "tool_use":
                    result = _dispatch_tool(block.name, block.input, tools)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })
            messages.append({"role": "user", "content": tool_results})
            continue
        # end_turn / stop / max_tokens
        if resp.stop_reason == "end_turn":
            return _extract_text(resp.content)
        # 其他 stop_reason（max_tokens / stop_sequence）→ 截断保护
        return _extract_text(resp.content) + "\n[讲解因长度限制被截断]"

    return "[讲解超出最大轮次，未生成]"


def _dispatch_tool(name: str, input_: dict, tools: list) -> dict:
    """根据 tool name 调对应 Python 函数。tools 是已绑定了 context 的 callable 列表。"""
    fn = {t["name"]: t["fn"] for t in tools}[name]
    return fn(**input_)


def _build_diagnosis_system_prompt() -> str:
    """替代 narrator.BASE_SYSTEM_PROMPT。铁律 + 讲解规则 + tool 使用指引。"""
    return """你是一位 KovaaK's flicking 教练...
    [讲解规则：同 narrator 旧版]
    [防幻觉铁律：见 §6]

    【tool 使用指引】
    你收到的 user message 是结构化诊断 payload（已定型，不可修改）。
    讲解前请用 tool calls 按需检索知识：
    - 对每个 priority=1 的 issue：调 coach_fetch_knowledge(signal) 拿社区归因
    - 想引用理论支撑：调 coach_fetch_kinematics / coach_fetch_prescription / coach_fetch_coaching_theory
    - 想给具体案例：调 coach_fetch_community_example
    不要把所有 tool 都调一遍——只调你讲解需要的。
    调完 tool 后，基于诊断 payload + tool 返回的片段，写一段中文讲解（150-400 字）。
    """
```

#### 关键设计点

- **`backend.messages_create` 是新的 tool-use capable backend**（见 §7），不是现有 `LLMBackend.generate`（那个是单次 text-in text-out）
- **tool 函数绑定 context**：`coach_get_diagnosis` 需要本次 `diagnosis` 引用，用 closure / functools.partial 绑定（不让 LLM 把诊断当入参，避免它篡改）
- **max_turns = 8** 是兜底——典型流程是 1-2 轮 tool use 就够（拿 knowledge + 拿 prescription）
- **三入口共用 loop，不同 system prompt + 不同 tool 子集**：
  - `narrate_diagnosis`：全 tool 集
  - `narrate_progress`：去掉 `coach_get_diagnosis`（progress 不消费 diagnosis），加 `coach_get_trend`（取趋势数据）+ `coach_get_comparison`
  - `narrate_plan`：去掉 `coach_get_diagnosis`，加 `coach_get_plan`，重点 tool 是 `coach_fetch_coaching_theory("contextual_interference")` / `("deliberate_practice")` / `("guidance_hypothesis")`

---

## 6. 防幻觉铁律

### 6.1 三层守卫

**第一层：诊断走规则引擎**
- `CoachDiagnosis` 由 `advice.advise()` + `diagnosis.build_diagnosis()` 产生，纯 Python 规则
- Agent 没有"改诊断 / 算指标 / 设阈值"的 tool
- LLM 在 system prompt 里被明确告知："诊断 payload 是 ground truth，你的任务是讲解，不是重新判定"

**第二层：LLM 只描述 / 推理 / 引用，不生成数值**
- 所有具体数值（decel_frac=0.75、SPARC=-6.2、cm/360=23.5）必须来自 payload 或 tool 返回的文档片段
- system prompt 铁律："禁止编造任何未在 payload 或 tool 返回中出现过的数值；数据缺失就略过该诊断点"
- 这一条直接继承自 `narrator.BASE_SYSTEM_PROMPT` 的"铁律"，已经过实战验证

**第三层：知识 tool 返回预备片段**
- 所有 `coach_fetch_*` 返回的是文档原文切片（带 `source_ref`），不是 LLM 生成的 paraphrase
- LLM 可以引用、改写措辞，但**不能编造文档没说的因果链**
- 例：`fetch_prescription("submovement_types")` 返回 Fradet 2008 Type 1/2/3 描述，LLM 只能说"按 Fradet 等人的分类，Type 2/3 子动作与精度相关"，不能自己加"所以你应该练 Multiclick 4 小时"（4 小时这个数字必须来自 deliberate_practice 章节才合法）

### 6.2 tool result 失败模式

- **未知 signal key**：tool 返回 `{"error": "unknown signal", "valid_keys": [...]}`，LLM 看到 valid_keys 后重试或放弃
- **未知 topic key**：同上，返回 valid topics 列表
- **tool 超时 / 异常**：返回 `{"error": "tool unavailable"}`，agent loop 继续走（LLM 在没有该片段的情况下讲解，质量降级但不崩）

### 6.3 显式拒绝"自由生成"模式

- 没有 `coach_generate_knowledge` 或 `coach_synthesize_theory` 这类让 LLM 自由生成的 tool
- 所有知识都以"文档原文 + 元数据"形态进入 LLM 视野
- 这是和"全 RAG/向量检索"流派的核心差异：**我们的检索是结构化 key→切片，不是"相似度→LLM 重写"**

### 6.4 Review 时检查清单（给点点）

实现完成后，跑一组回归测试：
1. 给 agent 一个固定 `CoachDiagnosis`，看输出是否引用了 payload 里的数值（不是编造的）
2. 给 agent 一个 SPARC 缺失的 payload，看讲解是否略过 SPARC（不是自己写"SPARC 偏低"）
3. mock 一个 `coach_fetch_kinematics("sparc")` 报错的 backend，看 agent 是否降级（不崩）
4. 检查 agent 是否在 max_turns 内收敛（不无限调 tool）

---

## 7. narrator.py 替换方案

### 7.1 留什么 / 删什么 / 加什么

| 文件 | 处理 |
|---|---|
| `coach/narrator.py` | **删整个文件**。三个 `generate_*` 函数被 agent 取代；`build_system_prompt` / `build_user_prompt` 的序列化逻辑迁移到新 `agent.py`；`PROGRESS_SYSTEM_PROMPT` / `PLAN_SYSTEM_PROMPT` 文本作为 agent 的 system prompt 起点重写（加 tool 使用指引段落）。（**实现修正**：narrator.py 未删，保留作 manual fallback——运行时不被 agent 调用，见 PROGRESS.md 2026-07-05 续二。） |
| `coach/providers.py` | **保留**。但需要**扩展**：现有 `LLMBackend.generate(system, user)` 不支持 tool use，需新增一个 tool-use capable backend 接口（建议新增 `class ToolUseBackend(Protocol)` 或直接给 `AnthropicBackend` / `OpenAICompatBackend` 加一个 `messages_create` 方法）。 |
| `coach/diagnosis.py` | **不动**。 |
| `coach/knowledge.py` | **保留**。`KNOWLEDGE` dict 作为 `coach_fetch_knowledge` tool 的数据源。 |
| `coach/report.py` | **改最小**。把 `from .narrator import ...` 换成 `from .agent import narrate_diagnosis / narrate_progress / narrate_plan`，调用签名保持兼容（`diagnosis, backend` / `trend, comparison, backend` / `plan, backend`）。 |
| `coach/agent.py` | **新建**。Agent loop + tool 定义 + dispatch + 入口函数。 |
| `coach/agent_kb.py` | **新建**。5 份文档的切片索引（Python dict）。 |

### 7.2 providers.py 的扩展（最小改动）

现有 `LLMBackend` Protocol 只要求 `generate(system, user) -> str`，narrator 全靠这个。agent 需要 tool use，不能复用 `generate`。建议：

- **保留** `LLMBackend.generate`（向后兼容，规则引擎相关测试可能还在用）
- **新增** `ToolUseBackend` Protocol（或 abstract method）：

```python
class ToolUseBackend(Protocol):
    def messages_create(self, *, system: str, messages: list,
                        tools: list[dict], max_tokens: int) -> ToolUseResponse: ...
```

`AnthropicBackend` / `OpenAICompatBackend` 各自实现 `messages_create`（封装原生 SDK 的 tool use API）。`ToolUseResponse` 是个简单 dataclass：`{content: list[block], stop_reason: str}`，屏蔽两家 SDK 的细节差异。

> **未决**（见 §8）：DeepSeek 是否原生支持 tool use？支持的话直接用 OpenAICompat 路径；不支持的话可能要 fallback 到单次生成（性能降级到当前 narrator 水平）。

### 7.3 入口签名兼容

为最小化对 `report.py` 的扰动，三个 agent 入口函数签名建议保持和现有 narrator 完全一致：

```python
# 旧
def generate_narration(diagnosis: CoachDiagnosis, backend: LLMBackend) -> str: ...
def generate_progress_narration(trend, comparison, backend) -> str: ...
def generate_plan_narration(plan: TrainingPlan, backend) -> str: ...

# 新
def narrate_diagnosis(diagnosis: CoachDiagnosis, backend: ToolUseBackend) -> str: ...
def narrate_progress(trend, comparison, backend: ToolUseBackend) -> str: ...
def narrate_plan(plan: TrainingPlan, backend: ToolUseBackend) -> str: ...
```

`report.py` 只改 import + 函数名，调用形态不变。backend 类型注解从 `LLMBackend` 改 `ToolUseBackend`。

### 7.4 迁移步骤（实现时按此顺序）

1. 新建 `coach/agent_kb.py`（5 份文档切片，先粗糙后细化）
2. 在 `providers.py` 加 `ToolUseBackend` Protocol + 给 `AnthropicBackend` / `OpenAICompatBackend` 实现 `messages_create`
3. 新建 `coach/agent.py`，实现 `narrate_diagnosis`（单次分析讲解为第一目标）
4. 跑通端到端：mock backend + 真实 `CoachDiagnosis`，验证 loop 收敛
5. 改 `report.py` 的 import（diagnosis 入口切换）
6. 删 `narrator.py`
7. 在 `agent.py` 加 `narrate_progress` / `narrate_plan`，改 `report.py` 另两处 import
8. 文档切片细化（每份文档补全 topic）
9. 回归测试

---

## 8. 未决问题（需要点点决定）

1. **LLM 选型**：Anthropic Claude（Sonnet/Haiku）还是 DeepSeek？
   - Claude 原生 tool use，文档成熟，但成本高
   - DeepSeek 国产便宜，但 tool use 支持需确认（DeepSeek-V3 支持 function calling，但稳定性待验证）
   - 推荐：默认 Claude（providers.py 已有 AnthropicBackend），DeepSeek 作为可选 backend，跑通后比较

2. **进度讲解 / 计划讲解是否也 agent 化**？
   - 本设计建议是（统一三个入口）。但 `generate_progress_narration` 当前是静态 system prompt，转 agent 收益相对小（progress 不消费 diagnosis 那种 signal-rich payload）
   - 备选：只把 `narrate_diagnosis` agent 化，progress/plan 保留单次 LLM
   - **推荐**：三个都 agent 化，保持代码形态统一，但 Phase 2 实现可以分两步（先 diagnosis 跑通，再迁 progress/plan）

3. **切片粒度**：每个 topic 200-600 字是否合适？太细 LLM 拿不到完整 context，太粗等于又回到全量预加载。建议先按"原文一节一切"试跑，再根据 agent 实际调用效果调

4. **topic 命名约定**：用 snake_case（`"sparc_low"`）还是和 signal 完全对齐（`"sparc low"` 空格）？
   - 当前 signal 用空格（`"sparc low"`），代码里更自然
   - topic 跨多文档，snake_case 更清晰
   - **推荐**：signal 保持空格，topic 用 snake_case，两者不强求一致

5. **agent_kb.py 的切片来源校验**：手动切片容易和原文漂移（文档改了忘改切片）。要不要加一个 CI 检查"切片 hash 必须能在原文找到"？
   - 增加复杂度，但能防漂移
   - **推荐**：先不加，等切片稳定后（Phase 3）再考虑

6. **max_turns / token budget 的具体值**：建议默认 `max_turns=8, max_tokens=2048`，但需要真实跑几个 case 调

7. **失败降级策略**：如果 agent loop 异常 / 超时 / 拿不到合法 tool result，是返回空字符串、返回降级文案（"讲解暂不可用"），还是 fallback 到单次 LLM 生成（不用 tool）？
   - **推荐**：返回降级文案 + 在 `report.notes` 里记原因，不 fallback（fallback 会让 agent 路径变成"两套实现"，维护负担）

8. **是否需要"agent 决策可观测性"**？比如记录每次讲解调了哪些 tool、各 tool 返回多少 token，方便 debug？
   - 推荐：Phase 1 加一个简单的 `agent_trace` list（每次 tool call 入栈），Phase 2 视情况暴露到 `report.notes` 或日志

9. **报告字段是否要新增"引用源列表"**？现在 `CoachReport.notes` 是字符串 list，agent 引用了哪些 source_ref 可能让用户更信任
   - **推荐**：Phase 2 加 `report.citations: list[SourceRef]`，从 agent_trace 提取

---

## 9. 实现拆解（Phase 2 子任务）

按依赖顺序：

### Phase 2.1 基础设施（半天）
- [ ] 在 `providers.py` 加 `ToolUseBackend` Protocol + `ToolUseResponse` dataclass
- [ ] 给 `AnthropicBackend` 实现 `messages_create`（封装 `client.messages.create(tools=...)`）
- [ ] 写一个 mock `ToolUseBackend` 用于测试（不调真实 API）
- [ ] 单测：mock backend + 假 tool，验证 loop 在 1-2 轮 tool use 后能 end_turn

### Phase 2.2 知识库切片（3-5 小时）
- [ ] 新建 `kovaak_tracker/coach/agent_kb.py`
- [ ] 对 5 份文档各挑 5-8 个 topic，切片 + 写元数据（`source_ref` + `source_level`）
- [ ] 定义四个 dict：`KINEMATICS_TOPICS` / `PRESCRIPTION_TOPICS` / `THEORY_TOPICS` / `COMMUNITY_TOPICS`
- [ ] 单测：每个 topic 都能通过 key 取到非空 content

### Phase 2.3 Tool 定义 + dispatch（半天）
- [ ] 在 `agent.py` 定义 8-10 个 tool 的 schema（JSON schema 形态，符合 Anthropic tool use 规范）
- [ ] 实现 `_dispatch_tool(name, input, bound_tools)` 路由
- [ ] 实现 context 绑定（`coach_get_diagnosis_binder(diagnosis)` 等闭包工厂）
- [ ] 单测：每个 tool 单独调通

### Phase 2.4 narrate_diagnosis 跑通（半天）
- [ ] 实现 `_build_diagnosis_system_prompt`（迁移 + 扩展 narrator.BASE_SYSTEM_PROMPT，加 tool 使用指引）
- [ ] 实现 `_serialize_diagnosis`（复用 `narrator.build_user_prompt` 逻辑）
- [ ] 实现 `narrate_diagnosis(diagnosis, backend)` 主体 loop
- [ ] 集成测试：真实 fair summary → advice → diagnosis → agent（用 mock backend）→ 输出非空中文

### Phase 2.5 切换 report.py（半小时）
- [ ] `from .agent import narrate_diagnosis`
- [ ] `build_report` 里的 narration 调用改成 `narrate_diagnosis(diagnosis, backend)`
- [ ] 跑现有 coach 相关测试（应该全过，因为 narration 是 best-effort）

### Phase 2.6 删 narrator.py（5 分钟）
- [ ] 确认没有其他地方 import narrator
- [ ] 删文件（用 recycle bin，不用 rm）
- [ ] 跑全测试

### Phase 2.7 progress / plan 入口（1-2 小时）
- [ ] 实现 `narrate_progress(trend, comparison, backend)`（system prompt 复用 narrator.PROGRESS_SYSTEM_PROMPT 文本 + 加 tool 指引）
- [ ] 实现 `narrate_plan(plan, backend)`
- [ ] 改 `report.py` 另两处 import

### Phase 2.8 真实 backend 跑（看点点有没有 API key）
- [ ] 配置 `providers.json` 加 anthropic key
- [ ] 跑一个真实视频 → 看输出讲解质量
- [ ] 调 system prompt（铁律、tool 指引、字数控制）

### Phase 2.9 文档（不算 Phase 2，但建议）
- [ ] 更新 `CLAUDE.md` 项目 instructions 的 coach 子系统描述（从"narrator 单次 LLM"改为"agent tool use"）
- [ ] 在 `coach/agent.py` 顶部写模块 docstring（防幻觉铁律 + 三入口说明）

---

## 附录 A：与现有 narrator 的对照

| 维度 | 旧 narrator.py | 新 agent.py |
|---|---|---|
| LLM 调用 | 单次 `generate(system, user)` | 多轮 `messages_create(system, messages, tools)` |
| 知识加载 | 预拼装 system prompt（`build_system_prompt` 把 KNOWLEDGE 全塞进去） | 按需 tool call（LLM 决定调哪些） |
| 知识来源 | 只有 `KNOWLEDGE` dict（12 条） | `KNOWLEDGE` + 5 份文档切片 |
| 防幻觉 | system prompt 铁律 | 同 + tool 只返回预备片段 |
| 扩展性 | 加新文档要写新的预拼装逻辑 | 加新文档只需扩 `agent_kb.py` dict |
| 测试 | mock `LLMBackend.generate` 返回固定文本 | mock `ToolUseBackend.messages_create` 按 tool_use block 路由 |
| 失败模式 | 抛异常被 `report.py` try/except 吞掉 → `notes.append("讲解不可用")` | 同（agent loop 异常也走 best-effort） |

---

## 附录 B：pi 借鉴点（不引入 pi）

本机 `C:\Users\袜子\Desktop\pi\` 不存在（环境扫描时 Glob 超时未找到 packages）。但 `providers.py` 注释里写了"borrows pi's provider-skeleton design"，说明之前的项目实践里借鉴过 pi 的：

- **provider 抽象**：按 API 协议（anthropic-messages / openai-completions）分类，配置驱动，credential 从 env 取
- **agent loop**：messages 列表 + tool_use block + tool_result 回填
- **tool 定义 schema**：JSON schema 入参 + 描述

本设计沿用这些思路，**不引入 pi 作为运行时依赖**（只用 anthropic / openai SDK 原生 tool use API）。如果后续点点想要更深借鉴 pi 的 skill 组织方式（prompt 模板 + 工具组合的 skill 抽象），可在 Phase 3 评估——本次不做。
