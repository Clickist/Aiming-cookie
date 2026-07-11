# Pi upstream provenance (vendored)

This directory is a **frozen vendor copy** of [earendil-works/pi](https://github.com/earendil-works/pi). Aiming Cookie does **not** track upstream releases or upgrade this tree automatically.

## Identity

| Field | Value |
|-------|--------|
| Repository URL | `https://github.com/earendil-works/pi.git` |
| Frozen commit | `3ea064ea2a0f01965923ce32e1bd17466c502b23` |
| Commit subject | `fix: support Bedrock API key login` |
| Commit date | `2026-07-10 19:34:11 +0200` |

## Package versions (at frozen commit)

| Package | npm name | Version |
|---------|----------|---------|
| Agent core | `@earendil-works/pi-agent-core` | `0.80.6` |
| AI | `@earendil-works/pi-ai` | `0.80.6` |
| Monorepo root | `pi-monorepo` | `0.0.3` |

## Aiming Cookie usage scope

Coach runtime integration uses **only**:

- `packages/ai`
- `packages/agent`

Other packages in this tree (TUI, coding-agent CLI, extensions, etc.) are present for auditability but are **not** part of the product runtime path unless explicitly adopted in a future plan.

## Copy method

Source was copied from a local checkout verified at the frozen commit (`.git` excluded). `node_modules` and build caches are **not** vendored; install dependencies locally when developing against this tree.

## Verification

```bash
node -e 'const p=require("./packages/agent/package.json"); console.log(p.name,p.version)'
# expect: @earendil-works/pi-agent-core 0.80.6
```