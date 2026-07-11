> ⚠️ **时间勘误（2026-07-09 核实 git 史）**：本报告 D-4 称 narrator「17 个月内从未被使用」系 subagent hallucination。实际：narrator.py 2026-06-28 引入、agent.py 2026-07-05 引入并替代、narrator 退役约 4 天。代码事实属实。07-09 已决策：删 narrator。

# YAGNI / 推后项 Review

> **日期** 2026-07-08 · **reviewer** YAGNI reviewer · **scope** `kovaak_tracker/` + `webapp/`
> **基准** `docs/PRD.md` §7 功能边界（v1 / B / C / 远期）+ §11 非目标 + CLAUDE.md

## 健康度：A-（零硬 YAGNI 违规；主要欠账是"过期原型未清理"）

点点在 v1 scope 内克制得很好——PRD 划为 B / C / 远期的功能（云端 history 同步、付费墙 / credits、pynput/mss 采集、Tauri/Electron 打包、④ 训练计划前端、多游戏抽象、tracking 前端入口）**没有一项被提前误做**。前端只有 4 个屏（upload / processing / report / coach），全部 v1 scope；后端 LLM budget 是防滥用运营控制，不是付费墙。

主要问题是 v1 早期迭代留下的**原型代码未清理**：旧 flicking pipeline（静止间隙切分 + `decel_smoothness`）在 PROGRESS [A] 完成后已被公平指标 pipeline 替代，但 `extract_flicks` / `compute_metrics` / `analyze_flicks` / `run_flicking_analysis` / 整个 `aligner.py` / `pan_tracker.analyze_flicking_video` 全链 ~470 行死代码仍在 `__all__` 导出，CLAUDE.md 还把它列为产物（`flicking_metrics.json` / `flicking_segments.csv`）。同类问题：`coach/narrator.py`（150 行）作为 manual fallback 保留，但运行时只走 `agent.py`，导致 `providers.py` 三个 backend 的 `generate()` 方法也跟着成为事实死代码。

---

## 1. PRD 推后项检查（YAGNI 违规清单）

**0 条违规。** 逐项核查：

| PRD 推后项 | 阶段 | 仓库内是否提前做 | 验证方法 |
|---|---|---|---|
| 云端 history 同步 | B | ❌ 未做 | `Grep "cloud_sync\|sync_to_cloud\|remote_history"` 零命中；`progress.py` 只写本地 JSONL（`output/history/sessions.jsonl`），webapp 把 history 放 SQLite（本地 dev db），无云端推送 |
| 付费墙 / credits | B | ❌ 未做 | `Grep "stripe\|lemonsqueezy\|paywall\|subscription\|credits"` 仅命中 docs + `globals.css`（样式表无语义）；`llm_budget.py` 是日预算防滥用，不卡用户付钱（v1 免费） |
| 录屏 / 鼠标采集（pynput / mss） | 远期 | ❌ 未做 | `Grep "pynput\|mss\|pyautogui\|keyboard\|mouse_event\|screen_capture"` 零命中 |
| 桌面打包 Tauri / Electron | 另 spec | ❌ 未做 | `Grep "tauri\|electron"` 在 `package.json` / `package-lock.json` / `tsconfig.json` 零命中；Next.js 仍按 dev/server 模式跑 |
| ④ 训练计划前端 | B | ❌ 未做 | `planning.py` 后端已实现（PRD §4 价值点 4，OK）；前端 `Grep "Plan\|training_plan"` 在 `webapp/frontend/**` 零命中——正确推迟到 B 阶段 |
| Tracking 前端入口 | C | ❌ 未做 | `advice_tracking.py` v1 接通 coach 是设计接受的（task spec 明确不算提前）；前端 upload 页只收 mp4 + KovaaK CSV，无 tracking 视频上传路径 |
| 多游戏支持 | 非目标 | ❌ 未做 | 无 `game_type` / `game_id` / 抽象 game 层；`csv_parser.GAME_YAW` 是 KovaaK's CSV 的 Sens Scale 查表（cm/360 算式需要），不是多游戏抽象 |
| 社交登录 | 非目标（国际化再加） | ❌ 未做 | 无 Apple/Google/GitHub SDK 痕迹 |
| Dashboard 独立页 | 非目标（合并进 history） | ❌ 未做 | 见 §4，dashboard.py 已 Phase 1B 删除 |

