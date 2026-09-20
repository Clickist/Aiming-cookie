# Aiming Cookie 数据说明（知识库作者版）

> 版本：2026-09-20 · 读者：知识库包作者（教练 / UP 主 / 高手）
> 作用：解释你的包所消费的每一类数据"从哪来、怎么采集、反映什么、分析管线怎么用它"。理解了数据，你才知道自己写的每条规则、每段解释站不站得住。
> **数据边界声明（红线）**：本文只描述**本机数据**——全部采集、解析、分析都发生在用户自己的电脑上。本文不包含、产品也不存在任何出网/上传数据链路；Coach 的 LLM 请求只携带第 8 节描述的投影层摘要，不携带任何原始数据本体。
> 事实核准基准：`kovaak_tracker/` 分析管线代码、`docs/PRD.md` §5.7、`docs/ARCHITECTURE.md`。

---

## 0. 一次练习的数据旅程（总览）

玩家在 KovaaK's 里跑完一局，Aiming Cookie 按下面顺序把数据变成知识：

```text
游戏运行中（KovaaK 进程 gate 内）          游戏结束后
├─ Raw Input 鼠标输入流（1）              ├─ Stats CSV 到达（2）→ 场景身份 + outcome
├─ MP4 回放缓冲滚动记录（4）              ├─ Performance .perf 到达（3）→ 事件流 + UTC 锚
                                          ├─ 时间对齐：三者锚定到同一个"挑战窗口"
                                          ├─ 视觉预处理（5，可选）：MP4 → 目标/准星数值证据
                                          ├─ 外部遥测导入（6，可选）：目标轨迹侧补充证据
                                          ▼
                              派生指标计算（7）：输入运动学 / 家族指标
                                          ▼
                              分析投影 L1-L3（8）：overview / metrics / events / evidence
                                          ▼
                              信号与观察（9）：mapping 规则匹配 + registry 知识标注
                                          ▼
                              Coach 讲解 / 诊断 / 训练推荐
```

括号编号对应下文九类数据。

---

## 1. Raw Input trace（鼠标原始输入流）

- **来源**：Windows Raw Input API，由桌面端 native 层采集。
- **怎么采集**：仅当 KovaaK 处于前台运行（进程 gate）且用户明确开启该功能后采集；只记录相对位移 `dx/dy`、时间戳 `timestamp_ms` 和鼠标按钮 `buttons`——**不采集键盘、不采集桌面绝对坐标**。Raw Input 默认关闭，首次开启必须明确告知用户采集范围、用途和关闭方式。
- **归一化口径（ACRI v2，2026-08-04 冻结）**：无论鼠标硬件以何种 polling rate 上报，canonical 运动时间粒度固定为 1 ms、最高 1000 Hz——同一毫秒内的 `dx/dy` 分别求和为至多一条运动记录，不生成补零记录；鼠标按钮按下/抬起边沿不受该上限约束并保持顺序。该归一化保留每毫秒 X/Y 净位移，**有意不保留亚毫秒路径形状**；产品不把它描述为硬件 polling rate 测量或亚毫秒运动证据。
- **反映什么**：玩家手部运动的唯一事实源——输入运动学。速度、加速度、减速占比（decel_frac）、平滑度（SPARC）、路径效率等全部输入侧指标的原始分母。
- **管线怎么用**：分析时按半开区间 `[start_ms, end_ms)` 切出挑战窗口（`kovaak_tracker/time_alignment.py`），窗口内逐条回放轨迹点；各分析器（如 `kovaak_tracker/native_flicking_analysis.py`）在轨迹上切分单次 flick 并计算运动学指标。没有 Raw Input 的 Run 走降级路径（见第 10 节证据档位），不能产出输入侧结论。

## 2. KovaaK Stats CSV（结果统计）

- **来源**：KovaaK's 游戏自己导出的 stats 文件；产品自动发现，也支持手动导入。
- **怎么采集**：文件监听；KovaaK 写出后由产品解析（`kovaak_tracker/csv_parser.py`）。
- **内容结构**：单个 CSV，含四个区块——
  1. **击杀表**：每行一次击杀，固定 13 列（Kill #、Timestamp、Bot、Weapon、TTK、Shots、Hits、Accuracy、Damage Done/Possible、Efficiency、OverShots、Cheated）；
  2. **武器汇总行**：按武器的全程射击/命中/伤害聚合；
  3. **总结块**：`Key:Value` 整局聚合（Kills、Deaths、Avg TTK、Total Overshots 等），其中 **Challenge Start 是壁钟锚点**——把击杀表里的 `HH:MM:SS.mmm` 时间戳换算成场景内相对秒数全靠它；
  4. **输入配置块**：FOV、DPI、Sens、分辨率等。
- **反映什么**：outcome 层事实——每一击的结果（打没打死、用了几秒、命中率、过射），以及场景身份与玩家设置。
- **管线怎么用**：击杀表提供 TTK 分布、命中率、过射等 outcome 指标与时间序列；总结块的 Challenge Start 参与时间对齐；输入配置进入设置上下文（如 `settings` 通道的 `cm_per_360`）。Bot/Weapon 名称属不可信文本，只做分组展示，不用于诊断。

