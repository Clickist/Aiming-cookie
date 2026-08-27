# Coach 引用回复（quote-reply）实现调研

> **调研草稿（2026-08-27），供点点拍板，非实施合同。**
>
> 范围：为「划选 Coach 发言引用到用户输入框」功能做外部产品对照与本地落地点侦查。
> 纪律声明：本文只读调研产出，未改动任何代码或既有文档；工作区中批 7（frontend-parity 富文本）施工中的未提交文件仅作只读观察，方向推断不构成评判。

---

## 1. 外部对照

### 1.1 对照表

| 产品 | 触发方式 | 浮层形态 | composer 内形态 | 多段并存 | 进上下文形态 | 已发送消息回显 |
|---|---|---|---|---|---|---|
| **ChatGPT 网页** | 划选回复文字 → 出现 quote 按钮，点击进输入框；无键盘快捷键（社区求快捷键未果） | 划选旁小按钮 | 引文插入输入框上方区域 | 未见官方支持说明 | 引文随消息文本进入上下文 | 公开资料未明确 |
| **OpenAI Codex 桌面端** | 划选先前消息/渲染内容即附加为上下文，无需点按钮 | 无独立浮层（选中即附加） | composer 分区呈现 `# Selected text:` 与请求正文两个区块 | 有 per-selection follow-up 的开放请求（#22677），当前基本单选区 | 选区文本保持 model-visible；changelog 称「selected-text references remain available after sending」 | **不回显**——发送后历史只剩用户输入，被报为 bug #22670「auditability gap」，至今 open |
| **Claude 网页 / 桌面端** | 无原生功能。claude-code 仓库有两条 open/closed feature request（#26716、#58691）明确索要本功能，佐证官方未发布 | — | — | #26716 明确要求「多段共存在一条 prompt 里」＋来源可追溯 | — | — |
| **Cursor** | 编辑器内划选代码 → selection popup「Add to Chat / Add to Composer」（带快捷键）；chat 内划选无 quote-reply | selection popup | 作为上下文 chip（@code snippet 形态）；直接粘贴长代码也会被转成 chip（社区抱怨失去纯文本形态） | 支持（多 chip 并存） | 以结构化上下文引用（非 markdown blockquote）送入模型 | 以 chip 形态回显在已发消息中 |
| **TypingMind（第三方网页客户端）** | 划选 AI 回复 → 菜单项「Quote」 | 菜单项 | 引文进输入框供追问 | — | — | — |
| **assistant-ui（开源 React 聊天组件库，最有价值的实现参照）** | 划选 assistant message 内容 → floating toolbar 带 Quote 按钮 | toolbar 只当选区**完整落在单个 message part 内**才出现；滚动时隐藏；快照于创建时固化，不与源消息保持链接 | `composer.setQuote()` 存到 composer 状态；**设计上一次只有一条，新引替换旧引**；quote 不影响 composer 的 isEmpty | 不支持多条（有意为之；与我们需求相反） | 三种后端适配可选：①默认 `injectQuoteContext` 把引文以 **markdown blockquote 前置**到消息前；②OpenAI 适配注入结构化前缀 `[Referring to: "<quote text>"]`；③Claude SDK 用带 citation 标注的独立 text block | quote 存 metadata（`message.metadata.custom.quote`），回显由消费方决定 |

### 1.2 各家「进上下文方式」的结论

1. **没有任何一家改造 user content 的数据结构**。全部做法是把引文以某种纯文本形态拼进 user turn 的字符串里（blockquote / bracket 前缀），或最多放进 metadata 由后端在发送时再拼装。差异只在拼装的「包装格式」，不在「是否结构化」。
2. 包装格式分三派：
   - **markdown blockquote 前置**（assistant-ui 默认）：`> 引文行` 逐行加 `>`；
   - **方括号结构化前缀**（Codex 桌面端 `# Selected text:`、assistant-ui OpenAI 适配 `[Referring to: "…"]`）：头部标明语义区块 + 正文分区；
   - **citation 结构化块**（assistant-ui Claude SDK 适配）：依赖特定 Provider API 能力，对本地多 Provider 场景不可移植。
