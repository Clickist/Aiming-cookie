// Aiming Cookie 下载计数 Worker（dl.aimingcookie.com 前置）。
// zone 路由 dl.aimingcookie.com/* 把请求拦到本 Worker：R2 桶 aiming-cookie-downloads 读流原样回给
// 客户端（支持 Range 断点续传），同时向 D1 库 ac-downloads 记一行事件（GET 记，HEAD 不记）。
// 计数口径：installer 安装包 / update_check latest.json 更新检查 / sig 签名文件 / other。
// 带 Range 的续传/分段请求各记一行（has_range=1），报表按 has_range=0 为主口径；
// 计数失败不影响下载（best-effort）。客户端/落地页零改动：URL、字节、ETag、续传与 R2 直连一致。
// 回滚：删掉 zone 路由 dl.aimingcookie.com/*，流量立即回落到原有 R2 自定义域（保留未动）。

function classify(path) {
  if (/-setup\.exe$/i.test(path)) return "installer";
  if (path === "latest.json") return "update_check";
  if (/\.sig$/i.test(path)) return "sig";
  return "other";
}

function parseVersion(path) {
  // 兼容 Aiming.Cookie_0.1.10 与 Aiming_Cookie_1.2.2 两种历史命名
  const m = path.match(/(\d+\.\d+\.\d+)/);
  return m ? m[1] : null;
}

// bytes=a-b / a- / -n → R2Range；无法解析或多段返回 null（按整文件 200 处理）
function parseRange(header) {
  if (!header) return null;
  const m = /^bytes=(\d*)-(\d*)$/.exec(header.trim());
  if (!m || (m[1] === "" && m[2] === "")) return null;
  if (m[1] === "") return { suffix: Number(m[2]) };
  const offset = Number(m[1]);
  if (m[2] === "") return { offset: offset };
  const length = Number(m[2]) - offset + 1;
  return length > 0 ? { offset: offset, length: length } : null;
}

async function record(env, ev) {
  await env.DB.prepare(
    "INSERT INTO download_events (created_at, kind, path, version, has_range, country, ua) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)"
  ).bind(Date.now(), ev.kind, ev.path, ev.version, ev.hasRange, ev.country, ev.ua).run();
}

export default {
  async fetch(request, env, ctx) {
    const isHead = request.method === "HEAD";
    if (request.method !== "GET" && !isHead) {
      return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET, HEAD" } });
    }
    const key = new URL(request.url).pathname.replace(/^\/+/, "");
    if (!key) return new Response("Not Found", { status: 404 });

    const range = isHead ? null : parseRange(request.headers.get("Range"));

    let object;
    try {
      object = isHead
        ? await env.DOWNLOADS.head(key)
        : await env.DOWNLOADS.get(key, range ? { range: range } : undefined);
    } catch (e) {
      if (range) return new Response("Range Not Satisfiable", { status: 416, headers: { "Content-Range": "bytes */*" } });
      return new Response("Bad Gateway", { status: 502 });
    }
    if (!object) return new Response("Not Found", { status: 404 });

    const headers = new Headers();
    object.writeHttpMetadata(headers);
    headers.set("ETag", object.httpEtag);
    if (object.uploaded) headers.set("Last-Modified", object.uploaded.toUTCString());
    headers.set("Accept-Ranges", "bytes");
    if (!headers.has("Cache-Control")) headers.set("Cache-Control", "max-age=14400"); // 对齐 R2 自定义域默认值
    headers.set("X-AC-Downloads", "worker"); // 验收标记：确认响应来自本 Worker

    let status = 200;
    if (range && object.range) {
      const offset = object.range.offset ?? 0;
      const length = object.range.length ?? object.size - offset;
      if (offset >= object.size || length <= 0) {
        return new Response(null, { status: 416, headers: { "Content-Range": "bytes */" + object.size } });
      }
      status = 206;
      headers.set("Content-Range", "bytes " + offset + "-" + (offset + length - 1) + "/" + object.size);
      headers.set("Content-Length", String(length));
    } else {
      headers.set("Content-Length", String(object.size));
    }

    if (!isHead) {
      ctx.waitUntil(record(env, {
        kind: classify(key),
        path: key,
        version: parseVersion(key),
        hasRange: status === 206 ? 1 : 0,
        country: (request.cf && request.cf.country) || null,
        ua: (request.headers.get("User-Agent") || "").slice(0, 200) || null,
      }).catch(function () {})); // 计数失败不阻塞下载
    }

    return new Response(isHead ? null : object.body, { status: status, headers: headers });
  },
};
