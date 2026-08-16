# Aiming Cookie Current Progress

> Updated: 2026-08-16. This is a current implementation snapshot, not a product or architecture source. Earlier detailed status is retained in [`archive/history/2026-08-10-progress-prelaunch-history.md`](archive/history/2026-08-10-progress-prelaunch-history.md).

## Current Product Direction

- First activation requires a tested Provider plus enabled Windows Raw Input and KovaaK-window capture. The normal product surfaces are Coach, History, and Settings.
- Coach automatically selects the strongest valid Run tier: `multimodal`, then `input_native`, then `video_fallback`. A Run with no valid tier is not a normal History record; Coach explains the failure and repair path.
- Pre-install KovaaK Stats/Performance files are not imported or displayed. Optional KovaaK score linking remains onboarding context only.

## Implementation Status

- **Architecture rewrite complete**（SQLite → JSON 文件）：canonical 数据以 JSON 文件直接保存在 `DATA_ROOT` 下——`runs/{id}/meta.json`（KovaaKRun）、`sessions/{id}.json`（Analysis Session/job）、`analyses/{session_id}/`（渐进式披露）、`training/`、`config/`、`profile.json`。Coach 用 Pi `JsonlSessionRepo` 把对话持久化为 `conversations/{id}.jsonl`，并直接读写文件系统；SQLite、Python `coach_*` 代理层与 context-attach 机制已移除。
- Documentation has been realigned with the above contract; the retired OpenDesign handoff and historical superpowers materials are reference-only.
- Backend automatic tier selection, Run readiness, server-side Analysis tier selection, first-launch routing, and mandatory onboarding UI are implemented and covered by the focused validation below.
- 场景分派已泛化（2026-08-15 决策）：拆除 exact-hash 白名单，任何场景按多级识别进入大类管线；场景分类记忆持久化于 `config/scenario-overrides.json`。该批连同 2026-08-16 的知识体系批次已合并 main（`c24c60a`）。
- Coach 能力实测（2026-08-15）：standalone CLI 直连真实 Provider 逐项验证，核心能力全部通过，实机发现并修复 3 个 bug（场景记忆哈希位数、重分析视频冻结幂等、测试环境令牌）；全量回归零失败。完整记录见 [`archive/history/2026-08-15-coach-capability-live-test.md`](archive/history/2026-08-15-coach-capability-live-test.md)。
- Desktop Coach requests now use the Node sidecar directly. Python remains the local Analysis API/ingestion/worker runtime; it is not a desktop Coach request proxy.
- Real Tauri Provider, KovaaK field capture, hardware load, packaged installer/signing/updater/download, clean-machine onboarding, and cross-vendor capture validation remain release gates.

## Verification

以下数字为 2026-08-13 会话的结果，早于 2026-08-15 批次；2026-08-16 在 main 工作树上的一次重跑见 2026-08-15 会话记录末尾（不构成对该分支的验证）。

- Python full suite: 1154 passed, 5 skipped; the backend suite `pytest webapp/tests` passed 635, 3 skipped. The architecture rewrite removed the SQLite / `coach_*` proxy-era tests.
- Coach runtime TypeScript suite: 152 tests, 150 passed, 2 skipped; Node native Analysis covers all three input tiers, Python worker v3 snapshot reads, cleanup, and auto-discovered KovaaK path derivation.
- Frontend unit/contracts: 179 passed; frontend type-check passed.
- Rust/Tauri MSVC: fmt, check, clippy passed; tests 93 passed, 0 failed, 7 field-only tests ignored.
- `desktop-coach-provider.spec.ts` now exercises the product UI and observes `/v1/agent-runs`, but the real Provider field test was not rerun in this cleanup. Its skip is not counted as passing validation.
- `git diff --check` passed. Full Python, production browser Playwright, real Tauri, real KovaaK, hardware, and Provider field checks remain to be reported separately if run.

## 2026-08-16 Session Changes

