import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { loadPiAi, loadPiAgent } from "./pi-source.ts";

type TypeBuilder = {
  Object(properties: Record<string, unknown>, options?: Record<string, unknown>): unknown;
  Optional(schema: unknown): unknown;
  String(options?: Record<string, unknown>): unknown;
};

const { Type } = (await loadPiAi()) as { Type: TypeBuilder };
const PiAgent = await loadPiAgent();
const formatSkillsForSystemPrompt = PiAgent.formatSkillsForSystemPrompt as
  (skills: SkillEntry[]) => string;
const formatSkillInvocation = PiAgent.formatSkillInvocation as
  (skill: SkillEntry, additionalInstructions?: string) => string;

export interface SkillEntry {
  name: string;
  description: string;
  content: string;
  filePath: string;
  disableModelInvocation?: boolean;
}

const SOURCE_SKILLS_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "prompts", "skills");

function skillsDir(): string {
  const resourceRoot = process.env.AIMING_COOKIE_RESOURCE_ROOT?.trim();
  return resourceRoot ? resolve(resourceRoot, "skills") : SOURCE_SKILLS_DIR;
}

function parseFrontmatter(text: string): { frontmatter: Record<string, unknown>; body: string } {
  const match = /^---\n([\s\S]*?)\n---\n?([\s\S]*)$/.exec(text);
  if (!match) return { frontmatter: {}, body: text.trim() };
  const frontmatter: Record<string, unknown> = {};
  for (const line of match[1].split("\n")) {
    const colonIdx = line.indexOf(":");
    if (colonIdx === -1) continue;
    const key = line.slice(0, colonIdx).trim();
    const value = line.slice(colonIdx + 1).trim();
    frontmatter[key] = value === "true" ? true : value === "false" ? false : value;
  }
  return { frontmatter, body: match[2].trim() };
}

let cachedSkills: SkillEntry[] | null = null;

export function loadAllSkills(): SkillEntry[] {
  if (cachedSkills !== null) return cachedSkills;
  const dir = skillsDir();
  const skills: SkillEntry[] = [];
  try {
    for (const entry of readdirSync(dir).sort()) {
      const fullPath = join(dir, entry);
      if (!statSync(fullPath).isDirectory()) continue;
      const skillFile = join(fullPath, "SKILL.md");
      try {
        const raw = readFileSync(skillFile, "utf-8");
        const { frontmatter, body } = parseFrontmatter(raw);
        const description = String(frontmatter.description ?? "").trim();
        if (!description) continue;
        skills.push({
          name: String(frontmatter.name ?? entry),
          description,
          content: body,
          filePath: skillFile,
          disableModelInvocation: frontmatter["disable-model-invocation"] === true,
        });
      } catch {
        // No SKILL.md in this directory — skip
      }
    }
  } catch {
    // Skills directory doesn't exist — no skills
  }
  cachedSkills = skills;
  return skills;
}

export function skillsSystemPromptBlock(): string {
  const skills = loadAllSkills().filter((s) => !s.disableModelInvocation);
  if (skills.length === 0) return "";
  return formatSkillsForSystemPrompt(skills);
}

export function createSkillLoaderTool() {
  return {
    name: "load_skill",
    label: "Load skill reference",
    description: "加载知识参考文档。可用的 skill 见系统提示中的 available_skills 列表。传入 name 加载对应文档的完整内容。",
    parameters: Type.Object({
      name: Type.String({ maxLength: 64 }),
    }, { additionalProperties: false }),
    async execute(_toolCallId: string, params: { name: string }) {
      const skills = loadAllSkills();
      const skill = skills.find((s) => s.name === params.name);
      if (!skill) {
        return {
          content: [{ type: "text" as const, text: `未找到 skill: ${params.name}` }],
          details: { event: { type: "skill_not_found", name: params.name } },
        };
      }
      const text = formatSkillInvocation(skill);
      return {
        content: [{ type: "text" as const, text }],
        details: { event: { type: "skill_loaded", name: skill.name, bytes: text.length } },
      };
    },
  };
}
