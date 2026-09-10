// Coach 会话自动命名（点点 0910 拍板路线 B）：run 成功后 fire-and-forget 的
// 一次便宜 LLM 调用，生成 ≤24 字短标题写入 meta.title。调用形态照抄 pi
// compaction 的"独立请求"先例（不写 prompt cache）；业界共识（LibreChat /
// open-webui）：一次会话只自动命名一次、超时短、失败静默回退首句截断降级、
// 用户手动命名（title_source="user"）永不被覆盖。
import { readConversationMeta, writeConversationMeta } from "./session-repo.ts";
import type { ResolvedProviderModel } from "./provider-models.ts";

/** 标题专用便宜快速档；provider 侧找不到该 id 时回退主会话模型。 */
const TITLE_MODEL_ID = "deepseek-v4-flash";
const TITLE_TIMEOUT_MS = 8000;
const TITLE_MAX_TOKENS = 32;
const TITLE_MAX_CHARS = 24;

const TITLE_SYSTEM_PROMPT =
  "你是会话标题生成器。根据训练教练与学员的一段对话开头，输出一个简短标题。" +
  "要求：12 个字以内、名词短语；使用对话的主要语言；不要引号、句号、emoji、" +
  "不要「关于」「对话」等前缀；概括核心话题，准确优先；只输出标题本身。";

export type SessionTitleProviders = {
  models: ResolvedProviderModel["models"];
  fallbackModel: ResolvedProviderModel["model"];
};

/** 清洗模型输出：取首行、去引号括号与前缀、去尾标点、截断；空结果放弃。 */
export function sanitizeSessionTitle(raw: string): string | null {
  const firstLine = raw.trim().split(/\r?\n/, 1)[0] ?? "";
  const cleaned = firstLine
    .replace(/^[「『"'《<（(【[]+/, "")
    .replace(/[」』"'》>）)】\]]+$/, "")
    .replace(/^(?:关于|对话|标题)\s*[:：]?\s*/, "")
    .replace(/[。．.!！?？～~、;；]+$/, "")
    .trim()
    .slice(0, TITLE_MAX_CHARS);
  return cleaned || null;
}

function outcomeText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((block) =>
        block && typeof block === "object" && typeof (block as { text?: unknown }).text === "string"
          ? (block as { text: string }).text
          : "",
      )
      .join("");
  }
  return "";
}

/**
 * run 成功后的自动命名入口。任何失败都静默吞掉（侧栏继续显示首句截断降级
 * 标题）；失败不写 title_source，下一次 run 成功会自然重试。调用方
 * fire-and-forget，绝不阻塞 run 终态。
 */
export async function maybeAutoTitleSession(
  threadId: number,
  userText: string,
  replyText: string,
  providers: SessionTitleProviders,
): Promise<void> {
  try {
    const meta = readConversationMeta(threadId);
    // 守卫：手动命名永不被覆盖；自动命名只发生一次。
    if (meta.title_source === "user" || meta.title_source === "auto") return;
    if (!userText.trim()) return;

    const titleModel =
      providers.models.getModels().find((candidate) => candidate.id === TITLE_MODEL_ID) ??
      providers.fallbackModel;
    if (!(await providers.models.getAuth(titleModel))) return;

    const userPrompt =
      `学员：${userText.slice(0, 600)}\n教练：${replyText.slice(0, 400)}\n\n标题：`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), TITLE_TIMEOUT_MS);
    timer.unref?.();
    let raw = "";
    try {
      const stream = providers.models.streamSimple(
        titleModel,
        {
          systemPrompt: TITLE_SYSTEM_PROMPT,
          messages: [{ role: "user", content: userPrompt, timestamp: Date.now() }],
        },
        { signal: controller.signal, maxTokens: TITLE_MAX_TOKENS },
      );
      const outcome = (await stream.result()) as {
        content?: unknown;
        stopReason?: string;
        errorMessage?: string;
      };
      if (outcome.stopReason === "aborted" || outcome.stopReason === "error") return;
      raw = outcomeText(outcome.content);
    } finally {
      clearTimeout(timer);
    }

    const title = sanitizeSessionTitle(raw);
    if (!title) return;

    const latest = readConversationMeta(threadId);
    // 双检：生成期间用户可能手动改名（或已有命名），不得覆盖。
    if (latest.title_source) return;
    latest.title = title;
    latest.title_source = "auto";
    latest.updated_at = new Date().toISOString();
    writeConversationMeta(threadId, latest);
  } catch {
    // 静默：命名是锦上添花，任何异常都不能影响对话主链路。
  }
}
