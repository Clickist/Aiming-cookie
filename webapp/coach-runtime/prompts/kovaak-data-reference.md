KovaaK 数据素养参考

本文件帮助教练理解 KovaaK 的 Stats CSV 和 Performance 二进制数据中每个字段的含义、单位和好方向，以及如何从这些数据中发现问题。这是数据解读的知识参考，不包含训练处方。

1 数据来源概览

每次 KovaaK 练习产生两个核心数据源：

Stats CSV：一个纯文本 CSV 文件，包含击杀表、武器汇总、挑战总结和输入配置四个区块。这是 outcome 层面的事实源——记录了每一击的结果。

Performance（.perf）：一个 protobuf 格式的二进制文件，包含时间轴上的事件流。这是事件层面的事实源——按时间顺序记录射击、命中、击杀、分数等增量变化。

Raw Input：原始鼠标输入记录（timestamp_ms, dx, dy, buttons），用于运动学分析。这是 kinematics 层面的事实源——记录了玩家手部的每一次物理移动。

三者通过时间对齐关联到同一个挑战窗口。Stats 的 Challenge Start 提供壁钟锚点，Performance 的 challenge_start_utc 提供 UTC 锚点，Raw Input 通过 epoch 毫秒对齐到挑战窗口。

2 Stats CSV 字段

2.1 击杀表（Kill Table）

每行记录一次击杀，固定 13 列：

Kill #（int，count）：击杀序号，从 1 开始递增。用于定位某次击杀在整局中的位置。

Timestamp（timestamp，milliseconds）：壁钟时间戳，格式 HH:MM:SS.mmm。减去 Challenge Start 得到场景内相对秒数。用于时间序列分析和退化检测。

Bot（string，untrusted_text）：被击杀的目标名称。不可信文本，不用于诊断。

Weapon（string，untrusted_text）：使用的武器名称。不可信文本，可用于按武器分组比较。

TTK（float，seconds）：Time to Kill，从这次击杀开始到目标被消灭的时长。越短越好。TTK 分布的中位数反映整体杀伤效率，离群值（远高于中位数的 TTK）指向具体某几次击杀中的问题。

Shots（int，count）：这次击杀总共射击的弹数。与 Hits 配合计算命中率。

Hits（int，count）：这次击杀中命中的弹数。

Accuracy（float，ratio）：命中率 = Hits / Shots。范围 0-1。越高越好，但需结合 TTK 一起看——高命中率加长 TTK 可能意味着过于保守。

Damage Done（float，source_damage_unit）：这次击杀造成的伤害。游戏内伤害单位，跨游戏不可比。

Damage Possible（float，source_damage_unit）：这次击杀中所有射击如果全部命中能造成的最大伤害。

Efficiency（float，ratio）：效率 = Damage Done / Damage Possible。范围 0-1。衡量弹道利用率，越高说明每一发都打在了能造成伤害的地方。

OverShots（int，count）：过射数——目标已经被消灭后额外打出的弹数。越低越好。高过射说明击杀确认不够果断，或在目标死后仍在射击。

Cheated（bool，boolean）：标记这次击杀是否使用了作弊检测认定的异常方式。通常为 0。

2.2 武器汇总行（Weapon Aggregates）

紧跟击杀表之后，按武器汇总：

Weapon（string）：武器名称。Shots、Hits 为该武器全程总射击和命中数。Damage Done、Damage Possible 为该武器全程总伤害和可造成伤害。用于比较不同武器在同一场景中的表现。

2.3 总结块（Summary）

Key:Value 格式的整局聚合：

Kills（int，count）：总击杀数。核心 outcome 指标。

Deaths（int，count）：死亡次数。

Fight Time（float，seconds）：战斗总时长。与 Kills 配合反映节奏。

Time Remaining（float，seconds）：挑战剩余时间。接近 0 说明整局被充分利用。

Avg TTK（float，seconds）：平均击杀时间。这是击杀表中所有 TTK 的均值。注意均值会被离群值拉偏，应配合分布看。

