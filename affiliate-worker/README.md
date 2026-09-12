# affiliate-links —— 型号→购买链接微服务

Cloudflare Worker：输入鼠标品牌/型号，返回淘宝联盟（物料搜索，结果自带推广短链）
和拼多多多多客（搜索+转链）的购买链接。联盟密钥全部存 Worker Secrets，客户端只拿短链，
所有点击佣金归本账号 PID。供 AC coach 与 mousedle（将来）共用；Mac 上的桌搭项目也可复用。

## API

- `GET /health` → `{ok, tb, pdd, kv}`（布尔，不回显密钥）
- `POST /links`，header `x-ac-token`，body：

```json
{ "items": [ { "brand": "Logitech", "model": "G Pro X Superlight 2", "variant": "SE" } ] }
```

- 返回：`{ "results": [ { "q", "taobao": {title, price, sales, url} | {miss, reason}, "pdd": 同形 } ] }`
- `GET /debug?q=...`（同 token）：返回淘宝原始响应结构，排查选品问题用。
- 最多 6 个 item/次；taobao 命中缓存 7 天，miss 缓存 24h（KV，key 前缀 v2:）。

## 选品规则（worker.js）

配件词过滤（不能用裸"线"字，"无线鼠标"会全中枪）→ 类目/标题须含"鼠标" →
两级标题匹配（严格=全部型号词命中；放宽=一半以上且品牌对）→ 按 `annual_vol` 销量排序。
型号词支持中文俗名映射（viper→毒蝰、gpw→狗屁王等，见 `MODEL_CN`）。

## 部署

```bash
cd affiliate-worker
npx wrangler deploy        # 自定义域名 affiliate.gearclickist.com（workers.dev 已关，国内不可达）
```

Secrets（wrangler secret put <NAME>）：`TB_APP_KEY` `TB_APP_SECRET` `TB_ADZONE_ID`
`PDD_CLIENT_ID` `PDD_CLIENT_SECRET` `PDD_PID` `AC_SHARED_TOKEN`。
凭证来源：桌面《渠道接入交接-2026-09-11.md》（淘宝 PID 第三段即 adzone_id）。

## 冒烟

```bash
curl -q -X POST https://affiliate.gearclickist.com/links \
  -H "Content-Type: application/json" -H "x-ac-token: <token>" \
  -d '{"items":[{"brand":"Logitech","model":"G304"}]}'
```

## 已知状态（2026-09-11）

- 淘宝：4/4 型号命中（GPX2 SE / 蜻蜓 R1 / 毒蝰 V3 Pro / G304），短链带佣金。
- 拼多多：接口通、授权通，但新账号索引未放数，关键词一律 0 命中——放数后自动生效，无需改代码。
- 京东：转链接口只支持网站/APP 推广位（要 ICP 备案），B站媒体位不可转——不进本服务；
  AC 侧继续用 marketplace-mapping 的静态直链做末位兜底（准确性未核验，被反爬拦无法批量验）。
