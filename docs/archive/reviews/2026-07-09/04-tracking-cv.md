# Tracking + CV Review

> **日期** 2026-07-09 · **reviewer** tracking+CV agent · **scope** `analysis.py` / `tracking.py` / `vision.py` / `video.py` / `calibration_cli.py` / `advice_tracking.py` / `app.py` / `Analyze.py` / `calibrate.py`

## 健康度：B-（核心可用，遗留 3 高优先级未修 + 1 理论矛盾需裁决）

昨日（07-08）修复的两处关键问题（`sys.exit(1)` → `raise`、`get_video_metadata` try/finally）均已验证正确。但遗留三个高优先级问题（VideoWriter 泄漏、NaN 传播、HSV 环绕失效）和多个中低优先级问题未修复。

tracking-coach spec §1.1-1.3 对 `v_c=0` 的反驳存在论证漏洞：spec 称"逐帧采样≠常量"否认 CLAUDE.md 的 `v_c=0` 断言，但代码在常见路径（app.py）下 cross_pos 确为常量中心，advice_tracking.py:155 的注释也承认 v_rel 主导项是目标速度。

PTC/speed_mismatch/accel_mismatch 实现与命名关系的理论债已记录在 CLAUDE.md；J/E Ratio / TBR 在代码中无残留实现（确认 clean）。

---

## 修复验证（5a5bb84）

| 修复项 | 验证结果 |
|---|---|
| `analysis.py:194-197` `sys.exit(1)` → `raise FileNotFoundError` | ✓ 已修复。库函数不再杀宿主进程；`import sys` 已删除。 |
| `video.py:28-46` `get_video_metadata` try/finally | ✓ 已修复。`cap.release()` 移入 finally 块，所有异常路径均覆盖。 |

---

## Critical

无。昨日修复已关闭最严重的嵌入场景进程杀和 VideoCapture 泄漏路径。

---

## High

### H-1 · `calibration_cli.py:121` — VideoWriter 在 finally 外，异常时泄漏（昨日未修）

```python
# line 70-75 (try 块内)
writer = cv2.VideoWriter(str(out_path), ...)
# line 83-117 (try 块内，循环体)
for idx in range(max_frames):
    ...
    writer.write(vis)
# line 118-119 (finally)
    finally:
        cap.release()
# line 121 (finally 外!)
writer.release()
```

**问题**：`writer = cv2.VideoWriter(...)` 在 try 块内创建（line 70），但 `writer.release()` 在 finally 块外（line 121）。如果 for 循环（line 83-117）中任何帧抛异常（如 `detect_ball_by_color` 内部 cv2 错误、frame 为 None 时 `frame.copy()` 崩溃），cap 被 finally 释放，但 writer **永不释放**。

**影响**：VideoWriter 持有输出 mp4 文件句柄。Windows 上未释放的 VideoWriter 会锁住 `calibration_check.mp4`，用户无法删除/覆盖该文件，且进程结束后才由 OS 回收。

**建议**：将 `writer.release()` 移入 finally 块。需处理 writer 尚未创建的情况（select_color_interactive 在 line 50/53 抛 SystemExit 时 writer 未创建）：
```python
    finally:
        cap.release()
        if 'writer' in locals():
            writer.release()
```
或给 writer 初始化为 None，finally 里 `if writer is not None: writer.release()`。

---

### H-2 · `analysis.py:15` — `load_tracking_data` 只过滤 `ball_x` NaN，cross_x 为 None 时 NaN 传播（昨日未修）

```python
df["is_valid"] = df["ball_x"].notna()   # line 15 — 只检查 ball_x
valid_df = df[df["is_valid"]]            # line 16
```

**问题**：`calibration_cli.py` 的输出 CSV 中 `cross_x`/`cross_y` 可以是 None（`detect_point_by_color` 未检出十字线时）。但 `load_tracking_data` 只按 `ball_x` 过滤行——ball 检出但 cross 未检出的行通过过滤。

在 `extract_kinematics` 中：
```python
cx = apply_smoothing(group["cross_x"].values, window_size)  # 含 NaN
```
NaN 经 savgol_filter → calc_derivative → `dx = bx - cx`（NaN 传播）→ `error_px = np.hypot(dx, dy)` = NaN → `is_miss` 判断中 `np.abs(NaN) > threshold` = False → 最终 `avg_error_px`、`ptc` 等指标被 NaN 污染。

**影响**：走 calibration_cli → Analyze.py 路线时，如果十字线检测率低，所有指标变 NaN 垃圾值，无报错。走 tracking.py 路线（cross_pos 默认中心）不受影响。

