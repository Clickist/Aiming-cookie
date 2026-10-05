# Aiming Cookie Current Progress

> Updated: 2026-10-05. 当前实现快照，不是产品或架构事实源。更早的逐会话历史见 [`archive/history/PROGRESS-2026-08-10-to-2026-08-27.md`](archive/history/PROGRESS-2026-08-10-to-2026-08-27.md)（其前史见同目录 `PROGRESS-2026-06-27-to-2026-07-10.md`、`PROGRESS-2026-07-12-desktop-slice.md`）。

## 2026-10-05（下午） — 遥测真值管线三根因全修复 + v1.3.9 发版

- **跨局索引污染根治**：worker→producer 边界显式透传本局入库 meta（targets/t_start，`frozen_round_meta`），冻结分析路径不再读活的共享 rounds_index.json；(round, file) 兜底分支退役（本次误认通道），源目录入口保持。插值热点改有界二分（去整段复制，30 万点 200 次查询毫秒级，2000+ 组样本与旧实现逐位等价）。
- **验证链**：定向 8 文件 97 passed + 1 skipped；dev 真实数据重放 run 54095 两遍结果指纹相同，贴合比 0.06675890695261155 与研究报告 meta 真生命窗基准分毫不差，每遍 4.8 秒；生产安装包构建+覆盖安装后，走安装版完整链路 force 重放 54095（session 78）：14:03:34 创建 → 14:03:36 完成，14 轨（track 0-13）全部进证据，time_in_radius_ratio / target_relative_error_px available，Coach intro-context 可读。修前同数据为 600 秒超时降级 outcome_only（session 76 metrics 空）。
- 无排除干净环境首启验收仍未做（本机有历史排除项）；死亡总数 85 对账未做；全量用户旅程 e2e 待安排。SciPy 完整出仓（4 函数自写替换）为下一版跟进项。
- changelog 1.3.9 块补管线修复条目（new 置首），sync-changelog 与 release-notice 11 测试全过。

## 2026-10-05 — 科学栈启动卡点：局部修复完成，未安装/未发布

- Windows 父管道监测由永久阻塞 `stdin.read(1)` 改为 `PeekNamedPipe` 轮询，保留父端关闭触发退出，运行时退出时停止监测线程；移除 lifespan 自动预热子进程和 worker 领取前最长 1500 秒等待门。手动科学栈诊断脚本保留。
- 跟枪模块的 SciPy 频谱依赖下沉到真实频谱计算分支；基础固定准星几何分析不再加载 SciPy。原有 producer/adapter 耗时与降级 cause 日志保留，没有修改指标公式或杀软配置。
- 定向回归：`python -B -m pytest webapp/tests/test_desktop_runtime.py webapp/tests/test_app.py tests/test_worker_prewarm_and_degradation_diag.py tests/test_tracking_cold_import.py tests/test_tracking_analysis.py -q` → **64 passed, 1 skipped**（真实采集集成测试未启用）。`git diff --check` 通过。
- 独立 PyInstaller runtime 构建成功（177.9 秒，未覆盖安装/发布资源）。真实修复代码、同步父管道保持打开下，SciPy 导入与四类算子首次 5.44 秒、第二次 1.14 秒；从进程启动到就绪分别 10.69/2.99 秒；父端关闭后两次均正常退出。新冻结版拒绝 SciPy 导入时，基础跟枪 fixture 仍产出预期几何指标。证据目录：`.zcode/analysis-blockers-1005-research/`；独立产物：`E:/DevCache/temp/ac-startup-fix-xt0mb97b/dist/aiming-cookie-runtime/`。
- **验收边界**：当前机器存在用户此前设置的排除项，精确范围需要管理员查询；新目录验证不等于无排除、无缓存的用户首启。没有重启/覆盖现有客户端，没有正式安装包、真 Tauri 全旅程、死亡总数 85 对账或无白名单冷启动验收，未提交/推送/发版。旧局共享索引错配与 producer 插值复制热点仅完成研究，未包含在本次科学栈启动修复中，run 54095 全分析仍不能宣布修好。

