import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";

// Keep the pinned Pi modules as static imports so Bun can embed them in the
// release sidecar. Development still uses PI_SOURCE_DIR for package identity
// checks, but the packaged Coach no longer needs a source checkout at runtime.
import * as PiAi from "../../../third_party/pi/packages/ai/src/index.ts";
import * as PiProviders from "../../../third_party/pi/packages/ai/src/providers/all.ts";
import * as PiOpenAiCompletions from "../../../third_party/pi/packages/ai/src/api/openai-completions.ts";
import * as PiAnthropicMessages from "../../../third_party/pi/packages/ai/src/api/anthropic-messages.lazy.ts";
import * as PiAgent from "../../../third_party/pi/packages/agent/src/index.ts";

export function piSourceRoot(): string {
  const resourceRoot = process.env.AIMING_COOKIE_RESOURCE_ROOT?.trim();
  if (resourceRoot) return resolve(resourceRoot, "pi");

  const root = process.env.PI_SOURCE_DIR;
  if (!root) {
    throw new Error("PI_SOURCE_DIR is required and must point to the pinned Pi source checkout");
  }
  return root;
}

export async function loadPiAi(): Promise<Record<string, unknown>> {
  return PiAi as Record<string, unknown>;
}

export async function loadPiProvidersAll(): Promise<Record<string, unknown>> {
  return PiProviders as Record<string, unknown>;
}

export async function loadPiOpenAiCompletions(): Promise<Record<string, unknown>> {
  return PiOpenAiCompletions as Record<string, unknown>;
}

export async function loadPiAnthropicMessages(): Promise<Record<string, unknown>> {
  return PiAnthropicMessages as Record<string, unknown>;
}

export async function loadPiAgent(): Promise<Record<string, unknown>> {
  return PiAgent as Record<string, unknown>;
}

export function readPinnedAgentPackageVersion(): string {
  const pkgPath = join(piSourceRoot(), "packages", "agent", "package.json");
  const pkg = JSON.parse(readFileSync(pkgPath, "utf8")) as { name?: string; version?: string };
  if (pkg.name !== "@earendil-works/pi-agent-core" || typeof pkg.version !== "string") {
    throw new Error("Pinned Pi agent package identity mismatch");
  }
  return pkg.version;
}
