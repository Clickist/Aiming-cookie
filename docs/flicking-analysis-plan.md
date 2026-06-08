# Flicking 分析方案

## 目标

扩展 Tension-Aware-Aim-Analyzer 支持 flicking（甩枪）场景分析。当前仅支持 tracking（跟枪）。

## 核心卡点

从纯录像无法检测 miss 的点击——画面零反馈，无法区分"还没点"和"点了但 miss"。纯录像方案有幸存者偏差，只能分析命中样本。

## 选定方案：KovaaK's 场景数据（方案B）

直接读取 KovaaK's 的内部数据（scenario data / 回放文件），获取：
- 每次射击的时间戳
- 命中/miss 状态
- 射击时的准星坐标
- 目标位置

### 优势
- 最准确，无时间对齐误差
- 同时覆盖命中和 miss
- 简化整个系统——不需要 CV 检测目标位置，不需要 CSRT 追踪
- 可以直接算反应时间、点击精度、miss 率等完整指标

### 需要调研
1. KovaaK's scenario data 的数据格式（JSON? CSV? 二进制?）
2. 数据文件的存储位置
3. 包含哪些字段（射击时间、命中/miss、坐标、目标信息等）
4. 数据获取方式（文件读取? API? 回放解析?）
5. 不同版本的数据格式兼容性

## Flicking 分析指标

基于 KovaaK's CSV 数据可实现的指标：

| 指标 | 数据来源 | 可行性 |
|---|---|---|
| 反应时间 | Timestamp 差值（目标出现 → 第一枪） | ✅ 可算 |
| 补枪率 | Shots > 1 的击杀占比 | ✅ 可算 |
| 命中率 | 汇总 Hit Count / (Hit Count + Miss Count) | ✅ 可算 |
| miss 率 | 汇总 Miss Count | ✅ 可算 |
| TTK 分布 | 逐击杀 TTK 字段 | ✅ 可算 |
| 过度射击 | OverShots 字段 | ✅ 可直接读取 |
| 点击精度 | 需要准星坐标 vs 目标坐标 | ❌ CSV 无坐标 |
| 过冲/欠冲 | 需要准星速度曲线 | ❌ CSV 无轨迹 |
| 速度曲线 | 需要准星运动轨迹 | ❌ CSV 无轨迹 |
| 两段式修正 | 需要准星运动轨迹 | ❌ CSV 无轨迹 |

## 实施路径

### Phase 0：搞明白 KovaaK's 怎么产出数据 ✅ 已完成

**调研结论（2025-07-15）：**

KovaaK's 内置自动导出每次 Challenge/Training 的统计数据为 CSV 文件。

**数据位置：**
```
{Steam安装目录}\steamapps\common\FPSAimTrainer\FPSAimTrainer\stats\
```

本机路径：`E:\SteamLibrary\steamapps\common\FPSAimTrainer\FPSAimTrainer\stats\`

**文件格式：** 标准 CSV，命名 `{场景名} - Challenge - {YYYY.MM.DD-HH.mm.ss} Stats.csv`

**数据字段：**

逐击杀数据（每次击杀一行）：
- `Kill #` — 击杀编号
- `Timestamp` — 时间戳（精确到 ms）
- `Bot` — 目标类型
- `Weapon` — 武器
- `TTK` — Time to Kill（秒）
- `Shots` — 这次击杀打了几枪
- `Hits` — 命中几发
- `Accuracy` — 命中率
- `Damage Done` / `Damage Possible` — 伤害
- `Efficiency` — 效率
- `Cheated` — 是否作弊
- `OverShots` — 过度射击

汇总数据（文件末尾）：Kills, Deaths, Fight Time, Avg TTK, Hit Count, Miss Count, Score, 场景名, 灵敏度, DPI, FOV 等。

**场景类型：** tracking 和 flicking 场景的 CSV 格式完全相同，可通过场景名区分。

**已知限制：** CSV 不包含坐标数据（无准星位置、目标位置、准星轨迹）。只能做统计级分析，无法做轨迹级分析。

**问题回答：**
1. ✅ 内置自动导出，无需手动开启
2. ✅ 存在 `stats/` 目录，标准 CSV 格式
3. ✅ 包含射击时间、命中/miss、TTK、射击次数等，但**无坐标**
4. ✅ 格式相同，仅场景名不同
5. 不需要第三方工具，原生支持

### Phase 1：数据读取 prototype
1. 写一个最小脚本读取 `stats/` 目录下的 CSV 文件
2. 解析逐击杀数据 + 汇总数据
3. 按场景类型分组（tracking vs flicking）
4. 计算基础 flicking 指标：反应时间、补枪率、命中率、TTK 分布

### Phase 2：指标设计 + 集成
1. 设计 flicking 场景的指标模型
2. 集成到现有系统（可能需要新的分析管线 + dashboard 标签页）
