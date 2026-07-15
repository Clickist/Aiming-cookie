import assert from "node:assert/strict";
import test from "node:test";

import {
  ProviderAuthOperationManager,
  ProviderAuthRequestError,
  SnapshotCredentialStore,
  listBuiltinProviderAuthCapabilities,
  type PiAuthProvider,
} from "../src/provider-auth.ts";
import { loadPiProvidersAll } from "../src/pi-source.ts";

const SECRET = "task4-secret-sentinel-do-not-return";

async function waitFor(
  manager: ProviderAuthOperationManager,
  operationId: string,
  predicate: (operation: ReturnType<ProviderAuthOperationManager["get"]>) => boolean,
): Promise<ReturnType<ProviderAuthOperationManager["get"]>> {
  const deadline = Date.now() + 1_000;
  while (Date.now() < deadline) {
    const operation = manager.get(operationId);
    if (predicate(operation)) return operation;
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
  throw new Error(`operation ${operationId} did not reach expected state`);
}

function fakeProvider(overrides: Partial<PiAuthProvider> = {}): PiAuthProvider {
  return {
    id: "fixture-provider",
    name: "Fixture Provider",
    auth: {},
    ...overrides,
  };
}

test("auth capabilities project every pinned Pi provider without product filtering", async () => {
  const all = (await loadPiProvidersAll()) as {
    builtinProviders(): PiAuthProvider[];
  };
  const expected = all.builtinProviders();
  const capabilities = await listBuiltinProviderAuthCapabilities();

  assert.deepEqual(
    capabilities.providers.map((provider) => provider.provider_id),
    expected.map((provider) => provider.id),
  );
  assert.equal(capabilities.providers.length, expected.length);
  assert.deepEqual(
    capabilities.providers.find((provider) => provider.provider_id === "anthropic")?.auth_modes,
    ["api_key", "ambient", "oauth"],
  );
  assert.deepEqual(
    capabilities.providers.find((provider) => provider.provider_id === "google-vertex")?.auth_modes,
    ["ambient"],
  );
  assert.deepEqual(
    capabilities.providers.find((provider) => provider.provider_id === "openai-codex")?.auth_modes,
    ["oauth"],
  );
});

test("SnapshotCredentialStore is provider-scoped and preserves the current credential on failed modify", async () => {
  const original = { type: "oauth", access: "old", refresh: "refresh", expires: 1, tenant: "extra" } as const;
  const store = new SnapshotCredentialStore("fixture-provider", original);

  assert.deepEqual(await store.read("fixture-provider"), original);
  await assert.rejects(
    store.modify("fixture-provider", async () => {
      throw new Error("refresh failed");
    }),
  );
  assert.deepEqual(await store.read("fixture-provider"), original);
  await assert.rejects(store.read("other-provider"), /scoped to fixture-provider/);
});

test("generic API-key login supports multiple prompt kinds without retaining or echoing input", async () => {
  const provider = fakeProvider({
    auth: {
      apiKey: {
        name: "Fixture API key",
        login: async (callbacks) => {
          const key = await callbacks.prompt({ type: "secret", message: "Secret" });
          const account = await callbacks.prompt({ type: "text", message: "Account" });
          const region = await callbacks.prompt({
            type: "select",
            message: "Region",
            options: [
              { id: "region-us-secret", label: "United States" },
              { id: "eu", label: "Europe", description: "EU region" },
            ],
          });
          const code = await callbacks.prompt({ type: "manual_code", message: "Manual code" });
          return {
            type: "api_key",
            key,
            env: { ACCOUNT_ID: account, REGION: region, MANUAL_CODE: code },
          };
        },
        resolve: async () => undefined,
      },
    },
  });
  const manager = new ProviderAuthOperationManager({ loadProviders: async () => [provider] });
  try {
    const started = await manager.start({
      action: "login",
      provider_id: provider.id,
      mode: "api_key",
      timeout_ms: 1_000,
    });
    const values = [SECRET, "account-secret", "region-us-secret", "manual-secret"];
    const expectedTypes = ["secret", "text", "select", "manual_code"];

    for (let index = 0; index < values.length; index += 1) {
      const waiting = await waitFor(manager, started.id, (operation) => operation.prompt?.type === expectedTypes[index]);
      assert.ok(waiting.prompt);
      const publicAfterInput = manager.submitInput(started.id, {
        prompt_id: waiting.prompt.prompt_id,
        value: values[index],
      });
      assert.ok(!JSON.stringify(publicAfterInput).includes(values[index]));
    }

    const succeeded = await waitFor(manager, started.id, (operation) => operation.status === "succeeded");
    const publicJson = JSON.stringify(succeeded);
    for (const value of values) assert.ok(!publicJson.includes(value));

    assert.deepEqual(manager.takeResult(started.id).credential, {
      type: "api_key",
      key: SECRET,
      env: { ACCOUNT_ID: "account-secret", REGION: "region-us-secret", MANUAL_CODE: "manual-secret" },
    });
    assert.throws(
      () => manager.takeResult(started.id),
      (error: unknown) => error instanceof ProviderAuthRequestError && error.code === "result_already_taken",
    );
  } finally {
    manager.dispose();
  }
});

test("OAuth login exposes Pi auth_url/device_code/progress events and private extra credential fields", async () => {
  const provider = fakeProvider({
    auth: {
      oauth: {
        name: "Fixture OAuth",
        login: async (callbacks) => {
          callbacks.notify({ type: "auth_url", url: "https://login.test/authorize", instructions: "Open browser" });
          callbacks.notify({
            type: "device_code",
            userCode: "ABCD-EFGH",
            verificationUri: "https://login.test/device",
            intervalSeconds: 2,
            expiresInSeconds: 60,
          });
          callbacks.notify({ type: "progress", message: "Waiting for authorization" });
          const code = await callbacks.prompt({ type: "manual_code", message: "Paste redirect URL" });
          return {
            type: "oauth",
            access: `access-${code}`,
            refresh: "refresh-token",
            expires: Date.now() + 60_000,
            accountId: "account-extra-field",
          };
        },
        refresh: async (credential) => credential,
        toAuth: async (credential) => ({ apiKey: credential.access as string }),
      },
    },
  });
  const manager = new ProviderAuthOperationManager({ loadProviders: async () => [provider] });
  try {
    const started = await manager.start({
      action: "login",
      provider_id: provider.id,
      mode: "oauth",
      timeout_ms: 120_000,
    });
    const waiting = await waitFor(manager, started.id, (operation) => operation.prompt?.type === "manual_code");
    assert.deepEqual(waiting.events.map((event) => event.type), ["auth_url", "device_code", "progress"]);
    assert.ok(waiting.expires_at <= Date.now() + 60_000);
    assert.ok(!JSON.stringify(waiting).includes("refresh-token"));

    manager.submitInput(started.id, { prompt_id: waiting.prompt!.prompt_id, value: "oauth-code" });
    await waitFor(manager, started.id, (operation) => operation.status === "succeeded");
    const result = manager.takeResult(started.id).credential;
    assert.equal(result.type, "oauth");
    assert.equal(result.access, "access-oauth-code");
    assert.equal(result.refresh, "refresh-token");
    assert.equal(typeof result.expires, "number");
    assert.equal(result.accountId, "account-extra-field");
  } finally {
    manager.dispose();
  }
});

test("cancel and timeout suppress late login success", async () => {
  let resolveFirst!: (credential: { type: "api_key"; key: string }) => void;
  let resolveSecond!: (credential: { type: "api_key"; key: string }) => void;
  let call = 0;
  const provider = fakeProvider({
    auth: {
      apiKey: {
        name: "Late API key",
        login: async () =>
          new Promise((resolve) => {
            call += 1;
            if (call === 1) resolveFirst = resolve;
            else resolveSecond = resolve;
          }),
        resolve: async () => undefined,
      },
    },
  });
  const manager = new ProviderAuthOperationManager({ loadProviders: async () => [provider] });
  try {
    const cancelled = await manager.start({ action: "login", provider_id: provider.id, mode: "api_key" });
    manager.cancel(cancelled.id);
    resolveFirst({ type: "api_key", key: SECRET });
    await new Promise((resolve) => setTimeout(resolve, 5));
    assert.equal(manager.get(cancelled.id).status, "cancelled");
    assert.throws(() => manager.takeResult(cancelled.id), /no result/i);

    const timedOut = await manager.start({
      action: "login",
      provider_id: provider.id,
      mode: "api_key",
      timeout_ms: 10,
    });
    await waitFor(manager, timedOut.id, (operation) => operation.status === "timed_out");
    resolveSecond({ type: "api_key", key: SECRET });
    await new Promise((resolve) => setTimeout(resolve, 5));
    assert.equal(manager.get(timedOut.id).status, "timed_out");
    assert.ok(!JSON.stringify(manager.get(timedOut.id)).includes(SECRET));
  } finally {
    manager.dispose();
  }
});

test("refresh preserves OAuth extra fields and failed refresh exposes no credential or provider error secret", async () => {
  const original = {
    type: "oauth",
    access: SECRET,
    refresh: "refresh-secret",
    expires: 1,
    accountId: "account-extra",
  } as const;
  let fail = false;
  const provider = fakeProvider({
    auth: {
      oauth: {
        name: "Fixture OAuth",
        login: async () => original,
        refresh: async (credential) => {
          if (fail) throw new Error(`provider leaked ${credential.access}`);
          return { ...credential, access: "new-access", expires: Date.now() + 60_000 };
        },
        toAuth: async (credential) => ({ apiKey: credential.access as string }),
      },
    },
  });
  const manager = new ProviderAuthOperationManager({ loadProviders: async () => [provider] });
  try {
    const refreshed = await manager.start({
      action: "refresh",
      provider_id: provider.id,
      credential: original,
      timeout_ms: 1_000,
    });
    await waitFor(manager, refreshed.id, (operation) => operation.status === "succeeded");
    const result = manager.takeResult(refreshed.id).credential;
    assert.equal(result.type, "oauth");
    assert.equal(result.access, "new-access");
    assert.equal(result.refresh, "refresh-secret");
    assert.equal(typeof result.expires, "number");
    assert.equal(result.accountId, "account-extra");
    assert.equal(original.access, SECRET);

    fail = true;
    const failed = await manager.start({
      action: "refresh",
      provider_id: provider.id,
      credential: original,
      timeout_ms: 1_000,
    });
    const publicFailure = await waitFor(manager, failed.id, (operation) => operation.status === "failed");
    assert.equal(publicFailure.error?.code, "refresh_failed");
    assert.ok(!JSON.stringify(publicFailure).includes(SECRET));
    assert.ok(!JSON.stringify(publicFailure).includes("refresh-secret"));
    assert.ok(!JSON.stringify(publicFailure).includes("provider leaked"));
    assert.deepEqual(original, {
      type: "oauth",
      access: SECRET,
      refresh: "refresh-secret",
      expires: 1,
      accountId: "account-extra",
    });
  } finally {
    manager.dispose();
  }
});

test("OAuth state mismatch is a sanitized terminal failure", async () => {
  const provider = fakeProvider({
    auth: {
      oauth: {
        name: "Fixture OAuth",
        login: async () => {
          throw new Error(`State mismatch ${SECRET}`);
        },
        refresh: async (credential) => credential,
        toAuth: async (credential) => ({ apiKey: credential.access }),
      },
    },
  });
  const manager = new ProviderAuthOperationManager({ loadProviders: async () => [provider] });
  try {
    const started = await manager.start({ action: "login", provider_id: provider.id, mode: "oauth" });
    const failed = await waitFor(manager, started.id, (operation) => operation.status === "failed");
    assert.equal(failed.error?.code, "login_failed");
    assert.equal(failed.error?.message, "Authentication login failed");
    assert.ok(!JSON.stringify(failed).includes(SECRET));
    assert.ok(!JSON.stringify(failed).includes("State mismatch"));
  } finally {
    manager.dispose();
  }
});

test("terminal operations and untaken credentials are evicted after retention", async () => {
  const provider = fakeProvider({
    auth: {
      apiKey: {
        name: "Fixture API key",
        login: async () => ({ type: "api_key", key: SECRET }),
        resolve: async () => undefined,
      },
    },
  });
  const manager = new ProviderAuthOperationManager({
    loadProviders: async () => [provider],
    terminalRetentionMs: 10,
  });
  try {
    const started = await manager.start({ action: "login", provider_id: provider.id, mode: "api_key" });
    await waitFor(manager, started.id, (operation) => operation.status === "succeeded");
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.throws(
      () => manager.get(started.id),
      (error: unknown) => error instanceof ProviderAuthRequestError && error.code === "operation_not_found",
    );
  } finally {
    manager.dispose();
  }
});
