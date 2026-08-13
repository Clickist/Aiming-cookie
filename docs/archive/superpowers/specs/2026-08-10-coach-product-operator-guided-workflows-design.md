# Coach Product Operator and Guided Workflows - Design Contract

> **Status:** active
> **Purpose:** Freeze how the existing Pi Coach executes user-authorized product operations and guides the remaining user-only steps without adding a second Agent runtime, generic UI automation, or duplicate product state.
> **Upstream:** [`../../PRD.md`](../../PRD.md), [`../../ARCHITECTURE.md`](../../ARCHITECTURE.md), [`../../frontend-uiux-design.md`](../../frontend-uiux-design.md)
> **Related contracts:** [`2026-07-13-coach-product-commands-explanations-provider-design.md`](2026-07-13-coach-product-commands-explanations-provider-design.md), [`2026-07-27-coach-guided-teaching-loop-design.md`](2026-07-27-coach-guided-teaching-loop-design.md)

This later, more specific contract supersedes the confirmation rules in section 2.3 of the 2026-07-13 product-command contract and section 4.5 of the frontend UI/UX contract where they conflict. It does not change evidence, diagnosis, Provider-data, capture, or Training Plan truth boundaries.

The active 2026-08-09 single-pipeline plan keeps its existing “no Provider profile mutation command” freeze while that plan is being executed. Provider metadata operations in this contract are a later expansion and must not be implemented concurrently with, or used to rewrite, that plan's Task 3.

## 1. Product outcome

Users should be able to state a goal in ordinary language without learning the page hierarchy, internal object names, or the order of product steps. Coach should do every product operation that can be performed safely through a registered product command, then guide the user through the small set of steps that only the user can complete.

The product model is:

```text
user message
  -> existing Pi Agent interprets the goal
  -> registered product command or bounded guidance intent
  -> existing product handlers / stores / routes / frontend controls
  -> state is read again and the next step is selected
```

Pi remains the only Agent runtime. The guidance layer is deterministic product orchestration around Pi, not another Agent, planner model, browser operator, or product store.

## 2. Natural-language authority

### 2.1 Direct authorization

An unambiguous instruction in the current user message directly authorizes the corresponding registered product operation, including deletion or another consequential operation. The product must not ask for a second confirmation merely because the operation is destructive.

Direct authorization is bounded to one interpreted command intent:

- Pi proposes a registered command, bounded parameters, and an exact instruction quote from the current user message;
- the trusted backend verifies that the quote belongs to the persisted current message, validates the command and parameters against the product-command registry, and deterministically resolves the command's target from the current selected/context ref or exactly one compatible reachable stable ref;
- if the command needs a target and zero or multiple compatible refs remain, no grant is issued even when the quote clearly expresses the action. Scalar fact parameters must be explicitly present in the message and parsed by the command's typed validator;
- only after target/parameter resolution does the backend issue an in-memory `instruction_grant.v1`;
- the grant is bound to `owner_id`, `thread_id`, `user_message_ref`, command name, normalized parameter digest, expiry, and one tool bridge;
- the backend, never Pi or the Provider, assigns `authorization_source=explicit_user_request`;
- execution still applies ownership, stable-ref reachability, capability, state-transition, idempotency, audit, privacy, and business validation.

The quote is grounding evidence, not proof that an arbitrary model-selected ref is the user's target and not a general permission token. It is not written to normal audit output; audit stores the message ref and safe digests. A missing, non-matching, ambiguous, underspecified, or non-unique instruction does not receive a direct grant.

### 2.2 Coach-proposed operations

If Coach proposes an operation that the user did not explicitly request in the current message, consequential writes continue as `coach_inferred` and return the existing `needs_confirmation` result. The confirmation must name the operation, target, meaningful effect, and cancel path. Read-only inspection needed to answer the user may execute directly.

If the user confirms the proposed operation in a later natural-language message, that new message can create an `explicit_user_request` grant; the existing confirmation endpoint remains supported for confirmation cards and recovery.

### 2.3 Ambiguity

Coach asks one minimum disambiguating question when the target or required parameter cannot be resolved from owner-scoped stable refs and current product state. It must not guess between multiple Runs, Analyses, plans, Provider profiles, or storage objects.

## 3. User-only actions

The following are not registered as Agent-executable product operations:

- entering, replacing, revealing, or transmitting an API key, OAuth/device-code response, password, token, or other secret;
- granting operating-system, Raw Input, recording, privacy, or external Provider consent;
- selecting a local file or directory, or supplying an absolute path;
- completing external OAuth/device-code/browser interaction;
- performing real KovaaK training or another physical action;
- asserting pain, fatigue, control feel, completion, preference, or another subjective/reality-dependent fact unless the user explicitly states it;
- resolving a choice whose intent cannot be established from product evidence.

