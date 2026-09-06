# cleaned 轮次旁车合同 v1（SIDECARS）

> 定位：`cleaned/<source>/` 目录里、`round_NN.jsonl` + `rounds_index.json` 之外的**附加
> 数据件**（旁车）合同。目标：让一个轮次目录成为自足的分析单元（目标轨迹 + 视角 +
> 输入 + 目标尺寸 + 对齐回执），Aiming-cookie 导入侧/分析侧按合同消费。
> 命名铁律：**旁车文件名不得以 `round_` 开头**——Aiming-cookie 导入器按 `round_*.jsonl`
> glob 轮文件，旁车匹配该模式会被当孤儿轮误导入。

## 0. 件清单

| 文件 | 生成者 | 内容 |
|---|---|---|
| `views_NN.jsonl` | merge_channels.py | 每行 `{"t", "pos":[x,y,z], "rot":[pitch,yaw,roll], "fov"}` |
| `inputs_NN.jsonl` | merge_channels.py | 每行 `{"t", "dx", "dy", "btn":[...]}` |
| `bb.json` | sce_bb.py from-crosscheck | 挑战窗 → 每目标命中盒（MainBB*） |
| `merge_manifest.json` | merge_channels.py | 对齐回执 + 每轮覆盖统计（+可选几何回执） |
| `scenario.json` | scenario_meta.py label（既有） | 轨迹签名场景标签 proposal（不变） |

## 1. 时间域（重要）

- 旁车 `t` 与轮文件 `t` **同域**：源录制文件相对秒（`round_NN.jsonl` 的 `t` 字段，
  非 `tr`）。消费者按 t 直接 join。
- 绝对时间：`epoch = merge_manifest.alignment.s_epoch_of_t0 + t`。
- `rounds_index.json` source 条目的 `t0_epoch`（cleaner 配套透传，仅新录制文件有）
  是同义的精确锚；有它时 merge_channels 直接采用、互相关仅作校验。
- 旧录制文件（无 clock_map）：`s_epoch_of_t0` 来自文件名墙钟粗锚 + 池化
  click↔death 互相关细化（±5ms 级，见 FORMAT §5.3/§5.4）。

## 2. views_NN.jsonl（相机通道切窗）

- 来源 `camera_probe_out_*.jsonl` 的 `ev:"cam"` 帧（null 行丢弃，计数入 manifest）。
- 字段语义同 FORMAT §2.2：`pos` UE4 世界 cm、`rot` FRotator 度（Pitch/Yaw/Roll）、
  `fov` 度。轮窗 = `rounds_index` 该轮 `t_start..t_end`；**轮窗可能重叠**
  （cleaner 按出生事件切分，长寿命目标可跨轮）——重叠段属于所有包含它的轮，
  views 与 inputs 同语义。
- dt 特征：~32Hz 可变间隔；`merge_manifest.rounds[].view_gaps_gt_200ms` 标空洞。

## 3. inputs_NN.jsonl（OS 输入切窗）

- 来源 `input_log*.jsonl` 的 `ev:"m"` 行：`dx/dy` 设备原始计数、`btn` 按钮沿
  （`L_down` 等）。时间已从 QPC 域换算到轮 t 域（经 clock_map 的 epoch↔perf 换算
  + §1 的 s）。**不要**用武器通道 D 的 ±50ms 电平当点击（FORMAT §4.4-5）。
- 点击聚类口径：50ms 内连点并簇（与 crosscheck 系列一致）；逐发沿保留在旁车里，
  聚类由消费者按需做。

## 4. bb.json（目标命中盒）

- `schema_version: round_bb.v1`；`challenges[]`：每挑战窗
  `{perf_file, scenario, rounds[], window_t:[lo,hi]（轮 t 域）, timescale, bots[]}`。
- `bots[].character.bb = {type, radius, height, has_head, head_radius}`——来自本机
  `.sce` 的 `[Character Profile].MainBB*`（运行时解析，新场景天然覆盖，零预制表）。
- **消费红线**：bb.radius 是碰撞盒半径（cm）；角半径 = atan(radius / 视点-目标距离)，
  距离用 views 的 pos 与轮帧目标坐标算（同 UE4 世界域）。**bb 即目标尺寸权威**
  （.sce 静态 MainBB）。~~变尺寸场景以 .perf 的 targetSize 事件流为准~~
  **[修订 2026-09-02] .perf 的 targetSize 通道实测恒空**：schema 有字段 16（两侧
  解析器 perf_probe.py / AC performance_parser.py 均有映射），但本 build 从不发射
  ——202 个 .perf 全库扫描（E 盘 133 + 仓副本 69，含 4 个 pasu 变体与 SuperbAim）
  零事件；"解析器有映射"≠"数据存在"。若未来出现真变尺寸场景（TargetSize modifier
  类），需另走内存碰撞体半径探针（Scale3D 已证死路，§8）或视频测径，届时再立
  尺寸时间线旁车合同。
