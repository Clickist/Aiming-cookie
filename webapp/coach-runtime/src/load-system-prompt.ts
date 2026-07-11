import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const PROMPT_FILE = join(dirname(fileURLToPath(import.meta.url)), "..", "prompts", "coach-system.md");

export function loadDefaultCoachSystemPrompt(): string {
  return readFileSync(PROMPT_FILE, "utf8").trim();
}

export function resolveSystemPrompt(requestPrompt?: string): string {
  const trimmed = requestPrompt?.trim();
  return trimmed && trimmed.length > 0 ? trimmed : loadDefaultCoachSystemPrompt();
}