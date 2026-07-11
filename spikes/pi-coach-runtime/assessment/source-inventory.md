# Pi source inventory for Coach Runtime assessment

All entries below are source-code facts from candidate commit `3ea064ea2a0f01965923ce32e1bd17466c502b23`, unless explicitly marked `README-only`. `runtime` means candidate for the isolated Spike only; it does not approve production vendoring.

| Capability | Entry/API | Package | Spike disposition | Evidence |
|---|---|---|---|---|
| Agent loop | `Agent`, `runAgentLoop`, `runAgentLoopContinue` | `packages/agent` | runtime | `packages/agent/src/agent.ts`; `packages/agent/src/agent-loop.ts`; public exports in `packages/agent/src/index.ts` |
| Provider stream injection | `StreamFn`, `AgentOptions.streamFn` | `packages/agent` | runtime | `packages/agent/src/types.ts:27-31`; `packages/agent/src/agent.ts` |
| Agent event union | `AgentEvent`, `agent_start/end`, `message_update/end`, tool execution events | `packages/agent` | runtime | `packages/agent/src/types.ts`; emissions in `packages/agent/src/agent-loop.ts` |
| Tool registry and execution | `AgentTool`, `initialState.tools`, tool execution and update callbacks | `packages/agent` | runtime | `packages/agent/src/types.ts`; `packages/agent/src/agent.ts`; `packages/agent/src/agent-loop.ts:390-787` |
| Tool progress lifecycle | `tool_execution_start/update/end` | `packages/agent` | runtime | `packages/agent/src/agent-loop.ts:390-787` |
| Abort propagation | `Agent.abort`, `SimpleStreamOptions.signal`, loop checks | `packages/agent` | runtime | `packages/agent/src/agent.ts`; `packages/agent/src/agent-loop.ts`; `packages/agent/src/proxy.ts:141-227` |
| Pi assistant event stream | `AssistantMessageEventStream`, assistant message event types | `packages/ai` | runtime | `packages/ai/src/index.ts`; `packages/ai/src/types.ts`; stream helpers in `packages/ai/src` |
| Generic proxy utility | `streamWithProxy` and abort/error handling | `packages/agent` | evidence-only | `packages/agent/src/proxy.ts` |
| JSONL runtime storage | `JsonlSessionStorage.open/create` | `packages/agent` | runtime | `packages/agent/src/harness/session/jsonl-storage.ts:180-240` |
| Session and custom markers | `Session`, `appendMessage`, `appendCustomEntry`, `buildContext` | `packages/agent` | runtime | `packages/agent/src/harness/session/session.ts:137-280` |
| Node execution environment | `NodeExecutionEnv` | `packages/agent` | runtime | `packages/agent/src/harness/env/nodejs.ts:246` |
| Harness / compaction | `AgentHarness`, compaction helpers | `packages/agent` | evidence-only | `packages/agent/src/harness/agent-harness.ts`; `packages/agent/src/harness/compaction/` |
| Core system-prompt helpers | prompt templates and harness system prompt | `packages/agent` | evidence-only | `packages/agent/src/harness/prompt-templates.ts`; `packages/agent/src/harness/system-prompt.ts` |
| Coding tools | `read`, `bash`, `edit`, `write`, `find`, `grep`, `ls` | `packages/coding-agent` | excluded | `packages/coding-agent/src/core/tools/*.ts` |
| Coding system prompt | `buildSystemPrompt` | `packages/coding-agent` | excluded | `packages/coding-agent/src/core/system-prompt.ts` |
| Project trust | project-trust and trust-manager code | `packages/coding-agent` | evidence-only | `packages/coding-agent/src/core/project-trust.ts`; `packages/coding-agent/src/core/trust-manager.ts`; `packages/coding-agent/src/cli/project-trust.ts` |
| Extension loader | local/global extension discovery and loader | `packages/coding-agent` | excluded | `packages/coding-agent/src/core/extensions/loader.ts:521-698` |
| RPC mode | RPC entry and protocol | `packages/coding-agent` | excluded | `packages/coding-agent/src/rpc-entry.ts`; `packages/coding-agent/src/modes/rpc/rpc-types.ts` |
| Coding session runtime | coding-agent session orchestration | `packages/coding-agent` | evidence-only | `packages/coding-agent/src/core/agent-session-runtime.ts`; `packages/coding-agent/src/core/agent-session.ts` |
| TUI/CLI | interactive and noninteractive application surfaces | `packages/coding-agent`, `packages/tui` | excluded | `packages/coding-agent/src/main.ts`; `packages/coding-agent/src/modes/`; `packages/tui/src/` |
| Orchestrator | process supervisor and RPC process | `packages/orchestrator` | excluded | `packages/orchestrator/src/supervisor.ts`; `packages/orchestrator/src/rpc-process.ts`; `packages/orchestrator/src/handler.ts` |
| Workspace sandbox | extension sandbox example; not core sandbox capability | `packages/coding-agent` examples | evidence-only | `packages/coding-agent/examples/extensions/sandbox/index.ts` and its example `package.json`; no Coach sandbox was proven in `packages/agent` |
| Container evidence | no core Agent container runtime identified in the assessed core entries | candidate repository | evidence-only | source search of `packages/agent/src`; any container claims outside core require later deployment verification |

## Boundary note

`packages/agent` supplies the actual Agent loop, tool lifecycle, abort path, JSONL storage, and Session APIs used by the Spike. `packages/coding-agent` contains coding-oriented tools, trust, extension loading, system prompt, CLI/TUI and RPC surfaces; it is **not** a Coach sandbox. The sandbox example is an example extension, not evidence that `@earendil-works/pi-agent-core` naturally supplies a safe Coach sandbox. No deployment verification has established a production Desktop or internal-preview sandbox.
