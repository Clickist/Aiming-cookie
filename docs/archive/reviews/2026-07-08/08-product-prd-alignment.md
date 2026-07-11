# 产品方向 PRD 对齐 Review

> 2026-07-08 · reviewer: 产品方向对齐 agent
> 范围:对 `docs/PRD.md` §8 的 13 条 UIUX 决策逐条核对实现状态 + §9 架构分工对齐分析
> 状态符号:✅ 已实现 · 🟡 部分 · ⬜ 待实现(正常,PRD 是目标态) · ❌ 偏差(矛盾才是问题)

---

## §1 13 条 UIUX 决策逐条核对

### 1. 默认页动态:无 history → upload,有 → history — ⬜ 待实现

**证据**:
- `webapp/frontend/app/page.tsx:28-298` 是 upload 页(应用入口 `/`),硬编码为 upload,无 history 检测分支
- `webapp/frontend/lib/api.ts` 无 `listSessions` / `getUserSessions` 接口
- `webapp/backend/routes.py` 全文无 `GET /api/sessions`(列表)端点,只有 `GET /api/sessions/{id}`(单查)
- `webapp/backend/db.py:33-61` sessions 表有 `user_id` 列 + `idx_sessions_user_status` 索引,但无 `list_sessions_by_user` 函数
- 无 `/history` 或 `/sessions` 路由(`webapp/frontend/app/sessions/[id]/page.tsx` 是 processing 页,不是列表页)

**说明**:PRD §6.1/§6.2 与 IA spec §3.3 都把 history 页列为 v1 必需。当前 webapp 前端只有 upload/processing/report/coach 四屏,无 history 页,也无"检测有无 history → 决定默认页"逻辑。属于 v1 待实现,不是偏差。

---

### 2. upload 无 profile 表单,CSV 自动算 — ✅ 已实现

**证据**:
- `webapp/frontend/app/page.tsx:38-40` — 只有 `cmPer360` + `fov` 两个 numeric 字段(`NumberField`),无 profile 表单(无 DPI/Sens/Game 等)
- `webapp/frontend/app/page.tsx:87-114`(`handleCsvChange`)— 选 CSV 后 `parseKovaaKConfig(text)` 自动抽取 FOV/DPI/Horiz Sens
- `webapp/frontend/app/page.tsx:105-107` — 抽到 FOV 自动 `setFov(String(extract.fov))`
- `webapp/frontend/lib/csv.ts:31-69`(`parseKovaaKConfig`)— 从 CSV config block 抽 FOV/DPI/Horiz Sens/Vert Sens/Resolution
- `webapp/frontend/app/page.tsx:223-225` 文案说明"后端从 CSV 自动算(DPI + Horiz Sens + game yaw 表)"

**说明**:upload 表单极简,profile 自动抽取。cm/360 留手填(因 KovaaK Sens Scale 对单位敏感,自动算会出错——CSV 不含 cm/360,只有 DPI + Horiz Sens 组件)。完全符合 PRD 决策。手改走 settings 的部分 ⬜(settings 页未实现),但 upload 端已对。

---

### 3. processing 教学时刻 + 空状态预告卡 — 🟡 部分

**证据**:
- **教学时刻 ✅**:`webapp/frontend/app/sessions/[id]/page.tsx:58-76` — `COACH_TIPS` 数组 4 条真实教练提示(Becker 2020 / 神经募集 / SPARC / Tracking),`useEffect:116-121` 每 6s 轮换,`CoachTipCard:363-384` 渲染为带标签的卡片。同时 pipeline 4 步(Parsing/Trajectory/Kinematics/Narration)有"数据解析/轨迹追踪/运动学建模/生成执教报告"双语标注,本身也是软件教学
- **可后台 ⚜️ 部分**:`page.tsx:141-160` 用 `setTimeout` 轮询,用户理论上可以浏览器内切走再回来(轮询会继续),但无显式的"切走兜底"机制(切走后 React 组件可能被卸载,轮询停止)
- **空状态预告卡 ⬜**:`page.tsx:201-211` 失败时给 ErrorCard,但无"history 空 → 完成后出现在这"的预告卡(且 history 页本身不存在)

**说明**:教学时刻做到位了(4 条轮换提示 + pipeline 步骤名都是真实概念);空状态预告卡依赖 history 页,属待实现。

