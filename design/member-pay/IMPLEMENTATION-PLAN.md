# member-pay · 落地实现规划

> 2026-09-19 · 架构规划 agent 产出。规则事实源：本目录 `README.md`（商业规则终稿）+ `wireframes.html`（18 屏 v3.3）+ ops-hub `decisions/decisions.md` 09-19 三条。本文只管「怎么落地」，不改任何已拍板规则。

## 0. 开工前必读：两个与任务前提不符的现场事实（2026-09-19 实测核实）

**事实 1：accounts Worker 源码没有丢——0917 已经逆向重建完毕并部署验证过。**

- `C:\Users\袜子\Desktop\accounts` = 可维护源码工程（Hono + better-auth，src/ 9 个文件共 568 行 + `ecos-gateway/`（gateway.py/poller.py/部署脚本）+ `INTERFACE.md` 接口契约 + `migrations-0001.sql`）。git 单提交 `517fd93`，**本地 = origin/main = GitHub Clickist/accounts**，工作区干净。
- 线上核实（本次探测）：`/api/device/start`→200、`/internal/provisioning/pending` 无鉴权→401、`/webhook/stripe` 无签名→400、`/health`→ok；D1 accounts-db（5ad7e929…）实存 `device_codes / entitlements / provisioning / stripe_events` 表。**线上跑的就是重建版**。
- `C:\Users\袜子\Desktop\accounts-rebuild\` = 重建**之前**的旧 bundle 备份（online-bundle.js，Phase 0 骨架版，无 Stripe/换票路由）+ auth_fn.txt 反推片段。它已完成使命，**保留作历史参考，不要在里面开发**。
- 结论：任务书里「WP-A 第一步 = 从 bundle 反推重建源码」**取消**，替换为 §2.1 的「基线核对」（半小时级）。

**事实 2：客户端已存在 `aiming-cookie-relay` 官方中转档的半成品接缝。**

- `webapp/frontend/lib/provider-helpers.ts` 已有 `OFFICIAL_RELAY_PROVIDER_ID = "aiming-cookie-relay"`（"Aiming Cookie 官方"，计费在官方侧结算、详情走专属模板）；`lib/provider-wizard.ts` 的 `wizardTypeOptions()` 目前**把它从「添加服务」列表里排除**；sidecar `coach-runtime/src/provider-models.ts` 在构建注入 `AC_RELAY_BASE_URL` 时注入该档（模型列表是全家桶，非锁单模型）。
- WP-C 不是从零加 Provider，而是**把这条既有档改造成会员档**：置顶推荐 + auth 换成账号订阅（浏览器登录 + deep-link）+ 模型锁 `deepseek-v4-flash` + 旧「key 对用户可见 / billing 端点查余额」路径退役。

其余基础事实（与任务书一致，已复核）：ECS `ac-gateway`（127.0.0.1:8422，nginx 8443 `/member/`）+ `ac-provision-poller`（60s 拉单）+ `ac-member.sh`（`/opt/new-api/ac-member.sh`，子命令 create/renew/reset/list/key/revoke/enable；建用户走 new-api PAT、额度走 MySQL 直写，`users.quota` 与 `tokens.remain_quota` 双记账；令牌锁 deepseek-v4-flash、`expired_time=-1`）。Stripe testmode 账户 gearclickist（acct_1UELytGaOC7LvtCN）。落地页源 = `design/opendesign-landing/index.html`（字节级镜像 `design/landing-archive/aiming-cookie-landing.html`，双文件同步是既有约定），CF Pages `aiming-cookie` GitHub 自动部署。

**额度换算口径**（0919 计价改官价人民币 1:1 后）：500,000 quota = ¥1。Standard 发 **6,250,000**，Plus 发 **18,750,000**，加油包发 **6,250,000**（与 Standard 同为 ¥12.5 当量 ≈120 轮）。

---

## 1. 目标架构总览（一张图）

```text
┌─客户端(Tauri/Next)─┐   系统浏览器    ┌─accounts Worker(CF)─────────────┐
│ Onboarding①选AC档  │──弹浏览器────▶│ /login(既有) → /pay(WP-B)        │
│ ①a等deep-link      │◀─aimingcookie://auth?ticket─┘ │ Checkout(WP-A) │
│ chip②/用户中心②c   │               │ webhook: 发放/取消/退款/失败(WP-A)│
│ 余量轮询 GET /api/me│───JWT Bearer─▶│ D1: entitlements/booster/pays.. │
└───────┬────────────┘               └──────┬──────────────────────────┘
        │ JWT (30d)                         │ 发货队列 provisioning(+params)
        ▼                                   ▼ 60s 拉单/回写/推送用量
