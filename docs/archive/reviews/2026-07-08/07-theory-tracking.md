# Tracking 理论债 + 声称一致性 review

> 日期：2026-07-08 · reviewer：theory-tracking reviewer（只读）
> scope：tracking 理论债（PTC / speed_mismatch / accel_mismatch 实现 vs 声称）+ 跨文档一致性
> 核心文件：`kovaak_tracker/analysis.py` / `kovaak_tracker/tracking.py` / `kovaak_tracker/advice_tracking.py` / `kovaak_tracker/coach/knowledge.py` / `kovaak_tracker/coach/agent_kb.py` + 声称文档（CLAUDE.md / PRD / README / product-strategy / tracking-coach spec / prescription-manual / theory-foundation）

---

## 总评（先写结论）

**Tracking 理论诚实度：中上，但存在一处关键的跨文档矛盾需要解决。**

- 主线 docs（CLAUDE.md / PRD / README / product-strategy）对 PTC 命名误导、J/E+TBR 已弃的表态**一致且诚实**，没有粉饰。
- J/E Ratio / TBR 的清理是**成功案例**——代码无残留实现，文档无残留声称（所有提及都是在"已弃/前一稿误判"语境下）。
- **但**：CLAUDE.md 和 tracking-coach spec §1.1-1.3 在 speed_mismatch / accel_mismatch 是否"只描述目标运动"上有**直接矛盾**——spec 显式把 CLAUDE.md 的判断标为"错"。审了实现后**CLAUDE.md 是对的，spec §1.1-1.3 错**（基于对常数列求导的数学误解）。
- advice_tracking.py L155 的 Finding 文案诚实承认"v_rel 含准星噪声，主导项是目标速度"——这**与 CLAUDE.md 一致、与 spec §1.1 矛盾**。代码层已经做对了，spec 层的辩护站不住。

| 维度 | 评价 |
|---|---|
| PTC 命名误导的承认 | 诚实（5 份主流文档一致修正） |
| J/E+TBR 残留清理 | 干净（代码 0 残留；文档仅历史语境提及） |
| speed_mismatch 理论债 | **矛盾**（CLAUDE.md 与 spec §1 直接对立；spec 错） |
| 跨文档声称一致性 | 中（1 处关键矛盾 + 1 处歧义） |
| 是否阻塞 v1 | 否（所有债都明确标"v2 重构"，advice_tracking thresholds = None 已守住） |

---

## 理论债清单

### #1 PTC 命名误导（Pure Tension Coeff ≠ 张力测量）

**公式**（`analysis.py` L121）：`ptc = mean(a_rel | miss) / max(mean(error_px | miss), 1.0)`

**核对**：
- `a_rel = ‖a_cx − a_tx, a_cy − a_ty‖`（L75）——屏幕空间中准星与目标的相对加速度
- `error_px = ‖bx − cx, by − cy‖`（L61）——准星到目标的屏幕距离
- miss_mask 限定只在脱靶帧聚合（L108, L117）
- **实际意义**：miss-frame 上的"每单位空间误差所承载的相对加速度"——是屏幕空间运动密度量，**不是肌肉张力测量**

**量纲推导**：
- `a_rel` 量纲：px/s²（position 的二阶时间导数）
- `error_px` 量纲：px
- `ptc` = (px/s²) / px = **1/s² = s⁻²**
- CLAUDE.md 说"量纲上 Hz² 成立"——**dimensionally 对**（Hz = 1/s，Hz² = 1/s²），但**语义上不规范**：Hz 是周期频率的单位，PTC 不是频率的平方。严格写法是 s⁻² 或 1/s²。code L177 直接打印"Hz²"，spec §2.1 也说"是否叫 Hz² 是命名选择"——技术正确但习惯怪。
- **严重度**：Trivial（纯命名 pedantry）

**"Pure Tension Coeff"是否仍误导**：
- 仍然误导。PTC 作为名字暗示"测张力"，但实际测的是加速度密度。"张力"是生物力学量，需要 EMG / 手部摄像头验证（肌肉激活模式、握力、微颤），不是屏幕空间加速度误差比能直接推断的。
- 代码里仍然使用此名：`analysis.py` L138 `"ptc"` key / L177 `"Pure Tension Coeff (PTC) ... Hz²"` 打印 / export `"ptc"` (L156)
- **文档修正状态**：5 份主流文档**一致修正**
  - CLAUDE.md「理论状态」：明示"Pure Tension Coeff是误导命名"
  - PRD §2：明示"不成立——PTC 实为 miss-frame 加速度-误差密度"
  - README L77：明示"命名误导"
  - product-strategy.md L96：明示"已修正"
  - tracking-coach spec §2.1：明示"修辞命名——不直接测肌肉张力"
