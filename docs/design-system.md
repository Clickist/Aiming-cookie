# Aiming Cookie Desktop Design System

> **定位：前端视觉实现合同。** 页面骨架和交互关系看 [`frontend-uiux-design.md`](frontend-uiux-design.md)，视觉意图和语义色板看 [`../DESIGN-cursor.md`](../DESIGN-cursor.md)。本文规定前端如何把这些决策实现成 token、主题和组件。

## 1. Authority and current state

设计职责顺序：

1. `frontend-uiux-design.md`：产品骨架、IA 与交互；
2. `DESIGN-cursor.md`：视觉方向、语义角色与 palette；
3. 本文：token/theme/component 的实现规则；
4. 当前前端代码：实际可执行状态。

原 `webapp/frontend/app/globals.css` 与 History / Run / Evidence prototype 已在 frontend reconstruction Task 1 删除。当前尚无被批准的 executable token 入口，也不得恢复旧样式作为新前端视觉基础。重建计划建立新的 token 模块后，应把实际路径补到本节，并以代码、主题测试与截图验证。

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
- preference 只保存在本地 UI 存储，不进入账号、分析数据或 auth；
- 首屏在 hydration 前解析主题，避免闪烁；
- 根级 controller 负责系统同步，设置页只修改 preference；
- 新实现选择 storage key 和模块路径时，应通过测试冻结，不能从已删除代码默认为长期合同。

## 4. Shared foundations

- **Typography:** UI 使用 Inter + 中文系统 fallback；数据/时间使用 JetBrains Mono；展示字体使用 Outfit + 中文 fallback。
- **Spacing:** 工作区控制区留出清晰呼吸空间；密集数据只在图表/表格内部压缩。
- **Geometry:** 紧凑、精密、机械感的圆角；避免大面积消费级胶囊化。
- **Depth:** 依赖 surface ladder 与 hairline，不使用装饰性重阴影。
- **Motion:** 克制、可中断、尊重 reduced motion；处理态可使用 primary 派生 pulse，但不让动画成为状态的唯一表达。
- **Accessibility:** 正文、状态、图表标注、focus 和 disabled 状态在两种主题下都需可读；颜色不能作为唯一信息载体。

## 5. Component governance

- 基础 primitives 先于页面视觉拼装建立；
- 页面只能组合语义组件和 token，不复制私有按钮/卡片样式；
- Coach 侧栏、主工作区和系统级导航共享同一视觉语言，但职责和层级由 UI/UX 文档决定；
- 训练 Run、证据来源、Raw Input 授权/采集状态和 source unavailable 使用统一语义状态组件；状态必须同时有文字或图标，不得只依赖颜色；
- input-native、multimodal、video-fallback 是能力/证据状态，不是装饰性标签；组件应使用一致的 badge、notice、warning 和 disabled 语义；
- 营销页面不自动继承 Desktop app 的信息密度和组件合同；
- 旧组件、当前 prototype 或截图只能帮助识别 capability 和状态，不能因为“已有实现”就覆盖新合同；
- frontend reconstruction 的第一项视觉任务必须建立唯一 executable token 入口，再允许页面拼装。

## 6. Review gate

新 executable token 层建立后，至少验证：

1. token 集在 light/dark 完整且无 raw color 泄漏；
2. System 首次启动、系统实时变化、显式 Light/Dark 固定三条路径；
3. 训练来源选择、Run/分析 History、processing、分析工作区、Coach 侧栏、Raw Input 设置和主要错误/空状态；
4. 文本、outline、事件色、primary action、focus/disabled 对比；
5. 窄窗口 drawer、视频/图表和 reduced-motion；
6. screenshot review 与相关 frontend tests/build。

一次验证的具体结果写入 `PROGRESS.md` 或任务报告，不写入本合同。
