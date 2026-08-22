# Aiming Cookie Desktop Design System

> **定位：前端视觉实现合同。** 页面骨架和交互关系看 [`frontend-uiux-design.md`](frontend-uiux-design.md)，视觉意图和语义色板看 [`../DESIGN-cursor.md`](../DESIGN-cursor.md)。本文规定前端如何把这些决策实现成 token、主题和组件。

## 1. Authority and current state

设计职责顺序：

1. `frontend-uiux-design.md`：产品骨架、IA 与交互；
2. `DESIGN-cursor.md`：视觉方向、语义角色与 palette；
3. 本文：token/theme/component 的实现规则；
4. 当前前端代码：实际可执行状态。

原 `webapp/frontend/app/globals.css` 与 History / Run / Evidence prototype 已在 frontend reconstruction Task 1 删除。`webapp/frontend/ui/tokens.ts` 是唯一被批准的 executable token 入口；它必须与 `DESIGN-cursor.md` 的 palette 一致，并由主题合同测试和截图验证。不得恢复旧样式作为新的视觉基础。

Mockup、Stitch、根目录 `DESIGN.md`、设计 HTML 和 style pack 都只是参考，不是 token 或组件事实源。

## 2. Semantic-token contract

组件只能消费语义角色，不能直接消费 palette 字面值或按页面发明颜色。完整角色和值由 `DESIGN-cursor.md` 维护；实现至少覆盖：

- surfaces/text：background、surface ladder、on-surface、outline；
- action：primary、secondary 及其 container/on-color；
- information/status：tertiary、error；
- analysis events：kill、miss、corrective、peak；
- inverse/fixed roles（仅在确有组件需求时实现）。

规则：

- light/dark 必须提供相同 token 集；
- 组件不得出现 raw hex/RGB/HSL，也不得用 theme 分支选择组件结构；
- 新视觉需求先判断是否已有语义角色；确需新增时，先更新 `DESIGN-cursor.md` 的含义和两套值，再更新 executable token；
- 不为单个组件创建没有复用语义的 token；
- 图表和视频标注也使用事件语义角色，不能另建页面私有 palette。

## 3. Theme contract

用户设置只允许：`system`、`light`、`dark`。

- 默认 `system`，跟随 `prefers-color-scheme` 并在系统变化时实时更新；
- 显式 light/dark 不跟随系统；
- preference 只保存在本地 UI 存储，不进入 Analysis、Provider auth 或 Coach payload；
- 首屏在 hydration 前解析主题，避免闪烁；
- 根级 controller 负责系统同步，设置页只修改 preference；
- `webapp/frontend/ui/tokens.ts`、`webapp/frontend/ui/theme-core.ts` 和 `webapp/frontend/ui/theme.tsx` 的模块路径、storage key 与 hydration 行为均由主题合同测试冻结；不能从已删除代码默认为长期合同。

## 4. Shared foundations

