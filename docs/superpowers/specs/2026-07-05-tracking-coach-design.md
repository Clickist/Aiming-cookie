# Tracking Coach 设计稿

> 日期：2026-07-05 · 状态：设计稿（v2，框架修正版），待点点 review · 作者：research session
> 范围：在**现有 tracking 算法（已实现、在跑）**之上叠 coaching 层（signal → 诊断 → 处方）。**只产出本文档，不动代码。**
> 上游契约：`kovaak_tracker/advice.py`（flicking 规则引擎范式）、`kovaak_tracker/analysis.py`（tracking 指标产出）、`kovaak_tracker/flicking.py` L4-5（准星/视角框架）、`docs/coach-theory-foundation.md` / `docs/coach-community-frontier.md` / `youtube doc/YouTube 瞄准训练内容综合.md`（理论底座）

---

## 1. 框架修正（三层模型，替代前一份 spec 的错误框架）

### 1.1 前一份 spec 哪里错了

> ⚠️ **2026-07-09 勘误（review §08 裁决）**：本节及 §1.3 此前用「Savitzky-Golay 平滑 + 数值微分会把常量 cross_pos 变成噪声级非零」论证 `v_c ≠ 0`，并把 CLAUDE.md 的 `v_c=0` 打成「前一稿错误」。**该数学论证不成立**：Savitzky-Golay 是线性滤波（常量进=常量出），`np.gradient` 对常量严格返回 0。app.py 路径下 cross_pos 默认硬编码画面中心（常量），**`v_c = 0` 字面成立**——CLAUDE.md「理论状态」段判断正确。仅 calibration_cli 启用准星 HSV 检测时 v_c 才可能 ≠ 0。下方表格把 `v_c=0` 当「前一稿错误」的措辞以此勘误为准；§1.3 line 64 已修正。metric 仍保留作 info/watch（§3），但理由是「miss 段目标速度是有用情境信号」，非「描述玩家追踪误差」。

前一稿把项目里**两层不同的东西**当成同一层、并用错误的态度审视：

| 前一稿的错误判断 | 实际情况 |
|---|---|
| 把 `speed_mismatch`/`accel_mismatch`/`ptc` 标为 "bug-shaped"、"实现 vs 命名不符"、"只描述目标运动，不描述玩家追踪误差" | **错**。算法在 frame_errors.csv 同时算并落盘了 `speed_c / accel_c / v_rel / a_rel`（`analysis.py` L74-75, 86-90）；前一稿误以为 `v_c = a_c = 0` 而得出"只反映目标运动"的结论——见 §1.3 反驳。 |
| 把 "准星硬编码画面中心" 当 tracking 的根 bug，宣称它让所有玩家运动学指标失效 | **错**。`flicking.py` L4-5 docstring 已明示：KovaaK's 中准星**锁屏中**，动鼠标 = **视角平移**。准星在画面中心是**对的设计**，不是 bug——CV 看到的目标屏幕坐标已经隐含了玩家的视角控制。 |
| 把 PTC / speed_mismatch 标为 "aspirational（声称但未实现）" | **错**。算法 100% 在跑，每次 `run_analysis` 都把它们写真数字到 `metrics.json`。"aspirational" 应只用于形容**解读层假设**（如 "PTC 高 = 张力大" 这种把数字翻译成玩家发力状态的论断），不是算法本身。 |
| 把 "J/E Ratio / TBR" 当独立指标审视 | 部分对。这两个名字在仓库里确实没单独算法（J/E 字面上 ≈ PTC，TBR 无公式），它们是**解读层标签**，不是独立算法。前一稿把它们和算法层混着判，结论虽然丢弃它们没错，但理由站不住。 |
| 结论建议 "改名 `speed_mismatch` → `target_speed_on_miss`" | **错**。基于错误的 "v_c=0" 前提，前提不成立所以改名理由不成立。原名 mismatch 在 v_rel（玩家准星速度 vs 目标速度的差）意义上是准确的。 |

### 1.2 新框架：三层清晰分离

```
┌──────────────────────────────────────────────────────────────┐
│ 算法层（已实现、在跑）                                        │
│ analysis.py.evaluate_mechanics 每次跑都写真数字到 metrics.json │
│ 产出：ptc / speed_mismatch / accel_mismatch / on_target_pct / │
│       loss_count / avg_error_px / total_off_time              │
│ 全帧时序（frame_errors.csv）：error_px / v_rel / a_rel / ...   │
└──────────────────────────────────────────────────────────────┘
                            ↓ 喂数字
┌──────────────────────────────────────────────────────────────┐
│ 解读层（假设，不是凭空）                                      │
│ 把数字翻译成 "玩家发力状态" 的合理生物力学直觉。              │
│ 例：PTC 高 = miss 段加速度密度高 = 可能张力大                 │
│ 例：TBR > 1.8 = 过度握紧                                      │
│ 性质：合理假设，可能要 EMG / 手部摄像头验证，但不和算法冲突。 │
└──────────────────────────────────────────────────────────────┘
                            ↓ 用假设给诊断
┌──────────────────────────────────────────────────────────────┐
│ Coaching 层（要建，本 spec 的范围）                           │
│ 消费算法产出，按 signal → 阈值 → 诊断 → 处方 规则给出建议。   │
│ 每条规则标 source_level + 解读假设的诚实等级。                │
└──────────────────────────────────────────────────────────────┘
```