3. **回显是真实痛点，不是锦上添花**：Codex 桌面端正是因「发送后看不到选了什么」开了长期 bug（#22670）。我们的需求第 5 条隐含自包含、则回显应当做。
4. ChatGPT/Codex 都没有 keyboard-only 触发路径（或尚未公开），划选→浮层按钮是公认主路径。

对本仓库的含义：**走「纯文本自包含拼装」路线即可对齐行业主流并满足零迁移约束**；格式采用下文 §3.4 的推荐。

---

## 2. 交互规格细化草案

以下行为精确化点点已确认的六条需求，作为后续实施的默认值；每条标注存疑处以待拍板。

### 2.1 划选判定与浮层出现

- 监听点：CoachPanel 已持有消息滚动容器 ref（`messagesRef`，`<section aria-label="Coach 消息">`）。在其上挂 `mouseup`（主判定）＋ `document.selectionchange`（收起清理）即可，不必全局监听。
- 合格选区的充要条件（建议做成 lib 纯函数，注入 DOM 探测参数以便 node:test 覆盖）：
  1. 选区未折叠（`range.collapsed === false`），`selection.toString().trim()` 非空；
  2. anchorNode 与 focusNode 同属**同一个** `article.task6-message[data-role="assistant"]` 元素（用 `closest()` 从两端分别向上找，找到且是同一节点）;
  3. 该 article 不是流式消息（见 2.5）。
- 用户自己的消息气泡、训练卡、工具步骤、讨论条上的划选一律不触发浮层。
- 浮层形态：单一动作「引用」的小操作条。视觉配方复用 `.task6-send-menu/.task6-mention-menu` 家族（`--outline-variant` 边框、`--surface-container-high` 底、`--radius-lg`、紧凑 padding）；出现/消失走 `useAnimatedPresence`（批 1 收敛件）150–180ms opacity+translateY(±4px)。
- 定位：以 `range.getBoundingClientRect()` 取视口坐标，**fixed 定位**显示在选区矩形正上方 8px；水平方向钳制在消息面板宽度内避免出窗。理由：比 absolute 更省换算，且桌面 App 无页面级滚动干扰。
- 关闭时机：点击别处 / 选区折叠 / 消息列表滚动 / Esc / 点击「引用」。滚动即隐藏（assistant-ui 同款选择），不做跟随重算——隐藏后用户重新划选成本极低。
- 既有原语核对：批 1 收敛后的 `ui/primitives.tsx` 提供 Button/IconButton/Badge/Status/Notice/Tabs/useAnimatedPresence/Drawer/Toast/Dialog 等，**没有 Popover primitive**。send-menu 与 mention-menu 都是 CoachPanel 本地手搓的绝对定位浮层，本功能的 selection toolbar 沿用同样的本地组件做法即可，不必新增全局 primitive（避免过度抽象）。

### 2.2 点「引用」

- 动作顺序：快照 `selection.toString()`（浏览器已完成富文本→纯文本降维；天然拿到的是**归一化展示链路渲染出来的文字**，即用户所见即所得）→ 白名单校验/长度钳制（§3.4）→ 折叠原选区 → 关浮层 → 向 quotes 数组追加 `{ id, text }` → 焦点移入 textarea（沿用 `requestAnimationFrame(() => textareaRef.current?.focus())` 的既有惯例）。
- 快照取的是 DOM 渲染文本而非 sidecar 存储的模型原始输出。两者因批 7 白名单归一化而不同（原始输出含 fence/HTML 注释等被剥除内容）。这是期望性质：引用所见即所引；需在测试合同中锁定该认知。

### 2.3 composer 内引用块