Total Overshots（int，count）：全局过射总数。高过射说明确认习惯需要改善。

Damage Done（float）：全局总伤害。

Damage Taken（float）：全局承受伤害。高伤害_taken 加低 Deaths 说明靠血量硬扛而非闪避。

Hit Count（int，count）：全局总命中数。Miss Count（int，count）：全局总未命中数。

Midairs / Midaired（int，count）：空中目标相关计数，部分场景才有意义。

Directs / Directed（int，count）：直击相关计数，部分场景才有意义。

Reloads（int，count）：装弹次数。异常多说明射击节奏有问题。

Distance Traveled（float，source_native）：玩家移动距离。大部分瞄准场景中此值很小。

MBS Points（float，points）：Multi-Bot Scenario 分数。

Score（float，points）：总得分。核心 outcome 指标，综合反映表现。

Scenario（string，untrusted_text）：场景显示名。不可信，不用于诊断。

Hash（string，scenario_hash）：场景哈希。用于精确匹配同一场景的不同练习记录。

Challenge Start（timestamp）：挑战开始的壁钟时间。是整个时间对齐的锚点。

Pause Count（int，count）：暂停次数。Pause Duration（float，seconds）：暂停总时长。频繁暂停影响数据连续性。

Avg Target Scale（float，source_scale）：平均目标缩放。Avg Time Dilation（float，source_scale）：平均时间缩放。非 1.0 时说明场景有难度修正。

2.4 配置块（Config）

Key:Value 格式的输入和画面配置：

DPI（int，counts_per_inch）：鼠标 DPI。与 Horiz Sens 和 yaw 一起决定 cm/360。

FOV（float，source_scale）：视野角度。影响目标表观大小和移动速度感知。

Horiz Sens（float，source_scale）：水平灵敏度。Vert Sens（float，source_scale）：垂直灵敏度。

Sens Scale（string，untrusted_text）：灵敏度比例尺，通常是游戏名（Source、Valorant、Apex Legends 等）。决定 yaw 值，用于 cm/360 计算。

Sens Increment（float，source_scale）：灵敏度微调步长。

FOVScale（string，untrusted_text）：FOV 缩放模式。

Resolution（pixels）：分辨率（宽 x 高）。影响画面精细度和目标表观大小。

Avg FPS（float，fps）：平均帧率。低帧率影响输入延迟和视觉流畅度。Max FPS（float，fps）：配置的帧率上限。

Input Lag（float）：配置的输入延迟模拟。Crosshair（bool）：是否使用自定义准星。Crosshair Scale（float）：准星缩放。Crosshair Color（rgba_hex）：准星颜色。

3 Performance 事件（.perf）

Performance 文件是 protobuf 格式，包含一个 header 和一系列 event。每个 event 带有时间戳和一个 payload（oneof，只有一个 payload type 生效）。

3.1 Header 字段

scenario_name（string）：场景显示名。scenario_hash（string）：场景哈希。challenge_start_utc（int，unix_epoch_ms）：挑战开始的 UTC 时间戳。schema_version（int）：schema 版本号。

3.2 ChallengeProfile

time_limit（float，seconds）：挑战时间限制。player_profile（string）：玩家档案名。added_bots（string_list）：添加的机器人列表。player_max_lives（int）：玩家最大生命数。bot_max_lives（int_list）：各机器人最大生命数。player_team（int）：玩家队伍。bot_teams（int_list）：机器人队伍。map_name（string）：地图名。map_scale（float）：地图缩放。timescale（float）：时间缩放。end_challenge_after_kills（float）：击杀结束条件。end_challenge_after_damage（float）：伤害结束条件。

3.3 事件 Payload 类型

每个事件恰好有一个 payload。整数类是 count_increment（累计计数器的增量），浮点数类中 score/damageDone/damagePossible/playerDamageTaken/distanceTraveled/mbsPoints 是 delta（增量），targetSize/targetSpeed/randomSensScale 是 instantaneous（瞬时值）。

shotsFired（int，count）：射击次数增量。与 Stats 的 Shots 对应但按时间分布。