┌─ECS ac-gateway:8443/member──┐      ┌─ECS poller + ac-member.sh────────┐
│ 验JWT→选池:先sub后boost     │◀─────│ 双令牌: ac-<uid>(sub)+ac-<uid>-b │
│ 透传 new-api(流式)          │ keys │ (boost)→new-api(MySQL额度原子扣)  │
└─────────────────────────────┘ .json└──────────────────────────────────┘
```

双池方案选型（任务书点名要求论证）：**双令牌（订阅令牌 + 加油包令牌），ac-gateway 按池路由**。理由见 §3.3。

---

## 2. 工作包拆分

规模：S ≤ 0.5 天，M = 1~2 天，L = 3 天+（单人；并行派发时按包内小节再切）。

### WP-A0 · 契约升级（先行 spikes，所有包的解锁点）——规模 S

| 项 | 内容 |
|---|---|
| 目标 | 把 `accounts/INTERFACE.md` 升到 v2，冻结三份契约，WP-A1/A2/B/C 才能四线并行 |
| 产出 | ① `/api/me`（JWT）与 `/api/me/web`（session+CORS）响应 schema；② provisioning 行 `params` JSON 与 action 枚举 v2；③ `keys.json` v2（`{"ac-<uid>":{"sub":"sk-…","boost":"sk-…"}}`）+ 网关选池规则；④ Checkout/升级/取消/恢复/portal 接口路径表（§2.2 汇总的即为草案） |
| 验收 | 四份契约逐字段写进 INTERFACE.md，A1/A2/B/C 负责人（子 agent）只读此文即可开工 |

### WP-A1 · accounts Worker：订阅双档 + 加油包 + 退款（主后端）——规模 L

| 项 | 内容 |
|---|---|
| 目标 | 在重建版源码上把「单档 Payment Link」升级为「双档 Checkout + 加油包 + 升级/取消/恢复 + 退款记账 + 用量回写」，即设计稿全部网页侧规则的落地 |
| 输入依赖 | WP-A0 契约；现有 `Desktop\accounts` 工程（在其上开发，勿动 `accounts-rebuild`） |
| 产出清单 | 见下方明细 |
| 验收 | §4.1 testmode 用例 T1~T11 全绿（可用 Stripe CLI 重放事件驱动） |

A1 产出明细：

**a) 基线核对（替代原「bundle 反推重建」）**
- `cd "C:\Users\袜子\Desktop\accounts" && npx wrangler deploy` 重发一次源码构建 → 重跑三条探测（device/start、internal 401、webhook 400）+ health，确认源码可复现线上行为。此后 accounts-rebuild 目录封存。

**b) D1 迁移 `migrations-0002.sql`**（在既有 5 张业务表上增量）：

```sql
-- entitlements 复用改义：plan 列值从 'monthly' 改为 'standard' | 'plus'
ALTER TABLE entitlements ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
  -- active | canceled(已取消未到期) | expired | refunded
