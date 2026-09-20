import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

// Each run gets a fresh data root so the repo checkout stays clean.
const dataRoot = mkdtempSync(join(tmpdir(), "aiming-cookie-session-title-test-"));
process.env.DATA_ROOT = dataRoot;

const { readConversationMeta, writeConversationMeta, deriveConversationTitle } = await import(
  "../src/session-repo.ts"
);
const { sanitizeSessionTitle, maybeAutoTitleSession } = await import("../src/session-title.ts");

test.after(() => {
  rmSync(dataRoot, { recursive: true, force: true });
});

test("sanitizeSessionTitle 清洗引号/前缀/尾标点并截断", () => {
  assert.equal(sanitizeSessionTitle("据枪稳定性诊断"), "据枪稳定性诊断");
  assert.equal(sanitizeSessionTitle("「据枪稳定性诊断」"), "据枪稳定性诊断");
  assert.equal(sanitizeSessionTitle("关于压枪节奏的问题。"), "压枪节奏的问题");
  assert.equal(sanitizeSessionTitle("标题：据枪稳定性诊断\n补充说明"), "据枪稳定性诊断");
  assert.equal(sanitizeSessionTitle("  \n  "), null);
  assert.equal(sanitizeSessionTitle("这是一个特别特别特别特别特别特别特别特别特别特别特别长的标题需要被截断"), length24());
});

function length24(): string {
  return "这是一个特别特别特别特别特别特别特别特别特别特别特别长的标题需要被截断".slice(0, 24);
}

test("deriveConversationTitle：已命名 meta 优先于首句截断，缺省回退降级链", () => {
  const messages = [
    { role: "user", content: "帮我看看最近压枪为什么总是往下掉" },
    { role: "assistant", content: "好的，我们一步步来。" },
  ];
  // 未命名：首句截断降级
  assert.equal(
    deriveConversationTitle(messages, { id: 1, title: "新对话", status: "active", created_at: "", updated_at: "" }),
    "帮我看看最近压枪为什么总是往下掉",
  );
  // auto 命名后：meta.title 优先
  assert.equal(
    deriveConversationTitle(messages, {
      id: 1,
      title: "压枪节奏诊断",
      title_source: "auto",
      status: "active",
      created_at: "",
      updated_at: "",
    }),
    "压枪节奏诊断",
  );
  // 无消息且未命名：回退 meta.title（"新对话"）
  assert.equal(
    deriveConversationTitle([], { id: 1, title: "新对话", status: "active", created_at: "", updated_at: "" }),
    "新对话",
  );
});

test("maybeAutoTitleSession：user 命名永不覆盖，auto 只发生一次，成功写库", async () => {
  const models = {
    getModels: () => [{ id: "deepseek-v4-flash", provider: "relay" }],
    getAuth: async () => ({ auth: { apiKey: "k" } }),
    streamSimple: (_model: unknown, _context: unknown, _options: unknown) => ({
      async result() {
        return { content: [{ type: "text", text: "「压枪节奏诊断」" }], stopReason: "stop" };
      },
    }),
  };
  const providers = { models, fallbackModel: { id: "gpt-5.5" } };

  // 首次：写入 auto 标题（清洗掉引号）
  writeConversationMeta(9001, {
    id: 9001,
    title: "新对话",
    title_source: null,
    status: "active",
    created_at: "",
    updated_at: "",
  });
  await maybeAutoTitleSession(9001, "压枪总往下掉怎么办", "我们先看弹道数据。", providers as never);
  assert.equal(readConversationMeta(9001).title, "压枪节奏诊断");
  assert.equal(readConversationMeta(9001).title_source, "auto");

  // 第二次：auto 守卫生效，不再调用（换个假标题也写不进去）
  const guarded = { ...providers, models: { ...models, streamSimple: () => ({ async result() { return { content: [{ type: "text", text: "不应写入" }] }; } }) } };
  await maybeAutoTitleSession(9001, "另一个问题", "回复", guarded as never);
  assert.equal(readConversationMeta(9001).title, "压枪节奏诊断");

  // user 命名：永不被自动覆盖
  writeConversationMeta(9002, {
    id: 9002,
    title: "我自己的名字",
    title_source: "user",
    status: "active",
    created_at: "",
    updated_at: "",
  });
  await maybeAutoTitleSession(9002, "问题", "回复", providers as never);
  assert.equal(readConversationMeta(9002).title, "我自己的名字");
  assert.equal(readConversationMeta(9002).title_source, "user");
});

