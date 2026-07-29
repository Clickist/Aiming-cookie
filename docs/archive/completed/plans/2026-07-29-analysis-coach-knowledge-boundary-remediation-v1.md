# Analysis / Coach 知识边界修复实施计划

> **状态：Completed；点点已于 2026-07-29 授权 Task 1-5。** Task 5 的无校准 policy 与 metric-ref version 合同 blocker 已记录；后续工作必须另立并授权新的 contract migration Task。当前 dirty worktree 中的既有改动必须保留；不提交、不推送。

**目标：** 退役 Analysis 的旧 Provider narration 副作用；让 Analysis 只生产确定性 observation/candidate projection，让 versioned Knowledge Registry 成为完整教育与处方正文的单一事实源；保留 Provider Coach 自然表达；以中性 UI 展示证据边界；只在现有 Analysis/history 证据支持时补复测 meaningful-change policy。

**成功标准：** 新 v2 Analysis 不再请求 Provider 或写 narration；v1/unversioned 历史仍可读；Registry v4 支持 explanation-only 等能力且 v3 精确兼容；新 Analysis 使用稳定 refs；Coach exact-ref first 且 legacy 文本只能展示不能写计划；UI 不再把候选解释写成已知根因；复测不发明阈值。

**不在范围：** 新 Coach/store/route、Provider authored state、删除历史字段、重写 TeachingSession/Training Plan/confirmation、把社区文章升级为 analyzer rule、通用外部 scenario identity、自动改设置、提交或推送。

## Task 1 - 退役活跃 Analysis narration

### Allowed files

- `webapp/backend/worker.py`
- `webapp/tests/test_worker.py`
- `docs/ARCHITECTURE.md`
- `docs/PROGRESS.md`
- `docs/coach-community-frontier.md`
- 本计划与 `docs/superpowers/plans/README.md`

### Tests first

1. 新 video fallback v2 完成时不加载 selected Provider、不调用 narration backend、结果不含 `narration`。
2. 旧 v1/unversioned 带 `narration` 的结果仍能通过现有读取/适配合同。
3. deterministic diagnosis、figures、source provenance 和 queue terminal semantics 不变。

### Minimal implementation

删除 worker 完成路径中的 Provider 加载和 `run_report(summary, backend)`，保留本地 report/diagnosis/figures。更新架构、进展与社区文档中的活跃 narrator 描述；不删除旧模块。

### Frozen decisions

- 无 Provider 时仍有本地 Analysis、History、指标和规则化建议，但没有 Coach。
- `kovaak_tracker/coach/report.py` 仍是 deterministic report 依赖；旧 Python runtime 仍可能由 `COACH_RUNTIME=python` fallback 使用。
- 读取合同继续允许历史 narration；本 Task 只停止新生产。

### Stop rule

如果切断 Provider 需要改变 queue/schema/route、删除历史字段、改旧 adapter，或会破坏非 video-fallback runtime，则停止并上报。

### Verification

focused worker pytest、相关 contract/history tests、Python compile、scoped `git diff --check`。

### Task 1 closeout

- video-fallback worker 不再读取 selected Provider profile、加载 backend 或发起 narration 请求；`run_report(summary)` 固定以 `backend=None` 构建 deterministic report。
- 新 v2 结果保留 `narration: {status: "not_requested", text: null}` 兼容 envelope；v1/unversioned history 读取合同未改。
- 未修改 queue/schema/route、`kovaak_tracker/coach/report.py`、Coach runtime、Provider profile/credential、TeachingSession 或 frontend adapter。
- Tests first 的四个预期失败已复现；最终 `webapp/tests/test_worker.py` 为 `86 passed`，worker + contracts + routes 为 `152 passed`；Python compile 与 scoped `git diff --check` 通过。

## Task 2 - 建立 versioned Registry v4

### Allowed files

- `knowledge/coach/schema.v3.json`
- `knowledge/coach/schema.v2.json`，仅补齐现有 Python / TypeScript 已接受的旧 enum parity
- `knowledge/coach/registry.v4.json`
- `knowledge/coach/registry.v3.json`，仅在测试证明当前文件格式错误且不得改变既有 entry 语义时
- `knowledge/coach/migrations/2026-07-29-v3-to-v4-audit.json`
- `kovaak_tracker/coach/knowledge_registry.py`
- `webapp/coach-runtime/src/knowledge-registry.ts`
- `webapp/backend/coach_runtime.py`
- `tests/coach/test_knowledge_registry.py`
- `webapp/coach-runtime/test/knowledge-registry.test.ts`
- `webapp/tests/test_coach_runtime.py`
- `docs/ARCHITECTURE.md`
- `docs/PROGRESS.md`
- `docs/coach-community-frontier.md`
- 本计划与索引

