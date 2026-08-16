# 2026-08-16 Coach 子命令深测记录（昨日未覆盖项逼出测试）

> 性质：实机驱动的产品能力验证记录，承接 [`2026-08-15-coach-capability-live-test.md`](2026-08-15-coach-capability-live-test.md) 的「部分覆盖」清单。目标：把昨日未被自然语言触发的子命令逐项逼出、验证结果正确性；发现 bug 只记录不修。
> 环境：独立 git worktree（`feat/capture-generalization-knowledge-2026-08-15`，HEAD `07eb136`）+ 真实数据副本（1.66 GB 复制到独立目录）+ standalone 三进程（uvicorn 8000 / sidecar 8765 / worker），全部写操作落在副本上。Coach 仍经 `node scripts/coach-cli.mjs` 驱动，直连真实 Provider（opencode-go / deepseek-v4-flash）。

## 一、测试方法

1. **环境隔离**：`git worktree add` 独立目录 checkout feat 分支（主工作树不动）；`cp -r` 复制 `%APPDATA%\com.aimingcookie.desktop` 全量数据做 DATA_ROOT；`AIMING_COOKIE_DESKTOP_TOKEN=dev-test-token` 与副本内既有 `desktop-runtime.json`（端口 8000）匹配；uvicorn 直起（不用 `--reload`）。Python 用主仓 `.venv`（两分支 requirements 无差异）；pi 的 `node_modules` 用 junction 指向主仓（lockfile 无差异，仅 2 个 pi 源文件在 feat 有改动，必须用 worktree 自己的源码）。
2. **逼出方法**：每个子命令一句自然语言指令 → 看是否调用预期工具 + 抽查数字与磁盘 artifact（events.json / evidence.json / plan.json / sessions/*.json）一致。一次未触发→点名工具名再试→仍因参数形态失败→把正确参数形态喂给它，区分「功能可用性」与「可发现性」两类问题。
3. **数字核对**：每个表族工具的输出都与 Node 直读 artifact 的 ground truth 逐项比对（本文所有「与 ground truth 一致」均指此流程）。

## 二、逐项结果

### analysis.events.*（表族 6 命令）— 6/6 通过（均需喂参数格式）

前置发现：昨日「未被触发」的根因不是模型不想用，而是**当前两个真实 Analysis（2/4，均 continuous_tracking）的 evidence artifact `event_bundles` 为空**，表目录（`analysis:N:table:<event_kind>`）根本不存在，模型只能 fallback 直读 events.json。本次先用 run 54004（1wall 6targets small，点击场景）生成 analysis:5（117 条 static_flick 事件入 bundle），表族才有对象。

| 命令 | 结果 | 验证 |
|---|---|---|
| `events.rank` | 通过（喂 table_ref 格式后） | 117 条全评估，decel_frac 降序 top5（0.864/0.853/0.847/0.845/0.816）与 ground truth 完全一致 |
| `events.get` | 通过 | flick:115 全字段（accel 82ms/decel 476ms/pe 0.994/sparc -3.57）逐项一致 |
| `events.filter` | 通过（喂 predicates 形态后） | path_efficiency<0.7 → matched_count=2（flick:30、51），与 ground truth 一致 |
| `events.aggregate` | 通过 | 按 quality 分组 n=117，decel_frac 均值 0.657（ground truth 0.6567） |
| `events.co_occurrence` | 通过 | decel_frac>0.7 ∧ reverse_ratio>0.3：both=40/left_only=1/right_only=71/neither=5，right_given_left=0.976，全部精确一致 |
| `events.sequence` | 通过 | early/middle/late 三段 movement_duration_ms 均值 487.1/487.3/503.7，与 ground truth 完全一致 |

自然语言首选仍是 `events.list` + 直读文件（排序类问题模型自己做）；点名工具后模型猜错参数 1-2 次才被喂对。`events.list` 对 591 条事件截断到 200 条返回（LIST_MAX），结果里无截断标记——模型基于截断 payload 自行排序时把真正的第 2 名（flick:115）漏掉且未察觉（见 Bug 8）。

### evidence / outcomes / metrics 族 — 3 通过，1 部分失败

| 命令 | 结果 | 验证 |
|---|---|---|
| `analysis.evidence.signal_window` | 通过（自然触发） | 首次漏 channel_keys 得到规范 invalid_parameters，模型自纠后成功；87 点、raw_counts、focus 窗口与 artifact 一致 |
| `analysis.outcomes.timeline` | 通过（喂枚举参数后） | 正确参数（scope="whole_run"、mode="overview"、series=["performance.kills","performance.score"]）下成功，59s 窗口分桶数据与 run 时间轴吻合；此前模型把 scope 猜成对象 2 次 |
| `analysis.metrics.distribution` | 通过（喂 key 前缀后） | metric_keys 必须带家族前缀（`static_clicking.decel_frac`），裸 key 报 not_found；正确后返回 value 0.6592/sample_count 117 |
| `analysis.evidence.compare` | 部分失败 | analysis 范围：跨场景家族（continuous_tracking vs static_clicking）metric key 前缀不同，永远无可比 key——可比性门槛本身合理，但模型无从得知 key 要带前缀，猜 2 次失败。segment 范围：**实现 bug，永远失败**（Bug 3）。模型最终 fallback 直读 metrics 手工对比，给出的数值本身正确 |

### training_plan.* 生命周期 — 9/9 通过（4 项需喂参数）

完整链路在副本上跑通，磁盘终态与合同一致：`plan.json` status=paused、version=2、items=1；`history.jsonl` 2 条（exec completed + retest matched/decrease）。

| 命令 | 结果 | 备注 |
|---|---|---|
| `generate_draft` | 通过（喂 plan_payload 后） | 首次模型传 {analysis_ref, plan_type, focus} → internal_error；随后触发 Bug 4 的 write 越权（见下），删除坏文件后带 plan_payload 重调成功（plan:777db4…） |
| `save` / `activate` | 通过 | 一次成功，draft→saved→active |
| `item.add` | 通过（喂 item_payload 后） | 模型 3 次传 `item`（变体）均 internal_error，喂对后成功（plan-item:e376…） |
| `execution.record` | 通过 | 一次成功（plan-execution:8bce…，completed） |
| `retest.record` | 通过（喂参数名后） | 首次失败后模型明确说「不瞎猜了，请给我契约」；喂对后成功（retest:9b5b…，matched/decrease，result 如实填 0.66→0.61） |
| `pause` | 通过 | active→paused 一次成功 |
| `adjust` | 通过（喂 plan_payload 后） | 首次传 {adjustments, reason, item_adjustments} 失败；喂对后 version 自增 v2、组数修改落盘 |
| `review` | 通过 | 状态/版本/执行/复测历史汇总与磁盘一致（过程中模型虚构过 `history.list` 命令名，被 schema 联合校验正确拒绝后用文件工具兜底） |

### analysis.retry — 通过（完整失败→重试链路）

副本上没有失败样本，且管线预检非常完备：0 字节 trace、中段损坏 trace（buttons unsupported bits）、视频截断（静默降级为 trace-only 分析仍成功）都造不出 failed 状态。最终用「杀 worker 制造中断」成功：

1. 触发 run 54005 分析 → 处理中 kill worker → session 8 卡 running；
2. 编辑 session 8（attempts=3、lease 过期）→ worker 重启的 stale 恢复将其标记 failed（`stale_lease_exhausted`，retryable=true）——这是产品真实的失败路径；
3. Coach 调 `analysis.retry(analysis:8)` → retried=true、session 10（parent=8）排队成功；
4. session 10 处理时死于源校验（见 Bug 7，非测试构造）；修复源稳定性后再 `analysis.retry(analysis:10)` → session 11（parent=10，attempt 3）**跑完，analysis:11 落盘**。

行为观察：面对「重试失败的分析」，模型默认选 `analysis.create_from_run` 重建而不是 `analysis.retry`（对「失败的那个会话本身」需明确指定）；retry 的两次非法调用（漏 analysis_ref / 传 run id 当 analysis id）都得到干净的结构化报错。

### Skill 主动加载 — 3/3 通过（真实对话确认）

| Skill | 证据 |
|---|---|
| teaching | 「带我练一局」→ 模型主动 read `prompts/skills/teaching/SKILL.md` → 回复严格按 skill 结构：真实证据引入（@12.1s/@25.8s）、心智模型、单一 cue（峰值位置往前挪）、单变量约束、要求用户复述对齐；teaching_session.update ×3 |
| peripheral-reference | 换鼠标咨询 → 主动 read SKILL.md → 抓握/手长/重量分类推荐（VGN 蜻蜓 F1、ATK、毒蝰 V2 Pro、Darmoshark M3，均与 skill 知识一致）→ 调 eloshapes.query |
| kovaak-data-reference | 起草训练计划时主动 read SKILL.md 并引用；`get_coach_knowledge` 多轮触发（含按 entry_ref 精确命中 `knowledge:static.flicking-terminal-control@2`） |

## 三、Bug 清单（只记录，未修）

| # | 严重度 | Bug | 复现 | 根因（代码位置） |
|---|---|---|---|---|
| 1 | 高 | `analysis.events.filter` 静默忽略谓词，返回全部行且状态 succeeded | 模型传 `{field,operator,value}`（顶层）或 `{conditions:[…]}` 而非 `predicates:[…]` → matched_count=117（真实答案 2），无任何告警 | `evidence-native.ts:889-891`：空谓词=全匹配；未知键不拒绝、无参数校验。模型看到的 schema 是全开放的 `additionalProperties:true`，正确形态无处可发现 |
| 2 | 高 | write 命令族真实错误被吞成通用 internal_error | `training_plan.generate_draft` 缺 `plan_payload` → 报「product command could not be completed」，模型无法自纠（昨天恰好传对才通过） | `product-commands-write.ts:999-1007`：catch 里算出了 `error.message`（"plan_payload is required"）但 else 分支丢弃，返回通用文案 |
| 3 | 高 | `analysis.evidence.compare` segment 范围永远失败 | evidence_refs 传两个 segment_ref → native_error "evidence is unavailable" | `evidence-native.ts:1104`：直接 `requireArtifact(ctx, ref)`，未先 `analysisRefFromSegment(ref)` 归一；`loadArtifact` 只认 `^analysis:\d+$`（:55-56） |
| 4 | 高 | 工具失败后 Coach 用原始 `write` 直写业务状态文件，且不符合同 | generate_draft internal_error 后，模型手写 `training/plan.json`（自造 plan_ref，缺 plan_id/status/version）→ 后续生命周期命令全部失配 | 行为层：write 工具对业务状态文件无防护/无提示；模型「完成任务」倾向压过了合同。本次靠人工删文件 + 喂参数才恢复正轨 |
| 5 | 中 | `analysis.events.rank` 缺 table_ref 抛裸 TypeError | 模型传 `{analysis_ref,event_type,sort_by,order}` → native_error "Cannot read properties of undefined (reading 'match')" | `evidence-native.ts:246-248`：`tableRef.match()` 无类型校验；与 filter 不同（后者至少有结构化 invalid_parameters） |
| 6 | 中 | `eloshapes.query` 静默丢弃全部未知过滤参数，返回全目录 | 模型传 `{hand_size,grip,max_weight_g}`（合理命名）→ succeeded + total_matches=1617（全库），过滤全失效 | `eloshapes-native.ts:121-132`：只保留 allowlist 键，其余静默丢；与 Bug 1 同族 |
| 7 | 中（真实环境） | KovaaK 源文件（E 盘）mtime_ns 观测值漂移 → 跨时间窗 source 校验随机失败 | 昨日 ingest 记录 ...619000，今天读出 619100/619300（sha256/size 恒定不变）；create 后秒级校验能过，retry 数分钟后校验必挂：session 10 死于 `source_unavailable`，提示「请重新提交分析」 | `worker_source_validation.py:30-63`：{sha256,size,mtime_ns} 全等比较；mtime_ns 在该文件系统上亚微秒级不稳定。sha 稳定、mtime 脆弱——建议 mtime 降级为辅助信号（未实施） |
| 8 | 低 | `events.list` 截断无标记 + 模型兜底排序不可靠 | 591 条事件截 200 条返回（无 truncated 字段）；模型对 payload 自行排序给出错误 top5（漏真第 2 名 flick:115，且 payload 内 42/16 也排错），语气确凿无提示 | `evidence-native.ts:804-842`（截断）+ 模型算术不可靠（deepseek-v4-flash）。工具正确时无此问题 |
| 9 | 低 | eloshapes catalog 首次缺失即永久缓存空数组 | sidecar 启动后第一次查询时文件不存在 → 本进程内后续查询永远 catalog_unavailable，文件恢复也不重试 | `eloshapes-native.ts:46-51`：`_catalogCache=[]` 后 `!== null` 短路。sidecar 重启即恢复 |

**系统性结论（Bug 1/2/5/6 及大量「猜 2-3 次才对」的共同根因）**：`run_product_command` 的 `parameters` 是完全开放的 `Type.Object({}, {additionalProperties:true})`，每个子命令的真实参数形态（table_ref 格式、predicates 数组、metric key 家族前缀、scope 枚举、item_payload/plan_payload 命名）只存在于实现里，模型无从发现；错了要么被静默忽略（假阳性结果），要么报不透明错误（无法自纠）。这既解释了昨天「未被自然语言触发」（fallback 直读文件更快更稳），也是本次多个数据正确性风险的来源。修复方向（未实施）：按命令收紧参数 schema / 在工具描述或知识库里暴露每命令契约 / 未知参数显式拒绝 / 关键错误信息透传。

## 四、值得记录的行为观察

1. **诚实与防幻觉纪律持续生效**：filter 工具返回假阳性 117 条时，模型核对数据后仍报出正确答案 2 条并主动说「工具没跑通，结果是我核对的」；参数反复失败后明确说「不瞎猜了，请给我契约」——宁可问也不编造。
2. **内部推理文本泄漏**：deepseek-v4-flash 多轮把「我应该…我直接给…」式思考过程写进可见回复（如 rank 第二轮、teaching 开场），观感受损，疑似 Provider/模型特性。
3. **损坏视频静默降级**：51MB 视频截断到 64KB 后分析仍成功（raw_input/performance/stats 三源，无视频），回复未提示视频缺失——用户无从知道视频证据没了。
4. **retry vs create 的默认选择**：模型对「重试失败的分析」默认理解为 create_from_run 重建；retry 工具需点名。
5. **预检完备**：0 字节 trace、按键位非法 trace 都在入队前被结构化拒绝，没有产生半途垃圾状态；stale 恢复（attempts 耗尽→failed）按设计工作。
6. **环境坑（供复现者）**：本机 PowerShell/robocopy 的文件写入被系统拒绝（疑安全软件），复制大目录要用 bash `cp -r` 或 Node；`artifacts/eloshapes/` 未入 git，worktree 起环境要额外复制，否则 eloshapes 全家不可用；中文路径下 cmd.exe 内联命令编码不可靠，一律走脚本文件。

## 五、遗留与后续

- Bug 1-9 待点点评审与修复（建议优先 Bug 1/2/3/4，都是「工具说了谎」或「合同被绕过」级别）；
- 参数可发现性的系统性整改需要产品决策（收紧 schema vs 暴露契约文档），不在本测试范围；
- 环境坑 6 中 eloshapes artifacts 未入库问题建议在 DEVELOPMENT.md standalone 说明中补一句；
- 本次未测：`analysis.events.sequence` 的 run_decile/adjacent 模式、`evidence.compare` 的 event 范围（与 segment 同一 bug 路径，预计同样失败）、教学会话完整多课闭环（昨日已过带课流程，本次只验证了开课）。

## 六、测试产物与清理

- 测试全程数据写入副本（`training/plan.json` paused v2、`history.jsonl` 2 条、analyses 2/4/5/7/9/11、sessions 8/10 failed + 11 done、teaching/session.json），真实数据目录零写入（唯一触碰是只读复制与硬链接读取）；
- 结束时已杀全部测试进程、删除 worktree、`git worktree list` 复核、数据副本与日志目录已删；
- 本文档为唯一保留产物（写回主工作树 `docs/archive/history/`）。
