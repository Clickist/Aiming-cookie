import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { coachEnvFacts, describeCoachEnvFacts } from "./coach-env-facts.ts";

const SOURCE_PROMPT_FILE = join(dirname(fileURLToPath(import.meta.url)), "..", "prompts", "coach-system.md");

function promptFile(): string {
  const resourceRoot = process.env.AIMING_COOKIE_RESOURCE_ROOT?.trim();
  return resourceRoot ? resolve(resourceRoot, "coach-system.md") : SOURCE_PROMPT_FILE;
}

export function loadDefaultCoachSystemPrompt(): string {
  return readFileSync(promptFile(), "utf8").trim();
}

/**
 * 默认提示词 + 本机环境实测（0911 点点纠偏：应用分发到任意用户机器，
 * bash/python/node/jq 的有无因机器而异，不能写死在提示词正文里）。
 * 探测在 sidecar 启动时就开始热身，这里只等结果；探测失败按全不可用呈现。
 */
export async function resolveSystemPromptWithEnvFacts(requestPrompt?: string): Promise<string> {
  const trimmed = requestPrompt?.trim();
  if (trimmed && trimmed.length > 0) return trimmed;
  const facts = await coachEnvFacts();
  return `${loadDefaultCoachSystemPrompt()}\n\n${describeCoachEnvFacts(facts)}`;
}

export function resolveSystemPrompt(requestPrompt?: string): string {
  const trimmed = requestPrompt?.trim();
  return trimmed && trimmed.length > 0 ? trimmed : loadDefaultCoachSystemPrompt();
}
