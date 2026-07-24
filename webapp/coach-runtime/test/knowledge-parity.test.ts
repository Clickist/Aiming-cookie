import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  entryRef,
  loadKnowledgeRegistry,
  queryKnowledgeRegistry,
  validateKnowledgeRegistry,
} from "../src/knowledge-registry.ts";

const QUERIES = [
  { topic: "smoothness_sparc", issue_signal: "sparc low", metric_refs: ["metric:sparc"], supported_use: "definition" },
  { topic: "tracking_control", issue_signal: "accel mismatch high", metric_refs: ["metric:accel_mismatch"], supported_use: "training_cue" },
  { topic: "body_tension", issue_signal: "ptc high", metric_refs: ["metric:ptc"], supported_use: "candidate_hypothesis" },
  { topic: "settings_experiment", issue_signal: "sensitivity high", metric_refs: ["metric:cm_per_360"], supported_use: "verification" },
];

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

test("TypeScript and Python return identical entry refs and order", () => {
  const registry = loadKnowledgeRegistry();
  for (const query of QUERIES) {
    const tsRefs = queryKnowledgeRegistry(registry, query).map(entryRef);
    const script = [
      "import json,sys",
      "from kovaak_tracker.coach.knowledge_registry import entry_ref,query_registry",
      "q=json.loads(sys.argv[1])",
      "print(json.dumps([entry_ref(e) for e in query_registry(topic=q.get('topic'), issue_signal=q.get('issue_signal'), metric_refs=q.get('metric_refs',()), supported_use=q.get('supported_use'))]))",
    ].join(";");
    for (const cwd of [REPO_ROOT, PACKAGE_ROOT]) {
      assert.deepEqual(tsRefs, runPython(script, query, cwd), `${cwd}: ${JSON.stringify(query)}`);
    }
  }
});

test("TypeScript and Python reject the same duplicate-section corpus", () => {
  const malformed = structuredClone(loadKnowledgeRegistry());
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