- 挑战窗配对（crosscheck_session.pair_members，2026-09-02 修订）：优先取**完全
  覆盖挑战窗**的轮（freeplay 长寿目标并入的覆盖轮合法，pasu round_04 案：轮窗早
  挑战窗 44s）；无覆盖轮时退回窗相交拼接（6targets 1+2 先例）。配对后 anchor_check
  打分校验：官方 kills>0 且锚处 score=0 才拒绝出窗；死亡偏多不拒绝（官方 kills
  权威，deaths 仅诊断，pasu +64% 异常见 §9）。
- mega-round（一轮跨多挑战）：按 `window_t` 分段取各自的 bb。

## 5. merge_manifest.json（对齐回执）

- `alignment`：`{method, seed_epoch, s_epoch_of_t0, xcorr{score,n_deaths,plateau_s,
  baseline{mean,p95,max}, click_to_death_latency_ms}, accepted, t0_epoch_from_index}`。
- 接受判据（本工具实现）：峰值 ≥5×基线均值 **且** 平台宽 ≤50ms。FORMAT §5.3 的
  "≥5×基线p95"按稀疏会话标定；连续点击密集会话（pasu/连点类）随机基线 p95 可达
  峰值 1/3（250ms 窗 × ~1点击/秒 的碰撞率），峰形锐度才是判别量（实测平台 4ms）。
- **[fix 2026-09-01]** 带**精确 index t0_epoch 锚**（clock_map 来源）时判据换轨为
  **双分级验收**（`alignment.accept_grade` / `accept_rule` 自描述）：
  - `click_geom`（点击富集局）：`check.n≥5 且 check.median_deg≤1° 且
    |xcorr峰−锚|≤250ms`——0901 验证局中位 0.233°/<1°占98%，与 0831 黄金回执一致；
  - `tracking_aim`（跟枪/hold-fire 局）：`aim_check.n≥5 且 median_deg≤5° 且
    share_lt_10deg≥0.5`。click 稀疏时互相关峰被场景动力学锁偏（峰偏≈−平均击杀延迟；
    2156 TileFrenzy tracking 实测 +1.125s，而双录 target_poll2×2 + 相机 yaw↔输入
    幅度 + 目标方位角↔相机 yaw 三方对账实证三通道钟互差 ≤±40ms——峰偏与钟无关），
    故该级 xcorr 降为纯诊断；`aim_check`（死亡前 200ms 窗 准心→垂死目标 最小夹角，
    点击无关）在 index 锚上评估，锚真错位时数量级恶化（2156 负对照：正确锚中位
    1.75°/<10°占56% vs 错位 ±1.125s → 34°/10% 与 44°/3%），不会静默放过坏锚。
    两级都不过 ⇒ 拒写旁车（退出码 2）。
  - 无精确锚的旧判据与 fail-closed 不变。
- 不达峰且无 t0_epoch ⇒ **拒绝写任何旁车**（退出码 2，fail-closed）。
- `rounds[]`：每轮 `{n_views, view_gaps_gt_200ms, n_inputs}` 覆盖统计。
- `check`（--check 时）：击杀点击瞬间 准心→垂死目标 角误差分布。**几何+对齐总验收**：
  实测参照 final_0831（596 击杀）中位 0.466°、p25 0.229°、66% <1°、71% <3°。
  显著劣于该水平 = 对齐或几何链路有问题，拒绝消费。
- `aim_check`（精确锚路径必含）：死亡前 200ms 窗 准心→垂死目标 **最小**夹角分布
  （`{n, median_deg, p25/p75_deg, share_lt_5deg, share_lt_10deg, window_ms, per_round}`）。
  tracking_aim 级的验收证据；参照 2156 tracking 中位 2.48°/56% <10°（错位对照 34°/10%）。
  消费侧注意：跟枪局碎片轮（场景切换收尾的 life）p75 可达数十度，取中位与占比判读。
- 过期判定：`rounds_index_mtime_ns` 与现 index mtime 不符 ⇒ 旁车过期，重跑
  merge_channels（cleaner 重洗后轮窗可能移动）。