## 3. KovaaK Performance（.perf 事件流）

- **来源**：KovaaK's 导出的 `.perf` 性能文件；与 Stats 一样自动发现。
- **怎么采集**：文件监听；产品内自带的解析器（`kovaak_tracker/performance_parser.py`）直接读取二进制 protobuf（字段映射改编自 RefleK 的 GPL-3.0 实现），未知字段跳过以保证前向兼容。
- **反映什么**：事件层事实——按时间顺序的射击 / 命中 / 击杀 / 分数增量流，以及场景名、场景 hash 和 **`challenge_start_utc`**（canonical 时间窗的 UTC 锚）。
- **管线怎么用**：与 Stats 的 Challenge Start 互为校验，共同把 Raw Input、视频、事件对齐到同一个挑战窗口；事件流是"哪一枪失误、什么时候目标变化"这类逐事件证据的来源，也是家族分析（动态点击 / 追踪 / 转火）逐行证据表的骨架。

## 4. Run-owned MP4（受管回放视频）

- **来源**：Windows Graphics Capture，只捕获 KovaaK 窗口。
- **怎么采集**：进程 gate 内持续录制（GPU 硬件编码路径，带三级降级合同），维护**最近 300 秒的有界缓冲**——不是无限录屏；Stats / Performance 到达后按时间对齐结果**事后切窗**，只保留挑战窗口对应的片段。仅 `Pause Count = 0` 的局才生成永久 MP4（暂停过的局时间线不可信，不进入证据链）。
- **反映什么**：屏幕上实际发生了什么——直观回放、问题定位的视觉证据。
- **管线怎么用**：两个用途。其一，用户可点击回放、定位某次失误；其二，作为第 5 类本地视觉预处理的输入。视频是**辅助**证据：基础运动学以 Raw Input 为事实源，视频不参与输入运动学计算。

## 5. 本地确定性视觉信号（CV 预处理）

- **来源**：Run-owned MP4，经本地确定性算法预处理。
- **怎么采集**：本地 CV 管线（`kovaak_tracker/vision.py`、`kovaak_tracker/visual_signals.py`）——**无模型、确定性算法**：目标检测、准星检测、目标-准星相对误差、事件数值化。每一步带质量 Gate：低置信度、遮挡、帧缺失等情形会显式标注（对应词汇表里的 `low_confidence_or_occluded`、`target_occlusion`、`target_identity_unresolved` 等 limitation tokens），而不是硬算一个数。
- **反映什么**：目标相对几何（误差多少像素）、命中关联、目标身份与速度——这些是输入数据拿不到的"屏幕上"事实。
- **管线怎么用**：只在**精确审核过的场景**（场景 registry 的 exact reviewed hash 条目）上全量开放；其余场景保留降级档并挂质量 limitations。作者须知：词汇表 `limitation_tokens` 里大量视觉类 token（如 `click_geometry_visible_radius_conditioned`）就是这条管线的诚实标注——家族规则的 `blocking_limitations` 常用它们做"证据质量不够就别下结论"的闸门。

## 6. ExternalTelemetryRun（外部遥测导入，可选通道）

- **来源**：用户自行运行的外部 RPM（ReadProcessMemory）采样管线产出的数据。
- **怎么采集**：用户主动配置一个本地 watch 根目录后，产品**只读导入**清洗产物（`cleaned/round_NN.jsonl`）；产品做轮次切分与轨道重建，导入产物是 `external_run.v1` 格式（合同见 `docs/EXTERNAL_TELEMETRY_IMPORT.md`，清洗器在 `telemetry_capture/cleaner.py`）。该通道默认未配置，不影响主链路。
- **反映什么**：目标轨迹侧的补充证据——T2K（target-to-crosshair）分布、目标生成/死亡/超时（spawns/deaths/timeouts）等。
- **管线怎么用**：为追踪/转火类场景补充"目标在动什么"的测量；其场景标签仅是 proposal（建议），不直接进入已审核场景 registry。没有这个通道，相关结论按不可用处理并标注。

## 7. 派生指标（分析器输出词汇）

- **来源**：上述 1-6 类原始数据，经各家族分析器（`static_clicking` / `continuous_tracking` 家族 / `target_switching` / `dynamic_clicking`）的确定性 Python 管线计算。
- **怎么采集**：不是独立采集，而是计算产物。每个指标携带 `metric_version`（算法版本）、provenance（来源链）、`availability`（是否可用）、`limitations`（局限标注）。
- **反映什么**：中间层测量事实。主要家族词汇：
  - `static_clicking.*`：decel_frac、SPARC、path_efficiency、reverse_ratio、peak_position_pct、submovement_overlap 等（flick 运动学）；
  - `continuous_tracking.*`：phase_lag_ms、loss_count、reacquisition_latency_ms、sparc、time_in_radius_ratio 等（追踪）；
  - `target_switching.*`：transition_time_ms、settle_duration_ms、terminal_correction_ratio 等（转火）；
  - `dynamic_clicking.*`：normalized_click_error、acquisition_time_ms、relative_velocity 等（动态点击）。
  完整清单即官方词汇表的 `metric_keys`（`knowledge/mapping/vocabulary.v1.json`）。
