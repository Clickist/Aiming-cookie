# 测试档位与发版验收清单

本文件是测试档位定义与 L4 发布验收清单的主责任文档。它回答"什么改动在什么时刻必须过什么测试"；命令入口在 [`DEVELOPMENT.md`](DEVELOPMENT.md)，发布 Gate 的 Go/No-Go 仍在 [`ROADMAP.md`](ROADMAP.md)，最近一次验证结果写 [`PROGRESS.md`](PROGRESS.md)。

## 1. 四档总览

| 档 | 时刻 | 真实度 | 现状 |
|---|---|---|---|
| **L1 代码门禁** | 每次改动、提交前 | 假壳+假数据 | 已有：pytest、前端 unit/contracts、coach-runtime node 测试、cargo 四件套、浏览器 E2E |
| **L2 实机功能集成** | 功能完成、合并前 | 真 Tauri dev 壳+fixture 数据 | 已有 7 个 `real Tauri` 用例（`run-tauri-e2e.ps1`） |
| **L3 打包版验收** | 每次打出候选包后 | 真安装包+fixture 数据 | 半成品：`test-windows-installer.ps1`（安装冒烟+CDP 渲染冒烟）、`test-packaged-runtime.ps1`（runtime 资源+健康检查）；功能用例待补 |
| **L4 发布全旅程** | 每次对外发版前 | 真安装包+真实使用 | 本文件第 3 节清单 |

分界线：**向右真实性递增、频率递减、人工占比递增**。L1 拦逻辑错，L2 拦集成错，L3 拦"开发模式全绿但打包版不可用"（打包环境分歧三类坑），L4 拦"单功能都对但用户旅程断裂"。

L4 六段清单中，🔁 = 可自动化（脚本/CDP 断言），✋ = 必须人工。

## 2. L1–L3 维护规则

- **L1**：按改动范围跑最小相关集；发版前跑全量。入口见 `DEVELOPMENT.md` §4。规则：改 `coach-runtime` 源码必须重编 sidecar 才算验证过；改 `webapp/backend/**` 必须重建 runtime 镜像。**浏览器 E2E（`npm run test:e2e`，纯 Edge）已废弃为验收路径**（2026-09-15 点点确认）：套件仅作存量保持绿，不作为任何档的验收依据，不在发版流程中跑；验收一律走真机 CDP（L2/L3/L4）。
- **L2**：每新增一个用户可见能力，配套加一个 fixture 驱动的 `real Tauri` 用例，保持"新功能不裸奔进 L3"。现有用例：desktop-matrix（能力矩阵）、desktop-managed-media（媒体播放降级）、interaction-polish×2（布局/表面）、desktop-coach-provider（真 Provider 回合，field test）、diagnostics-export-live（诊断包）、packaged-release（打包版渲染冒烟）。
- **L3**：补建打包版功能套件——静默装出包→起 CDP→复跑 L2 的 fixture 驱动用例（desktop-matrix、managed-media、interaction-polish、诊断包导出）。打包版 WebView 与 dev 版同源，用例理论可复用；跑不通处即"打包环境分歧"暴露点。现有入口：`test-windows-installer.ps1 -InstallSmoke -WebViewSmoke` 已能静默装+起 CDP+跑 packaged-release.spec.ts，扩展其 spec 集即可。
  ⚠️ **已知缺陷（2026-09-15 实锤）**：该脚本的 APPDATA 环境变量隔离对 Tauri 数据无效——Windows 上 `app_data_dir` 走 Known Folder（`DEVELOPMENT.md` 打包铁律同源问题），打包冒烟实际跑在真实 profile 上，断言仅"产品表面渲染"所以未露馅。真隔离需 identifier 覆盖构建（L4-D3 同款 `com.aimingcookie.l4smoke` 做法）；改造前不要把该冒烟当"全新环境"证据。
  L3 排障三铁律（打包版行为与 dev 不符时按序排查）：①bun 打包后 `import.meta` 相对路径失效→数据文件必须经 `AIMING_COOKIE_RESOURCE_ROOT`，新数据文件必须进 `build-windows-runtime.ps1` 拷贝清单；②tauri 增量打包可能不重嵌前端资产→重打包前删 `src-tauri/target/release/build/aiming-cookie-desktop-*`；③先验包再疑码→在活页面 fetch 它自己加载的 chunk 验字节数/标记串，区分旧包/旧缓存/真 bug。

