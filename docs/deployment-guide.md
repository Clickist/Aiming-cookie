# Aiming Cookie 上线部署指南（方案 A 执行版）

> **状态**：时间敏感的部署实施参考，不是产品或架构事实源。供应商、价格、政策和平台能力必须在实际部署前重新核实。
>
> 2026-07-10 · 本文是 `docs/PRD.md` §9.1「方案 A：一台香港小 VPS」的**落地 checklist**，不是新设计。方向锚以 PRD 为准；本文与 PRD 或 `docs/ARCHITECTURE.md` 冲突时以上游文档为准。
>
> 目标：把"香港 VPS + Cloudflare + GitHub Releases + 境外域名"从决策落到能直接下单、照着配的程度。需求约束：**中国大陆顺畅访问 + 免 ICP 备案**。

## 0. 信息来源与可信度

本指南的价格/政策数据通过 `gemini-grounding-search`（Antigravity OAuth 路径，gemini-2.5-flash）于 2026-07-10 获取。每条标注可信度：

- ✅ = 有搜索 sources 背书（grounding 触发）
- ⚠️ = 未触发 grounding 或基于通用知识，**下单/配置前请实时核实**

> 工具说明：gemini grounding 从大陆 IP 会出现间歇性 `User location is not supported`（400），与 VPN 波动相关——查询时若全批失败，等几分钟或切节点重试。综述性/政策性 query 不易触发搜索，需拆成"具体套餐/产品名/数字"才会返回 sources。

---

## 1. 备案规避红线（最重要，先读）

**核心结论** ✅（sources: huaweicloud.com / aliyun.com / alibabacloud.com 等厂商官方）：

> ICP 备案**仅针对物理服务器位于中国大陆境内**的服务。**香港服务器无需 ICP 备案**。但若 CDN 在**大陆境内有节点**，则必须备案。

由此推出 Aiming Cookie 的免备案红线 checklist：

- [x] 后端服务器在香港（境外 VPS）→ 无需 ICP 备案
- [ ] **域名绝不解析到大陆 IP**
- [ ] **不用带大陆节点的 CDN**（阿里云 CDN / 腾讯云 CDN / 百度云加速等一律不行）
- [ ] **不用大陆对象存储做公网回源**（阿里云 OSS / 腾讯云 COS 公网访问会触发）
- [ ] DNS 用 Cloudflare（境外）即可
- [ ] landing 用 Cloudflare Pages——Cloudflare 边缘节点属境外 CDN，**不触发**大陆备案要求 ✅

**合规补充** ✅：免备案 ≠ 无监管。内容须遵守香港当地法 + 大陆法；若收集大陆用户个人信息，需符合《个人信息保护法》(PIPL) 与数据跨境传输规定。公安联网备案一般无需（针对已 ICP 备案站点）。

---

## 2. 域名（第一步下单）

注册商对比 ✅（sources: cloudflare.com / porkbun.com / namesilo.com）：

| 注册商 | .com 首年/续费 | WHOIS 隐私 | 备注 |
|---|---|---|---|
| **Cloudflare Registrar** | **$10.44/年** | 免费 | 注册局成本价，**无溢价**，续费透明；推荐 |
| **Porkbun** | $10.99/年 | 免费 | 界面友好，价格低 |
| NameSilo | $17.29/年（预存 $50 后 $11.05） | 免费 | 零售偏贵 |

**推荐**：**Cloudflare Registrar**（成本最低 + 反正 DNS 也要用 Cloudflare，一站式）或 **Porkbun**（若想注册商与 DNS 解耦）。⚠️ Cloudflare Registrar 偶有转入/特定 TLD 限制，注册前确认 `.com` 可用。

操作：注册 `.com` → 把域名 NS 指向 Cloudflare → 在 Cloudflare 管理 DNS。

---

## 3. 香港 VPS 选型

五家对比 ✅（价格均为活动参考区间，**下单前以官网实时报价为准**）：

