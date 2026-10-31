"use client";

/**
 * Coach 受限 OpenUI 组件库（ARCHITECTURE §5.3 第一刀白名单）。
 *
 * 策略：以 react-ui 官方 `openuiLibrary` 为基底，用 `extend()` 删除一切
 * 白名单外组件（Image/ImageGallery/Form/Input/Query/Mutation/Table/...），
 * 再 override 三个需要安全适配的组件：
 * - TextContent / Callout：内部 Markdown 渲染替换为前端受限 rich-text
 *   （复用 `lib/rich-text` 的解析与 `CoachMessageText` 的受限呈现），
 *   不引入图片、任意外链或 HTML 通道；
 * - Button：action 槽收紧为只接受 @ToAssistant，并按生成/渲染状态禁用。
 *
 * library_id + library_version 是运行时 Renderer 与提示词生成的单一来源；
 * 数值必须与 sidecar `src/presentation/types.ts` 的冻结值一致。
 */

import { ACTION_STEPS, createLibrary, defineComponent, steps, useIsStreaming, useTriggerAction, type ActionPlan, type ComponentGroup, type DefinedComponent } from "@openuidev/react-lang";
import { Button as OpenUIButton } from "@openuidev/react-ui/Button";
import { openuiLibrary } from "@openuidev/react-ui/genui-lib";
import { createContext, useContext, type CSSProperties } from "react";
import { z } from "zod/v4";

import { CoachMessageText } from "@/components/task7/CoachMessageText";
import {
  COACH_OPENUI_LIBRARY_ID,
  COACH_OPENUI_LIBRARY_VERSION,
  isCoachUiActionMessage,
} from "./presentation";

/** 第一刀白名单（合同 §5.3）。 */
export const OPENUI_WHITELIST = [
  "Card",
  "CardHeader",
  "Stack",
  "TextContent",
  "BoldText",
  "MetricIndicatorInline",
  "Callout",
  "BarChart",
  "LineChart",
  "Series",
  "Buttons",
  "Button",
] as const;

export type OpenUIWhitelistComponent = (typeof OPENUI_WHITELIST)[number];

/**
 * @ToAssistant 按钮可用性闸门：默认关闭。只有展示组件在
 * generation_status=completed 且局部渲染状态 ready 时置 true。
 */
export const CoachOpenUIActionsEnabledContext = createContext(false);

const BODY_SIZE_VARS: Record<string, string> = {
  small: "--openui-text-body-sm",
  default: "--openui-text-body-default",
  large: "--openui-text-body-lg",
  "small-heavy": "--openui-text-body-sm-heavy",
  "large-heavy": "--openui-text-body-lg-heavy",
};

const baseTextContent = openuiLibrary.components["TextContent"]!;
const baseCallout = openuiLibrary.components["Callout"]!;

/**
 * 受限 TextContent：排版尺寸沿用 OpenUI 合同，正文走 CoachMessageText
 * 的受限节点树（GFM 表格/列表/加粗/引用块/@time），不经过 MarkDownRenderer。
 */
const RestrictedTextContent = defineComponent({
  name: "TextContent",
  props: baseTextContent.props,
  description:
    "Text block rendered with the host's restricted rich text (bold, lists, tables, blockquotes). No images, raw HTML, or arbitrary links. Optional size: small | default | large | small-heavy | large-heavy.",
  component: ({ props }) => {
    const size = typeof props.size === "string" ? props.size : "default";
    const varName = BODY_SIZE_VARS[size] ?? BODY_SIZE_VARS["default"]!;
    const style =
      size === "default"
        ? undefined
        : ({
            "--openui-text-body-default": `var(${varName})`,
            "--openui-text-body-default-letter-spacing": `var(${varName}-letter-spacing)`,
          } as CSSProperties);
    return (
      <div className="openui-text-content" style={style}>
        <CoachMessageText text={props.text == null ? "" : String(props.text)} />
      </div>
    );
  },
});

const CALLOUT_VARIANTS: Record<string, string> = {
  info: "info",
  warning: "warning",
  success: "success",
  error: "warning",
  neutral: "neutral",
};

/**
 * 受限 Callout：标题保持纯文本；description 走受限 rich-text 呈现，
 * 不再调用 react-ui 的 MarkDownRenderer（禁止任意链接/图片/HTML）。
 */
const RestrictedCallout = defineComponent({
  name: "Callout",
  props: baseCallout.props,
  description:
    "Callout banner. variant: info | warning | error | success | neutral. Description uses restricted rich text (no images or arbitrary links).",
  component: ({ props }) => {
    const variant = CALLOUT_VARIANTS[String(props.variant ?? "")] ?? "info";
    return (
      <div className={`openui-callout openui-callout-${variant}`}>
        {props.title ? (
          <span className="openui-callout-title">{String(props.title)}</span>
        ) : null}
        <div className="openui-callout-description">
          <CoachMessageText text={props.description == null ? "" : String(props.description)} />
        </div>
      </div>
    );
  },
});

/**
 * 第一刀动作槽：只允许 @ToAssistant（单个动作步骤）。@OpenUrl / Action([...])
 * 在解析期即被判为类型不符，不会进入运行时。
 */
