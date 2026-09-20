// zh-CN 源语言字典：键集合是 MessageKey 的唯一事实源，en-US.ts 用
// `satisfies Record<MessageKey, string>` 强制逐键对齐（缺键/多键都过不了 tsc），
// tests/i18n-contract.test.ts 再做运行时复核。
//
// 施工口径（详见 .zcode/i18n-conventions.md）：
// - 值必须是产品现行中文文案的逐字搬运，不许顺手润色改写；
// - 键按「模块.部件.语义」点分命名空间分组，同模块条目集中在一处区块注释下追加。
export const zhCN = {
  // components/task3/UpdatePrompt.tsx —— 桌面更新提示卡（批 0 样板）
  "update.prompt.ariaLabel": "应用更新提示",
  "update.prompt.title": "发现新版本 {version}",
  "update.prompt.noteInstalling": "正在下载并安装，完成后应用会自动重启…",
  "update.prompt.noteFailed": "更新失败，请确认网络后在设置的「应用更新」里重试。",
  "update.prompt.noteReady": "下载官方安装包并自动重启完成升级。",
  "update.prompt.busy": "处理中…",
  "update.prompt.retry": "重试",
  "update.prompt.installNow": "立即更新",
  "update.prompt.later": "稍后再说",
} as const;

export type MessageKey = keyof typeof zhCN;