| 服务商 | 代表套餐 | 配置 | 带宽 / 线路 | 流量 | 价格 | 大陆延迟 |
|---|---|---|---|---|---|---|
| **腾讯云轻量** 香港 | 2核2G | 2C2G | 3-30Mbps，CN2 优化 | 300GB-4TB | ~30-70 元/月 | 15-45ms |
| **腾讯云轻量** 香港 | 2核4G | 2C4G | ~30Mbps | 1.5-2TB | ~80-150 元/月 | 15-45ms |
| **阿里云轻量** 香港 | 2核2G | 2C2G | **200Mbps** BGP 优化 | 不限流量 | 活动 38-68 元/年；常规 ~25 元/月 | — |
| **阿里云轻量** 香港 | 2核4G | 2C4G | 200Mbps | 不限流量 | 活动 188-199 元/年 | — |
| 搬瓦工 BandwagonHost | HK CN2 GIA | 2C2G/40GB SSD | **1Gbps 三网 CN2 GIA 直连** | 500GB | **$89.99/月 / $899.99/年** | 30-50ms |
| DMIT | Premium MINI | 2C2G/60GB | 1Gbps CN2 GIA | 1.5TB | $119.9/月 | ~15ms |
| DMIT | Eyeball EB.STARTER | 1C2G/40GB | 2Gbps CMI | 2TB | $59.9/月 | — |
| GigsGigs | HK-K1 | 1C512M/20GB | 50Mbps PCCW 直连 | 300GB | $8.8/月 | — |
| GigsGigs | HK-V1 | 1C512M/10GB | 10Mbps CN2 GIA | 60GB | $22/月 | — |

**决策维度**：线路质量（CN2 GIA > CN2 GT > 普通 BGP/国际）→ 大陆延迟 → 带宽 → 流量 → 价格。

**推荐**（待点点拍板）：
- **v1 流量小、求省心**：**腾讯云或阿里云轻量香港**（¥25-70/月档，控制台中文友好、CN2/BGP 优化对 API 后端够用、活动价极低）。阿里云 200Mbps 不限流量性价比突出。
- **追求极致大陆质量**：**搬瓦工 HK CN2 GIA**（$75/月均，三网 GIA 1Gbps，业内天花板）或 **DMIT Premium**（~15ms，口碑最佳，但贵）。
- **极度预算 / 玩票**：GigsGigs HK-K1（$8.8/月），但配置太低（512M）跑 FastAPI+SQLite 勉强。

**买前必做** ✅：用各家的 **Looking Glass / 测速 IP** 在本地（点点的大陆网络）ping。香港到大陆**合理区间 20-50ms**；若长期 >80ms 或丢包严重，说明非真 CN2 GIA 直连，慎选。

> ⚠️ v1 后端只跑「鉴权 + LLM 代理 + 数据」（CV 已搬本地 sidecar，见 PRD §9），负载很轻，2核2G 足够起步，不够再升。轻量服务器一般不支持随时降配，按当前需求买即可。

---

## 4. Cloudflare 配置（大陆访问优化）

### 4.1 landing → Cloudflare Pages ✅
免费额度（sources: cloudflare.com）：500 次构建/月、1 并发、20 分钟构建超时、100 自定义域名、静态资源**请求与带宽均无限制**。Next.js 部署须 `output: 'export'` 静态导出（配合 `@cloudflare/next-on-pages`）。

> 注意：webapp 前端是 Next.js 16，但 landing（营销官网）是独立静态站，可纯静态导出上 Pages。**产品本体是桌面应用**，不走 Pages。

### 4.2 大陆访问优化 ⚠️（方案基于通用实践，未触发 grounding）
Cloudflare 默认智能路由常把大陆流量绕到拥堵国际节点。优化方案（"优选 IP"）：

1. 用 `XIU2/CloudflareSpeedTest` 或 `Better Cloudflare IP` 测出本地三网（电信/联通/移动）延迟最低、丢包最低的 Cloudflare 边缘 IP。
2. 通过 Cloudflare **SaaS（自定义主机名）**回源到优选 IP（SaaS 免费但需国际信用卡/PayPal 激活，通常不扣费）。
3. 在 DNS（DNSPod/阿里云 DNS 等支持分线路解析的）配置**分运营商解析**，三网分别指向各自优选 IP。

**时效性**：优选 IP 会变，需配合 DDNS 脚本定期更新。

**对 Aiming Cookie 的建议**：
- landing（Pages）可走 Cloudflare + 优选 IP。
- **后端 API 直连 VPS IP，不走 Cloudflare 代理**（DNS 只解析 A 记录到 VPS，关掉橙云）——VPS 本身在香港 CN2 GIA 线路上，直连延迟最低，且避免 Cloudflare 在大陆偶发的干扰/回源绕路。LLM 流式响应走 Cloudflare 反代还可能增加延迟。

---

## 5. 后端部署（香港 VPS · FastAPI）

复用 `webapp/backend/` slice 1 已 merge 的 FastAPI（角色按 PRD §9 瘦身为「账号 / LLM 代理 / 数据同步」）。

