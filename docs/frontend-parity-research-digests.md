# 前端追平工程 · 十二路调研合订摘要

> **状态：证据参考资料（2026-08-27）。** 本文浓缩十二路只读调研的核心结论，供后续细化引用；不构成产品或实现合同。配套总纲：[`handoff-frontend-parity-2026-08-27.md`](archive/history/handoff-frontend-parity-2026-08-27.md)（已归档）。原始报告全文存于 2026-08-27 主会话记录；本文为加工版。

---

## 1. 对话流工具调用过程呈现

**一句话**：后端把 thinking_text/工具耗时/参数结果预览全都在发，前端只接了文本——A 档已照此落地。
**核心发现**：Open WebUI 的"正在思考…→已思考 N 秒"文案状态机 + shimmer 单行状态是最普适范式；assistant-ui 的"用户手动切换一次后自动化永久让位"解决自动开合抢控制权；Zed #33401 是默认全展开失控的反面教材；Cline 用连续工具聚合成计数行。
**红线**：循环动画必须 reduced-motion 关闭并保留终态可读（shimmer 关闭时回填文字色）。

## 2. Provider 接入与设置向导

**一句话**：行业标杆是 Raycast"先验后存"——Verify 通过才解锁 Save；已按此实现干跑端点（见批次E）。
**要点**：LobeChat 的"连通性检查·检查通过"内联三态文案胜过 Toast（Toast 单实例会被冲掉）；Cursor 的"功能降级前置告知"用在选自定义 provider 时提示能力差异；失败恢复走 inline 结果区而非绿点；验证进行中必须可取消、可离开（Cursor verify 卡死是著名事故）。
**需另立合同项**：无遗留（dry-run 已落）；如未来要 catalog bypass-cache 再议。

## 3. 会话侧栏与历史列表

**一句话**：时间分组头是被 ChatGPT 用户用抗议换回来的功能（移除后遭集体要求恢复），我们的分组 CSS 类早已存在只是没接线——已在侧栏批落地。
**要点**：悬停操作收敛单一 ⋯ 菜单+删除确认+归档可撤销（现删除两步确认已做，菜单化/归档入口可后续）；行内重命名=原位 input+Enter/Esc；当前高亮与"会话内分析在跑"的静默视觉语言（data-current 之外加 data-active 小圆点脉冲）尚未做可纳入后续；元数据常驻三件套=标题+一行预览+相对时间。
**反模式**：破坏性操作无确认不可回溯；归档物埋进设置深处；Cmd/Ctrl+click 当多选。

## 4. 视频复盘面（详见 video-pane-revamp-brief.md）

**一句话**：复盘三件套的行业标准用法已固化为 brief：逐帧 `,/.`+Shift 粗调、变速 0.25/0.5/1 循环、AB 同键三态零确认循环、seek-and-pause 语义、marker `{timeMs,type,label}` 四元组+颜色形状双通道。
**要点**：循环生效期间必须有持续可见色带与角标，否则用户以为播放器坏了；点击证据锚点应停帧而非继续播；marker 密集防重叠走 YouTube 章节缝隙法或聚合簇钉；@time 双向跳转要有到达反馈和来源驻留高亮。

## 5. 训练数据呈现

**一句话**：桌面固定宽+几十个点的规模下，原生 SVG 手写路线被确认为最优，引入图表库是负收益；且发现 plotly 全家桶为死依赖（已清除）。
**要点**：指标卡三层级=大数字+环比 delta+一句话规则解释（WHOOP 恢复卡范式，其色阶阈值固定公开）；行内迷你柱状趋势条（Strava 周柱图式，纯 SVG rect×N 约 30 行组件）；Leetify 五档百分位词体系与我们 deterministic_rule 架构同构，可直接映射档位徽章；Hevy 式多定义 PR（最高分/最快 RT/最高命中率分列）适合场景历史页。
**红线**：禁混用量纲合成"综合分"曲线；空图表一律给原因+下一步，不用示例数据填补；给每个指标都加图=克制失败。

## 6. 后台任务与完成通知

**一句话**：三通道置信度门槛——站内永远可用、"用户看不见 Coach"才升级 toast/徽章、系统通知留给 ≥90s 任务且涉 Tauri/Rust 需另立合同（tauri-plugin-notification 无点击回调）。
**要点**：切走再回来的完成事件聚合为一条「离开期间 N 个分析完成」可展开摘要（Devin checkpoint 压缩思路），绝不逐条重放；排队可视化给序号口径「第 i/N 位 · 预计约 X 秒」替代裸呼吸点；任务级失败入口=失败分析 chip 变红带 retry（`analysis.retry` 合同现成，纯前端）；深链消费仿 `handledVideoEventsRef` 按 eventKey 幂等去重。
**红线**：启动即索要系统通知权限（必须在首个长任务价值时刻）；纯 toast 承载重要终态；新增一级"任务中心"页违反冻结 IA；回场伪装实时流。

