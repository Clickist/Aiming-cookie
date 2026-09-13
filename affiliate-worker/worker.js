/**
 * affiliate-links —— 型号→购买链接微服务（Cloudflare Workers）
 *
 * 淘宝联盟物料搜索（结果自带推广短链）+ 拼多多多多客搜索/转链。
 * 联盟密钥全部走 Worker Secrets，客户端（AC coach / mousedle 页面）只拿到短链。
 *
 * API：
 *   GET  /health → { ok, tb, pdd, kv }（只报布尔，不回显密钥）
 *   POST /links  header x-ac-token  body {"items":[{"brand":"罗技","model":"GPX2","variant":"SE"} | {"q":"罗技 gpx2"}]}
 *             → {"results":[{"q","taobao":{title,price,sales,url}|{miss,reason},"pdd":同形|null}]}
 *
 * 部署与冒烟见同目录 README.md。
 */

import brandAliasesData from "./brand-aliases.json";

const TB_GATEWAY = "https://eco.taobao.com/router/rest";
const PDD_GATEWAY = "https://gw-api.pinduoduo.com/api/router";

// 配件类目/标题词，命中即跳过（搜索会混进微动开关、伞绳线等配件）
// 注意不能用裸"线"字——"无线鼠标"会全中枪
const ACCESSORY_WORDS = ["开关", "鼠标垫", "键帽", "脚贴", "伞绳", "数据线", "充电线", "换线", "线夹", "壳", "贴纸", "收纳", "维修", "修理", "快修", "防滑贴", "配件", "适用于"];

// 型号词的中文俗名（淘宝标题常用中文名，英文官方名搜得到但标题匹配不上）
const MODEL_CN = {
  viper: "毒蝰",
  deathadder: "蝰蛇",
  basilisk: "巴塞利斯蛇",
  naga: "那伽梵蛇",
  orochi: "八岐大蛇",
  mamba: "曼巴",
  cobra: "眼镜蛇",
  lancehead: "强袭",
  gpw: "狗屁王",
  dragonfly: "蜻蜓",
  keris: "月刃",
  gen: "二代",
};

// 品牌别名数据与代码分离：brand-aliases.json 由三路联网调研合并生成
// （2026-09-11，186 品牌 / 85 个中文别名；ZywOo 特补 Pulsar 派世联名词）。
const BRAND_ALIASES = brandAliasesData;

// 无官方淘宝/京东渠道的品牌（点点拍板 2026-09-12）：不出链，免得把人引去
// 第三方代购。命中即返回 miss，由 Coach 降级链接手（建议用户自行搜索）。
const NO_LINK_BRANDS = new Set(["vaxee", "finalmouse"]);

// ── 官方直签联盟（品牌官方联盟计划，优先于淘宝联盟）──────────────────
// ref 码公开可见（推广链接本就带在用户面前），非机密；以后品牌直签往这里加行。
const DIRECT_AFFILIATES = {
  gwolves: {
    label: "G-Wolves 官方店",
    ref: "bzkvvtdy",
    coupon: "click", // 点点的 ¥30 优惠码（GoAffPro 门户），/discount 路由自动挂券
    searchBase: "https://www.g-wolves.cn/search?q=",
    searchSuffix: "&options%5Bprefix%5D=last",
  },
};