**本 spec 做的：加 coaching 层。不做：重做算法、删除解读假设、改 metrics 名字。**

### 1.3 准星/视角框架（关键事实，不是 bug）

引自 `kovaak_tracker/flicking.py` L4-5（module docstring）：

> In KovaaK's the crosshair is locked to screen center, so moving the mouse
> translates the whole view. The tracked target's screen-space velocity **is** the
> flick motion.

对应 tracking 场景：

- 准星视觉锁屏中（`tracking.py` L56 默认 `(width//2, height//2)`，L70-73 可选 HSV 检测覆盖但通常仍是中心）
- 玩家动鼠标 → KovaaK's 平移视角 → CV 看到目标在屏幕上动
- `analysis.py` L52-53 读 `cross_x / cross_y` 列做平滑，L69-72 对其求导得 `v_cx / v_cy / a_cx / a_cy`
- 关键：**app.py 路径下 `cross_pos` 默认硬编码画面中心（`tracking.py` L57 `(width//2, height//2)`，常量），未启用准星 HSV 检测 → `cross_x/y` 是同一常量**。Savitzky-Golay 是线性滤波（常量进=常量出），`np.gradient` 对常量严格返回 0，**所以 app.py 路径 `v_c = 0` 字面成立**（CLAUDE.md 判断正确，见 §1.1 勘误）；仅 `calibration_cli` 启用准星 HSV 检测时 cross_pos 才逐帧更新、v_c 才可能 ≠ 0。在 tracking 场景下，玩家视角平移让目标屏幕坐标变化主导，准星屏幕坐标接近常数，但算法层面 v_rel 公式 (`analysis.py` L74) `v_rel = ‖v_c − v_t‖` 是对的——它度量的是屏幕空间中"准星离目标多快地分开/合拢"。
- 所以 tracking 场景下 `v_rel ≈ speed_t`（目标屏幕速度）是**物理上正确**的解读，而不是 bug——动鼠标的效果就是让目标在屏幕上动，玩家把目标保持在小框内 = v_rel 小。
- 这与 flicking 一致（`flicking.py` 同样用目标屏幕速度作为 flick 速度）。

### 1.4 本 spec 与前一稿的连续性

保留的：候选 signal 列表（accuracy / loss / off_time / avg_error）的思路、`advice_tracking.py` 独立模块、按 `meta["summary_type"]` 分流的契约、与 flicking coach 共存的 narrate 入口。

替换的：第 2 章全段重写（不再审视 "solidity"，改为列算法事实 + 解读假设 + 证据等级）；PTC/speed_mismatch 的处理从 "改名 / 弃用" 改为 "保留，挂解读假设"；J/E+TBR 的处理从 "aspirational 全删" 改为 "解读层诚实标注，不在 v1 规则 emit"。

---

## 2. 现有 tracking 算法（在跑）+ 解读假设

每项列：**算法事实**（公式、来源、单位）、**解读假设**（把数字翻译成玩家状态）、**假设的证据等级**。

### 2.1 `ptc`（Pure Tension Coefficient）

- **算法事实**（`analysis.py` L121）：`ptc = mean(a_rel on miss frames) / max(mean(error_px on miss frames), 1.0)`
- **量纲**：`(px/s²) / px = 1/s² = Hz²`（量纲推演成立，是否叫 "Hz²" 是命名选择）
- **仅 miss 帧聚合**：`miss_mask = kdf["is_miss"]`，只在脱靶帧算（L108, L117）
- **解读假设**："每单位空间误差所承载的相对加速度；高 = miss 段目标急加速 / 误差却小 = 玩家在用大力气追 = **可能张力大**"
- **证据等级**：⚠️ **生物力学假设 / 经验未验证**
  - 物理直觉合理（miss 段玩家发力追，加速度密度高意味着制动密集发力）
  - 但 "Pure Tension Coefficient" 是修辞命名——**不直接测肌肉张力**。需 EMG 或手部摄像头（`docs/product-strategy.md` 产品 B 的延伸价值）验证。
  - 无 peer-reviewed 文献定义此指标，可挂 "speed-accuracy tradeoff (Fitts)" 作远亲。
- **v1 处理**：保留算法；作为可选 signal（§3 `ptc_high`）；narrator 文案标 "假设/可能" 而非断言。

### 2.2 `speed_mismatch`

- **算法事实**（`analysis.py` L118）：`mean(v_rel on miss frames)`，`v_rel = ‖v_cx − v_tx, v_cy − v_ty‖`（L74）
- **量纲**：px/s
- **解读假设**："高 = miss 段目标屏幕速度快 = 玩家在高速段失手多"
- **证据等级**：✅ **算法事实直接** / 解读 "高速段失手" 是**合理推断**（小跳跃：miss 帧采样了"目标屏幕速度"，但 v_rel 也含 v_c 噪声，主导项是目标速度）
- **理论锚点**：speed matching 是公认运动控制概念（Kowler, Murphy & Steinman 1978；Lisberger 2015 smooth pursuit——`youtube doc §4.4` 已核实）。
- **v1 处理**：作为 signal `speed_mismatch_high`（§3）。