**建议**：过滤条件改为同时检查 cross_x：
```python
df["is_valid"] = df["ball_x"].notna() & df["cross_x"].notna()
```

---

### H-3 · `vision.py:37-56` — `get_hsv_range` 永不产生 H 环绕区间，`_make_mask` 环绕处理为 dead code（昨日未修）

`_make_mask`（line 12-26）正确实现了 H 通道环绕：当 `hsv_lo[0] > hsv_hi[0]` 时拆两个 mask 做 OR。但 `get_hsv_range`（line 37-56）产生的 lo/hi 对 H 做了 clamp：
```python
np.array([max(0, h - tolerance_h), ...])     # lo
np.array([min(179, h + tolerance_h), ...])   # hi
```
当 h=2, tolerance_h=10：lo_h=0, hi_h=12 — 无环绕。
当 h=178, tolerance_h=15：lo_h=163, hi_h=179 — 无环绕。
当 h=0, tolerance_h=15：lo_h=0, hi_h=15 — **漏掉 165-179 的同色区域**。

**影响**：红色/品红色目标（H≈0 或 H≈179）的 HSV 范围永远只覆盖 0/179 边界的一侧。`_make_mask` 的环绕分支永远不被触发。KovaaK's 目标通常非红色，影响有限，但用户手动采色时可能踩到。

**建议**：`get_hsv_range` 中，当 `h - tolerance_h < 0` 或 `h + tolerance_h > 179` 时，主动产生环绕区间（让 lo_h > hi_h），使 `_make_mask` 环绕分支生效。

---

## Medium

### M-1 · `tracking.py:87-94` — CSRT 失败后从单帧 HSV 检测重初始化，无误检确认（昨日未修）

```python
if not tracking_active:
    detected_pos, detected_w, detected_h = detect_ball_by_color(...)
    if detected_pos and detected_w and detected_h:
        ...
        tracker = get_tracker(warn_callback)
        tracker.init(frame, bbox)
        tracking_active = True
```

**问题**：CSRT 跟踪失败后，下一帧用 HSV 检测重新初始化。如果该帧有同色干扰（UI 元素、背景物体），HSV 检测返回假阳性位置，CSRT 锁定错误目标，后续帧全部追踪错误目标。没有"连续 N 帧检测一致才切换回 tracking"的确认机制。

**影响**：在色彩干扰场景下，单次 HSV 假阳性就使整段追踪偏移，错误数据静默进入 CSV → analysis → coach 诊断。无告警。

**建议**（低成本）：重初始化前要求连续 2-3 帧 HSV 检测位置一致（距离 < ball_w/2），才切换回 tracking。或至少在 warn_callback 里报一条 "CSRT re-initialized from HSV detection at frame X"。

---

### M-2 · `calibration_cli.py:27-36` — `select_color_interactive` 窗口被 OS 关闭时死循环（昨日未修）

```python
while True:
    key = cv2.waitKey(20) & 0xFF
    if key == 13 and param["picked"]:   # Enter
        ...
    if key == 27:                        # ESC
        ...
```

**问题**：循环只响应 Enter 和 ESC。如果用户通过 OS 窗口关闭按钮（×）关闭 OpenCV 窗口，`cv2.waitKey` 在 Windows 上会持续返回 -1，不匹配任何退出条件，进入死循环。

**影响**：用户误关窗口 → calibration_cli 无限挂起，只能强制结束进程。

**建议**：加窗口存活检查：
```python
while True:
    if cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) < 1:
        raise SystemExit("Window closed.")
    key = cv2.waitKey(20) & 0xFF
    ...
```

---

### M-3 · `tracking-coach spec §1.1-1.3` — 对 `v_c=0` 的反驳存在论证漏洞

spec §1.3 声称：
> CSV 中 `cross_x/y` 是逐帧采样（不是常数标量），**所以 `v_c` 通常 ≠ 0**——前一稿"中心是常数"判断对 raw 数据成立，但对经过 Savitzky-Golay 平滑 + 数值微分的导数是噪声级非零。

**问题分析**：
1. `tracking.py:57` 默认 `cross_pos = (metadata.width // 2, metadata.height // 2)` — 确为常量中心
2. 只有当 `crosshair_hsv_lo/hi` 提供时（lines 72-78），cross_pos 才会更新
3. `app.py` 不提供 crosshair HSV bounds（lines 165-168 明确注释），所以 **app.py 路径下 cross_pos 是常量中心**
4. `advice_tracking.py:155` 注释：`"v_rel 含准星噪声，主导项是目标速度"` — 代码承认 v_rel 主导项是目标速度，即 `v_c ≈ 0`

