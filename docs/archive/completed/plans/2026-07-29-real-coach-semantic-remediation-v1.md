# Real Coach 语义修复实施计划

> **状态：Task 1 completed and field-verified on 2026-07-29。** 本计划修复 2026-07-29 全量 review 已复现的 Coach 产品路径问题；复用现有 Provider runtime、TeachingSession、Knowledge Registry、Training Plan 与确认流，不建立第二套路由、状态、知识或存储系统。

**目标：** 让普通 Coach 问答、基于 Analysis 的有限说明和进行中的带练流程各走正确的现有路径，并让候选解释与 Provider 表达既自然又不越过证据边界。

**实现边界：** Python 只决定当前 turn 是否需要现有 TeachingTurn，并从已有 issue 中提取可教候选；TypeScript 继续把 Provider 当作受约束的表达层。没有 grounded issue 时不制造问题；没有 TeachingTurn 时继续使用现有普通 Provider Coach。

**技术栈：** Python 3.9 / pytest、TypeScript / Node test runner、现有 Pi Coach runtime。

## Task 1 - 修复真实 Coach 语义与路由

### Allowed files

- `docs/superpowers/plans/2026-07-29-real-coach-semantic-remediation-v1.md`
- `docs/superpowers/plans/README.md`
- `webapp/backend/coach_agent_runs.py`
- `webapp/coach-runtime/src/teaching-policy.ts`
- `webapp/coach-runtime/src/turn.ts`，仅当现有可选 TeachingTurn 路径需要最小接线
- `webapp/tests/test_coach_agent_runs.py`
- `webapp/coach-runtime/test/teaching-policy.test.ts`
- `webapp/coach-runtime/test/turn-fake-stream.test.ts`

不得修改 DB/schema/routes、TeachingSession store、Training Plan/confirmation/command store、Knowledge/Scenario Registry、任何 analyzer、Switching、worker、frontend、`PROGRESS.md`、真实 Run/media、Provider credential 或 field DB。

### Tests first

1. 用真实 Static `Analysis 22` 的 issue 形状证明 `physical` 限制语和 `symptom` 都不会成为教学候选；只有明确可验证的 `training` / `hypothesis` 候选可进入 TeachingTurn。
2. 证明零上下文、空闲 TeachingSession 的普通问题不携带 TeachingTurn，仍走现有 Provider Coach；附加 Analysis 但没有 grounded issue 时返回有限说明；已经开始的 TeachingSession 即使本轮未重新附加 Analysis，也继续原状态机。
3. 证明用户否定当前候选、使用同义描述或同时提到多个方向时，系统不会静默保留原方向并推进到教学；只有唯一识别的现有候选才允许重排。
4. 证明 Provider 可用自然中文改写 observation、candidate、cue 和 discriminator question，但不得遗漏必需语义、改变数值/单位、增加原因、剂量、问题、内部字段或完成声明。
5. 证明多 Analysis 时显式 issue 选择仍优先；没有显式选择时，只使用现有 projection 中可验证的优先级、Registry/场景可执行性和上下文顺序做稳定选择，不以文本关键词或 Provider 判断排序。

### Minimal implementation

1. 增加一个本地 `TeachingTurn | None` 决策：当前有进行中的教学状态，或当前 context 中有 grounded lesson/no-lesson Analysis 时才附带现有 TeachingTurn；真正零上下文的空闲普通问答传 `None` 给现有 runtime，不 claim TeachingSession。
2. 候选提取改为明确 allowlist；不把“不能据此证明身体原因”等限制语变成让用户选择的原因。
3. 候选解析只接受唯一、非否定的现有候选。否定当前方向但无法唯一映射到另一个现有候选时，保持 `hypothesize` 并让下一轮自然澄清，不能推进旧方向。
4. 用本地、受限、逐字段的语义归一化验证代替整句 `includes()`。数值、单位、因果、剂量、命令、问题数量与阶段仍保持现有严格校验；不引入模型二次判定或新的翻译/知识表。
5. 把 issue 选择改为一次收集后稳定排序，保留显式 `target_ref` 的最高优先级；不从单次微小 delta 推导新 issue，也不复制 analyzer 的 meaningful-change 规则。

### Frozen decisions