## 7. 键盘优先与命令面板

**一句话**：中文输入法环境下放弃 Linear 式全域字母键/chord，改两层结构=修饰键组合全局安全 + 明确作用域内的单键（Zed 谓词思路）。
**12 键位草案**：⌘K 面板 / ⌘N 新会话 / ⌘E 视频开关 / ⌘1/2/3 布局档 / ⌘, 设置 / ⌘F 聚焦搜索 / ⌘[ ] 会话前后 / ↑↓ 列表(仅 rail 焦点) / ? 速查浮层。
**三铁律**：① 全局 handler 首句 `event.isComposing || keyCode===229` 放行；② target 为输入类时除纯组合键全短路；③ 禁无修饰字母全域热键。
**面板收录 15 条**准入=日常高频×无更短路径×非界面一键可达×非不可逆；MRU 置顶但过滤开始交还 fuzzy 分数（Obsidian 行为）；带副作用命令加确认段（Raycast → 展开 form 模式）。
**选型**：cmdk@1.1.1（gzip≈15KB，Radix a11y 底座）优于手写 200-300 行；无需虚拟化（列表<100）。railing roving tabindex 可抄 primitives Tabs 已验证模式；focus-visible 应沉淀 `--focus-ring` 类令牌（rail 里手写那份是债务苗头）。

## 8. 对话窗格版式诊断（治"看着怪"）

**高严重度三病灶**：① `.task6-messages` 顶部锚定生长＋输入框独立钉底 = 中间大空洞（ChatGPT/Claude 均底部锚定，margin-top:auto spacer 解决）；② header+训练卡+discussion-bar 三层 ≈130px 常驻压顶；③ 助手行长 57–75 汉字/行远超 CJK 舒适区 30–40（内容宽应收敛 ~720px/em 计量），而 user 气泡限 min(36em,92%) 一短一强不对称。
**中severity**：`.task6-coach-panel` 宽度用 72vw 在视频面板开启后被压缩致 composer textarea 只剩 ~180px（改 %/clamp 于父容器）；四处同色 chrome 带无 hairline/渐隐分隔；user 气泡 6px 圆角"半气泡"uncanny（提 ≥16px pill 向）。
**证伪记录**：四段并非套娃卡片，是无边框同色带——病根是层次过平而非过度嵌套；训练卡收纳四法对比后建议折叠态并入 header 行成单 chip。
**Top5 改向**均已标注涉及选择器与文件范围（底锚+hero／头部瘦身／行长收敛／气泡与 chip 半径统一 sm4 归并 md6、菜单 item=R外−pad／composer 工具行拆出模型菜单去掉 padding-right:196px）。

## 9. 视觉工艺系统

