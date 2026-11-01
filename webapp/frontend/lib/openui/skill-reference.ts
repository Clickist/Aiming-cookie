/**
 * data-visuals skill 的组件参考生成器。
 *
 * 唯一事实源是 `coachOpenUILibrary.toSpec()`（即前端 Renderer 使用的同一份
 * library schema）。这里只做确定性的 Markdown 投影，禁止手写组件签名；
 * `scripts/sync-openui-skill-reference.mjs` 把生成块写回 data-visuals/SKILL.md，
 * 漂移由 `tests/openui-skill-reference.test.ts` 逐字校验。
 *
 * 不直接使用 OpenUI 的 `library.prompt()`：它固定附带 Query/Mutation、
 * @OpenUrl、@Run 等本产品未开放的通用动作说明，与本合同的白名单冲突。
 */

import type { Library } from "@openuidev/react-lang";

import { COACH_OPENUI_LIBRARY_ID, COACH_OPENUI_LIBRARY_VERSION } from "./presentation";

export const OPENUI_SKILL_REFERENCE_BEGIN =
  "<!-- BEGIN GENERATED: coach-openui component reference (scripts/sync-openui-skill-reference.mjs) -->";
export const OPENUI_SKILL_REFERENCE_END =
  "<!-- END GENERATED: coach-openui component reference -->";

/**
 * 合同 §5.3 不允许出现在模型指南里的动作/工具层标记（1102 黑名单制后：
 * 组件全量放行，禁的还是动作与工具层——Form/Input/Select/Image 等组件
 * 本身已合法，其签名不再算违规标记）。
 */
export const OPENUI_UNSUPPORTED_PROMPT_TOKENS = [
  "@OpenUrl",
  "@Run",
  "@Set",
  "@Reset",
  "Action([",
  "Query(",
  "Mutation(",
  "$binding",
] as const;

/**
 * 从 schema 生成的签名里去掉本产品未开放的 `$binding` 槽位。
 * 只影响模型指南，不改变运行时 schema；其它参数原样保留。
 */
export function restrictOpenUIPromptSignature(signature: string): string {
  return signature
    .replace(/\s*,\s*[A-Za-z_$][\w$]*\??:\s*\$binding<[^>]*>/g, "")
    .replace(/\s*[A-Za-z_$][\w$]*\??:\s*\$binding<[^>]*>\s*,?/g, "")
    .replace(/\s*,\s*\)/g, ")")
    .replace(/\s{2,}/g, " ");
}

function assertPromptSafe(text: string): void {
  for (const token of OPENUI_UNSUPPORTED_PROMPT_TOKENS) {
    if (text.includes(token)) {
      throw new Error(`OpenUI skill reference contains unsupported prompt token: ${token}`);
    }
  }
}

type LibrarySpec = ReturnType<Library["toSpec"]>;

function groupedComponents(spec: LibrarySpec): Array<{ name: string; components: string[] }> {
  const seen = new Set<string>();
  const groups = (spec.componentGroups ?? [])
    .map((group) => {
      const components = group.components.filter((name) => {
        if (!spec.components[name] || seen.has(name)) return false;
        seen.add(name);
        return true;
      });
      return { name: group.name, components };
    })
    .filter((group) => group.components.length > 0);
  const remaining = Object.keys(spec.components)
    .filter((name) => !seen.has(name))
    .sort();
  if (remaining.length > 0) groups.push({ name: "Other", components: remaining });
  return groups;
}

/** 可直接渲染的完整示例；测试会用同一 library schema 解析它。 */
function openUISkillExample(root: string): string {
  return `root = ${root}([card, actions])
card = Card([title, metric, chart])
title = CardHeader("射击结果（单位：发）")
metric = MetricIndicatorInline("760 / 939", "命中发数 / 总射击发数")
chart = BarChart(["命中", "未命中"], [Series("发数", [760, 179])])
actions = Buttons([ask])
ask = Button("逐发看这 179 发未命中", @ToAssistant("逐发分析这 179 发未命中的原因"))`;
}

export function renderOpenUISkillReference(library: Library): string {
  const spec = library.toSpec();
  const root = spec.root ?? "Root";
  const libraryId = spec.id ?? COACH_OPENUI_LIBRARY_ID;

  const sections = groupedComponents(spec).map((group) => {
    const lines = [`### ${group.name}`];
    for (const name of group.components) {
      const component = spec.components[name]!;
      const signature = restrictOpenUIPromptSignature(component.signature);
      lines.push(
        component.description
          ? `- \`${signature}\` — ${component.description}`
          : `- \`${signature}\``,
      );
    }
    return lines.join("\n");
  });

  const block = `${OPENUI_SKILL_REFERENCE_BEGIN}
<!-- Generated from webapp/frontend/lib/openui/library.tsx. Do not hand-edit. -->
库：\`${libraryId}@${COACH_OPENUI_LIBRARY_VERSION}\`；根组件：\`${root}\`。

硬性格式（与运行时 Renderer 同一份 library schema）：
- 第一行必须是 \`root = ${root}([...])\`；没有 root，整段不会渲染。
- 参数一律按位置写，禁止 \`名字 = 值\`。可选参数只能从末尾省略；如果签名里可选参数后面还有必填参数，这个可选值也必须写。
- 每条语句一行：\`名字 = 组件(...)\`；允许先引用后定义，但所有定义都必须能挂到 root 上。
- 数字只能来自 Coach 本轮真实读到的数据；不在 DSL 里重算、合计或编造指标。

可用组件（签名由 library schema 生成，参数顺序即位置顺序）：

${sections.join("\n\n")}

动作限制：\`Button\` 的 \`action\` 只能是 \`@ToAssistant("追问内容")\`；除此之外的动作、表单、输入、查询与改写工具都不可用。

完整示例（可直接渲染；注意 root 和位置参数）：
\`\`\`openui-lang
${openUISkillExample(root)}
\`\`\`
${OPENUI_SKILL_REFERENCE_END}`;

  assertPromptSafe(block);
  return block;
}

/** 替换 SKILL.md 里的生成块；标记缺失时直接失败，避免静默跳过。 */
export function replaceOpenUISkillReference(skillMarkdown: string, block: string): string {
  const start = skillMarkdown.indexOf(OPENUI_SKILL_REFERENCE_BEGIN);
  const end = skillMarkdown.indexOf(OPENUI_SKILL_REFERENCE_END);
  if (start === -1 || end === -1 || end < start) {
    throw new Error(
      `data-visuals/SKILL.md 缺少生成块标记：${OPENUI_SKILL_REFERENCE_BEGIN} ... ${OPENUI_SKILL_REFERENCE_END}`,
    );
  }
  return `${skillMarkdown.slice(0, start)}${block}${skillMarkdown.slice(end + OPENUI_SKILL_REFERENCE_END.length)}`;
}
