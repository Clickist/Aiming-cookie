# 视频面板复盘体验升级 Brief

> **状态：提案草稿（未批准施工）。** 本文是 2026-08-27 与点点讨论后落库的设计提案，供后续细化与拍板；不构成实施授权，不改变 PRD/ARCHITECTURE/frontend-uiux-design 任何既有合同。行业参照来自只读调研（Frame.io、Vimeo Review、Loom、YouTube、media-chrome/Vidstack/videojs-markers 插件生态、Kinovea/mpv/VLC、OBS replay 心智），证据以调研原文为准。

## 一、现状诊断（基线于 `webapp/frontend/components/task5/VideoView.tsx`）

现有能力：播放/暂停、音量+静音、全屏、**固定 ±5 秒**快进退（`step()`）、**1×/0.5× 两档**变速、进度填充 + 拖动条的时间轴。

四个核心缺口（对照射击录像复盘场景）：

| # | 缺口 | 后果 |
|---|------|------|
| 1 | 无帧级步进（±5s 跳过细） | 无法精读击发前后的准星轨迹 |
| 2 | 变速只有两档且无 0.25× | 慢放精讲不可用 |
| 3 | 时间轴上零标记 | 击杀/死亡/甩枪/讲评事件不可见，时间轴只是刻度尺 |
| 4 | 无 AB 循环（产品已有「信号片段」概念但播放器不支持） | 讲评无法反复看某一段 |
| 5 | 聊天 @51.5s 跳转后继续播放、无到达反馈 | 用户来不及看清证据帧 |

另有结构事实：`CoachVideoPane.tsx` 仅是标题/加载壳；`presentationCache` 已在 pane 与消息卡间共享分析呈现数据，是事件标记的数据来源候选。

## 二、分层方案

### P0 精读三件套（小而硬）

1. **逐帧步进**：`,/.` 键与按钮按当前视频帧率步进（33ms@30fps，从 video 元素实际 fps 推算或取 33ms 常量），Shift 加倍粗调；替换现有 ⏮⏭ 的 ±5s（保留为长按或另设跳转）。
2. **变速三档循环**：0.25× → 0.5× → 1× → 回 0.25×；按钮明示当前档位字样；切换即时作用于 `playbackRate`。
3. **@time 到达即暂停 + 到达反馈**：从聊天时间链接 seek 时 `video.pause()`，播放头做一次性高亮脉冲（合规写法：`box-shadow: 0 0 0 4px var(--ring)` —— ring 属阴影合同白名单 token；reduced-motion 下静态高亮一次）。手动拖动进度条不改变播放状态。

### P1 事件上轴

- 标记数据契约对齐 `videojs-markers` 四元组习惯：`{ timeMs, type, label }`；type 映射语义色 token：kill→`--event-kill`、miss/death→`--event-miss`、纠偏→`--event-corrective`、峰值/甩枪→`--event-peak`。
- 渲染：绝对定位 2px 竖条插层（位置见 P2 栈图），**颜色+形状双通道**（竖条/三角/菱形之一待细化）防色盲不可辨；
- hover 显示 label tooltip；点击 = seek 并暂停在锚点帧；
- 扎堆聚合（间距 <4px 合并为带数量徽标的簇钉）列为可选后续。

### P2 信号片段 AB 循环（与点点已过的规格）

**一键三态语法**（mpv/Kinovea 惯例）：第一次触发设 A 点（立竖线+A 签）→ 第二次设 B 点并立即开始循环 → 第三次清除。全程零确认弹窗；Esc 随时退出。

**DOM 插层**（自下而上，色带插在进度与播放头之间）：

```
timelineTrack → timelineProgress → ★band(新) → ★markers(P1,新) → timelineCursor(z6) → timelineInput(z7)
```

定位照抄 progress 惯例（`left:10px` + 百分比）；`起点%=A÷总时长×100`，宽度同理。

