# Aiming Cookie 当前进度

> **最后整理：2026-07-30。** 本文是当前快照，不是产品或架构事实源。详细研发流水见 [`archive/history/`](archive/history/)；产品、架构与 UI/UX 结论分别以 [`PRD.md`](PRD.md)、[`ARCHITECTURE.md`](ARCHITECTURE.md) 和 [`frontend-uiux-design.md`](frontend-uiux-design.md) 为准。

## 当前摘要

- 产品保持 Desktop-first、local-first 和确定性诊断主路径。当前 launch scope 为 static clicking、dynamic clicking、continuous tracking 与 target switching；movement aiming 在缺少玩家移动遥测时保持 outcome-only。Aiming Cookie 不提供产品账号、登录或用户鉴权服务器；Coach 在 Provider 可用时承担长期关系与产品操作。
- 当前实现已覆盖 Run/Analysis recovery、正式 Browser/Desktop 产品界面、History/Run inspector、Analysis workspace、Coach sidebar/Provider Settings、有限 KovaaK 成绩同步、去身份 Coach 成绩摘要、版本化 Knowledge Registry 与 Training Plan 命令流。所有未校准的比较、诊断和处方继续 fail closed。
- 当前范围只对已有 NVIDIA 实机证据的路径作支持声明；AMD/Intel 与 OAuth/device-code 不在当前范围内。Tracking exact-parity 中位数为 `148.039s`，尚未达到 `<=130s` 目标；真实 Tauri product-path、高 polling-rate correctness/性能，以及 installer、签名、updater 和下载验证仍为 release No-Go Gate。
- 2026-07-30 full-worktree review 的已确认代码与文档问题已按独立复证、最小修复、根会话复核和逻辑分批提交收敛。最终自动化为 Python `1555 passed, 5 skipped`、Coach runtime `172 passed`、Pi AI `473 passed, 733 skipped`、frontend 默认 `58 passed`、production Playwright `55 passed, 3 skipped`、MSVC Rust `73 passed, 7 ignored`；compileall、type-check/build、Rust fmt/check/clippy、diff check、Agent contract parity 与相关文档链接检查通过。skip/ignored 与未执行实机项不改变上方 release No-Go。
- 被本快照覆盖的原始日期化状态、blocker 和验证记录见 [`archive/history/2026-07-30-progress-superseded-history.md`](archive/history/2026-07-30-progress-superseded-history.md)。