shotsHit（int，count）：命中次数增量。

shotsMissed（int，count）：未命中次数增量。

kills（int，count）：击杀增量。

deaths（int，count）：死亡增量。

overshots（int，count）：过射增量。

playerDamageTaken（float，delta）：承受伤害增量。

reloads（int，count）：装弹次数增量。

pauseCount（int，count）：暂停次数增量。

damageDone（float，delta）：造成伤害增量。

damagePossible（float，delta）：可造成伤害增量。

score（float，delta）：分数增量。

distanceTraveled（float，delta）：移动距离增量。

mbsPoints（float，delta）：MBS 分数增量。

targetSize（float，instantaneous）：当前目标大小。

targetSpeed（float，instantaneous）：当前目标速度。

randomSensScale（float，instantaneous）：随机灵敏度缩放（部分场景才有）。

3.4 事件时间轴的解读

Performance 事件流可以重建整局的时间线。按时间排列后，可以观察命中率随时间的变化、分数增长的节奏、过射是否集中在某些时段。这是 outcome 数据的时间维度，弥补 Stats CSV 只提供整局聚合的局限。

4 Native Flicking 运动学指标

以下指标由 raw mouse input（原始鼠标输入）计算，以左键按下作为分段锚点，把整局切分为多个 flick 事件。所有距离单位是 raw_counts（原始计数），不是像素或度数——不做灵敏度猜测。每个指标提供 session 级分布统计（median、p25、p75、p90、min、max、IQR），使用 Tukey 1.5 IQR 方法标记离群值。

4.1 时间结构指标

movement_duration_ms（ms）：移动时长。从移动开始到移动结束的总时长。越短越好（前提是能控制住），但过短可能说明没用够的减速来收尾。

time_to_peak_ms（ms）：到峰值速度时长。从移动开始到速度达到峰值的时长。

accel_duration_ms（ms）：加速时长。从移动开始到速度峰值的时长。与 time_to_peak_ms 数值相同。

decel_duration_ms（ms）：减速时长。从速度峰值到移动结束的时长。这是诊断重点——好的减速是平滑、单调下降的。

decel_frac（dimensionless）：减速占比。减速时长占整次移动时长的比例。范围 0-1。过高（如超过 0.7）说明大部分时间花在减速上，可能意味着初始移动 overshoot 后大量修正；过低（如低于 0.2）可能说明减速不够充分。

peak_position_pct（percent）：速度峰值位置。峰值出现在整次移动中的百分比位置。健康移动的峰值通常在前 40-55% 区间。峰值太靠前（如 20%）说明加速后需要很长的减速段；太靠后（如 80%）说明前半段犹豫。

settle_duration_ms（ms）：移动结束后时长。从移动结束到分析锚点（左键按下）的时长。反映点击前的稳定等待。过长说明需要很长时间才能确认目标。

4.2 速度与距离指标

peak_speed（raw_counts_per_second）：峰值速度。一次移动中的最大瞬时速度。session 级分布反映玩家的速度习惯。

path_length（raw_counts）：路径长度。一次移动累计经过的总距离（包括所有弯曲和修正）。session 级 key 为 flick_path_length。

displacement（raw_counts）：位移。从移动起点到终点的直线距离。

path_efficiency（dimensionless）：路径效率。位移 / 路径长度。范围 0-1。越高越好——接近 1 说明鼠标走了几乎笔直的路径。低值说明移动中有大量偏离主方向的往返。straightness 是同一个值的产品别名。

mean_speed（raw_counts_per_second）：平均速度。分析窗口内的时间加权平均速度。

mean_acceleration（raw_counts_per_second_squared）：平均加速度。分析窗口内的时间加权平均加速度绝对值。

4.3 修正与方向指标

reverse_ratio（dimensionless）：减速阶段再加速比例。在速度峰值之后的采样中，速度再次增加（而非继续下降）的采样所占比例。范围 0-1。越低越好——高值说明减速过程中反复停停走走，像在不断微调而非流畅收尾。

