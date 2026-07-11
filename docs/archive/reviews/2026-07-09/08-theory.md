# 2026-07-09 Review — 理论一致性

> 审查日期：2026-07-09 · 审查类型：理论一致性深度验证 + 命名债统一清单
> 审查范围：flicking 指标学术锚点验证、tracking 理论债核查、spec §1 v_c=0 数学矛盾裁决

---

## 健康度总览

### 理论项分级

| 类别 | 数量 | 占比 |
|---|---|---|
| **实现 bug**（公式错误/量纲不成立） | 0 | 0% |
| **命名债**（实现合理但名字/文档误导） | 4 | 100% |
| **文档措辞**（澄清不足/歧义） | 3 | — |

**综合评分：8.5/10** — 无实现错误，4 项命名债已充分标记，诊断规则安全。

---

## 5a5bb84 submovement_overlap docstring 验证

### 修改内容

```python
# flicking.py:417 字段注释
submovement_overlap: float   # 实为 trough depth ratio（谷深/主峰），非 Novak time-overlap 字面义；high=流体融合, low=两阶段 (§6.2, 见 _submovement_structure 命名注)

# flicking.py:513-519 docstring 扩展
**命名注**：实为 *trough depth ratio*（谷深 / 主峰速度），非 Novak 2002
time-overlap 的字面实现——命名沿用便于下游消费，但语义是"减速段谷有多深"
（高 = 两阶段界限清晰、低 = 流体融合），与 tracking PTC 同型的"实现合理
但名字误导"。
```

### 验证结论

**✅ 准确且充分**。修改明确承认了：
1. 实现是 `trough / peak_v`（谷深/主峰），不是 Novak 2002 的 time-window overlap
2. 保留命名是"便于下游消费"的实用决策
3. 与 tracking PTC 同型（"实现合理但名字误导"）

**与代码实现一致**：`flicking.py:535` `trough / peak_v` 确实是 trough depth ratio。

**严重度**：低（已修复）

---

## 命名债统一清单

### #1 submovement_overlap（flicking）

- **位置**：`kovaak_tracker/flicking.py:417, 513-519`
- **命名**：`submovement_overlap`
- **实际**：`trough_depth_ratio = trough / peak_v`（谷深/主峰速度）
- **学术名义**：Novak 2002 overlapping submovements（时间窗重叠比例）
- **误导程度**：中 — 名字暗示时间维度，实现是速度维度
- **修正状态**：✅ 已在 5a5bb84 修正（docstring 明确标注）
- **阻塞性**：否（advice 不用绝对值，仅作趋势参考）
- **建议**：v2 重命名为 `trough_ratio` 或 `corrective_dip_ratio`

### #2 PTC（tracking）

- **位置**：`kovaak_tracker/analysis.py:121, 138, 156`
- **命名**：`Pure Tension Coeff (PTC)`
- **实际**：`mean(a_rel | miss) / max(mean(error_px | miss), 1.0)`
- **学术名义**：肌肉张力测量（需 EMG 验证）
- **误导程度**：高 — "Pure Tension" 暗示直接测肌肉张力
- **修正状态**：✅ 文档已诚实标注（CLAUDE.md + tracking-coach spec §2.1 + README）
- **量纲**：`(px/s²) / px = 1/s² = Hz²`（量纲成立，但 Hz² 语义不规范）
- **阻塞性**：否（threshold=None，仅 severity=info 提示性诊断）
- **建议**：v2 重命名为 `accel_error_density` 或 `miss_frame_accel_density`

### #3 speed_mismatch（tracking）

