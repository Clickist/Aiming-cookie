import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { loadPiAi } from "./pi-source.ts";

type TypeBuilder = {
  Object(properties: Record<string, unknown>, options?: Record<string, unknown>): unknown;
};

const { Type } = (await loadPiAi()) as { Type: TypeBuilder };

const SOURCE_FILE = join(dirname(fileURLToPath(import.meta.url)), "..", "prompts", "peripheral-reference.md");

let cachedReference: string | null = null;

function loadPeripheralReference(): string {
  if (cachedReference !== null) return cachedReference;
  cachedReference = readFileSync(SOURCE_FILE, "utf-8");
  return cachedReference;
}

export function createPeripheralReferenceTool() {
  return {
    name: "get_peripheral_reference",
    label: "Get peripheral recommendation reference",
    description: "当用户主动讨论外设（鼠标、鼠标垫、脚贴、握姿、换设备等）时调用此工具，获取外设推荐的知识参考，包括握姿光谱与鼠标需求、EloShapes 字段映射、分析模式到外设方向、推荐链路、硬规则和教育内容。",
    parameters: Type.Object({}, { additionalProperties: false }),
    async execute() {
      const text = loadPeripheralReference();
      return {
        content: [{ type: "text" as const, text }],
        details: {
          event: {
            type: "peripheral_reference",
            source: "peripheral-reference.md",
            bytes: text.length,
          },
        },
      };
    },
  };
}