- **代码修正状态**：未改（变量名、key、打印标签都保留）——这是**明确的 v2 债**，不是遗漏

**严重度**：Medium（命名债，但被充分标记；底层算法仍然产有用数字）  
**阻塞 v1**：否（CLAUDE.md 明示"待 v2 重构处理，不阻塞 v1"；advice_tracking L170-177 用 severity="info" + "假设" 措辞守住）

---

### #2 speed_mismatch / accel_mismatch + cross_pos 硬编码（**关键矛盾**）

#### 2.1 实现事实（ground truth）

`tracking.py` L57：
```python
cross_pos = (metadata.width // 2, metadata.height // 2)  # 初始化为画面中心
```

L72-78：仅当用户配置了 crosshair HSV bounds 时才检测并更新：
```python
if crosshair_hsv_lo is not None and crosshair_hsv_hi is not None:
    cross_result, _, _ = detect_crosshair_by_color(...)
    if cross_result is not None:
        cross_pos = cross_result
```

L130-131：每帧把 `cross_pos` 当前值写入 CSV：
```python
"cross_x": cross_pos[0],
"cross_y": cross_pos[1],
```

**两种运行模式**：
- **默认模式**（无 HSV crosshair，典型情况）：`cross_pos` 永远不被重新赋值 → CSV 的 `cross_x/cross_y` 列是**字面常量**（每行都是 `width//2, height//2`）
- **HSV 模式**：`cross_pos` 按帧检测更新，但 KovaaK's 里准星**本来就锁屏中**，所以检测到的位置变化只是 CV 噪声（±几像素），不是真实运动

#### 2.2 analysis.py 怎么处理这列

`extract_kinematics` L52-53 读 `cross_x/cross_y`，L69-72 平滑 + 求导：

```python
cx = apply_smoothing(group["cross_x"].values, window_size)  # Savitzky-Golay
v_cx = calc_derivative(cx, fps)  # np.gradient
a_cx = calc_derivative(v_cx, fps)
```

**数学事实**：
- Savitzky-Golay 是线性滤波器；常数输入 → 输出同一常数（edge-padding + 多项式拟合常数 = 常数本身）
- `np.gradient` 中心差分：相邻值相等 → 差为 0 → 严格返回 **0**
- **默认模式下 `v_cx = v_cy = a_cx = a_cy = 0` 严格成立**（不是近似，是精确零）

因此：
- `v_rel = ‖v_cx − v_tx, v_cy − v_ty‖ = ‖0 − v_tx, 0 − v_ty‖ = ‖v_tx, v_ty‖ = speed_t`
- `speed_mismatch = mean(v_rel | miss) = mean(speed_t | miss)` —— **完全等于 miss 段目标屏幕速度**
- HSV 模式下 v_c 是检测噪声级非零，**主导项仍是目标速度**

**结论**：CLAUDE.md "speed_mismatch/accel_mismatch 实际只描述目标运动，不描述玩家追踪误差" 的判断在默认模式下**字面正确**。

#### 2.3 跨文档矛盾

| 文档 | 立场 | 对错 |
|---|---|---|
| CLAUDE.md L21, L117 | "cross_pos 硬编码画面中心 → v_c=0 → 只描述目标运动，不描述玩家追踪误差" | ✅ 对（默认模式字面成立） |
| PRD §2 | （沿用 CLAUDE.md 框架，未独立断言） | ✅ 对 |
| tracking-coach spec §1.1 | 把 CLAUDE.md 这条判断标为"**错**" | ❌ 错（基于数学误解） |
| tracking-coach spec §1.3 | 论证"v_c 通常 ≠ 0" | ❌ 错（见下） |
| advice_tracking.py L155 | Finding 文案："v_rel 含准星噪声，主导项是目标速度" | ✅ 对（与 CLAUDE.md 一致） |

**spec §1.3 的论证错在哪**：

> CSV 中 `cross_x/y` 是逐帧采样（不是常数标量），**所以 `v_c` 通常 ≠ 0**

这是**逻辑无效**：每帧采样 ≠ 每帧值变化。CSV 有 N 行 `cross_x`（per-frame sampled），但默认模式下每行的值都是 `width//2`（同一个常量）——列形状是 N×1 但 rank=1。求导严格得 0。

