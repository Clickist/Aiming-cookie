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