## 3. L4 发布全旅程验收清单

按用户旅程切六段。执行模式见第 4 节（全量/抽测口径）。

### 3.0 测试环境与凭据（真实测口径，2026-09-15 拍板）

- **真实安装**：L4 一律用打包出的安装包真实安装后测试，不用 `tauri dev`。onboarding 人工真实走完（步骤 1 Provider 连接 → 步骤 2 Windows 采集授权），不走任何跳过路径。
- **测试 key**：用本机 `%APPDATA%\com.aimingcookie.desktop\config\provider.json` 里已配好的两个真实档——**id=4「Aiming Cookie 官方」内置档**（onboarding 主路径用它）与 **id=3「自家中转站」自定义 OpenAI-compatible 档**（Settings 里验证自定义档形态）；DeepSeek（id=1）备用。**key 本身不写入任何文档、测试文件、截图或日志。**
- **真机即本机**：中文用户名 Windows 环境，安装、数据目录、sidecar 全程走中文路径，本身就是中文路径工况的真实覆盖。
- **环境准备清单**（跑 L2/L3 前核对）：仓库 `.venv` 就绪；pinned Pi `dist` + `hydrate-model-data` 完成；`artifacts/eloshapes/` 已从主仓复制（gitignore，缺失时 coach 测试爆发导入失败）；`webapp/frontend/fixtures/task7-video.mp4` 在位；需要全新首启时清理目标 profile 的 `onboarding.json` / `intro-session.json` 一次性 flag。

### D1 分发链 🔁（目标全自动化，落成 `check-release-assets.ps1`）

| # | 用例 | 判据 |
|---|---|---|
| D1-1 | 落地页中英双页下载按钮指向本次版本 | 页内 URL 版本号 = 本次发版号 |
| D1-2 | R2 三件套可下载 | exe / .sig / latest.json 均 200 且非空 |
| D1-3 | latest.json 内容正确 | 版本号对；双语更新说明在；signature 字段在（minisign 签名，实际验签发生在更新端 D5-1 时刻；latest.json 合同本身不含 sha256） |
| D1-4 | 老版本能收到更新 | latest.json 版本 > 全部线上受支持版本 |
| D1-5 | 落地页完整性 | hreflang/sitemap/版本号文案未被发版改动破坏 |

### D2 安装链

| # | 用例 | 方式 | 判据 |
|---|---|---|---|
| D2-1 | 干净环境静默装→主窗口出现→单实例生效 | 🔁 已有 | `test-windows-installer.ps1 -InstallSmoke -WebViewSmoke` 绿 |
| D2-2 | 安装器 UI 向导人工走一遍（含取消/重装路径） | ✋ 每版一次 | 向导可完成，失败路径不卡死 |
| D2-3 | **覆盖升级安装**：装上一版→产生会话/Provider 配置/训练计划/历史→直接装新版→旧数据全保留可读 | ✋（后续可脚本化：seed profile + CDP 验数据） | 会话列表、Provider 配置、训练计划、分析历史全部还在且可打开 |
| D2-4 | 卸载干净 + 数据目录行为符合合同 | ✋ 抽查 | 程序目录移除；用户数据目录语义符合 ARCHITECTURE 合同 |
| D2-5 | 未签名安装包的 SmartScreen/杀软提示走查 | ✋ | 提示可继续安装（内测期无 Authenticode 签名为已知状态），无致命误报拦截 |

### D3 首启链

