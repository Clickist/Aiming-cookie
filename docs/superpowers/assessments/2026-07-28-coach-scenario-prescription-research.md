# Coach Scenario Prescription Research

> Status: implementation input, not a Scenario Registry activation record. Researched on 2026-07-28. External scenario names remain candidates until Aiming Cookie has reviewed their exact local identity, analyzer compatibility and comparison conditions.

## 1. Question and product decision

The immediate question is not whether the internet contains many good aim-training scenarios. It is whether Coach can turn one grounded Analysis issue into a specific, versioned and testable Training Plan item without inventing a diagnosis, scenario, dose or transfer claim.

The resulting decision is deliberately narrow:

- reuse Coach Knowledge Registry v2 for issue, cue, dose, matched retest and near-transfer semantics;
- reuse Scenario Registry plus Launch Manifest for exact local scenario identity and analyzer eligibility;
- reuse the existing Training Plan item and confirmation stores;
- compile an item only when one exact active knowledge entry, one deterministic Analysis metric, one reviewed active scenario profile and one owner-scoped active plan all agree;
- treat current Voltaic S5 and Raw Input recommendations as research candidates, not production scenario identities;
- fail closed when any of those facts are missing or ambiguous.

No second scenario library, prescription store, query engine or family routing table is justified.

## 2. Evidence levels

| Level | Meaning | Allowed product use |
|---|---|---|
| E1 | Peer-reviewed motor-control or motor-learning research | Mechanism boundary, practice difficulty principle, delayed retention/transfer design |
| E2 | Official Voltaic or KovaaK product documentation | Scenario task design, benchmark category, playlist/version behavior |
| E3 | First-party coach article or community practice | A cue or scenario candidate that still requires a user-specific experiment |
| P1 | Aiming Cookie reviewed local registry and analyzer contract | Exact scenario identity and whether the current product may analyze it |
| P2 | Aiming Cookie comparable Analysis evidence | Whether a specific issue is present for this learner under matched conditions |

E2/E3 may propose what to test. They do not create P1/P2 facts.

## 3. Human-coach and Viscose teaching pattern

The sources converge on a workflow rather than a universal playlist:

1. observe a repeated technical pattern;
2. separate the observation from possible causes;
3. choose one technique target and one cue;
4. lower difficulty until the technique is readable;
5. change one task variable per block;
6. use the harder version as a stress test, not as proof of learning;
7. retest the same condition later;
8. test near transfer by changing one relevant dimension;
9. test game transfer independently in comparable game situations.

Viscose describes easier variants as technique-refinement tools and harder variants as stress tests. Movement reading means extracting current position, speed, acceleration, trajectory and animation cues; it is not guessing or simply reacting faster. MattyOW's static model is a fast straight flick, a necessary micro-correction and a brief confirmation, with fluidity between targets as a later bottleneck. Pasu adds target reading, smoothness, click timing and restrained leading.

This supports a Coach instruction such as “先把目标当前的运动读清，再做一次可控的动作” as a testable cue. It does not support “你的 reading 差” as a causal fact.

## 4. Scenario matrix

| Issue family | Current production-capable matched practice | External candidates after future exact review | Training role | Retest boundary |
|---|---|---|---|---|
| Static terminal control | `scenario:static.1wall_6targets_small@1` | VT 1w4ts/1w3ts/1w2ts S5; VT ww5t S5 | Current exact scenario can isolate one cue; S5 variants may later separate precision and speed stress | Same exact profile/settings/metric version; near transfer changes target size or distance only |
| Dynamic acquisition/click condition | `scenario:dynamic.pasu_small_reload@1` | VT Pasu S5 | Readable moving-target acquisition and intentional click timing | Same motion-condition distribution; near transfer changes one size/distance/layout variable |
| Dynamic speed matching/reading | `scenario:dynamic.pasu_small_reload@1` | VT Pasu S5; Popcorn S5 only as a later reaction/arc stress candidate | Match current direction/speed before committing; do not relabel error as reaction time | Same script/change type; near transfer changes one speed/phase/change sequence |
| Predictable tracking speed matching | `scenario:tracking.whj_smooth_strafe_sphere_easy@1` | PGT or Snake Track S5 after exact review | Readable continuous speed/phase matching | Same script/settings/visual and metric version; near transfer changes one speed or phase |
| Reactive tracking change response | none | Ground then Aether S5 after exact review | Confirm change, reacquire, then stabilize | Cannot generate a production item until an exact reviewed profile and validated change points exist |
| Control tracking smoothness | none | Raw Control then Controlsphere S5 after exact review | Reduce ineffective reversals while retaining error/coverage guardrails | Cannot generate a production item until an exact reviewed profile exists |

The three local profiles above are production-capable identities, not claims that they are universally optimal scenarios. Coach may select them only when the current Analysis already occurred on that exact profile and the matching knowledge entry carries a prescription binding.

