/**
 * One-time Intro Session kickoff turn text.
 *
 * The intro-session skill opens without user input. The turn still needs a
 * concrete prompt for the agent loop, so the sidecar synthesizes this internal
 * instruction. It is persisted to the Pi session (the model must have its own
 * opening instruction in context) but is filtered out of every UI read — the
 * user must never see a fake user message (wireframe 状态①).
 *
 * Leaf module: no imports, so both session-repo.ts (UI filter) and the sidecar
 * routes can share the sentinel without an import cycle.
 */

export const INTRO_KICKOFF_SENTINEL = "[开场分析自动启动]";

export const INTRO_KICKOFF_PROMPT =
  `${INTRO_KICKOFF_SENTINEL} 现在开始本次「开场分析」。按 intro-session skill 发首条消息：` +
  "一句自我介绍，然后问第一问「平时都玩什么游戏？」；发这条的同时并行调用 intro_context.get 和 user_profile.get 拿背景数据。" +
  "不要用任何引导用的假用户消息。";

export function isIntroKickoffMessage(content: string): boolean {
  return content.trimStart().startsWith(INTRO_KICKOFF_SENTINEL);
}
