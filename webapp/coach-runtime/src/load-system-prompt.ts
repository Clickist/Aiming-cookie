import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SOURCE_PROMPT_FILE = join(dirname(fileURLToPath(import.meta.url)), "..", "prompts", "coach-system.md");

function promptFile(): string {
  const resourceRoot = process.env.AIMING_COOKIE_RESOURCE_ROOT?.trim();
  return resourceRoot ? resolve(resourceRoot, "coach-system.md") : SOURCE_PROMPT_FILE;
}

export function loadDefaultCoachSystemPrompt(): string {
  return readFileSync(promptFile(), "utf8").trim();
}

export function resolveSystemPrompt(requestPrompt?: string): string {
  const trimmed = requestPrompt?.trim();
  return trimmed && trimmed.length > 0 ? trimmed : loadDefaultCoachSystemPrompt();
}
