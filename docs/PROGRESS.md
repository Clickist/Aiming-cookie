# Aiming Cookie Current Progress

> Updated: 2026-09-06. 当前实现快照，不是产品或架构事实源。更早的逐会话历史见 [`archive/history/PROGRESS-2026-08-10-to-2026-08-27.md`](archive/history/PROGRESS-2026-08-10-to-2026-08-27.md)（其前史见同目录 `PROGRESS-2026-06-27-to-2026-07-10.md`、`PROGRESS-2026-07-12-desktop-slice.md`）。

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

- Windows 单机桌面应用（内测阶段，v0.1.14）：KovaaK's 训练诊断 + AI 教练。正常产品面 = Coach、History、Settings。
- Coach 自动选最强可用 Run 档：`multimodal` → `input_native` → `video_fallback`；真值口径以 KovaaK 遥测为权威，CV 是行为细节与回退档（视频 e2e 回归锚的意义所在，见 `webapp/tests/test_e2e.py`）。
- 商业模式（PRD 09-06 修订）：官方托管套餐 + BYOK 双轨；中转站（relay）接入已完成订阅 API 与排障，售卖链路（自建套餐/注册/订阅/支付页）拍板为不急。
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
- **内测**：两位内测用户待复测「中转站 + AC 搭配」（上下文治理已换 microcompact）。
- **营销**：B站口播定稿待拍摄（录屏基准 v0.1.14）；HyperFrames hook 工作台推进中（工作台在 `Desktop\Aiming-cookie-video-fx`）。
- **既有失败**：coach-runtime `skills-loading`（pi 库 Windows 路径 split），不挡发版，待 pi 上游或本地补丁。
- **仓库卫生（09-06 清扫）**：`.zcode/` 已 gitignore；output/、.firecrawl/、macos-vibrancy-style-pack/、compendium HTML 出库留本地；`data/` 保留（csv=测试夹具，mp4=CV 回退回归锚）；x76-wiki 快照仅本地参考（原站 robots `ai-train=no`，勿入库勿进产品知识库）。
