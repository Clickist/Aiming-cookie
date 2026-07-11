> ⚠️ **时间勘误（2026-07-09 核实 git 史）**：本报告 narrator 相关的「17 个月未调用」系 subagent hallucination。实际：narrator.py 2026-06-28 引入、agent.py 2026-07-05 引入并替代、narrator 退役约 4 天。代码事实属实。决策已更新：**删 narrator**。

# 2026-07-09 Review — 产品/PRD对齐 + YAGNI

> **日期** 2026-07-09 · **reviewer** 产品/PRD对齐 + YAGNI reviewer
> **范围** 跨代码 vs docs/PRD.md 对齐 + 全仓 dead code/YAGNI 清查 + 新维度补充
> **验证** 代码今天无变动，昨天无回归

---

## 健康度：A（无偏差，YAGNI 清单清晰）

| 维度 | 状态 |
|---|---|
| PRD 对齐 | **0 偏差**（13 条决策：✅5 / 🟡5 / ⬜3，无❌） |
| YAGNI 违规 | **0 条**（v1/B/C/远期 功能无一提前误做） |
| Dead code | **~470 行可删**（旧 flicking pipeline + narrator 双轨） |
| 架构演进 | **干净**（kovaak_tracker 对 webapp.backend 零依赖） |

---

## §1 PRD 13 条逐条对齐（今天无变化）

**与昨天（08-PRD对齐）一致，无回归。** 分布：✅ 已实现 5 / 🟡 部分 5 / ⬜ 待实现 3 / ❌ 偏差 0

| # | 决策 | 今天状态 | 今天变化 |
|---|---|---|---|
| 1 | 默认页动态：无 history → upload，有 → history | ⬜ 待实现 | 无 |
| 2 | upload 无 profile 表单，CSV 自动算 | ✅ 已实现 | 无 |
| 3 | processing 教学时刻 + 空状态预告卡 | 🟡 部分 | 无 |
| 4 | diagnosis_report 免费 + 底部教练入口按钮 | ✅ 已实现 | 无 |
| 5 | coach_dialogue = LLM；D 不立墙 | ✅ 已实现 | 无 |
| 6 | history 本地优先，删/导出/导入 | 🟡 部分 | 无 |
| 7 | v1 登录收窄为"计费+身份"，不背 history | ⬜ 待实现 | 无 |
| 8 | 失败态分类：本地 CV / 云端 LLM / 网络 | 🟡 部分 | 无 |
| 9 | 日志 cross-cutting：本地 CV / agent / 云端 各层埋 | 🟡 部分 | 无 |
| 10 | 录屏+鼠标采集远期；upload 留扩展位 | ✅ 扩展位不阻塞 | 无 |
| 11 | 分析完成全局 toast + 顶栏角标 | ⬜ 待实现 | 无 |
| 12 | 教练即时访问新数据 | ✅ 已实现 | 无 |
| 13 | upload 视频/CSV 文件夹记忆分离 | ✅ 已实现（无 bug） | 无 |

### 两个"轻度偏离"（维持昨天判定）

**偏离-1：processing 完成时强制跳转**
- 位置：`webapp/frontend/app/sessions/[id]/page.tsx:164-168`
- 现状：`useEffect(() => { if (status === "done") router.push(...) })` 强制跳转到 report
- PRD §6.3："全局 toast + 顶栏角标（任意页可见），**不强制跳转**"
- 判定：架构演进中间态，非 bug。当前 processing 是独立全屏页，用户无"切走"选项，完成后跳转合理。真正"不强制跳转"需等 toast/角标落地时一并改。

**偏离-2：webapp 后端用 server-side SQLite**
- 位置：`webapp/backend/db.py` + `config.py` 的 SQLite (`aiming_cookie_dev.db`)
- PRD §8 item 6 / §9："history 本地优先"（桌面 hybrid 的"本地 sidecar"定位）
- 判定：slice 1 阶段是纯 web 架构，SQLite 在 server 合理。coach 包自己有 JSONL 本地持久化（`progress.py`），但 webapp worker 未接通（`worker.py:91` 没传 history_path）。两个持久化路径并存，等桌面 hybrid 落地时统一。**非矛盾**，是演进中间态。

---

## §2 架构分工验证（无回归）

