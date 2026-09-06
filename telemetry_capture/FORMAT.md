# KovaaK's 外部遥测数据格式规范 v2 —— 全通道统一

> 面向下游项目（Aiming-cookie 等）的正式数据格式说明。
> 数据来源：外部内存读取（`ReadProcessMemory`，零注入零写入）+ OS 层 Raw Input 采集的 KovaaK's
> 训练遥测。坐标为 **UE4 世界坐标（cm）**，右手系，z 向上；旋转为 **角度（°）**，FRotator
> 顺序 Pitch/Yaw/Roll。
>
> **v1 → v2 变更**：v1 只覆盖目标通道 + 清洗层；v2 扩展为四通道统一规范（目标 / 相机 / OS 输入 /
> 武器字段观察，另附规划中的 shot 事件通道），新增**双时钟对齐章节（实证）**、**跨通道不一致清单**。
> 清洗层规则与 v1 兼容（cleaner.py 未变）。
>
> **证据标注约定**：`[confirmed]` = 录制器代码 / 反编译 / 数据实测可复核；
> `[inferred]` = 由证据合理推断；**[待验证]** = 尚无运行时数据支撑。
> 各断言尽量给出出处（文件名 + 字段 / 行为依据）。

---

## 0. 通道总览

| # | 通道 | 录制器 | 输出文件模式 | 记录 `ev` | 名义频率 | 时间戳域 | 文件内绝对锚 | 数据现状 |
|---|---|---|---|---|---|---|---|---|
| A | 目标位姿 | `target_poll2.py` | `target_poll_out_<MMDD_HHMMSS>.jsonl` | `frame` | 50 Hz（实际 ≈32 Hz） | 会话相对（epoch 差） | **无** | 实数据（5 个采集文件 + 3 个 validation 副本） |
| B | 相机 POV | `camera_probe.py` | `camera_probe_out_<MMDD_HHMMSS>.jsonl` | `clock_map` / `cam` / `null` | 50 Hz | 相对 + epoch 锚 | 首行 `clock_map`（epoch） | 1 文件，**全部 null 帧见 §2.4** |
| C | OS 原始输入 | `input_logger.py` | 用户指定路径（默认 `input_log.jsonl`，**追加**） | `clock_map` / `m` | 鼠标上报率（125/500/1000 Hz） | perf_counter（QPC） | 首行 `clock_map`（perf+epoch） | 实数据（396,194 事件 / 2129 s） |
| D | 武器字段观察 | `weapon_field_watch.py` | `weapon_watch_log.jsonl`（固定名，**追加**） | **无 ev 字段** | 20 Hz（50 ms 轮询，变化才写行） | 会话相对（epoch 差） | **无** | 实数据（289 行 / 2 会话） |
| E | 射击事件（规划） | `shot_probe.py` | `shot_probe_out_<MMDD_HHMMSS>.jsonl` | `state` / `shot` | ≥100 Hz | 会话相对（epoch 差） | **无** | **尚无实数据，schema [待验证]** |

公共约定：

- 全部为 JSONL（UTF-8，每行一个 JSON 对象）；录制器以追加模式写文件并周期 flush。
- `ev` 字段区分行类型；**通道 D 没有该字段**（不一致清单 §7-3）。
- 时间戳的三种时钟域与换算关系见 §5（对齐前必读）。
- 录制器对游戏只读（RPM），输入记录器与游戏进程零接触 [confirmed：各脚本]。

---

## 1. 通道 A：目标位姿（target_poll2.py）

### 1.1 文件命名

`target_poll_out_<MMDD_HHMMSS>.jsonl`，时间戳为 `run()` 开始时的本地时间
`time.strftime("%m%d_%H%M%S")` [confirmed：target_poll2.py run()]。每次附着（含游戏重启后
自动重附着）生成**新文件**；同文件内 `t` 严格单调（实测 62800 帧无回退）。
旧版 `target_poll.py`（v2 之前）写固定名 `target_poll_out.jsonl`（遗留文件仍在）。

### 1.2 记录 schema

每行一个 JSON 对象：

```json
{"ev": "frame", "t": 22.336, "targets": [[2061603336336, 1953.7, 1313.2, 850.2], ...]}
```

| 字段 | 类型 | 单位/域 | 语义与来源 |
|---|---|---|---|
| `ev` | string | — | 恒 `"frame"` |
| `t` | float | 秒，会话相对（`time.time() − t0`，4 位小数 ≈0.1 ms 分辨率） | 帧时间戳。**epoch 域取差**，无文件内锚（§5） [confirmed：run() `t0=time.time()`] |
| `targets` | list | — | 本帧目标列表，可为空 |
| targets[i][0] `addr` | int | — | 目标 actor 的 UObject 指针（十进制）。**不是稳定身份**：actor 池化复用（§6.5、§1.3） |
| targets[i][1..3] `x,y,z` | float | cm | `actor → RootComponent(+0x130) → ComponentToWorld(+0x1c0)` 的平移分量（FTransform 四元数 @+0、平移 @+0x10，读 float32×3） [confirmed：本构建两轮真值轨迹验证的常量，target_poll2.py] |

字段来源内存链 [confirmed：target_poll2.py / tp1.py]：

- 对象数组：`GUObjectArray` RVA `0x53B4508`（+ 模块基址），分 chunk，item stride `0x18`，
  槽 serial 在 item+0x10；`UObject.ClassPrivate`=+0x10，`OwnerPrivate`=+0x20。
- 目标发现：扫描 `TargetHudComponent` 类实例 → `OwnerPrivate` 即目标 actor；运行中每 0.5 s 做
  全数组 chunk 差分发现新目标 / 槽位复用摘除（serial 变化 = 原对象销毁）。
- 类名解析经 FNamePool（`names.py`）。

### 1.3 采样率与已知坑

1. **实际 ≈32 Hz，不是 50 Hz**：读内存耗时挤占 sleep；帧间 dt 中位 31.0 ms、p95 32.0 ms、
   实测最长单帧停顿 1.05 s（会话首帧的全数组发现扫描）。逐目标有效采样率 ≈31 Hz。
   **下游不要假设固定 dt**，一切速度/加速度用相邻帧 `t` 差 [confirmed：62800 帧实测]。
