# ac-logs-worker · AC 诊断包接收（Cloudflare Worker + R2 + D1）

`logs.aimingcookie.com` 的接收端。**2026-09-13 起取代洛杉矶机上的 `ac_logs_server.py`**
（点点拍板：日志上传不必占服务器——他担心的"CF 限流"实为 KV 1000 写/天特有配额，
本用法走 R2[100 万次写/月]+Worker[10 万请求/天]，用量不足 0.1%）。

```
AC 桌面端「上传诊断包」
  → POST https://logs.aimingcookie.com/upload（头 X-AC-Token）
  → CF 边缘限速规则（20 次/10 秒/IP，免费档）
  → Worker ac-logs：token → 10MB → schema 校验 → D1 去重(hash) → 单 IP 10 次/时 → 200MB/天
  → R2 桶 ac-logs（bundles/<日期>/<编号>.json，14 天生命周期自动删）+ D1 台账 uploads 表
```

## 取包 / 排障

```bash
# 列最近的包（时间倒序）
npx wrangler r2 object get ac-logs/bundles/<日期>/<编号>.json --file <编号>.json
# 或控制台：R2 → ac-logs → bundles/<日期>/
# 台账（谁、何时、多大、哪个版本、IP）：
npx wrangler d1 execute ac-logs --command "SELECT * FROM uploads ORDER BY created_at DESC LIMIT 20"
# Worker 实时日志：控制台 Workers → ac-logs → Logs
```

用户报障只需报编号；编号格式 `YYYYMMDD-HHMMSS-<hash8>`（北京时间）。

## 客户端契约（勿破坏）

- `POST /upload`，头 `X-AC-Token`，体 = 诊断包 JSON（**camelCase 键**，校验 `schemaVersion` 存在、读 `appVersion`——0913 真机验收抓过两版字段名错：schema/schema_version 都不对）
- 成功 `200 {"id":"...","bytes":n}`；重复 `200 {"id":"原编号","duplicate":true}`
- 限频 `429`（Retry-After）；日额度 `503`；token 错 `403`；包体非法 `400`；超大小 `413`
- 客户端实现：`webapp/frontend/lib/desktop.ts` `uploadDesktopCaptureDiagnostics`

## 运维

- 部署：`npx wrangler deploy`（wrangler.toml 就绪；token 在控制台/`wrangler secret put AC_LOGS_TOKEN`）
- 变更限速：zone aimingcookie.com → 限速规则（20 次/10s/IP block）
- 回滚：服务器版（`../ac-logs-server/`）保留作退役备份，回滚步骤见内部文档。