- 位置：textarea 上方区域（与 queuedChips、editing-banner 同一插槽层级），自成一组引用块列表。
- 单块结构：左侧竖线来源标注「引用 Coach」＋ 文本体 ＋ 右侧整块删除 IconButton（IconClose，compact）。文本体是普通只读呈现元素（非 input/contentEditable），天然满足「不可编辑」；建议 `max-height` 约 5 行 + `overflow-y:auto`，超长块内部滚动，`title` 属性悬停看全文。
- 多段并存：数组按加入顺序排列，逐条删除，不提供合并/拖拽排序（最小实现）。
- 引用块不影响既有 Enter 发送逻辑（它们不在 textarea 字符串里）；composer 的「空」判定不变（quote-only 不发送的需求不存在——点点未要求，保持现状：draft 为空但存在引用块时是否允许发送？**存疑点 A，建议禁止并发 Toast 提示，防止发出无正文的孤引**）。

### 2.4 与既有 composer 编排的共存折衷

三处既有路径会把「最终拼装串」当纯字符串搬运，引用块语义会在这些位置退化为纯文本：

1. **运行中队列 chips**：busy 时 sendText 把 content 整体转入 chip；chip 回填编辑会把引文解锁成可编辑文本。
2. **编辑重发**：把历史 user content 原样放回 textarea，同样混入纯文本。
3. **↑ 发送历史**：pushSentHistory 存最终串，回填亦含引文（行为一致，无需处理）。

处理建议（MVP）：接受 1、2 的折衷并注明——彻底方案需要把 QueuedChip/Draft 全程结构化，改动面大，性价比低。**是否接受该折衷＝存疑点 B。**

### 2.5 流式禁划

- 流式消息在 CoachPanel 是独立渲染分支（`run.partial_text` 的 article，不在 `messages` 数组内）。给它加 `data-streaming="true"` 属性，§2.1 条件 3 即「article 无 data-streaming」。
- 不用 CSS `user-select: none` 强禁：保留复制自由（Cmd+C 在流式中仍可用），只在浮层判定层拦截。这比强禁更友好且实现更少。
- 失败/停止终态的消息同属独立分支且不渲染 partial article，天然不触发，无需额外处理。

### 2.6 与批 5 @ 引用的关系

@ 引用 token 是 textarea 内联文本（`analysis:3` 等），经 mention 下拉落词，随 draft 字符串走发送管线；划选引用是 composer 外挂结构块，发送时才拼装。二者在不同层面，互不改写对方字符，自然并存、不合并。唯一交集：发送拼装时正文里的 @token 原样保留在 text 部分，不受影响。

---

## 3. 数据流设计

### 3.1 全链路

```
mouseup(消息容器)
  └─ evaluateSelection(range, containerEl)          [lib/quote.ts 纯逻辑＋DOM探针]
       ├─ null → 关浮层
       └─ { article, range } → 显示 SelectionToolbar(fixed, rect)
            │
        点击「引用」
            └─ snapshotQuote(selection.toString())     [lib/quote.ts：trim＋长度钳制]
                 └─ setQuotes([...quotes, {id, text}])  [CoachPanel state]
                      └─ QuoteBlocks 渲染（textarea 上方）
                           │
                       submitComposer / sendText
                            └─ composeQuotedContent({quotes, text})  [lib/quote.ts]
                                 = 序列化引文段落 + "\n\n" + text
                                 ├─ 预算校验（§3.4），超限 notify 拒发
                                 └─ createCoachAgentRun(finalString)    [现有 API 零改动]
                                      └─ sidecar agent-runs：content 存 JSONL（原始字符串，
                                         appendUserMessageOnce）＋ 进模型 messages[]
                                          └─ 成功：setDraft("") && setQuotes([])
                                             失败：catch 分支已有草稿回填，quotes 未动仍在 composer
```

### 3.2 draft 与持久化