ALTER TABLE entitlements ADD COLUMN period_start TEXT;
ALTER TABLE entitlements ADD COLUMN cancel_at_period_end INTEGER NOT NULL DEFAULT 0;
ALTER TABLE entitlements ADD COLUMN dunning INTEGER NOT NULL DEFAULT 0;      -- ⑧ 扣款失败标记
ALTER TABLE entitlements ADD COLUMN stripe_customer_id TEXT;
-- 加油包（每用户至多一行活跃包：余额归零才能买下一包）
CREATE TABLE booster_packs (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, payment_id TEXT,
  grant_quota INTEGER NOT NULL,            -- 6,250,000
  remaining_quota INTEGER NOT NULL,        -- 用量推送镜像，仅展示/购买资格判定用
  status TEXT NOT NULL DEFAULT 'active',   -- active | drained | refunded
  created_at TEXT NOT NULL
);
-- 付款记录（⑦ 表格数据源；退款以负数行呈现）
CREATE TABLE payments (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  kind TEXT NOT NULL,                      -- subscription | booster | refund
  item TEXT NOT NULL,                      -- standard | plus | booster | refund_partial
  amount_minor INTEGER NOT NULL, currency TEXT NOT NULL DEFAULT 'cny',
  fee_minor INTEGER, net_minor INTEGER,    -- 从 Stripe balance_transaction 读，勿硬编码 ¥2.3
  stripe_object TEXT, stripe_id TEXT,      -- invoice / payment_intent / charge / refund
  checkout_session_id TEXT,                -- ⑥ 状态页反查
  status TEXT NOT NULL DEFAULT 'paid',     -- paid | refunded | partially_refunded
  paid_at TEXT, receipt_url TEXT, created_at TEXT NOT NULL
);
-- 额度发放流水（⑥a「轮询确认发放」的判据 + 审计）
CREATE TABLE quota_grants (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  pool TEXT NOT NULL,                      -- sub | boost
  grant_quota INTEGER NOT NULL, reason TEXT,   -- first | cycle | upgrade | booster
  stripe_event_id TEXT UNIQUE,             -- 幂等键：同事件只发一次
  provision_id TEXT, created_at TEXT NOT NULL
);
-- 用量镜像（客户端百分比与购买资格判定的唯一展示源）
CREATE TABLE usage_snapshots (
  user_id TEXT PRIMARY KEY,
  sub_remaining INTEGER, sub_grant INTEGER,
  boost_remaining INTEGER, boost_grant INTEGER,
  updated_at TEXT NOT NULL
);
-- 用量日汇总（⑪a 用量页；poller 按 new-api logs 聚合推送）
CREATE TABLE usage_daily (
  user_id TEXT NOT NULL, day TEXT NOT NULL, model TEXT NOT NULL,
  calls INTEGER NOT NULL DEFAULT 0, quota_used INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, day, model)
);
-- 发货单带参数
ALTER TABLE provisioning ADD COLUMN params TEXT;   -- JSON {"pool":"sub","quota":6250000}
```

**c) Stripe 侧配置**（testmode 先行，livemode 复刻）
- 新建 Price：`price_standard`（¥10/月 CNY，month）、`price_plus`（¥30/月）、`price_booster`（¥10 one-time）；三个 Price ID 进 `wrangler.toml [vars]`。
- 新 secret：`STRIPE_SECRET_KEY`（现有工程只有验签没有 API 调用，这是新增依赖面）。
- webhook 事件扩到 6 个：`checkout.session.completed` / `invoice.paid` / `customer.subscription.deleted`（既有）+ `customer.subscription.updated` / `invoice.payment_failed` / `charge.refunded`。

**d) 接口**（全部挂 accounts Worker，session 走 better-auth cookie）：

| 路径 | 方法/鉴权 | 作用 |
|---|---|---|
| `/api/billing/checkout` | POST session `{item: standard\|plus\|booster}` | 建 Checkout：订阅 mode=subscription（`client_reference_id=user_id`、`metadata.user_id`、success_url=`/pay?status=success&session_id={CHECKOUT_SESSION_ID}`、cancel_url=`/pay?status=canceled`）；加油包 mode=payment，**下单前服务端校验**：有效订阅 + `booster_packs.remaining_quota<=0`（或无包），否则 409 |
| `/api/billing/status` | GET session `?session_id=` | ⑥ 四态判定：查 payments（是否已扣款）+ quota_grants（是否已发放）→ `{state: success\|processing\|canceled}`，processing 附 `paid_at`（>10 分钟前端展示「联系我们」） |
| `/api/billing/upgrade-preview` | GET session | 调 Stripe upcoming invoice 算「抵扣后本单 ¥X · 下期起 ¥30/月」（⑤/⑦ 确认弹层数字） |
| `/api/billing/upgrade` | POST session | `subscriptions.update`：换 `price_plus` + `proration_behavior=create_prorations` + `billing_cycle_anchor=now`（新周期从升级日起算）+ `payment_behavior=error_if_incomplete`（差价当场结清） |
| `/api/billing/cancel` | POST session | `cancel_at_period_end=true`（周期末止） |
| `/api/billing/resume` | POST session | `cancel_at_period_end=false`（同一订阅恢复，⑦ 按钮双态） |
| `/api/billing/portal` | POST session | 建 Customer Portal session（⑧「更新支付方式」用；portal 配置里**关掉让用户自助换档**，只留支付方式与订阅取消——取消也会走 `customer.subscription.updated` 同步回 D1） |
| `/api/me` | GET **JWT**（Bearer，Worker 用 JWT_SECRET 自验） | 客户端 chip/用户中心数据源：`{user, plan, status, cancel_at_period_end, period_start/end, dunning, pools:{sub:{remaining,grant,pct}|null, boost:{…}|null}, boost_buyable}` |
| `/api/me/web` | GET session + `Access-Control-Allow-Origin: https://aimingcookie.com` | 落地页⑩已登录态检查（cookie 需 `SameSite=None; Secure`，见风险 R6） |
| `/internal/usage` | POST PROVISION_TOKEN | poller 每 60s 推送：`{snapshots:[{user_id,sub_remaining,sub_grant,boost_remaining,boost_grant}], daily:[{user_id,day,model,calls,quota_used}]}`（只推 `accessed_time` 变化过的用户） |
| `/internal/admin/requeue` | POST PROVISION_TOKEN | ⑥b 人工补发：`{provision_id}` → 置回 pending、attempts=0 |
| `/account` `/account/usage` `/account/billing` | GET session（HTML） | 数据 API 供 WP-B 页面消费 |

**e) webhook 处理器 v2**（`src/stripe.ts` 重写，幂等三保险：stripe_events 主键 + quota_grants.stripe_event_id 唯一键 + 发货动作幂等）：

| 事件 | 处理 |
|---|---|
| `checkout.session.completed`（subscription） | 用 `metadata.user_id` 定位用户（不再猜 email；Payment Link 老路径 email 兜底保留一个版本）；payments 落行；按 price_id 定档写 entitlements（plan=standard/plus、period、status=active）；`quota_grants(reason=first)` → provisioning `{action: create_user 或 grant_sub, params:{quota}}` |
| `checkout.session.completed`（payment/booster） | payments 落行；booster_packs 插入（grant=625 万）；quota_grants(reason=booster) → provisioning `{action: grant_boost}` |
| `invoice.paid`（billing_reason=subscription_cycle） | **语义改为重置而非叠加**（现状代码是 +31 天堆叠，必须改）：entitlements 周期滚动、status 回 active、cancel_at_period_end=0、dunning=0；quota_grants(reason=cycle) → grant_sub 重置 |
| `invoice.paid`（billing_reason=subscription_update，即升级） | entitlements.plan=plus、period 从升级日重算；quota_grants(reason=upgrade) → grant_sub 重置满额（**显示 100% 向上跳**；旧池余量按 proration 折成钱抵扣，quota 不结转——已拍板） |
| `customer.subscription.updated` | 同步 `cancel_at_period_end` / `status`（覆盖我们从 portal 取消、以及自家 API 调用后的回声） |
| `customer.subscription.deleted` | 周期末到期或退款即删：entitlements.status=expired（或 refunded）→ provisioning `{action: revoke_sub}`（**加油包不动**，断订可烧完） |
| `invoice.payment_failed` | dunning=1（⑧ 客户端黄条；Smart Retries 期间不中断服务、不入队任何 revoke） |
| `charge.refunded` | payments 标 refunded / 插负数行；若订阅同删 → provisioning `{action: revoke_all}`（两池清零，退完即免费用户）。退款金额本身由人在 Stripe Dashboard 执行（MVP 拍板），webhook 只做记账与清池 |