### 2.3 `accel_mismatch`

- **算法事实**（`analysis.py` L119）：`mean(a_rel on miss frames)`
- **量纲**：px/s²
- **解读假设**："高 = miss 段目标急加速/变向 = 玩家应对瞬时加速度吃力"
- **证据等级**：✅ **算法事实直接** / 解读合理。
- **理论锚点**：同 §2.2，"reactive tracking 应对瞬时加速度" 是 Voltaic 社区分类（`youtube doc §4.1`）。
- **v1 处理**：作为 signal `accel_mismatch_high`（§3）。

### 2.4 `on_target_pct`（命中率）

- **算法事实**（`analysis.py` L115）：`(1 − miss_frames / total_frames) × 100`
- **量纲**：%
- **解读假设**："命中率 = 整体追踪控制能力的金标准量"
- **证据等级**：✅ **solid / 行业金标准**（Voltaic tracking scores 本质 = accuracy + time-on-target）。
- **v1 处理**：作为 signal `accuracy_low`（§3）。

### 2.5 `loss_count`

- **算法事实**（`analysis.py` L127）：`sum(is_miss.diff() > 0)`（脱靶→命中的边沿数，即"丢失次数"）
- **量纲**：次
- **解读假设**："高 = 追踪频繁断（速度匹配跟不上目标变向 / 视觉读取慢）"
- **证据等级**：✅ **solid / 社区共识**。
- **v1 处理**：作为 signal `loss_count_high`（§3）。

### 2.6 `avg_error_px`

- **算法事实**（`analysis.py` L136）：`mean(error_px)` 全帧
- **量纲**：px
- **解读假设**："高 = 准星虽在 target 上但偏移大，临界命中多 / 精度不足"
- **证据等级**：✅ **算法事实**。但**跨分辨率/跨目标尺寸不可比**——需归一化（`avg_error_px / ball_w`）。
- **v1 处理**：作为 signal `avg_error_high`（§3），summary 层需补 `ball_w` 派生比值。

### 2.7 `total_off_time`

- **算法事实**（`analysis.py` L129）：`total_miss_frames / inferred_fps`
- **量纲**：s
- **解读假设**："高 = 累计偏离久 / 单次脱靶回位慢"
- **证据等级**：✅ **算法事实**。
- **v1 处理**：作为 signal `off_target_long` 的派生（`total_off_time / loss_count` = 单次平均回位时间）（§3）。

### 2.8 全帧时序（`frame_errors.csv`）

`extract_kinematics`（`analysis.py` L44）逐帧输出 `error_px / is_miss / speed_t / accel_t / speed_c / accel_c / v_rel / a_rel / ball_w / ball_h`。这些是**规则引擎可用的原料**远不止 miss-frame 聚合的 7 个标量。本 spec v1 不消费时序（只用 summary 标量），v2 可派生 SPARC / 方向偏置等。

### 2.9 metrics.json schema

`export_analysis`（`analysis.py` L142-160）写到 `output/metrics.json`：

```json
{
  "tension": {"avg_error_px", "speed_mismatch", "accel_mismatch", "ptc"},
  "loss":    {"on_target_pct", "loss_count", "total_off_time"}
}
```

注意：**summary 是扁平标量 dict**，与 flicking summary 的 `{metric: {med, p75, p90}}` per-flick 分布不同（§5）。

---

## 3. Tracking coaching 规则（signal → 阈值 → 诊断 → 处方）

仿 `advice.Finding`（`advice.py` L25-31）结构。**所有阈值标"需真实数据校准"**——给保守初始值，不编造精确数字。每个 signal 标 source_level（按 `coach-theory-foundation.md` 铁律：诊断规则只用学术根基 / 社区共识；个人经验进 narrator 文案不进规则）。

候选 signal 全集（v1 选其中部分实装）：

### 3.1 v1 候选 signal

#### A. `accuracy_low`（命中率低）

- **算法**：`on_target_pct < THRESHOLDS["tracking_accuracy_low"]`
- **阈值**：`70.0` %（Voltaic tracking benchmark 健康线；**初始经验值，需真实数据校准**）
- **source_level**：`community_consensus`（Voltaic benchmarks）
- **诊断**：`f"命中率 {on_target_pct:.1f}%（健康 >70%）——整体追踪控制不足。"`
- **处方**：
  - `Prescription("pasu", "连续追踪基础，速度匹配")`
  - `Prescription("VT Multiclick 30% larger", "落点精度 + 微调")`
- **理论锚点**：speed-accuracy tradeoff + Voltaic tracking 子类评估。

#### B. `loss_count_high`（脱靶频次高）

- **算法**：`loss_count > THRESHOLDS["tracking_loss_count_high"]`
- **阈值**：`60` 次 / 60s 录像（**初始经验值，需真实数据校准**——50/60s≈1 次/s 已是断追踪很严重）
- **source_level**：`personal_experience_unverified`（初始值待校准；规则本身的"频繁脱靶=速度匹配跟不上"是 community_consensus）
- **诊断**：`f"脱靶 {loss_count} 次（频繁断追踪），每次回位 {total_off_time/max(loss_count,1):.2f}s——追踪不连续，可能是速度匹配跟不上目标变向。"`
- **处方**：
  - `Prescription("VT reactive tracking", "应对瞬时加速度")`
  - `Prescription("Clover Raw Control", "速度匹配 + 侧向挤压稳准星")`
