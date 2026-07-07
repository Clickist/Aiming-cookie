# Coach 子包 Review — 2026-07-08

## scope + 健康度

**scope**: `kovaak_tracker/coach/` 全部（agent / agent_tools / agent_kb / providers / diagnosis / report / visualization / profiles / knowledge / narrator / planning / progress）+ `kovaak_tracker/advice.py` + `advice_tracking.py`。

**健康度: B+**（扎实，07-07 修复全部验证通过，1 个 High 新发现 + 4 Medium + 6 Low，无 Critical）。

**一句话总评**: 架构清晰（确定性规则引擎 + LLM 仅翻译/检索）、防幻觉铁律贯穿到底、agent loop 健壮性在 07-07 修复后已达标；主要短板是 `decel_frac low` 信号在 ROOT_CAUSES 表缺失导致三层根因链断裂，以及若干可视化/序列化的边界处理粗糙。

---

## Findings

### Critical (0)

无。07-07 修复了所有 Critical 级问题（provider messages_create 补全 / agent tool_calls 单条件 / max_turns 半截 preamble），本轮验证全部完整。

### High (1)

#### H1. `profiles.ROOT_CAUSES` 缺 `decel_frac low` — 三层根因链断裂

**文件**: `kovaak_tracker/coach/profiles.py:77-97`
**关联**: `kovaak_tracker/coach/diagnosis.py:124-132` (`_root_causes_for`)

**问题**: `advice.advise()` 在 `decel_frac < 0.40` 时发 `Finding(signal="decel_frac low", severity="watch")`（advice.py:91）。但 `profiles.ROOT_CAUSES` 只有 `"decel_frac high"`（line 78），没有 `"decel_frac low"`。`diagnosis._root_causes_for()` 找不到 triple 时 fallback 到单层 `[RootCause("symptom", finding.diagnosis)]`——只给症状层，不给物理层 + 训练层。

**影响**: 当玩家减速段占比过低（刹车太急/撞墙式制动）时，诊断 issue 只有一层 root cause 而非三层。agent narration 收到的 payload 也只有 symptom 层，讲解缺少物理归因和训练方向。其他所有信号（包括 `decel_frac high`、`sparc low` 等）都有完整三层，唯独 `decel_frac low` 退化。

**佐证**: `knowledge.py:49` 有 `"decel_frac low"` 条目（社区知识存在），`planning.py:31` `_METRIC_SIGNAL` 用 tuple fallback 覆盖了 high+low（07-07 修过），说明这个信号在各处都被预期存在，唯独 ROOT_CAUSES 漏了。

**建议**: 在 `profiles.ROOT_CAUSES` 补一行：
```python
"decel_frac low": ("减速段占比过低", "制动不足/撞墙式急停", "匀减速着陆练习"),
```

---

### Medium (4)

#### M1. agent loop 终端分支 `last_text` 跨轮泄漏

**文件**: `kovaak_tracker/coach/agent.py:225,229,240`

**问题**: `last_text` 在 tool-call 分支被赋值为 `resp.content_text or last_text`（line 225），在终端分支也是 `resp.content_text or last_text`（line 229）。如果：
1. turn 0：模型调 tool + 产 preamble "让我查一下..." → `last_text = "让我查一下..."`
2. turn 1：模型不调 tool、content_text 为空、`stop_reason="end_turn"` → `last_text = "" or "让我查一下..."` = `"让我查一下..."`
3. 返回 `narration = last_text or None` = `"让我查一下..."`

这把前导词当最终讲解返回——正是 07-07 在 `max_turns_exceeded` 路径修过的同类问题，但终端分支没覆盖到。

**影响**: 实际罕见（多数 backend 终端响应总有内容），但一旦触发，用户看到半截前导词当最终讲解。

**建议**: 终端分支不用 `last_text`，直接用 `resp.content_text or None`：
```python
# line 229
last_text = resp.content_text or last_text  # 删此行或改逻辑
# line 240
return {
    "narration": resp.content_text or None,  # 不用 last_text
    ...
}
```
`last_text` 仅供 `max_turns_exceeded` 路径参考（该路径已正确返回 None），终端路径应只看当前响应。

#### M2. 雷达图 `_normalize` 对 band-shaped 指标（decel_frac）归一化错误

**文件**: `kovaak_tracker/coach/visualization.py:92-104`，配合 `_RADAR_DIMS`（line 12-18）

**问题**: `_RADAR_DIMS` 里 `decel_frac` 标 `inverted=True`（lower-is-better），`_normalize` 用 band `(0.50, 0.65)` 做线性映射 + 翻转。但 `decel_frac` 是**band-shaped**（健康 [0.40, 0.65]，中心 ~0.525 最优，两端都差），不是单调的 lower-better。