// 官方店直搜：抓搜索结果页的 /products/ 链接与标题，按型号词挑最合适的，
// 拼上 ref 码返回。失败返回 null（调用方回退淘宝联盟）。
async function directAffiliateSearch(item, tok) {
  const cfg = DIRECT_AFFILIATES[norm(item.brand || "")];
  if (!cfg) return null;
  const q = [item.model, item.variant].filter(Boolean).join(" ").trim() || "mouse";
  try {
    const res = await fetch(cfg.searchBase + encodeURIComponent(q) + cfg.searchSuffix, {
      headers: { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0" },
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) return { platform: "direct", miss: true, reason: "api_error", msg: "HTTP " + res.status };
    const html = await res.text();
    const links = [...html.matchAll(/href="([^"]*products\/[a-z0-9-]+)"/g)].map((m) => m[1]);
    const titles = new Map(
      [...html.matchAll(/<a[^>]+href="([^"]*products\/[^"]+)"[^>]*>\s*([^<]{3,80}?)\s*<\/a>/g)].map((m) => [m[1], m[2]]),
    );
    if (!links.length) return { platform: "direct", miss: true, reason: "no_match" };
    let pick = null;
    for (const l of links) {
      const nl = norm(l);
      const nt = norm(titles.get(l) || "");
      const ok = tok.mt.every((t) => {
        const alias = MODEL_CN[t] ? norm(MODEL_CN[t]) : "";
        return nl.includes(t) || nt.includes(t) || (alias && (nl.includes(alias) || nt.includes(alias)));
      });
      if (ok) {
        pick = l;
        break;
      }
    }
    if (!pick) pick = links[0]; // 搜索页 prefix=last 已按相关度排序
    const path = pick.startsWith("/") ? pick : "/" + pick;
    // 有优惠码时走 /discount 路由：挂券 + redirect 直达商品页 + ref 佣金三合一
    // （浏览器实测：落地商品页且写入 discount_code cookie）
    const url = cfg.coupon
      ? `https://www.g-wolves.cn/discount/${cfg.coupon}?redirect=${encodeURIComponent(path)}&ref=${cfg.ref}`
      : "https://www.g-wolves.cn" + path + "?ref=" + cfg.ref;
    return {
      platform: "direct",
      title: (titles.get(pick) || cfg.label).trim(),
      price: "",
      sales: "",
      url,
    };
  } catch (e) {
    return { platform: "direct", miss: true, reason: "exception", msg: String(e).slice(0, 80) };
  }
}

// ── 小工具 ──────────────────────────────────────────────

const enc = new TextEncoder();
let kvWriteErrors = 0;
// isolate 级内存缓存：批测风暴时同 isolate 重复查询不再消耗 KV 写入额度
const memCache = new Map(); // key → { val, until }

function norm(s) {
  return (s || "").toLowerCase().replace(/[^0-9a-z\u4e00-\u9fff]/g, "");
}

// 淘宝返回的图片/短链常是协议相对地址（//img.alicdn.com/...）
function httpsUrl(u) {
  return typeof u === "string" && u ? (u.startsWith("//") ? "https:" + u : u) : "";
}

// UTF-8 字节串（供 md5 按字符处理，等价于对 UTF-8 字节做 MD5）
function utf8BinaryString(s) {
  const bytes = enc.encode(s);
  let out = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    out += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return out;
}

function tbTimestamp() {
  return new Date(Date.now() + 8 * 3600e3).toISOString().replace("T", " ").slice(0, 19);
}

async function tbSign(secret, params) {
  const src = Object.keys(params).sort().map((k) => k + params[k]).join("");
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(src));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("").toUpperCase();
}

// ── MD5（拼多多签名要求；Workers crypto.subtle 不支持 MD5）──────────────

function md5cycle(x, k) {
  let a = x[0], b = x[1], c = x[2], d = x[3];
  a = ff(a, b, c, d, k[0], 7, -680876936); d = ff(d, a, b, c, k[1], 12, -389564586);
  c = ff(c, d, a, b, k[2], 17, 606105819); b = ff(b, c, d, a, k[3], 22, -1044525330);
  a = ff(a, b, c, d, k[4], 7, -176418897); d = ff(d, a, b, c, k[5], 12, 1200080426);
  c = ff(c, d, a, b, k[6], 17, -1473231341); b = ff(b, c, d, a, k[7], 22, -45705983);
  a = ff(a, b, c, d, k[8], 7, 1770035416); d = ff(d, a, b, c, k[9], 12, -1958414417);
  c = ff(c, d, a, b, k[10], 17, -42063); b = ff(b, c, d, a, k[11], 22, -1990404162);
  a = ff(a, b, c, d, k[12], 7, 1804603682); d = ff(d, a, b, c, k[13], 12, -40341101);
  c = ff(c, d, a, b, k[14], 17, -1502002290); b = ff(b, c, d, a, k[15], 22, 1236535329);
  a = gg(a, b, c, d, k[1], 5, -165796510); d = gg(d, a, b, c, k[6], 9, -1069501632);
  c = gg(c, d, a, b, k[11], 14, 643717713); b = gg(b, c, d, a, k[0], 20, -373897302);
  a = gg(a, b, c, d, k[5], 5, -701558691); d = gg(d, a, b, c, k[10], 9, 38016083);
  c = gg(c, d, a, b, k[15], 14, -660478335); b = gg(b, c, d, a, k[4], 20, -405537848);
  a = gg(a, b, c, d, k[9], 5, 568446438); d = gg(d, a, b, c, k[14], 9, -1019803690);
  c = gg(c, d, a, b, k[3], 14, -187363961); b = gg(b, c, d, a, k[8], 20, 1163531501);
  a = gg(a, b, c, d, k[13], 5, -1444681467); d = gg(d, a, b, c, k[2], 9, -51403784);
  c = gg(c, d, a, b, k[7], 14, 1735328473); b = gg(b, c, d, a, k[12], 20, -1926607734);
  a = hh(a, b, c, d, k[5], 4, -378558); d = hh(d, a, b, c, k[8], 11, -2022574463);
  c = hh(c, d, a, b, k[11], 16, 1839030562); b = hh(b, c, d, a, k[14], 23, -35309556);
  a = hh(a, b, c, d, k[1], 4, -1530992060); d = hh(d, a, b, c, k[4], 11, 1272893353);
  c = hh(c, d, a, b, k[7], 16, -155497632); b = hh(b, c, d, a, k[10], 23, -1094730640);
  a = hh(a, b, c, d, k[13], 4, 681279174); d = hh(d, a, b, c, k[0], 11, -358537222);
  c = hh(c, d, a, b, k[3], 16, -722521979); b = hh(b, c, d, a, k[6], 23, 76029189);
  a = hh(a, b, c, d, k[9], 4, -640364487); d = hh(d, a, b, c, k[12], 11, -421815835);
  c = hh(c, d, a, b, k[15], 16, 530742520); b = hh(b, c, d, a, k[2], 23, -995338651);
  a = ii(a, b, c, d, k[0], 6, -198630844); d = ii(d, a, b, c, k[7], 10, 1126891415);
  c = ii(c, d, a, b, k[14], 15, -1416354905); b = ii(b, c, d, a, k[5], 21, -57434055);
  a = ii(a, b, c, d, k[12], 6, 1700485571); d = ii(d, a, b, c, k[3], 10, -1894986606);
  c = ii(c, d, a, b, k[10], 15, -1051523); b = ii(b, c, d, a, k[1], 21, -2054922799);
  a = ii(a, b, c, d, k[8], 6, 1873313359); d = ii(d, a, b, c, k[15], 10, -30611744);
  c = ii(c, d, a, b, k[6], 15, -1560198380); b = ii(b, c, d, a, k[13], 21, 1309151649);
  a = ii(a, b, c, d, k[4], 6, -145523070); d = ii(d, a, b, c, k[11], 10, -1120210379);
  c = ii(c, d, a, b, k[2], 15, 718787259); b = ii(b, c, d, a, k[9], 21, -343485551);
  x[0] = add32(a, x[0]); x[1] = add32(b, x[1]); x[2] = add32(c, x[2]); x[3] = add32(d, x[3]);
}
function cmn(q, a, b, x, s, t) { return add32(rotl(add32(add32(a, q), add32(x, t)), s), b); }
function ff(a, b, c, d, x, s, t) { return cmn((b & c) | (~b & d), a, b, x, s, t); }
function gg(a, b, c, d, x, s, t) { return cmn((b & d) | (c & ~d), a, b, x, s, t); }
function hh(a, b, c, d, x, s, t) { return cmn(b ^ c ^ d, a, b, x, s, t); }
function ii(a, b, c, d, x, s, t) { return cmn(c ^ (b | ~d), a, b, x, s, t); }
function rotl(n, c) { return (n << c) | (n >>> (32 - c)); }
function add32(a, b) { return (a + b) & 0xffffffff; }
function md5blk(s) {
  const blks = [];
  for (let i = 0; i < 64; i += 4)
    blks[i >> 2] = s.charCodeAt(i) + (s.charCodeAt(i + 1) << 8) + (s.charCodeAt(i + 2) << 16) + (s.charCodeAt(i + 3) << 24);
  return blks;
}
function md5Raw(binaryString) {
  const n = binaryString.length;
  const state = [1732584193, -271733879, -1732584194, 271733878];
  let i;
  for (i = 64; i <= n; i += 64) md5cycle(state, md5blk(binaryString.substring(i - 64, i)));
  const tail = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
  const rest = binaryString.substring(i - 64);
  for (i = 0; i < rest.length; i++) tail[i >> 2] |= rest.charCodeAt(i) << ((i % 4) << 3);
  tail[i >> 2] |= 0x80 << ((i % 4) << 3);
  if (i > 55) { md5cycle(state, tail); tail.fill(0); }
  tail[14] = n * 8;
  md5cycle(state, tail);
  return state.map(rhex).join("");
}
const HEX = "0123456789abcdef";
function rhex(n) {
  let s = "";
  for (let j = 0; j < 4; j++) s += HEX[(n >> (j * 8 + 4)) & 0x0f] + HEX[(n >> (j * 8)) & 0x0f];
  return s;
}
function md5(s) {
  return md5Raw(utf8BinaryString(s));
}

// ── 选品 ──────────────────────────────────────────────

function brandTokens(brand) {
  const nb = norm(brand);
  if (!nb) return [];
  if (BRAND_ALIASES[nb]) return BRAND_ALIASES[nb];
  let best = null;
  for (const key of Object.keys(BRAND_ALIASES)) {
    if (nb.includes(key) && (!best || key.length > best.length)) best = key;
  }
  return best ? BRAND_ALIASES[best] : [nb];
}

function tokensFor(item) {
  const bt = brandTokens(item.brand);
  const mt = [...new Set((item.model || "").split(/[\s\-+/\\]+/).map(norm).filter((t) => t.length >= 2))];
  const vt = norm(item.variant).length >= 3 ? [norm(item.variant)] : [];
  return { bt, mt, vt };
}

function cnAlias(token) {
  const t = norm(token);
  return MODEL_CN[t] || token;
}

// 型号词命中：标题含原词或其中文俗名
function tokenHit(title, token) {
  const t = norm(token);
  if (!t) return true;
  if (title.includes(t)) return true;
  const alias = MODEL_CN[t];
  return !!alias && title.includes(norm(alias));
}

function searchQuery(item) {
  if (item.q) return item.q.trim();
  const bt = brandTokens(item.brand);
  const parts = (item.model || "").split(/[\s\-+/\\]+/).map(cnAlias).filter(Boolean);
  const v = (item.variant || "").trim();
  const segs = [...new Set([...bt, ...parts])];
  if (v) segs.push(v);
  let q = segs.join(" ").trim();
  // 淘宝 q 过长会截断，超 40 字符时先丢品牌词（中→英顺序）
  while (q.length > 40 && segs.length > 1) {
    segs.shift();
    q = segs.join(" ").trim();
  }
  return q;
}

// 三级查询梯子：全词形态（品牌中英+型号）→ 纯中文品牌 → 纯英文品牌。
// 中英混写会把不少品牌搜零（zaopin 皂品 z1 pro → 0），逐级降约束。
function queryForms(item) {
  if (item.q) return { primary: item.q.trim(), forms: [] };
  const bt = brandTokens(item.brand);
  const btF = bt.filter((t) => !bt.some((o) => o !== t && t.includes(o)));
  const parts = (item.model || "").split(/[\s\-+/\\]+/).map(cnAlias).filter(Boolean);
  const v = (item.variant || "").trim();
  const build = (brandSegs) => {
    const segs = [...new Set(brandSegs)];
    if (v) segs.push(v);
    let q = segs.join(" ").trim();
    while (q.length > 40 && segs.length > 1) {
      segs.shift();
      q = segs.join(" ").trim();
    }
    return q;
  };
  const primary = build([...btF, ...parts]);
  const cn = btF.find((t) => /[\u4e00-\u9fff]/.test(t)) || "";
  const en = btF.find((t) => !/[\u4e00-\u9fff]/.test(t)) || (item.brand || "").trim();
  const forms = [];
  const push = (q) => {
    if (q && q !== primary && !forms.includes(q) && forms.length < 4) forms.push(q);
  };
  push(build([cn, ...parts].filter(Boolean)));
  push(build([en, ...parts].filter(Boolean)));
  // 型号英文尾缀（如 ultra wireless）在中文标题常写作「无线」，AND 搜索会被
  // 卡死 → 再降级试「品牌 + 首个型号词」，由选品的放宽档兜住型号不全的标题
  if (parts.length > 1) {
    push(build([cn, parts[0]].filter(Boolean)));
    push(build([en, parts[0]].filter(Boolean)));
  }
  return { primary, forms };
}

function parseSales(s) {
  const t = String(s || "");
  const wan = t.match(/([\d.]+)\s*万/);
  if (wan) return Math.round(parseFloat(wan[1]) * 10000);
  const n = t.match(/(\d+)/);
  return n ? parseInt(n[1], 10) : 0;
}

function titleText(it) {
  return norm(it?.item_basic_info?.title || it?.item_basic_info?.short_title || "");
}

function pickTaobao(list, tok) {
  const pool = (list || []).filter((it) => {
    const info = it.item_basic_info || {};
    const cat = norm((info.category_name || "") + (info.level_one_category_name || ""));
    const title = titleText(it);
    if (ACCESSORY_WORDS.some((w) => cat.includes(w) || title.includes(w))) return false;
    return cat.includes("鼠标") || title.includes("鼠标");
  });
  // 两级匹配：先严格（全部型号词命中），放宽（至少一半型号词命中且品牌对）
  const strict = pool.filter((it) => {
    const title = titleText(it);
    return (!tok.bt.length || tok.bt.some((t) => title.includes(t)))
      && tok.mt.every((t) => tokenHit(title, t))
      && tok.vt.every((t) => tokenHit(title, t));
  });
  const relaxed = pool.filter((it) => {
    const title = titleText(it);
    const need = tok.mt.length + tok.vt.length;
    if (!need) return !!tok.bt.length;
    const hits = tok.mt.concat(tok.vt).filter((t) => tokenHit(title, t)).length;
    return (!tok.bt.length || tok.bt.some((t) => title.includes(t))) && hits * 2 >= need;
  });
  const candidates = strict.length ? strict : relaxed;
  if (!candidates.length) return null;
  candidates.sort((a, b) => parseSales(b?.item_basic_info?.annual_vol) - parseSales(a?.item_basic_info?.annual_vol));
  const it = candidates[0];
  const click = it?.publish_info?.click_url;
  if (!click) return null;
  return {
    platform: "taobao",
    title: it?.item_basic_info?.title || "",
    price: it?.price_promotion_info?.final_promotion_price || "",
    sales: it?.item_basic_info?.annual_vol || "",
    url: httpsUrl(click),
    image: httpsUrl(it?.item_basic_info?.white_image || it?.item_basic_info?.pict_url || ""),
  };
}

function pickPdd(list, tok) {
  return (list || []).filter((it) => {
    const title = norm(it?.goods_name || "");
    if (ACCESSORY_WORDS.some((w) => title.includes(w))) return false;
    if (!title.includes("鼠标")) return false;
    if (tok.bt.length && !tok.bt.some((t) => title.includes(t))) return false;
    return tok.mt.every((t) => title.includes(t)) && tok.vt.every((t) => title.includes(t));
  })[0] || null;
}

// ── 平台调用 ──────────────────────────────────────────────

async function formPost(url, params) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "affiliate-links/1.0" },
    body: new URLSearchParams(params).toString(),
  });
  return res.json();
}