| # | 用例 | 方式 | 判据 |
|---|---|---|---|
| D3-1 | 真实安装→全新 profile 首启→onboarding 步骤 1（Provider）是硬门槛 | ✋ | 不配置 Provider 无法进入步骤 2 和主工作区 |
| D3-2 | 三段说明完整（产品免费/第三方费用/数据边界） | ✋ | 步骤 1 页面三信息可见 |
| D3-3 | 用官方档（id=4）真 key 完成 onboarding 连接测试 | ✋ | 测试连接成功→可进步骤 2；进主工作区后官方档两视图正常（会员计划视图 + API 计费真余额） |
| D3-4 | onboarding 步骤 2：Windows 采集授权 opt-in | ✋ | 采集范围与关闭方式说明在；授权开启后 backend.log 落位正确 |
| D3-5 | Settings 补挂自定义档（id=3 自家中转站）：BaseURL+Key+⟳发现模型+设为当前 | ✋ | 模型列表拉到、测试连接成功、active 切换官方↔自定义均生效 |
| D3-6 | 错误 key 负向：显示失败红字，不崩不卡 | ✋ | 恢复正确 key 后自愈 |
| D3-7 | 开场分析自动创建（首启一次性） | ✋ | 四问流程走通；**红线：只出成绩层结论，不出现动作层断言、不出处方卡**；切走再回来不再自动创建 |
| D3-8 | 默认路由矩阵（PRD §5.6 五分支抽验） | 🔁 CDP | 无历史→Coach 首页；有待分析→History；开场分析未建→自动创建等 |

### D4 功能矩阵（核心闭环深度，按模块）

标注说明：**[自动]** = 已有 L1–L3 用例锁定，L4 无需逐条重跑，只在打包/真机环境抽验确认无环境差异；**[真机]** = L4 必须人工或 CDP 断言验证。

#### 模块 C：Coach 对话工作区

| # | 功能点 | L4 验证 | 方式 |
|---|---|---|---|
| C-1 | Session rail：会话列表、自动起名（首条消息后生成短标题）、新建、切换、会话保留 | 发 2-3 条对话后标题生成；重启后列表与草稿还在 | [真机] CDP |
| C-2 | Composer：@提及、划选引用（context_refs 结构化，Coach 能看到引用内容）、草稿保留 | 引用一条分析→Coach 回答体现引用内容 | [真机] |
| C-3 | 发送键三态：发送→终止→排队/立即打断 | 活跃 run 时点终止立即停；排队任务可立即打断 | [真机] |
| C-4 | 乐观气泡：发送即出气泡，失败回滚 | 断网发送→气泡回滚+错误卡 | [真机] |
| C-5 | 思考流+工具工作流条目：按轮交错、折叠/展开、中途重载不产生假中断标记 | 对话中途刷新页面，回复不丢不重复、无假"回答已停止" | [真机] |
| C-6 | 模型菜单+effort 独立菜单：完整模型名、切换生效 | 切模型后下一回合用新模型（看 usage/行为） | [真机] |
| C-7 | 消息卡片：证据卡、指标卡、计划卡（morph 动效）、错误卡各自渲染正确 | 结合 D4 各场景自然覆盖 | [真机] |
| C-8 | 讨论条挂载：主题/参照分流，不污染后续对话 | 挂主题讨论后新消息仍带上下文，取消挂载后不带 | [真机] |
| C-9 | 视频证据打开→三栏形态（rail+视频+Coach） | 见模块 E | [真机] |
| C-10 | 长对话上下文治理（microcompact）：超长对话不死、早期工具结果被清理但对话不丢 | 长会话连发 20+ 条，Coach 仍记得早期结论 | [真机] 抽测 |
| C-11 | Provider 失效恢复：错误卡+Settings 引导，不强制回 onboarding | 改错 key→对话报错→引导修复→修好即恢复 | [真机] |
| C-12 | 会话管理操作：悬浮菜单（重命名/删除等）与 scrim 浮层 | 走查各操作生效、误触可逃逸 | [真机] |

#### 模块 D：Coach 工具能力（产品命令逐族）

