import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";

import { readLastCoachSessionId, writeLastCoachSessionId } from "../lib/contracts";

const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

function memoryStorage(): Storage {
  const map = new Map<string, string>();
  return {
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => { map.set(key, value); },
    removeItem: (key: string) => { map.delete(key); },
    clear: () => { map.clear(); },
    key: (index: number) => [...map.keys()][index] ?? null,
    get length() { return map.size; },
  } as Storage;
}

test("last coach session helpers round-trip a valid id", () => {
  const storage = memoryStorage();
  assert.equal(readLastCoachSessionId(storage), null);
  writeLastCoachSessionId(storage, 42);
  assert.equal(readLastCoachSessionId(storage), 42);
});

test("last coach session helpers reject corrupted values", () => {
  const storage = memoryStorage();
  storage.setItem("aiming-cookie.last-coach-session", "not-a-number");
  assert.equal(readLastCoachSessionId(storage), null);
  storage.setItem("aiming-cookie.last-coach-session", "-3");
  assert.equal(readLastCoachSessionId(storage), null);
});

test("last coach session helpers tolerate a missing storage", () => {
  assert.equal(readLastCoachSessionId(null), null);
  writeLastCoachSessionId(null, 7); // 静默不抛
});

test("last coach session helpers survive a storage that throws", () => {
  const throwing: Storage = {
    get length() { return 0; },
    clear: () => {},
    getItem: () => { throw new Error("SecurityError"); },
    key: () => null,
    removeItem: () => {},
    setItem: () => { throw new Error("SecurityError"); },
  };
  assert.equal(readLastCoachSessionId(throwing), null);
  writeLastCoachSessionId(throwing, 7); // 静默不抛
});

test("AppShell restores the last viewed session only on cold start", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  // 一次性消费：列表加载前不消耗恢复机会，且只消费一次。
  assert.match(shell, /hasRestoredLastSessionRef/);
  assert.match(shell, /!hasRestoredLastSessionRef\.current && coachSessions\.length > 0/);
  assert.match(shell, /hasRestoredLastSessionRef\.current = true/);
  // 恢复数据源：上次会话 id 有效且仍存在于列表才采纳。
  assert.match(shell, /readLastCoachSessionId\(window\.localStorage\)/);
  assert.match(shell, /coachSessions\.some\(\(session\) => Number\(session\.id\) === lastViewedId\)/);
  // 失效/未选中时回退 primary 会话（上次对话的延续）。
  assert.match(shell, /const primary = coachSessions\.find\(\(session\) => session\.kind === "primary"\)/);
  // 选中变化即记录，供下次启动恢复。
  assert.match(shell, /writeLastCoachSessionId\(window\.localStorage, selectedCoachSessionId\)/);
});
