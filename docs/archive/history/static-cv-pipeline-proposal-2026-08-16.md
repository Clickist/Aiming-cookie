# 静态点击通用 CV 管线方案（spike 实测 + 设计提案）

- 日期：2026-08-16
- 状态：调研产出，未实施；不构成施工授权
- 素材：真实 run 54006（Valorant Small Flicks，2026-08-16 13:12 采集，60s，130 kill / 7 miss，1920x1080@60fps MP4 + 1ms Raw trace + Stats/Performance）
- spike 产物目录：`E:\DevCache\temp\cv-spike\`（worktree 外，未入 git；真实数据全程只读）

## 0. 一句话结论

**可行。** 用"颜色假设集 + 三形状轮廓分类 + 静止靶轨迹"的便宜 CV（无训练、无模板）就能对白名单外的 static clicking 场景产出目标相对位置测量；spike 在三局真实视频上验证：主素材（Valorant Small Flicks，青色球靶）检出率 98.6% 帧覆盖、击杀时刻靶心与画面中心（准星）偏移中位数仅 (-4.4, +2.1)px、检出+解码 18.7ms/帧（60s 视频全量 67s，在预算内且有明显优化空间）；跨场景颜色无关原型在人形靶（99.7%）与暗色球靶（100%）上同样通过。主要工程风险不在检测，在**时间对齐（MP4 含 capture preroll）与多靶身份歧义（26.5% 帧内存在 <60px 邻近靶对）**，两者都有明确的 fail-closed 处理路径。

## 1. 现状与差距（代码事实）

| 组件 | 现状 | 位置 |
|---|---|---|
| 视觉预处理 | 仅 3 个 exact reviewed producer：whj_smooth_strafe_sphere_easy（tracking）、pasu_small_reload（dynamic）、beants_larger（switching），按 scenario_hash + 1920x1080 精确匹配 | `webapp/backend/worker_visual_producers.py:13-330` |
| 白名单外场景 | `_resolve_reviewed_visual_producer` 抛 `VisualPreprocessingUnavailable`；static clicking baseline 干脆不做视觉（`worker.py:2749` guard + `worker.py:2812` baseline 分支，MP4 只作为可回放证据附带） | `webapp/backend/worker_visual_producers.py:333`、`webapp/backend/worker.py:2715-2827` |
| static clicking 输出 | 输入侧运动学指标（flick 形态、SPARC、submovement 等）+ 固定 limitations：`target_relative_facts_unavailable`、`exact_visual_profile_unavailable`、`outcome_association_unavailable`、`scenario_prescription_unavailable` | `kovaak_tracker/native_flicking_analysis.py:209`、真实 analyses/5/overview.json |
| 已有 CV 底座 | `detect_color_observations_v2`（HSV+形态学+圆度+合并靶歧义剔除+排除区）、`preprocess_visual_video_v1`（逐帧解码+PTS+即时丢弃图像）、episode 提取（beants 式 kill 边界）、temporal bridge | `kovaak_tracker/vision.py`、`kovaak_tracker/visual_signals.py:1288,1488` |
| 命中关联 | `associate_one_shot_kills_v1` 已完整实现（click↔kill↔track 时空+几何唯一性判定，fail-closed），但规则 registry 为空且绑定 exact profile ref，未投产 | `kovaak_tracker/outcome_association.py:157`、`knowledge/scenarios/outcome-association-rules.v1.json`（entries=[]） |
| 时间对齐 | MP4 是"run-owned exact canonical clip"（`visual_video_time_mapping.v1`：pts_origin=0 ↔ canonical_origin=window.start）。**但 capture receipt 含 decodePreroll（本局 121.97ms），PTS 0 实际早于 canonical 窗口起点**，现有 mapping 结构未携带该字段 | `webapp/backend/worker_visual_producers.py:396-442`、run 54006 meta.json video_receipt |

含义：不是从零建 CV，而是把"exact profile 门禁"放宽为"场景家族 + 通用检测器 + 质量 Gate"的通用 producer，复用已有检测/episode/关联三段机械。

## 2. Spike 实测（真实视频）

### 2.1 素材特征（数值刻画，未目视）

- 靶子：青色（HSV H≈170-179，S≈200-226，V≈205-215），静止、全部位于 y≈540 水平带（p5=534 / p95=551，带外仅 0.6%），x 全域分布（3..1916px），半径 3.6-87px（透视层次）。
- 背景：低饱和灰绿（H≈101，S≈51，V≈208），与靶色区分度极高。
- 多靶并存：帧内 blob 数中位 4（1-8），93% 帧有 ≥2 个靶。
- 帧率/分辨率：60.0002fps 恒定，1920x1080，PTS 步长严格 16.667ms。

### 2.2 检测器与性能

检测器：HSV 掩码（H160-179∨0-10, S≥100, V∈[140,245]）→ 开闭形态学 → 轮廓过滤（面积 40..5%帧、纵横比 0.5-2）——与 `detect_color_observations_v2` 同族但更宽松。

| 指标 | 数值 |
|---|---|
| 帧覆盖（≥1 blob） | 3549/3600 = **98.6%** |
| 检测耗时（1080p 全帧，未优化） | **13.9 ms/帧**（中位圆度 0.664） |
| 解码耗时 | 4.8 ms/帧 |
| 全片 60s 端到端 | **67.3s**（vs 架构预算 ~160s/局；visual worker 硬超时 600s） |
| 优化空间 | 靶子 99.4% 在 y∈[480,600] 带内 → 裁剪行带后检测像素量降至 ~11%，预计检测 <4ms/帧，全片 ~30s |

### 2.3 命中关联验证（核心）

方法：137 个 flick 事件（`segmentation_basis=left_button_press`，end_ms 即点击时刻，与 Stats kill 行时刻吻合，如 flick:1 end=641ms ↔ kill#1 canonical 641ms）× 视频帧靶位置 × 准星=画面中心假设。

**验证 1（准星=中心）：** 130 条真实 kill 行（evidence.json `stats.kill.kill_index`）与"濒死靶轨迹"（静止靶跟踪，靶在 kill 帧消失）匹配 71 条，濒死靶相对画面中心偏移：**dx 中位 -4.4px，dy 中位 +2.1px**。准星位于画面中心成立；击杀≈靶死于准星之下成立。

**验证 2（时间对齐）：** 偏移扫描确认 receipt 的 decodePreroll 方向与量级正确：canonical_ms = window.start_ms + pts_ms + 121.97ms（本局）。kill 时刻 vs 靶消失帧的直方图在 0/+17ms（1 帧）处成峰；另见 -150ms 附近次峰（死亡动画残影 ~9 帧后靶 blob 才彻底消失）。

**验证 3（命中/未命中判别）：** 以"点击前 ≤4 帧内存在靶 blob 覆盖中心（含 10px 余量）"判 hit，最优偏移下 90/137 判 hit（期望 130 hit / 7 miss）。判不满的原因见失败模式。

### 2.4 失败模式（如实报告）

1. **快速甩枪的运动模糊**：末段接近帧靶被拉糊，HSV 掩码碎裂或半径骤缩 → margin=0 严格判定只剩 6-9 hit。处理：几何判定需按 blob 外接框（w/2）而非等效圆半径，并对"点击前 2-4 帧"窗口取最优帧而非固定帧。
2. **死亡动画残影**：击杀后靶以碎片形式存活 ~150ms，NN 跟踪器把残影当成新轨迹 → 59/130 kill 没能干净匹配濒死轨迹。处理：以 Stats kill 边界切段（beants episode producer 已有此模式），段内只认"kill 前最后稳定位置"，不跟踪死后。
3. **多靶身份歧义**：26.5% 帧内存在间距 <60px 的靶对，朴素 NN 跟踪会并轨/错配。处理：命中关联不需要持久 identity——只需要"唯一覆盖中心的 blob"（`associate_one_shot_kills_v1` 的几何唯一性判定正是这个语义）；持续 identity 仅在后续靶切换分析里需要，届时按"静止靶+出生/死亡事件"而非逐帧 NN 重建。
4. **自校准/overshoot 演示未成**：用 flick 位移对靶角偏移回归的 counts/px 标定，样本太少（n=3 单靶帧）噪声过大。非阻塞：标定链已有确定数据源（Stats config：FOV=103、DPI=1600、cm_per_360=54.43 → 0.0536°/px、0.0105°/count），视频回归只作交叉验证。

### 2.4b 跨场景泛化检查（颜色无关原型，三形状先验验证）

用"自动 hue 峰发现 + 同一检测器"的原型（无任何针对场景的手调）在另外两局真实视频上验证：

| 场景 | 自动发现的靶色 | 帧覆盖 | 每帧 blob | 形状签名 | 结论 |
|---|---|---|---|---|---|
| 53993 Humanoid Strafe（人形） | hue=5（红），优势 74.6% | 299/300 = **99.7%** | 1.9 | circ=0.52、含 aspect<0.7 的竖长轮廓、y∈[280,650] 二维散布 | 人形靶通过：颜色自动发现 + 人形形状签名吻合 |
| 54004 1wall 6targets small（暗色球） | 饱和峰=85 指向**绿色背景**（99.9%）→ 饱和假设 0% 检出；**暗色假设（V≤80）** 200/200 = **100%** | 3.7 | aspect=1.25、circ=0.70 | 暗色球通过，但暴露"仅饱和色假设不够"的真实失败模式 |
| 54006 Valorant Small Flicks（青色球） | hue=175 | 98.6% | 4 | 见 2.1/2.2 | 主素材，全链路验证 |

（54004 检测耗时 24.4ms/帧、53993 27.6ms/帧，含跨 3 帧取样的解码分摊，量级与主素材一致。）竖直胶囊型靶在现有三局视频中均未出现，**待验证**——其形状签名介于球与人形之间（aspect 0.35-0.75、矩形度高），分类器按先验预留区间，P0/P1 用一局含胶囊靶的实测场景（如典型 pasu 变体或 vertical 场景）补验。

### 2.5 spike 产物清单（`E:\DevCache\temp\cv-spike\`）

- `run54006.mp4`（真实视频副本）；`frame_*.png`、`annot_*.png`（检测框+准星十字标注样例帧）
- `detections.json`（3600 帧全量检测：pts/ms/blob 列表）；`association.json`（137 点击判定）
- 脚本：`probe.py`、`color_analysis.py`、`detect.py`、`associate.py`、`scan_offset.py`、`tracks.py`、`kills_assoc.py`、`generic_check.py`（跨场景颜色无关原型，53993/54004）

## 3. 通用管线设计（提案）

### 3.1.0 检测目标定义（三形状先验）

点点提供的领域先验（2026-08-16）：KovaaK 的靶子**只有三种形状**，万变不离其宗：

1. **球形靶**（各种颜色）
2. **竖直胶囊型靶**（各种颜色，竖着的 pill 形）
3. **人形靶**

含义：检测器的目标空间是**封闭集合**（3 个形状模板 × 颜色通道），鲁棒性要点在颜色变化与大小/距离变化，不在形状开放性——因此不需要通用目标检测器（YOLO 类），轮廓特征分类即可。三个真实场景的实测形状签名：

| 场景 | 靶型 | 实测签名（中位数） |
|---|---|---|
| Valorant Small Flicks（54006） | 球/扁球 | aspect(w/h)=1.31、fill=0.66、circ=0.66；静止帧与运动帧一致（非模糊所致，KovaaK 球靶渲染本身略扁） |
| Humanoid Strafe（53993） | 人形 | aspect=1.30（p5=0.56 含竖长人形）、fill=0.70、circ=**0.52**（显著低于球）、位置 y∈[280,650] 二维散布（非水平带） |
| 1wall 6targets small（54004） | 球（暗色） | aspect=1.25、fill=0.61、circ=0.70、暗色（V≤80） |

形状分类器（封闭判别，无需训练）：

```
球     : aspect ∈ [0.7, 1.6], circ ≥ 0.60, fill ≥ 0.55
竖胶囊 : aspect ∈ [0.35, 0.75], circ ≥ 0.55, fill ≥ 0.65（矩形度高于球）
人形   : circ < 0.60 或 aspect < 0.7（竖长）且面积下限更高（人形靶通常更大）
```

阈值以三场景实测分布为起点，P0 用真实 fixture 固化；分类结果本身进质量 Gate（某局若三形状都不匹配 → fail-closed 降级）。

### 3.1 管线阶段

```
MP4 → [1 解码] → [2 靶检测(自适应颜色)] → [3 像素→角度标定] → [4 静止靶轨迹重建]
    → [5 与 Raw 轨迹对齐] → [6 target-relative 指标] → [7 质量 Gate] → [8 并入 analyzer]
