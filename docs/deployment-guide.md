# Aiming Cookie 部署候选方案调研（2026-07-10 快照）

> **状态**：历史候选方案与时间敏感调研，不是已批准的实施计划，也不是产品、架构、合规或当前运行状态的事实源。供应商、价格、政策、平台能力和代码入口必须在实际部署前重新核实。
>
> 建立于 2026-07-10，记录当时的候选方案和调研证据。PRD 现在只冻结“合规境外部署 + 发布前复核”的产品约束，不再指定供应商；本文中的香港 VPS、Cloudflare、GitHub Releases、价格与线路均为候选参考。
>
> 使用前先读取 `docs/PRD.md` 与 `docs/ARCHITECTURE.md`，再重新核实法规、供应商、价格、地区可用性与数据义务。未经新的 active plan 批准，不得把本文 checklist 当作当前部署指令。

## 0. 信息来源与可信度

本文的价格/政策数据通过 `gemini-grounding-search`（Antigravity OAuth 路径，gemini-2.5-flash）于 2026-07-10 获取。每条标注可信度：

- ✅ = 有搜索 sources 背书（grounding 触发）
- ⚠️ = 未触发 grounding 或基于通用知识，**下单/配置前请实时核实**

> 工具说明：gemini grounding 从大陆 IP 会出现间歇性 `User location is not supported`（400），与 VPN 波动相关——查询时若全批失败，等几分钟或切节点重试。综述性/政策性 query 不易触发搜索，需拆成"具体套餐/产品名/数字"才会返回 sources。

---

## 1. 合规与托管边界（部署前重新核实）

**2026-07-10 的调研结论**（当时参考 huaweicloud.com / aliyun.com / alibabacloud.com 等厂商资料）：香港等境外托管与中国大陆境内托管适用的备案要求不同，CDN 节点、域名解析和实际服务形态也会影响义务。该结论不是持续有效的法律意见。

候选 checklist；实际结论必须在上线前由最新法规和目标服务形态重新确认：

- [ ] 核实后端托管地区对应的备案、许可和数据义务
- [ ] 核实域名解析、托管地区与目标用户服务方式对应的备案义务
- [ ] 核实 CDN 节点地区、备案要求和服务条款
- [ ] 核实对象存储、公网回源与数据跨境义务
- [ ] DNS、证书和域名供应商按发布时合规性与可用性选择
- [ ] landing 托管方案需按实际节点、域名和监管要求重新确认

**合规补充**：境外托管不等于无监管。内容、个人信息、数据跨境、支付和用户所在地义务必须在上线前由合适的法律/合规渠道确认。

---

## 2. 域名候选调研

注册商对比 ✅（sources: cloudflare.com / porkbun.com / namesilo.com）：

| 注册商 | .com 首年/续费 | WHOIS 隐私 | 备注 |
|---|---|---|---|
| **Cloudflare Registrar** | **$10.44/年** | 免费 | 注册局成本价，**无溢价**，续费透明；推荐 |
| **Porkbun** | $10.99/年 | 免费 | 界面友好，价格低 |
| NameSilo | $17.29/年（预存 $50 后 $11.05） | 免费 | 零售偏贵 |

**当时的候选判断**：Cloudflare Registrar 偏向低成本和一站式管理，Porkbun 偏向注册商与 DNS 解耦。该判断不是当前推荐；注册前应重新核实价格、地区可用性、转入限制和目标 TLD 支持。

**历史方案路径**：注册 `.com` → 将域名 NS 指向 Cloudflare → 在 Cloudflare 管理 DNS。只有在新的 active plan 明确选定该方案后才执行。

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

**当时记录的候选分层**（需重新评估）：
- **v1 流量小、求省心**：**腾讯云或阿里云轻量香港**（¥25-70/月档，控制台中文友好、CN2/BGP 优化对 API 后端够用、活动价极低）。阿里云 200Mbps 不限流量性价比突出。
- **追求极致大陆质量**：**搬瓦工 HK CN2 GIA**（$75/月均，三网 GIA 1Gbps，业内天花板）或 **DMIT Premium**（~15ms，口碑最佳，但贵）。
- **极度预算 / 玩票**：GigsGigs HK-K1（$8.8/月），但配置太低（512M）跑 FastAPI+SQLite 勉强。

**若重新选择香港 VPS，购买前应做**：用各家的 **Looking Glass / 测速 IP** 在本地（点点的大陆网络）ping。香港到大陆**合理区间 20-50ms**；若长期 >80ms 或丢包严重，说明非真 CN2 GIA 直连，慎选。