async function taobaoSearchOnce(env, q, tok) {
  try {
    const p = {
      method: "taobao.tbk.dg.material.optional.upgrade",
      app_key: env.TB_APP_KEY,
      timestamp: tbTimestamp(),
      v: "2.0",
      format: "json",
      sign_method: "hmac-sha256",
      adzone_id: env.TB_ADZONE_ID,
      q,
      page_size: "10",
    };
    p.sign = await tbSign(env.TB_APP_SECRET, p);
    const r = await formPost(TB_GATEWAY, p);
    if (r.error_response) {
      const e = r.error_response;
      return { platform: "taobao", miss: true, reason: "api_error", msg: String(e.sub_msg || e.msg || e.code || "").slice(0, 100) };
    }
    const list = r?.tbk_dg_material_optional_upgrade_response?.result_list?.map_data || [];
    return pickTaobao(list, tok) || { platform: "taobao", miss: true, reason: "no_match" };
  } catch (e) {
    return { platform: "taobao", miss: true, reason: "exception", msg: String(e).slice(0, 100) };
  }
}

// 两级查询：全词形态失败（含淘宝对零结果回 api_error「无结果」）后，
// 用纯中文品牌词形态重试一次
async function taobaoSearch(env, item, tok) {
  const { primary, forms } = queryForms(item);
  if (!primary) return { platform: "taobao", miss: true, reason: "no_query" };
  // 无官方渠道品牌不出链（第三方代购店不挂）
  const nb = norm(item.brand || "");
  if (NO_LINK_BRANDS.has(nb)) {
    return { platform: "taobao", miss: true, reason: "no_official_store" };
  }
  const first = await taobaoSearchOnce(env, primary, tok);
  if (!first.miss) return first;
  for (const q of forms) {
    const alt = await taobaoSearchOnce(env, q, tok);
    if (!alt.miss) return alt;
  }
  return first.reason === "api_error" ? first : { platform: "taobao", miss: true, reason: "no_match" };
}