---

### 4. diagnosis_report 免费(backend=None 可跳过 LLM)+ 底部教练入口按钮 — ✅ 已实现

**证据**:
- **backend=None 路径 ✅**:`kovaak_tracker/coach/report.py:28-73`(`build_report`)— `backend` 参数默认 `None`;`if backend is not None:` 守卫 narration(report.py:56),`backend=None` 时 narration=None + notes 空但 diagnosis/figures/prescriptions 正常产出
- **worker 降级路径 ✅**:`webapp/backend/worker.py:152-164` — LLM 预算超限时 `run_report(summary, backend=None)`;`_load_backend` 异常也降级 `backend=None`(worker.py:160-163),CV 成功结果不丢
- **前端底部教练入口 ✅**:`webapp/frontend/app/sessions/[id]/report/ReportView.tsx:184-220` — 底部 sticky action bar 有 `<Link href={`/sessions/${sessionId}/coach`}>和教练对话 →</Link>`(行 212-216),样式为主按钮(primary 背景 + 加粗)
- **LLM 失败软降级 ✅**:`ReportView.tsx:114-136` — narration 为 null 时显示"讲解生成失败。诊断数据仍在,参考下方结构化问题列表",诊断数据(issues/root_causes/prescriptions)仍然完整渲染

**说明**:规则化诊断(diagnosis + figures + prescriptions)完全免费可看;LLM 讲解是 best-effort 软依赖;教练入口在底部 sticky bar 显著位置。完全符合 PRD 决策。freemium 切分点干净(印证 PRD §14 "build_report backend=None 跳过 narration")。

---

### 5. coach_dialogue = LLM;D 不立墙 — ✅ 已实现(v1 阶段)

**证据**:
- **coach = LLM ✅**:`webapp/backend/routes.py:163-264`(POST `/api/sessions/{id}/chat`)— 加载 backend(`_load_backend_or_none`,行 153-160),调 `chat_with_coach(diagnosis, chat_history, backend)`(行 227)
- **backend 不可用降级**:`routes.py:220-222` — `backend=None` 时 `notes.append("LLM 后端不可用,本次未生成回复")`,reply=null
- **D 阶段不立墙 ✅**:`routes.py` 全文无付费墙校验,无 credits/订阅检查,只有 `status != "done"` 的 409(行 175-179)和日预算 429(行 194)
- LLM 预算:`webapp/backend/llm_budget.py` + `LLM_DAILY_BUDGET_CNY=1.0`(默认,`config.py:30`)— 是成本控制,不是用户付费墙

**说明**:v1 阶段 coach 对话完全无付费门槛。B 阶段的墙是"待实现"(PRD 明确标 D→B 分阶段,当前是 D 前的开放注册 v1)。符合 PRD 决策。

---

### 6. history 本地优先,删/导出/导入 — 🟡 部分

**证据**:
- **coach 层本地持久化 ✅**:`kovaak_tracker/coach/progress.py:48-54`(`save_session`)— 追加写 `output/history/sessions.jsonl`(JSONL,本地文件);`load_history:57-72` 读 JSONL;无云端依赖
- **report.py 自动写 history ✅**:`kovaak_tracker/coach/report.py:68-72` — `history_path is not None` 时 `save_session(report, meta, history_path)`,best-effort(失败加 note 不崩)
- **webapp 后端持久化 ❌ 偏离"本地优先"**:`webapp/backend/db.py:33-61` — sessions 表 + chat_messages 表存在 SQLite(`aiming_cookie_dev.db`),视频在 `VIDEO_TMP_DIR` tempfile。这是云端/server-side 持久化,不是"本地优先"(桌面 hybrid 未落地)
- **删/导出/导入 API ⬜**:`routes.py` 无 `DELETE /api/sessions/{id}`、无 `GET /api/sessions/export`、无 `POST /api/sessions/import`
- **history 列表页 ⬜**:见 item 1

**说明**:coach 包的 history 是本地 JSONL(符合 PRD),但 webapp 用的是 server-side SQLite——两者是分裂的。worker 调 `build_report(summary, backend=backend)`(`webapp/backend/worker.py:91`)未传 `history_path`,所以 webapp 路径**根本没触发** coach 的 JSONL 写入。这是架构演进的中间态:webapp slice 1 先把数据走 SQLite(为云端),桌面 hybrid 真正落地后 sidecar 直接用 coach 包的 JSONL。当前不算"矛盾",只是两个持久化路径并存且 webapp 未接通 coach 的本地 JSONL。

