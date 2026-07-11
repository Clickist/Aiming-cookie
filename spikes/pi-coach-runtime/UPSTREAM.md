# Pi candidate provenance (assessment/Spike only)

> **Status:** candidate for assessment/Spike, **not** an approved production vendor baseline.

- Upstream URL: `https://github.com/earendil-works/pi.git`
- Frozen commit: `3ea064ea2a0f01965923ce32e1bd17466c502b23`
- Commit subject: `fix: support Bedrock API key login`
- Candidate checkout: `/tmp/aiming-cookie-pi-assessment` (the resolved temporary path reported by the runner is `/private/tmp/aiming-cookie-pi-assessment`)
- Branch snapshot: `main` shallow checkout
- Package versions inspected: `@earendil-works/pi-ai@0.80.6`, `@earendil-works/pi-agent-core@0.80.6`, `@earendil-works/pi-coding-agent@0.80.6`, `@earendil-works/pi-tui@0.80.6`, `@earendil-works/pi-orchestrator@0.80.6`
- Root workspace package version: `0.0.3`
- Node engine: `>=22.19.0` (verified runtime: `v25.9.0`)
- License: MIT; `Copyright (c) 2025 Mario Zechner`
- Verification date: 2026-07-11

## Commands and results

```bash
test -d /tmp/aiming-cookie-pi-assessment/.git
git -C /tmp/aiming-cookie-pi-assessment remote get-url origin
# https://github.com/earendil-works/pi.git

git -C /tmp/aiming-cookie-pi-assessment rev-parse HEAD
# 3ea064ea2a0f01965923ce32e1bd17466c502b23

git -C /tmp/aiming-cookie-pi-assessment status --short --branch
# ## main...origin/main
# (no tracked or untracked checkout changes)

node -e 'const p=require("/tmp/aiming-cookie-pi-assessment/package.json"); console.log(p.engines?.node, p.workspaces)'
# >=22.19.0 ["packages/*", ...]

npm run test --workspace @earendil-works/pi-agent-core -- --reporter=dot
# 16 passed files; 180 passed tests
```

No `git pull`, `fetch`, `checkout`, `reset`, `clean`, dependency installation, or candidate-source modification was performed.
