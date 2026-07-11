# License and dependency notes (technical inventory; legal review required)

> This is a technical source inventory, not legal advice. Production source distribution, attribution, modification notices, and direct/transitive dependency obligations require legal review before any vendor decision.

## Root license facts

- Source: `/tmp/aiming-cookie-pi-assessment/LICENSE`, lines 1-40.
- License text begins `MIT License`.
- Copyright line: `Copyright (c) 2025 Mario Zechner`.
- The checked root had no `NOTICE` file (`test -e /tmp/aiming-cookie-pi-assessment/NOTICE` was false).
- MIT's included notice requirement is present in the root license text. Whether it applies to a proposed packaged product, and what other notices are needed, remains for legal review.

## Direct package dependency summary

| Package | Direct dependencies from package manifest |
|---|---|
| `packages/ai` / `@earendil-works/pi-ai@0.80.6` | `@anthropic-ai/sdk`, `@aws-sdk/client-bedrock-runtime`, `@google/genai`, `@mistralai/mistralai`, `@opentelemetry/api`, `@smithy/node-http-handler`, `http-proxy-agent`, `https-proxy-agent`, `openai`, `partial-json`, `typebox` |
| `packages/agent` / `@earendil-works/pi-agent-core@0.80.6` | `@earendil-works/pi-ai`, `ignore`, `typebox`, `yaml` |
| `packages/coding-agent` / `@earendil-works/pi-coding-agent@0.80.6` | `@earendil-works/pi-agent-core`, `@earendil-works/pi-ai`, `@earendil-works/pi-tui`, `@silvia-odwyer/photon-node`, `chalk`, `cross-spawn`, `diff`, `glob`, `highlight.js`, `hosted-git-info`, `ignore`, `jiti`, `minimatch`, `proper-lockfile`, `semver`, `typebox`, `undici`, `yaml` |
| `packages/tui` / `@earendil-works/pi-tui@0.80.6` | `get-east-asian-width`, `marked` |
| `packages/orchestrator` / `@earendil-works/pi-orchestrator@0.80.6` | `@earendil-works/pi-coding-agent` |

Evidence: the five corresponding `packages/*/package.json` manifests in candidate checkout `3ea064ea2a0f01965923ce32e1bd17466c502b23`.

## Pending production-vendor work

1. Generate and retain a direct **and transitive** third-party dependency inventory from the selected source baseline and its lockfile.
2. Review package licenses, notices, source-distribution and attribution obligations, including bundled assets and generated artifacts.
3. Freeze provenance, patch tracking, and update/maintenance policy in the future formal takeover plan.
4. Obtain legal review before source distribution or production vendoring.