## 5. Dose and difficulty

No reviewed source supplies a universal number of minutes, runs or accuracy percentage suitable for every learner and every scenario. Voltaic rank thresholds are benchmark thresholds, not diagnostic or dosage thresholds.

MattyOW gives a community rule of thumb of roughly 75-80% accuracy for particular Pasu practice, while also varying the target according to experience. That is useful coaching context for Pasu, but it is not a universal dose, a calibrated Aiming Cookie threshold or permission for Provider text to invent an accuracy target.

The supported guardrails are:

- start with a version where the target behavior can be read and the chosen cue can be attempted;
- change one of target size, speed, change density, layout or distance at a time;
- keep outcome/error guardrails visible when optimizing smoothness or correction burden;
- reduce difficulty when performance collapse is unrelated to the cue;
- stop and record the result when the user reports pain, numbness, weakness or persistent discomfort;
- never infer tension, grip or a hardware limitation from kinematics alone.

The product should therefore preserve the Registry dose guardrail verbatim. The Provider must not add a generic `10 minutes`, `90% accuracy` or `5 runs` prescription.

## 6. Matched, near-transfer and game transfer

Immediate improvement only answers whether the cue changed this attempt. A later matched retest is needed before treating the change as retained. Near transfer should modify one relevant dimension while preserving the remaining measurement contract.

Game transfer remains separate evidence. A trainer improvement may motivate a game drill, but it does not prove better match performance. A credible game check needs the same game, weapon/character role, sensitivity and a repeatable situation or outcome definition. General kills, rank or one highlight are insufficient by themselves.

## 7. Stable identity and version behavior

Voltaic currently distinguishes Novice, Intermediate and Advanced S5 scenarios and only accepts the latest benchmark version for official ranking. KovaaK documents playlist share-code lookup and warns that stale local scenario versions can affect score submission. KovaaK does not publish a stable public API contract.

Consequences for Aiming Cookie:

- display name and share code are not permanent scenario identity;
- an external candidate needs exact local content/hash, reviewed analyzer compatibility, retrieval date and replacement status before activation;
- season/difficulty metadata may help research and UI, but cannot be hidden inside free-text limitations;
- a renamed, replaced or unknown-version scenario suspends matched comparison until re-reviewed.

## 8. Executable binding requirements

A prepared Training Plan item is valid only when all of these hold:

- exact active Analysis issue and deterministic metric;
- one exact active Knowledge Registry signal match;
- one `scenario_prescription` in that knowledge entry;
- the prescribed profile equals the Analysis profile;
- the profile is active in both Scenario Registry and Launch Manifest;
- cue, dose, expected direction and retest sections come from that same knowledge entry;
- an owner-scoped active plan exists at turn creation;
- the complete item is frozen into TeachingTurn before Provider execution;
- Node permits only byte-for-byte equivalent `plan_ref + item` parameters.

Missing active plan means Coach can still explain the exercise, but it must not claim that an item was saved. Missing Analysis issue, deterministic metric, profile binding or exact registry activation means no formal item is produced.

## 9. Direct-source audit: Viscose and Voltaic

The Viscose Benchmarks spreadsheet, Voltaic Aim Journey and Weakness-Specific Routines were exported and reviewed in full on 2026-07-29. They strengthen the teaching vocabulary above, but they do not justify a second knowledge registry or a new deterministic diagnosis.

### 9.1 What the direct sources add

- Viscose treats the benchmark as a practice tool: easier variants refine technique, harder variants stress-test it, and overlapping difficulty levels are intentional. `Easier / Medium / Hard` describe scenario difficulty, not learner rank.
- The Viscose taxonomy is more detailed than the product's family labels. It separates control tracking by arm/wrist/fingertip/blending emphasis, reactive tracking by control/speed/reading, flick technique by speed/stability/micro/post-flick, and click timing by reading/precision/stability/linear.
- Voltaic and MattyOW repeatedly decompose performance into observable task phases: initial acquisition, transport, arrival, necessary correction, confirmation, sustained tracking, reacquisition and target selection.
- The sources support cue experiments such as reading current direction and acceleration, matching target speed, completing one continuous response, releasing unnecessary tension, and using a readable variant before a stress variant.
- Weakness-Specific Routines adds a useful progression candidate: large-target technique work followed by a smaller-target stress condition. It still lacks the exact local scenario identity, version/hash and analyzer comparison contract required for a production Training Plan item.

### 9.2 What is already covered

The current ten Knowledge Registry v3 entries already cover the production-safe part of the sources:

