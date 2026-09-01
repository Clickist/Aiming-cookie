// aiming-stats — Aiming Cookie 落地页极简统计
// POST /api/stats/c  采集：{t:"pv"|"dl", p:path, r:referrer, d:下载链接}
// GET  /api/stats/v?t=<STATS_TOKEN>  仪表盘（HTML）
// 隐私：无 cookie；IP 原文不落库，仅存「日 + IP + UA」哈希前缀做粗去重。

const ALLOWED_HOSTS = new Set(['aimingcookie.com', 'www.aimingcookie.com']);
const DAY = 86400000;

async function sha256Hex(s) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, '0')).join('');
}

async function collect(req, env) {
  const origin = req.headers.get('origin') || req.headers.get('referer') || '';
  let host = '';
  try { host = new URL(origin).host; } catch (e) {}
  if (!ALLOWED_HOSTS.has(host)) return new Response(null, { status: 403 });

  let d = null;
  try { d = await req.json(); } catch (e) {}
  if (!d || (d.t !== 'pv' && d.t !== 'dl')) return new Response(null, { status: 204 });

  const day = new Date().toISOString().slice(0, 10);
  const ip = req.headers.get('cf-connecting-ip') || '';
  const ua = req.headers.get('user-agent') || '';
  const uid = (await sha256Hex(ip + '|' + ua + '|' + day)).slice(0, 16);

  await env.DB.prepare(
    'INSERT INTO events (ts, type, path, ref, country, dl, uid) VALUES (?,?,?,?,?,?,?)'
  ).bind(
    Date.now(), d.t,
    String(d.p || '/').slice(0, 120),
    host,
    req.headers.get('cf-ipcountry') || '',
    String(d.d || '').slice(0, 200),
    uid
  ).run();
  return new Response(null, { status: 204 });
}

async function dashboard(url, env) {
  if (url.searchParams.get('t') !== env.STATS_TOKEN) {
    return new Response('forbidden', { status: 403 });
  }
  const now = Date.now();
  const q = async (sql, ...binds) => (await env.DB.prepare(sql).bind(...binds).all()).results;

  const win = async (ms) => {
    const rows = await q(
      `SELECT
         SUM(CASE WHEN type='pv' THEN 1 ELSE 0 END) AS pv,
         COUNT(DISTINCT CASE WHEN type='pv' THEN uid END) AS uniq,
         SUM(CASE WHEN type='dl' THEN 1 ELSE 0 END) AS dl
       FROM events WHERE ts > ?`, now - ms);
    return rows[0] || {};
  };
  const [today, d7, d30] = [await win(0.9 * DAY), await win(7 * DAY), await win(30 * DAY)];
  const daily = await q(
    `SELECT date(ts/1000, 'unixepoch') AS day,
            SUM(CASE WHEN type='pv' THEN 1 ELSE 0 END) AS pv,
            COUNT(DISTINCT CASE WHEN type='pv' THEN uid END) AS uniq,
            SUM(CASE WHEN type='dl' THEN 1 ELSE 0 END) AS dl
     FROM events WHERE ts > ? GROUP BY day ORDER BY day DESC LIMIT 14`, now - 14 * DAY);
  const refs = await q(
    `SELECT ref, COUNT(*) AS n FROM events WHERE type='pv' AND ts > ? AND ref != ''
     GROUP BY ref ORDER BY n DESC LIMIT 10`, now - 30 * DAY);
  const countries = await q(
    `SELECT country, COUNT(*) AS n FROM events WHERE type='pv' AND ts > ? AND country != ''
     GROUP BY country ORDER BY n DESC LIMIT 10`, now - 30 * DAY);
  const dls = await q(
    `SELECT dl, COUNT(*) AS n FROM events WHERE type='dl' AND ts > ?
     GROUP BY dl ORDER BY n DESC LIMIT 10`, now - 30 * DAY);

  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const table = (head, rows) => rows.length
    ? `<table><tr>${head.map(h => `<th>${esc(h)}</th>`).join('')}</tr>${
        rows.map(r => `<tr>${r.map(c => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</table>`
    : '<p class="empty">（暂无数据）</p>';
  const card = (label, v) => `<div class="card"><div class="num">${v ?? 0}</div><div class="label">${esc(label)}</div></div>`;

  const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>统计 · Aiming Cookie</title><style>
  body{font-family:'Inter','PingFang SC','Microsoft YaHei',sans-serif;background:#f7f5f0;color:#24211d;margin:0;padding:32px;}
  h1{font-size:22px;margin:0 0 6px;} .sub{color:#625c54;font-size:13px;margin-bottom:28px;}
  h2{font-size:16px;margin:32px 0 12px;}
  .cards{display:flex;gap:16px;flex-wrap:wrap;} .card{background:#fffdf8;border:1px solid #cec6bc;border-radius:12px;padding:16px 24px;min-width:120px;}
  .num{font-size:26px;font-weight:700;} .label{color:#625c54;font-size:12px;margin-top:4px;}
  table{border-collapse:collapse;background:#fffdf8;border:1px solid #cec6bc;border-radius:8px;overflow:hidden;width:100%;max-width:560px;}
  th,td{padding:8px 14px;font-size:14px;border-bottom:1px solid #eee9e1;text-align:left;} th{color:#625c54;font-weight:600;}
  tr:last-child td{border-bottom:none;} .empty{color:#625c54;font-size:14px;} .grp{font-size:13px;color:#625c54;margin:26px 0 -6px;}
</style></head><body>
<h1>Aiming Cookie · 落地页统计</h1>
<div class="sub">生成于 ${esc(new Date().toISOString().slice(0, 16).replace('T', ' '))} UTC · 数据为自托管采集，无 cookie</div>

<div class="grp">页面浏览 / 独立访客（按日去重）/ 下载点击</div>
<div class="cards">
  ${card('今日 PV', today.pv)}${card('今日访客', today.uniq)}${card('今日下载点击', today.dl)}
  ${card('7日 PV', d7.pv)}${card('7日访客', d7.uniq)}${card('7日下载点击', d7.dl)}
  ${card('30日 PV', d30.pv)}${card('30日访客', d30.uniq)}${card('30日下载点击', d30.dl)}
</div>

<h2>近 14 天逐日</h2>
${table(['日期', 'PV', '访客', '下载点击'], daily.map(r => [r.day, r.pv, r.uniq, r.dl]))}

<h2>来源（30 天）</h2>
${table(['来源站', 'PV'], refs.map(r => [r.ref || '(空/直达)', r.n]))}

<h2>国家/地区（30 天）</h2>
${table(['地区', 'PV'], countries.map(r => [r.country, r.n]))}

<h2>下载点击明细（30 天）</h2>
${table(['目标', '次数'], dls.map(r => [r.dl, r.n]))}
</body></html>`;
  return new Response(html, { headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' } });
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (url.pathname === '/api/stats/c') {
      if (req.method !== 'POST') return new Response(null, { status: 204 });
      return collect(req, env);
    }
    if (url.pathname === '/api/stats/v') return dashboard(url, env);
    return new Response('not found', { status: 404 });
  }
};