async function pddSearch(env, q, tok) {
  try {
    const biz = { keyword: q, page_size: 10, pid: env.PDD_PID };
    const p = { type: "pdd.ddk.goods.search", client_id: env.PDD_CLIENT_ID, timestamp: String(Math.floor(Date.now() / 1000)), data_type: "JSON" };
    p.param_json = JSON.stringify(biz);
    p.sign = md5(env.PDD_CLIENT_SECRET + Object.keys(p).sort().map((k) => k + p[k]).join("") + env.PDD_CLIENT_SECRET).toUpperCase();
    const r = await formPost(PDD_GATEWAY, p);
    if (r.error_response) {
      const e = r.error_response;
      return { platform: "pdd", miss: true, reason: "api_error", msg: String(e.error_msg || e.msg || "").slice(0, 100) };
    }
    const root = r?.goods_search_response || {};
    const list = root?.goods_list || root?.goods_search_list_get_response?.list || [];
    const hit = pickPdd(list, tok);
    if (!hit) return { platform: "pdd", miss: true, reason: "no_match" };
    const link = await pddGoodsLink(env, hit.goods_sign).catch(() => null);
    return {
      platform: "pdd",
      title: hit.goods_name || "",
      price: String((hit.min_group_price || 0) / 100),
      sales: parseSales(hit.sales_tip ? String(hit.sales_tip) : "") || undefined,
      url: link || undefined,
      ...(link ? {} : { miss: true, reason: "no_link_api" }),
    };
  } catch (e) {
    return { platform: "pdd", miss: true, reason: "exception", msg: String(e).slice(0, 100) };
  }
}

