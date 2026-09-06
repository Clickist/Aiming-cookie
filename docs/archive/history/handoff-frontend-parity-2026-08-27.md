# 前端「追平主流 Agent 桌面端」工程 · 交接总纲

> **状态：交接快照（2026-08-27）。** 本文与 [`frontend-parity-research-digests.md`](frontend-parity-research-digests.md) 合读即本工程的完整上下文。文中"批次/排期"均为建议草案，需点点逐批批准后施工；本文不构成实施授权。配套提案：[`video-pane-revamp-brief.md`](video-pane-revamp-brief.md)（已单列）。

## ① 工作区未提交改动盘点（截至本快照）

工作区压着 **六个已完成并验证的批次**，建议按下述顺序分别 commit：

| 序 | 批次 | 主要 touched | 验证结论 |
|---|------|-------------|----------|
| A | **pi 底座升级** 0.80.6→0.83.0 | `third_party/pi/**`（384M/30D/108 新增 tracked）；`third_party/pi/PROVENANCE.md` 重写（记录 0.84.x harness 脚手架化阻塞原因）；`docs/DEVELOPMENT.md` pinned-Pi 恢复步骤（新增 `hydrate-model-data` 必经步）；coach-runtime 5 个测试文件的断言级适配（版本号×2、Vertex auth_modes+interactive、DeepSeek "(New)" 后缀、OAuth 夹具有效期移出 5 分钟主动刷新窗） | coach 全套件 **262 总 / 259 过 / 1 环境失败(eloshapes 缺件) / 2 跳过** = 升级前基线持平；vendor 树 vs pristine v0.83.0 仅差 dist/providers-data/PROVENANCE |
| B | **Coach 对话流 A 档**（过程呈现） | `components/task6/CoachPanel.tsx`、`task6.css`、`tests/task6-source.test.ts`；新增 `CoachRunActivity.tsx`、`tests/task6-coach-activity.test.ts`。功能：SSE thinking_text 思考折叠块（流中自动展开/完成收起/"已思考N秒"，用户切换后自动化让位）；成功回合活动归档不再消失；工具步耗时/args/result_preview 可展开+完成收敛计数；分析类长任务 m:ss 计时并列 ETA；pending chip 呼吸点+计时；流式文字同构走 CoachMessageText；reduced-motion 死规则清理 | type-check 绿；涉及测试全绿；下游注意到 task6.css 有阴影白名单与禁 grid-template-rows 两条合同测试（本轮已依规实现） |
| C | **plotly 死依赖清除** | `package.json` 删 plotly.js-dist-min/react-plotly.js/@types/react-plotly.js；lockfile −2847/+120 | type-check 绿；unit 40/40；contracts 仅剩 build:tauri 环境失败；零引用复核过 |
| D | **会话侧栏三小修** | `SessionRail.tsx`、`session-rail.css`（时间分组接线 今天/昨天/近7天/更早）、删除改两步行内确认、遮挡问题核实为误报→以测试锁定现状合约；`tests/task7-session-rail.test.tsx` 4→7 | 单跑 7/7；contracts 通过数升 190，无新增失败 |
| E | **Provider 先验后存**（跨层） | sidecar `provider-profiles.ts` 新增 `POST /v1/provider-profiles/test` 干跑路由（零持久化，+14 行）；`lib/api.ts` +23 行 `testProviderProfileDraft`；`SettingsWorkspace.tsx` 保存门控+指纹失效+可取消+aria-live 内联结果区；新增 `provider-profiles-dryrun.test.ts`(4)、`task6-provider-dryrun.test.ts`(3)。**例外知情项：OAuth 类仍走先存后授权（draft 无法预验），文案已说明** | frontend 全套绿；sidecar 262 总口径内无回归 |
| F | **杂项加固/文档** | `scripts/sync-api-types.mjs` 临时目录回退仓库根修复（os.tmpdir()）；`docs/README.md` 登记 video brief 与本文；`docs/video-pane-revamp-brief.md` 新增（见上） | sync --check 通过 |

另：`webapp/frontend/package-lock.json` 属 C；node_modules 里 plotly 物理残留到下次干净安装才消失；本机曾缺 `openapi-typescript` 已随补装解决（api-types 清单被证实无漂移）。

## ② 终验数字总表

| 套件 | 结果 | 备注 |
|---|---|---|
| 前端 unit(lib/mocks) | **40/40** | |
| 前端 contracts(tests/) | **193/194** | 唯一失败 `build:tauri`＝Windows-only 门禁（npm.cmd/powershell），mac 环境性 |
| coach-runtime 全量 | **262 总 / 259 过 / 1 环境失 / 2 跳** | eloshapes.query 失败系本机缺 `artifacts/eloshapes/`（不入 git 的已知环境缺口） |
| Python pytest | 未跑 | 六批均未触 backend / worker 行为（E 侦查证实 Provider 链路直连 sidecar 不过 FastAPI） |