- **位置**：`kovaak_tracker/analysis.py:118, 135`
- **命名**：`speed_mismatch`
- **实际**：`mean(v_rel | miss)`，默认模式 `v_c=0` → `v_rel ≈ speed_t`（目标屏幕速度）
- **学术名义**：玩家准星速度 vs 目标速度的失配
- **误导程度**：中高 — 名字暗示"玩家误差"，实现主要描述"目标速度"
- **修正状态**：⚠️ 跨文档矛盾（CLAUDE.md 对，spec §1.1 错）
- **证据**：
  - `tracking.py:57` `cross_pos = (width//2, height//2)` 硬编码中心
  - `analysis.py:68-72` Savitzky-Golay 平滑 + np.gradient 对常量严格返回 0
  - `advice_tracking.py:155` 文案承认"v_rel 含准星噪声，**主导项是目标速度**"
- **阻塞性**：否（threshold=None，仅作 watch 提示）
- **建议**：spec §1.1-1.3 承认 CLAUDE.md 对；metric 作 info/watch 保留

### #4 accel_mismatch（tracking）

- **位置**：`kovaak_tracker/analysis.py:119, 136`
- **命名**：`accel_mismatch`
- **实际**：`mean(a_rel | miss)`，默认模式 `a_c=0` → `a_rel ≈ accel_t`（目标加速度）
- **学术名义**：玩家准星加速度 vs 目标加速度的失配
- **误导程度**：中 — 与 speed_mismatch 同型
- **修正状态**：⚠️ 跨文档矛盾（同 #3）
- **阻塞性**：否（threshold=None）
- **建议**：同 #3

### 命名债模式总结

四项命名债的共同特征：
1. **实现算法正确** — 产出的数字本身有意义
2. **名字与学术名义不符** — submovement_overlap ≠ time overlap、PTC ≠ 张力、mismatch ≠ 玩家误差
3. **诊断规则安全** — 全部用 None threshold / info / watch severity，不触发硬诊断
4. **已充分标记** — 文档都承认了命名误导

**最该修**：PTC（误导程度最高），但 v2 重构处理，不阻塞 v1。

---

## flicking 指标 vs 学术锚点（逐项验证）

### SPARC（频域平滑度，Balasubramanian 2012）

**公式**（`flicking.py:462-497`）：
```python
spectrum = np.abs(np.fft.rfft(speed)) / spectrum[0]  # DC 归一化
above = np.where(spectrum > 0.05)[0]
fc = int(above.max())
return -np.sum(np.sqrt(np.diff(f_v) ** 2 + np.diff(V_v) ** 2))
```

| 维度 | 论文 | 实现 | 判定 |
|---|---|---|---|
| FFT 类型 | rfft | `np.fft.rfft` | ✓ |
| DC 归一化 | /V(0) | `/= spectrum[0]` | ✓ |
| amp_th ε | 0.05 | `0.05` | ✓ |
| 弧长公式 | $-\sum\sqrt{\Delta\omega^2 + \Delta\hat{V}^2}$ | 同 | ✓ |
| 截止频率 fc | max freq 使 $\hat{V} > \epsilon$ | `above.max()` | ✓ |

**验证结论**：✅ 公式数学正确，与 Balasubramanian 2012 一致。

**已知局限**：短 flick（<16 帧）NaN — 文档已诚实标注（`flicking.py:473-479`）。

### Fitts throughput（bits/s）

**公式**（`flicking.py:591-594`）：
```python
D_deg = straight_px * deg_per_px
throughput = float(np.log2(D_deg / target_width_deg + 1)) / duration_s
```

| 维度 | Shannon 1947 | 实现 | 判定 |
|---|---|---|---|
| ID 公式 | $\log_2(D/W+1)$ | 同 | ✓ |
| TP = ID/MT | 是 | 是 | ✓ |
| D 语义 | 运动距离 | 起终点直线距离 | ✓（合理近似） |
| W 语义 | 目标宽度 | 全局中位数 bbox 宽 | ✓（1w6ts 假设成立） |

**验证结论**：✅ 公式正确，归一化合理（对 1w6ts 场景）。

### submovement（Novak 2002）