Coach may navigate to the trusted control, reveal it, prefill non-secret values, explain the single required action, wait, and verify the resulting product state. Secret values, paths, system handles, raw traces, MP4 data, and Steam identity never enter Pi tool parameters, Provider context, guidance events, or audit.

An explicit user statement can directly authorize a registered write of that stated fact, such as a calibration value or completed-training record, but Coach cannot invent the fact.

## 4. Product operation coverage

Every user-visible operation must be classified as one of the following; an eligible operation is not complete until it uses the same domain handler as the UI and is registered in the existing Coach command boundary.

| Domain | Agent-executable through registered commands | User-only boundary |
|---|---|---|
| Run, History, Analysis, Evidence | list, inspect, compare, create/retry eligible Analysis, navigate, remove eligible Analysis or Run-owned evidence | choose among genuinely ambiguous Runs; select local fallback files |
| Training Plan and TeachingSession | generate, save, activate, pause, adjust, review, record an explicitly stated execution/retest fact | perform training; provide subjective or reality-dependent facts |
| Coach relationship | create, rename, archive/delete session, attach/detach safe context, stop/retry run | none beyond ambiguity resolution |
| Storage | inspect categories, list incomplete captures, remove a specifically resolved eligible item | no bulk silent cleanup; resolve an ambiguous target |
| Calibration | inspect, save, or delete values explicitly supplied by the user | supply the real `cm/360` and FOV values |
| KovaaK connection and scores | inspect status, refresh, disconnect, and operate an existing opaque connection ref | enter Steam Profile identity and consent to local persistence |
| Provider | inspect catalog/readiness, create/update non-secret profile metadata, select/test/delete a resolved profile | enter credentials; complete OAuth/device-code; approve external auth |
| Capture and desktop permissions | inspect readiness and guide recovery | grant privacy/system permission and enable first capture through the trusted Desktop control |
| Import/export/external destinations | prepare and navigate to the bounded product flow | choose files/directories/destination and approve external transfer |

There is no shell, filesystem, arbitrary HTTP, arbitrary Tauri invoke, URL, DOM selector, or simulated mouse/keyboard command. New product operations are added by extending the existing typed command registry and tests, not by exposing a generic action primitive.

## 5. ProductReadiness projection

`product_readiness.v1` is a path-free, secret-free, owner-scoped read model derived from existing stores and adapters. It is not persisted as a second source of truth.

It contains only bounded state needed to select a next step:

```text
product_readiness.v1
  domains
    onboarding: { state, availability, reason_code? }
    provider: { state, availability, reason_code?, refs[], count }
    capture: { state, availability, reason_code? }
    kovaak: { state, availability, reason_code?, refs[], count }
    pending_runs: { state, availability, reason_code?, refs[], count, truncated }
    analysis: { state, availability, reason_code?, refs[], count, truncated }
    training_plan: { state, availability, reason_code?, refs[], count }
    storage: { state, availability, reason_code?, refs[], count, truncated }
  capabilities[]
  blocking_reasons[]
```

`availability` is `known | unavailable`; an unavailable read is distinct from a known empty list. `refs[]` contains bounded opaque stable refs only, is capped per domain, and reports `count` plus `truncated` when applicable. Each domain state remains a bounded enum appropriate to that domain, such as Provider `missing | needs_auth | testing | ready`, pending Runs `none | one | many | incomplete`, or Analysis `none | queued | running | ready | failed`.

Each field is projected from the existing canonical Provider, product-state, capture, Run, Analysis/task, Training Plan, Storage, and TeachingSession sources. Unknown or failed reads remain explicit and never become `ready`; the projection never invents a ref from display text.

Before Provider readiness exists, the frontend may use this deterministic projection to drive onboarding and recovery. That bootstrap guidance is not a Provider-less Coach reply and does not create a second conversational Agent.

## 6. GuidanceIntent contract

`guidance_intent.v1` is the only cross-layer guidance envelope:

```text
guidance_intent.v1
  intent_id
  kind = execute_command | request_confirmation | ui_navigation |
         user_action_required | wait_for_state | completed | blocked
  goal
  target?
  command_result_ref?
  precondition
  completion_condition
  recovery?
```

Rules by kind:

- `execute_command` references an existing product-command result; it does not carry a parallel mutation payload.
- `request_confirmation` references the existing confirmation contract.
- `ui_navigation` uses allow-listed semantic route, section, reveal, focus, and safe-prefill targets from one versioned `guidance_target_registry.v1` asset consumed by backend validation and frontend mapping. It cannot contain a URL, selector, script, secret, path, arbitrary component name, Provider credential slot, file input, or permission control.
- `user_action_required` names one user-only action, the trusted control target, completion condition, cancel path, and recovery state.
- `wait_for_state` names an allow-listed readiness field and terminal states; the frontend does not trust chat text as completion.
- `completed` and `blocked` contain a safe result/ref or bounded reason and recovery action.