## 2026-10-04（凌晨） — v1.3.8 发布：新用户付费转化漏斗五卡点（数据定罪 + 沙箱真机 + 当夜发版）

「今晚零单」研究定案：**付款链路无辜（Stripe 当日零 checkout session），流失全在付款前**——device_code（选官方档跳浏览器）近 24h 23 创 5 成 ≈ 78% 流失在浏览器侧，历史完成率 4%~21%。五卡点全修：①会员档在 Provider 下拉沉底（注释写「置顶」实现却渲染在 26 个第三方之后，与线框相反）→ 改置顶，真机截图验证；②等待页只等 deep-link 永不超时 → 5s 轮询兜底，CDP 时间线实锤；③**态1 死环**（首次 /me 必然 trial 未落库——服务端惰性补发异步 30s~4min，界面停「打开订阅页」不自动变「先免费体验」，用户被指去 /pay 又被验证闸弹回 = 1003「冻结在发放前快照重启才恢复」的根因）→ 轮询扩展至 not_subscribed 且未激活态，trial 到位自动切换；④device_code TTL 600→1800s（accounts，curl 冒烟 expires_in=1800）；⑤OTP 有效期 300→600s 对齐邮件文案 + 获取验证码 60s 防连点（线上实案 8218217@qq.com 3 秒 3 封且从未注册成功）。验证：tsc + 346 测试绿 ×2 轮、真机沙箱回归置顶+轮询。accounts 两轮部署（d0c72590）。发版：三处 bump + changelog.json + 落地页链接/changelog 页/llms.txt 渲染，R2 三件套（installer SHA-256 `8a69b31c…`）+ latest.json（version 1.3.8 线上验证）+ tag `v1.3.8` + GH release（附件 exe/.sig）。commit 5b69394（fix）+ 26f34c1（release）；accounts e3dc73f（含 1003 JWT 180 天在途改动一并上线）。发版坑复踩+新知：`build-windows-installer.ps1` 不带 `-Unsigned` 拒跑（1.3.7 同款）；`npm run build:tauri` 只重建 out/ **不编译 exe**，完整产物必须 `npx tauri build --no-bundle`（beforeBuildCommand 自动重建 out）；CUA 沙箱验收后恢复真实数据目录，**再次启动 exe 前必须重新做沙箱**（本轮曾忘，应用加载点点真实工作台，CDP 点击落空白区未损数据）。挂账：QQ 邮箱验证码送达率无直接证据待观察；accounts worker 未开 observability。

## 2026-10-03（晚） — v1.3.7 热修发布：试用出口在第二步卡死（用户报障当日闭环）

用户报障「第二步显示待确认/未启用走不通」→ 定案为 **1.3.5 引入的恶性 onboarding 死锁**：1002 的「先免费体验」CTA 放行进第 2 步，但进入工作台门禁 `memberReady` 只认 `memberStage === "member"`，试用用户（not_subscribed + trialAvailable）按钮永远禁用；叠加订阅页 verified_at 闸 = 试用新用户完全死锁，且按钮灰死不报错不留日志、线上无法发现。修法一行（`memberReady` 认 `not_subscribed && memberTrialAvailable`）+ task3 源码契约回归锁（343/343 绿 + tsc 净）。

