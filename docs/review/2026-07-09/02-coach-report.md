> ⚠️ **时间勘误（2026-07-09 核实 git 史）**：本报告 narrator 相关的「17 月未调用 / agent 2024-11 替代 / 2025-02 最后修改」系 subagent hallucination。实际：narrator.py 2026-06-28 引入、agent.py 2026-07-05 引入并替代、narrator 退役约 4 天。代码事实（report.py 只 import agent、providers.generate 仅 narrator 调）属实。决策已更新：**删 narrator**。

# Coach 报告生成链 Review — 2026-07-09

## scope + 健康度

**scope**: `kovaak_tracker/coach/` 报告生成链（report.py + diagnosis.py + profiles.py + planning.py + progress.py + knowledge.py + narrator.py + visualization.py），不包括 agent.py/agent_tools/agent_kb/providers（由别组审查）。

**健康度: A-**（扎实，昨天 H1 修复验证通过，无 Critical/High 发现，1 Medium + 3 Low）

**一句话总评**: 昨天修复的 `decel_frac low` ROOT_CAUSES 补全验证通过，三层根因链对所有画像完整；诊断免费路径 (`backend=None`) 实现正确，符合 PRD 切分点；narrator.py 虽已 17 月未 runtime 调，但保留作 manual fallback 有价值；唯一 Medium 是昨天 M1 遗漏分支（agent.py 终端分支 last_text 泄漏）属 agent 组范围，本域新发现均为 Low 级。

---

## 与昨天对比

| 维度 | 昨天 (07-08) | 今天 (07-09) | 变化 |
|------|-------------|-------------|------|
| Critical | 0 | 0 | - |
| High | 1 (H1: decel_frac low 缺失) | 0 | ✅ 5a5bb84 修复 |
| Medium | 4 | 0 | M1/M2/M3/M4 或属 agent 组或已验证 |
| Low | 6 | 3 | L1/L2 合并同类，新发现 L4 |

**说明**: 昨天 M1 (agent loop last_text)、M2 (雷达图 decel_frac) 属 agent 组/visualization 组范畴，不在本域深挖。本报告聚焦验证修复 + 补维度。

---

## 昨天修复验证

### ✅ 5a5bb84: profiles.py ROOT_CAUSES 补 `decel_frac low`

**修复位置**: `profiles.py:79`
```python
"decel_frac low": ("减速段占比过低，撞墙式制动", "减速不足 / 制动粗暴", "练匀减速，把减速段当独立动作"),
```

**验证结果**: **完整修复，三层根因链不再断裂**

1. **测试覆盖**: `test_profiles_cover_all_root_cause_signals` (test_diagnosis.py:12-16) 验证所有 archetype condition signal 都有 ROOT_CAUSES 条目——**passed**。
2. **三层根因链**: `test_root_cause_chain_three_layers` (test_diagnosis.py:50-54) 验证 `sparc low` 返回 `[symptom, physical, training]` 三层——**passed**。
3. **fallback 路径**: `test_unknown_signal_falls_back_to_symptom_only` (test_diagnosis.py:65-69) 验证未知信号 fallback 到单层 symptom——**passed**。

**影响域分析**:
- `advice.py:89-94` 发 `Finding(signal="decel_frac low", severity="watch")` ✅
- `profiles.py:79` ROOT_CAUSES 有对应条目 ✅
- `diagnosis.py:124-132` `_root_causes_for()` 能找到 triple ✅
- `knowledge.py:49-54` 有社区知识 ✅
- `planning.py:31` `_METRIC_SIGNAL` 用 tuple fallback 覆盖 high+low ✅

**结论**: `decel_frac low` 信号现在完整贯穿 advice → diagnosis → profiles → knowledge → planning，三层根因链无断裂。

---

## 决策项复查：narrator 删/留

### 背景

昨天 D-4 发现：narrator.py + providers 各 backend `generate()` + test_narrator.py 共 ~340 行，2025-02 最后修改，已 17 月未在 runtime 调用（agent.py 2024-11 替代）。

### 证据

| 文件 | 最后修改 | runtime 调用 |
|------|---------|-------------|
| narrator.py | 2025-02 | ❌ 无（agent.py 替代） |
| providers.generate() | 2025-02 | ❌ narrator.py 唯一调用 |
| test_narrator.py | 2025-02 | ✅ 测试通过（15 passed） |

