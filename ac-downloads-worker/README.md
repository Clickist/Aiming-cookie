# ac-downloads-worker · AC 下载量统计（Cloudflare Worker 前置 + R2 + D1）

`dl.aimingcookie.com` 的前置计数器。**2026-09-18 上线**：此前安装包直连 R2 自定义域、全链路无任何
计数，历史下载量无法补记（CF 免费版区域分析为空 + R2 无按对象计数 + 落地页无统计，均已实测确认）。
点点拍板：服务器侧真计数（方案②）先做；应用端首启上报装机量（方案③）等账号系统上线后再接。

```
用户 / 更新器 GET https://dl.aimingcookie.com/<文件>
  → zone 路由 dl.aimingcookie.com/*（Worker 前置，先于源站与边缘缓存）
  → Worker ac-downloads：R2 桶 aiming-cookie-downloads 读流返回（Range 断点续传支持）
  → D1 库 ac-downloads.download_events 记一行（GET 记，HEAD 不记；记失败不影响下载）
```

## 计数口径

| kind | 含义 | 是否算"下载量" |
|---|---|---|
| `installer` | `*-setup.exe` 安装包 | ✅ 是 |
| `update_check` | `latest.json` 更新检查轮询 | ❌ 单列 |
| `sig` | `.sig` 签名文件（更新器配套） | ❌ 单列 |
| `other` | 其他对象 | ❌ 单列 |

- 断点续传/分段下载每段各记一行（`has_range=1`）；**主口径 = `has_range=0`**。
- 版本号从文件名解析（兼容 `Aiming.Cookie_0.1.10` 与 `Aiming_Cookie_1.2.2` 两种历史命名）。
- 另存国家（`request.cf.country`）与 UA 前 200 字符，便于过滤爬虫/脚本。

## 查询

```bash
# 各类总量（主口径）
npx wrangler d1 execute ac-downloads --remote --command \
  "SELECT kind, COUNT(*) n FROM download_events WHERE has_range=0 GROUP BY kind"

# 按版本的安装包下载量
npx wrangler d1 execute ac-downloads --remote --command \
  "SELECT version, COUNT(*) n FROM download_events WHERE kind='installer' AND has_range=0 GROUP BY version ORDER BY version"

# 最近 7 天按日
npx wrangler d1 execute ac-downloads --remote --command \
  "SELECT date(created_at/1000,'unixepoch') d, COUNT(*) n FROM download_events \
   WHERE kind='installer' AND has_range=0 AND created_at > (strftime('%s','now')-7*86400)*1000 GROUP BY d"
```

## 运维

- 部署：`npx wrangler deploy`（wrangler.toml 就绪，含路由声明；MCP/控制台部署亦可）
- 验证：响应头 `X-AC-Downloads: worker` = 请求走了本 Worker
- **回滚**：zone `aimingcookie.com` → Workers Routes → 删 `dl.aimingcookie.com/*`，
  流量立即回落到原 R2 自定义域（保留未动），秒级生效
- 客户端/落地页零改动：URL、字节、ETag、Last-Modified、断点续传与 R2 直连一致
  （2026-09-18 验收：150MB 完整下载 sha256 与迁移前逐字节一致 `b0ae1126…`）
- 已知取舍：Worker 前置后放弃原边缘缓存 HIT（CF 默认缓存 .exe），每次下载回源 WNAM 桶；
  R2 出口流量免费、读操作用量不足免费额度 0.1%，速度感知无差别