test("maybeAutoTitleSession：模型失败静默回退，不写 title_source（下次 run 自然重试）", async () => {
  const failing = {
    getModels: () => [],
    getAuth: async () => ({ auth: { apiKey: "k" } }),
    streamSimple: () => ({
      async result() {
        return { content: [{ type: "text", text: "" }], stopReason: "error", errorMessage: "boom" };
      },
    }),
  };
  writeConversationMeta(9003, {
    id: 9003,
    title: "新对话",
    title_source: null,
    status: "active",
    created_at: "",
    updated_at: "",
  });
  await maybeAutoTitleSession(9003, "问题", "回复", failing as never);
  const meta = readConversationMeta(9003);
  assert.equal(meta.title_source, null);
  assert.equal(meta.title, "新对话");
});

test("英文清洗：引号/About 类前缀/尾标点/词边界截断，中文行为不变", () => {
  assert.equal(sanitizeSessionTitle("「Flick accuracy」", 48), "Flick accuracy");
  assert.equal(sanitizeSessionTitle("\"Tracking diagnosis.\"", 48), "Tracking diagnosis");
  assert.equal(sanitizeSessionTitle("About: settling issues", 48), "settling issues");
  assert.equal(sanitizeSessionTitle("Title: Flick stability", 48), "Flick stability");
  // 词边界截断：不吃半个词、不留尾空格
  const long = "Understanding micro corrections and settling behavior in dynamic scenarios";
  const cut = sanitizeSessionTitle(long, 48)!;
  assert.ok(cut.length <= 48, `截断超预算: ${cut}`);
  assert.match(cut, /^Understanding micro corrections and settling/, `截断应保留完整词: ${cut}`);
  assert.doesNotMatch(cut, /\s$/, "截断不应留尾随空格");
  // zh 缺省预算 24 字不变
  assert.equal(
    sanitizeSessionTitle("这是一个特别特别特别特别特别特别特别特别特别特别特别长的标题需要被截断"),
    length24(),
  );
});

test("maybeAutoTitleSession：标题语言跟随学员消息语言（en 消息走英文 prompt 与英文标签）", async () => {
  const captured: Array<{ systemPrompt: string; userContent: string }> = [];
  const models = {
    getModels: () => [{ id: "deepseek-v4-flash", provider: "relay" }],
    getAuth: async () => ({ auth: { apiKey: "k" } }),
    streamSimple: (_model: unknown, context: { systemPrompt: string; messages: Array<{ content: string }> }) => {
      captured.push({ systemPrompt: context.systemPrompt, userContent: context.messages[0]!.content });
      return {
        async result() {
          return { content: [{ type: "text", text: "\"Flick accuracy decline\"" }], stopReason: "stop" };
        },
      };
    },
  };
  writeConversationMeta(9004, {
    id: 9004,
    title: "新对话",
    title_source: null,
    status: "active",
    created_at: "",
    updated_at: "",
  });
  await maybeAutoTitleSession(
    9004,
    "Why does my flick accuracy keep dropping on static targets?",
    "Your deceleration phase is too long.",
    { models, fallbackModel: { id: "gpt-5.5" } } as never,
  );
  const meta = readConversationMeta(9004);
  assert.equal(meta.title, "Flick accuracy decline", "英文标题应写库（含引号清洗）");
  assert.equal(meta.title_source, "auto");
  assert.match(captured[0]!.systemPrompt, /You are a session title generator/);
  assert.match(captured[0]!.userContent, /^Student: /);
  assert.match(captured[0]!.userContent, /\nCoach: /);
  assert.match(captured[0]!.userContent, /\n\nTitle:$/);
  assert.doesNotMatch(captured[0]!.userContent, /[\u4e00-\u9fff]/, "英文路径 prompt 不应含中文标签");
});

test("maybeAutoTitleSession：中文消息仍走中文 prompt（行为逐字不变）", async () => {
  const captured: Array<{ systemPrompt: string; userContent: string }> = [];
  const models = {
    getModels: () => [{ id: "deepseek-v4-flash", provider: "relay" }],
    getAuth: async () => ({ auth: { apiKey: "k" } }),
    streamSimple: (_model: unknown, context: { systemPrompt: string; messages: Array<{ content: string }> }) => {
      captured.push({ systemPrompt: context.systemPrompt, userContent: context.messages[0]!.content });
      return {
        async result() {
          return { content: [{ type: "text", text: "压枪节奏诊断" }], stopReason: "stop" };
        },
      };
    },
  };
  writeConversationMeta(9005, {
    id: 9005,
    title: "新对话",
    title_source: null,
    status: "active",
    created_at: "",
    updated_at: "",
  });
  await maybeAutoTitleSession(9005, "压枪总往下掉怎么办", "我们先看弹道数据。", {
    models,
    fallbackModel: { id: "gpt-5.5" },
  } as never);
  assert.equal(readConversationMeta(9005).title, "压枪节奏诊断");
  assert.match(captured[0]!.systemPrompt, /你是会话标题生成器/);
  assert.match(captured[0]!.userContent, /^学员: /);
  assert.match(captured[0]!.userContent, /\n\n标题:$/);
});