归一化结果：
- `decel_frac=0.50`（理想下界）→ t=0 → inv → **1.0（best）** ✓
- `decel_frac=0.65`（上界阈值）→ t=1 → inv → **0.0（worst）** ✓
- `decel_frac=0.30`（病态：刹车太急）→ t=-1.33 → clamp 0 → inv → **1.0（best!!）** ✗
- `decel_frac=0.80`（病态：减速拖沓）→ t=2.0 → clamp 1 → inv → **0.0（worst）** ✓（碰巧对）

**影响**: decel_frac 低于 0.50 的玩家（包括 0.30 的严重刹车太急），雷达图显示为"最好"（满格），与诊断结果（advice 发 watch finding）矛盾。雷达是用户最先看的可视化之一，误导性强。

**对比**: advice.py 的 `compare_table` 把 decel_frac 放进 `_NO_VERDICT`（info），progress._decel_frac_verdict 正确实现了健康带单调判定——07-07 修过。visualization 雷达是遗漏点。

**建议**: 雷达对 decel_frac 用带状归一化（距健康带中心的距离 → 分数），或退而求其次标 `decel_frac` 为 `info` 维度（不显示在雷达上，在 issue_list 里展示）。

#### M3. `build_progress_report` 硬编码 flicking advice — tracking 进步报告无 findings

**文件**: `kovaak_tracker/coach/report.py:76-116`，具体 line 85

**问题**: `build_progress_report` 里 `findings = advise(current_summary, ref_summary, ...)`（line 85）始终调 flicking advice engine。如果 `current_summary` 是 tracking summary（`metrics.json` 的 `{tension:{...}, loss:{...}}` 形），`advise()` 在 flicking 指标（linearity/sparc/decel_frac 等）上全部拿不到值 → 返回空 findings → `build_plan` 无 finding 驱动 → plan 只有 rest + schedule_note，无 metric-based 调整。

**影响**: tracking session 的进步报告实质上是空壳（趋势图/对比图有数据但 plan 无内容，narration 收到空 findings）。`build_report`（单次报告）正确路由到 `advise_tracking`（line 37），但 `build_progress_report` 没做同等路由。

**判断**: 按 spec tracking v1 是 self-only（comparison=None），progress 可能在 v2 scope。但 `build_progress_report` 的签名 `current_summary` 没有限制类型，webapp 若对 tracking session 调此函数会静默产出空 plan。建议至少加 `summary_type` 路由或 docstring 标注限制。

**建议**: 短期：docstring 标注"v1 仅支持 flicking progress"；中期：复用 `build_report` 的 `_is_tracking_summary` 检测 + `advise_tracking` 路由。

#### M4. `build_comparison_figure` 把 None 当 0 显示

**文件**: `kovaak_tracker/coach/visualization.py:158-171`

**问题**: 
```python
ys = [r.get(key) if r.get(key) is not None else 0 for r in comparison]
```
当 baseline/last/ref 为 None（无数据）时，柱状图显示为 0。用户无法区分"该值真的是 0"和"没有数据"。

**影响**: 首次分析（history 空 → baseline=None）或无参考（ref=None）时，图上显示一堆 0 高度的柱子，可能被误读为"指标值为 0"。

**建议**: None 用 `float('nan')`（Plotly 会留空/不绘制），或直接在 caption 标注"无数据项不显示"。

---

### Low (6)

#### L1. `_serialize_diagnosis` 缺 `default=str` — 与 progress/plan 序列化不一致

**文件**: `kovaak_tracker/coach/agent.py:295` vs `:303,:320`

`_serialize_diagnosis` 用 `json.dumps(payload, ensure_ascii=False)`（无 default），`_serialize_progress` 和 `_serialize_plan` 都有 `default=str`。如果 diagnosis payload 含非 JSON 原生类型（numpy float / datetime），diagnosis 序列化会抛异常被 agent loop 的 `except Exception` 吞掉 → narration=None。progress/plan 路径有 `default=str` 保护不会。

当前 `meta` 通常只有 str/float/None，但这是潜在的脆弱性。建议统一加 `default=str`。

#### L2. `_is_tracking_summary` 重复定义

**文件**: `kovaak_tracker/coach/report.py:18-25` + `kovaak_tracker/coach/visualization.py:21-29`

同一个 heuristic 函数在两个文件里各定义一份，注释说"Same heuristic as report._is_tracking_summary"。如果一份改了另一份忘跟，路由会不一致。建议提取到共用位置（如 `diagnosis.py` 或新 utils）。

#### L3. `_call_with_retry` 对不可恢复异常也重试

**文件**: `kovaak_tracker/coach/providers.py:236-251`

