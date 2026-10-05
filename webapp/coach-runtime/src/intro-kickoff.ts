/**
 * One-time Intro Session kickoff turn text.
 *
 * The intro-session skill opens without user input. The turn still needs a
 * concrete prompt for the agent loop, so the sidecar synthesizes this internal
 * instruction. It is persisted to the Pi session (the model must have its own
 * opening instruction in context) but is filtered out of every UI read — the
 * user must never see a fake user message (wireframe 状态①).
 *
 * 双语（2026-09 i18n 收尾）：开场回合是系统代用户合成的，用户还没打字，
 * 唯一语言信号是应用语言（POST /coach/intro-session 的 X-Locale →
 * RunRecord.locale）。kickoff 按该 locale 选语言，教练随后跟随 kickoff 的
 * 语言——与「教练跟随用户消息语言」一致：kickoff 就是该回合的用户消息。
 * 哨兵前缀两种语言恒定同一个中文串：它是 UI 过滤器（isIntroKickoffMessage）
 * 按字节匹配的机器标记，永不出现在用户可见文本里。
 *
 * Leaf module: no imports, so both session-repo.ts (UI filter) and the sidecar
 * routes can share the sentinel without an import cycle.
 */

export const INTRO_KICKOFF_SENTINEL = "[开场分析自动启动]";

export const INTRO_KICKOFF_PROMPTS = {
  "zh-CN":
    `${INTRO_KICKOFF_SENTINEL} 现在开始本次「开场分析」。按 intro-session skill 的「开场即主线」发首条消息：` +
    "一句自我介绍，然后直接给主线任务（AC 需要一局真实数据才能开始帮你；现在就去打一局，还没装 KovaaK 的先装；打完回来喊我分析——这一局同时确认 AC 在你电脑上一切运作正常），末行带主线卡。" +
    "发这条的同时并行调用 intro_context.get 和 user_profile.get 拿背景数据；本地已有记录则按 skill 规则开场直接分析最近一局。" +
    "不要问任何了解用户的问题（四问在首次分析完成后才出场），也不要用任何引导用的假用户消息。",
  "en-US":
    `${INTRO_KICKOFF_SENTINEL} Start this Intro Session now. Follow the intro-session skill's "main quest from the first message" rule to send the first message: ` +
    "a one-sentence self-introduction, then straight to the main quest (AC needs one real run of data before it can help you; go play one round now — install KovaaK's first if you haven't; come back and call me to analyze it — that round also confirms everything works on your PC), with the main-quest card as the last line. " +
    "While sending it, call intro_context.get and user_profile.get in parallel; if local runs already exist, follow the skill rule and analyze the most recent run right away. " +
    "Do not ask any getting-to-know-you questions (the four questions come only after the first analysis). Do not use any guiding fake user message.",
} as const;

export type IntroKickoffLocale = keyof typeof INTRO_KICKOFF_PROMPTS;

/** Kickoff prompt for the app locale; zh-CN is the default and stays byte-identical. */
export function introKickoffPrompt(locale: IntroKickoffLocale = "zh-CN"): string {
  return INTRO_KICKOFF_PROMPTS[locale];
}

export function isIntroKickoffMessage(content: string): boolean {
  return content.trimStart().startsWith(INTRO_KICKOFF_SENTINEL);
}