- **理论锚点**：smooth pursuit 速度匹配（Kowler 1978）+ 社区"反应式追踪"（`youtube doc §4.1`）。

#### C. `off_target_long`（单次脱靶回位慢）

- **算法**：`total_off_time / max(loss_count, 1) > THRESHOLDS["tracking_off_target_long_s"]`
- **阈值**：`0.05` s（约 3 帧 @ 60fps——**初始经验值，需真实数据校准**）
- **source_level**：`personal_experience_unverified`
- **诊断**：`f"每次脱靶平均 {off_per:.2f}s 才回位——读回目标慢（可能视觉锁定 / 反应延迟）。"`
- **处方**：
  - `Prescription("VT evasive tracking", "目标逃逸型，练视觉读取")`
  - `Prescription("Clover Raw Control", "锁定目标，减少丢失")`
- **理论锚点**：smooth pursuit 重新捕获（catch-up saccade 文献）。

#### D. `avg_error_high`（平均误差大）

- **算法**：`avg_error_px / ball_w > THRESHOLDS["tracking_avg_error_ratio"]`（**需归一化**，summary 层补 `ball_w`）
- **阈值**：`0.5`（误差超过半个目标宽——**初始经验值，需真实数据校准**）
- **source_level**：`community_consensus`
- **诊断**：`f"平均误差 {avg_error_px:.1f}px（{avg_error_px/ball_w:.0%}% 目标宽）——准星虽在 target 上但偏移大，临界命中多。"`
- **处方**：
  - `Prescription("VT precise tracking", "精度追踪专项")`
  - `Prescription("focus on crosshair gap", "中间空隙准星，注意力锁中心")`
- **理论锚点**：Fitts（精度 = 1/目标宽）+ Bardoz gap 准星（社区）。

#### E. `speed_mismatch_high`（高速段失手多）

- **算法**：`speed_mismatch > THRESHOLDS["tracking_speed_mismatch_high"]`
- **阈值**：**待标定**（与目标速度的绝对量级有关；先观察分布再定）
- **source_level**：`community_consensus`（speed matching 概念学术 + "高速段失手" 经验）
- **诊断**：`f"miss 段目标屏幕速度 {speed_mismatch:.0f} px/s——高速段失手多，速度匹配跟不上。"`
- **处方**：
  - `Prescription("VT control tracking", "持续中速追踪")`
  - `Prescription("Clover Raw Control", "侧向挤压稳准星")`
- **理论锚点**：speed matching（Kowler 1978）+ `youtube doc §4.1`。
- **注**：若该阈值与 `loss_count_high` 共发，提升 severity（高速 + 频繁丢 = reactive tracking 弱）。

#### F. `accel_mismatch_high`（瞬时加速度段失手多）

- **算法**：`accel_mismatch > THRESHOLDS["tracking_accel_mismatch_high"]`
- **阈值**：**待标定**
- **source_level**：`community_consensus`
- **诊断**：`f"miss 段目标加速度 {accel_mismatch:.0f} px/s²——应对变向吃力。"`
- **处方**：
  - `Prescription("VT reactive tracking", "应对瞬时加速度")`
- **理论锚点**：reactive tracking（`youtube doc §4.1`）。

#### G. `ptc_high`（PTC 高 = 张力大假设）

- **算法**：`ptc > THRESHOLDS["tracking_ptc_high"]`
- **阈值**：**待标定**（前一份 spec 提到 1364 这种值无参照——必须先跑真实数据看分布）
- **source_level**：`personal_experience_unverified`（**生物力学假设**，需 EMG 验证）
- **诊断**（**措辞用假设/可能**，不断言）：
  - `f"miss 段加速度密度 PTC={ptc:.0f} Hz²——可能张力偏大（生物力学假设，未 EMG 验证）。结合 sparc / 反向修正一起读更稳。"`
- **处方**：
  - `Prescription("暴露疗法：高 sens + 低 FOV 精准追踪", "放大微颤，逼大脑修正张力分配")`
- **理论锚点**：张力预算（Viscose，`youtube doc §3`）。**这条规则是假设性 signal**，可降级 severity 到 "info"（提示性而非诊断性）。
- **诚实标注**：见 §7。

### 3.2 v2 候选 signal（需派生新指标，本 spec 只列）

| signal | 算法 | 前置 |
|---|---|---|
| `tracking_sparc_low` | 全帧 `v_rel` 上算 SPARC（Balasubramanian 2012），跨整段或滑窗 | 全帧时序已在 frame_errors.csv，可直接派生；阈值需人群标定 |
| `trailing_micros` | `v_target` 与 `crosshair→target` 向量的夹角时序均值偏大 = "尾随" | 时序数据已有；社区锚点（`youtube doc §6.2`） |
| `directional_bias` | `error_dx, error_dy` 分方向聚合 | 时序数据已有；尺偏 ROM + 内在/外在肌协同（`youtube doc §6.3`） |

### 3.3 不进 v1 规则的诊断（移 narrator）