| Direct-source concept | Existing product entry or boundary |
|---|---|
| static flick, terminal correction and confirmation | `static.flicking-terminal-control` |
| dynamic acquisition, click condition and reading | `dynamic.click-error-and-acquisition`, `dynamic.speed-matching-and-reading` |
| predictable speed matching and reactive change response | `tracking.predictable-speed-matching`, `tracking.reactive-change-response` |
| continuity with error/coverage guardrails | `tracking.control-smoothness` |
| switching transport, arrival and settle | `switching.transition-and-arrival` |
| selection only when observable | `switching.selection-observable-only` |
| tension only after user report and as a single-variable experiment | `hypothesis.tension-management` |
| no body, grip, posture or muscle inference from movement data | `movement.outcome-only-boundary` |

No new entry is justified merely to repeat `reading`, `speed matching`, `fluidity`, grip labels or a community scenario name.

### 9.3 Claims that must stay downgraded

- Arm/wrist/fingertip labels are the author's scenario emphasis and vary with sensitivity; they are not measured anatomy or fixed player traits.
- Visible jitter, lag, overshoot or segmented motion does not prove too much or too little tension. Tension remains a user-reported hypothesis tested with one reversible cue.
- Community sensitivity ranges, FOV changes, accuracy targets, playlist durations and score-threshold methods are author-specific practice strategies, not universal defaults.
- Benchmark rank or score improvement does not prove main-game transfer. Matched retention, near transfer and a separate game check remain different evidence.
- Target-priority advice such as nearest target, cluster farming or spawn-sound timing is scenario strategy. It cannot become a selection error unless the current scenario rules and target state make the choice observable.

### 9.4 Remaining product gaps

The direct sources expose three workflow gaps rather than missing aim definitions:

1. **Difficulty progression:** the product can prescribe a reviewed matched scenario and one-variable near transfer, but it does not yet have reviewed large-target/small-target or easier/medium/hard profile ladders across all families.
2. **Plateau handling:** the product can retest and revise one item, but it has no reliable plateau detector based on repeated comparable sessions, adherence and delayed retention.
3. **Execution and game-transfer evidence:** the plan can record execution and retest outcomes, but the current data contract does not observe animation reading, eye focus, hand/grip behavior, game decisions or repeatable main-game situations. Coach may ask for user reports; it must not present them as measured signals.

These gaps should be closed by reviewing exact scenario identities and by reusing the existing Training Plan execution/retest records. Adding prose to the Registry cannot close them.

## 10. Sources

- Voltaic, [Season 5 KovaaK benchmarks announcement](https://blog.voltaic.gg/announcing-the-voltaic-season-5-aiming-benchmarks-beta-for-kovaaks/), 2024-12-25.
- Voltaic, [current Benchmarks hub](https://app.voltaic.gg/benchmarks), checked 2026-07-28.
- Voltaic, [Getting started with Voltaic](https://blog.voltaic.gg/getting-started-with-voltaic/), checked 2026-07-28.
- KovaaK, [FAQ](https://kovaaks.com/kovaaks/faq), checked 2026-07-28.
- Viscose, [The new best way to train your aim](https://rawinput.net/resources/viscbenches), 2025-10-29.
- Viscose, [How to react faster in any game](https://rawinput.net/resources/reaction-time), 2025-02-08.
- Viscose, [Tension Management in Aiming](https://rawinput.net/resources/tension), 2025-06-16.
- MattyOW, [Fluidity in Static Clicking](https://rawinput.net/resources/staticfluidity), 2024-07-21.
- MattyOW, [How to Pasu](https://rawinput.net/resources/pasu), 2022-08-04.
- Viscose, [Benchmarks spreadsheet](https://docs.google.com/spreadsheets/d/1bFAlt6g_Gm8P9RBkcAoObpbIGFwVS5gXIdIK9B_YyZE/edit), checked 2026-07-29.
- Voltaic, [Aim Journey](https://docs.google.com/document/d/1JoNtoHK9GgJCjE-7yQxKXkpAkGJyOBBipiZqPNYwECs), checked 2026-07-29.
- Voltaic, [Weakness-Specific Routines](https://docs.google.com/document/d/1oNUBAaLovS0oMLn0_z3BEPT0_txlRtugr0Qzxqp0SvI), checked 2026-07-29.
- MattyOW, [How to Speed Match](https://rawinput.net/resources/speedmatching), 2025-05-23.
- pinguefy and Viscose, [Smoothness: the most important aiming skill](https://rawinput.net/resources/smoothness), 2024-06-24.
- Viscose, [Does mouse grip actually matter?](https://rawinput.net/resources/mousegrips), 2024-05-13.
- Guadagnoli and Lee, [Challenge point framework](https://pubmed.ncbi.nlm.nih.gov/15130871/), 2004.
- Czyz, Wojcik and Solarska, [Contextual interference and transfer meta-analysis](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2024.1377122/full), 2024.
- Kreyenmeier et al., [Humans Can Track But Fail to Predict Accelerating Objects](https://pmc.ncbi.nlm.nih.gov/articles/PMC9469915/), 2022.
- Balasubramanian et al., [On the analysis of movement smoothness](https://pmc.ncbi.nlm.nih.gov/articles/PMC4674971/), 2015.