| # | 命令族 | L4 验证 | 方式 |
|---|---|---|---|
| D-1 | 历史读侧：`history.list` / `history.trend` / `run.list` / `run.get` | 问"我最近练了什么/进步如何"→答案引用真实数字 | [真机] |
| D-2 | 分析读侧：`analysis.get` / `analysis.compare` | 问两局对比→只用共同已验证指标，不编造 | [真机] |
| D-3 | `analysis.create_from_run`：对话内发起分析 | Coach 发起→分析入队→完成后自动开讲 | [真机] |
| D-4 | `analysis.retry`：失败分析重试 | 制造一次失败→Coach 重试成功 | [真机] |
| D-5 | `analysis.delete`：删除语义（进行中拒、完成后删且 Coach 记忆保留） | 见模块 G 删除语义 | [真机] |
| D-6 | `navigation.open`：Coach 导航到页面 | "带我去设置"→跳 Settings | [真机] |
| D-7 | 训练计划六命令：generate_draft / save / activate / adjust / pause / review | 生成→保存→激活→练后记录（execution.record）→复测（retest.record）全链；计划卡 morph 展示（练什么/练多少/注意/观察/复测） | [真机] |
| D-8 | `teaching_session.update`：带练会话状态 | 主动带练流程推进 | [真机] 抽测 |
| D-9 | `scenario_memory.set`：用户纠正场景类型→持久记忆→重分析让位 | 纠正一次类型→同 Run 重分析按新大类 | [真机] |
| D-10 | `user_profile` / `peripheral_profile` get+update | "记住我用 1600 DPI"→下个会话仍记得 | [真机] |
| D-11 | `kovaak_scores.lookup` / `refresh_connected` / `kovaak_leaderboard.lookup`：Steam 成绩与排名 | 问成绩→返回真实分数；排名查询返回真接口数据 | [真机] |
| D-12 | `eloshapes.query`：段位形状数据 | 问段位→真实数据引用 | [真机] 抽测 |
| D-13 | `scenario.search`：官方 17.5 万场景库搜索推荐 | 处方库没覆盖的场景→仍能给出搜索推荐 | [真机] |
| D-14 | `scenario.open`：同意门（先问后开）+ 本地 .sce 优先 | 拒绝→不启动 KovaaK；同意→真拉起 KovaaK 对应场景 | [真机] |
| D-15 | `scenario.list`：本机场景列表 | 列出本机场景 | [真机] 抽测 |
| D-16 | `purchase_links.lookup`：外设推荐转链（affiliate） | 证据支持时给推荐+联盟链接可打开+披露商业关系；证据不支持时不推荐 | [真机] 抽测 |
| D-17 | `web_search` / `fetch_page`（DDG 兜底链） | 问时效性问题→真搜索并引用 | [真机] |
| D-18 | 知识库/处方库检索：引用带出处、与 Coach 解释融合 | 分析解释中出现处方引用+出处 | [真机] |
| D-19 | 环境事实：bash/python 可用性按运行时注入作答 | 问"你能跑 python 吗"→按注入事实回答 | [真机] 抽测 |
| D-20 | 身份问答：创作者身份仅被问时说 | 问"你是谁"→按白名单口径 | [真机] 抽测 |

#### 模块 E：视频讲解面

| # | 功能点 | L4 验证 | 方式 |
|---|---|---|---|
| E-1 | 视频 pane 三栏形态切换（开/收） | 证据卡"在视频中查看"→三栏；收起→居中 | [真机] CDP |
| E-2 | @time 回看 chips：跟随讲解短语、4-6 个、seek 跳转正确 | 点 chip→视频跳到对应讲解点（真机帧对） | [真机] |
| E-3 | 逐帧/变速/暂停控制 | 各控制可用 | [真机] 抽测 |
| E-4 | managed media 播放：`aiming-cookie-media.localhost` 206 Range | 已有 L2/L3 用例 | [自动] 抽验 |
| E-5 | 无视频降级：不显示视频按钮、说明限制 | video_fallback 分析→无"在视频中查看" | [自动]+[真机] |
| E-6 | 源文件删除→WebView 本地媒体错误，不崩 | 已有 L2 用例 | [自动] |
| E-7 | 训练计划浮层（视频面内）：定高滚动、右对齐、条目删除、不与重名条目混淆 | 打开浮层走查交互 | [真机] 抽测 |

#### 模块 F：自动采集链

