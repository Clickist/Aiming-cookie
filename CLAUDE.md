# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 产品方向锚：PRD

**`docs/PRD.md`** 是 Aiming Cookie 的**方向锚 + 原始设想记录**。所有 spec / plan 从此派生；下游文档与 PRD 冲突时以 PRD 为准；PRD 过时则更新 PRD，不在下游打补丁。

**行为要求（重要）**：当点点的发言与 PRD 既有决策冲突时（形态 / 流程 / 阶段 / 功能边界 / 商业 / 技术选型等），**主动指出冲突点 + 问是否更新 PRD**——不要默默按新说法执行（会导致文档与现实脱节），也不要无视冲突。PRD 是活文档，随产品演进更新；确认改 PRD 后再同步下游。

## 项目概述

基于物理 + 运动学的 KovaaK's 瞄准分析工具。产品路线按 `docs/PRD.md` 保留 **flicking + tracking 双主线**：flicking 从录屏 + KovaaK's Stats CSV 提取目标运动，算减速段公平指标；tracking 从 calibration CSV 算 accuracy/loss/PTC 等；两者最终都接同一 AI coach（诊断 + 处方 + agent）。

**当前交付优先级**：这不代表放弃任何路线。2026-07-13 至 2026-07-19 的目标是受控环境中的 **flicking-only 内部技术预览**，不是完整产品 v1；先完成版本化合同、可恢复分析、最小 History、文件生命周期和发布 Gate，再推进 tracking v1 标定、完整 UIUX 收敛、登录/计费/云同步与桌面 hybrid。tracking 的 PTC / speed_mismatch 命名与实现存在理论债（见下"理论状态"，待 v2 重构处理），因此不应阻塞 Alpha 验收。

**设计系统约束**：新增或改造前端视觉时，先遵循 `docs/design-system.md`。`webapp/frontend/app/globals.css` 中的可执行 token 是前端视觉值的唯一事实源；页面组件不得自行新增 raw hex、临时色板或脱离 token 的视觉体系。设计 HTML、Stitch 产物与 mockup 只作参考，不是可执行 token 的来源。

### 理论状态（flicking + tracking，PTC 命名误导待 v2 重构）

**flicking**（主线）：公平指标体系是核心，全部有运动学/学术锚点——`decel_frac` / `linearity` / `sparc`（Balasubramanian 2012）/ `reverse_ratio` / `peak_speed` / `peak_position` / `path_efficiency` / `throughput`（Fitts）/ `corrective_count` / `submovement_overlap`。诊断规则只用学术根基，社区经验进讲解文案（铁律）。

**tracking**（早期，待重构）：`analysis.py` 的 PTC（Pure Tension Coeff）实际公式是 `mean(a_rel | miss) / max(mean(error_px | miss), 1.0)`——即 miss-frame 上的加速度-误差比，单位 1/s²（量纲上 Hz² 成立）。**"Pure Tension Coeff"是误导命名**：它不是直接测肌肉张力，张力需要手部摄像头/EMG 验证（详见 `docs/archive/retired/specs/2026-07-05-tracking-coach-design.md` §2.1；该文件只作历史理论审视）。
- 早期文档曾声称 **J/E (Jitter/Error) Ratio / TBR (Tension Balance Ratio)** 是核心理论——**已确认不成立**：J/E Ratio 在代码里没有独立实现（字面最贴近的就是 PTC 本身，是同一量的双名/营销名）；TBR 没有可计算定义，TBR>1.8 / TBR<0.6 阈值在仓库内无任何推导或人群标定来源（凭空）。两者已从主线移除，tracking coach v1 基于 accuracy / loss_count / off_time / avg_error 等 solid 量（见 spec §3.1）。
- tracking 的 PTC 命名误导 + speed_mismatch/accel_mismatch 实现 vs 命名不符（`cross_pos` 硬编码画面中心 → `v_c=0` → 这俩实际只描述目标运动，不描述玩家追踪误差）的债，已在 spec §2.4 / §6.4 记录，待 v1 重构处理。

## 开发命令

```bash
# 安装依赖
pip install -r requirements.txt

# 校准步骤（Streamlit Web UI，含 CSRT 混合追踪）
streamlit run app.py

# 物理分析（命令行，FPS 自动从 calib_config.json 读取）
python Analyze.py --csv output/calibration_raw.csv

# 命令行校准（需要本地 OpenCV 窗口交互）
python calibrate.py --video your_recording.mp4
```

