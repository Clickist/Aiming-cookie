# Backend Full Review Ledger

> 状态：complete。创建并完成于 2026-07-24。本文件是本轮 12 路审计的执行合同、跨 session/compaction 恢复点和最终 assessment；confirmed、pending field measurement、release Gate 与 rejected candidate 已分区，不得跨区改写结论强度。

## 1. 审计目标

对当前 dirty worktree 的后端、原生采集、分析、Coach、数据层、测试、发布边界及其主责任文档做全量只读审计。最终回答以下问题：

1. 真实用户运行 KovaaK 时，空闲、游戏运行、Challenge 采集、局后分析和 Coach 调用各阶段是否存在不可接受的 CPU、GPU、内存、磁盘、线程、队列或帧时间负担。
2. 是否存在已证实的重复功能、重复状态机、重复 schema/validator/projector、无必要复杂度和维护风险。
3. 数据、队列、retry、删除恢复、migration、旧版本读取、安全与诊断边界是否可靠。
4. 当前架构能否以合理成本扩展到 Aim Lab、手部摄像头、新 observation channel、新 aim family 和新前端消费者。
5. 当前代码、测试、PRD、Architecture、Roadmap、Progress、active spec/plan、Development、UI/UX 与设计系统之间有哪些直接冲突、过期结论或遗漏。

## 2. 冻结边界

- 审计对象：当前 dirty worktree 的完整实际状态，不只看 `origin/main`；用 Git 区分已提交基线与未提交改动。
- 允许：读代码和文档；运行静态检查、测试、fixture/synthetic benchmark；读取已有、明确用于审计的非产品 field evidence；临时产物仅放 `E:\DevCache\temp`。
- 禁止：修改业务代码；修改 PRD 或产品决策；启动 KovaaK；发起真实 Challenge 采集；读取或修改真实产品数据库；提交、推送、新建分支；在仓库散落 subagent 中间记录。
- 需要真人 A/B 的性能结论标记为 `待实测`，不得用静态推理或旧证据冒充当前实测。
- PRD、UI/UX 和架构修改只形成建议；需要点点拍板的条目必须明确标记。
- 各 subagent 只读并回传证据。只有主审可以更新本总账。

## 3. Git 基线

- 工作区：`C:\Users\袜子\Desktop\Aiming-cookie`
- 分支：`main`
- HEAD：`e5598939ae1f4a688b3b52696986dbef6b0314e7`
- 与上游关系：`main...origin/main [ahead 12]`
- 开始审计时状态：68 个 tracked modified 条目、36 个 untracked 条目，共 104 个 dirty 条目。
- 已知本轮审计前就存在的改动包括 docs、Coach、Python analyzer、FastAPI backend、Node coach runtime、Rust/Tauri capture、测试、knowledge registry 和 scenario registry。除本文件及最终所需的 `docs/README.md` 索引行外，均视为用户已有改动。

## 4. 证据与严重度标准

每条 confirmed finding 必须包含：

- 唯一 ID 和 P0/P1/P2/P3；
- 精确文件与行号；
- 可复核证据或命令；
- 用户/开发影响；
- 已验证事实、静态证据、推理和待实测边界；
- 最小修复方向，不把 speculative refactor 写成必做项；
- 是否需要点点拍板；
- 主审复核状态。

严重度：

- P0：当前可导致数据破坏、严重安全问题、游戏/采集不可用或发布必须立即停止。
- P1：真实主流程高概率错误、明显性能竞争、不可恢复状态、冻结合同直接违背或扩展被核心耦合实质阻断。
- P2：有明确触发条件的可靠性、维护性、性能或文档一致性问题，应排期修复。
- P3：低风险债务、诊断性不足、过期说明或未来触发前再处理的结构问题。

不成立条件：仅凭文件很长、存在重复文字、理论上可能、通用最佳实践或未经确认的依赖告警，不得成为 finding。

## 5. 十二路审计矩阵

| ID | 波次 | 专项 | 核心范围 | 状态 | 负责人/恢复备注 |
|---|---:|---|---|---|---|
| R01 | 1 | 游戏与 Challenge 期间负担 | Rust capture、Raw Input、WGC、MF encoder、watcher、线程、帧时间 | verified | Terra high wave-1；主审已重开 release/轮询/backpressure 调用链 |
| R02 | 1 | 局后处理负担 | MP4 解码、CV、analyzer、worker、峰值内存、磁盘 I/O、并发 | verified | Terra high wave-1；主审已重开 native/video/evidence 调用链 |
| R03 | 1 | 生命周期与容量 | 300 秒缓冲、队列、backpressure、掉帧、退出恢复、长期存储增长 | verified | Terra high wave-1；主审已重开 Raw/barrier/storage/recovery 调用链 |
| R04 | 2 | 重复实现与测试质量 | schema、validator、projector、状态机、错误处理、fixture、假覆盖 | verified | Terra high wave-2；主审复核 fixture/parity/forbidden-key drift |
| R05 | 2 | 复杂度与模块边界 | 超大函数、耦合、私有 helper、隐藏依赖、无必要抽象与兼容层 | verified | Terra high wave-2；1 条误报驳回，2 条降级/分期 |
| R06 | 2 | 数据可靠性 | SQLite migration、事务、retry、幂等、删除恢复、竞态、旧数据兼容 | verified | Terra high wave-2；P0 shared-connection interleaving 已由主审临时 DB 复现 |
| R07 | 3 | 分析与教学正确性 | 指标定义、置信度、Scenario Gate、降级、Coach 过度解释、有效性边界 | verified | Terra high wave-3；主审复核 product-path reachability |
| R08 | 3 | 扩展架构 | Aim Lab、手部摄像头、新传感器、新 aim family、版本化 observation channel | verified | Terra high wave-3 + 主审；无当前 P0/P1，形成触发式迁移 Gate |
| R09 | 3 | API、安全与 Provider | owner 隔离、secret/path/raw 泄漏、权限、上下文成本、接口版本 | verified | Terra high wave-3；主审复核 sidecar 生命周期、preview 部署合同与 public result 路径 |
| R10 | 4 | 测试、CI、依赖与发布 | 覆盖盲区、flaky/慢测试、Windows、Python/Node/Tauri、许可证 | verified | Terra high wave-4；主审区分新 finding 与 Roadmap 已知 No-Go Gate |
| R11 | 4 | 文档事实源一致性 | PRD、Architecture、Roadmap、Progress、spec、plan、Development 与代码 | verified | Terra high wave-4；PRD 无需因本轮 finding 修改，Progress/Roadmap closeout 需纠正 |
| R12 | 4 | UI/UX、可运营性与反向审查 | partial/outcome-only/失败/重试/删除/视频缺失/诊断/存储；挑战前 11 路 | verified | Terra high + 主审；UI 状态合同充分，完成 reject/downgrade/merge/keep |

状态只使用 `pending | running | agent-complete | verified | blocked | deferred-field-test`。每波最多并发 3 个 subagent；释放槽位后再启动下一波。

## 6. 主审复核规则

1. 对每个 subagent finding 重新打开对应代码和调用链，不直接照抄结论。
2. 优先寻找反证、现有 guard、测试和上游合同；确认触发条件真实存在。
3. 性能数字必须注明机器、输入、命令、样本量和是否与游戏并行。旧 field evidence 只能证明当时结果。
4. 安全 finding 必须给出资产、入口、信任边界与实际影响；本地单用户设计不能套用无关 Web 多租户假设。
5. 文档冲突区分：同层直接冲突、上游合同与实现差距、过期状态、合理摘要差异、proposal 未实施。
6. 复核后将结论写入本文件；被驳回的候选只记入“反向审查与排除项”，避免后续重复提出。

