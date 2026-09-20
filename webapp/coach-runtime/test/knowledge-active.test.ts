import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

// Active resolution binds to DATA_ROOT on first use (getDataRoot caches);
// point it at a throwaway directory before any test triggers a call.
const dataRoot = mkdtempSync(join(tmpdir(), "coach-knowledge-active-"));
process.env.DATA_ROOT = dataRoot;

import {
  activePackDisplayName,
  lastActiveKnowledgeFallbackReason,
  loadActiveKnowledgeRegistry,
  resolveActiveKnowledge,
} from "../src/knowledge-active.ts";
import { loadKnowledgeRegistry } from "../src/knowledge-registry.ts";

function configPath(): string {
  return join(dataRoot, "config", "knowledge.json");
}

function writeConfig(doc: unknown): void {
  mkdirSync(join(dataRoot, "config"), { recursive: true });
  writeFileSync(configPath(), typeof doc === "string" ? doc : JSON.stringify(doc, null, 2), "utf-8");
}

function installPack(packId: string, registryRaw: unknown): string {
  const registryPath = join(dataRoot, "knowledge-packs", packId, "knowledge", "registry.json");
  mkdirSync(join(registryPath, ".."), { recursive: true });
  writeFileSync(
    registryPath,
    typeof registryRaw === "string" ? registryRaw : JSON.stringify(registryRaw, null, 2),
    "utf-8",
  );
  return registryPath;
}

function packConfig(active: string): Record<string, unknown> {
  return {
    schema_version: "knowledge_config.v1",
    active,
    installed: [
      {
        pack_id: active,
        pack_version: "1.0.0",
        display_name: "示例知识包",
        author: "Example community",
        installed_at: "2026-09-20T00:00:00Z",
        has_mapping: false,
      },
    ],
  };
}

/** Minimal valid v3-shaped third-party pack registry (inline fixture). */
function packRegistryFixture(registryVersion: string): Record<string, unknown> {
  return {
    schema_version: "coach_knowledge_registry.v3",
    registry_version: registryVersion,
    signal_aliases: {},
    sources: [
      {
        source_ref: "community.example-guide",
        source_level: "community_consensus",
        title: "Example community guide",
        author_or_org: "Example community",
        published_at: null,
        retrieved_at: "2026-09-20",
        locator: "https://example.invalid/guide",
        applicability: ["all_families"],
        supports_sections: ["definition", "scope", "expected_direction", "mechanisms"],
      },
    ],
    entries: [
      {
        entry_id: "community.example-note",
        entry_version: 1,
        status: "active",
        category: "mechanism",
        topics: ["example.topic"],
        signals: ["sparc low"],
        metric_refs: ["metric:sparc"],
        family_scope: ["static_clicking"],
        observation_refs: [],
        quality_prerequisites: [],
        definition: {
          section_ref: "community.example-note.definition",
          claim_level: "community_consensus",
          source_refs: ["community.example-guide"],
          text: "Example definition text.",
        },
        scope: {
          section_ref: "community.example-note.scope",
          claim_level: "community_consensus",
          source_refs: ["community.example-guide"],
          text: "Scope text.",
        },
        expected_direction: {
          section_ref: "community.example-note.expected-direction",
          claim_level: "community_consensus",
          source_refs: ["community.example-guide"],
          text: "higher_better",
        },
        mechanisms: [
          {
            section_ref: "community.example-note.mechanisms",
            claim_level: "community_consensus",
            source_refs: ["community.example-guide"],
            text: "Example mechanism.",
          },
        ],
        alternative_explanations: ["Alternative explanation."],
        forbidden_inferences: ["Forbidden inference."],
        limitations: ["Example limitation."],
        counterevidence: ["Example counterevidence."],
        sources: ["community.example-guide"],
        supported_uses: ["explanation_only"],
      },
    ],
  };
}

test("a missing config resolves the official registry by default", () => {
  rmSync(configPath(), { force: true });
  assert.deepEqual(resolveActiveKnowledge(), { mode: "official" });
  assert.equal(lastActiveKnowledgeFallbackReason(), null);
  assert.equal(
    loadActiveKnowledgeRegistry().registry_version,
    loadKnowledgeRegistry().registry_version,
  );
});

