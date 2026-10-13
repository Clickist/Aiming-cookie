// Coach 模型请求的 Anthropic 思考协议自愈层（2026-10-12 野外 haiku-5.5 案：
// 报障包 20261012-192831-5e704b84，BYOK 直连选 claude-haiku-5-5 必 400）。
// 背景：Anthropic 新模型家族废弃 thinking.type.enabled（要求 adaptive +
// output_config.effort），pi 内建目录按模型打 forceAdaptiveThinking 标——
// 目录未收录的新模型走老协议 enabled 必被拒。自适应重试不依赖目录更新，
// 未来新模型同样兜住（业界同行 Cline/Aider 靠发版追目录，空窗期用户必炸）。
//
// 形态：包装 fetch（与 net-resilience 同层同先例），收到 400 且响应体命中
// 「thinking 不被该模型支持」特征、请求体含 thinking.type=enabled 时，把
// 请求体手术成 adaptive 形态（保留 display、丢弃 budget_tokens——adaptive
// 语义即模型自定思考量）后重发一次。请求从未被服务端接受（400 拒收、
// usage=0），重发无重复计费。已 adaptive 的体 rewrite 返回 null，天然无循环。
// 自愈触发落一行 [adaptive-fallback] 诊断到 coach-error.log，随报障包
// coachErrorLogTail 带出（降级可观测合同）。
import { appendFileSync } from "node:fs";
import { join } from "node:path";
import { getDataRoot } from "./app-data.ts";
import { coachFetch, type FetchLike } from "./net-resilience.ts";

/** 上游 400 响应体的「该模型不支持此 thinking 形态」特征（报错文案即说明书）。 */
const THINKING_UNSUPPORTED_PATTERN = /thinking[\s\S]{0,60}not supported for this model/i;

function defaultWriteLog(line: string): void {
  try {
    appendFileSync(join(getDataRoot(), "coach-error.log"), line + "\n", "utf8");
  } catch {
    // Best-effort diagnostic; never mask the request flow.
  }
}

export interface AdaptiveThinkingFetchDeps {
  writeLog?: (line: string) => void;
}

/**
 * 请求体手术：thinking:{type:"enabled",budget_tokens,display} →
 * thinking:{type:"adaptive",display}。其余字段原样保留。返回 null = 不适用
 * （非 JSON、无 thinking、或已是 adaptive/disabled——幂等，防重发循环）。
 */
export function rewriteThinkingEnabledBody(body: string): string | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null) return null;
  const record = parsed as Record<string, unknown>;
  const thinking = record.thinking;
  if (typeof thinking !== "object" || thinking === null) return null;
  const t = thinking as Record<string, unknown>;
  if (t.type !== "enabled") return null;
  const next: Record<string, unknown> = { type: "adaptive" };
  if (typeof t.display === "string") next.display = t.display;
  return JSON.stringify({ ...record, thinking: next });
}

/** 400 响应是否命中「该模型不支持此 thinking 形态」特征。 */
export function isThinkingUnsupportedResponse(status: number, bodyText: string): boolean {
  return status === 400 && THINKING_UNSUPPORTED_PATTERN.test(bodyText);
}

/**
 * 包装 base fetch：400 命中特征且请求体可手术时，改写 thinking 后重发一次，
 * 返回重发响应。重发直接走 base（重写后的体 rewrite=null，天然无二次自愈）。
 */
export function createAdaptiveThinkingFetch(
  base: FetchLike,
  deps: AdaptiveThinkingFetchDeps = {},
): FetchLike {
  const writeLog = deps.writeLog ?? defaultWriteLog;
  return (async (input: string | URL, init?: RequestInit) => {
    const res = await base(input, init);
    if (res.status !== 400 || typeof init?.body !== "string" || !init.body) return res;
    let upstreamText: string;
    try {
      upstreamText = await res.clone().text();
    } catch {
      return res;
    }
    if (!isThinkingUnsupportedResponse(res.status, upstreamText)) return res;
    const rewritten = rewriteThinkingEnabledBody(init.body);
    if (rewritten === null) return res;

    let model = "unknown";
    try {
      model = String((JSON.parse(init.body) as Record<string, unknown>).model ?? "unknown");
    } catch {
      // model 仅用于诊断行，解析失败不影响自愈。
    }
    writeLog(
      `${new Date().toISOString()} [adaptive-fallback] model=${model} upstream=${upstreamText.slice(0, 200)} action=retry_with_adaptive_thinking`,
    );
    return base(input, { ...init, body: rewritten });
  }) as FetchLike;
}

let singleton: FetchLike | null = null;

/** 生产入口：自愈层包在韧性 fetch 之外（协议自愈在其网络救援之后）。 */
export function adaptiveThinkingFetch(): FetchLike {
  if (!singleton) singleton = createAdaptiveThinkingFetch(coachFetch());
  return singleton;
}