**真机端到端闭环**（打包版 1.3.7 沙箱全旅程）：临时改写 Roaming `storage-location.json` 指针指向沙箱根（事后字节级还原；Tauri app_data_dir 走 Windows 已知文件夹 API，LOCALAPPDATA 环境变量重定向**无效**）→ 全新向导 → 会员档 → 真实 login/start → 账号侧 API 自助完成登录（**accounts D1 `verification` 表 OTP 明文可读**，无需收件邮箱；新邮箱 sign-in 即自动注册）→ `/api/device/claim` 换 ticket → `aimingcookie://auth?scene=login` deep-link 换票 → 态1 出现（试用发放是 /me 惰性入队异步发货，**首读无 trial、需二次同步**——再发 `scene=open` deep-link 触发 syncMemberState 后 CTA 才出现，这本身是个真实用户可感知的延迟）→ 先免费体验 → 第二步勾选后进入工作台**已激活**（1.3.6 同态永久灰）→ finish 全链 → 工作台 + `capture-enabled.json {"enabled":true}`。证据截图 `C:\ac-smoke-137\shots\`。

发版物：installer SHA-256 `e73d2a65…`，R2 三件套（exe/.sig/latest.json）已上传并线上验证，tag `v1.3.7` + GitHub release，落地页 1.3.7（commit 880696f/bcf7400/692d18d）。装机注意：本机已覆盖装 1.3.7（真实数据根指针已还原）。发版管线新坑：pwsh 脚本漏 `-Unsigned` 时 bash 管道吃掉退出码呈现 exit0 假绿（对照 1001 中文参数假绿，同族）。


## 2026-10-03 — v1.3.6 发布：安全加固/评审修复批/动效与指引卡

workflow 全量代码评审（10 领域 68 发现，67 经独立复核确认）→ 活体验证（真客户端 CDP：sidecar 裸奔偷 key 全链、坏文件 500、白屏循环等 10 条实锤）→ 两子代理实施 → 全量测试（pytest 743+884 / 前端 342 / coach-runtime 534 / cargo 177 全绿）→ 两连实机走查（安装版老用户旅程 + 隔离标识符 onboarding→真实流式对话→持久化全旅程，SSE query token 路径实测）→ 发版。主要内容：①**sidecar 启动令牌闸门**（ARCHITECTURE 合同落地：全路由除 healthz 校验 `X-Aiming-Cookie-Desktop-Token`，CORS * 收白名单，Tauri 生成→IPC 下发，SSE 走 query 特批，Python 后端调 sidecar 两处补带）；②评审七小修（坏会话文件 500 保护/存储写序 record-first/会员缓存深校验自愈/分析 hash 下线程/omega_frac 空保护/安装器 0x07 控制字符/ac-logs 备份对齐 schemaVersion）；③七条动效+KovaaK 安装指引卡+模型 reasoning 力度档位投影；④1002 分析器家族能力随车。

发版坑新账：**`tauri build` 不重编 coach-sidecar/PyInstaller runtime 二进制**——改 sidecar/backend 必须先 `build-windows-runtime.ps1` 再 tauri build（本次首轮终验偷 key 仍 200 即此因，活体验证抓出）；installer 脚本签名挂死时 exe 已完整，`tauri signer sign` 补签 1 分钟收尾。

同日 accounts 仓上线（`7c57d9f` 退款链 + `7109f7c` 文案手术）：账单页清除订阅时代"自动续费"残留（取消/恢复卡、FAQ 等），对齐按月购买事实——真实客诉（用户点取消自动续费被告知无订阅）闭环；部署方式实证=`wrangler deploy` 非 push 自动部署。usage_pusher.py 已入库待 ECS 重启生效。

## 2026-10-02 — v1.3.5 发布：会话治理/采集可观测/onboarding 死循环修复

发版内容（5 commit + 发版 commit + landing）：①compaction 触发修复（0927 挂账：切超长会话首请求绕过压缩直发 62 万 tokens）——shouldCompactNow 折叠视图 + CJK 感知字符估算取 max；怪物会话分块摘要兜底（pi 原生 session_before_compact + generateSummaryWithUsage 链式）+ 超长单消息块内截断。**真机 e2e 全链验证**：902 超窗会话（2.1MB/零压缩条目）触发→分块摘要 7 连发（网关日志）→压缩条目落库（0→2）→巨型消息摘要折叠（界面 443894→1880 字符）→模型识别干扰内容正常回复。②采集自愈可观测（trace-coverage-gap 研究 P1/P4）：recover_unhealthy_raw 结构化事件+限频+重启前 barrier flush，receipt echo 进诊断包（schema v7→v8）。③onboarding 态1 试用出口：官方模式新用户死循环（态1 无试用出口 × 订阅页验证闸，今天 8 注册 4 卡死；粉丝 skyxz2000@qq.com 手动写 verified_at 解锁，另 3 个同状态用户 EWLeB8e0/tAb0TYV1/GKGvzXy 待拍板处理）。

网关侧（accounts 仓 cd09edb，已部署）：aiohttp 默认 1MB 请求体上限 413 拒客（用户 223.167.246.53 连拒 24 次流失）→ 48MiB + 413 观测日志，真机三连击验证。

测试基线：cargo 177/pytest 869/coach-runtime 523/前端 unit 216/contracts 336 全绿（3 处既有红不在本批域）；L3 沙箱冒烟 + 覆盖装真机 CDP e2e（surface/coach 回合/设置/历史/console 零错误）。

发版新坑三账：①`build:tauri` 只备前端静态产物，真打包= `build-windows-installer.ps1`（含运行时重建——改 coach-runtime 必须走它）；②更新签名密码=空串但须显式 `TAURI_SIGNING_PRIVATE_KEY_PASSWORD=""`，否则无 tty 下挂死；③`test-windows-installer.ps1` 冒烟不恢复 NSIS 安装目录注册表 → 真机静默装被导回冒烟临时目录（本次已修：save/restore）。

发版新坑再账（1005 凌晨 agent 打包实录）：④空串参数经 bash→`powershell -File` 嵌套转发会被**吞掉**（`-UpdaterSigningKeyPassword ""` 传不到，脚本报缺参）——签名 env 在自写 wrapper 里直接 `$env:...=""` 设置、**不传参**（脚本仅在传参时才覆盖 env，不传即透传）；⑤wrapper .ps1 含中文路径（如 C:\Users\袜子\...）必被 PS5.1 按 GBK 读成乱码 → -File 指向不存在路径**假失败**——wrapper 纯 ASCII、路径用 `$env:USERPROFILE`/`$PSScriptRoot` 运行时拼；⑥tauri NSIS 收尾的更新签名会挂等密码（进程存活零 CPU）——杀掉构建树，exe 已产出，`TAURI_SIGNING_PRIVATE_KEY="$(cat ~/.tauri-keys/aiming-cookie.key)" npx tauri signer sign <exe>` 补签 1 分钟（**-k 收的是钥匙内容不是路径**），再手补 .sha256/.unsigned.txt；⑦一切以**产物时间戳**判定成败，退出码 0 可能是内层早退。coach-runtime 在途 WIP（串视频 9 文件）继续留工作区未随发。

## 2026-10-01 — v1.3.4 发布：B1 空回复止血热修（手术式拆分，pi 解耦不随发）

1001 全天巡查+研究后按点点拍板走「手术式拆分」：`a10fa86`（B1/A3/C2）+ `2c11380`（版本号+changelog）随 1.3.4 先发，pi P0 解耦改动留在工作区不随发（待独立分支保护，等 0.99.2 升级窗口随 1.4.0）。主内容：B1 空回复同 harness 受控重试恰一次（nudge 不落历史；provider 错误体透传供前端 §5.3 分流）；A3 前端 `quota_prehold_insufficient` 分类+中英文案；C2 内置档模型白名单封堵+官方档模型锁读时自愈+needs_reselect 指路。

测试基线（发版前实测）：coach-runtime **512=510 过/0 败/2 跳**（含 B1 专项 3 条：重试恰一次/仍空中文文案/重试请求无空 assistant 条目——上游 pi 转换层整条跳过，二次 422 无从发生）；前端 **335 过/0 败**（发版树与主树前端逐字节同）。L3 安装冒烟全绿（SHA-256 `5fa05764…`）后追加**覆盖装真机 CDP e2e 五条全绿**：二进制与 UI 版本 1.3.4、真实 Coach 回合收到真实回复、设置/历史页打开、console 零错误。

同日挂账：28000 采集加固 A+C 方案备妥待派工（今日唯一 28000 报障=4090 驱动没装环境问题，话术已回用户）；支付→入账「门铃」施工完毕（Worker 37 绿+铃自检 8 过），待点点拍板部署（60s 轮询兜底不变）。

## 2026-09-30 — v1.3.3 发布：奇数分辨率录制防线 + 采集子进程可观测

同日 1.3.2（28000 预览版 WGC 逐适配器重试/按局深度分析/训练记录删除/DeepSeek 直连修复）发布后，当晚报障巡查定位出「MF 编码器集群」：分数 DPI 缩放/虚拟显示器下 KovaaK 窗口宽高为奇数时，硬编 BGRA→NV12 转换与软编 output type 双双拒绝，视频采集整场不可用（报障机视频从未成功过）。1.3.3 同晚发版：

- **编码器尺寸防线（window_capture.rs）**：WGC 会话启动一次性向下取偶（裁 ≤1px），编码器构造/replay 导出/MP4 writer 三消费点同值；<2×2 或超 4096 显式终态；软编 staging 保源尺寸、回读按编码尺寸裁剪，writer readback 改 CopySubresourceRegion 裁剪；真 MFT 奇数源回归锁（321x241→320x240）入 cargo。
- **编码器诊断补全**：软编回退点保留硬件层错误（`lastHardwareRejection` 进诊断包）；`WindowCaptureStatus` 增 capture/encode 尺寸四字段；终态错误统一 `capture WxH@fps` 前缀；软编聚合补 candidates 计数；诊断包 schema v5→v6。
- **采集子进程 rc=1 加固（telemetry_capture + service）**：camera_probe/target_poll2 初次路径加固（find_pid/Proc 附着重试 3×2s）+ 采样段 <5s 防热自旋；子进程 stdout/stderr 合并落 `{role}.log`，崩溃尾部经 `child_log_tail` 进诊断包。
- **文档**：PRD/ARCH/ROADMAP 同步遥测四档口径（telemetry_multimodal 优先）；AGENTS/CLAUDE 退出 git 跟踪（点点拍板本地保留，.gitignore 收口）。

测试基线（发版前实测）：Rust **170 过/0 败/7 跳**（含 4 新测试）+ clippy 净；pytest **1648 过/5 跳 + 1 条既有 flaky**（`test_external_deletion_is_detected_and_ledger_recomputed`：目录 mtime 同 tick 漏检实锤——本机「建文件→stat→删→再 stat」mtime_ns 零变化，单跑必过；09-06 cba1234 引入，非本批域）；前端 type-check 0 错 + unit/contracts **335 过/0 败**；coach-runtime **503 过/2 跳/0 败**；pi-ai 740 过/12 败=HEAD 基线（7f6c066 记录在案：kimi-coding 从 models.dev 消失致 0.83 pin 后 strict hydrate 全阻，数据冻结 08-27）。L2 真 Tauri E2E 全绿（desktop-matrix/managed-media/interaction-polish×2 + diagnostics-export-live v6 断言）。L3 安装冒烟全绿（静默安装 + 打包版 CDP 渲染 packaged-release 5.2s + 单实例二次启动；SHA-256 `9a76ce15…`；Authenticode 未签名=内测预期）。

已知遗留：存储台账 flaky 待修（测试内跨 tick 或 utime 触发即可）；pi-ai 12 红为上游活数据漂移，需 pin 内救济或上游跟进。

## 2026-09-21 — i18n 双语收官：英文体验全链可用（前端 1359+53 键/侧 + 后端三波）

一天内完成「完整英文体验」（点点拍板）全部可自主施工的部分，19 个 commit（`209e782`..`5c98060`，含双语 README）。架构决策（点点拍板）：**自建轻量字典**（无第三方 i18n 库，静态导出单页用不上路由级 i18n）；**知识库 921 条不翻**（教练读中文库、按语言转述，跨语言 RAG 标准做法）；**教练语言与提示词解耦**——语言指令块恒定注入、跟随用户消息语言，不维护英文提示词全家桶。

- **前端（批 0-5 + 收尾）**：字典底座 `lib/i18n/`（zh-CN 源语言 / en-US `satisfies` 编译期键校验 / `t()` 插值 / `useT()` 静态导出水合安全），分片机制支撑多批并行施工；55 个含中文源文件全量抽取 **1359 键/侧** + 错误码 53 键；设置页语言切换（两档单选、即时生效+持久化）、**首次启动按系统语言预选**（zh*→中文/其余→英文，手动选择后不再跟随）、4 处日期格式随 locale。
- **耦合雷全拆**：capture-events 显示词枚举化（`CaptureEvidenceStatus`）、「新对话」跨层哨兵常量化、kovaak feedback tone 结构化、CoachPanel:104 中文标点字符串手术改插值、contracts.ts 对后端文本的中文正则分类改稳定键、RSC import 红线（Server Component 直连 core）。
- **后端**：X-Locale 管道（前端→Python→coach-runtime，RunRecord 带 locale）；**104 条报错错误码化**（api 层 code 优先查字典，老客户端 message 兼容通道保留）；**诊断/指标文案双语目录化**（82 指标×2 语言、mapping 官方 en 变体、labels 目录、训练卡/timeline/warnings 双目录；worker 落盘带 locale，**结果语言=生成时语言，读侧不回翻**）；教练语言指令块（含 24 条术语中英映射）+ teaching-policy 双语校验 + timepoints/rich-text 英文解析 + intro 开场与会话自动命名跟随消息语言 + 兜底话术双目录。
- **Rust/NSIS**：invoke 错误码化 8 码（与 `lib/desktop.ts` 重复文案定单一事实源）；安装器 LangString 双语（跟随系统语言，SimpChinese 为回落档）。

测试基线（收盘实测）：pytest **1621 过 / 5 跳**；coach-runtime **470 过 / 2 跳**；前端 unit **185** + contracts **349**，tsc 0 错；Rust **139 过**。安装包 `Aiming Cookie_1.2.5_x64-setup.exe`（09-21 00:21 重打，含全量改动）已产出，待点点真机验收。

遗留（未做/待拍板）：e2e 全量 28 个失败均为 09-06~09-13 界面改版后的过期基线（独立工单；i18n 改动已逐条洗清，且顺手修复了 fixtures bridge 被 esbuild `__name` 注入破坏的基建断裂）；真实 LLM 英文会话冒烟待真机走查；安装器双语未做双系统实机安装；第三方知识包（pack 模式）诊断 copy 保持 zh 语料（与 registry 同口径，拍板跳过，前端 `presentDisplayText` 已透传）。

## 2026-09-20 — 知识库 SDK 一期收官（16 包完工 + 深夜 review 修复）

知识库 SDK 一期 16 个工作包（WP-01～WP-16）全部完工：词汇表冻结、registry/mapping 校验器（Python + TS parity）、静态/家族规则引擎、官方 v13 组装、知识包导入/激活/卸载链路（Python 校验 + sidecar TS parity + 设置页三步向导）与 SPEC/模板交付。

同日深夜 review 出 1 个 P0 + 2 个 P1，均已修复并重跑受影响闸：

- **P0**：`validate_pack` 补 registry schema_version 必须 v3 的前置门（新错误码 `registry_schema_version_invalid`），堵住 v1 形状 registry 绕过第三方来源天花板的洞；sidecar `/knowledge/validate` 加同一前置检查，parity 口径一致。
- **P1**：sidecar 新增 `POST /knowledge/rematerialize`，backend 激活端点写完 config 后即时调用——知识库切换立即生效（下一次对话即用新口径）；sidecar 不可达时如实降级为「重启应用后生效」。前端与 SPEC 的「重启 Coach 会话后生效」失实文案全部改正。
- **P1**：分析侧补 `knowledge_fallback_official` 结果 warning（含回退原因），坏包回退官方在分析结果里可观测。

总闸结果（修复后实测）：pytest `tests/coach/` **338 过 / 0 败**、`test_worker.py` 102 过 / 2 败（两条均为既有 v12→v13 版本号断言未更新，非本批范围）、`test_knowledge_pack.py` 56 过、`test_knowledge_packs_api.py` 12 过；coach-runtime `node --test` **453 测试 / 451 过 / 2 跳 / 0 败**；前端 type-check 0 错、unit+contracts **325 过 / 0 败**。

说明：v13 registry 的 dynamic fail-closed 统一是有意变更，不是回归。遗留 P2 五条（registry/mapping 加载缓存、安装/激活链路线程安全、TOCTOU 注释、真机验收等）留待后续批次。

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
- Coach 自动选最强可用 Run 档：`telemetry_multimodal` → `multimodal` → `input_native` → `video_fallback`；真值口径以 KovaaK 遥测为权威，CV 是行为细节与回退档（视频 e2e 回归锚的意义所在，见 `webapp/tests/test_e2e.py`）。
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
