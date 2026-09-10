// Coach 空对话首页（点点 0910 拍板）：时间问候不带称呼、三颗建议 chips
// 点击填入草稿（不直发）。纯逻辑无 React 依赖，便于单测。
// 0910 二轮拍板：问候升级为完整引导句（参考桌面 Agent 客户端起始页感觉），
// 原「能力提示」行的信息并入问候语后整行删除。

/**
 * 按时段取问候语。同一日期内稳定同一句（按「日 % 池大小」轮换，不引随机数，
 * 测试可断言）；所有文案不携带称呼——应用内拿不到用户名（0910 拍板）。
 */
export function coachGreeting(now: Date): string {
  const hour = now.getHours();
  const pool =
    hour >= 5 && hour < 11
      ? ["早上好，今天想练什么？", "早上好，开练前聊聊昨天的问题？"]
      : hour >= 11 && hour < 14
        ? ["中午好，有什么想让我看的？", "中午好，上午的训练要我看看吗？"]
        : hour >= 14 && hour < 18
          ? ["下午好，今天练得怎么样？", "下午好，有什么想让我帮忙的？"]
          : hour >= 18
            ? ["晚上好，练过了吗？要我看看？", "晚上好，聊聊今天的训练？"]
            : ["夜深了，还没休息？", "夜深了，明天再练也不迟。"];
  return pool[now.getDate() % pool.length]!;
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
      label: "复盘我最近打的一局",
      prompt: "帮我复盘我最近打的一局，讲讲主要问题和改进方向。",
    },
    {
      id: "progress",
      label: "最近两周我的进步",
      prompt: "帮我看看最近两周的进步趋势：哪些在变好，哪些在退步？",
    },
    {
      id: "weakness",
      label: "我的弱点在哪",
      prompt: "结合我最近的训练数据，说说当前最大的弱点和针对性练法。",
    },
  ];
}
