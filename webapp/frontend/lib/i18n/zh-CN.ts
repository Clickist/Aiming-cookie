// zh-CN 源语言字典：键集合是 MessageKey 的唯一事实源，en-US.ts 用
// `satisfies Record<MessageKey, string>` 强制逐键对齐（缺键/多键都过不了 tsc），
// tests/i18n-contract.test.ts 再做运行时复核。
//
// 字典分片化（为批次并行施工）：键按批次住在 dict/ 下各自的分片文件里，本文件
// 只做机械 spread——并行批次各写各的分片，互不踩脚。
// 新批次开工：照 dict/task3.zh.ts 模板建 dict/<批>.zh.ts / dict/<批>.en.ts，
// 并在本文件与 en-US.ts 的聚合里各加一行 spread；收批入库后分片冻结。
//
// 施工口径（详见 .zcode/i18n-conventions.md）：
// - 值必须是产品现行中文文案的逐字搬运，不许顺手润色改写；
// - 键按「模块.部件.语义」点分命名空间分组，同模块条目集中在一处区块注释下追加。
import { sharedZh } from "./dict/shared.zh";
import { task3Zh } from "./dict/task3.zh";
import { task45Zh } from "./dict/task45.zh";
import { task7Zh } from "./dict/task7.zh";

export const zhCN = {
  ...sharedZh,
  ...task3Zh,
  ...task45Zh,
  ...task7Zh,
} as const;

export type MessageKey = keyof typeof zhCN;
