// Coach 模型请求的网络韧性层（2026-10-08 野外"涛"案立项：8 天 26 次
// "Connection error." 零到达，快失败 1.6s，同期 WebView2/其他软件全通）。
// 形态：包装 models.streamSimple 注入自定义 fetch（pi StreamOptions.fetch
// 公开合同，不动 third_party/pi）。首选 fetch 连接级失败（响应头未返回=
// 服务端零处理，重发幂等）时：
//   1) 落一行 [netprobe] 诊断 JSON 到 coach-error.log（随报障包
//      coachErrorLogTail 自动带出，零 schema 改动）；
//   2) 用 node:http(s) + dns.lookup(family:4) 强制 IPv4 重发同请求，成功则
//      本轮对话无感继续。验证闸实测：Bun 1.3.14 的 node:http 忽略 family
//      选项，必须显式 lookup 拿 IP 再以 Host/servername 保持域名。
// 救援也失败时原样 rethrow 首选错误——分类层输入与旧路径逐字节一致。
import { appendFileSync } from "node:fs";
import { promises as dnsPromises } from "node:dns";
import * as http from "node:http";
import * as https from "node:https";
import * as net from "node:net";
import { Readable } from "node:stream";
import { join } from "node:path";
import { getDataRoot } from "./app-data.ts";

export type FetchLike = typeof globalThis.fetch;

const STICKY_TTL_MS = 10 * 60 * 1000;
const PROBE_THROTTLE_MS = 30 * 1000;

export interface ResilientFetchDeps {
  /** 首选传输（默认 globalThis.fetch）。 */
  primary?: FetchLike;
  /** 强制 IPv4 传输（默认 node:http(s)+dns.lookup(family:4) 实现）。 */
  forced4?: (input: string | URL, init?: RequestInit) => Promise<Response>;
  dns4?: (host: string) => Promise<string[]>;
  dns6?: (host: string) => Promise<string[]>;
  tcpProbe?: (host: string, port: number, timeoutMs: number) => Promise<"open" | "refused" | "timeout" | "error">;
  /** 收到完整一行（含 ISO 前缀）。默认追加 coach-error.log。 */
  writeLog?: (line: string) => void;
  now?: () => number;
}

// ── 连接级失败判定 ──────────────────────────────────────────────────────────
// 只认"连接从未建立"族（服务端零处理，重发幂等）。不含 terminated（连接
// 建立后中断，是 pi 流内重试/owner 领地）。Bun 原生文案 "Unable to connect."
// 与 OpenAI SDK 包装后的 "Connection error." 都在案。

const CONN_ERROR_CODES = new Set([
  "ConnectionRefused", "ConnectionReset", "ConnectionClosed", "ConnectionTimedOut",
  "ECONNREFUSED", "ECONNRESET", "ECONNABORTED", "EHOSTUNREACH", "ENETUNREACH",
  "ENOTFOUND", "EAI_AGAIN", "ETIMEDOUT", "UND_ERR_CONNECT_TIMEOUT",
]);
const CONN_ERROR_MESSAGE =
  /unable to connect|connection error|connection refused|connection reset|socket hang up|econn(refused|reset|aborted)|ehostunreach|enetunreach|enotfound|eai_again|etimedout|connect timeout|fetch failed|network error/;

export function isConnectionLevelError(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  const code = (error as Error & { code?: unknown }).code;
  if (typeof code === "string" && CONN_ERROR_CODES.has(code)) return true;
  return CONN_ERROR_MESSAGE.test(error.message.toLowerCase());
}

// ── 默认依赖实现 ────────────────────────────────────────────────────────────

async function dnsList(host: string, family: 4 | 6): Promise<string[]> {
  try {
    const all = await dnsPromises.lookup(host, { family, all: true });
    return all.slice(0, 2).map((r) => r.address);
  } catch {
    return [];
  }
}

function tcpProbeDefault(host: string, port: number, timeoutMs: number): Promise<"open" | "refused" | "timeout" | "error"> {
  return new Promise((resolve) => {
    const socket = net.connect({ host, port });
    const done = (v: "open" | "refused" | "timeout" | "error") => {
      socket.removeAllListeners();
      socket.destroy();
      resolve(v);
    };
    socket.setTimeout(timeoutMs, () => done("timeout"));
    socket.on("connect", () => done("open"));
    socket.on("error", (e: NodeJS.ErrnoException) =>
      done(e.code === "ECONNREFUSED" ? "refused" : e.code === "ETIMEDOUT" ? "timeout" : "error"));
  });
}

function runtimeTag(): string {
  const bun = (globalThis as { Bun?: { version?: string } }).Bun;
  return bun?.version ? `bun-${bun.version}` : "node";
}