```

1. **解码**：复用 `preprocess_visual_video_v1` 的逐帧模式；帧预算/分辨率校验保留，但分辨率改为"记录并缩放"而非 1920x1080 硬门。
2. **靶检测（自适应颜色假设 + 三形状分类）**：不再依赖人工审核的 HSV 区间。首 N 帧子采样（本局 25 帧即够）枚举**颜色假设集**：{主饱和 hue 峰、暗色簇（V≤80）、高亮簇（V≥200 低饱和）}，每个假设跑一遍掩码检测，按"blob 数量/大小合理 + 三形状分类命中 + 时空稳定"打分取最优假设。**饱和峰假设对暗色靶场景会指错**（54004 实测：绿色背景拿了 99.9% 饱和峰，真靶是黑球，暗色假设才检出 100%）；假设都不达标 → fail-closed 降级。KovaaK 靶色由皮肤决定但单局内恒定。
3. **像素→角度**：水平 deg/px = FOV/宽（Stats config FOV；Valorant/Horiz FOVScale 103）。counts→deg 用 DPI+cm_per_360。视频侧靶角偏移与输入侧积分角偏移可互相回归校验（自检 Gate）。
4. **静止靶轨迹**：以"出现/消失事件"组织（出生帧、死亡帧、存活期位置中位数），不做逐帧 identity；死亡事件与 Stats kill 行做时间窗配对。
5. **对齐**：canonical_ms = window.start + pts_ms + decodePreroll_ms（**需把 preroll 写进 video_time_mapping，升版本到 v2**——这是本方案对冻结合同的唯一必要扩展）。
6. **target-relative 指标（新解锁）**：
   - 每 flick：目标角偏移（deg，x/y 分量）、实际积分位移（deg）、overshoot 比（signed）、落点误差向量（deg）、首次接近角。
   - 每 miss：点击时刻靶相对准星向量 = 未命中距离/方向（deg）——spike 已可直接测量。
   - 每 kill：残差精度（靶心-准星距离，spike 实测中位 ~16px ≈ 0.85°）。
   - 会话级：overshoot 分布、方向偏差分布、miss 空间图（左右/上下系统偏差）。
7. **质量 Gate（每局产出 `generic_visual_quality` 摘要）**：靶色置信度、帧覆盖率（spike 基线 98.6%）、kill 配对率、角标定回归残差。任一不达标 → 该局回退现有 input-only baseline，limitations 照实标注（fail-closed，与现有 `visual_quality_profile` 哲学一致，但不做 exact-hash 预审）。
8. **接入 analyzer**：作为 static_clicking baseline 之上的增强层，替换 `target_relative_facts_unavailable` limitation（仅在 Gate 通过时）。

### 3.1.1 胶囊靶实测补录（主会话 2026-08-16，run 54008 VSS GP9；点点现场提供素材与靶色"黑色"）

- 素材：VSS GP9，黑色竖胶囊，白底场景（S 中位=0）。四个朴素假设全灭：饱和 hue 峰被白底废掉；V≤80 暗簇 + 5×5 开运算把靶子磨碎（抗锯齿边缘 V 落在 80-90），只剩 HUD 横条
- 破案路径：帧间差分定位中心竖长块 → 解剖该块真实 HSV → 修正为 **V≤90、S<60、无开运算** → 竖胶囊现形
- 最终检测（720 采样帧）：**62 次检出全部落在竖胶囊签名**（aspect 中位 0.47 范围 0.38-0.79；高 63-134px 中位 107px），数量与 switching 击杀节奏量级吻合；帧覆盖 8.6% 为单靶短存活的场景性质而非检测缺陷
- **设计修订**：颜色假设集增加第四条「低饱和暗色簇（V≤90、S<60、无开运算）」；HUD 排除 = 形状区间 + 中心活动区坐标窗；白底黑靶场景由该假设覆盖
- 三靶型状态：球 ✅（54006/54004）、人形 ✅（53993）、竖胶囊 ✅（54008 本节）。风险表"竖直胶囊靶未实测"可关闭。spike 脚本 `E:\DevCache	emp\cv-spike\capsule_final.py`

### 3.2 选型论证

- **颜色分割 vs 模板匹配**：靶形随距离/模糊变化、透视缩放 3.6-87px，模板需多尺度金字塔，成本高且对皮肤变体脆；颜色分割对形状不敏感、13.9ms/帧（可裁剪到 <4ms），且仓库已有成熟实现与歧义处理。选颜色分割。
- **颜色分割 vs 通用检测器（YOLO 类）**：三形状先验把目标空间封死在 3 个模板——封闭集合用轮廓特征判别即可，不需要开放集检测器；且神经网络违反本地确定性+无重模型分发的约束（模型文件、跨平台、确定性都成问题），背景也干净到不需要。颜色假设集 × 三形状分类的两级判别已在球/人形/暗色球三类真实素材上验证（2.4b）。仅当出现"三形状都不匹配"的皮肤时才需要再议。
- **准星轨迹：Raw 重建 vs 视频检测**：用 Raw。KovaaK 准星恒在画面中心（spike 验证 dx/dy 中位 <5px），视频里检测准星毫无增益且引入遮挡/颜色冲突；Raw 1ms 采样远高于 60fps，且 flick 形态指标已全部由 native 管线产出。视频只负责"目标在哪里"，输入负责"准星怎么动"——两者在 canonical 时间轴上拼接。
- **命中关联**：复用 `associate_one_shot_kills_v1` 的判定语义（时间窗唯一 + 几何唯一 + fail-closed），但走新的 generic 规则绑定（不要求 exact scenario profile ref），输出标注 `association_kind=generic_visual`、confidence<1.0，与 reviewed 路径的产品语义分级。

### 3.3 架构接入点

| 层 | 接入 |
|---|---|
| worker 阶段 | `worker.py` multimodal 分支：static_clicking 不再直接跳过视觉（`worker.py:2749`），新增 generic producer 解析路径（解析失败仍安静降级，行为不变） |
| 隔离进程 | 复用 `visual_worker_process` + 600s 超时；67s 实测在预算内 |
| producer 注册 | `worker_visual_producers.py` 新增 `_build_generic_static_clicking_producer()`：selector 按 aim_family=static_clicking + resolution 匹配（无 scenario_hash），quality profile 状态用 `limited`（区别于 exact reviewed 的 `accepted`） |
| evidence 层 | `analysis_evidence_artifact.v1` 的 signal_bundles（靶轨迹）/event_bundles（出生/死亡/关联事件）+ 新 metric_records；沿用现有 schema，无破坏性变更 |
| 指标层 | `STATIC_CLICKING_BASELINE_ANALYSIS_VERSION` 旁加 `static_clicking.generic_visual.v1` 增强版 |
| Coach 侧解锁 | overshoot 方向/幅度、miss 向量分布、落点残差——正是 [[peripheral-analysis-patterns]] 与知识库里"输入侧只能看到运动形态、看不到落点"缺的另一半证据 |

### 3.4 分阶段工作量

| 阶段 | 范围 | 预估 |
|---|---|---|
| **P0 最小可用** | 颜色假设集（饱和峰/暗簇/亮簇）+ 三形状分类 + 静止靶出生/死亡 + kill 配对 + hit/miss 关联 + miss 向量与 kill 残差指标 + 质量 Gate + preroll 进 mapping v2 + 测试（真实 run 54006 作 fixture） | 3-5 个工作日 |
| **P1** | flick 级 target-relative 全指标（overshoot 比、落点误差、首近角）+ 角标定回归自检 + 逐 flick 视频/输入联合时间线 + 前端 DataView 展示 | 3-4 个工作日 |
| **P2** | 多色/多类靶（换皮肤鲁棒性）、非水平带场景（垂直/2D 网格靶）、dynamic/target_switching 家族的 generic 化、出生位置分布（spawn 偏好分析） | 按需评估 |

### 3.5 风险与替代路线

| 风险 | 影响 | 缓解 |
|---|---|---|
| 靶色接近背景/皮肤暗色 | 饱和 hue 峰指向背景（54004 实测） | 颜色假设集必含暗色簇/亮簇假设；假设间用形状签名+时空稳定性打分裁决；仍不唯一 → fail-closed |
| 暗靶 × 暗背景（V 同低） | 暗色假设掩码爆掉 | 面积上限+形状分类兜底；三假设全败 → 降级并记录 limitation |
| ~~竖直胶囊靶未实测~~ | 已实测（2026-08-16 run 54008，见 3.1.1）：62 检出全落形状签名 | 关闭；假设集增补低饱和暗色簇 |
| 死亡动画残影 | kill 配对率下降（spike 59/130 未干净匹配） | kill 边界切段 + 段内末稳定位置；动画长度按帧统计自动学习 |
| 运动模糊 | 接近帧漏检 | 外接框半径 + 回看窗口取最优帧；必要时相邻帧靶位置插值（静止靶位置恒定，天然抗模糊） |
| 多靶歧义 | 身份错配 | 命中判定只需"中心唯一覆盖"；identity 类分析后置 P2 |
| preroll 缺失/漂移 | 时间错位 → 全盘错 | mapping v2 显式携带；无 receipt preroll 时用 kill-消失相关自动估偏移并标注不确定性 |
| 分辨率/FOV 变体 | 角标定误差 | 全部从 Stats config 每局读取，不写死；分辨率缩放系数进 detector config |
| 性能（1080p60×300s） | 超预算 | 实测 67s/60s；裁剪带+可选半分辨率后 ~30s/60s，300s 局 ~150s，仍在 160s 内；再不够就子采样（静止靶只需出生死亡邻域全帧率） |

**替代路线（若颜色路线在某皮肤族彻底失败）**：帧差分（静止背景 + 运动靶出生/死亡天然产生差分信号）作为检测兜底，成本同为传统 CV 量级；仍不引入神经网络。

## 4. 本次 spike 验证清单（可复跑）

```text
1. detect.py       → 检出率 98.6%、13.9ms/帧          [通过]
2. kills_assoc.py  → kill↔濒死靶 71/130、dx/dy≈0      [机制成立，跟踪器待段化]
3. scan_offset.py  → preroll +122ms 方向/量级确认      [通过]
4. associate.py    → hit 判定 90/137（期望130）        [部分：模糊/残影致漏判，改进路径明确]
5. 自校准回归       → 样本不足                          [未成，改用 Stats config 确定标定]
6. generic_check   → 53993 人形 99.7%、54004 暗球     [通过；暴露饱和峰假设盲区，
                                                      暗色假设 100% 补齐；胶囊靶待验]
```

环境：仓库 `.venv`（cv2 5.0.0 / numpy 2.4.6 已存在，未改动 requirements.txt / 产品代码 / git 工作树）。
