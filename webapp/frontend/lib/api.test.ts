import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { analyzeKovaakRun, listSessions, retrySession } from "./api";

const originalFetch = globalThis.fetch;
const originalWindow = Reflect.get(globalThis, "window");
const originalIsTauri = Reflect.get(globalThis, "isTauri");

function restoreGlobal(name: "window" | "isTauri", value: unknown): void {
  if (value === undefined) {
    Reflect.deleteProperty(globalThis, name);
  } else {
    Reflect.set(globalThis, name, value);
  }
}

afterEach(() => {
  globalThis.fetch = originalFetch;
  restoreGlobal("window", originalWindow);
  restoreGlobal("isTauri", originalIsTauri);
});

test("desktop API requests include the in-memory launch token by default", async () => {
  const requests: Array<{ input: string; init?: RequestInit }> = [];
  Reflect.set(globalThis, "isTauri", true);
  Reflect.set(globalThis, "window", {
    __TAURI_INTERNALS__: {
      invoke: async (command: string) => {
        assert.equal(command, "desktop_runtime_connection");
        return {
          baseUrl: "http://127.0.0.1:43127",
          token: "test-launch-token",
        };
      },
    },
  });
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    requests.push({ input: String(input), init });
    return new Response(JSON.stringify({ sessions: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  await listSessions();

  assert.equal(requests[0]?.input, "http://127.0.0.1:43127/api/sessions");
  const headers = new Headers(requests[0]?.init?.headers);
  assert.equal(headers.get("X-User-Id"), "desktop-local");
  assert.equal(
    headers.get("X-Aiming-Cookie-Desktop-Token"),
    "test-launch-token",
  );
});

test("browser API requests stay relative and do not add a desktop token", async () => {
  const requests: Array<{ input: string; init?: RequestInit }> = [];
  Reflect.set(globalThis, "isTauri", false);
  Reflect.set(globalThis, "window", {});
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    requests.push({ input: String(input), init });
    return new Response(JSON.stringify({ sessions: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  await listSessions();

  assert.equal(requests[0]?.input, "/api/sessions");
  const headers = new Headers(requests[0]?.init?.headers);
  assert.equal(headers.get("X-Aiming-Cookie-Desktop-Token"), null);
});

test("analysis write requests forward their stable idempotency keys", async () => {
  const requests: Array<{ input: string; init?: RequestInit }> = [];
  Reflect.set(globalThis, "isTauri", true);
  Reflect.set(globalThis, "window", {
    __TAURI_INTERNALS__: {
      invoke: async () => ({
        baseUrl: "http://127.0.0.1:43127",
        token: "test-launch-token",
      }),
    },
  });
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    requests.push({ input: String(input), init });
    return new Response(JSON.stringify({ session_id: 11, id: 11 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  await analyzeKovaakRun(
    7,
    { input_mode: "input_native" },
    { idempotencyKey: "analyze-key" },
  );
  await retrySession(11, { idempotencyKey: "retry-key" });

  assert.equal(
    new Headers(requests[0]?.init?.headers).get("Idempotency-Key"),
    "analyze-key",
  );
  assert.equal(
    new Headers(requests[1]?.init?.headers).get("Idempotency-Key"),
    "retry-key",
  );
});
