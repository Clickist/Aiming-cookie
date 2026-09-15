# Aiming Cookie Current Progress

> Updated: 2026-09-13. 当前实现快照，不是产品或架构事实源。更早的逐会话历史见 [`archive/history/PROGRESS-2026-08-10-to-2026-08-27.md`](archive/history/PROGRESS-2026-08-10-to-2026-08-27.md)（其前史见同目录 `PROGRESS-2026-06-27-to-2026-07-10.md`、`PROGRESS-2026-07-12-desktop-slice.md`）。

## 2026-09-13 — v1.0.1 发布：审计修复版（0913 全量审计的修复批）

v1.0.1（tag `v1.0.1` 指向 `8c4a924`）发版物：NSIS 安装包 149,816,063 字节（Authenticode 未签名，内测预期），R2 三件套（exe + `.sig` + `latest.json`，`latest.json` version=1.0.1，`pub_date` 2026-09-13T06:17:38Z）已上传，落地页下载指向 1.0.1；线上 v1.0.0 老客户端自动收到更新提示。打包采用 bash 预导出签名环境变量的单次跑通法（见本地发版 runbook 记录）。

v1.0.1 承接同日全量审计（`.zcode/audit-2026-09-13/REPORT.md`，主 agent 逐条复核 44 条零误报）后的五路修复 + 休眠代码删除：

- **Python 后端+遥测**：worker.py 冻结源校验顺序回归（v1.0.0 带的 2 条红测试转绿，发版 Gate 恢复）；外遥测孤儿导入跨 key 身份核对（内容哈希+轮文件双匹配，重复导入不再产生）；`training_at` 统一为文件名词干对局时间；本机场景集统一合并口径；offset 探针负 take 防崩；reoffset 修裸栈并启用第 4 级自定位；采集子进程死亡落日志+诊断可见。
- **教练核心**：`coach_list_signals`/`valid_signals` 过滤 prescription.*（与 query_registry 口径一致）；advice 0 值误判 None 修正；pan_tracker 报错文案；knowledge.py 残留清理。**删除休眠 agent 循环 2654 行**（agent/planning/progress/providers/agent_kb，生产零调用，回收站可还原），打包不再强制携带 v1 注册表。
- **coach-runtime**：retryAgentRun 保留 contextRefs；stopped 标记与 truncate 统一可见消息枚举（防未来截断错位）；harness.abort 兜底 catch；处方索引 v1 形状守卫；契约补 context_refs；删孤儿 fake-stream.ts。
- **前端**：回合终态收敛竞态（切会话窗口不再写错归档/掐流）；打断后刷新消息；删当前会话清 URL；分割条 pointercancel；死代码与 10 处死 CSS 清理。
- **文档**：PROGRESS/ROADMAP 随 1.0.0 与点点拍板更新；训练事实写入合同改真话（无 confirmation/grant 硬门，按现状记录并经拍板维持）；affiliate-worker 归属补齐；PRD 决策日志记两条（训练写入不加门、scenario.open 提示词层防线）。

测试基线（发版前实测）：pytest **1372 过 / 0 败 / 5 跳**、coach-runtime 373 过 / 2 跳、前端 unit+contracts 443 过 + type-check 0 错。点点拍板排期（发版后）：正则测试瘦身+覆盖缺口补写同批启动；启动扫尾时序下次更新；HTTPS 等服务器。

## 2026-09-13 — v1.0.0 发布：首个正式版本（tag `bc444c8`，其后 `e7fcdaf` 内测修补）

v1.0.0（tag 指向落地页 commit `bc444c8`）是**首个正式版本**；发版物为 NSIS 安装包，R2 三件套（exe + `.sig` + `latest.json`）已上传，`latest.json` 的 `version` 为 `1.0.0`（`pub_date` 2026-09-12T21:44:10Z），落地页下载指向 1.0.0。tag 之后另有内测修补 commit `e7fcdaf`（外链唤起默认浏览器 + 训练卡浮层定高精简），未另打 tag。