### Tests first

1. v1/v2/v3 继续按原 version/schema 精确通过，v3 refs 不变。
2. Python、TypeScript 和 JSON Schema 对 v4 `supported_uses` 的接受/拒绝完全一致。
3. `explanation_only` 不要求 cue/dose/retest/scenario；`candidate_experiment`、`diagnosis_support` 与 `scenario_prescription` 各有递增约束，只有最后一种要求 exact scenario + cue/dose + matched/near retest。
4. 九篇文章只能以低权限 entry 进入；不能被编译为无支持的 exact scenario prescription。
5. registry event allow-list 接受 v4，未知 version 仍 fail closed。

### Minimal implementation

新增 schema/registry version，不原地改写 v3；添加 capability-based supported uses、source metadata 和 migration audit。九篇文章各建独立 reviewed entry，按审计准入范围设置用途和 `not_applicable` 字段。

### Frozen decisions

- 继续使用一个 Registry loader/query API，不建平行 community store。
- 社区材料提供 explanation、cue 或 reversible experiment；不自动成为 analyzer rule、身体事实或 exact scenario 身份。
- Provider 仍可自然组织语言，但不能提升 entry capability。

### Stop rule

如果 v4 需要 DB migration、新 store/route、改变 v3 既有 trace 语义，或某篇文章缺少足够本地 source evidence 以形成结构化 entry，则保留 source 记录但不伪造能力。

### Verification

focused Python/Node red-green、JSON Schema validation、完整 Registry suites、migration audit diff、`git diff --check`。

### Task 2 closeout

- 新增 `coach_knowledge_registry.v3` schema 与 staged `2026-07-29.v4` Registry；默认继续使用 `2026-07-28.v3`，留待 Task 3 与全部消费者一起切换。
- v4 把 v3 的 10 个迁移 entry 从 `@1` 升为 `@2`，避免 capability 或字段变化复用历史引用；v3 `@1` 资产未改写。另新增 9 篇 Raw Input 作者级 source 与低权限 `@1` entry；总计 19 entries / 27 sources。社区材料只获得 explanation 或 reversible-experiment capability，不获得 analyzer rule、身体事实或 exact scenario prescription 权限。
- Python、TypeScript 与 JSON Schema 统一强制四级严格 capability 前缀，并用来源数量、topic 数量、unsafe/空白/长 prose 与非法 alias 共同语料校验边界；v4 loader 不再给低 capability 条目补出 forbidden 字段。v1/v2/v3 显式版本仍可读，v2 schema 的旧 enum parity 已补齐，v4 runtime event allow-list 已接通。
- 验证：Python Registry/Agent/Diagnosis/Runtime `129 passed`；Node knowledge Registry `12 passed`；标准 Draft 2020-12 validator 同时通过 `registry.v3.json` 与 `registry.v4.json`；Python compile 与 scoped `git diff --check` 通过；未发现 `???` 损坏文本。

## Task 3 - 稳定 observation refs 与单一知识 projection

### Allowed files

- `kovaak_tracker/advice.py`
- `kovaak_tracker/advice_dynamic_clicking.py`
- `kovaak_tracker/advice_tracking.py`
- `kovaak_tracker/advice_target_switching.py`
- `kovaak_tracker/coach/diagnosis.py`
- `webapp/backend/worker.py`，仅把 producer 的稳定 refs 原样投影进现有 v2 issue，并收敛新 Analysis 的教学正文
- `webapp/tests/test_worker.py`
- `webapp/backend/contracts.py`，仅当现有 v2 validator 需要接受向后兼容的可选 issue 字段时
- `webapp/backend/coach_context.py`
- `kovaak_tracker/coach/agent_tools.py`，仅让既有 Registry payload 读取 capability-aware sections
- `webapp/backend/coach_agent_runs.py`，仅让既有 prepared-item compiler 显式要求 `scenario_prescription` capability
- `webapp/coach-runtime/src/analysis-summary-tool.ts`
- `webapp/frontend/lib/types.ts`
- `webapp/frontend/lib/contracts.ts`
- `webapp/frontend/fixtures/task7-fixtures.ts`
- 对应的 producer、Coach context、Node contract 与 frontend adapter 测试
- 对应的 agent payload 与 prepared-item compiler tests
- `docs/ARCHITECTURE.md`
- `docs/PROGRESS.md`
- 本计划与索引

### Tests first