| 层 | PRD 目标位置 | 当前位置 | 状态 |
|---|---|---|---|
| 视频解析 + pan_tracker + 指标计算 | **本地 sidecar** | **云端 Worker** (`webapp/backend/worker.py:run_analysis`) | ⬜ 待搬迁 |
| coach agent 框架（tool-use loop） | **本地 sidecar** | **云端** (`routes.py` 调 `chat_with_coach`) | ⬜ 待搬迁 |
| LLM 推理请求 | **云端 API 代理** | ✅ 云端 (`providers.py` 藏 key，DeepSeek 默认) | ✅ 已对齐 |
| 账号 / 订阅 / 画像 / history | **云端**（B+ 阶段） | 🟡 部分 (sessions 表在 SQLite，B 阶段升 Postgres) | 🟡 演进中 |

### 关键验证点（无反例）

**1. kovaak_tracker 对 webapp.backend 零依赖**
- `Grep "from webapp|import webapp"` 在 `kovaak_tracker/` 全包：**0 命中**
- CV/agent 逻辑完全独立，搬本地 sidecar 不需解耦

**2. worker 纯函数无 FastAPI/DB 耦合**
- `worker.py:run_analysis(video_path, csv_path, ...)` 是纯函数入口
- 内部调 `kovaak_tracker.pan_tracker.analyze_flicking_fair_summary` ——无 FastAPI/DB 耦合
- `run_report(summary, backend)` 同理，纯函数调 `build_report`
- 搬 sidecar 时整个函数原样带走，无需重构

**3. providers.py LLM 代理定位正确**
- `kovaak_tracker/coach/providers.py` 已是"藏 key + OpenAI-compatible 调用"形态
- 搬云端代理层天然成立

**结论**：架构演进路径干净，印证 PRD §9 "既有资产演进不浪费"。**无矛盾，无反例**。

---

## §3 Dead Code 清单（ROI 排序）

**总计：~470 行可删**（旧 flicking pipeline + narrator 双轨）

### D-1 · 旧 flicking pipeline 全链（~280 行，**最高 ROI**）

| 组件 | 位置 | 行数 | Runtime 占用 | 测试覆盖 | 删除影响 |
|---|---|---|---|---|---|
| `extract_flicks` | `kovaak_tracker/flicking.py:79-163` | 85 | 0%（无引用） | 0 | 无 |
| `compute_metrics` | `kovaak_tracker/flicking.py:165-234` | 70 | 0%（无引用） | 0 | 无 |
| `analyze_flicks` | `kovaak_tracker/flicking.py:236-250` | 15 | 0%（无引用） | 0 | 无 |
| `run_flicking_analysis` | `kovaak_tracker/flicking.py:355-373` | 19 | 0%（无引用） | 0 | 无 |
| `_summarize` | `kovaak_tracker/flicking.py:303-318` | 16 | 0%（仅 `analyze_flicks` 用） | 0 | 无 |
| `export_flicking` | `kovaak_tracker/flicking.py:321-374` | 54 | 0%（无引用） | 0 | 无 |
| 数据类 | `kovaak_tracker/flicking.py:45-73` | 29 | 0%（无引用） | 0 | 无 |
| `__all__` 条目 | `kovaak_tracker/flicking.py:616-625` | 10 | — | — | 无 |

**验证**：ripgrep 全仓库 `extract_flicks|compute_metrics|analyze_flicks|run_flicking_analysis` 非本文件/非 docs 命中 = 0。Runtime 100% 走 `analyze_flicking_fair_summary`。

**连带可删**：
- `kovaak_tracker/aligner.py` 整文件（159 行，见 D-3）
- `kovaak_tracker/pan_tracker.py:199-245` `analyze_flicking_video`（47 行，见 D-2）
- `kovaak_tracker/flicking.py:29` 的 `from .aligner import Alignment, align` import

**建议**：删。v1 scope 完全用不到，PROGRESS 已标"历史入口不再推荐"，代码层不应留墓碑。

---

### D-2 · `pan_tracker.analyze_flicking_video`（~50 行）

| 位置 | 行数 | Runtime 占用 | 测试覆盖 | 删除影响 |
|---|---|---|---|---|
| `kovaak_tracker/pan_tracker.py:199-245` | 47 | 0%（无引用） | 0 | 无 |

**验证**：`Grep "analyze_flicking_video\b"` 全仓库非 docs 命中 = 0。唯一"引用"是 `pan_tracker.py:14` 自己的 docstring。