- 引用块**不进** textarea 字符串（否则无法实现整块删除与文字锁定）。CoachPanel 新增独立 state `quotes: { id: number; text: string }[]`。
- 草稿持久化（三级键 localStorage）现只存一个字符串。兼容方案：存储值升级为 JSON envelope `{ v: 2, text, quotes }`，读取端遇到非 `{` 开头的历史值按 legacy 纯文本处理。逻辑放 `lib/composer.ts` 扩展（读写各一小函数），可全量 node:test 锁定。
- 最小替代：quotes 不持久化（会话内存），刷新丢失并在 UI 上可接受时可以砍掉 envelope 改造。**persist or not＝存疑点 C，建议 persist（成本低、防丢稿一致性好）。**

### 3.3 发送链路接入点

接入点只有一处：`sendText` 开头。`submitComposer`／四动作菜单的 steer/queue/interrupt-steer 最终都汇入 `sendText(content)` 或 `enqueueQueuedItem(content)`。建议在 `sendText` 入口统一先 `composeQuotedContent` 再分流，保证 steer（运行中转向注入）发出的消息同样携带引文；chip 化分支携带的也是已拼装完整串（§2.4 折衷）。

### 3.4 进上下文的文本形态（推荐与理由）

推荐格式：

```
[引用 Coach]
> 第一段引文第一行
> 第二行…

[引用 Coach]
> 另一段引文…

（用户正文）
```

理由：

1. **`>` blockquote 是模型的母语标记**：大量 markdown 对话语料让「逐行 > 前缀」天然指向「这是别人的话」，边界清晰不易与正文混淆；
2. **`[引用 Coach]` 头部补足来源语义**：blockquote 本身不指明谁说的，header 一行写明来自 Coach 的历史回复，消除归属歧义（对齐 Codex `# Selected text:` 与 assistant-ui `[Referring to: …]` 两家的共同思路）；刻意不用 `#` 标题做 header——批 7 归一化白名单剥除标题记号，用 `#` 徒增下游混淆风险；
3. **纯文本 ⇒ 零迁移成立**：sidecar 的 user content 就是纯字符串（`CoachThreadMessageOut.content: string`；`createAgentRun` 收 string 后 `slice(0, 12_000)` 存 JSONL），任何格式都能无损落盘、无损回放历史，不需要动合同、类型生成和读取链。metadata/citation 结构化路线需要动 sidecar 合同＋前端类型生成＋session-repo 读取，违背零迁移前提，排除；
4. **前端构造器与解析器共享同一常量**（`QUOTE_HEADER` 等），`composeQuotedContent` 与 `parseQuotedContent` 做 round-trip 单测锁定，杜绝构造/解析漂移。

长度预算（必须做）：sidecar 对 content **静默**执行 `slice(0, 12_000)` 且从尾部切——引文前置会把过长内容的**正文尾巴**切掉，属隐性丢内容。建议每条引文 ≤ 2000 字符（超长拒收并提示）、引文总量 + 正文在发送前校验合计 ≤ ~11000，超限 notify 明确告知而不是依赖 sidecar 静默切尾。

### 3.5 已发送消息回显

- 数据侧零成本：JSONL 存的就是拼装串，历史 GET 原样返回。
- 渲染侧：user 气泡目前直接 `{message.content}` 纯文本（`white-space: pre-wrap`），拼装串会长相朴素但信息完整（这正是 Codex 缺失、被开成 bug 的 auditability）。
- 推荐：user 气泡渲染前过一遍 `parseQuotedContent(content)`——命中前缀则引文部分渲染为引用块视觉（缩进竖线小字），正文正常呈现；未命中（legacy 消息/编辑过的 chip 文本）按原样渲染，向后兼容。视觉与 composer 内引用块共用样式类。

---

## 4. 文件级落点清单