## 架构

`kovaak_tracker/` 包是核心逻辑，根目录脚本是薄 CLI/UI 包装。

### 包结构 (`kovaak_tracker/`)

**flicking 主线**：
- **`coach/`**（子包）— 当前 Python Coach / narration 实现：`report.py`（`build_report` / `build_progress_report`）编排入口、`diagnosis.py`（画像 + 三层根因链）、`advice` 调用、`agent.py`（当前 tool-use loop；3 个 narration 入口 + session-bound `chat_with_coach`，**不是终局 Coach runtime**）、`agent_tools.py`（tool schema + handlers）、`agent_kb.py`（agent 知识库，按 signal / topic 索引）、`visualization.py`（5 类 Plotly figures）、`profiles.py`（archetype）、`knowledge.py`（12 条社区知识 → agent 检索）、`planning.py`（④ 训练计划）、`progress.py`（历史趋势）、`providers.py`/`providers.json`（LLM 后端，DeepSeek/Anthropic/local）
- **pan_tracker.py** — 全局平移轨迹（flick 角速度 = 视角平移）；提供 `analyze_flicking_fair_summary`（CSV 模式，主线入口）/ `analyze_flicking_reference`（无 CSV 模式）
- **flicking.py** — flick 谷切分（`segment_by_valleys`）+ 公平指标（`compute_fair_metrics`：SPARC / submovement / Fitts throughput / linearity / path_efficiency）
- **advice.py** — flicking 规则引擎（公平 summary → `Finding` 列表：症状 → 物理 → 处方）
- **advice_tracking.py** — tracking 规则引擎（tracking summary → `Finding`，7 signal：accuracy / loss_count / off_time / avg_error / speed / accel / ptc；后三个 None=uncalibrated emit info/watch；复用 flicking `Prescription`/`Finding` dataclass）
- **csv_parser.py** — KovaaK's Stats CSV 解析
- **start_frame.py** — 起始帧检测

**tracking 早期代码（v1 已接通 coach，命名债待 v2 重构）**：
- **analysis.py** — tracking 物理分析（PTC / speed_mismatch / accel_mismatch，理论状态见上）
- **tracking.py** — CSRT 混合追踪引擎（flicking 不用 CSRT，已弃用于 flicking 主线）
- **vision.py** — CV 检测原语（HSV blob + CSRT factory），tracking 用
- **video.py** — 视频工具：`VideoMetadata` / `save_uploaded_video` / `get_video_metadata` / `read_frame`
- **calibration_cli.py** — 交互式颜色校准（tracking 用，本地 OpenCV 窗口）

**通用**：
- **settings.py** — `OUTPUT_DIR` / `ensure_output_dir()`

### 根目录脚本（tracking 时代，CLI 仍可跑）

- **app.py**（Streamlit）— tracking 校准 UI（视频上传 → 裁剪 → 颜色采样 → CSRT 混合追踪 → CSV + config）
- **Analyze.py**（CLI）— tracking 分析薄包装，调 `analysis.run_analysis()`
- **calibrate.py**（CLI）— 调 `calibration_cli.run_calibration()`
- **dashboard.py** 已在 Phase 1B 删除（webapp 前端接替，见 `webapp/frontend/`）

### 两条分析流水线

**flicking 主线**（当前 scope）：
1. KovaaK's 录屏 (.mp4) + Stats CSV → `pan_tracker.analyze_flicking_fair_summary` → 公平 summary（`decel_frac` / `sparc` / `linearity` 等逐 flick 分布的 med/p75/p90）
2. summary → `coach.build_report(summary, reference, meta, backend)` → 画像 + 三层根因 + 5 figures + LLM narration
3. 多次报告 → `build_progress_report(history, current, backend)` → 趋势 + ④ 训练计划

**tracking**（v1 已接通 coach）：
1. `app.py` → `output/calibration_raw.csv` + `output/calib_config.json`
2. `Analyze.py` → `output/metrics.json` + `output/frame_errors.csv`
3. metrics.json → `advice_tracking.advise_tracking`（7 signal，复用 `Finding`/`Prescription`）→ 同一 coach 管线（diagnosis / visualization / agent）。speed/accel/ptc threshold 标 uncalibrated（None），需真实 tracking session 数据校准（见 tracking-coach spec §7）

## 关键算法