## 7. Findings

### Summary

- 最终确认 `2 P0 / 5 P1 / 13 P2 / 7 P3`；另有 4 个已知 No-Go release Gate、5 组待真人/硬件实测、8 个明确驳回或去范围候选。
- 真实使用性能：当前 GPU capture、drop-first bounded queue 与无 CPU encoder fallback 的方向正确；没有本轮 A/B 证据证明开启 Aiming Cookie 已显著拖慢 KovaaK。已确认风险集中在 KovaaK 退出后 capture 不 release、finalizer/CV/下一局缺 QoS，以及局后 Raw/visual 全量物化；游戏帧时间、三厂商 GPU 与高 polling 仍必须实测。
- 正确性与数据：测试 DB 隔离和全局共享 SQLite transaction 是两个 P0；uploading 永久卡死、Raw 完整性、frame PTS completeness 和 partial native 1.0 diagnosis 是五个 P1 主流程问题中的其余部分。
- 代码质量：没有发现值得把四个 family 强行表驱动或合并跨语言安全校验的“大面积重复”；确认的重复/漂移主要是 native trajectory 二次构造、Knowledge validator 接受集、snapshot shape 与少量 projector/测试入口依赖。
- 可维护与扩展：当前 KovaaK-specific source adapter 符合 v1 最小权限，不应现在泛化成插件。Aim Lab 获批后再引入 versioned `run_ref + source_kind`；手部摄像头必须先冻结 consent、local-only raw、camera/biometric 分类、校准/时钟、retention/tombstone 和 Coach allowlist。
- 文档与 UI/UX：PRD 最近提交为 2026-07-20，当前 dirty PRD 也有新改动，不是“很久未更新”；本轮无需修改 PRD。UI/UX 对 onboarding、partial/outcome-only、视频缺失、Coach unavailable、History/Storage 和删除状态覆盖充分；真正差距是正式 routes 尚未实现、frontend adapter 已与 backend/native DTO 漂移。Progress/Roadmap 的 backend closeout 必须因两个 P0 重新打开。

### Confirmed

#### P1-01 - KovaaK 退出后 capture session 没有运行期 release 路径

- 证据：`webapp/frontend/src-tauri/src/capture_coordinator.rs:854-870` 在进程退出时只关闭 Raw 并进入 `Finalizing`；该 phase 在后续监控中直接返回。只有同文件 `1014-1031` 的 `release_capture_session()` 会停止 WGC/encoder 并回到 waiting。全仓 Python 调用点只有 `webapp/backend/kovaak_capture_finalizer.py:103-130` 的 runtime shutdown；正常 finalizer 路径没有 release，`webapp/backend/desktop_runtime.py:267-268` 也只在整个 desktop runtime 退出时调用。
- 触发与影响：用户启用自动采集、退出 KovaaK、但 Aiming Cookie 继续运行。下一次启动 KovaaK 时 coordinator 仍处于 `Finalizing`，不会建立新的 Raw/WGC session；旧 WGC/硬编/最多 384 MiB replay 资源也可继续存活。
- 边界：状态机与调用点是已验证静态事实；退出后具体 GPU 驱动占用仍待实测。不能简单在“每个 Run finalized”后 release，因为 Stats 可在同一 KovaaK 进程的多局之间到达，提前 release 会破坏下一局 pre-roll。
- 最小方向：新增 process-exit -> pending finalizer drain -> same-session release 的明确 handshake，并为没有新 Stats/Performance 到达的退出路径定义有界 grace timeout；补 restart-without-app-exit 与 exactly-once release 测试。
- 拍板：需要点点冻结退出后的 evidence grace window；修复卡在 `Finalizing` 本身不需要改变产品范围。

#### P1-02 - Raw barrier 未携带掉点/过期质量，incomplete trace 可被标为 attached

- 证据：`webapp/frontend/src-tauri/src/raw_input.rs:145-163` 的 ring 超过 1,000,000 points 会丢最旧点，`1313-1321` 的 bounded queue 满时只累计 drop；barrier/receipt 在 `935-960` 只返回覆盖时间与 point count。`webapp/backend/kovaak_capture_finalizer.py:248-268` 和 `webapp/backend/kovaak_run_store.py:993-1047` 主要按 `coveredThrough >= challenge end` 决定 trace attachment，没有消费 session-scoped drop/expiry 质量。
- 触发与影响：高 polling-rate、调度停顿或长 session。300 秒 ring 在 4 kHz 需要 120 万点、8 kHz 需要 240 万点，而硬上限只有 100 万；即使最后时间覆盖，前段或中间掉点也可能未被表达，输入原生指标会建立在不完整 trace 上。
- 边界：容量公式与缺失 receipt 字段是事实；真实 4/8 kHz 掉点率尚未实测。当前 drop-first 策略正确地保护了游戏输入线程，问题是质量语义没有进入 Run/Analysis Gate。
- 最小方向：把 capture-session scoped queue drops、ring expirations 与请求窗口内完整性纳入 barrier receipt/Run quality；非零缺失必须明确 `limited` 或 fail closed，不能继续表示为完整 native evidence。
- 拍板：需要点点选择缺失时允许 limited 分析还是完全拒绝。

#### P2-13 - 局后 native/视觉 evidence 全量物化，缺少统一资源预算

- 证据：`kovaak_tracker/native_flicking_analysis.py:42-64` 先构造 trajectory；`160-174` 又把它传给会在 `83` 再次调用 `derive_trajectory()` 的 alignment。`224-398` 随后保留完整 point dictionaries，并为 position x/y、speed、acceleration 复制多组数组。视觉路径也在 `kovaak_tracker/visual_signals.py` 与 analyzer 间累积整局 observations/samples，`webapp/backend/evidence_store.py:110-177` 再对完整 artifact 做 canonical JSON、fsync 与回读。公开 AnalysisResult 在 `webapp/backend/worker.py:1547-1582` 会去掉 Raw points，因此不是 public JSON 泄漏。
- 触发与影响：高 polling-rate、长 Run 或多目标。主审隔离 synthetic 的 100,000-point native 路径为 2.154 s、`tracemalloc` peak 71.73 MiB、内部 JSON 8.12 MiB；视觉 synthetic 3,600/18,000 frames 分别约 1.587/8.002 s、4.79/23.64 MiB peak。两者都未包含真实 decode、输入 bytes、下一局并行与完整 RSS，不能据此声明游戏帧时间受损。
- 最小方向：alignment 接受一次验证后的 trajectory；按 analyzer/query 的真实需求设计有界 derived representation；给 60/300 秒、多目标与 100k/300k/1M Raw 建立统一 wall/RSS/artifact-size budget。
- 拍板：消除第二次 derive 不需拍板；改变 derived evidence 分辨率或保留合同需要点点批准。

#### P2-01 - finalizer 无并发/QoS 上界，可与 worker 和下一局争抢默认线程池