| 文件 | 动作 | 内容 |
|---|---|---|
| `webapp/frontend/lib/quote.ts` | 新增 | 纯逻辑模块（无 React 依赖，node:test 直测）：`QUOTE_HEADER` 等常量、`snapshotQuote(raw)` 校验钳制、`composeQuotedContent({quotes,text})`、`parseQuotedContent(content)` round-trip、`evaluateSelection` 的可注入 DOM 探针版 |
| `webapp/frontend/lib/composer.test.ts` | 扩展 | draft 持久化 envelope v2 兼容读写用例（若采纳存疑点 C 的 persist 方案） |
| `webapp/frontend/components/task6/CoachPanel.tsx` | 修改 | ① quotes state ＋ SelectionToolbar 本地组件（fixed 定位）；② messagesRef 容器上的 mouseup/selectionchange effect；③ 流式 partial article 加 `data-streaming`；④ `sendText` 入口 composeQuotedContent 接入与预算 notify；⑤ 成功分支清 quotes；⑥ user 气泡回显解析分支；⑦ composer 引用块列表渲染（复用 IconButton/IconClose） |
| `webapp/frontend/components/task6/task6.css` | 扩展 | `.task6-quote-block`（竖线 `border-inline-start` ＋ 来源标注 ＋ 小字 pre-wrap 文本体）、`.task6-selection-toolbar`（复用 send-menu 视觉配方）。红线遵守见 §5 风险前 last note |
| `webapp/frontend/tests/quote-composer-contract.test.ts` | 新增 | 源码断言合同（风格仿 `coach-composer-orchestration.test.ts`，见 §5） |
| `webapp/frontend/e2e/*.spec.ts` | 可选扩展 | Playwright 划选冒烟（Chromium Selection API 可靠；desktop-matrix 已有多引擎基础设施可复用） |

不需要动的文件：`lib/api.ts`（内容仍是单个 string 参数）、sidecar 全部代码（agent-runs/turn/session-repo 零改动）、`types.ts`（无合同变化）、批 7 的 `CoachMessageText.tsx`/`rich-text.ts`（selection 判定只锚定 `article[data-role]` 容器，不依赖其内部产物；批 7 落地前后均成立）。

---

## 5. 测试策略

仓库现行的两层测试范式直接套用：

1. **纯逻辑 → `lib/*.test.ts`（node:test，无 React）**
   - `composeQuotedContent` ⇄ `parseQuotedContent` round-trip（含多段、空正文拒绝、引文内含 `\n`/`>` 字符转义安全性——引文原文若本身含行首 `>` 会破坏块状结构，compose 时必须做防御：或逐行剥离行首 `>`、或改用整段包裹符，测试先行暴露；
   - `snapshotQuote`：trim、拒空、2000 字符钳制；
   - 总量预算校验函数；
   - draft envelope v2 读写兼容 legacy 字符串。
2. **接线合同 → `tests/quote-composer-contract.test.ts`（源码断言，仿 coach-composer-orchestration / task6-source 风格）**
   - CoachPanel 确实在 `sendText` 前、`sendingRef` 置位逻辑之前消费 `composeQuotedContent`；
   - 流式 partial article 存在 `data-streaming` 且 selection effect 只认非 streaming 的 assistant article；
   - 引用块渲染于 textarea 上方插槽、删除按钮调用对应 remove；
   - 成功分支同时清空 draft 与 quotes、失败分支保留 quotes；
   - SelectionToolbar 关闭路径齐全（滚动/Esc/外点）。
3. **CSS 红线自动覆盖**：新增类自然落入既有扫描范围，无需新增红线测试，但要确认不会触发——task6.css 禁 hex/rgb/hsl 字面量、禁 `grid-template-rows`、六枚 parity token（含 `--shadow-menu`）只能在 theme.css 定义、组件层只能 var() 消费。引用块竖线颜色用 `var(--outline-variant)` 或 `var(--divider-strong)`，底色 `var(--surface-container)` 系，阴影如需用 `var(--shadow-menu)`（task6.css 现状偏好 shadow:none/描边，引入实阴影与否由点点定夺）。
4. **e2e（可选）**：一条 Chromium 冒烟——mock 流式回复 → 划选 → 点引用 → composer 断言引用块 → 发送 → 断言乐观 user 气泡与 createCoachAgentRun payload 含序列化引文。