direction_reverse_ratio（dimensionless）：方向反转比例。原始路径中方向与主方向相反的移动距离占总路径的比例。范围 0-1。越低越好。高值说明鼠标在主方向上反复来回。

corrective_count（count）：修正次数。峰值之后按离散方向变化识别出的修正段数量。整数，通常 0-3。0 是理想值——说明一次流畅移动到位。每多一次修正都意味着初始移动没到位，需要额外子动作补偿。

submovement_count（count）：子动作数量。corrective_count + 1（当有移动时）。反映一次点击被分解成了几个子动作。健康 flick 通常是 1（一次到位）。

4.4 平滑度指标

sparc（dimensionless）：运动平滑度（SPARC）。通过对速度轨迹做 FFT 频谱分析，计算归一化频率-振幅曲线的弧长再取负。值越接近 0 越平滑（负值，越大的负数越不平滑）。这是核心公平指标之一——平滑的移动意味着高效的运动控制。需要至少 8 个重采样点才能计算。注意跨轮次可比性未经验证，只能在同一 session 内比较。

trough_depth_ratio（dimensionless）：速度谷深度比例。速度峰值之后最低速度谷值 / 峰值速度。范围 0-1。越低说明子动作之间的分离越明显（速度显著下降后再加速），值高说明子动作之间有重叠。需要至少 3 个速度采样点。

submovement_overlap（dimensionless）：子动作重叠代理值。当前使用 trough_depth_ratio 作为代理，不是真正的时间重叠分解。

4.5 Session 级汇总指标

flick_count（count）：纳入汇总的 Flick 事件数量。太少的 flick 数会影响分布统计的可靠性。

所有上述指标在 session 级都有分布统计（mean、std、min、p25、median、p75、p90、max、IQR），用 Tukey 1.5 IQR 方法标记离群 flick。outlier_refs 指向具体的 flick 事件 ID。

4.6 重要限制

所有运动学指标基于 raw input，没有目标位置信息。只能说"移动收尾""反向修正"等已测现象，不能说准星已经对上目标、冲过目标或已经命中。target_relative_facts_unavailable 是核心限制。

5 跟踪与动态点击指标（简要）

5.1 连续跟踪（continuous_tracking）

target_relative_error_px（px）：目标相对误差。鼠标位置与目标中心之间的距离。越低越好。这是跟踪质量的核心指标。

time_in_radius_ratio（ratio）：目标范围内时间占比。鼠标位于目标半径内的时间比例。范围 0-1。越高越好。

loss_count（count）：离开目标次数。鼠标离开目标半径的次数。越低越好。

loss_duration_ms（ms）：离开目标时长。每次离开的平均持续时长。

reacquisition_latency_ms（ms）：重新捕获耗时。离开后重新进入目标半径所用的时长。

relative_lag_ms / phase_lag_ms（ms）：滞后时间。鼠标运动相对目标运动的时间偏移。

coherence（dimensionless）：跟踪相干性。鼠标与目标运动在频域中的相干程度。

velocity_gain（dimensionless）：速度增益。鼠标速度 / 目标速度。接近 1 说明速度匹配好。

observed_change_response_ms（ms）：观测到的变向响应时长。目标变向后到鼠标响应之间的时长。不代表人的反应时间。

sparc（dimensionless）：跟踪平滑度。与 flicking 的 SPARC 同理。

correction_direction_reversal_count（count）：修正方向反转次数。跟踪过程中修正方向反转的次数。

smoothness_acceleration_rms：加速度均方根。跟踪过程中加速度的均方根。

5.2 目标切换（target_switching）

transition_time_ms（ms）：切换移动时长。从上一目标击杀到捕获下一目标的时长。

path_efficiency（dimensionless）：切换路径效率。切换期间的路径效率。

settle_duration_ms（ms）：捕获确认时长。从首次接触到满足连续接触条件的时长。

first_shot_latency_ms（ms）：首次射击时长。从捕获到首次射击的时长。

