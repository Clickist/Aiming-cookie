"use client";

import { MemberCenter } from "@/components/task3/MemberCenter";
import { useMemberState } from "@/lib/member-state";

/** `/account` 路由壳：拉会员态并渲染用户中心（②c）。 */
export function MemberCenterPage() {
  const { me } = useMemberState();
  return <MemberCenter me={me} />;
}
