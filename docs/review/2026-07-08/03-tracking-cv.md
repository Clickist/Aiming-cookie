# Tracking + CV Review

> **日期** 2026-07-08 · **reviewer** tracking+CV agent · **scope** `analysis.py` / `tracking.py` / `vision.py` / `video.py` / `calibration_cli.py`

## 健康度：B-（可用，但资源管理和边界守卫有明确缺口）

tracking + CV 工具链的 07-07 修复（VideoCapture try/finally × 5、inferred_fps 偏置、维度校验等）覆盖了最严重的资源泄漏路径。但残留三个资源/控制流问题（VideoWriter 泄漏、get_video_metadata 无 try/finally、sys.exit 杀宿主进程）和一个 NaN 传播路径（calibration_cli 路线 cross_x 为 None 时 analysis 不设防），以及 CSRT 恢复状态机缺乏误检确认。

PTC 公式实现与 CLAUDE.md 记载一致。Savitzky-Golay edge-pad + np.gradient + chunk 分割逻辑正确。HSV 环绕处理在 `_make_mask` 层面实现正确，但 `get_hsv_range` 永远不产生环绕区间（dead path）。

---

## Critical

无。07-07 修复已关闭最严重的 VideoCapture 泄漏（calibration_cli ESC 路径）。

---

## High

### H-1 · `calibration_cli.py:121` — VideoWriter 在 try/finally 外，异常时泄漏

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

**影响**：VideoWriter 持有输出 mp4 文件句柄。Windows 上未释放的 VideoWriter 会锁住 `calibration_check.mp4`，用户无法删除/覆盖该文件，且进程结束后才由 OS 回收（有时需要强制结束进程）。与 07-07 修的 ESC 问题同类。

**建议**：将 `writer.release()` 移入 finally 块。需处理 writer 尚未创建的情况（select_color_interactive 在 line 50/53 抛 SystemExit 时 writer 未创建）：
```python
    finally:
        cap.release()
        if 'writer' in locals():
            writer.release()
```
或给 writer 初始化为 None，finally 里 `if writer is not None: writer.release()`。

---

### H-2 · `video.py:27-46` — `get_video_metadata` 无 try/finally（07-07 5 处覆盖的缺口）

```python
cap = cv2.VideoCapture(str(video_path))       # line 27
if not cap.isOpened():                         # line 28
    raise FileNotFoundError(...)               # line 29 — cap 未 release（isOpened=False 时 cap 仍需释放）
width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) # line 31
height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))# line 32
if width <= 0 or height <= 0:                  # line 33
    cap.release()                              # line 34 — 有手动 release ✓
    raise ValueError(...)                      # line 35
metadata = VideoMetadata(                      # line 39
    fps=float(cap.get(cv2.CAP_PROP_FPS) or 60),
    ...
)
cap.release()                                  # line 45 — 正常路径 ✓
return metadata
```

**问题**：
1. `isOpened()` 返回 False 时 `raise FileNotFoundError` — 此时 cap 对象已创建但未 release。VideoCapture 即使 isOpened=False 也占用资源。
2. line 31-43 之间任何异常（如 `cap.get()` 返回非常规类型导致 `int()` 失败、`VideoMetadata` frozen dataclass 构造异常——虽然极罕见）都会导致 cap 泄漏。line 33-35 的维度守卫只覆盖了一种异常路径。

**影响**：`get_video_metadata` 是全模块最频繁调用的 VideoCapture 入口（tracking.py、start_frame.py×2、calibration_cli.py、pan_tracker.py 都调它）。任何泄漏在高频调用场景累积。

**建议**：整个函数体包 try/finally：
```python
cap = cv2.VideoCapture(str(video_path))
try:
    if not cap.isOpened():
        raise FileNotFoundError(...)
    ...
    return metadata
finally:
    cap.release()
```

**07-07 5 处验证**：tracking.py:51 ✓ / calibration_cli.py:43 ✓ / start_frame.py:69 ✓ / start_frame.py:186 ✓ / video.py:63(read_frame) ✓ — 这 5 处是对的。但 `get_video_metadata`（video.py:27）是第 6 个调用点，未被覆盖。

