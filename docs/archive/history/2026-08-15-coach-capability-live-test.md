# 2026-08-15 Coach 能力实测记录（CLI 直连真实 Provider）

> 性质：一次实机驱动的产品能力验证记录。测试环境为 standalone 后端（不开 Tauri、不开浏览器），Coach 通过 Node sidecar（`http://127.0.0.1:8765`）直连真实 LLM Provider（opencode-go / deepseek-v4-flash），对 `%APPDATA%\com.aimingcookie.desktop` 下的当日真实采集数据（91 个 Run、多个 Analysis）提问。
> 工具：`scripts/coach-cli.mjs`（本次新增的命令行 Coach 客户端，`node scripts/coach-cli.mjs "消息" [--session N]`，输出回复与本轮工具调用清单）。

## 一、测试方法

1. **环境**：standalone 三进程——FastAPI（uvicorn，端口 8000）、Coach sidecar（端口 8765）、分析 worker，全部以 `DATA_ROOT=%APPDATA%\com.aimingcookie.desktop` 指向真实桌面数据。
2. **方式**：对每项能力用一句自然语言指令驱动 Coach，检查两件事—— 是否调用了预期工具；（b）调用是否成功、结果是否正确。每次新 session 或续用既有 session。
3. **发现 bug 即修即重测**：本轮共发现 3 个 bug（见下），修复后重测该能力，最后做全量回归。

## 二、发现并修复的 3 个 bug

| # | Bug | 根因 | 修复 | 验证 |
|---|---|---|---|---|
| A | `scenario_memory.set` 无法写入场景记忆 | 哈希校验写死 64 位 hex，真实 KovaaK scenario_hash 是 32 位 MD5 | TS/Python 两侧校验改 32 位（`product-commands-write.ts` / `analysis_service.py`） | 重测成功写入 `config/scenario-overrides.json` |
| B | Coach 调后端命令报「桌面运行时令牌无效」 | 测试环境错位：`desktop-runtime.json` 残留上次 Tauri 会话的死端口；`uvicorn --reload` 丢失 `AIMING_COOKIE_DESKTOP_TOKEN` 环境变量 | 刷新 runtime 配置 + 直起 uvicorn（非产品 bug，是 standalone 测试环境的坑） | `/api/storage` 鉴权通过 |
| C | 重新分析已分析过的 Run 时 500 | `analysis_service.py` `os.link(视频, workspace/video.mp4)` 非幂等，二次分析目标已存在 → FileExistsError | 改幂等：目标存在且指纹匹配则复用硬链接，否则先删再链 | 重测成功产出新追踪分析（session 4） |

## 三、逐项能力测试结果

### 通过（真模型、真实数据验证）

| 能力 | 验证方式与观察 |
|---|---|
| `run.list` / `run.get` | 列出当日全部 14 局（含场景名与时间），准确 |
| `history.list` / `history.trend` | 两局 Ascended Tracking 趋势对比，正确调用 |
| `analysis.get` / `analysis.compare` | 读取追踪分析真实指标（SPARC -3.18、decel_frac 0.134 等）；两局对比成功 |
| `analysis.evidence.list` | 成功返回证据片段清单 |
| `analysis.events.list` | 成功返回事件序列 |
| `analysis.run_facts.get` | 成功（场景纠正流程中多次调用） |
| `profile.aiming.snapshot` | 成功（如实报告画像为空——数据事实） |
| `product.readiness.get` | 成功 |
| `analysis.create_from_run` | 场景纠正后重分析成功（修复 Bug C 后） |
| `analysis.delete` | 删除误标分析（session 1）成功，目录核对一致 |
| `training_plan.generate_draft` | 基于追踪分析起草计划成功（同时调了知识库） |
| `teaching_session.update` | 带课流程成功创建教学会话 |
| `scenario_memory.set` | 写入「Air Angelic = continuous_tracking」（修复 Bug A 后） |
| `peripheral_profile.get` / `.update` | 读取与更新握姿均成功 |
| `navigation.open` | 视频时间点导航指令成功调用 |
| `eloshapes.query` | 「60 克以内鼠标」返回真实库数据（1617 款，列出最轻 20 款） |
| `kovaak_scores.lookup` | 用真实 Steam 主页链接查询成功（返回两阶段完成度与段位） |
| `kovaak_scores.refresh_connected` | 正确返回「未连接账号」提示，调用本身成功 |
| `get_coach_knowledge` | 多轮触发；一次未命中后如实说「没查到，按实测数据解读」——防幻觉纪律生效 |
| 场景纠正→记忆→重分析闭环 | 完整跑通：用户纠正→写记忆→触发重分析→新追踪分析落盘（同 Run 双分析并存，旧分析可删） |
| 新用户冷启动（「你能干什么」） | 自我介绍覆盖分析/带练/回顾/查成绩，主动引导「先去打一局」，零工具调用、无免责堆砌 |

### 部分覆盖（组内命令未逐一触发）

- `analysis.events.*`：只触发了 `events.list`，`events.get/rank/filter/aggregate/co_occurrence/sequence` 未被自然语言触发（Coach 常以 `read` 文件达到同目的）；
- `analysis.evidence.signal_window` / `compare`、`analysis.outcomes.timeline`、`analysis.metrics.distribution`：未单独触发；
- `training_plan.*`：只触发了 `generate_draft`，`save/activate/pause/adjust/review/item.add/execution.record/retest.record` 未触发。

### 未测（缺真实素材/需前端）

- `analysis.retry`：本地无失败的 Analysis 可重试；
- 自动开讲：前端观察路径触发，CLI 测不到（此前实机已观察过一次成功）；
- Skill 主动加载：单测证明三 Skill 均可加载，但未在真实对话中确认 Coach 主动加载并引用。

## 四、值得记录的行为观察

1. **Coach 能识别数据与标注的矛盾**：对误标为 static_clicking 的 Air Angelic，它从数据形态（持续开火、无 flick 分割）主动指出标注可疑，并请用户确认——正是场景记忆设计所期望的交互。
2. **诚实汇报失败不编造**：Bug 修复前，它如实报告「记忆写不进（哈希位数不符，我不编造）」「重分析触发失败」，未谎报完成。
3. **话术收敛生效**：全轮测试无「这不能说明什么」式免责堆砌。
4. **已知小瑕疵**：Coach 偶发 `read [failed]`——对不存在路径的试探性读取（如首次读教学状态）。文件直读架构下的正常 probe 行为，不影响结果。

## 五、修复后的全量回归（零回归）

| 套件 | 结果 |
|---|---|
| Python 全量（`tests` + `webapp/tests`） | 1155 passed / 5 skipped / 0 failed |
| Coach runtime TS 全量 | 190 passed / 2 skipped（既有 live skip）/ 0 failed |
| 前端 unit + contracts + type-check | 149 passed / 0 failed |

## 六、遗留与后续

- 未触发的子命令清单见上「部分覆盖」，后续可用更明确的指令逐项逼出测试；
- `analysis.retry` 需造失败样本；
- 测试期间的改动（哈希位数、幂等冻结、CLI）已提交于 `feat/capture-generalization-knowledge-2026-08-15` 分支（commit `07eb136`）；
- standalone 测试环境的两个坑（runtime 配置端口、reload 丢 token）值得沉淀进 DEVELOPMENT.md 的 standalone 测试说明（本次未写入）。