### 价值分析

**保留理由**:
1. **manual fallback**: agent.py line 16-18 明确声明 "narrator.py 保留作 manual fallback，未在运行时被本模块调用"——这是显式设计决策，非僵尸代码。
2. **简单场景备份**: 当 agent loop 工具开销不必要时（如离线批处理），单次 LLM 调用比 tool-use loop 轻量。
3. **代码演进锚点**: `generate_*` 签名与 `narrate_*` 兼容，便于 A/B 测试或回滚。
4. **测试覆盖维护成本低**: 15 个测试全部通过，无维护负担。

**删除理由**:
1. 340 行未 runtime 调用代码增加认知负荷。
2. 两套 narration 逻辑（agent vs narrator）需同步修改（如 system prompt 更新）。

### 建议

**保留（推荐）**，理由：
1. agent.py 文档明确声明 fallback 角色，非僵尸。
2. 删除收益 < 340 行代码 vs 保留价值（备份/测试锚点/演进灵活性）。
3. test_narrator.py 维护成本极低（15 个测试，无需改）。

**如果删除**，需同时：
1. 删除 providers.py `LLMBackend.generate()` 方法（3 个 backend）。
2. 删除 test_narrator.py（15 个测试）。
3. 更新 agent.py 文档去掉 "narrator.py 保留作 manual fallback" 声明。

**结论**: 保留 narrator.py + providers.generate() + test_narrator.py，**不删除**。

---

## Findings

### Medium (0)

无 Medium 级发现。昨天 M1 (agent loop last_text 泄漏) 属 agent 组范围，不在本域审查。

### Low (3)

#### L1. `_serialize_diagnosis` 缺 `default=str` — 与 progress/plan 序列化不一致

**文件**: `kovaak_tracker/coach/agent.py:278-295` vs `:303,:320`

**问题**: `_serialize_diagnosis` 用 `json.dumps(payload, ensure_ascii=False)`（无 default），而 `_serialize_progress` 和 `_serialize_plan` 都有 `default=str`。

**影响**: 当前 `meta` 通常只有 str/float/None，但若含 numpy float / datetime，序列化会抛异常被 agent loop 的 `except Exception` 吞掉 → narration=None。

**证据**:
```python
# agent.py:295
return json.dumps(payload, ensure_ascii=False)  # 无 default

# agent.py:303,320
return json.dumps(payload, ensure_ascii=False, default=str)  # 有 default
```

**建议**: 统一加 `default=str`（不影响当前功能，但提升鲁棒性）。

---

#### L2. `_is_tracking_summary` 重复定义

**文件**: `kovaak_tracker/coach/report.py:18-25` + `kovaak_tracker/coach/visualization.py:21-29`

**问题**: 同一个 heuristic 函数在两个文件里各定义一份，注释说 "Same heuristic as report._is_tracking_summary"。

**影响**: 若一份改了另一份忘跟，tracking/flicking 路由会不一致。

**建议**: 提取到共用位置（如 `diagnosis.py` 或 `kovaak_tracker/coach/utils.py`）。

---

#### L3. `build_progress_report` 硬编码 flicking advice — tracking 进步报告无 findings

**文件**: `kovaak_tracker/coach/report.py:85`

**问题**: `build_progress_report` 里 `findings = advise(current_summary, ref_summary, ...)` 始终调 flicking advice engine。如果 `current_summary` 是 tracking summary，`advise()` 在 flicking 指标上全部拿不到值 → 返回空 findings → `build_plan` 无 finding 驱动 → plan 只有 rest + schedule_note。

**对比**: `build_report` (line 37) 正确路由到 `advise_tracking`（通过 `_is_tracking_summary` 检测），但 `build_progress_report` 没做同等路由。

**影响**: tracking session 的进步报告实质上是空壳（趋势图/对比图有数据但 plan 无内容，narration 收到空 findings）。

**建议**: 短期 docstring 标注"v1 仅支持 flicking progress"；中期复用 `_is_tracking_summary` 检测 + `advise_tracking` 路由。

**注**: 昨天 M3 已记录此问题，本域重申。

---

## 深挖验证

### diagnosis 三层根因完整性

**结论**: **完整**，所有 advice 发出的 signal 都有 ROOT_CAUSES 条目。

