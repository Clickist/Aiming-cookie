import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import test from "node:test";

import { entryRef, loadKnowledgeRegistry, queryKnowledgeRegistry } from "../src/knowledge-registry.ts";

const QUERIES = [
  { topic: "smoothness_sparc", issue_signal: "sparc low", metric_refs: ["metric:sparc"], supported_use: "definition" },
  { topic: "tracking_control", issue_signal: "accel mismatch high", metric_refs: ["metric:accel_mismatch"], supported_use: "training_cue" },
  { topic: "body_tension", issue_signal: "ptc high", metric_refs: ["metric:ptc"], supported_use: "candidate_hypothesis" },
  { topic: "settings_experiment", issue_signal: "sensitivity high", metric_refs: ["metric:cm_per_360"], supported_use: "verification" },
];

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
    const pyRefs = JSON.parse(execFileSync(
      process.env.PYTHON_BIN ?? ".venv/bin/python",
      ["-c", script, JSON.stringify(query)],
      { encoding: "utf8", cwd: process.cwd() },
    ));
    assert.deepEqual(tsRefs, pyRefs, JSON.stringify(query));
  }
});