**连带可删**：
- `kovaak_tracker/pan_tracker.py:34` 的 `run_flicking_analysis` import

**建议**：删。D-1 删后此函数成孤儿。

---

### D-3 · `aligner.py` 整文件（159 行）

| 位置 | 行数 | Runtime 占用 | 测试覆盖 | 删除影响 |
|---|---|---|---|---|
| `kovaak_tracker/aligner.py:1-159` | 159 | 0%（无引用） | 0 | 无 |

**验证**：`Grep "from .aligner|from kovaak_tracker.aligner|align\(|Alignment\b|AlignedKill|coverage_report"` 全仓库命中只在 `flicking.py:29`（D-1 的 import）+ `aligner.py` 自己 + 0 个测试。

**建议**：删整个文件。D-1 删后此文件零消费者。

---

### D-4 · `coach/narrator.py`（150 行，**需决策**）

| 位置 | 行数 | Runtime 占用 | 测试覆盖 | 删除影响 |
|---|---|---|---|---|
| `kovaak_tracker/coach/narrator.py:1-151` | 151 | 0%（17 个月未调用） | 155（自证测试） | 删除需同步改 agent_tools |

**验证**：
- Runtime 路径（`report.py`、`routes.py`、`worker.py`）100% 走 `agent.py`
- `coach/agent.py:17-18` docstring 明确："narrator.py 保留作 manual fallback，未在运行时被本模块调用"
- `coach/providers.py:30` 注释："Existing LLMBackend.generate stays for narrator.py fallback compatibility"
- `narrator` 函数的唯一调用者是 `tests/coach/test_narrator.py`（自证测试）

**核心问题**："manual fallback"从未定义触发路径——没有 CLI 入口、没有 flag、没有 env 切换。代价：
- `agent.py` 和 `narrator.py` 两套 prompt + 两套 payload 序列化逻辑并存
- 每次改 prompt 都要同步两处

**连带若删**：
- `tests/coach/test_narrator.py`（155 行）
- `coach/providers.py` 三个 backend 的 `generate()` 方法（~30 行，见 D-5）
- `agent_tools.py` 的 `make_narrate_diagnosis` 等工具（需删）

**建议**：**倾向删**。当前"保留但不接"是最坏情况——既不删，也不真用。若真要保留 manual fallback，正确做法是：
- (a) 独立 CLI `python -m kovaak_tracker.coach.narrator <diagnosis.json>`，或
- (b) 在 `report.build_report` 加显式 `use_agent: bool = True` 开关

需点点决策。

---

### D-5 · `providers.py.LLMBackend.generate()` 三实现（~30 行）

| 位置 | 行数 | Runtime 占用 | 删除影响 |
|---|---|---|---|---|
| `kovaak_tracker/coach/providers.py:23-24`（Protocol） | 2 | 0%（仅 narrator 用） | 随 D-4 |
| `AnthropicBackend.generate` | 6 | 0%（仅 narrator 用） | 随 D-4 |
| `OpenAICompatBackend.generate` | 7 | 0%（仅 narrator 用） | 随 D-4 |
| `DeepSeekBackend.generate` | 7 | 0%（仅 narrator 用） | 随 D-4 |

**验证**：`Grep "\.generate\("` 非测试命中 = `narrator.py:65, 105, 134`。

**建议**：随 D-4 一起决定。删 narrator 则这层也清；保留 narrator 则继续保留。

---

### D-6 · `video.py.save_uploaded_video`（~5 行）

| 位置 | 行数 | Runtime 占用 | 删除影响 |
|---|---|---|---|---|
| `kovaak_tracker/video.py:20-23` | 4 | 0%（无引用） | CLAUDE.md 需同步删一行 |

**验证**：`Grep "save_uploaded_video"` 全仓库命中 = 只有本文件 + CLAUDE.md。

**建议**：删。早期 Streamlit app 现在用 `tempfile.NamedTemporaryFile`（`app.py:47-49`）。

---

### D-7 · `flicking.FlickFairMetrics.direction_deg`（~6 行）

| 位置 | 行数 | Runtime 占用 | 消费者 |
|---|---|---|---|
| `kovaak_tracker/flicking.py:420`（字段）+ 583-584（计算）+ 608（写入） | 6 | 0%（算但不消费） | **无** |