### WP-A2 · ECS 侧：双令牌 + 用量推送——规模 M

| 项 | 内容 |
|---|---|
| 目标 | new-api 侧实现「先订阅池后加油包池」的物理隔离与扣减顺序；把余量/用量回传 accounts |
| 输入依赖 | WP-A0 契约（keys.json v2、action v2、/internal/usage schema）；`ac-member.sh` 现状（已读：create/renew/reset/revoke/enable，单令牌、锁模型、MySQL 直写双记账） |
| 产出 | ① `ac-member.sh` **增量**子命令（不得改动既有子命令行为——`/opt/ac-member-api/`(8421) 老服务还在用）：`create2 <name> <sub_pts>`（建用户+sub 令牌）、`grant-sub <name> <pts>`（sub 令牌 remain 重置 + `users.quota` 重算）、`grant-boost <name> <pts>`（无 boost 令牌则建 `ac-<name>-b`，有则 +=；断订后 re-sub 场景是「无令牌重建」）、`revoke-sub` / `revoke-boost` / `revoke-all`、`key2 <name>`（双 key 输出）；`users.quota` 一律重算为 `SUM(活跃令牌.remain_quota)`（单条 UPDATE 子查询，避免双表漂移）。② `poller.py` v2：action 分发到新子命令 + `params` 透传；keys.json v2 原子写（保留 v1 读兼容一个版本）；每轮顺带查 MySQL（tokens.accessed_time 水位 + logs 按日聚合）推 `/internal/usage`。③ `gateway.py` v2：按 §3.3 选池 |
| 验收 | 双令牌并发压测（两个并发会话同时打）不串池、不超扣；停 poller 30 分钟再启动，发货与用量推送自愈；日志红线不变（零 sk-/JWT） |

### WP-B · accounts 网页前端——规模 L（可与 A1 契约冻结后并行）

| 项 | 内容 |
|---|---|
| 目标 | ⑤ /pay、⑥ 四态、⑦/⑪ 账号中心三子页（Open Design 左栏壳） |
| 输入依赖 | WP-A0 契约 + WP-A1 的 `/api/billing/*`、`/api/me/web`、`/account/*` 数据接口（可先对 wrangler dev + 种子数据开发） |
| 技术形态 | 沿用 `login-page.ts` 的**服务端 HTML 模板字符串**模式（工程零新增构建链）；样式复用 member-login 设计稿已固化的 AC 语义 token（明暗双主题） |
| 产出 | `/pay`（未订阅双档卡 + 加油包加购区含未订阅置灰 + **已订阅视角**：状态条 + 升级 CTA + 确认弹层带 upgrade-preview 数字）；`/pay?status=` 四态页（success 轮询 `/api/billing/status`，processing 超时展示凭证与「联系我们」mailto，canceled 文案通用化，success 按 `item=booster` 切加油包文案）；`/account`（概览：订阅横幅 + 黑色余量大条两池全貌 + 加油包卡置灰态 + 自动续费卡）、`/account/usage`（按天汇总表）、`/account/billing`（⑦ 订阅卡生效/已取消双态 + 取消↔恢复双按钮 + 升级 + 退款卡 mailto 自动附摘要 + 付款记录表含负数退款行）；登录跳转链（未登录 → /login → 回 /pay）；⑥→deep-link「回到 Aiming Cookie」按钮 + 兜底文案 |
| 验收 | 对照 §4.3 走查清单 ⑤⑥⑦⑪⑪a 屏逐项过；未订阅买加油包 409 置灰；取消态与恢复态切换即时反映 |

附注（相邻工作项，不在本包但同一仓库）：0918 拍板的「邮箱自适应生长登录页 + 密码体系」（member-login 设计稿）建议作为 WP-B 的前置小包随行实施——/pay 依赖登录页的观感一致性，且后端待办（邮箱查询接口、emailAndPassword 开关）已在 journal 挂账。若要砍范围，现有 OTP 登录页可用，登录页升级可延后不阻塞。

### WP-C · 客户端（Tauri + Next 前端 + sidecar）——规模 L（最长杆，最早启动）

| 项 | 内容 |
|---|---|
| 目标 | ①/①a/①b Onboarding、②/②b/②c chip 与用户中心、④/④b/⑧/⑨ 提示态、余量轮询、deep-link 全链 |
| 输入依赖 | WP-A0 契约（/api/me、device 三步、deep-link 参数）；WP-A1 testmode 部署（联调期） |
| 改造点（按文件） | 见下表 |