> 前一稿"中心是常数"判断对 raw 数据成立，但对经过 Savitzky-Golay 平滑 + 数值微分的导数是噪声级非零

这是**数学错误**：Savitzky-Golay 是线性 filter，常量输入 → 同常量输出；np.gradient 对常量数组严格返回 0。不存在"平滑/微分引入的非零噪声"——这两步都不向常量信号注入噪声。

#### 2.4 实际影响

- spec §1.3 的辩护站不住，但它的**实践结论**（"保留 speed_mismatch 不改名，作为 info/watch 级 signal"）是可辩护的——metric 本身仍然有信息（"你在目标屏幕速度 X 时 miss"），只是不能直接读作"玩家追踪误差"。
- advice_tracking.py 已经正确处理：threshold = None（永不触发）+ 文案诚实标注"主导项是目标速度"。
- **真正的问题**是 spec §1 把 CLAUDE.md 标错——这会让未来 reviewer / 维护者困惑哪份文档才权威。

**严重度**：High（跨文档直接矛盾 + spec 基于数学误解给出错误判断）  
**阻塞 v1**：否（实现层 advice_tracking 已经做对；thresholds = None 守住不触发）

---

### #3 J/E Ratio / TBR —— **确认无残留**（清理成功）

#### 3.1 代码搜索结果

| 搜索词 | 代码命中 | 结论 |
|---|---|---|
| `TBR` / `tension_balance` / `TensionBalance` / `tension.balance` | 0 | 无实现 |
| `J/E` / `J\/E` / `jitter_error` / `JitterError` / `jitter.error` | 0 | 无实现 |
| `1.8` / `0.6`（TBR 阈值）| 0（文档命中均为 SPARC 阈值 -5.0、cm/360 区间等无关数字） | 无凭空阈值 |

**结论**：仓库内**没有 J/E Ratio 或 TBR 的任何代码实现、变量、key、函数、类**。CLAUDE.md 声称"J/E Ratio 在代码里没有独立实现，TBR 没有可计算定义"——**字面属实**。

#### 3.2 文档搜索结果（残留声称扫描）

所有文档提及 J/E+TBR 都是在**历史 / 否认语境**：

| 文档 | 提及方式 | 是否残留声称 |
|---|---|---|
| CLAUDE.md L20, L118, L129 | "已确认不成立"、"凭空"、"本文件理论状态段的事实来源" | 否（明确否认） |
| README L77 | "J/E Ratio / TBR ... 已确认不成立"、"没有独立实现"、"凭空" | 否（明确否认） |
| PRD §2 | "PTC... 后经审视确认不成立" | 否（明确否认） |
| product-strategy.md L96 | "已修正——PTC 实为 miss-frame 加速度-误差密度" | 否（明确否认） |
| product-strategy.md L109 | "PTC/J-E/TBR 已弃" | 见 #4（歧义） |
| tracking-coach spec §1.1 | "前一稿把它们和算法层混着判，结论虽然丢弃它们没错" | 否（历史语境） |
| tracking-coach spec §7.2 | "TBR 阈值无公式无校准——TBR 凭空" | 否（明确否认） |
| tracking-coach spec §8.4 | "J/E Ratio / TBR 是核心理论... 与代码不符——是已记录的债" + "已解决" | 否（已结案记录） |
| PROGRESS.md L585 | "TBR 措辞清理（agent_kb + 处方手册 + youtube 综合）" | 否（清理记录） |
| youtube doc L95 | "PTC（miss-frame 加速度-误差密度，**常被误称** J/E Ratio）" | 否（主动纠正误称） |

**结论**：**J/E Ratio / TBR 的清理是完整且成功的**。没有文档仍然把它们当核心理论声称。

**严重度**：None（成功清理）  
**阻塞 v1**：否

---

### #4 product-strategy.md L109 歧义（PTC 是否"已弃"）

**原文**（`docs/product-strategy.md` L109）：
```
├── tracking 分析（早期，待 v1 重构）→ accuracy、loss_count、off_time（PTC/J-E/TBR 已弃）
```

**问题**：括号里"PTC/J-E/TBR 已弃"的字面读法是 PTC 也已弃。

**矛盾**：
- `analysis.py` 仍然计算并 export PTC（L121, L138, L156）
- tracking-coach spec §2.1："v1 处理：保留算法"
- advice_tracking.py 保留 `tracking_ptc_high` threshold（L38，虽为 None）
- knowledge.py 有 `"ptc high"` entry（L126）
- agent_kb.py 有 `tension_budget_tracking` chunk 挂 `ptc high`（L501）