- **Typography:** UI 使用 Inter + 中文系统 fallback；数据/时间使用 JetBrains Mono；展示字体使用 Outfit + 中文 fallback。可执行字号为 `--text-micro` 到 `--text-display`，定义在 `tokens.ts` 的 `SCALE_TOKENS`。
- **Spacing:** 工作区控制区留出清晰呼吸空间；密集数据只在图表/表格内部压缩。可执行间距为 `--space-1`（4px）到 `--space-6`（32px）；页面布局级大间距（≥40px）不受该阶梯约束。
- **Geometry:** 紧凑、精密、机械感的圆角；避免大面积消费级胶囊化。可执行圆角为 `--radius-sm` / `--radius-md` / `--radius-lg`；控件高度为 `--control-height`（36px）与 `--control-height-compact`（32px）。页面不得再发明 5px 圆角或 13.5px 字号；1-3px 微形状（圆点、进度条端头）不算违规。
- **Primary scarcity:** `--primary` 只用于真正的 CTA（发送、新建、继续）和 `:focus-visible`。选中态、hover、badge、工具进行中不得用橙色填充或描边；数据可视化与表单 `accent-color` 不受此限。
- **Enforcement:** 字号/圆角 token、旧 board 别名层（`--fg`、`--s-high` 等）的废除、transition 动效 token、四级字重与阴影 token 由 `webapp/frontend/tests/design-system-contract.test.ts` 扫描全部 CSS 强制执行。
- **Depth:** 依赖 surface ladder 与 hairline，不使用装饰性重阴影。浮层（菜单/抽屉/对话框/弹层）唯一投影 `--shadow-overlay`；聚焦光环 `--ring`；1px hairline ring 只用于 focus-within 边框强调。
- **Motion:** 克制、可中断、尊重 reduced motion。transition 时长只有 `--duration-fast`（150ms，状态/hover）、`--duration-surface`（200ms，开闭/进出）与 `--duration-reduced-motion`（120ms，reduced 覆盖），缓动一律 `--ease-out`（大位移抽屉可用 `--ease-drawer`）；裸写 ms/ease/cubic-bezier 禁止。`animation:` 循环（加载/呼吸/光标闪烁）与关键帧时长不受此约束。处理态动画不让动画成为状态的唯一表达。
- **Status color semantics:** 信息/模式/进行中 = `tertiary-container` 系；预览/降级 = 中性 `surface-container` + `on-surface-variant`；成功 = `event-kill` 文字（透明底+hairline 边）；危险 = `error` 系；`event-*` 只用于数据可视化，不做 chrome 色；身份/选中/hover 一律 surface 阶梯。链接色为中性 `on-surface`。
- **Button patterns:** 交互按钮统一走 ac-button 形态——高度/圆角/字重用 token；hover：填充变体（primary/danger）`color-mix 90% 暗化`，ghost/默认升一档 surface；active `translateY(1px)`；disabled `opacity: 0.55`；`:focus-visible` 2px primary outline（光环 `--ring`）。
- **Border semantics:** 分界线与面板边 = 1px `outline-variant`；强调/选中边框 = `outline`；边框不承担 hover 强调（hover 走 surface 阶梯）。
- **Accessibility:** 正文、状态、图表标注、focus 和 disabled 状态在两种主题下都需可读；颜色不能作为唯一信息载体。

## 5. Component governance

- 基础 primitives 先于页面视觉拼装建立；
- 页面只能组合语义组件和 token，不复制私有按钮/卡片样式；
- Coach 侧栏、主工作区和系统级导航共享同一视觉语言，但职责和层级由 UI/UX 文档决定；
- 训练 Run、证据来源、Raw Input 授权/采集状态和 source unavailable 使用统一语义状态组件；状态必须同时有文字或图标，不得只依赖颜色；
- input-native、multimodal、video-fallback 是 Coach 自动选择的能力/证据等级，不是用户可切换的模式或装饰性标签；组件应使用一致的 badge、notice、warning 和 disabled 语义；
- 营销页面不自动继承 Desktop app 的信息密度和组件合同；
- 旧组件、当前 prototype 或截图只能帮助识别 capability 和状态，不能因为“已有实现”就覆盖新合同；
- 页面只能消费 `webapp/frontend/ui/tokens.ts` 定义的唯一 executable token 入口。

## 6. Review gate

每次修改 executable token 层，至少验证：

1. token 集在 light/dark 完整且无 raw color 泄漏；
2. System 首次启动、系统实时变化、显式 Light/Dark 固定三条路径；
3. 训练来源选择、Run/分析 History、processing、分析工作区、Coach 侧栏、Raw Input 设置和主要错误/空状态；
4. 文本、outline、事件色、primary action、focus/disabled 对比；
5. 窄窗口 drawer、视频/图表和 reduced-motion；
6. screenshot review 与相关 frontend tests/build。

一次验证的具体结果写入 `PROGRESS.md` 或任务报告，不写入本合同。