### flicking（主线）
- **全局平移轨迹**：flick 是视角快速平移，`pan_tracker` 用全图特征点匹配估计相机平移 → 反推视角角速度（CSRT 不适用 flick，已弃用）
- **flick 谷切分**（`segment_by_valleys`）：在角速度时序上找局部低谷切出每个 flick
- **公平指标**（`compute_fair_metrics`，无量纲、跨距离/速度可比）：
  - **SPARC**（频域平滑度，Balasubramanian 2012）— 减速段平滑度金标准
  - **submovement**：corrective vs primary 重叠度（Novak 2002 optimized submovements）
  - **Fitts throughput**（bits/s，跨距离公平）
  - `decel_frac` / `linearity` / `reverse_ratio` / `peak_speed` / `peak_position` / `path_efficiency`
- **lock 段采样**：稳定段降采样（7x 加速，指标 ±6%），详见 `docs/PROGRESS.md` 2026-06-28 续三

### tracking（早期）
- **CSRT 混合追踪**：CSRT tracker（O(1)/帧）为主，HSV 检测为回退（flicking 已弃用此机制）
- **目标检测**：HSV 颜色掩码（含 H 通道环绕处理）+ 形态学开闭 + 轮廓分析
- **PTC 计算**：仅在 miss-frame 上计算，公式 `mean(a_rel | miss) / max(mean(error_px | miss), 1.0)`，单位 1/s²（量纲 Hz²）。**命名误导**：是 miss-frame 加速度密度，不直接测肌肉张力（见"理论状态"）
- **平滑**：Savitzky-Golay（edge-padded），窗口 = max(5, fps*0.1)
- **运动学**：`np.gradient` 中心差分
- **Chunk 分割**：帧间隔 > max(3, fps*0.01)

## 输出文件

`output/` 目录：
- **flicking**：`history/sessions.jsonl`（coach 历史）、`ref_pan_trajectory.csv`（reference 轨迹，详见 `docs/PROGRESS.md`）、`flicking_segments.csv`、`flicking_metrics.json`
- **tracking**（早期）：`calib_config.json`、`calibration_raw.csv`、`metrics.json`（PTC、speed_mismatch、accel_mismatch、accuracy、loss_count）、`frame_errors.csv`

## 依赖

Python 3.10+，见 `requirements.txt`。关键依赖：opencv-contrib-python（CSRT tracker 需要 contrib 模块）、streamlit、pandas、numpy、plotly、scipy

## 注意事项

- **flicking + tracking 都进 coach**：flicking 是当前主线；tracking v1 已接通同一 coach 管线（`advice_tracking.py`，7 signal）。webapp（Aiming Cookie）同时承载两类 session
- **准星位置硬编码为画面中心**（`cross_pos = (width // 2, height // 2)`，tracking 模块）。导致 tracking 的 PTC / speed_mismatch / accel_mismatch **只描述目标运动**，不描述玩家追踪误差——详见 tracking-coach spec §1.2 / §2.4
- PTC 命名误导（"Pure Tension Coeff" 实为 miss-frame 加速度-误差密度，非直接测肌肉张力）；J/E Ratio / TBR 已确认不成立（无实现 + 凭空阈值），见"理论状态"段
- `opencv-contrib-python`（CSRT tracker 需要，flicking 已不依赖；仅 tracking 模块仍用）
- `Analyze.py` 的 `--fps` 参数默认从 `output/calib_config.json` 自动读取

## 规划文档

- `docs/README.md` — 唯一日常文档入口；新会话不要默认扫描归档
- `docs/PRD.md` — 产品方向锚、完整范围与阶段边界
- `docs/ARCHITECTURE.md` — 已定型架构、稳定合同与数据归属
- `docs/ROADMAP.md` — 当前发布定义、P0/P1/P2、2–4 周路线与 Go/No-Go gates
- `docs/PROGRESS.md` — 当前执行状态
- `docs/superpowers/specs/README.md` — 当前有效的局部设计
- `docs/superpowers/plans/README.md` — 当前可执行 plan 和 Task 状态；Fast 模型不得绕过该层直接从顶层文档实施跨层功能
- `docs/design-system.md` — 前端视觉治理与 token 入口
- `docs/coach-theory-foundation.md` / `docs/aim-kinematics-research.md` — 学术与运动学底座
- `docs/coach-community-frontier.md` — 时间敏感社区材料，只用于解释与处方理由，不进诊断规则

旧战略、旧 spec、旧 plan 和历史 review 已统一移至 `docs/archive/`，只供追溯，不参与当前决策。