2. **addr ≠ 目标身份**：池化复用使同一 addr 序列含多个逻辑目标；跨帧身份用清洗产物 `tid`（§6.4）。
3. **无文件内绝对时间锚**：`t` 相对会话起点，跨文件对齐需 §5.3 的配准流程。
   *建议*：录制器未来版本应在首行写 `clock_map`（§7 不一致清单；本次不改录制器）。
4. **死亡残留**：目标死亡后 RootComponent 短暂读回精确 `(0,0,0)`（容差 1.0），随后该 addr 从帧里
   消失或被复用 [confirmed：FORMAT v1 验证 + 本批 030352 数据 3 个 addr 各 7800 个原点帧]。
5. **NaN/野值**：指针指向已释放内存时读回 NaN 或出界值（实测一例 `z=-48039`）[confirmed]。
6. **幽灵轨道**：TargetHudComponent owner 扫描会误捞 CDO/预览体：首帧即出现、贯穿全程
   （presence≈100%）、速度 0~2.8 u/s、位置 `(0,0,0)` 或固定点 [confirmed]。cleaner 剔除（§6.6 规则 7）。
7. **多局混采**：一个文件可覆盖多局（本批 030352 文件含 9 轮，92~1872 s）；轮间全灭空窗
   112 s 实测。轮次切分由 cleaner 完成（§6.6 规则 8）。
8. **重启自动附着**：主循环自带"游戏退出 → 每 10 s 找新 pid → 重校准 → 新文件续采"；
   `--wait` 只把首次校准重试延长到 40×15 s [confirmed：main()]。

---

## 2. 通道 B：相机 POV（camera_probe.py）

### 2.1 文件命名

`camera_probe_out_<MMDD_HHMMSS>.jsonl`（`--run` 启动时本地时间）。每次 `--run` 新文件。

### 2.2 记录 schema

首行 `clock_map`（**唯一一条**，run 开始时写入）：

```json
{"ev": "clock_map", "t": 1788031946.5150318, "povs": ["0x2804f353980"],
 "offsets": {"cache": 3740, "loc": 0, "rot": 12, "fov": 24}}
```

| 字段 | 类型 | 单位/域 | 语义 |
|---|---|---|---|
| `t` | float | **Unix epoch 绝对秒** | 采样起点锚（注意：与帧 `t` 的"相对秒"**语义不同**，§7-1） |
| `povs` | list[string] | — | 各 PCM 实例的 POV 绝对地址（hex） |
| `offsets.cache` | int | — | `CameraCachePrivate` 在 PCM 内的偏移（反射失败时用实测常量 0xe9c=3740）[confirmed：--scan 差分实测] |
| `offsets.loc/rot/fov` | int | — | FMinimalViewInfo 内 Location/Rotation/FOV 偏移（反射或 4.26 标准回退 0x0/0xC/0x18） |

数据帧（每 tick 一行）：

```json
{"ev": "cam", "t": 12.3457, "i": 0, "pos": [x, y, z], "rot": [pitch, yaw, roll], "fov": 103.0}
```

或读取失败时整行 `null`（json 的 null 字面量）。

| 字段 | 类型 | 单位/域 | 语义与来源 |
|---|---|---|---|
| `t` | float | 秒，**会话相对**（`time.time() − t0`，4 位小数） | epoch 值 ≈ `clock_map.t + t`（<1 ms 偏差，因 clock_map 先于 `t0` 写入）[confirmed：run() 语句顺序] |
| `i` | int | — | PCM 实例序号（单机通常 0；多实例取第一个有效） |
| `pos` | float×3 | cm | `FCameraCacheEntry.POV.Location`。POV 地址 = `PCM + CameraCachePrivate + 4`（+0 是 TimeStamp，未读取）[confirmed：FCameraCacheEntry 布局 {float TimeStamp; FMinimalViewInfo POV}] |
| `rot` | float×3 | 度 | `POV.Rotation`（Pitch/Yaw/Roll，FRotator 度） |
| `fov` | float | 度 | `POV.FOV`；合法性过滤 1<fov<179 [confirmed：read_pov()] |
| `null` 行 | — | — | 本 tick 无有效 POV（读取失败 **或实例类校验失败**，见 §2.4-1）。**null 行没有任何字段，包括没有 `t`**——时间只能按行号 × 名义 dt（1/50 s）推算 [confirmed：run() 写 `json.dumps(None)`] |

### 2.3 来源内存链（自校准）

1. 枚举 GUObjectArray 按类名找 `PlayerCameraManager` 的 UClass（exec thunk 全走虚表、UPROPERTY
   注册结构无 Offset 字段 ⇒ 静态拿不到偏移，必须运行时反射）[confirmed：event_layer_notes §1.1]。
2. **FField.Offset_Internal 位置**：沿 PCM UClass 的 SuperStruct(+0x40) 链找到 `Actor` 类，在候选
   {0x40, 0x44, 0x3C, 0x2C} 中以 `RootComponent == 0x130` 锚定（0x130 = 通道 A 验证过的本构建常量）。
   4.26 预期 0x40 [confirmed 锚点；event_layer_notes §1.2]。
3. 从 PCM 属性链读 `CameraCachePrivate` 偏移；失败回退实测常量 0xe9c。
   FMinimalViewInfo 内 Location/Rotation/FOV 用 `MinimalViewInfo` UScriptStruct 反射，失败回退
   4.26 标准布局 0x0/0xC/0x18 [confirmed 代码；回退值 [inferred→标准布局]]。
4. `--scan`：对 PCM 内存 0.5 s 差分找"旋转三连变化 + fov 合理"的 POV 段，人工复核用。

### 2.4 已知坑