| # | 功能点 | L4 验证 | 方式 |
|---|---|---|---|
| F-1 | 进程 gate：KovaaK 启动触发采集、退出停止 | 开/关 KovaaK 看采集状态词变化 | [真机] |
| F-2 | Raw Input：opt-in 后采到输入；1000Hz canonical；按钮边沿保留 | 打一局→分析里 Raw 指标真实（SPARC 等非零合理） | [真机] |
| F-3 | 300s 有界回放缓冲滚动丢弃 | 连打超 5 分钟→只保留最近 300s，明确降级不伪造 | [真机] 抽测 |
| F-4 | 硬件编码优先，软编降级记录 encoder path | 诊断包里 encoder path 正确 | [真机] 抽测 |
| F-5 | Stats/Performance 事后切分：连打多局切成独立 Run | 连打 2-3 局→History 出现对应条数，不粘连不丢 | [真机] |
| F-6 | 暂停局 fail closed：`Pause Count > 0` 不出永久 MP4 | 打一局按 ESC→partial/unavailable evidence，不声称 canonical | [真机] |
| F-7 | offset_resolver 四级链：KovaaK 更新后偏移重取 | 升级场景抽测或模拟空表→云表兜底→fail-fast | [真机] 抽测 |
| F-8 | 采集状态词：待命/采集中/整理中/完成/失败 | 各状态在 UI 正确表达 | [真机] |
| F-9 | 退出清理：关窗后 sidecar/Python/句柄全退 | 任务管理器无残留进程 | [真机] |
| F-10 | 采集发现底座：Steam 多库发现（libraryfolders.vdf）、50-file bound、watcher 轮询 | 多 Steam 库机器能发现；文件洪峰不失控 | [真机] 抽测 |
| F-11 | KovaaK 统计导出保底注入：写 `SaveStatistics=true`+`StatsExportLevel=1`；游戏运行中拒写 | 关游戏时注入生效；游戏开着时拒绝写并提示重启生效 | [真机] |
| F-12 | 外部遥测导入（ExternalTelemetryRun）：`external_run.v1` 合同、与 KovaaKRun 并行互不依赖、格式不符 fail-closed | 导入一份 cleaned 轮次→History 可见独立条目；喂坏格式→拒绝不污染 | [真机] 抽测 |
| F-13 | Raw Input 前台约束 | 已知开放 Gate（ROADMAP）：验当前实际行为并记录 | [真机] 抽测 |
| F-14 | 高轮询率鼠标（8K）抽测：1000Hz canonical 归一化下不炸、SPARC 合理 | 用高报率鼠标打一局，指标非零且量级正常 | [真机] 抽测 |

#### 模块 G：分析管线

| # | 功能点 | L4 验证 | 方式 |
|---|---|---|---|
| G-1 | 三档路径自动选择：multimodal / input_native / video_fallback | 三档各造一局验一次（全来源；关采集授权重打；导入历史文件） | [真机] |
| G-2 | stats-only 导入（安装前历史文件） | 导入旧 Stats→video_fallback（stats-only）+限制显示 | [真机] |
| G-3 | 四家族专项分析 + movement outcome-only | static/dynamic clicking、tracking、target switching 各一局分析正确；movement 局只出 outcome-only | [真机] |
| G-4 | 场景五级识别链：exact hash→.sce→challenge_shape→名称→兜底（family 未解析标记） | 每级至少一例：审核图、同名 .sce、生僻图名、乱造图名 | [真机] |
| G-5 | 质量 Gate 与 limitations：缺来源必须显示缺失与限制 | video_fallback 结果显示"缺 Raw Input"等 | [真机] |
| G-6 | ETA 分桶+新鲜度：按 analysis_type 分档显示 | flicking 局 ETA ≈ 分钟级不误导 | [真机] 抽测 |
| G-7 | 渐进式披露：processing 可后台、完成 toast+角标不强制跳转 | 分析中切走→回来进度还在；完成有通知 | [真机] |
| G-8 | evidence_segments 与 @time 锚生成 | 分析完成的局有证据分段供视频面消费 | [真机] |
| G-9 | 分析版本升级 STALE 重新分析提示 | 跨版本抽测 | [真机] 抽测 |
| G-10 | 删除语义：进行中不可删；删除分析不级联 Coach 记忆、引用降级显示 | 删一条被 Coach 引用过的分析→对话引用显示已删除，Coach 仍记得结论 | [真机] |
| G-11 | worker/stale-job 恢复：分析中途杀进程→重启后无永久"分析中"幽灵 | PRD 成功标准：异常退出不留无法恢复的进行中状态；失败可识别可重试 | [真机] |
| G-12 | 分析工作区三视图（数据/诊断/视频，AppShell 挂载）：三视图数据同源一致 | 同一分析在三视图间切换，指标数字一致 | [真机] |

#### 模块 H：History