## ③ 决策积压（全部等点点拍板）

| # | 事项 | 我方建议 |
|---|------|----------|
| P1 | **"提交"指令**＋按上表分六个 commit | 就差一声令下 |
| P2 | 视频面板 D1–D4（@time 自动暂停／入口三路并存／聚合成簇缓做／循环专属色 token） | 见 video brief 第三节 |
| P3 | Composer 排队立项授权：需要 sidecar 透传 `steer/followUp` 小合同（引擎原生支持，纯透传） | 建议做，价值最高 |
| P4 | 6 枚新 token 色值（--ring-color/--overlay-scrim/--divider-strong/--state-hover/--state-pressed/--shadow-menu，浅深双值已备） | 过目即用 |
| P5 | OAuth 先验例外是否接受（否则需重构 draft-scoped auth） | 接受现状 |
| P6 | 圆角窗口路线 A（decorations:false 下加 DWMWA_WINDOW_CORNER_PREFERENCE=ROUND 十余行 Rust） | 同意则先写好放工作区 |
| P7 | 安装包品牌化的 publisher/copyright 文本定稿（草案 © 2026 Aiming Cookie） | 待 Win 机联调 |

## ④ 实机（Windows）一揽子验收清单

对话流手感（思考块密度/计时噪音）· 设置页先验后存门控 · 圆角窗口效果（若 P6 实施）· 安装包新向导观感（若批次⑩实施）· `npm run test:e2e` 等 Windows 门禁跑绿 · 金标准 114杀/73±10% 回归顺带确认。

## ⑤ 十二路调研索引（细节全文在 [frontend-parity-research-digests.md](frontend-parity-research-digests.md)）

1 对话流工具调用过程呈现（本会话已部分落地为批次B方向依据）2 Provider 接入向导 3 会话侧栏 4 视频复盘面 5 训练数据呈现 6 后台任务与完成通知 7 键盘优先与命令面板 8 对话窗格版式诊断 9 视觉工艺系统 10 富文本渲染与中文排印 11 输入框编排 12 Windows 安装包品牌化。

## ⑥ 排期路线图（建议，逐批批准制）

```
批0 提交落库（P1）
批1 工艺卫生半天：Drawer左滑错位/Toast tone零样式/IconButton无hover/backdrop双定义/Dialog关闭钮重造 ＋ 运行中吞Enter最小止血
批2 token 半天：P4 六枚 → 收敛全站 hover/state-layer
批3 版式改版一批：消息底部锚定+空态hero居中 / 头部瘦身(训练卡并入header线、discussion条仅pending时现) / 内容宽收敛~720px(CJK行长) / 用户气泡大圆角+chip半径体系合一 / composer拆工具行(去padding-right:196px与72vw)
批4 视频 P0+P1 一批（brief就绪，P2决策即可动）
批5 输入框编排一批（P3合同后）：排队chips三操作/发送键四动作菜单/@引用下拉(LibreChat Mention骨架)/草稿三级持久键/↑翻发送历史；编辑重发选截断派
批6 视频 P2 AB循环色带（规格已在brief定稿）
批7 受控富渲染一批：turn.ts 归一化由删字符改白名单透传(list/table/bold)+前端200行受限解析器+中文排印十条入验收(text-autospace原生/弯引号/tabular-nums/1.75行高)
批8 任务通知半~1批：失败分析红chip带retry(analysis.retry合同现成)/跨页徽章置信度门槛/回场聚合一条摘要;系统通知涉Rust另立合同
批9 键盘面板一批：cmdk@15KB + 12键位表(IMO三铁律:isComposing放行/输入焦点短路/禁全域字母键) + focus-visible令牌沉淀
批10 安装品牌化：conf键位(publisher/copyright/languages:["SimpChinese","English"]/headerImage 150x57/sidebarImage 164x314/installerIcon)+图标链路重生成(npx @tauri-apps/cli icon,ico需16-256多层)+customLanguageFiles润色；hooks首启联动=中复杂度可选；整体接管模板=高复杂度不建议
```

## ⑦ 口径与红线备忘

- 十二份调研属证据参考，逐条落地仍须过 PRD/uiux/design-system 上游核对，不得直接当合同。
- 样式合同红灯区（有测试守着）：task6.css 禁 hex/rgb/hsl 字面量与 grid-template-rows；阴影只许 var(--shadow-overlay)/var(--ring)/inset 或 hairline ring；循环动画必须 reduced-motion 关闭并保证终态可读。
- 既有环境缺口（非故障）：eloshapes 数据件、build:tauri 门禁、plotly 物理残留待 clean install。
- 本会话产出物除上述外无其他工作区影响；全程未执行任何 git 写操作。