function defaultWriteLog(line: string): void {
  try {
    appendFileSync(join(getDataRoot(), "coach-error.log"), line + "\n", "utf8");
  } catch {
    // best-effort：日志写不进去不能影响对话主链路
  }
}

// ── forced4 默认传输 ────────────────────────────────────────────────────────

type PlainHeaders = Record<string, string | string[] | undefined>;

function toPlainHeaders(init?: RequestInit): PlainHeaders {
  const raw = init?.headers;
  if (!raw) return {};
  if (Array.isArray(raw)) {
    const out: PlainHeaders = {};
    for (const [k, v] of raw) out[k] = v;
    return out;
  }
  if (typeof (raw as Headers).forEach === "function") {
    const out: PlainHeaders = {};
    (raw as Headers).forEach((v, k) => { out[k] = v; });
    return out;
  }
  return raw as PlainHeaders;
}

export function forced4FetchDefault(input: string | URL, init?: RequestInit): Promise<Response> {
  const url = new URL(input.toString());
  const isHttps = url.protocol === "https:";
  const transport = isHttps ? https : http;
  return dnsPromises.lookup(url.hostname, { family: 4 }).then(({ address }) =>
    new Promise<Response>((resolve, reject) => {
      const headers: PlainHeaders = toPlainHeaders(init);
      const hasHost = Object.keys(headers).some((k) => k.toLowerCase() === "host");
      if (!hasHost) {
        // HTTP/1.1：非默认端口 Host 须带端口（默认 80/443 时省略）。
        const defaultPort = isHttps ? "443" : "80";
        headers["Host"] = url.port && url.port !== defaultPort ? `${url.hostname}:${url.port}` : url.hostname;
      }
      const bodyStr = typeof init?.body === "string" ? init.body : undefined;
      if (bodyStr !== undefined && Object.keys(headers).every((k) => k.toLowerCase() !== "content-length")) {
        headers["Content-Length"] = String(Buffer.byteLength(bodyStr));
      }
      const req = transport.request(
        {
          host: address,
          port: url.port ? Number(url.port) : isHttps ? 443 : 80,
          path: url.pathname + url.search,
          method: init?.method ?? "GET",
          headers,
          // https：连接 IP 但 SNI/证书校验保持原域名（node 标准语义）。
          ...(isHttps ? { servername: url.hostname } : {}),
        },
        (res) => {
          const web = Readable.toWeb(res) as unknown as ReadableStream<Uint8Array>;
          resolve(new Response(web, {
            status: res.statusCode ?? 502,
            statusText: res.statusMessage,
            headers: res.headers as Record<string, string>,
          }));
        },
      );
      req.on("error", reject);
      if (init?.signal) {
        if (init.signal.aborted) {
          req.destroy();
          reject(new Error("The operation was aborted"));
          return;
        }
        init.signal.addEventListener("abort", () => req.destroy(), { once: true });
      }
      if (bodyStr !== undefined) req.write(bodyStr);
      req.end();
    }),
  );
}

// ── netprobe 诊断 ───────────────────────────────────────────────────────────

async function collectNetprobe(
  host: string,
  port: number,
  phase: "rescue_ok" | "rescue_fail",
  primaryError: string,
  forced4Result: string,
  deps: ResilientFetchDeps,
  startedAt: number,
): Promise<Record<string, unknown>> {
  const [dns4, dns6] = await Promise.all([(deps.dns4 ?? ((h) => dnsList(h, 4)))(host), (deps.dns6 ?? ((h) => dnsList(h, 6)))(host)]);
  const probe = deps.tcpProbe ?? tcpProbeDefault;
  const tcp4 = dns4.length ? await probe(dns4[0], port, 2000) : "skipped";
  const tcp6 = dns6.length ? await probe(dns6[0], port, 2000) : "skipped";
  const env = process.env as Record<string, string | undefined>;
  return {
    v: 1,
    runtime: runtimeTag(),
    host,
    port,
    phase,
    primary_error: primaryError,
    dns4,
    dns6,
    tcp4,
    tcp6,
    forced4: forced4Result,
    proxy_env: {
      HTTP_PROXY: Boolean(env.HTTP_PROXY ?? env.http_proxy),
      HTTPS_PROXY: Boolean(env.HTTPS_PROXY ?? env.https_proxy),
      ALL_PROXY: Boolean(env.ALL_PROXY ?? env.all_proxy),
      NO_PROXY: Boolean(env.NO_PROXY ?? env.no_proxy),
    },
    elapsed_ms: Date.now() - startedAt,
  };
}

// ── 韧性 fetch ──────────────────────────────────────────────────────────────