**结论**：spec 用"逐帧采样≠常量"否认 CLAUDE.md 的 `v_c=0` 断言，但：
- 对 app.py 路径（常见 Streamlit UI）不成立，cross_pos 确为常量
- 对 calibration_cli 路径（检测启用）可能成立，但依赖检测成功率
- 代码注释（advice_tracking.py:155）支持 CLAUDE.md 位置

**建议**：spec 应承认实现现实：
1. app.py 路径（UI 模式）：`v_c = 0`（center default），speed_mismatch ≈ target_speed
2. calibration_cli 路径（检测模式）：`v_c` 可能 ≠ 0（如果检测成功）
3. 结论保持不变：speed_mismatch/accel_mismatch 在 **常见路径**（app.py）下主要描述目标运动，不描述玩家追踪误差

**或**：改实现而非改 spec — 在 tracking.py 中即使未提供 crosshair HSV，也用某种方式估计准星运动（如从目标运动反向推视角），使 v_c 真正反映玩家追踪误差。但这是 v2 级别改动。

---

### M-4 · `analysis.py:188` — FPS fallback 用相对路径 `Path("output/calib_config.json")`

```python
config_path = Path("output/calib_config.json")   # 相对于 CWD
```

**问题**：`output_dir` 参数默认为 `OUTPUT_DIR`（`Path("output")`，也是相对路径）。但 `run_analysis` 读 config 时硬编码 `"output/calib_config.json"` 而非 `output_dir / "calib_config.json"`。如果 CWD 不是项目根目录（如从 webapp/ 目录调用），config 永远找不到。

**影响**：非项目根目录调用时 fallback 路径失效，触发 `FileNotFoundError`（已从 sys.exit(1) 改为 raise，但仍是错误路径）。

**建议**：`config_path = output_dir / "calib_config.json"`。

---

## Low

### L-1 · `app.py:85-104` — VideoCapture 无 try/finally（scope 外，备注）

```python
cap_play = cv2.VideoCapture(video_path)   # line 85
...
for i in range(0, process_length, step):
    ret, p_frame = cap_play.read()
    ...
cap_play.release()                         # line 104
```

app.py 是 Streamlit UI 包装，非核心库代码。但这是仓库内第 7 个 VideoCapture 调用点，且无 try/finally。循环体内 `cv2.putText` 等操作如果异常（如 read 返回 None 但未 break 的边界），cap 泄漏。

**影响**：Streamlit 页面异常时 VideoCapture 可能泄漏，但 Streamlit 进程模型会定期重启，影响有限。

**建议**：包 try/finally，与其他 VideoCapture 调用点一致。或加注释说明"Streamlit 进程模型会清理，低优先级"。

---

### L-2 · `video.py:40` — FPS 无下界校验

```python
fps=float(cap.get(cv2.CAP_PROP_FPS) or 60),
```

`cap.get(cv2.CAP_PROP_FPS)` 返回 0.0 时 `or 60` 兜底为 60。但如果返回极小非零值（如 0.001，损坏视频容器），会直接传入。后续 `calc_derivative(series, 1.0/0.001)` = `np.gradient(series, 1000.0)` 导致所有导数趋近 0。

**建议**：加 `if fps < 1.0: fps = 60.0` 下界保护。

---

### L-3 · `analysis.py:176` — `_print_report` 标签 "Pure Tension Coeff (PTC)" 传播误导命名

```python
print(f"  Pure Tension Coeff (PTC) : {metrics['ptc']:>8.1f} Hz²")
```

CLAUDE.md「理论状态」段明确记录 PTC 命名误导（实为 miss-frame 加速度-误差密度）。`_print_report` 和 `export_analysis` 的 metrics.json 结构（`"tension": {...}`）继续使用旧命名。

**这是已记录的理论债**（v2 重构处理），不是新发现。标注以防遗漏。

---

### L-4 · `tracking.py:57` — `cross_pos` 初始化为画面中心（理论债，CLAUDE.md 已记录）

```python
cross_pos = (metadata.width // 2, metadata.height // 2)
```

这是 CLAUDE.md「注意事项」记录的已知设计限制。**不是实现 bug**，理论债归 tracking-coach spec 处理。标注以区分。

---

## 理论状态确认

### PTC / speed_mismatch / accel_mismatch 实现现状

