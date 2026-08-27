# Pi upstream provenance (vendored)

This directory is a **frozen vendor copy** of [earendil-works/pi](https://github.com/earendil-works/pi). Aiming Cookie does **not** track upstream releases or upgrade this tree automatically.

## Identity

| Field | Value |
|-------|--------|
| Repository URL | `https://github.com/earendil-works/pi.git` |
| Frozen commit | `845d6ff1f6643aba440341cce877ce1c43ebbc39` |
| Commit subject | `Release v0.83.0` (tag `v0.83.0`) |
| Commit date | `2026-07-30 00:24:19 +0200` |
| Re-vendored | `2026-08-27`, upgrading from frozen `3ea064ea` (`v0.80.6`, 2026-07-10) |

## Why not v0.84.x

Upstream reworked the agent harness starting **v0.84.0**: the turnkey
`AgentHarness` became an unfinished scaffold (`prompt()` rejects with
`HarnessNotImplemented`; constructor went private in favor of
`AgentHarness.create`, `env` option removed) while the real loop moved behind
an incomplete server/session-backends architecture. Rebuilding Coach's turn
engine on those moving foundations was rejected for this batch. Re-evaluate
when upstream ships a stable harness API (checkpoint: `AgentHarness.prompt`
implemented in the core package).

## Local modifications

None. The two Windows path-compat patches previously carried on
`packages/agent/src/harness/env/nodejs.ts` and
`packages/agent/src/harness/skills.ts` were dropped at this upgrade:
upstream is now natively path-separator aware (backslash normalization and
separator-aware basename/split), so the vendor tree matches upstream exactly.

## Package versions (at frozen commit)

| Package | npm name | Version |
|---------|----------|---------|
| Agent core | `@earendil-works/pi-agent-core` | `0.83.0` |
| AI | `@earendil-works/pi-ai` | `0.83.0` |
| Monorepo root | `pi-monorepo` | (see root `package.json`) |

## Aiming Cookie usage scope

Coach runtime integration uses **only**:

- `packages/ai`
- `packages/agent`

Other packages in this tree (TUI, coding-agent CLI, extensions, etc.) are present for auditability but are **not** part of the product runtime path unless explicitly adopted in a future plan.

## Copy method

Source was copied from a clean clone verified at the frozen tag (`.git`
excluded; local `node_modules` and build caches are not vendored).

Restore additionally requires generating upstream's **gitignored runtime
model data**: since 0.81+ the committed `.models.ts` shards import
`providers/data/<provider>.json`, which npm does not ship and git does not
track. See `docs/DEVELOPMENT.md` "pinned Pi 安装恢复" for the full sequence
(`npm ci --ignore-scripts` → targeted `tsgo` emit →
`hydrate-model-data --data-only`). The hydrate step writes only the ignored
`src/providers/data/` directory; never run the full `generate-models`, which
rewrites committed pinned source.

## Verification

```bash
node -e 'const p=require("./packages/agent/package.json"); console.log(p.name,p.version)'
# expect: @earendil-works/pi-agent-core 0.83.0
```