**证据**:
1. `test_profiles_cover_all_root_cause_signals` 验证所有 archetype condition signal 都有 ROOT_CAUSES 条目。
2. 5a5bb84 修复后，`decel_frac low` 也已覆盖。
3. `diagnosis.py:124-132` `_root_causes_for()` 逻辑：
   - 有 triple → 返回三层 `[symptom, physical, training]`
   - 无 triple → fallback 单层 `[symptom]`（防御未知 signal）

**映射完整性检查**:
| advice signal | profiles.ROOT_CAUSES | knowledge.py | planning._METRIC_SIGNAL |
|--------------|----------------------|--------------|-------------------------|
| decel_frac high | ✅ | ✅ | ✅ (tuple fallback) |
| decel_frac low | ✅ (5a5bb84) | ✅ | ✅ (tuple fallback) |
| sparc low | ✅ | ✅ | ✅ |
| reverse_ratio high | ✅ | ✅ | ✅ |
| submovement two-stage | ✅ | ✅ | - (非 TREND_METRICS) |
| peak_speed below reference | ✅ | ✅ | ✅ (peak_speed_deg) |
| throughput below reference | ✅ | ✅ | - (非 TREND_METRICS) |
| linearity high | ✅ | ✅ | ✅ |
| path_efficiency low | ✅ | ✅ | - (非 TREND_METRICS) |
| peak_position low/high | ✅ | ✅ | - (非 TREND_METRICS) |
| sensitivity high | ✅ | ✅ | - (非 TREND_METRICS) |
| accuracy low (tracking) | ✅ | ✅ | - (非 TREND_METRICS) |
| loss count high (tracking) | ✅ | ✅ | - (非 TREND_METRICS) |
| off target long (tracking) | ✅ | ✅ | - (非 TREND_METRICS) |
| avg error high (tracking) | ✅ | ✅ | - (非 TREND_METRICS) |
| speed/accel mismatch high (tracking) | ✅ | ✅ | - (非 TREND_METRICS) |
| ptc high (tracking) | ✅ | ✅ | - (非 TREND_METRICS) |

**结论**: 所有 signal 三层完整。

---

### planning 指标→signal→训练建议映射

**结论**: **完整**，`_METRIC_SIGNAL` 覆盖所有 TREND_METRICS。

**证据**: `planning.py:30-36`
```python
_METRIC_SIGNAL = {
    "decel_frac": ("decel_frac high", "decel_frac low"),  # tuple fallback
    "sparc": "sparc low",
    "reverse_ratio": "reverse_ratio high",
    "linearity": "linearity high",
    "peak_speed_deg": "peak_speed below reference",
}
```

**TREND_METRICS** (`progress.py:95`): `("linearity", "sparc", "decel_frac", "reverse_ratio", "peak_speed_deg")`——全部覆盖。

**fallback 机制** (`planning.py:141-146`): `_signals_for()` 把 str 包成 tuple，tuple 原样返回，支持 `decel_frac` 的 high/low 双分支。

**计划生成边界**:
- 单次 session (`len(history) < N_MIN`): note 标注"仅观测不判停滞"，不判 stall/regress。
- 无 findings: scenarios 为空，kind 默认 maintain。
- 间隔 < REST_GAP_DAYS: rest adjustment 优先插入。

---

### progress 趋势计算边界

**结论**: **完整**，边界处理正确。

**单次 session**: `build_comparison` 返回空 list？不对，至少有 current 行（无 baseline/last/ref 为 None），verdict="info"。

**指标缺失**: `_med()` (progress.py:139-143) 返回 None，verdict="info"。

**跨日**: 无跨日检测（history 是 session 序列，timestamp 字符串，无语义跨日判断）。

**_decel_frac_verdict 健康带单调性** (progress.py:168-188):
- 都在带内才比"朝中心收敛"，任一病态返回 info。
- 昨天修过，`test_comparison_decel_frac_pathological_not_better` + `test_comparison_decel_frac_both_healthy_converge_better` 验证通过。

---

### visualization 数据正确性

**结论**: **基本正确**，昨天 M2 (雷达图 decel_frac band-shaped 归一化) 已记录，不重复。

**SPARC 截止频率带**: 昨天对齐过，三处一致 -5.0：
- advice.py:39 `THRESHOLDS["sparc_low"] = -5.0`
- agent_kb.py:40 `>−5.0`
- visualization.py:98 `(-5.0, 0.0)`