| 指标 | 实现事实 | 命名 vs 实现 | 状态 |
|---|---|---|---|
| PTC | `mean(a_rel\|miss) / max(mean(error_px\|miss), 1.0)` (analysis.py:120) | "Pure Tension Coeff" 是误导命名；实际是 miss-frame 加速度-误差密度，不直接测肌肉张力 | 已记录债（CLAUDE.md、spec §2.1） |
| speed_mismatch | `mean(v_rel\|miss)` (analysis.py:118)，`v_rel = ‖v_c − v_t‖` (line 74) | app.py 路径下 `v_c ≈ 0`，所以 `v_rel ≈ v_t`，主要描述目标速度而非玩家追踪误差 | 已记录债（CLAUDE.md、spec §6.4） |
| accel_mismatch | `mean(a_rel\|miss)` (analysis.py:119)，`a_rel = ‖a_c − a_t‖` (line 75) | app.py 路径下 `a_c ≈ 0`，所以 `a_rel ≈ a_t`，主要描述目标加速度而非玩家追踪误差 | 已记录债（CLAUDE.md、spec §6.4） |

### J/E Ratio / TBR 残留确认

**结论：代码无残留实现。**

昨天的理论 review（07-08 #7）已确认：
- 代码中无 `jitter_error`、`JitterError`、`TBR`、`tension_balance` 等变量/函数/类
- 无 `1.8` / `0.6` 作为 TBR 阈值（所有数字均为 SPARC 阈值 -5.0、cm/360 区间等）
- advice_tracking.py 不 emit TBR 相关 signal
- profiles.py / knowledge.py / agent_kb.py 无 TBR 条目

本次 grep 扫描确认：代码中无 J/E Ratio / TBR 实现。所有文档提及均在"已弃/否认/历史"语境。

---

## advice_tracking 规则引擎验证

### 7 signal 路径正确性

| Signal | 触发条件 | Severity | 阈值来源 | 状态 |
|---|---|---|---|---|
| `accuracy_low` | `on_target_pct < 70.0` | fix | spec §3.1 A | ✓ 正确 |
| `loss_count_high` | `loss_count > 60` | fix | spec §3.1 B | ✓ 正确 |
| `off_target_long` | `total_off_time/loss_count > 0.05` | watch | spec §3.1 C | ✓ 正确 |
| `avg_error_high` | `avg_error_px/ball_w > 0.5` 或 `avg_error_px > 30.0` | fix | spec §3.1 D | ✓ 正确 |
| `speed_mismatch_high` | `speed_mismatch > None` | - | spec §3.1 E (uncalibrated) | ✓ 不 emit（threshold=None） |
| `accel_mismatch_high` | `accel_mismatch > None` | - | spec §3.1 F (uncalibrated) | ✓ 不 emit（threshold=None） |
| `ptc_high` | `ptc > None` | - | spec §3.1 G (uncalibrated) | ✓ 不 emit（threshold=None） |

**验证结果**：
- 4 个 calibrated signal（accuracy/loss_count/off_time/avg_error）正确 emit 为 `fix`/`watch`
- 3 个 uncalibrated signal（speed/accel/ptc）正确不 emit（threshold=None）
- 当 threshold 被设置后，speed/accel emit 为 `watch`，ptc emit 为 `info` — 符合 spec §7 解读假设分级

### 测试覆盖

`tests/coach/test_advice_tracking.py`：29 测试，全部通过。覆盖：
- flatten_metrics 各种输入形状
- 7 signal 触发/不触发边界
- build_report routing（summary_type 显式 + fallback heuristic）
- 处方完整性

---

## 算法细节验证

### Savitzky-Golay 平滑边界

`apply_smoothing`（analysis.py:27-35）：
- `window_length = max(5, fps * 0.1)`（line 44） ✓
- edge padding 用 `mode='edge'`（line 33） ✓
- 返回时截断回原长度（line 35） ✓

**验证**：正确。

### Chunk 分割

`load_tracking_data`（analysis.py:22）：
```python
valid_df["chunk_id"] = (valid_df["frame_diff"] > max(3, fps * 0.01)).cumsum()
```
帧间隔 > max(3, fps*0.01) → 新 chunk。**验证**：正确。

### HSV 检测 H 通道环绕

`_make_mask`（vision.py:12-26）：环绕逻辑正确。但 `get_hsv_range` 永不产生环绕区间（见 M-3）。**验证**：算法正确但调用路径无效。

### CSRT 混合追踪失败回退

`tracking.py:87-112`：
- CSRT 失败 → `tracking_active = False`（line 110）
- 下一帧 `if not tracking_active` → HSV 检测（line 88）
- HSV 检测成功 → `tracker.init(frame, bbox)` + `tracking_active = True`（lines 92-94）

**验证**：回退机制存在，但无误检确认（见 M-1）。

---

## CLI 输入校验 + 错误信息质量

### Analyze.py

