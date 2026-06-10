# Flicking 分析方案

## 目标

扩展 Tension-Aware-Aim-Analyzer 支持 flicking（甩枪）场景分析。当前仅支持 tracking（跟枪）。

## 核心卡点

从纯录像无法检测 miss 的点击——画面零反馈，无法区分"还没点"和"点了但 miss"。纯录像方案有幸存者偏差，只能分析命中样本。

## 选定方案：KovaaK's CSV + 视频 CV 对齐

用两个现有数据源拼合，不需要额外采集工具（pynput 等）。

### 数据源互补

| 数据 | KovaaK's Stats CSV | 视频CV追踪 (calibration_raw.csv) |
|---|---|---|
| 击杀时间戳 | ✅ | ✅ |
| 命中/miss | ✅ | ❌ |
| 目标位置 | ❌ | ✅ |
| 准星位置 | ❌ | ✅ (KovaaK's 准星固定中心) |
| 修正耗时 | ✅ TTK | — |
| 元数据 (DPI/Sens/FOV) | ✅ | ❌ |

### 时间对齐

CSV Timestamp 是墙钟时间 `HH:MM:SS.mmm`，汇总区有 `Challenge Start` 字段。

```
csv_relative_time = parse(Timestamp) - parse(Challenge Start)
video_time = csv_relative_time + (start_frame / fps)
```

从 calibration_raw.csv 按 `time_s` 插值，拿到射击时刻的 ball_x/y。

### Miss 识别

最后那发必定命中（否则不算 Kill），前面全是 miss：

| Shots | 含义 |
|---|---|
| 1 | 一次命中 |
| 2 | 第1发 miss，第2发 hit（击杀） |
| ≥3 | 罕见（<2%），忽略 |

TTK = 第一枪到击杀的时间，`miss_time = kill_timestamp - TTK`。

### 汇总区额外数据

CSV 底部包含 FOV、DPI、Sensitivity、Resolution、Crosshair 设置、Avg FPS 等。可用于 cm/360 换算和灵敏度分析，不需要用户手动输入。

## Flicking 运动学（2026-06-10 讨论）

### 核心认知：视频是主数据源，CSV 是补充

```
视频（主）                    CSV（补充）
─────────────────────         ─────────────────
轨迹、速度、加速度         ←→  开枪时刻（视频看不出点击）
目标位置、运动方向              命中/miss 结果
flick 边界（速度特征）          DPI/Sens/FOV 元数据
```

- CSV 不决定片段边界。flick 的起点是玩家开始向目标移动的时刻（CV 检测速度变化），不是 spawn/kill 时间戳
- CSV 的核心价值：标注开枪时刻 + 命中结果。这两样从纯视频里拿不到
- Shots=2 时 miss→hit 是一段连续运动，视频里看得到，不是两段

### 1w6ts 场景特点

- 多个目标同时在屏幕上，玩家自己规划路线选打哪个
- flick 是玩家主动发起的目标切换，不是被动等目标 pop
- 分析维度：**每次 flick 独立分析** + **连续表现趋势**（疲劳、节奏）都有价值

### 片段切分

Flick 边界基于 CV 追踪到的运动特征：
- **开始**：速度从低到高的转折点（玩家开始向目标移动）
- **结束**：击杀时刻（CSV 标注）或目标切换

### 张力指标方向（2026-06-10 调研更新）

#### 社区技术流派

| | Bardpill（经典） | Zeonlo（流体派） |
|---|---|---|
| 模式 | 快速 flick → 急停 → 独立 micro 修正 | flick 和 micro 是一个连续动作 |
| 速度曲线 | 两个峰（flick + micro） | 单个平滑钟形，减速段即修正 |
| 张力 | 两段式：flick 紧 → micro 再紧 | 前臂爆发 → 快速释放 → 指尖保持控制 |
| 评价 | 有效但有延迟 | 社区公认更优，更 fluid |

来源：[r/FPSAimTrainer 技术讨论](https://www.reddit.com/r/FPSAimTrainer/comments/1lwv34c/any_advice_on_how_to_learn_and_implement_static/)

#### 张力管理机制（社区 + 神经科学交叉验证）

加速段：前臂高张力产生速度 → 减速段：前臂释放 + 指尖精细控制。问题出在过渡：释放太快→欠冲，释放不够→过冲/抖动。

神经科学论文（[Becker et al., 2020](https://www.biorxiv.org/content/10.1101/2020.04.24.060533v1)）验证：
- **速度曲线是钟形但不对称**，减速段比加速段占更多时间
- **减速段运动学参数是预测成功/失败的最强信号**（随机森林模型，最后1/4段变量排前6）
- **减速是自适应的**：mean deceleration 由峰值速度 + 峰值速度时刻位置共同决定
- 失败的 reach 不是系统性偏移，而是**端点精度更差（方差更大）**
- 速度有"甜区"——太快或太慢都不准

```
理想速度曲线（不对称钟形）：
速度
 │      ╱╲
 │     ╱  ╲
 │    ╱    ╲
 │   ╱      ╲
 │  ╱        ╲
 │ ╱          ╲____
 │╱                ╲____
 0────────────────────────→ 时间
     加速    减速（更长）
              ↑ 决定成败
```

来源：[Voltaic Static Clicking Guide](https://www.youtube.com/watch?v=pOSQt1UEybM)、[Tension Management](https://www.youtube.com/watch?v=9JoDMDXVTcg)、[r/FPSAimTrainer](https://www.reddit.com/r/FPSAimTrainer/comments/k7n5iq/how_to_stop_over_flicking_and_under_flicking_and/)

#### 社区识别的病理模式

| 问题 | 速度曲线表现 | 物理原因 |
|---|---|---|
| 过冲 | 峰值过高或减速不够 | 张力释放不够，制动不足 |
| 欠冲 | 峰值不足或减速过早 | 张力释放太快 |
| 减速段抖动 | 减速段有锯齿/震荡 | 张力释放不平滑 |
| flick 后延迟 | 速度到零后停顿再 micro | 两段式延迟 |
| 太滑 | 没有明确加速减速 | 缺乏果断 flick |

#### 指标设计（调研得出）

| 指标 | 提取方法 | 诊断意义 |
|---|---|---|
| 速度曲线不对称度 | 加速段面积 vs 减速段面积 | 过度不对称 = 减速段需要太多修正 |
| 减速段平滑度 | 减速段加速度的标准差 | 高 = 抖动/锯齿，张力释放不平滑 |
| 过冲量 | 点击时刻目标到中心的距离（方向性） | 正 = 过冲，负 = 欠冲 |
| 峰值速度位置 | 峰值速度出现在 flick 时程的比例 | 过早 = 中段拖沓，过晚 = 来不及减速 |
| 端点精度 | 点击时刻速度的大小 | 理想接近零但不为零 |

#### Static Clicking 三维度框架

- **Speed**：flick 到目标的速度
- **Accuracy**：落到目标的精度
- **Pathing**：目标之间的路线规划（1w6ts cluster farming）

### 与 Tracking 代码的关系

底层函数（`apply_smoothing`、`calc_derivative`）可复用。`extract_kinematics` 和 `evaluate_mechanics` 需要重新设计——flicking 是逐片段分析，不是连续轨迹分析，但片段内有连续加速度特征。

## 可实现指标

| 指标 | 数据来源 | 可行性 |
|---|---|---|
| 反应时间 | CSV Timestamp 差值（前一个 kill → 本次 first shot） | ✅ |
| 命中率 | CSV Hit Count / (Hit + Miss Count) | ✅ |
| miss 率 | CSV Miss Count | ✅ |
| TTK 分布 | CSV TTK 字段 | ✅ |
| 过度射击 | CSV OverShots 字段 | ✅ |
| 补枪率 | Shots > 1 的击杀占比 | ✅ |
| 点击精度 | CV 插值射击时刻 ball 到中心的距离 | ✅ 对齐后可算 |
| 过冲/欠冲 | CV 轨迹中目标是否穿过中心 | ✅ 对齐后可算 |
| 减速平滑度 | CV 轨迹减速段加速度变化 | ✅ 对齐后可算 |
| 修正耗时 | TTK (Shots=2 时) | ✅ |
| 速度曲线 | CV 轨迹逐帧速度 | ✅ 对齐后可算 |
| 两段式修正 | CV 轨迹 miss→hit 段方向变化 | ✅ Shots=2 可算 |

## 实施路径

### Phase 0：搞明白 KovaaK's 怎么产出数据 ✅ 已完成

**调研结论：**

KovaaK's 内置自动导出每次 Challenge/Training 的统计数据为 CSV 文件。

**数据位置：**
```
{Steam安装目录}\steamapps\common\FPSAimTrainer\FPSAimTrainer\stats\
```

本机路径：`E:\SteamLibrary\steamapps\common\FPSAimTrainer\FPSAimTrainer\stats\`

**文件格式：** 标准 CSV，命名 `{场景名} - Challenge - {YYYY.MM.DD-HH.mm.ss} Stats.csv`

**数据字段（逐击杀）：**
- `Kill #` — 击杀编号
- `Timestamp` — **墙钟时间** `HH:MM:SS.mmm`（不是相对时间，需减去 Challenge Start 换算）
- `Bot` — 目标类型
- `Weapon` — 武器
- `TTK` — **第一枪到击杀的时间**（Shots=1 时为 0，Shots=2 时 = miss 到 hit 的间隔）
- `Shots` — 这次击杀打了几枪
- `Hits` — 命中几发
- `Accuracy` — 命中率
- `Damage Done` / `Damage Possible` — 伤害
- `Efficiency` — 效率
- `Cheated` — 是否作弊
- `OverShots` — 过度射击

**汇总区字段：**
- `Challenge Start` — 场景启动的墙钟时间（对齐锚点）
- `Fight Time`, `Kills`, `Deaths`, `Avg TTK`
- `Hit Count`, `Miss Count`, `Score`
- `Scenario` — 场景名
- `FOV`, `DPI`, `Horiz Sens`, `Vert Sens`, `Resolution`
- `Crosshair`, `Crosshair Scale`, `Crosshair Color`
- `Avg FPS`, `Max FPS`, `Input Lag`

**场景类型：** tracking 和 flicking 场景的 CSV 格式完全相同，可通过场景名或 Shots 量级区分（flicking: Shots=1~2, tracking: Shots=数百~数千）。

**数据验证（2026-06-09）：**
- 1989 个 CSV 文件在 stats 目录
- Timestamp 格式确认：墙钟 `HH:MM:SS.mmm`
- TTK 含义确认：第一枪到击杀时间（通过 1-shot kill TTK=0 + 多 shot kill 时间一致性交叉验证）
- Miss 顺序确认：最后一发 = hit（击杀），之前全 = miss
- Shots≥3 占比 <2%，可忽略
- 汇总区包含完整的灵敏度/FOV/DPI 元数据

### Phase 1：CSV Parser + 时间对齐

#### 时间对齐方案（2026-06-10 讨论）

**用户流程：**

1. 用户上传视频 + 选择场景名（首批支持：flicking=1w6ts，tracking 待定一个）
2. 系统自动搜索 Steam 路径找到 KovaaK's stats 目录
3. 用场景名 + 视频时间锁定对应 CSV（文件名含 `{场景名} - Challenge - {YYYY.MM.DD-HH.mm.ss}`）
4. CV 自动检测视频里场景开始的**建议起始帧**（检测 UI 切换 / 目标首次出现 / 倒计时消失——"从非游戏画面变为游戏画面"）
5. 用户通过帧预览 + 滑块确认或调整起始帧
6. 锚定：视频起始帧 ↔ CSV Challenge Start，后续帧号按 fps 换算

**对齐原理：**

视频早于 CSV Challenge Start 开始（用户点击录制 → 菜单操作 → 几秒延迟 → 场景正式开始）。CSV 只有场景内数据，视频包含"前摇"。对齐需要找到视频里哪一帧是场景起点。

```
视频帧号 → 视频时间: time_s = (frame - start_frame) / fps
视频时间 → CSV Timestamp: csv_relative = parse(Timestamp) - parse(Challenge Start)
对齐后: frame = start_frame + csv_relative * fps
```

**首批场景范围：** 只支持动态/静态各一个场景，先跑通再扩展。

#### 实现步骤

1. 写 CSV parser 读取 stats 目录，解析逐击杀数据 + 汇总区元数据
2. 实现 `Challenge Start` 解析 → 相对时间换算
3. 实现场景起始帧自动检测（CV 检测 UI 切换）+ 用户手动校准交互
4. 实现视频 time_s ↔ CSV Timestamp 的对齐 + 插值
5. 用实际 flicking CSV + 对应录像验证对齐精度

### Phase 2：Flick 轨迹提取 + 指标设计

1. 从对齐后的 CV 轨迹中提取每次 flick 的运动段（速度特征切分）
2. 计算速度/加速度曲线，识别加速段和减速段
3. 设计减速段质量指标（平滑度、过冲量、不对称度、端点精度等）
4. 用实际数据验证指标区分度

### Phase 3：集成到现有系统

1. 新的分析管线（flicking analysis pipeline），与 tracking 平行
2. Dashboard 新增 flicking 标签页
3. 和 tracking 指标统一展示（Tension Quadrant 等）

## 架构定位（2026-06-10 讨论）

Flicking 与 tracking 是平行板块，不是子模块。

```
kovaak_tracker/
├── tracking/
│   ├── engine.py         # CSRT 追踪引擎
│   └── analysis.py       # tracking 分析（PTC 等）
├── flicking/
│   ├── csv_parser.py     # KovaaK's CSV 解析
│   ├── aligner.py        # 视频-CSV 时间对齐
│   └── analysis.py       # flicking 分析（减速段指标）
├── vision.py             # CV 检测（共用）
├── video.py              # 视频工具（共用）
├── dashboard_data.py     # dashboard 图表
├── calibration_cli.py    # CLI 校准
├── settings.py           # 输出目录
└── ...（共用函数用到再抽，不提前抽象）
```

**原则：** 写 flicking 时先看 tracking 有没有现成函数能复用，不造轮子。
