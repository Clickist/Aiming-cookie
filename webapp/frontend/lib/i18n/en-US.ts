// en-US 字典：satisfies 让 tsc 强制本表与 zh-CN 键集完全一致（缺键、多键都是
// 编译错误）。分片机制见 zh-CN.ts 头注：键住在 dict/ 分片里，本文件只做机械
// spread 与键集校验。
// 翻译口径见 .zcode/i18n-conventions.md：自然简洁的产品英文，按钮用动词短语，
// 金额/单位/时间格式跟随原串风格。
import type { MessageKey } from "./zh-CN";
import { sharedEn } from "./dict/shared.en";
import { task3En } from "./dict/task3.en";
import { task45En } from "./dict/task45.en";
import { task6En } from "./dict/task6.en";
import { task7En } from "./dict/task7.en";

export const enUS = {
  ...sharedEn,
  ...task3En,
  ...task45En,
  ...task6En,
  ...task7En,
} satisfies Record<MessageKey, string>;