carry_over_overshoot_ratio（ratio）：携带过冲比例。被标记为携带过冲的切换链所占比例。

5.3 动态点击（dynamic_clicking）

normalized_click_error（dimensionless）：归一化点击误差。点击位置与目标中心的距离，按目标半径归一化。越低越好。

acquisition_time_ms（ms）：捕获时长。从捕获开始到准星进入目标半径的时长。

relative_velocity（dimensionless）：相对速度。鼠标与目标间相对速度向量大小。

target_state_accuracy（ratio）：关联目标成功比例。已关联且有结果的点击中成功的比例。

6 分析角度

6.1 TTK 分布与离群值

击杀表中 TTK 的分布形态比均值更有信息量。中位数反映典型效率，p90 和 max 指向最差的几次击杀。离群高 TTK 的击杀是重点分析对象——查看它们的 Accuracy、Shots 和 OverShots，判断是命中率问题（打不中）还是确认问题（打中了但没收手）。

TTK 随时间（按 Kill # 或 time_s 排序）的退化趋势如果存在，可能指向后半局注意力或稳定性下降。

6.2 命中率与效率

Accuracy 和 Efficiency 要一起看。高 Accuracy 但低 Efficiency 说明每一发都打中了但位置不够好（伤害低）。低 Accuracy 但高 Efficiency 说明打中了但浪费了很多弹。OverShots 过高说明击杀确认不够果断。

命中率随时间的退化可以通过 Performance 事件流重建——按时间窗口聚合 shotsHit / shotsFired，观察是否有下降趋势。

6.3 运动平滑度

SPARC 是核心公平指标。低 SPARC（大负值）说明速度曲线有很多高频抖动，运动控制不够流畅。结合 submovement_count 和 corrective_count：高修正次数加低平滑度通常指向初始移动控制不准、需要大量补偿的模式。

reverse_ratio 高说明减速段反复停停走走，不是流畅的减速曲线。decel_frac 过高说明移动的主要时间花在减速修正上而非快速接近。

6.4 路径效率与过度修正

path_efficiency 低说明鼠标走了远比直线更长的路径。结合 direction_reverse_ratio 可以区分是弧线路径（效率低但方向一致）还是反复来回（方向反转多）。

corrective_count 的分布比均值更重要——如果大多数 flick 的 corrective_count 是 0，但 p90 和 max 很高，说明是少数 flick 拖低了整体。这些离群 flick 的 evidence segment 值得重点查看。

6.5 设置问题

cm/360 过大或过小可能不匹配当前场景需求。Sens Scale 为空或未知游戏时 cm/360 无法计算。

FOV 与目标表观大小相关。Avg FPS 低于显示器刷新率会增加输入延迟，影响表现。

Crosshair 设置不当（颜色对比度低、大小不合适）可能影响瞄准确认。

6.6 时间序列分析

Performance 事件流和 Stats 击杀表的时间序列可以揭示整局的节奏。分数增长曲线的斜率变化、命中率窗口间的波动、过射是否集中在特定时段——这些都是 static 聚合看不到的维度。

7 字段语义注意事项

Damage Done 和 Damage Possible 的单位是 source_damage_unit，跨游戏不可比。不同武器的 Damage Possible 不同，Efficiency 跨武器比较时需注意。

Scenario 和 Bot 是 untrusted_text，可能包含任意内容，不用于诊断。

Sens Scale 为 "cm/360" 时，Horiz Sens 直接是 cm/360 物理值。其他已知游戏用公式 cm/360 = 914.4 / (DPI x Horiz_Sens x yaw) 计算。未知游戏或缺失 DPI/Horiz Sens 时 cm/360 返回 None。

Performance 事件的 payload 是 oneof——每个事件只有一个 payload 生效。整数 payload 是累计计数器的增量（不是瞬时值），浮点 payload 中 score/damageDone 等也是增量。

Raw input 的 dx/dy 是原始鼠标计数，不是像素、度或物理距离。除非有显式校准（raw_counts_per_unit），路径长度只在 raw_counts 空间有意义。
