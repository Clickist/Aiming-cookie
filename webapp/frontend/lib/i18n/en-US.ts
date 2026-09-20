import type { MessageKey } from "./zh-CN";

// en-US 字典：satisfies 让 tsc 强制本表与 zh-CN 键集完全一致（缺键、多键都是
// 编译错误）。翻译口径见 .zcode/i18n-conventions.md：自然简洁的产品英文，
// 按钮用动词短语，金额/单位/时间格式跟随原串风格。
export const enUS = {
  // components/task3/UpdatePrompt.tsx —— 桌面更新提示卡（批 0 样板）
  "update.prompt.ariaLabel": "App update notice",
  "update.prompt.title": "Version {version} is available",
  "update.prompt.noteInstalling": "Downloading and installing. The app will restart automatically when it's done…",
  "update.prompt.noteFailed": "Update failed. Check your connection, then retry from \"App updates\" in Settings.",
  "update.prompt.noteReady": "Download the official installer and restart automatically to complete the upgrade.",
  "update.prompt.busy": "Working…",
  "update.prompt.retry": "Retry",
  "update.prompt.installNow": "Update now",
  "update.prompt.later": "Later",
} satisfies Record<MessageKey, string>;