| 接缝 | 文件 | 改造 |
|---|---|---|
| 协议注册/唤起 | `src-tauri`（tauri.conf.json + deep-link 插件 + single-instance 插件：二次启动把 URL 转发给已运行实例） | 注册 `aimingcookie` 协议；`aimingcookie://auth?ticket=` 事件 → 转交 sidecar |
| 会员档注入 | `webapp/coach-runtime/src/provider-models.ts` | relay 档 auth 模式从「内存档 key」改为「JWT 凭据」；模型锁 `deepseek-v4-flash`；base_url 固定 `https://token.gearclickist.com:8443/member`（`AC_RELAY_BASE_URL` 注入机制保留给 BYOK 无关的旧路径退场期） |
| 换票三步 | coach-runtime 新增 `member-auth.ts` | `POST /api/device/start` → 打开系统浏览器 → 收 deep-link ticket → `POST /api/device/exchange` → JWT 存入 provider 凭据仓（`config/provider.json`，沿用明文本地合同）→ 连通测试（拿 JWT 打网关 `/v1/models` 或免模型探测） |
| 向导与 Onboarding | `lib/provider-wizard.ts`（`wizardTypeOptions` 不再排除 relay 档、置顶+推荐徽标+auth=account 类型分支）、`components/task3/OnboardingFlow.tsx`（① 列表项 + ①a 等待页 + ①b 三中间态：已登录未订阅/老会员直连/连通失败） | 选 AC 档后第 2 步整页换成「登录并订阅」流，不走 API key 表单；连通成功才亮「继续」（铁律⑤） |
| 左下角 chip | `components/task3/AppShell.tsx`（侧栏底部账号位） | ②/②b 三态（会员百分比 / BYOK 未登录 / BYOK 已登录）+ ⑨ 两态（到期用加油包 / 订阅已结束 0%）+ ⑧ 黄条一次性可关闭；百分比 = `/api/me` 的 `pools` 当前池单条，绿橙红分档阈值实现时对照设计 token 定（<10% 红、10~30% 橙，遗留 TBD 待点点过目） |
| 用户中心 | 新组件（挂 AppShell 右主区，导航模式抄训练历史页「← 返回」） | ②c 态A/态B：两池全貌、加油包购买（弹浏览器 /pay#booster）与「余额未尽」置灰、管理订阅/退款（弹浏览器 /account/billing）、退出登录（④b fallback：有 BYOK 自动切、无则 Coach 置灰指设置，**永不回 Onboarding**） |
| 教练页提示 | `components/task6/CoachPanel.tsx`（发送拦截层） | ④ 双池皆空 inline 提示条（发送禁用、历史可读）；网关 403 `quota_exhausted` / `member_required` / 401 `jwt_expired` 错误码分流到 ④/⑨/重登录引导 |
| 余量轮询 | `lib/api.ts` 层新增 `fetchMe()` | 启动时 + 窗口聚焦 + 每 10 分钟 + 每次教练回合结束后 + deep-link 回归事件时拉 `/api/me`；401 静默降级为未登录态 |
| 设置页 | `components/task6/ProviderSettingsSection.tsx`、`SettingsWorkspace.tsx` | AC 档详情走会员专属模板（套餐/余量/管理按钮），不显示 Base URL/API key 行 |

| 验收 | §4.2 全部 UI 状态可模拟触发且表现符合 18 屏线框；BYOK 全路径回归不受影响（既有 provider-wizard 测试组必须保持绿）；deep-link 在未装客户端的机器上点击有兜底（网页文案已覆盖） |

### WP-D · 落地页——规模 S

| 项 | 内容 |
|---|---|
| 目标 | ⑩ 右上角账号入口两态 + ⑩b 底部三档价格块 |
| 输入依赖 | WP-A1 `/api/me/web`（CORS）；无其他依赖 |
| 产出 | `design/opendesign-landing/index.html` + 镜像文件**双文件同步**改动：导航最右加「登录」描边按钮；已登录态（跨域 session 探测成功时）原位下拉（头像+邮箱+余量%+前往账号中心/管理订阅/退出）；页脚前加三档价格块（BYOK ¥0 写清 API 费付给厂商 / Standard ¥10 / Plus ¥30，CTA → accounts.gearclickist.com/pay）；「未使用可退·渠道手续费不返」话术按 0919 翻案定稿 |
| 验收 | CF Pages 预览部署过点点目检；未登录/已登录两态截图对照线框 ⑩/⑩b；探测失败时回退未登录态不报错 |

---

## 3. 接线规划（时序级）

### 3.1 deep-link 全链（① → 支付 → 回客户端）

```text
1. 客户端 wizard 选「Aiming Cookie」
2. sidecar POST accounts /api/device/start → {device_code, login_url=/login?dc=…}（10 分钟有效）
3. Tauri shell 打开系统浏览器 login_url；客户端进 ①a 等待态（只等 deep-link 事件，不轮询）
4. 浏览器：邮箱验证码登录成功 → login 页自动 POST /api/device/claim（带 session cookie）→ {ticket}（5 分钟一次性）
5. 登录页立即 deep-link：aimingcookie://auth?ticket=…&dc=…（不等付款——①b 态1 依赖这一步）
6. 客户端 Tauri 收 URL → 转交 sidecar → POST /api/device/exchange → {jwt, user, member}
   ├─ member=false → ①b 态1「还差订阅」→ 浏览器打开 /pay?dc=…（dc 换新：复用第 2 步再起一轮，旧 dc 已被 exchange 消费）
   └─ member=true  → ①b 态2 直连路径（换机/重装快路径）
7. JWT 写入 provider 凭据仓 → 连通测试（Bearer JWT GET :8443/member/v1/models）
   ├─ 过 → 「连接成功」亮「继续」→ 放行主界面
   └─ 不过 → ①b 态3 红字 + 重试（不放行；订阅无损）
8. （态1 用户）网页 /pay 选档 → Stripe Checkout（subscription）→ 付成回跳 /pay?status=success&session_id=…
9. 结果页轮询 /api/billing/status：payments 有行 + quota_grants 有行 → 成功态；显示「回到 Aiming Cookie」
   → 按钮触发 deep-link（此时客户端已在等待/或已进主界面，均能收：single-instance 转发）
   → 客户端拉 /api/me 刷新余量（⑥a2 加油包同机制，仅文案不同）
10. 兜底：浏览器拦自定义协议 → ⑥ 页尾文案「打开 Aiming Cookie 客户端，登录同一账号即自动连接」
    → 用户手动打开客户端 → chip 登录入口 → 重走 2~7（老会员态2 直连，10 秒闭环）
```

