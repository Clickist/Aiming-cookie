import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

export function piSourceRoot(): string {
  const root = process.env.PI_SOURCE_DIR;
  if (!root) {
    throw new Error("PI_SOURCE_DIR is required and must point to the pinned Pi source checkout");
  }
  return root;
}

function sourceModule(...parts: string[]): string {
  return pathToFileURL(join(piSourceRoot(), ...parts)).href;
}

export async function loadPiAi(): Promise<Record<string, unknown>> {
  return (await import(sourceModule("packages", "ai", "src", "index.ts"))) as Record<string, unknown>;
}

export async function loadPiProvidersAll(): Promise<Record<string, unknown>> {
  return (await import(sourceModule("packages", "ai", "src", "providers", "all.ts"))) as Record<
    string,
    unknown
  >;
}

export async function loadPiOpenAiCompletions(): Promise<Record<string, unknown>> {
  return (await import(sourceModule("packages", "ai", "src", "api", "openai-completions.ts"))) as Record<
    string,
    unknown
  >;
}

export async function loadPiAnthropicMessages(): Promise<Record<string, unknown>> {
  return (await import(sourceModule("packages", "ai", "src", "api", "anthropic-messages.lazy.ts"))) as Record<
    string,
    unknown
  >;
}

export async function loadPiAgent(): Promise<Record<string, unknown>> {
  return (await import(sourceModule("packages", "agent", "src", "index.ts"))) as Record<string, unknown>;
}

export function readPinnedAgentPackageVersion(): string {
  const pkgPath = join(piSourceRoot(), "packages", "agent", "package.json");
  const pkg = JSON.parse(readFileSync(pkgPath, "utf8")) as { name?: string; version?: string };
  if (pkg.name !== "@earendil-works/pi-agent-core" || typeof pkg.version !== "string") {
    throw new Error("Pinned Pi agent package identity mismatch");
  }
  return pkg.version;
}
