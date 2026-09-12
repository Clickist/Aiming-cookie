// Regression for auto-title P0 (0911 审计 §12.1): the wiring in agent-runs.ts
// used to pass {profile, needsReauth} where maybeAutoTitleSession expects
// {models, fallbackModel}; the resulting TypeError was swallowed inside
// session-title.ts and the title was never written. Shape A below now runs the
// REAL wiring path (resolveSessionTitleProviders) and must reach the stream
// gate; shape B stays as the direct-shape control.
import path from "node:path";
import fs from "node:fs";
import os from "node:os";

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "autotitle-repro-"));
process.env.DATA_ROOT = tmp;

const { maybeAutoTitleSession } = await import("../../webapp/coach-runtime/src/session-title.ts");
const { resolveSessionTitleProviders } = await import("../../webapp/coach-runtime/src/agent-runs.ts");

const metaDir = path.join(tmp, "conversations");
fs.mkdirSync(metaDir, { recursive: true });
const metaPath = path.join(metaDir, "999.meta.json");
fs.writeFileSync(metaPath, JSON.stringify({ id: 999, title: "新对话", status: "active", created_at: new Date().toISOString(), updated_at: new Date().toISOString() }));

// shape A: what loadDefaultProviderProfile returns (agent-runs.ts) — resolved
// through the actual wiring helper before reaching maybeAutoTitleSession.
const profile = {
  kind: "custom_openai_compatible",
  provider_id: "repro-provider",
  provider_name: "Repro Provider",
  base_url: "http://127.0.0.1:9",
  credential: { type: "api_key", key: "repro-key" },
  model_id: "deepseek-v4-flash",
};
const titleProviders = await resolveSessionTitleProviders(profile);
console.log("=== shape A (profile 经 resolveSessionTitleProviders 解析) ===");
console.log("解析出的 providers.models 可用:", typeof titleProviders.models?.getModels === "function");
console.log("解析出的 fallbackModel.id:", titleProviders.fallbackModel?.id);

// Stub only the network boundary: reaching streamSimple IS the stream gate.
let streamReached = false;
const spyModels = Object.create(titleProviders.models);
spyModels.streamSimple = () => {
  streamReached = true;
  return { result: async () => ({ content: [{ type: "text", text: "接线回归标题" }], stopReason: "stop" }) };
};

await maybeAutoTitleSession(999, "你好", "收到", { models: spyModels, fallbackModel: titleProviders.fallbackModel });

const metaAfter = JSON.parse(fs.readFileSync(metaPath, "utf-8"));
console.log("stream 门被走到:", streamReached);
console.log("meta.title 写入:", JSON.stringify(metaAfter.title), " title_source:", metaAfter.title_source ?? "无");

// control: correct shape reaches the model resolution
let streamReachedB = false;
const shapeB = {
  models: {
    getModels: () => [{ id: "test-model" }],
    getAuth: async () => ({}),
    streamSimple: () => { streamReachedB = true; return { result: async () => ({ content: [{ type: "text", text: "测试标题" }], stopReason: "stop" }) }; },
  },
  fallbackModel: { id: "test-model" },
};
const meta2Path = path.join(metaDir, "998.meta.json");
fs.writeFileSync(meta2Path, JSON.stringify({ id: 998, title: "新对话", status: "active", created_at: new Date().toISOString(), updated_at: new Date().toISOString() }));
await maybeAutoTitleSession(998, "你好", "收到", shapeB);
const meta2 = JSON.parse(fs.readFileSync(meta2Path, "utf-8"));
console.log("=== shape B (SessionTitleProviders，期望的形) ===");
console.log("stream 被调用:", streamReachedB, " meta.title 写入:", JSON.stringify(meta2.title), " title_source:", meta2.title_source);

if (!streamReached || metaAfter.title_source !== "auto" || !streamReachedB) {
  console.error("FAIL: 接线回归未通过");
  fs.rmSync(tmp, { recursive: true, force: true });
  process.exit(1);
}
console.log("PASS: shape A 经真实接线走到 stream 门并写入标题");
fs.rmSync(tmp, { recursive: true, force: true });
process.exit(0);