**三状态显示内容**：

| 状态 | 屏幕出现 |
|------|----------|
| 仅 A 点 | 2px 竖线 @A 处 + 12px 小字签「A」 |
| 循环激活 | ① 半透明带体（`--event-peak` 约 22% 透明度版，略高于轨道）；② 两端 2px 实线把手常亮；③ 带中央常显时间码 `00:48.2 – 00:53.9`（0.1 秒精度，始终可见）；④ 来源小字如「甩枪窗口 · analysis:12」 |
| 清除 | 整组 DOM 移除 |

**配套元素**：播放条右侧 pill 按钮 `↻ 循环 A–B · Esc退出`——状态灯 / 撤销入口 / 快捷键教学三合一，点击即清。

**行为细则**：`onTimeUpdate` 内 `t>=B → seek(A)` 且保持当前 playbackRate；进入循环不代按播放键；Esc 全局监听但输入框聚焦时不劫持；paused 进入则 paused 循环；从证据 chip 直达时沿用 `initialTimeMs` 传参惯例扩展可选区间参数（一次传入归内部管理）。

**token 备注**：优先复用 `--event-peak`；若点点要求循环专属色区分普通事件，往 `ui/tokens.ts` 浅深双主题各加一行 `--loop-band` 即可。

## 三、待拍板决策点

| # | 决策 | 我方推荐 |
|---|------|----------|
| D1 | @time 跳转是否自动暂停 | 是（"带你去看证据"语义）；手动拖拽不受影响 |
| D2 | AB 入口形态 | 三路并存：播放条按钮 + `[` `]` 键盘党 + 点击信号窗口证据 chip 直达该区间循环 |
| D3 | 聚合与缩略图预览 | 先不做，实机用出痛感再排期 |
| D4 | 色带配色 | 默认 `--event-peak` 透明版；要不要专属 `--loop-band` 由点点定 |

## 四、实施前核查项

1. `AnalysisWorkspacePresentation` 是否携带事件清单及字段名（type/label/timeMs 齐备度）；缺什么走 read-model 补投影，不改底层合同。
2. 「信号窗口」证据的现存数据形状（起止毫秒、关联分析 id）与 band 初始化映射核对。
3. 实施前先跑 design-system-contract / task6-source 基线，确认样式扫描范围（hex 禁令、grid-template-rows 禁令生效文件清单）后再动手，避免踩合同测试。
4. 尺寸细节（±10px 轨道内边距下极端百分比的对齐偏差）顺手修正时不得破坏拖动输入层的命中区。

## 五、验收标准草案（细化种子）

- [ ] `,/.` 逐帧、Shift 粗调可用；按钮同步呈现
- [ ] 三档变速循环且循环中切档立即生效并保持到退出
- [ ] 聊天 @time → 视频 pane 打开并暂停在目标帧，播放头有一次性反馈；连续点击不同时间码正确更新
- [ ] 时间轴显示全部类型标记；hover 有标签；点击暂停在标记处
- [ ] AB 三态完整可逆；循环期间角标可见；Esc/点按钮均能清除
- [ ] 从信号窗口 chip 进入直接获得预填区间循环
- [ ] prefers-reduced-motion 下所有新增动画关闭且状态仍可辨识（至少靠形状/文字）
- [ ] 上述各条的源码断言测试入 tests/ 相应文件

## 六、参照出处（节选）

Frame.io 评论钉/区间评论（seek-and-pause、区间把手）；Loom 双向联动与来源高亮教训；YouTube 章节缝隙法（密集标记防重叠思路）；videojs-markers `{time,duration,class,label}` + prev/next 数据契约；Vidstack TimeSlider 的 keyStep/shiftKeyMultiplier 倍率公式；mpv `[` `]`/`,` `.` 及 ab-loop 三态；Kinovea 工作区边界+慢放档位布局惯例；NVIDIA/OBS replay 的"单热键即时回看"心智。