- **管线怎么用**：这层就是你包的**输入**。静态规则的 `summary` 通道形如 `{指标名: {med: 中位数, metric_version: 版本}}`；家族规则对比的 `metrics` / `baseline_metrics` 也来自这层。指标的中文显示名与中性描述由 `kovaak_tracker/metric_definitions.py` 提供——它是**纯显示字典**，不含好坏方向，好坏方向的声明永远属于你的知识条目（带 claim_level）。

## 8. L1-L3 投影（Coach 可见层）

- **来源**：上述全部数据的分析产物。
- **怎么采集**：分析完成后，产品把结果写成版本化、字段白名单、预算受限的投影文件（`webapp/backend/analysis_output.py`）：`analyses/<id>/` 下的 `overview.json`（诊断总览，含信号、知识引用）、`metrics.json`（指标汇总）、`events.json`（事件）、`evidence.json`（证据定位）。
- **反映什么**：分析的**结论与证据索引**，而不是原始数据本体。
- **管线怎么用**：Coach（LLM）与前端只消费这层投影。**L0（Raw trace 原文、原始 CSV/protobuf、MP4 字节、文件路径）永不进入 Provider 请求**。作者视角的含义：你的 mapping 规则匹配到的信号、你的 registry 条目被引用的方式，都发生在投影层；包内容进入 Coach 上下文的也只有条目正文与索引，不是任何原始数据。

## 9. 信号与观察词汇表（产品的稳定合同）

- **来源**：产品定义，随代码冻结导出（`knowledge/mapping/vocabulary.v1.json`）。
- **内容**：`signals`（"decel_frac high"、"sparc low" 等现象名）、`metric_keys`（指标键）、`observation_refs`（"metric.terminal_control"、"event.switch_chain" 等观察对象）——这三个集合对作者**只加不改不删**（硬合同）；`limitation_tokens`、`row_fields` 等（产品拥有，改名算 breaking）。
- **反映什么**：测量侧与知识侧的**匹配语言**。mapping 规则发出的 signal、registry 条目声明的 signals/metric_refs、诊断标注写的 observation_ref，全靠同一套词汇才能对上。
- **管线怎么用**：导入校验强制你的包落在这套词汇内（SPEC 第 5 节规则 4）；运行时按 signal/metric/observation 把"测量到的现象"与"知识条目的解释"接起来。你写包之前先通读一遍词汇表，基本就知道产品"看得见"什么。

---

## 10. 证据档位语义（每档能/不能声明什么）

每次分析按**最高可用**的证据档位自动选择路径，用户不手动选择；档位决定该次分析**有权声明什么结论**：

| 档位 | 数据构成 | 能声明 | 不能声明 |
|---|---|---|---|
| `multimodal` | Stats + Performance + Raw Input + managed MP4 + canonical window | 输入运动学结论 + 视觉证据 + 完整 Coach 消费 | ——（最高档） |
| `input_native` | Stats + Performance + Raw Input + canonical window（无视频） | 全部输入侧结论 | 任何视觉证据：不得声称目标相对误差、视觉反应时刻、视频证据 |
| `video_fallback` | Stats + managed MP4（无 Raw Input；含导入的历史 KovaaK 数据） | outcome 层观察、直观回放 | 任何输入运动学结论 |

底线口径：**低层路径只能声明其来源实际支撑的结论**；任一档都不允许伪造目标相对误差、视觉反应时刻或视频证据。这就是词汇表 `limitation_tokens` 存在的原因——每次降级、每个质量缺口都有显式标注，而你的家族规则可以用 `blocking_limitations` 引用这些标注，把"证据不足"变成"这条规则不触发"。

## 11. "阈值未经产品校准"声明（作者必读）

产品对静态点击阈值族（THRESHOLDS，如 `decel_frac > 0.65`、`sparc < -5.0`、`cm_per_360 < 25`）的诚实口径是：**这些数字来自初始研究草稿的启发式，不是经过产品数据校准的健康区间**（`kovaak_tracker/advice.py` 的 THRESHOLDS 注释与 `_finalize_uncalibrated_findings`）。

对作者的三点实际含义：

1. **不要假设绝对健康区间**。官方规则全部带 `threshold_requires_product_calibration` limitation；任何"低于 X 就是不达标"的绝对化表述都不成立。阈值应当被表述为"触发一次受控实验的假设"。
2. **当前运行时会统一收尾静态信号**。静态规则的求值结果无论声明什么 severity / claim_level，当前都会被统一改为 `severity=info`、`claim_level=experimental`、`limitations=[threshold_requires_product_calibration]`（SPEC 6.7 第 3 条）。你的阈值声明要如实写并保留 limitation——它们在校准放开后才会生效。
3. **官方规则与你的包走同一引擎、同一收尾**。参照官方包 `knowledge/mapping/official.v1.json` 的写法：诊断句里写清"该阈值仍需真实产品数据校准"，把绝对判定留给复测。
