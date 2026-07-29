# KovaaK Connected Account and Coach Lookup Design

> Status: active. This is the product and system contract for the 2026-07-30 implementation task.

## User Outcome

The learner can connect one KovaaK account in local Settings, refresh its training scores manually, and let Coach use the resulting score snapshot in future conversations. In a Coach conversation, the learner can also ask about a profile supplied in that one message. That temporary request produces an answer for the current conversation but does not become the learner's score history or retained identity data.

## Data Ownership

- `kovaak_connections` holds one normalized Steam ID and connection timestamp per local owner. It is a local external-identity setting, not an LLM Provider credential and not a product account.
- Existing `benchmark_records` remains the only persistent score-snapshot store. Only a refresh of the owner's saved connection may write it.
- The turn-scoped Coach bridge owns temporary profile bindings in memory. They disappear on bridge revocation, expiry or process restart.
- Coach context, command traces, confirmations, public score APIs and LLM requests receive no raw external identity. The temporary score projection may be used for that Coach reply, but it never becomes the learner's Benchmark history or command-audit payload and may not be described as the learner's score.

## Commands

- `kovaak_scores.refresh_connected`: no model parameters; reads the owner-scoped saved connection and replaces no data unless both remote stages validate and the existing atomic store write succeeds.
- `kovaak_scores.lookup`: requires a bridge-issued `steam_profile:N` reference. It returns the same bounded score facts to the current turn without persisting a connection or scores.

Neither command treats scores, course labels or ranking as technique, body, tension, grip, hardware or transfer evidence.

## UI Boundary

The existing OpenDesign handoff remains the authority for the future onboarding/Settings module and minimal score view. The module now says that a connected account is saved locally for later manual refresh; it still does not display the raw identifier, add Steam OAuth, create a user account or add a leaderboard.
