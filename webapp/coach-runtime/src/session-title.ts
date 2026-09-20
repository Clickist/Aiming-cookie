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
/** 中文按字截断；英文同量级预算放宽（6 词量级的中文 24 字 ≈ 英文 48 字符）。 */
const TITLE_MAX_CHARS_ZH = 24;
const TITLE_MAX_CHARS_EN = 48;

/**
 * 标题语言跟随学员消息：有汉字→中文标题，否则英文标题。与教练语言同一决策
 * 哲学（跟消息走），但这里做确定性判定而不交给模型自判——标题是短输出任务，
 * 模型会被 system prompt 的语言带偏（旧版整段中文 prompt 下，英文学员也得到
 * 中文标题），必须显式指定输出语言并配同语言 prompt。
 */
function titleLanguageOf(userText: string): "zh" | "en" {
  return /[\u4e00-\u9fff]/.test(userText) ? "zh" : "en";
}

const TITLE_SYSTEM_PROMPTS: Record<"zh" | "en", string> = {
  zh:
    "你是会话标题生成器。根据训练教练与学员的一段对话开头，输出一个简短标题。" +
    "要求：12 个字以内、名词短语；使用对话的主要语言；不要引号、句号、emoji、" +
    "不要「关于」「对话」等前缀；概括核心话题，准确优先；只输出标题本身。",
  en:
    "You are a session title generator. Given the opening of a conversation between a training coach and a student, output a short title. " +
    "Requirements: at most 6 words, a noun phrase; write the title in English; no quotes, periods, emoji, or prefixes like \"About\"/\"Conversation\"; " +
    "summarize the core topic, prefer accuracy; output only the title itself.",
};

export type SessionTitleProviders = {
  models: ResolvedProviderModel["models"];
  fallbackModel: ResolvedProviderModel["model"];
};

/** 清洗模型输出：取首行、去引号括号与前缀、去尾标点、截断；空结果放弃。 */
export function sanitizeSessionTitle(raw: string, maxChars: number = TITLE_MAX_CHARS_ZH): string | null {
  const firstLine = raw.trim().split(/\r?\n/, 1)[0] ?? "";
  const cleaned = firstLine
    .replace(/^[「『"'《<（(【[]+/, "")
    .replace(/[」』"'》>）)】\]]+$/, "")
    .replace(/^(?:关于|对话|标题|About|Conversation|Title)\s*[:：]?\s*/i, "")
    .replace(/[。．.!！?？～~、;；]+$/, "")
    .trim();
  // 超预算截断：英文回退到词边界（不吃半个词）；中文无空格不受影响。
  const truncated =
    cleaned.length > maxChars ? cleaned.slice(0, maxChars).replace(/\s+\S*$/, "").trim() : cleaned;
  return truncated || null;
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

    // 标题语言跟随学员消息（与教练语言同哲学）；prompt 与标签同语言，
    // 避免模型被 prompt 语言带偏。
    const lang = titleLanguageOf(userText);
    const labels = lang === "zh"
      ? { user: "学员", coach: "教练", title: "标题" }
      : { user: "Student", coach: "Coach", title: "Title" };
    const userPrompt =
      `${labels.user}: ${userText.slice(0, 600)}\n${labels.coach}: ${replyText.slice(0, 400)}\n\n${labels.title}:`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), TITLE_TIMEOUT_MS);
    timer.unref?.();
    let raw = "";
    try {
      const stream = providers.models.streamSimple(
        titleModel,
        {
          systemPrompt: TITLE_SYSTEM_PROMPTS[lang],
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

    const title = sanitizeSessionTitle(raw, lang === "en" ? TITLE_MAX_CHARS_EN : TITLE_MAX_CHARS_ZH);
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