**验证**：
- `_summarize_reference`（`pan_tracker.py:266-270`）的 `names` 元组**不含** `direction_deg` → 永远不进 summary
- `advice.compare_table`（`advice.py:223-227`）的 metrics 列表也不含 → 永远不进对比
- `advice.advise` 无 `direction` finding
- `progress.TREND_METRICS` 不含

**建议**：删。每 flick 算了但从来不读。

---

### D-8 · `__pycache__/dashboard_data.cpython-39.pyc`（孤儿缓存）

| 位置 | 性质 | 删除影响 |
|---|---|---|---|
| `kovaak_tracker/__pycache__/dashboard_data.cpython-39.pyc` | 孤儿 .pyc（无对应 .py） | 无（属 CLAUDE.md §5 例外） |

**建议**：本地 `rm` 即可，无需纳入代码变更。

---

## §4 功能缺口：history 页（最大缺口）

**依赖 item 1（默认页动态）+ item 6（删/导出/导入）+ item 11（角标）+ item 7（login 不背 history）**

### 缺口量化

| 功能 | 依赖 | 当前状态 | 缺失代码量估算 |
|---|---|---|---|
| history 列表页 | item 1 | ⬜ 无 `/history` 或 `/sessions` 路由 | 前端 ~200 行 |
| 默认页分支逻辑 | item 1 | ⬜ 硬编码 upload，无检测分支 | 前端 ~20 行 |
| 列表 API（GET /api/sessions） | item 1 | ⬜ `routes.py` 无此端点 | 后端 ~30 行 |
| 删 session API | item 6 | ⬜ 无 `DELETE /api/sessions/{id}` | 后端 ~15 行 |
| 导出/导入 API | item 6 | ⬜ 无 `GET /api/sessions/export` / `POST /api/sessions/import` | 后端 ~40 行 |
| 顶栏角标 | item 11 | ⬜ 无全局顶栏组件 | 前端 ~50 行 |
| 全局 toast | item 11 | ⬜ 无 ToastProvider / useToast | 前端 ~80 行 |

**总计缺失代码量**：前端 ~350 行 / 后端 ~85 行

### 建议

history 页实现时一次性覆盖这 4 条依赖（item 1 + item 6 + item 11 + item 7），避免分散增量。

---

## §5 依赖必要性检查

### Python 依赖（requirements.txt）

| 包 | 是否真用 | 验证 |
|---|---|---|
| opencv-contrib-python | ✅ 是 | `app.py:15`、`tracking.py:8`、`pan_tracker.py:25`、`video.py:8`、`vision.py:5`、`start_frame.py:23`、`calibration_cli.py:6` 等 |
| streamlit | ✅ 是 | `app.py:8`（CLI 入口）、`app.py:16` import |
| pandas | ✅ 是 | `csv_parser.py:23`、`analysis.py:7`、`tracking.py:10`、`pan_tracker.py:27` 等 |
| numpy | ✅ 是 | 遍布 `pan_tracker.py:26`、`flicking.py:25`、`analysis.py:6`、`tracking.py:9` 等 |
| plotly | ✅ 是 | `coach/visualization.py`（coach 包）、`webapp/frontend/package.json:14`（plotly.js-dist-min） |
| scipy | ✅ 是 | `analysis.py:8`（`savgol_filter`）、`flicking.py:27`（`find_peaks`） |
| pytest>=7.0 | ✅ 是 | 测试运行器 |
| anthropic>=0.40 | ✅ 是 | `coach/providers.py:1`（AnthropicBackend，spec 备选 provider） |
| openai>=1.0 | ✅ 是 | `coach/providers.py:2`（OpenAICompatBackend，DeepSeek/OpenRouter 用） |

**结论**：requirements.txt **全部真用**，无一冗余。

### 前端依赖（webapp/frontend/package.json）

| 包 | 是否真用 | 验证 |
|---|---|---|
| next | ✅ 是 | Next.js 框架 |
| plotly.js-dist-min | ✅ 是 | `components/PlotlyChart.tsx` |
| react | ✅ 是 | React 19 |
| react-dom | ✅ 是 | React DOM |
| react-plotly.js | ✅ 是 | `components/PlotlyChart.tsx` |

**结论**：前端依赖 **全部真用**，无一冗余。

---

## §6 配置死参数检查