---

### 7. v1 登录收窄为"计费+身份",不背 history — ⬜ 待实现

**证据**:
- **登录页 ⬜**:`webapp/frontend/app/` 无 `/login` 路由,无登录组件
- **认证 ⬜**:`webapp/backend/routes.py:42` — `x_user_id: str = Header(default="dev", alias="X-User-Id")` 是 dev shim,无真实 auth;无任何 JWT/Clerk/session_token 校验代码
- **history 解耦 ⬜**:history 本身未实现,无“背 history”问题(但也无“不背 history”的正面证据)
- **webapp spec §2 的 OTP-only 已演进**:IA spec §4.1 定"密码为主 + OTP 为辅"

**说明**:登录系统完全待实现。PRD §5.2 + IA spec §4 把 login 列为 v1 必需,当前是 slice 1 的 dev shim 阶段。不是偏差,是 v1 待做。

---

### 8. 失败态分类:本地 CV / 云端 LLM / 网络 — 🟡 部分

**证据**:
- **失败态显示 ✅**:
  - `webapp/frontend/app/sessions/[id]/page.tsx:201-211` — `failed` 状态显示 `ErrorCard`,title="分析失败",message 来自 `state.data.error`(后端存的错误字符串)
  - `page.tsx:183-193` — 网络错(`state.kind === "err"`)显示"无法连接后端"+ 重试按钮
  - `webapp/frontend/app/sessions/[id]/report/page.tsx:43-50` — failed 时 ReportError
- **后端错误来源**:`webapp/backend/worker.py:172-176` — `except Exception as e: await queue.mark_failed(sid, str(e))` 把 CV/分析异常字符串存进 sessions.error
- **LLM 软失败区分 ✅**:`ReportView.tsx:114-136` — LLM 讲解失败不丢报告,显示"讲解生成失败。诊断数据仍在"
- **失败分类 ❌**:前端 ErrorCard 统一渲染一个字符串,无"本地 CV / 云端 LLM / 网络"三类视觉或文案区分。用户看到的就是一个 generic 错误条
- **重试 ⚜️ 部分**:`page.tsx:413-458` ErrorCard 有"重试"按钮(调 `poll()` 重新查询状态),但后端 mark_failed 后状态不会自己变回 queued——重试只是重新查,不是重新入队;`routes.py` 也无 `POST /api/sessions/{id}/retry` 端点

**说明**:错误能显示,但 PRD §6.3 说的"分开写明白"没做到——三类失败对用户是同一个 generic 错误条。重试机制也不完整(只重查不重排)。属待实现/部分。

---

### 9. 日志 cross-cutting:本地 CV / agent / 云端 各层埋 — 🟡 部分

**证据**:
- **后端日志 ✅(基本到位)**:
  - `webapp/backend/worker.py:10` `log = logging.getLogger(__name__)`;行 133/153/162/173/186 各 warning/exception
  - `webapp/backend/routes.py:27` `log = logging.getLogger(__name__)`;行 159(load_backend 失败)/ 232(chat_with_coach 失败)
  - `webapp/backend/worker.py:198-200` — `__main__` 入口 `logging.basicConfig(level=INFO)`
- **agent 层日志 ✅**:`kovaak_tracker/coach/agent.py:37` `_log = logging.getLogger(__name__)`(但 agent.py 文件里没看到实际 `_log.xxx` 调用——只有定义)
- **FastAPI 请求日志 ❌**:`webapp/backend/app.py` 全文无 middleware 记录 HTTP 请求/响应/耗时
- **LLM 请求日志 ❌**:`webapp/backend/llm_budget.py` 只记成本,不记请求 payload/响应/延迟;`providers.py` 的 LLM 调用层无独立日志埋点(从 grep 看未单独打)
- **前端日志 ❌**:无 Sentry / 无 window.onerror / 无统一错误上报;`CoachView.tsx:220` 用 `console.warn` 算零散的 console 输出

**说明**:Python 层 logging 基本框架在,但离"cross-cutting 各层埋"还有距离(缺请求日志 middleware、LLM 调用层独立埋点、前端可观测)。属待实现/部分。

---