test("a broken config degrades to official with a recorded reason", () => {
  writeConfig("{ not json");
  assert.deepEqual(resolveActiveKnowledge(), { mode: "official" });
  assert.equal(lastActiveKnowledgeFallbackReason(), "knowledge config is not valid JSON");
  assert.equal(
    loadActiveKnowledgeRegistry().registry_version,
    loadKnowledgeRegistry().registry_version,
  );
  assert.equal(lastActiveKnowledgeFallbackReason(), "knowledge config is not valid JSON");
});

test("an unsupported config schema_version degrades to official", () => {
  writeConfig({ ...packConfig("com.example.good"), schema_version: "knowledge_config.v2" });
  assert.deepEqual(resolveActiveKnowledge(), { mode: "official" });
  assert.equal(lastActiveKnowledgeFallbackReason(), "knowledge config schema_version is unsupported");
});

test("an invalid active field degrades to official", () => {
  writeConfig({ ...packConfig("com.example.good"), active: 42 });
  assert.deepEqual(resolveActiveKnowledge(), { mode: "official" });
  assert.equal(lastActiveKnowledgeFallbackReason(), "knowledge config active field is invalid");
});

test("an active pointer to a pack that is not installed degrades to official", () => {
  writeConfig(packConfig("com.example.ghost"));
  assert.deepEqual(resolveActiveKnowledge(), { mode: "official" });
  assert.equal(
    lastActiveKnowledgeFallbackReason(),
    "active knowledge pack is not installed: com.example.ghost",
  );
});

test("an installed pack without a registry file degrades to official", () => {
  // Install the directory but strip the registry file: manifest present,
  // knowledge/registry.json missing.
  const registryPath = installPack("com.example.empty", packRegistryFixture("com.example.empty@1.0.0"));
  rmSync(registryPath, { force: true });
  writeConfig(packConfig("com.example.empty"));
  assert.deepEqual(resolveActiveKnowledge(), { mode: "official" });
  assert.equal(
    lastActiveKnowledgeFallbackReason(),
    "active knowledge pack registry is missing: com.example.empty",
  );
});

test("a configured pack resolves and loads its own registry", () => {
  installPack("com.example.good", packRegistryFixture("com.example.good@1.0.0"));
  writeConfig(packConfig("com.example.good"));
  const active = resolveActiveKnowledge();
  assert.equal(active.mode, "pack");
  if (active.mode !== "pack") throw new Error("expected pack mode");
  assert.equal(active.packId, "com.example.good");
  assert.ok(active.registryPath.endsWith(join("knowledge-packs", "com.example.good", "knowledge", "registry.json")));
  assert.equal(lastActiveKnowledgeFallbackReason(), null);
  assert.equal(loadActiveKnowledgeRegistry().registry_version, "com.example.good@1.0.0");
  assert.equal(lastActiveKnowledgeFallbackReason(), null);
});

test("a corrupt active pack falls back to the official registry with a logged reason", () => {
  installPack("com.example.bad", "{ broken json");
  writeConfig(packConfig("com.example.bad"));
  // Resolution still reports pack mode: the fallback is a load-time decision.
  assert.equal(resolveActiveKnowledge().mode, "pack");

  const errors: string[] = [];
  const originalError = console.error;
  console.error = (...args: unknown[]) => { errors.push(args.map(String).join(" ")); };
  let registry: ReturnType<typeof loadActiveKnowledgeRegistry>;
  try {
    registry = loadActiveKnowledgeRegistry();
  } finally {
    console.error = originalError;
  }
  assert.equal(registry.registry_version, loadKnowledgeRegistry().registry_version);
  assert.equal(
    lastActiveKnowledgeFallbackReason(),
    "active knowledge pack registry is invalid: com.example.bad",
  );
  assert.ok(
    errors.some((line) => line.includes("com.example.bad")),
    `fallback must be logged with the pack id: ${JSON.stringify(errors)}`,
  );
});

test("an explicit official active resolves the official registry", () => {
  installPack("com.example.good", packRegistryFixture("com.example.good@1.0.0"));
  writeConfig({ ...packConfig("com.example.good"), active: "official" });
  assert.deepEqual(resolveActiveKnowledge(), { mode: "official" });
  assert.equal(lastActiveKnowledgeFallbackReason(), null);
  assert.equal(
    loadActiveKnowledgeRegistry().registry_version,
    loadKnowledgeRegistry().registry_version,
  );
});

test("activePackDisplayName reads the installed entry from the config", () => {
  writeConfig(packConfig("com.example.good"));
  assert.equal(activePackDisplayName("com.example.good"), "示例知识包");
  assert.equal(activePackDisplayName("com.example.other"), undefined);
});
