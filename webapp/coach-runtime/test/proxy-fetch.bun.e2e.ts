// Bun 专属 e2e（不进 node --test 的 *.test.ts glob）：验证 compile 场景的关键行为——
// 安装 proxy-fetch 后，https 出站请求对指定代理发起 CONNECT，环回请求零代理。
// 跑法：bun run test/proxy-fetch.bun.e2e.ts（bun test 被禁用——单进程污染 env）。
// 升级 Bun 版本后须重跑本文件（init.proxy 是 Bun 专属 API）。
import assert from "node:assert/strict";
import * as net from "node:net";
import * as http from "node:http";

if ((globalThis as { Bun?: unknown }).Bun === undefined) {
  console.log("SKIP: bun-only e2e (run with `bun run test/proxy-fetch.bun.e2e.ts`)");
  process.exit(0);
}

const { installProxyFetch } = await import("../src/proxy-fetch.ts");

// 1) 假代理：记录 CONNECT 目标，随即销毁连接（fetch 侧会失败，无关紧要）
const connectTargets: string[] = [];
const proxy = net.createServer((socket) => {
  socket.once("data", (chunk: Buffer) => {
    const firstLine = chunk.toString("utf8").split("\r\n")[0] ?? "";
    connectTargets.push(firstLine);
    socket.destroy();
  });
  socket.on("error", () => {});
});
await new Promise<void>((resolve) => proxy.listen(0, "127.0.0.1", resolve));
const proxyPort = (proxy.address() as net.AddressInfo).port;

// 2) 环回真服务：loopback 必须直连可达
const local = http.createServer((_req, res) => {
  res.end("loopback-ok");
});
await new Promise<void>((resolve) => local.listen(0, "127.0.0.1", resolve));
const localPort = (local.address() as net.AddressInfo).port;

installProxyFetch({ env: { HTTPS_PROXY: `http://127.0.0.1:${proxyPort}`, HTTP_PROXY: `http://127.0.0.1:${proxyPort}` } });

// 3) https 出站：wrapper 应对假代理发 CONNECT（fetch 失败可容忍）
let outboundFailed = false;
try {
  await fetch("https://proxy-e2e-target.invalid/x", { signal: AbortSignal.timeout(3_000) });
} catch {
  outboundFailed = true;
}
assert.ok(outboundFailed, "outbound via dead fake proxy should fail");
assert.equal(connectTargets.length, 1, `expected exactly one CONNECT, got: ${JSON.stringify(connectTargets)}`);
assert.ok(
  connectTargets[0]!.startsWith("CONNECT proxy-e2e-target.invalid:443"),
  `unexpected CONNECT line: ${connectTargets[0]}`,
);

// 4) 环回目标：硬排除，直连成功且零 CONNECT
const response = await fetch(`http://127.0.0.1:${localPort}/probe`);
assert.equal(await response.text(), "loopback-ok");
assert.equal(connectTargets.length, 1, "loopback request must not hit the proxy");

proxy.close();
local.close();
console.log("PASS: CONNECT reached fake proxy for https target; loopback stayed direct");
process.exit(0);
