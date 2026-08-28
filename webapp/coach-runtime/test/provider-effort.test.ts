import assert from "node:assert/strict";
import http from "node:http";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test, { describe, it } from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-provider-effort-"));
process.env.DATA_ROOT = dataRoot;

import {
  parseProviderProfile,
  ProviderProfileError,
  sanitizeProviderProfile,
} from "../src/provider-profile.ts";
import { loadProviderStore, saveProviderStore } from "../src/provider-store.ts";
import { createSidecarServer } from "../src/sidecar-server.ts";
import { defaultThinkingLevel } from "../src/turn.ts";

describe("parseProviderProfile（reasoning_effort 白名单校验）", () => {
  it("接受五档合法值并保留字段", () => {
    for (const effort of ["minimal", "low", "medium", "high", "off"] as const) {
      const profile = parseProviderProfile({
        kind: "builtin",
        provider_id: "opencode-go",
        model_id: "deepseek-v4-flash",
        reasoning_effort: effort,
      });
      assert.equal(profile.reasoning_effort, effort);
    }
  });

  it("custom 档同样校验并保留", () => {
    const profile = parseProviderProfile({
      kind: "custom_openai_compatible",
      provider_name: "Local Lab",
      base_url: "https://lab.example/v1",
      model_id: "fixture-model",
      api_key: "k",
      reasoning_effort: "medium",
    });
    assert.equal(profile.reasoning_effort, "medium");
  });

  it("非法值直接拒绝且不静默吞掉", () => {
    for (const bad of ["ultra", "", 2, true, null]) {
      assert.throws(
        () =>
          parseProviderProfile({
            kind: "builtin",
            provider_id: "opencode-go",
            model_id: "deepseek-v4-flash",
            reasoning_effort: bad,
          }),
        (error: unknown) =>
          error instanceof ProviderProfileError && /reasoning_effort/.test(error.message),
      );
    }
  });
});

describe("profile 往返", () => {
  it("sanitize 透传 reasoning_effort，未设置时不产出该键", () => {
    const withEffort = sanitizeProviderProfile(parseProviderProfile({
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
      reasoning_effort: "low",
    }));
    assert.equal(withEffort.reasoning_effort, "low");
    const withoutEffort = sanitizeProviderProfile(parseProviderProfile({
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
    }));
    assert.equal("reasoning_effort" in withoutEffort, false);
  });

  it("provider store 原样往返 reasoning_effort（读入不白名单）", () => {
    const profile = parseProviderProfile({
      kind: "builtin",
      provider_id: "opencode-go",
      model_id: "deepseek-v4-flash",
      reasoning_effort: "low",
    });
    saveProviderStore({
      schema_version: 2,
      active_id: 7,
      next_id: 8,
      profiles: [{ id: 7, ...profile }],
    });
    const store = loadProviderStore();
    assert.equal(store.profiles.length, 1);
    assert.equal(store.profiles[0]?.reasoning_effort, "low");
  });
});

describe("defaultThinkingLevel（off / 未设置 三态语义）", () => {
  const reasoningModel = { provider: "opencode-go", reasoning: true };

  it("未设置维持默认 fallback：推理模型 high（deepseek 说出声回归红线）", () => {
    assert.equal(defaultThinkingLevel(reasoningModel), "high");
    assert.equal(defaultThinkingLevel(reasoningModel, undefined), "high");
    // 非推理模型维持默认 off，不动请求形态。
    assert.equal(defaultThinkingLevel({ provider: "deepseek", reasoning: false }), undefined);
  });

  it("off = 显式关闭思考：返回 undefined（harness 默认 off）", () => {
    assert.equal(defaultThinkingLevel(reasoningModel, "off"), undefined);
  });

  it("minimal..high 原样下发，不支持档由 Pi clampThinkingLevel 收敛", () => {
    assert.equal(defaultThinkingLevel(reasoningModel, "minimal"), "minimal");
    assert.equal(defaultThinkingLevel(reasoningModel, "low"), "low");
    assert.equal(defaultThinkingLevel(reasoningModel, "medium"), "medium");
    assert.equal(defaultThinkingLevel(reasoningModel, "high"), "high");
  });
});