---

## 2. Dead Code 清单

### D-1 · `flicking.py` 旧 pipeline 全链（~280 行，可删）

**位置**：
- `kovaak_tracker/flicking.py:35-262` — `FlickSegment` / `FlickMetrics` / `FlickAnalysis` 数据类 + `extract_flicks` / `compute_metrics` / `analyze_flicks` 函数
- `kovaak_tracker/flicking.py:303-318` — `_summarize`（仅供 `analyze_flicks` 用）
- `kovaak_tracker/flicking.py:321-374` — `export_flicking` + `run_flicking_analysis`
- `kovaak_tracker/flicking.py:613-621` — `__all__` 旧条目

**验证无引用**：ripgrep 全仓库 `extract_flicks|compute_metrics|analyze_flicks|run_flicking_analysis|export_flicking|FlickSegment|FlickMetrics\b|FlickAnalysis\b` 的非 docs / 非本文件命中 = 0。运行时（`webapp/backend/worker.py`）走 `analyze_flicking_fair_summary`（公平指标 pipeline）。

**为什么还留着**：PROGRESS [A]（commit 5c3aef8, 2026-06-28）切换了主线入口但没删旧 chain；docs（`flicking-aim-coach.md` §7、`PROGRESS.md`）把它标为"历史入口，不再推荐"，但代码层未回收。

**连带可删**：
- `kovaak_tracker/flicking.py:29` `from .aligner import Alignment, align` 这行 import 也成为孤儿
- `kovaak_tracker/pan_tracker.py:31-36` 的 `run_flicking_analysis` 导入（仅供下面的 D-2 用）

**建议**：删。v1 scope 完全用不到。"历史入口"的语义已在 docs 留底，代码层不留墓碑。

---

### D-2 · `pan_tracker.analyze_flicking_video`（~50 行，可删）

**位置**：`kovaak_tracker/pan_tracker.py:199-245`（含 docstring）；`__all__` 条目 line 481。

**验证无引用**：`Grep "analyze_flicking_video\b"` 全仓库非 docs 命中 = 0；唯一引用是 `pan_tracker.py:14` 自己的 docstring。

**为什么还留着**：它是 D-1 旧 chain 的对外入口，D-1 不删它就不会被引用。

**建议**：删。连带可把 `pan_tracker.py:34` 的 `run_flicking_analysis` import 也清掉。

---

### D-3 · `aligner.py` 整个文件（159 行，可删）

**位置**：`kovaak_tracker/aligner.py:1-159` 全部（`AlignedKill` / `Alignment` / `align` / `coverage_report`）。

**验证无引用**：`Grep "from .aligner|from kovaak_tracker.aligner|align\(|Alignment\b|AlignedKill|coverage_report"` 全仓库命中只在 `flicking.py:29`（D-1 的 import）+ `aligner.py` 自己 + 0 个测试。无 runtime 调用，无测试覆盖。

**为什么还留着**：D-1 旧 chain 唯一消费者，D-1 删了它就成孤儿。

**建议**：删。整个文件清空。

---

### D-4 · `coach/narrator.py`（150 行，判断为"该重新决定"）

**位置**：`kovaak_tracker/coach/narrator.py:1-151` 全部。

**验证**：`Grep "narrator"` 命中很多，但**运行时引用**只有：
- `coach/__init__.py:4` docstring 提及（惰性 import 不拖累纯逻辑模块，不是导入 narrator）
- `coach/agent.py:17-18` docstring 注释："narrator.py 保留作 manual fallback，未在运行时被本模块调用"
- `coach/providers.py:30` 注释："Existing LLMBackend.generate stays for narrator.py fallback compatibility"

实际**调用 narrator 函数的只有** `tests/coach/test_narrator.py`（自证测试）。runtime（`report.py`、`routes.py`、`worker.py`）全走 `agent.py` 的 `narrate_diagnosis / narrate_progress / narrate_plan / chat_with_coach`。

**核心问题**：design spec（`2026-07-05-aiming-coach-agent-design.md` §9）原定"删整个 narrator.py"，实现修正改"保留作 manual fallback"。但 manual fallback 从未定义触发路径——没有 CLI 入口、没有 flag、没有 env 切换。"fallback" 实际是**未删除的同义词**。代价：`agent.py` 和 `narrator.py` 两套 prompt + 两套 payload 序列化逻辑并存（同 diagnosis 的 `build_user_prompt` 与 `_serialize_diagnosis` 各写一遍），每次改 prompt 都要同步两处。