- 证据：`webapp/backend/desktop_runtime.py:34-64` 的 `FinalizerFutureTracker` 只追踪/取消，不限并发；`181-204` 对每个稳定 discovery 立即 `run_coroutine_threadsafe(finalizer.finalize())`。finalizer 的 Raw flush/export 与 worker 的 CV/native 都使用 `asyncio.to_thread`，共享默认 executor；worker job 本身虽为单消费者，但 capture finalization 不是。
- 触发与影响：首次启动发现多份稳定文件、快速连续 Run 或 pending retry，同时用户开始 Analysis/下一局。CPU、磁盘和 default thread pool 可并发竞争；实际游戏帧时间影响待实测。
- 最小方向：给 finalization/export 建立有界队列与优先级/QoS；保留 source revision 去重和 worker 单消费者。
- 拍板：需要点点选择“下一局流畅优先”与“局后尽快可见”的策略。

#### P2-02 - orphan quarantine 不计入 Storage，且没有用户可见管理路径

- 证据：`webapp/backend/kovaak_run_store.py:2337-2343`、`2535-2541` 会把未引用视频/Raw 移到 `runs/orphans`；`2191-2243` 的 `run_storage_usage()` 只遍历数据库中的 Run 文件。`webapp/backend/routes.py:226-244` 将该结果直接汇入 `/storage`。
- 触发与影响：崩溃、部分 finalization 或 reconciliation 产生 orphan 后，它们会持久占盘，但设置页总量与分类不显示，用户也无法审查/移除。
- 最小方向：把 orphan count/bytes 作为独立 storage category，并提供显式 review/remove；不得在 derived evidence 未验证前静默删除。
- 拍板：保留期与最终删除动作需要点点决定。

#### P2-03 - History/Storage 长期读路径无分页并在 API event loop 同步 walk

- 证据：`webapp/backend/queue.py:514-590` 返回 owner 全部 sessions；`494-508` 对每条 session 同步调用 `workspace_size_bytes()`，后者在 `webapp/backend/workspace.py:74-83` 用 `os.walk`。Run storage 同样遍历所有已知 Run 文件。Run list 的每项 correlated `analysis_count` 依赖 `(kovaak_run_id,user_id)`，而当前 schema 只有 `sessions(user_id,status)` 索引。
- 触发与影响：长期使用积累大量 Analysis、Run 与 artifacts 后，History/Storage 请求在 event loop 上线性扫描文件；Run list 还会放大 sessions 扫描。UI 可能出现可感知卡顿，且与后台工作竞争磁盘 metadata I/O。
- 最小方向：History 使用 cursor pagination；storage accounting 改为异步/缓存增量；增加适配实际查询的 `(user_id,kovaak_run_id)` 索引或一次 group aggregate。
- 拍板：不需要。

#### P3-04 - managed MP4 视觉路径重复做三次全文件 SHA-256

- 证据：`webapp/backend/worker.py:2833-2837` 在 dispatch 前校验；`293-330` 的 `run_visual_preprocessing()` 内再次校验；`3401-3405` 在 evidence/terminal commit 前第三次校验。每次 `_assert_managed_video_matches_snapshot()` 在 `826-857` 完整读取文件并计算 SHA-256。
- 触发与影响：任何 managed MP4 分析。长视频会产生额外全文件读取和 CPU hash；其中 CV 前与 commit 前属于刻意 TOCTOU guard，只有相邻重复校验可疑，用户可感知程度仍待 10/100/500 MiB benchmark，因此最终降为 P3。
- 最小方向：保留 CV 前与 commit 前的 TOCTOU guard；让 preprocessing 接受已经验证的 immutable revision token，去掉相邻重复校验，且不能削弱“CV 期间 source 改变必须拒绝”的现有测试。
- 拍板：不需要。

#### P0-02 - webapp pytest 可能迁移并改写外部/产品数据库

- 证据：`webapp/tests/conftest.py:15` 用 `os.environ.setdefault()`，因此调用者已有 `DATABASE_URL` 会被保留；`webapp/backend/config.py:9-23` 在 import 时把它冻结为 `DB_PATH`。fixture 在 `conftest.py:37-41` 却固定删除 `./aiming_cookie_test.db` 后对实际 `DB_PATH` 调 `init_schema()`；后续 tests 全部使用该实际连接。
- 触发与影响：开发者 shell、CI 或 Desktop 环境预先设置了指向开发/产品库的 `DATABASE_URL` 后运行 `pytest webapp/tests`。测试不会重置那个外部库，却会迁移并写入/删除其数据；同时还可能误删 cwd 下无关的 `aiming_cookie_test.db`。
- 最小方向：在 import backend 前无条件把 `DATABASE_URL` 设置为本次测试唯一、绝对、位于测试 temp root 的 SQLite path；fixture 删除必须使用解析后的同一个测试路径，并增加“外部 DATABASE_URL 不可被测试消费”进程隔离测试。
- 文档后果：`docs/PROGRESS.md:14` 的 `1202 passed, 5 skipped` 只能作为历史特定环境快照，不能继续证明 fixture 已隔离；`docs/DEVELOPMENT.md:115-117` 的命令在修复前必须写明安全前置条件。
- 拍板：不需要；修复前不得在继承未知 `DATABASE_URL` 的 shell 运行 webapp 全量测试。

#### P0-03 - 全局共享 aiosqlite connection 允许跨协程提前 commit 别人的事务

- 证据：`webapp/backend/db.py:11-24` 全进程只暴露一个 connection。`webapp/backend/queue.py:654-693` 的删除事务在多次 `await` 间执行 `BEGIN IMMEDIATE -> tombstone -> delete row -> COMMIT`；`210-222` 的 heartbeat 对同一个 connection 独立 `commit()`。各 store 的 module-local locks 不能保护 queue/Run/Coach 或彼此事务。
- 主审复现：在 `E:\DevCache\temp` 的全新 DB 中，T1 `BEGIN IMMEDIATE` 并插入删除 tombstone，T2 调真实 `queue.heartbeat()`；关闭/重开连接后，tombstone 与仍为 `done` 的 session 同时持久。运行真实 `reconcile_analysis_deletions()` 后，session 行仍为 `done`，tombstone 被清掉，session workspace 被删除。
- 触发与影响：任何显式事务在 await 间与 heartbeat、enqueue、store write 等独立 commit 交错，再发生进程崩溃或异常。原本原子操作会部分持久，删除恢复可移除仍存活 Analysis 的 managed evidence；同类风险覆盖 command reservation、plan/profile/provider/Run evidence 事务。
- 最小方向：建立 DB-wide transaction ownership，保证显式事务期间其它 statement/commit 不能使用同一 connection；或为事务提供独立连接并统一配置 PRAGMA。单纯增加某一个模块锁无效。
- 文档后果：`docs/ROADMAP.md:51-55,100` 的 deletion/reconciliation closeout 与 `docs/PROGRESS.md:13-15` 的 backend handoff 已被该复现否决，必须重新打开 transaction-ownership Gate。
- 拍板：不需要；在修复和并发回归前，当前 SQLite transaction 原子性不可作为发布证据。

#### P1-04 - 中断上传永久停在 uploading，并阻塞该 owner 后续分析

- 证据：`webapp/backend/routes.py:214-230`、`287-330` 先持久化 `uploading` session，再写最终命名文件并 finish。`webapp/backend/app.py:21-38` 启动只 reconciliation Analysis deletion；worker claim 在 `queue.py:158-166` 排除 uploading，而 `queue.py:453-462` 的 active gate 包含它。普通删除又只允许 done/failed。
- 触发与影响：进程在 enqueue 后、finish/exception cleanup 前退出。重启后 row 与 partial workspace 永久保留，不能被 worker claim/普通删除，且同 owner 新请求持续 429。
- 最小方向：startup 原子清理/terminalize stale uploading row，只删除对应 managed workspace；上传先写临时名，完整校验后原子 publish，再 finish。
- 拍板：不需要；uploading 没有可保留的完整用户结果。

