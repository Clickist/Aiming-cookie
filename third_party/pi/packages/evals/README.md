# Pi evals

Behavioral evaluations for Pi using `vitest-evals`.

`src/pi-harness.ts` adapts Pi's `AgentSession` directly to `vitest-evals`. Each run uses isolated temporary project and
agent directories that are removed afterward. Judge harnesses are configured separately from the application harness.

Eval cases supply either one prompt or a JSON-safe sequence of prompt and reload steps. The harness executes that input
and normalizes the resulting session without defining scenario-specific behavior. Suites can configure a typed output
selector over the final response and `AgentSession` state.

## Running

From the repository root, run with an explicit provider and model:

```bash
npm run eval -- --provider openai-codex --model gpt-5.4
```

When invoked from a Pi Bash tool, the current session supplies `PI_PROVIDER` and `PI_MODEL`, so this is sufficient:

```bash
npm run eval
```

The runner requires both values and never falls back to another model. Additional arguments are forwarded to Vitest, for example:

```bash
npm run eval -- -t "capital of France"
```

Authentication is resolved by Pi's normal `ModelRuntime`. Subscription-backed providers such as `openai-codex` use credentials from the user's Pi configuration. API-backed providers use their standard environment variables, such as `OPENAI_API_KEY` for `openai`.