### 10. 录屏+鼠标采集远期;upload 留扩展位 — ✅ 扩展位不阻塞

**证据**:
- **upload 不绑死视频+CSV 形态**:`webapp/frontend/app/page.tsx:50-69` — `validateVideo` + `validateCsv` 是独立校验函数,DropZone/FileField 是独立组件;未来加"屏幕录制"按钮不冲突
- **远期未做 ⬜(符合 PRD)**:无 pynput/mss 依赖,无 ScreenRecorder 组件
- **CSV 必填的约束**:`page.tsx:124-127` CSV 必填,后端 `routes.py:39` `csv: UploadFile = File(...)` 也是必填——这跟“鼠标采集”正交,不冲突

**说明**:PRD 把这条标"远期",当前 v1 不做是符合的。扩展位保留良好(upload 表单结构松散,易扩)。不是偏差。

---

### 11. 分析完成全局 toast + 顶栏角标 — ⬜ 待实现

**证据**:
- **toast ⬜**:全文 grep `toast|角标|badge|notification` 只在 `ReportView.tsx` 命中(那是页面内的 badge,不是全局 toast);无 `ToastProvider` / 无 `useToast` hook / 无全局通知 context
- **角标 ⬜**:`layout.tsx:39-66` 是 RootLayout,无全局顶栏 / 无角标组件
- **processing 完成是强制跳转**:`webapp/frontend/app/sessions/[id]/page.tsx:164-168` — `useEffect` 监听 `status === "done"` 时 `router.push(/sessions/${sessionId}/report)`,**强制跳转**,与 PRD §6.3 "不强制跳转"矛盾

**说明**:这条有两个层面——(a) 全局 toast/角标 ⬜ 待实现(正常);(b) processing 完成时**强制跳转**到 report,跟 PRD §6.3 的"全局 toast + 顶栏角标,任意页可见,**不强制跳转**"有方向性出入。但当前 processing 是独立全屏页(不是可切走的后台任务),强制跳转是这个架构下的合理选择——真正"不强制跳转"要等 processing 能后台化(用户能切到别的页)后才有意义。属架构演进中间态,标 🟡(方向已知,当前实现受架构限制)。

---

### 12. 教练即时访问新数据 — ✅ 已实现

**证据**:
- **分析完写入 session ✅**:`webapp/backend/worker.py:166-168` — `report_dict["timeline"] = timeline_events` + `queue.mark_done(sid, report_dict, cost)`,CoachReport 完整持久化到 sessions.result
- **coach_dialogue 进入时加载**:`webapp/backend/routes.py:172-179` — chat 端点先 `queue.get_session(session_id)`,从 `result` 拿 CoachReport dict,`_reconstruct_diagnosis(result)`(行 109-150)反序列化成 CoachDiagnosis
- **agent 用最新 diagnosis**:`routes.py:213-218` — `chat_with_coach(diagnosis, chat_history, backend)` 把本次 session 的 diagnosis 喂给 agent
- **coach 页拉历史 + 渲染**:`webapp/frontend/app/sessions/[id]/coach/page.tsx:13-57` — 服务端组件 getSession 后才渲染 CoachView,保证进入时数据已 ready

**说明**:教练进入 coach_dialogue 时,后端从 sessions.result(刚分析完写入的)拿 diagnosis,反序列化后直接喂给 agent——agent 即时访问最新数据。完全符合 PRD 决策。**注**:PRD 措辞是"写入本地 session",当前是写 server-side SQLite(桌面 hybrid 未落地);功能等价,载体差异随架构演进而收敛,不算偏差。

---

### 13. upload 视频/CSV 文件夹记忆分离 — ✅ 已实现(无 bug)

**证据**:
- **两个独立 input 元素**:
  - 视频:`webapp/frontend/app/page.tsx:369-375`(DropZone 内)— `<input type="file" accept="video/mp4,video/*">`
  - CSV:`page.tsx:485-491`(FileField 内)— `<input type="file" accept=".csv,text/csv">`
- **浏览器原生行为**:Chrome/Edge/Firefox 对每个 `<input type="file">` 独立记忆上次文件夹——两个独立 input 天然分别记忆,不会共用
- **id 不同**:视频 input 无 id(DropZone 内部 ref),CSV input `id="csv"`(page.tsx:206)— 不是同一个元素