#### P2-06 - Knowledge Registry v2 的 Python/TypeScript 接受集合已漂移

- 证据：`kovaak_tracker/coach/knowledge_registry.py:510-534` 要求所有 entry section 的 `section_ref` 全局唯一；`webapp/coach-runtime/src/knowledge-registry.ts:460-497` 只检查 entry/version 与 active uniqueness，没有 section uniqueness。把同一 entry 的 `scope.section_ref` 改成其 `definition.section_ref` 时 Python 拒绝、TypeScript 接受。现有 `knowledge-parity.test.ts:7-34` 只比较 4 个合法查询结果，不能发现非法资产接受集合差异。
- 影响：未来资产编辑可能在 Python backend fail closed、Coach sidecar 却成功加载，造成运行面分裂；当前 canonical asset 合法，现有用户数据未受损。
- 最小方向：TS 增加同等全局 uniqueness；用共享 malformed corpus 对两端 validator 做 accept/reject parity，保留两端独立安全校验。
- 拍板：不需要。

#### P3-05 - Coach tool 与 backend 对 `password`/`secret` 的参数拒绝集不一致

- 证据：`webapp/backend/coach_commands.py:88-104` 拒绝 `password`、`secret`；`webapp/coach-runtime/src/product-command-tools.ts:32-36` 未包含这两项。TS 已通过 `normalized.includes("path")` 拒绝 `trace_path`，所以原候选中该部分不成立；backend 仍会最终拒绝 password/secret。
- 影响：sidecar 不能在本地第一边界阻止模型生成的敏感形状进入 loopback bridge；后端是有效最终防线，因此不是当前越权，最终降为 P3 合同/测试漂移。
- 最小方向：TS 增加 password/secret 并用参数化测试断言 bridge fetch 未发生；backend 独立校验必须保留。
- 拍板：不需要。

#### P2-08 - claim/retry 对旧 snapshot JSON 的 shape 规则不一致

- 证据：`webapp/backend/queue.py:796-805` 的 `get_session()` 把非 object JSON 转成 `None`；`claim_next()` 在 `199-203` 只 `json.loads`，可把 list/string/scalar交给 worker。`worker.py:1830-1851` 立即假定 `.get()`；generic exception 被写成 retryable `analysis_failed`，而 `queue.py:363-375` 的 retry 只检查原始 JSON 非空。
- 触发与影响：旧库或 malformed row 的 `input_snapshot_json='[1]'` 等。job 反复进入 misleading internal retry，而不是稳定 invalid/migration outcome。
- 最小方向：共享一个 dict-only decoder；Run-backed retry 必须校验对应 snapshot contract，非法形状稳定 terminalize 为 input validation。
- 拍板：不需要。

#### P3-01 - REST route 依赖 Coach command 模块的私有 projector

- 证据：`webapp/backend/routes.py:1323` 直接调用 `coach_commands._safe_segment()`；该私有 helper 定义在 `coach_commands.py:1623-1633`，同时用于 Coach tool result。
- 影响：改变 Coach command 返回或“清理私有 helper”会意外破坏前端 evidence endpoint，依赖方向不清晰。
- 最小方向：前端接入前将其提升为 leaf projection/contract 模块中的公共 `project_evidence_segment()`，两端复用。
- 拍板：不需要。

#### P3-02 - lease loss 可留下未被 terminal result 引用的 evidence revision

- 证据：`webapp/backend/worker.py:3406-3418` 先原子发布 evidence revision，再做 `mark_done` lease CAS；CAS false 只记录日志。没有 revision reachability reconciliation。
- 影响：失败/retry可在同一 session workspace 留下额外 immutable revision。它仍被 Analysis workspace storage accounting 计入，且用户删除 done/failed Analysis 会整体清理，因此原报告所称“完全不可见且无法清理”不成立；真实问题是单 Analysis 内冗余占盘。
- 最小方向：accepted terminal commit 后保留 winning revision并清理/隔离其它 revision，或为 failed lease 添加补偿清理；不得误删另一 worker 已引用的 revision。
- 拍板：不需要。

#### P3-03 - Knowledge parity test 依赖调用 cwd

- 证据：`webapp/coach-runtime/test/knowledge-parity.test.ts:14-16,28-31` 以 `process.cwd()` 解析 `.venv/Scripts/python.exe`。从 coach-runtime 目录执行会 ENOENT，从 repo root 且显式 `PYTHON_BIN` 才通过。
- 影响：同一测试因入口 cwd 不同出现假失败，降低 parity Gate 可维护性。
- 最小方向：用 `import.meta.url` 推导 repo root 或在唯一 package script 固定 cwd。
- 拍板：不需要。

#### P1-05 - 缺失/异常 frame PTS 未降低 metric completeness，可进入高置信 profile

- 证据：`kovaak_tracker/visual_signals.py:2471-2486` 把 `missing_frame_pts`、`non_monotonic_frame_pts`、`frame_pts_outside_canonical_window` 标成 artifact partial，但 `2260-2301` 的 runtime disable 只覆盖 identity/event/frame-gap 等其它条件。`webapp/backend/worker.py:1421-1448` 把保留下来的 target/crosshair samples 无条件写成 `measurement_complete=True`；tracking analyzer 据此计算 usable measurements。`webapp/backend/aiming_profile_store.py:193-245` 允许 support `partial` 且 metric coverage >= .95、无 metric limitations 的 contribution。
- 触发与影响：producer 出现缺失/乱序/out-of-window PTS，但剩余观测能形成 samples。artifact 顶层虽 partial，具体 metric 可继续显示完整 coverage、进入 History/profile，用户得到不真实的 target-relative 能力画像或训练方向。
- 边界：static + synthetic 已证实 PTS `[0,17,None,51]` 仍保留 dynamic/tracking/switching family；真实 GPU/codec 出现率待实测。不是要求“一帧缺失就永久拒绝”，而是不能把缺失从 metric quality 中抹掉。
- 最小方向：按缺失/乱序比例与位置降低 channel/metric coverage和 `measurement_complete`；无法界定时 disable 依赖时间连续性的 family。Profile contribution 必须看到相同 limitation/coverage。
- 拍板：不需要，符合现有 quality/fail-closed 合同。

#### P1-06 - partial native evidence 可被诊断为 `fluid_precise` confidence 1.0

- 证据：`kovaak_tracker/native_flicking_analysis.py:206-243` 对 partial alignment 仍产出 metrics，并在 limitations 中写 `alignment_partial`/click anchor缺失。`webapp/backend/worker.py:1585-1639` 的 `_native_diagnosis()` 只过滤 unavailable metric，不接收 result status、coverage或limitations。`kovaak_tracker/coach/diagnosis.py:133-147` 在无 negative finding 时固定返回正向 `fluid_precise`/`fluid_tracker`、confidence 1.0。
- 可达性：`webapp/backend/kovaak_run_store.py:393-429` 的 readiness 只要 Stats/Performance/trace 文件 available 就启用 input-native；trace attach 在 `974-1065` 只要求窗口内有点和 barrier 覆盖到 end，不证明 start/中间完整。这与 P1-02 的 drop/expiry receipt缺口相连，不只是不可达 synthetic。
- 影响：证据不足的 Run 会显示正向类型标签和 1.0 置信度，可能掩盖需要重采/复测的事实。
- 最小方向：native diagnosis 前要求 status available、完整 alignment/coverage和可用 flick evidence；否则 profile=`unclassified`、confidence=0，并把 quality limitation留在 UI/Coach。
- 拍板：不需要。

