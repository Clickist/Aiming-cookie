// Coach 空对话首页（点点 0910 拍板）：时间问候不带称呼、三颗建议 chips
// 点击填入草稿（不直发）。纯逻辑无 React 依赖，便于单测。
// 0910 二轮拍板：问候升级为完整引导句（参考桌面 Agent 客户端起始页感觉），
// 原「能力提示」行的信息并入问候语后整行删除。
//
// i18n 批 1：问候池与 chips 文案入字典（coach.home.*），调用时经 t() 解析。

import { t, type MessageKey } from "./i18n/core";

/** 时段 → 问候键池（同一时段两句，按「日 % 池大小」轮换）。 */
const GREETING_KEYS: ReadonlyArray<ReadonlyArray<MessageKey>> = [
  ["coach.home.greeting.morning1", "coach.home.greeting.morning2"],
  ["coach.home.greeting.noon1", "coach.home.greeting.noon2"],
  ["coach.home.greeting.afternoon1", "coach.home.greeting.afternoon2"],
  ["coach.home.greeting.evening1", "coach.home.greeting.evening2"],
  ["coach.home.greeting.lateNight1", "coach.home.greeting.lateNight2"],
];

/**
 * 按时段取问候语。同一日期内稳定同一句（按「日 % 池大小」轮换，不引随机数，
 * 测试可断言）；所有文案不携带称呼——应用内拿不到用户名（0910 拍板）。
 */
export function coachGreeting(now: Date): string {
  const hour = now.getHours();
  const pool =
    hour >= 5 && hour < 11
      ? GREETING_KEYS[0]!
      : hour >= 11 && hour < 14
        ? GREETING_KEYS[1]!
        : hour >= 14 && hour < 18
          ? GREETING_KEYS[2]!
          : hour >= 18
            ? GREETING_KEYS[3]!
            : GREETING_KEYS[4]!;
  return t(pool[now.getDate() % pool.length]!);
}

export interface CoachHomeChip {
  id: string;
  /** chips 上显示的短标签（一行三颗，0910 拍板）。 */
  label: string;
  /** 点击后填入输入框的完整提问；由用户确认后自己发送（不直发）。 */
  prompt: string;
}

/** 首页建议 chips：每颗对应 Coach 现成的真能力，不是摆设文案。 */
export function coachHomeChips(): CoachHomeChip[] {
  return [
    {
      id: "review-latest",
      label: t("coach.home.chip.reviewLatestLabel"),
      prompt: t("coach.home.chip.reviewLatestPrompt"),
    },
    {
      id: "progress",
      label: t("coach.home.chip.progressLabel"),
      prompt: t("coach.home.chip.progressPrompt"),
    },
    {
      id: "weakness",
      label: t("coach.home.chip.weaknessLabel"),
      prompt: t("coach.home.chip.weaknessPrompt"),
    },
  ];
}