- **"过度握紧"**：TBR 阈值无公式无校准（§7），v1 不 emit `overgripping` signal。narrator 在讲 `ptc_high` + `tracking_sparc_low` 共发时可**提示性**提及"可能是张力锁定"，但规则不直接断言。
- **"反应滞后"**：需 leading-vs-trailing 比（§3.2 `trailing_micros`），v1 不做。

---

## 4. profiles / knowledge / agent_kb 接入

### 4.1 `profiles.ARCHETYPES` 追加（tracking archetype）

仿现有 flicking archetype 风格（`profiles.py` L10-44）。signal key 必须与 §3 Finding.signal 完全一致。

```python
# profiles.py 追加（v2 实装，spec 阶段只设计）
ARCHETYPES += [
    {
        "id": "tension_locked",      # 张力锁定型
        "label": "张力锁定型",
        "conditions": {"ptc high": 1.0, "accuracy low": 0.5},
    },
    {
        "id": "reactive_loser",      # 反应滞后型
        "label": "反应滞后型",
        "conditions": {"loss count high": 1.0, "off target long": 0.7},
    },
    {
        "id": "precision_borderline",  # 临界精度型
        "label": "临界精度型",
        "conditions": {"avg error high": 1.0},  # 命中率不一定低但偏移大
    },
    {
        "id": "speed_overmatched",   # 速度超纲型（目标太快跟不上）
        "label": "速度超纲型",
        "conditions": {"speed mismatch high": 1.0, "accel mismatch high": 0.7},
    },
    {
        "id": "fluid_tracker",       # 流体追踪型（positive）
        "label": "流体追踪型",
        "conditions": {},  # matched when no negative signals fire
    },
]
```

注：信号 label 用空格分隔（对齐现有 `"decel_frac high"` / `"sparc low"` 风格）。

### 4.2 `profiles.ROOT_CAUSES` 追加（三层根因）

仿现有格式（`profiles.py` L48-60）：

```python
ROOT_CAUSES.update({
    "accuracy low":          ("命中率低",         "整体速度匹配 + 微调精度不足", "pasu + VT Multiclick 落点"),
    "loss count high":       ("频繁脱靶",         "目标变向读取 / 速度匹配跟不上", "VT reactive tracking"),
    "off target long":       ("脱靶后回位慢",     "视觉重新锁定延迟", "VT evasive + Clover Raw Control"),
    "avg error high":        ("误差大",           "准星虽在 target 但偏移大", "VT precise tracking + crosshair gap 意识"),
    "speed mismatch high":   ("高速段失手",       "speed matching 上限", "VT control tracking"),
    "accel mismatch high":   ("变向段失手",       "reactive tracking 极限", "VT reactive tracking"),
    "ptc high":              ("可能张力偏大",     "假设：发力密集（未 EMG 验证）", "暴露疗法 + 侧向挤压"),
})
```

### 4.3 `knowledge.KNOWLEDGE` 追加（社区经验 → narrator）

仿现有 12 条 flicking knowledge（`knowledge.py` L19-99）：

```python
KNOWLEDGE.update({
    "accuracy low": {
        "community": "Voltaic tracking benchmark 健康线 70%+；命中率是 tracking 金标准量。",
        "cues": ["pasu 练连续追踪基础", "VT Multiclick 30% larger 练落点精度"],
    },
    "loss count high": {
        "community": "频繁断追踪 = 速度匹配跟不上目标变向（MattyOW reactive tracking 概念）。",
        "cues": ["VT reactive tracking 应对瞬时加速度", "Clover Raw Control 速度匹配 + 侧向挤压稳准星"],
    },
    "off target long": {
        "community": "脱靶后回位慢 = 视觉重新锁定延迟（catch-up saccade 文献）。",
        "cues": ["VT evasive tracking 练视觉读取", "锁定目标整体运动矢量，不被局部小动作干扰"],
    },
    "avg error high": {
        "community": "临界命中（Fitts：精度 = 1/目标宽）。bardOZ 推荐带 gap 准星，注意力锁中心。",
        "cues": ["VT precise tracking 专项", "用带 gap 准星，注意力集中在空隙中心"],
    },
    "speed mismatch high": {
        "community": "smooth pursuit 速度匹配（Kowler 1978）——目标屏幕速度快时失手多。",
        "cues": ["VT control tracking 持续中速追踪", "前臂大平稳位移 + 手腕抵消微小误差"],
    },
    "accel mismatch high": {
        "community": "reactive tracking（Voltaic S5 子类）——目标瞬时变向时失手。",
        "cues": ["VT reactive tracking 应对瞬时加速度", "极短张力爆发应对变向，随后立即释放"],
    },
    "ptc high": {
        "community": "张力预算（Viscose）：手部张力是有限预算，超支会震颤并剥夺视觉读取（lockout）。",
        "cues": [
            "暴露疗法：高 sens + 低 FOV 精准追踪放大微颤，逼大脑修正张力分配",
            "侧向挤压鼠标侧面而非向下垂直按压——侧向给纯摩擦力控制",
            "（生物力学假设，未 EMG 验证；作提示性诊断，需结合 SPARC / 反向修正一起读）",
        ],
    },
})
```

### 4.4 `agent_kb.py` 已有 tracking 素材挂钩

`agent_kb.py` 当前已有 4 个 tracking 相关 topic（`signal=None` 未挂钩）：