关键点：**deep-link 在登录成功即发一次（携 JWT 前置），支付成功再发一次（刷新态）**；`device/exchange` 一次性（exchange 即删行），第二轮购买回来时用新 dc 起轮。ticket 5 分钟 + dc 10 分钟超时均有 410 语义，客户端引导重开浏览器页。

### 3.2 发放与显示全链（webhook → new-api → 百分比）

```text
Stripe invoice.paid / checkout.session.completed
  → Worker 验签（±300s 容差）→ stripe_events 主键幂等
  → entitlements/booster_packs/payments 落库
  → quota_grants 插行（stripe_event_id 唯一，二次防御）
  → provisioning 入队 {action, params}
ECS poller（60s）拉 /internal/provisioning/pending（Bearer PROVISION_TOKEN，UA 已修 403 坑）
  → ac-member.sh 新子命令 → MySQL：tokens.remain_quota（原子 UPDATE）+ users.quota 重算=Σ活跃令牌
  → keys.json v2 原子写 → POST /internal/complete
  → 同轮推送 /internal/usage：snapshots（双池 remain/grant，只推 accessed_time 有变化的用户）+ daily 聚合
Worker 把 snapshots 写 usage_snapshots → /api/me 计算 pct=remaining/grant
客户端轮询 /api/me → chip 单条百分比（当前池）；用户中心两池全貌
失败路径（⑥b）：扣款已成功（payments 有行）但 quota_grants 无行 = 处理中态
  自愈：provisioning pending 行每轮重拉、attempts<5；poller 停机恢复即补
  终态失败（attempts≥5 → failed）：/pay 页 >10 分钟出示凭证文案 → 人工：wrangler d1 或
  /internal/admin/requeue 置回 pending → poller 下一轮补发（ac-member.sh 幂等：create 已存在视为成功）
```

### 3.3 双池扣减：双令牌方案（选型结论）

**选择：双 new-api 令牌（`ac-<uid>` 订阅池 + `ac-<uid>-b` 加油包池），ac-gateway 按池路由。**

| 维度 | 双令牌（选定） | 单令牌合并额度（否决） |
|---|---|---|
| 扣减顺序 | 天然强制：网关永远先用 sub key | new-api 只有一个数，顺序只存在于我们的台账算术里，**无强制力** |
| 周期重置/升级满上 | `grant-sub` 直接重置该令牌 remain，无关加油包 | 必须先精确读出「sub 还剩多少」再合并回写，与并发请求竞态，用户可能掉额度 |
| 断订/退款清池 | `revoke-sub` 禁用令牌，boost 不受影响（“断订可烧完”零成本成立） | 退款清零需对合并数做减法手术，同样竞态 |
| 百分比显示 | 每池 remain/grant 现成，两池口径永不错 | “当前池百分比”要靠台账反推（消耗先记 sub 溢出记 boost），对账漂移即显示错 |
| 代价 | keys.json/gateway/poller 改 v2；换池瞬间一次内部失败重试 | 表面零改动，实则是把复杂度全押在账本对账上 |

网关选池规则（gateway.py v2）：
1. 默认走 sub key；收到 new-api「令牌额度耗尽」类 4xx 时**同请求内换 boost key 重试一次**（客户端无感），并对该 uid 记 5 分钟 sub 负缓存（负缓存过期自动回探——续费重置后最多 5 分钟回到订阅池）。
2. boost key 不存在或同样耗尽 → 原样透传错误，错误体带 `type: "quota_exhausted"`（客户端据此触发 ④）。
3. 令牌级并发安全由 new-api 的 MySQL 原子扣减保证（pre-consume/post-consume 行锁），网关无需加锁。
4. 可选优化（不进 MVP）：转发前 SELECT 一次 `tokens.remain_quota` 预判池子，省掉换池那一次失败重试。

### 3.4 各生命周期的端到端语义对照

| 场景 | Stripe 动作 | webhook | 额度动作 | 客户端呈现 |
|---|---|---|---|---|
| 首订 | Checkout(subscription) | csc.completed | create_user + grant_sub | ⑥a → 100% |
| 续费 | 自动扣款 | invoice.paid(cycle) | grant_sub 重置 | 悄然回 100% |
| 升级 | subscriptions.update+proration+anchor=now | invoice.paid(update) | grant_sub 重置 Plus 满额 | 100% 向上跳，新周期从升级日 |
| 取消 | cancel_at_period_end | subscription.updated | 无动作 | ②c 态B「可用至 X」+ 恢复按钮 |
| 恢复 | 同订阅 resume | subscription.updated | 无动作 | 回态A |
| 到期不续 | 周期末自然删除 | subscription.deleted | revoke_sub | ⑨：有加油包=续命态；双空=断开 |
| 扣款失败 | Smart Retries(~7天) | invoice.payment_failed | 无动作（不中断） | ⑧ 黄条一次性 |
| 买加油包 | Checkout(payment) | csc.completed(payment) | grant_boost | ⑥a2 → 加油包 100% |
| 退款 | Dashboard 手工部分退款 | charge.refunded(+deleted) | revoke_all（订阅同删时） | 退完即免费用户 |