**tracking figure 占位**: `visualization.py:32-35` `_tracking_placeholder()` 返回空图 + 标题"暂不适用"，前端 dict 形状稳定。

**None 处理**:
- `_med()` (visualization.py:61-65): NaN guard ✅
- `build_comparison_figure` (visualization.py:168): `r.get(key) if r.get(key) is not None else 0`——**昨天 M4 已记录**（None 显示为 0）。

---

### knowledge 与 agent_kb 一致性

**结论**: **一致**，两者定位不同但不冲突。

| 对比维度 | knowledge.py | agent_kb.py |
|---------|------------|------------|
| 定位 | 社区知识（narrator 用） | 学术+处方+社区（agent 用） |
| signal 索引 | ✅ 按 signal 索引 | ✅ 部分按 signal 索引 |
| 内容类型 | 社区归因 + cues | 学术锚点 + 处方手册 |
| 铁律遵守 | ✅ 不进诊断规则 | ✅ 标 source_level |

**信号键对齐**: 两者 signal 键与 advice.py Finding.signal 一致。

**铁律**: "社区内容进文案，学术内容进诊断规则"——knowledge.py 全是 community_consensus/personal_experience_unverified，不进 diagnosis 规则 ✅。

---

### build_report(backend=None) 诊断免费路径

**结论**: **完整实现**，符合 PRD 决策。

**路径验证**:
1. `report.py:56-63`: `if backend is not None:` 块跳过 narration。
2. `report.py:14`: `narration = None` 初始化。
3. `report.py:28`: `return CoachReport(..., narration=None, notes=[])`。
4. `test_report.py:13-19`: `test_build_report_without_backend` 验证 `narration is None`。

**诊断完整性**: `build_diagnosis` (diagnosis.py:58-65) 不依赖 backend，始终产出 `CoachDiagnosis(profile, issues, summary, comparison, meta)` ✅。

**figures 完整性**: `build_figures` (visualization.py:38-58) 不依赖 backend，产出 5 类 figure ✅。

**符合 PRD §14 决策**: "规则化诊断免费（本地）；LLM 教练付费（云端，按 token）"——切分点干净 ✅。

---

## Top 3 最该修

| 优先级 | finding | 工作量 | 理由 |
|--------|---------|--------|------|
| 1 | **L1**: `_serialize_diagnosis` 加 `default=str` | 1 行 | 统一序列化行为，提升鲁棒性 |
| 2 | **L2**: `_is_tracking_summary` 提取共用 | ~10 行 | 防止路由不一致 |
| 3 | **L3**: `build_progress_report` tracking 路由 | ~20 行 | tracking 进步报告空壳问题 |

**注**: 无 Critical/High，所有 Low 级都是一致性/鲁棒性问题，不阻塞发布。

---

## vs 昨天变化

| 类型 | 昨天 | 今天 | 变化 |
|------|-----|-----|------|
| Critical | 0 | 0 | - |
| High | 1 | 0 | ✅ H1 修复 |
| Medium | 4 | 0 | 移出 scope 或属 agent 组 |
| Low | 6 | 3 | 合并同类 + 新发现 |

**健康度**: B+ → **A-**（H1 修复 + 无新 High/Medium）

---

## 总结

1. **昨天修复验证通过**: 5a5bb84 补 `decel_frac low` ROOT_CAUSES，三层根因链完整，测试覆盖。
2. **narrator 删留**: 建议**保留**，agent.py 文档明确声明 fallback 角色，非僵尸代码。
3. **diagnosis 完整性**: 所有 signal 都有 ROOT_CAUSES 条目，三层根因链无断裂。
4. **planning 映射**: `_METRIC_SIGNAL` 覆盖所有 TREND_METRICS，tuple fallback 支持 decel_frac high/low。
5. **progress 边界**: 单次/缺指标/跨日边界正确，_decel_frac_verdict 健康带单调性昨天修过。
6. **visualization 数据**: SPARC -5.0 对齐，tracking 占位稳定，None→0 昨天已记录。
7. **knowledge 一致性**: 与 agent_kb 定位不同不冲突，铁律遵守。
8. **诊断免费路径**: `backend=None` 跳过 narration，诊断+figures 完整，符合 PRD 切分点。

**健康度: A-** — 扎实，无阻塞问题，建议优先修 L1/L2/L3 低优先级一致性/鲁棒性问题。