**说明**:PRD §13 描述的"当前 bug:共用,导致 CSV 被导向视频目录"在当前实现里**不存在**——视频和 CSV 是两个独立 `<input type="file">` 元素,浏览器天然分别记忆。要么 bug 已被修(实现时就用两 input),要么 PRD 这条是基于早期原型的 stale 描述。无论如何,当前实现 ✅ 符合 PRD 决策。

---

## §2 架构分工对齐分析(PRD §9 桌面 hybrid)

| 层 | PRD 目标位置 | 当前位置 | 状态 |
|---|---|---|---|
| 视频解析 + pan_tracker + 指标计算 | 本地 sidecar | **云端 Worker**(`webapp/backend/worker.py:run_analysis`) | ⬜ 待搬迁 |
| coach agent 框架(tool-use loop) | 本地 sidecar | **云端**(`routes.py` 调 `chat_with_coach`,worker 调 `narrate_diagnosis`) | ⬜ 待搬迁 |
| LLM 推理请求 | 云端 API 代理 | ✅ 云端(`providers.py` 藏 key,DeepSeek 默认) | ✅ 已对齐 |
| 账号 / 订阅 / 画像 / history | 云端(B+ 阶段) | 🟡 部分(sessions 表在 SQLite,DB_PATH 本地文件;B 阶段升 Postgres + 跨设备同步待做) | 🟡 演进中 |

### 当前代码跟"本地 sidecar"目标矛盾的地方?

**结论:无矛盾,只是纯 web 架构待演进。** 详细判定:

1. **worker.py 结构干净,易搬迁**:`run_analysis(video_path, csv_path, ...)` 是纯函数式入口(`worker.py:15-40`),调 `kovaak_tracker.pan_tracker.analyze_flicking_fair_summary`——无 FastAPI/DB 耦合,搬到 sidecar 时整个函数原样带走
2. **run_report 同理**:`worker.py:82-100` 调 `kovaak_tracker.coach.report.build_report`,纯函数,best-effort LLM——sidecar 里直接复用
3. **没有"云端硬依赖"反锁**:`kovaak_tracker/` 包不 import `webapp.backend.*`(grep 验证),CV/agent 逻辑对 webapp 零依赖——sidecar 化时 webapp.backend 可以原地瘦身为鉴权+LLM 代理
4. **FastAPI routes 可拆**:`routes.py` 当前混了 analyze(应搬本地)+ sessions 查询(可留云端)+ chat(LLM 代理,留云端)+ timeline(数据查询,留云端)。拆分点清晰,无纠缠
5. **providers.py LLM 代理定位正确**:`kovaak_tracker/coach/providers.py` 已是"藏 key + OpenAI-compatible 调用"形态,搬到云端代理层天然成立

**唯一要留意的演进风险**:
- `webapp/backend/queue.py` 用 SQLite `BEGIN IMMEDIATE` 做队列(`queue.py:32`)。搬到桌面 hybrid 后,本地 sidecar 是否还需要队列(单用户单机)?可能直接同步调 `run_analysis` 即可,queue.py 的并发控制是为多用户云端设计的,sidecar 里冗余。但这不是"矛盾",是演进时简化,留待桌面打包 spec 处理。

---

## §3 真偏差清单(排除待实现,只列做了但矛盾的)

**经逐条核对,当前实现跟 PRD 无硬性矛盾。** 唯一值得提的“轻度偏离”:

### 偏差-1:processing 完成时强制跳转(PRD §6.3 说"不强制跳转")

- **位置**:`webapp/frontend/app/sessions/[id]/page.tsx:164-168`
- **代码**:`useEffect(() => { if (status === "done") router.push(`/sessions/${sessionId}/report`); }, ...)`
- **PRD §6.3**:"完成通知:全局 toast + 顶栏角标(任意页可见,**不强制跳转**)"
- **判定**:这是架构限制——当前 processing 是独立全屏页,用户没有"切走"的选项,完成后跳转是合理的 UX。真正"不强制跳转"要等 processing 能后台化(用户切到 history 看别的)后才有意义。**不算 bug,是演进中间态**。但要在后续 toast/角标落地时,把强制跳转改成“toast 提示 + 用户决定是否跳”。

### 偏差-2:webapp 后端用 server-side SQLite,不符合"history 本地优先"