- **进程**：`docker-compose`（FastAPI + 可选 Postgres）+ systemd 开机自启
- **反代**：nginx + Let's Encrypt (certbot) HTTPS
- **DB**：SQLite（v1 单机够）→ B 阶段升 Postgres
- **密钥**：LLM API key 等走环境变量 / `.env`，不进代码仓库
- **目录结构建议**：`/opt/aiming-cookie/{docker-compose.yml, .env, data/}`

---

## 6. CI/CD ✅（sources: github.com / cloudflare.com / tencentcloud.com）

| 产物 | 方案 | 要点 |
|---|---|---|
| landing | **Cloudflare Pages 官方 Git 集成** | Dashboard 绑 GitHub 仓库 → git push 自动构建。无需维护 workflow，原生支持预览部署 |
| 后端 API | **GitHub Actions → GHCR → SSH** | 构建镜像推 GHCR → `appleboy/ssh-action` 登 VPS → `docker-compose pull && up -d` |
| 桌面安装包 | **GitHub Actions 构建 → GitHub Releases** | PRD §9.1 已定；跨平台矩阵构建（win/mac） |

**安全铁律** ✅：
- SSH 私钥、`CLOUDFLARE_API_TOKEN`、`CLOUDFLARE_ACCOUNT_ID` 全部存 **GitHub Secrets**
- 所有第三方 Action **固定到具体 commit SHA**（不 pin `@main`/`@v2`），防供应链攻击
- SSH key 用 `ssh-keygen -m PEM -t rsa -b 4096` 生成

GitHub Actions 私有仓库每月 2000 分钟免费额度，v1 足够。

---

## 7. 成本预估（月）

| 项 | 方案 | 月成本 |
|---|---|---|
| VPS | 腾讯云/阿里云轻量香港 2核 | ~¥25-70 |
| （或） | 搬瓦工 HK CN2 GIA | ~$75（¥540） |
| 域名 | .com（Cloudflare Registrar） | ~¥6（$10.44/年摊） |
| landing | Cloudflare Pages | 免费 |
| 桌面包分发 | GitHub Releases | 免费 |
| CI/CD | GitHub Actions | 免费（私有仓 2000 分钟/月） |
| LLM | DeepSeek 默认（按 token） | 按用量 |

**v1 合计 ~¥30-80/月**（国产轻量路线）或 ~¥550/月（搬瓦工顶配路线）。与 PRD §9.1「~¥100/月」量级一致。

---

## 8. 上线 checklist（执行顺序）

1. [ ] **域名**：Cloudflare Registrar（或 Porkbun）注册 `.com`
2. [ ] **VPS**：下单腾讯云/阿里云轻量香港 2核2G（起步）；用 Looking Glass 验证大陆延迟 <50ms
3. [ ] **DNS**：域名 NS → Cloudflare；A 记录指向 VPS IP（后端关橙云直连）
4. [ ] **后端**：VPS 上 docker-compose 起 FastAPI + SQLite；nginx + certbot HTTPS
5. [ ] **landing**：Cloudflare Pages 绑仓库，git push 自动部署
6. [ ] **验证大陆访问**：本地 ping/curl 测后端 API + landing 延迟与可用性
7. [ ] （可选）**Cloudflare 优选 IP**：landing 走优选 IP 提速
8. [ ] （B 阶段）DB 升 Postgres、history 上云、计费

---

## 9. 待点点决策

1. **VPS 选哪家**：国产轻量（腾讯/阿里，便宜省心）vs 搬瓦工/DMIT（贵但大陆质量顶级）——建议 v1 先国产轻量起步。
2. **域名注册商**：Cloudflare Registrar（成本价+一站式）vs Porkbun（解耦）。
3. **Cloudflare 优选 IP**：v1 就做（landing 提速）还是先用默认（YAGNI，等真慢了再优化）。
4. **桌面打包分发**：GitHub Releases 已在 PRD 定，确认即可（历史研究见 `docs/archive/legacy/desktop-packaging-research.md`，实际施工前重新核实）。

---

## 关联

- 方向锚：`docs/PRD.md` §9.1 / §5.2 / §11（合规）
- 架构前提：`docs/ARCHITECTURE.md`（Desktop hybrid 与 Local Runtime / Cloud 边界）
- 桌面打包历史研究：`docs/archive/legacy/desktop-packaging-research.md`（只供追溯，选型需通过新 spike）