| topic | 当前 signal | 建议挂钩 |
|---|---|---|
| `tracking_three_kinds`（`youtube doc §4.1-4.3`）| None | 多 signal 共享——按 sub-topic 拆分时挂 `speed mismatch high` / `accel mismatch high` / `loss count high`；或保持 None 由 narrator 按主题检索（topic-based 而非 signal-based） |
| `fluidity`（`youtube doc §5.3`）| None | 正向画像，挂 `fluid_tracer`（不进 BY_SIGNAL，由 topic 索引检索） |
| `vod_review`（`youtube doc §6`）| None | 含 trailing micros / directional bias，v2 挂 `trailing micros` / `directional bias` |
| `tension_budget`（`youtube doc §3`）| `"sparc low"`（已挂 flicking） | tracking 场景下复用——可同时挂 `ptc high`（同一 chunk 挂多 signal 需扩展 BY_SIGNAL 为多值映射，或复制 chunk） |

**v1 最小改动**：`agent_kb._build_indexes` 已经按 signal 索引；§4.3 新增 signal 进入 KNOWLEDGE 后，narrator 按 signal 检索 KnowledgeChunk 时自动覆盖 tracking。`agent_kb.py` 的 BY_SIGNAL 不需立即加 tracking signal——它的 chunk 是 narrator 进阶素材，不是规则必需。

---

## 5. 实现方案

### 5.1 新模块 `kovaak_tracker/advice_tracking.py`

**为什么不 extend `advice.py`**：

- flicking summary 是 `{metric: {med, p75, p90}}` per-flick 分布 dict（`advice._med` L51-61 适配这种形状）
- tracking summary 是**单段标量聚合**（`analysis.evaluate_mechanics` L131-139 直接出标量；`export_analysis` L148-160 分 `tension` / `loss` 两组但仍是标量）
- unify 会导致 `advise()` 内部 if-else 分流 + `_med` 适配两种形状 + 两套 THRESHOLDS key 互染——破坏单一职责（前一份 spec 这点判断对）。

**模块契约**（与 `advice.advise` 对称）：

```python
# kovaak_tracker/advice_tracking.py（spec 阶段，未实装）
from dataclasses import dataclass, field
from typing import Optional
from .advice import Prescription, Finding  # 复用 dataclass

THRESHOLDS = {
    "tracking_accuracy_low": 70.0,         # %
    "tracking_loss_count_high": 60,        # 次 / 60s（需校准）
    "tracking_off_target_long_s": 0.05,    # s（需校准）
    "tracking_avg_error_ratio": 0.5,       # error_px / ball_w（需校准）
    "tracking_speed_mismatch_high": None,  # 待标定
    "tracking_accel_mismatch_high": None,  # 待标定
    "tracking_ptc_high": None,             # 待标定
}

def advise_tracking(
    self_summary: dict,
    reference_summary: dict | None = None,
    cm_per_360: float | None = None,
) -> list[Finding]:
    """Tracking summary（标量 dict）→ diagnosis + prescriptions.

    self_summary 形如 metrics.json：
        {"tension": {avg_error_px, speed_mismatch, accel_mismatch, ptc},
         "loss":    {on_target_pct, loss_count, total_off_time}}
    或扁平化后的 {"on_target_pct", "loss_count", ...}（report.py 调用前 normalize）。
    """
    ...
```

### 5.2 `report.build_report` 分流契约

**两个方案**：

**方案 A（推荐）：`meta["summary_type"]` 显式标记**

```python
def build_report(summary, reference_summary=None, meta=None, ...):
    meta = meta or {}
    st = meta.get("summary_type")
    if st == "flicking":
        findings = advise(summary, reference_summary, cm_per_360=meta.get("cm_per_360"))
        comparison = compare_table(summary, reference_summary) if reference_summary else None
    elif st == "tracking":
        findings = advise_tracking(summary, reference_summary, cm_per_360=meta.get("cm_per_360"))
        comparison = None  # v1 self-only（§8.3）
    else:
        # 兜底：探测 summary 形状（少歧义性 fallback）
        if any(k in summary for k in ("tension", "loss")) or any(
            k in summary for k in ("on_target_pct", "ptc")
        ):
            findings = advise_tracking(summary, reference_summary, ...)
        else:
            findings = advise(summary, reference_summary, ...)
    diagnosis = build_diagnosis(findings, summary, comparison, meta)
    ...
```

**方案 B：纯 keys 探测**——少 1 行 meta 设置，但 flicking/tracking summary 万一 keys 重叠（如未来都加 `ptc`）会误判。

**选 A**：显式 > 隐式，少歧义。flicking 调用方加 `meta={"summary_type": "flicking"}`，tracking 加 `"tracking"`，是调用方的本就拿在手上的信息。

### 5.3 summary normalize（建议）

`metrics.json` 的两层嵌套（`tension / loss`）对 `advise_tracking` 不友好。建议 build_report 调用前 flatten：

```python
def _flatten_metrics(metrics_json: dict) -> dict:
    return {**metrics_json.get("tension", {}), **metrics_json.get("loss", {})}
```

输出：`{"avg_error_px", "speed_mismatch", "accel_mismatch", "ptc", "on_target_pct", "loss_count", "total_off_time"}`——`advise_tracking` 直接读这 7 个标量。

### 5.4 阶段化实装