1.0.0 批次（`v0.1.15..bc444c8`）六个实质提交：

- **知识库 v12 + 偏移四级链 resolver**（`845a367`）：registry v12 共 111 条含 60 条处方，`query_registry` 隔离，补齐处方出处引用；`telemetry_capture` 的 `offset_resolver` 走「包内表 → 缓存 → 云表 → GUOD 运行时自定位」四级链，自适应 KovaaK 更新重取偏移。
- **coach-runtime 收官批**（`3b868dc`）：provider catalog 固定「Aiming Cookie 官方」排第一；`readSessionMessagesForUi` 为被打断回合合成 stopped 标记（不进 Provider 上下文）；新增 `scenario-native` / `web-search-native`（DDG 兜底链）/ `affiliate-native` / `coach-env-facts` 原生命令；plan-builder skill 补 draft→saved→activate 状态机说明。
- **backend 修复批**（`9cad8a9`）：修复激活计划后顶栏训练卡消失——`current-training` 在 items 注册表为空时回退投影 `plan_payload.items`，`display_name` 回退 `item.name`，可启动性只认 reviewed 场景 ref；随 0912-0913 采集与存储屏批次更新 `kovaak_run_projection`/`store`、`telemetry_capture_service`、schemas、config、`desktop_runtime`。
- **frontend 壳层与面板批次**（`593c05e`）：训练卡 chip 兜底、白名单域裸 https URL（淘宝短链/JD/B站）autolink、设置五屏（通用/Provider/采集/KovaaK/存储）保真、Coach 壳层 v6、error/global-error 错误边界；版本号抬至 1.0.0（`package.json` / `tauri.conf.json`）。
- **ARCHITECTURE 随批次修订**（`299b8b8`）。
- **落地页下载链接指向 1.0.0**（`bc444c8`）。

当日审计与修复：全量审计（六路并行、只读）报告见 `.zcode/audit-2026-09-13/REPORT.md`，实跑三套测试——pytest **1423 过 / 2 败 / 5 跳**、coach-runtime **368 过 / 2 跳**、前端 unit+contracts **443 过**。两个 pytest 失败在 `tests/test_native_flicking_analysis.py`（`0d911f1` 于 09-05 把 raw trace 指纹校验挪到 `parse_stats_bytes` 之后，坏数据在解析处先抛别的错误），属已发布 tag 内的回归，待修。

## 2026-09-07 — v0.1.15 发布：遥测采集工具随产品接线

v0.1.15（tag 指向落地页 commit `804b78b`）版本号抬升与下载链接切换为 `182e87b` / `804b78b`。本版主体（v0.1.14 之后 10 个 commit）：

- **遥测采集工具随产品接线**（`a0349e6`）：采集工具自研究工作区复制入仓（`telemetry_capture/`，零第三方依赖）；backend `telemetry_capture_service` 常驻托管 target/camera/input 三通道，游戏退场自动 cleaner 分轮 + merge 旁车进托管 cleaned 根，watch 根未配置时自动指向托管根；收尾带退场缓冲、归档容错与周期重试（修复 WinError 32 句柄竞争），孤儿进程按 pid+创建时间回收；打包 `--add-data` 入 bundle，frozen 态经 `--telemetry-child` 模式拉起；安全阀 `AIMING_COOKIE_TELEMETRY_CAPTURE=0`。实机两局真打全链贯通，分析均 `telemetry_multimodal` 档。
- **壳启动就绪超时 15s→45s**（`d0c64fb`）：Windows 冷启动（node/tsx 首载 + Defender 扫描）实测可超 15s。
- **文档批**：`test_e2e` 定位改口径为 CV 回退管线回归锚（`a9560d7`）；README 补漏索引并把前端追平交接归档（`8dae0cf`）；ROADMAP/PROGRESS 对齐 09-06 快照（`05fa381`、`52fbacc`）；参考料与旧运行产物出库（`8174ad2`）。

## 2026-09-06 — v0.1.14 发布：pi 原版工具 + microcompact + 自动更新首航

