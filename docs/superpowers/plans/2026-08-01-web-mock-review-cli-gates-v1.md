# Web Mock Review, Integration, And Desktop Gates v1 Implementation Plan

> **Status: active.** 点点已确认实施顺序为 Web Mock 审核 -> Web 真实联调 -> Tauri 命令式验收。本计划当前只授权 Task 1；后续阶段必须在真实 DTO、产品操作和 Tauri runtime 边界确定后另行登记 Task。

**Goal:** 提供一个仅用于前端连续审核的本地 Web Mock 模式，使页面能使用真实 `/api` DTO 形状和可见操作流转，而不依赖正在运行的 FastAPI、worker 或 Tauri。

**Architecture:** 在 `AIMING_COOKIE_API_MODE=mock` 下，Next.js 的 catch-all Route 消费纯 TypeScript review scenario 与框架无关的 request handler。默认模式继续使用现有 FastAPI rewrite。Playwright fixture 复用同一场景和 handler 的适配器，因此 Browser E2E、Mock 审核和后续 Web 联调不会维护三套数据形状。

**Non-goals:** 不 mock 或改写 FastAPI、SQLite、worker、Provider 真实网络请求、KovaaK 本地发现、Tauri command/runtime、owner/confirmation 权限边界；不创建任何开发者后门或产品新页面。

## Task 1 - Shared Web Mock review mode

### Allowed files

- `docs/superpowers/plans/README.md`
- this plan
- `webapp/frontend/next.config.ts`
- `webapp/frontend/package.json`
- `webapp/frontend/lib/api.ts`
- `webapp/frontend/lib/api.test.ts`
- `webapp/frontend/mocks/review-scenario.ts`
- `webapp/frontend/mocks/review-api.ts`
- `webapp/frontend/mocks/review-api.test.ts`
- `webapp/frontend/app/api/[...path]/route.ts`
- `webapp/frontend/fixtures/task7-fixtures.ts`
- `webapp/frontend/e2e/mock-review.spec.ts`
- `webapp/frontend/playwright.mock.config.ts`

### Tests first

1. A framework-independent request handler returns the same versioned DTOs consumed by the frontend for the default review scenario, and unknown routes fail explicitly.
2. Mock-only mutations preserve user-visible state across requests: onboarding completion; provider profile create/test/default/delete; KovaaK connect/refresh/remove; start/retry analysis; Coach message/agent run; and settings calibration update/delete.
3. With `AIMING_COOKIE_API_MODE=mock`, `/api/*` resolves to the Next handler. Without it, the existing FastAPI rewrite remains unchanged.
4. A real browser can traverse onboarding, history, analysis (Diagnosis/Video/Data), Coach, Settings, and KovaaK content against `dev:mock`, including at least one mutation and reload.
5. Existing Playwright API fixtures delegate route matching to the shared handler; fixture-only desktop bridge and local video byte delivery remain adapters, not mock scenario data.

### Verification

```powershell
Push-Location webapp\frontend
npm.cmd run test:unit
npm.cmd run type-check
npm.cmd run build
npm.cmd exec playwright test --config playwright.mock.config.ts
Pop-Location
```

### Stop rule

Stop if any mock path is reachable in the normal API mode, if a secret/Steam identity/desktop launch token is introduced into scenario responses, if an operation would bypass a confirmation or owner boundary in a real runtime, or if one of the listed API DTOs must be invented rather than taken from the current frontend contract.

### Handoff after Task 1

Record the verified command and scenario names in `docs/PROGRESS.md` only after point-in-time verification. Task 2 may introduce a bounded `devctl` only by mapping user-visible operations to existing application services. Task 3 is Web real-integration gates. Task 4 is a real Tauri command-driven smoke and cannot be satisfied by Browser E2E.
