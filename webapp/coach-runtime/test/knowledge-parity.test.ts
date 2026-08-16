import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  entryRef,
  loadKnowledgeRegistry,
  validateKnowledgeRegistry,
} from "../src/knowledge-registry.ts";

const PACKAGE_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const REPO_ROOT = resolve(PACKAGE_ROOT, "..", "..");
const DEFAULT_PYTHON_BIN = process.platform === "win32"
  ? resolve(REPO_ROOT, ".venv", "Scripts", "python.exe")
  : resolve(REPO_ROOT, ".venv", "bin", "python");

function runPython(
  script: string,
  value: unknown,
  cwd: string,
  source: "argument" | "stdin" = "argument",
): unknown {
  return JSON.parse(execFileSync(
    process.env.PYTHON_BIN ?? DEFAULT_PYTHON_BIN,
    source === "argument" ? ["-c", script, JSON.stringify(value)] : ["-c", script],
    {
      encoding: "utf8",
      input: source === "stdin" ? JSON.stringify(value) : undefined,
      cwd,
      env: { ...process.env, PYTHONPATH: REPO_ROOT },
    },
  ));
}

test("TypeScript and Python reject the same duplicate-section corpus", () => {
  const malformed = structuredClone(loadKnowledgeRegistry("2026-07-28.v3"));
  if (malformed.schema_version !== "coach_knowledge_registry.v2") throw new Error("missing v2 registry");
  const entry = malformed.entries[0];
  if (!entry || !("family_scope" in entry)) throw new Error("missing v2 entry");
  entry.scope.section_ref = entry.definition.section_ref;
  assert.throws(() => validateKnowledgeRegistry(malformed), /duplicate section_ref/);

  const script = [
    "import json,sys",
    "from kovaak_tracker.coach.knowledge_registry import KnowledgeRegistryError,validate_registry",
    "try:",
    "    validate_registry(json.loads(sys.stdin.read()))",
    "except KnowledgeRegistryError:",
    "    print(json.dumps(False))",
    "else:",
    "    print(json.dumps(True))",
  ].join("\n");
  for (const cwd of [REPO_ROOT, PACKAGE_ROOT]) {
    assert.equal(runPython(script, malformed, cwd, "stdin"), false, cwd);
  }
});

test("TypeScript and Python load the same registry version", () => {
  const registry = loadKnowledgeRegistry();
  const script = [
    "import json",
    "from kovaak_tracker.coach.knowledge_registry import load_registry",
    "r=load_registry()",
    "print(json.dumps([r['registry_version']]))",
  ].join(";");
  for (const cwd of [REPO_ROOT, PACKAGE_ROOT]) {
    const [pyVersion] = runPython(script, null, cwd) as string[];
    assert.equal(pyVersion, registry.registry_version, cwd);
    assert.equal(pyVersion, "2026-08-16.v8", cwd);
  }
});