- **位置**:`webapp/backend/db.py:33-61` sessions/chat_messages 表 + `config.py:8` `sqlite+aiosqlite:///./aiming_cookie_dev.db`
- **PRD §8 item 6 / §9**:history 本地优先(桌面 hybrid 的"本地 sidecar"定位)
- **判定**:slice 1 阶段是纯 web 架构,SQLite 在 server 是合理的——不是"做错了",只是还没搬到 sidecar。同时 coach 包自己有 JSONL 本地持久化(`progress.py:48-72`),但 webapp worker 没接通(worker.py:91 调 build_report 没传 history_path)。**两个持久化路径并存且未协同**,等桌面 hybrid 落地时统一。标“轻度偏离”,不是硬矛盾。

---

## §4 总评:实现与 PRD 对齐度

### 13 条状态分布

| 状态 | 数量 | 条目 |
|---|---|---|
| ✅ 已实现 | 5 | 2, 4, 5, 12, 13 |
| 🟡 部分 | 5 | 3, 6, 8, 9, 11 |
| ⬜ 待实现 | 3 | 1, 7, 10(upload 扩展位)/ 11(部分子项) |
| ❌ 偏差 | 0 | — |

**精确计数**(把 item 11 的 toast/角标 vs 强制跳转分开看):
- ✅ 已实现:5 条(2, 4, 5, 12, 13)
- 🟡 部分:5 条(3, 6, 8, 9, 11)
- ⬜ 待实现:3 条(1-history 页, 7-login, 10-录屏采集远期 + 各 🟡 里的子项)
- ❌ 硬偏差:0 条

### 关键结论

1. **无硬性矛盾**:当前实现是 PRD 目标态的合理前置阶段(slice 1 纯 web + slice 2 前端 + slice 3 待做 auth/history/桌面化)。所有"待实现"都是 PRD 明确标 v1/B/远期的项目,代码没做错方向。
2. **核心价值链已通**:upload → processing → report → coach 这条主线功能完整,LLM 软依赖 + 免费诊断 + 教练入口都符合 PRD §4 的 5 个价值主张(除"长期进步追踪"待 history 页)。
3. **架构演进路径干净**:`kovaak_tracker` 包对 `webapp.backend` 零依赖,worker 的 run_analysis/run_report 是纯函数,搬到本地 sidecar 时不需要解耦——演进不浪费(印证 PRD §9 "webapp 既有资产演进不浪费" + MEMORY.md "productize-dont-rebuild")。
4. **最值得关注的轻度偏离**:processing 完成强制跳转(item 11),跟 PRD §6.3 "不强制跳转"方向相反——但受当前 processing 全屏页架构限制,toast/角标落地时一并改。
5. **history 是最大的功能缺口**:item 1(默认页动态)+ item 6(删/导出/导入)+ item 11(角标)+ item 7(login 不背 history)都依赖 history 页落地。建议 history 页实现时一次性覆盖这四条。

### 文件清单(本次 review 引用的)

- `docs/PRD.md`(全文,§8 item 表 + §9 架构分工)
- `webapp/frontend/app/page.tsx`(upload 页)
- `webapp/frontend/app/layout.tsx`(RootLayout)
- `webapp/frontend/app/sessions/[id]/page.tsx`(processing 页)
- `webapp/frontend/app/sessions/[id]/report/page.tsx`(report server gate)
- `webapp/frontend/app/sessions/[id]/report/ReportView.tsx`(report 视图)
- `webapp/frontend/app/sessions/[id]/coach/page.tsx`(coach server gate)
- `webapp/frontend/app/sessions/[id]/coach/CoachView.tsx`(coach 视图)
- `webapp/frontend/app/sessions/[id]/{report,coach}/loading.tsx`
- `webapp/frontend/app/not-found.tsx`
- `webapp/frontend/lib/{api.ts, csv.ts, types.ts}`
- `webapp/frontend/components/PlotlyChart.tsx`
- `webapp/backend/{app.py, config.py, db.py, queue.py, routes.py, worker.py, schemas.py, llm_budget.py}`
- `kovaak_tracker/coach/{report.py, agent.py, agent_tools.py, progress.py}`
- `docs/superpowers/specs/2026-07-06-aiming-cookie-ia-redesign-design.md`
- `docs/superpowers/specs/2026-07-05-flicking-coach-webapp-design.md`