**实现**（`flicking.py:500-536`）：
- `prominence = peak_v * 0.2`（学术 50%）
- `distance = max(1, int(0.08 * fps))`（学术 200ms）
- `overlap = trough / peak_v`（非 Novak time-overlap）

**验证结论**：⚠️ 实现 ≠ 学术标准，但**命名债已标注**（5a5bb84）。

### linearity（恒定制动）

**实现**（`flicking.py:561-565`）：
```python
fit = np.polyfit(t, decel, 1)  # deg=1 直线
resid = decel - np.polyval(fit, t)
linearity = float(np.sqrt(np.mean(resid ** 2)) / peak_v)
```

**验证结论**：✅ 正确。理论锚点是 **constant-deceleration**（非 min-jerk），已修正（`research.md §6.1`）。

### decel_frac（减速段占比）

**实现**（`flicking.py:572`）：
```python
decfrac = (e - p) / max(1.0, (e - s))
```

**验证结论**：✅ 正确。健康带 [0.40, 0.65] 合理（Becker 2020）。

### path_efficiency（路径效率）

**实现**（`flicking.py:578-584`）：
```python
seg_len = float(np.sum(np.hypot(np.diff(xs), np.diff(ys))))
straight_px = float(np.hypot(xs[-1] - xs[0], ys[-1] - ys[0]))
path_eff = straight_px / seg_len
```

**验证结论**：✅ 正确。值域 [0, 1]，1 = 完美直线。

### reverse_ratio（反向修正比）

**实现**（`flicking.py:571`）：
```python
da = accel[p:e + 1]
reverse = float(np.mean(da > 0))
```

**验证结论**：✅ 正确。度量减速段正加速度帧占比。

### peak_speed_deg（峰值角速度）

**实现**（`flicking.py:600`）：
```python
peak_speed_deg = round(peak_v * deg_per_px, 2)
# deg_per_px = fov / meta.width
```

**问题**：x/y 各向同性近似未文档化（`deg_per_px` 仅水平 FOV，垂直低 17%）。

**验证结论**：⚠️ 量纲近似，建议 docstring 加注。

---

## tracking 理论债 + J/E+TBR 残留核查

### PTC 公式量纲验证

**公式**：`ptc = mean(a_rel | miss) / max(mean(error_px | miss), 1.0)`

**量纲推演**：
- `a_rel`：`‖a_c − a_t‖`，单位 `px/s²`
- `error_px`：`‖b - c‖`，单位 `px`
- `ptc`：`(px/s²) / px = 1/s²`

**Hz² vs s⁻²**：
- `Hz = 1/s`，所以 `Hz² = 1/s²` — 量纲上等价
- 但 `Hz` 通常指周期频率，PTC 不是频率 — 语义不规范

**验证结论**：✅ 量纲成立，单位命名可选（Hz² 或 s⁻² 均可接受）。

### J/E Ratio / TBR 残留核查

**代码搜索结果**（`kovaak_tracker/` 全包）：
- `TBR` / `tension_balance` / `TensionBalance`：0 命中
- `J/E` / `JitterError` / `jitter_error`：0 命中
- `1.8` / `0.6`（TBR 阈值）：0 命中

**文档核查**：
- 所有提及都在"已弃/否认/历史"语境（`CLAUDE.md L20, L118`、`README L77`、`tracking-coach spec §7.2`）
- `youtube doc L95` 主动纠正："PTC（...，**常被误称** J/E Ratio）"

**验证结论**：✅ J/E Ratio / TBR 清理**干净**，无残留。

### spec §1.1-1.3 v_c=0 数学矛盾裁决

**spec §1.3 论证**：
> "CSV 中 `cross_x/y` 是逐帧采样（不是常数标量），**所以 `v_c` 通常 ≠ 0**"

**数学事实核查**：
1. `tracking.py:57` `cross_pos = (width//2, height//2)` — **硬编码常量**
2. `analysis.py:51-52` 读 `cross_x/y` 列 → **每行同一常量**
3. `analysis.py:68` Savitzky-Golay 平滑 — **线性滤波器，常量输入 → 常量输出**
4. `analysis.py:68` `calc_derivative = np.gradient(..., 1/fps)` — **中心差分，常量数组 → 严格返回 0**

