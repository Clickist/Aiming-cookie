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

## Flicking 张力指标

### 理论基础

Flick 不是单次爆发，而是完整的运动过程：**看到目标 → 加速 → 运动 → 减速 → 微调 → 点击**。

从 CV 轨迹可以提取每次 flick 的速度曲线。张力指标围绕**减速段质量**设计，与 tracking PTC 同源但形态不同：

| | Tracking | Flicking |
|---|---|---|
| 运动形态 | 持续修正 | 加速→减速→点击 |
| 张力问题 | 修正力度是否匹配误差 | 减速制动是否平滑精准 |
| 过度握紧 | PTC 高，抖动大 | 过冲、减速段抖动/锯齿、Shots≥2 多 |
| 发力不足 | PTC 低，跟不上 | 欠冲、减速过早 |

### 指标方向

- **减速平滑度**：减速阶段加速度变化是否平滑（有无抖动/锯齿）
- **过冲量**：目标是否穿过中心再回来
- **微调次数**：减速后到点击之间的方向改变次数
- **减速耗时 vs 总耗时**：减速占整个 flick 的比例

### 与 Tracking 代码的关系

底层函数（`apply_smoothing`、`calc_derivative`）可复用。`extract_kinematics` 和 `evaluate_mechanics` 需要重新设计——flicking 是离散事件分析，不是连续轨迹分析。

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

1. 写 CSV parser 读取 stats 目录，解析逐击杀数据 + 汇总区元数据
2. 实现 `Challenge Start` 解析 → 相对时间换算
3. 实现视频 time_s ↔ CSV Timestamp 的对齐 + 插值
4. 用实际 flicking CSV + 对应录像验证对齐精度

### Phase 2：Flick 轨迹提取 + 指标设计

1. 从对齐后的 CV 轨迹中提取每次 flick 的运动段（目标 spawn → 击杀）
2. 计算速度/加速度曲线，识别加速段和减速段
3. 设计减速段质量指标（平滑度、过冲量、微调次数等）
4. 用实际数据验证指标区分度

### Phase 3：集成到现有系统

1. 新的分析管线（flicking analysis pipeline）
2. Dashboard 新增 flicking 标签页
3. 和 tracking 指标统一展示（Tension Quadrant 等）