---

## 4. 测试设计

### 4.1 testmode 端到端用例（WP-A 验收主表）

| # | 用例 | 步骤要点 | 通过判据 |
|---|---|---|---|
| T1 | 首订 Standard | testmode 4242 卡走 /pay | payments+quota_grants+entitlements(plan=standard) 落库；60s 内 new-api sub 令牌 remain=625 万；/api/me pct=100% |
| T2 | 首订 Plus | 同上选 Plus | 1875 万；plan=plus |
| T3 | 续费（周期滚动） | **Stripe Test Clock** 推进 1 个月 | invoice.paid(cycle) 后 sub 令牌**重置**非叠加；剩余部分旧额度不结转 |
| T4 | 升级折差价 | Standard 用掉 40% 后点升级 | upcoming invoice 差价 ≈ 剩余比例折算；anchor 重置；quota 重置 1875 万且显示 100%；boost 不动 |
| T5 | 取消→恢复 | cancel → /api/me 显示态B → resume | 双态往返；period_end 不变；无重复发货 |
| T6 | 取消后到期 | Test Clock 推到周期末 | subscription.deleted → sub 令牌 status=2；boost 令牌照常可用（⑨ 续命态） |
| T7 | 加油包资格 | 无订阅买包 / 有包未烧完买包 | 均 409 挡在下单前（Checkout 不创建）；烧完后可买 |
| T8 | 双池扣减顺序 | 把 sub 令牌 quota 调小（SQL 直改 2 万）→ 连续对话 | 消耗先打 sub 至 0，自动落 boost；百分比当前池切换；④ 仅双池皆空触发 |
| T9 | 并发扣减 | 两并发会话同时打网关 | 不超扣（users.quota ≥ Σ令牌 remain 恒成立）；不串池 |
| T10 | 退款 | Dashboard 对 T1 订单部分退款 | charge.refunded → payments 负数行 + 费用/净额正确（读 balance_transaction）；订阅删 → revoke_all；退完 /api/me=免费用户 |
| T11 | ⑥b 延迟到账 | 付款前 `systemctl stop ac-provision-poller` → 付款 → 观察处理中态 → 重新启动 | /api/billing/status=processing（payments 有/grants 无）；poller 恢复后自愈转 success；人为把 provisioning 置 failed 后 admin/requeue 可补发 |
| T12 | webhook 幂等/重放 | Stripe CLI 重放同一 invoice.paid 两次；乱序发 deleted→paid | stripe_events+quota_grants 双键去重，额度只发一次 |
| T13 | 6b→客户端 | T11 场景下客户端 chip | 处理中不显示错误；到账后下一轮轮询跳 100% |
| T14 | deep-link 全链 | 装好的客户端走 ①→支付→回跳 | token 注入、连通测试通过、放行；浏览器拦协议时按兜底文案手动路径可走通 |

### 4.2 客户端 UI 状态模拟触发（不依赖真实付款）

| 状态 | 触发法 |
|---|---|
| ① / ①a | 清空 app-data（首次启动路径）；「重新打开浏览器页面」按钮直接可测 |
| ①b 态1/态2/态3 | 态1：登录页登录后直接关 /pay；态2：已订阅账号走换票；态3：本地 hosts 把 :8443 指向拒绝端口或停 nginx 再测重试 |
| ②/②b 三态 | /api/me 三份 fixture（dev 注入或 wrangler dev 种子数据）：会员/BYOK 未登录/BYOK 已登录 |
| ②c 态A/态B、置灰 | fixture：cancel_at_period_end 0/1；boost_remaining>0 → 购买置灰 |
| ④ 双池皆空 | fixture：两池 remaining=0；或 SQL 直改两令牌额度为极小值真实打空 |
| ④b 退出登录 | 有/无 BYOK 配置各跑一遍；确认不回 Onboarding |
| ⑧ 黄条 | fixture dunning=1；✕ 关闭后本轮不再出现 |
| ⑨ 两态 | fixture：status=expired+boost>0 / boost=0；SQL 直改令牌真实触发亦可 |
| 余量分档变色 | fixture remaining/grant 扫 100/30/10/2% |

### 4.3 验收走查（对照 18 屏）

①Provider 下拉置顶 → ①a 等待/成功 → ①b 三中间态 → ② chip 单条百分比 → ②b 三态真实尺寸 → ②c 两态+置灰 → ④ 用尽零商业化 → ④b 两去向 → ⑤ 双档+加油包+已订阅视角 → ⑥ 四态 → ⑦ 生效/已取消双态+付款表 → ⑧ Stripe 自带+轻提示 → ⑨ 续命/断开 → ⑩ 两态 → ⑩b 三档价格块 → ⑪ 概览 → ⑪a 用量表 →（⑪b 已并入⑦）。逐屏截图留档（延续 0919 Browser Use 截图自查的做法）。