**结论**：**spec §1.3 的数学论证错误**。
- Savitzky-Golay 不向常量注入噪声
- `np.gradient` 对常量数组严格返回 0
- 默认模式下 `v_cx = v_cy = 0` 字面成立

**CLAUDE.md 对错判定**：
- CLAUDE.md "只描述目标运动，不描述玩家追踪误差" — ✅ **对**（默认模式字面成立）
- spec §1.1 标 CLAUDE.md 为"错" — ❌ **错**（基于数学误解）

**advice_tracking.py 立场**（L155）：
> "v_rel 含准星噪声，**主导项是目标速度**" — 与 CLAUDE.md 一致，与 spec 矛盾

**建议**：
- spec §1.1-1.3 需修正，承认默认模式 `v_c=0` 是实现事实
- 保留 metric 的实践结论（info/watch），理由改为"目标速度本身是有用情境信号"

---

## 短 flick SPARC NaN 率

**现象**：60fps 下 < 16 帧（< 0.27s）的 flick 返回 NaN。

**根因**（`flicking.py:482-493`）：
```python
if n < 8: return NaN  # Nyquist 限制
above = np.where(spectrum > 0.05)[0]
if above.size == 0 or above.max() < 2: return NaN  # 只有 DC 无法算弧长
```

**学术背景**：Balasubramanian 2012 原始研究用 2-2.6s reach 运动。

**性质**：
- 频域方法的**固有采样限制**，不是实现缺陷
- docstring 已诚实标注（`flicking.py:473-479`）

**建议**：
- 保留为已知局限
- v2 研究 time-domain fallback（如 jerk metric）

**阻塞性评估**：否（不影响健康 flick 诊断，仅短 flick 无 SPARC 样本）

---

## v1 阻塞性评估

### 阻塞项：0 项

| 债项 | 阻塞性 | 理由 |
|---|---|---|
| submovement_overlap 命名误导 | 否 | 已标注，advice 不用绝对值 |
| PTC 命名误导 | 否 | threshold=None，仅 info 提示 |
| speed_mismatch/accel_mismatch 不描述玩家误差 | 否 | threshold=None，仅 watch 提示 |
| 短 flick SPARC NaN | 否 | 已知局限，属频域方法固有限制 |
| spec §1 vs CLAUDE.md 矛盾 | 否 | 实现层已做对（advice_tracking.py），仅文档矛盾 |

### 诊断规则安全性

所有命名债项都被以下机制保护：
1. **threshold = None** — 永不触发（speed/accel/ptc）
2. **severity = info/watch** — 提示性而非诊断性（ptc）
3. **compare_table 标 info** — 不触发 finding（corrective_count）

即使命名误导，诊断**不会**基于错误解读发出处方。

---

## 结论摘要

1. **理论健康度 8.5/10**：无实现 bug，4 项命名债已充分标记。
2. **命名债 4 项各一句话**：
   - submovement_overlap：已修（5a5bb84），trough ratio 非 time overlap
   - PTC：文档已承认"修辞命名，非直接测肌肉张力"
   - speed_mismatch：CLAUDE.md 对（只描述目标运动），spec §1.1 错（数学误解）
   - accel_mismatch：同 speed_mismatch
3. **spec §1 修正建议一句话**：承认默认模式 v_c=0 是实现事实，保留 metric 作 info/watch，理由改为"目标速度是有用情境信号"。
4. **v1 阻塞性评估**：否（所有债明确标 v2 重构，advice thresholds 守住）。

---

**审查文件**：`docs/review/2026-07-09/08-theory.md`（本文件）
**关联报告**：`docs/review/2026-07-08/06-theory-flicking.md`、`07-theory-tracking.md`