#### P2-09 - Knowledge Registry 的 `family_scope` 未成为 query 硬过滤条件

- 证据：Registry v2 schema要求每条 entry 有 family scope；`webapp/coach-runtime/src/knowledge-registry.ts:545-565` 只按 topic/signal/metric/supported_use计分，`knowledge-tools.ts:108-140` 没有 family参数。当前 context虽携带 scenario/support，但检索层不据此限制。
- 影响：Coach可获得与当前 Run family不适用的 cue/mechanism；entry自身仍携带 scope/forbidden inference，prompt也有证据等级约束，因此尚未证明实际越界，属于 fail-closed缺口而非已发生错误处方。
- 最小方向：把 resolved aim family作为不可由模型伪造的 tool context，默认硬过滤；若产品需要 cross-family transfer，新增显式 `general/transfer` 查询模式并在回答中标注。
- 拍板：需要点点决定默认严格 family lock，还是允许显式跨 family transfer；建议默认严格。

#### P2-14 - Desktop Coach 把 credential 与 launch token 发给未认证的固定 loopback listener

- 证据：`webapp/backend/config.py:346-349` 默认把 Coach sidecar 固定为 `127.0.0.1:8765`；`webapp/backend/coach_runtime.py:265-294,649-655` 将完整 Provider credential、turn payload、bridge bearer 和可选 Desktop token POST 到该地址。`webapp/coach-runtime/src/sidecar-server.ts:95-239` 对 credential/status/turn 路由没有调用方 capability 校验。Tauri `webapp/frontend/src-tauri/src/runtime.rs:34-71` 只启动 Python runtime，全仓 Desktop 路径没有启动并绑定本次 launch 的 sidecar；开发脚本才独立管理它。
- 触发与影响：Desktop 调用 Coach，而固定端口已被残留或先占用的本机 listener 监听。该 listener 可收到 Provider API/OAuth credential；带 tool bridge 的 turn 还包含短期 bearer 与 Desktop launch token。威胁边界是同一 OS 用户的本机 listener，不是远程或跨身份攻击；PRD 又允许 credential 明文存于 app-owned SQLite，因此最终按架构边界违背定为 P2。subprocess fallback 只在请求失败后发生，不能撤回已发送的 secret。
- 合同：`docs/ARCHITECTURE.md:43-54,292-295` 要求 Desktop 管理本次启动 runtime、使用 launch-scoped token，并限制 secret 传播。当前实现既没有证明 sidecar 属于本次受控进程树，也让 Desktop Coach 在没有外部 sidecar 时不可用。
- 最小方向：Tauri 以直接子进程启动 sidecar，使用随机 loopback port 和独立一次性 sidecar capability；Python 只从本次启动参数获得二者，sidecar 对除 health 外的每个请求验证 capability，并随 Desktop 进程树退出。
- 拍板：不需要改变产品范围；packaging 方案需要决定 Node/Pi sidecar 的分发形态。

#### P2-10 - `analysis_result.v2` 接受未知 private 字段并原样进入公开 Session DTO

- 证据：`webapp/backend/contracts.py:809-922` 校验 v2 必需字段、路径形状和部分嵌套合同，但没有递归拒绝未知字段；`deterministic` 与 `input_snapshot` 仍是开放 dict。`webapp/backend/queue.py:401-415,813-830` 将校验后的完整 dict 持久化并读取；`webapp/backend/schemas.py:126-132` 的 `SessionStatus.result` 是无约束 `dict`，`webapp/backend/routes.py:336-342,580-588` 原样返回。
- 触发与影响：未来 producer、迁移/import 或损坏 DB 在合法 v2 中加入非 path-shaped 的 private/parser/secret 字段。路径检查能阻止绝对路径形状，却不能阻止 `private_parser_blob` 等普通键和值进入 Session API、前端或下游 Coach。
- 边界：当前 worker 会先做 public snapshot/projector，尚未发现现有 canonical producer 正在泄漏 secret；这是公开合同 fail-closed 缺口，不是已发生的数据外传。
- 最小方向：给公开 result/error 建立递归 allowlist 或单独 public projection；持久化内部合同与 API DTO 分离，并用 unknown private/raw/secret 字段回归用例证明不会出站。
- 拍板：不需要。

#### P3-06 - Web preview 的 trusted-proxy 安全条件只存在于部署约定，没有启动时 fail-closed

- 证据：`docs/ARCHITECTURE.md:59-61` 明确公开 preview 必须位于 VPN/SSO/可信反代后，浏览器 owner header 不能成为信任根。`webapp/backend/auth.py:31-52` 在 `TRUST_PROXY_USER=0` 时仍接受客户端 `X-User-Id`/默认 `dev`；`webapp/backend/app.py:44-56` 只有存在 Desktop launch token 才全局鉴权。`scripts/dev-up.sh:6,60` 的官方开发入口默认只绑 `127.0.0.1`，因此本地开发路径本身没有远程暴露。
- 触发与影响：部署者把 FastAPI 暴露到非 loopback，却遗漏 `TRUST_PROXY_USER=1` 和可信代理 header 清洗。请求者可伪造 owner，读取对应 Analysis/History/video/Provider 状态或调用 Coach。
- 边界：这是部署合同未被程序化强制，不是正式公网服务已被证实可攻击；默认开发 bind、No-Go 与 Architecture 的受控预览要求都是有效 guard，因此最终降为 P3 release/preview Gate。CORS 仍不是身份边界。
- 最小方向：非 loopback/preview startup 必须显式选择 trusted-proxy 模式并校验代理契约，否则拒绝启动；保留一个显式 test/dev-only unsigned owner 开关。
- 拍板：需要点点冻结受控 preview 的启动配置与 unsigned dev 模式。

#### P2-15 - 产品仓库没有自动 CI Gate

- 证据：根仓库没有 tracked `.github/workflows`；现有 workflow 只位于 vendored `third_party/pi/.github/workflows/ci.yml`，且只覆盖 Pi 的 Linux/Node 检查。`docs/DEVELOPMENT.md:110-173` 列出的 Python、Coach、frontend 和 Windows MSVC 矩阵全部依赖人工执行。
- 影响：当前大范围 dirty 改动或后续 PR 可以在没有 backend DB 隔离、Coach parity、frontend build、MSVC locked check/test/clippy 的情况下合入；也没有产品依赖审计。已确认的 test DB P0 还使最直接的 backend 命令暂时不安全，进一步削弱 handoff 可信度。
- 边界：项目仍为 No-Go、没有正式 release，因此不是线上回归；手工验证和本轮 focused suites 是现有 guard。缺 CI 是持续集成与维护风险，不等同于某个测试已失败，最终按 pre-release Gate 定为 P2。
- 最小方向：先修 P0 test DB 隔离，再增加产品 workflow：隔离 DB/temp root、完整 Coach env、frontend tests/build、Windows MSVC locked checks；真实 capture/高 polling/三厂商 GPU 保留人工 Gate。
- 拍板：需要点点决定 hosted runner、CI secrets 与未来 release workflow 权限。

#### P2-12 - Python/前端工具链缺少可复现版本与依赖安全基线