const RestrictedButtonSchema = z.object({
  label: z.string(),
  action: steps.ToAssistant.ref.optional(),
  variant: z.enum(["primary", "secondary", "tertiary"]).optional(),
  type: z.enum(["normal", "destructive"]).optional(),
  size: z.enum(["extra-small", "small", "medium", "large"]).optional(),
});

const VARIANT_MAP: Record<string, "primary" | "secondary" | "tertiary"> = {
  primary: "primary",
  secondary: "secondary",
  ghost: "tertiary",
  tertiary: "tertiary",
};

/**
 * 受限 Button：@ToAssistant 手势 = 用户点击发起新回合。流式、降级、中断
 * 三种状态下按钮禁用（gate 默认 false）；历史重放不会自动触发点击。
 */
const RestrictedButton = defineComponent({
  name: "Button",
  props: RestrictedButtonSchema,
  description:
    "Clickable button. action may only be @ToAssistant(message); the click starts a new assistant turn after user gesture. Buttons without an action send their label.",
  component: ({ props }) => {
    const triggerAction = useTriggerAction();
    const isStreaming = useIsStreaming();
    const actionsEnabled = useContext(CoachOpenUIActionsEnabledContext);
    const label = props.label == null ? "" : String(props.label);
    // 即使模型绕过了 schema 写进 @OpenUrl/@Run，也只允许 @ToAssistant：
    // 非白名单动作的按钮直接禁用，点击不会进入 onAction。
    const plan = props.action as ActionPlan | undefined;
    const actionAllowed =
      !plan?.steps ||
      (plan.steps.length === 1 && plan.steps[0]?.type === ACTION_STEPS.ToAssistant);
    const actionMessage = plan?.steps?.[0]?.type === ACTION_STEPS.ToAssistant
      ? plan.steps[0].message : label;
    const disabled = isStreaming || !actionsEnabled || !actionAllowed || !isCoachUiActionMessage(actionMessage);
    return (
      <OpenUIButton
        buttonType={(props.type as "normal" | "destructive") || "normal"}
        disabled={disabled}
        onClick={() => {
          if (disabled) return;
          triggerAction(label, undefined, props.action as ActionPlan | undefined);
        }}
        size={(props.size as "extra-small" | "small" | "medium" | "large") || "medium"}
        variant={VARIANT_MAP[String(props.variant ?? "")] ?? "primary"}
      >
        {label}
      </OpenUIButton>
    );
  },
});

/** 只保留白名单组件（其余从基底库删除，父级联合类型由 extend 重建）。 */
const kept = new Set<string>(OPENUI_WHITELIST);
const removedComponents = Object.keys(openuiLibrary.components).filter(
  (name) => !kept.has(name),
);

const restrictedBase = openuiLibrary.extend({
  components: {
    remove: removedComponents,
    override: [RestrictedTextContent, RestrictedCallout, RestrictedButton],
  },
});

/**
 * 提示词只描述白名单内的组件与安全动作；notes 不得引用已删除组件
 * （Image/Form/Query/@OpenUrl 等）。
 */
export const COACH_OPENUI_COMPONENT_GROUPS: ComponentGroup[] = [
  {
    name: "Layout",
    components: ["Card", "CardHeader", "Stack"],
    notes: [
      "Card groups related content; CardHeader supplies a title/subtitle.",
      "Stack lays out children in a row or column. Use direction \"row\" with wrap for side-by-side blocks.",
    ],
  },
  {
    name: "Content",
    components: ["TextContent", "BoldText", "Callout", "MetricIndicatorInline"],
    notes: [
      "TextContent renders restricted rich text only: no images, raw HTML, or arbitrary links.",
      "Use Callout for a short caveat or status. Description is restricted rich text.",
      "MetricIndicatorInline presents a single headline metric with optional subtext/trend.",
    ],
  },
  {
    name: "Charts",
    components: ["BarChart", "LineChart", "Series"],
    notes: [
      "Charts take a labels array plus Series(name, values) entries: BarChart(labels, [series]) or LineChart(labels, [series]).",
      "Numbers must come from data the Coach has already read; never recompute official metrics in the DSL.",
      "At most 2 charts per reply.",
    ],
  },
  {
    name: "Actions",
    components: ["Buttons", "Button"],
    notes: [
      "Only @ToAssistant(message) is allowed as a Button action. No @OpenUrl, forms, inputs, Query/Mutation, or action context.",
      "Buttons are disabled while streaming, degraded, or interrupted; historical replays never auto-execute actions.",
    ],
  },
];

export const coachOpenUILibrary = createLibrary({
  id: COACH_OPENUI_LIBRARY_ID,
  root: "Stack",
  componentGroups: COACH_OPENUI_COMPONENT_GROUPS,
  components: OPENUI_WHITELIST.map(
    (name) => restrictedBase.components[name] as DefinedComponent,
  ),
});

/** 运行时与提示词共用的库身份（导出只读常量，避免调用方各自硬编码）。 */
export const OPENUI_LIBRARY_ID = COACH_OPENUI_LIBRARY_ID;
export const OPENUI_LIBRARY_VERSION = COACH_OPENUI_LIBRARY_VERSION;