**实测 bug 清单（工艺卫生批素材）**：Drawer `data-side="left"` 锚定 inline-end 复制粘贴错位（左抽屉开右边）；Toast tone prop CSS 零承接=API 说谎；IconButton 无 :hover 且命中区 32<40；Dialog __close 裸 button 重造 + backdrop 样式 theme.css 双定义必漂移；session-rail__new 重造 primary 连 hover 公式都手抄一份（公式已两处漂移）。
**补齐姿势**：Button data-busy 占原图标槽防位移+aria-busy；禁用态 opacity:1 实底灰流派（Field 输入框 opacity:.55 与之矛盾待删）；Tabs 嵌套圆角违例 r4 应 r2。
**token 缺口六枚**（浅/深值已备）：--ring-color(#c83d00/#ff8a5c)、--overlay-scrim、--divider-strong、--state-hover(M3 8%)、--state-pressed(12%)、--shadow-menu。
**工艺十则**精要：busy 占位防位移；圆角嵌套 内R=外R−间距；分割线三档决策树（结构线 hairline/强调界线 divider-strong/内容分组留白或 recessed 底色，禁画线）；hover/pressed 用 state-layer 半透明叠层禁止私定底色名；弹层家族共用 --duration-surface±50ms 同缓动族位置类才 --ease-drawer；图标 16px 栅格 ≥36px 命中区；新循环动效前先问能否一次性 enter 替代。
**红线**：rehype-raw 渲染模型 HTML=XSS；手工盘古之白已被 text-autospace 基线取代。

## 10. 富文本渲染与中文排印

**重磅发现**：`turn.ts normalizeUserFacingText` 把有序列表序号一并剥除——训练计划天然列表/表格形状被展示端永久摧毁。JSONL 存的是模型原始输出，故正确演进=归一化从"删字符"改"白名单透传"（list/table/bold/@time），历史消息零迁移。
**受控子集**：开放 GFM 表格(≤5列+横向滚动容器+数值列 tabular-nums)/有序无序列表/行内加粗(font-weight 600)；继续剥 H1–H6、代码块(消 fence 流式陷阱)、引用块/Mermaid/图片/HTML。
**流式容错选型 D**：自研受限解析器约 200 行字尾状态机；拒绝 streamdown 因强制 Tailwind v4/shadcn OKLCH token 合同；B 方案 react-markdown 未闭合 fence 吞文的坑即被绕开。
**引用融合**：@time 芯片三态(关联可点+hover 场景与邻域事件/深读兜底可点/否则静态灰)；证据栏由 presentationCache 推导仅当回答确实出现 analysis:N（防寒暄插广告）；双向 hover 高亮 chip↔时间轴刻度；>20 行/>1500 字符升级出正文保护双栏宽度（Claude Artifacts 阈值哲学倒置应用）。
**中文排印十条验收**：行高 1.75±0.10；text-autospace 原生混排（禁 JS 插空格）；弯引号；400/600 两档字重（雅黑中间权重 Win 发糊）补 Source Han Sans SC 回退；tabular-nums；CJK 禁 break-all 用 overflow-wrap:anywhere；聊天列 max ~42em；禁伪斜体；拼音空隙交 CSS；维持系统字体栈。

## 11. 输入框编排

**实锤现状反模式第一名**：运行中 send() 静默 return＝keystroke 凭空消失。解法有引擎原生合同支撑（pi AgentLane steer/followUp/nextRun + QueueMode，前端完全未消费）。
**推荐形态**（Cline QueuedPrompts/LibreChat DuringRunSendButton/Cherry Dock 三源一致）：运行中发送→可见队列 chips（96 字符截断预览、逐条取消、上浮立即 steer、回填编辑）；发送键运行中变下拉四动作 steer/queue/interrupt-steer/interrupt。**每条队列必须可视可编辑可删**（Cline #12226 丢消息教训）。
**其余**：编辑重发采截断派兼容单线 JsonlSession（fork 树需 branch_id 字段+全员感知，成本不成比例）；@ 引用骨架照 LibreChat Mention 组件（触发符监听/↑↓Enter/Esc/选中 token 化防再触发，天然引用物 analysis:N/场景名）；草稿三级持久键 sessionId|PENDING_CONVO|NEW_CONVO + 300–500ms debounce 落 localStorage 多窗格加后缀；↑ 上翻发送历史（内存 sent array 即可）；模型选择器运行中保持可用（下一轮生效）而非连坐 disabled。
**IME 守卫既有正确**勿在新下拉中破坏（isComposing+229）。

## 12. Windows 安装包品牌化（Tauri v2 NSIS）

**关键键位草案**（tauri.conf.json bundle.windows.nsis）：`languages:["SimpChinese","English"]`（保底英文防 EN 系统+中文乱回退；**必须 NSIS 名不是 zh-Hans**）＋ displayLanguageSelector:false；headerImage **150×57**/sidebarImage **164×314**（同时管欢迎+完成页，>100% DPI 放大发虚属 MUI2 机制限制，重要文字别画进位图）；installerIcon/uninstallerIcon 指向完整 icon.ico；bundle 级 publisher/copyright/homepage（进 ARP Publisher 列与 LegalCopyright 版本资源；shortDescription 不落 Windows 面）。
**图标链路**：现状 icon.ico 24KB 存疑缺高层——用 ≥1024px 源图跑 `npx @tauri-apps/cli icon` 产出全家桶（ico 含 16/24/32/48/64/128/256 层，不会自动从单 PNG 生成；微软最低集 16/24/32/48/256）。
**润色通道**：customLanguageFiles 覆盖 SimpChinese 27 条里的拗口句（降级警告等）；升级 Tauri 后 diff 新增 LangString 键防回落英文；卸载器语言读注册表记忆值测试时要清。
**分级愿望单**：低=上述配置+startMenuFolder；中=installerHooks POSTINSTALL 写首启标记配合 OnboardingFlow 直通（官方无安装器→应用参数合同）；高=整体接管 template 深色 nsDialogs 向导——上游 installer.nsi 几乎每版都在变，fork 升级易碎不建议。
**分发语境结论**：currentUser 免 UAC 维持；未签名直链 SmartScreen 必警告且声誉按版本清零——上线前备"仍要运行"图文指引，证书立项归 ROADMAP；Webview2 downloadBootstrapper 默认即可（offlineInstaller +127MB）。
**品牌参照共同点**（QQ NT/网易云）：品牌色铺满欢迎完成页侧栏、中文口语化、免 UAC 默认运行勾选、全套图标像素一致。

---

*检索受阻备注：ChatGPT 运行中队列官方文档缺失、Cursor 新文档入口未展开处均已在原文标注置信度；其余关键 claims 均有官方文档/源码级佐证。*
