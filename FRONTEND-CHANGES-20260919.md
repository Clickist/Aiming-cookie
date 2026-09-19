# ⚠️ 前端改动通报（2026-09-19，会员体系第一波已合入 main）

给本仓库并行工作的会话（知识库 SDK / 排障）：main 上今天合入了会员体系客户端改动（commit `027281b`，44 文件 +3789/−291），开工前请先 `git pull`，并知悉以下撞面信息。

## 今天动过的区域（碰这些前先看）

| 区域 | 改了什么 |
|---|---|
| 侧栏（session rail） | 底部新增账号卡（贴底、无橙强调）、布局调整 |
| 路由层 | 新增用户中心路由 `memberCenterRoute`；`data-page-motion` / `data-page-workspace` 逻辑改过（修窗口抖动） |
| Onboarding 向导 | Provider 目录新增「Aiming Cookie（推荐）」档（锁定 deepseek-v4-flash）、登录/订阅中间态 |
| 设置页 | 新增会员专属模板 |
| coach-runtime | 新增 `member-auth.ts` + 五个 sidecar 路由（deep-link 换票、会员查询） |
| Tauri 配置 | `aimingcookie://` deep-link 协议注册（NSIS + dev 自愈）、single-instance |

## 已知测试基线（别误判）

- `e2e/screenshots.spec.ts` 有 **8 项视觉基线失败是既有问题**（已在改动前的基线代码上复现），不是今天引入的，勿回滚排查。

## 会员体系上下文在哪

- 实现规划：`design/member-pay/IMPLEMENTATION-PLAN.md`（四工作包、接线时序、测试设计）
- 商业规则与线框：`design/member-pay/`（README + wireframes.html 18 屏）
- 登录页定稿：`design/member-login/`（V1 分栏稿 + visual-art.png；线上 /login 尚未按此实现，属待办）
- 完整暂停快照：ops-hub `journal/2026-09-19.md` §⑫

## 后端侧（accounts 仓库 / ECS）同步知悉

- accounts Worker v2、ECS 网关/轮询器 v2 今天全部上线（testmode 全绿）。**ECS 上所有 `.bak-时间戳` 文件都是 v1 旧版，禁止用 .bak 恢复**（会冲掉 v2，退回续费不发货状态）。动 gateway/poller/ac-member.sh/keys.json 前先读 accounts 仓库 `INTERFACE.md`。