- **分支体检与文档治理**：修正 PROGRESS 对 2026-08-15 批次的滞后；补跑 Rust MSVC gate 四项全绿（feat 分支验证收官；feat fmt 干净意味着 main `runtime.rs` 的格式偏差随合并即修复）；`db9799b` 已把 08-15 实测记录入档 main。
- **Coach 子命令深测**（subagent，standalone CLI 直连真实 Provider，数据副本隔离）：昨日未覆盖项逼出测试——**22 项通过**（`events` 表族 6/6、`training_plan` 生命周期 9/9、`analysis.retry` 完整失败→重试链路、signal_window/outcomes.timeline/metrics.distribution）、`evidence.compare` 部分失败（segment 范围实现 bug）；**Skill 主动加载 3/3 确认**（真实对话中主动加载 teaching/peripheral-reference/kovaak-data-reference 并按 skill 结构行为）。发现 **9 个 bug**（只记录未修，高严重度 4 个：`events.filter` 谓词静默假阳性、write 命令错误吞成通用 internal_error、`evidence.compare` segment 范围永远失败、工具失败后越权直写 `training/plan.json` 不符合同）。系统性根因：`run_product_command` 参数 schema 全开放、每命令真实参数形态无处可发现——与知识库检索截断不可见同属「可发现性」缺口。报告：[`archive/history/2026-08-16-coach-subcommand-deep-test.md`](archive/history/2026-08-16-coach-subcommand-deep-test.md)。
- **x76 wiki 知识评审**（subagent，103 篇通读）：9 条 gap 建议（Tier1×4 + Tier2×3，Tier3×2 暂缓）+ 8 条 fold-in 碎片 + 10 类跳过项；claim 天花板 community_practice（community_organization 源限制），robots 声明 ai-train=no，入库须转述改写 + URL/SHA-256 引用。报告：根目录 `x76-wiki-knowledge-review-2026-08-16.md`（未跟踪，待点点过目）。
- **知识库文件化方案已定**（未实施）：物化 `knowledge/` 目录（index + entries/）+ 检索截断可见（`total_matches`/`has_more`）+ `MAX_RESULTS` 放宽 + prompt 引导 read 下钻；与命令参数契约暴露同批考虑。实施分两步：先架构（物化），后内容（registry v8 入库 wiki 知识）。
- **批一/批二施工完成（subagent fixer，feat 分支本地 commit 未推送）**：`af7f2e5` 修复深测 bug 1/2/3/4/7/9（filter 谓词校验、write 错误透传、compare ref 归一、业务状态文件直写防护扩至全部 7 个 native 写命令管理文件、mtime 降辅助信号、eloshapes 空缓存重载）；`25ff494` 知识库文件化四件套（sidecar 启动物化 `DATA_ROOT/knowledge/` index+entries、prompt 引导 read 下钻、`get_coach_knowledge` 加 total_matches/has_more、MAX_RESULTS 3→8 含 Python parity）+ 命令参数契约（events 表命令 table_ref 校验、eloshapes 未知键双重拒绝、events.list 截断标 total/truncated）。验收：TS focused 211 tests（209 过/2 既有 skip）、Python 全量 1161/5、全部新测试先红后绿。
- **批三知识入库 + 一致性修复（subagent，feat 分支本地 commit 未推送）**：知识体系一致性审核（只读）发现 7 条真矛盾（内容层干净，集中在 registry↔管线接口层），报告在根目录 `knowledge-consistency-audit-2026-08-16.md`；2 条设计张力（static cue 话术优先级、前瞻 metric_refs 去留）待点点决策。`4aa0622` 入库 registry v8：27→36 条（x76 wiki Tier1×4 + Tier2×3 + strafe 阶梯 explanation_only + 手速↔cm/360 换算 + fold-in 12 条升级）、community.x76-wiki 源（转述零 7-gram 重叠、逐篇 URL+SHA-256）、9 条逐条检索召回验证。`d4b2aa5` 修复全部矛盾并恢复 Fitts 条目（registry 终态 37 条）：M1 标注方案（不替 T2 张力做决定）、M2 恢复 research.speed-precision.fitts 修死路 + 发射信号可命中知识的不变量测试、M4 settle_time_ms→settle_duration_ms 对齐管线、M7 移除死 token 并修正 diagnosis/advice 的 metric 前缀形态（**此前诊断管线的知识检索交集恒空——静默失效已修**）。验收：Python 1167/5、TS 212+2skip、物化 37 条、parity 同步。
- **知识消费面实测（主会话亲自执行，同款 standalone + 真实 Provider，feat @ d4b2aa5）**：通过 6 / 部分 1 / 失败 3。数据/指标类场景全过（分析讲解查库且 M1 修复行为体现、overshoot→DPI 条目全链路通、计划起草挂库、批二 read index 下钻与物化 37 条即工作）；三个失败同根因——**检索入口没覆盖用户语言**：概念问答裸答（cm/360 零工具调用）、strafe 阶梯自然语言映射不到库内 token 信号、具名词条（bardpill）不可按名检索且 miss 后模型编造解释。修复方向：prompt 概念/具名方法引导 + 未命中禁止自行解释、检索挂词条名、入库验收加自然语言变体。报告：[`archive/history/2026-08-16-knowledge-consumption-live-test.md`](archive/history/2026-08-16-knowledge-consumption-live-test.md)。
- **术语社群校订（`31af2d3`，点点人类修正）**：术语对照表 8 处修正（停稳/拉过头/刻意拉少一点/转火/目标阅读/重新跟住/复位/动作分段）+ 补录（预判、趴握/抓握/指握、大臂/手腕、低敏/高敏）+ cue 消解（对话说"这局你只注意一件事"，概括用"口诀"）。真实验证："停稳"落地零"收稳"、"转火"自然使用、"两种流派"话术转换生效、教学句零 cue 字样。
- **知识入口统一为 read（点点决策，`6a1c14d`+`eefcbd7`）**：退役 `get_coach_knowledge` 工具与 TS 检索栈（打分召回/MAX_RESULTS/parity，净删 605 行），LLM 知识消费唯一路径 = `read knowledge/index.json` → 下钻 `entries/`；Python 分析管线内部消费不动。同时落话术修复：概念/具名方法查库纪律（未命中如实说、禁编造）、中文术语对照表（flick→甩枪、settle→收稳等 12 条）、讲解前必须 read index 按 signal/metric_refs 定位条目并用条目口径讲。真实 LLM 回归五场景全过：讲解 index→条目→input-native 边界口径进讲解；strafe 难度一击命中阶梯条目（此前工具路径 4 次查询 miss）；bardpill 命中且内容/claim/反证据全对；概念题如实声明；"收稳了再点"话术落地。判定依据：37 条规模下开架浏览全面优于黑盒查询，查询词对齐失败类问题连根消失。
- **实机测试（Tauri product-path，点点亲自测试）**：采集链路实测通过——3 局新采集（Valorant Small Flicks / Fireworks Flick / VSS GP9）三源 attached、视频导出成功；**场景泛化真机验证**（Valorant Small Flicks 按挑战形态+开火模式判定 → static_clicking baseline，130杀/1280按钮样本/每杀9.85次的点射数据进 limitations）；自动开讲触发正常。发现 8 项问题（#1 capture controlError 日志噪音不阻塞未定位、#2 视频就绪需手动刷新、#3 会话标题不吸顶、#4 @ 标记误用于报指标、#5 话术僵硬、#6 研究术语非人话、#7 免责过载、#8 视频回看引导不足）+ 隐性收获：**MP4 PTS 0 与 canonical 窗口差 decodePreroll（本局 121.97ms）**——现有 visual_video_time_mapping 未携带该修正，@ 视频跳转疑似系统性偏约 0.12s（未修，待实施 v2 映射时一并）。
- **实机修复批落地（subagent fixer-2，main 本地 `94abb5c`+`884a8a7` 未推送）**：话术批——@ 双向修正（带看画面才用+必配观察点、报指标禁用、每次 1-3 个回看引导）、口语化规则、研究术语人话对照（开环→凭感觉一把甩过去等）、免责默认不写（完整边界由落地页承载，点点负责）+附加（无基线不做"正常"评价、历史对照是 Coach 自身职责）；前端批——「本次讨论」区块移出滚动区吸顶常驻+项目名按钮开视频面板、历史页视频就绪 5s 轮询自动刷新（终态自动停）。验证全绿。
- **静态 CV 管线定案（点点决策，未实施）**：走**无训练路线**——Lab 主导色自动找靶色 + 现有 `detect_color_observations_v2` 检测器，四局真素材全过（红球×青天 3.21/帧、黑球、黑胶囊零漏检、人形 2.28/帧；39-66ms/帧零新依赖）。YOLO 训练线关闭（点点判断"单模型通吃多靶型不现实"）。关键过程资产：点点 6 次肉眼校验纠正了 AI 连环分析错误（三形状先验/靶色黑色/无绿底/无青球/红靶+青天空/HUD 误框），确立"背景必有反差"不变量与"检测×定位互补"事实（BGSub 对黑白反差强、颜色聚类对彩色靶强）；两份调研报告（pipeline-proposal、github-survey）与 spike 产物（`E:\DevCache\temp\cv-spike|cv-eval`）留档；AGPL 对本项目 OK（非商业）。素材事实：69 Run/35 场景，仅 16 局有视频（自动录像修复前的局无 MP4）；人形/胶囊类训练素材缺口（训练线已关闭，无影响）。
- **待办（不急，点点已确认记录）**：deepseek-v4-flash 会把内部推理文本（"我应该…"）写进可见回复，观感受损，疑模型特性；损坏视频（截断到 64KB）静默降级为三源分析，回复不提示视频证据缺失。均见深测报告第四节。小遗留：events.filter 的 matched_count 在命中超限时低报；evidence.compare 的 event 精确匹配语义未验证；新处方的中文本地化待 plan review 流程；glossary 轻量层待点点确认；auditor 的 2 条设计张力待拍板；E:\DevCache\temp\kfix\wt 与 cv-train 临时目录待清理；**CV P0 施工（3-5 天）与 preroll v2 修正待开工**；#1 controlError 噪音排查；实机修复批 2 commit 待推送验收。