> ⚠️ v1 后端只跑「鉴权 + LLM 代理 + 数据」（CV 已搬本地 sidecar，见 PRD §9），负载很轻，2核2G 足够起步，不够再升。轻量服务器一般不支持随时降配，按当前需求买即可。

---

## 4. Cloudflare 候选配置（历史方案）

### 4.1 landing → Cloudflare Pages ✅
免费额度（sources: cloudflare.com）：500 次构建/月、1 并发、20 分钟构建超时、100 自定义域名、静态资源**请求与带宽均无限制**。Next.js 部署须 `output: 'export'` 静态导出（配合 `@cloudflare/next-on-pages`）。

> 注意：webapp 前端是 Next.js 16，但 landing（营销官网）是独立静态站，可纯静态导出上 Pages。**产品本体是桌面应用**，不走 Pages。

### 4.2 大陆访问优化 ⚠️（方案基于通用实践，未触发 grounding）
Cloudflare 默认智能路由常把大陆流量绕到拥堵国际节点。优化方案（"优选 IP"）：

1. 用 `XIU2/CloudflareSpeedTest` 或 `Better Cloudflare IP` 测出本地三网（电信/联通/移动）延迟最低、丢包最低的 Cloudflare 边缘 IP。
2. 通过 Cloudflare **SaaS（自定义主机名）**回源到优选 IP（SaaS 免费但需国际信用卡/PayPal 激活，通常不扣费）。
3. 在 DNS（DNSPod/阿里云 DNS 等支持分线路解析的）配置**分运营商解析**，三网分别指向各自优选 IP。

**时效性**：优选 IP 会变，需配合 DDNS 脚本定期更新。

**当时对 Aiming Cookie 的候选建议**（不是当前批准方案）：
- landing（Pages）可走 Cloudflare + 优选 IP。
- **后端 API 直连 VPS IP，不走 Cloudflare 代理**（DNS 只解析 A 记录到 VPS，关掉橙云）——VPS 本身在香港 CN2 GIA 线路上，直连延迟最低，且避免 Cloudflare 在大陆偶发的干扰/回源绕路。LLM 流式响应走 Cloudflare 反代还可能增加延迟。

---

## 5. 后端部署候选形态（香港 VPS · FastAPI）

以下内容记录 2026-07-10 时设想的部署形态。实际部署必须先以当前代码、测试和 `docs/ARCHITECTURE.md` 核验服务边界，再由新的 active plan 冻结入口、存储、身份和回滚方案。

- **进程**：`docker-compose`（FastAPI + 可选 Postgres）+ systemd 开机自启
- **反代**：nginx + Let's Encrypt (certbot) HTTPS
- **DB**：SQLite（v1 单机够）→ B 阶段升 Postgres
- **密钥**：LLM API key 等走环境变量 / `.env`，不进代码仓库
- **目录结构建议**：`/opt/aiming-cookie/{docker-compose.yml, .env, data/}`

### 5.1 探活、就绪与预览身份（内部技术预览）

| 端点 / 变量 | 用途 |
|---|---|
| `GET /healthz` | **存活探针**：进程能响应即 200 `{"ok":true}`，给负载均衡 / systemd 轻量检查。 |
| `GET /readyz` | **就绪探针**：DB 可连，且配置了 `COACH_SIDECAR_URL` 时 sidecar `/healthz` 可达 → 200；任一失败 → 503。 |
| `TRUST_PROXY_USER=1` | **预览/生产身份**：只认反代注入的 `X-Forwarded-User` 或 `Remote-User`，**忽略**客户端自填的 `X-User-Id`。前面必须有 VPN/SSO/nginx 等可信反代。 |
| `COACH_SIDECAR_URL` | Pi coach 常驻 sidecar 基址（默认 `http://127.0.0.1:8765`）。 |

**当时的本地预览建议**：与 API 同机用 `./scripts/dev-up.sh` 一并拉起 sidecar + API（见 `webapp/README.md`）。

**Coach sidecar 启动**（与 API 同机 loopback）：

```bash
./scripts/run-coach-sidecar.sh
# 可选：COACH_SIDECAR_PORT=8765 COACH_SIDECAR_HOST=127.0.0.1
```

API 环境示例：`.env` 中 `COACH_SIDECAR_URL=http://127.0.0.1:8765`；配置该 URL 后，`/readyz` 会把 sidecar 健康作为就绪条件。不使用 sidecar 时将该变量设为空。

**nginx 预览身份示例**（示意，按实际 SSO 改 `auth_request` / header）：