| # | 功能点 | L4 验证 | 方式 |
|---|---|---|---|
| H-1 | 待分析区：选择 Run 开始分析、多条选一条、其余找回 | 多局→选一条→其余仍在待分析 | [真机] |
| H-2 | 列表交互：按天分组、过滤框、标题搜索、折叠、整行点选、圆点勾选、"更新于 N 前⟳"防抖 | 手工走查 | [真机] |
| H-3 | 分析记录摘要与交给 Coach | 从 History 发起"让 Coach 分析" | [真机] |
| H-4 | 导入/导出 | 导出一份数据→删除→导入恢复 | [真机] 抽测 |

#### 模块 I：Settings 五屏

| # | 屏 | L4 验证 | 方式 |
|---|---|---|---|
| I-1 | 通用：主题切换（明暗跟随+持久化）、应用更新检查、Profile 三态按钮（灰存→橙存→删除退回） | 逐项点一遍 | [真机] |
| I-2 | LLM Provider：官方档两视图、内置档 Key+⟳获取模型、自定义档 BaseURL 失焦即存、设为当前、编辑/删除、自动测活绿红点 | 逐项走查（与 D3-3/4 合并跑） | [真机] |
| I-3 | 自动采集三卡：状态词、授权开关、范围说明 | 开关授权即时生效 | [真机] |
| I-4 | KovaaK：本地目录自动发现、S2 同意勾选 | 目录显示本机 KovaaK 路径 | [真机] |
| I-5 | 数据与存储：分类占用、打开位置、手动移除 | 移除一个 Run 录像→占用下降、分析降级说明出现 | [真机] |

#### 模块 J：跨切面

| # | 功能点 | L4 验证 | 方式 |
|---|---|---|---|
| J-1 | 双主题全页面跟随 | 明暗各截一轮关键页 | [真机] |
| J-2 | 窗口：最小宽度 1180、最大化/全屏、拖拽带、三键浮条 | 缩放/全屏走查 | [真机] |
| J-3 | toast：键盘关闭、自动消失、reduced motion | 已有 L2 浏览器用例 | [自动]+[真机] 抽验 |
| J-4 | 诊断包导出 v5：字段完整、session id 剥离 | 已有 live 用例，真机再导一次人工看内容 | [真机] |
| J-5 | 日志一键上传：上传成功返回链接 | 真机传一次 | [真机] |
| J-6 | 启动性能：设置/历史秒开 | 感官验证 | [真机] |
| J-7 | 长时间运行稳定性：连打 N 局内存不涨不崩 | 大版本抽测 | [真机] |
| J-8 | 安全边界：API 受 launch token 保护、响应无 token/绝对路径泄漏 | 已有 desktop-matrix 断言 | [自动] |
| J-9 | 无障碍：键盘可达、焦点、reduced motion | 已有 L1 浏览器用例 | [自动] |
| J-10 | 凭据零泄漏（PRD 红线）：provider key 不进日志、诊断包、Coach 消息、导出 | 全链路抽查：诊断包/日志文件/对话记录里搜不到任何 key 片段 | [真机] |
| J-11 | ErrorBoundary：局部渲染崩溃有兜底，不死白屏 | 走查异常路径不出现无提示白屏 | [真机] 抽测 |
| J-12 | 代理环境抽测：系统代理开启下 Provider 请求可达 | 大陆用户真实环境 | [真机] 抽测 |
| J-13 | DPI 缩放/多显示器抽测：150%/多屏下窗口与浮层不错位 | 走查 | [真机] 抽测 |
| J-14 | 中文路径工况：中文用户名下安装/数据目录/sidecar 全链正常 | 本机即此环境，随全量自然覆盖 | [真机] |

#### 模块 K：在线面（发版链依赖）

| # | 功能点 | L4 验证 | 方式 |
|---|---|---|---|
| K-1 | 落地页双语言+下载（同 D1） | 🔁 | D1 覆盖 |
| K-2 | ac-offsets 云表可达 | 🔁 curl 冒烟 | 发版抽测 |
| K-3 | ac-logs worker 可达 | 🔁 随 J-5 验证 | |
| K-4 | affiliate-worker 转链可达 | 🔁 curl 冒烟（未部署期间标注跳过） | |
| K-5 | 官方托管余额端点 | 随 D3-3 验证 | [真机] |

#### 模块 S：Skills 系统