1. static/dynamic/tracking/switching 的新 issue 发稳定 `observation_ref`、exact registry version 和 knowledge refs；展示 signal 改写不改变解析结果。
2. resolver 优先 exact version + refs；metric refs 只做完整性/歧义检查；错 version/ref fail closed。
3. legacy 无 refs 的历史可以通过 signal fallback 展示，但不能据此生成新的 prepared Training Plan item。
4. Analysis deterministic projection 不再复制 Registry 的完整 definition/cue/dose/retest；Coach context 仍取得完整 Registry entry。
5. wire/frontend allow-list 接受新增可选字段且不改变旧 fixture。

### Minimal implementation

在既有 issue contract 上增加可选稳定 ref，由 producer 显式给出。改造现有 resolver 为 exact-ref first；保留只读 legacy fallback。把完整教学正文收敛到 Registry，Analysis 仅输出简短、证据受限的 observation/candidate/projection。

### Frozen decisions

- 不从展示文本推断新 ref，不创建 text-to-ref 表。
- 不删除 persisted `root_causes` / `prescriptions`；旧数据由 adapter 降级展示。
- Provider 获取同一 Registry entry 后可自然改写，不要求逐字复述。
- Training Plan 继续使用现有 owner、active plan、exact scenario、prepared item 和 confirmation 边界。

### Stop rule

如果需要 AnalysisResult v3、DB migration、第二套 resolver/registry、Provider authored refs，或无法区分 legacy display fallback 与 write authorization，则停止并上报。

### Verification

各 family producer tests、Coach context tests、Node runtime tests、frontend contract/source tests、完整受影响 Python/Node suites、`git diff --check`。

### Task 3 closeout

- Static、Dynamic、Tracking 与 Switching 的 Registry-backed 新 issue 均携带稳定 `observation_ref`、exact Registry version 和 entry ref；Static 未覆盖的既有本地 observation 仍展示，但没有 refs，也不能触发 Coach 教学或计划写入。
- Coach context 只按 exact version + single entry ref + Registry observation ref 解析；显式 `metric:*` 仅校验一致性。legacy signal fallback 不再授权 prepared Training Plan；prepared-item compiler 还要求 `scenario_prescription` capability。
- Python/Pi 前端投影均把这三个可选字段作为成对、白名单化的安全合同传递；低 capability Registry entry 不补伪 cue/dose/retest 字段，Provider 仍可自然改写已获准的内容。
- 默认 Registry 已原子切至 `2026-07-29.v4`，历史 v1/v2/v3 继续显式可读。验证：Python 受影响 suites `358 passed`；Coach runtime Node `165 passed`；frontend contract/source `22 passed`；`git diff --check` 通过。

## Task 4 - 修正 Analysis UI 的归因语义

### Allowed files

- `webapp/frontend/lib/contracts.ts`
- `webapp/frontend/lib/types.ts`
- `webapp/frontend/tsconfig.json`，仅排除 Git 已忽略的 `src-tauri/target` 二进制构建产物，使现有前端 type-check 可验证本 Task
- Analysis Data 现有展示组件及其样式，仅限语义展示所需
- `webapp/frontend/fixtures/task7-fixtures.ts`
- 对应 frontend unit/source/E2E tests 与 screenshot snapshots
- `docs/frontend-uiux-design.md`
- `docs/design-system.md`，仅当 claim-level 呈现需要补充既有 token 规则时
- `docs/PROGRESS.md`
- 本计划与索引

### Tests first

1. 新结果使用“重点观察 / 候选解释 / 规则化练习建议”，不出现无证据的“最需要处理 / 三层根因 / 处方”。
2. claim level 可见且正文不把 physical limitation 当成已证根因。
3. legacy `root_causes/prescriptions` 仍可渲染，标记为历史候选说明；证据、指标和“问 Coach”入口保留。
4. compact desktop 尺寸无溢出、遮挡或布局跳动。

### Minimal implementation

修改现有 adapter 与展示标签，不改 API persistence。新结果优先 observation/candidate/projection；旧字段只作为 legacy candidate display。使用现有 design system，不新建卡片体系。

### Frozen decisions

- UI 不替代事实源修复；Task 3 必须先完成。
- 无 Provider 时不显示伪 Coach 文案，规则化建议仍明确属于本地 Analysis。
- Provider Coach 的自然表达不受 UI 标签逐字约束。

### Stop rule

如果需要全页重构、设计系统重做、删除旧 fixture/adapter、改变导航或 Coach 交互，则停止并缩回语义层。

### Verification

frontend unit/source tests、Playwright E2E 与 accessibility；`1280x820`、`960x640` 截图人工检查；开发服务器 URL；`git diff --check`。