---

## 5. 并行执行建议

```text
第 0 步（串行，半天）  WP-A0 契约升级 ←—— 唯一总闸门
第 1 波（三线并行）    WP-A1(Worker)   WP-A2(ECS，只依赖契约)   WP-C(客户端，只依赖契约+本地 fixture)
第 2 波（A1 部署 testmode 后）WP-B(网页前端，对真接口联调)   WP-D(落地页，只依赖 /api/me/web)
联调顺序               A1+A2 先合（发货链 T1/T8 通过）→ C 接 deep-link 真链（T14）
                       → B 上真支付页（T1/T4/T7）→ 全量 T1~T14 → 18 屏走查
```

- WP-A1 与 WP-A2 天然可并行（D1 契约 vs keys.json/action 契约都在 A0 冻结）；A2 是 SSH/ECS 操作流，适合单独派发。
- WP-C 最长（OnboardingFlow/AppShell/CoachPanel 三处深改 + Tauri 插件），**建议第 0 步当天就启动**，先用 /api/me fixture 开发全部 UI 态。
- WP-B、WP-D 可晚一波，避免对着未定接口空转。
- 集成阻塞点只有三处：①A1 部署 testmode（B/C 联调的前置）；②A2 的 keys.json v2 上线（gateway 切换瞬间新旧格式兼容，poller 先发 v2、gateway 双读，灰度一轮）；③Stripe webhook 事件扩容（先加事件再发新代码，避免漏事件）。

---

## 6. 风险清单

| # | 风险 | 缓解 |
|---|---|---|
| R1 | bundle 反推重建偏差 | **已消解**（0917 重建已部署+端到端验证；本次三路由探测复核）。残余动作：A1 开工先 `wrangler deploy` 源码复现一遍线上行为再动手；`accounts-rebuild/` 封存勿动 |
| R2 | webhook 幂等与重放 | 三层：stripe_events 主键、quota_grants.stripe_event_id 唯一、ac-member.sh 动作幂等（create 已存在视为成功）；验签 ±300s；重放测试 T12 进验收。**注意现状代码续费是 +31d 叠加语义，v2 改重置语义时把存量测试账号 entitlements 手工复位** |
| R3 | deep-link 浏览器兼容 | Chrome/Edge 可能弹协议确认或 iframe 内静默失败：⑥ 页兜底文案（线框已定）+ 「点此重新打开」+ 手动路径（开客户端登录同账号即连）。Windows 未装客户端时协议无响应同走兜底。Tauri 侧 single-instance 插件保证二实例 URL 转发 |
| R4 | 额度并发扣减 | 执行点收敛在 new-api MySQL 原子 UPDATE；users.quota 重算用单条子查询 UPDATE；网关换池重试不放大扣减（失败请求未计费）；T9 并发用例把关 |
| R5 | testmode→livemode 切换 | 清单：①livemode 新 STRIPE_SECRET_KEY+webhook secret（test/live 签名不通用）②Product/Price/Payment Link 全部重建（ID 不同，[vars] 切换）③老 Payment Link 下线 ④DEV_OTP_ECHO=false + RESEND_API_KEY 上真发码 ⑤**new-api 是 test/live 共用实例**：测试会员统一 `ac-t-` 前缀用户，切换时批量 revoke 测试号，或届时评估独立测试桶 ⑥ECS env 双侧 secret 同步（JWT_SECRET/PROVISION_TOKEN 不换则不动）⑦D1 数据清或标记 test=1 ⑧Stripe HK 收款币种/结算账户复核 + 顾客邮件语言 ⑨费率实测校准 payments.fee_minor 读取逻辑 |
| R6 | 跨域会话（落地页⑩已登录态） | accounts cookie 需 `SameSite=None; Secure` + CORS 白名单 aimingcookie.com；探测失败静默回退「未登录」态；better-auth cookie 属性覆盖点在 createAuth 配置 |
| R7 | ac-member.sh 双面依赖 | `/opt/ac-member-api/`(8421) 老服务还在用旧子命令：**只做增量子命令**，不碰 create/renew/reset/revoke 既有行为；改动前 `cp ac-member.sh ac-member.sh.bak-日期`（沿既有备份惯例） |
| R8 | JWT 30 天无刷新 | 到期后 chip 降级未登录态 + Coach 报 401 引导重登（走 ①b 态2 快路径 10 秒闭环）；不做 refresh token（MVP 拍板的简化，风险可接受） |
| R9 | 公平使用/防薅 | 0919 定价决策提到「配公平使用限额防薅」（一个号≈39 满额用户）。**不在 member-pay 18 屏范围**，本规划不含；上线后按 new-api 用量报表观察再定（已在 decisions 挂账，勿在此扩权） |

---

## 7. 明确的非目标（防执行期跑偏）

- 不做任何提醒邮件（到期/额度/续费全无；Resend 仅登录验证码）。
- 不做客户端内登录/套餐/支付表单；主消费页零商业化。
- 不做降级；不做额度 rollover（订阅池周期末清、加油包永不过期）。
- 不做自动退款；退款=邮件申请+人工 Dashboard 执行。
- 不动 BYOK 路径一行逻辑（回归必须全绿）。
- 不动中转站渠道优先级（商汤→GOAT→OPC→官方）与 3000 收口（0914 拍板推迟）。
