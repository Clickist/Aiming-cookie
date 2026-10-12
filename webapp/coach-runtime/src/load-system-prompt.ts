import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { coachEnvFacts, describeCoachEnvFacts } from "./coach-env-facts.ts";

const SOURCE_PROMPT_FILE = join(dirname(fileURLToPath(import.meta.url)), "..", "prompts", "coach-system.md");

function promptFile(): string {
  const resourceRoot = process.env.AIMING_COOKIE_RESOURCE_ROOT?.trim();
  return resourceRoot ? resolve(resourceRoot, "coach-system.md") : SOURCE_PROMPT_FILE;
}

/**
 * TS-5 实测（2026-10-11）：本函数此前每调用一次都同步 readFileSync（26KB，
 * 实测 ~0.3ms/次），落在每回合 turn 热路径；turn.ts 的注释「此处命中缓存」
 * 只指 env 探测（coach-env-facts），提示词本体从未缓存。提示词是打包资源，
 * 进程内不变——按解析路径缓存（release-resource-paths 合同会切换
 * AIMING_COOKIE_RESOURCE_ROOT，按路径缓存保住 env 切换语义，每路径至多读一次）。
 */
const promptCache = new Map<string, string>();

export function loadDefaultCoachSystemPrompt(): string {
  const path = promptFile();
  const cached = promptCache.get(path);
  if (cached !== undefined) return cached;
  const prompt = readFileSync(path, "utf8").trim();
  promptCache.set(path, prompt);
  return prompt;
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
