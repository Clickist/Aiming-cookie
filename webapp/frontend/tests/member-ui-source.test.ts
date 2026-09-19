import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const frontendRoot = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(frontendRoot, relativePath), "utf8");
}

test("member chrome has zero commercial surface (no login form, no pricing, no top-up dialog)", async () => {
  const files = [
    "components/task3/MemberCenter.tsx",
    "components/task3/MemberChrome.tsx",
    "lib/member.ts",
    "lib/member-state.ts",
    "lib/member-deeplink.ts",
  ];
  for (const file of files) {
    const value = await source(file);
    // 铁律②：无充值弹窗；铁律①：无登录表单与套餐选择；零商业化：无价格。
    // 「云教练」是禁用措辞：允许出现在注释说明里，禁止出现在用户可见文案（MEMBER_COPY）。
    if (file === "lib/member.ts") {
      const copyBlock = value.slice(value.indexOf("export const MEMBER_COPY"), value.indexOf("} as const;", value.indexOf("export const MEMBER_COPY")));
      assert.doesNotMatch(copyBlock, /云教练/, file);
    } else {
      assert.doesNotMatch(value, /云教练/, file);
    }
    assert.doesNotMatch(value, /<form|type="password"|type="email"/, file);
    assert.doesNotMatch(value, /Dialog|Modal/, file);
    // 金额只允许出现在加油包固定标价（线框原文「加油包 ¥10」）。
    const withoutBoosterPrice = value.replace(/加油包 ¥10/g, "");
    assert.doesNotMatch(withoutBoosterPrice, /¥/, file);
  }
});

test("the four member screens exist with the wireframe navigation and states", async () => {
  const onboarding = await source("components/task3/OnboardingFlow.tsx");
  const center = await source("components/task3/MemberCenter.tsx");
  const coach = await source("components/task6/CoachPanel.tsx");
  const rail = await source("components/task7/SessionRail.tsx");

  // ① 会员档置顶 + 选中即走登录订阅流（①a/①b 四态）。
  assert.match(onboarding, /isMemberWizardType/);
  assert.match(onboarding, /MEMBER_COPY\.providerDropdownLabel/);
  for (const state of ["waiting", "not_subscribed", "member", "test_failed"]) {
    assert.match(onboarding, new RegExp(`"${state}"`), state);
  }
  assert.match(onboarding, /startMemberLogin/);
  assert.match(onboarding, /exchangeMemberTicket/);

  // ②c 用户中心：← 返回导航 + 两池全貌 + 加油包置灰 + 退出登录双去向。
  assert.match(center, /IconChevronLeft/);
  assert.match(center, /MEMBER_COPY\.centerTitle/);
  assert.match(center, /MEMBER_COPY\.boosterBuyDisabled/);
  assert.match(center, /MEMBER_COPY\.logoutButton/);
  assert.match(center, /MEMBER_COPY\.logoutNote/);
  // 退款条款不在客户端（零商业化）：只留按钮入口，细则在网页账单子页。
  assert.doesNotMatch(center, /MEMBER_COPY\.refundHint/);
  assert.doesNotMatch(center, /30 天内且未使用可退/);
  assert.match(center, /logoutMemberAccount/);
  // 加油包与订阅管理都跳系统浏览器（客户端内无支付界面）。
  assert.match(center, /openExternalUrl/);

  // ②b chip 两态由数据投影决定，组件本身不拉数据。
  assert.match(rail, /task7-session-rail__account/);
  assert.doesNotMatch(rail, /from "@\/lib\/api"/);

  // ④/⑧/⑨ 教练页提示与发送门。
  assert.match(coach, /MemberNotice/);
  assert.match(coach, /sendBlockedByMember/);
  assert.match(coach, /classifyMemberGatewayError/);
});

test("member state never infers entitlement from the URL and always reads /api/me", async () => {
  const deeplink = await source("lib/member-deeplink.ts");
  const state = await source("lib/member-state.ts");
  const member = await source("lib/member.ts");
  // 契约 §3.3-6：权益永不从 URL 推断——deep-link 只带 ticket/dc。
  assert.doesNotMatch(member, /searchParams\.get\("member"\)|searchParams\.get\("pct"\)|searchParams\.get\("plan"\)/);
  assert.match(deeplink, /exchangeMemberTicket/);
  assert.match(deeplink, /fetchMemberStatus/);
  // 无 ticket / 拒绝路径一律降级刷新，不弹错误（§3.2 触发 2 / §3.3-7）。
  assert.match(deeplink, /kind: "refreshed"/);
  assert.doesNotMatch(deeplink, /alert\(|throw new Error\("deep-link/);
  // 60s 轮询 + 聚焦刷新（任务书 ⑥）；401 静默降级未登录。
  assert.match(state, /MEMBER_POLL_INTERVAL_MS = 60_000/);
  assert.match(state, /addEventListener\("focus"/);
});

test("member deep-link rules cover the nine contract checks", async () => {
  const value = `${await source("lib/member.ts")}
${await source("lib/member-deeplink.ts")}`;
  // §3.3-1/2：scheme+host 白名单与 scene 枚举（未知按 open）。
  assert.match(value, /MEMBER_DEEP_LINK_SCHEME = "aimingcookie"/);
  assert.match(value, /MEMBER_DEEP_LINK_HOST = "auth"/);
  assert.match(value, /MEMBER_SCENES = \["login", "subscribe", "booster", "open"\]/);
  // §3.3-3/4：ticket 与 dc 成对 + dc 绑定（本地待用 dc 在校验在 sidecar）。
  assert.match(value, /canExchange/);
  // §3.3-5：一次性 ticket 去重（URL 级 seen 集合在前端；ticket 级在 sidecar）。
  assert.match(value, /firstMemberDeepLink/);
  const deeplink = await source("lib/member-deeplink.ts");
  assert.match(deeplink, /seenRef|已消费|handledUrlsRef/);
  // §5.3：新旧错误值双认。
  assert.match(value, /auth_expired: "jwt_expired"/);
  assert.match(value, /ac_member_required: "member_required"/);
  // 三档色：绿 / 橙 / 红。
  assert.match(value, /"quota-ok"|POOL_TIER_COLORS/);
});
