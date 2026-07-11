# 06 — flicking 指标理论对应 review

> 审查范围：`kovaak_tracker/flicking.py::compute_fair_metrics` 产出的 12 个 `FlickFairMetrics` 字段 vs 学术定义（Balasubramanian 2012 / Fitts 1954 / Novak 2002 / Meyer 1988 / Becker 2020 / Flash & Hogan 1985）。
> 学术锚点底稿：`docs/aim-kinematics-research.md` §2/§6。
> 文档声称底稿：`docs/flicking-aim-coach.md` §4.3 + `CLAUDE.md`「关键算法 → flicking」段。
> 审查方式：只读；逐指标比对"实现 vs 论文公式 vs 文档声称"。

---

## 总览（12 指标）

| # | 指标 | 实现位置 | 学术锚点（文档声称） | 实现忠实度 | 严重度 |
|---|---|---|---|---|---|
| 1 | `sparc` | `flicking.py:462-497` | Balasubramanian 2012 | **公式对，缺加窗** | 低 |
| 2 | `throughput` | `flicking.py:585-591` | Fitts 1954 / Shannon form | **公式对，W 全局中位数** | 低 |
| 3 | `submovement_overlap` | `flicking.py:500-533` | Novak 2002 overlapping | **命名误导（trough ratio ≠ time overlap）** | 中 |
| 4 | `corrective_count` | 同上 | Woodworth/Meyer/Schwartze 2024 | **阈值比学术宽**（抓得过多） | 低-中 |
| 5 | `decel_frac` | `flicking.py:569` | Becker 2020 不对称钟形 | **正确** | 低 |
| 6 | `linearity` | `flicking.py:558-564` | constant-deceleration（非 min-jerk） | **正确，归因已修正** | 低 |
| 7 | `path_efficiency` | `flicking.py:578-582` | min-jerk 点对点直线 | **正确** | 低 |
| 8 | `reverse_ratio` | `flicking.py:567-568` | 单调制动 | **正确** | 低 |
| 9 | `peak_speed_deg` | `flicking.py:597` | 速度-精度权衡 | **量纲近似（x/y 各向同性）** | 中 |
| 10 | `peak_position_pct` | `flicking.py:571` | min-jerk 50% / aim 35-50% | **正确** | 低 |
| 11 | `endpoint_peak` | `flicking.py:570` | 谷切分下终点=谷 | **正确（但区分度有限）** | 低 |
| 12 | `path_length_deg` / `direction_deg` | `flicking.py:582-584` | 路径几何 | **量纲同 #9 近似** | 中 |

**计数**：低严重度 8、中严重度 3、高严重度 0。无"实现错了"级别的真 bug；3 条中严重度均为"命名/量纲近似未在文档诚实承认"。

---

## 1. SPARC（频域平滑度，Balasubramanian 2012）

### 学术定义（论文公式）

Balasubramanian et al. 2012（IEEE TBME 59:2126）— Spectral Arc Length（SALTM/SPARC）：

$$
\text{SPARC} = -\int_0^{f_c} \sqrt{1 + \left(\frac{d\hat{V}(\omega)}{d\omega}\right)^2}\, d\omega
$$

其中：
- $\hat{V}(\omega) = |V(\omega)| / V(0)$ — 速度幅度谱，**DC 归一化**（使 $\hat{V}(0)=1$）
- $f_c$ = 自适应截止频率：最大频率使 $\hat{V}(\omega) > \epsilon$（默认 $\epsilon = 0.05$）
- 离散化：弧长 ≈ $-\sum \sqrt{\Delta\omega^2 + \Delta\hat{V}^2}$
- 值域：负数，**越接近 0 越平滑**（直线谱弧长 = 0）