---

## 6. 风险与未知

1. **Tauri/WKWebView 的 Selection 怪癖（macOS 主运行时）**：WebKit 的 `selectionchange` 触发时机与 Chromium 不同（长按、双击词选择、跨节点选择的行为差异均有报告史）；`Range.getClientRects` 在折叠边界可能返回空集合。缓解：主判定用 `mouseup`（此时选区已稳定），selectionchange 只做收起清理；竖线引用块不依赖 Range rects（只用一次 boundingRect）。桌面 App 固定视口无 pinch-zoom，visualViewport 漂移风险低。
2. **跨 `@time` link 按钮的划选**：assistant 段内的 `<button class="task6-time-link">` 是内联交互元素，跨它选择的 `toString()` 拼接行为 WebKit 与 Blink 可能有空格差异；结果只影响引文文本的标点间距，功能不坏，但要有 e2e/手动检查点。
3. **批 7 施工等待点**：`turn.ts`（normalize 白名单重写）、`rich-text.ts`（新受限解析器）与 `CoachMessageText.tsx` 的接线尚在工作区未提交状态。本方案与其正交（selection 只锚容器不锚内部结构），但**实施排期应在批 7 合入之后**，避免对同一批文件的双头改动冲突。
4. **sidecar 静默截断**：`createAgentRun` 的 `content.trim().slice(0, 12_000)` 从尾切，配合引文前置会把正文尾部无声吞掉——预.send 预算校验从「nice to have」升格为必做（§3.4）。
5. **引文原文含 markdown 语义字符**：引文里若本身有行首 `>`、表格竖线、`**` 等，拼装后在模型侧仍无害（模型理解力足够），但会影响 §3.5 的回显解析精度——parseQuotedContent 应只认 `[引用 Coach]\n> …` 的固定开头形状，其余当正文，宁可保守错杀。
6. **chips / 编辑重发的语义退化**（§2.4，存疑点 B）：引用块在那两条路径解锁为纯文本。彻底解法改动大，建议 MVP 接受并文档注明。
7. **quote-only 发送**（存疑点 A）：draft 为空仅引用块时能否发送需拍板；建议禁止。
8. **quotes 是否持久化**（存疑点 C）：建议 envelope v2 持久化；不持久化的最小方案也可行，代价是刷新丢引用。
9. **shadow 使用倾向**：task6.css 现有浮层家族（send-menu/mention-menu/unread-prompt）都没有实阴影（unread-prompt 显式 `box-shadow: none`）。SelectionToolbar 是否破例用 `var(--shadow-menu)` 是视觉一致性微决策，交点点。

### 外部资料索引

- Codex 桌面端 selected-context 不回显 bug：<https://github.com/openai/codex/issues/22670>
- Codex 桌面端 per-selection follow-up 请求：<https://github.com/openai/codex/issues/22677>
- claude-code 引用 UI feature request（多段引用诉求）：<https://github.com/anthropics/claude-code/issues/26716> ；右键 reply 诉求：<https://github.com/anthropics/claude-code/issues/58691>
- assistant-ui quoting 指南（浮动 Quote 按钮 / setQuote / 三种后端注入形态）：<https://www.assistant-ui.com/docs/guides/quoting>
- ChatGPT 网页 quote 功能讨论（无快捷键、MacOS 端缺失）：<https://community.openai.com/t/quote-feature-from-highlighted-text-keyboard-shortcut/1129453> 、<https://community.openai.com/t/quoting-feature-in-macos-app/763816>
- Cursor Add-to-Chat 讨论与 chip 化抱怨：<https://forum.cursor.com/t/automatically-add-the-selected-text-lines-as-context-in-the-ai-chat/16705> 、<https://www.reddit.com/r/cursor/comments/1hj6qxm/>