## 6. 录制器/cleaner 配套变更（2026-08-31，本批落地）

1. `target_poll2.py`：首行 `clock_map`（epoch 锚）；`--scale` 每秒每目标读
   `ComponentToWorld` FTransform 的 Scale3D（+0x20）写 `ev:"scale"` 旁线（尺寸
   缺口的内存侧兜底路线，待真机验证值域）；calibrate 增加偏移哨兵（样例全出域/
   全零 ⇒ fail-fast，防游戏更新后静默错坐标）。
2. `camera_probe.py`：持续失联 >15s 熔断 + main 重附着循环（新进程新文件）；
   `--wait` 初始也等游戏进程。
3. `cleaner.py`：`load_frames` 跳过 `ev` 非 frame/None 的行（clock_map/scale），
   clock_map 锚透传为 `rounds_index.sources[].t0_epoch`（旧输入文件无此键，产物
   不变——回归已验证）。**注意：旧版 cleaner 吃新录制文件会把 clock_map 的 epoch
   当 t 排到末尾，两个补丁必须配套升级。**

## 7. 验收基线（final_0831 实测，2026-08-31）

- 13 轮全部出旁车；views 覆盖 401~9152 帧/轮，>200ms 空洞 **0**；
- 对齐：s*=1788107500.368，平台 4ms，596/833 死亡有点击关联，
  click→death 中位 56.2ms（含 31ms 死亡量化 + 50ms 聚类的卷积）；
- 几何回执：见 §5；
- bb：6 挑战窗全解析（1wall6ts r=60 / pasu r=60 / Controlsphere r=20 /
  Humanoid Cylindrical r=37 h=185 等）。

## 8. 待真机验证项（点点）

- ~~`target_poll2.py --scale`~~ **已验证（2026-08-31 晚）**：31 地址×两场景全读 [1.0,1.0,1.0] —— actor 缩放不编码尺寸，**内存路线关闭，.sce 配置路线（sce_bb.py）为唯一答案**。
- ~~相机熔断/重附着~~ **已验证（0831 晚，两个完整周期）**：退出→15s 熔断停笔→10s 轮询→重开自动重附着开新文件。
- ~~进程死亡熔断（target_poll2）~~ **实测发现真 bug 并已修**：RPM 对已退出进程返回 None 不抛异常 → run() 永远写空帧、main 重附着循环永不触发（§1.3-8 的"confirmed"仅覆盖抛异常死法）。修复=差分周期搭车活性探测（对象数组头，连续 15s 失败熔断）。实测 19:41:26 精确熔断 + 19:44:02 重附着新文件（该 main 循环首次真实运行）。
- ~~t0_epoch 全链路~~ **已验证（0831 晚 verify0831 会话）**：录制器首行 clock_map → cleaner 透传 → merge 走精确锚路径；与互相关独立求出的锚**仅差 65ms**（两种独立方法互证）。
- 偏移哨兵：被动项，待 KovaaK 更新时自然检验。
- verify0831 会话几何回执：170 击杀中位 **0.231°**、87% <1°（优于 final_0831 的 0.466°/66%，该局轮次更干净）。
- 附带发现：三个录制器均可由后台进程拉起（对齐未来 supervisor 总控）。

## 9. 已知异常：pasu 挑战窗 deaths≫kills（非击杀性目标更换，待专项）

- session_0901_2132（2026-09-01）1wall5targets_pasu 挑战窗 [411.052, 497.249] 内
  cleaner 死亡 **156** vs 官方 kills **95**（+64%）；同会话 1wall 6targets small
  为 114 vs 112（正常，±2 属对齐抖动）——排除对齐/聚类系统性偏差。
- 这是"死亡通道 ≠ 官方 kills 即不可作对齐权威"红线的又一形态：死亡流混入了
  **非击杀性的目标消失**。机制未明：.sce 无任何可实现它的字段（§4 修订同日复核），
  疑似场景内置的目标更换/重生策略（0.1s 秒重生场景）；poller 侧按 pointer 生命
  事件记 death，无法区分"被打死"与"被场景回收"。
- 处置（已落地）：官方 kills 为配对/验收权威，死亡计数降级为诊断量——
  score_offset 逐 tick min(kills, deaths) 天然抗死亡偏多；配对拒绝条件只有
  "kills>0 且锚处 score=0"（§4）。
- 后续专项（未排期）：内存侧对照 actor 生命周期，把"击杀死亡"与"回收死亡"分开
  （如读 Health 或 despawn 原因），届时死亡通道才可重新升级为对齐证据。