### Task 4 closeout

- 新 `analysis_result.v2` 只有在 `observation_ref`、精确 Registry version 与 entry ref 完整配对时才按当前语义呈现：页面显示“重点观察 / 候选解释 / 规则化练习建议”及 claim level。没有完整配对的记录一律按 legacy 显示，不能借展示文字升级为知识驱动候选。
- 历史 `root_causes` / `prescriptions` 保持可读，但统一标为“历史候选说明”；Analysis 不再把 physical limitation、动作机制或身体状态写成已证实根因。Provider Coach 的自然表达、持久化、导航与交互均未改动。
- `frontend-uiux-design.md` 已同步当前/历史显示边界。验证：Task 4 frontend unit/source `11 passed`、完整 `npm.cmd run type-check`、完整 `npm.cmd run build`、production `next start` 下 Browser smoke + accessibility `16 passed`，以及人工检查并复跑通过的 `1280x820` / `960x640` screenshot baselines。
- `tsconfig` 仅排除 Git 已忽略的 `src-tauri/target` 二进制构建产物；未排除任何 Tauri 源码或前端产品代码。临时 Edge `--no-proxy-server` 只用于本机测试绕过系统代理，未进入仓库或产品运行时配置。

## Task 5 - 补齐有证据的 metric meaningful-change policy

### Allowed files

- `webapp/backend/history_trends.py`
- `webapp/backend/aiming_profile_store.py`
- `webapp/backend/coach_retest_decision.py`
- 对应 history/profile/retest tests
- `docs/ARCHITECTURE.md`
- `docs/PROGRESS.md`
- 本计划与索引

### Tests first

1. exact metric version、direction、comparability 与 existing resolution/tolerance 共同决定 improve/regress/unchanged。
2. 无 policy、version 不匹配、不可比或证据不足继续 `mixed_or_inconclusive`，不能用任意非零 delta 下结论。
3. policy 定义在 Analysis/history metric 层；Coach 只消费结果。
4. TeachingSession、confirmation、idempotency 和 retain-lower-reject 状态机不变。

### Minimal implementation

先审计 metric 定义中已有的 precision/resolution/tolerance；只将有现成依据的指标注册为 versioned policy 并复用 `_METRIC_DIRECTIONS`/comparison helper。没有依据的指标保留 fail closed。

### Frozen decisions

- 不发明 5% 或其它全局阈值，不以一次微小 delta 自动调课。
- 不在 Coach 层复制 analyzer 阈值。
- 不改变状态机/store/schema。

### Stop rule

若 meaningful threshold 只能靠主观新数字、需要真实校准尚未完成、或必须改变 Analysis metric contract，则零实现收口为已记录 blocker，不用猜测补齐。

### Verification

focused history/profile/retest red-green、完整受影响 Coach backend tests、Python compile、`git diff --check`。

### Task 5 closeout

- 未注册任何 automatic improve/regress policy：现有 metric 只有 direction、计算版本和 strict comparability 合同，没有 exact metric/version/conditions 的重复测量误差、worthwhile-change/target-band 与必要 outcome guardrail 证据。非零 Analysis retest 继续为 `mixed_or_inconclusive + metric_change_policy_missing`，没有发明百分比或绝对阈值。
- Profile 曾绕过该边界，将任意同方向非零 contribution 显示为 improving/deteriorating；现已改为 `unknown + metric_change_policy_missing`，只有精确相等保持 `stable`。没有改动 schema、store lifecycle、TeachingSession、confirmation、idempotency 或 Provider。
- `expected_metric_ref` 的 canonical version 仍是 blocker：Training Plan/Coach 同时存在无版本 ref 与完整 `metric_version` ref，无法在本 Task 中强制比对 expected version 而不扩大冻结合同。后续先统一 ref contract，再加 expected-version mismatch 的 fail-closed 测试。
- focused history/profile/retest suites `47 passed`；最终受影响 Python 交叉套件 `643 passed, 1 skipped`、Python compileall、pinned Pi Coach runtime `167 passed`、frontend lib/source `18 passed` 与 scoped `git diff --check` 通过。Task 4 的 type-check/build、production Browser/accessibility 和两尺寸截图验证保持其 closeout 记录。

## 最终交叉验证

完成 Task 1-5 后，执行：Python registry/analysis/Coach/backend suites；完整 Coach Node suite；frontend unit/source/E2E/accessibility；两尺寸截图；旧 Analysis fixture 兼容；真实 Provider 只做受限只读表达验证且不写 field DB。最终由独立只读 review 检查重复机制、历史兼容、Provider 边界和 dirty-worktree 误伤。