v0.1.14（tag 指向落地页 commit `3355b89`）是**首个内置自动更新（tauri-plugin-updater）的版本**。发版物：NSIS 153,011,822 字节，SHA-256 `d48d446a880b57522a084f487699d30ebe7dad27e3610e55a2b6468de8746d26`，R2 三件套（exe + .sig + latest.json，latest.json 最后传）已上传并验证 200，落地页下载链接已切 0.1.14（CF Pages 自动部署，线上已复核）。

本版主体（v0.1.13 之后 20 个 commit）：

- **Coach 文件工具切换 pi 原版实现并扩容**：read/write/ls/edit/grep/find/bash 全部来自 pi coding-agent 原版（`fs-tools.ts` 只加 Coach 产品护栏，analysis-read 上报保留）；系统提示词同步新工具规矩；导入边界测试放宽为「coding-agent 只准 pi-source.ts 出口」。
- **上下文治理换 microcompact**：旧「超 150K 字符从最旧整条丢弃」改为对齐 Claude Code 的 microcompact——超 400K 触发时把旧 toolResult 内容替换为占位符（保留最近 3 条完整），对话文本一条不丢，只改发送视图不回写 session。真 token 兜底仍是 pi compaction。
- **工具行行尾箭头改为真实展开热区**：此前箭头是 aria-hidden 装饰图标，展开热区只有工具名文字（点点与实机双确认点不开）；单步行与组行统一经 CaretToggle 渲染，CDP 真实输入事件端到端验证。
- **自动更新全链路**：tauri.conf.json `createUpdaterArtifacts` + minisign 签名（私钥在本机 `.tauri-keys/`，永不入仓库）；客户端端点 `dl.aimingcookie.com/latest.json`，启动 4s 静默检查 + 设置页手动检查；发版脚本与 manifest 生成器入库。发版三坑（BOM 丢失致 PS5.1 解析炸、签名密码在无 tty 下挂死、落地页单文件化）记录在发版记忆。
- **其余**：设置页重设计（Provider 主从 + 四步向导 + 渐进渲染）；设置/历史秒开（存储记账 + 队列缓存 + 离循环，实测 storage 764→138ms、并发尾部 2.6s→0.34-0.54s）；PRD 商业模式修订（官方托管套餐 + BYOK 双轨）；录制完整性校验改 sha2 crate；场景转火判定两轮修正（hold_frac 先验）；Coach 回看引导放宽 4-6 个；Coach 面板讨论 chip 折叠 + 时间段循环窗 ±2.5s；删除 prompt 死拷贝；B站脚本三件套入 `docs/marketing/`。

## 2026-08-29 → 2026-09-05 — v0.1.11 / v0.1.12 / v0.1.13

- **v0.1.11（08-29）**：Coach 自动开讲双故障修复（无 session_id 重复建会话 + 手动分析被二次开讲）。
- **v0.1.12（08-29）**：划选引用浮层去滚动量双算（浮层偏出屏幕）。
- **v0.1.13（09-05）**：**遥测真值主路径落地**——外部遥测导入模块（cleaned 轮次 → ExternalTelemetryRun）+ 真值主路径四切片 + 自动开启 KovaaK 统计导出（PUS 两键保底 stats+perf）+ `telemetry_observed` 场景判别层 + baseline 档遥测优先 + native 分析器无 raw trace 时由 inputs 旁车合成轨迹 + 遥测档源需求门；CV 侧修复视觉子进程循环导入回归（08-27 起 static/multimodal 视觉验证全灭）；Coach 发送键运行态恒直发 + Toast 不吞点击；持续火力家族转火判定；落地页 SEO 修复批（字体自托管、404、D1 stats Worker）。

## Current Product Direction