**判断**：**倾向删**。若真要保留 manual fallback，正确做法是（a）独立 CLI `python -m kovaak_tracker.coach.narrator <diagnosis.json>`，或（b）在 `report.build_report` 加显式 `use_agent: bool = True` 开关。当前"保留但不接"是最坏情况——既不删，也不真用。

**如删则连带**：
- `tests/coach/test_narrator.py`（155 行）
- `coach/providers.py` 三个 backend 的 `generate()` 方法（见 D-5）
- `LLMBackend` Protocol（D-5）

---

### D-5 · `providers.py.LLMBackend.generate()` 三个实现（~30 行）

**位置**：`kovaak_tracker/coach/providers.py:23-24`（`LLMBackend` Protocol）+ `AnthropicBackend.generate` (line 105-110) + `OpenAICompatBackend.generate` (line 192-198) + `DeepSeekBackend.generate` (line 365-371)。

**验证无引用**：`Grep "\.generate\("` 在非测试代码命中 = `narrator.py:65, 105, 134`（D-4 的三条入口）+ `tests/coach/test_narrator.py` 的 fake backend。Runtime（agent loop）走 `messages_create`。

**为什么还留着**：和 D-4 一体。providers.py line 30 注释明确："Existing LLMBackend.generate stays for narrator.py fallback compatibility."

**建议**：随 D-4 一起决定。删 narrator 则这层也清；保留 narrator 则继续保留。

---

### D-6 · `video.py.save_uploaded_video`（~5 行）

**位置**：`kovaak_tracker/video.py:20-23`。

**验证无引用**：`Grep "save_uploaded_video"` 全仓库命中 = 只有本文件 + CLAUDE.md（描述模块时列了它）。无调用者。

**为什么还留着**：早期 Streamlit app（`app.py`）现在用 `tempfile.NamedTemporaryFile` 直接写（line 47-49），不再走这个 helper；可能更早的 dashboard 版本用过。

**建议**：删。CLAUDE.md 那行描述也同步删掉。

---

### D-7 · `flicking.FlickFairMetrics.direction_deg`（~6 行）

**位置**：`kovaak_tracker/flicking.py:420`（字段定义）+ `flicking.py:583-584`（计算）+ `flicking.py:608`（写入 dataclass）。

**验证无引用**：
- `_summarize_reference`（`pan_tracker.py:266-270`）的 `names` 元组**不含** `direction_deg` → 永远不进 summary
- `advice.compare_table`（`advice.py:223-227`）的 metrics 列表也不含 → 永远不进对比
- `advice.advise` 无 `direction` finding
- `progress.TREND_METRICS` 不含

也就是说 `direction_deg` 在每 flick 上算了，塞进 `FlickFairMetrics`，但**从来没有任何下游消费**。06-theory-flicking.md §12 已独立发现这点。

**建议**：删。同类`path_length_deg` / `endpoint_peak` 还在 summary 里参与对比，属于"惰性但消费中"，不在本条 scope。

---

### D-8 · `agent_tools.make_get_comparison` 多余 `trend` 参数（小）

**位置**：`kovaak_tracker/coach/agent_tools.py:323` `def make_get_comparison(trend: dict, comparison: list[dict])` — 函数体只用 `comparison`，不用 `trend`。

**为什么还留着**：签名上为了和 `make_get_trend(trend)` 对称，看起来像一组。

**建议**：删 `trend` 参数（callers 都传两个，要同步改 `build_progress_tools` line 402）。

---

### D-9 · `__pycache__/dashboard_data.cpython-39.pyc`（孤儿缓存）

**位置**：`kovaak_tracker/__pycache__/dashboard_data.cpython-39.pyc`

**验证**：`find . -name "dashboard*"` 只命中这一个 .pyc，**无对应 .py 源**。CLAUDE.md 说 dashboard.py 在 Phase 1B 删除；这个缓存是当时遗留。`.gitignore` 应已忽略 `__pycache__/`，但磁盘上仍占空间。

**建议**：本地 `rm` 即可（属 CLAUDE.md §5 例外：可再生的 cache）；无需纳入代码变更。

---

## 3. 过度工程清单

### O-1 · narrator + agent 双轨维护（最大）