| 阶段 | 范围 | 前置 |
|---|---|---|
| **v1.0** | §3.1 A-D 四条 signal（accuracy / loss_count / off_time / avg_error） | `advice_tracking.py` 新增；`report.build_report` 加 `summary_type` 分流；analysis 输出 normalize（或 report 端 flatten）+ `ball_w` 进 summary |
| **v1.1** | §3.1 E-G（speed_mismatch / accel_mismatch / ptc_high） + §4 profiles/knowledge 接入 | 真实数据标定阈值 |
| **v2.0** | §3.2 SPARC / trailing / directional_bias | 时序派生 + 阈值标定 |

---

## 6. 与 flicking coach 共存

### 6.1 agent narrate 入口不变

`coach/agent.py` 的 `narrate_diagnosis`（`agent.py` L278）是通用的——它消费 `CoachDiagnosis` dataclass（`diagnosis.py` L36-41），不分 flicking/tracking。画像 / issues / 根因链 / 处方列表的 dataclass 是共用的。

### 6.2 分流发生在 build_report 层

`report.build_report` 按 `meta["summary_type"]`（§5.2）分流到 `advise` / `advise_tracking`，得到 `list[Finding]`。然后：

- `diagnosis.build_diagnosis(findings, ...)`（`diagnosis.py` L58）已经消费任意 Finding list，不需改。
- `profiles.ROOT_CAUSES` / `profiles.ARCHETYPES` 需补 tracking signal（§4.1, §4.2）——`diagnosis._root_causes_for`（L111）会自动找到。
- `knowledge.KNOWLEDGE` 需补 tracking signal（§4.3）——narrator 按 signal 检索自动覆盖。
- `agent_kb.BY_SIGNAL` 可选挂 tracking signal（§4.4），v1 不阻塞。

### 6.3 diagnosis tool `coach_get_diagnosis` 返回

agent 工具返回 `CoachDiagnosis`——其中 `meta["summary_type"]` 标明 flicking/tracking，agent 据此选 narrator 文案模板（例如 tracking 不讲 submovement / path_efficiency，flicking 不讲 loss_count / accuracy）。narrator 不需要分两个模块，**用同一 narrate_diagnosis + 按 signal 词表选 prompt 模板**即可。

### 6.4 narrator 不区分

`narrate_diagnosis`（`agent.py` L278）的 prompt 是按 findings 的 signal 词表动态组装的——tracking signal 自然走 tracking knowledge 检索（§4.3），flicking signal 走 flicking knowledge。不需要两个 narrator。

---

## 7. 解读层假设的诚实标注

**铁律**：narrator 文案对解读假设用 "假设 / 可能 / 推测" 而非断言；规则层不直接 emit 假设性 signal 作为 "fix" severity（用 "info" / "watch"）。

### 7.1 PTC = 张力

- **假设性质**：生物力学假设——"miss 段加速度密度高" → "玩家发力密集" → "张力大"。每一步都是合理推断但非直接测量。
- **验证路径**：EMG 或手部摄像头（`docs/product-strategy.md` 产品 B）。
- **narrator 措辞**："可能张力偏大"、"提示性诊断"、"结合 SPARC / 反向修正一起读更稳"——而非 "你张力过大"。
- **规则处理**：`ptc_high` signal severity 默认 "info"（§3.1 G）。

### 7.2 TBR 1.8 / 0.6 阈值

- **假设性质**：仓库内无公式、无常模、无引用。前一份 spec 此点判断对——TBR 凭空。
- **v1 处理**：**不实装**。`profiles.ROOT_CAUSES` / `knowledge.KNOWLEDGE` 不挂 TBR signal；不进 `advice_tracking`。
- **未来路径**：如果要做，必须先有可计算定义（如 `ptc / mean(error_px)` 的某种比值？）+ 真实数据标定 1.8/0.6 是否对人有意义。在 v2 手部摄像头落地前不应进规则。
- **CLAUDE.md / AGENTS.md 措辞**：声明 "J/E Ratio / TBR 是核心理论" 与代码不符（已有 memory `tracking-tbr-not-implemented.md` 记录此债）。**本 spec read-only 不改**——这是给点点的待办（§8.4）。（**更新**：AGENTS.md 已 2026-07-07 删除，CLAUDE.md 已更新为权威源并修正 J/E Ratio / TBR 措辞，见 CLAUDE.md "理论状态"段。）

### 7.3 speed_mismatch 解读跳跃

- **算法事实**：`mean(v_rel on miss)` = miss 段目标屏幕速度（与玩家准星屏幕速度的差，准星近常数）。
- **解读跳跃**："高速段失手多"——v_rel 高 miss 多 ⇒ 玩家在目标快时失手。**合理但有跳跃**：
  - 跳跃 1：miss 帧采样偏差——只看 miss 段，目标速度高或许是 miss 的原因或许是结果。
  - 跳跃 2：v_rel 含 v_c 噪声（平滑/微分引入）。
- **narrator 措辞**："miss 段目标速度 X px/s，提示高速段可能失手多"——而非 "你高速段失手"。
- **规则处理**：`speed_mismatch_high` severity 默认 "watch"。

### 7.4 总结：哪些是算法事实 vs 假设