async function pddGoodsLink(env, goodsSign) {
  if (!goodsSign) return null;
  const biz = { goods_sign_list: [goodsSign], pid: env.PDD_PID };
  const p = { type: "pdd.ddk.goods.prom.url.generate", client_id: env.PDD_CLIENT_ID, timestamp: String(Math.floor(Date.now() / 1000)), data_type: "JSON" };
  p.param_json = JSON.stringify(biz);
  p.sign = md5(env.PDD_CLIENT_SECRET + Object.keys(p).sort().map((k) => k + p[k]).join("") + env.PDD_CLIENT_SECRET).toUpperCase();
  const r = await formPost(PDD_GATEWAY, p);
  const body = r?.goods_prom_url_generate_response || {};
  return body.short_url || body.url || null;
}

// ── 缓存与入口 ──────────────────────────────────────────────

async function cached(env, platform, q, fn) {
  const key = "v6:" + platform + ":" + q;
  const now = Date.now();
  const mem = memCache.get(key);
  if (mem && mem.until > now) return mem.val;
  if (mem) memCache.delete(key);
  try {
    const hit = await env.AFFILIATE_CACHE.get(key);
    if (hit) {
      const val = JSON.parse(hit);
      memCache.set(key, { val, until: now + 300_000 });
      return val;
    }
  } catch {}
  const val = await fn();
  // 内存层一律记录（api_error 2 分钟、其余 5 分钟），同 isolate 重复查询零 KV 开销
  memCache.set(key, { val, until: now + (val?.reason === "api_error" ? 120_000 : 300_000) });
  if (memCache.size > 1000) memCache.clear();
  try {
    // 命中缓存 7 天；no_match 24h；api_error（多为淘宝瞬时限流）**不写 KV**——
    // 2 分钟就过期，写了没收益，风暴期每 2 分钟重烧一次写入额度
    // （09-11 打爆 KV 每日 1000 put 的主凶）。
    if (val && !val.miss) {
      await env.AFFILIATE_CACHE.put(key, JSON.stringify(val), { expirationTtl: 7 * 86400 });
    } else if (val?.reason !== "api_error") {
      await env.AFFILIATE_CACHE.put(key, JSON.stringify(val), { expirationTtl: 86400 });
    }
  } catch {
    // KV 每日写入额度打爆时静默失败（2026-09-11 批测踩过）——计数暴露到
    // /health，别再瞎着跑。
    kvWriteErrors++;
  }
  return val;
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Access-Control-Allow-Origin": "*" },
  });
}

