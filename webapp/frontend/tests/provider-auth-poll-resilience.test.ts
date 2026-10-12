import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const frontendRoot = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(frontendRoot, relativePath), "utf8");
}

test("OAuth authorization polling re-arms itself after a transient failure (P1-14)", async () => {
  // 鲁棒性审计 P1-14：一次瞬态网络失败后 catch 只 toast 不改 state → effect
  // 不重跑 → 轮询永久停摆，状态条停在「授权运行中」。修复后 catch 必须自增
  // 轮询节拍重排 effect，节拍必须出现在 effect 依赖里。
  const section = await source("components/task6/ProviderSettingsSection.tsx");
  assert.match(section, /authUnreadable[\s\S]{0,220}setAuthPollTick\(\(tick\) => tick \+ 1\)/);
  assert.match(section, /\}, \[authOperation, authProfileId, notify, refresh, authPollTick\]\);/);
});