### 检查范围
- `kovaak_tracker/coach/providers.json`（provider 配置）
- `output/calib_config.json`（tracking 校准配置）
- `webapp/backend/config.py`（环境变量 + 常量）
- `kovaak_tracker/settings.py`（常量）

### 发现

**1. providers.json 6 个 provider 全部活跃**
- anthropropic（spec 备选）
- deepseek（PRD §9.1 默认，`LLM_PROVIDER=deepseek`）
- openrouter（未测但配置在）
- groq（未测但配置在）
- openai（OpenAI-compat，DeepSeek 用）
- local（Ollama，dev 测试用）

**判定**：保留。这是合理的 provider 抽象，load_backend 的 switch 一行加一个，不是过度。

**2. config.py 常量全部真用**
- `DATABASE_URL` → `db.py:8`
- `DB_PATH` → `db.py` 全文
- `VIDEO_TMP_DIR` → `routes.py:136`
- `LLM_PROVIDER` → `worker.py:8`、`routes.py:153`
- `LLM_DAILY_BUDGET_CNY` → `llm_budget.py:6`
- `MAX_VIDEO_BYTES` → `routes.py:82`
- `MAX_CSV_BYTES` → `routes.py:77`

**判定**：全部消费，无死参数。

**3. settings.py 常量全部真用**
- `OUTPUT_DIR` → `pan_tracker.py:37`、`flicking.py:32`、`tracking.py:12` 等
- `ensure_output_dir()` → `pan_tracker.py:248`、`flicking.py:365`、`tracking.py:236` 等

**判定**：全部消费，无死参数。

**结论**：**未发现配置死参数**。

---

## §7 文档冗余检查

### 检查范围：`docs/` 下所有 .md

### 发现（已过时/冗余文档）

**1. 过时但保留（演进历史）**
- `docs/superpowers/specs/2026-07-05-flicking-coach-webapp-design.md` — §3 部署 / §10 Phase 3 备案被 PRD §5.2 + §9.1 取代，但顶部已标注演进关系，**保留作设计历史**。
- `docs/superpowers/specs/2026-06-28-ai-aim-coach-design.md` — 单次 coaching 设计，被 `2026-07-05-aiming-coach-agent-design.md`（agent loop）演进，但顶部未标注。**建议标注"被 agent spec 演进"**。
- `docs/superpowers/specs/2026-06-28-progress-loop-design.md` — 进步闭环设计，被 PRD §4 价值点 4 + CLAUDE.md 参考，顶部未标注。**建议标注"已落地 progress.py + planning.py"**。

**2. 活跃文档（不冗余）**
- `docs/PRD.md` — **方向锚**，必须最新
- `docs/product-strategy.md` — 战略 + 商业化 + 远期愿景，PRD 关联
- `docs/superpowers/specs/2026-07-05-tracking-coach-design.md` — tracking 理论审视 + coach 设计，C 阶段参考
- `docs/superpowers/specs/2026-07-05-aiming-coach-agent-design.md` — coach agent 设计，当前实现
- `docs/superpowers/specs/2026-07-06-aiming-cookie-ia-redesign-design.md` — IA + login + 流程细节，v1 参考
- `docs/coach-theory-foundation.md` — 教练/反馈/技能习得理论底座，经久
- `docs/coach-community-frontier.md` — 瞙准社区前沿，易过时但标注
- `docs/flicking-aim-coach.md` — flicking coach 完整介绍，当前产品参考
- `docs/PROGRESS.md` — 进步日志，必须最新

**3. Review 归档（不冗余）**
- `docs/review/2026-07-08/*` — 昨天全量 review，归档保留

**建议**：对过时 spec 顶部加"演进至 XXX"标注，但不删——演进历史有价值。

---

## §8 决策项复查：文件夹记忆 bug 定性

### PRD §8 #13 描述
> upload 修复：视频 / CSV 上传来源文件夹**分别记忆**（当前 bug：共用，导致 CSV 被导向视频目录）

### 验证结果

**当前实现无此 bug。**

**证据**：
- 视频和 CSV 是**两个独立 `<input type="file">` 元素**：
  - 视频：`webapp/frontend/app/page.tsx:369-375`（DropZone 内）— `<input type="file" accept="video/mp4,video/*">`
  - CSV：`webapp/frontend/app/page.tsx:485-491`（FileField 内）— `<input type="file" accept=".csv,text/csv">`