## 2026-08-15 Session Changes

实机驱动的一批修复与泛化改造，位于分支 `feat/capture-generalization-knowledge-2026-08-15`（commits `e45c5c7`、`8c6ddb6`、`07eb136`，领先 main 3 个提交，已推送远端、未合并）：

- **采集链路修复（实机复现并修复）**：Rust 侧退出时等待在途导出连接（根除 WinError 10053）、帧队列 8→32、视频缺口 100ms 容差；Python 侧赛后残留正确切分（窗口内全收/窗外剔除）、watcher 重试上限、视频三路径失败不再连坐 Raw trace、45 秒 release 死锁、exportReplay 客户端重试环。
- **场景分派泛化**：拆除 4 场景 exact-hash 白名单，任何场景按多级识别进入大类管线；新增开火模式判定器（追踪按住 vs 点击点射）；分析完成且 Provider 可用时自动开讲一次；baseline 档挂视频回放与文案纠偏。
- **场景分类记忆**：`config/scenario-overrides.json` + `scenario_memory.set` native 写命令 + 用户纠正后重分析闭环。
- **知识库 v7**：registry v7 补挂基础档指标标签（decel_frac 等）；检索 metric 归一化（bare / family / `metric:` 三形态命中）。
- **guided teaching 重建**：`teaching/session.json`（`coach_teaching_session.v1`）+ `teaching` skill + `teaching_session.update` native 写命令，替代 per-run TeachingTurnContract 快照。
- **架构重写审计 14 项修复**：视频透传、幽灵命令、重试去重、原子写、子进程超时、长回复截断等；另有 Steam 成绩查询接通、Pi skill 加载器 Windows 路径修复、话术收敛与死代码清理。
- **前端 Coach-first IA 重写**：既有 WIP 与本批改动交织，随 `8c6ddb6` 一并提交（AppShell/History/Settings/SessionRail 等大面积改动，`components/task3` 下架）。
- **实机后续修复（`07eb136`）**：`scenario_memory.set` 哈希校验位数写错（64→32，真实场景哈希为 32 位 MD5）；重分析已分析过的 Run 时视频冻结 `os.link` 非幂等；新增 `scripts/coach-cli.mjs` 命令行 Coach 客户端。