**两种读法**：
- **字面读**：PTC 算法已弃 → 与代码/spec/KB 矛盾
- **慈善读**：意思是"PTC 作为张力测量已弃，J-E/TBR 也已弃" → 与 spec §2.1 一致

字面上是矛盾，作者意图大概率是慈善读（product-strategy L40, L96 都明说 PTC 算法保留只是命名误导）。但措辞应该澄清——避免未来读者误以为 PTC 算法本身要删。

**严重度**：Low（措辞歧义，非实质性矛盾）  
**阻塞 v1**：否

---

### #5 knowledge.py `"ptc high"` community 文案欠对冲

**`kovaak_tracker/coach/knowledge.py` L126-133**：

```python
"ptc high": {
    "community": "张力预算（Viscose）：手部张力是有限预算，超支会震颤并剥夺视觉读取（lockout）...",
    "cues": [
        "暴露疗法：高 sens + 低 FOV 精准追踪放大微颤，逼大脑修正张力分配",
        "侧向挤压鼠标侧面而非向下垂直按压——侧向给纯摩擦力控制",
        "（生物力学假设，未 EMG 验证；作提示性诊断，需结合 SPARC / 反向修正一起读）",
    ],
},
```

**问题**：
- `community` 字段直接陈述"手部张力是有限预算"——**无对冲**。这是 Viscose 的真实社区框架（合理引用），但挂在 `ptc high` signal 下，暗示 PTC 是探测张力预算的工具。
- 仅 `cues` 列表最后一行有"生物力学假设，未 EMG 验证"——容易在 narrator 组装 prompt 时被截或忽略。

**对比 agent_kb.py L500-512**（`tension_budget_tracking` chunk，signal="ptc high"）：
- 开篇即明示："PTC... 把数字翻译成「玩家发力状态」是合理生物力学假设... 但「Pure Tension」是修辞命名，不直接测肌肉张力——需 EMG / 手部摄像头验证"
- 结尾再次重申："（生物力学假设，未 EMG 验证；narrator 措辞用「可能/提示」，不作断言；severity=info）"
- **这个 chunk 对冲充分**

**不对称**：
- knowledge.py 是 narrator 的传统检索源（legacy path）
- agent_kb.py 是 agent 运行时检索源（current runtime）
- 当前运行时走 agent → agent_kb，所以**实际 narrator 输出有对冲**
- 但 knowledge.py 仍可被 narrator 直接检索（保留作 manual fallback），其 community 字段欠对冲是**潜在风险**

**严重度**：Low（运行时路径已对冲；legacy 路径欠对冲）  
**阻塞 v1**：否

---

### #6 advice_tracking.py threshold = None 守护（正面发现）

`kovaak_tracker/advice_tracking.py` L30-39：
```python
THRESHOLDS = {
    ...
    "tracking_speed_mismatch_high": None, # uncalibrated
    "tracking_accel_mismatch_high": None, # uncalibrated
    "tracking_ptc_high": None,            # uncalibrated (biomechanics hypothesis)
}
```

L150-151, L161-162, L170-171：所有三个 signal 都有 `if thresh is not None and ...` 双重守护，**threshold 为 None 时永不触发**。

这是**正确的工程处理**：理论债存疑 → 默认关闭 → 等真实数据校准 → 再开启。即使 PTC 命名误导、speed_mismatch 不描述玩家误差，这些 signal 也不会误诊。

**严重度**：N/A（正面）  
**阻塞 v1**：否

---

## 跨文档不一致清单

### 不一致 #1（**关键**）：CLAUDE.md vs tracking-coach spec §1.1-1.3 关于 speed_mismatch 性质

| 文档位置 | 原话 | 立场 |
|---|---|---|
| `CLAUDE.md` L21 | "speed_mismatch/accel_mismatch 实现 vs 命名不符（cross_pos 硬编码画面中心 → v_c=0 → 这俩实际只描述目标运动，不描述玩家追踪误差）" | 只描述目标运动 |
| `CLAUDE.md` L117 | "导致 tracking 的 PTC / speed_mismatch / accel_mismatch **只描述目标运动**，不描述玩家追踪误差" | 只描述目标运动 |
| `tracking-coach spec` §1.1 表格 | 把"把 speed_mismatch/accel_mismatch/ptc 标为 bug-shaped、实现 vs 命名不符、只描述目标运动，不描述玩家追踪误差"判为"**错**" | 否定 CLAUDE.md |
| `tracking-coach spec` §1.3 | "CSV 中 cross_x/y 是逐帧采样（不是常数标量），所以 v_c 通常 ≠ 0" | 数学错误 |
| `advice_tracking.py` L155 | Finding 文案："v_rel 含准星噪声，**主导项是目标速度**" | 与 CLAUDE.md 一致 |