Coach 的 9 个 skill（`coach-runtime/prompts/skills/`）分三类：引导类（intro-session）、执行类（sensitivity-fitting、plan-builder、peripheral-reference、ergonomics-check）、教学类（teaching、self-review、recovery-descale、kovaak-data-reference）。加载是软引导：系统提示词让 Coach 自主判断激活，无代码层强制门。设计合同见 [`skills-design.md`](skills-design.md)。

现有自动覆盖：L1 `skills-loading.test.ts`（9 个 skill 全部可加载，含 Windows 路径归一化）、`skill-read-events.test.ts`、intro/teaching/peripheral 各自的单测与 live 测试；L3 `test-packaged-runtime.ps1`（打包 skills 数量与源一致）。**真机缺口是触发与执行行为**——单测只保证"文件能加载"，不保证"Coach 在该用的时候真用了、步骤走对"。以下用例各用一句触发问句在真机对话里验证激活与产物：

| # | Skill | 触发场景 | L4 验证 | 方式 |
|---|---|---|---|---|
| S-1 | 加载底座 | 打包版任意对话 | Coach 能读到 skill 内容（抽 2-3 个问触发问句，回答体现 skill 步骤） | [自动]+[真机] |
| S-2 | intro-session（引导） | 首启开场分析 | D3-6 覆盖，交叉引用 | [真机] |
| S-3 | teaching（教学） | 分析完成自动开讲、阶段推进 | 开讲一次+推进一个阶段，`teaching_session.update` 状态正确（与 D-8 合并跑） | [真机] |
| S-4 | plan-builder（执行） | 用户要训练计划 | 与 D-7 计划六命令合并跑：草稿符合 plan-builder 合同 | [真机] |
| S-5 | sensitivity-fitting（执行） | 用户要校灵敏度 | 进入 fitting 流程，步骤与产物符合合同 | [真机] |
| S-6 | ergonomics-check（执行） | 用户提到疲劳/姿势不适 | 触发检查流程而非闲聊 | [真机] 抽测 |
| S-7 | peripheral-reference（执行） | 外设问题诊断 | 引用外设参考数据（已有 live 测试，真机再验对话触发） | [真机] 抽测 |
| S-8 | kovaak-data-reference（教学） | 讨论 KovaaK 数据/指标含义 | 引用数据参考而非编造 | [真机] 抽测 |
| S-9 | recovery-descale（教学） | 用户状态差/成绩下滑 | 触发降档/恢复建议 | [真机] 抽测 |
| S-10 | self-review（教学） | 长会话收尾/复盘 | 触发自查流程 | [真机] 抽测 |
| S-11 | 负向触发 | 与 skill 无关的普通闲聊 | 不误激活（软引导的负向面） | [真机] 抽测 |

### D5 更新链（真机）

| # | 用例 | 判据 |
|---|---|---|
| D5-1 | 旧版实机收更新→下载→验签→重启升级 | 版本号变新；会话/配置/计划/历史全保留 |
| D5-2 | 同版本不弹更新 | 无更新弹窗 |
| D5-3 | 升级后抽验 2-3 条 D4 关键路径 | 发一局分析+一轮对话正常 |

### D6 收尾

R2 三件套与落地页切换确认（发版 runbook 步骤清单化）、git tag、内测公告（含应用内更新公告待办）。

## 4. 执行模式（2026-09-15 点点委托按推荐默认执行，可随时翻）

- **全量触发**：大版本（能力新增/合同变更）跑 D1–D6 全量。
- **抽测触发**：小修补版只跑 D1 + D2-1 + D5 + D4 按本次改动范围选 2-3 条；D2-3 覆盖升级、D3 首启链每 2-3 个版本回归一轮。
- **家族覆盖**：D4 G-3 按本次改动范围选家族，每月至少一轮四家族全量。
- 测试档位的执行结果与偏差写入 `PROGRESS.md`，不在本文件维护日期化状态。

## 5. 落地路线

1. 本文件落库即完成"L4 清单化"（零代码）；
2. `check-release-assets.ps1`：D1 五项校验脚本化，并入发版 runbook；
3. L3 打包功能套件：扩展 `test-windows-installer.ps1` 的 spec 集，复跑 L2 fixture 用例；
4. D2-3 覆盖升级、D5 更新链脚本化（seed profile 方案）。