验证状态：本批以真实 KovaaK 实机会话驱动发现并修复问题，分支验证已完整。2026-08-15 修复后全量回归零失败（Python 1155 passed / 5 skipped、coach-runtime TS 190 passed / 2 skipped、前端 unit+contracts 149 passed / 0 failed，详见实测记录）；2026-08-16 补跑 Rust MSVC gate 四项全绿（fmt --check、check --locked、test 98 passed / 0 failed / 7 ignored、clippy `-D warnings`）。feat 分支 fmt 干净意味着 main 上 `runtime.rs` 的格式偏差（445fc5a/4c0667f 引入，`cargo fmt --check` 在 main 失败）随本分支合并即修复。2026-08-16 在 main（445fc5a）工作树上的重跑除 fmt 外亦全绿（Python 1109 passed / 5 skipped、coach-runtime 168 passed / 2 skipped、前端 170 passed / 0 failed、Rust test 93 passed / 0 failed / 7 ignored）。另 main 缺少 e45c5c7 新增的 `app-data/`、`viscose-youtube/` ignore 条目，合并前 main 工作树会显示这些目录为未跟踪。未运行：frontend E2E（`test:e2e`）、production build、Pi packages/ai workspace 测试、compileall。工作区另有 4 张未提交的 Playwright 截图基线（`screenshots.spec.ts` 的 `toHaveScreenshot` 基线，Coach-first IA 重写后生成）与根目录 `logo.jfif`，去向待定。

