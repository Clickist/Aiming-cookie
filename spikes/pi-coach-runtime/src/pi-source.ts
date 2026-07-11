import { join } from "node:path";
import { pathToFileURL } from "node:url";

function sourceRoot(): string {
  const root = process.env.PI_SOURCE_DIR;
  if (!root) {
    throw new Error("PI_SOURCE_DIR is required and must point to the pinned Pi source checkout");
  }
  return root;
}

function sourceModule(...parts: string[]): string {
  return pathToFileURL(join(sourceRoot(), ...parts)).href;
}

export async function loadPiAi(): Promise<Record<string, unknown>> {
  return (await import(sourceModule("packages", "ai", "src", "index.ts"))) as Record<string, unknown>;
}

export async function loadPiAgent(): Promise<Record<string, unknown>> {
  return (await import(sourceModule("packages", "agent", "src", "index.ts"))) as Record<string, unknown>;
}