---

### H-3 · `analysis.py:197-198` — 库函数内 `sys.exit(1)` 杀宿主进程

```python
def run_analysis(csv_path, fps=None, output_dir=OUTPUT_DIR):
    if fps is None:
        config_path = Path("output/calib_config.json")
        if config_path.exists():
            ...
        else:
            print("ERROR: FPS not specified ...")
            sys.exit(1)   # ← line 198
```

**问题**：`run_analysis` 是库函数（被 `Analyze.py` CLI 调，也被 webapp worker 调）。`sys.exit(1)` 抛 `SystemExit`，在 CLI 里行为如预期，但在 Streamlit / FastAPI worker / 任何嵌入场景下会**杀死整个宿主进程**。webapp worker 如果直接调此函数（或未来桌面 sidecar 调），进程直接退。

**影响**：嵌入场景下单个分析失败 = 整个服务崩溃。Streamlit app 会整个重启。

**建议**：改为 `raise FileNotFoundError("FPS not specified and output/calib_config.json not found. Pass fps= explicitly.")` 或 `raise ValueError(...)`。CLI 入口 `Analyze.py` 可以 catch 后 `sys.exit(1)`，把退出决策留给调用方。

---

## Medium

### M-1 · `analysis.py:16` — `load_tracking_data` 只过滤 `ball_x` NaN，cross_x 为 None 时 NaN 传播

```python
df["is_valid"] = df["ball_x"].notna()   # line 16 — 只检查 ball_x
valid_df = df[df["is_valid"]]            # line 17
```

**问题**：`calibration_cli.py` 的输出 CSV 中 `cross_x`/`cross_y` 可以是 None（`detect_point_by_color` 未检出十字线时，line 113-114）。`tracking.py` 路径不会出这个问题（cross_pos 恒为 screen center 或检测值，line 57+130）。但 `load_tracking_data` 只按 `ball_x` 过滤行——ball 检出但 cross 未检出的行通过过滤，进入 `extract_kinematics`。

在 `extract_kinematics` 中：
```python
cx = apply_smoothing(group["cross_x"].values, window_size)  # 含 NaN
```
NaN 经 savgol_filter → calc_derivative → `dx = bx - cx`（NaN 传播）→ `error_px = np.hypot(dx, dy)` = NaN → `is_miss` 判断中 `np.abs(NaN) > threshold` = False → miss_mask=0 但 error=NaN → 最终 `avg_error_px`、`ptc` 等指标被 NaN 污染。

**影响**：走 calibration_cli → Analyze.py 路线时，如果十字线检测率低（颜色不明显），所有指标变 NaN 垃圾值，无报错。走 Streamlit（tracking.py）路线不受影响。

**建议**：过滤条件改为同时检查 cross_x：
```python
df["is_valid"] = df["ball_x"].notna() & df["cross_x"].notna()
```

---

### M-2 · `calibration_cli.py:27-36` — `select_color_interactive` 窗口被 OS 关闭时死循环

```python
while True:
    key = cv2.waitKey(20) & 0xFF
    if key == 13 and param["picked"]:   # Enter
        ...
    if key == 27:                        # ESC
        ...
```

**问题**：循环只响应 Enter 和 ESC。如果用户通过 OS 窗口关闭按钮（×）关闭 OpenCV 窗口，`cv2.waitKey` 在 Windows 上会持续返回 -1（或 255），不匹配任何退出条件，进入死循环。CPU 空转，进程挂起。没有检查窗口是否仍然存在（如 `cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE)`）。

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

### M-3 · `vision.py:37-56` — `get_hsv_range` 永不产生 H 环绕区间，`_make_mask` 环绕处理为 dead code