## 2026-08-13 Session Changes

- Completed the authorized SQLite → JSON-file architecture rewrite: removed `db.py`/SQLite, the Python `coach_*` proxy modules, and the context-attach mechanism; Coach now reads/writes `DATA_ROOT` directly and persists conversations via Pi `JsonlSessionRepo`.
- Made Analysis Session reservation atomic across Python upload/import and Node `analysis.create_from_run`, with failed input setup removing the reservation and workspace.
- Implemented Node-native Run-to-Analysis input freezing with `multimodal > input_native > video_fallback`, canonical v3 snapshots, worker-readable fingerprints, scenario resolution, and auto-discovered KovaaK path reuse.
- Fixed Agent Run retry message reuse, Provider-wait recovery, TeachingSession stale-CAS handling, canonical context projection, Python-compatible context dedupe keys, and duplicate Agent Run readers.
- Kept the desktop Coach product adapter on Node sidecar routes and removed the unused frontend Python soft-start adapter. Browser-only fallback and Python Analysis routes remain compatibility surfaces.
- Hardened frontend sidecar reconnection, stale batch workflows, context clearing, frameless window controls, and SessionRail contract preservation.
- Added product-path Provider E2E coverage without copying credentials from a developer DB; the real Provider field run remains open.

This validation does not prove packaged release readiness. It does not cover installer packaging, signing, updater, clean-machine onboarding, real KovaaK four-source field capture, long-running hardware load, or cross-vendor GPU behavior.

## 2026-08-11 Session Changes

Full-codebase audit completed across 5 subsystems with ~70 findings. Key fixes applied this session:

- **Backend deduplication**: monolithic `worker.py` / `coach_commands.py` / `kovaak_run_store.py` split into focused modules (source validation, family analysis, visual producers, run projection, snapshot codec, context refs, confirmations, guidance, etc.).
- **v0 turn schema cleanup**: unified to a single v1 turn contract path; removed obsolete v0 multi-path branching in `teaching-policy.ts` and `turn.ts`.
- **v3 diagnosis context injection fix**: Coach can now see analysis data (diagnosis, events, metrics) through the evidence bridge; previously the context was assembled but not reaching the LLM turn.
- **Metric localization**: `metric_definitions.py` is now the single source of truth for metric display names (Chinese); frontend and backend both consume it.
- **Coach tool calling fix**: product command tools are now retryable and inject context correctly; `teaching_session.update` is registered as a product command.
- **Video evidence navigation**: added 2-second seek padding so clicking an evidence segment lands slightly before the event, not after.
- **FOV/DPI/sensitivity KeyError tolerance**: missing optional fields no longer crash analysis or frontend rendering.
- **Codex review regression fixes**: schema synchronization between frontend contracts and backend DTOs; deletion confirmation flow restored.