- 证据：根 `requirements.txt` 与 `webapp/requirements.txt` 使用浮动/无上界依赖，仓库没有 Python lockfile 或受支持 Python 版本声明；frontend 有 lockfile 但 `webapp/frontend/package.json` 没有 Node engine。当前没有产品级 `pip-audit`、`npm audit` 或 Cargo audit/deny 报告。
- 影响：不同开发机/时间安装可得到不同 Python 依赖，Python 3.9 annotation compatibility 已造成 focused suite collection 问题；发布前也无法复核一份稳定的漏洞与许可证依赖集合。
- 边界：没有运行依赖漏洞扫描，也没有确认任何具体 CVE；不得把“未审计”写成“存在漏洞”。Pi 与 Cargo 已有各自 lock/engine 的部分 guard。
- 最小方向：声明受支持 Python/Node/Rust toolchain，生成可复现 Python lock，CI 从锁定依赖安装并保存依赖审计结果。
- 拍板：需要点点选择 lock/audit 更新策略；不需要修改 PRD。

#### P2-16 - 前端 adapter 与现有 backend/native DTO 已漂移

- 证据：`webapp/backend/schemas.py:55-65` 的 `StorageResponse` 已包含分类 totals，而 `webapp/frontend/lib/types.ts:436-446` 仍只有总量和 sessions；`types.ts:259` 的 Session 状态遗漏 backend 在 `webapp/backend/queue.py:147-151` 持久化的 `uploading`。后端已有 Provider routes（`webapp/backend/routes.py:811-1060`）、EvidenceSegment DTO/route（`schemas.py:389-425`、`routes.py:1282-1355`）与 plan/execution/retest routes，native 也已有 capture commands，但 frontend `lib` 没有对应完整类型/API adapter。
- 影响：正式页面重建若直接使用当前 adapter，无法按冻结 UI 合同表达 Storage 分类、上传恢复、Provider onboarding、EvidenceSegment playback、训练计划和 capture lifecycle。
- 边界：`webapp/frontend` 当前没有 `app/`/`pages/`，所以尚不存在错误渲染；这是前端 Task 开工前必须纠正的接线合同，不是当前可达 UI 回归。
- 最小方向：以 backend 公开 DTO/native command 为唯一源补齐或生成 adapter contract tests；不要从已删除 prototype 恢复旧 shape。
- 拍板：不需要；需在点点授权相应 frontend reconstruction Task 后实施。

### 文档与代码一致性冲突

P0 test evidence 与 reliability closeout 冲突已分别合并到 P0-02/P0-03，避免把同一根因重复计数。PRD 的四 family、movement outcome-only、自动采集/pause fail-closed、Run-owned storage、No-Go 与三厂商 Gate 均与代码/manifest 一致。

#### DOC-01 (P2) - 7 月 24 日 closeout 没有标明是未合入的 dirty-worktree 事实

- `docs/PROGRESS.md:9-15` 与 active plan 多处记录 tracking manifest、Task 11/12 closeout；实际 `knowledge/scenarios/`、相关后端与文档仍未提交。代码事实存在，但不是已合入 baseline。
- 最小文档动作：在 7 月 24 快照和 Task 12 status 明确“当前 dirty worktree、未合入”，避免下一次恢复把它当作 `main` 已提交能力。

#### DOC-02 (P3) - Progress 顶部时间与历史状态容易被误读为同一当前快照

- `docs/PROGRESS.md:3` 仍写最后整理 2026-07-23，但 `:9-15` 已记录 2026-07-24；`:29` 的 7 月 23 历史仍称 manifest 为空、Task 11 未开始。后者有日期，所以不是直接事实冲突，但顶部元数据和当前/历史混排会放大歧义。
- 最小文档动作：更新时间，并把被覆盖的细节移入 history 或显式标成“已被上方 7 月 24 快照取代”。

### 已知发布 Gate（不是本轮新代码缺陷）

- `RG-01 Desktop distribution`：`webapp/frontend/src-tauri/tauri.conf.json:33` 的 bundle 仍为 disabled，runtime 依赖源码目录和系统 Python；当前 EXE 不是可独立分发安装包。Roadmap/Progress 已明确 packaging 未完成和 No-Go，需单独 plan 冻结 Python/Coach sidecar bundling、installer、签名、update/rollback 与 clean-machine tests。
- `RG-02 Third-party notices`：根 `NOTICE` 只覆盖当前显式 RefleK attribution，vendored Pi 有独立 LICENSE/PROVENANCE；正式分发前需按最终安装包生成第三方依赖/资产 notice inventory 并由点点做 release/legal 决策。当前未分发，不声称已发生许可证违规。
- `RG-03 Physical matrix`：Raw Input/GPU live tests 按设计 ignored；Roadmap 正确保留 high-polling 与 NVIDIA/AMD/Intel Gate。现有 NVIDIA evidence 不能替代本轮真实 A/B 或 AMD/Intel，继续归入 `PF-01` 至 `PF-05`。
- `RG-04 Frontend reconstruction`：`webapp/frontend` 当前只有 adapters/Tauri，没有 `app/`/`pages/`；`docs/frontend-uiux-design.md:745-753` 与 Progress 已明确正式产品路由不可用。onboarding、capture、Tasks、History、Storage、Coach 与长期 plan UI 都不可达，这是单一已知 No-Go blocker，不重复拆成每个状态一个 finding。

### Pending Field Measurements

- `PF-01`：KovaaK baseline vs capture enabled non-Challenge vs Challenge+export；记录平均/1% low/p99 frame time、input latency、process/thread CPU、context switches、GPU 3D/Copy/Video Encode、VRAM 与功耗。
- `PF-02`：NVIDIA/AMD/Intel，1080p/1440p/4K 与 60/120/240 Hz；确认同 adapter hardware encode、384 MiB ring 和 export 对真实游戏的成本。现有 NVIDIA field evidence不能替代本轮 A/B 或 AMD/Intel。
- `PF-03`：1/4/8 kHz Raw，60/120/300 秒；记录 queue drops、ring expirations、snapshot flush latency、磁盘队列与 Analysis coverage/quality。
- `PF-04`：退出 KovaaK后等待 5 分钟并重启；确认旧 WGC/MFT threads、GPU/内存资源和 coordinator phase。
- `PF-05`：10/100/500 MiB MP4、100k/300k/1M Raw points、1/10/50 concurrent discoveries；记录 wall、CPU、private bytes/RSS、read/write bytes 与 default executor queue。

### Rejected Or De-scoped Candidates