- Windows 单机桌面应用（内测阶段，v1.0.0）：KovaaK's 训练诊断 + AI 教练。正常产品面 = Coach、History、Settings。
- Coach 自动选最强可用 Run 档：`multimodal` → `input_native` → `video_fallback`；真值口径以 KovaaK 遥测为权威，CV 是行为细节与回退档（视频 e2e 回归锚的意义所在，见 `webapp/tests/test_e2e.py`）。
- 商业模式（PRD 09-06 修订）：官方托管套餐 + BYOK 双轨；官方托管服务接入已完成订阅 API 与排障，售卖链路拍板为不急。
- 自动更新已上线：新版本发布 = 上传 R2 三件套（latest.json 最后）→ 老客户端自动弹更新。

## Implementation Status

- 架构：SQLite → JSON 文件重写已完成并稳定运行多版本；canonical 数据在 `DATA_ROOT` 下（runs/sessions/analyses/training/config/profile），Coach 会话为 `conversations/{id}.jsonl`。
- 后端 Python（webapp/ + kovaak_tracker/）：分析管线、watcher、ingest、诊断包 v3/v4；场景分派泛化（多级识别 + `config/scenario-overrides.json`）。
- Coach sidecar（webapp/coach-runtime，Node 22 + pinned pi source）：pi harness 直连 Provider、JSONL 会话、知识库物化、product commands、pi 原版 fs 工具 + microcompact、pi compaction 兜底、usage 统计端点。
- 前端（Next 16 + Tauri 2）：Coach 工作态（思考/工具按轮交错、默认折叠）、History 渐进渲染、Settings 重设计（Provider 主从向导）、自动更新 UX。
- 桌面壳（Rust/MSVC）：捕获、录制（MFT 8Mbps）、单实例锁、updater 插件。

## Verification（2026-09-06 发版门）

- 后端 `pytest webapp/tests`：**755 passed, 3 skipped**。
- 前端：`tsc --noEmit` 干净；unit + contracts **271 passed**。
- coach-runtime：**306 passed / 309**（1 fail = pi `skills-loading` Windows 路径既有问题，已对照 HEAD worktree 复核与本批无关；2 skip）。
- 实机（dev，本机）：History/Settings 秒开、Coach 真实 LLM 回合（DeepSeek v4-flash）bash/grep/find 全部执行正确、会话恢复正常、backend.log 零错误（仅既有 54052-54055 video_capture_unavailable 警告，已定案非 bug）。
- 打包：PyInstaller + NSIS 冒烟通过（backend + coach 双进程起）；R2 直链 200 且字节数对拍一致；线上落地页已出 0.1.14。

## 阻塞与交接（截至 09-06）

- **未接线**：Coach usage 面板与 Composer next_turn 触发（后端合同已就绪，前端入口未做）。
- **待确认**：设置页右侧 7 分区 layout 提案（点点）。
- **内测**：两位内测用户待复测「官方托管服务 + AC 搭配」（上下文治理已换 microcompact）。
- **营销**：B站口播定稿待拍摄（录屏基准 v0.1.14）；HyperFrames hook 工作台推进中（工作台为本地独立工程）。
- **既有失败**：coach-runtime `skills-loading`（pi 库 Windows 路径 split），不挡发版，待 pi 上游或本地补丁。
- **仓库卫生（09-06 清扫）**：`.zcode/` 已 gitignore；output/、.firecrawl/、macos-vibrancy-style-pack/、compendium HTML 出库留本地；`data/` 保留（csv=测试夹具，mp4=CV 回退回归锚）；x76-wiki 快照仅本地参考（原站 robots `ai-train=no`，勿入库勿进产品知识库）。
- **1.0.0 审计遗留（2026-09-13，已随 v1.0.1 清零）**：全量审计报告见本地 `.zcode/audit-2026-09-13/REPORT.md`（未入库，不随仓库分发）；审计的 5 个活 P1 中 4 个已随 v1.0.1 修复发版（回合终态收敛竞态、外部遥测重复导入、重试丢 contextRefs、stopped/truncate 错位），第 5 个（训练卡 observation 映射）经点点拍板删除死映射（UI 已不展示观察栏，翻译无人消费）；pytest 的 2 个回归已随 v1.0.1 修复。