| 项 | 算法事实（可复算） | 解读假设（需验证） |
|---|---|---|
| `ptc` 数字 | ✅ | "PTC 高 = 张力大" ❌ 待 EMG |
| `speed_mismatch` 数字 | ✅ | "高速段失手多" ⚠️ 合理跳跃 |
| `on_target_pct` 数字 | ✅ | "整体追踪控制不足" ✅ 金标准 |
| `loss_count` 数字 | ✅ | "速度匹配跟不上" ⚠️ 合理推断 |
| `avg_error_px` 数字 | ✅ | "临界命中多" ✅ 直接 |
| `total_off_time` 数字 | ✅ | "回位慢" ✅ 直接 |

---

## 8. 未决问题（需要点点决定）

### 8.1 真实数据校准（v1 阻塞）

`tracking_accuracy_low=70` / `tracking_loss_count_high=60` / `tracking_off_target_long_s=0.05` / `tracking_avg_error_ratio=0.5` 这四个初始值**都没有点点自己的真实数据支撑**。speed/accel/ptc 三个阈值连初始值都没法给。

- **方案 a（推荐）**：点点提供若干段自己的 VT tracking 录像（precise / control / reactive 各跑一遍），跑 `analysis.run_analysis`，看自己指标分布，用"主观感受 + 录像回放"标定初版阈值。
- **方案 b**：先 ship v1 用保守阈值 + 显式 "info" severity（不诊断只提示），积累数据后再调。
- **memory**：点点有 DPI1600/48cm360/FOV103 配置（`memory/user-aim-config.md`），VT 录像应该可跑。

### 8.2 SPARC 等时序派生（v2）

`tracking_sparc_low` / `trailing_micros` / `directional_bias` 都基于 frame_errors.csv 的全帧时序——数据已有，不需要新 CSV。前置只是阈值标定 + 派生函数。可在 v1.1 / v2.0 阶段做。

### 8.3 tracking reference 模式（self-only vs 跨人）

flicking 有高手 reference summary（`memory/ref-trajectory-csv-convention.md`）。tracking 是否引入同样的对比表？

- **挑战**：tracking summary 是标量，不是分布。reference 用"命中率 vs 高手命中率"这种点对比意义有限（高手命中率 95%，玩家 75%——已知差距，无须系统算）。
- **可能更有价值的对比**：基于"理想速度匹配模型"算 gap（如 v_rel 在目标速度区间上的偏离），而非跨人。
- **问点点**：v1 要不要 reference？还是先 self-only？**推荐 self-only**。

### 8.4 CLAUDE.md / AGENTS.md 文档对齐（已解决）

CLAUDE.md / AGENTS.md 的 "J/E Ratio / TBR 是核心理论" 与代码不符——是已记录的债（`memory/tracking-tbr-not-implemented.md`、README L77 自我否认）。**已解决**：AGENTS.md 已 2026-07-07 删除（过时双胞胎），CLAUDE.md 已更新为权威源并修正措辞（见 CLAUDE.md "理论状态"段：PTC 命名误导、J/E Ratio / TBR 已确认不成立）。

### 8.5 dashboard.py 已删除

dashboard.py 已在 Phase 1B 删除，scope 已扩为 flicking + tracking 双主线。tracking coach v1 输出走 webapp frontend / coach Plotly figures + agent narrator，不走 Streamlit。

---

## 附：与前一稿的修正对照（一行 summary）

| 前一稿的判断 | 新稿的修正 |
|---|---|
| `speed_mismatch` 是 "bug-shaped"，要改名 `target_speed_on_miss` | 算法在跑且命名准确（v_rel 是 mismatch），保留原名 |
| 准星硬编码画面中心是 "根 bug" | 是设计——flicking.py L4-5 明示 KovaaK 视角平移模型 |
| PTC / speed_mismatch 是 "aspirational" | 算法在跑写真数字；aspirational 只形容解读层（"PTC 高=张力大"） |
| TBR / J-E "从 v1 完全移除" | TBR v1 不实装但保留为解读层假设（§7.2）；J-E = PTC 别名 |
| `tracking_sparc_low` 前置 "真实准星轨迹" | frame_errors.csv 时序已够，前置是阈值标定 |
| 候选 signal 4 条（accuracy/loss/off_time/avg_error） | 扩到 7 条（加 speed/accel/ptc 三条），pacing 一致 |

---

## 工作量估算

| 阶段 | 工作量（人时） | 说明 |
|---|---|---|
| v1.0 实装（advice_tracking + report 分流 + summary normalize + 4 signal） | 4-6h | 含 THRESHOLDS dict + advise_tracking 函数 + report 改 summary_type 分流 + 单测 |
| v1.0 测试（构造 fake tracking summary，验证 4 signal 触发 + 不触发） | 2-3h | 仿 advice.py 的测试风格 |
| v1.1 profiles + knowledge 接入（archetype + root_causes + knowledge dict） | 2-3h | profiles.py / knowledge.py 数据追加 |
| 真实数据校准（跑自己 VT 录像 + 调阈值） | 2-4h | 取决于录像数量 |
| **v1 总计** | **10-16h** | 不含 narrator 文案打磨（另算） |

narrator 文案打磨 + agent prompt 适配 tracking signal 词表：另 4-6h（在 v1.1 / v1.2 阶段）。
