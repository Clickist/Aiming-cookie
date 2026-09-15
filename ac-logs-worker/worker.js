// Aiming Cookie 诊断包接收 Worker（logs.aimingcookie.com）。
// 取代部署在洛杉矶机的 ac_logs_server.py（该版本保留在 ../ac-logs-server/ 作退役备份）。
// 存储：R2 桶 ac-logs（桶上配了 14 天自动删除生命周期）；台账/去重/限频：D1 库 ac-logs。
// 响应形状与服务器版逐字节兼容（{id, bytes} / {id, duplicate:true} / 429 / 503），客户端零感知。
// 防滥用语义：去重、单 IP 限次、日配额（常量见下方代码）；CF 边缘另有 20 次/10 秒兜底。

const MAX_BODY = 10 * 1024 * 1024; // 单包上限：诊断包正常 <2MB
const RATE_LIMIT_PER_HOUR = 10;    // 真实用户每天 1-2 次
const DAILY_BYTE_CAP = 200 * 1024 * 1024;

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, X-AC-Token",
  "Access-Control-Max-Age": "86400",
};

async function sha256Hex(buffer) {
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// 北京时间的 YYYYMMDD / HHMMSS / 当日零点（ms），与服务器版编号口径一致。
function beijingParts(nowMs) {
  const shifted = new Date(nowMs + 8 * 3600e3).toISOString();
  const day = shifted.slice(0, 10).replaceAll("-", "");
  const time = shifted.slice(11, 19).replaceAll(":", "");
  const dayStartMs = Math.floor((nowMs + 8 * 3600e3) / 86400e3) * 86400e3 - 8 * 3600e3;
  return { day, time, dayStartMs };
}

function reply(payload, status = 200, extra = {}) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { ...CORS, "Content-Type": "application/json; charset=utf-8", ...extra },
  });
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (req.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: { ...CORS, "Content-Length": "0" } });
    }
    if (url.pathname === "/healthz" && req.method === "GET") {
      return new Response("ok", { headers: { ...CORS, "Content-Type": "text/plain" } });
    }
    if (url.pathname !== "/upload" || req.method !== "POST") {
      return reply({ error: "not found" }, 404);
    }
    if (!env.AC_LOGS_TOKEN || req.headers.get("X-AC-Token") !== env.AC_LOGS_TOKEN) {
      return reply({ error: "forbidden" }, 403);
    }

    const declared = Number(req.headers.get("Content-Length") || "0");
    if (!(declared > 0) || declared > MAX_BODY) {
      return reply({ error: "invalid or oversized body" }, 413);
    }
    const body = await req.arrayBuffer();
    if (body.byteLength === 0 || body.byteLength > MAX_BODY) {
      return reply({ error: "invalid or oversized body" }, 413);
    }

    // 校验包体：Tauri 结构体 serde 序列化为 camelCase（schemaVersion / appVersion）
    let appVersion = "unknown";
    try {
      const bundle = JSON.parse(new TextDecoder().decode(body));
      if (!bundle || typeof bundle !== "object" || !("schemaVersion" in bundle)) throw new Error("bad");
      appVersion = String(bundle.appVersion ?? bundle.app_version ?? "unknown");
    } catch {
      return reply({ error: "body must be a capture diagnostics bundle JSON" }, 400);
    }

    const hash = await sha256Hex(body);
    const ip = req.headers.get("CF-Connecting-IP") || "unknown";
    const now = Date.now();
    const { day, time, dayStartMs } = beijingParts(now);

    // 幂等去重：同字节内容返回原编号，不重复占存储
    const dup = await env.DB.prepare("SELECT id FROM uploads WHERE hash = ?1").bind(hash).first();
    if (dup) {
      return reply({ id: dup.id, duplicate: true });
    }

    // 单 IP 限频：滑动窗口按台账计数
    const recent = await env.DB
      .prepare("SELECT COUNT(*) AS n FROM uploads WHERE ip = ?1 AND created_at > ?2")
      .bind(ip, now - 3600e3)
      .first();
    if ((recent?.n ?? 0) >= RATE_LIMIT_PER_HOUR) {
      return reply({ error: "rate limited", retry_after: 600 }, 429, { "Retry-After": "600" });
    }

    // 全局日额度：当日已收字节数 + 本包 超过 200MB 则拒绝，次日自恢复
    const today = await env.DB
      .prepare("SELECT COALESCE(SUM(bytes), 0) AS s FROM uploads WHERE created_at > ?1")
      .bind(dayStartMs)
      .first();
    if ((today?.s ?? 0) + body.byteLength > DAILY_BYTE_CAP) {
      return reply({ error: "daily quota exceeded, try tomorrow" }, 503);
    }

    // 落 R2 + 记台账（R2 同 key 写幂等，并发重复无害；台账 hash UNIQUE 兜底并发同内容）
    const id = `${day}-${time}-${hash.slice(0, 8)}`;
    const r2Key = `bundles/${day}/${id}.json`;
    await env.LOGS.put(r2Key, body);
    try {
      await env.DB
        .prepare(
          "INSERT INTO uploads (id, hash, ip, bytes, app_version, r2_key, created_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)"
        )
        .bind(id, hash, ip, body.byteLength, appVersion, r2Key, now)
        .run();
    } catch (error) {
      const raced = await env.DB.prepare("SELECT id FROM uploads WHERE hash = ?1").bind(hash).first();
      if (raced) return reply({ id: raced.id, duplicate: true });
      throw error;
    }

    console.log(`[upload] id=${id} bytes=${body.byteLength} app=${appVersion} ip=${ip}`);
    return reply({ id, bytes: body.byteLength });
  },
};