`_make_mask`（line 12-26）正确实现了 H 通道环绕：当 `hsv_lo[0] > hsv_hi[0]` 时拆两个 mask 做 OR。但 `get_hsv_range`（line 37-56）产生的 lo/hi 对 H 做了 clamp：
```python
np.array([max(0, h - tolerance_h), ...])     # lo
np.array([min(179, h + tolerance_h), ...])   # hi
```
当 h=2, tolerance_h=10：lo_h=0, hi_h=12 — 无环绕。
当 h=178, tolerance_h=15：lo_h=163, hi_h=179 — 无环绕。
当 h=0, tolerance_h=15：lo_h=0, hi_h=15 — 只覆盖 0-15，**漏掉 165-179 的同色区域**。

红色/品红色目标（H≈0 或 H≈179）的 HSV 范围永远只覆盖 0/179 边界的一侧。`_make_mask` 的环绕分支永远不被触发（除非手动构造 HSV 区间）。

**影响**：红色目标检测召回率低（只检测到一半色相空间）。KovaaK's 目标通常非红色，影响有限，但用户手动采色时可能踩到。

**建议**：`get_hsv_range` 中，当 `h - tolerance_h < 0` 或 `h + tolerance_h > 179` 时，主动产生环绕区间（让 lo_h > hi_h），使 `_make_mask` 环绕分支生效。或直接扩展容差到全 0-179 范围时退化为全 H 通配。

---

### M-4 · `tracking.py:87-94` — CSRT 失败后从单帧 HSV 检测重初始化，无误检确认

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

### M-5 · `analysis.py:189` — FPS fallback 用相对路径 `Path("output/calib_config.json")`

```python
config_path = Path("output/calib_config.json")   # 相对于 CWD
```

**问题**：`output_dir` 参数默认为 `OUTPUT_DIR`（settings.py: `Path("output")`，也是相对路径）。但 `run_analysis` 读 config 时硬编码 `"output/calib_config.json"` 而非 `output_dir / "calib_config.json"`。如果 CWD 不是项目根目录（如从 webapp/ 目录调用），config 永远找不到。

**影响**：非项目根目录调用时 fallback 路径失效，触发 `sys.exit(1)`（与 H-3 叠加）。

**建议**：`config_path = output_dir / "calib_config.json"`。

---

## Low

### L-1 · `analysis.py:177` — `_print_report` 标签 "Pure Tension Coeff (PTC)" 传播误导命名

```python
print(f"  Pure Tension Coeff (PTC) : {metrics['ptc']:>8.1f} Hz²")
```

CLAUDE.md「理论状态」段明确记录 PTC 命名误导（实为 miss-frame 加速度-误差密度，不直接测肌肉张力）。`_print_report` 和 `export_analysis` 的 metrics.json 结构（`"tension": {...}`）继续使用旧命名。这是**已记录的理论债**（v2 重构处理），不是新发现，但标注以防遗漏。

**建议**：v2 重构时统一改名（如 `miss_accel_density`），metrics.json key 改为 `"kinematics"`。当前 v1 不阻塞。

---

### L-2 · `video.py:40` — FPS 无下界校验

```python
fps=float(cap.get(cv2.CAP_PROP_FPS) or 60),
```

`cap.get(cv2.CAP_PROP_FPS)` 返回 0.0 时 `or 60` 兜底为 60。但如果返回极小非零值（如 0.001，损坏视频容器），会直接传入。后续 `calc_derivative(series, 1.0/0.001)` = `np.gradient(series, 1000.0)` 导致所有导数趋近 0，指标失真但不报错。

**建议**：加 `if fps < 1.0: fps = 60.0` 下界保护。

---

### L-3 · `app.py:85-104` — VideoCapture 无 try/finally（scope 外，备注）

```python
cap_play = cv2.VideoCapture(video_path)   # line 85
...
for i in range(0, process_length, step):
    ret, p_frame = cap_play.read()
    ...
    cv2.putText(p_frame, ...)              # 如果 p_frame 格式异常 → 崩溃 → 泄漏
cap_play.release()                         # line 104
```