参考实现：[siva82kb/SPARC MATLAB](https://github.com/siva82kb/smoothness) — 用矩形窗（裸 FFT）、DC 归一化、$\epsilon=0.05$。

### 实现（`flicking.py:462-497`）

```python
spectrum = np.abs(np.fft.rfft(speed))          # V(ω)
dc = spectrum[0]
if dc <= 0: return NaN
spectrum = spectrum / dc                        # DC 归一化
freqs = np.fft.rfftfreq(n, d=1.0 / fps)
above = np.where(spectrum > amp_th)[0]          # amp_th=0.05
if above.size == 0 or above.max() < 2: return NaN
fc = int(above.max())
f_v = freqs[1:fc + 1]
V_v = spectrum[1:fc + 1]
return -np.sum(np.sqrt(np.diff(f_v) ** 2 + np.diff(V_v) ** 2))
```

### 核对结果

| 维度 | 论文 | 实现 | 判定 |
|---|---|---|---|
| FFT 类型 | rfft（real signal） | `np.fft.rfft` | ✓ |
| 归一化 | /V(0)（DC） | `/= spectrum[0]` | ✓ |
| amp_th ε | 0.05 | `amp_th=0.05` | ✓ |
| 弧长公式 | $-\sum\sqrt{\Delta\omega^2 + \Delta\hat{V}^2}$ | 同 | ✓（数学等价） |
| 截止频率 fc | 最大频率使 $\hat V>\epsilon$ | `above.max()` | ✓ |
| 窗函数 | 矩形（裸 FFT，论文原版） | 无加窗 | ✓ 与原版一致 |
| 应用范围 | 整段速度曲线 | 整段 seg_speed | ✓ |

### 偏差

**A. 短 flick NaN 率高**（已知，非 bug）
- 根因：`n < 8` 守卫（4 帧 Nyquist 都不够）+ `fc < 2` 守卫（只有 DC 一个频率分量无法算弧长）。
- 量级：60fps 下 < 16 帧（< 0.27s）的 flick 不贡献 SPARC 样本。
- 性质：频域方法的固有采样限制，不是实现缺陷。docstring 已诚实记录（`flicking.py:478-479`）：`"This is a known sampling limitation of the frequency-domain method, not a bug; a time-domain fallback is research scope"`。
- **建议**：保留。文档已足够诚实。

**B. 缺加窗的 spectral leakage**（轻微未承认）
- 学术原版用矩形窗，但实践改进（Hann/Hamming）能减少短段 spectral leakage。
- 对 < 30 帧的 flick，矩形窗会让能量泄漏到相邻频率，**虚高弧长**（更负的 SPARC = 看起来更抖）。
- 性质：与论文原版一致，**不是偏差**；只是没有采用工程改进。
- **建议**：docstring 加一行说明"用矩形窗（与 Balasubramanian 原版一致），未做 Hann 加窗；短段可能有 spectral leakage"。可选。

### 严重度：**低**

核心公式数学正确，归一化、截止频率、弧长离散化都对。唯一缺点是未加窗的工程改进，但与论文原版一致。短段 NaN 已诚实文档化。

---

## 2. Fitts throughput（bits/s）

### 学术定义

Fitts 1954（J Exp Psychol 47:381）+ MacKenzie 1989 Shannon form：

$$
MT = a + b\cdot ID,\quad ID = \log_2\left(\frac{D}{W}+1\right)\ \text{(Shannon)}
$$

$$
TP = ID / MT \quad (\text{bits/s})
$$

- $D$ = 运动距离（amplitude，到目标中心）
- $W$ = 目标宽度（nominal：物理宽度；**effective** $W_e = 4.133\cdot SD_x$ 更严谨，需端点分布）
- $MT$ = 运动时间

文档声称（`flicking.py:586-587` + research.md §6.3）：用 nominal-W Shannon form，effective-W 属后续。

### 实现（`flicking.py:585-591`）

```python
if (target_width_deg and target_width_deg > 0 and duration_s > 0
        and straight_px > 0):
    D_deg = straight_px * deg_per_px
    throughput = float(np.log2(D_deg / target_width_deg + 1)) / duration_s
```

`target_width_deg` 来自 `pan_tracker.py:409-411`：
```python
target_width_deg = float(np.median(widths_px)) * deg_per_px
```
即所有帧所有检测目标的 bbox 宽度的全局中位数 × deg_per_px。

### 核对结果

| 维度 | 论文 | 实现 | 判定 |
|---|---|---|---|
| ID 公式 | $\log_2(D/W+1)$（Shannon） | 同 | ✓ |
| TP = ID/MT | 是 | 是 | ✓ |
| D 语义 | 到目标中心的距离 | 起终点直线距离 | **近似**（见下） |
| W 语义 | 目标宽度（nominal） | 全局中位数 bbox 宽度 | **近似**（见下） |
| MT | 运动时间 | `duration_s` | ✓ |

### 偏差

**A. D 用起终点直线距离**（合理近似）
- Fitts D 严格是"运动起点到目标中心的距离"。
- 代码用 `straight_px`（flick 起终点直线距离）。对 1w6ts（每次 flick 都从屏幕中心到目标），起点≈画面中心、终点≈目标位置，起终点直线 ≈ D。
- 偏差场景：玩家欠冲/过冲时，终点 ≠ 目标中心，起终点直线 < 真实 D。属可接受近似。
- **建议**：保留。docstring 已说明"D the start->end amplitude"。

**B. W 用全局中位数**（对 1w6ts 正确，对变大小目标错）
- `pan_tracker.py:411` 取**所有帧所有目标**的 bbox 宽度中位数。
- KovaaK click-timing 场景（1w6ts / Tile Frenzy）目标大小一致 → 中位数 = 真实 W。✓
- 变大小目标场景（e.g. Pasu 远距离目标变小）会错。但 flicking-aim-coach.md §3 说明主线是 1w6ts 类，假设成立。
- docstring（`pan_tracker.py:406-408`）已说明："KovaaK click-timing targets are uniform size"。
- **建议**：保留。已诚实文档化。

**C. W 用 bbox 宽度而非视角直径**
- KovaaK 目标是球形，bbox 宽度 ≈ 直径。对正圆目标近似无偏差。
- 但视角投影下，屏幕边缘的目标会被透视拉伸（非正圆），bbox 宽 > 直径。轻微正偏差（D/W 偏小 → ID 偏小 → TP 偏低）。
- **建议**：保留。偏差 < 5%，可接受。

**D. effective target width（$W_e = 4.133\cdot SD_x$）未实现**
- 文档已承认（`flicking.py:401-402` docstring + research.md §6.3）：需多次击中同类目标的端点分布，属后续 PROGRESS [C]。
- **建议**：保留为后续。

### 严重度：**低**

所有偏差都是"合理近似 + 文档已承认"。核心公式正确。

---

## 3. submovement_overlap（Novak 2002 overlapping submovements）

### 学术定义

**Novak et al. 2002**（Exp Brain Res 144:351）— Overlapping submovements：
- 两个 submovement 在**时间窗上重叠**（一个未结束另一个已开始），融合成连续速度曲线 = **流体**
- Discrete submovements：速度峰之间有明显的速度低谷（可分离）= **两段式**
- 量化："overlap"在 Novak 原文中是 submovement **时间窗的重叠比例**（primary 末端与 corrective 起点的时间交集 / primary 时长）

切分标准（Rouse 2022 / Schwartze 2024，见 research.md §6.2）：
1. 速度峰
2. ±200ms 内无更大峰
3. peak prominence ≥ **50% of adjacent troughs**
4. initial 至少移动到目标一半距离

### 实现（`flicking.py:500-533`）

```python
hi = min(len(speed), peak_idx + int(window_s * fps))   # window_s=0.4
tail = speed[peak_idx:hi]
peaks, _ = find_peaks(
    tail, prominence=peak_v * 0.2, distance=max(1, int(0.08 * fps))
)
peaks = [pk for pk in peaks if tail[pk] < peak_v * corr_frac]  # corr_frac=0.7
# overlap 定义：
trough = float(tail[1:first].min())
return len(peaks), float(trough / peak_v)
```

### 偏差

**A. `overlap` 命名误导（重要）** ⚠️
- Novak 2002 overlap = **时间重叠比例**（time-window intersection / primary duration）
- 代码 overlap = **速度谷深度比**（trough / peak_v）
- 两者**直觉相关**（谷浅 ≈ 时间上重叠多），但**不是同一量**。
- 性质：命名误导。文档（`FlickFairMetrics` docstring + research.md §6.2）说"overlap high = overlapping/fluid, low = discrete/two-stage"，**描述的是 trough ratio 的行为**，不是 Novak 的 time overlap。
- **类比**：这和 PTC 命名误导（"Pure Tension Coeff" 实为加速度密度）同型——**实现合理，名字错**。
- **建议**：要么 (a) 重命名为 `trough_ratio` / `corrective_dip_ratio`，要么 (b) 在 `FlickFairMetrics` docstring 顶部明确写"命名为 submovement_overlap 但实为 trough depth ratio（Novak time-overlap 的 proxy）"。最低成本是 (b)。

**B. prominence 阈值偏低**（抓得过多）
- 学术标准：prominence ≥ **50%** of adjacent troughs（即谷深 ≥ 主峰 × 0.5）
- 代码：`prominence = peak_v * 0.2`（20%）
- 影响：会把高频抖动也算 corrective，`corrective_count` 偏高。
- 但 `corr_frac=0.7` 过滤（只保留峰值 < 主峰 × 0.7 的）部分抵消——意味着"小 corrective"才算。
- **建议**：要么调到 0.5（严格按学术），要么 docstring 明确"用 20% prominence 抓宽 corrective（含 micro-jitter），区别于学术 50%"。文档现在没写这层区别。

**C. distance 阈值偏小**（多分 corrective）
- 学术 ±200ms 内无更大峰
- 代码 `distance = max(1, int(0.08 * fps))` = 80ms
- 影响：相邻 corrective 间隔 < 80ms 就合并；学术是 < 200ms 合并。代码更严格（合并少），可能多分。
- **建议**：文档化或调到 200ms。

**D. initial/corrective 区分不严格**
- 学术：primary = initial（ballistic，至少移动到目标一半距离）；corrective = initial 之后所有 submovement。
- 代码：默认 segment 内最大峰 = primary，其后窗口内所有小峰 = corrective。
- 偏差场景：valley 切分可能让 corrective 段单独成段，那段的最大峰其实是 corrective，但仍被当 primary 处理。
- 文档承认（`flicking.py:506-510`）：主峰后固定窗口"独立于 valley 切分"——缓解了但不能完全消除。
- **建议**：保留。是已知局限。

### 严重度：**中**

主要是 #A 命名误导（与 PTC 同型问题），其次是 #B/#C 阈值偏差未文档化。

---

## 4. corrective_count（Woodworth/Meyer/Schwartze）

### 学术定义

- **Woodworth 1899**：initial (ballistic, 前馈) + corrective (视觉反馈修正)
- **Meyer 1988**：optimized submovements — 总 MT 最小化下的 primary+secondary 相对时长
- **Schwartze/Rouse 2024**：corrective 在 M1 用不同神经子空间编码，gain 1.14-1.36×（区分的神经证据）

切分标准：见 #3。

### 实现

同 #3，`len(peaks)` 即 corrective 数。

### 偏差

- 同 #3 的 #B（prominence 20% vs 学术 50%）和 #C（distance 80ms vs 学术 200ms）。
- 代码会**多抓** corrective（高频微抖也算），`corrective_count` 系统性偏高。
- 文档说"少 = 好"（`flicking-aim-coach.md` §4.3 "corrective_count: 少 = 好"），未量化阈值——这避免了"伪精度"，反而更宽容。

### 严重度：**低-中**

阈值偏差但 advice.py 未用绝对阈值（`compare_table` 标 `info`，不触发 finding），所以**实际诊断不受影响**。

---

## 5. decel_frac（减速段占运动时间比）

### 学术定义

Becker 2020：aim 速度曲线**不对称钟形**，减速段 > 加速段。健康 aim decel_frac ∈ 0.50-0.65（research.md §2）。

### 实现（`flicking.py:569`）

```python
decfrac = (e - p) / max(1.0, (e - s))
```
`e` = 段尾，`p` = 峰位，`s` = 段首。

### 核对

- 分子 `(e - p)` = peak 到 end 的帧数 = 减速段时长
- 分母 `(e - s)` = start 到 end 的帧数 = 总时长
- 比值 ∈ [0, 1]
- ⚠️ **隐含假设**：速度曲线单峰（s 到 p 单调上升 = 加速段；p 到 e 单调下降 = 减速段）。
- 多峰 flick（two-stage）会被误算：s 到 primary-peak 之间如果有 pre-peak dip，会被算成"加速段"。

### 偏差

- 单峰假设对 ~70% flick 成立（健康的 fluid flick）。对 two-stage flick 偏差小（pre-peak 抖动通常 < 5 帧）。
- **建议**：保留。简化合理。

### 严重度：**低**

---

## 6. linearity（减速段线性度）

### 学术定义

文档（research.md §6.1）：减速段速度对**匀减速直线**（constant-deceleration）拟合的归一化 RMSE，/peak。

**重要**：理论锚点是 **constant-deceleration**（恒定制动），**不是 min-jerk**。min-jerk 减速段（τ ∈ [0.5, 1]）是平滑曲线 $v(\tau) \propto 30\tau^2(1-\tau)^2$，偏离匀减速直线。一个完美的 min-jerk 减速反而会得到较差的 linearity 分数。

### 实现（`flicking.py:558-564`）

```python
if len(decel) >= 3:
    t = np.arange(len(decel))
    fit = np.polyfit(t, decel, 1)           # deg=1 直线拟合
    resid = decel - np.polyval(fit, t)
    linearity = float(np.sqrt(np.mean(resid ** 2)) / peak_v)
```

### 核对

- ✓ polyfit deg=1 = 直线 = 匀减速（恒定负加速度）
- ✓ RMSE = $\sqrt{\text{mean}(v - \hat v)^2}$
- ✓ /peak_v = 归一化（无量纲、跨速度公平）
- ✓ peak_v 是 segment peak = 减速段起点速度 = 正确归一化基准

### 偏差

**无实质偏差**。归因（constant-deceleration）在 docstring + research.md §6.1 已修正——这是 2026-06-28 深化的关键结论。

唯一细节：`/peak_v` 用的是 segment 全局 peak，不是 decel 段起点（= 同一个，因为 decel = speed[p:e+1]，p 就是 peak 帧）。等价。

### 严重度：**低**（无偏差）

---

## 7. path_efficiency（起终点直线 / 实际路径）

### 学术定义

min-jerk 假设点对点直线运动；path efficiency = straight/actual ∈ [0,1]，1=完美直线。

### 实现（`flicking.py:578-582`）

```python
seg_len = float(np.sum(np.hypot(np.diff(xs), np.diff(ys))))
straight_px = float(np.hypot(xs[-1] - xs[0], ys[-1] - ys[0]))
if seg_len > 0:
    path_eff = straight_px / seg_len
```

### 核对

- ✓ 分子 = 起终点 Euclidean 距离
- ✓ 分母 = 路径长度（逐帧 |Δ| 之和）
- ✓ 比值 ∈ [0, 1]
- ✓ 用 `ball_x/ball_y`（合成 pan 轨迹）= 视角平移路径

### 偏差

**无实质偏差**。

### 严重度：**低**（无偏差）

---

## 8. reverse_ratio（减速段反向加速帧占比）

### 学术定义

单调制动 = 减速段加速度恒为负。reverse_ratio = 减速段正加速度帧占比，低 = 单调，高 = 锯齿。

### 实现（`flicking.py:567-568`）

```python
da = accel[p:e + 1]
reverse = float(np.mean(da > 0)) if len(da) else float("nan")
```

### 核对

- ✓ `da` = 减速段加速度
- ✓ `da > 0` = 正加速度（与减速方向相反 = 反向加速）
- ✓ `mean(da > 0)` = 反向帧占比
- ✓ `accel` 已 apply_smoothing 过（`pan_tracker.py:399-403`），所以 da > 0 是真实方向变化不是噪声

### 偏差

**无偏差**。accel smoothing 后 reverse_ratio 是稳健的。

### 严重度：**低**（无偏差）

---

## 9. peak_speed_deg（峰值角速度）

### 学术定义

峰值角速度（°/s）—— 速度-精度权衡（Fitts）中的速度维度。

### 实现（`flicking.py:597`）

```python
peak_speed_deg=round(peak_v * deg_per_px, 2),
```
其中 `deg_per_px = fov / meta.width`（`pan_tracker.py:323`），`fov` 默认 103（KovaaK 水平 FOV），`meta.width` 是视频水平像素。

### 偏差

**A. x/y 各向同性近似**（未文档化） ⚠️
- `peak_v = np.hypot(vx, vy)`（`_ball_speed`），vx/vy 都是 px/s。
- 乘以单个 `deg_per_px` 假设水平/垂直角分辨率相同。
- 实际上 KovaaK 103° 是**水平 FOV**；垂直 FOV 由宽高比决定（16:9 → ~68°）。
- 水平 deg_per_px = 103/1920 ≈ 0.054 °/px
- 垂直 deg_per_px = 68/1080 ≈ 0.063 °/px（大 ~17%）
- 对纯水平 flick：peak_speed_deg 正确。
- 对纯垂直 flick：被低估 ~14%。
- 对 45° 斜 flick：被低估 ~7%。
- **这是量纲近似**，文档（`flicking.py:405-407` docstring 说"Angular quantities use deg_per_px = FOV / width so they are comparable across resolution and sensitivity"）—— 说"跨分辨率可比"是对的，但**没说"对方向敏感"**。
- 影响：peak_speed_deg 对比 self vs ref（同方向分布）OK；跨玩家方向分布不同时有系统偏差。

**B. `cm_per_deg` 换算继承同样偏差**
- `_summarize_reference` 里 `peak_cm_per_s = peak_speed_deg × cm_per_deg`，cm_per_deg 来自 cm_per_360 / 360。
- cm_per_360 是水平旋转 360° 的物理距离，对应**水平**角速度。把它用于合成的 hypot(vx,vy) 角速度，量纲上不严格。
- 但这是次级偏差（peak_speed_deg 已经是近似的 hypot）。

### 严重度：**中**

**建议**：
- 最小修复：`FlickFairMetrics.peak_speed_deg` docstring 加一行"假设 x/y 各向同性（实际水平 FOV/垂直 FOV 比 ~1.17，斜向 flick 低估 ~7%）"。
- 理想修复：在 `_ball_speed` 内把 vx, vy 分别转 °/s（用不同 deg_per_px），再 hypot。

---

## 10. peak_position_pct（峰位 %）

### 学术定义

min-jerk 峰在 50%；aim 典型 35-50%（Becker 2020 不对称钟形）。

### 实现（`flicking.py:571`）

```python
peak_pos = round(100.0 * (p - s) / max(1, (e - s)), 1)
```

### 核对

- ✓ 分子 = start 到 peak 的帧数
- ✓ 分母 = 总帧数
- ✓ × 100 = 百分比
- ✓ 值域 [0, 100]，50% = min-jerk 理想

### 偏差

**无**。

### 严重度：**低**（无偏差）

---

## 11. endpoint_peak（终点速度 / 峰值速度）

### 学术定义

文档（`flicking.py:415`）：valley speed / peak speed，低 = 减速充分（谷切分下终点=谷）。

### 实现（`flicking.py:570`）

```python
endpk = float(speed[e] / peak_v)
```

### 偏差

- 谷切分下终点 e = 速度谷，所以 endpoint ≈ flick 间最低速度。
- 文档（research.md §1）承认："谷切分下终点=谷，endpoint/peak 失去区分度"。
- 实际：advice 没对 endpoint_peak 设阈值，`compare_table` 里它是 `_LOWER_BETTER`（lower = 更好），但**未在 advise() 中触发任何 finding**。是惰性字段。
- **建议**：保留为惰性指标（无害），或考虑移除（YAGNI）。

### 严重度：**低**（实现正确但区分度有限）

---

## 12. path_length_deg / direction_deg

### 实现

```python
path_len_deg = seg_len * deg_per_px      # 累积路径长度（°）
direction = np.degrees(np.arctan2(ys[-1] - ys[0], xs[-1] - xs[0]))  # 起终点方向（°）
```

### 偏差

- `path_len_deg` 继承 #9 的 x/y 各向同性近似（seg_len 是 px 累积，× 单 deg_per_px）。
- `direction_deg` 用原始像素坐标的 arctan2 —— **不是视角角度方向**。如果 x/y 角分辨率不同，方向角会被扭曲。
- 但 direction_deg 在 advice 里是惰性的（无 finding 触发，`_NO_VERDICT`），仅用于报告。

### 严重度：**中**（继承 #9）

---

## 理论扎实度总评

**评分**：8/10 — **扎实可信赖**，无真 bug。

**分项**：

| 维度 | 评分 | 说明 |
|---|---|---|
| 公式正确性 | 9/10 | SPARC/Fitts/linearity/reverse_ratio/decel_frac 公式数学正确 |
| 归因准确性 | 8/10 | linearity 已从 min-jerk 改 constant-deceleration；submovement_overlap 仍有命名误导 |
| 量纲一致性 | 7/10 | peak_speed_deg/path_length_deg 的 x/y 各向同性近似未文档化 |
| 文档诚实度 | 8/10 | 短 flick NaN、W 全局中位数、effective W 未实现都已承认；峰值速度各向同性未承认 |
| 实现简化合理性 | 9/10 | 简化都指向可计算的工程量，无伪精度 |

**与 tracking 模块对比**：flicking 模块的理论扎实度**显著高于** tracking。tracking 的 PTC 是"命名误导 + 实现 vs 命名不符"（speed_mismatch 实际只描述目标运动）；flicking 的所有指标**实现都对得上名字**，唯一例外是 `submovement_overlap`（实为 trough ratio，非 Novak time overlap）。

**诊断规则安全性**：advice.py 的诊断规则只用学术根基（CLAUDE.md「铁律」），社区经验进 narrator。即使 #3/#4 的 corrective 阈值偏宽（抓得过多），因为 `corrective_count` 在 advice 中只标 `info`（不触发 finding），实际诊断**不受影响**。这是设计上的容错。

---

## 最该修的理论偏差 Top 3

### Top 1: `submovement_overlap` 命名误导（中严重度）
- **问题**：名为 Novak 2002 time overlap，实为 trough depth ratio（trough / peak_v）。
- **同型问题**：与 tracking 的 PTC（"Pure Tension Coeff" 实为加速度密度）同型——**实现合理，名字错**。
- **影响**：理论审查者会误以为实现了 Novak 的 time-window overlap 量化；实际是 proxy。诊断不受影响（advice 用 `< 0.3` 阈值，是经验校准的，不依赖量的物理意义）。
- **修复成本**：
  - 最小（5 行）：`FlickFairMetrics.submovement_overlap` docstring 顶部加"**命名为 submovement_overlap 但实为 trough depth ratio**（Novak 2002 time-overlap 的 proxy：trough 浅 ≈ 时间重叠多）"。
  - 理想：重命名为 `trough_ratio` 或 `corrective_dip_ratio`（需改 advice.py + visualization + 测试，~30 处引用）。
- **建议**：先做最小修复（docstring），重命名留 v2 重构。

### Top 2: `peak_speed_deg` / `path_length_deg` 的 x/y 各向同性近似（中严重度，未文档化）
- **问题**：`deg_per_px = FOV / width` 用水平 FOV，但 `_ball_speed` 用 `np.hypot(vx, vy)` 合成速度。垂直方向角分辨率大 ~17%，斜向/垂直 flick 被系统性低估。
- **影响**：
  - self vs self 对比：无影响（同方向分布）。
  - self vs ref 对比：若两人方向偏好不同（e.g. 一人水平多、一人斜向多），有 ~7-14% 系统偏差。
  - `path_efficiency` / `direction_deg` 也涉及，但前者是比值（x/y 近似抵消）、后者是惰性字段。
- **修复成本**：
  - 最小（3 行）：`FlickFairMetrics.peak_speed_deg` docstring 加"假设 x/y 各向同性（水平 FOV/垂直 FOV 比 ~1.17；垂直 flick 低估 ~14%、45° 低估 ~7%）"。
  - 理想：`_ball_speed` 内分别转 `vx_deg = vx × (FOV_h/W)`、`vy_deg = vy × (FOV_v/H)`，再 hypot。需 `fov_v` 参数（从 `meta.height` 和宽高比推）。
- **建议**：先做最小修复。理想修复在 v2 加 `fov_v` 时一起做。

### Top 3: `corrective_count` 阈值偏差（低-中严重度，已通过设计容错）
- **问题**：`_submovement_structure` 的 `prominence = peak_v × 0.2`（学术 50%）和 `distance = 0.08s`（学术 0.2s）—— 会多抓 corrective。文档未说明与学术标准的差异。
- **影响**：
  - `corrective_count` 系统性偏高。
  - 但 advice.py 中 `corrective_count` 标 `_NO_VERDICT`（info，不触发 finding），**诊断不受影响**。
  - `submovement_overlap` 的 trough 是从这组 peaks 取的，所以也连带受影响（trough 位置依赖第一个 corrective）。
- **修复成本**：
  - 最小（5 行 docstring）：`_submovement_structure` 加注释说明"prominence 20% / distance 80ms（学术 50% / 200ms），抓得更宽——含 micro-jitter 作 corrective"。
  - 理想：调到学术标准（但需重新校准 advice 阈值）。
- **建议**：文档化即可。调阈值属后续数据校准。

---

## 附：其他次要观察（非理论偏差，供参考）

1. **`_ball_speed` forward-fill 在运动段会造 spike**（`flicking.py:281-292`）——forward-fill 缺失帧后，从 last_good 到恢复帧的 diff 会被 `np.gradient` 当成大跳。对 pan_tracker 合成轨迹（不写 NaN）影响小；对 CSV 模式若 ball_w=0 可能影响。是潜在 bug 但非本 review scope（指标理论）。
2. **`segment_by_valleys` 的 `peak_v < prom * 1.5` 守卫**（`flicking.py:454`）——丢弃峰值 < 全局峰 22.5% 的小段。合理（噪声过滤），但文档未说。
3. **`endpoint_peak` 是惰性字段**——valley 切分下终点=谷，区分度有限；advice 未用。考虑 YAGNI 移除（非本 review scope）。

---

## 结论

flicking 指标体系**理论扎实**，12 个指标中 9 个实现与学术定义一致或仅有合理简化，3 个有中严重度偏差（均属"命名/量纲近似未诚实文档化"，非实现错误）。无真 bug。

最该修的 1 条：**`submovement_overlap` 命名误导**——与 tracking PTC 同型问题，最小修复是 docstring 加注"实为 trough ratio"。理想是 v2 重命名。

诊断安全性高：即使 corrective 阈值偏宽，advice.py 的设计（`_NO_VERDICT` 标 info）让诊断**不受影响**。这是工程上的容错设计。

审查文件：`docs/review/2026-07-08/06-theory-flicking.md`（本文件）。