只排除了 4xx（`status_code` 在 400-499），其他所有异常都重试。SDK 序列化错误（`ValidationError`）、认证错误（某些 SDK 表现为非 4xx 的 `AuthenticationError`）等不可恢复异常也会浪费 2 次重试 + ~4.5s。影响小（用户多等几秒），但可改善：额外排除 `openai.AuthenticationError` / `anthropic.AuthenticationError` 等。

#### L4. `_to_jsonable` + `_diag_payload` 在 agent.py 和 agent_tools.py 双份

**文件**: `kovaak_tracker/coach/agent.py:268-295` vs `kovaak_tracker/coach/agent_tools.py:177-204`

两个文件各有一份功能相同的 `_to_jsonable` + diagnosis payload 序列化逻辑。不影响正确性，但改一处忘另一处会产生不一致。建议 agent.py 直接复用 agent_tools.py 的 `_diag_payload`。

#### L5. `chat_with_coach` 诊断 payload 同时在 system prompt + tool 里

**文件**: `kovaak_tracker/coach/agent.py:396-399,409`

`chat_with_coach` 把 `_serialize_diagnosis(diagnosis)` 拼进 system prompt，同时 `build_diagnosis_tools(diagnosis)` 包含 `coach_get_diagnosis` tool（返回同一 payload）。LLM 可以从两处拿到相同数据。冗余但不影响正确性，浪费一些 context tokens（diagnosis payload 通常 ~1-3KB）。

#### L6. `providers.json` deepseek base_url 无 `/v1` 后缀

**文件**: `kovaak_tracker/coach/providers.json:13` vs `providers.py:358`

providers.json 里 `"base_url": "https://api.deepseek.com"`，而 `DeepSeekBackend` 默认值和 `load_backend` fallback 是 `"https://api.deepseek.com/v1"`。DeepSeek API 两个路径都支持（已验证），所以功能不受影响，但配置和代码默认值不一致容易混淆。建议统一（推荐 providers.json 加 `/v1`，与 openai entry 对齐）。

---

## Top 3 最该修

| 优先级 | finding | 工作量 | 理由 |
|--------|---------|--------|------|
| 1 | **H1**: ROOT_CAUSES 补 `decel_frac low` | 1 行 | 唯一 High：三层根因链断裂影响诊断质量，修复极简 |
| 2 | **M1**: agent loop 终端分支 last_text 泄漏 | ~5 行 | 07-07 同类问题的遗漏分支；罕见但一旦触发用户看到半截前导词 |
| 3 | **M2**: 雷达图 decel_frac band-shaped 归一化 | ~10 行 | 用户最先看的可视化，病态值显示为"最好"误导性强 |

---

## 07-07 修复验证

| 修复项 | 验证结果 | 证据 |
|--------|---------|------|
| agent.py tool_calls 双条件→单条件 | **✅ 完整** | `agent.py:196` `if resp.tool_calls:` 单条件，无 stop_reason 双条件；注释（:192-195）说明为何 |
| throughput 接通（detect_targets→target_width_deg） | **✅ 完整** | advice.py:176-187 `"throughput below reference"` finding 使用 `self_tp / ref_tp`；agent_kb fitts chunk（:88-98）引用 detect_targets |
| decel_frac 带状化（progress._decel_frac_verdict + compare_table _NO_VERDICT） | **✅ 完整** | `progress.py:168-188` 健康带单调判定；`advice.py:204-211` `_NO_VERDICT` 含 decel_frac；planning.py:31 tuple fallback 覆盖 high+low |
| SPARC 阈值 -0.5→-5.0 对齐 | **✅ 完整** | advice.py:39 `-5.0`；agent_kb.py:40 `>−5.0`；visualization.py:98 `(-5.0, 0.0)`——三处一致 |
| provider backends 补 messages_create | **✅ 完整** | AnthropicBackend（:112-182）、OpenAICompatBackend（:200-211）、DeepSeekBackend（:373-385）三者齐备；共享 `_openai_compat_messages_create`（:254-339） |
| KB SPARC 阈值对齐 | **✅ 完整** | agent_kb.py:68 `advice 阈值：sparc < −5.0`；与 advice.py 一致 |
| 删 BY_SIGNAL 死索引 | **✅ 完整** | 全 kovaak_tracker/ 搜索 `BY_SIGNAL` 零匹配；agent_kb.py 仅保留 `BY_TOPIC`（:599） |
| max_turns/max_tokens 返回半截 preamble | **✅ 基本完整** | max_turns 路径（:246-252）narration=None ✅；max_tokens 路径（:231-238）narration=None ✅。**但终端分支（:229-240）有残留泄漏**（见 M1） |

**结论**: 07-07 修复 7/7 完整（含测试覆盖）。唯一残留是 M1（终端分支 last_text 泄漏），是 max_turns preamble 修复的遗漏分支——同一 `last_text` 变量在终端路径没做同等处理。不阻塞，但应跟 H1 一起补。
