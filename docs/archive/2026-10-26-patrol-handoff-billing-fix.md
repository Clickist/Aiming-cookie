# 给巡检 session 的提示词（点点复制带过去）

> 背景：今天（10-26）我做了"会员余额显示虚高"事故的修复，全链已上线。巡检从今天起请把以下三处纳入观察清单，出现异常按 P0 叫我。

## 今天上了什么（巡检须知）

1. **new-api 侧**：459 个 ac- 令牌 `unlimited_quota` 1→0（备份 `/root/unlimited-backup-20261026.tsv`）。发货脚本 `/opt/new-api/ac-member.sh` 已更新为 unlimited=0 标准语义（备份 `ac-member.sh.bak-20261026-unlimited`），并补齐了订阅令牌 expired_time 参数（与仓库版对齐）。
2. **accounts Worker**（版本 dc610acd）：`buildPools` 余量显示兜底改为「发放行比快照新才算真续费窗显示 100%，否则照实显示陈旧快照」。用户可见变化：App 关闭/心跳断几分钟后，余量显示最后同步值，不再跳 100%。
3. **验证已做**：标准计费端点 `/v1/dashboard/billing/subscription` 恢复真值（测试号 $25.12）；生产 `/api/me` 陈旧窗不再假满额；订阅池烧穿+有加油包的用户网关切池实弹验证通过（真实 AI 请求 200、boost 记账正确）。

## 巡检新增观察点

- **切池路径首日观察**：订阅池烧穿（sub 令牌 remain≤0）且有 boost 的用户，以前靠合并钱包烧 boost，现在走网关标准 403→切池。观察 `journalctl -u ac-gateway` 里 `trying next pool` 后是否 200 成功；若出现切池后仍 403/500 聚集，P0 叫我。
- **usage_pusher 水位**：翻旗不改变推送判据（只读 remain/accessed_time），但首日留意 `无变化（N 用户在线）` 日志是否正常、有没有推送报错。
- **充值/续费窗口显示**：新购买用户应显示 100%（真续费窗）；如果有人刚买完显示的不是 100% 或一直是旧数字超过 10 分钟，P0 叫我（续费窗判定逻辑的新边界）。

## 回滚开关（万一出事）

- 令牌旗：`UPDATE tokens SET unlimited_quota=1 WHERE name LIKE "ac-%"`（备份文件里有原始状态）。
- 脚本：`cp /opt/new-api/ac-member.sh.bak-20261026-unlimited /opt/new-api/ac-member.sh`。
- Worker：wrangler rollback 到上一版本即可。

## 一句话总结

计费/显示链路已回归 new-api 标准语义；观察重点是切池路径第一天和续费窗显示边界；备份齐备，任何一步可回滚。
