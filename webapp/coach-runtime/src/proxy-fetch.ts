// issue#8：Bun --compile 产物（生产 coach-sidecar.exe）的全局 fetch 不读
// HTTPS_PROXY/HTTP_PROXY 环境变量（源码模式读取，compile 后被丢弃——已实测），
// 导致 xAI 等 OAuth 设备码请求直连墙外域名必败（GitHub issue#8）。
//
// 本模块在 sidecar 入口以 side-effect import 自安装：包装 globalThis.fetch，
// 对命中代理规则的出站请求显式注入 Bun 专属的 init.proxy（compile 产物尊重
// per-request proxy，已实测）。规则与 curl 语义对齐：
//   - 环回地址（localhost/127.0.0.0-8/::1/0.0.0.0）一律直连——sidecar 与本地
//     Python 后端/Tauri 的互访是生命线，不依赖用户设置 NO_PROXY；
//   - NO_PROXY 支持 `*`、裸主机名精确匹配、前导点后缀匹配；
//   - https 走 HTTPS_PROXY（兜底 ALL_PROXY），http 走 HTTP_PROXY（兜底 ALL_PROXY）。
// 非 Bun 运行时（开发 node+tsx）：透传不注入（node fetch 静默忽略未知 init 键，
// 无害），仅提示一行。不实现"代理失败回退直连"：静默直连是隐私泄露，大声失败。

type ProxyFetchEnv = Record<string, string | undefined>;

const LOOPBACK_HOSTNAMES = new Set(["localhost", "::1", "0.0.0.0"]);

function isLoopbackHost(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (LOOPBACK_HOSTNAMES.has(host)) return true;
  // 127.0.0.0/8 整段环回（Python 后端/Tauri 本地服务可能绑 127.x 任意地址）
  const v4 = host.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (v4) return v4[1] === "127";
  return false;
}

function normalizeProxyUrl(value: string): string {
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(value)) return value;
  return `http://${value}`;
}

function noProxyMatches(hostname: string, noProxy: string): boolean {
  for (const rawEntry of noProxy.split(",")) {
    const entry = rawEntry.trim().toLowerCase().replace(/^\./, "").replace(/^\*\./, "");
    if (entry === "") continue;
    if (entry === "*") return true;
    const host = hostname.toLowerCase();
    if (host === entry || host.endsWith(`.${entry}`)) return true;
  }
  return false;
}

/** 纯函数：该 URL 应使用的代理地址；null=直连。 */
export function resolveProxyForUrl(url: string | URL, env: ProxyFetchEnv = process.env): string | null {
  let parsed: URL;
  try {
    parsed = url instanceof URL ? url : new URL(url);
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return null;
  if (isLoopbackHost(parsed.hostname)) return null;

  const noProxy = env.NO_PROXY ?? env.no_proxy;
  if (noProxy && noProxyMatches(parsed.hostname, noProxy)) return null;

  const candidates =
    parsed.protocol === "https:"
      ? [env.HTTPS_PROXY, env.https_proxy, env.ALL_PROXY, env.all_proxy]
      : [env.HTTP_PROXY, env.http_proxy, env.ALL_PROXY, env.all_proxy];
  const value = candidates.find((candidate) => typeof candidate === "string" && candidate.trim().length > 0);
  return value ? normalizeProxyUrl(value.trim()) : null;
}

type InstallOptions = {
  fetchImpl?: typeof fetch;
  env?: ProxyFetchEnv;
  warn?: (message: string) => void;
};

let installed = false;

/** 安装代理感知包装器（每次调用重新包装；模块级幂等由文件尾 guard 负责，
 * 这里不挡——测试需用 fetchImpl/env 显式注入重复安装）。无代理 env 时零行为变化。 */
export function installProxyFetch(options: InstallOptions = {}): void {
  const original = options.fetchImpl ?? globalThis.fetch;
  const env = options.env ?? process.env;
  const isBun = (globalThis as { Bun?: unknown }).Bun !== undefined;
  const warn = options.warn ?? ((message: string) => process.stderr.write(`${message}\n`));

  if (!isBun) {
    const hasProxyEnv = [env.HTTPS_PROXY, env.https_proxy, env.HTTP_PROXY, env.http_proxy, env.ALL_PROXY, env.all_proxy]
      .some((value) => typeof value === "string" && value.trim().length > 0);
    if (hasProxyEnv) {
      warn(
        "[proxy-fetch] node development runtime does not support proxy injection; outbound fetch will connect directly",
      );
    }
    // 透传安装：保持包装器在位（行为与原件一致），Bun/node 判定以安装时为准
  }

  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    const proxyUrl = isBun ? resolveProxyForUrl(input instanceof URL ? input : String(input), env) : null;
    if (!proxyUrl) return original(input, init);
    return original(input, { ...init, proxy: proxyUrl } as RequestInit & { proxy: string });
  }) as typeof fetch;
}

// side-effect import 自安装（start-sidecar.ts 首条 import）。guard 只挡模块级
// 安装；测试可再显式调 installProxyFetch({ fetchImpl, env }) 注入观察。
if (!installed) {
  installed = true;
  installProxyFetch();
}