**裁决**：
- 实现事实（见上 §2.2）：默认模式下 `cross_x/cross_y` CSV 列是字面常量 → Savitzky-Golay 平滑返回同常量 → np.gradient 严格返回 0 → `v_c = 0` 严格成立 → `v_rel = speed_t`
- **CLAUDE.md 是对的**
- spec §1.3 的"v_c ≠ 0"论证基于数学误解（线性 filter 不向常量注入噪声；中心差分对常量严格为 0）
- advice_tracking.py L155 的实现层文案与 CLAUDE.md 一致，与 spec §1.1 矛盾——**代码已经做对，spec 的辩护反而是错的**

**建议**：
- spec §1.1-1.3 需要修正——至少要承认"在默认模式下 v_c 严格为 0"这一实现事实
- 实践结论（保留 metric 作 info/watch）可以不变，但理由要改：不是因为"v_c ≠ 0"，而是因为"miss 段目标屏幕速度本身是有用的情境信号"

### 不一致 #2（Low）：product-strategy.md L109 措辞歧义

见上 #4。"PTC/J-E/TBR 已弃"字面读法与代码/spec 矛盾。建议改为"（J-E/TBR 已弃；PTC 算法保留，命名误导待 v2）"或类似明确措辞。

### 不一致 #3（Trivial）：Hz² vs s⁻² 单位偏好

| 文档 / 代码 | 使用 |
|---|---|
| `analysis.py` L177 打印 | `Hz²` |
| `CLAUDE.md` 理论状态 | `1/s²（量纲上 Hz² 成立）` |
| tracking-coach spec §2.1 | `Hz²`（并明示"是否叫 Hz² 是命名选择"） |
| advice_tracking.py L174 | `Hz²` |

**不是矛盾**（都承认量纲是 1/s²），只是单位命名偏好。Hz² 语义不规范（Hz 通常给周期频率），但不阻塞。

---

## 残留搜索完整结果

### 代码（Python）

| 搜索 | 命中 |
|---|---|
| `TBR` | 0 |
| `tension_balance` / `TensionBalance` | 0 |
| `jitter_error` / `JitterError` | 0 |
| `J/E Ratio` 字面 | 0 |
| `pure_tension` / `tension_coeff` | 0 |
| `1.8` / `0.6` 作为 TBR 阈值 | 0 |

**代码完全干净。**

### 文档（Markdown）

所有 J/E+TBR 提及都在"已弃/否认/历史"语境，**无残留 active 声称**。详见 #3.2 表格。

### narrator 文案（knowledge.py + agent_kb.py）

- knowledge.py：无 J/E+TBR key（key 全是 signal 名）
- agent_kb.py：无 J/E+TBR chunk
- `"ptc high"` entry / `tension_budget_tracking` chunk 存在，但都标"生物力学假设，未 EMG 验证"（agent_kb 对冲充分；knowledge 欠对冲，见 #5）

---

## 关键路径索引

- 实现：
  - `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\kovaak_tracker\analysis.py` L121（PTC 公式）/ L74-75（v_rel / a_rel）/ L52-72（cross_x 平滑求导）/ L177（打印 "Pure Tension Coeff ... Hz²"）
  - `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\kovaak_tracker\tracking.py` L57（cross_pos 初始化为画面中心）/ L72-78（HSV 检测更新）/ L130-131（CSV 写入）
  - `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\kovaak_tracker\advice_tracking.py` L30-39（thresholds=None）/ L155（"主导项是目标速度"）/ L170-177（ptc_high severity=info）
- 声称文档：
  - `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\CLAUDE.md` 理论状态段（L15-21）
  - `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\docs\PRD.md` §2（L14-24）
  - `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\README.md` L77
  - `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\docs\product-strategy.md` L40, L96, **L109（歧义）**
  - `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\docs\superpowers\specs\2026-07-05-tracking-coach-design.md` §1.1-1.3（与 CLAUDE.md 矛盾）/ §2.1（PTC 算法事实）/ §7.1-7.2（PTC/TBR 诚实标注）
- narrator 内容：
  - `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\kovaak_tracker\coach\knowledge.py` L126-133（ptc high，community 欠对冲）
  - `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\kovaak_tracker\coach\agent_kb.py` L500-512（tension_budget_tracking，对冲充分）