**位置**：`coach/narrator.py` 全文 vs `coach/agent.py` 的 `_serialize_diagnosis` / `DIAGNOSIS_SYSTEM_PROMPT` 等。

**问题**：同一职责（diagnosis → 中文讲解 prompt 构造 + 序列化）有两套实现并行存在。`narrator.build_user_prompt` 和 `agent._serialize_diagnosis` 字段选择、JSON 结构几乎一致；`BASE_SYSTEM_PROMPT` 和 `DIAGNOSIS_SYSTEM_PROMPT` 文本 90% 重叠。任何 prompt 调整、字段增减、措辞修正都要同步两处，否则 fallback 与主路径行为漂移。

**判断**：narrator"fallback"既未接也不打算接，是**名义 fallback，实质死代码**（见 D-4）。建议合并到 agent.py 单轨。

---

### O-2 · `advice_tracking.advise_tracking` 接受未用参数

**位置**：`kovaak_tracker/advice_tracking.py:66-82`

**问题**：签名是 `(self_summary, reference_summary=None, cm_per_360=None, ball_w=None)`。但：
- `reference_summary` 函数体一次都不读
- `cm_per_360` 函数体一次都不读
- docstring 解释："v1 is self-only (no reference comparison; sensitivity note is handled by flicking advice when shared meta is provided)"

这是**为未来 B 阶段（reference 对比）占位的 API surface**。当前 v1 自参考就够了，参数空挂着。属轻度 YAGNI——单看每条 1-2 行，但累积起来让接口语义模糊（caller 会以为传了会有效果）。

**判断**：可接受（docstring 明确标了未用）；若想更干净，砍掉这两个参数，等真做 reference 对比时再加。

---

### O-3 · `agent_kb.KnowledgeChunk.signal` 字段无查询入口

**位置**：`kovaak_tracker/coach/agent_kb.py:11-19`（TypedDict 定义）+ 大量 chunk 填了 `signal` 值（如 `"sparc low"` / `"two_stage"`）。

**问题**：chunk 上有 `signal` 字段，每个 chunk 都填了值；但 `_build_indexes()` 只构建 `BY_TOPIC: dict[str, list[KnowledgeChunk]]`（line 599-604），**没有 `BY_SIGNAL` 索引**。所有 retrieval（`_make_fetch_by_source` line 276-289）按 topic 查。`signal` 字段是纯元数据，不参与任何查询。

PROGRESS.md 2026-07-05 续二记录了"BY_SIGNAL 已删"，所以**当前 state 是已知设计**——但每个 chunk 还在写 signal 值，形成"半死元数据"：填字段不报错，但代码不读。

**判断**：可接受（文档化的未来索引扩展位，留着等真做 signal-based retrieval）。轻微过度。若长期不做，应删字段简化 TypedDict。

---

### O-4 · 前端"待接通"占位按钮（合法）

**位置**：
- `webapp/frontend/app/page.tsx:284-292` — footer 的 Privacy / Terms / Contact 三个 disabled 按钮
- `webapp/frontend/app/sessions/[id]/report/ReportView.tsx:199-211` — "导出 PDF" / "复测" 两个 disabled 按钮（title="待接通(切片 4)"）
- `webapp/frontend/app/sessions/[id]/coach/CoachView.tsx:452-460` — "A-B 循环(占位)" 按钮

**判断**：**不算过度工程**。这些是 UI 上的未来路径占位，明确标注"待接通 / 占位"，不是隐藏的复杂度。PRD §6.1 onboarding 流程明确要做"教学时刻"，这些按钮起到预告作用。保留。

---

### O-5 · `routes.py` X-User-Id header（合法占位）

**位置**：`webapp/backend/routes.py:31, 47` + `webapp/frontend/lib/api.ts:24-26`

**判断**：**不算过度工程**。注释明确："切片 1 占位，切片 3 加 Clerk 后换 session"。这是分阶段实现的明示占位，v1 dev 用 "dev" 字符串作 user_id，生产换 Clerk。属合理演进路径，不是猜测性抽象。

---

## 4. 已弃用保留项判断

### R-1 · `tracking.py` CSRT 混合追踪 — **留**

**位置**：`kovaak_tracker/tracking.py` 全文（CSRT tracker 为主，HSV 检测为回退）。