- `--csv` required：✓ argparse enforce
- `--fps` optional，fallback 到 `output/calib_config.json`：✓ 但路径为相对路径（见 M-4）
- CSV 不存在时 `pd.read_csv` 抛 `FileNotFoundError`：✓ 保留，让调用方处理

### calibrate.py

- `--video` required：✓ argparse enforce
- `--frames` optional，default 100：✓
- 视频打不开时 `get_video_metadata` 抛 `FileNotFoundError`：✓

### app.py (Streamlit UI)

- 文件上传：✓ `st.file_uploader` enforce
- ROI 坐标校验（lines 126-128）：
  ```python
  if x2 <= x1 or y2 <= y1:
      st.warning("Invalid ROI: x2 must be > x1 and y2 > y1.")
      st.stop()
  ```
  ✓ 有校验
- 颜色采样无边界校验：⚠️ 如果用户输入 x1/x2/y1/y2 超出画面宽高，`sample_median_bgr` 的 slice 会越界（但 Python slice 不会崩溃，只会返回空/部分数据）

**建议**：加边界校验 `0 <= x1 < x2 <= width`，`0 <= y1 < y2 <= height`。

---

## VideoCapture 覆盖总结（tracking+CV 域）

| 文件:行 | try/finally | release 位置 | 状态 |
|---|---|---|---|
| `tracking.py:51` | ✓ (line 52 try / 140 finally) | line 141 | ✓ 正确 |
| `calibration_cli.py:43` | ✓ (line 44 try / 118 finally) | line 119 | ⚠️ cap 正确，**writer 在 finally 外**（H-1） |
| `video.py:27` (get_video_metadata) | ✓ (line 28 try / 45 finally) | line 46 | ✓ 昨日已修 |
| `video.py:63` (read_frame own_cap) | ✓ (line 64 try / 70 finally) | line 72 | ✓ 正确（own_cap 条件释放） |
| `app.py:85` | ✗ | line 104 | ⚠️ scope 外（L-1） |

**结论**：tracking+CV 域内 VideoCapture cap 全部正确。但 calibration_cli 的 VideoWriter 仍在 finally 外。

---

## Top 3（按修复优先级）

1. **H-1** `calibration_cli.py:121 VideoWriter 泄漏` — Windows 文件锁，用户可感知。2 行修。
2. **H-2** `analysis.py:15 NaN 传播` — calibration_cli 路线下指标变垃圾值，无报错。1 行修。
3. **H-3** `vision.py:37-56 H 环绕失效` — 红色目标检测召回率低。5 行修。

---

## 修复验证一句话

- **sys.exit(1) → raise**：✓ 已验证，库函数不再杀宿主进程，`import sys` 已删。
- **get_video_metadata try/finally**：✓ 已验证，`cap.release()` 移入 finally 块。

---

## Spec §1 决策建议一句话

tracking-coach spec §1.3 用"逐帧采样≠常量"否认 `v_c=0`，但 app.py 路径下 cross_pos 确为常量中心，advice_tracking.py:155 也承认 v_rel 主导项是目标速度——**建议 spec 承认实现现实**（或改实现使 v_c 真正反映玩家追踪误差，v2 级别）。

---

## 统计

- **Critical**：0
- **High**：3（H-1, H-2, H-3）
- **Medium**：4（M-1, M-2, M-3, M-4）
- **Low**：4（L-1, L-2, L-3, L-4）

**总计**：15 发现（11 新问题 + 4 遗留未修）

---

## 附：未修复项对比（昨日 vs 今日）

| 昨日编号 | 昨日发现 | 今日状态 |
|---|---|---|
| H-1 | calibration_cli VideoWriter 泄漏 | **未修**（今日 H-1） |
| H-2 | get_video_metadata 无 try/finally | **已修**（5a5bb84） |
| H-3 | analysis.py sys.exit(1) | **已修**（5a5bb84） |
| M-1 | analysis.py NaN 传播 | **未修**（今日 H-2，升级） |
| M-2 | calibration_cli 死循环 | **未修**（今日 M-2） |
| M-3 | vision.py H 环绕失效 | **未修**（今日 H-3，升级） |
| M-4 | tracking.py CSRT 误检 | **未修**（今日 M-1） |
| M-5 | analysis.py FPS 相对路径 | **未修**（今日 M-4） |
| L-1 | analysis.py PTC 命名误导 | **保留**（今日 L-3，理论债） |
| L-2 | video.py FPS 无下界校验 | **未修**（今日 L-2） |
| L-3 | app.py VideoCapture | **未修**（今日 L-1） |
| L-4 | tracking.py cross_pos 中心 | **保留**（今日 L-4，理论债） |

**修复率**：2/12（17%）
