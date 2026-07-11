# Aiming Cookie — 前端设计系统

> **状态**：P1.5 的共同约束。它不要求在当前阶段一次性建设完整组件库；目标是防止新旧页面继续各自定义视觉语言。

## 1. 目标与边界

设计系统服务于产品路线，而不是另开一个视觉探索项目：

- P0/P1 的页面先获得清楚、可用、一致的状态与流程；
- P2.5 再在同一体系内完成 UIUX 收敛与视觉打磨；
- 不删除现有功能，不以设计重做阻塞 flicking Alpha 闭环；
- 不为尚未使用的场景预造大型组件库。

## 2. 事实源与设计资产

| 类型 | 位置 | 规则 |
|---|---|---|
| **可执行视觉 token（唯一事实源）** | `webapp/frontend/app/globals.css` | 颜色、排版、spacing、radius、容器与运行时主题值在这里维护 |
| **前端基础组件** | `webapp/frontend/components/ui/` | 仅创建已有页面/近期计划实际需要的组件；组件消费 token，不定义另一套主题 |
| **图表 token** | `webapp/frontend/lib/design-tokens.ts`（P1.5 创建） | Plotly 等 TypeScript 侧需要复用的状态色、序列色从这里导出，并与 CSS token 对齐 |
| **设计规则** | 本文档 | 解释命名、边界、迁移顺序；不复制一份可执行色值 |
| **视觉参考** | `design/`、根目录 `DESIGN.md`、Stitch HTML、mockup | 可用于判断方向和页面构图；不能绕过运行时 token 成为新的实现事实源 |

若参考稿与 `globals.css` 或本文冲突，先更新设计系统决策，再实施页面；不要在单页内打补丁。

## 3. Token 规则

### 使用语义，不使用临时视觉值

页面和组件必须优先使用既有语义 token，例如：

- surface：`background`、`surface`、`surface-container-*`
- text：`on-surface`、`on-surface-variant`
- action：`primary`、`on-primary`、`primary-container`
- feedback：`error`、`event-kill`、`event-miss`、`event-corrective`、`event-peak`
- layout：现有 `spacing-*`、`radius-*`、`text-*`、`container-*`

禁止在 JSX/CSS module 中新写 raw hex、临时 Tailwind 色板或无来源的 arbitrary color。动态数据的图表颜色也必须由统一导出的 slot 决定。

临时任意尺寸不是默认手段：先使用现有 spacing、type、radius 和 container scale；确实无法表达且有稳定语义时，补充 token 后再使用。

## 4. 组件边界

组件分两层：

1. **基础组件**：`Button`、`Card`、`Input`、`Badge`、`EmptyState`、`Toast`、`AppNavbar`。只在现有页面或已确认的近期计划需要时创建。
2. **业务组件**：上传表单、分析状态、报告指标卡、教练聊天、历史 session 列表等。它们组合基础组件和 token，不复制基础样式。

不要因为“设计系统”引入新的 UI 框架、Storybook 或一套未被产品使用的抽象层。

## 5. 实施顺序

### P1.5：最小系统

1. 审计现有四屏中 raw visual value 与重复样式；只记录，不顺手重绘。
2. 确认 `globals.css` 的 token 命名、补齐必要的图表 TypeScript token。
3. 先建设导航、按钮、卡片、输入、状态反馈需要的最小基础组件。
4. 新写的 History、通知、Settings、Login 必须直接消费这些 token/组件。

### P2.5：体验收敛

在 P0/P1 的用户闭环可用后，迁移 Upload、Processing、Report、Coach，并完成 History、Settings、Login 的状态、动效、响应式和可访问性收敛。此阶段沿用既定 Obsidian Hearth dark 方向，不重新开启独立视觉风格探索。

## 6. 完成判断

当下列条件满足，设计系统才算真正生效：

- 新页面没有自行定义颜色、字体、阴影和圆角体系；
- AppNavbar、状态反馈和核心表单/卡片不再各有一套样式；
- 图表和页面对成功、告警、失败事件使用相同的语义色；
- 视觉参考稿与运行时实现发生冲突时，有明确的单一决策入口。