## 7. Frontend GuidanceHost

One `GuidanceHost` in the Coach-first application shell consumes GuidanceIntent events and maps semantic targets to existing routes and controls. It owns:

- navigation without losing the current Coach session or draft;
- reveal, accessible focus, and non-secret safe prefill;
- one visible next step for `user_action_required`;
- completion, cancellation, failure, and timeout acknowledgement;
- acknowledging the UI outcome without claiming product completion; the backend re-reads ProductReadiness before returning the next step.

The host does not duplicate Settings forms, onboarding, stores, API clients, or command handlers. Cross-route recovery uses existing Agent run events and canonical product state; v1 adds no workflow database or generic persisted automation engine. The Pi turn keeps its existing terminal Agent run status. Guidance continuation is represented by bounded `guidance` events appended to that run after model/tool completion; acknowledgement never reopens the run or introduces an `awaiting_input` Agent-run state. The latest unacknowledged guidance event is the current intent.

The continuation endpoint is `POST /api/coach/guidance/ack`. Its request contains only `schema_version = guidance_ack_request.v1`, `run_ref`, `intent_id`, and `outcome = completed | cancelled | failed | timed_out`. `completed` means only that the semantic UI action finished; it is not evidence that the product goal succeeded. The backend verifies owner/run/current-intent binding, re-reads canonical readiness, appends a safe event to the existing Coach Agent run event stream, and returns `guidance_ack_response.v1` with the same `run_ref`, the accepted `intent_id` and `outcome`, plus exactly one of `next_intent` or `terminal_state`. The frontend cannot submit readiness values, product refs, command parameters, or a self-declared product success state through this endpoint.

## 8. Deterministic workflow compiler

Pi selects a user goal and may call product commands, but the allowed next-step sequence is compiled from ProductReadiness and registered operations. Initial goal families are:

- start first usable training;
- analyze the most recent eligible Run;
- decide what to practice today;
- record an explicitly stated execution and schedule/inspect retest;
- inspect progress or evidence;
- recover Provider, capture, KovaaK, Analysis, or storage readiness.

Every step follows the same loop:

```text
read readiness -> execute command or guide one user action -> verify state
               -> continue | ask one disambiguating question | block with recovery
```

The compiler reuses Training Plan, TeachingSession, Coach Agent runs/events, product commands, routes, and stores. It must not create a parallel learning state machine or infer completion from the assistant's own prose.

When an explicit user statement directly succeeds for a Training Plan execution/retest fact, TeachingSession reconciliation must accept the audited `explicit_user_request` result as the same trusted fact transition that confirmation previously supplied. A Coach-inferred fact continues to require confirmation; no direct command may skip the statement-to-fact binding.

## 9. Failure and recovery

- Tool success is not workflow success until the relevant canonical state satisfies the completion condition.
- If a side effect may have completed but the result was lost, return the existing `idempotency_outcome_unknown` behavior and inspect state before retrying.
- Expired/replaced grants, owner/thread/message mismatch, unreachable refs, altered parameters, or unregistered commands fail closed.
- Navigation/focus failure reports `blocked` or a bounded recovery target; it never falls back to DOM search or simulated input.
- User cancellation leaves the underlying product state unchanged unless an already audited command completed.
- Provider failure preserves deterministic bootstrap/recovery UI and existing local records; it does not fabricate a Coach response.

## 10. Acceptance journeys

The contract is complete only when these journeys work through real registered operations and semantic UI targets:

1. “分析刚才那局” resolves one eligible Run, creates Analysis without a redundant confirmation, waits for terminal state, and attaches/opens the result.
2. Multiple candidate Runs produce one disambiguating question and no guessed mutation.
3. “删除这次分析” directly deletes the resolved terminal Analysis, audits the message-bound grant, and verifies the reference is unavailable while Coach history remains.
4. “今天练什么” reads the active plan, opens the scenario when possible, waits for real training, and records completion only after the user states it.
5. Provider recovery opens and focuses the trusted auth control; API key/OAuth data never enters Coach, and successful readiness automatically resumes the pending goal.
6. Storage cleanup shows bounded targets; an explicit deletion instruction executes the registered removal and verifies new storage state.
7. Capture onboarding explains the privacy scope, waits for user consent/system permission in the trusted Desktop control, and verifies readiness without Pi receiving permission data.
8. Stop, retry, cancellation, process restart, and lost-result paths preserve owner isolation, idempotency, audit, and a recoverable next step.