- `TeachingSession`、TeachingTurn contract、Registry、Training Plan store 与 Provider runtime 保持单一事实源。
- 普通问答不是另一套 Coach；它只是现有 runtime 在没有 TeachingTurn 时的既有路径。
- Analysis 附件存在但没有 grounded issue 时，Coach 可以解释已有结果和下一步可用操作，但不能从自由文本制造候选或处方。
- 用户的自然语言只可重排已有候选，不能新增或确认因果。
- 外设建议只在用户主动问起，或已有证据支持一个外设相关实验时出现；措辞使用自然中文，例如“现在没必要换鼠标”，不主动插入无关外设结论。
- MattyOW、Viscose 等社区教练资料只用于 cue、实验和调课语言，不升级为本地确定性诊断或外部 exact scenario 身份。
- 单次微小 delta 是否形成 analyzer issue 属于分析事实源；本 Task 不在 Coach 层复制阈值。该项必须在并发分析管线完成后，以独立 Task 修改原 analyzer/association 规则。

### Stop rule

- 修复需要新 phase/schema/store/route/tool、第二套知识/场景表、Provider authored state 或文本关键词诊断。
- 普通问答绕过现有 owner、stop/retry、消息持久化或产品命令安全边界。
- 自然改写验证无法在确定性本地规则下保留数值、单位、因果、剂量和单问题边界。
- 安全实施需要修改并发中的 Analysis、Switching、Scenario、worker、真实 Run/media、field DB 或 `PROGRESS.md` 文件。
- 现有 CAS、幂等、确认、prepared-item exact equality、隐私投影或完整 Coach 测试回归。

### Verification

1. 运行 focused Python red/green tests，记录原始失败与最终通过。
2. 运行 focused Node red/green tests以及完整 Coach Node suite。
3. 运行受影响的 Python Coach/backend suite。
4. 使用 `%TEMP%` field DB 副本只读/隔离验证 Static 22、Dynamic 23、Tracking 24、Switching 25；不要求真实 Provider 网络成功才能判定本地语义正确。
5. 运行 Python compile、TypeScript check（若现有命令可用）和 scoped `git diff --check`。

### Task 1 closeout

- 空闲、零 Analysis 的普通问题现在把现有可选 `teaching_turn` 保持为 `None`，继续走既有 Provider Coach；“继续解释一下”不再因为包含“继续”被误判为带练。明确“开始带练”、附加 Analysis 或已有非 `intake` TeachingSession 仍走原状态机，stop/retry/message/tool 路径未分叉。
- issue 候选只接受明确的 `training` / `hypothesis`。Static field `Analysis 22` 的三条 `physical` 限制语不再进入候选；实际投影选择“单次制动 + 流体修正”。Dynamic `23`、Tracking `24`、Switching `25` 仍为 0 issue，不制造候选、cue 或计划项。
- 用户只有唯一、非否定地指向现有候选时才会重排。否定、同时提到多个方向、或用无法唯一映射的同义表达时，保留现有候选并回到一次 discriminator 澄清，不沿旧方向直接开始教学。
- 多 Analysis 先保留显式 issue 选择；无显式选择时一次收集后按 exact Registry/场景可执行性、Registry 可解释性、issue priority 和原上下文顺序稳定排序，不使用 Provider 或文本关键词决定问题。
- Provider 表达不再要求整句逐字复制。确定性 validator 按字段检查候选核心词、非重叠语义锚点和原 discriminator 中的选项；否定冲突、反转 cue、遗漏候选、改剂量、增加因果/问题/完成声明或内部信息仍回退到本地安全表达。
- MattyOW / Viscose 调研只影响实验与话术边界：一轮只验证一个 cue/变量；即时同条件、延迟同条件、近迁移和主游戏迁移分开；外设只在用户主动提出或有明确约束时进入。没有把社区术语、外部场景或通用时长写成确定性诊断。
- Focused red/green 最终为 Python `111 passed`、TypeScript policy `25 passed`。更宽 Python Coach/TeachingSession/Training Plan/Knowledge 为 `490 passed`；完整 Coach Node 为 `160 passed`。最终 Terra 只读交叉 review 未发现新的阻塞回归。
- field 验证只读取 `%TEMP%` 副本并在结束后送回收站；真实数据库、Run/media 和 Provider credential 未写。2026-07-29 后续只读验收使用已保存的 `deepseek-v4-flash` profile 与隔离 loopback sidecar：profile status 和显式连接测试均为 `ready`，真实无工具 Coach turn 精确返回 `CONNECTION_OK`，tool event 为 0；先前 HTTP 502/timeout 不再是当前 Provider 网络 E2E blocker。
- 未修改 analyzer、Switching、Scenario Registry/Manifest、worker、DB/schema/routes、Training Plan store、frontend 或 `PROGRESS.md`。单次微小 delta 的 meaningful-change/repeated-evidence policy 仍属于并发 Analysis 事实源，不在 Coach 层复制阈值。