- 浏览器原生行为：Chrome/Edge/Firefox 对**每个 `<input type="file">` 独立记忆上次文件夹**——两个独立 input 天然分别记忆，不会共用
- `id` 不同：视频 input 无 id（DropZone 内部 ref），CSV input `id="csv"`（`page.tsx:206`）——不是同一个元素

### 判定

**PRD §13 描述的"当前 bug"在当前实现里不存在**——视频和 CSV 是两个独立 `<input type="file">` 元素，浏览器天然分别记忆。

### 修正建议

**PRD §13 描述需修正为**：

> upload 修复：视频 / CSV 上传来源文件夹**分别记忆**（Chromium 原生行为：同 origin 所有 file input 共享"上次目录"；当前实现用两个独立 input 天然隔离，无需修复）

### 背景（参考 07-webapp-frontend.md）

Chromium 行为：同 origin 下所有 file input 共享一个"上次访问的目录"。若要实现"分别记忆"，需用 File System Access API 的 `showOpenFilePicker({id})` 重构，算功能开发，非 bug 修复。

**结论**：**非代码 bug，是浏览器原生行为**。PRD 描述需修正为"Chromium 原生行为，非代码bug"。

---

## §9 总评

### 健康度：A

| 维度 | 状态 |
|---|---|
| PRD 对齐 | **0 偏差**（13 条：✅5 / 🟡5 / ⬜3） |
| YAGNI 违规 | **0 条**（v1/B/C/远期 功能无一提前误做） |
| Dead code | **~470 行可删**（旧 flicking pipeline + narrator 双轨） |
| 架构演进 | **干净**（kovaak_tracker 对 webapp.backend 零依赖） |
| 依赖必要性 | **全部真用**（Python + 前端无一冗余） |
| 配置死参数 | **未发现** |
| 文档冗余 | **3 份过时 spec 需标注演进关系** |
| 文件夹记忆 | **非 bug，PRD 描述需修正** |

### 最值得清理 Top 3（按 ROI）

1. **D-1 + D-2 + D-3 一起删**（~470 行）—— 旧 flicking pipeline + aligner.py + analyze_flicking_video，零 runtime 引用、零测试覆盖、零 API 暴露。**最高 ROI**。

2. **D-4 narrator.py + D-5 providers.generate + test_narrator.py**（~340 行）—— 消除双轨维护。但需要明确决策"manual fallback 不要了"，比 D-1 决策成本高。

3. **D-6 + D-7 + D-8 零散清理**（~20 行 + 1 个 .pyc）—— 单条价值小，但顺手做能让仓库更紧。

### 功能缺口量化

- **history 页**（最大缺口）：前端 ~350 行 / 后端 ~85 行
- 依赖 item 1 + item 6 + item 11 + item 7，建议一次性实现覆盖

### 点点决策待办

- [ ] **narrator 双轨删还是留**（D-4，17 个月未用，倾向删）
- [ ] **旧 flicking pipeline 清理确认**（D-1/2/3，~470 行，runtime 0%）
- [ ] **PRD §13 描述修正**（文件夹记忆"bug"定性为 Chromium 原生行为）
- [ ] **过时 spec 顶部加演进标注**（2026-06-28 两份）

---

## 附录：引用文件路径

- 旧 flicking chain: `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\kovaak_tracker\flicking.py:35-374`
- aligner.py: `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\kovaak_tracker\aligner.py`
- analyze_flicking_video: `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\kovaak_tracker\pan_tracker.py:199-245`
- narrator: `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\kovaak_tracker\coach\narrator.py`
- providers.generate: `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\kovaak_tracker\coach\providers.py:23-24, 105-110, 192-198, 365-371`
- save_uploaded_video: `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\kovaak_tracker\video.py:20-23`
- direction_deg: `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\kovaak_tracker\flicking.py:420`
- 孤儿 .pyc: `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\kovaak_tracker\__pycache__\dashboard_data.cpython-39.pyc`
- PRD: `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\docs\PRD.md`
- requirements.txt: `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\requirements.txt`
- frontend package.json: `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\webapp\frontend\package.json`
- 过时 spec: `C:\Users\袜子\Desktop\Tension-Aware-Aim-Analyzer\docs\superpowers\specs\2026-06-28-*.md`