export function createResilientFetch(deps: ResilientFetchDeps = {}): FetchLike {
  const primary = deps.primary ?? globalThis.fetch.bind(globalThis);
  const forced4 = deps.forced4 ?? forced4FetchDefault;
  const now = deps.now ?? Date.now;
  const writeLog = deps.writeLog ?? defaultWriteLog;
  const state = { stickyUntil: 0 };
  const probedAt = new Map<string, number>();

  const shouldProbe = (host: string): boolean => {
    const last = probedAt.get(host) ?? Number.NEGATIVE_INFINITY;
    if (now() - last < PROBE_THROTTLE_MS) return false;
    probedAt.set(host, now());
    return true;
  };

  const emit = async (
    host: string,
    port: number,
    phase: "rescue_ok" | "rescue_fail" | "sticky",
    primaryError: string,
    forced4Result: string,
    startedAt: number,
  ): Promise<void> => {
    if (!shouldProbe(host)) return;
    const record =
      phase === "sticky"
        ? { v: 1, runtime: runtimeTag(), host, port, phase, primary_error: "", dns4: [], dns6: [], tcp4: "skipped", tcp6: "skipped", forced4: forced4Result, proxy_env: {}, elapsed_ms: Date.now() - startedAt }
        : await collectNetprobe(host, port, phase, primaryError, forced4Result, deps, startedAt);
    if (phase === "rescue_ok") record.via = "forced4";
    writeLog(`${new Date().toISOString()} [netprobe] ${JSON.stringify(record)}`);
  };

  return async (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input));
    const httpUrl = url.protocol === "http:" || url.protocol === "https:";
    const startedAt = now();

    // 粘性窗口：本轮进程内已知首选传输死、forced4 活（rescue_ok 置 10 分钟）。
    // 窗口内跳过注定失败的首选，直接 forced4（现场首选每跳快败 1.6s）。
    if (httpUrl && now() < state.stickyUntil) {
      try {
        const res = await forced4(input, init);
        void emit(url.hostname, Number(url.port) || (url.protocol === "https:" ? 443 : 80), "sticky", "", String(res.status), startedAt);
        return res;
      } catch (e) {
        void emit(url.hostname, Number(url.port) || (url.protocol === "https:" ? 443 : 80), "rescue_fail", "", e instanceof Error ? (e as NodeJS.ErrnoException).code ?? e.message : String(e), startedAt);
        state.stickyUntil = 0;
        // 粘性路径没有首选错误可 rethrow，抛当前错误（同为连接级语义）。
        throw e;
      }
    }

    try {
      return await primary(input, init);
    } catch (primaryError) {
      if (!(primaryError instanceof Error)) throw primaryError;
      if (init?.signal?.aborted) throw primaryError;
      const body = init?.body;
      if (body != null && typeof body !== "string") throw primaryError; // 流式 body 不重发
      if (!httpUrl || !isConnectionLevelError(primaryError)) throw primaryError;

      const port = Number(url.port) || (url.protocol === "https:" ? 443 : 80);
      // 救援与诊断都在后台 settle，不阻塞主路径返回（label 若同步等会拖慢
      // forced4 超时型失败下的响应返回）。
      const attempt = forced4(input, init);
      void attempt.then(
        async (res) => {
          state.stickyUntil = now() + STICKY_TTL_MS;
          await emit(url.hostname, port, "rescue_ok", primaryError.message, String(res.status), startedAt);
        },
        async (e) => {
          await emit(url.hostname, port, "rescue_fail", primaryError.message,
            e instanceof Error ? String((e as NodeJS.ErrnoException).code ?? e.message).slice(0, 80) : String(e), startedAt);
        },
      );
      try {
        return await attempt;
      } catch {
        // 救援也失败：原样 rethrow 首选错误，保证分类层输入不变。
        throw primaryError;
      }
    }
  };
}

// ── 单例与 models 包装 ──────────────────────────────────────────────────────

let singleton: FetchLike | null = null;

function coachFetch(): FetchLike {
  if (!singleton) singleton = createResilientFetch();
  return singleton;
}

/**
 * 包装 PiModels：streamSimple 注入韧性 fetch（pi StreamOptions.fetch 公开
 * 合同）。上层已显式传入 options.fetch 时不覆盖（尊重测试/特殊注入）。
 * 其余 Models 方法原样透传（与 turn.ts 现有 streamFn 包装同构）。
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function wrapModelsWithResilientFetch(models: any): any {
  return Object.assign(Object.create(models), {
    streamSimple: (model: unknown, context: unknown, options?: { fetch?: FetchLike } & Record<string, unknown>) =>
      (models as { streamSimple: (m: unknown, c: unknown, o?: unknown) => unknown }).streamSimple(model, context, {
        ...(options ?? {}),
        fetch: options?.fetch ?? coachFetch(),
      }),
  });
}