**判断**：CLAUDE.md 已说明 flicking 弃用 CSRT，但 **tracking 仍用**。`app.py`（Streamlit 校准 UI）line 19, 158-171 调 `run_tracking_analysis`；`calibrate.py` CLI 也调它。PRD 把 tracking coach 列为 C 阶段接通，但 v1 的 advice_tracking + calibration_cli 工具链保留 CLI-runnable 是合理演进。**留**。

---

### R-2 · `coach/narrator.py` — **倾向删**（见 D-4）

理由见 D-4 / O-1。"保留作 manual fallback"在 17 个月内从未被使用过一次（runtime 100% 走 agent），且 spec 原本就要删。当前 state = 维护双轨 prompt/序列化的成本 + 零收益。

---

### R-3 · `dashboard.py` — **已确认删**（无残留源）

CLAUDE.md 说 Phase 1B 删了；`find . -name "dashboard*"` 在 `kovaak_tracker/` / `webapp/` / 根目录下**只有 `__pycache__/dashboard_data.cpython-39.pyc` 这一个孤儿缓存**，无 .py 源。`webapp/frontend` 用 Next.js 4 屏（upload/processing/report/coach），不再有 dashboard 页。

**残留清理**：仅本地 .pyc（见 D-9），删不删代码无影响。

---

### R-4 · 根目录脚本（`app.py` / `Analyze.py` / `calibrate.py`）— **留**

CLAUDE.md 把它们标为"tracking 时代，CLI 仍可跑"。PRD v1 scope 不要求 tracking 前端，但保留 CLI 工具链是合理演进（tracking 重构 v2 时仍可用）。所有脚本都是薄包装（`Analyze.py` 25 行、`calibrate.py` 25 行），不是过度。

**留**。

---

### R-5 · `coach/providers.py` 多 provider config — **留**

`providers.json` 有 6 个 provider（anthropic / openai / deepseek / openrouter / groq / local）。看似多，但：
- DeepSeek 是 PRD §9.1 默认（`LLM_PROVIDER=deepseek`）
- Anthropic 是 spec 设计的备选（`docs/superpowers/specs/2026-07-05-aiming-coach-agent-design.md`）
- local（Ollama）是 dev 测试用
- openrouter / groq 是 config 占位（未测但配置在）

**判断**：保留。这是合理的 provider 抽象，load_backend 的 switch 一行加一个，不是过度。

---

## 5. 总评

| 维度 | 评级 | 说明 |
|---|---|---|
| PRD scope 克制 | **A** | 0 条 B/C/远期 功能被提前误做；前端 4 屏严格在 v1 内 |
| Dead code 清理 | **C+** | ~470 行旧 flicking chain + 150 行 narrator 双轨未回收；docs 已标"历史入口"但代码层没清 |
| 过度工程 | **A-** | 只有 narrator 双轨是显著问题；其他都是文档化的演进占位 |
| 弃用判断 | **B+** | dashboard 已删，tracking 保留合理；narrator 判断需要明确决定（当前悬置） |

### 最值得清理的 3 项（按 ROI 排序）

1. **D-1 + D-2 + D-3 一起删**（~470 行）—— 旧 flicking pipeline + aligner.py + analyze_flicking_video，零 runtime 引用、零测试覆盖、零 API 暴露。删了 CLAUDE.md / docs 同步修一行即可。**最高 ROI**。

2. **D-4 narrator.py + D-5 providers.generate + test_narrator.py**（~340 行）—— 消除双轨维护。但需要明确决定"manual fallback 不要了"，比 D-1 决策成本高。

3. **D-6 + D-7 + D-8 + D-9 零散清理**（~20 行 + 1 个 .pyc）—— 单条价值小，但顺手做能让仓库更紧。

### 关键文件路径

- 旧 flicking chain: `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\kovaak_tracker\flicking.py:35-374`
- aligner.py: `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\kovaak_tracker\aligner.py`
- analyze_flicking_video: `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\kovaak_tracker\pan_tracker.py:199-245`
- narrator: `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\kovaak_tracker\coach\narrator.py`
- providers.generate: `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\kovaak_tracker\coach\providers.py:23-24, 105-110, 192-198, 365-371`
- save_uploaded_video: `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\kovaak_tracker\video.py:20-23`
- direction_deg: `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\kovaak_tracker\flicking.py:420`
- 孤儿 .pyc: `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\kovaak_tracker\__pycache__\dashboard_data.cpython-39.pyc`
