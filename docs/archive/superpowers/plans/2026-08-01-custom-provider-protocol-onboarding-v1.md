# Custom Provider Protocol And Onboarding v1 Implementation Plan

> **Status: active.** 点点于 2026-08-01 明确要求自定义 Provider 同时支持
> OpenAI-compatible 与 Anthropic-compatible API，并将 Onboarding 固定为
> Provider/URL -> API key -> discovered models -> manual model-ID fallback 的顺序。
> 本计划覆盖先前 OpenDesign realization plan 中“不修改 Provider contract”的冻结项；
> 其余 Provider、分析和 Coach 边界保持不变。

**Goal:** 让用户能用任一受支持协议的自定义 URL、安全读取模型列表并完成模型选择。

**Architecture:** 保留既有 `builtin` profile 与认证流程。自定义 profile 以两个明确的
`kind` 记录 OpenAI 或 Anthropic 协议，API key 仅在一次模型读取或本地 profile credential
路径中使用；Pi runtime 依据 profile kind 选择对应 stream adapter。前端不存储 key，模型
列表失败时才显示手填 model ID 的恢复入口。

**Discovery behavior:** custom onboarding probes both model-list protocols after URL and API key entry. A successful probe selects the stored profile kind internally; the protocol control is shown only when both probes fail and a manual model ID fallback is needed.

## Task 1 - Dual custom protocol and automatic onboarding

### Allowed files

- `docs/ARCHITECTURE.md`
- `docs/superpowers/plans/README.md`
- this plan
- `webapp/backend/coach_runtime.py`
- `webapp/backend/db.py`
- `webapp/backend/provider_store.py`
- `webapp/backend/routes.py`
- `webapp/backend/schemas.py`
- `webapp/tests/test_provider_auth_routes.py`
- `webapp/tests/test_provider_auth.py`
- `webapp/tests/test_provider_routes.py`
- `webapp/tests/test_provider_store.py`
- `webapp/tests/test_db.py`
- `webapp/coach-runtime/src/contracts.ts`
- `webapp/coach-runtime/src/pi-source.ts`
- `webapp/coach-runtime/src/provider-models.ts`
- `webapp/coach-runtime/src/provider-profile.ts`
- `webapp/coach-runtime/test/provider-models.test.ts`
- `webapp/coach-runtime/test/sidecar-server.test.ts`
- `webapp/frontend/components/task3/OnboardingFlow.tsx`
- `webapp/frontend/components/task3/task3.css`
- `webapp/frontend/components/task6/SettingsWorkspace.tsx`
- `webapp/frontend/lib/api.ts`
- `webapp/frontend/lib/api.test.ts`
- `webapp/frontend/lib/types.ts`
- `webapp/frontend/tests/task3-source.test.ts`
- `webapp/frontend/tests/task6-source.test.ts`
- focused existing onboarding Browser E2E and snapshot files only where behavior requires it

### Tests first

1. A custom OpenAI profile and custom Anthropic profile validate, persist and resolve to their matching Pi stream adapters without serializing credentials.
2. Automatic discovery probes both corresponding model-list requests and sends the required authentication headers; response parsing is bounded and returns only model IDs.
3. The onboarding control prevents profile creation until the selected Provider/custom URL and required credential are supplied, attempts discovery, then offers list selection; manual model ID is available only after discovery finds no matching model or fails.
4. Built-in API-key, OAuth and ambient flows are unchanged.
5. Settings recognizes both custom profile types for credential replacement, connection testing and deletion; creating a custom profile uses the same automatic discovery and rare manual fallback.

### Verification

```powershell
$env:KOVAAK_INSTALL_DIR = Join-Path $env:TEMP "aiming-cookie-no-kovaak"
.\.venv\Scripts\python.exe -m pytest webapp/tests/test_provider_store.py webapp/tests/test_provider_auth_routes.py -q
cd webapp\coach-runtime
npm.cmd test -- --test-name-pattern="custom|provider"
cd ..\frontend
npm.cmd run test:unit
npm.cmd run type-check
npm.cmd run build
npm.cmd exec playwright test e2e/browser-smoke.spec.ts e2e/accessibility.spec.ts --grep "onboarding"
```

### Stop rule

Stop if a secret appears in a public API response, browser storage, exception text or test fixture; if an unselected protocol can be inferred only from URL text; or if the change requires changing built-in catalog/OAuth behavior.