- `RJ-01`：自动视频会退回 CPU readback/CPU encoder并拖慢游戏。当前 automatic path 要求同 adapter、D3D11-aware hardware H.264；不可用时视频 fail closed/degrade，CPU writer只是 diagnostic baseline。
- `RJ-02`：bounded queue 会反压并阻塞 Raw/game input。Raw/video producer 使用 `try_send`/drop-first；真实问题是证据质量和诊断是否正确表达，不是同步阻塞 producer。
- `RJ-03`：`candidate_limit=50` 本身是数据丢失 bug。它是已冻结的 bounded discovery 防线，只保证最新候选；是否导入更老 backlog 是产品行为决策。保留一个“历史积压不可见”UI/产品问题供 R11/R12 审计，不把它当作当前实现违反既有合同。
- `RJ-04`：public AnalysisResult 持久化全部 Raw trajectory。`_native_deterministic_v2()` 只保留 unit/point_count；全量数组只存在于局后内部与 local-only derived evidence。性能/容量问题成立，public payload 泄漏不成立。
- `RJ-05`：`continuous_tracking` 与 visual quality 的 `tracking` 是未声明别名漂移。前者是 Scenario `aim_family`/analyzer contract；后者由 `visual_signals.py` 独立冻结为 visual metric-family vocabulary，测试和 spec 均显式使用。当前没有把同一个字段写成两个名字。
- `RJ-06`：为减少行数立即把四个 family analyzer 强行做成完全表驱动。四者输入、事件和降级语义不同，当前 PRD 也没有第 5 个 launch family。`worker.py` 的多接点是未来扩展成本，列入 R08 的“新 family/新 source 前再改”，不作为今晚必须重构。
- `RJ-07`：跨 Python/TypeScript/Rust 的全部重复 validator/codec 都应合并。跨进程不可信边界必须独立验证；ACRI 有 shared golden fixture，Knowledge 使用 canonical JSON asset。只修已证实的接受集合 drift。
- `RJ-08`：因为未来可能接 Aim Lab/手部摄像头，现在就把 KovaaK capture/process allowlist和全部 Run API抽象成通用插件。PRD `7`/`11` 明确多游戏与手部摄像头是远期、非 v1；提前泛化会扩大采集权限和迁移风险。

## 8. 决策与修复排序

### 当前扩展性判断

- 现有 KovaaK process/window allowlist、KovaaK discovery/finalizer/parser chain 是符合 v1 最小权限的 source adapter，不应现在改成任意进程/动态插件。
- normalized `analysis_evidence`、SignalBundle extension registry、ScenarioProfile、family-specific analyzer、Coach/profile/plan stable refs 可以复用；未知 schema/channel/family继续 fail closed。
- 真正的多游戏迁移面位于 `sessions.kovaak_run_id`、`kovaak_runs`、`KovaaKRun*` API、AnalysisResult v2 `kovaak_run_ref` 和 `kovaak_run` artifact ownership。它们目前是合理硬耦合，但 Aim Lab 不能伪装成 KovaaK。

### 仅在点点批准 Aim Lab/多游戏后

1. 新 contract version 引入 source-agnostic `run_ref` + `source_kind` + source-specific normalized payload；旧 v2/KovaaK reader永久可读。
2. Aim Lab独立 source adapter产出 versioned Run candidate、clock capability、parser provenance和 normalized events；不得把格式塞进 KovaaK parser。
3. 只有可靠 window/clock/event source才能接自动 capture；否则保持手动 source或 outcome-only。
4. 新 family同时交付 ScenarioProfile、evidence extension、analyzer、quality/fixture/knowledge/real-Run Gate；届时再决定是否引入小型 analyzer descriptor。

### 仅在点点批准手部摄像头后

1. 先冻结 opt-in、local-only raw、camera/biometric/keypoint数据分类、默认不出站和可撤销删除语义；不能只靠现有 path/secret blocklist。
2. 建立 run-owned sensor artifact 的 owner/consent/calibration/retention/tombstone/storage合同；当前删除层只认 video/raw，不能借用普通 MP4。
3. 独立 camera adapter以明确 clock-offset、placement、occlusion、fps、calibration profile对齐既有 Run；不扩大 Raw Input的 KovaaK process gate。
4. 只把 versioned derived observation、coverage/confidence/alignment/calibration ref送入 Analysis/Coach；原始 frame/keypoint不进普通 API、日志或 Provider。
5. History/profile只比较同 calibration profile；未校准数据 descriptive/unavailable。前端采集 UI必须等待上述后端安全合同。

### 现在必须改

1. `P0-02` 测试 DB 隔离：这是安全恢复全量 backend suite 的前置条件；修复前继续禁止在继承未知 `DATABASE_URL` 的 shell 运行 `webapp/tests`。
2. `P0-03` DB transaction ownership：选择 DB-wide owner 或 per-transaction connection，补跨 store/heartbeat 并发回归，再恢复 deletion/reconciliation、plan/profile/provider/Run 原子性声明。
3. `P1-01` capture process-exit release：冻结 grace window，保证 drain 后 exactly-once release 和不退出应用即可重启 KovaaK。
4. `P1-04` stale uploading recovery：startup 原子 terminalize/cleanup，解除 owner 永久 429。
5. `P1-02`、`P1-05`、`P1-06` 证据质量链：把 Raw drops/expiry、frame PTS completeness、native result status/coverage/limitations 一路传入 analyzer/profile/Coach，禁止 partial evidence 得到无条件 1.0 正向诊断。

### 前端实施前

- `P2-10` 建立 AnalysisResult/error public projection；`P2-16` 再按公开 DTO/native commands 补齐 frontend adapter，不从旧 prototype 恢复 shape。
- `P2-03` 给 History/Storage 加分页和非阻塞 accounting；同时纳入 `P2-02` orphan category/用户管理，避免正式长期 History 一上线就线性退化或漏报占盘。
- `P2-14` 让 Tauri 管理随机端口、带 capability 的 Coach sidecar；这是 Provider onboarding/Coach 页面可用前置条件。
- `P2-06`、`P2-08` 修 validator/snapshot accept-set drift；`P3-01` 在 evidence endpoint 接线前把私有 projector 提升为稳定公共投影。

### 性能与运行策略 Gate

- `P2-01` finalizer QoS、`P2-13` 局后资源预算和 `PF-01` 至 `PF-05` 一起验证；先测真实 KovaaK 帧时间、CPU/GPU/RSS/I/O，再决定并发、采样或 artifact 表示。
- `P3-04` 三次 MP4 hash 在 10/100/500 MiB benchmark 证明可感知前不改；不得为省一次读取削弱 CV 前/commit 前 TOCTOU guard。

### 发布前

- `P2-15` 产品 CI、`P2-12` 锁定工具链/依赖审计，以及 `RG-01` packaging、`RG-02` notices、`RG-03` 三厂商/high-polling、`RG-04` frontend reconstruction 全部是 No-Go Gate。
- CI 必须晚于 `P0-02`，否则只是自动化一个可能触碰外部 DB 的危险命令；Desktop clean-machine、installer、签名、update/rollback 需独立 active plan。

### 需要点点拍板

- capture 退出 grace window；Raw 缺失时 limited 分析还是完全拒绝；finalizer 的“下一局流畅 vs 尽快可见”优先级；orphan retention/remove 语义；derived evidence 是否允许降采样。
- `P2-09` Knowledge 默认 strict family lock 还是只允许显式、带标注的 cross-family transfer；建议默认 strict。
- Web preview 的 trusted-proxy/unsigned dev 模式、hosted CI 与 secret 权限、Python/toolchain lock 更新策略、Desktop packaging/notice/release policy。
- 本轮没有需要修改 PRD 的新产品决定；PRD 最近提交是 2026-07-20，当前 dirty PRD 也已有改动。任何进一步 PRD 修改继续由点点拍板。

### 新来源获批后再改

- Aim Lab 与手部摄像头只按上面的触发式迁移合同实施。当前不要把 KovaaK process/window gate、Run API 或 capture 权限抽象成通用动态插件。

### 暂不值得处理

- `P3-02` lease-loss 冗余 revision、`P3-03` parity test cwd、`P3-05` TS 第一层 secret key drift、`P3-06` preview startup enforcement 可随相邻授权 Task 修，不单独启动重构。
- 不合并跨 Python/TypeScript/Rust 的独立不可信边界 validator；不为减行数把四个 family analyzer 强制表驱动；不把 `candidate_limit=50` 或 `continuous_tracking`/visual `tracking` 重新包装成 bug。