describe("POST /v1/provider-profiles/model（reasoning_effort 挂档落盘）", () => {
  function request(
    server: http.Server,
    method: string,
    path: string,
    body?: string,
  ): Promise<{ statusCode: number; json: unknown }> {
    return new Promise((resolve, reject) => {
      const address = server.address();
      if (!address || typeof address === "string") {
        reject(new Error("server not listening"));
        return;
      }
      const req = http.request(
        {
          host: "127.0.0.1",
          port: address.port,
          method,
          path,
          headers: body
            ? { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) }
            : undefined,
        },
        (res) => {
          const chunks: Buffer[] = [];
          res.on("data", (chunk: Buffer) => chunks.push(chunk));
          res.on("end", () => {
            const raw = Buffer.concat(chunks).toString("utf8");
            resolve({ statusCode: res.statusCode ?? 0, json: raw ? JSON.parse(raw) : null });
          });
        },
      );
      req.on("error", reject);
      if (body) req.write(body);
      req.end();
    });
  }

  function withServer(run: (server: http.Server) => Promise<void>): Promise<void> {
    const server = createSidecarServer();
    return new Promise((resolve, reject) => {
      server.listen(0, "127.0.0.1", async () => {
        try {
          await run(server);
          resolve();
        } catch (error) {
          reject(error);
        } finally {
          await new Promise<void>((closeResolve) => server.close(() => closeResolve()));
        }
      });
    });
  }

  function switchBody(modelId: string, effort?: string | null): string {
    return JSON.stringify({
      schema_version: "coach_provider_model_switch.v1",
      model_id: modelId,
      ...(effort === undefined ? {} : { reasoning_effort: effort }),
    });
  }

  it("设置 → 缺省切模型保留 → null 清除，非法值 400 且不落盘", async () => {
    await withServer(async (server) => {
      const created = await request(server, "POST", "/v1/provider-profiles", JSON.stringify({
        kind: "builtin",
        provider_id: "opencode-go",
        model_id: "deepseek-v4-flash",
      }));
      assert.equal(created.statusCode, 201);

      // 1) 挂力度：与 model_id 同路由、同落盘，响应 profile 带回当前值。
      const setRes = await request(server, "POST", "/v1/provider-profiles/model", switchBody("deepseek-v4-flash", "low"));
      assert.equal(setRes.statusCode, 200);
      assert.equal(
        (setRes.json as { profile: Record<string, unknown> | null }).profile?.reasoning_effort,
        "low",
      );
      assert.equal(loadProviderStore().profiles[0]?.reasoning_effort, "low");

      // 2) 缺省 reasoning_effort 切模型：力度是档级旋钮，不随模型切换丢失。
      const switchRes = await request(server, "POST", "/v1/provider-profiles/model", switchBody("deepseek-v4-pro"));
      assert.equal(switchRes.statusCode, 200);
      assert.equal(
        (switchRes.json as { profile: Record<string, unknown> | null }).profile?.reasoning_effort,
        "low",
      );
      const afterSwitch = loadProviderStore().profiles[0];
      assert.equal(afterSwitch?.model_id, "deepseek-v4-pro");
      assert.equal(afterSwitch?.reasoning_effort, "low");

      // 3) null = 显式清除回「未设置」，运行时恢复默认高档 fallback。
      const clearRes = await request(server, "POST", "/v1/provider-profiles/model", switchBody("deepseek-v4-pro", null));
      assert.equal(clearRes.statusCode, 200);
      const clearedProfile = (clearRes.json as { profile: Record<string, unknown> | null }).profile;
      assert.ok(clearedProfile);
      assert.equal("reasoning_effort" in clearedProfile, false);
      assert.equal("reasoning_effort" in (loadProviderStore().profiles[0] ?? {}), false);

      // 4) 非法值 400，且不得改动已存档。
      const badRes = await request(server, "POST", "/v1/provider-profiles/model", switchBody("deepseek-v4-pro", "ultra"));
      assert.equal(badRes.statusCode, 400);
      assert.equal("reasoning_effort" in (loadProviderStore().profiles[0] ?? {}), false);
    });
  });
});
