# Coach Conversation Cards and Video Workspace Implementation Plan

> **For Codex:** execute one Task at a time, tests first, and stop if a frozen backend privacy or Analysis ownership contract must change.

**Status:** active; 点点已于 2026-08-10 连续授权 Tasks 1-7。

**Goal:** Complete the Coach-first desktop workspace so useful Analysis evidence is consumed inside Coach, video opens beside the conversation, History and Settings stay centered at large sizes, and obsolete Tasks/Analysis pages are no longer user-facing.

**Architecture:** Keep persisted Analysis results and existing owner-scoped Coach context refs as the factual source. Message cards are deterministic frontend projections of immutable Analysis context, never model-authored chart JSON. AppShell owns the video target and renders Session rail + optional video pane + Coach conversation. Tauri enforces the supported desktop width; legacy page URLs remain bounded redirects.

**Frozen decisions:**
- Product navigation contains Coach, History, and Settings only.
- New Run analysis continues through the existing Coach-first fixed pipeline; this plan does not change backend analysis or capture contracts.
- Analysis data remains owner-scoped and local. Cards consume only existing public frontend projections.
- No new database schema, Provider payload, arbitrary tool result renderer, or raw trace/video path exposure.
- Tauri supports normal desktop and maximized/full-screen layouts, with `1180px` as the minimum content width.

**Stop rule:** Stop if implementation requires changing AnalysisResult, Coach context ownership, media protocol security, Provider prompts, or files outside the Allowed files below.

---

### Task 1: Freeze the frontend acceptance contract

**Allowed files:**
- Create: `webapp/frontend/tests/coach-conversation-workspace.test.ts`
- Modify: `webapp/frontend/tests/packaging-contract.test.ts`
- Modify: `webapp/frontend/tests/task4-source.test.ts`
- Modify: `webapp/frontend/tests/task5-source.test.ts`
- Modify: `webapp/frontend/tests/task6-source.test.ts`
- Modify: `webapp/frontend/tests/task7-coach-workspace.test.tsx`
- Modify: `webapp/frontend/tests/task7-session-rail.test.tsx`

**Tests first:** Add source-contract assertions for the minimum Tauri width, centered page widths, message-card wiring, optional center video pane, Settings exit, and legacy route redirects.

**Verify:** `npm.cmd --prefix webapp/frontend run test:contracts`

### Task 2: Enforce the supported desktop width and Settings exit

**Allowed files:**
- Modify: `webapp/frontend/src-tauri/tauri.conf.json`
- Modify: `webapp/frontend/components/task3/task3.css`
- Modify: `webapp/frontend/components/task6/SettingsWorkspace.tsx`
- Modify: `webapp/frontend/components/task6/task6.css`
- Tests from Task 1

**Tests first:** Confirm the current `960px` minimum and hidden narrow Settings navigation fail the new contract.

**Implementation:** Set the desktop minimum width to `1180px`; keep a visible Settings return action in loading, failure, and ready states; do not add another mobile navigation system.

**Verify:** focused contract tests and `npm.cmd --prefix webapp/frontend run type-check`.

### Task 3: Center large History and Settings content

**Allowed files:**
- Modify: `webapp/frontend/components/task4/task4.css`
- Modify: `webapp/frontend/components/task6/task6.css`
- Tests from Task 1

**Tests first:** Assert both consumption surfaces use the shared `1040px` maximum and remain centered.

**Implementation:** Constrain the page content without changing row, form, Provider, capture, or storage behavior.

**Verify:** focused contract tests and desktop screenshots at `1280x820` and `1920x1080`.

### Task 4: Render deterministic Coach message cards

**Allowed files:**
- Create: `webapp/frontend/components/task7/CoachMessageCards.tsx`
- Create: `webapp/frontend/components/task7/coach-message-cards.css`
- Modify: `webapp/frontend/components/task6/CoachPanel.tsx`
- Modify: `webapp/frontend/components/task6/task6.css`
- Modify: `webapp/frontend/lib/types.ts`
- Modify: `webapp/backend/schemas.py`
- Modify: `webapp/backend/routes.py`
- Test: `webapp/tests/test_routes_coach.py`
- Tests from Task 1

**Tests first:** Prove only safe, recognized Analysis tool traces produce typed presentation hints, deleted/missing refs fail closed, and the frontend does not parse arbitrary model content as card data.

**Implementation:** Project bounded `coach_message_card.v1` hints from the already-persisted safe tool trace and message context refs. Fetch existing public Analysis projections client-side and render compact summary, metric, event-timeline, or evidence cards. No raw tool result is exposed.

**Verify:** focused Python tests, frontend contracts, and type-check.

### Task 5: Connect the center video pane

**Allowed files:**
- Create: `webapp/frontend/components/task7/CoachVideoPane.tsx`
- Create: `webapp/frontend/components/task7/coach-video-pane.css`
- Modify: `webapp/frontend/components/task3/AppShell.tsx`
- Modify: `webapp/frontend/components/task3/task3.css`
- Modify: `webapp/frontend/components/task6/CoachPanel.tsx`
- Tests from Task 1

**Tests first:** Assert AppShell owns an optional Analysis video target and that Coach cards/context refs call one typed open-video callback.

**Implementation:** Reuse `getSession`, `presentAnalysisWorkspace`, and the existing `VideoView`. Without a video target, keep the conversation at a readable centered maximum width; with a target, render center video plus right Coach conversation.

**Verify:** contract tests, type-check, and mocked browser interaction for open/seek/close.

### Task 6: Retire user-facing Tasks and Analysis pages

**Allowed files:**
- Modify: `webapp/frontend/app/tasks/page.tsx`
- Modify: `webapp/frontend/app/analyze/page.tsx`
- Modify: `webapp/frontend/app/analysis/page.tsx`
- Modify: `webapp/frontend/app/analysis/[analysisId]/page.tsx`
- Modify: `webapp/frontend/components/task4/HistoryClient.tsx`
- Modify: `webapp/frontend/components/task6/CoachPanel.tsx`
- Modify: `webapp/frontend/tests/packaging-contract.test.ts`
- Modify: `webapp/frontend/tests/task4-source.test.ts`

**Tests first:** Assert the legacy routes redirect to Coach/History and History opens its local summary or sends selected Analysis refs to Coach rather than navigating to `/analysis`.

**Implementation:** Preserve URL compatibility through redirects; do not delete backend task/analysis capabilities or reusable video/data components.

**Verify:** focused contract tests, type-check, and production build.

### Task 7: Visual regression, documentation, and closeout

**Allowed files:**
- Modify: `webapp/frontend/e2e/browser-smoke.spec.ts`
- Modify: `webapp/frontend/e2e/accessibility.spec.ts`
- Modify: `webapp/frontend/e2e/screenshots.spec.ts`
- Modify: `docs/PRD.md`
- Modify: `docs/frontend-uiux-design.md`
- Modify: `docs/PROGRESS.md`
- Modify: `docs/superpowers/plans/README.md`
- Files modified by Tasks 1-6

**Verification:**
```powershell
npm.cmd --prefix webapp/frontend run test
npm.cmd --prefix webapp/frontend run type-check
npm.cmd --prefix webapp/frontend run build
npm.cmd --prefix webapp/frontend exec playwright test e2e/browser-smoke.spec.ts e2e/accessibility.spec.ts
git diff --check
```

Visually inspect `1280x820` and `1920x1080` Coach, History, Settings, message-card, and video-open states. Record exact automated and visual results in `docs/PROGRESS.md`; leave real Tauri/KovaaK/hardware/release Gates unchanged.