## 9. 验证日志

| 时间 | 范围 | 命令/证据 | 结果 |
|---|---|---|---|
| 2026-07-24 | 恢复基线 | `git status --short --branch`、`git diff --stat`、`git status --porcelain=v1 -uall` | `main` ahead 12；104 个既有 dirty 条目；总账此前不存在 |
| 2026-07-24 | R01-R03 静态复核 | `rg` + 精确代码范围：capture release、Raw barrier/drop、native trajectory、video revision、storage/recovery、runtime futures | 3 个 P1、5 个 P2 confirmed；5 组性能结论保留待实测 |
| 2026-07-24 | R02 focused tests | `pytest -q tests/test_visual_signals.py`；五个 analyzer/evidence focused suites | `59 passed`；`143 passed, 1 skipped`（agent 隔离运行） |
| 2026-07-24 | P2-13 主审复测 | inline synthetic `analyze_native_flicking()`，100,000 points，输入 points 在 `tracemalloc` 前构造 | 2.154 s，peak 71.73 MiB，内部结果 JSON 8.12 MiB，trajectory 100,000 points；本次 synthetic 因最小 anchor payload为 unavailable，仅验证重复构造/保留成本，不代表完整真实 Run wall time |
| 2026-07-24 | P0-03 主审复现 | 唯一 temp DB；T1 `BEGIN IMMEDIATE` + tombstone，T2 真实 `queue.heartbeat().commit()`，关闭/重开后跑真实 reconciliation | heartbeat 提前提交 tombstone；reconciliation 后 victim session 仍 `done`、workspace 已删除，P0 confirmed |
| 2026-07-24 | R04 parity focused | Node knowledge registry/parity + Python knowledge registry focused tests（agent） | Node `7 passed`；Python `41 passed`；同时 malformed duplicate section ref 证明 TS/Python accept/reject drift |
| 2026-07-24 | R07 focused suites | analyzer/evidence/profile/knowledge相关 fixture（agent） | `367 passed, 2 skipped in 17.74s`；未运行真实 capture |
| 2026-07-24 | Rust/capture test 尝试 | agent 使用当前 GNU toolchain 运行 `cargo test` | 缺 `dlltool.exe`，未进入测试；后续必须按 MSVC 入口重试 |
| 2026-07-24 | 其它 focused pytest 尝试 | capture/finalizer/run suites | Python 3.9 annotation compatibility 与既有测试 DB 锁导致 collection/setup 失败；未作为业务 finding，未触碰锁定 DB |
| 2026-07-24 | R09 安全边界复核 | sidecar/Tauri/Python 调用链、preview auth/deployment contract、AnalysisResult v2 public route | 最终 2 个 P2、1 个 P3；sidecar/preview 均按本机单用户与 No-Go guard 降级；未启动服务或读取 DB |
| 2026-07-24 | R10 Coach/frontend 与格式检查 | 完整 Coach Node 命令、frontend adapter tests、MSVC `fmt --check`、`git diff --check` | Coach `71/71`、frontend `4/4`、fmt 与 diff check 通过；Python suite 因 P0 未跑，build/check/test/clippy/package/audit/实机未跑 |
| 2026-07-24 | R11 文档一致性 | PRD/Architecture/Roadmap/Progress/active spec/plan/Development 与代码/Git 对照 | PRD 核心合同一致；确认 2 个 P0 closeout 冲突、1 个 P2 dirty-state 缺口、1 个 P3 快照歧义 |
| 2026-07-24 | R12 UI/UX + red-team | UI 合同、frontend adapter/backend DTO、全部 confirmed/release/doc finding 反向复核 | UI 状态合同充分；新增 1 个 adapter P2、1 个已知 frontend No-Go；7 项降级、2 组根因合并、P0/P1 lifecycle/quality 保留 |
| 2026-07-24 | 最终完整性检查 | finding ID/count、`npm.cmd --prefix webapp/frontend test`、`git diff --check`、AGENTS/CLAUDE SHA-256、索引目标、Git status | 27 个唯一 finding：`2 P0 / 5 P1 / 13 P2 / 7 P3`；frontend `4/4`；diff check 通过；Agent Contract 一致；索引目标存在；104 个既有 dirty 条目 + 本轮新增总账 |
| 2026-07-24 | Remediation Wave 1 | test DB isolation、task-owned SQLite transaction gate、capture exit drain/release；主审 focused Python/MSVC | DB/queue/provider/plan `115 passed`；capture Python `46 passed, 1 skipped`；Rust capture `13 passed` |
| 2026-07-24 | Remediation Wave 2 + size audit | Raw receipt、PTS/native quality、stale upload/QoS；删除未用 fake、修复前即通过的重复 test 与算术假覆盖 | Python `414 passed, 1 skipped`；MSVC Raw `24 passed, 1 ignored`；Rust fmt 与 scoped diff check 通过；Wave 2 估算 production/tests 各约一半，不再用整个 dirty diff 冒充本轮增量 |
| 2026-07-24 | Remediation Wave 3 | Knowledge malformed parity/secret/cwd；native trajectory single derive；dict-only snapshot、flat error 与 EvidenceSegment public leaf | 主审 Python `406 passed`；Coach `72 passed`；100k native synthetic 单次前后为 `5.449s / 89.82 MiB` 到 `4.464s / 63.88 MiB` tracemalloc peak；RSS 未测，不替代真实 Run |
| 2026-07-24 | 过度实现反审 | 三个 terra high agent 分别复核 Wave 2 增量，主审检查 nested public result 与 family caller context | 撤回只能过滤顶层的 v2 result projector；不扩大到新 nested schema；strict family 因缺少 server-derived caller context 保留 No-Go；Storage/frontend/sidecar/CI/release/P3 revision 不提前施工 |
| 2026-07-24 | 提交前安全全量矩阵 | 隔离 temp DB/KovaaK path 的全仓 pytest；Coach tsx loader；frontend adapters；Windows MSVC fmt/check/test；全仓 diff check | Python `1230 passed, 5 skipped`；Coach `72 passed`；frontend `4 passed`；Rust `70 passed, 7 ignored`；ignored 项均为显式实机/hardware Gate；未运行真实 KovaaK、Provider 或产品 DB |
| 2026-07-24 | 本地分批 commit | staged file list、cached diff check、领域边界复核 | `4558850` analysis、`2ae4d32` Coach/Knowledge、`84f96d9` platform/capture；本 ledger 与上游事实源随第四批 docs commit；未 push |

## 10. Checkpoint

- 当前阶段：review/remediation complete；当前 P0/P1 与小范围现行合同修复已通过三波主审并按分析、Coach/Knowledge、platform/capture、文档四批本地 commit。后续不再把 frontend、packaging、release、长期规模或低价值 P3 finding 转成今晚的业务代码增长。
- 当前边界：未启动 KovaaK/真实 capture，未读取或修改产品 DB，未新增前端页面，未修改 UIUX 文件；PRD 修改来自点点已批准的 Coach 产品语义。未 push/建分支。strict family caller context、完整 nested public result DTO、frontend/Desktop packaging/license/三厂商实机继续是 No-Go Gate。
- 下一动作：核对最终 Git status/commit log；不得把 deferred Gate 写成已修复，也不在未授权时 push。
- 恢复协议：新 session/compaction 后，先读本文件，再运行 `git status --short --branch`；如 HEAD 或 dirty 集合变化，记录新基线，不覆盖未知改动。