1. **相机实例发现必须含 BP 子类（SuperStruct+0x40 链）——而运行帧校验没有**。发现逻辑（`calibrate`/`scan`）
   把 SuperStruct 链 ≤8 跳可达原生 PCM 的类（如 `BP_PlayerCameraManager_C`）都算作 PCM 类
   [confirmed：camera_probe.py 注释 "含 BP 子类——SuperStruct(+0x40) 链可达即算"]；但 `run()` 的
   `check_serial()` 只比较 `ClassPrivate == 原生 PCM UClass`。**当实际实例是 BP 子类时，发现成功、
   逐帧校验全失败 → 全文件 null**。实证：`camera_probe_out_0830_033226.jsonl` = 1 clock_map +
   **7900 个 null + 0 个 cam 帧**（158 s @50 Hz），与该机理一致 [confirmed 数据；因果 [inferred，
   另一可能是采样期间游戏不在场景/已退出——判别方法：null 期间 `check_serial` 的返回值，录制器
   未打印，[待验证]]。*修复方向（未实施）*：check_serial 应比对"pcm_set（含 BP 子类）"。
2. **null 帧无时间戳**（§2.2）：null 占比高的文件时间轴只能靠行号重建，且行号节奏在读写失败时
   并不严格 50 Hz **[待验证]**。
3. **clock_map 只有首行一条**，之后不再刷新；epoch 锚精度受 `t0` 捕获顺序影响 <1 ms [confirmed 代码]。
4. 坐标系注意：`pos` 是相机（视点）世界坐标，与通道 A 的目标坐标同域（UE4 世界 cm），可直接做
   视点-目标几何 [confirmed 同源读取]；`rot` 是引擎 FRotator（度），**不是**瞄准射线方向本身
   （需要按 UE 旋转约定自行构造）。
5. `--wait`（40×15 s）只覆盖**首次**校准等待；采样中断后**没有**重附着循环（游戏退出后持续写
   null，不会自动恢复）[confirmed：main() 无外层等待循环]。与通道 A 行为不同（§7-8）。

---

## 3. 通道 C：OS 原始输入（input_logger.py）

与游戏进程**零接触**。Windows Raw Input（WM_INPUT，`RIDEV_INPUTSINK`）——游戏前台/全屏均收，
且与游戏自身的 Raw Input 注册互不干扰（多窗口可同时收同一设备流）[confirmed：Win32 语义]。

### 3.1 文件命名

路径由命令行给定（默认 `input_log.jsonl`），**追加模式**。多次启动混写同一文件时，clock_map 会
成对出现（每个会话一条首 map）——当前 `input_log.jsonl` 为单会话 + 前缀拷贝（§7-8）。
`--test` 只打印统计不写文件；`--poll` 为降级方案（语义不同，见 §3.4-5）。

### 3.2 记录 schema

`clock_map` 行（代码设定首行 + 每 10 s 一条；**实测节奏见 §3.4-3**）：

```json
{"ev": "clock_map", "qpc": 3834480981, "t": 383.4480981, "t_unix": 1788030090.6340141}
```

| 字段 | 类型 | 单位/域 | 语义 |
|---|---|---|---|
| `qpc` | int | QPC 计数（100 ns tick） | `QueryPerformanceCounter` 原始值 |
| `t` | float | 秒，perf_counter 域（**开机相对，非 epoch**） | ≡ `qpc / 1e7`（QPF=10 MHz，实测恒等，误差 <5e-8 s）[confirmed：396,194 行全量抽查] |
| `t_unix` | float | Unix epoch 秒 | `t0_unix + (perf − t0_perf)`——**由启动时一对锚换算，非逐行采样 time.time()** [confirmed：Logger.map_line()；含义见 §3.4-4] |

鼠标事件行（每个 WM_INPUT 包一行，事件率 = 鼠标上报率）：

```json
{"ev": "m", "t": 383.460154, "qpc": 3834601540, "dx": 1, "dy": -1, "btn": ["L_down"]}
```

| 字段 | 类型 | 单位/域 | 语义与来源 |
|---|---|---|---|
| `t` | float | 秒，perf_counter 域 | 事件入队（消息分发）时刻，≡ qpc/1e7 |
| `qpc` | int | 100 ns tick | 同一刻的原始计数 |
| `dx`,`dy` | int | **设备原始计数**（非 cm、非度、非像素） | `RAWMOUSE.lLastX/lLastY`（x64 缓冲偏移 +36/+40），相对增量。数值含义取决于鼠标 DPI/回报率；游戏再按灵敏度换算 [inferred：Raw Input 标准语义] |
| `btn` | list[string] | — | 按钮沿，来自 `usButtonFlags`（+28）：`L_down/L_up/R_down/R_up/M_down/M_up`；无按钮为 `[]` |

x64 RAWINPUT 缓冲布局 [confirmed：input_logger.py 常量]：header 24 B；`usFlags`@24
（`&1`=MOUSE_MOVE_ABSOLUTE——绝对模式设备被跳过，罕见）、`usButtonFlags`@28、`lLastX`@36、`lLastY`@40。
`dx=dy=0` 且无按钮的包不写行 [confirmed：emit() 条件]。

### 3.3 实测概况（input_log.jsonl，2026-08-30 会话）

396,194 事件 / 2129.4 s；`t` ∈ [383.460, 2512.818]，严格单调；dt 中位 53 µs、p1 18 µs、
最长空窗 189.98 s（无输入时段，无行属预期）。L_down/L_up 各 853 次；移动包 394,488；
累计 |dx|=1,012,266、|dy|=607,230 [confirmed：全量解析]。

### 3.4 已知坑

1. **只记录真实硬件输入**：SendInput 合成的移动/点击不产生 WM_INPUT（2026-08-29 实测：LL 钩子见
   65 个 move、WM_INPUT 为 0）[confirmed 实验]。对遥测是特性（只录真人输入）；做重放注入验证时
   不要指望本通道看到合成输入。
2. **时间戳不在 epoch 域**：`t`/`qpc` 是开机相对的 QPC 域；换算 epoch 用 clock_map 的
   `t_unix − t`（本会话 6/6 条 map 恒为 1788029707.185916，漂移 0.000000 s）[confirmed]。见 §5。
3. **clock_map 实测节奏与代码不符**：代码设定 10 s 一条，实测仅 6 条 / 2129 s，间隔
   ≈393.31 s（383.4 / 776.8 / 1170.1 / 1563.4 / 1956.7 / 2350.0）[confirmed 数据；机理未定位，
   [待验证]]。**对齐不要依赖 map 密度**：`t ≡ qpc/1e7` 恒等式 + 首条 map 足够。
4. **epoch 锚是"单点重建"**：`t_unix` 由启动时刻一对 (time.time, perf_counter) 推出，之后不再采样
   墙钟。若会话中途发生 NTP 步进/手动改钟，map 之间**看不出**漂移（恒等是构造性的）
   [confirmed 代码 → 风险 [inferred]。本会话 6 条 map 漂移 0，未见异常]。
   通道 A/D 逐帧用 time.time()，NTP 步进会直接表现为帧 dt 跳变——两通道机理不同，对齐时注意。
5. **`--poll` 降级模式语义不同**：GetAsyncKeyState 500 Hz 轮询 + GetCursorPos 差分——丢失 raw 增量
   精度、点击沿量化到轮询周期。**混入正式数据前必须区分**（文件本身无模式标记，[待验证]——建议
   人工记录）。
6. 输入记录器与游戏生命周期无关：游戏重启不需要任何操作 [confirmed：OS 层]。

---

## 4. 通道 D：武器字段观察（weapon_field_watch.py）+ 规划通道 E（shot_probe.py）

### 4.1 文件命名（D）

固定名 `weapon_watch_log.jsonl`，**追加模式**；`t0 = time.time()` 每次启动重置 ⇒ **同文件可含多个
会话，t 会回跳**。实证：现文件 289 行含 2 个会话，记录 #141 处 `t: 92.046 → 5.665`
[confirmed 数据]。消费前必须先按 t 回跳点切会话。

### 4.2 记录 schema（D）

```json
{"t": 0.436, "click": 1, "changed": {"608": 1, "672": 256}}
```

| 字段 | 类型 | 单位/域 | 语义与来源 |
|---|---|---|---|
| `t` | float | 秒，会话相对（`time.time() − t0`，3 位小数 ≈1 ms） | **无 `ev` 字段**（§7-3）；轮询周期 50 ms，仅"有槽位变化或有点击沿"时写行 ⇒ 空闲时段整段缺行（实测最长 t 隙 50.8 s） |
| `click` | 0/1 | — | `GetAsyncKeyState(VK_LBUTTON) & 0x8000` 电平（**非沿**；沿需自行差分），量化 ±50 ms [confirmed：lbtn()] |
| `changed` | object | — | 本 tick 变化的 int32 槽：**键 = 武器处理器对象内字节偏移（十进制字符串）**，值 = 新值。观察窗口 = handler+0x0 … +0x1500 全部 int32 槽 [confirmed：SCAN_LO/HI] |

观察对象：`WeaponHandler` 实例（类名族含 BP 子类——SuperStruct+0x40 链可达原生类即算，
取第一个非 `Default__` 实例）[confirmed：find_weapon()]。**注意观察的是 handler，不是武器对象**：
武器对象字段（弹药 0x13a4 等）在 `handler+0x288` 的 TArray 元素里，**不在本窗口内** [confirmed]。

### 4.3 实测观测到的字段（weapon_watch_log，2 会话 / 119 次点击）

| 偏移 | 观测值 | 行为 | 判读 |
|---|---|---|---|
| **+0x260** | {0, 1} | 与 +0x2a0 同沿成对翻转（172 次，0 次不同步）。脉宽固定：63 ms 为主（实测 61~128 ms，= 1~2 个 63 ms tick）；脉冲间隔中位 0.50~0.56 s（min 0.187）；**脉冲上升与 L_down 同 tick**（延迟中位 0.000 s，max 0.063 s），但会话 B 中 **47% 的点击（25/53）无脉冲**；持续开火时呈 ~0.5 s 链式节流（会话 A：58 脉冲连成 1 条链 vs 66 次点击） | **周期翻转/节流脉冲，不能当开火事件**（本行即任务书给定坑位，数据支持"漏检近半点击"）[confirmed 计数；机理 [inferred]：受 ~0.5 s 内部计时器门控的闪烁对；"命中闪烁"假说 [待验证]，判别实验=对墙开火看是否仍脉冲] |
| **+0x2a0** | {0, 256} | 恒与 0x260 同沿 | 同上（UI 参数对） |
| +0xda0 | float（int32 位型） | 倒计时：0.5587 → 0.0037，每 ~63 ms 步进 −0.06，归零后回卷；仅 4 个 bursts 共 37 次变化 | 周期 ≈0.56 s 的 float 倒计时，与 0x260 脉冲同周期 [confirmed 数值；语义 unknown] |
| +0xbc0 | {0x03000100, 0x01000100} | 两态翻转 8 次 | unknown |
| +0x2a8 | 1580876928 | 单次变化 | unknown |

### 4.4 已知坑（D）

1. **0x260/0x2a0 不是开火标记**（§4.3）。开火检测用 handler 偏移：+0x398 `bIsOnCooldown`（开火置 1、
   tick 复位 [confirmed AttemptShot 真身 / 复位 [inferred]]）、+0x1498 `TimeBetweenShots` f32
   （开火重置满值后递减，突变点即开火 [inferred]）[出处：event_layer_notes §2]。
2. **miss 计数（accuracy）类场景无弹药递减**：武器为 InfiniteUse / 不扣弹路径，弹药递减信号不存在，
   shot 判定必须退回坑 1 的冷却/间隔信号 [inferred：event_layer_notes §2.3 "CooldownType=InfiniteUse
   （KovaaK's 大部分场景）弹药不扣"；与 weapon_watch 会话未见弹量型递减一致]。
   判别：`weapon+0x1384 CooldownType`（==1 走弹药路径）[inferred→likely] 或 `shot_probe --detect` 实测。
3. **无绝对时间锚、20 Hz 变化触发写行、多会话追加**（§4.1/4.2）——本通道只适合做字段行为研究，
   不适合做正式事件流。
4. **游戏重启不能自愈**：读失败仅每 30 s 重试同一进程句柄，不会重新找 pid [confirmed：main() 循环]。
5. click 电平采样 ±50 ms 量化；与通道 C 的 Raw Input 沿（µs 级）不可混用。

### 4.5 规划通道 E：射击事件（shot_probe.py，schema 仅有代码依据，**[待验证]——尚无实数据**）

- 命名：`shot_probe_out_<MMDD_HHMMSS>.jsonl`；`--run --hz 100`（默认 100 Hz）。
- `state` 行（每 tick）：`{"ev":"state","t","cd","interval","ammo","idx"}` —— handler+0x398 u8、
  +0x1498 f32、武器+0x13a4 i32（经 handler+0x288 Data[+0x298 idx] 寻址，上界 +0x290）[confirmed 偏移：
  两个独立真身；**修正**：旧 shooting_system.md §7 的读法（0x290=idx）作废]。
- `shot` 行（弹药递减沿）：`{"ev":"shot","t","ammo","prev_ammo","weapon"}`。InfiniteUse 场景无 shot
  行（§4.4-2），需换信号。
- `--detect`：5 s 实弹自动锁定 [0x1300,0x1420) 内递减字段（自校准）。
- `--wait` 仅覆盖首次校准（40×15 s）；采样中断无重附着 [confirmed：main()]。

---

## 5. 双时钟对齐与跨通道配准（实证）

### 5.1 各通道时间戳的时钟来源（代码依据）

| 通道 | 逐行 `t` 的来源（代码） | 时钟域 | 文件内锚 |
|---|---|---|---|
| A 目标 | `round(time.time() − t0, 4)`，t0=run() 起点墙钟 [target_poll2.py run()] | epoch 差（相对） | **无** |
| B 相机 | `round(time.time() − t0, 4)` [camera_probe.py run()]；首行 clock_map `t = time.time()`（epoch 绝对） | 相对 + epoch 锚 | 首行（唯一） |
| C 输入 | `qpc / QPF`（QPC=QueryPerformanceCounter）[input_logger.py now()] | **perf_counter（开机相对 QPC）** | clock_map：`t_unix = t0_unix + (perf − t0_perf)`，首行 + 周期（实测 §3.4-3） |
| D 武器 | `round(time.time() − t0, 3)` [weapon_field_watch.py] | epoch 差（相对） | **无** |
| E shot | 同 A（`time.time() − t0`）[shot_probe.py run()] | epoch 差（相对） | **无** |

要点 [confirmed，除注明外]：

- **QPF = 10⁷（100 ns tick）**：396,194 行 `t ≡ qpc/1e7`（|误差|<5e-8 s）[实测]。
- **QPC 单调**，不受 NTP/改钟影响（Windows 语义）；`time.time()` 是墙钟，会被 NTP 步进。
  通道 A/D/B 逐帧使用 time.time() ⇒ NTP 步进会体现为帧 dt 跳变；通道 C 的 t_unix 是单点重建 ⇒
  中途步进不可见（§3.4-4）。本批数据未见任何跳变。
- **epoch ↔ perf_counter 差恒定**（会话内）：输入通道 6 条 map 的 `t_unix − t` 漂移 = 0.000000 s
  （2129 s 内）[实测；注意恒等部分来自构造]。
- 通道 B 的 clock_map `t` 是 **epoch**，帧 `t` 是**相对**——同一文件里同名字段两种语义（§7-1）。

### 5.2 各通道时间戳实测统计（2026-08-30 同轮数据）

统计对象：`target_poll_out_0830_030352.jsonl`（A）、`camera_probe_out_0830_033226.jsonl`（B）、
`input_log.jsonl`（C）、`weapon_watch_log.jsonl`（D）。`validation_fullstack_*` 两文件为同轮
（03:01–03:08）采集：targets 是 A 文件的前 7800 行**字节级前缀**；input 是 C 文件的前 72400 行前缀
[confirmed：逐行比对]。

| 通道 | n | t 范围 | 分辨率 | 单调性 | dt 特征 |
|---|---|---|---|---|---|
| A | 62,800 帧 | 0 → 1961.65 s | 0.1 ms（round4） | 严格单调（0 回退） | 中位 31.0 ms / p95 32.0 ms / max 1.05 s |
| B | 1 map + 7,900 null | 相对 0→≈158 s（行号×20 ms 推算） | 0.1 ms | —（无有效帧） | 名义 50 Hz |
| C | 396,194 事件 | 383.460 → 2512.818 s（perf 域） | 100 ns（QPC） | 严格单调（0 回退） | 中位 53 µs / p1 18 µs / max 189.98 s（无输入空窗） |
| D | 289 行 / **2 会话** | 0.436→92.046 + 5.665→403.440 s | 1 ms（round3） | **会话边界回跳 −86.4 s** | 50 ms 轮询、变化触发 |

### 5.3 对齐算法（可执行伪代码）

原则：**有锚通道直接换算；无锚通道"粗锚（文件名墙钟，±1 s）+ 共同事件互相关细化（±5 ms）"**。

```text
# ---- 常量（从各文件首部读出） ----
# input_log.jsonl 首行: {"ev":"clock_map","qpc":Q0,"t":P0,"t_unix":E0}
DELTA_IN = E0 - P0                       # perf → epoch；实测会话内恒定（§5.1）
in_to_epoch(t)        = t + DELTA_IN      # 通道 C 行 → epoch
in_perf_of_epoch(e)   = e - DELTA_IN      # epoch → 通道 C 行

# camera_probe_out_*.jsonl 首行: {"ev":"clock_map","t":E1,...}（epoch）；帧 t 为相对
cam_to_epoch(t_rel)   = E1 + t_rel       # 通道 B 帧 → epoch（<1ms 偏差，clock_map 先于 t0 写入）
# ⇒ B↔C 对齐 = 各自换算到 epoch 后直接比较（B 有锚，无需互相关）

# ---- 无锚通道（A/D/E）→ 通道 C 域（两步） ----
def align_target_to_input(target_jsonl, input_jsonl, coarse_epoch):
    # 第 1 步：粗锚（±1s）。target 录制器文件名 <MMDD_HHMMSS> ≈ t0 墙钟（strftime 先于 t0，亚秒内）
    s0 = (coarse_epoch - 本地时区偏移) - DELTA_IN     # 使 input_perf ≈ target_t + s0

    # 第 2 步：共同事件互相关（点击 ↔ 击杀）
    deaths = [ 对每个 addr，相邻有效点满足 d>2000u 或 v>5000u/s 的"跳变前一帧时刻" ]
             # = 击杀/池化重生时刻（与 cleaner 切段同判据，§6.5）
    clicks = [ 行.t  where "L_down" ∈ btn ]           # 通道 C，perf 域

    best = argmax over s ∈ [s0-3, s0+3], step 1ms:
        count{ e ∈ deaths : ∃ c ∈ clicks, 0 ≤ (e + s) − c ≤ 0.200 }
    # 含义：找使"死亡发生在某次点击后 0~200ms 内"的死亡数最大的平移 s

    # 接受判据：best_count ≥ 5 × 随机基线（基线 = 在 [50,950]s 随机 s 的得分分布）
    return s_best    # 之后 input_perf(t) = t + s_best；epoch(t) = t + s_best + DELTA_IN
```

### 5.4 实证结果（validation_fullstack_targets ↔ validation_fullstack_input，同轮）

- 事件提取：目标侧 2 addr × 58 条命 = **116 个跳变死亡**（与 cleaned rounds_index 的 lives 数一致）；
  输入侧 176 个 L_down [confirmed]。
- **互相关峰：s* = 525.455 s**（`input_perf = target_t + 525.455`），**115/116 死亡匹配**
  （唯一 miss 的一次可能对应输入记录器未捕获的点击）[confirmed]。
- 峰形：平台 [s*−4 ms, s*+49 ms] 全分（115）；左缘陡降（−6 ms:108 → −22 ms:57 → −48 ms:1）；
  **右缘 = 最大 click→death 延迟**。随机偏移基线（200 抽样）：中位 0、p95=24、max=52
  ⇒ 峰值≈5×p95，判定可靠。**配准精度 ≈ ±5 ms** [confirmed]。
- click→death 延迟（s* 处）：中位 **21.9 ms**，范围 [4.7, 48.8] ms —— 物理合理（输入记录延迟 +
  游戏 tick 处理 + 通道 A 31 ms 轮询量化的卷积）；**事件级同步精度受 31 ms 量化限制，≈±40 ms**。
- 对照组：出生事件（跳变后帧）在 s* 处仅 11/116 与点击锁合（出生不点击锁定）[confirmed]。
- 交叉验证：文件名墙钟法给 s0 = 524.814（03:03:52 整秒截断），与互相关 s* 差 0.64 s；
  反推 target t=0 的 epoch = 1788030232.64 = 本地 03:03:52.64，与文件名 03:03:52 一致
  [confirmed 两法自洽]。
- 时钟一致性：对齐后 `t_unix(target t=0) = DELTA_IN + 525.455 + 1788029707.186`，三条独立证据
  （文件名、互相关、map 锚）闭合到 <1 s [confirmed]。

### 5.5 相机 ↔ 输入配准（伪代码，**[待验证]**——尚无有效 cam 帧，见 §2.4-1）

```text
# B、C 都有锚：先各自换算到 epoch（§5.3），再做滞后扫描细化
bins = 20 ms
X[i] = Σ dx（通道 C，epoch-bin i）
Y[i] = Δyaw（通道 B，yaw 按弧度解缠绕后差分，epoch-bin i）
τ* = argmax over τ ∈ [−100, +100] ms of corr(X, shift(Y, τ))
# 期望 τ* ≈ 0~30 ms（游戏采样输入 → 渲染更新 CameraCache → 50Hz 外部轮询的流水线延迟）
# 验收：|τ*| 有清晰单峰；换用 pos.x/y 对 dx/dy 复核符号一致性
```

### 5.6 对齐操作建议（保守项）

1. 优先用有锚通道（B/C）之间直接换算；A/D/E 与它们对齐一律走 §5.3 两步法。
2. 每次会话重新求 s（**不要**跨会话复用偏移：perf_counter 开机相对、target t0 每会话重置）。
3. 对齐结果落盘时同时记录：`{channel_pair, s, method, matched/total, latency_stats, baseline}`，
   便于审计。
4. 判据不达峰（如无人击杀的局）时，退而求其次用"文件名墙钟 ±1 s"并显式标注精度。

### 5.7 操作注记：Timelimit 是游戏秒 —— 对账窗一律按墙钟时长切（Timescale）

- 场景可设 `Timescale≠1`（实测 1wall5targets_pasu = 0.7）：Timelimit 60 **游戏秒** = 85.7 **墙钟秒**
  （60/0.7）。`.perf` 逐 tick 为墙钟秒：末 tick 85.702 ≈ 60/0.7、duration 86.202、文件名时刻
  （=局结束墙钟）三者闭合 [confirmed：crosscheck_morning_report.json / morning_session_0830.md，
  2026-08-30 晨会话首次发现]。
- **对账/切窗一律用墙钟时长 = time_limit / timescale**（.perf header 含 timescale 字段）。
  按 60 s 整切 pasu 类局会把局尾 26 s 的事件错判到窗外（实测该局 60 s 窗口径死亡少计 26，
  墙钟窗 86.2 s 修正后总缺口 −12/289）。

---

## 6. cleaned/ 层（cleaner.py，v1 规则不变）

```
cleaned/
├── rounds_index.json                  # 全部输入文件的轮次元数据 + 丢弃报表
└── <输入文件名去扩展名>/
    ├── round_01.jsonl                 # 每轮一个帧文件（schema 同原始格式 + tr 字段）
    └── ...
```

> **[2026-08-31] 轮次旁车扩展**：views/inputs/bb/merge_manifest 等旁车件合同见
> [`SIDECARS.md`](SIDECARS.md)（相机/输入并轮 + 目标尺寸 + 对齐回执；录制器
> clock_map/哨兵/重附着的配套变更也在该文档 §6）。旁车文件名不以 `round_` 开头。

当前 `cleaned/` 含 4 个 source：3 个 v1 验证文件 + `target_poll_out_0830_030352`（9 轮，
92~1872 s，1 phantom + 2 origin-ghost 轨道剔除，121,463 垃圾点）[confirmed：rounds_index.json]。

### 6.1 round_NN.jsonl

与原始格式同构：`{"ev":"frame","t":<原始绝对秒>,"tr":<相对本轮 t_start 秒>,"targets":[[addr,x,y,z],...]}`

- `t`：与输入一致（跨轮回溯用）；`tr`：本轮首帧 =0（下游对齐用）。
- `targets` 按_tid 升序_排序；坐标 3 位小数（float32 在 4096 量级分辨率 ≈0.0005，无损失）；
  已不含野值/原点/幽灵；轮时间跨度内的空目标帧保留为 `"targets": []`。不插值。

### 6.2 rounds_index.json

结构（完整示例见 v1 及现文件，字段不变）：`format_version=1`、`generator`、`params`（全部清洗阈值）、
`sources[]`：每输入文件一项 —— `source / outdir / t_min / t_max / frames_total / n_rounds / rounds[] /
discarded{} / per_addr_cut_stats{}`。`rounds[]` 项：`round(1 起)、file、t_start、t_end、duration、
n_frames、n_targets、n_moving_targets、motion_mix(moving/static/mixed)、targets[]`。
`targets[]` 项（每目标元数据）：

| 字段 | 语义 |
|---|---|
| `tid` | **轮内目标身份，0 起、按出生先后编号。跨帧/跨 life 追踪一律用它**；addr 仅用于与原始采集对账 |
| `addr` / `addr_hex` | UObject 指针（可被池化复用） |
| `motion` | `moving`（单 life 速度 >50 u/s）/ `static` |
| `birth` / `death` / `alive_window` | 首生~末死（跨池化重生） |
| `n_samples` / `n_lives` | 样本数；life 数 = 出生次数（含池化重生，≈击杀数+1） |
| `path_length` | life 内累计位移之和，**不含**重生跳距离 |
| `lives[]` | 每段 `{t_start,t_end,n,path}` |
| `domain` | 全 life 并集的位置域 min/max（场景归一化依据） |

`discarded`：`malformed_records / phantom_tracks{} / origin_ghost_tracks{} / static_ghost_tracks{} /
garbage_points / noise_segments`（审计用）。

### 6.3 边界情况：重生跳 vs 野值

| 特征 | 击杀重生跳（**合法**，life 边界） | 瞬移/野值（**杂质**，丢弃） |
|---|---|---|
| 新位置域 | 场景域内（\|coord\| ≤ ~4096） | 出界（>8192，实测 z=−48039）或 (0,0,0) |
| 网格/平面约束 | 静态局对齐 128 网格、墙靶恒 x=3840 等 | 无结构 |
| 跳后行为 | 新 life 正常持续 | 轨道终止或继续出野值 |
| 速率 | 同量级，**速率不可区分，靠位置域区分** | — |

cleaner 统一切段，再按"新段位置合法性 + 段长"决定去留 ⇒ life 边界 = 出生/死亡事件，无野值。

### 6.4 tid 语义（明确）

- tid 在**轮内**唯一、稳定（含池化重生：重生不换 tid）；跨轮、跨文件无意义。
- 编号 = 轮内首次出生先后（cleaner 按 `rmap[a][0][0]` 排序赋 0..n−1）[confirmed：assign_rounds 后排序]。
- 下游做"同一目标"的轨迹/击杀统计必须用 tid；比较不同轮用 (source, round, tid) 三元组。

### 6.5 重生判定规则（切段阈值）

- 判据：单帧位移 **>2000 u** 或 单帧速度 **>5000 u/s** → 切段（= 出生/死亡边界，含池化复用）。
- 依据：正常目标移动 ≤~2000 u/s；**实测最小重生跳 676 u、折算速度 ≥8450 u/s**
  （v1 曾写 "676u/31ms"，按 31 ms 折算应 ≈21800 u/s；两种口径均远超 5000 阈值，结论不变；
  精确分母 [待验证]）。速度阈值对采样停顿鲁棒（停顿期正常移动不触发）[confirmed：cleaner.py 常量注释]。
- **二级短距重生判据（规则 2b）[fix 2026-08-30，出处 analysis/external/cleaner_fix_0830.md]**：
  v∈(2100, 5000] u/s 且 d∈(60, 2000) u 的帧间跳变，若与段内前 3 步平均滑行方向夹角 >78°
  （cos<0.2；静态目标要求 d>100 u；与相邻步构成"跳起-回落"弧对者豁免）→ 判重生切段。
  依据：实测合并跳 v 2330~4156 u/s、d 68~128 u，而滑行/走位 ≤~2000 u/s、卡顿前窜 60~69 u
  （同向 cos≈+1）、跳跃动画弧 ≤90 u —— 速度/位移/方向三道门叠加后 5 处真合并全捕获、
  两类合法移动全豁免（030352 ×3、235309 ×2；validation 三目录与 morning 文件零变化）。
  切段计数落盘 `per_addr_cut_stats.respawn2_cuts`（`params` 同步新增 5 个判据参数，附加式变更，
  旧消费者不受影响）。
- **原理性边界（merge B 型，不可由 cleaner 修复）**：死亡+重生落在同一 31 ms 采样间隙、
  重生点距死亡点 ≤31.5 u 且同向同速时，位置序列与"目标未死"完全不可区分——纯位置通道
  信息论上无法恢复该事件（实测 030352 R03/R02 各 1 处；同位重生占比更高的场景见
  reports/PIPELINE_ACCEPTANCE.md 缺口清单）。下游用消亡数推 kills 时，单池/同位重生类场景
  每局保留 ±1 校准余量，或用 .perf kills 交叉校准。
- 其余点级规则：NaN/Inf/\|coord\|>8192 野值剔除；距原点 <1.0 死亡残留剔除；段 <3 样本丢噪声；
  整轨寿命 <2 s 且位移 <1 丢弃。
- 幽灵轨道剔除：首个非空帧即出现 ∧ 出现帧占比 >95% ∧ 速度 <10 u/s（实测幽灵 0~2.8 u/s，
  真目标 ≥90 u/s）[confirmed：v1 验证数据]。
- 轮次切分：出生事件间隔 >10 s，或全灭空窗 >0.05 s 后再有出生 → 新一轮（实测局内重生空窗 ≤1 帧
  0.031 s、换局 ≥2 帧 0.063 s、局间隔可达 112 s）[confirmed：v1 验证]。

### 6.6 cleaned 层限制（v1 保留 + 新增）

- 小于跳变阈值（≈155 u @31 ms）的重生跳不切段（实测影响可忽略）。
- 单目标场景换局空窗 <0.05 s 会误合并；多目标局内 ≥2 帧全体缺采会误切（本批未发生）。
- 只有位置，无朝向/尺寸/血量；玩家侧数据（准星/射击/命中）需通道 B/C/E 对齐补齐。
- **新增**：cleaner 全局按 `t` 排序——**多会话追加文件（t 重置）喂给 cleaner 会把会话交错**
  [confirmed：load_frames `frames.sort`]。先按 t 回跳切会话，再逐会话清洗（通道 D 文件实证含 2 会话）。

---

## 7. 发现的不一致（跨通道 schema 冲突清单）

1. **同名 `t` 三种语义**：A/B/D/E = 会话相对秒（epoch 差）；C = perf_counter 秒（开机相对）；
   B 的 `clock_map.t` 又是 **epoch 绝对秒**（与其自身帧 `t` 相对值同文件并存）。下游严禁跨通道
   直接比较 `t`，必须走 §5。
2. **`clock_map` 行两种 schema**：B = `{t(epoch), povs, offsets}`；C = `{qpc, t(perf), t_unix}`。
   同 `ev` 名不同结构，解析需按通道分发。
3. **`ev` 字段覆盖不全**：通道 D 记录**没有** `ev` 字段；B 的 `null` 行既无 `ev` 也无 `t`。
4. **锚覆盖不全**：B/C 有文件内绝对锚；A/D/E 完全没有（§5.3 两步法兜底）。*建议*：录制器 v2.1
   统一首行写 clock_map（本次不改录制器，仅规范建议）。
5. **时间分辨率三档**：100 ns（C）/ 0.1 ms（A/B）/ 1 ms（D）；轮询量化：C=设备上报率、A/B≈31 ms、
   D=50 ms、E≈10 ms。事件级对齐精度受**最粗通道**限制（§5.4：±40 ms）。
6. **单位域不同**：目标/相机 pos = cm；rot = 度；输入 dx/dy = **设备计数**（与 DPI、灵敏度相关，
   不是 cm 也不是度）；fov = 度。求"速度"只允许在 cm 域（通道 A/B）做。
7. **会话/文件关系不一致**：A/B/E 每次附着新文件；C/D 固定/用户路径**追加** ⇒ D 实证含 2 会话
   t 回跳、C 含前缀拷贝。cleaner 不能直接吃多会话文件（§6.6）。
8. **重启行为不一致**：A 自动重附着（新文件）；B/E `--wait` 只管首次校准、中断后不重附着
   （B 持续写 null）；D 死循环重试同一句柄；C 与游戏无关 [confirmed 各 main()]。
   ⇒ **跨游戏重启的会话，各通道会话数不同，对齐按会话对进行**。
9. **偏移记法不一致**：D 的 `changed` 键是十进制字符串（"608"=0x260），其余文档/通道用十六进制
   叙述。解析时统一转 int。
10. **遗留错误口径（引用时注意）**：shooting_system.md §7 的武器仓读法（+0x290=idx）已被两个独立
    真身推翻，正确读法 `weapon = [h+0x288] + [h+0x298]×8`、`idx < [h+0x290]` [confirmed：
    event_layer_notes §2 修正]；`CurrentAmmo` UPROPERTY（uint8）属 UI 控件类，不是弹药存储
    [confirmed：event_layer_notes §1.3]。

---

## 8. 下游使用建议

1. 目标身份用 `tid`；addr 仅对账。时间对齐轮内用 `tr`，跨轮/跨文件用 §5 流程求 s 后换算。
2. 输入通道先换域（`t + DELTA_IN` → epoch）再与其他通道比较；点击沿用 `L_down`（µs 级），不要用
   通道 D 的 `click`（±50 ms 电平）。
3. 相机数据使用前先检查 null 比率（§2.4-1 的坑会让整个文件无效）；有效帧的 pos/rot 与目标同域，
   可直接做视点-目标几何。
4. 击杀/出生事件从 cleaned 的 `lives[]` 边界取（= 切段边界），原始侧自行提取用 §5.3 判据保持一致。
5. 静态局：life = 一次"出现→被击杀"窗口（反应时标注）；移动局：life 内可差分求速度，跨 life 差分
   无意义。场景归一化用 `rounds_index.domain`。
6. 轮次完整性校验：`n_targets` 应与场景配置一致（如 1wall5targets=5）；不一致说明切分有误，回报。

## 9. 已知限制与待办

- 通道 B 尚无一个有效 cam 帧（BP 子类校验坑，§2.4-1）——相机-输入滞后 τ* [待验证]。
- 通道 E（shot 事件）schema 无实数据验证；InfiniteUse 场景无弹药信号（§4.4-2）。
- 通道 C 的 clock_map 实测间隔 ~393 s 与代码 10 s 不符，机理 [待验证]（不影响对齐）。
- 0x260/0x2a0 的机理判读（节流闪烁 vs 命中闪烁）[待验证]：判别实验 = 对墙开火（必 miss）观察脉冲。
- 输入通道 epoch 锚的单点重建对 NTP 步进不设防（§3.4-4）；长会话建议在锚之外定期对读
  `time.time()`（需改录制器，当前未做）。
- 各录制器统一 clock_map、统一 `ev` 枚举、D 通道加会话分隔行 —— 列为录制器 v2.1 规范建议（本文档
  仅规范数据消费，不改录制器）。
