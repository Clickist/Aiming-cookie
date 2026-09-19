import assert from "node:assert/strict";
import { mkdirSync, rmSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

// 规划 §4.2 的 UI 态夹具：AC_MEMBER_FIXTURE 必须在模块导入前设好
//（夹具在模块加载时读一次环境变量，与 sidecar 生命期一致）。
// node --test 每个测试文件独立进程，故这里改环境变量不会污染其他用例。
const testRoot = join(process.env.TEMP ?? process.env.TMP ?? ".", `ac-member-fixture-${process.pid}-${Date.now()}`);
process.env.DATA_ROOT = testRoot;
process.env.AC_ACCOUNTS_BASE_URL = "http://127.0.0.1:1";
process.env.AC_MEMBER_GATEWAY_BASE_URL = "http://127.0.0.1:1/v1";
process.env.AC_MEMBER_FIXTURE = "lost";

const memberAuth = await import("../src/member-auth.ts");

test("fixture mode serves the lost state (both pools drained) without any network", async () => {
  if (process.env.TEMP) mkdirSync(testRoot, { recursive: true });
  try {
    const result = await memberAuth.fetchMemberMe();
    assert.equal(result.ok, true);
    assert.equal(result.ok && result.me.status, "expired");
    assert.equal(result.ok && result.me.member, false);
    assert.equal(result.ok && result.me.pools.sub?.pct, 0);
    assert.equal(result.ok && result.me.pools.boost, null);
    // 夹具下连通测试直接通过，不必起网关。
    assert.deepEqual(await memberAuth.testMemberConnection(), { ok: true });
    assert.equal(memberAuth.storedMemberJwt(), "fixture-member-jwt");
  } finally {
    rmSync(testRoot, { recursive: true, force: true });
  }
});
