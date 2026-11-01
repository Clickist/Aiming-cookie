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

import { ACTION_STEPS, defineComponent, steps, useIsStreaming, useTriggerAction, type ActionPlan } from "@openuidev/react-lang";
import { Button as OpenUIButton } from "@openuidev/react-ui/Button";
import { openuiLibrary } from "@openuidev/react-ui/genui-lib";
import { createContext, useContext, type CSSProperties, type ReactNode } from "react";
import { z } from "zod/v4";

import { CoachMessageText } from "@/components/task7/CoachMessageText";
import {
  COACH_OPENUI_LIBRARY_ID,
  COACH_OPENUI_LIBRARY_VERSION,
  isCoachUiActionMessage,
} from "./presentation";

/**
 * 禁入清单（合同 §5.3，1102 由白名单制反转为黑名单制）。
 *
 * 原则：基底库（@openuidev/react-ui genui-lib）的全量组件默认全部放行——
 * 图表全家（饼/散点/面积/雷达/横向柱/环形/堆叠）、表格、标签页、手风琴、
 * 步骤条、标签、列表、卡片族、Image 三件套、表单输入族、Modal 等都可用；
 * 上游新增的安全组件自动生效，不再逐个审批。
 *
 * 唯一禁入 = MarkDownRenderer：它渲染任意 HTML/脚本，而本 app 的 CSP 不限
 * script-src 且 webview 持有 Tauri IPC 桥（文件/桌面令牌），注入脚本等同
 * 应用接管。文本一律走 RestrictedTextContent/RestrictedCallout 的消毒器
 * （CoachMessageText 受限节点树）。动作层另有双重闸：RestrictedButton/
 * RestrictedIconButton 只放 @ToAssistant，宿主 onAction 只认
 * continue_conversation；Query/Mutation 语句由 deriveOpenUIRenderState
 * 的 hasForbiddenCalls 拒收。
 */
export const OPENUI_BLOCKLIST = ["MarkDownRenderer"] as const;

export type OpenUIBlockedComponent = (typeof OPENUI_BLOCKLIST)[number];

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

/**
 * 受限 IconButton：与 RestrictedButton 同款动作闸。基底 IconButton 的动作面
 * 不加限制，故以覆盖件形式进库——渲染基底组件本身，但把非 @ToAssistant 的
 * action 摘掉（摘掉后基底渲染为无动作按钮，点击无效果）。宿主 onAction
 * 只认 continue_conversation，是第二道闸。
 */
const baseIconButton = openuiLibrary.components["IconButton"]!;
const BaseIconButton = baseIconButton.component as unknown as (
  renderProps: unknown,
) => ReactNode;
const RestrictedIconButton = defineComponent({
  name: "IconButton",
  props: baseIconButton.props,
  description:
    "Icon-only clickable button. action may only be @ToAssistant(message); the click starts a new assistant turn after user gesture. Buttons without an action do nothing.",
  component: (renderProps) => {
    const plan = renderProps.props.action as unknown as ActionPlan | undefined;
    const actionAllowed =
      !plan?.steps ||
      (plan.steps.length === 1 && plan.steps[0]?.type === ACTION_STEPS.ToAssistant);
    if (actionAllowed) return BaseIconButton(renderProps);
    return BaseIconButton({
      ...renderProps,
      props: { ...renderProps.props, action: undefined },
    });
  },
});

/** 唯一移除 = MarkDownRenderer（任意 HTML/脚本）；其余全量放行。 */
const restrictedBase = openuiLibrary.extend({
  components: {
    remove: [...OPENUI_BLOCKLIST],
    override: [RestrictedTextContent, RestrictedCallout, RestrictedButton, RestrictedIconButton],
  },
});

/**
 * coach 库 = 基底库全量组件（黑名单制，仅移除 MarkDownRenderer）+
 * 四个受限覆盖件（TextContent/Callout 走消毒器、Button/IconButton 只放
 * @ToAssistant）。分组沿用基底库自带分组；基底 root 已是 Stack，id 由
 * 常量 COACH_OPENUI_LIBRARY_ID 兜底（spec.id 为空时 skill-reference 回退）。
 */
export const coachOpenUILibrary = restrictedBase;

/** 运行时与提示词共用的库身份（导出只读常量，避免调用方各自硬编码）。 */
export const OPENUI_LIBRARY_ID = COACH_OPENUI_LIBRARY_ID;
export const OPENUI_LIBRARY_VERSION = COACH_OPENUI_LIBRARY_VERSION;