```nginx
proxy_set_header X-Forwarded-User $remote_user;
# 应用侧 TRUST_PROXY_USER=1
```

本地开发默认 `TRUST_PROXY_USER=0`，仍可用 `X-User-Id: dev`。

**浏览器验收骨架**（可选 CI / 发布前）：`webapp/tests/test_browser_smoke.py`；需 `pip install playwright && playwright install chromium`。


---

## 6. CI/CD 候选方案（2026-07-10 调研）

| 产物 | 方案 | 要点 |
|---|---|---|
| landing | **Cloudflare Pages 官方 Git 集成** | Dashboard 绑 GitHub 仓库 → git push 自动构建。无需维护 workflow，原生支持预览部署 |
| 后端 API | **GitHub Actions → GHCR → SSH** | 构建镜像推 GHCR → `appleboy/ssh-action` 登 VPS → `docker-compose pull && up -d` |
| 桌面安装包 | **GitHub Actions 构建 → GitHub Releases** | 历史候选；发布渠道、签名和平台矩阵须由新的 active plan 冻结 |

**若采用上述方案，仍需满足的安全基线**：
- SSH 私钥、`CLOUDFLARE_API_TOKEN`、`CLOUDFLARE_ACCOUNT_ID` 全部存 **GitHub Secrets**
- 所有第三方 Action **固定到具体 commit SHA**（不 pin `@main`/`@v2`），防供应链攻击
- SSH key 用 `ssh-keygen -m PEM -t rsa -b 4096` 生成

当时调研认为 GitHub Actions 免费额度可能足够早期使用；实际额度、计费与仓库策略必须在启用前重新核实。

---

## 7. 历史成本快照（月）

| 项 | 方案 | 月成本 |
|---|---|---|
| VPS | 腾讯云/阿里云轻量香港 2核 | ~¥25-70 |
| （或） | 搬瓦工 HK CN2 GIA | ~$75（¥540） |
| 域名 | .com（Cloudflare Registrar） | ~¥6（$10.44/年摊） |
| landing | Cloudflare Pages | 免费 |
| 桌面包分发 | GitHub Releases | 免费 |
| CI/CD | GitHub Actions | 免费（私有仓 2000 分钟/月） |
| LLM | DeepSeek 默认（按 token） | 按用量 |

**2026-07-10 粗估**：国产轻量路线约 ¥30-80/月，较高线路质量方案约 ¥550/月。该估算不再由 PRD 背书，不得用于当前预算或采购决策。

---

## 8. 若重新批准后的复核清单

> 本节保留的是历史步骤轮廓，不代表推荐顺序。只有新的 active plan 明确了负责人、环境、回滚、验证和供应商后，才能把对应条目转成可执行任务。

1. [ ] **域名**：Cloudflare Registrar（或 Porkbun）注册 `.com`
2. [ ] **VPS**：下单腾讯云/阿里云轻量香港 2核2G（起步）；用 Looking Glass 验证大陆延迟 <50ms
3. [ ] **DNS**：域名 NS → Cloudflare；A 记录指向 VPS IP（后端关橙云直连）
4. [ ] **后端**：VPS 上 docker-compose 起 FastAPI + SQLite；nginx + certbot HTTPS
5. [ ] **landing**：Cloudflare Pages 绑仓库，git push 自动部署
6. [ ] **验证大陆访问**：本地 ping/curl 测后端 API + landing 延迟与可用性
7. [ ] （可选）**Cloudflare 优选 IP**：landing 走优选 IP 提速
8. [ ] （B 阶段）DB 升 Postgres、history 上云、计费

---

## 9. 历史未决选项（需重新评估）

1. **托管区域与供应商**：先依据最新合规、目标用户、延迟、可靠性和成本重新形成候选，不沿用本文结论。
2. **域名与 DNS 供应商**：重新核实价格、地区可用性、账户安全和解耦需求。
3. **CDN 与大陆访问策略**：基于发布时的真实网络测试和合规边界重新选择，不默认采用优选 IP。
4. **桌面打包分发**：发布渠道、签名、公证、安装器和更新机制需通过新的 active plan 冻结；历史研究见 `docs/archive/legacy/desktop-packaging-research.md`。

---

## 关联

- 产品与合规方向锚：`docs/PRD.md`
- 架构前提：`docs/ARCHITECTURE.md`（Desktop、Local Runtime 与 Cloud 边界）
- 桌面打包历史研究：`docs/archive/legacy/desktop-packaging-research.md`（只供追溯，选型需通过新 spike）
