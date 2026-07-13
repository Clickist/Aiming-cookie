# 教练启动体验 — 应用起就带引擎 + UI 状态

> **状态：completed（Task 1–4，2026-07-12）。点点 2026-07-12：启动即起教练服务，避免聊天冷启动空白。**

## Frozen

1. **dev 默认**：`scripts/dev-up.sh` 先起 sidecar，再起 API（可配置只打印 worker/frontend 命令）。
2. **API**：`GET /api/coach/runtime-status` →
   `{ "ok": true, "runtime": "pi"|"python", "sidecar": "up"|"down"|"n/a", "ready_for_fast_path": bool, "message": "..." }`
3. **UI `/coach`**：挂载后轮询 runtime-status（2s，最多 N 次或直到 ready）；横幅说明状态；发送不硬禁（慢路径仍可用），但 **sending 文案区分**「引擎启动中…」vs「教练思考中…」。
4. 不在本 plan 做 Desktop 自动拉起；不改 schema。

## Tasks

### Task 1 — runtime-status API + tests
Allowed: health or routes, schemas optional, tests

### Task 2 — scripts/dev-up.sh + README 一句
Allowed: scripts/, webapp/README.md, deployment-guide 小段

### Task 3 — CoachClient 状态横幅 + 文案
Allowed: frontend coach + api.ts + types

### Task 4 — 回归
pytest + tsc；PROGRESS / plans README