app.py 是 Streamlit 薄包装（非本 review scope），但这是仓库内第 7 个 VideoCapture 调用点，且无 try/finally。循环体内 `cv2.putText` 等操作如果异常（如 read 返回 None 但未 break 的边界），cap 泄漏。

---

### L-4 · `tracking.py:57` — `cross_pos` 初始化为画面中心（理论债，CLAUDE.md 已记录）

```python
cross_pos = (metadata.width // 2, metadata.height // 2)
```

这是 CLAUDE.md「注意事项」记录的已知设计限制：准星硬编码画面中心导致 speed_mismatch / accel_mismatch 只描述目标运动。**不是实现 bug**，理论债归 tracking-coach spec 处理。标注以区分。

---

## 07-07 修复验证

| 修复项 | 验证结果 |
|---|---|
| inferred_fps 偏置→传 fps 参数 | ✓ `run_analysis` 接受 `fps` 参数（line 187）；`run_tracking_analysis` 写 `metadata.fps` 到 config（line 149） |
| start_frame 滑窗最小长度守卫 | ✓ `start_frame.py:104-106` window 长度 < hold_frames+1 时 `continue`；`tracking.py:48` `process_length==0` raise |
| **VideoCapture try/finally（5 处）** | **见下表** |
| aligner NaN 守卫 | （非本 scope，skip） |
| csv_parser TTK coerce | （非本 scope，skip） |
| video 维度校验 | ✓ `video.py:33-38` width/height <= 0 时 raise ValueError |

### VideoCapture try/finally 覆盖表

| # | 文件:行 | try/finally | release 位置 | 状态 |
|---|---|---|---|---|
| 1 | `tracking.py:51` | ✓ (line 52 try / 140 finally) | line 141 | ✓ 已修 |
| 2 | `calibration_cli.py:43` | ✓ (line 44 try / 118 finally) | line 119 | ✓ 已修（ESC 路径 now covered） |
| 3 | `start_frame.py:69` | ✓ (line 70 try / 86 finally) | line 87 | ✓ 已修 |
| 4 | `start_frame.py:186` | ✓ (line 187 try / 222 finally) | line 223 | ✓ 已修 |
| 5 | `video.py:63` (read_frame) | ✓ (line 64 try / 70 finally) | line 72 | ✓ 已修 |
| **6** | **`video.py:27` (get_video_metadata)** | **✗ 无 try/finally** | line 34/45 (分散) | **缺口 → H-2** |
| 7 | `app.py:85` | ✗ | line 104 | scope 外（L-3） |
| 8 | `pan_tracker.py:141` | ✗ | line 184 | flicking scope |

**结论**：07-07 修的 5 处全部正确覆盖。但 `get_video_metadata`（全模块最高频调用点）漏修（H-2）。calibration_cli 的 VideoWriter 也在 finally 外（H-1）。

---

## Top 3（按修复优先级）

1. **H-3** `analysis.py:197 sys.exit(1)` — 嵌入场景杀宿主进程。最简单的修（1 行改 raise），收益最大。
2. **H-1** `calibration_cli.py:121 VideoWriter 泄漏` — Windows 文件锁，用户可感知。2 行修。
3. **H-2** `video.py:27 get_video_metadata 无 try/finally` — 高频调用点的资源缺口。5 行修。

---

## 测试覆盖观察

- `tests/coach/test_advice_tracking.py`：21 测试，覆盖 advice 引擎（signal 触发 / flatten / build_report 路由）。充分。
- `analysis.py` / `tracking.py` / `vision.py` / `video.py` / `calibration_cli.py`：**零单元测试**。CV 管线的核心逻辑（PTC 计算、平滑、导数、chunk 分割、HSV 检测、CSRT 状态机）完全无回归保护。07-07 修的 5 处 VideoCapture try/finally 也无测试验证（资源管理测试难写但可用 mock 覆盖）。
- **缺口**：`apply_smoothing` 边界（len < window_length）、`load_tracking_data` chunk 分割、`evaluate_mechanics` 的 loss_count 跨 chunk 边界处理（已有注释说明修复了，但无测试锁定）值得补回归。