export { md5 };

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "POST, GET, OPTIONS", "Access-Control-Allow-Headers": "Content-Type, x-ac-token" } });
    }
    if (url.pathname === "/health") {
      return json({ ok: true, tb: !!env.TB_APP_KEY, pdd: !!env.PDD_CLIENT_ID, kv: !!env.AFFILIATE_CACHE, kv_write_errors: kvWriteErrors });
    }
    if (url.pathname === "/debug" && request.headers.get("x-ac-token") === env.AC_SHARED_TOKEN) {
      const q = url.searchParams.get("q") || "罗技 G304";
      const p = {
        method: "taobao.tbk.dg.material.optional.upgrade",
        app_key: env.TB_APP_KEY,
        timestamp: tbTimestamp(),
        v: "2.0",
        format: "json",
        sign_method: "hmac-sha256",
        adzone_id: env.TB_ADZONE_ID,
        q,
        page_size: "3",
      };
      p.sign = await tbSign(env.TB_APP_SECRET, p);
      const r = await formPost(TB_GATEWAY, p);
      const list = r?.tbk_dg_material_optional_upgrade_response?.result_list?.map_data || [];
      const first = list[0] || {};
      return json({ total: list.length, top_keys: Object.keys(r), basic_keys: Object.keys(first.item_basic_info || {}), title: first?.item_basic_info?.title ?? null, sample: JSON.stringify(first).slice(0, 700) });
    }
    if (url.pathname === "/links" && request.method === "POST") {
      if (!env.AC_SHARED_TOKEN || request.headers.get("x-ac-token") !== env.AC_SHARED_TOKEN) {
        return json({ error: "unauthorized" }, 401);
      }
      let body;
      try { body = await request.json(); } catch { return json({ error: "bad_json" }, 400); }
      const items = Array.isArray(body?.items) ? body.items.filter(Boolean).slice(0, 6) : null;
      if (!items) return json({ error: "items_required" }, 400);
      const results = await Promise.all(items.map(async (it) => {
        const q = searchQuery(it);
        if (!q) return { q: "", taobao: null, pdd: null };
        const tok = tokensFor(it.q ? { brand: "", model: q } : it);
        const noLink = NO_LINK_BRANDS.has(norm(it.brand || ""));
        // 官方直签品牌：走品牌官方店（ref 归属），不走淘宝联盟
        if (DIRECT_AFFILIATES[norm(it.brand || "")]) {
          const direct = await cached(env, "direct", norm(q), () => directAffiliateSearch(it, tok));
          return { q, direct, taobao: null, pdd: null };
        }
        return {
          q,
          taobao: noLink
            ? { platform: "taobao", miss: true, reason: "no_official_store" }
            : await cached(env, "tb", norm(q), () => taobaoSearch(env, it, tok)),
          pdd: noLink
            ? { platform: "pdd", miss: true, reason: "no_official_store" }
            : await cached(env, "pdd", norm(q), () => pddSearch(env, q, tok)),
        };
      }));
      return json({ results });
    }
    return json({ error: "not_found" }, 404);
  },
};
