# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

基于物理的准星追踪分析工具，通过计算机视觉从 KovaaK's FPS Aim Trainer 录屏中提取目标与准星位置，计算肌肉张力与速度匹配指标。

核心理论：J/E (Jitter/Error) Ratio 模型——用相对加速度(Jitter)与空间误差(Error)的比值（Tension Balance Ratio）诊断过度握紧(TBR>1.8)或反应滞后(TBR<0.6)。

## 开发命令

```bash
# 校准步骤（Streamlit Web UI）
streamlit run app.py

# 物理分析（命令行，FPS 自动从 calib_config.json 读取）
python Analyze.py --csv output/calibration_raw.csv

# 可视化仪表盘（Streamlit Web UI）
streamlit run dashboard.py

# 命令行校准（旧版，需要本地 OpenCV 窗口交互）
python calibrate.py --video your_recording.mp4
```

## 架构

三步流水线：

1. **app.py**（Streamlit）：视频裁剪 → 颜色采样 → 逐帧 CV 检测 → 输出 `output/calibration_raw.csv` + `output/calib_config.json`
2. **Analyze.py**（CLI）：读取 CSV → Savitzky-Golay 平滑（edge-padded）→ np.gradient 计算加速度 → 基于 loss-frame 计算 PTC（Pure Tension Coefficient）→ 输出 `output/metrics.json` + `output/frame_errors.csv`
3. **dashboard.py**（Streamlit）：读取 metrics.json → Plotly 绘制 Tension Quadrant 图

共享模块：
- **cv_detect.py**：`get_hsv_range`、`detect_ball_by_color`、`detect_crosshair_by_color`——`app.py` 和 `calibrate.py` 共用的 CV 检测逻辑，含 H 通道环绕处理、双向宽高比过滤、分辨率无关的面积阈值

`calibrate.py` 是早期的本地 OpenCV 交互式校准脚本，功能已被 `app.py` 替代。

## 关键算法

- **目标检测**：HSV 颜色掩码（含 H 通道环绕处理）+ 形态学开闭操作 + 轮廓分析，限制目标面积 50px ~ 5% 画面、宽高比 0.7~1.3、天空区域死区过滤
- **PTC 计算**：仅在 loss-frame（准星脱离目标判定框的帧）上计算，公式为 `mean_a_rel / mean_E`，单位 Hz²
- **平滑**：Savitzky-Golay 滤波器（edge-padded），窗口大小 = max(5, fps*0.1)
- **运动学**：`np.gradient` 中心差分计算速度和加速度
- **Chunk 分割**：帧间隔 > max(3, fps*0.01) 时切分，阈值与帧率成正比

## 输出文件

所有输出在 `output/` 目录：
- `calib_config.json` — 颜色校准参数（BGR + HSV 范围）
- `calibration_raw.csv` — 逐帧坐标数据（frame, time_s, ball_x/y, cross_x/y, ball_w/h）
- `metrics.json` — 汇总指标（PTC、平均误差、命中率）
- `frame_errors.csv` — 逐帧误差 + on_target 标记

## 依赖

Python 3.10+，需要：opencv-python, streamlit, pandas, plotly, scipy, numpy

## 注意事项

- `app.py` 中准星位置硬编码为画面中心（`cross_pos = (width // 2, height // 2)`），不追踪实际游戏准星。这意味着 PTC 测量的是"目标相对于画面中心的运动"而非"玩家的肌肉张力响应"——在目标远离画面中心的场景中，误差会被放大，PTC 结果不可靠。设计上适用于目标保持在画面中心附近的追踪场景（如 KovaaK's 中心固定靶类场景）
- `Analyze.py` 的 `--fps` 参数默认从 `output/calib_config.json` 自动读取，无需手动指定
