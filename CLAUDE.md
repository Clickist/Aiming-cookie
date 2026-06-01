# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

基于物理的准星追踪分析工具，通过计算机视觉从 KovaaK's FPS Aim Trainer 录屏中提取目标与准星位置，计算肌肉张力与速度匹配指标。

核心理论：J/E (Jitter/Error) Ratio 模型——用相对加速度(Jitter)与空间误差(Error)的比值（Tension Balance Ratio）诊断过度握紧(TBR>1.8)或反应滞后(TBR<0.6)。

## 开发命令

```bash
# 安装依赖
pip install -r requirements.txt

# 校准步骤（Streamlit Web UI，含 CSRT 混合追踪）
streamlit run app.py

# 物理分析（命令行，FPS 自动从 calib_config.json 读取）
python Analyze.py --csv output/calibration_raw.csv

# 可视化仪表盘（Streamlit Web UI，含 Quadrant + Error Timeline + VOD Review）
streamlit run dashboard.py

# 命令行校准（需要本地 OpenCV 窗口交互）
python calibrate.py --video your_recording.mp4
```

## 架构

`kovaak_tracker/` 包是核心逻辑，根目录脚本是薄 CLI/UI 包装。

### 包结构 (`kovaak_tracker/`)

- **vision.py** — CV 检测原语：HSV 范围生成、颜色 blob 检测、CSRT tracker 工厂。含 H 通道环绕处理（`_make_mask`）、双向宽高比过滤、形态学清洗（`_apply_morphology`）
- **tracking.py** — CSRT 混合追踪引擎：CSRT 快速追踪 + HSV 检测回退。输出 DataFrame + 预览帧 + 统计
- **analysis.py** — 物理分析管线：edge-padded SG 平滑 → np.gradient 运动学 → PTC/Speed Mismatch/Accel Mismatch 计算
- **video.py** — 视频工具：`VideoMetadata`、`save_uploaded_video`、`get_video_metadata`、`read_frame`
- **dashboard_data.py** — 仪表盘数据：加载 metrics + frame_errors、构建 Plotly 图表、VOD 帧渲染
- **calibration_cli.py** — 交互式颜色校准（本地 OpenCV 窗口）
- **settings.py** — `OUTPUT_DIR` 常量和 `ensure_output_dir()`

### 根目录脚本

- **app.py**（Streamlit）：视频上传 → 裁剪 → 颜色采样 → CSRT 混合追踪分析 → 输出 CSV + config
- **Analyze.py**（CLI）：薄包装调用 `kovaak_tracker.analysis.run_analysis()`
- **dashboard.py**（Streamlit）：三标签页——Tension Quadrant / Error Timeline / VOD Review
- **calibrate.py**（CLI）：薄包装调用 `kovaak_tracker.calibration_cli.run_calibration()`

### 三步流水线

1. **app.py** → 输出 `output/calibration_raw.csv` + `output/calib_config.json`
2. **Analyze.py** → 输出 `output/metrics.json` + `output/frame_errors.csv`
3. **dashboard.py** → 读取 metrics.json 可视化

## 关键算法

- **CSRT 混合追踪**：CSRT tracker（O(1)/帧）为主，HSV 检测为回退。tracker 丢失目标时自动重新检测并初始化
- **目标检测**：HSV 颜色掩码（含 H 通道环绕处理）+ 形态学开闭 + 轮廓分析，面积 50px ~ 5% 画面、宽高比 0.7~1.3、天空区域死区
- **PTC 计算**：仅在 miss-frame 上计算，公式 `mean_a_mismatch / mean_e_mismatch`，单位 Hz²
- **平滑**：Savitzky-Golay（edge-padded），窗口 = max(5, fps*0.1)
- **运动学**：`np.gradient` 中心差分
- **Chunk 分割**：帧间隔 > max(3, fps*0.01)

## 输出文件

所有输出在 `output/` 目录：
- `calib_config.json` — 颜色校准参数（BGR + HSV 范围 + FPS + 分辨率）
- `calibration_raw.csv` — 逐帧坐标数据（frame, time_s, ball_x/y, cross_x/y, ball_w/h）
- `metrics.json` — 汇总指标（PTC、speed_mismatch、accel_mismatch、accuracy、loss_count 等）
- `frame_errors.csv` — 逐帧误差 + is_miss 标记

## 依赖

Python 3.10+，见 `requirements.txt`。关键依赖：opencv-contrib-python（CSRT tracker 需要 contrib 模块）、streamlit、pandas、numpy、plotly、scipy

## 注意事项

- 准星位置硬编码为画面中心（`cross_pos = (width // 2, height // 2)`），不追踪实际游戏准星。PTC 测量的是"目标相对于画面中心的运动"而非"玩家的肌肉张力响应"——目标远离中心时误差被放大。适用于目标保持在画面中心附近的场景
- CSRT tracker 需要 `opencv-contrib-python`，标准 `opencv-python` 不含此模块。如果 contrib 不可用，自动回退到 KCF tracker
- `Analyze.py` 的 `--fps` 参数默认从 `output/calib_config.json` 自动读取

## 规划文档

- `docs/product-strategy.md` — 产品战略：轻量版（录像上传）+ 专业版（本地采集+手部摄像头）两条产品线
- `docs/flicking-analysis-plan.md` — Flicking 分析方案：通过 KovaaK's 场景数据扩展 flicking 支持
